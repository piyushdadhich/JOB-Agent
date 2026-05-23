"""Smart application form filler using local Gemma 4 E4B.

Usage:
  python scripts/apply_local.py <url>
  python scripts/apply_local.py <url> --resume path/to/resume.docx
  python scripts/apply_local.py <url> --headless    (for testing)
  python scripts/apply_local.py <url> --dry-run     (detect only)

Requires:
  - Ollama running on localhost:11434 with gemma4:e4b loaded
  - config/profiles/<profile>_applicant.yaml populated
  - Playwright Chromium installed (`playwright install chromium`)

The browser stays open and pauses for human review after filling.
This tool NEVER clicks submit -- the user does that manually after
verifying every field.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.applicant.local_filler import LocalFormFiller
from engine.applicant.profile import load_applicant_profile
from engine.applicant.qa_matcher import QAMatcher


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("url", help="Job application page URL")
    ap.add_argument("--resume", help="Path to resume file to upload")
    ap.add_argument("--profile", default="default")
    ap.add_argument("--model", default="gemma4:e4b")
    ap.add_argument("--headless", action="store_true",
                    help="Run browser headless (no window)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Detect fields and print them; do not fill")
    args = ap.parse_args()

    profile = load_applicant_profile(args.profile)
    missing = profile.required_fields_missing()
    if missing:
        print(f"WARNING: profile missing required fields: {missing}")

    qa = QAMatcher.from_yaml(profile=profile)
    filler = LocalFormFiller(
        profile=profile, qa_matcher=qa, model=args.model,
    )

    # Lazy-import Playwright so --help doesn't pay for it.
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        try:
            page = browser.new_page()
            page.goto(args.url, wait_until="networkidle", timeout=30000)
            time.sleep(2)  # let JS finish

            if args.dry_run:
                detected = filler.detect_fields(page)
                print(f"\nDetected {len(detected)} fields:")
                for d in detected:
                    opts = (
                        f" [{', '.join(d.options[:5])}]"
                        if d.options else ""
                    )
                    req = " (required)" if d.required else ""
                    print(f"  [{d.field_type}] {d.label}{opts}{req}")
                return 0

            t0 = time.perf_counter()
            result = filler.run(page, resume_path=args.resume)
            elapsed = time.perf_counter() - t0

            print(f"\nDetected: {result['detected']}, "
                  f"Filled: {len(result['filled'])}, "
                  f"Skipped: {len(result['skipped'])}, "
                  f"Time: {elapsed:.1f}s")
            for f in result["filled"]:
                print(f"  + [{f['source']}] {f['label']}: {f['value']!r}")
            for f in result["skipped"]:
                print(f"  - {f['label']}: {f['error']}")

            if not args.headless:
                print("\n" + "=" * 60)
                print("REVIEW THE FORM. Press Enter to close browser.")
                print("DO NOT CLICK SUBMIT until you have reviewed.")
                print("=" * 60)
                try:
                    input()
                except (EOFError, KeyboardInterrupt):
                    pass
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
