"""Tests for /api/applications list / get / status update."""
from __future__ import annotations

import sys
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


def _seed_application(t, employer="TD", title="PM", variant="r.docx"):
    cid = t.upsert_company(employer)
    oid, _ = t.insert_opportunity(
        company_id=cid, source="manual_entry",
        source_url=f"https://x/{employer}", title=title,
        location="Toronto", posting_text="...",
    )
    app_id = t.create_application(
        opportunity_id=oid, resume_variant=variant,
    )
    return app_id, oid


def test_list_applications_joined_with_employer_and_title(harness):
    client, t = harness
    _seed_application(t, "TD", "Senior PM")
    _seed_application(t, "BMO", "Delivery Lead")
    r = client.get("/api/applications")
    assert r.status_code == 200
    rows = r.json()
    employers = {row["employer"] for row in rows}
    titles = {row["opportunity_title"] for row in rows}
    assert employers == {"TD", "BMO"}
    assert titles == {"Senior PM", "Delivery Lead"}


def test_get_application_by_id_includes_lifecycle_columns(harness):
    client, t = harness
    app_id, _ = _seed_application(t)
    t.update_application_fields(
        app_id,
        selected_at="2026-05-07T10:00:00Z",
        prompt_generated_at="2026-05-07T10:05:00Z",
    )
    r = client.get(f"/api/applications/{app_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == app_id
    assert body["selected_at"] == "2026-05-07T10:00:00Z"
    assert body["prompt_generated_at"] == "2026-05-07T10:05:00Z"


def test_get_application_404(harness):
    client, _ = harness
    r = client.get("/api/applications/9999")
    assert r.status_code == 404


def test_update_status_happy_path(harness):
    client, t = harness
    app_id, _ = _seed_application(t)
    r = client.put(
        f"/api/applications/{app_id}/status",
        json={"status": "submitted", "reason": "applied via dashboard"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "submitted"
    assert t.get_application_by_id(app_id)["status"] == "submitted"


def test_update_status_invalid_value_returns_400(harness):
    client, t = harness
    app_id, _ = _seed_application(t)
    r = client.put(
        f"/api/applications/{app_id}/status",
        json={"status": "definitely_not_a_real_status"},
    )
    assert r.status_code == 400


def test_update_status_404_for_unknown_id(harness):
    client, _ = harness
    r = client.put(
        "/api/applications/9999/status",
        json={"status": "submitted"},
    )
    assert r.status_code == 404
