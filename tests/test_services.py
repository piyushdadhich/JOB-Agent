"""Spec 16 TASK 3 — tests for jobagent.services.

Subprocess calls are mocked; file writes go to tmp_path. We
verify the right commands are issued and the right unit files
are written for each platform, without actually touching
schtasks, launchctl, or systemctl on the test host.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jobagent import services


# --- _parse_schedule -------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [
        ("02:00", (2, 0)),
        ("00:00", (0, 0)),
        ("23:59", (23, 59)),
        ("9:30",  (9, 30)),
        ("  10:15  ", (10, 15)),
    ],
)
def test_parse_schedule_valid(value, expected):
    assert services._parse_schedule(value) == expected


@pytest.mark.parametrize(
    "value",
    ["25:00", "12:99", "not-a-time", "", "02:0", "2", "02-00"],
)
def test_parse_schedule_invalid(value):
    with pytest.raises(services.ServiceError):
        services._parse_schedule(value)


# --- install / uninstall / status routing ----------------------

@pytest.mark.parametrize(
    "platform,target",
    [
        ("win32",  "_install_windows"),
        ("darwin", "_install_macos"),
        ("linux",  "_install_linux"),
    ],
)
def test_install_routes_by_platform(platform, target):
    with patch.object(services.sys, "platform", platform), \
         patch.object(services, target) as called, \
         patch.object(services, "_install_windows" if target != "_install_windows" else "_install_macos") as other:
        services.install_service("02:00", "default")
    called.assert_called_once()


@pytest.mark.parametrize(
    "platform,target",
    [
        ("win32",  "_uninstall_windows"),
        ("darwin", "_uninstall_macos"),
        ("linux",  "_uninstall_linux"),
    ],
)
def test_uninstall_routes_by_platform(platform, target):
    with patch.object(services.sys, "platform", platform), \
         patch.object(services, target) as called:
        services.uninstall_service()
    called.assert_called_once()


@pytest.mark.parametrize(
    "platform,target",
    [
        ("win32",  "_status_windows"),
        ("darwin", "_status_macos"),
        ("linux",  "_status_linux"),
    ],
)
def test_status_routes_by_platform(platform, target):
    fake = services.ServiceStatus("X", True, None, True, None)
    with patch.object(services.sys, "platform", platform), \
         patch.object(services, target, return_value=fake):
        s = services.get_status()
    assert s is fake


# --- Windows ----------------------------------------------------

def test_install_windows_calls_schtasks_create_twice():
    with patch.object(services, "_run") as run:
        services._install_windows(2, 0, "default")
    assert run.call_count == 2
    for call in run.call_args_list:
        cmd = call.args[0]
        assert "/Create" in cmd
    # The pipeline call carries /ST HH:MM and /SC DAILY; the
    # dashboard call carries /SC ONLOGON.
    flat = [arg for call in run.call_args_list for arg in call.args[0]]
    assert "ONLOGON" in flat
    assert "DAILY" in flat
    assert "02:00" in flat


def test_uninstall_windows_uses_allow_failure():
    with patch.object(services, "_run") as run:
        services._uninstall_windows()
    assert run.call_count == 2
    for call in run.call_args_list:
        assert call.kwargs.get("allow_failure") is True
        assert "/Delete" in call.args[0]


# --- macOS ------------------------------------------------------

def test_install_macos_writes_plists_and_loads(tmp_path):
    fake_dir = tmp_path / "LaunchAgents"
    with patch.object(services, "_MAC_LAUNCH_DIR", fake_dir), \
         patch.object(services, "_run") as run:
        services._install_macos(2, 30, "default")

    dash = fake_dir / "com.jobagent.dashboard.plist"
    pipe = fake_dir / "com.jobagent.pipeline.plist"
    assert dash.exists()
    assert pipe.exists()

    dash_txt = dash.read_text(encoding="utf-8")
    assert "<key>Label</key><string>com.jobagent.dashboard</string>" in dash_txt
    assert "<key>RunAtLoad</key><true/>" in dash_txt
    assert "<key>KeepAlive</key><true/>" in dash_txt

    pipe_txt = pipe.read_text(encoding="utf-8")
    assert "<key>Hour</key><integer>2</integer>" in pipe_txt
    assert "<key>Minute</key><integer>30</integer>" in pipe_txt

    assert run.call_count == 2
    # Both calls are `launchctl load <path>`.
    for call in run.call_args_list:
        cmd = call.args[0]
        assert cmd[:2] == ["launchctl", "load"]


def test_uninstall_macos_unloads_and_removes(tmp_path):
    fake_dir = tmp_path / "LaunchAgents"
    fake_dir.mkdir()
    dash = fake_dir / "com.jobagent.dashboard.plist"
    pipe = fake_dir / "com.jobagent.pipeline.plist"
    dash.write_text("stub", encoding="utf-8")
    pipe.write_text("stub", encoding="utf-8")

    with patch.object(services, "_MAC_LAUNCH_DIR", fake_dir), \
         patch.object(services, "_run") as run:
        services._uninstall_macos()

    assert not dash.exists()
    assert not pipe.exists()
    # Two launchctl unload calls.
    assert run.call_count == 2
    for call in run.call_args_list:
        cmd = call.args[0]
        assert cmd[:2] == ["launchctl", "unload"]
        assert call.kwargs.get("allow_failure") is True


# --- Linux ------------------------------------------------------

def test_install_linux_writes_units_and_enables(tmp_path):
    fake_dir = tmp_path / "systemd-user"
    with patch.object(services, "_LIN_DIR", fake_dir), \
         patch.object(services, "_run") as run:
        services._install_linux(2, 30, "default")

    dash = fake_dir / "jobagent-dashboard.service"
    pipe = fake_dir / "jobagent-pipeline.service"
    timer = fake_dir / "jobagent-pipeline.timer"
    assert dash.exists()
    assert pipe.exists()
    assert timer.exists()

    dash_txt = dash.read_text(encoding="utf-8")
    assert "ExecStart=" in dash_txt
    assert "-m jobagent start" in dash_txt
    assert "Restart=on-failure" in dash_txt

    pipe_txt = pipe.read_text(encoding="utf-8")
    assert "ExecStart=" in pipe_txt
    assert "Type=oneshot" in pipe_txt
    assert "-m jobagent run --profile default" in pipe_txt

    timer_txt = timer.read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 02:30:00" in timer_txt
    assert "Persistent=true" in timer_txt
    assert "Unit=jobagent-pipeline.service" in timer_txt

    # daemon-reload + enable dashboard + enable timer = 3 calls.
    assert run.call_count == 3
    cmds = [call.args[0] for call in run.call_args_list]
    assert ["systemctl", "--user", "daemon-reload"] in cmds


def test_uninstall_linux_disables_and_removes(tmp_path):
    fake_dir = tmp_path / "systemd-user"
    fake_dir.mkdir()
    for fn in (
        "jobagent-dashboard.service",
        "jobagent-pipeline.service",
        "jobagent-pipeline.timer",
    ):
        (fake_dir / fn).write_text("stub", encoding="utf-8")

    with patch.object(services, "_LIN_DIR", fake_dir), \
         patch.object(services, "_run") as run:
        services._uninstall_linux()

    # All three unit files removed.
    for fn in (
        "jobagent-dashboard.service",
        "jobagent-pipeline.service",
        "jobagent-pipeline.timer",
    ):
        assert not (fake_dir / fn).exists()

    # disable --now for dashboard + timer (2), plus daemon-reload (1).
    assert run.call_count == 3
    cmds = [call.args[0] for call in run.call_args_list]
    assert ["systemctl", "--user", "daemon-reload"] in cmds


# --- _run helper -----------------------------------------------

def test_run_raises_service_error_on_nonzero_exit():
    fake = MagicMock()
    fake.returncode = 1
    fake.stderr = "boom"
    fake.stdout = ""
    with patch.object(services.subprocess, "run", return_value=fake):
        with pytest.raises(services.ServiceError) as excinfo:
            services._run(["fake-tool", "arg"])
    assert "boom" in str(excinfo.value)


def test_run_swallows_failure_when_allow_failure_set():
    fake = MagicMock()
    fake.returncode = 1
    fake.stderr = "boom"
    fake.stdout = ""
    with patch.object(services.subprocess, "run", return_value=fake):
        # No exception.
        services._run(["fake-tool", "arg"], allow_failure=True)


def test_run_raises_when_command_missing():
    with patch.object(
        services.subprocess, "run", side_effect=FileNotFoundError,
    ):
        with pytest.raises(services.ServiceError):
            services._run(["missing-tool"])


def test_run_swallows_missing_when_allow_failure_set():
    with patch.object(
        services.subprocess, "run", side_effect=FileNotFoundError,
    ):
        services._run(["missing-tool"], allow_failure=True)
