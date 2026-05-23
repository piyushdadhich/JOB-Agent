"""Shared daily call counter for Google AI Studio (Gemma 4 31B).

One free-tier budget (1,500 requests/day) is split across two
consumers, in priority order:

  1. Resume / cover-letter generation — user-initiated, on demand.
     ALWAYS runs. It never checks the budget; it only LOGS each
     call so the evaluator can see what's left.
  2. Job evaluation — the automated overnight batch. It checks the
     remaining budget before each call and stops when exhausted.

Both consumers append to the same `data/{profile}/cloud_eval_usage.jsonl`
file. Because every call lands in one log, `calls_used_today`
naturally reflects evaluation + resume + cover-letter combined —
the evaluator subtracting `calls_used_today` from the daily limit
is exactly "1,500 minus everything already spent today", which
includes resume generation.

This module is the canonical API. The evaluator's GemmaCloudClient
keeps its own richer per-call logging for the audit trail; this
module is the lightweight shared counter the dashboard and budget
checks read.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DAILY_LIMIT = 1500


def usage_path(profile_id: str) -> Path:
    """Per-profile usage log — shared with the evaluator."""
    return PROJECT_ROOT / "data" / profile_id / "cloud_eval_usage.jsonl"


def _resolve(profile_id: str, path: Optional[Path]) -> Path:
    return Path(path) if path is not None else usage_path(profile_id)


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _iter_today(log: Path):
    """Yield parsed entries whose timestamp falls on the current UTC
    calendar day. Malformed / blank lines are skipped."""
    if not log.exists():
        return
    today = _today_utc()
    for line in log.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = entry.get("timestamp", "")
        if isinstance(ts, str) and ts.startswith(today):
            yield entry


def calls_used_today(
    profile_id: str, *, path: Optional[Path] = None,
) -> int:
    """Total API calls logged today — evaluation + resume + cover
    letter. Every consumer writes to one log, so this is the whole
    day's spend."""
    log = _resolve(profile_id, path)
    return sum(1 for _ in _iter_today(log))


def calls_remaining_today(
    profile_id: str, *, path: Optional[Path] = None,
) -> int:
    return max(0, DAILY_LIMIT - calls_used_today(profile_id, path=path))


def usage_breakdown_today(
    profile_id: str, *, path: Optional[Path] = None,
) -> dict[str, int]:
    """Today's calls grouped by call_type. Entries without a
    `call_type` field are evaluator rows — bucketed as 'eval'."""
    log = _resolve(profile_id, path)
    out: dict[str, int] = {}
    for entry in _iter_today(log):
        ct = entry.get("call_type") or "eval"
        out[ct] = out.get(ct, 0) + 1
    return out


def can_eval(
    profile_id: str, *, path: Optional[Path] = None,
) -> bool:
    """Whether the evaluator still has budget. Resume generation does
    NOT call this — it always runs."""
    return calls_remaining_today(profile_id, path=path) > 0


def log_call(
    profile_id: str,
    call_type: str,
    *,
    path: Optional[Path] = None,
    **fields,
) -> None:
    """Append one call to the shared usage log.

    `call_type` is 'eval', 'resume', or 'cover_letter'. Extra fields
    (model, opportunity_id, token counts, status) are stored as-is.
    """
    log = _resolve(profile_id, path)
    log.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "call_type": call_type,
        **fields,
    }
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
