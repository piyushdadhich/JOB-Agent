"""Probe the demographic / self-id inputs that target.fill() can't
populate. Reports readOnly / disabled / role / aria-haspopup so we
know if they're click-triggers, comboboxes, or plain text inputs.

Usage:
  python scripts/probe_eeoc_inputs.py
"""
from __future__ import annotations

import asyncio

from playwright.async_api import async_playwright


URL = "https://job-boards.greenhouse.io/maintainx/jobs/5120026007"

# IDs the dry-run had to skip (multi-select self-id) and the IDs
# it filled (single-select EEOC) -- compare attributes side by side.
PROBE_IDS = [
    "4007799007",        # gender identity (skipped)
    "4007800007",        # racial/ethnic (skipped)
    "4007801007",        # sexual orientation (skipped)
    "4007802007",        # transgender (skipped)
    "4007803007",        # disability (skipped)
    "4007804007",        # veteran (skipped)
    "gender",            # standard EEOC (filled)
    "hispanic_ethnicity",# standard EEOC (filled)
    "veteran_status",    # standard EEOC (filled)
    "disability_status", # standard EEOC (filled)
]

JS_PROBE = r"""
(ids) => {
  return ids.map(id => {
    const el = document.getElementById(id);
    if (!el) return {id, found: false};
    const role = el.getAttribute('role');
    const haspopup = el.getAttribute('aria-haspopup');
    const expanded = el.getAttribute('aria-expanded');
    const owns = el.getAttribute('aria-owns');
    const controls = el.getAttribute('aria-controls');
    const auto = el.getAttribute('autocomplete');
    return {
      id, found: true,
      tag: el.tagName,
      type: el.type || '',
      readOnly: el.readOnly === true,
      disabled: el.disabled === true,
      role,
      ariaHasPopup: haspopup,
      ariaExpanded: expanded,
      ariaOwns: owns,
      ariaControls: controls,
      autocomplete: auto,
      placeholder: el.placeholder || '',
      value: el.value || '',
    };
  });
}
"""


async def main() -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        try:
            print(f"Navigating: {URL}")
            await page.goto(URL, timeout=60_000)
            try:
                await page.wait_for_load_state(
                    "networkidle", timeout=30_000,
                )
            except Exception:
                pass
            await asyncio.sleep(2)

            data = await page.evaluate(JS_PROBE, PROBE_IDS)
            print()
            print("=" * 110)
            for d in data:
                if not d.get("found"):
                    print(f"  {d['id']}  NOT FOUND")
                    continue
                print(
                    f"  id={d['id']:<22} "
                    f"readOnly={d['readOnly']!s:<5} "
                    f"disabled={d['disabled']!s:<5} "
                    f"role={(d['role'] or '-'):<10} "
                    f"haspopup={(d['ariaHasPopup'] or '-'):<10} "
                    f"controls={(d['ariaControls'] or '-')[:25]:<25} "
                    f"auto={(d['autocomplete'] or '-')[:5]}"
                )
            print("=" * 110)
            print()
        finally:
            await context.close()
            await browser.close()
    return 0


if __name__ == "__main__":
    asyncio.run(main())
