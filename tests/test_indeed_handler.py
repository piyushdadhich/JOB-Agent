"""Unit tests for engine.applicant.handlers.indeed."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.applicant.handlers.base import FillContext  # noqa: E402
from engine.applicant.handlers.indeed import IndeedHandler  # noqa: E402
from engine.applicant.profile import ApplicantProfile  # noqa: E402
from engine.applicant.qa_matcher import QAMatcher  # noqa: E402

from tests.test_greenhouse_handler import (  # noqa: E402
    MockLocator,
    MockPage,
)


def _profile() -> ApplicantProfile:
    return ApplicantProfile(
        first_name="Alex", last_name="Doe",
        email="p@example.com", phone="555-0100",
    )


def _ctx(tmp_path) -> FillContext:
    resume = tmp_path / "resume_1.docx"
    resume.write_text("RESUME", encoding="utf-8")
    cl = tmp_path / "cover_letter_1.docx"
    cl.write_text("COVER", encoding="utf-8")
    return FillContext(
        posting={
            "id": 1, "title": "PM", "employer": "Acme",
            "source_url": "https://ca.indeed.com/viewjob?jk=abc",
        },
        profile=_profile(),
        resume_path=resume,
        cover_letter_path=cl,
        resume_text="RESUME",
        cover_letter_text="Dear team, ...",
        qa_matcher=QAMatcher(patterns=[]),
    )


def _run(coro):
    return asyncio.run(coro)


# --- field fills --------------------------------------------------

def test_indeed_fills_full_name_email_phone(tmp_path):
    name_loc = MockLocator()
    email_loc = MockLocator()
    phone_loc = MockLocator()
    page = MockPage(selectors={
        "input[id='input-applicant.name']":        name_loc,
        "input[id='input-applicant.email']":       email_loc,
        "input[id='input-applicant.phoneNumber']": phone_loc,
    })
    h = IndeedHandler()
    res = _run(h.fill_application(page, _ctx(tmp_path)))
    assert "name" in res.fields_filled
    assert "email" in res.fields_filled
    assert "phone" in res.fields_filled
    name_loc.fill.assert_awaited_with("Alex Doe")  # full name


def test_indeed_uploads_resume_via_pdf_input(tmp_path):
    resume_loc = MockLocator()
    page = MockPage(selectors={
        "input[type='file'][accept*='pdf']": resume_loc,
    })
    h = IndeedHandler()
    ctx = _ctx(tmp_path)
    res = _run(h.fill_application(page, ctx))
    assert "resume" in res.fields_filled
    resume_loc.set_input_files.assert_awaited_with(str(ctx.resume_path))


def test_indeed_falls_back_to_generic_file_input(tmp_path):
    """No accept='pdf' input -> the last-ditch input[type='file']
    selector picks up the only file input on the page."""
    file_loc = MockLocator()
    page = MockPage(selectors={
        "input[type='file']": file_loc,
    })
    h = IndeedHandler()
    ctx = _ctx(tmp_path)
    res = _run(h.fill_application(page, ctx))
    assert "resume" in res.fields_filled
    file_loc.set_input_files.assert_awaited_with(str(ctx.resume_path))


def test_indeed_pastes_cover_letter_in_textarea(tmp_path):
    textarea = MockLocator()
    page = MockPage(selectors={
        "textarea[id*='cover' i]": textarea,
    })
    h = IndeedHandler()
    ctx = _ctx(tmp_path)
    res = _run(h.fill_application(page, ctx))
    assert "cover_letter_text" in res.fields_filled
    textarea.fill.assert_awaited_with("Dear team, ...")


def test_indeed_submit_clicks_submit_button(tmp_path):
    btn = MockLocator()
    page = MockPage(selectors={
        "button:has-text('Submit your application')": btn,
    }, url="https://ca.indeed.com/submitted")
    h = IndeedHandler()
    sub = _run(h.submit(page))
    assert sub.ok is True
    assert sub.final_url.endswith("/submitted")
    btn.click.assert_awaited_once()


def test_indeed_submit_returns_error_when_no_button(tmp_path):
    page = MockPage(selectors={})
    h = IndeedHandler()
    sub = _run(h.submit(page))
    assert sub.ok is False
    assert "indeed" in (sub.error or "").lower()
