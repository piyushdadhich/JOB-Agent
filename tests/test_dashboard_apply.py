"""Tests for /api/apply/* with a fake runner (no Playwright).

These tests use httpx.AsyncClient + ASGITransport + pytest-asyncio so
the async tasks created inside route handlers live on the same loop
as the test code. Starlette's sync TestClient tears down its anyio
portal between requests and orphans `asyncio.create_task`-spawned
work, which makes state-machine tests flaky.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.backend.app import create_app  # noqa: E402
from dashboard.backend.deps import get_profile_id, get_tracker  # noqa: E402
from dashboard.backend.services.apply_service import (  # noqa: E402
    ApplyService,
    get_apply_service,
)
from engine.persistence.tracker import Tracker  # noqa: E402


def make_fake_runner(skipped_questions: list[str] | None = None):
    """Drives the state machine without launching a browser.

    When skipped_questions is provided, the runner mimics a handler
    that recorded those as unmatched and asks ctx['qa_generator']
    (if present) for proposals so the test can exercise the
    pending_questions path.
    """
    async def _runner(session, ctx):
        session.state = "filling"
        await asyncio.sleep(0)
        session.fields_filled = ["full_name", "email", "phone"]
        session.questions_answered = {"Are you authorized?": "Yes"}
        session.questions_skipped = list(skipped_questions or [])
        qa = ctx.get("qa_generator")
        if qa is not None and session.questions_skipped:
            from dashboard.backend.services.apply_service import (
                _propose_for_skipped,
            )
            session.pending_questions = _propose_for_skipped(
                qa, session.questions_skipped, posting={
                    "title": "PM", "employer": "Acme",
                },
            )
        session.state = "ready_for_submit"
        done, pending = await asyncio.wait(
            [
                asyncio.create_task(session.submit_event.wait()),
                asyncio.create_task(session.abort_event.wait()),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        if session.abort_event.is_set():
            session.state = "aborted"
            return
        session.state = "submitting"
        await asyncio.sleep(0)
        session.submitted_url = "https://confirmed.example/123"
        session.application_id = 42
        session.state = "submitted"

    return _runner


@pytest_asyncio.fixture
async def harness(tmp_path):
    db = tmp_path / "t.db"
    tracker = Tracker(profile_id="p", db_path=db)
    apply_svc = ApplyService(runner=make_fake_runner())
    app = create_app()

    def _tracker():
        yield tracker

    def _profile():
        return "p"

    def _apply():
        return apply_svc

    app.dependency_overrides[get_tracker] = _tracker
    app.dependency_overrides[get_profile_id] = _profile
    app.dependency_overrides[get_apply_service] = _apply

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client, tracker, apply_svc
    tracker.close()


def _add_posting(t, employer="TD", url="https://boards.greenhouse.io/td/jobs/1"):
    cid = t.upsert_company(employer)
    oid, _ = t.insert_opportunity(
        company_id=cid, source="manual_entry",
        source_url=url, title="PM",
        location="Toronto", posting_text="...",
    )
    return oid


def _seed_pending_docs(profile_id, posting_id, monkeypatch, tmp_path):
    """Create the .docx pending files the route checks for."""
    from dashboard.backend.services import render_service as rs

    monkeypatch.setattr(rs, "PROJECT_ROOT", tmp_path)
    pending = (
        tmp_path / "data" / profile_id / "applications" / "pending"
    )
    pending.mkdir(parents=True, exist_ok=True)
    (pending / f"resume_{posting_id}.docx").write_bytes(b"PK\x03\x04...")
    (pending / f"cover_letter_{posting_id}.docx").write_bytes(
        b"PK\x03\x04..."
    )


def _seed_applicant_profile(profile_id, monkeypatch, tmp_path):
    from engine.applicant import profile as prof

    monkeypatch.setattr(prof, "PROJECT_ROOT", tmp_path)
    cfg = tmp_path / "config" / "profiles"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / f"{profile_id}_applicant.yaml").write_text(
        "first_name: Test\n"
        "last_name: User\n"
        "email: t@example.com\n"
        "phone: '555-0000'\n",
        encoding="utf-8",
    )


async def _wait_for_state(client, target, timeout: float = 3.0):
    """Poll /api/apply/status until state == target. We're on the
    same loop as the route's task, so awaiting sleep yields to it."""
    deadline = asyncio.get_event_loop().time() + timeout
    last = None
    while asyncio.get_event_loop().time() < deadline:
        body = (await client.get("/api/apply/status")).json()
        s = body.get("session")
        last = s and s.get("state")
        if last == target:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"timeout waiting for state {target!r}; current={last!r}"
    )


# --- Tests ----------------------------------------------------

@pytest.mark.asyncio
async def test_status_when_idle_returns_active_false(harness):
    client, _, _ = harness
    r = await client.get("/api/apply/status")
    assert r.json() == {"active": False, "session": None}


@pytest.mark.asyncio
async def test_start_404_for_unknown_posting(harness):
    client, _, _ = harness
    r = await client.post("/api/apply/9999/start")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_start_400_when_url_does_not_match_any_ats(harness):
    client, t, _ = harness
    oid = _add_posting(t, url="https://example.com/careers/123")
    r = await client.post(f"/api/apply/{oid}/start")
    assert r.status_code == 400
    assert "no ATS handler matched" in r.json()["detail"]


@pytest.mark.asyncio
async def test_start_400_for_linkedin(harness, monkeypatch):
    client, t, _ = harness
    oid = _add_posting(
        t, url="https://www.linkedin.com/jobs/view/12345",
    )
    # FIX-2: LinkedIn postings are no longer blanket-blocked — the
    # route resolves the final URL (most LinkedIn jobs redirect off
    # to a real ATS). Stub the resolver to keep us on linkedin.com,
    # which exercises the Easy-Apply branch (returns 200 + state).
    from dashboard.backend.routes import apply as apply_mod
    async def _stub_resolve(url):
        return url
    monkeypatch.setattr(apply_mod, "_resolve_final_url", _stub_resolve)
    monkeypatch.setattr(apply_mod.webbrowser, "open", lambda u: True)
    r = await client.post(f"/api/apply/{oid}/start")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "linkedin_easy_apply"
    assert body["ats"] == "linkedin"
    assert "Easy Apply" in body["detail"]


@pytest.mark.asyncio
async def test_start_400_when_profile_missing(harness):
    client, t, _ = harness
    oid = _add_posting(t)
    r = await client.post(f"/api/apply/{oid}/start")
    assert r.status_code == 400
    assert "applicant profile" in r.json()["detail"]


@pytest.mark.asyncio
async def test_start_400_when_pending_docs_missing(
    harness, monkeypatch, tmp_path,
):
    client, t, _ = harness
    oid = _add_posting(t)
    _seed_applicant_profile("p", monkeypatch, tmp_path)
    # Don't seed pending docs.
    r = await client.post(f"/api/apply/{oid}/start")
    assert r.status_code == 400
    assert "missing" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_start_happy_path_drives_state_to_submitted(
    harness, monkeypatch, tmp_path,
):
    client, t, apply = harness
    oid = _add_posting(t)
    _seed_applicant_profile("p", monkeypatch, tmp_path)
    _seed_pending_docs("p", oid, monkeypatch, tmp_path)

    r = await client.post(f"/api/apply/{oid}/start")
    assert r.status_code == 200

    await _wait_for_state(client, "ready_for_submit")
    body = (await client.get("/api/apply/status")).json()
    assert body["active"] is True
    assert body["session"]["posting_id"] == oid
    assert "full_name" in body["session"]["fields_filled"]

    r = await client.post(f"/api/apply/{oid}/submit")
    assert r.status_code == 200

    await _wait_for_state(client, "submitted")
    final = apply.current()
    assert final.state == "submitted"
    assert final.submitted_url == "https://confirmed.example/123"


@pytest.mark.asyncio
async def test_submit_409_when_no_active_session(harness):
    client, t, _ = harness
    oid = _add_posting(t)
    r = await client.post(f"/api/apply/{oid}/submit")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_abort_during_ready_for_submit_marks_aborted(
    harness, monkeypatch, tmp_path,
):
    client, t, apply = harness
    oid = _add_posting(t)
    _seed_applicant_profile("p", monkeypatch, tmp_path)
    _seed_pending_docs("p", oid, monkeypatch, tmp_path)

    await client.post(f"/api/apply/{oid}/start")
    await _wait_for_state(client, "ready_for_submit")
    r = await client.post(f"/api/apply/{oid}/abort")
    assert r.status_code == 200
    await _wait_for_state(client, "aborted")
    assert apply.current().state == "aborted"


@pytest.mark.asyncio
async def test_start_409_when_session_already_in_flight(
    harness, monkeypatch, tmp_path,
):
    client, t, apply = harness
    oid = _add_posting(t)
    _seed_applicant_profile("p", monkeypatch, tmp_path)
    _seed_pending_docs("p", oid, monkeypatch, tmp_path)

    await client.post(f"/api/apply/{oid}/start")
    await _wait_for_state(client, "ready_for_submit")

    oid2 = _add_posting(
        t, employer="BMO",
        url="https://boards.greenhouse.io/bmo/jobs/2",
    )
    r = await client.post(f"/api/apply/{oid2}/start")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_mark_applied_creates_application_row(harness):
    client, t, _ = harness
    oid = _add_posting(t)
    r = await client.post(
        f"/api/apply/{oid}/mark-applied",
        json={"notes": "applied on LinkedIn"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "submitted"
    app = t.get_application_by_id(body["application_id"])
    assert app["status"] == "submitted"
    assert app["ats_platform"] == "manual"


@pytest.mark.asyncio
async def test_answer_endpoint_400_for_blank(harness):
    client, _, _ = harness
    r = await client.post(
        "/api/apply/answer",
        json={"question": "Q?", "answer": "   "},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_answer_endpoint_marks_pending_question_confirmed(
    monkeypatch, tmp_path,
):
    """POST /api/apply/answer should flip pending_questions[i].
    confirmed=True for the matching question on the live session."""
    from engine.applicant.qa_generator import QAGenerator
    from tests.test_applicant_qa_generator import FakeClient

    db = tmp_path / "t.db"
    tracker = Tracker(profile_id="p", db_path=db)
    cache = tmp_path / "cache.json"
    qa = QAGenerator(
        cloud_client=FakeClient(response="Drafted."),
        inventory_summary="...", cache_path=cache, interactive=False,
    )
    apply_svc = ApplyService(
        runner=make_fake_runner(skipped_questions=["Q1?"]),
    )

    app = create_app()
    app.dependency_overrides[get_tracker] = lambda: (yield tracker)
    app.dependency_overrides[get_profile_id] = lambda: "p"
    app.dependency_overrides[get_apply_service] = lambda: apply_svc

    # The route's confirm_answer rebuilds a QAGenerator from disk, so
    # the test must pre-create the cache file the route will write to.
    # We don't need an LLM key for that (no LLM call when only writing
    # the cache through QAGenerator.confirm_answer). Patch the
    # _build_qa_generator factory in the route module to return our
    # in-memory QAGenerator so the route writes to OUR cache.
    from dashboard.backend.routes import apply as apply_route

    monkeypatch.setattr(
        apply_route, "_build_qa_generator", lambda pid: qa,
    )

    _seed_applicant_profile("p", monkeypatch, tmp_path)
    cid = tracker.upsert_company("Acme")
    oid, _ = tracker.insert_opportunity(
        company_id=cid, source="manual_entry",
        source_url="https://boards.greenhouse.io/acme/jobs/1",
        title="PM", location="Toronto", posting_text="...",
    )
    _seed_pending_docs("p", oid, monkeypatch, tmp_path)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        # Manually attach a pending question so we don't need to
        # round-trip through propose_answer's LLM path.
        await client.post(f"/api/apply/{oid}/start")
        await _wait_for_state(client, "ready_for_submit")
        s = apply_svc.current()
        s.pending_questions = [
            {
                "question": "Q1?", "draft": None,
                "source": "skipped", "confirmed": False,
            },
        ]
        r = await client.post(
            "/api/apply/answer",
            json={"question": "Q1?", "answer": "User answer."},
        )
        assert r.status_code == 200
        assert r.json()["cached"] is True
        assert apply_svc.current().pending_questions[0]["confirmed"] is True
        # Cache write reaches disk for future runs.
        new = QAGenerator(
            cloud_client=FakeClient(),
            inventory_summary="...", cache_path=cache,
            interactive=False,
        )
        rr = new.propose_answer("Q1?")
        assert rr.source == "cache"
        assert rr.answer == "User answer."
        await client.post(f"/api/apply/{oid}/abort")
    tracker.close()


@pytest.mark.asyncio
async def test_pending_questions_populate_after_fill(
    monkeypatch, tmp_path,
):
    """Runner with a qa_generator in run_context populates
    session.pending_questions on the way to ready_for_submit."""
    from engine.applicant.qa_generator import QAGenerator
    from tests.test_applicant_qa_generator import FakeClient

    class _Runner:
        def __init__(self, qa):
            self.qa = qa

        async def __call__(self, session, ctx):
            ctx = dict(ctx)
            ctx["qa_generator"] = self.qa
            r = make_fake_runner(
                skipped_questions=["Tell me about a stakeholder you mediated."],
            )
            await r(session, ctx)

    db = tmp_path / "t.db"
    tracker = Tracker(profile_id="p", db_path=db)
    cache = tmp_path / "cache.json"
    qa = QAGenerator(
        cloud_client=FakeClient(response="Calmly aligned all parties."),
        inventory_summary="ten years of delivery", cache_path=cache,
        interactive=False,
    )
    apply_svc = ApplyService(runner=_Runner(qa))

    app = create_app()
    app.dependency_overrides[get_tracker] = lambda: (yield tracker)
    app.dependency_overrides[get_profile_id] = lambda: "p"
    app.dependency_overrides[get_apply_service] = lambda: apply_svc

    _seed_applicant_profile("p", monkeypatch, tmp_path)
    cid = tracker.upsert_company("Acme")
    oid, _ = tracker.insert_opportunity(
        company_id=cid, source="manual_entry",
        source_url="https://boards.greenhouse.io/acme/jobs/1",
        title="PM", location="Toronto", posting_text="...",
    )
    _seed_pending_docs("p", oid, monkeypatch, tmp_path)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        await client.post(f"/api/apply/{oid}/start")
        await _wait_for_state(client, "ready_for_submit")
        body = (await client.get("/api/apply/status")).json()
        pending = body["session"]["pending_questions"]
        assert len(pending) == 1
        assert pending[0]["question"] == (
            "Tell me about a stakeholder you mediated."
        )
        assert pending[0]["draft"] == "Calmly aligned all parties."
        assert pending[0]["source"] == "generated"
        assert pending[0]["confirmed"] is False
        await client.post(f"/api/apply/{oid}/abort")
    tracker.close()


@pytest.mark.asyncio
async def test_mark_applied_promotes_existing_drafted_app(harness):
    client, t, _ = harness
    oid = _add_posting(t)
    existing = t.create_application(
        opportunity_id=oid, resume_variant="dashboard_pending",
    )
    r = await client.post(
        f"/api/apply/{oid}/mark-applied",
        json={"notes": "submitted on LinkedIn"},
    )
    assert r.status_code == 200
    assert r.json()["application_id"] == existing
    assert t.get_application_by_id(existing)["status"] == "submitted"
