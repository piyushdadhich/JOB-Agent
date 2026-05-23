"""Tests for /api/prompts GET resume / cover-letter and POST save."""
from __future__ import annotations

import sys
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

    def _tracker():
        yield tracker

    def _profile():
        return "p"

    app.dependency_overrides[get_tracker] = _tracker
    app.dependency_overrides[get_profile_id] = _profile
    yield TestClient(app), tracker
    tracker.close()


def _add_posting(t, employer="TD", title="PM"):
    cid = t.upsert_company(employer)
    oid, _ = t.insert_opportunity(
        company_id=cid, source="manual_entry",
        source_url=f"https://x/{employer}", title=title,
        location="Toronto",
        posting_text="Lead delivery, manage stakeholders.",
    )
    return oid


def test_get_resume_prompt_includes_posting_details(harness):
    client, t = harness
    oid = _add_posting(t, "TD", "Senior PM")
    r = client.get(f"/api/prompts/{oid}/resume")
    assert r.status_code == 200
    body = r.json()
    assert body["posting_id"] == oid
    assert "Senior PM" in body["prompt"]
    assert "TD" in body["prompt"]
    assert "OUTPUT TEMPLATE" in body["prompt"]


def test_get_cover_letter_prompt_includes_posting_details(harness):
    client, t = harness
    oid = _add_posting(t, "BMO", "Delivery Lead")
    r = client.get(f"/api/prompts/{oid}/cover-letter")
    assert r.status_code == 200
    body = r.json()
    assert body["posting_id"] == oid
    assert "Delivery Lead" in body["prompt"]
    assert "BMO" in body["prompt"]
    assert "Cover Letter" in body["prompt"]


def test_get_prompt_404_for_unknown_posting(harness):
    client, _ = harness
    r = client.get("/api/prompts/9999/resume")
    assert r.status_code == 404


def test_get_prompt_stamps_prompt_generated_at_when_app_exists(harness):
    client, t = harness
    oid = _add_posting(t)
    app_id = t.create_application(
        opportunity_id=oid, resume_variant="dashboard_pending",
    )
    assert t.get_application_by_id(app_id)["prompt_generated_at"] is None
    client.get(f"/api/prompts/{oid}/resume")
    row = t.get_application_by_id(app_id)
    assert row["prompt_generated_at"] is not None


def test_get_prompt_no_app_does_not_create_one(harness):
    """Generating a prompt is read-only when no application exists."""
    client, t = harness
    oid = _add_posting(t)
    r = client.get(f"/api/prompts/{oid}/resume")
    assert r.status_code == 200
    rows = t._query_all(
        "SELECT id FROM applications WHERE opportunity_id = ?", (oid,),
    )
    assert rows == []


def test_save_resume_text_persists_and_lazy_creates_application(harness):
    client, t = harness
    oid = _add_posting(t)
    r = client.post(
        f"/api/prompts/{oid}/resume",
        json={"content": "## RESUME-MARKDOWN-1234"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["chars"] == len("## RESUME-MARKDOWN-1234")
    assert body["docs_ready_at"] is None  # CL not saved yet
    row = t.get_application_by_id(body["application_id"])
    assert row["resume_text"] == "## RESUME-MARKDOWN-1234"


def test_save_cover_letter_after_resume_sets_docs_ready_at(harness):
    client, t = harness
    oid = _add_posting(t)
    client.post(
        f"/api/prompts/{oid}/resume",
        json={"content": "RESUME"},
    )
    r = client.post(
        f"/api/prompts/{oid}/cover-letter",
        json={"content": "DEAR HIRING MANAGER..."},
    )
    body = r.json()
    assert body["docs_ready_at"] is not None
    row = t.get_application_by_id(body["application_id"])
    assert row["resume_text"] == "RESUME"
    assert row["cover_letter_text"] == "DEAR HIRING MANAGER..."
    assert row["docs_ready_at"] is not None


def test_save_text_404_for_unknown_posting(harness):
    client, _ = harness
    r = client.post(
        "/api/prompts/9999/resume",
        json={"content": "x"},
    )
    assert r.status_code == 404
