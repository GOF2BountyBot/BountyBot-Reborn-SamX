"""
Integration tests for DuelService — S6 sprint.

Covers the ORM mutation paths that AsyncMock-based unit tests cannot exercise:
  - accept_duel:   duel status → "completed", credits transferred (cross-session)
  - reject_duel:   duel status → "rejected" persisted (cross-session)
  - expire_duel:   duel status → "expired" persisted (cross-session)
  - create_challenge: duel created with status "pending" persisted (cross-session)

Cross-session reload rule (B.34): every test opens session A, performs the
operation, closes session A, opens a fresh session B, then asserts persistence
through session B.

SQLite compatibility: User, Player, DuelRequest are SQLite-safe.
accept_duel calls CombatService / LoadoutBuilder which require ship/weapon
repos (ARRAY tables). For accept_duel tests, we mock LoadoutBuilder.from_player
at the method boundary (1 mock per test) — see AGENTS.md §SQLite Compatibility.

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

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from persist.models.base import Base
from persist.models.duel_request import DuelRequest
from persist.models.guild_config import GuildConfig
from persist.models.guild_shop import GuildShop
from persist.models.player import Player
from persist.models.player_inventory import PlayerInventory
from persist.models.player_ship import PlayerShip
from persist.models.user import User
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# DuelRequest is SQLite-compatible (no ARRAY columns).
_SQLITE_TABLES = [
    User.__table__,
    Player.__table__,
    GuildConfig.__table__,
    GuildShop.__table__,
    PlayerInventory.__table__,
    PlayerShip.__table__,
    DuelRequest.__table__,
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
) -> Player:
    p = Player(
        user_id=user_id,
        guild_id=guild_id,
        credits=credits,
        lifetime_credits=credits,
        xp=0,
        xp_surplus=0,
        tier="Bronze",
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _seed_duel(
    db: AsyncSession,
    guild_id: int,
    challenger_id: int,
    target_id: int,
    stakes: int = 100,
    status: str = "pending",
    expires_at: datetime | None = None,
) -> DuelRequest:
    if expires_at is None:
        expires_at = datetime.now(UTC) + timedelta(days=1)
    duel = DuelRequest(
        guild_id=guild_id,
        challenger_id=challenger_id,
        target_id=target_id,
        stakes=stakes,
        status=status,
        expires_at=expires_at,
    )
    db.add(duel)
    await db.commit()
    await db.refresh(duel)
    return duel


# ---------------------------------------------------------------------------
# create_challenge integration tests
# ---------------------------------------------------------------------------
# DuelService.create_challenge:
#   - validates players exist and have enough credits
#   - creates DuelRequest via duel_repo.create (commit=True by default)


class TestCreateChallengeIntegration:
    """Cross-session persistence tests for DuelService.create_challenge."""

    @pytest.fixture
    def service(self):
        from services.duel_service import DuelService

        return DuelService()

    @pytest.mark.asyncio
    async def test_create_challenge_persisted_cross_session(self, service):
        """create_challenge creates a DuelRequest with status 'pending' — cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=1)
                await _seed_user(session_a, user_id=2)
                challenger = await _seed_player(session_a, user_id=1, guild_id=100, credits=5000)
                target = await _seed_player(session_a, user_id=2, guild_id=100, credits=5000)

                duel = await service.create_challenge(
                    session_a,
                    challenger_id=challenger.id,
                    target_id=target.id,
                    stakes=500,
                    guild_id=100,
                )
                duel_id = duel.id

            # Cross-session reload
            async with factory() as session_b:
                duel_b = await session_b.get(DuelRequest, duel_id)
                assert duel_b is not None, "DuelRequest should have been persisted"
                assert duel_b.status == "pending"
                assert duel_b.stakes == 500
                assert duel_b.challenger_id == challenger.id
                assert duel_b.target_id == target.id
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_create_challenge_insufficient_challenger_credits_raises(self, service):
        """create_challenge raises ValueError when challenger has insufficient credits."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=3)
                await _seed_user(session_a, user_id=4)
                challenger = await _seed_player(session_a, user_id=3, guild_id=100, credits=50)
                target = await _seed_player(session_a, user_id=4, guild_id=100, credits=5000)

                with pytest.raises(ValueError, match="insufficient available credits"):
                    await service.create_challenge(
                        session_a,
                        challenger_id=challenger.id,
                        target_id=target.id,
                        stakes=1000,
                        guild_id=100,
                    )

            # No DuelRequest row should have been created
            async with factory() as session_b:
                from sqlalchemy import select

                result = await session_b.execute(select(DuelRequest))
                duels = result.scalars().all()
                assert len(duels) == 0, "No duel should have been persisted on validation failure"
        finally:
            await engine.dispose()


# ---------------------------------------------------------------------------
# reject_duel integration tests
# ---------------------------------------------------------------------------
# DuelService.reject_duel:
#   - validates duel exists and status == "pending"
#   - calls duel_repo.update_status(db, duel_id, "rejected", commit=True)
#   - returns updated DuelRequest


class TestRejectDuelIntegration:
    """Cross-session persistence tests for DuelService.reject_duel."""

    @pytest.fixture
    def service(self):
        from services.duel_service import DuelService

        return DuelService()

    @pytest.mark.asyncio
    async def test_reject_duel_status_persisted_cross_session(self, service):
        """reject_duel sets status to 'rejected' — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=10)
                await _seed_user(session_a, user_id=11)
                challenger = await _seed_player(session_a, user_id=10, guild_id=200, credits=1000)
                target = await _seed_player(session_a, user_id=11, guild_id=200, credits=1000)
                duel = await _seed_duel(session_a, guild_id=200, challenger_id=challenger.id, target_id=target.id)
                duel_id = duel.id
                assert duel.status == "pending"

                updated = await service.reject_duel(session_a, duel_id=duel_id)

            assert updated is not None
            assert updated.status == "rejected"

            # Cross-session reload
            async with factory() as session_b:
                duel_b = await session_b.get(DuelRequest, duel_id)
                assert duel_b is not None
                assert duel_b.status == "rejected", f"Expected status='rejected'; got {duel_b.status!r}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_reject_already_rejected_duel_raises(self, service):
        """reject_duel raises ValueError when duel is already rejected."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=12)
                await _seed_user(session_a, user_id=13)
                challenger = await _seed_player(session_a, user_id=12, guild_id=200, credits=1000)
                target = await _seed_player(session_a, user_id=13, guild_id=200, credits=1000)
                duel = await _seed_duel(
                    session_a, guild_id=200, challenger_id=challenger.id, target_id=target.id, status="rejected"
                )
                duel_id = duel.id

                with pytest.raises(ValueError, match="cannot be rejected"):
                    await service.reject_duel(session_a, duel_id=duel_id)

            # Status should remain "rejected" (not changed again)
            async with factory() as session_b:
                duel_b = await session_b.get(DuelRequest, duel_id)
                assert duel_b.status == "rejected"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_reject_nonexistent_duel_raises(self, service):
        """reject_duel raises ValueError for nonexistent duel ID."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                with pytest.raises(ValueError, match="not found"):
                    await service.reject_duel(session_a, duel_id=99999)
        finally:
            await engine.dispose()


# ---------------------------------------------------------------------------
# expire_duel integration tests
# ---------------------------------------------------------------------------
# DuelService.expire_duel:
#   - validates duel exists and status == "pending"
#   - calls duel_repo.update_status(db, duel_id, "expired", commit=True)
#   - returns updated DuelRequest


class TestExpireDuelIntegration:
    """Cross-session persistence tests for DuelService.expire_duel."""

    @pytest.fixture
    def service(self):
        from services.duel_service import DuelService

        return DuelService()

    @pytest.mark.asyncio
    async def test_expire_duel_status_persisted_cross_session(self, service):
        """expire_duel sets status to 'expired' — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=20)
                await _seed_user(session_a, user_id=21)
                challenger = await _seed_player(session_a, user_id=20, guild_id=300, credits=2000)
                target = await _seed_player(session_a, user_id=21, guild_id=300, credits=2000)
                duel = await _seed_duel(session_a, guild_id=300, challenger_id=challenger.id, target_id=target.id)
                duel_id = duel.id

                updated = await service.expire_duel(session_a, duel_id=duel_id)

            assert updated is not None
            assert updated.status == "expired"

            # Cross-session reload
            async with factory() as session_b:
                duel_b = await session_b.get(DuelRequest, duel_id)
                assert duel_b is not None
                assert duel_b.status == "expired", f"Expected status='expired'; got {duel_b.status!r}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_expire_multiple_duels_persisted_cross_session(self, service):
        """expire_duel on multiple duels — all statuses persisted cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            duel_ids = []
            async with factory() as session_a:
                await _seed_user(session_a, user_id=22)
                await _seed_user(session_a, user_id=23)
                await _seed_user(session_a, user_id=24)
                p1 = await _seed_player(session_a, user_id=22, guild_id=300, credits=3000)
                p2 = await _seed_player(session_a, user_id=23, guild_id=300, credits=3000)
                p3 = await _seed_player(session_a, user_id=24, guild_id=300, credits=3000)

                duel1 = await _seed_duel(session_a, guild_id=300, challenger_id=p1.id, target_id=p2.id)
                duel2 = await _seed_duel(session_a, guild_id=300, challenger_id=p2.id, target_id=p3.id)
                duel3 = await _seed_duel(session_a, guild_id=300, challenger_id=p1.id, target_id=p3.id)
                duel_ids = [duel1.id, duel2.id, duel3.id]

                for duel_id in duel_ids:
                    await service.expire_duel(session_a, duel_id=duel_id)

            # All three should be "expired" in a fresh session
            async with factory() as session_b:
                for duel_id in duel_ids:
                    duel_b = await session_b.get(DuelRequest, duel_id)
                    assert duel_b is not None
                    assert duel_b.status == "expired", (
                        f"Duel {duel_id}: expected status='expired'; got {duel_b.status!r}"
                    )
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_expire_already_expired_duel_raises(self, service):
        """expire_duel raises ValueError when duel already expired — status unchanged."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=25)
                await _seed_user(session_a, user_id=26)
                challenger = await _seed_player(session_a, user_id=25, guild_id=300, credits=1000)
                target = await _seed_player(session_a, user_id=26, guild_id=300, credits=1000)
                duel = await _seed_duel(
                    session_a, guild_id=300, challenger_id=challenger.id, target_id=target.id, status="expired"
                )
                duel_id = duel.id

                with pytest.raises(ValueError, match="cannot be expired"):
                    await service.expire_duel(session_a, duel_id=duel_id)

            async with factory() as session_b:
                duel_b = await session_b.get(DuelRequest, duel_id)
                assert duel_b.status == "expired"
        finally:
            await engine.dispose()


# ---------------------------------------------------------------------------
# accept_duel integration tests
# ---------------------------------------------------------------------------
# accept_duel:
#   - re-validates credits under row-level lock
#   - calls LoadoutBuilder.from_player for both players (needs ship/weapon ARRAY tables)
#   - resolves combat via CombatService
#   - mutates player.credits and duel.status
#   - commits internally via db.commit()
#
# LoadoutBuilder.from_player is mocked (1 mock) to bypass ARRAY-column tables.
# See AGENTS.md §SQLite Compatibility.


class TestAcceptDuelIntegration:
    """Cross-session persistence tests for DuelService.accept_duel."""

    @pytest.fixture
    def service(self):
        from services.duel_service import DuelService

        return DuelService()

    def _make_mock_loadout(self, ship_name: str):
        """Build a minimal mock ShipLoadout sufficient for CombatService.fight_ships."""
        from services.combat_models import ShipLoadout

        return ShipLoadout(
            ship_name=ship_name,
            base_armour=100,
            weapons=[],
            modules=[],
            turrets=[],
            upgrades=[],
        )

    @pytest.mark.asyncio
    async def test_accept_duel_status_completed_cross_session(self, service):
        """accept_duel sets duel status to 'completed' — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=30)
                await _seed_user(session_a, user_id=31)
                challenger = await _seed_player(session_a, user_id=30, guild_id=400, credits=5000)
                target = await _seed_player(session_a, user_id=31, guild_id=400, credits=5000)
                duel = await _seed_duel(
                    session_a, guild_id=400, challenger_id=challenger.id, target_id=target.id, stakes=500
                )
                duel_id = duel.id

                # 1 mock — LoadoutBuilder.from_player: requires PlayerShip + weapon repos
                # (ARRAY-column tables unavailable in SQLite — see AGENTS.md §SQLite Compatibility)
                challenger_loadout = self._make_mock_loadout("Betty")
                target_loadout = self._make_mock_loadout("Raptor")

                with patch(
                    "services.loadout_builder.LoadoutBuilder.from_player",
                    new=AsyncMock(side_effect=[challenger_loadout, target_loadout]),
                ):
                    await service.accept_duel(session_a, duel_id=duel_id)

            # Cross-session reload — duel should be completed
            async with factory() as session_b:
                duel_b = await session_b.get(DuelRequest, duel_id)
                assert duel_b is not None
                assert duel_b.status == "completed", f"Expected status='completed'; got {duel_b.status!r}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_accept_duel_credits_transferred_cross_session(self, service):
        """accept_duel transfers stakes credits from loser to winner — verified cross-session."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=32)
                await _seed_user(session_a, user_id=33)
                challenger = await _seed_player(session_a, user_id=32, guild_id=400, credits=5000)
                target = await _seed_player(session_a, user_id=33, guild_id=400, credits=5000)
                challenger_id = challenger.id
                target_id = target.id
                duel = await _seed_duel(
                    session_a, guild_id=400, challenger_id=challenger_id, target_id=target_id, stakes=1000
                )
                duel_id = duel.id

                # 1 mock — LoadoutBuilder.from_player: ARRAY-column tables unavailable in SQLite
                challenger_loadout = self._make_mock_loadout("Betty")
                target_loadout = self._make_mock_loadout("Raptor")

                with patch(
                    "services.loadout_builder.LoadoutBuilder.from_player",
                    new=AsyncMock(side_effect=[challenger_loadout, target_loadout]),
                ):
                    result = await service.accept_duel(session_a, duel_id=duel_id)

            stakes = 1000
            credits_moved = result["credits_transferred"]

            # Cross-session reload — verify credit conservation
            async with factory() as session_b:
                ch_b = await session_b.get(Player, challenger_id)
                tg_b = await session_b.get(Player, target_id)
                assert ch_b is not None and tg_b is not None

                total = ch_b.credits + tg_b.credits
                # Total should be conserved (5000 + 5000 = 10000)
                assert total == 10000, f"Total credits should be conserved at 10000; got {total}"

                if credits_moved > 0:
                    # If not stalemate, one player gained stakes, one lost stakes
                    credits_diff = abs(ch_b.credits - tg_b.credits)
                    assert credits_diff == stakes, f"Credit diff should equal stakes={stakes}; got diff={credits_diff}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_accept_duel_insufficient_credits_raises(self, service):
        """accept_duel raises ValueError when re-validated credits are insufficient."""
        engine, factory = await _fresh_engine_and_factory()
        try:
            async with factory() as session_a:
                await _seed_user(session_a, user_id=34)
                await _seed_user(session_a, user_id=35)
                challenger = await _seed_player(session_a, user_id=34, guild_id=400, credits=50)
                target = await _seed_player(session_a, user_id=35, guild_id=400, credits=5000)
                duel = await _seed_duel(
                    session_a,
                    guild_id=400,
                    challenger_id=challenger.id,
                    target_id=target.id,
                    stakes=1000,  # challenger can't afford
                )
                duel_id = duel.id

                with pytest.raises(ValueError, match="can no longer cover this duel"):
                    await service.accept_duel(session_a, duel_id=duel_id)

            # Duel status should remain "pending"
            async with factory() as session_b:
                duel_b = await session_b.get(DuelRequest, duel_id)
                assert duel_b.status == "pending", (
                    f"Duel status should remain 'pending' on credit failure; got {duel_b.status!r}"
                )
        finally:
            await engine.dispose()
