"""Spec 9 TASK 3 — CAPTCHA detection.

The LocalFormFiller calls :func:`detect_captcha` after each page
transition. When something CAPTCHA-shaped is on the page, the
filler pauses, prints a "solve the CAPTCHA, then press Enter"
banner, and yields control to the user.

Detection is selector-only — we don't try to recognise CAPTCHA
images, we just look for the iframes / containers the major
providers use. Coverage:

  Google reCAPTCHA v2 / v3   — iframe src ∈ {google.com/recaptcha/*}
  hCaptcha                   — iframe src ∈ {hcaptcha.com/captcha*}
  Cloudflare Turnstile       — iframe src ∈ {challenges.cloudflare.com/*}
"""
from __future__ import annotations

import re
from dataclasses import dataclass


_CAPTCHA_PATTERNS = (
    (re.compile(r"google\.com/recaptcha", re.I), "reCAPTCHA"),
    (re.compile(r"hcaptcha\.com", re.I),          "hCaptcha"),
    (re.compile(r"challenges\.cloudflare\.com", re.I), "Cloudflare Turnstile"),
)


@dataclass(frozen=True)
class CaptchaDetection:
    present: bool
    provider: str | None
    detail: str | None = None


def detect_from_html(html: str) -> CaptchaDetection:
    """Synchronous detection against a raw HTML string. Used by
    worksheet pre-fetch + tests."""
    for pattern, name in _CAPTCHA_PATTERNS:
        if pattern.search(html or ""):
            return CaptchaDetection(
                present=True, provider=name,
                detail=f"matched {pattern.pattern!r}",
            )
    return CaptchaDetection(present=False, provider=None)


async def detect_on_page(page) -> CaptchaDetection:
    """Async Playwright variant: queries the live page for the
    typical iframe selectors."""
    # Locator-based check is cheaper than serialising the whole DOM.
    selectors = [
        "iframe[src*='google.com/recaptcha']",
        "iframe[src*='hcaptcha.com']",
        "iframe[src*='challenges.cloudflare.com']",
        "div.g-recaptcha",
        "div.h-captcha",
        "div.cf-turnstile",
    ]
    for sel in selectors:
        try:
            count = await page.locator(sel).count()
        except Exception:
            count = 0
        if count:
            return CaptchaDetection(
                present=True,
                provider="reCAPTCHA" if "recaptcha" in sel
                          else "hCaptcha" if "hcaptcha" in sel
                          else "Cloudflare Turnstile",
                detail=f"selector {sel!r} matched ({count})",
            )
    return CaptchaDetection(present=False, provider=None)
