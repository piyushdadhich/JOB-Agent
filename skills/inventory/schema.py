"""Pydantic schema for the inventory extract.

InventoryExtract is the canonical machine-readable shape produced
by Gemma 4 from career_inventory.md. Bumping schema_version
invalidates all caches and history files.
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

SeniorityLevel = Literal[
    "junior", "mid", "senior", "lead", "manager", "director", "vp",
]

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class _StripBase(BaseModel):
    """All string inputs have leading/trailing whitespace stripped."""
    model_config = ConfigDict(str_strip_whitespace=True)


class Role(_StripBase):
    id: str = Field(min_length=1)
    employer: str = Field(min_length=1)
    title: str = Field(min_length=1)
    start_date: str = Field(min_length=1)
    end_date: Optional[str] = None
    location: Optional[str] = None
    function: str = Field(min_length=1)
    industry: Optional[str] = None
    seniority_level: SeniorityLevel
    skill_clusters: list[str] = Field(default_factory=list)
    evidence_phrases: list[str] = Field(default_factory=list)
    outcomes: list[str] = Field(default_factory=list)
    cultural_signals: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)


class TransferableSkillCluster(_StripBase):
    name: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    evidence_role_ids: list[str] = Field(default_factory=list)


class Trajectory(_StripBase):
    current_level: str = Field(min_length=1)
    target_levels: list[str] = Field(default_factory=list)
    advance_step: str = Field(min_length=1)
    lateral_step: str = Field(min_length=1)
    avoid: list[str] = Field(default_factory=list)


class InventoryExtract(_StripBase):
    schema_version: int = 1
    profile_id: str = Field(min_length=1)
    source_hash: str
    extracted_at: str = Field(min_length=1)
    extractor_model: str = Field(min_length=1)
    roles: list[Role] = Field(min_length=1)
    transferable_skill_clusters: list[TransferableSkillCluster] = Field(
        default_factory=list,
    )
    trajectory: Trajectory
    hard_exclusions: list[str] = Field(default_factory=list)
    geography: list[str] = Field(default_factory=list)

    @field_validator("source_hash")
    @classmethod
    def _check_hex64(cls, v: str) -> str:
        if not _HEX64.fullmatch(v):
            raise ValueError(
                "source_hash must be a 64-char lowercase hex digest"
            )
        return v

    @model_validator(mode="after")
    def _check_evidence_role_ids(self) -> "InventoryExtract":
        known = {r.id for r in self.roles}
        for tsc in self.transferable_skill_clusters:
            for rid in tsc.evidence_role_ids:
                if rid not in known:
                    raise ValueError(
                        f"transferable_skill_cluster '{tsc.name}' "
                        f"references unknown role id '{rid}'"
                    )
        return self
