"""Spec 16 TASK 2 — tests for jobagent.platform.

Two layers:
  - Pure-function tests for _assign_tier (no mocks needed).
  - Mock-based tests for detection paths that shell out
    (nvidia-smi, sysctl, /proc/cpuinfo).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from jobagent import platform as jpf


# --- _assign_tier (pure) -----------------------------------------

@pytest.mark.parametrize(
    "gpu_type,vram,ram,expected",
    [
        # CPU-only ⇒ minimum, regardless of RAM.
        (None, None, 8.0,   "minimum"),
        (None, None, 64.0,  "minimum"),
        # NVIDIA dGPU buckets.
        ("nvidia", 12.0, 32.0, "full"),
        ("nvidia",  8.0, 16.0, "full"),
        ("nvidia",  6.0, 16.0, "recommended"),
        ("nvidia",  4.0, 16.0, "recommended"),
        ("nvidia",  2.0, 16.0, "minimum"),
        # Apple Silicon: tier comes from RAM, not VRAM.
        ("apple_silicon", None, 32.0, "recommended"),
        ("apple_silicon", None, 16.0, "recommended"),
        ("apple_silicon", None,  8.0, "minimum"),
        # AMD GPU detected but VRAM not parsed (0.0) ⇒ minimum.
        ("amd", 0.0, 16.0, "minimum"),
        ("amd", 8.0, 16.0, "full"),
    ],
)
def test_assign_tier(gpu_type, vram, ram, expected):
    assert jpf._assign_tier(gpu_type, vram, ram) == expected


# --- _detect_os_name --------------------------------------------

@pytest.mark.parametrize(
    "platform_str,expected",
    [
        ("win32",  "Windows"),
        ("darwin", "macOS"),
        ("linux",  "Linux"),
        ("linux2", "Linux"),
        # An unknown platform string passes through verbatim — better
        # than silently lying.
        ("freebsd", "freebsd"),
    ],
)
def test_detect_os_name(platform_str, expected):
    with patch.object(jpf.sys, "platform", platform_str):
        assert jpf._detect_os_name() == expected


# --- _detect_nvidia_gpu -----------------------------------------

def _fake_completed(stdout: str, returncode: int = 0):
    """Build the subprocess.CompletedProcess shape that _run_text
    expects."""
    class _CP:
        pass
    cp = _CP()
    cp.stdout = stdout
    cp.returncode = returncode
    return cp


def test_detect_nvidia_returns_none_when_smi_missing():
    with patch.object(jpf.shutil, "which", return_value=None):
        assert jpf._detect_nvidia_gpu() is None


def test_detect_nvidia_parses_model_and_vram():
    # nvidia-smi --format=csv,noheader,nounits returns lines like:
    #   "NVIDIA GeForce RTX 4090, 24564"
    # (memory in MiB).
    with patch.object(jpf.shutil, "which", return_value="nvidia-smi"), \
         patch.object(jpf.subprocess, "run",
                      return_value=_fake_completed(
                          "NVIDIA GeForce RTX 4070, 12282\n")):
        result = jpf._detect_nvidia_gpu()
    assert result is not None
    model, vram_gb = result
    assert model == "NVIDIA GeForce RTX 4070"
    # 12282 MiB ≈ 12.0 GiB.
    assert 11.9 < vram_gb < 12.1


def test_detect_nvidia_handles_unparseable_vram():
    with patch.object(jpf.shutil, "which", return_value="nvidia-smi"), \
         patch.object(jpf.subprocess, "run",
                      return_value=_fake_completed("Bad GPU, NaN\n")):
        assert jpf._detect_nvidia_gpu() is None


def test_detect_nvidia_handles_subprocess_failure():
    with patch.object(jpf.shutil, "which", return_value="nvidia-smi"), \
         patch.object(jpf.subprocess, "run",
                      side_effect=FileNotFoundError):
        assert jpf._detect_nvidia_gpu() is None


# --- _detect_apple_silicon --------------------------------------

def test_detect_apple_silicon_skips_x86():
    with patch.object(jpf._stdlib_platform, "machine", return_value="x86_64"):
        assert jpf._detect_apple_silicon() is None


def test_detect_apple_silicon_returns_model_when_arm64():
    with patch.object(jpf._stdlib_platform, "machine", return_value="arm64"), \
         patch.object(jpf.subprocess, "run",
                      return_value=_fake_completed("Mac15,7\n")):
        result = jpf._detect_apple_silicon()
    assert result is not None
    model, vram = result
    assert "Mac15,7" in model
    # Unified memory: vram is None, tier reads ram instead.
    assert vram is None


# --- _detect_gpu (composite) ------------------------------------

def test_detect_gpu_returns_none_when_nothing_present():
    with patch.object(jpf, "_detect_nvidia_gpu", return_value=None), \
         patch.object(jpf, "_detect_apple_silicon", return_value=None), \
         patch.object(jpf, "_detect_amd_gpu", return_value=None):
        assert jpf._detect_gpu() == (None, None, None)


def test_detect_gpu_prefers_nvidia_over_apple():
    with patch.object(jpf, "_detect_nvidia_gpu",
                      return_value=("RTX 3080", 10.0)), \
         patch.object(jpf, "_detect_apple_silicon",
                      return_value=("Apple Silicon", None)):
        assert jpf._detect_gpu() == ("nvidia", "RTX 3080", 10.0)


# --- detect_hardware (smoke) ------------------------------------

def test_detect_hardware_returns_valid_snapshot():
    """End-to-end smoke: every field is populated and the tier is
    one of the three allowed values."""
    info = jpf.detect_hardware()
    assert info.os_name in ("Windows", "macOS", "Linux") or info.os_name
    assert info.cpu_cores >= 1
    assert info.ram_total_gb > 0
    assert info.ram_available_gb > 0
    assert info.ram_available_gb <= info.ram_total_gb
    assert info.disk_free_gb >= 0
    assert info.tier in ("minimum", "recommended", "full")
    # gpu_type and gpu_model are either both populated or both None.
    if info.gpu_type is None:
        assert info.gpu_model is None
    else:
        assert info.gpu_model is not None


def test_detect_hardware_is_frozen():
    info = jpf.detect_hardware()
    with pytest.raises(AttributeError):
        info.tier = "full"  # type: ignore[misc]
