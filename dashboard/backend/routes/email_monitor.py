"""Spec F1 TASK 3 — Email Monitor API.

Surfaces email_monitor source status to the dashboard. Read-mostly:
status/recent/setup-status report what's been parsed from the
opportunities table (source='email_monitor'); check-now triggers
the standalone run_email_monitor cycle inline.

Configured-state derivation: presence of data/{profile}/gmail_token.json
means OAuth has completed once. credentials_exist further reports
whether the OAuth client secret has been downloaded. The setup_guide
endpoint returns the README-equivalent markdown for users who haven't
finished setup.

No new schema is required — last_check / parsed counts are read from
existing opportunities rows. parser_stats is keyed by parser name
heuristically from raw_payload (set by each EmailParser on yield)
and falls back to {} when the column is absent.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from dashboard.backend.deps import get_profile_id, get_tracker
from engine.persistence.tracker import Tracker

router = APIRouter(prefix="/api/email-monitor", tags=["email-monitor"])

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# Recent-emails endpoint cap. Spec says "last 5" — a small fixed
# limit avoids fetching the full email_monitor history when the
# source has been running for weeks.
_RECENT_LIMIT = 25


# --- Path helpers ------------------------------------------------

def _token_path(profile_id: str) -> Path:
    return PROJECT_ROOT / "data" / profile_id / "gmail_token.json"


def _credentials_path(profile_id: str) -> Path:
    return PROJECT_ROOT / "data" / profile_id / "gmail_credentials.json"


def _token_valid(profile_id: str) -> bool:
    """Token presence + JSON parseable. Doesn't verify expiry — that
    requires a Gmail roundtrip and runs every status poll, which is
    too noisy. A failed scope check surfaces on the next check-now."""
    path = _token_path(profile_id)
    if not path.exists():
        return False
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return True
    except (json.JSONDecodeError, OSError):
        return False


# --- Response models ---------------------------------------------

class ParserStat(BaseModel):
    matched: int
    parsed: int


class EmailMonitorStatus(BaseModel):
    configured: bool
    last_check: Optional[str]
    emails_checked_24h: int
    postings_parsed_24h: int
    parser_stats: dict[str, ParserStat]


class RecentEmail(BaseModel):
    email_subject: str
    sender: str
    received_at: Optional[str]
    parser_used: str
    postings_extracted: int
    posting_titles: list[str]


class CheckNowResponse(BaseModel):
    status: str
    new: int = 0
    persisted: int = 0
    error: Optional[str] = None


class SetupStatus(BaseModel):
    credentials_exist: bool
    token_valid: bool
    setup_guide_url: str


class SetupGuide(BaseModel):
    markdown: str


# --- Status endpoints --------------------------------------------

@router.get("/status", response_model=EmailMonitorStatus)
def get_status(
    tracker: Tracker = Depends(get_tracker),
    profile_id: str = Depends(get_profile_id),
) -> EmailMonitorStatus:
    configured = _token_valid(profile_id)
    last_check_row = tracker._query_one(
        "SELECT MAX(last_seen_at) AS last_seen "
        "FROM opportunities WHERE source = 'email_monitor'",
    )
    last_check = (
        last_check_row["last_seen"]
        if last_check_row and last_check_row["last_seen"]
        else None
    )

    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=24)
    ).isoformat()
    parsed_24h_row = tracker._query_one(
        "SELECT COUNT(*) AS c FROM opportunities "
        "WHERE source = 'email_monitor' AND date_discovered >= ?",
        (cutoff,),
    )
    postings_parsed_24h = (
        int(parsed_24h_row["c"]) if parsed_24h_row else 0
    )

    # parser_stats: best-effort breakdown by parser name carried in
    # raw_payload['parser']. Falls back to a single 'recruiter' bucket
    # for rows lacking that hint (back-compat with pre-Spec-F1
    # email_monitor rows).
    rows = tracker._query_all(
        "SELECT raw_payload FROM opportunities "
        "WHERE source = 'email_monitor' AND date_discovered >= ?",
        (cutoff,),
    )
    parser_buckets: dict[str, int] = {}
    for r in rows:
        raw = r["raw_payload"]
        parser_name = "unknown"
        if raw:
            try:
                payload = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(payload, dict):
                    parser_name = (
                        payload.get("parser")
                        or payload.get("parser_name")
                        or "unknown"
                    )
            except (json.JSONDecodeError, TypeError):
                pass
        parser_buckets[parser_name] = parser_buckets.get(parser_name, 0) + 1
    parser_stats = {
        name: ParserStat(matched=count, parsed=count)
        for name, count in parser_buckets.items()
    }

    # emails_checked_24h is not tracked at source level — each
    # parsed posting came from at least one email, so the parsed
    # count is the conservative floor.
    return EmailMonitorStatus(
        configured=configured,
        last_check=last_check,
        emails_checked_24h=postings_parsed_24h,
        postings_parsed_24h=postings_parsed_24h,
        parser_stats=parser_stats,
    )


@router.get("/recent", response_model=list[RecentEmail])
def get_recent(
    tracker: Tracker = Depends(get_tracker),
) -> list[RecentEmail]:
    rows = tracker._query_all(
        "SELECT title, source_url, date_discovered, raw_payload "
        "FROM opportunities "
        "WHERE source = 'email_monitor' "
        "ORDER BY date_discovered DESC LIMIT ?",
        (_RECENT_LIMIT,),
    )
    # Group by email subject (carried in raw_payload['email_subject'])
    # so multi-posting emails fold into one row.
    grouped: dict[str, dict] = {}
    for r in rows:
        raw = r["raw_payload"]
        payload: dict = {}
        if raw:
            try:
                payload = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(payload, dict):
                    payload = {}
            except (json.JSONDecodeError, TypeError):
                payload = {}
        key = (
            payload.get("email_message_id")
            or payload.get("email_subject")
            or r["source_url"]
        )
        bucket = grouped.setdefault(key, {
            "email_subject": payload.get("email_subject", r["title"]),
            "sender": (
                payload.get("from_email")
                or payload.get("sender")
                or "unknown"
            ),
            "received_at": (
                payload.get("email_date") or r["date_discovered"]
            ),
            "parser_used": (
                payload.get("parser")
                or payload.get("parser_name")
                or "unknown"
            ),
            "posting_titles": [],
        })
        bucket["posting_titles"].append(r["title"])
    out = [
        RecentEmail(
            email_subject=b["email_subject"],
            sender=b["sender"],
            received_at=b["received_at"],
            parser_used=b["parser_used"],
            postings_extracted=len(b["posting_titles"]),
            posting_titles=b["posting_titles"],
        )
        for b in grouped.values()
    ]
    out.sort(
        key=lambda x: x.received_at or "", reverse=True,
    )
    return out[:5]


@router.post("/check-now", response_model=CheckNowResponse)
def check_now(
    tracker: Tracker = Depends(get_tracker),
    profile_id: str = Depends(get_profile_id),
) -> CheckNowResponse:
    """Trigger one email_monitor cycle inline. Surfaces setup
    failures (missing token, invalid credentials) as a status:error
    payload rather than a 500 so the dashboard can render an
    actionable message."""
    if not _token_valid(profile_id):
        return CheckNowResponse(
            status="error",
            error=(
                "Gmail not configured. Run scripts/gmail_auth.py "
                f"--profile {profile_id} first."
            ),
        )
    try:
        from scripts.run_email_monitor import run as run_email_monitor
    except Exception as e:  # pragma: no cover — import-time errors
        return CheckNowResponse(
            status="error", error=f"Failed to load runner: {e}",
        )
    try:
        counts = run_email_monitor(
            profile_id=profile_id,
            hours=24,
            dry_run=False,
            mark_processed=False,
        )
    except Exception as e:
        return CheckNowResponse(status="error", error=str(e))
    return CheckNowResponse(
        status="started",
        new=int(counts.get("new", 0)),
        persisted=int(counts.get("persisted", 0)),
    )


@router.get("/setup-status", response_model=SetupStatus)
def get_setup_status(
    profile_id: str = Depends(get_profile_id),
) -> SetupStatus:
    return SetupStatus(
        credentials_exist=_credentials_path(profile_id).exists(),
        token_valid=_token_valid(profile_id),
        setup_guide_url="/api/email-monitor/setup-guide",
    )


_SETUP_GUIDE_MD = """# Email Monitor setup

The Email Monitor discovers postings from recruiter emails,
LinkedIn job alerts, and newsletter digests. Setup is one-time
per profile.

## 1. Enable the Gmail API

1. Open the Google Cloud Console: https://console.cloud.google.com/
2. Create (or pick) a project for the job agent.
3. Enable the **Gmail API** for that project.
4. Configure the OAuth consent screen — choose **External**, add
   yourself as a test user. Scopes: `gmail.readonly` is enough
   for read-only fetching.
5. Create an **OAuth 2.0 Client ID** of type **Desktop app**.
6. Download the JSON, save it as
   `data/{profile_id}/gmail_credentials.json`.

## 2. Authorize the agent

```
python scripts/gmail_auth.py --profile default
```

This opens a browser, you grant access, and the refresh token is
saved to `data/{profile_id}/gmail_token.json` (gitignored).

## 3. Configure recruiter / newsletter sources

Edit `config/profiles/{profile_id}.yaml` and add:

```yaml
email_monitor:
  hours_lookback: 24
  recruiter_domains:
    - hays.com
    - randstad.com
  newsletter_senders:
    - newsletter@indeed.com
```

## 4. Run

```
python scripts/run_email_monitor.py --profile default --dry-run
```

Confirm parsed records look reasonable, then drop `--dry-run` to
persist.

## Troubleshooting

- **`gmail_token.json` not found** — step 2 didn't complete. Re-run
  `gmail_auth.py`.
- **Token expired after 7 days** — Google issues short-lived
  refresh tokens to OAuth consent screens in `Testing` mode.
  Publish the consent screen (Production) for stable tokens, or
  re-run `gmail_auth.py` weekly.
- **No postings discovered** — verify `recruiter_domains` and
  `newsletter_senders` match the actual `From:` addresses on the
  emails you expect to parse.
"""


@router.get("/setup-guide", response_model=SetupGuide)
def get_setup_guide() -> SetupGuide:
    return SetupGuide(markdown=_SETUP_GUIDE_MD)
