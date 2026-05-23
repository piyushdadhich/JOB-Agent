"""Unit tests for engine/discovery/ats_detector.py.

All HTTP calls are mocked via monkeypatching requests.get and the
detector's time.sleep. No live ATS endpoints are touched.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.ats_detector import (  # noqa: E402
    ATSDetector,
    ATSDetectionResult,
    CONFIDENCE_CAREERS_PAGE,
    CONFIDENCE_OVERRIDE,
    CONFIDENCE_SLUG_PROBE,
    slug_variants,
)
from engine.persistence.tracker import Tracker  # noqa: E402


# --- Mock HTTP plumbing -------------------------------------------

class _MockResp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text

    def json(self):
        import json
        return json.loads(self.text)


def _install_mock_get(monkeypatch, dispatch):
    """Replace requests.get inside the detector module with a
    function that delegates to `dispatch(url) -> _MockResp`."""
    def fake_get(url, **kwargs):
        return dispatch(url)
    monkeypatch.setattr(
        "engine.discovery.ats_detector.requests.get", fake_get,
    )


def _silence_sleep(monkeypatch):
    """Replace time.sleep so tests don't actually wait."""
    monkeypatch.setattr(
        "engine.discovery.ats_detector.time.sleep", lambda s: None,
    )


# --- Signal 1: manual override ------------------------------------

def test_manual_override_highest_priority(monkeypatch):
    """A manual override returns immediately with confidence 1.0,
    even when slug-probe / careers-page would also have hit."""
    _silence_sleep(monkeypatch)
    # If these were ever called, they'd return 200 — but they
    # shouldn't be called when the override fires first.
    _install_mock_get(monkeypatch, lambda url: _MockResp(200))

    detector = ATSDetector(rate_limit=0)
    overrides = {
        "Wealthsimple": {"platform": "ashby", "slug": "wealthsimple"},
    }
    result = detector.detect(
        "Wealthsimple", domain="wealthsimple.com", overrides=overrides,
    )
    assert result is not None
    assert result.platform == "ashby"
    assert result.slug == "wealthsimple"
    assert result.method == "manual_override"
    assert result.confidence == CONFIDENCE_OVERRIDE


# --- Signal 2: careers page detection (one test per platform) -----

def _careers_dispatch(domain, embed_html):
    """Build a dispatch fn: 200 + embed_html for /careers, 404 otherwise."""
    def dispatch(url):
        if url == f"https://{domain}/careers":
            return _MockResp(200, embed_html)
        return _MockResp(404)
    return dispatch


def test_careers_page_detects_greenhouse_embed(monkeypatch):
    _silence_sleep(monkeypatch)
    html = (
        '<html><body><iframe src="'
        'https://boards.greenhouse.io/wealthsimple"></iframe>'
        '</body></html>'
    )
    _install_mock_get(
        monkeypatch, _careers_dispatch("acme.com", html),
    )
    detector = ATSDetector(rate_limit=0)
    result = detector.detect("Acme", domain="acme.com")
    assert result is not None
    assert result.platform == "greenhouse"
    assert result.slug == "wealthsimple"
    assert result.method == "careers_page"
    assert result.confidence == CONFIDENCE_CAREERS_PAGE
    assert result.detection_url == "https://acme.com/careers"


def test_careers_page_detects_lever_embed(monkeypatch):
    _silence_sleep(monkeypatch)
    html = '<a href="https://jobs.lever.co/wealthsimple">Apply</a>'
    _install_mock_get(monkeypatch, _careers_dispatch("acme.com", html))
    detector = ATSDetector(rate_limit=0)
    result = detector.detect("Acme", domain="acme.com")
    assert result is not None
    assert result.platform == "lever"
    assert result.slug == "wealthsimple"


def test_careers_page_detects_ashby_embed(monkeypatch):
    _silence_sleep(monkeypatch)
    html = '<iframe src="https://jobs.ashbyhq.com/Wealthsimple/"></iframe>'
    _install_mock_get(monkeypatch, _careers_dispatch("acme.com", html))
    detector = ATSDetector(rate_limit=0)
    result = detector.detect("Acme", domain="acme.com")
    assert result is not None
    assert result.platform == "ashby"
    # Slug is captured verbatim — "Wealthsimple" not "wealthsimple"
    assert result.slug == "Wealthsimple"


def test_careers_page_detects_workable_embed(monkeypatch):
    _silence_sleep(monkeypatch)
    html = '<iframe src="https://apply.workable.com/wealthsimple/"></iframe>'
    _install_mock_get(monkeypatch, _careers_dispatch("acme.com", html))
    detector = ATSDetector(rate_limit=0)
    result = detector.detect("Acme", domain="acme.com")
    assert result is not None
    assert result.platform == "workable"
    assert result.slug == "wealthsimple"


def test_careers_page_detects_personio_embed(monkeypatch):
    _silence_sleep(monkeypatch)
    html = (
        '<a href="https://wealthsimple.jobs.personio.de/">'
        'Open positions</a>'
    )
    _install_mock_get(monkeypatch, _careers_dispatch("acme.com", html))
    detector = ATSDetector(rate_limit=0)
    result = detector.detect("Acme", domain="acme.com")
    assert result is not None
    assert result.platform == "personio"
    assert result.slug == "wealthsimple"


def test_careers_page_detects_recruitee_embed(monkeypatch):
    _silence_sleep(monkeypatch)
    html = (
        '<iframe src="https://wealthsimple.recruitee.com/widget">'
        '</iframe>'
    )
    _install_mock_get(monkeypatch, _careers_dispatch("acme.com", html))
    detector = ATSDetector(rate_limit=0)
    result = detector.detect("Acme", domain="acme.com")
    assert result is not None
    assert result.platform == "recruitee"
    assert result.slug == "wealthsimple"


# --- Signal 3: slug probe ------------------------------------------

def test_slug_probe_finds_greenhouse(monkeypatch):
    """When the careers page yields nothing, the slug probe loops
    through ATS endpoints; first 200 wins."""
    _silence_sleep(monkeypatch)

    def dispatch(url):
        # No careers page; nothing returns 200 from any /careers* URL.
        if "boards-api.greenhouse.io/v1/boards/wealthsimple/jobs" in url:
            return _MockResp(200)
        return _MockResp(404)

    _install_mock_get(monkeypatch, dispatch)
    detector = ATSDetector(rate_limit=0)
    result = detector.detect("Wealthsimple", domain="wealthsimple.com")
    assert result is not None
    assert result.platform == "greenhouse"
    assert result.slug == "wealthsimple"
    assert result.method == "slug_probe"
    assert result.confidence == CONFIDENCE_SLUG_PROBE


def test_slug_probe_generates_case_variants():
    """Variants for a multi-word name include lowercase, PascalCase,
    camelCase, and hyphenated forms."""
    variants = slug_variants("Wealth Simple")
    assert variants == [
        "wealthsimple", "WealthSimple",
        "wealthSimple", "wealth-simple",
    ]
    # Single-word names produce only lowercase + PascalCase.
    assert slug_variants("Wealthsimple") == [
        "wealthsimple", "Wealthsimple",
    ]
    # Legal suffix stripped before variant generation.
    assert slug_variants("Acme Inc.") == ["acme", "Acme"]


def test_slug_probe_stores_exact_case_on_200(monkeypatch):
    """When a non-lowercase variant is the one that returns 200,
    the slug stored on the result is that exact variant — not
    lowercased (decision #29)."""
    _silence_sleep(monkeypatch)

    def dispatch(url):
        # Only the PascalCase variant on Lever returns 200.
        if "api.lever.co/v0/postings/WealthSimple" in url:
            return _MockResp(200)
        return _MockResp(404)

    _install_mock_get(monkeypatch, dispatch)
    detector = ATSDetector(rate_limit=0)
    result = detector.detect("Wealth Simple", domain=None)
    assert result is not None
    assert result.platform == "lever"
    assert result.slug == "WealthSimple"


# --- Workable false-positive guard ---------------------------------

def test_slug_probe_workable_requires_non_empty_jobs(monkeypatch):
    """Workable's widget API returns 200 for ANY slug. The detector
    must reject responses with an empty jobs array — otherwise it
    falsely detects every probed company as a Workable user."""
    _silence_sleep(monkeypatch)

    def dispatch(url):
        if "apply.workable.com/api/v1/widget/accounts/acme" in url:
            # Real Workable behavior for unknown slugs
            return _MockResp(
                200, '{"name":"Acme","description":null,"jobs":[]}',
            )
        return _MockResp(404)

    _install_mock_get(monkeypatch, dispatch)
    detector = ATSDetector(rate_limit=0)
    result = detector.detect("Acme", domain=None)
    assert result is None, (
        "expected detector to reject Workable 200-with-empty-jobs"
    )


def test_slug_probe_workable_accepts_when_jobs_present(monkeypatch):
    """When Workable's response has at least one job, the slug is a
    legitimate active Workable account."""
    _silence_sleep(monkeypatch)

    def dispatch(url):
        if "apply.workable.com/api/v1/widget/accounts/acme" in url:
            return _MockResp(
                200,
                '{"name":"Acme","jobs":[{"id":"abc","title":"Engineer"}]}',
            )
        return _MockResp(404)

    _install_mock_get(monkeypatch, dispatch)
    detector = ATSDetector(rate_limit=0)
    result = detector.detect("Acme", domain=None)
    assert result is not None
    assert result.platform == "workable"
    assert result.slug == "acme"


# --- Common behavior -----------------------------------------------

def test_404_skipped_gracefully(monkeypatch):
    """If every endpoint returns 404, detect() returns None."""
    _silence_sleep(monkeypatch)
    _install_mock_get(monkeypatch, lambda url: _MockResp(404))
    detector = ATSDetector(rate_limit=0)
    result = detector.detect("Acme", domain="acme.com")
    assert result is None


def test_connection_error_skipped_gracefully(monkeypatch):
    """A requests.RequestException is swallowed; cascade continues."""
    _silence_sleep(monkeypatch)
    import requests
    call_log = []

    def dispatch(url):
        call_log.append(url)
        # First careers-page fetch raises; everything else 404.
        if "/careers" in url and len(call_log) == 1:
            raise requests.ConnectionError("down")
        return _MockResp(404)

    # We can't use _install_mock_get for raising — wrap manually.
    monkeypatch.setattr(
        "engine.discovery.ats_detector.requests.get",
        lambda url, **kw: dispatch(url),
    )
    detector = ATSDetector(rate_limit=0)
    result = detector.detect("Acme", domain="acme.com")
    assert result is None
    # Cascade kept going past the first failure
    assert len(call_log) > 1


def test_rate_limit_enforced(monkeypatch):
    """The detector throttles HTTP requests by calling time.sleep
    with a positive value bounded by rate_limit."""
    sleeps: list[float] = []
    monkeypatch.setattr(
        "engine.discovery.ats_detector.time.sleep",
        lambda s: sleeps.append(s),
    )
    _install_mock_get(monkeypatch, lambda url: _MockResp(404))
    detector = ATSDetector(rate_limit=0.5)
    # No domain -> skip signal 2; signal 3 issues many requests.
    detector.detect("Acme Corp", domain=None)
    positive = [s for s in sleeps if s > 0]
    assert positive, "expected at least one rate-limit sleep"
    for s in positive:
        assert 0 < s <= 0.5 + 0.001, f"sleep {s} outside rate_limit"


def test_confidence_values_correct(monkeypatch):
    """The three confidence constants match the architecture doc:
    1.0 / 0.9 / 0.7."""
    assert CONFIDENCE_OVERRIDE == 1.0
    assert CONFIDENCE_CAREERS_PAGE == 0.9
    assert CONFIDENCE_SLUG_PROBE == 0.7


# --- detect_batch (full DB integration) ---------------------------

def test_detect_batch_persists_results(tmp_path, monkeypatch):
    """detect_batch writes platform/slug/detected_at/method/
    confidence to the company row on success, and writes
    detected_at + method='not_detected' on miss."""
    _silence_sleep(monkeypatch)

    def dispatch(url):
        # Acme: greenhouse slug probe 200.
        if "boards-api.greenhouse.io/v1/boards/acme/jobs" in url:
            return _MockResp(200)
        return _MockResp(404)

    _install_mock_get(monkeypatch, dispatch)

    t = Tracker(profile_id="test", db_path=tmp_path / "t.db")
    try:
        # Two companies: Acme should detect (greenhouse), Foo shouldn't.
        cid_a = t.upsert_company(name="Acme")
        cid_f = t.upsert_company(name="Foo")
        # Give them domains to skip signal 2 quickly.
        t.update_company_ats(cid_a, canonical_domain="acme.com")
        t.update_company_ats(cid_f, canonical_domain="foo.com")

        detector = ATSDetector(rate_limit=0)
        result = detector.detect_batch(t)

        assert result["detected"] == 1
        assert result["not_detected"] == 1
        assert result["errors"] == 0
        assert result["by_platform"] == {"greenhouse": 1}

        row_a = t.get_company_by_id(cid_a)
        assert row_a["ats_platform"] == "greenhouse"
        assert row_a["ats_slug"] == "acme"
        assert row_a["ats_detection_method"] == "slug_probe"
        assert row_a["ats_detection_confidence"] == CONFIDENCE_SLUG_PROBE
        assert row_a["ats_detected_at"] is not None

        row_f = t.get_company_by_id(cid_f)
        assert row_f["ats_platform"] is None
        assert row_f["ats_slug"] is None
        assert row_f["ats_detection_method"] == "not_detected"
        assert row_f["ats_detection_confidence"] == 0.0
        assert row_f["ats_detected_at"] is not None
    finally:
        t.close()


def test_detect_batch_skips_already_detected(tmp_path, monkeypatch):
    """Companies whose ats_detected_at is already populated are
    skipped (the SELECT filters on IS NULL). The mocked HTTP layer
    asserts no requests are even attempted."""
    _silence_sleep(monkeypatch)

    call_count = {"n": 0}

    def dispatch(url):
        call_count["n"] += 1
        return _MockResp(200)

    _install_mock_get(monkeypatch, dispatch)

    t = Tracker(profile_id="test", db_path=tmp_path / "t.db")
    try:
        cid = t.upsert_company(name="Acme")
        # Mark as already attempted.
        t.update_company_ats(
            cid,
            ats_detected_at="2026-05-01T00:00:00+00:00",
            ats_detection_method="not_detected",
            ats_detection_confidence=0.0,
        )

        detector = ATSDetector(rate_limit=0)
        result = detector.detect_batch(t)

        assert result["detected"] == 0
        assert result["not_detected"] == 0
        # No HTTP fetched because the company was filtered out.
        assert call_count["n"] == 0
    finally:
        t.close()
