"""Tests for the health check API endpoints."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient


class TestSimpleHealth:
    """Tests for GET /api/v1/health/simple."""

    def test_simple_health_returns_200(self, client):
        """GET /api/v1/health/simple returns 200 with status 'healthy'."""
        response = client.get("/api/v1/health/simple")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data


class TestLiveness:
    """Tests for GET /api/v1/health/liveness."""

    def test_liveness_returns_alive(self, client):
        """GET /api/v1/health/liveness returns {"status": "alive"}."""
        response = client.get("/api/v1/health/liveness")
        assert response.status_code == 200
        data = response.json()
        assert data == {"status": "alive"}


class TestHealthCheck:
    """Tests for GET /api/v1/health/ (comprehensive)."""

    def test_health_check_returns_healthy_when_db_accessible(self, client):
        """GET /api/v1/health/ returns 200 with status 'healthy' when db reports connectivity=True."""
        response = client.get("/api/v1/health/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["version"] == "1.0.0"
        assert data["service"] == "BountyBot API"
        assert "timestamp" in data
        assert "environment" in data
        assert "checks" in data
        assert data["checks"]["database_connectivity"] is True
        assert data["checks"]["schema_version_current"] is True
        assert data["database_check"] is not None
        assert data["database_check"]["connectivity"] is True
        assert data["schema_check"] is not None
        assert data["schema_check"]["version_match"] is True


class TestReadiness:
    """Tests for GET /api/v1/health/readiness."""

    def test_readiness_returns_ready_when_db_accessible(self, client):
        """GET /api/v1/health/readiness returns {"status": "ready"} when db is accessible."""
        response = client.get("/api/v1/health/readiness")
        assert response.status_code == 200
        data = response.json()
        assert data == {"status": "ready"}

    def test_readiness_returns_503_when_no_db_manager(self):
        """Readiness returns 503 when db_manager reports connectivity=False.

        Creates a standalone test app where the db_manager's get_health_info
        returns connectivity=False, which triggers the 503 path.
        """
        from api.routers.health import router as health_router

        app = FastAPI(title="BountyBot API Test - No DB")
        app.include_router(health_router, prefix="/api/v1")

        # Set a db_manager that reports connectivity=False
        failing_db_manager = AsyncMock()
        failing_db_manager.get_health_info = AsyncMock(return_value={
            "connectivity": False,
            "status": "unreachable",
            "error": "Connection refused",
        })
        app.state.db_manager = failing_db_manager

        with TestClient(app) as no_db_client:
            response = no_db_client.get("/api/v1/health/readiness")
            assert response.status_code == 503


class TestDatabaseHealth:
    """Tests for GET /api/v1/health/database."""

    def test_database_health_returns_info(self, client):
        """GET /api/v1/health/database returns db + schema info."""
        response = client.get("/api/v1/health/database")
        assert response.status_code == 200
        data = response.json()
        assert "timestamp" in data
        assert "database" in data
        assert "schema" in data
        # Verify the mock data is returned
        assert data["database"]["connectivity"] is True
        assert data["database"]["status"] == "healthy"
        assert data["schema"]["version_match"] is True
        assert data["schema"]["current_version"] == "1.0.0"
