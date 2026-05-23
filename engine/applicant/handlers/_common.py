"""Shared helpers used by per-ATS handlers (Greenhouse, Lever, Ashby).

Three primitives all the handlers need:
  - fill_first_match(page, selectors, value): try each selector in
    order, fill the first one that exists. Returns True on success.
  - upload_first_match(page, selectors, path): same pattern but for
    file inputs via locator.set_input_files.
  - answer_custom_questions(page, qa_matcher, ctx): walk every
    <label>, run text through QAMatcher, fill the linked input by
    `for=` lookup. Routes upload_resume / upload_cover_letter
    strategies to set_input_files.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from engine.applicant.handlers.base import FillContext
from engine.applicant.qa_matcher import QAMatch

logger = logging.getLogger(__name__)


async def fill_first_match(page, selectors, value: str) -> bool:
    """Try each selector in order; fill the first one that exists.
    Returns True if any selector succeeded.
    """
    if not value:
        return False
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if await loc.count():
                await loc.fill(value)
                logger.info(
                    "filled %s = %r", sel, str(value)[:40],
                )
                return True
        except Exception as e:
            logger.warning("fill %r failed: %s", sel, e)
    return False


async def upload_first_match(page, selectors, path) -> bool:
    if path is None:
        return False
    p = Path(path)
    if not p.exists():
        logger.warning("Upload skipped (file missing): %s", p)
        return False
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if await loc.count():
                await loc.set_input_files(str(p))
                logger.info("uploaded %s via %s", p.name, sel)
                return True
        except Exception as e:
            logger.warning("upload %r failed: %s", sel, e)
    return False


async def answer_custom_questions(
    page, qa_matcher, ctx: FillContext,
) -> tuple[dict, list[str]]:
    """Walk every label, run through QAMatcher. Returns
    (answers, skipped) -- answers is question->answer dict,
    skipped is questions that didn't match any pattern.
    """
    answered: dict = {}
    skipped: list[str] = []
    labels = page.locator("label")
    try:
        count = await labels.count()
    except Exception:
        return answered, skipped

    for i in range(count):
        label = labels.nth(i)
        try:
            text = (await label.text_content()) or ""
        except Exception:
            continue
        text = text.strip()
        if not text:
            continue

        # Resolve the target FIRST so we can skip already-filled
        # inputs before burning a Tier 2 cloud call on them.
        try:
            for_attr = await label.get_attribute("for")
        except Exception:
            for_attr = None
        if not for_attr:
            continue
        # Use attribute-selector form -- handles numeric IDs and
        # IDs with special characters that #id-style selectors can't.
        # Greenhouse uses numeric IDs (e.g. id="4007799007") for
        # custom self-id questions which `#4007799007` rejects.
        target = page.locator(f"[id={for_attr!r}]").first
        try:
            if not await target.count():
                # Dangling label (target id no longer in DOM).
                skipped.append(text[:120])
                continue
            tag = (await target.evaluate("el => el.tagName")).lower()
        except Exception:
            skipped.append(text[:120])
            continue

        # Skip if the target input was already filled by a
        # standard-field pass earlier (first_name, email, etc.) or
        # if a previous label-walk iteration set it. Also catches
        # file inputs after a successful set_input_files (input.value
        # returns the path string). Silent -- no skipped append --
        # because the direct fillers already logged + counted these.
        try:
            if tag in ("input", "textarea"):
                current = await target.input_value()
                if current:
                    continue
        except Exception:
            pass

        m: Optional[QAMatch] = qa_matcher.match(text)
        # Tier 2 fallback: if Tier 1 didn't match and a generator is
        # available, ask Gemma + the user to fill the gap.
        gen_answer: Optional[str] = None
        if m is None:
            qa_gen = getattr(ctx, "qa_generator", None)
            if qa_gen is not None:
                try:
                    result = qa_gen.answer(
                        text, posting=ctx.posting,
                    )
                    if result.answer:
                        gen_answer = result.answer
                except Exception as e:
                    logger.warning(
                        "qa_generator failed on %r: %s",
                        text[:60], e,
                    )
            if gen_answer is None:
                skipped.append(text[:120])
                continue

        # If Tier 2 produced an answer, fill the input directly
        # (text only -- generator can't drive selects sanely).
        if gen_answer is not None:
            try:
                if tag == "select":
                    skipped.append(text[:120])
                else:
                    await target.fill(gen_answer)
                    answered[text] = gen_answer
            except Exception as e:
                logger.warning(
                    "tier2 fill %r failed: %s", text[:60], e,
                )
                skipped.append(text[:120])
            continue

        # Resolve strategy -> concrete value.
        if m.strategy == "upload_resume":
            try:
                await target.set_input_files(str(ctx.resume_path))
                answered[text] = f"<uploaded {ctx.resume_path.name}>"
            except Exception as e:
                logger.warning(
                    "resume upload to %r failed: %s", text, e,
                )
                skipped.append(text[:120])
            continue
        if m.strategy == "upload_cover_letter":
            try:
                await target.set_input_files(
                    str(ctx.cover_letter_path),
                )
                answered[text] = (
                    f"<uploaded {ctx.cover_letter_path.name}>"
                )
            except Exception as e:
                logger.warning(
                    "cover letter upload to %r failed: %s", text, e,
                )
                skipped.append(text[:120])
            continue
        if m.answer is None:
            skipped.append(text[:120])
            continue

        try:
            if tag == "select":
                await target.select_option(label=m.answer)
                answered[text] = m.answer
            else:
                # Detect combobox-style inputs (role='combobox' with
                # an aria-haspopup popup). Greenhouse uses these for
                # all demographic dropdowns and country selectors;
                # plain target.fill() types the text but doesn't
                # commit the option. Need click -> type -> pick.
                role = None
                try:
                    role = await target.evaluate(
                        "el => el.getAttribute('role')",
                    )
                except Exception:
                    pass
                if role == "combobox":
                    if not await _combobox_pick(
                        page, target, m.answer,
                    ):
                        # Fall back to plain fill so the user can
                        # at least see what we tried to enter.
                        await target.fill(m.answer)
                    answered[text] = m.answer
                else:
                    await target.fill(m.answer)
                    answered[text] = m.answer
        except Exception as e:
            logger.warning("answer %r failed: %s", text[:60], e)
            skipped.append(text[:120])
    return answered, skipped


_DECLINE_FALLBACKS = (
    "I don't wish to answer",
    "I do not wish to answer",
    "I don't want to answer",
    "I do not want to answer",
    "Decline to self-identify",
    "Decline to identify",
    "Decline to answer",
    "Decline to disclose",
    "Decline to state",
    "Prefer not to say",
    "Prefer not to answer",
    "Prefer not to disclose",
    "Prefer not to identify",
    "I don't wish to disclose",
    "I do not wish to disclose",
    "Do not wish to answer",
    "Do not wish to disclose",
)


async def _combobox_pick(page, target, value: str) -> bool:
    """Click a combobox trigger, type to filter, click the matching
    option in the popup. Returns True on success, False if no
    popup option could be located.

    Tries the requested value first, then -- if the value is one
    of the standard decline-to-answer phrasings -- iterates the
    other common phrasings the form might use. Greenhouse, Workday,
    and Indeed all render popup options as role='option' nodes.
    """
    candidates = [value]
    if value in _DECLINE_FALLBACKS:
        candidates.extend(p for p in _DECLINE_FALLBACKS if p != value)

    try:
        await target.click()
    except Exception as e:
        logger.warning(
            "combobox click %r failed: %s", value[:40], e,
        )
        return False

    try:
        await target.fill(value)
    except Exception:
        pass  # some comboboxes don't accept .fill() before pick

    for phrase in candidates:
        option = page.locator(
            f"[role='option']:has-text({phrase!r})"
        ).first
        try:
            await option.click(timeout=1_500)
            return True
        except Exception:
            continue

    logger.warning(
        "combobox pick %r failed: no option matched any of %s",
        value[:40], candidates,
    )
    return False
