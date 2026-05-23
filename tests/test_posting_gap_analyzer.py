"""Unit tests for engine/resume/posting_gap_analyzer.py.

LLMClient is mocked — no Ollama required.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.resume.posting_gap_analyzer import (  # noqa: E402
    CoverageItem,
    GenuineGap,
    PostingGapAnalyzer,
    PostingGapReport,
    UndocumentedItem,
    _slim_inventory,
    _to_report,
)


# --- Fixtures -----------------------------------------------------

class _FakeLLM:
    """Stand-in for LLMClient — returns whatever JSON the test
    sets up. Records the kwargs passed to generate_chat for
    assertion."""

    def __init__(self, response_text: str):
        self.response_text = response_text
        self.calls: list[dict] = []

    def generate_chat(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {"response": self.response_text}


def _basic_posting():
    return {
        "id": 335,
        "title": "Senior Product Manager - C2B Bill Pay",
        "employer": "TD",
        "location": "Toronto, ON",
        "posting_text": "Need agile delivery experience and 10+ years.",
    }


def _basic_inventory():
    return {
        "roles": [
            {
                "id": "consulting_finserv_2023",
                "employer": "Acme Corp",
                "title": "Senior Consultant",
                "function": "Single point of delivery accountability...",
                "skill_clusters": [
                    "program_delivery_leadership",
                    "offshore_team_leadership",
                ],
                "evidence_phrases": [
                    "Led 18-person cross-continent program",
                ],
                "outcomes": ["should be dropped by slim"],
            },
            {
                "id": "testco_payments_2025",
                "employer": "TestCo",
                "title": "Payments Officer",
                "function": "Processed payment files...",
                "skill_clusters": ["payments_operations"],
                "evidence_phrases": [],
            },
        ],
        "transferable_skill_clusters": [
            {
                "name": "agile_delivery_leadership",
                "summary": "Planning, facilitating, driving Agile delivery.",
                "evidence_role_ids": ["consulting_finserv_2023"],
            },
        ],
        "trajectory": {"current_level": "Senior Delivery Lead"},
        "hard_exclusions": ["MegaBank"],
    }


def _llm_response(covered=None, undocumented=None, genuine_gaps=None):
    return json.dumps({
        "covered": covered or [],
        "undocumented": undocumented or [],
        "genuine_gaps": genuine_gaps or [],
    })


# --- Inventory slimming -------------------------------------------

def test_slim_inventory_keeps_essential_role_fields():
    """The slim inventory drops outcomes/cultural_signals/red_flags
    but keeps id/employer/title/function/skill_clusters/
    evidence_phrases for matching."""
    slim = _slim_inventory(_basic_inventory())
    assert len(slim["roles"]) == 2
    keys = set(slim["roles"][0].keys())
    assert keys == {
        "id", "employer", "title", "function",
        "skill_clusters", "evidence_phrases",
    }
    assert "outcomes" not in slim["roles"][0]
    # Transferable clusters are preserved
    assert len(slim["transferable_skill_clusters"]) == 1
    assert slim["transferable_skill_clusters"][0]["name"] == (
        "agile_delivery_leadership"
    )


# --- Covered items ------------------------------------------------

def test_covered_requirement_found_in_skill_clusters():
    """A covered item whose evidence is a skill_cluster name."""
    fake_llm = _FakeLLM(_llm_response(covered=[{
        "requirement": "Agile delivery experience",
        "inventory_evidence": "program_delivery_leadership",
        "inventory_role_id": "consulting_finserv_2023",
        "strength": "direct_match",
    }]))
    analyzer = PostingGapAnalyzer(llm_client=fake_llm)
    report = analyzer.analyze(_basic_posting(), _basic_inventory())
    assert len(report.covered) == 1
    cov = report.covered[0]
    assert isinstance(cov, CoverageItem)
    assert cov.inventory_role_id == "consulting_finserv_2023"
    assert cov.strength == "direct_match"


def test_covered_requirement_found_in_evidence_phrases():
    """Coverage with an evidence_phrase as the support."""
    fake_llm = _FakeLLM(_llm_response(covered=[{
        "requirement": "Cross-continent team leadership",
        "inventory_evidence": "Led 18-person cross-continent program",
        "inventory_role_id": "consulting_finserv_2023",
        "strength": "direct_match",
    }]))
    analyzer = PostingGapAnalyzer(llm_client=fake_llm)
    report = analyzer.analyze(_basic_posting(), _basic_inventory())
    assert len(report.covered) == 1
    assert "18-person" in report.covered[0].inventory_evidence


# --- Undocumented items -------------------------------------------

def test_undocumented_inferred_from_adjacent_role():
    """An undocumented item points at a likely_source role and
    suggests an inventory update."""
    fake_llm = _FakeLLM(_llm_response(undocumented=[{
        "requirement": "VP-level stakeholder management",
        "likely_source": "consulting_finserv_2023",
        "reasoning": "Acme Corp role had monthly 1:1s with Discover VP",
        "suggested_update": (
            "Add 'vp_stakeholder_management' to "
            "consulting_finserv_2023.skill_clusters"
        ),
        "urgency": "high",
    }]))
    analyzer = PostingGapAnalyzer(llm_client=fake_llm)
    report = analyzer.analyze(_basic_posting(), _basic_inventory())
    assert len(report.undocumented) == 1
    item = report.undocumented[0]
    assert isinstance(item, UndocumentedItem)
    assert item.likely_source == "consulting_finserv_2023"
    assert item.urgency == "high"


def test_suggested_update_includes_role_id():
    """The suggested_update text should reference the role id so
    the user knows where to apply the change."""
    fake_llm = _FakeLLM(_llm_response(undocumented=[{
        "requirement": "Salesforce admin experience",
        "likely_source": "healthtech_po_2021",
        "reasoning": "Healthcare CRM in HealthCo likely involved Salesforce",
        "suggested_update": (
            "Add 'salesforce_admin' to healthtech_po_2021"
        ),
        "urgency": "low",
    }]))
    analyzer = PostingGapAnalyzer(llm_client=fake_llm)
    report = analyzer.analyze(_basic_posting(), _basic_inventory())
    assert "healthtech_po_2021" in (
        report.undocumented[0].suggested_update
    )


# --- Genuine gaps -------------------------------------------------

def test_genuine_gap_not_found_anywhere():
    """A genuine gap surfaces with impact + mitigation."""
    fake_llm = _FakeLLM(_llm_response(genuine_gaps=[{
        "requirement": "Azure DevOps administration",
        "impact": "preferred",
        "mitigation": (
            "Frame as transferable: JIRA + CI/CD pipeline experience"
        ),
    }]))
    analyzer = PostingGapAnalyzer(llm_client=fake_llm)
    report = analyzer.analyze(_basic_posting(), _basic_inventory())
    assert len(report.genuine_gaps) == 1
    gap = report.genuine_gaps[0]
    assert isinstance(gap, GenuineGap)
    assert gap.impact == "preferred"


def test_mitigation_suggested_for_preferred_requirement():
    """Mitigation field is non-empty for genuine gaps."""
    fake_llm = _FakeLLM(_llm_response(genuine_gaps=[{
        "requirement": "ServiceNow workflow design",
        "impact": "preferred",
        "mitigation": (
            "Bridge statement: experience with similar workflow "
            "tools at MedCo (Jira) translates to ServiceNow"
        ),
    }]))
    analyzer = PostingGapAnalyzer(llm_client=fake_llm)
    report = analyzer.analyze(_basic_posting(), _basic_inventory())
    assert report.genuine_gaps[0].mitigation
    assert "Bridge" in report.genuine_gaps[0].mitigation


# --- Coverage score & readiness -----------------------------------

def test_coverage_score_calculated_correctly():
    """coverage_score = covered / (covered + undocumented + gaps)."""
    fake_llm = _FakeLLM(_llm_response(
        covered=[{"requirement": f"R{i}",
                  "inventory_evidence": "x",
                  "inventory_role_id": "consulting_finserv_2023",
                  "strength": "direct_match"}
                 for i in range(7)],
        undocumented=[{"requirement": "U", "likely_source": "x",
                       "reasoning": "x", "suggested_update": "x",
                       "urgency": "low"}],
        genuine_gaps=[{"requirement": "G", "impact": "preferred",
                       "mitigation": "x"},
                      {"requirement": "G2", "impact": "preferred",
                       "mitigation": "x"}],
    ))
    analyzer = PostingGapAnalyzer(llm_client=fake_llm)
    report = analyzer.analyze(_basic_posting(), _basic_inventory())
    # 7 / (7 + 1 + 2) = 0.7
    assert abs(report.coverage_score - 0.7) < 0.001


def test_interview_readiness_strong_above_70():
    """coverage_score >= 0.70 -> 'strong'."""
    fake_llm = _FakeLLM(_llm_response(
        covered=[{"requirement": f"R{i}",
                  "inventory_evidence": "x",
                  "inventory_role_id": "consulting_finserv_2023",
                  "strength": "direct_match"}
                 for i in range(7)],
        undocumented=[],
        genuine_gaps=[{"requirement": f"G{i}",
                       "impact": "preferred", "mitigation": "x"}
                      for i in range(3)],
    ))
    analyzer = PostingGapAnalyzer(llm_client=fake_llm)
    report = analyzer.analyze(_basic_posting(), _basic_inventory())
    # 7 / 10 = 0.70 -> strong
    assert report.interview_readiness == "strong"


def test_interview_readiness_competitive_above_50():
    """0.50 <= coverage_score < 0.70 -> 'competitive'."""
    fake_llm = _FakeLLM(_llm_response(
        covered=[{"requirement": f"R{i}",
                  "inventory_evidence": "x",
                  "inventory_role_id": "consulting_finserv_2023",
                  "strength": "direct_match"}
                 for i in range(5)],
        genuine_gaps=[{"requirement": f"G{i}",
                       "impact": "preferred", "mitigation": "x"}
                      for i in range(5)],
    ))
    analyzer = PostingGapAnalyzer(llm_client=fake_llm)
    report = analyzer.analyze(_basic_posting(), _basic_inventory())
    # 5 / 10 = 0.50 -> competitive
    assert report.interview_readiness == "competitive"


def test_interview_readiness_stretch_below_50():
    """coverage_score < 0.50 -> 'stretch'."""
    fake_llm = _FakeLLM(_llm_response(
        covered=[{"requirement": "R1",
                  "inventory_evidence": "x",
                  "inventory_role_id": "consulting_finserv_2023",
                  "strength": "direct_match"}],
        genuine_gaps=[{"requirement": f"G{i}",
                       "impact": "preferred", "mitigation": "x"}
                      for i in range(5)],
    ))
    analyzer = PostingGapAnalyzer(llm_client=fake_llm)
    report = analyzer.analyze(_basic_posting(), _basic_inventory())
    # 1 / 6 ~= 0.17 -> stretch
    assert report.interview_readiness == "stretch"


# --- LLM call wiring ----------------------------------------------

def test_llm_called_with_pinned_model():
    """The analyzer pins gemma4:e4b as the model and uses
    deterministic temperature=0.0."""
    fake_llm = _FakeLLM(_llm_response())
    analyzer = PostingGapAnalyzer(llm_client=fake_llm)
    analyzer.analyze(_basic_posting(), _basic_inventory())
    assert len(fake_llm.calls) == 1
    call = fake_llm.calls[0]
    assert call["model"] == "gemma4:e4b"
    assert call["temperature"] == 0.0
    assert call["think"] is False


def test_parse_response_handles_markdown_fence():
    """Gemma sometimes wraps JSON in a ```json ...``` fence; the
    parser should still extract it."""
    wrapped = (
        "Here is the JSON:\n```json\n"
        + _llm_response(covered=[{
            "requirement": "X", "inventory_evidence": "y",
            "inventory_role_id": "consulting_finserv_2023",
            "strength": "direct_match",
        }])
        + "\n```\nDone."
    )
    fake_llm = _FakeLLM(wrapped)
    analyzer = PostingGapAnalyzer(llm_client=fake_llm)
    report = analyzer.analyze(_basic_posting(), _basic_inventory())
    assert len(report.covered) == 1


def test_parse_response_fail_loud_on_garbage():
    """An unparseable response raises ValueError — fail-loud rather
    than silently fail-open like the evaluator filter does."""
    fake_llm = _FakeLLM("not json at all, just prose")
    analyzer = PostingGapAnalyzer(llm_client=fake_llm)
    with pytest.raises(ValueError, match="unable to parse"):
        analyzer.analyze(_basic_posting(), _basic_inventory())
