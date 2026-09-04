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
    """Build a REAL ``Player`` ORM instance (transient, no DB).

    The admin give/remove endpoints only read real columns off the player
    (``player.id`` etc.); a real instance removes the MagicMock auto-attribute
    masking without needing a DB session.
    """
    from persist.models.player import Player

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
    return Player(**defaults)


def make_mock_user(**overrides):
    """Build a REAL ``User`` ORM instance.

    The router reads only ``user.id`` (via get_by_discord_id); the previous mock
    carried fabricated ``discord_id``/``username`` columns that the real User
    model does not have (its columns are id / discord_username / display_name).
    """
    from persist.models.user import User

    defaults = dict(id=1, discord_username="TestUser", display_name="TestUser")
    defaults.update(overrides)
    return User(**defaults)


def make_mock_player_ship(**overrides):
    """Build a REAL ``PlayerShip`` ORM instance (transient, no DB)."""
    from persist.models.player_ship import PlayerShip

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
    return PlayerShip(**defaults)


def make_mock_game_ship(**overrides):
    """A REAL game ``Ship`` from ShipRepository (not PlayerShip); transient, no DB."""
    from persist.models.ship import Ship

    defaults = dict(id=99, name="Sidewinder")
    defaults.update(overrides)
    return Ship(**defaults)


def _configure_db_mock(mock_get_db):
    mock_session = AsyncMock()
    mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
    # Package G B.19: routers now wrap mutating calls in `db.begin()`.
    # Make `db.begin()` return an async context manager so the patched
    # session works with `async with db.begin():`.
    _begin_ctx = AsyncMock()
    _begin_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    _begin_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=_begin_ctx)
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

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.PlayerRepository")
    @patch("persist.repositories.user_repository.UserRepository")
    def test_give_item_without_item_type_resolves_from_catalog(
        self, mock_user_repo_cls, mock_player_repo_cls, mock_get_db, client, mock_inventory_service
    ):
        """B.80: item_type omitted — server resolves concrete type from item catalog."""
        _configure_db_mock(mock_get_db)

        mock_user_repo = AsyncMock()
        mock_user_repo.get_by_discord_id = AsyncMock(return_value=make_mock_user())
        mock_user_repo_cls.return_value = mock_user_repo

        mock_player_repo = AsyncMock()
        mock_player_repo.get_by_user_and_guild = AsyncMock(return_value=make_mock_player())
        mock_player_repo_cls.return_value = mock_player_repo

        # Simulate _get_item_details returning the resolved concrete type
        mock_inventory_service.get_item_details = AsyncMock(
            return_value={"name": "Pulse Laser", "type": "primary_weapon", "tech_level": 5, "value": 1000}
        )

        # B.80: no item_type in payload
        payload = {
            "guild_id": 67890,
            "user_id": 111222333,
            "item_name": "Pulse Laser",
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
    @patch("persist.repositories.user_repository.UserRepository")
    def test_give_item_without_item_type_item_not_in_catalog(
        self, mock_user_repo_cls, mock_player_repo_cls, mock_get_db, client, mock_inventory_service
    ):
        """B.80: item_type omitted and item not in catalog returns 404."""
        _configure_db_mock(mock_get_db)

        mock_user_repo = AsyncMock()
        mock_user_repo.get_by_discord_id = AsyncMock(return_value=make_mock_user())
        mock_user_repo_cls.return_value = mock_user_repo

        mock_player_repo = AsyncMock()
        mock_player_repo.get_by_user_and_guild = AsyncMock(return_value=make_mock_player())
        mock_player_repo_cls.return_value = mock_player_repo

        # _get_item_details returns None for unknown items
        mock_inventory_service.get_item_details = AsyncMock(return_value=None)

        payload = {
            "guild_id": 67890,
            "user_id": 111222333,
            "item_name": "NonExistentItem",
            "quantity": 1,
        }
        with patch("persist.repositories.user_repository.UserRepository", mock_user_repo_cls):
            resp = client.post("/api/v1/admin/give-item?admin_user_id=999", json=payload)
        assert resp.status_code == 404
        assert "not found in game catalog" in resp.json()["detail"]

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.PlayerRepository")
    @patch("persist.repositories.user_repository.UserRepository")
    def test_give_item_without_item_type_ship_rejected(
        self, mock_user_repo_cls, mock_player_repo_cls, mock_get_db, client, mock_inventory_service
    ):
        """B.80: item_type omitted but item resolves to ship type returns 400 (use /give-ship)."""
        _configure_db_mock(mock_get_db)

        mock_user_repo = AsyncMock()
        mock_user_repo.get_by_discord_id = AsyncMock(return_value=make_mock_user())
        mock_user_repo_cls.return_value = mock_user_repo

        mock_player_repo = AsyncMock()
        mock_player_repo.get_by_user_and_guild = AsyncMock(return_value=make_mock_player())
        mock_player_repo_cls.return_value = mock_player_repo

        # _get_item_details returns ship type
        mock_inventory_service.get_item_details = AsyncMock(
            return_value={"name": "Sidewinder", "type": "ship", "tech_level": None, "value": 5000}
        )

        payload = {
            "guild_id": 67890,
            "user_id": 111222333,
            "item_name": "Sidewinder",
            "quantity": 1,
        }
        with patch("persist.repositories.user_repository.UserRepository", mock_user_repo_cls):
            resp = client.post("/api/v1/admin/give-item?admin_user_id=999", json=payload)
        assert resp.status_code == 400
        assert "ship" in resp.json()["detail"].lower()


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


class TestAdminRemoveItemTypeResolution:
    """B.80-style: item_type optional — resolved from player inventory when omitted."""

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.PlayerRepository")
    def test_remove_item_without_item_type_resolves_from_inventory(
        self, mock_player_repo_cls, mock_get_db, client, mock_inventory_service
    ):
        """item_type omitted → resolved from player's inventory row by item_name → 200."""
        _configure_db_mock(mock_get_db)

        mock_user_repo = AsyncMock()
        mock_user_repo.get_by_discord_id = AsyncMock(return_value=make_mock_user())

        mock_player_repo = AsyncMock()
        mock_player_repo.get_by_user_and_guild = AsyncMock(return_value=make_mock_player())
        mock_player_repo_cls.return_value = mock_player_repo

        # Fake inventory row with concrete type
        mock_inv_row = MagicMock()
        mock_inv_row.item_type = "primary_weapon"

        mock_inventory_repo = AsyncMock()
        mock_inventory_repo.get_player_items_by_name = AsyncMock(return_value=[mock_inv_row])

        # No item_type in payload
        payload = {
            "guild_id": 67890,
            "user_id": 111222333,
            "item_name": "Pulse Laser",
            "quantity": 1,
        }
        # InventoryRepository is imported inside the function body (deferred import),
        # so we must patch at the source module level.
        with (
            patch("persist.repositories.user_repository.UserRepository") as mock_ur,
            patch("persist.repositories.inventory_repository.InventoryRepository", return_value=mock_inventory_repo),
        ):
            mock_ur.return_value = mock_user_repo
            resp = client.post("/api/v1/admin/remove-item?admin_user_id=999", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["item_name"] == "Pulse Laser"
        assert "message" in data

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.PlayerRepository")
    def test_remove_item_without_item_type_not_in_inventory_returns_404(
        self, mock_player_repo_cls, mock_get_db, client
    ):
        """item_type omitted → item not found in player's inventory → 404."""
        _configure_db_mock(mock_get_db)

        mock_user_repo = AsyncMock()
        mock_user_repo.get_by_discord_id = AsyncMock(return_value=make_mock_user())

        mock_player_repo = AsyncMock()
        mock_player_repo.get_by_user_and_guild = AsyncMock(return_value=make_mock_player())
        mock_player_repo_cls.return_value = mock_player_repo

        mock_inventory_repo = AsyncMock()
        mock_inventory_repo.get_player_items_by_name = AsyncMock(return_value=[])  # empty — not in inventory

        payload = {
            "guild_id": 67890,
            "user_id": 111222333,
            "item_name": "Nonexistent Item",
            "quantity": 1,
        }
        # InventoryRepository is imported inside the function body (deferred import),
        # so we must patch at the source module level.
        with (
            patch("persist.repositories.user_repository.UserRepository") as mock_ur,
            patch("persist.repositories.inventory_repository.InventoryRepository", return_value=mock_inventory_repo),
        ):
            mock_ur.return_value = mock_user_repo
            resp = client.post("/api/v1/admin/remove-item?admin_user_id=999", json=payload)
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.PlayerRepository")
    def test_remove_item_with_explicit_item_type_still_works(
        self, mock_player_repo_cls, mock_get_db, client, mock_inventory_service
    ):
        """item_type provided explicitly → passed through without inventory lookup (backward compat)."""
        _configure_db_mock(mock_get_db)

        mock_user_repo = AsyncMock()
        mock_user_repo.get_by_discord_id = AsyncMock(return_value=make_mock_user())

        mock_player_repo = AsyncMock()
        mock_player_repo.get_by_user_and_guild = AsyncMock(return_value=make_mock_player())
        mock_player_repo_cls.return_value = mock_player_repo

        payload = {
            "guild_id": 67890,
            "user_id": 111222333,
            "item_name": "Pulse Laser",
            "item_type": "primary_weapon",  # explicit — no inventory lookup needed
            "quantity": 1,
        }
        with patch("persist.repositories.user_repository.UserRepository") as mock_ur:
            mock_ur.return_value = mock_user_repo
            resp = client.post("/api/v1/admin/remove-item?admin_user_id=999", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["item_name"] == "Pulse Laser"


class TestAdminRemoveItemA45Rejection:
    """A.45 alias rejection tests for POST /api/v1/admin/remove-item."""

    def test_admin_remove_item_rejects_alias_with_422(self, client):
        """A.45: posting item_type='weapon' (generic alias) is rejected at schema with HTTP 422.

        AdminRemoveItemRequest allows None but NOT aliases.
        'weapon' is not in the Literal set, so it is still rejected with 422.
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
    def test_give_ship_success(
        self,
        mock_player_repo_cls,
        mock_ship_repo_cls,
        mock_get_db,
        client,
        mock_inventory_service,
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

        # grant_ship goes through inventory_service; no PlayerShipRepository needed here
        created_ship = make_mock_player_ship()
        mock_inventory_service.grant_ship = AsyncMock(return_value=created_ship)

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
    def test_remove_ship_success(
        self,
        mock_player_ship_repo_cls,
        mock_player_repo_cls,
        mock_get_db,
        client,
    ):
        """Returns 200 and returns items to inventory on successful removal.

        Package G (B.19): the inline evacuation loop has been replaced with a
        call to ``LoadoutConsistencyService.evacuate_ship_loadout_to_inventory``.
        We patch that service directly to control its return shape.

        A.36 regression guard: verifies the response surfaces the items moved
        to inventory.
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

        # Patch the LoadoutConsistencyService used by the router; return a
        # canonical evacuation report so the response is deterministic.
        mock_consistency = AsyncMock()
        mock_consistency.evacuate_ship_loadout_to_inventory = AsyncMock(
            return_value={
                "items_returned": ["Pulse Laser", "Shield Gen"],
                "items_returned_detail": {
                    "weapons": ["Pulse Laser"],
                    "modules": ["Shield Gen"],
                    "turrets": [],
                    "secondary_weapons": [],
                },
                "duplicates_dropped": 0,
            }
        )

        payload = {
            "guild_id": 67890,
            "user_id": 111222333,
            "ship_name": "Sidewinder",
        }
        with (
            patch("persist.repositories.user_repository.UserRepository") as mock_ur,
            patch("services.loadout_consistency_service.LoadoutConsistencyService") as mock_lcs_cls,
        ):
            mock_ur.return_value = mock_user_repo
            mock_lcs_cls.return_value = mock_consistency
            resp = client.post("/api/v1/admin/remove-ship?admin_user_id=999", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ship_name"] == "Sidewinder"
        assert "items_returned_to_inventory" in data
        assert "message" in data
        # Should have returned weapons + modules
        assert "Pulse Laser" in data["items_returned_to_inventory"]
        assert "Shield Gen" in data["items_returned_to_inventory"]
        # The router now delegates to the consistency service exactly once.
        assert mock_consistency.evacuate_ship_loadout_to_inventory.call_count == 1

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.PlayerRepository")
    @patch("api.routers.admin.PlayerShipRepository")
    def test_remove_ship_only_active_ship_blocked(
        self,
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
    def test_remove_ship_ship_not_found(
        self,
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
    def test_remove_ship_active_ship_allowed_when_multiple_ships(
        self,
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

        # Patch the consistency service so evacuation is deterministic.
        mock_consistency = AsyncMock()
        mock_consistency.evacuate_ship_loadout_to_inventory = AsyncMock(
            return_value={
                "items_returned": [],
                "items_returned_detail": {
                    "weapons": [],
                    "modules": [],
                    "turrets": [],
                    "secondary_weapons": [],
                },
                "duplicates_dropped": 0,
            }
        )

        payload = {
            "guild_id": 67890,
            "user_id": 111222333,
            "ship_name": "Sidewinder",
        }
        with (
            patch("persist.repositories.user_repository.UserRepository") as mock_ur,
            patch("services.loadout_consistency_service.LoadoutConsistencyService") as mock_lcs_cls,
        ):
            mock_ur.return_value = mock_user_repo
            mock_lcs_cls.return_value = mock_consistency
            resp = client.post("/api/v1/admin/remove-ship?admin_user_id=999", json=payload)
        assert resp.status_code == 200
