"""Unit tests for engine.discovery.email_parsers.linkedin_parser."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.email_parsers.linkedin_parser import (  # noqa: E402
    LinkedInParser,
)


def _email(
    sender_email="jobs-noreply@linkedin.com",
    subject="3 new jobs match your profile",
    html="",
    mid="m1",
):
    return {
        "id": mid,
        "from_raw": sender_email,
        "from_email": sender_email,
        "from_domain": sender_email.split("@", 1)[1],
        "subject": subject,
        "text_body": "",
        "html_body": html,
        "date": datetime.now(timezone.utc),
    }


def test_matches_linkedin_with_job_subject():
    p = LinkedInParser()
    assert p.matches(_email()) is True


def test_does_not_match_non_linkedin():
    p = LinkedInParser()
    assert p.matches(_email(sender_email="hr@td.com")) is False


def test_does_not_match_linkedin_without_job_subject():
    p = LinkedInParser()
    assert p.matches(_email(subject="Your weekly news digest")) is False


def test_extracts_jobs_from_cards():
    html = """
    <html><body>
      <a href="https://www.linkedin.com/comm/jobs/view/123?refid=abc">
        Senior Project Manager at TD Bank
      </a>
      <a href="https://linkedin.com/comm/jobs/view/456?refid=def">
        Delivery Lead at BMO
      </a>
      <a href="https://linkedin.com/comm/jobs/view/123?refid=xyz">
        Senior Project Manager at TD Bank
      </a>
    </body></html>
    """
    p = LinkedInParser()
    recs = p.parse(_email(html=html))
    assert len(recs) == 2  # third is dedup'd by base URL
    assert "Senior Project Manager" in recs[0].title
    assert "Delivery Lead" in recs[1].title
    assert recs[0].source == "email_monitor:linkedin"
