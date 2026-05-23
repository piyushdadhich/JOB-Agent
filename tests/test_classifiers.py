"""Unit tests for engine/persistence/classifiers (v2.11 schema)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.classifiers import (  # noqa: E402
    classify_ai_subtype,
    classify_city,
    classify_function,
    classify_industry,
    derive_eval_priority,
)


@pytest.mark.parametrize("location, expected", [
    ("Toronto, ON", "Toronto"),
    ("Mississauga", "Toronto"),
    ("Brampton, Ontario, Canada", "Toronto"),
    ("Markham", "Toronto"),
    ("GTA", "Toronto"),
    ("Greater Toronto Area", "Toronto"),
    ("Calgary, AB", "Calgary"),
    ("Edmonton, Alberta", "Edmonton"),
    ("Remote", "Remote-Canada"),
    ("remote (Canada)", "Remote-Canada"),
    ("Anywhere in Canada", "Remote-Canada"),
    ("Vancouver, BC", None),
    ("Halifax", None),
    ("", None),
    (None, None),
])
def test_classify_city(location, expected):
    assert classify_city(location) == expected


@pytest.mark.parametrize("title, expected", [
    ("AI Engineer", "ai_engineer"),
    ("Senior ML Engineer", "ai_engineer"),
    ("Machine Learning Engineer", "ai_engineer"),
    ("AI Product Manager", "ai_pm"),
    ("AI PM, Platform", "ai_pm"),
    ("AI Governance Lead", "ai_governance"),
    ("AI Risk Officer", "ai_governance"),
    ("Machine Learning Specialist", "ml_general"),
    ("Generative AI Lead", "ai_general"),
    ("AI Manager", "ai_general"),
    ("Senior Project Manager", None),
    ("Maintained legacy systems", None),
    ("", None),
    (None, None),
])
def test_classify_ai_subtype(title, expected):
    assert classify_ai_subtype(title) == expected


def test_classify_ai_subtype_search_context_optional():
    assert classify_ai_subtype("AI Engineer", None) == "ai_engineer"
    assert (
        classify_ai_subtype(
            "AI Engineer", {"keywords": "AI engineer"}
        ) == "ai_engineer"
    )


@pytest.mark.parametrize("ai_subtype, expected", [
    (None, 2),
    ("ai_engineer", 1),
    ("ai_pm", 1),
    ("ai_governance", 1),
    ("ml_general", 1),
    ("ai_general", 1),
])
def test_derive_eval_priority(ai_subtype, expected):
    assert derive_eval_priority(ai_subtype) == expected


@pytest.mark.parametrize("title, expected", [
    ("Senior Project Manager", "Project Management & Delivery"),
    ("Delivery Lead, Platform", "Project Management & Delivery"),
    ("Scrum Master", "Project Management & Delivery"),
    ("Business Analyst", "Business Analysis"),
    ("Business Systems Analyst", "Business Analysis"),
    ("Product Owner", "Product Management"),
    ("Senior Product Manager", "Product Management"),
    ("Data Analyst", "Data & Analytics"),
    ("Data Scientist", "Data & Analytics"),
    ("BI Developer", "Data & Analytics"),
    ("Software Engineer", "Software Engineering"),
    ("Senior Backend Engineer", "Software Engineering"),
    ("Site Reliability Engineer", "Software Engineering"),
    ("Finance Manager", "Finance & Accounting"),
    ("Controller", "Finance & Accounting"),
    ("Talent Acquisition Partner", "Human Resources"),
    ("HR Business Partner", "Human Resources"),
    ("Marketing Manager", "Marketing"),
    ("Brand Manager", "Marketing"),
    ("Account Executive", "Sales"),
    ("BDR, Mid-Market", "Sales"),
    ("Operations Manager", "Operations"),
    ("Legal Counsel", "Legal & Compliance"),
    ("Compliance Officer", "Legal & Compliance"),
    ("Systems Administrator", "IT & Infrastructure"),
    ("UX Designer", "Design"),
    ("Customer Success Manager", "Customer Success"),
    ("Random Other Title", None),
    ("", None),
    (None, None),
])
def test_classify_function_title(title, expected):
    assert classify_function(title) == expected


def test_classify_function_falls_back_to_posting_text():
    assert classify_function("Senior Position", "We are hiring a project manager for our team") == "Project Management & Delivery"


def test_classify_function_title_takes_precedence_over_posting():
    # Title matches Software Engineering; posting talks about projects.
    # Title scanned first via concatenation but ordering still
    # produces the title's match because Project Management is checked
    # before Software Engineering -- so this also confirms the
    # specific-first ordering.
    assert classify_function("Site Reliability Engineer", "Some text about products") == "Software Engineering"


@pytest.mark.parametrize("employer, industry, expected", [
    ("TD Bank", None, "Financial Services"),
    ("Scotiabank", None, "Financial Services"),
    ("Sun Life Insurance", None, "Financial Services"),
    ("Enbridge", "Oil and Gas", "Energy & Utilities"),
    ("Hydro One", None, "Energy & Utilities"),
    ("City of Toronto", None, "Public Sector"),
    ("Ontario Ministry of Health", None, "Public Sector"),
    ("Brookfield Real Estate", None, "Real Estate & Construction"),
    ("PCL Construction", None, "Real Estate & Construction"),
    ("Sunnybrook Hospital", None, "Healthcare"),
    ("Pfizer Pharmaceuticals", None, "Healthcare"),
    ("University of Toronto", None, "Education"),
    ("Bell Canada", "Telecom", "Telecommunications"),
    ("Rogers Communications", "Telecommunications", "Telecommunications"),
    ("CBC", "Media", "Media & Entertainment"),
    ("Air Canada", "Airline", "Transportation & Logistics"),
    ("Teck Resources", "Mining", "Mining & Resources"),
    ("Loblaws", "Retail", "Retail & Consumer"),
    ("Magna International", "Manufacturing", "Manufacturing"),
    ("Deloitte", "Consulting", "Professional Services"),
    ("Some SaaS Company", "Software", "Technology"),
    ("Random Co", None, None),
    ("", None, None),
    (None, None, None),
])
def test_classify_industry(employer, industry, expected):
    assert classify_industry(employer, industry) == expected


def test_classify_industry_prefers_employer_industry():
    # Even an ambiguous employer name should classify when industry says so.
    assert classify_industry("ABC Corp", "Insurance") == "Financial Services"
