"""Tests for engine.applicant.handlers.base -- URL detection mainly."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.applicant.handlers.base import (  # noqa: E402
    detect_ats_from_url,
)


@pytest.mark.parametrize("url,expected", [
    ("https://boards.greenhouse.io/td/jobs/12345", "greenhouse"),
    ("https://job-boards.greenhouse.io/foo/jobs/9", "greenhouse"),
    ("https://jobs.lever.co/wealthsimple/abc", "lever"),
    ("https://jobs.ashbyhq.com/cohere/x", "ashby"),
    ("https://td.wd3.myworkdayjobs.com/en-US/TD_Bank_Careers",
     "workday"),
    ("https://accenture.wd103.myworkdayjobs.com/x", "workday"),
    ("https://www.indeed.com/applystart?jk=abc", "indeed"),
    ("https://ca.indeed.com/viewjob?jk=xyz", "indeed"),
    ("https://www.linkedin.com/jobs/view/123456", "linkedin"),
    ("https://example.com/careers/123", None),
    ("", None),
])
def test_detect_ats_from_url(url, expected):
    assert detect_ats_from_url(url) == expected
