"""Spec 9 TASK 2 — multi-step form helpers.

Some ATS forms span multiple pages (Workday, Greenhouse's
multi-section flows, Lever's review page). After filling the
visible fields, the filler clicks "Next" / "Continue", waits for
navigation to settle, and resumes filling.

This module is just selector + click helpers — the LocalFormFiller
calls into it after every batch of fills.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional


# Button-text patterns we'll click. Ordered by specificity: a
# button labelled "Submit Application" wins over a plain "Submit".
_NEXT_PATTERNS = (
    r"^next$",
    r"^continue$",
    r"^next step$",
    r"^continue to.*",
    r"^proceed$",
    r"^save & continue$",
    r"^review.*",
)


_NEXT_BUTTON_SELECTORS = [
    f"button:has-text('{p.strip('^$')}'):not([disabled])"
    for p in _NEXT_PATTERNS
]


async def find_next_button(page) -> Optional[object]:
    """Return the most-specific 'Next' / 'Continue' locator, or None."""
    for sel in _NEXT_BUTTON_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                return loc
        except Exception:
            continue
    return None


async def advance(
    page,
    *,
    settle_ms: int = 600,
    waiter: Optional[Callable[[int], Awaitable[None]]] = None,
) -> bool:
    """Click the Next button if present, wait for the page to settle.

    Returns True iff a click happened. `waiter` defaults to
    ``page.wait_for_timeout`` but is injectable for tests.
    """
    btn = await find_next_button(page)
    if btn is None:
        return False
    await btn.click()
    if waiter is None:
        waiter = page.wait_for_timeout
    await waiter(settle_ms)
    return True
