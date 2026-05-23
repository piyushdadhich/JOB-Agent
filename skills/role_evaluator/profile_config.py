"""Read-only loader for config/profiles/{id}.yaml.

Exposes the config-level fields that the Stage 2a evaluator needs and
that don't belong in InventoryTool — geography (which cities/regions
this profile is willing to work in), the salary floor, the
remote-acceptable flag, and config-only employer exclusions (e.g.
RailCo, PayCo — companies that never appeared in the inventory and so
have no inventory-side hard_exclusion entry).

The yaml uses `target_cities` for the geography list (existing
convention in this repo); the Python API exposes it under the cleaner
name `.geography`.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _profile_path(profile_id: str) -> Path:
    return PROJECT_ROOT / "config" / "profiles" / f"{profile_id}.yaml"


class ProfileConfig:
    """Read-only loader for config/profiles/{id}.yaml."""

    def __init__(self, profile_id: str) -> None:
        path = _profile_path(profile_id)
        if not path.exists():
            raise FileNotFoundError(
                f"Profile config not found: {path}"
            )
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        self._data = loaded if isinstance(loaded, dict) else {}
        self._profile_id = profile_id

    @property
    def profile_id(self) -> str:
        return self._profile_id

    @property
    def geography(self) -> list[str]:
        """Cities/regions this profile is willing to work in.

        Sourced from the yaml's `target_cities` key. Empty list when
        the key is missing.
        """
        return list(self._data.get("target_cities", []))

    @property
    def config_hard_exclusions(self) -> list[str]:
        """Config-level employer exclusions.

        Distinct from `InventoryTool.get_hard_exclusions()`: these
        are companies the user wants to skip that never appeared in
        the career inventory (e.g. RailCo, PayCo). Stage 2a unions
        the two lists.
        """
        return list(self._data.get("config_hard_exclusions", []))

    @property
    def salary_min(self) -> Optional[int]:
        """The salary floor (yaml's `salary.floor`). None if missing."""
        salary = self._data.get("salary") or {}
        floor = salary.get("floor")
        return int(floor) if floor is not None else None

    @property
    def remote_acceptable(self) -> bool:
        """Whether postings with no stated location should pass the
        geography rule. Defaults to True when the key is absent.
        """
        return bool(self._data.get("remote_acceptable", True))
