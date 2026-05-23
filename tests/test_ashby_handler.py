"""Unit tests for engine.applicant.handlers.ashby."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.applicant.handlers.ashby import AshbyHandler  # noqa: E402
from engine.applicant.handlers.base import FillContext  # noqa: E402
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
            "source_url": "https://jobs.ashbyhq.com/x/1",
        },
        profile=_profile(),
        resume_path=resume,
        cover_letter_path=cl,
        resume_text="RESUME",
        cover_letter_text="COVER",
        qa_matcher=QAMatcher(patterns=[]),
    )


def _run(coro):
    return asyncio.run(coro)


def test_ashby_fills_systemfield_name_email_phone(tmp_path):
    name_loc = MockLocator()
    email_loc = MockLocator()
    phone_loc = MockLocator()
    page = MockPage(selectors={
        "input[name='_systemfield_name']":  name_loc,
        "input[name='_systemfield_email']": email_loc,
        "input[name='_systemfield_phone']": phone_loc,
    })
    h = AshbyHandler()
    res = _run(h.fill_application(page, _ctx(tmp_path)))
    assert "name" in res.fields_filled
    assert "email" in res.fields_filled
    assert "phone" in res.fields_filled
    name_loc.fill.assert_awaited_with("Alex Doe")
    email_loc.fill.assert_awaited_with("p@example.com")


def test_ashby_uploads_resume_via_systemfield(tmp_path):
    resume_loc = MockLocator()
    page = MockPage(selectors={
        "input[type='file'][name='_systemfield_resume']": resume_loc,
    })
    h = AshbyHandler()
    ctx = _ctx(tmp_path)
    res = _run(h.fill_application(page, ctx))
    assert "resume" in res.fields_filled
    resume_loc.set_input_files.assert_awaited_with(str(ctx.resume_path))


def test_ashby_falls_back_to_generic_file_input_for_resume(tmp_path):
    """If no _systemfield_resume input exists, the last-ditch
    `input[type='file']` selector picks up the only file input."""
    file_loc = MockLocator()
    page = MockPage(selectors={
        "input[type='file']": file_loc,
    })
    h = AshbyHandler()
    ctx = _ctx(tmp_path)
    res = _run(h.fill_application(page, ctx))
    assert "resume" in res.fields_filled
    file_loc.set_input_files.assert_awaited_with(str(ctx.resume_path))


def test_ashby_submit_returns_ok(tmp_path):
    btn = MockLocator()
    page = MockPage(selectors={
        "button[type='submit']:has-text('Submit')": btn,
    }, url="https://jobs.ashbyhq.com/x/1/done")
    h = AshbyHandler()
    sub = _run(h.submit(page))
    assert sub.ok is True
    btn.click.assert_awaited_once()
