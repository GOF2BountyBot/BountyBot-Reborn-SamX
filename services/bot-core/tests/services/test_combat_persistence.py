"""
T10 — CombatLogService.persist + retention + overkill-absorbed tests.

Covers:
  - CombatLogService.persist: happy-path PvP + PvC, context validation,
    NPC-vs-NPC invariant, serialization (CombatEvent→dict), ORM insert.
  - db_retention_executor: combat_log pass added (fourth pass).
  - Deliverable 0 (overkill): damage_dealt/damage_taken reflect absorbed HP
    not raw overkill.

Max 2 mocks per test.
"""

from __future__ import annotations

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
)
from services.combat_resolver import _apply_damage, _build_fight_summary, _init_combatant

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fight_results(
    *,
    winner_name: str | None = "C1",
    loser_name: str | None = "C2",
    is_stalemate: bool = False,
    c1_user_id: int | None = 111,
    c2_user_id: int | None = None,
    context_hint: str = "duel",
) -> FightResults:
    """Minimal FightResults with enough §12 metadata for persist()."""
    summary = {
        "outcome": "win",
        "reason": "hp_depleted",
        "duration_ticks": 100,
        "winner": winner_name,
        "combatants": {
            "1": {"name": "C1", "ship": "Betty", "damage_dealt": 50, "damage_taken": 20},
            "2": {"name": "C2", "ship": "Bandit", "damage_dealt": 20, "damage_taken": 50},
        },
    }
    stats = FightStats(ship_name="C1", raw_hp=200, raw_dps=10.0, varied_hp=200, varied_dps=10.0, ttk=None)
    stats2 = FightStats(ship_name="C2", raw_hp=100, raw_dps=5.0, varied_hp=100, varied_dps=5.0, ttk=5.0)
    event = CombatEvent(tick=0, type=CombatEventType.fight_start, actor=None, target=None, data={})
    return FightResults(
        winner_name=winner_name,
        loser_name=loser_name,
        is_stalemate=is_stalemate,
        ship1_stats=stats,
        ship2_stats=stats2,
        combat_log=[event],
        metadata={
            "schema_version": 1,
            "summary": summary,
            "metadata": {"tick_ms": 10, "total_ticks": 100, "resolver": "tick_v1", "pvc_damage_reduction": 0.0},
            "combatant_user_ids": {"c1": c1_user_id, "c2": c2_user_id},
        },
    )


# ---------------------------------------------------------------------------
# CombatLogService.persist — context validation
# ---------------------------------------------------------------------------


class TestCombatLogServiceContextValidation:
    """persist() raises ValueError on invalid context."""

    @pytest.mark.asyncio
    async def test_invalid_context_raises_value_error(self):
        """persist(context='garbage') raises ValueError."""
        from services.combat_log_service import CombatLogService

        svc = CombatLogService()
        fr = _make_fight_results()
        meta = CombatMeta(guild_id=1)

        with pytest.raises(ValueError, match="Invalid combat context"):
            await svc.persist(meta, fr, "garbage", session=AsyncMock())

    @pytest.mark.asyncio
    async def test_valid_contexts_accepted(self):
        """persist() accepts all three valid contexts without raising."""
        from services.combat_log_service import CombatLogService

        svc = CombatLogService()
        meta = CombatMeta(guild_id=1)

        for ctx in ("duel", "bounty_pvc", "bounty_bonus"):
            fr = _make_fight_results(c1_user_id=42, c2_user_id=99 if ctx == "duel" else None)
            mock_session = AsyncMock()
            mock_row = MagicMock()
            mock_row.id = 999
            with patch.object(svc._repo, "add", new=AsyncMock(return_value=mock_row)):
                result = await svc.persist(meta, fr, ctx, session=mock_session)
            assert result == 999


# ---------------------------------------------------------------------------
# CombatLogService.persist — NPC-vs-NPC invariant
# ---------------------------------------------------------------------------


class TestCombatLogServiceNPCInvariant:
    """persist() raises when both combatant user_ids are NULL."""

    @pytest.mark.asyncio
    async def test_both_null_raises_value_error(self):
        """NPC-vs-NPC (both user_ids NULL) raises ValueError before DB call."""
        from services.combat_log_service import CombatLogService

        svc = CombatLogService()
        fr = _make_fight_results(c1_user_id=None, c2_user_id=None)
        meta = CombatMeta(guild_id=1)
        session_mock = AsyncMock()

        with pytest.raises(ValueError, match="NPC invariant"):
            await svc.persist(meta, fr, "bounty_pvc", session=session_mock)


# ---------------------------------------------------------------------------
# CombatLogService.persist — serialization: CombatEvent→dict
# ---------------------------------------------------------------------------


class TestCombatLogServiceSerialization:
    """persist() serialises CombatEvent dataclasses to dicts before insert."""

    @pytest.mark.asyncio
    async def test_combat_events_serialised_to_dict(self):
        """Timeline CombatEvent objects become plain dicts in the data blob."""
        from services.combat_log_service import CombatLogService

        svc = CombatLogService()
        meta = CombatMeta(guild_id=1)
        event = CombatEvent(
            tick=5,
            type="weapon_fire",
            actor="C1",
            target="C2",
            data={"weapon": "Gatling Gun", "hit": True},
        )
        fr = _make_fight_results(c1_user_id=42)
        # Replace combat_log with a real CombatEvent
        object.__setattr__(fr, "combat_log", [event])

        captured_row = None

        async def _fake_add(session, row):
            nonlocal captured_row
            captured_row = row
            row.id = 1
            return row

        with patch.object(svc._repo, "add", side_effect=_fake_add):
            await svc.persist(meta, fr, "duel", session=AsyncMock())

        assert captured_row is not None
        timeline = captured_row.data["timeline"]
        assert isinstance(timeline, list)
        assert len(timeline) == 1
        item = timeline[0]
        assert isinstance(item, dict), f"Expected dict; got {type(item)}"
        assert item["tick"] == 5
        assert item["type"] == "weapon_fire"
        assert item["actor"] == "C1"


# ---------------------------------------------------------------------------
# Deliverable 0 — overkill: absorbed vs raw damage in summary
# ---------------------------------------------------------------------------


class TestAbsorbedOverkill:
    """damage_dealt / damage_taken count absorbed HP, not raw overkill."""

    def test_cluster_overkill_absorbed_not_raw(self):
        """
        Cluster missile with 3 sub-munitions each dealing 200 dmg vs a target
        with only 100 HP remaining. Only 100 HP can be absorbed (the rest is overkill).
        damage_dealt must equal 100, NOT 600.

        Uses _apply_damage directly against a synthetic _CombatantState to
        validate the absorbed field on each damage event.
        """
        loadout = ShipLoadout(ship_name="Target", base_armour=100)
        state = _init_combatant(loadout, is_player=False)
        events: list[CombatEvent] = []

        # Fire 3 sub-munitions each dealing 200 damage against a target with 100 hull
        for _ in range(3):
            _apply_damage(
                state,
                raw_damage=200.0,
                tick=1,
                events=events,
                source={"subtype": "cluster-missile", "weapon": "ClusterBomb", "attacker": "Attacker"},
                pvc_damage_reduction=0.0,
            )

        # Collect absorbed amounts from damage events
        damage_events = [ev for ev in events if ev.type == CombatEventType.damage]
        total_absorbed = sum(ev.data.get("absorbed", 0) for ev in damage_events)

        # Total available HP was 100; all sub-munitions together can only absorb 100
        assert total_absorbed == 100, (
            f"Expected absorbed=100 (all available HP), got {total_absorbed}. "
            "Overkill from sub-munitions must NOT count toward damage_dealt."
        )

    def test_absorbed_field_present_on_damage_events(self):
        """Every damage event emitted by _apply_damage includes the 'absorbed' field."""
        loadout = ShipLoadout(ship_name="Target", base_armour=500)
        state = _init_combatant(loadout, is_player=False)
        events: list[CombatEvent] = []

        _apply_damage(
            state,
            raw_damage=100.0,
            tick=0,
            events=events,
            source={"subtype": "primary", "weapon": "Gun", "attacker": "A"},
            pvc_damage_reduction=0.0,
        )

        damage_events = [ev for ev in events if ev.type == CombatEventType.damage]
        assert len(damage_events) == 1
        ev = damage_events[0]
        assert "absorbed" in ev.data, "'absorbed' field must be present on damage events"
        assert ev.data["absorbed"] == ev.data["amount"], (
            "No overkill: absorbed should equal amount when target has more HP than damage"
        )

    def test_full_fight_summary_absorbed_not_raw(self):
        """TickResolver fight summary damage_dealt reflects absorbed HP only."""
        # Two weak ships: C1 has a single-shot weapon that can overkill.
        # We use the full resolver to verify the summary builder uses absorbed.
        # Build a minimal state to verify _build_fight_summary uses absorbed
        ev_damage = CombatEvent(
            tick=5,
            type=CombatEventType.damage,
            actor=None,
            target="Target",
            data={
                "amount": 500,  # raw overkill amount
                "absorbed": 100,  # only 100 HP was actually removed
                "source": {"attacker": "Attacker", "subtype": "primary", "weapon": "Gun"},
                "breakdown": {"shield": 0, "armour": 0, "hull": 100},
                "hp_after": {"shield": 0, "armour": 0, "hull": 0},
            },
        )
        ev_start = CombatEvent(
            tick=0,
            type=CombatEventType.fight_start,
            actor=None,
            target=None,
            data={
                "combatants": [
                    {"name": "Attacker", "ship": "A", "hp": {"shield": 0, "armour": 0, "hull": 100}},
                    {"name": "Target", "ship": "B", "hp": {"shield": 0, "armour": 0, "hull": 100}},
                ],
                "initial_distance": 5000.0,
            },
        )
        ev_end = CombatEvent(
            tick=5,
            type=CombatEventType.fight_end,
            actor=None,
            target=None,
            data={
                "winner": "Attacker",
                "reason": "hp_depleted",
                "duration_ticks": 6,
                "final_hp": {
                    "c1": {"shield": 0, "armour": 0, "hull": 100},
                    "c2": {"shield": 0, "armour": 0, "hull": 0},
                },
            },
        )

        # Create minimal combatant states for the summary builder
        l1 = ShipLoadout(ship_name="Attacker", base_armour=100)
        l2 = ShipLoadout(ship_name="Target", base_armour=100)
        c1 = _init_combatant(l1, is_player=False)
        c2 = _init_combatant(l2, is_player=False)

        summary = _build_fight_summary(
            events=[ev_start, ev_damage, ev_end],
            c1=c1,
            c2=c2,
            outcome="win",
            reason="hp_depleted",
            duration_ticks=6,
            winner_name="Attacker",
        )

        attacker_block = summary["combatants"]["1"]
        target_block = summary["combatants"]["2"]

        assert attacker_block["damage_dealt"] == 100, (
            f"damage_dealt must be absorbed=100, not raw=500. Got {attacker_block['damage_dealt']}"
        )
        assert target_block["damage_taken"] == 100, (
            f"damage_taken must be absorbed=100, not raw=500. Got {target_block['damage_taken']}"
        )


# ---------------------------------------------------------------------------
# CombatMeta dataclass
# ---------------------------------------------------------------------------


class TestCombatMeta:
    """CombatMeta is a minimal frozen dataclass with guild_id."""

    def test_combat_meta_fields(self):
        """CombatMeta has guild_id and is frozen."""
        meta = CombatMeta(guild_id=12345)
        assert meta.guild_id == 12345

    def test_combat_meta_is_frozen(self):
        """CombatMeta is immutable."""
        import dataclasses

        meta = CombatMeta(guild_id=1)
        with pytest.raises(dataclasses.FrozenInstanceError):
            meta.guild_id = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# FightResults.combat_log_id
# ---------------------------------------------------------------------------


class TestFightResultsCombatLogId:
    """FightResults has combat_log_id field (None by default)."""

    def test_combat_log_id_defaults_to_none(self):
        """FightResults.combat_log_id is None by default."""
        stats = FightStats(ship_name="A", raw_hp=100, raw_dps=10.0, varied_hp=100, varied_dps=10.0, ttk=None)
        fr = FightResults(
            winner_name="A",
            loser_name="B",
            is_stalemate=False,
            ship1_stats=stats,
            ship2_stats=stats,
        )
        assert fr.combat_log_id is None


# ---------------------------------------------------------------------------
# db_retention_executor — fourth pass (combat_log)
# ---------------------------------------------------------------------------


class TestDbRetentionExecutorCombatLogPass:
    """execute_db_retention_job includes a fourth combat_log pass."""

    @pytest.mark.asyncio
    async def test_return_dict_includes_combat_logs_deleted(self):
        """Return dict has 'combat_logs_deleted' key."""
        # Mock all four repo calls so no real DB is needed.
        with (
            patch("persist.database.manager.db_manager") as mock_manager,
            patch(
                "persist.repositories.bounty_repository.BountyRepository.delete_terminal_older_than",
                new=AsyncMock(return_value=0),
            ),
            patch(
                "persist.repositories.duel_repository.DuelRepository.delete_terminal_older_than",
                new=AsyncMock(return_value=0),
            ),
            patch(
                "persist.repositories.admin_audit_log_repository.AdminAuditLogRepository.delete_older_than",
                new=AsyncMock(return_value=0),
            ),
            patch(
                "persist.repositories.combat_log_repository.CombatLogRepository.delete_older_than",
                new=AsyncMock(return_value=3),
            ),
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_manager.get_session = MagicMock(return_value=mock_ctx)

            from utils.executors.db_retention_executor import execute_db_retention_job

            result = await execute_db_retention_job("test-job", {})

        assert "combat_logs_deleted" in result, "Return dict must include 'combat_logs_deleted'"
        assert result["combat_logs_deleted"] == 3
        assert result["status"] == "success"
