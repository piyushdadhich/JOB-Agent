"""Spec 13 TASK 2 — digest generator + endpoint tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from dashboard.backend import app as app_module
from engine.digest.generator import generate
from engine.persistence.tracker import Tracker


def _seed(tracker: Tracker, *, hours_ago: int, tier: str, score: int) -> int:
    cid = tracker.upsert_company("Co")
    opp_id, _ = tracker.insert_opportunity(
        company_id=cid, source="t",
        source_url=f"https://example.com/{hours_ago}-{tier}-{score}",
        title="T",
    )
    when = (
        datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    ).isoformat()
    tracker._execute(
        "INSERT INTO eval_decisions "
        "(opportunity_id, evaluator_version, tier, fit_score, "
        " stage_trace, evaluated_at) VALUES (?, 'test-1', ?, ?, '[]', ?)",
        (opp_id, tier, score, when),
    )
    return opp_id


@pytest.fixture
def tracker(tmp_path):
    t = Tracker(profile_id="default", db_path=tmp_path / "t.db")
    yield t
    t.close()


def test_generate_counts_recent_tiers(tracker):
    _seed(tracker, hours_ago=1, tier="TOP_TIER", score=8)
    _seed(tracker, hours_ago=2, tier="STRONG", score=6)
    _seed(tracker, hours_ago=30, tier="TOP_TIER", score=9)  # outside window
    d = generate(tracker, window_hours=24)
    assert d.new_top_tier == 1
    assert d.new_strong == 1
    assert d.new_exploratory == 0


def test_generate_counts_zero_when_empty(tracker):
    d = generate(tracker, window_hours=24)
    assert d.new_top_tier == 0
    assert d.new_strong == 0


def test_generate_takes_latest_eval_per_opportunity(tracker):
    cid = tracker.upsert_company("Co")
    opp_id, _ = tracker.insert_opportunity(
        company_id=cid, source="t",
        source_url="https://example.com/dup", title="T",
    )
    now = datetime.now(timezone.utc)
    # First decision: STRONG (older).
    tracker._execute(
        "INSERT INTO eval_decisions "
        "(opportunity_id, evaluator_version, tier, fit_score, "
        " stage_trace, evaluated_at) VALUES (?, 'v1', 'STRONG', 5, '[]', ?)",
        (opp_id, (now - timedelta(hours=2)).isoformat()),
    )
    # Second decision: TOP_TIER (newer). Only this one should count.
    tracker._execute(
        "INSERT INTO eval_decisions "
        "(opportunity_id, evaluator_version, tier, fit_score, "
        " stage_trace, evaluated_at) VALUES (?, 'v2', 'TOP_TIER', 9, '[]', ?)",
        (opp_id, (now - timedelta(hours=1)).isoformat()),
    )
    d = generate(tracker, window_hours=24)
    assert d.new_top_tier == 1
    assert d.new_strong == 0


def test_generate_counts_stale_followups(tracker):
    cid = tracker.upsert_company("Co")
    opp_id, _ = tracker.insert_opportunity(
        company_id=cid, source="t",
        source_url="https://example.com/fu", title="T",
    )
    app_id = tracker.create_application(
        opportunity_id=opp_id, resume_variant="public_sector",
    )
    # Push the status row into a stale-applied state.
    stale = (
        datetime.now(timezone.utc) - timedelta(days=10)
    ).isoformat()
    tracker._execute(
        "UPDATE applications "
        "SET status = 'submitted', status_updated_at = ? "
        "WHERE id = ?",
        (stale, app_id),
    )
    d = generate(tracker, window_hours=24)
    assert d.needs_followup == 1


# --- Endpoint ----------------------------------------------------

def test_digest_endpoint_returns_payload(tmp_path):
    db = tmp_path / "t.db"
    t = Tracker(profile_id="default", db_path=db)
    _seed(t, hours_ago=1, tier="TOP_TIER", score=8)
    t.close()

    from dashboard.backend.deps import get_tracker

    def _ovr():
        tt = Tracker(profile_id="default", db_path=db)
        try:
            yield tt
        finally:
            tt.close()

    import dashboard.backend.app as app_module2
    app_module2.FRONTEND_DIST = tmp_path / "nope"
    app = app_module2.create_app()
    app.dependency_overrides[get_tracker] = _ovr
    client = TestClient(app)
    r = client.get("/api/digest/today")
    assert r.status_code == 200
    body = r.json()
    assert body["new_top_tier"] == 1
    assert body["window_hours"] == 24
