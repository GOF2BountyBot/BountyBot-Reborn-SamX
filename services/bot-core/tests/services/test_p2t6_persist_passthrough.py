"""P2-T6 — persist passthrough + no-asdict-on-hot-path tests.

Covers:
  1. PERSISTED BYTE-IDENTITY: data.timeline for an offload fight is byte-identical
     to the canonical list[dict] produced by run_fight(seed=42) directly.
  2. IN-PROCESS/LEGACY GUARD: a caller passing list[CombatEvent] (dataclasses) to
     persist still serializes correctly via the is_dataclass branch.
  3. NO ASDICT ON HOT PATH: when the timeline is already list[dict], dataclasses.asdict
     is NOT called during persist; and fight_ships no longer re-hydrates the worker
     output into CombatEvent objects.
  4. COMBAT-LOG READ PATH: /combat-log detail still returns identical results when
     data.timeline contains plain dicts (no regression).

Max 2 mocks per test.
"""

from __future__ import annotations

import dataclasses
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Shared bblogger + sqlalchemy_utils guard
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
from services.combat_models import (
    CombatEvent,
    CombatEventType,
    CombatMeta,
    FightResults,
    FightStats,
    ShipLoadout,
    WeaponStats,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_loadouts():
    """Deterministic (l1, l2) pair: C1 kills C2 in one hit, no stalemate."""
    l1 = ShipLoadout(
        ship_name="Attacker",
        base_armour=500,
        weapons=[WeaponStats(name="Pulse", dps=0.0, damage_per_shot=9999, loading_speed_ms=100, range_m=999999.0)],
    )
    l2 = ShipLoadout(ship_name="Defender", base_armour=10)
    return l1, l2


def _make_fight_results_with_dataclass_log(c1_user_id: int = 111) -> FightResults:
    """FightResults whose combat_log contains CombatEvent dataclass instances (legacy path)."""
    summary = {
        "outcome": "win",
        "reason": "hp_depleted",
        "duration_ticks": 5,
        "winner": "C1",
        "combatants": {
            "1": {
                "name": "C1",
                "ship": "Betty",
                "damage_dealt": 100,
                "damage_taken": 0,
                "shots_fired": 1,
                "shots_hit": 1,
                "final_hp": {"hull": 100, "armour": 0, "shield": 0},
                "start_hp": {"hull": 100, "armour": 0, "shield": 0},
            },
            "2": {
                "name": "C2",
                "ship": "Bandit",
                "damage_dealt": 0,
                "damage_taken": 100,
                "shots_fired": 0,
                "shots_hit": 0,
                "final_hp": {"hull": 0, "armour": 0, "shield": 0},
                "start_hp": {"hull": 100, "armour": 0, "shield": 0},
            },
        },
    }
    stats = FightStats(ship_name="C1", raw_hp=200, raw_dps=10.0, varied_hp=200, varied_dps=10.0, ttk=None)
    stats2 = FightStats(ship_name="C2", raw_hp=100, raw_dps=5.0, varied_hp=100, varied_dps=5.0, ttk=5.0)
    events: list[CombatEvent] = [
        CombatEvent(tick=0, type=CombatEventType.fight_start, actor=None, target=None, data={}),
        CombatEvent(
            tick=1, type=CombatEventType.weapon_fire, actor="C1", target="C2", data={"weapon": "Pulse", "hit": True}
        ),
        CombatEvent(
            tick=1, type=CombatEventType.damage, actor=None, target="C2", data={"amount": 100, "absorbed": 100}
        ),
        CombatEvent(
            tick=1,
            type=CombatEventType.fight_end,
            actor=None,
            target=None,
            data={"winner": "C1", "reason": "hp_depleted"},
        ),
    ]
    return FightResults(
        winner_name="C1",
        loser_name="C2",
        is_stalemate=False,
        ship1_stats=stats,
        ship2_stats=stats2,
        combat_log=events,  # type: ignore[arg-type]  — intentional legacy list[CombatEvent]
        metadata={
            "schema_version": 1,
            "summary": summary,
            "metadata": {"tick_ms": 10, "total_ticks": 5, "resolver": "tick_v1", "pvc_damage_reduction": 0.0},
            "combatant_user_ids": {"c1": c1_user_id, "c2": None},
        },
    )


def _make_fight_results_with_dict_log(c1_user_id: int = 111) -> FightResults:
    """FightResults whose combat_log contains plain dicts (offload path)."""
    summary = {
        "outcome": "win",
        "reason": "hp_depleted",
        "duration_ticks": 5,
        "winner": "C1",
        "combatants": {
            "1": {
                "name": "C1",
                "ship": "Betty",
                "damage_dealt": 100,
                "damage_taken": 0,
                "shots_fired": 1,
                "shots_hit": 1,
                "final_hp": {"hull": 100, "armour": 0, "shield": 0},
                "start_hp": {"hull": 100, "armour": 0, "shield": 0},
            },
            "2": {
                "name": "C2",
                "ship": "Bandit",
                "damage_dealt": 0,
                "damage_taken": 100,
                "shots_fired": 0,
                "shots_hit": 0,
                "final_hp": {"hull": 0, "armour": 0, "shield": 0},
                "start_hp": {"hull": 100, "armour": 0, "shield": 0},
            },
        },
    }
    stats = FightStats(ship_name="C1", raw_hp=200, raw_dps=10.0, varied_hp=200, varied_dps=10.0, ttk=None)
    stats2 = FightStats(ship_name="C2", raw_hp=100, raw_dps=5.0, varied_hp=100, varied_dps=5.0, ttk=5.0)
    events: list[dict] = [
        {"tick": 0, "type": "fight_start", "actor": None, "target": None, "data": {}},
        {"tick": 1, "type": "weapon_fire", "actor": "C1", "target": "C2", "data": {"weapon": "Pulse", "hit": True}},
        {"tick": 1, "type": "damage", "actor": None, "target": "C2", "data": {"amount": 100, "absorbed": 100}},
        {
            "tick": 1,
            "type": "fight_end",
            "actor": None,
            "target": None,
            "data": {"winner": "C1", "reason": "hp_depleted"},
        },
    ]
    return FightResults(
        winner_name="C1",
        loser_name="C2",
        is_stalemate=False,
        ship1_stats=stats,
        ship2_stats=stats2,
        combat_log=events,
        metadata={
            "schema_version": 1,
            "summary": summary,
            "metadata": {"tick_ms": 10, "total_ticks": 5, "resolver": "tick_v1", "pvc_damage_reduction": 0.0},
            "combatant_user_ids": {"c1": c1_user_id, "c2": None},
        },
    )


# ---------------------------------------------------------------------------
# Test 1: IN-PROCESS/LEGACY GUARD — list[CombatEvent] → asdict correctly
# ---------------------------------------------------------------------------


class TestLegacyGuardDataclassBranch:
    """persist() with a list[CombatEvent] timeline still serialises to dicts via asdict."""

    @pytest.mark.asyncio
    async def test_dataclass_timeline_serialised_via_asdict(self):
        """Passing list[CombatEvent] to persist yields plain dicts in data.timeline (is_dataclass guard)."""
        from services.combat_log_service import CombatLogService

        svc = CombatLogService()
        meta = CombatMeta(guild_id=1)
        fr = _make_fight_results_with_dataclass_log()

        captured_row = None

        async def _fake_add(session, row):
            nonlocal captured_row
            captured_row = row
            row.id = 1
            return row

        with patch.object(svc._repo, "add", side_effect=_fake_add):
            await svc.persist(meta, fr, "bounty_pvc", session=AsyncMock())

        assert captured_row is not None
        timeline = captured_row.data["timeline"]
        assert isinstance(timeline, list)
        assert len(timeline) == len(fr.combat_log)
        for item in timeline:
            assert isinstance(item, dict), f"Expected dict; got {type(item)}"
            assert set(item.keys()) == {"tick", "type", "actor", "target", "data"}, f"Unexpected keys: {item.keys()}"

    @pytest.mark.asyncio
    async def test_dataclass_timeline_values_match_asdict(self):
        """Each dict in data.timeline matches dataclasses.asdict of the original CombatEvent."""
        from services.combat_log_service import CombatLogService

        svc = CombatLogService()
        meta = CombatMeta(guild_id=1)
        fr = _make_fight_results_with_dataclass_log()
        original_events = list(fr.combat_log)  # copy before mutation check

        captured_row = None

        async def _fake_add(session, row):
            nonlocal captured_row
            captured_row = row
            row.id = 1
            return row

        with patch.object(svc._repo, "add", side_effect=_fake_add):
            await svc.persist(meta, fr, "bounty_pvc", session=AsyncMock())

        timeline = captured_row.data["timeline"]
        for i, (stored_dict, original_ev) in enumerate(zip(timeline, original_events, strict=True)):
            expected = dataclasses.asdict(original_ev)
            assert stored_dict == expected, f"Event [{i}] mismatch:\n  stored: {stored_dict}\n  expected: {expected}"


# ---------------------------------------------------------------------------
# Test 2: NO ASDICT ON HOT PATH — dict timeline passes through without asdict
# ---------------------------------------------------------------------------


class TestNoAsdictOnHotPath:
    """When the timeline is already list[dict], dataclasses.asdict is NOT called."""

    @pytest.mark.asyncio
    async def test_asdict_not_called_when_timeline_is_dicts(self):
        """Spy on dataclasses.asdict — must not be called when combat_log is list[dict]."""
        from services.combat_log_service import CombatLogService

        from services import combat_log_service as cls_module

        svc = CombatLogService()
        meta = CombatMeta(guild_id=1)
        fr = _make_fight_results_with_dict_log()

        asdict_call_count = 0
        original_asdict = dataclasses.asdict

        def _spy_asdict(obj, **kwargs):
            nonlocal asdict_call_count
            asdict_call_count += 1
            return original_asdict(obj, **kwargs)

        captured_row = None

        async def _fake_add(session, row):
            nonlocal captured_row
            captured_row = row
            row.id = 2
            return row

        # Patch dataclasses.asdict in the combat_log_service module namespace
        with (
            patch.object(cls_module.dataclasses, "asdict", side_effect=_spy_asdict),
            patch.object(svc._repo, "add", side_effect=_fake_add),
        ):
            await svc.persist(meta, fr, "bounty_pvc", session=AsyncMock())

        assert asdict_call_count == 0, (
            f"dataclasses.asdict must NOT be called when timeline is already list[dict]; "
            f"was called {asdict_call_count} time(s)"
        )

    @pytest.mark.asyncio
    async def test_dict_timeline_passthrough_byte_identical(self):
        """Plain dicts in combat_log are stored byte-identically in data.timeline."""
        from services.combat_log_service import CombatLogService

        svc = CombatLogService()
        meta = CombatMeta(guild_id=1)
        fr = _make_fight_results_with_dict_log()
        original_dicts = list(fr.combat_log)

        captured_row = None

        async def _fake_add(session, row):
            nonlocal captured_row
            captured_row = row
            row.id = 3
            return row

        with patch.object(svc._repo, "add", side_effect=_fake_add):
            await svc.persist(meta, fr, "bounty_pvc", session=AsyncMock())

        timeline = captured_row.data["timeline"]
        assert timeline == original_dicts, (
            f"Dict timeline must be stored byte-identically.\n  stored:   {timeline}\n  original: {original_dicts}"
        )


# ---------------------------------------------------------------------------
# Test 3: PERSISTED BYTE-IDENTITY — offload fight vs canonical run_fight
# ---------------------------------------------------------------------------


class TestPersistedByteIdentity:
    """data.timeline for an offload fight is byte-identical to run_fight(seed=42) directly."""

    @pytest.mark.asyncio
    async def test_offload_timeline_byte_identical_to_run_fight(self):
        """fight_ships persists a timeline byte-identical to run_fight(seed=42).

        1. Call run_fight(seed=42) directly → canonical list[dict].
        2. Call fight_ships with run_fight patched to seed=42.
        3. Capture what CombatLogService.persist receives as fight_results.combat_log.
        4. Assert it equals the canonical list[dict] element-by-element.
        """
        from compute.combat_worker import run_fight as real_run_fight
        from services.combat_service import CombatService

        FIXED_SEED = 42
        l1, l2 = _make_loadouts()

        # Step 1: canonical timeline from run_fight(seed=42) directly
        canonical_raw = real_run_fight(
            l1,
            l2,
            pvc_damage_reduction=0.0,
            seed=FIXED_SEED,
            combatant1_label="",
            combatant2_label="",
            compact=False,
        )
        canonical_timeline: list[dict] = canonical_raw["timeline"]
        assert len(canonical_timeline) > 0, "Canonical timeline must be non-empty"

        # Step 2: fight_ships with seeded run_fight
        def _seeded_run_fight(lo1, lo2, *, pvc_damage_reduction, seed, combatant1_label, combatant2_label, compact):
            return real_run_fight(
                lo1,
                lo2,
                pvc_damage_reduction=pvc_damage_reduction,
                seed=FIXED_SEED,
                combatant1_label=combatant1_label,
                combatant2_label=combatant2_label,
                compact=compact,
            )

        captured: list[list[dict]] = []

        async def _capture_persist(meta, fight_results, context, session):
            captured.append(list(fight_results.combat_log))
            return 99

        service = CombatService()
        cs_globals = service.fight_ships.__func__.__globals__
        orig_run_fight = cs_globals["run_fight"]
        cs_globals["run_fight"] = _seeded_run_fight
        try:
            with (
                patch(
                    "services.combat_log_service.CombatLogService.persist",
                    new=AsyncMock(side_effect=_capture_persist),
                ),
                patch(
                    "persist.repositories.player_repository.PlayerRepository.get_by_user_and_guild",
                    new=AsyncMock(return_value=None),
                ),
            ):
                await service.fight_ships(
                    l1,
                    l2,
                    context="duel",
                    log_result=True,
                    session=AsyncMock(),
                    guild_id=1,
                    combatant1_user_id=100,
                    combatant2_user_id=None,
                )
        finally:
            cs_globals["run_fight"] = orig_run_fight

        assert len(captured) == 1, "Expected exactly one persist call"
        offload_timeline = captured[0]

        # Step 3: each element must be a plain dict (no re-hydration)
        for i, ev in enumerate(offload_timeline):
            assert isinstance(ev, dict), f"Event [{i}] must be dict (P2-T6 no re-hydration), got {type(ev)}"

        # Step 4: byte-identical to canonical
        assert len(offload_timeline) == len(canonical_timeline), (
            f"Timeline length mismatch: offload={len(offload_timeline)}, canonical={len(canonical_timeline)}"
        )
        for i, (offload_d, canonical_d) in enumerate(zip(offload_timeline, canonical_timeline, strict=True)):
            assert offload_d == canonical_d, (
                f"Event [{i}] mismatch:\n  offload:   {offload_d}\n  canonical: {canonical_d}"
            )


# ---------------------------------------------------------------------------
# Test 4: NO RE-HYDRATION in fight_ships — combat_log is list[dict] not list[CombatEvent]
# ---------------------------------------------------------------------------


class TestFightShipsNoRehydration:
    """fight_ships must not convert list[dict] → list[CombatEvent] (P2-T6 removal)."""

    @pytest.mark.asyncio
    async def test_fight_ships_combat_log_is_list_of_dicts(self):
        """fight_ships (log_result=False) returns FightResults.combat_log as list[dict]."""
        from services.combat_service import CombatService

        service = CombatService()
        l1, l2 = _make_loadouts()
        result = await service.fight_ships(l1, l2, log_result=False)

        assert len(result.combat_log) > 0, "Expected non-empty combat_log"
        for i, ev in enumerate(result.combat_log):
            assert isinstance(ev, dict), f"Event [{i}]: expected dict (P2-T6 no re-hydration), got {type(ev)}"

    @pytest.mark.asyncio
    async def test_fight_ships_dict_events_have_expected_keys(self):
        """Every dict in combat_log has the 5 expected CombatEvent keys."""
        from services.combat_service import CombatService

        service = CombatService()
        l1, l2 = _make_loadouts()
        result = await service.fight_ships(l1, l2, log_result=False)

        expected_keys = {"tick", "type", "actor", "target", "data"}
        for i, ev in enumerate(result.combat_log):
            assert set(ev.keys()) == expected_keys, (
                f"Event [{i}] unexpected keys: got {set(ev.keys())}, expected {expected_keys}"
            )

    @pytest.mark.asyncio
    async def test_no_combat_event_instances_in_combat_log(self):
        """combat_log must NOT contain CombatEvent dataclass instances (re-hydration removed)."""
        from services.combat_service import CombatService

        service = CombatService()
        l1, l2 = _make_loadouts()
        result = await service.fight_ships(l1, l2, log_result=False)

        for i, ev in enumerate(result.combat_log):
            # CombatEvent is a frozen dataclass — check via is_dataclass to be module-path safe
            assert not dataclasses.is_dataclass(ev) or isinstance(ev, type), (
                f"Event [{i}] is a dataclass instance (CombatEvent) — re-hydration was not removed: {ev!r}"
            )


# ---------------------------------------------------------------------------
# Test 5: COMBAT-LOG READ PATH regression — get_detail works with list[dict] timeline
# ---------------------------------------------------------------------------


class TestCombatLogReadPathRegression:
    """get_detail still returns valid results when data.timeline contains plain dicts."""

    @pytest.mark.asyncio
    async def test_get_detail_with_dict_timeline(self):
        """CombatLogService.get_detail extracts key_events correctly from list[dict] timeline."""
        from services.combat_log_service import CombatLogService

        svc = CombatLogService()

        # Build a MagicMock row (avoids ORM MetaData collision when imported in isolation)
        fake_row = MagicMock()
        fake_row.id = 7
        fake_row.guild_id = 1
        fake_row.context = "duel"
        fake_row.combatant1_name = "C1"
        fake_row.combatant2_name = "C2"
        fake_row.combatant1_user_id = 42
        fake_row.combatant2_user_id = None
        fake_row.winner_name = "C1"
        fake_row.is_stalemate = False
        from datetime import UTC, datetime

        fake_row.created_at = datetime(2025, 1, 1, tzinfo=UTC)
        fake_row.data = {
            "schema_version": 1,
            "summary": {
                "outcome": "win",
                "reason": "hp_depleted",
                "duration_ticks": 5,
                "winner": "C1",
                "combatants": {
                    "1": {
                        "name": "C1",
                        "ship": "Betty",
                        "damage_dealt": 100,
                        "damage_taken": 0,
                        "shots_fired": 1,
                        "shots_hit": 1,
                        "final_hp": {"hull": 100, "armour": 0, "shield": 0},
                        "start_hp": {"hull": 100, "armour": 0, "shield": 0},
                    },
                    "2": {
                        "name": "C2",
                        "ship": "Bandit",
                        "damage_dealt": 0,
                        "damage_taken": 100,
                        "shots_fired": 0,
                        "shots_hit": 0,
                        "final_hp": {"hull": 0, "armour": 0, "shield": 0},
                        "start_hp": {"hull": 100, "armour": 0, "shield": 0},
                    },
                },
            },
            "timeline": [
                {"tick": 0, "type": "fight_start", "actor": None, "target": None, "data": {}},
                {
                    "tick": 1,
                    "type": "fight_end",
                    "actor": None,
                    "target": None,
                    "data": {"winner": "C1", "reason": "hp_depleted", "duration_ticks": 5},
                },
            ],
            "metadata": {"tick_ms": 10, "total_ticks": 5, "resolver": "tick_v1", "pvc_damage_reduction": 0.0},
        }

        # P4-T7b: get_detail calls get_subpath_for_detail first.
        # fake_row has no "key_events" in data (legacy row) → sub returns key_events=None
        # → service falls back to get_by_id + _extract_key_events.
        fake_sub = MagicMock()
        fake_sub.id = fake_row.id
        fake_sub.guild_id = fake_row.guild_id
        fake_sub.context = fake_row.context
        fake_sub.combatant1_name = fake_row.combatant1_name
        fake_sub.combatant2_name = fake_row.combatant2_name
        fake_sub.combatant1_user_id = fake_row.combatant1_user_id
        fake_sub.combatant2_user_id = fake_row.combatant2_user_id
        fake_sub.winner_name = fake_row.winner_name
        fake_sub.is_stalemate = fake_row.is_stalemate
        fake_sub.created_at = fake_row.created_at
        fake_sub.summary = fake_row.data["summary"]
        fake_sub.metadata = fake_row.data["metadata"]
        fake_sub.key_events = None  # legacy row — triggers full-row fallback

        with (
            patch.object(svc._repo, "get_subpath_for_detail", new=AsyncMock(return_value=fake_sub)),
            patch.object(svc._repo, "get_by_id", new=AsyncMock(return_value=fake_row)),
        ):
            detail = await svc.get_detail(db=AsyncMock(), battle_id=7, user_id=42)

        # Should not raise; key fields must be present
        assert detail["id"] == 7
        assert detail["outcome"] == "won"  # user_id=42 is combatant1, winner=C1
        assert isinstance(detail["key_events"], list)
        assert detail["duration_ticks"] == 5
