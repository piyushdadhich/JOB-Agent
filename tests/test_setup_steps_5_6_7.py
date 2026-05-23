"""Spec 1 TASKS 5+6+7 — target-market / sources / Gmail tests."""
from __future__ import annotations

import json

import pytest
import yaml
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
    profiles_dir = tmp_path / "config" / "profiles"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "default.yaml.example").write_text(
        yaml.safe_dump({
            "profile_id": "default",
            "display_name": "Your Name",
            "domain": "corporate",
            "salary": {"floor": 80000, "target": 95000, "cap": 120000},
            "jobspy": {
                "sites": ["indeed"],
                "cities": [],
                "rate_limit_seconds": 8,
            },
            "linkedin_guest": {
                "keywords_traditional": [],
                "locations": [],
                "max_pages_per_query": 8,
            },
        }),
        encoding="utf-8",
    )
    return TestClient(app_module.create_app())


# --- TASK 5: target-market -------------------------------------

def test_target_market_writes_yaml(client, tmp_path):
    r = client.post("/api/setup/target-market", json={
        "target_cities": ["toronto", "remote_canada"],
        "remote_preference": "hybrid",
        "salary": {"floor": 90000, "target": 110000, "cap": 140000},
        "target_role_types": ["delivery_manager", "program_manager"],
        "employer_size_priority": "mid_sized",
        "industries": ["financial_services", "public_sector"],
    })
    assert r.status_code == 200
    data = yaml.safe_load(
        (tmp_path / "config" / "profiles" / "default.yaml").read_text(
            encoding="utf-8",
        )
    )
    assert data["target_cities"] == ["toronto", "remote_canada"]
    assert data["remote_preference"] == "hybrid"
    assert data["salary"] == {"floor": 90000, "target": 110000, "cap": 140000}
    assert data["target_role_types"] == [
        "delivery_manager", "program_manager",
    ]
    assert data["employer_size_priority"] == "mid_sized"
    assert data["target_industries"] == [
        "financial_services", "public_sector",
    ]


def test_target_market_preserves_template_keys(client, tmp_path):
    client.post("/api/setup/target-market", json={
        "target_cities": ["toronto"],
    })
    data = yaml.safe_load(
        (tmp_path / "config" / "profiles" / "default.yaml").read_text(
            encoding="utf-8",
        )
    )
    # Non-overridden template keys flow through.
    assert data["domain"] == "corporate"
    assert "jobspy" in data


# --- TASK 6: sources --------------------------------------------

def test_sources_writes_jobspy_and_linkedin(client, tmp_path):
    r = client.post("/api/setup/sources", json={
        "sources_enabled": [
            "greenhouse_api", "jobspy", "linkedin_guest", "manual_entry",
        ],
        "country": "Canada",
        "jobspy_sites": ["indeed", "google"],
        "jobspy_cities": ["toronto", "calgary"],
        "linkedin_keywords": ["senior manager", "delivery manager"],
        "linkedin_locations": ["Toronto", "Calgary"],
    })
    assert r.status_code == 200
    data = yaml.safe_load(
        (tmp_path / "config" / "profiles" / "default.yaml").read_text(
            encoding="utf-8",
        )
    )
    assert data["sources_enabled"] == [
        "greenhouse_api", "jobspy", "linkedin_guest", "manual_entry",
    ]
    assert data["country"] == "Canada"
    # jobspy: merged into the existing template block, rate_limit_seconds
    # preserved.
    assert data["jobspy"]["sites"] == ["indeed", "google"]
    assert data["jobspy"]["cities"] == ["toronto", "calgary"]
    assert data["jobspy"]["rate_limit_seconds"] == 8
    # linkedin_guest similarly merged.
    assert data["linkedin_guest"]["keywords_traditional"] == [
        "senior manager", "delivery manager",
    ]
    assert data["linkedin_guest"]["max_pages_per_query"] == 8


# --- TASK 7: Gmail ----------------------------------------------

def test_gmail_upload_accepts_json(client, tmp_path):
    creds = json.dumps({"installed": {"client_id": "abc"}})
    r = client.post(
        "/api/setup/gmail/upload",
        files={
            "file": (
                "credentials.json", creds.encode("utf-8"),
                "application/json",
            ),
        },
    )
    assert r.status_code == 200
    target = tmp_path / "data" / "default" / "gmail_credentials.json"
    assert target.exists()
    assert json.loads(target.read_text())["installed"]["client_id"] == "abc"


def test_gmail_upload_rejects_non_json_file(client):
    r = client.post(
        "/api/setup/gmail/upload",
        files={"file": ("creds.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 400


def test_gmail_upload_rejects_invalid_json(client):
    r = client.post(
        "/api/setup/gmail/upload",
        files={
            "file": ("creds.json", b"not-json{", "application/json"),
        },
    )
    assert r.status_code == 400


def test_gmail_finalize_detects_token(client, tmp_path):
    token_path = tmp_path / "data" / "default" / "gmail_token.json"
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(json.dumps({"token": "x"}), encoding="utf-8")
    r = client.post("/api/setup/gmail/finalize")
    assert r.status_code == 200
    body = r.json()
    assert body["gmail_configured"] is True
    assert body["token_path"].endswith("gmail_token.json")
    # Also patched into setup state.
    cfg = client.get("/api/setup/config").json()
    assert cfg["steps"]["7"] == {"gmail_configured": True}


def test_gmail_finalize_when_token_absent(client):
    r = client.post("/api/setup/gmail/finalize")
    body = r.json()
    assert body["gmail_configured"] is False
    assert body["token_path"] is None
    cfg = client.get("/api/setup/config").json()
    assert cfg["steps"]["7"] == {"gmail_configured": False}
