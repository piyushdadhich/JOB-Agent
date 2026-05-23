"""One-shot live smoke for the Workday client. Hits TD's CXS endpoint
and prints the first 5 records. Use:

  python scripts\workday_smoke.py
"""
from __future__ import annotations

import sys
from itertools import islice
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.workday_client import WorkdayClient


def main() -> int:
    td = {
        "name": "TD Bank", "tenant": "td",
        "wd_server": "3", "site": "TD_Bank_Careers",
    }
    client = WorkdayClient(
        [td], rate_limit=2.0, fetch_details=False, page_size=5,
    )
    print("Fetching first 5 listings from TD...")
    records = list(islice(client.fetch(), 5))
    for r in records:
        print(f"  {r.title[:55]:55s}  {(r.location or '')[:30]}")
        print(f"    -> {r.source_url}")
    print(f"\nGot {len(records)} records.")
    return 0 if records else 2


if __name__ == "__main__":
    sys.exit(main())
