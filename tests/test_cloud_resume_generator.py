"""Tests for the cloud resume/cover-letter generator + endpoints."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from dashboard.backend.app import create_app  # noqa: E402
from dashboard.backend.deps import get_profile_id, get_tracker  # noqa: E402
from engine.persistence.tracker import Tracker  # noqa: E402
from engine.resume.cloud_generator import CloudResumeGenerator  # noqa: E402


class FakeCloudClient:
    """Stand-in for GemmaCloudClient — records prompts, no network."""

    def __init__(self):
        self.prompts: list[str] = []

    def generate(self, prompt, system=None, temperature=0.4):
        self.prompts.append(prompt)
        return "# Generated Document\n\nSome generated content."


class FakeGenerator:
    """Stand-in for CloudResumeGenerator used in endpoint tests."""

    def __init__(self, profile_id="default", **_):
        self.profile_id = profile_id
        self.calls: list[tuple] = []

    def generate(self, prompt, *, call_type="resume",
                 opportunity_id=None, temperature=0.7):
        self.calls.append((call_type, opportunity_id))
        return f"# {call_type.title()}\n\nGenerated body for review."


# --- CloudResumeGenerator unit tests -----------------------------

def test_cloud_generator_builds_from_prompt(tmp_path):
    fake = FakeCloudClient()
    gen = CloudResumeGenerator(profile_id="p", client=fake)
    gen.usage_log = tmp_path / "usage.jsonl"
    out = gen.generate("Build a resume for X", call_type="resume")
    assert "Generated Document" in out
    assert fake.prompts == ["Build a resume for X"]
    # Usage line written + tagged with call_type.
    assert gen.usage_log.exists()
    import json
    entry = json.loads(gen.usage_log.read_text().strip())
    assert entry["call_type"] == "resume"


def test_cloud_generator_rate_limits(tmp_path):
    interval = 0.5
    fake = FakeCloudClient()
    gen = CloudResumeGenerator(
        profile_id="p", client=fake, min_interval=interval,
    )
    gen.usage_log = tmp_path / "usage.jsonl"
    start = time.monotonic()
    gen.generate("a", call_type="resume")
    gen.generate("b", call_type="cover_letter")
    elapsed = time.monotonic() - start
    # Second call must wait out the min_interval (allow a small
    # margin for monotonic-clock granularity on Windows).
    assert elapsed >= interval * 0.9


# --- Endpoint tests ----------------------------------------------

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
    yield TestClient(app), tracker, tmp_path
    tracker.close()


def _seed(t, employer="TD", *, selected=True):
    cid = t.upsert_company(employer)
    oid, _ = t.insert_opportunity(
        company_id=cid, source="manual",
        source_url=f"https://x/{employer}", title="Project Manager",
        location="Springfield", posting_text="Lead delivery.",
    )
    t._execute(
        "INSERT INTO eval_decisions "
        "(opportunity_id, evaluator_version, tier, fit_score, "
        " stage_trace, reasoning, evaluated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (oid, "gemma-4-31b", "STRONG", 8, "[]", "x",
         "2026-05-01T10:00:00Z"),
    )
    aid = t.create_application(
        opportunity_id=oid, resume_variant="dashboard_pending",
    )
    if selected:
        t.update_application_fields(
            aid, selected_at="2026-05-01T10:00:00Z",
        )
    t.update_opportunity_status(oid, "shortlisted")
    return oid


def test_generate_endpoint_returns_markdown(harness, monkeypatch):
    client, t, _ = harness
    import engine.resume.cloud_generator as cg
    monkeypatch.setattr(cg, "CloudResumeGenerator", FakeGenerator)

    opp = _seed(t, "EllisDon")
    r = client.post(
        f"/api/applications/by-opp/{opp}/generate",
        json={"type": "resume"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["type"] == "resume"
    assert "Resume" in body["markdown"]
    assert body["saved"] is True


def test_generate_endpoint_saves_docx(harness, monkeypatch):
    client, t, _ = harness
    import engine.resume.cloud_generator as cg
    monkeypatch.setattr(cg, "CloudResumeGenerator", FakeGenerator)

    opp = _seed(t, "BMO")
    r = client.post(
        f"/api/applications/by-opp/{opp}/generate",
        json={"type": "resume"},
    )
    assert r.status_code == 200, r.text
    # The markdown is persisted to the application row.
    row = t._query_one(
        "SELECT resume_text FROM applications WHERE opportunity_id = ?",
        (opp,),
    )
    assert row["resume_text"]


def test_generate_endpoint_404_for_unknown_opportunity(harness):
    client, _, _ = harness
    r = client.post(
        "/api/applications/by-opp/99999/generate",
        json={"type": "resume"},
    )
    assert r.status_code == 404


def test_batch_generate_creates_task(harness, monkeypatch):
    client, t, tmp_path = harness
    import engine.resume.cloud_generator as cg
    import dashboard.backend.routes.applications_board as ab

    monkeypatch.setattr(cg, "CloudResumeGenerator", FakeGenerator)
    # Batch worker gets a fresh connection to the SAME temp DB file.
    db_path = t.db_path
    monkeypatch.setattr(
        ab, "_batch_tracker",
        lambda pid: Tracker(profile_id="p", db_path=db_path),
    )
    monkeypatch.setattr(
        ab, "_batch_generator", lambda pid: FakeGenerator(pid),
    )

    _seed(t, "Mastercard")
    _seed(t, "Sanofi")
    r = client.post(
        "/api/applications/generate-all",
        json={"types": ["resume", "cover_letter"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert body["task_id"]


def test_batch_generate_progress_polling(harness, monkeypatch):
    client, t, _ = harness
    import engine.resume.cloud_generator as cg
    import dashboard.backend.routes.applications_board as ab

    monkeypatch.setattr(cg, "CloudResumeGenerator", FakeGenerator)
    db_path = t.db_path
    monkeypatch.setattr(
        ab, "_batch_tracker",
        lambda pid: Tracker(profile_id="p", db_path=db_path),
    )
    monkeypatch.setattr(
        ab, "_batch_generator", lambda pid: FakeGenerator(pid),
    )

    _seed(t, "PwC")
    start = client.post(
        "/api/applications/generate-all",
        json={"types": ["resume"]},
    ).json()
    task_id = start["task_id"]

    # Poll until the worker finishes (fake generator is fast).
    deadline = time.monotonic() + 15
    status = "running"
    while time.monotonic() < deadline:
        prog = client.get(
            f"/api/applications/generate-all/{task_id}"
        ).json()
        status = prog["status"]
        if status != "running":
            break
        time.sleep(0.3)
    assert status == "done"
    assert prog["completed"] == prog["total"] == 1


def test_batch_generate_progress_404_unknown_task(harness):
    client, _, _ = harness
    r = client.get("/api/applications/generate-all/not-a-real-task")
    assert r.status_code == 404


def test_batch_generate_progress_includes_current_item(
    harness, monkeypatch,
):
    """The progress payload carries a current_opp_id field so the UI
    can show which posting is being generated."""
    client, t, _ = harness
    import engine.resume.cloud_generator as cg
    import dashboard.backend.routes.applications_board as ab

    monkeypatch.setattr(cg, "CloudResumeGenerator", FakeGenerator)
    db_path = t.db_path
    monkeypatch.setattr(
        ab, "_batch_tracker",
        lambda pid: Tracker(profile_id="p", db_path=db_path),
    )
    monkeypatch.setattr(
        ab, "_batch_generator", lambda pid: FakeGenerator(pid),
    )

    _seed(t, "Kinaxis")
    start = client.post(
        "/api/applications/generate-all",
        json={"types": ["resume"]},
    ).json()
    # Poll to completion — the field is present throughout.
    deadline = time.monotonic() + 15
    prog = None
    while time.monotonic() < deadline:
        prog = client.get(
            f"/api/applications/generate-all/{start['task_id']}"
        ).json()
        assert "current_opp_id" in prog
        if prog["status"] != "running":
            break
        time.sleep(0.3)
    assert prog["status"] == "done"
    # Cleared once the batch finishes.
    assert prog["current_opp_id"] is None


def test_batch_generate_skips_existing_without_force(harness, monkeypatch):
    """Default generate-all only counts postings missing docs."""
    client, t, _ = harness
    # One posting already has both docs; one is missing them.
    done_opp = _seed(t, "Done")
    t._execute(
        "UPDATE applications SET resume_text = 'R', "
        "cover_letter_text = 'C' WHERE opportunity_id = ?",
        (done_opp,),
    )
    _seed(t, "Pending")
    r = client.post(
        "/api/applications/generate-all",
        json={"types": ["resume", "cover_letter"]},
    ).json()
    assert r["total"] == 1  # only the pending one


def test_batch_generate_force_includes_existing(harness, monkeypatch):
    """force=true regenerates every shortlisted posting, even ones
    that already have docs."""
    client, t, _ = harness
    done_opp = _seed(t, "Done")
    t._execute(
        "UPDATE applications SET resume_text = 'R', "
        "cover_letter_text = 'C' WHERE opportunity_id = ?",
        (done_opp,),
    )
    _seed(t, "Pending")
    r = client.post(
        "/api/applications/generate-all",
        json={"types": ["resume", "cover_letter"], "force": True},
    ).json()
    assert r["total"] == 2  # both, including the already-done one
