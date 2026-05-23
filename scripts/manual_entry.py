"""Manually add a job posting by URL.

Usage:
  python scripts/manual_entry.py --url https://... --profile default
  python scripts/manual_entry.py --profile default
      (prompts for URL interactively)

The script fetches the page, extracts what it can (page title +
text), prompts the user for any missing fields (employer, title,
location), and persists the result via persist_record. Source is
recorded as "manual_entry"; standard dedup applies.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import requests
from bs4 import BeautifulSoup

from engine.discovery.base import OpportunityRecord
from engine.persistence.opportunities import (
    IncompleteRecordError,
    persist_record,
)
from engine.persistence.tracker import Tracker

logger = logging.getLogger(__name__)

POSTING_TEXT_TRUNCATE = 8000
DEFAULT_LOCATION = "Toronto"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/119.0 Safari/537.36"
)


def _guess_employer(url: str, page_title: str) -> str:
    """Best-effort: strip well-known subdomain prefixes off netloc
    and capitalize the next segment."""
    try:
        netloc = urlparse(url).netloc.lower()
    except Exception:
        return ""
    parts = [p for p in netloc.split(".") if p]
    while parts and parts[0] in (
        "www", "jobs", "careers", "apply", "boards",
    ):
        parts = parts[1:]
    if parts:
        return parts[0].capitalize()
    return ""


def fetch_page(url: str) -> tuple[Optional[str], str]:
    """Return (page_title, page_text). Both may be empty on failure."""
    try:
        resp = requests.get(
            url, timeout=15, headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning("manual_entry: page fetch failed for %s: %s", url, e)
        return None, ""
    soup = BeautifulSoup(resp.text, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    text = soup.get_text("\n", strip=True)
    if len(text) > POSTING_TEXT_TRUNCATE:
        text = text[:POSTING_TEXT_TRUNCATE]
    return title, text


def build_record(
    url: str, employer: str, title: str, location: str,
    page_text: str,
) -> OpportunityRecord:
    return OpportunityRecord(
        source="manual_entry",
        source_url=url,
        employer=employer,
        title=title,
        location=location,
        posting_text=page_text,
        posted_at=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        employer_industry=None,
        raw_payload={"fetched_url": url},
        search_context={"entry_method": "manual"},
        date_discovered=datetime.now(timezone.utc),
    )


def _prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{label}{suffix}: ").strip()
    return answer or default


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--url", default=None)
    parser.add_argument("--employer", default=None)
    parser.add_argument("--title", default=None)
    parser.add_argument("--location", default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    url = args.url or input("Posting URL: ").strip()
    if not url:
        print("ERROR: no URL provided")
        return 1

    print(f"Fetching {url} ...")
    page_title, page_text = fetch_page(url)

    employer = args.employer or _prompt(
        "Employer", _guess_employer(url, page_title or ""),
    )
    title = args.title or _prompt(
        "Title", (page_title or "")[:80],
    )
    location = args.location or _prompt("Location", DEFAULT_LOCATION)

    record = build_record(
        url=url, employer=employer, title=title,
        location=location, page_text=page_text,
    )

    tracker = Tracker(args.profile)
    try:
        try:
            oid, was_new = persist_record(tracker, record)
        except IncompleteRecordError as e:
            print(f"ERROR: incomplete record - {e}")
            return 1
        if was_new:
            print(f"Added as opportunity #{oid}")
        else:
            print(f"Already exists as opportunity #{oid} (dedup)")
    finally:
        tracker.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
