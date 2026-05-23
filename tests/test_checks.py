"""Spec 16 TASK 4 — tests for jobagent.checks."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from jobagent import checks


# --- _check_python_version --------------------------------------

def test_python_check_passes_on_current_interpreter():
    # The test runner is on 3.12+ per pyproject's requires-python.
    assert checks._check_python_version() is None


def test_python_check_flags_old_version():
    class FakeVersionInfo:
        def __init__(self, major, minor, micro=0):
            self.major = major
            self.minor = minor
            self.micro = micro
        def __ge__(self, other):
            return (self.major, self.minor) >= other
        def __lt__(self, other):
            return (self.major, self.minor) < other

    fake = FakeVersionInfo(3, 11, 9)
    with patch.object(checks.sys, "version_info", fake), \
         patch.object(
             checks._stdlib_platform, "python_version",
             return_value="3.11.9",
         ):
        issue = checks._check_python_version()
    assert issue is not None
    assert issue.severity == "fatal"
    assert issue.found == "3.11.9"


# --- _check_ollama ----------------------------------------------

def test_ollama_check_passes_when_on_path():
    with patch.object(checks.shutil, "which", return_value="/usr/bin/ollama"):
        assert checks._check_ollama() is None


def test_ollama_check_flags_when_missing():
    with patch.object(checks.shutil, "which", return_value=None):
        issue = checks._check_ollama()
    assert issue is not None
    assert issue.severity == "optional"
    assert issue.name == "Ollama"
    assert "ollama.com" in (issue.install_hint or "")


# --- _check_playwright_browsers ---------------------------------

def test_playwright_check_passes_when_browser_launches():
    # Mock the sync_playwright context manager to no-op cleanly.
    fake_browser = type("B", (), {"close": lambda self: None})()
    fake_chromium = type("C", (), {"launch": lambda self, **kw: fake_browser})()
    fake_p = type("P", (), {"chromium": fake_chromium})()

    class FakeCM:
        def __enter__(self): return fake_p
        def __exit__(self, *a): return False

    with patch("playwright.sync_api.sync_playwright", return_value=FakeCM()):
        assert checks._check_playwright_browsers() is None


def test_playwright_check_flags_when_browser_missing():
    # Mock sync_playwright to raise on launch — simulates the
    # "browser binary not installed" case Playwright emits.
    class FakeBrokenCM:
        def __enter__(self):
            class P:
                class chromium:
                    @staticmethod
                    def launch(**kw):
                        raise RuntimeError("browser binary missing")
            return P()
        def __exit__(self, *a): return False

    with patch(
        "playwright.sync_api.sync_playwright",
        return_value=FakeBrokenCM(),
    ):
        issue = checks._check_playwright_browsers()
    assert issue is not None
    assert issue.severity == "optional"
    assert "chromium" in (issue.install_hint or "").lower()


# --- check_dependencies (composite) -----------------------------

def test_check_dependencies_returns_list():
    # On the dev machine: Python is fine; Ollama + Playwright
    # browsers may or may not be installed. Either way, the
    # function must return a list (never raise).
    result = checks.check_dependencies()
    assert isinstance(result, list)
    for issue in result:
        assert isinstance(issue, checks.DependencyIssue)
        assert issue.severity in ("fatal", "optional")


def test_check_dependencies_empty_when_everything_present():
    # Force every individual check to return None — the composite
    # should report no issues.
    with patch.object(checks, "_check_python_version", return_value=None), \
         patch.object(checks, "_check_ollama", return_value=None), \
         patch.object(
             checks, "_check_playwright_browsers", return_value=None,
         ):
        assert checks.check_dependencies() == []


def test_check_dependencies_aggregates_multiple():
    fatal = checks.DependencyIssue(
        name="Python", required=">= 3.12", found="3.11",
        severity="fatal",
    )
    optional = checks.DependencyIssue(
        name="Ollama", required="any", found=None,
        severity="optional",
    )
    with patch.object(
            checks, "_check_python_version", return_value=fatal,
         ), \
         patch.object(
            checks, "_check_ollama", return_value=optional,
         ), \
         patch.object(
            checks, "_check_playwright_browsers", return_value=None,
         ):
        result = checks.check_dependencies()
    assert result == [fatal, optional]


# --- format_issues ----------------------------------------------

def test_format_issues_empty():
    assert checks.format_issues([]) == ""


def test_format_issues_renders_severity_and_hint():
    issue = checks.DependencyIssue(
        name="Ollama", required="any", found=None,
        severity="optional",
        install_hint="https://ollama.com/download",
    )
    text = checks.format_issues([issue])
    assert "[OPTIONAL]" in text
    assert "Ollama" in text
    assert "not found" in text
    assert "https://ollama.com/download" in text
