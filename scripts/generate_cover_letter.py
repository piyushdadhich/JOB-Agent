"""Generate a cover letter prompt tailored to a specific posting.

Usage:
  python scripts/generate_cover_letter.py --profile default --posting 335
  python scripts/generate_cover_letter.py --profile default --posting 335 --no-prompt
  python scripts/generate_cover_letter.py --profile default --posting 335 --skip-gap-analysis

Same workflow as generate_resume.py: fetch posting + eval, run gap
analysis (unless --skip-gap-analysis), confirm with user (unless
--no-prompt), assemble prompt, write to data/<profile>/resumes/
prompts/cover_letter_prompt_{posting_id}_{date}.md, print
copy-paste instructions.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Reconfigure stdout to utf-8 so the en-dash / arrow / em-dash
# characters in the prompt don't crash on Windows cp1252.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml

from engine.persistence.tracker import Tracker
from engine.resume.cover_letter_prompt_builder import (
    CoverLetterPromptBuilder,
)
from engine.resume.posting_gap_analyzer import (
    PostingGapAnalyzer,
    PostingGapReport,
)


def _load_inventory_text(profile_id: str) -> str:
    path = (
        PROJECT_ROOT / "source_materials" / profile_id /
        "career_inventory.md"
    )
    if not path.exists():
        raise SystemExit(f"career_inventory.md not found: {path}")
    return path.read_text(encoding="utf-8")


def _load_inventory_extract(profile_id: str) -> dict:
    path = PROJECT_ROOT / "data" / profile_id / "inventory_extract.json"
    if not path.exists():
        raise SystemExit(f"inventory_extract.json not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_profile_config(profile_id: str) -> dict:
    path = PROJECT_ROOT / "config" / "profiles" / f"{profile_id}.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _print_gap_summary(report: PostingGapReport) -> None:
    print()
    print("=" * 60)
    print(
        f"GAP ANALYSIS — coverage {report.coverage_score * 100:.0f}% — "
        f"readiness: {report.interview_readiness}"
    )
    print("=" * 60)
    print(f"  COVERED:       {len(report.covered)}")
    print(f"  UNDOCUMENTED:  {len(report.undocumented)}")
    print(f"  GENUINE GAPS:  {len(report.genuine_gaps)}")
    if report.genuine_gaps:
        critical = [
            g for g in report.genuine_gaps if g.impact == "critical"
        ]
        target = (critical or report.genuine_gaps)[0]
        print()
        print(
            f"  Most-visible gap to address in paragraph 3: "
            f"{target.requirement[:80]}"
        )
    print()


def _confirm_proceed() -> str:
    while True:
        try:
            response = input(
                "Proceed with cover letter prompt generation? "
                "(Y / n=abort) : "
            ).strip().lower()
        except EOFError:
            return "proceed"
        if response in ("", "y", "yes"):
            return "proceed"
        if response in ("n", "no"):
            return "abort"
        print(f"  unrecognized response: {response!r}")


def main(argv=None, *, tracker_override=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--posting", type=int, required=True)
    parser.add_argument(
        "--skip-gap-analysis", action="store_true",
        help="skip the per-posting gap analysis step entirely",
    )
    parser.add_argument(
        "--no-prompt", action="store_true",
        help="non-interactive mode — always proceed",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if tracker_override is not None:
        tracker = tracker_override
        owns_tracker = False
    else:
        tracker = Tracker(args.profile)
        owns_tracker = True

    try:
        posting = tracker.get_opportunity_by_id(args.posting)
        if posting is None:
            print(f"ERROR: posting {args.posting} not found")
            return 1
        eval_decision = tracker.get_latest_evaluation(args.posting)
    finally:
        if owns_tracker:
            tracker.close()

    print(
        f"Posting #{args.posting}: "
        f"{posting.get('title')!r} at {posting.get('employer')!r}"
    )
    if eval_decision:
        print(
            f"  Latest eval: tier={eval_decision.get('tier')}, "
            f"fit_score={eval_decision.get('fit_score')}"
        )

    gap_report: PostingGapReport | None = None
    inventory_extract: dict | None = None
    if not args.skip_gap_analysis:
        inventory_extract = _load_inventory_extract(args.profile)
        analyzer = PostingGapAnalyzer()
        print(
            "\nRunning gap analysis "
            "(single Gemma 4 E4B call, ~16s)..."
        )
        gap_report = analyzer.analyze(
            posting, inventory_extract, eval_decision,
        )
        _print_gap_summary(gap_report)

        if not args.no_prompt:
            decision = _confirm_proceed()
            if decision == "abort":
                print("Aborted.")
                return 0

    inventory_text = _load_inventory_text(args.profile)
    if inventory_extract is None:
        inventory_extract = _load_inventory_extract(args.profile)
    profile_config = _load_profile_config(args.profile)

    builder = CoverLetterPromptBuilder()
    prompt = builder.build_prompt(
        posting=posting,
        eval_decision=eval_decision,
        gap_report=gap_report,
        inventory_text=inventory_text,
        inventory_extract=inventory_extract,
        profile_config=profile_config,
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = (
        PROJECT_ROOT / "data" / args.profile / "resumes" / "prompts"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / (
        f"cover_letter_prompt_{args.posting}_{today}.md"
    )
    out_path.write_text(prompt, encoding="utf-8")

    print()
    print("=" * 60)
    print("COVER LETTER PROMPT (ready for Claude)")
    print("=" * 60)
    print(prompt)
    print()
    print("=" * 60)
    print(f"Prompt written to: {out_path}")
    print("=" * 60)
    print()
    print("NEXT STEPS:")
    print(
        "  1. Copy the prompt above and paste it into Claude."
    )
    print("  2. Save Claude's markdown response to:")
    drafts_dir = (
        PROJECT_ROOT / "data" / args.profile / "resumes" / "drafts"
    )
    print(
        f"     {drafts_dir / f'cl_draft_{args.posting}_{today}.md'}"
    )
    print("  3. Render to .docx:")
    print(
        f"     python scripts/render_cover_letter.py "
        f"--draft <path> --posting {args.posting} "
        f"--profile {args.profile}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
