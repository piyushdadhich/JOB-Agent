"""Tests for the new Spec-2 batch endpoints:
  POST /api/prompts/batch/preview
  POST /api/apply/batch         + GET .../next + POST .../advance
"""
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


def _add(t, employer, title, url):
    cid = t.upsert_company(employer)
    opp_id, _ = t.insert_opportunity(
        company_id=cid, source="manual_entry",
        source_url=url, title=title,
        location="Toronto", posting_text="x",
    )
    return opp_id


def _set_status(t, opp_id, status):
    """Create an application for `opp_id` and bring it to `status`."""
    app_id = t.create_application(
        opportunity_id=opp_id, resume_variant="r.docx",
    )
    if status != "drafted":
        t.update_application_status(app_id, status)
    return app_id


# --- POST /api/prompts/batch/preview -----------------------------

def test_preview_returns_assembled_text_with_markers(harness):
    client, t = harness
    a = _add(t, "Acme", "Project Manager", "https://x/1")
    b = _add(t, "BCorp", "Scrum Master", "https://x/2")
    r = client.post(
        "/api/prompts/batch/preview",
        json={"opportunity_ids": [a, b]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert body["ids"] == [a, b]
    assert "═══ JOB 1 OF 2: Acme — Project Manager" in body["batch_prompt"]
    assert "═══ JOB 2 OF 2: BCorp — Scrum Master" in body["batch_prompt"]
    assert "═══ END ═══" in body["batch_prompt"]
    assert "--- RESUME ---" in body["batch_prompt"]
    assert "--- COVER LETTER ---" in body["batch_prompt"]


def test_preview_empty_list_400(harness):
    client, _ = harness
    r = client.post(
        "/api/prompts/batch/preview", json={"opportunity_ids": []},
    )
    assert r.status_code == 400


def test_preview_unknown_id_404(harness):
    client, t = harness
    a = _add(t, "Acme", "PM", "https://x/1")
    r = client.post(
        "/api/prompts/batch/preview",
        json={"opportunity_ids": [a, 99999]},
    )
    assert r.status_code == 404
    assert "99999" in r.json()["detail"]


# --- POST /api/apply/batch start ---------------------------------

def test_apply_batch_start_returns_id_and_total(harness):
    client, t = harness
    ids = [
        _add(t, "A", "PM", "https://x/1"),
        _add(t, "B", "PM", "https://x/2"),
        _add(t, "C", "PM", "https://x/3"),
    ]
    r = client.post(
        "/api/apply/batch", json={"opportunity_ids": ids},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert body["batch_id"]


def test_apply_batch_start_empty_400(harness):
    client, _ = harness
    r = client.post(
        "/api/apply/batch", json={"opportunity_ids": []},
    )
    assert r.status_code == 400


def test_apply_batch_start_unknown_id_404(harness):
    client, _ = harness
    r = client.post(
        "/api/apply/batch", json={"opportunity_ids": [99999]},
    )
    assert r.status_code == 404


# --- GET /next + POST /advance walkthrough -----------------------

def test_apply_batch_next_returns_first(harness):
    client, t = harness
    ids = [
        _add(t, "A", "PM-1", "https://x/1"),
        _add(t, "B", "PM-2", "https://x/2"),
    ]
    batch_id = client.post(
        "/api/apply/batch", json={"opportunity_ids": ids},
    ).json()["batch_id"]

    body = client.get(f"/api/apply/batch/{batch_id}/next").json()
    assert body["done"] is False
    assert body["opportunity_id"] == ids[0]
    assert body["position"] == 1
    assert body["total"] == 2


def test_apply_batch_advance_sequence_until_done(harness):
    client, t = harness
    ids = [
        _add(t, "A", "PM-1", "https://x/1"),
        _add(t, "B", "PM-2", "https://x/2"),
        _add(t, "C", "PM-3", "https://x/3"),
    ]
    batch_id = client.post(
        "/api/apply/batch", json={"opportunity_ids": ids},
    ).json()["batch_id"]

    # First /next -> posting 0
    r0 = client.get(f"/api/apply/batch/{batch_id}/next").json()
    assert r0["opportunity_id"] == ids[0]

    # advance applied -> posting 1
    r1 = client.post(
        f"/api/apply/batch/{batch_id}/advance",
        json={"action": "applied"},
    ).json()
    assert r1["opportunity_id"] == ids[1]
    assert r1["applied"] == 1
    assert r1["skipped"] == 0

    # advance skipped -> posting 2
    r2 = client.post(
        f"/api/apply/batch/{batch_id}/advance",
        json={"action": "skipped"},
    ).json()
    assert r2["opportunity_id"] == ids[2]
    assert r2["applied"] == 1
    assert r2["skipped"] == 1

    # advance applied -> done
    r3 = client.post(
        f"/api/apply/batch/{batch_id}/advance",
        json={"action": "applied"},
    ).json()
    assert r3["done"] is True
    assert r3["applied"] == 2
    assert r3["skipped"] == 1
    assert r3["total"] == 3


def test_apply_batch_invalid_action_400(harness):
    client, t = harness
    a = _add(t, "A", "PM", "https://x/1")
    batch_id = client.post(
        "/api/apply/batch", json={"opportunity_ids": [a]},
    ).json()["batch_id"]
    r = client.post(
        f"/api/apply/batch/{batch_id}/advance",
        json={"action": "garbage"},
    )
    assert r.status_code == 400


def test_apply_batch_unknown_batch_404(harness):
    client, _ = harness
    r = client.get("/api/apply/batch/no-such-id/next")
    assert r.status_code == 404
    assert "Shortlist" in r.json()["detail"]


def test_apply_batch_auto_skips_already_applied(harness):
    """A posting whose latest application is at submitted+ status is
    auto-skipped (counted as skipped, with a note). The queue walks
    forward to the next pending posting in the same /next call."""
    client, t = harness
    a = _add(t, "A", "PM", "https://x/1")
    b = _add(t, "B", "PM", "https://x/2")
    c = _add(t, "C", "PM", "https://x/3")
    _set_status(t, b, "submitted")  # B already applied -> auto-skip

    batch_id = client.post(
        "/api/apply/batch", json={"opportunity_ids": [a, b, c]},
    ).json()["batch_id"]

    r0 = client.get(f"/api/apply/batch/{batch_id}/next").json()
    assert r0["opportunity_id"] == a

    r1 = client.post(
        f"/api/apply/batch/{batch_id}/advance",
        json={"action": "applied"},
    ).json()
    # b was auto-skipped during the next-response walk; we land on c.
    assert r1["opportunity_id"] == c
    assert r1["skipped"] == 1
    assert any("auto-skipped" in n for n in r1["notes"])

    r2 = client.post(
        f"/api/apply/batch/{batch_id}/advance",
        json={"action": "applied"},
    ).json()
    assert r2["done"] is True
    assert r2["applied"] == 2
    assert r2["skipped"] == 1
