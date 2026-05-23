"""Tests for /api/email-monitor/* endpoints (Spec F1 TASK 3)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from dashboard.backend.app import create_app  # noqa: E402
from dashboard.backend.deps import (  # noqa: E402
    get_profile_id, get_tracker,
)
from dashboard.backend.routes import email_monitor as email_route  # noqa: E402
from engine.persistence.tracker import Tracker  # noqa: E402


@pytest.fixture
def harness(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    tracker = Tracker(profile_id="testprof", db_path=db)
    # Redirect credentials/token lookups to tmp_path.
    monkeypatch.setattr(
        email_route, "PROJECT_ROOT", tmp_path,
    )
    # Create per-profile data dir so token_path parent exists for writes.
    (tmp_path / "data" / "testprof").mkdir(parents=True, exist_ok=True)
    app = create_app()

    def _override_tracker():
        yield tracker

    def _override_profile():
        return "testprof"

    app.dependency_overrides[get_tracker] = _override_tracker
    app.dependency_overrides[get_profile_id] = _override_profile
    yield TestClient(app), tracker, tmp_path
    tracker.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_email_posting(t, employer, title, payload=None):
    cid = t.upsert_company(employer)
    oid, _ = t.insert_opportunity(
        company_id=cid, source="email_monitor",
        source_url=f"https://x/{employer}/{title}",
        title=title, location="Toronto", posting_text="...",
    )
    if payload is not None:
        t._execute(
            "UPDATE opportunities SET raw_payload = ? WHERE id = ?",
            (json.dumps(payload), oid),
        )
    return oid


def test_email_status_returns_200(harness):
    client, _, _ = harness
    r = client.get("/api/email-monitor/status")
    assert r.status_code == 200
    body = r.json()
    assert "configured" in body
    assert "last_check" in body
    assert "emails_checked_24h" in body
    assert "postings_parsed_24h" in body
    assert "parser_stats" in body


def test_email_status_reflects_configured(harness):
    client, _, tmp_path = harness
    # Drop a fake token to flip "configured" to True.
    token = tmp_path / "data" / "testprof" / "gmail_token.json"
    token.write_text(json.dumps({"refresh_token": "x"}))
    body = client.get("/api/email-monitor/status").json()
    assert body["configured"] is True


def test_email_status_reports_parsed_24h(harness):
    client, t, _ = harness
    _seed_email_posting(
        t, "ATCO", "PM Role",
        payload={"parser": "recruiter", "email_subject": "PM at ATCO"},
    )
    body = client.get("/api/email-monitor/status").json()
    assert body["postings_parsed_24h"] == 1
    assert "recruiter" in body["parser_stats"]
    assert body["parser_stats"]["recruiter"]["parsed"] == 1


def test_recent_returns_list(harness):
    client, t, _ = harness
    _seed_email_posting(
        t, "ATCO", "Senior Project Manager",
        payload={
            "parser": "recruiter",
            "email_subject": "Senior PM at ATCO",
            "from_email": "recruiter@hays.com",
            "email_date": _now_iso(),
        },
    )
    r = client.get("/api/email-monitor/recent")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["email_subject"] == "Senior PM at ATCO"
    assert body[0]["sender"] == "recruiter@hays.com"
    assert body[0]["parser_used"] == "recruiter"
    assert body[0]["postings_extracted"] == 1
    assert "Senior Project Manager" in body[0]["posting_titles"]


def test_check_now_returns_started_when_not_configured(harness):
    client, _, _ = harness
    r = client.post("/api/email-monitor/check-now")
    assert r.status_code == 200
    body = r.json()
    # No token → graceful error, not 500.
    assert body["status"] == "error"
    assert "Gmail not configured" in (body.get("error") or "")


def test_setup_status_unconfigured(harness):
    client, _, _ = harness
    r = client.get("/api/email-monitor/setup-status")
    assert r.status_code == 200
    body = r.json()
    assert body["credentials_exist"] is False
    assert body["token_valid"] is False
    assert body["setup_guide_url"].endswith("/setup-guide")


def test_setup_status_when_configured(harness):
    client, _, tmp_path = harness
    creds = tmp_path / "data" / "testprof" / "gmail_credentials.json"
    creds.write_text(json.dumps({"installed": {}}))
    token = tmp_path / "data" / "testprof" / "gmail_token.json"
    token.write_text(json.dumps({"refresh_token": "x"}))
    body = client.get("/api/email-monitor/setup-status").json()
    assert body["credentials_exist"] is True
    assert body["token_valid"] is True


def test_setup_guide_returns_markdown(harness):
    client, _, _ = harness
    r = client.get("/api/email-monitor/setup-guide")
    assert r.status_code == 200
    body = r.json()
    assert "markdown" in body
    assert "Gmail" in body["markdown"]
    assert "gmail_auth.py" in body["markdown"]
