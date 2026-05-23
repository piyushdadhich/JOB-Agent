"""GET /api/applications, GET /{id}, PUT /{id}/status.

The history tab + post-apply state live here. The list and single-row
queries join the application to its opportunity + company so the
caller sees the employer + role title without an extra round-trip.

The list endpoint also exposes a `stage` filter that maps the
dashboard's lifecycle to a SQL predicate, e.g. stage=needs_prompts
returns the queue the Prompts tab walks through.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from dashboard.backend.deps import get_tracker
from engine.persistence.tracker import (
    InvalidStatusError,
    Tracker,
    TrackerError,
)

router = APIRouter(prefix="/api/applications", tags=["applications"])


class Application(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int
    opportunity_id: int
    resume_variant: str
    status: str
    status_updated_at: str
    submitted_date: Optional[str] = None
    submitted_via: Optional[str] = None
    ats_platform: Optional[str] = None
    submitted_url: Optional[str] = None
    screenshot_path: Optional[str] = None
    selected_at: Optional[str] = None
    prompt_generated_at: Optional[str] = None
    docs_ready_at: Optional[str] = None
    resume_text: Optional[str] = None
    cover_letter_text: Optional[str] = None
    resume_prompt: Optional[str] = None
    cover_letter_prompt: Optional[str] = None
    employer: Optional[str] = None
    opportunity_title: Optional[str] = None
    flagged_at: Optional[str] = None     # v2.12: pulled from latest eval_decisions


_LIST_SQL_BASE = """\
SELECT a.*,
       c.name AS employer,
       o.title AS opportunity_title,
       e.flagged_at AS flagged_at
FROM applications a
JOIN opportunities o ON o.id = a.opportunity_id
JOIN companies c ON c.id = o.company_id
LEFT JOIN eval_decisions e ON e.id = (
    SELECT MAX(id) FROM eval_decisions
    WHERE opportunity_id = o.id
)
"""


# Lifecycle stage filters. Keyed by the public `stage` query param.
_STAGE_PREDICATES = {
    # Picked but resume + cover letter not both saved yet.
    "needs_prompts": (
        "a.selected_at IS NOT NULL "
        "AND a.docs_ready_at IS NULL "
        "AND a.status = 'drafted'"
    ),
    # Both docs saved; ready for the Apply tab to launch Playwright.
    "ready_to_apply": (
        "a.docs_ready_at IS NOT NULL "
        "AND a.status = 'drafted'"
    ),
    # Already submitted -- the History tab.
    "submitted": (
        "a.status NOT IN ('drafted', 'ready_to_submit')"
    ),
}


_GET_SQL = _LIST_SQL_BASE + "WHERE a.id = ?"


@router.get("", response_model=list[Application])
def list_applications(
    limit: int = 200,
    stage: Optional[str] = Query(
        default=None,
        description=(
            "Optional lifecycle filter. One of: needs_prompts, "
            "ready_to_apply, submitted."
        ),
    ),
    flagged_only: bool = Query(
        default=False,
        description=(
            "v2.12: filter to applications whose latest eval_decision "
            "has flagged_at set."
        ),
    ),
    tracker: Tracker = Depends(get_tracker),
) -> list[Application]:
    where_parts: list[str] = []
    if stage:
        predicate = _STAGE_PREDICATES.get(stage)
        if predicate is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"unknown stage {stage!r}; expected one of "
                    f"{sorted(_STAGE_PREDICATES)}"
                ),
            )
        where_parts.append(predicate)
    if flagged_only:
        where_parts.append("e.flagged_at IS NOT NULL")
    where = ("WHERE " + " AND ".join(where_parts) + " ") if where_parts else ""
    sql = (
        _LIST_SQL_BASE + where
        + "ORDER BY a.status_updated_at DESC LIMIT ?"
    )
    rows = tracker._query_all(sql, (limit,))
    return [Application(**dict(r)) for r in rows]


@router.get("/{application_id}", response_model=Application)
def get_application(
    application_id: int,
    tracker: Tracker = Depends(get_tracker),
) -> Application:
    rows = tracker._query_all(_GET_SQL, (application_id,))
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"application {application_id} not found",
        )
    return Application(**dict(rows[0]))


class StatusUpdate(BaseModel):
    status: str
    reason: Optional[str] = None


@router.put(
    "/{application_id}/status", response_model=Application,
)
def update_status(
    application_id: int,
    body: StatusUpdate,
    tracker: Tracker = Depends(get_tracker),
) -> Application:
    if tracker.get_application_by_id(application_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"application {application_id} not found",
        )
    try:
        tracker.update_application_status(
            application_id, body.status, reason=body.reason,
        )
    except InvalidStatusError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except TrackerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return get_application(application_id, tracker)
