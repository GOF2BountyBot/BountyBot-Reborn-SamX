"""
Unit tests for DuelService.

Strategy:
- Mock duel_repo and player_repo at the service instance level (2 mocks max per test).
- Use a REAL CombatService with 0% variance or a custom deterministic resolver.
- Use SimpleNamespace / MagicMock objects for Player and DuelRequest (no real DB).

The conftest.py already patches shared.bblogger before imports reach here.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# Guard: ensure shared.bblogger is mocked if running in isolation.
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

# Mock sqlalchemy_utils before any model imports that depend on it.
# discord_message.py uses UUIDType from sqlalchemy_utils which is not installed
# in the test environment. The models/__init__.py auto-imports all models, so
# we must stub this out before the first model import is triggered.
if "sqlalchemy_utils" not in sys.modules:
    _sqla_utils = types.ModuleType("sqlalchemy_utils")
    _sqla_utils.UUIDType = MagicMock()
    sys.modules["sqlalchemy_utils"] = _sqla_utils

from services.duel_service import DuelService
from src.services.combat_models import (
    CombatStats,
    FightResults,
    FightStats,
)
from src.services.combat_service import CombatService

# ---------------------------------------------------------------------------
# Helpers / factory functions
# ---------------------------------------------------------------------------


def make_player(
    player_id: int,
    credits: int = 1000,
    active_ship=None,
    *,
    duel_wins: int = 0,
    duel_losses: int = 0,
    duel_credits_won: int = 0,
    duel_credits_lost: int = 0,
) -> MagicMock:
    """Create a Player-like MagicMock with mutable integer attributes."""
    p = MagicMock()
    p.id = player_id
    p.user_id = player_id * 100
    p.guild_id = 9999
    p.credits = credits
    p.lifetime_credits = credits
    p.duel_wins = duel_wins
    p.duel_losses = duel_losses
    p.duel_credits_won = duel_credits_won
    p.duel_credits_lost = duel_credits_lost
    p.active_ship = active_ship
    return p


def make_active_ship(ship_name: str, armour: int = 100) -> MagicMock:
    """Create a PlayerShip-like MagicMock."""
    ship = MagicMock()
    ship.ship_name = ship_name
    ship.armour = armour
    return ship


def make_duel(
    duel_id: int = 1,
    challenger_id: int = 1,
    target_id: int = 2,
    stakes: int = 100,
    guild_id: int = 9999,
    status: str = "pending",
) -> SimpleNamespace:
    """Create a DuelRequest-like SimpleNamespace."""
    return SimpleNamespace(
        id=duel_id,
        challenger_id=challenger_id,
        target_id=target_id,
        stakes=stakes,
        guild_id=guild_id,
        status=status,
        created_at=None,
        expires_at=None,
    )


def make_service(*, duel_repo=None, player_repo=None, combat_service=None) -> DuelService:
    """Instantiate DuelService with mocked repositories."""
    svc = DuelService.__new__(DuelService)
    svc.duel_repo = duel_repo or AsyncMock()
    svc.player_repo = player_repo or AsyncMock()
    svc.combat_service = combat_service or CombatService()
    return svc


# ---------------------------------------------------------------------------
# Deterministic custom combat resolvers
# ---------------------------------------------------------------------------


class ChallengerWinsResolver:
    """Combat resolver: ship1 (challenger) always wins decisively."""

    def resolve(
        self,
        ship1_stats: CombatStats,
        ship2_stats: CombatStats,
        variance_percent: float,
    ) -> FightResults:
        fs1 = FightStats(
            ship_name=ship1_stats.ship_name,
            raw_hp=ship1_stats.total_hp, raw_dps=100.0,
            varied_hp=ship1_stats.total_hp, varied_dps=100.0,
            ttk=10.0,
        )
        fs2 = FightStats(
            ship_name=ship2_stats.ship_name,
            raw_hp=ship2_stats.total_hp, raw_dps=1.0,
            varied_hp=ship2_stats.total_hp, varied_dps=1.0,
            ttk=1.0,
        )
        return FightResults(
            winner_name=ship1_stats.ship_name,
            loser_name=ship2_stats.ship_name,
            is_stalemate=False,
            ship1_stats=fs1,
            ship2_stats=fs2,
            variance_percent=0.0,
        )


class TargetWinsResolver:
    """Combat resolver: ship2 (target) always wins decisively."""

    def resolve(
        self,
        ship1_stats: CombatStats,
        ship2_stats: CombatStats,
        variance_percent: float,
    ) -> FightResults:
        fs1 = FightStats(
            ship_name=ship1_stats.ship_name,
            raw_hp=ship1_stats.total_hp, raw_dps=1.0,
            varied_hp=ship1_stats.total_hp, varied_dps=1.0,
            ttk=1.0,
        )
        fs2 = FightStats(
            ship_name=ship2_stats.ship_name,
            raw_hp=ship2_stats.total_hp, raw_dps=100.0,
            varied_hp=ship2_stats.total_hp, varied_dps=100.0,
            ttk=10.0,
        )
        return FightResults(
            winner_name=ship2_stats.ship_name,
            loser_name=ship1_stats.ship_name,
            is_stalemate=False,
            ship1_stats=fs1,
            ship2_stats=fs2,
            variance_percent=0.0,
        )


class StalemateResolver:
    """Combat resolver: always stalemate."""

    def resolve(
        self,
        ship1_stats: CombatStats,
        ship2_stats: CombatStats,
        variance_percent: float,
    ) -> FightResults:
        fs1 = FightStats(
            ship_name=ship1_stats.ship_name,
            raw_hp=ship1_stats.total_hp, raw_dps=0.0,
            varied_hp=ship1_stats.total_hp, varied_dps=0.0,
            ttk=None,
        )
        fs2 = FightStats(
            ship_name=ship2_stats.ship_name,
            raw_hp=ship2_stats.total_hp, raw_dps=0.0,
            varied_hp=ship2_stats.total_hp, varied_dps=0.0,
            ttk=None,
        )
        return FightResults(
            winner_name=None,
            loser_name=None,
            is_stalemate=True,
            ship1_stats=fs1,
            ship2_stats=fs2,
            variance_percent=0.0,
        )


# ---------------------------------------------------------------------------
# TestCreateChallenge
# ---------------------------------------------------------------------------


class TestCreateChallenge:
    """Tests for DuelService.create_challenge()."""

    @pytest.mark.asyncio
    async def test_valid_challenge_creates_duel(self):
        """Happy path: valid challenge between two players is persisted."""
        challenger = make_player(1, credits=500)
        target = make_player(2, credits=500)

        player_repo = AsyncMock()
        player_repo.get_by_id.side_effect = lambda db, pid: {
            1: challenger, 2: target,
        }.get(pid)

        duel_repo = AsyncMock()
        duel_repo.get_pending_by_players.return_value = None
        expected = make_duel(duel_id=42)
        duel_repo.create.return_value = expected

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
        result = await svc.create_challenge(db=None, challenger_id=1, target_id=2, stakes=100, guild_id=9999)

        assert result is expected
        duel_repo.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_self_challenge_raises(self):
        """Challenging yourself raises ValueError."""
        svc = make_service()
        with pytest.raises(ValueError, match="cannot challenge themselves"):
            await svc.create_challenge(db=None, challenger_id=5, target_id=5, stakes=0, guild_id=1)

    @pytest.mark.asyncio
    async def test_negative_stakes_raises(self):
        """Negative stakes raises ValueError."""
        svc = make_service()
        with pytest.raises(ValueError, match="non-negative"):
            await svc.create_challenge(db=None, challenger_id=1, target_id=2, stakes=-1, guild_id=1)

    @pytest.mark.asyncio
    async def test_challenger_not_found_raises(self):
        """Challenger player not in DB raises ValueError."""
        player_repo = AsyncMock()
        player_repo.get_by_id.return_value = None  # challenger not found

        svc = make_service(player_repo=player_repo)
        with pytest.raises(ValueError, match="Challenger player"):
            await svc.create_challenge(db=None, challenger_id=99, target_id=2, stakes=0, guild_id=1)

    @pytest.mark.asyncio
    async def test_target_not_found_raises(self):
        """Target player not in DB raises ValueError."""
        challenger = make_player(1, credits=100)

        player_repo = AsyncMock()
        player_repo.get_by_id.side_effect = lambda db, pid: (
            challenger if pid == 1 else None
        )

        svc = make_service(player_repo=player_repo)
        with pytest.raises(ValueError, match="Target player"):
            await svc.create_challenge(db=None, challenger_id=1, target_id=99, stakes=0, guild_id=1)

    @pytest.mark.asyncio
    async def test_challenger_insufficient_credits_raises(self):
        """Challenger with fewer credits than stakes raises ValueError."""
        challenger = make_player(1, credits=50)
        target = make_player(2, credits=500)

        player_repo = AsyncMock()
        player_repo.get_by_id.side_effect = lambda db, pid: {
            1: challenger, 2: target,
        }.get(pid)

        svc = make_service(player_repo=player_repo)
        with pytest.raises(ValueError, match="Challenger has insufficient"):
            await svc.create_challenge(db=None, challenger_id=1, target_id=2, stakes=100, guild_id=1)

    @pytest.mark.asyncio
    async def test_target_insufficient_credits_raises(self):
        """Target with fewer credits than stakes raises ValueError."""
        challenger = make_player(1, credits=500)
        target = make_player(2, credits=30)

        player_repo = AsyncMock()
        player_repo.get_by_id.side_effect = lambda db, pid: {
            1: challenger, 2: target,
        }.get(pid)

        svc = make_service(player_repo=player_repo)
        with pytest.raises(ValueError, match="Target has insufficient"):
            await svc.create_challenge(db=None, challenger_id=1, target_id=2, stakes=100, guild_id=1)

    @pytest.mark.asyncio
    async def test_duplicate_pending_duel_raises(self):
        """Existing pending duel between same players in guild raises ValueError."""
        challenger = make_player(1, credits=500)
        target = make_player(2, credits=500)

        player_repo = AsyncMock()
        player_repo.get_by_id.side_effect = lambda db, pid: {
            1: challenger, 2: target,
        }.get(pid)

        duel_repo = AsyncMock()
        duel_repo.get_pending_by_players.return_value = make_duel()  # existing duel

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
        with pytest.raises(ValueError, match="pending duel already exists"):
            await svc.create_challenge(db=None, challenger_id=1, target_id=2, stakes=50, guild_id=9999)

    @pytest.mark.asyncio
    async def test_zero_stakes_is_valid(self):
        """Stakes of zero is explicitly allowed (both players can have 0 credits)."""
        challenger = make_player(1, credits=0)
        target = make_player(2, credits=0)

        player_repo = AsyncMock()
        player_repo.get_by_id.side_effect = lambda db, pid: {
            1: challenger, 2: target,
        }.get(pid)

        duel_repo = AsyncMock()
        duel_repo.get_pending_by_players.return_value = None
        duel_repo.create.return_value = make_duel(stakes=0)

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
        result = await svc.create_challenge(db=None, challenger_id=1, target_id=2, stakes=0, guild_id=9999)
        assert result is not None


# ---------------------------------------------------------------------------
# TestAcceptDuel
# ---------------------------------------------------------------------------


class TestAcceptDuel:
    """Tests for DuelService.accept_duel()."""

    @pytest.mark.asyncio
    async def test_accept_challenger_wins(self):
        """Challenger wins: gets +stakes credits; target loses -stakes; stats updated."""
        challenger_ship = make_active_ship("ChallengerShip", armour=100)
        target_ship = make_active_ship("TargetShip", armour=100)
        challenger = make_player(1, credits=1000, active_ship=challenger_ship)
        target = make_player(2, credits=1000, active_ship=target_ship)

        duel = make_duel(duel_id=1, challenger_id=1, target_id=2, stakes=200)

        duel_repo = AsyncMock()
        duel_repo.get_by_id.return_value = duel
        duel_repo.update_status.return_value = duel

        player_repo = AsyncMock()
        player_repo.get_by_id.side_effect = lambda db, pid: {
            1: challenger, 2: target,
        }.get(pid)

        svc = make_service(
            duel_repo=duel_repo,
            player_repo=player_repo,
            combat_service=CombatService(resolver=ChallengerWinsResolver()),
        )

        result = await svc.accept_duel(db=None, duel_id=1)

        assert result["credits_transferred"] == 200
        assert challenger.credits == 1200
        assert challenger.duel_wins == 1
        assert challenger.duel_credits_won == 200
        assert target.credits == 800
        assert target.duel_losses == 1
        assert target.duel_credits_lost == 200
        duel_repo.update_status.assert_awaited_once_with(None, 1, "completed")

    @pytest.mark.asyncio
    async def test_accept_target_wins(self):
        """Target wins: gets +stakes credits; challenger loses -stakes; stats updated."""
        challenger_ship = make_active_ship("ChallengerShip", armour=100)
        target_ship = make_active_ship("TargetShip", armour=100)
        challenger = make_player(1, credits=500, active_ship=challenger_ship)
        target = make_player(2, credits=500, active_ship=target_ship)

        duel = make_duel(duel_id=2, challenger_id=1, target_id=2, stakes=100)

        duel_repo = AsyncMock()
        duel_repo.get_by_id.return_value = duel
        duel_repo.update_status.return_value = duel

        player_repo = AsyncMock()
        player_repo.get_by_id.side_effect = lambda db, pid: {
            1: challenger, 2: target,
        }.get(pid)

        svc = make_service(
            duel_repo=duel_repo,
            player_repo=player_repo,
            combat_service=CombatService(resolver=TargetWinsResolver()),
        )

        result = await svc.accept_duel(db=None, duel_id=2)

        assert result["credits_transferred"] == 100
        assert target.credits == 600
        assert target.duel_wins == 1
        assert target.duel_credits_won == 100
        assert challenger.credits == 400
        assert challenger.duel_losses == 1
        assert challenger.duel_credits_lost == 100

    @pytest.mark.asyncio
    async def test_accept_stalemate_no_credits_transferred(self):
        """Stalemate: no credits move, no stat changes, status still set to completed."""
        challenger_ship = make_active_ship("ShipA", armour=100)
        target_ship = make_active_ship("ShipB", armour=100)
        challenger = make_player(1, credits=500, active_ship=challenger_ship)
        target = make_player(2, credits=500, active_ship=target_ship)

        duel = make_duel(duel_id=3, challenger_id=1, target_id=2, stakes=100)

        duel_repo = AsyncMock()
        duel_repo.get_by_id.return_value = duel
        duel_repo.update_status.return_value = duel

        player_repo = AsyncMock()
        player_repo.get_by_id.side_effect = lambda db, pid: {
            1: challenger, 2: target,
        }.get(pid)

        svc = make_service(
            duel_repo=duel_repo,
            player_repo=player_repo,
            combat_service=CombatService(resolver=StalemateResolver()),
        )

        result = await svc.accept_duel(db=None, duel_id=3)

        fight = result["fight_results"]
        assert fight.is_stalemate is True
        assert result["credits_transferred"] == 0
        # Stats unchanged
        assert challenger.duel_wins == 0
        assert challenger.duel_losses == 0
        assert target.duel_wins == 0
        assert target.duel_losses == 0
        # Credits unchanged
        assert challenger.credits == 500
        assert target.credits == 500
        # Status still updated
        duel_repo.update_status.assert_awaited_once_with(None, 3, "completed")

    @pytest.mark.asyncio
    async def test_accept_duel_not_found_raises(self):
        """Accept on non-existent duel raises ValueError."""
        duel_repo = AsyncMock()
        duel_repo.get_by_id.return_value = None

        svc = make_service(duel_repo=duel_repo)
        with pytest.raises(ValueError, match="not found"):
            await svc.accept_duel(db=None, duel_id=999)

    @pytest.mark.asyncio
    async def test_accept_already_resolved_raises(self):
        """Accepting a non-pending duel raises ValueError."""
        duel = make_duel(status="completed")

        duel_repo = AsyncMock()
        duel_repo.get_by_id.return_value = duel

        svc = make_service(duel_repo=duel_repo)
        with pytest.raises(ValueError, match="cannot be accepted"):
            await svc.accept_duel(db=None, duel_id=1)

    @pytest.mark.asyncio
    async def test_accept_challenger_insufficient_credits_at_accept_time(self):
        """Challenger who spent credits between challenge and accept is rejected."""
        challenger = make_player(1, credits=0)   # no longer has credits
        target = make_player(2, credits=500)

        duel = make_duel(duel_id=1, challenger_id=1, target_id=2, stakes=100)

        duel_repo = AsyncMock()
        duel_repo.get_by_id.return_value = duel

        player_repo = AsyncMock()
        player_repo.get_by_id.side_effect = lambda db, pid: {
            1: challenger, 2: target,
        }.get(pid)

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
        with pytest.raises(ValueError, match="insufficient credits at accept-time"):
            await svc.accept_duel(db=None, duel_id=1)

    @pytest.mark.asyncio
    async def test_accept_target_insufficient_credits_at_accept_time(self):
        """Target who spent credits between challenge and accept is rejected."""
        challenger = make_player(1, credits=500)
        target = make_player(2, credits=10)  # not enough

        duel = make_duel(duel_id=1, challenger_id=1, target_id=2, stakes=100)

        duel_repo = AsyncMock()
        duel_repo.get_by_id.return_value = duel

        player_repo = AsyncMock()
        player_repo.get_by_id.side_effect = lambda db, pid: {
            1: challenger, 2: target,
        }.get(pid)

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
        with pytest.raises(ValueError, match="insufficient credits at accept-time"):
            await svc.accept_duel(db=None, duel_id=1)

    @pytest.mark.asyncio
    async def test_accept_real_stalemate_with_zero_dps_ships(self):
        """Real CombatService: two unarmed ships produce a stalemate."""
        # No active ship → default "Unarmed" loadout, 0 DPS
        challenger = make_player(1, credits=500, active_ship=None)
        target = make_player(2, credits=500, active_ship=None)

        duel = make_duel(duel_id=4, challenger_id=1, target_id=2, stakes=100)

        duel_repo = AsyncMock()
        duel_repo.get_by_id.return_value = duel
        duel_repo.update_status.return_value = duel

        player_repo = AsyncMock()
        player_repo.get_by_id.side_effect = lambda db, pid: {
            1: challenger, 2: target,
        }.get(pid)

        svc = make_service(
            duel_repo=duel_repo,
            player_repo=player_repo,
            combat_service=CombatService(),  # real, 0% variance applied
        )

        result = await svc.accept_duel(db=None, duel_id=4)

        # Two ships with 0 DPS → stalemate
        fight = result["fight_results"]
        assert fight.is_stalemate is True
        assert result["credits_transferred"] == 0


# ---------------------------------------------------------------------------
# TestRejectDuel
# ---------------------------------------------------------------------------


class TestRejectDuel:
    """Tests for DuelService.reject_duel()."""

    @pytest.mark.asyncio
    async def test_reject_pending_duel(self):
        """Rejecting a pending duel updates status to rejected."""
        duel = make_duel(status="pending")
        rejected = make_duel(status="rejected")

        duel_repo = AsyncMock()
        duel_repo.get_by_id.return_value = duel
        duel_repo.update_status.return_value = rejected

        svc = make_service(duel_repo=duel_repo)
        result = await svc.reject_duel(db=None, duel_id=1)

        duel_repo.update_status.assert_awaited_once_with(None, 1, "rejected")
        assert result.status == "rejected"

    @pytest.mark.asyncio
    async def test_reject_duel_not_found_raises(self):
        """Rejecting a non-existent duel raises ValueError."""
        duel_repo = AsyncMock()
        duel_repo.get_by_id.return_value = None

        svc = make_service(duel_repo=duel_repo)
        with pytest.raises(ValueError, match="not found"):
            await svc.reject_duel(db=None, duel_id=999)

    @pytest.mark.asyncio
    async def test_reject_already_resolved_raises(self):
        """Rejecting a non-pending duel raises ValueError."""
        duel = make_duel(status="completed")

        duel_repo = AsyncMock()
        duel_repo.get_by_id.return_value = duel

        svc = make_service(duel_repo=duel_repo)
        with pytest.raises(ValueError, match="cannot be rejected"):
            await svc.reject_duel(db=None, duel_id=1)


# ---------------------------------------------------------------------------
# TestExpireDuel
# ---------------------------------------------------------------------------


class TestExpireDuel:
    """Tests for DuelService.expire_duel()."""

    @pytest.mark.asyncio
    async def test_expire_pending_duel(self):
        """Expiring a pending duel updates status to expired."""
        duel = make_duel(status="pending")
        expired = make_duel(status="expired")

        duel_repo = AsyncMock()
        duel_repo.get_by_id.return_value = duel
        duel_repo.update_status.return_value = expired

        svc = make_service(duel_repo=duel_repo)
        result = await svc.expire_duel(db=None, duel_id=1)

        duel_repo.update_status.assert_awaited_once_with(None, 1, "expired")
        assert result.status == "expired"

    @pytest.mark.asyncio
    async def test_expire_duel_not_found_raises(self):
        """Expiring a non-existent duel raises ValueError."""
        duel_repo = AsyncMock()
        duel_repo.get_by_id.return_value = None

        svc = make_service(duel_repo=duel_repo)
        with pytest.raises(ValueError, match="not found"):
            await svc.expire_duel(db=None, duel_id=999)

    @pytest.mark.asyncio
    async def test_expire_already_resolved_raises(self):
        """Expiring a non-pending duel raises ValueError."""
        duel = make_duel(status="rejected")

        duel_repo = AsyncMock()
        duel_repo.get_by_id.return_value = duel

        svc = make_service(duel_repo=duel_repo)
        with pytest.raises(ValueError, match="cannot be expired"):
            await svc.expire_duel(db=None, duel_id=1)
