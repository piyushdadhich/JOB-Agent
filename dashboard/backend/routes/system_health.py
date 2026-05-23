"""Spec 12 TASKS 2 + 3 — hardware-drift + fallback-history surfaces."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from dashboard.backend.deps import get_profile_id

router = APIRouter(prefix="/api/system", tags=["system"])


# --- Hardware drift -----------------------------------------------

class HardwareDriftResponse(BaseModel):
    current_tier: str
    stored_tier: str | None
    gpu_disappeared: bool
    gpu_appeared: bool
    drift_summary: str


@router.get("/hardware-check", response_model=HardwareDriftResponse)
def hardware_check(
    profile_id: str = Depends(get_profile_id),
) -> HardwareDriftResponse:
    from jobagent.platform import detect_hardware

    current = detect_hardware()
    stored = _read_stored_hardware(profile_id)

    stored_tier = (stored or {}).get("tier")
    stored_gpu_type = (stored or {}).get("gpu_type")

    gpu_disappeared = bool(stored_gpu_type) and not current.gpu_type
    gpu_appeared = (not stored_gpu_type) and bool(current.gpu_type)

    summary_bits: list[str] = []
    if gpu_disappeared:
        summary_bits.append(
            "GPU disappeared since last check — local LLM path "
            "may be unavailable."
        )
    if gpu_appeared:
        summary_bits.append(
            f"GPU detected ({current.gpu_model}); consider "
            "enabling local LLM paths."
        )
    if stored_tier and current.tier != stored_tier:
        summary_bits.append(
            f"Tier moved from {stored_tier} → {current.tier}."
        )
    if not summary_bits:
        summary_bits.append("No drift detected.")

    return HardwareDriftResponse(
        current_tier=current.tier,
        stored_tier=stored_tier,
        gpu_disappeared=gpu_disappeared,
        gpu_appeared=gpu_appeared,
        drift_summary=" ".join(summary_bits),
    )


def _project_root() -> Path:
    # routes/ → backend/ → dashboard/ → repo root.
    return Path(__file__).resolve().parents[3]


def _read_stored_hardware(profile_id: str) -> dict | None:
    """Read step 1 of setup_state.json — the onboarding wizard
    stashes the hardware tier + gpu_type after the user clicks
    Continue on the hardware step."""
    path = (
        _project_root() / "data" / profile_id / "setup_state.json"
    )
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return (state.get("steps") or {}).get("1")


# --- Fallback history -------------------------------------------

class FallbackHistoryRow(BaseModel):
    timestamp: str
    function_name: str
    success: bool
    provider: str | None
    fallbacks_used: int
    events: list[dict[str, Any]]


def _fallback_history_path(profile_id: str) -> Path:
    return (
        _project_root() / "data" / profile_id
        / "fallback_history.jsonl"
    )


def append_fallback_history(
    profile_id: str, entry: dict,
) -> Path:
    """Append a single FallbackHistoryEntry-as-dict to the per-
    profile JSONL log. Public so the engine.llm.fallback_chain
    caller can log directly without importing from the dashboard."""
    path = _fallback_history_path(profile_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return path


@router.get(
    "/fallback-history", response_model=list[FallbackHistoryRow],
)
def fallback_history(
    limit: int = 50,
    profile_id: str = Depends(get_profile_id),
) -> list[FallbackHistoryRow]:
    path = _fallback_history_path(profile_id)
    if not path.exists():
        return []
    rows: list[FallbackHistoryRow] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(raw)
            rows.append(FallbackHistoryRow(**payload))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return rows[-limit:]
