"""Tests for engine.applicant.smart_filler.SmartFormFiller."""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

# Transitively imports scrapegraphai via engine.applicant.smart_filler
# → engine.discovery.smart_scraper. Skip when the optional [scraper]
# extra isn't installed.
pytest.importorskip("scrapegraphai")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.applicant.smart_filler import (  # noqa: E402
    FillAction,
    PlanValidation,
    SmartFormFiller,
    _action_matches_field_type,
    _parse_actions_json,
)


# --- Fixtures and fakes ----------------------------------------------

@dataclass
class FakeProfile:
    first_name: str = "Alex"
    last_name: str = "Doe"
    email: str = "default@example.com"
    phone: str = "555-0100"
    city: str = "Toronto"
    province: str = "Ontario"
    country: str = "Canada"
    postal_code: str = "M5V 0A1"
    linkedin_url: str = "https://linkedin.com/in/default"
    work_authorization: str = "Permanent Resident"
    requires_sponsorship: bool = False
    willing_to_relocate: bool = True
    salary_expectation: str = "Competitive"
    start_date: str = "Immediately"


class FakeScraper:
    """Stand-in for SmartScraper with a controllable _run() result."""
    def __init__(self, run_result=None):
        self.run_result = run_result
        self.last_prompt = None
        self.last_source = None

    def _run(self, prompt, source):
        self.last_prompt = prompt
        self.last_source = source
        return self.run_result


class FakeCloud:
    """Stand-in for GemmaCloudClient.generate."""
    def __init__(self, response="[]"):
        self.response = response
        self.last_prompt = None
        self.last_system = None
        self.calls = 0

    def generate(self, *, prompt, system=None, temperature=0.4):
        self.last_prompt = prompt
        self.last_system = system
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeLocator:
    def __init__(self, count=1, raise_count=False):
        self._count = count
        self._raise_count = raise_count
        self.fill_calls = []
        self.select_calls = []
        self.upload_calls = []
        self.checked = False

    @property
    def first(self):
        return self

    async def count(self):
        if self._raise_count:
            raise RuntimeError("locator boom")
        return self._count

    async def fill(self, value):
        self.fill_calls.append(value)

    async def select_option(self, label=None, value=None):
        self.select_calls.append((label, value))

    async def set_input_files(self, path):
        self.upload_calls.append(path)

    async def check(self):
        self.checked = True

    async def click(self):
        pass


class FakePage:
    def __init__(self, html="<html></html>", locators=None):
        self._html = html
        # locators maps selector → FakeLocator. Anything missing returns count=0.
        self._locators = locators or {}

    async def content(self):
        return self._html

    def locator(self, selector):
        if selector in self._locators:
            return self._locators[selector]
        return FakeLocator(count=0)

    async def wait_for_load_state(self, *args, **kwargs):
        pass


def _run(coro):
    """Run a coroutine in a fresh event loop.

    Using asyncio.get_event_loop() raises DeprecationWarning on 3.10+
    and can fail outright when pytest-asyncio's auto-fixture has
    already closed the implicit loop. A dedicated loop per call avoids
    both pitfalls without forcing every test to be async.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _filler(*, scraper=None, cloud=None, profile=None, qa_matcher=None):
    return SmartFormFiller(
        scraper=scraper or FakeScraper(),
        cloud_client=cloud or FakeCloud(),
        profile=profile or FakeProfile(),
        inventory_summary="Strong delivery PM; 13y experience.",
        qa_matcher=qa_matcher,
    )


# --- _action_matches_field_type --------------------------------------

def test_action_matches_field_type():
    assert _action_matches_field_type("fill", "text")
    assert _action_matches_field_type("fill", "email")
    assert _action_matches_field_type("fill", "textarea")
    assert _action_matches_field_type("select", "select")
    assert _action_matches_field_type("upload", "file")
    assert _action_matches_field_type("check", "checkbox")
    assert _action_matches_field_type("skip", "anything")
    # incompatible
    assert not _action_matches_field_type("fill", "file")
    assert not _action_matches_field_type("select", "text")
    assert not _action_matches_field_type("upload", "text")
    assert not _action_matches_field_type("nonsense", "text")


# --- _parse_actions_json ---------------------------------------------

def test_parse_actions_json_array():
    out = _parse_actions_json('[{"field": 1, "action": "fill", "value": "x"}]')
    assert len(out) == 1
    assert out[0].field_index == 1
    assert out[0].value == "x"


def test_parse_actions_json_code_fence():
    out = _parse_actions_json(
        '```json\n[{"field": 2, "action": "skip"}]\n```'
    )
    assert len(out) == 1
    assert out[0].action == "skip"


def test_parse_actions_json_envelope():
    out = _parse_actions_json(
        '{"actions": [{"field": 1, "action": "fill", "value": "y"}]}'
    )
    assert len(out) == 1
    assert out[0].value == "y"


def test_parse_actions_json_invalid_returns_empty():
    assert _parse_actions_json("not json") == []
    assert _parse_actions_json("") == []
    assert _parse_actions_json("{}") == []


# --- extract_fields --------------------------------------------------

def test_extract_fields_returns_normalized_list():
    scraper = FakeScraper(run_result=[
        {"index": 1, "label": "Name", "type": "text", "selector": "#name"},
        {"index": 2, "label": "Email", "type": "email", "selector": "#email"},
    ])
    f = _filler(scraper=scraper)
    page = FakePage(html="<form>...</form>")
    out = _run(f.extract_fields(page))
    assert len(out) == 2
    assert out[0]["label"] == "Name"
    assert scraper.last_source == "<form>...</form>"
    assert "extract every visible form field" in scraper.last_prompt.lower()


def test_extract_fields_unwraps_envelope():
    scraper = FakeScraper(run_result={
        "fields": [
            {"index": 1, "label": "Name", "type": "text", "selector": "#n"},
        ],
    })
    f = _filler(scraper=scraper)
    out = _run(f.extract_fields(FakePage()))
    assert len(out) == 1
    assert out[0]["label"] == "Name"


def test_extract_fields_returns_empty_on_failure():
    scraper = FakeScraper(run_result=None)
    f = _filler(scraper=scraper)
    assert _run(f.extract_fields(FakePage())) == []


def test_extract_fields_handles_page_content_failure():
    class BoomPage:
        async def content(self):
            raise RuntimeError("page closed")
    f = _filler()
    assert _run(f.extract_fields(BoomPage())) == []


# --- decide_fills ----------------------------------------------------

def test_decide_fills_calls_cloud_for_each_field():
    cloud = FakeCloud(response=json.dumps([
        {"field": 1, "action": "fill", "value": "Alex Doe"},
        {"field": 2, "action": "fill", "value": "default@example.com"},
    ]))
    f = _filler(cloud=cloud)
    fields = [
        {"index": 1, "label": "Full Name", "type": "text",
         "selector": "#name", "required": True},
        {"index": 2, "label": "Email", "type": "email",
         "selector": "#email", "required": True},
    ]
    actions = f.decide_fills(fields, {"title": "PM", "employer": "TD"})
    assert len(actions) == 2
    assert all(isinstance(a, FillAction) for a in actions)
    assert actions[0].value == "Alex Doe"
    assert cloud.calls == 1


def test_decide_fills_qa_matcher_short_circuits_cloud():
    class StubMatcher:
        def match(self, text):
            from engine.applicant.qa_matcher import QAMatch
            if "authorization" in text.lower():
                return QAMatch(pattern_index=0, answer="Permanent Resident")
            return None

    cloud = FakeCloud(response=json.dumps([
        {"field": 2, "action": "fill", "value": "from llm"},
    ]))
    f = _filler(cloud=cloud, qa_matcher=StubMatcher())
    fields = [
        {"index": 1, "label": "Work authorization", "type": "text",
         "selector": "#auth"},
        {"index": 2, "label": "Why this role?", "type": "textarea",
         "selector": "#why"},
    ]
    actions = f.decide_fills(fields, {"title": "PM"})
    by_index = {a.field_index: a for a in actions}
    assert by_index[1].value == "Permanent Resident"
    assert by_index[1].source == "qa_matcher"
    assert by_index[2].value == "from llm"
    # Cloud was called once but only with the unmatched field in the prompt.
    assert "Why this role?" in cloud.last_prompt
    assert "Work authorization" not in cloud.last_prompt


def test_decide_fills_defaults_unspecified_to_skip():
    cloud = FakeCloud(response="[]")  # LLM returns no actions
    f = _filler(cloud=cloud)
    fields = [
        {"index": 1, "label": "Mystery", "type": "text", "selector": "#m"},
    ]
    actions = f.decide_fills(fields, {})
    assert len(actions) == 1
    assert actions[0].action == "skip"


def test_decide_fills_handles_cloud_failure():
    cloud = FakeCloud(response=RuntimeError("offline"))
    f = _filler(cloud=cloud)
    fields = [
        {"index": 1, "label": "Name", "type": "text", "selector": "#n"},
    ]
    actions = f.decide_fills(fields, {})
    # Falls through to default-skip action so caller can fall back cleanly.
    assert actions[0].action == "skip"


def test_decide_fills_empty_fields_returns_empty():
    f = _filler()
    assert f.decide_fills([], {}) == []


# --- validate_plan ---------------------------------------------------

def test_validate_plan_all_valid():
    f = _filler()
    fields = [
        {"index": 1, "label": "Name", "type": "text",
         "selector": "#name", "required": True},
    ]
    actions = [FillAction(field_index=1, action="fill", value="x")]
    page = FakePage(locators={"#name": FakeLocator(count=1)})
    plan = _run(f.validate_plan(page, fields, actions))
    assert plan.fraction_valid == 1.0
    assert plan.required_covered == 1.0
    assert plan.is_trustworthy


def test_validate_plan_invalid_when_selector_missing():
    f = _filler()
    fields = [
        {"index": 1, "label": "Name", "type": "text", "selector": "#nope"},
    ]
    actions = [FillAction(field_index=1, action="fill", value="x")]
    page = FakePage()  # no locators registered
    plan = _run(f.validate_plan(page, fields, actions))
    assert plan.fraction_valid == 0.0
    assert plan.invalid_count == 1
    assert any("matched nothing" in i for i in plan.issues)


def test_validate_plan_flags_action_field_mismatch():
    f = _filler()
    fields = [
        {"index": 1, "label": "Resume", "type": "file", "selector": "#cv"},
    ]
    actions = [FillAction(field_index=1, action="fill", value="text-into-file")]
    page = FakePage(locators={"#cv": FakeLocator(count=1)})
    plan = _run(f.validate_plan(page, fields, actions))
    assert plan.invalid_count == 1
    assert any("incompatible" in i for i in plan.issues)


def test_validate_plan_skip_actions_dont_hurt_score():
    f = _filler()
    fields = [
        {"index": 1, "label": "Optional", "type": "text", "selector": "#o"},
        {"index": 2, "label": "Name", "type": "text",
         "selector": "#n", "required": True},
    ]
    actions = [
        FillAction(field_index=1, action="skip"),
        FillAction(field_index=2, action="fill", value="x"),
    ]
    page = FakePage(locators={"#n": FakeLocator(count=1)})
    plan = _run(f.validate_plan(page, fields, actions))
    assert plan.fraction_valid == 1.0
    assert plan.skip_count == 1
    assert plan.is_trustworthy


def test_validate_plan_required_coverage_low():
    f = _filler()
    fields = [
        {"index": 1, "label": "A", "type": "text",
         "selector": "#a", "required": True},
        {"index": 2, "label": "B", "type": "text",
         "selector": "#b", "required": True},
        {"index": 3, "label": "C", "type": "text",
         "selector": "#c", "required": True},
    ]
    # Only one of three required fields has a real action.
    actions = [
        FillAction(field_index=1, action="fill", value="x"),
        FillAction(field_index=2, action="skip"),
        FillAction(field_index=3, action="skip"),
    ]
    page = FakePage(locators={"#a": FakeLocator(count=1)})
    plan = _run(f.validate_plan(page, fields, actions))
    assert plan.required_covered == pytest.approx(1 / 3)
    assert not plan.is_trustworthy


# --- execute_fills ---------------------------------------------------

def test_execute_fills_runs_each_action(tmp_path):
    f = _filler()
    name_loc = FakeLocator(count=1)
    why_loc = FakeLocator(count=1)
    seniority_loc = FakeLocator(count=1)
    cv_loc = FakeLocator(count=1)
    page = FakePage(locators={
        "#name": name_loc,
        "#why": why_loc,
        "#sen": seniority_loc,
        "#cv": cv_loc,
    })
    fields = [
        {"index": 1, "label": "Name", "type": "text", "selector": "#name"},
        {"index": 2, "label": "Why", "type": "textarea", "selector": "#why"},
        {"index": 3, "label": "Seniority", "type": "select",
         "selector": "#sen", "options": ["Junior", "Senior"]},
        {"index": 4, "label": "Resume", "type": "file", "selector": "#cv"},
    ]
    actions = [
        FillAction(field_index=1, action="fill", value="Alex"),
        FillAction(field_index=2, action="fill", value="Because..."),
        FillAction(field_index=3, action="select", value="Senior"),
        FillAction(field_index=4, action="upload", file="resume"),
    ]
    resume = tmp_path / "resume.docx"
    cl = tmp_path / "cl.docx"
    resume.write_bytes(b"x")
    cl.write_bytes(b"y")
    out = _run(f.execute_fills(page, actions, fields, resume, cl))
    assert "Name" in out.fields_filled
    assert "Why" in out.fields_filled
    assert "Seniority" in out.fields_filled
    assert "Resume" in out.fields_filled
    assert out.questions_answered["Why"] == "Because..."
    assert out.questions_answered["Seniority"] == "Senior"
    assert name_loc.fill_calls == ["Alex"]
    assert seniority_loc.select_calls[0] == ("Senior", None)
    assert str(resume) in cv_loc.upload_calls


def test_execute_fills_skips_when_selector_missing(tmp_path):
    f = _filler()
    page = FakePage()  # no locator registered
    fields = [
        {"index": 1, "label": "Name", "type": "text", "selector": "#nope"},
    ]
    actions = [FillAction(field_index=1, action="fill", value="x")]
    out = _run(f.execute_fills(
        page, actions, fields, tmp_path / "r.docx", tmp_path / "c.docx",
    ))
    assert "Name" in out.fields_skipped
    assert "Name" not in out.fields_filled


def test_execute_fills_continues_after_one_error(tmp_path):
    f = _filler()
    boom = FakeLocator(count=1)
    async def boom_fill(value):
        raise RuntimeError("element detached")
    boom.fill = boom_fill
    good = FakeLocator(count=1)
    page = FakePage(locators={"#bad": boom, "#good": good})
    fields = [
        {"index": 1, "label": "Bad", "type": "text", "selector": "#bad"},
        {"index": 2, "label": "Good", "type": "text", "selector": "#good"},
    ]
    actions = [
        FillAction(field_index=1, action="fill", value="x"),
        FillAction(field_index=2, action="fill", value="y"),
    ]
    out = _run(f.execute_fills(
        page, actions, fields, tmp_path / "r", tmp_path / "c",
    ))
    assert "Good" in out.fields_filled
    assert "Bad" in out.fields_skipped
    assert any("Bad" in e for e in out.errors)


def test_execute_fills_uploads_cover_letter_path(tmp_path):
    f = _filler()
    cv = FakeLocator(count=1)
    page = FakePage(locators={"#cl": cv})
    fields = [
        {"index": 1, "label": "Cover Letter", "type": "file",
         "selector": "#cl"},
    ]
    actions = [
        FillAction(field_index=1, action="upload", file="cover_letter"),
    ]
    cl_path = tmp_path / "letter.docx"
    cl_path.write_bytes(b"hi")
    out = _run(f.execute_fills(
        page, actions, fields, tmp_path / "resume.docx", cl_path,
    ))
    assert str(cl_path) in cv.upload_calls
    assert "Cover Letter" in out.fields_filled


# --- fill_multi_step -------------------------------------------------

def test_fill_multi_step_loops_until_no_fields(tmp_path):
    """Three pages: first two yield fields and a Next button; third
    yields no fields, ending the loop."""
    pages_seen = []

    class WizardPage:
        def __init__(self):
            self.next_clicks = 0
            self.html_seq = [
                "<form>page1</form>",
                "<form>page2</form>",
                "<form>page3</form>",
            ]

        async def content(self):
            html = self.html_seq[len(pages_seen)]
            pages_seen.append(html)
            return html

        def locator(self, selector):
            # Next button present only on first 2 pages.
            if "Next" in selector:
                if len(pages_seen) <= 2:
                    return FakeLocator(count=1)
                return FakeLocator(count=0)
            return FakeLocator(count=1)

        async def wait_for_load_state(self, *_args, **_kwargs):
            pass

    extracted = [
        [{"index": 1, "label": "F1", "type": "text", "selector": "#f1"}],
        [{"index": 1, "label": "F2", "type": "text", "selector": "#f2"}],
        [],  # third page: no fields → loop ends
    ]
    extract_calls = {"n": 0}

    def fake_run(prompt, source):
        out = extracted[extract_calls["n"]]
        extract_calls["n"] += 1
        return out

    scraper = FakeScraper()
    scraper._run = fake_run

    cloud = FakeCloud(response=json.dumps([
        {"field": 1, "action": "fill", "value": "x"},
    ]))
    f = _filler(scraper=scraper, cloud=cloud)
    out = _run(f.fill_multi_step(
        WizardPage(), {"title": "PM"}, tmp_path / "r", tmp_path / "c",
    ))
    # Two pages had fields -> two cloud calls -> two filled labels.
    assert out.fields_filled.count("F1") == 1
    assert out.fields_filled.count("F2") == 1
    assert cloud.calls == 2


def test_fill_multi_step_respects_max_pages(tmp_path):
    class InfinitePage:
        async def content(self):
            return "<form>endless</form>"

        def locator(self, selector):
            return FakeLocator(count=1)  # always has Next

        async def wait_for_load_state(self, *_args, **_kwargs):
            pass

    scraper = FakeScraper(run_result=[
        {"index": 1, "label": "F", "type": "text", "selector": "#f"},
    ])
    cloud = FakeCloud(response=json.dumps([
        {"field": 1, "action": "fill", "value": "x"},
    ]))
    f = _filler(scraper=scraper, cloud=cloud)
    _run(f.fill_multi_step(
        InfinitePage(), {}, tmp_path / "r", tmp_path / "c", max_pages=3,
    ))
    # Capped at 3 cloud calls even though pages "never end".
    assert cloud.calls == 3
