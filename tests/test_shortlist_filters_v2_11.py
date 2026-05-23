"""Tests for /api/shortlist multi-axis filters and
/api/shortlist/filter-options (Spec 2 TASK 1)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from dashboard.backend.app import create_app  # noqa: E402
from dashboard.backend.deps import get_tracker  # noqa: E402
from engine.discovery.base import OpportunityRecord  # noqa: E402
from engine.persistence.opportunities import persist_record  # noqa: E402
from engine.persistence.tracker import Tracker  # noqa: E402


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


def _seed_eval(tracker, opp_id, tier, fit_score=8):
    """Inject a TOP_TIER/STRONG eval so the posting reaches shortlist."""
    now = datetime.now(timezone.utc).isoformat()
    tracker._execute(
        "INSERT INTO eval_decisions "
        "(opportunity_id, evaluator_version, tier, fit_score, "
        " stage_trace, reasoning, evaluated_at) "
        "VALUES (?, 'test', ?, ?, '[]', 'good', ?)",
        (opp_id, tier, fit_score, now),
    )


def _persist(tracker, *, employer, title, location, url):
    """Use persist_record so v2.11 city/ai_subtype/eval_priority stamp."""
    rec = OpportunityRecord(
        source="manual_entry",
        source_url=url,
        employer=employer,
        title=title,
        location=location,
        posting_text="x",
        date_discovered=datetime(2026, 5, 9, tzinfo=timezone.utc),
    )
    opp_id, _ = persist_record(tracker, rec)
    return opp_id


def _set_classification(tracker, opp_id, *, function=None, industry=None):
    """Direct UPDATE since persist_record leaves these columns NULL
    (they're populated by the v2.11 backfill of eval_decisions
    reasoning, not at insert time)."""
    tracker._execute(
        "UPDATE opportunities SET function = ?, industry_normalized = ? "
        "WHERE id = ?",
        (function, industry, opp_id),
    )


# --- single-axis + AND-of-ORs ------------------------------------

def test_filter_by_single_function(harness):
    client, t = harness
    a = _persist(t, employer="A", title="PM", location="Toronto, ON",
                 url="https://x/1")
    b = _persist(t, employer="B", title="PM", location="Toronto, ON",
                 url="https://x/2")
    _seed_eval(t, a, "TOP_TIER")
    _seed_eval(t, b, "TOP_TIER")
    _set_classification(t, a, function="project_manager")
    _set_classification(t, b, function="scrum_master")

    items = client.get(
        "/api/shortlist?function=project_manager"
    ).json()
    assert [i["opportunity_id"] for i in items] == [a]


def test_filter_by_multiple_function_values_or_within_axis(harness):
    client, t = harness
    a = _persist(t, employer="A", title="PM", location="Toronto",
                 url="https://x/1")
    b = _persist(t, employer="B", title="PM", location="Toronto",
                 url="https://x/2")
    c = _persist(t, employer="C", title="PM", location="Toronto",
                 url="https://x/3")
    for o in (a, b, c):
        _seed_eval(t, o, "TOP_TIER")
    _set_classification(t, a, function="project_manager")
    _set_classification(t, b, function="scrum_master")
    _set_classification(t, c, function="business_analyst")

    items = client.get(
        "/api/shortlist?function=project_manager,scrum_master"
    ).json()
    assert sorted(i["opportunity_id"] for i in items) == sorted([a, b])


def test_filter_function_and_industry_intersection(harness):
    client, t = harness
    a = _persist(t, employer="A", title="PM", location="Toronto",
                 url="https://x/1")
    b = _persist(t, employer="B", title="PM", location="Toronto",
                 url="https://x/2")
    _seed_eval(t, a, "TOP_TIER")
    _seed_eval(t, b, "TOP_TIER")
    _set_classification(t, a, function="project_manager",
                        industry="banking")
    _set_classification(t, b, function="project_manager",
                        industry="energy")

    items = client.get(
        "/api/shortlist?function=project_manager&industry=banking"
    ).json()
    assert [i["opportunity_id"] for i in items] == [a]


# --- mode=hide ----------------------------------------------------

def test_mode_hide_negates_function_filter(harness):
    client, t = harness
    a = _persist(t, employer="A", title="PM", location="Toronto",
                 url="https://x/1")
    b = _persist(t, employer="B", title="PM", location="Toronto",
                 url="https://x/2")
    _seed_eval(t, a, "TOP_TIER")
    _seed_eval(t, b, "TOP_TIER")
    _set_classification(t, a, function="project_manager")
    _set_classification(t, b, function="scrum_master")

    items = client.get(
        "/api/shortlist?function=project_manager&mode=hide"
    ).json()
    assert [i["opportunity_id"] for i in items] == [b]


def test_mode_hide_with_two_filters(harness):
    client, t = harness
    a = _persist(t, employer="A", title="PM", location="Toronto",
                 url="https://x/1")
    b = _persist(t, employer="B", title="PM", location="Toronto",
                 url="https://x/2")
    c = _persist(t, employer="C", title="PM", location="Toronto",
                 url="https://x/3")
    for o in (a, b, c):
        _seed_eval(t, o, "TOP_TIER")
    _set_classification(t, a, function="project_manager",
                        industry="banking")
    _set_classification(t, b, function="scrum_master",
                        industry="energy")
    _set_classification(t, c, function="business_analyst",
                        industry="healthcare")

    # Hide rows where function=PM AND industry=banking. Only `a`
    # matches both; `b` and `c` survive.
    items = client.get(
        "/api/shortlist"
        "?function=project_manager&industry=banking&mode=hide"
    ).json()
    assert sorted(i["opportunity_id"] for i in items) == sorted([b, c])


def test_invalid_mode_returns_400(harness):
    client, _ = harness
    r = client.get("/api/shortlist?mode=garbage")
    assert r.status_code == 400


# --- city: legacy slug back-compat + new canonical list ----------

def test_city_filter_legacy_gta_slug_uses_like_patterns(harness):
    """?city=gta still uses LIKE matching against o.location
    (preserves bookmarks from the pre-Spec-2 single-select UI)."""
    client, t = harness
    a = _persist(t, employer="A", title="PM",
                 location="Mississauga, ON", url="https://x/1")
    b = _persist(t, employer="B", title="PM",
                 location="Calgary, AB", url="https://x/2")
    _seed_eval(t, a, "TOP_TIER")
    _seed_eval(t, b, "TOP_TIER")

    items = client.get("/api/shortlist?city=gta").json()
    # `a` is in Mississauga which matches the gta LIKE patterns.
    assert [i["opportunity_id"] for i in items] == [a]


def test_city_filter_canonical_list_uses_exact_match(harness):
    """?city=Toronto,Calgary uses IN against the v2.11 o.city column."""
    client, t = harness
    a = _persist(t, employer="A", title="PM", location="Toronto, ON",
                 url="https://x/1")
    b = _persist(t, employer="B", title="PM", location="Calgary, AB",
                 url="https://x/2")
    c = _persist(t, employer="C", title="PM", location="Edmonton, AB",
                 url="https://x/3")
    for o in (a, b, c):
        _seed_eval(t, o, "TOP_TIER")

    items = client.get("/api/shortlist?city=Toronto,Calgary").json()
    assert sorted(i["opportunity_id"] for i in items) == sorted([a, b])


# --- /filter-options endpoint ------------------------------------

def test_filter_options_returns_distinct_values(harness):
    client, t = harness
    a = _persist(t, employer="A", title="AI Engineer",
                 location="Toronto", url="https://x/1")
    b = _persist(t, employer="B", title="ML Engineer",
                 location="Calgary", url="https://x/2")
    _set_classification(t, a, function="project_manager",
                        industry="banking")
    _set_classification(t, b, function="scrum_master",
                        industry="energy")

    body = client.get("/api/shortlist/filter-options").json()
    assert sorted(body["functions"]) == ["project_manager", "scrum_master"]
    assert sorted(body["industries"]) == ["banking", "energy"]
    assert sorted(body["cities"]) == ["Calgary", "Toronto"]
    # AI Engineer + ML Engineer both classify as ai_engineer; distinct.
    assert body["ai_subtypes"] == ["ai_engineer"]


def test_filter_options_empty_db_returns_empty_lists(harness):
    """Regression on the empty-axis handling: every axis is [] when
    no opportunities exist."""
    client, _ = harness
    body = client.get("/api/shortlist/filter-options").json()
    assert body == {
        "functions": [],
        "industries": [],
        "cities": [],
        "ai_subtypes": [],
    }


def test_filter_options_partially_populated(harness):
    """Day-1 reality: city + ai_subtype have values from classify_*;
    function and industry stay empty until reasoning JSON populates."""
    client, t = harness
    a = _persist(t, employer="A", title="AI Engineer",
                 location="Toronto", url="https://x/1")
    _seed_eval(t, a, "TOP_TIER")
    body = client.get("/api/shortlist/filter-options").json()
    assert body["functions"] == []
    assert body["industries"] == []
    assert body["cities"] == ["Toronto"]
    assert body["ai_subtypes"] == ["ai_engineer"]


def test_unfiltered_shortlist_returns_all_top_tier_strong(harness):
    client, t = harness
    a = _persist(t, employer="A", title="PM", location="Toronto",
                 url="https://x/1")
    b = _persist(t, employer="B", title="PM", location="Calgary",
                 url="https://x/2")
    _seed_eval(t, a, "TOP_TIER")
    _seed_eval(t, b, "STRONG")

    items = client.get("/api/shortlist").json()
    assert sorted(i["opportunity_id"] for i in items) == sorted([a, b])
