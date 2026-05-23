"""FlagRule dataclass + match_opportunity helper (v2.12).

A flag_rule auto-skips matching opportunities at persist time.
A rule matches an opportunity if EVERY non-null pattern matches:

  employer_pattern, title_pattern  fnmatchcase (LIKE-style * and ?
                                   wildcards). Case-insensitive.
  industry_pattern, function_pattern,
  ai_subtype_pattern               exact string match
                                   (case-sensitive).

A rule with ALL patterns null is a "match everything" sentinel and
is rejected at API insert time -- it would auto-skip every future
posting.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FlagRule:
    id: int
    employer_pattern: Optional[str] = None
    title_pattern: Optional[str] = None
    industry_pattern: Optional[str] = None
    function_pattern: Optional[str] = None
    ai_subtype_pattern: Optional[str] = None

    def has_any_pattern(self) -> bool:
        return any(
            p is not None for p in (
                self.employer_pattern, self.title_pattern,
                self.industry_pattern, self.function_pattern,
                self.ai_subtype_pattern,
            )
        )

    def matches(
        self, *,
        employer: str,
        title: str,
        industry: Optional[str] = None,
        function: Optional[str] = None,
        ai_subtype: Optional[str] = None,
    ) -> bool:
        """Return True iff every non-null pattern matches.

        Null patterns are wildcards (always match). Industry, function,
        and ai_subtype use exact-string equality; employer and title
        use fnmatchcase with both sides lowercased for case-insensitive
        wildcard matching.
        """
        if self.employer_pattern is not None:
            if not fnmatch.fnmatchcase(
                (employer or "").lower(),
                self.employer_pattern.lower(),
            ):
                return False
        if self.title_pattern is not None:
            if not fnmatch.fnmatchcase(
                (title or "").lower(),
                self.title_pattern.lower(),
            ):
                return False
        if self.industry_pattern is not None:
            if industry != self.industry_pattern:
                return False
        if self.function_pattern is not None:
            if function != self.function_pattern:
                return False
        if self.ai_subtype_pattern is not None:
            if ai_subtype != self.ai_subtype_pattern:
                return False
        return True


def rule_from_row(row: dict) -> FlagRule:
    """Build a FlagRule from a SQLite row dict."""
    return FlagRule(
        id=row["id"],
        employer_pattern=row.get("employer_pattern"),
        title_pattern=row.get("title_pattern"),
        industry_pattern=row.get("industry_pattern"),
        function_pattern=row.get("function_pattern"),
        ai_subtype_pattern=row.get("ai_subtype_pattern"),
    )
