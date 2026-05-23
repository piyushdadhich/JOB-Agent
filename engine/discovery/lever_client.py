"""Lever ATS public posting feed client.

Fetches all open postings from configured Lever company boards via
the public API at https://api.lever.co/v0/postings/{slug}?mode=json
which requires no authentication.

Returns OpportunityRecord per posting. The Lever API exposes the
company slug but not the display name, so OpportunityRecord.employer
is set to the slug; the companies upsert path normalizes it.

Pagination: 100 per page via skip/limit. Stops on empty page or
partial page (<PAGE_SIZE) to avoid an extra empty-page round-trip.
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

LEVER_API_BASE = "https://api.lever.co/v0/postings"
PAGE_SIZE = 100


def _parse_lever_date(ms: Optional[int]) -> Optional[datetime]:
    """Lever returns createdAt as milliseconds since epoch (UTC)."""
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


class LeverClient:
    """Fetch all open postings from configured Lever company boards."""

    name = "lever_api"

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
                    "Lever fetch failed for slug %r: %s", slug, e,
                )

    def _fetch_slug(self, slug: str) -> Iterator[OpportunityRecord]:
        url = f"{LEVER_API_BASE}/{slug}"
        offset = 0
        while True:
            params = {
                "mode": "json",
                "limit": PAGE_SIZE,
                "skip": offset,
            }
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 404:
                logger.info(
                    "Lever slug %r not found (404); skipping", slug,
                )
                return
            resp.raise_for_status()
            postings = resp.json()
            if not postings:
                break
            for p in postings:
                yield self._to_record(p, slug)
            if len(postings) < PAGE_SIZE:
                break
            offset += len(postings)

    def _to_record(self, posting: dict, slug: str) -> OpportunityRecord:
        categories = posting.get("categories") or {}
        description_parts: list[str] = []

        if posting.get("description"):
            description_parts.append(
                BeautifulSoup(posting["description"], "html.parser")
                .get_text("\n", strip=True)
            )

        for section in posting.get("lists") or []:
            text = section.get("text")
            if text:
                description_parts.append(text)
            content = section.get("content")
            if content:
                description_parts.append(
                    BeautifulSoup(content, "html.parser")
                    .get_text("\n", strip=True)
                )

        return OpportunityRecord(
            source=self.name,
            source_url=posting.get("hostedUrl") or "",
            employer=slug,
            title=posting.get("text") or "",
            location=categories.get("location") or "",
            posting_text="\n\n".join(description_parts),
            posted_at=_parse_lever_date(posting.get("createdAt")),
            salary_min=None,
            salary_max=None,
            salary_currency=None,
            employer_industry=None,
            raw_payload=posting,
            search_context={"lever_slug": slug},
            date_discovered=datetime.now(timezone.utc),
        )
