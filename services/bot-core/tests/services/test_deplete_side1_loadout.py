"""Unit tests for ``deplete_side1_loadout`` — preflight cross-fight depletion.

The preflight Monte-Carlo (``run_fight_batch(carry_side1_resources=True)``) threads
the player's (side-1) consumable state across the sequential 20-sim run.  This pure
helper applies one fight's consumption to a frozen ``ShipLoadout``, returning a NEW
loadout.  It mirrors ``CombatService._consume_secondary_ammo`` and
``_consume_emergency_system`` but operates on the dataclass (no ORM/DB).
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Stub shared.bblogger before importing resolver code (same pattern as siblings).
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

from services.combat_models import ModuleStats, ShipLoadout, WeaponStats
from services.combat_resolver import _EMERGENCY_SYSTEM_MODULE_TYPE, deplete_side1_loadout

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _sw(name: str, ammo: int | None, subtype: str = "rocket") -> WeaponStats:
    return WeaponStats(
        name=name,
        dps=1.0,
        damage_per_shot=50.0,
        loading_speed_ms=100,
        range_m=4000.0,
        subtype=subtype,
        ammo=ammo,
    )


def _es(name: str = "EmergencySys") -> ModuleStats:
    return ModuleStats(name=name, module_type=_EMERGENCY_SYSTEM_MODULE_TYPE)


def _booster(name: str = "Booster") -> ModuleStats:
    return ModuleStats(name=name, module_type="BoosterModule")


def _loadout(*, secondary_weapons=None, modules=None) -> ShipLoadout:
    return ShipLoadout(
        ship_name="Player",
        base_armour=200,
        secondary_weapons=list(secondary_weapons or []),
        modules=list(modules or []),
    )


def _summary(*, secondary_rounds=None, module_activations=None, slot: str = "1") -> dict:
    block: dict = {}
    if secondary_rounds is not None:
        block["secondary_rounds_by_weapon"] = secondary_rounds
    if module_activations is not None:
        block["module_activations"] = module_activations
    return {"combatants": {slot: block}}


# ---------------------------------------------------------------------------
# Secondary ammo depletion
# ---------------------------------------------------------------------------


class TestSecondaryAmmoDepletion:
    def test_finite_secondary_decrements_by_rounds_fired(self):
        lo = _loadout(secondary_weapons=[_sw("Rocket", ammo=5)])
        out = deplete_side1_loadout(lo, _summary(secondary_rounds={"Rocket": 2}))
        assert len(out.secondary_weapons) == 1
        assert out.secondary_weapons[0].ammo == 3

    def test_secondary_dropped_at_zero(self):
        lo = _loadout(secondary_weapons=[_sw("Rocket", ammo=2)])
        out = deplete_side1_loadout(lo, _summary(secondary_rounds={"Rocket": 2}))
        assert out.secondary_weapons == []  # depleted → auto-unequipped

    def test_secondary_dropped_when_overfired(self):
        """Rounds fired exceeding ammo clamps to 0 and drops the weapon (no negative)."""
        lo = _loadout(secondary_weapons=[_sw("Rocket", ammo=1)])
        out = deplete_side1_loadout(lo, _summary(secondary_rounds={"Rocket": 3}))
        assert out.secondary_weapons == []

    def test_ammo_none_untouched(self):
        """ammo=None (infinite) weapons are never depleted nor dropped — back-compat."""
        lo = _loadout(secondary_weapons=[_sw("InfRocket", ammo=None)])
        out = deplete_side1_loadout(lo, _summary(secondary_rounds={"InfRocket": 99}))
        assert len(out.secondary_weapons) == 1
        assert out.secondary_weapons[0].ammo is None

    def test_unfired_secondary_unchanged(self):
        """A weapon with no rounds-fired entry keeps its full ammo."""
        lo = _loadout(secondary_weapons=[_sw("Rocket", ammo=4)])
        out = deplete_side1_loadout(lo, _summary(secondary_rounds={"OtherGun": 3}))
        assert out.secondary_weapons[0].ammo == 4

    def test_mixed_secondaries(self):
        """One depletes to 0 (dropped), one decrements, one is infinite (kept)."""
        lo = _loadout(
            secondary_weapons=[
                _sw("Drop", ammo=2),
                _sw("Decr", ammo=5),
                _sw("Inf", ammo=None),
            ]
        )
        out = deplete_side1_loadout(lo, _summary(secondary_rounds={"Drop": 2, "Decr": 1, "Inf": 3}))
        names = {w.name: w.ammo for w in out.secondary_weapons}
        assert "Drop" not in names
        assert names["Decr"] == 4
        assert names["Inf"] is None


# ---------------------------------------------------------------------------
# EmergencySystem consumption
# ---------------------------------------------------------------------------


class TestEmergencySystemConsumption:
    def test_es_activation_removes_one_module(self):
        lo = _loadout(modules=[_booster(), _es()])
        out = deplete_side1_loadout(lo, _summary(module_activations={"emergency_system": 1}))
        types_left = [m.module_type for m in out.modules]
        assert _EMERGENCY_SYSTEM_MODULE_TYPE not in types_left
        assert "BoosterModule" in types_left  # non-ES module preserved

    def test_es_two_equipped_only_one_removed(self):
        """ES fires at most once/fight → exactly one of two ES modules is consumed."""
        lo = _loadout(modules=[_es("ES_A"), _es("ES_B")])
        out = deplete_side1_loadout(lo, _summary(module_activations={"emergency_system": 1}))
        es_left = [m for m in out.modules if m.module_type == _EMERGENCY_SYSTEM_MODULE_TYPE]
        assert len(es_left) == 1

    def test_es_no_activation_noop(self):
        lo = _loadout(modules=[_es()])
        out = deplete_side1_loadout(lo, _summary(module_activations={"booster": 1}))
        assert any(m.module_type == _EMERGENCY_SYSTEM_MODULE_TYPE for m in out.modules)

    def test_es_absent_activation_reported_is_safe(self):
        """ES activation reported but none equipped — no crash, modules unchanged."""
        lo = _loadout(modules=[_booster()])
        out = deplete_side1_loadout(lo, _summary(module_activations={"emergency_system": 1}))
        assert [m.module_type for m in out.modules] == ["BoosterModule"]


# ---------------------------------------------------------------------------
# Slot isolation, no-ops, immutability
# ---------------------------------------------------------------------------


class TestIsolationAndImmutability:
    def test_only_side1_consumed(self):
        """Side-2 (criminal) consumption in the summary must not affect the player loadout."""
        lo = _loadout(
            secondary_weapons=[_sw("Rocket", ammo=5)],
            modules=[_es()],
        )
        summary = {
            "combatants": {
                "2": {
                    "secondary_rounds_by_weapon": {"Rocket": 5},
                    "module_activations": {"emergency_system": 1},
                }
            }
        }
        out = deplete_side1_loadout(lo, summary)
        assert out.secondary_weapons[0].ammo == 5
        assert any(m.module_type == _EMERGENCY_SYSTEM_MODULE_TYPE for m in out.modules)

    def test_empty_summary_noop(self):
        lo = _loadout(secondary_weapons=[_sw("Rocket", ammo=5)], modules=[_es()])
        out = deplete_side1_loadout(lo, {})
        assert out.secondary_weapons[0].ammo == 5
        assert len(out.modules) == 1

    def test_returns_new_object_input_unmutated(self):
        sw = _sw("Rocket", ammo=5)
        es = _es()
        lo = _loadout(secondary_weapons=[sw], modules=[es])
        out = deplete_side1_loadout(
            lo, _summary(secondary_rounds={"Rocket": 1}, module_activations={"emergency_system": 1})
        )
        # New object; input loadout and its lists are untouched.
        assert out is not lo
        assert lo.secondary_weapons[0].ammo == 5
        assert len(lo.modules) == 1
        # The carried-forward (decremented) secondary is a new WeaponStats.
        assert out.secondary_weapons[0].ammo == 4
        assert out.modules == []


# ---------------------------------------------------------------------------
# Real-fight round-trip: the helper reads the keys the REAL summary builder writes.
# (All tests above use hand-built summaries; this pins the actual key names so a
#  future change to _build_fight_summary that renamed a key would fail here.)
# ---------------------------------------------------------------------------


class TestRealFightSummaryRoundTrip:
    def test_real_summary_drives_secondary_depletion(self):
        """A real run_fight summary decrements the matching secondary by name."""
        from compute.combat_worker import run_fight

        # Secondary with huge range fires from tick 0; big ammo so it never fully
        # depletes — lets us assert an exact decrement equal to rounds fired.
        rocket = WeaponStats(
            name="TestRocket",
            dps=1.0,
            damage_per_shot=50.0,
            loading_speed_ms=100,
            range_m=99_999.0,
            subtype="rocket",
            ammo=100_000,  # far above max possible fires → never fully depletes
        )
        # Weak primary + huge armour on both sides → fight runs long enough for
        # the secondary to fire several times, then ends at the time cap.
        weak_gun = WeaponStats(name="Pop", dps=0.1, damage_per_shot=1.0, loading_speed_ms=10_000, range_m=99_999.0)
        player = ShipLoadout(ship_name="P", base_armour=99_999, weapons=[weak_gun], secondary_weapons=[rocket])
        crim = ShipLoadout(ship_name="C", base_armour=99_999)

        full = run_fight(
            player,
            crim,
            pvc_damage_reduction=0.0,
            seed=42,
            combatant1_label="",
            combatant2_label="",
            compact=False,
        )

        rounds = full["summary"]["combatants"]["1"].get("secondary_rounds_by_weapon", {})
        fired = rounds.get("TestRocket", 0)
        assert fired > 0, f"secondary should have fired and been recorded by name; summary={rounds!r}"

        out = deplete_side1_loadout(player, full["summary"])
        remaining = next((w.ammo for w in out.secondary_weapons if w.name == "TestRocket"), None)
        assert remaining == 100_000 - fired, f"expected {100_000 - fired} ammo left, got {remaining}"

    def test_real_summary_drives_emergency_system_consumption(self):
        """A real run_fight where the player's ES fires removes it via the helper.

        The player has a near-fatal hull so incoming damage trips the EmergencySystem
        (hull <= 0 → ES consumes). We then confirm the real summary's
        module_activations drives removal through deplete_side1_loadout.
        """
        from compute.combat_worker import run_fight

        es = ModuleStats(
            name="EmergencySys",
            module_type=_EMERGENCY_SYSTEM_MODULE_TYPE,
            effect_duration_ms=10_000,
        )
        # Player is fragile and unarmed; criminal has a strong gun → player hull
        # drops to 0, tripping the ES.
        crim_gun = WeaponStats(name="Cannon", dps=500.0, damage_per_shot=200.0, loading_speed_ms=100, range_m=99_999.0)
        player = ShipLoadout(ship_name="P", base_armour=50, modules=[es])
        crim = ShipLoadout(ship_name="C", base_armour=500, weapons=[crim_gun])

        full = run_fight(
            player,
            crim,
            pvc_damage_reduction=0.0,
            seed=42,
            combatant1_label="",
            combatant2_label="",
            compact=False,
        )

        acts = full["summary"]["combatants"]["1"].get("module_activations", {})
        assert acts.get("emergency_system", 0) >= 1, f"ES should have fired; module_activations={acts!r}"

        out = deplete_side1_loadout(player, full["summary"])
        assert not any(m.module_type == _EMERGENCY_SYSTEM_MODULE_TYPE for m in out.modules)
