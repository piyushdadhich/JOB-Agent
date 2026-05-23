"""Spec 1 TASK 3 — tests for POST /api/setup/profile."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from dashboard.backend import app as app_module
from dashboard.backend.routes import setup as setup_routes


def _seed_templates(tmp_path: Path) -> None:
    """Write minimal .yaml.example templates the endpoint reads from."""
    profiles_dir = tmp_path / "config" / "profiles"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "default.yaml.example").write_text(
        yaml.safe_dump({
            "profile_id": "default",
            "display_name": "Your Name",
            "domain": "corporate",
            "salary": {"floor": 80000},
        }),
        encoding="utf-8",
    )
    (profiles_dir / "default_applicant.yaml.example").write_text(
        yaml.safe_dump({
            "first_name": "Your",
            "last_name": "Name",
            "email": "you@example.com",
            "phone": "555 000 0000",
            "city": "Toronto",
        }),
        encoding="utf-8",
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_routes, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        app_module, "FRONTEND_DIST",
        tmp_path / "frontend_dist_does_not_exist",
    )
    _seed_templates(tmp_path)
    return TestClient(app_module.create_app())


def test_save_profile_writes_yaml_files(client, tmp_path):
    r = client.post("/api/setup/profile", json={
        "full_name": "Alex Doe",
        "email": "alex@example.com",
        "phone": "555 123 4567",
        "linkedin_url": "https://linkedin.com/in/alexdoe",
    })
    assert r.status_code == 200

    profile = yaml.safe_load(
        (tmp_path / "config" / "profiles" / "default.yaml")
        .read_text(encoding="utf-8")
    )
    applicant = yaml.safe_load(
        (tmp_path / "config" / "profiles" / "default_applicant.yaml")
        .read_text(encoding="utf-8")
    )

    assert profile["display_name"] == "Alex Doe"
    assert profile["profile_id"] == "default"
    # Non-overridden fields from the template flow through.
    assert profile["domain"] == "corporate"
    assert profile["salary"] == {"floor": 80000}

    assert applicant["first_name"] == "Alex"
    assert applicant["last_name"] == "Doe"
    assert applicant["email"] == "alex@example.com"
    assert applicant["phone"] == "555 123 4567"
    assert applicant["linkedin_url"] == "https://linkedin.com/in/alexdoe"
    # Non-overridden fields preserved.
    assert applicant["city"] == "Toronto"


def test_save_profile_includes_llm_routing_from_step_2(client, tmp_path):
    # Pre-seed step 2 data so the profile endpoint can pull routing.
    client.post("/api/setup/step/2", json={
        "routing": {"evaluation": "local", "resume": "anthropic"},
    })
    client.post("/api/setup/profile", json={
        "full_name": "Alex Doe",
        "email": "alex@example.com",
    })
    profile = yaml.safe_load(
        (tmp_path / "config" / "profiles" / "default.yaml")
        .read_text(encoding="utf-8")
    )
    assert profile["llm_routing"] == {
        "evaluation": "local", "resume": "anthropic",
    }


def test_save_profile_requires_name_and_email(client):
    r1 = client.post("/api/setup/profile", json={
        "full_name": "  ", "email": "x@y.com",
    })
    assert r1.status_code == 400

    r2 = client.post("/api/setup/profile", json={
        "full_name": "Alex Doe", "email": "  ",
    })
    assert r2.status_code == 400


def test_save_profile_handles_single_name_token(client, tmp_path):
    client.post("/api/setup/profile", json={
        "full_name": "Cher",
        "email": "cher@example.com",
    })
    applicant = yaml.safe_load(
        (tmp_path / "config" / "profiles" / "default_applicant.yaml")
        .read_text(encoding="utf-8")
    )
    assert applicant["first_name"] == "Cher"
    assert applicant["last_name"] == ""


def test_save_profile_appends_api_keys_to_env(client, tmp_path):
    r = client.post("/api/setup/profile", json={
        "full_name": "Alex Doe",
        "email": "alex@example.com",
        "api_keys": {
            "anthropic": "sk-anth-test",
            "openai":    "sk-openai-test",
        },
    })
    assert r.status_code == 200
    assert r.json()["env_updated"] is True

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY=sk-anth-test" in env_text
    assert "OPENAI_API_KEY=sk-openai-test" in env_text


def test_save_profile_does_not_duplicate_existing_env_keys(client, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("ANTHROPIC_API_KEY=already-set\n", encoding="utf-8")
    r = client.post("/api/setup/profile", json={
        "full_name": "Alex Doe",
        "email": "alex@example.com",
        "api_keys": {
            "anthropic": "new-value-ignored",
            "gemini":    "g-test",
        },
    })
    assert r.status_code == 200
    text = env_path.read_text(encoding="utf-8")
    # Existing line untouched.
    assert "ANTHROPIC_API_KEY=already-set" in text
    assert "new-value-ignored" not in text
    # New provider appended.
    assert "GEMINI_API_KEY=g-test" in text


def test_save_profile_skips_env_write_when_no_keys(client, tmp_path):
    r = client.post("/api/setup/profile", json={
        "full_name": "Alex Doe",
        "email": "alex@example.com",
    })
    assert r.status_code == 200
    assert r.json()["env_updated"] is False
    assert not (tmp_path / ".env").exists()


def test_save_profile_500s_when_template_missing(client, tmp_path):
    (tmp_path / "config" / "profiles" / "default.yaml.example").unlink()
    r = client.post("/api/setup/profile", json={
        "full_name": "Alex Doe",
        "email": "alex@example.com",
    })
    assert r.status_code == 500
    assert "template" in r.json()["detail"].lower()
