"""Loads and filters the career inventory markdown for the agent.

The career inventory at source_materials/career_inventory.md is the
master record of every role. The prompt bundle generator pulls a
filtered subset into each bundle so Claude.ai Max has full detail on
the most relevant roles plus a one-line summary of every other role.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


# --- Exceptions -----------------------------------------------------------

class InventoryLoadError(Exception):
    """Base class for inventory loading/parsing errors."""


# --- Logging --------------------------------------------------------------

logger = logging.getLogger("inventory_loader")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)


# --- Constants ------------------------------------------------------------

# Words that don't carry signal for keyword overlap. Kept short and
# domain-neutral; we want to keep things like "java" or "delivery".
_STOPWORDS = {
    "a", "an", "and", "the", "for", "of", "in", "on", "to", "with",
    "or", "by", "at", "as", "is", "are", "be", "this", "that", "from",
    "into", "via", "per", "i", "me", "my", "we", "our", "you", "your",
    "it", "its", "but", "not", "no", "so", "if", "then", "than",
    "n/a", "none", "new", "role", "roles",
}

# Headers like "## Roles (...)" or "## Role Details" are structural,
# not role entries. Real role headers contain an em dash or pipe
# separating the role title from the company.
_ROLE_HEADER_HINTS = ("\u2014", "|")  # em dash, pipe


# --- Path resolution ------------------------------------------------------

def _inventory_path() -> Path:
    """Return the absolute path to the career inventory markdown file."""
    load_dotenv()
    project_root = os.getenv("PROJECT_ROOT")
    base = Path(project_root) if project_root else Path(__file__).resolve().parent.parent
    return base / "source_materials" / "career_inventory.md"


# --- Public API -----------------------------------------------------------

def load_inventory(path: Optional[Path] = None) -> dict:
    """Parse the career inventory markdown into a structured dict.

    Args:
        path: Optional override for the inventory file path.

    Returns:
        A dict of the form:
            {"roles": [{"title": ..., "company": ..., "dates": ...,
                        "sections": {...}, "full_text": ...}, ...]}

    Raises:
        InventoryLoadError: if the file cannot be read.
    """
    p = path or _inventory_path()
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise InventoryLoadError(f"Cannot read inventory at {p}: {e}") from e

    roles = _parse_roles(text)
    logger.info("load_inventory roles=%d path=%s", len(roles), p)
    return {"roles": roles}


def filter_inventory_for_opportunity(inventory: dict, opportunity: dict,
                                     top_n: int = 3) -> dict:
    """Rank and filter inventory roles by keyword overlap with an opportunity.

    Args:
        inventory: The dict returned by load_inventory().
        opportunity: A row dict from the tracker (title, employer, sector,
            fit_reasoning, notes — any may be missing or None).
        top_n: How many of the highest-ranked roles to include in full.

    Returns:
        Dict with:
            all_role_summaries: short summary for every role
            detailed_roles:     top_n role dicts
            ranking_rationale:  human-readable scoreboard string
    """
    roles = inventory.get("roles", [])
    keywords = _extract_keywords(opportunity)

    scored = []
    for role in roles:
        score = _score_role(role, keywords)
        scored.append((score, role))

    # Stable sort, highest score first.
    scored.sort(key=lambda t: t[0], reverse=True)
    detailed = [r for (_, r) in scored[:top_n]]

    all_summaries = [
        {
            "title": r["title"],
            "company": r.get("company"),
            "summary": _short_summary(r),
        }
        for r in roles
    ]
    rationale_parts = [f"{r['title'].split('\u2014')[0].strip()}={s}"
                       for (s, r) in scored]
    rationale = "Keyword match scores: " + ", ".join(rationale_parts)

    logger.info("filter_inventory_for_opportunity top_n=%d total=%d kw=%d",
                top_n, len(roles), len(keywords))
    return {
        "all_role_summaries": all_summaries,
        "detailed_roles": detailed,
        "ranking_rationale": rationale,
    }


# --- Parsing internals ----------------------------------------------------

def _parse_roles(text: str) -> list[dict]:
    """Split markdown text into role dicts.

    Algorithm: walk lines, treat any `## ` header that contains an
    em-dash or pipe as a role header. Everything until the next role
    header (or end) is that role's body. Inside the body, `### `
    headers are subsection markers.
    """
    lines = text.splitlines()
    roles: list[dict] = []
    current: Optional[dict] = None
    current_section: Optional[str] = None
    body_lines: list[str] = []
    section_lines: dict[str, list[str]] = {}

    def flush_section():
        if current_section is not None:
            section_lines.setdefault(current_section, []).extend([])  # ensure key
            content = "\n".join(section_lines.get(current_section, [])).strip()
            current["sections"][current_section] = content

    def flush_role():
        if current is None:
            return
        flush_section()
        # Re-render full_text from accumulated body lines so it stays exact.
        current["full_text"] = "\n".join(body_lines).rstrip()
        roles.append(current)

    for line in lines:
        if line.startswith("## ") and not line.startswith("### "):
            header = line[3:].strip()
            if any(h in header for h in _ROLE_HEADER_HINTS):
                # New role.
                flush_role()
                current = _new_role(header)
                current_section = None
                body_lines = [line]
                section_lines = {}
                continue
            else:
                # Structural header outside role context — ignore.
                if current is not None:
                    body_lines.append(line)
                continue

        if current is None:
            continue

        body_lines.append(line)
        if line.startswith("### "):
            # New subsection.
            flush_section()
            sub = line[4:].strip()
            current_section = _normalize_section_key(sub)
            section_lines[current_section] = []
        else:
            if current_section is not None:
                section_lines[current_section].append(line)

    flush_role()
    return roles


def _new_role(header: str) -> dict:
    """Construct a role dict from a `## ` header line.

    Header forms supported:
        "Senior Consultant — Acme Corp | Personal Loan ..."
        "Senior Consultant — Acme Corp"
    Splits on em-dash for title/company; treats anything past a pipe
    as part of the title.
    """
    title = header
    company = None
    if "\u2014" in header:
        left, right = header.split("\u2014", 1)
        title = left.strip()
        company = right.strip()
        # If there's a pipe in the right side, keep it on the title side.
        if "|" in right:
            company_part, project = right.split("|", 1)
            company = company_part.strip()
            title = f"{title} \u2014 {project.strip()}"
    return {
        "title": header,
        "company": company,
        "dates": None,
        "sections": {},
        "full_text": "",
    }


def _normalize_section_key(name: str) -> str:
    key = name.lower().strip()
    key = re.sub(r"[^a-z0-9]+", "_", key).strip("_")
    return key or "section"


# --- Ranking internals ----------------------------------------------------

def _extract_keywords(opportunity: dict) -> set[str]:
    bits = [
        opportunity.get("title") or "",
        opportunity.get("employer") or "",
        opportunity.get("sector") or "",
        opportunity.get("fit_reasoning") or "",
        opportunity.get("notes") or "",
        opportunity.get("tags") or "",
    ]
    raw = " ".join(bits).lower()
    tokens = re.findall(r"[a-z0-9][a-z0-9+.#-]{1,}", raw)
    return {t for t in tokens if t not in _STOPWORDS and len(t) > 2}


def _score_role(role: dict, keywords: set[str]) -> int:
    if not keywords:
        return 0
    text = (role.get("full_text") or "").lower()
    return sum(1 for kw in keywords if kw in text)


def _short_summary(role: dict, max_chars: int = 200) -> str:
    sections = role.get("sections") or {}
    body = sections.get("role") or role.get("full_text") or ""
    body = re.sub(r"\s+", " ", body).strip()
    if len(body) <= max_chars:
        return body
    return body[:max_chars].rstrip() + "..."
