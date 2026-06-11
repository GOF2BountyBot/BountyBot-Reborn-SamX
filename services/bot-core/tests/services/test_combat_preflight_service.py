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
    return svc


def _criminal_bounty(ship_name: str = "Raider") -> object:
    return SimpleNamespace(
        id=1,
        criminal_ship={"ship_name": ship_name, "ship_armour": 80, "weapons": [], "turrets": []},
    )


def _make_synthetic_criminal(ship_name: str = "SynthRaider") -> object:
    """Return a SimpleNamespace that mimics a synthesized criminal (no DB id)."""
    return SimpleNamespace(criminal_ship={"ship_name": ship_name, "ship_armour": 80, "weapons": [], "turrets": []})


def _all_player_wins(num_sims: int) -> list[tuple]:
    """Return batch results where player (side 1) wins every fight."""
    return [(1, False)] * num_sims


def _all_criminal_wins(num_sims: int) -> list[tuple]:
    """Return batch results where criminal (side 2) wins every fight."""
    return [(2, False)] * num_sims


def _alternating_wins(num_sims: int) -> list[tuple]:
    """Return alternating player/criminal wins (50-50 split)."""
    return [(1, False) if i % 2 == 0 else (2, False) for i in range(num_sims)]


def _all_stalemates(num_sims: int) -> list[tuple]:
    """Return batch results where every fight is a stalemate."""
    return [(None, True)] * num_sims


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

        # Synthesis returns 5 synthetic criminals
        synthetics = [_make_synthetic_criminal() for _ in range(5)]
        with (
            patch.object(svc, "_synthesize_criminals", new=AsyncMock(return_value=synthetics)),
            patch(
                "services.combat_preflight_service.LoadoutBuilder.from_player",
                new=AsyncMock(return_value=player_loadout),
            ),
            patch(
                "services.combat_preflight_service.offload_cpu",
                new=AsyncMock(return_value=_all_player_wins(20)),
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

        synthetics = [_make_synthetic_criminal() for _ in range(5)]
        with (
            patch.object(svc, "_synthesize_criminals", new=AsyncMock(return_value=synthetics)),
            patch(
                "services.combat_preflight_service.LoadoutBuilder.from_player",
                new=AsyncMock(return_value=player_loadout),
            ),
            patch(
                "services.combat_preflight_service.offload_cpu",
                new=AsyncMock(return_value=_all_player_wins(20)),
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

        with (
            patch(
                "services.combat_preflight_service.LoadoutBuilder.from_player",
                new=AsyncMock(return_value=player_loadout),
            ),
            patch(
                "services.combat_preflight_service.offload_cpu",
                new=AsyncMock(return_value=_all_player_wins(20)),
            ),
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

        with (
            patch(
                "services.combat_preflight_service.LoadoutBuilder.from_player",
                new=AsyncMock(return_value=player_loadout),
            ),
            patch(
                "services.combat_preflight_service.offload_cpu",
                new=AsyncMock(return_value=_all_criminal_wins(20)),
            ),
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

        with (
            patch(
                "services.combat_preflight_service.LoadoutBuilder.from_player",
                new=AsyncMock(return_value=player_loadout),
            ),
            patch(
                "services.combat_preflight_service.offload_cpu",
                new=AsyncMock(return_value=_alternating_wins(20)),
            ),
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

        with (
            patch(
                "services.combat_preflight_service.LoadoutBuilder.from_player",
                new=AsyncMock(return_value=player_loadout),
            ),
            patch(
                "services.combat_preflight_service.offload_cpu",
                new=AsyncMock(return_value=_all_player_wins(5)),
            ),
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

        with (
            patch(
                "services.combat_preflight_service.LoadoutBuilder.from_player",
                new=AsyncMock(return_value=player_loadout),
            ),
            patch(
                "services.combat_preflight_service.offload_cpu",
                new=AsyncMock(return_value=_all_player_wins(4)),
            ),
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

        with (
            patch(
                "services.combat_preflight_service.LoadoutBuilder.from_player",
                new=AsyncMock(return_value=player_loadout),
            ),
            patch(
                "services.combat_preflight_service.offload_cpu",
                new=AsyncMock(return_value=_all_player_wins(1)),
            ),
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

        with (
            patch(
                "services.combat_preflight_service.LoadoutBuilder.from_player",
                new=AsyncMock(return_value=player_loadout),
            ),
            patch(
                "services.combat_preflight_service.offload_cpu",
                new=AsyncMock(return_value=_all_player_wins(2)),
            ),
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

        assert received_divisions == ["silver"], f"Expected lowercase 'silver', got {received_divisions}"


# ===========================================================================
# P2-T7 Tests: Batched preflight — single dispatch, side-keyed win predicate
# ===========================================================================


class TestP2T7OneDispatch:
    """estimate() uses exactly ONE offload_cpu call regardless of num_sims."""

    @pytest.mark.asyncio
    async def test_single_offload_dispatch(self):
        """Assert offload_cpu is called exactly once (not num_sims times)."""
        from services.combat_models import ShipLoadout

        svc = _make_service()
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[_criminal_bounty()])
        player_loadout = ShipLoadout(ship_name="Player", base_armour=200)

        offload_mock = AsyncMock(return_value=_all_player_wins(10))
        with (
            patch(
                "services.combat_preflight_service.LoadoutBuilder.from_player",
                new=AsyncMock(return_value=player_loadout),
            ),
            patch("services.combat_preflight_service.offload_cpu", new=offload_mock),
        ):
            await svc.estimate(MagicMock(), player_id=1, guild_id=1, target_tier="Silver", num_sims=10)

        assert offload_mock.call_count == 1, f"Expected exactly 1 offload_cpu dispatch, got {offload_mock.call_count}"

    @pytest.mark.asyncio
    async def test_offload_called_with_run_fight_batch(self):
        """offload_cpu is called with run_fight_batch as the function argument."""
        from compute.combat_worker import run_fight_batch
        from services.combat_models import ShipLoadout

        svc = _make_service()
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[_criminal_bounty()])
        player_loadout = ShipLoadout(ship_name="Player", base_armour=200)

        offload_mock = AsyncMock(return_value=_all_player_wins(4))
        with (
            patch(
                "services.combat_preflight_service.LoadoutBuilder.from_player",
                new=AsyncMock(return_value=player_loadout),
            ),
            patch("services.combat_preflight_service.offload_cpu", new=offload_mock),
        ):
            await svc.estimate(MagicMock(), player_id=1, guild_id=1, target_tier="Silver", num_sims=4)

        # First positional arg of the single call must be run_fight_batch
        called_fn = offload_mock.call_args[0][0]
        assert called_fn is run_fight_batch

    @pytest.mark.asyncio
    async def test_matchup_list_length_equals_num_sims(self):
        """The matchups list passed to run_fight_batch has exactly num_sims entries."""
        from services.combat_models import ShipLoadout

        svc = _make_service()
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[_criminal_bounty()])
        player_loadout = ShipLoadout(ship_name="Player", base_armour=200)

        captured_matchups: list = []

        async def _capture_offload(fn, matchups, **kwargs):
            captured_matchups.extend(matchups)
            return _all_player_wins(len(matchups))

        with (
            patch(
                "services.combat_preflight_service.LoadoutBuilder.from_player",
                new=AsyncMock(return_value=player_loadout),
            ),
            patch("services.combat_preflight_service.offload_cpu", new=_capture_offload),
        ):
            await svc.estimate(MagicMock(), player_id=1, guild_id=1, target_tier="Silver", num_sims=7)

        assert len(captured_matchups) == 7


class TestP2T7WinPredicate:
    """Win predicate: only a decisive winner_side==1 counts as a player win.

    Stalemates mirror the real PvC outcome (spec §9: criminal escapes — same
    path as a loss), so they count toward the criminal side.
    """

    @pytest.mark.asyncio
    async def test_stalemate_counts_as_criminal_win(self):
        """Stalemates count toward the criminal side (criminal escapes in a real fight)."""
        from services.combat_models import ShipLoadout

        svc = _make_service()
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[_criminal_bounty()])
        player_loadout = ShipLoadout(ship_name="Player", base_armour=200)

        # All stalemates → all criminal wins → RED
        with (
            patch(
                "services.combat_preflight_service.LoadoutBuilder.from_player",
                new=AsyncMock(return_value=player_loadout),
            ),
            patch(
                "services.combat_preflight_service.offload_cpu",
                new=AsyncMock(return_value=_all_stalemates(20)),
            ),
        ):
            result = await svc.estimate(MagicMock(), player_id=1, guild_id=1, target_tier="Silver", num_sims=20)

        assert result.player_win_rate == 0.0
        assert result.criminal_win_rate == 1.0
        assert result.verdict == PreflightVerdict.RED

    @pytest.mark.asyncio
    async def test_winner_side_1_counts_as_player_win(self):
        """winner_side==1 (no stalemate) counts as player win."""
        from services.combat_models import ShipLoadout

        svc = _make_service()
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[_criminal_bounty()])
        player_loadout = ShipLoadout(ship_name="Player", base_armour=200)

        with (
            patch(
                "services.combat_preflight_service.LoadoutBuilder.from_player",
                new=AsyncMock(return_value=player_loadout),
            ),
            patch(
                "services.combat_preflight_service.offload_cpu",
                new=AsyncMock(return_value=[(1, False)] * 20),
            ),
        ):
            result = await svc.estimate(MagicMock(), player_id=1, guild_id=1, target_tier="Silver", num_sims=20)

        assert result.player_win_rate == 1.0
        assert result.verdict == PreflightVerdict.GREEN

    @pytest.mark.asyncio
    async def test_winner_side_2_counts_as_criminal_win(self):
        """winner_side==2 (no stalemate) counts as criminal win."""
        from services.combat_models import ShipLoadout

        svc = _make_service()
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[_criminal_bounty()])
        player_loadout = ShipLoadout(ship_name="Player", base_armour=200)

        with (
            patch(
                "services.combat_preflight_service.LoadoutBuilder.from_player",
                new=AsyncMock(return_value=player_loadout),
            ),
            patch(
                "services.combat_preflight_service.offload_cpu",
                new=AsyncMock(return_value=[(2, False)] * 20),
            ),
        ):
            result = await svc.estimate(MagicMock(), player_id=1, guild_id=1, target_tier="Silver", num_sims=20)

        assert result.criminal_win_rate == 1.0
        assert result.verdict == PreflightVerdict.RED

    @pytest.mark.asyncio
    async def test_win_by_side_not_name_same_name_matchup(self):
        """When player and criminal share the same ship name, wins decide by side (not name).

        This proves the new side-keyed predicate is correct even when names collide,
        eliminating the old winner_name == player_loadout.ship_name ambiguity.
        """
        from services.combat_models import ShipLoadout

        svc = _make_service()
        # Criminal has the SAME ship name as the player
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(
            return_value=[_criminal_bounty(ship_name="Player")]
        )
        player_loadout = ShipLoadout(ship_name="Player", base_armour=200)

        # side 2 wins (criminal) — with the OLD name-keyed check this would have been
        # mis-attributed as a player win since winner_name == "Player"
        with (
            patch(
                "services.combat_preflight_service.LoadoutBuilder.from_player",
                new=AsyncMock(return_value=player_loadout),
            ),
            patch(
                "services.combat_preflight_service.offload_cpu",
                new=AsyncMock(return_value=[(2, False)] * 20),
            ),
        ):
            result = await svc.estimate(MagicMock(), player_id=1, guild_id=1, target_tier="Silver", num_sims=20)

        # Side-keyed: side 2 = criminal win, so RED
        assert result.criminal_win_rate == 1.0
        assert result.verdict == PreflightVerdict.RED


class TestP2T7CriminalDrawDistribution:
    """Criminal draw distribution is unchanged: random.choice(criminals) with replacement, num_sims draws."""

    @pytest.mark.asyncio
    async def test_draw_order_matches_fixed_rng_sequence(self):
        """With a fixed RNG seed, the criminal draw sequence is identical to the old loop."""
        import random as _random

        from services.combat_models import ShipLoadout

        svc = _make_service()
        criminal_a = _criminal_bounty("Alpha")
        criminal_b = _criminal_bounty("Beta")
        criminal_c = _criminal_bounty("Gamma")
        criminals = [criminal_a, criminal_b, criminal_c]
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=criminals)
        player_loadout = ShipLoadout(ship_name="Player", base_armour=200)

        # Simulate what the OLD code's draw loop would produce with a fixed seed
        _random.seed(12345)
        expected_names: list[str] = []
        for _ in range(8):
            b = _random.choice(criminals)
            expected_names.append((b.criminal_ship or {}).get("ship_name", ""))

        # Now run estimate() with the same seed, capture actual matchup criminal names
        actual_names: list[str] = []

        async def _capture_offload(fn, matchups, **kwargs):
            for _p, _c, _seed, _l1, _l2 in matchups:
                actual_names.append(_c.ship_name)
            return _all_player_wins(len(matchups))

        _random.seed(12345)
        with (
            patch(
                "services.combat_preflight_service.LoadoutBuilder.from_player",
                new=AsyncMock(return_value=player_loadout),
            ),
            patch("services.combat_preflight_service.offload_cpu", new=_capture_offload),
        ):
            await svc.estimate(MagicMock(), player_id=1, guild_id=1, target_tier="Silver", num_sims=8)

        assert actual_names == expected_names, (
            f"Criminal draw sequence mismatch.\nExpected: {expected_names}\nActual:   {actual_names}"
        )

    @pytest.mark.asyncio
    async def test_all_criminals_drawn_with_replacement(self):
        """Criminal pool has 2 entries; with enough sims both should appear (replacement)."""
        from services.combat_models import ShipLoadout

        svc = _make_service()
        criminals = [_criminal_bounty("Alpha"), _criminal_bounty("Beta")]
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=criminals)
        player_loadout = ShipLoadout(ship_name="Player", base_armour=200)

        seen_names: set[str] = set()

        async def _capture_offload(fn, matchups, **kwargs):
            for _p, _c, _seed, _l1, _l2 in matchups:
                seen_names.add(_c.ship_name)
            return _all_player_wins(len(matchups))

        # 50 sims — near-certain to hit both criminals under any RNG
        with (
            patch(
                "services.combat_preflight_service.LoadoutBuilder.from_player",
                new=AsyncMock(return_value=player_loadout),
            ),
            patch("services.combat_preflight_service.offload_cpu", new=_capture_offload),
        ):
            await svc.estimate(MagicMock(), player_id=1, guild_id=1, target_tier="Silver", num_sims=50)

        assert "Alpha" in seen_names
        assert "Beta" in seen_names


class TestP2T7WinRateParity:
    """Win-rate parity: the new side-keyed predicate matches the old name-keyed one for normal cases.

    The old check was: is_stalemate or winner_name == player_loadout.ship_name
    The new check is:  is_stalemate or winner_side == 1

    For non-colliding names these are equivalent because run_fight sets winner_side
    to 1 when loadout1 wins and winner_name to loadout1.ship_name for the same fight.
    """

    def test_run_fight_batch_stalemate_semantics(self):
        """run_fight_batch stalemate result is (None, True) — matches compact=True run_fight."""
        import pathlib
        import sys

        _src = pathlib.Path(__file__).resolve().parent.parent.parent / "src"
        if str(_src) not in sys.path:
            sys.path.insert(0, str(_src))

        from compute.combat_worker import run_fight_batch
        from services.combat_models import ShipLoadout

        # Bare loadouts → stalemate (no weapons, fight runs to time cap)
        bare1 = ShipLoadout(ship_name="S1", base_armour=100)
        bare2 = ShipLoadout(ship_name="S2", base_armour=100)

        matchups = [(bare1, bare2, 42, "", "")]
        results = run_fight_batch(matchups, pvc_damage_reduction=0.0, compact=True)

        assert len(results) == 1
        winner_side, is_stalemate = results[0]
        assert is_stalemate is True
        # Stalemate: winner_side is None but is_stalemate flag is True
        # new predicate: is_stalemate or winner_side==1 → True (player win)
        assert (is_stalemate or winner_side == 1) is True

    def test_run_fight_batch_player_win_side1(self):
        """Asymmetric loadout (player vastly stronger): winner_side==1, not stalemate."""
        import pathlib
        import sys

        _src = pathlib.Path(__file__).resolve().parent.parent.parent / "src"
        if str(_src) not in sys.path:
            sys.path.insert(0, str(_src))

        from compute.combat_worker import run_fight_batch
        from services.combat_models import ShipLoadout, WeaponStats

        gun = WeaponStats(name="Cannon", dps=500.0, damage_per_shot=50.0, loading_speed_ms=100, range_m=5000.0)
        strong = ShipLoadout(ship_name="StrongPlayer", base_armour=2000, weapons=[gun])
        weak = ShipLoadout(ship_name="WeakCriminal", base_armour=1)

        matchups = [(strong, weak, 42, "", "")]
        results = run_fight_batch(matchups, pvc_damage_reduction=0.0, compact=True)

        winner_side, is_stalemate = results[0]
        assert is_stalemate is False
        assert winner_side == 1  # player (side 1) wins
        # New predicate: True
        assert (is_stalemate or winner_side == 1) is True

    def test_run_fight_batch_criminal_win_side2(self):
        """Asymmetric loadout (criminal vastly stronger): winner_side==2, not stalemate."""
        import pathlib
        import sys

        _src = pathlib.Path(__file__).resolve().parent.parent.parent / "src"
        if str(_src) not in sys.path:
            sys.path.insert(0, str(_src))

        from compute.combat_worker import run_fight_batch
        from services.combat_models import ShipLoadout, WeaponStats

        gun = WeaponStats(name="Cannon", dps=500.0, damage_per_shot=50.0, loading_speed_ms=100, range_m=5000.0)
        weak = ShipLoadout(ship_name="WeakPlayer", base_armour=1)
        strong = ShipLoadout(ship_name="StrongCriminal", base_armour=2000, weapons=[gun])

        matchups = [(weak, strong, 42, "", "")]
        results = run_fight_batch(matchups, pvc_damage_reduction=0.0, compact=True)

        winner_side, is_stalemate = results[0]
        assert is_stalemate is False
        assert winner_side == 2  # criminal (side 2) wins
        # New predicate: False (criminal win)
        assert (is_stalemate or winner_side == 1) is False

    def test_run_fight_batch_multiple_matchups_returns_one_per_matchup(self):
        """run_fight_batch returns exactly len(matchups) results."""
        import pathlib
        import sys

        _src = pathlib.Path(__file__).resolve().parent.parent.parent / "src"
        if str(_src) not in sys.path:
            sys.path.insert(0, str(_src))

        from compute.combat_worker import run_fight_batch
        from services.combat_models import ShipLoadout, WeaponStats

        gun = WeaponStats(name="Cannon", dps=500.0, damage_per_shot=50.0, loading_speed_ms=100, range_m=5000.0)
        l1 = ShipLoadout(ship_name="P1", base_armour=300, weapons=[gun])
        l2 = ShipLoadout(ship_name="C1", base_armour=200, weapons=[gun])

        matchups = [(l1, l2, seed, "", "") for seed in range(5)]
        results = run_fight_batch(matchups, pvc_damage_reduction=0.0, compact=True)

        assert len(results) == 5
        for winner_side, is_stalemate in results:
            assert isinstance(is_stalemate, bool)
            assert winner_side in (1, 2, None)


class TestP2T7Picklability:
    """run_fight_batch inputs are picklable and the function runs in a real forkserver pool."""

    def test_run_fight_batch_in_forkserver_child(self):
        """run_fight_batch executes correctly in a real forkserver ProcessPoolExecutor."""
        import multiprocessing
        import os
        import pathlib
        import pickle
        import sys
        from concurrent.futures import ProcessPoolExecutor

        _src = pathlib.Path(__file__).resolve().parent.parent.parent / "src"
        if str(_src) not in sys.path:
            sys.path.insert(0, str(_src))

        from services.combat_models import ShipLoadout, WeaponStats

        gun = WeaponStats(name="Gun", dps=400.0, damage_per_shot=40.0, loading_speed_ms=100, range_m=5000.0)
        l1 = ShipLoadout(ship_name="Attacker", base_armour=300, weapons=[gun])
        l2 = ShipLoadout(ship_name="Defender", base_armour=300, weapons=[gun])

        matchups = [(l1, l2, 99, "", ""), (l2, l1, 99, "", "")]
        matchups_pkl = pickle.dumps(matchups)

        ctx = multiprocessing.get_context("forkserver")
        with ProcessPoolExecutor(max_workers=1, mp_context=ctx) as pool:
            fut = pool.submit(_run_batch_in_child, str(_src), matchups_pkl)
            child_out = fut.result(timeout=60)

        assert child_out["success"], f"run_fight_batch in child failed: {child_out.get('error')}"
        assert child_out["num_results"] == 2
        assert child_out["child_pid"] != os.getpid()
        # Each result is (winner_side, is_stalemate)
        for ws, sm in child_out["results"]:
            assert isinstance(sm, bool)
            assert ws in (1, 2, None)


def _run_batch_in_child(src_dir: str, matchups_pkl: bytes) -> dict:
    """Run run_fight_batch in a forkserver child process. Must be module-level for picklability."""
    import sys

    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    import pickle as _pickle

    result: dict = {"success": False, "error": None, "num_results": 0, "results": [], "child_pid": 0}
    try:
        matchups = _pickle.loads(matchups_pkl)
        from compute.combat_worker import run_fight_batch as _run_batch

        batch_results = _run_batch(matchups, pvc_damage_reduction=0.0, compact=True)
        result["success"] = True
        result["num_results"] = len(batch_results)
        result["results"] = batch_results
        result["child_pid"] = __import__("os").getpid()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        result["error"] = str(exc)
    return result
