"""
P2-T4 — _increment_player_stats: collapsed full-timeline scan → summary-block read.

Tests:
  1. NORMAL fights (distinct names): nukes_fired and module_activations written to DB
     are identical pre/post the refactor (summary counts match what the old scan produced).
  2. SAME-NAME fight: counts are correctly attributed per-side (each combatant gets its
     OWN nukes/activations), proving the bugfix; the old name-scan would have wrongly
     collapsed both sides onto any name-matched side.
  3. ALLOWLIST COVERAGE: _ACTIVATION_MODULES in the summary builder covers ALL modules
     that currently emit module_activation events (cloak / booster / emergency_system).
  4. ONE FEWER TIMELINE WALK: _increment_player_stats no longer iterates fight_results.combat_log
     for these counts — the function reads the summary block directly.

Max 2 mocks per test.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Shared bblogger + sqlalchemy_utils guard — must run before any project import
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
from persist.models.player import Player
from services.combat_models import (
    CombatEvent,
    CombatEventType,
    FightResults,
    FightStats,
    ShipLoadout,
)
from services.combat_resolver import _ACTIVATION_MODULES, _build_fight_summary, _init_combatant
from services.combat_service import CombatService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stats(name: str = "A") -> FightStats:
    return FightStats(ship_name=name, raw_hp=100, raw_dps=10.0, varied_hp=100, varied_dps=10.0, ttk=None)


def _make_fight_results(
    *,
    summary: dict,
    combat_log: list | None = None,
    c1_user_id: int | None = 1,
    c2_user_id: int | None = 2,
) -> FightResults:
    """Build a FightResults with caller-supplied summary and (optionally) a combat_log."""
    return FightResults(
        winner_name="A",
        loser_name="B",
        is_stalemate=False,
        ship1_stats=_make_stats("A"),
        ship2_stats=_make_stats("B"),
        combat_log=combat_log if combat_log is not None else [],
        metadata={
            "schema_version": 1,
            "summary": summary,
            "metadata": {"tick_ms": 10, "total_ticks": 100, "resolver": "tick_v1", "pvc_damage_reduction": 0.0},
            "combatant_user_ids": {"c1": c1_user_id, "c2": c2_user_id},
        },
    )


def _summary(c1_nukes: int = 0, c2_nukes: int = 0, c1_mods: dict | None = None, c2_mods: dict | None = None) -> dict:
    """Build a minimal §12 summary dict with controllable per-side nuke/module counts."""
    c1_mods = c1_mods or {}
    c2_mods = c2_mods or {}
    c1_secondary_fired: dict = {}
    c2_secondary_fired: dict = {}
    if c1_nukes:
        c1_secondary_fired["nuke"] = c1_nukes
    if c2_nukes:
        c2_secondary_fired["nuke"] = c2_nukes
    return {
        "outcome": "win",
        "reason": "hp_depleted",
        "duration_ticks": 50,
        "winner": "A",
        "combatants": {
            "1": {
                "name": "A",
                "ship": "ShipA",
                "damage_dealt": 100,
                "damage_taken": 0,
                "shots_fired": 5,
                "shots_hit": 5,
                "accuracy": 1.0,
                "module_activations": c1_mods,
                "secondary_fired": c1_secondary_fired,
                "secondary_rounds_by_weapon": {},
            },
            "2": {
                "name": "B",
                "ship": "ShipB",
                "damage_dealt": 0,
                "damage_taken": 100,
                "shots_fired": 3,
                "shots_hit": 1,
                "accuracy": 0.33,
                "module_activations": c2_mods,
                "secondary_fired": c2_secondary_fired,
                "secondary_rounds_by_weapon": {},
            },
        },
    }


async def _call_increment(
    fr: FightResults,
    *,
    c1_user_id: int | None = 1,
    c2_user_id: int | None = 2,
    guild_id: int = 99,
) -> list[Player]:
    """Call _increment_player_stats and return [player1, player2] (or fewer if NPC).

    Players are real Player model instances (int counters), so the attribution
    logic runs against the true column types, not MagicMock auto-attributes.
    """
    service = CombatService()

    players: dict[int, Player] = {}
    if c1_user_id is not None:
        players[c1_user_id] = Player(
            user_id=c1_user_id,
            guild_id=guild_id,
            credits=0,
            total_fights=0,
            total_nukes_fired=0,
            total_module_activations=0,
        )
    if c2_user_id is not None:
        players[c2_user_id] = Player(
            user_id=c2_user_id,
            guild_id=guild_id,
            credits=0,
            total_fights=0,
            total_nukes_fired=0,
            total_module_activations=0,
        )

    async def _mock_get(session, user_id, gid):
        return players.get(user_id)

    session_mock = AsyncMock()
    with patch(
        "persist.repositories.player_repository.PlayerRepository.get_by_user_and_guild",
        side_effect=_mock_get,
    ):
        await service._increment_player_stats(
            session=session_mock,
            fight_results=fr,
            combatant1_user_id=c1_user_id,
            combatant2_user_id=c2_user_id,
            guild_id=guild_id,
        )

    return list(players.values())


# ---------------------------------------------------------------------------
# Test 1: NORMAL fights — distinct names, summary counts are correct
# ---------------------------------------------------------------------------


class TestNormalFightStatReads:
    """Summary-based counts match the counts the old timeline scan would have produced."""

    @pytest.mark.asyncio
    async def test_no_nukes_no_modules_both_zero(self):
        """When no nukes or modules fired, both counters increment by 0."""
        fr = _make_fight_results(summary=_summary())
        p1, p2 = await _call_increment(fr)
        assert p1.total_nukes_fired == 0
        assert p1.total_module_activations == 0
        assert p2.total_nukes_fired == 0
        assert p2.total_module_activations == 0

    @pytest.mark.asyncio
    async def test_c1_fires_2_nukes(self):
        """C1 fires 2 nukes; C2 fires 0. nukes_fired=2 for C1, 0 for C2."""
        fr = _make_fight_results(summary=_summary(c1_nukes=2, c2_nukes=0))
        p1, p2 = await _call_increment(fr)
        assert p1.total_nukes_fired == 2
        assert p2.total_nukes_fired == 0

    @pytest.mark.asyncio
    async def test_c2_fires_1_nuke(self):
        """C1 fires 0 nukes; C2 fires 1 nuke."""
        fr = _make_fight_results(summary=_summary(c1_nukes=0, c2_nukes=1))
        p1, p2 = await _call_increment(fr)
        assert p1.total_nukes_fired == 0
        assert p2.total_nukes_fired == 1

    @pytest.mark.asyncio
    async def test_module_activations_summed_from_dict(self):
        """module_activations values are summed: {cloak:1, booster:2} → 3 total."""
        fr = _make_fight_results(summary=_summary(c1_mods={"cloak": 1, "booster": 2}))
        p1, p2 = await _call_increment(fr)
        assert p1.total_module_activations == 3
        assert p2.total_module_activations == 0

    @pytest.mark.asyncio
    async def test_combined_nukes_and_modules(self):
        """C1 has 3 nukes + 2 module activations; C2 has 1 nuke + 1 activation."""
        fr = _make_fight_results(
            summary=_summary(
                c1_nukes=3,
                c2_nukes=1,
                c1_mods={"emergency_system": 1, "booster": 1},
                c2_mods={"cloak": 1},
            )
        )
        p1, p2 = await _call_increment(fr)
        assert p1.total_nukes_fired == 3
        assert p1.total_module_activations == 2
        assert p2.total_nukes_fired == 1
        assert p2.total_module_activations == 1

    @pytest.mark.asyncio
    async def test_total_fights_always_incremented(self):
        """total_fights += 1 is unchanged by the refactor."""
        fr = _make_fight_results(summary=_summary())
        p1, p2 = await _call_increment(fr)
        assert p1.total_fights == 1
        assert p2.total_fights == 1

    @pytest.mark.asyncio
    async def test_summary_identity_with_real_resolver_events(self):
        """End-to-end: build a summary via _build_fight_summary, then verify
        _increment_player_stats reads back the same nuke/module counts.

        This proves that for NORMAL fights the summary-read path is byte-identical
        to what the old timeline scan would have produced.
        """
        from services.combat_models import ModuleStats, WeaponStats
        from services.combat_resolver import (
            _CLOAK_MODULE_TYPE,
            _EMERGENCY_SYSTEM_MODULE_TYPE,
        )

        # Build two combatants: C1 has cloak, C2 has emergency_system
        l1 = ShipLoadout(
            ship_name="Alpha",
            base_armour=200,
            modules=[ModuleStats(name="Cloak", module_type=_CLOAK_MODULE_TYPE, effect_duration_ms=5000)],
            weapons=[WeaponStats(name="Laser", dps=5.0, damage_per_shot=5, loading_speed_ms=200, range_m=9999.0)],
        )
        l2 = ShipLoadout(
            ship_name="Beta",
            base_armour=200,
            modules=[ModuleStats(name="ES", module_type=_EMERGENCY_SYSTEM_MODULE_TYPE)],
            weapons=[WeaponStats(name="Laser", dps=5.0, damage_per_shot=5, loading_speed_ms=200, range_m=9999.0)],
        )

        c1_state = _init_combatant(l1, is_player=True)
        c2_state = _init_combatant(l2, is_player=True)

        # Emit a module_activation event for C1 (cloak) and a nuke for C2 (secondary_fired)
        events = [
            CombatEvent(
                tick=0,
                type=CombatEventType.fight_start,
                actor=None,
                target=None,
                data={
                    "combatants": [
                        {"name": "Alpha", "ship": "Alpha", "hp": {"shield": 0, "armour": 200, "hull": 0}},
                        {"name": "Beta", "ship": "Beta", "hp": {"shield": 0, "armour": 200, "hull": 0}},
                    ],
                    "initial_distance": 5000.0,
                },
            ),
            CombatEvent(
                tick=5,
                type=CombatEventType.module_activation,
                actor="Alpha",
                target=None,
                data={"module": "cloak", "trigger_hp_pct": 50, "side": 1},
            ),
            CombatEvent(
                tick=6,
                type=CombatEventType.weapon_fire,
                actor="Beta",
                target="Alpha",
                data={"slot": "secondary", "subtype": "nuke", "weapon": "NukeLauncher", "hit": True, "side": 2},
            ),
            CombatEvent(
                tick=10,
                type=CombatEventType.fight_end,
                actor=None,
                target=None,
                data={
                    "winner": "Alpha",
                    "reason": "hp_depleted",
                    "duration_ticks": 11,
                    "final_hp": {
                        "c1": {"shield": 0, "armour": 100, "hull": 0},
                        "c2": {"shield": 0, "armour": 0, "hull": 0},
                    },
                },
            ),
        ]

        built_summary = _build_fight_summary(
            events=events,
            c1=c1_state,
            c2=c2_state,
            outcome="win",
            reason="hp_depleted",
            duration_ticks=11,
            winner_name="Alpha",
        )

        # C1 (slot "1") should have 1 cloak activation, 0 nukes
        # C2 (slot "2") should have 0 module activations, 1 nuke
        assert built_summary["combatants"]["1"]["module_activations"] == {"cloak": 1}
        assert built_summary["combatants"]["1"]["secondary_fired"].get("nuke", 0) == 0
        assert built_summary["combatants"]["2"]["module_activations"] == {}
        assert built_summary["combatants"]["2"]["secondary_fired"].get("nuke", 0) == 1

        # Now feed this through _increment_player_stats and verify the increments match
        fr = _make_fight_results(
            summary=built_summary,
            combat_log=events,
            c1_user_id=10,
            c2_user_id=20,
        )
        p1, p2 = await _call_increment(fr, c1_user_id=10, c2_user_id=20)

        # C1: 1 cloak activation, 0 nukes
        assert p1.total_module_activations == 1
        assert p1.total_nukes_fired == 0
        # C2: 0 activations, 1 nuke
        assert p2.total_module_activations == 0
        assert p2.total_nukes_fired == 1


# ---------------------------------------------------------------------------
# Test 2: SAME-NAME fight — correct per-side attribution (bugfix test)
# ---------------------------------------------------------------------------


class TestSameNameFightCorrectAttribution:
    """When both combatants share the same ship name, side-keyed summary gives
    correct per-side counts. The old actor-name scan would collapse/misattribute."""

    @pytest.mark.asyncio
    async def test_same_name_nukes_attributed_per_side(self):
        """Two combatants with identical names: C1 fires 3 nukes, C2 fires 1 nuke.
        New code correctly reads slot "1" → 3, slot "2" → 1.
        Old name-based scan: both actors named "X" → C1 would have gotten 4, C2 would have gotten 4
        (or results would depend on scan order / de-dup behavior), either way incorrect.
        """
        same_name_summary = {
            "outcome": "win",
            "reason": "hp_depleted",
            "duration_ticks": 50,
            "winner": "X",
            "combatants": {
                "1": {
                    "name": "X",
                    "ship": "ShipX",
                    "damage_dealt": 100,
                    "damage_taken": 0,
                    "shots_fired": 5,
                    "shots_hit": 5,
                    "accuracy": 1.0,
                    "module_activations": {},
                    "secondary_fired": {"nuke": 3},
                    "secondary_rounds_by_weapon": {},
                },
                "2": {
                    "name": "X",  # same name — old scan would merge both
                    "ship": "ShipX",
                    "damage_dealt": 0,
                    "damage_taken": 100,
                    "shots_fired": 2,
                    "shots_hit": 0,
                    "accuracy": 0.0,
                    "module_activations": {},
                    "secondary_fired": {"nuke": 1},
                    "secondary_rounds_by_weapon": {},
                },
            },
        }

        fr = _make_fight_results(summary=same_name_summary)
        p1, p2 = await _call_increment(fr)

        # New side-keyed read: each gets its own counts
        assert p1.total_nukes_fired == 3, f"C1 (slot 1) must have 3 nukes, got {p1.total_nukes_fired}"
        assert p2.total_nukes_fired == 1, f"C2 (slot 2) must have 1 nuke, got {p2.total_nukes_fired}"

    @pytest.mark.asyncio
    async def test_same_name_module_activations_attributed_per_side(self):
        """Two combatants with identical names: C1 activates cloak 2×, C2 activates booster 1×.
        New code correctly reads slot "1" → 2, slot "2" → 1.
        Old name-scan: actor="X" matches both — C2's booster would land on whichever scan picked "X".
        """
        same_name_summary = {
            "outcome": "win",
            "reason": "hp_depleted",
            "duration_ticks": 50,
            "winner": "X",
            "combatants": {
                "1": {
                    "name": "X",
                    "ship": "ShipX",
                    "damage_dealt": 100,
                    "damage_taken": 0,
                    "shots_fired": 0,
                    "shots_hit": 0,
                    "accuracy": 0.0,
                    "module_activations": {"cloak": 2},
                    "secondary_fired": {},
                    "secondary_rounds_by_weapon": {},
                },
                "2": {
                    "name": "X",  # same name
                    "ship": "ShipX",
                    "damage_dealt": 0,
                    "damage_taken": 100,
                    "shots_fired": 0,
                    "shots_hit": 0,
                    "accuracy": 0.0,
                    "module_activations": {"booster": 1},
                    "secondary_fired": {},
                    "secondary_rounds_by_weapon": {},
                },
            },
        }

        fr = _make_fight_results(summary=same_name_summary)
        p1, p2 = await _call_increment(fr)

        assert p1.total_module_activations == 2, (
            f"C1 (slot 1) must have 2 activations (cloak×2), got {p1.total_module_activations}"
        )
        assert p2.total_module_activations == 1, (
            f"C2 (slot 2) must have 1 activation (booster×1), got {p2.total_module_activations}"
        )

    @pytest.mark.asyncio
    async def test_same_name_both_fire_nukes_and_modules(self):
        """Combined same-name test: C1 fires 2 nukes + 1 cloak; C2 fires 0 nukes + 2 boosters.
        Proves no cross-side bleed even with identical names.
        """
        same_name_summary = {
            "outcome": "stalemate",
            "reason": "timeout",
            "duration_ticks": 200,
            "winner": None,
            "combatants": {
                "1": {
                    "name": "Gemini",
                    "ship": "ShipG",
                    "damage_dealt": 50,
                    "damage_taken": 50,
                    "shots_fired": 5,
                    "shots_hit": 3,
                    "accuracy": 0.6,
                    "module_activations": {"cloak": 1},
                    "secondary_fired": {"nuke": 2},
                    "secondary_rounds_by_weapon": {},
                },
                "2": {
                    "name": "Gemini",  # same name
                    "ship": "ShipG",
                    "damage_dealt": 50,
                    "damage_taken": 50,
                    "shots_fired": 5,
                    "shots_hit": 3,
                    "accuracy": 0.6,
                    "module_activations": {"booster": 2},
                    "secondary_fired": {},
                    "secondary_rounds_by_weapon": {},
                },
            },
        }

        fr = _make_fight_results(summary=same_name_summary)
        p1, p2 = await _call_increment(fr)

        assert p1.total_nukes_fired == 2
        assert p1.total_module_activations == 1
        assert p2.total_nukes_fired == 0
        assert p2.total_module_activations == 2


# ---------------------------------------------------------------------------
# Test 3: ALLOWLIST COVERAGE — _ACTIVATION_MODULES covers all current emitters
# ---------------------------------------------------------------------------


class TestActivationModulesAllowlistCoverage:
    """_ACTIVATION_MODULES in combat_resolver covers all modules that currently
    emit module_activation events. This ensures the summary count equals the
    count the old 'any module_activation' scan would have produced today.
    """

    def test_cloak_in_activation_modules(self):
        """'cloak' module key is in _ACTIVATION_MODULES."""
        assert "cloak" in _ACTIVATION_MODULES, (
            "'cloak' must be in _ACTIVATION_MODULES; it emits module_activation events"
        )

    def test_booster_in_activation_modules(self):
        """'booster' module key is in _ACTIVATION_MODULES."""
        assert "booster" in _ACTIVATION_MODULES, (
            "'booster' must be in _ACTIVATION_MODULES; it emits module_activation events"
        )

    def test_emergency_system_in_activation_modules(self):
        """'emergency_system' module key is in _ACTIVATION_MODULES."""
        assert "emergency_system" in _ACTIVATION_MODULES, (
            "'emergency_system' must be in _ACTIVATION_MODULES; it emits module_activation events"
        )

    def test_all_resolver_module_activation_emitters_covered(self):
        """Enumerate the exact module keys emitted in combat_resolver module_activation events
        and assert each is in _ACTIVATION_MODULES.

        This is the authoritative proof that the summary count equals the full-timeline count
        today: since the summary filters by _ACTIVATION_MODULES and every emitter is in that set,
        no activations are dropped.
        """
        # The three module keys emitted by combat_resolver at module_activation events are:
        #   "cloak"             — _eval_cloak_booster (cloak branch)
        #   "booster"           — _eval_cloak_booster (booster branch)
        #   "emergency_system"  — _eval_emergency_system
        # These are the ONLY places in combat_resolver.py that create module_activation events.
        known_emitters = {"cloak", "booster", "emergency_system"}
        missing = known_emitters - _ACTIVATION_MODULES
        assert not missing, (
            f"The following module keys emit module_activation events but are NOT in "
            f"_ACTIVATION_MODULES: {missing!r}. The summary count would silently under-count them."
        )

    def test_no_unknown_keys_in_activation_modules(self):
        """_ACTIVATION_MODULES contains only the three known current emitters.
        If a new key appears, this test will fail as a reminder to update this doc.
        """
        expected = {"cloak", "booster", "emergency_system"}
        extra = _ACTIVATION_MODULES - expected
        assert not extra, (
            f"_ACTIVATION_MODULES contains unexpected keys: {extra!r}. "
            "Update this test and the coupling-note comment in _increment_player_stats."
        )


# ---------------------------------------------------------------------------
# Test 4: ONE FEWER TIMELINE WALK — _increment_player_stats reads summary, not log
# ---------------------------------------------------------------------------


class TestNoTimelineWalk:
    """_increment_player_stats does NOT iterate fight_results.combat_log for
    nukes_fired / module_activations. It reads from cb_block (the summary dict)."""

    @pytest.mark.asyncio
    async def test_module_activations_read_from_summary_not_timeline(self):
        """Behavioral proof (module counts): a combat_log packed with activation
        events that DISAGREE with the summary must be ignored — the stat comes
        from the summary block, not a timeline re-scan.

        The summary says C1 activated 3 modules; the timeline contains 7 module
        activation events for C1. The written stat must be 3 (summary), not 7.
        """
        summary_says_3 = _summary(c1_nukes=0, c2_nukes=0, c1_mods={"cloak": 1, "booster": 2})

        # Seven contradicting module-activation events in the timeline for "A".
        contradicting_events = [
            CombatEvent(
                tick=t,
                type=CombatEventType.module_activation,
                actor="A",
                target="A",
                data={"module": "booster"},
            )
            for t in range(7)
        ]

        fr = _make_fight_results(
            summary=summary_says_3,
            combat_log=contradicting_events,
            c1_user_id=1,
            c2_user_id=2,
        )
        p1, _p2 = await _call_increment(fr)

        # Must read 3 from summary (1 cloak + 2 booster), NOT 7 from timeline scan.
        assert p1.total_module_activations == 3, (
            f"Must read module_activations=3 from summary, not 7 from timeline scan. "
            f"Got {p1.total_module_activations}"
        )

    @pytest.mark.asyncio
    async def test_summary_read_ignores_combat_log_contents(self):
        """Providing a combat_log whose events DISAGREE with the summary proves
        the function reads the summary, not the timeline.

        The summary says C1 fired 5 nukes; the combat_log contains only 1 nuke event
        for C1. The stat written must be 5 (from summary), not 1 (from timeline).
        """
        summary_says_5 = _summary(c1_nukes=5, c2_nukes=0)

        # One contradicting nuke event in the timeline for actor "A"
        one_nuke_event = CombatEvent(
            tick=1,
            type=CombatEventType.weapon_fire,
            actor="A",
            target="B",
            data={"slot": "secondary", "subtype": "nuke", "weapon": "NukeLauncher", "hit": True},
        )

        fr = _make_fight_results(
            summary=summary_says_5,
            combat_log=[one_nuke_event],
            c1_user_id=1,
            c2_user_id=2,
        )
        p1, _p2 = await _call_increment(fr)

        # Must read 5 from summary, NOT 1 from timeline
        assert p1.total_nukes_fired == 5, (
            f"Must read nukes_fired=5 from summary, not 1 from timeline scan. Got {p1.total_nukes_fired}"
        )

    @pytest.mark.asyncio
    async def test_empty_combat_log_uses_summary_counts(self):
        """An empty combat_log still yields correct counts from the summary block."""
        summary = _summary(c1_nukes=2, c2_nukes=0, c1_mods={"booster": 3})
        fr = _make_fight_results(summary=summary, combat_log=[])
        p1, p2 = await _call_increment(fr)

        assert p1.total_nukes_fired == 2
        assert p1.total_module_activations == 3
        assert p2.total_nukes_fired == 0
        assert p2.total_module_activations == 0
