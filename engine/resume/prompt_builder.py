"""Step 1: build a Claude-ready prompt for a resume tailored to ONE
posting.

The prompt is plain text — paste it into Claude (or feed via the
Anthropic SDK), save Claude's response as a markdown draft, then
hand the draft to scripts/render_resume.py for ATS-friendly .docx
rendering.

The prompt embeds:
  - posting details (title, employer, location, full text)
  - evaluator context (tier, fit_score, reasoning) — if available
  - the candidate's full career_inventory.md (not just the summary)
  - GAP-AWARE GUIDANCE: covered items to emphasize, undocumented
    items to surface (post-update only), genuine gaps NOT to claim,
    and per-gap bridge-statement hints for the cover letter
  - the canonical ATS formatting rules so Claude's output is
    structurally renderable into .docx
  - an anti-fabrication clause repeated at the end for emphasis
"""
from __future__ import annotations

from typing import Optional

from engine.resume.applicant_contact import format_candidate_contact
from engine.resume.posting_gap_analyzer import PostingGapReport


# Canonical ATS rules used in BOTH this prompt and the renderer's
# enforcement step. Single source of truth so they don't drift.
ATS_FORMATTING_RULES = """\
- .docx output, single-column layout (no tables, text boxes, columns,
  graphics, icons, or images anywhere in the document).
- All content in the body — NOTHING in document header/footer (ATS
  ignores headers/footers).
- Calibri font family throughout: 11pt body, 13pt section headings,
  16pt name at top.
- 0.75 inch margins on all four sides; line spacing 1.0–1.15.
- Standard round bullets only (no checkmarks, arrows, custom symbols).
- Section names MUST be: PROFESSIONAL SUMMARY, SKILLS, WORK EXPERIENCE,
  EDUCATION & CERTIFICATIONS (no creative aliases like "About Me",
  "Career Journey", "What I Bring", "Core Competencies").
- Section order: Name + Contact → PROFESSIONAL SUMMARY → SKILLS →
  WORK EXPERIENCE → EDUCATION & CERTIFICATIONS.
- Dates: "Month Year – Month Year" (en-dash) or "Month Year – Present".
- 4–6 bullets per role from the past 5 years; 2–3 for older roles.
- Each bullet starts with an action verb and includes a measurable
  outcome where possible (PAR format: Problem → Action → Result).
- Mirror exact terminology from the job description; include both
  acronym and full term (e.g., "Project Management Professional (PMP)").
- Length target: 2 pages for 10+ years experience.
"""


# Strict markdown structure Claude must use so the renderer can
# parse the output into the right paragraph styles.
OUTPUT_MARKDOWN_TEMPLATE = """\
# {FULL NAME IN CAPS}
{tagline (one short line, e.g. "Senior Delivery Lead | City, ST")}
{contact line — copy VERBATIM from the CANDIDATE CONTACT block's
second header line; do not drop or reorder any link}

## PROFESSIONAL SUMMARY
{3-4 sentences tailored to THIS posting}

## SKILLS
**{Category 1}:** comma, separated, terms
**{Category 2}:** comma, separated, terms
**{Category 3}:** comma, separated, terms

## WORK EXPERIENCE

### {Title} | {Company} | {Location} | {Month Year – Month Year}
- {Action verb} ... {measurable outcome}
- {Action verb} ... {measurable outcome}

### {Title} | {Company} | {Location} | {Month Year – Month Year}
- ...

## EDUCATION & CERTIFICATIONS
- {Degree or Credential}, {Institution} ({Year if applicable})
- {Credential}, {Issuer} ({Status: Active, etc.})
"""


# The anti-fabrication clause — repeated at the end of the prompt
# for salience.
ANTI_FABRICATION_CLAUSE = """\
ANTI-FABRICATION RULE (CRITICAL):
Reference ONLY experience, skills, accomplishments, employers, and
metrics that appear in the candidate's career inventory below. Do
NOT invent, embellish, paraphrase beyond what's documented, or claim
experience the candidate does not have. If a posting requirement is
not in the inventory, leave it out of the resume — the cover letter
handles gaps. Every dollar amount, percentage, headcount, and
duration must trace back to the inventory."""


# --- Sub-section builders ----------------------------------------

def _format_posting_section(posting: dict) -> str:
    title = posting.get("title") or ""
    employer = posting.get("employer") or ""
    location = posting.get("location") or ""
    posting_text = (posting.get("posting_text") or "").strip()
    return (
        "POSTING:\n"
        f"  Employer: {employer}\n"
        f"  Title:    {title}\n"
        f"  Location: {location}\n"
        "\n"
        "POSTING TEXT:\n"
        f"{posting_text}\n"
    )


def _format_eval_context(eval_decision: Optional[dict]) -> str:
    if not eval_decision:
        return (
            "EVALUATOR CONTEXT:\n"
            "  (no prior evaluation — proceed using the posting + "
            "inventory directly)\n"
        )
    tier = eval_decision.get("tier") or "?"
    fit = eval_decision.get("fit_score")
    fit_str = "?" if fit is None else str(fit)
    reasoning = (eval_decision.get("reasoning") or "").strip()
    parts = [
        "EVALUATOR CONTEXT (why the pipeline picked this posting):",
        f"  Tier: {tier}, fit_score: {fit_str}/10",
    ]
    if reasoning:
        parts.append(f"  Reasoning: {reasoning}")
    parts.append("")
    return "\n".join(parts)


def _format_gap_guidance(gap_report: Optional[PostingGapReport]) -> str:
    if not gap_report:
        return (
            "GAP-AWARE GUIDANCE:\n"
            "  (no prior gap analysis — use the inventory and posting "
            "text directly)\n"
        )

    parts: list[str] = []
    parts.append(
        "GAP-AWARE GUIDANCE (from prior posting-gap analysis):"
    )
    parts.append(
        f"  Coverage: {gap_report.coverage_score * 100:.0f}% — "
        f"interview readiness: {gap_report.interview_readiness}."
    )
    parts.append("")

    parts.append(
        "  EMPHASIZE these covered requirements — the posting asks "
        "for them and the candidate has them:"
    )
    if not gap_report.covered:
        parts.append("    (none surfaced)")
    else:
        for c in gap_report.covered:
            parts.append(
                f"    - {c.requirement} "
                f"  ← {c.inventory_role_id}: \"{c.inventory_evidence}\" "
                f"({c.strength})"
            )
    parts.append("")

    parts.append(
        "  POST-UPDATE — surface these undocumented items if (and "
        "only if) the inventory was updated to include them:"
    )
    if not gap_report.undocumented:
        parts.append("    (none surfaced)")
    else:
        for u in gap_report.undocumented:
            parts.append(
                f"    - {u.requirement} "
                f"  ← suggested home: {u.likely_source} "
                f"({u.urgency} urgency)"
            )
    parts.append("")

    parts.append(
        "  DO NOT CLAIM — these are genuine gaps; the candidate "
        "does not have this experience. Leave them OFF the resume; "
        "the cover letter handles them via bridge statements:"
    )
    if not gap_report.genuine_gaps:
        parts.append("    (none surfaced)")
    else:
        for g in gap_report.genuine_gaps:
            parts.append(
                f"    - {g.requirement} (impact: {g.impact})"
            )
            if g.mitigation:
                parts.append(
                    f"      cover-letter bridge hint: {g.mitigation}"
                )
    parts.append("")
    return "\n".join(parts)


def _format_role_selection_hints(posting: dict) -> str:
    """Soft heuristics from the spec, baked into the prompt as a
    nudge for Claude on which inventory roles to lead with."""
    return (
        "ROLE SELECTION HINTS:\n"
        "  - Lead with the AI Systems Developer role when the "
        "posting is AI-adjacent (mentions LLM, ML, agentic AI, "
        "AI strategy, etc.).\n"
        "  - Lead with Acme Corp when the posting is "
        "delivery / program management / financial services.\n"
        "  - Lead with TestCo when the posting is public sector, "
        "payments, expropriations, or land acquisition.\n"
        "  - BigBank can be collapsed to 2 bullets (early "
        "career foundation).\n"
        "  - PMP Instructor can be omitted unless the posting "
        "values teaching, training, or curriculum delivery.\n"
        "  - Choose 4–5 roles total; older ones get 2–3 bullets, "
        "the most recent and most relevant get 4–6.\n"
    )


def _format_instructions() -> str:
    return """\
INSTRUCTIONS — produce the resume's content as markdown matching
the OUTPUT TEMPLATE below.

A. PROFESSIONAL SUMMARY (3–4 sentences):
   - Open with: "[Descriptor] [Title] with [X]+ years of experience..."
   - Lead with the strongest match from the gap-aware guidance
     above (covered items first).
   - Mention PMP certification and MBA in the summary or skills.
   - End with one specific value-prop for THIS company / role.
   - Mirror 2–3 keywords from the posting text verbatim.

B. SKILLS (grouped by category):
   - Default categories (include only those relevant to the posting):
     * Delivery & Program Management
     * Tools & Platforms
     * AI & Automation                (only if posting is AI-adjacent)
     * Domain                         (e.g. Financial Services, Payments,
                                       Public Sector — match posting)
   - Each category: 5–8 comma-separated terms.
   - Mirror posting keywords where the candidate actually has the skill.
   - Include both acronym AND full term where applicable
     (e.g., "Agile Scrum Framework (Scrum)").
   - Skills section MUST come BEFORE Work Experience (2026 best
     practice — skills-based screening is the primary ATS filter
     at most US enterprise hiring teams now).

C. WORK EXPERIENCE (reverse chronological):
   - Use the role-selection hints below to pick 4–5 roles.
   - Header line per role: "Title | Company | Location | Month Year – Month Year"
   - Bullets follow PAR format: Problem → Action → Result.
   - Every bullet starts with an action verb; every bullet has a
     measurable outcome where the inventory provides one.

D. EDUCATION & CERTIFICATIONS:
   - MBA (Fordham University), PMP (active), and any other
     credentials documented in the inventory.

E. KEYWORD ALIGNMENT:
   - Mirror exact terminology from the posting where the
     candidate has matching experience documented.
   - Include both the acronym AND the full term at least once
     (e.g., "Project Management Professional (PMP)").
   - Do not stuff keywords — every keyword must map to a real
     bullet, skill, or summary phrase.
"""


def _format_output_template() -> str:
    return (
        "OUTPUT TEMPLATE — produce content using EXACTLY this markdown "
        "structure (the renderer parses it):\n\n"
        + OUTPUT_MARKDOWN_TEMPLATE
        + "\n"
    )


# --- Main builder class -------------------------------------------

class ResumePromptBuilder:
    """Assemble the resume prompt for one posting.

    Pure assembly — no I/O, no LLM calls. Tests pass canned
    posting/eval/gap inputs and assert structural properties of the
    output string.
    """

    def build_prompt(
        self,
        posting: dict,
        eval_decision: Optional[dict] = None,
        gap_report: Optional[PostingGapReport] = None,
        inventory_text: str = "",
        inventory_extract: Optional[dict] = None,
        profile_config: Optional[dict] = None,
        applicant_profile: Optional[dict] = None,
    ) -> str:
        sections: list[str] = []

        sections.append(
            "You are a senior career writer creating an ATS-friendly "
            "resume tailored to a specific job posting. Your output "
            "will be rendered into a .docx by a downstream tool — "
            "follow the markdown structure exactly."
        )
        sections.append("")

        sections.append(_format_posting_section(posting))
        sections.append(_format_eval_context(eval_decision))
        sections.append(_format_gap_guidance(gap_report))
        sections.append(_format_role_selection_hints(posting))
        sections.append("")

        sections.append("ATS FORMATTING RULES (must be reflected in the output):")
        sections.append(ATS_FORMATTING_RULES)
        sections.append("")

        sections.append(_format_instructions())
        sections.append("")

        sections.append(_format_output_template())
        sections.append("")

        sections.append(format_candidate_contact(applicant_profile))
        sections.append("")

        sections.append("CANDIDATE CAREER INVENTORY (canonical source — every claim must trace here):")
        sections.append("")
        sections.append(inventory_text or "(inventory not provided)")
        sections.append("")

        sections.append(ANTI_FABRICATION_CLAUSE)
        sections.append("")
        sections.append(
            "Now produce the resume content as markdown using the "
            "OUTPUT TEMPLATE structure. Output ONLY the markdown — "
            "no preamble, no explanation, no closing remarks."
        )

        return "\n".join(sections)
