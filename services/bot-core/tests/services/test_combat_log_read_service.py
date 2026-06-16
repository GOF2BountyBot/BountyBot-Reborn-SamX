"""Tests for CombatLogService read path (list_for_player, get_detail, _pov_outcome).

Covers:
  - list_for_player: guild-scoped listing, ordinal disambiguation
  - get_detail: happy path, ownership gate (non-combatant → KeyError)
  - _pov_outcome: win/loss determination including same-ship-name case, stalemate
  - _extract_key_events: secondary fires, module activations, layer-depleted milestones
  - NPC fight excluded from wrong player's list; included for player side

Max 2 mocks per test.
"""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Guard: mock shared.bblogger before any src imports
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
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import pytest
from services.combat_log_service import CombatLogService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(
    *,
    row_id: int = 1,
    guild_id: int = 699744305274945650,
    context: str = "duel",
    c1_name: str = "Betty",
    c2_name: str = "Betty",
    c1_user_id: int | None = 402296276617527306,
    c2_user_id: int | None = 970691862035841048,
    winner_name: str | None = "Betty",
    is_stalemate: bool = False,
    created_at: datetime | None = None,
    c1_final_hull: int = 50,
    c2_final_hull: int = 0,
    timeline: list | None = None,
):
    """Build a MagicMock that looks like a CombatLog ORM row."""
    row = MagicMock()
    row.id = row_id
    row.guild_id = guild_id
    row.context = context
    row.combatant1_name = c1_name
    row.combatant2_name = c2_name
    row.combatant1_user_id = c1_user_id
    row.combatant2_user_id = c2_user_id
    row.winner_name = winner_name
    row.is_stalemate = is_stalemate
    row.created_at = created_at or datetime.now(UTC)

    # Build data blob mirroring the real schema
    row.data = {
        "schema_version": 1,
        "summary": {
            "reason": "hp_depleted",
            "winner": winner_name,
            "outcome": "stalemate" if is_stalemate else "win",
            "combatants": {
                "1": {
                    "name": c1_name,
                    "ship": c1_name,
                    "start_hp": {"hull": 95, "armour": 40, "shield": 0},
                    "final_hp": {"hull": c1_final_hull, "armour": 20, "shield": 0},
                    "shots_fired": 60,
                    "shots_hit": 40,
                    "damage_dealt": 120,
                    "damage_taken": 80,
                },
                "2": {
                    "name": c2_name,
                    "ship": c2_name,
                    "start_hp": {"hull": 95, "armour": 40, "shield": 0},
                    "final_hp": {"hull": c2_final_hull, "armour": 0, "shield": 0},
                    "shots_fired": 55,
                    "shots_hit": 35,
                    "damage_dealt": 80,
                    "damage_taken": 120,
                },
            },
            "duration_ticks": 3488,
        },
        "timeline": timeline if timeline is not None else [],
        "metadata": {
            "tick_ms": 10,
            "resolver": "tick_v1",
            "total_ticks": 3488,
            "pvc_damage_reduction": 0.0,
        },
    }
    return row


def _make_sub_row(row: MagicMock, *, key_events: list | None = None) -> MagicMock:
    """Build a MagicMock that looks like a get_subpath_for_detail Row namedtuple.

    P4-T7b: get_detail now calls get_subpath_for_detail() first and gets back a
    sub-path Row with fields: id, guild_id, context, combatant1/2_name/user_id,
    winner_name, is_stalemate, created_at, summary, metadata, key_events.
    Pass key_events=None to simulate a legacy row (triggers full-row fallback).
    """
    sub = MagicMock()
    sub.id = row.id
    sub.guild_id = row.guild_id
    sub.context = row.context
    sub.combatant1_name = row.combatant1_name
    sub.combatant2_name = row.combatant2_name
    sub.combatant1_user_id = row.combatant1_user_id
    sub.combatant2_user_id = row.combatant2_user_id
    sub.winner_name = row.winner_name
    sub.is_stalemate = row.is_stalemate
    sub.created_at = row.created_at
    sub.summary = row.data["summary"]
    sub.metadata = row.data["metadata"]
    sub.key_events = key_events
    return sub


# ---------------------------------------------------------------------------
# Tests: _pov_outcome
# ---------------------------------------------------------------------------


class TestPovOutcome:
    def test_c1_wins_c2_dead(self):
        row = _make_row(c1_user_id=100, c2_user_id=200, c1_final_hull=50, c2_final_hull=0)
        opponent, outcome = CombatLogService._pov_outcome(row, user_id=100)
        assert outcome == "won"
        assert opponent == row.combatant2_name

    def test_c2_wins_c1_dead(self):
        row = _make_row(c1_user_id=100, c2_user_id=200, c1_final_hull=0, c2_final_hull=50)
        opponent, outcome = CombatLogService._pov_outcome(row, user_id=200)
        assert outcome == "won"
        assert opponent == row.combatant1_name

    def test_c1_lost(self):
        row = _make_row(c1_user_id=100, c2_user_id=200, c1_final_hull=0, c2_final_hull=50)
        _opp, outcome = CombatLogService._pov_outcome(row, user_id=100)
        assert outcome == "lost"

    def test_c2_lost(self):
        row = _make_row(c1_user_id=100, c2_user_id=200, c1_final_hull=50, c2_final_hull=0)
        _opp, outcome = CombatLogService._pov_outcome(row, user_id=200)
        assert outcome == "lost"

    def test_stalemate(self):
        row = _make_row(c1_user_id=100, c2_user_id=200, is_stalemate=True, winner_name=None)
        _opp, outcome = CombatLogService._pov_outcome(row, user_id=100)
        assert outcome == "stalemate"

    def test_same_ship_name_c1_wins(self):
        """Both ships named 'Betty' — must use final_hp not string match."""
        row = _make_row(
            c1_name="Betty",
            c2_name="Betty",
            winner_name="Betty",  # ambiguous string!
            c1_user_id=402296276617527306,
            c2_user_id=970691862035841048,
            c1_final_hull=95,  # c1 survived
            c2_final_hull=0,  # c2 died
        )
        _opp, outcome = CombatLogService._pov_outcome(row, user_id=402296276617527306)
        assert outcome == "won"

    def test_same_ship_name_c2_wins(self):
        """Both ships named 'Betty' — c2 survived, c1 died."""
        row = _make_row(
            c1_name="Betty",
            c2_name="Betty",
            winner_name="Betty",
            c1_user_id=402296276617527306,
            c2_user_id=970691862035841048,
            c1_final_hull=0,
            c2_final_hull=95,
        )
        _opp, outcome = CombatLogService._pov_outcome(row, user_id=402296276617527306)
        assert outcome == "lost"

    def test_pvc_fight_c1_is_player(self):
        """PvC fight: c2_user_id is None, player is c1."""
        row = _make_row(
            c1_user_id=402296276617527306,
            c2_user_id=None,
            c1_final_hull=95,
            c2_final_hull=0,
        )
        opp, outcome = CombatLogService._pov_outcome(row, user_id=402296276617527306)
        assert outcome == "won"
        assert opp == row.combatant2_name


# ---------------------------------------------------------------------------
# Tests: _extract_key_events
# ---------------------------------------------------------------------------


class TestExtractKeyEvents:
    def _make_weapon_fire_event(self, tick, actor, slot, subtype, weapon, hit=True):
        return {
            "tick": tick,
            "type": "weapon_fire",
            "actor": actor,
            "target": "Opponent",
            "data": {"slot": slot, "subtype": subtype, "weapon": weapon, "hit": hit},
        }

    def _make_module_event(self, tick, actor, module_name):
        return {
            "tick": tick,
            "type": "module_activation",
            "actor": actor,
            "target": None,
            "data": {"module": module_name, "module_type": "CloakModule"},
        }

    def _make_layer_event(self, tick, actor, layer):
        return {
            "tick": tick,
            "type": "layer_depleted",
            "actor": actor,
            "target": None,
            "data": {"layer": layer},
        }

    def test_secondary_fires_included(self):
        """Secondaries (rocket) appear as 'Weapon in range' R1 lines; nuke as 'Nuke detonation'."""
        events = [
            self._make_weapon_fire_event(100, "Betty", "secondary", "rocket", "Rockets MK1"),
            # Nuke: per-fire beat with opponent_damage / self_damage
            {
                "tick": 200,
                "type": "weapon_fire",
                "actor": "Betty",
                "target": "Opponent",
                "data": {
                    "slot": "secondary",
                    "subtype": "nuke",
                    "weapon": "Nuke",
                    "opponent_damage": 80,
                    "self_damage": 5,
                },
            },
        ]
        result = CombatLogService._extract_key_events(events)
        assert len(result) == 2
        # Rocket → "Weapon in range" (R1 range-in beat)
        assert result[0]["event_type"] == "Weapon in range"
        assert "enters range" in result[0]["detail"]
        assert "hit" in result[0]["detail"]
        # Nuke → "Nuke detonation" per-fire beat with detonated/damage detail
        assert result[1]["event_type"] == "Nuke detonation"
        assert "detonated" in result[1]["detail"]
        assert "80" in result[1]["detail"]  # opponent damage shown

    def test_primary_fires_included_as_weapon_in_range(self):
        """Primary weapon_fire (any slot, with or without side=) now produces a 'Weapon in range' event."""
        events = [
            self._make_weapon_fire_event(100, "Betty", "primary", "primary", "Nirai Impulse EX 1"),
        ]
        result = CombatLogService._extract_key_events(events)
        # Primaries are now included as range-in beats (not excluded like before)
        assert len(result) == 1
        assert result[0]["event_type"] == "Weapon in range"
        assert "Nirai Impulse EX 1" in result[0]["detail"]

    def test_primary_fires_with_side_produces_weapon_in_range_not_first_hit(self):
        """Primary weapon_fire WITH side=1, hit=True produces a 'Weapon in range' line; 'First hit' is GONE."""
        events = [
            {
                "tick": 100,
                "type": "weapon_fire",
                "actor": "Betty",
                "target": "Opponent",
                "data": {
                    "slot": "primary",
                    "subtype": "primary",
                    "weapon": "Nirai Impulse EX 1",
                    "hit": True,
                    "side": 1,
                },
            },
        ]
        result = CombatLogService._extract_key_events(events)
        # No "First hit" event — that event type is GONE
        first_hits = [e for e in result if e["event_type"] == "First hit"]
        assert len(first_hits) == 0
        # Instead: "Weapon in range" range-in beat
        wir = [e for e in result if e["event_type"] == "Weapon in range"]
        assert len(wir) == 1
        assert "Nirai Impulse EX 1" in wir[0]["detail"]

    def test_module_activations_included(self):
        events = [self._make_module_event(500, "Betty", "U'tool")]
        result = CombatLogService._extract_key_events(events)
        assert len(result) == 1
        assert result[0]["event_type"] == "Module activated"
        assert "U'tool" in result[0]["detail"]

    def test_layer_depleted_shield(self):
        """Shield depletion → event_type 'Layer depleted', detail contains 'Shield depleted'."""
        events = [self._make_layer_event(800, "Betty", "shield")]
        result = CombatLogService._extract_key_events(events)
        assert len(result) == 1
        assert result[0]["event_type"] == "Layer depleted"
        assert "Shield depleted" in result[0]["detail"]

    def test_layer_depleted_armour(self):
        """Armour depletion → event_type 'Layer depleted', detail contains 'Armour depleted'."""
        events = [self._make_layer_event(1200, "Betty", "armour")]
        result = CombatLogService._extract_key_events(events)
        assert result[0]["event_type"] == "Layer depleted"
        assert "Armour depleted" in result[0]["detail"]

    def test_layer_depleted_hull_not_emitted(self):
        """Hull depletion is NOT emitted as a key event — the kill is shown by the Outcome line only."""
        events = [self._make_layer_event(3488, "Betty", "hull")]
        result = CombatLogService._extract_key_events(events)
        # Hull layer_depleted is suppressed; no event emitted
        assert len(result) == 0

    def test_time_conversion(self):
        """Tick 100 at 10ms/tick = 1.0 seconds."""
        events = [self._make_layer_event(100, "Betty", "armour")]
        result = CombatLogService._extract_key_events(events, tick_ms=10)
        assert result[0]["time_s"] == 1.0

    def test_non_notable_events_ignored(self):
        """distance and regen are not key events; fight_start/fight_end generate baseline lines."""
        events = [
            {"tick": 50, "type": "distance", "actor": None, "target": None, "data": {}},
            {"tick": 100, "type": "regen", "actor": "Betty", "target": None, "data": {"layer": "shield"}},
        ]
        result = CombatLogService._extract_key_events(events)
        assert len(result) == 0

    def test_fight_start_generates_engagement_line(self):
        """fight_start → Engagement baseline line (CI-22 Tier A)."""
        events = [
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
                            "hp": {"hull": 100, "armour": 50, "shield": 0},
                        },
                        {
                            "name": "Vossk",
                            "display_name": "H'Soc",
                            "ship": "Vossk",
                            "hp": {"hull": 80, "armour": 30, "shield": 20},
                        },
                    ],
                    "initial_distance": 5000,
                },
            },
        ]
        result = CombatLogService._extract_key_events(events)
        assert len(result) == 1
        assert result[0]["event_type"] == "Engagement"
        assert "SamX" in result[0]["detail"]
        assert "H'Soc" in result[0]["detail"]

    def test_fight_end_generates_outcome_line(self):
        """fight_end → Outcome baseline line (CI-22 Tier A)."""
        events = [
            {
                "tick": 3488,
                "type": "fight_end",
                "actor": None,
                "target": None,
                "data": {"winner": "Betty", "reason": "hp_depleted", "duration_ticks": 3488},
            },
        ]
        result = CombatLogService._extract_key_events(events)
        assert len(result) == 1
        assert result[0]["event_type"] == "Outcome"
        assert "Betty" in result[0]["detail"]

    def test_fight_end_same_ship_name_c2_wins_correct_label(self):
        """CI-22 regression: same-ship-name fight where c2 wins must show c2's display name.

        Both ships are named 'Betty'; winner string is ambiguous.  The fix resolves
        the winner slot from final_hp (c1.hull=0, c2.hull=95) → c2 won → 'H'Soc wins'.
        Pre-fix this always picked c1's label ('SamX wins') — wrong when c2 won.
        """
        combatants_map = {
            "1": {"name": "SamX", "ship": "Betty"},
            "2": {"name": "H'Soc", "ship": "Betty"},
        }
        events = [
            {
                "tick": 3488,
                "type": "fight_end",
                "actor": None,
                "target": None,
                "data": {
                    "winner": "Betty",  # ambiguous — both ships named Betty
                    "reason": "hp_depleted",
                    "duration_ticks": 3488,
                    "final_hp": {
                        "c1": {"hull": 0, "armour": 0, "shield": 0},  # c1 (SamX) died
                        "c2": {"hull": 95, "armour": 20, "shield": 0},  # c2 (H'Soc) survived
                    },
                },
            },
        ]
        result = CombatLogService._extract_key_events(events, combatants_map=combatants_map)
        assert len(result) == 1
        outcome = result[0]
        assert outcome["event_type"] == "Outcome"
        # H'Soc (slot 2) won; SamX (slot 1) lost — label must reflect this
        assert "H'Soc wins" in outcome["detail"], f'Expected "H\'Soc wins" in outcome but got: {outcome["detail"]!r}'
        assert "SamX" in outcome["detail"], f"Expected 'SamX' (the loser) in outcome but got: {outcome['detail']!r}"

    def test_fight_end_stalemate_outcome_line(self):
        """fight_end with no winner → Stalemate outcome line."""
        events = [
            {
                "tick": 18000,
                "type": "fight_end",
                "actor": None,
                "target": None,
                "data": {"winner": None, "reason": "time_cap", "duration_ticks": 18000},
            },
        ]
        result = CombatLogService._extract_key_events(events)
        assert len(result) == 1
        assert result[0]["event_type"] == "Outcome"
        assert "Stalemate" in result[0]["detail"]

    def test_same_tick_causal_order_preserved(self):
        """CI-29: same-tick events must come out in causal sub-order.

        Scenario at tick 1200: the resolver emits in this order:
          1. weapon_fire (secondary hit) — now "Weapon in range" (range-in beat, k=1)
          2. layer_depleted (armour) — now "Layer depleted" (effect beat, k=2)
          3. secondary_depleted — "Ammo depleted" (ammo-out beat, k=4)
          4. damage → crosses the 50% HP milestone — "HP milestone (50%)" (effect beat, k=2)

        The causal sub-order keys (k values) define the within-tick ordering:
          weapon action (k=1) → effect/milestone (k=2) → ammo-out (k=4)

        NOTE: HP milestone fires BEFORE ammo-out because damage processing (k=2) happens
        before secondary_depleted (k=4) in the causal ordering. The layer_depleted for
        slot 2 also fires at k=2 but precedes the milestone because the layer event comes
        earlier in the timeline scan.
        """
        tick = 1200
        events = [
            # fight_start at tick 0 to seed HP tracking
            {
                "tick": 0,
                "type": "fight_start",
                "actor": None,
                "target": None,
                "data": {
                    "combatants": [
                        {
                            "name": "Alice",
                            "ship": "Wraith",
                            "hp": {"hull": 100, "armour": 50, "shield": 0},
                        },  # total = 150
                        {"name": "Bob", "ship": "Centaur", "hp": {"hull": 100, "armour": 50, "shield": 0}},
                    ],
                    "initial_distance": 3000,
                },
            },
            # 1. secondary weapon_fire hit (causal first)
            {
                "tick": tick,
                "type": "weapon_fire",
                "actor": "Alice",
                "target": "Bob",
                "data": {
                    "slot": "secondary",
                    "subtype": "rocket",
                    "weapon": "S'koon",
                    "hit": True,
                },
            },
            # 2. layer_depleted for the target (armour depleted by that hit)
            {
                "tick": tick,
                "type": "layer_depleted",
                "actor": "Bob",
                "target": None,
                "data": {"layer": "armour", "side": 2},
            },
            # 3. secondary_depleted (ran out of ammo after firing)
            {
                "tick": tick,
                "type": "secondary_depleted",
                "actor": "Alice",
                "target": None,
                "data": {"weapon": "S'koon"},
            },
            # 4. damage event that crosses 50% HP milestone for side 1
            {
                "tick": tick,
                "type": "damage",
                "actor": "Bob",
                "target": "Alice",
                "data": {
                    "side": 1,
                    "hp_after": {"hull": 74, "armour": 0, "shield": 0},  # 74/150 ≈ 49% → crosses 50%
                },
            },
        ]

        result = CombatLogService._extract_key_events(events)

        # Filter to same-tick events only (tick 1200)
        same_tick = [e for e in result if e["tick"] == tick]

        # Expected causal sub-order (k values): Weapon in range (k=1) → Layer depleted (k=2)
        # → HP milestone (k=2, comes after layer) → Ammo depleted (k=4)
        event_types = [e["event_type"] for e in same_tick]
        expected_order = [
            "Weapon in range",
            "Layer depleted",
            "HP milestone (50%)",
            "Ammo depleted",
        ]
        assert event_types == expected_order, (
            f"Causal order violated. Expected {expected_order}, got {event_types}. "
            "Hint: sub-order keys must be weapon=1, effect=2, ammo-out=4."
        )

        # Global invariant: Engagement is first, Outcome absent (no fight_end here)
        assert result[0]["event_type"] == "Engagement"

    def test_engagement_first_outcome_last(self):
        """Engagement (tick 0) must always be first; Outcome (last tick) must be last
        regardless of what events appear in between.

        This verifies the stable tick-sort doesn't shuffle boundary events.
        """
        events = [
            {
                "tick": 0,
                "type": "fight_start",
                "actor": None,
                "target": None,
                "data": {
                    "combatants": [
                        {"name": "A", "ship": "X", "hp": {"hull": 100, "armour": 0, "shield": 0}},
                        {"name": "B", "ship": "Y", "hp": {"hull": 100, "armour": 0, "shield": 0}},
                    ],
                    "initial_distance": 1000,
                },
            },
            # Mid-fight event at tick 500
            {
                "tick": 500,
                "type": "layer_depleted",
                "actor": "A",
                "target": None,
                "data": {"layer": "shield"},
            },
            # fight_end at tick 3000 — must be last after stable sort
            {
                "tick": 3000,
                "type": "fight_end",
                "actor": None,
                "target": None,
                "data": {
                    "winner": "A",
                    "reason": "hp_depleted",
                    "duration_ticks": 3000,
                    "final_hp": {
                        "c1": {"hull": 40, "armour": 0, "shield": 0},
                        "c2": {"hull": 0, "armour": 0, "shield": 0},
                    },
                },
            },
        ]
        result = CombatLogService._extract_key_events(events)
        assert len(result) >= 3
        assert result[0]["event_type"] == "Engagement"
        assert result[-1]["event_type"] == "Outcome"


# ---------------------------------------------------------------------------
# Tests: list_for_player (service layer)
# ---------------------------------------------------------------------------


class TestListForPlayer:
    async def test_returns_list_items_with_ordinals(self):
        """list_for_player builds correct dicts and disambiguates ordinals."""
        svc = CombatLogService()
        # Two fights vs same opponent on same day — should get ordinals 1 and 2
        now = datetime(2026, 6, 3, 15, 0, 0, tzinfo=UTC)
        earlier = datetime(2026, 6, 3, 10, 0, 0, tzinfo=UTC)
        row_a = _make_row(row_id=10, c1_user_id=100, c2_user_id=200, c2_name="Foe", created_at=now)
        row_b = _make_row(row_id=11, c1_user_id=100, c2_user_id=200, c2_name="Foe", created_at=earlier)

        mock_repo = AsyncMock()
        mock_repo.list_for_player = AsyncMock(return_value=[row_a, row_b])
        svc._repo = mock_repo

        items = await svc.list_for_player(MagicMock(), user_id=100, guild_id=9999)
        assert len(items) == 2
        # Most-recent row gets higher ordinal
        ordinals = {item["id"]: item["ordinal"] for item in items}
        assert ordinals[10] > ordinals[11]

    async def test_different_day_gets_ordinal_1(self):
        """Fights on different days each get ordinal=1."""
        svc = CombatLogService()
        day1 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        day2 = datetime(2026, 6, 2, 12, 0, 0, tzinfo=UTC)
        row_a = _make_row(row_id=20, c1_user_id=100, c2_user_id=200, c2_name="Foe", created_at=day1)
        row_b = _make_row(row_id=21, c1_user_id=100, c2_user_id=200, c2_name="Foe", created_at=day2)

        mock_repo = AsyncMock()
        mock_repo.list_for_player = AsyncMock(return_value=[row_b, row_a])
        svc._repo = mock_repo

        items = await svc.list_for_player(MagicMock(), user_id=100, guild_id=9999)
        for item in items:
            assert item["ordinal"] == 1

    async def test_npc_fight_included_for_player(self):
        """NPC fight (c2_user_id=None) is visible for the player side."""
        svc = CombatLogService()
        npc_row = _make_row(
            row_id=30,
            c1_user_id=100,
            c2_user_id=None,
            c2_name="Vossk Soldier",
            c1_final_hull=95,
            c2_final_hull=0,
        )

        mock_repo = AsyncMock()
        mock_repo.list_for_player = AsyncMock(return_value=[npc_row])
        svc._repo = mock_repo

        items = await svc.list_for_player(MagicMock(), user_id=100, guild_id=9999)
        assert len(items) == 1
        assert items[0]["opponent_name"] == "Vossk Soldier"
        assert items[0]["outcome"] == "won"

    async def test_npc_fight_not_seen_by_other_user(self):
        """A user who is NOT a combatant gets empty list from repo (repo-level enforcement)."""
        svc = CombatLogService()
        mock_repo = AsyncMock()
        mock_repo.list_for_player = AsyncMock(return_value=[])
        svc._repo = mock_repo

        items = await svc.list_for_player(MagicMock(), user_id=999, guild_id=9999)
        assert items == []

    async def test_list_items_include_combatant_names(self):
        """CI-20: each list item must contain combatant1_name and combatant2_name from the row.

        This is the regression test for the gap: the schema was stripping these fields
        because they were absent from CombatLogListItem. The service has always emitted
        them; the schema fix now preserves them end-to-end.
        """
        svc = CombatLogService()
        row = _make_row(
            row_id=57,
            c1_name="General_Failure",
            c2_name="Bartholomeu Drew",
            c1_user_id=100,
            c2_user_id=200,
        )

        mock_repo = AsyncMock()
        mock_repo.list_for_player = AsyncMock(return_value=[row])
        svc._repo = mock_repo

        items = await svc.list_for_player(MagicMock(), user_id=100, guild_id=9999)
        assert len(items) == 1
        item = items[0]
        # Both names must be present and match the stored row values
        assert item["combatant1_name"] == "General_Failure"
        assert item["combatant2_name"] == "Bartholomeu Drew"

    async def test_list_item_combatant_names_survive_schema_validation(self):
        """CI-20: CombatLogListItem schema must NOT strip combatant1_name/combatant2_name.

        This tests the exact failure mode: if the schema omits the fields, Pydantic
        would silently drop them and the gateway would fall back to the old 'vs <opponent>'
        label format.
        """
        from datetime import UTC, datetime

        from api.schemas.combat_log_schema import CombatLogListItem

        raw = {
            "id": 57,
            "guild_id": 699744305274945650,
            "context": "duel",
            "opponent_name": "Bartholomeu Drew",
            "combatant1_name": "General_Failure",
            "combatant2_name": "Bartholomeu Drew",
            "outcome": "won",
            "created_at": datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC),
            "ordinal": 1,
        }
        item = CombatLogListItem(**raw)
        assert item.combatant1_name == "General_Failure"
        assert item.combatant2_name == "Bartholomeu Drew"

    async def test_list_item_schema_allows_none_combatant_names(self):
        """CI-20: old rows without combatant names → fields are None, gateway falls back gracefully."""
        from datetime import UTC, datetime

        from api.schemas.combat_log_schema import CombatLogListItem

        raw = {
            "id": 1,
            "guild_id": 699744305274945650,
            "context": "duel",
            "opponent_name": "SomeFoe",
            "outcome": "lost",
            "created_at": datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC),
            "ordinal": 1,
            # combatant1_name and combatant2_name intentionally absent (legacy row)
        }
        item = CombatLogListItem(**raw)
        assert item.combatant1_name is None
        assert item.combatant2_name is None


# ---------------------------------------------------------------------------
# Tests: get_detail
# ---------------------------------------------------------------------------


class TestGetDetail:
    async def test_happy_path(self):
        # P4-T7b: get_detail now calls get_subpath_for_detail first (fast path).
        # Provide a sub-path row with stored key_events to exercise the fast path.
        svc = CombatLogService()
        row = _make_row(row_id=1, c1_user_id=100, c2_user_id=200, c1_final_hull=50, c2_final_hull=0)
        stored_ke = [{"tick": 0, "event_type": "Engagement", "time_s": 0.0, "actor": None, "detail": "test"}]
        sub = _make_sub_row(row, key_events=stored_ke)
        mock_repo = AsyncMock()
        mock_repo.get_subpath_for_detail = AsyncMock(return_value=sub)
        svc._repo = mock_repo

        detail = await svc.get_detail(MagicMock(), battle_id=1, user_id=100)
        assert detail["id"] == 1
        assert detail["outcome"] == "won"
        assert detail["combatant1"]["name"] == row.combatant1_name

    async def test_ownership_gate_non_combatant_raises_key_error(self):
        """User not in the fight gets KeyError (→ 404).

        P4-T7b: ownership gate is applied on the sub-path row (no full-row load needed).
        """
        svc = CombatLogService()
        row = _make_row(row_id=2, c1_user_id=100, c2_user_id=200)
        sub = _make_sub_row(row, key_events=[])
        mock_repo = AsyncMock()
        mock_repo.get_subpath_for_detail = AsyncMock(return_value=sub)
        svc._repo = mock_repo

        with pytest.raises(KeyError):
            await svc.get_detail(MagicMock(), battle_id=2, user_id=999)

    async def test_not_found_raises_key_error(self):
        """Non-existent battle_id also raises KeyError (→ 404).

        P4-T7b: get_subpath_for_detail returns None → KeyError (same as T7a).
        """
        svc = CombatLogService()
        mock_repo = AsyncMock()
        mock_repo.get_subpath_for_detail = AsyncMock(return_value=None)
        svc._repo = mock_repo

        with pytest.raises(KeyError):
            await svc.get_detail(MagicMock(), battle_id=9999, user_id=100)

    async def test_pvc_damage_reduction_included(self):
        # P4-T7b fast path: metadata comes from the sub-path row directly.
        svc = CombatLogService()
        row = _make_row(row_id=3, c1_user_id=100, c2_user_id=None)
        row.data["metadata"]["pvc_damage_reduction"] = 0.33
        stored_ke = []
        sub = _make_sub_row(row, key_events=stored_ke)
        sub.metadata = dict(row.data["metadata"])  # include updated pvc_damage_reduction
        mock_repo = AsyncMock()
        mock_repo.get_subpath_for_detail = AsyncMock(return_value=sub)
        svc._repo = mock_repo

        detail = await svc.get_detail(MagicMock(), battle_id=3, user_id=100)
        assert detail["pvc_damage_reduction"] == 0.33

    async def test_key_events_extraction(self):
        """Key events are extracted from the timeline (legacy-row fallback path).

        P4-T7b: when get_subpath_for_detail returns key_events=None (legacy row),
        get_detail falls back to a full get_by_id load + _extract_key_events.

        NEW behavior: layer_depleted → event_type "Layer depleted"; rocket fires
        → "Weapon in range" range-in beats.
        """
        timeline = [
            {"tick": 100, "type": "layer_depleted", "actor": "Betty", "target": None, "data": {"layer": "armour"}},
            {
                "tick": 200,
                "type": "weapon_fire",
                "actor": "Betty",
                "target": "Foe",
                "data": {"slot": "secondary", "subtype": "rocket", "weapon": "Rockets MK1", "hit": True},
            },
        ]
        svc = CombatLogService()
        row = _make_row(row_id=4, c1_user_id=100, c2_user_id=200, timeline=timeline)
        # Simulate legacy row: sub-path returns key_events=None
        sub = _make_sub_row(row, key_events=None)
        mock_repo = AsyncMock()
        mock_repo.get_subpath_for_detail = AsyncMock(return_value=sub)
        mock_repo.get_by_id = AsyncMock(return_value=row)
        svc._repo = mock_repo

        detail = await svc.get_detail(MagicMock(), battle_id=4, user_id=100)
        assert len(detail["key_events"]) == 2
        types_found = {ev["event_type"] for ev in detail["key_events"]}
        # New event types: "Layer depleted" (not "Armour depleted"), "Weapon in range" (not "Secondary fire (rocket)")
        assert "Layer depleted" in types_found
        assert "Weapon in range" in types_found

    async def test_stalemate_outcome(self):
        # P4-T7b fast path: stalemate detected via sub-path row is_stalemate flag.
        svc = CombatLogService()
        row = _make_row(row_id=5, c1_user_id=100, c2_user_id=200, is_stalemate=True, winner_name=None)
        row.data["summary"]["winner"] = None
        stored_ke = []
        sub = _make_sub_row(row, key_events=stored_ke)
        mock_repo = AsyncMock()
        mock_repo.get_subpath_for_detail = AsyncMock(return_value=sub)
        svc._repo = mock_repo

        detail = await svc.get_detail(MagicMock(), battle_id=5, user_id=100)
        assert detail["outcome"] == "stalemate"

    async def test_accuracy_computed(self):
        """accuracy = shots_hit / shots_fired (fast path)."""
        svc = CombatLogService()
        row = _make_row(row_id=6, c1_user_id=100, c2_user_id=200)
        # Override c1 shots for a known ratio
        row.data["summary"]["combatants"]["1"]["shots_fired"] = 100
        row.data["summary"]["combatants"]["1"]["shots_hit"] = 65
        stored_ke = []
        sub = _make_sub_row(row, key_events=stored_ke)
        mock_repo = AsyncMock()
        mock_repo.get_subpath_for_detail = AsyncMock(return_value=sub)
        svc._repo = mock_repo

        detail = await svc.get_detail(MagicMock(), battle_id=6, user_id=100)
        assert detail["combatant1"]["accuracy"] == pytest.approx(0.65)

    async def test_accuracy_none_when_no_shots(self):
        """accuracy is None when shots_fired == 0 (fast path)."""
        svc = CombatLogService()
        row = _make_row(row_id=7, c1_user_id=100, c2_user_id=200)
        row.data["summary"]["combatants"]["1"]["shots_fired"] = 0
        row.data["summary"]["combatants"]["1"]["shots_hit"] = 0
        stored_ke = []
        sub = _make_sub_row(row, key_events=stored_ke)
        mock_repo = AsyncMock()
        mock_repo.get_subpath_for_detail = AsyncMock(return_value=sub)
        svc._repo = mock_repo

        detail = await svc.get_detail(MagicMock(), battle_id=7, user_id=100)
        assert detail["combatant1"]["accuracy"] is None
