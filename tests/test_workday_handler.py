"""Unit tests for engine.applicant.handlers.workday.

Workday is the most complex handler -- 5-step wizard with
searchable comboboxes and a voluntary self-id page. Each test
focuses on one helper or one step.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.applicant.handlers.base import FillContext  # noqa: E402
from engine.applicant.handlers.workday import (  # noqa: E402
    WorkdayHandler,
    click_next,
    decline_voluntary_self_id,
    open_combobox_and_pick,
)
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
        city="Toronto", province="Ontario", country="Canada",
        postal_code="M1A 1A1",
    )


def _ctx(tmp_path) -> FillContext:
    resume = tmp_path / "resume_1.docx"
    resume.write_text("RESUME", encoding="utf-8")
    cl = tmp_path / "cover_letter_1.docx"
    cl.write_text("COVER", encoding="utf-8")
    return FillContext(
        posting={
            "id": 1, "title": "PM", "employer": "TD",
            "source_url": "https://td.wd3.myworkdayjobs.com/x/job/y",
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


# --- combobox primitive ------------------------------------------

def test_open_combobox_clicks_trigger_types_value_picks_option():
    trigger = MockLocator()
    option = MockLocator()
    page = MockPage(selectors={
        "[data-automation-id='countryDropdown']": trigger,
        "[role='option']:has-text('Canada')":     option,
    })
    ok = _run(open_combobox_and_pick(
        page, ("[data-automation-id='countryDropdown']",),
        "Canada",
    ))
    assert ok is True
    trigger.click.assert_awaited_once()
    trigger.fill.assert_awaited_with("Canada")
    option.click.assert_awaited_once()


def test_open_combobox_returns_false_when_no_trigger():
    page = MockPage(selectors={})
    ok = _run(open_combobox_and_pick(
        page, ("[data-automation-id='countryDropdown']",), "Canada",
    ))
    assert ok is False


# --- click_next --------------------------------------------------

def test_click_next_finds_pageFooterNextButton():
    btn = MockLocator()
    page = MockPage(selectors={
        "[data-automation-id='pageFooterNextButton']": btn,
    })
    assert _run(click_next(page)) is True
    btn.click.assert_awaited_once()


def test_click_next_falls_back_to_text_match():
    btn = MockLocator()
    page = MockPage(selectors={"button:has-text('Next')": btn})
    assert _run(click_next(page)) is True
    btn.click.assert_awaited_once()


def test_click_next_returns_false_when_no_button():
    page = MockPage(selectors={})
    assert _run(click_next(page)) is False


# --- decline_voluntary_self_id -----------------------------------

def test_decline_voluntary_self_id_picks_first_decline_option():
    """Two selects on the page; first label tried that succeeds wins."""
    sel1 = MockLocator()
    sel2 = MockLocator()
    selects_group = MockLocator(children=[sel1, sel2])
    page = MockPage(selectors={"select": selects_group})
    selects_group._count = 2
    n = _run(decline_voluntary_self_id(page))
    assert n == 2
    # The first decline option in _DECLINE_OPTIONS is
    # "I don't wish to answer".
    sel1.select_option.assert_awaited_with(label="I don't wish to answer")
    sel2.select_option.assert_awaited_with(label="I don't wish to answer")


def test_decline_voluntary_self_id_skips_when_no_decline_label():
    """Mock that raises on every label -> count stays 0."""
    sel1 = MockLocator()
    sel1.select_option = AsyncMock(
        side_effect=Exception("no such label"),
    )
    selects_group = MockLocator(children=[sel1])
    selects_group._count = 1
    page = MockPage(selectors={"select": selects_group})
    n = _run(decline_voluntary_self_id(page))
    assert n == 0


# --- WorkdayHandler.fill_application -----------------------------

def _step1_locators():
    """Build the locator dict for a Step 1-only happy path:
    name/email/phone/city/postal fields all present + Next button.
    """
    next_btn = MockLocator()
    return {
        "[data-automation-id='legalNameSection_firstName']": MockLocator(),
        "[data-automation-id='legalNameSection_lastName']":  MockLocator(),
        "[data-automation-id='email']":                      MockLocator(),
        "[data-automation-id='phone-number']":               MockLocator(),
        "[data-automation-id='addressSection_city']":        MockLocator(),
        "[data-automation-id='addressSection_postalCode']":  MockLocator(),
        "[data-automation-id='pageFooterNextButton']":       next_btn,
    }


def test_fill_application_aborts_when_wizard_never_appears(tmp_path):
    page = MockPage(wait_for_selector_raises=True)
    h = WorkdayHandler()
    res = _run(h.fill_application(page, _ctx(tmp_path)))
    assert res.ok is False
    assert "wizard never appeared" in (res.error or "").lower()


def test_fill_application_step1_populates_my_information(tmp_path):
    locs = _step1_locators()
    # Re-use Next button on every step (Steps 2/3/4 will also click
    # this same locator since it's the only Next on the page).
    locs["[data-automation-id='file-upload-input-ref']"] = MockLocator()
    page = MockPage(selectors=locs)
    h = WorkdayHandler()
    res = _run(h.fill_application(page, _ctx(tmp_path)))
    assert res.ok is True
    assert "first_name" in res.fields_filled
    assert "last_name" in res.fields_filled
    assert "email" in res.fields_filled
    assert "phone" in res.fields_filled
    assert "city" in res.fields_filled
    assert "postal_code" in res.fields_filled


def test_fill_application_uploads_resume_at_step2(tmp_path):
    locs = _step1_locators()
    upload = MockLocator()
    locs["[data-automation-id='file-upload-input-ref']"] = upload
    page = MockPage(selectors=locs)
    h = WorkdayHandler()
    ctx = _ctx(tmp_path)
    res = _run(h.fill_application(page, ctx))
    assert res.ok is True
    assert "resume" in res.fields_filled
    upload.set_input_files.assert_awaited_with(str(ctx.resume_path))


def test_fill_application_returns_error_when_step1_next_missing(
    tmp_path,
):
    locs = _step1_locators()
    # Drop the Next button so click_next returns False.
    del locs["[data-automation-id='pageFooterNextButton']"]
    page = MockPage(selectors=locs)
    h = WorkdayHandler()
    res = _run(h.fill_application(page, _ctx(tmp_path)))
    assert res.ok is False
    assert "step 1" in (res.error or "").lower()


# --- submit ------------------------------------------------------

def test_submit_clicks_pageFooterSubmitButton(tmp_path):
    btn = MockLocator()
    page = MockPage(selectors={
        "[data-automation-id='pageFooterSubmitButton']": btn,
    }, url="https://td.wd3.myworkdayjobs.com/x/applied")
    h = WorkdayHandler()
    sub = _run(h.submit(page))
    assert sub.ok is True
    assert sub.final_url.endswith("/applied")


def test_submit_returns_error_when_no_button(tmp_path):
    page = MockPage(selectors={})
    h = WorkdayHandler()
    sub = _run(h.submit(page))
    assert sub.ok is False
    assert "submit" in (sub.error or "").lower()
