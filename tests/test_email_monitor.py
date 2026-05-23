"""Unit tests for engine.discovery.email_monitor.

Mocks the Gmail API service via the `service` injection seam. No
live HTTP, no token file required.
"""
from __future__ import annotations

import base64
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.email_monitor import (  # noqa: E402
    EmailMonitorClient,
    _normalize_message,
)
from engine.discovery.base import OpportunityRecord  # noqa: E402


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii")


def _gmail_message(
    msg_id="m1",
    sender="John Smith <john@example.com>",
    subject="Hello",
    date_dt=None,
    text_body="hi there",
    html_body="<p>hi there</p>",
):
    if date_dt is None:
        date_dt = datetime.now(timezone.utc)
    parts = []
    if text_body is not None:
        parts.append({
            "mimeType": "text/plain",
            "body": {"data": _b64(text_body)},
        })
    if html_body is not None:
        parts.append({
            "mimeType": "text/html",
            "body": {"data": _b64(html_body)},
        })
    return {
        "id": msg_id,
        "threadId": "t1",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": format_datetime(date_dt)},
            ],
            "parts": parts,
        },
    }


def _stub_service(messages):
    """Build a mock Gmail service that returns the given messages."""
    svc = MagicMock()
    svc.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{"id": m["id"]} for m in messages],
    }

    def get_exec(userId=None, id=None, format=None):  # noqa: A002
        for m in messages:
            if m["id"] == id:
                rv = MagicMock()
                rv.execute.return_value = m
                return rv
        rv = MagicMock()
        rv.execute.side_effect = Exception(f"unknown id {id}")
        return rv

    svc.users.return_value.messages.return_value.get.side_effect = get_exec
    return svc


# --- skip path -----------------------------------------------------

def test_skip_when_no_token(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    client = EmailMonitorClient(profile_id="nope")
    # No service passed in; no token file under tmp_path either.
    records = list(client.fetch())
    assert records == []
    assert any(
        "Gmail token not found" in r.message for r in caplog.records
    )


# --- query-builder -------------------------------------------------

def test_query_uses_hours_lookback():
    client = EmailMonitorClient(
        profile_id="x", hours_lookback=24,
        service=_stub_service([]),
    )
    q = client._build_query()
    assert q.startswith("after:")
    # Format check: after:YYYY/MM/DD
    assert len(q.split(":", 1)[1]) == 10


def test_query_override_wins():
    client = EmailMonitorClient(
        profile_id="x", search_query_override="from:noone@nowhere.com",
        service=_stub_service([]),
    )
    assert client._build_query() == "from:noone@nowhere.com"


# --- iteration / dispatch -----------------------------------------

def test_iterates_and_dispatches_to_parser():
    msgs = [_gmail_message(subject="Senior PM role at Acme")]
    svc = _stub_service(msgs)

    matched = []

    class FakeParser:
        name = "fake"
        def matches(self, email):
            matched.append(email["subject"])
            return True
        def parse(self, email):
            return [OpportunityRecord(
                source="fake", source_url="x", employer="A",
                title=email["subject"], location="", posting_text="",
                date_discovered=datetime.now(timezone.utc),
            )]

    client = EmailMonitorClient(
        profile_id="x", service=svc, parsers=[FakeParser()],
    )
    records = list(client.fetch())
    assert len(records) == 1
    assert records[0].title == "Senior PM role at Acme"
    assert matched == ["Senior PM role at Acme"]


def test_first_matching_parser_wins():
    msgs = [_gmail_message()]
    svc = _stub_service(msgs)

    class P1:
        name = "p1"
        def matches(self, _): return True
        def parse(self, _): return [OpportunityRecord(
            source="p1", source_url="", employer="", title="from p1",
            location="", posting_text="",
            date_discovered=datetime.now(timezone.utc),
        )]

    class P2:
        name = "p2"
        called = False
        def matches(self, _):
            P2.called = True
            return True
        def parse(self, _): return []

    client = EmailMonitorClient(
        profile_id="x", service=svc, parsers=[P1(), P2()],
    )
    records = list(client.fetch())
    assert len(records) == 1
    assert records[0].title == "from p1"
    # P2.matches should never have been called
    assert P2.called is False


def test_parser_exception_does_not_abort_batch(caplog):
    msgs = [
        _gmail_message(msg_id="m1", subject="boom"),
        _gmail_message(msg_id="m2", subject="ok"),
    ]
    svc = _stub_service(msgs)

    class FlakyParser:
        name = "flaky"
        def matches(self, _): return True
        def parse(self, email):
            if email["subject"] == "boom":
                raise RuntimeError("kaboom")
            return [OpportunityRecord(
                source="ok", source_url="", employer="",
                title=email["subject"], location="", posting_text="",
                date_discovered=datetime.now(timezone.utc),
            )]

    client = EmailMonitorClient(
        profile_id="x", service=svc, parsers=[FlakyParser()],
    )
    records = list(client.fetch())
    assert len(records) == 1
    assert records[0].title == "ok"


# --- _normalize_message --------------------------------------------

def test_normalize_message_extracts_fields():
    raw = _gmail_message(
        msg_id="abc",
        sender='"Jane Doe" <jane@hays.com>',
        subject="Hi",
        text_body="hello world",
    )
    norm = _normalize_message(raw)
    assert norm["id"] == "abc"
    assert norm["from_email"] == "jane@hays.com"
    assert norm["from_domain"] == "hays.com"
    assert norm["subject"] == "Hi"
    assert "hello world" in norm["text_body"]
    assert norm["date"] is not None


# --- mark_processed -------------------------------------------------

def _stub_service_with_labels(messages, existing_labels=None):
    """Stub service that also supports labels.list/create + messages.modify.
    `existing_labels` is a list of {'id', 'name'} dicts."""
    svc = _stub_service(messages)
    labels = list(existing_labels or [])
    modify_calls: list[dict] = []
    create_calls: list[dict] = []

    def labels_list_exec(userId=None):
        rv = MagicMock()
        rv.execute.return_value = {"labels": labels}
        return rv

    def labels_create_exec(userId=None, body=None):
        new = {"id": f"Label_{len(labels) + 1}", "name": body["name"]}
        labels.append(new)
        create_calls.append(body)
        rv = MagicMock()
        rv.execute.return_value = new
        return rv

    def modify_exec(userId=None, id=None, body=None):  # noqa: A002
        modify_calls.append({"id": id, "body": body})
        rv = MagicMock()
        rv.execute.return_value = {}
        return rv

    svc.users.return_value.labels.return_value.list.side_effect = (
        labels_list_exec
    )
    svc.users.return_value.labels.return_value.create.side_effect = (
        labels_create_exec
    )
    svc.users.return_value.messages.return_value.modify.side_effect = (
        modify_exec
    )
    # Expose call recorders on the mock for assertions.
    svc._modify_calls = modify_calls
    svc._create_calls = create_calls
    return svc


def test_query_excludes_processed_label_when_enabled():
    svc = _stub_service_with_labels([])
    client = EmailMonitorClient(
        profile_id="x",
        service=svc,
        enable_mark_processed=True,
        parsers=[],
    )
    q = client._build_query()
    assert '-label:"JobAgent-Processed"' in q


def test_query_omits_label_filter_when_disabled():
    client = EmailMonitorClient(
        profile_id="x",
        service=_stub_service([]),
        enable_mark_processed=False,
        parsers=[],
    )
    assert "-label:" not in client._build_query()


def test_marks_processed_after_yielding_records():
    msgs = [_gmail_message(msg_id="m1")]
    svc = _stub_service_with_labels(
        msgs, existing_labels=[{"id": "Label_42", "name": "JobAgent-Processed"}],
    )

    class AlwaysYieldsParser:
        name = "always"
        def matches(self, _): return True
        def parse(self, _):
            return [OpportunityRecord(
                source="t", source_url="u", employer="E",
                title="T", location="", posting_text="",
                date_discovered=datetime.now(timezone.utc),
            )]

    client = EmailMonitorClient(
        profile_id="x",
        service=svc,
        enable_mark_processed=True,
        parsers=[AlwaysYieldsParser()],
    )
    records = list(client.fetch())
    assert len(records) == 1
    assert len(svc._modify_calls) == 1
    assert svc._modify_calls[0]["id"] == "m1"
    assert svc._modify_calls[0]["body"] == {"addLabelIds": ["Label_42"]}


def test_does_not_mark_when_no_parser_matches():
    msgs = [_gmail_message(msg_id="m1")]
    svc = _stub_service_with_labels(
        msgs, existing_labels=[{"id": "Label_42", "name": "JobAgent-Processed"}],
    )

    class NeverMatchesParser:
        name = "never"
        def matches(self, _): return False
        def parse(self, _): return []

    client = EmailMonitorClient(
        profile_id="x",
        service=svc,
        enable_mark_processed=True,
        parsers=[NeverMatchesParser()],
    )
    records = list(client.fetch())
    assert records == []
    assert svc._modify_calls == []


def test_does_not_mark_when_feature_disabled():
    msgs = [_gmail_message(msg_id="m1")]
    svc = _stub_service_with_labels(msgs)

    class AlwaysYieldsParser:
        name = "always"
        def matches(self, _): return True
        def parse(self, _):
            return [OpportunityRecord(
                source="t", source_url="u", employer="E",
                title="T", location="", posting_text="",
                date_discovered=datetime.now(timezone.utc),
            )]

    client = EmailMonitorClient(
        profile_id="x",
        service=svc,
        enable_mark_processed=False,
        parsers=[AlwaysYieldsParser()],
    )
    list(client.fetch())
    assert svc._modify_calls == []


def test_ensure_processed_label_creates_when_missing():
    svc = _stub_service_with_labels([], existing_labels=[])
    client = EmailMonitorClient(
        profile_id="x",
        service=svc,
        enable_mark_processed=True,
        parsers=[],
    )
    label_id = client._ensure_processed_label(svc)
    assert label_id is not None
    assert len(svc._create_calls) == 1
    assert svc._create_calls[0]["name"] == "JobAgent-Processed"


def test_ensure_processed_label_reuses_existing():
    svc = _stub_service_with_labels(
        [], existing_labels=[
            {"id": "Label_Existing", "name": "JobAgent-Processed"},
        ],
    )
    client = EmailMonitorClient(
        profile_id="x",
        service=svc,
        enable_mark_processed=True,
        parsers=[],
    )
    label_id = client._ensure_processed_label(svc)
    assert label_id == "Label_Existing"
    assert svc._create_calls == []  # no new label created


def test_is_a_source_subclass():
    """EmailMonitorClient inherits from Source ABC."""
    from engine.discovery.base import Source
    assert issubclass(EmailMonitorClient, Source)
