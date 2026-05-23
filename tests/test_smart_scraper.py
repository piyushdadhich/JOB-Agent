"""Unit tests for engine.discovery.smart_scraper.

The actual SmartScraperGraph spins up a headless Playwright browser and
calls Ollama, neither of which we want in unit tests. Tests inject a
fake graph factory and assert behaviour around prompt construction,
result parsing, and error handling.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Skip the whole file when the optional [scraper] extra isn't
# installed — engine.discovery.smart_scraper imports scrapegraphai
# unconditionally at module load.
pytest.importorskip("scrapegraphai")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.smart_scraper import (  # noqa: E402
    SmartScraper,
    build_config,
    _coerce_result,
    _normalize_dict,
    _normalize_listings,
)


# --- Fake graph factory ----------------------------------------------

class FakeGraph:
    """Stand-in for SmartScraperGraph that records inputs and returns a
    pre-canned result. Constructor signature mirrors the real class."""

    last_prompt = None
    last_source = None
    last_config = None
    next_result = None
    raise_on_run = None

    def __init__(self, *, prompt, source, config, schema=None):
        FakeGraph.last_prompt = prompt
        FakeGraph.last_source = source
        FakeGraph.last_config = config

    def run(self):
        if FakeGraph.raise_on_run is not None:
            raise FakeGraph.raise_on_run
        return FakeGraph.next_result


@pytest.fixture(autouse=True)
def reset_fake():
    FakeGraph.last_prompt = None
    FakeGraph.last_source = None
    FakeGraph.last_config = None
    FakeGraph.next_result = None
    FakeGraph.raise_on_run = None
    yield


# --- build_config ----------------------------------------------------

def test_build_config_defaults():
    cfg = build_config()
    assert cfg["llm"]["model"] == "ollama/gemma3:4b"
    assert cfg["llm"]["format"] == "json"
    assert cfg["llm"]["temperature"] == 0.0
    assert cfg["headless"] is True


def test_build_config_overrides():
    cfg = build_config(model="ollama/foo", headless=False, verbose=True)
    assert cfg["llm"]["model"] == "ollama/foo"
    assert cfg["headless"] is False
    assert cfg["verbose"] is True


# --- _coerce_result --------------------------------------------------

def test_coerce_result_passes_dict_through():
    assert _coerce_result({"a": 1}) == {"a": 1}


def test_coerce_result_passes_list_through():
    assert _coerce_result([{"a": 1}]) == [{"a": 1}]


def test_coerce_result_parses_json_string():
    assert _coerce_result('{"a": 1}') == {"a": 1}


def test_coerce_result_strips_code_fence():
    assert _coerce_result('```json\n{"a": 1}\n```') == {"a": 1}


def test_coerce_result_returns_none_for_malformed_json():
    assert _coerce_result("not json") is None


def test_coerce_result_returns_none_for_empty_string():
    assert _coerce_result("") is None
    assert _coerce_result("   ") is None


def test_coerce_result_returns_none_for_none():
    assert _coerce_result(None) is None


# --- _normalize_listings ---------------------------------------------

def test_normalize_listings_array_of_dicts():
    assert _normalize_listings([{"title": "PM"}, {"title": "EM"}]) == [
        {"title": "PM"}, {"title": "EM"},
    ]


def test_normalize_listings_unwraps_envelope():
    out = _normalize_listings({"job_postings": [{"title": "PM"}]})
    assert out == [{"title": "PM"}]


def test_normalize_listings_promotes_single_posting():
    out = _normalize_listings({"title": "PM", "apply_url": "https://x"})
    assert out == [{"title": "PM", "apply_url": "https://x"}]


def test_normalize_listings_filters_non_dict_items():
    out = _normalize_listings([{"title": "PM"}, "junk", None])
    assert out == [{"title": "PM"}]


def test_normalize_listings_returns_empty_for_unknown_shape():
    assert _normalize_listings(42) == []
    assert _normalize_listings(None) == []
    assert _normalize_listings({"unrelated": "shape"}) == []


# --- _normalize_dict -------------------------------------------------

def test_normalize_dict_passes_dict_through():
    assert _normalize_dict({"a": 1}) == {"a": 1}


def test_normalize_dict_unwraps_single_key_envelope():
    assert _normalize_dict({"content": {"title": "PM"}}) == {"title": "PM"}


def test_normalize_dict_unwraps_single_item_list():
    assert _normalize_dict([{"title": "PM"}]) == {"title": "PM"}


def test_normalize_dict_returns_none_for_unknown_shape():
    assert _normalize_dict(None) is None
    assert _normalize_dict("string") is None


# --- SmartScraper end-to-end (with fake graph) -----------------------

def test_extract_job_listings_returns_normalized_list():
    FakeGraph.next_result = [
        {"title": "PM", "company": "TD"},
        {"title": "EM", "company": "BMO"},
    ]
    s = SmartScraper(graph_factory=FakeGraph)
    out = s.extract_job_listings("https://example.com/jobs")
    assert len(out) == 2
    assert out[0]["title"] == "PM"
    # Verify the graph was called with the listings prompt
    assert FakeGraph.last_source == "https://example.com/jobs"
    assert "Extract ALL job postings" in FakeGraph.last_prompt


def test_extract_job_listings_returns_empty_on_graph_failure():
    FakeGraph.raise_on_run = RuntimeError("ollama unreachable")
    s = SmartScraper(graph_factory=FakeGraph)
    assert s.extract_job_listings("https://example.com/jobs") == []


def test_extract_job_listings_returns_empty_on_malformed_json():
    FakeGraph.next_result = "this is not json"
    s = SmartScraper(graph_factory=FakeGraph)
    assert s.extract_job_listings("https://example.com/jobs") == []


def test_extract_job_detail_returns_dict():
    FakeGraph.next_result = {
        "title": "Senior PM", "company": "TD", "location": "Toronto",
    }
    s = SmartScraper(graph_factory=FakeGraph)
    out = s.extract_job_detail("https://example.com/jobs/123")
    assert out["title"] == "Senior PM"
    assert "full job posting details" in FakeGraph.last_prompt


def test_extract_job_detail_returns_none_on_failure():
    FakeGraph.raise_on_run = RuntimeError("boom")
    s = SmartScraper(graph_factory=FakeGraph)
    assert s.extract_job_detail("https://example.com/jobs/123") is None


def test_extract_company_info_returns_dict():
    FakeGraph.next_result = {
        "name": "TD Bank", "industry": "Banking",
        "description": "Canadian bank", "size_hint": "10000+",
    }
    s = SmartScraper(graph_factory=FakeGraph)
    out = s.extract_company_info("https://td.com/about")
    assert out["name"] == "TD Bank"
    assert "company information" in FakeGraph.last_prompt


def test_smart_scraper_uses_custom_model():
    s = SmartScraper(model="ollama/llama3", graph_factory=FakeGraph)
    FakeGraph.next_result = []
    s.extract_job_listings("https://example.com")
    assert FakeGraph.last_config["llm"]["model"] == "ollama/llama3"
