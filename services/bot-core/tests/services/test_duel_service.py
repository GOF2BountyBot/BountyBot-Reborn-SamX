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

from unittest.mock import patch

from services.duel_service import DuelService
from services.loadout_builder import LoadoutBuilder
from src.services.combat_models import (
    FightResults,
    FightStats,
    ShipLoadout,
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


def make_ship_loadout(ship_name: str, base_armour: int = 100) -> ShipLoadout:
    """Create a minimal ShipLoadout for testing duel combat."""
    return ShipLoadout(ship_name=ship_name, base_armour=base_armour)


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


def make_service(*, duel_repo=None, player_repo=None, user_repo=None, combat_service=None) -> DuelService:
    """Instantiate DuelService with mocked repositories.

    B.49: config_repo is also initialised so that create_challenge can call
    get_by_guild_id without an AttributeError. Defaults to returning None
    (global GameConstants fallback).
    """
    svc = DuelService.__new__(DuelService)
    svc.duel_repo = duel_repo or AsyncMock()
    svc.player_repo = player_repo or AsyncMock()
    svc.user_repo = user_repo or AsyncMock()
    svc.combat_service = combat_service or CombatService()
    config_repo = AsyncMock()
    config_repo.get_by_guild_id = AsyncMock(return_value=None)
    svc.config_repo = config_repo
    return svc


# ---------------------------------------------------------------------------
# Deterministic fight_ships mock factories (T10: fight_ships is now async)
# ---------------------------------------------------------------------------


def _make_fight_results(
    winner: str | None,
    loser: str | None,
    is_stalemate: bool = False,
    *,
    winner_side: int | None = None,
) -> FightResults:
    """Build a deterministic FightResults for unit tests (no real DB needed).

    P2-T8a: winner_side is now required for correct winner decoding.
    Pass winner_side=1 when challenger wins, winner_side=2 when target wins,
    winner_side=None for stalemate.  The default None is kept for back-compat
    with any test that doesn't call accept_duel (e.g. router tests that only
    check presentation fields).
    """
    fs1 = FightStats(ship_name=winner or "ShipA", raw_hp=100, raw_dps=10.0, varied_hp=100, varied_dps=10.0, ttk=None)
    fs2 = FightStats(
        ship_name=loser or "ShipB",
        raw_hp=50,
        raw_dps=5.0,
        varied_hp=50,
        varied_dps=5.0,
        ttk=5.0 if not is_stalemate else None,
    )
    return FightResults(
        winner_name=winner,
        loser_name=loser,
        is_stalemate=is_stalemate,
        winner_side=winner_side,
        ship1_stats=fs1,
        ship2_stats=fs2,
        combat_log=[],
        metadata={
            "schema_version": 1,
            "summary": {
                "outcome": "stalemate" if is_stalemate else "win",
                "reason": "time_cap" if is_stalemate else "hp_depleted",
                "duration_ticks": 100,
                "winner": winner,
                "combatants": {
                    "1": {"name": winner or "ShipA", "ship": "ShipA", "damage_dealt": 50, "damage_taken": 10},
                    "2": {"name": loser or "ShipB", "ship": "ShipB", "damage_dealt": 10, "damage_taken": 50},
                },
            },
            "metadata": {"tick_ms": 10, "total_ticks": 100, "resolver": "tick_v1", "pvc_damage_reduction": 0.0},
        },
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
        player_repo.get_by_id_for_update.side_effect = lambda db, pid: {
            1: challenger,
            2: target,
        }.get(pid)

        duel_repo = AsyncMock()
        duel_repo.get_total_pending_stakes_for_player.return_value = 0
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
    async def test_self_challenge_raises_before_player_lookup(self):
        """B.15 fix: self-target check fires BEFORE any player lookup.

        If a non-existent challenger ID were looked up first, the repo would
        return None and raise a 'Challenger not found' error.  This test
        confirms the self-target ValueError is raised instead, proving the
        check runs before I/O.
        """
        player_repo = AsyncMock()
        # Repo would return None for any ID (player doesn't exist)
        player_repo.get_by_id.return_value = None

        svc = make_service(player_repo=player_repo)
        with pytest.raises(ValueError, match="cannot challenge themselves"):
            # Same ID on both sides; player doesn't exist in DB
            await svc.create_challenge(db=None, challenger_id=999999, target_id=999999, stakes=0, guild_id=1)

        # Confirm no DB lookups were performed
        player_repo.get_by_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_repo_exception_on_challenger_lookup_raises_value_error(self):
        """B.15 fix: repo DB exception during challenger lookup is wrapped as ValueError (→ 400).

        Previously any non-ValueError exception from the repo would bubble as a
        raw 500.  After the fix it is caught and re-raised as ValueError so the
        router returns 400 with a safe message.
        """
        player_repo = AsyncMock()
        player_repo.get_by_id_for_update.side_effect = RuntimeError("DB connection lost")

        svc = make_service(player_repo=player_repo)
        with pytest.raises(ValueError, match="could not be retrieved"):
            await svc.create_challenge(db=None, challenger_id=1, target_id=2, stakes=0, guild_id=1)

    @pytest.mark.asyncio
    async def test_repo_exception_on_target_lookup_raises_value_error(self):
        """B.15 fix: repo DB exception during target lookup is wrapped as ValueError (→ 400)."""
        challenger = make_player(1, credits=500)

        player_repo = AsyncMock()

        async def _side_effect(db, pid):
            if pid == 1:
                return challenger
            raise RuntimeError("DB connection lost")

        player_repo.get_by_id_for_update.side_effect = _side_effect

        svc = make_service(player_repo=player_repo)
        with pytest.raises(ValueError, match="could not be retrieved"):
            await svc.create_challenge(db=None, challenger_id=1, target_id=2, stakes=0, guild_id=1)

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
        player_repo.get_by_id_for_update.return_value = None  # challenger not found

        svc = make_service(player_repo=player_repo)
        with pytest.raises(ValueError, match="Challenger player"):
            await svc.create_challenge(db=None, challenger_id=99, target_id=2, stakes=0, guild_id=1)

    @pytest.mark.asyncio
    async def test_target_not_found_raises(self):
        """Target player not in DB raises ValueError."""
        challenger = make_player(1, credits=100)

        player_repo = AsyncMock()
        player_repo.get_by_id_for_update.side_effect = lambda db, pid: challenger if pid == 1 else None

        svc = make_service(player_repo=player_repo)
        with pytest.raises(ValueError, match="Target player"):
            await svc.create_challenge(db=None, challenger_id=1, target_id=99, stakes=0, guild_id=1)

    @pytest.mark.asyncio
    async def test_challenger_insufficient_credits_raises(self):
        """Challenger with fewer credits than stakes raises ValueError."""
        challenger = make_player(1, credits=50)
        target = make_player(2, credits=500)

        player_repo = AsyncMock()
        player_repo.get_by_id_for_update.side_effect = lambda db, pid: {
            1: challenger,
            2: target,
        }.get(pid)

        duel_repo = AsyncMock()
        duel_repo.get_total_pending_stakes_for_player.return_value = 0

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
        with pytest.raises(ValueError, match="insufficient available credits"):
            await svc.create_challenge(db=None, challenger_id=1, target_id=2, stakes=100, guild_id=1)

    @pytest.mark.asyncio
    async def test_target_insufficient_credits_raises(self):
        """Target with fewer credits than stakes raises ValueError."""
        challenger = make_player(1, credits=500)
        target = make_player(2, credits=30)

        player_repo = AsyncMock()
        player_repo.get_by_id_for_update.side_effect = lambda db, pid: {
            1: challenger,
            2: target,
        }.get(pid)

        duel_repo = AsyncMock()
        duel_repo.get_total_pending_stakes_for_player.return_value = 0

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
        with pytest.raises(ValueError, match="insufficient available credits"):
            await svc.create_challenge(db=None, challenger_id=1, target_id=2, stakes=100, guild_id=1)

    @pytest.mark.asyncio
    async def test_duplicate_pending_duel_raises(self):
        """Existing pending duel between same players in guild raises ValueError."""
        challenger = make_player(1, credits=500)
        target = make_player(2, credits=500)

        player_repo = AsyncMock()
        player_repo.get_by_id_for_update.side_effect = lambda db, pid: {
            1: challenger,
            2: target,
        }.get(pid)

        duel_repo = AsyncMock()
        duel_repo.get_total_pending_stakes_for_player.return_value = 0
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
        player_repo.get_by_id_for_update.side_effect = lambda db, pid: {
            1: challenger,
            2: target,
        }.get(pid)

        duel_repo = AsyncMock()
        duel_repo.get_total_pending_stakes_for_player.return_value = 0
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
        duel_repo.get_by_id_for_update.return_value = duel  # X3-duel: accept re-reads under lock
        duel_repo.update_status.return_value = duel
        duel_repo.get_total_pending_stakes_for_player.return_value = 0

        player_repo = AsyncMock()
        # accept_duel uses get_by_id_for_update (locking in sorted ID order: 1 then 2)
        player_repo.get_by_id_for_update.side_effect = lambda db, pid: {
            1: challenger,
            2: target,
        }.get(pid)

        # T10: fight_ships is async; inject deterministic result via AsyncMock
        mock_combat_svc = MagicMock()
        mock_combat_svc.fight_ships = AsyncMock(
            return_value=_make_fight_results(winner="ChallengerShip", loser="TargetShip", winner_side=1)
        )
        svc = make_service(duel_repo=duel_repo, player_repo=player_repo, combat_service=mock_combat_svc)

        # LoadoutBuilder.from_player now needs DB; mock it to return deterministic loadouts
        async def mock_from_player(db, player_id):
            return make_ship_loadout("ChallengerShip" if player_id == 1 else "TargetShip")

        # B.34 closeout: accept_duel now self-commits. Mock db.commit / db.refresh.
        mock_db = AsyncMock()
        with patch.object(LoadoutBuilder, "from_player", side_effect=mock_from_player):
            result = await svc.accept_duel(db=mock_db, duel_id=1)

        assert result["credits_transferred"] == 200
        assert challenger.credits == 1200
        assert challenger.duel_wins == 1
        assert challenger.duel_credits_won == 200
        assert target.credits == 800
        assert target.duel_losses == 1
        assert target.duel_credits_lost == 200
        duel_repo.update_status.assert_awaited_once_with(mock_db, 1, "completed", commit=False)
        mock_db.commit.assert_awaited_once()

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
        duel_repo.get_by_id_for_update.return_value = duel  # X3-duel: accept re-reads under lock
        duel_repo.update_status.return_value = duel
        duel_repo.get_total_pending_stakes_for_player.return_value = 0

        player_repo = AsyncMock()
        # accept_duel uses get_by_id_for_update (locking in sorted ID order: 1 then 2)
        player_repo.get_by_id_for_update.side_effect = lambda db, pid: {
            1: challenger,
            2: target,
        }.get(pid)

        mock_combat_svc = MagicMock()
        mock_combat_svc.fight_ships = AsyncMock(
            return_value=_make_fight_results(winner="TargetShip", loser="ChallengerShip", winner_side=2)
        )
        svc = make_service(duel_repo=duel_repo, player_repo=player_repo, combat_service=mock_combat_svc)

        async def mock_from_player(db, player_id):
            return make_ship_loadout("ChallengerShip" if player_id == 1 else "TargetShip")

        mock_db = AsyncMock()
        with patch.object(LoadoutBuilder, "from_player", side_effect=mock_from_player):
            result = await svc.accept_duel(db=mock_db, duel_id=2)

        assert result["credits_transferred"] == 100
        assert target.credits == 600
        assert target.duel_wins == 1
        assert target.duel_credits_won == 100
        assert challenger.credits == 400
        assert challenger.duel_losses == 1
        assert challenger.duel_credits_lost == 100
        mock_db.commit.assert_awaited_once()

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
        duel_repo.get_by_id_for_update.return_value = duel  # X3-duel: accept re-reads under lock
        duel_repo.update_status.return_value = duel
        duel_repo.get_total_pending_stakes_for_player.return_value = 0

        player_repo = AsyncMock()
        # accept_duel uses get_by_id_for_update (locking in sorted ID order: 1 then 2)
        player_repo.get_by_id_for_update.side_effect = lambda db, pid: {
            1: challenger,
            2: target,
        }.get(pid)

        mock_combat_svc = MagicMock()
        mock_combat_svc.fight_ships = AsyncMock(
            return_value=_make_fight_results(winner=None, loser=None, is_stalemate=True)
        )
        svc = make_service(duel_repo=duel_repo, player_repo=player_repo, combat_service=mock_combat_svc)

        async def mock_from_player(db, player_id):
            return make_ship_loadout("ShipA" if player_id == 1 else "ShipB")

        mock_db = AsyncMock()
        with patch.object(LoadoutBuilder, "from_player", side_effect=mock_from_player):
            result = await svc.accept_duel(db=mock_db, duel_id=3)

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
        # Status still updated; B.34 closeout: explicit commit owned by service
        duel_repo.update_status.assert_awaited_once_with(mock_db, 3, "completed", commit=False)
        mock_db.commit.assert_awaited_once()

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
        challenger = make_player(1, credits=0)  # no longer has credits
        target = make_player(2, credits=500)

        duel = make_duel(duel_id=1, challenger_id=1, target_id=2, stakes=100)

        duel_repo = AsyncMock()
        duel_repo.get_by_id.return_value = duel
        duel_repo.get_by_id_for_update.return_value = duel  # X3-duel: accept re-reads under lock
        duel_repo.get_total_pending_stakes_for_player.return_value = 0

        player_repo = AsyncMock()
        # accept_duel uses get_by_id_for_update (locking in sorted ID order: 1 then 2)
        player_repo.get_by_id_for_update.side_effect = lambda db, pid: {
            1: challenger,
            2: target,
        }.get(pid)

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
        with pytest.raises(ValueError, match="can no longer cover this duel"):
            await svc.accept_duel(db=None, duel_id=1)

    @pytest.mark.asyncio
    async def test_accept_target_insufficient_credits_at_accept_time(self):
        """Target who spent credits between challenge and accept is rejected."""
        challenger = make_player(1, credits=500)
        target = make_player(2, credits=10)  # not enough

        duel = make_duel(duel_id=1, challenger_id=1, target_id=2, stakes=100)

        duel_repo = AsyncMock()
        duel_repo.get_by_id.return_value = duel
        duel_repo.get_by_id_for_update.return_value = duel  # X3-duel: accept re-reads under lock
        duel_repo.get_total_pending_stakes_for_player.return_value = 0

        player_repo = AsyncMock()
        # accept_duel uses get_by_id_for_update (locking in sorted ID order: 1 then 2)
        player_repo.get_by_id_for_update.side_effect = lambda db, pid: {
            1: challenger,
            2: target,
        }.get(pid)

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
        with pytest.raises(ValueError, match="can no longer cover this duel"):
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
        duel_repo.get_by_id_for_update.return_value = duel  # X3-duel: accept re-reads under lock
        duel_repo.update_status.return_value = duel
        duel_repo.get_total_pending_stakes_for_player.return_value = 0

        player_repo = AsyncMock()
        # accept_duel uses get_by_id_for_update (locking in sorted ID order: 1 then 2)
        player_repo.get_by_id_for_update.side_effect = lambda db, pid: {
            1: challenger,
            2: target,
        }.get(pid)

        # T10: fight_ships is async. Mock it to return a deterministic stalemate.
        mock_combat_svc = MagicMock()
        mock_combat_svc.fight_ships = AsyncMock(
            return_value=_make_fight_results(winner=None, loser=None, is_stalemate=True)
        )
        svc = make_service(
            duel_repo=duel_repo,
            player_repo=player_repo,
            combat_service=mock_combat_svc,
        )

        # LoadoutBuilder.from_player needs DB access; mock it to return 0-DPS unarmed ships
        async def mock_from_player(db, player_id):
            return make_ship_loadout("Unarmed", base_armour=100)  # 0 weapons → 0 DPS

        mock_db = AsyncMock()
        with patch.object(LoadoutBuilder, "from_player", side_effect=mock_from_player):
            result = await svc.accept_duel(db=mock_db, duel_id=4)

        # Two ships with 0 DPS → stalemate
        fight = result["fight_results"]
        assert fight.is_stalemate is True
        assert result["credits_transferred"] == 0
        mock_db.commit.assert_awaited_once()


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


# ---------------------------------------------------------------------------
# TestGetPendingForTarget  (B.60: challenger_name resolution)
# ---------------------------------------------------------------------------


class TestGetPendingForTarget:
    """Tests for DuelService.get_pending_for_target() — returns (duel, challenger_name) tuples."""

    @pytest.mark.asyncio
    async def test_returns_tuples_with_challenger_name(self):
        """Happy path: challenger name is resolved from player → user chain."""
        duel1 = make_duel(duel_id=1, challenger_id=10, target_id=20)
        duel2 = make_duel(duel_id=2, challenger_id=30, target_id=20)

        duel_repo = AsyncMock()
        duel_repo.get_pending_by_target.return_value = [duel1, duel2]

        # Build minimal player and user mocks
        challenger1 = MagicMock()
        challenger1.user_id = 1000
        challenger2 = MagicMock()
        challenger2.user_id = 2000

        user1 = MagicMock()
        user1.discord_username = "SamAccountX"
        user2 = MagicMock()
        user2.discord_username = "GunnerY"

        player_repo = AsyncMock()
        player_repo.get_by_id.side_effect = lambda db, pid: {10: challenger1, 30: challenger2}.get(pid)

        user_repo = AsyncMock()
        user_repo.get_by_id.side_effect = lambda db, uid: {1000: user1, 2000: user2}.get(uid)

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo, user_repo=user_repo)
        result = await svc.get_pending_for_target(db=None, target_id=20, guild_id=9999)

        assert len(result) == 2
        duel_a, name_a = result[0]
        assert duel_a is duel1
        assert name_a == "SamAccountX"
        duel_b, name_b = result[1]
        assert duel_b is duel2
        assert name_b == "GunnerY"

    @pytest.mark.asyncio
    async def test_challenger_name_none_when_player_not_found(self):
        """challenger_name is None when the challenger player row does not exist."""
        duel = make_duel(duel_id=1, challenger_id=99, target_id=20)

        duel_repo = AsyncMock()
        duel_repo.get_pending_by_target.return_value = [duel]

        player_repo = AsyncMock()
        player_repo.get_by_id.return_value = None  # player not found

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
        result = await svc.get_pending_for_target(db=None, target_id=20, guild_id=9999)

        assert len(result) == 1
        _, name = result[0]
        assert name is None

    @pytest.mark.asyncio
    async def test_challenger_name_none_when_user_not_found(self):
        """challenger_name is None when the User row does not exist for the challenger player."""
        duel = make_duel(duel_id=1, challenger_id=10, target_id=20)

        duel_repo = AsyncMock()
        duel_repo.get_pending_by_target.return_value = [duel]

        challenger = MagicMock()
        challenger.user_id = 1000

        player_repo = AsyncMock()
        player_repo.get_by_id.return_value = challenger

        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = None  # user not found

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo, user_repo=user_repo)
        result = await svc.get_pending_for_target(db=None, target_id=20, guild_id=9999)

        assert len(result) == 1
        _, name = result[0]
        assert name is None

    @pytest.mark.asyncio
    async def test_challenger_name_none_when_username_empty(self):
        """challenger_name is None when discord_username is None/empty on the User row."""
        duel = make_duel(duel_id=1, challenger_id=10, target_id=20)

        duel_repo = AsyncMock()
        duel_repo.get_pending_by_target.return_value = [duel]

        challenger = MagicMock()
        challenger.user_id = 1000

        user = MagicMock()
        user.discord_username = None  # no username stored

        player_repo = AsyncMock()
        player_repo.get_by_id.return_value = challenger

        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = user

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo, user_repo=user_repo)
        result = await svc.get_pending_for_target(db=None, target_id=20, guild_id=9999)

        assert len(result) == 1
        _, name = result[0]
        assert name is None

    @pytest.mark.asyncio
    async def test_lookup_exception_degrades_gracefully(self):
        """A DB error during challenger resolution returns None name instead of raising."""
        duel = make_duel(duel_id=1, challenger_id=10, target_id=20)

        duel_repo = AsyncMock()
        duel_repo.get_pending_by_target.return_value = [duel]

        player_repo = AsyncMock()
        player_repo.get_by_id.side_effect = RuntimeError("DB connection lost")

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
        # Should NOT raise; should degrade gracefully to name=None
        result = await svc.get_pending_for_target(db=None, target_id=20, guild_id=9999)

        assert len(result) == 1
        _, name = result[0]
        assert name is None

    @pytest.mark.asyncio
    async def test_empty_duels_returns_empty_list(self):
        """When no pending duels exist, returns an empty list."""
        duel_repo = AsyncMock()
        duel_repo.get_pending_by_target.return_value = []

        svc = make_service(duel_repo=duel_repo)
        result = await svc.get_pending_for_target(db=None, target_id=20, guild_id=9999)

        assert result == []


# ---------------------------------------------------------------------------
# TestCancelDuel  (B.64 / B.65)
# ---------------------------------------------------------------------------


class TestCancelDuel:
    """Tests for DuelService.cancel_duel()."""

    @pytest.mark.asyncio
    async def test_cancel_pending_duel_by_challenger(self):
        """B.64: challenger can cancel their own pending duel."""
        duel = make_duel(challenger_id=1, target_id=2, status="pending")
        cancelled = make_duel(challenger_id=1, target_id=2, status="cancelled")

        duel_repo = AsyncMock()
        duel_repo.get_by_id.return_value = duel
        duel_repo.update_status.return_value = cancelled

        svc = make_service(duel_repo=duel_repo)
        result = await svc.cancel_duel(db=None, duel_id=1, requesting_player_id=1)

        duel_repo.update_status.assert_awaited_once_with(None, 1, "cancelled")
        assert result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_pending_duel_by_admin(self):
        """B.65: admin can cancel any pending duel without ownership check."""
        duel = make_duel(challenger_id=1, target_id=2, status="pending")
        cancelled = make_duel(challenger_id=1, target_id=2, status="cancelled")

        duel_repo = AsyncMock()
        duel_repo.get_by_id.return_value = duel
        duel_repo.update_status.return_value = cancelled

        svc = make_service(duel_repo=duel_repo)
        # No requesting_player_id → admin path
        result = await svc.cancel_duel(db=None, duel_id=1)

        duel_repo.update_status.assert_awaited_once_with(None, 1, "cancelled")
        assert result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_duel_not_found_raises(self):
        """Cancelling a non-existent duel raises ValueError('Duel not found.')."""
        duel_repo = AsyncMock()
        duel_repo.get_by_id.return_value = None

        svc = make_service(duel_repo=duel_repo)
        with pytest.raises(ValueError, match="Duel not found"):
            await svc.cancel_duel(db=None, duel_id=999, requesting_player_id=1)

    @pytest.mark.asyncio
    async def test_cancel_non_pending_duel_raises(self):
        """Cancelling a completed duel raises ValueError('Only pending duels can be cancelled.')."""
        duel = make_duel(status="completed")

        duel_repo = AsyncMock()
        duel_repo.get_by_id.return_value = duel

        svc = make_service(duel_repo=duel_repo)
        with pytest.raises(ValueError, match="Only pending duels can be cancelled"):
            await svc.cancel_duel(db=None, duel_id=1, requesting_player_id=1)

    @pytest.mark.asyncio
    async def test_cancel_by_non_challenger_raises(self):
        """B.64: target (non-challenger) cannot cancel a duel via the self-cancel path."""
        duel = make_duel(challenger_id=1, target_id=2, status="pending")

        duel_repo = AsyncMock()
        duel_repo.get_by_id.return_value = duel

        svc = make_service(duel_repo=duel_repo)
        # requesting_player_id=2 is the target, not the challenger
        with pytest.raises(ValueError, match="Only the challenger can cancel"):
            await svc.cancel_duel(db=None, duel_id=1, requesting_player_id=2)

    @pytest.mark.asyncio
    async def test_admin_cancel_skips_ownership_check(self):
        """B.65: passing requesting_player_id=None skips challenger ownership check."""
        # Duel where challenger=5 — if ownership check ran with player_id=9, it would fail
        duel = make_duel(challenger_id=5, target_id=6, status="pending")
        cancelled = make_duel(challenger_id=5, target_id=6, status="cancelled")

        duel_repo = AsyncMock()
        duel_repo.get_by_id.return_value = duel
        duel_repo.update_status.return_value = cancelled

        svc = make_service(duel_repo=duel_repo)
        # requesting_player_id=None means admin — ownership check skipped
        result = await svc.cancel_duel(db=None, duel_id=1, requesting_player_id=None)

        assert result.status == "cancelled"


# ---------------------------------------------------------------------------
# TestGetOutgoingForChallenger  (B.64)
# ---------------------------------------------------------------------------


class TestGetOutgoingForChallenger:
    """Tests for DuelService.get_outgoing_for_challenger() — returns (duel, target_name) tuples."""

    @pytest.mark.asyncio
    async def test_returns_tuples_with_target_name(self):
        """Happy path: target name is resolved from player → user chain."""
        duel1 = make_duel(duel_id=1, challenger_id=10, target_id=20)
        duel2 = make_duel(duel_id=2, challenger_id=10, target_id=30)

        duel_repo = AsyncMock()
        duel_repo.get_pending_by_challenger.return_value = [duel1, duel2]

        target1 = MagicMock()
        target1.user_id = 2000
        target2 = MagicMock()
        target2.user_id = 3000

        user1 = MagicMock()
        user1.discord_username = "TargetAlpha"
        user2 = MagicMock()
        user2.discord_username = "TargetBeta"

        player_repo = AsyncMock()
        player_repo.get_by_id.side_effect = lambda db, pid: {20: target1, 30: target2}.get(pid)

        user_repo = AsyncMock()
        user_repo.get_by_id.side_effect = lambda db, uid: {2000: user1, 3000: user2}.get(uid)

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo, user_repo=user_repo)
        result = await svc.get_outgoing_for_challenger(db=None, challenger_id=10, guild_id=9999)

        assert len(result) == 2
        duel_a, name_a = result[0]
        assert duel_a is duel1
        assert name_a == "TargetAlpha"
        duel_b, name_b = result[1]
        assert duel_b is duel2
        assert name_b == "TargetBeta"

    @pytest.mark.asyncio
    async def test_target_name_none_when_player_not_found(self):
        """target_name is None when the target player row does not exist."""
        duel = make_duel(duel_id=1, challenger_id=10, target_id=99)

        duel_repo = AsyncMock()
        duel_repo.get_pending_by_challenger.return_value = [duel]

        player_repo = AsyncMock()
        player_repo.get_by_id.return_value = None  # target player not found

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
        result = await svc.get_outgoing_for_challenger(db=None, challenger_id=10, guild_id=9999)

        assert len(result) == 1
        _, name = result[0]
        assert name is None

    @pytest.mark.asyncio
    async def test_lookup_exception_degrades_gracefully(self):
        """A DB error during target resolution returns None name instead of raising."""
        duel = make_duel(duel_id=1, challenger_id=10, target_id=20)

        duel_repo = AsyncMock()
        duel_repo.get_pending_by_challenger.return_value = [duel]

        player_repo = AsyncMock()
        player_repo.get_by_id.side_effect = RuntimeError("DB connection lost")

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
        result = await svc.get_outgoing_for_challenger(db=None, challenger_id=10, guild_id=9999)

        assert len(result) == 1
        _, name = result[0]
        assert name is None

    @pytest.mark.asyncio
    async def test_empty_outgoing_returns_empty_list(self):
        """When no outgoing duels exist, returns an empty list."""
        duel_repo = AsyncMock()
        duel_repo.get_pending_by_challenger.return_value = []

        svc = make_service(duel_repo=duel_repo)
        result = await svc.get_outgoing_for_challenger(db=None, challenger_id=10, guild_id=9999)

        assert result == []


# ---------------------------------------------------------------------------
# Double-spend protection: _resolve_player_label
# ---------------------------------------------------------------------------


class TestResolvePlayerLabel:
    @pytest.mark.asyncio
    async def test_prefers_display_name(self):
        """display_name is returned when set."""
        player = make_player(1, credits=1000)
        player.display_name = "CoolPilot"

        user_repo = AsyncMock()
        svc = make_service(user_repo=user_repo)
        label = await svc._resolve_player_label(db=None, player=player)

        assert label == "CoolPilot"
        user_repo.get_by_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_discord_username(self):
        """Falls back to discord_username when display_name is None/falsy."""
        player = make_player(1, credits=1000)
        player.display_name = None

        mock_user = MagicMock()
        mock_user.discord_username = "galaxy55"
        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = mock_user

        svc = make_service(user_repo=user_repo)
        label = await svc._resolve_player_label(db=None, player=player)

        assert label == "galaxy55"

    @pytest.mark.asyncio
    async def test_falls_back_to_player_id(self):
        """Falls back to 'Player {id}' when both display_name and discord_username are absent."""
        player = make_player(42, credits=1000)
        player.display_name = None

        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = None

        svc = make_service(user_repo=user_repo)
        label = await svc._resolve_player_label(db=None, player=player)

        assert label == "Player 42"

    @pytest.mark.asyncio
    async def test_db_error_falls_back_to_player_id(self):
        """DB error during lookup gracefully falls back to 'Player {id}'."""
        player = make_player(7, credits=500)
        player.display_name = None

        user_repo = AsyncMock()
        user_repo.get_by_id.side_effect = RuntimeError("connection lost")

        svc = make_service(user_repo=user_repo)
        label = await svc._resolve_player_label(db=None, player=player)

        assert label == "Player 7"


# ---------------------------------------------------------------------------
# Double-spend protection: create_challenge available-balance validation
# ---------------------------------------------------------------------------


class TestCreateChallengeAvailableBalance:
    """Tests that create_challenge uses available balance (credits - pending stakes)."""

    def _make_db(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_blocks_when_challenger_has_pending_stakes(self):
        """Challenger with 10k credits and 8k in pending duels cannot wager 5k more."""
        challenger = make_player(1, credits=10_000)
        challenger.display_name = "Alice"
        target = make_player(2, credits=10_000)
        target.display_name = "Bob"

        player_repo = AsyncMock()
        player_repo.get_by_id_for_update.side_effect = lambda db, pid: challenger if pid == 1 else target

        duel_repo = AsyncMock()
        # 8k pending for challenger, 0 for target
        duel_repo.get_total_pending_stakes_for_player.side_effect = lambda db, pid, **kw: 8_000 if pid == 1 else 0
        duel_repo.get_pending_by_players.return_value = None

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
        with pytest.raises(ValueError) as exc_info:
            await svc.create_challenge(db=self._make_db(), challenger_id=1, target_id=2, stakes=5_000, guild_id=9999)

        msg = str(exc_info.value)
        assert "Alice" in msg
        assert "available" in msg.lower()

    @pytest.mark.asyncio
    async def test_blocks_when_target_has_pending_stakes(self):
        """Target with 6k credits and 5k in pending duels cannot be challenged for 3k."""
        challenger = make_player(1, credits=10_000)
        challenger.display_name = "Alice"
        target = make_player(2, credits=6_000)
        target.display_name = "Bob"

        player_repo = AsyncMock()
        player_repo.get_by_id_for_update.side_effect = lambda db, pid: challenger if pid == 1 else target

        duel_repo = AsyncMock()
        # 0 for challenger, 5k pending for target
        duel_repo.get_total_pending_stakes_for_player.side_effect = lambda db, pid, **kw: 5_000 if pid == 2 else 0
        duel_repo.get_pending_by_players.return_value = None

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
        with pytest.raises(ValueError) as exc_info:
            await svc.create_challenge(db=self._make_db(), challenger_id=1, target_id=2, stakes=3_000, guild_id=9999)

        msg = str(exc_info.value)
        assert "Bob" in msg
        assert "available" in msg.lower()

    @pytest.mark.asyncio
    async def test_allows_when_pending_stakes_leave_room(self):
        """10k credits, 3k pending, new 5k challenge should pass (7k available)."""
        challenger = make_player(1, credits=10_000)
        challenger.display_name = "Alice"
        target = make_player(2, credits=10_000)
        target.display_name = "Bob"

        player_repo = AsyncMock()
        player_repo.get_by_id_for_update.side_effect = lambda db, pid: challenger if pid == 1 else target

        duel_repo = AsyncMock()
        duel_repo.get_total_pending_stakes_for_player.return_value = 3_000
        duel_repo.get_pending_by_players.return_value = None
        created_duel = make_duel(duel_id=99, challenger_id=1, target_id=2, stakes=5_000)
        duel_repo.create.return_value = created_duel

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
        result = await svc.create_challenge(
            db=self._make_db(), challenger_id=1, target_id=2, stakes=5_000, guild_id=9999
        )

        assert result.id == 99

    @pytest.mark.asyncio
    async def test_zero_stakes_always_passes(self):
        """Friendly duel (stakes=0) passes even when player has 0 available credits."""
        challenger = make_player(1, credits=0)
        challenger.display_name = "Alice"
        target = make_player(2, credits=0)
        target.display_name = "Bob"

        player_repo = AsyncMock()
        player_repo.get_by_id_for_update.side_effect = lambda db, pid: challenger if pid == 1 else target

        duel_repo = AsyncMock()
        duel_repo.get_total_pending_stakes_for_player.return_value = 0
        duel_repo.get_pending_by_players.return_value = None
        created_duel = make_duel(duel_id=1, stakes=0)
        duel_repo.create.return_value = created_duel

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
        result = await svc.create_challenge(db=self._make_db(), challenger_id=1, target_id=2, stakes=0, guild_id=9999)
        assert result is not None

    @pytest.mark.asyncio
    async def test_error_message_includes_display_name(self):
        """Insufficient credits error message includes the player's display name."""
        challenger = make_player(1, credits=500)
        challenger.display_name = "SpartanAce"
        target = make_player(2, credits=10_000)
        target.display_name = "Bob"

        player_repo = AsyncMock()
        player_repo.get_by_id_for_update.side_effect = lambda db, pid: challenger if pid == 1 else target

        duel_repo = AsyncMock()
        duel_repo.get_total_pending_stakes_for_player.return_value = 0

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
        with pytest.raises(ValueError) as exc_info:
            await svc.create_challenge(db=self._make_db(), challenger_id=1, target_id=2, stakes=1_000, guild_id=9999)

        assert "SpartanAce" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Double-spend protection: accept_duel available-balance validation
# ---------------------------------------------------------------------------


class TestAcceptDuelAvailableBalance:
    """Tests that accept_duel uses available balance and excludes the current duel."""

    def _make_db(self):
        db = AsyncMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        return db

    def _make_duel_and_repos(
        self, *, challenger_credits, target_credits, stakes, challenger_other_pending=0, target_other_pending=0
    ):
        duel = make_duel(duel_id=5, challenger_id=1, target_id=2, stakes=stakes)
        challenger = make_player(1, credits=challenger_credits)
        challenger.display_name = "Alice"
        target = make_player(2, credits=target_credits)
        target.display_name = "Bob"

        duel_repo = AsyncMock()
        duel_repo.get_by_id.return_value = duel
        # X3-duel: accept_duel now re-reads duel under FOR UPDATE lock after player locks.
        duel_repo.get_by_id_for_update.return_value = duel

        def pending_stakes(db, pid, *, exclude_duel_id=None):
            if pid == 1:
                return challenger_other_pending
            return target_other_pending

        duel_repo.get_total_pending_stakes_for_player.side_effect = pending_stakes

        player_repo = AsyncMock()
        # get_by_id_for_update returns locked player
        player_repo.get_by_id_for_update.side_effect = lambda db, pid: challenger if pid == 1 else target

        return duel, challenger, target, duel_repo, player_repo

    @pytest.mark.asyncio
    async def test_accept_blocks_when_challenger_other_pending_exceeds_available(self):
        """Challenger has 10k, 7k in OTHER pending duels, trying to accept 6k duel → blocked."""
        duel, _challenger, _target, duel_repo, player_repo = self._make_duel_and_repos(
            challenger_credits=10_000,
            target_credits=10_000,
            stakes=6_000,
            challenger_other_pending=7_000,
            target_other_pending=0,
        )
        duel_repo.update_status = AsyncMock(return_value=duel)

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
        with pytest.raises(ValueError) as exc_info:
            await svc.accept_duel(db=self._make_db(), duel_id=5)

        msg = str(exc_info.value)
        assert "Alice" in msg
        assert "available" in msg.lower()

    @pytest.mark.asyncio
    async def test_accept_excludes_current_duel_from_pending_sum(self):
        """Accepting a 10k duel with exactly 10k credits and no OTHER pending → passes."""
        duel, _challenger, _target, duel_repo, player_repo = self._make_duel_and_repos(
            challenger_credits=10_000,
            target_credits=10_000,
            stakes=10_000,
            challenger_other_pending=0,
            target_other_pending=0,
        )
        duel_repo.update_status = AsyncMock(return_value=duel)

        # Mock LoadoutBuilder so combat can run
        with patch("services.duel_service.LoadoutBuilder") as mock_lb:
            mock_lb.from_player = AsyncMock(return_value=make_ship_loadout("ShipA"))
            svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
            # T10: fight_ships is async; use AsyncMock for deterministic stalemate result
            svc.combat_service = MagicMock()
            svc.combat_service.fight_ships = AsyncMock(
                return_value=_make_fight_results(winner=None, loser=None, is_stalemate=True)
            )
            result = await svc.accept_duel(db=self._make_db(), duel_id=5)

        assert result["stakes"] == 10_000


# ---------------------------------------------------------------------------
# Double-spend protection: cancel_underfunded_duels
# ---------------------------------------------------------------------------


class TestCancelUnderfundedDuels:
    @pytest.mark.asyncio
    async def test_cancels_only_underfunded_duels(self):
        """Player has 5k credits. 3k duel stays pending; 7k duel is cancelled."""
        player = make_player(1, credits=5_000)
        duel_ok = make_duel(duel_id=1, challenger_id=1, stakes=3_000)
        duel_bad = make_duel(duel_id=2, challenger_id=1, stakes=7_000)

        player_repo = AsyncMock()
        player_repo.get_by_id.return_value = player

        duel_repo = AsyncMock()
        duel_repo.get_all_pending_involving_player.return_value = [duel_ok, duel_bad]
        cancelled_duel = make_duel(duel_id=2, status="cancelled")
        duel_repo.update_status.return_value = cancelled_duel

        mock_db = AsyncMock()
        svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
        cancelled = await svc.cancel_underfunded_duels(db=mock_db, player_id=1)

        assert len(cancelled) == 1
        assert cancelled[0].id == 2
        # Only duel 2 should have been updated
        duel_repo.update_status.assert_called_once_with(mock_db, 2, "cancelled", commit=False)

    @pytest.mark.asyncio
    async def test_cancels_in_both_roles(self):
        """Player is target in one unaffordable duel and challenger in another — both cancelled."""
        player = make_player(1, credits=1_000)
        duel_as_challenger = make_duel(duel_id=10, challenger_id=1, target_id=99, stakes=2_000)
        duel_as_target = make_duel(duel_id=11, challenger_id=99, target_id=1, stakes=5_000)

        player_repo = AsyncMock()
        player_repo.get_by_id.return_value = player

        duel_repo = AsyncMock()
        duel_repo.get_all_pending_involving_player.return_value = [duel_as_challenger, duel_as_target]
        duel_repo.update_status.side_effect = lambda db, did, status, **kw: make_duel(duel_id=did, status=status)

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
        cancelled = await svc.cancel_underfunded_duels(db=AsyncMock(), player_id=1)

        assert len(cancelled) == 2
        cancelled_ids = {d.id for d in cancelled}
        assert cancelled_ids == {10, 11}

    @pytest.mark.asyncio
    async def test_returns_empty_when_all_duels_affordable(self):
        """No duels cancelled when player can cover all of them."""
        player = make_player(1, credits=10_000)
        duel1 = make_duel(duel_id=1, stakes=3_000)
        duel2 = make_duel(duel_id=2, stakes=4_000)

        player_repo = AsyncMock()
        player_repo.get_by_id.return_value = player

        duel_repo = AsyncMock()
        duel_repo.get_all_pending_involving_player.return_value = [duel1, duel2]

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
        cancelled = await svc.cancel_underfunded_duels(db=AsyncMock(), player_id=1)

        assert cancelled == []
        duel_repo.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_nonexistent_player(self):
        """Returns empty list without error when player_id doesn't exist."""
        player_repo = AsyncMock()
        player_repo.get_by_id.return_value = None

        svc = make_service(player_repo=player_repo)
        cancelled = await svc.cancel_underfunded_duels(db=AsyncMock(), player_id=999)

        assert cancelled == []

    @pytest.mark.asyncio
    async def test_idempotent_no_pending(self):
        """Returns empty list when player has no pending duels."""
        player = make_player(1, credits=100)

        player_repo = AsyncMock()
        player_repo.get_by_id.return_value = player

        duel_repo = AsyncMock()
        duel_repo.get_all_pending_involving_player.return_value = []

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
        cancelled = await svc.cancel_underfunded_duels(db=AsyncMock(), player_id=1)

        assert cancelled == []

    @pytest.mark.asyncio
    async def test_zero_stakes_friendly_duel_never_cancelled(self):
        """Friendly duels (stakes=0) are never cancelled even when player has 0 credits."""
        player = make_player(1, credits=0)
        friendly = make_duel(duel_id=1, challenger_id=1, stakes=0)

        player_repo = AsyncMock()
        player_repo.get_by_id.return_value = player

        duel_repo = AsyncMock()
        duel_repo.get_all_pending_involving_player.return_value = [friendly]

        svc = make_service(duel_repo=duel_repo, player_repo=player_repo)
        cancelled = await svc.cancel_underfunded_duels(db=AsyncMock(), player_id=1)

        assert cancelled == []
        duel_repo.update_status.assert_not_called()


# ---------------------------------------------------------------------------
# P2-T8a: Winner decoded from winner_side (snowflake), not from ship name
# ---------------------------------------------------------------------------


def _make_accept_duel_scaffolding(
    *,
    challenger_id: int,
    target_id: int,
    challenger_ship: str,
    target_ship: str,
    stakes: int = 200,
    duel_id: int = 99,
):
    """Return (challenger, target, duel, duel_repo, player_repo) for accept_duel tests."""
    ch = make_player(challenger_id, credits=1000)
    ch.display_name = f"Player{challenger_id}"
    tg = make_player(target_id, credits=1000)
    tg.display_name = f"Player{target_id}"

    duel = make_duel(duel_id=duel_id, challenger_id=challenger_id, target_id=target_id, stakes=stakes)

    duel_repo = AsyncMock()
    duel_repo.get_by_id.return_value = duel
    # X3-duel: accept_duel now re-reads duel under FOR UPDATE lock after player locks.
    duel_repo.get_by_id_for_update.return_value = duel
    duel_repo.update_status.return_value = duel
    duel_repo.get_total_pending_stakes_for_player.return_value = 0

    player_repo = AsyncMock()
    player_repo.get_by_id_for_update.side_effect = lambda db, pid: {
        challenger_id: ch,
        target_id: tg,
    }.get(pid)

    return ch, tg, duel, duel_repo, player_repo


class TestWinnerDecodedBySnowflake:
    """P2-T8a: winner decoded from winner_side (immutable snowflake), never by ship name.

    D6 same-name decisive tests prove a name-compare implementation would be
    ambiguous/wrong, while the snowflake-based implementation is unambiguous.
    """

    # ------------------------------------------------------------------
    # D6 — same-name decisive (core test)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_same_name_challenger_wins_side1(self):
        """D6/side-1: Both players fly IDENTICALLY-named ships and share the SAME display name.
        winner_side=1 → challenger wins.  A name-compare would be ambiguous.
        """
        SHARED_SHIP = "Betty"
        SHARED_DISPLAY = "Pilot"
        CHALLENGER_ID = 1001
        TARGET_ID = 1002

        ch, tg, duel, duel_repo, player_repo = _make_accept_duel_scaffolding(
            challenger_id=CHALLENGER_ID,
            target_id=TARGET_ID,
            challenger_ship=SHARED_SHIP,
            target_ship=SHARED_SHIP,
            stakes=200,
        )
        # Both share the same display name
        ch.display_name = SHARED_DISPLAY
        tg.display_name = SHARED_DISPLAY

        mock_combat_svc = MagicMock()
        mock_combat_svc.fight_ships = AsyncMock(
            return_value=_make_fight_results(
                winner=SHARED_SHIP,
                loser=SHARED_SHIP,
                winner_side=1,  # challenger (side 1) wins
            )
        )
        svc = make_service(duel_repo=duel_repo, player_repo=player_repo, combat_service=mock_combat_svc)

        async def mock_from_player(db, player_id):
            return make_ship_loadout(SHARED_SHIP)

        mock_db = AsyncMock()
        with patch.object(LoadoutBuilder, "from_player", side_effect=mock_from_player):
            result = await svc.accept_duel(db=mock_db, duel_id=duel.id)

        # Challenger (player_id=1001) won — credits transferred TO challenger
        assert ch.credits == 1200, f"challenger credits should be 1200, got {ch.credits}"
        assert tg.credits == 800, f"target credits should be 800, got {tg.credits}"
        assert ch.duel_wins == 1
        assert ch.duel_losses == 0
        assert tg.duel_wins == 0
        assert tg.duel_losses == 1
        assert result["credits_transferred"] == 200

    @pytest.mark.asyncio
    async def test_same_name_target_wins_side2(self):
        """D6/side-2: Both players fly IDENTICALLY-named ships and share the SAME display name.
        winner_side=2 → target wins.  A name-compare would give the wrong player here.
        """
        SHARED_SHIP = "Betty"
        SHARED_DISPLAY = "Pilot"
        CHALLENGER_ID = 2001
        TARGET_ID = 2002

        ch, tg, duel, duel_repo, player_repo = _make_accept_duel_scaffolding(
            challenger_id=CHALLENGER_ID,
            target_id=TARGET_ID,
            challenger_ship=SHARED_SHIP,
            target_ship=SHARED_SHIP,
            stakes=200,
        )
        ch.display_name = SHARED_DISPLAY
        tg.display_name = SHARED_DISPLAY

        mock_combat_svc = MagicMock()
        mock_combat_svc.fight_ships = AsyncMock(
            return_value=_make_fight_results(
                winner=SHARED_SHIP,
                loser=SHARED_SHIP,
                winner_side=2,  # target (side 2) wins
            )
        )
        svc = make_service(duel_repo=duel_repo, player_repo=player_repo, combat_service=mock_combat_svc)

        async def mock_from_player(db, player_id):
            return make_ship_loadout(SHARED_SHIP)

        mock_db = AsyncMock()
        with patch.object(LoadoutBuilder, "from_player", side_effect=mock_from_player):
            result = await svc.accept_duel(db=mock_db, duel_id=duel.id)

        # Target (player_id=2002) won — credits transferred TO target
        assert tg.credits == 1200, f"target credits should be 1200, got {tg.credits}"
        assert ch.credits == 800, f"challenger credits should be 800, got {ch.credits}"
        assert tg.duel_wins == 1
        assert tg.duel_losses == 0
        assert ch.duel_wins == 0
        assert ch.duel_losses == 1
        assert result["credits_transferred"] == 200

    @pytest.mark.asyncio
    async def test_same_name_anti_vacuous_name_compare_fails(self):
        """Anti-vacuous: behavioral proof that the service uses winner_side, not name comparison.

        Both ships are named "Betty" and both players share the display name "Pilot".
        winner_side=2 → target wins.  A name-keyed implementation would compare
        winner_name ("Betty") to challenger_ship_name ("Betty") → always True → always
        credits the challenger, which is WRONG here.

        This test calls accept_duel and asserts that credits moved TO the target (not
        the challenger).  Reverting duel_service to a name-compare implementation
        would credit the challenger instead, making this test fail.
        """
        SHARED_SHIP = "Betty"
        SHARED_DISPLAY = "Pilot"
        CHALLENGER_ID = 3001
        TARGET_ID = 3002

        ch, tg, duel, duel_repo, player_repo = _make_accept_duel_scaffolding(
            challenger_id=CHALLENGER_ID,
            target_id=TARGET_ID,
            challenger_ship=SHARED_SHIP,
            target_ship=SHARED_SHIP,
            stakes=200,
        )
        ch.display_name = SHARED_DISPLAY
        tg.display_name = SHARED_DISPLAY

        mock_combat_svc = MagicMock()
        mock_combat_svc.fight_ships = AsyncMock(
            return_value=_make_fight_results(
                winner=SHARED_SHIP,
                loser=SHARED_SHIP,
                winner_side=2,  # target (side 2) wins
            )
        )
        svc = make_service(duel_repo=duel_repo, player_repo=player_repo, combat_service=mock_combat_svc)

        async def mock_from_player(db, player_id):
            return make_ship_loadout(SHARED_SHIP)

        mock_db = AsyncMock()
        with patch.object(LoadoutBuilder, "from_player", side_effect=mock_from_player):
            result = await svc.accept_duel(db=mock_db, duel_id=duel.id)

        # Target (player_id=3002) won — credits must flow TO target.
        # A name-compare implementation would credit challenger (wrong) instead.
        assert tg.credits == 1200, (
            f"target should have 1200 credits after winning (got {tg.credits}); "
            "a name-compare revert would credit challenger instead"
        )
        assert ch.credits == 800, f"challenger should have 800 credits after losing (got {ch.credits})"
        assert tg.duel_wins == 1
        assert ch.duel_losses == 1
        assert result["credits_transferred"] == 200

    # ------------------------------------------------------------------
    # Distinct-name regression tests
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_distinct_name_challenger_wins(self):
        """Regression: distinct ship names, challenger wins → correct player credited."""
        CHALLENGER_ID = 4001
        TARGET_ID = 4002

        ch, tg, duel, duel_repo, player_repo = _make_accept_duel_scaffolding(
            challenger_id=CHALLENGER_ID,
            target_id=TARGET_ID,
            challenger_ship="Viper",
            target_ship="Cobra",
            stakes=150,
        )

        mock_combat_svc = MagicMock()
        mock_combat_svc.fight_ships = AsyncMock(
            return_value=_make_fight_results(winner="Viper", loser="Cobra", winner_side=1)
        )
        svc = make_service(duel_repo=duel_repo, player_repo=player_repo, combat_service=mock_combat_svc)

        async def mock_from_player(db, player_id):
            return make_ship_loadout("Viper" if player_id == CHALLENGER_ID else "Cobra")

        mock_db = AsyncMock()
        with patch.object(LoadoutBuilder, "from_player", side_effect=mock_from_player):
            result = await svc.accept_duel(db=mock_db, duel_id=duel.id)

        assert ch.credits == 1150
        assert tg.credits == 850
        assert ch.duel_wins == 1
        assert tg.duel_losses == 1
        assert result["credits_transferred"] == 150

    @pytest.mark.asyncio
    async def test_distinct_name_target_wins(self):
        """Regression: distinct ship names, target wins → correct player credited."""
        CHALLENGER_ID = 5001
        TARGET_ID = 5002

        ch, tg, duel, duel_repo, player_repo = _make_accept_duel_scaffolding(
            challenger_id=CHALLENGER_ID,
            target_id=TARGET_ID,
            challenger_ship="Viper",
            target_ship="Cobra",
            stakes=150,
        )

        mock_combat_svc = MagicMock()
        mock_combat_svc.fight_ships = AsyncMock(
            return_value=_make_fight_results(winner="Cobra", loser="Viper", winner_side=2)
        )
        svc = make_service(duel_repo=duel_repo, player_repo=player_repo, combat_service=mock_combat_svc)

        async def mock_from_player(db, player_id):
            return make_ship_loadout("Viper" if player_id == CHALLENGER_ID else "Cobra")

        mock_db = AsyncMock()
        with patch.object(LoadoutBuilder, "from_player", side_effect=mock_from_player):
            result = await svc.accept_duel(db=mock_db, duel_id=duel.id)

        assert tg.credits == 1150
        assert ch.credits == 850
        assert tg.duel_wins == 1
        assert ch.duel_losses == 1
        assert result["credits_transferred"] == 150

    # ------------------------------------------------------------------
    # Stalemate / draw
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_stalemate_no_credits_transferred(self):
        """Stalemate (winner_side=None): no credits move, stats unchanged."""
        CHALLENGER_ID = 6001
        TARGET_ID = 6002

        ch, tg, duel, duel_repo, player_repo = _make_accept_duel_scaffolding(
            challenger_id=CHALLENGER_ID,
            target_id=TARGET_ID,
            challenger_ship="Iron",
            target_ship="Steel",
            stakes=300,
        )

        mock_combat_svc = MagicMock()
        mock_combat_svc.fight_ships = AsyncMock(
            return_value=_make_fight_results(winner=None, loser=None, is_stalemate=True, winner_side=None)
        )
        svc = make_service(duel_repo=duel_repo, player_repo=player_repo, combat_service=mock_combat_svc)

        async def mock_from_player(db, player_id):
            return make_ship_loadout("Iron" if player_id == CHALLENGER_ID else "Steel")

        mock_db = AsyncMock()
        with patch.object(LoadoutBuilder, "from_player", side_effect=mock_from_player):
            result = await svc.accept_duel(db=mock_db, duel_id=duel.id)

        assert result["credits_transferred"] == 0
        assert ch.credits == 1000
        assert tg.credits == 1000
        assert ch.duel_wins == 0
        assert ch.duel_losses == 0
        assert tg.duel_wins == 0
        assert tg.duel_losses == 0
        fight = result["fight_results"]
        assert fight.is_stalemate is True

    # ------------------------------------------------------------------
    # Embed presentation: names still surfaced (not removed)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_winner_name_still_present_in_result_for_embed(self):
        """Embed presentation: winner_name and loser_name are still present in fight_results
        for use by the embed — only the winner DECISION changed to snowflake.
        """
        CHALLENGER_ID = 7001
        TARGET_ID = 7002

        ch, tg, duel, duel_repo, player_repo = _make_accept_duel_scaffolding(
            challenger_id=CHALLENGER_ID,
            target_id=TARGET_ID,
            challenger_ship="Panther",
            target_ship="Jaguar",
            stakes=50,
        )

        mock_combat_svc = MagicMock()
        mock_combat_svc.fight_ships = AsyncMock(
            return_value=_make_fight_results(winner="Panther", loser="Jaguar", winner_side=1)
        )
        svc = make_service(duel_repo=duel_repo, player_repo=player_repo, combat_service=mock_combat_svc)

        async def mock_from_player(db, player_id):
            return make_ship_loadout("Panther" if player_id == CHALLENGER_ID else "Jaguar")

        mock_db = AsyncMock()
        with patch.object(LoadoutBuilder, "from_player", side_effect=mock_from_player):
            result = await svc.accept_duel(db=mock_db, duel_id=duel.id)

        # Presentation fields still present
        fight = result["fight_results"]
        assert fight.winner_name == "Panther", "winner_name must remain for embed presentation"
        assert fight.loser_name == "Jaguar", "loser_name must remain for embed presentation"
        # Snowflake-based decision was correct
        assert ch.duel_wins == 1
        assert tg.duel_losses == 1
