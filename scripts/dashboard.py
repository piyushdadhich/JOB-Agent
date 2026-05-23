"""Launch the localhost dashboard.

Usage:
  python scripts/dashboard.py
  python scripts/dashboard.py --no-browser
  python scripts/dashboard.py --port 8080
  python scripts/dashboard.py --profile default

Starts uvicorn on http://localhost:{port} serving FastAPI + the
prebuilt React bundle, then opens the default browser. The frontend
is served from dashboard/frontend/dist; if dist/ is missing, prints a
clear "build the frontend first" error and exits 1.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn  # noqa: E402

from dashboard.backend.app import FRONTEND_DIST  # noqa: E402

DEFAULT_PORT = 8000


def _open_browser(url: str, delay: float = 0.8) -> None:
    """Open the browser slightly after uvicorn binds the port."""

    def _open():
        time.sleep(delay)
        webbrowser.open(url)

    threading.Thread(target=_open, daemon=True).start()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--profile", default=None,
        help="Override JOB_AGENT_PROFILE for this process.",
    )
    parser.add_argument(
        "--no-browser", action="store_true",
        help="Don't auto-open the browser (e.g. headless setups).",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if args.profile:
        os.environ["JOB_AGENT_PROFILE"] = args.profile

    if not FRONTEND_DIST.exists():
        print(
            "ERROR: frontend bundle not found at "
            f"{FRONTEND_DIST}\n\n"
            "Build it first:\n"
            "  cd dashboard/frontend\n"
            "  npm install   # one-time\n"
            "  npm run build",
            file=sys.stderr,
        )
        return 1

    url = f"http://{args.host}:{args.port}"
    print(f"Job Agent Dashboard -> {url}")
    if not args.no_browser:
        _open_browser(url)

    uvicorn.run(
        "dashboard.backend.app:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
