"""Unit tests for scripts/prune_screenshots.py."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prune_screenshots import prune  # noqa: E402


def _make_file(path: Path, age_days: float, size: int = 100) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    age_seconds = age_days * 86_400
    target_mtime = time.time() - age_seconds
    os.utime(path, (target_mtime, target_mtime))
    return path


def test_deletes_files_older_than_keep_days(tmp_path):
    old = _make_file(tmp_path / "old.png", age_days=120, size=1000)
    recent = _make_file(tmp_path / "recent.png", age_days=10, size=500)
    deleted, bytes_freed = prune(tmp_path, keep_days=90)
    assert deleted == 1
    assert bytes_freed == 1000
    assert not old.exists()
    assert recent.exists()


def test_keeps_recent_files(tmp_path):
    _make_file(tmp_path / "a.png", age_days=10)
    _make_file(tmp_path / "b.png", age_days=30)
    _make_file(tmp_path / "c.png", age_days=89)
    deleted, _ = prune(tmp_path, keep_days=90)
    assert deleted == 0
    assert len(list(tmp_path.iterdir())) == 3


def test_dry_run_deletes_nothing(tmp_path):
    old = _make_file(tmp_path / "old.png", age_days=120, size=2048)
    deleted, bytes_freed = prune(
        tmp_path, keep_days=90, dry_run=True,
    )
    assert deleted == 1
    assert bytes_freed == 2048
    assert old.exists()  # still on disk


def test_handles_missing_directory(tmp_path):
    deleted, bytes_freed = prune(
        tmp_path / "does-not-exist", keep_days=90,
    )
    assert deleted == 0
    assert bytes_freed == 0
