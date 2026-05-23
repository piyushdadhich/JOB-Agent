"""Open each EEOC / self-id combobox and dump its actual option
text. Tells us what decline-to-answer phrasing each dropdown uses
so the _DECLINE_FALLBACKS list in _common.py can be tuned.

Usage:
  python scripts/probe_combobox_options.py
"""
from __future__ import annotations

import asyncio

from playwright.async_api import async_playwright


URL = "https://job-boards.greenhouse.io/maintainx/jobs/5120026007"

PROBE_IDS = [
    ("4007799007",        "gender identity (multi)"),
    ("4007800007",        "racial/ethnic (multi)"),
    ("4007801007",        "sexual orientation (multi)"),
    ("4007802007",        "transgender (single)"),
    ("4007803007",        "disability long (single)"),
    ("4007804007",        "veteran long (single)"),
    ("gender",            "gender short (gov EEOC)"),
    ("hispanic_ethnicity","hispanic short (gov EEOC)"),
    ("veteran_status",    "veteran short (gov EEOC)"),
    ("disability_status", "disability short (gov EEOC)"),
]


async def main() -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        from pathlib import Path
        out_path = Path(__file__).resolve().parent / "output" / "combobox_options.txt"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        outf = open(out_path, "w", encoding="utf-8")

        def log(msg: str) -> None:
            print(msg)
            outf.write(msg + "\n")

        try:
            log(f"Navigating: {URL}")
            await page.goto(URL, timeout=60_000)
            try:
                await page.wait_for_load_state(
                    "networkidle", timeout=30_000,
                )
            except Exception:
                pass
            await asyncio.sleep(2)

            for cid, label in PROBE_IDS:
                log("")
                log(f"=== {label} (id={cid}) ===")
                trigger = page.locator(f"[id={cid!r}]").first
                try:
                    if not await trigger.count():
                        log("  not found")
                        continue
                    # Scroll into view + close any stray popup first.
                    try:
                        await page.keyboard.press("Escape")
                    except Exception:
                        pass
                    await trigger.scroll_into_view_if_needed()
                    await trigger.click()
                    await asyncio.sleep(0.6)
                    options = page.locator("[role='option']")
                    n = await options.count()
                    log(f"  ({n} options)")
                    for i in range(n):
                        opt = options.nth(i)
                        text = (await opt.text_content()) or ""
                        text = text.strip()
                        if text:
                            log(f"  - {text!r}")
                    try:
                        await page.keyboard.press("Escape")
                    except Exception:
                        pass
                    await asyncio.sleep(0.3)
                except Exception as e:
                    log(f"  probe failed: {e}")
        finally:
            outf.close()
            print(f"\nFull output written to: {out_path}")
        finally:
            await context.close()
            await browser.close()
    return 0


if __name__ == "__main__":
    asyncio.run(main())
