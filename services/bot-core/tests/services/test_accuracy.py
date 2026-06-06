"""
T4 Accuracy system tests.

Covers all 11 categories from TASK_0004.md §Test surface:
  1.  Layered formula (cloak inactive) — 4 representative combinations
  2.  Cloak override — all-max-positive and all-max-negative inputs
  3.  Cloak override with extreme cloak_set_value (below/above clamp bounds)
  4.  Clamp saturation (upper and lower bounds)
  5.  pilot_primary_acc vs pilot_turret_acc divergence (thruster term)
  6.  thruster_ramp() pure function — 5 boundary cases
  7.  booster_debuff_pp() pure function — 3 representative cases
  8.  resolve_scanner_tier() — 9 loadout cases
  9.  Resolver integration — scanner_tier precomputed; accuracy reflects tier
  10. T3 drift-to-floor regression (unchanged behaviour after T4 edits)
  11. RNG injection seam — rng= kwarg accepted without error
"""

from __future__ import annotations

import random
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared bblogger guard — must run before any project import
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

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------
from services.combat_balance import (
    ScannerTier,
    booster_debuff_pp,
    compute_pilot_accuracy,
    resolve_scanner_tier,
    thruster_ramp,
)
from services.combat_models import ModuleStats, ShipLoadout
from services.combat_resolver import TickResolver, _init_combatant
from services.game_constants import GameConstants

# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

_CLAMP_MIN = GameConstants.ACCURACY_CLAMP_MIN  # 0.05
_CLAMP_MAX = GameConstants.ACCURACY_CLAMP_MAX  # 0.99
_CLOAK = GameConstants.CLOAK_SET_VALUE  # 0.25
_PLAYER = GameConstants.PLAYER_BASE_ACCURACY  # 0.60
_NPC = GameConstants.NPC_BASE_ACCURACY  # 0.50


def _acc(
    base: float,
    scanner_pp: float = 0.0,
    thruster_pp: float = 0.0,
    booster_pp: float = 0.0,
    cloak_active: bool = False,
    cloak_set: float = _CLOAK,
) -> tuple[float, float]:
    """Thin wrapper around compute_pilot_accuracy using default clamp/cloak values."""
    return compute_pilot_accuracy(
        combatant_base=base,
        own_scanner_bonus_pp=scanner_pp,
        own_thruster_bonus_pp=thruster_pp,
        opponent_booster_debuff_pp=booster_pp,
        opponent_cloak_active=cloak_active,
        cloak_set_value=cloak_set,
        clamp_min=_CLAMP_MIN,
        clamp_max=_CLAMP_MAX,
    )


# ---------------------------------------------------------------------------
# 1. Layered formula (cloak inactive) — representative cross-product
# ---------------------------------------------------------------------------


class TestLayeredFormula:
    def test_player_no_scanner_no_thruster_no_booster(self):
        """Player base 0.60, no modifiers → (0.60, 0.60)."""
        primary, turret = _acc(_PLAYER)
        assert pytest.approx(0.60) == primary
        assert pytest.approx(0.60) == turret

    def test_npc_scanner_b(self):
        """NPC base 0.50 + scanner B (+5pp) → (0.55, 0.55) — no thruster so equal."""
        primary, turret = _acc(_NPC, scanner_pp=5.0)
        assert pytest.approx(0.55) == primary
        assert pytest.approx(0.55) == turret

    def test_player_scanner_c_with_thruster(self):
        """Player 0.60 + scanner C (+10pp) + thruster (+10pp) → primary=0.80, turret=0.70."""
        primary, turret = _acc(_PLAYER, scanner_pp=10.0, thruster_pp=10.0)
        assert pytest.approx(0.80) == primary
        assert pytest.approx(0.70) == turret

    def test_npc_with_booster_debuff(self):
        """NPC 0.50 − booster debuff (10pp) → (0.40, 0.40)."""
        primary, turret = _acc(_NPC, booster_pp=10.0)
        assert pytest.approx(0.40) == primary
        assert pytest.approx(0.40) == turret


# ---------------------------------------------------------------------------
# 2. Cloak override — supersedes all stack inputs
# ---------------------------------------------------------------------------


class TestCloakOverride:
    def test_cloak_beats_all_positive_contributions(self):
        """With max positive stack (scanner +10, thruster +50), cloak still forces 0.25."""
        primary, turret = _acc(_PLAYER, scanner_pp=10.0, thruster_pp=50.0, cloak_active=True)
        assert pytest.approx(_CLOAK) == primary
        assert pytest.approx(_CLOAK) == turret

    def test_cloak_beats_all_negative_contributions(self):
        """With heavy booster penalty (−1000pp), cloak still forces 0.25 (not clamp floor)."""
        primary, turret = _acc(_NPC, booster_pp=1000.0, cloak_active=True)
        assert pytest.approx(_CLOAK) == primary
        assert pytest.approx(_CLOAK) == turret

    def test_cloak_both_variants_equal(self):
        """Cloak override applies identically to both primary and turret variants."""
        primary, turret = _acc(_PLAYER, thruster_pp=20.0, cloak_active=True)
        assert primary == turret


# ---------------------------------------------------------------------------
# 3. Cloak override with extreme cloak_set_value
# ---------------------------------------------------------------------------


class TestCloakOverrideExtremeValues:
    def test_cloak_set_value_below_clamp_min(self):
        """cloak_set_value=0.01 < clamp_min=0.05 → output clamped to 0.05."""
        primary, turret = compute_pilot_accuracy(
            combatant_base=_PLAYER,
            own_scanner_bonus_pp=0.0,
            own_thruster_bonus_pp=0.0,
            opponent_booster_debuff_pp=0.0,
            opponent_cloak_active=True,
            cloak_set_value=0.01,
            clamp_min=_CLAMP_MIN,
            clamp_max=_CLAMP_MAX,
        )
        assert pytest.approx(0.05) == primary
        assert pytest.approx(0.05) == turret

    def test_cloak_set_value_above_clamp_max(self):
        """cloak_set_value=1.50 > clamp_max=0.99 → output clamped to 0.99."""
        primary, turret = compute_pilot_accuracy(
            combatant_base=_PLAYER,
            own_scanner_bonus_pp=0.0,
            own_thruster_bonus_pp=0.0,
            opponent_booster_debuff_pp=0.0,
            opponent_cloak_active=True,
            cloak_set_value=1.50,
            clamp_min=_CLAMP_MIN,
            clamp_max=_CLAMP_MAX,
        )
        assert pytest.approx(0.99) == primary
        assert pytest.approx(0.99) == turret


# ---------------------------------------------------------------------------
# 4. Clamp saturation
# ---------------------------------------------------------------------------


class TestClampSaturation:
    def test_upper_clamp_all_bonuses_stacked(self):
        """NPC base + 100pp scanner + 100pp thruster → would be 3.00, clamped to 0.99."""
        primary, turret = _acc(_NPC, scanner_pp=100.0, thruster_pp=100.0)
        assert pytest.approx(_CLAMP_MAX) == primary
        assert pytest.approx(_CLAMP_MAX) == turret  # turret_pp = (50+100+100) - 100 = 150pp -> 1.50 -> clamped

    def test_lower_clamp_extreme_booster(self):
        """Player base − 1000pp booster → would be −9.40, clamped to 0.05."""
        primary, turret = _acc(_PLAYER, booster_pp=1000.0)
        assert pytest.approx(_CLAMP_MIN) == primary
        assert pytest.approx(_CLAMP_MIN) == turret


# ---------------------------------------------------------------------------
# 5. pilot_primary_acc vs pilot_turret_acc divergence
# ---------------------------------------------------------------------------


class TestPrimaryVsTurretDivergence:
    def test_thruster_only_causes_divergence(self):
        """Only thruster bonus active → primary > turret by thruster_pp / 100."""
        thruster_pp = 13.0  # Pulsed Plasma at default k_thruster
        primary, turret = _acc(_PLAYER, thruster_pp=thruster_pp)
        assert primary > turret
        assert pytest.approx(thruster_pp / 100) == primary - turret

    def test_cloak_active_primary_equals_turret(self):
        """When cloak is active, both variants are equal (cloak override applies to both)."""
        primary, turret = _acc(_PLAYER, thruster_pp=20.0, cloak_active=True)
        assert primary == turret

    def test_no_thruster_primary_equals_turret(self):
        """Without thruster bonus, primary and turret are identical."""
        primary, turret = _acc(_PLAYER, scanner_pp=5.0, booster_pp=3.0)
        assert pytest.approx(primary) == turret


# ---------------------------------------------------------------------------
# 6. thruster_ramp() pure function
# ---------------------------------------------------------------------------


class TestThrusterRamp:
    _WIN = float(GameConstants.THRUSTER_WINDOW_M)  # 750
    _MIN = float(GameConstants.MIN_DISTANCE_M)  # 300

    def test_just_outside_window_returns_zero(self):
        """751 m → just outside THRUSTER_WINDOW_M=750 → ramp = 0.0."""
        assert thruster_ramp(751.0, thruster_window_m=self._WIN, min_distance_m=self._MIN) == 0.0

    def test_at_window_boundary_returns_zero(self):
        """750 m = THRUSTER_WINDOW_M → ramp = 0.0 (closed interval, >= gate)."""
        assert thruster_ramp(750.0, thruster_window_m=self._WIN, min_distance_m=self._MIN) == 0.0

    def test_at_floor_returns_one(self):
        """300 m = MIN_DISTANCE_M → ramp = 1.0."""
        assert thruster_ramp(300.0, thruster_window_m=self._WIN, min_distance_m=self._MIN) == 1.0

    def test_below_floor_clamped_to_one(self):
        """299 m < MIN_DISTANCE_M → ramp clamped to 1.0."""
        assert thruster_ramp(299.0, thruster_window_m=self._WIN, min_distance_m=self._MIN) == 1.0

    def test_midpoint_is_exactly_half(self):
        """525 m = (750+300)/2 → ramp = (750−525)/(750−300) = 225/450 = 0.5."""
        result = thruster_ramp(525.0, thruster_window_m=self._WIN, min_distance_m=self._MIN)
        assert pytest.approx(0.5) == result


# ---------------------------------------------------------------------------
# 7. booster_debuff_pp() pure function
# ---------------------------------------------------------------------------


class TestBoosterDebuffPp:
    def test_polytron_default_k_boost(self):
        """Polytron effect_pct=300, k_boost=0.10 → 30.0 pp."""
        assert pytest.approx(30.0) == booster_debuff_pp(300.0, k_boost=0.10)

    def test_linear_default_k_boost(self):
        """Linear effect_pct=60, k_boost=0.10 → 6.0 pp."""
        assert pytest.approx(6.0) == booster_debuff_pp(60.0, k_boost=0.10)

    def test_zero_effect_pct(self):
        """effect_pct=0 → 0.0 pp regardless of k_boost."""
        assert pytest.approx(0.0) == booster_debuff_pp(0.0, k_boost=0.10)

    def test_returns_positive_magnitude(self):
        """Function always returns a positive magnitude (caller subtracts)."""
        result = booster_debuff_pp(150.0, k_boost=0.10)
        assert result > 0.0


# ---------------------------------------------------------------------------
# 8. resolve_scanner_tier()
# ---------------------------------------------------------------------------


def _loadout_with(*module_names: str) -> ShipLoadout:
    mods = [ModuleStats(name=n) for n in module_names]
    return ShipLoadout(ship_name="TestShip", base_armour=100, modules=mods)


class TestResolveScannerTier:
    _B = float(GameConstants.SCANNER_TIER_B_BONUS_PP)  # 5
    _C = float(GameConstants.SCANNER_TIER_C_BONUS_PP)  # 10

    def test_empty_loadout_is_tier_a(self):
        t = resolve_scanner_tier(
            ShipLoadout(ship_name="S", base_armour=100), tier_b_bonus_pp=self._B, tier_c_bonus_pp=self._C
        )
        assert t.tier == "A"
        assert t.accuracy_bonus_pp == 0.0
        assert t.missile_tracking_active is False

    def test_telta_quickscan_is_tier_b(self):
        t = resolve_scanner_tier(_loadout_with("Telta Quickscan"), tier_b_bonus_pp=self._B, tier_c_bonus_pp=self._C)
        assert t.tier == "B"
        assert t.accuracy_bonus_pp == self._B
        assert t.missile_tracking_active is True

    def test_telta_ecoscan_is_tier_b(self):
        t = resolve_scanner_tier(_loadout_with("Telta Ecoscan"), tier_b_bonus_pp=self._B, tier_c_bonus_pp=self._C)
        assert t.tier == "B"
        assert t.accuracy_bonus_pp == self._B

    def test_hiroto_proscan_is_tier_c(self):
        t = resolve_scanner_tier(_loadout_with("Hiroto Proscan"), tier_b_bonus_pp=self._B, tier_c_bonus_pp=self._C)
        assert t.tier == "C"
        assert t.accuracy_bonus_pp == self._C
        assert t.missile_tracking_active is True

    def test_hiroto_ultrascan_is_tier_c(self):
        t = resolve_scanner_tier(_loadout_with("Hiroto Ultrascan"), tier_b_bonus_pp=self._B, tier_c_bonus_pp=self._C)
        assert t.tier == "C"
        assert t.accuracy_bonus_pp == self._C

    def test_unknown_module_is_tier_a(self):
        """Any module name not in _SCANNER_TIER_BY_NAME (dict-miss) → Tier A."""
        t = resolve_scanner_tier(
            _loadout_with("__test_unknown_scanner__"), tier_b_bonus_pp=self._B, tier_c_bonus_pp=self._C
        )
        assert t.tier == "A"
        assert t.accuracy_bonus_pp == 0.0
        assert t.missile_tracking_active is False

    def test_unknown_module_plus_hiroto_proscan_is_tier_c(self):
        """Non-combat module + combat scanner → combat scanner tier wins (dict-miss is Tier A)."""
        t = resolve_scanner_tier(
            _loadout_with("__test_unknown_scanner__", "Hiroto Proscan"),
            tier_b_bonus_pp=self._B,
            tier_c_bonus_pp=self._C,
        )
        assert t.tier == "C"

    def test_two_combat_scanners_picks_highest_tier(self):
        """Telta Quickscan (B) + Hiroto Proscan (C) → Tier C (highest wins)."""
        t = resolve_scanner_tier(
            _loadout_with("Telta Quickscan", "Hiroto Proscan"),
            tier_b_bonus_pp=self._B,
            tier_c_bonus_pp=self._C,
        )
        assert t.tier == "C"

    def test_stateless_same_loadout_same_result(self):
        """Same loadout always returns identical ScannerTier (stateless function)."""
        loadout = _loadout_with("Telta Ecoscan")
        t1 = resolve_scanner_tier(loadout, tier_b_bonus_pp=self._B, tier_c_bonus_pp=self._C)
        t2 = resolve_scanner_tier(loadout, tier_b_bonus_pp=self._B, tier_c_bonus_pp=self._C)
        assert t1 == t2

    def test_scanner_tier_is_frozen(self):
        """ScannerTier is a frozen dataclass — mutation raises AttributeError."""
        t = ScannerTier(tier="A", accuracy_bonus_pp=0.0, missile_tracking_active=False)
        with pytest.raises(AttributeError):
            t.tier = "B"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 9. Resolver integration — scanner_tier precomputed; accuracy reflects tier
# ---------------------------------------------------------------------------


class TestResolverIntegration:
    def test_scanner_tier_set_at_init_tier_c(self):
        """_init_combatant precomputes scanner_tier=C for a loadout with Hiroto Proscan."""
        loadout = ShipLoadout(ship_name="C1", base_armour=100, modules=[ModuleStats(name="Hiroto Proscan")])
        state = _init_combatant(loadout, is_player=True)
        assert state.scanner_tier.tier == "C"
        assert state.scanner_tier.accuracy_bonus_pp == float(GameConstants.SCANNER_TIER_C_BONUS_PP)

    def test_scanner_tier_set_at_init_tier_a(self):
        """_init_combatant precomputes scanner_tier=A for an empty loadout."""
        state = _init_combatant(ShipLoadout(ship_name="C2", base_armour=100), is_player=False)
        assert state.scanner_tier.tier == "A"
        assert state.scanner_tier.accuracy_bonus_pp == 0.0

    def test_tier_c_player_accuracy(self):
        """Player (0.60 base) + Tier C (+10pp) → pilot_primary_acc = 0.70 (no thruster in T4)."""
        primary, turret = compute_pilot_accuracy(
            combatant_base=GameConstants.PLAYER_BASE_ACCURACY,
            own_scanner_bonus_pp=float(GameConstants.SCANNER_TIER_C_BONUS_PP),
            own_thruster_bonus_pp=0.0,
            opponent_booster_debuff_pp=0.0,
            opponent_cloak_active=False,
            cloak_set_value=GameConstants.CLOAK_SET_VALUE,
            clamp_min=GameConstants.ACCURACY_CLAMP_MIN,
            clamp_max=GameConstants.ACCURACY_CLAMP_MAX,
        )
        assert pytest.approx(0.70) == primary
        assert pytest.approx(0.70) == turret  # no thruster → equal

    def test_tier_a_npc_accuracy(self):
        """NPC (0.50 base) + Tier A (0pp) → pilot_primary_acc = 0.50."""
        primary, turret = compute_pilot_accuracy(
            combatant_base=GameConstants.NPC_BASE_ACCURACY,
            own_scanner_bonus_pp=0.0,
            own_thruster_bonus_pp=0.0,
            opponent_booster_debuff_pp=0.0,
            opponent_cloak_active=False,
            cloak_set_value=GameConstants.CLOAK_SET_VALUE,
            clamp_min=GameConstants.ACCURACY_CLAMP_MIN,
            clamp_max=GameConstants.ACCURACY_CLAMP_MAX,
        )
        assert pytest.approx(0.50) == primary
        assert pytest.approx(0.50) == turret

    def test_pilot_turret_acc_equals_primary_acc_no_thruster(self):
        """In T4 era (thruster_bonus=0), pilot_turret_acc == pilot_primary_acc for all inputs."""
        for scanner_pp in (0.0, 5.0, 10.0):
            primary, turret = compute_pilot_accuracy(
                combatant_base=GameConstants.PLAYER_BASE_ACCURACY,
                own_scanner_bonus_pp=scanner_pp,
                own_thruster_bonus_pp=0.0,
                opponent_booster_debuff_pp=0.0,
                opponent_cloak_active=False,
                cloak_set_value=GameConstants.CLOAK_SET_VALUE,
                clamp_min=GameConstants.ACCURACY_CLAMP_MIN,
                clamp_max=GameConstants.ACCURACY_CLAMP_MAX,
            )
            assert pytest.approx(primary) == turret, f"Diverged at scanner_pp={scanner_pp}"

    def test_resolver_runs_with_scanner_equipped(self):
        """TickResolver accepts loadouts with scanner modules and runs to completion."""
        l1 = ShipLoadout(ship_name="C1", base_armour=100, modules=[ModuleStats(name="Hiroto Proscan")])
        l2 = ShipLoadout(ship_name="C2", base_armour=100)
        result = TickResolver().resolve(l1, l2)
        assert result.is_stalemate is True  # drift fight, no weapons — time_cap

    def test_tick_loop_writes_pilot_accuracy_to_state(self):
        """Tick loop calls compute_pilot_accuracy and passes scanner bonus_pp from state.

        Approach (c): spy-patch compute_pilot_accuracy to capture call arguments,
        then assert the resolver passed the correct scanner_bonus_pp for each combatant.
        1 mock (the patch).
        """
        # Tier B scanner on C1 → scanner_bonus_pp=5 is distinguishable from C2's 0
        l_b = ShipLoadout(ship_name="C1", base_armour=100, modules=[ModuleStats(name="Telta Quickscan")])
        l_bare = ShipLoadout(ship_name="C2", base_armour=100)

        scanner_bonus_pp_seen: list[float] = []
        real_fn = compute_pilot_accuracy

        def spy(**kwargs):
            scanner_bonus_pp_seen.append(kwargs["own_scanner_bonus_pp"])
            return real_fn(**kwargs)

        with patch("services.combat_resolver.compute_pilot_accuracy", side_effect=spy):
            TickResolver(seed=0).resolve(l_b, l_bare)

        # Called at least once per combatant per tick (18,000 ticks × 2 = 36,000 calls)
        assert len(scanner_bonus_pp_seen) >= 2
        # C1 (Tier B) calls must pass 5.0; C2 (Tier A) must pass 0.0
        assert 5.0 in scanner_bonus_pp_seen
        assert 0.0 in scanner_bonus_pp_seen


# ---------------------------------------------------------------------------
# 10. T3 drift-to-floor regression
# ---------------------------------------------------------------------------


class TestT3DriftRegression:
    def test_drift_fight_unchanged_after_t4(self):
        """Empty loadouts drift to floor in MAX_FIGHT_TICKS — T4 must not alter this."""
        loadout = ShipLoadout(ship_name="Ship", base_armour=100)
        result = TickResolver(seed=42).resolve(loadout, loadout)
        assert result.is_stalemate is True
        assert result.winner_name is None
        assert result.metadata["metadata"]["total_ticks"] == GameConstants.MAX_FIGHT_TICKS
        assert result.metadata["metadata"]["resolver"] == "tick_v1"


# ---------------------------------------------------------------------------
# 11. RNG injection seam
# ---------------------------------------------------------------------------


class TestRngInjectionSeam:
    def test_rng_kwarg_accepted(self):
        """TickResolver.resolve() accepts rng=random.Random(seed) without error."""
        loadout = ShipLoadout(ship_name="Ship", base_armour=100)
        seeded_rng = random.Random(42)
        result = TickResolver().resolve(loadout, loadout, rng=seeded_rng)
        assert result is not None
        assert result.is_stalemate is True

    def test_rng_none_default_runs_normally(self):
        """rng=None (default) → resolver uses internal RNG, behaves as before."""
        loadout = ShipLoadout(ship_name="Ship", base_armour=100)
        result = TickResolver().resolve(loadout, loadout, rng=None)
        assert result.is_stalemate is True

    def test_rng_kwarg_takes_precedence_over_constructor_seed(self):
        """rng= kwarg takes precedence over constructor seed= — constructor _rng not consumed.

        In T4 there are no random draws, so we verify the constructor _rng is untouched
        after a call made with rng=: its next value equals a fresh Random(seed)'s next value.
        T5+ will strengthen this once actual draws occur from the injected rng.
        """
        loadout = ShipLoadout(ship_name="Ship", base_armour=100)
        resolver = TickResolver(seed=42)
        # What the constructor RNG would produce next (fresh reference, same seed)
        expected_next = random.Random(42).random()

        # Call with an injected rng= — constructor _rng must NOT be consumed
        result = resolver.resolve(loadout, loadout, rng=random.Random(99))
        assert result.is_stalemate is True

        # Constructor _rng still at initial state: next draw matches a fresh Random(42)
        actual_next = resolver._rng.random()
        assert pytest.approx(expected_next) == actual_next, "constructor _rng was consumed when rng= kwarg was provided"

    def test_rng_none_falls_back_to_constructor_seed(self):
        """rng=None falls back to self._rng (constructor seed); both calls use the same object."""
        loadout = ShipLoadout(ship_name="Ship", base_armour=100)
        resolver = TickResolver(seed=7)
        # In T4 no draws occur from _rng regardless of path, so we verify the call completes
        # and that self._rng is the same object reference both before and after
        original_rng = resolver._rng
        result = resolver.resolve(loadout, loadout, rng=None)
        assert result.is_stalemate is True
        assert resolver._rng is original_rng  # object identity unchanged
