"""Unit tests for engine.discovery.lever_client.

Mocks requests.get and time.sleep. No live HTTP, no real waits.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.lever_client import (  # noqa: E402
    PAGE_SIZE,
    LeverClient,
)


def _resp(status: int, body):
    """Build a mock requests.Response."""
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    if 200 <= status < 300:
        r.raise_for_status = MagicMock()
    else:
        r.raise_for_status = MagicMock(
            side_effect=Exception(f"HTTP {status}")
        )
    return r


def _posting(**overrides):
    base = {
        "id": "p1",
        "text": "Senior Project Manager",
        "categories": {"location": "Toronto"},
        "description": "<p>Lead delivery</p>",
        "lists": [],
        "hostedUrl": "https://jobs.lever.co/d2l/p1",
        "createdAt": 1735689600000,
    }
    base.update(overrides)
    return base


# --- Behavioral ----------------------------------------------------

def test_fetch_slug_returns_records():
    posts = [_posting(id="p1"), _posting(id="p2", text="PM")]
    with patch("engine.discovery.lever_client.requests.get",
               return_value=_resp(200, posts)), \
         patch("engine.discovery.lever_client.time.sleep"):
        client = LeverClient(slugs=["d2l"], rate_limit=0.0)
        records = list(client.fetch())
    assert len(records) == 2
    assert records[0].title == "Senior Project Manager"
    assert records[1].title == "PM"


def test_pagination_stops_on_empty():
    """Page 1 full, page 2 empty -> 100 records, 2 calls."""
    page1 = [_posting(id=f"p{i}") for i in range(PAGE_SIZE)]
    calls = [_resp(200, page1), _resp(200, [])]
    with patch("engine.discovery.lever_client.requests.get",
               side_effect=calls) as mock_get, \
         patch("engine.discovery.lever_client.time.sleep"):
        client = LeverClient(slugs=["d2l"], rate_limit=0.0)
        records = list(client.fetch())
    assert len(records) == PAGE_SIZE
    assert mock_get.call_count == 2


def test_pagination_stops_on_partial_page():
    """Partial first page (<PAGE_SIZE) ends pagination after 1 call."""
    page = [_posting(id=f"p{i}") for i in range(50)]
    with patch("engine.discovery.lever_client.requests.get",
               return_value=_resp(200, page)) as mock_get, \
         patch("engine.discovery.lever_client.time.sleep"):
        client = LeverClient(slugs=["d2l"], rate_limit=0.0)
        records = list(client.fetch())
    assert len(records) == 50
    assert mock_get.call_count == 1


def test_404_slug_skipped_gracefully():
    with patch("engine.discovery.lever_client.requests.get",
               return_value=_resp(404, [])), \
         patch("engine.discovery.lever_client.time.sleep"):
        client = LeverClient(slugs=["nonexistent"], rate_limit=0.0)
        records = list(client.fetch())
    assert records == []


def test_record_source_is_lever_api():
    with patch("engine.discovery.lever_client.requests.get",
               return_value=_resp(200, [_posting()])), \
         patch("engine.discovery.lever_client.time.sleep"):
        client = LeverClient(slugs=["d2l"], rate_limit=0.0)
        records = list(client.fetch())
    assert records[0].source == "lever_api"


def test_description_html_stripped():
    """get_text("\\n", strip=True) emits one line per tag-content
    span, so '<p>Hello <b>world</b></p>' becomes 'Hello\\nworld'.
    The point of the assertion is that tags themselves are gone."""
    p = _posting(description="<p>Hello <b>world</b></p>")
    with patch("engine.discovery.lever_client.requests.get",
               return_value=_resp(200, [p])), \
         patch("engine.discovery.lever_client.time.sleep"):
        client = LeverClient(slugs=["d2l"], rate_limit=0.0)
        records = list(client.fetch())
    text = records[0].posting_text
    assert "Hello" in text
    assert "world" in text
    assert "<p>" not in text and "<b>" not in text


def test_lists_sections_included_in_posting_text():
    p = _posting(
        description="Main desc",
        lists=[
            {"text": "Responsibilities",
             "content": "<ul><li>Deliver</li></ul>"},
            {"text": "Requirements",
             "content": "<p>5 years</p>"},
        ],
    )
    with patch("engine.discovery.lever_client.requests.get",
               return_value=_resp(200, [p])), \
         patch("engine.discovery.lever_client.time.sleep"):
        client = LeverClient(slugs=["d2l"], rate_limit=0.0)
        records = list(client.fetch())
    text = records[0].posting_text
    assert "Main desc" in text
    assert "Responsibilities" in text
    assert "Deliver" in text
    assert "Requirements" in text
    assert "5 years" in text
    assert "<li>" not in text


def test_hosted_url_used_as_source_url():
    p = _posting(hostedUrl="https://jobs.lever.co/d2l/abc")
    with patch("engine.discovery.lever_client.requests.get",
               return_value=_resp(200, [p])), \
         patch("engine.discovery.lever_client.time.sleep"):
        client = LeverClient(slugs=["d2l"], rate_limit=0.0)
        records = list(client.fetch())
    assert records[0].source_url == "https://jobs.lever.co/d2l/abc"


def test_created_at_parsed_from_milliseconds():
    """1735689600000 ms = 2025-01-01T00:00:00 UTC."""
    p = _posting(createdAt=1735689600000)
    with patch("engine.discovery.lever_client.requests.get",
               return_value=_resp(200, [p])), \
         patch("engine.discovery.lever_client.time.sleep"):
        client = LeverClient(slugs=["d2l"], rate_limit=0.0)
        records = list(client.fetch())
    assert records[0].posted_at == datetime(
        2025, 1, 1, tzinfo=timezone.utc,
    )


def test_rate_limit_applied():
    """time.sleep is called once per slug at the configured rate."""
    with patch("engine.discovery.lever_client.requests.get",
               return_value=_resp(200, [])), \
         patch("engine.discovery.lever_client.time.sleep") as mock_sleep:
        client = LeverClient(slugs=["a", "b"], rate_limit=2.5)
        list(client.fetch())
    assert mock_sleep.call_count == 2
    for call in mock_sleep.call_args_list:
        assert call.args[0] == 2.5
