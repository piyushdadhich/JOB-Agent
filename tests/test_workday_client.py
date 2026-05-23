"""Unit tests for engine.discovery.workday_client.

Mocks requests at the module level — no live HTTP.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery import workday_client as mod  # noqa: E402
from engine.discovery.workday_client import WorkdayClient  # noqa: E402


def _tenant(
    name="TD Bank", tenant="td", wd_server="3", site="TD_Careers",
):
    return {
        "name": name, "tenant": tenant,
        "wd_server": wd_server, "site": site,
    }


def _list_response(jobs, total=None):
    if total is None:
        total = len(jobs)
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"total": total, "jobPostings": jobs}
    resp.raise_for_status = MagicMock()
    return resp


def _detail_response(description="Full description text", start_date=None):
    resp = MagicMock()
    resp.status_code = 200
    info = {"jobDescription": f"<p>{description}</p>"}
    if start_date:
        info["startDate"] = start_date
    resp.json.return_value = {"jobPostingInfo": info}
    return resp


def _job(
    title="Senior PM",
    path="/job/Toronto/Senior-PM_R1",
    loc="Toronto, ON",
):
    return {
        "title": title, "externalPath": path,
        "locationsText": loc, "postedOn": "Posted Yesterday",
        "bulletFields": ["R1"],
    }


# --- happy paths -------------------------------------------------

def test_fetch_tenant_returns_records(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    monkeypatch.setattr(mod.requests, "post", MagicMock(
        return_value=_list_response([_job(), _job(title="BA")])))
    monkeypatch.setattr(mod.requests, "get", MagicMock(
        return_value=_detail_response()))
    client = WorkdayClient([_tenant()], fetch_details=True)
    records = list(client.fetch())
    assert len(records) == 2
    assert records[0].source == "workday"
    assert records[0].employer == "TD Bank"
    assert records[0].title == "Senior PM"
    assert "Full description" in records[0].posting_text


def test_source_is_workday(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    monkeypatch.setattr(mod.requests, "post", MagicMock(
        return_value=_list_response([_job()])))
    client = WorkdayClient([_tenant()], fetch_details=False)
    rec = next(client.fetch())
    assert rec.source == "workday"


def test_source_url_constructed_from_external_path(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    monkeypatch.setattr(mod.requests, "post", MagicMock(
        return_value=_list_response([
            _job(path="/job/Toronto/Senior-PM_R-12345")])))
    client = WorkdayClient([_tenant()], fetch_details=False)
    rec = next(client.fetch())
    assert rec.source_url == (
        "https://td.wd3.myworkdayjobs.com/en-US/TD_Careers"
        "/job/Toronto/Senior-PM_R-12345"
    )


# --- pagination --------------------------------------------------

def test_pagination_with_offset(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    page1 = [_job(title=f"j{i}") for i in range(20)]
    page2 = [_job(title=f"j{i}") for i in range(20, 25)]
    post_mock = MagicMock(side_effect=[
        _list_response(page1, total=25),
        _list_response(page2, total=25),
    ])
    monkeypatch.setattr(mod.requests, "post", post_mock)
    client = WorkdayClient(
        [_tenant()], fetch_details=False, page_size=20,
    )
    records = list(client.fetch())
    assert len(records) == 25
    assert post_mock.call_count == 2
    second_payload = post_mock.call_args_list[1].kwargs["json"]
    assert second_payload["offset"] == 20


def test_pagination_stops_at_total(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    post_mock = MagicMock(return_value=_list_response(
        [_job() for _ in range(5)], total=5))
    monkeypatch.setattr(mod.requests, "post", post_mock)
    client = WorkdayClient(
        [_tenant()], fetch_details=False, page_size=20,
    )
    list(client.fetch())
    assert post_mock.call_count == 1


def test_empty_postings_stops_iteration(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    post_mock = MagicMock(return_value=_list_response([], total=0))
    monkeypatch.setattr(mod.requests, "post", post_mock)
    client = WorkdayClient([_tenant()], fetch_details=False)
    records = list(client.fetch())
    assert records == []
    assert post_mock.call_count == 1


# --- error handling ----------------------------------------------

def test_404_tenant_skipped(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    not_found = MagicMock()
    not_found.status_code = 404
    monkeypatch.setattr(
        mod.requests, "post", MagicMock(return_value=not_found),
    )
    client = WorkdayClient([_tenant()], fetch_details=False)
    records = list(client.fetch())
    assert records == []


def test_graceful_failure_per_tenant(monkeypatch):
    """One bad tenant must not block the others."""
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    good_tenant = _tenant(name="Good", tenant="good", site="g")
    bad_tenant = _tenant(name="Bad", tenant="bad", site="b")

    def fake_post(url, **kw):
        if "bad" in url:
            raise mod.requests.exceptions.ConnectionError("boom")
        return _list_response([_job()])

    monkeypatch.setattr(
        mod.requests, "post", MagicMock(side_effect=fake_post),
    )
    client = WorkdayClient(
        [bad_tenant, good_tenant], fetch_details=False,
    )
    records = list(client.fetch())
    assert len(records) == 1
    assert records[0].employer == "Good"


# --- detail fetching ---------------------------------------------

def test_detail_fetched_when_enabled(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    monkeypatch.setattr(mod.requests, "post", MagicMock(
        return_value=_list_response([_job()])))
    get_mock = MagicMock(return_value=_detail_response(
        description="Lead end-to-end delivery"))
    monkeypatch.setattr(mod.requests, "get", get_mock)
    client = WorkdayClient([_tenant()], fetch_details=True)
    rec = next(client.fetch())
    assert "Lead end-to-end delivery" in rec.posting_text
    assert get_mock.call_count == 1


def test_detail_skipped_when_disabled(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    monkeypatch.setattr(mod.requests, "post", MagicMock(
        return_value=_list_response([_job()])))
    get_mock = MagicMock()
    monkeypatch.setattr(mod.requests, "get", get_mock)
    client = WorkdayClient([_tenant()], fetch_details=False)
    rec = next(client.fetch())
    assert rec.posting_text == "Senior PM"
    get_mock.assert_not_called()


def test_detail_404_returns_none(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    monkeypatch.setattr(mod.requests, "post", MagicMock(
        return_value=_list_response([_job()])))
    not_found = MagicMock()
    not_found.status_code = 404
    monkeypatch.setattr(mod.requests, "get", MagicMock(
        return_value=not_found))
    client = WorkdayClient([_tenant()], fetch_details=True)
    rec = next(client.fetch())
    assert rec.posting_text == "Senior PM"


# --- rate limiting -----------------------------------------------

def test_rate_limit_applied(monkeypatch):
    sleeps = []
    monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(mod.requests, "post", MagicMock(
        return_value=_list_response([_job()])))
    monkeypatch.setattr(mod.requests, "get", MagicMock(
        return_value=_detail_response()))
    client = WorkdayClient(
        [_tenant()], fetch_details=True, rate_limit=2.5,
    )
    list(client.fetch())
    assert sleeps[0] == 2.5
    assert sleeps[1] == 2.5
