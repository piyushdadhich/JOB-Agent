"""Spec 16 TASK 4 — first-run dependency checks.

`check_dependencies()` returns a list of :class:`DependencyIssue`
describing anything that's missing, out-of-spec, or otherwise
worth surfacing to the user before they hit a cryptic stack
trace deeper in the pipeline.

Severity buckets:
  fatal     — the install cannot work. Block startup.
  optional  — a feature group is unavailable but the core
              pipeline runs. Surface the install hint and move on.

Checks today:
  Python ≥ 3.12         (fatal)
  Ollama binary on PATH (optional — local LLM path)
  Playwright browsers   (optional — application form filler)

The onboarding wizard (Spec 1) calls this first; the CLI exposes
it as `job-agent status` adjacent metadata in future iterations.
"""
from __future__ import annotations

import platform as _stdlib_platform
import shutil
import sys
from dataclasses import dataclass
from typing import Literal, Optional

Severity = Literal["fatal", "optional"]


@dataclass(frozen=True)
class DependencyIssue:
    name: str
    required: str
    found: Optional[str]
    severity: Severity
    install_hint: Optional[str] = None


# --- Public entry point ------------------------------------------

def check_dependencies() -> list[DependencyIssue]:
    """Run every check and return the list of issues found.

    Returns an empty list if everything is fine.
    """
    issues: list[DependencyIssue] = []
    for check in (
        _check_python_version,
        _check_ollama,
        _check_playwright_browsers,
    ):
        issue = check()
        if issue is not None:
            issues.append(issue)
    return issues


# --- Individual checks -------------------------------------------

def _check_python_version() -> Optional[DependencyIssue]:
    if sys.version_info >= (3, 12):
        return None
    return DependencyIssue(
        name="Python",
        required=">= 3.12",
        found=_stdlib_platform.python_version(),
        severity="fatal",
        install_hint="Install Python 3.12+ from https://www.python.org/downloads/",
    )


def _check_ollama() -> Optional[DependencyIssue]:
    """Ollama is the local-LLM provider; only the `local` extra needs it.

    Detection: just check the binary is on PATH. We don't try to
    talk to the daemon — the user may not have it running yet, and
    that's not the same kind of problem as "missing install".
    """
    if shutil.which("ollama"):
        return None
    return DependencyIssue(
        name="Ollama",
        required="any version",
        found=None,
        severity="optional",
        install_hint=(
            "Install from https://ollama.com/download (only needed for "
            "the `local` LLM path; API-only installs can ignore)."
        ),
    )


def _check_playwright_browsers() -> Optional[DependencyIssue]:
    """Playwright needs a Chromium binary that gets installed via
    `playwright install chromium`. The Python package alone isn't
    enough — the browser binaries are a separate download.

    We try a lightweight launch + close. The application agent
    can't fill forms without this.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return DependencyIssue(
            name="playwright",
            required="any",
            found=None,
            severity="optional",
            install_hint=(
                "pip install playwright  (only needed for the "
                "application form filler)"
            ),
        )
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
    except Exception:
        return DependencyIssue(
            name="Playwright browsers",
            required="chromium",
            found=None,
            severity="optional",
            install_hint=(
                "playwright install chromium  (downloads the browser "
                "binaries the application agent drives)"
            ),
        )
    return None


# --- Pretty-printing --------------------------------------------

def format_issues(issues: list[DependencyIssue]) -> str:
    """Render the issue list as a multi-line string suitable for
    stderr. Empty input returns an empty string."""
    if not issues:
        return ""
    lines = []
    for issue in issues:
        prefix = "FATAL" if issue.severity == "fatal" else "OPTIONAL"
        found = issue.found or "not found"
        lines.append(f"  [{prefix}] {issue.name}: required {issue.required}, {found}")
        if issue.install_hint:
            lines.append(f"           hint: {issue.install_hint}")
    return "\n".join(lines)
