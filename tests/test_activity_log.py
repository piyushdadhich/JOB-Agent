"""Tests for the system activity log — logger + endpoints."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from dashboard.backend.app import create_app  # noqa: E402
from dashboard.backend.deps import get_tracker  # noqa: E402
from engine import activity_log  # noqa: E402
from engine.persistence.tracker import Tracker  # noqa: E402


@pytest.fixture
def harness(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    tracker = Tracker(profile_id="p", db_path=db)
    # log_activity opens its own Tracker(profile_id) — point that at
    # the same temp DB file so the test sees the rows it writes.
    monkeypatch.setattr(
        activity_log, "Tracker",
        lambda pid: Tracker(profile_id="p", db_path=db),
        raising=False,
    )
    app = create_app()
    app.dependency_overrides[get_tracker] = lambda: tracker
    yield TestClient(app), tracker
    tracker.close()


def test_log_activity_writes_row(harness):
    client, t = harness
    activity_log.log_activity(
        "p", "generation", "resume_generated", "success",
        "Resume for Acme — PM", opportunity_id=42, duration_ms=1200,
    )
    row = t._query_one("SELECT * FROM activity_log")
    assert row["category"] == "generation"
    assert row["action"] == "resume_generated"
    assert row["status"] == "success"
    assert row["opportunity_id"] == 42
    assert row["duration_ms"] == 1200
    assert row["timestamp"]  # default-stamped


def test_log_activity_failure_is_silent(monkeypatch):
    """A logging failure must never raise into the caller."""
    def _boom(pid):
        raise RuntimeError("db locked")

    monkeypatch.setattr(activity_log, "Tracker", _boom, raising=False)
    # Must not raise.
    activity_log.log_activity(
        "p", "system", "test", "error", "should be swallowed",
    )


def test_activity_log_endpoint_returns_items(harness):
    client, t = harness
    for i in range(3):
        activity_log.log_activity(
            "p", "discovery", "run_completed", "success",
            f"source-{i}: {i} new",
        )
    body = client.get("/api/activity-log").json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    # Newest first.
    assert body["items"][0]["category"] == "discovery"


def test_activity_log_endpoint_filters_by_category(harness):
    client, t = harness
    activity_log.log_activity("p", "discovery", "x", "success", "d")
    activity_log.log_activity("p", "evaluation", "x", "success", "e")
    body = client.get("/api/activity-log?category=evaluation").json()
    assert body["total"] == 1
    assert body["items"][0]["category"] == "evaluation"


def test_activity_log_summary(harness):
    client, t = harness
    activity_log.log_activity(
        "p", "discovery", "run_completed", "success", "268 new",
    )
    activity_log.log_activity(
        "p", "evaluation", "run_failed", "error", "eval crashed",
        error_message="boom",
    )
    activity_log.log_activity(
        "p", "system", "source_stale", "warning", "linkedin stale",
    )
    s = client.get("/api/activity-log/summary").json()
    assert s["last_discovery"]["summary"] == "268 new"
    assert s["last_evaluation"]["status"] == "error"
    assert s["last_generation"] is None
    assert s["errors_last_24h"] == 1
    assert s["warnings_last_24h"] == 1
