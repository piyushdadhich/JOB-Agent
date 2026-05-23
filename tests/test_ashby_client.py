"""Unit tests for engine.discovery.ashby_client."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.ashby_client import AshbyClient  # noqa: E402


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


def _job(**overrides):
    base = {
        "id": "j1",
        "title": "Senior Project Manager",
        "location": "Toronto, ON",
        "department": "Delivery",
        "applicationUrl": "https://jobs.ashbyhq.com/ashby/j1",
        "publishedAt": "2025-06-01T00:00:00Z",
        "descriptionPlain": "Lead delivery in financial services.",
    }
    base.update(overrides)
    return base


# --- Behavioral ----------------------------------------------------

def test_fetch_returns_records():
    body = {"jobs": [_job(id="a"), _job(id="b", title="PM")]}
    with patch("engine.discovery.ashby_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.ashby_client.time.sleep"):
        client = AshbyClient(slugs=["ashby"], rate_limit=0.0)
        records = list(client.fetch())
    assert len(records) == 2
    assert records[0].title == "Senior Project Manager"
    assert records[1].title == "PM"


def test_404_slug_skipped():
    with patch("engine.discovery.ashby_client.requests.get",
               return_value=_resp(404, {})), \
         patch("engine.discovery.ashby_client.time.sleep"):
        client = AshbyClient(slugs=["nope"], rate_limit=0.0)
        assert list(client.fetch()) == []


def test_compensation_parsed_when_present():
    """currencyCode flows to OpportunityRecord.salary_currency."""
    body = {"jobs": [_job(compensation={"currencyCode": "CAD"})]}
    with patch("engine.discovery.ashby_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.ashby_client.time.sleep"):
        client = AshbyClient(slugs=["a"], rate_limit=0.0)
        records = list(client.fetch())
    assert records[0].salary_currency == "CAD"


def test_compensation_absent_handled():
    """Missing compensation field doesn't crash; fields stay None."""
    body = {"jobs": [_job()]}
    with patch("engine.discovery.ashby_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.ashby_client.time.sleep"):
        client = AshbyClient(slugs=["a"], rate_limit=0.0)
        records = list(client.fetch())
    assert records[0].salary_currency is None
    assert records[0].salary_min is None
    assert records[0].salary_max is None


def test_description_plain_preferred_over_html():
    body = {"jobs": [_job(
        descriptionPlain="Plain version",
        descriptionHtml="<p>HTML version</p>",
    )]}
    with patch("engine.discovery.ashby_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.ashby_client.time.sleep"):
        client = AshbyClient(slugs=["a"], rate_limit=0.0)
        records = list(client.fetch())
    assert records[0].posting_text == "Plain version"


def test_html_description_stripped_when_plain_empty():
    """Empty descriptionPlain falls through to stripped HTML."""
    body = {"jobs": [_job(
        descriptionPlain="",
        descriptionHtml="<p>Lead <b>delivery</b></p>",
    )]}
    with patch("engine.discovery.ashby_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.ashby_client.time.sleep"):
        client = AshbyClient(slugs=["a"], rate_limit=0.0)
        records = list(client.fetch())
    text = records[0].posting_text
    assert "Lead" in text
    assert "delivery" in text
    assert "<p>" not in text and "<b>" not in text


def test_published_at_parsed():
    body = {"jobs": [_job(publishedAt="2025-01-15T12:34:56Z")]}
    with patch("engine.discovery.ashby_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.ashby_client.time.sleep"):
        client = AshbyClient(slugs=["a"], rate_limit=0.0)
        records = list(client.fetch())
    assert records[0].posted_at == datetime(
        2025, 1, 15, 12, 34, 56, tzinfo=timezone.utc,
    )


def test_source_is_ashby_api():
    body = {"jobs": [_job()]}
    with patch("engine.discovery.ashby_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.ashby_client.time.sleep"):
        client = AshbyClient(slugs=["a"], rate_limit=0.0)
        records = list(client.fetch())
    assert records[0].source == "ashby_api"


def test_rate_limit_applied():
    """time.sleep called once per slug at the configured rate."""
    body = {"jobs": []}
    with patch("engine.discovery.ashby_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.ashby_client.time.sleep") as ms:
        client = AshbyClient(slugs=["a", "b"], rate_limit=1.5)
        list(client.fetch())
    assert ms.call_count == 2
    for c in ms.call_args_list:
        assert c.args[0] == 1.5


def test_empty_jobs_returns_nothing():
    body = {"jobs": []}
    with patch("engine.discovery.ashby_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.ashby_client.time.sleep"):
        client = AshbyClient(slugs=["a"], rate_limit=0.0)
        assert list(client.fetch()) == []


def test_include_compensation_param_sent():
    """When include_compensation=True, ?includeCompensation=true is
    passed to the request."""
    body = {"jobs": []}
    with patch("engine.discovery.ashby_client.requests.get",
               return_value=_resp(200, body)) as mock_get, \
         patch("engine.discovery.ashby_client.time.sleep"):
        client = AshbyClient(
            slugs=["a"], rate_limit=0.0, include_compensation=True,
        )
        list(client.fetch())
    _, kwargs = mock_get.call_args
    assert kwargs["params"].get("includeCompensation") == "true"


def test_include_compensation_false_omits_param():
    body = {"jobs": []}
    with patch("engine.discovery.ashby_client.requests.get",
               return_value=_resp(200, body)) as mock_get, \
         patch("engine.discovery.ashby_client.time.sleep"):
        client = AshbyClient(
            slugs=["a"], rate_limit=0.0, include_compensation=False,
        )
        list(client.fetch())
    _, kwargs = mock_get.call_args
    assert "includeCompensation" not in kwargs["params"]
