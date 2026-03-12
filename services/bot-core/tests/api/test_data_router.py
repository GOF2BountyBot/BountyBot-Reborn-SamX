"""Tests for the data API router endpoints.

Import path setup and sqlalchemy_utils mocking are handled by
tests/api/conftest.py which runs before this module is loaded.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_app():
    from api.routers.data import router as data_router

    app = FastAPI()
    app.include_router(data_router, prefix="/api/v1")
    return app


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


# ===========================================================================
# 1. GET /data/categories
# ===========================================================================


class TestListDataCategories:
    """Tests for GET /api/v1/data/categories."""

    def test_list_categories_happy_path(self, client):
        """Returns 200 with list of category strings."""
        response = client.get("/api/v1/data/categories")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_list_categories_includes_expected_values(self, client):
        """Returns all expected data categories."""
        response = client.get("/api/v1/data/categories")

        assert response.status_code == 200
        data = response.json()
        expected = {"module", "primary_weapon", "secondary_weapon", "turret_weapon", "ship", "criminal", "system"}
        returned = set(data)
        assert expected == returned

    def test_list_categories_returns_strings(self, client):
        """All returned categories are strings."""
        response = client.get("/api/v1/data/categories")

        assert response.status_code == 200
        for cat in response.json():
            assert isinstance(cat, str)


# ===========================================================================
# 2. POST /data/{category}
# ===========================================================================


class TestApiLoadData:
    """Tests for POST /api/v1/data/{category}."""

    @patch("api.routers.data.load_data")
    def test_load_data_module_happy_path(self, mock_load, client):
        """Returns 200 with list of loaded names for 'module' category."""
        mock_load.return_value = AsyncMock(return_value=["Module A", "Module B"])()
        mock_load.side_effect = None
        mock_load.return_value = ["Module A", "Module B"]

        response = client.post("/api/v1/data/module")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    @patch("api.routers.data.load_data")
    def test_load_data_ship_category(self, mock_load, client):
        """Returns 200 for 'ship' category."""
        mock_load.return_value = ["Ship X"]

        response = client.post("/api/v1/data/ship")

        assert response.status_code == 200

    @patch("api.routers.data.load_data")
    def test_load_data_primary_weapon_category(self, mock_load, client):
        """Returns 200 for 'primary_weapon' category."""
        mock_load.return_value = ["Pulse Laser"]

        response = client.post("/api/v1/data/primary_weapon")

        assert response.status_code == 200

    @patch("api.routers.data.load_data")
    def test_load_data_secondary_weapon_category(self, mock_load, client):
        """Returns 200 for 'secondary_weapon' category."""
        mock_load.return_value = ["Rocket Pod"]

        response = client.post("/api/v1/data/secondary_weapon")

        assert response.status_code == 200

    @patch("api.routers.data.load_data")
    def test_load_data_turret_weapon_category(self, mock_load, client):
        """Returns 200 for 'turret_weapon' category."""
        mock_load.return_value = ["Turret Alpha"]

        response = client.post("/api/v1/data/turret_weapon")

        assert response.status_code == 200

    @patch("api.routers.data.load_data")
    def test_load_data_criminal_category(self, mock_load, client):
        """Returns 200 for 'criminal' category."""
        mock_load.return_value = ["Bandit"]

        response = client.post("/api/v1/data/criminal")

        assert response.status_code == 200

    @patch("api.routers.data.load_data")
    def test_load_data_system_category(self, mock_load, client):
        """Returns 200 for 'system' category."""
        mock_load.return_value = ["Sol"]

        response = client.post("/api/v1/data/system")

        assert response.status_code == 200

    @patch("api.routers.data.load_data")
    def test_load_data_calls_load_with_category_value(self, mock_load, client):
        """Passes the correct category value string to load_data."""
        mock_load.return_value = []

        client.post("/api/v1/data/module")

        mock_load.assert_called_once_with("module")

    @patch("api.routers.data.load_data")
    def test_load_data_value_error_returns_404(self, mock_load, client):
        """Returns 404 when load_data raises ValueError."""
        mock_load.side_effect = ValueError("Category files not found")

        response = client.post("/api/v1/data/module")

        assert response.status_code == 404
        assert "Category files not found" in response.json()["detail"]

    def test_load_data_invalid_category_returns_422(self, client):
        """Returns 422 when category is not a valid DataCategory enum value."""
        response = client.post("/api/v1/data/invalid_category")

        assert response.status_code == 422

    def test_load_data_empty_category_not_found(self, client):
        """Returns 404/405 for empty category segment."""
        # categories endpoint exists at /data/categories (GET), not POST
        response = client.post("/api/v1/data/")

        # Either 404 (route not found) or 405 (method not allowed) is acceptable
        assert response.status_code in (404, 405, 422)

    @patch("api.routers.data.load_data")
    def test_load_data_returns_empty_list(self, mock_load, client):
        """Returns 200 with empty list when no data files exist."""
        mock_load.return_value = []

        response = client.post("/api/v1/data/module")

        assert response.status_code == 200
        assert response.json() == []
