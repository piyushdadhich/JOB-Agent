"""ATS Detector — 3-signal cascade for identifying which ATS each
company uses (Phase 5a Step 6).

Cascade:
  Signal 1  manual_override   confidence 1.0
  Signal 2  careers_page      confidence 0.9
  Signal 3  slug_probe        confidence 0.7

Slug case sensitivity matters (decision #29). The slug that returns
200 from an ATS endpoint is stored on companies.ats_slug verbatim —
never lowercased — because boards-api.greenhouse.io routes are case-
sensitive in the path.

All HTTP requests are throttled by the configured rate_limit
(default 1.0 sec). Failures are silent: 404, connection error, or
timeout cause the cascade to fall through to the next signal.
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# --- Endpoint configs ---------------------------------------------

# Probe order matters: first 200 wins. Greenhouse first because it
# has the largest market share (cheapest cascade exit for the
# common case).
_PLATFORM_PROBES: tuple[tuple[str, str], ...] = (
    ("greenhouse",
     "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"),
    ("lever",
     "https://api.lever.co/v0/postings/{slug}?mode=json&limit=1"),
    ("ashby",
     "https://api.ashbyhq.com/posting-api/job-board/{slug}"),
    ("workable",
     "https://apply.workable.com/api/v1/widget/accounts/{slug}"),
    ("personio",
     "https://{slug}.jobs.personio.de/xml?language=en"),
    ("recruitee",
     "https://{slug}.recruitee.com/api/offers"),
)


# Regex patterns scanning a careers-page HTML body for ATS embeds.
# Group 1 is always the slug.
_EMBED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("greenhouse",
     re.compile(
         r"boards(?:-api)?\.greenhouse\.io/(?:v1/boards/)?"
         r"([A-Za-z0-9_-]+)"
     )),
    ("lever",
     re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]+)")),
    ("ashby",
     re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)")),
    ("workable",
     re.compile(
         r"apply\.workable\.com/(?:api/v1/widget/accounts/)?"
         r"([A-Za-z0-9_-]+)"
     )),
    ("personio",
     re.compile(r"([A-Za-z0-9_-]+)\.jobs\.personio\.(?:de|com)")),
    ("recruitee",
     re.compile(r"([A-Za-z0-9_-]+)\.recruitee\.com")),
)


# Careers-page paths to fetch when probing a domain. Order matters
# only as a tiebreaker — first match per platform wins.
_CAREERS_PATHS: tuple[str, ...] = (
    "/careers",
    "/careers/",
    "/jobs",
    "/jobs/",
    "/about/careers",
)


# Legal suffixes stripped from company names before deriving slug
# variants. Order matters — longest first.
_LEGAL_SUFFIXES = (
    " incorporated", ", incorporated",
    ", inc.", ", inc", " inc.", " inc",
    ", corp.", ", corp", " corp.", " corp", " corporation",
    ", llc", " llc", ", llp", " llp",
    ", ltd.", ", ltd", " ltd.", " ltd", " limited",
    " plc",
    " co.", " company",
)


# Confidence values per detection method (decision #23 in
# Job_Agent_Architecture_v4.md).
CONFIDENCE_OVERRIDE = 1.0
CONFIDENCE_CAREERS_PAGE = 0.9
CONFIDENCE_SLUG_PROBE = 0.7


@dataclass
class ATSDetectionResult:
    """Successful ATS detection. The detection_url is the URL that
    confirmed it (the careers page that contained the embed, or the
    ATS API endpoint that returned 200)."""
    platform: str
    slug: str
    method: str          # manual_override | careers_page | slug_probe
    confidence: float
    detection_url: str


# --- Slug variant generator (also used in tests) ------------------

def _clean_for_slug(name: str) -> str:
    """Lowercase + strip legal suffixes/accents/punctuation, but
    preserve word boundaries (spaces) so callers can produce variants
    of multi-word names."""
    if not name:
        return ""
    s = name.strip().lower()
    for suffix in _LEGAL_SUFFIXES:
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
            break
    s = s.replace("&", "and")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = "".join(c if c.isalnum() else " " for c in s)
    s = " ".join(s.split())
    return s


def slug_variants(company_name: str) -> list[str]:
    """Generate the case-sensitive slug variants probed against each
    ATS endpoint.

    For "Wealthsimple"     -> ['wealthsimple', 'Wealthsimple']
    For "Wealth Simple"    -> ['wealthsimple', 'WealthSimple',
                                'wealthSimple', 'wealth-simple']

    Variants are unique-preserving order. The variant that returns
    200 from an ATS endpoint is stored verbatim on
    companies.ats_slug (decision #29).
    """
    cleaned = _clean_for_slug(company_name)
    if not cleaned:
        return []
    parts = cleaned.split()
    variants: list[str] = []
    # lowercase no-spaces
    variants.append("".join(parts))
    # PascalCase no-spaces
    variants.append("".join(p[:1].upper() + p[1:] for p in parts))
    if len(parts) > 1:
        # camelCase
        first = parts[0]
        rest = [p[:1].upper() + p[1:] for p in parts[1:]]
        variants.append(first + "".join(rest))
        # hyphenated lowercase
        variants.append("-".join(parts))

    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


# --- Per-platform response validation -----------------------------

def _is_valid_response(platform: str, resp) -> bool:
    """True if `resp` looks like a real ATS-account hit for `platform`.

    Most ATSs (Greenhouse, Lever, Ashby, Personio, Recruitee) return
    404 for unknown slugs, so a 200 alone is sufficient. Workable
    breaks this convention: its `/api/v1/widget/accounts/{slug}`
    endpoint returns 200 with `{"name": "...", "jobs": []}` for
    arbitrary slugs (we observed false positives like 'bdo',
    'amazon-web-services', 'jpmorganchase' all returning 200 with an
    empty jobs list).

    For Workable, require at least one job in the response. False
    negatives during low-hiring periods are acceptable; false
    positives would pollute workable_api.slugs and waste discovery
    cycles fetching empty boards.
    """
    if platform != "workable":
        return True
    try:
        data = resp.json()
    except Exception:
        return False
    jobs = data.get("jobs") if isinstance(data, dict) else None
    return bool(jobs)


# --- Detector class -----------------------------------------------

class ATSDetector:
    """Detect which ATS a company uses via 3-signal cascade.

    Construction is cheap; HTTP work happens in detect()/detect_batch().
    The detector maintains a last-request timestamp and throttles all
    HTTP requests to at-least rate_limit seconds apart.
    """

    def __init__(
        self,
        rate_limit: float = 1.0,
        request_timeout: int = 10,
    ):
        self.rate_limit = float(rate_limit)
        self.request_timeout = int(request_timeout)
        self._last_request_at: float = 0.0

    # -- HTTP helpers --

    def _throttle(self) -> None:
        now = time.monotonic()
        wait = self.rate_limit - (now - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _get(self, url: str) -> Optional[requests.Response]:
        self._throttle()
        try:
            return requests.get(
                url, timeout=self.request_timeout,
                allow_redirects=True,
            )
        except requests.RequestException as e:
            logger.debug("ATSDetector GET %s failed: %s", url, e)
            return None

    # -- Signal 1: manual override --

    def _detect_via_override(
        self,
        company_name: str,
        overrides: Optional[dict],
    ) -> Optional[ATSDetectionResult]:
        if not overrides or not company_name:
            return None
        target = company_name.strip().lower()
        for key, value in overrides.items():
            if str(key).strip().lower() != target:
                continue
            if not isinstance(value, dict):
                continue
            platform = value.get("platform")
            slug = value.get("slug")
            if platform and slug:
                return ATSDetectionResult(
                    platform=str(platform),
                    slug=str(slug),
                    method="manual_override",
                    confidence=CONFIDENCE_OVERRIDE,
                    detection_url="config:ats_overrides",
                )
        return None

    # -- Signal 2: careers page --

    def _detect_via_careers_page(
        self, domain: str,
    ) -> Optional[ATSDetectionResult]:
        if not domain:
            return None
        for path in _CAREERS_PATHS:
            url = f"https://{domain}{path}"
            resp = self._get(url)
            if resp is None or resp.status_code != 200:
                continue
            body = resp.text or ""
            for platform, pattern in _EMBED_PATTERNS:
                m = pattern.search(body)
                if m:
                    return ATSDetectionResult(
                        platform=platform,
                        slug=m.group(1),
                        method="careers_page",
                        confidence=CONFIDENCE_CAREERS_PAGE,
                        detection_url=url,
                    )
        return None

    # -- Signal 3: slug probe --

    def _detect_via_slug_probe(
        self, company_name: str,
    ) -> Optional[ATSDetectionResult]:
        variants = slug_variants(company_name)
        if not variants:
            return None
        for platform, url_template in _PLATFORM_PROBES:
            for variant in variants:
                url = url_template.format(slug=variant)
                resp = self._get(url)
                if resp is None:
                    continue
                if resp.status_code != 200:
                    continue
                if not _is_valid_response(platform, resp):
                    continue
                return ATSDetectionResult(
                    platform=platform,
                    slug=variant,
                    method="slug_probe",
                    confidence=CONFIDENCE_SLUG_PROBE,
                    detection_url=url,
                )
        return None

    # -- Public API --

    def detect(
        self,
        company_name: str,
        domain: Optional[str],
        overrides: Optional[dict] = None,
    ) -> Optional[ATSDetectionResult]:
        """Run the 3-signal cascade. Return the first hit, or None."""
        result = self._detect_via_override(company_name, overrides)
        if result:
            return result
        result = self._detect_via_careers_page(domain or "")
        if result:
            return result
        result = self._detect_via_slug_probe(company_name)
        if result:
            return result
        return None

    def detect_batch(
        self,
        tracker,
        overrides: Optional[dict] = None,
        limit: Optional[int] = None,
    ) -> dict:
        """Run detection on companies that haven't been attempted
        yet (ats_detected_at IS NULL).

        On success: persists platform / slug / detected_at /
        method / confidence to the company row.
        On miss: persists detected_at + method='not_detected' so
        subsequent runs skip this company (caching).

        Returns:
          {"detected": n, "not_detected": n, "errors": n,
           "by_platform": {platform: n, ...}}
        """
        sql = (
            "SELECT id, name, canonical_domain "
            "FROM companies "
            "WHERE ats_detected_at IS NULL "
            "ORDER BY id ASC"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = tracker._query_all(sql)

        detected = 0
        not_detected = 0
        errors = 0
        by_platform: dict[str, int] = {}
        now = lambda: datetime.now(timezone.utc).isoformat()

        for row in rows:
            cid = row["id"]
            name = row["name"]
            domain = row["canonical_domain"]
            try:
                result = self.detect(name, domain, overrides=overrides)
            except Exception:
                logger.exception(
                    "ATS detection error for company %s (%s)",
                    cid, name,
                )
                errors += 1
                continue

            if result is not None:
                tracker.update_company_ats(
                    cid,
                    ats_platform=result.platform,
                    ats_slug=result.slug,
                    ats_detected_at=now(),
                    ats_detection_method=result.method,
                    ats_detection_confidence=result.confidence,
                )
                detected += 1
                by_platform[result.platform] = (
                    by_platform.get(result.platform, 0) + 1
                )
            else:
                tracker.update_company_ats(
                    cid,
                    ats_detected_at=now(),
                    ats_detection_method="not_detected",
                    ats_detection_confidence=0.0,
                )
                not_detected += 1

        logger.info(
            "ATS detect_batch: detected=%d not_detected=%d errors=%d "
            "by_platform=%s",
            detected, not_detected, errors, by_platform,
        )
        return {
            "detected": detected,
            "not_detected": not_detected,
            "errors": errors,
            "by_platform": by_platform,
        }
