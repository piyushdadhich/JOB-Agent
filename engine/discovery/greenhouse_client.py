"""Greenhouse ATS public job-board feed client.

Fetches all open jobs from configured Greenhouse company boards via
the public API at https://boards-api.greenhouse.io/v1/boards/{slug}/jobs
which requires no authentication.

?content=true returns the full job description embedded as an HTML-
escaped string in the `content` field. We html.unescape() once, then
BeautifulSoup-strip to produce plain posting_text.

Health check pings /v1/boards/{slug} (board metadata) rather than
/jobs (which can be large).
"""
from __future__ import annotations

import html
import logging
import time
from datetime import datetime, timezone
from typing import Iterator, Optional

import requests
from bs4 import BeautifulSoup

from engine.discovery.base import OpportunityRecord, SourceHealth

logger = logging.getLogger(__name__)

GREENHOUSE_API_BASE = "https://boards-api.greenhouse.io/v1/boards"


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        s = value.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


class GreenhouseClient:
    """Fetch open jobs from configured Greenhouse boards."""

    name = "greenhouse_api"

    def __init__(self, slugs: list[str], rate_limit: float = 1.0):
        self.slugs = list(slugs)
        self.rate_limit = float(rate_limit)

    def fetch(self) -> Iterator[OpportunityRecord]:
        for slug in self.slugs:
            time.sleep(self.rate_limit)
            try:
                yield from self._fetch_slug(slug)
            except Exception as e:
                logger.warning(
                    "Greenhouse fetch failed for slug %r: %s", slug, e,
                )

    def _fetch_slug(self, slug: str) -> Iterator[OpportunityRecord]:
        url = f"{GREENHOUSE_API_BASE}/{slug}/jobs"
        resp = requests.get(url, params={"content": "true"}, timeout=15)
        if resp.status_code == 404:
            logger.info(
                "Greenhouse slug %r not found (404); skipping", slug,
            )
            return
        resp.raise_for_status()
        data = resp.json() or {}
        for job in data.get("jobs") or []:
            yield self._to_record(job, slug)

    def _to_record(self, job: dict, slug: str) -> OpportunityRecord:
        # Greenhouse returns `content` HTML-escaped (e.g. "&lt;p&gt;..."),
        # so unescape once before stripping tags.
        content_raw = job.get("content") or ""
        if content_raw:
            unescaped = html.unescape(content_raw)
            posting_text = (
                BeautifulSoup(unescaped, "html.parser")
                .get_text("\n", strip=True)
            )
        else:
            posting_text = ""

        location = ""
        loc_obj = job.get("location") or {}
        if isinstance(loc_obj, dict):
            location = loc_obj.get("name") or ""

        job_id = job.get("id")
        source_id = str(job_id) if job_id is not None else None

        return OpportunityRecord(
            source=self.name,
            source_url=job.get("absolute_url") or "",
            employer=slug,
            title=job.get("title") or "",
            location=location,
            posting_text=posting_text,
            posted_at=_parse_iso(job.get("updated_at")),
            source_id=source_id,
            salary_min=None,
            salary_max=None,
            salary_currency=None,
            employer_industry=None,
            raw_payload=job,
            search_context={"greenhouse_slug": slug},
            date_discovered=datetime.now(timezone.utc),
        )

    def health_check(self) -> SourceHealth:
        if not self.slugs:
            return SourceHealth(
                source=self.name, reachable=False,
                last_error="no slugs configured",
            )
        first = self.slugs[0]
        url = f"{GREENHOUSE_API_BASE}/{first}"
        try:
            resp = requests.get(url, timeout=10)
        except requests.RequestException as e:
            return SourceHealth(
                source=self.name, reachable=False, last_error=str(e),
            )
        if resp.status_code == 200:
            return SourceHealth(
                source=self.name, reachable=True,
                last_success=datetime.now(timezone.utc),
            )
        return SourceHealth(
            source=self.name, reachable=False,
            last_error=f"HTTP {resp.status_code} from {url}",
        )
