"""Tests for the prompt + render service wrappers."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.backend.services import prompt_service as ps  # noqa: E402
from dashboard.backend.services import render_service as rs  # noqa: E402


_SAMPLE_RESUME_MD = """\
# JANE DOE
Senior PM | Toronto, ON
jane@example.com • 555-1234 • Toronto • linkedin.com/in/jane

## PROFESSIONAL SUMMARY
Senior delivery leader with 10+ years.

## SKILLS
**Delivery:** Agile, Scrum

## WORK EXPERIENCE

### Senior PM | Acme | Toronto | Jan 2020 - Present
- Led 5 cross-functional teams
- Shipped flagship product on time

## EDUCATION & CERTIFICATIONS
- MBA, Fordham (2018)
- PMP, PMI (Active)
"""


_SAMPLE_COVER_LETTER_MD = """\
# Cover Letter -- Senior PM at Acme

May 7, 2026

Dear Hiring Manager,

I'm thrilled to apply for the Senior PM role at Acme.

In my current role at Wayne, I led delivery for a 12-team program.

Acme's recent move into payments aligns with my background.

Sincerely,
Jane Doe
"""


def test_prompt_service_resume_includes_posting_section(monkeypatch, tmp_path):
    monkeypatch.setattr(ps, "PROJECT_ROOT", tmp_path)
    posting = {
        "title": "Senior PM",
        "employer": "Acme",
        "location": "Toronto",
        "posting_text": "Lead delivery, manage stakeholders.",
    }
    text = ps.PromptService().build_resume_prompt(
        profile_id="p", posting=posting, eval_decision=None,
    )
    assert "Senior PM" in text
    assert "Acme" in text
    assert "Lead delivery" in text


def test_prompt_service_cover_letter_includes_posting_section(monkeypatch, tmp_path):
    monkeypatch.setattr(ps, "PROJECT_ROOT", tmp_path)
    posting = {
        "title": "Delivery Lead",
        "employer": "BMO",
        "location": "Toronto",
        "posting_text": "...",
    }
    text = ps.PromptService().build_cover_letter_prompt(
        profile_id="p", posting=posting, eval_decision=None,
    )
    assert "Delivery Lead" in text
    assert "BMO" in text
    assert "Cover Letter" in text


def test_render_resume_writes_to_stable_pending_path(monkeypatch, tmp_path):
    monkeypatch.setattr(rs, "PROJECT_ROOT", tmp_path)
    posting = {"employer": "Acme", "title": "Senior PM"}
    out = rs.RenderService().render_resume(
        profile_id="p", posting_id=42,
        content=_SAMPLE_RESUME_MD, posting=posting,
    )
    expected = (
        tmp_path / "data" / "p" / "applications" / "pending"
        / "resume_42.docx"
    )
    assert out == expected
    assert out.exists()
    assert out.stat().st_size > 0


def test_render_cover_letter_writes_to_stable_pending_path(monkeypatch, tmp_path):
    monkeypatch.setattr(rs, "PROJECT_ROOT", tmp_path)
    posting = {"employer": "Acme", "title": "Senior PM"}
    out = rs.RenderService().render_cover_letter(
        profile_id="p", posting_id=42,
        content=_SAMPLE_COVER_LETTER_MD, posting=posting,
        candidate_name="Jane Doe",
    )
    expected = (
        tmp_path / "data" / "p" / "applications" / "pending"
        / "cover_letter_42.docx"
    )
    assert out == expected
    assert out.exists()


def test_render_resume_overwrites_existing_pending_file(monkeypatch, tmp_path):
    monkeypatch.setattr(rs, "PROJECT_ROOT", tmp_path)
    posting = {"employer": "Acme", "title": "PM"}
    svc = rs.RenderService()
    p1 = svc.render_resume(
        profile_id="p", posting_id=7,
        content=_SAMPLE_RESUME_MD, posting=posting,
    )
    p2 = svc.render_resume(
        profile_id="p", posting_id=7,
        content=_SAMPLE_RESUME_MD, posting=posting,
    )
    assert p1 == p2
    assert p2.exists()
