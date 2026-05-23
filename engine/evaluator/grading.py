"""Spec JA-1 TASK 2 — eval_decisions extras.

Three deterministic helpers and two Gemma-backed generators that
populate the new v2.20 columns on `eval_decisions`:

  letter_grade     -- score_to_letter(): pure fn of fit_score (0-10)
  red_flags        -- detect_red_flags(): pattern-match on posting + co
  interview_plan   -- build_interview_plan(): Gemma JSON output (A/B)
  culture_signals  -- build_culture_signals(): Gemma JSON output (A/B)

Both Gemma helpers accept an injectable ``ollama_call`` so tests can
run without a live server. They return [] on JSON parse failure
rather than raising — eval_decisions rows with no plan/signals are
valid (NULL on disk) and the caller decides whether to retry.

Letter-grade thresholds match the spec exactly:
  A: score >= 8.0
  B: score >= 6.5
  C: score >= 5.0
  D: score >= 3.0
  F: score < 3.0
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

OllamaCall = Callable[[str], str]

DEFAULT_MODEL = "gemma4:e4b"
DEFAULT_OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_TIMEOUT_SEC = 30

# Salary floor matches the spec's "below market" rule for senior PM /
# delivery roles in Toronto. Roles below this in the posted ceiling
# trigger a medium-severity flag.
SALARY_FLOOR = 80_000

YEARS_REQUIRED_CEILING = 15
TINY_COMPANY_BUCKETS = {"tiny", "<50", "<10", "1-10", "11-50"}

_STARTUP_SIGNALS = (
    "wear many hats", "startup mentality",
    "wear multiple hats", "scrappy", "rockstar",
)
_AFTER_HOURS_PATTERNS = ("24/7", "on-call", "weekend")
_UNPAID_TOKENS = ("unpaid",)


def score_to_letter(score: Optional[float]) -> Optional[str]:
    """Map a 0-10 fit_score to A/B/C/D/F. Returns None for None."""
    if score is None:
        return None
    try:
        s = float(score)
    except (TypeError, ValueError):
        return None
    if s >= 8.0:
        return "A"
    if s >= 6.5:
        return "B"
    if s >= 5.0:
        return "C"
    if s >= 3.0:
        return "D"
    return "F"


def is_top_grade(grade: Optional[str]) -> bool:
    """A and B grades get the LLM-backed extras."""
    return grade in ("A", "B")


# ---------------------------------------------------------------------------
# Red flags — deterministic, no LLM
# ---------------------------------------------------------------------------

def detect_red_flags(
    posting: dict, company: Optional[dict] = None,
) -> list[dict]:
    """Return a list of {flag, severity} dicts for one posting.

    Severities: 'low' | 'medium' | 'high'.
    Empty list means no flags fired.
    """
    flags: list[dict] = []
    company = company or {}
    text = str(posting.get("posting_text") or "").lower()

    salary_max = posting.get("salary_max")
    try:
        if salary_max is not None and float(salary_max) < SALARY_FLOOR:
            flags.append({
                "flag": "Below market compensation",
                "severity": "medium",
            })
    except (TypeError, ValueError):
        pass

    if any(t in text for t in _UNPAID_TOKENS):
        flags.append({"flag": "Unpaid position", "severity": "high"})

    if any(s in text for s in _STARTUP_SIGNALS):
        flags.append({
            "flag": "Startup workload signals",
            "severity": "low",
        })

    if any(p in text for p in _AFTER_HOURS_PATTERNS):
        flags.append({
            "flag": "After-hours work expected",
            "severity": "medium",
        })

    # "contract only" pattern — but not when posting also offers
    # permanent conversion. Naive but matches spec.
    if "contract" in text and "permanent" not in text:
        flags.append({"flag": "Contract only", "severity": "low"})

    years_match = re.search(r"(\d+)\+?\s*years", text)
    if years_match:
        try:
            yrs = int(years_match.group(1))
            if yrs > YEARS_REQUIRED_CEILING:
                flags.append({
                    "flag": f"{yrs}+ years required",
                    "severity": "medium",
                })
        except ValueError:
            pass

    size_bucket = str(company.get("size_bucket") or "").lower()
    if size_bucket in TINY_COMPANY_BUCKETS:
        flags.append({
            "flag": "Company < 50 employees",
            "severity": "low",
        })

    return flags


# ---------------------------------------------------------------------------
# Interview plan — Gemma JSON, A/B only
# ---------------------------------------------------------------------------

def _strip_think(raw: str) -> str:
    return re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S).strip()


def _parse_json_array(raw: str) -> list:
    cleaned = _strip_think(raw)
    if not cleaned:
        return []
    # Find the first '[' and matching ']' — Gemma sometimes wraps
    # JSON in markdown fences or trailing prose.
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    blob = cleaned[start:end + 1]
    try:
        parsed = json.loads(blob)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def build_interview_plan_prompt(
    posting: dict,
    matched_skills: list[str],
    gap_skills: list[str],
    proof_points: list[str],
    *,
    today: Optional[str] = None,
) -> str:
    today = today or date.today().isoformat()
    title = posting.get("title") or "the role"
    company = posting.get("company_name") or "the company"
    matched = ", ".join(matched_skills[:5]) or "n/a"
    gaps = ", ".join(gap_skills[:3]) or "n/a"
    proofs = "; ".join(proof_points[:5]) or "n/a"
    return (
        f"Today is {today}. Given this job posting: {title} at {company}. "
        f"Key required skills: {matched}. "
        f"Key gap skills: {gaps}. "
        f"Candidate proof points: {proofs}. "
        f"Generate exactly 3 interview talking points. Each must: "
        f"- Reference a specific proof point from the candidate "
        f"- Connect it to a specific requirement from the posting "
        f"- Be under 30 words. "
        f"Format as JSON array: "
        f'[{{"topic": "...", "talking_point": "...", '
        f'"proof_point": "..."}}] '
        f"Nothing else. /no_think"
    )


def build_interview_plan(
    posting: dict,
    matched_skills: list[str],
    gap_skills: list[str],
    proof_points: list[str],
    *,
    ollama_call: Optional[OllamaCall] = None,
    today: Optional[str] = None,
) -> list[dict]:
    """Return [] when grade isn't A/B (caller must gate) or on parse fail."""
    if ollama_call is None:
        return []
    prompt = build_interview_plan_prompt(
        posting, matched_skills, gap_skills, proof_points, today=today,
    )
    try:
        raw = ollama_call(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("interview-plan Ollama call failed: %s", exc)
        return []
    items = _parse_json_array(raw)
    # Defensive: keep only items that look like the spec shape.
    valid = []
    for item in items:
        if "talking_point" in item:
            valid.append({
                "topic": str(item.get("topic", "")).strip(),
                "talking_point": str(item["talking_point"]).strip(),
                "proof_point": str(item.get("proof_point", "")).strip(),
            })
    return valid[:3]


# ---------------------------------------------------------------------------
# Culture signals — Gemma JSON, A/B only
# ---------------------------------------------------------------------------

_VALID_SENTIMENTS = {"positive", "neutral", "negative"}


def build_culture_signal_prompt(
    posting: dict, *, today: Optional[str] = None,
) -> str:
    today = today or date.today().isoformat()
    text = str(posting.get("posting_text") or "")[:500]
    return (
        f"Today is {today}. Analyze this job posting for culture "
        f"signals. Posting: {text}. "
        f"Generate exactly 3 culture signals as JSON: "
        f'[{{"signal": "...", "sentiment": "positive|neutral|negative"}}] '
        f"Nothing else. /no_think"
    )


def build_culture_signals(
    posting: dict,
    *,
    ollama_call: Optional[OllamaCall] = None,
    today: Optional[str] = None,
) -> list[dict]:
    if ollama_call is None:
        return []
    prompt = build_culture_signal_prompt(posting, today=today)
    try:
        raw = ollama_call(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("culture-signals Ollama call failed: %s", exc)
        return []
    items = _parse_json_array(raw)
    valid = []
    for item in items:
        signal = str(item.get("signal", "")).strip()
        sent = str(item.get("sentiment", "")).strip().lower()
        if not signal:
            continue
        if sent not in _VALID_SENTIMENTS:
            sent = "neutral"
        valid.append({"signal": signal, "sentiment": sent})
    return valid[:3]
