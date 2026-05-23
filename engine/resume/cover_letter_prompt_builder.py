"""Step 2: build a Claude-ready prompt for a cover letter tailored
to ONE posting.

Same input shape as the resume prompt builder (posting + eval +
gap_report + inventory) but the output is a 3-4 paragraph business
letter, not a structured resume.

Structural intent:
  Paragraph 1 — Hook: role, company, why this role.
  Paragraph 2 — Bridge ("the money paragraph"): connect 2-3
    concrete inventory accomplishments to the posting's top
    requirements. Lead with COVERED items.
  Paragraph 3 — Why this company + ONE gap bridge: show company
    research; mitigate the single most visible genuine gap with a
    bridge statement.
  Paragraph 4 — Close: enthusiasm, availability, sign-off.

The full posting text is embedded so Claude can mirror the
posting's language and address specific requirements verbatim.
"""
from __future__ import annotations

from typing import Optional

from engine.resume.applicant_contact import format_candidate_contact
from engine.resume.posting_gap_analyzer import PostingGapReport


COVER_LETTER_OUTPUT_TEMPLATE = """\
# Cover Letter — {Title} at {Employer}

{Month Day, Year}

{Salutation, e.g. "Dear Hiring Manager,"}

{Paragraph 1 — Hook: role + company + why this role}

{Paragraph 2 — Bridge: 2-3 concrete inventory accomplishments
mapped to the posting's top requirements, with metrics and
mirrored posting keywords}

{Paragraph 3 — Why this company + ONE bridge statement for the
single most visible genuine gap}

{Paragraph 4 — Close: enthusiasm, availability, sign-off}

Sincerely,
{candidate name — copy VERBATIM from the CANDIDATE CONTACT block}
"""


COVER_LETTER_ANTI_FABRICATION = """\
ANTI-FABRICATION RULE (CRITICAL):
Reference ONLY experience, employers, accomplishments, and metrics
that appear in the candidate's career inventory below. Do NOT
invent or embellish. Every dollar amount, percentage, headcount,
and duration in the cover letter must trace back to the inventory.
For genuine gaps, use a bridge statement that frames adjacent
experience honestly — never claim experience the candidate does
not have."""


# --- Helper builders ---------------------------------------------

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
        "POSTING TEXT (mirror this language where the candidate has matching experience):\n"
        f"{posting_text}\n"
    )


def _format_eval_context(eval_decision: Optional[dict]) -> str:
    if not eval_decision:
        return (
            "EVALUATOR CONTEXT:\n"
            "  (no prior evaluation — proceed directly from posting + "
            "inventory)\n"
        )
    tier = eval_decision.get("tier") or "?"
    fit = eval_decision.get("fit_score")
    fit_str = "?" if fit is None else str(fit)
    reasoning = (eval_decision.get("reasoning") or "").strip()
    parts = [
        "EVALUATOR CONTEXT (the pipeline's read on why this posting fits):",
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
            "  (no prior gap analysis — work from posting + inventory)\n"
        )
    parts: list[str] = []
    parts.append("GAP-AWARE GUIDANCE:")
    parts.append(
        f"  Coverage: {gap_report.coverage_score * 100:.0f}% — "
        f"interview readiness: {gap_report.interview_readiness}."
    )
    parts.append("")

    parts.append(
        "  LEAD with these covered strengths in the bridge "
        "paragraph (paragraph 2):"
    )
    if not gap_report.covered:
        parts.append("    (none surfaced)")
    else:
        for c in gap_report.covered[:5]:
            parts.append(
                f"    - {c.requirement} "
                f"  ← {c.inventory_role_id}: \"{c.inventory_evidence}\""
            )
    parts.append("")

    if gap_report.undocumented:
        parts.append(
            "  Optionally surface these undocumented items in the "
            "bridge paragraph IF the candidate has updated their "
            "inventory; otherwise skip them:"
        )
        for u in gap_report.undocumented[:5]:
            parts.append(
                f"    - {u.requirement} "
                f"  ← suggested home: {u.likely_source}"
            )
        parts.append("")

    # Identify the SINGLE most visible gap for paragraph 3 — pick
    # the first 'critical' if any, else the first overall.
    if gap_report.genuine_gaps:
        critical = [
            g for g in gap_report.genuine_gaps
            if g.impact == "critical"
        ]
        target = (critical or gap_report.genuine_gaps)[0]
        parts.append(
            "  In paragraph 3, address the SINGLE most visible "
            "genuine gap with one bridge statement. The most "
            "critical gap to mitigate:"
        )
        parts.append(
            f"    - Gap: {target.requirement} (impact: {target.impact})"
        )
        if target.mitigation:
            parts.append(
                f"    - Suggested bridge: {target.mitigation}"
            )
        parts.append("")
        if len(gap_report.genuine_gaps) > 1:
            parts.append(
                "  Do NOT enumerate every gap — only address the one above."
            )
            parts.append("")

    return "\n".join(parts)


def _format_instructions() -> str:
    return """\
INSTRUCTIONS — produce the cover letter as markdown matching the
OUTPUT TEMPLATE below.

A. FORMAT:
   - 3-4 paragraphs, single page (~250-350 words).
   - Professional but warm tone — confident, not pleading.
   - Salutation: "Dear Hiring Manager," unless a specific recipient
     name appears in the posting.

B. PARAGRAPH 1 — Hook:
   - State the role title and company by name.
   - One compelling sentence on why THIS role at THIS company.
   - Mention the candidate's strongest match (lead with the top
     covered item from the gap-aware guidance above).

C. PARAGRAPH 2 — Bridge (the money paragraph):
   - Connect 2-3 specific inventory accomplishments to the
     posting's key requirements.
   - Lead with COVERED items (strongest matches). If the inventory
     was updated post-gap-analysis, you may surface UNDOCUMENTED
     items too — but only if they're plausibly there.
   - Use concrete numbers from the inventory (no vague claims).
   - Mirror posting keywords where the candidate genuinely has
     the experience.
   - Lead choice based on posting flavor:
     * AI-adjacent posting -> lead with the Job Agent project
       (Independent AI Systems Developer)
     * Delivery / PM / financial services -> lead with Acme Corp
     * Public sector / payments / land acquisition -> lead with
       TestCo
     * Product ownership -> lead with MedCo or HealthCo

D. PARAGRAPH 3 — Why this company + ONE bridge statement:
   - Show concrete research / knowledge of the company (one
     specific reason — products, mission, recent initiative).
   - Connect the candidate's values or trajectory to the company's
     mission.
   - Include ONE bridge statement for the SINGLE most visible
     genuine gap (per the gap-aware guidance above). Frame
     honestly: "While my experience with [X] is limited, my work
     on [adjacent Y] has built a foundation to ramp quickly."
   - Do NOT enumerate every gap — only the one identified above.

E. PARAGRAPH 4 — Close:
   - Express enthusiasm.
   - Note availability for interview.
   - Professional sign-off ("Sincerely,").

F. KEYWORD MIRRORING:
   - Use the posting's exact terminology where the candidate has
     matching documented experience.
   - Include both acronym AND full term where applicable
     (e.g., "Project Management Professional (PMP)").
"""


def _format_output_template() -> str:
    return (
        "OUTPUT TEMPLATE — produce the cover letter using EXACTLY "
        "this markdown structure (the renderer parses it):\n\n"
        + COVER_LETTER_OUTPUT_TEMPLATE
        + "\n"
    )


# --- Main builder class -------------------------------------------

class CoverLetterPromptBuilder:
    """Assemble the cover letter prompt for one posting.

    Pure assembly — no I/O, no LLM calls. Tests pass canned inputs
    and assert structural properties of the returned prompt string.
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
            "You are a senior career writer drafting a cover letter "
            "for a candidate applying to a specific posting. The "
            "letter is a business document — professional, warm, "
            "specific, and never generic. Your output will be "
            "rendered into a .docx by a downstream tool — follow "
            "the markdown structure exactly."
        )
        sections.append("")

        sections.append(_format_posting_section(posting))
        sections.append(_format_eval_context(eval_decision))
        sections.append(_format_gap_guidance(gap_report))
        sections.append("")

        sections.append(_format_instructions())
        sections.append("")

        sections.append(_format_output_template())
        sections.append("")

        sections.append(format_candidate_contact(applicant_profile))
        sections.append("")

        sections.append(
            "CANDIDATE CAREER INVENTORY (every claim must trace here):"
        )
        sections.append("")
        sections.append(inventory_text or "(inventory not provided)")
        sections.append("")

        sections.append(COVER_LETTER_ANTI_FABRICATION)
        sections.append("")
        sections.append(
            "Now produce the cover letter as markdown using the "
            "OUTPUT TEMPLATE structure. Output ONLY the markdown — "
            "no preamble, no explanation, no closing remarks."
        )

        return "\n".join(sections)
