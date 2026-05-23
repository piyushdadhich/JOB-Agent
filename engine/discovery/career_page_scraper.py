"""Universal career-page Source backed by SmartScraper + Ollama.

Closes the coverage gap for companies whose careers pages aren't on a
supported ATS (Greenhouse / Lever / Ashby / Workday / Workable /
Personio / Recruitee) and aren't surfaced by jobspy or LinkedIn.

Configuration in `config/profiles/{profile}.yaml`:

    source_config:
      career_pages:
        rate_limit_seconds: 5
        urls:
          - https://careers.td.com/en/jobs
          - url: https://jobs.scotiabank.com/search
            employer_hint: Scotiabank
          - url: https://www.atco.com/en-ca/careers.html
            employer_hint: ATCO

A bare string is treated as just a URL with no employer hint. The
employer_hint is used as a fallback when the LLM extracts a posting
that doesn't include a company name (the page itself is the company,
so the LLM often doesn't repeat it on each card).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from engine.discovery.base import (
    OpportunityRecord,
    Source,
    SourceHealth,
)
from engine.discovery.smart_scraper import SmartScraper
from engine.profiles.loader import Profile

logger = logging.getLogger(__name__)

DEFAULT_RATE_LIMIT_SECONDS = 5.0


@dataclass
class CareerPageEntry:
    """One configured career page, optionally with an employer hint."""
    url: str
    employer_hint: Optional[str] = None


@dataclass
class CareerPagesConfig:
    entries: list[CareerPageEntry] = field(default_factory=list)
    rate_limit_seconds: float = DEFAULT_RATE_LIMIT_SECONDS

    @classmethod
    def from_profile(cls, profile: Profile) -> "CareerPagesConfig":
        cfg = (profile.source_config or {}).get("career_pages", {}) or {}
        entries: list[CareerPageEntry] = []
        for raw in cfg.get("urls") or []:
            entries.append(_parse_entry(raw))
        return cls(
            entries=entries,
            rate_limit_seconds=float(
                cfg.get("rate_limit_seconds", DEFAULT_RATE_LIMIT_SECONDS)
            ),
        )


def _parse_entry(raw: Any) -> CareerPageEntry:
    """A career_pages.urls entry may be a bare URL string or a dict."""
    if isinstance(raw, str):
        return CareerPageEntry(url=raw)
    if isinstance(raw, dict):
        url = raw.get("url") or ""
        return CareerPageEntry(
            url=url, employer_hint=raw.get("employer_hint"),
        )
    raise ValueError(f"Invalid career_pages.urls entry: {raw!r}")


class CareerPageScraper(Source):
    """Source that scrapes any company career page via SmartScraper."""

    name = "career_page"

    def __init__(
        self,
        profile: Profile,
        config: Optional[CareerPagesConfig] = None,
        scraper: Optional[SmartScraper] = None,
        sleep_fn=time.sleep,
    ):
        self.profile = profile
        self.config = config or CareerPagesConfig.from_profile(profile)
        # SmartScraper is constructed lazily so tests can pass a fake.
        self._scraper = scraper
        self._sleep = sleep_fn

    @property
    def scraper(self) -> SmartScraper:
        if self._scraper is None:
            self._scraper = SmartScraper()
        return self._scraper

    # ------- Source protocol -------

    def fetch(
        self, profile: Optional[Profile] = None,
    ) -> Iterator[OpportunityRecord]:
        if not self.config.entries:
            logger.info("career_page: no URLs configured; nothing to fetch")
            return
        for idx, entry in enumerate(self.config.entries):
            if idx > 0:
                self._sleep(self.config.rate_limit_seconds)
            yield from self._fetch_entry(entry)

    def health_check(self) -> SourceHealth:
        # We never call the network here — that would be a heavy
        # SmartScraperGraph run per URL just to test connectivity.
        # Healthy means "we have URLs configured and a scraper instance".
        if not self.config.entries:
            return SourceHealth(
                source=self.name,
                reachable=False,
                last_error="no career_pages.urls configured",
            )
        return SourceHealth(source=self.name, reachable=True)

    # ------- Internals -------

    def _fetch_entry(
        self, entry: CareerPageEntry,
    ) -> Iterator[OpportunityRecord]:
        try:
            listings = self.scraper.extract_job_listings(entry.url)
        except Exception as e:
            # SmartScraper itself catches most graph failures and returns
            # []. This is a defensive net for anything else (e.g. invalid
            # URL, network timeout escaping ScrapeGraphAI's own handling).
            logger.warning(
                "career_page: %s failed: %s", entry.url, e,
            )
            return
        if not listings:
            logger.info("career_page: %s yielded 0 listings", entry.url)
            return
        logger.info(
            "career_page: %s yielded %d listings", entry.url, len(listings),
        )
        for item in listings:
            record = self._to_record(item, entry)
            if record is not None:
                yield record

    def _to_record(
        self, item: dict, entry: CareerPageEntry,
    ) -> Optional[OpportunityRecord]:
        title = (item.get("title") or "").strip()
        if not title:
            return None  # cannot persist without a title

        employer = (item.get("company") or entry.employer_hint or "").strip()
        if not employer:
            logger.debug(
                "career_page: skipping listing on %s (no employer): %r",
                entry.url, item,
            )
            return None

        apply_url = (item.get("apply_url") or entry.url).strip()
        if not apply_url:
            return None

        location = (item.get("location") or "").strip()
        description = (item.get("description") or "").strip()

        return OpportunityRecord(
            source=self.name,
            source_url=apply_url,
            employer=employer,
            title=title,
            location=location,
            posting_text=description,
            date_discovered=datetime.now(timezone.utc),
            posted_at=None,  # llm-supplied dates are too unreliable to parse
            raw_payload={
                "career_page_url": entry.url,
                "employer_hint": entry.employer_hint,
                "raw_listing": item,
            },
            search_context={
                "career_page": entry.url,
            },
        )
