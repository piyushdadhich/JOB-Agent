"""Spec 13 TASK 3 — explain-score endpoint tests."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from dashboard.backend import app as app_module
from dashboard.backend.deps import get_tracker
from engine.persistence.tracker import Tracker


def _seed_scored(tracker: Tracker) -> int:
    cid = tracker.upsert_company("Co")
    opp_id, _ = tracker.insert_opportunity(
        company_id=cid, source="t",
        source_url="https://example.com/x", title="PM",
    )
    tracker._execute(
        "INSERT INTO match_scores "
        "(opportunity_id, scorer_version, overlap_count, "
        " posting_skill_count, inventory_skill_count, "
        " coverage_raw, coverage_idf, overlap_skill_ids, "
        " missed_skill_ids, bucket, scored_at) "
        "VALUES (?, 'v1', 2, 5, 100, 0.4, 0.55, ?, ?, 'high', "
        " '2026-01-01T00:00:00+00:00')",
        (opp_id,
         json.dumps(["KS123", "KS456"]),
         json.dumps(["KS789", "KS999", "KS111"])),
    )
    for sid, label in [
        ("KS123", "Project Management"),
        ("KS456", "Agile"),
        ("KS789", "Java"),
        ("KS999", "React"),
        # KS111 intentionally missing from skill_labels — explain
        # endpoint should fall back to the bare ID.
    ]:
        tracker._execute(
            "INSERT INTO skill_labels (skill_id, label, taxonomy) "
            "VALUES (?, ?, 'lightcast')",
            (sid, label),
        )
    return opp_id


@pytest.fixture
def client(tmp_path):
    db = tmp_path / "t.db"
    t = Tracker(profile_id="default", db_path=db)
    opp_id = _seed_scored(t)
    t.close()

    def _ovr():
        tt = Tracker(profile_id="default", db_path=db)
        try:
            yield tt
        finally:
            tt.close()

    app_module.FRONTEND_DIST = tmp_path / "nope"
    app = app_module.create_app()
    app.dependency_overrides[get_tracker] = _ovr
    c = TestClient(app)
    c.seeded_opportunity_id = opp_id
    return c


def test_explain_returns_matched_missed_and_coverage(client):
    r = client.get(f"/api/shortlist/{client.seeded_opportunity_id}/explain")
    assert r.status_code == 200
    body = r.json()
    assert set(body["matched_skills"]) == {"Project Management", "Agile"}
    # KS111 has no label row → falls back to the raw id.
    assert "KS111" in body["missed_skills"]
    assert "Java" in body["missed_skills"]
    # 2 matched / 5 total = 40%
    assert body["coverage_pct"] == 40.0
    # coverage prefers idf when available.
    assert body["coverage"] == 0.55


def test_explain_404s_for_unknown_posting(client):
    r = client.get("/api/shortlist/99999/explain")
    assert r.status_code == 404


def test_explain_returns_empty_for_unscored(client, tmp_path):
    # Seed a fresh opportunity with no match_scores row.
    from dashboard.backend.deps import get_tracker
    from engine.persistence.tracker import Tracker as T
    db = tmp_path / "unscored.db"
    t = T(profile_id="default", db_path=db)
    cid = t.upsert_company("Co")
    opp_id, _ = t.insert_opportunity(
        company_id=cid, source="t",
        source_url="https://example.com/u", title="PM",
    )
    t.close()

    def _ovr():
        tt = T(profile_id="default", db_path=db)
        try:
            yield tt
        finally:
            tt.close()

    app_module.FRONTEND_DIST = tmp_path / "nope2"
    app = app_module.create_app()
    app.dependency_overrides[get_tracker] = _ovr
    c = TestClient(app)
    r = c.get(f"/api/shortlist/{opp_id}/explain")
    assert r.status_code == 200
    body = r.json()
    assert body["matched_skills"] == []
    assert body["missed_skills"] == []
