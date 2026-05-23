"""Lever ATS handler.

Form layout: jobs.lever.co/{slug}/{job-id}/apply

Lever uses a single 'name' field rather than first_name/last_name.
Cover letter is typically pasted into a 'comments' / 'Additional
information' textarea, NOT a file upload (this is the main
divergence from Greenhouse). The handler tries the file-upload
input first; if that's not present it falls back to filling the
textarea with cover_letter_text.
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


_NAME_SELECTORS = (
    "input[name='name']",
    "input[autocomplete='name']",
    "input[placeholder*='Full name' i]",
)
_EMAIL_SELECTORS = (
    "input[name='email']",
    "input[type='email']",
)
_PHONE_SELECTORS = (
    "input[name='phone']",
    "input[type='tel']",
)
_LINKEDIN_SELECTORS = (
    "input[name='urls[LinkedIn]']",
    "input[name*='linkedin' i]",
)
_RESUME_SELECTORS = (
    "input[type='file'][name='resume']",
    "input[type='file'][name*='resume' i]",
)
_COVER_LETTER_FILE_SELECTORS = (
    "input[type='file'][name='cover_letter']",
    "input[type='file'][name*='cover' i]",
)
_COVER_LETTER_TEXT_SELECTORS = (
    "textarea[name='comments']",
    "textarea[name*='additional' i]",
    "textarea[name*='cover' i]",
    "textarea[placeholder*='Additional information' i]",
)
_SUBMIT_SELECTORS = (
    "button[type='submit']:has-text('Submit')",
    "button[type='submit']",
    "button:has-text('Submit application')",
)


class LeverHandler:
    name = "lever"

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
        if await fill_first_match(
            page, _LINKEDIN_SELECTORS, profile.linkedin_url,
        ):
            filled.append("linkedin")

        if await upload_first_match(
            page, _RESUME_SELECTORS, ctx.resume_path,
        ):
            filled.append("resume")

        # Cover letter: file input first, textarea fallback.
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
            error="No matching submit button found.",
        )

    async def capture_confirmation(
        self, page, output_path,
    ) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(output_path), full_page=True)
        return output_path
