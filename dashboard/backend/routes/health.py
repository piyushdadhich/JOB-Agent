"""Spec FIX-6 — System health monitor.

  GET /api/health  — rich health payload (checks + alerts)

Replaces the old trivial liveness probe. Callers that only need
"is the server up" still get a 200 with a top-level `status`
field.

Checks: source freshness, evaluator recency, last scheduled-task
run (from activity_log), API budget, and DB size.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from dashboard.backend.deps import get_profile_id, get_tracker
from engine.persistence.tracker import Tracker

router = APIRouter(prefix="/api/health", tags=["health"])


class HealthCheck(BaseModel):
    name: str
    status: str  # ok | warning | error
    message: str
    last_check: Optional[str] = None


class HealthAlert(BaseModel):
    level: str  # warning | error
    message: str


class HealthResponse(BaseModel):
    status: str  # healthy | degraded | unhealthy
    checks: list[HealthCheck]
    alerts: list[HealthAlert]


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hours_since(ts: Optional[str]) -> Optional[float]:
    dt = _parse(ts)
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


def _check_sources(tracker: Tracker) -> HealthCheck:
    rows = tracker._query_all(
        "SELECT source, MAX(last_seen_at) AS last_seen "
        "FROM opportunities GROUP BY source",
    )
    total = len(rows)
    stale = 0
    for r in rows:
        hrs = _hours_since(r["last_seen"])
        if hrs is None or hrs > 24:
            stale += 1
    status = "ok" if stale == 0 else "warning"
    return HealthCheck(
        name="sources",
        status=status,
        message=f"{total} sources, {stale} stale (>24h)",
    )


def _check_evaluator(tracker: Tracker) -> HealthCheck:
    row = tracker._query_one(
        "SELECT MAX(evaluated_at) AS last FROM eval_decisions",
    )
    last = row["last"] if row else None
    hrs = _hours_since(last)
    if hrs is None:
        return HealthCheck(
            name="evaluator", status="warning",
            message="No evaluations on record",
        )
    if hrs > 48:
        return HealthCheck(
            name="evaluator", status="warning",
            message=f"Last evaluation {int(hrs)}h ago",
            last_check=last,
        )
    return HealthCheck(
        name="evaluator", status="ok",
        message=f"Last evaluation {int(hrs)}h ago",
        last_check=last,
    )


def _check_scheduled_task(tracker: Tracker) -> HealthCheck:
    row = tracker._query_one(
        "SELECT timestamp, status, summary FROM activity_log "
        "WHERE category = 'scheduled_task' "
        "ORDER BY timestamp DESC, id DESC LIMIT 1",
    )
    if row is None:
        return HealthCheck(
            name="scheduled_task", status="warning",
            message="No scheduled-task run recorded yet",
        )
    last_status = row["status"]
    summary = row["summary"] or "scheduled task"
    if last_status == "error":
        return HealthCheck(
            name="scheduled_task", status="error",
            message=f"Last run failed: {summary}",
            last_check=row["timestamp"],
        )
    return HealthCheck(
        name="scheduled_task", status="ok",
        message=f"Last run: {summary}",
        last_check=row["timestamp"],
    )


def _check_api_budget(profile_id: str) -> HealthCheck:
    from engine import cloud_budget

    remaining = cloud_budget.calls_remaining_today(profile_id)
    limit = cloud_budget.DAILY_LIMIT
    if remaining < 10:
        status = "error"
    elif remaining < 100:
        status = "warning"
    else:
        status = "ok"
    return HealthCheck(
        name="api_budget", status=status,
        message=f"{remaining:,} / {limit:,} calls remaining today",
    )


def _check_disk(tracker: Tracker) -> HealthCheck:
    try:
        size_mb = Path(tracker.db_path).stat().st_size / (1024 * 1024)
    except OSError:
        return HealthCheck(
            name="disk", status="warning",
            message="Could not stat the database file",
        )
    status = "warning" if size_mb > 500 else "ok"
    return HealthCheck(
        name="disk", status=status,
        message=f"Database size: {size_mb:.0f} MB",
    )


@router.get("", response_model=HealthResponse)
def health(
    profile_id: str = Depends(get_profile_id),
    tracker: Tracker = Depends(get_tracker),
) -> HealthResponse:
    checks = [
        _check_sources(tracker),
        _check_evaluator(tracker),
        _check_scheduled_task(tracker),
        _check_api_budget(profile_id),
        _check_disk(tracker),
    ]
    alerts: list[HealthAlert] = []
    for c in checks:
        if c.status == "error":
            alerts.append(HealthAlert(level="error", message=c.message))
        elif c.status == "warning":
            alerts.append(
                HealthAlert(level="warning", message=c.message),
            )

    if any(c.status == "error" for c in checks):
        overall = "unhealthy"
    elif any(c.status == "warning" for c in checks):
        overall = "degraded"
    else:
        overall = "healthy"

    return HealthResponse(status=overall, checks=checks, alerts=alerts)
