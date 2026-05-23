"""Wrap engine.resume.docx_renderer for the dashboard.

The renderer produces .docx with its canonical filename
({First}{Last}{Resume|CoverLetter}{Company}{YYYYMMDD}.docx). The
dashboard wants stable, posting-keyed names in `pending/` so previews
and uploads can be located by id:

  data/{profile}/applications/pending/resume_{posting_id}.docx
  data/{profile}/applications/pending/cover_letter_{posting_id}.docx

After the renderer writes its file we move it to the stable name.
The canonical name is recoverable from the application row at submit
time if we ever want it.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from engine.resume.docx_renderer import ATSResumeRenderer

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def pending_dir(profile_id: str) -> Path:
    return (
        PROJECT_ROOT / "data" / profile_id / "applications" / "pending"
    )


def resume_pending_path(profile_id: str, posting_id: int) -> Path:
    return pending_dir(profile_id) / f"resume_{posting_id}.docx"


def cover_letter_pending_path(
    profile_id: str, posting_id: int,
) -> Path:
    return pending_dir(profile_id) / f"cover_letter_{posting_id}.docx"


class RenderService:
    def __init__(self, renderer: Optional[ATSResumeRenderer] = None):
        self._renderer = renderer or ATSResumeRenderer()

    def render_resume(
        self,
        *,
        profile_id: str,
        posting_id: int,
        content: str,
        posting: dict,
    ) -> Path:
        out_dir = pending_dir(profile_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        rendered = self._renderer.render_resume(
            content=content, posting=posting, output_dir=out_dir,
        )
        target = resume_pending_path(profile_id, posting_id)
        if rendered != target:
            if target.exists():
                target.unlink()
            shutil.move(str(rendered), str(target))
        return target

    def render_cover_letter(
        self,
        *,
        profile_id: str,
        posting_id: int,
        content: str,
        posting: dict,
        candidate_name: str,
    ) -> Path:
        out_dir = pending_dir(profile_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        rendered = self._renderer.render_cover_letter(
            content=content, posting=posting,
            candidate_name=candidate_name, output_dir=out_dir,
        )
        target = cover_letter_pending_path(profile_id, posting_id)
        if rendered != target:
            if target.exists():
                target.unlink()
            shutil.move(str(rendered), str(target))
        return target
