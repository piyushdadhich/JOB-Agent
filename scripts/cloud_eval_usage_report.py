"""Cloud evaluator usage report.

Usage:
  python scripts/cloud_eval_usage_report.py
  python scripts/cloud_eval_usage_report.py --profile default

Reads data/{profile}/cloud_eval_usage.jsonl and prints:
  - Today (Pacific time): calls, tokens, errors, retry queue depth
  - Last 7 days: per-day breakdown
  - Lifetime: totals + averages

Pure read-only. Zero API calls.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

PACIFIC = ZoneInfo("America/Los_Angeles")
SOFT_STOP = 1200  # mirrors GemmaCloudClient.SOFT_STOP


def _load_entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _pacific_date(entry: dict) -> str:
    ts = entry.get("timestamp", "")
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return dt.astimezone(PACIFIC).strftime("%Y-%m-%d")


def _retry_queue_depth(profile_id: str) -> int:
    path = PROJECT_ROOT / "data" / profile_id / "cloud_eval_queue.json"
    if not path.exists():
        return 0
    try:
        queue = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0
    return sum(1 for q in queue if q.get("status") == "queued")


def _format_today(entries: list[dict], profile_id: str) -> list[str]:
    today_str = datetime.now(PACIFIC).strftime("%Y-%m-%d")
    today = [e for e in entries if _pacific_date(e) == today_str]
    if not today:
        return [
            f"  Today ({today_str}, Pacific):",
            "    No calls yet.",
            "",
        ]
    calls = len(today)
    input_tok = sum(e.get("input_tokens", 0) or 0 for e in today)
    output_tok = sum(e.get("output_tokens", 0) or 0 for e in today)
    latencies = [
        e.get("latency_ms", 0) or 0 for e in today
        if e.get("latency_ms") is not None
    ]
    avg_latency = (
        sum(latencies) / len(latencies) / 1000
    ) if latencies else 0
    errors = sum(1 for e in today if e.get("status") != "ok")
    error_kinds = Counter(
        e.get("status") for e in today if e.get("status") != "ok"
    )
    error_detail = ", ".join(
        f"{n} {k}" for k, n in error_kinds.most_common()
    )
    pct = calls / SOFT_STOP * 100
    err_line = f"    Errors:         {errors}"
    if error_detail:
        err_line += f" ({error_detail})"
    return [
        f"  Today ({today_str}, Pacific):",
        f"    Calls:          {calls} / {SOFT_STOP} ({pct:.1f}%)",
        f"    Input tokens:   {input_tok:,}",
        f"    Output tokens:  {output_tok:,}",
        f"    Avg latency:    {avg_latency:.1f}s per call",
        err_line,
        f"    Retry queue:    {_retry_queue_depth(profile_id)} pending",
        "",
    ]


def _format_last_7_days(entries: list[dict]) -> list[str]:
    by_day: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        d = _pacific_date(e)
        if d:
            by_day[d].append(e)
    today = datetime.now(PACIFIC).date()
    days = [
        (today - timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(7)
    ]
    lines = [
        "  Last 7 days:",
        "    Day        Calls    Errors   Budget%",
    ]
    for day in days:
        rows = by_day.get(day, [])
        if not rows:
            lines.append(
                f"    {day[5:]}         0        0       0.0%"
            )
            continue
        calls = len(rows)
        errs = sum(1 for r in rows if r.get("status") != "ok")
        pct = calls / SOFT_STOP * 100
        lines.append(
            f"    {day[5:]}    {calls:5d}    {errs:5d}     {pct:5.1f}%"
        )
    lines.append("")
    return lines


def _format_lifetime(entries: list[dict]) -> list[str]:
    if not entries:
        return ["  Lifetime: no calls yet.", ""]
    by_day: dict[str, int] = defaultdict(int)
    for e in entries:
        d = _pacific_date(e)
        if d:
            by_day[d] += 1
    total_calls = len(entries)
    total_errors = sum(1 for e in entries if e.get("status") != "ok")
    error_pct = (
        total_errors / total_calls * 100 if total_calls else 0
    )
    daily_counts = list(by_day.values())
    avg_daily = (
        sum(daily_counts) / len(daily_counts) if daily_counts else 0
    )
    peak_daily = max(daily_counts) if daily_counts else 0
    days_over_soft = sum(1 for n in daily_counts if n > SOFT_STOP)
    return [
        "  Totals:",
        f"    Lifetime calls:     {total_calls:,}",
        f"    Lifetime errors:    {total_errors} "
        f"({error_pct:.1f}% error rate)",
        f"    Avg daily calls:    {avg_daily:.0f}",
        f"    Peak daily calls:   {peak_daily}",
        f"    Days over soft stop: {days_over_soft}",
        "",
    ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="default")
    args = parser.parse_args(argv)

    log_path = (
        PROJECT_ROOT / "data" / args.profile / "cloud_eval_usage.jsonl"
    )
    entries = _load_entries(log_path)

    print()
    print("--- GEMMA 4 31B CLOUD USAGE REPORT ---")
    print()
    if not entries:
        print(f"  No usage log found at {log_path}.")
        print(
            "  Run scripts/run_daily.py --evaluator cloud to start logging."
        )
        print()
        return 0

    for line in _format_today(entries, args.profile):
        print(line)
    for line in _format_last_7_days(entries):
        print(line)
    for line in _format_lifetime(entries):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
