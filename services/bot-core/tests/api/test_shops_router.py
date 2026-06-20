"""Tests for the shops API router endpoints.

Import path setup and sqlalchemy_utils mocking are handled by
tests/api/conftest.py which runs before this module is loaded.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def make_mock_shop_item(**overrides):
    """Create a mock shop item object."""
    defaults = dict(
        id=1,
        guild_id=67890,
        tier="Bronze",
        tech_level=1,
        item_type="weapon",
        item_name="Pulse Laser",
        quantity=5,
        price=100,
        last_restocked=datetime(2026, 1, 1),
        refresh_interval_hours=24,
    )
    defaults.update(overrides)
    item = MagicMock()
    for k, v in defaults.items():
        setattr(item, k, v)
    return item


@pytest.fixture
def mock_shop_service():
    service = AsyncMock()
    service.shop_repo = AsyncMock()
    service.get_shop_items = AsyncMock(return_value=[make_mock_shop_item()])
    service.shop_repo.get_guild_shops_summary = AsyncMock(
        return_value={
            "guild_id": 67890,
            "total_items": 10,
            "shops": {"Bronze": {"weapons": 3, "modules": 2}, "Silver": {"weapons": 5}},
        }
    )
    service.purchase_item = AsyncMock(
        return_value={
            "player_id": 1,
            "item_type": "weapon",
            "item_name": "Pulse Laser",
            "quantity": 1,
            "total_cost": 100,
            "remaining_credits": 400,
        }
    )
    service.sell_item = AsyncMock(
        return_value={
            "player_id": 1,
            "item_type": "primary_weapon",
            "item_name": "Pulse Laser",
            "quantity": 1,
            "total_sell_value": 50,
            "new_credits": 550,
        }
    )
    service.refresh_shop = AsyncMock(return_value={"refreshed": True, "items_count": 10})
    service.shop_repo.get_shop_statistics = AsyncMock(return_value={"total_items": 10, "avg_price": 150})
    service.shop_repo.get_items_by_tech_level = AsyncMock(return_value=[make_mock_shop_item()])
    service.shop_repo.get_items_due_for_refresh = AsyncMock(return_value=[])
    service.shop_repo.get_by_id = AsyncMock(return_value=make_mock_shop_item())
    service.shop_repo.update_prices = AsyncMock(return_value=10)
    service.purchase_ship = AsyncMock(
        return_value={
            "player_id": 1,
            "item_type": "ship",
            "item_name": "Hammerhead",
            "quantity": 1,
            "unit_price": 5000,
            "total_cost": 5000,
            "trade_in_value": 0,
            "net_cost": 5000,
            "remaining_credits": 5000,
            "items_transferred": 2,
            "items_unequipped_to_inventory": 0,
            "remaining_shop_quantity": 2,
        }
    )
    return service


@pytest.fixture
def test_app(mock_shop_service):
    app = FastAPI()
    from api.routers.shops import get_shop_service
    from api.routers.shops import router as shops_router

    app.include_router(shops_router, prefix="/api/v1")
    app.dependency_overrides[get_shop_service] = lambda: mock_shop_service
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

    Also configures mock_session.execute() to return a MagicMock result that
    supports .scalars() → iterable (empty list) for the new stat-lookup queries
    added in Task 0002 Sub-task A (_build_item_stat_map).
    """
    from contextlib import asynccontextmanager

    mock_session = AsyncMock()

    @asynccontextmanager
    async def _mock_begin():
        yield

    mock_session.begin = MagicMock(side_effect=lambda: _mock_begin())

    # Configure execute() to return a MagicMock result that supports
    # row-iteration AND .scalars() → empty iterable.
    # Without this, AsyncMock.execute.return_value is itself an AsyncMock,
    # making .scalars() return a coroutine which is not iterable.
    _execute_result = MagicMock()
    _execute_result.scalars.return_value = iter([])  # empty scalars()
    _execute_result.__iter__ = MagicMock(return_value=iter([]))  # empty row iteration
    mock_session.execute = AsyncMock(return_value=_execute_result)

    mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_session


# ===========================================================================
# 1. GET /shops/guild/{guild_id}/tier/{tier}
# ===========================================================================


class TestGetShopItems:
    """Tests for GET /api/v1/shops/guild/{guild_id}/tier/{tier}."""

    @patch("api.routers.shops.get_db_session")
    def test_get_shop_items_happy_path(self, mock_get_db, client, mock_shop_service):
        """Returns 200 with a list of shop items for a valid guild/tier."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/shops/guild/67890/tier/Bronze")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        item = data[0]
        assert item["id"] == 1
        assert item["guild_id"] == 67890
        assert item["tier"] == "Bronze"
        assert item["tech_level"] == 1
        assert item["item_type"] == "weapon"
        assert item["item_name"] == "Pulse Laser"
        assert item["quantity"] == 5
        assert item["price"] == 100
        assert item["refresh_interval_hours"] == 24

    @patch("api.routers.shops.get_db_session")
    def test_get_shop_items_with_item_type_filter(self, mock_get_db, client, mock_shop_service):
        """Passes item_type query param to service and returns filtered results."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/shops/guild/67890/tier/Bronze?item_type=weapon")

        assert response.status_code == 200
        mock_shop_service.get_shop_items.assert_awaited_once()
        call_args = mock_shop_service.get_shop_items.call_args
        # item_type should be passed as "weapon"
        assert call_args.args[3] == "weapon" or call_args.kwargs.get("item_type") == "weapon"

    @patch("api.routers.shops.get_db_session")
    def test_get_shop_items_value_error_returns_400(self, mock_get_db, client, mock_shop_service):
        """Returns 400 when service raises ValueError."""
        _configure_db_mock(mock_get_db)
        mock_shop_service.get_shop_items.side_effect = ValueError("Invalid tier")

        response = client.get("/api/v1/shops/guild/67890/tier/invalid")

        assert response.status_code == 400
        assert "Invalid tier" in response.json()["detail"]

    @patch("api.routers.shops.get_db_session")
    def test_get_shop_items_server_error_returns_500(self, mock_get_db, client, mock_shop_service):
        """Returns 500 when service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_shop_service.get_shop_items.side_effect = RuntimeError("DB failure")

        response = client.get("/api/v1/shops/guild/67890/tier/Bronze")

        assert response.status_code == 500
        assert "Failed to get shop items" in response.json()["detail"]


# ===========================================================================
# 2. GET /shops/guild/{guild_id}/summary
# ===========================================================================


class TestGetGuildShopsSummary:
    """Tests for GET /api/v1/shops/guild/{guild_id}/summary."""

    @patch("api.routers.shops.get_db_session")
    def test_get_guild_shops_summary_happy_path(self, mock_get_db, client, mock_shop_service):
        """Returns 200 with guild shops summary."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/shops/guild/67890/summary")

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890
        assert data["total_items"] == 10
        assert "Bronze" in data["shops"]
        assert data["shops"]["Bronze"]["weapons"] == 3

    @patch("api.routers.shops.get_db_session")
    def test_get_guild_shops_summary_server_error_returns_500(self, mock_get_db, client, mock_shop_service):
        """Returns 500 when repo raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_shop_service.shop_repo.get_guild_shops_summary.side_effect = RuntimeError("DB error")

        response = client.get("/api/v1/shops/guild/67890/summary")

        assert response.status_code == 500
        assert "Failed to get shops summary" in response.json()["detail"]


# ===========================================================================
# 3. POST /shops/purchase
# ===========================================================================


class TestPurchaseItem:
    """Tests for POST /api/v1/shops/purchase."""

    @patch("api.routers.shops.get_db_session")
    def test_purchase_item_happy_path(self, mock_get_db, client, mock_shop_service):
        """Returns 200 with a TransactionResponse on a successful purchase."""
        _configure_db_mock(mock_get_db)
        payload = {"player_id": 1, "shop_item_id": 1, "quantity": 1}

        response = client.post("/api/v1/shops/purchase", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["player_id"] == 1
        assert data["item_type"] == "weapon"
        assert data["item_name"] == "Pulse Laser"
        assert data["quantity"] == 1
        assert data["total_cost"] == 100
        assert data["remaining_credits"] == 400
        assert data["transaction_type"] == "purchase"

    @patch("api.routers.shops.get_db_session")
    def test_purchase_item_insufficient_credits_returns_400(self, mock_get_db, client, mock_shop_service):
        """Returns 400 when service raises ValueError (e.g. insufficient credits)."""
        _configure_db_mock(mock_get_db)
        mock_shop_service.purchase_item.side_effect = ValueError("Insufficient credits")
        payload = {"player_id": 1, "shop_item_id": 1, "quantity": 1}

        response = client.post("/api/v1/shops/purchase", json=payload)

        assert response.status_code == 400
        assert "Insufficient credits" in response.json()["detail"]

    @patch("api.routers.shops.get_db_session")
    def test_purchase_item_server_error_returns_500(self, mock_get_db, client, mock_shop_service):
        """Returns 500 when service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_shop_service.purchase_item.side_effect = RuntimeError("unexpected")
        payload = {"player_id": 1, "shop_item_id": 1, "quantity": 1}

        response = client.post("/api/v1/shops/purchase", json=payload)

        assert response.status_code == 500
        assert "Failed to process purchase" in response.json()["detail"]

    def test_purchase_item_validation_quantity_zero_returns_422(self, client, mock_shop_service):
        """Returns 422 when quantity is 0 (must be gt=0)."""
        payload = {"player_id": 1, "shop_item_id": 1, "quantity": 0}

        response = client.post("/api/v1/shops/purchase", json=payload)

        assert response.status_code == 422

    def test_purchase_item_validation_missing_player_id_returns_422(self, client, mock_shop_service):
        """Returns 422 when required field player_id is missing."""
        payload = {"shop_item_id": 1, "quantity": 1}

        response = client.post("/api/v1/shops/purchase", json=payload)

        assert response.status_code == 422

    def test_purchase_item_validation_missing_shop_item_id_returns_422(self, client, mock_shop_service):
        """Returns 422 when required field shop_item_id is missing."""
        payload = {"player_id": 1, "quantity": 1}

        response = client.post("/api/v1/shops/purchase", json=payload)

        assert response.status_code == 422


# ===========================================================================
# 4. POST /shops/sell
# ===========================================================================


class TestSellItem:
    """Tests for POST /api/v1/shops/sell.

    A.42b: SellRequest no longer accepts item_type or target_tier.
    The server resolves item_type from inventory and uses player.tier as target_tier.
    """

    @patch("api.routers.shops.get_db_session")
    def test_sell_item_happy_path(self, mock_get_db, client, mock_shop_service):
        """Returns 200 with a TransactionResponse on a successful sale (A.42 regression test).

        Payload: player_id + item_name + quantity only (no item_type, no target_tier).
        Verifies that the concrete item_type returned is primary_weapon (not the generic 'weapon').
        """
        _configure_db_mock(mock_get_db)
        payload = {
            "player_id": 1,
            "item_name": "Pulse Laser",
            "quantity": 1,
        }

        response = client.post("/api/v1/shops/sell", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["player_id"] == 1
        assert data["item_type"] == "primary_weapon"  # concrete type — not generic 'weapon'
        assert data["item_name"] == "Pulse Laser"
        assert data["quantity"] == 1
        assert data["total_value"] == 50
        assert data["remaining_credits"] == 550
        assert data["transaction_type"] == "sale"

    @patch("api.routers.shops.get_db_session")
    def test_sell_item_value_error_returns_400(self, mock_get_db, client, mock_shop_service):
        """Returns 400 when service raises ValueError."""
        _configure_db_mock(mock_get_db)
        mock_shop_service.sell_item.side_effect = ValueError("Item not in inventory")
        payload = {
            "player_id": 1,
            "item_name": "Nonexistent",
            "quantity": 1,
        }

        response = client.post("/api/v1/shops/sell", json=payload)

        assert response.status_code == 400
        assert "Item not in inventory" in response.json()["detail"]

    @patch("api.routers.shops.get_db_session")
    def test_sell_item_server_error_returns_500(self, mock_get_db, client, mock_shop_service):
        """Returns 500 when service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_shop_service.sell_item.side_effect = RuntimeError("DB crashed")
        payload = {
            "player_id": 1,
            "item_name": "Pulse Laser",
            "quantity": 1,
        }

        response = client.post("/api/v1/shops/sell", json=payload)

        assert response.status_code == 500
        assert "Failed to process sale" in response.json()["detail"]

    def test_sell_item_stale_client_sends_item_type_is_ignored(self, client, mock_shop_service):
        """Stale client sending item_type is gracefully ignored (extra fields silently dropped by Pydantic).

        A.42b contract: Pydantic does NOT raise 422 for extra fields by default
        (model_config does not set extra='forbid'). Unknown fields are silently ignored.
        This documents the chosen behavior so future agents do not inadvertently change it.
        """
        # A stale client that still sends the old item_type and target_tier fields
        payload = {
            "player_id": 1,
            "item_name": "Pulse Laser",
            "quantity": 1,
            "item_type": "weapon",  # stale field — silently ignored
            "target_tier": "Bronze",  # stale field — silently ignored
        }

        with patch("api.routers.shops.get_db_session") as mock_get_db:
            _configure_db_mock(mock_get_db)
            response = client.post("/api/v1/shops/sell", json=payload)
            # Must be 200 — stale fields are NOT rejected by the schema
            assert response.status_code == 200

    def test_sell_item_default_quantity(self, client, mock_shop_service):
        """Accepts minimal payload (player_id + item_name only) using default quantity=1."""
        payload = {
            "player_id": 1,
            "item_name": "Pulse Laser",
        }

        with patch("api.routers.shops.get_db_session") as mock_get_db:
            _configure_db_mock(mock_get_db)
            response = client.post("/api/v1/shops/sell", json=payload)
            assert response.status_code == 200

    def test_sell_item_invalid_quantity_zero_returns_422(self, client, mock_shop_service):
        """Returns 422 when quantity is 0 (must be gt=0)."""
        payload = {
            "player_id": 1,
            "item_name": "Pulse Laser",
            "quantity": 0,
        }

        response = client.post("/api/v1/shops/sell", json=payload)
        assert response.status_code == 422

    @patch("api.routers.shops.get_db_session")
    def test_sell_item_invalid_item_type_error_returns_422(self, mock_get_db, client, mock_shop_service):
        """Returns 422 when the service raises InvalidItemTypeError (e.g. cross-type collision)."""
        from services.exceptions import InvalidItemTypeError as _ITE

        _configure_db_mock(mock_get_db)
        mock_shop_service.sell_item.side_effect = _ITE("Ambiguous item 'X': found under multiple types")
        payload = {
            "player_id": 1,
            "item_name": "X",
            "quantity": 1,
        }

        response = client.post("/api/v1/shops/sell", json=payload)

        assert response.status_code == 422
        assert "Ambiguous item" in response.json()["detail"]


# ===========================================================================
# 5. POST /shops/refresh
# ===========================================================================


class TestRefreshShop:
    """Tests for POST /api/v1/shops/refresh."""

    @patch("api.routers.shops.get_db_session")
    def test_refresh_shop_happy_path(self, mock_get_db, client, mock_shop_service):
        """Returns 200 with refresh details dict."""
        _configure_db_mock(mock_get_db)
        payload = {"guild_id": 67890, "tier": "Bronze"}

        response = client.post("/api/v1/shops/refresh", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["refreshed"] is True
        assert data["items_count"] == 10

    @patch("api.routers.shops.get_db_session")
    def test_refresh_shop_with_force_tech_level(self, mock_get_db, client, mock_shop_service):
        """Accepts optional force_tech_level and passes it to service."""
        _configure_db_mock(mock_get_db)
        payload = {"guild_id": 67890, "tier": "Silver", "force_tech_level": 5}

        response = client.post("/api/v1/shops/refresh", json=payload)

        assert response.status_code == 200
        mock_shop_service.refresh_shop.assert_awaited_once()
        call_args = mock_shop_service.refresh_shop.call_args
        # force_tech_level should be 5
        assert 5 in call_args.args or call_args.kwargs.get("force_tech_level") == 5

    @patch("api.routers.shops.get_db_session")
    def test_refresh_shop_value_error_returns_400(self, mock_get_db, client, mock_shop_service):
        """Returns 400 when service raises ValueError."""
        _configure_db_mock(mock_get_db)
        mock_shop_service.refresh_shop.side_effect = ValueError("Shop already refreshed recently")
        payload = {"guild_id": 67890, "tier": "Bronze"}

        response = client.post("/api/v1/shops/refresh", json=payload)

        assert response.status_code == 400
        assert "Shop already refreshed recently" in response.json()["detail"]

    @patch("api.routers.shops.get_db_session")
    def test_refresh_shop_server_error_returns_500(self, mock_get_db, client, mock_shop_service):
        """Returns 500 when service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_shop_service.refresh_shop.side_effect = RuntimeError("Service crashed")
        payload = {"guild_id": 67890, "tier": "Bronze"}

        response = client.post("/api/v1/shops/refresh", json=payload)

        assert response.status_code == 500
        assert "Failed to refresh shop" in response.json()["detail"]

    def test_refresh_shop_invalid_tier_returns_422(self, client, mock_shop_service):
        """Returns 422 when tier does not match allowed pattern."""
        payload = {"guild_id": 67890, "tier": "Diamond"}  # not in pattern

        response = client.post("/api/v1/shops/refresh", json=payload)

        assert response.status_code == 422

    def test_refresh_shop_force_tech_level_out_of_range_returns_422(self, client, mock_shop_service):
        """Returns 422 when force_tech_level is outside 1-10."""
        payload = {"guild_id": 67890, "tier": "Bronze", "force_tech_level": 11}

        response = client.post("/api/v1/shops/refresh", json=payload)

        assert response.status_code == 422

    @patch("api.routers.shops.get_db_session")
    def test_refresh_shop_orm_items_are_serialized(self, mock_get_db, client, mock_shop_service):
        """Returns 200 with serializable body when service returns ORM-like objects in 'items'.

        Regression test for CI-10: the endpoint crashed with
        PydanticSerializationError when refresh_shop() returned a dict whose
        'items' key contained raw GuildShop ORM objects rather than plain dicts.
        """
        _configure_db_mock(mock_get_db)
        # Simulate what the real ShopService.refresh_shop() returns:
        # a dict with 'items' being a list of ORM-like objects (not plain dicts).
        orm_item = SimpleNamespace(
            id=1,
            guild_id=67890,
            tier="Bronze",
            tech_level=3,
            item_type="weapon",
            item_name="Pulse Laser",
            quantity=5,
            price=200,
            last_restocked=datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC),
            refresh_interval_hours=12,
            # Enrichment fields absent on real GuildShop ORM object (no attr → None)
        )
        mock_shop_service.refresh_shop = AsyncMock(
            return_value={
                "guild_id": 67890,
                "tier": "Bronze",
                "tech_level": 3,
                "items_generated": 1,
                "items": [orm_item],  # ORM-like object — would cause PydanticSerializationError without the fix
                "refresh_time": "2026-01-15T12:00:00+00:00",
            }
        )
        payload = {"guild_id": 67890, "tier": "Bronze"}

        response = client.post("/api/v1/shops/refresh", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890
        assert data["tier"] == "Bronze"
        assert data["items_generated"] == 1
        items = data["items"]
        assert isinstance(items, list)
        assert len(items) == 1
        item = items[0]
        assert item["item_name"] == "Pulse Laser"
        assert item["price"] == 200
        # Timezone-aware datetime serializes with +00:00 offset
        assert item["last_restocked"] == "2026-01-15T12:00:00+00:00"
        # ShopItemResponse shape: optional enrichment fields present as null
        assert "emoji" in item
        assert "dps" in item
        assert "shield" in item
        assert "armour" in item
        assert "hull_hp" in item
        assert item["emoji"] is None
        assert item["dps"] is None


# ===========================================================================
# 6. GET /shops/guild/{guild_id}/tier/{tier}/stats
# ===========================================================================


class TestGetShopStatistics:
    """Tests for GET /api/v1/shops/guild/{guild_id}/tier/{tier}/stats."""

    @patch("api.routers.shops.get_db_session")
    def test_get_shop_statistics_happy_path(self, mock_get_db, client, mock_shop_service):
        """Returns 200 with statistics dict."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/shops/guild/67890/tier/Bronze/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["total_items"] == 10
        assert data["avg_price"] == 150

    @patch("api.routers.shops.get_db_session")
    def test_get_shop_statistics_server_error_returns_500(self, mock_get_db, client, mock_shop_service):
        """Returns 500 when repo raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_shop_service.shop_repo.get_shop_statistics.side_effect = RuntimeError("Query failed")

        response = client.get("/api/v1/shops/guild/67890/tier/Bronze/stats")

        assert response.status_code == 500
        assert "Failed to get shop statistics" in response.json()["detail"]


# ===========================================================================
# 7. GET /shops/guild/{guild_id}/tier/{tier}/tech-level/{tech_level}
# ===========================================================================


class TestGetItemsByTechLevel:
    """Tests for GET /api/v1/shops/guild/{guild_id}/tier/{tier}/tech-level/{tech_level}."""

    @patch("api.routers.shops.get_db_session")
    def test_get_items_by_tech_level_happy_path(self, mock_get_db, client, mock_shop_service):
        """Returns 200 with list of items at given tech level."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/shops/guild/67890/tier/Bronze/tech-level/1")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["tech_level"] == 1

    @patch("api.routers.shops.get_db_session")
    def test_get_items_by_tech_level_boundary_min(self, mock_get_db, client, mock_shop_service):
        """Returns 200 when tech_level is 1 (minimum valid)."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/shops/guild/67890/tier/Gold/tech-level/1")

        assert response.status_code == 200

    @patch("api.routers.shops.get_db_session")
    def test_get_items_by_tech_level_boundary_max(self, mock_get_db, client, mock_shop_service):
        """Returns 200 when tech_level is 9 (maximum valid)."""
        _configure_db_mock(mock_get_db)
        mock_shop_service.shop_repo.get_items_by_tech_level = AsyncMock(
            return_value=[make_mock_shop_item(tech_level=9)]
        )

        response = client.get("/api/v1/shops/guild/67890/tier/Gold/tech-level/9")

        assert response.status_code == 200
        data = response.json()
        assert data[0]["tech_level"] == 9

    def test_get_items_by_tech_level_zero_returns_400(self, client, mock_shop_service):
        """Returns 400 when tech_level is 0 (below minimum)."""
        with patch("api.routers.shops.get_db_session") as mock_get_db:
            _configure_db_mock(mock_get_db)
            response = client.get("/api/v1/shops/guild/67890/tier/Bronze/tech-level/0")

        assert response.status_code == 400
        assert "Tech level must be between 1 and 10" in response.json()["detail"]

    def test_get_items_by_tech_level_eleven_returns_400(self, client, mock_shop_service):
        """Returns 400 when tech_level is 11 (above new maximum of 10)."""
        with patch("api.routers.shops.get_db_session") as mock_get_db:
            _configure_db_mock(mock_get_db)
            response = client.get("/api/v1/shops/guild/67890/tier/Bronze/tech-level/11")

        assert response.status_code == 400
        assert "Tech level must be between 1 and 10" in response.json()["detail"]

    @patch("api.routers.shops.get_db_session")
    def test_get_items_by_tech_level_server_error_returns_500(self, mock_get_db, client, mock_shop_service):
        """Returns 500 when repo raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_shop_service.shop_repo.get_items_by_tech_level.side_effect = RuntimeError("Connection lost")

        response = client.get("/api/v1/shops/guild/67890/tier/Bronze/tech-level/3")

        assert response.status_code == 500
        assert "Failed to get items by tech level" in response.json()["detail"]


# ===========================================================================
# 8. GET /shops/guild/{guild_id}/refresh-status
# ===========================================================================


class TestGetRefreshStatus:
    """Tests for GET /api/v1/shops/guild/{guild_id}/refresh-status."""

    @patch("api.routers.shops.get_db_session")
    def test_get_refresh_status_no_items_due(self, mock_get_db, client, mock_shop_service):
        """Returns 200 with needs_refresh=False when no items are due."""
        _configure_db_mock(mock_get_db)
        mock_shop_service.shop_repo.get_items_due_for_refresh = AsyncMock(return_value=[])

        response = client.get("/api/v1/shops/guild/67890/refresh-status")

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890
        assert data["total_items_due_for_refresh"] == 0
        assert data["due_by_tier"] == {}
        assert data["needs_refresh"] is False

    @patch("api.routers.shops.get_db_session")
    def test_get_refresh_status_with_items_due(self, mock_get_db, client, mock_shop_service):
        """Returns 200 with needs_refresh=True and counts grouped by tier."""
        _configure_db_mock(mock_get_db)
        due_items = [
            make_mock_shop_item(tier="Bronze"),
            make_mock_shop_item(tier="Bronze", id=2),
            make_mock_shop_item(tier="Silver", id=3),
        ]
        mock_shop_service.shop_repo.get_items_due_for_refresh = AsyncMock(return_value=due_items)

        response = client.get("/api/v1/shops/guild/67890/refresh-status")

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890
        assert data["total_items_due_for_refresh"] == 3
        assert data["due_by_tier"]["Bronze"] == 2
        assert data["due_by_tier"]["Silver"] == 1
        assert data["needs_refresh"] is True

    @patch("api.routers.shops.get_db_session")
    def test_get_refresh_status_server_error_returns_500(self, mock_get_db, client, mock_shop_service):
        """Returns 500 when repo raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_shop_service.shop_repo.get_items_due_for_refresh.side_effect = RuntimeError("Repo failed")

        response = client.get("/api/v1/shops/guild/67890/refresh-status")

        assert response.status_code == 500
        assert "Failed to get refresh status" in response.json()["detail"]


# ===========================================================================
# 9. GET /shops/item/{shop_item_id}
# ===========================================================================


class TestGetShopItem:
    """Tests for GET /api/v1/shops/item/{shop_item_id}."""

    @patch("api.routers.shops.get_db_session")
    def test_get_shop_item_happy_path(self, mock_get_db, client, mock_shop_service):
        """Returns 200 with ShopItemResponse when item exists."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/shops/item/1")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 1
        assert data["guild_id"] == 67890
        assert data["tier"] == "Bronze"
        assert data["item_name"] == "Pulse Laser"
        assert data["price"] == 100

    @patch("api.routers.shops.get_db_session")
    def test_get_shop_item_not_found_returns_404(self, mock_get_db, client, mock_shop_service):
        """Returns 404 when shop item does not exist (repo returns None)."""
        _configure_db_mock(mock_get_db)
        mock_shop_service.shop_repo.get_by_id = AsyncMock(return_value=None)

        response = client.get("/api/v1/shops/item/9999")

        assert response.status_code == 404
        assert "9999" in response.json()["detail"]

    @patch("api.routers.shops.get_db_session")
    def test_get_shop_item_server_error_returns_500(self, mock_get_db, client, mock_shop_service):
        """Returns 500 when repo raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_shop_service.shop_repo.get_by_id.side_effect = RuntimeError("DB unavailable")

        response = client.get("/api/v1/shops/item/1")

        assert response.status_code == 500
        assert "Failed to get shop item" in response.json()["detail"]


# ===========================================================================
# 10. PUT /shops/guild/{guild_id}/prices
# ===========================================================================


class TestUpdateShopPrices:
    """Tests for PUT /api/v1/shops/guild/{guild_id}/prices."""

    @patch("api.routers.shops.get_db_session")
    def test_update_shop_prices_happy_path(self, mock_get_db, client, mock_shop_service):
        """Returns 200 with updated count when multiplier is valid."""
        _configure_db_mock(mock_get_db)

        response = client.put("/api/v1/shops/guild/67890/prices?price_multiplier=1.1")

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890
        assert data["price_multiplier"] == pytest.approx(1.1)
        assert data["items_updated"] == 10
        assert "Updated prices for 10 shop items" in data["message"]

    @patch("api.routers.shops.get_db_session")
    def test_update_shop_prices_multiplier_one(self, mock_get_db, client, mock_shop_service):
        """Accepts multiplier=1.0 (no change, but still valid)."""
        _configure_db_mock(mock_get_db)

        response = client.put("/api/v1/shops/guild/67890/prices?price_multiplier=1.0")

        assert response.status_code == 200

    @patch("api.routers.shops.get_db_session")
    def test_update_shop_prices_value_error_returns_400(self, mock_get_db, client, mock_shop_service):
        """Returns 400 when repo raises ValueError."""
        _configure_db_mock(mock_get_db)
        mock_shop_service.shop_repo.update_prices.side_effect = ValueError("Price multiplier too large")

        response = client.put("/api/v1/shops/guild/67890/prices?price_multiplier=1000.0")

        assert response.status_code == 400
        assert "Price multiplier too large" in response.json()["detail"]

    @patch("api.routers.shops.get_db_session")
    def test_update_shop_prices_server_error_returns_500(self, mock_get_db, client, mock_shop_service):
        """Returns 500 when repo raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_shop_service.shop_repo.update_prices.side_effect = RuntimeError("Transaction failed")

        response = client.put("/api/v1/shops/guild/67890/prices?price_multiplier=1.5")

        assert response.status_code == 500
        assert "Failed to update shop prices" in response.json()["detail"]

    def test_update_shop_prices_missing_multiplier_returns_422(self, client, mock_shop_service):
        """Returns 422 when price_multiplier query param is missing."""
        response = client.put("/api/v1/shops/guild/67890/prices")

        assert response.status_code == 422

    def test_update_shop_prices_zero_multiplier_returns_422(self, client, mock_shop_service):
        """Returns 422 when price_multiplier=0 (must be gt=0)."""
        response = client.put("/api/v1/shops/guild/67890/prices?price_multiplier=0")

        assert response.status_code == 422

    def test_update_shop_prices_negative_multiplier_returns_422(self, client, mock_shop_service):
        """Returns 422 when price_multiplier is negative (must be gt=0)."""
        response = client.put("/api/v1/shops/guild/67890/prices?price_multiplier=-0.5")

        assert response.status_code == 422


# ===========================================================================
# 9. POST /shops/purchase-ship
# ===========================================================================


class TestPurchaseShip:
    """Tests for POST /api/v1/shops/purchase-ship."""

    @patch("api.routers.shops.get_db_session")
    def test_purchase_ship_happy_path(self, mock_get_db, client, mock_shop_service):
        """Returns 200 with transaction details for a valid ship purchase."""
        _configure_db_mock(mock_get_db)

        response = client.post(
            "/api/v1/shops/purchase-ship",
            json={"player_id": 1, "shop_item_id": 10},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["player_id"] == 1
        assert data["item_type"] == "ship"
        assert data["item_name"] == "Hammerhead"
        assert data["transaction_type"] == "ship_purchase"
        assert data["remaining_credits"] == 5000

    @patch("api.routers.shops.get_db_session")
    def test_purchase_ship_insufficient_credits_returns_400(self, mock_get_db, client, mock_shop_service):
        """Returns 400 when player cannot afford the ship."""
        _configure_db_mock(mock_get_db)
        mock_shop_service.purchase_ship.side_effect = ValueError("Insufficient credits. Cost: 5000, Available: 100")

        response = client.post(
            "/api/v1/shops/purchase-ship",
            json={"player_id": 1, "shop_item_id": 10},
        )

        assert response.status_code == 400
        assert "Insufficient credits" in response.json()["detail"]

    @patch("api.routers.shops.get_db_session")
    def test_purchase_ship_not_a_ship_returns_400(self, mock_get_db, client, mock_shop_service):
        """Returns 400 when shop item is not a ship."""
        _configure_db_mock(mock_get_db)
        mock_shop_service.purchase_ship.side_effect = ValueError("Shop item 10 is not a ship (type=weapon)")

        response = client.post(
            "/api/v1/shops/purchase-ship",
            json={"player_id": 1, "shop_item_id": 10},
        )

        assert response.status_code == 400
        assert "not a ship" in response.json()["detail"]


# ===========================================================================
# Gap 1: Empty-State / Null-Result Tests — Shops
# ===========================================================================


class TestGetGuildShopEmpty:
    """Gap 1: Shop endpoints for a guild with no items → proper empty/error responses."""

    @patch("api.routers.shops.get_db_session")
    def test_get_guild_shop_empty_returns_200_empty_list(self, mock_get_db, client, mock_shop_service):
        """GET /shops/guild/{id}/tier/Bronze with no shop items → 200 + empty list.

        Verifies that a guild with an empty shop does not produce a 500 error.
        The endpoint should return an empty JSON array gracefully.
        """
        _configure_db_mock(mock_get_db)
        mock_shop_service.get_shop_items = AsyncMock(return_value=[])

        response = client.get("/api/v1/shops/guild/99999/tier/Bronze")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert data == []

    @patch("api.routers.shops.get_db_session")
    def test_buy_item_shop_empty_returns_proper_error(self, mock_get_db, client, mock_shop_service):
        """POST /shops/purchase when the shop is empty → 400 (not 500).

        Buying from a shop with no items should raise a ValueError (e.g. item not found)
        which maps to 400, not a 500 from an unhandled exception.
        """
        _configure_db_mock(mock_get_db)
        mock_shop_service.purchase_item = AsyncMock(side_effect=ValueError("Shop item 9999 not found in guild shop"))
        payload = {"player_id": 1, "shop_item_id": 9999, "quantity": 1}

        response = client.post("/api/v1/shops/purchase", json=payload)

        # Must be a client error (400), not a server error (500)
        assert response.status_code == 400
        assert "not found" in response.json()["detail"].lower() or "shop" in response.json()["detail"].lower()


# ===========================================================================
# Emoji field tests for ShopItemResponse
# ===========================================================================


def _configure_db_mock_with_emoji(mock_get_db, emoji_rows):
    """Configure mock_get_db to act as an async context manager with emoji query support.

    Args:
        mock_get_db: The mock for get_db_session
        emoji_rows: List of (name, emoji) tuples to return from the emoji query
    """
    mock_session = AsyncMock()

    # Mock the emoji query result — db.execute returns an iterable of row-like objects.
    # Use SimpleNamespace so that row.name and row.emoji work correctly.
    # NOTE: MagicMock(name=...) sets the mock's display name, not an attribute.
    rows = [SimpleNamespace(name=n, emoji=e) for n, e in emoji_rows]
    mock_emoji_result = MagicMock()
    mock_emoji_result.__iter__ = MagicMock(return_value=iter(rows))
    mock_session.execute = AsyncMock(return_value=mock_emoji_result)

    mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_session


class TestEmojiInShopItemResponses:
    """Tests verifying that emoji is looked up and returned in ShopItemResponse."""

    @patch("api.routers.shops.get_db_session")
    def test_get_shop_items_returns_emoji_when_found(self, mock_get_db, client, mock_shop_service):
        """GET /shops/guild/{id}/tier/{tier} returns emoji when Item table has a match.

        Acceptance criteria: emoji field is populated from Item.emoji when the item
        name matches a record in the Item table.
        """
        _configure_db_mock_with_emoji(mock_get_db, [("Pulse Laser", "🔫")])

        response = client.get("/api/v1/shops/guild/67890/tier/Bronze")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["emoji"] == "🔫"

    @patch("api.routers.shops.get_db_session")
    def test_get_shop_items_returns_null_emoji_when_not_found(self, mock_get_db, client, mock_shop_service):
        """GET /shops/guild/{id}/tier/{tier} returns emoji=null when no Item record exists.

        Acceptance criteria: emoji is None when item_name has no corresponding Item row.
        """
        _configure_db_mock_with_emoji(mock_get_db, [])  # No matching items in Item table

        response = client.get("/api/v1/shops/guild/67890/tier/Bronze")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["emoji"] is None

    @patch("api.routers.shops.get_db_session")
    def test_get_shop_items_returns_null_emoji_when_item_emoji_is_none(self, mock_get_db, client, mock_shop_service):
        """GET /shops/guild/{id}/tier/{tier} returns emoji=null when Item.emoji is null.

        Acceptance criteria: emoji=None propagates when Item record exists but emoji column is null.
        """
        _configure_db_mock_with_emoji(mock_get_db, [("Pulse Laser", None)])

        response = client.get("/api/v1/shops/guild/67890/tier/Bronze")

        assert response.status_code == 200
        data = response.json()
        assert data[0]["emoji"] is None

    @patch("api.routers.shops.get_db_session")
    def test_get_shop_items_empty_shop_skips_emoji_query(self, mock_get_db, client, mock_shop_service):
        """GET /shops/guild/{id}/tier/{tier} with empty shop skips emoji DB query.

        Acceptance criteria: when the shop has no items, the emoji query is never executed
        (no item_names means no DB round-trip).
        """
        mock_shop_service.get_shop_items = AsyncMock(return_value=[])
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/api/v1/shops/guild/67890/tier/Bronze")

        assert response.status_code == 200
        assert response.json() == []
        # The emoji query should NOT have been called since there are no items
        mock_session.execute.assert_not_called()

    @patch("api.routers.shops.get_db_session")
    def test_get_items_by_tech_level_returns_emoji_when_found(self, mock_get_db, client, mock_shop_service):
        """GET /shops/guild/{id}/tier/{tier}/tech-level/{n} returns emoji from Item table.

        Acceptance criteria: the tech-level endpoint also looks up and returns emoji.
        """
        _configure_db_mock_with_emoji(mock_get_db, [("Pulse Laser", "⚡")])

        response = client.get("/api/v1/shops/guild/67890/tier/Bronze/tech-level/1")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["emoji"] == "⚡"

    @patch("api.routers.shops.get_db_session")
    def test_get_shop_item_by_id_returns_emoji_when_found(self, mock_get_db, client, mock_shop_service):
        """GET /shops/item/{id} returns emoji from Item table.

        Acceptance criteria: single item endpoint also looks up and returns emoji.
        """
        # The single-item endpoint uses the same iteration pattern as list endpoints.
        # Use _configure_db_mock_with_emoji since Pulse Laser is the default shop item name.
        _configure_db_mock_with_emoji(mock_get_db, [("Pulse Laser", "🚀")])

        response = client.get("/api/v1/shops/item/1")

        assert response.status_code == 200
        data = response.json()
        assert data["emoji"] == "🚀"

    @patch("api.routers.shops.get_db_session")
    def test_get_shop_item_by_id_returns_null_emoji_when_no_item_record(self, mock_get_db, client, mock_shop_service):
        """GET /shops/item/{id} returns emoji=null when no Item record exists.

        Acceptance criteria: emoji is None when the item name has no matching Item row.
        """
        # Empty emoji_rows → no rows in Item table → emoji_map.get returns None
        _configure_db_mock_with_emoji(mock_get_db, [])

        response = client.get("/api/v1/shops/item/1")

        assert response.status_code == 200
        data = response.json()
        assert data["emoji"] is None

    @patch("api.routers.shops.get_db_session")
    def test_shop_item_response_schema_includes_emoji_field(self, mock_get_db, client, mock_shop_service):
        """ShopItemResponse schema includes emoji field with None default.

        Acceptance criteria: emoji field exists in the response JSON structure.
        """
        _configure_db_mock_with_emoji(mock_get_db, [("Pulse Laser", "🔫")])

        response = client.get("/api/v1/shops/guild/67890/tier/Bronze")

        assert response.status_code == 200
        data = response.json()
        # Verify emoji key exists in the response
        assert "emoji" in data[0]
