"""Render a cover letter markdown draft into ATS-friendly .docx.

Usage:
  python scripts/render_cover_letter.py --draft <path-to-md> \\
      --posting 335 --profile default

The candidate name is taken from the profile YAML's display_name
field; the file name follows
  <FirstName><LastName>CoverLetter<Company><YYYYMMDD>.docx
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

import yaml

from engine.persistence.tracker import Tracker
from engine.resume.docx_renderer import ATSResumeRenderer


def _profile_display_name(profile_id: str) -> str:
    path = PROJECT_ROOT / "config" / "profiles" / f"{profile_id}.yaml"
    if not path.exists():
        return profile_id
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return cfg.get("display_name") or profile_id


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", required=True)
    parser.add_argument("--posting", type=int, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output-dir", default=None)
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

    candidate_name = _profile_display_name(args.profile)
    output_dir = (
        Path(args.output_dir) if args.output_dir
        else PROJECT_ROOT / "data" / args.profile / "resumes" / "generated"
    )
    renderer = ATSResumeRenderer()
    out_path = renderer.render_cover_letter(
        content, posting, candidate_name, output_dir,
    )

    print(f"Cover letter written to: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
