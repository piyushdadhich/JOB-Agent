"""Pipeline overview endpoint for the Dashboard page.

  GET /api/pipeline?range=today    (default)
  GET /api/pipeline?range=week
  GET /api/pipeline?range=month

Returns funnel counts for every stage (Discovered → Evaluated →
Shortlisted → Selected → Docs Ready → Applied → Interview → Offer)
plus deltas vs the previous period, daily breakdown for trend
charts, per-source counts, and evaluator status.

The endpoint is read-only — all queries hit the existing v2.9
schema. No mutation, no caching: the SQLite tracker is small enough
that a fresh aggregation per request is well under 50 ms.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from dashboard.backend.deps import get_tracker
from engine.persistence.tracker import Tracker
from engine.utils.day_boundary import (
    agent_day_range_utc_iso,
    calendar_month_start,
    calendar_week_start,
    current_agent_day,
    entry_agent_day,
)

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])

# Real-world Gemma 4 31B free-tier latency (per the cloud_eval_latency
# operations memo). The schema does not record per-call latency, so we
# surface the canonical figure rather than fabricate one.
CLOUD_EVAL_AVG_LATENCY_MS = 24_100
# Google AI Studio free-tier daily request limit. The 1,200 figure used
# elsewhere is GemmaCloudClient.SOFT_STOP — a safety margin, not the
# ceiling. The dashboard shows usage against the real ceiling so the user
# sees how much of the free tier they've actually consumed.
CLOUD_EVAL_DAILY_BUDGET = 1_500

ApplyStatuses = (
    "submitted",
    "confirmed_received",
    "responded",
    "interviewing",
    "offered",
)
InterviewStatuses = ("interviewing",)
OfferStatuses = ("offered",)


# --- Pydantic response models ------------------------------------

class PipelinePeriod(BaseModel):
    start: str  # YYYY-MM-DD
    end: str    # YYYY-MM-DD


class PipelineStage(BaseModel):
    name: str
    count: int
    previous: int
    delta: int
    delta_pct: Optional[float]  # null when previous == 0


class PipelineDailyRow(BaseModel):
    date: str
    discovered: int
    evaluated: int
    shortlisted: int
    applied: int


class PipelineSource(BaseModel):
    name: str
    count: int
    last_seen: Optional[str] = None


class PipelineEvaluatorTiers(BaseModel):
    TOP_TIER: int = 0
    STRONG: int = 0
    EXPLORATORY: int = 0
    SKIP: int = 0


class PipelineEvaluatorTokens(BaseModel):
    """Cloud Gemma 4 31B token usage aggregated over calendar windows.

    Local Ollama (Gemma 3 4B + Gemma 4 E4B) has no per-call token
    logging — see dead_code_report.md "Local Ollama per-call token
    logging gap" — so this surfaces cloud-only data. Spec 6b TASK 6
    PATH 6A-cloud-only.
    """
    today_input: int
    today_output: int
    this_week_input: int
    this_week_output: int
    this_month_input: int
    this_month_output: int


class PipelineEvaluator(BaseModel):
    model: Optional[str]
    calls_today: int
    calls_this_week: int
    calls_this_month: int
    budget: int
    avg_latency_ms: int
    retries_pending: int
    tiers: PipelineEvaluatorTiers
    tokens: PipelineEvaluatorTokens


class PipelineResponse(BaseModel):
    range: str
    period: PipelinePeriod
    previous_period: PipelinePeriod
    stages: list[PipelineStage]
    daily_breakdown: list[PipelineDailyRow]
    sources: list[PipelineSource]
    evaluator: PipelineEvaluator


# --- Helpers -----------------------------------------------------

def _today() -> date:
    """Current agent-day (default: 3 AM Toronto = midnight Pacific
    = Google free-tier quota reset). See engine.utils.day_boundary.
    """
    return current_agent_day()


def _resolve_range(range_key: str) -> tuple[date, date, date, date]:
    """Return (period_start, period_end, prev_start, prev_end) for
    a rolling window ending on the current agent-day. Mirrors
    engine.utils.day_boundary.agent_day_range but anchors on the
    module-level current_agent_day reference so tests can monkeypatch
    a single seam. Periods:
      today: 1 day; week: 7 days; month: 30 days.
    """
    if range_key == "today":
        days = 1
    elif range_key == "week":
        days = 7
    elif range_key == "month":
        days = 30
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid range {range_key!r}; expected today|week|month",
        )
    period_end = current_agent_day()
    period_start = period_end - timedelta(days=days - 1)
    prev_end = period_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    return period_start, period_end, prev_start, prev_end


def _iso(d: date) -> str:
    return d.isoformat()


def _delta_pct(current: int, previous: int) -> Optional[float]:
    if previous == 0:
        return None
    return round(((current - previous) / previous) * 100.0, 1)


def _count_in_range(
    tracker: Tracker, sql: str, start: date, end: date,
    extra_params: tuple = (),
) -> int:
    """Run a stage-total COUNT query bound to an agent-day range.

    Converts the date-only [start, end] inclusive range into a UTC
    ISO half-open [start_utc, end_utc) range so the SQL >= / <
    comparison precisely matches the agent-day window, including
    timestamps that fall in the 03:00-Toronto-boundary window
    where UTC date and agent-day diverge.
    """
    start_utc, end_utc = agent_day_range_utc_iso(start, end)
    row = tracker._query_one(
        sql, (start_utc, end_utc, *extra_params),
    )
    return int(row["c"]) if row and row["c"] is not None else 0


# Stage-total queries use a UTC half-open range comparison
# (col >= ? AND col < ?) so the agent-day boundary at 3 AM Toronto
# (= 7/8 AM UTC depending on DST) is honored precisely. Daily
# breakdown queries (further down) keep substr(col, 1, 10) +
# UTC-date grouping -- the off-by-3-hours skew at the boundary is
# acceptable for the chart's visual accuracy.

_SQL_DISCOVERED = (
    "SELECT COUNT(*) AS c FROM opportunities "
    "WHERE date_discovered >= ? AND date_discovered < ?"
)
# Funnel stages must flow left-to-right: Evaluated ≤ Discovered. Counting
# eval_decisions by evaluated_at would include re-evaluations of postings
# discovered before the period, breaking that invariant. Anchor on the
# opportunity's date_discovered instead.
_SQL_EVALUATED = (
    "SELECT COUNT(DISTINCT ed.opportunity_id) AS c FROM eval_decisions ed "
    "JOIN opportunities o ON o.id = ed.opportunity_id "
    "WHERE o.date_discovered >= ? AND o.date_discovered < ? "
    "AND ed.evaluated_at IS NOT NULL"
)
# Shortlisted = the pool of evaluated postings still awaiting a
# decision (latest eval is TOP_TIER / STRONG / EXPLORATORY, status
# new/NULL) — the same set the Shortlist page's "All" tab counts.
# NOT the same as Selected (status='shortlisted'); that is a
# distinct, later stage. The latest-eval subquery mirrors the
# Shortlist route so the two numbers always agree.
_SQL_SHORTLISTED_TOTAL = (
    "SELECT COUNT(*) AS c FROM opportunities o "
    "JOIN eval_decisions e ON e.id = ("
    "    SELECT id FROM eval_decisions e2 "
    "    WHERE e2.opportunity_id = o.id "
    "    ORDER BY e2.evaluated_at DESC LIMIT 1) "
    "WHERE e.tier IN ('TOP_TIER', 'STRONG', 'EXPLORATORY') "
    "AND (o.status IS NULL OR o.status = 'new')"
)
_SQL_SELECTED = (
    "SELECT COUNT(*) AS c FROM applications "
    "WHERE selected_at IS NOT NULL "
    "AND selected_at >= ? AND selected_at < ?"
)
_SQL_DOCS_READY_TOTAL = (
    "SELECT COUNT(*) AS c FROM applications "
    "WHERE docs_ready_at IS NOT NULL"
)


def _applied_sql(statuses: Iterable[str]) -> str:
    placeholders = ",".join("?" * len(tuple(statuses)))
    return (
        "SELECT COUNT(*) AS c FROM applications "
        f"WHERE status IN ({placeholders}) "
        "AND status_updated_at >= ? AND status_updated_at < ?"
    )


def _count_applied(
    tracker: Tracker, statuses: tuple[str, ...],
    start: date, end: date,
) -> int:
    placeholders = ",".join("?" * len(statuses))
    sql = (
        "SELECT COUNT(*) AS c FROM applications "
        f"WHERE status IN ({placeholders}) "
        "AND status_updated_at >= ? AND status_updated_at < ?"
    )
    start_utc, end_utc = agent_day_range_utc_iso(start, end)
    row = tracker._query_one(sql, (*statuses, start_utc, end_utc))
    return int(row["c"]) if row and row["c"] is not None else 0


def _stage(
    name: str, current: int, previous: int,
) -> PipelineStage:
    return PipelineStage(
        name=name,
        count=current,
        previous=previous,
        delta=current - previous,
        delta_pct=_delta_pct(current, previous),
    )


def _daily_grouped(
    tracker: Tracker, sql_template: str,
    start: date, end: date, extra_params: tuple = (),
) -> dict[str, int]:
    """Run a `GROUP BY substr(col, 1, 10) AS day` query and return
    {YYYY-MM-DD: count}."""
    rows = tracker._query_all(
        sql_template, (_iso(start), _iso(end), *extra_params),
    )
    return {r["day"]: int(r["c"]) for r in rows}


_SQL_DAILY_DISCOVERED = (
    "SELECT substr(date_discovered, 1, 10) AS day, COUNT(*) AS c "
    "FROM opportunities "
    "WHERE substr(date_discovered, 1, 10) BETWEEN ? AND ? "
    "GROUP BY day"
)
_SQL_DAILY_EVALUATED = (
    "SELECT substr(o.date_discovered, 1, 10) AS day, "
    "       COUNT(DISTINCT ed.opportunity_id) AS c "
    "FROM eval_decisions ed "
    "JOIN opportunities o ON o.id = ed.opportunity_id "
    "WHERE substr(o.date_discovered, 1, 10) BETWEEN ? AND ? "
    "AND ed.evaluated_at IS NOT NULL "
    "GROUP BY day"
)
_SQL_DAILY_SHORTLISTED = (
    "SELECT substr(selected_at, 1, 10) AS day, COUNT(*) AS c "
    "FROM applications "
    "WHERE selected_at IS NOT NULL "
    "AND substr(selected_at, 1, 10) BETWEEN ? AND ? "
    "GROUP BY day"
)


def _daily_applied(
    tracker: Tracker, statuses: tuple[str, ...],
    start: date, end: date,
) -> dict[str, int]:
    placeholders = ",".join("?" * len(statuses))
    sql = (
        "SELECT substr(status_updated_at, 1, 10) AS day, COUNT(*) AS c "
        "FROM applications "
        f"WHERE status IN ({placeholders}) "
        "AND substr(status_updated_at, 1, 10) BETWEEN ? AND ? "
        "GROUP BY day"
    )
    rows = tracker._query_all(sql, (*statuses, _iso(start), _iso(end)))
    return {r["day"]: int(r["c"]) for r in rows}


def _build_daily_breakdown(
    tracker: Tracker, start: date, end: date,
) -> list[PipelineDailyRow]:
    discovered = _daily_grouped(tracker, _SQL_DAILY_DISCOVERED, start, end)
    evaluated = _daily_grouped(tracker, _SQL_DAILY_EVALUATED, start, end)
    shortlisted = _daily_grouped(tracker, _SQL_DAILY_SHORTLISTED, start, end)
    applied = _daily_applied(tracker, ApplyStatuses, start, end)

    out: list[PipelineDailyRow] = []
    cursor = start
    while cursor <= end:
        key = _iso(cursor)
        out.append(
            PipelineDailyRow(
                date=key,
                discovered=discovered.get(key, 0),
                evaluated=evaluated.get(key, 0),
                shortlisted=shortlisted.get(key, 0),
                applied=applied.get(key, 0),
            )
        )
        cursor += timedelta(days=1)
    return out


def _build_sources(
    tracker: Tracker, start: date, end: date,
) -> list[PipelineSource]:
    start_utc, end_utc = agent_day_range_utc_iso(start, end)
    rows = tracker._query_all(
        "SELECT source AS name, COUNT(*) AS c, "
        "       MAX(last_seen_at) AS last_seen "
        "FROM opportunities "
        "WHERE date_discovered >= ? AND date_discovered < ? "
        "GROUP BY source ORDER BY c DESC",
        (start_utc, end_utc),
    )
    return [
        PipelineSource(
            name=r["name"], count=int(r["c"]),
            last_seen=r["last_seen"],
        )
        for r in rows
    ]


def _usage_log_path(tracker: Tracker) -> Path:
    """Cloud usage log is a sibling of the tracker DB by convention:
    data/{profile}/cloud_eval_usage.jsonl."""
    return Path(tracker.db_path).parent / "cloud_eval_usage.jsonl"


def _count_cloud_calls_in_range(
    log_path: Path, start: date, end: date,
) -> int:
    """Count cloud_eval_usage.jsonl entries whose AGENT-DAY falls in
    [start, end]. Bins each timestamp via entry_agent_day so the
    dashboard's "calls today" panel agrees with
    GemmaCloudClient.count_today() exactly.
    """
    if not log_path.exists():
        return 0
    count = 0
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = entry.get("timestamp", "")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            day = entry_agent_day(dt)
            if start <= day <= end:
                count += 1
    return count


def _sum_cloud_tokens_in_range(
    log_path: Path, start: date, end: date,
) -> tuple[int, int]:
    """Return (input_tokens_total, output_tokens_total) summed across
    cloud_eval_usage.jsonl entries whose agent-day falls in
    [start, end]. Same iteration pattern as
    _count_cloud_calls_in_range; missing/invalid token fields default
    to 0. Spec 6b TASK 6 (PATH 6A-cloud-only)."""
    if not log_path.exists():
        return (0, 0)
    in_total = 0
    out_total = 0
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = entry.get("timestamp", "")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            day = entry_agent_day(dt)
            if start <= day <= end:
                in_total += int(entry.get("input_tokens", 0) or 0)
                out_total += int(entry.get("output_tokens", 0) or 0)
    return (in_total, out_total)


def _latest_cloud_model(log_path: Path) -> Optional[str]:
    """Most recent `model` field from the cloud usage log, or None if
    the log is empty/missing."""
    if not log_path.exists():
        return None
    last: Optional[str] = None
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            m = entry.get("model")
            if m:
                last = m
    return last


def _build_evaluator(
    tracker: Tracker, start: date, end: date,
) -> PipelineEvaluator:
    log_path = _usage_log_path(tracker)

    # Cloud counts always cover today + this calendar week
    # (Monday-onwards) + this calendar month (1st-onwards), independent
    # of the page-level range selector. The selector drives the funnel,
    # source health, and tier breakdown; the cloud counter card surfaces
    # absolute quota usage relevant regardless of view. Calendar windows
    # (not rolling 7d/30d) match how humans think about "this week" and
    # "this month" and how cloud providers reset quotas.
    today = current_agent_day()
    week_start = calendar_week_start(today)
    month_start = calendar_month_start(today)
    calls_today = _count_cloud_calls_in_range(log_path, today, today)
    calls_this_week = _count_cloud_calls_in_range(
        log_path, week_start, today
    )
    calls_this_month = _count_cloud_calls_in_range(
        log_path, month_start, today
    )
    # Token totals (Spec 6b TASK 6 PATH 6A-cloud-only). Same calendar
    # boundaries as the call counts above; locals deferred until
    # call_manager.py adds per-call token logging.
    in_today, out_today = _sum_cloud_tokens_in_range(
        log_path, today, today
    )
    in_week, out_week = _sum_cloud_tokens_in_range(
        log_path, week_start, today
    )
    in_month, out_month = _sum_cloud_tokens_in_range(
        log_path, month_start, today
    )

    # Tier breakdown: only rows from the cloud evaluator (the latest
    # evaluator_version observed in the date range). Without this filter,
    # local-evaluator rows (Gemma 3 4B, E4B) would inflate the tiers.
    start_utc, end_utc = agent_day_range_utc_iso(start, end)
    latest_in_range = tracker._query_one(
        "SELECT evaluator_version FROM eval_decisions "
        "WHERE evaluated_at >= ? AND evaluated_at < ? "
        "ORDER BY evaluated_at DESC LIMIT 1",
        (start_utc, end_utc),
    )
    if latest_in_range and latest_in_range["evaluator_version"]:
        rows = tracker._query_all(
            "SELECT tier, COUNT(*) AS c FROM eval_decisions "
            "WHERE evaluator_version = ? "
            "AND evaluated_at >= ? AND evaluated_at < ? "
            "GROUP BY tier",
            (
                latest_in_range["evaluator_version"],
                start_utc, end_utc,
            ),
        )
    else:
        rows = []
    by_tier = {r["tier"]: int(r["c"]) for r in rows}

    model = _latest_cloud_model(log_path)
    if model is None:
        model_row = tracker._query_one(
            "SELECT evaluator_version FROM eval_decisions "
            "ORDER BY evaluated_at DESC LIMIT 1",
        )
        model = model_row["evaluator_version"] if model_row else None

    return PipelineEvaluator(
        model=model,
        calls_today=calls_today,
        calls_this_week=calls_this_week,
        calls_this_month=calls_this_month,
        tokens=PipelineEvaluatorTokens(
            today_input=in_today,
            today_output=out_today,
            this_week_input=in_week,
            this_week_output=out_week,
            this_month_input=in_month,
            this_month_output=out_month,
        ),
        budget=CLOUD_EVAL_DAILY_BUDGET,
        avg_latency_ms=CLOUD_EVAL_AVG_LATENCY_MS,
        retries_pending=0,  # not tracked in v2.9 schema
        tiers=PipelineEvaluatorTiers(
            TOP_TIER=by_tier.get("TOP_TIER", 0),
            STRONG=by_tier.get("STRONG", 0),
            EXPLORATORY=by_tier.get("EXPLORATORY", 0),
            SKIP=by_tier.get("SKIP", 0),
        ),
    )


# --- Endpoint ----------------------------------------------------

@router.get("", response_model=PipelineResponse)
def get_pipeline(
    range: Literal["today", "week", "month"] = Query("today"),
    tracker: Tracker = Depends(get_tracker),
) -> PipelineResponse:
    period_start, period_end, prev_start, prev_end = _resolve_range(range)

    def stage_pair(name: str, sql: str, *, extra: tuple = ()) -> PipelineStage:
        return _stage(
            name,
            _count_in_range(tracker, sql, period_start, period_end, extra),
            _count_in_range(tracker, sql, prev_start, prev_end, extra),
        )

    discovered = stage_pair("Discovered", _SQL_DISCOVERED)
    evaluated = stage_pair("Evaluated", _SQL_EVALUATED)
    # Shortlisted is a CURRENT-STATE count (the active pool awaiting a
    # decision) rather than a range-bounded event, because the user
    # wants the dashboard to surface the size of the active shortlist
    # pool — not "how many entered the shortlist this week".
    # Previous = current so the delta badge reads 0 (no period delta).
    shortlisted_row = tracker._query_one(_SQL_SHORTLISTED_TOTAL)
    shortlisted_count = (
        int(shortlisted_row["c"])
        if shortlisted_row and shortlisted_row["c"] is not None else 0
    )
    shortlisted = _stage(
        "Shortlisted", shortlisted_count, shortlisted_count,
    )
    selected = stage_pair("Selected", _SQL_SELECTED)
    # Docs Ready is also a current-state pool count (applications.
    # docs_ready_at IS NOT NULL): "how many postings have resume +
    # cover letter saved and are ready for Apply", not "how many
    # crossed the docs_ready threshold this week".
    docs_ready_row = tracker._query_one(_SQL_DOCS_READY_TOTAL)
    docs_ready_count = (
        int(docs_ready_row["c"])
        if docs_ready_row and docs_ready_row["c"] is not None else 0
    )
    docs_ready = _stage(
        "Docs Ready", docs_ready_count, docs_ready_count,
    )

    applied = _stage(
        "Applied",
        _count_applied(tracker, ApplyStatuses, period_start, period_end),
        _count_applied(tracker, ApplyStatuses, prev_start, prev_end),
    )
    interview = _stage(
        "Interview",
        _count_applied(tracker, InterviewStatuses, period_start, period_end),
        _count_applied(tracker, InterviewStatuses, prev_start, prev_end),
    )
    offer = _stage(
        "Offer",
        _count_applied(tracker, OfferStatuses, period_start, period_end),
        _count_applied(tracker, OfferStatuses, prev_start, prev_end),
    )

    return PipelineResponse(
        range=range,
        period=PipelinePeriod(start=_iso(period_start), end=_iso(period_end)),
        previous_period=PipelinePeriod(
            start=_iso(prev_start), end=_iso(prev_end),
        ),
        stages=[
            discovered, evaluated, shortlisted, selected,
            docs_ready, applied, interview, offer,
        ],
        daily_breakdown=_build_daily_breakdown(
            tracker, period_start, period_end,
        ),
        sources=_build_sources(tracker, period_start, period_end),
        evaluator=_build_evaluator(tracker, period_start, period_end),
    )


# --- Spec 13 TASK 1: schedule + run-now -----------------------

class ScheduleResponse(BaseModel):
    last_run: dict | None
    next_run: str
    schedule_time: str


class RunNowResponse(BaseModel):
    started: bool
    return_code: int | None = None
    detail: str | None = None


@router.get("/schedule", response_model=ScheduleResponse)
def get_schedule() -> ScheduleResponse:
    from jobagent.scheduler import schedule_snapshot
    snap = schedule_snapshot()
    return ScheduleResponse(**snap)


@router.post("/run-now", response_model=RunNowResponse)
def post_run_now() -> RunNowResponse:
    """Trigger a synchronous pipeline run. Returns when finished.

    For long-running pipelines (>30s) the dashboard should poll
    /api/pipeline/schedule instead — this endpoint blocks. The
    onboarding wizard's first-run path uses the dedicated
    /api/setup/first-run threaded endpoint instead.
    """
    from jobagent.scheduler import run as scheduler_run

    try:
        record = scheduler_run()
    except Exception as e:
        return RunNowResponse(
            started=False, return_code=None,
            detail=f"{type(e).__name__}: {e}",
        )
    return RunNowResponse(
        started=True,
        return_code=record.return_code,
        detail=None,
    )
