"""Prompt bundle generator for Claude.ai Max.

Builds markdown bundles that combine job posting, resume variant, and
filtered career inventory, plus framing instructions in the
Context/Problem/Success/Options/Depth format. Each bundle is paired
with an application record in the tracker and a folder under
data/applications/.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from engine.persistence.tracker import Tracker, TrackerError
from agents.resume_loader import load_resume_as_markdown, ResumeLoadError
from agents.inventory_loader import (
    load_inventory, filter_inventory_for_opportunity, InventoryLoadError,
)


# --- Exceptions -----------------------------------------------------------

class PromptBundleError(Exception):
    """Base class for prompt-bundle errors."""


__all__ = [
    "PromptBundleError", "ResumeLoadError", "InventoryLoadError",
    "PromptBundleGenerator", "BUNDLE_TYPES",
]


BUNDLE_TYPES = {
    "cover_letter",
    "cover_letter_with_federal_responses",
    "screening_questions",
    "recruiter_followup",
}

SECTOR_TO_VARIANT = {
    "public_sector":         "public_sector",
    "financial_services":    "financial_services",
    "healthcare_education":  "healthcare_education",
    "real_estate":           "real_estate",
    "exploration":           "public_sector",
    "unclassified":          "public_sector",
    None:                    "public_sector",
}


# --- Logging --------------------------------------------------------------

_LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "prompt_bundles.log"
logger = logging.getLogger("prompt_bundles")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    try:
        fh = logging.FileHandler(_LOG_PATH, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)


# --- Generator ------------------------------------------------------------

class PromptBundleGenerator:
    """Generates prompt bundles and registers application records."""

    def __init__(self, tracker: Optional[Tracker] = None,
                 applications_root: Optional[Path] = None):
        self.tracker = tracker or Tracker(profile_id="default")
        self._owns_tracker = tracker is None
        root = Path(__file__).resolve().parent.parent
        self.applications_root = (Path(applications_root)
                                  if applications_root
                                  else root / "data" / "applications")
        self.applications_root.mkdir(parents=True, exist_ok=True)

    # -- Public entry point ----------------------------------------------

    def generate_bundle(self, opportunity_id: int,
                        bundle_type: Optional[str] = None,
                        resume_variant_override: Optional[str] = None,
                        purpose: Optional[str] = None) -> dict:
        """Generate a bundle for an opportunity and register an application.

        Args:
            opportunity_id: Existing opportunity id in the tracker.
            bundle_type: Override; otherwise auto-determined from source/sector.
            resume_variant_override: Override the auto-selected resume variant.
            purpose: Free-text purpose, used only by recruiter_followup.

        Returns:
            Dict with application_id, bundle_path, bundle_type,
            resume_variant, folder_path.
        """
        opp = self.tracker.get_opportunity_by_id(opportunity_id)
        if opp is None:
            raise PromptBundleError(
                f"opportunity_id {opportunity_id} not found in tracker"
            )

        bundle_type = bundle_type or self._auto_bundle_type(opp)
        if bundle_type not in BUNDLE_TYPES:
            raise PromptBundleError(
                f"invalid bundle_type {bundle_type!r}; "
                f"allowed: {sorted(BUNDLE_TYPES)}"
            )

        variant = resume_variant_override or SECTOR_TO_VARIANT.get(
            opp.get("sector"), "public_sector"
        )

        try:
            resume_md = load_resume_as_markdown(variant)
        except (ResumeLoadError, FileNotFoundError, ValueError) as e:
            raise PromptBundleError(f"resume load failed: {e}") from e

        try:
            inventory = load_inventory()
            filtered = filter_inventory_for_opportunity(inventory, opp, top_n=3)
        except InventoryLoadError as e:
            raise PromptBundleError(f"inventory load failed: {e}") from e

        folder = self._build_application_folder(opp)
        bundle_path = folder / "prompt_bundle.md"
        content = self._render_bundle(
            bundle_type=bundle_type,
            opp=opp,
            variant=variant,
            resume_md=resume_md,
            filtered_inventory=filtered,
            purpose=purpose,
        )
        bundle_path.write_text(content, encoding="utf-8")

        app_id = self.tracker.create_application(
            opportunity_id=opportunity_id,
            resume_variant=variant,
            notes=f"bundle_type={bundle_type}; generated_at={_now_iso()}",
        )
        # Best-effort: opportunity may already be 'pursued'; that's fine.
        try:
            self.tracker.mark_opportunity_pursued(opportunity_id)
        except TrackerError as e:
            logger.warning("could not mark opportunity %d pursued: %s",
                           opportunity_id, e)

        self.tracker.log_event(
            event_type="application_drafted",
            summary=f"prompt bundle generated ({bundle_type}) for app {app_id}",
            entity_type="application",
            entity_id=app_id,
            details={
                "bundle_type":    bundle_type,
                "resume_variant": variant,
                "bundle_path":    str(bundle_path),
            },
        )
        logger.info("generate_bundle app=%d type=%s variant=%s path=%s",
                    app_id, bundle_type, variant, bundle_path)

        return {
            "application_id": app_id,
            "bundle_path":    str(bundle_path),
            "bundle_type":    bundle_type,
            "resume_variant": variant,
            "folder_path":    str(folder),
        }

    # -- Auto-determination ---------------------------------------------

    def _auto_bundle_type(self, opp: dict) -> str:
        source = (opp.get("source") or "").lower()
        sector = (opp.get("sector") or "").lower()
        if source == "recruiter":
            return "recruiter_followup"
        if sector == "public_sector":
            return "cover_letter_with_federal_responses"
        return "cover_letter"

    # -- Folder/slug helpers --------------------------------------------

    def _slugify(self, text: str) -> str:
        """Return a filesystem-safe slug (lowercase, _-separated, <=50 chars)."""
        if not text:
            return "unknown"
        s = text.lower()
        s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
        return (s or "unknown")[:50]

    def _build_application_folder(self, opp: dict) -> Path:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        emp = self._slugify(opp.get("employer", ""))
        title = self._slugify(opp.get("title", ""))
        base = self.applications_root / f"{date}_{emp}_{title}"
        folder = base
        suffix = 2
        while folder.exists():
            folder = base.with_name(f"{base.name}_{suffix}")
            suffix += 1
        folder.mkdir(parents=True, exist_ok=False)
        return folder

    # -- Rendering ------------------------------------------------------

    def _render_bundle(self, bundle_type: str, opp: dict, variant: str,
                       resume_md: str, filtered_inventory: dict,
                       purpose: Optional[str]) -> str:
        instructions = _INSTRUCTION_BLOCKS[bundle_type]
        if bundle_type == "recruiter_followup":
            instructions = instructions.replace(
                "{PURPOSE}", purpose or "status check"
            )
        markers = _OUTPUT_MARKERS[bundle_type]

        sections = [
            "# Prompt Bundle",
            f"_Generated: {_now_iso()}_",
            f"_Bundle type: `{bundle_type}`_",
            "",
            "## Instructions for Claude.ai Max",
            "",
            instructions,
            "",
            "---",
            "",
            "## Job Posting",
            "",
            _render_job_posting(opp),
            "",
            "---",
            "",
            f"## Resume Variant: `{variant}`",
            "",
            resume_md.strip(),
            "",
            "---",
            "",
            "## Career Inventory",
            "",
            _render_inventory(filtered_inventory),
            "",
        ]

        if bundle_type == "screening_questions":
            sections += [
                "---",
                "",
                "## Screening Questions",
                "",
                "_Paste the actual screening questions from the posting"
                " between the markers below before sending to Claude.ai Max._",
                "",
                "<<< BEGIN SCREENING QUESTIONS >>>",
                "1. ",
                "2. ",
                "3. ",
                "<<< END SCREENING QUESTIONS >>>",
                "",
            ]

        sections += ["---", "", "## Output Markers", "", markers, ""]
        return "\n".join(sections)


# --- Helpers --------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _render_job_posting(opp: dict) -> str:
    lines = [
        f"- **Employer:** {opp.get('employer', '')}",
        f"- **Title:** {opp.get('title', '')}",
        f"- **Location:** {opp.get('location') or 'n/a'}",
        f"- **Sector:** {opp.get('sector') or 'unclassified'}",
        f"- **Source:** {opp.get('source', '')}",
        f"- **URL:** {opp.get('url', '')}",
        f"- **Fit score:** {opp.get('fit_score') if opp.get('fit_score') is not None else 'n/a'}",
    ]
    if opp.get("fit_reasoning"):
        lines += ["", "**Fit reasoning:**", "", opp["fit_reasoning"]]
    return "\n".join(lines)


def _render_inventory(filtered: dict) -> str:
    parts = ["### All role summaries", ""]
    for s in filtered.get("all_role_summaries", []):
        parts.append(
            f"- **{s['title']}** ({s.get('company') or 'n/a'}): {s['summary']}"
        )
    parts += ["", "### Detailed relevant roles", ""]
    for r in filtered.get("detailed_roles", []):
        parts += [f"#### {r['title']}", "", r.get("full_text", "").strip(), ""]
    rationale = filtered.get("ranking_rationale")
    if rationale:
        parts += ["", f"_Ranking rationale: {rationale}_"]
    return "\n".join(parts)


# --- Instruction blocks --------------------------------------------------

_COVER_LETTER_INSTRUCTIONS = """\
**Context:** I am Alex Doe, a senior delivery/operations professional based in Toronto. I am applying for the role described below. Please draft a cover letter for this application.

**Problem Framing:** I need a cover letter that positions my experience as directly relevant to this role without overpromising or using generic language. The letter should feel like it was written by someone who has actually done the work described in my resume and inventory, not someone reciting keywords.

**Success Criteria:** A good cover letter is 300-400 words, has a specific opening that references something concrete about this role or employer, connects 2-3 specific experiences from my background to the requirements of the role, uses my own voice rather than generic consulting-speak, and ends with a clear statement of interest without being sycophantic.

**Options:** Please provide two alternative drafts: one that leads with technical depth (specific accomplishments, metrics, tools) and one that leads with leadership narrative (team building, delivery ownership, judgment calls). I will choose between them or combine elements from both.

**Depth Level:** Produce complete, polished cover letters ready for submission. Do not produce outlines or drafts that need significant editing."""

_FEDERAL_ADDENDUM = """\

---

**Federal Qualifications Addendum**

This is a federal government posting. In addition to the cover letter above, please draft responses to each essential qualification listed in the job posting. Each response should: (1) start with a clear statement of how I meet the qualification, (2) provide a specific example from my background using the STAR format (Situation, Task, Action, Result), (3) quantify outcomes where possible, (4) be 250-400 words per qualification."""

_SCREENING_INSTRUCTIONS = """\
**Context:** I am Alex Doe, applying for the role described below. The application includes screening questions (listed in the Screening Questions section).

**Problem Framing:** Please draft responses to each screening question. Each response should be 100-200 words, use specific examples from my background, and feel conversational rather than essay-like.

**Success Criteria:** Responses are concrete, draw directly from my resume/inventory, avoid generic platitudes, and are sized appropriately for an ATS form field.

**Options:** For any question where two angles seem viable, draft both and label them A/B.

**Depth Level:** Produce complete responses ready to paste into the application form."""

_RECRUITER_INSTRUCTIONS = """\
**Context:** I need to follow up with a recruiter about: {PURPOSE}.

**Problem Framing:** Draft a short message appropriate to that purpose - checking status, responding to a recruiter prompt, providing requested information, or scheduling a next step.

**Success Criteria:** A good recruiter message is 50-150 words, professional without being stiff, specific about what I'm following up on or providing, and makes the recruiter's job easier.

**Options:** Provide two alternative phrasings - one warmer/relational, one tighter/transactional.

**Depth Level:** Produce complete messages ready to send."""

_INSTRUCTION_BLOCKS = {
    "cover_letter": _COVER_LETTER_INSTRUCTIONS,
    "cover_letter_with_federal_responses":
        _COVER_LETTER_INSTRUCTIONS + _FEDERAL_ADDENDUM,
    "screening_questions":    _SCREENING_INSTRUCTIONS,
    "recruiter_followup":     _RECRUITER_INSTRUCTIONS,
}

_OUTPUT_MARKERS = {
    "cover_letter": (
        "<<< BEGIN COVER LETTER OPTION A (TECHNICAL DEPTH) >>>\n"
        "[Claude.ai Max writes option A here]\n"
        "<<< END COVER LETTER OPTION A >>>\n\n"
        "<<< BEGIN COVER LETTER OPTION B (LEADERSHIP NARRATIVE) >>>\n"
        "[Claude.ai Max writes option B here]\n"
        "<<< END COVER LETTER OPTION B >>>"
    ),
    "cover_letter_with_federal_responses": (
        "<<< BEGIN COVER LETTER OPTION A (TECHNICAL DEPTH) >>>\n"
        "[Claude.ai Max writes option A here]\n"
        "<<< END COVER LETTER OPTION A >>>\n\n"
        "<<< BEGIN COVER LETTER OPTION B (LEADERSHIP NARRATIVE) >>>\n"
        "[Claude.ai Max writes option B here]\n"
        "<<< END COVER LETTER OPTION B >>>\n\n"
        "<<< BEGIN FEDERAL QUALIFICATION RESPONSES >>>\n"
        "[Claude.ai Max writes responses here, one per qualification]\n"
        "<<< END FEDERAL QUALIFICATION RESPONSES >>>"
    ),
    "screening_questions": (
        "<<< BEGIN SCREENING RESPONSES >>>\n"
        "[one labeled response per question]\n"
        "<<< END SCREENING RESPONSES >>>"
    ),
    "recruiter_followup": (
        "<<< BEGIN RECRUITER FOLLOWUP OPTION A (WARM) >>>\n"
        "[option A]\n<<< END RECRUITER FOLLOWUP OPTION A >>>\n\n"
        "<<< BEGIN RECRUITER FOLLOWUP OPTION B (TIGHT) >>>\n"
        "[option B]\n<<< END RECRUITER FOLLOWUP OPTION B >>>"
    ),
}
