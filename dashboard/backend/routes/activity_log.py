"""Spec FIX-6 — Activity Log API.

  GET /api/activity-log          paginated feed (optional category)
  GET /api/activity-log/summary  last-run-per-category + 24h error counts

Read-only over the activity_log table populated by
engine.activity_log.log_activity.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from dashboard.backend.deps import get_tracker
from engine.persistence.tracker import Tracker

router = APIRouter(prefix="/api/activity-log", tags=["activity-log"])


class ActivityEntry(BaseModel):
    id: int
    timestamp: str
    category: str
    action: str
    status: str
    summary: Optional[str] = None
    details: Optional[dict] = None
    opportunity_id: Optional[int] = None
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None


class ActivityLogResponse(BaseModel):
    items: list[ActivityEntry]
    total: int


def _row_to_entry(row: dict) -> ActivityEntry:
    details = None
    raw = row.get("details")
    if raw:
        try:
            decoded = json.loads(raw)
            details = decoded if isinstance(decoded, dict) else None
        except (TypeError, json.JSONDecodeError):
            details = None
    return ActivityEntry(
        id=row["id"],
        timestamp=row["timestamp"],
        category=row["category"],
        action=row["action"],
        status=row["status"],
        summary=row.get("summary"),
        details=details,
        opportunity_id=row.get("opportunity_id"),
        duration_ms=row.get("duration_ms"),
        error_message=row.get("error_message"),
    )


@router.get("", response_model=ActivityLogResponse)
def list_activity(
    category: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    tracker: Tracker = Depends(get_tracker),
) -> ActivityLogResponse:
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    where = ""
    params: list = []
    if category and category != "all":
        where = "WHERE category = ?"
        params.append(category)

    total_row = tracker._query_one(
        f"SELECT COUNT(*) AS c FROM activity_log {where}",
        tuple(params),
    )
    total = int(total_row["c"]) if total_row else 0

    rows = tracker._query_all(
        f"SELECT * FROM activity_log {where} "
        "ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    )
    return ActivityLogResponse(
        items=[_row_to_entry(dict(r)) for r in rows],
        total=total,
    )


class LastRun(BaseModel):
    timestamp: str
    summary: Optional[str] = None
    status: str


class ActivitySummary(BaseModel):
    last_discovery: Optional[LastRun] = None
    last_evaluation: Optional[LastRun] = None
    last_generation: Optional[LastRun] = None
    last_scheduled_task: Optional[LastRun] = None
    errors_last_24h: int
    warnings_last_24h: int


def _last_in_category(tracker: Tracker, category: str) -> Optional[LastRun]:
    row = tracker._query_one(
        "SELECT timestamp, summary, status FROM activity_log "
        "WHERE category = ? ORDER BY timestamp DESC, id DESC LIMIT 1",
        (category,),
    )
    if row is None:
        return None
    return LastRun(
        timestamp=row["timestamp"],
        summary=row["summary"],
        status=row["status"],
    )


@router.get("/summary", response_model=ActivitySummary)
def activity_summary(
    tracker: Tracker = Depends(get_tracker),
) -> ActivitySummary:
    def count_status(status: str) -> int:
        row = tracker._query_one(
            "SELECT COUNT(*) AS c FROM activity_log "
            "WHERE status = ? "
            "AND timestamp >= datetime('now', '-24 hours')",
            (status,),
        )
        return int(row["c"]) if row else 0

    return ActivitySummary(
        last_discovery=_last_in_category(tracker, "discovery"),
        last_evaluation=_last_in_category(tracker, "evaluation"),
        last_generation=_last_in_category(tracker, "generation"),
        last_scheduled_task=_last_in_category(tracker, "scheduled_task"),
        errors_last_24h=count_status("error"),
        warnings_last_24h=count_status("warning"),
    )
