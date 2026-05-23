"""Job Bank monthly CSV dump parser.

The Canadian federal Job Bank publishes monthly opportunity dumps
via Open Government Portal. This is a file-based source: the
download is manual (or via a separate downloader script) and the
client just parses the CSV at a configured path.

The exact CSV column names vary across dumps, so column lookups
fall through a list of plausible names per field.

Filters: optional target_cities list. When set, rows whose
location field does not contain any target city (case-insensitive
substring match) are skipped.
"""
from __future__ import annotations

import csv
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from engine.discovery.base import OpportunityRecord

logger = logging.getLogger(__name__)


def _first_nonempty(row: dict, *keys: str) -> str:
    for k in keys:
        v = row.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def _parse_salary(value) -> Optional[float]:
    """Parse a salary value tolerantly. Strips '$', ',', whitespace.
    Returns None on any parse failure."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    cleaned = re.sub(r"[\s$,]", "", s)
    try:
        return float(cleaned)
    except (TypeError, ValueError):
        return None


def _parse_date(value) -> Optional[datetime]:
    """Parse a date in common Job Bank formats. Returns None on
    failure."""
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


class JobBankCSVClient:
    """Parse a Job Bank monthly CSV dump.

    Does NOT download. The CSV must already be on disk at
    `csv_path`. If the file is missing, fetch() yields nothing
    (logged warning) - the daily pipeline keeps moving.
    """

    name = "job_bank_csv"

    def __init__(
        self,
        csv_path,
        target_cities: Optional[list[str]] = None,
    ):
        self.csv_path = Path(csv_path) if csv_path else None
        self.target_cities = [
            c.strip().lower() for c in (target_cities or [])
            if c and c.strip()
        ]

    def fetch(self) -> Iterator[OpportunityRecord]:
        if not self.csv_path or not self.csv_path.exists():
            logger.warning(
                "Job Bank CSV not found at %s; skipping",
                self.csv_path,
            )
            return
        try:
            with self.csv_path.open(
                "r", encoding="utf-8-sig", newline="",
            ) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rec = self._maybe_record(row)
                    if rec is not None:
                        yield rec
        except (OSError, csv.Error) as e:
            logger.warning(
                "Job Bank CSV read failed for %s: %s",
                self.csv_path, e,
            )

    def _maybe_record(self, row: dict) -> Optional[OpportunityRecord]:
        location = _first_nonempty(row, "location", "city", "place")
        if self.target_cities:
            loc_lower = location.lower()
            if not any(c in loc_lower for c in self.target_cities):
                return None

        title = _first_nonempty(row, "title", "job_title", "occupation")
        employer = _first_nonempty(row, "employer", "company",
                                    "business_name")
        if not title and not employer:
            # Truly empty row; skip rather than yield a record that
            # persist_record will immediately reject.
            return None

        return OpportunityRecord(
            source=self.name,
            source_url=_first_nonempty(row, "url", "job_url", "link"),
            employer=employer,
            title=title,
            location=location,
            posting_text=_first_nonempty(row, "description", "summary"),
            posted_at=_parse_date(
                _first_nonempty(row, "date_posted", "posted_date",
                                 "posting_date")
            ),
            salary_min=_parse_salary(
                _first_nonempty(row, "salary_min", "minimum_salary",
                                 "wage_min")
            ),
            salary_max=_parse_salary(
                _first_nonempty(row, "salary_max", "maximum_salary",
                                 "wage_max")
            ),
            salary_currency="CAD",
            employer_industry=_first_nonempty(
                row, "noc_code", "industry", "sector",
            ) or None,
            raw_payload=row,
            search_context={"source_file": str(self.csv_path)},
            date_discovered=datetime.now(timezone.utc),
        )
