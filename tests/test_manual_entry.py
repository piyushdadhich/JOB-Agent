"""Unit tests for scripts/manual_entry.py."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.manual_entry import (  # noqa: E402
    POSTING_TEXT_TRUNCATE,
    _guess_employer,
    build_record,
    fetch_page,
    main,
)


def _resp(status: int, html: str):
    r = MagicMock()
    r.status_code = status
    r.text = html
    if 200 <= status < 300:
        r.raise_for_status = MagicMock()
    else:
        r.raise_for_status = MagicMock(
            side_effect=Exception(f"HTTP {status}"),
        )
    return r


# --- _guess_employer ----------------------------------------------

def test_guess_employer_strips_common_subdomains():
    assert _guess_employer(
        "https://www.acme.com/jobs/123", "",
    ) == "Acme"
    assert _guess_employer(
        "https://jobs.acme.com/123", "",
    ) == "Acme"
    assert _guess_employer(
        "https://careers.acme.com/role", "",
    ) == "Acme"


# --- fetch_page ---------------------------------------------------

def test_fetch_page_truncates_text_to_8000():
    huge = "<html><body>" + ("x" * 20000) + "</body></html>"
    with patch("scripts.manual_entry.requests.get",
               return_value=_resp(200, huge)):
        title, text = fetch_page("https://example.com/job/1")
    assert len(text) == POSTING_TEXT_TRUNCATE


def test_fetch_page_failure_returns_empty():
    """Network failure must not crash; returns ('', '')."""
    with patch("scripts.manual_entry.requests.get",
               side_effect=ConnectionError("down")):
        title, text = fetch_page("https://example.com/x")
    assert title is None
    assert text == ""


# --- build_record + persistence -----------------------------------

def test_record_persisted_with_source_manual_entry():
    """End-to-end main() path: --url + --employer + --title +
    --location all provided non-interactively. Result has
    source='manual_entry' and was persisted via persist_record."""
    fake_tracker = MagicMock()
    persist_mock = MagicMock(return_value=(42, True))
    with patch("scripts.manual_entry.requests.get",
               return_value=_resp(
                   200, "<html><title>X</title><body>Y</body></html>",
               )), \
         patch("scripts.manual_entry.Tracker",
               return_value=fake_tracker), \
         patch("scripts.manual_entry.persist_record",
               persist_mock):
        rc = main([
            "--profile", "default",
            "--url", "https://acme.com/job/1",
            "--employer", "Acme",
            "--title", "Senior PM",
            "--location", "Toronto",
        ])
    assert rc == 0
    # persist_record was called once with the record
    persist_mock.assert_called_once()
    rec = persist_mock.call_args.args[1]
    assert rec.source == "manual_entry"
    assert rec.employer == "Acme"
    assert rec.title == "Senior PM"
    assert rec.location == "Toronto"
    assert rec.source_url == "https://acme.com/job/1"
    fake_tracker.close.assert_called_once()


def test_dedup_reports_existing_id():
    """When persist_record returns was_new=False, main prints the
    existing id (we just check exit code 0 - dedup is non-fatal)."""
    fake_tracker = MagicMock()
    persist_mock = MagicMock(return_value=(99, False))
    with patch("scripts.manual_entry.requests.get",
               return_value=_resp(200, "<html><title>X</title></html>")), \
         patch("scripts.manual_entry.Tracker",
               return_value=fake_tracker), \
         patch("scripts.manual_entry.persist_record",
               persist_mock):
        rc = main([
            "--profile", "default",
            "--url", "https://acme.com/job/1",
            "--employer", "Acme",
            "--title", "PM",
            "--location", "Toronto",
        ])
    assert rc == 0
    persist_mock.assert_called_once()


def test_url_required():
    """No --url, no interactive input -> exit 1."""
    with patch("builtins.input", return_value=""):
        rc = main(["--profile", "default"])
    assert rc == 1


def test_employer_from_argument_overrides_guess():
    """--employer wins over the URL-derived guess."""
    persist_mock = MagicMock(return_value=(1, True))
    with patch("scripts.manual_entry.requests.get",
               return_value=_resp(200, "<html><title>X</title></html>")), \
         patch("scripts.manual_entry.Tracker", return_value=MagicMock()), \
         patch("scripts.manual_entry.persist_record", persist_mock):
        main([
            "--profile", "default",
            "--url", "https://www.acme.com/job/1",
            "--employer", "Custom Co",
            "--title", "PM",
            "--location", "Toronto",
        ])
    rec = persist_mock.call_args.args[1]
    # 'Acme' would be the URL-derived guess; --employer takes precedence
    assert rec.employer == "Custom Co"


def test_title_from_argument_overrides_page_title():
    persist_mock = MagicMock(return_value=(1, True))
    with patch("scripts.manual_entry.requests.get",
               return_value=_resp(
                   200, "<html><title>Page Title</title></html>",
               )), \
         patch("scripts.manual_entry.Tracker", return_value=MagicMock()), \
         patch("scripts.manual_entry.persist_record", persist_mock):
        main([
            "--profile", "default",
            "--url", "https://acme.com/job/1",
            "--employer", "Acme",
            "--title", "My Custom Title",
            "--location", "Toronto",
        ])
    rec = persist_mock.call_args.args[1]
    assert rec.title == "My Custom Title"


def test_page_text_truncated_to_8000():
    """The OpportunityRecord.posting_text must be truncated to
    8000 chars before persist."""
    huge = "<html><body>" + ("x" * 20000) + "</body></html>"
    persist_mock = MagicMock(return_value=(1, True))
    with patch("scripts.manual_entry.requests.get",
               return_value=_resp(200, huge)), \
         patch("scripts.manual_entry.Tracker", return_value=MagicMock()), \
         patch("scripts.manual_entry.persist_record", persist_mock):
        main([
            "--profile", "default",
            "--url", "https://acme.com/job/1",
            "--employer", "Acme",
            "--title", "PM",
            "--location", "Toronto",
        ])
    rec = persist_mock.call_args.args[1]
    assert len(rec.posting_text) == POSTING_TEXT_TRUNCATE


def test_build_record_source_is_manual_entry():
    rec = build_record(
        url="https://acme.com/job/1",
        employer="Acme",
        title="PM",
        location="Toronto",
        page_text="Lead delivery.",
    )
    assert rec.source == "manual_entry"
    assert rec.source_url == "https://acme.com/job/1"
    assert rec.posting_text == "Lead delivery."
