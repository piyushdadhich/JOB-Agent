"""Apply to one posting via the Playwright Application Agent.

Usage:
  python scripts/apply.py --profile default --posting 1234
  python scripts/apply.py --profile default --posting 1234 --dry-run

Workflow:
  1. Look up posting + applicant profile + render paths.
  2. Detect ATS from posting source_url.
  3. Open visible Chromium browser; navigate to source_url.
  4. Hand control to the matching ATSHandler -> fill_application().
  5. PAUSE: "Review form. Enter to submit, Ctrl+C to abort."
  6. handler.submit() (skipped if --dry-run).
  7. handler.capture_confirmation() -> screenshot to disk.
  8. Save application via engine.applicant.storage.save_application.
  9. Delete pending .docx files.

Phase 11.2 covers Greenhouse only. Lever / Ashby / Workday / Indeed
land in subsequent phases. LinkedIn Easy Apply is NEVER automated --
the agent prints a "do this manually" message and exits.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.applicant.handlers.ashby import AshbyHandler
from engine.applicant.handlers.base import (
    FillContext, detect_ats_from_url,
)
from engine.applicant.handlers.greenhouse import GreenhouseHandler
from engine.applicant.handlers.indeed import IndeedHandler
from engine.applicant.handlers.lever import LeverHandler
from engine.applicant.handlers.workday import WorkdayHandler
from engine.applicant.profile import load_applicant_profile
from engine.applicant.qa_generator import QAGenerator
from engine.applicant.qa_matcher import QAMatcher
from engine.applicant.storage import save_application
from engine.persistence.tracker import Tracker

logger = logging.getLogger(__name__)


HANDLERS: dict[str, type] = {
    "greenhouse": GreenhouseHandler,
    "lever": LeverHandler,
    "ashby": AshbyHandler,
    "workday": WorkdayHandler,
    "indeed": IndeedHandler,
}


def _resolve_handler(ats: str):
    cls = HANDLERS.get(ats)
    return cls() if cls is not None else None


def _pending_dir(profile_id: str) -> Path:
    return (
        PROJECT_ROOT / "data" / profile_id
        / "applications" / "pending"
    )


def _screenshots_dir(profile_id: str) -> Path:
    return (
        PROJECT_ROOT / "data" / profile_id
        / "applications" / "screenshots"
    )


def _resume_path(profile_id: str, posting_id: int) -> Path:
    return _pending_dir(profile_id) / f"resume_{posting_id}.docx"


def _cover_letter_path(profile_id: str, posting_id: int) -> Path:
    return (
        _pending_dir(profile_id) / f"cover_letter_{posting_id}.docx"
    )


def _load_text_for(docx_path: Path) -> str:
    """Try sibling .md first; fall back to extracting from .docx."""
    md_path = docx_path.with_suffix(".md")
    if md_path.exists():
        return md_path.read_text(encoding="utf-8")
    if not docx_path.exists():
        return ""
    try:
        from docx import Document
        doc = Document(str(docx_path))
        return "\n".join(p.text for p in doc.paragraphs if p.text)
    except Exception as e:
        logger.warning("text extract from %s failed: %s", docx_path, e)
        return ""


def _print_fill_summary(result) -> None:
    print()
    print("=" * 70)
    print(f"Fields filled:    {', '.join(result.fields_filled) or '(none)'}")
    if result.questions_answered:
        print()
        print("Questions answered:")
        for q, a in result.questions_answered.items():
            print(f"  - {q[:80]}")
            print(f"      -> {a}")
    if result.questions_skipped:
        print()
        print("Questions SKIPPED (review manually):")
        for q in result.questions_skipped:
            print(f"  - {q}")
    print("=" * 70)


def _print_fill_plan(fields, actions, validation) -> None:
    """Pretty-print SmartFormFiller's proposed actions plus the
    validation summary. Called before the [e]xecute prompt."""
    label_by_index = {int(f["index"]): f.get("label", "") for f in fields}
    print()
    print("=" * 70)
    print("SMART FILL PLAN")
    print("=" * 70)
    for action in actions:
        label = label_by_index.get(action.field_index, f"#{action.field_index}")
        if action.action == "upload":
            display = f"upload({action.file or 'resume'})"
        elif action.action == "skip":
            display = "skip"
        else:
            value_str = (action.value or "")[:60]
            display = f"{action.action} -> {value_str}"
        source = f" [{action.source}]" if action.source != "llm" else ""
        print(f"  {label[:50]:50s}  {display}{source}")
    print("-" * 70)
    pct = round(validation.fraction_valid * 100)
    req_pct = round(validation.required_covered * 100)
    print(
        f"  Selectors valid:    {validation.valid_count}/"
        f"{validation.valid_count + validation.invalid_count} "
        f"({pct}%)"
    )
    print(
        f"  Required addressed: {req_pct}%   "
        f"Trustworthy: {validation.is_trustworthy}"
    )
    if validation.issues:
        print("  Issues:")
        for issue in validation.issues[:6]:
            print(f"    - {issue}")
    print("=" * 70)


def _build_smart_filler(args, ctx):
    """Construct a SmartFormFiller using the same plumbing as the
    dashboard route. Returns None if the cloud client cannot be
    initialized so the caller can fall back to the handler path.
    """
    try:
        from engine.applicant.smart_filler import SmartFormFiller
        from engine.discovery.smart_scraper import SmartScraper
        from engine.llm.gemma_cloud_client import GemmaCloudClient
    except ImportError as e:
        logger.warning("smart filler deps missing: %s", e)
        return None
    try:
        cloud = GemmaCloudClient(profile_id=args.profile)
    except Exception as e:
        print(
            f"Cannot initialise Gemma cloud client: {e}\n"
            f"Set GEMINI_API_KEY or drop the --smart flag."
        )
        return None
    inventory_path = (
        PROJECT_ROOT / "source_materials" / args.profile
        / "career_inventory.md"
    )
    inventory = (
        inventory_path.read_text(encoding="utf-8")
        if inventory_path.exists() else ""
    )
    return SmartFormFiller(
        scraper=SmartScraper(),
        cloud_client=cloud,
        profile=ctx.profile,
        inventory_summary=inventory,
        qa_matcher=ctx.qa_matcher,
    )


async def _run_smart_fill(page, ctx, args, handler):
    """Run the SmartFormFiller pipeline against the current page and
    return an ApplicationResult-shaped object. Falls back to the
    handler when the plan can't be validated or the user chooses skip.
    """
    from engine.applicant.handlers.base import ApplicationResult
    smart = _build_smart_filler(args, ctx)
    if smart is None:
        print("Smart filler unavailable -- falling back to handler.")
        return await handler.fill_application(page, ctx)

    print(f"Smart filler: extracting fields via {smart.scraper.config['llm']['model']}...")
    fields = await smart.extract_fields(page)
    if not fields:
        print(
            "Smart filler found no form fields on this page. "
            "Falling back to handler."
        )
        return await handler.fill_application(page, ctx)

    print(
        f"Smart filler: {len(fields)} field(s) extracted. "
        f"Asking cloud LLM for fill decisions..."
    )
    actions = smart.decide_fills(fields, ctx.posting)
    validation = await smart.validate_plan(page, fields, actions)
    _print_fill_plan(fields, actions, validation)

    if not validation.is_trustworthy:
        print(
            "Plan validation below threshold "
            f"(valid={validation.fraction_valid:.0%}, "
            f"required_covered={validation.required_covered:.0%}). "
            "Falling back to handler -- press Enter to continue or Ctrl+C to abort."
        )
        try:
            input()
        except KeyboardInterrupt:
            print("\nAborted by user.")
            return ApplicationResult(ok=False, error="aborted at fallback prompt")
        return await handler.fill_application(page, ctx)

    while True:
        choice = input(
            "[e]xecute plan / [s]kip (fill manually) / [a]bort: "
        ).strip().lower()
        if choice in {"e", "execute", ""}:
            exec_result = await smart.execute_fills(
                page, actions, fields,
                ctx.resume_path, ctx.cover_letter_path,
            )
            return ApplicationResult(
                ok=True,
                fields_filled=list(exec_result.fields_filled),
                questions_answered=dict(exec_result.questions_answered),
                questions_skipped=list(exec_result.fields_skipped),
            )
        if choice in {"s", "skip"}:
            print("Skipping execute -- fill the form yourself in the browser.")
            input("Press Enter once you've finished filling...")
            return ApplicationResult(
                ok=True,
                fields_filled=[],
                questions_answered={},
                questions_skipped=[
                    f.get("label", f"field_{f.get('index')}") for f in fields
                ],
            )
        if choice in {"a", "abort"}:
            print("Aborted by user.")
            return ApplicationResult(ok=False, error="aborted at plan review")
        print("Unrecognised choice -- type e, s, or a.")


async def _run(args) -> int:
    tracker = Tracker(args.profile)
    try:
        posting = tracker.get_opportunity_by_id(args.posting)
        if posting is None:
            print(f"opportunity {args.posting} not found")
            return 1
    finally:
        tracker.close()

    ats = detect_ats_from_url(posting.get("source_url") or "")
    if ats is None:
        print(
            f"No ATS handler matched URL "
            f"{posting.get('source_url')!r}. "
            f"Open the link manually."
        )
        return 2
    if ats == "linkedin":
        print(
            "LinkedIn Easy Apply detected. Per architecture rule, "
            "this agent does NOT automate LinkedIn -- ban risk to "
            "your account. Open the URL manually:\n"
            f"  {posting['source_url']}"
        )
        return 0
    handler = _resolve_handler(ats)
    if handler is None:
        print(
            f"ATS '{ats}' detected but no handler registered yet "
            f"(Phase 2-5). Skipping for now."
        )
        return 3

    profile = load_applicant_profile(args.profile)
    missing = profile.required_fields_missing()
    if missing:
        print(
            "Applicant profile is missing required PII fields: "
            f"{', '.join(missing)}. Edit "
            f"config/profiles/{args.profile}_applicant.yaml first."
        )
        return 4

    resume_p = _resume_path(args.profile, args.posting)
    cl_p = _cover_letter_path(args.profile, args.posting)
    if not resume_p.exists():
        print(
            f"Resume not found at {resume_p}. "
            f"Run scripts/render_resume.py first."
        )
        return 5
    if not cl_p.exists():
        print(
            f"Cover letter not found at {cl_p}. "
            f"Run scripts/render_cover_letter.py first."
        )
        return 5

    qa_matcher = QAMatcher.from_yaml(profile=profile)

    # Tier 2 LLM Q&A is OPT-IN via --llm-questions. Default OFF
    # because the cloud call is interactive + slow + can block the
    # form-fill flow if the network is shaky. Add the flag to
    # invoke Gemma for behavioral / open-ended questions.
    qa_generator = None
    if args.llm_questions:
        try:
            from engine.llm.gemma_cloud_client import GemmaCloudClient
            from skills.inventory import InventoryTool
            cloud = GemmaCloudClient(profile_id=args.profile)
            inventory_summary = (
                InventoryTool(args.profile).get_summary()
            )
            qa_generator = QAGenerator(cloud, inventory_summary)
            print("Tier 2 LLM Q&A enabled (--llm-questions).")
        except Exception as e:
            logger.warning(
                "Cloud Q&A requested but unavailable: %s", e,
            )

    ctx = FillContext(
        posting=posting,
        profile=profile,
        resume_path=resume_p,
        cover_letter_path=cl_p,
        resume_text=_load_text_for(resume_p),
        cover_letter_text=_load_text_for(cl_p),
        qa_matcher=qa_matcher,
        qa_generator=qa_generator,
        dry_run=args.dry_run,
    )

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        try:
            print(f"Opening {posting['source_url']}")
            await page.goto(posting["source_url"], timeout=60_000)
            try:
                await page.wait_for_load_state(
                    "networkidle", timeout=30_000,
                )
            except Exception:
                pass

            if handler.name in ("workday", "indeed"):
                label = "WORKDAY" if handler.name == "workday" else "INDEED"
                print(
                    f"\n{label}: please sign in (or create an account) "
                    "in the browser, navigate to the Apply form, "
                    "then press Enter to continue..."
                )
                try:
                    input()
                except KeyboardInterrupt:
                    print("\nAborted by user.")
                    return 6

            if args.smart:
                print("Filling form via SmartFormFiller (LLM plan-review).")
                result = await _run_smart_fill(page, ctx, args, handler)
            else:
                print(f"Filling form via handler: {handler.name}")
                result = await handler.fill_application(page, ctx)
            if not result.ok:
                print(f"Fill failed: {result.error}")
                return 7
            _print_fill_summary(result)

            if args.dry_run:
                input(
                    "\nDRY RUN -- form filled, NOT submitting. "
                    "Press Enter to close the browser..."
                )
                return 0

            try:
                input(
                    "\nReview the form in the browser. "
                    "Press Enter to SUBMIT, or Ctrl+C to abort: "
                )
            except KeyboardInterrupt:
                print("\nAborted by user. Pending .docx files left in place.")
                return 6

            sub = await handler.submit(page)
            if not sub.ok:
                print(f"Submit failed: {sub.error}")
                return 7
            print(f"Submitted. Final URL: {sub.final_url}")

            today = datetime.now(timezone.utc).strftime(
                "%Y%m%dT%H%M%SZ",
            )
            screenshot_path = (
                _screenshots_dir(args.profile)
                / f"{args.posting}_{today}.png"
            )
            await handler.capture_confirmation(page, screenshot_path)
            print(f"Screenshot saved: {screenshot_path}")

            tracker = Tracker(args.profile)
            try:
                resume_variant = resume_p.name
                save_application(
                    tracker,
                    opportunity_id=args.posting,
                    resume_variant=resume_variant,
                    resume_text=ctx.resume_text,
                    cover_letter_text=ctx.cover_letter_text,
                    ats_platform=handler.name,
                    screenshot_path=str(
                        screenshot_path.relative_to(PROJECT_ROOT),
                    ),
                    submitted_url=sub.final_url,
                    screening_answers=result.questions_answered,
                )
            finally:
                tracker.close()

            for path in (resume_p, cl_p):
                try:
                    path.unlink()
                except Exception:
                    pass
            print("Pending .docx files cleaned up.")
            return 0
        finally:
            await context.close()
            await browser.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--posting", type=int, required=True,
        help="opportunity_id from the tracker",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="fill the form but never submit",
    )
    parser.add_argument(
        "--llm-questions", action="store_true",
        help=(
            "enable Tier 2 LLM Q&A (Gemma 4 31B) for screening "
            "questions that don't match a Tier 1 pattern. Off by "
            "default to keep dry-runs fast and offline-safe."
        ),
    )
    parser.add_argument(
        "--smart", action="store_true",
        help=(
            "use SmartFormFiller (ScrapeGraphAI + Ollama + Gemma cloud) "
            "instead of the ATS handler. Plan is shown for review "
            "before any fills. Falls back to the handler when the "
            "plan can't be validated. Pair with --dry-run for safe "
            "first-time tests."
        ),
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
