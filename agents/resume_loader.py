"""Loads a resume variant docx and returns its text as markdown.

Used by the prompt bundle generator to embed the appropriate resume
into the bundle pasted into Claude.ai Max.
"""

from __future__ import annotations

import logging
from pathlib import Path

from docx import Document


# --- Exceptions -----------------------------------------------------------

class ResumeLoadError(Exception):
    """Base class for resume loading errors."""


# --- Constants ------------------------------------------------------------

VARIANT_TO_FILENAME = {
    "public_sector":         "Alex_Doe_Resume_PublicSector.docx",
    "financial_services":    "Alex_Doe_Resume_FinancialServices.docx",
    "healthcare_education":  "Alex_Doe_Resume_HealthcareEducation.docx",
    "real_estate":           "Alex_Doe_Resume_RealEstate.docx",
}

_RESUME_DIR = (Path(__file__).resolve().parent.parent
               / "source_materials" / "resumes")


# --- Logging --------------------------------------------------------------

logger = logging.getLogger("resume_loader")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)


# --- Public API -----------------------------------------------------------

def load_resume_as_markdown(variant: str) -> str:
    """Load a resume variant and return its content as markdown text.

    Args:
        variant: One of public_sector, financial_services,
            healthcare_education, real_estate.

    Returns:
        Markdown string with paragraphs separated by blank lines and
        list-style paragraphs converted to bullet points. Bold/italic
        runs are preserved as ** and *.

    Raises:
        ValueError: if `variant` is not a known variant.
        FileNotFoundError: if the docx file does not exist on disk.
        ResumeLoadError: if the docx cannot be parsed.
    """
    if variant not in VARIANT_TO_FILENAME:
        raise ValueError(
            f"Unknown resume variant {variant!r}; "
            f"valid options: {sorted(VARIANT_TO_FILENAME)}"
        )
    path = _RESUME_DIR / VARIANT_TO_FILENAME[variant]
    if not path.exists():
        raise FileNotFoundError(f"Resume file not found: {path}")

    try:
        doc = Document(str(path))
    except Exception as e:
        raise ResumeLoadError(f"Could not open docx {path}: {e}") from e

    out: list[str] = []
    for para in doc.paragraphs:
        text = _render_paragraph(para)
        if not text.strip():
            out.append("")
            continue
        style = (para.style.name or "").lower() if para.style else ""
        if "list" in style or "bullet" in style:
            out.append(f"- {text}")
        elif style.startswith("heading"):
            # Cap at h4 for embedding within a larger markdown document.
            level = "".join(c for c in style if c.isdigit()) or "1"
            level_n = min(max(int(level), 1), 4)
            out.append(f"{'#' * (level_n + 2)} {text}")
        else:
            out.append(text)

    md = "\n".join(out).strip()
    logger.info("load_resume_as_markdown variant=%s chars=%d", variant, len(md))
    return md


# --- Helpers --------------------------------------------------------------

def _render_paragraph(para) -> str:
    """Render a docx paragraph, preserving bold/italic runs as markdown."""
    parts: list[str] = []
    for run in para.runs:
        text = run.text or ""
        if not text:
            continue
        if run.bold and run.italic:
            parts.append(f"***{text}***")
        elif run.bold:
            parts.append(f"**{text}**")
        elif run.italic:
            parts.append(f"*{text}*")
        else:
            parts.append(text)
    return "".join(parts)
