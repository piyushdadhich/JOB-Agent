"""Tests for /api/pipeline/* tracker endpoints (Spec G1 TASK 1)."""
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


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _now_offset(days: int = 0) -> str:
    return _iso(datetime.now(timezone.utc) - timedelta(days=days))


def _seed_opp(t, employer="ATCO", title="PM", location="Calgary"):
    cid = t.upsert_company(employer)
    oid, _ = t.insert_opportunity(
        company_id=cid, source="workday",
        source_url=f"https://x/{employer}/{title}",
        title=title, location=location, posting_text="...",
    )
    return oid


def _seed_eval(t, oid, tier="STRONG", fit=8):
    t._execute(
        "INSERT INTO eval_decisions "
        "(opportunity_id, evaluator_version, tier, fit_score, "
        " stage_trace, reasoning, evaluated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (oid, "test-v1", tier, fit, "[]", "x", _now_offset(0)),
    )


def _seed_selected_app(t, employer, title, days_ago_select=0):
    oid = _seed_opp(t, employer=employer, title=title)
    _seed_eval(t, oid)
    app_id = t.create_application(
        opportunity_id=oid, resume_variant="dashboard_pending",
    )
    t.update_application_fields(
        app_id, selected_at=_now_offset(days_ago_select),
    )
    return app_id, oid


def _bump_status_age(t, app_id, days_ago):
    """Backdate the status_updated_at so the days_in_stage logic
    can be exercised without sleeping."""
    t._execute(
        "UPDATE applications SET status_updated_at = ? WHERE id = ?",
        (_now_offset(days_ago), app_id),
    )


def test_board_returns_grouped_columns(harness):
    client, t = harness
    _seed_selected_app(t, "ATCO", "PM")
    # A submitted application (Applied bucket)
    app2, _ = _seed_selected_app(t, "BMO", "Sr PM")
    t.update_application_status(app2, "submitted")
    # An interviewing application
    app3, _ = _seed_selected_app(t, "TD", "Lead PM")
    t.update_application_status(app3, "interviewing")
    # Offered
    app4, _ = _seed_selected_app(t, "Shopify", "Director")
    t.update_application_status(app4, "offered")
    # Rejected
    app5, _ = _seed_selected_app(t, "Acme", "Manager")
    t.update_application_status(app5, "rejected")

    r = client.get("/api/pipeline/board")
    assert r.status_code == 200
    body = r.json()
    cols = body["columns"]
    assert {"shortlisted", "applied", "interview", "offer", "rejected"} <= set(cols)
    assert len(cols["shortlisted"]) == 1
    assert len(cols["applied"]) == 1
    assert len(cols["interview"]) == 1
    assert len(cols["offer"]) == 1
    assert len(cols["rejected"]) == 1
    assert cols["shortlisted"][0]["employer"] == "ATCO"
    assert cols["applied"][0]["status"] == "applied"


def test_board_includes_stats(harness):
    client, t = harness
    _seed_selected_app(t, "ATCO", "PM")
    app2, _ = _seed_selected_app(t, "BMO", "Sr PM")
    t.update_application_status(app2, "submitted")
    r = client.get("/api/pipeline/board")
    stats = r.json()["stats"]
    assert stats["total"] == 2
    assert stats["shortlisted"] == 1
    assert stats["applied"] == 1
    assert stats["needs_followup"] == 0


def test_update_status_valid(harness):
    client, t = harness
    app_id, _ = _seed_selected_app(t, "ATCO", "PM")
    r = client.put(
        f"/api/pipeline/{app_id}/status",
        json={"status": "interview"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "interview"
    assert body["db_status"] == "interviewing"


def test_update_status_sets_applied_at(harness):
    client, t = harness
    app_id, _ = _seed_selected_app(t, "ATCO", "PM")
    assert t.get_application_by_id(app_id)["submitted_date"] is None
    r = client.put(
        f"/api/pipeline/{app_id}/status",
        json={"status": "applied"},
    )
    assert r.status_code == 200
    assert r.json()["applied_at"] is not None
    assert t.get_application_by_id(app_id)["submitted_date"] is not None


def test_update_status_invalid_rejected(harness):
    client, t = harness
    app_id, _ = _seed_selected_app(t, "ATCO", "PM")
    r = client.put(
        f"/api/pipeline/{app_id}/status",
        json={"status": "garbage"},
    )
    assert r.status_code == 422  # Pydantic Literal validation


def test_update_notes(harness):
    client, t = harness
    app_id, _ = _seed_selected_app(t, "ATCO", "PM")
    note = "Interviewed with VP Ops, follow up Thursday"
    r = client.put(
        f"/api/pipeline/{app_id}/notes",
        json={"notes": note},
    )
    assert r.status_code == 200
    assert r.json()["notes"] == note
    persisted = t.get_application_by_id(app_id)
    assert persisted["notes"] == note


def test_stats_includes_followup(harness):
    client, t = harness
    # Applied 10 days ago — should trigger followup (> 7 days).
    app1, _ = _seed_selected_app(t, "ATCO", "PM")
    t.update_application_status(app1, "submitted")
    _bump_status_age(t, app1, days_ago=10)
    t.update_application_fields(
        app1, submitted_date=_now_offset(days=10),
    )
    r = client.get("/api/pipeline/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["needs_followup"] == 1
    items = body["needs_followup_items"]
    assert len(items) == 1
    assert items[0]["employer"] == "ATCO"
    assert items[0]["days_since_applied"] >= 7
    # This_month buckets exist
    assert "this_month" in body
    assert body["this_month"]["applied"] >= 1


def test_followup_only_after_7_days(harness):
    client, t = harness
    # Applied 3 days ago — should NOT be flagged.
    app1, _ = _seed_selected_app(t, "ATCO", "PM")
    t.update_application_status(app1, "submitted")
    _bump_status_age(t, app1, days_ago=3)
    body = client.get("/api/pipeline/board").json()
    applied_cards = body["columns"]["applied"]
    assert len(applied_cards) == 1
    assert applied_cards[0]["needs_followup"] is False


def test_shortlisted_filters_to_selected_at(harness):
    """Apps in drafted/ready_to_submit but lacking selected_at must
    not appear on the board — verifies Shortlist Deselect removes
    the Pipeline card without dropping the application row."""
    client, t = harness
    oid = _seed_opp(t, "ATCO", "PM")
    _seed_eval(t, oid)
    app_id = t.create_application(
        opportunity_id=oid, resume_variant="dashboard_pending",
    )
    # No selected_at set → should not appear.
    body = client.get("/api/pipeline/board").json()
    assert body["stats"]["total"] == 0
    # Now set selected_at → appears in shortlisted column.
    t.update_application_fields(app_id, selected_at=_now_offset(0))
    body2 = client.get("/api/pipeline/board").json()
    assert body2["stats"]["total"] == 1
    assert len(body2["columns"]["shortlisted"]) == 1
    # Now clear selected_at (simulating Deselect) → card disappears.
    t.update_application_fields(app_id, selected_at=None)
    body3 = client.get("/api/pipeline/board").json()
    assert body3["stats"]["total"] == 0


def test_update_status_404_for_unknown_app(harness):
    client, _ = harness
    r = client.put(
        "/api/pipeline/9999/status", json={"status": "applied"},
    )
    assert r.status_code == 404
