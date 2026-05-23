"""Spec 1 TASKS 8+9 — schedule / first-run tests."""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from dashboard.backend import app as app_module
from dashboard.backend.routes import setup as setup_routes


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_routes, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        app_module, "FRONTEND_DIST",
        tmp_path / "frontend_dist_does_not_exist",
    )
    # Reset the module-level first-run state so tests don't pollute
    # each other.
    setup_routes._FIRST_RUN_STATE.clear()
    return TestClient(app_module.create_app())


# --- TASK 8: install-service ----------------------------------

def test_install_service_routes_to_jobagent(client):
    with patch("jobagent.services.install_service") as m:
        r = client.post(
            "/api/setup/install-service",
            json={"schedule_time": "03:30"},
        )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "detail": None}
    m.assert_called_once_with(
        schedule_time="03:30", profile="default",
    )


def test_install_service_surfaces_servicerror(client):
    from jobagent.services import ServiceError
    with patch(
        "jobagent.services.install_service",
        side_effect=ServiceError("schtasks failed"),
    ):
        r = client.post(
            "/api/setup/install-service",
            json={"schedule_time": "02:00"},
        )
    body = r.json()
    assert body["ok"] is False
    assert "schtasks failed" in body["detail"]


# --- TASK 9: first-run ---------------------------------------

def test_first_run_progress_idle_before_start(client):
    r = client.get("/api/setup/first-run/progress").json()
    assert r["status"] == "idle"
    assert r["stages"] == []
    assert r["summary"] is None


def test_first_run_starts_background_thread(client):
    # Mock run_daily.main so the test doesn't actually run the
    # discovery pipeline. The endpoint should kick off the thread,
    # the thread should call run_daily.main(), and progress should
    # transition to "done".
    with patch("scripts.run_daily.main", return_value=0) as m:
        r = client.post("/api/setup/first-run").json()
        assert r["started"] is True
        # Poll briefly for the background thread to flip to done.
        for _ in range(20):
            time.sleep(0.05)
            snap = client.get("/api/setup/first-run/progress").json()
            if snap["status"] == "done":
                break
        assert snap["status"] == "done"
        assert snap["summary"]["rc"] == 0
    m.assert_called_once()


def test_first_run_handles_failure(client):
    with patch("scripts.run_daily.main", return_value=5):
        client.post("/api/setup/first-run")
        for _ in range(20):
            time.sleep(0.05)
            snap = client.get("/api/setup/first-run/progress").json()
            if snap["status"] == "failed":
                break
    assert snap["status"] == "failed"
    assert snap["summary"]["rc"] == 5


def test_first_run_does_not_relaunch_when_running(client):
    # Force the in-memory state into "running" without actually
    # starting a thread; subsequent POST should be a no-op.
    setup_routes._set_first_run("default", status="running", stages=[])
    r = client.post("/api/setup/first-run").json()
    assert r == {"started": False, "reason": "already running"}
