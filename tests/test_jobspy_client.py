"""Tests for engine.discovery.jobspy_client. No live network calls."""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.base import OpportunityRecord, SourceHealth
from engine.discovery.jobspy_client import JobSpyClient
from engine.profiles.loader import Profile, RoleType


def _make_profile(
    *,
    cities=None,
    role_types=None,
    jobspy_overrides=None,
    include_jobspy_cfg: bool = True,
) -> Profile:
    if cities is None:
        cities = ["toronto"]
    if role_types is None:
        role_types = [
            RoleType(
                id="delivery_manager",
                description="Delivery manager",
                search_terms=["delivery manager"],
                typical_seniority=["mid", "senior"],
            )
        ]
    source_config = {}
    if include_jobspy_cfg:
        jobspy_cfg = {
            "sites": ["indeed"],
            "hours_old": 72,
            "results_per_search": 10,
            "exact_phrase": True,
            "rate_limit_seconds": 0,  # disable sleep in tests
        }
        if jobspy_overrides:
            jobspy_cfg.update(jobspy_overrides)
        source_config["jobspy"] = jobspy_cfg
    return Profile(
        profile_id="test",
        display_name="Test",
        domain="corporate",
        target_cities=cities,
        target_role_types=role_types,
        sources_enabled=["jobspy"],
        source_config=source_config,
    )


def _row(**fields) -> pd.Series:
    base = {
        "id": "abc123",
        "job_url": "https://indeed.com/viewjob?jk=abc123",
        "company": "Acme Corp",
        "title": "Delivery Manager",
        "location": "Toronto, ON",
        "description": "Lead delivery teams.",
        "date_posted": date(2026, 4, 28),
        "is_remote": False,
        "min_amount": 90000.0,
        "max_amount": 120000.0,
        "currency": "CAD",
        "interval": "yearly",
        "company_industry": "Technology",
        "company_employees_label": "201-500",
    }
    base.update(fields)
    return pd.Series(base)


def _role(rid="delivery_manager", terms=("delivery manager",)) -> RoleType:
    return RoleType(id=rid, description="", search_terms=list(terms))


class TestJobSpyClientInit(unittest.TestCase):
    def test_init_with_minimal_profile(self):
        client = JobSpyClient(profile=_make_profile())
        self.assertEqual(client.profile.target_cities, ["toronto"])
        self.assertEqual(len(client.profile.target_role_types), 1)
        self.assertEqual(client.sites, ["indeed"])
        self.assertEqual(client.hours_old, 72)
        self.assertEqual(client.results_per_search, 10)
        self.assertTrue(client.exact_phrase)

    def test_init_defaults_when_jobspy_section_absent(self):
        client = JobSpyClient(
            profile=_make_profile(include_jobspy_cfg=False)
        )
        self.assertEqual(client.sites, ["indeed"])
        self.assertEqual(client.hours_old, 72)
        self.assertEqual(client.results_per_search, 50)
        self.assertTrue(client.exact_phrase)
        self.assertEqual(
            client.rate_limit_seconds,
            JobSpyClient.DEFAULT_RATE_LIMIT_SECONDS,
        )


class TestCityMapping(unittest.TestCase):
    def setUp(self):
        self.client = JobSpyClient(profile=_make_profile())

    def test_city_mapping_known_cities(self):
        self.assertEqual(
            self.client._city_to_jobspy_location("toronto"),
            "Toronto, ON",
        )
        self.assertEqual(
            self.client._city_to_jobspy_location("calgary"),
            "Calgary, AB",
        )
        self.assertEqual(
            self.client._city_to_jobspy_location("edmonton"),
            "Edmonton, AB",
        )
        self.assertEqual(
            self.client._city_to_jobspy_location("remote_canada"),
            "Canada",
        )

    def test_city_mapping_unknown_city_returns_none(self):
        self.assertIsNone(self.client._city_to_jobspy_location("vancouver"))
        self.assertIsNone(self.client._city_to_jobspy_location(""))


class TestSearchTermFormatting(unittest.TestCase):
    def setUp(self):
        self.client = JobSpyClient(profile=_make_profile())

    def test_exact_phrase_wraps_term_in_quotes(self):
        self.assertEqual(
            self.client._format_term("delivery manager"),
            '"delivery manager"',
        )

    def test_loose_search_does_not_wrap(self):
        client = JobSpyClient(
            profile=_make_profile(jobspy_overrides={"exact_phrase": False})
        )
        self.assertEqual(
            client._format_term("delivery manager"),
            "delivery manager",
        )

    def test_format_term_handles_multi_word_strings(self):
        self.assertEqual(
            self.client._format_term("right of way agent"),
            '"right of way agent"',
        )

    def test_profile_role_type_search_terms_passed_through(self):
        role = _role(rid="rt", terms=["alpha", "beta"])
        client = JobSpyClient(profile=_make_profile(role_types=[role]))
        self.assertEqual(
            client.profile.target_role_types[0].search_terms,
            ["alpha", "beta"],
        )


class TestRowToRecord(unittest.TestCase):
    def setUp(self):
        self.client = JobSpyClient(profile=_make_profile())
        self.role = _role()

    def test_row_to_record_required_fields_populated(self):
        rec = self.client._row_to_record(
            _row(), "toronto", self.role, "delivery manager"
        )
        self.assertIsInstance(rec, OpportunityRecord)
        self.assertEqual(rec.source, "jobspy")
        self.assertEqual(rec.source_url, "https://indeed.com/viewjob?jk=abc123")
        self.assertEqual(rec.employer, "Acme Corp")
        self.assertEqual(rec.title, "Delivery Manager")
        self.assertEqual(rec.location, "Toronto, ON")
        self.assertEqual(rec.posting_text, "Lead delivery teams.")
        self.assertIsInstance(rec.date_discovered, datetime)

    def test_row_to_record_optional_fields_when_missing(self):
        row = _row(
            id=None, company_industry=None, company_employees_label=None,
            min_amount=float("nan"), max_amount=float("nan"),
            currency=None, interval=None, is_remote=None,
            date_posted=None,
        )
        rec = self.client._row_to_record(
            row, "toronto", self.role, "delivery manager"
        )
        self.assertIsNone(rec.salary_min)
        self.assertIsNone(rec.salary_max)
        self.assertIsNone(rec.salary_currency)
        self.assertIsNone(rec.salary_interval)
        self.assertIsNone(rec.is_remote)
        self.assertIsNone(rec.employer_industry)
        self.assertIsNone(rec.posted_at)
        self.assertEqual(rec.source_id, "https://indeed.com/viewjob?jk=abc123")

    def test_row_to_record_search_context_attached(self):
        rec = self.client._row_to_record(
            _row(), "toronto", self.role, "delivery manager"
        )
        self.assertEqual(rec.search_context["city"], "toronto")
        self.assertEqual(rec.search_context["role_type"], "delivery_manager")
        self.assertEqual(
            rec.search_context["search_term"], '"delivery manager"'
        )

    def test_row_to_record_preserves_raw_payload(self):
        rec = self.client._row_to_record(
            _row(), "toronto", self.role, "delivery manager"
        )
        self.assertIn("job_url", rec.raw_payload)
        self.assertEqual(rec.raw_payload["company"], "Acme Corp")

    def test_row_to_record_date_discovered_is_timezone_aware_utc(self):
        rec = self.client._row_to_record(
            _row(), "toronto", self.role, "delivery manager"
        )
        self.assertIsNotNone(rec.date_discovered.tzinfo)


class TestFetch(unittest.TestCase):
    def test_fetch_handles_empty_dataframe(self):
        client = JobSpyClient(profile=_make_profile())
        with patch(
            "engine.discovery.jobspy_client.scrape_jobs",
            return_value=pd.DataFrame(),
        ):
            results = list(client.fetch())
        self.assertEqual(results, [])

    def test_fetch_handles_jobspy_exception(self):
        client = JobSpyClient(profile=_make_profile())
        with patch(
            "engine.discovery.jobspy_client.scrape_jobs",
            side_effect=RuntimeError("boom"),
        ):
            results = list(client.fetch())
        self.assertEqual(results, [])

    def test_fetch_handles_none_return(self):
        client = JobSpyClient(profile=_make_profile())
        with patch(
            "engine.discovery.jobspy_client.scrape_jobs",
            return_value=None,
        ):
            results = list(client.fetch())
        self.assertEqual(results, [])

    def test_fetch_deduplicates_within_run(self):
        roles = [
            _role("delivery_manager", ["delivery manager"]),
            _role("scrum_master", ["scrum master"]),
        ]
        client = JobSpyClient(profile=_make_profile(role_types=roles))

        df = pd.DataFrame([_row().to_dict()])
        with patch(
            "engine.discovery.jobspy_client.scrape_jobs",
            return_value=df,
        ):
            results = list(client.fetch())
        self.assertEqual(len(results), 1)

    def test_fetch_yields_one_record_per_unique_url(self):
        roles = [
            _role("delivery_manager", ["delivery manager"]),
            _role("scrum_master", ["scrum master"]),
        ]
        client = JobSpyClient(profile=_make_profile(role_types=roles))

        def _fake_scrape(**kwargs):
            term = kwargs.get("search_term", "")
            if "delivery" in term:
                return pd.DataFrame([_row(
                    id="a", job_url="https://x/a", title="DM",
                ).to_dict()])
            return pd.DataFrame([_row(
                id="b", job_url="https://x/b", title="SM",
            ).to_dict()])

        with patch(
            "engine.discovery.jobspy_client.scrape_jobs",
            side_effect=_fake_scrape,
        ):
            results = list(client.fetch())
        urls = sorted(r.source_url for r in results)
        self.assertEqual(urls, ["https://x/a", "https://x/b"])

    def test_fetch_skips_unknown_city(self):
        client = JobSpyClient(profile=_make_profile(cities=["vancouver"]))
        with patch(
            "engine.discovery.jobspy_client.scrape_jobs",
        ) as mock_scrape:
            results = list(client.fetch())
        self.assertEqual(results, [])
        mock_scrape.assert_not_called()

    def test_fetch_calls_scrape_with_quoted_term_when_exact_phrase(self):
        client = JobSpyClient(profile=_make_profile())
        with patch(
            "engine.discovery.jobspy_client.scrape_jobs",
            return_value=pd.DataFrame(),
        ) as mock_scrape:
            list(client.fetch())
        kwargs = mock_scrape.call_args.kwargs
        self.assertEqual(kwargs["search_term"], '"delivery manager"')
        self.assertEqual(kwargs["location"], "Toronto, ON")
        self.assertEqual(kwargs["site_name"], ["indeed"])

    def test_fetch_iterates_all_search_terms_per_role_type(self):
        role = _role(
            "delivery_manager",
            ["delivery manager", "delivery lead", "agile delivery lead"],
        )
        client = JobSpyClient(profile=_make_profile(
            cities=["toronto", "calgary"],
            role_types=[role],
        ))
        with patch(
            "engine.discovery.jobspy_client.scrape_jobs",
            return_value=pd.DataFrame(),
        ) as mock_scrape:
            list(client.fetch())
        # 2 cities x 1 role x 3 search_terms = 6 calls
        self.assertEqual(mock_scrape.call_count, 6)
        terms_called = {
            c.kwargs["search_term"] for c in mock_scrape.call_args_list
        }
        self.assertEqual(
            terms_called,
            {'"delivery manager"', '"delivery lead"', '"agile delivery lead"'},
        )

    def test_fetch_uses_jobspy_config_from_profile(self):
        client = JobSpyClient(profile=_make_profile(
            jobspy_overrides={
                "sites": ["indeed", "linkedin"],
                "hours_old": 24,
                "results_per_search": 7,
            }
        ))
        with patch(
            "engine.discovery.jobspy_client.scrape_jobs",
            return_value=pd.DataFrame(),
        ) as mock_scrape:
            list(client.fetch())
        kwargs = mock_scrape.call_args.kwargs
        self.assertEqual(kwargs["site_name"], ["indeed", "linkedin"])
        self.assertEqual(kwargs["hours_old"], 24)
        self.assertEqual(kwargs["results_wanted"], 7)


class TestHealthCheck(unittest.TestCase):
    def test_health_check_reports_source_name(self):
        client = JobSpyClient(profile=_make_profile())
        health = client.health_check()
        self.assertIsInstance(health, SourceHealth)
        self.assertEqual(health.source, "jobspy")
        self.assertTrue(health.reachable)


class TestCitiesOverride(unittest.TestCase):
    """jobspy.cities config narrows the city set for JobSpy only,
    leaving profile.target_cities intact for other sources."""

    def test_jobspy_cities_subset_used_when_configured(self):
        profile = _make_profile(
            cities=["toronto", "calgary", "edmonton", "remote_canada"],
            jobspy_overrides={"cities": ["toronto", "calgary"]},
        )
        client = JobSpyClient(profile=profile)
        with patch(
            "engine.discovery.jobspy_client.scrape_jobs",
            return_value=pd.DataFrame(),
        ) as mock_scrape:
            list(client.fetch())
        locations_called = {
            c.kwargs["location"] for c in mock_scrape.call_args_list
        }
        self.assertEqual(
            locations_called, {"Toronto, ON", "Calgary, AB"}
        )

    def test_jobspy_cities_falls_back_to_target_cities_when_unset(self):
        profile = _make_profile(
            cities=["toronto", "calgary"],
        )
        client = JobSpyClient(profile=profile)
        with patch(
            "engine.discovery.jobspy_client.scrape_jobs",
            return_value=pd.DataFrame(),
        ) as mock_scrape:
            list(client.fetch())
        locations_called = {
            c.kwargs["location"] for c in mock_scrape.call_args_list
        }
        self.assertEqual(
            locations_called, {"Toronto, ON", "Calgary, AB"}
        )


class TestRateLimitAbort(unittest.TestCase):
    """After 3 consecutive 429-class errors in a city, JobSpyClient
    aborts that city and moves on. Without this, every remaining
    (role_type x search_term) keeps triggering ERROR-level URL
    spam in the daily log."""

    def test_jobspy_consecutive_429_aborts_city(self):
        # 2 cities, 1 role with 5 search_terms = 10 total queries.
        # Toronto is healthy; Calgary 429s on every call.
        role = _role(
            "delivery_manager",
            ["t1", "t2", "t3", "t4", "t5"],
        )
        profile = _make_profile(
            cities=["toronto", "calgary"],
            role_types=[role],
        )
        client = JobSpyClient(profile=profile)

        def _fake_scrape(**kwargs):
            if "Calgary" in kwargs.get("location", ""):
                raise RuntimeError(
                    "HTTPSConnectionPool ... too many 429 error responses"
                )
            return pd.DataFrame()

        with patch(
            "engine.discovery.jobspy_client.scrape_jobs",
            side_effect=_fake_scrape,
        ) as mock_scrape:
            list(client.fetch())

        calls_by_city: dict[str, int] = {}
        for c in mock_scrape.call_args_list:
            loc = c.kwargs["location"]
            calls_by_city[loc] = calls_by_city.get(loc, 0) + 1
        # All 5 toronto queries fired
        self.assertEqual(calls_by_city["Toronto, ON"], 5)
        # Calgary aborted at the threshold (3) — we never tried 4 or 5
        self.assertEqual(
            calls_by_city["Calgary, AB"],
            JobSpyClient.RATE_LIMIT_ABORT_THRESHOLD,
        )

    def test_jobspy_non_429_errors_do_not_count_toward_abort(self):
        """Non-rate-limit errors must NOT trigger an abort — the
        city should keep retrying its search-terms even if a few
        unrelated transient errors occur."""
        role = _role(
            "delivery_manager",
            ["t1", "t2", "t3", "t4", "t5"],
        )
        profile = _make_profile(
            cities=["calgary"], role_types=[role],
        )
        client = JobSpyClient(profile=profile)

        with patch(
            "engine.discovery.jobspy_client.scrape_jobs",
            side_effect=RuntimeError("some random failure, not 429"),
        ) as mock_scrape:
            list(client.fetch())
        # All 5 queries attempted — no abort because none were 429s.
        self.assertEqual(mock_scrape.call_count, 5)


if __name__ == "__main__":
    unittest.main()
