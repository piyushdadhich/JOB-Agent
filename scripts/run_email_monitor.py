"""Standalone CLI for the email_monitor discovery source.

Usage:
  python scripts/run_email_monitor.py --profile default
  python scripts/run_email_monitor.py --profile default --dry-run
  python scripts/run_email_monitor.py --profile default --hours 48
  python scripts/run_email_monitor.py --profile default --mark-processed

Loads the profile's `email_monitor` block from config/profiles/{p}.yaml,
builds the Gmail-backed source, and either persists each yielded
OpportunityRecord via persist_record() or (under --dry-run) prints
what would be persisted.

Gmail OAuth must already be authorized — run scripts/gmail_auth.py
once before the first live invocation. --mark-processed requires
re-running gmail_auth.py with the gmail.modify scope.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml  # noqa: E402

from engine.discovery.email_monitor import EmailMonitorClient  # noqa: E402
from engine.persistence.opportunities import (  # noqa: E402
    IncompleteRecordError,
    persist_record,
)
from engine.persistence.tracker import Tracker  # noqa: E402

logger = logging.getLogger(__name__)


def _load_email_monitor_config(profile_id: str) -> dict:
    path = PROJECT_ROOT / "config" / "profiles" / f"{profile_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"profile yaml not found: {path}")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return cfg.get("email_monitor") or {}


def _build_client(
    profile_id: str,
    hours: int,
    mark_processed: bool,
    client_factory=EmailMonitorClient,
) -> EmailMonitorClient:
    em_cfg = _load_email_monitor_config(profile_id)
    return client_factory(
        profile_id=profile_id,
        hours_lookback=hours or em_cfg.get("hours_lookback", 24),
        recruiter_domains=em_cfg.get("recruiter_domains") or [],
        newsletter_senders=em_cfg.get("newsletter_senders") or [],
        search_query_override=em_cfg.get("search_query_override"),
        enable_mark_processed=mark_processed,
    )


def run(
    profile_id: str,
    hours: int = 24,
    dry_run: bool = False,
    mark_processed: bool = False,
    client_factory=EmailMonitorClient,
    tracker_factory=Tracker,
    out=sys.stdout,
) -> dict:
    """Execute one email-monitor cycle and return summary counts.

    Factories are injectable so tests can pass mocks. Returns:
        {"yielded": N, "persisted": N, "new": N, "duplicates": N,
         "skipped_incomplete": N}
    """
    client = _build_client(
        profile_id, hours, mark_processed, client_factory=client_factory,
    )

    tracker = None
    if not dry_run:
        tracker = tracker_factory(profile_id)

    counts = {
        "yielded": 0,
        "persisted": 0,
        "new": 0,
        "duplicates": 0,
        "skipped_incomplete": 0,
    }
    for record in client.fetch():
        counts["yielded"] += 1
        if dry_run:
            print(
                f"  [{record.source}] {record.title!r} "
                f"@ {record.employer!r} -- {record.source_url}",
                file=out,
            )
            continue
        try:
            _opp_id, was_new = persist_record(tracker, record)
        except IncompleteRecordError as e:
            counts["skipped_incomplete"] += 1
            logger.info("skipped incomplete record: %s", e)
            continue
        counts["persisted"] += 1
        if was_new:
            counts["new"] += 1
        else:
            counts["duplicates"] += 1

    print(
        f"email_monitor: yielded={counts['yielded']} "
        f"persisted={counts['persisted']} new={counts['new']} "
        f"dupes={counts['duplicates']} "
        f"skipped_incomplete={counts['skipped_incomplete']} "
        f"(dry_run={dry_run})",
        file=out,
    )
    return counts


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", default="default")
    p.add_argument(
        "--hours", type=int, default=0,
        help="lookback hours; 0 (default) uses the profile's value",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="parse and print records; do NOT persist or mark-processed",
    )
    p.add_argument(
        "--mark-processed", action="store_true",
        help="add 'JobAgent-Processed' label to parsed emails so they "
             "are excluded from the next fetch (requires gmail.modify "
             "scope — re-run scripts/gmail_auth.py first)",
    )
    p.add_argument(
        "--verbose", action="store_true",
        help="enable DEBUG logging",
    )
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # --dry-run + --mark-processed is nonsensical; treat as user error.
    effective_mark = args.mark_processed and not args.dry_run
    if args.mark_processed and args.dry_run:
        logger.warning(
            "--mark-processed ignored under --dry-run",
        )

    run(
        profile_id=args.profile,
        hours=args.hours,
        dry_run=args.dry_run,
        mark_processed=effective_mark,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
