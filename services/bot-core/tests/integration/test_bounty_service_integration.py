"""
Integration tests for BountyService — S5 rewrite.

Covers the ORM mutation paths that AsyncMock-based unit tests cannot exercise:
  - distribute_rewards: player.credits / xp / bounty_wins / systems_checked mutations
    + bounty.status / win_user_id + db.commit() semantics
  - _award_combat_bonus: player.credits / lifetime_credits / xp mutations

Cross-session reload rule (B.34): every test that mutates the DB
opens session A, performs the operation, closes session A, opens a fresh
session B, then asserts persistence through session B.

SQLite compatibility note: Bounty and Player/User/GuildConfig are
SQLite-safe. Criminal/Ship/System/Item tables have ARRAY columns and
cannot be seeded in SQLite — tests that need them must mock at the
repo boundary (see AGENTS.md §SQLite Compatibility).

Mock budget: max 2 mocks per test.
"""

# ---------------------------------------------------------------------------
# Path setup: ensure src/ is first on sys.path so 'services.*' / 'persist.*'
# resolve to src/ rather than tests/ packages.
#
# NOTE: Unlike other integration tests (e.g. test_cross_session_persistence.py)
# that also purge services.* from sys.modules, this file intentionally does NOT
# purge because doing so would break class identity comparisons in
# tests/services/test_bounty_service.py when both suites are collected together
# in a single pytest run. This integration file only needs persist.* imports,
# which are always loaded from src/ by the conftest chain.
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
# We do NOT purge services.* to avoid class-identity conflicts with the unit
# tests in tests/services/test_bounty_service.py when collected together.
for _key in list(sys.modules):
    if _key in ("api", "persist") or _key.startswith(("api.", "persist.")):
        _mod = sys.modules[_key]
        _file = getattr(_mod, "__file__", "") or ""
        if _SRC_DIR not in _file:
            del sys.modules[_key]

# ---------------------------------------------------------------------------

import pytest
from persist.models.base import Base
from persist.models.bounty import Bounty
from persist.models.guild_config import GuildConfig
from persist.models.player import Player
from persist.models.user import User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# SQLite-compatible tables (no ARRAY columns).
# Bounty is JSON-only and SQLite-safe.
_SQLITE_TABLES = [
    User.__table__,
    Player.__table__,
    GuildConfig.__table__,
    Bounty.__table__,
]


# ---------------------------------------------------------------------------
# Per-test engine + session factory
# ---------------------------------------------------------------------------


async def _fresh_engine_and_factory():
    """Create a fresh SQLite in-memory engine + session factory.

    Returns (engine, factory). Caller is responsible for disposing the engine.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_SQLITE_TABLES)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_user(db: AsyncSession, user_id: int) -> User:
    user = User(id=user_id, discord_username=f"u{user_id}")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _seed_player(
    db: AsyncSession,
    user_id: int,
    guild_id: int,
    credits: int = 5000,
    xp: int = 0,
    systems_checked: int = 0,
    bounty_wins: int = 0,
    classic_mode: bool = False,
) -> Player:
    p = Player(
        user_id=user_id,
        guild_id=guild_id,
        credits=credits,
        lifetime_credits=credits,
        xp=xp,
        xp_surplus=0,
        tier="Bronze",
        systems_checked=systems_checked,
        bounty_wins=bounty_wins,
        classic_mode=classic_mode,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _seed_bounty(
    db: AsyncSession,
    guild_id: int,
    division: str = "bronze",
    reward: int = 10000,
    reward_per_sys: int = 1000,
    route: list[str] | None = None,
    answer: str = "Sol",
    checked: dict | None = None,
    status: str = "active",
) -> Bounty:
    if route is None:
        route = ["Alpha", "Beta", "Gamma", "Sol", "Omega"]
    if checked is None:
        checked = {s: -1 for s in route}
    from datetime import UTC, datetime, timedelta

    bounty = Bounty(
        guild_id=guild_id,
        division=division,
        criminal_name="Test Criminal",
        criminal_faction="terran",
        route=route,
        answer=answer,
        reward=reward,
        reward_per_sys=reward_per_sys,
        checked=checked,
        status=status,
        issue_time=datetime.now(UTC),
        end_time=datetime.now(UTC) + timedelta(hours=8),
        tech_level=3,
        criminal_ship={"ship_name": "Bandit", "ship_armour": 100, "weapons": [], "turrets": []},
    )
    db.add(bounty)
    await db.commit()
    await db.refresh(bounty)
    return bounty


# ---------------------------------------------------------------------------
# distribute_rewards integration tests
# ---------------------------------------------------------------------------
# Rationale for integration placement: distribute_rewards mutates player.credits,
# player.xp, player.lifetime_credits, player.bounty_wins, player.systems_checked
# via direct ORM attribute assignment, then issues db.commit(). The identity-map
# behavior (whether SQLAlchemy propagates the mutations to the DB) can only be
# verified against a real session. Mock-based unit tests mask this entirely.


class TestDistributeRewardsIntegration:
    """Cross-session persistence tests for BountyService.distribute_rewards."""

    @pytest.fixture
    def service(self):
        from services.bounty_service import BountyService

        return BountyService()

    @pytest.mark.asyncio
    async def test_distribute_rewards_credits_persisted_cross_session(self, service):
        """Winner credits mutation is durably committed — readable from a fresh session B."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            # Session A: seed and run
            async with factory() as session_a:
                await _seed_user(session_a, user_id=1)
                player = await _seed_player(session_a, user_id=1, guild_id=100, credits=5000)
                player_id = player.id
                player_discord_id = player.user_id  # User.id == Discord snowflake
                bounty = await _seed_bounty(
                    session_a,
                    guild_id=100,
                    reward=10000,
                    reward_per_sys=1000,
                    route=["Alpha", "Sol"],
                    answer="Sol",
                    checked={"Alpha": -1, "Sol": player_id},
                )
                bounty_id = bounty.id

                from services.bounty_service import RewardInfo

                rewards = [RewardInfo(player_id=player_id, credits_earned=2000, xp_earned=200, is_winner=True)]
                await service.distribute_rewards(session_a, bounty, rewards)

            # Session B: verify persistence
            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b is not None
                assert player_b.credits == 7000, f"Expected credits=7000 (5000+2000) but got {player_b.credits}"
                assert player_b.lifetime_credits == 7000
                bounty_b = await session_b.get(Bounty, bounty_id)
                assert bounty_b is not None
                assert bounty_b.status == "completed"
                # win_user_id stores the Discord snowflake (User.id / Player.user_id),
                # NOT the player table PK.
                assert bounty_b.win_user_id == player_discord_id
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_distribute_rewards_xp_persisted_cross_session(self, service):
        """Winner XP mutation is durably committed — readable from a fresh session B."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=2)
                player = await _seed_player(session_a, user_id=2, guild_id=100, xp=500, classic_mode=False)
                player_id = player.id
                bounty = await _seed_bounty(
                    session_a,
                    guild_id=100,
                    reward=5000,
                    reward_per_sys=500,
                    route=["A", "Sol"],
                    answer="Sol",
                    checked={"A": -1, "Sol": player_id},
                )

                from services.bounty_service import RewardInfo

                rewards = [RewardInfo(player_id=player_id, credits_earned=1000, xp_earned=100, is_winner=True)]
                await service.distribute_rewards(session_a, bounty, rewards)

            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b is not None
                assert player_b.xp == 600, f"Expected xp=600 (500+100) but got {player_b.xp}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_distribute_rewards_classic_mode_no_xp_persisted(self, service):
        """Classic-mode player receives credits but XP unchanged — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=3)
                player = await _seed_player(session_a, user_id=3, guild_id=100, credits=1000, xp=0, classic_mode=True)
                player_id = player.id
                bounty = await _seed_bounty(
                    session_a,
                    guild_id=100,
                    reward=3000,
                    reward_per_sys=300,
                    route=["X", "Sol"],
                    answer="Sol",
                    checked={"X": -1, "Sol": player_id},
                )

                from services.bounty_service import RewardInfo

                rewards = [RewardInfo(player_id=player_id, credits_earned=3000, xp_earned=300, is_winner=False)]
                await service.distribute_rewards(session_a, bounty, rewards)

            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b is not None
                assert player_b.credits == 4000, f"Expected credits=4000 but got {player_b.credits}"
                assert player_b.xp == 0, f"Classic-mode XP must stay 0; got {player_b.xp}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_distribute_rewards_bounty_wins_incremented_cross_session(self, service):
        """bounty_wins counter increment is durable — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=4)
                player = await _seed_player(session_a, user_id=4, guild_id=100, bounty_wins=3)
                player_id = player.id
                bounty = await _seed_bounty(
                    session_a,
                    guild_id=100,
                    reward=5000,
                    reward_per_sys=500,
                    route=["A", "Sol"],
                    answer="Sol",
                    checked={"A": -1, "Sol": player_id},
                )

                from services.bounty_service import RewardInfo

                rewards = [RewardInfo(player_id=player_id, credits_earned=5000, xp_earned=500, is_winner=True)]
                await service.distribute_rewards(session_a, bounty, rewards)

            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b is not None
                assert player_b.bounty_wins == 4, f"Expected bounty_wins=4 (3+1) but got {player_b.bounty_wins}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_distribute_rewards_systems_checked_incremented_cross_session(self, service):
        """systems_checked counter increment is durable — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=5)
                player = await _seed_player(session_a, user_id=5, guild_id=100, systems_checked=10)
                player_id = player.id
                bounty = await _seed_bounty(
                    session_a,
                    guild_id=100,
                    reward=5000,
                    reward_per_sys=500,
                    route=["A", "Sol"],
                    answer="Sol",
                    checked={"A": -1, "Sol": player_id},
                )

                from services.bounty_service import RewardInfo

                rewards = [
                    RewardInfo(
                        player_id=player_id,
                        credits_earned=5000,
                        xp_earned=500,
                        is_winner=True,
                        systems_checked_count=3,
                    )
                ]
                await service.distribute_rewards(session_a, bounty, rewards)

            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b is not None
                assert player_b.systems_checked == 13, (
                    f"Expected systems_checked=13 (10+3) but got {player_b.systems_checked}"
                )
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_distribute_rewards_multi_player_cross_session(self, service):
        """Multi-player reward distribution: winner + contributor both persist correctly."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=10)
                await _seed_user(session_a, user_id=11)
                player_a = await _seed_player(session_a, user_id=10, guild_id=200, credits=5000)
                player_b = await _seed_player(session_a, user_id=11, guild_id=200, credits=3000)
                pid_a = player_a.id
                pid_b = player_b.id
                pid_b_discord = player_b.user_id  # User.id == Discord snowflake
                bounty = await _seed_bounty(
                    session_a,
                    guild_id=200,
                    reward=5000,
                    reward_per_sys=1000,
                    route=["Alpha", "Beta", "Sol"],
                    answer="Sol",
                    checked={"Alpha": pid_a, "Beta": -1, "Sol": pid_b},
                )
                bounty_id = bounty.id

                from services.bounty_service import RewardInfo

                # winner_reserve = int(5000 * 0.25) = 1250, consolation = 3750
                # p_a (non-winner): 1000 credits, 0 xp
                # p_b (winner): 1250 + 2750 = 4000 credits
                rewards = [
                    RewardInfo(player_id=pid_a, credits_earned=1000, xp_earned=0, is_winner=False),
                    RewardInfo(player_id=pid_b, credits_earned=4000, xp_earned=400, is_winner=True),
                ]
                await service.distribute_rewards(session_a, bounty, rewards)

            async with factory() as session_b:
                pa_b = await session_b.get(Player, pid_a)
                pb_b = await session_b.get(Player, pid_b)
                bounty_b = await session_b.get(Bounty, bounty_id)

                assert pa_b is not None
                assert pa_b.credits == 6000  # 5000 + 1000
                assert pa_b.xp == 0

                assert pb_b is not None
                assert pb_b.credits == 7000  # 3000 + 4000
                assert pb_b.xp == 400
                assert pb_b.bounty_wins == 1

                assert bounty_b is not None
                assert bounty_b.status == "completed"
                # win_user_id stores the Discord snowflake (User.id / Player.user_id),
                # NOT the player table PK.
                assert bounty_b.win_user_id == pid_b_discord
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_distribute_rewards_bounty_status_completed_persisted(self, service):
        """Bounty status set to 'completed' is durable — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=20)
                player = await _seed_player(session_a, user_id=20, guild_id=300, credits=1000)
                player_id = player.id
                player_discord_id = player.user_id  # User.id == Discord snowflake
                bounty = await _seed_bounty(
                    session_a,
                    guild_id=300,
                    reward=1000,
                    reward_per_sys=200,
                    route=["X", "Sol"],
                    answer="Sol",
                    checked={"X": -1, "Sol": player_id},
                )
                bounty_id = bounty.id
                assert bounty.status == "active"

                from services.bounty_service import RewardInfo

                rewards = [RewardInfo(player_id=player_id, credits_earned=1000, xp_earned=100, is_winner=True)]
                await service.distribute_rewards(session_a, bounty, rewards)

            async with factory() as session_b:
                bounty_b = await session_b.get(Bounty, bounty_id)
                assert bounty_b is not None
                assert bounty_b.status == "completed"
                # win_user_id stores the Discord snowflake (User.id / Player.user_id),
                # NOT the player table PK.
                assert bounty_b.win_user_id == player_discord_id
        finally:
            await engine.dispose()


# ---------------------------------------------------------------------------
# _award_combat_bonus integration tests
# ---------------------------------------------------------------------------
# Rationale: _award_combat_bonus fetches player via player_repo.get_by_id,
# mutates player.credits, player.lifetime_credits, player.xp directly,
# and relies on the surrounding transaction (from check_bounty's db.commit()).
# The identity-map behavior can only be verified with a real SQLite session.


class TestAwardCombatBonusIntegration:
    """Cross-session persistence tests for BountyService._award_combat_bonus."""

    @pytest.fixture
    def service(self):
        from services.bounty_service import BountyService

        return BountyService()

    @pytest.mark.asyncio
    async def test_award_combat_bonus_credits_persisted_cross_session(self, service):
        """Credits and lifetime_credits mutation from bonus persists across sessions."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=30)
                player = await _seed_player(session_a, user_id=30, guild_id=400, credits=1000)
                player_id = player.id

                await service._award_combat_bonus(session_a, player_id=player_id, bonus_credits=500)
                await session_a.commit()

            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b is not None
                assert player_b.credits == 1500, f"Expected credits=1500 (1000+500) but got {player_b.credits}"
                assert player_b.lifetime_credits == 1500
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_award_combat_bonus_xp_persisted_cross_session(self, service):
        """XP mutation from bonus persists across sessions for non-classic players."""
        from services.game_constants import GameConstants

        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=31)
                player = await _seed_player(session_a, user_id=31, guild_id=400, xp=50, classic_mode=False)
                player_id = player.id

                bonus = 1000
                await service._award_combat_bonus(session_a, player_id=player_id, bonus_credits=bonus)
                await session_a.commit()

            expected_xp = 50 + int(bonus * GameConstants.BOUNTY_REWARD_TO_XP_GAIN_MULT)
            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b is not None
                assert player_b.xp == expected_xp, f"Expected xp={expected_xp} but got {player_b.xp}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_award_combat_bonus_classic_mode_no_xp_cross_session(self, service):
        """Classic-mode player receives credits but no XP — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=32)
                player = await _seed_player(session_a, user_id=32, guild_id=400, credits=1000, xp=0, classic_mode=True)
                player_id = player.id

                await service._award_combat_bonus(session_a, player_id=player_id, bonus_credits=500)
                await session_a.commit()

            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b is not None
                assert player_b.credits == 1500, f"Expected credits=1500 but got {player_b.credits}"
                assert player_b.xp == 0, f"Classic-mode XP must stay 0; got {player_b.xp}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_award_combat_bonus_player_not_found_no_mutation(self, service):
        """When player does not exist, no rows are created or mutated."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                # No player seeded — player_id=999 does not exist
                await service._award_combat_bonus(session_a, player_id=999, bonus_credits=500)
                await session_a.commit()

            async with factory() as session_b:
                result = await session_b.execute(select(Player))
                players = result.scalars().all()
                assert len(players) == 0, "No players should exist when award target was missing"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_award_combat_bonus_large_amount_cross_session(self, service):
        """Large bonus amount (matching winner payout) persists correctly."""
        from services.game_constants import GameConstants

        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=33)
                player = await _seed_player(session_a, user_id=33, guild_id=400, credits=1000, xp=0)
                player_id = player.id

                winner_reward = 3055  # Gendol Ethor scenario winner amount
                expected_xp = int(winner_reward * GameConstants.BOUNTY_REWARD_TO_XP_GAIN_MULT)

                await service._award_combat_bonus(session_a, player_id=player_id, bonus_credits=winner_reward)
                await session_a.commit()

            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b is not None
                assert player_b.credits == 1000 + winner_reward
                assert player_b.xp == expected_xp
                assert player_b.lifetime_credits == 1000 + winner_reward
        finally:
            await engine.dispose()
