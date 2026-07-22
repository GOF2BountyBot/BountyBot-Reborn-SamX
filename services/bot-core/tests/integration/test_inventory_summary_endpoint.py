"""
Integration tests for GET /api/v1/inventory/player/{player_id}/summary endpoint.

Tests the A.43 fix: InventorySummaryResponse now uses concrete-type vocabulary
(primary_weapon, secondary_weapon, turret_weapon, module, ship) instead of the
legacy alias keys (weapon, turret) that caused KeyError at runtime.

These tests use a real SQLite in-memory database (fresh per test) and patch
get_db_session to inject the test session. This exercises the full
router → service → repository → schema pipeline without a live PostgreSQL instance.

Note: SQLite FOR UPDATE is a no-op (parsed as a plain SELECT). This is acceptable
here because we are testing the schema vocabulary fix (A.43), not lock semantics.
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


def _make_cm_patcher(module_path: str, db_session: AsyncSession):
    """Patch get_db_session in the given module to yield the provided session.

    Uses ``side_effect`` (a factory) rather than ``return_value`` so EACH
    ``get_db_session()`` call gets a FRESH @asynccontextmanager — mirroring the
    gold-standard sibling (test_response_body_consistency.py).  With
    ``return_value=_fake_get_db()`` a single already-created CM is injected and a
    second call within the same request would raise RuntimeError (CM re-entry);
    the factory keeps the fake faithful to the real per-call get_db_session.
    """

    @asynccontextmanager
    async def _fake_get_db():
        yield db_session

    return patch(module_path, side_effect=_fake_get_db)


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_user(db: AsyncSession, user_id: int = 800001) -> User:
    user = User(id=user_id, discord_username=f"user_{user_id}")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _seed_guild_config(db: AsyncSession, guild_id: int = 8001) -> GuildConfig:
    config = GuildConfig(guild_id=guild_id, starting_credits=500)
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return config


async def _seed_player(
    db: AsyncSession,
    user_id: int,
    guild_id: int,
    tier: str = "Bronze",
) -> Player:
    player = Player(user_id=user_id, guild_id=guild_id, credits=500, tier=tier)
    db.add(player)
    await db.commit()
    await db.refresh(player)
    return player


async def _seed_inventory_item(
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


# ---------------------------------------------------------------------------
# App fixture helper
# ---------------------------------------------------------------------------


def _make_inventory_app() -> FastAPI:
    """Create a minimal FastAPI app containing only the inventory router."""
    app = FastAPI()
    from api.routers.inventory import router as inventory_router

    app.include_router(inventory_router, prefix="/api/v1")
    return app


# ---------------------------------------------------------------------------
# A.43 integration tests
# ---------------------------------------------------------------------------


class TestInventorySummaryEndpoint:
    """Tests for GET /api/v1/inventory/player/{player_id}/summary (A.43 fix)."""

    async def test_summary_returns_200_with_concrete_type_keys(self):
        """
        Full-stack test: seeds Player + 2 inventory rows (primary_weapon + module),
        hits the endpoint, asserts:
          - HTTP 200
          - JSON contains all 9 concrete-vocabulary fields
          - JSON does NOT contain legacy alias keys 'weapon' or 'turret'
          - Counts are correct: primary_weapon=1, module=1, others=0, total_items=2
        """
        engine, factory = await _make_sqlite_session_factory()

        # Seed data
        async with factory() as db:
            user = await _seed_user(db, user_id=800001)
            await _seed_guild_config(db, guild_id=8001)
            player = await _seed_player(db, user_id=user.id, guild_id=8001)
            await _seed_inventory_item(db, player.id, "primary_weapon", "Pulse Laser", quantity=1)
            await _seed_inventory_item(db, player.id, "module", "Shield Generator", quantity=1)
            player_id = player.id

        app = _make_inventory_app()

        async with factory() as router_db:
            with _make_cm_patcher("api.routers.inventory.get_db_session", router_db):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://testserver",
                ) as client:
                    response = await client.get(f"/api/v1/inventory/player/{player_id}/summary")

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        data = response.json()

        # All 9 concrete-vocabulary keys must be present
        expected_keys = {
            "player_id",
            "player_tier",
            "guild_id",
            "ship",
            "primary_weapon",
            "secondary_weapon",
            "turret_weapon",
            "module",
            "total_items",
        }
        for key in expected_keys:
            assert key in data, f"Expected key '{key}' missing from response: {data}"

        # Legacy alias keys must NOT appear (A.43 removes them)
        assert "weapon" not in data, f"Legacy alias key 'weapon' must not appear in response: {data}"
        assert "turret" not in data, f"Legacy alias key 'turret' must not appear in response: {data}"

        # Count assertions
        assert data["primary_weapon"] == 1, f"primary_weapon should be 1, got {data['primary_weapon']}"
        assert data["module"] == 1, f"module should be 1, got {data['module']}"
        assert data["secondary_weapon"] == 0, f"secondary_weapon should be 0, got {data['secondary_weapon']}"
        assert data["turret_weapon"] == 0, f"turret_weapon should be 0, got {data['turret_weapon']}"
        assert data["ship"] == 0, f"ship should be 0, got {data['ship']}"
        assert data["total_items"] == 2, f"total_items should be 2, got {data['total_items']}"

        # Context fields
        assert data["player_id"] == player_id
        assert data["guild_id"] == 8001
        assert data["player_tier"] == "Bronze"

        await engine.dispose()

    async def test_summary_empty_inventory(self):
        """A player with no inventory returns all-zero counts and total_items=0."""
        engine, factory = await _make_sqlite_session_factory()

        async with factory() as db:
            user = await _seed_user(db, user_id=800002)
            await _seed_guild_config(db, guild_id=8002)
            player = await _seed_player(db, user_id=user.id, guild_id=8002)
            player_id = player.id

        app = _make_inventory_app()

        async with factory() as router_db:
            with _make_cm_patcher("api.routers.inventory.get_db_session", router_db):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://testserver",
                ) as client:
                    response = await client.get(f"/api/v1/inventory/player/{player_id}/summary")

        assert response.status_code == 200
        data = response.json()
        assert data["total_items"] == 0
        assert data["primary_weapon"] == 0
        assert data["module"] == 0
        assert "weapon" not in data
        assert "turret" not in data

        await engine.dispose()

    async def test_summary_nonexistent_player_returns_400(self):
        """Requesting a summary for a nonexistent player returns 400 (ValueError) not 500."""
        engine, factory = await _make_sqlite_session_factory()
        app = _make_inventory_app()

        async with factory() as router_db:
            with _make_cm_patcher("api.routers.inventory.get_db_session", router_db):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://testserver",
                ) as client:
                    response = await client.get("/api/v1/inventory/player/999999/summary")

        # Service raises ValueError for missing player → router returns 400 (not 404).
        # DEF-T-002 fix: tightened from 'in (400, 404)' to '== 400' so future
        # drift to 404 would be caught rather than silently accepted.
        assert response.status_code == 400, (
            f"Expected 400 for missing player, got {response.status_code}: {response.text}"
        )

        await engine.dispose()
