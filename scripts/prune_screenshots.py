"""Delete confirmation screenshots older than --keep-days.

Usage:
  python scripts/prune_screenshots.py --profile default
  python scripts/prune_screenshots.py --profile default --keep-days 90
  python scripts/prune_screenshots.py --profile default --dry-run
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def screenshots_dir(profile_id: str) -> Path:
    return (
        PROJECT_ROOT / "data" / profile_id
        / "applications" / "screenshots"
    )


def prune(
    directory: Path,
    keep_days: int,
    dry_run: bool = False,
    now: Optional[float] = None,
) -> tuple[int, int]:
    """Delete files older than keep_days. Returns (count, bytes)."""
    if not directory.exists():
        return 0, 0
    cutoff = (now or time.time()) - (keep_days * 86_400)
    deleted = 0
    bytes_freed = 0
    for f in sorted(directory.iterdir()):
        if not f.is_file():
            continue
        try:
            stat = f.stat()
        except OSError:
            continue
        if stat.st_mtime >= cutoff:
            continue
        bytes_freed += stat.st_size
        deleted += 1
        prefix = "WOULD DELETE" if dry_run else "DELETED"
        print(f"  {prefix}: {f.name}")
        if not dry_run:
            try:
                f.unlink()
            except OSError as e:
                print(f"    (unlink failed: {e})")
    return deleted, bytes_freed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--keep-days", type=int, default=90,
        help="files older than this many days are deleted",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    directory = screenshots_dir(args.profile)
    if not directory.exists():
        print(f"No screenshot dir at {directory}. Nothing to do.")
        return 0

    deleted, bytes_freed = prune(
        directory, args.keep_days, dry_run=args.dry_run,
    )
    suffix = " (dry run)" if args.dry_run else " freed"
    print(
        f"\n{deleted} file(s); "
        f"{bytes_freed / 1024:.1f} KB{suffix}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
