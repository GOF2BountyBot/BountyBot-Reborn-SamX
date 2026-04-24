"""Tests for new admin inventory management endpoints:
- POST /admin/give-item
- POST /admin/remove-item
- POST /admin/give-ship
- POST /admin/remove-ship

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
        id=10,
        user_id=1,
        guild_id=67890,
        credits=1000,
        lifetime_credits=1000,
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


def make_mock_user(**overrides):
    defaults = dict(id=1, discord_id=111222333, username="TestUser")
    defaults.update(overrides)
    user = MagicMock()
    for k, v in defaults.items():
        setattr(user, k, v)
    return user


def make_mock_player_ship(**overrides):
    defaults = dict(
        id=42,
        player_id=10,
        ship_name="Sidewinder",
        nickname=None,
        is_active=False,
        weapons=["Pulse Laser"],
        modules=["Shield Gen"],
        turrets=[],
        secondary_weapons=[],
        created_at=datetime(2026, 1, 1),
    )
    defaults.update(overrides)
    ship = MagicMock()
    for k, v in defaults.items():
        setattr(ship, k, v)
    return ship


def make_mock_game_ship(**overrides):
    """A game ship from ShipRepository (not PlayerShip)."""
    defaults = dict(id=99, name="Sidewinder")
    defaults.update(overrides)
    ship = MagicMock()
    for k, v in defaults.items():
        setattr(ship, k, v)
    return ship


def _configure_db_mock(mock_get_db):
    mock_session = AsyncMock()
    mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_session


@pytest.fixture
def mock_inventory_service():
    service = AsyncMock()
    service.add_item_to_inventory = AsyncMock(
        return_value={
            "player_id": 10,
            "item_type": "weapon",
            "item_name": "Pulse Laser",
            "quantity_added": 1,
            "new_total_quantity": 2,
            "transaction_time": "2026-01-01T00:00:00",
        }
    )
    service.remove_item_from_inventory = AsyncMock(
        return_value={
            "player_id": 10,
            "item_type": "weapon",
            "item_name": "Pulse Laser",
            "quantity_removed": 1,
            "old_quantity": 2,
            "new_quantity": 1,
            "item_completely_removed": False,
        }
    )
    return service


@pytest.fixture
def test_app(mock_inventory_service):
    app = FastAPI()
    from api.routers.admin import get_inventory_service
    from api.routers.admin import router as admin_router

    app.include_router(admin_router, prefix="/api/v1")
    app.dependency_overrides[get_inventory_service] = lambda: mock_inventory_service
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


# ===========================================================================
# POST /admin/give-item
# ===========================================================================


class TestAdminGiveItem:
    """Tests for POST /api/v1/admin/give-item."""

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.PlayerRepository")
    @patch("persist.repositories.user_repository.UserRepository")
    def test_give_item_success(
        self, mock_user_repo_cls, mock_player_repo_cls, mock_get_db, client, mock_inventory_service
    ):
        """Returns 200 with transaction details when item is given successfully."""
        _configure_db_mock(mock_get_db)

        mock_user_repo = AsyncMock()
        mock_user_repo.get_by_discord_id = AsyncMock(return_value=make_mock_user())
        mock_user_repo_cls.return_value = mock_user_repo

        mock_player_repo = AsyncMock()
        mock_player_repo.get_by_user_and_guild = AsyncMock(return_value=make_mock_player())
        mock_player_repo_cls.return_value = mock_player_repo

        # A.45: use concrete type (primary_weapon, not alias "weapon")
        payload = {
            "guild_id": 67890,
            "user_id": 111222333,
            "item_name": "Pulse Laser",
            "item_type": "primary_weapon",
            "quantity": 1,
        }
        with patch("persist.repositories.user_repository.UserRepository", mock_user_repo_cls):
            resp = client.post("/api/v1/admin/give-item?admin_user_id=999", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["item_name"] == "Pulse Laser"
        assert data["new_total_quantity"] == 2
        assert "message" in data

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.PlayerRepository")
    def test_give_item_user_not_found(self, mock_player_repo_cls, mock_get_db, client):
        """Returns 404 when the Discord user does not exist."""
        _configure_db_mock(mock_get_db)

        mock_user_repo = AsyncMock()
        mock_user_repo.get_by_discord_id = AsyncMock(return_value=None)
        mock_player_repo_cls.return_value = AsyncMock()

        # A.45: use concrete type
        payload = {
            "guild_id": 67890,
            "user_id": 999999,
            "item_name": "Pulse Laser",
            "item_type": "primary_weapon",
            "quantity": 1,
        }
        with patch("persist.repositories.user_repository.UserRepository") as mock_ur:
            mock_ur.return_value = mock_user_repo
            resp = client.post("/api/v1/admin/give-item?admin_user_id=999", json=payload)
        assert resp.status_code == 404

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.PlayerRepository")
    def test_give_item_player_not_found(self, mock_player_repo_cls, mock_get_db, client):
        """Returns 404 when user exists but has no player in the guild."""
        _configure_db_mock(mock_get_db)

        mock_user_repo = AsyncMock()
        mock_user_repo.get_by_discord_id = AsyncMock(return_value=make_mock_user())

        mock_player_repo = AsyncMock()
        mock_player_repo.get_by_user_and_guild = AsyncMock(return_value=None)
        mock_player_repo_cls.return_value = mock_player_repo

        # A.45: use concrete type
        payload = {
            "guild_id": 67890,
            "user_id": 111222333,
            "item_name": "Pulse Laser",
            "item_type": "primary_weapon",
            "quantity": 1,
        }
        with patch("persist.repositories.user_repository.UserRepository") as mock_ur:
            mock_ur.return_value = mock_user_repo
            resp = client.post("/api/v1/admin/give-item?admin_user_id=999", json=payload)
        assert resp.status_code == 404

    def test_give_item_invalid_item_type_rejected_by_schema(self, client):
        """Schema validation rejects invalid item_type."""
        payload = {
            "guild_id": 67890,
            "user_id": 111222333,
            "item_name": "Pulse Laser",
            "item_type": "ship",  # Not valid for give-item (only weapon/module/turret)
            "quantity": 1,
        }
        resp = client.post("/api/v1/admin/give-item?admin_user_id=999", json=payload)
        assert resp.status_code == 422  # schema validation error


# ===========================================================================
# POST /admin/remove-item
# ===========================================================================


class TestAdminRemoveItem:
    """Tests for POST /api/v1/admin/remove-item."""

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.PlayerRepository")
    def test_remove_item_success(self, mock_player_repo_cls, mock_get_db, client, mock_inventory_service):
        """Returns 200 with transaction details when item is removed successfully."""
        _configure_db_mock(mock_get_db)

        mock_user_repo = AsyncMock()
        mock_user_repo.get_by_discord_id = AsyncMock(return_value=make_mock_user())

        mock_player_repo = AsyncMock()
        mock_player_repo.get_by_user_and_guild = AsyncMock(return_value=make_mock_player())
        mock_player_repo_cls.return_value = mock_player_repo

        # A.45: use concrete type (primary_weapon, not alias "weapon")
        payload = {
            "guild_id": 67890,
            "user_id": 111222333,
            "item_name": "Pulse Laser",
            "item_type": "primary_weapon",
            "quantity": 1,
        }
        with patch("persist.repositories.user_repository.UserRepository") as mock_ur:
            mock_ur.return_value = mock_user_repo
            resp = client.post("/api/v1/admin/remove-item?admin_user_id=999", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["item_name"] == "Pulse Laser"
        assert data["quantity_removed"] == 1
        assert "message" in data

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.PlayerRepository")
    def test_remove_item_user_not_found(self, mock_player_repo_cls, mock_get_db, client):
        """Returns 404 when user does not exist."""
        _configure_db_mock(mock_get_db)

        mock_user_repo = AsyncMock()
        mock_user_repo.get_by_discord_id = AsyncMock(return_value=None)
        mock_player_repo_cls.return_value = AsyncMock()

        # A.45: use concrete type
        payload = {
            "guild_id": 67890,
            "user_id": 999999,
            "item_name": "Pulse Laser",
            "item_type": "primary_weapon",
            "quantity": 1,
        }
        with patch("persist.repositories.user_repository.UserRepository") as mock_ur:
            mock_ur.return_value = mock_user_repo
            resp = client.post("/api/v1/admin/remove-item?admin_user_id=999", json=payload)
        assert resp.status_code == 404

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.PlayerRepository")
    def test_remove_item_insufficient_quantity(self, mock_player_repo_cls, mock_get_db, client, mock_inventory_service):
        """Returns 400 when player doesn't have enough of the item."""
        _configure_db_mock(mock_get_db)

        mock_user_repo = AsyncMock()
        mock_user_repo.get_by_discord_id = AsyncMock(return_value=make_mock_user())

        mock_player_repo = AsyncMock()
        mock_player_repo.get_by_user_and_guild = AsyncMock(return_value=make_mock_player())
        mock_player_repo_cls.return_value = mock_player_repo

        mock_inventory_service.remove_item_from_inventory = AsyncMock(side_effect=ValueError("Insufficient quantity"))

        # A.45: use concrete type
        payload = {
            "guild_id": 67890,
            "user_id": 111222333,
            "item_name": "Pulse Laser",
            "item_type": "primary_weapon",
            "quantity": 999,
        }
        with patch("persist.repositories.user_repository.UserRepository") as mock_ur:
            mock_ur.return_value = mock_user_repo
            resp = client.post("/api/v1/admin/remove-item?admin_user_id=999", json=payload)
        assert resp.status_code == 400

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.PlayerRepository")
    def test_remove_item_player_not_found(self, mock_player_repo_cls, mock_get_db, client):
        """Returns 404 when player not found in the guild."""
        _configure_db_mock(mock_get_db)

        mock_user_repo = AsyncMock()
        mock_user_repo.get_by_discord_id = AsyncMock(return_value=make_mock_user())

        mock_player_repo = AsyncMock()
        mock_player_repo.get_by_user_and_guild = AsyncMock(return_value=None)
        mock_player_repo_cls.return_value = mock_player_repo

        # A.45: use concrete type
        payload = {
            "guild_id": 67890,
            "user_id": 111222333,
            "item_name": "Pulse Laser",
            "item_type": "primary_weapon",
            "quantity": 1,
        }
        with patch("persist.repositories.user_repository.UserRepository") as mock_ur:
            mock_ur.return_value = mock_user_repo
            resp = client.post("/api/v1/admin/remove-item?admin_user_id=999", json=payload)
        assert resp.status_code == 404


class TestAdminGiveItemA45Rejection:
    """A.45 alias rejection tests for POST /api/v1/admin/give-item."""

    def test_admin_give_item_rejects_alias_with_422(self, client):
        """A.45: posting item_type='weapon' (generic alias) is rejected at schema with HTTP 422.

        AdminGiveItemRequest now uses Literal[4 concrete values] — 'weapon' is not in the set.
        Mock budget: 0 (schema rejects before any handler is called).
        """
        payload = {
            "guild_id": 67890,
            "user_id": 111222333,
            "item_name": "Pulse Laser",
            "item_type": "weapon",  # alias — should be rejected
            "quantity": 1,
        }
        resp = client.post("/api/v1/admin/give-item?admin_user_id=999", json=payload)
        assert resp.status_code == 422, (
            f"Expected 422 for alias 'weapon' in AdminGiveItemRequest, got {resp.status_code}"
        )

    def test_admin_give_item_rejects_ship_with_422(self, client):
        """A.45: 'ship' is excluded from AdminGiveItemRequest (use AdminGiveShipRequest instead).

        Mock budget: 0.
        """
        payload = {
            "guild_id": 67890,
            "user_id": 111222333,
            "item_name": "Hammerhead",
            "item_type": "ship",  # excluded from give-item; use give-ship instead
            "quantity": 1,
        }
        resp = client.post("/api/v1/admin/give-item?admin_user_id=999", json=payload)
        assert resp.status_code == 422


class TestAdminRemoveItemA45Rejection:
    """A.45 alias rejection tests for POST /api/v1/admin/remove-item."""

    def test_admin_remove_item_rejects_alias_with_422(self, client):
        """A.45: posting item_type='weapon' (generic alias) is rejected at schema with HTTP 422.

        AdminRemoveItemRequest now uses Literal[4 concrete values] — 'weapon' is not in the set.
        Mock budget: 0 (schema rejects before any handler is called).
        """
        payload = {
            "guild_id": 67890,
            "user_id": 111222333,
            "item_name": "Pulse Laser",
            "item_type": "weapon",  # alias — should be rejected
            "quantity": 1,
        }
        resp = client.post("/api/v1/admin/remove-item?admin_user_id=999", json=payload)
        assert resp.status_code == 422, (
            f"Expected 422 for alias 'weapon' in AdminRemoveItemRequest, got {resp.status_code}"
        )


# ===========================================================================
# POST /admin/give-ship
# ===========================================================================


class TestAdminGiveShip:
    """Tests for POST /api/v1/admin/give-ship."""

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.ShipRepository")
    @patch("api.routers.admin.PlayerRepository")
    @patch("api.routers.admin.PlayerShipRepository")
    def test_give_ship_success(
        self,
        mock_player_ship_repo_cls,
        mock_player_repo_cls,
        mock_ship_repo_cls,
        mock_get_db,
        client,
    ):
        """Returns 200 with ship details when ship is given successfully."""
        _configure_db_mock(mock_get_db)

        mock_user_repo = AsyncMock()
        mock_user_repo.get_by_discord_id = AsyncMock(return_value=make_mock_user())

        mock_ship_repo = AsyncMock()
        mock_ship_repo.get_by_name = AsyncMock(return_value=make_mock_game_ship())
        mock_ship_repo_cls.return_value = mock_ship_repo

        mock_player_repo = AsyncMock()
        mock_player_repo.get_by_user_and_guild = AsyncMock(return_value=make_mock_player())
        mock_player_repo_cls.return_value = mock_player_repo

        created_ship = make_mock_player_ship()
        mock_ps_repo = AsyncMock()
        mock_ps_repo.add = AsyncMock(return_value=created_ship)
        mock_player_ship_repo_cls.return_value = mock_ps_repo

        payload = {
            "guild_id": 67890,
            "user_id": 111222333,
            "ship_name": "Sidewinder",
        }
        with patch("persist.repositories.user_repository.UserRepository") as mock_ur:
            mock_ur.return_value = mock_user_repo
            resp = client.post("/api/v1/admin/give-ship?admin_user_id=999", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ship_name"] == "Sidewinder"
        assert data["is_active"] is False
        assert "message" in data

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.ShipRepository")
    @patch("api.routers.admin.PlayerRepository")
    @patch("api.routers.admin.PlayerShipRepository")
    def test_give_ship_invalid_ship_name(
        self,
        mock_player_ship_repo_cls,
        mock_player_repo_cls,
        mock_ship_repo_cls,
        mock_get_db,
        client,
    ):
        """Returns 404 when the ship name doesn't exist in game data."""
        _configure_db_mock(mock_get_db)

        mock_ship_repo = AsyncMock()
        mock_ship_repo.get_by_name = AsyncMock(return_value=None)
        mock_ship_repo_cls.return_value = mock_ship_repo

        mock_player_repo_cls.return_value = AsyncMock()
        mock_player_ship_repo_cls.return_value = AsyncMock()

        payload = {
            "guild_id": 67890,
            "user_id": 111222333,
            "ship_name": "NonExistentShip",
        }
        resp = client.post("/api/v1/admin/give-ship?admin_user_id=999", json=payload)
        assert resp.status_code == 404

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.ShipRepository")
    @patch("api.routers.admin.PlayerRepository")
    @patch("api.routers.admin.PlayerShipRepository")
    def test_give_ship_user_not_found(
        self,
        mock_player_ship_repo_cls,
        mock_player_repo_cls,
        mock_ship_repo_cls,
        mock_get_db,
        client,
    ):
        """Returns 404 when Discord user is not found."""
        _configure_db_mock(mock_get_db)

        mock_ship_repo = AsyncMock()
        mock_ship_repo.get_by_name = AsyncMock(return_value=make_mock_game_ship())
        mock_ship_repo_cls.return_value = mock_ship_repo

        mock_user_repo = AsyncMock()
        mock_user_repo.get_by_discord_id = AsyncMock(return_value=None)

        mock_player_repo_cls.return_value = AsyncMock()
        mock_player_ship_repo_cls.return_value = AsyncMock()

        payload = {
            "guild_id": 67890,
            "user_id": 999999,
            "ship_name": "Sidewinder",
        }
        with patch("persist.repositories.user_repository.UserRepository") as mock_ur:
            mock_ur.return_value = mock_user_repo
            resp = client.post("/api/v1/admin/give-ship?admin_user_id=999", json=payload)
        assert resp.status_code == 404


# ===========================================================================
# POST /admin/remove-ship
# ===========================================================================


class TestAdminRemoveShip:
    """Tests for POST /api/v1/admin/remove-ship."""

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.PlayerRepository")
    @patch("api.routers.admin.PlayerShipRepository")
    @patch("api.routers.admin.InventoryRepository")
    @patch("api.routers.admin.ItemRepository")
    def test_remove_ship_success(
        self,
        mock_item_repo_cls,
        mock_inv_repo_cls,
        mock_player_ship_repo_cls,
        mock_player_repo_cls,
        mock_get_db,
        client,
    ):
        """Returns 200 and returns items to inventory on successful removal.

        A.36 regression guard: verifies that items are returned to inventory
        with CONCRETE item types (primary_weapon, module) not generic aliases.
        """
        _configure_db_mock(mock_get_db)

        mock_user_repo = AsyncMock()
        mock_user_repo.get_by_discord_id = AsyncMock(return_value=make_mock_user())

        mock_player_repo = AsyncMock()
        mock_player_repo.get_by_user_and_guild = AsyncMock(return_value=make_mock_player())
        mock_player_repo_cls.return_value = mock_player_repo

        ship = make_mock_player_ship()
        mock_ps_repo = AsyncMock()
        mock_ps_repo.get_ships_by_name = AsyncMock(return_value=[ship])
        mock_ps_repo.get_player_ships = AsyncMock(return_value=[ship, make_mock_player_ship(id=43)])
        mock_ps_repo.remove = AsyncMock()
        mock_player_ship_repo_cls.return_value = mock_ps_repo

        mock_inv_repo = AsyncMock()
        mock_inv_repo.add_item = AsyncMock()
        mock_inv_repo_cls.return_value = mock_inv_repo

        # Mock ItemRepository to return items with concrete STI discriminators
        mock_item_repo = AsyncMock()

        def _make_item_mock(type_str: str):
            m = MagicMock()
            m.type = type_str
            return m

        async def _get_by_name_any_type(db, name):
            if name == "Pulse Laser":
                return _make_item_mock("PrimaryWeapon")
            if name == "Shield Gen":
                return _make_item_mock("ShieldModule")
            return None

        mock_item_repo.get_by_name_any_type = _get_by_name_any_type
        mock_item_repo_cls.return_value = mock_item_repo

        payload = {
            "guild_id": 67890,
            "user_id": 111222333,
            "ship_name": "Sidewinder",
        }
        with patch("persist.repositories.user_repository.UserRepository") as mock_ur:
            mock_ur.return_value = mock_user_repo
            resp = client.post("/api/v1/admin/remove-ship?admin_user_id=999", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ship_name"] == "Sidewinder"
        assert "items_returned_to_inventory" in data
        assert "message" in data
        # Should have returned weapons + modules
        assert "Pulse Laser" in data["items_returned_to_inventory"]
        assert "Shield Gen" in data["items_returned_to_inventory"]
        # A.36 regression guard: verify CONCRETE types used in add_item calls
        add_item_calls = mock_inv_repo.add_item.call_args_list
        item_types_used = {call.args[2] for call in add_item_calls if len(call.args) >= 3}
        assert "weapon" not in item_types_used, "generic alias 'weapon' must not be written"
        assert "turret" not in item_types_used, "generic alias 'turret' must not be written"
        assert "primary_weapon" in item_types_used, "PrimaryWeapon must map to concrete 'primary_weapon'"
        assert "module" in item_types_used, "ShieldModule must map to concrete 'module'"

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.PlayerRepository")
    @patch("api.routers.admin.PlayerShipRepository")
    @patch("api.routers.admin.InventoryRepository")
    def test_remove_ship_only_active_ship_blocked(
        self,
        mock_inv_repo_cls,
        mock_player_ship_repo_cls,
        mock_player_repo_cls,
        mock_get_db,
        client,
    ):
        """Returns 400 when attempting to remove the player's only active ship."""
        _configure_db_mock(mock_get_db)

        mock_user_repo = AsyncMock()
        mock_user_repo.get_by_discord_id = AsyncMock(return_value=make_mock_user())

        mock_player_repo = AsyncMock()
        mock_player_repo.get_by_user_and_guild = AsyncMock(return_value=make_mock_player())
        mock_player_repo_cls.return_value = mock_player_repo

        active_ship = make_mock_player_ship(is_active=True)
        mock_ps_repo = AsyncMock()
        mock_ps_repo.get_ships_by_name = AsyncMock(return_value=[active_ship])
        mock_ps_repo.get_player_ships = AsyncMock(return_value=[active_ship])  # only one ship
        mock_player_ship_repo_cls.return_value = mock_ps_repo

        mock_inv_repo_cls.return_value = AsyncMock()

        payload = {
            "guild_id": 67890,
            "user_id": 111222333,
            "ship_name": "Sidewinder",
        }
        with patch("persist.repositories.user_repository.UserRepository") as mock_ur:
            mock_ur.return_value = mock_user_repo
            resp = client.post("/api/v1/admin/remove-ship?admin_user_id=999", json=payload)
        assert resp.status_code == 400
        assert "only active ship" in resp.json()["detail"].lower()

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.PlayerRepository")
    @patch("api.routers.admin.PlayerShipRepository")
    @patch("api.routers.admin.InventoryRepository")
    def test_remove_ship_ship_not_found(
        self,
        mock_inv_repo_cls,
        mock_player_ship_repo_cls,
        mock_player_repo_cls,
        mock_get_db,
        client,
    ):
        """Returns 404 when player does not own the specified ship."""
        _configure_db_mock(mock_get_db)

        mock_user_repo = AsyncMock()
        mock_user_repo.get_by_discord_id = AsyncMock(return_value=make_mock_user())

        mock_player_repo = AsyncMock()
        mock_player_repo.get_by_user_and_guild = AsyncMock(return_value=make_mock_player())
        mock_player_repo_cls.return_value = mock_player_repo

        mock_ps_repo = AsyncMock()
        mock_ps_repo.get_ships_by_name = AsyncMock(return_value=[])  # player doesn't own this ship
        mock_player_ship_repo_cls.return_value = mock_ps_repo

        mock_inv_repo_cls.return_value = AsyncMock()

        payload = {
            "guild_id": 67890,
            "user_id": 111222333,
            "ship_name": "VenomStrike",
        }
        with patch("persist.repositories.user_repository.UserRepository") as mock_ur:
            mock_ur.return_value = mock_user_repo
            resp = client.post("/api/v1/admin/remove-ship?admin_user_id=999", json=payload)
        assert resp.status_code == 404

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.PlayerRepository")
    @patch("api.routers.admin.PlayerShipRepository")
    @patch("api.routers.admin.InventoryRepository")
    def test_remove_ship_active_ship_allowed_when_multiple_ships(
        self,
        mock_inv_repo_cls,
        mock_player_ship_repo_cls,
        mock_player_repo_cls,
        mock_get_db,
        client,
    ):
        """Active ship CAN be removed if player has multiple ships."""
        _configure_db_mock(mock_get_db)

        mock_user_repo = AsyncMock()
        mock_user_repo.get_by_discord_id = AsyncMock(return_value=make_mock_user())

        mock_player_repo = AsyncMock()
        mock_player_repo.get_by_user_and_guild = AsyncMock(return_value=make_mock_player())
        mock_player_repo_cls.return_value = mock_player_repo

        active_ship = make_mock_player_ship(is_active=True, weapons=[], modules=[], turrets=[])
        second_ship = make_mock_player_ship(id=43, is_active=False)
        mock_ps_repo = AsyncMock()
        mock_ps_repo.get_ships_by_name = AsyncMock(return_value=[active_ship])
        mock_ps_repo.get_player_ships = AsyncMock(return_value=[active_ship, second_ship])
        mock_ps_repo.remove = AsyncMock()
        mock_player_ship_repo_cls.return_value = mock_ps_repo

        mock_inv_repo = AsyncMock()
        mock_inv_repo.add_item = AsyncMock()
        mock_inv_repo_cls.return_value = mock_inv_repo

        payload = {
            "guild_id": 67890,
            "user_id": 111222333,
            "ship_name": "Sidewinder",
        }
        with patch("persist.repositories.user_repository.UserRepository") as mock_ur:
            mock_ur.return_value = mock_user_repo
            resp = client.post("/api/v1/admin/remove-ship?admin_user_id=999", json=payload)
        assert resp.status_code == 200
