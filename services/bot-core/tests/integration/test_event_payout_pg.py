"""Integration test: full event payout + medals against real Postgres.

Exercises three bugs:
  Bug 2: end_event called inside async with session.begin(): must not corrupt the
         transaction via an internal commit (update_player_credits commit=False fix).
  Bug 3: medals() EventResult.qualified == 1 filter must work on Postgres Integer column
         (was EventResult.qualified.is_(True) → DatatypeMismatchError).

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

import pytest
import respx
from httpx import Response
from persist.models.admin_audit_log import AdminAuditLog
from persist.models.game_event import EventResult, GameEvent, GameEventMetric, GameEventPrize
from persist.models.player import Player
from persist.models.user import User
from persist.repositories.config_repository import ConfigRepository
from services.event_service import end_event, medals
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.pg_env import PG_ASYNC_URL, pg_skip_reason

_PG_SKIP = pg_skip_reason()
pytestmark = pytest.mark.skipif(bool(_PG_SKIP), reason=_PG_SKIP or "")

_GUILD = 999_777_222_001
_DISCORD_A = 999_777_222_101
_DISCORD_B = 999_777_222_102

_GATEWAY_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
_GATEWAY_PORT = os.getenv("GATEWAY_PORT", "7999")
_MEMBERS_URL = f"http://{_GATEWAY_HOST}:{_GATEWAY_PORT}/api/v1/guilds/{_GUILD}/members?limit=5000"
_CHANNEL_URL_TMPL = f"http://{_GATEWAY_HOST}:{_GATEWAY_PORT}/api/v1/channels/{{channel_id}}/messages"

# NullPool: each session gets a fresh connection — no pool sharing, no asyncpg "another operation in progress"
_pg_factory = async_sessionmaker(
    create_async_engine(PG_ASYNC_URL, poolclass=NullPool),
    class_=AsyncSession,
    expire_on_commit=False,
)


async def _cleanup(ev_id: int | None = None) -> None:
    async with _pg_factory() as db, db.begin():
        if ev_id is not None:
            await db.execute(text("DELETE FROM event_results WHERE event_id = :eid"), {"eid": ev_id})
            await db.execute(text("DELETE FROM game_events WHERE id = :eid"), {"eid": ev_id})
        else:
            await db.execute(text("DELETE FROM game_events WHERE guild_id = :gid"), {"gid": _GUILD})
        await db.execute(
            text("DELETE FROM admin_audit_logs WHERE resource_type = 'event' AND guild_id = :gid"),
            {"gid": _GUILD},
        )
        await db.execute(
            text("DELETE FROM players WHERE user_id = ANY(:ids)"),
            {"ids": [_DISCORD_A, _DISCORD_B]},
        )
        await db.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": [_DISCORD_A, _DISCORD_B]})
        await db.execute(text("DELETE FROM guild_configs WHERE guild_id = :gid"), {"gid": _GUILD})


async def test_payout_credits_and_medals():
    """Two players tied at 1st; 1st-place credit prize + participation prize.

    Verifies (all on Postgres):
    - end_event inside async with session.begin(): completes without aborting the transaction.
    - Both players' credits and lifetime_credits increase by 600 (500 + 100).
    - Two EventResult rows written, state=ended, announcement non-None.
    - One event_payout AdminAuditLog row.
    - medals() returns both players with gold=1 (Bug 3: qualified==1 on PG Integer column).
    """
    now = datetime.now(UTC)
    ev_id: int | None = None
    player_a_id: int | None = None
    player_b_id: int | None = None

    await _cleanup()  # pre-clean any leftover from previous run

    try:
        # --- Seed ---
        async with _pg_factory() as db, db.begin():
            cfg = await ConfigRepository().create_default_config(db, _GUILD, commit=False)
            cfg.discussion_channel_id = 42000

            db.add(User(id=_DISCORD_A, discord_username="PayoutA"))
            db.add(User(id=_DISCORD_B, discord_username="PayoutB"))
            await db.flush()

            pa = Player(user_id=_DISCORD_A, guild_id=_GUILD, credits=1000, lifetime_credits=5000)
            pb = Player(user_id=_DISCORD_B, guild_id=_GUILD, credits=1000, lifetime_credits=5000)
            db.add(pa)
            db.add(pb)
            await db.flush()
            player_a_id = pa.id
            player_b_id = pb.id

            ev = GameEvent(
                guild_id=_GUILD,
                type_slug="bounty_caps",
                params={},
                duration_days=7,
                state="active",
                created_by_user_id=_DISCORD_A,
                created_at=now,
                updated_at=now,
            )
            db.add(ev)
            await db.flush()
            ev_id = ev.id

            # 1st-place 500 + participation 100
            db.add(GameEventPrize(event_id=ev_id, rank_from=1, rank_to=1, kind="credits", item_ref=None, qty=500))
            db.add(GameEventPrize(event_id=ev_id, rank_from=None, rank_to=None, kind="credits", item_ref=None, qty=100))
            # Both tied at 10 captures → both rank 1
            db.add(GameEventMetric(event_id=ev_id, player_id=player_a_id, metric="captures", value=10))
            db.add(GameEventMetric(event_id=ev_id, player_id=player_a_id, metric="checks", value=10))
            db.add(GameEventMetric(event_id=ev_id, player_id=player_b_id, metric="captures", value=10))
            db.add(GameEventMetric(event_id=ev_id, player_id=player_b_id, metric="checks", value=10))

        assert ev_id is not None

        # --- Run end_event inside async with session.begin(): ---
        channel_url = _CHANNEL_URL_TMPL.format(channel_id=42000)
        member_data = [{"user": {"id": str(_DISCORD_A)}}, {"user": {"id": str(_DISCORD_B)}}]
        summary: dict = {}
        with respx.mock:
            respx.get(_MEMBERS_URL).mock(return_value=Response(200, json={"data": member_data}))
            respx.post(channel_url).mock(return_value=Response(200, json={"id": "1"}))
            async with _pg_factory() as db, db.begin():
                ev = (await db.execute(select(GameEvent).where(GameEvent.id == ev_id))).scalar_one()
                summary = await end_event(db, ev, payout=True, actor_user_id=_DISCORD_A)

        assert summary.get("status") == "ok", f"status={summary.get('status')!r}"
        assert summary.get("ranked_players") == 2
        assert summary.get("announcement") is not None, "announcement should be set"

        # --- Assert persistence ---
        async with _pg_factory() as db:
            ev = (await db.execute(select(GameEvent).where(GameEvent.id == ev_id))).scalar_one()
            assert ev.state == "ended"

            results = (await db.execute(select(EventResult).where(EventResult.event_id == ev_id))).scalars().all()
            assert len(results) == 2, f"expected 2 result rows, got {len(results)}"
            assert all(r.rank == 1 for r in results)
            assert all(r.status == "ok" for r in results)

            pa = (await db.execute(select(Player).where(Player.id == player_a_id))).scalar_one()
            pb = (await db.execute(select(Player).where(Player.id == player_b_id))).scalar_one()
            assert pa.credits == 1600, f"player A credits={pa.credits}"
            assert pb.credits == 1600, f"player B credits={pb.credits}"
            assert pa.lifetime_credits == 5600, f"player A lifetime={pa.lifetime_credits}"
            assert pb.lifetime_credits == 5600, f"player B lifetime={pb.lifetime_credits}"

            audits = (
                await db.execute(
                    select(AdminAuditLog).where(
                        AdminAuditLog.action == "event_payout",
                        AdminAuditLog.resource_id == str(ev_id),
                    )
                )
            ).scalars().all()
            assert len(audits) == 1, f"expected 1 audit row, got {len(audits)}"

        # --- Bug 3: medals() qualified==1 on Postgres Integer ---
        async with _pg_factory() as db:
            medal_rows = await medals(db, _GUILD)

        medal_by_player = {r["player_id"]: r for r in medal_rows}
        assert player_a_id in medal_by_player, "player A should have medals"
        assert player_b_id in medal_by_player, "player B should have medals"
        assert medal_by_player[player_a_id]["gold"] == 1
        assert medal_by_player[player_b_id]["gold"] == 1

    finally:
        await _cleanup(ev_id)
