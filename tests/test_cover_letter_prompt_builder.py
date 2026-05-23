"""Unit tests for engine/resume/cover_letter_prompt_builder.py and
scripts/generate_cover_letter.py.

Pure-string tests for the builder; CLI test uses tmp_path with a
mocked PostingGapAnalyzer.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker  # noqa: E402
from engine.resume.cover_letter_prompt_builder import (  # noqa: E402
    CoverLetterPromptBuilder,
)
from engine.resume.posting_gap_analyzer import (  # noqa: E402
    CoverageItem,
    GenuineGap,
    PostingGapReport,
    UndocumentedItem,
)


# --- Fixtures -----------------------------------------------------

def _basic_posting():
    return {
        "id": 335,
        "title": "Senior Product Manager - C2B Bill Pay",
        "employer": "TD",
        "location": "Toronto, ON",
        "posting_text": (
            "Seeking a Senior Product Manager to develop bill "
            "payment solutions for U.S. and Canadian commercial "
            "and corporate customers."
        ),
    }


def _basic_eval():
    return {
        "tier": "TOP_TIER",
        "fit_score": 9,
        "reasoning": "Delivery + payments background match.",
    }


def _basic_gap_report():
    return PostingGapReport(
        posting_id=335,
        posting_title="Senior Product Manager - C2B Bill Pay",
        employer="TD",
        covered=[
            CoverageItem(
                requirement="Agile delivery",
                inventory_evidence="Led 18-person agile program",
                inventory_role_id="consulting_finserv_2023",
                strength="direct_match",
            ),
        ],
        undocumented=[
            UndocumentedItem(
                requirement="VP stakeholder management",
                likely_source="consulting_finserv_2023",
                reasoning="VP 1:1s at Acme Corp",
                suggested_update="Add to skill_clusters",
                urgency="high",
            ),
        ],
        genuine_gaps=[
            GenuineGap(
                requirement="Biller direct integration",
                impact="critical",
                mitigation=(
                    "Frame TestCo payments work as foundational"
                ),
            ),
            GenuineGap(
                requirement="Salesforce admin",
                impact="preferred",
                mitigation="Note CRM-adjacent experience",
            ),
        ],
        coverage_score=0.5,
        interview_readiness="competitive",
    )


# --- Builder tests ------------------------------------------------

def test_prompt_includes_posting_details():
    """Title + posting text appear in the prompt."""
    builder = CoverLetterPromptBuilder()
    p = builder.build_prompt(
        posting=_basic_posting(),
        inventory_text="(stub)",
    )
    assert "Senior Product Manager - C2B Bill Pay" in p
    assert "bill payment solutions" in p


def test_prompt_includes_company_name():
    """The employer is named so Claude can address the right
    company in paragraph 1."""
    builder = CoverLetterPromptBuilder()
    p = builder.build_prompt(
        posting=_basic_posting(),
        inventory_text="(stub)",
    )
    assert "TD" in p


def test_prompt_includes_bridge_instruction():
    """Paragraph 2 'Bridge / money paragraph' instructions are
    explicit — connect 2-3 inventory accomplishments to the
    posting's requirements."""
    builder = CoverLetterPromptBuilder()
    p = builder.build_prompt(
        posting=_basic_posting(),
        inventory_text="(stub)",
    )
    assert "Bridge" in p
    assert "money paragraph" in p
    assert "2-3" in p or "2–3" in p


def test_prompt_includes_anti_fabrication():
    """Anti-fabrication clause is present, and at the end of the
    prompt for salience (after the inventory)."""
    builder = CoverLetterPromptBuilder()
    p = builder.build_prompt(
        posting=_basic_posting(),
        inventory_text="(stub)",
    )
    assert "ANTI-FABRICATION" in p
    inv_idx = p.find("CANDIDATE CAREER INVENTORY")
    af_idx = p.find("ANTI-FABRICATION")
    assert inv_idx >= 0 and af_idx >= 0
    assert af_idx > inv_idx


def test_prompt_includes_inventory_context():
    """The full career inventory text is embedded so claims trace
    back to a single source."""
    inv = "## Roles\n### Acme Corp — 18-person team ..."
    builder = CoverLetterPromptBuilder()
    p = builder.build_prompt(
        posting=_basic_posting(),
        inventory_text=inv,
    )
    assert "18-person team" in p


def test_prompt_works_without_eval_decision():
    """Missing eval_decision -> graceful '(no prior evaluation)'
    note instead of crashing."""
    builder = CoverLetterPromptBuilder()
    p = builder.build_prompt(
        posting=_basic_posting(),
        eval_decision=None,
        inventory_text="(stub)",
    )
    assert "EVALUATOR CONTEXT" in p
    assert "no prior evaluation" in p


def test_prompt_addresses_hiring_manager():
    """Default salutation is 'Dear Hiring Manager,' unless a
    specific recipient name is in the posting."""
    builder = CoverLetterPromptBuilder()
    p = builder.build_prompt(
        posting=_basic_posting(),
        inventory_text="(stub)",
    )
    assert "Dear Hiring Manager" in p


def test_prompt_addresses_single_critical_gap_in_paragraph_3():
    """When gap_report is provided, paragraph 3 instructions
    target the SINGLE most critical gap (not all gaps)."""
    builder = CoverLetterPromptBuilder()
    p = builder.build_prompt(
        posting=_basic_posting(),
        gap_report=_basic_gap_report(),
        inventory_text="(stub)",
    )
    # Critical gap surfaces with mitigation
    assert "Biller direct integration" in p
    assert "Frame TestCo payments work as foundational" in p
    # Instruction to address ONLY one gap
    assert "Do NOT enumerate every gap" in p


def test_prompt_specifies_paragraph_count():
    """Prompt instructs 3-4 paragraphs (not a free-form letter)."""
    builder = CoverLetterPromptBuilder()
    p = builder.build_prompt(
        posting=_basic_posting(),
        inventory_text="(stub)",
    )
    assert "3-4 paragraphs" in p or "3–4 paragraphs" in p


# --- CLI integration test -----------------------------------------

def _seed_posting_for_cli(t: Tracker) -> int:
    cid = t.upsert_company(name="TestCorp")
    oid, _ = t.insert_opportunity(
        company_id=cid, source="test",
        source_url="https://example.test/x",
        title="Test PM",
        posting_text="Need agile delivery and stakeholder mgmt.",
    )
    t.record_evaluation(
        opportunity_id=oid,
        evaluator_version="pipeline-v2.3.0",
        tier="STRONG", fit_score=8, sector=None,
        role_type=None, stage_trace={}, reasoning=None,
    )
    return oid


def test_output_file_created(tmp_path, monkeypatch):
    """CLI writes the prompt to data/<profile>/resumes/prompts/
    cover_letter_prompt_{id}_{date}.md when invoked end-to-end."""
    # Real Tracker on tmp_path so the CLI's get_opportunity_by_id
    # finds something. Inject via tracker_override.
    t = Tracker(profile_id="test", db_path=tmp_path / "t.db")
    try:
        oid = _seed_posting_for_cli(t)
        # Set up a stub inventory_extract.json so --skip-gap-analysis
        # still loads it.
        inv_dir = tmp_path / "data" / "test"
        inv_dir.mkdir(parents=True)
        (inv_dir / "inventory_extract.json").write_text(
            json.dumps({"roles": [], "transferable_skill_clusters": []}),
            encoding="utf-8",
        )
        # Stub career_inventory.md
        sm_dir = tmp_path / "source_materials" / "test"
        sm_dir.mkdir(parents=True)
        (sm_dir / "career_inventory.md").write_text(
            "# stub inventory", encoding="utf-8",
        )

        # Patch PROJECT_ROOT so CLI reads from tmp_path
        from scripts import generate_cover_letter as gcl
        monkeypatch.setattr(gcl, "PROJECT_ROOT", tmp_path)

        rc = gcl.main(
            [
                "--profile", "test",
                "--posting", str(oid),
                "--skip-gap-analysis",
                "--no-prompt",
            ],
            tracker_override=t,
        )
        assert rc == 0

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        expected = (
            tmp_path / "data" / "test" / "resumes" / "prompts" /
            f"cover_letter_prompt_{oid}_{today}.md"
        )
        assert expected.exists()
        body = expected.read_text(encoding="utf-8")
        assert "Test PM" in body
        assert "TestCorp" in body
    finally:
        t.close()
