"""Unit tests for engine.discovery.email_parsers.newsletter_parser."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.email_parsers.newsletter_parser import (  # noqa: E402
    NewsletterParser,
)


def _email(sender="newsletter@noreply.indeed.com", body="", mid="m1"):
    return {
        "id": mid,
        "from_raw": sender,
        "from_email": sender,
        "from_domain": sender.split("@", 1)[1],
        "subject": "Weekly digest",
        "text_body": body,
        "html_body": "",
        "date": datetime.now(timezone.utc),
    }


def test_matches_configured_sender():
    p = NewsletterParser(senders=["newsletter@noreply.indeed.com"])
    assert p.matches(_email()) is True


def test_does_not_match_unconfigured_sender():
    p = NewsletterParser(senders=["newsletter@indeed.com"])
    assert p.matches(_email(sender="other@indeed.com")) is False


def test_extracts_unique_job_links():
    body = (
        "Top jobs this week:\n"
        "  https://example.com/jobs/123\n"
        "  https://example.com/jobs/456\n"
        "  https://example.com/jobs/123?utm=foo\n"
        "  https://example.com/news/blah"  # not job-like
    )
    p = NewsletterParser(senders=["newsletter@noreply.indeed.com"])
    recs = p.parse(_email(body=body))
    # Two unique job URLs (third is dedup'd by base; news/ skipped)
    assert len(recs) == 2
    assert all(r.source == "email_monitor:newsletter" for r in recs)
    assert "/jobs/123" in recs[0].source_url
    assert "/jobs/456" in recs[1].source_url
