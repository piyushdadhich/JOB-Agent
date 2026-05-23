"""Spec 12 TASKS 2 + 3 — hardware-drift + fallback-history tests."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from dashboard.backend import app as app_module
from dashboard.backend.routes import system_health


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        system_health, "_project_root", lambda: tmp_path,
    )
    monkeypatch.setattr(
        app_module, "FRONTEND_DIST",
        tmp_path / "frontend_dist_does_not_exist",
    )
    return TestClient(app_module.create_app())


def _seed_setup_state(tmp_path, payload: dict) -> None:
    state_path = tmp_path / "data" / "default" / "setup_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({
            "completed": False, "current_step": 2,
            "steps": {"1": payload},
        }),
        encoding="utf-8",
    )


# --- hardware-check ---------------------------------------------

def test_hardware_check_reports_no_drift_when_unchanged(client, tmp_path):
    from jobagent.platform import HardwareInfo
    fake_current = HardwareInfo(
        os_name="Windows", os_version="11",
        cpu_model="Test", cpu_cores=8,
        ram_total_gb=32.0, ram_available_gb=16.0,
        gpu_model="RTX 4070", gpu_vram_gb=12.0,
        gpu_type="nvidia", disk_free_gb=500.0,
        tier="full",
    )
    _seed_setup_state(tmp_path, {
        "tier": "full", "gpu_type": "nvidia", "ram_total_gb": 32.0,
    })
    with patch(
        "jobagent.platform.detect_hardware", return_value=fake_current,
    ):
        r = client.get("/api/system/hardware-check")
    body = r.json()
    assert body["current_tier"] == "full"
    assert body["stored_tier"] == "full"
    assert body["gpu_disappeared"] is False
    assert body["gpu_appeared"] is False
    assert "No drift" in body["drift_summary"]


def test_hardware_check_detects_gpu_disappearance(client, tmp_path):
    from jobagent.platform import HardwareInfo
    fake_current = HardwareInfo(
        os_name="Windows", os_version="11",
        cpu_model="Test", cpu_cores=8,
        ram_total_gb=32.0, ram_available_gb=16.0,
        gpu_model=None, gpu_vram_gb=None, gpu_type=None,
        disk_free_gb=500.0, tier="minimum",
    )
    _seed_setup_state(tmp_path, {
        "tier": "full", "gpu_type": "nvidia", "ram_total_gb": 32.0,
    })
    with patch(
        "jobagent.platform.detect_hardware", return_value=fake_current,
    ):
        r = client.get("/api/system/hardware-check")
    body = r.json()
    assert body["gpu_disappeared"] is True
    assert "GPU disappeared" in body["drift_summary"]


def test_hardware_check_no_stored_returns_no_drift_string(client, tmp_path):
    from jobagent.platform import HardwareInfo
    fake_current = HardwareInfo(
        os_name="Windows", os_version="11",
        cpu_model="Test", cpu_cores=8,
        ram_total_gb=32.0, ram_available_gb=16.0,
        gpu_model=None, gpu_vram_gb=None, gpu_type=None,
        disk_free_gb=500.0, tier="minimum",
    )
    with patch(
        "jobagent.platform.detect_hardware", return_value=fake_current,
    ):
        r = client.get("/api/system/hardware-check")
    body = r.json()
    assert body["stored_tier"] is None
    assert body["gpu_disappeared"] is False


# --- fallback-history -------------------------------------------

def test_fallback_history_empty_when_no_file(client):
    r = client.get("/api/system/fallback-history")
    assert r.json() == []


def test_append_fallback_history_writes_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(
        system_health, "_project_root", lambda: tmp_path,
    )
    entry = {
        "timestamp": "2026-05-19T00:00:00+00:00",
        "function_name": "scorer",
        "success": True,
        "provider": "api",
        "fallbacks_used": 0,
        "events": [],
    }
    path = system_health.append_fallback_history("default", entry)
    assert path.exists()
    assert "scorer" in path.read_text(encoding="utf-8")


def test_fallback_history_reads_appended_rows(client, tmp_path):
    for i in range(3):
        system_health.append_fallback_history("default", {
            "timestamp": f"2026-05-19T00:0{i}:00+00:00",
            "function_name": "scorer",
            "success": True,
            "provider": "api",
            "fallbacks_used": i,
            "events": [],
        })
    rows = client.get("/api/system/fallback-history").json()
    assert len(rows) == 3
    assert rows[0]["fallbacks_used"] == 0
    assert rows[-1]["fallbacks_used"] == 2


def test_fallback_history_respects_limit(client, tmp_path):
    for i in range(5):
        system_health.append_fallback_history("default", {
            "timestamp": "x", "function_name": "f", "success": True,
            "provider": "api", "fallbacks_used": i, "events": [],
        })
    rows = client.get(
        "/api/system/fallback-history?limit=2",
    ).json()
    assert len(rows) == 2
    # Returns the tail (most-recent 2).
    assert rows[-1]["fallbacks_used"] == 4
