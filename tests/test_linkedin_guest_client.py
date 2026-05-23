"""Tests for engine.discovery.linkedin_guest.

All HTTP is mocked via injected MagicMock sessions. No real network calls.
sleep_fn is also injected so tests run instantly.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.base import OpportunityRecord, Source
from engine.discovery.linkedin_guest import (
    LIST_URL,
    DETAIL_URL_TEMPLATE,
    LinkedInGuestClient,
    LinkedInGuestConfig,
    RateLimitError,
)
from engine.profiles.loader import Profile


# ---------- helpers ----------

def _make_profile(**overrides) -> Profile:
    cfg = {
        "keywords": ["project manager"],
        "locations": ["Toronto"],
        "experience": [4, 5],
        "time": "r86400",
        "workplace": [1, 2, 3],
        "list_rate_limit_seconds": 0,
        "detail_rate_limit_seconds": 0,
        "rate_limit_fallback_seconds": 0,
        "max_pages_per_query": 5,
        "fetch_details": True,
        "request_timeout_seconds": 5,
    }
    cfg.update(overrides)
    return Profile(
        profile_id="test",
        display_name="Test",
        domain="corporate",
        target_cities=["toronto"],
        target_role_types=[],
        sources_enabled=["linkedin_guest"],
        source_config={"linkedin_guest": cfg},
    )


def _make_card_html(
    *,
    title="Project Manager",
    employer="Acme Corp",
    location_text="Toronto, ON",
    href="https://www.linkedin.com/jobs/view/3856293612",
    posted_iso="2026-04-28",
    snippet=None,
    snippet_class="base-search-card__metadata",
    include_title=True,
    include_employer=True,
    include_location=True,
    include_link=True,
    include_date=True,
) -> str:
    parts = ['<div class="base-card">']
    if include_link:
        parts.append(
            f'<a class="base-card__full-link" href="{href}"></a>'
        )
    if include_title:
        parts.append(
            f'<h3 class="base-search-card__title">{title}</h3>'
        )
    if include_employer:
        parts.append(
            f'<h4 class="base-search-card__subtitle">{employer}</h4>'
        )
    if include_location:
        parts.append(
            f'<span class="job-search-card__location">'
            f'{location_text}</span>'
        )
    if include_date:
        parts.append(
            f'<time class="job-search-card__listdate" '
            f'datetime="{posted_iso}"></time>'
        )
    if snippet is not None:
        parts.append(f'<p class="{snippet_class}">{snippet}</p>')
    parts.append("</div>")
    return "\n".join(parts)


def _make_list_html(cards: list[str]) -> str:
    return "<html><body>" + "\n".join(cards) + "</body></html>"


def _make_detail_html(
    *,
    description="A great role.",
    employment_type="Full-time",
    seniority_level="Mid-Senior level",
    industries="Software Development",
    job_function="Project Management",
    salary=None,
    applicant_count=None,
) -> str:
    parts: list[str] = []
    if description is not None:
        parts.append(
            f'<div class="show-more-less-html__markup">'
            f'{description}</div>'
        )
    parts.append("<ul>")
    for label, value in [
        ("Employment type", employment_type),
        ("Seniority level", seniority_level),
        ("Industries", industries),
        ("Job function", job_function),
    ]:
        if value is None:
            continue
        parts.append(
            '<li class="description__job-criteria-item">'
            f'<h3 class="description__job-criteria-subheader">'
            f'{label}</h3>'
            f'<span class="description__job-criteria-text">'
            f'{value}</span>'
            "</li>"
        )
    parts.append("</ul>")
    if salary is not None:
        parts.append(
            f'<span class="compensation__salary">{salary}</span>'
        )
    if applicant_count is not None:
        parts.append(
            '<figcaption class="num-applicants__caption">'
            f'Over {applicant_count} applicants</figcaption>'
        )
    return "<html><body>" + "\n".join(parts) + "</body></html>"


def _make_resp(status_code: int = 200, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if status_code >= 400 and status_code != 429:
        resp.raise_for_status.side_effect = requests.HTTPError(
            f"HTTP {status_code}"
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _make_client(
    *,
    profile_overrides: dict | None = None,
    session: MagicMock | None = None,
    sleep_fn=None,
) -> LinkedInGuestClient:
    profile = _make_profile(**(profile_overrides or {}))
    if session is None:
        session = MagicMock()
        session.headers = {}
    if sleep_fn is None:
        sleep_fn = MagicMock()
    return LinkedInGuestClient(
        profile, session=session, sleep_fn=sleep_fn
    )


# ---------- Card parsing (8) ----------

class CardParsingTests(unittest.TestCase):
    def setUp(self):
        self.client = _make_client(
            profile_overrides={"fetch_details": False}
        )

    def _parse_one(self, html: str):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.find_all("div", class_="base-card")
        self.assertEqual(len(cards), 1)
        return self.client._card_to_record(cards[0], "kw", "loc")

    def test_card_parsing_extracts_title(self):
        record = self._parse_one(_make_card_html(title="Senior PM"))
        self.assertEqual(record.title, "Senior PM")

    def test_card_parsing_extracts_employer(self):
        record = self._parse_one(_make_card_html(employer="WidgetCo"))
        self.assertEqual(record.employer, "WidgetCo")

    def test_card_parsing_extracts_location(self):
        record = self._parse_one(
            _make_card_html(location_text="Calgary, AB")
        )
        self.assertEqual(record.location, "Calgary, AB")

    def test_card_parsing_extracts_source_url(self):
        record = self._parse_one(_make_card_html(
            href="https://www.linkedin.com/jobs/view/9999999"
        ))
        self.assertEqual(
            record.source_url,
            "https://www.linkedin.com/jobs/view/9999999",
        )

    def test_url_tracking_params_stripped(self):
        record = self._parse_one(_make_card_html(
            href=(
                "https://www.linkedin.com/jobs/view/9999999"
                "?trackingId=abc&refId=def"
            )
        ))
        self.assertEqual(
            record.source_url,
            "https://www.linkedin.com/jobs/view/9999999",
        )

    def test_card_parsing_handles_missing_date(self):
        record = self._parse_one(_make_card_html(include_date=False))
        self.assertIsNone(record.posted_at)

    def test_card_parsing_handles_malformed_card_returns_none(self):
        # Card that's just an empty div — no title/employer/url at all.
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(
            '<div class="base-card"></div>', "html.parser"
        )
        card = soup.find("div", class_="base-card")
        self.assertIsNone(
            self.client._card_to_record(card, "kw", "loc")
        )

    def test_card_parsing_skips_when_required_fields_missing(self):
        # Missing title alone is enough to skip
        record = self._parse_one(_make_card_html(include_title=False))
        self.assertIsNone(record)
        # Missing employer alone is enough to skip
        record = self._parse_one(_make_card_html(include_employer=False))
        self.assertIsNone(record)
        # Missing link alone is enough to skip
        record = self._parse_one(_make_card_html(include_link=False))
        self.assertIsNone(record)


# ---------- Job ID extraction (3) ----------

class JobIdExtractionTests(unittest.TestCase):
    def test_extract_job_id_from_simple_url(self):
        self.assertEqual(
            LinkedInGuestClient._extract_job_id(
                "https://www.linkedin.com/jobs/view/3856293612"
            ),
            "3856293612",
        )

    def test_extract_job_id_from_slug_url(self):
        self.assertEqual(
            LinkedInGuestClient._extract_job_id(
                "https://ca.linkedin.com/jobs/view/"
                "delivery-manager-at-acme-3856293612"
            ),
            "3856293612",
        )

    def test_extract_job_id_returns_none_for_malformed_url(self):
        self.assertIsNone(LinkedInGuestClient._extract_job_id(""))
        self.assertIsNone(LinkedInGuestClient._extract_job_id(None))
        self.assertIsNone(LinkedInGuestClient._extract_job_id(
            "https://www.linkedin.com/jobs/view/not-numeric/"
        ))


# ---------- Detail fetching (6) ----------

class DetailFetchingTests(unittest.TestCase):
    def _client_with_detail(self, status_code=200, text=""):
        session = MagicMock()
        session.headers = {}
        session.get.return_value = _make_resp(status_code, text)
        return _make_client(session=session), session

    def test_fetch_detail_populates_description(self):
        html = _make_detail_html(description="Lead delivery teams.")
        client, _ = self._client_with_detail(200, html)
        result = client._fetch_detail("123")
        self.assertEqual(result["description"], "Lead delivery teams.")

    def test_fetch_detail_populates_employment_type(self):
        html = _make_detail_html(employment_type="Contract")
        client, _ = self._client_with_detail(200, html)
        result = client._fetch_detail("123")
        self.assertEqual(result["employment_type"], "Contract")

    def test_fetch_detail_populates_seniority_level(self):
        html = _make_detail_html(seniority_level="Director")
        client, _ = self._client_with_detail(200, html)
        result = client._fetch_detail("123")
        self.assertEqual(result["seniority_level"], "Director")

    def test_fetch_detail_populates_industries(self):
        html = _make_detail_html(industries="Real Estate")
        client, _ = self._client_with_detail(200, html)
        result = client._fetch_detail("123")
        self.assertEqual(result["industries"], "Real Estate")

    def test_fetch_detail_handles_404_gracefully(self):
        client, _ = self._client_with_detail(404, "")
        result = client._fetch_detail("123")
        self.assertEqual(result, {})

    def test_fetch_detail_raises_on_429(self):
        client, _ = self._client_with_detail(429, "")
        with self.assertRaises(RateLimitError):
            client._fetch_detail("123")


# ---------- Detail parsing edge cases (3) ----------

class DetailParsingEdgeCaseTests(unittest.TestCase):
    def test_parse_detail_handles_missing_optional_fields(self):
        client = _make_client()
        # Description only — no criteria, no salary, no applicants
        html = (
            "<html><body>"
            '<div class="show-more-less-html__markup">just text</div>'
            "</body></html>"
        )
        result = client._parse_detail(html)
        self.assertEqual(result["description"], "just text")
        self.assertIsNone(result["employment_type"])
        self.assertIsNone(result["seniority_level"])
        self.assertIsNone(result["industries"])
        self.assertIsNone(result["job_function"])
        self.assertIsNone(result["salary_min"])
        self.assertIsNone(result["salary_max"])
        self.assertIsNone(result["salary_currency"])
        self.assertIsNone(result["applicant_count"])

    def test_parse_detail_extracts_salary_when_present(self):
        client = _make_client()
        html = _make_detail_html(salary="CA$90,000 - CA$120,000")
        result = client._parse_detail(html)
        self.assertEqual(result["salary_min"], 90000)
        self.assertEqual(result["salary_max"], 120000)
        self.assertEqual(result["salary_currency"], "CAD")

    def test_parse_detail_extracts_applicant_count_when_present(self):
        client = _make_client()
        html = _make_detail_html(applicant_count=200)
        result = client._parse_detail(html)
        self.assertEqual(result["applicant_count"], 200)


# ---------- End-to-end card_to_record (3) ----------

class CardToRecordTests(unittest.TestCase):
    def _client_with_responses(self, responses, fetch_details=True):
        session = MagicMock()
        session.headers = {}
        session.get.side_effect = responses
        return _make_client(
            profile_overrides={"fetch_details": fetch_details},
            session=session,
        ), session

    def _parse_one_card(self, client, card_html):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(card_html, "html.parser")
        return client._card_to_record(
            soup.find("div", class_="base-card"), "kw", "loc"
        )

    def test_card_to_record_calls_fetch_detail(self):
        detail_html = _make_detail_html(description="Detail body.")
        client, session = self._client_with_responses(
            [_make_resp(200, detail_html)]
        )
        record = self._parse_one_card(client, _make_card_html())
        self.assertIsNotNone(record)
        # session.get should have been called once for the detail URL
        self.assertEqual(session.get.call_count, 1)
        called_url = session.get.call_args.args[0]
        self.assertEqual(
            called_url,
            DETAIL_URL_TEMPLATE.format(job_id="3856293612"),
        )

    def test_card_to_record_populates_posting_text_from_detail(self):
        detail_html = _make_detail_html(description="Body of posting.")
        client, _ = self._client_with_responses(
            [_make_resp(200, detail_html)]
        )
        record = self._parse_one_card(client, _make_card_html())
        self.assertEqual(record.posting_text, "Body of posting.")

    def test_card_to_record_skips_detail_fetch_when_disabled(self):
        client, session = self._client_with_responses(
            [], fetch_details=False
        )
        record = self._parse_one_card(client, _make_card_html())
        self.assertIsNotNone(record)
        self.assertEqual(record.posting_text, "")
        # No HTTP calls at all
        session.get.assert_not_called()


# ---------- Pagination + rate limiting (5) ----------

class PaginationTests(unittest.TestCase):
    def test_fetch_page_429_raises_rate_limit_error(self):
        session = MagicMock()
        session.headers = {}
        session.get.return_value = _make_resp(429)
        client = _make_client(session=session)
        with self.assertRaises(RateLimitError):
            client._fetch_list_page("kw", "loc", 0)

    def test_fetch_page_other_error_propagates(self):
        session = MagicMock()
        session.headers = {}
        session.get.return_value = _make_resp(500)
        client = _make_client(session=session)
        with self.assertRaises(requests.HTTPError):
            client._fetch_list_page("kw", "loc", 0)

    def test_pagination_stops_on_empty_response(self):
        # First page has 1 card, second page has 0 cards (terminator)
        session = MagicMock()
        session.headers = {}
        first_html = _make_list_html([_make_card_html()])
        empty_html = _make_list_html([])
        session.get.side_effect = [
            _make_resp(200, first_html),
            _make_resp(200, empty_html),
        ]
        client = _make_client(
            profile_overrides={"fetch_details": False},
            session=session,
        )
        records = list(client.fetch())
        self.assertEqual(len(records), 1)
        # 2 list calls (one with cards, one empty terminator)
        self.assertEqual(session.get.call_count, 2)

    def test_pagination_respects_max_pages(self):
        # Every page returns 1 card; cap at 2 pages → 2 cards, 2 calls
        session = MagicMock()
        session.headers = {}
        page = _make_list_html([_make_card_html()])
        session.get.return_value = _make_resp(200, page)
        client = _make_client(
            profile_overrides={
                "fetch_details": False,
                "max_pages_per_query": 2,
            },
            session=session,
        )
        records = list(client.fetch())
        self.assertEqual(len(records), 2)
        self.assertEqual(session.get.call_count, 2)

    def test_rate_limit_delay_applied_between_pages(self):
        # Two pages; sleep_fn should be called twice (once per page).
        session = MagicMock()
        session.headers = {}
        page = _make_list_html([_make_card_html()])
        empty = _make_list_html([])
        session.get.side_effect = [
            _make_resp(200, page),
            _make_resp(200, empty),
        ]
        sleep_fn = MagicMock()
        client = _make_client(
            profile_overrides={
                "fetch_details": False,
                "list_rate_limit_seconds": 1.5,
            },
            session=session,
            sleep_fn=sleep_fn,
        )
        list(client.fetch())
        self.assertEqual(sleep_fn.call_count, 2)
        sleep_fn.assert_called_with(1.5)


# ---------- Rate limit escalation (2) ----------

class RateLimitEscalationTests(unittest.TestCase):
    def test_rate_limit_escalates_on_first_429(self):
        session = MagicMock()
        session.headers = {}
        session.get.return_value = _make_resp(429)
        client = _make_client(
            profile_overrides={
                "list_rate_limit_seconds": 1.0,
                "detail_rate_limit_seconds": 1.0,
                "rate_limit_fallback_seconds": 2.0,
            },
            session=session,
        )
        list(client.fetch())  # consume generator, triggers escalation
        self.assertTrue(client._rate_limit_escalated)
        self.assertEqual(client._current_list_rate_limit, 2.0)
        self.assertEqual(client._current_detail_rate_limit, 2.0)

    def test_rate_limit_does_not_escalate_twice(self):
        session = MagicMock()
        session.headers = {}
        session.get.return_value = _make_resp(429)
        client = _make_client(
            profile_overrides={
                "keywords": ["a", "b"],
                "list_rate_limit_seconds": 1.0,
                "detail_rate_limit_seconds": 1.0,
                "rate_limit_fallback_seconds": 2.0,
            },
            session=session,
        )
        list(client.fetch())
        # After first 429, both rates were bumped to 2.0.
        # Subsequent 429s must not bump further (or to anything else).
        self.assertEqual(client._current_list_rate_limit, 2.0)
        self.assertEqual(client._current_detail_rate_limit, 2.0)
        self.assertTrue(client._rate_limit_escalated)

        # Verify _escalate_rate_limit is idempotent if called again
        client._escalate_rate_limit()
        self.assertEqual(client._current_list_rate_limit, 2.0)
        self.assertEqual(client._current_detail_rate_limit, 2.0)

    def test_rate_limit_escalation_bumps_both_list_and_detail(self):
        # Start with DIFFERENT list and detail rates; verify both
        # collapse to the single fallback value after a 429.
        session = MagicMock()
        session.headers = {}
        session.get.return_value = _make_resp(429)
        client = _make_client(
            profile_overrides={
                "list_rate_limit_seconds": 1.0,
                "detail_rate_limit_seconds": 2.5,
                "rate_limit_fallback_seconds": 4.0,
            },
            session=session,
        )
        # Pre-escalation: rates differ
        self.assertEqual(client._current_list_rate_limit, 1.0)
        self.assertEqual(client._current_detail_rate_limit, 2.5)
        list(client.fetch())
        # Post-escalation: both pinned to fallback
        self.assertEqual(client._current_list_rate_limit, 4.0)
        self.assertEqual(client._current_detail_rate_limit, 4.0)


# ---------- Multi-query iteration (2) ----------

class MultiQueryTests(unittest.TestCase):
    def test_multiple_keywords_iterated(self):
        session = MagicMock()
        session.headers = {}
        empty = _make_list_html([])
        session.get.return_value = _make_resp(200, empty)
        client = _make_client(
            profile_overrides={
                "keywords": ["delivery manager", "scrum master"],
                "locations": ["Toronto"],
                "fetch_details": False,
            },
            session=session,
        )
        list(client.fetch())
        # 1 page per (keyword, location) — 2 keywords × 1 location = 2 calls
        self.assertEqual(session.get.call_count, 2)
        called_keywords = [
            c.kwargs["params"]["keywords"]
            for c in session.get.call_args_list
        ]
        self.assertIn("delivery manager", called_keywords)
        self.assertIn("scrum master", called_keywords)

    def test_multiple_locations_iterated(self):
        session = MagicMock()
        session.headers = {}
        empty = _make_list_html([])
        session.get.return_value = _make_resp(200, empty)
        client = _make_client(
            profile_overrides={
                "keywords": ["delivery manager"],
                "locations": ["Toronto", "Calgary"],
                "fetch_details": False,
            },
            session=session,
        )
        list(client.fetch())
        self.assertEqual(session.get.call_count, 2)
        called_locations = [
            c.kwargs["params"]["location"]
            for c in session.get.call_args_list
        ]
        self.assertIn("Toronto", called_locations)
        self.assertIn("Calgary", called_locations)


# ---------- Source identity (1) ----------

class SourceIdentityTests(unittest.TestCase):
    def test_record_marked_with_source_linkedin_guest(self):
        session = MagicMock()
        session.headers = {}
        page = _make_list_html([_make_card_html()])
        empty = _make_list_html([])
        session.get.side_effect = [
            _make_resp(200, page),
            _make_resp(200, empty),
        ]
        client = _make_client(
            profile_overrides={"fetch_details": False},
            session=session,
        )
        records = list(client.fetch())
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].source, "linkedin_guest")
        self.assertIsInstance(records[0], OpportunityRecord)
        self.assertIsInstance(client, Source)


# ---------- Step 7b: Config back-compat (3) ----------

class ConfigBackCompatTests(unittest.TestCase):
    @staticmethod
    def _profile_with_cfg(cfg: dict) -> Profile:
        return Profile(
            profile_id="t",
            display_name="t",
            domain="corporate",
            target_cities=["toronto"],
            target_role_types=[],
            sources_enabled=["linkedin_guest"],
            source_config={"linkedin_guest": cfg},
        )

    def test_legacy_rate_limit_seconds_maps_to_list_rate(self):
        cfg = {"keywords": ["x"], "rate_limit_seconds": 3.0}
        loaded = LinkedInGuestConfig.from_profile(
            self._profile_with_cfg(cfg)
        )
        self.assertEqual(loaded.list_rate_limit_seconds, 3.0)
        # Detail rate falls to its own default, NOT the legacy value
        self.assertEqual(loaded.detail_rate_limit_seconds, 2.5)

    def test_new_rate_limit_keys_take_precedence(self):
        cfg = {
            "keywords": ["x"],
            "rate_limit_seconds": 3.0,          # legacy
            "list_rate_limit_seconds": 5.0,     # new — wins
            "detail_rate_limit_seconds": 6.0,   # new — independent
        }
        loaded = LinkedInGuestConfig.from_profile(
            self._profile_with_cfg(cfg)
        )
        self.assertEqual(loaded.list_rate_limit_seconds, 5.0)
        self.assertEqual(loaded.detail_rate_limit_seconds, 6.0)

    def test_card_text_skip_threshold_default(self):
        cfg = {"keywords": ["x"]}
        loaded = LinkedInGuestConfig.from_profile(
            self._profile_with_cfg(cfg)
        )
        self.assertEqual(loaded.card_text_skip_threshold, 200)


# ---------- Step 7b: Card snippet extraction (3) ----------

class CardSnippetExtractionTests(unittest.TestCase):
    @staticmethod
    def _card_from(html: str):
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "html.parser").find(
            "div", class_="base-card"
        )

    def test_extract_card_snippet_returns_empty_when_no_match(self):
        card = self._card_from('<div class="base-card"></div>')
        self.assertEqual(
            LinkedInGuestClient._extract_card_snippet(card), ""
        )

    def test_extract_card_snippet_finds_metadata_class(self):
        card = self._card_from(
            '<div class="base-card">'
            '<p class="base-search-card__metadata">Hello snippet</p>'
            "</div>"
        )
        self.assertEqual(
            LinkedInGuestClient._extract_card_snippet(card),
            "Hello snippet",
        )

    def test_extract_card_snippet_finds_first_priority_class(self):
        # base-search-card__metadata is first in the candidates list,
        # so it should win over a co-present job-search-card__snippet.
        card = self._card_from(
            '<div class="base-card">'
            '<p class="job-search-card__snippet">SECOND</p>'
            '<p class="base-search-card__metadata">FIRST</p>'
            "</div>"
        )
        self.assertEqual(
            LinkedInGuestClient._extract_card_snippet(card), "FIRST"
        )


# ---------- Step 7b: Skip-detail logic (4) ----------

class SkipDetailLogicTests(unittest.TestCase):
    def _client_with_responses(
        self, responses, profile_overrides=None
    ):
        session = MagicMock()
        session.headers = {}
        session.get.side_effect = responses
        return _make_client(
            profile_overrides=profile_overrides,
            session=session,
        ), session

    def _parse_one_card(self, client, card_html):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(card_html, "html.parser")
        return client._card_to_record(
            soup.find("div", class_="base-card"), "kw", "loc"
        )

    def test_card_to_record_skips_detail_when_snippet_long_enough(
        self,
    ):
        long_snippet = "x" * 500
        client, session = self._client_with_responses(
            [],  # no HTTP responses needed — detail must NOT be fetched
            profile_overrides={"card_text_skip_threshold": 200},
        )
        record = self._parse_one_card(
            client, _make_card_html(snippet=long_snippet)
        )
        self.assertIsNotNone(record)
        session.get.assert_not_called()
        self.assertEqual(record.posting_text, long_snippet)
        self.assertFalse(record.raw_payload["detail_fetched"])
        self.assertEqual(
            record.raw_payload["card_snippet_len"], 500
        )

    def test_card_to_record_fetches_detail_when_snippet_short(self):
        short_snippet = "x" * 50
        detail_html = _make_detail_html(description="Detail body.")
        client, session = self._client_with_responses(
            [_make_resp(200, detail_html)],
            profile_overrides={"card_text_skip_threshold": 200},
        )
        record = self._parse_one_card(
            client, _make_card_html(snippet=short_snippet)
        )
        self.assertIsNotNone(record)
        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(record.posting_text, "Detail body.")
        self.assertTrue(record.raw_payload["detail_fetched"])

    def test_card_to_record_uses_card_snippet_when_detail_empty(self):
        # Card is below threshold (50 chars) so detail IS fetched.
        # But the detail HTML returns no description div, so
        # detail.get("description") evaluates to "" — falsy. The
        # OR-fallback should land on the card snippet.
        snippet = "x" * 50
        detail_html_no_desc = "<html><body></body></html>"
        client, _ = self._client_with_responses(
            [_make_resp(200, detail_html_no_desc)],
            profile_overrides={"card_text_skip_threshold": 200},
        )
        record = self._parse_one_card(
            client, _make_card_html(snippet=snippet)
        )
        self.assertEqual(record.posting_text, snippet)
        # detail was attempted (snippet was short)
        self.assertTrue(record.raw_payload["detail_fetched"])

    def test_card_to_record_uses_detail_when_both_present(self):
        # Long snippet (would skip), but a very high threshold forces
        # the detail fetch path regardless. Detail's description must
        # win the precedence battle.
        snippet = "x" * 500
        detail_html = _make_detail_html(description="From detail.")
        client, _ = self._client_with_responses(
            [_make_resp(200, detail_html)],
            profile_overrides={"card_text_skip_threshold": 99999},
        )
        record = self._parse_one_card(
            client, _make_card_html(snippet=snippet)
        )
        self.assertEqual(record.posting_text, "From detail.")
        self.assertTrue(record.raw_payload["detail_fetched"])


# ---------- Hiring team parsing (v2.14 / Spec A2 TASK 4) ----------


def _make_hirers_card_html(
    *,
    name: str = "Jane Smith",
    title: str | None = "Director of Engineering",
    href: str | None = (
        "https://www.linkedin.com/in/jane-smith-abc123"
    ),
) -> str:
    parts = ['<div class="base-aside-card">']
    if href:
        parts.append(
            f'<a class="base-aside-card__full-link" href="{href}"></a>'
        )
    if name:
        parts.append(f"<h3>{name}</h3>")
    if title:
        parts.append(f"<h4>{title}</h4>")
    parts.append("</div>")
    return "\n".join(parts)


def _make_detail_html_with_hirers(*cards: str) -> str:
    section = (
        '<section class="hirers-card">'
        + "\n".join(cards)
        + "</section>"
    )
    return (
        '<html><body>'
        '<div class="show-more-less-html__markup">Body.</div>'
        + section
        + "</body></html>"
    )


def _make_detail_html_with_byline(text: str) -> str:
    return (
        '<html><body>'
        '<div class="show-more-less-html__markup">Body.</div>'
        f'<div class="top-card-layout__second-subline">{text}</div>'
        '</body></html>'
    )


class HiringTeamParsingTests(unittest.TestCase):
    def _parse(self, html: str) -> list[dict]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        return LinkedInGuestClient._parse_hiring_team(soup)

    def test_parse_hiring_team_extracts_card_members(self):
        html = _make_detail_html_with_hirers(
            _make_hirers_card_html(
                name="Alice", title="VP Ops",
                href="https://www.linkedin.com/in/alice-1",
            ),
            _make_hirers_card_html(
                name="Bob", title="Eng Mgr",
                href="https://www.linkedin.com/in/bob-2",
            ),
        )
        team = self._parse(html)
        self.assertEqual(len(team), 2)
        self.assertEqual(team[0]["name"], "Alice")
        self.assertEqual(team[0]["title"], "VP Ops")
        self.assertEqual(
            team[0]["profile_url"],
            "https://www.linkedin.com/in/alice-1",
        )
        self.assertEqual(team[0]["role"], "hiring_team")
        self.assertEqual(team[1]["name"], "Bob")

    def test_parse_hiring_team_extracts_posted_by_byline(self):
        html = _make_detail_html_with_byline(
            "Posted by Carol Lee · Talent Acquisition Lead"
        )
        team = self._parse(html)
        self.assertEqual(len(team), 1)
        self.assertEqual(team[0]["name"], "Carol Lee")
        self.assertEqual(team[0]["title"], "Talent Acquisition Lead")
        self.assertEqual(team[0]["role"], "posted_by")
        self.assertIsNone(team[0]["profile_url"])

    def test_parse_hiring_team_strips_profile_url_tracking(self):
        html = _make_detail_html_with_hirers(
            _make_hirers_card_html(
                name="Dee", title="Director",
                href=(
                    "https://www.linkedin.com/in/dee-x?"
                    "trackingId=abc&refId=def"
                ),
            )
        )
        team = self._parse(html)
        self.assertEqual(
            team[0]["profile_url"],
            "https://www.linkedin.com/in/dee-x",
        )

    def test_parse_hiring_team_handles_missing_section(self):
        html = (
            '<html><body>'
            '<div class="show-more-less-html__markup">Body.</div>'
            '</body></html>'
        )
        self.assertEqual(self._parse(html), [])

    def test_parse_hiring_team_handles_malformed_cards(self):
        # A card with no name element at all gets skipped silently.
        bad_card = (
            '<div class="base-aside-card">'
            '<h4>Title only, no name</h4>'
            '</div>'
        )
        good_card = _make_hirers_card_html(name="Eve", title="CTO")
        html = _make_detail_html_with_hirers(bad_card, good_card)
        team = self._parse(html)
        self.assertEqual(len(team), 1)
        self.assertEqual(team[0]["name"], "Eve")

    def test_parse_hiring_team_handles_byline_without_title(self):
        html = _make_detail_html_with_byline("Posted by Frank Solo")
        team = self._parse(html)
        self.assertEqual(len(team), 1)
        self.assertEqual(team[0]["name"], "Frank Solo")
        self.assertIsNone(team[0]["title"])

    def test_card_to_record_surfaces_hiring_team_in_raw_payload(self):
        # Detail returns a hirers-card-only page; card->record should
        # propagate the parsed list to raw_payload.
        detail_html = _make_detail_html_with_hirers(
            _make_hirers_card_html(name="Gina", title="PM")
        )
        session = MagicMock()
        session.headers = {}
        session.get.return_value = _make_resp(200, detail_html)
        client = _make_client(session=session)
        from bs4 import BeautifulSoup
        list_html = _make_card_html()
        soup = BeautifulSoup(list_html, "html.parser")
        card = soup.find("div", class_="base-card")
        record = client._card_to_record(card, "kw", "loc")
        self.assertIsNotNone(record)
        hiring = record.raw_payload.get("hiring_team")
        self.assertIsNotNone(hiring)
        self.assertEqual(len(hiring), 1)
        self.assertEqual(hiring[0]["name"], "Gina")
        self.assertEqual(hiring[0]["role"], "hiring_team")

    def test_parse_hiring_team_handles_completely_unknown_layout(self):
        # Neither hirers-card nor a parseable byline.
        html = (
            '<html><body>'
            '<div class="show-more-less-html__markup">Body.</div>'
            '<section class="some-other-section">'
            '<p>Not the hiring team you are looking for.</p>'
            '</section>'
            '</body></html>'
        )
        self.assertEqual(self._parse(html), [])


if __name__ == "__main__":
    unittest.main()
