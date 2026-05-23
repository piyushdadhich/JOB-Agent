"""Spec 9 TASK 1 — manual worksheet generator.

For users who can't (or don't want to) run the Playwright filler,
this module fetches a posting's application URL, parses the form
fields out of the rendered HTML, and produces a printable per-field
"Field N: Label → enter '<value>' (from profile)" guide. The user
fills the form manually with the guide in hand.

The fetch is best-effort: a plain GET hits the page HTML and we
scrape inputs / textareas / selects. Anything rendered purely
client-side (Workday in particular) won't surface — for those
we surface the link only and let the user iterate inside the
Playwright filler.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup


@dataclass(frozen=True)
class WorksheetField:
    index: int
    field_type: str       # "text" | "textarea" | "select" | "checkbox" | "file" | "radio"
    label: str
    name: str | None
    suggested_value: str  # what to put in the field — from profile or "[paste resume]" etc.
    options: list[str]    # for select / radio


@dataclass(frozen=True)
class Worksheet:
    url: str
    fields: list[WorksheetField]
    notes: list[str]


_PROFILE_FIELD_MAP = {
    # crude label → applicant_profile field mapping.
    "first name":    ("text", "first_name"),
    "last name":     ("text", "last_name"),
    "full name":     ("text", "full_name"),
    "email":         ("text", "email"),
    "phone":         ("text", "phone"),
    "city":          ("text", "city"),
    "linkedin":      ("text", "linkedin_url"),
    "portfolio":     ("text", "portfolio_url"),
    "github":        ("text", "github_username"),
    "resume":        ("file", "[upload resume file]"),
    "cover letter":  ("file", "[upload cover-letter file]"),
    "salary":        ("text", "salary_expectation"),
    "start date":    ("text", "start_date"),
}


def _label_text(element) -> str:
    # BeautifulSoup helpers — try multiple sources for the label.
    if element.has_attr("aria-label"):
        return element["aria-label"].strip()
    if element.has_attr("placeholder"):
        return element["placeholder"].strip()
    if element.has_attr("name"):
        return element["name"].strip()
    if element.has_attr("id"):
        return element["id"].strip()
    return ""


def _all_match_candidates(element) -> list[str]:
    """Every attr value we'll feed to the profile-key matcher.

    Just using _label_text isn't enough — a placeholder of
    "you@example.com" displays better than the name "email" but
    only the name matches the profile-field map. Try all of them."""
    out: list[str] = []
    for attr in ("aria-label", "placeholder", "name", "id"):
        if element.has_attr(attr):
            v = element[attr].strip()
            if v:
                out.append(v)
    return out


def _suggested_for(
    candidates: list[str], applicant: dict | None,
) -> str:
    if not candidates:
        return "[fill from profile]"
    for raw in candidates:
        low = raw.lower().replace("_", " ").replace("-", " ")
        for key, (_, field) in _PROFILE_FIELD_MAP.items():
            if key in low:
                if field.startswith("["):
                    return field
                if applicant and field in applicant and applicant[field]:
                    return str(applicant[field])
                return f"[your {field}]"
    return "[fill in your answer]"


def _input_type(element) -> str:
    if element.name == "textarea":
        return "textarea"
    if element.name == "select":
        return "select"
    t = (element.get("type") or "text").lower()
    if t in {"text", "email", "tel", "url", "number"}:
        return "text"
    if t in {"checkbox", "radio", "file"}:
        return t
    return "text"


def _select_options(element) -> list[str]:
    if element.name != "select":
        return []
    return [
        opt.get_text(strip=True)
        for opt in element.find_all("option")
        if opt.get_text(strip=True)
    ]


def parse_html(
    url: str,
    html: str,
    *,
    applicant: dict | None = None,
) -> Worksheet:
    """Parse pre-fetched HTML into a :class:`Worksheet`.

    Separated from :func:`from_url` so tests can drive the parser
    without hitting the network.
    """
    soup = BeautifulSoup(html, "html.parser")
    selectors = ["input", "textarea", "select"]
    fields: list[WorksheetField] = []
    for tag in soup.find_all(selectors):
        ftype = _input_type(tag)
        # Skip hidden / submit / button inputs.
        type_attr = (tag.get("type") or "").lower()
        if tag.name == "input" and type_attr in {"hidden", "submit", "button", "image"}:
            continue
        label = _label_text(tag) or "(no label)"
        fields.append(WorksheetField(
            index=len(fields) + 1,
            field_type=ftype,
            label=label,
            name=tag.get("name"),
            suggested_value=_suggested_for(
                _all_match_candidates(tag), applicant,
            ),
            options=_select_options(tag),
        ))
    notes: list[str] = []
    if not fields:
        notes.append(
            "No form fields found in the served HTML. This is "
            "expected for SPA-rendered ATS pages (Workday, Lever's "
            "modern flow); fall back to the Playwright filler."
        )
    return Worksheet(url=url, fields=fields, notes=notes)


def from_url(
    url: str,
    *,
    applicant: dict | None = None,
    session: Optional[requests.Session] = None,
    timeout: int = 15,
) -> Worksheet:
    """Fetch the URL and parse it into a worksheet."""
    s = session or requests
    resp = s.get(url, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()
    return parse_html(url, resp.text, applicant=applicant)


def render_markdown(worksheet: Worksheet) -> str:
    """Render a worksheet as a printable markdown document."""
    lines = [
        f"# Application Worksheet — {worksheet.url}",
        "",
        f"{len(worksheet.fields)} field(s) detected.",
        "",
    ]
    for note in worksheet.notes:
        lines.append(f"> {note}")
        lines.append("")
    for f in worksheet.fields:
        lines.append(
            f"**Field {f.index}: {f.label}** ({f.field_type})"
        )
        lines.append(f"- Suggested: `{f.suggested_value}`")
        if f.options:
            lines.append(f"- Options: {', '.join(f.options[:10])}"
                          + (f" + {len(f.options) - 10} more"
                             if len(f.options) > 10 else ""))
        lines.append("")
    return "\n".join(lines)
