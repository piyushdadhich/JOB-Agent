"""Spec 13 TASK 5 — periodic expiry sweep.

`check_postings()` walks every active opportunity, HEADs its
source_url with a strict timeout, and marks 404 / 410 / permanent-
redirect-to-listings-page rows as dismissed. The check runs at the
configured rate (default 1 req/sec) to stay polite to ATS hosts.

Schema-wise we reuse the existing `dismissed` opportunity status
rather than adding an expired column (would require a migration);
the per-run log file under scripts/output/expiry_runs/{date}.log
preserves the "this row was killed by expiry, not by the user"
distinction.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from engine.persistence.tracker import Tracker


# HEAD-request budget. 5s is enough for the ATS hosts that respond
# at all; sites that 10s+ are either flaky enough that retrying
# next sweep is the right answer.
_HEAD_TIMEOUT = 5

# Status codes that mean "this posting is gone for good".
_EXPIRED_STATUSES = {404, 410}

# HTTP method: HEAD is what we want, but some ATSes 405 HEAD —
# those get a follow-up GET to disambiguate.
_HEAD = "HEAD"
_GET = "GET"


@dataclass(frozen=True)
class ExpiryRecord:
    opportunity_id: int
    source_url: str
    outcome: str         # "expired" | "ok" | "skipped" | "error: <msg>"
    http_status: Optional[int] = None


@dataclass(frozen=True)
class ExpiryReport:
    total_checked: int
    expired: list[int]
    errors: list[ExpiryRecord]
    started_at: str
    finished_at: str

    def as_dict(self) -> dict:
        return {
            "total_checked": self.total_checked,
            "expired": list(self.expired),
            "error_count": len(self.errors),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


_ACTIVE_OPPORTUNITIES_SQL = """\
SELECT id, source_url
FROM opportunities
WHERE status NOT IN ('dismissed', 'pursued')
ORDER BY id
"""


def _check_one(
    url: str,
    *,
    session: Optional[requests.Session] = None,
    timeout: int = _HEAD_TIMEOUT,
) -> tuple[str, Optional[int]]:
    """Return (outcome, http_status) for a single URL."""
    s = session or requests
    try:
        resp = s.request(
            _HEAD, url, timeout=timeout, allow_redirects=True,
        )
    except (requests.RequestException, OSError) as e:
        return f"error: {type(e).__name__}", None

    code = resp.status_code
    if code == 405:
        # HEAD not allowed — retry with GET (some Workday tenants
        # 405 HEAD but accept GET).
        try:
            resp = s.request(
                _GET, url, timeout=timeout, allow_redirects=True,
                stream=True,
            )
            code = resp.status_code
            resp.close()
        except (requests.RequestException, OSError) as e:
            return f"error: {type(e).__name__}", None

    if code in _EXPIRED_STATUSES:
        return "expired", code
    if 200 <= code < 400:
        return "ok", code
    # 5xx is transient — leave the row alone, retry next sweep.
    return f"error: HTTP {code}", code


def check_postings(
    tracker: Tracker,
    *,
    rate_seconds: float = 1.0,
    limit: Optional[int] = None,
    session: Optional[requests.Session] = None,
) -> ExpiryReport:
    started = datetime.now(timezone.utc).isoformat()
    rows = tracker._query_all(_ACTIVE_OPPORTUNITIES_SQL)
    if limit is not None:
        rows = list(rows)[:limit]

    expired_ids: list[int] = []
    errors: list[ExpiryRecord] = []
    last_request: float = 0.0

    for r in rows:
        # Rate limit.
        if rate_seconds > 0:
            elapsed = time.monotonic() - last_request
            if elapsed < rate_seconds:
                time.sleep(rate_seconds - elapsed)
        last_request = time.monotonic()

        opp_id = int(r["id"])
        url = r["source_url"]
        outcome, code = _check_one(url, session=session)

        record = ExpiryRecord(
            opportunity_id=opp_id, source_url=url,
            outcome=outcome, http_status=code,
        )
        if outcome == "expired":
            tracker.update_opportunity_status(opp_id, "dismissed")
            expired_ids.append(opp_id)
        elif outcome.startswith("error"):
            errors.append(record)

    finished = datetime.now(timezone.utc).isoformat()
    return ExpiryReport(
        total_checked=len(rows),
        expired=expired_ids,
        errors=errors,
        started_at=started,
        finished_at=finished,
    )


def write_run_log(report: ExpiryReport, *, project_root: Path) -> Path:
    """Append a JSON-line for this run to scripts/output/expiry_runs/."""
    import json
    log_dir = project_root / "scripts" / "output" / "expiry_runs"
    log_dir.mkdir(parents=True, exist_ok=True)
    day = report.finished_at[:10]  # YYYY-MM-DD
    path = log_dir / f"{day}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(report.as_dict()) + "\n")
    return path
