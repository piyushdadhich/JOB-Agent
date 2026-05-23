"""Tests for /api/expansion/* endpoints (Spec F1 TASK 1)."""
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
from dashboard.backend.routes import expansion as expansion_route  # noqa: E402
from engine.persistence.tracker import Tracker  # noqa: E402


@pytest.fixture
def harness(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    tracker = Tracker(profile_id="testprof", db_path=db)
    # Redirect overlay file to the per-test tmp_path so tests don't
    # collide with scripts/output state.
    monkeypatch.setattr(
        expansion_route, "_STATE_DIR", tmp_path / "out",
    )
    app = create_app()

    def _override_tracker():
        yield tracker

    def _override_profile():
        return "testprof"

    app.dependency_overrides[get_tracker] = _override_tracker
    app.dependency_overrides[get_profile_id] = _override_profile
    yield TestClient(app), tracker, tmp_path
    tracker.close()


def _now_iso(offset_minutes: int = 0) -> str:
    from datetime import timedelta
    return (
        datetime.now(timezone.utc)
        + timedelta(minutes=offset_minutes)
    ).isoformat()


def _seed_strong_posting(t, employer, title, skill_ids, tier="STRONG"):
    cid = t.upsert_company(employer)
    oid, _ = t.insert_opportunity(
        company_id=cid, source="workday",
        source_url=f"https://x/{employer}/{title}",
        title=title, location="Toronto", posting_text="...",
    )
    t._execute(
        "UPDATE opportunities SET extracted_skill_ids = ? WHERE id = ?",
        (json.dumps(skill_ids), oid),
    )
    t._execute(
        "INSERT INTO eval_decisions "
        "(opportunity_id, evaluator_version, tier, fit_score, "
        " stage_trace, reasoning, evaluated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (oid, "test-v1", tier, 8, "[]", "x", _now_iso()),
    )
    return cid, oid


def _seed_three_similar(t):
    """Three STRONG postings with overlapping skill ids → one cluster."""
    skills = ["S1", "S2", "S3", "S4", "S5"]
    _seed_strong_posting(t, "ATCO", "Land Acquisition Officer", skills)
    _seed_strong_posting(t, "AltaLink", "Land Acquisition Officer", skills)
    _seed_strong_posting(t, "TestCo", "Land Acquisition Officer", skills)


def test_expansion_summary_returns_200(harness):
    client, _, _ = harness
    r = client.get("/api/expansion/summary")
    assert r.status_code == 200
    body = r.json()
    assert "days_analyzed" in body
    assert "title_clusters_count" in body
    assert "deep_targets_count" in body
    assert "skill_gaps_count" in body


def test_title_clusters_returns_list(harness):
    client, t, _ = harness
    _seed_three_similar(t)
    r = client.get("/api/expansion/title-clusters")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) >= 1
    c = body[0]
    assert c["modal_title"] == "Land Acquisition Officer"
    assert c["posting_count"] == 3
    assert c["status"] == "pending"
    assert "id" in c


def test_deep_targets_returns_list(harness):
    client, t, _ = harness
    # 2 STRONG postings at one employer → deep target
    skills = ["S1", "S2", "S3"]
    cid, _ = _seed_strong_posting(t, "ATCO", "PM", skills)
    _seed_strong_posting(t, "ATCO", "Senior PM", skills, tier="TOP_TIER")
    r = client.get("/api/expansion/deep-targets")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    targets = [d for d in body if d["company_id"] == cid]
    assert len(targets) == 1
    assert targets[0]["strong_count"] == 1
    assert targets[0]["top_tier_count"] == 1
    assert targets[0]["is_watched"] is False


def test_skill_gaps_returns_list(harness):
    client, t, _ = harness
    # 3 EXPLORATORY postings sharing a skill not in any inventory.
    gap_skill = "KS_GAP"
    for emp in ("E1", "E2", "E3"):
        _seed_strong_posting(
            t, emp, "Some Role",
            [gap_skill, "OTHER1", "OTHER2"], tier="EXPLORATORY",
        )
    r = client.get("/api/expansion/skill-gaps")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    found = [g for g in body if g["skill_id"] == gap_skill]
    assert len(found) == 1
    assert found[0]["occurrence_count"] == 3
    assert found[0]["action"] is None


def test_confirm_cluster_updates_status(harness):
    client, t, _ = harness
    _seed_three_similar(t)
    clusters = client.get("/api/expansion/title-clusters").json()
    cid = clusters[0]["id"]
    r = client.post(f"/api/expansion/title-clusters/{cid}/confirm")
    assert r.status_code == 200
    assert r.json()["status"] == "confirmed"
    # Persistence: re-GET should still show confirmed.
    again = client.get("/api/expansion/title-clusters").json()
    match = [c for c in again if c["id"] == cid]
    assert match and match[0]["status"] == "confirmed"


def test_reject_cluster_updates_status(harness):
    client, t, _ = harness
    _seed_three_similar(t)
    clusters = client.get("/api/expansion/title-clusters").json()
    cid = clusters[0]["id"]
    r = client.post(f"/api/expansion/title-clusters/{cid}/reject")
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"


def test_watch_deep_target(harness):
    client, t, _ = harness
    skills = ["S1", "S2", "S3"]
    cid, _ = _seed_strong_posting(t, "ATCO", "PM", skills)
    _seed_strong_posting(t, "ATCO", "Senior PM", skills, tier="TOP_TIER")
    # prime the report cache
    client.get("/api/expansion/deep-targets")

    r = client.post(f"/api/expansion/deep-targets/{cid}/watch")
    assert r.status_code == 200
    assert r.json()["is_watched"] is True
    # Verify the persisted is_deep_target via fresh GET.
    rows = client.get("/api/expansion/deep-targets").json()
    targets = [d for d in rows if d["company_id"] == cid]
    assert targets and targets[0]["is_watched"] is True

    # Unwatch round-trip.
    r2 = client.post(f"/api/expansion/deep-targets/{cid}/unwatch")
    assert r2.status_code == 200
    assert r2.json()["is_watched"] is False


def test_run_expansion_returns_started(harness):
    client, _, _ = harness
    r = client.post("/api/expansion/run")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "started"
    assert "message" in body


def test_skill_gap_action_persists(harness):
    client, t, _ = harness
    gap_skill = "KS_GAP_A"
    for emp in ("E1", "E2", "E3"):
        _seed_strong_posting(
            t, emp, "Some Role",
            [gap_skill, "OTHER1", "OTHER2"], tier="EXPLORATORY",
        )
    client.get("/api/expansion/skill-gaps")  # prime
    r = client.post(
        f"/api/expansion/skill-gaps/{gap_skill}/action",
        json={"action": "in_inventory"},
    )
    assert r.status_code == 200
    assert r.json()["action"] == "in_inventory"
    rows = client.get("/api/expansion/skill-gaps").json()
    match = [g for g in rows if g["skill_id"] == gap_skill]
    assert match and match[0]["action"] == "in_inventory"


def test_cluster_not_found_returns_404(harness):
    client, _, _ = harness
    r = client.post(
        "/api/expansion/title-clusters/no-such-cluster/confirm",
    )
    assert r.status_code == 404
