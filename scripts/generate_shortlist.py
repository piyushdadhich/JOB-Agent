"""Generate a daily shortlist markdown file from tracked opportunities.

Usage:
    python scripts\\generate_shortlist.py
    python scripts\\generate_shortlist.py --days 14
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.persistence.tracker import Tracker


def _project_root() -> Path:
    load_dotenv()
    root = os.getenv("PROJECT_ROOT")
    return Path(root) if root else Path(__file__).resolve().parent.parent


def _load_opportunities(tracker: Tracker, days: int) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    # list_opportunities goes through the JOIN-based read so legacy column
    # aliases (employer, url, fit_score, sector) are populated.
    rows = tracker.list_opportunities(limit=10000)
    return [r for r in rows if r.get("date_discovered", "") >= cutoff]


def _sector_label(opp: dict) -> str:
    return opp.get("sector") or "unclassified"


def _sort_key(opp: dict) -> tuple:
    """Sort by fit_score descending."""
    score = opp.get("fit_score") or 0
    return (-score,)


def generate_shortlist(tracker: Tracker, days: int = 7) -> str:
    """Return the shortlist as a markdown string."""
    opps = _load_opportunities(tracker, days)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Stats
    total = len(opps)
    by_sector: dict[str, int] = defaultdict(int)
    by_source: dict[str, int] = defaultdict(int)
    for o in opps:
        by_sector[_sector_label(o)] += 1
        by_source[o.get("source") or "unknown"] += 1

    lines: list[str] = []
    lines.append(f"# Daily Shortlist \u2014 {today}")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- New opportunities this week: {total}")
    sector_parts = [f"{k.replace('_', ' ').title()} {v}"
                    for k, v in sorted(by_sector.items())]
    lines.append(f"- By sector: {' | '.join(sector_parts)}")
    source_parts = [f"{k} {v}" for k, v in sorted(by_source.items())]
    lines.append(f"- By source: {' | '.join(source_parts)}")
    lines.append("")

    # Top opportunities grouped by sector
    sorted_opps = sorted(opps, key=_sort_key)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for o in sorted_opps:
        grouped[_sector_label(o)].append(o)

    lines.append("## Top Opportunities")
    lines.append("")
    for sector, sector_opps in sorted(grouped.items()):
        label = sector.replace("_", " ").title()
        lines.append(f"### {label}")
        for i, o in enumerate(sector_opps, 1):
            title = o.get("title") or "Untitled"
            employer = o.get("employer") or "Unknown"
            location = o.get("location") or "n/a"
            score = o.get("fit_score")
            score_str = f"{score}/10" if score is not None else "n/a"
            source = o.get("source") or "unknown"
            url = o.get("url") or ""
            lines.append(f"{i}. **{title}** \u2014 {employer} ({location})")
            lines.append(f"   - Fit: {score_str} | Source: {source}")
            if url:
                lines.append(f"   - URL: {url}")
        lines.append("")

    # Full list sorted by fit score
    lines.append("## All New Opportunities")
    lines.append("")
    all_sorted = sorted(opps, key=lambda o: -(o.get("fit_score") or 0))
    for i, o in enumerate(all_sorted, 1):
        title = o.get("title") or "Untitled"
        employer = o.get("employer") or "Unknown"
        score = o.get("fit_score")
        score_str = f"{score}/10" if score is not None else "n/a"
        source = o.get("source") or "unknown"
        lines.append(f"{i}. [{score_str}] {title} \u2014 {employer} ({source})")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate daily shortlist from tracked opportunities.")
    parser.add_argument("--days", type=int, default=7,
                        help="Look back this many days (default 7).")
    args = parser.parse_args()

    root = _project_root()
    out_dir = root / "output" / "daily_shortlist"
    out_dir.mkdir(parents=True, exist_ok=True)

    tracker = Tracker(profile_id="default")
    try:
        md = generate_shortlist(tracker, days=args.days)
    finally:
        tracker.close()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = out_dir / f"{today}.md"
    out_path.write_text(md, encoding="utf-8")
    print(f"Shortlist written to {out_path}")
    print(f"({md.count(chr(10))} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
