"""Spec JA-3 TASK 3 -- double-click launcher for the Job Agent dashboard.

Starts the FastAPI backend on a background thread, then opens a native
PyWebView window pointing at it. If dashboard/frontend/dist/ is present
the backend serves the SPA from one origin (:8080). Otherwise this
launcher also spawns `npm run dev` for hot-reload and points at :5173.

Used by the desktop shortcut. Run from anywhere:

  Windows:        .\\venv\\Scripts\\python.exe scripts\\run_app.py
  macOS / Linux:  ./venv/bin/python scripts/run_app.py

Production-style (no Vite, dist served by uvicorn):
  npm run build
  python scripts/run_app.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import uvicorn  # noqa: E402
import webview  # noqa: E402

BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8080
VITE_PORT = 5173

FRONTEND_DIR = PROJECT_ROOT / "dashboard" / "frontend"
FRONTEND_DIST = FRONTEND_DIR / "dist"


def _start_backend() -> None:
    """Run uvicorn in the current thread. Called from a daemon thread."""
    uvicorn.run(
        "dashboard.backend.app:app",
        host=BACKEND_HOST,
        port=BACKEND_PORT,
        log_level="warning",
    )


def _start_vite_dev_server() -> subprocess.Popen:
    """Spawn `npm run dev` as a child process; returns the Popen handle.

    `shell=True` is required on Windows because `npm` is a `.cmd`
    shim there and Popen cannot exec a shim directly. On macOS /
    Linux `npm` is a real binary; we use `shell=False` so the
    process is properly parented and Ctrl-C handling works as
    expected. stdout/stderr go to DEVNULL so the dashboard console
    stays quiet -- the dev server output is visible in the PyWebView
    devtools instead.
    """
    return subprocess.Popen(
        ["npm", "run", "dev"],
        cwd=str(FRONTEND_DIR),
        shell=(sys.platform == "win32"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> int:
    threading.Thread(target=_start_backend, daemon=True).start()
    time.sleep(2)  # let uvicorn bind the port before we navigate

    if FRONTEND_DIST.is_dir():
        url = f"http://{BACKEND_HOST}:{BACKEND_PORT}"
    else:
        _start_vite_dev_server()
        time.sleep(3)
        url = f"http://{BACKEND_HOST}:{VITE_PORT}"

    profile = os.environ.get("JOB_AGENT_PROFILE", "default")
    webview.create_window(
        f"Job Agent -- {profile}",
        url,
        width=1400,
        height=900,
        min_size=(1000, 600),
    )
    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
