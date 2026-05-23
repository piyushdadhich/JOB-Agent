"""Orchestrate the Playwright apply flow from the dashboard.

scripts/apply.py runs the same flow from the CLI; this service
mirrors it but waits on asyncio.Events instead of stdin so the
dashboard UI can drive submit/abort via HTTP. Personal tool, so we
keep at most one in-flight session globally and refuse a second
start while one is active.

State machine:
  starting          -> task created, browser not yet open
  filling           -> handler.fill_application running
  ready_for_submit  -> form filled; waiting for human approval
  submitting        -> handler.submit running
  submitted         -> done; application row written
  aborted           -> user cancelled before submit
  error             -> exception captured in session.error
  not_supported     -> ATS handler missing or LinkedIn (no automation)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

ACTIVE_STATES = frozenset(
    {"starting", "filling", "awaiting_login", "awaiting_plan_approval",
     "ready_for_submit", "submitting"}
)
TERMINAL_STATES = frozenset(
    {"submitted", "aborted", "error", "not_supported"}
)

# URL fragments that signal a login / registration wall. When the
# apply page lands on one of these — or shows a password field — the
# runner pauses so the user can sign in; the persistent browser
# profile then saves the session for next time.
_LOGIN_URL_MARKERS = ("/login", "/signin", "/register", "/sso")
_LOGIN_CLEAR_TIMEOUT_MS = 300_000  # 5 minutes for the user to log in


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ApplySession:
    posting_id: int
    profile_id: str
    ats: Optional[str] = None
    state: str = "starting"
    error: Optional[str] = None
    fields_filled: list[str] = field(default_factory=list)
    questions_answered: dict = field(default_factory=dict)
    questions_skipped: list[str] = field(default_factory=list)
    # Tier-2 proposals for the questions we couldn't answer with the
    # pattern matcher. Each entry: {"question": str, "draft": str|None,
    # "source": "cache"|"generated"|"skipped", "confirmed": bool}.
    pending_questions: list[dict] = field(default_factory=list)
    submitted_url: Optional[str] = None
    screenshot_path: Optional[str] = None
    application_id: Optional[int] = None
    started_at: str = field(default_factory=_utc_now)
    finished_at: Optional[str] = None
    dry_run: bool = False
    using_smart_filler: bool = False
    # Smart-filler plan-review fields. Populated only while
    # state == 'awaiting_plan_approval'. extracted_fields is the field
    # list the LLM produced; fill_plan is the action list keyed by field
    # index. plan_validation summarises the trust check.
    extracted_fields: list[dict] = field(default_factory=list)
    fill_plan: list[dict] = field(default_factory=list)
    plan_validation: Optional[dict] = None
    submit_event: asyncio.Event = field(default_factory=asyncio.Event)
    abort_event: asyncio.Event = field(default_factory=asyncio.Event)
    plan_approve_event: asyncio.Event = field(default_factory=asyncio.Event)
    plan_skip_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional[asyncio.Task] = field(default=None, repr=False)

    def to_status(self) -> dict:
        """Public-safe shape returned to the frontend."""
        return {
            "posting_id": self.posting_id,
            "ats": self.ats,
            "state": self.state,
            "error": self.error,
            "fields_filled": list(self.fields_filled),
            "questions_answered": dict(self.questions_answered),
            "questions_skipped": list(self.questions_skipped),
            "pending_questions": list(self.pending_questions),
            "submitted_url": self.submitted_url,
            "screenshot_path": self.screenshot_path,
            "application_id": self.application_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "dry_run": self.dry_run,
            "using_smart_filler": self.using_smart_filler,
            "extracted_fields": list(self.extracted_fields),
            "fill_plan": list(self.fill_plan),
            "plan_validation": self.plan_validation,
        }


# Runner signature: receives the session + a "context" dict prepared
# by the route (posting, paths, profile, etc.) and drives the session
# state machine. Default runner uses Playwright; tests inject a fake.
SessionRunner = Callable[
    ["ApplySession", dict], Awaitable[None]
]


class ApplyService:
    """Singleton orchestrator. One in-flight session at a time."""

    def __init__(self, runner: Optional[SessionRunner] = None):
        self._session: Optional[ApplySession] = None
        self._runner = runner

    @property
    def runner(self) -> SessionRunner:
        return self._runner or _default_playwright_runner

    def set_runner(self, runner: Optional[SessionRunner]) -> None:
        self._runner = runner

    def current(self) -> Optional[ApplySession]:
        return self._session

    def is_active(self) -> bool:
        return (
            self._session is not None
            and self._session.state in ACTIVE_STATES
        )

    async def start(
        self,
        *,
        posting_id: int,
        profile_id: str,
        ats: str,
        run_context: dict,
        dry_run: bool = False,
        runner: Optional[SessionRunner] = None,
        using_smart_filler: bool = False,
    ) -> ApplySession:
        if self.is_active():
            raise RuntimeError(
                f"another apply session is already in flight "
                f"(posting {self._session.posting_id}, "
                f"state {self._session.state}); abort it first"
            )
        session = ApplySession(
            posting_id=posting_id,
            profile_id=profile_id,
            ats=ats,
            dry_run=dry_run,
            state="starting",
            using_smart_filler=using_smart_filler,
        )
        self._session = session
        chosen_runner = runner or self.runner
        # Wrap the runner so unhandled exceptions land on the session
        # rather than propagating into asyncio's default handler.
        async def _safe_runner():
            try:
                await chosen_runner(session, run_context)
            except asyncio.CancelledError:
                if session.state not in TERMINAL_STATES:
                    session.state = "aborted"
                    session.finished_at = _utc_now()
                raise
            except Exception as e:
                logger.exception("apply session crashed")
                session.state = "error"
                session.error = f"{type(e).__name__}: {e}"
                session.finished_at = _utc_now()

        session.task = asyncio.create_task(_safe_runner())
        return session

    def submit(self) -> bool:
        s = self._session
        if s is None or s.state != "ready_for_submit":
            return False
        s.submit_event.set()
        return True

    def approve_plan(self) -> bool:
        s = self._session
        if s is None or s.state != "awaiting_plan_approval":
            return False
        s.plan_approve_event.set()
        return True

    def skip_plan(self) -> bool:
        """User decided to fill the form manually instead of executing
        the LLM plan. Runner advances directly to ready_for_submit."""
        s = self._session
        if s is None or s.state != "awaiting_plan_approval":
            return False
        s.plan_skip_event.set()
        return True

    def abort(self) -> bool:
        s = self._session
        if s is None or s.state in TERMINAL_STATES:
            return False
        s.abort_event.set()
        # If we're still filling (not yet at ready_for_submit) the
        # task isn't watching abort_event, so cancel directly.
        if (
            s.state in {"starting", "filling"}
            and s.task is not None
        ):
            s.task.cancel()
        return True

    async def wait_finished(self, timeout: float = 60.0) -> None:
        """Block until the current session reaches a terminal state.
        Used by tests; not called from routes."""
        s = self._session
        if s is None or s.task is None:
            return
        try:
            await asyncio.wait_for(s.task, timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    def reset(self) -> None:
        self._session = None


# Module-level singleton. Routes resolve via Depends(get_apply_service)
# so tests can override.
_GLOBAL_SERVICE = ApplyService()


def get_apply_service() -> ApplyService:
    return _GLOBAL_SERVICE


# --- Default Playwright runner ------------------------------------

def _propose_for_skipped(
    qa_generator, skipped_questions: list[str], posting: dict,
) -> list[dict]:
    """Best-effort propose-answer pass over a list of skipped
    questions. Failures degrade to source='skipped' rather than
    aborting the apply session -- the user can still submit by
    filling the field manually in the browser."""
    out: list[dict] = []
    for q in skipped_questions:
        try:
            r = qa_generator.propose_answer(q, posting=posting)
        except Exception as e:
            logger.warning("propose_answer crashed: %s", e)
            out.append(
                {
                    "question": q,
                    "draft": None,
                    "source": "skipped",
                    "confirmed": False,
                }
            )
            continue
        out.append(
            {
                "question": q,
                "draft": r.answer,
                "source": r.source,
                "confirmed": False,
            }
        )
    return out


async def _await_login_if_walled(session: "ApplySession", page) -> bool:
    """If the apply page is a login wall, pause until the user signs
    in, then continue.

    Detection: a password input on the page, or a login marker in the
    URL. While paused the session sits in 'awaiting_login' and the
    user signs in directly in the headed browser — the persistent
    profile saves the cookies so the same ATS won't wall them again.

    Returns True if the form is reachable (no wall, or login cleared),
    False if the 5-minute window elapsed without the user logging in.
    """
    try:
        has_password = await page.query_selector(
            'input[type="password"]',
        )
    except Exception:  # pragma: no cover — defensive
        has_password = None
    url = page.url or ""
    url_walled = any(m in url for m in _LOGIN_URL_MARKERS)
    if not has_password and not url_walled:
        return True

    logger.info("login wall detected at %s — awaiting user login", url)
    session.state = "awaiting_login"
    try:
        await page.wait_for_url(
            lambda u: not any(
                m in u for m in ("/login", "/signin")
            ),
            timeout=_LOGIN_CLEAR_TIMEOUT_MS,
        )
        return True
    except Exception:
        return False


async def _default_playwright_runner(
    session: ApplySession, ctx: dict,
) -> None:
    """Mirrors the body of scripts/apply.py but waits on asyncio
    events for submit/abort instead of stdin input.

    `ctx` carries pre-resolved inputs from the route:
      handler            engine.applicant.handlers.<X>Handler instance
      fill_context       engine.applicant.handlers.base.FillContext
      source_url         str
      tracker_factory    callable(profile_id) -> Tracker
      pending_paths      (resume_path, cover_letter_path)
      screenshots_dir    Path
      project_root       Path (for relative screenshot path)
    """
    from engine.applicant.storage import save_application
    from playwright.async_api import async_playwright

    handler = ctx["handler"]
    fill_ctx = ctx["fill_context"]
    source_url = ctx["source_url"]
    tracker_factory = ctx["tracker_factory"]
    resume_path, cl_path = ctx["pending_paths"]
    screenshots_dir = ctx["screenshots_dir"]
    project_root = ctx["project_root"]
    user_data_dir = ctx.get("user_data_dir")

    async with async_playwright() as p:
        # Persistent profile keeps logins (Greenhouse, Workday, etc.)
        # so the user signs in once, not every time. Falls back to an
        # ephemeral context if no user_data_dir is provided.
        if user_data_dir is not None:
            Path(user_data_dir).mkdir(parents=True, exist_ok=True)
            context = await p.chromium.launch_persistent_context(
                str(user_data_dir), headless=False,
            )
            browser = None
        else:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context()
        page = await context.new_page()
        try:
            await page.goto(source_url, timeout=60_000)
            try:
                await page.wait_for_load_state(
                    "networkidle", timeout=30_000,
                )
            except Exception:
                pass

            if not await _await_login_if_walled(session, page):
                session.state = "error"
                session.error = (
                    "login wall not cleared within 5 minutes"
                )
                session.finished_at = _utc_now()
                return

            session.state = "filling"
            result = await handler.fill_application(page, fill_ctx)
            session.fields_filled = list(result.fields_filled)
            session.questions_answered = dict(result.questions_answered)
            session.questions_skipped = list(result.questions_skipped)
            if not result.ok:
                session.state = "error"
                session.error = result.error or "fill_application returned ok=False"
                session.finished_at = _utc_now()
                return

            qa_generator = ctx.get("qa_generator")
            if qa_generator is not None and result.questions_skipped:
                session.pending_questions = _propose_for_skipped(
                    qa_generator, result.questions_skipped,
                    fill_ctx.posting,
                )

            if session.dry_run:
                session.state = "ready_for_submit"
                # Wait for abort only -- dry_run never submits.
                await session.abort_event.wait()
                session.state = "aborted"
                session.finished_at = _utc_now()
                return

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
                session.finished_at = _utc_now()
                return

            session.state = "submitting"
            sub = await handler.submit(page)
            if not sub.ok:
                session.state = "error"
                session.error = sub.error or "submit returned ok=False"
                session.finished_at = _utc_now()
                return

            today = datetime.now(timezone.utc).strftime(
                "%Y%m%dT%H%M%SZ",
            )
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            screenshot = (
                screenshots_dir
                / f"{session.posting_id}_{today}.png"
            )
            await handler.capture_confirmation(page, screenshot)
            try:
                rel = str(screenshot.relative_to(project_root))
            except ValueError:
                rel = str(screenshot)
            session.screenshot_path = rel
            session.submitted_url = sub.final_url

            tracker = tracker_factory(session.profile_id)
            try:
                app_id = save_application(
                    tracker,
                    opportunity_id=session.posting_id,
                    resume_variant=resume_path.name,
                    resume_text=fill_ctx.resume_text,
                    cover_letter_text=fill_ctx.cover_letter_text,
                    ats_platform=handler.name,
                    screenshot_path=rel,
                    submitted_url=sub.final_url,
                    screening_answers=result.questions_answered,
                )
                session.application_id = app_id
            finally:
                tracker.close()

            for path in (resume_path, cl_path):
                try:
                    if path.exists():
                        path.unlink()
                except Exception as e:
                    logger.warning("cleanup failed for %s: %s", path, e)

            session.state = "submitted"
            session.finished_at = _utc_now()
        finally:
            await context.close()
            if browser is not None:
                await browser.close()


# --- Smart-filler runner (universal coverage with handler fallback) ---

async def _smart_filler_runner(
    session: ApplySession, ctx: dict,
) -> None:
    """Smart-Agent runner: SmartFormFiller proposes a fill plan, the
    user approves it, then Playwright executes. Falls back to the
    classic handler when the plan looks untrustworthy.

    Required ctx keys (in addition to the default-runner keys):
      smart_filler   engine.applicant.smart_filler.SmartFormFiller instance
      handler        ATS handler instance (for fallback path)
      ...             (rest mirrors _default_playwright_runner)
    """
    from engine.applicant.storage import save_application
    from playwright.async_api import async_playwright

    smart = ctx["smart_filler"]
    handler = ctx["handler"]
    fill_ctx = ctx["fill_context"]
    source_url = ctx["source_url"]
    tracker_factory = ctx["tracker_factory"]
    resume_path, cl_path = ctx["pending_paths"]
    screenshots_dir = ctx["screenshots_dir"]
    project_root = ctx["project_root"]
    user_data_dir = ctx.get("user_data_dir")

    async with async_playwright() as p:
        if user_data_dir is not None:
            Path(user_data_dir).mkdir(parents=True, exist_ok=True)
            context = await p.chromium.launch_persistent_context(
                str(user_data_dir), headless=False,
            )
            browser = None
        else:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context()
        page = await context.new_page()
        try:
            await page.goto(source_url, timeout=60_000)
            try:
                await page.wait_for_load_state(
                    "networkidle", timeout=30_000,
                )
            except Exception:
                pass

            if not await _await_login_if_walled(session, page):
                session.state = "error"
                session.error = (
                    "login wall not cleared within 5 minutes"
                )
                session.finished_at = _utc_now()
                return

            session.state = "filling"

            # Step 1+2: extract fields and decide actions
            fields = await smart.extract_fields(page)
            if not fields:
                # SmartFormFiller couldn't see any form. Fall back to
                # the handler so the user still gets a chance to apply.
                await _run_handler_fallback(
                    session, handler, page, fill_ctx, ctx,
                )
                if session.state in TERMINAL_STATES:
                    return
            else:
                actions = smart.decide_fills(fields, fill_ctx.posting)
                validation = await smart.validate_plan(
                    page, fields, actions,
                )
                session.extracted_fields = list(fields)
                session.fill_plan = [a.to_dict() for a in actions]
                session.plan_validation = {
                    "fraction_valid": validation.fraction_valid,
                    "valid_count": validation.valid_count,
                    "invalid_count": validation.invalid_count,
                    "skip_count": validation.skip_count,
                    "required_covered": validation.required_covered,
                    "is_trustworthy": validation.is_trustworthy,
                    "issues": list(validation.issues),
                }

                if not validation.is_trustworthy:
                    # Plan didn't pass — fall back to the handler.
                    logger.info(
                        "smart_runner: plan not trustworthy "
                        "(fraction_valid=%.2f required_covered=%.2f); "
                        "falling back to handler",
                        validation.fraction_valid,
                        validation.required_covered,
                    )
                    await _run_handler_fallback(
                        session, handler, page, fill_ctx, ctx,
                    )
                    if session.state in TERMINAL_STATES:
                        return
                else:
                    # Pause for human approval of the plan.
                    session.state = "awaiting_plan_approval"
                    done, pending = await asyncio.wait(
                        [
                            asyncio.create_task(
                                session.plan_approve_event.wait()
                            ),
                            asyncio.create_task(
                                session.plan_skip_event.wait()
                            ),
                            asyncio.create_task(session.abort_event.wait()),
                        ],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                    if session.abort_event.is_set():
                        session.state = "aborted"
                        session.finished_at = _utc_now()
                        return

                    if session.plan_approve_event.is_set():
                        exec_result = await smart.execute_fills(
                            page, actions, fields, resume_path, cl_path,
                        )
                        session.fields_filled = list(exec_result.fields_filled)
                        session.questions_answered = dict(
                            exec_result.questions_answered
                        )
                        session.questions_skipped = list(
                            exec_result.fields_skipped
                        )
                    else:
                        # plan_skip_event: user fills manually in the browser.
                        session.fields_filled = []
                        session.questions_skipped = [
                            f.get("label", f"field_{f.get('index')}")
                            for f in fields
                        ]

            # From here onward the flow mirrors the default runner:
            # pending Tier-2 question proposals (still useful even when
            # we used SmartFormFiller because some skipped fields may
            # warrant a generated draft), dry-run gate, submit gate,
            # screenshot, persist.
            qa_generator = ctx.get("qa_generator")
            if qa_generator is not None and session.questions_skipped:
                session.pending_questions = _propose_for_skipped(
                    qa_generator, session.questions_skipped,
                    fill_ctx.posting,
                )

            if session.dry_run:
                session.state = "ready_for_submit"
                await session.abort_event.wait()
                session.state = "aborted"
                session.finished_at = _utc_now()
                return

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
                session.finished_at = _utc_now()
                return

            session.state = "submitting"
            sub = await handler.submit(page)
            if not sub.ok:
                session.state = "error"
                session.error = sub.error or "submit returned ok=False"
                session.finished_at = _utc_now()
                return

            today = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            screenshot = (
                screenshots_dir / f"{session.posting_id}_{today}.png"
            )
            await handler.capture_confirmation(page, screenshot)
            try:
                rel = str(screenshot.relative_to(project_root))
            except ValueError:
                rel = str(screenshot)
            session.screenshot_path = rel
            session.submitted_url = sub.final_url

            tracker = tracker_factory(session.profile_id)
            try:
                app_id = save_application(
                    tracker,
                    opportunity_id=session.posting_id,
                    resume_variant=resume_path.name,
                    resume_text=fill_ctx.resume_text,
                    cover_letter_text=fill_ctx.cover_letter_text,
                    ats_platform=handler.name,
                    screenshot_path=rel,
                    submitted_url=sub.final_url,
                    screening_answers=session.questions_answered,
                )
                session.application_id = app_id
            finally:
                tracker.close()

            for path in (resume_path, cl_path):
                try:
                    if path.exists():
                        path.unlink()
                except Exception as e:
                    logger.warning("cleanup failed for %s: %s", path, e)

            session.state = "submitted"
            session.finished_at = _utc_now()
        finally:
            await context.close()
            if browser is not None:
                await browser.close()


async def _run_handler_fallback(
    session: ApplySession, handler, page, fill_ctx, ctx: dict,
) -> None:
    """Invoke the legacy handler.fill_application as a fallback path.
    Mutates session in-place. Mirrors the body of
    _default_playwright_runner's fill+propose section."""
    result = await handler.fill_application(page, fill_ctx)
    session.fields_filled = list(result.fields_filled)
    session.questions_answered = dict(result.questions_answered)
    session.questions_skipped = list(result.questions_skipped)
    if not result.ok:
        session.state = "error"
        session.error = result.error or "fill_application returned ok=False"
        session.finished_at = _utc_now()
        return
    qa_generator = ctx.get("qa_generator")
    if qa_generator is not None and result.questions_skipped:
        session.pending_questions = _propose_for_skipped(
            qa_generator, result.questions_skipped, fill_ctx.posting,
        )
