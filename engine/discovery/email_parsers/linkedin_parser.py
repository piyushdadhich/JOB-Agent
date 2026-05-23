"""LinkedIn job-notification email parser.

Matches: from @linkedin.com (or a subdomain) with subject containing
"job", "jobs", "opportunity", "you appeared in", or "matches"
(case-insensitive). LinkedIn sends digest emails with multiple job
cards -- extracts one OpportunityRecord per card.

Cards typically link to `linkedin.com/comm/jobs/view/{jobId}` with
the title text in the anchor.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from engine.discovery.base import OpportunityRecord

_LI_DOMAIN_SUFFIXES = ("@linkedin.com", "@e.linkedin.com")
_SUBJECT_HINTS = ("job", "opportunity", "you appeared in", "matches")
_JOB_VIEW_RE = re.compile(
    r"https?://(?:www\.)?linkedin\.com/(?:comm/)?jobs/view/\d+",
    re.IGNORECASE,
)
_JOB_ID_RE = re.compile(r"/jobs/view/(\d+)", re.IGNORECASE)


class LinkedInParser:
    name = "linkedin"

    def matches(self, email: dict) -> bool:
        sender = (email.get("from_email") or "").lower()
        if not any(sender.endswith(s) for s in _LI_DOMAIN_SUFFIXES):
            return False
        subj = (email.get("subject") or "").lower()
        return any(h in subj for h in _SUBJECT_HINTS)

    def parse(self, email: dict) -> list[OpportunityRecord]:
        html = email.get("html_body") or ""
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        records: list[OpportunityRecord] = []
        seen: set[str] = set()
        for a in soup.find_all("a", href=_JOB_VIEW_RE):
            href = a.get("href", "")
            # Dedup by job ID — the same posting can appear under
            # both linkedin.com and www.linkedin.com hosts with
            # different tracking params.
            m = _JOB_ID_RE.search(href)
            job_id = m.group(1) if m else href.split("?", 1)[0]
            if job_id in seen:
                continue
            seen.add(job_id)
            title = a.get_text(" ", strip=True)
            if not title or len(title) > 200:
                continue
            records.append(OpportunityRecord(
                source="email_monitor:linkedin",
                source_url=href,
                employer="",  # LinkedIn anchor doesn't reliably expose this
                title=title,
                location="",
                posting_text=title,
                posted_at=email.get("date"),
                date_discovered=datetime.now(timezone.utc),
                raw_payload={
                    "subject": email.get("subject"),
                    "gmail_message_id": email.get("id"),
                },
                search_context={"email_parser": self.name},
            ))
        return records
