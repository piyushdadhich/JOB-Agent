"""Workable widget API client.

Two-call pattern (list -> detail) like the LinkedIn guest client.
The list endpoint at https://apply.workable.com/api/v1/widget/
accounts/{slug} returns a `jobs` array with summary fields. The
full description requires a per-job detail call to
.../accounts/{slug}/jobs/{shortcode} which returns description,
requirements, and benefits as separate HTML blobs.

Set fetch_details=False to skip the per-job detail call (faster
but posting_text falls back to shortDescription).

Rate-limit applies to BOTH the list call and each detail call.
For a slug with N jobs and fetch_details=True, that is 1 + N
sleeps per slug.
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

WORKABLE_API_BASE = "https://apply.workable.com/api/v1/widget/accounts"


def _strip_html(html: Optional[str]) -> str:
    if not html:
        return ""
    return (
        BeautifulSoup(html, "html.parser")
        .get_text("\n", strip=True)
    )


class WorkableClient:
    """Fetch open jobs from configured Workable accounts."""

    name = "workable_api"

    def __init__(
        self, slugs: list[str], rate_limit: float = 1.0,
        fetch_details: bool = True,
    ):
        self.slugs = list(slugs)
        self.rate_limit = float(rate_limit)
        self.fetch_details = bool(fetch_details)

    def fetch(self) -> Iterator[OpportunityRecord]:
        for slug in self.slugs:
            time.sleep(self.rate_limit)
            try:
                yield from self._fetch_slug(slug)
            except Exception as e:
                logger.warning(
                    "Workable fetch failed for slug %r: %s", slug, e,
                )

    def _fetch_slug(self, slug: str) -> Iterator[OpportunityRecord]:
        url = f"{WORKABLE_API_BASE}/{slug}"
        resp = requests.get(url, timeout=15)
        if resp.status_code == 404:
            logger.info(
                "Workable slug %r not found (404); skipping", slug,
            )
            return
        resp.raise_for_status()
        data = resp.json() or {}
        for job in data.get("jobs") or []:
            detail: dict = {}
            if self.fetch_details and job.get("shortcode"):
                detail = self._fetch_detail(slug, job["shortcode"])
            yield self._to_record(job, detail, slug)

    def _fetch_detail(self, slug: str, shortcode: str) -> dict:
        time.sleep(self.rate_limit)
        url = (
            f"{WORKABLE_API_BASE}/{slug}/jobs/{shortcode}"
        )
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code != 200:
                return {}
            return resp.json() or {}
        except Exception as e:
            logger.debug(
                "Workable detail fetch failed for %s/%s: %s",
                slug, shortcode, e,
            )
            return {}

    def _to_record(
        self, job: dict, detail: dict, slug: str,
    ) -> OpportunityRecord:
        parts: list[str] = []
        for field in ("description", "requirements", "benefits"):
            stripped = _strip_html(detail.get(field))
            if stripped:
                parts.append(stripped)
        if parts:
            posting_text = "\n\n".join(parts)
        else:
            posting_text = job.get("shortDescription") or ""

        location_parts = [
            (job.get("city") or "").strip(),
            (job.get("state") or "").strip(),
            (job.get("country") or "").strip(),
        ]
        location = ", ".join(p for p in location_parts if p)

        return OpportunityRecord(
            source=self.name,
            source_url=job.get("url") or "",
            employer=slug,
            title=job.get("title") or "",
            location=location,
            posting_text=posting_text,
            posted_at=None,
            salary_min=None,
            salary_max=None,
            salary_currency=None,
            employer_industry=job.get("department") or None,
            raw_payload={**job, "detail": detail},
            search_context={"workable_slug": slug},
            date_discovered=datetime.now(timezone.utc),
        )
