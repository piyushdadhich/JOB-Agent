"""Ashby ATS public job-board feed client.

Fetches all open jobs from configured Ashby company boards via the
public API at https://api.ashbyhq.com/posting-api/job-board/{slug}
which requires no authentication.

The API returns a JSON `{"jobs": [...]}` payload. Optional
?includeCompensation=true returns compensation data
(currencyCode + a textual compensationTierSummary like "$X - $Y").

Each job exposes either descriptionPlain or descriptionHtml; we
prefer the plain text when present, otherwise strip the HTML.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Iterator, Optional

import requests
from bs4 import BeautifulSoup

from engine.discovery.base import OpportunityRecord

logger = logging.getLogger(__name__)

ASHBY_API_BASE = "https://api.ashbyhq.com/posting-api/job-board"


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        s = value.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


class AshbyClient:
    """Fetch open jobs from configured Ashby boards."""

    name = "ashby_api"

    def __init__(
        self, slugs: list[str], rate_limit: float = 1.0,
        include_compensation: bool = True,
    ):
        self.slugs = list(slugs)
        self.rate_limit = float(rate_limit)
        self.include_compensation = bool(include_compensation)

    def fetch(self) -> Iterator[OpportunityRecord]:
        for slug in self.slugs:
            time.sleep(self.rate_limit)
            try:
                yield from self._fetch_slug(slug)
            except Exception as e:
                logger.warning(
                    "Ashby fetch failed for slug %r: %s", slug, e,
                )

    def _fetch_slug(self, slug: str) -> Iterator[OpportunityRecord]:
        url = f"{ASHBY_API_BASE}/{slug}"
        params: dict = {}
        if self.include_compensation:
            params["includeCompensation"] = "true"
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 404:
            logger.info(
                "Ashby slug %r not found (404); skipping", slug,
            )
            return
        resp.raise_for_status()
        data = resp.json() or {}
        for job in data.get("jobs") or []:
            yield self._to_record(job, slug)

    def _to_record(self, job: dict, slug: str) -> OpportunityRecord:
        comp = job.get("compensation") or {}
        salary_currency = comp.get("currencyCode") or None
        # Ashby's compensation structure is inconsistent across
        # accounts; we expose what's reliably present and leave
        # numeric min/max for downstream parsing.
        salary_min: Optional[float] = None
        salary_max: Optional[float] = None

        plain = (job.get("descriptionPlain") or "").strip()
        if plain:
            description = plain
        else:
            html = job.get("descriptionHtml") or ""
            description = (
                BeautifulSoup(html, "html.parser")
                .get_text("\n", strip=True)
                if html else ""
            )

        return OpportunityRecord(
            source=self.name,
            source_url=(
                job.get("applicationUrl")
                or job.get("jobUrl")
                or ""
            ),
            employer=slug,
            title=job.get("title") or "",
            location=job.get("location") or "",
            posting_text=description,
            posted_at=_parse_iso(job.get("publishedAt")),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            employer_industry=job.get("department") or None,
            raw_payload=job,
            search_context={"ashby_slug": slug},
            date_discovered=datetime.now(timezone.utc),
        )
