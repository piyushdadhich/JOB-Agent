"""Spec 9 TASKS 1 + 3 — worksheet + CAPTCHA detection tests."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from engine.applicant import captcha, worksheet


# --- worksheet --------------------------------------------------

_SAMPLE_FORM = """
<html><body>
<form>
  <label>First Name</label>
  <input type="text" name="first_name" id="fn" />

  <label>Email</label>
  <input type="email" name="email" placeholder="you@example.com" />

  <input type="hidden" name="csrf" />

  <textarea name="cover_letter" placeholder="Why are you a fit?"></textarea>

  <select name="years">
    <option>0-2</option>
    <option>3-5</option>
    <option>6+</option>
  </select>

  <input type="submit" value="Apply" />
</form>
</body></html>
"""


def test_parse_html_extracts_visible_fields():
    ws = worksheet.parse_html("https://example.com/x", _SAMPLE_FORM)
    types = [f.field_type for f in ws.fields]
    labels = [f.label for f in ws.fields]
    # 2 text inputs + 1 textarea + 1 select = 4. Hidden + submit dropped.
    assert len(ws.fields) == 4
    assert types.count("text") == 2
    assert "textarea" in types
    assert "select" in types
    assert any("first_name" in lbl or "First" in lbl for lbl in labels)


def test_parse_html_pulls_suggested_value_from_profile():
    applicant = {
        "first_name": "Alex",
        "email": "alex@example.com",
    }
    ws = worksheet.parse_html(
        "https://example.com/x", _SAMPLE_FORM, applicant=applicant,
    )
    by_name = {f.name: f for f in ws.fields}
    assert by_name["first_name"].suggested_value == "Alex"
    assert by_name["email"].suggested_value == "alex@example.com"


def test_parse_html_captures_select_options():
    ws = worksheet.parse_html("https://example.com/x", _SAMPLE_FORM)
    sel = next(f for f in ws.fields if f.field_type == "select")
    assert sel.options == ["0-2", "3-5", "6+"]


def test_parse_html_emits_note_when_empty():
    ws = worksheet.parse_html("https://example.com/spa", "<html></html>")
    assert ws.fields == []
    assert any("SPA" in n or "no form" in n.lower() for n in ws.notes)


def test_render_markdown_includes_each_field():
    ws = worksheet.parse_html("https://example.com/x", _SAMPLE_FORM)
    md = worksheet.render_markdown(ws)
    assert "Application Worksheet" in md
    assert "Field 1" in md
    assert "Suggested:" in md


def test_from_url_uses_session(monkeypatch):
    session = MagicMock()
    response = MagicMock()
    response.text = _SAMPLE_FORM
    response.raise_for_status = lambda: None
    session.get.return_value = response
    ws = worksheet.from_url(
        "https://example.com/x", session=session,
    )
    assert len(ws.fields) == 4
    session.get.assert_called_once()


# --- captcha ----------------------------------------------------

def test_detect_recaptcha_iframe():
    html = (
        '<iframe src="https://www.google.com/recaptcha/api2/anchor">'
        '</iframe>'
    )
    out = captcha.detect_from_html(html)
    assert out.present is True
    assert out.provider == "reCAPTCHA"


def test_detect_hcaptcha_iframe():
    html = '<iframe src="https://hcaptcha.com/captcha"></iframe>'
    out = captcha.detect_from_html(html)
    assert out.present is True
    assert out.provider == "hCaptcha"


def test_detect_cloudflare_turnstile_iframe():
    html = (
        '<iframe src="https://challenges.cloudflare.com/turnstile/v0/api">'
        '</iframe>'
    )
    out = captcha.detect_from_html(html)
    assert out.present is True
    assert out.provider == "Cloudflare Turnstile"


def test_detect_returns_false_when_no_captcha():
    out = captcha.detect_from_html("<html><body>clean</body></html>")
    assert out.present is False
    assert out.provider is None


@pytest.mark.asyncio
async def test_detect_on_page_finds_recaptcha_locator():
    # Mock the Playwright page surface: locator(sel).count() returns
    # 1 for the recaptcha selector, 0 for the rest.
    page = MagicMock()

    class FakeLocator:
        def __init__(self, n):
            self._n = n
        async def count(self):
            return self._n

    def _locator(sel):
        return FakeLocator(
            1 if "recaptcha" in sel else 0,
        )
    page.locator = _locator
    out = await captcha.detect_on_page(page)
    assert out.present is True
    assert out.provider == "reCAPTCHA"


@pytest.mark.asyncio
async def test_detect_on_page_returns_false_on_clean_page():
    page = MagicMock()
    class FakeLocator:
        async def count(self):
            return 0
    page.locator = lambda sel: FakeLocator()
    out = await captcha.detect_on_page(page)
    assert out.present is False
