"""Personio public XML job feed client.

Personio publishes a public XML feed at
https://{slug}.jobs.personio.de/xml?language=en (some accounts use
.com instead of .de). No authentication required.

The XML schema is `<workzag-jobs><position>...</position></workzag-jobs>`.
Each position carries id, name, office, department,
recruitingCategory, employmentType, schedule, and a `jobDescriptions`
wrapper with multiple `<jobDescription>` blocks (each with name +
CDATA-HTML value).

Fallback: try .de first, then .com on 404. Slight deviation from
the spec - we pass the working domain into `_to_record` so the
constructed apply URL points at the right domain.
"""
from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Iterator, Optional

import requests
from bs4 import BeautifulSoup

from engine.discovery.base import OpportunityRecord

logger = logging.getLogger(__name__)

PERSONIO_DOMAINS = ("jobs.personio.de", "jobs.personio.com")


class PersonioClient:
    """Fetch open positions from configured Personio accounts."""

    name = "personio_api"

    def __init__(self, slugs: list[str], rate_limit: float = 1.0):
        self.slugs = list(slugs)
        self.rate_limit = float(rate_limit)

    def fetch(self) -> Iterator[OpportunityRecord]:
        for slug in self.slugs:
            time.sleep(self.rate_limit)
            for domain in PERSONIO_DOMAINS:
                try:
                    yield from self._fetch_slug(slug, domain)
                    break  # success
                except requests.HTTPError as e:
                    if (
                        e.response is not None
                        and e.response.status_code == 404
                    ):
                        # try next domain
                        continue
                    logger.warning(
                        "Personio HTTP error for %s.%s: %s",
                        slug, domain, e,
                    )
                    break
                except Exception as e:
                    logger.warning(
                        "Personio fetch failed for %s.%s: %s",
                        slug, domain, e,
                    )
                    break

    def _fetch_slug(
        self, slug: str, domain: str,
    ) -> Iterator[OpportunityRecord]:
        url = f"https://{slug}.{domain}/xml"
        params = {"language": "en"}
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for pos in root.findall(".//position"):
            yield self._to_record(pos, slug, domain)

    def _to_record(
        self, pos: ET.Element, slug: str, domain: str,
    ) -> OpportunityRecord:
        def _text(tag: str) -> str:
            el = pos.find(tag)
            return (el.text or "").strip() if el is not None else ""

        desc_parts: list[str] = []
        for desc in pos.findall(".//jobDescription"):
            name_el = desc.find("name")
            value_el = desc.find("value")
            if name_el is not None and name_el.text:
                desc_parts.append(name_el.text.strip())
            if value_el is not None and value_el.text:
                desc_parts.append(
                    BeautifulSoup(value_el.text, "html.parser")
                    .get_text("\n", strip=True)
                )

        pid = _text("id")
        apply_url = (
            f"https://{slug}.{domain}/job/{pid}" if pid else ""
        )

        return OpportunityRecord(
            source=self.name,
            source_url=apply_url,
            employer=slug,
            title=_text("name"),
            location=_text("office"),
            posting_text="\n\n".join(desc_parts),
            posted_at=None,
            salary_min=None,
            salary_max=None,
            salary_currency=None,
            employer_industry=_text("recruitingCategory") or None,
            raw_payload={
                "xml_text": ET.tostring(pos, encoding="unicode"),
                "domain": domain,
            },
            search_context={"personio_slug": slug},
            date_discovered=datetime.now(timezone.utc),
        )
