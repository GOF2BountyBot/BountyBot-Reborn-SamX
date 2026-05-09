"""
Integration tests for API-response/DB consistency (Option B refactor, 2026-04-27).

These tests defend against the SQLAlchemy identity-map anti-pattern (see
``orm_identity_map_audit.md``), where a Core UPDATE inside a repository
silently expired ORM-tracked attributes, causing service code that read
``player.credits`` after ``update_credits()`` to observe POST-update values
and double-credit the response body.

Each test exercises the full route → service → repo → DB path against a real
SQLite-in-memory database, then asserts that the credits/quantities returned
in the API response body are byte-for-byte equal to the values persisted in
the database. This is the assertion class that AsyncMock-based service tests
mask.

DESIGN NOTES (mirrored from ``test_transaction_ownership_endpoints.py``):
 - Per-test fresh SQLite engine + session factory.
 - ``api.routers.*`` get their ``get_db_session`` patched to yield a fresh
   session from the test factory each call (factory-of-CMs pattern, NOT a
   single already-consumed CM).
 - At most 2 mocks per test (typically 1: the static ship/item table that
   isn't part of the SQLite integration schema).
 - SQLite parses ``SELECT ... FOR UPDATE`` as a plain SELECT (no row lock).
   Acceptable: the bug class we are testing is identity-map confusion, NOT
   lock semantics.
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
from unittest.mock import patch

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
# Seed helpers (kept local to this test file)
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

    Uses side_effect=_fake_get_db (factory) so each call returns a fresh CM.
    """

    @asynccontextmanager
    async def _fake_get_db():
        yield db_session

    return patch(module_path, side_effect=_fake_get_db)


# ---------------------------------------------------------------------------
# Test 1: POST /shops/sell — response credits MUST equal DB credits
# ---------------------------------------------------------------------------


class TestSellItemResponseCreditsMatchDB:
    """Defends against the doubled-credit bug from the identity-map anti-pattern.

    Pre-refactor: response body returned ``original + 2*sale_value`` instead of
    ``original + sale_value`` because ``shop_service.sell_item`` re-read
    ``player.credits`` after ``update_credits()`` had silently refreshed the
    ORM-tracked instance to the post-update value.
    """

    async def test_sell_item_response_credits_match_db(self):
        engine, factory = await _make_sqlite_session_factory()

        starting_credits = 250
        sale_unit_price = 75
        quantity = 2
        expected_new_credits = starting_credits + sale_unit_price * quantity  # 250 + 150 = 400

        async with factory() as db:
            user = await _seed_user(db, user_id=950101)
            await _seed_guild_config(db, guild_id=9501)
            player = await _seed_player(db, user_id=user.id, guild_id=9501, credits=starting_credits)
            await _seed_inventory(db, player.id, "primary_weapon", "Pulse Laser", quantity=quantity)
            player_id = player.id

        app = FastAPI()
        from api.routers.shops import router as shops_router

        app.include_router(shops_router, prefix="/api/v1")

        # Mock 1: item base price (static item table not in SQLite integration schema).
        from services.shop_service import ShopService

        original_get_price = ShopService._get_item_base_price

        async def _mock_price(self, db, item_name):
            return sale_unit_price

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
                            json={
                                "player_id": player_id,
                                "item_name": "Pulse Laser",
                                "quantity": quantity,
                            },
                        )
        finally:
            ShopService._get_item_base_price = original_get_price

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()

        # Response body must exactly match the expected new balance
        assert data["remaining_credits"] == expected_new_credits, (
            f"Response 'remaining_credits' should be {expected_new_credits}, "
            f"got {data['remaining_credits']} (doubled-credit bug regression!)"
        )

        # Response body must equal DB ground truth
        async with factory() as verify_db:
            result = await verify_db.execute(select(Player).where(Player.id == player_id))
            db_player = result.scalars().first()
        assert data["remaining_credits"] == db_player.credits, (
            f"Response credits ({data['remaining_credits']}) != DB credits ({db_player.credits})"
        )
        assert db_player.credits == expected_new_credits

        await engine.dispose()


# ---------------------------------------------------------------------------
# Test 2: POST /shops/sell-ship — response credits MUST equal DB credits
# ---------------------------------------------------------------------------


class TestSellShipResponseCreditsMatchDB:
    """Same bug class as sell_item (line 636 in shop_service.py pre-refactor)."""

    async def test_sell_ship_response_credits_match_db(self):
        engine, factory = await _make_sqlite_session_factory()

        starting_credits = 500
        # Use a non-zero ship_value so the regression test is meaningful.
        # With OLD buggy code (Core UPDATE + identity-map refresh):
        #   player.credits would re-read as post-update (700), so the service
        #   would compute 700 + 200 = 900 instead of 700 → test FAILS → bug caught.
        ship_value = 200
        expected_new_credits = starting_credits + ship_value  # 700

        async with factory() as db:
            user = await _seed_user(db, user_id=950201)
            await _seed_guild_config(db, guild_id=9502)
            player = await _seed_player(db, user_id=user.id, guild_id=9502, credits=starting_credits)
            await _seed_player_ship(db, player.id, ship_name="ActiveShip", is_active=True)
            inactive = await _seed_player_ship(db, player.id, ship_name="InactiveShip", is_active=False)
            player_id = player.id
            ship_id = inactive.id

        app = FastAPI()
        from api.routers.shops import router as shops_router

        app.include_router(shops_router, prefix="/api/v1")

        # Mock 1: static ship lookup not in SQLite schema; return a mock Ship with
        # value=200 so ship_value is non-zero and the regression test is meaningful.
        # With OLD buggy code the response would be 900 (≠ 700) → test would FAIL.
        from unittest.mock import MagicMock

        from persist.repositories.ship_repository import ShipRepository

        original_ship_get = ShipRepository.get_by_name
        _mock_ship = MagicMock()
        _mock_ship.value = 200

        async def _mock_ship_get_by_name(self, db, name):
            return _mock_ship

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
                                "ship_id": ship_id,
                                "clear_equipment": False,
                                "target_tier": "Bronze",
                            },
                        )
        finally:
            ShipRepository.get_by_name = original_ship_get

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()

        assert data["remaining_credits"] == expected_new_credits, (
            f"Response 'remaining_credits' should be {expected_new_credits}, "
            f"got {data['remaining_credits']} (doubled-credit bug regression!)"
        )

        async with factory() as verify_db:
            result = await verify_db.execute(select(Player).where(Player.id == player_id))
            db_player = result.scalars().first()
        assert data["remaining_credits"] == db_player.credits, (
            f"Response credits ({data['remaining_credits']}) != DB credits ({db_player.credits})"
        )

        await engine.dispose()


# ---------------------------------------------------------------------------
# Test 3: POST /shops/purchase — defense in depth on the safe pattern
# ---------------------------------------------------------------------------


class TestPurchaseItemResponseCreditsMatchDB:
    """``shop_service.purchase_item`` already used direct ORM mutation
    (player.credits -= total_cost) before the Option B refactor, so it was
    not affected by the bug class. This test pins the current behavior so
    any future refactor that re-introduces Core UPDATE will be caught.
    """

    async def test_purchase_item_response_credits_match_db(self):
        engine, factory = await _make_sqlite_session_factory()

        starting_credits = 1000
        unit_price = 200
        quantity = 1
        expected_new_credits = starting_credits - unit_price * quantity  # 800

        async with factory() as db:
            user = await _seed_user(db, user_id=950301)
            await _seed_guild_config(db, guild_id=9503)
            player = await _seed_player(db, user_id=user.id, guild_id=9503, credits=starting_credits, tier="Bronze")
            shop_item = await _seed_guild_shop(
                db,
                guild_id=9503,
                item_type="primary_weapon",
                item_name="Ion Blaster",
                tier="Bronze",
                quantity=3,
                price=unit_price,
            )
            player_id = player.id
            shop_item_id = shop_item.id

        app = FastAPI()
        from api.routers.shops import router as shops_router

        app.include_router(shops_router, prefix="/api/v1")

        async with factory() as router_db:
            with _make_cm_patcher("api.routers.shops.get_db_session", router_db):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://testserver",
                ) as client:
                    response = await client.post(
                        "/api/v1/shops/purchase",
                        json={"player_id": player_id, "shop_item_id": shop_item_id, "quantity": quantity},
                    )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()

        assert data["remaining_credits"] == expected_new_credits, (
            f"Response 'remaining_credits' should be {expected_new_credits}, got {data['remaining_credits']}"
        )

        async with factory() as verify_db:
            result = await verify_db.execute(select(Player).where(Player.id == player_id))
            db_player = result.scalars().first()
        assert data["remaining_credits"] == db_player.credits, (
            f"Response credits ({data['remaining_credits']}) != DB credits ({db_player.credits})"
        )

        await engine.dispose()


# ---------------------------------------------------------------------------
# Test 4: POST /players/transfer — both source AND target balances
# ---------------------------------------------------------------------------


class TestTransferCreditsResponseBalancesMatchDB:
    """``player_service.transfer_credits`` already used the locally-captured-value
    pattern. This test pins the contract so a regression that switches back to
    reading ``source.credits``/``target.credits`` after update_credits() will
    be caught.
    """

    async def test_transfer_credits_response_balances_match_db(self):
        engine, factory = await _make_sqlite_session_factory()

        source_start = 500
        target_start = 100
        amount = 150
        expected_source_after = source_start - amount  # 350
        expected_target_after = target_start + amount  # 250

        async with factory() as db:
            source_user = await _seed_user(db, user_id=950401)
            target_user = await _seed_user(db, user_id=950402)
            await _seed_guild_config(db, guild_id=9504)
            source = await _seed_player(db, user_id=source_user.id, guild_id=9504, credits=source_start)
            target = await _seed_player(db, user_id=target_user.id, guild_id=9504, credits=target_start)
            source_id = source.id
            target_id = target.id

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
                            "source_player_id": source_id,
                            "target_player_id": target_id,
                            "amount": amount,
                        },
                    )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()

        # Response body asserts
        assert data["source_remaining_credits"] == expected_source_after
        assert data["target_new_credits"] == expected_target_after

        # DB ground truth — both rows
        async with factory() as verify_db:
            src_row = (await verify_db.execute(select(Player).where(Player.id == source_id))).scalars().first()
            tgt_row = (await verify_db.execute(select(Player).where(Player.id == target_id))).scalars().first()

        assert data["source_remaining_credits"] == src_row.credits, (
            f"Response source ({data['source_remaining_credits']}) != DB ({src_row.credits})"
        )
        assert data["target_new_credits"] == tgt_row.credits, (
            f"Response target ({data['target_new_credits']}) != DB ({tgt_row.credits})"
        )

        await engine.dispose()


# ---------------------------------------------------------------------------
# Test 5: Repo-level guard — update_credits returned object must reflect new value
# ---------------------------------------------------------------------------


class TestUpdateCreditsReturnsAccurateInstance:
    """Direct repo-level test: after the Option B refactor, the Player instance
    returned by ``update_credits()`` MUST carry the new credit value (not a
    stale or refreshed-to-DB-then-modified-elsewhere value)."""

    async def test_update_credits_returned_instance_has_new_value(self):
        from persist.repositories.player_repository import PlayerRepository

        engine, factory = await _make_sqlite_session_factory()

        async with factory() as db:
            user = await _seed_user(db, user_id=950501)
            await _seed_guild_config(db, guild_id=9505)
            player = await _seed_player(db, user_id=user.id, guild_id=9505, credits=100)
            player_id = player.id

            repo = PlayerRepository()
            returned = await repo.update_credits(db, player_id, 250)

            # Returned instance carries the new value
            assert returned.credits == 250

            # The previously-held instance (same identity-map row) also reflects it
            assert player.credits == 250

            # DB ground truth
            await db.refresh(player)
            assert player.credits == 250

        await engine.dispose()
