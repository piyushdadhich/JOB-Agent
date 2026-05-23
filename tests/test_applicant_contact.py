"""Tests for the CANDIDATE CONTACT block in resume/CL prompts."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.resume.applicant_contact import (  # noqa: E402
    build_contact_header,
    format_candidate_contact,
)
from engine.resume.cover_letter_prompt_builder import (  # noqa: E402
    CoverLetterPromptBuilder,
)
from engine.resume.prompt_builder import ResumePromptBuilder  # noqa: E402


_APPLICANT = {
    "first_name": "Alex",
    "last_name": "Sample",
    "email": "alex.sample@example.com",
    "phone": "+1 (415) 200-0123",
    "city": "Springfield",
    "province": "Sample State",
    "linkedin_url": "https://www.linkedin.com/in/alex-sample/",
    "education": [
        {"degree": "MBA", "school": "Sample University"},
    ],
    "certifications": [
        {"name": "PMP", "issuer": "PMI", "active": True},
    ],
}

_POSTING = {
    "title": "Project Manager",
    "employer": "Acme Corp",
    "location": "Springfield, ST",
    "posting_text": "Lead delivery of capital projects.",
}


def test_contact_block_has_all_fields():
    block = format_candidate_contact(_APPLICANT)
    assert "Alex Sample" in block
    assert "alex.sample@example.com" in block
    assert "+1 (415) 200-0123" in block
    assert "Springfield, Sample State" in block
    assert "https://www.linkedin.com/in/alex-sample/" in block
    assert "MBA, Sample University" in block
    assert "PMP" in block and "PMI" in block
    # The verbatim-copy instruction must be present.
    assert "VERBATIM" in block
    # The pre-built header line joins contacts with bullets, so the
    # LLM copies rather than re-assembles from a field list.
    assert "alex.sample@example.com • +1 (415) 200-0123" in block


def test_contact_block_omits_unset_github():
    block = format_candidate_contact(_APPLICANT)
    assert "GitHub" not in block
    with_gh = format_candidate_contact(
        {**_APPLICANT, "github_url": "https://github.com/alex-sample"},
    )
    assert "https://github.com/alex-sample" in with_gh


def test_contact_block_handles_missing_profile():
    block = format_candidate_contact(None)
    assert "applicant profile not provided" in block


def test_build_contact_header_two_lines():
    header = build_contact_header(_APPLICANT)
    line1, line2 = header.split("\n")
    assert line1 == "Alex Sample | Springfield, Sample State"
    # Every contact link, bullet-joined, in profile order.
    assert line2 == (
        "alex.sample@example.com • +1 (415) 200-0123 • "
        "https://www.linkedin.com/in/alex-sample/"
    )


def test_build_contact_header_includes_github_when_set():
    header = build_contact_header(
        {**_APPLICANT, "github_url": "https://github.com/alex-sample"},
    )
    assert header.endswith("https://github.com/alex-sample")
    # No dangling separators when a field is absent.
    assert " •  • " not in header


def test_build_contact_header_empty_profile():
    assert build_contact_header(None) == ""


def test_resume_prompt_includes_contact_block():
    prompt = ResumePromptBuilder().build_prompt(
        posting=_POSTING, applicant_profile=_APPLICANT,
    )
    assert "CANDIDATE CONTACT" in prompt
    assert "+1 (415) 200-0123" in prompt
    assert "alex.sample@example.com" in prompt
    # B.Tech must not leak in — the candidate does not hold that degree.
    assert "B.Tech" not in prompt


def test_cover_letter_prompt_includes_contact_block():
    prompt = CoverLetterPromptBuilder().build_prompt(
        posting=_POSTING, applicant_profile=_APPLICANT,
    )
    assert "CANDIDATE CONTACT" in prompt
    assert "Alex Sample" in prompt
    assert "https://www.linkedin.com/in/alex-sample/" in prompt


def test_prompt_builders_still_work_without_applicant():
    # Backward compatible: applicant_profile defaults to None.
    r = ResumePromptBuilder().build_prompt(posting=_POSTING)
    cl = CoverLetterPromptBuilder().build_prompt(posting=_POSTING)
    assert "applicant profile not provided" in r
    assert "applicant profile not provided" in cl
