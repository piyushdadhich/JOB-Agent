"""Analyze a posting's requirements against the career inventory.

Usage:
  python scripts/analyze_posting_gaps.py --profile default --posting 335
  python scripts/analyze_posting_gaps.py --profile default --posting 335 \\
        --output-dir data/default/gap_reports

Runs a single Gemma 4 E4B call against the local Ollama service.
Requires: ollama running, gemma4:e4b loaded.

Output:
  Markdown report to stdout AND to data/{profile}/gap_reports/
  gap_analysis_{posting_id}_{date}.md
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker
from engine.resume.posting_gap_analyzer import (
    PostingGapAnalyzer,
    PostingGapReport,
)


def render_report_md(report: PostingGapReport) -> str:
    """Render the gap report as a markdown document."""
    lines: list[str] = []
    lines.append(
        f"# Gap Analysis: {report.posting_title} at {report.employer}"
    )
    lines.append("")
    lines.append(
        f"**Coverage Score:** {report.coverage_score * 100:.0f}% — "
        f"**Interview Readiness:** {report.interview_readiness}"
    )
    lines.append("")
    lines.append(
        f"_Posting #{report.posting_id} — Generated "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_"
    )
    lines.append("")

    # COVERED
    lines.append(f"## COVERED ({len(report.covered)})")
    lines.append("")
    if not report.covered:
        lines.append("(none)")
    else:
        lines.append(
            "These requirements are clearly documented in your inventory."
        )
        lines.append("")
        lines.append(
            "| Requirement | Evidence | Source Role | Match Type |"
        )
        lines.append("|---|---|---|---|")
        for item in report.covered:
            req = (item.requirement or "")[:80].replace("|", "\\|")
            ev = (item.inventory_evidence or "")[:80].replace("|", "\\|")
            lines.append(
                f"| {req} | {ev} | {item.inventory_role_id} | "
                f"{item.strength} |"
            )
    lines.append("")

    # UNDOCUMENTED
    lines.append(
        f"## UNDOCUMENTED ({len(report.undocumented)}) — "
        f"Update inventory before applying"
    )
    lines.append("")
    if not report.undocumented:
        lines.append("(none)")
    else:
        lines.append(
            "You probably have this experience but it is not yet "
            "in your inventory."
        )
        lines.append("")
        lines.append(
            "| Requirement | Likely Source | Suggested Update | Urgency |"
        )
        lines.append("|---|---|---|---|")
        for item in report.undocumented:
            req = (item.requirement or "")[:80].replace("|", "\\|")
            upd = (item.suggested_update or "")[:80].replace("|", "\\|")
            lines.append(
                f"| {req} | {item.likely_source} | {upd} | "
                f"{item.urgency.upper()} |"
            )
    lines.append("")

    # GENUINE GAPS
    lines.append(
        f"## GENUINE GAPS ({len(report.genuine_gaps)}) — "
        f"Address in cover letter"
    )
    lines.append("")
    if not report.genuine_gaps:
        lines.append("(none)")
    else:
        lines.append("Real gaps. Mitigate, don't hide.")
        lines.append("")
        lines.append("| Requirement | Impact | Mitigation Strategy |")
        lines.append("|---|---|---|")
        for item in report.genuine_gaps:
            req = (item.requirement or "")[:80].replace("|", "\\|")
            mit = (item.mitigation or "")[:120].replace("|", "\\|")
            lines.append(f"| {req} | {item.impact} | {mit} |")
    lines.append("")

    # ACTIONS
    lines.append("## RECOMMENDED ACTIONS")
    lines.append("")
    if report.undocumented:
        high = [u for u in report.undocumented if u.urgency == "high"]
        if high:
            lines.append(
                f"- Update career_inventory.md "
                f"({len(high)} HIGH-urgency items)"
            )
        else:
            lines.append(
                "- Review undocumented items; update inventory if needed"
            )
        lines.append(
            "- Re-run extraction: "
            "`python scripts/extract_inventory_skills.py`"
        )
        lines.append(
            "- Then generate resume: "
            "`python scripts/generate_resume.py "
            f"--profile <profile> --posting {report.posting_id}`"
        )
    else:
        lines.append("Inventory is aligned with this posting. Proceed to:")
        lines.append(
            f"- `python scripts/generate_resume.py "
            f"--profile <profile> --posting {report.posting_id}`"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--posting", type=int, required=True)
    parser.add_argument(
        "--output-dir", default=None,
        help="override output dir (default: data/<profile>/gap_reports)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    inventory_path = (
        PROJECT_ROOT / "data" / args.profile / "inventory_extract.json"
    )
    if not inventory_path.exists():
        print(f"ERROR: inventory extract not found: {inventory_path}")
        return 1
    inventory_extract = json.loads(
        inventory_path.read_text(encoding="utf-8"),
    )

    tracker = Tracker(args.profile)
    try:
        posting = tracker.get_opportunity_by_id(args.posting)
        if posting is None:
            print(f"ERROR: posting {args.posting} not found in tracker")
            return 1
        eval_decision = tracker.get_latest_evaluation(args.posting)
    finally:
        tracker.close()

    print(
        f"Analyzing posting {args.posting}: "
        f"{posting.get('title')!r} at {posting.get('employer')!r}"
    )
    print("(single Gemma 4 E4B call, ~16s)")

    analyzer = PostingGapAnalyzer()
    report = analyzer.analyze(posting, inventory_extract, eval_decision)

    md = render_report_md(report)
    print()
    print(md)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_dir = (
        Path(args.output_dir) if args.output_dir
        else PROJECT_ROOT / "data" / args.profile / "gap_reports"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / (
        f"gap_analysis_{args.posting}_{today}.md"
    )
    out_path.write_text(md, encoding="utf-8")
    print(f"\nReport written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
