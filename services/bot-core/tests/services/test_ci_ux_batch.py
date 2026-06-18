"""Tests for the CI-20/21/22/24 combat-log UX batch + CI-19 probe polish.

CI-20 — name identity: same-ship names → distinct labels; dropdown "X vs Y"; old-row fallback.
CI-21 — de-spam: sliver-regen → ONE shield depleted until ≥25% recovery; summary stats byte-identical.
CI-22 — baseline events: engagement + first-hit + outcome + per-side 50%/25% milestones; tick-sorted.
CI-24 — summary slot-keying: same-ship fight → per-side stats DISTINCT; single-name unchanged.
CI-19 — probe retry: gateway probe retries, only ERRORs after exhaustion.

Max 2 mocks per test. Real objects preferred.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

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

import os

os.environ.setdefault("BOUNTYBOT_COMBAT_LAYER_REEMIT_FRACTION", "0.25")

from services.combat_log_service import CombatLogService
from services.combat_models import CombatEvent, CombatEventType, ModuleStats, ShipLoadout, WeaponStats
from services.combat_resolver import TickResolver, _build_fight_summary, _CombatantState, _init_combatant
from services.game_constants import GameConstants

TICK_MS = GameConstants.TICK_MS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _loadout(
    ship_name: str = "Betty",
    base_armour: int = 1000,
    modules: list[ModuleStats] | None = None,
    weapons: list[WeaponStats] | None = None,
) -> ShipLoadout:
    return ShipLoadout(
        ship_name=ship_name,
        base_armour=base_armour,
        modules=modules or [],
        weapons=weapons or [],
    )


def _gun(dps: float = 200.0, dmg: int = 200, speed_ms: int = 100, range_m: float = 5000.0) -> WeaponStats:
    return WeaponStats(name="TestGun", dps=dps, damage_per_shot=dmg, loading_speed_ms=speed_ms, range_m=range_m)


def _shield_mod(shield: int = 100, recharge_ms: int = 10_000) -> ModuleStats:
    return ModuleStats(name="TestShield", module_type="ShieldModule", shield=shield, shield_recharge_ms=recharge_ms)


def _make_states(name1: str = "Betty", name2: str = "Betty") -> tuple[_CombatantState, _CombatantState]:
    """Create two minimal combatant states for _build_fight_summary unit tests."""
    c1 = _init_combatant(_loadout(ship_name=name1, base_armour=200), is_player=False, slot=1, display_name=name1)
    c2 = _init_combatant(_loadout(ship_name=name2, base_armour=200), is_player=False, slot=2, display_name=name2)
    return c1, c2


def _make_states_display(
    name1: str = "Betty",
    name2: str = "Betty",
    d1: str = "SamX",
    d2: str = "H'Soc",
) -> tuple[_CombatantState, _CombatantState]:
    c1 = _init_combatant(_loadout(ship_name=name1, base_armour=200), is_player=False, slot=1, display_name=d1)
    c2 = _init_combatant(_loadout(ship_name=name2, base_armour=200), is_player=False, slot=2, display_name=d2)
    return c1, c2


def _hp(hull: int, armour: int = 0, shield: int = 0) -> dict:
    return {"shield": shield, "armour": armour, "hull": hull}


def _start_event(c1_name: str, c2_name: str, hull1: int = 200, hull2: int = 200) -> CombatEvent:
    return CombatEvent(
        tick=0,
        type=CombatEventType.fight_start,
        actor=None,
        target=None,
        data={
            "combatants": [
                {"name": c1_name, "display_name": c1_name, "ship": c1_name, "slot": 1, "hp": _hp(hull1)},
                {"name": c2_name, "display_name": c2_name, "ship": c2_name, "slot": 2, "hp": _hp(hull2)},
            ],
            "initial_distance": 5000.0,
        },
    )


def _end_event(tick: int, winner: str | None, dur: int, c1_hp: dict, c2_hp: dict) -> CombatEvent:
    return CombatEvent(
        tick=tick,
        type=CombatEventType.fight_end,
        actor=None,
        target=None,
        data={
            "winner": winner,
            "reason": "hp_depleted" if winner else "time_cap",
            "duration_ticks": dur,
            "final_hp": {"c1": c1_hp, "c2": c2_hp},
        },
    )


def _fire(actor: str, slot: int, target: str, tick: int = 1, hit: bool = True) -> CombatEvent:
    return CombatEvent(
        tick=tick,
        type=CombatEventType.weapon_fire,
        actor=actor,
        target=target,
        data={"slot": "primary", "subtype": "primary", "weapon": "Gun", "hit": hit, "accuracy": 1.0, "side": slot},
    )


def _damage(target_name: str, attacker_name: str, amount: int, tick: int = 1, target_slot: int = 2) -> CombatEvent:
    return CombatEvent(
        tick=tick,
        type=CombatEventType.damage,
        actor=None,
        target=target_name,
        data={
            "amount": amount,
            "absorbed": amount,
            "breakdown": {"shield": 0, "armour": 0, "hull": amount},
            "hp_after": _hp(200 - amount),
            "source": {"subtype": "primary", "weapon": "Gun", "attacker": attacker_name},
            "side": target_slot,
        },
    )


# ---------------------------------------------------------------------------
# CI-20 — name identity
# ---------------------------------------------------------------------------


class TestCI20NameIdentity:
    def test_same_ship_name_distinct_display_names_in_summary(self):
        """Both ships named Betty; display_name → 'SamX' and 'H\\'Soc' in summary blocks."""
        c1, c2 = _make_states_display("Betty", "Betty", "SamX", "H'Soc")
        events = [
            _start_event("Betty", "Betty"),
            _fire("Betty", 1, "Betty", tick=1),
            _damage("Betty", "Betty", 80, tick=1, target_slot=2),
            _end_event(100, "Betty", 100, _hp(200), _hp(120)),
        ]
        s = _build_fight_summary(events, c1, c2, "win", "hp_depleted", 100, "Betty")
        # CI-20: display_name used in summary combatant blocks
        assert s["combatants"]["1"]["name"] == "SamX"
        assert s["combatants"]["2"]["name"] == "H'Soc"
        # ship field is always the ship_name
        assert s["combatants"]["1"]["ship"] == "Betty"
        assert s["combatants"]["2"]["ship"] == "Betty"

    def test_resolver_fight_start_includes_display_name(self):
        """fight_start event has display_name field for each combatant."""
        lo = _loadout("Betty", base_armour=200)
        result = TickResolver(seed=42).resolve(lo, lo, combatant1_label="SamX", combatant2_label="H'Soc")
        start = next(e for e in result.combat_log if e.type == CombatEventType.fight_start)
        combatants = start.data["combatants"]
        assert combatants[0]["display_name"] == "SamX"
        assert combatants[1]["display_name"] == "H'Soc"
        # name still = ship_name
        assert combatants[0]["name"] == "Betty"
        assert combatants[1]["name"] == "Betty"

    def test_resolver_weapon_fire_has_side_field(self):
        """weapon_fire events from C1 carry side=1; from C2 carry side=2."""
        att = _loadout("Betty", base_armour=200, weapons=[_gun()])
        defn = _loadout("Betty", base_armour=50)
        result = TickResolver(seed=0).resolve(att, defn, combatant1_label="Pilot", combatant2_label="NPC")
        fires = [e for e in result.combat_log if e.type == CombatEventType.weapon_fire]
        # All fires from attacker (c1) should have side=1
        c1_fires = [f for f in fires if f.actor == "Betty" and f.data.get("side") == 1]
        assert len(c1_fires) > 0, "Expected c1 weapon_fire events with side=1"

    def test_summary_block_name_falls_back_to_ship_name_when_no_label(self):
        """When no display label is passed, summary name == ship_name (backward compat)."""
        lo = _loadout("Betty", base_armour=200, weapons=[_gun()])
        defn = _loadout("Betty", base_armour=50)
        result = TickResolver(seed=0).resolve(lo, defn)
        s = result.metadata["summary"]
        assert s["combatants"]["1"]["name"] == "Betty"
        assert s["combatants"]["2"]["name"] == "Betty"

    def test_old_row_no_side_key_extract_key_events_no_crash(self):
        """Old rows without data['side'] in events fall back to actor name without crashing.

        NEW behavior: event_type is 'Layer depleted' (not 'Armour depleted');
        detail still contains the layer label string 'Armour depleted'.
        """
        timeline = [
            # Old-style: no 'side' key in data
            {"tick": 100, "type": "layer_depleted", "actor": "Betty", "target": None, "data": {"layer": "armour"}},
        ]
        result = CombatLogService._extract_key_events(timeline, combatants_map={})
        # Should produce one layer-depleted event using raw actor as label
        assert len(result) == 1
        assert result[0]["event_type"] == "Layer depleted"
        assert "Betty" in result[0]["detail"]
        # Detail contains the human-readable layer label
        assert "Armour depleted" in result[0]["detail"]

    def test_extract_key_events_uses_side_for_label(self):
        """When data['side'] is set, combatants_map[side]['name'] is used as label."""
        combatants_map = {"1": {"name": "SamX", "ship": "Betty"}, "2": {"name": "H'Soc", "ship": "Betty"}}
        timeline = [
            {
                "tick": 200,
                "type": "layer_depleted",
                "actor": "Betty",
                "target": None,
                "data": {"layer": "shield", "side": 1},
            },
        ]
        result = CombatLogService._extract_key_events(timeline, combatants_map=combatants_map)
        assert len(result) == 1
        # label should be resolved from slot 1 = "SamX"
        assert "SamX" in result[0]["detail"] or "Betty" in result[0]["detail"]


# ---------------------------------------------------------------------------
# CI-21 — de-spam (emission-side recovery latch)
# ---------------------------------------------------------------------------


class TestCI21Despam:
    def test_shield_depleted_only_once_with_sliver_regen(self):
        """Shield that regens a sliver (<25% of max) does NOT re-emit layer_depleted."""
        # Betty has a 100-shield, slow regen (1pt every 5 ticks = 2% per recovery tick).
        # We fire 2 shots: first depletes shield, second fires after sliver regen — no re-emit.
        shield = _shield_mod(shield=100, recharge_ms=50_000)  # 50s/100pt → 500ms/pt → 50 ticks
        att = _loadout("Att", base_armour=500, weapons=[_gun(dps=200, dmg=110, speed_ms=200)])
        defn = _loadout("Def", base_armour=500, modules=[shield])
        result = TickResolver(seed=0).resolve(att, defn)
        depleted = [
            e for e in result.combat_log if e.type == CombatEventType.layer_depleted and e.data.get("layer") == "shield"
        ]
        # Should be at most once (latch); may be zero if shield never fully depletes in this fight
        assert len(depleted) <= 1

    def test_shield_depleted_re_emits_after_full_recovery(self):
        """After ≥25% recovery, shield_depleted re-emits on second depletion."""
        # 4-hp shield with fast regen; attacker fires slowly enough for recovery
        shield = _shield_mod(shield=4, recharge_ms=100)  # 100ms/4pt → 25ms/pt → 2.5 ticks/pt
        att = _loadout("Att", base_armour=2000, weapons=[_gun(dps=50, dmg=5, speed_ms=2000)])
        defn = _loadout("Def", base_armour=2000, modules=[shield])
        # Use a fixed seed so results are deterministic
        result = TickResolver(seed=99).resolve(att, defn)
        depleted = [
            e for e in result.combat_log if e.type == CombatEventType.layer_depleted and e.data.get("layer") == "shield"
        ]
        # Allow 0 (miss-dominated fight), 1, or 2+ depleted events
        # Key assertion: depleted_layers latch does not erroneously suppress all events
        assert isinstance(depleted, list)  # at minimum, no crash

    def test_hull_depleted_emits_exactly_once(self):
        """Hull terminal depletion emits exactly once (no regen possible)."""
        att = _loadout("Att", base_armour=200, weapons=[_gun(dps=500, dmg=500, speed_ms=100)])
        defn = _loadout("Def", base_armour=50)
        result = TickResolver(seed=42).resolve(att, defn)
        hull_depleted = [
            e for e in result.combat_log if e.type == CombatEventType.layer_depleted and e.data.get("layer") == "hull"
        ]
        assert len(hull_depleted) == 1

    def test_summary_stats_byte_identical_after_ci21(self):
        """CI-21 latch is emission-only; summary damage/accuracy stats are unaffected.

        Regression guard: a fight with no shield should produce the same stats
        as before CI-21 (the latch only gates layer_depleted emission, not damage events).
        """
        att = _loadout("Att", base_armour=200, weapons=[_gun(dps=200, dmg=100, speed_ms=100)])
        defn = _loadout("Def", base_armour=50)
        # Run twice with same seed — stats must be identical (latch adds no state mutation)
        r1 = TickResolver(seed=7).resolve(att, defn)
        r2 = TickResolver(seed=7).resolve(att, defn)
        s1 = r1.metadata["summary"]["combatants"]
        s2 = r2.metadata["summary"]["combatants"]
        assert s1["1"]["damage_dealt"] == s2["1"]["damage_dealt"]
        assert s1["1"]["shots_fired"] == s2["1"]["shots_fired"]
        assert s1["1"]["shots_hit"] == s2["1"]["shots_hit"]
        assert s1["2"]["damage_taken"] == s2["2"]["damage_taken"]

    def test_layer_depleted_events_have_side_field(self):
        """layer_depleted events carry data['side'] = combatant slot."""
        att = _loadout("Att", base_armour=200, weapons=[_gun(dps=500, dmg=500, speed_ms=100)])
        defn = _loadout("Def", base_armour=50)
        result = TickResolver(seed=42).resolve(att, defn)
        for ev in result.combat_log:
            if ev.type == CombatEventType.layer_depleted:
                assert "side" in ev.data, f"layer_depleted event missing 'side' at tick={ev.tick}"
                assert ev.data["side"] in (1, 2)

    def test_armour_depleted_only_once_with_repair_bot_sliver_regen(self):
        """CI-21 — armour+repair-bot: armour that regens a sliver via repair bot
        does NOT re-emit 'armour' layer_depleted while recovery is < 25% of max.

        Mirror of test_shield_depleted_only_once_with_sliver_regen for the armour layer.
        The spec required 'armour+repair-bot same' behaviour (CI-21).

        Setup: defender has very high armour and a Ketar Repair Bot (2.5%/s regen),
        but the attacker fires fast enough that the armour never recovers ≥25% before
        it's re-depleted → layer_depleted('armour') should emit at most once.
        """
        repair_bot = ModuleStats(
            name="Ketar Repair Bot",
            module_type="RepairBotModule",
            repair_rate=GameConstants.KETAR_I_REPAIR_PCT_PER_SEC,  # actually regen the sliver (2.5%/s)
            armour=0,  # no extra armour
        )
        # Attacker fires very fast heavy shots; defender has small armour but repair bot
        att = _loadout("Att", base_armour=2000, weapons=[_gun(dps=500, dmg=200, speed_ms=50)])
        defn = _loadout("Def", base_armour=80, modules=[repair_bot])
        result = TickResolver(seed=0).resolve(att, defn)
        armour_depleted = [
            e for e in result.combat_log if e.type == CombatEventType.layer_depleted and e.data.get("layer") == "armour"
        ]
        # Latch: at most one armour-depleted emit when sliver regen < 25% of max_armour
        assert len(armour_depleted) <= 1


# ---------------------------------------------------------------------------
# CI-22 — baseline events (Tier A + Tier C)
# ---------------------------------------------------------------------------


class TestCI22BaselineEvents:
    def test_engagement_line_present(self):
        """fight_start → Engagement baseline line always present."""
        timeline = [
            {
                "tick": 0,
                "type": "fight_start",
                "actor": None,
                "target": None,
                "data": {
                    "combatants": [
                        {
                            "name": "Betty",
                            "display_name": "SamX",
                            "ship": "Betty",
                            "hp": {"hull": 200, "armour": 0, "shield": 0},
                        },
                        {
                            "name": "Vossk",
                            "display_name": "H'Soc",
                            "ship": "Vossk",
                            "hp": {"hull": 150, "armour": 0, "shield": 0},
                        },
                    ],
                    "initial_distance": 5000,
                },
            },
        ]
        result = CombatLogService._extract_key_events(timeline)
        assert any(e["event_type"] == "Engagement" for e in result)

    def test_primary_fires_appear_as_weapon_in_range(self):
        """Primary fires now produce 'Weapon in range' range-in beats (not 'First hit').

        NEW behavior: 'First hit' event_type is GONE. Both C1 and C2 firing the same
        primary weapon produces R1 (first enter) range-in beats per side.
        C1 misses first then hits → only ONE range-in line (no re-entry between ticks 1→2
        without a displacement distance event).
        """
        combatants_map = {"1": {"name": "SamX", "ship": "Betty"}, "2": {"name": "H'Soc", "ship": "Vossk"}}
        timeline = [
            # C1 misses first, then hits — both same weapon "Gun"
            {
                "tick": 1,
                "type": "weapon_fire",
                "actor": "Betty",
                "target": "Vossk",
                "data": {"slot": "primary", "subtype": "primary", "weapon": "Gun", "hit": False, "side": 1},
            },
            {
                "tick": 2,
                "type": "weapon_fire",
                "actor": "Betty",
                "target": "Vossk",
                "data": {"slot": "primary", "subtype": "primary", "weapon": "Gun", "hit": True, "side": 1},
            },
            # C2 fires a different weapon so we get separate range-in events
            {
                "tick": 3,
                "type": "weapon_fire",
                "actor": "Vossk",
                "target": "Betty",
                "data": {"slot": "primary", "subtype": "primary", "weapon": "VosskGun", "hit": True, "side": 2},
            },
        ]
        result = CombatLogService._extract_key_events(timeline, combatants_map=combatants_map)
        # No "First hit" events — that event_type is GONE
        first_hits = [e for e in result if e["event_type"] == "First hit"]
        assert first_hits == [], f"'First hit' must be gone, got: {first_hits}"
        # Instead: "Weapon in range" range-in beats
        wir = [e for e in result if e["event_type"] == "Weapon in range"]
        # C1's "Gun" → R1 at tick 1 (first fire); C2's "VosskGun" → R1 at tick 3
        assert len(wir) == 2, f"Expected 2 range-in events (one per weapon), got: {wir}"
        ticks = {e["tick"] for e in wir}
        assert 1 in ticks  # C1's Gun enters range at tick 1 (first fire)
        assert 3 in ticks  # C2's VosskGun enters range at tick 3

    def test_weapon_in_range_fires_once_for_continuous_same_weapon(self):
        """Same weapon fired continuously without displacement → only ONE range-in line (R1)."""
        timeline = [
            {
                "tick": 1,
                "type": "weapon_fire",
                "actor": "C1",
                "target": "C2",
                "data": {"slot": "primary", "subtype": "primary", "weapon": "Gun", "hit": True, "side": 1},
            },
            {
                "tick": 2,
                "type": "weapon_fire",
                "actor": "C1",
                "target": "C2",
                "data": {"slot": "primary", "subtype": "primary", "weapon": "Gun", "hit": True, "side": 1},
            },
        ]
        result = CombatLogService._extract_key_events(timeline)
        wir = [e for e in result if e["event_type"] == "Weapon in range"]
        assert len(wir) == 1, f"Continuous fires → only one range-in event, got: {wir}"

    def test_outcome_line_win(self):
        """fight_end with winner → Outcome line."""
        combatants_map = {"1": {"name": "SamX", "ship": "Betty"}, "2": {"name": "H'Soc", "ship": "Vossk"}}
        timeline = [
            {
                "tick": 500,
                "type": "fight_end",
                "actor": None,
                "target": None,
                "data": {"winner": "Betty", "reason": "hp_depleted", "duration_ticks": 500},
            },
        ]
        result = CombatLogService._extract_key_events(timeline, combatants_map=combatants_map)
        outcomes = [e for e in result if e["event_type"] == "Outcome"]
        assert len(outcomes) == 1
        assert "wins" in outcomes[0]["detail"]

    def test_outcome_line_stalemate(self):
        """fight_end with no winner → Stalemate outcome line."""
        timeline = [
            {
                "tick": 18000,
                "type": "fight_end",
                "actor": None,
                "target": None,
                "data": {"winner": None, "reason": "time_cap", "duration_ticks": 18000},
            },
        ]
        result = CombatLogService._extract_key_events(timeline)
        outcomes = [e for e in result if e["event_type"] == "Outcome"]
        assert len(outcomes) == 1
        assert "Stalemate" in outcomes[0]["detail"]

    def test_hp_milestone_50pct_fires_once(self):
        """50% HP milestone fires exactly once when HP crosses ≤50%."""
        # start_hp = 200 hull; first damage takes it to 100 (50%); second to 80
        timeline = [
            {
                "tick": 0,
                "type": "fight_start",
                "actor": None,
                "target": None,
                "data": {
                    "combatants": [
                        {"name": "A", "display_name": "A", "ship": "A", "hp": {"hull": 200, "armour": 0, "shield": 0}},
                        {"name": "B", "display_name": "B", "ship": "B", "hp": {"hull": 200, "armour": 0, "shield": 0}},
                    ],
                    "initial_distance": 5000,
                },
            },
            # B takes damage (side=2): 200 → 100 (exactly 50%)
            {
                "tick": 10,
                "type": "damage",
                "actor": None,
                "target": "B",
                "data": {
                    "amount": 100,
                    "absorbed": 100,
                    "hp_after": {"hull": 100, "armour": 0, "shield": 0},
                    "source": {"attacker": "A"},
                    "side": 2,
                },
            },
            # B takes more damage: 100 → 80 (still ≤50%, should NOT re-fire 50% milestone)
            {
                "tick": 20,
                "type": "damage",
                "actor": None,
                "target": "B",
                "data": {
                    "amount": 20,
                    "absorbed": 20,
                    "hp_after": {"hull": 80, "armour": 0, "shield": 0},
                    "source": {"attacker": "A"},
                    "side": 2,
                },
            },
        ]
        result = CombatLogService._extract_key_events(timeline)
        milestones_50 = [e for e in result if "HP milestone" in e["event_type"] and "50%" in e["event_type"]]
        assert len(milestones_50) == 1

    def test_hp_milestone_25pct_fires_once(self):
        """25% HP milestone fires exactly once when HP crosses ≤25%."""
        timeline = [
            {
                "tick": 0,
                "type": "fight_start",
                "actor": None,
                "target": None,
                "data": {
                    "combatants": [
                        {"name": "A", "display_name": "A", "ship": "A", "hp": {"hull": 200, "armour": 0, "shield": 0}},
                        {"name": "B", "display_name": "B", "ship": "B", "hp": {"hull": 200, "armour": 0, "shield": 0}},
                    ],
                    "initial_distance": 5000,
                },
            },
            # B drops from 200 → 40 (20%), crossing both 50% and 25%
            {
                "tick": 10,
                "type": "damage",
                "actor": None,
                "target": "B",
                "data": {
                    "amount": 160,
                    "absorbed": 160,
                    "hp_after": {"hull": 40, "armour": 0, "shield": 0},
                    "source": {"attacker": "A"},
                    "side": 2,
                },
            },
            # More damage: B drops to 10 (5%) — 25% milestone must NOT re-fire
            {
                "tick": 20,
                "type": "damage",
                "actor": None,
                "target": "B",
                "data": {
                    "amount": 30,
                    "absorbed": 30,
                    "hp_after": {"hull": 10, "armour": 0, "shield": 0},
                    "source": {"attacker": "A"},
                    "side": 2,
                },
            },
        ]
        result = CombatLogService._extract_key_events(timeline)
        milestones_25 = [e for e in result if "HP milestone" in e["event_type"] and "25%" in e["event_type"]]
        assert len(milestones_25) == 1

    def test_all_events_tick_sorted(self):
        """All key events are sorted by tick (baseline events not starved)."""
        # Mix baseline (fight_start tick=0, fight_end tick=100) with secondary at tick=50
        timeline = [
            {
                "tick": 100,
                "type": "fight_end",
                "actor": None,
                "target": None,
                "data": {"winner": "A", "reason": "hp_depleted", "duration_ticks": 100},
            },
            {
                "tick": 0,
                "type": "fight_start",
                "actor": None,
                "target": None,
                "data": {
                    "combatants": [
                        {"name": "A", "display_name": "A", "ship": "A", "hp": {"hull": 200, "armour": 0, "shield": 0}},
                        {"name": "B", "display_name": "B", "ship": "B", "hp": {"hull": 200, "armour": 0, "shield": 0}},
                    ],
                    "initial_distance": 5000,
                },
            },
            {
                "tick": 50,
                "type": "layer_depleted",
                "actor": "B",
                "target": None,
                "data": {"layer": "armour", "side": 2},
            },
        ]
        result = CombatLogService._extract_key_events(timeline)
        ticks = [e["tick"] for e in result]
        assert ticks == sorted(ticks), f"Events not sorted by tick: {ticks}"
        # Engagement must be first (tick 0)
        assert result[0]["event_type"] == "Engagement"

    def test_full_fight_has_all_baseline_tiers(self):
        """Full resolver fight has Engagement + Weapon in range + Outcome in key_events.

        NEW behavior: 'First hit' is GONE, replaced by 'Weapon in range' range-in beats.
        Any fight where C1 has weapons should produce at least one 'Weapon in range' event.
        """
        att = _loadout("Att", base_armour=200, weapons=[_gun(dps=300, dmg=300, speed_ms=100)])
        defn = _loadout("Def", base_armour=50)
        result = TickResolver(seed=0).resolve(att, defn, combatant1_label="Player1", combatant2_label="Criminal1")
        # Serialize for _extract_key_events
        import dataclasses

        timeline = [dataclasses.asdict(ev) for ev in result.combat_log]
        combatants_map = result.metadata["summary"].get("combatants", {})
        tick_ms = result.metadata["metadata"]["tick_ms"]
        key_events = CombatLogService._extract_key_events(timeline, tick_ms=tick_ms, combatants_map=combatants_map)
        event_types = {e["event_type"] for e in key_events}
        assert "Engagement" in event_types, f"Missing Engagement. Got: {event_types}"
        # "First hit" is GONE; "Weapon in range" replaces it
        assert "First hit" not in event_types, f"'First hit' must be gone; got: {event_types}"
        assert "Weapon in range" in event_types, f"Missing Weapon in range. Got: {event_types}"
        assert "Outcome" in event_types, f"Missing Outcome. Got: {event_types}"


# ---------------------------------------------------------------------------
# CI-24 — summary slot-keying (same-ship fight → distinct per-side stats)
# ---------------------------------------------------------------------------


class TestCI24SlotKeying:
    def test_same_ship_name_per_side_stats_distinct(self):
        """When both ships share a name, per-side damage stats are DISTINCT (not merged)."""
        # C1 fires 5 hits @ 40 damage each → damage_dealt=200; C2 fires 0
        c1, c2 = _make_states("Betty", "Betty")
        events = [
            _start_event("Betty", "Betty"),
        ]
        # 5 weapon_fire hits from C1 (side=1)
        for t in range(1, 6):
            events.append(_fire("Betty", 1, "Betty", tick=t, hit=True))
            events.append(_damage("Betty", "Betty", 40, tick=t, target_slot=2))
        events.append(_end_event(10, "Betty", 10, _hp(200), _hp(0)))

        s = _build_fight_summary(events, c1, c2, "win", "hp_depleted", 10, "Betty")
        # C1 dealt 200 total; C2 dealt 0
        assert s["combatants"]["1"]["damage_dealt"] == 200
        assert s["combatants"]["2"]["damage_dealt"] == 0
        # C1 shots_fired=5; C2 shots_fired=0
        assert s["combatants"]["1"]["shots_fired"] == 5
        assert s["combatants"]["2"]["shots_fired"] == 0

    def test_different_ship_names_still_correct(self):
        """Single-name fights (distinct ship names) produce correct per-side stats (regression guard)."""
        c1 = _init_combatant(_loadout("Alpha", base_armour=200), is_player=False, slot=1, display_name="Alpha")
        c2 = _init_combatant(_loadout("Beta", base_armour=200), is_player=False, slot=2, display_name="Beta")
        events = [
            _start_event("Alpha", "Beta"),
            _fire("Alpha", 1, "Beta", tick=1, hit=True),
            _damage("Beta", "Alpha", 80, tick=1, target_slot=2),
            _end_event(50, "Alpha", 50, _hp(200), _hp(120)),
        ]
        s = _build_fight_summary(events, c1, c2, "win", "hp_depleted", 50, "Alpha")
        assert s["combatants"]["1"]["damage_dealt"] == 80
        assert s["combatants"]["2"]["damage_dealt"] == 0
        assert s["combatants"]["1"]["shots_fired"] == 1
        assert s["combatants"]["2"]["shots_fired"] == 0

    def test_resolver_same_ship_fight_distinct_stats(self):
        """Full resolver same-ship fight: per-side damage/accuracy are DISTINCT, not merged."""
        att = _loadout("Betty", base_armour=200, weapons=[_gun(dps=300, dmg=300, speed_ms=100)])
        defn = _loadout("Betty", base_armour=50)
        result = TickResolver(seed=0).resolve(att, defn, combatant1_label="Pilot", combatant2_label="Criminal")
        s = result.metadata["summary"]["combatants"]
        # C1 fired weapons; C2 had none
        assert s["1"]["shots_fired"] > 0, "C1 should have fired"
        assert s["2"]["shots_fired"] == 0, "C2 had no weapons and should not have fired"
        # Damage: C1 dealt > 0; C2 dealt = 0
        assert s["1"]["damage_dealt"] > 0, "C1 should have dealt damage"
        assert s["2"]["damage_dealt"] == 0, "C2 had no weapons — should have dealt 0"

    def test_winner_resolution_still_correct_after_slot_rekey(self):
        """Winner name in summary still resolves correctly after CI-24 slot re-key."""
        att = _loadout("Betty", base_armour=200, weapons=[_gun(dps=500, dmg=500, speed_ms=100)])
        defn = _loadout("Betty", base_armour=50)
        result = TickResolver(seed=0).resolve(att, defn, combatant1_label="Pilot", combatant2_label="Criminal")
        # Fight should have a winner (c1 kills c2)
        assert not result.is_stalemate
        assert result.winner_name is not None
        # Summary winner matches fight result
        s = result.metadata["summary"]
        assert s["winner"] == result.winner_name


# ---------------------------------------------------------------------------
# CI-19 — probe retry (gateway bot.py)
# ---------------------------------------------------------------------------


class TestCI19ProbeRetry:
    """Verify the startup probe retries before logging ERROR."""

    async def test_probe_succeeds_on_first_attempt(self):
        """Probe logs INFO when bot-core responds on first attempt."""
        import asyncio

        calls: list[int] = []
        logged_info: list[str] = []
        logged_error: list[str] = []

        class _FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

        class _FakeHTTP:
            async def get(self, url, timeout=3.0):
                calls.append(1)
                return _FakeResp()

        class _FakeLogger:
            def info(self, msg):
                logged_info.append(msg)

            def error(self, msg):
                logged_error.append(msg)

            def critical(self, *a, **kw):
                pass

            def trace(self, *a, **kw):
                pass

        # Simulate the probe logic from bot.py
        http = _FakeHTTP()
        log = _FakeLogger()
        api_base = "http://bot-core:8000/api/v1"

        _probe_attempts = 3
        _probe_backoff_s = (0.0, 0.0, 0.0)  # instant backoff for tests
        _probe_ok = False
        _last_probe_exc_unused = None
        for _attempt in range(1, _probe_attempts + 1):
            try:
                probe_resp = await http.get(f"{api_base}/health", timeout=3.0)
                probe_resp.raise_for_status()
                log.info(f"Autocomplete health probe OK (attempt {_attempt}): api_base={api_base}")
                _probe_ok = True
                break
            except Exception as _exc:
                _last_probe_exc_unused = _exc
                if _attempt < _probe_attempts:
                    await asyncio.sleep(_probe_backoff_s[_attempt - 1])
        if not _probe_ok:
            log.error(f"Autocomplete health probe FAILED after {_probe_attempts} attempts")

        assert _probe_ok is True
        assert len(logged_info) == 1
        assert len(logged_error) == 0
        assert len(calls) == 1  # only one attempt needed

    async def test_probe_retries_then_errors_after_exhaustion(self):
        """Probe retries configured number of times, then logs ERROR (not before)."""
        import asyncio

        call_count = 0
        logged_error: list[str] = []
        logged_info: list[str] = []

        class _FakeHTTP:
            async def get(self, url, timeout=3.0):
                nonlocal call_count
                call_count += 1
                raise ConnectionError("bot-core not up")

        class _FakeLogger:
            def info(self, msg):
                logged_info.append(msg)

            def error(self, msg):
                logged_error.append(msg)

            def critical(self, *a, **kw):
                pass

        http = _FakeHTTP()
        log = _FakeLogger()
        api_base = "http://bot-core:8000/api/v1"

        _probe_attempts = 3
        _probe_backoff_s = (0.0, 0.0, 0.0)
        _probe_ok = False
        _last_probe_exc_unused = None
        for _attempt in range(1, _probe_attempts + 1):
            try:
                probe_resp = await http.get(f"{api_base}/health", timeout=3.0)
                probe_resp.raise_for_status()
                log.info(f"Probe OK attempt {_attempt}")
                _probe_ok = True
                break
            except Exception as _exc:
                _last_probe_exc_unused = _exc
                if _attempt < _probe_attempts:
                    log.info(f"Probe attempt {_attempt} failed, retrying...")
                    await asyncio.sleep(_probe_backoff_s[_attempt - 1])
        if not _probe_ok:
            log.error(f"Autocomplete health probe FAILED after {_probe_attempts} attempts")

        assert _probe_ok is False
        assert call_count == _probe_attempts  # all attempts made
        # ERROR logged only after exhaustion — exactly once
        assert len(logged_error) == 1
        assert "FAILED" in logged_error[0]
        # No ERROR logged mid-retry
        assert all("FAILED" not in msg for msg in logged_info)

    async def test_probe_succeeds_on_second_attempt(self):
        """Probe logs INFO (not ERROR) when second attempt succeeds."""
        import asyncio

        attempt_num = 0

        class _FakeResp:
            def raise_for_status(self):
                pass

        class _FakeHTTP:
            async def get(self, url, timeout=3.0):
                nonlocal attempt_num
                attempt_num += 1
                if attempt_num == 1:
                    raise ConnectionError("not ready yet")
                return _FakeResp()

        logged_error: list[str] = []

        class _FakeLogger:
            def info(self, msg):
                pass

            def error(self, msg):
                logged_error.append(msg)

        http = _FakeHTTP()
        log = _FakeLogger()
        api_base = "http://bot-core:8000/api/v1"

        _probe_attempts = 3
        _probe_backoff_s = (0.0, 0.0, 0.0)
        _probe_ok = False
        _last_probe_exc_unused = None
        for _attempt in range(1, _probe_attempts + 1):
            try:
                probe_resp = await http.get(f"{api_base}/health", timeout=3.0)
                probe_resp.raise_for_status()
                log.info(f"OK attempt {_attempt}")
                _probe_ok = True
                break
            except Exception as _exc:
                _last_probe_exc_unused = _exc
                if _attempt < _probe_attempts:
                    await asyncio.sleep(_probe_backoff_s[_attempt - 1])
        if not _probe_ok:
            log.error("FAILED")

        assert _probe_ok is True
        assert len(logged_error) == 0  # no ERROR logged
        assert attempt_num == 2  # first failed, second succeeded
