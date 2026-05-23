"""Universal job-posting scraper backed by ScrapeGraphAI + Ollama.

ScrapeGraphAI fetches a page (via Playwright under the hood), feeds the
rendered DOM to a local LLM, and returns the structured fields named in
the prompt. Two upsides over hand-rolled BeautifulSoup parsers:

  1. No CSS selectors to maintain. The LLM reasons about the DOM, so
     the wrapper survives layout/class-name churn.
  2. Works on dynamic pages (SPAs, React-rendered ATSes) because the
     headless browser executes JS before the LLM sees the DOM.

This module is the LOCAL Ollama half of the Smart Agent upgrade. The
Cloud LLM (Gemma 4 31B) is reserved for the form-fill DECISION step
in engine/applicant/smart_filler.py — never used here.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from scrapegraphai.graphs import SmartScraperGraph

logger = logging.getLogger(__name__)


# --- Prompts -----------------------------------------------------------

LISTINGS_PROMPT = """Extract ALL job postings visible on this page.
For each posting, return a JSON object with these exact keys:
  - title: the job title (string)
  - company: the employer name (string, or null if not visible)
  - location: city/region (string, or null)
  - salary: salary text if shown (string, or null)
  - posted_date: when posted if shown (string, or null)
  - apply_url: the URL to apply or view the full posting (string)
  - description: short description or snippet if visible (string, or null)

Return a JSON array of objects. If no postings are found, return [].
Do NOT include navigation links, footer links, or non-job content."""


DETAIL_PROMPT = """Extract the full job posting details from this page.
Return a single JSON object with these exact keys:
  - title: job title (string)
  - company: employer name (string)
  - location: city/region/country (string)
  - salary_min: minimum salary as integer (or null)
  - salary_max: maximum salary as integer (or null)
  - salary_currency: currency code like CAD, USD (or null)
  - description: full job description text (string)
  - requirements: list of requirements/qualifications (array of strings)
  - benefits: list of benefits if listed (array of strings, or null)
  - employment_type: Full-time, Part-time, Contract, etc. (string, or null)
  - seniority_level: Junior, Mid, Senior, Director, etc. (string, or null)
  - apply_url: direct apply link if different from this page (string, or null)

Return ONLY the JSON object. No markdown, no preamble."""


COMPANY_PROMPT = """Extract company information from this page.
Return a JSON object with these exact keys:
  - name: company name (string)
  - industry: what industry they're in (string, or null)
  - size_hint: employee count or size range if mentioned (string, or null)
  - description: what the company does, 1-2 sentences (string)
  - careers_url: link to their careers/jobs page if visible (string, or null)

Return ONLY the JSON object."""


# --- Default config ----------------------------------------------------

DEFAULT_MODEL = "ollama/gemma3:4b"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MODEL_TOKENS = 8192


def build_config(
    model: str = DEFAULT_MODEL,
    temperature: float = DEFAULT_TEMPERATURE,
    headless: bool = True,
    verbose: bool = False,
    model_tokens: int = DEFAULT_MODEL_TOKENS,
) -> dict:
    """Construct a ScrapeGraphAI config dict.

    Pulled out so tests can build configs without instantiating
    SmartScraper (which would import scrapegraphai's heavy deps).
    """
    return {
        "llm": {
            "model": model,
            "temperature": temperature,
            "format": "json",
            "model_tokens": model_tokens,
        },
        "verbose": verbose,
        "headless": headless,
    }


# --- Wrapper -----------------------------------------------------------

class SmartScraper:
    """Wrapper around SmartScraperGraph with three extraction methods.

    Each method runs ONE local LLM call against the page and returns
    parsed Python objects. If the LLM emits malformed JSON (rare with
    format="json" but possible) the result is returned as None so
    callers can fall back to whatever they had before.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        headless: bool = True,
        verbose: bool = False,
        graph_factory=SmartScraperGraph,
    ):
        # graph_factory is injectable so unit tests can mock the
        # heavy SmartScraperGraph without patching imports.
        self.config = build_config(
            model=model, headless=headless, verbose=verbose,
        )
        self._graph_factory = graph_factory

    # ---- Internals ----

    def _run(self, prompt: str, source: str) -> Any:
        """Run a graph and return the parsed result, or None on failure.

        SmartScraperGraph.run() is typed as str, but with Ollama's
        format=json it usually returns a dict already. Handle both.
        """
        try:
            graph = self._graph_factory(
                prompt=prompt, source=source, config=self.config,
            )
            result = graph.run()
        except Exception as e:
            logger.warning("smart_scraper: graph run failed: %s", e)
            return None

        return _coerce_result(result)

    # ---- Public API ----

    def extract_job_listings(self, url: str) -> list[dict]:
        """Extract every visible job posting from a search-results page.

        Returns an empty list when the LLM cannot find any postings or
        when the response is malformed. Always a list — never None —
        so callers can iterate safely.
        """
        result = self._run(LISTINGS_PROMPT, url)
        return _normalize_listings(result)

    def extract_job_detail(self, url: str) -> Optional[dict]:
        """Extract full details from a single job posting page.

        Returns the dict the LLM produced (after key normalization), or
        None if extraction failed or the response was non-dict.
        """
        result = self._run(DETAIL_PROMPT, url)
        return _normalize_dict(result)

    def extract_company_info(self, url: str) -> Optional[dict]:
        """Extract company information from an about/careers page."""
        result = self._run(COMPANY_PROMPT, url)
        return _normalize_dict(result)


# --- Result normalization ---------------------------------------------

# Pulled out as module-level helpers so tests can exercise them
# without instantiating SmartScraper.

def _coerce_result(result: Any) -> Any:
    """SmartScraperGraph.run() may return dict, list, or JSON-as-string.
    Coerce to a Python object; return None on parse failure."""
    if result is None:
        return None
    if isinstance(result, (dict, list)):
        return result
    if isinstance(result, str):
        text = result.strip()
        if not text:
            return None
        # Some Ollama pipelines wrap JSON in ```json fences.
        if text.startswith("```"):
            text = _strip_code_fence(text)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(
                "smart_scraper: response was not valid JSON: %s", e,
            )
            return None
    return None


def _strip_code_fence(text: str) -> str:
    """Strip a leading ```json (or ```) and trailing ``` if present."""
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


def _normalize_listings(result: Any) -> list[dict]:
    """Coerce arbitrary LLM output into a clean list[dict] of listings.

    ScrapeGraphAI sometimes returns the array directly, sometimes wraps
    it in a single-key envelope like {"job_postings": [...]} or
    {"content": [...]}. Unwrap the common shapes; otherwise return [].
    """
    if result is None:
        return []
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict):
        # Single-posting page wrapped as object — promote to list.
        if "title" in result or "apply_url" in result:
            return [result]
        # Common envelope keys produced by various ScrapeGraphAI versions.
        for key in (
            "job_postings", "postings", "jobs", "results",
            "content", "data", "items",
        ):
            value = result.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _normalize_dict(result: Any) -> Optional[dict]:
    """Coerce arbitrary LLM output into a single dict, unwrapping a few
    common envelopes. Returns None if no dict shape can be recovered."""
    if isinstance(result, dict):
        # Single-key envelopes like {"job": {...}} or {"content": {...}}
        if len(result) == 1:
            (only_value,) = result.values()
            if isinstance(only_value, dict):
                return only_value
        return result
    if isinstance(result, list) and len(result) == 1 and isinstance(result[0], dict):
        return result[0]
    return None
