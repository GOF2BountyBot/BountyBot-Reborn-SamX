"""Integration test: must_win event filtering against real Postgres (issue #120).

Verifies that for a module_activations event with must_win=True:
- A loss contrib records only 'fights' (not the scored metric).
- A win contrib records both 'fights' and the scored metric.
- fights == 2 after both contributions; scored metric == only the win's value.

Connection: bountydev-db via pg_env.PG_ASYNC_URL.
Skipped when the DB is unreachable or not migrated.
"""

from __future__ import annotations

import os
import sys
import types
from datetime import UTC, datetime
from unittest.mock import MagicMock

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

from types import SimpleNamespace

import pytest
from persist.models.game_event import GameEvent, GameEventMetric
from persist.models.guild_config import GuildConfig
from services.event_service import record
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.pg_env import PG_ASYNC_URL, pg_skip_reason

_PG_SKIP = pg_skip_reason()
pytestmark = pytest.mark.skipif(bool(_PG_SKIP), reason=_PG_SKIP or "")

_GUILD = 999_777_120_001  # unique namespace for this test module
_USER_ID = 999_777_120_101

_pg_factory = async_sessionmaker(
    create_async_engine(PG_ASYNC_URL, poolclass=NullPool),
    class_=AsyncSession,
    expire_on_commit=False,
)


async def _cleanup() -> None:
    async with _pg_factory() as db, db.begin():
        await db.execute(
            text("DELETE FROM game_event_metrics WHERE event_id IN (SELECT id FROM game_events WHERE guild_id = :gid)"),
            {"gid": _GUILD},
        )
        await db.execute(text("DELETE FROM game_events WHERE guild_id = :gid"), {"gid": _GUILD})
        await db.execute(
            text("DELETE FROM players WHERE user_id = :uid AND guild_id = :gid"),
            {"uid": _USER_ID, "gid": _GUILD},
        )
        await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": _USER_ID})
        await db.execute(text("DELETE FROM guild_configs WHERE guild_id = :gid"), {"gid": _GUILD})


async def test_must_win_loss_then_win_counts_correctly():
    """Loss adds only fights; win adds both fights and activations. fights==2, activations==win value."""
    await _cleanup()
    ev_id: int | None = None
    player_id: int | None = None

    try:
        now = datetime.now(UTC)

        # Seed: guild config + player + active must_win event
        async with _pg_factory() as db, db.begin():
            db.add(GuildConfig(guild_id=_GUILD))
            await db.flush()

            from persist.models.player import Player
            from persist.models.user import User

            db.add(User(id=_USER_ID, discord_username="MustWinTest"))
            await db.flush()
            p = Player(user_id=_USER_ID, guild_id=_GUILD, credits=1000)
            db.add(p)
            await db.flush()
            player_id = p.id

            ev = GameEvent(
                guild_id=_GUILD,
                type_slug="module_activations",
                params={"module": "emergency_system", "must_win": True},
                state="active",
                duration_days=7,
                created_at=now,
                updated_at=now,
            )
            db.add(ev)
            await db.flush()
            ev_id = ev.id

        # Contribution 1: loss (won=False) — only fights should be recorded
        async with _pg_factory() as db, db.begin():
            player = SimpleNamespace(guild_id=_GUILD, user_id=_USER_ID, tier="Silver", id=player_id)
            await record(
                db,
                player,
                {"fights": 1.0, "module_activations:emergency_system": 5.0},
                context="bounty",
                won=False,
            )

        # Contribution 2: win (won=True) — both fights and activations should be recorded
        async with _pg_factory() as db, db.begin():
            player = SimpleNamespace(guild_id=_GUILD, user_id=_USER_ID, tier="Silver", id=player_id)
            await record(
                db,
                player,
                {"fights": 1.0, "module_activations:emergency_system": 3.0},
                context="bounty",
                won=True,
            )

        # Verify: fights==2 (both contributions), activations==3 (win only, loss skipped)
        async with _pg_factory() as db:
            rows = (
                (
                    await db.execute(
                        select(GameEventMetric).where(
                            GameEventMetric.event_id == ev_id,
                            GameEventMetric.player_id == player_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            by_key = {r.metric: float(r.value) for r in rows}
            assert by_key.get("fights") == 2.0, f"fights must be 2 (both contribs): {by_key}"
            assert by_key.get("module_activations:emergency_system") == 3.0, (
                f"activations must be 3 (win only, loss 5 was skipped): {by_key}"
            )

    finally:
        await _cleanup()
