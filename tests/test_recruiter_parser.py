"""Unit tests for engine.discovery.email_parsers.recruiter_parser."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.email_parsers.recruiter_parser import (  # noqa: E402
    RecruiterParser,
)


def _email(domain="hays.com", subject="Senior PM role", body="x", mid="m1"):
    return {
        "id": mid,
        "from_raw": f"Recruiter <jane@{domain}>",
        "from_email": f"jane@{domain}",
        "from_domain": domain,
        "subject": subject,
        "text_body": body,
        "html_body": "",
        "date": datetime.now(timezone.utc),
    }


def test_matches_known_domain():
    p = RecruiterParser(domains=["hays.com", "robertwalters.com"])
    assert p.matches(_email(domain="hays.com")) is True
    assert p.matches(_email(domain="robertwalters.com")) is True


def test_matches_subdomain_of_known_domain():
    p = RecruiterParser(domains=["hays.com"])
    assert p.matches(_email(domain="apac.hays.com")) is True


def test_does_not_match_unknown_domain():
    p = RecruiterParser(domains=["hays.com"])
    assert p.matches(_email(domain="randomperson.io")) is False


def test_extracts_record_with_first_job_url():
    p = RecruiterParser(domains=["hays.com"])
    body = (
        "Hi Alex,\n\nI have a Senior PM opportunity at TD Bank. "
        "Apply here: https://hays.com/jobs/view/12345 or "
        "see https://hays.com/about for more about us."
    )
    recs = p.parse(_email(body=body))
    assert len(recs) == 1
    r = recs[0]
    assert r.source == "email_monitor:recruiter"
    assert r.source_url == "https://hays.com/jobs/view/12345"
    assert r.employer == "Hays"
    assert r.title == "Senior PM role"


def test_falls_back_to_gmail_url_when_no_job_link():
    p = RecruiterParser(domains=["hays.com"])
    recs = p.parse(_email(body="Hi just checking in", mid="abc123"))
    assert recs[0].source_url == "gmail://message/abc123"
