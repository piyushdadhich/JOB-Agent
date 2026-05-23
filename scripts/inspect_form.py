"""Open a posting URL in headed Chromium and dump the form structure
so we can tune ATS selectors without guessing.

Usage:
  python scripts/inspect_form.py https://job-boards.greenhouse.io/maintainx/jobs/5120026007

Prints, for every input/textarea/select on the page:
  tag  type  name  id  data-source  aria-label  label-text  for
And for every label:
  text  for  has-input-child
"""
from __future__ import annotations

import asyncio
import sys

from playwright.async_api import async_playwright


JS_DUMP = r"""
() => {
  const fields = [];
  for (const el of document.querySelectorAll('input, textarea, select')) {
    const labelFor = document.querySelector(`label[for='${el.id}']`);
    fields.push({
      tag: el.tagName,
      type: el.type || '',
      name: el.name || '',
      id: el.id || '',
      dataSource: el.getAttribute('data-source') || '',
      ariaLabel: el.getAttribute('aria-label') || '',
      labelByFor: labelFor ? labelFor.textContent.trim().slice(0, 80) : '',
      visible: el.offsetParent !== null,
    });
  }
  const labels = [];
  for (const lab of document.querySelectorAll('label')) {
    labels.push({
      for: lab.getAttribute('for') || '',
      text: lab.textContent.trim().slice(0, 80),
      hasInputChild: !!lab.querySelector('input, textarea, select'),
    });
  }
  return {fields, labels};
}
"""


async def main(url: str) -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        try:
            print(f"Navigating: {url}")
            await page.goto(url, timeout=60_000)
            try:
                await page.wait_for_load_state(
                    "networkidle", timeout=30_000,
                )
            except Exception:
                pass
            await asyncio.sleep(2)  # let any late JS settle

            data = await page.evaluate(JS_DUMP)
            print()
            print("=" * 90)
            print(f"{len(data['fields'])} INPUT/TEXTAREA/SELECT ELEMENTS")
            print("=" * 90)
            for i, f in enumerate(data["fields"]):
                print(
                    f"  [{i:>3}] {f['tag']:<8} type={f['type']:<10} "
                    f"id={(f['id'] or '-')[:25]:<25} "
                    f"name={(f['name'] or '-')[:30]:<30} "
                    f"vis={f['visible']!s:<5} "
                    f"aria={(f['ariaLabel'] or '-')[:30]:<30} "
                    f"label_for={(f['labelByFor'] or '-')[:30]}"
                )
            print()
            print("=" * 90)
            print(f"{len(data['labels'])} LABEL ELEMENTS")
            print("=" * 90)
            for i, lab in enumerate(data["labels"]):
                print(
                    f"  [{i:>3}] for={(lab['for'] or '-')[:25]:<25} "
                    f"hasInputChild={lab['hasInputChild']!s:<5} "
                    f"text={lab['text'][:60]}"
                )
            print()
            input("\nInspect the page in the browser. Press Enter to close: ")
        finally:
            await context.close()
            await browser.close()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/inspect_form.py <URL>")
        sys.exit(1)
    sys.exit(asyncio.run(main(sys.argv[1])))
