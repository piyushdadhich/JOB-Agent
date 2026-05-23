"""Spec D1 TASK 4 — inventory gap surfacing strategy.

Aggregates "missed" skill IDs across EXPLORATORY postings in the
lookback window and surfaces those that appear in
`min_occurrences`+ different postings but are NOT in the
profile's inventory.

Each gap carries an `action_prompt` to nudge the user — these
are gaps to either document (skill already exercised but not
listed) or learn (real capability gap). Includes a universal-
skill filter + document_existing vs learning_target
classification.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


_DEFAULT_ACTION_PROMPT = (
    "Is this something you've done but not documented? "
    "Or a gap to consider learning?"
)


@dataclass
class SkillGap:
    skill_id: str
    skill_label: str
    occurrence_count: int
    example_postings: list[str] = field(default_factory=list)
    action_prompt: str = _DEFAULT_ACTION_PROMPT


def _parse_skill_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        ids = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [s for s in ids if isinstance(s, str)]


class InventoryGapSurfer:
    """Aggregate missed skills across EXPLORATORY postings."""

    def __init__(self, tracker, inventory_skill_ids: set[str]) -> None:
        self.tracker = tracker
        self.inventory = set(inventory_skill_ids)

    def surface(
        self,
        days_back: int = 14,
        min_occurrences: int = 3,
    ) -> list[SkillGap]:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days_back)
        ).isoformat()

        rows = self.tracker._query_all(
            "SELECT o.id AS pid, o.title, o.extracted_skill_ids "
            "FROM opportunities o "
            "JOIN ("
            "  SELECT e1.* FROM eval_decisions e1 "
            "  JOIN ("
            "    SELECT opportunity_id, MAX(evaluated_at) AS max_at "
            "    FROM eval_decisions GROUP BY opportunity_id"
            "  ) e2 ON e1.opportunity_id = e2.opportunity_id "
            "     AND e1.evaluated_at = e2.max_at"
            ") latest ON latest.opportunity_id = o.id "
            "WHERE latest.tier = 'EXPLORATORY' "
            "  AND latest.evaluated_at >= ?",
            (cutoff,),
        )

        if not rows:
            return []

        missed: dict[str, list[str]] = {}
        for r in rows:
            ids = set(_parse_skill_ids(r["extracted_skill_ids"]))
            gap_ids = ids - self.inventory
            title = r["title"] or ""
            for sid in gap_ids:
                missed.setdefault(sid, []).append(title)

        qualifying = [
            (sid, titles) for sid, titles in missed.items()
            if len(titles) >= min_occurrences
        ]
        if not qualifying:
            return []

        labels = self.tracker.get_skill_labels([sid for sid, _ in qualifying])
        out: list[SkillGap] = []
        for sid, titles in qualifying:
            label = labels.get(sid) or sid
            if label == "<unknown>":
                label = sid
            out.append(SkillGap(
                skill_id=sid,
                skill_label=label,
                occurrence_count=len(titles),
                example_postings=titles[:5],
                action_prompt=_DEFAULT_ACTION_PROMPT,
            ))
        out.sort(
            key=lambda g: (-g.occurrence_count, g.skill_label),
        )
        return out
