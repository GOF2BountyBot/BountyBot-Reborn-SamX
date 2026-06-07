"""Tests for the inventory API router endpoints.

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
def mock_inventory_service():
    service = AsyncMock()
    service.get_player_inventory = AsyncMock(
        return_value=[
            {
                "id": 1,
                "item_type": "weapon",
                "item_name": "Pulse Laser",
                "quantity": 2,
                "acquired_at": "2026-01-01T00:00:00",
                "item_details": {"damage": 10},
            }
        ]
    )
    service.get_inventory_summary = AsyncMock(
        return_value={
            "player_id": 1,
            "player_tier": "Bronze",
            "guild_id": 67890,
            "ship": 1,
            "primary_weapon": 2,
            "secondary_weapon": 0,
            "turret_weapon": 0,
            "module": 1,
            "total_items": 4,
        }
    )
    service.add_item_to_inventory = AsyncMock(
        return_value={
            "player_id": 1,
            "item_type": "weapon",
            "item_name": "Pulse Laser",
            "quantity_added": 1,
            "new_total_quantity": 3,
            "transaction_time": "2026-01-01T00:00:00",
        }
    )
    service.remove_item_from_inventory = AsyncMock(
        return_value={
            "player_id": 1,
            "item_type": "weapon",
            "item_name": "Pulse Laser",
            "quantity_removed": 1,
            "new_quantity": 1,
        }
    )
    service.transfer_item_between_players = AsyncMock(
        return_value={
            "from_player_id": 1,
            "to_player_id": 2,
            "item_type": "weapon",
            "item_name": "Pulse Laser",
            "quantity": 1,
            "status": "success",
        }
    )
    service.search_inventory = AsyncMock(
        return_value=[
            {
                "id": 1,
                "item_type": "weapon",
                "item_name": "Pulse Laser",
                "quantity": 2,
                "acquired_at": "2026-01-01T00:00:00",
                "item_details": {"damage": 10},
            }
        ]
    )
    service.get_player_item_count = AsyncMock(return_value=5)
    service.validate_item_compatibility = AsyncMock(return_value={"compatible": True, "reason": "OK"})
    service.consolidate_inventory = AsyncMock(return_value={"consolidated": 2, "remaining": 5})
    return service


@pytest.fixture
def mock_player_repo():
    """Mock PlayerRepository for the consolidate route's aggregate-root lock (D5-T3).

    The consolidate route acquires ``get_by_id_for_update`` on the Player row
    FIRST; router-level tests override the repo so no real DB call is made.
    """
    repo = AsyncMock()
    repo.get_by_id_for_update = AsyncMock()
    return repo


@pytest.fixture
def test_app(mock_inventory_service, mock_player_repo):
    app = FastAPI()
    from api.routers.inventory import get_inventory_service, get_player_repository
    from api.routers.inventory import router as inventory_router

    app.include_router(inventory_router, prefix="/api/v1")
    app.dependency_overrides[get_inventory_service] = lambda: mock_inventory_service
    app.dependency_overrides[get_player_repository] = lambda: mock_player_repo
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


# ---------------------------------------------------------------------------
# Helper: build a configured mock get_db_session patcher result
# ---------------------------------------------------------------------------


def _configure_db_mock(mock_get_db):
    """Configure mock_get_db to act as an async context manager.

    Also configures mock_session.begin() to return an async context manager so
    that routers using ``async with get_db_session() as db, db.begin():`` work
    correctly after the A.44 transaction-ownership fix.
    """
    from contextlib import asynccontextmanager
    from unittest.mock import MagicMock

    mock_session = AsyncMock()

    @asynccontextmanager
    async def _mock_begin():
        yield

    mock_session.begin = MagicMock(side_effect=lambda: _mock_begin())
    mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_session


# ===========================================================================
# 1. GET /inventory/player/{player_id}
# ===========================================================================


class TestGetPlayerInventory:
    """Tests for GET /api/v1/inventory/player/{player_id}."""

    @patch("api.routers.inventory.get_db_session")
    def test_get_player_inventory_happy_path(self, mock_get_db, client, mock_inventory_service):
        """Returns 200 with a list of inventory items for a valid player."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/inventory/player/1")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        item = data[0]
        assert item["id"] == 1
        assert item["item_type"] == "weapon"
        assert item["item_name"] == "Pulse Laser"
        assert item["quantity"] == 2
        assert item["acquired_at"] == "2026-01-01T00:00:00"
        assert item["item_details"] == {"damage": 10}

    @patch("api.routers.inventory.get_db_session")
    def test_get_player_inventory_with_item_type_filter(self, mock_get_db, client, mock_inventory_service):
        """Passes item_type query param to service and returns filtered results."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/inventory/player/1?item_type=weapon")

        assert response.status_code == 200
        mock_inventory_service.get_player_inventory.assert_awaited_once()
        call_args = mock_inventory_service.get_player_inventory.call_args
        # item_type should be passed as "weapon"
        assert call_args.args[2] == "weapon" or call_args.kwargs.get("item_type") == "weapon"

    @patch("api.routers.inventory.get_db_session")
    def test_get_player_inventory_value_error_returns_400(self, mock_get_db, client, mock_inventory_service):
        """Returns 400 when service raises ValueError (player not found, etc.).
        A.33 fix: was 404 (wrong); correct code for validation errors is 400.
        """
        _configure_db_mock(mock_get_db)
        mock_inventory_service.get_player_inventory.side_effect = ValueError("Player not found")

        response = client.get("/api/v1/inventory/player/999")

        assert response.status_code == 400
        assert "Player not found" in response.json()["detail"]

    @patch("api.routers.inventory.get_db_session")
    def test_get_player_inventory_invalid_item_type_returns_422(self, mock_get_db, client, mock_inventory_service):
        """Returns 422 when service raises InvalidItemTypeError (A.33 fix)."""
        from services.exceptions import InvalidItemTypeError

        _configure_db_mock(mock_get_db)
        mock_inventory_service.get_player_inventory.side_effect = InvalidItemTypeError("Unknown type 'foo'")

        response = client.get("/api/v1/inventory/player/1?item_type=foo")

        assert response.status_code == 422
        assert "foo" in response.json()["detail"]

    @patch("api.routers.inventory.get_db_session")
    def test_get_player_inventory_server_error_returns_500(self, mock_get_db, client, mock_inventory_service):
        """Returns 500 when service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_inventory_service.get_player_inventory.side_effect = RuntimeError("DB connection failed")

        response = client.get("/api/v1/inventory/player/1")

        assert response.status_code == 500
        assert "Failed to get inventory" in response.json()["detail"]


# ===========================================================================
# 2. GET /inventory/player/{player_id}/summary
# ===========================================================================


class TestGetInventorySummary:
    """Tests for GET /api/v1/inventory/player/{player_id}/summary."""

    @patch("api.routers.inventory.get_db_session")
    def test_get_inventory_summary_happy_path(self, mock_get_db, client, mock_inventory_service):
        """Returns 200 with inventory summary for a valid player."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/inventory/player/1/summary")

        assert response.status_code == 200
        data = response.json()
        assert data["player_id"] == 1
        assert data["player_tier"] == "Bronze"
        assert data["guild_id"] == 67890
        assert data["ship"] == 1
        assert data["primary_weapon"] == 2
        assert data["secondary_weapon"] == 0
        assert data["turret_weapon"] == 0
        assert data["module"] == 1
        assert data["total_items"] == 4

    @patch("api.routers.inventory.get_db_session")
    def test_get_inventory_summary_value_error_returns_400(self, mock_get_db, client, mock_inventory_service):
        """Returns 400 when service raises ValueError. A.33 fix: was 404."""
        _configure_db_mock(mock_get_db)
        mock_inventory_service.get_inventory_summary.side_effect = ValueError("Player not found")

        response = client.get("/api/v1/inventory/player/999/summary")

        assert response.status_code == 400
        assert "Player not found" in response.json()["detail"]

    @patch("api.routers.inventory.get_db_session")
    def test_get_inventory_summary_server_error_returns_500(self, mock_get_db, client, mock_inventory_service):
        """Returns 500 when service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_inventory_service.get_inventory_summary.side_effect = Exception("Unexpected error")

        response = client.get("/api/v1/inventory/player/1/summary")

        assert response.status_code == 500
        assert "Failed to get inventory summary" in response.json()["detail"]


# ===========================================================================
# 3. POST /inventory/add
# ===========================================================================


class TestAddItemToInventory:
    """Tests for POST /api/v1/inventory/add."""

    @patch("api.routers.inventory.get_db_session")
    def test_add_item_happy_path(self, mock_get_db, client, mock_inventory_service):
        """Returns 200 with transaction response when item is added successfully."""
        _configure_db_mock(mock_get_db)

        response = client.post(
            "/api/v1/inventory/add",
            # A.45: use concrete type (not alias "weapon")
            json={"player_id": 1, "item_type": "primary_weapon", "item_name": "Pulse Laser", "quantity": 1},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["player_id"] == 1
        assert data["item_name"] == "Pulse Laser"
        assert data["quantity_changed"] == 1
        assert data["new_total_quantity"] == 3
        assert data["transaction_time"] == "2026-01-01T00:00:00"

    @patch("api.routers.inventory.get_db_session")
    def test_add_item_value_error_returns_400(self, mock_get_db, client, mock_inventory_service):
        """Returns 400 when service raises ValueError."""
        _configure_db_mock(mock_get_db)
        mock_inventory_service.add_item_to_inventory.side_effect = ValueError("Item does not exist")

        response = client.post(
            "/api/v1/inventory/add",
            # A.45: use concrete type to get past schema validation before testing service error
            json={"player_id": 1, "item_type": "primary_weapon", "item_name": "Nonexistent", "quantity": 1},
        )

        assert response.status_code == 400
        assert "Item does not exist" in response.json()["detail"]

    @patch("api.routers.inventory.get_db_session")
    def test_add_item_server_error_returns_500(self, mock_get_db, client, mock_inventory_service):
        """Returns 500 when service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_inventory_service.add_item_to_inventory.side_effect = RuntimeError("DB error")

        response = client.post(
            "/api/v1/inventory/add",
            # A.45: use concrete type to reach the service (which raises RuntimeError)
            json={"player_id": 1, "item_type": "primary_weapon", "item_name": "Pulse Laser", "quantity": 1},
        )

        assert response.status_code == 500
        assert "Failed to add item to inventory" in response.json()["detail"]

    @patch("api.routers.inventory.get_db_session")
    def test_add_item_invalid_item_type_service_error_returns_422(self, mock_get_db, client, mock_inventory_service):
        """Returns 422 (not 400) when service raises InvalidItemTypeError on write (DEF-IVF-001 fix).

        A.45: uses a concrete type in the request body so the schema passes and the
        service's InvalidItemTypeError is what triggers the 422 (defense-in-depth path).
        """
        from services.exceptions import InvalidItemTypeError

        _configure_db_mock(mock_get_db)
        mock_inventory_service.add_item_to_inventory.side_effect = InvalidItemTypeError(
            "Write operations require a concrete item type; got generic alias 'weapon'"
        )

        response = client.post(
            "/api/v1/inventory/add",
            # A.45: use concrete type so schema validation passes; service raises InvalidItemTypeError
            json={"player_id": 1, "item_type": "primary_weapon", "item_name": "Pulse Laser", "quantity": 1},
        )

        assert response.status_code == 422, (
            f"Expected 422 for InvalidItemTypeError on write, got {response.status_code}"
        )
        assert "concrete item type" in response.json()["detail"]

    @patch("api.routers.inventory.get_db_session")
    def test_add_item_invalid_item_type_returns_422(self, mock_get_db, client, mock_inventory_service):
        """Returns 422 when item_type doesn't match the allowed pattern."""
        _configure_db_mock(mock_get_db)

        response = client.post(
            "/api/v1/inventory/add",
            json={"player_id": 1, "item_type": "invalid_type", "item_name": "Pulse Laser", "quantity": 1},
        )

        assert response.status_code == 422

    @patch("api.routers.inventory.get_db_session")
    def test_add_item_quantity_zero_returns_422(self, mock_get_db, client, mock_inventory_service):
        """Returns 422 when quantity is 0 (must be gt=0)."""
        _configure_db_mock(mock_get_db)

        response = client.post(
            "/api/v1/inventory/add",
            # A.45: use concrete type; the 422 is from quantity=0 validation
            json={"player_id": 1, "item_type": "primary_weapon", "item_name": "Pulse Laser", "quantity": 0},
        )

        assert response.status_code == 422

    @patch("api.routers.inventory.get_db_session")
    def test_add_item_delegates_to_service_with_correct_args(self, mock_get_db, client, mock_inventory_service):
        """Service is called with the correct arguments from the request."""
        _configure_db_mock(mock_get_db)

        client.post(
            "/api/v1/inventory/add", json={"player_id": 1, "item_type": "ship", "item_name": "Eagle", "quantity": 2}
        )

        mock_inventory_service.add_item_to_inventory.assert_awaited_once()
        call_args = mock_inventory_service.add_item_to_inventory.call_args
        # args: (db, player_id, item_type, item_name, quantity)
        assert call_args.args[1] == 1
        assert call_args.args[2] == "ship"
        assert call_args.args[3] == "Eagle"
        assert call_args.args[4] == 2


# ===========================================================================
# 4. POST /inventory/remove
# ===========================================================================


class TestRemoveItemFromInventory:
    """Tests for POST /api/v1/inventory/remove."""

    @patch("api.routers.inventory.get_db_session")
    def test_remove_item_happy_path(self, mock_get_db, client, mock_inventory_service):
        """Returns 200 with transaction response when item is removed successfully."""
        _configure_db_mock(mock_get_db)

        response = client.post(
            "/api/v1/inventory/remove",
            # A.45: use concrete type (not alias "weapon")
            json={"player_id": 1, "item_type": "primary_weapon", "item_name": "Pulse Laser", "quantity": 1},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["player_id"] == 1
        assert data["item_name"] == "Pulse Laser"
        # quantity_changed is negative because it's a removal
        assert data["quantity_changed"] == -1
        assert data["new_total_quantity"] == 1
        # Remove operations don't have a transaction_time
        assert data["transaction_time"] is None

    @patch("api.routers.inventory.get_db_session")
    def test_remove_item_value_error_returns_400(self, mock_get_db, client, mock_inventory_service):
        """Returns 400 when service raises ValueError (insufficient quantity etc.)."""
        _configure_db_mock(mock_get_db)
        mock_inventory_service.remove_item_from_inventory.side_effect = ValueError("Insufficient quantity")

        response = client.post(
            "/api/v1/inventory/remove",
            # A.45: use concrete type
            json={"player_id": 1, "item_type": "primary_weapon", "item_name": "Pulse Laser", "quantity": 100},
        )

        assert response.status_code == 400
        assert "Insufficient quantity" in response.json()["detail"]

    @patch("api.routers.inventory.get_db_session")
    def test_remove_item_server_error_returns_500(self, mock_get_db, client, mock_inventory_service):
        """Returns 500 when service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_inventory_service.remove_item_from_inventory.side_effect = RuntimeError("DB error")

        response = client.post(
            "/api/v1/inventory/remove",
            # A.45: use concrete type
            json={"player_id": 1, "item_type": "primary_weapon", "item_name": "Pulse Laser", "quantity": 1},
        )

        assert response.status_code == 500
        assert "Failed to remove item from inventory" in response.json()["detail"]

    @patch("api.routers.inventory.get_db_session")
    def test_remove_item_invalid_item_type_service_error_returns_422(self, mock_get_db, client, mock_inventory_service):
        """Returns 422 (not 400) when service raises InvalidItemTypeError on remove write (DEF-IVF-001 fix)."""
        from services.exceptions import InvalidItemTypeError

        _configure_db_mock(mock_get_db)
        mock_inventory_service.remove_item_from_inventory.side_effect = InvalidItemTypeError(
            "Write operations require a concrete item type; got generic alias 'weapon'"
        )

        response = client.post(
            "/api/v1/inventory/remove",
            # A.45: use concrete type so schema passes; service raises InvalidItemTypeError
            json={"player_id": 1, "item_type": "primary_weapon", "item_name": "Pulse Laser", "quantity": 1},
        )

        assert response.status_code == 422, (
            f"Expected 422 for InvalidItemTypeError on remove, got {response.status_code}"
        )
        assert "concrete item type" in response.json()["detail"]

    @patch("api.routers.inventory.get_db_session")
    def test_remove_item_delegates_to_service_with_correct_args(self, mock_get_db, client, mock_inventory_service):
        """Service is called with the correct arguments from the request."""
        _configure_db_mock(mock_get_db)

        client.post(
            "/api/v1/inventory/remove",
            json={"player_id": 2, "item_type": "module", "item_name": "Shield", "quantity": 3},
        )

        mock_inventory_service.remove_item_from_inventory.assert_awaited_once()
        call_args = mock_inventory_service.remove_item_from_inventory.call_args
        assert call_args.args[1] == 2
        assert call_args.args[2] == "module"
        assert call_args.args[3] == "Shield"
        assert call_args.args[4] == 3


# ===========================================================================
# 5. POST /inventory/transfer
# ===========================================================================


class TestTransferItemBetweenPlayers:
    """Tests for POST /api/v1/inventory/transfer."""

    @patch("api.routers.inventory.get_db_session")
    def test_transfer_item_happy_path(self, mock_get_db, client, mock_inventory_service):
        """Returns 200 with transfer result when items are transferred successfully."""
        _configure_db_mock(mock_get_db)

        response = client.post(
            "/api/v1/inventory/transfer",
            # A.45: use concrete type (not alias "weapon")
            json={
                "from_player_id": 1,
                "to_player_id": 2,
                "item_type": "primary_weapon",
                "item_name": "Pulse Laser",
                "quantity": 1,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["from_player_id"] == 1
        assert data["to_player_id"] == 2
        assert data["item_name"] == "Pulse Laser"
        assert data["quantity"] == 1
        assert data["status"] == "success"

    @patch("api.routers.inventory.get_db_session")
    def test_transfer_item_value_error_returns_400(self, mock_get_db, client, mock_inventory_service):
        """Returns 400 when service raises ValueError."""
        _configure_db_mock(mock_get_db)
        mock_inventory_service.transfer_item_between_players.side_effect = ValueError("Player not found")

        response = client.post(
            "/api/v1/inventory/transfer",
            # A.45: use concrete type
            json={
                "from_player_id": 1,
                "to_player_id": 999,
                "item_type": "primary_weapon",
                "item_name": "Pulse Laser",
                "quantity": 1,
            },
        )

        assert response.status_code == 400
        assert "Player not found" in response.json()["detail"]

    @patch("api.routers.inventory.get_db_session")
    def test_transfer_item_server_error_returns_500(self, mock_get_db, client, mock_inventory_service):
        """Returns 500 when service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_inventory_service.transfer_item_between_players.side_effect = RuntimeError("DB error")

        response = client.post(
            "/api/v1/inventory/transfer",
            # A.45: use concrete type
            json={
                "from_player_id": 1,
                "to_player_id": 2,
                "item_type": "primary_weapon",
                "item_name": "Pulse Laser",
                "quantity": 1,
            },
        )

        assert response.status_code == 500
        assert "Failed to transfer item" in response.json()["detail"]

    @patch("api.routers.inventory.get_db_session")
    def test_transfer_item_invalid_item_type_service_error_returns_422(
        self, mock_get_db, client, mock_inventory_service
    ):
        """Returns 422 (not 400) when service raises InvalidItemTypeError on transfer write (DEF-IVF-001 fix)."""
        from services.exceptions import InvalidItemTypeError

        _configure_db_mock(mock_get_db)
        mock_inventory_service.transfer_item_between_players.side_effect = InvalidItemTypeError(
            "Write operations require a concrete item type; got generic alias 'weapon'"
        )

        response = client.post(
            "/api/v1/inventory/transfer",
            # A.45: use concrete type so schema passes; service raises InvalidItemTypeError
            json={
                "from_player_id": 1,
                "to_player_id": 2,
                "item_type": "primary_weapon",
                "item_name": "Pulse Laser",
                "quantity": 1,
            },
        )

        assert response.status_code == 422, (
            f"Expected 422 for InvalidItemTypeError on transfer, got {response.status_code}"
        )
        assert "concrete item type" in response.json()["detail"]

    @patch("api.routers.inventory.get_db_session")
    def test_transfer_item_delegates_to_service_with_correct_args(self, mock_get_db, client, mock_inventory_service):
        """Service is called with the correct args from the request body."""
        _configure_db_mock(mock_get_db)

        client.post(
            "/api/v1/inventory/transfer",
            # A.45: use concrete type (turret_weapon not alias "turret")
            json={
                "from_player_id": 10,
                "to_player_id": 20,
                "item_type": "turret_weapon",
                "item_name": "Heavy Cannon",
                "quantity": 2,
            },
        )

        mock_inventory_service.transfer_item_between_players.assert_awaited_once()
        call_args = mock_inventory_service.transfer_item_between_players.call_args
        assert call_args.args[1] == 10
        assert call_args.args[2] == 20
        assert call_args.args[3] == "turret_weapon"
        assert call_args.args[4] == "Heavy Cannon"
        assert call_args.args[5] == 2

    @patch("api.routers.inventory.get_db_session")
    def test_transfer_item_invalid_item_type_returns_422(self, mock_get_db, client, mock_inventory_service):
        """Returns 422 when item_type doesn't match the allowed pattern."""
        _configure_db_mock(mock_get_db)

        response = client.post(
            "/api/v1/inventory/transfer",
            json={
                "from_player_id": 1,
                "to_player_id": 2,
                "item_type": "invalid",
                "item_name": "Pulse Laser",
                "quantity": 1,
            },
        )

        assert response.status_code == 422

    def test_transfer_item_rejects_alias_with_422(self, client, mock_inventory_service):
        """A.45: posting item_type='weapon' (generic alias) is rejected at schema with HTTP 422.

        The Literal schema validation rejects 'weapon' before the service is called.
        Mock budget: 0 (schema rejects before service is called).
        """
        response = client.post(
            "/api/v1/inventory/transfer",
            json={
                "from_player_id": 1,
                "to_player_id": 2,
                "item_type": "weapon",
                "item_name": "Pulse Laser",
                "quantity": 1,
            },
        )

        assert response.status_code == 422, (
            f"Expected 422 for alias 'weapon' in TransferItemRequest, got {response.status_code}"
        )
        # Service should NOT have been called
        mock_inventory_service.transfer_item_between_players.assert_not_awaited()


class TestAddItemA45Rejection:
    """A.45 alias rejection tests for POST /api/v1/inventory/add."""

    def test_add_item_rejects_alias_with_422(self, client, mock_inventory_service):
        """A.45: posting item_type='weapon' (generic alias) is rejected at schema with HTTP 422.

        Mock budget: 0 (schema rejects before service is called).
        """
        response = client.post(
            "/api/v1/inventory/add",
            json={"player_id": 1, "item_type": "weapon", "item_name": "Pulse Laser", "quantity": 1},
        )

        assert response.status_code == 422, (
            f"Expected 422 for alias 'weapon' in AddItemRequest, got {response.status_code}"
        )
        mock_inventory_service.add_item_to_inventory.assert_not_awaited()


class TestRemoveItemA45Rejection:
    """A.45 alias rejection tests for POST /api/v1/inventory/remove."""

    def test_remove_item_rejects_alias_with_422(self, client, mock_inventory_service):
        """A.45: posting item_type='weapon' (generic alias) is rejected at schema with HTTP 422.

        Mock budget: 0 (schema rejects before service is called).
        """
        response = client.post(
            "/api/v1/inventory/remove",
            json={"player_id": 1, "item_type": "weapon", "item_name": "Pulse Laser", "quantity": 1},
        )

        assert response.status_code == 422, (
            f"Expected 422 for alias 'weapon' in RemoveItemRequest, got {response.status_code}"
        )
        mock_inventory_service.remove_item_from_inventory.assert_not_awaited()


# ===========================================================================
# 6. GET /inventory/player/{player_id}/search
# ===========================================================================


class TestSearchInventory:
    """Tests for GET /api/v1/inventory/player/{player_id}/search."""

    @patch("api.routers.inventory.get_db_session")
    def test_search_inventory_happy_path(self, mock_get_db, client, mock_inventory_service):
        """Returns 200 with matching inventory items for a valid search query."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/inventory/player/1/search?q=Pulse")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        item = data[0]
        assert item["id"] == 1
        assert item["item_name"] == "Pulse Laser"
        assert item["item_type"] == "weapon"

    @patch("api.routers.inventory.get_db_session")
    def test_search_inventory_passes_query_to_service(self, mock_get_db, client, mock_inventory_service):
        """Search query is passed correctly to the service."""
        _configure_db_mock(mock_get_db)

        client.get("/api/v1/inventory/player/1/search?q=Eagle")

        mock_inventory_service.search_inventory.assert_awaited_once()
        call_args = mock_inventory_service.search_inventory.call_args
        assert call_args.args[2] == "Eagle" or call_args.kwargs.get("q") == "Eagle"

    @patch("api.routers.inventory.get_db_session")
    def test_search_inventory_value_error_returns_400(self, mock_get_db, client, mock_inventory_service):
        """Returns 400 when service raises ValueError (player not found). A.33 fix: was 404."""
        _configure_db_mock(mock_get_db)
        mock_inventory_service.search_inventory.side_effect = ValueError("Player not found")

        response = client.get("/api/v1/inventory/player/999/search?q=laser")

        assert response.status_code == 400
        assert "Player not found" in response.json()["detail"]

    @patch("api.routers.inventory.get_db_session")
    def test_search_inventory_server_error_returns_500(self, mock_get_db, client, mock_inventory_service):
        """Returns 500 when service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_inventory_service.search_inventory.side_effect = RuntimeError("DB error")

        response = client.get("/api/v1/inventory/player/1/search?q=laser")

        assert response.status_code == 500
        assert "Failed to search inventory" in response.json()["detail"]

    @patch("api.routers.inventory.get_db_session")
    def test_search_inventory_empty_results(self, mock_get_db, client, mock_inventory_service):
        """Returns 200 with empty list when no items match the search."""
        _configure_db_mock(mock_get_db)
        mock_inventory_service.search_inventory.return_value = []

        response = client.get("/api/v1/inventory/player/1/search?q=nonexistent")

        assert response.status_code == 200
        assert response.json() == []


# ===========================================================================
# 7. GET /inventory/player/{player_id}/item/{item_name}/count
# ===========================================================================


class TestGetItemCount:
    """Tests for GET /api/v1/inventory/player/{player_id}/item/{item_name}/count."""

    @patch("api.routers.inventory.get_db_session")
    def test_get_item_count_happy_path(self, mock_get_db, client, mock_inventory_service):
        """Returns 200 with the item count for a player's specific item."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/inventory/player/1/item/Pulse%20Laser/count?item_type=weapon")

        assert response.status_code == 200
        data = response.json()
        assert data["player_id"] == 1
        assert data["item_type"] == "weapon"
        assert data["item_name"] == "Pulse Laser"
        assert data["quantity"] == 5

    @patch("api.routers.inventory.get_db_session")
    def test_get_item_count_delegates_to_service(self, mock_get_db, client, mock_inventory_service):
        """Service is called with the correct player_id, item_type, and item_name."""
        _configure_db_mock(mock_get_db)

        client.get("/api/v1/inventory/player/2/item/Eagle/count?item_type=ship")

        mock_inventory_service.get_player_item_count.assert_awaited_once()
        call_args = mock_inventory_service.get_player_item_count.call_args
        # args: (db, player_id, item_type, item_name)
        assert call_args.args[1] == 2
        assert call_args.args[2] == "ship"
        assert call_args.args[3] == "Eagle"

    @patch("api.routers.inventory.get_db_session")
    def test_get_item_count_server_error_returns_500(self, mock_get_db, client, mock_inventory_service):
        """Returns 500 when service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_inventory_service.get_player_item_count.side_effect = RuntimeError("DB error")

        response = client.get("/api/v1/inventory/player/1/item/Pulse%20Laser/count?item_type=weapon")

        assert response.status_code == 500
        assert "Failed to get item count" in response.json()["detail"]

    @patch("api.routers.inventory.get_db_session")
    def test_get_item_count_zero(self, mock_get_db, client, mock_inventory_service):
        """Returns 200 with quantity 0 when player has none of the specified item."""
        _configure_db_mock(mock_get_db)
        mock_inventory_service.get_player_item_count.return_value = 0

        response = client.get("/api/v1/inventory/player/1/item/Nonexistent/count?item_type=weapon")

        assert response.status_code == 200
        data = response.json()
        assert data["quantity"] == 0


# ===========================================================================
# 8. GET /inventory/player/{player_id}/validate/{ship_name}/{item_name}
# ===========================================================================


class TestValidateItemCompatibility:
    """Tests for GET /api/v1/inventory/player/{player_id}/validate/{ship_name}/{item_name}."""

    @patch("api.routers.inventory.get_db_session")
    def test_validate_item_compatibility_happy_path(self, mock_get_db, client, mock_inventory_service):
        """Returns 200 with compatibility result when validation succeeds."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/inventory/player/1/validate/Eagle/Pulse%20Laser?item_type=weapon")

        assert response.status_code == 200
        data = response.json()
        assert data["compatible"] is True
        assert data["reason"] == "OK"

    @patch("api.routers.inventory.get_db_session")
    def test_validate_item_compatibility_delegates_to_service(self, mock_get_db, client, mock_inventory_service):
        """Service is called with the correct player_id, ship_name, item_type, and item_name."""
        _configure_db_mock(mock_get_db)

        client.get("/api/v1/inventory/player/3/validate/Cobra/Turret%20Mk2?item_type=turret")

        mock_inventory_service.validate_item_compatibility.assert_awaited_once()
        call_args = mock_inventory_service.validate_item_compatibility.call_args
        # args: (db, player_id, ship_name, item_type, item_name)
        assert call_args.args[1] == 3
        assert call_args.args[2] == "Cobra"
        assert call_args.args[3] == "turret"
        assert call_args.args[4] == "Turret Mk2"

    @patch("api.routers.inventory.get_db_session")
    def test_validate_item_compatibility_incompatible(self, mock_get_db, client, mock_inventory_service):
        """Returns 200 with compatible=False when item is not compatible with ship."""
        _configure_db_mock(mock_get_db)
        mock_inventory_service.validate_item_compatibility.return_value = {
            "compatible": False,
            "reason": "Item too large for ship",
        }

        response = client.get("/api/v1/inventory/player/1/validate/Scout/Heavy%20Cannon?item_type=turret")

        assert response.status_code == 200
        data = response.json()
        assert data["compatible"] is False
        assert "too large" in data["reason"]

    @patch("api.routers.inventory.get_db_session")
    def test_validate_item_compatibility_server_error_returns_500(self, mock_get_db, client, mock_inventory_service):
        """Returns 500 when service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_inventory_service.validate_item_compatibility.side_effect = RuntimeError("DB error")

        response = client.get("/api/v1/inventory/player/1/validate/Eagle/Pulse%20Laser?item_type=weapon")

        assert response.status_code == 500
        assert "Failed to validate item compatibility" in response.json()["detail"]


# ===========================================================================
# 9. POST /inventory/player/{player_id}/consolidate
# ===========================================================================


class TestConsolidateInventory:
    """Tests for POST /api/v1/inventory/player/{player_id}/consolidate."""

    @patch("api.routers.inventory.get_db_session")
    def test_consolidate_inventory_happy_path(self, mock_get_db, client, mock_inventory_service):
        """Returns 200 with consolidation result when consolidation succeeds."""
        _configure_db_mock(mock_get_db)

        response = client.post("/api/v1/inventory/player/1/consolidate")

        assert response.status_code == 200
        data = response.json()
        assert data["consolidated"] == 2
        assert data["remaining"] == 5

    @patch("api.routers.inventory.get_db_session")
    def test_consolidate_inventory_delegates_to_service(self, mock_get_db, client, mock_inventory_service):
        """Service is called with the correct player_id, commit=False (router owns the txn)."""
        _configure_db_mock(mock_get_db)

        client.post("/api/v1/inventory/player/7/consolidate")

        mock_inventory_service.consolidate_inventory.assert_awaited_once()
        call_args = mock_inventory_service.consolidate_inventory.call_args
        assert call_args.args[1] == 7
        # D5-T3: the service runs with commit=False so the router's db.begin()
        # owns the transaction and the Player FOR UPDATE lock spans the whole RMW.
        assert call_args.kwargs.get("commit") is False

    @patch("api.routers.inventory.get_db_session")
    def test_consolidate_inventory_locks_player_first(
        self, mock_get_db, client, mock_inventory_service, mock_player_repo
    ):
        """D5-T3: the aggregate-root Player lock is acquired BEFORE the consolidate RMW."""
        _configure_db_mock(mock_get_db)

        response = client.post("/api/v1/inventory/player/7/consolidate")

        assert response.status_code == 200
        # Lock-first: get_by_id_for_update on the same player_id must have been awaited.
        mock_player_repo.get_by_id_for_update.assert_awaited_once()
        assert mock_player_repo.get_by_id_for_update.call_args.args[1] == 7

    @patch("api.routers.inventory.get_db_session")
    def test_consolidate_inventory_server_error_returns_500(self, mock_get_db, client, mock_inventory_service):
        """Returns 500 when service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_inventory_service.consolidate_inventory.side_effect = RuntimeError("DB error")

        response = client.post("/api/v1/inventory/player/1/consolidate")

        assert response.status_code == 500
        assert "Failed to consolidate inventory" in response.json()["detail"]

    @patch("api.routers.inventory.get_db_session")
    def test_consolidate_inventory_nothing_to_consolidate(self, mock_get_db, client, mock_inventory_service):
        """Returns 200 with zero consolidated when no duplicates exist."""
        _configure_db_mock(mock_get_db)
        mock_inventory_service.consolidate_inventory.return_value = {"consolidated": 0, "remaining": 10}

        response = client.post("/api/v1/inventory/player/1/consolidate")

        assert response.status_code == 200
        data = response.json()
        assert data["consolidated"] == 0
        assert data["remaining"] == 10
