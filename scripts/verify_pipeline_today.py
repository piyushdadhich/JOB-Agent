"""Verify the dashboard /api/pipeline 'today' bug is fixed.

Calls _resolve_range('today') and runs the actual SQL queries so we
can see what 'today' returns BEFORE and AFTER the local-time fix.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.backend.routes.pipeline import (  # noqa: E402
    _SQL_DISCOVERED, _count_in_range, _resolve_range, _today,
)
from engine.persistence.tracker import Tracker  # noqa: E402
from engine.utils.day_boundary import (  # noqa: E402
    TORONTO, agent_day_range_utc_iso, current_agent_day,
)


def main() -> int:
    now = datetime.now(TORONTO)
    print(f"  Toronto now:      {now}")
    print(f"  System UTC now:   {datetime.now(timezone.utc)}")
    print(f"  date.today():     {date.today()}")
    print(f"  UTC date():       {datetime.now(timezone.utc).date()}")
    print(f"  current_agent_day(): {current_agent_day()}")
    print()

    print(f"  _today() returns: {_today()}")
    p_start, p_end, prev_start, prev_end = _resolve_range("today")
    print(
        f"  _resolve_range('today'): "
        f"period={p_start}..{p_end}  prev={prev_start}..{prev_end}"
    )

    # Sanity check: at 02:30 Toronto May 9, current_agent_day
    # should still return May 8 (the boundary fix).
    fake_2am = datetime(2026, 5, 9, 2, 30, tzinfo=TORONTO)
    print(
        f"  current_agent_day(2026-05-09 02:30 Toronto) = "
        f"{current_agent_day(now=fake_2am)} "
        f"(expected 2026-05-08)"
    )
    fake_3am = datetime(2026, 5, 9, 3, 30, tzinfo=TORONTO)
    print(
        f"  current_agent_day(2026-05-09 03:30 Toronto) = "
        f"{current_agent_day(now=fake_3am)} "
        f"(expected 2026-05-09)"
    )
    print()

    t = Tracker("default")
    try:
        # Use the public helper so the agent-day -> UTC-ISO range
        # conversion happens inside the query.
        count = _count_in_range(
            t, _SQL_DISCOVERED, p_start, p_end,
        )
        print(
            f"  Discovered today (period {p_start}..{p_end}): "
            f"{count} opportunities"
        )

        from datetime import date as _date
        for explicit in (_date(2026, 5, 8), _date(2026, 5, 9)):
            c = _count_in_range(
                t, _SQL_DISCOVERED, explicit, explicit,
            )
            start_utc, end_utc = agent_day_range_utc_iso(
                explicit, explicit,
            )
            print(
                f"  Discovered on agent-day {explicit} "
                f"(UTC range [{start_utc}, {end_utc})): {c}"
            )
    finally:
        t.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
