"""Unit tests for engine.applicant.qa_matcher."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.applicant.profile import ApplicantProfile  # noqa: E402
from engine.applicant.qa_matcher import (  # noqa: E402
    QAMatch,
    QAMatcher,
)


PATTERNS = [
    {"match": ["legally authorized"], "answer": "Yes"},
    {
        "match": ["years of experience"],
        "strategy": "count_from_inventory",
        "default": "10+",
    },
    {"match": ["resume"], "strategy": "upload_resume"},
    {
        "match": ["expected salary"],
        "answer_from_profile": "salary_expectation",
    },
]


def test_first_match_wins():
    m = QAMatcher(PATTERNS)
    r = m.match("Are you legally authorized to work in Canada?")
    assert r is not None and r.answer == "Yes"


def test_count_from_inventory_uses_default():
    m = QAMatcher(PATTERNS)
    r = m.match("How many years of experience do you have?")
    assert r is not None and r.answer == "10+"


def test_upload_strategy_returns_strategy_string():
    m = QAMatcher(PATTERNS)
    r = m.match("Please upload your resume.")
    assert r is not None and r.strategy == "upload_resume"
    assert r.answer is None


def test_answer_from_profile_lookup():
    profile = ApplicantProfile(
        salary_expectation="Open to discussion",
    )
    m = QAMatcher(PATTERNS, profile=profile)
    r = m.match("What is your expected salary?")
    assert r is not None and r.answer == "Open to discussion"


def test_no_match_returns_none():
    m = QAMatcher(PATTERNS)
    assert m.match(
        "Tell me about a time you led a difficult project",
    ) is None


def test_from_yaml_loads_default_path():
    """Default config/applicant_qa_patterns.yaml should load."""
    m = QAMatcher.from_yaml()
    assert len(m.patterns) > 0
    assert m.match("Are you legally authorized to work?") is not None
