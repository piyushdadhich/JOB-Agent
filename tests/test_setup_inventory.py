"""Spec 1 TASK 4 — career inventory endpoint tests."""
from __future__ import annotations

import io
from pathlib import Path

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


# --- parse-inventory-response ----------------------------------

_SAMPLE_INVENTORY = """\
# Career Inventory

## Roles

### Senior Manager — Acme Corp — June 2022 to November 2024
**Location:** Remote
**Team size:** 18

Daily work narrative goes here. {body}

**Tools / technologies daily:**
- Jira
- Slack

**Measurable outcomes:**
- Delivered 6 weeks ahead.
- 40% reduction in MTTR.

**Why I left:** End of engagement.

**Interview narrative:** Strongest example of running a delivery
program where engineering, product, and the client sponsor all
needed different things from me on the same day. The credit-
decisioning workstream demonstrates that I can hold a technical
conversation about service-level objectives while also negotiating
commercial implications with a non-technical sponsor.

---

### Operations Manager — TestCo — Jan 2020 to May 2022
**Location:** Toronto
**Team size:** 12

Day-to-day operations of a regional transit agency's payments-
processing workstream. {body}

**Tools / technologies daily:**
- ServiceNow
- SAP
- Tableau

**Measurable outcomes:**
- Hit cycle-time SLA every month for 28 months.
- Closed two regulator findings in 90 days.

**Why I left:** Career move.

**Interview narrative:** Learned to operate inside compliance
constraints without treating them as adversarial. The audit
recommendations matter in their actual scope rather than over-
fitting the response.
"""


def test_parse_inventory_extracts_two_roles(client):
    body = _SAMPLE_INVENTORY.replace("{body}", "x" * 800)
    r = client.post(
        "/api/setup/parse-inventory-response",
        json={"markdown": body},
    )
    assert r.status_code == 200
    payload = r.json()
    assert len(payload["roles"]) == 2
    assert payload["roles"][0]["title"] == "Senior Manager"
    assert payload["roles"][0]["company"] == "Acme Corp"
    assert payload["roles"][0]["dates"] == "June 2022 to November 2024"


def test_parse_inventory_scores_quality(client):
    body = _SAMPLE_INVENTORY.replace("{body}", "x" * 1500)
    r = client.post(
        "/api/setup/parse-inventory-response",
        json={"markdown": body},
    ).json()
    # Both roles >1500 chars body → full per-role contribution.
    assert r["quality_score"] >= 70


def test_parse_inventory_flags_thin_roles(client):
    body = _SAMPLE_INVENTORY.replace("{body}", "")
    r = client.post(
        "/api/setup/parse-inventory-response",
        json={"markdown": body},
    ).json()
    notes = " ".join(r["quality_notes"]).lower()
    assert "thin" in notes or "detail" in notes


def test_parse_inventory_empty_response(client):
    r = client.post(
        "/api/setup/parse-inventory-response",
        json={"markdown": "this has no role headings"},
    ).json()
    assert r["roles"] == []
    assert r["quality_score"] == 0
    assert any("No roles" in n for n in r["quality_notes"])


# --- inventory-prompt ------------------------------------------

def test_inventory_prompt_returns_string(client):
    r = client.get("/api/setup/inventory-prompt").json()
    assert "career inventory" in r["prompt"].lower()
    # Substitutes the display name from step 3 when present.
    assert "the applicant" in r["prompt"]


def test_inventory_prompt_uses_full_name_from_step_3(client):
    client.post("/api/setup/step/3", json={"full_name": "Alex Doe"})
    r = client.get("/api/setup/inventory-prompt").json()
    assert "Alex Doe" in r["prompt"]


# --- save-inventory + inventory-quality ------------------------

def test_save_inventory_writes_file(client, tmp_path):
    body = _SAMPLE_INVENTORY.replace("{body}", "x" * 800)
    r = client.post(
        "/api/setup/save-inventory", json={"markdown": body},
    )
    assert r.status_code == 200
    target = tmp_path / "source_materials" / "default" / "career_inventory.md"
    assert target.exists()
    assert target.read_text(encoding="utf-8") == body


def test_save_inventory_rejects_empty(client):
    r = client.post("/api/setup/save-inventory", json={"markdown": "   "})
    assert r.status_code == 400


def test_inventory_quality_reports_missing(client):
    r = client.get("/api/setup/inventory-quality").json()
    assert r == {
        "exists": False, "role_count": 0, "quality_score": 0,
        "quality_notes": ["No career_inventory.md saved yet."],
    }


def test_inventory_quality_reads_saved_file(client):
    body = _SAMPLE_INVENTORY.replace("{body}", "x" * 1200)
    client.post("/api/setup/save-inventory", json={"markdown": body})
    r = client.get("/api/setup/inventory-quality").json()
    assert r["exists"] is True
    assert r["role_count"] == 2
    assert 60 <= r["quality_score"] <= 100


# --- parse-resume ----------------------------------------------

def test_parse_resume_accepts_txt(client):
    txt = (
        "Senior Manager — Acme Corp — June 2020 to March 2023\n"
        "Led big things.\n"
        "Director — TestCo — Apr 2023 to present\n"
    )
    r = client.post(
        "/api/setup/parse-resume",
        files={"file": ("resume.txt", txt.encode("utf-8"), "text/plain")},
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["parser"] == "txt"
    assert "Acme Corp" in payload["raw_text"]


def test_parse_resume_rejects_unknown_format(client):
    r = client.post(
        "/api/setup/parse-resume",
        files={"file": ("resume.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert r.status_code == 400
    assert "unsupported" in r.json()["detail"].lower()
