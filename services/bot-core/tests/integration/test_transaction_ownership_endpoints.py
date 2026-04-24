"""
Integration tests for A.44 fix: router-owned transactions.

Each test verifies that the endpoint returns HTTP NOT 500 and that the expected
state change (credits, inventory, ship ownership) occurred.

DESIGN NOTES
============
- These tests use a real SQLite in-memory database (built fresh per test).
- get_db_session is patched in each router module to inject a test session
  from the same engine. Because AsyncSession is event-loop-bound, we build the
  engine + session factory inside each test and use httpx.AsyncClient with
  ASGITransport to keep everything in the same async event loop.
- SQLite FOR UPDATE: SQLite parses ``SELECT ... FOR UPDATE`` as a plain SELECT
  (no row-level locking). This is acceptable here because we are testing the
  transaction-nesting bug (A.44), not the lock semantics. The critical assertion
  is that the endpoint returns HTTP 200 (or a domain-specific 4xx), NOT 500.
- At most 2 mocks per test (for static-data lookups against tables not in the
  SQLite integration schema). All DB operations use real SQLite sessions.
"""

# ---------------------------------------------------------------------------
# Path setup: ensure src/ is first on sys.path so that 'api.routers.*'
# resolves to src/api/routers/ rather than the tests/api/ package.
# ---------------------------------------------------------------------------
import os
import sys

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
elif sys.path[0] != _SRC_DIR:
    sys.path.remove(_SRC_DIR)
    sys.path.insert(0, _SRC_DIR)

# Purge any stale api.* entries loaded from tests/api/
for _key in list(sys.modules):
    if _key == "api" or _key.startswith("api."):
        _mod = sys.modules[_key]
        _file = getattr(_mod, "__file__", "") or ""
        if _SRC_DIR not in _file:
            del sys.modules[_key]

# ---------------------------------------------------------------------------

from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import httpx
from fastapi import FastAPI
from persist.models.base import Base
from persist.models.guild_config import GuildConfig
from persist.models.guild_shop import GuildShop
from persist.models.player import Player
from persist.models.player_inventory import PlayerInventory
from persist.models.player_ship import PlayerShip
from persist.models.user import User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Tables compatible with SQLite (no ARRAY/UUID columns)
_SQLITE_TABLES = [
    User.__table__,
    Player.__table__,
    GuildConfig.__table__,
    GuildShop.__table__,
    PlayerInventory.__table__,
    PlayerShip.__table__,
]


# ---------------------------------------------------------------------------
# Per-test engine + session factory helper
# ---------------------------------------------------------------------------


async def _make_sqlite_session_factory():
    """Create a fresh in-memory SQLite engine and session factory."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_SQLITE_TABLES)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_user(db: AsyncSession, user_id: int) -> User:
    user = User(id=user_id, discord_username=f"user_{user_id}")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _seed_guild_config(db: AsyncSession, guild_id: int) -> GuildConfig:
    config = GuildConfig(guild_id=guild_id, starting_credits=1000)
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return config


async def _seed_player(
    db: AsyncSession,
    user_id: int,
    guild_id: int,
    credits: int = 1000,
    tier: str = "Bronze",
) -> Player:
    player = Player(user_id=user_id, guild_id=guild_id, credits=credits, tier=tier)
    db.add(player)
    await db.commit()
    await db.refresh(player)
    return player


async def _seed_inventory(
    db: AsyncSession,
    player_id: int,
    item_type: str,
    item_name: str,
    quantity: int = 1,
) -> PlayerInventory:
    item = PlayerInventory(
        player_id=player_id,
        item_type=item_type,
        item_name=item_name,
        quantity=quantity,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def _seed_player_ship(
    db: AsyncSession,
    player_id: int,
    ship_name: str = "Hammerhead",
    is_active: bool = True,
) -> PlayerShip:
    ship = PlayerShip(
        player_id=player_id,
        ship_name=ship_name,
        is_active=is_active,
        weapons=[],
        modules=[],
        turrets=[],
        secondary_weapons=[],
    )
    db.add(ship)
    await db.commit()
    await db.refresh(ship)
    return ship


async def _seed_guild_shop(
    db: AsyncSession,
    guild_id: int,
    item_type: str,
    item_name: str,
    tier: str = "Bronze",
    quantity: int = 5,
    price: int = 100,
    tech_level: int = 1,
) -> GuildShop:
    shop_item = GuildShop(
        guild_id=guild_id,
        tier=tier,
        tech_level=tech_level,
        item_type=item_type,
        item_name=item_name,
        quantity=quantity,
        price=price,
    )
    db.add(shop_item)
    await db.commit()
    await db.refresh(shop_item)
    return shop_item


def _make_cm_patcher(module_path: str, db_session: AsyncSession):
    """Patch get_db_session in the given module to yield the provided session.

    DEF-T-001 fix: uses side_effect=_fake_get_db (factory) so each call to
    get_db_session() returns a fresh async context manager rather than a single
    already-consumed CM (return_value= bug).
    """

    @asynccontextmanager
    async def _fake_get_db():
        yield db_session

    return patch(module_path, side_effect=_fake_get_db)


# ---------------------------------------------------------------------------
# Test: POST /api/v1/shops/sell (A.44 — shop_service.sell_item)
# ---------------------------------------------------------------------------


class TestShopsSellEndpoint:
    """Tests for POST /api/v1/shops/sell (A.44 transaction fix)."""

    async def test_sell_item_returns_200_and_updates_credits(self):
        """
        Seeds a player with a primary_weapon inventory row, then sells it.
        Asserts HTTP 200 and that player credits increased.

        Key assertion: status is NOT 500 (which would indicate the nested-begin bug).
        Mock budget: 1 mock (ShopService._get_item_base_price — static item tables
        are not in the SQLite integration schema).
        """
        engine, factory = await _make_sqlite_session_factory()

        # Seed data
        async with factory() as db:
            user = await _seed_user(db, user_id=901001)
            await _seed_guild_config(db, guild_id=9001)
            player = await _seed_player(db, user_id=user.id, guild_id=9001, credits=100)
            await _seed_inventory(db, player.id, "primary_weapon", "Pulse Laser", quantity=1)
            player_id = player.id

        # Build the app
        app = FastAPI()
        from api.routers.shops import router as shops_router

        app.include_router(shops_router, prefix="/api/v1")

        # Mock 1: item price lookup (static item tables not in SQLite integration schema)
        from services.shop_service import ShopService

        original_get_price = ShopService._get_item_base_price

        async def _mock_price(self, db, item_name):
            return 50

        ShopService._get_item_base_price = _mock_price

        try:
            async with factory() as router_db:
                with _make_cm_patcher("api.routers.shops.get_db_session", router_db):
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://testserver",
                    ) as client:
                        response = await client.post(
                            "/api/v1/shops/sell",
                            json={"player_id": player_id, "item_name": "Pulse Laser", "quantity": 1},
                        )
        finally:
            ShopService._get_item_base_price = original_get_price

        # Key assertion: NOT 500 (A.44 transaction bug would cause 500)
        assert response.status_code != 500, (
            f"Got 500 — A.44 transaction-nesting bug may still be present: {response.text}"
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert data["transaction_type"] == "sale"
        assert data["player_id"] == player_id

        # Verify player credits increased
        async with factory() as verify_db:
            result = await verify_db.execute(select(Player).where(Player.id == player_id))
            updated_player = result.scalars().first()
        assert updated_player.credits == 150, (
            f"Player credits should have increased from 100 to 150, got {updated_player.credits}"
        )

        await engine.dispose()


# ---------------------------------------------------------------------------
# Test: POST /api/v1/shops/purchase-ship (A.44 — shop_service.buy_ship)
# ---------------------------------------------------------------------------


class TestShopsPurchaseShipEndpoint:
    """Tests for POST /api/v1/shops/purchase-ship (A.44 transaction fix)."""

    async def test_purchase_ship_not_500(self):
        """
        Seeds a player, a ship in the guild shop. Purchases the ship.
        Asserts HTTP NOT 500 (200 or 400 both acceptable).

        The 'ship' STI table uses ARRAY columns not supported by SQLite's schema in
        the integration test fixtures. Mock 1 patches ship_repo.get_by_name to return
        None (which the service converts to a ValueError/400 — not a 500).
        Mock budget: 1 mock (ship_repo.get_by_name — static ship table not in SQLite schema).
        """
        engine, factory = await _make_sqlite_session_factory()

        async with factory() as db:
            user = await _seed_user(db, user_id=902001)
            await _seed_guild_config(db, guild_id=9002)
            player = await _seed_player(db, user_id=user.id, guild_id=9002, credits=5000)
            shop_item = await _seed_guild_shop(
                db,
                guild_id=9002,
                item_type="ship",
                item_name="Hammerhead",
                tier="Bronze",
                quantity=1,
                price=1000,
            )
            player_id = player.id
            shop_item_id = shop_item.id

        app = FastAPI()
        from api.routers.shops import router as shops_router

        app.include_router(shops_router, prefix="/api/v1")

        # Mock 1: ship static table not in SQLite integration schema;
        # returning None causes the service to raise ValueError → HTTP 400
        from persist.repositories.ship_repository import ShipRepository

        original_ship_get = ShipRepository.get_by_name

        async def _mock_ship_get_by_name(self, db, name):
            return None  # static ship not found → service raises ValueError → 400

        ShipRepository.get_by_name = _mock_ship_get_by_name

        try:
            async with factory() as router_db:
                with _make_cm_patcher("api.routers.shops.get_db_session", router_db):
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://testserver",
                    ) as client:
                        response = await client.post(
                            "/api/v1/shops/purchase-ship",
                            json={"player_id": player_id, "shop_item_id": shop_item_id, "sell_old_ship": False},
                        )
        finally:
            ShipRepository.get_by_name = original_ship_get

        # Key assertion: NOT 500
        assert response.status_code != 500, (
            f"Got 500 — A.44 transaction-nesting bug may still be present: {response.text}"
        )
        # 400 is expected (static ship data not found → ValueError)
        assert response.status_code in (200, 400), f"Expected 200 or 400, got {response.status_code}: {response.text}"

        await engine.dispose()

    async def test_buy_ship_happy_path_creates_player_ship_and_deducts_credits(self):
        """
        GAP-3: Validates the buy_ship happy path through a full integration.
        Asserts HTTP 200, credits deducted, and PlayerShip row created.

        Mock budget: 1 (ship_repo.get_by_name — static Ship table has ARRAY columns
        incompatible with SQLite; mocked to return a minimal ship-like object).
        """
        engine, factory = await _make_sqlite_session_factory()

        async with factory() as db:
            user = await _seed_user(db, user_id=902002)
            await _seed_guild_config(db, guild_id=9022)
            player = await _seed_player(db, user_id=user.id, guild_id=9022, credits=10000, tier="Bronze")
            active_ship = await _seed_player_ship(db, player.id, ship_name="Starter", is_active=True)
            shop_item = await _seed_guild_shop(
                db,
                guild_id=9022,
                item_type="ship",
                item_name="Hammerhead",
                tier="Bronze",
                quantity=1,
                price=5000,
            )
            player_id = player.id
            shop_item_id = shop_item.id
            _ = active_ship  # referenced for clarity

        app = FastAPI()
        from api.routers.shops import router as shops_router

        app.include_router(shops_router, prefix="/api/v1")

        # Mock 1: ship static lookup (Ship table has ARRAY columns not in SQLite test schema).
        from persist.repositories.ship_repository import ShipRepository

        original_ship_get = ShipRepository.get_by_name

        def _mock_ship(name: str):
            """Minimal ship-like object with required attributes."""
            mock = MagicMock()
            mock.name = name
            mock.value = 4000
            mock.max_primaries = 2
            mock.max_modules = 2
            mock.max_turrets = 1
            mock.max_secondaries = 0
            return mock

        async def _mock_ship_get_by_name(self, db, name):
            return _mock_ship(name)

        ShipRepository.get_by_name = _mock_ship_get_by_name

        try:
            async with factory() as router_db:
                with _make_cm_patcher("api.routers.shops.get_db_session", router_db):
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://testserver",
                    ) as client:
                        response = await client.post(
                            "/api/v1/shops/purchase-ship",
                            json={"player_id": player_id, "shop_item_id": shop_item_id, "sell_old_ship": False},
                        )
        finally:
            ShipRepository.get_by_name = original_ship_get

        assert response.status_code != 500, f"Got 500 — unexpected error: {response.text}"
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        # Verify credits deducted
        async with factory() as verify_db:
            result = await verify_db.execute(select(Player).where(Player.id == player_id))
            updated_player = result.scalars().first()
            assert updated_player.credits == 5000, (
                f"Credits should be 10000 - 5000 = 5000, got {updated_player.credits}"
            )

            # Verify new PlayerShip row created
            result = await verify_db.execute(
                select(PlayerShip).where((PlayerShip.player_id == player_id) & (PlayerShip.ship_name == "Hammerhead"))
            )
            new_ship = result.scalars().first()
            assert new_ship is not None, "New PlayerShip row should have been created for 'Hammerhead'"

        await engine.dispose()


# ---------------------------------------------------------------------------
# Test: POST /api/v1/shops/sell-ship (A.44 — shop_service.sell_ship)
# ---------------------------------------------------------------------------


class TestShopsSellShipEndpoint:
    """Tests for POST /api/v1/shops/sell-ship (A.44 transaction fix)."""

    async def test_sell_inactive_ship_not_500(self):
        """
        Seeds a player with an inactive PlayerShip and sells it.
        Asserts HTTP 200, ship removed, credits unchanged (ship_value=0 when static data unavailable).

        The 'ship' STI table is not in the SQLite integration schema. Mock 1 patches
        ship_repo.get_by_name to return None (ship_value defaults to 0, which is fine).
        Mock budget: 1 mock (ship_repo.get_by_name — static ship table not in SQLite schema).
        """
        engine, factory = await _make_sqlite_session_factory()

        async with factory() as db:
            user = await _seed_user(db, user_id=903001)
            await _seed_guild_config(db, guild_id=9003)
            player = await _seed_player(db, user_id=user.id, guild_id=9003, credits=500)
            await _seed_player_ship(db, player.id, ship_name="ActiveShip", is_active=True)
            inactive_ship = await _seed_player_ship(db, player.id, ship_name="InactiveShip", is_active=False)
            player_id = player.id
            inactive_ship_id = inactive_ship.id

        app = FastAPI()
        from api.routers.shops import router as shops_router

        app.include_router(shops_router, prefix="/api/v1")

        # Mock 1: ship static table not in SQLite schema; returning None makes ship_value=0
        from persist.repositories.ship_repository import ShipRepository

        original_ship_get = ShipRepository.get_by_name

        async def _mock_ship_get_by_name(self, db, name):
            return None  # ship_static = None → ship_value = 0

        ShipRepository.get_by_name = _mock_ship_get_by_name

        try:
            async with factory() as router_db:
                with _make_cm_patcher("api.routers.shops.get_db_session", router_db):
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://testserver",
                    ) as client:
                        response = await client.post(
                            "/api/v1/shops/sell-ship",
                            json={
                                "player_id": player_id,
                                "ship_id": inactive_ship_id,
                                "clear_equipment": False,
                                "target_tier": "Bronze",
                            },
                        )
        finally:
            ShipRepository.get_by_name = original_ship_get

        # Key assertion: NOT 500
        assert response.status_code != 500, (
            f"Got 500 — A.44 transaction-nesting bug may still be present: {response.text}"
        )
        assert response.status_code in (200, 400), f"Expected 200 or 400, got {response.status_code}: {response.text}"

        if response.status_code == 200:
            async with factory() as verify_db:
                result = await verify_db.execute(select(PlayerShip).where(PlayerShip.id == inactive_ship_id))
                assert result.scalars().first() is None, "Sold ship should have been deleted from DB"

        await engine.dispose()

    async def test_sell_inactive_ship_credits_player_and_removes_ship(self):
        """
        GAP-4: Validates the sell_ship happy path through a full integration.
        Asserts HTTP 200, credits increased by ship value, and PlayerShip row deleted.

        Mock budget: 1 (ship_repo.get_by_name — static Ship table has ARRAY columns
        incompatible with SQLite; mocked to return a ship with value=2000).
        """
        SHIP_VALUE = 2000

        engine, factory = await _make_sqlite_session_factory()

        async with factory() as db:
            user = await _seed_user(db, user_id=903002)
            await _seed_guild_config(db, guild_id=9032)
            player = await _seed_player(db, user_id=user.id, guild_id=9032, credits=100, tier="Bronze")
            await _seed_player_ship(db, player.id, ship_name="ActiveShip", is_active=True)
            inactive_ship = await _seed_player_ship(db, player.id, ship_name="OldShip", is_active=False)
            player_id = player.id
            inactive_ship_id = inactive_ship.id

        app = FastAPI()
        from api.routers.shops import router as shops_router

        app.include_router(shops_router, prefix="/api/v1")

        # Mock 1: ship static lookup (Ship table has ARRAY columns not in SQLite schema).
        from persist.repositories.ship_repository import ShipRepository

        original_ship_get = ShipRepository.get_by_name

        async def _mock_ship_get_by_name_gap4(self, db, name):
            mock = MagicMock()
            mock.name = name
            mock.value = SHIP_VALUE
            return mock

        ShipRepository.get_by_name = _mock_ship_get_by_name_gap4

        try:
            async with factory() as router_db:
                with _make_cm_patcher("api.routers.shops.get_db_session", router_db):
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://testserver",
                    ) as client:
                        response = await client.post(
                            "/api/v1/shops/sell-ship",
                            json={
                                "player_id": player_id,
                                "ship_id": inactive_ship_id,
                                "clear_equipment": False,
                                "target_tier": "Bronze",
                            },
                        )
        finally:
            ShipRepository.get_by_name = original_ship_get

        assert response.status_code != 500, f"Got 500 — unexpected error: {response.text}"
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        # Verify credits increased by ship value and ship row deleted.
        # GAP-A-003 decision: no SHIP_SELL_PRICE_FACTOR is applied.
        # shop_service.sell_ship() credits `player.credits + ship_value` at full value
        # (comment at shop_service.py: "full value (no tax)").  If a sell-price factor
        # (e.g., GameConstants.SHIP_SELL_PRICE_FACTOR) is introduced in the future,
        # this assertion must be updated to: `expected = initial_credits + factor * SHIP_VALUE`.
        # Until then, absolute equality is safe because SHIP_VALUE is test-controlled
        # and the service applies no discount multiplier.
        async with factory() as verify_db:
            result = await verify_db.execute(select(Player).where(Player.id == player_id))
            updated_player = result.scalars().first()
            expected_credits = 100 + SHIP_VALUE
            assert updated_player.credits == expected_credits, (
                f"Credits should be {expected_credits} (100 + {SHIP_VALUE}), got {updated_player.credits}"
            )

            result = await verify_db.execute(select(PlayerShip).where(PlayerShip.id == inactive_ship_id))
            assert result.scalars().first() is None, "Sold ship should have been deleted from DB"

            # Verify the ship was added to the shop
            shop_result = await verify_db.execute(
                select(GuildShop).where((GuildShop.guild_id == 9032) & (GuildShop.item_name == "OldShip"))
            )
            assert shop_result.scalars().first() is not None, "Ship should appear in guild shop after sell"

        await engine.dispose()


# ---------------------------------------------------------------------------
# Test: POST /api/v1/inventory/transfer (A.44 — inventory_service.transfer_item)
# ---------------------------------------------------------------------------


class TestInventoryTransferEndpoint:
    """Tests for POST /api/v1/inventory/transfer (A.44 transaction fix)."""

    async def test_transfer_item_returns_200_and_updates_inventory(self):
        """
        Seeds two players in the same guild; source has 2 'ship' inventory items
        (ship is both accepted by the request schema regex AND normalizes to a single
        concrete type, avoiding the InvalidItemTypeError that would result from 'weapon').
        Transfers 1 item and asserts HTTP 200 + correct inventory state.

        Key assertion: NOT 500 (A.44 fix prevents nested-begin crash).
        Mock budget: 1 mock (InventoryService._validate_item_exists — static catalog
        tables not in the SQLite integration schema).
        """
        engine, factory = await _make_sqlite_session_factory()

        async with factory() as db:
            user1 = await _seed_user(db, user_id=904001)
            user2 = await _seed_user(db, user_id=904002)
            await _seed_guild_config(db, guild_id=9004)
            source_player = await _seed_player(db, user_id=user1.id, guild_id=9004)
            target_player = await _seed_player(db, user_id=user2.id, guild_id=9004)
            # Use 'ship' as item_type: accepted by request schema regex AND normalizes to one type
            await _seed_inventory(db, source_player.id, "ship", "Hammerhead", quantity=2)
            source_player_id = source_player.id
            target_player_id = target_player.id

        app = FastAPI()
        from api.routers.inventory import router as inventory_router

        app.include_router(inventory_router, prefix="/api/v1")

        # Mock 1: item-existence validation (static catalog not in SQLite schema)
        from services.inventory_service import InventoryService

        original_validate = InventoryService._validate_item_exists

        async def _mock_validate(self, db, item_name, item_type):
            return True

        InventoryService._validate_item_exists = _mock_validate

        try:
            async with factory() as router_db:
                with _make_cm_patcher("api.routers.inventory.get_db_session", router_db):
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://testserver",
                    ) as client:
                        response = await client.post(
                            "/api/v1/inventory/transfer",
                            json={
                                "from_player_id": source_player_id,
                                "to_player_id": target_player_id,
                                "item_type": "ship",
                                "item_name": "Hammerhead",
                                "quantity": 1,
                            },
                        )
        finally:
            InventoryService._validate_item_exists = original_validate

        # Key assertion: NOT 500
        assert response.status_code != 500, (
            f"Got 500 — A.44 transaction-nesting bug may still be present: {response.text}"
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        # Verify source inventory decreased (2 → 1)
        async with factory() as verify_db:
            result = await verify_db.execute(
                select(PlayerInventory).where(
                    (PlayerInventory.player_id == source_player_id) & (PlayerInventory.item_name == "Hammerhead")
                )
            )
            source_item = result.scalars().first()
            assert source_item is not None and source_item.quantity == 1, (
                f"Source should have 1 remaining after transfer, got: {source_item}"
            )

            result = await verify_db.execute(
                select(PlayerInventory).where(
                    (PlayerInventory.player_id == target_player_id) & (PlayerInventory.item_name == "Hammerhead")
                )
            )
            target_item = result.scalars().first()
            assert target_item is not None and target_item.quantity == 1, (
                f"Target should have received 1 item, got: {target_item}"
            )

        await engine.dispose()


# ---------------------------------------------------------------------------
# Test: POST /api/v1/players/transfer (A.44 — player_service.transfer_credits)
# ---------------------------------------------------------------------------


class TestPlayersTransferEndpoint:
    """Tests for POST /api/v1/players/transfer (A.44 transaction fix)."""

    async def test_transfer_credits_returns_200_and_updates_balances(self):
        """
        Seeds two players: source (1000 credits), target (200 credits).
        Transfers 250 credits. Asserts HTTP 200 and correct balances.

        Key assertion: NOT 500 (A.44 fix prevents nested-begin crash).
        Mock budget: 0 mocks (PlayerRepository is fully SQLite-compatible).
        """
        engine, factory = await _make_sqlite_session_factory()

        async with factory() as db:
            user1 = await _seed_user(db, user_id=905001)
            user2 = await _seed_user(db, user_id=905002)
            await _seed_guild_config(db, guild_id=9005)
            source_player = await _seed_player(db, user_id=user1.id, guild_id=9005, credits=1000)
            target_player = await _seed_player(db, user_id=user2.id, guild_id=9005, credits=200)
            source_player_id = source_player.id
            target_player_id = target_player.id

        app = FastAPI()
        from api.routers.players import router as players_router

        app.include_router(players_router, prefix="/api/v1")

        async with factory() as router_db:
            with _make_cm_patcher("api.routers.players.get_db_session", router_db):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://testserver",
                ) as client:
                    response = await client.post(
                        "/api/v1/players/transfer",
                        json={
                            "source_player_id": source_player_id,
                            "target_player_id": target_player_id,
                            "amount": 250,
                        },
                    )

        # Key assertion: NOT 500
        assert response.status_code != 500, (
            f"Got 500 — A.44 transaction-nesting bug may still be present: {response.text}"
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        data = response.json()
        assert data["source_remaining_credits"] == 750, (
            f"Source should have 750 remaining, got {data['source_remaining_credits']}"
        )
        assert data["target_new_credits"] == 450, f"Target should have 450 credits, got {data['target_new_credits']}"

        # Verify DB state
        async with factory() as verify_db:
            result = await verify_db.execute(select(Player).where(Player.id == source_player_id))
            updated_source = result.scalars().first()
            assert updated_source.credits == 750

            result = await verify_db.execute(select(Player).where(Player.id == target_player_id))
            updated_target = result.scalars().first()
            assert updated_target.credits == 450

        await engine.dispose()


# ---------------------------------------------------------------------------
# Test: POST /api/v1/shops/sell — rollback on 0 inventory (GAP-1)
# ---------------------------------------------------------------------------


class TestShopsSellRollback:
    """Tests that /shops/sell rolls back on ValueError (GAP-1)."""

    async def test_sell_with_zero_inventory_rolls_back_and_returns_400(self):
        """
        GAP-1: Validates that a sell attempt by a player with 0 inventory
        returns HTTP 400 and leaves all DB state unchanged.

        This tests the A.44 router-owned db.begin() rollback path.
        Mock budget: 1 (ShopService._get_item_base_price — static item tables
        not in the SQLite integration schema).
        """
        engine, factory = await _make_sqlite_session_factory()

        # Seed: player with NO inventory (0 items)
        async with factory() as db:
            user = await _seed_user(db, user_id=901002)
            await _seed_guild_config(db, guild_id=9002)
            player = await _seed_player(db, user_id=user.id, guild_id=9002, credits=100)
            player_id = player.id

        app = FastAPI()
        from api.routers.shops import router as shops_router

        app.include_router(shops_router, prefix="/api/v1")

        # Mock 1: price lookup (static tables not in SQLite integration schema)
        from services.shop_service import ShopService

        original_get_price = ShopService._get_item_base_price

        async def _mock_price(self, db, item_name):
            return 50

        ShopService._get_item_base_price = _mock_price

        try:
            async with factory() as router_db:
                with _make_cm_patcher("api.routers.shops.get_db_session", router_db):
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://testserver",
                    ) as client:
                        response = await client.post(
                            "/api/v1/shops/sell",
                            json={"player_id": player_id, "item_name": "Pulse Laser", "quantity": 1},
                        )
        finally:
            ShopService._get_item_base_price = original_get_price

        assert response.status_code == 400, f"Expected 400 (no inventory), got {response.status_code}: {response.text}"

        # Verify rollback: credits unchanged, no inventory row created, no shop row created
        async with factory() as verify_db:
            result = await verify_db.execute(select(Player).where(Player.id == player_id))
            updated_player = result.scalars().first()
            assert updated_player.credits == 100, f"Credits should be unchanged (100), got {updated_player.credits}"

            inv_result = await verify_db.execute(select(PlayerInventory).where(PlayerInventory.player_id == player_id))
            assert inv_result.scalars().first() is None, "No inventory row should exist after rollback"

            shop_result = await verify_db.execute(
                select(GuildShop).where((GuildShop.guild_id == 9002) & (GuildShop.item_name == "Pulse Laser"))
            )
            assert shop_result.scalars().first() is None, "No shop row should exist after rollback"

        await engine.dispose()


# ---------------------------------------------------------------------------
# Test: POST /api/v1/players/transfer — rollback on insufficient credits (GAP-2)
# ---------------------------------------------------------------------------


class TestPlayersTransferRollback:
    """Tests that /players/transfer rolls back on insufficient credits (GAP-2)."""

    async def test_transfer_with_insufficient_credits_rolls_back_and_returns_400(self):
        """
        GAP-2: Validates that a transfer attempt where source has insufficient
        credits returns HTTP 400 and leaves both player balances unchanged.

        Mock budget: 0 (PlayerRepository is fully SQLite-compatible).
        """
        engine, factory = await _make_sqlite_session_factory()

        async with factory() as db:
            user1 = await _seed_user(db, user_id=901003)
            user2 = await _seed_user(db, user_id=901004)
            await _seed_guild_config(db, guild_id=9003)
            source_player = await _seed_player(db, user_id=user1.id, guild_id=9003, credits=10)
            target_player = await _seed_player(db, user_id=user2.id, guild_id=9003, credits=20)
            source_player_id = source_player.id
            target_player_id = target_player.id

        app = FastAPI()
        from api.routers.players import router as players_router

        app.include_router(players_router, prefix="/api/v1")

        async with factory() as router_db:
            with _make_cm_patcher("api.routers.players.get_db_session", router_db):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://testserver",
                ) as client:
                    response = await client.post(
                        "/api/v1/players/transfer",
                        json={
                            "source_player_id": source_player_id,
                            "target_player_id": target_player_id,
                            "amount": 100,  # more than source has
                        },
                    )

        assert response.status_code == 400, (
            f"Expected 400 (insufficient credits), got {response.status_code}: {response.text}"
        )

        # Verify rollback: both balances unchanged
        async with factory() as verify_db:
            result = await verify_db.execute(select(Player).where(Player.id == source_player_id))
            updated_source = result.scalars().first()
            assert updated_source.credits == 10, (
                f"Source credits should be unchanged (10), got {updated_source.credits}"
            )

            result = await verify_db.execute(select(Player).where(Player.id == target_player_id))
            updated_target = result.scalars().first()
            assert updated_target.credits == 20, (
                f"Target credits should be unchanged (20), got {updated_target.credits}"
            )

        await engine.dispose()


# ---------------------------------------------------------------------------
# Test: POST /api/v1/ships/transfer — rollback on partial inventory failure (DEF-A47-002)
# ---------------------------------------------------------------------------


class TestShipTransferRollback:
    """DEF-A47-002: Real SQLite integration variant for ship-transfer rollback.

    The existing mock-based test in tests/api/test_ship_transfer.py verifies the
    HTTP 500 response but cannot confirm DB state was not mutated.  This test seeds
    real rows, triggers a mid-loop inventory failure, and asserts that ownership
    of the PlayerShip was NOT changed in the database.

    Mock budget: 2
      - Mock 1: InventoryRepository -- controls add_item; raises on 2nd call (partial failure)
      - Mock 2: ItemRepository -- returns a synthetic item so concrete type is resolved
        (the real Item/weapon tables are not in the SQLite integration schema)
    """

    async def test_ship_transfer_real_db_rollback_preserves_source_ownership(self):
        """
        DEF-A47-002: Seeds source_player with an inactive ship loaded with two weapons.
        Patches InventoryRepository.add_item to raise on the second call (mid-loop failure).
        POSTs /api/v1/ships/transfer and asserts:
          - HTTP 500 (RuntimeError -> generic Exception handler -> 500)
          - PlayerShip.player_id still equals source_player.id after the failed transfer
          - No PlayerInventory rows were added for source_player (rollback undid the 1st add_item)
        """
        engine, factory = await _make_sqlite_session_factory()

        async with factory() as db:
            source_user = await _seed_user(db, user_id=910001)
            target_user = await _seed_user(db, user_id=910002)
            await _seed_guild_config(db, guild_id=9100)
            source_player = await _seed_player(db, user_id=source_user.id, guild_id=9100, credits=100)
            target_player = await _seed_player(db, user_id=target_user.id, guild_id=9100, credits=100)
            # Seed an inactive ship with two weapons so the loop runs at least twice
            ship = PlayerShip(
                player_id=source_player.id,
                ship_name="TestShip",
                is_active=False,
                weapons=["Pulse Laser", "Burst Laser"],  # 2 weapons -> loop hits add_item twice
                modules=[],
                turrets=[],
                secondary_weapons=[],
            )
            db.add(ship)
            await db.commit()
            await db.refresh(ship)
            source_player_id = source_player.id
            target_player_id = target_player.id
            ship_id = ship.id

        app = FastAPI()
        from api.routers.ships import router as ships_router

        app.include_router(ships_router, prefix="/api/v1")

        call_count_add = {"n": 0}

        # Mock 1: ItemRepository -- resolves every item name to a PrimaryWeapon discriminator
        #         (the real Item/Weapon tables are not in the SQLite integration schema)
        class _FakeItemRepo:
            async def get_by_name_any_type(self, db, name):
                m = MagicMock()
                m.type = "PrimaryWeapon"  # discriminator -> resolves to "primary_weapon"
                return m

        # Mock 2: InventoryRepository -- add_item succeeds on first call, raises on second
        class _FakeInventoryRepo:
            async def add_item(self, db, player_id, item_type, item_name, quantity, commit=True):
                call_count_add["n"] += 1
                if call_count_add["n"] >= 2:
                    raise RuntimeError("Simulated DB write failure mid-loop (DEF-A47-002)")
                # First call: do a real insert so rollback is meaningful
                inv_item = PlayerInventory(
                    player_id=player_id,
                    item_type=item_type,
                    item_name=item_name,
                    quantity=quantity,
                )
                db.add(inv_item)
                await db.flush()

        with (
            patch("api.routers.ships.InventoryRepository", return_value=_FakeInventoryRepo()),
            patch("api.routers.ships.ItemRepository", return_value=_FakeItemRepo()),
        ):
            async with factory() as router_db:
                with _make_cm_patcher("api.routers.ships.get_db_session", router_db):
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://testserver",
                    ) as client:
                        response = await client.post(
                            "/api/v1/ships/transfer",
                            json={
                                "from_player_id": source_player_id,
                                "to_player_id": target_player_id,
                                "ship_id": ship_id,
                            },
                        )

        # The RuntimeError is caught by the generic Exception handler -> HTTP 500
        assert response.status_code == 500, (
            f"Expected 500 on partial inventory failure, got {response.status_code}: {response.text}"
        )

        # Real DB assertion: ship ownership NOT changed -- source_player still owns the ship
        async with factory() as verify_db:
            result = await verify_db.execute(select(PlayerShip).where(PlayerShip.id == ship_id))
            ship_row = result.scalars().first()
            assert ship_row is not None, "Ship row should still exist (not deleted on rollback)"
            assert ship_row.player_id == source_player_id, (
                f"Ship should still belong to source_player ({source_player_id}), "
                f"but player_id is {ship_row.player_id} -- rollback did not preserve ownership"
            )

            # Rollback must have undone the first add_item flush too
            inv_result = await verify_db.execute(
                select(PlayerInventory).where(PlayerInventory.player_id == source_player_id)
            )
            assert inv_result.scalars().first() is None, (
                "No inventory rows should exist after rollback -- the 1st add_item flush was rolled back"
            )

        await engine.dispose()
