"""Settings page tests — read + write per section."""
from __future__ import annotations

import io
import json
import zipfile

import pytest
import yaml
from fastapi.testclient import TestClient

from dashboard.backend import app as app_module
from dashboard.backend.routes import settings as settings_routes


def _seed_profile(tmp_path):
    profiles_dir = tmp_path / "config" / "profiles"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "default.yaml").write_text(
        yaml.safe_dump({
            "profile_id": "default",
            "display_name": "Existing Name",
            "domain": "corporate",
            "target_cities": ["toronto"],
            "target_role_types": ["delivery_manager"],
            "salary": {"floor": 80000, "target": 95000, "cap": 120000},
            "employer_size_priority": "mid_sized",
            "llm_routing": {
                "evaluation": "local",
                "resume": "anthropic",
                "form_filling": "local",
            },
            "sources_enabled": ["greenhouse_api", "jobspy"],
            "jobspy": {
                "sites": ["indeed"], "cities": ["toronto"],
                "rate_limit_seconds": 8,
            },
            "linkedin_guest": {
                "keywords_traditional": ["pm"],
                "locations": ["Toronto"],
                "max_pages_per_query": 8,
            },
        }),
        encoding="utf-8",
    )
    (profiles_dir / "default_applicant.yaml").write_text(
        yaml.safe_dump({
            "first_name": "Existing",
            "last_name": "User",
            "email": "old@example.com",
            "phone": "555 000 0000",
            "city": "Toronto",
        }),
        encoding="utf-8",
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings_routes, "_project_root", lambda: tmp_path,
    )
    monkeypatch.setattr(
        app_module, "FRONTEND_DIST",
        tmp_path / "frontend_dist_does_not_exist",
    )
    _seed_profile(tmp_path)
    return TestClient(app_module.create_app())


# --- Profile ----------------------------------------------------

def test_profile_read_returns_yaml_fields(client):
    r = client.get("/api/settings/profile")
    body = r.json()
    assert body["display_name"] == "Existing Name"
    assert body["target_cities"] == ["toronto"]
    assert body["salary"]["floor"] == 80000


def test_profile_write_round_trips(client, tmp_path):
    new = {
        "display_name": "New Name",
        "target_cities": ["calgary", "remote_canada"],
        "salary": {"floor": 100000, "target": 120000, "cap": 150000},
        "target_role_types": ["program_manager"],
        "employer_size_priority": "large",
        "remote_preference": "remote",
    }
    r = client.post("/api/settings/profile", json=new)
    assert r.status_code == 200
    saved = yaml.safe_load(
        (tmp_path / "config" / "profiles" / "default.yaml")
        .read_text(encoding="utf-8")
    )
    assert saved["display_name"] == "New Name"
    assert saved["target_cities"] == ["calgary", "remote_canada"]
    assert saved["salary"]["cap"] == 150000
    # Non-overridden template keys flow through.
    assert saved["domain"] == "corporate"


# --- Applicant --------------------------------------------------

def test_applicant_read_returns_identity(client):
    body = client.get("/api/settings/applicant").json()
    assert body["first_name"] == "Existing"
    assert body["email"] == "old@example.com"


def test_applicant_write_round_trips(client, tmp_path):
    r = client.post("/api/settings/applicant", json={
        "first_name": "Alex", "last_name": "Doe",
        "email": "alex@example.com", "phone": "555-1234",
        "linkedin_url": "https://linkedin.com/in/alex",
    })
    assert r.status_code == 200
    saved = yaml.safe_load(
        (tmp_path / "config" / "profiles" / "default_applicant.yaml")
        .read_text(encoding="utf-8")
    )
    assert saved["first_name"] == "Alex"
    assert saved["email"] == "alex@example.com"
    # Non-overridden field preserved.
    assert saved["city"] == "Toronto"


# --- LLM routing ------------------------------------------------

def test_llm_read_returns_routing(client):
    body = client.get("/api/settings/llm").json()
    assert body == {
        "evaluation": "local", "resume": "anthropic",
        "form_filling": "local",
    }


def test_llm_write_patches_yaml(client, tmp_path):
    r = client.post("/api/settings/llm", json={
        "evaluation": "gemini", "resume": "openai",
        "form_filling": "copy_paste",
    })
    assert r.status_code == 200
    saved = yaml.safe_load(
        (tmp_path / "config" / "profiles" / "default.yaml")
        .read_text(encoding="utf-8")
    )
    assert saved["llm_routing"]["evaluation"] == "gemini"
    assert saved["llm_routing"]["resume"] == "openai"


# --- Sources ----------------------------------------------------

def test_sources_read_returns_enabled_list(client):
    body = client.get("/api/settings/sources").json()
    assert "greenhouse_api" in body["sources_enabled"]
    assert body["jobspy_sites"] == ["indeed"]
    assert body["linkedin_keywords"] == ["pm"]


def test_sources_write_round_trips(client, tmp_path):
    r = client.post("/api/settings/sources", json={
        "sources_enabled": ["greenhouse_api", "lever_api"],
        "jobspy_sites": ["indeed", "google"],
        "jobspy_cities": ["toronto", "calgary"],
        "linkedin_keywords": ["delivery manager", "pm"],
        "linkedin_locations": ["Toronto", "Calgary"],
    })
    assert r.status_code == 200
    saved = yaml.safe_load(
        (tmp_path / "config" / "profiles" / "default.yaml")
        .read_text(encoding="utf-8")
    )
    assert saved["sources_enabled"] == ["greenhouse_api", "lever_api"]
    assert saved["jobspy"]["cities"] == ["toronto", "calgary"]
    # Rate limit knob in the template preserved.
    assert saved["jobspy"]["rate_limit_seconds"] == 8


# --- Exclusions -------------------------------------------------

def test_exclusions_list_returns_entries(client, tmp_path):
    # Seed: exclusions file with one entry.
    (tmp_path / "config" / "exclusions.yaml").write_text(
        yaml.safe_dump({
            "version": 1,
            "profiles": {
                "default": {
                    "excluded_employers": [
                        {
                            "name": "Acme Corp",
                            "aliases": ["Acme Inc"],
                            "reason": "test",
                            "added": "2026-01-01",
                        },
                    ],
                },
            },
        }),
        encoding="utf-8",
    )
    body = client.get("/api/settings/exclusions").json()
    assert len(body["entries"]) == 1
    assert body["entries"][0]["name"] == "Acme Corp"


def test_exclusions_add_creates_entry(client, tmp_path):
    r = client.post("/api/settings/exclusions", json={
        "name": "Beta Corp",
        "aliases": ["Beta Inc"],
        "reason": "previous employer",
        "added": "2026-05-19",
    })
    assert r.status_code == 200
    body = r.json()
    names = [e["name"] for e in body["entries"]]
    assert "Beta Corp" in names


def test_exclusions_delete_removes_entry(client, tmp_path):
    client.post("/api/settings/exclusions", json={
        "name": "ToBeRemoved",
    })
    r = client.delete("/api/settings/exclusions/ToBeRemoved")
    names = [e["name"] for e in r.json()["entries"]]
    assert "ToBeRemoved" not in names


# --- Schedule ---------------------------------------------------

def test_schedule_read_returns_time(client):
    body = client.get("/api/settings/schedule").json()
    # Defaults to 02:00 with no setup_state file.
    assert body["schedule_time"] == "02:00"
    assert "next_run" in body


# --- Gmail ------------------------------------------------------

def test_gmail_status_when_unconfigured(client):
    body = client.get("/api/settings/gmail").json()
    assert body["configured"] is False
    assert body["credentials_present"] is False


# --- Danger zone ------------------------------------------------

def test_reset_wizard_deletes_state(client, tmp_path):
    state_path = tmp_path / "data" / "default" / "setup_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        '{"completed": true, "current_step": 8}', encoding="utf-8",
    )
    r = client.post("/api/settings/reset-wizard")
    assert r.json() == {"deleted": True}
    assert not state_path.exists()


def test_export_data_returns_zip(client, tmp_path):
    # Make sure config/ has something to export.
    r = client.get("/api/settings/export-data")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(r.content))
    names = archive.namelist()
    # Profile YAML we seeded should be in the archive.
    assert any("default.yaml" in n for n in names)
