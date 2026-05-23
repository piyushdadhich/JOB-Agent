"""Phase 5a Step 7: smoke harness for LinkedInGuestClient.

Default: run with NARROW config (1 keyword x 1 city x 24h window) to
validate rate-limit behavior. Use --keywords / --cities / --max-pages
to override the profile config inline.

Spec A2 (May 2026) adds:
  * --max-keywords cap for overnight runs
  * parse_miss + hiring_team_persisted counters
  * --log-file flag that writes a detailed summary on exit
  * Catches SelectorBreakageError (>50% parse miss rate) as STOP signal
    and writes a stop reason file. (Currently the existing
    LinkedInGuestClient does NOT raise SelectorBreakageError; we still
    handle it here in case a future refactor introduces the circuit
    breaker.)

Usage:
  python scripts/smoke_linkedin.py
  python scripts/smoke_linkedin.py --cities Toronto Calgary
  python scripts/smoke_linkedin.py --no-details --max-pages 2
  python scripts/smoke_linkedin.py --max-keywords 4 \\
      --log-file scripts/output/linkedin_smoke_live.log
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.linkedin_guest import LinkedInGuestClient
from engine.persistence.opportunities import (
    IncompleteRecordError,
    persist_record,
)
from engine.persistence.tracker import Tracker
from engine.profiles.loader import load_profile_from_default


# SelectorBreakageError is not raised by the current
# LinkedInGuestClient, but the smoke harness still catches it so a
# future circuit breaker can plug in without changing this code.
class SelectorBreakageError(Exception):
    """Parse miss rate exceeded threshold; LinkedIn HTML may have changed."""


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="default")
    ap.add_argument(
        "--cities", nargs="*",
        help="override locations (default: profile config)",
    )
    ap.add_argument(
        "--keywords", nargs="*",
        help="override keywords (default: profile config)",
    )
    ap.add_argument(
        "--max-keywords", type=int, default=None,
        help=(
            "cap on number of keywords iterated; "
            "applied after --keywords override"
        ),
    )
    ap.add_argument(
        "--max-pages", type=int,
        help="override max_pages_per_query",
    )
    ap.add_argument(
        "--no-details", action="store_true",
        help="skip detail fetching (faster, but posting_text empty)",
    )
    ap.add_argument(
        "--log-file", type=str, default=None,
        help=(
            "write a structured summary block to this file on exit. "
            "Created if missing."
        ),
    )
    return ap.parse_args(argv)


def _resolve_keywords(client: LinkedInGuestClient) -> list[str]:
    """Merge keywords_traditional + keywords_ai if present, else fall
    back to client.config.keywords (already set by the config loader)."""
    cfg_keywords = list(client.config.keywords or [])
    if cfg_keywords:
        return cfg_keywords
    return []


def _summary_lines(
    *,
    keywords: list[str],
    locations: list[str],
    new_count: int,
    dup_count: int,
    skipped_count: int,
    parse_miss_count: int,
    hiring_team_persisted: int,
    rate_limit_escalated: bool,
    stop_reason: Optional[str] = None,
) -> list[str]:
    lines = [
        f"## LinkedIn smoke summary — "
        f"{datetime.now(timezone.utc).isoformat()}",
        f"keywords:                {keywords}",
        f"locations:               {locations}",
        f"new:                     {new_count}",
        f"dup:                     {dup_count}",
        f"skipped:                 {skipped_count}",
        f"parse_miss:              {parse_miss_count}",
        f"hiring_team_persisted:   {hiring_team_persisted}",
        f"rate_limit_escalated:    {rate_limit_escalated}",
    ]
    if stop_reason:
        lines.append(f"STOP REASON:             {stop_reason}")
    return lines


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    profile = load_profile_from_default(args.profile)
    client = LinkedInGuestClient(profile)

    # default.yaml splits keywords into keywords_traditional +
    # keywords_ai for `--focus` targeting in run_daily.py. The
    # LinkedInGuestConfig loader only reads the legacy single
    # `keywords` list, so we merge the split lists here when the
    # config block has them.
    if not client.config.keywords:
        ln_cfg = (
            (profile.source_config or {}).get("linkedin_guest", {})
            or {}
        )
        merged: list[str] = []
        for key in ("keywords_traditional", "keywords_ai"):
            merged.extend(ln_cfg.get(key) or [])
        if merged:
            client.config.keywords = merged

    if args.cities:
        client.config.locations = args.cities
    if args.keywords:
        client.config.keywords = args.keywords
    if args.max_pages is not None:
        client.config.max_pages_per_query = args.max_pages
    if args.no_details:
        client.config.fetch_details = False
    if args.max_keywords is not None and args.max_keywords >= 0:
        client.config.keywords = list(client.config.keywords or [])[
            : args.max_keywords
        ]

    print(f"linkedin_guest smoke: profile={args.profile}")
    print(f"  keywords:  {client.config.keywords}")
    print(f"  locations: {client.config.locations}")
    print(f"  time:      {client.config.time_filter}")
    print(f"  max_pages: {client.config.max_pages_per_query}")
    print(f"  details:   {client.config.fetch_details}")
    print(f"  list_rate:    {client.config.list_rate_limit_seconds}s")
    print(f"  detail_rate:  {client.config.detail_rate_limit_seconds}s")
    print(f"  skip_thresh:  "
          f"{client.config.card_text_skip_threshold} chars")
    print()

    tracker = Tracker(profile_id=profile.profile_id)
    new_count = 0
    dup_count = 0
    skipped_count = 0
    parse_miss_count = 0  # reserved for future _parse_cards miss tracking
    hiring_team_persisted = 0
    skipped_samples: list[str] = []
    sample: list = []
    stop_reason: Optional[str] = None
    exit_code = 0

    try:
        try:
            for record in client.fetch():
                try:
                    opp_id, was_new = persist_record(tracker, record)
                except IncompleteRecordError as e:
                    skipped_count += 1
                    if len(skipped_samples) < 3:
                        skipped_samples.append(str(e))
                    continue
                if was_new:
                    new_count += 1
                else:
                    dup_count += 1
                # Count hiring-team rows attributable to this opportunity.
                team = (record.raw_payload or {}).get("hiring_team") or []
                hiring_team_persisted += sum(
                    1 for m in team if (m.get("name") or "").strip()
                )
                if len(sample) < 5:
                    sample.append(record)
        except SelectorBreakageError as e:
            stop_reason = f"SelectorBreakageError: {e}"
            exit_code = 2
            stop_path = (
                PROJECT_ROOT
                / "scripts" / "output" / "spec_a2_stop_reason.md"
            )
            stop_path.parent.mkdir(parents=True, exist_ok=True)
            stop_path.write_text(
                f"# Spec A2 stop reason\n\n"
                f"Timestamp: {datetime.now(timezone.utc).isoformat()}\n\n"
                f"LinkedIn smoke aborted: {e}\n\n"
                "LinkedIn HTML structure may have changed; review and\n"
                "adjust selectors before resuming.\n",
                encoding="utf-8",
            )
    finally:
        tracker.close()

    print()
    print(f"new:                   {new_count}")
    print(f"dup:                   {dup_count}")
    print(f"skipped:               {skipped_count}")
    print(f"parse_miss:            {parse_miss_count}")
    print(f"hiring_team_persisted: {hiring_team_persisted}")
    if skipped_samples:
        print("skipped sample reasons:")
        for s in skipped_samples:
            print(f"  - {s}")
    if client._rate_limit_escalated:
        print(
            f"NOTE: rate limit was escalated; "
            f"list now {client._current_list_rate_limit}s, "
            f"detail now {client._current_detail_rate_limit}s"
        )
    if stop_reason:
        print(f"STOP REASON: {stop_reason}")

    if sample:
        print("\n--- First 5 LinkedIn OpportunityRecord ---")
        for i, rec in enumerate(sample, 1):
            text_len = len(rec.posting_text or "")
            team_len = len(
                (rec.raw_payload or {}).get("hiring_team") or []
            )
            print(f"\n[{i}] {rec.title}")
            print(f"    employer:        {rec.employer}")
            print(f"    location:        {rec.location}")
            print(f"    url:             {rec.source_url}")
            print(f"    posted_at:       {rec.posted_at}")
            print(f"    posting_text:    {text_len} chars")
            print(f"    salary:          "
                  f"{rec.salary_min}-{rec.salary_max} "
                  f"{rec.salary_currency}")
            print(f"    industry:        {rec.employer_industry}")
            print(f"    hiring_team:     {team_len} members")
            print(f"    job_id:          "
                  f"{rec.search_context.get('job_id')}")

    if args.log_file:
        try:
            log_path = Path(args.log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            lines = _summary_lines(
                keywords=list(client.config.keywords or []),
                locations=list(client.config.locations or []),
                new_count=new_count,
                dup_count=dup_count,
                skipped_count=skipped_count,
                parse_miss_count=parse_miss_count,
                hiring_team_persisted=hiring_team_persisted,
                rate_limit_escalated=client._rate_limit_escalated,
                stop_reason=stop_reason,
            )
            with log_path.open("a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n\n")
        except OSError as e:
            print(f"WARN: failed to write log file: {e}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
