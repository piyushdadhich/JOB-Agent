"""Read-only aggregation endpoints for the Stats tab.

Four narrow GETs, each returning a small dict the frontend can drop
straight into a widget. No mutation, no caching -- the queries are
cheap on a personal SQLite tracker (a few hundred rows per table).

  GET /api/stats/today          opportunities + evals discovered today
  GET /api/stats/sources        per-source health (counts + last seen)
  GET /api/stats/evaluator      evaluator version + today's call count
  GET /api/stats/applications   applications grouped by status / week
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from dashboard.backend.deps import get_tracker
from engine.persistence.tracker import Tracker

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _today_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .isoformat()
    )


def _seven_days_ago_iso() -> str:
    return (
        (datetime.now(timezone.utc) - timedelta(days=7))
        .isoformat()
    )


# --- Pipeline counts (today) -------------------------------------

class TodayStats(BaseModel):
    opportunities_total: int
    opportunities_today: int
    companies_total: int
    evaluations_today: int
    top_tier_today: int
    strong_today: int
    exploratory_today: int


@router.get("/today", response_model=TodayStats)
def today_stats(
    tracker: Tracker = Depends(get_tracker),
) -> TodayStats:
    today = _today_iso()
    opportunities_total = tracker._query_one(
        "SELECT COUNT(*) AS c FROM opportunities",
    )["c"]
    opportunities_today = tracker._query_one(
        "SELECT COUNT(*) AS c FROM opportunities "
        "WHERE date_discovered >= ?",
        (today,),
    )["c"]
    companies_total = tracker._query_one(
        "SELECT COUNT(*) AS c FROM companies",
    )["c"]

    eval_rows = tracker._query_all(
        "SELECT tier, COUNT(*) AS c FROM eval_decisions "
        "WHERE evaluated_at >= ? GROUP BY tier",
        (today,),
    )
    by_tier = {r["tier"]: r["c"] for r in eval_rows}
    return TodayStats(
        opportunities_total=opportunities_total,
        opportunities_today=opportunities_today,
        companies_total=companies_total,
        evaluations_today=sum(by_tier.values()),
        top_tier_today=by_tier.get("TOP_TIER", 0),
        strong_today=by_tier.get("STRONG", 0),
        exploratory_today=by_tier.get("EXPLORATORY", 0),
    )


# --- Source health -----------------------------------------------

class SourceHealth(BaseModel):
    source: str
    total: int
    today: int
    last_seen_at: Optional[str] = None


@router.get("/sources", response_model=list[SourceHealth])
def source_health(
    tracker: Tracker = Depends(get_tracker),
) -> list[SourceHealth]:
    today = _today_iso()
    rows = tracker._query_all(
        "SELECT source, "
        "       COUNT(*) AS total, "
        "       SUM(CASE WHEN date_discovered >= ? THEN 1 ELSE 0 END) AS today, "
        "       MAX(last_seen_at) AS last_seen_at "
        "FROM opportunities "
        "GROUP BY source "
        "ORDER BY total DESC",
        (today,),
    )
    return [
        SourceHealth(
            source=r["source"],
            total=r["total"],
            today=r["today"] or 0,
            last_seen_at=r["last_seen_at"],
        )
        for r in rows
    ]


# --- Evaluator usage ---------------------------------------------

class EvaluatorStats(BaseModel):
    versions: list[dict]
    calls_today: int
    avg_fit_score_today: Optional[float] = None


@router.get("/evaluator", response_model=EvaluatorStats)
def evaluator_stats(
    tracker: Tracker = Depends(get_tracker),
) -> EvaluatorStats:
    today = _today_iso()
    versions = [
        dict(r)
        for r in tracker._query_all(
            "SELECT evaluator_version AS version, "
            "       COUNT(*) AS calls "
            "FROM eval_decisions "
            "GROUP BY evaluator_version "
            "ORDER BY calls DESC",
        )
    ]
    today_row = tracker._query_one(
        "SELECT COUNT(*) AS calls, AVG(fit_score) AS avg_fit "
        "FROM eval_decisions "
        "WHERE evaluated_at >= ?",
        (today,),
    )
    return EvaluatorStats(
        versions=versions,
        calls_today=today_row["calls"] or 0,
        avg_fit_score_today=today_row["avg_fit"],
    )


# --- Application summary -----------------------------------------

class ApplicationStats(BaseModel):
    by_status: dict[str, int]
    submitted_this_week: int
    interviews_active: int
    pending_review: int


@router.get("/applications", response_model=ApplicationStats)
def application_stats(
    tracker: Tracker = Depends(get_tracker),
) -> ApplicationStats:
    week_ago = _seven_days_ago_iso()
    by_status = {
        r["status"]: r["c"]
        for r in tracker._query_all(
            "SELECT status, COUNT(*) AS c FROM applications "
            "GROUP BY status",
        )
    }
    submitted_this_week = tracker._query_one(
        "SELECT COUNT(*) AS c FROM applications "
        "WHERE status = 'submitted' AND status_updated_at >= ?",
        (week_ago,),
    )["c"]
    interviews_active = (
        by_status.get("interviewing", 0)
        + by_status.get("responded", 0)
    )
    pending_review = by_status.get("drafted", 0)
    return ApplicationStats(
        by_status=by_status,
        submitted_this_week=submitted_this_week,
        interviews_active=interviews_active,
        pending_review=pending_review,
    )
