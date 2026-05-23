"""Staleness detection for the inventory extract.

The skill regenerates the extract only when the source markdown
has changed. We use mtime (cheap) as a fast-path filter, but
verify with a content hash before declaring staleness -- this
guards against `touch` and `git checkout` resetting mtime
without changing content.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from skills.inventory.schema import InventoryExtract


def compute_source_hash(source_path: Path) -> str:
    """Return SHA-256 hex digest of source_path's bytes."""
    h = hashlib.sha256()
    with source_path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_extract_hash(extract_path: Path) -> Optional[str]:
    try:
        data = json.loads(extract_path.read_text(encoding="utf-8"))
        return data.get("source_hash")
    except (OSError, json.JSONDecodeError):
        return None


def is_stale(
    source_path: Path,
    extract_path: Path,
) -> tuple[bool, str]:
    """Return (is_stale, reason).

    Stale when:
      - extract_path does not exist
      - source mtime > extract mtime AND content hash differs
        from the extract's recorded source_hash

    A touch / git checkout that resets mtime but not content is
    correctly diagnosed as not-stale.
    """
    if not extract_path.exists():
        return True, "extract does not exist"

    src_mtime = source_path.stat().st_mtime
    ext_mtime = extract_path.stat().st_mtime
    if src_mtime <= ext_mtime:
        return False, (
            "source mtime not newer than extract; "
            "no regeneration needed"
        )

    cur_hash = compute_source_hash(source_path)
    rec_hash = _read_extract_hash(extract_path)
    if rec_hash is None:
        return True, "extract missing or unreadable source_hash"
    if cur_hash != rec_hash:
        return True, (
            f"source content hash changed "
            f"({rec_hash[:8]}... -> {cur_hash[:8]}...)"
        )
    return False, (
        "source mtime newer but content unchanged "
        "(touch/checkout case)"
    )


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def archive_current_extract(
    extract_path: Path,
    history_dir: Path,
) -> Path:
    """Copy extract_path into history_dir/extract_{ts}.json."""
    history_dir.mkdir(parents=True, exist_ok=True)
    archived = history_dir / f"extract_{_timestamp()}.json"
    shutil.copy2(extract_path, archived)
    return archived


def write_diff(
    old_path: Path,
    new_path: Path,
    diff_dir: Path,
) -> Path:
    """Write a markdown diff between two extract JSON files.

    Compares structurally via the InventoryExtract pydantic
    model (not text-diff). Reports roles changed/added/removed,
    trajectory changes, and counts of other field deltas.
    """
    diff_dir.mkdir(parents=True, exist_ok=True)
    diff_path = diff_dir / f"diff_{_timestamp()}.md"

    old = InventoryExtract.model_validate_json(
        old_path.read_text(encoding="utf-8"),
    )
    new = InventoryExtract.model_validate_json(
        new_path.read_text(encoding="utf-8"),
    )

    old_roles = {r.id: r for r in old.roles}
    new_roles = {r.id: r for r in new.roles}
    added = sorted(new_roles.keys() - old_roles.keys())
    removed = sorted(old_roles.keys() - new_roles.keys())
    common = sorted(old_roles.keys() & new_roles.keys())
    changed = [
        rid for rid in common if old_roles[rid] != new_roles[rid]
    ]

    trajectory_changed = old.trajectory != new.trajectory

    other_changes = []
    for field in (
        "transferable_skill_clusters",
        "hard_exclusions",
        "geography",
    ):
        old_val = getattr(old, field)
        new_val = getattr(new, field)
        if old_val != new_val:
            other_changes.append(
                f"  - {field}: {len(old_val)} -> "
                f"{len(new_val)} entries"
            )

    lines = [
        "# Inventory diff",
        "",
        f"- Old extract: {old_path.name}",
        f"- New extract: {new_path.name}",
        f"- Old extracted_at: {old.extracted_at}",
        f"- New extracted_at: {new.extracted_at}",
        "",
        f"## Roles changed: {len(changed)}",
    ]
    for rid in changed:
        lines.append(f"  - {rid}")
    lines.append("")
    lines.append(f"## Roles added: {len(added)}")
    for rid in added:
        lines.append(f"  - {rid}")
    lines.append("")
    lines.append(f"## Roles removed: {len(removed)}")
    for rid in removed:
        lines.append(f"  - {rid}")
    lines.append("")
    lines.append(
        f"## Trajectory changed: "
        f"{'yes' if trajectory_changed else 'no'}"
    )
    lines.append("")
    lines.append(f"## Other changes: {len(other_changes)}")
    lines.extend(other_changes if other_changes else ["  - (none)"])

    diff_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return diff_path
