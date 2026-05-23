"""Drive the Playwright apply flow from the dashboard.

POST /api/apply/{posting_id}/start         launch headed browser
POST /api/apply/{posting_id}/submit        click submit (after review)
POST /api/apply/{posting_id}/abort         close browser, no submit
POST /api/apply/{posting_id}/mark-applied  manual record (LinkedIn)
GET  /api/apply/status                     current session shape

The actual work is in services/apply_service.py; this module is the
HTTP shell. Personal tool, so we hold one in-flight session globally.
"""
from __future__ import annotations

import logging
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dashboard.backend.deps import get_profile_id, get_tracker
from dashboard.backend.services.apply_service import (
    ApplyService,
    get_apply_service,
)
from dashboard.backend.services.render_service import (
    cover_letter_pending_path,
    resume_pending_path,
)
from engine.applicant.handlers.base import (
    FillContext,
    detect_ats_from_url,
)
from engine.applicant.profile import load_applicant_profile
from engine.applicant.qa_matcher import QAMatcher
from engine.applicant.storage import save_application
from engine.persistence.tracker import Tracker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/apply", tags=["apply"])

# Built-in handler registry. Mirrors scripts/apply.py.
def _build_handler_registry() -> dict[str, type]:
    from engine.applicant.handlers.ashby import AshbyHandler
    from engine.applicant.handlers.greenhouse import GreenhouseHandler
    from engine.applicant.handlers.lever import LeverHandler
    from engine.applicant.handlers.workday import WorkdayHandler
    return {
        "greenhouse": GreenhouseHandler,
        "lever": LeverHandler,
        "ashby": AshbyHandler,
        "workday": WorkdayHandler,
    }


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _screenshots_dir(profile_id: str) -> Path:
    return (
        PROJECT_ROOT / "data" / profile_id
        / "applications" / "screenshots"
    )


def _browser_profile_dir(profile_id: str) -> Path:
    """Persistent browser profile directory — keeps cookies and logins
    around so the user only signs into Greenhouse/Workday/etc. once."""
    return PROJECT_ROOT / "data" / profile_id / "browser_profile"


def _is_linkedin(url: str) -> bool:
    """True when the URL's host is on linkedin.com — i.e. the apply
    flow stays on LinkedIn (Easy Apply), not a company ATS."""
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    return host == "linkedin.com" or host.endswith(".linkedin.com")


async def _resolve_final_url(url: str) -> str:
    """Navigate to `url` in a headless browser, follow every redirect
    (HTTP 3xx, JS, and meta), and return the final landed URL.

    Most LinkedIn job postings bounce through to the employer's real
    ATS (Greenhouse / Lever / Workday); only true Easy-Apply postings
    stay on linkedin.com. Resolving the final URL is the only reliable
    way to tell the two apart. On any navigation failure we return the
    original URL — the caller then treats it as "still LinkedIn" and
    falls back to manual apply, which is the safe default.
    """
    from playwright.async_api import async_playwright

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(
                    url, timeout=45_000, wait_until="domcontentloaded",
                )
                try:
                    await page.wait_for_load_state(
                        "networkidle", timeout=15_000,
                    )
                except Exception:
                    pass
                return page.url or url
            finally:
                await browser.close()
    except Exception as e:  # pragma: no cover — env-dependent
        logger.warning("redirect resolution failed for %s: %s", url, e)
        return url


def _build_smart_filler(profile_id: str, fill_ctx):
    """Construct a SmartFormFiller wired to the cloud LLM. Returns None
    if the Gemma cloud client cannot be initialized — the caller will
    surface a 400 to the dashboard so the user can fall back to the
    handler path."""
    try:
        from engine.applicant.smart_filler import SmartFormFiller
        from engine.discovery.smart_scraper import SmartScraper
        from engine.llm.gemma_cloud_client import GemmaCloudClient
    except ImportError as e:  # pragma: no cover -- defensive
        logger.warning("smart_filler deps missing: %s", e)
        return None
    try:
        cloud = GemmaCloudClient(profile_id=profile_id)
    except Exception as e:
        logger.info("no Gemma client (smart filler disabled): %s", e)
        return None
    inventory_path = (
        PROJECT_ROOT / "source_materials" / profile_id
        / "career_inventory.md"
    )
    inventory = (
        inventory_path.read_text(encoding="utf-8")
        if inventory_path.exists() else ""
    )
    return SmartFormFiller(
        scraper=SmartScraper(),
        cloud_client=cloud,
        profile=fill_ctx.profile,
        inventory_summary=inventory,
        qa_matcher=fill_ctx.qa_matcher,
    )


def _build_qa_generator(profile_id: str):
    """Wire QAGenerator + GemmaCloudClient if configured. Returns
    None if there's no API key on disk -- the apply flow will simply
    skip Tier-2 proposals and show questions as 'skipped' for the
    user to handle in the browser."""
    try:
        from engine.applicant.qa_generator import QAGenerator
        from engine.llm.gemma_cloud_client import GemmaCloudClient
    except ImportError as e:  # pragma: no cover -- defensive
        logger.warning("qa_generator deps missing: %s", e)
        return None
    try:
        client = GemmaCloudClient(profile_id=profile_id)
    except Exception as e:
        logger.info(
            "no Gemma client (Tier-2 disabled): %s", e,
        )
        return None
    inventory_path = (
        PROJECT_ROOT / "source_materials" / profile_id
        / "career_inventory.md"
    )
    inventory = (
        inventory_path.read_text(encoding="utf-8")
        if inventory_path.exists() else ""
    )
    return QAGenerator(
        cloud_client=client,
        inventory_summary=inventory,
        interactive=False,
    )


# --- Pydantic shapes ---------------------------------------------

class StartResponse(BaseModel):
    state: str
    posting_id: int
    ats: Optional[str] = None
    detail: Optional[str] = None


class StatusResponse(BaseModel):
    active: bool
    session: Optional[dict] = None


class MarkAppliedRequest(BaseModel):
    notes: Optional[str] = None


class AnswerRequest(BaseModel):
    question: str
    answer: str


class AnswerResponse(BaseModel):
    cached: bool
    question: str


# --- Helpers ------------------------------------------------------

def _latest_application_for(
    tracker: Tracker, posting_id: int,
) -> Optional[dict]:
    rows = tracker._query_all(
        "SELECT * FROM applications WHERE opportunity_id = ? "
        "ORDER BY status_updated_at DESC LIMIT 1",
        (posting_id,),
    )
    return dict(rows[0]) if rows else None


# --- Routes ------------------------------------------------------

@router.post("/{posting_id}/start", response_model=StartResponse)
async def start_apply(
    posting_id: int,
    dry_run: bool = False,
    smart: bool = False,
    profile_id: str = Depends(get_profile_id),
    tracker: Tracker = Depends(get_tracker),
    apply: ApplyService = Depends(get_apply_service),
) -> StartResponse:
    if apply.is_active():
        raise HTTPException(
            status_code=409,
            detail=(
                "another apply session is already in flight; "
                "abort it first via POST /api/apply/{id}/abort"
            ),
        )
    posting = tracker.get_opportunity_by_id(posting_id)
    if posting is None:
        raise HTTPException(
            status_code=404,
            detail=f"opportunity {posting_id} not found",
        )
    source_url = posting.get("source_url") or ""
    ats = detect_ats_from_url(source_url)

    # LinkedIn postings are NOT auto-blocked: most "LinkedIn jobs"
    # bounce through to the employer's real ATS. Navigate the posting,
    # follow every redirect, and judge the FINAL URL. Only postings
    # that stay on linkedin.com are true Easy Apply — those we hand
    # back to the user's browser.
    if ats == "linkedin":
        final_url = await _resolve_final_url(source_url)
        if _is_linkedin(final_url):
            webbrowser.open(final_url)
            return StartResponse(
                state="linkedin_easy_apply",
                posting_id=posting_id,
                ats="linkedin",
                detail=(
                    "This is LinkedIn Easy Apply — opened in your "
                    "browser. Submit it manually there, then click "
                    "Mark Applied."
                ),
            )
        # Redirected off LinkedIn to a real ATS — apply against it.
        source_url = final_url
        ats = detect_ats_from_url(final_url)
        if ats is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"LinkedIn posting redirected to {final_url!r}, "
                    "which matched no ATS handler; open it manually "
                    "and use mark-applied if you submit"
                ),
            )
    elif ats is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"no ATS handler matched URL {source_url!r}; "
                "open it manually and use mark-applied if you submit"
            ),
        )

    handler_cls = _build_handler_registry().get(ats)
    if handler_cls is None:
        raise HTTPException(
            status_code=400,
            detail=f"no handler registered for ATS {ats!r}",
        )

    try:
        profile = load_applicant_profile(profile_id)
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=400,
            detail=(
                f"applicant profile not configured for "
                f"{profile_id!r}: {e}"
            ),
        )
    missing = profile.required_fields_missing()
    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                "applicant profile is missing required PII fields: "
                f"{', '.join(missing)}"
            ),
        )

    resume_path = resume_pending_path(profile_id, posting_id)
    cl_path = cover_letter_pending_path(profile_id, posting_id)
    if not resume_path.exists() or not cl_path.exists():
        raise HTTPException(
            status_code=400,
            detail=(
                "resume or cover letter .docx missing; render them "
                "via the Prompts tab first"
            ),
        )

    app_row = _latest_application_for(tracker, posting_id) or {}
    fill_ctx = FillContext(
        posting=posting,
        profile=profile,
        resume_path=resume_path,
        cover_letter_path=cl_path,
        resume_text=app_row.get("resume_text") or "",
        cover_letter_text=app_row.get("cover_letter_text") or "",
        qa_matcher=QAMatcher.from_yaml(profile=profile),
        dry_run=dry_run,
    )

    run_context = {
        "handler": handler_cls(),
        "fill_context": fill_ctx,
        "source_url": source_url,
        "tracker_factory": lambda pid: Tracker(pid),
        "pending_paths": (resume_path, cl_path),
        "screenshots_dir": _screenshots_dir(profile_id),
        "project_root": PROJECT_ROOT,
        "qa_generator": _build_qa_generator(profile_id),
        "user_data_dir": _browser_profile_dir(profile_id),
    }

    runner_override = None
    if smart:
        smart_filler = _build_smart_filler(profile_id, fill_ctx)
        if smart_filler is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "smart filler unavailable — Gemma cloud client not "
                    "configured. Drop the ?smart=true flag to use the "
                    "ATS handler instead."
                ),
            )
        run_context["smart_filler"] = smart_filler
        from dashboard.backend.services.apply_service import (
            _smart_filler_runner,
        )
        runner_override = _smart_filler_runner

    session = await apply.start(
        posting_id=posting_id,
        profile_id=profile_id,
        ats=ats,
        run_context=run_context,
        dry_run=dry_run,
        runner=runner_override,
        using_smart_filler=smart,
    )
    return StartResponse(
        state=session.state, posting_id=posting_id, ats=ats,
    )


@router.post("/{posting_id}/submit")
async def submit_apply(
    posting_id: int,
    apply: ApplyService = Depends(get_apply_service),
) -> dict:
    s = apply.current()
    if s is None or s.posting_id != posting_id:
        raise HTTPException(
            status_code=409,
            detail="no active session for this posting",
        )
    if not apply.submit():
        raise HTTPException(
            status_code=409,
            detail=f"session is not ready_for_submit (state={s.state})",
        )
    return {"state": s.state, "posting_id": posting_id}


@router.post("/{posting_id}/approve-plan")
async def approve_plan(
    posting_id: int,
    apply: ApplyService = Depends(get_apply_service),
) -> dict:
    """Smart-filler only: user approved the LLM's fill plan; runner
    proceeds to execute it."""
    s = apply.current()
    if s is None or s.posting_id != posting_id:
        raise HTTPException(
            status_code=409,
            detail="no active session for this posting",
        )
    if not apply.approve_plan():
        raise HTTPException(
            status_code=409,
            detail=(
                f"session is not awaiting_plan_approval "
                f"(state={s.state})"
            ),
        )
    return {"state": s.state, "posting_id": posting_id}


@router.post("/{posting_id}/skip-plan")
async def skip_plan(
    posting_id: int,
    apply: ApplyService = Depends(get_apply_service),
) -> dict:
    """Smart-filler only: user wants to fill the form manually instead
    of executing the plan. Runner advances to ready_for_submit."""
    s = apply.current()
    if s is None or s.posting_id != posting_id:
        raise HTTPException(
            status_code=409,
            detail="no active session for this posting",
        )
    if not apply.skip_plan():
        raise HTTPException(
            status_code=409,
            detail=(
                f"session is not awaiting_plan_approval "
                f"(state={s.state})"
            ),
        )
    return {"state": s.state, "posting_id": posting_id}


@router.post("/{posting_id}/abort")
async def abort_apply(
    posting_id: int,
    apply: ApplyService = Depends(get_apply_service),
) -> dict:
    s = apply.current()
    if s is None or s.posting_id != posting_id:
        raise HTTPException(
            status_code=409,
            detail="no active session for this posting",
        )
    apply.abort()
    return {"state": s.state, "posting_id": posting_id}


@router.post("/{posting_id}/mark-applied")
async def mark_applied(
    posting_id: int,
    body: MarkAppliedRequest,
    profile_id: str = Depends(get_profile_id),
    tracker: Tracker = Depends(get_tracker),
) -> dict:
    """Record a manual application (e.g. LinkedIn Easy Apply that the
    user submitted in their own browser). Reuses the existing
    application row if one exists; otherwise creates a fresh one."""
    posting = tracker.get_opportunity_by_id(posting_id)
    if posting is None:
        raise HTTPException(
            status_code=404,
            detail=f"opportunity {posting_id} not found",
        )

    app_row = _latest_application_for(tracker, posting_id)
    today = datetime.now(timezone.utc).isoformat()
    if app_row is None:
        app_id = save_application(
            tracker,
            opportunity_id=posting_id,
            resume_variant="manual",
            resume_text="",
            cover_letter_text="",
            ats_platform="manual",
            submitted_url=posting.get("source_url"),
            screening_answers=None,
            submitted_via="direct",
            final_status="submitted",
        )
    else:
        app_id = app_row["id"]
        tracker.update_application_fields(
            app_id,
            ats_platform=app_row.get("ats_platform") or "manual",
            submitted_url=(
                app_row.get("submitted_url")
                or posting.get("source_url")
            ),
            submitted_date=today,
        )
        if app_row.get("status") not in {"submitted",
                                          "confirmed_received"}:
            tracker.update_application_status(
                app_id, "submitted",
                reason=body.notes or "marked applied via dashboard",
            )
    return {
        "application_id": app_id,
        "state": "submitted",
        "posting_id": posting_id,
    }


@router.post("/answer", response_model=AnswerResponse)
async def confirm_answer(
    body: AnswerRequest,
    profile_id: str = Depends(get_profile_id),
    apply: ApplyService = Depends(get_apply_service),
) -> AnswerResponse:
    """Persist a user-confirmed (or user-edited) screening answer.

    Updates the in-flight session's pending_questions entry so the
    UI shows it as confirmed, and writes the answer to the cache so
    future identical questions resolve via Tier 1 immediately.
    """
    answer = (body.answer or "").strip()
    if not answer:
        raise HTTPException(
            status_code=400, detail="answer must be non-empty",
        )

    s = apply.current()
    if s is not None:
        for entry in s.pending_questions:
            if entry["question"] == body.question:
                entry["draft"] = answer
                entry["confirmed"] = True
                break

    qa_generator = _build_qa_generator(profile_id)
    if qa_generator is not None:
        try:
            qa_generator.confirm_answer(body.question, answer)
        except Exception as e:
            logger.warning("qa cache write failed: %s", e)
            return AnswerResponse(cached=False, question=body.question)
        return AnswerResponse(cached=True, question=body.question)
    return AnswerResponse(cached=False, question=body.question)


@router.get("/status", response_model=StatusResponse)
async def status(
    apply: ApplyService = Depends(get_apply_service),
) -> StatusResponse:
    s = apply.current()
    if s is None:
        return StatusResponse(active=False)
    return StatusResponse(active=apply.is_active(), session=s.to_status())


# --- Batch apply queue (Spec 2 TASK 2) ----------------------------
#
# Single-user, single-process design: batch state lives in an in-memory
# dict keyed by batch_id. If the FastAPI process restarts, in-flight
# batches are lost and the user re-selects on the Shortlist tab. No
# persistence to DB — adding it would just slow the v1 down. The
# spec accepts this trade-off.

import uuid as _uuid
from dataclasses import dataclass as _dataclass, field as _dc_field
from typing import Optional as _Opt


@_dataclass
class _BatchState:
    id: str
    opportunity_ids: list[int]
    current_index: int = 0
    applied_count: int = 0
    skipped_count: int = 0
    notes: list[str] = _dc_field(default_factory=list)


_BATCHES: dict[str, _BatchState] = {}


class BatchStartRequest(BaseModel):
    opportunity_ids: list[int]


class BatchStartResponse(BaseModel):
    batch_id: str
    total: int


class BatchAdvanceRequest(BaseModel):
    action: str  # "applied" | "skipped"


class BatchNextResponse(BaseModel):
    done: bool
    batch_id: str
    total: int
    position: _Opt[int] = None    # 1-based posting position when not done
    applied: int = 0
    skipped: int = 0
    opportunity_id: _Opt[int] = None
    employer: _Opt[str] = None
    title: _Opt[str] = None
    source_url: _Opt[str] = None
    notes: list[str] = []


def _batch_already_applied(
    tracker: Tracker, opportunity_id: int,
) -> _Opt[str]:
    """Return the application status if the posting has already been
    submitted (status NOT IN ('drafted', 'ready_to_submit')); else None."""
    row = tracker._query_one(
        "SELECT status FROM applications "
        "WHERE opportunity_id = ? "
        "ORDER BY status_updated_at DESC LIMIT 1",
        (opportunity_id,),
    )
    if row is None:
        return None
    status = row["status"]
    if status in ("drafted", "ready_to_submit"):
        return None
    return status


def _build_batch_next_response(
    state: _BatchState, tracker: Tracker,
) -> BatchNextResponse:
    """Walk forward through the queue, auto-skipping already-applied
    postings, and return either the next pending posting OR the done
    state."""
    while state.current_index < len(state.opportunity_ids):
        pid = state.opportunity_ids[state.current_index]
        already = _batch_already_applied(tracker, pid)
        if already is not None:
            state.skipped_count += 1
            state.notes.append(
                f"posting {pid}: auto-skipped (status={already})"
            )
            state.current_index += 1
            continue
        posting = tracker.get_opportunity_by_id(pid)
        if posting is None:
            state.skipped_count += 1
            state.notes.append(f"posting {pid}: not found, skipped")
            state.current_index += 1
            continue
        return BatchNextResponse(
            done=False,
            batch_id=state.id,
            total=len(state.opportunity_ids),
            position=state.current_index + 1,
            applied=state.applied_count,
            skipped=state.skipped_count,
            opportunity_id=pid,
            employer=posting.get("employer"),
            title=posting.get("title"),
            source_url=posting.get("source_url"),
            notes=list(state.notes),
        )
    return BatchNextResponse(
        done=True,
        batch_id=state.id,
        total=len(state.opportunity_ids),
        applied=state.applied_count,
        skipped=state.skipped_count,
        notes=list(state.notes),
    )


@router.post("/batch", response_model=BatchStartResponse)
def start_batch(
    body: BatchStartRequest,
    tracker: Tracker = Depends(get_tracker),
) -> BatchStartResponse:
    """Create a new batch from a user-picked list of opportunity_ids.

    Returns a batch_id the frontend uses to drive the sequenced apply
    flow via GET /api/apply/batch/{id}/next + POST .../advance.
    """
    if not body.opportunity_ids:
        raise HTTPException(
            status_code=400,
            detail="opportunity_ids must be non-empty",
        )
    for pid in body.opportunity_ids:
        if tracker.get_opportunity_by_id(pid) is None:
            raise HTTPException(
                status_code=404,
                detail=f"opportunity {pid} not found",
            )
    batch_id = str(_uuid.uuid4())
    _BATCHES[batch_id] = _BatchState(
        id=batch_id,
        opportunity_ids=list(body.opportunity_ids),
    )
    return BatchStartResponse(
        batch_id=batch_id, total=len(body.opportunity_ids),
    )


@router.get(
    "/batch/{batch_id}/next", response_model=BatchNextResponse,
)
def batch_next(
    batch_id: str,
    tracker: Tracker = Depends(get_tracker),
) -> BatchNextResponse:
    state = _BATCHES.get(batch_id)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"batch {batch_id!r} not found "
                "(process may have restarted; "
                "re-select on the Shortlist tab)"
            ),
        )
    return _build_batch_next_response(state, tracker)


@router.post(
    "/batch/{batch_id}/advance", response_model=BatchNextResponse,
)
def batch_advance(
    batch_id: str,
    body: BatchAdvanceRequest,
    tracker: Tracker = Depends(get_tracker),
) -> BatchNextResponse:
    state = _BATCHES.get(batch_id)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"batch {batch_id!r} not found "
                "(process may have restarted; "
                "re-select on the Shortlist tab)"
            ),
        )
    if body.action not in ("applied", "skipped"):
        raise HTTPException(
            status_code=400,
            detail=(
                f"action must be 'applied' or 'skipped', "
                f"got {body.action!r}"
            ),
        )
    if state.current_index < len(state.opportunity_ids):
        if body.action == "applied":
            state.applied_count += 1
        else:
            state.skipped_count += 1
        state.current_index += 1
    return _build_batch_next_response(state, tracker)
