"""Tests for scripts/run_email_monitor.py.

Exercises the run() entry point with injected client + tracker
factories so no real Gmail or SQLite work happens.
"""
from __future__ import annotations

import importlib.util
import io
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.base import OpportunityRecord  # noqa: E402


def _load_module():
    """Dynamically load scripts/run_email_monitor.py — scripts/ isn't
    a package so we can't `import scripts.run_email_monitor`."""
    spec = importlib.util.spec_from_file_location(
        "run_email_monitor",
        PROJECT_ROOT / "scripts" / "run_email_monitor.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cli():
    return _load_module()


def _record(title="T", url="u", employer="E"):
    return OpportunityRecord(
        source="email_monitor:test",
        source_url=url,
        employer=employer,
        title=title,
        location="",
        posting_text="",
        date_discovered=datetime.now(timezone.utc),
    )


def _fake_client_factory(records):
    """Return a factory that yields the given records from .fetch()."""
    def factory(**kwargs):
        client = MagicMock()
        client.fetch.return_value = iter(records)
        client._kwargs = kwargs
        return client
    return factory


def test_dry_run_does_not_open_tracker(cli):
    records = [_record("PM at Acme"), _record("BA at Beta")]
    out = io.StringIO()
    tracker_factory = MagicMock(side_effect=AssertionError("tracker opened!"))

    counts = cli.run(
        profile_id="default",
        dry_run=True,
        client_factory=_fake_client_factory(records),
        tracker_factory=tracker_factory,
        out=out,
    )
    assert counts["yielded"] == 2
    assert counts["persisted"] == 0
    tracker_factory.assert_not_called()
    text = out.getvalue()
    assert "PM at Acme" in text
    assert "BA at Beta" in text


def test_persists_when_not_dry_run(cli, monkeypatch):
    records = [_record("PM at Acme"), _record("BA at Beta")]
    persisted: list = []

    def fake_persist(tracker, record):
        persisted.append(record)
        return 42 + len(persisted), True  # opp_id, was_new=True

    monkeypatch.setattr(cli, "persist_record", fake_persist)

    fake_tracker = MagicMock()
    tracker_factory = MagicMock(return_value=fake_tracker)

    counts = cli.run(
        profile_id="default",
        dry_run=False,
        client_factory=_fake_client_factory(records),
        tracker_factory=tracker_factory,
        out=io.StringIO(),
    )

    assert counts["yielded"] == 2
    assert counts["persisted"] == 2
    assert counts["new"] == 2
    assert counts["duplicates"] == 0
    tracker_factory.assert_called_once_with("default")
    assert len(persisted) == 2


def test_counts_duplicates(cli, monkeypatch):
    records = [_record("a"), _record("b"), _record("c")]
    state = {"i": 0}

    def fake_persist(tracker, record):
        state["i"] += 1
        # was_new=True only on the first call
        return state["i"], state["i"] == 1

    monkeypatch.setattr(cli, "persist_record", fake_persist)

    counts = cli.run(
        profile_id="default",
        dry_run=False,
        client_factory=_fake_client_factory(records),
        tracker_factory=MagicMock(return_value=MagicMock()),
        out=io.StringIO(),
    )
    assert counts["new"] == 1
    assert counts["duplicates"] == 2


def test_skipped_incomplete_increments(cli, monkeypatch):
    records = [_record("a")]

    def raising_persist(tracker, record):
        from engine.persistence.opportunities import IncompleteRecordError
        raise IncompleteRecordError("missing employer")

    monkeypatch.setattr(cli, "persist_record", raising_persist)

    counts = cli.run(
        profile_id="default",
        dry_run=False,
        client_factory=_fake_client_factory(records),
        tracker_factory=MagicMock(return_value=MagicMock()),
        out=io.StringIO(),
    )
    assert counts["skipped_incomplete"] == 1
    assert counts["persisted"] == 0


def test_passes_mark_processed_flag_through(cli):
    factory = _fake_client_factory([])
    cli.run(
        profile_id="default",
        dry_run=False,
        mark_processed=True,
        client_factory=factory,
        tracker_factory=MagicMock(return_value=MagicMock()),
        out=io.StringIO(),
    )
    # The most-recent client built captured the kwargs.
    # _build_client calls factory once, so we need access through the
    # MagicMock chain. Re-do with an inspectable factory:
    last_kwargs = {}

    def inspect_factory(**kwargs):
        last_kwargs.update(kwargs)
        m = MagicMock()
        m.fetch.return_value = iter([])
        return m

    cli.run(
        profile_id="default",
        dry_run=False,
        mark_processed=True,
        client_factory=inspect_factory,
        tracker_factory=MagicMock(return_value=MagicMock()),
        out=io.StringIO(),
    )
    assert last_kwargs["enable_mark_processed"] is True


def test_main_with_dry_run_argv(cli):
    """End-to-end: main() parses argv, calls run(), returns 0."""
    records = [_record("PM at Acme")]

    # Need to monkey the symbol main() will look up. Easiest: replace
    # EmailMonitorClient on the module so _build_client uses our fake.
    orig = cli.EmailMonitorClient

    def fake_client(**kwargs):
        m = MagicMock()
        m.fetch.return_value = iter(records)
        return m

    cli.EmailMonitorClient = fake_client
    try:
        rc = cli.main([
            "--profile", "default", "--dry-run", "--hours", "12",
        ])
        assert rc == 0
    finally:
        cli.EmailMonitorClient = orig
