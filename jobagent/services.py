"""Spec 16 TASK 3 — cross-platform background service registration.

`install_service()` registers two services with the host OS:

  Dashboard:   long-running, started at user logon/login, kept alive.
               Spawns `python -m jobagent start`.
  Pipeline:    daily oneshot at the user's chosen time
               (default 02:00 local). Runs `python -m jobagent run
               --profile <id>`.

Platform mapping:

  Windows  →  Task Scheduler via schtasks.exe.
              Dashboard: /SC ONLOGON.
              Pipeline:  /SC DAILY /ST HH:MM.

  macOS    →  launchd plists under ~/Library/LaunchAgents/.
              Dashboard: RunAtLoad + KeepAlive.
              Pipeline:  StartCalendarInterval (Hour/Minute).

  Linux    →  systemd user units under ~/.config/systemd/user/.
              Dashboard: .service with Restart=on-failure.
              Pipeline:  .service + .timer (OnCalendar, Persistent).

All three paths invoke `sys.executable -m jobagent <cmd>` so the
service runs the same Python interpreter that installed it.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class ServiceError(Exception):
    """Raised when a service install / uninstall / status query fails."""


@dataclass(frozen=True)
class ServiceStatus:
    platform: str             # "Windows" | "macOS" | "Linux"
    dashboard_registered: bool
    dashboard_running: Optional[bool]  # None when not knowable cheaply
    pipeline_registered: bool
    pipeline_next_run: Optional[str]   # platform-specific string


# --- Public API --------------------------------------------------

def install_service(
    schedule_time: str = "02:00",
    profile: str = "default",
) -> None:
    """Register dashboard + scheduled pipeline as background services.

    schedule_time: HH:MM (24-hour, local time) for the daily pipeline
                   run. Default 02:00 matches the project's day-
                   boundary convention.
    profile:       JOB_AGENT_PROFILE id; passed to both services so
                   each profile installs cleanly side-by-side.
    """
    hour, minute = _parse_schedule(schedule_time)
    if sys.platform == "win32":
        _install_windows(hour, minute, profile)
    elif sys.platform == "darwin":
        _install_macos(hour, minute, profile)
    else:
        _install_linux(hour, minute, profile)


def uninstall_service() -> None:
    """Remove services previously registered by :func:`install_service`.

    Tolerates a missing registration — uninstalling a service that
    isn't installed is a no-op, not an error.
    """
    if sys.platform == "win32":
        _uninstall_windows()
    elif sys.platform == "darwin":
        _uninstall_macos()
    else:
        _uninstall_linux()


def get_status() -> ServiceStatus:
    """Return a :class:`ServiceStatus` snapshot for the current host."""
    if sys.platform == "win32":
        return _status_windows()
    elif sys.platform == "darwin":
        return _status_macos()
    else:
        return _status_linux()


# --- Schedule parsing --------------------------------------------

_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _parse_schedule(schedule_time: str) -> tuple[int, int]:
    m = _HHMM_RE.match(schedule_time.strip())
    if not m:
        raise ServiceError(
            f"schedule_time must be HH:MM (24-hour); got {schedule_time!r}"
        )
    hour = int(m.group(1))
    minute = int(m.group(2))
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ServiceError(
            f"schedule_time {schedule_time!r} out of 00:00–23:59 range"
        )
    return hour, minute


# --- Windows (Task Scheduler) ------------------------------------

_WIN_DASH = "JobAgent-Dashboard"
_WIN_PIPE = "JobAgent-Pipeline"


def _install_windows(hour: int, minute: int, profile: str) -> None:
    schtasks = shutil.which("schtasks") or "schtasks"
    # Prefer pythonw.exe (no console window) for the dashboard so
    # there's no black console flash at user logon. Fall back to
    # python.exe if pythonw isn't there (e.g., embedded distros).
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    dash_exe = str(pythonw) if pythonw.exists() else sys.executable
    dash_tr = (
        f'"{dash_exe}" -m jobagent start'
    )
    _run([
        schtasks, "/Create", "/TN", _WIN_DASH,
        "/TR", dash_tr,
        "/SC", "ONLOGON",
        "/RL", "LIMITED",
        "/F",
    ])

    pipe_tr = (
        f'"{sys.executable}" -m jobagent run --profile {profile}'
    )
    _run([
        schtasks, "/Create", "/TN", _WIN_PIPE,
        "/TR", pipe_tr,
        "/SC", "DAILY", "/ST", f"{hour:02d}:{minute:02d}",
        "/RL", "LIMITED",
        "/F",
    ])


def _uninstall_windows() -> None:
    schtasks = shutil.which("schtasks") or "schtasks"
    for name in (_WIN_DASH, _WIN_PIPE):
        _run(
            [schtasks, "/Delete", "/TN", name, "/F"],
            allow_failure=True,
        )


def _status_windows() -> ServiceStatus:
    schtasks = shutil.which("schtasks") or "schtasks"
    dash = _windows_task_exists(schtasks, _WIN_DASH)
    pipe = _windows_task_exists(schtasks, _WIN_PIPE)
    next_run = (
        _windows_task_next_run(schtasks, _WIN_PIPE) if pipe else None
    )
    return ServiceStatus(
        platform="Windows",
        dashboard_registered=dash,
        # ONLOGON tasks: schtasks /Query doesn't reliably surface
        # "currently running" without a WMI call, which costs more
        # than the status payload is worth.
        dashboard_running=None,
        pipeline_registered=pipe,
        pipeline_next_run=next_run,
    )


def _windows_task_exists(schtasks: str, name: str) -> bool:
    try:
        out = subprocess.run(
            [schtasks, "/Query", "/TN", name],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return out.returncode == 0


def _windows_task_next_run(schtasks: str, name: str) -> Optional[str]:
    try:
        out = subprocess.run(
            [schtasks, "/Query", "/TN", name, "/FO", "LIST", "/V"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    for line in out.stdout.splitlines():
        if "Next Run Time:" in line:
            return line.split(":", 1)[1].strip()
    return None


# --- macOS (launchd) ----------------------------------------------

_MAC_LAUNCH_DIR = Path.home() / "Library" / "LaunchAgents"
_MAC_DASH_LABEL = "com.jobagent.dashboard"
_MAC_PIPE_LABEL = "com.jobagent.pipeline"


def _mac_dashboard_plist() -> Path:
    return _MAC_LAUNCH_DIR / f"{_MAC_DASH_LABEL}.plist"


def _mac_pipeline_plist() -> Path:
    return _MAC_LAUNCH_DIR / f"{_MAC_PIPE_LABEL}.plist"


_MAC_PLIST_DASHBOARD = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>-m</string>
    <string>jobagent</string>
    <string>start</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>JOB_AGENT_PROFILE</key><string>{profile}</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
"""

_MAC_PLIST_PIPELINE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>-m</string>
    <string>jobagent</string>
    <string>run</string>
    <string>--profile</string>
    <string>{profile}</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>JOB_AGENT_PROFILE</key><string>{profile}</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>{hour}</integer>
    <key>Minute</key><integer>{minute}</integer>
  </dict>
</dict>
</plist>
"""


def _install_macos(hour: int, minute: int, profile: str) -> None:
    _MAC_LAUNCH_DIR.mkdir(parents=True, exist_ok=True)
    _mac_dashboard_plist().write_text(
        _MAC_PLIST_DASHBOARD.format(
            label=_MAC_DASH_LABEL,
            python=sys.executable,
            profile=profile,
        ),
        encoding="utf-8",
    )
    _mac_pipeline_plist().write_text(
        _MAC_PLIST_PIPELINE.format(
            label=_MAC_PIPE_LABEL,
            python=sys.executable,
            profile=profile,
            hour=hour,
            minute=minute,
        ),
        encoding="utf-8",
    )
    _run(["launchctl", "load", str(_mac_dashboard_plist())])
    _run(["launchctl", "load", str(_mac_pipeline_plist())])


def _uninstall_macos() -> None:
    for path in (_mac_dashboard_plist(), _mac_pipeline_plist()):
        if path.exists():
            _run(
                ["launchctl", "unload", str(path)],
                allow_failure=True,
            )
            try:
                path.unlink()
            except OSError:
                pass


def _status_macos() -> ServiceStatus:
    try:
        out = subprocess.run(
            ["launchctl", "list"],
            capture_output=True, text=True, timeout=10,
        )
        listed = out.stdout if out.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        listed = ""
    return ServiceStatus(
        platform="macOS",
        dashboard_registered=_mac_dashboard_plist().exists(),
        dashboard_running=_MAC_DASH_LABEL in listed,
        pipeline_registered=_mac_pipeline_plist().exists(),
        # launchctl print for StartCalendarInterval is verbose to
        # parse and rarely useful — we report just registered/not.
        pipeline_next_run=None,
    )


# --- Linux (systemd user) -----------------------------------------

_LIN_DIR = Path.home() / ".config" / "systemd" / "user"
_LIN_DASH_UNIT = "jobagent-dashboard.service"
_LIN_PIPE_UNIT = "jobagent-pipeline.service"
_LIN_PIPE_TIMER = "jobagent-pipeline.timer"

_LIN_DASH_SERVICE = """\
[Unit]
Description=Job Agent dashboard

[Service]
Type=simple
Environment=JOB_AGENT_PROFILE={profile}
ExecStart={python} -m jobagent start
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""

_LIN_PIPE_SERVICE = """\
[Unit]
Description=Job Agent daily pipeline run

[Service]
Type=oneshot
Environment=JOB_AGENT_PROFILE={profile}
ExecStart={python} -m jobagent run --profile {profile}
"""

_LIN_PIPE_TIMER_UNIT = """\
[Unit]
Description=Daily Job Agent pipeline trigger

[Timer]
OnCalendar=*-*-* {hour:02d}:{minute:02d}:00
Persistent=true
Unit={unit}

[Install]
WantedBy=timers.target
"""


def _install_linux(hour: int, minute: int, profile: str) -> None:
    _LIN_DIR.mkdir(parents=True, exist_ok=True)
    (_LIN_DIR / _LIN_DASH_UNIT).write_text(
        _LIN_DASH_SERVICE.format(
            python=sys.executable, profile=profile,
        ),
        encoding="utf-8",
    )
    (_LIN_DIR / _LIN_PIPE_UNIT).write_text(
        _LIN_PIPE_SERVICE.format(
            python=sys.executable, profile=profile,
        ),
        encoding="utf-8",
    )
    (_LIN_DIR / _LIN_PIPE_TIMER).write_text(
        _LIN_PIPE_TIMER_UNIT.format(
            hour=hour, minute=minute, unit=_LIN_PIPE_UNIT,
        ),
        encoding="utf-8",
    )
    _run(["systemctl", "--user", "daemon-reload"])
    _run(["systemctl", "--user", "enable", "--now", _LIN_DASH_UNIT])
    _run(["systemctl", "--user", "enable", "--now", _LIN_PIPE_TIMER])


def _uninstall_linux() -> None:
    for unit in (_LIN_DASH_UNIT, _LIN_PIPE_TIMER):
        _run(
            ["systemctl", "--user", "disable", "--now", unit],
            allow_failure=True,
        )
    for fn in (_LIN_DASH_UNIT, _LIN_PIPE_UNIT, _LIN_PIPE_TIMER):
        try:
            (_LIN_DIR / fn).unlink()
        except OSError:
            pass
    _run(
        ["systemctl", "--user", "daemon-reload"],
        allow_failure=True,
    )


def _status_linux() -> ServiceStatus:
    dash_registered = (_LIN_DIR / _LIN_DASH_UNIT).exists()
    pipe_registered = (_LIN_DIR / _LIN_PIPE_TIMER).exists()

    dash_running: Optional[bool] = None
    if dash_registered:
        dash_running = _systemctl_is_active(_LIN_DASH_UNIT)

    next_run: Optional[str] = None
    if pipe_registered:
        next_run = _systemctl_next_elapse(_LIN_PIPE_TIMER)

    return ServiceStatus(
        platform="Linux",
        dashboard_registered=dash_registered,
        dashboard_running=dash_running,
        pipeline_registered=pipe_registered,
        pipeline_next_run=next_run,
    )


def _systemctl_is_active(unit: str) -> bool:
    try:
        out = subprocess.run(
            ["systemctl", "--user", "is-active", unit],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return out.stdout.strip() == "active"


def _systemctl_next_elapse(unit: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["systemctl", "--user", "show", unit,
             "--property=NextElapseUSecRealtime"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0 or "=" not in out.stdout:
        return None
    value = out.stdout.split("=", 1)[1].strip()
    return value or None


# --- subprocess helper -------------------------------------------

def _run(
    cmd: list[str],
    allow_failure: bool = False,
    timeout: int = 30,
) -> None:
    """Run a shell-out + raise :class:`ServiceError` on failure.

    `allow_failure=True` suppresses non-zero exit codes — used for
    idempotent uninstalls where the target may already be gone.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        if allow_failure:
            return
        raise ServiceError(f"{cmd[0]}: {e}") from e
    if result.returncode != 0 and not allow_failure:
        stderr = (result.stderr or "").strip() or (result.stdout or "").strip()
        raise ServiceError(
            f"{cmd[0]} exited {result.returncode}: {stderr}"
        )
