"""Tests for the smart-filler apply runner + plan-review routes.

Uses the same in-memory test pattern as test_dashboard_apply.py: build
a fresh app, override get_tracker, and inject a fake runner so we can
drive ApplySession through state transitions deterministically without
spinning up Playwright.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from dashboard.backend.app import create_app  # noqa: E402
from dashboard.backend.deps import get_tracker  # noqa: E402
from dashboard.backend.services.apply_service import (  # noqa: E402
    ApplyService,
    ApplySession,
    get_apply_service,
)
from engine.persistence.tracker import Tracker  # noqa: E402


# --- ApplyService unit tests (state machine) -------------------------

def _make_session(state="awaiting_plan_approval"):
    return ApplySession(posting_id=1, profile_id="p", state=state)


def test_approve_plan_sets_event_when_in_correct_state():
    s = _make_session("awaiting_plan_approval")
    svc = ApplyService()
    svc._session = s
    assert svc.approve_plan() is True
    assert s.plan_approve_event.is_set()


def test_approve_plan_returns_false_outside_state():
    s = _make_session("filling")
    svc = ApplyService()
    svc._session = s
    assert svc.approve_plan() is False
    assert not s.plan_approve_event.is_set()


def test_skip_plan_sets_event_when_in_correct_state():
    s = _make_session("awaiting_plan_approval")
    svc = ApplyService()
    svc._session = s
    assert svc.skip_plan() is True
    assert s.plan_skip_event.is_set()


def test_skip_plan_returns_false_outside_state():
    s = _make_session("ready_for_submit")
    svc = ApplyService()
    svc._session = s
    assert svc.skip_plan() is False


def test_to_status_includes_smart_filler_fields():
    s = _make_session("awaiting_plan_approval")
    s.using_smart_filler = True
    s.extracted_fields = [{"index": 1, "label": "Name"}]
    s.fill_plan = [{"field": 1, "action": "fill", "value": "x"}]
    s.plan_validation = {"fraction_valid": 1.0, "is_trustworthy": True}
    out = s.to_status()
    assert out["using_smart_filler"] is True
    assert out["extracted_fields"][0]["label"] == "Name"
    assert out["fill_plan"][0]["value"] == "x"
    assert out["plan_validation"]["is_trustworthy"] is True


def test_active_states_include_awaiting_plan_approval():
    from dashboard.backend.services.apply_service import ACTIVE_STATES
    assert "awaiting_plan_approval" in ACTIVE_STATES


# --- ApplyService.start with custom runner --------------------------

def _coro_done():
    async def _r(_session, _ctx):
        return None
    return _r


@pytest.mark.asyncio
async def test_start_uses_per_call_runner_override():
    svc = ApplyService(runner=_coro_done())
    invoked = {"flag": False}

    async def custom(_session, _ctx):
        invoked["flag"] = True

    session = await svc.start(
        posting_id=1, profile_id="p", ats="greenhouse",
        run_context={}, runner=custom,
    )
    await svc.wait_finished(timeout=5.0)
    assert invoked["flag"] is True
    svc.reset()


@pytest.mark.asyncio
async def test_start_marks_using_smart_filler_when_flag_set():
    svc = ApplyService(runner=_coro_done())
    session = await svc.start(
        posting_id=1, profile_id="p", ats="greenhouse",
        run_context={}, using_smart_filler=True,
    )
    assert session.using_smart_filler is True
    await svc.wait_finished(timeout=5.0)
    svc.reset()


# --- HTTP route tests -----------------------------------------------

@pytest.fixture
def harness(tmp_path):
    db = tmp_path / "t.db"
    tracker = Tracker(profile_id="p", db_path=db)
    app = create_app()

    def _override():
        yield tracker

    app.dependency_overrides[get_tracker] = _override
    svc = ApplyService()
    app.dependency_overrides[get_apply_service] = lambda: svc
    yield TestClient(app), tracker, svc
    tracker.close()
    svc.reset()


def test_approve_plan_route_409_when_no_session(harness):
    client, _, _ = harness
    resp = client.post("/api/apply/1/approve-plan")
    assert resp.status_code == 409
    assert "no active session" in resp.json()["detail"]


def test_skip_plan_route_409_when_no_session(harness):
    client, _, _ = harness
    resp = client.post("/api/apply/1/skip-plan")
    assert resp.status_code == 409


def test_approve_plan_route_409_when_wrong_state(harness):
    client, _, svc = harness
    # Park a session in 'filling' (not eligible for approval).
    svc._session = ApplySession(posting_id=42, profile_id="p", state="filling")
    resp = client.post("/api/apply/42/approve-plan")
    assert resp.status_code == 409
    assert "awaiting_plan_approval" in resp.json()["detail"]


def test_approve_plan_route_succeeds_when_state_matches(harness):
    client, _, svc = harness
    svc._session = ApplySession(
        posting_id=42, profile_id="p", state="awaiting_plan_approval",
    )
    resp = client.post("/api/apply/42/approve-plan")
    assert resp.status_code == 200
    assert svc._session.plan_approve_event.is_set()


def test_skip_plan_route_succeeds_when_state_matches(harness):
    client, _, svc = harness
    svc._session = ApplySession(
        posting_id=42, profile_id="p", state="awaiting_plan_approval",
    )
    resp = client.post("/api/apply/42/skip-plan")
    assert resp.status_code == 200
    assert svc._session.plan_skip_event.is_set()


def test_approve_plan_route_409_when_posting_id_mismatch(harness):
    client, _, svc = harness
    svc._session = ApplySession(
        posting_id=42, profile_id="p", state="awaiting_plan_approval",
    )
    resp = client.post("/api/apply/99/approve-plan")
    assert resp.status_code == 409


def test_status_route_exposes_fill_plan(harness):
    client, _, svc = harness
    s = ApplySession(
        posting_id=42, profile_id="p", state="awaiting_plan_approval",
    )
    s.using_smart_filler = True
    s.fill_plan = [{"field": 1, "action": "fill", "value": "x"}]
    svc._session = s
    body = client.get("/api/apply/status").json()
    assert body["active"] is True
    assert body["session"]["fill_plan"][0]["value"] == "x"
    assert body["session"]["using_smart_filler"] is True
