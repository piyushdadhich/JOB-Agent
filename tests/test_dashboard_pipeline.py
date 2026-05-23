"""Tests for /api/pipeline aggregation endpoint."""
from __future__ import annotations

import json
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from dashboard.backend.app import create_app  # noqa: E402
from dashboard.backend.deps import get_tracker  # noqa: E402
from engine.persistence.tracker import Tracker  # noqa: E402
from engine.utils.day_boundary import (  # noqa: E402
    TORONTO, current_agent_day,
)


@pytest.fixture
def harness(tmp_path):
    db = tmp_path / "t.db"
    tracker = Tracker(profile_id="p", db_path=db)
    app = create_app()

    def _override():
        yield tracker

    app.dependency_overrides[get_tracker] = _override
    yield TestClient(app), tracker
    tracker.close()


# --- Helpers ----------------------------------------------------

def _at(days_ago: int = 0) -> str:
    """ISO timestamp that lands inside the (current agent-day -
    `days_ago`) window the pipeline endpoint computes.

    The endpoint anchors its "today" on `current_agent_day()`
    (Toronto with a 3 AM boundary), not on `date.today()` (host
    local). In the post-midnight window before 3 AM Toronto those
    two diverge by a calendar day, which used to push seeded
    timestamps into the wrong stage-count bucket and break this
    suite intermittently overnight.

    We anchor each seeded timestamp at NOON TORONTO on the target
    agent-day. That has two properties the endpoint queries
    rely on:

      1. Stage counts use a UTC half-open [start, end) range
         derived from agent_day_range_utc_iso(); noon Toronto
         is well inside that 24-hour window on either side of
         the DST boundary.
      2. Daily breakdowns group by substr(col, 1, 10) (UTC
         date prefix); noon Toronto converts to 16:00-17:00 UTC
         depending on DST, so the date prefix matches the
         target agent-day's local calendar date.
    """
    target = current_agent_day() - timedelta(days=days_ago)
    return datetime.combine(
        target, time(12, 0), tzinfo=TORONTO,
    ).astimezone(timezone.utc).isoformat()


def _seed_opp(t, employer="TD", source="workday", days_ago=0) -> int:
    cid = t.upsert_company(employer)
    # Override date_discovered after insert because insert_opportunity stamps "now".
    oid, _ = t.insert_opportunity(
        company_id=cid, source=source,
        source_url=f"https://x/{employer}-{days_ago}", title="PM",
        location="Toronto", posting_text="...",
    )
    t._execute(
        "UPDATE opportunities SET date_discovered = ?, last_seen_at = ? "
        "WHERE id = ?",
        (_at(days_ago), _at(days_ago), oid),
    )
    return oid


def _seed_eval(t, opp_id, *, tier="STRONG", fit=7, version="gemma-4-31b",
               days_ago=0) -> None:
    t._execute(
        "INSERT INTO eval_decisions "
        "(opportunity_id, evaluator_version, tier, fit_score, "
        " stage_trace, reasoning, evaluated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (opp_id, version, tier, fit, "[]", "x", _at(days_ago)),
    )


def _seed_cloud_call(t, *, days_ago=0, model="gemma-4-31b-it",
                     status="ok") -> None:
    """Append a real-API-call entry to the profile's cloud_eval_usage.jsonl,
    co-located with the tracker DB."""
    log_path = Path(t.db_path).parent / "cloud_eval_usage.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": _at(days_ago),
        "model": model,
        "status": status,
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _seed_application(t, opp_id, *, status="drafted",
                      selected_days_ago=None, docs_days_ago=None,
                      status_days_ago=0) -> int:
    aid = t.create_application(opportunity_id=opp_id, resume_variant="r")
    if status != "drafted":
        t.update_application_status(aid, status)
    if selected_days_ago is not None:
        t._execute(
            "UPDATE applications SET selected_at = ? WHERE id = ?",
            (_at(selected_days_ago), aid),
        )
    if docs_days_ago is not None:
        t._execute(
            "UPDATE applications SET docs_ready_at = ? WHERE id = ?",
            (_at(docs_days_ago), aid),
        )
    if status != "drafted" or status_days_ago != 0:
        t._execute(
            "UPDATE applications SET status_updated_at = ? WHERE id = ?",
            (_at(status_days_ago), aid),
        )
    return aid


# --- Tests ------------------------------------------------------

def test_pipeline_today_returns_all_stages(harness):
    client, t = harness
    o1 = _seed_opp(t, "TD")
    o2 = _seed_opp(t, "BMO")
    _seed_eval(t, o1, tier="TOP_TIER", fit=9)
    _seed_eval(t, o2, tier="STRONG", fit=7)
    _seed_application(t, o1, status="submitted",
                      selected_days_ago=0, docs_days_ago=0,
                      status_days_ago=0)

    body = client.get("/api/pipeline?range=today").json()
    by_name = {s["name"]: s for s in body["stages"]}
    assert {"Discovered", "Evaluated", "Shortlisted", "Selected",
            "Docs Ready", "Applied", "Interview", "Offer"} == set(by_name)
    assert by_name["Discovered"]["count"] == 2
    assert by_name["Evaluated"]["count"] == 2
    assert by_name["Shortlisted"]["count"] == 2  # both TOP_TIER + STRONG
    assert by_name["Selected"]["count"] == 1
    assert by_name["Docs Ready"]["count"] == 1
    assert by_name["Applied"]["count"] == 1
    assert by_name["Offer"]["count"] == 0


def test_pipeline_default_range_is_today(harness):
    client, _ = harness
    body = client.get("/api/pipeline").json()
    assert body["range"] == "today"
    assert body["period"]["start"] == body["period"]["end"]


def test_pipeline_week_aggregates_seven_days(harness):
    client, t = harness
    # Seed opportunities across the past 6 days (still in window) and
    # one 10 days ago (out of window).
    for d in (0, 2, 4, 6):
        _seed_opp(t, f"E{d}", days_ago=d)
    _seed_opp(t, "OutOfWindow", days_ago=10)

    body = client.get("/api/pipeline?range=week").json()
    discovered = next(
        s for s in body["stages"] if s["name"] == "Discovered"
    )
    assert discovered["count"] == 4
    assert body["period"]["start"] != body["period"]["end"]
    # 7-day window: end-start spans 6 days
    start = datetime.fromisoformat(body["period"]["start"])
    end = datetime.fromisoformat(body["period"]["end"])
    assert (end - start).days == 6


def test_pipeline_month_aggregates_thirty_days(harness):
    client, t = harness
    _seed_opp(t, "Recent", days_ago=2)
    _seed_opp(t, "Edge", days_ago=29)
    _seed_opp(t, "OutOfWindow", days_ago=45)

    body = client.get("/api/pipeline?range=month").json()
    discovered = next(
        s for s in body["stages"] if s["name"] == "Discovered"
    )
    assert discovered["count"] == 2
    start = datetime.fromisoformat(body["period"]["start"])
    end = datetime.fromisoformat(body["period"]["end"])
    assert (end - start).days == 29


def test_pipeline_delta_compares_previous_period(harness):
    client, t = harness
    # Current week: 3 opps. Previous week: 1 opp.
    for d in (0, 1, 2):
        _seed_opp(t, f"Now{d}", days_ago=d)
    _seed_opp(t, "Then", days_ago=8)

    body = client.get("/api/pipeline?range=week").json()
    discovered = next(
        s for s in body["stages"] if s["name"] == "Discovered"
    )
    assert discovered["count"] == 3
    assert discovered["previous"] == 1
    assert discovered["delta"] == 2
    assert discovered["delta_pct"] == 200.0


def test_pipeline_delta_pct_null_when_previous_zero(harness):
    client, t = harness
    _seed_opp(t, "Solo", days_ago=0)
    body = client.get("/api/pipeline?range=today").json()
    discovered = next(
        s for s in body["stages"] if s["name"] == "Discovered"
    )
    assert discovered["count"] == 1
    assert discovered["previous"] == 0
    assert discovered["delta_pct"] is None


def test_pipeline_daily_breakdown_has_one_row_per_day(harness):
    client, t = harness
    for d in (0, 3, 6):
        _seed_opp(t, f"E{d}", days_ago=d)

    body = client.get("/api/pipeline?range=week").json()
    daily = body["daily_breakdown"]
    assert len(daily) == 7
    assert daily[0]["date"] < daily[-1]["date"]
    total = sum(row["discovered"] for row in daily)
    assert total == 3


def test_pipeline_sources_returns_per_source_counts(harness):
    client, t = harness
    _seed_opp(t, "A", source="workday", days_ago=0)
    _seed_opp(t, "B", source="workday", days_ago=1)
    _seed_opp(t, "C", source="lever_api", days_ago=0)
    _seed_opp(t, "Out", source="jobspy", days_ago=10)

    body = client.get("/api/pipeline?range=week").json()
    by_name = {s["name"]: s for s in body["sources"]}
    assert by_name["workday"]["count"] == 2
    assert by_name["lever_api"]["count"] == 1
    assert "jobspy" not in by_name  # out of window


def test_pipeline_evaluator_returns_tier_breakdown(harness):
    client, t = harness
    o1 = _seed_opp(t, "A")
    o2 = _seed_opp(t, "B")
    o3 = _seed_opp(t, "C")
    o4 = _seed_opp(t, "D")
    _seed_eval(t, o1, tier="TOP_TIER", version="gemma-4-31b-it")
    _seed_eval(t, o2, tier="STRONG", version="gemma-4-31b-it")
    _seed_eval(t, o3, tier="EXPLORATORY", version="gemma-4-31b-it")
    _seed_eval(t, o4, tier="SKIP", version="gemma-4-31b-it")
    for _ in range(4):
        _seed_cloud_call(t, model="gemma-4-31b-it")

    body = client.get("/api/pipeline?range=today").json()
    ev = body["evaluator"]
    assert ev["tiers"]["TOP_TIER"] == 1
    assert ev["tiers"]["STRONG"] == 1
    assert ev["tiers"]["EXPLORATORY"] == 1
    assert ev["tiers"]["SKIP"] == 1
    assert ev["calls_today"] == 4
    assert ev["model"] == "gemma-4-31b-it"
    assert ev["budget"] == 1500  # free-tier RPD ceiling, not the soft stop
    assert ev["avg_latency_ms"] > 0


def test_pipeline_evaluator_calls_excludes_local_evals(harness):
    # Regression: eval_decisions includes local-evaluator rows (Gemma 3 4B,
    # E4B). The cloud evaluator card must count real API calls only — i.e.
    # entries in cloud_eval_usage.jsonl — not eval_decisions.
    client, t = harness
    # 5 local evals (no cloud-API call): should NOT count toward calls_today.
    for i in range(5):
        opp = _seed_opp(t, f"L{i}")
        _seed_eval(t, opp, tier="EXPLORATORY", version="gemma-3-4b-local")
    # 2 real cloud calls: only these should count.
    _seed_cloud_call(t, model="gemma-4-31b-it")
    _seed_cloud_call(t, model="gemma-4-31b-it")
    cloud_opp = _seed_opp(t, "C")
    _seed_eval(t, cloud_opp, tier="TOP_TIER", version="gemma-4-31b-it")
    _seed_cloud_call(t, model="gemma-4-31b-it")

    ev = client.get("/api/pipeline?range=today").json()["evaluator"]
    assert ev["calls_today"] == 3  # JSONL count, not 5+1=6 or 1
    assert ev["model"] == "gemma-4-31b-it"


def test_pipeline_evaluator_tiers_filter_to_latest_version(harness):
    # Tier breakdown should reflect only the latest (cloud) evaluator,
    # not legacy local-evaluator rows.
    client, t = harness
    o_local = _seed_opp(t, "L")
    o_cloud_a = _seed_opp(t, "CA")
    o_cloud_b = _seed_opp(t, "CB")
    # Older local-evaluator row would otherwise inflate SKIP.
    _seed_eval(t, o_local, tier="SKIP", version="gemma-3-4b-local",
               days_ago=0)
    # Cloud rows logged later (latest by evaluated_at).
    t._execute(
        "INSERT INTO eval_decisions "
        "(opportunity_id, evaluator_version, tier, fit_score, "
        " stage_trace, reasoning, evaluated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (o_cloud_a, "gemma-4-31b-it", "TOP_TIER", 9, "[]", "x",
         _at(0).replace("T12:", "T18:")),
    )
    t._execute(
        "INSERT INTO eval_decisions "
        "(opportunity_id, evaluator_version, tier, fit_score, "
        " stage_trace, reasoning, evaluated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (o_cloud_b, "gemma-4-31b-it", "STRONG", 7, "[]", "x",
         _at(0).replace("T12:", "T19:")),
    )

    ev = client.get("/api/pipeline?range=today").json()["evaluator"]
    assert ev["tiers"]["TOP_TIER"] == 1
    assert ev["tiers"]["STRONG"] == 1
    assert ev["tiers"]["SKIP"] == 0  # local-evaluator row is excluded


def test_pipeline_evaluator_budget_uses_free_tier_ceiling(harness):
    client, _ = harness
    ev = client.get("/api/pipeline?range=today").json()["evaluator"]
    # Free tier RPD limit is 1,500 — the dashboard surfaces actual usage,
    # not the GemmaCloudClient.SOFT_STOP (1,200) safety margin.
    assert ev["budget"] == 1500


def test_pipeline_400_for_invalid_range(harness):
    client, _ = harness
    response = client.get("/api/pipeline?range=year")
    # FastAPI returns 422 by default for Literal validation, not 400.
    assert response.status_code in (400, 422)


def test_pipeline_empty_db_returns_zeros(harness):
    client, _ = harness
    body = client.get("/api/pipeline?range=week").json()
    for stage in body["stages"]:
        assert stage["count"] == 0
        assert stage["previous"] == 0
    assert body["sources"] == []
    assert body["evaluator"]["calls_today"] == 0
    assert body["evaluator"]["model"] is None


def test_pipeline_evaluated_excludes_evals_of_old_postings(harness):
    # Regression: re-evaluations of postings discovered before the period
    # must not inflate Evaluated past Discovered.
    client, t = harness
    old = _seed_opp(t, "OLD", days_ago=40)
    _seed_eval(t, old, tier="STRONG", days_ago=0)  # re-evaluated today
    new = _seed_opp(t, "NEW", days_ago=0)
    _seed_eval(t, new, tier="STRONG", days_ago=0)

    body = client.get("/api/pipeline?range=today").json()
    by_name = {s["name"]: s for s in body["stages"]}
    assert by_name["Discovered"]["count"] == 1
    assert by_name["Evaluated"]["count"] == 1
    # FIX-4: Shortlisted is now a current-state pool count (all
    # postings with a latest-eval in {TOP/STRONG/EXPLORATORY} whose
    # status is new/NULL), independent of `range`. Both OLD and NEW
    # qualify, so the pool count is 2.
    assert by_name["Shortlisted"]["count"] == 2


@pytest.mark.parametrize("range_key", ["today", "week", "month"])
def test_pipeline_funnel_invariant_holds(harness, range_key):
    client, t = harness
    # Mix designed to trigger the bug if anchoring on evaluated_at:
    # in-range opps with various tiers, plus an out-of-range opp that
    # was re-evaluated inside the window.
    n1 = _seed_opp(t, "N1", days_ago=0)
    n2 = _seed_opp(t, "N2", days_ago=2)
    n3 = _seed_opp(t, "N3", days_ago=20)
    old = _seed_opp(t, "OLD", days_ago=60)
    _seed_eval(t, n1, tier="TOP_TIER", days_ago=0)
    _seed_eval(t, n2, tier="STRONG", days_ago=2)
    _seed_eval(t, n3, tier="EXPLORATORY", days_ago=20)
    _seed_eval(t, old, tier="STRONG", days_ago=0)

    body = client.get(f"/api/pipeline?range={range_key}").json()
    by_name = {s["name"]: s for s in body["stages"]}
    assert (
        by_name["Evaluated"]["count"] <= by_name["Discovered"]["count"]
    ), f"Evaluated > Discovered for range={range_key}: {by_name}"
    # FIX-4: Shortlisted is now a current-state count rather than a
    # range-bounded event count, so it is intentionally decoupled
    # from Evaluated (it can be larger when the pool spans periods).
    # Selected -> Docs Ready -> Applied still respect a funnel
    # invariant within the same period.
    assert by_name["Selected"]["count"] <= by_name["Shortlisted"]["count"], (
        f"Selected > Shortlisted for range={range_key}: {by_name}"
    )


@pytest.mark.parametrize("range_key", ["today", "week", "month"])
def test_pipeline_daily_breakdown_sums_match_stage_totals(
    harness, range_key,
):
    # Daily rows must sum to the stage total — otherwise the funnel
    # invariant could hold globally but break per-day.
    client, t = harness
    n1 = _seed_opp(t, "N1", days_ago=0)
    n2 = _seed_opp(t, "N2", days_ago=3)
    old = _seed_opp(t, "OLD", days_ago=60)
    _seed_eval(t, n1, tier="TOP_TIER", days_ago=0)
    _seed_eval(t, n2, tier="STRONG", days_ago=3)
    _seed_eval(t, old, tier="STRONG", days_ago=0)

    body = client.get(f"/api/pipeline?range={range_key}").json()
    by_name = {s["name"]: s for s in body["stages"]}
    daily = body["daily_breakdown"]
    assert sum(r["discovered"] for r in daily) == by_name["Discovered"]["count"]
    assert sum(r["evaluated"] for r in daily) == by_name["Evaluated"]["count"]
    # FIX-4: the daily `shortlisted` series counts user *selections*
    # (applications.selected_at) inside the range, while the stage
    # total counts the current Shortlist pool. They are intentionally
    # different surfaces — only the Discovered / Evaluated series
    # still mirror their corresponding stage totals.
