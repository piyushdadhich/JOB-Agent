"""job-agent CLI — single-file subcommand dispatcher.

Each subcommand is a top-level _cmd_<name> function. New commands
are added in three steps:

  1. write a `_cmd_<name>(args) -> int` function below;
  2. register it in COMMANDS at the bottom (label, help text,
     subparser setup callback, handler);
  3. if it needs arguments, attach them in the setup callback.

Commands that are *planned* but not yet implemented (waiting on
later Specs) return exit code 2 with a clear message pointing
at the Spec that lands them, so users discovering them via
`--help` get a graceful "not yet" rather than a stack trace.
"""
from __future__ import annotations

import argparse
import sys
from typing import Callable, Optional


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="job-agent",
        description="Local-first AI job search agent.",
    )
    sub = parser.add_subparsers(
        dest="command", metavar="<command>",
        title="commands",
    )

    for name, cmd in COMMANDS.items():
        sub.add_parser(name, help=cmd.help, add_help=False)

    # parse_known_args lets each subcommand swallow its own flags
    # (forwarded to the underlying script). Without this, top-level
    # argparse rejects flags like `--profile` before they reach the
    # subcommand's handler. The trade-off is per-subcommand --help
    # via argparse: passthrough commands defer --help to the wrapped
    # script (run_daily, gmail_auth, etc.); planned commands ignore
    # extra flags and exit with the "not yet" message.
    args, extra = parser.parse_known_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    args.extra = extra
    return COMMANDS[args.command].handler(args)


# --- Command handlers --------------------------------------------
#
# Each handler returns an integer exit code (0 = success).
#
# Handlers that delegate to an existing scripts/<x>.py module
# import lazily so startup cost is paid only for the subcommand
# actually invoked. `job-agent --help` doesn't need to import the
# whole pipeline.

def _cmd_start(args) -> int:
    """Start the dashboard + open the native PyWebView window."""
    from scripts import run_app
    return run_app.main()


def _cmd_stop(args) -> int:
    """Best-effort stop: kill anything listening on the dashboard port.

    The background dashboard service (Spec 16 TASK 3) is the
    intended source of a running dashboard, but `job-agent start`
    can also spawn one in the foreground. Either way, killing the
    process bound to the dashboard port is the cross-platform way
    to bring it down.
    """
    import psutil

    target_ports = (8000, 8080)
    killed = 0
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            for conn in proc.net_connections(kind="inet"):
                if (
                    conn.laddr
                    and conn.laddr.port in target_ports
                    and conn.status == psutil.CONN_LISTEN
                ):
                    proc.terminate()
                    killed += 1
                    break
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    print(
        f"Stopped {killed} process(es) on ports {target_ports}."
        if killed
        else "No dashboard process found on the expected ports."
    )
    return 0


def _cmd_status(args) -> int:
    """Print a hardware + service-state snapshot."""
    from jobagent.platform import detect_hardware
    from jobagent.services import get_status

    info = detect_hardware()
    print(f"OS:               {info.os_name} {info.os_version}")
    print(f"CPU:              {info.cpu_model} ({info.cpu_cores} cores)")
    print(f"RAM:              "
          f"{info.ram_total_gb:.1f} GB total, "
          f"{info.ram_available_gb:.1f} GB available")
    if info.gpu_type:
        vram = (
            f"{info.gpu_vram_gb:.1f} GB VRAM"
            if info.gpu_vram_gb else "unified memory"
        )
        print(f"GPU:              {info.gpu_model} ({vram})")
    else:
        print("GPU:              none detected")
    print(f"Disk free:        {info.disk_free_gb:.1f} GB")
    print(f"Recommended tier: {info.tier}")

    svc = get_status()
    dash = "registered" if svc.dashboard_registered else "not installed"
    if svc.dashboard_running is True:
        dash += " (running)"
    elif svc.dashboard_running is False and svc.dashboard_registered:
        dash += " (stopped)"
    print(f"Dashboard svc:    {dash}")
    pipe = "registered" if svc.pipeline_registered else "not installed"
    if svc.pipeline_next_run:
        pipe += f" — next: {svc.pipeline_next_run}"
    print(f"Pipeline svc:     {pipe}")

    return 0


def _cmd_setup(args) -> int:
    return _not_yet(
        "setup", "Spec 1",
        "launches the browser-based onboarding wizard at "
        "http://localhost:8000/setup.",
    )


def _cmd_run(args) -> int:
    """Run discovery + evaluation now (passes through to run_daily)."""
    from scripts import run_daily
    return run_daily.main(args.extra or [])


def _cmd_expand(args) -> int:
    """Run the discovery expansion agent (passes through to run_expansion)."""
    from scripts import run_expansion
    return run_expansion.main(args.extra or [])


def _cmd_gaps(args) -> int:
    return _not_yet(
        "gaps", "Spec 13",
        "shows the skill-gap report from the expansion agent's "
        "recent runs.",
    )


def _cmd_worksheet(args) -> int:
    """Fetch a posting URL, print a printable per-field worksheet."""
    from engine.applicant.worksheet import from_url, render_markdown

    extra = args.extra or []
    if not extra:
        print(
            "worksheet: usage `job-agent worksheet <url>`",
            file=sys.stderr,
        )
        return 2
    url = extra[0]
    try:
        ws = from_url(url)
    except Exception as e:
        print(
            f"worksheet: fetch failed: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return 1
    print(render_markdown(ws))
    return 0


def _cmd_apply(args) -> int:
    """Fill an application form (passes through to scripts/apply.py)."""
    from scripts import apply as apply_script
    return apply_script.main(args.extra or [])


def _cmd_install_service(args) -> int:
    """Register the dashboard + scheduled pipeline as background services."""
    from jobagent.services import install_service, ServiceError

    extra = args.extra or []
    schedule = "02:00"
    profile = "default"
    # Tiny inline parser — we keep this out of the top-level
    # argparse so passthrough flags don't collide with subcommand
    # flags. Only --time and --profile are accepted.
    i = 0
    while i < len(extra):
        tok = extra[i]
        if tok == "--time" and i + 1 < len(extra):
            schedule = extra[i + 1]
            i += 2
        elif tok == "--profile" and i + 1 < len(extra):
            profile = extra[i + 1]
            i += 2
        else:
            print(
                f"install-service: unrecognised arg {tok!r} "
                f"(supported: --time HH:MM, --profile NAME)",
                file=sys.stderr,
            )
            return 2
    try:
        install_service(schedule_time=schedule, profile=profile)
    except ServiceError as e:
        print(f"install-service failed: {e}", file=sys.stderr)
        return 1
    print(
        f"Background services installed. Dashboard auto-starts at "
        f"logon; pipeline runs daily at {schedule} for profile "
        f"{profile!r}."
    )
    return 0


def _cmd_uninstall_service(args) -> int:
    """Remove background services registered by install-service."""
    from jobagent.services import uninstall_service, ServiceError

    try:
        uninstall_service()
    except ServiceError as e:
        print(f"uninstall-service failed: {e}", file=sys.stderr)
        return 1
    print("Background services removed.")
    return 0


def _cmd_gmail_auth(args) -> int:
    """Run the Gmail OAuth consent flow (passes through to scripts/gmail_auth.py)."""
    from scripts import gmail_auth
    return gmail_auth.main(args.extra or [])


def _cmd_check_expiry(args) -> int:
    """Walk every active posting, mark 404s as dismissed."""
    from pathlib import Path

    from engine.expiry.checker import check_postings, write_run_log
    from engine.persistence.tracker import Tracker

    profile = "default"
    rate = 1.0
    limit: int | None = None
    extra = args.extra or []
    i = 0
    while i < len(extra):
        tok = extra[i]
        if tok == "--profile" and i + 1 < len(extra):
            profile = extra[i + 1]
            i += 2
        elif tok == "--rate" and i + 1 < len(extra):
            rate = float(extra[i + 1])
            i += 2
        elif tok == "--limit" and i + 1 < len(extra):
            limit = int(extra[i + 1])
            i += 2
        else:
            print(
                f"check-expiry: unrecognised arg {tok!r}",
                file=sys.stderr,
            )
            return 2

    tracker = Tracker(profile_id=profile)
    try:
        report = check_postings(
            tracker, rate_seconds=rate, limit=limit,
        )
    finally:
        tracker.close()

    project_root = Path(__file__).resolve().parent.parent
    log_path = write_run_log(report, project_root=project_root)
    print(
        f"Checked {report.total_checked} posting(s). "
        f"Expired: {len(report.expired)}. "
        f"Errors: {len(report.errors)}. "
        f"Log: {log_path.relative_to(project_root)}"
    )
    return 0


def _cmd_export(args) -> int:
    return _not_yet(
        "export", "Spec 13",
        "exports the current shortlist to CSV, honoring the "
        "active dashboard filters.",
    )


# --- Internal helpers --------------------------------------------

def _not_yet(name: str, spec: str, what: str) -> int:
    """Print a graceful 'planned, not yet built' message + exit 2."""
    print(
        f"`job-agent {name}` is planned for {spec}. When it lands "
        f"it {what}",
        file=sys.stderr,
    )
    print(
        f"\nUntil then, see the corresponding scripts/ entry "
        f"or run `job-agent --help` for what works today.",
        file=sys.stderr,
    )
    return 2


# --- Command registry --------------------------------------------

class _Command:
    __slots__ = ("help", "handler")

    def __init__(
        self,
        help: str,
        handler: Callable[[argparse.Namespace], int],
    ) -> None:
        self.help = help
        self.handler = handler


COMMANDS: dict[str, _Command] = {
    "start":             _Command("start dashboard + open browser",          _cmd_start),
    "stop":              _Command("stop the dashboard (kill any process on its port)", _cmd_stop),
    "status":            _Command("show hardware + service status",          _cmd_status),
    "setup":             _Command("run the onboarding wizard (planned)",     _cmd_setup),
    "run":               _Command("run discovery + evaluation now",          _cmd_run),
    "expand":            _Command("run the expansion agent",                 _cmd_expand),
    "gaps":              _Command("show skill-gap report (planned)",         _cmd_gaps),
    "worksheet":         _Command("generate manual-fill worksheet for a URL", _cmd_worksheet),
    "apply":             _Command("fill an application form",                _cmd_apply),
    "install-service":   _Command("register dashboard + pipeline as background services", _cmd_install_service),
    "uninstall-service": _Command("remove the background services",          _cmd_uninstall_service),
    "gmail-auth":        _Command("Gmail OAuth consent flow",                _cmd_gmail_auth),
    "check-expiry":      _Command("walk active postings; mark 404s dismissed", _cmd_check_expiry),
    "export":            _Command("export shortlist to CSV (planned)",       _cmd_export),
}


if __name__ == "__main__":
    sys.exit(main())
