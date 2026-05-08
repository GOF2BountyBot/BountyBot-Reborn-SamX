"""
Integration tests for InventoryService — S6 sprint.

Covers the ORM mutation paths that AsyncMock-based unit tests cannot exercise:
  - add_item_to_inventory:    quantity updated / item created (cross-session)
  - remove_item_from_inventory: quantity decremented / row deleted (cross-session)
  - transfer_item_between_players: item moves from one player to another (cross-session)

Cross-session reload rule (B.34): every test opens session A, performs the
operation, closes session A, opens a fresh session B, then asserts persistence
through session B.

SQLite compatibility note:
  - User, Player, GuildConfig, PlayerInventory are SQLite-safe.
  - Ship / Item / Weapon STI tables have ARRAY columns → cannot be seeded in SQLite.
  - add_item_to_inventory calls _validate_item_exists which iterates all repos
    including ship_repo. We mock _validate_item_exists at the method boundary
    (1 mock per test) to bypass the ARRAY-column tables.
    See AGENTS.md §SQLite Compatibility.

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

from unittest.mock import AsyncMock, patch

import pytest
from persist.models.base import Base
from persist.models.guild_config import GuildConfig
from persist.models.guild_shop import GuildShop
from persist.models.player import Player
from persist.models.player_inventory import PlayerInventory
from persist.models.player_ship import PlayerShip
from persist.models.user import User
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_SQLITE_TABLES = [
    User.__table__,
    Player.__table__,
    GuildConfig.__table__,
    GuildShop.__table__,
    PlayerInventory.__table__,
    PlayerShip.__table__,
]


# ---------------------------------------------------------------------------
# Per-test engine + session factory
# ---------------------------------------------------------------------------


async def _fresh_engine_and_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_SQLITE_TABLES)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# add_item_to_inventory integration tests
# ---------------------------------------------------------------------------
# add_item_to_inventory:
#   - validates item exists via _validate_item_exists (mocked — ARRAY tables)
#   - calls inventory_repo.add_item (commit=True by default)
#   - if item exists: increments quantity; else creates new row


class TestAddItemToInventoryIntegration:
    """Cross-session persistence tests for InventoryService.add_item_to_inventory."""

    @pytest.fixture
    def service(self):
        from services.inventory_service import InventoryService

        return InventoryService()

    @pytest.mark.asyncio
    async def test_add_new_item_creates_row_cross_session(self, service):
        """add_item_to_inventory creates new inventory row — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=1)
                player = await _seed_player(session_a, user_id=1, guild_id=100)
                player_id = player.id

                # 1 mock — _validate_item_exists: needs Ship/Item ARRAY-column tables
                # (cannot be seeded in SQLite — see AGENTS.md §SQLite Compatibility)
                with patch.object(service, "_validate_item_exists", new=AsyncMock(return_value=True)):
                    result = await service.add_item_to_inventory(
                        session_a, player_id=player_id, item_type="module", item_name="E2 Exoclad", quantity=2
                    )

            assert result["quantity_added"] == 2
            assert result["new_total_quantity"] == 2

            async with factory() as session_b:
                result_b = await session_b.execute(
                    select(PlayerInventory).where(
                        and_(
                            PlayerInventory.player_id == player_id,
                            PlayerInventory.item_name == "E2 Exoclad",
                        )
                    )
                )
                inv = result_b.scalars().first()
                assert inv is not None, "Inventory row should have been created"
                assert inv.quantity == 2, f"Expected quantity=2; got {inv.quantity}"
                assert inv.item_type == "module"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_add_existing_item_increments_quantity_cross_session(self, service):
        """add_item_to_inventory increments quantity for existing item — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=2)
                player = await _seed_player(session_a, user_id=2, guild_id=100)
                player_id = player.id
                await _seed_inventory_item(session_a, player_id, "module", "Telta Quickscan", quantity=3)

                # 1 mock — _validate_item_exists: ARRAY-column tables unavailable in SQLite
                with patch.object(service, "_validate_item_exists", new=AsyncMock(return_value=True)):
                    result = await service.add_item_to_inventory(
                        session_a, player_id=player_id, item_type="module", item_name="Telta Quickscan", quantity=2
                    )

            assert result["new_total_quantity"] == 5

            async with factory() as session_b:
                result_b = await session_b.execute(
                    select(PlayerInventory).where(
                        and_(
                            PlayerInventory.player_id == player_id,
                            PlayerInventory.item_name == "Telta Quickscan",
                        )
                    )
                )
                inv = result_b.scalars().first()
                assert inv is not None
                assert inv.quantity == 5, f"Expected quantity=5 (3+2); got {inv.quantity}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_add_item_commit_false_requires_explicit_commit_cross_session(self, service):
        """add_item_to_inventory with commit=False needs explicit commit — cross-session check."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=3)
                player = await _seed_player(session_a, user_id=3, guild_id=100)
                player_id = player.id

                # 1 mock — _validate_item_exists: ARRAY-column tables unavailable in SQLite
                with patch.object(service, "_validate_item_exists", new=AsyncMock(return_value=True)):
                    await service.add_item_to_inventory(
                        session_a,
                        player_id=player_id,
                        item_type="primary_weapon",
                        item_name="Nirai Impulse EX 1",
                        quantity=1,
                        commit=False,
                    )
                # Explicit commit — simulating router's transaction ownership
                await session_a.commit()

            async with factory() as session_b:
                result_b = await session_b.execute(
                    select(PlayerInventory).where(
                        and_(
                            PlayerInventory.player_id == player_id,
                            PlayerInventory.item_name == "Nirai Impulse EX 1",
                        )
                    )
                )
                inv = result_b.scalars().first()
                assert inv is not None, "Inventory row should persist after explicit commit"
                assert inv.quantity == 1
        finally:
            await engine.dispose()


# ---------------------------------------------------------------------------
# remove_item_from_inventory integration tests
# ---------------------------------------------------------------------------
# remove_item_from_inventory:
#   - reads player and existing inventory row
#   - decrements quantity or deletes row if quantity reaches 0
#   - commit controlled by caller when commit=False


class TestRemoveItemFromInventoryIntegration:
    """Cross-session persistence tests for InventoryService.remove_item_from_inventory."""

    @pytest.fixture
    def service(self):
        from services.inventory_service import InventoryService

        return InventoryService()

    @pytest.mark.asyncio
    async def test_remove_item_decrements_quantity_cross_session(self, service):
        """remove_item decrements inventory quantity — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=10)
                player = await _seed_player(session_a, user_id=10, guild_id=200)
                player_id = player.id
                await _seed_inventory_item(session_a, player_id, "module", "E2 Exoclad", quantity=5)

                result = await service.remove_item_from_inventory(
                    session_a, player_id=player_id, item_type="module", item_name="E2 Exoclad", quantity=3
                )

            assert result["quantity_removed"] == 3
            assert result["new_quantity"] == 2

            async with factory() as session_b:
                result_b = await session_b.execute(
                    select(PlayerInventory).where(
                        and_(
                            PlayerInventory.player_id == player_id,
                            PlayerInventory.item_name == "E2 Exoclad",
                        )
                    )
                )
                inv = result_b.scalars().first()
                assert inv is not None
                assert inv.quantity == 2, f"Expected quantity=2 (5-3); got {inv.quantity}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_remove_item_full_quantity_deletes_row_cross_session(self, service):
        """remove_item with full quantity deletes the inventory row — cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=11)
                player = await _seed_player(session_a, user_id=11, guild_id=200)
                player_id = player.id
                await _seed_inventory_item(session_a, player_id, "primary_weapon", "Micro Gun MK I", quantity=1)

                result = await service.remove_item_from_inventory(
                    session_a, player_id=player_id, item_type="primary_weapon", item_name="Micro Gun MK I", quantity=1
                )

            assert result["item_completely_removed"] is True

            async with factory() as session_b:
                result_b = await session_b.execute(
                    select(PlayerInventory).where(
                        and_(
                            PlayerInventory.player_id == player_id,
                            PlayerInventory.item_name == "Micro Gun MK I",
                        )
                    )
                )
                inv = result_b.scalars().first()
                assert inv is None, "Inventory row should be deleted when removing all copies"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_remove_item_insufficient_quantity_raises(self, service):
        """remove_item raises ValueError when quantity exceeds available — no mutation."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=12)
                player = await _seed_player(session_a, user_id=12, guild_id=200)
                player_id = player.id
                await _seed_inventory_item(session_a, player_id, "module", "Shield Module", quantity=2)

                with pytest.raises(ValueError, match="Insufficient quantity"):
                    await service.remove_item_from_inventory(
                        session_a, player_id=player_id, item_type="module", item_name="Shield Module", quantity=10
                    )

            async with factory() as session_b:
                result_b = await session_b.execute(
                    select(PlayerInventory).where(
                        and_(
                            PlayerInventory.player_id == player_id,
                            PlayerInventory.item_name == "Shield Module",
                        )
                    )
                )
                inv = result_b.scalars().first()
                assert inv is not None
                assert inv.quantity == 2, "Quantity should be unchanged on error"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_remove_item_not_found_raises(self, service):
        """remove_item raises ValueError when item not in inventory."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=13)
                player = await _seed_player(session_a, user_id=13, guild_id=200)
                player_id = player.id

                with pytest.raises(ValueError, match="does not have"):
                    await service.remove_item_from_inventory(
                        session_a, player_id=player_id, item_type="module", item_name="NonExistentItem", quantity=1
                    )
        finally:
            await engine.dispose()


# ---------------------------------------------------------------------------
# transfer_item_between_players integration tests
# ---------------------------------------------------------------------------
# transfer_item_between_players:
#   - validates both players (same guild check)
#   - calls remove_item_from_inventory (commit=False)
#   - calls add_item_to_inventory (commit=False)
#   - does NOT commit — router owns the commit
# Tests issue db.commit() after calling the service.


class TestTransferItemBetweenPlayersIntegration:
    """Cross-session persistence tests for InventoryService.transfer_item_between_players."""

    @pytest.fixture
    def service(self):
        from services.inventory_service import InventoryService

        return InventoryService()

    @pytest.mark.asyncio
    async def test_transfer_item_source_decremented_cross_session(self, service):
        """transfer_item removes item from source player — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=20)
                await _seed_user(session_a, user_id=21)
                from_player = await _seed_player(session_a, user_id=20, guild_id=300)
                to_player = await _seed_player(session_a, user_id=21, guild_id=300)
                from_id = from_player.id
                to_id = to_player.id
                await _seed_inventory_item(session_a, from_id, "module", "E2 Exoclad", quantity=3)

                # 1 mock — _validate_item_exists: ARRAY-column tables unavailable in SQLite
                # (called by add_item_to_inventory on the target player)
                with patch.object(service, "_validate_item_exists", new=AsyncMock(return_value=True)):
                    await service.transfer_item_between_players(
                        session_a,
                        from_player_id=from_id,
                        to_player_id=to_id,
                        item_type="module",
                        item_name="E2 Exoclad",
                        quantity=2,
                    )
                    await session_a.commit()

            # Source should have 1 remaining
            async with factory() as session_b:
                result_b = await session_b.execute(
                    select(PlayerInventory).where(
                        and_(
                            PlayerInventory.player_id == from_id,
                            PlayerInventory.item_name == "E2 Exoclad",
                        )
                    )
                )
                inv = result_b.scalars().first()
                assert inv is not None
                assert inv.quantity == 1, f"Expected source quantity=1 (3-2); got {inv.quantity}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_transfer_item_target_receives_item_cross_session(self, service):
        """transfer_item adds item to target player — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=22)
                await _seed_user(session_a, user_id=23)
                from_player = await _seed_player(session_a, user_id=22, guild_id=300)
                to_player = await _seed_player(session_a, user_id=23, guild_id=300)
                from_id = from_player.id
                to_id = to_player.id
                await _seed_inventory_item(session_a, from_id, "primary_weapon", "Nirai Impulse EX 1", quantity=2)

                # 1 mock — _validate_item_exists: ARRAY-column tables unavailable in SQLite
                with patch.object(service, "_validate_item_exists", new=AsyncMock(return_value=True)):
                    await service.transfer_item_between_players(
                        session_a,
                        from_player_id=from_id,
                        to_player_id=to_id,
                        item_type="primary_weapon",
                        item_name="Nirai Impulse EX 1",
                        quantity=1,
                    )
                    await session_a.commit()

            # Target should now own 1 copy
            async with factory() as session_b:
                result_b = await session_b.execute(
                    select(PlayerInventory).where(
                        and_(
                            PlayerInventory.player_id == to_id,
                            PlayerInventory.item_name == "Nirai Impulse EX 1",
                        )
                    )
                )
                inv = result_b.scalars().first()
                assert inv is not None, "Target player should have received the item"
                assert inv.quantity == 1, f"Expected target quantity=1; got {inv.quantity}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_transfer_item_total_quantity_conserved_cross_session(self, service):
        """transfer_item conserves total item quantity — cross-session check."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=24)
                await _seed_user(session_a, user_id=25)
                from_player = await _seed_player(session_a, user_id=24, guild_id=300)
                to_player = await _seed_player(session_a, user_id=25, guild_id=300)
                from_id = from_player.id
                to_id = to_player.id
                # Source has 4, target already has 2
                await _seed_inventory_item(session_a, from_id, "module", "Telta Quickscan", quantity=4)
                await _seed_inventory_item(session_a, to_id, "module", "Telta Quickscan", quantity=2)

                # 1 mock — _validate_item_exists: ARRAY-column tables unavailable in SQLite
                with patch.object(service, "_validate_item_exists", new=AsyncMock(return_value=True)):
                    await service.transfer_item_between_players(
                        session_a,
                        from_player_id=from_id,
                        to_player_id=to_id,
                        item_type="module",
                        item_name="Telta Quickscan",
                        quantity=3,
                    )
                    await session_a.commit()

            async with factory() as session_b:
                from_result = await session_b.execute(
                    select(PlayerInventory).where(
                        and_(
                            PlayerInventory.player_id == from_id,
                            PlayerInventory.item_name == "Telta Quickscan",
                        )
                    )
                )
                to_result = await session_b.execute(
                    select(PlayerInventory).where(
                        and_(
                            PlayerInventory.player_id == to_id,
                            PlayerInventory.item_name == "Telta Quickscan",
                        )
                    )
                )
                from_inv = from_result.scalars().first()
                to_inv = to_result.scalars().first()

                from_qty = from_inv.quantity if from_inv else 0
                to_qty = to_inv.quantity if to_inv else 0

                # Total should be conserved: 4 + 2 = 6
                assert from_qty + to_qty == 6, f"Total quantity should be 6; got {from_qty} + {to_qty}"
                assert from_qty == 1, f"Source should have 1 (4-3); got {from_qty}"
                assert to_qty == 5, f"Target should have 5 (2+3); got {to_qty}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_transfer_item_cross_guild_raises_value_error(self, service):
        """transfer_item raises ValueError when players are in different guilds."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=26)
                await _seed_user(session_a, user_id=27)
                from_player = await _seed_player(session_a, user_id=26, guild_id=301)
                to_player = await _seed_player(session_a, user_id=27, guild_id=302)
                from_id = from_player.id
                to_id = to_player.id
                await _seed_inventory_item(session_a, from_id, "module", "E2 Exoclad", quantity=5)

                with pytest.raises(ValueError, match="same guild"):
                    await service.transfer_item_between_players(
                        session_a,
                        from_player_id=from_id,
                        to_player_id=to_id,
                        item_type="module",
                        item_name="E2 Exoclad",
                        quantity=1,
                    )

            # Source inventory should be unchanged
            async with factory() as session_b:
                result_b = await session_b.execute(
                    select(PlayerInventory).where(
                        and_(
                            PlayerInventory.player_id == from_id,
                            PlayerInventory.item_name == "E2 Exoclad",
                        )
                    )
                )
                inv = result_b.scalars().first()
                assert inv is not None
                assert inv.quantity == 5, "Source inventory should be unchanged on guild mismatch error"
        finally:
            await engine.dispose()
