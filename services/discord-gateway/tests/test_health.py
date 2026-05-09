"""Tests for the health check API endpoints."""


class TestHealthCheck:
    def test_health_check_returns_200(self, client):
        """GET /api/v1/health should return 200 with all expected fields."""
        response = client.get("/api/v1/health")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data
        assert "version" in data
        assert "service" in data
        assert data["service"] == "Discord Gateway API"
        assert "environment" in data
        assert "python_version" in data["environment"]
        assert "platform" in data["environment"]
        assert "architecture" in data["environment"]
        assert "checks" in data
        assert isinstance(data["checks"], dict)

    def test_simple_health_returns_200(self, client):
        """GET /api/v1/health/simple should return 200 with status healthy."""
        response = client.get("/api/v1/health/simple")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data

    def test_liveness_returns_alive(self, client):
        """GET /api/v1/healthliveness should return {"status": "alive"}.

        Note: the route path is ``"liveness"`` (no leading slash), so it
        concatenates with the router prefix as ``/health`` + ``liveness``
        → ``/api/v1/healthliveness``.
        """
        response = client.get("/api/v1/healthliveness")
        assert response.status_code == 200
        assert response.json() == {"status": "alive"}
