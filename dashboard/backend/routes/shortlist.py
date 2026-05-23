"""GET /api/shortlist + select / skip / unskip + filter options.

Filter axes (all comma-separated; AND-of-ORs across axes):
  ?function=project_manager,scrum_master
  ?industry=banking,energy
  ?city=CityA,CityB        (canonical city values; exact match on
                            v2.11 o.city column)  OR
  ?city=<slug>             (legacy CITY_FILTERS slug -> LIKE patterns;
                            preserves bookmarks from the pre-Spec-2
                            single-select UI)
  ?ai_subtype=ai_engineer,ai_pm
  ?mode=show               (default; matching rows shown)  OR
  ?mode=hide               (matching rows excluded; rest returned)

GET /api/shortlist/filter-options returns distinct non-NULL values
for each axis. Empty axes (function, industry on day 1) return [];
the frontend renders those dropdowns disabled with helper text.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from dashboard.backend.deps import get_tracker
from engine.persistence.tracker import Tracker

router = APIRouter(prefix="/api/shortlist", tags=["shortlist"])


class ShortlistItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    opportunity_id: int
    title: str
    employer: str
    location: Optional[str] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    posted_at: Optional[str] = None
    tier: Optional[str] = None
    fit_score: Optional[int] = None
    reasoning: Optional[str] = None
    sector: Optional[str] = None
    ats_platform: Optional[str] = None
    function: Optional[str] = None
    industry_normalized: Optional[str] = None
    city: Optional[str] = None
    ai_subtype: Optional[str] = None
    # opportunities.status — 'shortlisted' iff the user previously
    # clicked "Select to apply". Surfaced so the frontend can render
    # the Deselect affordance after a page reload (in-memory
    # statusByPosting alone loses this after refresh).
    status: Optional[str] = None
    # v2.20 (Spec JA-1 TASK 2) — human-facing extras off eval_decisions.
    letter_grade: Optional[str] = None
    interview_plan: Optional[list] = None
    red_flags: Optional[list] = None
    culture_signals: Optional[list] = None


# Legacy CITY_FILTERS: when ?city=<single-slug> matches a key here,
# we use LIKE patterns against o.location (preserves pre-Spec-2
# bookmarks). Comma-separated city values OR slugs not in this dict
# fall through to exact-match on the v2.11 o.city column.
CITY_FILTERS: dict[str, list[str]] = {
    "gta": [
        "%Toronto%", "%GTA%", "%Greater Toronto%", "%Mississauga%",
        "%Brampton%", "%Markham%", "%Scarborough%", "%Vaughan%",
        "%Richmond Hill%", "%Oakville%", "%Burlington%", "%Hamilton%",
        "%Oshawa%", "%Pickering%", "%Ajax%", "%Whitby%", "%Ontario%",
    ],
    "calgary": ["%Calgary%"],
    "edmonton": ["%Edmonton%"],
    "remote": ["%Remote%", "%remote%", "%Anywhere%"],
}


# The main shortlist shows only postings still awaiting a decision:
# status 'new' (or NULL). Everything else — 'shortlisted' (selected,
# now in the Applications tab), 'dismissed' (skipped), 'applied' /
# 'interview' / 'offer' (in the apply funnel) — drops off the queue.
_ACTIVE_STATUS_SQL = "(o.status IS NULL OR o.status = 'new')"
# The SELECTED tab inverts that: it shows the postings the user has
# already picked (status='shortlisted').
_SELECTED_STATUS_SQL = "o.status = 'shortlisted'"


# {status_clause} = _ACTIVE_STATUS_SQL or _SELECTED_STATUS_SQL;
# {axis_clause} = optional filter/tier AND-clause (may be empty).
_SHORTLIST_FROM_WHERE = """\
FROM opportunities o
JOIN companies c ON c.id = o.company_id
JOIN eval_decisions e ON e.id = (
    SELECT id FROM eval_decisions e2
    WHERE e2.opportunity_id = o.id
    ORDER BY e2.evaluated_at DESC LIMIT 1
)
WHERE e.tier IN ('TOP_TIER', 'STRONG', 'EXPLORATORY')
  AND {status_clause}{axis_clause}
"""


_BASE_SHORTLIST_SQL = """\
SELECT o.id AS opportunity_id, o.title, c.name AS employer,
       o.location, o.source, o.source_url, o.posted_at,
       e.tier, e.fit_score, e.reasoning, e.sector,
       c.ats_platform,
       o.function, o.industry_normalized, o.city, o.ai_subtype,
       o.status,
       e.letter_grade, e.interview_plan, e.red_flags,
       e.culture_signals
""" + _SHORTLIST_FROM_WHERE + """\
ORDER BY
  CASE e.tier
    WHEN 'TOP_TIER' THEN 0 WHEN 'STRONG' THEN 1 ELSE 2 END,
  e.fit_score DESC,
  o.date_discovered DESC
LIMIT ? OFFSET ?
"""


# Date-range filter: maps a range key to a SQL fragment on
# o.date_discovered. The fragments use SQLite date('now') literals
# (no bound params), so they are safe to interpolate directly.
_DATE_RANGE_SQL = {
    "today": "\n  AND date(o.date_discovered) = date('now')",
    "week": "\n  AND o.date_discovered >= date('now', '-7 days')",
    "month": "\n  AND o.date_discovered >= date('now', '-30 days')",
}


def _date_range_clause(date_range: Optional[str]) -> str:
    """SQL fragment for a date_range key; '' for None/'all'."""
    if not date_range or date_range == "all":
        return ""
    if date_range not in _DATE_RANGE_SQL:
        raise HTTPException(
            status_code=400,
            detail=(
                "date_range must be 'today', 'week', 'month', or "
                f"'all', got {date_range!r}"
            ),
        )
    return _DATE_RANGE_SQL[date_range]


_COUNTS_SQL = (
    "SELECT e.tier AS tier, COUNT(*) AS c\n"
    + _SHORTLIST_FROM_WHERE
    + "GROUP BY e.tier"
)


def _parse_csv(s: Optional[str]) -> Optional[list[str]]:
    """Parse a comma-separated query value to a list of trimmed values.
    None or empty -> None (no filter on this axis)."""
    if not s:
        return None
    parts = [p.strip() for p in s.split(",") if p.strip()]
    return parts or None


# --- Spec 13 TASK 6: CSV export ---------------------------------

# Registered BEFORE the catch-all `""` route so FastAPI's route
# matching doesn't shadow it.

import csv
import io as _io

from fastapi.responses import StreamingResponse


_EXPORT_SQL = """\
SELECT o.id AS opportunity_id,
       c.name AS employer,
       o.title AS title,
       o.location AS location,
       o.source_url AS source_url,
       o.date_discovered AS date_discovered,
       o.status AS status,
       e.tier AS tier,
       e.fit_score AS fit_score,
       ms.overlap_skill_ids AS overlap_skill_ids
FROM opportunities o
JOIN companies c ON c.id = o.company_id
LEFT JOIN eval_decisions e ON e.id = (
    SELECT MAX(id) FROM eval_decisions
    WHERE opportunity_id = o.id
)
LEFT JOIN match_scores ms ON ms.opportunity_id = o.id
WHERE o.status IN ('new', 'shortlisted', 'pursued')
  AND (e.tier IN ('TOP_TIER', 'STRONG') OR ? = 1)
ORDER BY e.fit_score DESC NULLS LAST
"""


def _resolve_skill_labels(tracker, skill_ids: list[str]) -> list[str]:
    if not skill_ids:
        return []
    placeholders = ",".join("?" for _ in skill_ids)
    rows = tracker._query_all(
        f"SELECT skill_id, label FROM skill_labels "
        f"WHERE skill_id IN ({placeholders})",
        tuple(skill_ids),
    )
    by_id = {r["skill_id"]: r["label"] for r in rows}
    return [by_id.get(s, s) for s in skill_ids]


@router.get("/export")
def export_shortlist(
    include_all_tiers: bool = False,
    tracker: Tracker = Depends(get_tracker),
):
    """Stream the shortlist as CSV.

    Default: only TOP_TIER + STRONG rows. include_all_tiers=true
    widens to every active opportunity (useful for snapshotting
    pre-calibration state)."""
    try:
        rows = tracker._query_all(
            _EXPORT_SQL.replace("NULLS LAST", ""),
            (1 if include_all_tiers else 0,),
        )
    except Exception:
        rows = []

    buf = _io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "opportunity_id", "employer", "title", "tier", "fit_score",
        "location", "matched_skills", "posting_url", "date_discovered",
        "status",
    ])
    for r in rows:
        try:
            overlap = json.loads(r["overlap_skill_ids"] or "[]")
        except (json.JSONDecodeError, TypeError):
            overlap = []
        matched_labels = _resolve_skill_labels(tracker, overlap)
        writer.writerow([
            r["opportunity_id"],
            r["employer"] or "",
            r["title"] or "",
            r["tier"] or "",
            r["fit_score"] if r["fit_score"] is not None else "",
            r["location"] or "",
            "; ".join(matched_labels),
            r["source_url"] or "",
            r["date_discovered"] or "",
            r["status"] or "",
        ])

    buf.seek(0)
    headers = {
        "Content-Disposition": (
            'attachment; filename="shortlist.csv"'
        ),
    }
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers=headers,
    )


# --- Filter options endpoint (registered BEFORE the catch-all
# `""` route so FastAPI matches it correctly).

@router.get("/filter-options")
def list_filter_options(
    tracker: Tracker = Depends(get_tracker),
) -> dict:
    """Distinct non-NULL values per axis. Empty axes return []."""
    def distinct(col: str) -> list[str]:
        rows = tracker._query_all(
            f"SELECT DISTINCT {col} FROM opportunities "
            f"WHERE {col} IS NOT NULL ORDER BY {col}"
        )
        return [r[col] for r in rows]

    return {
        "functions": distinct("function"),
        "industries": distinct("industry_normalized"),
        "cities": distinct("city"),
        "ai_subtypes": distinct("ai_subtype"),
    }


def _build_axis(
    city: Optional[str],
    function: Optional[str],
    industry: Optional[str],
    ai_subtype: Optional[str],
    ai_role: Optional[str],
    mode: str,
) -> tuple[str, list]:
    """Build the optional `AND (...)` axis clause + its bound params
    from the filter query values. Shared by the list and counts
    endpoints so both honour the same filters. Returns ('', []) when
    no filter is active."""
    axis_clauses: list[str] = []
    axis_params: list = []

    function_vals = _parse_csv(function)
    if function_vals:
        ph = ",".join("?" * len(function_vals))
        axis_clauses.append(f"o.function IN ({ph})")
        axis_params.extend(function_vals)

    industry_vals = _parse_csv(industry)
    if industry_vals:
        ph = ",".join("?" * len(industry_vals))
        axis_clauses.append(f"o.industry_normalized IN ({ph})")
        axis_params.extend(industry_vals)

    # City: legacy single-slug LIKE path, OR new exact-match list.
    if city:
        if "," not in city and city in CITY_FILTERS:
            patterns = CITY_FILTERS[city]
            like_clause = " OR ".join(
                "o.location LIKE ?" for _ in patterns
            )
            axis_clauses.append(f"({like_clause})")
            axis_params.extend(patterns)
        else:
            city_vals = _parse_csv(city)
            if city_vals:
                ph = ",".join("?" * len(city_vals))
                axis_clauses.append(f"o.city IN ({ph})")
                axis_params.extend(city_vals)

    ai_vals = _parse_csv(ai_subtype)
    if ai_vals:
        ph = ",".join("?" * len(ai_vals))
        axis_clauses.append(f"o.ai_subtype IN ({ph})")
        axis_params.extend(ai_vals)

    # Coarse AI / non-AI filter (Spec 6b TASK 3.5). Distinct from the
    # ai_subtype axis above: ai_role is a binary "was this posting
    # classified into any AI subtype at all?" signal. ai_subtype IS
    # NOT NULL is the canonical definition of "AI role" (733 / 17,370
    # rows in production at TASK 3.5 audit).
    if ai_role == "ai_only":
        axis_clauses.append("o.ai_subtype IS NOT NULL")
    elif ai_role == "non_ai_only":
        axis_clauses.append("o.ai_subtype IS NULL")

    if not axis_clauses:
        return "", []
    combined = " AND ".join(axis_clauses)
    if mode == "hide":
        combined = f"NOT ({combined})"
    return f"\n  AND ({combined})", axis_params


def _validate_filter_params(mode: str, ai_role: Optional[str]) -> None:
    if mode not in ("show", "hide"):
        raise HTTPException(
            status_code=400,
            detail=f"mode must be 'show' or 'hide', got {mode!r}",
        )
    if ai_role is not None and ai_role not in ("ai_only", "non_ai_only"):
        raise HTTPException(
            status_code=400,
            detail=(
                f"ai_role must be 'ai_only' or 'non_ai_only', "
                f"got {ai_role!r}"
            ),
        )


class ShortlistCounts(BaseModel):
    all: int          # unactioned postings, all tiers
    top_tier: int     # unactioned TOP_TIER
    strong: int       # unactioned STRONG
    exploratory: int  # unactioned EXPLORATORY
    selected: int     # postings already picked (status='shortlisted')


def _count_by_tier(
    tracker: Tracker, status_clause: str,
    axis_clause: str, axis_params: list,
) -> dict[str, int]:
    sql = _COUNTS_SQL.format(
        status_clause=status_clause, axis_clause=axis_clause,
    )
    rows = tracker._query_all(sql, tuple(axis_params))
    return {r["tier"]: int(r["c"]) for r in rows}


@router.get("/counts", response_model=ShortlistCounts)
def shortlist_counts(
    city: Optional[str] = None,
    function: Optional[str] = None,
    industry: Optional[str] = None,
    ai_subtype: Optional[str] = None,
    ai_role: Optional[str] = None,
    mode: str = "show",
    date_range: Optional[str] = None,
    tracker: Tracker = Depends(get_tracker),
) -> ShortlistCounts:
    """Tier counts for the current filter set — drives the tab
    labels and the pagination total in the UI. `all`/`top_tier`/
    `strong`/`exploratory` count unactioned postings; `selected`
    counts the postings already picked into the Applications tab.
    `date_range` (today/week/month/all) scopes every count."""
    _validate_filter_params(mode, ai_role)
    axis_clause, axis_params = _build_axis(
        city, function, industry, ai_subtype, ai_role, mode,
    )
    # The date filter scopes the counts so they match the list.
    axis_clause = axis_clause + _date_range_clause(date_range)
    active = _count_by_tier(
        tracker, _ACTIVE_STATUS_SQL, axis_clause, axis_params,
    )
    selected = _count_by_tier(
        tracker, _SELECTED_STATUS_SQL, axis_clause, axis_params,
    )
    top = active.get("TOP_TIER", 0)
    strong = active.get("STRONG", 0)
    exploratory = active.get("EXPLORATORY", 0)
    return ShortlistCounts(
        all=top + strong + exploratory,
        top_tier=top,
        strong=strong,
        exploratory=exploratory,
        selected=sum(selected.values()),
    )


@router.get("", response_model=list[ShortlistItem])
def list_shortlist(
    city: Optional[str] = None,
    function: Optional[str] = None,
    industry: Optional[str] = None,
    ai_subtype: Optional[str] = None,
    ai_role: Optional[str] = None,
    mode: str = "show",
    tier: Optional[str] = None,
    date_range: Optional[str] = None,
    page: int = 1,
    per_page: int = 20,
    tracker: Tracker = Depends(get_tracker),
) -> list[ShortlistItem]:
    _validate_filter_params(mode, ai_role)
    if page < 1:
        raise HTTPException(
            status_code=400, detail=f"page must be >= 1, got {page}",
        )
    if per_page < 1 or per_page > 200:
        raise HTTPException(
            status_code=400,
            detail=f"per_page must be 1-200, got {per_page}",
        )
    # `tier`: absent/'all' = every tier of unactioned postings;
    # a tier name = one tier; 'selected' = the picked pool.
    if tier not in (
        None, "", "all", "TOP_TIER", "STRONG", "EXPLORATORY", "selected",
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"tier must be 'all', 'TOP_TIER', 'STRONG', "
                f"'EXPLORATORY', or 'selected', got {tier!r}"
            ),
        )

    axis_clause, axis_params = _build_axis(
        city, function, industry, ai_subtype, ai_role, mode,
    )
    if tier == "selected":
        status_clause = _SELECTED_STATUS_SQL
    else:
        status_clause = _ACTIVE_STATUS_SQL
        # Tier eq is applied OUTSIDE _build_axis so hide-mode never
        # inverts it — the tab choice is not part of the filters.
        if tier in ("TOP_TIER", "STRONG", "EXPLORATORY"):
            axis_clause = axis_clause + "\n  AND e.tier = ?"
            axis_params = [*axis_params, tier]
    axis_clause = axis_clause + _date_range_clause(date_range)

    sql = _BASE_SHORTLIST_SQL.format(
        status_clause=status_clause, axis_clause=axis_clause,
    )
    params = (*axis_params, per_page, (page - 1) * per_page)
    rows = tracker._query_all(sql, params)
    return [ShortlistItem(**_decode_jsons(dict(r))) for r in rows]


_JSON_LIST_COLUMNS = ("interview_plan", "red_flags", "culture_signals")


def _decode_jsons(row: dict) -> dict:
    """Decode TEXT-stored JSON columns to lists for the response model.

    NULL stays None. Anything that doesn't parse as a list collapses
    to None — the frontend treats that the same as "no extras".
    """
    for col in _JSON_LIST_COLUMNS:
        raw = row.get(col)
        if raw is None:
            continue
        try:
            decoded = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            row[col] = None
            continue
        row[col] = decoded if isinstance(decoded, list) else None
    return row


# --- Mutations (unchanged from current behavior) ---

class SelectResponse(BaseModel):
    application_id: int
    selected_at: str


@router.post(
    "/{posting_id}/select", response_model=SelectResponse,
)
def select_posting(
    posting_id: int,
    tracker: Tracker = Depends(get_tracker),
) -> SelectResponse:
    if tracker.get_opportunity_by_id(posting_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"opportunity {posting_id} not found",
        )
    now = datetime.now(timezone.utc).isoformat()
    existing = tracker._query_one(
        "SELECT id FROM applications WHERE opportunity_id = ? "
        "ORDER BY status_updated_at DESC LIMIT 1",
        (posting_id,),
    )
    if existing is not None:
        app_id = existing["id"]
    else:
        app_id = tracker.create_application(
            opportunity_id=posting_id,
            resume_variant="dashboard_pending",
        )
    tracker.update_application_fields(app_id, selected_at=now)
    tracker.update_opportunity_status(posting_id, "shortlisted")
    return SelectResponse(application_id=app_id, selected_at=now)


class UnselectResponse(BaseModel):
    posting_id: int
    status: str = "new"


@router.post(
    "/{posting_id}/unselect", response_model=UnselectResponse,
)
def unselect_posting(
    posting_id: int,
    tracker: Tracker = Depends(get_tracker),
) -> UnselectResponse:
    if tracker.get_opportunity_by_id(posting_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"opportunity {posting_id} not found",
        )
    # Mirror select_posting in reverse: find the most recent
    # application for this posting (same lookup pattern) and clear
    # its selected_at timestamp. The application row is preserved
    # so history is intact (the events table records the status
    # transition; the application row itself records the
    # selected -> unselected lifecycle via the cleared timestamp).
    # No application = idempotent no-op on the apps table; we still
    # flip status back to 'new'.
    existing = tracker._query_one(
        "SELECT id FROM applications WHERE opportunity_id = ? "
        "ORDER BY status_updated_at DESC LIMIT 1",
        (posting_id,),
    )
    if existing is not None:
        tracker.update_application_fields(
            existing["id"], selected_at=None,
        )
    tracker.update_opportunity_status(posting_id, "new")
    return UnselectResponse(posting_id=posting_id)


class SkipResponse(BaseModel):
    posting_id: int
    status: str = "dismissed"


@router.post(
    "/{posting_id}/skip", response_model=SkipResponse,
)
def skip_posting(
    posting_id: int,
    tracker: Tracker = Depends(get_tracker),
) -> SkipResponse:
    if tracker.get_opportunity_by_id(posting_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"opportunity {posting_id} not found",
        )
    tracker.update_opportunity_status(posting_id, "dismissed")
    return SkipResponse(posting_id=posting_id)


class UnskipResponse(BaseModel):
    posting_id: int
    status: str = "new"


@router.post(
    "/{posting_id}/unskip", response_model=UnskipResponse,
)
def unskip_posting(
    posting_id: int,
    tracker: Tracker = Depends(get_tracker),
) -> UnskipResponse:
    if tracker.get_opportunity_by_id(posting_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"opportunity {posting_id} not found",
        )
    tracker.update_opportunity_status(posting_id, "new")
    return UnskipResponse(posting_id=posting_id)


# --- Spec 13 TASK 3: Why this score? ---------------------------

class ExplainResponse(BaseModel):
    opportunity_id: int
    coverage: float | None
    matched_skills: list[str]
    missed_skills: list[str]
    coverage_pct: float
    bridge_note: str | None = None
    reasoning: str | None = None


_EXPLAIN_SQL = """\
SELECT ms.coverage_raw, ms.coverage_idf,
       ms.overlap_skill_ids, ms.missed_skill_ids,
       ed.reasoning AS eval_reasoning
FROM match_scores ms
LEFT JOIN eval_decisions ed
       ON ed.opportunity_id = ms.opportunity_id
      AND ed.id = (SELECT MAX(id) FROM eval_decisions
                   WHERE opportunity_id = ms.opportunity_id)
WHERE ms.opportunity_id = ?
ORDER BY ms.scored_at DESC
LIMIT 1
"""


def _resolve_labels(tracker, skill_ids: list[str]) -> list[str]:
    if not skill_ids:
        return []
    placeholders = ",".join("?" for _ in skill_ids)
    rows = tracker._query_all(
        f"SELECT skill_id, label FROM skill_labels "
        f"WHERE skill_id IN ({placeholders})",
        tuple(skill_ids),
    )
    by_id = {r["skill_id"]: r["label"] for r in rows}
    return [by_id.get(sid, sid) for sid in skill_ids]


@router.get("/{posting_id}/explain", response_model=ExplainResponse)
def explain_score(
    posting_id: int,
    tracker: Tracker = Depends(get_tracker),
) -> ExplainResponse:
    if tracker.get_opportunity_by_id(posting_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"opportunity {posting_id} not found",
        )
    row = tracker._query_one(_EXPLAIN_SQL, (posting_id,))
    if row is None:
        # Posting hasn't been scored yet — return an empty
        # explanation rather than 404 so the UI can render a
        # "not scored yet" message.
        return ExplainResponse(
            opportunity_id=posting_id,
            coverage=None,
            matched_skills=[],
            missed_skills=[],
            coverage_pct=0.0,
            bridge_note=None,
            reasoning=None,
        )

    try:
        matched_ids = json.loads(row["overlap_skill_ids"] or "[]")
    except (json.JSONDecodeError, TypeError):
        matched_ids = []
    try:
        missed_ids = json.loads(row["missed_skill_ids"] or "[]")
    except (json.JSONDecodeError, TypeError):
        missed_ids = []

    matched_labels = _resolve_labels(tracker, matched_ids)
    missed_labels = _resolve_labels(tracker, missed_ids)
    posting_total = len(matched_ids) + len(missed_ids)
    coverage_pct = (
        100.0 * len(matched_ids) / posting_total if posting_total else 0.0
    )
    return ExplainResponse(
        opportunity_id=posting_id,
        coverage=row["coverage_idf"] if row["coverage_idf"] is not None
                  else row["coverage_raw"],
        matched_skills=matched_labels,
        missed_skills=missed_labels,
        coverage_pct=round(coverage_pct, 1),
        bridge_note=None,
        reasoning=row["eval_reasoning"],
    )
