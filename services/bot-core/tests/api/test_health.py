"""Tests for the health check API endpoints."""

from unittest.mock import AsyncMock

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
        failing_db_manager.get_health_info = AsyncMock(
            return_value={
                "connectivity": False,
                "status": "unreachable",
                "error": "Connection refused",
            }
        )
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


# ===========================================================================
# Additional tests for uncovered branches
# ===========================================================================


class TestHealthCheckMissingManagers:
    """Tests for health_check when db_manager or schema_manager are absent."""

    def test_health_no_db_manager_marks_db_not_initialized(self):
        """health_check returns 'database_connectivity': False and db status 'not_initialized'
        when app.state does NOT have a db_manager attribute.

        Covers lines 62-64 (the else branch in the db_manager hasattr check).
        """
        from api.routers.health import router as health_router

        app = FastAPI(title="No DB Manager")
        app.include_router(health_router, prefix="/api/v1")
        # Deliberately do NOT set app.state.db_manager

        with TestClient(app) as c:
            response = c.get("/api/v1/health/")
        assert response.status_code == 200
        data = response.json()
        assert data["checks"]["database_connectivity"] is False
        assert data["database_check"]["status"] == "not_initialized"

    def test_health_db_manager_raises_exception(self):
        """health_check catches exceptions from db_manager.get_health_info and reports error.

        Covers lines 68-71 (the except block for database health).
        """
        from api.routers.health import router as health_router

        app = FastAPI(title="DB Error")
        app.include_router(health_router, prefix="/api/v1")

        broken_db_manager = AsyncMock()
        broken_db_manager.get_health_info = AsyncMock(side_effect=RuntimeError("DB unreachable"))
        app.state.db_manager = broken_db_manager

        with TestClient(app) as c:
            response = c.get("/api/v1/health/")
        assert response.status_code == 200
        data = response.json()
        assert data["checks"]["database_connectivity"] is False
        assert data["database_check"]["status"] == "error"
        assert "DB unreachable" in data["database_check"]["error"]

    def test_health_no_schema_manager_marks_schema_not_initialized(self):
        """health_check returns 'schema_version_current': False and schema status 'not_initialized'
        when app.state does NOT have a schema_manager attribute.

        Covers lines 81-86 (the else branch in the schema_manager hasattr check).
        """
        from api.routers.health import router as health_router

        app = FastAPI(title="No Schema Manager")
        app.include_router(health_router, prefix="/api/v1")

        # Provide a working db_manager so only schema branch is exercised
        working_db = AsyncMock()
        working_db.get_health_info = AsyncMock(return_value={"connectivity": True, "status": "healthy"})
        app.state.db_manager = working_db
        # Deliberately do NOT set app.state.schema_manager

        with TestClient(app) as c:
            response = c.get("/api/v1/health/")
        assert response.status_code == 200
        data = response.json()
        assert data["checks"]["schema_version_current"] is False
        assert data["schema_check"]["status"] == "not_initialized"

    def test_health_schema_manager_raises_exception(self):
        """health_check catches exceptions from schema_manager.get_schema_health_info.

        Covers lines 87-90 (the except block for schema health).
        """
        from api.routers.health import router as health_router

        app = FastAPI(title="Schema Error")
        app.include_router(health_router, prefix="/api/v1")

        working_db = AsyncMock()
        working_db.get_health_info = AsyncMock(return_value={"connectivity": True, "status": "healthy"})
        app.state.db_manager = working_db

        broken_schema = AsyncMock()
        broken_schema.get_schema_health_info = AsyncMock(side_effect=RuntimeError("Schema check failed"))
        app.state.schema_manager = broken_schema

        with TestClient(app) as c:
            response = c.get("/api/v1/health/")
        assert response.status_code == 200
        data = response.json()
        assert data["checks"]["schema_version_current"] is False
        assert data["schema_check"]["status"] == "error"
        assert "Schema check failed" in data["schema_check"]["error"]

    def test_health_marks_unhealthy_when_db_inaccessible(self):
        """service_status is 'unhealthy' when database_accessible is False.

        Covers line 95 (the warning log + unhealthy branch).
        """
        from api.routers.health import router as health_router

        app = FastAPI(title="DB Inaccessible")
        app.include_router(health_router, prefix="/api/v1")

        db_manager = AsyncMock()
        db_manager.get_health_info = AsyncMock(return_value={"connectivity": False, "status": "unreachable"})
        app.state.db_manager = db_manager

        with TestClient(app) as c:
            response = c.get("/api/v1/health/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["checks"]["database_connectivity"] is False


class TestReadinessMissingManager:
    """Tests for readiness_check when no db_manager is present."""

    def test_readiness_no_db_manager_returns_ready(self):
        """readiness_check returns 'ready' when no db_manager is in app.state.

        The code path at lines 146-148: when there is no db_manager, the try
        block completes without raising and returns {"status": "ready"}.
        """
        from api.routers.health import router as health_router

        app = FastAPI(title="No DB Readiness")
        app.include_router(health_router, prefix="/api/v1")
        # No db_manager on state

        with TestClient(app) as c:
            response = c.get("/api/v1/health/readiness")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}

    def test_readiness_unexpected_exception_returns_503(self):
        """readiness_check returns 503 when an unexpected exception is raised.

        Covers lines 146-151 (the generic except block).
        """
        from api.routers.health import router as health_router

        app = FastAPI(title="Readiness Error")
        app.include_router(health_router, prefix="/api/v1")

        exploding_db = AsyncMock()
        exploding_db.get_health_info = AsyncMock(side_effect=RuntimeError("Network timeout"))
        app.state.db_manager = exploding_db

        with TestClient(app) as c:
            response = c.get("/api/v1/health/readiness")
        assert response.status_code == 503
        assert "Service not ready" in response.json()["detail"]


class TestDatabaseHealthMissingManagers:
    """Tests for database_health_check when db_manager or schema_manager are absent."""

    def test_database_health_no_db_manager_returns_not_initialized(self):
        """database_health_check populates database as not_initialized when no db_manager.

        Covers lines 184-187 (the else branch for database in database_health_check).
        Also triggers the 503 since connectivity is False (line 201).
        """
        from api.routers.health import router as health_router

        app = FastAPI(title="No DB for /database")
        app.include_router(health_router, prefix="/api/v1")
        # No db_manager

        with TestClient(app, raise_server_exceptions=False) as c:
            response = c.get("/api/v1/health/database")
        # No db_manager → connectivity=False → 503
        assert response.status_code == 503

    def test_database_health_no_schema_manager_returns_not_initialized(self):
        """database_health_check populates schema as not_initialized when no schema_manager.

        Covers lines 194-197 (the else branch for schema in database_health_check).
        When db is accessible but schema manager absent, schema shows not_initialized.
        """
        from api.routers.health import router as health_router

        app = FastAPI(title="No Schema for /database")
        app.include_router(health_router, prefix="/api/v1")

        working_db = AsyncMock()
        working_db.get_health_info = AsyncMock(return_value={"connectivity": True, "status": "healthy"})
        app.state.db_manager = working_db
        # No schema_manager

        with TestClient(app) as c:
            response = c.get("/api/v1/health/database")
        assert response.status_code == 200
        data = response.json()
        assert data["schema"]["status"] == "not_initialized"

    def test_database_health_db_not_accessible_returns_503(self):
        """database_health_check raises 503 when database connectivity is False.

        Covers line 201 (the HTTPException for no connectivity).
        """
        from api.routers.health import router as health_router

        app = FastAPI(title="DB Not Accessible")
        app.include_router(health_router, prefix="/api/v1")

        db_manager = AsyncMock()
        db_manager.get_health_info = AsyncMock(return_value={"connectivity": False, "status": "unreachable"})
        app.state.db_manager = db_manager

        with TestClient(app, raise_server_exceptions=False) as c:
            response = c.get("/api/v1/health/database")
        assert response.status_code == 503

    def test_database_health_unexpected_exception_returns_500(self):
        """database_health_check returns 500 when an unexpected exception occurs.

        Covers lines 208-214 (the generic except block in database_health_check).
        """
        from api.routers.health import router as health_router

        app = FastAPI(title="DB Exception")
        app.include_router(health_router, prefix="/api/v1")

        exploding_db = AsyncMock()
        exploding_db.get_health_info = AsyncMock(side_effect=RuntimeError("Unexpected crash"))
        app.state.db_manager = exploding_db

        with TestClient(app, raise_server_exceptions=False) as c:
            response = c.get("/api/v1/health/database")
        assert response.status_code == 500
