"""T7 — over-cap lockout gate (LOOT_JOURNAL §5.5 C-3, OQ-4).

bot-core is the authoritative guard: a player whose per-unit cargo load is
STRICTLY greater than their effective cap (``load > cap``; being exactly AT cap
is allowed) is locked out of the three "leaving station" combat entries BEFORE
any resolution:

* **duel challenge** — the CHALLENGER is gated at challenge time; on over-cap
  ``create_challenge`` raises ``OverCapError`` and NO duel row is created.
* **duel accept** — the ACCEPTER (target) is gated at accept time; on over-cap
  ``accept_duel`` raises ``OverCapError``, the duel stays ``pending`` and NO
  combat resolves.
* **/check** — the checking player is gated first; ``check_bounty`` returns a
  single ``OVER_CAP`` outcome carrying ``cargo_current``/``cargo_max`` (NN/XX)
  and NO bounty is resolved (no loot, no reward, no cooldown set).

Boundary: a player exactly AT cap (``load == cap``) and under cap proceed
normally (no over-cap rejection). Equip/unequip/buy are NOT gated (not exercised
here — they have no combat entry).

Runs against the throwaway seeded Postgres (head 0022). Mock budget: ≤2 mocks
per test, real objects preferred — these tests use REAL services + REAL DB and
0 mocks for the gate behaviour.
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest
from persist.models.duel_request import DuelRequest
from persist.models.player import Player
from persist.models.player_inventory import PlayerInventory
from persist.models.player_ship import PlayerShip
from persist.models.user import User
from services.bounty_service import BountyService, CheckResult
from services.duel_service import DuelService
from services.exceptions import OverCapError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# pg_env lives in tests/ (one level up).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from pg_env import PG_ASYNC_URL, pg_skip_reason

_PG_SKIP = pg_skip_reason()
pytestmark = pytest.mark.skipif(_PG_SKIP is not None, reason=_PG_SKIP or "")

# Isolation constants that cannot collide with production rows.
_TEST_GUILD = 999_888_777_010
_USER_A = 999_888_777_011
_USER_B = 999_888_777_012

# Seed-data anchor: Betty has cargo 25 (verified present at head 0022).
_SHIP_NAME = "Betty"
_SHIP_CARGO = 25
_WEAPON = "Micro Gun MK I"  # a real primary weapon (stackable cargo unit)
_INV_TYPE = "primary_weapon"


@asynccontextmanager
async def _pg():
    engine = create_async_engine(PG_ASYNC_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _cleanup(factory) -> None:
    async with factory() as db, db.begin():
        await db.execute(text(f"DELETE FROM duel_requests WHERE guild_id = {_TEST_GUILD}"))
        await db.execute(text(f"UPDATE players SET active_ship_id = NULL WHERE guild_id = {_TEST_GUILD}"))
        await db.execute(
            text(
                "DELETE FROM player_inventories WHERE player_id IN "
                f"(SELECT id FROM players WHERE guild_id = {_TEST_GUILD})"
            )
        )
        await db.execute(
            text(f"DELETE FROM player_ships WHERE player_id IN (SELECT id FROM players WHERE guild_id = {_TEST_GUILD})")
        )
        await db.execute(text(f"DELETE FROM players WHERE guild_id = {_TEST_GUILD}"))
        await db.execute(text(f"DELETE FROM users WHERE id IN ({_USER_A}, {_USER_B})"))


async def _seed_player(factory, *, user_id: int, cargo_load: int, credits: int = 100_000) -> int:
    """Seed a player on the active Betty (cargo 25) with ``cargo_load`` units of
    the test weapon in cargo. ``cargo_load > 25`` ⇒ over cap; ``== 25`` ⇒ at cap."""
    async with factory() as db, db.begin():
        if await db.get(User, user_id) is None:
            db.add(User(id=user_id, discord_username=f"t7-{user_id}"))
            await db.flush()
        player = Player(user_id=user_id, guild_id=_TEST_GUILD, credits=credits, tier="Bronze", classic_mode=False)
        db.add(player)
        await db.flush()
        ship = PlayerShip(
            player_id=player.id,
            ship_name=_SHIP_NAME,
            is_active=True,
            modules=[],
            weapons=[],
            turrets=[],
            secondary_weapons=[],
        )
        db.add(ship)
        await db.flush()
        player.active_ship_id = ship.id
        if cargo_load > 0:
            db.add(PlayerInventory(player_id=player.id, item_type=_INV_TYPE, item_name=_WEAPON, quantity=cargo_load))
        await db.flush()
        return player.id


async def _duel_count(factory) -> int:
    async with factory() as db:
        return (
            await db.execute(
                select(func.count())  # pylint: disable=not-callable
                .select_from(DuelRequest)
                .where(DuelRequest.guild_id == _TEST_GUILD)
            )
        ).scalar()


async def _seed_pending_duel(factory, challenger_id: int, target_id: int) -> int:
    async with factory() as db, db.begin():
        duel = DuelRequest(
            guild_id=_TEST_GUILD,
            challenger_id=challenger_id,
            target_id=target_id,
            stakes=0,
            status="pending",
        )
        db.add(duel)
        await db.flush()
        return duel.id


async def _duel_status(factory, duel_id: int) -> str:
    async with factory() as db:
        return (await db.execute(select(DuelRequest.status).where(DuelRequest.id == duel_id))).scalars().first()


# ===========================================================================
# Duel CHALLENGE — challenger gated
# ===========================================================================


class TestDuelChallengeOverCap:
    async def test_over_cap_challenger_blocked_no_duel_created(self) -> None:
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                challenger = await _seed_player(factory, user_id=_USER_A, cargo_load=_SHIP_CARGO + 1)  # 26 > 25
                target = await _seed_player(factory, user_id=_USER_B, cargo_load=0)
                svc = DuelService()
                async with factory() as db:
                    with pytest.raises(OverCapError) as exc:
                        await svc.create_challenge(db, challenger, target, stakes=0, guild_id=_TEST_GUILD)
                # NN/XX carried on the rejection.
                assert exc.value.current_load == _SHIP_CARGO + 1
                assert exc.value.effective_cap == _SHIP_CARGO
                assert exc.value.player_id == challenger
                # NO duel was created — gate ran before any duel row write.
                assert await _duel_count(factory) == 0
            finally:
                await _cleanup(factory)

    async def test_at_cap_challenger_proceeds_duel_created(self) -> None:
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                # Exactly AT cap (25/25) must NOT be locked out (strict > only).
                challenger = await _seed_player(factory, user_id=_USER_A, cargo_load=_SHIP_CARGO)
                target = await _seed_player(factory, user_id=_USER_B, cargo_load=0)
                svc = DuelService()
                async with factory() as db:
                    duel = await svc.create_challenge(db, challenger, target, stakes=0, guild_id=_TEST_GUILD)
                assert duel.id is not None
                assert duel.status == "pending"
                assert await _duel_count(factory) == 1
            finally:
                await _cleanup(factory)

    async def test_under_cap_challenger_proceeds(self) -> None:
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                challenger = await _seed_player(factory, user_id=_USER_A, cargo_load=3)
                target = await _seed_player(factory, user_id=_USER_B, cargo_load=0)
                svc = DuelService()
                async with factory() as db:
                    duel = await svc.create_challenge(db, challenger, target, stakes=0, guild_id=_TEST_GUILD)
                assert duel.status == "pending"
                assert await _duel_count(factory) == 1
            finally:
                await _cleanup(factory)


# ===========================================================================
# Duel ACCEPT — accepter (target) gated
# ===========================================================================


class TestDuelAcceptOverCap:
    async def test_over_cap_accepter_blocked_duel_stays_pending(self) -> None:
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                # Challenger under cap; the ACCEPTER (target) is over cap.
                challenger = await _seed_player(factory, user_id=_USER_A, cargo_load=0)
                target = await _seed_player(factory, user_id=_USER_B, cargo_load=_SHIP_CARGO + 5)  # 30 > 25
                duel_id = await _seed_pending_duel(factory, challenger, target)
                svc = DuelService()
                async with factory() as db:
                    with pytest.raises(OverCapError) as exc:
                        await svc.accept_duel(db, duel_id)
                assert exc.value.current_load == _SHIP_CARGO + 5
                assert exc.value.effective_cap == _SHIP_CARGO
                assert exc.value.player_id == target
                # NO combat resolved — duel is still pending.
                assert await _duel_status(factory, duel_id) == "pending"
            finally:
                await _cleanup(factory)

    async def test_under_cap_challenger_does_not_block_accept(self) -> None:
        """At challenge time only the CHALLENGER is gated; an over-cap TARGET does
        NOT block the challenge (the target is gated later, at accept)."""
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                challenger = await _seed_player(factory, user_id=_USER_A, cargo_load=0)
                target = await _seed_player(factory, user_id=_USER_B, cargo_load=_SHIP_CARGO + 5)
                svc = DuelService()
                async with factory() as db:
                    duel = await svc.create_challenge(db, challenger, target, stakes=0, guild_id=_TEST_GUILD)
                # Challenge succeeds even though the target is over cap.
                assert duel.status == "pending"
            finally:
                await _cleanup(factory)


# ===========================================================================
# /check — checking player gated, no bounty resolved
# ===========================================================================


class TestCheckOverCap:
    async def test_over_cap_check_returns_over_cap_no_resolution(self) -> None:
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                pid = await _seed_player(factory, user_id=_USER_A, cargo_load=_SHIP_CARGO + 7)  # 32 > 25
                svc = BountyService()
                async with factory() as db:
                    result = await svc.check_bounty(db, pid, system_name="Nowhere", guild_id=_TEST_GUILD)
                # Single OVER_CAP outcome carrying NN/XX; no bounty processed.
                assert len(result.outcomes) == 1
                outcome = result.outcomes[0]
                assert outcome.result is CheckResult.OVER_CAP
                assert outcome.cargo_current == _SHIP_CARGO + 7
                assert outcome.cargo_max == _SHIP_CARGO
                expected_msg = f"Cargo Overloaded — {_SHIP_CARGO + 7}/{_SHIP_CARGO}. Unable to leave station."
                assert outcome.message == expected_msg
                # No bounty was resolved (no bounty_id, no reward, no loot).
                assert outcome.bounty_id is None
                assert outcome.reward is None
                assert outcome.loot is None
                # No cooldown was set on the player (gate ran before cooldown step).
                async with factory() as db:
                    cd = (
                        (await db.execute(select(Player.bounty_cooldown_end).where(Player.id == pid))).scalars().first()
                    )
                assert cd is None
            finally:
                await _cleanup(factory)

    async def test_at_cap_check_not_over_cap(self) -> None:
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                # Exactly AT cap (25/25) must pass the gate (strict > only). The
                # checking player has no matching bounty here, so the result is a
                # NOT_FOUND outcome — NOT an OVER_CAP one. The point is only that
                # the over-cap gate did not fire.
                pid = await _seed_player(factory, user_id=_USER_A, cargo_load=_SHIP_CARGO)
                svc = BountyService()
                async with factory() as db:
                    result = await svc.check_bounty(db, pid, system_name="Nowhere", guild_id=_TEST_GUILD)
                assert all(o.result is not CheckResult.OVER_CAP for o in result.outcomes)
            finally:
                await _cleanup(factory)

    async def test_under_cap_check_not_over_cap(self) -> None:
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                pid = await _seed_player(factory, user_id=_USER_A, cargo_load=2)
                svc = BountyService()
                async with factory() as db:
                    result = await svc.check_bounty(db, pid, system_name="Nowhere", guild_id=_TEST_GUILD)
                assert all(o.result is not CheckResult.OVER_CAP for o in result.outcomes)
            finally:
                await _cleanup(factory)
