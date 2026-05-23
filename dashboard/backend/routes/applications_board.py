"""Spec FIX-2 TASK 2 — Unified Applications board endpoints.

The Applications tab consumes one endpoint to render the full Kanban
plus expanded-card state in a single round-trip. Existing endpoints
that work by application_id (pipeline_tracker.update_status,
applications.update_status) stay intact; this module adds opportunity-
keyed companions so the Kanban card — which is opportunity-centric in
the UI — can update notes / status without first mapping to an
application_id.

Routes:
  GET  /api/applications/board                     full kanban payload
  POST /api/applications/by-opp/{opp_id}/notes     upsert notes
  POST /api/applications/by-opp/{opp_id}/status    move bucket
  POST /api/applications/by-opp/{opp_id}/generate  cloud-generate a doc
  POST /api/applications/generate-all              batch generate (async)
  GET  /api/applications/generate-all/{task_id}    batch progress poll
  POST /api/applications/add                       manual job entry
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from dashboard.backend.deps import get_profile_id, get_tracker
from engine.persistence.tracker import (
    InvalidStatusError, Tracker, TrackerError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/applications", tags=["applications-board"])


# Five-bucket vocabulary used by the Kanban. `selected` is the
# user-curated pool (drafted + selected_at set) without saved docs;
# `docs_ready` flips when both resume + cover letter are persisted.
# The remaining four buckets mirror the existing PipelineTracker
# vocabulary so status moves continue to round-trip through
# tracker.update_application_status.
Bucket = Literal[
    "selected", "docs_ready",
    "applied", "interview", "offer", "rejected",
]

_BUCKET_TO_WRITE_STATUS: dict[str, str] = {
    "selected":   "drafted",
    "docs_ready": "drafted",  # same DB status; docs_ready_at differs
    "applied":    "submitted",
    "interview":  "interviewing",
    "offer":      "offered",
    "rejected":   "rejected",
}


class BoardCard(BaseModel):
    model_config = ConfigDict(extra="ignore")
    application_id: int
    opportunity_id: int
    title: str
    employer: str
    location: Optional[str] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    posted_at: Optional[str] = None
    fit_score: Optional[float] = None
    tier: Optional[str] = None
    status: Bucket
    db_status: str
    resume_saved: bool = False
    resume_saved_at: Optional[str] = None
    resume_markdown: Optional[str] = None
    cover_letter_saved: bool = False
    cover_letter_saved_at: Optional[str] = None
    cover_letter_markdown: Optional[str] = None
    docs_ready_at: Optional[str] = None
    notes: str = ""
    applied_at: Optional[str] = None
    updated_at: str


class BoardStats(BaseModel):
    total: int
    selected: int
    docs_ready: int
    applied: int
    interview: int
    offer: int
    rejected: int


class BoardResponse(BaseModel):
    columns: dict[str, list[BoardCard]]
    stats: BoardStats


_BOARD_SQL = """\
SELECT a.id AS application_id,
       a.opportunity_id,
       a.status,
       a.status_updated_at,
       a.selected_at,
       a.docs_ready_at,
       a.submitted_date,
       a.resume_text,
       a.cover_letter_text,
       a.notes,
       o.title,
       o.location,
       o.source,
       o.source_url,
       o.posted_at,
       c.name AS employer,
       e.tier AS tier,
       e.fit_score AS fit_score
FROM applications a
JOIN opportunities o ON o.id = a.opportunity_id
JOIN companies c ON c.id = o.company_id
LEFT JOIN eval_decisions e ON e.id = (
    SELECT MAX(id) FROM eval_decisions
    WHERE opportunity_id = o.id
)
WHERE a.status NOT IN ('drafted', 'ready_to_submit')
   OR a.selected_at IS NOT NULL
ORDER BY a.status_updated_at DESC
"""


_DB_STATUS_TO_BUCKET: dict[str, str] = {
    "submitted":         "applied",
    "confirmed_received": "applied",
    "responded":         "interview",
    "interviewing":      "interview",
    "offered":           "offer",
    "rejected":          "rejected",
    "ghosted":           "rejected",
    "silent_rejected":   "rejected",
    "withdrawn":         "rejected",
}


def _bucket_for(row: dict) -> Optional[str]:
    db_status = row["status"]
    bucket = _DB_STATUS_TO_BUCKET.get(db_status)
    if bucket is not None:
        return bucket
    # Pre-apply rows split into selected vs. docs_ready by docs_ready_at.
    if db_status in ("drafted", "ready_to_submit"):
        return "docs_ready" if row.get("docs_ready_at") else "selected"
    return None


def _row_to_card(row: dict) -> Optional[BoardCard]:
    bucket = _bucket_for(row)
    if bucket is None:
        return None
    resume_text = row.get("resume_text") or ""
    cl_text = row.get("cover_letter_text") or ""
    return BoardCard(
        application_id=row["application_id"],
        opportunity_id=row["opportunity_id"],
        title=row.get("title") or "",
        employer=row.get("employer") or "",
        location=row.get("location"),
        source=row.get("source"),
        source_url=row.get("source_url"),
        posted_at=row.get("posted_at"),
        fit_score=row.get("fit_score"),
        tier=row.get("tier"),
        status=bucket,
        db_status=row["status"],
        resume_saved=bool(resume_text),
        resume_saved_at=row.get("docs_ready_at") if resume_text else None,
        resume_markdown=resume_text or None,
        cover_letter_saved=bool(cl_text),
        cover_letter_saved_at=(
            row.get("docs_ready_at") if cl_text else None
        ),
        cover_letter_markdown=cl_text or None,
        docs_ready_at=row.get("docs_ready_at"),
        notes=row.get("notes") or "",
        applied_at=row.get("submitted_date"),
        updated_at=row.get("status_updated_at") or "",
    )


@router.get("/board", response_model=BoardResponse)
def get_board(
    tracker: Tracker = Depends(get_tracker),
) -> BoardResponse:
    rows = tracker._query_all(_BOARD_SQL)
    cards: list[BoardCard] = []
    for r in rows:
        card = _row_to_card(dict(r))
        if card is not None:
            cards.append(card)

    columns: dict[str, list[BoardCard]] = {
        "selected": [], "docs_ready": [], "applied": [],
        "interview": [], "offer": [], "rejected": [],
    }
    for c in cards:
        columns[c.status].append(c)
    stats = BoardStats(
        total=len(cards),
        selected=len(columns["selected"]),
        docs_ready=len(columns["docs_ready"]),
        applied=len(columns["applied"]),
        interview=len(columns["interview"]),
        offer=len(columns["offer"]),
        rejected=len(columns["rejected"]),
    )
    return BoardResponse(columns=columns, stats=stats)


# --- Per-opportunity helpers ------------------------------------

def _latest_app_id_for(
    tracker: Tracker, opp_id: int,
) -> Optional[int]:
    row = tracker._query_one(
        "SELECT id FROM applications WHERE opportunity_id = ? "
        "ORDER BY status_updated_at DESC LIMIT 1",
        (opp_id,),
    )
    return row["id"] if row else None


def _require_opp(tracker: Tracker, opp_id: int) -> dict:
    posting = tracker.get_opportunity_by_id(opp_id)
    if posting is None:
        raise HTTPException(
            status_code=404,
            detail=f"opportunity {opp_id} not found",
        )
    return posting


class NotesRequest(BaseModel):
    notes: str


class NotesResponse(BaseModel):
    application_id: int
    opportunity_id: int
    notes: str


@router.post(
    "/by-opp/{opp_id}/notes", response_model=NotesResponse,
)
def update_notes_by_opp(
    opp_id: int,
    body: NotesRequest,
    tracker: Tracker = Depends(get_tracker),
) -> NotesResponse:
    _require_opp(tracker, opp_id)
    app_id = _latest_app_id_for(tracker, opp_id)
    if app_id is None:
        app_id = tracker.create_application(
            opportunity_id=opp_id, resume_variant="dashboard_pending",
        )
    try:
        tracker.update_application_fields(app_id, notes=body.notes)
    except TrackerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return NotesResponse(
        application_id=app_id,
        opportunity_id=opp_id,
        notes=body.notes,
    )


class StatusRequest(BaseModel):
    status: Bucket


class StatusResponse(BaseModel):
    application_id: int
    opportunity_id: int
    status: Bucket
    db_status: str


@router.post(
    "/by-opp/{opp_id}/status", response_model=StatusResponse,
)
def update_status_by_opp(
    opp_id: int,
    body: StatusRequest,
    tracker: Tracker = Depends(get_tracker),
) -> StatusResponse:
    _require_opp(tracker, opp_id)
    write_status = _BUCKET_TO_WRITE_STATUS.get(body.status)
    if write_status is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"status must be one of {sorted(_BUCKET_TO_WRITE_STATUS)}; "
                f"got {body.status!r}"
            ),
        )

    app_id = _latest_app_id_for(tracker, opp_id)
    if app_id is None:
        app_id = tracker.create_application(
            opportunity_id=opp_id, resume_variant="dashboard_pending",
        )

    try:
        tracker.update_application_status(
            app_id, write_status,
            reason="applications board stage move",
        )
    except InvalidStatusError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except TrackerError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if body.status == "applied":
        row = tracker.get_application_by_id(app_id)
        if row and not row.get("submitted_date"):
            tracker.update_application_fields(
                app_id,
                submitted_date=datetime.now(timezone.utc).isoformat(),
            )
    return StatusResponse(
        application_id=app_id,
        opportunity_id=opp_id,
        status=body.status,
        db_status=write_status,
    )


# --- Manual job entry (Spec FIX-2 TASK 9) ------------------------

class AddJobRequest(BaseModel):
    # URL mode: only `url` set. Manual mode: title + employer set.
    url: Optional[str] = None
    title: Optional[str] = None
    employer: Optional[str] = None
    location: Optional[str] = None
    source_url: Optional[str] = None
    posting_text: Optional[str] = None


class AddJobResponse(BaseModel):
    opportunity_id: int
    was_new: bool
    title: str
    employer: str
    location: Optional[str] = None
    source_url: str
    status: str = "shortlisted"


def _evaluate_in_background(profile_id: str, opp_id: int) -> None:
    """Best-effort local evaluation of a freshly-added posting.

    Runs the v2.3 local pipeline (Ollama) and persists the verdict so
    the manual-added card picks up a tier + fit score. Any failure —
    Ollama down, missing inventory, profile not configured — is logged
    and swallowed: the card still lives in the Kanban, just unscored.
    """
    try:
        from llm.client import LLMClient
        from skills.inventory import InventoryTool
        from skills.role_evaluator.pipeline import run_batch
        from skills.role_evaluator.profile_config import ProfileConfig
        from skills.role_evaluator.stage2a import Stage2a
        from scripts.run_full_eval_v2_3 import persist_verdict

        tracker = Tracker(profile_id)
        try:
            posting = tracker.get_opportunity_by_id(opp_id)
            if posting is None:
                return
            inventory = InventoryTool(profile_id)
            profile_config = ProfileConfig(profile_id)
            stage2a = Stage2a(inventory, profile_config)
            verdicts = run_batch(
                [dict(posting)],
                inventory.get_summary(),
                profile_config,
                stage2a,
            )
            verdict = verdicts.get(opp_id)
            if verdict is not None:
                persist_verdict(
                    tracker, opp_id, verdict, posting=dict(posting),
                )
        finally:
            tracker.close()
    except Exception as e:  # pragma: no cover — env-dependent
        logger.info(
            "background eval skipped for opp %s: %s", opp_id, e,
        )


def _shortlist_opportunity(tracker: Tracker, opp_id: int) -> None:
    """Mark an opportunity shortlisted and stamp a selected application
    so it lands in the Applications board's Selected column."""
    tracker.update_opportunity_status(opp_id, "shortlisted")
    existing = tracker._query_one(
        "SELECT id FROM applications WHERE opportunity_id = ? "
        "ORDER BY status_updated_at DESC LIMIT 1",
        (opp_id,),
    )
    if existing is not None:
        app_id = existing["id"]
    else:
        app_id = tracker.create_application(
            opportunity_id=opp_id, resume_variant="dashboard_pending",
        )
    tracker.update_application_fields(
        app_id, selected_at=datetime.now(timezone.utc).isoformat(),
    )


@router.post("/add", response_model=AddJobResponse)
def add_job(
    body: AddJobRequest,
    background: BackgroundTasks,
    profile_id: str = Depends(get_profile_id),
    tracker: Tracker = Depends(get_tracker),
) -> AddJobResponse:
    """Add a job the user found outside the discovery pipeline.

    URL mode (`url` set, no `title`): fetch the page, extract
    title/employer/text via BeautifulSoup.
    Manual mode (`title` + `employer` set): use the supplied fields.

    Either way the opportunity is persisted with source='manual',
    flipped straight to status='shortlisted' (the user deliberately
    chose it — discovery + triage are skipped), and queued for a
    best-effort background evaluation.
    """
    from engine.discovery.base import OpportunityRecord
    from engine.persistence.opportunities import (
        IncompleteRecordError, persist_record,
    )

    url = (body.url or "").strip()
    title = (body.title or "").strip()
    employer = (body.employer or "").strip()
    location = (body.location or "").strip()
    posting_text = (body.posting_text or "").strip()
    source_url = (body.source_url or "").strip()

    url_mode = bool(url) and not title

    if url_mode:
        from scripts.manual_entry import _guess_employer, fetch_page

        page_title, page_text = fetch_page(url)
        if not page_text:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"could not fetch or parse {url!r}; "
                    "switch to manual entry and paste the details"
                ),
            )
        title = (page_title or "").strip()[:120] or "Untitled posting"
        employer = _guess_employer(url, page_title or "") or "Unknown"
        location = location or ""
        posting_text = page_text
        source_url = url
    else:
        if not title or not employer:
            raise HTTPException(
                status_code=400,
                detail="manual entry requires both title and employer",
            )
        location = location or ""
        # source_url is NOT NULL in the schema; synthesise a stable
        # placeholder for manual rows that have no real posting URL.
        if not source_url:
            slug = "-".join(
                (employer + "-" + title).lower().split()
            )[:60]
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            source_url = f"manual://{slug}-{stamp}"

    record = OpportunityRecord(
        source="manual",
        source_url=source_url,
        employer=employer,
        title=title,
        location=location,
        posting_text=posting_text,
        posted_at=None,
        raw_payload={"entry_method": "url" if url_mode else "manual"},
        search_context={"entry_method": "dashboard_manual"},
        date_discovered=datetime.now(timezone.utc),
    )

    try:
        opp_id, was_new = persist_record(tracker, record)
    except IncompleteRecordError as e:
        raise HTTPException(status_code=400, detail=str(e))

    _shortlist_opportunity(tracker, opp_id)

    if was_new:
        background.add_task(_evaluate_in_background, profile_id, opp_id)

    return AddJobResponse(
        opportunity_id=opp_id,
        was_new=was_new,
        title=title,
        employer=employer,
        location=location,
        source_url=source_url,
    )


# --- Cloud resume / cover-letter generation (Spec FIX-3 TASK 1) --

DocType = Literal["resume", "cover_letter"]

# field name on the applications table + URL kind for docx routes
_DOC_FIELDS: dict[str, tuple[str, str]] = {
    "resume": ("resume_text", "resume"),
    "cover_letter": ("cover_letter_text", "cover-letter"),
}


class GenerateRequest(BaseModel):
    type: DocType


class GenerateResponse(BaseModel):
    opportunity_id: int
    type: str
    markdown: str
    saved: bool
    docx_path: Optional[str] = None
    docs_ready_at: Optional[str] = None


def _generate_doc(
    tracker: Tracker,
    generator,
    profile_id: str,
    opp_id: int,
    doc_type: str,
) -> GenerateResponse:
    """Build the prompt, call Gemma, save the markdown + render docx.

    Shared by the single endpoint and the batch worker. Raises
    HTTPException(404) when the opportunity is missing.
    """
    from dashboard.backend.routes.prompts import _save_text_field
    from dashboard.backend.services.prompt_service import PromptService
    from dashboard.backend.services.render_service import (
        cover_letter_pending_path, resume_pending_path,
    )

    posting = tracker.get_opportunity_by_id(opp_id)
    if posting is None:
        raise HTTPException(
            status_code=404, detail=f"opportunity {opp_id} not found",
        )
    field, _kind = _DOC_FIELDS[doc_type]
    eval_d = tracker.get_latest_evaluation(opp_id)

    service = PromptService()
    if doc_type == "resume":
        prompt = service.build_resume_prompt(
            profile_id=profile_id, posting=posting, eval_decision=eval_d,
        )
    else:
        prompt = service.build_cover_letter_prompt(
            profile_id=profile_id, posting=posting, eval_decision=eval_d,
        )

    markdown = generator.generate(
        prompt, call_type=doc_type, opportunity_id=opp_id,
    )
    if not markdown.strip():
        raise HTTPException(
            status_code=502,
            detail=f"cloud generation returned empty {doc_type}",
        )

    save = _save_text_field(
        tracker, opp_id, field, markdown, profile_id=profile_id,
    )
    docx = (
        resume_pending_path(profile_id, opp_id)
        if doc_type == "resume"
        else cover_letter_pending_path(profile_id, opp_id)
    )
    from engine.activity_log import log_activity
    log_activity(
        profile_id, "generation", f"{doc_type}_generated", "success",
        f"{doc_type.replace('_', ' ').title()} for "
        f"{posting.get('employer') or '?'} — {posting.get('title') or '?'}",
        opportunity_id=opp_id,
    )
    return GenerateResponse(
        opportunity_id=opp_id,
        type=doc_type,
        markdown=markdown,
        saved=True,
        docx_path=str(docx) if docx.exists() else None,
        docs_ready_at=save.docs_ready_at,
    )


@router.post(
    "/by-opp/{opp_id}/generate", response_model=GenerateResponse,
)
def generate_doc(
    opp_id: int,
    body: GenerateRequest,
    profile_id: str = Depends(get_profile_id),
    tracker: Tracker = Depends(get_tracker),
) -> GenerateResponse:
    """Generate one resume or cover letter for an opportunity via
    Google AI Studio, save the markdown, and render the .docx."""
    _require_opp(tracker, opp_id)
    from engine.resume.cloud_generator import CloudResumeGenerator

    generator = CloudResumeGenerator(profile_id=profile_id)
    return _generate_doc(
        tracker, generator, profile_id, opp_id, body.type,
    )


# --- Batch generate (async, in-memory progress) ------------------

# Single-process personal tool: batch progress lives in a module dict
# keyed by task_id. Lost on process restart — the frontend re-issues
# generate-all if it can't find its task.
_GENERATE_TASKS: dict[str, dict] = {}


class GenerateAllRequest(BaseModel):
    types: list[DocType] = ["resume", "cover_letter"]
    # force=True regenerates EVERY shortlisted posting, replacing
    # docs that already exist (e.g. after a contact-detail fix);
    # force=False only fills the postings still missing docs.
    force: bool = False


class GenerateAllStartResponse(BaseModel):
    task_id: str
    total: int


class GenerateAllProgress(BaseModel):
    task_id: str
    total: int
    completed: int
    status: str  # running | done | error
    errors: list[str] = []
    # opportunity_id currently being generated (None when idle/done)
    current_opp_id: Optional[int] = None
    current_employer: Optional[str] = None


def _generation_opp_ids(
    tracker: Tracker, force: bool = False,
) -> list[int]:
    """Shortlisted opportunities the batch should process.

    force=False: only postings still missing a resume or cover
    letter (the Selected-column backlog).
    force=True: every shortlisted posting, so existing docs get
    regenerated too.
    """
    rows = tracker._query_all(
        "SELECT a.opportunity_id AS oid, a.resume_text AS r, "
        "       a.cover_letter_text AS cl "
        "FROM applications a "
        "JOIN opportunities o ON o.id = a.opportunity_id "
        "WHERE a.selected_at IS NOT NULL "
        "  AND a.status = 'drafted' "
        "  AND o.status = 'shortlisted' "
        "ORDER BY a.status_updated_at DESC",
    )
    if force:
        return [r["oid"] for r in rows]
    return [r["oid"] for r in rows if not (r["r"] and r["cl"])]


# Construction seams — the batch worker runs on its own thread, so it
# needs its own Tracker (a fresh connection to the same DB file) and
# its own generator. Tests monkeypatch these to inject a temp-DB
# tracker + a fake generator without touching the network.
def _batch_tracker(profile_id: str) -> Tracker:
    return Tracker(profile_id)


def _batch_generator(profile_id: str):
    from engine.resume.cloud_generator import CloudResumeGenerator
    return CloudResumeGenerator(profile_id=profile_id)


def _run_batch_generate(
    task_id: str, profile_id: str,
    opp_ids: list[int], types: list[str], force: bool = False,
) -> None:
    """Worker thread: generate the requested doc types for each
    opportunity, updating the shared progress dict as it goes.

    force=False skips docs that already exist (cheap re-runs);
    force=True regenerates them.
    """
    state = _GENERATE_TASKS[task_id]
    tracker = _batch_tracker(profile_id)
    generator = _batch_generator(profile_id)
    try:
        for opp_id in opp_ids:
            state["current_opp_id"] = opp_id
            posting = tracker.get_opportunity_by_id(opp_id)
            state["current_employer"] = (
                (posting or {}).get("employer") if posting else None
            )
            if posting is None:
                state["errors"].append(f"opp {opp_id}: not found")
                state["completed"] += 1
                continue
            for doc_type in types:
                field, _ = _DOC_FIELDS[doc_type]
                row = tracker._query_one(
                    "SELECT id, resume_text, cover_letter_text "
                    "FROM applications WHERE opportunity_id = ? "
                    "ORDER BY status_updated_at DESC LIMIT 1",
                    (opp_id,),
                )
                # Skip docs already present unless force regeneration.
                if not force and row is not None and row[field]:
                    continue
                try:
                    _generate_doc(
                        tracker, generator, profile_id,
                        opp_id, doc_type,
                    )
                except Exception as e:  # noqa: BLE001
                    state["errors"].append(
                        f"opp {opp_id} {doc_type}: {e}"
                    )
            state["completed"] += 1
        state["status"] = "done"
    except Exception as e:  # noqa: BLE001  pragma: no cover
        state["status"] = "error"
        state["errors"].append(str(e))
    finally:
        state["current_opp_id"] = None
        state["current_employer"] = None
        tracker.close()

    from engine.activity_log import log_activity
    if state["status"] == "done":
        log_activity(
            profile_id, "generation", "batch_completed", "success",
            f"Batch generated {state['completed']} of "
            f"{state['total']} postings",
            details={"errors": state["errors"][:20]},
        )
    else:
        log_activity(
            profile_id, "generation", "batch_failed", "error",
            f"Batch generation failed after {state['completed']} "
            f"of {state['total']}",
            error_message="; ".join(state["errors"][:5]),
        )


@router.post(
    "/generate-all", response_model=GenerateAllStartResponse,
)
def start_generate_all(
    body: GenerateAllRequest,
    profile_id: str = Depends(get_profile_id),
    tracker: Tracker = Depends(get_tracker),
) -> GenerateAllStartResponse:
    """Kick off a background batch generation. force=False fills only
    postings missing docs; force=True regenerates every shortlisted
    posting. Returns a task_id to poll."""
    opp_ids = _generation_opp_ids(tracker, force=body.force)
    task_id = str(uuid.uuid4())
    _GENERATE_TASKS[task_id] = {
        "total": len(opp_ids),
        "completed": 0,
        "status": "running" if opp_ids else "done",
        "errors": [],
        "current_opp_id": None,
        "current_employer": None,
    }
    if opp_ids:
        from engine.activity_log import log_activity
        log_activity(
            profile_id, "generation", "batch_started", "running",
            f"{'Regenerate' if body.force else 'Generate'} All "
            f"started — {len(opp_ids)} postings",
        )
        thread = threading.Thread(
            target=_run_batch_generate,
            args=(
                task_id, profile_id, opp_ids,
                list(body.types), body.force,
            ),
            daemon=True,
        )
        thread.start()
    return GenerateAllStartResponse(task_id=task_id, total=len(opp_ids))


@router.get(
    "/generate-all/{task_id}", response_model=GenerateAllProgress,
)
def poll_generate_all(task_id: str) -> GenerateAllProgress:
    state = _GENERATE_TASKS.get(task_id)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"generate-all task {task_id!r} not found "
                "(process may have restarted; re-run Generate All)"
            ),
        )
    return GenerateAllProgress(
        task_id=task_id,
        total=state["total"],
        completed=state["completed"],
        status=state["status"],
        errors=list(state["errors"]),
        current_opp_id=state.get("current_opp_id"),
        current_employer=state.get("current_employer"),
    )
