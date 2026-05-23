"""Unit tests for engine.discovery.recruitee_client."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.recruitee_client import RecruiteeClient  # noqa: E402


def _resp(status: int, body):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    if 200 <= status < 300:
        r.raise_for_status = MagicMock()
    else:
        r.raise_for_status = MagicMock(
            side_effect=Exception(f"HTTP {status}"),
        )
    return r


def _offer(**overrides):
    base = {
        "id": 1,
        "title": "Senior Project Manager",
        "location": "Toronto",
        "department": "Delivery",
        "description": "<p>Lead delivery</p>",
        "requirements": "<p>10 years</p>",
        "careers_url": "https://co.recruitee.com/o/1",
        "published_at": "2025-06-01T00:00:00Z",
        "min_salary": 90000,
        "max_salary": 120000,
        "salary_currency": "CAD",
    }
    base.update(overrides)
    return base


# --- Behavioral ----------------------------------------------------

def test_fetch_returns_records():
    body = {"offers": [_offer(id=1), _offer(id=2, title="PM")]}
    with patch("engine.discovery.recruitee_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.recruitee_client.time.sleep"):
        client = RecruiteeClient(slugs=["co"], rate_limit=0.0)
        records = list(client.fetch())
    assert len(records) == 2
    assert records[0].title == "Senior Project Manager"
    assert records[1].title == "PM"


def test_404_slug_skipped():
    with patch("engine.discovery.recruitee_client.requests.get",
               return_value=_resp(404, {})), \
         patch("engine.discovery.recruitee_client.time.sleep"):
        client = RecruiteeClient(slugs=["nope"], rate_limit=0.0)
        assert list(client.fetch()) == []


def test_salary_fields_populated():
    body = {"offers": [_offer(
        min_salary=80000, max_salary=110000, salary_currency="USD",
    )]}
    with patch("engine.discovery.recruitee_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.recruitee_client.time.sleep"):
        client = RecruiteeClient(slugs=["co"], rate_limit=0.0)
        records = list(client.fetch())
    r = records[0]
    assert r.salary_min == 80000
    assert r.salary_max == 110000
    assert r.salary_currency == "USD"


def test_description_and_requirements_combined():
    body = {"offers": [_offer(
        description="<p>Lead delivery in fin services.</p>",
        requirements="<p>10 years senior PM experience.</p>",
    )]}
    with patch("engine.discovery.recruitee_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.recruitee_client.time.sleep"):
        client = RecruiteeClient(slugs=["co"], rate_limit=0.0)
        records = list(client.fetch())
    text = records[0].posting_text
    assert "Lead delivery in fin services." in text
    assert "10 years senior PM experience." in text


def test_html_stripped():
    body = {"offers": [_offer(
        description="<p>Lead <b>delivery</b></p>",
        requirements="<ul><li>Senior</li></ul>",
    )]}
    with patch("engine.discovery.recruitee_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.recruitee_client.time.sleep"):
        client = RecruiteeClient(slugs=["co"], rate_limit=0.0)
        records = list(client.fetch())
    text = records[0].posting_text
    assert "Lead" in text
    assert "delivery" in text
    assert "Senior" in text
    assert "<p>" not in text and "<li>" not in text


def test_published_at_parsed():
    body = {"offers": [_offer(published_at="2025-03-15T08:30:00Z")]}
    with patch("engine.discovery.recruitee_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.recruitee_client.time.sleep"):
        client = RecruiteeClient(slugs=["co"], rate_limit=0.0)
        records = list(client.fetch())
    assert records[0].posted_at == datetime(
        2025, 3, 15, 8, 30, tzinfo=timezone.utc,
    )


def test_source_is_recruitee_api():
    body = {"offers": [_offer()]}
    with patch("engine.discovery.recruitee_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.recruitee_client.time.sleep"):
        client = RecruiteeClient(slugs=["co"], rate_limit=0.0)
        records = list(client.fetch())
    assert records[0].source == "recruitee_api"


def test_empty_offers_returns_nothing():
    body = {"offers": []}
    with patch("engine.discovery.recruitee_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.recruitee_client.time.sleep"):
        client = RecruiteeClient(slugs=["co"], rate_limit=0.0)
        assert list(client.fetch()) == []


def test_rate_limit_applied():
    body = {"offers": []}
    with patch("engine.discovery.recruitee_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.recruitee_client.time.sleep") as ms:
        client = RecruiteeClient(slugs=["a", "b"], rate_limit=1.5)
        list(client.fetch())
    assert ms.call_count == 2
    for c in ms.call_args_list:
        assert c.args[0] == 1.5
