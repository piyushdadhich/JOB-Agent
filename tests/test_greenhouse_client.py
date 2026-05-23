"""Unit tests for engine.discovery.greenhouse_client.

Mocks requests.get and time.sleep. No live HTTP, no real waits.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.greenhouse_client import (  # noqa: E402
    GREENHOUSE_API_BASE,
    GreenhouseClient,
)


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
        "id": 123,
        "title": "Senior Project Manager",
        "location": {"name": "Toronto, ON"},
        "absolute_url": "https://boards.greenhouse.io/d2l/jobs/123",
        "updated_at": "2026-05-01T12:34:56-04:00",
        "content": "Lead delivery in financial services.",
    }
    base.update(overrides)
    return base


# --- Behavioral ----------------------------------------------------

def test_fetch_returns_records():
    body = {"jobs": [_job(id=1), _job(id=2, title="PM")]}
    with patch("engine.discovery.greenhouse_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.greenhouse_client.time.sleep"):
        records = list(GreenhouseClient(["d2l"], 0.0).fetch())
    assert len(records) == 2
    assert records[0].title == "Senior Project Manager"
    assert records[1].title == "PM"


def test_fetch_iterates_multiple_slugs():
    body_a = {"jobs": [_job(id=1)]}
    body_b = {"jobs": [_job(id=2, title="X")]}
    with patch("engine.discovery.greenhouse_client.requests.get",
               side_effect=[_resp(200, body_a), _resp(200, body_b)]) \
                   as mock_get, \
         patch("engine.discovery.greenhouse_client.time.sleep"):
        records = list(GreenhouseClient(["a", "b"], 0.0).fetch())
    assert len(records) == 2
    assert mock_get.call_count == 2


def test_404_slug_skipped_gracefully():
    with patch("engine.discovery.greenhouse_client.requests.get",
               return_value=_resp(404, {})), \
         patch("engine.discovery.greenhouse_client.time.sleep"):
        assert list(GreenhouseClient(["nope"], 0.0).fetch()) == []


def test_500_logs_and_continues_to_next_slug():
    """Exception on slug 1 must not prevent slug 2 from fetching."""
    body_b = {"jobs": [_job(id=2)]}
    with patch("engine.discovery.greenhouse_client.requests.get",
               side_effect=[_resp(500, {}), _resp(200, body_b)]), \
         patch("engine.discovery.greenhouse_client.time.sleep"):
        records = list(GreenhouseClient(["broken", "ok"], 0.0).fetch())
    assert len(records) == 1


def test_rate_limit_applied():
    body = {"jobs": []}
    with patch("engine.discovery.greenhouse_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.greenhouse_client.time.sleep") as ms:
        list(GreenhouseClient(["a", "b"], 1.5).fetch())
    assert ms.call_count == 2
    for c in ms.call_args_list:
        assert c.args[0] == 1.5


def test_record_title_mapped():
    body = {"jobs": [_job(title="Delivery Manager")]}
    with patch("engine.discovery.greenhouse_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.greenhouse_client.time.sleep"):
        records = list(GreenhouseClient(["a"], 0.0).fetch())
    assert records[0].title == "Delivery Manager"


def test_record_employer_is_slug():
    body = {"jobs": [_job()]}
    with patch("engine.discovery.greenhouse_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.greenhouse_client.time.sleep"):
        records = list(GreenhouseClient(["d2l"], 0.0).fetch())
    assert records[0].employer == "d2l"


def test_record_location_mapped():
    body = {"jobs": [_job(location={"name": "Calgary, AB"})]}
    with patch("engine.discovery.greenhouse_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.greenhouse_client.time.sleep"):
        records = list(GreenhouseClient(["a"], 0.0).fetch())
    assert records[0].location == "Calgary, AB"


def test_record_location_missing_handled():
    body = {"jobs": [_job(location=None)]}
    with patch("engine.discovery.greenhouse_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.greenhouse_client.time.sleep"):
        records = list(GreenhouseClient(["a"], 0.0).fetch())
    assert records[0].location == ""


def test_record_source_url_is_absolute_url():
    body = {"jobs": [
        _job(absolute_url="https://boards.greenhouse.io/a/jobs/42")
    ]}
    with patch("engine.discovery.greenhouse_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.greenhouse_client.time.sleep"):
        records = list(GreenhouseClient(["a"], 0.0).fetch())
    assert records[0].source_url == (
        "https://boards.greenhouse.io/a/jobs/42"
    )


def test_record_source_id_is_job_id_string():
    body = {"jobs": [_job(id=987654321)]}
    with patch("engine.discovery.greenhouse_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.greenhouse_client.time.sleep"):
        records = list(GreenhouseClient(["a"], 0.0).fetch())
    assert records[0].source_id == "987654321"


def test_record_posting_text_strips_html():
    """Greenhouse `content` is HTML-escaped; unescape then strip tags."""
    body = {"jobs": [_job(
        content="&lt;p&gt;Lead &lt;b&gt;delivery&lt;/b&gt;&lt;/p&gt;",
    )]}
    with patch("engine.discovery.greenhouse_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.greenhouse_client.time.sleep"):
        records = list(GreenhouseClient(["a"], 0.0).fetch())
    text = records[0].posting_text
    assert "Lead" in text
    assert "delivery" in text
    assert "<p>" not in text and "<b>" not in text


def test_record_posting_text_unescapes_entities():
    """Real Greenhouse content is double-encoded HTML-in-JSON: an
    `&` in the source HTML arrives on the wire as `&amp;amp;`.
    html.unescape decodes once to `&amp;`, then BeautifulSoup's
    get_text decodes the well-formed entity to `&`.
    """
    body = {"jobs": [_job(
        content="&lt;p&gt;Profit &amp;amp; Loss&lt;/p&gt;",
    )]}
    with patch("engine.discovery.greenhouse_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.greenhouse_client.time.sleep"):
        records = list(GreenhouseClient(["a"], 0.0).fetch())
    assert "Profit & Loss" in records[0].posting_text


def test_record_posted_at_parsed_iso():
    body = {"jobs": [_job(updated_at="2026-01-15T12:34:56-04:00")]}
    with patch("engine.discovery.greenhouse_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.greenhouse_client.time.sleep"):
        records = list(GreenhouseClient(["a"], 0.0).fetch())
    posted = records[0].posted_at
    assert posted is not None
    assert posted.year == 2026 and posted.month == 1 and posted.day == 15


def test_record_source_is_greenhouse_api():
    body = {"jobs": [_job()]}
    with patch("engine.discovery.greenhouse_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.greenhouse_client.time.sleep"):
        records = list(GreenhouseClient(["a"], 0.0).fetch())
    assert records[0].source == "greenhouse_api"


def test_record_raw_payload_captured():
    """Full job dict stashed in raw_payload for replay/debug."""
    job = _job(id=42, custom_field="kept")
    body = {"jobs": [job]}
    with patch("engine.discovery.greenhouse_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.greenhouse_client.time.sleep"):
        records = list(GreenhouseClient(["a"], 0.0).fetch())
    assert records[0].raw_payload["custom_field"] == "kept"
    assert records[0].raw_payload["id"] == 42


def test_record_search_context_carries_slug():
    body = {"jobs": [_job()]}
    with patch("engine.discovery.greenhouse_client.requests.get",
               return_value=_resp(200, body)), \
         patch("engine.discovery.greenhouse_client.time.sleep"):
        records = list(GreenhouseClient(["d2l"], 0.0).fetch())
    assert records[0].search_context == {"greenhouse_slug": "d2l"}


def test_content_true_param_sent():
    """Client requests ?content=true so descriptions are inlined."""
    body = {"jobs": []}
    with patch("engine.discovery.greenhouse_client.requests.get",
               return_value=_resp(200, body)) as mock_get, \
         patch("engine.discovery.greenhouse_client.time.sleep"):
        list(GreenhouseClient(["a"], 0.0).fetch())
    _, kwargs = mock_get.call_args
    assert kwargs["params"].get("content") == "true"


# --- Health check --------------------------------------------------

def test_health_check_pings_first_slug_metadata():
    """health_check hits /v1/boards/{slug} (metadata), not /jobs."""
    with patch("engine.discovery.greenhouse_client.requests.get",
               return_value=_resp(200, {"name": "D2L"})) as mock_get:
        result = GreenhouseClient(
            ["d2l", "other"], 0.0,
        ).health_check()
    assert result.reachable is True
    called_url = mock_get.call_args.args[0]
    assert called_url.endswith("/boards/d2l")
    assert "/jobs" not in called_url


def test_health_check_returns_ok_on_200():
    with patch("engine.discovery.greenhouse_client.requests.get",
               return_value=_resp(200, {})):
        result = GreenhouseClient(["d2l"], 0.0).health_check()
    assert result.reachable is True
    assert result.last_error is None
    assert result.source == "greenhouse_api"


def test_health_check_returns_degraded_on_non_200():
    with patch("engine.discovery.greenhouse_client.requests.get",
               return_value=_resp(503, {})):
        result = GreenhouseClient(["d2l"], 0.0).health_check()
    assert result.reachable is False
    assert "503" in (result.last_error or "")
