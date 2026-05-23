"""Tests for the Settings API (applicant / api-key / exclusions)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import dashboard.backend.routes.settings as settings_mod  # noqa: E402
from dashboard.backend.app import create_app  # noqa: E402
from dashboard.backend.deps import get_profile_id  # noqa: E402


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """TestClient with the three Settings files redirected into a
    temp dir so tests never touch real config."""
    applicant = tmp_path / "applicant.yaml"
    api_key = tmp_path / "gemini_api_key.txt"
    exclusions = tmp_path / "exclusions.yaml"
    monkeypatch.setattr(
        settings_mod, "_applicant_path", lambda pid: applicant,
    )
    monkeypatch.setattr(
        settings_mod, "_api_key_path", lambda pid: api_key,
    )
    monkeypatch.setattr(
        settings_mod, "_exclusions_path", lambda pid: exclusions,
    )
    applicant.write_text(
        "first_name: Alex\n"
        "last_name: Sample\n"
        "email: alex.sample@example.com\n"
        "phone: +1 (415) 200-0123\n",
        encoding="utf-8",
    )
    app = create_app()
    app.dependency_overrides[get_profile_id] = lambda: "p"
    return TestClient(app), tmp_path


def test_get_applicant_profile_returns_fields(harness):
    client, _ = harness
    r = client.get("/api/settings/applicant")
    assert r.status_code == 200
    body = r.json()
    assert body["first_name"] == "Alex"
    assert body["email"] == "alex.sample@example.com"


def test_update_applicant_profile_persists(harness):
    client, _ = harness
    r = client.put(
        "/api/settings/applicant",
        json={"phone": "+1 (212) 200-0456", "city": "Springfield"},
    )
    assert r.status_code == 200
    # Re-read: the merged fields persisted, untouched ones survive.
    body = client.get("/api/settings/applicant").json()
    assert body["phone"] == "+1 (212) 200-0456"
    assert body["city"] == "Springfield"
    assert body["email"] == "alex.sample@example.com"


def test_api_key_masked_in_response(harness):
    client, tmp_path = harness
    (tmp_path / "gemini_api_key.txt").write_text(
        "SECRETKEY1234abcd", encoding="utf-8",
    )
    r = client.get("/api/settings/api-key")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    # Only the last 4 chars are visible.
    assert body["masked"].endswith("abcd")
    assert "SECRETKEY" not in body["masked"]
    assert body["masked"].count("•") == len("SECRETKEY1234abcd") - 4


def test_update_api_key_writes_file(harness):
    client, tmp_path = harness
    r = client.put(
        "/api/settings/api-key", json={"key": "  NEWKEY987zzzz  "},
    )
    assert r.status_code == 200
    assert r.json()["masked"].endswith("zzzz")
    # Trimmed and written to disk.
    assert (tmp_path / "gemini_api_key.txt").read_text(
        encoding="utf-8",
    ) == "NEWKEY987zzzz"


def test_update_api_key_rejects_empty(harness):
    client, _ = harness
    r = client.put("/api/settings/api-key", json={"key": "   "})
    assert r.status_code == 400


def test_exclusions_crud(harness):
    client, _ = harness
    # Starts empty. The endpoint returns BOTH the rich Spec-10
    # `entries` and the FIX-5 `companies` projection so two UIs
    # can share one endpoint — only the `companies` field is asserted
    # here (the FIX-5 surface).
    assert client.get("/api/settings/exclusions").json()["companies"] == []
    # Add two.
    client.post(
        "/api/settings/exclusions", json={"company": "Acme Corp"},
    )
    r = client.post(
        "/api/settings/exclusions", json={"company": "Globex"},
    )
    assert sorted(r.json()["companies"]) == ["Acme Corp", "Globex"]
    # Duplicate add is a no-op.
    r = client.post(
        "/api/settings/exclusions", json={"company": "Acme Corp"},
    )
    assert r.json()["companies"].count("Acme Corp") == 1
    # Delete one.
    r = client.delete("/api/settings/exclusions/Globex")
    assert r.json()["companies"] == ["Acme Corp"]
    # Persisted across a fresh read.
    assert client.get(
        "/api/settings/exclusions"
    ).json()["companies"] == ["Acme Corp"]
