"""Stage 2a deterministic rule filter.

Runs before any Stage 2b LLM call. Rejects postings on hard rules so
the LLM never sees them: hard employer exclusions, geography
mismatch, seniority floor below the candidate's current level. Also
flags postings with insufficient data for manual review.

Five rules in evaluation order (short-circuit on first reject):

  1. completeness        — must have employer, title, posting_text
  2. hard_exclusion      — substring match against employer
  3. geography           — substring match against location, with
                           remote_acceptable handling for null
                           location
  4. seniority_floor     — parsed seniority must be >= current_level
  5. passed_all_rules    — PASS_TO_2B

A note on hard_exclusion semantics: the inventory's
`hard_exclusions` list mixes employer names ("MegaBank (Royal Bank of
Canada, all variants)") with engagement-type patterns ("Pure
status-coordination roles", "Matrix engagements where delivery
accountability is unclear"). Stage 2a treats every entry as an
employer-name candidate — it strips anything from the first '(' and
substring-matches the result against `posting.employer`. The
engagement-type phrases keep their full multi-word text as the
match key, which never appears in real employer names, so they are
naturally inert here. They are intended to be picked up by Stage 2b
through the prompt context (content-based filtering), not Stage 2a.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# --- Verdict types --------------------------------------------------

class Stage2aResult(Enum):
    REJECT_HARD = "reject_hard"
    PASS_TO_2B = "pass_to_2b"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class Stage2aVerdict:
    result: Stage2aResult
    rule_fired: str
    reasoning: str


# --- Seniority hierarchy --------------------------------------------

SENIORITY_ORDER = {
    "junior": 0,
    "mid": 1,
    "senior": 2,
    "lead": 3,
    "manager": 4,
    "director": 5,
    "vp": 6,
}

_JUNIOR_RE = re.compile(
    r"\b(?:junior|jr\.?|associate|entry[\s-]?level|entry)\b",
    re.IGNORECASE,
)
_VP_RE = re.compile(r"\b(?:vp|vice\s+president)\b", re.IGNORECASE)
_DIRECTOR_RE = re.compile(r"\bdirector\b", re.IGNORECASE)
_MANAGER_RE = re.compile(r"\bmanager\b", re.IGNORECASE)
_LEAD_RE = re.compile(r"\b(?:lead|leader)\b", re.IGNORECASE)
_SENIOR_RE = re.compile(r"\b(?:senior|sr\.?)\b", re.IGNORECASE)


def parse_title_seniority(title: str) -> Optional[str]:
    """Map a job title to a seniority key, or None if no signal.

    Junior signals win when present (so 'Junior Project Lead' parses
    as junior, not lead). Otherwise the highest seniority signal in
    the title wins.
    """
    if not title:
        return None
    if _JUNIOR_RE.search(title):
        return "junior"
    if _VP_RE.search(title):
        return "vp"
    if _DIRECTOR_RE.search(title):
        return "director"
    if _MANAGER_RE.search(title):
        return "manager"
    if _LEAD_RE.search(title):
        return "lead"
    if _SENIOR_RE.search(title):
        return "senior"
    return None


# --- Hard-exclusion key extraction ----------------------------------

def exclusion_key(exclusion: str) -> str:
    """Extract the substring-match key from a hard_exclusion entry.

    Strips anything from the first '(' onward (so "MegaBank (Royal Bank of
    Canada, all variants)" -> "rbc") and lowercases. Engagement-type
    phrases like "Pure status-coordination roles" have no parenthesis
    so the full phrase becomes the key — which intentionally never
    matches a real employer name.
    """
    head = re.split(r"[(]", exclusion, maxsplit=1)[0]
    return head.strip().lower()


# --- Stage 2a evaluator ---------------------------------------------

class Stage2a:
    """Deterministic pre-LLM filter."""

    def __init__(self, inventory_tool, profile_config) -> None:
        self._inv = inventory_tool
        self._cfg = profile_config

        self._exclusions: list[tuple[str, str]] = []
        for exc in inventory_tool.get_hard_exclusions():
            self._exclusions.append((exc, exclusion_key(exc)))
        for exc in profile_config.config_hard_exclusions:
            self._exclusions.append((exc, exclusion_key(exc)))

        self._geography_lower = [
            g.lower() for g in profile_config.geography
        ]
        self._remote_ok = profile_config.remote_acceptable

        current = inventory_tool.get_extract().trajectory.current_level
        self._current_level = current
        self._floor_rank = SENIORITY_ORDER.get(current, 0)

    def evaluate(self, posting) -> Stage2aVerdict:
        employer = (posting.get("employer") or "").strip()
        title = (posting.get("title") or "").strip()
        posting_text = (posting.get("posting_text") or "").strip()
        location = (posting.get("location") or "").strip()

        # Rule 1: completeness
        missing = []
        if not employer:
            missing.append("employer")
        if not title:
            missing.append("title")
        if not posting_text:
            missing.append("posting_text")
        if missing:
            return Stage2aVerdict(
                result=Stage2aResult.INSUFFICIENT_DATA,
                rule_fired="completeness",
                reasoning=(
                    f"missing required field(s): {', '.join(missing)}"
                ),
            )

        # Rule 2: hard exclusion (employer match)
        employer_lower = employer.lower()
        for original, key in self._exclusions:
            if key and key in employer_lower:
                return Stage2aVerdict(
                    result=Stage2aResult.REJECT_HARD,
                    rule_fired="hard_exclusion",
                    reasoning=(
                        f"employer {employer!r} matched hard "
                        f"exclusion {original!r}"
                    ),
                )

        # Rule 3: geography
        if not location:
            if not self._remote_ok:
                return Stage2aVerdict(
                    result=Stage2aResult.REJECT_HARD,
                    rule_fired="geography",
                    reasoning=(
                        "location is empty and remote_acceptable "
                        "is False"
                    ),
                )
        else:
            location_lower = location.lower()
            if self._geography_lower and not any(
                g in location_lower for g in self._geography_lower
            ):
                return Stage2aVerdict(
                    result=Stage2aResult.REJECT_HARD,
                    rule_fired="geography",
                    reasoning=(
                        f"location {location!r} not in "
                        f"{self._cfg.geography}"
                    ),
                )

        # Rule 4: seniority floor
        parsed = parse_title_seniority(title)
        if parsed is not None:
            parsed_rank = SENIORITY_ORDER[parsed]
            if parsed_rank < self._floor_rank:
                return Stage2aVerdict(
                    result=Stage2aResult.REJECT_HARD,
                    rule_fired="seniority_floor",
                    reasoning=(
                        f"title parsed as {parsed!r} which is below "
                        f"current_level {self._current_level!r}"
                    ),
                )

        # Rule 5: passed all rules
        return Stage2aVerdict(
            result=Stage2aResult.PASS_TO_2B,
            rule_fired="passed_all_rules",
            reasoning="passed all Stage 2a deterministic rules",
        )
