"""InventoryTool: read-only façade over the inventory pipeline.

Stage 2b, Stage 2a, and the pipeline orchestrator all use this class
instead of touching inventory_extract.json or inventory_summary.md
directly. Extraction itself is manual (architectural decision #36):
this class never triggers Claude Code; callers run the
get_extraction_prompt -> manual paste -> validate_extract workflow and
then call reload().

# TODO(deps): pin jinja2==3.1.6 once the repo introduces a
# requirements.txt or pyproject.toml. Deferred until that happens
# project-wide rather than spot-pinned here.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from skills.inventory.get_extraction_prompt import assemble_prompt
from skills.inventory.schema import (
    InventoryExtract,
    Role,
    TransferableSkillCluster,
)
from skills.inventory.staleness import is_stale as _staleness_is_stale


def _profile_dir(profile_id: str) -> Path:
    return PROJECT_ROOT / "data" / profile_id


def _source_path(profile_id: str) -> Path:
    return (
        PROJECT_ROOT / "source_materials" / profile_id
        / "career_inventory.md"
    )


def _extract_path(profile_id: str) -> Path:
    return _profile_dir(profile_id) / "inventory_extract.json"


def _summary_path(profile_id: str) -> Path:
    return _profile_dir(profile_id) / "inventory_summary.md"


class InventoryTool:
    """Read-only façade over a profile's inventory pipeline."""

    def __init__(self, profile_id: str) -> None:
        self._profile_id = profile_id
        if not _profile_dir(profile_id).exists():
            raise FileNotFoundError(
                f"Profile directory not found: "
                f"{_profile_dir(profile_id)}"
            )
        extract_path = _extract_path(profile_id)
        if not extract_path.exists():
            raise FileNotFoundError(
                f"Inventory extract not found at {extract_path}. "
                f"Run get_extraction_prompt.py and validate_extract.py "
                f"first."
            )
        self._extract = InventoryExtract.model_validate_json(
            extract_path.read_text(encoding="utf-8"),
        )
        self._summary: Optional[str] = None

    # --- staleness ---------------------------------------------------

    def is_stale(self) -> bool:
        stale, _reason = _staleness_is_stale(
            _source_path(self._profile_id),
            _extract_path(self._profile_id),
        )
        return stale

    def staleness_reason(self) -> Optional[str]:
        stale, reason = _staleness_is_stale(
            _source_path(self._profile_id),
            _extract_path(self._profile_id),
        )
        return reason if stale else None

    # --- read access -------------------------------------------------

    def get_extract(self) -> InventoryExtract:
        return self._extract

    def get_summary(self) -> str:
        if self._summary is None:
            summary_path = _summary_path(self._profile_id)
            if not summary_path.exists():
                raise FileNotFoundError(
                    f"Summary not found at {summary_path}. Run "
                    f"render_summary.py --profile {self._profile_id} "
                    f"first."
                )
            self._summary = summary_path.read_text(encoding="utf-8")
        return self._summary

    def get_role(self, role_id: str) -> Optional[Role]:
        for role in self._extract.roles:
            if role.id == role_id:
                return role
        return None

    def get_hard_exclusions(self) -> list[str]:
        return list(self._extract.hard_exclusions)

    def get_transferable_clusters(
        self,
    ) -> list[TransferableSkillCluster]:
        return list(self._extract.transferable_skill_clusters)

    # --- metadata ----------------------------------------------------

    @property
    def profile_id(self) -> str:
        return self._profile_id

    @property
    def source_hash(self) -> str:
        return self._extract.source_hash

    @property
    def extracted_at(self) -> datetime:
        return datetime.fromisoformat(self._extract.extracted_at)

    # --- workflow helpers --------------------------------------------

    def get_extraction_prompt(self) -> str:
        prompt, _hash = assemble_prompt(self._profile_id)
        return prompt

    def reload(self) -> None:
        extract_path = _extract_path(self._profile_id)
        if not extract_path.exists():
            raise FileNotFoundError(
                f"Inventory extract not found at {extract_path}."
            )
        self._extract = InventoryExtract.model_validate_json(
            extract_path.read_text(encoding="utf-8"),
        )
        self._summary = None
