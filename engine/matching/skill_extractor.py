"""Filtered wrapper around ojd_daps_skills SkillsExtractor.

Two-stage API (the real ojd_daps_skills 3.0.0 contract):
  doc = sm.get_skills(text)   # NER pass over a bare string
  sm.map_skills(doc)          # taxonomy mapping (mutates doc)
  mapped = doc._.mapped_skills

Calling sm([text]) directly is WRONG -- echoes input and bypasses
mapping.

This module MUST run under venv-skills\\ (the ojd-daps-skills 3.0.0
venv with patched torch pin). Imports of ojd_daps_skills are lazy
so tests can stub the extractor without venv-skills\\ installed.

Defaults are tuned for the Phase 5d Lightcast production pass:
  min_score=0.5     -- drop matches below 50% confidence
  exclude_types={'most_common_level_1'}
                    -- drop broad Lightcast category fallbacks
                       (Biology, Manufacturing Design, etc.)

Use .unfiltered(...) for debugging when you want every raw match.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("skill_extractor")


class SkillExtractorWrapper:
    """Thin wrapper that loads ojd_daps_skills SkillsExtractor lazily
    and applies a min_score + match_type filter to its output.
    """

    def __init__(
        self,
        taxonomy: str = "lightcast",
        min_score: float = 0.5,
        exclude_types: Optional[set[str]] = None,
    ) -> None:
        self.taxonomy = taxonomy
        self.min_score = min_score
        # set() means "exclude nothing". None means "use default".
        if exclude_types is None:
            self.exclude_types: set[str] = {"most_common_level_1"}
        else:
            self.exclude_types = set(exclude_types)
        self._extractor = None  # lazy

    @classmethod
    def unfiltered(cls, taxonomy: str = "lightcast") -> "SkillExtractorWrapper":
        """Debug helper: no score floor, no type exclusion."""
        return cls(taxonomy=taxonomy, min_score=0.0, exclude_types=set())

    def _ensure_loaded(self) -> None:
        if self._extractor is not None:
            return
        from ojd_daps_skills.extract_skills.extract_skills import (
            SkillsExtractor,
        )
        self._extractor = SkillsExtractor(taxonomy_name=self.taxonomy)

    def _raw_mapped(self, text: str) -> list[dict]:
        """Run the two-stage extractor; return raw mapped_skills."""
        self._ensure_loaded()
        doc = self._extractor.get_skills(text)
        self._extractor.map_skills(doc)
        return list(doc._.mapped_skills) if doc._.mapped_skills else []

    def _passes_filter(self, m: dict) -> bool:
        if not isinstance(m, dict):
            return False
        score = m.get("match_score", 0)
        if not isinstance(score, (int, float)):
            score = 0
        if score < self.min_score:
            return False
        match_type = m.get("match_type", "")
        if match_type in self.exclude_types:
            return False
        return True

    @staticmethod
    def _match_id(m: dict) -> Optional[str]:
        sid = m.get("match_id") or m.get("ojo_skill_id")
        if not sid and isinstance(m.get("predictions"), dict):
            sid = m["predictions"].get("match_id")
        return str(sid) if sid else None

    def extract_skill_ids(self, text: str) -> list[str]:
        """Return the list of taxonomy IDs that survive filtering.

        Order is extraction order (not score). Duplicates are
        preserved -- the scorer dedupes via set() at match time.
        """
        mapped = self._raw_mapped(text)
        kept_ids: list[str] = []
        dropped = 0
        for m in mapped:
            if not self._passes_filter(m):
                dropped += 1
                continue
            sid = self._match_id(m)
            if sid:
                kept_ids.append(sid)
            else:
                dropped += 1
        logger.debug(
            "extract_skill_ids(taxonomy=%s): mapped=%d kept=%d dropped=%d",
            self.taxonomy, len(mapped), len(kept_ids), dropped,
        )
        return kept_ids

    def extract_detailed(self, text: str) -> list[dict]:
        """Return the filtered mapped_skills dicts (full structure
        preserved). Useful for inspection / debugging / report
        generation.
        """
        mapped = self._raw_mapped(text)
        kept = [m for m in mapped if self._passes_filter(m)]
        logger.debug(
            "extract_detailed(taxonomy=%s): mapped=%d kept=%d",
            self.taxonomy, len(mapped), len(kept),
        )
        return kept
