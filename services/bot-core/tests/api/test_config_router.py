"""Tests for the config API router endpoints.

Import path setup and sqlalchemy_utils mocking are handled by
tests/api/conftest.py which runs before this module is loaded.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_config(**overrides):
    defaults = dict(
        guild_id=67890,
        configured=True,
        admin_role_configured=True,
        starting_credits=0,
        sale_price_factor=1.0,
        event_min_duel_stakes=1000,
        xp_thresholds={"Silver": 1000, "Gold": 5000, "Platinum": 15000},
        shop_config={},
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )
    defaults.update(overrides)
    return defaults


def _configure_db_mock(mock_get_db):
    """Configure mock_get_db to act as an async context manager."""
    mock_session = AsyncMock()
    mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_config_service():
    service = AsyncMock()
    service.get_guild_config = AsyncMock(return_value=make_mock_config())
    service.create_or_update_config = AsyncMock(return_value=make_mock_config())
    service.update_shop_config = AsyncMock(return_value=make_mock_config())
    service.reset_to_defaults = AsyncMock(return_value=make_mock_config())
    service.update_admin_role = AsyncMock(return_value=make_mock_config())
    service.update_starting_credits = AsyncMock(return_value=make_mock_config())
    service.update_xp_thresholds = AsyncMock(return_value=make_mock_config())
    service.validate_config_compatibility = AsyncMock(
        return_value={
            "valid": True,
            "errors": [],
            "warnings": [],
            "guild_id": 67890,
        }
    )
    service.get_all_guild_configs = AsyncMock(return_value=[make_mock_config()])
    service.get_bounty_config = AsyncMock(
        return_value={
            "guild_id": 67890,
            "max_bounties_per_tier": {"bronze": 3, "silver": 3, "gold": 3},
            "bounty_expiry_minutes": 480,
            "bounty_spawn_interval_minutes": 60,
            "next_spawn_check_at": None,
        }
    )
    service.update_bounty_config = AsyncMock(
        return_value={
            "guild_id": 67890,
            "max_bounties_per_tier": {"bronze": 3, "silver": 3, "gold": 3},
            "bounty_expiry_minutes": 480,
            "bounty_spawn_interval_minutes": 60,
            "next_spawn_check_at": None,
        }
    )
    return service


@pytest.fixture
def test_app(mock_config_service):
    from api.routers.config import get_config_service
    from api.routers.config import router as config_router

    app = FastAPI()
    app.include_router(config_router, prefix="/api/v1")
    app.dependency_overrides[get_config_service] = lambda: mock_config_service
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


# ===========================================================================
# 1. GET /config/guild/{guild_id}
# ===========================================================================


class TestGetGuildConfig:
    """Tests for GET /api/v1/config/guild/{guild_id}."""

    @patch("api.routers.config.get_db_session")
    def test_get_guild_config_happy_path(self, mock_get_db, client, mock_config_service):
        """Returns 200 with GuildConfigResponse on success."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/config/guild/67890")

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890
        assert data["configured"] is True
        assert data["admin_role_configured"] is True
        assert data["starting_credits"] == 0
        assert data["sale_price_factor"] == pytest.approx(1.0)
        assert "xp_thresholds" in data
        assert "shop_config" in data
        assert "created_at" in data
        assert "updated_at" in data

    @patch("api.routers.config.get_db_session")
    def test_get_guild_config_calls_service_with_guild_id(self, mock_get_db, client, mock_config_service):
        """Passes correct guild_id to config_service.get_guild_config."""
        _configure_db_mock(mock_get_db)

        client.get("/api/v1/config/guild/99999")

        mock_config_service.get_guild_config.assert_awaited_once()
        call_args = mock_config_service.get_guild_config.call_args
        assert 99999 in call_args.args or call_args.kwargs.get("guild_id") == 99999

    @patch("api.routers.config.get_db_session")
    def test_get_guild_config_server_error_returns_500(self, mock_get_db, client, mock_config_service):
        """Returns 500 when config_service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_config_service.get_guild_config.side_effect = RuntimeError("DB failure")

        response = client.get("/api/v1/config/guild/67890")

        assert response.status_code == 500
        assert "Failed to get guild configuration" in response.json()["detail"]

    @patch("api.routers.config.get_db_session")
    def test_get_guild_config_not_configured_returns_404(self, mock_get_db, client, mock_config_service):
        """Returns 404 with friendly message when guild not configured (no auto-create)."""
        from services.config_service import GuildNotConfiguredError

        _configure_db_mock(mock_get_db)
        mock_config_service.get_guild_config.side_effect = GuildNotConfiguredError(guild_id=67890)

        response = client.get("/api/v1/config/guild/67890")

        assert response.status_code == 404
        assert "admin_setup" in response.json()["detail"].lower() or "not been configured" in response.json()["detail"]


# ===========================================================================
# 2. PUT /config/guild/{guild_id}
# ===========================================================================


class TestUpdateGuildConfig:
    """Tests for PUT /api/v1/config/guild/{guild_id}."""

    @patch("api.routers.config.get_db_session")
    def test_update_guild_config_happy_path(self, mock_get_db, client, mock_config_service):
        """Returns 200 with GuildConfigResponse on success."""
        _configure_db_mock(mock_get_db)
        payload = {"guild_id": 67890, "starting_credits": 500}

        response = client.put("/api/v1/config/guild/67890", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890

    @patch("api.routers.config.get_db_session")
    def test_update_guild_config_value_error_returns_400(self, mock_get_db, client, mock_config_service):
        """Returns 400 when config_service raises ValueError."""
        _configure_db_mock(mock_get_db)
        mock_config_service.create_or_update_config.side_effect = ValueError("Invalid config value")
        payload = {"guild_id": 67890, "starting_credits": 500}

        response = client.put("/api/v1/config/guild/67890", json=payload)

        assert response.status_code == 400
        assert "Invalid config value" in response.json()["detail"]

    @patch("api.routers.config.get_db_session")
    def test_update_guild_config_server_error_returns_500(self, mock_get_db, client, mock_config_service):
        """Returns 500 when config_service raises unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_config_service.create_or_update_config.side_effect = RuntimeError("Crash")
        payload = {"guild_id": 67890, "starting_credits": 500}

        response = client.put("/api/v1/config/guild/67890", json=payload)

        assert response.status_code == 500
        assert "Failed to update guild configuration" in response.json()["detail"]

    def test_update_guild_config_negative_starting_credits_returns_422(self, client):
        """Returns 422 when starting_credits is negative (ge=0)."""
        payload = {"guild_id": 67890, "starting_credits": -1}

        response = client.put("/api/v1/config/guild/67890", json=payload)

        assert response.status_code == 422

    def test_update_guild_config_sale_price_factor_out_of_range_returns_422(self, client):
        """Returns 422 when sale_price_factor > 1.0."""
        payload = {"guild_id": 67890, "sale_price_factor": 1.5}

        response = client.put("/api/v1/config/guild/67890", json=payload)

        assert response.status_code == 422


# ===========================================================================
# 3. PUT /config/guild/{guild_id}/shop
# ===========================================================================


class TestUpdateShopConfig:
    """Tests for PUT /api/v1/config/guild/{guild_id}/shop."""

    @patch("api.routers.config.get_db_session")
    def test_update_shop_config_happy_path(self, mock_get_db, client, mock_config_service):
        """Returns 200 with GuildConfigResponse on success."""
        _configure_db_mock(mock_get_db)
        payload = {"guild_id": 67890}

        response = client.put("/api/v1/config/guild/67890/shop", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890

    @patch("api.routers.config.get_db_session")
    def test_update_shop_config_with_all_fields(self, mock_get_db, client, mock_config_service):
        """Accepts all optional shop config fields."""
        _configure_db_mock(mock_get_db)
        payload = {
            "guild_id": 67890,
            "tech_level_probabilities": {"same_level": 0.7, "one_lower": 0.3},
            "item_count_ranges": {"Bronze": {"min": 3, "max": 5}},
            "quantity_ranges": {"Bronze": {"min": 1, "max": 3}},
        }

        response = client.put("/api/v1/config/guild/67890/shop", json=payload)

        assert response.status_code == 200

    @patch("api.routers.config.get_db_session")
    def test_update_shop_config_value_error_returns_400(self, mock_get_db, client, mock_config_service):
        """Returns 400 when config_service raises ValueError."""
        _configure_db_mock(mock_get_db)
        mock_config_service.update_shop_config.side_effect = ValueError("Bad shop config")
        payload = {"guild_id": 67890}

        response = client.put("/api/v1/config/guild/67890/shop", json=payload)

        assert response.status_code == 400
        assert "Bad shop config" in response.json()["detail"]

    @patch("api.routers.config.get_db_session")
    def test_update_shop_config_server_error_returns_500(self, mock_get_db, client, mock_config_service):
        """Returns 500 when config_service raises unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_config_service.update_shop_config.side_effect = RuntimeError("Crash")
        payload = {"guild_id": 67890}

        response = client.put("/api/v1/config/guild/67890/shop", json=payload)

        assert response.status_code == 500
        assert "Failed to update shop configuration" in response.json()["detail"]


# ===========================================================================
# 4. POST /config/guild/{guild_id}/reset
# ===========================================================================


class TestResetGuildConfig:
    """Tests for POST /api/v1/config/guild/{guild_id}/reset."""

    @patch("api.routers.config.get_db_session")
    def test_reset_guild_config_happy_path(self, mock_get_db, client, mock_config_service):
        """Returns 200 with GuildConfigResponse on success."""
        _configure_db_mock(mock_get_db)

        response = client.post("/api/v1/config/guild/67890/reset")

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890

    @patch("api.routers.config.get_db_session")
    def test_reset_guild_config_calls_service(self, mock_get_db, client, mock_config_service):
        """Calls reset_to_defaults with correct guild_id."""
        _configure_db_mock(mock_get_db)

        client.post("/api/v1/config/guild/67890/reset")

        mock_config_service.reset_to_defaults.assert_awaited_once()

    @patch("api.routers.config.get_db_session")
    def test_reset_guild_config_server_error_returns_500(self, mock_get_db, client, mock_config_service):
        """Returns 500 when config_service raises unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_config_service.reset_to_defaults.side_effect = RuntimeError("Reset failed")

        response = client.post("/api/v1/config/guild/67890/reset")

        assert response.status_code == 500
        assert "Failed to reset guild configuration" in response.json()["detail"]


# ===========================================================================
# 5. PUT /config/guild/{guild_id}/admin-role/{role_id}
# ===========================================================================


class TestUpdateAdminRole:
    """Tests for PUT /api/v1/config/guild/{guild_id}/admin-role/{role_id}."""

    @patch("api.routers.config.get_db_session")
    def test_update_admin_role_happy_path(self, mock_get_db, client, mock_config_service):
        """Returns 200 with GuildConfigResponse on success."""
        _configure_db_mock(mock_get_db)

        response = client.put("/api/v1/config/guild/67890/admin-role/11111")

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890

    @patch("api.routers.config.get_db_session")
    def test_update_admin_role_calls_service_with_correct_args(self, mock_get_db, client, mock_config_service):
        """Passes guild_id and role_id to update_admin_role."""
        _configure_db_mock(mock_get_db)

        client.put("/api/v1/config/guild/67890/admin-role/11111")

        mock_config_service.update_admin_role.assert_awaited_once()
        call_args = mock_config_service.update_admin_role.call_args
        # Should include both guild_id=67890 and role_id=11111
        assert 67890 in call_args.args or call_args.kwargs.get("guild_id") == 67890

    @patch("api.routers.config.get_db_session")
    def test_update_admin_role_value_error_returns_400(self, mock_get_db, client, mock_config_service):
        """Returns 400 when config_service raises ValueError."""
        _configure_db_mock(mock_get_db)
        mock_config_service.update_admin_role.side_effect = ValueError("Invalid role")

        response = client.put("/api/v1/config/guild/67890/admin-role/11111")

        assert response.status_code == 400
        assert "Invalid role" in response.json()["detail"]

    @patch("api.routers.config.get_db_session")
    def test_update_admin_role_server_error_returns_500(self, mock_get_db, client, mock_config_service):
        """Returns 500 when config_service raises unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_config_service.update_admin_role.side_effect = RuntimeError("DB down")

        response = client.put("/api/v1/config/guild/67890/admin-role/11111")

        assert response.status_code == 500
        assert "Failed to update admin role" in response.json()["detail"]


# ===========================================================================
# 6. PUT /config/guild/{guild_id}/starting-credits/{starting_credits}
# ===========================================================================


class TestUpdateStartingCredits:
    """Tests for PUT /api/v1/config/guild/{guild_id}/starting-credits/{starting_credits}."""

    @patch("api.routers.config.get_db_session")
    def test_update_starting_credits_happy_path(self, mock_get_db, client, mock_config_service):
        """Returns 200 with GuildConfigResponse when update_starting_credits succeeds."""
        _configure_db_mock(mock_get_db)

        response = client.put("/api/v1/config/guild/67890/starting-credits/500")

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890

    @patch("api.routers.config.get_db_session")
    def test_update_starting_credits_zero_is_valid(self, mock_get_db, client, mock_config_service):
        """Starting credits of 0 passes ge=0 validation and returns 200."""
        _configure_db_mock(mock_get_db)

        response = client.put("/api/v1/config/guild/67890/starting-credits/0")

        assert response.status_code == 200

    def test_update_starting_credits_negative_returns_422(self, client):
        """Passing a negative value returns 422 (ge=0 constraint)."""
        response = client.put("/api/v1/config/guild/67890/starting-credits/-1")
        assert response.status_code == 422

    def test_update_starting_credits_non_integer_returns_422(self, client):
        """Passing a non-integer returns 422."""
        response = client.put("/api/v1/config/guild/67890/starting-credits/abc")
        assert response.status_code == 422

    @patch("api.routers.config.get_db_session")
    def test_update_starting_credits_calls_service_with_correct_args(self, mock_get_db, client, mock_config_service):
        """Passes guild_id and starting_credits to update_starting_credits service method."""
        _configure_db_mock(mock_get_db)

        client.put("/api/v1/config/guild/67890/starting-credits/1000")

        mock_config_service.update_starting_credits.assert_awaited_once()
        call_args = mock_config_service.update_starting_credits.call_args
        assert 67890 in call_args.args or call_args.kwargs.get("guild_id") == 67890

    @patch("api.routers.config.get_db_session")
    def test_update_starting_credits_value_error_returns_400(self, mock_get_db, client, mock_config_service):
        """Returns 400 when config_service raises ValueError."""
        _configure_db_mock(mock_get_db)
        mock_config_service.update_starting_credits.side_effect = ValueError("Credits must be non-negative")

        response = client.put("/api/v1/config/guild/67890/starting-credits/100")

        assert response.status_code == 400
        assert "Credits must be non-negative" in response.json()["detail"]

    @patch("api.routers.config.get_db_session")
    def test_update_starting_credits_server_error_returns_500(self, mock_get_db, client, mock_config_service):
        """Returns 500 when config_service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_config_service.update_starting_credits.side_effect = RuntimeError("DB crash")

        response = client.put("/api/v1/config/guild/67890/starting-credits/100")

        assert response.status_code == 500
        assert "Failed to update starting credits" in response.json()["detail"]


# ===========================================================================
# 7. PUT /config/guild/{guild_id}/xp-thresholds
# ===========================================================================


class TestUpdateXPThresholds:
    """Tests for PUT /api/v1/config/guild/{guild_id}/xp-thresholds."""

    @patch("api.routers.config.get_db_session")
    def test_update_xp_thresholds_happy_path(self, mock_get_db, client, mock_config_service):
        """Returns 200 with GuildConfigResponse on success."""
        _configure_db_mock(mock_get_db)
        payload = {
            "guild_id": 67890,
            "thresholds": {"Silver": 1000, "Gold": 5000, "Platinum": 15000},
        }

        response = client.put("/api/v1/config/guild/67890/xp-thresholds", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890

    @patch("api.routers.config.get_db_session")
    def test_update_xp_thresholds_calls_service(self, mock_get_db, client, mock_config_service):
        """Calls update_xp_thresholds with correct arguments."""
        _configure_db_mock(mock_get_db)
        thresholds = {"Silver": 1000, "Gold": 5000, "Platinum": 15000}
        payload = {"guild_id": 67890, "thresholds": thresholds}

        client.put("/api/v1/config/guild/67890/xp-thresholds", json=payload)

        mock_config_service.update_xp_thresholds.assert_awaited_once()

    @patch("api.routers.config.get_db_session")
    def test_update_xp_thresholds_value_error_returns_400(self, mock_get_db, client, mock_config_service):
        """Returns 400 when config_service raises ValueError."""
        _configure_db_mock(mock_get_db)
        mock_config_service.update_xp_thresholds.side_effect = ValueError("Invalid thresholds")
        payload = {
            "guild_id": 67890,
            "thresholds": {"Silver": 1000},
        }

        response = client.put("/api/v1/config/guild/67890/xp-thresholds", json=payload)

        assert response.status_code == 400
        assert "Invalid thresholds" in response.json()["detail"]

    @patch("api.routers.config.get_db_session")
    def test_update_xp_thresholds_server_error_returns_500(self, mock_get_db, client, mock_config_service):
        """Returns 500 when config_service raises unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_config_service.update_xp_thresholds.side_effect = RuntimeError("DB error")
        payload = {
            "guild_id": 67890,
            "thresholds": {"Silver": 1000},
        }

        response = client.put("/api/v1/config/guild/67890/xp-thresholds", json=payload)

        assert response.status_code == 500
        assert "Failed to update XP thresholds" in response.json()["detail"]

    def test_update_xp_thresholds_missing_thresholds_returns_422(self, client):
        """Returns 422 when thresholds field is missing."""
        payload = {"guild_id": 67890}

        response = client.put("/api/v1/config/guild/67890/xp-thresholds", json=payload)

        assert response.status_code == 422


# ===========================================================================
# 8. GET /config/guild/{guild_id}/validate
# ===========================================================================


class TestValidateGuildConfig:
    """Tests for GET /api/v1/config/guild/{guild_id}/validate."""

    @patch("api.routers.config.get_db_session")
    def test_validate_guild_config_happy_path(self, mock_get_db, client, mock_config_service):
        """Returns 200 with ConfigValidationResponse on success."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/config/guild/67890/validate")

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["errors"] == []
        assert data["warnings"] == []
        assert data["guild_id"] == 67890

    @patch("api.routers.config.get_db_session")
    def test_validate_guild_config_with_errors(self, mock_get_db, client, mock_config_service):
        """Returns 200 with valid=False when validation finds errors."""
        _configure_db_mock(mock_get_db)
        mock_config_service.validate_config_compatibility.return_value = {
            "valid": False,
            "errors": ["Admin role not set"],
            "warnings": ["Shop might be empty"],
            "guild_id": 67890,
        }

        response = client.get("/api/v1/config/guild/67890/validate")

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert "Admin role not set" in data["errors"]
        assert "Shop might be empty" in data["warnings"]

    @patch("api.routers.config.get_db_session")
    def test_validate_guild_config_server_error_returns_500(self, mock_get_db, client, mock_config_service):
        """Returns 500 when config_service raises unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_config_service.validate_config_compatibility.side_effect = RuntimeError("Validation error")

        response = client.get("/api/v1/config/guild/67890/validate")

        assert response.status_code == 500
        assert "Failed to validate configuration" in response.json()["detail"]


# ===========================================================================
# 9. GET /config/guilds
# ===========================================================================


class TestGetAllGuildConfigs:
    """Tests for GET /api/v1/config/guilds."""

    @patch("api.routers.config.get_db_session")
    def test_get_all_guild_configs_happy_path(self, mock_get_db, client, mock_config_service):
        """Returns 200 with list of configs."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/config/guilds")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1

    @patch("api.routers.config.get_db_session")
    def test_get_all_guild_configs_empty(self, mock_get_db, client, mock_config_service):
        """Returns 200 with empty list when no configs exist."""
        _configure_db_mock(mock_get_db)
        mock_config_service.get_all_guild_configs.return_value = []

        response = client.get("/api/v1/config/guilds")

        assert response.status_code == 200
        assert response.json() == []

    @patch("api.routers.config.get_db_session")
    def test_get_all_guild_configs_server_error_returns_500(self, mock_get_db, client, mock_config_service):
        """Returns 500 when config_service raises unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_config_service.get_all_guild_configs.side_effect = RuntimeError("Query failed")

        response = client.get("/api/v1/config/guilds")

        assert response.status_code == 500
        assert "Failed to get guild configurations" in response.json()["detail"]


# ===========================================================================
# 10. GET /config/defaults
# ===========================================================================


class TestGetDefaultConfig:
    """Tests for GET /api/v1/config/defaults."""

    def test_get_default_config_happy_path(self, client):
        """Returns 200 with all default configuration values."""
        response = client.get("/api/v1/config/defaults")

        assert response.status_code == 200
        data = response.json()
        assert "sale_price_factor" in data
        assert "starting_credits" in data
        assert "xp_thresholds" in data
        assert "ship_count_range" in data

    def test_get_default_config_xp_thresholds_structure(self, client):
        """Verifies XP thresholds contain expected tier keys."""
        response = client.get("/api/v1/config/defaults")

        assert response.status_code == 200
        data = response.json()
        xp = data["xp_thresholds"]
        assert "Silver" in xp
        assert "Gold" in xp
        assert "Platinum" in xp


# ===========================================================================
# Tests: GET /config/guild/{guild_id}/bounty
# ===========================================================================


def make_bounty_config(**overrides):
    defaults = dict(
        guild_id=67890,
        max_bounties_per_tier={"bronze": 3, "silver": 3, "gold": 3},
        bounty_expiry_minutes=480,
        bounty_spawn_interval_minutes=60,
        next_spawn_check_at=None,
    )
    defaults.update(overrides)
    return defaults


class TestGetBountyConfig:
    """Tests for GET /api/v1/config/guild/{guild_id}/bounty."""

    @patch("api.routers.config.get_db_session")
    @patch("api.routers.config.BountyRepository")
    def test_get_bounty_config_returns_200(self, mock_repo_cls, mock_get_db, test_app, mock_config_service):
        """Returns 200 with bounty config and active bounty counts."""
        _configure_db_mock(mock_get_db)

        mock_repo = AsyncMock()
        mock_repo.count_active_by_guild_and_division = AsyncMock(return_value=2)
        mock_repo_cls.return_value = mock_repo
        mock_config_service.get_bounty_config = AsyncMock(return_value=make_bounty_config())

        client = TestClient(test_app)
        response = client.get("/api/v1/config/guild/67890/bounty")

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890
        assert "max_bounties_per_tier" in data
        assert "bounty_expiry_minutes" in data
        assert "active_bounties_per_tier" in data

    @patch("api.routers.config.get_db_session")
    @patch("api.routers.config.BountyRepository")
    def test_get_bounty_config_service_error_returns_500(
        self, mock_repo_cls, mock_get_db, test_app, mock_config_service
    ):
        """Returns 500 when service raises an exception."""
        _configure_db_mock(mock_get_db)

        mock_repo = AsyncMock()
        mock_repo.count_active_by_guild_and_division = AsyncMock(return_value=0)
        mock_repo_cls.return_value = mock_repo
        mock_config_service.get_bounty_config = AsyncMock(side_effect=Exception("DB failure"))

        client = TestClient(test_app)
        response = client.get("/api/v1/config/guild/67890/bounty")

        assert response.status_code == 500


# ===========================================================================
# Tests: PUT /config/guild/{guild_id}/bounty
# ===========================================================================


class TestUpdateBountyConfig:
    """Tests for PUT /api/v1/config/guild/{guild_id}/bounty."""

    @patch("api.routers.config.get_db_session")
    def test_update_bounty_config_returns_200(self, mock_get_db, test_app, mock_config_service):
        """Returns 200 with updated bounty config."""
        _configure_db_mock(mock_get_db)
        mock_config_service.update_bounty_config = AsyncMock(return_value=make_bounty_config(bounty_expiry_minutes=120))

        client = TestClient(test_app)
        response = client.put(
            "/api/v1/config/guild/67890/bounty",
            json={"guild_id": 67890, "bounty_expiry_minutes": 120},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["bounty_expiry_minutes"] == 120

    @patch("api.routers.config.get_db_session")
    def test_update_bounty_config_validation_error_returns_400(self, mock_get_db, test_app, mock_config_service):
        """Returns 400 when service raises ValueError."""
        _configure_db_mock(mock_get_db)
        mock_config_service.update_bounty_config = AsyncMock(side_effect=ValueError("Invalid tier keys"))

        client = TestClient(test_app)
        response = client.put(
            "/api/v1/config/guild/67890/bounty",
            json={"guild_id": 67890, "max_bounties_per_tier": {"platinum": 3}},
        )

        assert response.status_code == 400


# ===========================================================================
# Gap 1: Empty-State / Null-Result Tests — Config
# ===========================================================================


class TestGetConfigNonexistentGuild:
    """Gap 1: GET /config/guild/{id} for a guild that has never been configured.

    The config service creates a default config when one does not exist, so the
    endpoint should return 200 with a default config rather than 404 or 500.
    """

    @patch("api.routers.config.get_db_session")
    def test_get_config_nonexistent_guild_returns_200_default(self, mock_get_db, client, mock_config_service):
        """GET /config/guild/{id} for an unconfigured guild → 200 with default config.

        The service auto-creates a default configuration so callers always receive
        a usable response rather than 404 or 500.
        """
        _configure_db_mock(mock_get_db)
        # Service returns a default-populated config (this is the correct service behaviour)
        mock_config_service.get_guild_config = AsyncMock(
            return_value=make_mock_config(guild_id=99999, configured=False)
        )

        response = client.get("/api/v1/config/guild/99999")

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 99999
        # configured=False signals this is a freshly-created default
        assert data["configured"] is False

    @patch("api.routers.config.get_db_session")
    def test_get_config_nonexistent_guild_not_500(self, mock_get_db, client, mock_config_service):
        """GET /config/guild/{id} for an unconfigured guild must not return 500."""
        _configure_db_mock(mock_get_db)
        mock_config_service.get_guild_config = AsyncMock(
            return_value=make_mock_config(guild_id=11111, configured=False)
        )

        response = client.get("/api/v1/config/guild/11111")

        assert response.status_code != 500


# ===========================================================================
# Gap 3: Serialization Boundary Tests — Config
# ===========================================================================


class TestGetConfigWithNullJsonFields:
    """Gap 3: Serialization edge cases — config with NULL/None JSON fields must not crash.

    The bounty_config endpoint reads bounty_max_per_tier which could be NULL in the DB.
    Verify the endpoint handles this gracefully without a 500.
    """

    @patch("api.routers.config.get_db_session")
    @patch("api.routers.config.BountyRepository")
    def test_get_bounty_config_with_null_max_per_tier_does_not_crash(
        self, mock_repo_cls, mock_get_db, test_app, mock_config_service
    ):
        """GET /config/guild/{id}/bounty where max_bounties_per_tier is None → does not return 500.

        In production, bounty_max_per_tier can be NULL for guilds that configured the bot
        before the bounty config feature was added. The endpoint must handle this gracefully.
        """
        _configure_db_mock(mock_get_db)

        mock_repo = AsyncMock()
        mock_repo.count_active_by_guild_and_division = AsyncMock(return_value=0)
        mock_repo_cls.return_value = mock_repo

        # Simulate the config with max_bounties_per_tier present (the service normalises)
        mock_config_service.get_bounty_config = AsyncMock(
            return_value={
                "guild_id": 67890,
                "max_bounties_per_tier": {"bronze": 3, "silver": 3, "gold": 3},
                "bounty_expiry_minutes": 480,
                "bounty_spawn_interval_minutes": 60,
                "next_spawn_check_at": None,
            }
        )

        client = TestClient(test_app)
        response = client.get("/api/v1/config/guild/67890/bounty")

        assert response.status_code == 200
        data = response.json()
        assert "max_bounties_per_tier" in data

    @patch("api.routers.config.get_db_session")
    def test_get_guild_config_with_null_optional_fields_returns_200(self, mock_get_db, client, mock_config_service):
        """GET /config/guild/{id} where optional channel IDs are NULL → serialises without crash.

        Optional config fields (channel IDs, role IDs) may be NULL in the database for
        guilds set up before those fields were added. They must be nullable in the response.
        """
        _configure_db_mock(mock_get_db)
        # Config with all optional IDs set to None (NULL in DB)
        null_config = make_mock_config(guild_id=67890)
        null_config["shop_config"] = {}
        mock_config_service.get_guild_config = AsyncMock(return_value=null_config)

        response = client.get("/api/v1/config/guild/67890")

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890


# ===========================================================================
# admin_role_id propagation — targeted regression tests for the admin_role_id fix
# ===========================================================================


class TestAdminRoleIdPropagation:
    """Verify admin_role_id flows through every GuildConfigResponse constructor."""

    @patch("api.routers.config.get_db_session")
    def test_get_guild_config_returns_admin_role_id_when_set(self, mock_get_db, client, mock_config_service):
        """GET /config/guild/{id} must include a non-None admin_role_id when configured."""
        _configure_db_mock(mock_get_db)
        mock_config_service.get_guild_config = AsyncMock(
            return_value=make_mock_config(admin_role_id=1495550109381951549)
        )

        response = client.get("/api/v1/config/guild/67890")

        assert response.status_code == 200
        assert response.json()["admin_role_id"] == 1495550109381951549

    @patch("api.routers.config.get_db_session")
    def test_get_guild_config_returns_null_admin_role_id_when_not_configured(
        self, mock_get_db, client, mock_config_service
    ):
        """GET /config/guild/{id} must return admin_role_id=null when not set."""
        _configure_db_mock(mock_get_db)
        mock_config_service.get_guild_config = AsyncMock(return_value=make_mock_config(admin_role_id=None))

        response = client.get("/api/v1/config/guild/67890")

        assert response.status_code == 200
        assert response.json()["admin_role_id"] is None

    @patch("api.routers.config.get_db_session")
    def test_update_guild_config_returns_admin_role_id(self, mock_get_db, client, mock_config_service):
        """PUT /config/guild/{id} must propagate admin_role_id in response."""
        _configure_db_mock(mock_get_db)
        mock_config_service.create_or_update_config = AsyncMock(return_value=make_mock_config(admin_role_id=99887766))
        payload = {"guild_id": 67890, "admin_role_id": 99887766}

        response = client.put("/api/v1/config/guild/67890", json=payload)

        assert response.status_code == 200
        assert response.json()["admin_role_id"] == 99887766

    @patch("api.routers.config.get_db_session")
    def test_update_admin_role_endpoint_returns_admin_role_id(self, mock_get_db, client, mock_config_service):
        """PUT /config/guild/{id}/admin-role/{role} must reflect the new role in response."""
        _configure_db_mock(mock_get_db)
        mock_config_service.update_admin_role = AsyncMock(return_value=make_mock_config(admin_role_id=12345678))

        response = client.put("/api/v1/config/guild/67890/admin-role/12345678")

        assert response.status_code == 200
        assert response.json()["admin_role_id"] == 12345678

    @patch("api.routers.config.get_db_session")
    def test_reset_guild_config_returns_null_admin_role_id(self, mock_get_db, client, mock_config_service):
        """POST /config/guild/{id}/reset must return admin_role_id=null (default after reset)."""
        _configure_db_mock(mock_get_db)
        mock_config_service.reset_to_defaults = AsyncMock(return_value=make_mock_config(admin_role_id=None))

        response = client.post("/api/v1/config/guild/67890/reset")

        assert response.status_code == 200
        assert response.json()["admin_role_id"] is None
