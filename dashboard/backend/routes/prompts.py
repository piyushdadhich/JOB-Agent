"""GET resume / cover-letter prompts + POST pasted text.

Generation routes assemble the prompt via PromptService and stamp the
parent application's `prompt_generated_at`. Save routes accept
Claude's pasted markdown response and write it to the application's
`resume_text` / `cover_letter_text` columns; once both are present
the application's `docs_ready_at` is set, signaling the Apply tab.

If the user has not yet clicked "Apply" on the shortlist (i.e. there
is no application row), saving lazy-creates one at status='drafted'.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dashboard.backend.deps import get_profile_id, get_tracker
from dashboard.backend.services.prompt_service import PromptService
from engine.persistence.tracker import Tracker

router = APIRouter(prefix="/api/prompts", tags=["prompts"])


class PromptResponse(BaseModel):
    posting_id: int
    prompt: str


class SaveTextRequest(BaseModel):
    content: str


class SaveTextResponse(BaseModel):
    application_id: int
    field: str
    chars: int
    docs_ready_at: Optional[str] = None


def _require_posting(tracker: Tracker, posting_id: int) -> dict:
    posting = tracker.get_opportunity_by_id(posting_id)
    if posting is None:
        raise HTTPException(
            status_code=404,
            detail=f"opportunity {posting_id} not found",
        )
    return posting


def _latest_application_id(
    tracker: Tracker, posting_id: int,
) -> Optional[int]:
    row = tracker._query_one(
        "SELECT id FROM applications WHERE opportunity_id = ? "
        "ORDER BY status_updated_at DESC LIMIT 1",
        (posting_id,),
    )
    return row["id"] if row else None


def _stamp_prompt_generated(
    tracker: Tracker, posting_id: int,
) -> None:
    """Best-effort lifecycle stamp; no-op if the user has not yet
    selected this posting (no application row exists)."""
    app_id = _latest_application_id(tracker, posting_id)
    if app_id is None:
        return
    tracker.update_application_fields(
        app_id,
        prompt_generated_at=datetime.now(timezone.utc).isoformat(),
    )


def _save_text_field(
    tracker: Tracker,
    posting_id: int,
    field: str,
    content: str,
    profile_id: Optional[str] = None,  # FIX-3 hook for auto-render
) -> SaveTextResponse:
    # profile_id is accepted (but currently unused here) so the
    # FIX-3 cloud-generation endpoint in applications_board.py can
    # call this helper with the active profile and we can wire an
    # auto-render path later without changing every call site.
    _ = profile_id  # explicit acknowledgement; silences linters.
    app_id = _latest_application_id(tracker, posting_id)
    if app_id is None:
        app_id = tracker.create_application(
            opportunity_id=posting_id,
            resume_variant="dashboard_pending",
        )
    tracker.update_application_fields(app_id, **{field: content})

    docs_ready_at: Optional[str] = None
    row = tracker.get_application_by_id(app_id)
    if (
        row
        and row.get("resume_text")
        and row.get("cover_letter_text")
        and not row.get("docs_ready_at")
    ):
        docs_ready_at = datetime.now(timezone.utc).isoformat()
        tracker.update_application_fields(
            app_id, docs_ready_at=docs_ready_at,
        )
    elif row:
        docs_ready_at = row.get("docs_ready_at")

    return SaveTextResponse(
        application_id=app_id,
        field=field,
        chars=len(content),
        docs_ready_at=docs_ready_at,
    )


# --- Batch copy-paste workflow (Spec 4) ---------------------------

_QUEUE_SQL = """\
SELECT a.id, a.opportunity_id, a.resume_prompt, a.cover_letter_prompt,
       c.name AS employer, o.title AS opportunity_title
FROM applications a
JOIN opportunities o ON o.id = a.opportunity_id
JOIN companies c ON c.id = o.company_id
WHERE a.selected_at IS NOT NULL
  AND a.docs_ready_at IS NULL
  AND a.status = 'drafted'
ORDER BY a.status_updated_at DESC
"""


_JOB_SEPARATOR_RE = re.compile(
    r"═{3,}\s*JOB\s+(\d+)\s+OF\s+\d+[^═]*═{3,}", re.IGNORECASE,
)
_RESUME_SECTION_RE = re.compile(
    r"---\s*RESUME\s*---\s*\n(.*?)(?=---\s*COVER\s*LETTER|═{3,}|$)",
    re.IGNORECASE | re.DOTALL,
)
_COVER_LETTER_SECTION_RE = re.compile(
    r"---\s*COVER\s*LETTER\s*---\s*\n(.*?)(?=═{3,}|$)",
    re.IGNORECASE | re.DOTALL,
)


class BatchPromptsResponse(BaseModel):
    batch_prompt: str
    count: int
    ids: list[int]


@router.get("/batch", response_model=BatchPromptsResponse)
def get_batch_prompts(
    profile_id: str = Depends(get_profile_id),
    tracker: Tracker = Depends(get_tracker),
) -> BatchPromptsResponse:
    """Combined prompt for every posting in the needs_prompts queue.

    Rows that already have stored prompts (post-Spec-3) use them
    directly; older rows fall back to PromptService on the fly so the
    batch always contains full content."""
    rows = tracker._query_all(_QUEUE_SQL)
    if not rows:
        return BatchPromptsResponse(batch_prompt="", count=0, ids=[])

    service = PromptService()
    ids: list[int] = []
    sections: list[str] = []
    n = len(rows)

    for i, r in enumerate(rows, 1):
        opp_id = r["opportunity_id"]
        ids.append(opp_id)

        resume_prompt = r["resume_prompt"]
        cl_prompt = r["cover_letter_prompt"]
        if not (resume_prompt and cl_prompt):
            posting = tracker.get_opportunity_by_id(opp_id)
            eval_d = tracker.get_latest_evaluation(opp_id)
            if not resume_prompt:
                resume_prompt = service.build_resume_prompt(
                    profile_id=profile_id,
                    posting=posting, eval_decision=eval_d,
                )
            if not cl_prompt:
                cl_prompt = service.build_cover_letter_prompt(
                    profile_id=profile_id,
                    posting=posting, eval_decision=eval_d,
                )

        header = (
            f"═══ JOB {i} OF {n}: {r['employer']} — "
            f"{r['opportunity_title']} ═══"
        )
        sections.append(
            f"{header}\n"
            f"--- RESUME ---\n{resume_prompt}\n\n"
            f"--- COVER LETTER ---\n{cl_prompt}\n"
        )

    sections.append("═══ END ═══")
    batch = "\n\n".join(sections)
    return BatchPromptsResponse(batch_prompt=batch, count=n, ids=ids)


class BatchSaveRequest(BaseModel):
    response: str
    ids: list[int]


class BatchSaveResponse(BaseModel):
    saved: int
    total: int


@router.post("/batch", response_model=BatchSaveResponse)
def save_batch_responses(
    body: BatchSaveRequest,
    tracker: Tracker = Depends(get_tracker),
) -> BatchSaveResponse:
    """Parse Claude's combined response and write each posting's
    resume + cover letter via the same helper as single-posting saves.

    `saved` counts postings whose resume was written (one per posting);
    cover-letter saves do not double-count."""
    raw = body.response or ""
    ids = body.ids or []
    parts = _JOB_SEPARATOR_RE.split(raw)

    saved = 0
    for job_num_str, content in zip(parts[1::2], parts[2::2]):
        try:
            job_idx = int(job_num_str) - 1
        except ValueError:
            continue
        if job_idx < 0 or job_idx >= len(ids):
            continue
        opp_id = ids[job_idx]
        if tracker.get_opportunity_by_id(opp_id) is None:
            continue

        resume_match = _RESUME_SECTION_RE.search(content)
        cl_match = _COVER_LETTER_SECTION_RE.search(content)

        if resume_match:
            resume_text = resume_match.group(1).strip()
            if resume_text:
                _save_text_field(
                    tracker, opp_id, "resume_text", resume_text,
                )
                saved += 1
        if cl_match:
            cl_text = cl_match.group(1).strip()
            if cl_text:
                _save_text_field(
                    tracker, opp_id, "cover_letter_text", cl_text,
                )

    return BatchSaveResponse(saved=saved, total=len(ids))


# --- Spec 2: explicit-id batch prompt preview ---------------------

class BatchPreviewRequest(BaseModel):
    opportunity_ids: list[int]


@router.post("/batch/preview", response_model=BatchPromptsResponse)
def get_batch_preview(
    body: BatchPreviewRequest,
    profile_id: str = Depends(get_profile_id),
    tracker: Tracker = Depends(get_tracker),
) -> BatchPromptsResponse:
    """Assemble batch prompt for an explicit list of opportunity_ids.

    Unlike GET /api/prompts/batch (which assembles from the
    needs_prompts queue), this endpoint operates on a user-selected
    list from the Shortlist multi-select UI. Builds resume + cover
    letter prompts on the fly for each posting.
    """
    if not body.opportunity_ids:
        raise HTTPException(
            status_code=400,
            detail="opportunity_ids must be non-empty",
        )

    service = PromptService()
    ids: list[int] = []
    sections: list[str] = []
    n = len(body.opportunity_ids)

    for i, opp_id in enumerate(body.opportunity_ids, 1):
        posting = tracker.get_opportunity_by_id(opp_id)
        if posting is None:
            raise HTTPException(
                status_code=404,
                detail=f"opportunity {opp_id} not found",
            )
        ids.append(opp_id)
        eval_d = tracker.get_latest_evaluation(opp_id)
        resume_prompt = service.build_resume_prompt(
            profile_id=profile_id, posting=posting, eval_decision=eval_d,
        )
        cl_prompt = service.build_cover_letter_prompt(
            profile_id=profile_id, posting=posting, eval_decision=eval_d,
        )
        header = (
            f"═══ JOB {i} OF {n}: {posting.get('employer')} — "
            f"{posting.get('title')} ═══"
        )
        sections.append(
            f"{header}\n"
            f"--- RESUME ---\n{resume_prompt}\n\n"
            f"--- COVER LETTER ---\n{cl_prompt}\n"
        )

    sections.append("═══ END ═══")
    return BatchPromptsResponse(
        batch_prompt="\n\n".join(sections),
        count=n,
        ids=ids,
    )


@router.get(
    "/{posting_id}/resume", response_model=PromptResponse,
)
def get_resume_prompt(
    posting_id: int,
    profile_id: str = Depends(get_profile_id),
    tracker: Tracker = Depends(get_tracker),
) -> PromptResponse:
    posting = _require_posting(tracker, posting_id)
    eval_d = tracker.get_latest_evaluation(posting_id)
    text = PromptService().build_resume_prompt(
        profile_id=profile_id, posting=posting, eval_decision=eval_d,
    )
    _stamp_prompt_generated(tracker, posting_id)
    return PromptResponse(posting_id=posting_id, prompt=text)


@router.get(
    "/{posting_id}/cover-letter", response_model=PromptResponse,
)
def get_cover_letter_prompt(
    posting_id: int,
    profile_id: str = Depends(get_profile_id),
    tracker: Tracker = Depends(get_tracker),
) -> PromptResponse:
    posting = _require_posting(tracker, posting_id)
    eval_d = tracker.get_latest_evaluation(posting_id)
    text = PromptService().build_cover_letter_prompt(
        profile_id=profile_id, posting=posting, eval_decision=eval_d,
    )
    _stamp_prompt_generated(tracker, posting_id)
    return PromptResponse(posting_id=posting_id, prompt=text)


@router.post(
    "/{posting_id}/resume", response_model=SaveTextResponse,
)
def save_resume_text(
    posting_id: int,
    body: SaveTextRequest,
    tracker: Tracker = Depends(get_tracker),
) -> SaveTextResponse:
    _require_posting(tracker, posting_id)
    return _save_text_field(
        tracker, posting_id, "resume_text", body.content,
    )


@router.post(
    "/{posting_id}/cover-letter", response_model=SaveTextResponse,
)
def save_cover_letter_text(
    posting_id: int,
    body: SaveTextRequest,
    tracker: Tracker = Depends(get_tracker),
) -> SaveTextResponse:
    _require_posting(tracker, posting_id)
    return _save_text_field(
        tracker, posting_id, "cover_letter_text", body.content,
    )
