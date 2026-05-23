"""Recruiter cold-email parser.

Matches: sender's email domain is in the configured recruiter_domains list.
Extracts a single OpportunityRecord per matching email -- the email itself
is the lead. Title = email subject. Employer = recruiter firm name (derived
from domain). posting_text = email body text. source_url = first job-like
URL in the body, or the gmail message URL.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from engine.discovery.base import OpportunityRecord

_URL_RE = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
_JOB_HINTS = ("job", "career", "position", "opportunity", "role", "apply")


def _first_job_url(text: str) -> Optional[str]:
    for url in _URL_RE.findall(text or ""):
        low = url.lower()
        if any(h in low for h in _JOB_HINTS):
            return url
    return None


class RecruiterParser:
    name = "recruiter"

    def __init__(self, domains: list[str]):
        self.domains = [d.lower().lstrip("@") for d in (domains or [])]

    def matches(self, email: dict) -> bool:
        domain = (email.get("from_domain") or "").lower()
        if not domain:
            return False
        return any(
            domain == d or domain.endswith("." + d) for d in self.domains
        )

    def parse(self, email: dict) -> list[OpportunityRecord]:
        domain = email["from_domain"]
        body = email.get("text_body") or email.get("html_body") or ""
        url = _first_job_url(body) or (
            f"gmail://message/{email['id']}" if email.get("id") else ""
        )
        return [
            OpportunityRecord(
                source="email_monitor:recruiter",
                source_url=url,
                employer=domain.split(".")[0].title(),
                title=email.get("subject", "") or "(no subject)",
                location="",
                posting_text=body[:5000],
                posted_at=email.get("date"),
                date_discovered=datetime.now(timezone.utc),
                raw_payload={
                    "from": email.get("from_raw"),
                    "subject": email.get("subject"),
                    "gmail_message_id": email.get("id"),
                },
                search_context={"email_parser": self.name},
            ),
        ]
