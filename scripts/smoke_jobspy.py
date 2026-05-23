"""Live smoke test for JobSpyClient. Hits Indeed and persists results.

Usage:
  python scripts/smoke_jobspy.py
  python scripts/smoke_jobspy.py --cities toronto
  python scripts/smoke_jobspy.py --profile default --cities toronto,calgary
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.jobspy_client import JobSpyClient
from engine.persistence.opportunities import (
    IncompleteRecordError,
    persist_record,
)
from engine.persistence.tracker import Tracker
from engine.profiles.loader import load_profile_from_default


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", default="default",
        help="Profile id to load (default: default)",
    )
    parser.add_argument(
        "--cities", default=None,
        help="Comma-separated city tokens to override profile target_cities",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    profile = load_profile_from_default(args.profile)

    if args.cities:
        profile.target_cities = [
            c.strip() for c in args.cities.split(",") if c.strip()
        ]

    print(
        f"Profile: {profile.display_name} | domain={profile.domain} | "
        f"cities={profile.target_cities} | "
        f"role_types={[rt.id for rt in profile.target_role_types]}"
    )
    client = JobSpyClient(profile=profile)
    tracker = Tracker(profile_id=profile.profile_id)

    print("Starting fetch + persist (this can take a while)...")
    new_count = 0
    dup_count = 0
    skipped_count = 0
    sample: list = []
    skipped_samples: list[str] = []
    try:
        for record in client.fetch():
            try:
                _, was_new = persist_record(tracker, record)
            except IncompleteRecordError as e:
                skipped_count += 1
                if len(skipped_samples) < 3:
                    skipped_samples.append(str(e))
                continue
            if was_new:
                new_count += 1
            else:
                dup_count += 1
            if len(sample) < 5:
                sample.append(record)
    finally:
        tracker.close()

    print(
        f"\nInserted {new_count} new, updated {dup_count} existing, "
        f"skipped {skipped_count} incomplete"
    )
    final_tracker = Tracker(profile_id=profile.profile_id)
    try:
        print(f"Total in database: {final_tracker.count_opportunities()}")
    finally:
        final_tracker.close()

    if skipped_samples:
        print("\n--- Skipped records ---")
        for i, msg in enumerate(skipped_samples, 1):
            print(f"[{i}] {msg}")

    print("\n--- First 5 OpportunityRecord ---")
    for i, rec in enumerate(sample, 1):
        print(f"\n[{i}] {rec.title}")
        print(f"    employer:    {rec.employer}")
        print(f"    location:    {rec.location}")
        print(f"    url:         {rec.source_url}")
        print(f"    posted_at:   {rec.posted_at}")
        print(f"    salary:      "
              f"{rec.salary_min}-{rec.salary_max} "
              f"{rec.salary_currency}/{rec.salary_interval}")
        print(f"    role_type:   {rec.search_context.get('role_type')}")
        print(f"    search_term: {rec.search_context.get('search_term')}")


if __name__ == "__main__":
    main()
