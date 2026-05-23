"""Unit tests for engine.applicant.handlers.greenhouse.

Mocks the Playwright Page surface as a callable that maps selector
strings to MockLocator instances. asyncio.run() wraps each async
call so we don't need pytest-asyncio.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.applicant.handlers.base import FillContext  # noqa: E402
from engine.applicant.handlers.greenhouse import (  # noqa: E402
    GreenhouseHandler,
)
from engine.applicant.profile import ApplicantProfile  # noqa: E402
from engine.applicant.qa_matcher import QAMatcher  # noqa: E402


# --- Mocks --------------------------------------------------------

class MockLocator:
    """Stand-in for a Playwright Locator. Supports .first (returns
    self), async .count, .fill, .set_input_files, .click,
    .select_option, .text_content, .get_attribute, .evaluate, and
    .nth(i) for label-list iteration.
    """
    def __init__(
        self, count=1, text="", tag="input", attrs=None,
        children=None,
    ):
        self._count = int(count)
        self._text = text
        self._tag = tag
        self._attrs = attrs or {}
        self._children = children or []
        self.fill = AsyncMock()
        self.set_input_files = AsyncMock()
        self.click = AsyncMock()
        self.select_option = AsyncMock()

    @property
    def first(self):
        return self

    async def count(self):
        return self._count

    async def text_content(self):
        return self._text

    async def get_attribute(self, name):
        return self._attrs.get(name)

    async def evaluate(self, code):
        return self._tag.upper()

    def nth(self, i):
        if 0 <= i < len(self._children):
            return self._children[i]
        return MockLocator(count=0)


class MockPage:
    """Minimal Playwright Page stand-in.

    Caller constructs with `selectors`: a dict mapping selector
    string -> MockLocator. Anything not in the dict resolves to a
    count=0 locator (missing element).
    """
    def __init__(
        self,
        selectors=None,
        url="https://example.com/done",
        wait_for_selector_raises=False,
    ):
        self.selectors = selectors or {}
        self.url = url
        self.screenshot = AsyncMock()
        self.wait_for_load_state = AsyncMock()
        self._wait_raises = wait_for_selector_raises

    def locator(self, sel):
        return self.selectors.get(sel, MockLocator(count=0))

    async def wait_for_selector(self, selector, timeout=None):
        if self._wait_raises:
            raise TimeoutError(f"timed out waiting for {selector}")
        return self.selectors.get(selector)


def _profile() -> ApplicantProfile:
    return ApplicantProfile(
        first_name="Alex", last_name="Doe",
        email="p@example.com", phone="555-0100",
        linkedin_url="https://linkedin.com/in/default",
    )


def _ctx(tmp_path, qa=None) -> FillContext:
    resume = tmp_path / "resume_1.docx"
    resume.write_text("RESUME", encoding="utf-8")
    cl = tmp_path / "cover_letter_1.docx"
    cl.write_text("COVER", encoding="utf-8")
    qa = qa or QAMatcher(patterns=[])
    return FillContext(
        posting={"id": 1, "title": "PM", "employer": "Acme",
                 "source_url": "https://boards.greenhouse.io/x/jobs/1"},
        profile=_profile(),
        resume_path=resume,
        cover_letter_path=cl,
        resume_text="RESUME",
        cover_letter_text="COVER",
        qa_matcher=qa,
    )


def _run(coro):
    return asyncio.run(coro)


# --- Field-fill tests ---------------------------------------------

def test_fills_legacy_selectors(tmp_path):
    page = MockPage(selectors={
        "#first_name": MockLocator(),
        "#last_name":  MockLocator(),
        "#email":      MockLocator(),
        "#phone":      MockLocator(),
        # no linkedin selector match -- linkedin should NOT count
    })
    h = GreenhouseHandler()
    res = _run(h.fill_application(page, _ctx(tmp_path)))
    assert "first_name" in res.fields_filled
    assert "last_name" in res.fields_filled
    assert "email" in res.fields_filled
    assert "phone" in res.fields_filled
    assert "linkedin" not in res.fields_filled
    page.selectors["#first_name"].fill.assert_awaited_with("Alex")
    page.selectors["#email"].fill.assert_awaited_with("p@example.com")


def test_falls_back_to_react_selectors_when_legacy_missing(tmp_path):
    page = MockPage(selectors={
        "input[autocomplete='given-name']":  MockLocator(),
        "input[autocomplete='family-name']": MockLocator(),
        "input[type='email']":               MockLocator(),
        "input[type='tel']":                 MockLocator(),
    })
    h = GreenhouseHandler()
    res = _run(h.fill_application(page, _ctx(tmp_path)))
    assert "first_name" in res.fields_filled
    assert "last_name" in res.fields_filled
    assert "email" in res.fields_filled
    assert "phone" in res.fields_filled


def test_skips_field_when_no_selector_matches(tmp_path):
    page = MockPage(selectors={"#first_name": MockLocator()})
    h = GreenhouseHandler()
    res = _run(h.fill_application(page, _ctx(tmp_path)))
    assert res.fields_filled == ["first_name"]


def test_fills_linkedin_when_present(tmp_path):
    page = MockPage(selectors={
        "input[name*='linkedin' i]": MockLocator(),
    })
    h = GreenhouseHandler()
    res = _run(h.fill_application(page, _ctx(tmp_path)))
    assert "linkedin" in res.fields_filled


# --- File upload tests --------------------------------------------

def test_uploads_resume_via_legacy_selector(tmp_path):
    resume_loc = MockLocator()
    page = MockPage(selectors={
        "input[type='file'][id='resume']": resume_loc,
    })
    h = GreenhouseHandler()
    ctx = _ctx(tmp_path)
    res = _run(h.fill_application(page, ctx))
    assert "resume" in res.fields_filled
    resume_loc.set_input_files.assert_awaited_with(str(ctx.resume_path))


def test_uploads_cover_letter_via_legacy_selector(tmp_path):
    cl_loc = MockLocator()
    page = MockPage(selectors={
        "input[type='file'][id='cover_letter']": cl_loc,
    })
    h = GreenhouseHandler()
    ctx = _ctx(tmp_path)
    res = _run(h.fill_application(page, ctx))
    assert "cover_letter" in res.fields_filled
    cl_loc.set_input_files.assert_awaited_with(
        str(ctx.cover_letter_path),
    )


def test_skips_upload_when_file_missing(tmp_path):
    resume_loc = MockLocator()
    page = MockPage(selectors={
        "input[type='file'][id='resume']": resume_loc,
    })
    h = GreenhouseHandler()
    ctx = _ctx(tmp_path)
    # Delete the resume file so the upload is skipped.
    ctx.resume_path.unlink()
    res = _run(h.fill_application(page, ctx))
    assert "resume" not in res.fields_filled
    resume_loc.set_input_files.assert_not_called()


# --- Custom question tests ----------------------------------------

def test_answers_custom_question_via_qa_matcher(tmp_path):
    auth_label = MockLocator(
        text="Are you legally authorized to work?",
        attrs={"for": "auth"},
    )
    auth_input = MockLocator(tag="select")
    labels_list = MockLocator(children=[auth_label])
    page = MockPage(selectors={
        "label": labels_list,
        "[id='auth']": auth_input,
    })
    qa = QAMatcher(patterns=[
        {"match": ["legally authorized"], "answer": "Yes"},
    ])
    h = GreenhouseHandler()
    res = _run(h.fill_application(page, _ctx(tmp_path, qa=qa)))
    assert "Are you legally authorized to work?" in res.questions_answered
    auth_input.select_option.assert_awaited_with(label="Yes")


def test_skips_unmatched_custom_question(tmp_path):
    label = MockLocator(
        text="Tell me about a time you led a difficult team.",
        attrs={"for": "behav"},
    )
    labels_list = MockLocator(children=[label])
    page = MockPage(selectors={"label": labels_list})
    qa = QAMatcher(patterns=[
        {"match": ["legally authorized"], "answer": "Yes"},
    ])
    h = GreenhouseHandler()
    res = _run(h.fill_application(page, _ctx(tmp_path, qa=qa)))
    assert any(
        "Tell me about a time" in s for s in res.questions_skipped
    )


# --- Submit + screenshot tests ------------------------------------

def test_submit_clicks_button_and_returns_ok(tmp_path):
    btn = MockLocator()
    page = MockPage(
        selectors={"input[type='submit'][value*='Submit' i]": btn},
        url="https://confirmed.example.com",
    )
    h = GreenhouseHandler()
    sub = _run(h.submit(page))
    assert sub.ok is True
    assert sub.final_url == "https://confirmed.example.com"
    btn.click.assert_awaited_once()


def test_submit_returns_error_when_no_button_found(tmp_path):
    page = MockPage(selectors={})  # no selectors match
    h = GreenhouseHandler()
    sub = _run(h.submit(page))
    assert sub.ok is False
    assert "submit" in (sub.error or "").lower()


def test_capture_confirmation_writes_screenshot(tmp_path):
    page = MockPage()
    h = GreenhouseHandler()
    out = tmp_path / "screens" / "1.png"
    rv = _run(h.capture_confirmation(page, out))
    assert rv == out
    page.screenshot.assert_awaited_once()
    assert out.parent.exists()
