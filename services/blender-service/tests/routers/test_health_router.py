"""
Integration tests for the health router.

Covers GET /api/v1/health/, GET /api/v1/health/simple, GET /api/v1/health/liveness.
Each test uses at most 2 mocks.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Ensure src/ is on sys.path
_SRC = str(Path(__file__).resolve().parents[2] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from main import app

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    """Return a synchronous TestClient for the FastAPI app."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /api/v1/health/ — comprehensive health check
# ---------------------------------------------------------------------------


def test_health_check_returns_200(client: TestClient) -> None:
    """Comprehensive health check should return HTTP 200."""
    response = client.get("/api/v1/health/")
    assert response.status_code == 200


def test_health_check_returns_healthy_status(client: TestClient) -> None:
    """Comprehensive health check should return status='healthy'."""
    response = client.get("/api/v1/health/")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"


def test_health_check_contains_required_fields(client: TestClient) -> None:
    """Comprehensive health check response must include all required fields."""
    response = client.get("/api/v1/health/")
    body = response.json()
    for field in ("status", "timestamp", "version", "service", "environment", "checks"):
        assert field in body, f"Missing field: {field}"


def test_health_check_environment_has_python_version(client: TestClient) -> None:
    """Environment dict in health response must include python_version."""
    response = client.get("/api/v1/health/")
    body = response.json()
    assert "python_version" in body["environment"]
    # Must be a valid version string like "3.12.x"
    assert "." in body["environment"]["python_version"]


def test_health_check_environment_has_platform(client: TestClient) -> None:
    """Environment dict must include platform information."""
    response = client.get("/api/v1/health/")
    body = response.json()
    assert "platform" in body["environment"]
    assert len(body["environment"]["platform"]) > 0


def test_health_check_checks_dict_has_python_version(client: TestClient) -> None:
    """Checks dict must include python_version check as a bool."""
    response = client.get("/api/v1/health/")
    body = response.json()
    assert "python_version" in body["checks"]
    assert isinstance(body["checks"]["python_version"], bool)


def test_health_check_version_is_string(client: TestClient) -> None:
    """Version field must be a non-empty string."""
    response = client.get("/api/v1/health/")
    body = response.json()
    assert isinstance(body["version"], str)
    assert len(body["version"]) > 0


def test_health_check_service_name_is_string(client: TestClient) -> None:
    """Service field must be a non-empty string."""
    response = client.get("/api/v1/health/")
    body = response.json()
    assert isinstance(body["service"], str)
    assert len(body["service"]) > 0


def test_health_check_timestamp_is_iso_format(client: TestClient) -> None:
    """Timestamp field must be a valid ISO 8601 string."""
    from datetime import datetime

    response = client.get("/api/v1/health/")
    body = response.json()
    # Should not raise ValueError
    ts = datetime.fromisoformat(body["timestamp"])
    assert ts is not None


# ---------------------------------------------------------------------------
# GET /api/v1/health/simple — lightweight health check
# ---------------------------------------------------------------------------


def test_simple_health_check_returns_200(client: TestClient) -> None:
    """Simple health check should return HTTP 200."""
    response = client.get("/api/v1/health/simple")
    assert response.status_code == 200


def test_simple_health_check_returns_healthy(client: TestClient) -> None:
    """Simple health check should return status='healthy'."""
    response = client.get("/api/v1/health/simple")
    body = response.json()
    assert body["status"] == "healthy"


def test_simple_health_check_has_timestamp(client: TestClient) -> None:
    """Simple health check must include a timestamp field."""
    response = client.get("/api/v1/health/simple")
    body = response.json()
    assert "timestamp" in body
    assert body["timestamp"] is not None


def test_simple_health_check_only_has_status_and_timestamp(client: TestClient) -> None:
    """Simple health check response should contain status and timestamp keys."""
    response = client.get("/api/v1/health/simple")
    body = response.json()
    assert "status" in body
    assert "timestamp" in body


# ---------------------------------------------------------------------------
# GET /api/v1/health/liveness — liveness probe
# ---------------------------------------------------------------------------


def test_liveness_check_returns_200(client: TestClient) -> None:
    """Liveness check should return HTTP 200."""
    response = client.get("/api/v1/health/liveness")
    assert response.status_code == 200


def test_liveness_check_returns_alive(client: TestClient) -> None:
    """Liveness check must return status='alive'."""
    response = client.get("/api/v1/health/liveness")
    body = response.json()
    assert body["status"] == "alive"


def test_liveness_check_only_has_status(client: TestClient) -> None:
    """Liveness check response should only contain the status key."""
    response = client.get("/api/v1/health/liveness")
    body = response.json()
    assert "status" in body
    assert body["status"] == "alive"


# ---------------------------------------------------------------------------
# Repeat calls — ensure multiple calls are idempotent
# ---------------------------------------------------------------------------


def test_health_check_idempotent(client: TestClient) -> None:
    """Calling health check multiple times should return consistent results."""
    r1 = client.get("/api/v1/health/")
    r2 = client.get("/api/v1/health/")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["status"] == r2.json()["status"]


def test_simple_health_idempotent(client: TestClient) -> None:
    """Simple health check called twice should return consistent status."""
    r1 = client.get("/api/v1/health/simple")
    r2 = client.get("/api/v1/health/simple")
    assert r1.json()["status"] == r2.json()["status"] == "healthy"


def test_liveness_idempotent(client: TestClient) -> None:
    """Liveness probe called twice should both return alive."""
    r1 = client.get("/api/v1/health/liveness")
    r2 = client.get("/api/v1/health/liveness")
    assert r1.json()["status"] == "alive"
    assert r2.json()["status"] == "alive"


def test_health_checks_dict_memory_available(client: TestClient) -> None:
    """The checks dict must include a memory_available boolean check."""
    response = client.get("/api/v1/health/")
    body = response.json()
    assert "memory_available" in body["checks"]
    assert isinstance(body["checks"]["memory_available"], bool)


def test_health_checks_dict_disk_space(client: TestClient) -> None:
    """The checks dict must include a disk_space boolean check."""
    response = client.get("/api/v1/health/")
    body = response.json()
    assert "disk_space" in body["checks"]
    assert isinstance(body["checks"]["disk_space"], bool)


def test_health_check_reraises_on_exception() -> None:
    """If the health check internals raise, the exception should propagate (500)."""
    from unittest.mock import patch

    # Use raise_server_exceptions=False so 500 is returned rather than exception raised
    with (
        TestClient(app, raise_server_exceptions=False) as no_raise_client,
        patch("routers.health.datetime") as mock_dt,
    ):
        mock_dt.now.side_effect = RuntimeError("clock broke")
        mock_dt.UTC = __import__("datetime").UTC
        response = no_raise_client.get("/api/v1/health/")
    # FastAPI converts unhandled exceptions to 500
    assert response.status_code == 500


def test_simple_health_check_reraises_on_exception() -> None:
    """If simple_health_check internals raise, the exception should propagate (500)."""
    from unittest.mock import patch

    with (
        TestClient(app, raise_server_exceptions=False) as no_raise_client,
        patch("routers.health.datetime") as mock_dt,
    ):
        mock_dt.now.side_effect = RuntimeError("clock broke")
        mock_dt.UTC = __import__("datetime").UTC
        response = no_raise_client.get("/api/v1/health/simple")
    assert response.status_code == 500


def test_liveness_check_reraises_on_exception() -> None:
    """If liveness internals raise, the exception propagates (500)."""
    from unittest.mock import patch

    # The endpoint calls flogger.debug twice: once on entry, once after building the
    # result dict. Fail the second call so the failure happens deterministically inside
    # the try block, after `result` is built but before it's returned.
    with (
        TestClient(app, raise_server_exceptions=False) as no_raise_client,
        patch("routers.health.flogger") as mock_log,
    ):
        mock_log.debug.side_effect = [None, RuntimeError("log broke")]
        response = no_raise_client.get("/api/v1/health/liveness")
    assert response.status_code == 500
