"""Unit tests for engine.discovery.personio_client."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.personio_client import PersonioClient  # noqa: E402


def _xml(positions: list[str]) -> bytes:
    """Build a Personio-style XML feed from raw <position> snippets."""
    inner = "\n".join(positions)
    body = (
        f"<?xml version='1.0' encoding='UTF-8'?>"
        f"<workzag-jobs>{inner}</workzag-jobs>"
    )
    return body.encode("utf-8")


def _position(
    pid="42", name="Senior Project Manager",
    office="Toronto", recruiting_category="Delivery",
    descriptions=None,
) -> str:
    if descriptions is None:
        descriptions = [
            ("About the role", "<p>Lead delivery</p>"),
            ("Requirements", "<p>10 years</p>"),
        ]
    desc_xml = "".join(
        f"<jobDescription><name>{n}</name>"
        f"<value><![CDATA[{v}]]></value></jobDescription>"
        for n, v in descriptions
    )
    return (
        f"<position>"
        f"<id>{pid}</id>"
        f"<name>{name}</name>"
        f"<office>{office}</office>"
        f"<recruitingCategory>{recruiting_category}</recruitingCategory>"
        f"<jobDescriptions>{desc_xml}</jobDescriptions>"
        f"</position>"
    )


def _resp_xml(status: int, body: bytes):
    r = MagicMock()
    r.status_code = status
    r.content = body
    if 200 <= status < 300:
        r.raise_for_status = MagicMock()
    else:
        err = requests.HTTPError(f"HTTP {status}")
        err.response = r
        r.raise_for_status = MagicMock(side_effect=err)
    return r


# --- Behavioral ----------------------------------------------------

def test_fetch_returns_records():
    body = _xml([_position(pid="1"), _position(pid="2", name="PM")])
    with patch("engine.discovery.personio_client.requests.get",
               return_value=_resp_xml(200, body)), \
         patch("engine.discovery.personio_client.time.sleep"):
        client = PersonioClient(slugs=["acme"], rate_limit=0.0)
        records = list(client.fetch())
    assert len(records) == 2
    assert records[0].title == "Senior Project Manager"
    assert records[1].title == "PM"


def test_tries_de_then_com_domain():
    """First call hits .de, gets 404; second call hits .com, succeeds."""
    body = _xml([_position()])
    responses = [_resp_xml(404, b""), _resp_xml(200, body)]
    with patch("engine.discovery.personio_client.requests.get",
               side_effect=responses) as mock_get, \
         patch("engine.discovery.personio_client.time.sleep"):
        client = PersonioClient(slugs=["acme"], rate_limit=0.0)
        records = list(client.fetch())
    assert len(records) == 1
    # Two GET calls, with .de first then .com
    urls = [c.args[0] for c in mock_get.call_args_list]
    assert "jobs.personio.de" in urls[0]
    assert "jobs.personio.com" in urls[1]


def test_404_on_de_only_falls_through():
    """If .de fails with 404 and .com also fails with 404, no records.
    Both URLs are tried."""
    responses = [_resp_xml(404, b""), _resp_xml(404, b"")]
    with patch("engine.discovery.personio_client.requests.get",
               side_effect=responses) as mock_get, \
         patch("engine.discovery.personio_client.time.sleep"):
        client = PersonioClient(slugs=["acme"], rate_limit=0.0)
        records = list(client.fetch())
    assert records == []
    assert mock_get.call_count == 2


def test_xml_parsed_correctly():
    body = _xml([_position(pid="42", name="Sr PM",
                            office="Toronto",
                            recruiting_category="Delivery")])
    with patch("engine.discovery.personio_client.requests.get",
               return_value=_resp_xml(200, body)), \
         patch("engine.discovery.personio_client.time.sleep"):
        client = PersonioClient(slugs=["acme"], rate_limit=0.0)
        records = list(client.fetch())
    r = records[0]
    assert r.title == "Sr PM"
    assert r.location == "Toronto"
    assert r.employer_industry == "Delivery"


def test_description_blocks_combined():
    body = _xml([_position(descriptions=[
        ("About", "<p>Lead delivery</p>"),
        ("Requirements", "<p>10 years</p>"),
        ("Benefits", "<p>Pension</p>"),
    ])])
    with patch("engine.discovery.personio_client.requests.get",
               return_value=_resp_xml(200, body)), \
         patch("engine.discovery.personio_client.time.sleep"):
        client = PersonioClient(slugs=["acme"], rate_limit=0.0)
        records = list(client.fetch())
    text = records[0].posting_text
    for phrase in ("About", "Lead delivery", "Requirements",
                   "10 years", "Benefits", "Pension"):
        assert phrase in text


def test_html_in_description_stripped():
    body = _xml([_position(descriptions=[
        ("About", "<p>Lead <b>delivery</b></p>"),
    ])])
    with patch("engine.discovery.personio_client.requests.get",
               return_value=_resp_xml(200, body)), \
         patch("engine.discovery.personio_client.time.sleep"):
        client = PersonioClient(slugs=["acme"], rate_limit=0.0)
        records = list(client.fetch())
    text = records[0].posting_text
    assert "Lead" in text
    assert "delivery" in text
    assert "<p>" not in text and "<b>" not in text


def test_office_used_as_location():
    body = _xml([_position(office="Mississauga, ON")])
    with patch("engine.discovery.personio_client.requests.get",
               return_value=_resp_xml(200, body)), \
         patch("engine.discovery.personio_client.time.sleep"):
        client = PersonioClient(slugs=["acme"], rate_limit=0.0)
        records = list(client.fetch())
    assert records[0].location == "Mississauga, ON"


def test_source_is_personio_api():
    body = _xml([_position()])
    with patch("engine.discovery.personio_client.requests.get",
               return_value=_resp_xml(200, body)), \
         patch("engine.discovery.personio_client.time.sleep"):
        client = PersonioClient(slugs=["acme"], rate_limit=0.0)
        records = list(client.fetch())
    assert records[0].source == "personio_api"


def test_apply_url_constructed_from_id_and_winning_domain():
    """When .de works, apply URL uses .de. When .com works, apply
    URL uses .com."""
    body = _xml([_position(pid="999")])

    # .de wins
    with patch("engine.discovery.personio_client.requests.get",
               return_value=_resp_xml(200, body)), \
         patch("engine.discovery.personio_client.time.sleep"):
        client = PersonioClient(slugs=["acme"], rate_limit=0.0)
        records = list(client.fetch())
    assert records[0].source_url == "https://acme.jobs.personio.de/job/999"

    # .com wins (after .de 404)
    responses = [_resp_xml(404, b""), _resp_xml(200, body)]
    with patch("engine.discovery.personio_client.requests.get",
               side_effect=responses), \
         patch("engine.discovery.personio_client.time.sleep"):
        client2 = PersonioClient(slugs=["acme"], rate_limit=0.0)
        records2 = list(client2.fetch())
    assert records2[0].source_url == \
        "https://acme.jobs.personio.com/job/999"


def test_empty_feed_returns_nothing():
    body = _xml([])
    with patch("engine.discovery.personio_client.requests.get",
               return_value=_resp_xml(200, body)), \
         patch("engine.discovery.personio_client.time.sleep"):
        client = PersonioClient(slugs=["acme"], rate_limit=0.0)
        assert list(client.fetch()) == []
