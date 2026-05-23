"""LinkedIn Guest API discovery Source.

Uses LinkedIn's public guest endpoint (the same one Google's crawler hits)
to fetch job postings without authentication. ZERO risk to a real
LinkedIn account — no cookies, no login, no session.

Endpoints used:
  - List:   https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search
  - Detail: https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}

Per architecture decision #28 (v4): public guest API only.
Per architecture decision #31 (v4.1): detail fetched inline; posting_text
populated at fetch time, not deferred.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator, Optional

import requests
from bs4 import BeautifulSoup

from engine.discovery.base import (
    OpportunityRecord,
    Source,
    SourceHealth,
)
from engine.profiles.loader import Profile

logger = logging.getLogger(__name__)


LIST_URL = (
    "https://www.linkedin.com/jobs-guest/jobs/api/"
    "seeMoreJobPostings/search"
)
DETAIL_URL_TEMPLATE = (
    "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
)
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
}


class RateLimitError(Exception):
    """Raised when LinkedIn returns 429."""


@dataclass
class LinkedInGuestConfig:
    keywords: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    experience: list[int] = field(default_factory=lambda: [4, 5])
    time_filter: str = "r604800"  # week
    workplace: list[int] = field(default_factory=lambda: [1, 2, 3])
    list_rate_limit_seconds: float = 1.0
    detail_rate_limit_seconds: float = 2.5
    rate_limit_fallback_seconds: float = 4.0
    # Skip detail fetch when card snippet is at least this many chars
    card_text_skip_threshold: int = 200
    max_pages_per_query: int = 20  # 20 * 25 = 500 results cap per query
    fetch_details: bool = True
    request_timeout_seconds: int = 15
    # Feature flag: when True, try SmartScraper for detail-page extraction
    # whenever BeautifulSoup parsing yields an empty description. Listings
    # always go through BeautifulSoup because LinkedIn's guest list-page
    # markup is stable and SmartScraper would be ~100x slower per page.
    use_smart_scraper_detail_fallback: bool = False

    @classmethod
    def from_profile(cls, profile: Profile) -> "LinkedInGuestConfig":
        cfg = (profile.source_config or {}).get("linkedin_guest", {}) or {}
        # Backward compat: legacy rate_limit_seconds maps to list rate
        legacy_list = cfg.get("rate_limit_seconds")
        return cls(
            keywords=list(cfg.get("keywords") or []),
            locations=list(
                cfg.get("locations") or list(profile.target_cities)
            ),
            experience=list(cfg.get("experience") or [4, 5]),
            time_filter=cfg.get("time", "r604800"),
            workplace=list(cfg.get("workplace") or [1, 2, 3]),
            list_rate_limit_seconds=cfg.get(
                "list_rate_limit_seconds",
                legacy_list if legacy_list is not None else 1.0,
            ),
            detail_rate_limit_seconds=cfg.get(
                "detail_rate_limit_seconds", 2.5
            ),
            rate_limit_fallback_seconds=cfg.get(
                "rate_limit_fallback_seconds", 4.0
            ),
            card_text_skip_threshold=cfg.get(
                "card_text_skip_threshold", 200
            ),
            max_pages_per_query=cfg.get("max_pages_per_query", 20),
            fetch_details=cfg.get("fetch_details", True),
            request_timeout_seconds=cfg.get("request_timeout_seconds", 15),
            use_smart_scraper_detail_fallback=cfg.get(
                "use_smart_scraper_detail_fallback", False,
            ),
        )


class LinkedInGuestClient(Source):
    """LinkedIn jobs Source via the public guest API.

    NO cookies. NO login. NO session reuse from a real browser.
    The User-Agent is a generic browser string; we do not impersonate
    a logged-in user. See architecture v4 §2.4 / decision #28.
    """

    name = "linkedin_guest"

    def __init__(
        self,
        profile: Profile,
        config: Optional[LinkedInGuestConfig] = None,
        session: Optional[requests.Session] = None,
        sleep_fn=time.sleep,
        smart_scraper=None,
    ) -> None:
        self.profile = profile
        self.config = config or LinkedInGuestConfig.from_profile(profile)
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self._sleep = sleep_fn
        # SmartScraper is constructed lazily on first fallback use so the
        # heavy ScrapeGraphAI import cost is paid only when the fallback
        # actually fires. Tests inject a fake to avoid that cost entirely.
        self._smart_scraper = smart_scraper
        self._current_list_rate_limit = (
            self.config.list_rate_limit_seconds
        )
        self._current_detail_rate_limit = (
            self.config.detail_rate_limit_seconds
        )
        self._rate_limit_escalated = False

    def _get_smart_scraper(self):
        if self._smart_scraper is None:
            from engine.discovery.smart_scraper import SmartScraper
            self._smart_scraper = SmartScraper()
        return self._smart_scraper

    # ------- Source protocol -------

    def fetch(
        self, profile: Optional[Profile] = None
    ) -> Iterator[OpportunityRecord]:
        if not self.config.keywords:
            logger.warning(
                "linkedin_guest: no keywords configured; nothing to fetch"
            )
            return
        if not self.config.locations:
            logger.warning(
                "linkedin_guest: no locations configured; nothing to fetch"
            )
            return

        for keywords in self.config.keywords:
            for location in self.config.locations:
                logger.info(
                    "linkedin_guest: fetching keywords=%r location=%r",
                    keywords, location,
                )
                yield from self._fetch_query(keywords, location)

    def health_check(self) -> SourceHealth:
        try:
            resp = self.session.get(
                LIST_URL,
                params={
                    "keywords": "test",
                    "location": "Toronto",
                    "start": 0,
                },
                timeout=5,
            )
            ok = resp.status_code == 200
            return SourceHealth(
                source=self.name,
                reachable=ok,
                last_success=(
                    datetime.now(timezone.utc) if ok else None
                ),
                last_error=(
                    None if ok else f"status={resp.status_code}"
                ),
                notes=f"status={resp.status_code}",
            )
        except Exception as e:
            return SourceHealth(
                source=self.name,
                reachable=False,
                last_error=repr(e),
            )

    # ------- Internals -------

    def _fetch_query(
        self, keywords: str, location: str
    ) -> Iterator[OpportunityRecord]:
        seen_in_query = 0
        for page_idx in range(self.config.max_pages_per_query):
            start = page_idx * 25
            try:
                html = self._fetch_list_page(keywords, location, start)
            except RateLimitError:
                self._escalate_rate_limit()
                logger.warning(
                    "linkedin_guest: rate-limited at start=%d; "
                    "stopping query keywords=%r location=%r",
                    start, keywords, location,
                )
                return
            except Exception as e:
                logger.warning(
                    "linkedin_guest: list fetch failed at start=%d: %s",
                    start, e,
                )
                return

            cards = self._parse_cards(html)
            if not cards:
                logger.info(
                    "linkedin_guest: no more results at start=%d "
                    "(total this query: %d)", start, seen_in_query,
                )
                return

            for card in cards:
                try:
                    record = self._card_to_record(card, keywords, location)
                except RateLimitError:
                    self._escalate_rate_limit()
                    logger.warning(
                        "linkedin_guest: rate-limited during detail fetch; "
                        "stopping query"
                    )
                    return
                except Exception as e:
                    logger.warning(
                        "linkedin_guest: card->record failed: %s", e
                    )
                    continue
                if record is None:
                    continue
                seen_in_query += 1
                yield record

    def _fetch_list_page(
        self, keywords: str, location: str, start: int
    ) -> str:
        self._sleep(self._current_list_rate_limit)
        params = {
            "keywords": keywords,
            "location": location,
            "start": start,
            "f_TPR": self.config.time_filter,
            "f_E": ",".join(str(x) for x in self.config.experience),
            "f_WT": ",".join(str(x) for x in self.config.workplace),
        }
        resp = self.session.get(
            LIST_URL,
            params=params,
            timeout=self.config.request_timeout_seconds,
        )
        if resp.status_code == 429:
            raise RateLimitError(
                f"LinkedIn 429 list keywords={keywords!r} "
                f"location={location!r} start={start}"
            )
        resp.raise_for_status()
        return resp.text

    def _parse_cards(self, html: str) -> list:
        soup = BeautifulSoup(html, "html.parser")
        return soup.find_all("div", class_="base-card") or []

    @staticmethod
    def _extract_card_snippet(card) -> str:
        """Extract any description text embedded directly in the card.

        LinkedIn list-page cards sometimes include a usable description
        snippet without requiring a separate detail fetch. We look for
        a few known classes; if any yield substantive text, we use it.

        Returns empty string when no snippet is present. The caller
        decides (via card_text_skip_threshold) whether the snippet is
        long enough to skip the detail fetch.
        """
        # Known LinkedIn snippet container classes, in priority order
        candidates = [
            "base-search-card__metadata",
            "job-search-card__snippet",
            "base-search-card__snippet",
            "show-more-less-html__markup",
        ]
        for cls in candidates:
            el = card.find(class_=cls)
            if el:
                text = el.get_text("\n", strip=True)
                if text:
                    return text
        return ""

    def _card_to_record(
        self, card, keywords: str, location: str
    ) -> Optional[OpportunityRecord]:
        title_el = card.find("h3", class_="base-search-card__title")
        title = title_el.get_text(strip=True) if title_el else None

        company_el = card.find("h4", class_="base-search-card__subtitle")
        employer = company_el.get_text(strip=True) if company_el else None

        location_el = card.find(
            "span", class_="job-search-card__location"
        )
        location_str = (
            location_el.get_text(strip=True) if location_el else location
        )

        link_el = card.find("a", class_="base-card__full-link")
        source_url = link_el.get("href") if link_el else None
        if source_url:
            source_url = source_url.split("?")[0]

        date_el = card.find("time", class_="job-search-card__listdate")
        if date_el is None:
            date_el = card.find(
                "time", class_="job-search-card__listdate--new"
            )
        posted_at = self._parse_date(
            date_el.get("datetime") if date_el else None
        )

        if not source_url or not employer or not title:
            logger.debug(
                "linkedin_guest: skipping card with missing fields "
                "(url=%s employer=%s title=%s)",
                bool(source_url), bool(employer), bool(title),
            )
            return None

        job_id = self._extract_job_id(source_url)
        card_snippet = self._extract_card_snippet(card)
        skip_detail = (
            len(card_snippet)
            >= self.config.card_text_skip_threshold
        )

        if self.config.fetch_details and job_id and not skip_detail:
            detail = self._fetch_detail(job_id)
        else:
            detail = {}
            if skip_detail:
                logger.debug(
                    "linkedin_guest: skipped detail fetch for %s "
                    "(card snippet was %d chars)",
                    job_id, len(card_snippet),
                )

        # posting_text precedence: detail description first, card
        # snippet as fallback. detail.get returns "" not None when
        # description was missing in the parsed detail.
        posting_text = detail.get("description") or card_snippet

        return OpportunityRecord(
            source=self.name,
            source_id=job_id,
            source_url=source_url,
            employer=employer,
            title=title,
            location=location_str,
            posting_text=posting_text,
            date_discovered=datetime.now(timezone.utc),
            posted_at=posted_at,
            salary_min=detail.get("salary_min"),
            salary_max=detail.get("salary_max"),
            salary_currency=detail.get("salary_currency"),
            employer_industry=detail.get("industries"),
            raw_payload={
                "card_html": str(card)[:500],
                "card_snippet_len": len(card_snippet),
                "detail_fetched": not skip_detail,
                "detail": detail,
                "hiring_team": detail.get("hiring_team", []) or [],
            },
            search_context={
                "keywords": keywords,
                "location": location,
                "job_id": job_id,
            },
        )

    def _fetch_detail(self, job_id: str) -> dict:
        url = DETAIL_URL_TEMPLATE.format(job_id=job_id)
        self._sleep(self._current_detail_rate_limit)
        try:
            resp = self.session.get(
                url, timeout=self.config.request_timeout_seconds
            )
        except requests.RequestException as e:
            logger.warning(
                "linkedin_guest: detail request failed for %s: %s",
                job_id, e,
            )
            return {}
        if resp.status_code == 429:
            raise RateLimitError(
                f"LinkedIn 429 on detail fetch for {job_id}"
            )
        if resp.status_code == 404:
            logger.info(
                "linkedin_guest: job %s no longer available (404)", job_id
            )
            return {}
        if resp.status_code != 200:
            logger.warning(
                "linkedin_guest: detail status %d for %s",
                resp.status_code, job_id,
            )
            return {}
        parsed = self._parse_detail(resp.text)
        if (
            self.config.use_smart_scraper_detail_fallback
            and not (parsed.get("description") or "").strip()
        ):
            fallback = self._smart_detail_fallback(url)
            if fallback:
                # Merge: keep BeautifulSoup-derived criteria/salary; only
                # adopt the LLM's description (the field BS missed).
                if fallback.get("description"):
                    parsed["description"] = fallback["description"]
                if not parsed.get("seniority_level") and fallback.get(
                    "seniority_level"
                ):
                    parsed["seniority_level"] = fallback["seniority_level"]
                if not parsed.get("employment_type") and fallback.get(
                    "employment_type"
                ):
                    parsed["employment_type"] = fallback["employment_type"]
        return parsed

    def _smart_detail_fallback(self, url: str) -> dict:
        """LLM-driven detail extraction as a fallback when BeautifulSoup
        gets nothing. Returns {} on any failure so the caller proceeds
        with whatever it already had."""
        try:
            scraper = self._get_smart_scraper()
            result = scraper.extract_job_detail(url)
        except Exception as e:
            logger.warning(
                "linkedin_guest: smart_scraper detail fallback failed "
                "for %s: %s", url, e,
            )
            return {}
        return result or {}

    def _parse_detail(self, html: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")

        desc_el = soup.find(
            "div", class_="show-more-less-html__markup"
        )
        description = (
            desc_el.get_text("\n", strip=True) if desc_el else ""
        )

        criteria: dict[str, str] = {}
        for item in soup.find_all(
            "li", class_="description__job-criteria-item"
        ):
            header = item.find(
                "h3", class_="description__job-criteria-subheader"
            )
            value = item.find(
                "span", class_="description__job-criteria-text"
            )
            if header and value:
                key = header.get_text(strip=True).lower()
                criteria[key] = value.get_text(strip=True)

        salary_min, salary_max, salary_currency = (
            self._parse_salary(soup)
        )
        applicant_count = self._parse_applicant_count(soup)

        hiring_team = self._parse_hiring_team(soup)

        return {
            "description": description,
            "employment_type": criteria.get("employment type"),
            "seniority_level": criteria.get("seniority level"),
            "industries": criteria.get("industries"),
            "job_function": criteria.get("job function"),
            "applicant_count": applicant_count,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": salary_currency,
            "hiring_team": hiring_team,
        }

    @staticmethod
    def _parse_hiring_team(soup) -> list[dict]:
        """Extract hiring-team members from a LinkedIn detail page.

        Two HTML patterns are handled, both best-effort:

        1. `<section class="hirers-card">` containing one or more
           `<div class="base-aside-card">` entries — each is a "Meet
           the hiring team" card with name + title + (optionally)
           a profile link. role_on_posting = "hiring_team".

        2. `<div class="top-card-layout__second-subline">` or
           `<span class="topcard__flavor">` containing
           "Posted by <Name> · <Title>" — the top-card byline.
           role_on_posting = "posted_by".

        Returns a list of {name, title, profile_url, role} dicts.
        Empty list on missing sections or unknown layouts. Never
        raises — caller treats the list as best-effort signal.
        """
        team: list[dict] = []

        hirers = soup.find("section", class_="hirers-card")
        if hirers:
            for card in hirers.find_all("div", class_="base-aside-card"):
                name_el = (
                    card.find("h3")
                    or card.find(
                        "a", class_="base-aside-card__entity-title"
                    )
                )
                title_el = (
                    card.find("h4")
                    or card.find(
                        "p", class_="base-aside-card__entity-subtitle"
                    )
                )
                link_el = card.find(
                    "a", class_="base-aside-card__full-link"
                )
                if name_el is None:
                    continue
                name = name_el.get_text(strip=True)
                if not name:
                    continue
                profile_url: Optional[str] = None
                href = link_el.get("href") if link_el else None
                if href:
                    profile_url = href.split("?")[0]
                team.append({
                    "name": name,
                    "title": (
                        title_el.get_text(strip=True) if title_el else None
                    ),
                    "profile_url": profile_url,
                    "role": "hiring_team",
                })

        posted_by = (
            soup.find("div", class_="top-card-layout__second-subline")
            or soup.find("span", class_="topcard__flavor")
        )
        if posted_by:
            text = posted_by.get_text(" ", strip=True)
            m = re.match(
                r"^\s*Posted by\s+(.+?)(?:\s*[·•\-–]\s*(.+))?\s*$",
                text,
            )
            if m:
                name = m.group(1).strip()
                title = (m.group(2) or "").strip() or None
                if name:
                    team.append({
                        "name": name,
                        "title": title,
                        "profile_url": None,
                        "role": "posted_by",
                    })

        return team

    @staticmethod
    def _parse_salary(soup) -> tuple:
        salary_el = soup.find("span", class_="compensation__salary")
        if not salary_el:
            return (None, None, None)
        text = salary_el.get_text(" ", strip=True)
        currency = (
            "CAD" if ("CA" in text or "C$" in text)
            else ("USD" if "$" in text else None)
        )
        nums = re.findall(r"[\d,]+(?:\.\d+)?", text)
        if len(nums) >= 2:
            try:
                return (
                    int(nums[0].replace(",", "").split(".")[0]),
                    int(nums[1].replace(",", "").split(".")[0]),
                    currency,
                )
            except ValueError:
                return (None, None, currency)
        if len(nums) == 1:
            try:
                v = int(nums[0].replace(",", "").split(".")[0])
                return (v, v, currency)
            except ValueError:
                return (None, None, currency)
        return (None, None, currency)

    @staticmethod
    def _parse_applicant_count(soup) -> Optional[int]:
        el = soup.find("figcaption", class_="num-applicants__caption")
        if not el:
            return None
        text = el.get_text(" ", strip=True)
        m = re.search(r"(\d+)", text)
        return int(m.group(1)) if m else None

    @staticmethod
    def _extract_job_id(source_url: str) -> Optional[str]:
        """Extract LinkedIn numeric job ID from a job URL.

        URL formats observed:
          https://www.linkedin.com/jobs/view/3856293612
          https://ca.linkedin.com/jobs/view/some-title-at-co-3856293612
        """
        if not source_url:
            return None
        path = source_url.rstrip("/").split("?")[0]
        last = path.split("/")[-1] if "/" in path else path
        m = re.search(r"(\d+)$", last)
        return m.group(1) if m else None

    @staticmethod
    def _parse_date(iso_str: Optional[str]) -> Optional[datetime]:
        if not iso_str:
            return None
        try:
            return datetime.fromisoformat(iso_str)
        except ValueError:
            return None

    def _escalate_rate_limit(self) -> None:
        """Bump both list and detail rates to fallback on first 429.

        Single fallback value applied to both endpoints — once we've
        been throttled once, we slow everything until the run ends.
        """
        if self._rate_limit_escalated:
            return
        fallback = self.config.rate_limit_fallback_seconds
        self._current_list_rate_limit = fallback
        self._current_detail_rate_limit = fallback
        self._rate_limit_escalated = True
        logger.warning(
            "linkedin_guest: rate limit escalated to %.1fs "
            "(both list and detail)",
            fallback,
        )
