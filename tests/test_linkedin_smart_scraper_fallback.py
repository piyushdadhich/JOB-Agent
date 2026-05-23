"""Tests for LinkedIn guest client's SmartScraper detail-page fallback.

Listings always go through BeautifulSoup. Detail pages go through
BeautifulSoup first; when the description comes back empty AND the
feature flag is on, SmartScraper takes a second pass.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Reuse the existing test file's HTML helpers and mock factories.
from tests.test_linkedin_guest_client import (  # noqa: E402
    _make_card_html,
    _make_detail_html,
    _make_list_html,
    _make_profile,
    _make_resp,
)
from engine.discovery.linkedin_guest import (  # noqa: E402
    LinkedInGuestClient,
    LinkedInGuestConfig,
)


class FakeSmartScraper:
    """Stand-in for SmartScraper. Records URL passed and returns canned dict."""

    def __init__(self, return_value=None):
        self.return_value = return_value
        self.calls = []

    def extract_job_detail(self, url):
        self.calls.append(url)
        return self.return_value


def _build_client(*, fallback_enabled, smart_scraper, list_html, detail_html):
    profile = _make_profile(
        use_smart_scraper_detail_fallback=fallback_enabled,
    )
    session = MagicMock()
    # First .get() returns the listing page; second returns detail.
    session.get.side_effect = [
        _make_resp(200, list_html),
        _make_resp(200, detail_html),
    ]
    return LinkedInGuestClient(
        profile=profile,
        session=session,
        sleep_fn=lambda _: None,
        smart_scraper=smart_scraper,
    )


def test_fallback_off_never_calls_smart_scraper():
    fake = FakeSmartScraper(return_value={"description": "should not see"})
    list_html = _make_list_html([_make_card_html()])
    detail_html = _make_detail_html(description=None)  # empty description
    client = _build_client(
        fallback_enabled=False,
        smart_scraper=fake,
        list_html=list_html,
        detail_html=detail_html,
    )
    records = list(client.fetch())
    assert len(records) == 1
    # Description stayed empty (BeautifulSoup default) and fake was never called.
    assert fake.calls == []


def test_fallback_on_skipped_when_description_present():
    fake = FakeSmartScraper(return_value={"description": "from llm"})
    list_html = _make_list_html([_make_card_html()])
    detail_html = _make_detail_html(description="Real description from BS")
    client = _build_client(
        fallback_enabled=True,
        smart_scraper=fake,
        list_html=list_html,
        detail_html=detail_html,
    )
    records = list(client.fetch())
    assert len(records) == 1
    assert "Real description from BS" in records[0].posting_text
    # SmartScraper not invoked because BS already had the description.
    assert fake.calls == []


def test_fallback_on_invokes_smart_scraper_when_description_empty():
    fake = FakeSmartScraper(return_value={
        "description": "Recovered by SmartScraper.",
        "seniority_level": "Senior",
    })
    list_html = _make_list_html([_make_card_html(snippet=None)])
    # Detail page with no description div — BS returns "" for description.
    detail_html = _make_detail_html(description=None)
    client = _build_client(
        fallback_enabled=True,
        smart_scraper=fake,
        list_html=list_html,
        detail_html=detail_html,
    )
    records = list(client.fetch())
    assert len(records) == 1
    rec = records[0]
    assert rec.posting_text == "Recovered by SmartScraper."
    # Fallback was called with the LinkedIn detail URL, not the source_url.
    assert len(fake.calls) == 1
    assert "/jobs/api/jobPosting/" in fake.calls[0]


def test_fallback_handles_smart_scraper_failure_gracefully():
    class BoomScraper:
        calls = []
        def extract_job_detail(self, url):
            BoomScraper.calls.append(url)
            raise RuntimeError("ollama down")
    list_html = _make_list_html([_make_card_html(snippet=None)])
    detail_html = _make_detail_html(description=None)
    client = _build_client(
        fallback_enabled=True,
        smart_scraper=BoomScraper(),
        list_html=list_html,
        detail_html=detail_html,
    )
    # Must not crash the fetch loop — yield record with empty description.
    records = list(client.fetch())
    assert len(records) == 1
    assert records[0].posting_text == ""


def test_config_parses_fallback_flag():
    profile = _make_profile(use_smart_scraper_detail_fallback=True)
    cfg = LinkedInGuestConfig.from_profile(profile)
    assert cfg.use_smart_scraper_detail_fallback is True


def test_config_fallback_flag_defaults_off():
    profile = _make_profile()  # no override
    cfg = LinkedInGuestConfig.from_profile(profile)
    assert cfg.use_smart_scraper_detail_fallback is False
