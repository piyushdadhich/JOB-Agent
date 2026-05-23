"""Unit tests for engine/resume/prompt_builder.py.

Pure-string tests — no LLM, no I/O. The builder is given canned
posting/eval/gap inputs and we assert structural properties of the
returned prompt string.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.resume.posting_gap_analyzer import (  # noqa: E402
    CoverageItem,
    GenuineGap,
    PostingGapReport,
    UndocumentedItem,
)
from engine.resume.prompt_builder import (  # noqa: E402
    ResumePromptBuilder,
)


# --- Fixtures -----------------------------------------------------

def _basic_posting():
    return {
        "id": 335,
        "title": "Senior Product Manager - C2B Bill Pay",
        "employer": "TD",
        "location": "Toronto, ON",
        "posting_text": (
            "Seeking a Senior Product Manager with experience in "
            "agile delivery, payments systems, and stakeholder "
            "management at the VP level."
        ),
    }


def _basic_eval():
    return {
        "tier": "TOP_TIER",
        "fit_score": 9,
        "reasoning": (
            "Strong delivery experience and payments background "
            "match this role's core requirements."
        ),
    }


def _basic_gap_report():
    return PostingGapReport(
        posting_id=335,
        posting_title="Senior Product Manager - C2B Bill Pay",
        employer="TD",
        covered=[
            CoverageItem(
                requirement="Agile delivery experience",
                inventory_evidence="Led 18-person agile program",
                inventory_role_id="consulting_finserv_2023",
                strength="direct_match",
            ),
        ],
        undocumented=[
            UndocumentedItem(
                requirement="VP stakeholder management",
                likely_source="consulting_finserv_2023",
                reasoning="Acme Corp role had monthly VP 1:1s",
                suggested_update=(
                    "Add 'vp_stakeholder_management' to "
                    "consulting_finserv_2023.skill_clusters"
                ),
                urgency="high",
            ),
        ],
        genuine_gaps=[
            GenuineGap(
                requirement="Biller direct integration experience",
                impact="critical",
                mitigation="Frame TestCo payments work as foundational",
            ),
        ],
        coverage_score=0.5,
        interview_readiness="competitive",
    )


# --- Tests --------------------------------------------------------

def test_prompt_includes_posting_title_and_company():
    """The posting's title + employer appear verbatim in the prompt
    so Claude can address the right role."""
    builder = ResumePromptBuilder()
    p = builder.build_prompt(
        posting=_basic_posting(),
        inventory_text="(stub inventory)",
    )
    assert "Senior Product Manager - C2B Bill Pay" in p
    assert "TD" in p


def test_prompt_includes_career_inventory():
    """The full career_inventory.md text is embedded so Claude
    can reference exact phrases / employers / metrics."""
    inventory = "## Roles\n### Acme Corp — led 18-person program ..."
    builder = ResumePromptBuilder()
    p = builder.build_prompt(
        posting=_basic_posting(),
        inventory_text=inventory,
    )
    assert "led 18-person program" in p
    assert "CANDIDATE CAREER INVENTORY" in p


def test_prompt_includes_ats_formatting_rules():
    """The non-negotiable ATS rules are in the prompt so Claude's
    output is renderable."""
    builder = ResumePromptBuilder()
    p = builder.build_prompt(
        posting=_basic_posting(),
        inventory_text="(stub)",
    )
    assert "ATS FORMATTING RULES" in p
    # Spot-check a few of the rule phrases
    assert "single-column layout" in p
    assert "Calibri" in p
    assert "PROFESSIONAL SUMMARY" in p
    assert "WORK EXPERIENCE" in p
    assert "EDUCATION & CERTIFICATIONS" in p
    assert "0.75 inch" in p


def test_prompt_includes_anti_fabrication_instruction():
    """The anti-fabrication clause is present (and at the end for
    salience)."""
    builder = ResumePromptBuilder()
    p = builder.build_prompt(
        posting=_basic_posting(),
        inventory_text="(stub)",
    )
    assert "ANTI-FABRICATION" in p
    assert "Reference ONLY" in p
    # Should appear AFTER the inventory section so it's the last
    # instruction Claude reads before generating.
    inv_idx = p.find("CANDIDATE CAREER INVENTORY")
    af_idx = p.find("ANTI-FABRICATION")
    assert inv_idx >= 0 and af_idx >= 0
    assert af_idx > inv_idx, "anti-fabrication should follow inventory"


def test_prompt_includes_keyword_alignment():
    """Instruction to mirror posting terminology + include both
    acronym and full term."""
    builder = ResumePromptBuilder()
    p = builder.build_prompt(
        posting=_basic_posting(),
        inventory_text="(stub)",
    )
    assert "KEYWORD ALIGNMENT" in p or "Mirror exact" in p
    assert "acronym" in p.lower()


def test_prompt_includes_role_selection_hints():
    """Soft hints about which inventory roles to lead with based
    on posting flavor."""
    builder = ResumePromptBuilder()
    p = builder.build_prompt(
        posting=_basic_posting(),
        inventory_text="(stub)",
    )
    assert "ROLE SELECTION HINTS" in p
    assert "AI Systems Developer" in p
    assert "TestCo" in p
    assert "Acme Corp" in p


def test_prompt_includes_eval_decision_context():
    """When eval_decision is provided, its tier/fit/reasoning
    appear in the EVALUATOR CONTEXT block."""
    builder = ResumePromptBuilder()
    p = builder.build_prompt(
        posting=_basic_posting(),
        eval_decision=_basic_eval(),
        inventory_text="(stub)",
    )
    assert "EVALUATOR CONTEXT" in p
    assert "TOP_TIER" in p
    assert "9" in p  # fit_score
    assert "delivery experience" in p.lower()


def test_prompt_includes_skills_grouping_instruction():
    """The output template tells Claude to group skills by
    category."""
    builder = ResumePromptBuilder()
    p = builder.build_prompt(
        posting=_basic_posting(),
        inventory_text="(stub)",
    )
    assert "SKILLS" in p
    assert "Delivery & Program Management" in p
    assert "Tools & Platforms" in p
    # Skills section must precede Work Experience per 2026 best
    # practice — assert ordering in the template.
    skills_idx = p.find("## SKILLS")
    work_idx = p.find("## WORK EXPERIENCE")
    assert skills_idx >= 0 and work_idx >= 0
    assert skills_idx < work_idx


def test_prompt_works_without_eval_decision():
    """No eval_decision -> the EVALUATOR CONTEXT block notes that
    fact instead of crashing."""
    builder = ResumePromptBuilder()
    p = builder.build_prompt(
        posting=_basic_posting(),
        eval_decision=None,
        inventory_text="(stub)",
    )
    assert "EVALUATOR CONTEXT" in p
    assert "no prior evaluation" in p


def test_prompt_works_without_gap_report():
    """No gap_report -> the GAP-AWARE GUIDANCE block notes that
    fact instead of crashing."""
    builder = ResumePromptBuilder()
    p = builder.build_prompt(
        posting=_basic_posting(),
        gap_report=None,
        inventory_text="(stub)",
    )
    assert "GAP-AWARE GUIDANCE" in p
    assert "no prior gap analysis" in p


def test_prompt_includes_gap_aware_emphasize_and_avoid_lists():
    """When gap_report is provided, EMPHASIZE / DO NOT CLAIM blocks
    surface the actual covered + genuine_gap items."""
    builder = ResumePromptBuilder()
    gap = _basic_gap_report()
    p = builder.build_prompt(
        posting=_basic_posting(),
        gap_report=gap,
        inventory_text="(stub)",
    )
    # EMPHASIZE block has the covered requirement
    assert "EMPHASIZE" in p
    assert "Agile delivery experience" in p
    # DO NOT CLAIM block has the genuine gap
    assert "DO NOT CLAIM" in p
    assert "Biller direct integration" in p
    # Mitigation hint is included for the cover letter
    assert "Frame TestCo payments work as foundational" in p


def test_prompt_specifies_output_markdown_template():
    """Claude is told to output a specific markdown structure
    (so the renderer can parse it)."""
    builder = ResumePromptBuilder()
    p = builder.build_prompt(
        posting=_basic_posting(),
        inventory_text="(stub)",
    )
    assert "OUTPUT TEMPLATE" in p
    assert "## PROFESSIONAL SUMMARY" in p
    assert "## SKILLS" in p
    assert "## WORK EXPERIENCE" in p
    assert "## EDUCATION & CERTIFICATIONS" in p
    # Section ordering (Skills before Work Experience — 2026 best practice)
    skills_idx = p.rfind("## SKILLS")
    work_idx = p.rfind("## WORK EXPERIENCE")
    edu_idx = p.rfind("## EDUCATION & CERTIFICATIONS")
    assert skills_idx < work_idx < edu_idx
