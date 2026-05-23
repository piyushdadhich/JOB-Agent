"""Tests for engine.discovery.career_page_scraper.

A fake SmartScraper is injected so tests don't spin up a browser or hit
Ollama. Real network/LLM coverage lives in scripts/seed_dry_run_posting.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Transitively imports scrapegraphai via engine.discovery
# .career_page_scraper → smart_scraper. Skip when the optional
# [scraper] extra isn't installed.
pytest.importorskip("scrapegraphai")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.base import OpportunityRecord  # noqa: E402
from engine.discovery.career_page_scraper import (  # noqa: E402
    CareerPageEntry,
    CareerPagesConfig,
    CareerPageScraper,
)
from engine.profiles.loader import Profile  # noqa: E402


# --- Fakes -----------------------------------------------------------

class FakeScraper:
    """Records each url passed to extract_job_listings + returns
    pre-canned results from a per-url dict."""

    def __init__(self, by_url=None, raise_for=None):
        self.by_url = by_url or {}
        self.raise_for = raise_for or set()
        self.calls: list[str] = []

    def extract_job_listings(self, url):
        self.calls.append(url)
        if url in self.raise_for:
            raise RuntimeError(f"boom for {url}")
        return self.by_url.get(url, [])


def _make_profile(career_pages_cfg):
    return Profile(
        profile_id="p",
        display_name="P",
        domain="d",
        target_cities=["Toronto"],
        target_role_types=[],
        source_config={"career_pages": career_pages_cfg},
    )


# --- Config parsing --------------------------------------------------

def test_config_from_profile_with_string_urls():
    profile = _make_profile({
        "rate_limit_seconds": 2,
        "urls": ["https://a.com/jobs", "https://b.com/jobs"],
    })
    cfg = CareerPagesConfig.from_profile(profile)
    assert cfg.rate_limit_seconds == 2.0
    assert [e.url for e in cfg.entries] == [
        "https://a.com/jobs", "https://b.com/jobs",
    ]
    assert all(e.employer_hint is None for e in cfg.entries)


def test_config_from_profile_with_employer_hints():
    profile = _make_profile({
        "urls": [
            "https://a.com/jobs",
            {"url": "https://b.com/jobs", "employer_hint": "B Corp"},
        ],
    })
    cfg = CareerPagesConfig.from_profile(profile)
    assert cfg.entries[0].employer_hint is None
    assert cfg.entries[1].employer_hint == "B Corp"


def test_config_from_profile_defaults_when_missing():
    profile = Profile(
        profile_id="p", display_name="P", domain="d",
        target_cities=[], target_role_types=[],
    )
    cfg = CareerPagesConfig.from_profile(profile)
    assert cfg.entries == []
    assert cfg.rate_limit_seconds == 5.0


# --- Fetch behaviour --------------------------------------------------

def test_fetch_yields_opportunity_records():
    profile = _make_profile({
        "urls": [{"url": "https://td.com/jobs", "employer_hint": "TD"}],
    })
    fake = FakeScraper(by_url={
        "https://td.com/jobs": [
            {
                "title": "Senior PM",
                "company": "TD Bank",
                "location": "Toronto",
                "description": "Lead delivery",
                "apply_url": "https://td.com/jobs/1",
            },
            {
                "title": "EM",
                # company missing -> falls back to employer_hint
                "location": "Toronto",
                "apply_url": "https://td.com/jobs/2",
            },
        ],
    })
    s = CareerPageScraper(profile, scraper=fake, sleep_fn=lambda _: None)
    records = list(s.fetch())
    assert len(records) == 2
    assert records[0].employer == "TD Bank"
    assert records[1].employer == "TD"  # employer_hint fallback
    assert all(isinstance(r, OpportunityRecord) for r in records)
    assert records[0].source == "career_page"


def test_fetch_skips_listings_with_no_employer():
    profile = _make_profile({"urls": ["https://x.com/jobs"]})
    fake = FakeScraper(by_url={
        "https://x.com/jobs": [
            {"title": "PM", "apply_url": "https://x.com/jobs/1"},
        ],
    })
    s = CareerPageScraper(profile, scraper=fake, sleep_fn=lambda _: None)
    assert list(s.fetch()) == []


def test_fetch_skips_listings_with_no_title():
    profile = _make_profile({
        "urls": [{"url": "https://x.com", "employer_hint": "X"}],
    })
    fake = FakeScraper(by_url={
        "https://x.com": [
            {"company": "X Corp", "apply_url": "https://x.com/1"},
        ],
    })
    s = CareerPageScraper(profile, scraper=fake, sleep_fn=lambda _: None)
    assert list(s.fetch()) == []


def test_fetch_continues_after_one_url_fails():
    profile = _make_profile({"urls": [
        {"url": "https://bad.com", "employer_hint": "Bad"},
        {"url": "https://good.com", "employer_hint": "Good"},
    ]})
    fake = FakeScraper(
        by_url={
            "https://good.com": [
                {"title": "PM", "company": "Good Corp",
                 "apply_url": "https://good.com/1"},
            ],
        },
        raise_for={"https://bad.com"},
    )
    s = CareerPageScraper(profile, scraper=fake, sleep_fn=lambda _: None)
    records = list(s.fetch())
    assert len(records) == 1
    assert records[0].employer == "Good Corp"


def test_fetch_respects_rate_limit_between_urls():
    profile = _make_profile({
        "rate_limit_seconds": 7,
        "urls": [
            {"url": "https://a.com", "employer_hint": "A"},
            {"url": "https://b.com", "employer_hint": "B"},
            {"url": "https://c.com", "employer_hint": "C"},
        ],
    })
    fake = FakeScraper()
    sleeps = []
    s = CareerPageScraper(
        profile, scraper=fake, sleep_fn=lambda secs: sleeps.append(secs),
    )
    list(s.fetch())
    # Sleep before each URL after the first: 2 sleeps for 3 URLs.
    assert sleeps == [7.0, 7.0]


def test_fetch_uses_apply_url_as_source_url_when_present():
    profile = _make_profile({
        "urls": [{"url": "https://x.com", "employer_hint": "X"}],
    })
    fake = FakeScraper(by_url={
        "https://x.com": [
            {"title": "PM", "company": "X Corp",
             "apply_url": "https://x.com/jobs/123"},
        ],
    })
    s = CareerPageScraper(profile, scraper=fake, sleep_fn=lambda _: None)
    rec = next(iter(s.fetch()))
    assert rec.source_url == "https://x.com/jobs/123"


def test_fetch_falls_back_to_listing_page_url_when_apply_missing():
    profile = _make_profile({
        "urls": [{"url": "https://x.com/jobs", "employer_hint": "X"}],
    })
    fake = FakeScraper(by_url={
        "https://x.com/jobs": [
            {"title": "PM", "company": "X Corp"},  # no apply_url
        ],
    })
    s = CareerPageScraper(profile, scraper=fake, sleep_fn=lambda _: None)
    rec = next(iter(s.fetch()))
    assert rec.source_url == "https://x.com/jobs"


def test_health_check_unhealthy_when_no_urls():
    profile = _make_profile({"urls": []})
    s = CareerPageScraper(profile, scraper=FakeScraper(), sleep_fn=lambda _: None)
    h = s.health_check()
    assert h.reachable is False
    assert "no career_pages.urls" in (h.last_error or "")


def test_health_check_healthy_when_urls_present():
    profile = _make_profile({"urls": ["https://x.com"]})
    s = CareerPageScraper(profile, scraper=FakeScraper(), sleep_fn=lambda _: None)
    h = s.health_check()
    assert h.reachable is True
