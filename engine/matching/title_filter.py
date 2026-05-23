"""Title pre-filter for scorer validation (Spec B3 TASK 2).

Reads a list of excluded title patterns from config/title_exclusions.yaml
and returns True if a posting title matches any of them. Matching is
case-insensitive substring.

Used by validate_scorer.py to skip obviously-unrelated postings before
scoring them. The pre-filter is profile-specific: the canonical list
is curated for a senior PM/delivery inventory. A future onboarding
wizard will auto-generate per-profile lists.

This module has no ML dependencies. Runs in venv\\.
"""
from __future__ import annotations

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PATH = PROJECT_ROOT / "config" / "title_exclusions.yaml"


def load_title_exclusions(path: Path = DEFAULT_PATH) -> list[str]:
    """Return the list of excluded title patterns (lowercased).

    Missing file returns [] -- the caller decides whether to warn or
    proceed unfiltered.
    """
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = data.get("excluded_titles") or []
    return [str(t).lower() for t in raw if t]


def is_title_excluded(
    title: str | None, exclusions: list[str],
) -> bool:
    """True iff `title` contains any pattern from `exclusions`.

    Matching is case-insensitive substring against the lowercased
    title. Empty / None title returns False (caller decides).
    """
    if not title:
        return False
    title_lower = title.lower()
    return any(exc in title_lower for exc in exclusions)
