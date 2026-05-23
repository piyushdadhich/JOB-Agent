"""Apply to multiple postings in batch.

Usage:
  python scripts/apply_batch.py --profile default --postings 1234,5678
  python scripts/apply_batch.py --profile default --tier STRONG --limit 5
  python scripts/apply_batch.py --profile default --tier TOP_TIER --dry-run

Rate-limited at applicant.daily_application_cap (default 10).
Counts applications submitted today (UTC); aborts at the cap.

Each application opens its own visible browser. Reviewer pauses
for review per posting.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker  # noqa: E402

logger = logging.getLogger(__name__)


def _profile_yaml(profile_id: str) -> dict:
    path = (
        PROJECT_ROOT / "config" / "profiles" / f"{profile_id}.yaml"
    )
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def daily_application_count(tracker) -> int:
    """Count applications submitted today (UTC)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = tracker._query_all(
        "SELECT COUNT(*) AS n FROM applications "
        "WHERE status = 'submitted' "
        "  AND substr(status_updated_at, 1, 10) = ?",
        (today,),
    )
    return int(rows[0]["n"]) if rows else 0


def resolve_posting_ids(tracker, args) -> list[int]:
    """Translate args -> ordered list of opportunity_ids to process."""
    if args.postings:
        return [
            int(p.strip())
            for p in args.postings.split(",")
            if p.strip()
        ]
    if args.tier:
        rows = tracker._query_all(
            "SELECT o.id, latest.fit_score "
            "FROM opportunities o "
            "JOIN ("
            "  SELECT e1.* FROM eval_decisions e1 "
            "  JOIN ("
            "    SELECT opportunity_id, MAX(evaluated_at) AS max_at "
            "    FROM eval_decisions GROUP BY opportunity_id"
            "  ) e2 ON e1.opportunity_id = e2.opportunity_id "
            "     AND e1.evaluated_at = e2.max_at"
            ") latest ON latest.opportunity_id = o.id "
            "WHERE latest.tier = ? "
            "  AND o.status = 'new' "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM applications "
            "    WHERE opportunity_id = o.id"
            "  ) "
            "ORDER BY latest.fit_score DESC, o.date_discovered DESC "
            "LIMIT ?",
            (args.tier, args.limit or 50),
        )
        return [int(r["id"]) for r in rows]
    return []


async def _run_one(profile: str, posting_id: int, dry_run: bool) -> int:
    """Build args namespace and call apply._run for a single posting."""
    from scripts.apply import _run as apply_run
    ns = argparse.Namespace(
        profile=profile, posting=posting_id, dry_run=dry_run,
    )
    return await apply_run(ns)


async def main_async(args) -> int:
    profile_yaml = _profile_yaml(args.profile)
    applicant_cfg = profile_yaml.get("applicant", {}) or {}
    cap = int(applicant_cfg.get("daily_application_cap", 10))

    tracker = Tracker(args.profile)
    try:
        already_today = daily_application_count(tracker)
        budget = max(0, cap - already_today)
        print(
            f"Daily cap: {cap}; submitted today: {already_today}; "
            f"budget remaining: {budget}"
        )
        if budget == 0 and not args.dry_run:
            print("Daily cap reached. Aborting (use --dry-run to preview).")
            return 1

        ids = resolve_posting_ids(tracker, args)
        if not ids:
            print(
                "No postings selected. Use --postings or --tier."
            )
            return 2
    finally:
        tracker.close()

    if not args.dry_run:
        ids = ids[:budget]
    print(f"Applying to {len(ids)} posting(s)...")

    results = {
        "submitted": 0, "errored": 0, "skipped": 0,
    }
    for pid in ids:
        print(f"\n--- POSTING {pid} ---")
        try:
            rc = await _run_one(args.profile, pid, args.dry_run)
            if rc == 0:
                results["submitted"] += 1
            else:
                results["skipped"] += 1
        except Exception as e:
            logger.exception("apply error for %s: %s", pid, e)
            results["errored"] += 1

    print()
    print("=" * 30 + " Batch summary " + "=" * 30)
    print(f"  Submitted:     {results['submitted']}")
    print(f"  Skipped:       {results['skipped']}")
    print(f"  Errored:       {results['errored']}")
    cap_remaining = max(
        0, cap - daily_application_count(Tracker(args.profile)),
    )
    print(f"  Cap remaining: {cap_remaining}")
    print("=" * 75)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--postings", default=None,
        help="comma-separated opportunity_ids to apply to",
    )
    parser.add_argument(
        "--tier", choices=["TOP_TIER", "STRONG"], default=None,
        help="pick from active opps at this tier (latest eval)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="cap for --tier resolution before daily-cap slicing",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
