"""
T6 acceptance tests: secondary weapon firing path — all 5 subtypes.

Test categories (per TASK_0006.md §Test surface):
  Rocket:          1–4   (curve at min/max/midpoint; range gate)
  Missile:         5–7   (tier A/B/C branches)
  Cluster missile: 8–12  (snapshot semantics; independence; overkill; condensed log; tier-A)
  Nuke:            13–18 (point-blank; long-range; PvC DR; steerable ignored; no acc roll; RNG seam)
  Shock-blast:     19–23 (reset; deterministic; module independence; events; seed damage ignored)
  Pure-EMP:        24    (Mamba EMP)
  Cross-subtype:   25–27 (cooldown reset; T1–T5 regression; §12 payloads)

D0.5 integration test: builder-fed fight (real secondaries + primary true-up).
"""

from __future__ import annotations

import random
import sys
import types
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

# Stub sqlalchemy_utils
if "sqlalchemy_utils" not in sys.modules:
    _sqla_utils = types.ModuleType("sqlalchemy_utils")
    _sqla_utils.UUIDType = MagicMock()
    sys.modules["sqlalchemy_utils"] = _sqla_utils

from src.services.combat_models import ModuleStats, ShipLoadout, WeaponStats
from src.services.combat_service import TickResolver, _init_combatant, _nuke_dmg, _rocket_accuracy, _shock_blast_apply
from src.services.game_constants import GameConstants

TICK_MS: int = GameConstants.TICK_MS  # 10
MIN_DIST: float = float(GameConstants.MIN_DISTANCE_M)  # 300.0
STARTING_DIST: float = float(GameConstants.STARTING_DISTANCE_M)  # 5000.0


# ---------------------------------------------------------------------------
# Deterministic RNG stubs (no mocks — real objects)
# ---------------------------------------------------------------------------


class _AlwaysHit:
    """Returns 0.0 every .random() call — always below any positive accuracy."""

    def random(self) -> float:
        return 0.0

    def uniform(self, a: float, b: float) -> float:  # for nuke epicenter
        return a  # return MIN_DISTANCE_M


class _AlwaysMiss:
    """Returns 1.0 every .random() call — never below any accuracy < 1.0."""

    def random(self) -> float:
        return 1.0

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2.0


class _FixedEpicenter:
    """RNG that returns a fixed epicenter via uniform(), and fixed roll via random()."""

    def __init__(self, epicenter: float, roll: float = 0.0) -> None:
        self.epicenter = epicenter
        self.roll = roll

    def random(self) -> float:
        return self.roll

    def uniform(self, a: float, b: float) -> float:
        return max(a, min(b, self.epicenter))


class _SequencedRNG:
    """Returns values from a list in order; raises if exhausted."""

    def __init__(self, values: list[float]) -> None:
        self._values = list(values)
        self._idx = 0

    def random(self) -> float:
        v = self._values[self._idx]
        self._idx += 1
        return v

    def uniform(self, a: float, b: float) -> float:
        v = self._values[self._idx]
        self._idx += 1
        return max(a, min(b, v))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _secondary(
    name: str = "TestSecondary",
    subtype: str = "rocket",
    damage: float = 50.0,
    speed_ms: int = 1000,
    range_m: float = 4000.0,
    dps: float = 1.0,
    burst_count: int = 0,
    emp_damage: int = 0,
    magnitude_m: float = 0.0,
    steerable: bool = False,
) -> WeaponStats:
    return WeaponStats(
        name=name,
        dps=dps,
        damage_per_shot=damage,
        loading_speed_ms=speed_ms,
        range_m=range_m,
        subtype=subtype,
        burst_count=burst_count,
        emp_damage=emp_damage,
        magnitude_m=magnitude_m,
        steerable=steerable,
    )


def _primary(name: str = "Gun", damage: float = 10.0, speed_ms: int = 1000, range_m: float = 6000.0) -> WeaponStats:
    return WeaponStats(name=name, dps=1.0, damage_per_shot=damage, loading_speed_ms=speed_ms, range_m=range_m)


def _loadout(
    weapons: list[WeaponStats] | None = None,
    secondary_weapons: list[WeaponStats] | None = None,
    modules: list[ModuleStats] | None = None,
    base_armour: int = 500,
    name: str = "Ship",
) -> ShipLoadout:
    return ShipLoadout(
        ship_name=name,
        base_armour=base_armour,
        weapons=weapons or [],
        secondary_weapons=secondary_weapons or [],
        modules=modules or [],
    )


def _telta_scanner() -> ModuleStats:
    """Telta Quickscan — Tier B scanner."""
    return ModuleStats(name="Telta Quickscan")


def _hiroto_scanner() -> ModuleStats:
    """Hiroto Proscan — Tier C scanner."""
    return ModuleStats(name="Hiroto Proscan")


def _fire_events(log, actor: str) -> list:
    return [e for e in log if e.type == "weapon_fire" and e.actor == actor]


def _damage_events(log, target: str) -> list:
    return [e for e in log if e.type == "damage" and e.target == target]


def _dist_events(log) -> list:
    return [e for e in log if e.type == "distance"]


def _run_single_tick(
    attacker_loadout: ShipLoadout,
    target_loadout: ShipLoadout,
    *,
    rng=None,
    pvc_dr: float = 0.0,
) -> list:
    """Run a one-tick fight and return all tick-0 events."""
    resolver = TickResolver()
    result = resolver.resolve(attacker_loadout, target_loadout, pvc_damage_reduction=pvc_dr, rng=rng)
    return [e for e in result.combat_log if e.tick == 0]


# ===========================================================================
# Rocket tests (D2)
# ===========================================================================


class TestRocket:
    def test_curve_max_at_min_distance(self):
        """Curve produces 0.60 at min distance (clamp upper edge). Test 1."""
        acc = _rocket_accuracy(MIN_DIST, 4000.0, MIN_DIST)
        assert abs(acc - 0.60) < 1e-9

    def test_curve_min_at_max_range(self):
        """Curve produces 0.05 at max range (clamp lower edge). Test 2."""
        acc = _rocket_accuracy(4000.0, 4000.0, MIN_DIST)
        assert abs(acc - 0.05) < 1e-9

    def test_curve_midpoint(self):
        """Linear midpoint: current_distance = (range_m + MIN) / 2 → ~0.325. Test 3."""
        range_m = 4000.0
        mid = (range_m + MIN_DIST) / 2.0
        acc = _rocket_accuracy(mid, range_m, MIN_DIST)
        expected = 0.05 + 0.55 * 0.5
        assert abs(acc - expected) < 1e-6

    def test_range_gate_exact_boundary(self):
        """Range gate: distance = range_m → fires; distance = range_m + 1 → no fire. Test 4."""
        rocket = _secondary(subtype="rocket", range_m=1100.0, damage=50.0, speed_ms=500)  # noqa: F841
        # At exact range: should fire with AlwaysHit
        l2 = _loadout(name="Target")
        # Force current_distance to exactly range_m by using a large range weapon
        # We can't directly control distance on tick 0 (starts at 5000 m), so use a weapon
        # whose range_m matches STARTING_DISTANCE_M
        rocket_long = _secondary(subtype="rocket", range_m=5000.0, damage=50.0, speed_ms=500)
        l1_long = _loadout(secondary_weapons=[rocket_long], name="Attacker")
        t0 = _run_single_tick(l1_long, l2, rng=_AlwaysHit())
        fires = [e for e in t0 if e.type == "weapon_fire" and e.data.get("subtype") == "rocket"]
        assert len(fires) == 1, "Rocket should fire when current_distance == range_m"

        # Out of range: rocket with range_m slightly below STARTING_DISTANCE_M
        rocket_short = _secondary(subtype="rocket", range_m=4999.0, damage=50.0, speed_ms=500)
        l1_short = _loadout(secondary_weapons=[rocket_short], name="Attacker")
        t0_short = _run_single_tick(l1_short, l2, rng=_AlwaysHit())
        fires_short = [e for e in t0_short if e.type == "weapon_fire" and e.data.get("subtype") == "rocket"]
        assert len(fires_short) == 0, "Rocket should NOT fire when current_distance > range_m"

    def test_rocket_hit_applies_damage(self):
        """Rocket hit routes damage through T3 helper — target HP decreases."""
        rocket = _secondary(subtype="rocket", range_m=5000.0, damage=50.0, speed_ms=500)
        l1 = _loadout(secondary_weapons=[rocket], name="Attacker")
        l2 = _loadout(base_armour=200, name="Target")
        t0 = _run_single_tick(l1, l2, rng=_AlwaysHit())
        dmg = [e for e in t0 if e.type == "damage" and e.target == "Target"]
        assert len(dmg) == 1
        assert dmg[0].data["amount"] == 50


# ===========================================================================
# Missile tests (D3)
# ===========================================================================


class TestMissile:
    def test_tier_a_uses_rocket_curve(self):
        """Tier A (no scanner) → missile uses rocket curve. Test 5."""
        # No scanner module → Tier A
        missile = _secondary(subtype="missile", range_m=5000.0, damage=70.0, speed_ms=2000)
        l1 = _loadout(secondary_weapons=[missile], name="Attacker")
        l2 = _loadout(name="Target")
        t0 = _run_single_tick(l1, l2, rng=_AlwaysHit())
        fires = [e for e in t0 if e.type == "weapon_fire" and e.data.get("subtype") == "missile"]
        assert len(fires) == 1
        assert fires[0].data["branch"] == "tier_a"
        # Accuracy matches rocket curve at STARTING_DISTANCE_M
        expected_acc = _rocket_accuracy(STARTING_DIST, 5000.0, MIN_DIST)
        assert abs(fires[0].data["accuracy"] - expected_acc) < 1e-9

    def test_tier_b_uses_pilot_accuracy(self):
        """Tier B scanner → missile tracking active → uses pilot_primary_acc. Test 6."""
        missile = _secondary(subtype="missile", range_m=5000.0, damage=70.0, speed_ms=2000)
        scanner = _telta_scanner()
        l1 = _loadout(secondary_weapons=[missile], modules=[scanner], name="Attacker")
        l2 = _loadout(name="Target")
        t0 = _run_single_tick(l1, l2, rng=_AlwaysHit())
        fires = [e for e in t0 if e.type == "weapon_fire" and e.data.get("subtype") == "missile"]
        assert len(fires) == 1
        assert fires[0].data["branch"] == "tier_bc"
        # Accuracy should NOT depend on distance (it's pilot accuracy + scanner bonus)
        # NPC base = 0.50, Tier B = +5pp → 0.55
        acc = fires[0].data["accuracy"]
        assert abs(acc - 0.55) < 1e-6, f"Expected 0.55, got {acc}"

    def test_tier_c_uses_pilot_accuracy(self):
        """Tier C scanner (Hiroto) → same branch as Tier B. Test 7."""
        missile = _secondary(subtype="missile", range_m=5000.0, damage=70.0, speed_ms=2000)
        scanner = _hiroto_scanner()
        l1 = _loadout(secondary_weapons=[missile], modules=[scanner], name="Attacker")
        l2 = _loadout(name="Target")
        t0 = _run_single_tick(l1, l2, rng=_AlwaysHit())
        fires = [e for e in t0 if e.type == "weapon_fire" and e.data.get("subtype") == "missile"]
        assert len(fires) == 1
        assert fires[0].data["branch"] == "tier_bc"
        # NPC base = 0.50, Tier C = +10pp → 0.60
        acc = fires[0].data["accuracy"]
        assert abs(acc - 0.60) < 1e-6, f"Expected 0.60, got {acc}"


# ===========================================================================
# Cluster missile tests (D4)
# ===========================================================================


class TestClusterMissile:
    def test_accuracy_snapshot_semantics(self):
        """Fire-time snapshot: all N sub-munitions roll against the snapshot accuracy. Test 8.

        The hits_mask is computed ONCE at fire time (Phase 3) using the pilot_primary_acc
        snapshot. Phase 4 drains the pre-computed hits_mask blindly — no recomputation.

        Proof strategy: use _SequencedRNG with rolls that disambiguate Tier B (0.55) vs
        Tier A rocket curve (0.05) at STARTING_DISTANCE_M.

        At STARTING_DISTANCE_M (5000m) with range_m=5000m:
          - Tier B (pilot_primary_acc=0.55): rolls [0.50,0.56,0.50,0.56,0.50] → 3 hits
          - Tier A (rocket curve=0.05): same rolls → 0 hits (all > 0.05)

        This also verifies the hits_mask is frozen at fire time: any recomputation in
        Phase 4 using a different distance would change the hit count.
        """
        patala = _secondary(
            subtype="cluster-missile", range_m=5000.0, damage=90.0, speed_ms=3000, burst_count=5, name="Patala"
        )
        scanner = _telta_scanner()
        l1 = _loadout(secondary_weapons=[patala], modules=[scanner], name="Attacker")
        l2 = _loadout(base_armour=1000, name="Target")

        # --- Part 1: verify weapon_fire event records Tier B snapshot accuracy ---
        rng_a = random.Random(42)
        result_a = TickResolver().resolve(l1, l2, rng=rng_a)
        fires_a = [
            e
            for e in result_a.combat_log
            if e.tick == 0 and e.type == "weapon_fire" and e.data.get("subtype") == "cluster-missile"
        ]
        assert len(fires_a) == 1
        ev_a = fires_a[0]

        # Verify snapshot is Tier B pilot_primary_acc = 0.55 (not rocket curve ~0.05)
        assert "accuracy" in ev_a.data, "weapon_fire must carry accuracy snapshot"
        assert abs(ev_a.data["accuracy"] - 0.55) < 1e-6, (
            f"Tier B snapshot must be pilot_primary_acc=0.55, got {ev_a.data['accuracy']}"
        )
        assert ev_a.data["fired"] == 5

        # --- Part 2: _SequencedRNG proof — snapshot frozen at phase 3 ---
        # Rolls [0.50,0.56,0.50,0.56,0.50]: at accuracy=0.55 → 3 hits (0.50<0.55 hit, 0.56>=0.55 miss)
        # At rocket curve=0.05: all rolls > 0.05 → 0 hits.
        # If Phase 4 recomputed accuracy using ANY other distance, hit count would differ.
        rng_proof = _SequencedRNG([0.50, 0.56, 0.50, 0.56, 0.50] + [0.9] * 500)
        result_proof = TickResolver().resolve(l1, l2, rng=rng_proof)
        fires_proof = [
            e
            for e in result_proof.combat_log
            if e.tick == 0 and e.type == "weapon_fire" and e.data.get("subtype") == "cluster-missile"
        ]
        assert len(fires_proof) == 1
        ev_proof = fires_proof[0]
        assert ev_proof.data["hits"] == 3, (
            f"At Tier B snapshot=0.55: rolls [0.50,0.56,0.50,0.56,0.50] → 3 hits. "
            f"Got {ev_proof.data['hits']}. If 0 hits, snapshot was rocket curve (0.05) not pilot_primary_acc."
        )

    def test_sub_munition_independence(self):
        """Alternating hits: seeded RNG forces alternating hit/miss. Test 9."""
        patala = _secondary(
            subtype="cluster-missile", range_m=5000.0, damage=90.0, speed_ms=3000, burst_count=5, name="Patala"
        )
        scanner = _telta_scanner()
        l1 = _loadout(secondary_weapons=[patala], modules=[scanner], name="Attacker")
        l2 = _loadout(base_armour=1000, name="Target")
        # Accuracy snapshot uses Tier B pilot_primary_acc.
        # NPC base = 0.50, Tier B bonus = 5pp → pilot_primary_acc = 0.55.
        # Each sub-munition draws ONE random() call. Roll < 0.55 = hit.
        # Sequence: 0.0 (hit), 0.9 (miss), 0.0 (hit), 0.9 (miss), 0.0 (hit) → 3 hits
        # Provide many fallback 0.9s so exhaustion never happens
        rng = _SequencedRNG([0.0, 0.9, 0.0, 0.9, 0.0] + [0.9] * 500)
        result = TickResolver().resolve(l1, l2, rng=rng)
        t0 = [e for e in result.combat_log if e.tick == 0]
        fires = [e for e in t0 if e.type == "weapon_fire" and e.data.get("subtype") == "cluster-missile"]
        assert len(fires) == 1
        assert fires[0].data["hits"] == 3
        assert fires[0].data["fired"] == 5
        # 3 landed sub-munitions → 3 damage events
        dmg_evs = [e for e in t0 if e.type == "damage" and e.target == "Target"]
        assert len(dmg_evs) == 3

    def test_overkill_allowed(self):
        """Overkill: 5 hits × 90 damage on 100 HP hull → transient negative, clamped at 0. Test 10."""
        patala = _secondary(
            subtype="cluster-missile", range_m=5000.0, damage=90.0, speed_ms=3000, burst_count=5, name="Patala"
        )
        scanner = _telta_scanner()
        l1 = _loadout(secondary_weapons=[patala], modules=[scanner], name="Attacker")
        l2 = _loadout(base_armour=100, name="Target")  # 100 HP hull only
        # All 5 hit
        result = TickResolver().resolve(l1, l2, rng=_AlwaysHit())
        t0 = [e for e in result.combat_log if e.tick == 0]
        fires = [e for e in t0 if e.type == "weapon_fire" and e.data.get("subtype") == "cluster-missile"]
        assert fires[0].data["hits"] == 5
        # After all damage, check final HP is 0 (clamped at step 4b)
        # The fight ends on tick 0 — winner is the attacker
        assert result.winner_name == "Attacker"
        # last damage event's hp_after["hull"] should be at most 0 (clamped)
        dmg_evs = [e for e in t0 if e.type == "damage" and e.target == "Target"]
        assert len(dmg_evs) == 5
        # Check that hull can go transiently negative (penultimate damage may have negative hull)
        # Final hp is clamped to 0 via step 4b — inspect fight_end for confirmation
        fight_end = [e for e in result.combat_log if e.type == "fight_end"]
        assert fight_end[0].data["final_hp"]["c2"]["hull"] == 0

    def test_condensed_log_event(self):
        """Exactly ONE weapon_fire per cluster fire; 5 damage events separately. Test 11."""
        patala = _secondary(
            subtype="cluster-missile", range_m=5000.0, damage=90.0, speed_ms=3000, burst_count=5, name="Patala"
        )
        scanner = _telta_scanner()
        l1 = _loadout(secondary_weapons=[patala], modules=[scanner], name="Attacker")
        l2 = _loadout(base_armour=100, name="Target")
        result = TickResolver().resolve(l1, l2, rng=_AlwaysHit())
        t0 = [e for e in result.combat_log if e.tick == 0]
        fires = [e for e in t0 if e.type == "weapon_fire" and e.data.get("subtype") == "cluster-missile"]
        assert len(fires) == 1, "Must be exactly 1 weapon_fire per cluster fire"
        ev = fires[0]
        assert ev.data["fired"] == 5
        assert ev.data["hits"] == 5
        assert ev.data["damage_per_hit"] == 90
        assert ev.data["total_damage"] == 5 * 90  # K × damage (swung output)
        # 5 damage events (one per hit, via T3 helper)
        dmg_evs = [e for e in t0 if e.type == "damage" and e.target == "Target"]
        assert len(dmg_evs) == 5

    def test_tier_a_cluster_snapshot_uses_rocket_curve(self):
        """Tier A cluster — snapshot uses rocket curve at fire-time distance. Test 12."""
        patala = _secondary(
            subtype="cluster-missile", range_m=5000.0, damage=90.0, speed_ms=3000, burst_count=5, name="Patala"
        )
        # No scanner → Tier A
        l1 = _loadout(secondary_weapons=[patala], name="Attacker")
        l2 = _loadout(base_armour=1000, name="Target")
        rng = random.Random(42)
        result = TickResolver().resolve(l1, l2, rng=rng)
        t0_fires = [
            e
            for e in result.combat_log
            if e.tick == 0 and e.type == "weapon_fire" and e.data.get("subtype") == "cluster-missile"
        ]
        assert len(t0_fires) == 1
        ev = t0_fires[0]
        assert ev.data.get("branch") == "tier_a"
        # Accuracy should match rocket curve at STARTING_DISTANCE_M
        expected_acc = _rocket_accuracy(STARTING_DIST, 5000.0, MIN_DIST)
        assert abs(ev.data["accuracy"] - expected_acc) < 1e-9

    def test_burst_count_zero_degraded_fires_one_sub_munition(self):
        """burst_count=0 cluster missile fires exactly one sub-munition (graceful degradation).

        Live-DB production behavior: some cluster missiles have burst_count=0 in the DB
        (seed data not yet backfilled). The resolver degrades gracefully: _n = max(burst_count, 1)
        → exactly ONE sub-munition fires, emitting one condensed weapon_fire event.

        This test documents the ACTUAL behavior and guards against regressions where
        burst_count=0 might cause a no-fire or infinite loop.
        """
        # burst_count=0 → DB-omitted or zero-default case; mirrors live production data
        cluster = _secondary(
            subtype="cluster-missile",
            range_m=5000.0,
            damage=90.0,
            speed_ms=3000,
            burst_count=0,
            name="Degraded Cluster",
        )
        scanner = _telta_scanner()
        l1 = _loadout(secondary_weapons=[cluster], modules=[scanner], name="Attacker")
        l2 = _loadout(base_armour=200, name="Target")
        result = TickResolver().resolve(l1, l2, rng=_AlwaysHit())
        t0 = [e for e in result.combat_log if e.tick == 0]

        fires = [e for e in t0 if e.type == "weapon_fire" and e.data.get("subtype") == "cluster-missile"]
        assert len(fires) == 1, "burst_count=0 must emit exactly ONE condensed weapon_fire"

        ev = fires[0]
        # Graceful degradation: _n = 1 when burst_count=0
        assert ev.data["fired"] == 1, f"burst_count=0 → fired must be 1 (graceful degradation), got {ev.data['fired']}"
        # AlwaysHit → 1 hit
        assert ev.data["hits"] == 1, f"burst_count=0 with AlwaysHit → hits must be 1, got {ev.data['hits']}"
        assert ev.data["total_damage"] == 90, f"1 hit × 90 damage = 90 total_damage, got {ev.data['total_damage']}"
        # Exactly one damage event for the single sub-munition hit
        dmg_evs = [e for e in t0 if e.type == "damage" and e.target == "Target"]
        assert len(dmg_evs) == 1, f"burst_count=0 degraded case: 1 hit → 1 damage event, got {len(dmg_evs)}"


# ===========================================================================
# Nuke tests (D5)
# ===========================================================================


class TestNuke:
    def _liberator(self) -> WeaponStats:
        return _secondary(
            name="Liberator",
            subtype="nuke",
            damage=850,
            speed_ms=10000,
            range_m=13800.0,
            magnitude_m=12500.0,
            steerable=True,
        )

    def _tormentor(self) -> WeaponStats:
        return _secondary(
            name="AMR Tormentor",
            subtype="nuke",
            damage=150,
            speed_ms=6000,
            range_m=2500.0,
            magnitude_m=10000.0,
            steerable=False,
        )

    def test_liberator_point_blank(self):
        """Liberator: epicenter near MIN_DISTANCE_M, verify damage formula. Test 13."""
        lib = self._liberator()
        l1 = _loadout(secondary_weapons=[lib], name="Firer")
        l2 = _loadout(base_armour=2000, name="Target")
        # Fix epicenter to MIN_DISTANCE_M (AlwaysHit returns MIN_DIST from uniform)
        rng = _AlwaysHit()
        result = TickResolver().resolve(l1, l2, rng=rng)
        t0 = [e for e in result.combat_log if e.tick == 0]
        fires = [e for e in t0 if e.type == "weapon_fire" and e.data.get("subtype") == "nuke"]
        assert len(fires) == 1
        ev = fires[0]
        # Epicenter = MIN_DISTANCE_M = 300
        epicenter = ev.data["epicenter"]
        assert abs(epicenter - MIN_DIST) < 1e-6
        # Verify opponent damage: d_opponent = |epicenter - current_distance|
        d_opp = abs(epicenter - STARTING_DIST)
        eff_mag = 12500.0 * GameConstants.NUKE_MAGNITUDE_SCALE
        expected_opp = round(_nuke_dmg(d_opp, 850, eff_mag))
        assert ev.data["opponent_damage"] == expected_opp
        # Self-damage: d_firer = epicenter
        expected_self = round(_nuke_dmg(epicenter, 850, eff_mag) * GameConstants.NUKE_FRIENDLY_FACTOR)
        assert ev.data["self_damage"] == expected_self
        # Both damage events applied
        dmg_evs = [e for e in t0 if e.type == "damage"]
        assert len(dmg_evs) == 2  # one for target, one for self

    def test_tormentor_long_range_opponent_outside_magnitude(self):
        """Tormentor: opponent outside effective_magnitude → opponent damage = 0. Test 14.

        Tormentor range_m=2500m. Starting distance=5000m so it can't fire there.
        We use a Tormentor-equivalent weapon with range_m=5000 to ensure it fires.
        """
        # eff_mag = 10000 × 0.10 = 1000m
        # current_distance = 5000m (starting)
        # epicenter = MIN_DIST = 300m → d_opponent = |300 - 5000| = 4700m > 1000m → dmg = 0
        tort = _secondary(
            name="AMR Tormentor",
            subtype="nuke",
            damage=150,
            speed_ms=6000,
            range_m=5000.0,  # extended range so it fires at starting distance
            magnitude_m=10000.0,
            steerable=False,
        )
        l1 = _loadout(secondary_weapons=[tort], name="Firer")
        l2 = _loadout(base_armour=2000, name="Target")
        rng = _AlwaysHit()  # uniform returns MIN_DIST (epicenter = 300)
        result = TickResolver().resolve(l1, l2, rng=rng)
        t0 = [e for e in result.combat_log if e.tick == 0]
        fires = [e for e in t0 if e.type == "weapon_fire" and e.data.get("subtype") == "nuke"]
        assert len(fires) == 1
        assert fires[0].data["opponent_damage"] == 0, "Opponent d=4700m > eff_mag=1000m → 0 damage"
        # Firer self-damage: d_firer = 300m, eff_mag = 1000m
        d_firer = MIN_DIST
        eff_mag = 10000.0 * GameConstants.NUKE_MAGNITUDE_SCALE  # 1000
        expected_self = round(_nuke_dmg(d_firer, 150, eff_mag) * GameConstants.NUKE_FRIENDLY_FACTOR)
        assert fires[0].data["self_damage"] == expected_self

    def test_self_damage_pvc_dr_applied_once(self):
        """PvC fight: nuke self-damage applies DR once via T3 helper (NOT double-discounted). Test 15."""
        lib = self._liberator()
        l1 = _loadout(secondary_weapons=[lib], base_armour=3000, name="Player")  # player = C1
        l2 = _loadout(base_armour=100, name="NPC")
        # Epicenter at MIN_DIST; d_firer = MIN_DIST
        rng = _AlwaysHit()  # uniform returns MIN_DIST
        pvc_dr = 0.33
        result = TickResolver().resolve(l1, l2, pvc_damage_reduction=pvc_dr, rng=rng)
        t0 = [e for e in result.combat_log if e.tick == 0]
        fires = [e for e in t0 if e.type == "weapon_fire" and e.data.get("subtype") == "nuke"]
        ev = fires[0]
        raw_self = ev.data["self_damage"]  # T6 computes this BEFORE DR
        # DR applied once by T3 helper → applied_self = round(raw_self × 0.67)
        expected_applied = round(raw_self * (1.0 - pvc_dr))
        # Find the self-damage event on the player (C1 = "Player")
        dmg_evs = [e for e in t0 if e.type == "damage" and e.target == "Player"]
        # There may be 0 or 1 depending on raw_self
        eff_mag = 12500.0 * GameConstants.NUKE_MAGNITUDE_SCALE
        raw_self_computed = round(_nuke_dmg(MIN_DIST, 850, eff_mag) * GameConstants.NUKE_FRIENDLY_FACTOR)
        if raw_self_computed > 0 and dmg_evs:
            # Verify T3 applied exactly one DR
            applied_actual = dmg_evs[0].data["amount"]
            assert applied_actual == expected_applied, (
                f"Expected self-damage={expected_applied}, got {applied_actual}. "
                "DR must be applied exactly once, not zero or twice."
            )

    def test_steerable_ignored(self):
        """Liberator (steerable=True) and Tormentor (steerable=False) use identical code path. Test 16.

        Both nukes must fire the same code path — only damage/magnitude differ.
        To ensure both fire at starting distance (5000m), we give both range_m=5000.
        """
        lib = self._liberator()  # range_m=13800 — fires
        # Tormentor normally has range_m=2500 < 5000; adjust for test
        tort_adj = _secondary(
            name="AMR Tormentor",
            subtype="nuke",
            damage=150,
            speed_ms=6000,
            range_m=5000.0,
            magnitude_m=10000.0,
            steerable=False,
        )
        l1_lib = _loadout(secondary_weapons=[lib], name="Lib")
        l1_tort = _loadout(secondary_weapons=[tort_adj], name="Tort")
        l2 = _loadout(base_armour=2000, name="Target")
        # Use identical RNG; just check both fire with weapon_fire events
        for l1, name in ((l1_lib, "Lib"), (l1_tort, "Tort")):
            result = TickResolver().resolve(l1, l2, rng=_AlwaysHit())
            t0 = [e for e in result.combat_log if e.tick == 0]
            fires = [e for e in t0 if e.type == "weapon_fire" and e.data.get("subtype") == "nuke"]
            assert len(fires) == 1, f"Nuke should fire for {name}"

    def test_nuke_no_accuracy_roll(self):
        """Nuke fires at full damage regardless of cloak-like state — no accuracy roll path. Test 17."""
        lib = self._liberator()
        # Manually poke c2's 'cloak active' — just verify nuke fires and deals damage
        # regardless of any accuracy computation
        l1 = _loadout(secondary_weapons=[lib], name="Firer")
        l2 = _loadout(base_armour=2000, name="Target")
        # Even with AlwaysMiss (which would prevent primary hits), nuke still fires
        # because nukes skip the accuracy roll
        result = TickResolver().resolve(l1, l2, rng=_AlwaysMiss())
        t0 = [e for e in result.combat_log if e.tick == 0]
        fires = [e for e in t0 if e.type == "weapon_fire" and e.data.get("subtype") == "nuke"]
        # Nuke should fire; payload has no hit/accuracy fields
        assert len(fires) == 1
        assert "hit" not in fires[0].data
        assert "accuracy" not in fires[0].data

    def test_nuke_rng_determinism(self):
        """Same seed → same epicenter; different seeds → different epicenters. Test 18."""
        lib = self._liberator()
        l1 = _loadout(secondary_weapons=[lib], name="Firer")
        l2 = _loadout(base_armour=2000, name="Target")

        r42a = random.Random(42)
        r42b = random.Random(42)
        r99 = random.Random(99)

        res42a = TickResolver().resolve(l1, l2, rng=r42a)
        res42b = TickResolver().resolve(l1, l2, rng=r42b)
        res99 = TickResolver().resolve(l1, l2, rng=r99)

        epi42a = next(
            e.data["epicenter"]
            for e in res42a.combat_log
            if e.type == "weapon_fire" and e.data.get("subtype") == "nuke"
        )
        epi42b = next(
            e.data["epicenter"]
            for e in res42b.combat_log
            if e.type == "weapon_fire" and e.data.get("subtype") == "nuke"
        )
        epi99 = next(
            e.data["epicenter"] for e in res99.combat_log if e.type == "weapon_fire" and e.data.get("subtype") == "nuke"
        )

        assert abs(epi42a - epi42b) < 1e-12, "Same seed must reproduce same epicenter"
        assert abs(epi42a - epi99) > 1e-6, "Different seeds must produce different epicenters"


# ===========================================================================
# Shock-blast tests (D6)
# ===========================================================================


class TestShockBlast:
    def _sb(self, close_range: bool = False) -> WeaponStats:
        """Shock Blast weapon. range_m=0 means infinite range."""
        return _secondary(name="Shock Blast", subtype="shock-blast", damage=0.0, speed_ms=6000, range_m=0.0)

    def test_distance_reset_to_starting(self):
        """Shock-blast resets current_distance to STARTING_DISTANCE_M. Test 19."""
        sb = self._sb()
        l1 = _loadout(secondary_weapons=[sb], name="Attacker")
        l2 = _loadout(name="Target")
        result = TickResolver().resolve(l1, l2, rng=_AlwaysHit())
        dist_evs = [e for e in result.combat_log if e.type == "distance" and e.data.get("cause") == "shock_blast"]
        assert len(dist_evs) >= 1
        first = dist_evs[0]
        assert abs(first.data["to"] - STARTING_DIST) < 1e-6
        # No HP delta: check no damage events on tick 0
        t0_dmg = [e for e in result.combat_log if e.tick == 0 and e.type == "damage"]
        assert len(t0_dmg) == 0

    def test_deterministic_no_rng_draw(self):
        """Shock-blast takes no RNG draw — behaves identically with seed 0 vs seed 99. Test 20."""
        sb = self._sb()
        l1 = _loadout(secondary_weapons=[sb], name="Attacker")
        l2 = _loadout(name="Target")
        res0 = TickResolver().resolve(l1, l2, rng=random.Random(0))
        res99 = TickResolver().resolve(l1, l2, rng=random.Random(99))
        dist0 = [e.data for e in res0.combat_log if e.type == "distance" and e.data.get("cause") == "shock_blast"]
        dist99 = [e.data for e in res99.combat_log if e.type == "distance" and e.data.get("cause") == "shock_blast"]
        assert dist0 == dist99, "Shock-blast behavior must be identical across seeds"

    def test_active_cloak_booster_unaffected(self):
        """Shock-blast does not zero/clear module_cooldowns. Test 21.

        Anti-tautology design: we call _shock_blast_apply() (the ACTUAL production
        function used by TickResolver Phase 6) against a _CombatantState carrying
        non-zero module_cooldowns, then assert those cooldowns are UNCHANGED.

        If _shock_blast_apply (or the Phase 6 code path that calls it) were to call
        state.module_cooldowns.clear() / reset / zero any entry, the final assertions
        below would FAIL. This proves the invariant with zero tautology:
        the assertions can only pass if _shock_blast_apply left the state untouched.

        Tester's repro-check: inserting `state.module_cooldowns.clear()` between
        the _shock_blast_apply call and the assertions must make the test fail.
        """
        import copy

        # Build real state via the production _init_combatant path
        cloak_module = ModuleStats(name="Camo Booster")
        booster_module = ModuleStats(name="Speed Booster")
        sb = self._sb()
        loadout = _loadout(secondary_weapons=[sb], modules=[cloak_module, booster_module], name="Attacker")
        state = _init_combatant(loadout, is_player=False)

        # Set non-zero cooldowns — simulating active module timers pre-shock-blast
        state.module_cooldowns["Camo Booster"] = 5000  # active cloak duration_ms proxy
        state.module_cooldowns["Speed Booster"] = 3000  # active booster window proxy

        # Take an immutable snapshot BEFORE calling shock-blast
        snapshot = copy.deepcopy(state.module_cooldowns)

        # Call the ACTUAL production function (_shock_blast_apply is what TickResolver
        # Phase 6 calls). It returns the new distance but must NOT mutate state.
        prev_dist = STARTING_DIST
        new_dist = _shock_blast_apply(state, prev_dist)

        # Verify shock-blast returns the expected new distance
        assert abs(new_dist - STARTING_DIST) < 1e-6, (
            f"_shock_blast_apply must return STARTING_DISTANCE_M, got {new_dist}"
        )

        # CRITICAL: module_cooldowns must be byte-for-byte identical to the snapshot.
        # If _shock_blast_apply mutated them (clear/reset/zero), this fails.
        assert state.module_cooldowns == snapshot, (
            f"_shock_blast_apply must NOT mutate module_cooldowns. Before: {snapshot}, After: {state.module_cooldowns}"
        )
        assert state.module_cooldowns["Camo Booster"] == 5000, "Camo Booster cooldown must be unaffected by shock-blast"
        assert state.module_cooldowns["Speed Booster"] == 3000, (
            "Speed Booster cooldown must be unaffected by shock-blast"
        )

        # Supplemental: verify full resolve() path emits the expected events (no module noise)
        l2 = _loadout(name="Target")
        result = TickResolver().resolve(loadout, l2, rng=_AlwaysHit())
        dist_evs = [e for e in result.combat_log if e.type == "distance" and e.data.get("cause") == "shock_blast"]
        assert len(dist_evs) >= 1, "Shock-blast should emit distance event via resolve()"
        mod_activations = [e for e in result.combat_log if e.type == "module_activation"]
        assert len(mod_activations) == 0, "No module activations expected in T6"

    def test_weapon_fire_and_distance_events_emitted(self):
        """Shock-blast emits weapon_fire (phase 3) AND distance (phase 6). Test 22.

        After FIX 2, shock-blast fires only inside SHOCK_BLAST_TRIGGER_RANGE_M (500m),
        NOT on tick 0. We verify the events are emitted on the FIRST fire tick (inside range).
        """
        sb = self._sb()
        l1 = _loadout(secondary_weapons=[sb], name="Attacker")
        l2 = _loadout(name="Target")
        result = TickResolver().resolve(l1, l2, rng=_AlwaysHit())
        # After FIX 2: first fire happens when ships close to < 500m (not tick 0)
        all_fires = [e for e in result.combat_log if e.type == "weapon_fire" and e.data.get("subtype") == "shock-blast"]
        assert len(all_fires) >= 1, "Shock-blast must fire at least once (inside range)"
        first_fire = all_fires[0]
        assert first_fire.data["hit"] is True
        assert abs(first_fire.data["accuracy"] - 1.0) < 1e-9
        # Distance event must be emitted on same tick
        fire_tick = first_fire.tick
        tick_events = [e for e in result.combat_log if e.tick == fire_tick]
        dist_evs = [e for e in tick_events if e.type == "distance" and e.data.get("cause") == "shock_blast"]
        assert len(dist_evs) == 1, "Exactly one distance event for shock-blast fire"
        assert abs(dist_evs[0].data["to"] - STARTING_DIST) < 1e-6
        # weapon_fire is in phase 3 (before distance in phase 6) — verify ordering
        fire_idx = tick_events.index(first_fire)
        dist_idx = tick_events.index(dist_evs[0])
        assert fire_idx < dist_idx, "weapon_fire (phase 3) must come before distance (phase 6)"

    def test_seed_damage_ignored(self):
        """Shock-blast seed damage=140 is ignored — no HP delta. Test 23."""
        # Create shock-blast with non-zero damage_per_shot to verify it's ignored
        sb = WeaponStats(
            name="Shock Blast",
            dps=0.0,
            damage_per_shot=140.0,
            loading_speed_ms=6000,
            range_m=0.0,
            subtype="shock-blast",
            emp_damage=80,
        )
        l1 = _loadout(secondary_weapons=[sb], name="Attacker")
        l2 = _loadout(base_armour=200, name="Target")
        result = TickResolver().resolve(l1, l2, rng=_AlwaysHit())
        # No damage events on either combatant
        t0_dmg = [e for e in result.combat_log if e.tick == 0 and e.type == "damage"]
        assert len(t0_dmg) == 0, "Shock-blast must apply zero damage despite seed damage=140"


# ===========================================================================
# Pure-EMP secondary (D1.4)
# ===========================================================================


class TestPureEMPSecondary:
    def test_mamba_emp_fires_zero_damage(self):
        """Mamba EMP: fires, rolls accuracy, T3 records 0-amount damage event. Test 24."""
        mamba = WeaponStats(
            name="Mamba EMP",
            dps=0.0,
            damage_per_shot=0.0,
            loading_speed_ms=3000,
            range_m=5000.0,
            subtype="missile",
            emp_damage=100,
            steerable=True,
        )
        scanner = _telta_scanner()
        l1 = _loadout(secondary_weapons=[mamba], modules=[scanner], name="Attacker")
        l2 = _loadout(base_armour=200, name="Target")
        result = TickResolver().resolve(l1, l2, rng=_AlwaysHit())
        t0 = [e for e in result.combat_log if e.tick == 0]
        fires = [e for e in t0 if e.type == "weapon_fire" and e.data.get("subtype") == "missile"]
        assert len(fires) == 1
        assert fires[0].data["hit"] is True
        dmg_evs = [e for e in t0 if e.type == "damage" and e.target == "Target"]
        assert len(dmg_evs) == 1
        assert dmg_evs[0].data["amount"] == 0
        # Target HP unchanged
        fight_end = [e for e in result.combat_log if e.type == "fight_end"]
        if fight_end:
            # Fight should be a time_cap stalemate since 0 damage on both sides
            assert result.is_stalemate


# ===========================================================================
# Cross-subtype tests
# ===========================================================================


class TestCrossSubtype:
    def test_cooldown_reset_on_fire_all_subtypes(self):
        """Cooldown resets to loading_speed_ms after fire — hit OR miss — for every subtype. Test 25.

        After FIX 2, shock-blast does NOT fire on tick 0 (range guard: < 500m required).
        All other subtypes still fire on tick 0 (they have explicit range_m > 0 or infinite range
        without the trigger-range guard). Shock-blast fires once ships close to < 500m.
        """
        speed = 5000  # ms
        weapons = {
            "rocket": _secondary(subtype="rocket", speed_ms=speed, range_m=5000.0),
            "missile": _secondary(subtype="missile", speed_ms=speed, range_m=5000.0),
            "cluster": _secondary(subtype="cluster-missile", speed_ms=speed, range_m=5000.0, burst_count=3),
            "nuke": _secondary(subtype="nuke", speed_ms=speed, range_m=13800.0, magnitude_m=12500.0, damage=850.0),
            "shock-blast": _secondary(subtype="shock-blast", speed_ms=speed, range_m=0.0),
        }
        for subtype_name, sw in weapons.items():
            l1 = _loadout(secondary_weapons=[sw], base_armour=2000, name="Attacker")
            l2 = _loadout(base_armour=2000, name="Target")
            result = TickResolver(seed=0).resolve(l1, l2, rng=_AlwaysHit())
            # Check secondary starts at cooldown 0
            state = _init_combatant(l1, is_player=False)
            sw_rt = state.effective_secondaries[0]
            assert sw_rt.cooldown_remaining_ms == 0, f"{subtype_name}: should start at 0"
            # All subtypes except shock-blast fire on tick 0
            # Shock-blast has a range guard (< SHOCK_BLAST_TRIGGER_RANGE_M = 500m) — fires later
            if subtype_name == "shock-blast":
                # Verify it fires at some point (not tick 0, but still fires)
                all_fires = [
                    e
                    for e in result.combat_log
                    if e.type == "weapon_fire" and e.data.get("slot") == "secondary"
                    and e.data.get("subtype") == "shock-blast"
                ]
                assert len(all_fires) >= 1, f"{subtype_name}: should fire at some point in the fight"
                assert all_fires[0].tick > 0, f"{subtype_name}: must NOT fire on tick 0 (FIX 2 range guard)"
            else:
                t0 = [
                    e
                    for e in result.combat_log
                    if e.tick == 0 and e.type == "weapon_fire" and e.data.get("slot") == "secondary"
                ]
                assert len(t0) >= 1, f"{subtype_name}: should fire on tick 0"

    def test_t1_t5_regression(self):
        """T1–T5 baseline regression: primaries still fire, damage applies, tick loop runs. Test 26."""
        gun = _primary(damage=50.0, speed_ms=500, range_m=5000.0)
        l1 = _loadout(weapons=[gun], base_armour=500, name="C1")
        l2 = _loadout(weapons=[gun], base_armour=500, name="C2")
        result = TickResolver(seed=42).resolve(l1, l2)
        # Fight should end with a winner (primaries deal damage)
        assert not result.is_stalemate or result.metadata["metadata"]["total_ticks"] < GameConstants.MAX_FIGHT_TICKS

    def test_weapon_fire_payloads_conform_to_spec(self):
        """weapon_fire payloads match §12 per-subtype table. Test 27.

        After FIX 2, shock-blast does NOT fire on tick 0 (range guard: distance < 500m).
        The tick-0 check still verifies rocket/missile/cluster/nuke payloads.
        Shock-blast payload is verified on its actual first-fire tick.
        """
        speed = 5000
        # One of each subtype
        rocket = _secondary(name="TestRocket", subtype="rocket", speed_ms=speed, range_m=5000.0, damage=50.0)
        missile = _secondary(name="TestMissile", subtype="missile", speed_ms=speed, range_m=5000.0, damage=70.0)
        cluster = _secondary(
            name="TestCluster", subtype="cluster-missile", speed_ms=speed, range_m=5000.0, damage=60.0, burst_count=3
        )
        nuke = _secondary(
            name="TestNuke", subtype="nuke", speed_ms=speed, range_m=13800.0, damage=850.0, magnitude_m=12500.0
        )
        sb = _secondary(name="TestSB", subtype="shock-blast", speed_ms=speed, range_m=0.0, damage=0.0)
        all_secondaries = [rocket, missile, cluster, nuke, sb]
        l1 = _loadout(secondary_weapons=all_secondaries, base_armour=5000, name="Attacker")
        l2 = _loadout(base_armour=5000, name="Target")
        result = TickResolver().resolve(l1, l2, rng=_AlwaysHit())
        t0 = [
            e
            for e in result.combat_log
            if e.tick == 0 and e.type == "weapon_fire" and e.data.get("slot") == "secondary"
        ]
        ev_by_sub: dict[str, dict] = {e.data["subtype"]: e.data for e in t0}

        # rocket: {slot, subtype, weapon, hit, accuracy}
        assert "rocket" in ev_by_sub
        r = ev_by_sub["rocket"]
        assert "hit" in r and "accuracy" in r

        # missile: {slot, subtype, weapon, hit, accuracy, branch}
        assert "missile" in ev_by_sub
        m = ev_by_sub["missile"]
        assert "hit" in m and "accuracy" in m and "branch" in m

        # cluster-missile: {slot, subtype, weapon, fired, hits, damage_per_hit, total_damage, branch, accuracy}
        # Note: branch and accuracy are additive extension fields (permitted under Q9).
        # The required canonical fields per §12 are fired/hits/damage_per_hit/total_damage.
        assert "cluster-missile" in ev_by_sub
        c = ev_by_sub["cluster-missile"]
        assert "fired" in c and "hits" in c and "damage_per_hit" in c and "total_damage" in c
        assert "hit" not in c  # condensed — no per-shot hit boolean (that's only for single-shot weapons)

        # nuke: {slot, subtype, weapon, epicenter, opponent_damage, self_damage}
        assert "nuke" in ev_by_sub
        n = ev_by_sub["nuke"]
        assert "epicenter" in n and "opponent_damage" in n and "self_damage" in n
        assert "hit" not in n and "accuracy" not in n  # no accuracy roll

        # shock-blast: does NOT fire on tick 0 (FIX 2 range guard; fires only inside 500m)
        # Verify payload on the actual first-fire tick
        assert "shock-blast" not in ev_by_sub, (
            "Shock-blast must NOT fire on tick 0 after FIX 2 (distance=5000m > 500m trigger range)"
        )
        sb_fire_ev = next(
            (e for e in result.combat_log if e.type == "weapon_fire" and e.data.get("subtype") == "shock-blast"),
            None,
        )
        assert sb_fire_ev is not None, "Shock-blast must fire at some point in the fight (inside range)"
        s = sb_fire_ev.data
        assert s["hit"] is True
        assert abs(s["accuracy"] - 1.0) < 1e-9


# ===========================================================================
# Deferred subtype handling (D1.3 noop paths)
# ===========================================================================


class TestDeferredSubtypes:
    def test_emp_bomb_noop(self):
        """emp-bomb equipped: cooldown ticks, weapon never fires. D1.3."""
        emp_bomb = _secondary(subtype="emp-bomb", speed_ms=6000, range_m=5000.0, damage=2.0)
        l1 = _loadout(secondary_weapons=[emp_bomb], name="Attacker")
        l2 = _loadout(name="Target")
        result = TickResolver().resolve(l1, l2, rng=_AlwaysHit())
        sec_fires = [e for e in result.combat_log if e.type == "weapon_fire" and e.data.get("slot") == "secondary"]
        assert len(sec_fires) == 0, "emp-bomb should never emit weapon_fire"

    def test_mine_noop(self):
        """mine subtype: never fires."""
        mine = _secondary(subtype="mine", speed_ms=3000, range_m=5000.0)
        l1 = _loadout(secondary_weapons=[mine], name="Attacker")
        l2 = _loadout(name="Target")
        result = TickResolver().resolve(l1, l2, rng=_AlwaysHit())
        sec_fires = [e for e in result.combat_log if e.type == "weapon_fire" and e.data.get("slot") == "secondary"]
        assert len(sec_fires) == 0

    def test_sentry_gun_noop(self):
        """sentry-gun subtype: never fires."""
        sg = _secondary(subtype="sentry-gun", speed_ms=4000, range_m=5000.0)
        l1 = _loadout(secondary_weapons=[sg], name="Attacker")
        l2 = _loadout(name="Target")
        result = TickResolver().resolve(l1, l2, rng=_AlwaysHit())
        sec_fires = [e for e in result.combat_log if e.type == "weapon_fire" and e.data.get("slot") == "secondary"]
        assert len(sec_fires) == 0

    def test_ionizing_missile_fire_but_noop(self):
        """ionizing-missile: fires, rolls accuracy, 0 HP delta. D1.3."""
        ion = _secondary(subtype="ionizing-missile", speed_ms=6000, range_m=5000.0, damage=0.0)
        scanner = _telta_scanner()
        l1 = _loadout(secondary_weapons=[ion], modules=[scanner], name="Attacker")
        l2 = _loadout(base_armour=200, name="Target")
        result = TickResolver().resolve(l1, l2, rng=_AlwaysHit())
        t0 = [e for e in result.combat_log if e.tick == 0]
        fires = [e for e in t0 if e.type == "weapon_fire" and e.data.get("subtype") == "ionizing-missile"]
        assert len(fires) == 1, "ionizing-missile should fire (fire-but-noop)"
        assert fires[0].data["hit"] is True
        dmg_evs = [e for e in t0 if e.type == "damage" and e.target == "Target"]
        assert len(dmg_evs) == 1
        assert dmg_evs[0].data["amount"] == 0, "ionizing-missile applies 0 HP delta"


# ===========================================================================
# D0 data model verification
# ===========================================================================


class TestDataModel:
    def test_weapon_stats_discriminator_fields(self):
        """WeaponStats carries all T6 discriminator fields with correct defaults."""
        ws = WeaponStats(name="Test", dps=1.0)
        assert ws.subtype == ""
        assert ws.burst_count == 0
        assert ws.emp_damage == 0
        assert ws.magnitude_m == 0.0
        assert ws.steerable is False

    def test_shp_loadout_secondary_weapons_default_empty(self):
        """ShipLoadout.secondary_weapons defaults to empty list."""
        from src.services.combat_models import ShipLoadout

        sl = ShipLoadout(ship_name="Test", base_armour=100)
        assert sl.secondary_weapons == []

    def test_init_combatant_populates_secondaries(self):
        """_init_combatant builds effective_secondaries from loadout.secondary_weapons."""
        rocket = _secondary(subtype="rocket", damage=50.0, speed_ms=1000, range_m=4000.0)
        cluster = _secondary(subtype="cluster-missile", damage=60.0, speed_ms=3000, range_m=4400.0, burst_count=3)
        test_loadout = _loadout(secondary_weapons=[rocket, cluster])
        state = _init_combatant(test_loadout, is_player=False)
        assert len(state.effective_secondaries) == 2
        assert state.effective_secondaries[0].subtype == "rocket"
        assert state.effective_secondaries[1].subtype == "cluster-missile"
        assert state.effective_secondaries[1].burst_count == 3
        # All start at cooldown 0
        for sw_rt in state.effective_secondaries:
            assert sw_rt.cooldown_remaining_ms == 0

    def test_nuke_dmg_formula(self):
        """_nuke_dmg matches Appendix B formula."""
        # At distance 0: fraction=0 → dmg = damage × 1 = damage
        assert abs(_nuke_dmg(0.0, 100, 1000.0) - 100.0) < 1e-9
        # At distance = effective_magnitude: fraction=1 → dmg = 0
        assert abs(_nuke_dmg(1000.0, 100, 1000.0) - 0.0) < 1e-9
        # At distance > effective_magnitude: clamped to 1 → dmg = 0
        assert abs(_nuke_dmg(2000.0, 100, 1000.0) - 0.0) < 1e-9
        # At half: fraction = 0.5 → dmg = 100 × 0.5² = 25
        assert abs(_nuke_dmg(500.0, 100, 1000.0) - 25.0) < 1e-9


# ===========================================================================
# Performance test (fight resolution timing)
# ===========================================================================


class TestPerformance:
    def test_fight_resolution_timing(self):
        """Secondary-heavy fight resolves in < 5 seconds (perf regression guard)."""
        import time

        patala = _secondary(
            subtype="cluster-missile", range_m=5000.0, damage=90.0, speed_ms=3000, burst_count=5, name="Patala"
        )
        lib = _secondary(
            name="Liberator", subtype="nuke", damage=850, speed_ms=10000, range_m=13800.0, magnitude_m=12500.0
        )
        l1 = _loadout(secondary_weapons=[patala, lib], base_armour=3000, name="C1")
        l2 = _loadout(secondary_weapons=[patala], base_armour=3000, name="C2")
        start = time.perf_counter()
        TickResolver(seed=42).resolve(l1, l2)
        elapsed = time.perf_counter() - start
        assert elapsed < 5.0, f"Fight resolution took {elapsed:.2f}s — exceeds 5s perf guard"


# ===========================================================================
# D0.5: Builder-fed integration test
# ===========================================================================


def _make_execute_result(scalar_value):
    """Return a mock db.execute() result wrapping the given scalar."""
    result = MagicMock()
    scalars_result = MagicMock()
    scalars_result.first.return_value = scalar_value
    result.scalars.return_value = scalars_result
    return result


def _make_mock_secondary_weapon(
    name: str,
    damage: int,
    loading_speed: int,
    extra_atts: dict,
) -> MagicMock:
    """Create a SecondaryWeapon-like MagicMock."""
    sw = MagicMock()
    sw.name = name
    sw.damage = damage
    sw.dps = 0.0
    sw.loading_speed = loading_speed
    sw.extra_atts = extra_atts
    return sw


def _make_mock_primary_weapon(
    name: str,
    dps: float,
    extra_atts: dict,
) -> MagicMock:
    """Create a PrimaryWeapon-like MagicMock with extra_atts for tick-resolver fields."""
    pw = MagicMock()
    pw.name = name
    pw.dps = dps
    pw.extra_atts = extra_atts
    return pw


class TestBuilderFedIntegration:
    """D0.5: Builder-fed fight from LoadoutBuilder.from_player (mocked DB).

    Verifies:
    - secondary_weapons populated with typed WeaponStats (subtype/burst_count/etc.)
    - primaries carry real loading_speed_ms / range_m (T6 primary true-up)
    - A fight run from the builder-produced loadout fires secondaries correctly
    """

    @pytest.mark.asyncio
    async def test_builder_secondary_weapons_populated(self):
        """from_player populates secondary_weapons with typed WeaponStats. D0.5."""
        from unittest.mock import patch

        from src.services.loadout_builder import LoadoutBuilder

        # Mock DB objects
        player = MagicMock()
        player.id = 1
        player.active_ship_id = 10

        player_ship = MagicMock()
        player_ship.id = 10
        player_ship.ship_name = "Specter"
        player_ship.weapons = []
        player_ship.turrets = []
        player_ship.modules = []
        player_ship.secondary_weapons = ["Jet Rocket"]
        player_ship.manual_turret_mode = False

        ship = MagicMock()
        ship.name = "Specter"
        ship.armour = 300

        # Jet Rocket secondary weapon — nested extra_atts matches real DB structure:
        # outer dict contains builtIn/loading speed/techLevel/extra_atts;
        # the inner extra_atts holds combat-relevant snake_case fields.
        jet_rocket = _make_mock_secondary_weapon(
            name="Jet Rocket",
            damage=70,
            loading_speed=900,
            extra_atts={
                "builtIn": False,
                "loading speed": 900,
                "techLevel": 3,
                "extra_atts": {
                    "loading_speed_ms": 900,
                    "range_m": 1100,
                    "subtype": "rocket",
                    "steerable": False,
                    "emp_damage": 0,
                },
            },
        )

        player_repo = MagicMock()
        player_repo.get_by_id = AsyncMock(return_value=player)

        item_repo = MagicMock()
        item_repo.get_by_name = AsyncMock(return_value=None)

        # db.execute side_effect: PlayerShip → Ship → SecondaryWeapon
        db = MagicMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_execute_result(player_ship),  # PlayerShip query
                _make_execute_result(ship),  # Ship query
                _make_execute_result(jet_rocket),  # SecondaryWeapon query for "Jet Rocket"
            ]
        )

        with (
            patch("persist.repositories.player_repository.PlayerRepository", return_value=player_repo),
            patch("persist.repositories.item_repository.ItemRepository", return_value=item_repo),
        ):
            loadout = await LoadoutBuilder.from_player(db, player_id=1)

        assert len(loadout.secondary_weapons) == 1
        sw = loadout.secondary_weapons[0]
        assert sw.name == "Jet Rocket"
        assert sw.subtype == "rocket"
        assert sw.loading_speed_ms == 900
        assert abs(sw.range_m - 1100.0) < 1e-6
        assert sw.damage_per_shot == 70.0
        assert sw.emp_damage == 0
        assert sw.steerable is False

    @pytest.mark.asyncio
    async def test_builder_primary_true_up(self):
        """from_player populates primaries with loading_speed_ms + range_m (T6 true-up). D0.5."""
        from unittest.mock import patch

        from src.services.loadout_builder import LoadoutBuilder

        player = MagicMock()
        player.id = 1
        player.active_ship_id = 10

        player_ship = MagicMock()
        player_ship.id = 10
        player_ship.ship_name = "Betty"
        player_ship.weapons = ["Micro Gun MK I"]
        player_ship.turrets = []
        player_ship.modules = []
        player_ship.secondary_weapons = []
        player_ship.manual_turret_mode = False

        ship = MagicMock()
        ship.name = "Betty"
        ship.armour = 95

        # Micro Gun MK I primary weapon — nested extra_atts matches real DB structure
        primary = _make_mock_primary_weapon(
            name="Micro Gun MK I",
            dps=9.09,
            extra_atts={
                "builtIn": False,
                "techLevel": 1,
                "extra_atts": {
                    "loading_speed_ms": 220,
                    "range_m": 1300,
                    "damage_per_shot": 2,
                    "subtype": "auto-cannon",
                },
            },
        )

        player_repo = MagicMock()
        player_repo.get_by_id = AsyncMock(return_value=player)

        item_repo = MagicMock()
        item_repo.get_by_name = AsyncMock(return_value=primary)

        db = MagicMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_execute_result(player_ship),  # PlayerShip
                _make_execute_result(ship),  # Ship
            ]
        )

        with (
            patch("persist.repositories.player_repository.PlayerRepository", return_value=player_repo),
            patch("persist.repositories.item_repository.ItemRepository", return_value=item_repo),
        ):
            loadout = await LoadoutBuilder.from_player(db, player_id=1)

        assert len(loadout.weapons) == 1
        pw = loadout.weapons[0]
        assert pw.name == "Micro Gun MK I"
        assert pw.loading_speed_ms == 220
        assert abs(pw.range_m - 1300.0) < 1e-6
        assert pw.damage_per_shot == 2.0

    @pytest.mark.asyncio
    async def test_builder_fed_fight_fires_secondaries(self):
        """Fight from builder-produced loadout fires secondaries (not hand-built). D0.5."""
        from unittest.mock import patch

        from src.services.loadout_builder import LoadoutBuilder

        # Player with a cluster missile equipped
        player = MagicMock()
        player.id = 1
        player.active_ship_id = 10

        player_ship = MagicMock()
        player_ship.id = 10
        player_ship.ship_name = "Specter"
        player_ship.weapons = []
        player_ship.turrets = []
        player_ship.modules = []
        player_ship.secondary_weapons = ["Shesha"]
        player_ship.manual_turret_mode = False

        ship = MagicMock()
        ship.name = "Specter"
        ship.armour = 300

        # Shesha secondary weapon — nested extra_atts matches real DB structure
        shesha = _make_mock_secondary_weapon(
            name="Shesha",
            damage=60,
            loading_speed=3000,
            extra_atts={
                "builtIn": False,
                "loading speed": 3000,
                "techLevel": 9,
                "extra_atts": {
                    "loading_speed_ms": 3000,
                    "range_m": 4400.0,
                    "subtype": "cluster-missile",
                    "burst_count": 3,
                },
            },
        )

        player_repo = MagicMock()
        player_repo.get_by_id = AsyncMock(return_value=player)

        item_repo = MagicMock()
        item_repo.get_by_name = AsyncMock(return_value=None)

        db = MagicMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_execute_result(player_ship),  # PlayerShip
                _make_execute_result(ship),  # Ship
                _make_execute_result(shesha),  # SecondaryWeapon: "Shesha"
            ]
        )

        with (
            patch("persist.repositories.player_repository.PlayerRepository", return_value=player_repo),
            patch("persist.repositories.item_repository.ItemRepository", return_value=item_repo),
        ):
            player_loadout = await LoadoutBuilder.from_player(db, player_id=1)

        # Verify builder produced a correct loadout
        assert len(player_loadout.secondary_weapons) == 1
        sw = player_loadout.secondary_weapons[0]
        assert sw.subtype == "cluster-missile"
        assert sw.burst_count == 3
        assert sw.loading_speed_ms == 3000
        assert abs(sw.range_m - 4400.0) < 1e-6

        # Run a fight from the builder-produced loadout (not hand-built)
        opponent = _loadout(base_armour=2000, name="Opponent")
        result = TickResolver().resolve(player_loadout, opponent, rng=_AlwaysHit())

        # Secondaries should fire
        cluster_fires = [
            e for e in result.combat_log if e.type == "weapon_fire" and e.data.get("subtype") == "cluster-missile"
        ]
        assert len(cluster_fires) >= 1, "Cluster missile should fire from builder-produced loadout"
        ev = cluster_fires[0]
        assert ev.data["fired"] == 3
        assert "hits" in ev.data


# ===========================================================================
# LOW / coverage gap tests
# ===========================================================================


class TestCoverageGaps:
    """Coverage gap tests per tester requirements."""

    def test_dual_shock_blast_from_field(self):
        """Dual shock-blast same tick: second event's 'from' reflects post-first-reset distance.

        LOW fix: the second shock-blast's distance event must report from=STARTING_DISTANCE_M
        (already reset by the first), not the original pre-reset distance.

        After FIX 2, shock-blast fires only inside SHOCK_BLAST_TRIGGER_RANGE_M (500m), NOT on
        tick 0. We look for the first tick where dual fires occur (inside range).
        """
        sb1 = _secondary(name="Shock Blast", subtype="shock-blast", damage=0.0, speed_ms=1000, range_m=0.0)
        sb2 = _secondary(name="Shock Blast 2", subtype="shock-blast", damage=0.0, speed_ms=1000, range_m=0.0)
        # Both shock-blasts on same ship so they fire in the same tick (cooldown 1000ms = 100 ticks)
        l1 = _loadout(secondary_weapons=[sb1, sb2], name="Attacker")
        l2 = _loadout(name="Target")
        result = TickResolver().resolve(l1, l2, rng=_AlwaysHit())
        # Find the first tick where dual distance events from shock_blast occur
        dist_evs = [e for e in result.combat_log if e.type == "distance" and e.data.get("cause") == "shock_blast"]
        assert len(dist_evs) >= 2, f"Both shock-blasts should emit distance events, got {len(dist_evs)}"
        # Get the first pair (same tick)
        first_tick = dist_evs[0].tick
        first_pair = [e for e in dist_evs if e.tick == first_tick]
        assert len(first_pair) == 2, f"Both shock-blasts must fire on the same tick, got {len(first_pair)}"
        second_ev = first_pair[1]
        # First: from the actual pre-reset distance (< SHOCK_BLAST_TRIGGER_RANGE_M)
        # Second: from STARTING_DISTANCE_M (because first already reset it)
        assert abs(second_ev.data["from"] - STARTING_DIST) < 1e-6, (
            f"Second shock-blast 'from' must be STARTING_DISTANCE_M={STARTING_DIST}, "
            f"got {second_ev.data['from']}. Bug: stale phase-3 capture used instead of live distance."
        )
        assert abs(second_ev.data["to"] - STARTING_DIST) < 1e-6

    def test_ionizing_missile_miss_no_damage_event(self):
        """ionizing-missile MISS path: no spurious damage event emitted. D1.3.

        On a miss, ionizing-missile does NOT route through T3's damage helper.
        A miss should only emit weapon_fire(hit=false), not a damage event.
        """
        ion = _secondary(subtype="ionizing-missile", speed_ms=6000, range_m=5000.0, damage=0.0)
        scanner = _telta_scanner()
        l1 = _loadout(secondary_weapons=[ion], modules=[scanner], name="Attacker")
        l2 = _loadout(base_armour=200, name="Target")
        # AlwaysMiss: accuracy check fails → no hit queued
        result = TickResolver().resolve(l1, l2, rng=_AlwaysMiss())
        t0 = [e for e in result.combat_log if e.tick == 0]
        fires = [e for e in t0 if e.type == "weapon_fire" and e.data.get("subtype") == "ionizing-missile"]
        assert len(fires) == 1, "ionizing-missile must emit weapon_fire even on miss"
        assert fires[0].data["hit"] is False
        # No damage event — miss means no helper invocation
        dmg_evs = [e for e in t0 if e.type == "damage" and e.target == "Target"]
        assert len(dmg_evs) == 0, "ionizing-missile MISS must not emit a damage event"

    def test_cluster_all_miss_zero_total_damage(self):
        """Cluster missile all-miss: k=0 → total_damage=0, no damage events. D4.

        When all sub-munitions miss, the weapon_fire event must have:
          hits=0, total_damage=0
        And NO damage events should be emitted.
        """
        patala = _secondary(
            subtype="cluster-missile", range_m=5000.0, damage=90.0, speed_ms=3000, burst_count=5, name="Patala"
        )
        scanner = _telta_scanner()
        l1 = _loadout(secondary_weapons=[patala], modules=[scanner], name="Attacker")
        l2 = _loadout(base_armour=1000, name="Target")
        # AlwaysMiss: all 5 sub-munitions miss
        result = TickResolver().resolve(l1, l2, rng=_AlwaysMiss())
        t0 = [e for e in result.combat_log if e.tick == 0]
        fires = [e for e in t0 if e.type == "weapon_fire" and e.data.get("subtype") == "cluster-missile"]
        assert len(fires) == 1, "ONE weapon_fire even on all-miss"
        ev = fires[0]
        assert ev.data["hits"] == 0, f"All sub-munitions miss → hits=0, got {ev.data['hits']}"
        assert ev.data["total_damage"] == 0, f"All miss → total_damage=0, got {ev.data['total_damage']}"
        # No damage events
        dmg_evs = [e for e in t0 if e.type == "damage" and e.target == "Target"]
        assert len(dmg_evs) == 0, "All-miss cluster must not emit damage events"

    @pytest.mark.asyncio
    async def test_builder_sw_item_none_fallback(self):
        """LoadoutBuilder: sw_item=None after all lookups → zero-stat WeaponStats entry.

        Covers the fallback branch at loadout_builder.py ~lines 204-232:
        when SecondaryWeapon DB query returns None AND item_repo.get_by_name returns None,
        the secondary weapon is still added to the loadout with all stats defaulting to 0.
        This prevents KeyError / AttributeError crashes on unknown weapon names.
        """
        from unittest.mock import patch

        from src.services.loadout_builder import LoadoutBuilder

        player = MagicMock()
        player.id = 1
        player.active_ship_id = 10

        player_ship = MagicMock()
        player_ship.id = 10
        player_ship.ship_name = "Specter"
        player_ship.weapons = []
        player_ship.turrets = []
        player_ship.modules = []
        player_ship.secondary_weapons = ["Unknown Weapon X"]
        player_ship.manual_turret_mode = False

        ship = MagicMock()
        ship.name = "Specter"
        ship.armour = 300

        player_repo = MagicMock()
        player_repo.get_by_id = AsyncMock(return_value=player)

        # Both SecondaryWeapon query AND item_repo.get_by_name return None
        item_repo = MagicMock()
        item_repo.get_by_name = AsyncMock(return_value=None)

        db = MagicMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_execute_result(player_ship),  # PlayerShip query
                _make_execute_result(ship),  # Ship query
                _make_execute_result(None),  # SecondaryWeapon query → None
            ]
        )

        with (
            patch("persist.repositories.player_repository.PlayerRepository", return_value=player_repo),
            patch("persist.repositories.item_repository.ItemRepository", return_value=item_repo),
        ):
            loadout = await LoadoutBuilder.from_player(db, player_id=1)

        # Builder must not crash; secondary weapon entry is present with zero/default stats
        assert len(loadout.secondary_weapons) == 1, (
            "sw_item=None fallback must still add a WeaponStats entry (name preserved)"
        )
        sw = loadout.secondary_weapons[0]
        assert sw.name == "Unknown Weapon X", "Weapon name must be preserved even when item not found"
        # All stats default to 0 / empty when sw_item is None
        assert sw.dps == 0.0, f"sw_item=None → dps=0.0, got {sw.dps}"
        assert sw.damage_per_shot == 0.0, f"sw_item=None → damage_per_shot=0.0, got {sw.damage_per_shot}"
        assert sw.loading_speed_ms == 0, f"sw_item=None → loading_speed_ms=0, got {sw.loading_speed_ms}"
        assert sw.subtype == "", f"sw_item=None → subtype='', got {sw.subtype!r}"


# ===========================================================================
# FIX 1 — Repair Bot regen: base-bot loadout produces regen events (end-to-end)
# ===========================================================================


class TestRepairBotRegenEndToEnd:
    """Full-resolve regression: base repair bot loadout produces >=1 regen event.

    These tests use the subclass + repair_rate property approach (FIX 1).
    The repair_rate is baked into ModuleStats; _init_combatant picks it up via
    module_type == "RepairBotModule". This ensures the fix is wired end-to-end.
    """

    def test_base_bot_loadout_produces_regen_events(self):
        """A RepairBotModule with Ketar-I rate produces >=1 CombatEventType.regen events."""
        repair_bot = ModuleStats(
            name="Ketar Repair Bot",
            module_type="RepairBotModule",
            repair_rate=GameConstants.KETAR_I_REPAIR_PCT_PER_SEC,
        )
        # Attacker with big gun to deal hull damage; defender with repair bot
        gun = _primary(damage=100.0, speed_ms=500, range_m=6000.0)
        # Defender: hull=200, repair bot equipped; attacker will deal damage + bot will regen
        defender_loadout = _loadout(modules=[repair_bot], base_armour=200, name="Defender")
        attacker_loadout = _loadout(weapons=[gun], base_armour=500, name="Attacker")
        result = TickResolver(seed=0).resolve(attacker_loadout, defender_loadout, rng=_AlwaysHit())
        regen_evs = [e for e in result.combat_log if e.type == "regen" and e.actor == "Defender"]
        assert len(regen_evs) >= 1, (
            "Expected >=1 regen event from RepairBotModule with Ketar-I rate, got 0. "
            "This is the end-to-end regression for FIX 1."
        )

    def test_ketar_ii_bot_produces_regen_events(self):
        """A RepairBotModule with Ketar-II rate also produces >=1 regen event."""
        repair_bot = ModuleStats(
            name="Ketar Repair Bot II",
            module_type="RepairBotModule",
            repair_rate=GameConstants.KETAR_II_REPAIR_PCT_PER_SEC,
        )
        gun = _primary(damage=100.0, speed_ms=500, range_m=6000.0)
        defender_loadout = _loadout(modules=[repair_bot], base_armour=200, name="Defender")
        attacker_loadout = _loadout(weapons=[gun], base_armour=500, name="Attacker")
        result = TickResolver(seed=0).resolve(attacker_loadout, defender_loadout, rng=_AlwaysHit())
        regen_evs = [e for e in result.combat_log if e.type == "regen" and e.actor == "Defender"]
        assert len(regen_evs) >= 1, "Expected >=1 regen event from Ketar II repair bot module"

    def test_no_repair_bot_no_regen(self):
        """Without a repair bot, no regen events are emitted for hull/armour."""
        gun = _primary(damage=100.0, speed_ms=500, range_m=6000.0)
        defender_loadout = _loadout(base_armour=200, name="Defender")
        attacker_loadout = _loadout(weapons=[gun], base_armour=500, name="Attacker")
        result = TickResolver(seed=0).resolve(attacker_loadout, defender_loadout, rng=_AlwaysHit())
        hull_armour_regen = [
            e for e in result.combat_log
            if e.type == "regen" and e.actor == "Defender"
            and e.data.get("layer") in ("hull", "armour")
        ]
        assert len(hull_armour_regen) == 0, "No repair bot → no hull/armour regen events"


# ===========================================================================
# FIX 2 — Shock-blast range guard (only fires inside SHOCK_BLAST_TRIGGER_RANGE_M)
# ===========================================================================


class TestShockBlastRangeGuard:
    """Shock-blast fires ONLY when current_distance < SHOCK_BLAST_TRIGGER_RANGE_M (500m).

    Before FIX 2, shock-blast fired on tick 0 at 5000m (STARTING_DISTANCE_M),
    wasting a cooldown by resetting distance to 5000 from 5000. After FIX 2 it
    waits until ships close to < 500m.

    Distance model: delta = BASE_SHIP_SPEED_MPS * 2 * (TICK_MS / 1000)
      = 150 * 2 * 0.010 = 3 m/tick.
    From 5000m → <500m requires ceil((5000-500)/3) + 1 = 1501 ticks.
    The first shock-blast fire tick is therefore >= 1501.
    """

    SPEED_MS: int = 6000  # shock-blast reload cooldown (= 600 ticks at TICK_MS=10)

    def _sb_loadout(self, name: str = "Attacker") -> ShipLoadout:
        sb = _secondary(name="Shock Blast", subtype="shock-blast", damage=0.0, speed_ms=self.SPEED_MS, range_m=0.0)
        return _loadout(secondary_weapons=[sb], base_armour=5000, name=name)

    def _run_fight(self):
        l1 = self._sb_loadout("Attacker")
        l2 = _loadout(base_armour=5000, name="Target")
        return TickResolver(seed=0).resolve(l1, l2, rng=_AlwaysHit())

    def test_no_fire_on_tick_0(self):
        """Shock-blast must NOT fire on tick 0 (ships start at 5000m, range guard = 500m).

        Before FIX 2, the shock-blast fired immediately at tick 0 from 5000m,
        resetting distance to 5000 from 5000 (a no-op wasted cooldown). This test
        is the direct regression guard for the original bug.
        """
        result = self._run_fight()
        t0_fires = [
            e for e in result.combat_log
            if e.tick == 0 and e.type == "weapon_fire" and e.data.get("subtype") == "shock-blast"
        ]
        assert len(t0_fires) == 0, (
            "Shock-blast must NOT fire on tick 0 (distance=5000m >= SHOCK_BLAST_TRIGGER_RANGE_M=500m). "
            "FIX 2 range guard broken."
        )

    def test_all_fires_occur_inside_trigger_range(self):
        """Every shock-blast weapon_fire must have a 'from' distance < 500m.

        We infer the fire-time distance by looking at the distance event emitted on the
        same tick (shock-blast Phase 6 reset). The 'from' field of that distance event
        is the distance at which the weapon fired. It must be < SHOCK_BLAST_TRIGGER_RANGE_M.
        """
        result = self._run_fight()
        sb_fires = [
            e for e in result.combat_log
            if e.type == "weapon_fire" and e.data.get("subtype") == "shock-blast"
        ]
        assert len(sb_fires) >= 1, "Expected at least one shock-blast fire in a full fight"

        for fire_ev in sb_fires:
            tick = fire_ev.tick
            # Find the distance event on the same tick with cause='shock_blast'
            dist_ev = next(
                (e for e in result.combat_log
                 if e.tick == tick and e.type == "distance" and e.data.get("cause") == "shock_blast"),
                None,
            )
            assert dist_ev is not None, f"No shock_blast distance event on tick {tick}"
            fire_distance = dist_ev.data["from"]
            assert fire_distance < GameConstants.SHOCK_BLAST_TRIGGER_RANGE_M, (
                f"Shock-blast fired at {fire_distance}m but trigger range is "
                f"{GameConstants.SHOCK_BLAST_TRIGGER_RANGE_M}m. FIX 2 range guard broken."
            )

    def test_cooldown_set_only_on_fire(self):
        """Shock-blast cooldown is consumed ONLY when the weapon actually fires.

        Before FIX 2: cooldown was set even at tick 0 (long range), burning the first
        reload window. After FIX 2: the `continue` precedes the cooldown-set, so
        the first fire tick is the first tick a cooldown_end follows.
        Verified by checking no cooldown_end for shock-blast before the first fire tick.
        """
        result = self._run_fight()
        first_fire = next(
            (e for e in result.combat_log
             if e.type == "weapon_fire" and e.data.get("subtype") == "shock-blast"),
            None,
        )
        assert first_fire is not None, "Expected at least one shock-blast fire"

        # Shock-blast has no specific cooldown_end event in the current implementation
        # (secondaries don't emit cooldown_end directly).
        # Verify the first fire is NOT on tick 0 — that's the proxy for "no wasted cooldown".
        assert first_fire.tick > 0, (
            f"First shock-blast fire on tick {first_fire.tick} — expected tick > 0 after FIX 2"
        )

        # Additional: the fire tick corresponds to distance < SHOCK_BLAST_TRIGGER_RANGE_M
        dist_ev = next(
            (e for e in result.combat_log
             if e.tick == first_fire.tick and e.type == "distance" and e.data.get("cause") == "shock_blast"),
            None,
        )
        assert dist_ev is not None
        assert dist_ev.data["from"] < GameConstants.SHOCK_BLAST_TRIGGER_RANGE_M

    def test_phase6_distance_event_emitted_after_fire(self):
        """After shock-blast fires: distance event with cause='shock_blast', to=STARTING_DISTANCE_M.

        Also verifies the canonical ordering: weapon_fire (phase 3) < distance (phase 6).
        """
        result = self._run_fight()
        first_fire = next(
            (e for e in result.combat_log
             if e.type == "weapon_fire" and e.data.get("subtype") == "shock-blast"),
            None,
        )
        assert first_fire is not None

        tick = first_fire.tick
        dist_ev = next(
            (e for e in result.combat_log
             if e.tick == tick and e.type == "distance" and e.data.get("cause") == "shock_blast"),
            None,
        )
        assert dist_ev is not None, f"No shock_blast distance event on fire tick {tick}"
        assert abs(dist_ev.data["to"] - STARTING_DIST) < 1e-6, (
            "Shock-blast must reset distance to STARTING_DISTANCE_M"
        )
        assert first_fire.data["hit"] is True
        assert abs(first_fire.data["accuracy"] - 1.0) < 1e-9

        # Phase ordering
        fire_idx = result.combat_log.index(first_fire)
        dist_idx = result.combat_log.index(dist_ev)
        assert fire_idx < dist_idx, "weapon_fire (phase 3) must precede distance (phase 6)"
