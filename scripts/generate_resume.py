"""Generate a resume prompt tailored to a specific posting.

Usage:
  python scripts/generate_resume.py --profile default --posting 335
  python scripts/generate_resume.py --profile default --posting 335 --no-prompt
  python scripts/generate_resume.py --profile default --posting 335 --skip-gap-analysis

Workflow:
  1. Fetch posting + latest eval_decision from the tracker.
  2. Run PostingGapAnalyzer (single Gemma call, ~16s) — unless
     --skip-gap-analysis.
     - Print summary, warn on stretch (coverage < 50%), list
       undocumented items.
     - In interactive mode (default): ask Y / n / update.
     - With --no-prompt: always proceed.
  3. Build the resume prompt via ResumePromptBuilder (uses gap report).
  4. Write prompt to data/<profile>/resumes/prompts/
     resume_prompt_{posting_id}_{date}.md and print to stdout.
  5. Print copy/paste instructions for Claude + render command.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# The prompt contains unicode (en-dash, arrows) which the Windows
# cp1252 default console encoding can't render. Reconfigure stdout
# to utf-8 with replacement so the prompt prints cleanly.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml

from engine.persistence.tracker import Tracker
from engine.resume.posting_gap_analyzer import (
    PostingGapAnalyzer,
    PostingGapReport,
)
from engine.resume.prompt_builder import ResumePromptBuilder


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
    print(
        f"  COVERED:       {len(report.covered)}"
    )
    print(
        f"  UNDOCUMENTED:  {len(report.undocumented)}"
    )
    print(
        f"  GENUINE GAPS:  {len(report.genuine_gaps)}"
    )
    if report.coverage_score < 0.50:
        print(
            "\n  STRETCH role — consider updating the inventory "
            "before generating the resume."
        )
    if report.undocumented:
        high = [
            u for u in report.undocumented if u.urgency == "high"
        ]
        if high:
            print(
                f"\n  {len(high)} HIGH-urgency undocumented items "
                "to consider adding to career_inventory.md:"
            )
            for u in high[:5]:
                print(f"    - {u.requirement[:60]}")
                print(
                    f"      → suggested update on "
                    f"{u.likely_source}"
                )
    print()


def _confirm_proceed(report: PostingGapReport) -> str:
    """Returns 'proceed', 'abort', or 'update' based on user input."""
    while True:
        try:
            response = input(
                "Proceed with prompt generation? "
                "(Y / n=abort / update=stop & update inventory) : "
            ).strip().lower()
        except EOFError:
            return "proceed"
        if response in ("", "y", "yes"):
            return "proceed"
        if response in ("n", "no"):
            return "abort"
        if response in ("update", "u"):
            return "update"
        print(f"  unrecognized response: {response!r}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--posting", type=int, required=True)
    parser.add_argument(
        "--skip-gap-analysis", action="store_true",
        help="skip the per-posting gap analysis step entirely",
    )
    parser.add_argument(
        "--no-prompt", action="store_true",
        help="non-interactive mode — always proceed without asking",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # 1+2. Fetch posting + eval_decision
    tracker = Tracker(args.profile)
    try:
        posting = tracker.get_opportunity_by_id(args.posting)
        if posting is None:
            print(f"ERROR: posting {args.posting} not found")
            return 1
        eval_decision = tracker.get_latest_evaluation(args.posting)
    finally:
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

    # 3. Gap analysis (optional)
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
            decision = _confirm_proceed(gap_report)
            if decision == "abort":
                print(
                    "Aborted. Update career_inventory.md, then "
                    "re-run."
                )
                return 0
            if decision == "update":
                print(
                    "Interactive inventory update is not yet "
                    "implemented. Edit career_inventory.md by hand, "
                    "re-run scripts/extract_inventory_skills.py, "
                    "then re-run this command."
                )
                return 0

    # 4-5. Inventory + profile config
    inventory_text = _load_inventory_text(args.profile)
    if inventory_extract is None:
        inventory_extract = _load_inventory_extract(args.profile)
    profile_config = _load_profile_config(args.profile)

    # 6. Build prompt
    builder = ResumePromptBuilder()
    prompt = builder.build_prompt(
        posting=posting,
        eval_decision=eval_decision,
        gap_report=gap_report,
        inventory_text=inventory_text,
        inventory_extract=inventory_extract,
        profile_config=profile_config,
    )

    # 7. Write to file
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = (
        PROJECT_ROOT / "data" / args.profile / "resumes" / "prompts"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"resume_prompt_{args.posting}_{today}.md"
    out_path.write_text(prompt, encoding="utf-8")

    # 8-9. Print + instructions
    print()
    print("=" * 60)
    print("RESUME PROMPT (ready for Claude)")
    print("=" * 60)
    print(prompt)
    print()
    print("=" * 60)
    print(f"Prompt written to: {out_path}")
    print("=" * 60)
    print()
    print("NEXT STEPS:")
    print(
        f"  1. Copy the prompt above and paste it into Claude "
        "(claude.ai or via the SDK)."
    )
    print(
        f"  2. Save Claude's markdown response to:"
    )
    drafts_dir = (
        PROJECT_ROOT / "data" / args.profile / "resumes" / "drafts"
    )
    print(
        f"     {drafts_dir / f'resume_draft_{args.posting}_{today}.md'}"
    )
    print(
        f"  3. Render to .docx:"
    )
    print(
        f"     python scripts/render_resume.py "
        f"--draft <path> --posting {args.posting} "
        f"--profile {args.profile}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
