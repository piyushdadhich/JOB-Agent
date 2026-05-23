"""FastAPI app factory for the dashboard.

A factory (rather than a module-level `app = FastAPI()`) keeps tests
clean: each test builds a fresh app and overrides dependencies on it
without polluting global state.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIST = PROJECT_ROOT / "dashboard" / "frontend" / "dist"


def create_app() -> FastAPI:
    app = FastAPI(
        title="Job Agent Dashboard",
        version="0.1.0",
        description=(
            "Localhost interface for the daily shortlist, prompt "
            "generation, document rendering, and Playwright apply "
            "flow. Personal tool — not for cloud deployment."
        ),
    )

    # The Vite dev server runs on :5173; the prod build is served
    # from the same FastAPI on :8000. Allow both for local-tool use.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://localhost:8000",
            "http://127.0.0.1:5173",
            "http://127.0.0.1:8000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # /api/health is served by the health router (Spec FIX-6) — a
    # rich health payload whose top-level `status` field still works
    # as a plain liveness probe for callers that only need 200/up.

    # Routers (imported here so test code can construct an app
    # without paying the import cost of unrelated modules).
    from dashboard.backend.routes import (
        activity_log as activity_log_router,
        applications as applications_router,
        applications_board as applications_board_router,
        apply as apply_router,
        calibrate as calibrate_router,
        cloud_usage as cloud_usage_router,
        digest as digest_router,
        email_monitor as email_monitor_router,
        expansion as expansion_router,
        files as files_router,
        flags as flags_router,
        health as health_router,
        pipeline as pipeline_router,
        pipeline_tracker as pipeline_tracker_router,
        prompts as prompts_router,
        resume_costs as resume_costs_router,
        settings as settings_router,
        setup as setup_router,
        shortlist as shortlist_router,
        stats as stats_router,
        system_health as system_health_router,
    )

    app.include_router(shortlist_router.router)
    # applications_board MUST register before applications: both use
    # the /api/applications prefix, and the literal /board path would
    # otherwise be captured by /api/applications/{application_id:int}
    # and 422 on the "board" segment.
    app.include_router(applications_board_router.router)
    app.include_router(applications_router.router)
    app.include_router(prompts_router.router)
    app.include_router(files_router.router)
    app.include_router(apply_router.router)
    app.include_router(stats_router.router)
    app.include_router(pipeline_router.router)
    app.include_router(flags_router.router)
    app.include_router(expansion_router.router)
    app.include_router(email_monitor_router.router)
    app.include_router(pipeline_tracker_router.router)
    app.include_router(setup_router.router)
    app.include_router(calibrate_router.router)
    app.include_router(digest_router.router)
    app.include_router(resume_costs_router.router)
    app.include_router(system_health_router.router)
    app.include_router(cloud_usage_router.router)
    app.include_router(settings_router.router)
    app.include_router(activity_log_router.router)
    app.include_router(health_router.router)

    # Serve the built React bundle at root, but only if it exists.
    # Mount last so /api/* routes win over the static catch-all.
    if FRONTEND_DIST.exists():
        app.mount(
            "/",
            StaticFiles(directory=str(FRONTEND_DIST), html=True),
            name="frontend",
        )
    return app


app = create_app()
