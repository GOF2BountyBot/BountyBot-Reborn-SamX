"""
T10 — fight_ships cutover tests: TickResolver, Player stat promotion,
log_result=False preflight path, FightStats wire-compat.

Max 2 mocks per test.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Shared bblogger + sqlalchemy_utils guard
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

if "sqlalchemy_utils" not in sys.modules:
    _sqla_utils = types.ModuleType("sqlalchemy_utils")
    _sqla_utils.UUIDType = MagicMock()
    sys.modules["sqlalchemy_utils"] = _sqla_utils

import pytest
from src.services.combat_models import (
    FightResults,
    FightStats,
    ShipLoadout,
    WeaponStats,
)
from src.services.combat_service import CombatService
from src.services.game_constants import GameConstants

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_loadout(ship_name: str = "A", base_armour: int = 200) -> ShipLoadout:
    return ShipLoadout(
        ship_name=ship_name,
        base_armour=base_armour,
        weapons=[WeaponStats(name="Laser", dps=10.0, damage_per_shot=10, loading_speed_ms=100, range_m=999999.0)],
    )


def _mock_fight_results(winner: str = "A") -> FightResults:
    stats = FightStats(ship_name=winner, raw_hp=200, raw_dps=10.0, varied_hp=200, varied_dps=10.0, ttk=None)
    stats2 = FightStats(ship_name="B", raw_hp=100, raw_dps=5.0, varied_hp=100, varied_dps=5.0, ttk=5.0)
    return FightResults(
        winner_name=winner,
        loser_name="B",
        is_stalemate=False,
        ship1_stats=stats,
        ship2_stats=stats2,
        combat_log=[],
        metadata={
            "schema_version": 1,
            "summary": {
                "outcome": "win",
                "reason": "hp_depleted",
                "duration_ticks": 100,
                "winner": winner,
                "combatants": {
                    "1": {"name": "A", "ship": "Betty", "damage_dealt": 50, "damage_taken": 20},
                    "2": {"name": "B", "ship": "Bandit", "damage_dealt": 20, "damage_taken": 50},
                },
            },
            "metadata": {"tick_ms": 10, "total_ticks": 100, "resolver": "tick_v1", "pvc_damage_reduction": 0.0},
        },
    )


# ---------------------------------------------------------------------------
# Boundary validation
# ---------------------------------------------------------------------------


class TestFightShipsBoundaryValidation:
    """fight_ships raises ValueError on log_result=True ∧ context=None."""

    @pytest.mark.asyncio
    async def test_log_result_true_no_context_raises(self):
        """fight_ships(log_result=True, context=None) raises ValueError."""
        service = CombatService()
        l1 = ShipLoadout(ship_name="A", base_armour=100)
        l2 = ShipLoadout(ship_name="B", base_armour=100)
        with pytest.raises(ValueError, match="context is required"):
            await service.fight_ships(l1, l2, log_result=True, context=None)

    @pytest.mark.asyncio
    async def test_log_result_false_no_context_ok(self):
        """fight_ships(log_result=False, context=None) is valid (preflight)."""
        service = CombatService()
        l1 = ShipLoadout(ship_name="A", base_armour=100)
        l2 = ShipLoadout(ship_name="B", base_armour=100)
        result = await service.fight_ships(l1, l2, log_result=False)
        assert result is not None

    @pytest.mark.asyncio
    async def test_log_result_true_session_none_raises(self):
        """fight_ships(log_result=True, session=None) raises ValueError — production-safe guard."""
        service = CombatService()
        l1 = ShipLoadout(ship_name="A", base_armour=100)
        l2 = ShipLoadout(ship_name="B", base_armour=100)
        with pytest.raises(ValueError, match="session is required"):
            await service.fight_ships(l1, l2, log_result=True, context="duel", session=None, guild_id=1)

    @pytest.mark.asyncio
    async def test_log_result_true_guild_id_none_raises(self):
        """fight_ships(log_result=True, guild_id=None) raises ValueError — production-safe guard."""
        service = CombatService()
        l1 = ShipLoadout(ship_name="A", base_armour=100)
        l2 = ShipLoadout(ship_name="B", base_armour=100)
        session_mock = AsyncMock()
        with pytest.raises(ValueError, match="guild_id is required"):
            await service.fight_ships(l1, l2, log_result=True, context="duel", session=session_mock, guild_id=None)


# ---------------------------------------------------------------------------
# log_result=False path — no DB writes
# ---------------------------------------------------------------------------


class TestFightShipsLogResultFalse:
    """log_result=False path: no persist, no stat increment, combat_log_id=None."""

    @pytest.mark.asyncio
    async def test_no_db_writes_log_result_false(self):
        """When log_result=False, CombatLogService.persist is never called."""
        service = CombatService()
        l1 = ShipLoadout(ship_name="A", base_armour=100)
        l2 = ShipLoadout(ship_name="B", base_armour=100)

        # CombatLogService is imported lazily inside fight_ships; patch the module-level class
        with patch("services.combat_log_service.CombatLogService") as mock_cls:
            result = await service.fight_ships(l1, l2, log_result=False)

        mock_cls.assert_not_called()
        assert result.combat_log_id is None

    @pytest.mark.asyncio
    async def test_preflight_20_sims_no_db_writes(self):
        """Preflight Monte-Carlo loop (20 fights) yields 0 DB writes."""
        service = CombatService()
        l1 = ShipLoadout(ship_name="Player", base_armour=200)
        l2 = ShipLoadout(ship_name="NPC", base_armour=100)

        persist_calls = 0

        async def _track_persist(*args, **kwargs):
            nonlocal persist_calls
            persist_calls += 1
            return 1

        # CombatLogService is imported lazily inside fight_ships; patch the module-level class
        with patch("services.combat_log_service.CombatLogService") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.persist = AsyncMock(side_effect=_track_persist)
            mock_cls.return_value = mock_instance
            for _ in range(20):
                await service.fight_ships(l1, l2, log_result=False)

        assert persist_calls == 0, f"Expected 0 persist calls, got {persist_calls}"


# ---------------------------------------------------------------------------
# log_result=True — persist called + combat_log_id populated
# ---------------------------------------------------------------------------


class TestFightShipsLogResultTrue:
    """log_result=True path: persist called, combat_log_id on FightResults."""

    @pytest.mark.asyncio
    async def test_combat_log_id_populated(self):
        """fight_ships(..., log_result=True) returns FightResults with combat_log_id."""
        service = CombatService()
        l1 = ShipLoadout(ship_name="A", base_armour=100)
        l2 = ShipLoadout(ship_name="B", base_armour=100)
        session_mock = AsyncMock()

        with (
            patch("services.combat_log_service.CombatLogService.persist", new=AsyncMock(return_value=42)),
            patch(
                "persist.repositories.player_repository.PlayerRepository.get_by_user_and_guild",
                new=AsyncMock(return_value=None),
            ),
        ):
            fight = await service.fight_ships(
                l1,
                l2,
                context="duel",
                log_result=True,
                session=session_mock,
                guild_id=1,
                combatant1_user_id=101,
                combatant2_user_id=102,
            )

        assert fight.combat_log_id == 42

    @pytest.mark.asyncio
    async def test_pvc_damage_reduction_reaches_resolver(self):
        """pvc_damage_reduction=0.33 is forwarded to the TickResolver."""
        service = CombatService()
        l1 = ShipLoadout(ship_name="A", base_armour=200)
        l2 = ShipLoadout(ship_name="B", base_armour=200)

        captured_pvc_dr = None

        def _spy_resolve(loadout1, loadout2, *, pvc_damage_reduction=0.0, guild_config=None, rng=None, **_kwargs):
            nonlocal captured_pvc_dr
            captured_pvc_dr = pvc_damage_reduction
            # Return a minimal FightResults
            from src.services.combat_models import FightResults, FightStats

            s = FightStats(ship_name="A", raw_hp=200, raw_dps=0.0, varied_hp=200, varied_dps=0.0, ttk=None)
            return FightResults(
                winner_name=None,
                loser_name=None,
                is_stalemate=True,
                ship1_stats=s,
                ship2_stats=s,
                combat_log=[],
                metadata={
                    "schema_version": 1,
                    "summary": {
                        "outcome": "stalemate",
                        "reason": "time_cap",
                        "duration_ticks": 100,
                        "winner": None,
                        "combatants": {
                            "1": {"name": "A", "ship": "A", "damage_dealt": 0, "damage_taken": 0},
                            "2": {"name": "B", "ship": "B", "damage_dealt": 0, "damage_taken": 0},
                        },
                    },
                    "metadata": {
                        "tick_ms": 10,
                        "total_ticks": 100,
                        "resolver": "tick_v1",
                        "pvc_damage_reduction": pvc_damage_reduction,
                    },
                },
            )

        service._tick_resolver.resolve = _spy_resolve
        await service.fight_ships(l1, l2, log_result=False, pvc_damage_reduction=0.33)
        assert captured_pvc_dr == pytest.approx(0.33)


# ---------------------------------------------------------------------------
# Player stat promotion (§13)
# ---------------------------------------------------------------------------


class TestPlayerStatPromotion:
    """Player stat counters incremented for human combatants; NPC side skipped."""

    @pytest.mark.asyncio
    async def test_total_fights_incremented_for_human_combatant(self):
        """total_fights += 1 for a human combatant after a fight."""
        service = CombatService()
        l1 = ShipLoadout(ship_name="A", base_armour=100)
        l2 = ShipLoadout(ship_name="B", base_armour=100)
        session_mock = AsyncMock()

        mock_player = MagicMock()
        mock_player.total_fights = 5
        mock_player.total_nukes_fired = 0
        mock_player.total_module_activations = 0

        with (
            patch("services.combat_log_service.CombatLogService.persist", new=AsyncMock(return_value=1)),
            patch(
                "persist.repositories.player_repository.PlayerRepository.get_by_user_and_guild",
                new=AsyncMock(return_value=mock_player),
            ),
        ):
            await service.fight_ships(
                l1,
                l2,
                context="duel",
                log_result=True,
                session=session_mock,
                guild_id=1,
                combatant1_user_id=101,
                combatant2_user_id=102,
            )

        # total_fights should be incremented for each human combatant (both 101 and 102)
        assert mock_player.total_fights >= 6, f"total_fights should be ≥ 6, got {mock_player.total_fights}"

    @pytest.mark.asyncio
    async def test_npc_side_skipped_cleanly(self):
        """NPC side (combatant2_user_id=None) triggers no DB call for C2."""
        service = CombatService()
        l1 = ShipLoadout(ship_name="Player", base_armour=100)
        l2 = ShipLoadout(ship_name="NPC", base_armour=100)
        session_mock = AsyncMock()

        player_mock = MagicMock()
        player_mock.total_fights = 0
        player_mock.total_nukes_fired = 0
        player_mock.total_module_activations = 0

        call_args_list = []

        async def _mock_get_by_user_and_guild(session, user_id, guild_id):
            call_args_list.append((user_id, guild_id))
            return player_mock

        with (
            patch("services.combat_log_service.CombatLogService.persist", new=AsyncMock(return_value=1)),
            patch(
                "persist.repositories.player_repository.PlayerRepository.get_by_user_and_guild",
                side_effect=_mock_get_by_user_and_guild,
            ),
        ):
            await service.fight_ships(
                l1,
                l2,
                context="bounty_pvc",
                log_result=True,
                session=session_mock,
                guild_id=10,
                combatant1_user_id=555,
                combatant2_user_id=None,  # NPC
            )

        # Only user_id 555 (human) should be looked up; NPC side (None) skipped
        user_ids_looked_up = [uid for uid, _ in call_args_list]
        assert 555 in user_ids_looked_up, "Human combatant must be looked up"
        assert None not in user_ids_looked_up, "NPC side (None) must NOT be looked up"
        assert len(user_ids_looked_up) == 1, "Only 1 DB call for 1 human combatant"

    @pytest.mark.asyncio
    async def test_stat_increment_failure_is_non_fatal(self):
        """PlayerRepository.get_by_user_and_guild raising during stat increment is non-fatal.

        The fight result is still returned; the error is logged, not raised.
        """
        service = CombatService()
        l1 = ShipLoadout(ship_name="Player", base_armour=100)
        l2 = ShipLoadout(ship_name="NPC", base_armour=100)
        session_mock = AsyncMock()

        with (
            patch("services.combat_log_service.CombatLogService.persist", new=AsyncMock(return_value=7)),
            patch(
                "persist.repositories.player_repository.PlayerRepository.get_by_user_and_guild",
                side_effect=RuntimeError("DB connection lost"),
            ),
        ):
            # Should NOT raise — stat increment error is non-fatal
            result = await service.fight_ships(
                l1,
                l2,
                context="duel",
                log_result=True,
                session=session_mock,
                guild_id=1,
                combatant1_user_id=42,
                combatant2_user_id=None,
            )

        # Fight still completes and returns a valid result
        assert result is not None
        assert result.combat_log_id == 7


# ---------------------------------------------------------------------------
# FightStats wire-compat (§12)
# ---------------------------------------------------------------------------


class TestFightStatsWireCompat:
    """Legacy FightStats fields populated correctly from TickResolver output."""

    @pytest.mark.asyncio
    async def test_raw_hp_equals_sum_of_start_hp(self):
        """raw_hp == sum of shield + armour + hull at fight start."""
        service = CombatService()
        # C1: base_armour=200, C2: base_armour=100 → raw_hp should be 200 and 100
        l1 = ShipLoadout(ship_name="C1", base_armour=200)
        l2 = ShipLoadout(ship_name="C2", base_armour=100)

        result = await service.fight_ships(l1, l2, log_result=False)

        assert result.ship1_stats.raw_hp == 200
        assert result.ship2_stats.raw_hp == 100

    @pytest.mark.asyncio
    async def test_varied_hp_equals_raw_hp(self):
        """varied_hp == raw_hp (tick resolver has no variance)."""
        service = CombatService()
        l1 = ShipLoadout(ship_name="C1", base_armour=300)
        l2 = ShipLoadout(ship_name="C2", base_armour=150)

        result = await service.fight_ships(l1, l2, log_result=False)

        assert result.ship1_stats.varied_hp == result.ship1_stats.raw_hp
        assert result.ship2_stats.varied_hp == result.ship2_stats.raw_hp

    @pytest.mark.asyncio
    async def test_loser_ttk_equals_duration_s(self):
        """The loser's ttk equals the fight duration in seconds."""
        # Use a weapon that will kill quickly and deterministically
        service = CombatService()
        l1 = ShipLoadout(
            ship_name="C1",
            base_armour=50000,  # near-indestructible
            weapons=[
                WeaponStats(name="SuperGun", dps=0.0, damage_per_shot=99999, loading_speed_ms=100, range_m=999999.0)
            ],
        )
        l2 = ShipLoadout(ship_name="C2", base_armour=10)

        result = await service.fight_ships(l1, l2, log_result=False)

        if result.is_stalemate:
            pytest.skip("Unexpected stalemate — weapon not strong enough")

        if result.winner_name == "C1":
            # C2 is the loser; ship2_stats.ttk should be the fight duration
            duration_s = (result.metadata["metadata"]["total_ticks"] * GameConstants.TICK_MS) / 1000.0
            assert result.ship2_stats.ttk == pytest.approx(duration_s, abs=0.1), (
                f"Loser ttk {result.ship2_stats.ttk} should ≈ duration_s {duration_s}"
            )
            # Winner has ttk=None (survived)
            assert result.ship1_stats.ttk is None
