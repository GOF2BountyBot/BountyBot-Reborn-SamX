"""
§7.7 regression suite: EmergencySystem post-fight consumption (_consume_emergency_system).

EmergencySystem is a one-use consumable: per COMBAT_SPEC_LOCKED.md §7.7 it is
"removed from loadout after use; player must manually re-equip a spare from inventory."
The in-fight ``es_runtime.consumed`` flag only enforces once-per-fight; this suite covers
the cross-fight loadout removal performed by CombatService._consume_emergency_system.

Mirrors the write-back test pattern in test_secondary_ammo.py (Section B): real
FightResults/summary objects, repos injected via the module-level class-replacement
pattern, max 2 mocks of substance per test.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Module-level dependency stubs (same pattern as test_secondary_ammo.py)
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
# Helpers
# ---------------------------------------------------------------------------

_ES_TYPE = "EmergencySystemModule"


def _module(name: str, type_str: str) -> SimpleNamespace:
    """Stand-in for a persist Module row (Item.type discriminator)."""
    return SimpleNamespace(name=name, type=type_str)


def _fight_results(*, slot_block: dict, slot_key: str = "1"):
    """Build a minimal FightResults whose summary carries one combatant block."""
    from services.combat_models import FightResults, FightStats

    return FightResults(
        winner_name="Human",
        loser_name="NPC",
        is_stalemate=False,
        ship1_stats=FightStats("Human", 1000, 10.0, 1000, 10.0, 100.0),
        ship2_stats=FightStats("NPC", 500, 5.0, 500, 5.0, 50.0),
        combat_log=[],
        metadata={"summary": {"combatants": {slot_key: slot_block}}},
    )


async def _run_consume(
    *,
    fight_results,
    ship,
    module_lookup,
    combatant1_user_id,
    combatant2_user_id=None,
    player=SimpleNamespace(id=100),
):
    """Invoke _consume_emergency_system with repos injected via class replacement.

    Returns (player_repo_mock, ship_repo_mock, module_repo_mock) for assertions.
    """
    from services.combat_service import CombatService

    mock_player_repo = AsyncMock()
    mock_ship_repo = AsyncMock()
    mock_module_repo = AsyncMock()
    mock_player_repo.get_by_user_and_guild = AsyncMock(return_value=player)
    mock_ship_repo.get_active_ship = AsyncMock(return_value=ship)
    mock_module_repo.get_by_name = AsyncMock(side_effect=lambda _db, name: module_lookup.get(name))

    import persist.repositories.module_repository as _mr
    import persist.repositories.player_repository as _pr
    import persist.repositories.player_ship_repository as _psr

    orig_pr, orig_psr, orig_mr = _pr.PlayerRepository, _psr.PlayerShipRepository, _mr.ModuleRepository
    _pr.PlayerRepository = lambda: mock_player_repo
    _psr.PlayerShipRepository = lambda: mock_ship_repo
    _mr.ModuleRepository = lambda: mock_module_repo
    try:
        svc = CombatService()
        await svc._consume_emergency_system(
            session=AsyncMock(),
            fight_results=fight_results,
            combatant1_user_id=combatant1_user_id,
            combatant2_user_id=combatant2_user_id,
            guild_id=1,
        )
    finally:
        _pr.PlayerRepository = orig_pr
        _psr.PlayerShipRepository = orig_psr
        _mr.ModuleRepository = orig_mr

    return mock_player_repo, mock_ship_repo, mock_module_repo


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestConsumeEmergencySystem:
    @pytest.mark.asyncio
    async def test_activation_removes_es_module_from_loadout(self):
        """ES fired on side 1 → the EmergencySystemModule is removed from ship.modules."""
        ship = MagicMock()
        ship.id = 1
        ship.modules = ["Beamshield II", "Emergency System", "Ketar Repair Bot"]
        lookup = {
            "Beamshield II": _module("Beamshield II", "ShieldModule"),
            "Emergency System": _module("Emergency System", _ES_TYPE),
            "Ketar Repair Bot": _module("Ketar Repair Bot", "RepairBotModule"),
        }
        fr = _fight_results(slot_block={"name": "Human", "module_activations": {"emergency_system": 1}})

        await _run_consume(fight_results=fr, ship=ship, module_lookup=lookup, combatant1_user_id=999)

        assert ship.modules == ["Beamshield II", "Ketar Repair Bot"], "ES must be removed; siblings untouched"

    @pytest.mark.asyncio
    async def test_no_activation_leaves_loadout_untouched(self):
        """No emergency_system activation in summary → modules unchanged, no ship lookup."""
        ship = MagicMock()
        ship.id = 1
        ship.modules = ["Emergency System", "Beamshield II"]
        lookup = {"Emergency System": _module("Emergency System", _ES_TYPE)}
        # Booster fired, but not the ES.
        fr = _fight_results(slot_block={"name": "Human", "module_activations": {"booster": 1}})

        _, ship_repo, _ = await _run_consume(fight_results=fr, ship=ship, module_lookup=lookup, combatant1_user_id=999)

        assert ship.modules == ["Emergency System", "Beamshield II"], "ES must survive when it never fired"
        ship_repo.get_active_ship.assert_not_called()

    @pytest.mark.asyncio
    async def test_npc_side_no_db_access(self):
        """Both sides NPC (user_id None) → never touches the DB."""
        ship = MagicMock()
        ship.modules = ["Emergency System"]
        fr = _fight_results(slot_block={"name": "NPC", "module_activations": {"emergency_system": 1}}, slot_key="2")

        player_repo, _, _ = await _run_consume(
            fight_results=fr,
            ship=ship,
            module_lookup={"Emergency System": _module("Emergency System", _ES_TYPE)},
            combatant1_user_id=None,
            combatant2_user_id=None,
        )

        player_repo.get_by_user_and_guild.assert_not_called()

    @pytest.mark.asyncio
    async def test_only_one_instance_consumed_when_two_equipped(self):
        """ES fires at most once per fight → only one of two equipped ES modules is removed."""
        ship = MagicMock()
        ship.id = 1
        ship.modules = ["Emergency System", "Emergency System", "Beamshield II"]
        lookup = {
            "Emergency System": _module("Emergency System", _ES_TYPE),
            "Beamshield II": _module("Beamshield II", "ShieldModule"),
        }
        fr = _fight_results(slot_block={"name": "Human", "module_activations": {"emergency_system": 1}})

        await _run_consume(fight_results=fr, ship=ship, module_lookup=lookup, combatant1_user_id=999)

        assert ship.modules == ["Emergency System", "Beamshield II"], "exactly one ES instance consumed"

    @pytest.mark.asyncio
    async def test_activation_but_no_es_equipped_is_noop(self):
        """Defensive: summary claims ES fired but no ES in modules → leave list intact, no crash."""
        ship = MagicMock()
        ship.id = 1
        ship.modules = ["Beamshield II", "Ketar Repair Bot"]
        lookup = {
            "Beamshield II": _module("Beamshield II", "ShieldModule"),
            "Ketar Repair Bot": _module("Ketar Repair Bot", "RepairBotModule"),
        }
        fr = _fight_results(slot_block={"name": "Human", "module_activations": {"emergency_system": 1}})

        await _run_consume(fight_results=fr, ship=ship, module_lookup=lookup, combatant1_user_id=999)

        assert ship.modules == ["Beamshield II", "Ketar Repair Bot"], "unrelated modules must be preserved"
