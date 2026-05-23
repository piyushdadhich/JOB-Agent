"""FastAPI dependency injection.

Production: each request opens its own Tracker (cheap — sqlite3 file
connection) and closes on teardown. Tests override `get_tracker` and
`get_profile_id` via `app.dependency_overrides[...]`.

The active profile id comes from the JOB_AGENT_PROFILE env var, with
"default" as the default for local-tool ergonomics.
"""
from __future__ import annotations

import os
from typing import Iterator

from engine.persistence.tracker import Tracker

DEFAULT_PROFILE_ID = "default"


def get_profile_id() -> str:
    return os.environ.get("JOB_AGENT_PROFILE", DEFAULT_PROFILE_ID)


def get_tracker() -> Iterator[Tracker]:
    profile_id = get_profile_id()
    tracker = Tracker(profile_id)
    try:
        yield tracker
    finally:
        tracker.close()
