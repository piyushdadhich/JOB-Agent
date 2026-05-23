"""Tests for /api/stats/* aggregation endpoints."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from dashboard.backend.app import create_app  # noqa: E402
from dashboard.backend.deps import get_tracker  # noqa: E402
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


def _now_iso(offset_minutes: int = 0) -> str:
    return (
        datetime.now(timezone.utc)
        + timedelta(minutes=offset_minutes)
    ).isoformat()


def _seed_opportunity(t, employer="TD", source="workday"):
    cid = t.upsert_company(employer)
    oid, _ = t.insert_opportunity(
        company_id=cid, source=source,
        source_url=f"https://x/{employer}", title="PM",
        location="Toronto", posting_text="...",
    )
    return oid


def _seed_eval(t, opp_id, tier="STRONG", fit=7, version="v1"):
    t._execute(
        "INSERT INTO eval_decisions "
        "(opportunity_id, evaluator_version, tier, fit_score, "
        " stage_trace, reasoning, evaluated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (opp_id, version, tier, fit, "[]", "x", _now_iso()),
    )


def test_stats_today_with_seeded_data(harness):
    client, t = harness
    o1 = _seed_opportunity(t, "TD")
    o2 = _seed_opportunity(t, "BMO")
    _seed_eval(t, o1, "TOP_TIER", 9)
    _seed_eval(t, o2, "STRONG", 7)
    body = client.get("/api/stats/today").json()
    assert body["opportunities_total"] == 2
    assert body["opportunities_today"] == 2
    assert body["companies_total"] == 2
    assert body["evaluations_today"] == 2
    assert body["top_tier_today"] == 1
    assert body["strong_today"] == 1


def test_stats_today_empty(harness):
    client, _ = harness
    body = client.get("/api/stats/today").json()
    assert body["opportunities_total"] == 0
    assert body["evaluations_today"] == 0


def test_stats_sources_aggregates_per_source(harness):
    client, t = harness
    _seed_opportunity(t, "TD", source="workday")
    _seed_opportunity(t, "BMO", source="workday")
    _seed_opportunity(t, "Acme", source="lever_api")
    body = client.get("/api/stats/sources").json()
    by_src = {r["source"]: r for r in body}
    assert by_src["workday"]["total"] == 2
    assert by_src["lever_api"]["total"] == 1


def test_stats_evaluator_lists_versions(harness):
    client, t = harness
    o1 = _seed_opportunity(t, "TD")
    o2 = _seed_opportunity(t, "BMO")
    _seed_eval(t, o1, version="gemma-v1", fit=8)
    _seed_eval(t, o2, version="gemma-v2", fit=6)
    body = client.get("/api/stats/evaluator").json()
    versions = {v["version"]: v["calls"] for v in body["versions"]}
    assert versions["gemma-v1"] == 1
    assert versions["gemma-v2"] == 1
    assert body["calls_today"] == 2
    assert body["avg_fit_score_today"] == 7.0


def test_stats_evaluator_empty(harness):
    client, _ = harness
    body = client.get("/api/stats/evaluator").json()
    assert body["versions"] == []
    assert body["calls_today"] == 0
    assert body["avg_fit_score_today"] is None


def test_stats_applications_summary(harness):
    client, t = harness
    o1 = _seed_opportunity(t, "TD")
    o2 = _seed_opportunity(t, "BMO")
    o3 = _seed_opportunity(t, "Acme")
    a1 = t.create_application(opportunity_id=o1, resume_variant="r")
    a2 = t.create_application(opportunity_id=o2, resume_variant="r")
    a3 = t.create_application(opportunity_id=o3, resume_variant="r")
    t.update_application_status(a1, "submitted")
    t.update_application_status(a2, "interviewing")
    body = client.get("/api/stats/applications").json()
    assert body["by_status"]["submitted"] == 1
    assert body["by_status"]["interviewing"] == 1
    assert body["by_status"]["drafted"] == 1
    assert body["pending_review"] == 1
    assert body["interviews_active"] == 1
    assert body["submitted_this_week"] == 1


def test_stats_applications_empty(harness):
    client, _ = harness
    body = client.get("/api/stats/applications").json()
    assert body["by_status"] == {}
    assert body["submitted_this_week"] == 0
