"""
T5 acceptance tests: primary weapon firing path + PrimaryWeaponMod mechanics.

Test categories (per TASK_0005.md §Test surface):
  1.  Single primary, no mod — cadence verification
  2.  Range gate — boundary semantics
  3.  Range gate — cooldown still ticks while out of range
  4.  Multiple primaries, independent cooldowns
  5.  PrimaryWeaponMod — Nirai Overdrive (damage_pct=-10, fire_rate_pct=+20)
  6.  PrimaryWeaponMod — Nirai Overcharge (damage_pct=+20, fire_rate_pct=-10)
  7.  PrimaryWeaponMod — base-0 EMP primary, no floor
  8.  PrimaryWeaponMod — dpsMultiplier NOT consumed by tick resolver
  9.  Pure-EMP primary — fires + 0 HP delta
  10. Hybrid primary — physical only
  11. Tick-0 firing
  12. Cooldown reset on hit AND miss
  13. weapon_fire event ordering (phase 3 → 4, C1 before C2)
  14. T3 drift-to-floor regression
  15. Two-combatant fight to hp_depleted
  16. cooldown_end event count
  17. Acceptance — weapon_fire payload conforms to §12
"""

from __future__ import annotations

import pytest
from src.services.combat_models import ModuleStats, ShipLoadout, WeaponStats
from src.services.combat_service import TickResolver, _init_combatant
from src.services.game_constants import GameConstants

TICK_MS: int = GameConstants.TICK_MS  # 10
STARTING_DIST: float = float(GameConstants.STARTING_DISTANCE_M)  # 5000.0


# ---------------------------------------------------------------------------
# Deterministic RNG stubs (not mocks — real objects with a .random() method)
# ---------------------------------------------------------------------------


class _AlwaysHit:
    """Returns 0.0 every time: roll < any positive accuracy → always hit."""

    def random(self) -> float:
        return 0.0


class _AlwaysMiss:
    """Returns 1.0 every time: roll ≥ accuracy → always miss."""

    def random(self) -> float:
        return 1.0


# ---------------------------------------------------------------------------
# Loadout / weapon helpers
# ---------------------------------------------------------------------------


def _gun(
    name: str = "TestGun",
    damage: float = 10.0,
    speed_ms: int = 1000,
    range_m: float = 6000.0,
    dps: float = 1.0,
) -> WeaponStats:
    return WeaponStats(name=name, dps=dps, damage_per_shot=damage, loading_speed_ms=speed_ms, range_m=range_m)


def _loadout(
    weapons: list[WeaponStats] | None = None,
    modules: list[ModuleStats] | None = None,
    base_armour: int = 500,
    name: str = "TestShip",
) -> ShipLoadout:
    return ShipLoadout(
        ship_name=name,
        base_armour=base_armour,
        weapons=weapons or [],
        modules=modules or [],
    )


def _nirai_overdrive() -> ModuleStats:
    """Nirai Overdrive: damage_pct=-10, fire_rate_pct=+20 (lighter, faster shots)."""
    return ModuleStats(name="Nirai Overdrive", module_type="PrimaryWeaponModModule", damage_pct=-10, fire_rate_pct=20)


def _nirai_overcharge() -> ModuleStats:
    """Nirai Overcharge: damage_pct=+20, fire_rate_pct=-10 (heavier, slower shots)."""
    return ModuleStats(name="Nirai Overcharge", module_type="PrimaryWeaponModModule", damage_pct=20, fire_rate_pct=-10)


# ---------------------------------------------------------------------------
# Event-log query helpers
# ---------------------------------------------------------------------------


def _fire_events(log, actor: str):
    return [e for e in log if e.type == "weapon_fire" and e.actor == actor]


def _fire_ticks(log, actor: str) -> list[int]:
    return [e.tick for e in _fire_events(log, actor)]


def _cooldown_end_ticks(log, actor: str) -> list[int]:
    return [e.tick for e in log if e.type == "cooldown_end" and e.actor == actor]


def _damage_events_on(log, target: str):
    return [e for e in log if e.type == "damage" and e.target == target]


# ============================================================================
# Category 1 — Single primary, cadence verification
# ============================================================================


class TestCadence:
    def test_fires_at_expected_ticks(self):
        """weapon_fire events appear at tick 0, 100, 200, 300, 400 for 1000ms cooldown."""
        w = _gun(speed_ms=1000, range_m=6000.0)  # range > starting dist → always in range
        l1 = _loadout([w], base_armour=999999, name="Attacker")
        l2 = _loadout(base_armour=999999, name="Defender")
        result = TickResolver(seed=42).resolve(l1, l2)
        assert _fire_ticks(result.combat_log, "Attacker")[:5] == [0, 100, 200, 300, 400]

    def test_500ms_cooldown_cadence(self):
        """loading_speed_ms=500 → fires every 50 ticks: 0, 50, 100, ..."""
        w = _gun(speed_ms=500, range_m=6000.0)
        l1 = _loadout([w], base_armour=999999, name="Ship")
        l2 = _loadout(base_armour=999999, name="Dummy")
        result = TickResolver(seed=42).resolve(l1, l2)
        assert _fire_ticks(result.combat_log, "Ship")[:6] == [0, 50, 100, 150, 200, 250]


# ============================================================================
# Category 2 — Range gate boundary
# ============================================================================


class TestRangeGateBoundary:
    def test_fires_at_exactly_range_m(self):
        """current_distance == range_m → fires (closed interval ≤)."""
        w = _gun(speed_ms=1000, range_m=STARTING_DIST)  # exactly at range at tick 0
        l1 = _loadout([w], base_armour=999999, name="Ship")
        l2 = _loadout(base_armour=999999, name="Dummy")
        result = TickResolver(seed=42).resolve(l1, l2)
        assert 0 in _fire_ticks(result.combat_log, "Ship")

    def test_does_not_fire_one_meter_beyond_range(self):
        """current_distance = range_m + 1 → no fire at tick 0; fires at tick 1."""
        # range_m=4999 < STARTING_DIST=5000 → out of range at tick 0
        # After Phase6 of tick 0: dist=4997 ≤ 4999 → fires at tick 1
        w = _gun(speed_ms=1000, range_m=STARTING_DIST - 1.0)
        l1 = _loadout([w], base_armour=999999, name="Ship")
        l2 = _loadout(base_armour=999999, name="Dummy")
        result = TickResolver(seed=42).resolve(l1, l2)
        ticks = _fire_ticks(result.combat_log, "Ship")
        assert ticks[0] != 0
        assert ticks[0] == 1


# ============================================================================
# Category 3 — Cooldown still ticks while out of range
# ============================================================================


class TestCooldownTicksOutOfRange:
    def test_fires_immediately_when_in_range(self):
        """Weapon sits ready (cooldown=0) while out of range; fires on first in-range tick."""
        # range_m=3000: 5000-n*3 ≤ 3000 → n ≥ 667 → first in-range tick = 667
        w = _gun(speed_ms=1000, range_m=3000.0)
        l1 = _loadout([w], base_armour=999999, name="Ship")
        l2 = _loadout(base_armour=999999, name="Dummy")
        result = TickResolver(seed=42).resolve(l1, l2)
        ticks = _fire_ticks(result.combat_log, "Ship")
        assert ticks[0] == 667  # first in-range tick
        assert ticks[1] == 767  # next fire 100 ticks later (loading_speed_ms=1000)

    def test_no_fire_events_before_in_range(self):
        """No weapon_fire events occur before the weapon enters its range."""
        w = _gun(speed_ms=1000, range_m=3000.0)
        l1 = _loadout([w], base_armour=999999, name="Ship")
        l2 = _loadout(base_armour=999999, name="Dummy")
        result = TickResolver(seed=42).resolve(l1, l2)
        pre_range_fires = [e for e in result.combat_log if e.type == "weapon_fire" and e.tick < 667]
        assert len(pre_range_fires) == 0


# ============================================================================
# Category 4 — Multiple primaries, independent cooldowns
# ============================================================================


class TestMultiplePrimaries:
    def test_two_primaries_independent_cooldowns(self):
        """Two primaries on C1 run their own cooldown cycles independently."""
        w_a = _gun("GunA", speed_ms=500, range_m=6000.0)
        w_b = _gun("GunB", speed_ms=2000, range_m=6000.0)
        l1 = _loadout([w_a, w_b], base_armour=999999, name="C1")
        l2 = _loadout(base_armour=999999, name="C2")
        result = TickResolver(seed=42).resolve(l1, l2)

        a_ticks = [e.tick for e in result.combat_log if e.type == "weapon_fire" and e.data.get("weapon") == "GunA"]
        b_ticks = [e.tick for e in result.combat_log if e.type == "weapon_fire" and e.data.get("weapon") == "GunB"]

        # GunA: 500ms / 10ms = 50 ticks; first 9: [0, 50, 100, 150, 200, 250, 300, 350, 400]
        assert a_ticks[:9] == list(range(0, 401, 50))
        # GunB: 2000ms / 10ms = 200 ticks; first 3: [0, 200, 400]
        assert b_ticks[:3] == [0, 200, 400]


# ============================================================================
# Categories 5 & 6 — PrimaryWeaponMod baked stats
# ============================================================================


class TestPrimaryWeaponModBaking:
    def test_nirai_overdrive_effective_stats(self):
        """Nirai Overdrive (damage_pct=-10, fire_rate_pct=+20) bakes correctly."""
        # effective_damage = round(100 × 0.90) = 90
        # effective_speed  = round((1000 / 1.20) / 10) × 10 = round(83.33) × 10 = 830
        w = _gun(damage=100.0, speed_ms=1000)
        state = _init_combatant(_loadout([w], [_nirai_overdrive()]), is_player=False)
        pw = state.effective_primaries[0]
        assert pw.effective_damage_per_shot == 90
        assert pw.effective_loading_speed_ms == 830

    def test_nirai_overcharge_effective_stats(self):
        """Nirai Overcharge (damage_pct=+20, fire_rate_pct=-10) bakes correctly."""
        # effective_damage = round(100 × 1.20) = 120
        # effective_speed  = round((1000 / 0.90) / 10) × 10 = round(111.11) × 10 = 1110
        w = _gun(damage=100.0, speed_ms=1000)
        state = _init_combatant(_loadout([w], [_nirai_overcharge()]), is_player=False)
        pw = state.effective_primaries[0]
        assert pw.effective_damage_per_shot == 120
        assert pw.effective_loading_speed_ms == 1110

    def test_no_mod_identity(self):
        """No mod: effective stats equal the seed stats unchanged."""
        w = _gun(damage=50.0, speed_ms=800)
        state = _init_combatant(_loadout([w]), is_player=False)
        pw = state.effective_primaries[0]
        assert pw.effective_damage_per_shot == 50
        assert pw.effective_loading_speed_ms == 800

    def test_initial_cooldown_zero_after_mod_bake(self):
        """D6: PrimaryWeaponMod pre-pass must NOT alter initial cooldown_remaining_ms (§1)."""
        w = _gun(speed_ms=1000)
        state = _init_combatant(_loadout([w], [_nirai_overdrive()]), is_player=False)
        assert state.effective_primaries[0].cooldown_remaining_ms == 0

    # Category 7
    def test_base_zero_emp_stays_zero_no_floor(self):
        """Pure-EMP weapon (damage_per_shot=0) with mod: round(0 × 0.9) = 0. No floor (§7.8)."""
        emp = WeaponStats(name="EMP", dps=0.0, damage_per_shot=0.0, loading_speed_ms=1000, range_m=6000.0)
        state = _init_combatant(_loadout([emp], [_nirai_overdrive()]), is_player=False)
        assert state.effective_primaries[0].effective_damage_per_shot == 0

    # Category 8
    def test_dps_multiplier_not_consumed(self):
        """Legacy dpsMultiplier on ModuleStats is NOT consumed by the tick resolver (§7.8)."""
        w = _gun(damage=100.0, speed_ms=1000)
        mod = ModuleStats(
            name="Nirai Overdrive",
            module_type="PrimaryWeaponModModule",
            damage_pct=-10,
            fire_rate_pct=20,
            dps_multiplier=1.5,  # legacy field — must be ignored by resolver
        )
        state = _init_combatant(_loadout([w], [mod]), is_player=False)
        # Should be round(100 × 0.90) = 90, NOT multiplied by dps_multiplier
        assert state.effective_primaries[0].effective_damage_per_shot == 90


# ============================================================================
# Category 9 — Pure-EMP primary: fires + 0 HP delta
# ============================================================================


class TestPureEMPPrimary:
    def test_pure_emp_fires_emits_weapon_fire_and_zero_damage(self):
        """Pure-EMP weapon emits weapon_fire(hit=True) and damage(amount=0, no HP change)."""
        emp = WeaponStats(name="EMP Blaster", dps=0.0, damage_per_shot=0.0, loading_speed_ms=1000, range_m=6000.0)
        l1 = _loadout([emp], base_armour=100, name="Attacker")
        l2 = _loadout(base_armour=100, name="Target")
        result = TickResolver(seed=0).resolve(l1, l2, rng=_AlwaysHit())

        fire_evts = _fire_events(result.combat_log, "Attacker")
        assert len(fire_evts) > 0, "EMP weapon must emit weapon_fire events"
        assert all(e.data["hit"] is True for e in fire_evts), "AlwaysHit → all shots are hits"

        # damage events must be emitted (hit=True path) with amount=0
        dmg_evts = _damage_events_on(result.combat_log, "Target")
        assert len(dmg_evts) > 0, "damage events emitted for hits even at 0 HP delta"
        for evt in dmg_evts:
            assert evt.data["amount"] == 0
            assert evt.data["breakdown"] == {"shield": 0, "armour": 0, "hull": 0}

        # No layer_depleted events; no hp_depleted termination
        layer_evts = [e for e in result.combat_log if e.type == "layer_depleted"]
        assert len(layer_evts) == 0
        assert result.is_stalemate is True


# ============================================================================
# Category 10 — Hybrid primary: physical only
# ============================================================================


class TestHybridPrimary:
    def test_only_physical_damage_applied(self):
        """Weapon with damage_per_shot=50: effective_damage=50 (emp_damage is not a field)."""
        # WeaponStats has no emp_damage field; resolver only uses damage_per_shot.
        hybrid = WeaponStats(name="HybridGun", dps=5.0, damage_per_shot=50.0, loading_speed_ms=1000, range_m=6000.0)
        state = _init_combatant(_loadout([hybrid]), is_player=False)
        assert state.effective_primaries[0].effective_damage_per_shot == 50

        l1 = _loadout([hybrid], base_armour=100, name="Attacker")
        l2 = _loadout(base_armour=100, name="Target")
        result = TickResolver(seed=0).resolve(l1, l2, rng=_AlwaysHit())

        dmg_evts = _damage_events_on(result.combat_log, "Target")
        assert len(dmg_evts) > 0
        for evt in dmg_evts:
            # amount = 50 (physical only; PvC DR=0 default)
            assert evt.data["amount"] == 50


# ============================================================================
# Category 11 — Tick-0 firing
# ============================================================================


class TestTick0Firing:
    def test_in_range_weapon_fires_at_tick_0(self):
        """Weapon with cooldown=0 and range >= starting distance fires at tick 0."""
        w = _gun(speed_ms=1000, range_m=6000.0)
        l1 = _loadout([w], base_armour=999999, name="Ship")
        l2 = _loadout(base_armour=999999, name="Dummy")
        result = TickResolver(seed=42).resolve(l1, l2)
        ticks = _fire_ticks(result.combat_log, "Ship")
        assert ticks[0] == 0

    def test_out_of_range_weapon_does_not_fire_at_tick_0(self):
        """Weapon whose range < starting distance does NOT fire at tick 0."""
        w = _gun(speed_ms=1000, range_m=4000.0)  # 5000 > 4000 → out of range at tick 0
        l1 = _loadout([w], base_armour=999999, name="Ship")
        l2 = _loadout(base_armour=999999, name="Dummy")
        result = TickResolver(seed=42).resolve(l1, l2)
        ticks = _fire_ticks(result.combat_log, "Ship")
        assert 0 not in ticks


# ============================================================================
# Category 12 — Cooldown reset on hit AND miss
# ============================================================================


class TestCooldownReset:
    def test_cooldown_resets_after_hit(self):
        """Cooldown resets to effective_loading_speed_ms after a hit; next fire = tick + period."""
        w = _gun(speed_ms=1000, range_m=6000.0)
        l1 = _loadout([w], base_armour=999999, name="Ship")
        l2 = _loadout(base_armour=999999, name="Dummy")
        result = TickResolver(seed=0).resolve(l1, l2, rng=_AlwaysHit())
        ticks = _fire_ticks(result.combat_log, "Ship")
        assert ticks[0] == 0 and ticks[1] == 100 and ticks[2] == 200

    def test_cooldown_resets_after_miss(self):
        """Cooldown resets even on a miss; weapon fires again at the expected tick."""
        w = _gun(speed_ms=1000, range_m=6000.0)
        l1 = _loadout([w], base_armour=999999, name="Ship")
        l2 = _loadout(base_armour=999999, name="Dummy")
        result = TickResolver(seed=0).resolve(l1, l2, rng=_AlwaysMiss())
        ticks = _fire_ticks(result.combat_log, "Ship")
        # Every shot misses, but cadence is unchanged
        assert ticks[:3] == [0, 100, 200]


# ============================================================================
# Category 13 — Event ordering (phase 3 before 4, C1 before C2)
# ============================================================================


class TestEventOrdering:
    def test_all_weapon_fire_events_before_damage_in_same_tick(self):
        """Phase 3 weapon_fire events precede phase 4 damage events within each tick."""
        w = _gun(speed_ms=1000, range_m=6000.0, damage=10.0)
        l1 = _loadout([w], base_armour=500, name="C1")
        l2 = _loadout([w], base_armour=500, name="C2")
        result = TickResolver(seed=0).resolve(l1, l2, rng=_AlwaysHit())

        # Verify on tick 0 (both ships fire and hits land)
        tick0 = [e for e in result.combat_log if e.tick == 0]
        fire_idxs = [i for i, e in enumerate(tick0) if e.type == "weapon_fire"]
        dmg_idxs = [i for i, e in enumerate(tick0) if e.type == "damage"]
        assert fire_idxs and dmg_idxs
        assert max(fire_idxs) < min(dmg_idxs), "All weapon_fire must precede all damage at same tick"

    def test_c1_weapon_fire_before_c2_weapon_fire(self):
        """C1's weapon_fire event comes before C2's weapon_fire on the same tick (Appendix B)."""
        w = _gun(speed_ms=1000, range_m=6000.0)
        l1 = _loadout([w], base_armour=500, name="C1")
        l2 = _loadout([w], base_armour=500, name="C2")
        result = TickResolver(seed=0).resolve(l1, l2, rng=_AlwaysHit())

        tick0_fire_actors = [e.actor for e in result.combat_log if e.tick == 0 and e.type == "weapon_fire"]
        assert tick0_fire_actors == ["C1", "C2"]

    def test_c1_damage_on_c2_before_c2_damage_on_c1(self):
        """Phase 4: C1's hit on C2 applied before C2's hit on C1 (Appendix B C1-before-C2)."""
        w = _gun(speed_ms=1000, range_m=6000.0)
        l1 = _loadout([w], base_armour=500, name="C1")
        l2 = _loadout([w], base_armour=500, name="C2")
        result = TickResolver(seed=0).resolve(l1, l2, rng=_AlwaysHit())

        tick0_dmg_targets = [e.target for e in result.combat_log if e.tick == 0 and e.type == "damage"]
        assert tick0_dmg_targets == ["C2", "C1"]


# ============================================================================
# Category 14 — T3 drift-to-floor regression
# ============================================================================


class TestT3Regression:
    def test_empty_loadout_fight_still_terminates_as_time_cap(self):
        """Empty-loadout drift fight terminates with stalemate/time_cap (T3 behaviour intact)."""
        l1 = ShipLoadout(ship_name="C1", base_armour=100)
        l2 = ShipLoadout(ship_name="C2", base_armour=100)
        result = TickResolver(seed=42).resolve(l1, l2)
        assert result.is_stalemate is True
        assert result.metadata["total_ticks"] == GameConstants.MAX_FIGHT_TICKS


# ============================================================================
# Category 15 — Fight to hp_depleted with shield→armour→hull traversal
# ============================================================================


class TestFightToHPDepleted:
    def test_fight_terminates_hp_depleted_correct_winner(self):
        """Higher-HP ship wins; fight terminates with reason=hp_depleted."""
        w = _gun(damage=20.0, speed_ms=1000, range_m=6000.0)
        l1 = _loadout([w], base_armour=70, name="C1")
        l2 = _loadout([w], base_armour=50, name="C2")
        result = TickResolver(seed=0).resolve(l1, l2, rng=_AlwaysHit())

        assert result.is_stalemate is False
        assert result.winner_name == "C1"
        assert result.loser_name == "C2"
        end_evt = next(e for e in result.combat_log if e.type == "fight_end")
        assert end_evt.data["reason"] == "hp_depleted"

    def test_damage_walks_hull_when_no_shield_or_armour(self):
        """With no modules, all damage accumulates in hull (shield=0, armour=0)."""
        w = _gun(damage=20.0, speed_ms=1000, range_m=6000.0)
        l1 = _loadout([w], base_armour=70, name="C1")
        l2 = _loadout([w], base_armour=50, name="C2")
        result = TickResolver(seed=0).resolve(l1, l2, rng=_AlwaysHit())

        c2_dmg = _damage_events_on(result.combat_log, "C2")
        assert len(c2_dmg) > 0
        for evt in c2_dmg:
            assert evt.data["breakdown"]["shield"] == 0
            assert evt.data["breakdown"]["armour"] == 0
            assert evt.data["breakdown"]["hull"] > 0


# ============================================================================
# Category 16 — cooldown_end event semantics
# ============================================================================


class TestCooldownEndEvents:
    def test_no_cooldown_end_at_tick_0(self):
        """No cooldown_end at tick 0: cooldown starts at 0, no >0→0 transition occurs."""
        w = _gun(speed_ms=1000, range_m=6000.0)
        l1 = _loadout([w], base_armour=999999, name="Ship")
        l2 = _loadout(base_armour=999999, name="Dummy")
        result = TickResolver(seed=42).resolve(l1, l2)
        assert 0 not in _cooldown_end_ticks(result.combat_log, "Ship")

    def test_cooldown_end_at_correct_ticks(self):
        """cooldown_end fires on the tick the cooldown crosses >0→0 after each fire."""
        w = _gun(speed_ms=1000, range_m=6000.0)
        l1 = _loadout([w], base_armour=999999, name="Ship")
        l2 = _loadout(base_armour=999999, name="Dummy")
        result = TickResolver(seed=42).resolve(l1, l2)
        # After tick-0 fire (reset=1000), cooldown reaches 0 at tick 100. Then 200, 300, 400.
        cde = _cooldown_end_ticks(result.combat_log, "Ship")
        assert cde[:4] == [100, 200, 300, 400]

    def test_cooldown_end_coincides_with_weapon_fire(self):
        """Every non-tick-0 fire tick has a preceding cooldown_end event on the same tick."""
        w = _gun(speed_ms=1000, range_m=6000.0)
        l1 = _loadout([w], base_armour=999999, name="Ship")
        l2 = _loadout(base_armour=999999, name="Dummy")
        result = TickResolver(seed=42).resolve(l1, l2)
        cde = set(_cooldown_end_ticks(result.combat_log, "Ship"))
        fire = set(_fire_ticks(result.combat_log, "Ship")) - {0}  # exclude tick-0 (no CDE)
        assert cde == fire


# ============================================================================
# Category 17 — weapon_fire payload conforms to §12
# ============================================================================


class TestWeaponFirePayload:
    def test_required_keys_present(self):
        """weapon_fire data must carry {slot, subtype, weapon, hit, accuracy} (§12)."""
        w = _gun()
        l1 = _loadout([w], base_armour=999999, name="Ship")
        l2 = _loadout(base_armour=999999, name="Dummy")
        result = TickResolver(seed=42).resolve(l1, l2)
        fire_evts = [e for e in result.combat_log if e.type == "weapon_fire"]
        assert fire_evts, "No weapon_fire events found"
        for evt in fire_evts[:5]:
            d = evt.data
            assert d["slot"] == "primary"
            assert d["subtype"] == "primary"
            assert isinstance(d["weapon"], str)
            assert isinstance(d["hit"], bool)
            assert isinstance(d["accuracy"], float)
            assert GameConstants.ACCURACY_CLAMP_MIN <= d["accuracy"] <= GameConstants.ACCURACY_CLAMP_MAX

    def test_miss_emits_no_damage_event(self):
        """weapon_fire(hit=false) is the sole record; no damage event for misses (Q10 lock)."""
        w = _gun()
        l1 = _loadout([w], base_armour=999999, name="Attacker")
        l2 = _loadout(base_armour=999999, name="Target")
        result = TickResolver(seed=0).resolve(l1, l2, rng=_AlwaysMiss())

        fire_evts = _fire_events(result.combat_log, "Attacker")
        dmg_evts = _damage_events_on(result.combat_log, "Target")
        assert all(e.data["hit"] is False for e in fire_evts)
        assert len(dmg_evts) == 0, "No damage events for misses (Q10)"

    def test_accuracy_value_is_npc_base(self):
        """accuracy in event == NPC base accuracy when no scanner/thruster/booster in play."""
        w = _gun()
        l1 = _loadout([w], base_armour=999999, name="Ship")
        l2 = _loadout(base_armour=999999, name="Dummy")
        result = TickResolver(seed=42).resolve(l1, l2)
        for evt in _fire_events(result.combat_log, "Ship")[:3]:
            assert evt.data["accuracy"] == pytest.approx(GameConstants.NPC_BASE_ACCURACY, abs=1e-9)


# ============================================================================
# Determinism — same seed → identical timeline
# ============================================================================


class TestDeterminism:
    def test_same_seed_produces_identical_timeline(self):
        """Two TickResolver(seed=N) instances produce byte-for-byte identical event logs."""
        w = _gun(damage=15.0, speed_ms=500, range_m=6000.0)
        l1 = _loadout([w], base_armour=200, name="C1")
        l2 = _loadout([w], base_armour=200, name="C2")

        res1 = TickResolver(seed=77).resolve(l1, l2)
        res2 = TickResolver(seed=77).resolve(l1, l2)

        assert len(res1.combat_log) == len(res2.combat_log)
        for e1, e2 in zip(res1.combat_log, res2.combat_log, strict=True):
            assert e1.tick == e2.tick
            assert e1.type == e2.type
            assert e1.actor == e2.actor
            assert e1.target == e2.target
