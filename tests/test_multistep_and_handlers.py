"""Spec 9 TASKS 2 + 4 — multi-step helpers + handler-registry sanity."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from engine.applicant import multistep
from engine.applicant.handlers import base as handlers_base


# --- TASK 4: ATS handler registry already exists in base.py ----

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://boards.greenhouse.io/acme/jobs/123",  "greenhouse"),
        ("https://job-boards.greenhouse.io/acme/jobs/123", "greenhouse"),
        ("https://jobs.lever.co/wealthsimple/abc",      "lever"),
        ("https://jobs.ashbyhq.com/cohere/abc",         "ashby"),
        ("https://td.wd3.myworkdayjobs.com/TD_Bank_Careers/job/x",
         "workday"),
        ("https://www.indeed.com/applystart?jk=abc",    "indeed"),
        ("https://www.indeed.com/viewjob?jk=abc",       "indeed"),
        ("https://www.linkedin.com/jobs/view/123",      "linkedin"),
        ("https://example.com/careers/123",             None),
        ("",                                            None),
    ],
)
def test_detect_ats_from_url(url, expected):
    assert handlers_base.detect_ats_from_url(url) == expected


# --- TASK 2: multistep -----------------------------------------

@pytest.mark.asyncio
async def test_advance_clicks_next_button_and_waits():
    btn = MagicMock()
    btn.click = AsyncMock()
    btn.count = AsyncMock(return_value=1)

    page = MagicMock()

    def _locator(sel):
        # Return a stub whose .first is the same btn (we don't
        # distinguish first across selectors for the test).
        loc = MagicMock()
        loc.first = btn
        loc.count = AsyncMock(return_value=1)
        return loc

    page.locator = _locator
    waiter = AsyncMock()
    advanced = await multistep.advance(page, settle_ms=10, waiter=waiter)
    assert advanced is True
    btn.click.assert_awaited_once()
    waiter.assert_awaited_with(10)


@pytest.mark.asyncio
async def test_advance_returns_false_when_no_next_button():
    page = MagicMock()

    def _locator(sel):
        loc = MagicMock()
        loc.first = MagicMock()
        loc.count = AsyncMock(return_value=0)
        return loc

    page.locator = _locator
    waiter = AsyncMock()
    advanced = await multistep.advance(page, waiter=waiter)
    assert advanced is False
    waiter.assert_not_awaited()
