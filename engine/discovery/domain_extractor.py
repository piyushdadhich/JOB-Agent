"""Best-effort domain extraction for the ATS Detector.

Many companies in the tracker have no canonical_domain. The ATS
Detector needs a domain to probe for ATS embeds (signal 2 of the
3-signal cascade). This module derives a domain from one of:

  1. An opportunity's source_url, when it points at the employer's
     own site (not a known aggregator/ATS host).
  2. A slugified company name as `{slug}.com` — the slug-fallback
     of last resort.

It does NOT verify that the domain resolves or that the page exists;
the ATS Detector handles that downstream. The goal here is to
populate companies.canonical_domain with a reasonable starting guess.
"""
from __future__ import annotations

import logging
import unicodedata
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


# Hosts that aren't the employer's own site. These cover job
# aggregators (where source_url == aggregator URL) and ATS-hosted
# career boards (where the slug is in the path, not the host).
_AGGREGATOR_HOSTS = frozenset({
    # Aggregators
    "indeed.com",
    "glassdoor.com",
    "glassdoor.ca",
    "linkedin.com",
    "google.com",
    "jobs.google.com",
    "ziprecruiter.com",
    "monster.com",
    "monster.ca",
    "simplyhired.com",
    "careerbuilder.com",
    "dice.com",
    "wellfound.com",
    "angel.co",
    "jobbank.gc.ca",
    # ATS-hosted boards (the slug is in the path, not the host)
    "boards.greenhouse.io",
    "boards-api.greenhouse.io",
    "jobs.lever.co",
    "api.lever.co",
    "jobs.ashbyhq.com",
    "api.ashbyhq.com",
    "apply.workable.com",
    "jobs.personio.de",
    "jobs.personio.com",
    "recruitee.com",
})

# Legal suffixes stripped before slugifying. Order matters — longest
# variants first so "Foo Inc." doesn't get partially matched.
_LEGAL_SUFFIXES = (
    " incorporated", ", incorporated",
    ", inc.", ", inc", " inc.", " inc",
    ", corp.", ", corp", " corp.", " corp", " corporation",
    ", llc", " llc", ", llp", " llp",
    ", ltd.", ", ltd", " ltd.", " ltd", " limited",
    " plc",
    " co.", " company",
)


def _is_aggregator(host: str) -> bool:
    """True if the host is a known aggregator/ATS host (not the
    employer's own site). Matches on suffix so subdomains of
    aggregators (e.g. ca.indeed.com) are also rejected."""
    h = host.lower()
    if h.startswith("www."):
        h = h[4:]
    if h in _AGGREGATOR_HOSTS:
        return True
    return any(h.endswith("." + a) for a in _AGGREGATOR_HOSTS)


def extract_domain_from_url(url: str) -> str | None:
    """Return the registrable host from a URL, sans leading 'www.',
    or None if the URL points at a known aggregator/ATS host.

    Does not perform DNS resolution; pure string parsing.
    """
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except Exception:
        return None
    host = (parts.netloc or "").lower()
    # Strip user-info if present (e.g. user:pass@host)
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    # Strip port
    if ":" in host:
        host = host.rsplit(":", 1)[0]
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    if _is_aggregator(host):
        return None
    return host or None


def _slugify_for_domain(name: str) -> str | None:
    """Lowercase, strip legal suffixes/accents/punctuation, return a
    pure a-z0-9 slug. None for empty / pure-non-ASCII inputs."""
    if not name or not name.strip():
        return None
    s = name.strip().lower()
    for suffix in _LEGAL_SUFFIXES:
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
            break
    s = s.replace("&", "and")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = "".join(c for c in s if c.isalnum())
    return s or None


def extract_domain(company_name: str) -> str | None:
    """Slug-fallback domain guess from a company name.

    Strips legal suffixes (Inc., Corp., LLC, …), accents, and
    punctuation, then returns `{slug}.com`. Returns None for
    empty / whitespace / pure-non-ASCII inputs.

    Does NOT consult any opportunities or the database — that's
    backfill_domains' job. This is the strategy-3 fallback when no
    employer URL is available.
    """
    slug = _slugify_for_domain(company_name)
    if slug is None:
        return None
    return f"{slug}.com"


def backfill_domains(tracker) -> dict:
    """For every company with canonical_domain IS NULL, derive a
    domain and persist it.

    Strategy per company:
      1. Walk that company's opportunities.source_url (newest first).
         First non-aggregator host wins.
      2. Fall back to extract_domain(name) — `{slug}.com`.

    Companies whose name slugifies to None and have no usable
    source_url stay NULL.

    Returns: {"updated": n, "still_null": n}
    """
    rows = tracker._query_all(
        "SELECT id, name FROM companies "
        "WHERE canonical_domain IS NULL"
    )
    updated = 0
    still_null = 0
    for row in rows:
        cid = row["id"]
        name = row["name"]

        opp_rows = tracker._query_all(
            "SELECT source_url FROM opportunities "
            "WHERE company_id = ? "
            "ORDER BY date_discovered DESC",
            (cid,),
        )
        domain: str | None = None
        for opp in opp_rows:
            domain = extract_domain_from_url(opp["source_url"])
            if domain:
                break

        if domain is None:
            domain = extract_domain(name)

        if domain:
            tracker.update_company_ats(cid, canonical_domain=domain)
            updated += 1
        else:
            still_null += 1

    logger.info(
        "backfill_domains: updated=%d still_null=%d",
        updated, still_null,
    )
    return {"updated": updated, "still_null": still_null}
