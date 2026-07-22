"""
B.34 remediation — AC-8 cross-session persistence integration tests.

Reference: ``/proj/recon/B34-remediation-spec.md`` §6.1 (20-operation inventory)

These tests cover the 20 cross-table operations required by AC-8. The
canonical test idiom is the cross-session-reload assertion:

  1. Open session A (factory()).
  2. Run the operation under test (call service or hit API via TestClient).
  3. Close session A.
  4. Open a fresh session B from the same engine.
  5. Query DB through session B — assert every row that should have
     persisted, did persist; every row that should NOT have persisted
     (e.g. on rollback paths), did not.

This is the precise idiom that would have caught B.34 (player rows
committed, ship+inventory silently rolled back). Mock-only unit tests
cannot catch this class because mocked repos return success regardless
of whether commit was called.

Infrastructure notes
====================
- SQLite-in-memory engine, fresh per test (no shared state across tests).
- Tables that have PostgreSQL ARRAY columns (Ship, Item, Module STI
  tables) are NOT included in the SQLite schema. Tests that need static
  game-data lookups mock at the repo boundary (1 mock max per test).
- Each test calls service methods directly (not via TestClient) for
  service-layer focus. The companion file
  ``test_transaction_ownership_endpoints.py`` covers HTTP-stack integration.

Mock budget
===========
Following ``tests/AGENTS.md`` rule: max 2 mocks per test. Where a test
needs both ``ship_repo.get_by_name`` (static catalog) AND
``item_repo.get_by_name`` (item table not in SQLite schema), it counts
as 2 mocks. No test exceeds this budget.

Operations covered (AC-8 / spec §6.1)
======================================
 1. Create player (first registration, B.34 reproduction)
 2. Equip item
 3. Unequip item
 4. Buy ship from shop
 5. Sell ship to shop
 6. Buy item from shop
 7. Sell item to shop
 8. Transfer item between players
 9. Transfer credits between players
10. Prestige player
11. Set active ship
12. Spawn bounty
13. Resolve bounty (winner path)
14. Expire bounty
15. Duel accept (winner path)
16. Duel decline
17. Admin give item
18. Admin remove ship
19. Guild config update
20. Negative case — forced rollback mid-flow
"""

# ---------------------------------------------------------------------------
# Path setup: ensure src/ is first on sys.path so 'services.*' / 'persist.*'
# resolve to src/ rather than tests/ packages.
#
# This is the same idiom used by test_transaction_ownership_endpoints.py.
# pytest auto-imports tests/services/__init__.py as the 'services' package
# during collection; without explicit path bookkeeping, subsequent
# `from services.X import Y` calls inside test bodies resolve to the test
# package and fail with ModuleNotFoundError.
# ---------------------------------------------------------------------------
import os
import sys

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
elif sys.path[0] != _SRC_DIR:
    sys.path.remove(_SRC_DIR)
    sys.path.insert(0, _SRC_DIR)

# Purge any stale api.*, services.*, persist.* entries pointing at tests/ packages.
for _key in list(sys.modules):
    if _key in ("api", "services", "persist") or _key.startswith(("api.", "services.", "persist.")):
        _mod = sys.modules[_key]
        _file = getattr(_mod, "__file__", "") or ""
        if _SRC_DIR not in _file:
            del sys.modules[_key]

# ---------------------------------------------------------------------------

from unittest.mock import MagicMock

import pytest
from persist.models.base import Base
from persist.models.bounty import Bounty
from persist.models.combat_log import CombatLog
from persist.models.duel_request import DuelRequest
from persist.models.guild_config import GuildConfig
from persist.models.guild_shop import GuildShop
from persist.models.player import Player
from persist.models.player_inventory import PlayerInventory
from persist.models.player_ship import PlayerShip
from persist.models.user import User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# SQLite-compatible subset of the schema (excludes ARRAY-typed Ship/Item/etc).
_SQLITE_TABLES = [
    User.__table__,
    Player.__table__,
    GuildConfig.__table__,
    GuildShop.__table__,
    PlayerInventory.__table__,
    PlayerShip.__table__,
    Bounty.__table__,  # SQLite-safe (JSON-only, no ARRAY columns); needed by promote_player→scrub_orphaned_checks
    DuelRequest.__table__,  # SQLite-safe (scalar columns only); real DuelService accept/reject cross-session
    CombatLog.__table__,  # SQLite-safe; real DuelService.accept_duel → fight_ships persists a combat log (T10)
]


# ---------------------------------------------------------------------------
# Per-test engine + session factory
# ---------------------------------------------------------------------------


async def _fresh_sqlite_factory():
    """Create a fresh in-memory SQLite engine and session factory.

    Returns (engine, factory). Caller must dispose of engine.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_SQLITE_TABLES)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


# ---------------------------------------------------------------------------
# Seed helpers — each commits in its own transaction; the seed lives in DB
# before the operation under test runs.
# ---------------------------------------------------------------------------


async def _seed_user(db: AsyncSession, user_id: int) -> User:
    user = User(id=user_id, discord_username=f"u{user_id}")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _seed_guild_config(db: AsyncSession, guild_id: int, starting_credits: int = 1000) -> GuildConfig:
    cfg = GuildConfig(guild_id=guild_id, starting_credits=starting_credits)
    db.add(cfg)
    await db.commit()
    await db.refresh(cfg)
    return cfg


async def _seed_player(
    db: AsyncSession,
    user_id: int,
    guild_id: int,
    credits: int = 1000,
    tier: str = "Bronze",
    xp: int = 0,
    prestige_count: int = 0,
) -> Player:
    p = Player(
        user_id=user_id,
        guild_id=guild_id,
        credits=credits,
        lifetime_credits=credits,
        tier=tier,
        xp=xp,
        xp_surplus=0,
        prestige_count=prestige_count,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _seed_player_ship(
    db: AsyncSession,
    player_id: int,
    ship_name: str = "Hammerhead",
    is_active: bool = True,
    weapons: list[str] | None = None,
    modules: list[str] | None = None,
    turrets: list[str] | None = None,
) -> PlayerShip:
    s = PlayerShip(
        player_id=player_id,
        ship_name=ship_name,
        is_active=is_active,
        weapons=weapons or [],
        modules=modules or [],
        turrets=turrets or [],
        secondary_weapons=[],
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _seed_inventory(
    db: AsyncSession,
    player_id: int,
    item_type: str,
    item_name: str,
    quantity: int = 1,
) -> PlayerInventory:
    inv = PlayerInventory(player_id=player_id, item_type=item_type, item_name=item_name, quantity=quantity)
    db.add(inv)
    await db.commit()
    await db.refresh(inv)
    return inv


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
    s = GuildShop(
        guild_id=guild_id,
        tier=tier,
        tech_level=tech_level,
        item_type=item_type,
        item_name=item_name,
        quantity=quantity,
        price=price,
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _seed_bounty(
    db: AsyncSession,
    guild_id: int,
    *,
    answer: str = "SOL",
    checked: dict | None = None,
    reward: int = 1000,
    reward_per_sys: int = 100,
    status: str = "active",
    division: str = "bronze",
) -> Bounty:
    b = Bounty(
        guild_id=guild_id,
        division=division,
        criminal_name="Viper",
        criminal_faction="terran",
        route=[answer],
        answer=answer,
        reward=reward,
        reward_per_sys=reward_per_sys,
        checked=checked if checked is not None else {},
        tech_level=1,
        criminal_ship=None,
        status=status,
    )
    db.add(b)
    await db.commit()
    await db.refresh(b)
    return b


async def _seed_duel(
    db: AsyncSession,
    guild_id: int,
    challenger_id: int,
    target_id: int,
    *,
    stakes: int = 500,
    status: str = "pending",
) -> DuelRequest:
    d = DuelRequest(
        guild_id=guild_id,
        challenger_id=challenger_id,
        target_id=target_id,
        stakes=stakes,
        status=status,
    )
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return d


# ---------------------------------------------------------------------------
# Op 1: Create player (first registration) — B.34 cross-session reproduction
# ---------------------------------------------------------------------------


class TestOp01CreatePlayer:
    """The B.34 canonical test: create a player, close session, open fresh
    session, assert player + ship + inventory all persisted."""

    async def test_create_player_persists_all_starter_state_cross_session(self):
        """B.34 fix verification: first registration produces players row +
        player_ships row + player_inventories rows + non-null active_ship_id,
        ALL visible after the originating session closes."""
        engine, factory = await _fresh_sqlite_factory()

        # Seed: guild config + user pre-exist
        async with factory() as db:
            await _seed_guild_config(db, guild_id=10001, starting_credits=5000)
            user = await _seed_user(db, user_id=70001)
            user_id = user.id

        # Operation under test: simulate the fix's atomic block.
        # We bypass _create_starter_loadout's choke-point dependency on the
        # static item catalog (ARRAY tables) by writing the equivalent state
        # directly: this validates the transactional boundary of the route's
        # db.begin() pattern, which is what B.34 actually broke.
        from persist.repositories.player_repository import PlayerRepository
        from persist.repositories.user_repository import UserRepository

        player_repo = PlayerRepository()
        user_repo = UserRepository()

        async with factory() as db, db.begin():  # The route-level wrapper added in B.34 fix.
            # 1. Get-or-create user (idempotent on existing user) — commit=False participant
            _ = await user_repo.get_or_create_user(db, user_id, "test", commit=False)

            # 2. Add player — commit=False participant
            new_player = Player(
                user_id=user_id,
                guild_id=10001,
                credits=5000,
                lifetime_credits=5000,
                tier="Bronze",
                xp=0,
                xp_surplus=0,
            )
            new_player = await player_repo.add(db, new_player, commit=False)

            # 3. Add a player_ship row (Betty starter) — flush-only via repo
            from persist.repositories.player_ship_repository import PlayerShipRepository

            ps_repo = PlayerShipRepository()
            starter_ship = await ps_repo.create_or_update(
                db,
                {
                    "player_id": new_player.id,
                    "ship_name": "Betty",
                    "is_active": True,
                    "weapons": ["Nirai Impulse EX 1"],
                    "modules": ["E2 Exoclad", "Telta Quickscan"],
                    "turrets": [],
                    "secondary_weapons": [],
                },
                commit=False,
            )

            # 4. Update active_ship_id — commit=False
            await player_repo.update_active_ship(db, new_player.id, starter_ship.id, commit=False)

            # 5. Add 4 inventory rows — commit=False
            from persist.repositories.inventory_repository import InventoryRepository

            inv_repo = InventoryRepository()
            for itype, iname in [
                ("primary_weapon", "Nirai Impulse EX 1"),
                ("module", "E2 Exoclad"),
                ("module", "Telta Quickscan"),
                ("primary_weapon", "Micro Gun MK I"),
            ]:
                await inv_repo.add_item(db, new_player.id, itype, iname, quantity=1, commit=False)

            created_player_id = new_player.id

        # Cross-session reload — fresh session, query, assert
        async with factory() as fresh_db:
            # Player row?
            res = await fresh_db.execute(select(Player).where(Player.id == created_player_id))
            persisted_player = res.scalars().first()
            assert persisted_player is not None, "Player row should persist after session close"
            assert persisted_player.credits == 5000
            assert persisted_player.tier == "Bronze"
            assert persisted_player.active_ship_id is not None, (
                "B.34 regression — players.active_ship_id was silently rolled back"
            )

            # Player_ship row?
            res = await fresh_db.execute(select(PlayerShip).where(PlayerShip.player_id == created_player_id))
            ships = list(res.scalars().all())
            assert len(ships) == 1, f"B.34 regression — expected 1 player_ships row, got {len(ships)}: silent rollback?"
            assert ships[0].ship_name == "Betty"
            assert ships[0].is_active is True

            # Player_inventory rows?
            res = await fresh_db.execute(select(PlayerInventory).where(PlayerInventory.player_id == created_player_id))
            inv = list(res.scalars().all())
            assert len(inv) == 4, f"B.34 regression — expected 4 inventory rows, got {len(inv)}: silent rollback?"
            inv_names = sorted({i.item_name for i in inv})
            assert "Nirai Impulse EX 1" in inv_names
            assert "Micro Gun MK I" in inv_names

        await engine.dispose()


# ---------------------------------------------------------------------------
# Op 11: Set active ship — direct repo cross-session test
# (Listed early because Op 2/3 use it as setup)
# ---------------------------------------------------------------------------


class TestOp11SetActiveShip:
    """Set active ship: players.active_ship_id updated AND
    player_ships.is_active flipped on both old and new — visible after
    session close."""

    async def test_set_active_ship_persists_atomically_cross_session(self):
        engine, factory = await _fresh_sqlite_factory()

        async with factory() as db:
            user = await _seed_user(db, user_id=11001)
            await _seed_guild_config(db, guild_id=11001)
            player = await _seed_player(db, user_id=user.id, guild_id=11001)
            old_ship = await _seed_player_ship(db, player.id, ship_name="Old", is_active=True)
            new_ship = await _seed_player_ship(db, player.id, ship_name="New", is_active=False)
            player_id = player.id
            old_ship_id = old_ship.id
            new_ship_id = new_ship.id

        from persist.repositories.player_repository import PlayerRepository
        from persist.repositories.player_ship_repository import PlayerShipRepository

        player_repo = PlayerRepository()
        ps_repo = PlayerShipRepository()

        async with factory() as db, db.begin():
            await ps_repo.set_active_ship(db, player_id=player_id, ship_id=new_ship_id, commit=False)
            await player_repo.update_active_ship(db, player_id, new_ship_id, commit=False)

        # Cross-session verify
        async with factory() as fresh_db:
            res = await fresh_db.execute(select(Player).where(Player.id == player_id))
            p = res.scalars().first()
            assert p.active_ship_id == new_ship_id, "players.active_ship_id should reflect new ship"

            res = await fresh_db.execute(select(PlayerShip).where(PlayerShip.id == old_ship_id))
            old = res.scalars().first()
            assert old.is_active is False, "Old ship should be deactivated"

            res = await fresh_db.execute(select(PlayerShip).where(PlayerShip.id == new_ship_id))
            new = res.scalars().first()
            assert new.is_active is True, "New ship should be activated"

        await engine.dispose()


# ---------------------------------------------------------------------------
# Op 9: Transfer credits between players — cross-session
# ---------------------------------------------------------------------------


class TestOp09TransferCredits:
    """Transfer credits between players: both players' credits balances
    reflect transfer in a fresh session."""

    async def test_transfer_credits_persists_both_sides_cross_session(self):
        engine, factory = await _fresh_sqlite_factory()

        async with factory() as db:
            user_a = await _seed_user(db, user_id=12001)
            user_b = await _seed_user(db, user_id=12002)
            await _seed_guild_config(db, guild_id=12000)
            source = await _seed_player(db, user_id=user_a.id, guild_id=12000, credits=1000)
            target = await _seed_player(db, user_id=user_b.id, guild_id=12000, credits=500)
            source_id = source.id
            target_id = target.id

        from services.player_service import PlayerService

        svc = PlayerService()

        async with factory() as db, db.begin():
            await svc.transfer_credits(db, source_id, target_id, amount=200)

        # Cross-session verify
        async with factory() as fresh_db:
            res = await fresh_db.execute(select(Player).where(Player.id == source_id))
            assert res.scalars().first().credits == 800, "Source should be 1000 - 200 = 800"
            res = await fresh_db.execute(select(Player).where(Player.id == target_id))
            assert res.scalars().first().credits == 700, "Target should be 500 + 200 = 700"

        await engine.dispose()


# ---------------------------------------------------------------------------
# Op 19: Guild config update — single-table cross-session
# ---------------------------------------------------------------------------


class TestOp19GuildConfigUpdate:
    """Guild config update persists in fresh session."""

    async def test_update_starting_credits_persists_cross_session(self):
        engine, factory = await _fresh_sqlite_factory()

        async with factory() as db:
            await _seed_guild_config(db, guild_id=19001, starting_credits=500)

        # Operation: update starting_credits — config repo is a transaction-OWNER
        # (uses await db.commit() internally), so we do NOT wrap in db.begin().
        from persist.repositories.config_repository import ConfigRepository

        repo = ConfigRepository()
        async with factory() as db:
            await repo.update_starting_credits(db, guild_id=19001, new_credits=2500)

        # Cross-session verify
        async with factory() as fresh_db:
            res = await fresh_db.execute(select(GuildConfig).where(GuildConfig.guild_id == 19001))
            cfg = res.scalars().first()
            assert cfg.starting_credits == 2500

        await engine.dispose()


# ---------------------------------------------------------------------------
# Op 6: Buy item from shop — cross-session
# ---------------------------------------------------------------------------


class TestOp06BuyItemFromShop:
    """Buy item from shop: inventory row added, player credits decremented,
    shop quantity decremented — all visible in fresh session."""

    async def test_buy_item_persists_inventory_credits_shop_qty_cross_session(self):
        engine, factory = await _fresh_sqlite_factory()

        async with factory() as db:
            user = await _seed_user(db, user_id=13001)
            await _seed_guild_config(db, guild_id=13000)
            player = await _seed_player(db, user_id=user.id, guild_id=13000, credits=1000, tier="Bronze")
            shop_item = await _seed_guild_shop(
                db,
                guild_id=13000,
                item_type="primary_weapon",
                item_name="Pulse Laser",
                tier="Bronze",
                quantity=3,
                price=200,
            )
            player_id = player.id
            shop_item_id = shop_item.id

        # ShopService.purchase_item self-commits and is a transaction-owner;
        # we call it directly (its body has its own commit logic).
        from services.shop_service import ShopService

        svc = ShopService()

        # Mock 1: _get_item_base_price — static catalog tables not in SQLite schema.
        # (Used internally by ShopService; we don't need the price for this assertion path.)
        from persist.repositories.shop_repository import ShopRepository  # noqa: F401

        async with factory() as db:
            await svc.purchase_item(db, player_id=player_id, shop_item_id=shop_item_id, quantity=1)

        async with factory() as fresh_db:
            # Player credits decremented (1000 - 200)
            res = await fresh_db.execute(select(Player).where(Player.id == player_id))
            assert res.scalars().first().credits == 800

            # Inventory row added
            res = await fresh_db.execute(
                select(PlayerInventory).where(
                    (PlayerInventory.player_id == player_id) & (PlayerInventory.item_name == "Pulse Laser")
                )
            )
            inv = res.scalars().first()
            assert inv is not None, "Inventory row should have been created"
            assert inv.quantity == 1

            # Shop quantity decremented
            res = await fresh_db.execute(select(GuildShop).where(GuildShop.id == shop_item_id))
            shop = res.scalars().first()
            assert shop.quantity == 2, "Shop quantity should be 3 - 1 = 2"

        await engine.dispose()


# ---------------------------------------------------------------------------
# Op 7: Sell item to shop — cross-session
# ---------------------------------------------------------------------------


class TestOp07SellItemToShop:
    """Sell item to shop: inventory decremented (or removed), credits
    increased — all visible in fresh session."""

    async def test_sell_item_persists_inventory_credits_cross_session(self):
        engine, factory = await _fresh_sqlite_factory()

        async with factory() as db:
            user = await _seed_user(db, user_id=14001)
            await _seed_guild_config(db, guild_id=14000)
            player = await _seed_player(db, user_id=user.id, guild_id=14000, credits=100, tier="Bronze")
            await _seed_inventory(db, player.id, "primary_weapon", "Pulse Laser", quantity=1)
            player_id = player.id

        # Mocks 1+2: _get_item_base_price / _get_item_tech_level — static catalog not in SQLite.
        from services.shop_service import ShopService

        original_price = ShopService._get_item_base_price
        original_tech_level = ShopService._get_item_tech_level

        async def _mock_price(self, db, item_name):
            return 50

        async def _mock_tech_level(self, db, item_type, item_name, base_price):
            return 2

        ShopService._get_item_base_price = _mock_price
        ShopService._get_item_tech_level = _mock_tech_level

        try:
            svc = ShopService()
            # ShopService.sell_item is a transaction-PARTICIPANT (commit=False
            # internally). Caller must wrap in db.begin().
            async with factory() as db, db.begin():
                result = await svc.sell_item(db, player_id=player_id, item_name="Pulse Laser", quantity=1)
                assert result["item_name"] == "Pulse Laser"
                assert result["quantity"] == 1
        finally:
            ShopService._get_item_base_price = original_price
            ShopService._get_item_tech_level = original_tech_level

        async with factory() as fresh_db:
            # Player credits increased (100 + sale price)
            res = await fresh_db.execute(select(Player).where(Player.id == player_id))
            p = res.scalars().first()
            assert p.credits > 100, f"Credits should have increased from 100, got {p.credits}"

            # Inventory row removed (quantity was 1, sold 1)
            res = await fresh_db.execute(
                select(PlayerInventory).where(
                    (PlayerInventory.player_id == player_id) & (PlayerInventory.item_name == "Pulse Laser")
                )
            )
            assert res.scalars().first() is None, "Inventory row should be removed when quantity hits 0"

        await engine.dispose()


# ---------------------------------------------------------------------------
# Op 10: Prestige player — cross-session
# ---------------------------------------------------------------------------


class TestOp10PrestigePlayer:
    """Prestige player (B.49): XP/credits/tier reset, prestige_count++,
    EVERY existing ship deleted, entire inventory cleared, then Betty
    recreated as the active starter ship — all visible in fresh session."""

    async def test_prestige_persists_reset_state_cross_session(self):
        engine, factory = await _fresh_sqlite_factory()

        # B.48: prestige gated on the per-guild ``Prestige`` XP threshold
        # (default 50,000 XP). Seed XP comfortably above the default.
        prestige_xp = 75_000

        async with factory() as db:
            user = await _seed_user(db, user_id=15001)
            await _seed_guild_config(db, guild_id=15000)
            player = await _seed_player(
                db,
                user_id=user.id,
                guild_id=15000,
                credits=99999,
                tier="Platinum",
                xp=prestige_xp,
                prestige_count=2,
            )
            ship_pre = await _seed_player_ship(
                db, player.id, ship_name="Hammerhead", weapons=["Pulse Laser"], modules=["Shield"]
            )
            await _seed_inventory(db, player.id, "primary_weapon", "Pulse Laser", quantity=2)
            player_id = player.id
            pre_ship_id = ship_pre.id

        from services.player_service import PlayerService

        svc = PlayerService()
        # B.49: prestige calls _create_starter_loadout, which references the
        # ``ship`` and ``item`` STI tables that the SQLite test schema does
        # not include (per tests/integration/conftest.py — ARRAY columns are
        # excluded). Stub the starter-loadout helper for this integration
        # test; a dedicated unit test in test_player_service.py verifies
        # delegation. The integration assertion here covers the reset side.
        from unittest.mock import AsyncMock

        svc._create_starter_loadout = AsyncMock()
        async with factory() as db, db.begin():
            await svc.prestige_player(db, player_id)

        async with factory() as fresh_db:
            res = await fresh_db.execute(select(Player).where(Player.id == player_id))
            p = res.scalars().first()
            assert p.xp == 0
            assert p.credits == 0
            assert p.tier == "Bronze"
            assert p.prestige_count == 3
            # active_ship_id was nulled (since we stubbed _create_starter_loadout
            # the recreate step did not set a new active ship).
            assert p.active_ship_id is None

            # B.49: pre-prestige ship hull deleted (NOT preserved).
            res = await fresh_db.execute(select(PlayerShip).where(PlayerShip.id == pre_ship_id))
            assert res.scalars().first() is None, "Pre-prestige ship hull must be deleted (B.49)."

            # No PlayerShip rows remain for this player (the starter helper was stubbed).
            res = await fresh_db.execute(select(PlayerShip).where(PlayerShip.player_id == player_id))
            assert list(res.scalars().all()) == [], (
                "All player_ships rows must be removed before _create_starter_loadout runs (B.49)."
            )

            # Inventory cleared.
            res = await fresh_db.execute(select(PlayerInventory).where(PlayerInventory.player_id == player_id))
            assert list(res.scalars().all()) == []

        # Verify _create_starter_loadout was invoked exactly once with the player.
        svc._create_starter_loadout.assert_awaited_once()

        await engine.dispose()


# ---------------------------------------------------------------------------
# Op 8: Transfer item between players — cross-session
# ---------------------------------------------------------------------------


class TestOp08TransferItemBetweenPlayers:
    """Transfer item between players: both inventories reflect transfer in
    a fresh session."""

    async def test_transfer_item_persists_both_inventories_cross_session(self):
        engine, factory = await _fresh_sqlite_factory()

        async with factory() as db:
            user_a = await _seed_user(db, user_id=16001)
            user_b = await _seed_user(db, user_id=16002)
            await _seed_guild_config(db, guild_id=16000)
            from_p = await _seed_player(db, user_id=user_a.id, guild_id=16000)
            to_p = await _seed_player(db, user_id=user_b.id, guild_id=16000)
            await _seed_inventory(db, from_p.id, "primary_weapon", "Pulse Laser", quantity=2)
            from_pid = from_p.id
            to_pid = to_p.id

        from services.inventory_service import InventoryService

        svc = InventoryService()

        # Mock 1: _validate_item_exists — static catalog not in SQLite.
        original_validate = InventoryService._validate_item_exists

        async def _mock_validate(self, db, item_name, item_type):
            return True

        InventoryService._validate_item_exists = _mock_validate

        try:
            async with factory() as db, db.begin():
                await svc.transfer_item_between_players(
                    db, from_pid, to_pid, "primary_weapon", "Pulse Laser", quantity=1
                )
        finally:
            InventoryService._validate_item_exists = original_validate

        async with factory() as fresh_db:
            # Source has 1 (was 2, transferred 1)
            res = await fresh_db.execute(
                select(PlayerInventory).where(
                    (PlayerInventory.player_id == from_pid) & (PlayerInventory.item_name == "Pulse Laser")
                )
            )
            src_inv = res.scalars().first()
            assert src_inv is not None
            assert src_inv.quantity == 1

            # Target has 1
            res = await fresh_db.execute(
                select(PlayerInventory).where(
                    (PlayerInventory.player_id == to_pid) & (PlayerInventory.item_name == "Pulse Laser")
                )
            )
            tgt_inv = res.scalars().first()
            assert tgt_inv is not None
            assert tgt_inv.quantity == 1

        await engine.dispose()


# ---------------------------------------------------------------------------
# Op 17: Admin give item — cross-session
# ---------------------------------------------------------------------------


class TestOp17AdminGiveItem:
    """Admin give item: inventory row added, AdminAuditLog row written —
    both visible in fresh session.

    Note: AdminAuditLog table is in the SQLite schema only if we add it.
    For this test we focus on the inventory side (audit-log integration is
    covered by test_transaction_ownership_endpoints.py).
    """

    async def test_admin_give_item_persists_inventory_cross_session(self):
        engine, factory = await _fresh_sqlite_factory()

        async with factory() as db:
            user = await _seed_user(db, user_id=17001)
            await _seed_guild_config(db, guild_id=17000)
            player = await _seed_player(db, user_id=user.id, guild_id=17000)
            player_id = player.id

        # Direct repo call to add an inventory row inside a wrapped transaction
        from persist.repositories.inventory_repository import InventoryRepository

        inv_repo = InventoryRepository()
        async with factory() as db, db.begin():
            await inv_repo.add_item(db, player_id, "primary_weapon", "Disruptor Laser", quantity=3, commit=False)

        async with factory() as fresh_db:
            res = await fresh_db.execute(
                select(PlayerInventory).where(
                    (PlayerInventory.player_id == player_id) & (PlayerInventory.item_name == "Disruptor Laser")
                )
            )
            inv = res.scalars().first()
            assert inv is not None
            assert inv.quantity == 3

        await engine.dispose()


# ---------------------------------------------------------------------------
# Op 18: Admin remove ship — cross-session via direct service
# ---------------------------------------------------------------------------


class TestOp18AdminRemoveShip:
    """Admin remove ship: ship row gone, loadout evacuated to inventory —
    visible in fresh session."""

    async def test_admin_remove_ship_persists_evacuation_cross_session(self):
        engine, factory = await _fresh_sqlite_factory()

        async with factory() as db:
            user = await _seed_user(db, user_id=18001)
            await _seed_guild_config(db, guild_id=18000)
            player = await _seed_player(db, user_id=user.id, guild_id=18000)
            ship = await _seed_player_ship(
                db,
                player.id,
                ship_name="ToBeRemoved",
                is_active=False,
                weapons=["Pulse Laser"],
                modules=["Shield"],
            )
            other = await _seed_player_ship(db, player.id, ship_name="Other", is_active=True)
            player_id = player.id
            ship_id = ship.id
            _ = other  # so player retains a ship

        # The choke-point evacuation needs item_repo (Item table — ARRAY columns,
        # not in SQLite schema). Mock 1: item_repo.get_by_name to return a fake
        # Item-shaped object with .type set to PrimaryWeapon / Module so the
        # service knows where to put each evacuated item.
        from persist.repositories.item_repository import ItemRepository
        from persist.repositories.player_ship_repository import PlayerShipRepository

        original_item_get = ItemRepository.get_by_name_any_type

        def _fake_item(name, item_type):
            obj = MagicMock()
            obj.name = name
            obj.type = item_type
            return obj

        async def _mock_item_get_any_type(self, db, name):
            # PulseLaser → PrimaryWeapon, Shield → ArmourModule (any *Module string)
            if "Laser" in name or "Cannon" in name or "Gun" in name:
                return _fake_item(name, "PrimaryWeapon")
            return _fake_item(name, "ArmourModule")

        ItemRepository.get_by_name_any_type = _mock_item_get_any_type

        try:
            from services.loadout_consistency_service import LoadoutConsistencyService

            consistency = LoadoutConsistencyService()
            ps_repo = PlayerShipRepository()

            async with factory() as db, db.begin():
                # Re-fetch ship in this session (avoid cross-session detached-instance issue)
                fetched = await ps_repo.get_by_id(db, ship_id)
                await consistency.evacuate_ship_loadout_to_inventory(db, ship=fetched)
                await ps_repo.remove(db, fetched, commit=False)
        finally:
            ItemRepository.get_by_name_any_type = original_item_get

        async with factory() as fresh_db:
            # Ship gone
            res = await fresh_db.execute(select(PlayerShip).where(PlayerShip.id == ship_id))
            assert res.scalars().first() is None, "Ship should be removed"

            # Inventory has Pulse Laser + Shield
            res = await fresh_db.execute(select(PlayerInventory).where(PlayerInventory.player_id == player_id))
            inv = list(res.scalars().all())
            inv_names = {i.item_name for i in inv}
            assert "Pulse Laser" in inv_names, "Pulse Laser should be evacuated to inventory"
            assert "Shield" in inv_names, "Shield should be evacuated to inventory"

        await engine.dispose()


# ---------------------------------------------------------------------------
# Op 2: Equip item — cross-session via choke-point
# ---------------------------------------------------------------------------


class TestOp02EquipItem:
    """Equip item via LoadoutConsistencyService.equip_one: ship.weapons
    contains item, inventory row decremented — both visible in fresh
    session."""

    async def test_equip_persists_ship_slot_and_inventory_decrement_cross_session(self):
        engine, factory = await _fresh_sqlite_factory()

        async with factory() as db:
            user = await _seed_user(db, user_id=20001)
            await _seed_guild_config(db, guild_id=20000)
            player = await _seed_player(db, user_id=user.id, guild_id=20000)
            ship = await _seed_player_ship(db, player.id, ship_name="Hammerhead", is_active=True)
            await _seed_inventory(db, player.id, "primary_weapon", "Pulse Laser", quantity=1)
            player_id = player.id
            ship_id = ship.id

        # Mocks: item_repo.get_by_name (Item table not in SQLite) AND
        # ship_repo.get_by_name (Ship static catalog not in SQLite).
        from persist.repositories.item_repository import ItemRepository
        from persist.repositories.ship_repository import ShipRepository

        original_item_named = ItemRepository.get_by_name
        original_ship_get = ShipRepository.get_by_name

        async def _mock_item_get_by_name(self, db, name, item_type=None):
            obj = MagicMock()
            obj.name = name
            obj.type = "PrimaryWeapon"
            return obj

        async def _mock_ship_get_by_name(self, db, name):
            obj = MagicMock()
            obj.name = name
            obj.max_primaries = 2
            obj.max_modules = 2
            obj.max_turrets = 1
            obj.max_secondaries = 0
            return obj

        ItemRepository.get_by_name = _mock_item_get_by_name
        ShipRepository.get_by_name = _mock_ship_get_by_name

        try:
            from services.loadout_consistency_service import LoadoutConsistencyService

            consistency = LoadoutConsistencyService()
            async with factory() as db, db.begin():
                await consistency.equip_one(
                    db, player_id=player_id, ship_id=ship_id, item_name="Pulse Laser", equipment_type="weapons"
                )
        finally:
            ItemRepository.get_by_name = original_item_named
            ShipRepository.get_by_name = original_ship_get

        async with factory() as fresh_db:
            res = await fresh_db.execute(select(PlayerShip).where(PlayerShip.id == ship_id))
            s = res.scalars().first()
            assert "Pulse Laser" in (s.weapons or []), "Item should appear in ship.weapons"

            res = await fresh_db.execute(
                select(PlayerInventory).where(
                    (PlayerInventory.player_id == player_id) & (PlayerInventory.item_name == "Pulse Laser")
                )
            )
            inv = res.scalars().first()
            # Inventory row should be removed (quantity hit 0)
            assert inv is None, "Inventory row should be removed when quantity decremented to 0"

        await engine.dispose()


# ---------------------------------------------------------------------------
# Op 3: Unequip item — cross-session via choke-point
# ---------------------------------------------------------------------------


class TestOp03UnequipItem:
    """Unequip item via LoadoutConsistencyService.unequip_one: ship slot no
    longer contains item, inventory row added — both visible in fresh
    session."""

    async def test_unequip_persists_ship_slot_and_inventory_add_cross_session(self):
        engine, factory = await _fresh_sqlite_factory()

        async with factory() as db:
            user = await _seed_user(db, user_id=21001)
            await _seed_guild_config(db, guild_id=21000)
            player = await _seed_player(db, user_id=user.id, guild_id=21000)
            ship = await _seed_player_ship(
                db, player.id, ship_name="Hammerhead", is_active=True, weapons=["Pulse Laser"]
            )
            player_id = player.id
            ship_id = ship.id

        from persist.repositories.item_repository import ItemRepository

        original_item_named = ItemRepository.get_by_name_any_type

        async def _mock_item_any(self, db, name):
            obj = MagicMock()
            obj.name = name
            obj.type = "PrimaryWeapon"
            return obj

        ItemRepository.get_by_name_any_type = _mock_item_any

        try:
            from services.loadout_consistency_service import LoadoutConsistencyService

            consistency = LoadoutConsistencyService()
            async with factory() as db, db.begin():
                await consistency.unequip_one(db, player_id=player_id, ship_id=ship_id, item_name="Pulse Laser")
        finally:
            ItemRepository.get_by_name_any_type = original_item_named

        async with factory() as fresh_db:
            res = await fresh_db.execute(select(PlayerShip).where(PlayerShip.id == ship_id))
            s = res.scalars().first()
            assert "Pulse Laser" not in (s.weapons or []), "Item should be removed from ship.weapons"

            res = await fresh_db.execute(
                select(PlayerInventory).where(
                    (PlayerInventory.player_id == player_id) & (PlayerInventory.item_name == "Pulse Laser")
                )
            )
            inv = res.scalars().first()
            assert inv is not None, "Inventory row should be created"
            assert inv.quantity == 1

        await engine.dispose()


# ---------------------------------------------------------------------------
# Op 12: Spawn bounty — cross-session (without bounty table in SQLite this
# op is approximated by writing the cross-table guild_config update that
# spawn would write).
# ---------------------------------------------------------------------------


class TestOp12SpawnBounty:
    """Spawn persistence via the REAL bounty repository create path.

    The full ``BountyService.spawn_bounty`` orchestration (criminal select →
    A* route → loadout gen → loot roll) reads the ARRAY-column Ship/Item/graph
    catalogs that SQLite cannot host, so it is covered by the real-Postgres t4/t10
    suites.  Here we exercise the actual persistence step spawn performs — a real
    ``BountyRepository.create`` of a real ``Bounty`` ORM — and assert the row is
    visible, active, and field-correct in a fresh session (the cross-session
    contract this file exists to prove)."""

    async def test_spawned_bounty_row_persists_cross_session(self):
        engine, factory = await _fresh_sqlite_factory()

        async with factory() as db:
            await _seed_guild_config(db, guild_id=22000)

        # Operation: the real repo create that spawn_bounty step 9 uses.
        from persist.repositories.bounty_repository import BountyRepository

        repo = BountyRepository()
        async with factory() as db, db.begin():
            bounty = Bounty(
                guild_id=22000,
                division="bronze",
                criminal_name="Viper",
                criminal_faction="terran",
                route=["SOL", "ALPHA", "BETA"],
                answer="ALPHA",
                reward=1500,
                reward_per_sys=100,
                checked={"SOL": -1, "ALPHA": -1, "BETA": -1},
                tech_level=1,
                criminal_ship={
                    "ship_name": "Betty",
                    "cargo": {"item_type": "commodity", "item_name": "Iron", "quantity": 3},
                },
                status="active",
            )
            await repo.create(db, bounty, commit=False)

        # Cross-session verify the spawned bounty persisted with its fields.
        async with factory() as fresh_db:
            res = await fresh_db.execute(select(Bounty).where(Bounty.guild_id == 22000))
            b = res.scalars().first()
            assert b is not None, "spawned bounty row should persist"
            assert b.status == "active"
            assert b.answer == "ALPHA"
            assert b.route == ["SOL", "ALPHA", "BETA"]
            assert b.criminal_ship["cargo"]["item_name"] == "Iron"

        await engine.dispose()


# ---------------------------------------------------------------------------
# Op 13: Resolve bounty — approximated by atomic credits+xp+tier change.
# ---------------------------------------------------------------------------


class TestOp13ResolveBounty:
    """Bounty resolution via the REAL ``BountyService.calc_rewards`` +
    ``distribute_rewards`` — the exact write path ``check_bounty`` uses on a
    capture.  Seeds a real active Bounty whose ``checked`` map names the winner,
    resolves it through the real service, and asserts the winner's credits/xp/
    bounty_wins AND the bounty status='completed'/win_user_id all persist in a
    fresh session (replacing the previous hand-rolled credit math)."""

    async def test_winner_rewards_persist_atomically_cross_session(self):
        engine, factory = await _fresh_sqlite_factory()

        async with factory() as db:
            user = await _seed_user(db, user_id=23001)
            await _seed_guild_config(db, guild_id=23000)
            player = await _seed_player(db, user_id=user.id, guild_id=23000, credits=100, tier="Bronze", xp=0)
            player_id = player.id
            user_id = user.id
            # Seed the bounty as already found by this player (checked[answer] = player_id).
            bounty = await _seed_bounty(
                db, guild_id=23000, answer="SOL", checked={"SOL": player_id}, reward=500, reward_per_sys=100
            )
            bounty_id = bounty.id

        # Operation: the REAL resolution path.
        from services.bounty_service import BountyService

        svc = BountyService()
        async with factory() as db:
            b = await db.get(Bounty, bounty_id)
            rewards = await svc.calc_rewards(db, b)
            await svc.distribute_rewards(db, b, rewards)

        async with factory() as fresh_db:
            p = (await fresh_db.execute(select(Player).where(Player.id == player_id))).scalars().first()
            # Winner reserve + remaining consolation = full reward (single checker).
            assert p.credits == 100 + 500, "winner credits should persist (full reward on single checker)"
            assert p.xp > 0, "winner xp should persist"
            assert p.bounty_wins == 1, "bounty_wins increment should persist"

            b = (await fresh_db.execute(select(Bounty).where(Bounty.id == bounty_id))).scalars().first()
            assert b.status == "completed", "bounty status should persist as completed"
            assert b.win_user_id == user_id, "win_user_id should be the winner's Discord snowflake (User.id)"

        await engine.dispose()


# ---------------------------------------------------------------------------
# Op 14: Expire bounty — approximated by atomic single-row update
# ---------------------------------------------------------------------------


class TestOp14ExpireBounty:
    """Expire bounty via the REAL ``BountyService.expire_bounty``: an active
    bounty's status flips to 'expired' and persists cross-session (replacing the
    GuildConfig-field stand-in with the actual expiry op)."""

    async def test_expire_status_persists_cross_session(self):
        engine, factory = await _fresh_sqlite_factory()

        async with factory() as db:
            await _seed_guild_config(db, guild_id=24000, starting_credits=500)
            bounty = await _seed_bounty(db, guild_id=24000, status="active")
            bounty_id = bounty.id

        # Operation: the REAL expiry op.
        from services.bounty_service import BountyService

        svc = BountyService()
        async with factory() as db:
            updated = await svc.expire_bounty(db, bounty_id)
            assert updated is not None and updated.status == "expired"

        async with factory() as fresh_db:
            b = (await fresh_db.execute(select(Bounty).where(Bounty.id == bounty_id))).scalars().first()
            assert b.status == "expired", "expired status should persist cross-session"

        await engine.dispose()


# ---------------------------------------------------------------------------
# Op 15: Duel accept — winner side updates persist atomically.
# ---------------------------------------------------------------------------


class TestOp15DuelAccept:
    """Duel accept (winner path) via the REAL ``DuelService.accept_duel``.

    Seeds a real pending DuelRequest + two players, then resolves it through the
    real service — real credit-transfer, real duel-stat mutation, real combat.
    Combat is forced DECISIVE (challenger gets a live weapon loadout, target an
    unarmed one → target has 0 DPS and cannot win) so a REAL stakes transfer
    actually occurs and can be asserted cross-session (the old version hand-rolled
    the credit math; the sibling duel-integration accept test's stalemate hid any
    transfer).  Only ``LoadoutBuilder.from_player`` is mocked (R1-legit: it reads
    the ARRAY-column ship/weapon catalogs)."""

    async def test_accept_transfers_stakes_and_stats_persist_cross_session(self):
        from unittest.mock import AsyncMock, patch

        from services.combat_models import ShipLoadout, WeaponStats
        from services.duel_service import DuelService

        engine, factory = await _fresh_sqlite_factory()

        async with factory() as db:
            ua = await _seed_user(db, user_id=25001)
            ub = await _seed_user(db, user_id=25002)
            await _seed_guild_config(db, guild_id=25000)
            challenger = await _seed_player(db, user_id=ua.id, guild_id=25000, credits=5000)
            target = await _seed_player(db, user_id=ub.id, guild_id=25000, credits=5000)
            challenger_id = challenger.id
            target_id = target.id
            duel = await _seed_duel(db, guild_id=25000, challenger_id=challenger_id, target_id=target_id, stakes=1000)
            duel_id = duel.id

        # Decisive loadouts: challenger has a one-shot weapon (TickResolver reads
        # damage_per_shot/loading_speed_ms/range_m, NOT the legacy `dps`), target
        # is unarmed → target deals 0 damage and cannot win, so the challenger
        # kills on the first shot (winner_side==1, no stalemate).
        challenger_loadout = ShipLoadout(
            ship_name="Betty",
            base_armour=1000,
            weapons=[
                WeaponStats(name="SuperGun", dps=0.0, damage_per_shot=99999, loading_speed_ms=100, range_m=999999.0)
            ],
        )
        target_loadout = ShipLoadout(ship_name="Raptor", base_armour=10, weapons=[])

        svc = DuelService()
        async with factory() as db:
            with patch(
                "services.loadout_builder.LoadoutBuilder.from_player",
                new=AsyncMock(side_effect=[challenger_loadout, target_loadout]),
            ):
                result = await svc.accept_duel(db, duel_id=duel_id)
        # Sanity: a decisive challenger win with the real stakes transferred.
        assert result["credits_transferred"] == 1000
        assert result["fight_results"].winner_side == 1

        async with factory() as fresh_db:
            wp = (await fresh_db.execute(select(Player).where(Player.id == challenger_id))).scalars().first()
            lp = (await fresh_db.execute(select(Player).where(Player.id == target_id))).scalars().first()
            # Real transfer persisted: challenger +stakes, target -stakes.
            assert wp.credits == 6000 and lp.credits == 4000
            assert wp.duel_wins == 1 and wp.duel_credits_won == 1000
            assert lp.duel_losses == 1 and lp.duel_credits_lost == 1000
            # Credit conservation across the pair.
            assert wp.credits + lp.credits == 10000
            d = await fresh_db.get(DuelRequest, duel_id)
            assert d.status == "completed", "duel status should persist as completed"

        await engine.dispose()


# ---------------------------------------------------------------------------
# Op 16: Duel decline — single-table update; no credit transfer
# ---------------------------------------------------------------------------


class TestOp16DuelDecline:
    """Duel decline via the REAL ``DuelService.reject_duel``: the pending duel's
    status flips to 'rejected' and NO credits move — both asserted cross-session
    (replacing the previous empty no-op transaction)."""

    async def test_reject_sets_status_and_moves_no_credits_cross_session(self):
        from services.duel_service import DuelService

        engine, factory = await _fresh_sqlite_factory()

        async with factory() as db:
            ua = await _seed_user(db, user_id=26001)
            ub = await _seed_user(db, user_id=26002)
            await _seed_guild_config(db, guild_id=26000)
            challenger = await _seed_player(db, user_id=ua.id, guild_id=26000, credits=300)
            target = await _seed_player(db, user_id=ub.id, guild_id=26000, credits=400)
            challenger_id = challenger.id
            target_id = target.id
            duel = await _seed_duel(db, guild_id=26000, challenger_id=challenger_id, target_id=target_id, stakes=100)
            duel_id = duel.id

        # Operation: the REAL decline op.
        svc = DuelService()
        async with factory() as db:
            updated = await svc.reject_duel(db, duel_id=duel_id)
            assert updated.status == "rejected"

        async with factory() as fresh_db:
            d = await fresh_db.get(DuelRequest, duel_id)
            assert d.status == "rejected", "rejected status should persist cross-session"
            # Credits unchanged — decline never transfers.
            assert (
                await fresh_db.execute(select(Player).where(Player.id == challenger_id))
            ).scalars().first().credits == 300
            assert (
                await fresh_db.execute(select(Player).where(Player.id == target_id))
            ).scalars().first().credits == 400

        await engine.dispose()


# ---------------------------------------------------------------------------
# Op 4: Buy ship from shop — cross-session via shop_service
# ---------------------------------------------------------------------------


class TestOp04BuyShipFromShop:
    """Buy ship from shop: new player_ships row, credits decremented, shop
    quantity decremented — all visible in fresh session."""

    async def test_buy_ship_persists_atomically_cross_session(self):
        engine, factory = await _fresh_sqlite_factory()

        async with factory() as db:
            user = await _seed_user(db, user_id=27001)
            await _seed_guild_config(db, guild_id=27000)
            player = await _seed_player(db, user_id=user.id, guild_id=27000, credits=10000, tier="Bronze")
            await _seed_player_ship(db, player.id, ship_name="Old", is_active=True)
            shop_item = await _seed_guild_shop(
                db,
                guild_id=27000,
                item_type="ship",
                item_name="NewShip",
                tier="Bronze",
                quantity=1,
                price=5000,
            )
            player_id = player.id
            shop_item_id = shop_item.id

        # Mock 1: ShipRepository.get_by_name (static Ship catalog with ARRAY columns
        # not in SQLite integration schema).
        from persist.repositories.ship_repository import ShipRepository

        original_ship_get = ShipRepository.get_by_name

        async def _mock_ship_get(self, db, name):
            obj = MagicMock()
            obj.name = name
            obj.value = 4000
            obj.max_primaries = 2
            obj.max_modules = 2
            obj.max_turrets = 1
            obj.max_secondaries = 0
            return obj

        ShipRepository.get_by_name = _mock_ship_get

        try:
            from services.shop_service import ShopService

            svc = ShopService()
            # purchase_ship is a transaction-PARTICIPANT — caller wraps.
            async with factory() as db, db.begin():
                await svc.purchase_ship(db, player_id=player_id, shop_item_id=shop_item_id)
        finally:
            ShipRepository.get_by_name = original_ship_get

        async with factory() as fresh_db:
            res = await fresh_db.execute(select(Player).where(Player.id == player_id))
            assert res.scalars().first().credits == 5000, "Credits should be 10000 - 5000 = 5000"

            res = await fresh_db.execute(
                select(PlayerShip).where((PlayerShip.player_id == player_id) & (PlayerShip.ship_name == "NewShip"))
            )
            assert res.scalars().first() is not None, "New ship row should persist"

            res = await fresh_db.execute(select(GuildShop).where(GuildShop.id == shop_item_id))
            shop = res.scalars().first()
            # Shop quantity went 1 -> 0, the row is either still there with qty=0 or removed
            assert shop is None or shop.quantity == 0

        await engine.dispose()


# ---------------------------------------------------------------------------
# Op 5: Sell ship to shop — cross-session via shop_service
# ---------------------------------------------------------------------------


class TestOp05SellShipToShop:
    """Sell ship to shop: ship row deleted (clear_equipment=False) or
    loadout evacuated (clear_equipment=True), credits increased — visible
    in fresh session."""

    async def test_sell_inactive_ship_persists_removal_cross_session(self):
        engine, factory = await _fresh_sqlite_factory()

        async with factory() as db:
            user = await _seed_user(db, user_id=28001)
            await _seed_guild_config(db, guild_id=28000)
            player = await _seed_player(db, user_id=user.id, guild_id=28000, credits=500)
            await _seed_player_ship(db, player.id, ship_name="Active", is_active=True)
            inactive = await _seed_player_ship(db, player.id, ship_name="Inactive", is_active=False)
            player_id = player.id
            inactive_id = inactive.id

        # Mock 1: ShipRepository.get_by_name (static catalog not in SQLite schema)
        from persist.repositories.ship_repository import ShipRepository

        original_ship_get = ShipRepository.get_by_name

        async def _mock_ship_get(self, db, name):
            return None  # ship_value=0 — fine for this assertion

        ShipRepository.get_by_name = _mock_ship_get

        try:
            from services.shop_service import ShopService

            svc = ShopService()
            # sell_ship is a transaction-PARTICIPANT — caller wraps.
            async with factory() as db, db.begin():
                await svc.sell_ship(
                    db, player_id=player_id, ship_id=inactive_id, clear_equipment=False, target_tier="Bronze"
                )
        finally:
            ShipRepository.get_by_name = original_ship_get

        async with factory() as fresh_db:
            # Inactive ship gone
            res = await fresh_db.execute(select(PlayerShip).where(PlayerShip.id == inactive_id))
            assert res.scalars().first() is None, "Sold ship should be removed"

            # Active ship preserved
            res = await fresh_db.execute(
                select(PlayerShip).where((PlayerShip.player_id == player_id) & PlayerShip.is_active.is_(True))
            )
            assert res.scalars().first() is not None, "Active ship should be preserved"

        await engine.dispose()


# ---------------------------------------------------------------------------
# Op 20: Negative path — forced rollback mid-flow
# ---------------------------------------------------------------------------


class TestOp20RollbackNegativePath:
    """Forced rollback test: if any step in a wrapped transaction raises,
    NOTHING from that transaction persists in a fresh session.

    This proves the wrapping is functional — the inverse of the B.34
    silent-rollback class. With AC-7 auto-commit AND the wrapping,
    commits and rollbacks both behave correctly.
    """

    async def test_exception_mid_flow_rolls_back_all_writes(self):
        engine, factory = await _fresh_sqlite_factory()

        async with factory() as db:
            user = await _seed_user(db, user_id=29001)
            await _seed_guild_config(db, guild_id=29000)
            player = await _seed_player(db, user_id=user.id, guild_id=29000, credits=500)
            player_id = player.id

        from persist.repositories.inventory_repository import InventoryRepository
        from persist.repositories.player_repository import PlayerRepository

        inv_repo = InventoryRepository()
        player_repo = PlayerRepository()

        # Run a wrapped transaction that:
        #   1. Adds inventory row (commit=False)
        #   2. Updates credits (commit=False)
        #   3. Raises an exception → outer db.begin() must roll back BOTH writes
        with pytest.raises(RuntimeError, match="forced abort"):
            async with factory() as db, db.begin():
                await inv_repo.add_item(db, player_id, "primary_weapon", "ShouldNotPersist", 1, commit=False)
                await player_repo.update_credits(db, player_id, 999, commit=False)
                raise RuntimeError("forced abort mid-flow")

        # Cross-session verify: NOTHING persisted from the failed transaction
        async with factory() as fresh_db:
            # Player credits unchanged (still 500)
            res = await fresh_db.execute(select(Player).where(Player.id == player_id))
            assert res.scalars().first().credits == 500, "Credits update must be rolled back"

            # No inventory row was added
            res = await fresh_db.execute(
                select(PlayerInventory).where(
                    (PlayerInventory.player_id == player_id) & (PlayerInventory.item_name == "ShouldNotPersist")
                )
            )
            assert res.scalars().first() is None, "Inventory row must NOT persist after rollback"

        await engine.dispose()
