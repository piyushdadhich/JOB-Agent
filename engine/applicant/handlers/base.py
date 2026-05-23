"""ATSHandler protocol + shared dataclasses + URL detection."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FillContext:
    """Inputs handed to a handler when filling a form."""
    posting: dict                  # opportunity row from tracker
    profile: object                # ApplicantProfile
    resume_path: Path              # rendered .docx
    cover_letter_path: Path        # rendered .docx
    resume_text: str               # plain-text resume (for storage)
    cover_letter_text: str         # plain-text cover letter (for storage)
    qa_matcher: object             # QAMatcher (Tier 1)
    qa_generator: Optional[object] = None  # QAGenerator (Tier 2)
    dry_run: bool = False


@dataclass(frozen=True)
class ApplicationResult:
    ok: bool
    fields_filled: list[str] = field(default_factory=list)
    questions_answered: dict = field(default_factory=dict)
    questions_skipped: list[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass(frozen=True)
class SubmissionResult:
    ok: bool
    final_url: Optional[str] = None
    screenshot_path: Optional[Path] = None
    error: Optional[str] = None


@runtime_checkable
class ATSHandler(Protocol):
    name: str

    async def fill_application(
        self, page, ctx: FillContext,
    ) -> ApplicationResult:
        """Fill the form. Do NOT click submit."""
        ...

    async def submit(self, page) -> SubmissionResult:
        """Click submit. Called only after human approval."""
        ...

    async def capture_confirmation(
        self, page, output_path: Path,
    ) -> Path:
        """Screenshot the confirmation page. Returns the path."""
        ...


# --- ATS detection from URL --------------------------------------

_ATS_URL_PATTERNS = (
    (re.compile(r"(?:job-)?boards\.greenhouse\.io", re.I), "greenhouse"),
    (re.compile(r"jobs\.lever\.co", re.I),               "lever"),
    (re.compile(r"jobs\.ashbyhq\.com", re.I),            "ashby"),
    (re.compile(r"\.myworkdayjobs\.com", re.I),          "workday"),
    (re.compile(r"indeed\.com/applystart", re.I),        "indeed"),
    (re.compile(r"indeed\.com/viewjob", re.I),           "indeed"),
    (re.compile(r"linkedin\.com/jobs/view", re.I),       "linkedin"),
)


def detect_ats_from_url(url: str) -> Optional[str]:
    """Return the ATS short-name for a known URL, or None for fallback."""
    if not url:
        return None
    for pattern, name in _ATS_URL_PATTERNS:
        if pattern.search(url):
            return name
    return None
