"""
Integration tests for PlayerService — S6 sprint.

Covers the ORM mutation paths that AsyncMock-based unit tests cannot exercise:
  - update_player_credits: credits + lifetime_credits persisted correctly
  - update_player_xp:      XP clamping and persistence
  - transfer_credits:      source debited, target credited (cross-session)
  - promote_player:        tier progression persisted

Cross-session reload rule (B.34): every test opens session A, performs the
operation, closes session A, opens a fresh session B, then asserts persistence
through session B.

SQLite compatibility: User, Player, GuildConfig are SQLite-safe.
No ARRAY-column tables are needed for these paths.

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

import pytest
from persist.models.base import Base
from persist.models.guild_config import GuildConfig
from persist.models.guild_shop import GuildShop
from persist.models.player import Player
from persist.models.player_inventory import PlayerInventory
from persist.models.player_ship import PlayerShip
from persist.models.user import User
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


async def _seed_guild_config(db: AsyncSession, guild_id: int, starting_credits: int = 1000) -> GuildConfig:
    config = GuildConfig(
        guild_id=guild_id,
        starting_credits=starting_credits,
        xp_thresholds={"Silver": 1000, "Gold": 5000, "Platinum": 15000, "Prestige": 50000},
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return config


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
    lifetime_credits: int | None = None,
    xp: int = 0,
    tier: str = "Bronze",
) -> Player:
    p = Player(
        user_id=user_id,
        guild_id=guild_id,
        credits=credits,
        lifetime_credits=lifetime_credits if lifetime_credits is not None else credits,
        xp=xp,
        xp_surplus=0,
        tier=tier,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


# ---------------------------------------------------------------------------
# update_player_credits integration tests
# ---------------------------------------------------------------------------
# PlayerService.update_player_credits:
#   - reads player via player_repo.get_by_id
#   - mutates player.credits (ORM tracked setattr)
#   - optionally increments lifetime_credits
#   - commits via db.commit() internally


class TestUpdatePlayerCreditsIntegration:
    """Cross-session persistence tests for PlayerService.update_player_credits."""

    @pytest.fixture
    def service(self):
        from services.player_service import PlayerService

        return PlayerService()

    @pytest.mark.asyncio
    async def test_update_credits_increase_persisted_cross_session(self, service):
        """Credit increase is durably committed — readable from a fresh session B."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=1)
                player = await _seed_player(session_a, user_id=1, guild_id=100, credits=3000)
                player_id = player.id

                await service.update_player_credits(session_a, player_id=player_id, new_credits=7000)

            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b is not None
                assert player_b.credits == 7000, f"Expected credits=7000; got {player_b.credits}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_update_credits_lifetime_incremented_on_increase_cross_session(self, service):
        """Lifetime credits increase when credits go up — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=2)
                player = await _seed_player(session_a, user_id=2, guild_id=100, credits=2000, lifetime_credits=2000)
                player_id = player.id

                await service.update_player_credits(
                    session_a, player_id=player_id, new_credits=5000, update_lifetime=True
                )

            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b is not None
                assert player_b.credits == 5000
                # Lifetime should have increased by (5000 - 2000) = 3000
                assert player_b.lifetime_credits == 5000, (
                    f"Expected lifetime_credits=5000; got {player_b.lifetime_credits}"
                )
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_update_credits_decrease_does_not_change_lifetime_cross_session(self, service):
        """Credit decrease does NOT update lifetime_credits — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=3)
                player = await _seed_player(session_a, user_id=3, guild_id=100, credits=5000, lifetime_credits=10000)
                player_id = player.id

                # Decrease credits (e.g., after spending)
                await service.update_player_credits(
                    session_a, player_id=player_id, new_credits=2000, update_lifetime=True
                )

            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b is not None
                assert player_b.credits == 2000
                # lifetime_credits unchanged — decrease doesn't affect it
                assert player_b.lifetime_credits == 10000, (
                    f"Expected lifetime_credits=10000 (unchanged); got {player_b.lifetime_credits}"
                )
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_update_credits_zero_persisted_cross_session(self, service):
        """Setting credits to 0 persists correctly — boundary case."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=4)
                player = await _seed_player(session_a, user_id=4, guild_id=100, credits=1000)
                player_id = player.id

                await service.update_player_credits(session_a, player_id=player_id, new_credits=0)

            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b is not None
                assert player_b.credits == 0, f"Expected credits=0; got {player_b.credits}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_update_credits_negative_raises_value_error(self, service):
        """Setting credits to negative raises ValueError — no mutation."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=5)
                player = await _seed_player(session_a, user_id=5, guild_id=100, credits=500)
                player_id = player.id

                with pytest.raises(ValueError, match="cannot be negative"):
                    await service.update_player_credits(session_a, player_id=player_id, new_credits=-100)

            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b.credits == 500, "Credits should be unchanged on error"
        finally:
            await engine.dispose()


# ---------------------------------------------------------------------------
# update_player_xp integration tests
# ---------------------------------------------------------------------------
# PlayerService.update_player_xp:
#   - reads player, mutates player.xp, commits internally
#   - clamps xp to 0 if negative, to 1,000,000 if over max


class TestUpdatePlayerXpIntegration:
    """Cross-session persistence tests for PlayerService.update_player_xp."""

    @pytest.fixture
    def service(self):
        from services.player_service import PlayerService

        return PlayerService()

    @pytest.mark.asyncio
    async def test_update_xp_persisted_cross_session(self, service):
        """XP mutation is durably committed — readable from a fresh session B."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=10)
                player = await _seed_player(session_a, user_id=10, guild_id=200, xp=0)
                player_id = player.id

                await service.update_player_xp(session_a, player_id=player_id, xp=1500)

            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b is not None
                assert player_b.xp == 1500, f"Expected xp=1500; got {player_b.xp}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_update_xp_clamped_to_zero_when_negative_cross_session(self, service):
        """Negative XP input is clamped to 0 — persisted as 0 in fresh session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=11)
                player = await _seed_player(session_a, user_id=11, guild_id=200, xp=200)
                player_id = player.id

                await service.update_player_xp(session_a, player_id=player_id, xp=-50)

            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b is not None
                assert player_b.xp == 0, f"Expected xp=0 (clamped from -50); got {player_b.xp}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_update_xp_clamped_to_max_cross_session(self, service):
        """XP above 1,000,000 is clamped to 1,000,000 — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=12)
                player = await _seed_player(session_a, user_id=12, guild_id=200, xp=0)
                player_id = player.id

                await service.update_player_xp(session_a, player_id=player_id, xp=2_000_000)

            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b is not None
                assert player_b.xp == 1_000_000, f"Expected xp=1,000,000 (clamped); got {player_b.xp}"
        finally:
            await engine.dispose()


# ---------------------------------------------------------------------------
# transfer_credits integration tests
# ---------------------------------------------------------------------------
# PlayerService.transfer_credits:
#   - locks both rows via get_by_id_for_update
#   - calls player_repo.update_credits(commit=False) for both
#   - does NOT commit — router owns the commit
# Tests issue db.commit() after calling the service.


class TestTransferCreditsIntegration:
    """Cross-session persistence tests for PlayerService.transfer_credits."""

    @pytest.fixture
    def service(self):
        from services.player_service import PlayerService

        return PlayerService()

    @pytest.mark.asyncio
    async def test_transfer_credits_source_debited_cross_session(self, service):
        """transfer_credits debits source player — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=20)
                await _seed_user(session_a, user_id=21)
                source = await _seed_player(session_a, user_id=20, guild_id=300, credits=8000)
                target = await _seed_player(session_a, user_id=21, guild_id=300, credits=2000)
                source_id = source.id
                target_id = target.id

                result = await service.transfer_credits(
                    session_a, source_player_id=source_id, target_player_id=target_id, amount=3000
                )
                await session_a.commit()

            assert result["source_remaining_credits"] == 5000
            assert result["target_new_credits"] == 5000

            async with factory() as session_b:
                source_b = await session_b.get(Player, source_id)
                assert source_b is not None
                assert source_b.credits == 5000, f"Expected source credits=5000 (8000-3000); got {source_b.credits}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_transfer_credits_target_credited_cross_session(self, service):
        """transfer_credits credits target player — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=22)
                await _seed_user(session_a, user_id=23)
                source = await _seed_player(session_a, user_id=22, guild_id=300, credits=5000)
                target = await _seed_player(session_a, user_id=23, guild_id=300, credits=1000)
                source_id = source.id
                target_id = target.id

                await service.transfer_credits(
                    session_a, source_player_id=source_id, target_player_id=target_id, amount=2000
                )
                await session_a.commit()

            async with factory() as session_b:
                target_b = await session_b.get(Player, target_id)
                assert target_b is not None
                assert target_b.credits == 3000, f"Expected target credits=3000 (1000+2000); got {target_b.credits}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_transfer_credits_both_mutations_atomic_cross_session(self, service):
        """Both source debit and target credit persist atomically — cross-session check."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=24)
                await _seed_user(session_a, user_id=25)
                source = await _seed_player(session_a, user_id=24, guild_id=300, credits=10000)
                target = await _seed_player(session_a, user_id=25, guild_id=300, credits=500)
                source_id = source.id
                target_id = target.id

                await service.transfer_credits(
                    session_a, source_player_id=source_id, target_player_id=target_id, amount=4000
                )
                await session_a.commit()

            async with factory() as session_b:
                source_b = await session_b.get(Player, source_id)
                target_b = await session_b.get(Player, target_id)
                assert source_b is not None and target_b is not None
                # Total credits should be conserved (10000 + 500 = 10500)
                total = source_b.credits + target_b.credits
                assert total == 10500, f"Total credits should be conserved at 10500; got {total}"
                assert source_b.credits == 6000
                assert target_b.credits == 4500
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_transfer_credits_insufficient_raises_value_error(self, service):
        """transfer_credits raises ValueError when source has insufficient credits."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=26)
                await _seed_user(session_a, user_id=27)
                source = await _seed_player(session_a, user_id=26, guild_id=300, credits=100)
                target = await _seed_player(session_a, user_id=27, guild_id=300, credits=500)
                source_id = source.id
                target_id = target.id

                with pytest.raises(ValueError, match="Insufficient credits"):
                    await service.transfer_credits(
                        session_a, source_player_id=source_id, target_player_id=target_id, amount=9999
                    )

            # Both balances should be unchanged
            async with factory() as session_b:
                source_b = await session_b.get(Player, source_id)
                target_b = await session_b.get(Player, target_id)
                assert source_b.credits == 100
                assert target_b.credits == 500
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_transfer_credits_self_transfer_raises_value_error(self, service):
        """transfer_credits raises ValueError on self-transfer."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=28)
                player = await _seed_player(session_a, user_id=28, guild_id=300, credits=1000)
                player_id = player.id

                with pytest.raises(ValueError, match="yourself"):
                    await service.transfer_credits(
                        session_a, source_player_id=player_id, target_player_id=player_id, amount=100
                    )

            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b.credits == 1000, "Credits should be unchanged on self-transfer error"
        finally:
            await engine.dispose()


# ---------------------------------------------------------------------------
# promote_player integration tests
# ---------------------------------------------------------------------------
# PlayerService.promote_player:
#   - reads player and config
#   - mutates player.tier
#   - commits via db.commit() internally


class TestPromotePlayerIntegration:
    """Cross-session persistence tests for PlayerService.promote_player."""

    @pytest.fixture
    def service(self):
        from services.player_service import PlayerService

        return PlayerService()

    @pytest.mark.asyncio
    async def test_promote_player_tier_persisted_cross_session(self, service):
        """promote_player updates tier to next level — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_guild_config(session_a, guild_id=400)
                await _seed_user(session_a, user_id=30)
                player = await _seed_player(session_a, user_id=30, guild_id=400, xp=1500, tier="Bronze")
                player_id = player.id

                result = await service.promote_player(session_a, player_id=player_id)

            assert result["new_tier"] == "Silver"
            assert result["old_tier"] == "Bronze"

            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b is not None
                assert player_b.tier == "Silver", f"Expected tier=Silver; got {player_b.tier}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_promote_player_insufficient_xp_raises(self, service):
        """promote_player raises ValueError when XP is below threshold."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_guild_config(session_a, guild_id=400)
                await _seed_user(session_a, user_id=31)
                player = await _seed_player(session_a, user_id=31, guild_id=400, xp=50, tier="Bronze")
                player_id = player.id

                with pytest.raises(ValueError, match="Not eligible for promotion"):
                    await service.promote_player(session_a, player_id=player_id)

            async with factory() as session_b:
                player_b = await session_b.get(Player, player_id)
                assert player_b.tier == "Bronze", "Tier should not have changed"
        finally:
            await engine.dispose()
