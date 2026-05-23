"""Spec 16 TASK 1 — tests for the `job-agent` CLI entry point.

These exercise the argparse plumbing only: --help returns 0,
each registered subcommand parses correctly, planned commands
return exit code 2 with a graceful message, and the
script-passthrough handlers actually route to the right module.
"""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from jobagent import cli


def test_no_args_prints_help_and_succeeds(capsys):
    rc = cli.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Local-first AI job search agent" in out
    assert "start" in out
    assert "run" in out


def test_help_flag_succeeds(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--help"])
    assert excinfo.value.code == 0


def test_unknown_command_errors():
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["bogus-not-a-command"])
    assert excinfo.value.code == 2


def test_every_planned_command_exits_2_with_pointer(capsys):
    # Each planned-but-not-implemented command should print its
    # spec pointer to stderr and exit 2, never raise.
    planned = [
        "setup", "gaps", "export",
    ]
    for name in planned:
        rc = cli.main([name])
        err = capsys.readouterr().err
        assert rc == 2, f"{name} returned {rc}, expected 2"
        assert "planned for Spec" in err, (
            f"{name} stderr missing spec pointer: {err!r}"
        )


def test_install_service_passes_time_and_profile():
    with patch("jobagent.services.install_service") as m:
        rc = cli.main([
            "install-service", "--time", "03:15",
            "--profile", "default",
        ])
    assert rc == 0
    m.assert_called_once_with(schedule_time="03:15", profile="default")


def test_install_service_defaults_when_no_flags():
    with patch("jobagent.services.install_service") as m:
        rc = cli.main(["install-service"])
    assert rc == 0
    m.assert_called_once_with(schedule_time="02:00", profile="default")


def test_install_service_reports_servicerror():
    from jobagent.services import ServiceError
    with patch(
        "jobagent.services.install_service",
        side_effect=ServiceError("simulated"),
    ):
        rc = cli.main(["install-service"])
    assert rc == 1


def test_uninstall_service_invokes_function():
    with patch("jobagent.services.uninstall_service") as m:
        rc = cli.main(["uninstall-service"])
    assert rc == 0
    m.assert_called_once_with()


def test_stop_walks_processes(capsys):
    # `stop` enumerates psutil.process_iter and terminates anything
    # listening on 8000 or 8080. With process_iter mocked empty,
    # we should get a clean "no process found" message and exit 0.
    with patch("psutil.process_iter", return_value=[]):
        rc = cli.main(["stop"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "No dashboard process" in out


def test_status_prints_hardware_snapshot(capsys):
    rc = cli.main(["status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "OS:" in out
    assert "CPU:" in out
    assert "RAM:" in out
    assert "Recommended tier:" in out


def test_start_invokes_run_app_main():
    # `job-agent start` is a thin wrapper around scripts/run_app.py
    # — verify the wrapper calls into the right module without
    # actually spawning uvicorn + PyWebView.
    with patch("scripts.run_app.main", return_value=0) as m:
        rc = cli.main(["start"])
    assert rc == 0
    m.assert_called_once_with()


def test_run_passes_through_extra_args():
    with patch("scripts.run_daily.main", return_value=0) as m:
        rc = cli.main(["run", "--profile", "default", "--dry-run"])
    assert rc == 0
    m.assert_called_once_with(["--profile", "default", "--dry-run"])


def test_expand_passes_through_extra_args():
    with patch("scripts.run_expansion.main", return_value=0) as m:
        rc = cli.main(["expand", "--lookback-days", "30"])
    assert rc == 0
    m.assert_called_once_with(["--lookback-days", "30"])


def test_apply_passes_through_extra_args():
    with patch("scripts.apply.main", return_value=0) as m:
        rc = cli.main(["apply", "--posting-id", "42"])
    assert rc == 0
    m.assert_called_once_with(["--posting-id", "42"])


def test_gmail_auth_passes_through_extra_args():
    with patch("scripts.gmail_auth.main", return_value=0) as m:
        rc = cli.main(["gmail-auth", "--profile", "default"])
    assert rc == 0
    m.assert_called_once_with(["--profile", "default"])


def test_all_14_commands_registered():
    # Spec 16 TASK 1 lists 14 subcommands. Keep this assertion in
    # sync if the spec or the command list changes.
    expected = {
        "start", "stop", "status", "setup", "run", "expand",
        "gaps", "worksheet", "apply", "install-service",
        "uninstall-service", "gmail-auth", "check-expiry",
        "export",
    }
    assert set(cli.COMMANDS.keys()) == expected


def test_command_handlers_are_callable():
    for name, cmd in cli.COMMANDS.items():
        assert callable(cmd.handler), f"{name}: handler not callable"
        assert cmd.help, f"{name}: empty help string"


def test_run_help_passes_through_to_underlying_script():
    # `job-agent run --help` should reach run_daily.main with --help
    # in args.extra; we don't want top-level argparse to intercept it.
    with patch("scripts.run_daily.main", return_value=0) as m:
        rc = cli.main(["run", "--help"])
    assert rc == 0
    m.assert_called_once_with(["--help"])
