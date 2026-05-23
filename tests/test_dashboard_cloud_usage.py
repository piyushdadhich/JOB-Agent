"""Tests for GET /api/cloud-usage/today."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from dashboard.backend.app import create_app  # noqa: E402
from dashboard.backend.deps import get_profile_id  # noqa: E402
from engine import cloud_budget  # noqa: E402


@pytest.fixture
def client():
    app = create_app()
    app.dependency_overrides[get_profile_id] = lambda: "usage_test_profile"
    return TestClient(app)


def test_cloud_usage_today_empty(client, monkeypatch, tmp_path):
    log = tmp_path / "cloud_eval_usage.jsonl"
    monkeypatch.setattr(cloud_budget, "usage_path", lambda pid: log)
    r = client.get("/api/cloud-usage/today")
    assert r.status_code == 200
    body = r.json()
    assert body["daily_limit"] == 1500
    assert body["total_used"] == 0
    assert body["remaining"] == 1500
    assert body["breakdown"] == {}


def test_cloud_usage_today_with_calls(client, monkeypatch, tmp_path):
    log = tmp_path / "cloud_eval_usage.jsonl"
    today = datetime.now(timezone.utc).isoformat()
    with open(log, "w", encoding="utf-8") as f:
        for ct in ["eval", "eval", "resume", "cover_letter"]:
            f.write(json.dumps(
                {"timestamp": today, "call_type": ct}
            ) + "\n")
        # Evaluator row without call_type → buckets as 'eval'.
        f.write(json.dumps({"timestamp": today}) + "\n")
    monkeypatch.setattr(cloud_budget, "usage_path", lambda pid: log)

    body = client.get("/api/cloud-usage/today").json()
    assert body["total_used"] == 5
    assert body["remaining"] == 1495
    assert body["breakdown"] == {
        "eval": 3, "resume": 1, "cover_letter": 1,
    }
