"""Unit tests for engine/resume/docx_renderer.py.

Renders a sample markdown into a .docx in tmp_path, then re-opens
the .docx and asserts properties (font, size, margins, structure)
directly. python-docx is required (already in venv).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from docx import Document
from docx.shared import Inches, Pt

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.resume.docx_renderer import (  # noqa: E402
    BODY_FONT_SIZE_PT,
    FONT_NAME,
    HEADING_FONT_SIZE_PT,
    MARGIN_INCHES,
    NAME_FONT_SIZE_PT,
    ATSResumeRenderer,
    _company_token,
    _resume_filename,
    _split_first_last,
)


SAMPLE_RESUME = """# ALEX DOE
Senior Delivery Lead | Toronto, ON
default@example.com • 416 555 1234 • linkedin.com/in/alexdoe

## PROFESSIONAL SUMMARY
Operations and delivery professional with 10+ years across Fortune 500 financial services and crown corporations.

## SKILLS
**Delivery & Program Management:** Agile, Scrum, Sprint Planning, Backlog Management, SAFe
**Tools & Platforms:** JIRA, Confluence, SAP, Microsoft Project
**Domain:** Financial Services, Payments, Public Sector

## WORK EXPERIENCE

### Senior Consultant | Acme Corp | Toronto | June 2022 – November 2023
- Led an 18-person cross-continent program delivering credit modernization for Discover Financial.
- Improved team velocity by 15 story points within three sprints.

### Payments Officer | TestCo | Toronto | December 2024 – March 2025
- Processed payment files valued $50K to $3M under Ontario's Expropriations Act.

## EDUCATION & CERTIFICATIONS
- MBA, Fordham University
- Project Management Professional (PMP), Active
"""


SAMPLE_COVER_LETTER = """# Cover Letter — Senior Product Manager at TD

May 5, 2026

Dear Hiring Manager,

I am writing to express my interest in the Senior Product Manager - C2B Bill Pay role at TD.

In my recent work at Acme Corp, I led an 18-person cross-continent program delivering credit decisioning modernization to Discover.

While my experience with biller direct integration is limited, my work on payments processing at TestCo provides a strong foundation.

I would welcome the opportunity to discuss how my experience aligns with your team's needs.

Sincerely,
Alex Doe
"""


def _render_sample(tmp_path):
    """Render the sample resume; return the saved .docx path."""
    renderer = ATSResumeRenderer()
    return renderer.render_resume(
        SAMPLE_RESUME,
        posting={"id": 335, "employer": "TD"},
        output_dir=tmp_path,
        today="20260505",
    )


# --- Renders cleanly ---------------------------------------------

def test_renders_valid_docx(tmp_path):
    """Rendering succeeds and the file is a parseable .docx."""
    out = _render_sample(tmp_path)
    assert out.exists()
    assert out.suffix == ".docx"
    # Re-open — if it parses, the file is structurally valid.
    doc = Document(str(out))
    assert len(doc.paragraphs) > 5


# --- Single column / no tables -----------------------------------

def test_single_column_layout(tmp_path):
    """No tables, no text boxes, no inline shapes — single column
    only."""
    out = _render_sample(tmp_path)
    doc = Document(str(out))
    assert len(doc.tables) == 0, "no tables in ATS-friendly resume"
    # python-docx exposes inline_shapes via the document; an
    # ATS-friendly resume has none (no images/icons).
    assert len(doc.inline_shapes) == 0


# --- Fonts --------------------------------------------------------

def test_font_is_calibri(tmp_path):
    out = _render_sample(tmp_path)
    doc = Document(str(out))
    fonts_used: set[str] = set()
    for para in doc.paragraphs:
        for run in para.runs:
            if run.font.name:
                fonts_used.add(run.font.name)
    assert fonts_used == {FONT_NAME}, (
        f"expected Calibri only; saw {fonts_used}"
    )


def test_body_font_size_11pt(tmp_path):
    """Body paragraphs (summary, bullets, skill terms) are 11pt."""
    out = _render_sample(tmp_path)
    doc = Document(str(out))
    body_size = Pt(BODY_FONT_SIZE_PT)
    saw_body = False
    for para in doc.paragraphs:
        # Skip the first three paragraphs (name + tagline + contact)
        # and the all-caps section headings — focus on body lines.
        text = para.text.strip()
        if not text:
            continue
        if text.upper() == text and not text.startswith("•"):
            # likely a heading or all-caps name; skip
            continue
        for run in para.runs:
            if run.font.size and not run.bold:
                assert run.font.size == body_size
                saw_body = True
    assert saw_body


def test_heading_font_size_13pt(tmp_path):
    """Section headings (PROFESSIONAL SUMMARY etc.) are 13pt."""
    out = _render_sample(tmp_path)
    doc = Document(str(out))
    heading_size = Pt(HEADING_FONT_SIZE_PT)
    expected_headings = {
        "PROFESSIONAL SUMMARY",
        "SKILLS",
        "WORK EXPERIENCE",
        "EDUCATION & CERTIFICATIONS",
    }
    seen: set[str] = set()
    for para in doc.paragraphs:
        text = para.text.strip()
        if text in expected_headings:
            for run in para.runs:
                if run.font.size:
                    assert run.font.size == heading_size, text
            seen.add(text)
    assert seen == expected_headings


def test_name_font_size_16pt(tmp_path):
    """Top-of-document name is 16pt bold."""
    out = _render_sample(tmp_path)
    doc = Document(str(out))
    first = doc.paragraphs[0]
    assert first.text.strip() == "ALEX DOE"
    run = first.runs[0]
    assert run.font.size == Pt(NAME_FONT_SIZE_PT)
    assert run.bold is True


# --- Margins -----------------------------------------------------

def test_margins_075_inches(tmp_path):
    out = _render_sample(tmp_path)
    doc = Document(str(out))
    expected = Inches(MARGIN_INCHES)
    for section in doc.sections:
        assert section.top_margin == expected
        assert section.bottom_margin == expected
        assert section.left_margin == expected
        assert section.right_margin == expected


# --- Header/footer empty -----------------------------------------

def test_no_content_in_header_footer(tmp_path):
    """ATS readers ignore headers/footers — keep them empty."""
    out = _render_sample(tmp_path)
    doc = Document(str(out))
    for section in doc.sections:
        header_text = "".join(
            p.text for p in section.header.paragraphs
        ).strip()
        footer_text = "".join(
            p.text for p in section.footer.paragraphs
        ).strip()
        assert header_text == ""
        assert footer_text == ""


# --- Section names + order --------------------------------------

def test_section_names_standard(tmp_path):
    """Section headings exactly match the standard ATS names, in
    the canonical order."""
    out = _render_sample(tmp_path)
    doc = Document(str(out))
    headings = []
    for para in doc.paragraphs:
        t = para.text.strip()
        if t in (
            "PROFESSIONAL SUMMARY", "SKILLS", "WORK EXPERIENCE",
            "EDUCATION & CERTIFICATIONS",
        ):
            headings.append(t)
    assert headings == [
        "PROFESSIONAL SUMMARY",
        "SKILLS",
        "WORK EXPERIENCE",
        "EDUCATION & CERTIFICATIONS",
    ], (
        "section order must be Summary -> Skills -> Experience -> "
        "Education (skills before experience per 2026 best practice)"
    )


# --- File naming --------------------------------------------------

def test_file_naming_convention(tmp_path):
    """Filename: {FirstName}{LastName}Resume{Company}{YYYYMMDD}.docx"""
    out = _render_sample(tmp_path)
    assert out.name == "AlexDoeResumeTD20260505.docx"


def test_split_first_last_handles_caps_and_lower():
    assert _split_first_last("ALEX DOE") == ("Alex", "Doe")
    assert _split_first_last("Alex Doe") == ("Alex", "Doe")
    assert _split_first_last("Mary Anne Smith") == ("Mary", "AnneSmith")
    assert _split_first_last("Solo") == ("Solo", "")


def test_company_token_strips_punctuation_and_spaces():
    assert _company_token("BigBank") == "BigBank"
    assert _company_token("BMO Financial Group") == "BMOFinancialGroup"
    assert _company_token("AT&T") == "ATT"
    assert _company_token("TD") == "TD"


# --- Cover letter rendering --------------------------------------

def test_render_cover_letter_creates_file(tmp_path):
    renderer = ATSResumeRenderer()
    out = renderer.render_cover_letter(
        SAMPLE_COVER_LETTER,
        posting={"id": 335, "employer": "TD"},
        candidate_name="Alex Doe",
        output_dir=tmp_path,
        today="20260505",
    )
    assert out.exists()
    assert out.name == "AlexDoeCoverLetterTD20260505.docx"
    # Cover letter has no tables / inline shapes
    doc = Document(str(out))
    assert len(doc.tables) == 0
    # Same Calibri / 11pt body convention
    fonts = {
        run.font.name for p in doc.paragraphs for run in p.runs
        if run.font.name
    }
    assert fonts == {FONT_NAME}


def test_render_resume_raises_on_missing_h1(tmp_path):
    """A draft missing the '# {NAME}' line should fail loudly."""
    renderer = ATSResumeRenderer()
    with pytest.raises(ValueError, match="must start with"):
        renderer.render_resume(
            "## PROFESSIONAL SUMMARY\nbody",
            posting={"id": 1, "employer": "X"},
            output_dir=tmp_path,
            today="20260505",
        )
