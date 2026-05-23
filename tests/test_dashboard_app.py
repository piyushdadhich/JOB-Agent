"""FastAPI app + health route + CORS smoke tests."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from dashboard.backend.app import create_app  # noqa: E402


def test_health_route_returns_ok():
    client = TestClient(create_app())
    r = client.get("/api/health")
    assert r.status_code == 200
    # FIX-6: /api/health now returns a rich payload (checks/alerts).
    # The top-level `status` field still works as a liveness probe;
    # the FIX-6 router maps to one of healthy / degraded / unhealthy.
    body = r.json()
    assert isinstance(body, dict)
    assert "status" in body
    assert body["status"] in ("ok", "healthy", "degraded", "unhealthy")


def test_cors_allows_localhost_vite_origin():
    client = TestClient(create_app())
    r = client.get(
        "/api/health",
        headers={"Origin": "http://localhost:5173"},
    )
    assert r.status_code == 200
    assert (
        r.headers.get("access-control-allow-origin")
        == "http://localhost:5173"
    )


def test_routes_registered():
    """Every Phase 1.5-1.8 route is mounted with the right method."""
    app = create_app()
    # Drop the auto-added HEAD that FastAPI attaches to every GET so
    # the assertions read like the route declarations.
    paths = {
        (r.path, tuple(sorted(m for m in r.methods if m != "HEAD")))
        for r in app.routes if hasattr(r, "methods")
    }
    expected = {
        ("/api/health", ("GET",)),
        ("/api/shortlist", ("GET",)),
        ("/api/shortlist/{posting_id}/select", ("POST",)),
        ("/api/shortlist/{posting_id}/skip", ("POST",)),
        ("/api/applications", ("GET",)),
        ("/api/applications/{application_id}", ("GET",)),
        ("/api/applications/{application_id}/status", ("PUT",)),
        ("/api/prompts/{posting_id}/resume", ("GET",)),
        ("/api/prompts/{posting_id}/resume", ("POST",)),
        ("/api/prompts/{posting_id}/cover-letter", ("GET",)),
        ("/api/prompts/{posting_id}/cover-letter", ("POST",)),
    }
    missing = expected - paths
    assert not missing, f"missing routes: {missing}"
