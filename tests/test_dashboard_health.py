"""Tests for the /api/health monitor endpoint."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from dashboard.backend.app import create_app  # noqa: E402
from dashboard.backend.deps import get_profile_id, get_tracker  # noqa: E402
from engine.persistence.tracker import Tracker  # noqa: E402


@pytest.fixture
def harness(tmp_path):
    db = tmp_path / "t.db"
    tracker = Tracker(profile_id="p", db_path=db)
    app = create_app()
    app.dependency_overrides[get_tracker] = lambda: tracker
    app.dependency_overrides[get_profile_id] = lambda: "p"
    yield TestClient(app), tracker
    tracker.close()


def _seed_opp(t, employer, *, last_seen_days_ago=0):
    cid = t.upsert_company(employer)
    oid, _ = t.insert_opportunity(
        company_id=cid, source=f"src_{employer}",
        source_url=f"https://x/{employer}", title="PM",
        location="Springfield", posting_text="...",
    )
    ts = (
        datetime.now(timezone.utc) - timedelta(days=last_seen_days_ago)
    ).isoformat()
    t._execute(
        "UPDATE opportunities SET last_seen_at = ? WHERE id = ?",
        (ts, oid),
    )
    return oid


def _check(body, name):
    return next(c for c in body["checks"] if c["name"] == name)


def test_health_endpoint_returns_checks(harness):
    client, _ = harness
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("healthy", "degraded", "unhealthy")
    names = {c["name"] for c in body["checks"]}
    assert names == {
        "sources", "evaluator", "scheduled_task",
        "api_budget", "disk",
    }


def test_health_warns_on_stale_sources(harness):
    client, t = harness
    _seed_opp(t, "FreshCo", last_seen_days_ago=0)
    _seed_opp(t, "StaleCo", last_seen_days_ago=3)
    body = client.get("/api/health").json()
    sources = _check(body, "sources")
    assert sources["status"] == "warning"
    assert "1 stale" in sources["message"]
    assert body["status"] in ("degraded", "unhealthy")


def test_health_warns_on_missing_evaluator(harness):
    client, _ = harness
    # Empty DB — no eval_decisions at all.
    body = client.get("/api/health").json()
    evaluator = _check(body, "evaluator")
    assert evaluator["status"] == "warning"
    assert "No evaluations" in evaluator["message"]


def test_health_warns_when_no_scheduled_task(harness):
    client, _ = harness
    body = client.get("/api/health").json()
    sched = _check(body, "scheduled_task")
    assert sched["status"] == "warning"
