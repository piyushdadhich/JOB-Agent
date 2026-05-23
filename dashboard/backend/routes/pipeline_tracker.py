"""Spec G1 TASK 1 — Application Pipeline Tracker API.

Kanban-style view over the applications table. The applications
table already carries every column we need (notes, status_updated_at,
selected_at, submitted_date), so no schema migration ships with this
endpoint.

The DB stores eleven canonical statuses; the dashboard collapses
those into five user-facing buckets:

  shortlisted = drafted, ready_to_submit  (selected_at IS NOT NULL)
  applied     = submitted, confirmed_received
  interview   = responded, interviewing
  offer       = offered
  rejected    = rejected, ghosted, silent_rejected, withdrawn

When the user moves a card via PUT /api/pipeline/{id}/status, the
five-bucket name maps to a canonical write target:

  shortlisted -> drafted
  applied     -> submitted
  interview   -> interviewing
  offer       -> offered
  rejected    -> rejected

The shortlist filter `selected_at IS NOT NULL` for the shortlisted
bucket is what makes Shortlist's Deselect button work end-to-end:
unselect_posting clears selected_at and the card vanishes from
the Pipeline without dropping the row (history preserved via
status_history).

Follow-up surfacing (`needs_followup`): a row is flagged when its
status_updated_at is older than the per-stage threshold (7 days
for applied, 14 days for interview). The flag drives the orange
"follow up?" banner on each card. In-app follow-up sending was
removed for v1 (see Spec 15 TASK 5) — the surfacing stays so users
can act on the prompt outside the agent.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from pydantic import ConfigDict

from dashboard.backend.deps import get_profile_id, get_tracker
from engine.persistence.tracker import (
    InvalidStatusError, Tracker, TrackerError,
)

router = APIRouter(prefix="/api/pipeline", tags=["pipeline-tracker"])

# --- Bucket mappings ---------------------------------------------

PipelineBucket = Literal[
    "shortlisted", "applied", "interview", "offer", "rejected",
]

_BUCKET_TO_DB_STATUSES: dict[str, tuple[str, ...]] = {
    "shortlisted": ("drafted", "ready_to_submit"),
    "applied":     ("submitted", "confirmed_received"),
    "interview":   ("responded", "interviewing"),
    "offer":       ("offered",),
    "rejected":    ("rejected", "ghosted", "silent_rejected", "withdrawn"),
}

_DB_STATUS_TO_BUCKET: dict[str, str] = {
    db_status: bucket
    for bucket, statuses in _BUCKET_TO_DB_STATUSES.items()
    for db_status in statuses
}

# Canonical DB status the dashboard writes when the user advances
# to a five-bucket target. Picked so the transition is unambiguous
# even when several DB statuses share a bucket.
_BUCKET_TO_WRITE_STATUS: dict[str, str] = {
    "shortlisted": "drafted",
    "applied":     "submitted",
    "interview":   "interviewing",
    "offer":       "offered",
    "rejected":    "rejected",
}

# Follow-up thresholds (days). After this much time in stage with
# no status change, surface in the daily digest as needing
# user attention.
_FOLLOWUP_DAYS_APPLIED = 7
_FOLLOWUP_DAYS_INTERVIEW = 14


# --- Response models ---------------------------------------------

class PipelineCard(BaseModel):
    model_config = ConfigDict(extra="ignore")
    application_id: int
    opportunity_id: int
    employer: str
    title: str
    location: Optional[str] = None
    tier: Optional[str] = None
    fit_score: Optional[float] = None
    status: str
    db_status: str
    applied_at: Optional[str] = None
    updated_at: str
    notes: str = ""
    days_in_stage: int
    needs_followup: bool


class PipelineStats(BaseModel):
    total: int
    shortlisted: int
    applied: int
    interview: int
    offer: int
    rejected: int
    needs_followup: int


class PipelineBoardResponse(BaseModel):
    columns: dict[str, list[PipelineCard]]
    stats: PipelineStats


class MonthlyStats(BaseModel):
    applied: int
    interview: int
    offer: int


class FollowupItem(BaseModel):
    application_id: int
    employer: str
    title: str
    applied_at: Optional[str] = None
    days_since_applied: int


class PipelineStatsResponse(BaseModel):
    total: int
    shortlisted: int
    applied: int
    interview: int
    offer: int
    rejected: int
    needs_followup: int
    this_month: MonthlyStats
    needs_followup_items: list[FollowupItem]


class StatusUpdateRequest(BaseModel):
    status: PipelineBucket


class NotesUpdateRequest(BaseModel):
    notes: str


# --- Internal helpers --------------------------------------------

_BOARD_SQL = """\
SELECT a.id AS application_id,
       a.opportunity_id,
       a.status,
       a.status_updated_at,
       a.selected_at,
       a.submitted_date,
       a.notes,
       c.name AS employer,
       o.title AS title,
       o.location AS location,
       e.tier AS tier,
       e.fit_score AS fit_score
FROM applications a
JOIN opportunities o ON o.id = a.opportunity_id
JOIN companies c ON c.id = o.company_id
LEFT JOIN eval_decisions e ON e.id = (
    SELECT MAX(id) FROM eval_decisions
    WHERE opportunity_id = o.id
)
WHERE
  -- Drafted/ready_to_submit only show up after the user has
  -- explicitly selected them on the Shortlist (selected_at set).
  -- Once an application progresses past drafted/ready_to_submit,
  -- selected_at is no longer load-bearing.
  (
    a.status NOT IN ('drafted', 'ready_to_submit')
    OR a.selected_at IS NOT NULL
  )
ORDER BY a.status_updated_at DESC
"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _days_between(then: Optional[datetime], now: datetime) -> int:
    if then is None:
        return 0
    return max(0, (now - then).days)


def _is_followup(bucket: str, days_in_stage: int) -> bool:
    """A card needs follow-up when it sits in 'applied' (>7d) or
    'interview' (>14d) without a status change."""
    threshold = (
        _FOLLOWUP_DAYS_APPLIED if bucket == "applied"
        else _FOLLOWUP_DAYS_INTERVIEW if bucket == "interview"
        else None
    )
    if threshold is None:
        return False
    return days_in_stage > threshold


def _row_to_card(row: dict, now: datetime) -> Optional[PipelineCard]:
    """Convert a board SQL row to a PipelineCard. Returns None if
    the DB status doesn't map to any of our five buckets — safer
    than silently bucketing into 'shortlisted'."""
    db_status = row["status"]
    bucket = _DB_STATUS_TO_BUCKET.get(db_status)
    if bucket is None:
        return None
    updated_at = row["status_updated_at"]
    parsed_updated = _parse_iso(updated_at) or now
    days_in_stage = _days_between(parsed_updated, now)
    return PipelineCard(
        application_id=row["application_id"],
        opportunity_id=row["opportunity_id"],
        employer=row["employer"] or "",
        title=row["title"] or "",
        location=row["location"],
        tier=row["tier"],
        fit_score=row["fit_score"],
        status=bucket,
        db_status=db_status,
        applied_at=row["submitted_date"],
        updated_at=updated_at,
        notes=row["notes"] or "",
        days_in_stage=days_in_stage,
        needs_followup=_is_followup(bucket, days_in_stage),
    )


def _load_cards(tracker: Tracker) -> list[PipelineCard]:
    now = _utcnow()
    rows = tracker._query_all(_BOARD_SQL)
    out: list[PipelineCard] = []
    for r in rows:
        card = _row_to_card(dict(r), now)
        if card is not None:
            out.append(card)
    return out


def _group_into_columns(
    cards: list[PipelineCard],
) -> dict[str, list[PipelineCard]]:
    columns: dict[str, list[PipelineCard]] = {
        k: [] for k in _BUCKET_TO_DB_STATUSES
    }
    for c in cards:
        columns[c.status].append(c)
    return columns


def _build_stats(cards: list[PipelineCard]) -> PipelineStats:
    counts = {k: 0 for k in _BUCKET_TO_DB_STATUSES}
    followups = 0
    for c in cards:
        counts[c.status] += 1
        if c.needs_followup:
            followups += 1
    return PipelineStats(
        total=len(cards),
        shortlisted=counts["shortlisted"],
        applied=counts["applied"],
        interview=counts["interview"],
        offer=counts["offer"],
        rejected=counts["rejected"],
        needs_followup=followups,
    )


# --- Endpoints ---------------------------------------------------

@router.get("/board", response_model=PipelineBoardResponse)
def get_board(
    tracker: Tracker = Depends(get_tracker),
) -> PipelineBoardResponse:
    cards = _load_cards(tracker)
    return PipelineBoardResponse(
        columns=_group_into_columns(cards),
        stats=_build_stats(cards),
    )


@router.get("/stats", response_model=PipelineStatsResponse)
def get_stats(
    tracker: Tracker = Depends(get_tracker),
) -> PipelineStatsResponse:
    cards = _load_cards(tracker)
    base = _build_stats(cards)
    now = _utcnow()
    month_start = now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0,
    )
    # Calendar-month totals are computed off status_history so a
    # status that moved into "applied" -> "rejected" inside the same
    # month still counts as one applied transition. Without joining
    # status_history we'd undercount any card that has already
    # progressed past the month's headline buckets.
    rows = tracker._query_all(
        "SELECT to_status, COUNT(*) AS c FROM status_history "
        "WHERE changed_at >= ? GROUP BY to_status",
        (month_start.isoformat(),),
    )
    counts_by_db_status: dict[str, int] = {
        r["to_status"]: int(r["c"]) for r in rows
    }
    month_applied = sum(
        counts_by_db_status.get(s, 0)
        for s in _BUCKET_TO_DB_STATUSES["applied"]
    )
    month_interview = sum(
        counts_by_db_status.get(s, 0)
        for s in _BUCKET_TO_DB_STATUSES["interview"]
    )
    month_offer = sum(
        counts_by_db_status.get(s, 0)
        for s in _BUCKET_TO_DB_STATUSES["offer"]
    )
    followup_items: list[FollowupItem] = []
    for c in cards:
        if not c.needs_followup:
            continue
        parsed_applied = _parse_iso(c.applied_at) or _parse_iso(c.updated_at)
        days = _days_between(parsed_applied, now)
        followup_items.append(FollowupItem(
            application_id=c.application_id,
            employer=c.employer,
            title=c.title,
            applied_at=c.applied_at,
            days_since_applied=days,
        ))
    followup_items.sort(
        key=lambda x: x.days_since_applied, reverse=True,
    )
    return PipelineStatsResponse(
        total=base.total,
        shortlisted=base.shortlisted,
        applied=base.applied,
        interview=base.interview,
        offer=base.offer,
        rejected=base.rejected,
        needs_followup=base.needs_followup,
        this_month=MonthlyStats(
            applied=month_applied,
            interview=month_interview,
            offer=month_offer,
        ),
        needs_followup_items=followup_items,
    )


_CARD_SQL = """\
SELECT a.id AS application_id,
       a.opportunity_id,
       a.status,
       a.status_updated_at,
       a.selected_at,
       a.submitted_date,
       a.notes,
       c.name AS employer,
       o.title AS title,
       o.location AS location,
       e.tier AS tier,
       e.fit_score AS fit_score
FROM applications a
JOIN opportunities o ON o.id = a.opportunity_id
JOIN companies c ON c.id = o.company_id
LEFT JOIN eval_decisions e ON e.id = (
    SELECT MAX(id) FROM eval_decisions
    WHERE opportunity_id = o.id
)
WHERE a.id = ?
"""


def _refresh_card(
    tracker: Tracker, application_id: int,
) -> PipelineCard:
    row = tracker._query_one(_CARD_SQL, (application_id,))
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"application {application_id} not found",
        )
    card = _row_to_card(dict(row), _utcnow())
    if card is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"application {application_id} has status "
                f"{row['status']!r} which is not surfaced in the "
                "pipeline board"
            ),
        )
    return card


@router.put(
    "/{application_id}/status", response_model=PipelineCard,
)
def update_status(
    application_id: int,
    body: StatusUpdateRequest,
    tracker: Tracker = Depends(get_tracker),
) -> PipelineCard:
    write_status = _BUCKET_TO_WRITE_STATUS.get(body.status)
    if write_status is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"status must be one of "
                f"{sorted(_BUCKET_TO_WRITE_STATUS)}; got {body.status!r}"
            ),
        )
    if tracker.get_application_by_id(application_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"application {application_id} not found",
        )
    try:
        tracker.update_application_status(
            application_id, write_status,
            reason="pipeline tracker stage move",
        )
    except InvalidStatusError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except TrackerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Stamp submitted_date the first time the card enters Applied,
    # so the daily-digest "days_since_applied" stays stable even if
    # the card later bounces through Interview -> Applied (rare,
    # but possible via the dashboard's free-form transitions).
    if body.status == "applied":
        existing = tracker.get_application_by_id(application_id)
        if existing and not existing.get("submitted_date"):
            tracker.update_application_fields(
                application_id, submitted_date=_utcnow().isoformat(),
            )
    return _refresh_card(tracker, application_id)


@router.put(
    "/{application_id}/notes", response_model=PipelineCard,
)
def update_notes(
    application_id: int,
    body: NotesUpdateRequest,
    tracker: Tracker = Depends(get_tracker),
) -> PipelineCard:
    if tracker.get_application_by_id(application_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"application {application_id} not found",
        )
    try:
        tracker.update_application_fields(
            application_id, notes=body.notes,
        )
    except TrackerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _refresh_card(tracker, application_id)
