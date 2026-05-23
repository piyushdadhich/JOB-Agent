"""Spec 16 TASK 2 — cross-platform hardware detection.

`detect_hardware()` returns a :class:`HardwareInfo` snapshot the
onboarding wizard, LLM router, and `job-agent status` command use
to decide which model tier the user can comfortably run locally.

Detection paths:
  CPU model:  platform.processor() on Windows; sysctl on macOS;
              /proc/cpuinfo on Linux; "Unknown CPU" fallback.
  RAM:        psutil (cross-platform).
  GPU:        nvidia-smi first (Windows + Linux); Apple Silicon via
              uname machine code (macOS); rocm-smi (Linux AMD).
  Disk free:  shutil.disk_usage on the repo root (where data/ and
              logs grow).
  Tier:       _assign_tier — pure function of gpu_type + vram + ram,
              kept separate so it's unit-testable without touching
              real hardware.
"""
from __future__ import annotations

import os
import platform as _stdlib_platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import psutil


@dataclass(frozen=True)
class HardwareInfo:
    os_name: str            # "Windows" | "macOS" | "Linux" | raw sys.platform
    os_version: str
    cpu_model: str
    cpu_cores: int
    ram_total_gb: float
    ram_available_gb: float
    gpu_model: Optional[str]
    gpu_vram_gb: Optional[float]
    gpu_type: Optional[str]  # "nvidia" | "apple_silicon" | "amd"
    disk_free_gb: float
    tier: str               # "minimum" | "recommended" | "full"


# --- Public entry point ------------------------------------------

def detect_hardware() -> HardwareInfo:
    """One-shot snapshot of the host's relevant capabilities."""
    vm = psutil.virtual_memory()
    ram_total_gb = vm.total / (1024 ** 3)
    ram_available_gb = vm.available / (1024 ** 3)

    gpu_type, gpu_model, gpu_vram_gb = _detect_gpu()
    tier = _assign_tier(gpu_type, gpu_vram_gb, ram_total_gb)

    return HardwareInfo(
        os_name=_detect_os_name(),
        os_version=_stdlib_platform.release() or "unknown",
        cpu_model=_detect_cpu_model(),
        cpu_cores=os.cpu_count() or 1,
        ram_total_gb=round(ram_total_gb, 2),
        ram_available_gb=round(ram_available_gb, 2),
        gpu_model=gpu_model,
        gpu_vram_gb=gpu_vram_gb,
        gpu_type=gpu_type,
        disk_free_gb=round(_detect_disk_free(), 2),
        tier=tier,
    )


# --- OS / CPU detection ------------------------------------------

_OS_MAP = {
    "win32":  "Windows",
    "darwin": "macOS",
    "linux":  "Linux",
    "linux2": "Linux",
}


def _detect_os_name() -> str:
    return _OS_MAP.get(sys.platform, sys.platform)


def _detect_cpu_model() -> str:
    # platform.processor() returns the CPU brand string on Windows
    # but is often empty on macOS / Linux. Fall back to platform-
    # specific sources before giving up.
    p = (_stdlib_platform.processor() or "").strip()
    if p:
        return p

    if sys.platform == "darwin":
        out = _run_text(["sysctl", "-n", "machdep.cpu.brand_string"])
        if out:
            return out
    elif sys.platform.startswith("linux"):
        try:
            cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8")
        except OSError:
            cpuinfo = ""
        for line in cpuinfo.splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()

    return "Unknown CPU"


# --- GPU detection -----------------------------------------------

def _detect_gpu() -> tuple[Optional[str], Optional[str], Optional[float]]:
    """Return (type, model, vram_gb). All three are None if no GPU
    is detected. vram_gb is None for Apple Silicon (unified memory
    — caller reads ram_total_gb instead)."""
    nvidia = _detect_nvidia_gpu()
    if nvidia is not None:
        model, vram = nvidia
        return ("nvidia", model, vram)

    if sys.platform == "darwin":
        apple = _detect_apple_silicon()
        if apple is not None:
            model, vram = apple
            return ("apple_silicon", model, vram)

    amd = _detect_amd_gpu()
    if amd is not None:
        model, vram = amd
        return ("amd", model, vram)

    return (None, None, None)


def _detect_nvidia_gpu() -> Optional[tuple[str, float]]:
    """Return (model, vram_gb) if nvidia-smi reports a GPU."""
    if not shutil.which("nvidia-smi"):
        return None
    out = _run_text([
        "nvidia-smi",
        "--query-gpu=name,memory.total",
        "--format=csv,noheader,nounits",
    ])
    if not out:
        return None
    first_line = out.splitlines()[0]
    parts = [p.strip() for p in first_line.split(",")]
    if len(parts) != 2:
        return None
    import math
    model = parts[0]
    try:
        # nvidia-smi reports memory in MiB with --nounits.
        vram_mib = float(parts[1])
    except ValueError:
        return None
    # float("nan") and float("inf") both parse — guard against them
    # so a junk driver output doesn't poison tier assignment.
    if not math.isfinite(vram_mib) or vram_mib <= 0:
        return None
    return (model, round(vram_mib / 1024, 2))


def _detect_apple_silicon() -> Optional[tuple[str, Optional[float]]]:
    """Return (model, None) if running on Apple Silicon (M1+).

    Apple Silicon GPUs share system memory (unified memory
    architecture), so vram_gb is not separately reported. The
    tier assignment falls back to ram_total_gb in that case.
    """
    if _stdlib_platform.machine().lower() not in ("arm64", "aarch64"):
        return None
    model_str = _run_text(["sysctl", "-n", "hw.model"]) or "Apple Silicon"
    if model_str and model_str != "Apple Silicon":
        return (f"Apple Silicon ({model_str})", None)
    return (model_str, None)


def _detect_amd_gpu() -> Optional[tuple[str, float]]:
    """Return (model, vram_gb=0.0) if rocm-smi is on PATH.

    rocm-smi's JSON output varies by version; we report presence
    without trying to parse VRAM precisely. AMD GPUs with vram=0
    get "minimum" tier — users on ROCm-supported cards can override
    via the LLM-routing config after onboarding.
    """
    if not shutil.which("rocm-smi"):
        return None
    return ("AMD GPU", 0.0)


# --- Disk -------------------------------------------------------

def _detect_disk_free() -> float:
    """Disk free in GB on the volume holding the repo root."""
    project_root = Path(__file__).resolve().parent.parent
    try:
        usage = shutil.disk_usage(str(project_root))
    except OSError:
        return 0.0
    return usage.free / (1024 ** 3)


# --- Tier classifier (pure) --------------------------------------

def _assign_tier(
    gpu_type: Optional[str],
    gpu_vram_gb: Optional[float],
    ram_total_gb: float,
) -> str:
    """Map (gpu_type, gpu_vram_gb, ram_total_gb) → tier string.

    Tiers:
      minimum     — CPU-only, or a dGPU too small for local 7B models.
                    Run the API-LLM path; expect ~24s/eval on the
                    Gemma 4 31B free tier.
      recommended — 4-8 GB VRAM dGPU, or Apple Silicon with 16GB+
                    unified memory. Can run a quantised 7B model
                    locally for evaluation; falls back to API for
                    resume/cover-letter generation.
      full        — 8 GB+ VRAM dGPU. Full local pipeline including
                    resume generation runs without API calls.
    """
    if gpu_type is None:
        return "minimum"
    if gpu_type == "apple_silicon":
        # Unified memory: GPU shares system RAM, so tier comes from
        # total system RAM. 16 GB+ is comfortably above the 4-bit
        # 7B-model threshold; smaller Apple Silicon machines fall
        # back to API LLMs.
        return "recommended" if ram_total_gb >= 16 else "minimum"
    if gpu_vram_gb is not None:
        if gpu_vram_gb >= 8:
            return "full"
        if gpu_vram_gb >= 4:
            return "recommended"
    return "minimum"


# --- subprocess helper -------------------------------------------

def _run_text(cmd: list[str], timeout: int = 5) -> str:
    """Run a command, return its stripped stdout, or "" on failure.

    Centralised so every detection path handles
    FileNotFoundError / TimeoutExpired the same way and never
    raises out to the public API — a missing tool is just "no
    information available".
    """
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if out.returncode != 0:
        return ""
    return out.stdout.strip()
