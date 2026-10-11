"""Integration test: due-aware bulk shop refresh against real Postgres (issue #119).

One bulk run of the real executor over a set of guilds, one per schedule case, with
the real ShopService.refresh_shop and real writes. Checks who gets refreshed and announced
and what shop_next_refresh_at becomes.

Patched: db_manager (bridges to the PG test DB) and ConfigRepository.list_all (keeps the
run to this module's guilds so other rows in the shared DB are never refreshed); the
second test also counts preload_static_data calls (passing through to the real one).
Gateway HTTP goes to respx.

Connection: pg_env.PG_ASYNC_URL. Skipped when the DB is unreachable or not migrated.
"""

from __future__ import annotations

import os
import sys
import types
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

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

import pytest
import respx
from persist.models.guild_config import GuildConfig
from persist.models.guild_shop import GuildShop
from persist.repositories.config_repository import ConfigRepository
from services.shop_service import ShopService
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from utils.executors.shop_refresh_executor import execute_shop_refresh_job

from tests.pg_env import PG_ASYNC_URL, pg_skip_reason

_PG_SKIP = pg_skip_reason()
pytestmark = pytest.mark.skipif(bool(_PG_SKIP), reason=_PG_SKIP or "")

_pg_factory = async_sessionmaker(
    create_async_engine(PG_ASYNC_URL, poolclass=NullPool),
    class_=AsyncSession,
    expire_on_commit=False,
)

_BASE = 999_777_119_000  # unique guild-id namespace for this module
H = timedelta(hours=1)


def _gid(n: int) -> int:
    return _BASE + n


async def _cleanup() -> None:
    async with _pg_factory() as db, db.begin():
        await db.execute(
            text("DELETE FROM guild_shops WHERE guild_id BETWEEN :lo AND :hi"), {"lo": _BASE, "hi": _BASE + 99}
        )
        await db.execute(
            text("DELETE FROM guild_configs WHERE guild_id BETWEEN :lo AND :hi"), {"lo": _BASE, "hi": _BASE + 99}
        )


async def _seed(n: int, *, next_at=None, last_restocked=None, interval=None, jitter=None) -> None:
    async with _pg_factory() as db, db.begin():
        db.add(
            GuildConfig(
                guild_id=_gid(n),
                shop_channel_id=_gid(n),  # distinct channel per guild → announcements attributable
                shop_next_refresh_at=next_at,
                shop_refresh_interval_hours=interval,
                shop_refresh_jitter_pct=jitter,
            )
        )
        await db.flush()
        if last_restocked is not None:
            db.add(
                GuildShop(
                    guild_id=_gid(n),
                    tier="Bronze",
                    tech_level=1,
                    item_type="module",
                    item_name="Stale Stock",
                    quantity=1,
                    price=1,
                    last_restocked=last_restocked,
                )
            )


async def _next_at(n: int):
    async with _pg_factory() as db:
        return (
            await db.execute(select(GuildConfig.shop_next_refresh_at).where(GuildConfig.guild_id == _gid(n)))
        ).scalar_one()


async def _has_stale_stock(n: int) -> bool:
    async with _pg_factory() as db:
        row = await db.execute(
            select(GuildShop.id).where(GuildShop.guild_id == _gid(n), GuildShop.item_name == "Stale Stock")
        )
        return row.first() is not None


def _fake_db_manager():
    @asynccontextmanager
    async def _session():
        async with _pg_factory() as session:
            yield session

    fake = MagicMock()
    fake.get_session = MagicMock(side_effect=_session)
    return fake


_real_list_all = ConfigRepository.list_all


async def _own_guilds_only(self, db):
    return [c for c in await _real_list_all(self, db) if _BASE <= c.guild_id < _BASE + 100]


async def test_bulk_run_refreshes_only_due_guilds_and_rolls_schedule():
    await _cleanup()
    now = datetime.now(UTC)
    try:
        await _seed(1, next_at=now - timedelta(minutes=1), jitter=0)  # due; jitter 0 → next exactly +6h
        await _seed(2, next_at=now + H, last_restocked=now - 7 * H)  # stored next in future → not due
        await _seed(3, last_restocked=now - 7 * H, jitter=0)  # NULL next: last+6h is past → due
        await _seed(4, last_restocked=now - 5 * H)  # NULL next: last+6h is future → not due
        await _seed(5)  # NULL next, no shops → due now
        await _seed(6, next_at=now - 72 * H, jitter=0)  # long downtime → next = now + 6h, not due + 6h
        await _seed(7, last_restocked=now - 1.5 * H, interval=1, jitter=0)  # 1h override → due
        await _seed(8, next_at=now - timedelta(minutes=1), interval=2, jitter=50)  # gap in [1h, 3h]

        announced: dict[int, int] = {}

        def _count(request):
            channel = int(request.url.path.split("/channels/")[1].split("/")[0])
            announced[channel] = announced.get(channel, 0) + 1
            return respx.MockResponse(200, json={"ok": True})

        with respx.mock(assert_all_called=False) as router:
            router.post(url__regex=r".*/channels/\d+/messages").mock(side_effect=_count)
            router.post(url__regex=r".*/internal/autocomplete/.*").mock(return_value=respx.MockResponse(200))
            with (
                patch("persist.database.manager.db_manager", _fake_db_manager()),
                patch.object(ConfigRepository, "list_all", _own_guilds_only),
            ):
                before = datetime.now(UTC)
                result = await execute_shop_refresh_job("t119-pg", {})
                after = datetime.now(UTC)

        due, not_due = {1, 3, 5, 6, 7, 8}, {2, 4}
        assert set(result["results"]) == {_gid(n) for n in due}
        assert result["guilds_skipped"] == len(not_due)
        for n in due:
            assert announced.get(_gid(n)) == 4, f"guild {n}: expected 4 tier announcements, got {announced}"
        for n in not_due:
            assert _gid(n) not in announced, f"guild {n} is not due but was announced"
            assert await _has_stale_stock(n), f"guild {n} is not due but its shop was refreshed"
        assert not await _has_stale_stock(3), "due guild 3 kept its stale stock"

        assert await _next_at(1) == (now - timedelta(minutes=1)) + 6 * H  # rolled from due time, no drift
        assert await _next_at(2) == now + H  # untouched
        assert await _next_at(4) is None  # untouched
        assert await _next_at(3) == (now - 7 * H) + 6 * H + 6 * H  # NULL fallback due time + gap
        assert before + 6 * H <= await _next_at(5) <= after + 6 * H  # due "now" → now + gap
        assert before + 6 * H <= await _next_at(6) <= after + 6 * H  # missed cycles → restart from now
        assert await _next_at(7) == (now - 1.5 * H) + H + H  # override used for both due check and gap
        nxt8, due8 = await _next_at(8), now - timedelta(minutes=1)
        assert due8 + H <= nxt8 <= due8 + 3 * H  # 2h ± 50%
    finally:
        await _cleanup()


async def test_second_tick_right_after_is_a_no_op():
    """A re-fire straight after a refresh finds nothing due (the first run rolled the schedule)
    and does not preload static data."""
    await _cleanup()
    preloads: list[int] = []
    _real_preload = ShopService.preload_static_data

    async def _counting_preload(self, db):
        preloads.append(1)
        await _real_preload(self, db)

    try:
        await _seed(20, next_at=datetime.now(UTC) - timedelta(minutes=1))
        with respx.mock(assert_all_called=False) as router:
            router.post(url__regex=r".*").mock(return_value=respx.MockResponse(200, json={"ok": True}))
            with (
                patch("persist.database.manager.db_manager", _fake_db_manager()),
                patch.object(ConfigRepository, "list_all", _own_guilds_only),
                patch.object(ShopService, "preload_static_data", _counting_preload),
            ):
                first = await execute_shop_refresh_job("t119-pg-1", {})
                second = await execute_shop_refresh_job("t119-pg-2", {})
        assert first["guilds_refreshed"] == 1
        assert second == {"status": "success", "guilds_refreshed": 0, "guilds_skipped": 1, "results": {}}
        assert len(preloads) == 1, "a tick with nothing due must not preload static data"
    finally:
        await _cleanup()
