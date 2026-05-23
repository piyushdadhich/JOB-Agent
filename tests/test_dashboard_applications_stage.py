"""Tests for the ?stage filter on /api/applications."""
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


def _seed_app(t, employer, *, selected=False, resume=False, cl=False, status="drafted"):
    cid = t.upsert_company(employer)
    oid, _ = t.insert_opportunity(
        company_id=cid, source="manual_entry",
        source_url=f"https://x/{employer}", title="PM",
        location="Toronto", posting_text="...",
    )
    app_id = t.create_application(
        opportunity_id=oid, resume_variant="dashboard_pending",
    )
    if selected:
        t.update_application_fields(
            app_id, selected_at="2026-05-07T10:00:00Z",
        )
    if resume:
        t.update_application_fields(app_id, resume_text="RESUME")
    if cl:
        t.update_application_fields(app_id, cover_letter_text="CL")
    if resume and cl:
        t.update_application_fields(
            app_id, docs_ready_at="2026-05-07T10:30:00Z",
        )
    if status != "drafted":
        t.update_application_status(app_id, status)
    return app_id


def test_stage_needs_prompts_returns_only_unfinished_drafted(harness):
    client, t = harness
    a_unselected = _seed_app(t, "A")
    a_selected = _seed_app(t, "B", selected=True)
    a_partial = _seed_app(t, "C", selected=True, resume=True)
    a_ready = _seed_app(t, "D", selected=True, resume=True, cl=True)
    rows = client.get(
        "/api/applications?stage=needs_prompts",
    ).json()
    ids = {row["id"] for row in rows}
    assert a_selected in ids
    assert a_partial in ids
    assert a_unselected not in ids
    assert a_ready not in ids


def test_stage_ready_to_apply_returns_docs_ready(harness):
    client, t = harness
    pending = _seed_app(t, "X", selected=True)
    ready = _seed_app(t, "Y", selected=True, resume=True, cl=True)
    rows = client.get(
        "/api/applications?stage=ready_to_apply",
    ).json()
    ids = {row["id"] for row in rows}
    assert ready in ids
    assert pending not in ids


def test_stage_submitted_returns_post_drafted(harness):
    client, t = harness
    drafted = _seed_app(t, "X")
    submitted = _seed_app(
        t, "Y", selected=True, resume=True, cl=True,
        status="submitted",
    )
    rows = client.get("/api/applications?stage=submitted").json()
    ids = {row["id"] for row in rows}
    assert submitted in ids
    assert drafted not in ids


def test_unknown_stage_returns_400(harness):
    client, _ = harness
    r = client.get("/api/applications?stage=not_a_real_stage")
    assert r.status_code == 400


def test_no_stage_returns_all(harness):
    client, t = harness
    _seed_app(t, "A")
    _seed_app(t, "B", selected=True)
    rows = client.get("/api/applications").json()
    assert len(rows) == 2
