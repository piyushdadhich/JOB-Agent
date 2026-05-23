"""Bridge proximity signal from the Lightcast taxonomy hierarchy.

For posting skills NOT in the inventory, compute the minimum hop
distance through the taxonomy to the nearest inventory skill.
Hops translate to a 0.0..1.0 proximity score, then averaged across
the missed posting skills to produce the bridge signal for the
three-signal scorer.

Hierarchy shape (from `data/lightcast_hierarchy.json`):
  {"skill_id": ["top_category", "subcategory"], ...}
  e.g. "KS126XS6CQCFGC3NG79X" -> ["17.0", "17.0.442.0"]

Hop -> proximity:
  same subcategory  (1 hop) -> 0.75
  same category     (2 hops) -> 0.50
  different category (3+ hops) -> 0.25
  no hierarchy info on either side -> 0.0

This module has no ML dependencies. Runs in venv\\.
"""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_HIERARCHY_PATH = (
    PROJECT_ROOT / "data" / "lightcast_hierarchy.json"
)


def load_lightcast_hierarchy(
    path: Path = DEFAULT_HIERARCHY_PATH,
) -> dict[str, list[str]]:
    """Load the {skill_id: [category, subcategory]} mapping.

    Missing file returns {}; the caller decides whether to warn or
    proceed with a stub bridge signal.
    """
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
    out: dict[str, list[str]] = {}
    for sid, levels in raw.items():
        if isinstance(levels, list):
            out[str(sid)] = [str(x) for x in levels]
    return out


# Per-hop proximity scores. Tunable but kept conservative until
# Phase 6 calibrates against the eval set.
_HOP_PROXIMITY = {
    1: 0.75,  # same subcategory
    2: 0.50,  # same top-level category
    3: 0.25,  # cross-category (still some signal from a known
              # hierarchy on both ends; better than 0)
}


class BridgeCalculator:
    """Compute bridge proximity for a single posting against an
    inventory, using a pre-loaded Lightcast hierarchy.
    """

    def __init__(self, taxonomy_hierarchy: dict[str, list[str]]):
        self.hierarchy = taxonomy_hierarchy

    @staticmethod
    def _hop_distance(
        path_a: list[str], path_b: list[str],
    ) -> int:
        """1=same subcategory, 2=same category, 3=different.

        Identical paths return 1 (the shared subcategory). This
        helper is never called with paths from the same skill ID
        in normal scoring -- inventory skills are excluded before
        the call -- but the behavior is defined for safety.
        """
        if len(path_a) >= 2 and len(path_b) >= 2:
            if path_a[1] == path_b[1]:
                return 1
            if path_a[0] == path_b[0]:
                return 2
        elif len(path_a) >= 1 and len(path_b) >= 1:
            if path_a[0] == path_b[0]:
                return 2
        return 3

    def _nearest_inventory_proximity(
        self, skill: str, inventory: set[str],
    ) -> float | None:
        """Best proximity for a single missed posting skill against
        the inventory. None if the skill has no hierarchy info or
        no inventory skill does.

        Iterates the inventory; early-exits on the first 0.75 hit
        since that's the maximum possible non-self proximity.
        """
        skill_path = self.hierarchy.get(skill)
        if not skill_path:
            return None
        best = 0.0
        found_any = False
        for inv_skill in inventory:
            inv_path = self.hierarchy.get(inv_skill)
            if not inv_path:
                continue
            found_any = True
            hops = self._hop_distance(skill_path, inv_path)
            prox = _HOP_PROXIMITY.get(hops, 0.0)
            if prox > best:
                best = prox
                if best >= 0.75:
                    return best
        return best if found_any else None

    def bridge_score(
        self,
        posting_skills: set[str],
        inventory_skills: set[str],
    ) -> float:
        """Average bridge proximity across posting skills NOT in
        the inventory. Returns 1.0 when there are no missed skills
        (perfect match), 0.0 when there are no inventory skills or
        no missed skills have hierarchy info.
        """
        if not posting_skills:
            return 0.0
        if not inventory_skills:
            return 0.0
        missed = posting_skills - inventory_skills
        if not missed:
            return 1.0  # everything matched
        total = 0.0
        counted = 0
        for skill in missed:
            prox = self._nearest_inventory_proximity(
                skill, inventory_skills,
            )
            if prox is None:
                continue
            total += prox
            counted += 1
        if counted == 0:
            return 0.0
        return total / counted
