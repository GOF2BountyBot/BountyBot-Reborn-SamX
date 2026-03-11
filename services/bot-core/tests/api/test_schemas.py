"""Tests for health check Pydantic schemas."""
from datetime import UTC, datetime

from api.schemas.health_schema import HealthResponse, SimpleHealthResponse


class TestHealthResponseSchema:
    """Tests for the HealthResponse schema."""

    def test_health_response_schema_valid(self):
        """Construct a HealthResponse and verify all fields are set correctly."""
        now = datetime.now(UTC)
        response = HealthResponse(
            status="healthy",
            timestamp=now,
            version="1.0.0",
            service="BountyBot API",
            environment={
                "python_version": "3.11.0",
                "platform": "Linux-6.1",
                "architecture": "64bit",
            },
            checks={
                "python_version": True,
                "memory_available": True,
                "disk_space": True,
                "database_connectivity": True,
                "schema_version_current": True,
            },
            database_check={
                "connectivity": True,
                "status": "healthy",
                "host": "localhost",
                "port": 5432,
                "database": "bountybot",
            },
            schema_check={
                "version_match": True,
                "current_version": "1.0.0",
                "expected_version": "1.0.0",
                "status": "current",
            },
        )

        assert response.status == "healthy"
        assert response.timestamp == now
        assert response.version == "1.0.0"
        assert response.service == "BountyBot API"
        assert response.environment["python_version"] == "3.11.0"
        assert response.checks["database_connectivity"] is True
        assert len(response.checks) == 5
        assert response.database_check["connectivity"] is True
        assert response.schema_check["version_match"] is True

    def test_health_response_optional_fields(self):
        """Verify database_check and schema_check default to None when omitted."""
        now = datetime.now(UTC)
        response = HealthResponse(
            status="unhealthy",
            timestamp=now,
            version="1.0.0",
            service="BountyBot API",
            environment={"python_version": "3.11.0"},
            checks={"python_version": True},
        )

        assert response.database_check is None
        assert response.schema_check is None
        assert response.status == "unhealthy"
        assert response.environment == {"python_version": "3.11.0"}
        assert response.checks == {"python_version": True}


class TestSimpleHealthResponseSchema:
    """Tests for the SimpleHealthResponse schema."""

    def test_simple_health_response_schema_valid(self):
        """Construct a SimpleHealthResponse and verify fields."""
        now = datetime.now(UTC)
        response = SimpleHealthResponse(
            status="healthy",
            timestamp=now,
        )

        assert response.status == "healthy"
        assert response.timestamp == now
