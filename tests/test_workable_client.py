"""Unit tests for engine.discovery.workable_client."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.workable_client import WorkableClient  # noqa: E402


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
        "shortcode": "SPM01",
        "city": "Toronto",
        "state": "ON",
        "country": "Canada",
        "department": "Delivery",
        "url": "https://apply.workable.com/co/j/SPM01",
        "shortDescription": "Lead delivery.",
    }
    base.update(overrides)
    return base


def _detail(**overrides):
    base = {
        "description": "<p>Full description</p>",
        "requirements": "<ul><li>5 years</li></ul>",
        "benefits": "<p>Health <b>plan</b></p>",
    }
    base.update(overrides)
    return base


# --- Behavioral ----------------------------------------------------

def test_fetch_returns_records():
    list_body = {"jobs": [_job(id="j1"), _job(id="j2", title="PM")]}
    responses = [_resp(200, list_body), _resp(200, _detail()),
                 _resp(200, _detail())]
    with patch("engine.discovery.workable_client.requests.get",
               side_effect=responses), \
         patch("engine.discovery.workable_client.time.sleep"):
        client = WorkableClient(slugs=["co"], rate_limit=0.0)
        records = list(client.fetch())
    assert len(records) == 2
    assert records[0].title == "Senior Project Manager"
    assert records[1].title == "PM"


def test_404_slug_skipped():
    with patch("engine.discovery.workable_client.requests.get",
               return_value=_resp(404, {})), \
         patch("engine.discovery.workable_client.time.sleep"):
        client = WorkableClient(slugs=["nope"], rate_limit=0.0)
        assert list(client.fetch()) == []


def test_detail_fetched_when_enabled():
    """fetch_details=True triggers a per-job detail GET."""
    list_body = {"jobs": [_job()]}
    responses = [_resp(200, list_body), _resp(200, _detail())]
    with patch("engine.discovery.workable_client.requests.get",
               side_effect=responses) as mock_get, \
         patch("engine.discovery.workable_client.time.sleep"):
        client = WorkableClient(
            slugs=["co"], rate_limit=0.0, fetch_details=True,
        )
        list(client.fetch())
    # 2 calls: list + detail
    assert mock_get.call_count == 2


def test_detail_skipped_when_disabled():
    """fetch_details=False -> only the list call is made."""
    list_body = {"jobs": [_job()]}
    with patch("engine.discovery.workable_client.requests.get",
               return_value=_resp(200, list_body)) as mock_get, \
         patch("engine.discovery.workable_client.time.sleep"):
        client = WorkableClient(
            slugs=["co"], rate_limit=0.0, fetch_details=False,
        )
        records = list(client.fetch())
    assert mock_get.call_count == 1
    # falls back to shortDescription
    assert records[0].posting_text == "Lead delivery."


def test_detail_404_returns_empty():
    """A 404 on the detail endpoint must not crash the slug; the
    record falls back to shortDescription."""
    list_body = {"jobs": [_job(shortDescription="Short text")]}
    responses = [_resp(200, list_body), _resp(404, {})]
    with patch("engine.discovery.workable_client.requests.get",
               side_effect=responses), \
         patch("engine.discovery.workable_client.time.sleep"):
        client = WorkableClient(slugs=["co"], rate_limit=0.0)
        records = list(client.fetch())
    assert records[0].posting_text == "Short text"


def test_description_combined_from_detail():
    list_body = {"jobs": [_job()]}
    detail = _detail(
        description="<p>Lead delivery.</p>",
        requirements="<p>10 years.</p>",
        benefits="<p>Pension.</p>",
    )
    responses = [_resp(200, list_body), _resp(200, detail)]
    with patch("engine.discovery.workable_client.requests.get",
               side_effect=responses), \
         patch("engine.discovery.workable_client.time.sleep"):
        client = WorkableClient(slugs=["co"], rate_limit=0.0)
        records = list(client.fetch())
    text = records[0].posting_text
    assert "Lead delivery." in text
    assert "10 years." in text
    assert "Pension." in text


def test_short_description_fallback_when_detail_empty():
    """If detail returns empty {}, fallback to shortDescription."""
    list_body = {"jobs": [_job(shortDescription="Quick blurb")]}
    responses = [_resp(200, list_body), _resp(200, {})]
    with patch("engine.discovery.workable_client.requests.get",
               side_effect=responses), \
         patch("engine.discovery.workable_client.time.sleep"):
        client = WorkableClient(slugs=["co"], rate_limit=0.0)
        records = list(client.fetch())
    assert records[0].posting_text == "Quick blurb"


def test_location_from_city_state_country():
    list_body = {"jobs": [_job(city="Toronto", state="ON",
                                country="Canada")]}
    responses = [_resp(200, list_body), _resp(200, _detail())]
    with patch("engine.discovery.workable_client.requests.get",
               side_effect=responses), \
         patch("engine.discovery.workable_client.time.sleep"):
        client = WorkableClient(slugs=["co"], rate_limit=0.0)
        records = list(client.fetch())
    assert records[0].location == "Toronto, ON, Canada"


def test_location_omits_empty_components():
    list_body = {"jobs": [_job(city="Toronto", state="", country="Canada")]}
    responses = [_resp(200, list_body), _resp(200, _detail())]
    with patch("engine.discovery.workable_client.requests.get",
               side_effect=responses), \
         patch("engine.discovery.workable_client.time.sleep"):
        client = WorkableClient(slugs=["co"], rate_limit=0.0)
        records = list(client.fetch())
    assert records[0].location == "Toronto, Canada"


def test_source_is_workable_api():
    list_body = {"jobs": [_job()]}
    responses = [_resp(200, list_body), _resp(200, _detail())]
    with patch("engine.discovery.workable_client.requests.get",
               side_effect=responses), \
         patch("engine.discovery.workable_client.time.sleep"):
        client = WorkableClient(slugs=["co"], rate_limit=0.0)
        records = list(client.fetch())
    assert records[0].source == "workable_api"


def test_rate_limit_on_list_and_detail():
    """1 slug + 2 jobs with details = 3 sleeps total."""
    list_body = {"jobs": [_job(id="a"), _job(id="b")]}
    responses = [_resp(200, list_body), _resp(200, _detail()),
                 _resp(200, _detail())]
    with patch("engine.discovery.workable_client.requests.get",
               side_effect=responses), \
         patch("engine.discovery.workable_client.time.sleep") as ms:
        client = WorkableClient(slugs=["co"], rate_limit=0.5)
        list(client.fetch())
    assert ms.call_count == 3
    for c in ms.call_args_list:
        assert c.args[0] == 0.5


def test_empty_jobs_returns_nothing():
    list_body = {"jobs": []}
    with patch("engine.discovery.workable_client.requests.get",
               return_value=_resp(200, list_body)), \
         patch("engine.discovery.workable_client.time.sleep"):
        client = WorkableClient(slugs=["co"], rate_limit=0.0)
        assert list(client.fetch()) == []


def test_html_stripped_from_detail():
    list_body = {"jobs": [_job()]}
    detail = _detail(
        description="<p>Lead <b>delivery</b></p>",
        requirements="<ul><li>5 years</li></ul>",
        benefits="",
    )
    responses = [_resp(200, list_body), _resp(200, detail)]
    with patch("engine.discovery.workable_client.requests.get",
               side_effect=responses), \
         patch("engine.discovery.workable_client.time.sleep"):
        client = WorkableClient(slugs=["co"], rate_limit=0.0)
        records = list(client.fetch())
    text = records[0].posting_text
    assert "Lead" in text
    assert "delivery" in text
    assert "5 years" in text
    assert "<p>" not in text and "<b>" not in text and "<li>" not in text
