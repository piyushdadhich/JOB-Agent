"""Format the CANDIDATE CONTACT block for resume / cover-letter
prompts.

The LLM otherwise has no source for the candidate's real contact
details — the career inventory is about experience, not PII — so it
would invent a phone number, email, or links.

Rather than handing the model a labelled field list and a format
template (which makes it *assemble* the header — and silently drop
any field the template forgot to mention, e.g. GitHub), this module
pre-builds the exact two-line header and tells the model to *copy*
it verbatim. Adding a contact field to the applicant YAML now flows
through automatically; nothing is hardcoded per-field.

Shared by ResumePromptBuilder and CoverLetterPromptBuilder. Accepts
a plain dict (the parsed applicant yaml) so the builders stay pure
assembly — prompt_service does the file I/O.
"""
from __future__ import annotations

from typing import Optional


def build_contact_header(applicant: Optional[dict]) -> str:
    """Pre-build the two-line resume header from the applicant
    profile:

      line 1: "{Name} | {City, Province}"
      line 2: every non-empty contact link joined with " • "

    Empty fields are skipped, so the line never has dangling
    separators. Returns "" when no applicant profile is given.
    """
    if not applicant:
        return ""
    first = str(applicant.get("first_name") or "").strip()
    last = str(applicant.get("last_name") or "").strip()
    name = f"{first} {last}".strip()
    city = str(applicant.get("city") or "").strip()
    province = str(applicant.get("province") or "").strip()
    location = ", ".join(p for p in (city, province) if p)

    line1 = f"{name} | {location}" if (name and location) else name

    parts: list[str] = []
    for key in ("email", "phone", "linkedin_url"):
        val = str(applicant.get(key) or "").strip()
        if val:
            parts.append(val)
    # External link: explicit github_url overrides portfolio_url.
    link = str(
        applicant.get("github_url") or applicant.get("portfolio_url") or ""
    ).strip()
    if link:
        parts.append(link)
    line2 = " • ".join(parts)

    return "\n".join(p for p in (line1, line2) if p)


def format_candidate_contact(applicant: Optional[dict]) -> str:
    """Return the CANDIDATE CONTACT block: a copy-ready two-line
    header plus education / certifications. When `applicant` is
    missing, returns a short note so the prompt still assembles."""
    if not applicant:
        return (
            "CANDIDATE CONTACT:\n"
            "  (applicant profile not provided — use the contact "
            "details from the career inventory)\n"
        )

    header = build_contact_header(applicant)

    lines = [
        "CANDIDATE CONTACT — copy the two header lines below VERBATIM "
        "into the resume header / cover-letter signature. Do not",
        "invent, reorder, reformat, abbreviate, or omit any value:",
        "",
    ]
    for hl in header.split("\n"):
        lines.append(f"  {hl}")
    lines.append("")

    for e in applicant.get("education") or []:
        if not isinstance(e, dict):
            continue
        degree = str(e.get("degree") or "").strip()
        school = str(e.get("school") or "").strip()
        if degree and school:
            lines.append(f"  Education: {degree}, {school}")
        elif degree:
            lines.append(f"  Education: {degree}")

    for c in applicant.get("certifications") or []:
        if not isinstance(c, dict):
            continue
        cname = str(c.get("name") or "").strip()
        if not cname:
            continue
        issuer = str(c.get("issuer") or "").strip()
        suffix = " (active)" if c.get("active", True) else ""
        if issuer:
            lines.append(f"  Certification: {cname} — {issuer}{suffix}")
        else:
            lines.append(f"  Certification: {cname}{suffix}")

    lines.append("")
    return "\n".join(lines)
