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


def _make_synthetic_criminal(ship_name: str = "SynthRaider") -> object:
    """Return a SimpleNamespace that mimics a synthesized criminal (no DB id)."""
    return SimpleNamespace(
        criminal_ship={"ship_name": ship_name, "ship_armour": 80, "weapons": [], "turrets": []}
    )


# ===========================================================================
# Tests: NO_DATA verdict — now only reached when synthesis also fails
# ===========================================================================


class TestEstimateNoData:
    """estimate() returns NO_DATA only when synthesis itself fails."""

    @pytest.mark.asyncio
    async def test_synthesis_failure_returns_no_data(self):
        """Returns NO_DATA when no active bounties exist AND synthesis fails."""
        svc = _make_service()
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

        # Both bounty repo empty AND _synthesize_criminals returns empty
        with patch.object(svc, "_synthesize_criminals", new=AsyncMock(return_value=[])):
            result = await svc.estimate(MagicMock(), player_id=1, guild_id=1, target_tier="Silver")

        assert result.verdict == PreflightVerdict.NO_DATA
        assert result.sims_run == 0
        assert result.sample_size == 0

    @pytest.mark.asyncio
    async def test_bounties_without_criminal_ship_synthesis_fails_returns_no_data(self):
        """Returns NO_DATA when bounties exist but none have criminal_ship AND synthesis fails."""
        svc = _make_service()
        empty_bounty = SimpleNamespace(id=1, criminal_ship=None)
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[empty_bounty])

        with patch.object(svc, "_synthesize_criminals", new=AsyncMock(return_value=[])):
            result = await svc.estimate(MagicMock(), player_id=1, guild_id=1, target_tier="Silver")

        assert result.verdict == PreflightVerdict.NO_DATA
        assert result.sims_run == 0

    @pytest.mark.asyncio
    async def test_loadout_builder_failure_returns_no_data(self):
        """Returns NO_DATA when the player's loadout cannot be built.

        This is the 'impossible in production' path — active ship sale is blocked
        at the service layer. Covered defensively.
        """
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
# Tests: No active bounties — synthesis kicks in
# ===========================================================================


class TestEstimateNoBountiesSynthesis:
    """estimate() synthesizes criminals and runs sims when no active bounties exist."""

    @pytest.mark.asyncio
    async def test_no_active_bounties_synthesis_runs_sims(self):
        """No active bounties → synthesis provides criminals → sims run → real verdict returned."""
        from services.combat_models import ShipLoadout

        svc = _make_service()
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])
        player_loadout = ShipLoadout(ship_name="Player", base_armour=200)
        svc.combat_service.fight_ships = MagicMock(return_value=_fight_result(winner="Player"))

        # Synthesis returns 5 synthetic criminals
        synthetics = [_make_synthetic_criminal() for _ in range(5)]
        with (
            patch.object(svc, "_synthesize_criminals", new=AsyncMock(return_value=synthetics)),
            patch(
                "services.combat_preflight_service.LoadoutBuilder.from_player",
                new=AsyncMock(return_value=player_loadout),
            ),
        ):
            result = await svc.estimate(MagicMock(), player_id=1, guild_id=1, target_tier="Silver", num_sims=20)

        assert result.verdict != PreflightVerdict.NO_DATA
        assert result.sims_run == 20
        assert result.sample_size == 5

    @pytest.mark.asyncio
    async def test_no_active_bounties_synthesis_green_verdict(self):
        """No bounties + synthesis + player wins all → GREEN verdict."""
        from services.combat_models import ShipLoadout

        svc = _make_service()
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])
        player_loadout = ShipLoadout(ship_name="Player", base_armour=200)
        svc.combat_service.fight_ships = MagicMock(return_value=_fight_result(winner="Player"))

        synthetics = [_make_synthetic_criminal() for _ in range(5)]
        with (
            patch.object(svc, "_synthesize_criminals", new=AsyncMock(return_value=synthetics)),
            patch(
                "services.combat_preflight_service.LoadoutBuilder.from_player",
                new=AsyncMock(return_value=player_loadout),
            ),
        ):
            result = await svc.estimate(MagicMock(), player_id=1, guild_id=1, target_tier="Silver", num_sims=20)

        assert result.verdict == PreflightVerdict.GREEN
        assert result.player_win_rate >= 0.75

    @pytest.mark.asyncio
    async def test_no_bounties_synthesis_called_with_correct_division(self):
        """_synthesize_criminals is called with the lowercased division name."""
        svc = _make_service()
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

        synth_mock = AsyncMock(return_value=[])
        with patch.object(svc, "_synthesize_criminals", new=synth_mock):
            await svc.estimate(MagicMock(), player_id=1, guild_id=1, target_tier="Gold")

        # Should be called with lowercase "gold"
        call_args = synth_mock.call_args
        assert call_args[0][1] == "gold" or call_args[1].get("division") == "gold"


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


# ===========================================================================
# Tests: _synthesize_criminals helper
# ===========================================================================


class TestSynthesizeCriminals:
    """Unit tests for the _synthesize_criminals private helper."""

    @pytest.mark.asyncio
    async def test_synthesize_returns_objects_with_criminal_ship(self):
        """Each synthesized object has a criminal_ship attribute."""
        svc = _make_service()
        fake_loadout = {"ship_name": "TestShip", "ship_armour": 100, "weapons": [], "turrets": []}

        with patch(
            "services.combat_preflight_service.BountyService.generate_loadout",
            new=AsyncMock(return_value=fake_loadout),
        ):
            result = await svc._synthesize_criminals(MagicMock(), "silver", count=3)

        assert len(result) == 3
        for obj in result:
            assert hasattr(obj, "criminal_ship")
            assert obj.criminal_ship == fake_loadout

    @pytest.mark.asyncio
    async def test_synthesize_uses_division_max_tl(self):
        """Synthesis calls generate_loadout with TL within the division's range."""
        svc = _make_service()
        recorded_tls: list[int] = []

        async def _mock_generate(db, tl, **kwargs):
            recorded_tls.append(tl)
            return {"ship_name": "Ship", "ship_armour": 100, "weapons": [], "turrets": []}

        with patch(
            "services.combat_preflight_service.BountyService.generate_loadout",
            new=_mock_generate,
        ):
            await svc._synthesize_criminals(MagicMock(), "bronze", count=5)

        # Bronze max TL is 2; min TL is 1
        for tl in recorded_tls:
            assert 1 <= tl <= 2

    @pytest.mark.asyncio
    async def test_synthesize_partial_failure_returns_what_succeeded(self):
        """If some generate_loadout calls fail, successfully built ones are still returned."""
        svc = _make_service()
        good_loadout = {"ship_name": "Ship", "ship_armour": 100, "weapons": [], "turrets": []}

        # Use AsyncMock with side_effect list: alternate success and failure
        side_effects = [
            good_loadout,
            RuntimeError("flaky DB"),
            good_loadout,
            RuntimeError("flaky DB"),
        ]
        mock_generate = AsyncMock(side_effect=side_effects)

        with patch(
            "services.combat_preflight_service.BountyService.generate_loadout",
            new=mock_generate,
        ):
            result = await svc._synthesize_criminals(MagicMock(), "silver", count=4)

        # 2 out of 4 should succeed (calls 1 and 3 succeed, 2 and 4 fail)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_synthesize_all_failures_returns_empty(self):
        """If all generate_loadout calls fail, an empty list is returned (no raise)."""
        svc = _make_service()

        with patch(
            "services.combat_preflight_service.BountyService.generate_loadout",
            new=AsyncMock(side_effect=RuntimeError("DB down")),
        ):
            result = await svc._synthesize_criminals(MagicMock(), "gold", count=5)

        assert result == []

    @pytest.mark.asyncio
    async def test_synthesize_tl_never_exceeds_division_max_tl_all_tiers(self):
        """Adversarial: TL never exceeds DIVISION_MAX_TL for any tier (100 iterations each).

        Uses a large iteration count to make it statistically certain that a
        max_tl+1 bug would produce at least one out-of-range call.
        Avoids the B023 loop-closure issue by using a mutable container declared
        outside the inner function.
        """
        from services.game_constants import GameConstants

        svc = _make_service()

        for division, max_tl in GameConstants.DIVISION_MAX_TL.items():
            _box: dict[str, list[int]] = {"tls": []}

            async def _capture(db, tl, _b=_box, **kwargs):
                _b["tls"].append(tl)
                return {"ship_name": "Ship", "ship_armour": 100, "weapons": [], "turrets": []}

            with patch(
                "services.combat_preflight_service.BountyService.generate_loadout",
                new=_capture,
            ):
                await svc._synthesize_criminals(MagicMock(), division, count=100)

            for tl in _box["tls"]:
                assert GameConstants.MIN_TECH_LEVEL <= tl <= max_tl, (
                    f"division={division}: TL {tl} outside [{GameConstants.MIN_TECH_LEVEL}, {max_tl}]"
                )

    @pytest.mark.asyncio
    async def test_estimate_passes_lowercased_division_to_synthesize(self):
        """Adversarial: estimate() passes already-lowercased division to _synthesize_criminals.

        Ensures DIVISION_MAX_TL.get(division) cannot miss due to capitalisation.
        """
        svc = _make_service()
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])

        received_divisions: list[str] = []

        async def _capture_division(db, division, count=5):
            received_divisions.append(division)
            return []  # synthesis empty → NO_DATA (no player loadout needed)

        with patch.object(svc, "_synthesize_criminals", new=_capture_division):
            await svc.estimate(MagicMock(), player_id=1, guild_id=1, target_tier="Silver")

        assert received_divisions == ["silver"], (
            f"Expected lowercase 'silver', got {received_divisions}"
        )
