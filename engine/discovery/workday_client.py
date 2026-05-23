"""Workday ATS public job-board feed client.

Fetches all open jobs from configured Workday CXS endpoints. The API
is undocumented but stable across hundreds of tenants.

Listings:  POST {base}/wday/cxs/{tenant}/{site}/jobs
Detail:    GET  {base}/wday/cxs/{tenant}/{site}/job/{externalPath}

Each tenant has its own base URL: https://{tenant}.wdN.myworkdayjobs.com
where N is the data-center digit (1, 3, 5, 10, 103 all seen in the wild).

Per-tenant config lives in config/profiles/{profile}.yaml under
`workday.tenants`. Missing tenants, 404s, and shape changes are
logged and skipped — never abort the batch.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Iterator, Optional

import requests
from bs4 import BeautifulSoup

from engine.discovery.base import OpportunityRecord

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": USER_AGENT,
}


def _strip_html(html: str) -> str:
    if not html:
        return ""
    return BeautifulSoup(html, "html.parser").get_text(
        separator=" ", strip=True,
    )


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


class WorkdayClient:
    """Fetch open jobs from configured Workday tenants."""

    name = "workday"

    def __init__(
        self,
        tenants: list[dict],
        rate_limit: float = 2.0,
        fetch_details: bool = True,
        page_size: int = 20,
        timeout: float = 15.0,
    ):
        self.tenants = list(tenants)
        self.rate_limit = float(rate_limit)
        self.fetch_details = bool(fetch_details)
        self.page_size = int(page_size)
        self.timeout = float(timeout)

    def fetch(self) -> Iterator[OpportunityRecord]:
        for cfg in self.tenants:
            try:
                yield from self._fetch_tenant(cfg)
            except Exception as e:
                logger.warning(
                    "Workday tenant %r failed: %s", cfg.get("name"), e,
                )

    def _base_url(self, cfg: dict) -> str:
        return (
            f"https://{cfg['tenant']}.wd{cfg['wd_server']}"
            f".myworkdayjobs.com"
        )

    def _fetch_tenant(self, cfg: dict) -> Iterator[OpportunityRecord]:
        base = self._base_url(cfg)
        list_url = (
            f"{base}/wday/cxs/{cfg['tenant']}/{cfg['site']}/jobs"
        )

        offset = 0
        while True:
            time.sleep(self.rate_limit)
            payload = {
                "appliedFacets": {},
                "limit": self.page_size,
                "offset": offset,
                "searchText": "",
            }
            resp = requests.post(
                list_url, json=payload, headers=HEADERS,
                timeout=self.timeout,
            )
            if resp.status_code == 404:
                logger.info(
                    "Workday tenant %r returned 404; skipping",
                    cfg.get("name"),
                )
                return
            resp.raise_for_status()
            data = resp.json() or {}

            postings = data.get("jobPostings") or []
            if not postings:
                return

            for posting in postings:
                yield self._to_record(posting, cfg, base)

            total = int(data.get("total") or 0)
            offset += len(postings)
            if total and offset >= total:
                return
            # Hard cap: stop after 50 pages per tenant (1000 jobs).
            # Discovery is incremental; daily run pulls fresh ones.
            if offset >= self.page_size * 50:
                logger.info(
                    "Workday tenant %r reached 50-page cap (offset=%d)",
                    cfg.get("name"), offset,
                )
                return

    def _fetch_detail(
        self, base: str, cfg: dict, external_path: str,
    ) -> Optional[dict]:
        time.sleep(self.rate_limit)
        url = (
            f"{base}/wday/cxs/{cfg['tenant']}/{cfg['site']}/job"
            f"{external_path}"
        )
        try:
            resp = requests.get(
                url, headers=HEADERS, timeout=self.timeout,
            )
            if resp.status_code != 200:
                return None
            return resp.json()
        except Exception as e:
            logger.warning(
                "Workday detail fetch failed for %s: %s",
                external_path, e,
            )
            return None

    def _to_record(
        self, posting: dict, cfg: dict, base: str,
    ) -> OpportunityRecord:
        title = posting.get("title") or ""
        location = posting.get("locationsText") or ""
        external_path = posting.get("externalPath") or ""
        source_url = (
            f"{base}/en-US/{cfg['site']}{external_path}"
            if external_path else ""
        )

        # Bullet fields are typically [job_id, employment_type, ...] —
        # NOT description. Default to title; replace with full text if
        # detail fetch is on and succeeds.
        posting_text = title
        posted_at: Optional[datetime] = None

        if self.fetch_details and external_path:
            detail = self._fetch_detail(base, cfg, external_path)
            if detail:
                info = detail.get("jobPostingInfo") or {}
                desc_html = info.get("jobDescription") or ""
                desc = _strip_html(desc_html)
                if desc:
                    posting_text = desc
                # Workday's startDate is the ISO posting date; postedOn
                # is a relative string like "Posted Yesterday".
                posted_at = _parse_iso(info.get("startDate"))
                # Some tenants put the canonical URL here.
                ext = info.get("externalUrl")
                if ext:
                    source_url = ext

        return OpportunityRecord(
            source=self.name,
            source_url=source_url,
            employer=cfg.get("name") or cfg.get("tenant", ""),
            title=title,
            location=location,
            posting_text=posting_text,
            posted_at=posted_at,
            date_discovered=datetime.now(timezone.utc),
            raw_payload={"workday_listing": posting},
            search_context={
                "workday_tenant": cfg.get("tenant"),
                "workday_site": cfg.get("site"),
            },
        )
