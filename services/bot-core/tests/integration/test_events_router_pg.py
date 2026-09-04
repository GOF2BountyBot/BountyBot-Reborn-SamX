"""Integration test: POST /events/{id}/prizes with item/ship kinds against real Postgres.

Exercises the Bug 1 fix: catalog lookups run inside the transaction (no autobegin-before-begin
conflict) so item and ship prizes no longer raise InvalidRequestError on Postgres.

Connection: bountydev-db via pg_env.PG_ASYNC_URL (env vars or docker-bridge default).
Skipped when the DB is not reachable or not migrated.
"""

from __future__ import annotations

import os
import sys
import types
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
elif sys.path[0] != _SRC_DIR:
    sys.path.remove(_SRC_DIR)
    sys.path.insert(0, _SRC_DIR)

if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

if "sqlalchemy_utils" not in sys.modules:
    _sau = types.ModuleType("sqlalchemy_utils")
    _sau.UUIDType = MagicMock()
    sys.modules["sqlalchemy_utils"] = _sau

import pytest
from persist.models.game_event import GameEvent, GameEventPrize
from persist.repositories.config_repository import ConfigRepository
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.pg_env import PG_ASYNC_URL, pg_skip_reason

_PG_SKIP = pg_skip_reason()
pytestmark = pytest.mark.skipif(bool(_PG_SKIP), reason=_PG_SKIP or "")

_GUILD = 999_777_111_001
_USER = 999_777_111_002

# NullPool: each session gets a fresh connection — no pool sharing, no asyncpg errors
_pg_factory = async_sessionmaker(
    create_async_engine(PG_ASYNC_URL, poolclass=NullPool),
    class_=AsyncSession,
    expire_on_commit=False,
)


@pytest.fixture
async def pg_event():
    """Create a draft event and guild config in Postgres; delete on teardown."""
    now = datetime.now(UTC)
    # Pre-clean leftover rows from previous crashed runs
    async with _pg_factory() as db, db.begin():
        await db.execute(
            text("DELETE FROM event_results WHERE event_id IN (SELECT id FROM game_events WHERE guild_id = :gid)"),
            {"gid": _GUILD},
        )
        await db.execute(text("DELETE FROM game_events WHERE guild_id = :gid"), {"gid": _GUILD})
        await db.execute(text("DELETE FROM guild_configs WHERE guild_id = :gid"), {"gid": _GUILD})

    async with _pg_factory() as db, db.begin():
        if await ConfigRepository().get_by_guild_id(db, _GUILD) is None:
            await ConfigRepository().create_default_config(db, _GUILD, commit=False)
        ev = GameEvent(
            guild_id=_GUILD,
            type_slug="bounty_caps",
            params={},
            duration_days=7,
            state="draft",
            created_by_user_id=_USER,
            created_at=now,
            updated_at=now,
        )
        db.add(ev)
        await db.flush()
        ev_id = ev.id

    yield ev_id

    async with _pg_factory() as db, db.begin():
        ev = (await db.execute(select(GameEvent).where(GameEvent.id == ev_id))).scalar_one_or_none()
        if ev:
            # event_results has no DB CASCADE from game_events; delete explicitly
            await db.execute(text("DELETE FROM event_results WHERE event_id = :eid"), {"eid": ev_id})
            await db.execute(text("DELETE FROM game_event_prizes WHERE event_id = :eid"), {"eid": ev_id})
            await db.execute(text("DELETE FROM game_events WHERE id = :eid"), {"eid": ev_id})
        await db.execute(text("DELETE FROM guild_configs WHERE guild_id = :gid"), {"gid": _GUILD})


async def _first_item_name() -> str | None:
    async with _pg_factory() as db:
        row = (await db.execute(text("SELECT name FROM item LIMIT 1"))).first()
        return row[0] if row else None


async def _first_ship_name() -> str | None:
    async with _pg_factory() as db:
        row = (await db.execute(text("SELECT name FROM ship LIMIT 1"))).first()
        return row[0] if row else None


async def test_add_item_prize_201(pg_event):
    """add_prize with kind='item' against real Postgres returns a PrizeResponse (→201).

    Before the Bug 1 fix, the catalog lookup (get_item_details) ran BEFORE begin(),
    causing SQLAlchemy to autobegin; the subsequent begin() raised InvalidRequestError.
    """
    item_name = await _first_item_name()
    if not item_name:
        pytest.skip("No items seeded in DB")

    from api.routers.events import add_prize
    from api.schemas.events_schema import AddPrizeRequest, PrizeResponse

    body = AddPrizeRequest(kind="item", item_ref=item_name, qty=1, rank_from=1, rank_to=1)

    with (
        patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True),
        patch("api.routers.events._push_events_cache", new_callable=AsyncMock),
        patch("api.routers.events.get_db_session", new=_pg_factory),
    ):
        result = await add_prize(event_id=pg_event, body=body, guild_id=_GUILD, user_id=_USER)

    assert isinstance(result, PrizeResponse)
    assert result.kind == "item"
    assert result.item_ref == item_name

    async with _pg_factory() as db, db.begin():
        pr = (await db.execute(select(GameEventPrize).where(GameEventPrize.id == result.id))).scalar_one_or_none()
        if pr:
            await db.delete(pr)


async def test_add_ship_prize_201(pg_event):
    """add_prize with kind='ship' against real Postgres returns a PrizeResponse (→201).

    Same bug: ship lookup (ship_repo.get_by_name) ran before begin() → autobegin conflict.
    """
    ship_name = await _first_ship_name()
    if not ship_name:
        pytest.skip("No ships seeded in DB")

    from api.routers.events import add_prize
    from api.schemas.events_schema import AddPrizeRequest, PrizeResponse

    body = AddPrizeRequest(kind="ship", item_ref=ship_name, qty=1, rank_from=2, rank_to=2)

    with (
        patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True),
        patch("api.routers.events._push_events_cache", new_callable=AsyncMock),
        patch("api.routers.events.get_db_session", new=_pg_factory),
    ):
        result = await add_prize(event_id=pg_event, body=body, guild_id=_GUILD, user_id=_USER)

    assert isinstance(result, PrizeResponse)
    assert result.kind == "ship"
    assert result.item_ref == ship_name

    async with _pg_factory() as db, db.begin():
        pr = (await db.execute(select(GameEventPrize).where(GameEventPrize.id == result.id))).scalar_one_or_none()
        if pr:
            await db.delete(pr)
