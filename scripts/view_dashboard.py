"""Print a readable dashboard of current tracker state.

Run from project root with venv active:
    python scripts\\view_dashboard.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.persistence.tracker import Tracker  # noqa: E402


SECTION = "=" * 60


def _header(title: str) -> None:
    print()
    print(SECTION)
    print(f"  {title}")
    print(SECTION)


def _counts_block(d: dict) -> None:
    if not d:
        print("  (none)")
        return
    width = max(len(k) for k in d)
    for k in sorted(d.keys()):
        print(f"  {k.ljust(width)}  {d[k]:>5}")


def main() -> int:
    tracker = Tracker(profile_id="default")
    try:
        summary = tracker.get_dashboard_summary()

        _header("JOB-AGENT DASHBOARD")
        print(f"  Total opportunities:       {summary['total_opportunities']:>5}")
        print(f"  Opportunities this week:   {summary['opportunities_this_week']:>5}")
        print(f"  Total applications:        {summary['total_applications']:>5}")
        print(f"  Applications this week:    {summary['applications_this_week']:>5}")
        print(f"  Pending followups:         {summary['pending_followups']:>5}")
        print(f"  Active interviews:         {summary['active_interviews']:>5}")

        _header("OPPORTUNITIES BY SECTOR")
        _counts_block(summary["opportunities_by_sector"])

        _header("APPLICATIONS BY STATUS")
        _counts_block(summary["applications_by_status"])

        _header("PENDING FOLLOWUPS")
        followups = tracker.get_pending_followups()
        if not followups:
            print("  (none)")
        else:
            for c in followups:
                by = c["followup_by"] or "(no date)"
                print(f"  [{by}] app={c['application_id']} "
                      f"{c['channel']}/{c['direction']}: {c['summary']}")

        _header("RECENT EVENTS (last 10)")
        events = tracker.list_recent_events(limit=10)
        if not events:
            print("  (none)")
        else:
            for e in events:
                when = e["occurred_at"]
                ent = (f"{e['entity_type']}#{e['entity_id']}"
                       if e["entity_type"] else "-")
                print(f"  {when}  {e['event_type']:<25}  {ent:<18}  "
                      f"{e['summary']}")

        print()
        return 0
    finally:
        tracker.close()


if __name__ == "__main__":
    sys.exit(main())
