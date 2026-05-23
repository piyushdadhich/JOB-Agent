"""Unit tests for engine.applicant.handlers.lever."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.applicant.handlers.base import FillContext  # noqa: E402
from engine.applicant.handlers.lever import LeverHandler  # noqa: E402
from engine.applicant.profile import ApplicantProfile  # noqa: E402
from engine.applicant.qa_matcher import QAMatcher  # noqa: E402


# Reuse the MockLocator/MockPage from the greenhouse test module.
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
            "source_url": "https://jobs.lever.co/x/1",
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


def test_lever_fills_name_email_phone(tmp_path):
    name_loc = MockLocator()
    email_loc = MockLocator()
    phone_loc = MockLocator()
    page = MockPage(selectors={
        "input[name='name']":  name_loc,
        "input[name='email']": email_loc,
        "input[name='phone']": phone_loc,
    })
    h = LeverHandler()
    res = _run(h.fill_application(page, _ctx(tmp_path)))
    assert "name" in res.fields_filled
    assert "email" in res.fields_filled
    assert "phone" in res.fields_filled
    name_loc.fill.assert_awaited_with("Alex Doe")  # full name


def test_lever_uploads_resume(tmp_path):
    resume_loc = MockLocator()
    page = MockPage(selectors={
        "input[type='file'][name='resume']": resume_loc,
    })
    h = LeverHandler()
    ctx = _ctx(tmp_path)
    res = _run(h.fill_application(page, ctx))
    assert "resume" in res.fields_filled
    resume_loc.set_input_files.assert_awaited_with(str(ctx.resume_path))


def test_lever_pastes_cover_letter_into_textarea_when_no_file_input(
    tmp_path,
):
    """No cover_letter file input on the page -> fall back to
    textarea[name='comments'] with the plain-text body."""
    textarea = MockLocator()
    page = MockPage(selectors={
        "textarea[name='comments']": textarea,
    })
    h = LeverHandler()
    ctx = _ctx(tmp_path)
    res = _run(h.fill_application(page, ctx))
    assert "cover_letter_text" in res.fields_filled
    assert "cover_letter_file" not in res.fields_filled
    textarea.fill.assert_awaited_with("Dear team, ...")


def test_lever_submit_clicks_button(tmp_path):
    btn = MockLocator()
    page = MockPage(selectors={
        "button[type='submit']:has-text('Submit')": btn,
    }, url="https://jobs.lever.co/x/1/thanks")
    h = LeverHandler()
    sub = _run(h.submit(page))
    assert sub.ok is True
    assert sub.final_url == "https://jobs.lever.co/x/1/thanks"
    btn.click.assert_awaited_once()
