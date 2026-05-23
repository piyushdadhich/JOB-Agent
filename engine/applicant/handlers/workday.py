"""Workday ATS handler.

Multi-step wizard form on {tenant}.wd{N}.myworkdayjobs.com.
Workday requires a user account; the handler PAUSES at the start
and asks the user to sign in (or create an account) in the
visible browser, then proceed.

Wizard steps the handler navigates:
  1. My Information      -- name, email, phone, address, source
  2. My Experience       -- resume upload (Workday auto-parses the
                            rest), education
  3. Application Questions  -- custom screening
  4. Voluntary Self-Identification -- defaults to decline-to-answer
  5. Review & Submit     -- pauses for human review

Workday selectors use data-automation-id attributes that are
remarkably stable across tenants. The handler relies on those
plus standard ARIA roles (role='combobox' for searchable dropdowns).
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


# Stable data-automation-id selectors used by Workday across tenants.
def _da(name: str) -> str:
    return f"[data-automation-id='{name}']"


# Step 1: My Information
_LEGAL_NAME_FIRST = (_da("legalNameSection_firstName"),)
_LEGAL_NAME_LAST  = (_da("legalNameSection_lastName"),)
_EMAIL_INPUT      = (_da("email"), "input[type='email']")
_PHONE_INPUT      = (_da("phone-number"), "input[type='tel']")
_ADDRESS_LINE_1   = (_da("addressSection_addressLine1"),)
_ADDRESS_CITY     = (_da("addressSection_city"),)
_ADDRESS_POSTAL   = (_da("addressSection_postalCode"),)

# Searchable combobox triggers.
_COUNTRY_TRIGGER  = (_da("countryDropdown"),)
_PROVINCE_TRIGGER = (_da("addressSection_countryRegion"),)

# Step 2: My Experience -- resume upload.
_RESUME_FILE_INPUT = (
    _da("file-upload-input-ref"),
    "input[type='file']",
)

# Voluntary self-identification: opt-out option labels we try in order.
_DECLINE_OPTIONS = (
    "I don't wish to answer",
    "Decline to self-identify",
    "Prefer not to say",
    "Prefer not to answer",
    "I don't wish to disclose",
)

_NEXT_BUTTON   = (
    _da("pageFooterNextButton"),
    "button:has-text('Next')",
)
_SAVE_CONTINUE = (
    _da("pageFooterNextButton"),
    "button:has-text('Save and Continue')",
)
_SUBMIT_BUTTON = (
    _da("pageFooterSubmitButton"),
    "button:has-text('Submit')",
)


async def open_combobox_and_pick(page, trigger_selectors, value: str):
    """Workday searchable combobox: click trigger, type, click first
    matching option in the popup."""
    if not value:
        return False
    for sel in trigger_selectors:
        loc = page.locator(sel).first
        try:
            if not await loc.count():
                continue
            await loc.click()
            await loc.fill(value)
            option = page.locator(
                f"[role='option']:has-text('{value}')",
            ).first
            await option.click()
            return True
        except Exception as e:
            logger.debug("combobox %r failed: %s", sel, e)
    return False


async def click_next(page) -> bool:
    seen: set[str] = set()
    for sel_group in (_NEXT_BUTTON, _SAVE_CONTINUE):
        for sel in sel_group:
            if sel in seen:
                continue
            seen.add(sel)
            loc = page.locator(sel).first
            try:
                if await loc.count():
                    await loc.click()
                    try:
                        await page.wait_for_load_state(
                            "networkidle", timeout=20_000,
                        )
                    except Exception:
                        pass
                    return True
            except Exception as e:
                logger.debug("next click %r failed: %s", sel, e)
    return False


async def decline_voluntary_self_id(page) -> int:
    """Pick a decline-to-answer option in every visible select on
    the voluntary self-id page. Returns the count of dropdowns
    that got a decline pick.
    """
    count = 0
    selects = page.locator("select")
    try:
        n = await selects.count()
    except Exception:
        return 0
    for i in range(n):
        sel = selects.nth(i)
        for label in _DECLINE_OPTIONS:
            try:
                await sel.select_option(label=label)
                count += 1
                break
            except Exception:
                continue
    return count


class WorkdayHandler:
    name = "workday"

    async def fill_application(
        self, page, ctx: FillContext,
    ) -> ApplicationResult:
        """Drive the 5-step wizard. apply.py pauses for sign-in
        BEFORE calling this method. fill_application then waits
        for the wizard form to be visible (first_name selector
        as the signal) and proceeds.
        """
        profile = ctx.profile
        filled: list[str] = []
        all_answered: dict = {}
        all_skipped: list[str] = []

        try:
            await page.wait_for_selector(
                _LEGAL_NAME_FIRST[0], timeout=120_000,
            )
        except Exception as e:
            return ApplicationResult(
                ok=False,
                error=(
                    f"Workday wizard never appeared (signed in?): {e}"
                ),
            )

        # --- Step 1: My Information ---
        if await fill_first_match(
            page, _LEGAL_NAME_FIRST, profile.first_name,
        ):
            filled.append("first_name")
        if await fill_first_match(
            page, _LEGAL_NAME_LAST, profile.last_name,
        ):
            filled.append("last_name")
        if await fill_first_match(
            page, _EMAIL_INPUT, profile.email,
        ):
            filled.append("email")
        if await fill_first_match(
            page, _PHONE_INPUT, profile.phone,
        ):
            filled.append("phone")
        if await open_combobox_and_pick(
            page, _COUNTRY_TRIGGER, profile.country,
        ):
            filled.append("country")
        if await fill_first_match(
            page, _ADDRESS_CITY, profile.city,
        ):
            filled.append("city")
        if await open_combobox_and_pick(
            page, _PROVINCE_TRIGGER, profile.province,
        ):
            filled.append("province")
        if await fill_first_match(
            page, _ADDRESS_POSTAL, profile.postal_code,
        ):
            filled.append("postal_code")
        if not await click_next(page):
            return ApplicationResult(
                ok=False,
                fields_filled=filled,
                error="Step 1 -> Next click failed.",
            )

        # --- Step 2: My Experience (resume upload) ---
        try:
            await page.wait_for_load_state(
                "networkidle", timeout=20_000,
            )
        except Exception:
            pass
        if await upload_first_match(
            page, _RESUME_FILE_INPUT, ctx.resume_path,
        ):
            filled.append("resume")
        if not await click_next(page):
            return ApplicationResult(
                ok=False,
                fields_filled=filled,
                error="Step 2 -> Next click failed.",
            )

        # --- Step 3: Application Questions ---
        try:
            await page.wait_for_load_state(
                "networkidle", timeout=20_000,
            )
        except Exception:
            pass
        answered, skipped = await answer_custom_questions(
            page, ctx.qa_matcher, ctx,
        )
        all_answered.update(answered)
        all_skipped.extend(skipped)
        if not await click_next(page):
            return ApplicationResult(
                ok=False,
                fields_filled=filled,
                questions_answered=all_answered,
                questions_skipped=all_skipped,
                error="Step 3 -> Next click failed.",
            )

        # --- Step 4: Voluntary Self-Identification (decline) ---
        try:
            await page.wait_for_load_state(
                "networkidle", timeout=20_000,
            )
        except Exception:
            pass
        declined = await decline_voluntary_self_id(page)
        if declined:
            filled.append(f"voluntary_self_id_declined_{declined}")
        if not await click_next(page):
            # Some tenants skip step 4 entirely.
            logger.info(
                "Step 4 (Voluntary Self-Id) -> Next missing; "
                "may already be on Review page.",
            )

        # --- Step 5: Review & Submit -- caller pauses for review ---
        return ApplicationResult(
            ok=True,
            fields_filled=filled,
            questions_answered=all_answered,
            questions_skipped=all_skipped,
        )

    async def submit(self, page) -> SubmissionResult:
        for sel in _SUBMIT_BUTTON:
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
            error="No matching submit button on Review page.",
        )

    async def capture_confirmation(
        self, page, output_path,
    ) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(output_path), full_page=True)
        return output_path
