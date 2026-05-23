"""Spec 13 TASK 1 — daily pipeline orchestrator.

`run()` is the entry point both the OS scheduler (`job-agent run`
called by Task Scheduler / launchd / systemd) and the dashboard's
"run now" button hit. It sequences the daily phases through the
existing scripts (run_daily, run_expansion, etc.) and writes a
run-history entry to data/{profile}/run_history.json so the
dashboard's "last run" lines have something to read.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


@dataclass
class RunRecord:
    started_at: str
    finished_at: str
    duration_seconds: float
    phases: dict          # phase name → "ok" | "skipped" | "failed: <msg>"
    return_code: int


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _history_path(profile_id: str) -> Path:
    return (
        _project_root() / "data" / profile_id / "run_history.json"
    )


def _load_history(profile_id: str) -> list[dict]:
    path = _history_path(profile_id)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


_MAX_HISTORY = 60


def _append_history(profile_id: str, record: RunRecord) -> None:
    history = _load_history(profile_id)
    history.append(asdict(record))
    if len(history) > _MAX_HISTORY:
        history = history[-_MAX_HISTORY:]
    path = _history_path(profile_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(history, indent=2, sort_keys=False),
        encoding="utf-8",
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run(
    profile_id: str = "default",
    *,
    include_expansion: Optional[bool] = None,
    dry_run: bool = False,
) -> RunRecord:
    """Run the daily pipeline for ``profile_id``.

    `include_expansion` defaults to True if today is Monday (weekly
    expansion cadence); explicit True / False overrides that.
    """
    start = _now()
    phases: dict[str, str] = {}
    rc = 0

    # Phase: daily discover → evaluate → shortlist.
    try:
        from scripts import run_daily
        args = ["--profile", profile_id]
        if dry_run:
            args.append("--dry-run")
        result = run_daily.main(args)
        if result == 0:
            phases["daily"] = "ok"
        else:
            phases["daily"] = f"failed: exit {result}"
            rc = result
    except Exception as e:
        phases["daily"] = f"failed: {type(e).__name__}: {e}"
        rc = 1

    # Phase: weekly expansion (Monday by default).
    weekly = (
        include_expansion
        if include_expansion is not None
        else start.weekday() == 0
    )
    if weekly:
        try:
            from scripts import run_expansion
            result = run_expansion.main(["--profile", profile_id])
            phases["expansion"] = (
                "ok" if result == 0 else f"failed: exit {result}"
            )
        except Exception as e:
            phases["expansion"] = (
                f"failed: {type(e).__name__}: {e}"
            )
    else:
        phases["expansion"] = "skipped"

    finished = _now()
    record = RunRecord(
        started_at=start.isoformat(),
        finished_at=finished.isoformat(),
        duration_seconds=(finished - start).total_seconds(),
        phases=phases,
        return_code=rc,
    )
    _append_history(profile_id, record)
    return record


# --- Schedule introspection (dashboard reads this) ---------------

def last_run(profile_id: str = "default") -> Optional[dict]:
    history = _load_history(profile_id)
    return history[-1] if history else None


def next_run(
    profile_id: str = "default",
    *,
    schedule_time: str = "02:00",
) -> str:
    """ISO timestamp of the next scheduled run.

    schedule_time is HH:MM in local time. If today's slot has passed,
    we return tomorrow's; otherwise today's.
    """
    try:
        hour_s, minute_s = schedule_time.split(":")
        hour, minute = int(hour_s), int(minute_s)
    except (ValueError, TypeError):
        hour, minute = 2, 0
    now = datetime.now()
    today_slot = now.replace(
        hour=hour, minute=minute, second=0, microsecond=0,
    )
    target = (
        today_slot if today_slot > now else today_slot + timedelta(days=1)
    )
    return target.isoformat()


def schedule_snapshot(
    profile_id: str = "default",
    *,
    schedule_time: str = "02:00",
) -> dict:
    last = last_run(profile_id)
    return {
        "last_run": last,
        "next_run": next_run(profile_id, schedule_time=schedule_time),
        "schedule_time": schedule_time,
    }
