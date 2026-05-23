"""Tests for /api/applications/board + opportunity-keyed companions."""
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


def _seed(t, employer, *, tier="STRONG", fit=7, selected=True,
          resume=False, cl=False, docs_ready=False, status="drafted"):
    cid = t.upsert_company(employer)
    oid, _ = t.insert_opportunity(
        company_id=cid, source="manual_entry",
        source_url=f"https://x/{employer}", title="PM",
        location="Springfield", posting_text="...",
    )
    t._execute(
        "INSERT INTO eval_decisions "
        "(opportunity_id, evaluator_version, tier, fit_score, "
        " stage_trace, reasoning, evaluated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (oid, "gemma-4-31b", tier, fit, "[]", "x", "2026-05-01T10:00:00Z"),
    )
    aid = t.create_application(
        opportunity_id=oid, resume_variant="dashboard_pending",
    )
    if selected:
        t.update_application_fields(
            aid, selected_at="2026-05-01T10:00:00Z",
        )
    if resume:
        t.update_application_fields(aid, resume_text="RESUME-MD")
    if cl:
        t.update_application_fields(aid, cover_letter_text="CL-MD")
    if docs_ready:
        t.update_application_fields(
            aid, docs_ready_at="2026-05-01T11:00:00Z",
        )
    if status != "drafted":
        t.update_application_status(aid, status)
    return oid, aid


def test_board_groups_by_status_bucket(harness):
    client, t = harness
    sel_opp, _ = _seed(t, "Sel")
    ready_opp, _ = _seed(t, "Ready", resume=True, cl=True, docs_ready=True)
    applied_opp, _ = _seed(
        t, "Applied", resume=True, cl=True, docs_ready=True,
        status="submitted",
    )
    interview_opp, _ = _seed(
        t, "Interview", resume=True, cl=True, docs_ready=True,
        status="interviewing",
    )

    body = client.get("/api/applications/board").json()
    by_id = {
        col: [c["opportunity_id"] for c in cards]
        for col, cards in body["columns"].items()
    }
    assert sel_opp in by_id["selected"]
    assert ready_opp in by_id["docs_ready"]
    assert applied_opp in by_id["applied"]
    assert interview_opp in by_id["interview"]
    assert body["stats"]["total"] == 4


def test_board_includes_resume_cover_letter_markdown(harness):
    client, t = harness
    opp, _ = _seed(t, "TD", resume=True, cl=True, docs_ready=True)
    body = client.get("/api/applications/board").json()
    card = next(
        c for c in body["columns"]["docs_ready"]
        if c["opportunity_id"] == opp
    )
    assert card["resume_saved"] is True
    assert card["cover_letter_saved"] is True
    assert card["resume_markdown"] == "RESUME-MD"
    assert card["cover_letter_markdown"] == "CL-MD"


def test_notes_lazy_creates_application(harness):
    client, t = harness
    cid = t.upsert_company("Acme")
    oid, _ = t.insert_opportunity(
        company_id=cid, source="manual_entry",
        source_url="https://x/Acme", title="PM",
        location="Springfield", posting_text="...",
    )
    r = client.post(
        f"/api/applications/by-opp/{oid}/notes",
        json={"notes": "hello"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["notes"] == "hello"
    assert tracker_notes(t, body["application_id"]) == "hello"


def tracker_notes(t, app_id):
    row = t.get_application_by_id(app_id)
    return row["notes"]


def test_notes_updates_existing_application(harness):
    client, t = harness
    opp, aid = _seed(t, "Sel")
    r = client.post(
        f"/api/applications/by-opp/{opp}/notes",
        json={"notes": "review the staff page"},
    )
    assert r.status_code == 200
    assert r.json()["application_id"] == aid
    assert tracker_notes(t, aid) == "review the staff page"


def test_status_by_opp_moves_bucket(harness):
    client, t = harness
    opp, aid = _seed(t, "Sel", resume=True, cl=True, docs_ready=True)
    r = client.post(
        f"/api/applications/by-opp/{opp}/status",
        json={"status": "applied"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "applied"
    assert body["db_status"] == "submitted"
    row = t.get_application_by_id(aid)
    assert row["status"] == "submitted"
    assert row["submitted_date"] is not None


def test_status_by_opp_404_for_unknown_opportunity(harness):
    client, _ = harness
    r = client.post(
        "/api/applications/by-opp/99999/status",
        json={"status": "applied"},
    )
    assert r.status_code == 404


def test_status_by_opp_rejects_unknown_bucket(harness):
    client, t = harness
    opp, _ = _seed(t, "Sel")
    r = client.post(
        f"/api/applications/by-opp/{opp}/status",
        json={"status": "in_a_meeting"},
    )
    assert r.status_code in (400, 422)


# --- Manual job entry (Spec FIX-2 TASK 9) ------------------------

def test_add_job_manual_mode(harness):
    client, t = harness
    r = client.post(
        "/api/applications/add",
        json={
            "title": "Project Manager",
            "employer": "Acme Corp",
            "location": "Springfield, ST",
            "posting_text": "Lead delivery, manage stakeholders.",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["was_new"] is True
    assert body["title"] == "Project Manager"
    assert body["employer"] == "Acme Corp"
    assert body["status"] == "shortlisted"
    # Lands in the Selected column of the board.
    board = client.get("/api/applications/board").json()
    sel_ids = [
        c["opportunity_id"] for c in board["columns"]["selected"]
    ]
    assert body["opportunity_id"] in sel_ids
    # Opportunity row carries status='shortlisted'.
    opp = t.get_opportunity_by_id(body["opportunity_id"])
    assert opp["status"] == "shortlisted"


def test_add_job_manual_missing_required_fields(harness):
    client, _ = harness
    r = client.post(
        "/api/applications/add",
        json={"employer": "Acme Corp"},  # no title
    )
    assert r.status_code == 400


def test_add_job_url_mode(harness, monkeypatch):
    client, t = harness
    from scripts import manual_entry

    monkeypatch.setattr(
        manual_entry, "fetch_page",
        lambda url: ("Senior PM — Globex Careers", "Big posting body."),
    )
    r = client.post(
        "/api/applications/add",
        json={"url": "https://careers.globex.com/jobs/123"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["was_new"] is True
    assert "Senior PM" in body["title"]
    assert body["source_url"] == "https://careers.globex.com/jobs/123"
    assert body["status"] == "shortlisted"


def test_add_job_url_mode_fetch_failure_returns_502(harness, monkeypatch):
    client, _ = harness
    from scripts import manual_entry

    monkeypatch.setattr(
        manual_entry, "fetch_page", lambda url: (None, ""),
    )
    r = client.post(
        "/api/applications/add",
        json={"url": "https://unreachable.example/jobs/1"},
    )
    assert r.status_code == 502


def test_add_job_duplicate_url_dedupes(harness, monkeypatch):
    client, t = harness
    from scripts import manual_entry

    monkeypatch.setattr(
        manual_entry, "fetch_page",
        lambda url: ("PM — Initech", "Body text."),
    )
    payload = {"url": "https://initech.com/jobs/77"}
    first = client.post("/api/applications/add", json=payload).json()
    second = client.post("/api/applications/add", json=payload).json()
    assert first["was_new"] is True
    assert second["was_new"] is False
    assert first["opportunity_id"] == second["opportunity_id"]
