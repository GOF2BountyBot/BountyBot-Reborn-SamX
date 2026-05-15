"""Unit tests for CombatPreflightService."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Guard: mock shared.bblogger before importing service code.
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

from services.combat_preflight_service import CombatPreflightService, PreflightVerdict

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service() -> CombatPreflightService:
    svc = CombatPreflightService.__new__(CombatPreflightService)
    svc.bounty_repo = MagicMock()
    svc.combat_service = MagicMock()
    return svc


def _criminal_bounty(ship_name: str = "Raider") -> object:
    return SimpleNamespace(
        id=1,
        criminal_ship={"ship_name": ship_name, "ship_armour": 80, "weapons": [], "turrets": []},
    )


def _fight_result(winner: str, is_stalemate: bool = False) -> object:
    return SimpleNamespace(winner_name=winner, is_stalemate=is_stalemate)


# ===========================================================================
# Tests: NO_DATA verdict
# ===========================================================================


class TestEstimateNoData:
    """estimate() returns NO_DATA when there is nothing to simulate against."""

    @pytest.mark.asyncio
    async def test_no_active_bounties_returns_no_data(self):
        """Returns NO_DATA when no active bounties exist at the target tier."""
        svc = _make_service()
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

        result = await svc.estimate(MagicMock(), player_id=1, guild_id=1, target_tier="Silver")

        assert result.verdict == PreflightVerdict.NO_DATA
        assert result.sims_run == 0
        assert result.sample_size == 0

    @pytest.mark.asyncio
    async def test_bounties_without_criminal_ship_returns_no_data(self):
        """Returns NO_DATA when bounties exist but none have criminal_ship data."""
        svc = _make_service()
        empty_bounty = SimpleNamespace(id=1, criminal_ship=None)
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[empty_bounty])

        result = await svc.estimate(MagicMock(), player_id=1, guild_id=1, target_tier="Silver")

        assert result.verdict == PreflightVerdict.NO_DATA
        assert result.sims_run == 0

    @pytest.mark.asyncio
    async def test_loadout_builder_failure_returns_no_data(self):
        """Returns NO_DATA when the player's loadout cannot be built."""
        svc = _make_service()
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[_criminal_bounty()])

        with patch(
            "services.combat_preflight_service.LoadoutBuilder.from_player",
            new=AsyncMock(side_effect=RuntimeError("no active ship")),
        ):
            result = await svc.estimate(MagicMock(), player_id=1, guild_id=1, target_tier="Silver")

        assert result.verdict == PreflightVerdict.NO_DATA
        assert result.sims_run == 0


# ===========================================================================
# Tests: verdict thresholds
# ===========================================================================


class TestEstimateVerdicts:
    """estimate() returns the correct GREEN / YELLOW / RED verdict."""

    @pytest.mark.asyncio
    async def test_green_when_player_wins_all_sims(self):
        """Verdict is GREEN when player wins every simulated fight (>= 0.75)."""
        from services.combat_models import ShipLoadout

        svc = _make_service()
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[_criminal_bounty()])
        player_loadout = ShipLoadout(ship_name="Player", base_armour=200)
        svc.combat_service.fight_ships = MagicMock(return_value=_fight_result(winner="Player"))

        with patch(
            "services.combat_preflight_service.LoadoutBuilder.from_player",
            new=AsyncMock(return_value=player_loadout),
        ):
            result = await svc.estimate(MagicMock(), player_id=1, guild_id=1, target_tier="Silver", num_sims=20)

        assert result.verdict == PreflightVerdict.GREEN
        assert result.player_win_rate >= 0.75

    @pytest.mark.asyncio
    async def test_red_when_criminal_wins_all_sims(self):
        """Verdict is RED when criminal wins every simulated fight (criminal_win_rate >= 0.75)."""
        from services.combat_models import ShipLoadout

        svc = _make_service()
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[_criminal_bounty()])
        player_loadout = ShipLoadout(ship_name="Player", base_armour=50)
        svc.combat_service.fight_ships = MagicMock(return_value=_fight_result(winner="Raider"))

        with patch(
            "services.combat_preflight_service.LoadoutBuilder.from_player",
            new=AsyncMock(return_value=player_loadout),
        ):
            result = await svc.estimate(MagicMock(), player_id=1, guild_id=1, target_tier="Silver", num_sims=20)

        assert result.verdict == PreflightVerdict.RED
        assert result.criminal_win_rate >= 0.75

    @pytest.mark.asyncio
    async def test_yellow_when_split_evenly(self):
        """Verdict is YELLOW when neither player nor criminal wins >= 75% of fights."""
        from services.combat_models import ShipLoadout

        svc = _make_service()
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[_criminal_bounty()])
        player_loadout = ShipLoadout(ship_name="Player", base_armour=100)

        call_count = 0

        def _alternating(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            winner = "Player" if call_count % 2 == 0 else "Raider"
            return _fight_result(winner=winner)

        svc.combat_service.fight_ships = MagicMock(side_effect=_alternating)

        with patch(
            "services.combat_preflight_service.LoadoutBuilder.from_player",
            new=AsyncMock(return_value=player_loadout),
        ):
            result = await svc.estimate(MagicMock(), player_id=1, guild_id=1, target_tier="Silver", num_sims=20)

        assert result.verdict == PreflightVerdict.YELLOW

    @pytest.mark.asyncio
    async def test_sims_run_matches_num_sims_arg(self):
        """sims_run in the result equals the num_sims argument."""
        from services.combat_models import ShipLoadout

        svc = _make_service()
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[_criminal_bounty()])
        player_loadout = ShipLoadout(ship_name="Player", base_armour=200)
        svc.combat_service.fight_ships = MagicMock(return_value=_fight_result(winner="Player"))

        with patch(
            "services.combat_preflight_service.LoadoutBuilder.from_player",
            new=AsyncMock(return_value=player_loadout),
        ):
            result = await svc.estimate(MagicMock(), player_id=1, guild_id=1, target_tier="Gold", num_sims=5)

        assert result.sims_run == 5

    @pytest.mark.asyncio
    async def test_sample_size_excludes_bounties_without_criminal_ship(self):
        """sample_size counts only bounties that have criminal_ship data."""
        from services.combat_models import ShipLoadout

        svc = _make_service()
        bounties = [
            _criminal_bounty("Raider"),
            _criminal_bounty("Guardian"),
            SimpleNamespace(id=3, criminal_ship=None),
        ]
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=bounties)
        player_loadout = ShipLoadout(ship_name="Player", base_armour=200)
        svc.combat_service.fight_ships = MagicMock(return_value=_fight_result(winner="Player"))

        with patch(
            "services.combat_preflight_service.LoadoutBuilder.from_player",
            new=AsyncMock(return_value=player_loadout),
        ):
            result = await svc.estimate(MagicMock(), player_id=1, guild_id=1, target_tier="Silver", num_sims=4)

        assert result.sample_size == 2  # 3 bounties, only 2 have criminal_ship

    @pytest.mark.asyncio
    async def test_division_is_lowercased_before_query(self):
        """target_tier is lowercased when passed to bounty_repo."""
        from services.combat_models import ShipLoadout

        svc = _make_service()
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[_criminal_bounty()])
        player_loadout = ShipLoadout(ship_name="Player", base_armour=200)
        svc.combat_service.fight_ships = MagicMock(return_value=_fight_result(winner="Player"))

        with patch(
            "services.combat_preflight_service.LoadoutBuilder.from_player",
            new=AsyncMock(return_value=player_loadout),
        ):
            await svc.estimate(MagicMock(), player_id=1, guild_id=7, target_tier="Gold", num_sims=1)

        svc.bounty_repo.get_active_by_guild_and_division.assert_awaited_once_with(
            svc.bounty_repo.get_active_by_guild_and_division.call_args[0][0],
            7,
            "gold",
        )

    @pytest.mark.asyncio
    async def test_target_tier_preserved_in_result(self):
        """The result's target_tier field matches the input target_tier string."""
        from services.combat_models import ShipLoadout

        svc = _make_service()
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[_criminal_bounty()])
        player_loadout = ShipLoadout(ship_name="Player", base_armour=200)
        svc.combat_service.fight_ships = MagicMock(return_value=_fight_result(winner="Player"))

        with patch(
            "services.combat_preflight_service.LoadoutBuilder.from_player",
            new=AsyncMock(return_value=player_loadout),
        ):
            result = await svc.estimate(MagicMock(), player_id=1, guild_id=1, target_tier="Platinum", num_sims=2)

        assert result.target_tier == "Platinum"
