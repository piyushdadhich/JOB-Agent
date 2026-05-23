"""Tests for /api/preview and /api/download .docx routes."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from dashboard.backend import app as app_module  # noqa: E402
from dashboard.backend import deps  # noqa: E402
from dashboard.backend.app import create_app  # noqa: E402
from dashboard.backend.deps import get_profile_id, get_tracker  # noqa: E402
from dashboard.backend.routes import files as files_route  # noqa: E402
from dashboard.backend.services import render_service as rs  # noqa: E402
from engine.persistence.tracker import Tracker  # noqa: E402


_RESUME_MD = """\
# JANE DOE
Senior PM | Toronto, ON
jane@example.com

## PROFESSIONAL SUMMARY
Senior delivery leader.

## SKILLS
**Delivery:** Agile

## WORK EXPERIENCE

### Senior PM | Acme | Toronto | Jan 2020 - Present
- Led 5 cross-functional teams

## EDUCATION & CERTIFICATIONS
- MBA, Fordham (2018)
"""


_COVER_LETTER_MD = """\
# Cover Letter -- Senior PM at Acme

May 7, 2026

Dear Hiring Manager,

I'm thrilled to apply.

In my current role, I led delivery.

Acme's mission resonates.

Sincerely,
Jane Doe
"""


@pytest.fixture
def harness(tmp_path, monkeypatch):
    # Point the render service at a temp PROJECT_ROOT so .docx files
    # land in tmp_path rather than data/default/applications/pending.
    monkeypatch.setattr(rs, "PROJECT_ROOT", tmp_path)
    # _candidate_name imports the applicant profile loader which
    # would otherwise look for config/profiles/p_applicant.yaml. The
    # FileNotFoundError fallback returns "Candidate", which is fine
    # for the test.

    db = tmp_path / "t.db"
    tracker = Tracker(profile_id="p", db_path=db)
    app = create_app()

    def _tracker():
        yield tracker

    def _profile():
        return "p"

    app.dependency_overrides[get_tracker] = _tracker
    app.dependency_overrides[get_profile_id] = _profile
    yield TestClient(app), tracker
    tracker.close()


def _add_posting(t, employer="Acme", title="PM"):
    cid = t.upsert_company(employer)
    oid, _ = t.insert_opportunity(
        company_id=cid, source="manual_entry",
        source_url=f"https://x/{employer}", title=title,
        location="Toronto", posting_text="...",
    )
    return oid


def test_preview_resume_returns_docx_when_text_saved(harness):
    client, t = harness
    oid = _add_posting(t)
    client.post(
        f"/api/prompts/{oid}/resume", json={"content": _RESUME_MD},
    )
    r = client.get(f"/api/preview/{oid}/resume.docx")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument"
    )
    # .docx files start with the PK zip-magic bytes.
    assert r.content[:2] == b"PK"


def test_preview_404_when_no_text_saved(harness):
    client, t = harness
    oid = _add_posting(t)
    client.post(  # creates an application but with no text yet
        f"/api/shortlist/{oid}/select",
    )
    r = client.get(f"/api/preview/{oid}/resume.docx")
    assert r.status_code == 404


def test_preview_404_when_no_application(harness):
    client, t = harness
    oid = _add_posting(t)
    r = client.get(f"/api/preview/{oid}/resume.docx")
    assert r.status_code == 404


def test_preview_404_for_unknown_posting(harness):
    client, _ = harness
    r = client.get("/api/preview/9999/resume.docx")
    assert r.status_code == 404


def test_download_resume_attaches_filename(harness):
    client, t = harness
    oid = _add_posting(t)
    client.post(
        f"/api/prompts/{oid}/resume", json={"content": _RESUME_MD},
    )
    r = client.get(f"/api/download/{oid}/resume.docx")
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert "resume_" in cd or ".docx" in cd


def test_preview_cover_letter_returns_docx_when_text_saved(harness):
    client, t = harness
    oid = _add_posting(t)
    client.post(
        f"/api/prompts/{oid}/cover-letter",
        json={"content": _COVER_LETTER_MD},
    )
    r = client.get(f"/api/preview/{oid}/cover-letter.docx")
    assert r.status_code == 200
    assert r.content[:2] == b"PK"


# --- Screenshot route -------------------------------------------

def _seed_app_with_screenshot(t, monkeypatch, tmp_path, image_bytes):
    """Create a posting + application row + a real PNG on disk and
    record the relative path on the row -- mirrors what the apply
    flow writes after capture_confirmation."""
    from dashboard.backend.routes import files as files_route

    # Point the route's PROJECT_ROOT at tmp_path so resolves stay
    # inside the test sandbox.
    monkeypatch.setattr(files_route, "PROJECT_ROOT", tmp_path)
    cid = t.upsert_company("Acme")
    oid, _ = t.insert_opportunity(
        company_id=cid, source="manual_entry",
        source_url="https://x", title="PM",
        location="Toronto", posting_text="...",
    )
    app_id = t.create_application(
        opportunity_id=oid, resume_variant="r.docx",
    )
    screenshots_dir = (
        tmp_path / "data" / "p" / "applications" / "screenshots"
    )
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{oid}_20260507T120000Z.png"
    (screenshots_dir / fname).write_bytes(image_bytes)
    rel = f"data/p/applications/screenshots/{fname}"
    t.update_application_fields(app_id, screenshot_path=rel)
    return app_id, oid


def test_screenshot_route_returns_png_when_file_exists(
    harness, monkeypatch, tmp_path,
):
    client, t = harness
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    app_id, _ = _seed_app_with_screenshot(
        t, monkeypatch, tmp_path, png_bytes,
    )
    r = client.get(f"/api/screenshots/{app_id}.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG")


def test_screenshot_404_when_application_missing(harness):
    client, _ = harness
    r = client.get("/api/screenshots/9999.png")
    assert r.status_code == 404


def test_screenshot_404_when_no_screenshot_recorded(harness):
    client, t = harness
    cid = t.upsert_company("Acme")
    oid, _ = t.insert_opportunity(
        company_id=cid, source="manual_entry",
        source_url="https://x", title="PM",
        location="Toronto", posting_text="...",
    )
    app_id = t.create_application(
        opportunity_id=oid, resume_variant="r.docx",
    )
    r = client.get(f"/api/screenshots/{app_id}.png")
    assert r.status_code == 404


def test_screenshot_404_when_recorded_but_file_missing(
    harness, monkeypatch, tmp_path,
):
    """Row points at a path that doesn't exist on disk -- the
    pending dir got pruned, say. 404 with a clear message."""
    from dashboard.backend.routes import files as files_route

    monkeypatch.setattr(files_route, "PROJECT_ROOT", tmp_path)
    client, t = harness
    cid = t.upsert_company("Acme")
    oid, _ = t.insert_opportunity(
        company_id=cid, source="manual_entry",
        source_url="https://x", title="PM",
        location="Toronto", posting_text="...",
    )
    app_id = t.create_application(
        opportunity_id=oid, resume_variant="r.docx",
    )
    # Make the screenshots dir so the resolve() doesn't blow up,
    # but skip writing the actual file.
    (
        tmp_path / "data" / "p" / "applications" / "screenshots"
    ).mkdir(parents=True, exist_ok=True)
    t.update_application_fields(
        app_id,
        screenshot_path="data/p/applications/screenshots/missing.png",
    )
    r = client.get(f"/api/screenshots/{app_id}.png")
    assert r.status_code == 404


def test_screenshot_403_when_path_traverses_outside_screenshots(
    harness, monkeypatch, tmp_path,
):
    from dashboard.backend.routes import files as files_route

    monkeypatch.setattr(files_route, "PROJECT_ROOT", tmp_path)
    client, t = harness
    cid = t.upsert_company("Acme")
    oid, _ = t.insert_opportunity(
        company_id=cid, source="manual_entry",
        source_url="https://x", title="PM",
        location="Toronto", posting_text="...",
    )
    app_id = t.create_application(
        opportunity_id=oid, resume_variant="r.docx",
    )
    # Make the expected screenshots dir; the row points elsewhere.
    (
        tmp_path / "data" / "p" / "applications" / "screenshots"
    ).mkdir(parents=True, exist_ok=True)
    bad = tmp_path / "secrets.png"
    bad.write_bytes(b"\x89PNG\r\n\x1a\n")
    t.update_application_fields(
        app_id, screenshot_path="../../../secrets.png",
    )
    r = client.get(f"/api/screenshots/{app_id}.png")
    # The row's relative path resolves outside the screenshots dir,
    # so the route refuses with 403 (not 404).
    assert r.status_code == 403
