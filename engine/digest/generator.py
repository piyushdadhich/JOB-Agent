"""Spec 13 TASK 2 — daily digest.

`generate()` reads eval_decisions + applications and returns a
compact summary the dashboard's DailyDigest card renders. The
same payload also drives the optional terminal-print mode (the
scheduler can dump it to logs after a daily run).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from engine.persistence.tracker import Tracker


@dataclass(frozen=True)
class DigestPayload:
    window_hours: int
    new_top_tier: int
    new_strong: int
    new_exploratory: int
    needs_followup: int
    generated_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "window_hours": self.window_hours,
            "new_top_tier": self.new_top_tier,
            "new_strong": self.new_strong,
            "new_exploratory": self.new_exploratory,
            "needs_followup": self.needs_followup,
            "generated_at": self.generated_at,
        }


_TIER_SQL = """\
SELECT tier, COUNT(*) AS n
FROM eval_decisions
WHERE evaluated_at >= ?
  AND id IN (
    SELECT MAX(id) FROM eval_decisions GROUP BY opportunity_id
  )
GROUP BY tier
"""


_FOLLOWUP_SQL = """\
SELECT COUNT(*) AS n
FROM applications
WHERE status IN ('submitted', 'confirmed_received', 'responded', 'interviewing')
  AND status_updated_at IS NOT NULL
  AND status_updated_at < ?
"""


# Threshold for the "needs follow-up" count: applications sitting
# in applied/interview for >7 days with no status change.
_FOLLOWUP_AGE_DAYS = 7


def generate(
    tracker: Tracker,
    *,
    window_hours: int = 24,
) -> DigestPayload:
    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=window_hours)).isoformat()
    followup_cutoff = (
        now - timedelta(days=_FOLLOWUP_AGE_DAYS)
    ).isoformat()

    by_tier = {
        r["tier"]: int(r["n"])
        for r in tracker._query_all(_TIER_SQL, (since,))
    }
    followups_row = tracker._query_one(
        _FOLLOWUP_SQL, (followup_cutoff,),
    )
    followups = int(followups_row["n"]) if followups_row else 0

    return DigestPayload(
        window_hours=window_hours,
        new_top_tier=by_tier.get("TOP_TIER", 0),
        new_strong=by_tier.get("STRONG", 0),
        new_exploratory=by_tier.get("EXPLORATORY", 0),
        needs_followup=followups,
        generated_at=now.isoformat(),
    )
