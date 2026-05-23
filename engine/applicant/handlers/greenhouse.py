"""Greenhouse ATS handler for the Playwright Application Agent.

Form layouts handled:
  - boards.greenhouse.io/{slug}/jobs/{id}     (legacy, server-rendered)
  - job-boards.greenhouse.io/{slug}/jobs/{id} (newer React-rendered)

Both use a single page form. Required fields: first_name, last_name,
email, phone, resume upload. Optional: cover letter upload, LinkedIn
URL, custom screening questions (varies per posting).

Strategy:
  1. Fill structured fields with try-each-selector fallbacks (the
     React variant uses different attributes than the legacy form).
  2. set_input_files for resume + cover letter file uploads.
  3. Walk every <label> element, run its text through QAMatcher,
     fill the associated input by `for=` link.
  4. Take a screenshot. Do NOT click submit -- caller does that
     after human review.
"""
from __future__ import annotations

import logging
from pathlib import Path

from engine.applicant.handlers._common import (
    answer_custom_questions,
    fill_first_match,
    upload_first_match,
)
from engine.applicant.handlers.base import (
    ApplicationResult,
    FillContext,
    SubmissionResult,
)

logger = logging.getLogger(__name__)


# Tolerant selectors -- try in order, first one that exists wins.
_FIELD_SELECTORS: dict[str, tuple[str, ...]] = {
    "first_name":     ("#first_name",
                       "input[autocomplete='given-name']",
                       "input[name*='first_name' i]"),
    "last_name":      ("#last_name",
                       "input[autocomplete='family-name']",
                       "input[name*='last_name' i]"),
    "preferred_name": ("#preferred_name",
                       "input[id*='preferred' i]"),
    "email":          ("#email", "input[type='email']",
                       "input[name*='email' i]"),
    "phone":          ("#phone", "input[type='tel']",
                       "input[name*='phone' i]"),
    # Greenhouse country combobox -- text input with autocomplete.
    "country":        ("#country",),
    "linkedin":       ("input[name*='linkedin' i]",
                       "input[id*='linkedin' i]"),
}

_RESUME_INPUT_SELECTORS = (
    "input[type='file'][id='resume']",
    "input[type='file'][name*='resume' i]",
    "input[type='file'][id*='resume' i]",
)
_COVER_LETTER_INPUT_SELECTORS = (
    "input[type='file'][id='cover_letter']",
    "input[type='file'][name*='cover_letter' i]",
    "input[type='file'][id*='cover_letter' i]",
)
_SUBMIT_BUTTON_SELECTORS = (
    "input[type='submit'][value*='Submit' i]",
    "button[type='submit']",
    "button:has-text('Submit Application')",
)


class GreenhouseHandler:
    name = "greenhouse"

    async def fill_application(
        self, page, ctx: FillContext,
    ) -> ApplicationResult:
        profile = ctx.profile
        filled: list[str] = []

        if await fill_first_match(
            page, _FIELD_SELECTORS["first_name"], profile.first_name,
        ):
            filled.append("first_name")
        if await fill_first_match(
            page, _FIELD_SELECTORS["last_name"], profile.last_name,
        ):
            filled.append("last_name")
        if await fill_first_match(
            page, _FIELD_SELECTORS["preferred_name"], profile.first_name,
        ):
            filled.append("preferred_name")
        if await fill_first_match(
            page, _FIELD_SELECTORS["email"], profile.email,
        ):
            filled.append("email")
        if await fill_first_match(
            page, _FIELD_SELECTORS["phone"], profile.phone,
        ):
            filled.append("phone")
        if await fill_first_match(
            page, _FIELD_SELECTORS["country"], profile.country,
        ):
            filled.append("country")
        if await fill_first_match(
            page, _FIELD_SELECTORS["linkedin"], profile.linkedin_url,
        ):
            filled.append("linkedin")

        if await upload_first_match(
            page, _RESUME_INPUT_SELECTORS, ctx.resume_path,
        ):
            filled.append("resume")
        if await upload_first_match(
            page, _COVER_LETTER_INPUT_SELECTORS, ctx.cover_letter_path,
        ):
            filled.append("cover_letter")

        answered, skipped = await answer_custom_questions(
            page, ctx.qa_matcher, ctx,
        )

        return ApplicationResult(
            ok=True,
            fields_filled=filled,
            questions_answered=answered,
            questions_skipped=skipped,
        )

    async def submit(self, page) -> SubmissionResult:
        for sel in _SUBMIT_BUTTON_SELECTORS:
            loc = page.locator(sel).first
            try:
                if await loc.count():
                    await loc.click()
                    try:
                        await page.wait_for_load_state(
                            "networkidle", timeout=30_000,
                        )
                    except Exception:
                        pass
                    return SubmissionResult(
                        ok=True, final_url=page.url,
                    )
            except Exception as e:
                logger.warning(
                    "submit click %r failed: %s", sel, e,
                )
        return SubmissionResult(
            ok=False,
            error="No matching submit button found.",
        )

    async def capture_confirmation(
        self, page, output_path,
    ) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(output_path), full_page=True)
        return output_path
