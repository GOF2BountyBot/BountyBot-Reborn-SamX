"""Tests for the admin API router endpoints.

Import path setup and sqlalchemy_utils mocking are handled by
tests/api/conftest.py which runs before this module is loaded.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def make_mock_player(**overrides):
    defaults = dict(
        id=1,
        user_id=12345,
        guild_id=67890,
        credits=100,
        lifetime_credits=100,
        xp=0,
        tier="Bronze",
        prestige_count=0,
        systems_checked=0,
        bounty_wins=0,
        duel_wins=0,
        duel_losses=0,
        duel_credits_won=0,
        duel_credits_lost=0,
        active_ship_id=None,
    )
    defaults.update(overrides)
    player = MagicMock()
    for k, v in defaults.items():
        setattr(player, k, v)
    return player


@pytest.fixture
def mock_player_service():
    service = AsyncMock()
    service.player_repo = AsyncMock()
    service.player_repo.get_by_id = AsyncMock(return_value=make_mock_player())
    service.player_repo.get_players_by_guild = AsyncMock(
        return_value=[
            make_mock_player(id=1, credits=100, xp=50, tier="Bronze"),
            make_mock_player(id=2, credits=200, xp=150, tier="Silver"),
        ]
    )
    service.update_player_credits = AsyncMock(return_value=make_mock_player(credits=500, lifetime_credits=500))
    service.update_player_xp = AsyncMock(return_value=make_mock_player(xp=100, tier="Bronze"))
    return service


@pytest.fixture
def mock_shop_service():
    service = AsyncMock()
    service.refresh_shop = AsyncMock(return_value={"refreshed": True, "items_count": 10})
    return service


@pytest.fixture
def mock_config_service():
    service = AsyncMock()
    service.create_or_update_config = AsyncMock()
    service.clear_guild_players = AsyncMock()
    service.reset_to_defaults = AsyncMock()
    service.uninstall_guild = AsyncMock(return_value={"players": 5, "configs": 1, "shops": 40})
    service.update_shop_config = AsyncMock(return_value={"sale_price_factor": 0.5})
    return service


@pytest.fixture
def test_app(mock_player_service, mock_shop_service, mock_config_service):
    app = FastAPI()
    from api.routers.admin import get_config_service, get_player_service, get_shop_service
    from api.routers.admin import router as admin_router

    app.include_router(admin_router, prefix="/api/v1")
    app.dependency_overrides[get_player_service] = lambda: mock_player_service
    app.dependency_overrides[get_shop_service] = lambda: mock_shop_service
    app.dependency_overrides[get_config_service] = lambda: mock_config_service
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


# ---------------------------------------------------------------------------
# Helper: build a configured mock get_db_session patcher result
# ---------------------------------------------------------------------------


def _configure_db_mock(mock_get_db):
    """Configure mock_get_db to act as an async context manager."""
    mock_session = AsyncMock()
    mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_session


# ===========================================================================
# 1. POST /admin/guilds/initialize
# ===========================================================================


class TestInitializeGuild:
    """Tests for POST /api/v1/admin/guilds/initialize."""

    @patch("api.routers.admin.get_db_session")
    def test_initialize_guild_happy_path(self, mock_get_db, client, mock_config_service, mock_shop_service):
        """Returns 200 with GuildInitializationResponse on success."""
        _configure_db_mock(mock_get_db)
        payload = {"guild_id": 67890, "admin_role_id": 11111, "starting_credits": 500}

        response = client.post("/api/v1/admin/guilds/initialize", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890
        assert data["admin_role_id"] == 11111
        assert data["shops_created"] == 4
        assert data["config_created"] is True
        assert "67890" in data["message"]

    @patch("api.routers.admin.get_db_session")
    def test_initialize_guild_default_values(self, mock_get_db, client, mock_config_service, mock_shop_service):
        """Returns 200 when optional fields use defaults."""
        _configure_db_mock(mock_get_db)
        payload = {"guild_id": 12345}

        response = client.post("/api/v1/admin/guilds/initialize", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 12345
        assert data["admin_role_id"] is None
        assert data["shops_created"] == 4
        assert data["config_created"] is True

    @patch("api.routers.admin.get_db_session")
    def test_initialize_guild_calls_config_and_shop_services(
        self, mock_get_db, client, mock_config_service, mock_shop_service
    ):
        """Calls create_or_update_config and refresh_shop for all 4 tiers."""
        _configure_db_mock(mock_get_db)
        payload = {"guild_id": 67890}

        client.post("/api/v1/admin/guilds/initialize", json=payload)

        mock_config_service.create_or_update_config.assert_awaited_once()
        assert mock_shop_service.refresh_shop.await_count == 4
        tiers_called = [call.args[2] for call in mock_shop_service.refresh_shop.call_args_list]
        assert set(tiers_called) == {"Bronze", "Silver", "Gold", "Platinum"}

    @patch("api.routers.admin.get_db_session")
    def test_initialize_guild_server_error_returns_500(self, mock_get_db, client, mock_config_service):
        """Returns 500 when config_service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_config_service.create_or_update_config.side_effect = RuntimeError("DB failure")
        payload = {"guild_id": 67890}

        response = client.post("/api/v1/admin/guilds/initialize", json=payload)

        assert response.status_code == 500
        assert "Failed to initialize guild" in response.json()["detail"]

    def test_initialize_guild_missing_guild_id_returns_422(self, client):
        """Returns 422 when required field guild_id is missing."""
        payload = {"admin_role_id": 11111}

        response = client.post("/api/v1/admin/guilds/initialize", json=payload)

        assert response.status_code == 422

    def test_initialize_guild_negative_starting_credits_returns_422(self, client):
        """Returns 422 when starting_credits is negative (ge=0)."""
        payload = {"guild_id": 67890, "starting_credits": -100}

        response = client.post("/api/v1/admin/guilds/initialize", json=payload)

        assert response.status_code == 422


# ===========================================================================
# 2. POST /admin/guilds/{guild_id}/reset
# ===========================================================================


class TestResetGuild:
    """Tests for POST /api/v1/admin/guilds/{guild_id}/reset."""

    @patch("api.routers.admin.get_db_session")
    def test_reset_guild_preserve_players_true(self, mock_get_db, client, mock_config_service, mock_shop_service):
        """Does not clear player data when preserve_players=true."""
        _configure_db_mock(mock_get_db)

        response = client.post("/api/v1/admin/guilds/67890/reset?preserve_players=true")

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890
        assert data["players_preserved"] is True
        assert data["shops_refreshed"] == 4
        assert "67890" in data["message"]
        mock_config_service.clear_guild_players.assert_not_awaited()
        mock_config_service.reset_to_defaults.assert_awaited_once()

    @patch("api.routers.admin.get_db_session")
    def test_reset_guild_preserve_players_false(self, mock_get_db, client, mock_config_service, mock_shop_service):
        """Calls clear_guild_players when preserve_players=false."""
        _configure_db_mock(mock_get_db)

        response = client.post("/api/v1/admin/guilds/67890/reset?preserve_players=false")

        assert response.status_code == 200
        data = response.json()
        assert data["players_preserved"] is False
        mock_config_service.clear_guild_players.assert_awaited_once()
        mock_config_service.reset_to_defaults.assert_awaited_once()

    @patch("api.routers.admin.get_db_session")
    def test_reset_guild_default_preserve_players(self, mock_get_db, client, mock_config_service, mock_shop_service):
        """Defaults preserve_players to True when not provided."""
        _configure_db_mock(mock_get_db)

        response = client.post("/api/v1/admin/guilds/67890/reset")

        assert response.status_code == 200
        data = response.json()
        assert data["players_preserved"] is True
        mock_config_service.clear_guild_players.assert_not_awaited()

    @patch("api.routers.admin.get_db_session")
    def test_reset_guild_refreshes_all_4_tiers(self, mock_get_db, client, mock_config_service, mock_shop_service):
        """Calls refresh_shop for all 4 tiers."""
        _configure_db_mock(mock_get_db)

        client.post("/api/v1/admin/guilds/67890/reset?preserve_players=true")

        assert mock_shop_service.refresh_shop.await_count == 4
        tiers_called = [call.args[2] for call in mock_shop_service.refresh_shop.call_args_list]
        assert set(tiers_called) == {"Bronze", "Silver", "Gold", "Platinum"}

    @patch("api.routers.admin.get_db_session")
    def test_reset_guild_server_error_returns_500(self, mock_get_db, client, mock_config_service):
        """Returns 500 when service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_config_service.reset_to_defaults.side_effect = RuntimeError("Reset failed")

        response = client.post("/api/v1/admin/guilds/67890/reset")

        assert response.status_code == 500
        assert "Failed to reset guild" in response.json()["detail"]


# ===========================================================================
# 3. DELETE /admin/guilds/{guild_id}/uninstall
# ===========================================================================


class TestUninstallBot:
    """Tests for DELETE /api/v1/admin/guilds/{guild_id}/uninstall."""

    @patch("api.routers.admin.get_db_session")
    def test_uninstall_bot_happy_path(self, mock_get_db, client, mock_config_service):
        """Returns 200 with removed counts on successful uninstall."""
        _configure_db_mock(mock_get_db)

        response = client.delete("/api/v1/admin/guilds/67890/uninstall")

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890
        assert data["removed_counts"] == {"players": 5, "configs": 1, "shops": 40}
        assert "67890" in data["message"]
        assert "warning" in data
        mock_config_service.uninstall_guild.assert_awaited_once()

    @patch("api.routers.admin.get_db_session")
    def test_uninstall_bot_calls_uninstall_guild_with_correct_guild_id(self, mock_get_db, client, mock_config_service):
        """Passes the correct guild_id to config_service.uninstall_guild."""
        _configure_db_mock(mock_get_db)

        client.delete("/api/v1/admin/guilds/99999/uninstall")

        call_args = mock_config_service.uninstall_guild.call_args
        assert 99999 in call_args.args or call_args.kwargs.get("guild_id") == 99999

    @patch("api.routers.admin.get_db_session")
    def test_uninstall_bot_server_error_returns_500(self, mock_get_db, client, mock_config_service):
        """Returns 500 when config_service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_config_service.uninstall_guild.side_effect = RuntimeError("Uninstall failed")

        response = client.delete("/api/v1/admin/guilds/67890/uninstall")

        assert response.status_code == 500
        assert "Failed to uninstall bot" in response.json()["detail"]


# ===========================================================================
# 4. PUT /admin/players/credits
# ===========================================================================


class TestUpdatePlayerCredits:
    """Tests for PUT /api/v1/admin/players/credits."""

    @patch("api.routers.admin.get_db_session")
    def test_update_player_credits_happy_path(self, mock_get_db, client, mock_player_service):
        """Returns 200 with updated credit information."""
        _configure_db_mock(mock_get_db)
        payload = {"player_id": 1, "credits": 500, "update_lifetime": True}

        response = client.put("/api/v1/admin/players/credits", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["player_id"] == 1
        assert data["new_credits"] == 500
        assert "message" in data

    @patch("api.routers.admin.get_db_session")
    def test_update_player_credits_calls_service_with_correct_args(self, mock_get_db, client, mock_player_service):
        """Passes correct arguments to player_service.update_player_credits."""
        _configure_db_mock(mock_get_db)
        payload = {"player_id": 42, "credits": 1000, "update_lifetime": False}

        client.put("/api/v1/admin/players/credits", json=payload)

        mock_player_service.update_player_credits.assert_awaited_once()
        call_args = mock_player_service.update_player_credits.call_args
        assert 42 in call_args.args or call_args.kwargs.get("player_id") == 42
        assert 1000 in call_args.args or call_args.kwargs.get("credits") == 1000

    @patch("api.routers.admin.get_db_session")
    def test_update_player_credits_value_error_returns_404(self, mock_get_db, client, mock_player_service):
        """Returns 404 when player_service raises ValueError."""
        _configure_db_mock(mock_get_db)
        mock_player_service.update_player_credits.side_effect = ValueError("Player not found")
        payload = {"player_id": 9999, "credits": 100}

        response = client.put("/api/v1/admin/players/credits", json=payload)

        assert response.status_code == 404
        assert "Player not found" in response.json()["detail"]

    @patch("api.routers.admin.get_db_session")
    def test_update_player_credits_server_error_returns_500(self, mock_get_db, client, mock_player_service):
        """Returns 500 when service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_player_service.update_player_credits.side_effect = RuntimeError("DB failure")
        payload = {"player_id": 1, "credits": 100}

        response = client.put("/api/v1/admin/players/credits", json=payload)

        assert response.status_code == 500
        assert "Failed to update credits" in response.json()["detail"]

    def test_update_player_credits_negative_credits_returns_422(self, client):
        """Returns 422 when credits is negative (ge=0)."""
        payload = {"player_id": 1, "credits": -50}

        response = client.put("/api/v1/admin/players/credits", json=payload)

        assert response.status_code == 422

    def test_update_player_credits_missing_player_id_returns_422(self, client):
        """Returns 422 when player_id is missing."""
        payload = {"credits": 100}

        response = client.put("/api/v1/admin/players/credits", json=payload)

        assert response.status_code == 422

    def test_update_player_credits_missing_credits_returns_422(self, client):
        """Returns 422 when credits field is missing."""
        payload = {"player_id": 1}

        response = client.put("/api/v1/admin/players/credits", json=payload)

        assert response.status_code == 422

    @patch("api.routers.admin.get_db_session")
    def test_update_player_credits_default_update_lifetime_true(self, mock_get_db, client, mock_player_service):
        """Defaults update_lifetime to True when not provided."""
        _configure_db_mock(mock_get_db)
        payload = {"player_id": 1, "credits": 200}

        response = client.put("/api/v1/admin/players/credits", json=payload)

        assert response.status_code == 200
        call_args = mock_player_service.update_player_credits.call_args
        # update_lifetime=True should be passed
        assert True in call_args.args or call_args.kwargs.get("update_lifetime") is True


# ===========================================================================
# 5. PUT /admin/players/xp
# ===========================================================================


class TestUpdatePlayerXP:
    """Tests for PUT /api/v1/admin/players/xp."""

    @patch("api.routers.admin.get_db_session")
    def test_update_player_xp_happy_path(self, mock_get_db, client, mock_player_service):
        """Returns 200 with updated XP and tier information."""
        _configure_db_mock(mock_get_db)
        mock_player_service.player_repo.get_by_id = AsyncMock(return_value=make_mock_player(xp=50, tier="Bronze"))
        mock_player_service.update_player_xp = AsyncMock(return_value=make_mock_player(xp=100, tier="Bronze"))
        payload = {"player_id": 1, "xp": 100}

        response = client.put("/api/v1/admin/players/xp", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["player_id"] == 1
        assert data["old_xp"] == 50
        assert data["new_xp"] == 100
        assert data["old_tier"] == "Bronze"
        assert data["new_tier"] == "Bronze"
        assert data["tier_changed"] is False
        assert "message" in data

    @patch("api.routers.admin.get_db_session")
    def test_update_player_xp_tier_change(self, mock_get_db, client, mock_player_service):
        """Returns tier_changed=True when tier advances."""
        _configure_db_mock(mock_get_db)
        mock_player_service.player_repo.get_by_id = AsyncMock(return_value=make_mock_player(xp=100, tier="Bronze"))
        mock_player_service.update_player_xp = AsyncMock(return_value=make_mock_player(xp=5000, tier="Silver"))
        payload = {"player_id": 1, "xp": 5000}

        response = client.put("/api/v1/admin/players/xp", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["old_tier"] == "Bronze"
        assert data["new_tier"] == "Silver"
        assert data["tier_changed"] is True

    @patch("api.routers.admin.get_db_session")
    def test_update_player_xp_player_not_found_returns_500(self, mock_get_db, client, mock_player_service):
        """Returns 500 when player does not exist.

        Note: The router raises HTTPException(404) inside the try block, but
        the broad `except Exception` handler catches it and wraps it as 500.
        This reflects the actual router behaviour.
        """
        _configure_db_mock(mock_get_db)
        mock_player_service.player_repo.get_by_id = AsyncMock(return_value=None)
        payload = {"player_id": 9999, "xp": 100}

        response = client.put("/api/v1/admin/players/xp", json=payload)

        assert response.status_code == 500
        assert "Failed to update XP" in response.json()["detail"]

    @patch("api.routers.admin.get_db_session")
    def test_update_player_xp_value_error_returns_404(self, mock_get_db, client, mock_player_service):
        """Returns 404 when update_player_xp raises ValueError."""
        _configure_db_mock(mock_get_db)
        mock_player_service.player_repo.get_by_id = AsyncMock(return_value=make_mock_player())
        mock_player_service.update_player_xp.side_effect = ValueError("Invalid XP value")
        payload = {"player_id": 1, "xp": 100}

        response = client.put("/api/v1/admin/players/xp", json=payload)

        assert response.status_code == 404
        assert "Invalid XP value" in response.json()["detail"]

    @patch("api.routers.admin.get_db_session")
    def test_update_player_xp_server_error_returns_500(self, mock_get_db, client, mock_player_service):
        """Returns 500 when update_player_xp raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_player_service.player_repo.get_by_id = AsyncMock(return_value=make_mock_player())
        mock_player_service.update_player_xp.side_effect = RuntimeError("Unexpected failure")
        payload = {"player_id": 1, "xp": 100}

        response = client.put("/api/v1/admin/players/xp", json=payload)

        assert response.status_code == 500
        assert "Failed to update XP" in response.json()["detail"]

    def test_update_player_xp_negative_xp_returns_422(self, client):
        """Returns 422 when xp is negative (ge=0)."""
        payload = {"player_id": 1, "xp": -10}

        response = client.put("/api/v1/admin/players/xp", json=payload)

        assert response.status_code == 422

    def test_update_player_xp_exceeds_max_returns_422(self, client):
        """Returns 422 when xp exceeds 1000000 (le=1000000)."""
        payload = {"player_id": 1, "xp": 1000001}

        response = client.put("/api/v1/admin/players/xp", json=payload)

        assert response.status_code == 422

    def test_update_player_xp_missing_player_id_returns_422(self, client):
        """Returns 422 when player_id is missing."""
        payload = {"xp": 100}

        response = client.put("/api/v1/admin/players/xp", json=payload)

        assert response.status_code == 422


# ===========================================================================
# 6. POST /admin/players/inventory/add
# ===========================================================================


class TestAddInventoryItem:
    """Tests for POST /api/v1/admin/players/inventory/add."""

    @patch("api.routers.admin.get_db_session")
    def test_add_inventory_item_happy_path(self, mock_get_db, client, mock_player_service):
        """Returns 200 with item details when player exists."""
        _configure_db_mock(mock_get_db)
        payload = {
            "player_id": 1,
            "item_type": "weapon",
            "item_name": "Pulse Laser",
            "quantity": 2,
        }

        response = client.post("/api/v1/admin/players/inventory/add", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["player_id"] == 1
        assert data["item_type"] == "weapon"
        assert data["item_name"] == "Pulse Laser"
        assert data["quantity"] == 2
        assert "message" in data

    @patch("api.routers.admin.get_db_session")
    def test_add_inventory_item_default_quantity_one(self, mock_get_db, client, mock_player_service):
        """Defaults quantity to 1 when not provided."""
        _configure_db_mock(mock_get_db)
        payload = {"player_id": 1, "item_type": "ship", "item_name": "Raptor"}

        response = client.post("/api/v1/admin/players/inventory/add", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["quantity"] == 1

    @patch("api.routers.admin.get_db_session")
    def test_add_inventory_item_all_valid_item_types(self, mock_get_db, client, mock_player_service):
        """Accepts all valid item types: ship, weapon, module, turret."""
        _configure_db_mock(mock_get_db)
        valid_types = ["ship", "weapon", "module", "turret"]

        for item_type in valid_types:
            mock_player_service.player_repo.get_by_id = AsyncMock(return_value=make_mock_player())
            payload = {"player_id": 1, "item_type": item_type, "item_name": "Test Item"}
            response = client.post("/api/v1/admin/players/inventory/add", json=payload)
            assert response.status_code == 200, f"Expected 200 for item_type={item_type}"

    @patch("api.routers.admin.get_db_session")
    def test_add_inventory_item_player_not_found_returns_404(self, mock_get_db, client, mock_player_service):
        """Returns 404 when player does not exist (repo returns None)."""
        _configure_db_mock(mock_get_db)
        mock_player_service.player_repo.get_by_id = AsyncMock(return_value=None)
        payload = {"player_id": 9999, "item_type": "weapon", "item_name": "Pulse Laser"}

        response = client.post("/api/v1/admin/players/inventory/add", json=payload)

        assert response.status_code == 404
        assert "Player not found" in response.json()["detail"]

    @patch("api.routers.admin.get_db_session")
    def test_add_inventory_item_server_error_returns_500(self, mock_get_db, client, mock_player_service):
        """Returns 500 when an unexpected exception is raised."""
        _configure_db_mock(mock_get_db)
        mock_player_service.player_repo.get_by_id = AsyncMock(side_effect=RuntimeError("DB connection lost"))
        payload = {"player_id": 1, "item_type": "weapon", "item_name": "Pulse Laser"}

        response = client.post("/api/v1/admin/players/inventory/add", json=payload)

        assert response.status_code == 500
        assert "Failed to add inventory item" in response.json()["detail"]

    def test_add_inventory_item_invalid_item_type_returns_422(self, client):
        """Returns 422 when item_type is not in allowed pattern."""
        payload = {
            "player_id": 1,
            "item_type": "spaceship",  # not in pattern
            "item_name": "X-Wing",
        }

        response = client.post("/api/v1/admin/players/inventory/add", json=payload)

        assert response.status_code == 422

    def test_add_inventory_item_quantity_zero_returns_422(self, client):
        """Returns 422 when quantity is 0 (gt=0)."""
        payload = {"player_id": 1, "item_type": "weapon", "item_name": "Laser", "quantity": 0}

        response = client.post("/api/v1/admin/players/inventory/add", json=payload)

        assert response.status_code == 422

    def test_add_inventory_item_negative_quantity_returns_422(self, client):
        """Returns 422 when quantity is negative (gt=0)."""
        payload = {"player_id": 1, "item_type": "weapon", "item_name": "Laser", "quantity": -1}

        response = client.post("/api/v1/admin/players/inventory/add", json=payload)

        assert response.status_code == 422

    def test_add_inventory_item_missing_required_fields_returns_422(self, client):
        """Returns 422 when required fields are missing."""
        payload = {"player_id": 1}  # missing item_type and item_name

        response = client.post("/api/v1/admin/players/inventory/add", json=payload)

        assert response.status_code == 422


# ===========================================================================
# 7. POST /admin/shops/refresh
# ===========================================================================


class TestRefreshShop:
    """Tests for POST /api/v1/admin/shops/refresh."""

    @patch("api.routers.admin.get_db_session")
    def test_refresh_shop_happy_path(self, mock_get_db, client, mock_shop_service):
        """Returns 200 with refresh details and message."""
        _configure_db_mock(mock_get_db)
        payload = {"guild_id": 67890, "tier": "Bronze"}

        response = client.post("/api/v1/admin/shops/refresh", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["refreshed"] is True
        assert data["items_count"] == 10
        assert "message" in data
        assert "Bronze" in data["message"]
        assert "67890" in data["message"]

    @patch("api.routers.admin.get_db_session")
    def test_refresh_shop_with_force_tech_level(self, mock_get_db, client, mock_shop_service):
        """Accepts optional force_tech_level and passes it to service."""
        _configure_db_mock(mock_get_db)
        payload = {"guild_id": 67890, "tier": "Gold", "force_tech_level": 7}

        response = client.post("/api/v1/admin/shops/refresh", json=payload)

        assert response.status_code == 200
        mock_shop_service.refresh_shop.assert_awaited_once()
        call_args = mock_shop_service.refresh_shop.call_args
        assert 7 in call_args.args or call_args.kwargs.get("force_tech_level") == 7

    @patch("api.routers.admin.get_db_session")
    def test_refresh_shop_all_valid_tiers(self, mock_get_db, client, mock_shop_service):
        """Accepts all valid tiers: Bronze, Silver, Gold, Platinum."""
        _configure_db_mock(mock_get_db)
        valid_tiers = ["Bronze", "Silver", "Gold", "Platinum"]

        for tier in valid_tiers:
            mock_shop_service.refresh_shop.reset_mock()
            mock_shop_service.refresh_shop = AsyncMock(return_value={"refreshed": True, "items_count": 5})
            payload = {"guild_id": 67890, "tier": tier}
            response = client.post("/api/v1/admin/shops/refresh", json=payload)
            assert response.status_code == 200, f"Expected 200 for tier={tier}"

    @patch("api.routers.admin.get_db_session")
    def test_refresh_shop_value_error_returns_400(self, mock_get_db, client, mock_shop_service):
        """Returns 400 when shop_service raises ValueError."""
        _configure_db_mock(mock_get_db)
        mock_shop_service.refresh_shop.side_effect = ValueError("Shop already refreshed recently")
        payload = {"guild_id": 67890, "tier": "Bronze"}

        response = client.post("/api/v1/admin/shops/refresh", json=payload)

        assert response.status_code == 400
        assert "Shop already refreshed recently" in response.json()["detail"]

    @patch("api.routers.admin.get_db_session")
    def test_refresh_shop_server_error_returns_500(self, mock_get_db, client, mock_shop_service):
        """Returns 500 when shop_service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_shop_service.refresh_shop.side_effect = RuntimeError("Refresh service crashed")
        payload = {"guild_id": 67890, "tier": "Silver"}

        response = client.post("/api/v1/admin/shops/refresh", json=payload)

        assert response.status_code == 500
        assert "Failed to refresh shop" in response.json()["detail"]

    def test_refresh_shop_invalid_tier_returns_422(self, client):
        """Returns 422 when tier is not in allowed pattern."""
        payload = {"guild_id": 67890, "tier": "Diamond"}

        response = client.post("/api/v1/admin/shops/refresh", json=payload)

        assert response.status_code == 422

    def test_refresh_shop_force_tech_level_out_of_range_returns_422(self, client):
        """Returns 422 when force_tech_level is outside 1-9."""
        payload = {"guild_id": 67890, "tier": "Bronze", "force_tech_level": 10}

        response = client.post("/api/v1/admin/shops/refresh", json=payload)

        assert response.status_code == 422

    def test_refresh_shop_force_tech_level_zero_returns_422(self, client):
        """Returns 422 when force_tech_level is 0 (ge=1)."""
        payload = {"guild_id": 67890, "tier": "Bronze", "force_tech_level": 0}

        response = client.post("/api/v1/admin/shops/refresh", json=payload)

        assert response.status_code == 422


# ===========================================================================
# 8. PUT /admin/shops/config
# ===========================================================================


class TestUpdateShopConfig:
    """Tests for PUT /api/v1/admin/shops/config."""

    @patch("api.routers.admin.get_db_session")
    def test_update_shop_config_happy_path(self, mock_get_db, client, mock_config_service):
        """Returns 200 with updated config when all fields are valid."""
        _configure_db_mock(mock_get_db)
        payload = {
            "guild_id": 67890,
            "sale_price_factor": 0.5,
        }

        response = client.put("/api/v1/admin/shops/config", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890
        assert "updated_config" in data
        assert "message" in data
        assert "67890" in data["message"]

    @patch("api.routers.admin.get_db_session")
    def test_update_shop_config_minimal_payload(self, mock_get_db, client, mock_config_service):
        """Returns 200 with only guild_id when optional fields are omitted."""
        _configure_db_mock(mock_get_db)
        payload = {"guild_id": 67890}

        response = client.put("/api/v1/admin/shops/config", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890

    @patch("api.routers.admin.get_db_session")
    def test_update_shop_config_with_all_fields(self, mock_get_db, client, mock_config_service):
        """Returns 200 when all optional config fields are provided."""
        _configure_db_mock(mock_get_db)
        payload = {
            "guild_id": 67890,
            "tech_level_probabilities": {"1": 0.5, "2": 0.3, "3": 0.2},
            "sale_price_factor": 0.8,
            "item_count_ranges": {"Bronze": {"min": 5, "max": 10}},
            "quantity_ranges": {"Bronze": {"min": 1, "max": 3}},
        }

        response = client.put("/api/v1/admin/shops/config", json=payload)

        assert response.status_code == 200
        mock_config_service.update_shop_config.assert_awaited_once()

    @patch("api.routers.admin.get_db_session")
    def test_update_shop_config_calls_service_with_request_data(self, mock_get_db, client, mock_config_service):
        """Passes request data to config_service.update_shop_config."""
        _configure_db_mock(mock_get_db)
        payload = {"guild_id": 67890, "sale_price_factor": 0.7}

        client.put("/api/v1/admin/shops/config", json=payload)

        mock_config_service.update_shop_config.assert_awaited_once()

    @patch("api.routers.admin.get_db_session")
    def test_update_shop_config_server_error_returns_500(self, mock_get_db, client, mock_config_service):
        """Returns 500 when config_service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_config_service.update_shop_config.side_effect = RuntimeError("Config update failed")
        payload = {"guild_id": 67890, "sale_price_factor": 0.5}

        response = client.put("/api/v1/admin/shops/config", json=payload)

        assert response.status_code == 500
        assert "Failed to update shop configuration" in response.json()["detail"]

    def test_update_shop_config_sale_price_factor_exceeds_one_returns_422(self, client):
        """Returns 422 when sale_price_factor > 1.0 (le=1)."""
        payload = {"guild_id": 67890, "sale_price_factor": 1.5}

        response = client.put("/api/v1/admin/shops/config", json=payload)

        assert response.status_code == 422

    def test_update_shop_config_sale_price_factor_zero_returns_422(self, client):
        """Returns 422 when sale_price_factor is 0 (gt=0)."""
        payload = {"guild_id": 67890, "sale_price_factor": 0.0}

        response = client.put("/api/v1/admin/shops/config", json=payload)

        assert response.status_code == 422

    def test_update_shop_config_missing_guild_id_returns_422(self, client):
        """Returns 422 when guild_id is missing."""
        payload = {"sale_price_factor": 0.5}

        response = client.put("/api/v1/admin/shops/config", json=payload)

        assert response.status_code == 422


# ===========================================================================
# 9. GET /admin/system/health
# ===========================================================================


class TestGetSystemHealth:
    """Tests for GET /api/v1/admin/system/health."""

    @patch("api.routers.admin.get_db_session")
    def test_get_system_health_happy_path(self, mock_get_db, client):
        """Returns 200 with SystemHealthResponse."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/admin/system/health")

        assert response.status_code == 200
        data = response.json()
        assert data["database_status"] == "healthy"
        assert isinstance(data["total_users"], int)
        assert isinstance(data["total_players"], int)
        assert isinstance(data["total_guilds"], int)
        assert isinstance(data["shop_items_count"], int)
        assert data["system_status"] == "operational"

    @patch("api.routers.admin.get_db_session")
    def test_get_system_health_response_model_fields(self, mock_get_db, client):
        """Verifies all required SystemHealthResponse fields are present."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/admin/system/health")

        assert response.status_code == 200
        data = response.json()
        required_fields = [
            "database_status",
            "total_users",
            "total_players",
            "total_guilds",
            "shop_items_count",
            "system_status",
        ]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"

    @patch("api.routers.admin.get_db_session")
    def test_get_system_health_server_error_returns_500(self, mock_get_db, client):
        """Returns 500 when get_db_session raises an unexpected exception."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(side_effect=RuntimeError("DB unavailable"))
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/api/v1/admin/system/health")

        assert response.status_code == 500
        assert "Failed to get system health" in response.json()["detail"]


# ===========================================================================
# 10. GET /admin/guilds/{guild_id}/stats
# ===========================================================================


class TestGetGuildStatistics:
    """Tests for GET /api/v1/admin/guilds/{guild_id}/stats."""

    @patch("api.routers.admin.get_db_session")
    def test_get_guild_statistics_happy_path(self, mock_get_db, client, mock_player_service):
        """Returns 200 with correct statistics for a guild with players."""
        _configure_db_mock(mock_get_db)
        # 2 players: Bronze with 100 credits/50 xp, Silver with 200 credits/150 xp
        mock_player_service.player_repo.get_players_by_guild = AsyncMock(
            return_value=[
                make_mock_player(id=1, credits=100, xp=50, tier="Bronze"),
                make_mock_player(id=2, credits=200, xp=150, tier="Silver"),
            ]
        )

        response = client.get("/api/v1/admin/guilds/67890/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890
        assert data["total_players"] == 2
        assert data["total_credits"] == 300
        assert data["total_xp"] == 200
        assert data["average_credits"] == 150.0
        assert data["average_xp"] == 100.0
        assert data["tier_distribution"]["Bronze"] == 1
        assert data["tier_distribution"]["Silver"] == 1

    @patch("api.routers.admin.get_db_session")
    def test_get_guild_statistics_multiple_players_same_tier(self, mock_get_db, client, mock_player_service):
        """Correctly counts tier distribution when multiple players share a tier."""
        _configure_db_mock(mock_get_db)
        mock_player_service.player_repo.get_players_by_guild = AsyncMock(
            return_value=[
                make_mock_player(id=1, credits=100, xp=50, tier="Bronze"),
                make_mock_player(id=2, credits=150, xp=75, tier="Bronze"),
                make_mock_player(id=3, credits=500, xp=500, tier="Silver"),
            ]
        )

        response = client.get("/api/v1/admin/guilds/67890/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["total_players"] == 3
        assert data["tier_distribution"]["Bronze"] == 2
        assert data["tier_distribution"]["Silver"] == 1

    @patch("api.routers.admin.get_db_session")
    def test_get_guild_statistics_empty_guild(self, mock_get_db, client, mock_player_service):
        """Returns zero averages and empty tier_distribution for guild with no players."""
        _configure_db_mock(mock_get_db)
        mock_player_service.player_repo.get_players_by_guild = AsyncMock(return_value=[])

        response = client.get("/api/v1/admin/guilds/67890/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890
        assert data["total_players"] == 0
        assert data["total_credits"] == 0
        assert data["total_xp"] == 0
        assert data["average_credits"] == 0
        assert data["average_xp"] == 0
        assert data["tier_distribution"] == {}

    @patch("api.routers.admin.get_db_session")
    def test_get_guild_statistics_calls_repo_with_correct_guild_id(self, mock_get_db, client, mock_player_service):
        """Passes the correct guild_id to player_repo.get_players_by_guild."""
        _configure_db_mock(mock_get_db)

        client.get("/api/v1/admin/guilds/99999/stats")

        mock_player_service.player_repo.get_players_by_guild.assert_awaited_once()
        call_args = mock_player_service.player_repo.get_players_by_guild.call_args
        assert 99999 in call_args.args or call_args.kwargs.get("guild_id") == 99999

    @patch("api.routers.admin.get_db_session")
    def test_get_guild_statistics_server_error_returns_500(self, mock_get_db, client, mock_player_service):
        """Returns 500 when player_repo raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_player_service.player_repo.get_players_by_guild = AsyncMock(side_effect=RuntimeError("Query timeout"))

        response = client.get("/api/v1/admin/guilds/67890/stats")

        assert response.status_code == 500
        assert "Failed to get guild statistics" in response.json()["detail"]

    @patch("api.routers.admin.get_db_session")
    def test_get_guild_statistics_correct_average_calculation(self, mock_get_db, client, mock_player_service):
        """Calculates average_credits and average_xp correctly."""
        _configure_db_mock(mock_get_db)
        mock_player_service.player_repo.get_players_by_guild = AsyncMock(
            return_value=[
                make_mock_player(id=1, credits=100, xp=0, tier="Bronze"),
                make_mock_player(id=2, credits=300, xp=200, tier="Bronze"),
                make_mock_player(id=3, credits=200, xp=100, tier="Bronze"),
            ]
        )

        response = client.get("/api/v1/admin/guilds/67890/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["average_credits"] == pytest.approx(200.0)
        assert data["average_xp"] == pytest.approx(100.0)
