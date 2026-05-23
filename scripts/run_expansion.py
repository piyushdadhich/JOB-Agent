"""Spec D1 TASK 5 — CLI for the Discovery Expansion Agent.

Usage:
  python scripts/run_expansion.py [--days 14] [--profile default]

Runs ExpansionOrchestrator against the profile's tracker.db.
Prints a summary to console + writes the full markdown report
to scripts/output/expansion_report_{YYYY-MM-DD}.md.

The report is FOR HUMAN REVIEW. Nothing auto-applies. After
review the user can run scripts/update_discovered_roles.py
(and similar) for accepted suggestions.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml  # noqa: E402

from engine.expansion.orchestrator import ExpansionOrchestrator  # noqa: E402
from engine.matching.scorer import load_inventory_skill_ids  # noqa: E402
from engine.persistence.tracker import Tracker  # noqa: E402

OUTPUT_DIR = PROJECT_ROOT / "scripts" / "output"
SKILL_TAXONOMY = "lightcast"


def _profile_yaml(profile_id: str) -> dict:
    path = PROJECT_ROOT / "config" / "profiles" / f"{profile_id}.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _print_summary(report) -> None:
    print(f"Generated: {report.generated_at.isoformat(timespec='seconds')}")
    print(f"Days analyzed: {report.days_analyzed}")
    print()
    print(f"Title clusters:       {len(report.title_clusters)}")
    new_clusters = [c for c in report.title_clusters
                    if not c.already_in_target]
    print(f"  NEW role types:     {len(new_clusters)}")
    print(f"Deep-target candidates: {len(report.deep_targets)}")
    new_targets = [d for d in report.deep_targets if not d.already_watched]
    print(f"  NOT yet watched:    {len(new_targets)}")
    print(f"Adjacent suggestions: {len(report.adjacent_suggestions)}")
    print(f"Skill gaps:           {len(report.skill_gaps)}")
    print()
    if new_clusters:
        print("New role types found:")
        for c in new_clusters[:10]:
            print(
                f"  - {c.modal_title} ({c.posting_count} postings, "
                f"conf {c.confidence:.2f})"
            )
        print()
    if new_targets:
        print("New deep-target candidates:")
        for d in new_targets[:10]:
            print(
                f"  - {d.company_name}: {d.top_tier_count} TOP_TIER + "
                f"{d.strong_count} STRONG"
            )
        print()


def main(argv=None, *, tracker_override=None) -> int:
    p = argparse.ArgumentParser(
        description="Run the Discovery Expansion Agent."
    )
    p.add_argument("--profile", default="default",
                   help="Profile id (default: default)")
    p.add_argument("--days", type=int, default=14,
                   help="Lookback window in days (default: 14)")
    args = p.parse_args(argv)

    profile_yaml = _profile_yaml(args.profile)

    if tracker_override is not None:
        tracker = tracker_override
    else:
        tracker = Tracker(profile_id=args.profile)

    try:
        inventory = load_inventory_skill_ids(
            tracker, profile_id=args.profile, taxonomy=SKILL_TAXONOMY,
        )
        orch = ExpansionOrchestrator(
            tracker=tracker,
            profile=profile_yaml,
            inventory_skill_ids=inventory,
        )
        report = orch.run(days_back=args.days)
        _print_summary(report)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        date_str = report.generated_at.strftime("%Y-%m-%d")
        out_path = OUTPUT_DIR / f"expansion_report_{date_str}.md"
        out_path.write_text(report.to_markdown(), encoding="utf-8")
        print(f"Full report written to {out_path.relative_to(PROJECT_ROOT)}")
        return 0
    finally:
        if tracker_override is None:
            tracker.close()


if __name__ == "__main__":
    raise SystemExit(main())
