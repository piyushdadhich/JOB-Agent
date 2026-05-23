"""Email Monitor source.

Reads emails from the past N hours via the Gmail API, runs each
through a parser registry. First matching parser wins.

Default behavior is read-only (gmail.readonly scope). Optional
opt-in `enable_mark_processed=True` upgrades to gmail.modify scope
so successfully-parsed emails get a 'JobAgent-Processed' label and
are excluded from subsequent fetches — saves Gmail API quota across
daily runs. Opt-in requires re-running `scripts/gmail_auth.py
--modify` to grant the broader scope.

Lazy-imports the Gmail SDK so a missing google-auth package
doesn't break unrelated discovery sources. If gmail_token.json
is missing, fetch() yields nothing and logs a warning.
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Iterator, Optional, Protocol, runtime_checkable

from engine.discovery.base import OpportunityRecord, Source

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


@runtime_checkable
class EmailParser(Protocol):
    name: str
    def matches(self, email: dict) -> bool: ...
    def parse(self, email: dict) -> list[OpportunityRecord]: ...


def _decode_body(data: str) -> str:
    """Base64url-decode a Gmail body part."""
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data).decode(
            "utf-8", errors="replace",
        )
    except Exception:
        return ""


def _walk_parts(payload: dict) -> Iterator[dict]:
    """Recursively yield every payload part."""
    yield payload
    for part in payload.get("parts") or []:
        yield from _walk_parts(part)


def _extract_bodies(payload: dict) -> tuple[str, str]:
    """Return (text_body, html_body) from a Gmail message payload."""
    text_body = ""
    html_body = ""
    for part in _walk_parts(payload):
        mime = part.get("mimeType", "")
        body = (part.get("body") or {}).get("data") or ""
        if not body:
            continue
        if mime == "text/plain" and not text_body:
            text_body = _decode_body(body)
        elif mime == "text/html" and not html_body:
            html_body = _decode_body(body)
    return text_body, html_body


def _normalize_message(msg: dict) -> dict:
    """Turn a raw Gmail message into the dict shape parsers expect."""
    headers = {
        h["name"].lower(): h["value"]
        for h in (msg.get("payload", {}).get("headers") or [])
    }
    from_raw = headers.get("from", "")
    _, from_email = parseaddr(from_raw)
    from_email = from_email.lower()
    from_domain = from_email.split("@", 1)[1] if "@" in from_email else ""
    date_raw = headers.get("date", "")
    try:
        date_dt = parsedate_to_datetime(date_raw) if date_raw else None
    except (TypeError, ValueError):
        date_dt = None

    text_body, html_body = _extract_bodies(msg.get("payload") or {})

    return {
        "id": msg.get("id", ""),
        "thread_id": msg.get("threadId", ""),
        "from_raw": from_raw,
        "from_email": from_email,
        "from_domain": from_domain,
        "subject": headers.get("subject", ""),
        "date": date_dt,
        "text_body": text_body,
        "html_body": html_body,
    }


class EmailMonitorClient(Source):
    """Gmail-backed source. Fetches recent emails and runs parsers."""

    name = "email_monitor"

    def __init__(
        self,
        profile_id: str,
        hours_lookback: int = 24,
        recruiter_domains: Optional[list[str]] = None,
        newsletter_senders: Optional[list[str]] = None,
        search_query_override: Optional[str] = None,
        parsers: Optional[list[EmailParser]] = None,
        enable_mark_processed: bool = False,
        processed_label_name: str = "JobAgent-Processed",
        service=None,  # injectable for tests
    ):
        self.profile_id = profile_id
        self.hours_lookback = int(hours_lookback)
        self.recruiter_domains = [
            d.lower() for d in (recruiter_domains or [])
        ]
        self.newsletter_senders = [
            s.lower() for s in (newsletter_senders or [])
        ]
        self.search_query_override = search_query_override
        self.enable_mark_processed = bool(enable_mark_processed)
        self.processed_label_name = processed_label_name
        self._service = service
        self._processed_label_id: Optional[str] = None

        if parsers is None:
            from engine.discovery.email_parsers import default_parsers
            parsers = default_parsers(
                recruiter_domains=self.recruiter_domains,
                newsletter_senders=self.newsletter_senders,
            )
        self.parsers = list(parsers)

    def _get_service(self):
        if self._service is not None:
            return self._service
        # Lazy-import the Gmail SDK so missing google-auth doesn't
        # break other sources.
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials

        token_path = (
            _project_root() / "data" / self.profile_id
            / "gmail_token.json"
        )
        if not token_path.exists():
            raise FileNotFoundError(
                f"Gmail token not found at {token_path}. "
                f"Run scripts/gmail_auth.py first."
            )
        # gmail.modify is required to add the 'JobAgent-Processed'
        # label; falls back to gmail.readonly when the feature is off.
        scope = (
            "https://www.googleapis.com/auth/gmail.modify"
            if self.enable_mark_processed
            else "https://www.googleapis.com/auth/gmail.readonly"
        )
        creds = Credentials.from_authorized_user_file(
            str(token_path), [scope],
        )
        self._service = build("gmail", "v1", credentials=creds)
        return self._service

    def _build_query(self) -> str:
        if self.search_query_override:
            return self.search_query_override
        cutoff = datetime.now(timezone.utc) - timedelta(
            hours=self.hours_lookback,
        )
        # Gmail search syntax uses `after:YYYY/MM/DD` — day granularity.
        # That's fine since we re-run daily and dedupe in persistence.
        q = f"after:{cutoff.strftime('%Y/%m/%d')}"
        if self.enable_mark_processed:
            # Quote the label name so spaces/hyphens are tolerated.
            q += f' -label:"{self.processed_label_name}"'
        return q

    def _list_message_ids(self, service) -> list[str]:
        query = self._build_query()
        ids: list[str] = []
        page_token: Optional[str] = None
        while True:
            kwargs = {"userId": "me", "q": query, "maxResults": 100}
            if page_token:
                kwargs["pageToken"] = page_token
            resp = service.users().messages().list(**kwargs).execute()
            ids.extend(m["id"] for m in resp.get("messages") or [])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return ids

    def _get_message(self, service, msg_id: str) -> dict:
        return service.users().messages().get(
            userId="me", id=msg_id, format="full",
        ).execute()

    def _ensure_processed_label(self, service) -> Optional[str]:
        """Return the ID of the processed label, creating it if missing.
        Returns None if the labels API fails (mark_processed becomes a
        no-op for this run — fail-soft so a Gmail outage doesn't block
        record yielding)."""
        if self._processed_label_id is not None:
            return self._processed_label_id
        try:
            resp = service.users().labels().list(
                userId="me",
            ).execute()
        except Exception as e:
            logger.warning(
                "Email monitor: labels.list failed (%s); "
                "mark_processed disabled for this run", e,
            )
            return None
        for lbl in resp.get("labels") or []:
            if lbl.get("name") == self.processed_label_name:
                self._processed_label_id = lbl.get("id")
                return self._processed_label_id
        try:
            created = service.users().labels().create(
                userId="me",
                body={
                    "name": self.processed_label_name,
                    "labelListVisibility": "labelHide",
                    "messageListVisibility": "hide",
                },
            ).execute()
        except Exception as e:
            logger.warning(
                "Email monitor: labels.create(%s) failed: %s",
                self.processed_label_name, e,
            )
            return None
        self._processed_label_id = created.get("id")
        return self._processed_label_id

    def mark_processed(self, service, msg_id: str, label_id: str) -> None:
        """Add the processed label to a Gmail message. Best-effort: any
        Gmail error is logged and swallowed (the record was already
        yielded — re-marking will happen on the next run)."""
        try:
            service.users().messages().modify(
                userId="me",
                id=msg_id,
                body={"addLabelIds": [label_id]},
            ).execute()
        except Exception as e:
            logger.warning(
                "Email monitor: mark_processed failed for %s: %s",
                msg_id, e,
            )

    def fetch(self) -> Iterator[OpportunityRecord]:
        try:
            service = self._get_service()
        except FileNotFoundError as e:
            logger.warning("Email monitor: %s", e)
            return
        except Exception as e:
            logger.warning(
                "Email monitor: failed to build service: %s", e,
            )
            return

        processed_label_id: Optional[str] = None
        if self.enable_mark_processed:
            processed_label_id = self._ensure_processed_label(service)

        try:
            ids = self._list_message_ids(service)
        except Exception as e:
            logger.warning("Email monitor: list failed: %s", e)
            return

        logger.info(
            "Email monitor: %d messages in lookback window (%dh)",
            len(ids), self.hours_lookback,
        )
        for msg_id in ids:
            try:
                raw = self._get_message(service, msg_id)
            except Exception as e:
                logger.warning(
                    "Email monitor: get %s failed: %s", msg_id, e,
                )
                continue
            email = _normalize_message(raw)

            for parser in self.parsers:
                try:
                    if not parser.matches(email):
                        continue
                except Exception as e:
                    logger.warning(
                        "Email parser %s match check failed: %s",
                        parser.name, e,
                    )
                    continue
                try:
                    recs = list(parser.parse(email))
                except Exception as e:
                    logger.warning(
                        "Email parser %s failed on %s: %s",
                        parser.name, msg_id, e,
                    )
                    recs = []
                for r in recs:
                    yield r
                # Only mark as processed when a parser actually yielded
                # something — emails that matched no parser today might
                # match a parser we add tomorrow, so leave them un-labeled.
                if recs and self.enable_mark_processed and processed_label_id:
                    self.mark_processed(service, msg_id, processed_label_id)
                break  # first matching parser wins
