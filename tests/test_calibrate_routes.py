"""Spec 6 — backend route tests."""
from __future__ import annotations

import yaml

import pytest
from fastapi.testclient import TestClient

from dashboard.backend import app as app_module
from dashboard.backend.routes import calibrate as calibrate_routes
from engine.persistence.tracker import Tracker


def _seed_eval_decisions(tracker: Tracker) -> None:
    # Insert 3 opportunities + eval_decisions with fit_scores
    # spread across the tier bands.
    company_id = tracker.upsert_company("Acme Corp")
    opp_a, _ = tracker.insert_opportunity(
        company_id=company_id, source="test",
        source_url="https://example.com/a", title="Senior PM",
    )
    opp_b, _ = tracker.insert_opportunity(
        company_id=company_id, source="test",
        source_url="https://example.com/b", title="Junior PM",
    )
    opp_c, _ = tracker.insert_opportunity(
        company_id=company_id, source="test",
        source_url="https://example.com/c", title="Director",
    )
    # Direct INSERT into eval_decisions; the higher-level recorder
    # has more constraints than this fixture needs.
    for opp_id, tier, score in [
        (opp_a, "TOP_TIER", 8),
        (opp_b, "EXPLORATORY", 2),
        (opp_c, "SKIP", 1),
    ]:
        tracker._execute(
            "INSERT INTO eval_decisions "
            "(opportunity_id, evaluator_version, tier, fit_score, "
            " stage_trace, evaluated_at) "
            "VALUES (?, 'test-1', ?, ?, '[]', '2026-01-01T00:00:00+00:00')",
            (opp_id, tier, score),
        )


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        calibrate_routes, "_project_root", lambda: tmp_path,
    )
    monkeypatch.setattr(
        app_module, "FRONTEND_DIST",
        tmp_path / "frontend_dist_does_not_exist",
    )
    # Per-request tracker fixture: shared in-memory test DB via the
    # tmp_path-named profile so the routes + the test see the same
    # rows.
    profile_dir = tmp_path / "data" / "default"
    profile_dir.mkdir(parents=True, exist_ok=True)
    db_path = profile_dir / "tracker.db"
    tr = Tracker(profile_id="default", db_path=db_path)
    _seed_eval_decisions(tr)
    tr.close()

    # Override get_tracker to point at our seeded DB.
    from dashboard.backend.deps import get_tracker

    def _tracker_override():
        t = Tracker(profile_id="default", db_path=db_path)
        try:
            yield t
        finally:
            t.close()

    app = app_module.create_app()
    app.dependency_overrides[get_tracker] = _tracker_override
    return TestClient(app)


# --- /auto -------------------------------------------------------

def test_auto_calibrate_endpoint_returns_thresholds(client):
    r = client.post("/api/calibrate/auto")
    assert r.status_code == 200
    body = r.json()
    assert "thresholds" in body
    t = body["thresholds"]
    assert "top" in t and "strong" in t and "exploratory" in t
    assert body["distribution"]["total"] == 3
    assert body["distribution"]["top_tier_count"] == 1


# --- /sample -----------------------------------------------------

def test_sample_endpoint_returns_top_and_bottom(client):
    r = client.get("/api/calibrate/sample")
    assert r.status_code == 200
    body = r.json()
    assert len(body["top"]) >= 1
    assert len(body["bottom"]) >= 1
    # Top sorted desc by fit_score; bottom asc. With our seed
    # (fit_score INTEGER 1..10 per schema): 8 > 2 > 1.
    assert body["top"][0]["fit_score"] == 8.0
    assert body["bottom"][0]["fit_score"] == 1.0


# --- /confirm ----------------------------------------------------

def test_confirm_endpoint_raises_when_top_rejected(client):
    r = client.post("/api/calibrate/confirm", json={
        "current": {"top": 4.0, "strong": 2.5, "exploratory": 1.0},
        "user_confirms_top": False,
        "user_confirms_bottom": True,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["thresholds"]["top"] == 4.5
    assert body["changed"] is True


def test_confirm_endpoint_lowers_when_bottom_rejected(client):
    r = client.post("/api/calibrate/confirm", json={
        "current": {"top": 4.0, "strong": 2.5, "exploratory": 1.0},
        "user_confirms_top": True,
        "user_confirms_bottom": False,
    })
    body = r.json()
    assert body["thresholds"]["top"] == 3.5
    assert body["changed"] is True


def test_confirm_endpoint_no_change_on_yes_yes(client):
    r = client.post("/api/calibrate/confirm", json={
        "current": {"top": 4.0, "strong": 2.5, "exploratory": 1.0},
        "user_confirms_top": True,
        "user_confirms_bottom": True,
    })
    body = r.json()
    assert body["changed"] is False
    assert body["thresholds"]["top"] == 4.0


# --- /apply ------------------------------------------------------

def test_apply_endpoint_writes_yaml_and_rebuckets(client, tmp_path):
    # Seed a profile yaml the apply endpoint can patch.
    yaml_path = tmp_path / "config" / "profiles" / "default.yaml"
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_path.write_text(
        yaml.safe_dump({"profile_id": "default", "display_name": "test"}),
        encoding="utf-8",
    )

    # Apply a tighter set: top=9.0 should bucket the score=8 row
    # down to STRONG (since 8 < 9 but 8 >= 5).
    r = client.post("/api/calibrate/apply", json={
        "thresholds": {"top": 9.0, "strong": 5.0, "exploratory": 1.0},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["profile_yaml"].endswith("default.yaml")
    assert body["rebucketed"] >= 3  # all seeded rows touched

    saved = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert saved["tier_thresholds"] == {
        "top": 9.0, "strong": 5.0, "exploratory": 1.0,
    }


def test_apply_endpoint_404s_when_profile_yaml_missing(client):
    r = client.post("/api/calibrate/apply", json={
        "thresholds": {"top": 4.0, "strong": 2.5, "exploratory": 1.0},
    })
    assert r.status_code == 404
    assert "profile yaml" in r.json()["detail"].lower()
