"""Tests for /api/prompts/batch GET (combined prompt) and POST
(parsed combined response) -- Spec 4 batch copy-paste workflow."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
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


def _add_queued_posting(
    tracker: Tracker,
    employer: str,
    title: str,
    resume_prompt: str = "RESUME PROMPT TEXT",
    cl_prompt: str = "COVER LETTER PROMPT TEXT",
) -> int:
    """Create a posting + application in the needs_prompts queue with
    pre-stored prompts so batch GET doesn't fall back to PromptService."""
    cid = tracker.upsert_company(employer)
    oid, _ = tracker.insert_opportunity(
        company_id=cid, source="manual_entry",
        source_url=f"https://x/{employer}/{title}",
        title=title, location="Toronto",
        posting_text="Lead delivery.",
    )
    app_id = tracker.create_application(
        opportunity_id=oid, resume_variant="dashboard_pending",
    )
    tracker.update_application_fields(
        app_id,
        selected_at=datetime.now(timezone.utc).isoformat(),
        resume_prompt=resume_prompt,
        cover_letter_prompt=cl_prompt,
    )
    return oid


def test_batch_prompts_returns_combined_string(harness):
    client, t = harness
    oid_a = _add_queued_posting(
        t, "TD", "Senior PM",
        resume_prompt="A-RESUME", cl_prompt="A-CL",
    )
    oid_b = _add_queued_posting(
        t, "BMO", "Delivery Lead",
        resume_prompt="B-RESUME", cl_prompt="B-CL",
    )

    r = client.get("/api/prompts/batch")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert set(body["ids"]) == {oid_a, oid_b}
    assert "═══ JOB 1 OF 2:" in body["batch_prompt"]
    assert "═══ JOB 2 OF 2:" in body["batch_prompt"]
    assert "═══ END ═══" in body["batch_prompt"]
    assert "A-RESUME" in body["batch_prompt"]
    assert "A-CL" in body["batch_prompt"]
    assert "B-RESUME" in body["batch_prompt"]
    assert "B-CL" in body["batch_prompt"]
    assert "--- RESUME ---" in body["batch_prompt"]
    assert "--- COVER LETTER ---" in body["batch_prompt"]


def test_batch_prompts_empty_when_no_apps(harness):
    client, _ = harness
    r = client.get("/api/prompts/batch")
    assert r.status_code == 200
    assert r.json() == {"batch_prompt": "", "count": 0, "ids": []}


def test_batch_response_splits_by_job_separator(harness):
    client, t = harness
    oid_a = _add_queued_posting(t, "TD", "Senior PM")
    oid_b = _add_queued_posting(t, "BMO", "Delivery Lead")

    response_payload = (
        "═══ JOB 1 OF 2: TD — Senior PM ═══\n"
        "--- RESUME ---\n"
        "## ALEX FOR TD\nbody A\n\n"
        "--- COVER LETTER ---\n"
        "Dear TD,\nbody A cl\n\n"
        "═══ JOB 2 OF 2: BMO — Delivery Lead ═══\n"
        "--- RESUME ---\n"
        "## ALEX FOR BMO\nbody B\n\n"
        "--- COVER LETTER ---\n"
        "Dear BMO,\nbody B cl\n\n"
        "═══ END ═══\n"
    )
    r = client.post(
        "/api/prompts/batch",
        json={"response": response_payload, "ids": [oid_a, oid_b]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {"saved": 2, "total": 2}


def test_batch_response_splits_resume_and_cl(harness):
    client, t = harness
    oid = _add_queued_posting(t, "TD", "Senior PM")
    response_payload = (
        "═══ JOB 1 OF 1: TD — Senior PM ═══\n"
        "--- RESUME ---\n"
        "RESUME-MARKER-RESUME\n\n"
        "--- COVER LETTER ---\n"
        "CL-MARKER-CL\n\n"
        "═══ END ═══\n"
    )
    client.post(
        "/api/prompts/batch",
        json={"response": response_payload, "ids": [oid]},
    )

    row = t._query_one(
        "SELECT resume_text, cover_letter_text FROM applications "
        "WHERE opportunity_id = ?", (oid,),
    )
    assert "RESUME-MARKER-RESUME" in row["resume_text"]
    assert "CL-MARKER-CL" in row["cover_letter_text"]
    # END marker should NOT bleed into either field.
    assert "═══" not in row["resume_text"]
    assert "═══" not in row["cover_letter_text"]


def test_batch_response_saves_to_correct_postings(harness):
    """Job N maps to ids[N-1]; out-of-order job numbers respect the index."""
    client, t = harness
    oid_a = _add_queued_posting(t, "TD", "Senior PM")
    oid_b = _add_queued_posting(t, "BMO", "Delivery Lead")
    oid_c = _add_queued_posting(t, "CIBC", "Scrum Master")

    response_payload = (
        "═══ JOB 1 OF 3: TD — Senior PM ═══\n"
        "--- RESUME ---\nFOR-A\n\n--- COVER LETTER ---\nCL-A\n\n"
        "═══ JOB 2 OF 3: BMO — Delivery Lead ═══\n"
        "--- RESUME ---\nFOR-B\n\n--- COVER LETTER ---\nCL-B\n\n"
        "═══ JOB 3 OF 3: CIBC — Scrum Master ═══\n"
        "--- RESUME ---\nFOR-C\n\n--- COVER LETTER ---\nCL-C\n\n"
        "═══ END ═══"
    )
    client.post(
        "/api/prompts/batch",
        json={"response": response_payload,
              "ids": [oid_a, oid_b, oid_c]},
    )

    for oid, marker in [(oid_a, "FOR-A"), (oid_b, "FOR-B"), (oid_c, "FOR-C")]:
        row = t._query_one(
            "SELECT resume_text FROM applications "
            "WHERE opportunity_id = ?", (oid,),
        )
        assert marker in row["resume_text"], f"{marker} -> {oid}"


def test_batch_response_handles_partial_input(harness):
    """If only one of N jobs is in the response, only that one saves;
    the rest stay empty."""
    client, t = harness
    oid_a = _add_queued_posting(t, "TD", "Senior PM")
    oid_b = _add_queued_posting(t, "BMO", "Delivery Lead")

    # Only job 1 is in the paste; user got rate-limited mid-response.
    response_payload = (
        "═══ JOB 1 OF 2: TD — Senior PM ═══\n"
        "--- RESUME ---\nPARTIAL-RESUME\n\n"
        "--- COVER LETTER ---\nPARTIAL-CL\n"
    )
    r = client.post(
        "/api/prompts/batch",
        json={"response": response_payload,
              "ids": [oid_a, oid_b]},
    )
    body = r.json()
    assert body == {"saved": 1, "total": 2}

    row_a = t._query_one(
        "SELECT resume_text, cover_letter_text FROM applications "
        "WHERE opportunity_id = ?", (oid_a,),
    )
    row_b = t._query_one(
        "SELECT resume_text, cover_letter_text FROM applications "
        "WHERE opportunity_id = ?", (oid_b,),
    )
    assert "PARTIAL-RESUME" in row_a["resume_text"]
    assert "PARTIAL-CL" in row_a["cover_letter_text"]
    assert row_b["resume_text"] is None
    assert row_b["cover_letter_text"] is None
