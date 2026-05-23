"""
JobSpy-based scraper for Indeed (primary) and LinkedIn (supplementary).

Fits the architecture's Source interface — same shape as
greenhouse_api.py, lever_api.py, ashby_api.py. Driven by a Profile
loaded from config/profiles/{id}.yaml: target_cities and the
resolved target_role_types (list of RoleType objects from the
profile's domain) provide the search matrix; source_config["jobspy"]
provides the per-source knobs.

Why JobSpy: covers the "mid-sized employers that post on Indeed
but don't have proper ATS infrastructure" gap. Maintained library
(python-jobspy 1.1.82+) with active updates against bot detection.

Why not custom Indeed scraper: arms race, our own scraper from
Phase 4b is bot-blocked, JobSpy keeps current.

ToS posture: Indeed prohibits scraping in ToS. JobSpy is still
scraping under the hood. Risk: IP soft-block on heavy use.
Mitigation: cap daily searches per profile, respect rate limits,
rotate user agents.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

import pandas as pd
from jobspy import scrape_jobs

from engine.discovery.base import OpportunityRecord, Source, SourceHealth
from engine.profiles.loader import Profile, RoleType

logger = logging.getLogger(__name__)


def _clean(value: Any) -> Any:
    """Convert pandas NaN/NaT to None; pass through everything else."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


class JobSpyClient(Source):
    """Source adapter for JobSpy library. Driven by a Profile."""

    name = "jobspy"

    DEFAULT_RATE_LIMIT_SECONDS = 5
    # Stop sending JobSpy queries to a city once N consecutive queries
    # have come back as Google rate-limit errors. Without this, every
    # remaining (role_type x search_term) for that city will trigger
    # another 429-cascade ERROR line, flooding the daily log.
    RATE_LIMIT_ABORT_THRESHOLD = 3

    CITY_MAP = {
        "toronto": "Toronto, ON",
        "calgary": "Calgary, AB",
        "edmonton": "Edmonton, AB",
        "remote_canada": "Canada",
    }

    def __init__(self, profile: Profile):
        """
        Construct from a hydrated Profile (see engine.profiles.loader).
        Reads target_cities, target_role_types (resolved RoleType list),
        and source_config["jobspy"] for sites/hours_old/results_per_search/
        rate_limit_seconds/exact_phrase. Optionally, source_config
        ["jobspy"]["cities"] narrows the city set for JobSpy only —
        useful when a subset of profile.target_cities reliably triggers
        Google Jobs 429s and is better covered by another source
        (e.g. LinkedIn guest for remote_canada).
        """
        self.profile = profile

        jobspy_cfg = (profile.source_config or {}).get("jobspy", {}) or {}
        self.sites = jobspy_cfg.get("sites", ["indeed"])
        self.hours_old = jobspy_cfg.get("hours_old", 72)
        self.results_per_search = jobspy_cfg.get("results_per_search", 50)
        self.exact_phrase = jobspy_cfg.get("exact_phrase", True)
        self.rate_limit_seconds = jobspy_cfg.get(
            "rate_limit_seconds", self.DEFAULT_RATE_LIMIT_SECONDS
        )
        cities_override = jobspy_cfg.get("cities")
        self.cities_override = (
            list(cities_override) if cities_override else None
        )

    @staticmethod
    def _is_rate_limit_error(exc: BaseException) -> bool:
        """True iff the exception message looks like an HTTP rate-limit.

        Matches actual HTTP-library phrasings (urllib3 / requests /
        Cloudflare). Plain '429' as a substring is rejected to avoid
        misclassifying unrelated errors that happen to contain that
        token.
        """
        msg = str(exc).lower()
        return (
            "too many 429" in msg
            or "too many requests" in msg
            or "429 client error" in msg
            or "429 too many" in msg
            or "rate limit" in msg
        )

    def fetch(
        self, profile: Optional[Profile] = None
    ) -> Iterator[OpportunityRecord]:
        """
        Iterate (city x role_type x search_term) and yield deduplicated
        OpportunityRecord. Dedup is by source_url within a single fetch run.

        Source contract requires fetch(profile); we accept it for protocol
        compliance but fall back to self.profile when callers omit it.
        """
        active = profile or self.profile

        seen_urls: set[str] = set()
        search_count = 0
        emitted = 0

        cities = self.cities_override or list(active.target_cities)
        for city in cities:
            location = self._city_to_jobspy_location(city)
            if location is None:
                logger.warning("Unknown city token, skipping: %s", city)
                continue

            total_for_city = sum(
                len(rt.search_terms)
                for rt in active.target_role_types
            )
            queries_for_city = 0
            consecutive_429 = 0
            aborted_city = False

            for role_type in active.target_role_types:
                if aborted_city:
                    break
                for raw_term in role_type.search_terms:
                    if aborted_city:
                        break
                    if search_count > 0 and self.rate_limit_seconds > 0:
                        time.sleep(self.rate_limit_seconds)
                    search_count += 1
                    queries_for_city += 1

                    search_term = self._format_term(raw_term)

                    try:
                        df = scrape_jobs(
                            site_name=self.sites,
                            search_term=search_term,
                            location=location,
                            results_wanted=self.results_per_search,
                            hours_old=self.hours_old,
                            country_indeed="Canada",
                            description_format="markdown",
                        )
                    except Exception as e:
                        if self._is_rate_limit_error(e):
                            consecutive_429 += 1
                            logger.debug(
                                "JobSpy 429 for %s/%s/%s (consecutive=%d)",
                                city, role_type.id, raw_term,
                                consecutive_429,
                            )
                            if (
                                consecutive_429
                                >= self.RATE_LIMIT_ABORT_THRESHOLD
                            ):
                                logger.warning(
                                    "Google rate-limited for %s; "
                                    "skipping remaining queries "
                                    "(%d of %d completed)",
                                    city, queries_for_city,
                                    total_for_city,
                                )
                                aborted_city = True
                                break
                        else:
                            consecutive_429 = 0
                            logger.error(
                                "JobSpy fetch failed for %s/%s/%s: %s",
                                city, role_type.id, raw_term, e,
                            )
                        continue

                    consecutive_429 = 0

                    if df is None or df.empty:
                        logger.info(
                            "No results for %s/%s/%s",
                            city, role_type.id, raw_term,
                        )
                        continue

                    for _, row in df.iterrows():
                        url = row.get("job_url")
                        if not url or url in seen_urls:
                            continue
                        seen_urls.add(url)
                        yield self._row_to_record(
                            row, city, role_type, raw_term
                        )
                        emitted += 1

        logger.info(
            "JobSpy emitted %d unique opportunities across %d searches",
            emitted, search_count,
        )

    def health_check(self) -> SourceHealth:
        """Default health check — assumes reachable until proven otherwise."""
        return SourceHealth(source=self.name, reachable=True)

    def _city_to_jobspy_location(self, city: str) -> Optional[str]:
        return self.CITY_MAP.get(city)

    def _format_term(self, raw_term: str) -> str:
        """Wrap in double quotes when exact_phrase=True for Indeed exact match."""
        if self.exact_phrase:
            return f'"{raw_term}"'
        return raw_term

    def _row_to_record(
        self,
        row: pd.Series,
        city: str,
        role_type: RoleType,
        raw_term: str,
    ) -> OpportunityRecord:
        """Map JobSpy DataFrame row -> OpportunityRecord."""
        url = row.get("job_url") or ""
        source_id_raw = _clean(row.get("id")) or url

        posted_at_raw = _clean(row.get("date_posted"))
        posted_at: Optional[datetime] = None
        if posted_at_raw is not None:
            if isinstance(posted_at_raw, datetime):
                posted_at = posted_at_raw
            else:
                try:
                    posted_at = datetime.combine(
                        posted_at_raw, datetime.min.time()
                    )
                except (TypeError, ValueError):
                    posted_at = None

        return OpportunityRecord(
            source=self.name,
            source_id=str(source_id_raw) if source_id_raw else None,
            source_url=url,
            employer=_clean(row.get("company")) or "",
            title=_clean(row.get("title")) or "",
            location=_clean(row.get("location")) or "",
            posting_text=_clean(row.get("description")) or "",
            date_discovered=datetime.now(timezone.utc),
            posted_at=posted_at,
            is_remote=_clean(row.get("is_remote")),
            salary_min=_clean(row.get("min_amount")),
            salary_max=_clean(row.get("max_amount")),
            salary_currency=_clean(row.get("currency")),
            salary_interval=_clean(row.get("interval")),
            employer_industry=_clean(row.get("company_industry")),
            raw_payload=row.to_dict(),
            search_context={
                "city": city,
                "role_type": role_type.id,
                "search_term": self._format_term(raw_term),
            },
        )
