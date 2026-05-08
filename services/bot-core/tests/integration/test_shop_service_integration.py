"""
Integration tests for ShopService — S6 sprint.

Covers the ORM mutation paths that caused the April 2026 credit-doubling bug:
  - sell_item:     update_credits + inventory remove  (cross-session)
  - sell_ship:     update_credits + PlayerShip delete (cross-session)
  - purchase_item: credits deducted + shop quantity updated (cross-session)

Cross-session reload rule (B.34): every test opens session A, performs the
operation, closes session A, opens a fresh session B, then asserts persistence
through session B.

SQLite compatibility note:
  - GuildConfig, GuildShop, Player, User, PlayerInventory, PlayerShip are SQLite-safe.
  - Ship / Item / Weapon STI tables have ARRAY columns → cannot be seeded in SQLite.
  - Tests that call methods needing item-price lookups (via ship/item repos) mock
    ShopService._get_item_base_price at the METHOD boundary only, with a comment
    citing AGENTS.md §SQLite Compatibility.

Mock budget: max 2 mocks per test (per AGENTS.md).
"""

# ---------------------------------------------------------------------------
# Path setup: ensure src/ is first on sys.path.
# ---------------------------------------------------------------------------
import os
import sys

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
elif sys.path[0] != _SRC_DIR:
    sys.path.remove(_SRC_DIR)
    sys.path.insert(0, _SRC_DIR)

# Purge stale api.* and persist.* entries pointing at tests/ packages ONLY.
for _key in list(sys.modules):
    if _key in ("api", "persist") or _key.startswith(("api.", "persist.")):
        _mod = sys.modules[_key]
        _file = getattr(_mod, "__file__", "") or ""
        if _SRC_DIR not in _file:
            del sys.modules[_key]

# ---------------------------------------------------------------------------

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from persist.models.base import Base
from persist.models.guild_config import GuildConfig
from persist.models.guild_shop import GuildShop
from persist.models.player import Player
from persist.models.player_inventory import PlayerInventory
from persist.models.player_ship import PlayerShip
from persist.models.user import User
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# SQLite-compatible tables only (no ARRAY columns).
_SQLITE_TABLES = [
    User.__table__,
    Player.__table__,
    GuildConfig.__table__,
    GuildShop.__table__,
    PlayerInventory.__table__,
    PlayerShip.__table__,
]


# ---------------------------------------------------------------------------
# Per-test engine + session factory helpers
# ---------------------------------------------------------------------------


async def _fresh_engine_and_factory():
    """Create a fresh SQLite in-memory engine + session factory."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_SQLITE_TABLES)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_guild_config(db: AsyncSession, guild_id: int) -> GuildConfig:
    config = GuildConfig(
        guild_id=guild_id,
        starting_credits=1000,
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return config


async def _seed_user(db: AsyncSession, user_id: int) -> User:
    user = User(id=user_id, discord_username=f"user{user_id}")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _seed_player(
    db: AsyncSession,
    user_id: int,
    guild_id: int,
    credits: int = 5000,
    tier: str = "Bronze",
) -> Player:
    p = Player(
        user_id=user_id,
        guild_id=guild_id,
        credits=credits,
        lifetime_credits=credits,
        xp=0,
        xp_surplus=0,
        tier=tier,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _seed_inventory_item(
    db: AsyncSession,
    player_id: int,
    item_type: str,
    item_name: str,
    quantity: int = 1,
) -> PlayerInventory:
    inv = PlayerInventory(
        player_id=player_id,
        item_type=item_type,
        item_name=item_name,
        quantity=quantity,
    )
    db.add(inv)
    await db.commit()
    await db.refresh(inv)
    return inv


async def _seed_shop_item(
    db: AsyncSession,
    guild_id: int,
    tier: str,
    item_type: str,
    item_name: str,
    price: int,
    quantity: int = 5,
) -> GuildShop:
    shop = GuildShop(
        guild_id=guild_id,
        tier=tier,
        tech_level=3,
        item_type=item_type,
        item_name=item_name,
        quantity=quantity,
        price=price,
        last_restocked=datetime.now(UTC),
    )
    db.add(shop)
    await db.commit()
    await db.refresh(shop)
    return shop


async def _seed_player_ship(
    db: AsyncSession,
    player_id: int,
    ship_name: str,
    is_active: bool = False,
) -> PlayerShip:
    ps = PlayerShip(
        player_id=player_id,
        ship_name=ship_name,
        is_active=is_active,
        weapons=[],
        modules=[],
        turrets=[],
        secondary_weapons=[],
    )
    db.add(ps)
    await db.commit()
    await db.refresh(ps)
    return ps


# ---------------------------------------------------------------------------
# sell_item integration tests
# ---------------------------------------------------------------------------
# sell_item calls:
#   - inventory_repo.get_player_items_by_name  (reads player_inventories)
#   - _get_item_base_price                     (needs Ship/Item repos — ARRAY tables → mock)
#   - player_repo.get_by_id_for_update         (reads players)
#   - inventory_repo.remove_item               (commit=False)
#   - player_repo.update_credits               (commit=False)
#   - _add_item_to_shop                        (reads/writes guild_shops)
# The service does NOT commit — the router owns the commit.
# Tests issue db.commit() after calling the service to satisfy the pattern.


class TestSellItemIntegration:
    """Cross-session persistence tests for ShopService.sell_item."""

    @pytest.fixture
    def service(self):
        from services.shop_service import ShopService

        return ShopService()

    @pytest.mark.asyncio
    async def test_sell_item_credits_credited_cross_session(self, service):
        """sell_item credits the player — value readable from a fresh session B."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_guild_config(session_a, guild_id=10)
                await _seed_user(session_a, user_id=1)
                player = await _seed_player(session_a, user_id=1, guild_id=10, credits=3000)
                player_id = player.id
                await _seed_inventory_item(session_a, player_id, "module", "E2 Exoclad", quantity=2)

                # 1 mock — _get_item_base_price: Ship/Item tables have ARRAY columns
                # (cannot be seeded in SQLite — see AGENTS.md §SQLite Compatibility)
                with patch.object(service, "_get_item_base_price", new=AsyncMock(return_value=500)):
                    result = await service.sell_item(session_a, player_id=player_id, item_name="E2 Exoclad", quantity=1)
                    await session_a.commit()

            assert result["new_credits"] == 3500
            assert result["total_sell_value"] == 500

            # Cross-session reload — verify persistence
            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b is not None
                assert player_b.credits == 3500, f"Expected credits=3500 (3000+500); got {player_b.credits}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_sell_item_inventory_quantity_decremented_cross_session(self, service):
        """sell_item decrements player inventory quantity — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_guild_config(session_a, guild_id=10)
                await _seed_user(session_a, user_id=2)
                player = await _seed_player(session_a, user_id=2, guild_id=10, credits=1000)
                player_id = player.id
                await _seed_inventory_item(session_a, player_id, "module", "Telta Quickscan", quantity=3)

                # 1 mock — _get_item_base_price: ARRAY-column tables unavailable in SQLite
                with patch.object(service, "_get_item_base_price", new=AsyncMock(return_value=200)):
                    await service.sell_item(session_a, player_id=player_id, item_name="Telta Quickscan", quantity=2)
                    await session_a.commit()

            # Expect quantity = 3 - 2 = 1
            async with factory() as session_b:
                from sqlalchemy import and_, select

                result = await session_b.execute(
                    select(PlayerInventory).where(
                        and_(
                            PlayerInventory.player_id == player_id,
                            PlayerInventory.item_name == "Telta Quickscan",
                        )
                    )
                )
                inv = result.scalars().first()
                assert inv is not None
                assert inv.quantity == 1, f"Expected quantity=1 (3-2); got {inv.quantity}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_sell_item_full_quantity_removes_row_cross_session(self, service):
        """sell_item selling all copies removes the inventory row — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_guild_config(session_a, guild_id=10)
                await _seed_user(session_a, user_id=3)
                player = await _seed_player(session_a, user_id=3, guild_id=10, credits=500)
                player_id = player.id
                await _seed_inventory_item(session_a, player_id, "primary_weapon", "Nirai Impulse EX 1", quantity=1)

                # 1 mock — _get_item_base_price: ARRAY-column tables unavailable in SQLite
                with patch.object(service, "_get_item_base_price", new=AsyncMock(return_value=1000)):
                    await service.sell_item(session_a, player_id=player_id, item_name="Nirai Impulse EX 1", quantity=1)
                    await session_a.commit()

            # Inventory row should be deleted entirely
            async with factory() as session_b:
                from sqlalchemy import and_, select

                result = await session_b.execute(
                    select(PlayerInventory).where(
                        and_(
                            PlayerInventory.player_id == player_id,
                            PlayerInventory.item_name == "Nirai Impulse EX 1",
                        )
                    )
                )
                inv = result.scalars().first()
                assert inv is None, "Inventory row should have been deleted when selling all copies"

            # Credits should be increased
            async with factory() as session_b2:
                player_b = await session_b2.get(Player, player_id)
                assert player_b.credits == 1500, f"Expected 1500; got {player_b.credits}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_sell_item_adds_item_to_shop_cross_session(self, service):
        """sell_item creates a shop listing for the sold item — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_guild_config(session_a, guild_id=11)
                await _seed_user(session_a, user_id=4)
                player = await _seed_player(session_a, user_id=4, guild_id=11, credits=2000, tier="Bronze")
                player_id = player.id
                await _seed_inventory_item(session_a, player_id, "module", "Shield Booster", quantity=1)

                # 1 mock — _get_item_base_price: ARRAY-column tables unavailable in SQLite
                with patch.object(service, "_get_item_base_price", new=AsyncMock(return_value=750)):
                    await service.sell_item(session_a, player_id=player_id, item_name="Shield Booster", quantity=1)
                    await session_a.commit()

            # The shop should now contain "Shield Booster" in the Bronze tier
            async with factory() as session_b:
                from sqlalchemy import and_, select

                result = await session_b.execute(
                    select(GuildShop).where(
                        and_(
                            GuildShop.guild_id == 11,
                            GuildShop.item_name == "Shield Booster",
                            GuildShop.tier == "Bronze",
                        )
                    )
                )
                shop_item = result.scalars().first()
                assert shop_item is not None, "Shop listing should have been created for sold item"
                assert shop_item.quantity == 1
                assert shop_item.price == 750
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_sell_item_not_in_inventory_raises_value_error(self, service):
        """sell_item raises ValueError when item not in inventory — no mutation."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_guild_config(session_a, guild_id=10)
                await _seed_user(session_a, user_id=5)
                player = await _seed_player(session_a, user_id=5, guild_id=10, credits=1000)
                player_id = player.id
                # No inventory item seeded

                with pytest.raises(ValueError, match="not found in your inventory"):
                    await service.sell_item(session_a, player_id=player_id, item_name="NonExistentItem", quantity=1)

            # No mutation should have occurred
            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b.credits == 1000, "Credits should be unchanged on error"
        finally:
            await engine.dispose()


# ---------------------------------------------------------------------------
# sell_ship integration tests
# ---------------------------------------------------------------------------
# sell_ship calls:
#   - player_repo.get_by_id                   (reads players)
#   - player_ship_repo.get_by_id              (reads player_ships)
#   - ship_repo.get_by_name                   (needs Ship table — ARRAY → mock)
#   - player_repo.get_by_id_for_update        (locks player row)
#   - player_repo.update_credits              (commit=False)
#   - db.delete(player_ship)                  (deletes PlayerShip row)
#   - _add_item_to_shop                       (writes guild_shops)
# Service does NOT commit — tests issue db.commit() after calling the service.


class TestSellShipIntegration:
    """Cross-session persistence tests for ShopService.sell_ship."""

    @pytest.fixture
    def service(self):
        from services.shop_service import ShopService

        return ShopService()

    @pytest.mark.asyncio
    async def test_sell_ship_credits_credited_cross_session(self, service):
        """sell_ship adds ship value to player credits — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_guild_config(session_a, guild_id=20)
                await _seed_user(session_a, user_id=10)
                player = await _seed_player(session_a, user_id=10, guild_id=20, credits=1000)
                player_id = player.id
                ship = await _seed_player_ship(session_a, player_id, "Valkyr", is_active=False)
                ship_id = ship.id

                # 1 mock — ship_repo.get_by_name: Ship table has ARRAY columns
                # (cannot be seeded in SQLite — see AGENTS.md §SQLite Compatibility)
                mock_ship_static = type("MockShip", (), {"value": 4000, "name": "Valkyr"})()
                with patch.object(service.ship_repo, "get_by_name", new=AsyncMock(return_value=mock_ship_static)):
                    result = await service.sell_ship(
                        session_a, player_id=player_id, ship_id=ship_id, target_tier="Bronze"
                    )
                    await session_a.commit()

            assert result["new_credits"] == 5000
            assert result["sell_value"] == 4000

            # Cross-session reload
            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b is not None
                assert player_b.credits == 5000, f"Expected credits=5000 (1000+4000); got {player_b.credits}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_sell_ship_deletes_player_ship_cross_session(self, service):
        """sell_ship removes the PlayerShip row — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_guild_config(session_a, guild_id=20)
                await _seed_user(session_a, user_id=11)
                player = await _seed_player(session_a, user_id=11, guild_id=20, credits=500)
                player_id = player.id
                ship = await _seed_player_ship(session_a, player_id, "Raptor", is_active=False)
                ship_id = ship.id

                # 1 mock — ship_repo.get_by_name: Ship table has ARRAY columns
                mock_ship_static = type("MockShip", (), {"value": 2000, "name": "Raptor"})()
                with patch.object(service.ship_repo, "get_by_name", new=AsyncMock(return_value=mock_ship_static)):
                    await service.sell_ship(session_a, player_id=player_id, ship_id=ship_id, target_tier="Bronze")
                    await session_a.commit()

            # PlayerShip row should be deleted
            async with factory() as session_b:
                ship_b = await session_b.get(PlayerShip, ship_id)
                assert ship_b is None, f"PlayerShip row should have been deleted (id={ship_id})"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_sell_ship_adds_to_shop_cross_session(self, service):
        """sell_ship creates a shop listing for the sold ship — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_guild_config(session_a, guild_id=21)
                await _seed_user(session_a, user_id=12)
                player = await _seed_player(session_a, user_id=12, guild_id=21, credits=0)
                player_id = player.id
                ship = await _seed_player_ship(session_a, player_id, "Betty", is_active=False)
                ship_id = ship.id

                # 1 mock — ship_repo.get_by_name: Ship table has ARRAY columns
                mock_ship_static = type("MockShip", (), {"value": 3500, "name": "Betty"})()
                with patch.object(service.ship_repo, "get_by_name", new=AsyncMock(return_value=mock_ship_static)):
                    await service.sell_ship(session_a, player_id=player_id, ship_id=ship_id, target_tier="Bronze")
                    await session_a.commit()

            # Shop listing should exist
            async with factory() as session_b:
                from sqlalchemy import and_, select

                result = await session_b.execute(
                    select(GuildShop).where(
                        and_(
                            GuildShop.guild_id == 21,
                            GuildShop.item_name == "Betty",
                            GuildShop.tier == "Bronze",
                        )
                    )
                )
                shop_item = result.scalars().first()
                assert shop_item is not None, "Shop listing should have been created for sold ship"
                assert shop_item.quantity == 1
                assert shop_item.price == 3500
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_sell_active_ship_raises_value_error(self, service):
        """sell_ship raises ValueError when trying to sell the active ship — no mutation."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_guild_config(session_a, guild_id=20)
                await _seed_user(session_a, user_id=13)
                player = await _seed_player(session_a, user_id=13, guild_id=20, credits=2000)
                player_id = player.id
                active_ship = await _seed_player_ship(session_a, player_id, "ActiveShip", is_active=True)
                ship_id = active_ship.id

                with pytest.raises(ValueError, match="Cannot sell active ship"):
                    await service.sell_ship(session_a, player_id=player_id, ship_id=ship_id, target_tier="Bronze")

            # Credits should be unchanged
            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b.credits == 2000
        finally:
            await engine.dispose()


# ---------------------------------------------------------------------------
# purchase_item integration tests
# ---------------------------------------------------------------------------
# purchase_item commits internally (calls db.commit() + db.refresh(player)).
# Tests verify cross-session state without needing an explicit commit.


class TestPurchaseItemIntegration:
    """Cross-session persistence tests for ShopService.purchase_item."""

    @pytest.fixture
    def service(self):
        from services.shop_service import ShopService

        return ShopService()

    @pytest.mark.asyncio
    async def test_purchase_item_credits_deducted_cross_session(self, service):
        """purchase_item deducts credits — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_guild_config(session_a, guild_id=30)
                await _seed_user(session_a, user_id=20)
                player = await _seed_player(session_a, user_id=20, guild_id=30, credits=5000, tier="Bronze")
                player_id = player.id
                shop_item = await _seed_shop_item(
                    session_a,
                    guild_id=30,
                    tier="Bronze",
                    item_type="module",
                    item_name="E2 Exoclad",
                    price=1000,
                    quantity=3,
                )
                shop_item_id = shop_item.id

                result = await service.purchase_item(
                    session_a, player_id=player_id, shop_item_id=shop_item_id, quantity=1
                )

            assert result["total_cost"] == 1000
            assert result["remaining_credits"] == 4000

            # Cross-session reload
            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b is not None
                assert player_b.credits == 4000, f"Expected credits=4000 (5000-1000); got {player_b.credits}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_purchase_item_shop_quantity_decremented_cross_session(self, service):
        """purchase_item decrements shop quantity — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_guild_config(session_a, guild_id=30)
                await _seed_user(session_a, user_id=21)
                player = await _seed_player(session_a, user_id=21, guild_id=30, credits=10000, tier="Bronze")
                player_id = player.id
                shop_item = await _seed_shop_item(
                    session_a,
                    guild_id=30,
                    tier="Bronze",
                    item_type="module",
                    item_name="Telta Quickscan",
                    price=500,
                    quantity=5,
                )
                shop_item_id = shop_item.id

                await service.purchase_item(session_a, player_id=player_id, shop_item_id=shop_item_id, quantity=2)

            # Shop quantity should be 5 - 2 = 3
            async with factory() as session_b:
                shop_b = await session_b.get(GuildShop, shop_item_id)
                assert shop_b is not None
                assert shop_b.quantity == 3, f"Expected shop quantity=3 (5-2); got {shop_b.quantity}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_purchase_item_added_to_inventory_cross_session(self, service):
        """purchase_item adds item to player inventory — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_guild_config(session_a, guild_id=30)
                await _seed_user(session_a, user_id=22)
                player = await _seed_player(session_a, user_id=22, guild_id=30, credits=8000, tier="Bronze")
                player_id = player.id
                shop_item = await _seed_shop_item(
                    session_a,
                    guild_id=30,
                    tier="Bronze",
                    item_type="module",
                    item_name="Shield Module",
                    price=2000,
                    quantity=4,
                )
                shop_item_id = shop_item.id

                await service.purchase_item(session_a, player_id=player_id, shop_item_id=shop_item_id, quantity=1)

            # Player inventory should now contain the item
            async with factory() as session_b:
                from sqlalchemy import and_, select

                result = await session_b.execute(
                    select(PlayerInventory).where(
                        and_(
                            PlayerInventory.player_id == player_id,
                            PlayerInventory.item_name == "Shield Module",
                        )
                    )
                )
                inv = result.scalars().first()
                assert inv is not None, "Inventory row should have been created after purchase"
                assert inv.quantity == 1
                assert inv.item_type == "module"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_purchase_item_last_unit_removes_shop_row_cross_session(self, service):
        """purchase_item buying the last unit removes the shop row — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_guild_config(session_a, guild_id=30)
                await _seed_user(session_a, user_id=23)
                player = await _seed_player(session_a, user_id=23, guild_id=30, credits=5000, tier="Bronze")
                player_id = player.id
                shop_item = await _seed_shop_item(
                    session_a,
                    guild_id=30,
                    tier="Bronze",
                    item_type="module",
                    item_name="LastOne",
                    price=500,
                    quantity=1,
                )
                shop_item_id = shop_item.id

                await service.purchase_item(session_a, player_id=player_id, shop_item_id=shop_item_id, quantity=1)

            # Shop row should be gone
            async with factory() as session_b:
                shop_b = await session_b.get(GuildShop, shop_item_id)
                assert shop_b is None, "Shop row should be deleted when last unit is purchased"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_purchase_item_insufficient_credits_raises(self, service):
        """purchase_item raises ValueError when player has insufficient credits — no mutation."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_guild_config(session_a, guild_id=30)
                await _seed_user(session_a, user_id=24)
                player = await _seed_player(session_a, user_id=24, guild_id=30, credits=100, tier="Bronze")
                player_id = player.id
                shop_item = await _seed_shop_item(
                    session_a,
                    guild_id=30,
                    tier="Bronze",
                    item_type="module",
                    item_name="Expensive",
                    price=9999,
                    quantity=3,
                )
                shop_item_id = shop_item.id

                with pytest.raises(ValueError, match="Insufficient credits"):
                    await service.purchase_item(session_a, player_id=player_id, shop_item_id=shop_item_id, quantity=1)

            # Credits should be unchanged, shop unchanged
            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b.credits == 100

                shop_b = await session_b.get(GuildShop, shop_item_id)
                assert shop_b is not None
                assert shop_b.quantity == 3
        finally:
            await engine.dispose()
