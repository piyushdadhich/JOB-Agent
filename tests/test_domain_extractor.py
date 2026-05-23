"""Unit tests for engine/discovery/domain_extractor.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.domain_extractor import (  # noqa: E402
    backfill_domains,
    extract_domain,
    extract_domain_from_url,
)
from engine.persistence.tracker import Tracker  # noqa: E402


# --- extract_domain_from_url ---------------------------------------

def test_extracts_domain_from_source_url():
    """A non-aggregator URL yields its hostname, sans leading 'www.'."""
    assert (
        extract_domain_from_url("https://www.acme.com/jobs/123")
        == "acme.com"
    )
    assert (
        extract_domain_from_url("https://careers.example.io/role/42")
        == "careers.example.io"
    )


def test_skips_indeed_urls():
    """Indeed and its country-specific subdomains are not employer
    sites; extract_domain_from_url returns None."""
    assert extract_domain_from_url(
        "https://ca.indeed.com/viewjob?jk=abc"
    ) is None
    assert extract_domain_from_url(
        "https://www.indeed.com/jobs?q=xyz"
    ) is None


def test_skips_linkedin_urls():
    """LinkedIn URLs (job postings, company pages) are skipped."""
    assert extract_domain_from_url(
        "https://www.linkedin.com/jobs/view/4123456789"
    ) is None
    assert extract_domain_from_url(
        "https://linkedin.com/company/acme"
    ) is None


def test_skips_other_aggregators_and_ats_hosts():
    """Glassdoor, Google jobs, Greenhouse, Lever, Ashby etc. are
    skipped — none are the employer's own site."""
    for url in (
        "https://glassdoor.ca/job-listing/eng-acme-JV.htm",
        "https://jobs.google.com/jobs?q=eng",
        "https://boards.greenhouse.io/acme/jobs/123",
        "https://jobs.lever.co/acme/abc",
        "https://jobs.ashbyhq.com/acme/role-id",
        "https://apply.workable.com/acme/j/ABC",
    ):
        assert extract_domain_from_url(url) is None, url


def test_extract_domain_from_url_handles_empty_and_invalid():
    """Empty/None/garbage URLs return None without raising."""
    assert extract_domain_from_url("") is None
    assert extract_domain_from_url("not a url") is None
    assert extract_domain_from_url("/relative/path") is None


# --- extract_domain (slug fallback) --------------------------------

def test_slugifies_company_name():
    """Plain names produce {slug}.com; legal suffixes are stripped."""
    assert extract_domain("Acme") == "acme.com"
    assert extract_domain("Acme Corporation") == "acme.com"
    assert extract_domain("Foo Inc.") == "foo.com"
    assert extract_domain("Foo & Bar, LLC") == "fooandbar.com"


def test_returns_none_for_unknown():
    """A name that slugifies to nothing (pure punctuation, etc.)
    yields None — caller must handle it."""
    assert extract_domain("...") is None
    assert extract_domain("---") is None


def test_handles_empty_company_name():
    """Empty / whitespace / None company names produce None."""
    assert extract_domain("") is None
    assert extract_domain("   ") is None


# --- backfill_domains (end-to-end against a real Tracker) ----------

def test_backfill_updates_null_domains(tmp_path):
    """A company with a non-aggregator opportunity URL gets its
    canonical_domain populated from that URL."""
    t = Tracker(profile_id="test", db_path=tmp_path / "t.db")
    try:
        cid = t.upsert_company(name="Acme")
        t.insert_opportunity(
            company_id=cid,
            source="x",
            source_url="https://acme.com/careers/123",
            title="Engineer",
        )
        result = backfill_domains(t)

        assert result["updated"] == 1
        assert result["still_null"] == 0
        row = t.get_company_by_id(cid)
        assert row["canonical_domain"] == "acme.com"
    finally:
        t.close()


def test_backfill_falls_back_to_slug_when_only_aggregator_urls(tmp_path):
    """If all source_urls are aggregators (Indeed, LinkedIn), the
    backfill falls back to {slug}.com from the company name."""
    t = Tracker(profile_id="test", db_path=tmp_path / "t.db")
    try:
        cid = t.upsert_company(name="Wealthsimple Inc.")
        t.insert_opportunity(
            company_id=cid,
            source="indeed",
            source_url="https://ca.indeed.com/viewjob?jk=zzz",
            title="Engineer",
        )
        result = backfill_domains(t)

        assert result["updated"] == 1
        assert result["still_null"] == 0
        assert (
            t.get_company_by_id(cid)["canonical_domain"]
            == "wealthsimple.com"
        )
    finally:
        t.close()


def test_backfill_skips_already_populated(tmp_path):
    """Companies whose canonical_domain is already set are not
    revisited (the SELECT filters on IS NULL)."""
    t = Tracker(profile_id="test", db_path=tmp_path / "t.db")
    try:
        cid = t.upsert_company(name="Acme")
        t.update_company_ats(cid, canonical_domain="prefilled.example")
        t.insert_opportunity(
            company_id=cid,
            source="x",
            source_url="https://acme.com/careers/123",
            title="Engineer",
        )

        result = backfill_domains(t)

        assert result["updated"] == 0
        assert (
            t.get_company_by_id(cid)["canonical_domain"]
            == "prefilled.example"
        )
    finally:
        t.close()
