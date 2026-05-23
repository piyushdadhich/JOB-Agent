"""Spec 1 — onboarding wizard backend route tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard.backend import app as app_module
from dashboard.backend.routes import setup as setup_routes


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Redirect the state-file location into tmp_path so each test
    # starts fresh and never touches the real data/ tree.
    monkeypatch.setattr(setup_routes, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        app_module, "FRONTEND_DIST",
        tmp_path / "frontend_dist_does_not_exist",
    )
    app = app_module.create_app()
    return TestClient(app)


def test_status_defaults_to_step_1_not_completed(client):
    r = client.get("/api/setup/status")
    assert r.status_code == 200
    body = r.json()
    assert body["completed"] is False
    assert body["current_step"] == 1
    assert body["total_steps"] == 8


def test_save_step_advances_current_step(client):
    r = client.post("/api/setup/step/1", json={"tier": "recommended"})
    assert r.status_code == 200
    assert r.json() == {"current_step": 2, "completed": False}

    # Status should reflect the new cursor.
    s = client.get("/api/setup/status").json()
    assert s["current_step"] == 2


def test_save_step_persists_data(client, tmp_path):
    client.post("/api/setup/step/1", json={"tier": "full"})
    state_file = tmp_path / "data" / "default" / "setup_state.json"
    assert state_file.exists()
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["steps"]["1"] == {"tier": "full"}
    assert state["current_step"] == 2


def test_save_step_does_not_rewind_cursor(client):
    # Skip ahead to step 5.
    client.post("/api/setup/step/4", json={"x": 1})
    assert client.get("/api/setup/status").json()["current_step"] == 5
    # Revisiting step 1 should NOT move the cursor backwards.
    client.post("/api/setup/step/1", json={"tier": "minimum"})
    assert client.get("/api/setup/status").json()["current_step"] == 5


def test_save_step_completes_after_final(client):
    for n in range(1, 9):
        r = client.post(f"/api/setup/step/{n}", json={})
        assert r.status_code == 200
    final = r.json()
    assert final["completed"] is True
    assert final["current_step"] == 8


def test_save_step_rejects_out_of_range(client):
    assert client.post("/api/setup/step/0", json={}).status_code == 400
    assert client.post("/api/setup/step/9", json={}).status_code == 400


def test_get_config_returns_full_state(client):
    client.post("/api/setup/step/1", json={"tier": "recommended"})
    client.post("/api/setup/step/2", json={"provider": "ollama"})
    cfg = client.get("/api/setup/config").json()
    assert cfg["completed"] is False
    assert cfg["current_step"] == 3
    assert cfg["steps"] == {
        "1": {"tier": "recommended"},
        "2": {"provider": "ollama"},
    }


def test_hardware_endpoint_returns_snapshot(client):
    r = client.get("/api/setup/hardware")
    assert r.status_code == 200
    body = r.json()
    # The real detect_hardware fires here; we check the shape and
    # the few invariants that hold on any host.
    assert body["cpu_cores"] >= 1
    assert body["ram_total_gb"] > 0
    assert body["tier"] in ("minimum", "recommended", "full")


def test_complete_endpoint_marks_done(client):
    r = client.post("/api/setup/complete", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["completed"] is True
    assert body["current_step"] == 8


def test_corrupt_state_file_is_recovered(client, tmp_path):
    state_file = tmp_path / "data" / "default" / "setup_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text("{ not valid json", encoding="utf-8")
    # Should treat corrupt state as fresh, not 500.
    r = client.get("/api/setup/status")
    assert r.status_code == 200
    assert r.json() == {
        "completed": False, "current_step": 1, "total_steps": 8,
    }
