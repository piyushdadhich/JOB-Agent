"""Newsletter digest email parser.

Matches: sender email is in the configured newsletter_senders list
(exact match, lowercased). For each job-like URL found in the body,
yields a stub OpportunityRecord. Newsletter content varies wildly --
this parser deliberately stays conservative.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from engine.discovery.base import OpportunityRecord

_URL_RE = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
_JOB_HINTS = ("job", "career", "position", "role", "apply")


class NewsletterParser:
    name = "newsletter"

    def __init__(self, senders: list[str]):
        self.senders = [s.lower() for s in (senders or [])]

    def matches(self, email: dict) -> bool:
        sender = (email.get("from_email") or "").lower()
        return sender in self.senders

    def parse(self, email: dict) -> list[OpportunityRecord]:
        body = email.get("text_body") or email.get("html_body") or ""
        urls: list[str] = []
        seen: set[str] = set()
        for url in _URL_RE.findall(body):
            low = url.lower()
            if not any(h in low for h in _JOB_HINTS):
                continue
            base = url.split("?", 1)[0]
            if base in seen:
                continue
            seen.add(base)
            urls.append(url)

        records: list[OpportunityRecord] = []
        for url in urls:
            records.append(OpportunityRecord(
                source="email_monitor:newsletter",
                source_url=url,
                employer="",
                title=email.get("subject", "") or "(newsletter link)",
                location="",
                posting_text=url,
                posted_at=email.get("date"),
                date_discovered=datetime.now(timezone.utc),
                raw_payload={
                    "from": email.get("from_raw"),
                    "subject": email.get("subject"),
                    "gmail_message_id": email.get("id"),
                },
                search_context={"email_parser": self.name},
            ))
        return records
