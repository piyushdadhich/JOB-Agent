"""Indeed Easy Apply handler.

URL pattern: indeed.com/viewjob?jk=... or /applystart?jk=...
The form is hosted ON indeed.com (not redirected to the company
career site). Requires an active Indeed session; the agent
pauses for the user to log in before fill_application runs.

Single-page form (most postings) with:
  - single 'name' input (full name like Lever)
  - email + phone (often pre-filled from the Indeed account)
  - resume upload (or "Use existing resume" radio)
  - optional cover letter (text or file)
  - custom screening questions (varies)

Submit button is labelled "Continue" on the first page if the
posting has multiple steps, "Submit your application" otherwise.
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


# Indeed selectors. Indeed uses both id="input-applicant.X" patterns
# and data-testid attributes; tolerate both.
_NAME_SELECTORS = (
    "input[id='input-applicant.name']",
    "input[name='applicant.name']",
    "input[autocomplete='name']",
)
_EMAIL_SELECTORS = (
    "input[id='input-applicant.email']",
    "input[name='applicant.email']",
    "input[type='email']",
)
_PHONE_SELECTORS = (
    "input[id='input-applicant.phoneNumber']",
    "input[name='applicant.phoneNumber']",
    "input[type='tel']",
)
_RESUME_SELECTORS = (
    "input[type='file'][accept*='pdf']",
    "input[type='file']",
)
_COVER_LETTER_FILE_SELECTORS = (
    "input[type='file'][name*='cover' i]",
)
_COVER_LETTER_TEXT_SELECTORS = (
    "textarea[id*='cover' i]",
    "textarea[name*='cover' i]",
    "textarea[placeholder*='cover' i]",
)
# Indeed wraps the apply button in either a 'Continue' or 'Submit
# your application' label depending on posting complexity.
_SUBMIT_SELECTORS = (
    "button:has-text('Submit your application')",
    "button:has-text('Submit application')",
    "button[data-testid*='IndeedApplyButton']",
    "button:has-text('Continue')",
    "button[type='submit']",
)


class IndeedHandler:
    name = "indeed"

    async def fill_application(
        self, page, ctx: FillContext,
    ) -> ApplicationResult:
        profile = ctx.profile
        filled: list[str] = []

        if await fill_first_match(
            page, _NAME_SELECTORS, profile.full_name,
        ):
            filled.append("name")
        if await fill_first_match(
            page, _EMAIL_SELECTORS, profile.email,
        ):
            filled.append("email")
        if await fill_first_match(
            page, _PHONE_SELECTORS, profile.phone,
        ):
            filled.append("phone")
        if await upload_first_match(
            page, _RESUME_SELECTORS, ctx.resume_path,
        ):
            filled.append("resume")

        if await upload_first_match(
            page, _COVER_LETTER_FILE_SELECTORS, ctx.cover_letter_path,
        ):
            filled.append("cover_letter_file")
        elif await fill_first_match(
            page, _COVER_LETTER_TEXT_SELECTORS, ctx.cover_letter_text,
        ):
            filled.append("cover_letter_text")

        answered, skipped = await answer_custom_questions(
            page, ctx.qa_matcher, ctx,
        )
        return ApplicationResult(
            ok=True, fields_filled=filled,
            questions_answered=answered, questions_skipped=skipped,
        )

    async def submit(self, page) -> SubmissionResult:
        for sel in _SUBMIT_SELECTORS:
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
            error=(
                "No matching Continue/Submit button on "
                "Indeed apply page."
            ),
        )

    async def capture_confirmation(
        self, page, output_path,
    ) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(output_path), full_page=True)
        return output_path
