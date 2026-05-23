"""Render saved markdown to .docx and serve it.

Two flavors per document type:
  /api/preview/{id}/{kind}.docx   inline (browser tries to display)
  /api/download/{id}/{kind}.docx  attachment (force download)

`{id}` is the opportunity id (matches the prompt routes), and `{kind}`
is "resume" or "cover-letter". The route reads the saved text from
the application row, hands it to RenderService, and serves the .docx.
404s when no text is saved yet -- the caller should save first.

We render on every request rather than caching the rendered file
because the saved text can change between requests and the rendering
cost is sub-second. The pending/ folder is the rendered output's
home; the FileResponse streams from there.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from dashboard.backend.deps import get_profile_id, get_tracker
from dashboard.backend.services.render_service import RenderService
from engine.persistence.tracker import Tracker

router = APIRouter(tags=["files"])


_DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument."
    "wordprocessingml.document"
)


def _candidate_name(profile_id: str) -> str:
    """Best-effort full name for the cover letter. Falls back to a
    generic placeholder if the applicant profile isn't on disk yet
    (e.g. fresh checkout)."""
    try:
        from engine.applicant.profile import load_applicant_profile
        return load_applicant_profile(profile_id).full_name or "Candidate"
    except FileNotFoundError:
        return "Candidate"


def _latest_application_for_posting(
    tracker: Tracker, posting_id: int,
) -> Optional[dict]:
    rows = tracker._query_all(
        "SELECT * FROM applications WHERE opportunity_id = ? "
        "ORDER BY status_updated_at DESC LIMIT 1",
        (posting_id,),
    )
    return dict(rows[0]) if rows else None


def _render_for(
    posting_id: int,
    kind: Literal["resume", "cover-letter"],
    profile_id: str,
    tracker: Tracker,
) -> Path:
    posting = tracker.get_opportunity_by_id(posting_id)
    if posting is None:
        raise HTTPException(
            status_code=404,
            detail=f"opportunity {posting_id} not found",
        )
    app_row = _latest_application_for_posting(tracker, posting_id)
    if app_row is None:
        raise HTTPException(
            status_code=404,
            detail=f"no application yet for opportunity {posting_id}",
        )
    text_field = (
        "resume_text" if kind == "resume" else "cover_letter_text"
    )
    content = app_row.get(text_field)
    if not content:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no {kind.replace('-', ' ')} text saved yet "
                f"for opportunity {posting_id}; POST to "
                f"/api/prompts/{posting_id}/"
                f"{kind} first"
            ),
        )
    svc = RenderService()
    if kind == "resume":
        return svc.render_resume(
            profile_id=profile_id,
            posting_id=posting_id,
            content=content,
            posting=posting,
        )
    return svc.render_cover_letter(
        profile_id=profile_id,
        posting_id=posting_id,
        content=content,
        posting=posting,
        candidate_name=_candidate_name(profile_id),
    )


@router.get("/api/preview/{posting_id}/resume.docx")
def preview_resume(
    posting_id: int,
    profile_id: str = Depends(get_profile_id),
    tracker: Tracker = Depends(get_tracker),
) -> FileResponse:
    path = _render_for(posting_id, "resume", profile_id, tracker)
    return FileResponse(
        str(path), media_type=_DOCX_MIME, filename=path.name,
        content_disposition_type="inline",
    )


@router.get("/api/preview/{posting_id}/cover-letter.docx")
def preview_cover_letter(
    posting_id: int,
    profile_id: str = Depends(get_profile_id),
    tracker: Tracker = Depends(get_tracker),
) -> FileResponse:
    path = _render_for(
        posting_id, "cover-letter", profile_id, tracker,
    )
    return FileResponse(
        str(path), media_type=_DOCX_MIME, filename=path.name,
        content_disposition_type="inline",
    )


@router.get("/api/download/{posting_id}/resume.docx")
def download_resume(
    posting_id: int,
    profile_id: str = Depends(get_profile_id),
    tracker: Tracker = Depends(get_tracker),
) -> FileResponse:
    path = _render_for(posting_id, "resume", profile_id, tracker)
    return FileResponse(
        str(path), media_type=_DOCX_MIME, filename=path.name,
        content_disposition_type="attachment",
    )


@router.get("/api/download/{posting_id}/cover-letter.docx")
def download_cover_letter(
    posting_id: int,
    profile_id: str = Depends(get_profile_id),
    tracker: Tracker = Depends(get_tracker),
) -> FileResponse:
    path = _render_for(
        posting_id, "cover-letter", profile_id, tracker,
    )
    return FileResponse(
        str(path), media_type=_DOCX_MIME, filename=path.name,
        content_disposition_type="attachment",
    )


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _screenshots_root(profile_id: str) -> Path:
    return (
        PROJECT_ROOT / "data" / profile_id
        / "applications" / "screenshots"
    ).resolve()


@router.get("/api/screenshots/{application_id}.png")
def confirmation_screenshot(
    application_id: int,
    profile_id: str = Depends(get_profile_id),
    tracker: Tracker = Depends(get_tracker),
) -> FileResponse:
    """Serve the confirmation screenshot for an application.

    The application row stores screenshot_path as a project-root-
    relative string (e.g. data/default/applications/screenshots/...).
    We resolve it, then verify it sits inside the expected
    screenshots dir before serving -- traversal protection.
    """
    row = tracker.get_application_by_id(application_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"application {application_id} not found",
        )
    rel = row.get("screenshot_path")
    if not rel:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no screenshot recorded for application "
                f"{application_id}"
            ),
        )
    candidate = (PROJECT_ROOT / rel).resolve()
    expected_root = _screenshots_root(profile_id)
    try:
        candidate.relative_to(expected_root)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail="screenshot path is outside the screenshots dir",
        )
    if not candidate.exists():
        raise HTTPException(
            status_code=404,
            detail=f"screenshot file missing on disk: {rel}",
        )
    return FileResponse(
        str(candidate),
        media_type="image/png",
        filename=candidate.name,
    )
