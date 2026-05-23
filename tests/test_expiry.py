"""Spec 13 TASK 5 — expiry-checker tests."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from engine.expiry import checker
from engine.persistence.tracker import Tracker


@pytest.fixture
def tracker(tmp_path):
    t = Tracker(profile_id="default", db_path=tmp_path / "t.db")
    yield t
    t.close()


def _seed_opportunity(tracker, url: str, status: str = "new") -> int:
    cid = tracker.upsert_company("Co")
    opp_id, _ = tracker.insert_opportunity(
        company_id=cid, source="t",
        source_url=url, title="T",
    )
    if status != "new":
        tracker.update_opportunity_status(opp_id, status)
    return opp_id


def _resp(status_code: int) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    return r


def test_check_one_returns_expired_on_404():
    s = MagicMock()
    s.request.return_value = _resp(404)
    outcome, code = checker._check_one("https://x.example", session=s)
    assert outcome == "expired"
    assert code == 404


def test_check_one_returns_ok_on_200():
    s = MagicMock()
    s.request.return_value = _resp(200)
    outcome, code = checker._check_one("https://x.example", session=s)
    assert outcome == "ok"
    assert code == 200


def test_check_one_retries_with_get_on_405():
    s = MagicMock()
    head_resp = _resp(405)
    get_resp = _resp(200)
    s.request.side_effect = [head_resp, get_resp]
    outcome, code = checker._check_one("https://x.example", session=s)
    assert outcome == "ok"
    assert code == 200
    assert s.request.call_count == 2


def test_check_one_handles_network_error():
    import requests as r
    s = MagicMock()
    s.request.side_effect = r.ConnectionError("dns")
    outcome, code = checker._check_one("https://x.example", session=s)
    assert outcome.startswith("error")
    assert code is None


def test_check_one_returns_error_on_5xx():
    s = MagicMock()
    s.request.return_value = _resp(503)
    outcome, code = checker._check_one("https://x.example", session=s)
    assert outcome.startswith("error")
    # 5xx is transient — leaves the row alone (caller doesn't
    # mark it dismissed).
    assert code == 503


def test_check_postings_marks_404s_as_dismissed(tracker):
    a = _seed_opportunity(tracker, "https://example.com/a")
    b = _seed_opportunity(tracker, "https://example.com/b")
    c = _seed_opportunity(tracker, "https://example.com/c")

    session = MagicMock()
    # a → 404, b → 200, c → 503
    responses = {
        "https://example.com/a": _resp(404),
        "https://example.com/b": _resp(200),
        "https://example.com/c": _resp(503),
    }
    session.request.side_effect = (
        lambda method, url, **kw: responses[url]
    )
    report = checker.check_postings(
        tracker, rate_seconds=0, session=session,
    )
    assert report.total_checked == 3
    assert report.expired == [a]
    assert len(report.errors) == 1
    assert report.errors[0].opportunity_id == c

    # Verify DB-side: a is dismissed, b/c are not.
    assert tracker.get_opportunity_by_id(a)["status"] == "dismissed"
    assert tracker.get_opportunity_by_id(b)["status"] == "new"
    assert tracker.get_opportunity_by_id(c)["status"] == "new"


def test_check_postings_skips_dismissed_rows(tracker):
    _seed_opportunity(tracker, "https://example.com/d", status="dismissed")
    session = MagicMock()
    session.request.return_value = _resp(404)
    report = checker.check_postings(
        tracker, rate_seconds=0, session=session,
    )
    # The pre-dismissed row is excluded from the sweep entirely;
    # session.request should never have been called.
    assert report.total_checked == 0
    session.request.assert_not_called()


def test_check_postings_respects_limit(tracker):
    for i in range(5):
        _seed_opportunity(tracker, f"https://example.com/{i}")
    session = MagicMock()
    session.request.return_value = _resp(200)
    report = checker.check_postings(
        tracker, rate_seconds=0, limit=2, session=session,
    )
    assert report.total_checked == 2


def test_write_run_log_appends_jsonl(tmp_path):
    report = checker.ExpiryReport(
        total_checked=10, expired=[1, 2], errors=[],
        started_at="2026-05-19T00:00:00+00:00",
        finished_at="2026-05-19T00:01:00+00:00",
    )
    path = checker.write_run_log(report, project_root=tmp_path)
    assert path.exists()
    assert path.suffix == ".jsonl"
    content = path.read_text(encoding="utf-8")
    assert '"total_checked": 10' in content
    # Second write appends.
    checker.write_run_log(report, project_root=tmp_path)
    assert path.read_text(encoding="utf-8").count("\n") == 2
