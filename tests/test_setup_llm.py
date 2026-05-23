"""Spec 1 TASK 2 — tests for the LLM-config setup endpoints."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

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
    return TestClient(app_module.create_app())


# --- test-api-key -----------------------------------------------

def _fake_response(status: int):
    resp = MagicMock()
    resp.status_code = status
    return resp


def test_test_api_key_rejects_unknown_provider(client):
    r = client.post(
        "/api/setup/test-api-key",
        json={"provider": "wat", "api_key": "x"},
    )
    assert r.status_code == 400


def test_test_api_key_rejects_empty_key(client):
    r = client.post(
        "/api/setup/test-api-key",
        json={"provider": "openai", "api_key": "  "},
    )
    assert r.status_code == 400


def test_test_api_key_returns_ok_when_provider_returns_200(client):
    with patch.object(
        setup_routes.requests, "get",
        return_value=_fake_response(200),
    ):
        r = client.post(
            "/api/setup/test-api-key",
            json={"provider": "openai", "api_key": "sk-test"},
        )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "detail": None}


def test_test_api_key_returns_not_ok_on_401(client):
    with patch.object(
        setup_routes.requests, "get",
        return_value=_fake_response(401),
    ):
        r = client.post(
            "/api/setup/test-api-key",
            json={"provider": "anthropic", "api_key": "bad"},
        )
    body = r.json()
    assert body["ok"] is False
    assert "401" in body["detail"]


def test_test_api_key_handles_network_error(client):
    with patch.object(
        setup_routes.requests, "get",
        side_effect=setup_routes.requests.ConnectionError("dns fail"),
    ):
        r = client.post(
            "/api/setup/test-api-key",
            json={"provider": "gemini", "api_key": "x"},
        )
    body = r.json()
    assert body["ok"] is False
    assert "dns fail" in body["detail"]


# --- check-ollama -----------------------------------------------

def test_check_ollama_missing_binary(client):
    with patch.object(setup_routes.shutil, "which", return_value=None):
        r = client.post("/api/setup/check-ollama")
    body = r.json()
    assert body == {
        "installed": False, "models": [], "detail": "ollama binary not on PATH",
    }


def test_check_ollama_parses_model_list(client):
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = (
        "NAME              ID            SIZE   MODIFIED\n"
        "gemma:7b          abc123        4.1 GB 2 days ago\n"
        "llama3.2:3b       def456        2.0 GB 5 days ago\n"
    )
    fake_proc.stderr = ""
    with patch.object(setup_routes.shutil, "which", return_value="ollama"), \
         patch.object(
             setup_routes.subprocess, "run", return_value=fake_proc,
         ):
        r = client.post("/api/setup/check-ollama")
    body = r.json()
    assert body["installed"] is True
    assert body["models"] == ["gemma:7b", "llama3.2:3b"]
    assert body["detail"] is None


# --- pull-model -------------------------------------------------

def test_pull_model_rejects_empty(client):
    r = client.post("/api/setup/pull-model", json={"model": "  "})
    assert r.status_code == 400


def test_pull_model_streams_subprocess_output(client):
    # Simulate a tiny pull: two output lines, exit code 0.
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    output_lines = iter([b"pulling manifest\n", b"verifying sha256\n", b""])
    fake_proc.stdout = MagicMock()
    fake_proc.stdout.readline = lambda: next(output_lines)

    with patch.object(setup_routes.shutil, "which", return_value="ollama"), \
         patch.object(
             setup_routes.subprocess, "Popen", return_value=fake_proc,
         ):
        with client.stream(
            "POST", "/api/setup/pull-model", json={"model": "gemma:7b"},
        ) as r:
            assert r.status_code == 200
            body = b"".join(r.iter_bytes())
    text = body.decode("utf-8")
    assert "pulling manifest" in text
    assert "verifying sha256" in text


def test_pull_model_handles_missing_binary(client):
    with patch.object(setup_routes.shutil, "which", return_value=None):
        with client.stream(
            "POST", "/api/setup/pull-model", json={"model": "x"},
        ) as r:
            assert r.status_code == 200
            body = b"".join(r.iter_bytes())
    assert b"not on PATH" in body
