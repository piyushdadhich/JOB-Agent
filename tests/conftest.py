"""Test session bootstrap.

Two responsibilities both keyed to the "fresh-clone friendly" goal
of the v1 release:

  1. Several tests read config/profiles/default.yaml + default_
     applicant.yaml directly (test_profile_loader, test_run_email_
     monitor_cli, the docs examples). The .yaml files are gitignored
     now — only the .example templates ship in the repo. If the live
     file is missing, copy it from the template before any test runs
     so the integration-style tests have something to read.

  2. (Future) any other session-scoped setup the harness needs that
     pytest fixtures can't express cleanly because they run too late.
"""
from __future__ import annotations

import shutil
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def pytest_configure(config) -> None:
    profiles_dir = _PROJECT_ROOT / "config" / "profiles"
    for stem in ("default.yaml", "default_applicant.yaml"):
        live = profiles_dir / stem
        template = profiles_dir / f"{stem}.example"
        if not live.exists() and template.exists():
            shutil.copyfile(template, live)
