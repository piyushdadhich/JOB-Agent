"""Recruitee public job-board API client.

Fetches open offers from configured Recruitee company boards via
the public API at https://{slug}.recruitee.com/api/offers which
requires no authentication.

Each offer includes description and requirements as HTML, plus
optional structured salary fields (min_salary, max_salary,
salary_currency) when the company has populated them.
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


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        s = value.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _strip_html(html: Optional[str]) -> str:
    if not html:
        return ""
    return BeautifulSoup(html, "html.parser").get_text("\n", strip=True)


class RecruiteeClient:
    """Fetch open offers from configured Recruitee accounts."""

    name = "recruitee_api"

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
                    "Recruitee fetch failed for slug %r: %s", slug, e,
                )

    def _fetch_slug(self, slug: str) -> Iterator[OpportunityRecord]:
        url = f"https://{slug}.recruitee.com/api/offers"
        resp = requests.get(url, timeout=15)
        if resp.status_code == 404:
            logger.info(
                "Recruitee slug %r not found (404); skipping", slug,
            )
            return
        resp.raise_for_status()
        data = resp.json() or {}
        for offer in data.get("offers") or []:
            yield self._to_record(offer, slug)

    def _to_record(self, offer: dict, slug: str) -> OpportunityRecord:
        parts: list[str] = []
        for field in ("description", "requirements"):
            stripped = _strip_html(offer.get(field))
            if stripped:
                parts.append(stripped)

        return OpportunityRecord(
            source=self.name,
            source_url=offer.get("careers_url") or "",
            employer=slug,
            title=offer.get("title") or "",
            location=offer.get("location") or "",
            posting_text="\n\n".join(parts),
            posted_at=_parse_iso(offer.get("published_at")),
            salary_min=offer.get("min_salary"),
            salary_max=offer.get("max_salary"),
            salary_currency=offer.get("salary_currency"),
            employer_industry=offer.get("department") or None,
            raw_payload=offer,
            search_context={"recruitee_slug": slug},
            date_discovered=datetime.now(timezone.utc),
        )
