"""Spec 6 — scorer-threshold calibrator.

The :class:`ScorerCalibrator` produces (TOP_TIER, STRONG,
EXPLORATORY) tier thresholds that aim for a healthy distribution
on the user's specific inventory. The default thresholds baked
into ``engine.matching.scorer`` (4.0 / 2.5 / 1.0) were tuned for
a 100-skill PM-style inventory; users with much smaller or much
larger inventories will see degenerate "everything is SKIP" or
"everything is TOP_TIER" without recalibration.

Flow:
  1. :meth:`auto_calibrate` picks a starting tier set from inventory
     size + actual scoring distribution.
  2. :meth:`guided_tune` lets the user nudge it based on a top-10
     / bottom-10 sample ("do these look like jobs you'd apply to?").

Both methods are pure functions of their inputs — the dashboard
routes glue them together with the tracker queries.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable


# Tier-tuning step size for guided_tune (in raw overall-score
# points). 0.5 is large enough to move the distribution meaningfully
# on a 0-10 scale without producing thrash; the user can re-run
# the tune step if the first nudge wasn't enough.
_NUDGE = 0.5


@dataclass(frozen=True)
class Thresholds:
    top: float
    strong: float
    exploratory: float

    def as_dict(self) -> dict:
        return asdict(self)

    def clamped(self) -> "Thresholds":
        """Return self with each band held above the next at a 0.5
        minimum gap and inside 0.0-10.0. Prevents pathological
        configs after repeated nudges (e.g. STRONG floating above
        TOP after multiple "lower" actions)."""
        top = max(0.0, min(10.0, self.top))
        strong = max(0.0, min(top - 0.5, self.strong))
        exploratory = max(0.0, min(strong - 0.5, self.exploratory))
        return Thresholds(top=top, strong=strong, exploratory=exploratory)


# --- Public API --------------------------------------------------

class ScorerCalibrator:
    """Pure-function calibration helpers.

    The class form (vs free functions) leaves room for future
    state — e.g., memoising calibration history per profile,
    surfacing "you've nudged 3 times, consider auto-calibrate
    again" hints — without changing the route surface.
    """

    def auto_calibrate(
        self,
        inventory_skill_count: int,
        scoring_distribution: dict,
    ) -> Thresholds:
        """Pick a starting threshold set.

        ``scoring_distribution`` is a dict with keys:
          - total: total scored postings (int)
          - top_tier_count, strong_count, exploratory_count, skip_count (int)

        Strategy:
          1. Seed from inventory size: small inventories need lower
             thresholds because their overall scores are mechanically
             smaller (fewer matched skills → less coverage).
          2. Nudge based on the current top-tier rate: aim for the
             5-15% sweet spot; tighten if the user is drowning in
             TOP_TIER cards, loosen if they're starving.
        """
        # --- Seed by inventory size ---
        if inventory_skill_count < 50:
            t = Thresholds(top=3.0, strong=1.5, exploratory=0.5)
        elif inventory_skill_count < 150:
            t = Thresholds(top=4.0, strong=2.5, exploratory=1.0)
        else:
            t = Thresholds(top=5.0, strong=3.5, exploratory=1.5)

        # --- Adjust by distribution ---
        total = max(1, int(scoring_distribution.get("total", 0)))
        top_tier_pct = (
            100.0 * int(scoring_distribution.get("top_tier_count", 0)) / total
        )
        if top_tier_pct > 20:
            t = Thresholds(
                top=t.top + _NUDGE,
                strong=t.strong + _NUDGE,
                exploratory=t.exploratory + _NUDGE,
            )
        elif top_tier_pct < 3:
            t = Thresholds(
                top=t.top - _NUDGE,
                strong=t.strong - _NUDGE,
                exploratory=t.exploratory - _NUDGE,
            )
        return t.clamped()

    def guided_tune(
        self,
        current: Thresholds,
        *,
        user_confirms_top: bool,
        user_confirms_bottom: bool,
    ) -> Thresholds:
        """Adjust ``current`` based on the user's yes/no on the
        top-10 and bottom-10 sample sets.

        Semantics:
          - "No" to top sample → the current top 10 contains junk →
            user wants TOP_TIER stricter → raise thresholds.
          - "No" to bottom sample → the current bottom 10 contains
            postings the user would actually consider → too many
            postings are being SKIP'd → lower thresholds.
          - "No" to both is a wash: the user wants fewer extreme
            ratings on both ends, which we approximate by holding
            current. Two separate runs (one each direction) is the
            right pattern.

        A "Yes" on a sample is treated as "no change in that direction".
        """
        if user_confirms_top and user_confirms_bottom:
            return current
        if (not user_confirms_top) and (not user_confirms_bottom):
            return current

        if not user_confirms_top:
            # Raise: TOP_TIER too loose; nudge every band up.
            return Thresholds(
                top=current.top + _NUDGE,
                strong=current.strong + _NUDGE,
                exploratory=current.exploratory + _NUDGE,
            ).clamped()
        # user_confirms_top=True, user_confirms_bottom=False:
        # the bottom is being too strict, lower the floor.
        return Thresholds(
            top=current.top - _NUDGE,
            strong=current.strong - _NUDGE,
            exploratory=current.exploratory - _NUDGE,
        ).clamped()


def distribution_from_rows(rows: Iterable[dict]) -> dict:
    """Helper for the routes layer: collapse an iterable of
    eval-decision rows (dicts with `tier` column) into the
    distribution shape :meth:`auto_calibrate` expects.
    """
    counts = {
        "top_tier_count": 0,
        "strong_count": 0,
        "exploratory_count": 0,
        "skip_count": 0,
        "total": 0,
    }
    for r in rows:
        tier = (r.get("tier") or "").upper()
        counts["total"] += 1
        if tier == "TOP_TIER":
            counts["top_tier_count"] += 1
        elif tier == "STRONG":
            counts["strong_count"] += 1
        elif tier == "EXPLORATORY":
            counts["exploratory_count"] += 1
        elif tier == "SKIP":
            counts["skip_count"] += 1
    return counts
