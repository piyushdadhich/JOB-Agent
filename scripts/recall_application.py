"""Print a past application's full text, by opportunity id.

Usage:
  python scripts/recall_application.py --profile default --posting 1234
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.applicant.storage import recall_application
from engine.persistence.tracker import Tracker


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--posting", type=int, required=True,
        help="opportunity_id to recall",
    )
    args = parser.parse_args(argv)

    tracker = Tracker(args.profile)
    try:
        row = recall_application(
            tracker, opportunity_id=args.posting,
        )
    finally:
        tracker.close()

    if row is None:
        print(
            f"No application found for opportunity {args.posting}."
        )
        return 1

    print(f"Application #{row['id']}")
    print(
        f"  posting:       #{row['opportunity_id']}  "
        f"{row.get('employer','')} -- "
        f"{row.get('opportunity_title','')}"
    )
    print(
        f"  status:        {row['status']} "
        f"(updated {row.get('status_updated_at','')})"
    )
    print(f"  ats_platform:  {row.get('ats_platform')}")
    print(f"  submitted_url: {row.get('submitted_url')}")
    print(f"  screenshot:    {row.get('screenshot_path')}")
    if row.get("screening_answers"):
        print()
        print("Screening answers:")
        for q, a in (row["screening_answers"] or {}).items():
            print(f"  - {q}\n      -> {a}")
    print()
    print("=" * 70)
    print("RESUME")
    print("=" * 70)
    print(row.get("resume_text") or "(no resume_text on file)")
    print()
    print("=" * 70)
    print("COVER LETTER")
    print("=" * 70)
    print(
        row.get("cover_letter_text")
        or "(no cover_letter_text on file)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
