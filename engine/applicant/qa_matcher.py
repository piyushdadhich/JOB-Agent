"""Tier 1 screening-question matcher.

Pure-Python pattern matching. Each pattern is one of:
  - {match: [...], answer: "literal answer"}
  - {match: [...], strategy: "upload_resume" | "upload_cover_letter"
                            | "count_from_inventory", default: "..."}
  - {match: [...], answer_from_profile: "salary_expectation"}

First match wins. Case-insensitive substring matching.

Patterns load from config/applicant_qa_patterns.yaml.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PATTERNS_PATH = (
    PROJECT_ROOT / "config" / "applicant_qa_patterns.yaml"
)


@dataclass(frozen=True)
class QAMatch:
    pattern_index: int
    answer: Optional[str] = None
    strategy: Optional[str] = None  # upload_resume | upload_cover_letter


class QAMatcher:
    def __init__(
        self,
        patterns: list[dict],
        profile=None,
    ):
        self.patterns = list(patterns)
        self.profile = profile

    @classmethod
    def from_yaml(
        cls, path: Optional[Path] = None, profile=None,
    ) -> "QAMatcher":
        path = path or DEFAULT_PATTERNS_PATH
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(patterns=cfg.get("patterns") or [], profile=profile)

    def match(self, question_text: str) -> Optional[QAMatch]:
        if not question_text:
            return None
        q = question_text.lower()
        for i, pat in enumerate(self.patterns):
            triggers = pat.get("match") or []
            if not any(t.lower() in q for t in triggers):
                continue
            if "answer" in pat:
                return QAMatch(pattern_index=i, answer=str(pat["answer"]))
            if "strategy" in pat:
                strat = pat["strategy"]
                if strat == "count_from_inventory":
                    return QAMatch(
                        pattern_index=i,
                        answer=str(pat.get("default", "10+")),
                    )
                return QAMatch(pattern_index=i, strategy=strat)
            if "answer_from_profile" in pat:
                if self.profile is None:
                    continue
                value = getattr(
                    self.profile, pat["answer_from_profile"], None,
                )
                # Skip empty profile values -- otherwise the walker
                # would overwrite the field with "".
                if value:
                    return QAMatch(
                        pattern_index=i, answer=str(value),
                    )
        return None
