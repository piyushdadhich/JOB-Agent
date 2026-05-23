"""Live smoke test for GreenhouseClient. Hits the public Greenhouse
board API and prints the first few records to stdout.

Usage:
  python scripts/smoke_greenhouse.py
  python scripts/smoke_greenhouse.py --slugs d2l,affirm --limit 5
  python scripts/smoke_greenhouse.py --profile default --limit 5

By default reads slugs from config/profiles/{profile}.yaml under
greenhouse_api.slugs. If that block doesn't exist yet (it won't
until TASK 2), --slugs is required. Always read-only - never
persists to the tracker.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml  # noqa: E402

from engine.discovery.greenhouse_client import GreenhouseClient  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", default="default",
        help="Profile id to read slugs from (default: default)",
    )
    parser.add_argument(
        "--slugs", default=None,
        help="Comma-separated slugs to override profile config",
    )
    parser.add_argument(
        "--limit", type=int, default=5,
        help="Max records to print (default: 5)",
    )
    return parser.parse_args()


def _load_slugs_from_profile(profile_id: str) -> list[str]:
    path = PROJECT_ROOT / "config" / "profiles" / f"{profile_id}.yaml"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return list((cfg.get("greenhouse_api") or {}).get("slugs") or [])


def main() -> None:
    args = _parse_args()

    if args.slugs:
        slugs = [s.strip() for s in args.slugs.split(",") if s.strip()]
    else:
        slugs = _load_slugs_from_profile(args.profile)

    if not slugs:
        print(
            "ERROR: no slugs to fetch. Either pass --slugs or add a "
            "greenhouse_api.slugs block to "
            f"config/profiles/{args.profile}.yaml"
        )
        sys.exit(1)

    print(f"Profile: {args.profile} | slugs={slugs} | limit={args.limit}")
    client = GreenhouseClient(slugs=slugs, rate_limit=1.0)

    printed = 0
    total = 0
    for rec in client.fetch():
        total += 1
        if printed >= args.limit:
            continue
        print(f"\n[{printed + 1}] {rec.title}")
        print(f"    employer:    {rec.employer}")
        print(f"    location:    {rec.location}")
        print(f"    url:         {rec.source_url}")
        print(f"    source_id:   {rec.source_id}")
        print(f"    posted_at:   {rec.posted_at}")
        print(f"    slug:        {rec.search_context.get('greenhouse_slug')}")
        snippet = (rec.posting_text or "").strip().replace("\n", " ")
        truncated = "..." if len(snippet) > 120 else ""
        print(f"    text head:   {snippet[:120]}{truncated}")
        printed += 1

    print(f"\nTotal records yielded: {total} (printed {printed})")


if __name__ == "__main__":
    main()
