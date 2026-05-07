"""
Integration tests for the config router.

Uses FastAPI TestClient to test:
  GET  /api/v1/config/render
  PUT  /api/v1/config/render
  POST /api/v1/config/render/reset

Each test uses at most 2 mocks.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Ensure src/ is on sys.path (conftest.py handles this, but be explicit).
_SRC = str(Path(__file__).resolve().parents[2] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from main import app
from services.render_config_service import RenderConfigService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _seed_app_state() -> None:
    """Inject a fresh RenderConfigService into app.state before each test.

    This avoids needing the full lifespan to run inside TestClient.
    """
    app.state.render_config = RenderConfigService()


@pytest.fixture()
def client() -> TestClient:
    """Return a synchronous TestClient for the FastAPI app."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /api/v1/config/render
# ---------------------------------------------------------------------------


def test_get_config(client: TestClient) -> None:
    """GET /config/render should return a dict with all expected keys."""
    response = client.get("/api/v1/config/render")
    assert response.status_code == 200
    body = response.json()
    # Verify all expected keys are present.
    for key in (
        "max_res_x",
        "max_res_y",
        "min_res_x",
        "min_res_y",
        "max_samples",
        "min_samples",
        "default_res_x",
        "default_res_y",
        "default_samples",
        "max_concurrent_renders",
        "job_ttl_hours",
    ):
        assert key in body, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# PUT /api/v1/config/render
# ---------------------------------------------------------------------------


def test_update_config(client: TestClient) -> None:
    """PUT /config/render should update fields and return the new config."""
    payload = {"max_res_x": 1920, "default_samples": 16}
    response = client.put("/api/v1/config/render", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["max_res_x"] == 1920
    assert body["default_samples"] == 16


def test_update_config_unknown_key_returns_422(client: TestClient) -> None:
    """B.32: PUT /config/render with only unknown keys returns 422 (no valid fields applied)."""
    response = client.put("/api/v1/config/render", json={"totally_fake_setting": 42})
    assert response.status_code == 422
    body = response.json()
    assert "detail" in body


def test_update_config_unknown_key_samples_returns_422(client: TestClient) -> None:
    """B.32: PUT /config/render with 'samples' (non-existent field) returns 422.

    'samples' is a plausible near-miss for 'default_samples' — exact scenario from B.32.
    """
    response = client.put("/api/v1/config/render", json={"samples": 64})
    assert response.status_code == 422


def test_update_config_mixed_valid_unknown_succeeds(client: TestClient) -> None:
    """B.32: PUT /config/render with at least one valid field plus unknowns succeeds."""
    response = client.put("/api/v1/config/render", json={"max_res_x": 1920, "unknown_field": 99})
    assert response.status_code == 200
    body = response.json()
    assert body["max_res_x"] == 1920


# ---------------------------------------------------------------------------
# POST /api/v1/config/render/reset
# ---------------------------------------------------------------------------


def test_reset_config(client: TestClient) -> None:
    """POST /config/render/reset should restore defaults and return them."""
    # First change a value.
    client.put("/api/v1/config/render", json={"max_res_x": 100})
    # Then reset.
    response = client.post("/api/v1/config/render/reset")
    assert response.status_code == 200
    body = response.json()
    # Default max_res_x is 3840.
    assert body["max_res_x"] == 3840
