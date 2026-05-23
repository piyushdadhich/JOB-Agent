"""Render a resume markdown draft into ATS-friendly .docx.

Usage:
  python scripts/render_resume.py --draft <path-to-md> \\
      --posting 335 --profile default

Reads Claude's markdown response (the OUTPUT_TEMPLATE structure
from generate_resume.py) and renders to:
  data/<profile>/resumes/generated/
    <FirstName><LastName>Resume<Company><YYYYMMDD>.docx
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker
from engine.resume.docx_renderer import ATSResumeRenderer


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", required=True,
                        help="path to the markdown draft file")
    parser.add_argument("--posting", type=int, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--output-dir", default=None,
        help="override output dir (default: data/<profile>/resumes/generated)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    draft_path = Path(args.draft)
    if not draft_path.exists():
        print(f"ERROR: draft not found: {draft_path}")
        return 1
    content = draft_path.read_text(encoding="utf-8")

    tracker = Tracker(args.profile)
    try:
        posting = tracker.get_opportunity_by_id(args.posting)
    finally:
        tracker.close()
    if posting is None:
        print(f"ERROR: posting {args.posting} not found")
        return 1

    output_dir = (
        Path(args.output_dir) if args.output_dir
        else PROJECT_ROOT / "data" / args.profile / "resumes" / "generated"
    )
    renderer = ATSResumeRenderer()
    out_path = renderer.render_resume(content, posting, output_dir)

    print(f"Resume written to: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
