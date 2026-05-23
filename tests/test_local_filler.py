"""Tests for engine.applicant.local_filler.LocalFormFiller."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.applicant.local_filler import (  # noqa: E402
    DetectedField,
    LocalFormFiller,
    format_profile_for_prompt,
)


@dataclass
class FakeEducation:
    degree: str
    school: str = ""


@dataclass
class FakeCert:
    name: str
    active: bool = True


@dataclass
class FakeProfile:
    first_name: str = "Alex"
    last_name: str = "Doe"
    email: str = "p@example.com"
    phone: str = "555-0100"
    city: str = "Toronto"
    province: str = "Ontario"
    country: str = "Canada"
    postal_code: str = ""
    linkedin_url: str = "https://linkedin.com/in/p"
    portfolio_url: str = ""
    work_authorization: str = "Permanent Resident"
    requires_sponsorship: bool = False
    willing_to_relocate: bool = True
    salary_expectation: str = "Competitive"
    salary_min: int = 95000
    salary_max: int = 145000
    salary_currency: str = "CAD"
    total_years_experience: int = 13
    start_date: str = "Immediately"
    education: list = field(default_factory=lambda: [
        FakeEducation(degree="MBA", school="Fordham University"),
    ])
    certifications: list = field(default_factory=lambda: [
        FakeCert(name="PMP", active=True),
    ])


# --- format_profile_for_prompt ----------------------------------------

def test_format_profile_includes_name_email_and_certs():
    s = format_profile_for_prompt(FakeProfile())
    assert "first_name: Alex" in s
    assert "email: p@example.com" in s
    assert "certifications: PMP" in s
    assert "highest_degree: MBA" in s
    assert "university: Fordham University" in s


def test_format_profile_skips_empty_strings():
    s = format_profile_for_prompt(
        FakeProfile(postal_code="", portfolio_url=""),
    )
    assert "postal_code" not in s
    assert "portfolio_url" not in s


def test_format_profile_skips_zero_numerics():
    s = format_profile_for_prompt(
        FakeProfile(salary_min=0, total_years_experience=0),
    )
    assert "salary_min" not in s
    assert "total_years_experience" not in s


def test_format_profile_emits_requires_sponsorship_explicitly_no():
    s = format_profile_for_prompt(
        FakeProfile(requires_sponsorship=False),
    )
    assert "requires_sponsorship: no" in s


# --- _clean_llm_value -------------------------------------------------

def test_clean_value_strips_double_quotes():
    assert LocalFormFiller._clean_llm_value('"Alex"') == "Alex"


def test_clean_value_strips_single_quotes():
    assert LocalFormFiller._clean_llm_value("'Toronto'") == "Toronto"


def test_clean_value_takes_first_line():
    raw = "Yes\nExplanation: I am authorized."
    assert LocalFormFiller._clean_llm_value(raw) == "Yes"


def test_clean_value_strips_think_block():
    raw = "<think>let me think</think>Alex"
    assert LocalFormFiller._clean_llm_value(raw) == "Alex"


# --- _build_prompt ----------------------------------------------------

def test_prompt_includes_no_think_directive():
    f = LocalFormFiller(FakeProfile())
    p = f._build_prompt("First Name", "text", [], required=True)
    assert "/no_think" in p


def test_prompt_includes_today_iso_date():
    f = LocalFormFiller(FakeProfile())
    p = f._build_prompt("Start Date", "text", [], required=True)
    assert date.today().isoformat() in p


def test_prompt_lists_dropdown_options():
    f = LocalFormFiller(FakeProfile())
    p = f._build_prompt(
        "Country", "dropdown", ["Canada", "United States"],
        required=True,
    )
    assert "Options: Canada, United States" in p


def test_prompt_marks_optional_fields_optional():
    f = LocalFormFiller(FakeProfile())
    p = f._build_prompt("Portfolio", "url", [], required=False)
    assert "Optional" in p


# --- fill_field: QA matcher short-circuit -----------------------------

def test_fill_field_qa_matcher_hit_skips_llm():
    qa = MagicMock()
    match = MagicMock()
    match.answer = "Yes"
    qa.match.return_value = match
    f = LocalFormFiller(FakeProfile(), qa_matcher=qa)
    with patch.object(f, "_ollama_call") as mock_call:
        value, source = f.fill_field(
            "Authorized to work in Canada?", "dropdown",
            ["Yes", "No"], required=True,
        )
    assert (value, source) == ("Yes", "qa_matcher")
    mock_call.assert_not_called()


def test_fill_field_falls_through_when_qa_matcher_misses():
    qa = MagicMock()
    qa.match.return_value = None
    f = LocalFormFiller(FakeProfile(), qa_matcher=qa)
    with patch.object(f, "_ollama_call", return_value="Alex"):
        value, source = f.fill_field(
            "First Name", "text", [], required=True,
        )
    assert (value, source) == ("Alex", "llm")


def test_fill_field_with_no_qa_matcher_calls_llm():
    f = LocalFormFiller(FakeProfile())
    with patch.object(f, "_ollama_call", return_value="13"):
        value, source = f.fill_field(
            "Years of project management experience",
            "number", [], required=True,
        )
    assert (value, source) == ("13", "llm")


# --- _ollama_call: HTTP behavior --------------------------------------

def test_ollama_call_posts_correct_payload():
    f = LocalFormFiller(FakeProfile())
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": "Alex"}
    mock_resp.raise_for_status.return_value = None
    with patch("requests.post", return_value=mock_resp) as mock_post:
        out = f._ollama_call("test prompt")
    assert out == "Alex"
    args, kwargs = mock_post.call_args
    assert args[0] == "http://localhost:11434/api/generate"
    payload = kwargs["json"]
    assert payload["model"] == "gemma4:e4b"
    assert payload["prompt"] == "test prompt"
    assert payload["stream"] is False
    assert payload["options"]["temperature"] == 0.0


def test_ollama_call_raises_on_http_error():
    import requests as rq
    f = LocalFormFiller(FakeProfile())
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = rq.HTTPError("500")
    with patch("requests.post", return_value=mock_resp):
        with pytest.raises(rq.HTTPError):
            f._ollama_call("test")


# --- Field detection + filling via real Playwright -------------------
#
# These tests use a real headless chromium with page.set_content() to
# load small HTML fixtures inline. No Ollama is touched; the tests
# only exercise detect_fields() and fill_one().

@pytest.fixture(scope="module")
def playwright_browser():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    yield browser
    browser.close()
    pw.stop()


@pytest.fixture
def page(playwright_browser):
    ctx = playwright_browser.new_context()
    p = ctx.new_page()
    yield p
    ctx.close()


def test_detect_text_input_with_label_for(page):
    page.set_content(
        "<form>"
        "<label for='fn'>First Name</label>"
        "<input id='fn' type='text' required>"
        "</form>"
    )
    f = LocalFormFiller(FakeProfile())
    fields = f.detect_fields(page)
    assert len(fields) == 1
    assert fields[0].label == "First Name"
    assert fields[0].field_type == "text"
    assert fields[0].required is True


def test_detect_email_input_by_aria_label(page):
    page.set_content(
        "<input type='email' aria-label='Email Address'>"
    )
    f = LocalFormFiller(FakeProfile())
    fields = f.detect_fields(page)
    assert len(fields) == 1
    assert fields[0].label == "Email Address"
    assert fields[0].field_type == "email"


def test_detect_dropdown_with_options(page):
    page.set_content(
        "<label for='country'>Country</label>"
        "<select id='country'>"
        "<option>Canada</option>"
        "<option>United States</option>"
        "</select>"
    )
    f = LocalFormFiller(FakeProfile())
    fields = f.detect_fields(page)
    assert len(fields) == 1
    assert fields[0].field_type == "dropdown"
    assert fields[0].options == ["Canada", "United States"]


def test_detect_radio_group_uses_legend_as_label(page):
    page.set_content(
        "<fieldset><legend>Work authorization</legend>"
        "<label><input type='radio' name='auth' value='yes'>Yes</label>"
        "<label><input type='radio' name='auth' value='no'>No</label>"
        "</fieldset>"
    )
    f = LocalFormFiller(FakeProfile())
    fields = f.detect_fields(page)
    radios = [x for x in fields if x.field_type == "radio"]
    assert len(radios) == 1
    assert radios[0].label == "Work authorization"
    assert radios[0].group_name == "auth"
    assert set(radios[0].options) == {"Yes", "No"}


def test_detect_checkbox_via_parent_label(page):
    page.set_content(
        "<label><input type='checkbox' id='tc' required>"
        "I agree to the terms</label>"
    )
    f = LocalFormFiller(FakeProfile())
    fields = f.detect_fields(page)
    cb = [x for x in fields if x.field_type == "checkbox"]
    assert len(cb) == 1
    assert "agree" in cb[0].label.lower()
    assert cb[0].required is True


def test_detect_file_input(page):
    page.set_content(
        "<label for='cv'>Resume</label>"
        "<input id='cv' type='file'>"
    )
    f = LocalFormFiller(FakeProfile())
    fields = f.detect_fields(page)
    assert len(fields) == 1
    assert fields[0].field_type == "file"


def test_detect_uses_placeholder_when_no_label(page):
    page.set_content(
        "<input type='text' placeholder='Your full name'>"
    )
    f = LocalFormFiller(FakeProfile())
    fields = f.detect_fields(page)
    assert len(fields) == 1
    assert fields[0].label == "Your full name"


def test_fill_text_input(page):
    page.set_content(
        "<input id='fn' type='text' aria-label='First Name'>"
    )
    f = LocalFormFiller(FakeProfile())
    fields = f.detect_fields(page)
    f.fill_one(page, fields[0], "Alex")
    assert page.locator("#fn").input_value() == "Alex"


def test_fill_dropdown(page):
    page.set_content(
        "<select aria-label='Country' id='c'>"
        "<option>Canada</option>"
        "<option>United States</option>"
        "</select>"
    )
    f = LocalFormFiller(FakeProfile())
    fields = f.detect_fields(page)
    f.fill_one(page, fields[0], "United States")
    assert page.locator("#c").input_value() == "United States"


def test_fill_checkbox_yes(page):
    page.set_content(
        "<input type='checkbox' id='tc' aria-label='Agree'>"
    )
    f = LocalFormFiller(FakeProfile())
    fields = f.detect_fields(page)
    f.fill_one(page, fields[0], "Yes")
    assert page.locator("#tc").is_checked()


def test_fill_radio_group_by_label(page):
    page.set_content(
        "<fieldset><legend>Authorized</legend>"
        "<label><input type='radio' name='a' value='y' id='y'>Yes</label>"
        "<label><input type='radio' name='a' value='n' id='n'>No</label>"
        "</fieldset>"
    )
    f = LocalFormFiller(FakeProfile())
    fields = f.detect_fields(page)
    radios = [x for x in fields if x.field_type == "radio"]
    f.fill_one(page, radios[0], "Yes")
    assert page.locator("#y").is_checked()
    assert not page.locator("#n").is_checked()


# --- run(): end-to-end with mocked LLM --------------------------------

def test_detect_fields_against_html_fixture(page):
    """Load tests/fixtures/sample_application_form.html via file://
    and confirm the locator detection sees every field the CLI's
    dry-run path is expected to find."""
    fixture = (
        Path(__file__).parent / "fixtures"
        / "sample_application_form.html"
    ).absolute()
    page.goto(fixture.as_uri(), wait_until="domcontentloaded")
    f = LocalFormFiller(FakeProfile())
    fields = f.detect_fields(page)
    types = {x.field_type for x in fields}
    # All the major form-element types should be represented.
    assert {
        "text", "email", "tel", "number", "url",
        "textarea", "dropdown", "radio", "checkbox", "file",
    } <= types
    assert len(fields) == 15
    # The radio groups should be detected once each, not per-radio.
    radios = [x for x in fields if x.field_type == "radio"]
    assert len(radios) == 2
    assert {r.group_name for r in radios} == {"auth", "sponsor"}


def test_run_end_to_end_with_mocked_llm(page):
    page.set_content(
        "<form>"
        "<label for='fn'>First Name</label>"
        "<input id='fn' type='text' required>"
        "<label for='em'>Email</label>"
        "<input id='em' type='email' required>"
        "</form>"
    )
    f = LocalFormFiller(FakeProfile())
    # Mock so each LLM call returns a deterministic value.
    with patch.object(
        f, "_ollama_call",
        side_effect=["Alex", "p@example.com"],
    ):
        result = f.run(page)
    assert result["detected"] == 2
    assert len(result["filled"]) == 2
    assert page.locator("#fn").input_value() == "Alex"
    assert page.locator("#em").input_value() == "p@example.com"
