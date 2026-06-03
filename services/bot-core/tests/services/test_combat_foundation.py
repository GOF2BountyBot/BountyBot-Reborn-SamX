"""
T1 Foundation tests — constants, model surfaces, balance hooks.

Covers:
  D1: All 25 Appendix A constants on GameConstants with locked defaults + env overrides.
  D2: ShipLoadout.manual_turret_mode field defaults and round-trips.
  D3: LoadoutBuilder.from_player / from_criminal_ship surface manual_turret_mode.
  D4: CombatEvent dataclass + CombatEventType vocabulary constants.
  D5: combat_balance.weapon_accuracy() passthrough + SUBTYPE_ACCURACY_MOD empty.
  D6: WeaponStats.accuracy_modifier removed; ModuleStats.accuracy_modifier intact.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared bblogger guard — mirrors other test files in this directory
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
from services.combat_balance import SUBTYPE_ACCURACY_MOD, weapon_accuracy
from services.combat_models import (
    CombatEvent,
    CombatEventType,
    ModuleStats,
    ShipLoadout,
    WeaponStats,
)
from services.game_constants import GameConstants, resolve_constant
from services.loadout_builder import LoadoutBuilder

# ---------------------------------------------------------------------------
# D1 — Appendix A constant defaults
# ---------------------------------------------------------------------------


class TestConstantDefaults:
    """All 25 Appendix A constants are present on GameConstants with locked defaults."""

    def test_tick_and_timing(self):
        assert GameConstants.TICK_MS == 10
        assert GameConstants.MAX_FIGHT_TICKS == 18000

    def test_distance_model(self):
        assert GameConstants.STARTING_DISTANCE_M == 5000
        assert GameConstants.BASE_SHIP_SPEED_MPS == 150
        assert GameConstants.MIN_DISTANCE_M == 300
        assert GameConstants.THRUSTER_WINDOW_M == 750

    def test_accuracy_scalars(self):
        assert pytest.approx(0.25) == GameConstants.CLOAK_SET_VALUE
        assert pytest.approx(0.10) == GameConstants.BOOSTER_ACCURACY_DEBUFF_FACTOR
        assert pytest.approx(0.10) == GameConstants.THRUSTER_ACCURACY_BONUS_FACTOR
        assert pytest.approx(0.85) == GameConstants.AUTO_TURRET_ACCURACY_MULTIPLIER
        assert pytest.approx(0.60) == GameConstants.PLAYER_BASE_ACCURACY
        assert pytest.approx(0.50) == GameConstants.NPC_BASE_ACCURACY
        assert pytest.approx(0.05) == GameConstants.ACCURACY_CLAMP_MIN
        assert pytest.approx(0.99) == GameConstants.ACCURACY_CLAMP_MAX

    def test_scanner_tier_bonuses(self):
        assert GameConstants.SCANNER_TIER_B_BONUS_PP == 5
        assert GameConstants.SCANNER_TIER_C_BONUS_PP == 10

    def test_repair_bot_rates(self):
        assert pytest.approx(0.025) == GameConstants.KETAR_I_REPAIR_PCT_PER_SEC
        assert pytest.approx(0.050) == GameConstants.KETAR_II_REPAIR_PCT_PER_SEC

    def test_hp_threshold_lists(self):
        assert GameConstants.CLOAK_HP_THRESHOLDS_PCT == [66, 33]
        assert GameConstants.BOOSTER_HP_THRESHOLDS_PCT == [80, 60, 40, 20]

    def test_module_and_nuke(self):
        assert GameConstants.EMERGENCY_SYSTEM_INVULN_S == 10
        assert pytest.approx(0.10) == GameConstants.NUKE_MAGNITUDE_SCALE
        assert pytest.approx(0.25) == GameConstants.NUKE_FRIENDLY_FACTOR

    def test_pvc_and_retention(self):
        assert pytest.approx(0.33) == GameConstants.PVC_DAMAGE_REDUCTION
        assert GameConstants.COMBAT_LOG_RETENTION_HOURS == 72


# ---------------------------------------------------------------------------
# D1 — env-override path
# ---------------------------------------------------------------------------


class TestEnvOverrides:
    """GameConstants.load() picks up BOUNTYBOT_<NAME> for int, float, and list helpers."""

    def test_int_override(self, monkeypatch):
        monkeypatch.setattr(GameConstants, "TICK_MS", GameConstants.TICK_MS)
        monkeypatch.setenv("BOUNTYBOT_TICK_MS", "20")
        GameConstants.load()
        assert GameConstants.TICK_MS == 20

    def test_float_override(self, monkeypatch):
        monkeypatch.setattr(GameConstants, "CLOAK_SET_VALUE", GameConstants.CLOAK_SET_VALUE)
        monkeypatch.setenv("BOUNTYBOT_CLOAK_SET_VALUE", "0.30")
        GameConstants.load()
        assert pytest.approx(0.30) == GameConstants.CLOAK_SET_VALUE

    def test_int_list_override_cloak_thresholds(self, monkeypatch):
        monkeypatch.setattr(GameConstants, "CLOAK_HP_THRESHOLDS_PCT", GameConstants.CLOAK_HP_THRESHOLDS_PCT)
        monkeypatch.setenv("BOUNTYBOT_CLOAK_HP_THRESHOLDS_PCT", "50,25")
        GameConstants.load()
        assert GameConstants.CLOAK_HP_THRESHOLDS_PCT == [50, 25]

    def test_int_list_override_booster_thresholds(self, monkeypatch):
        monkeypatch.setattr(GameConstants, "BOOSTER_HP_THRESHOLDS_PCT", GameConstants.BOOSTER_HP_THRESHOLDS_PCT)
        monkeypatch.setenv("BOUNTYBOT_BOOSTER_HP_THRESHOLDS_PCT", "75,50,25,10")
        GameConstants.load()
        assert GameConstants.BOOSTER_HP_THRESHOLDS_PCT == [75, 50, 25, 10]

    def test_int_list_empty_string_returns_default(self, monkeypatch):
        """Empty-string env var preserves the default list, not []."""
        monkeypatch.setattr(GameConstants, "CLOAK_HP_THRESHOLDS_PCT", GameConstants.CLOAK_HP_THRESHOLDS_PCT)
        monkeypatch.setenv("BOUNTYBOT_CLOAK_HP_THRESHOLDS_PCT", "")
        GameConstants.load()
        assert GameConstants.CLOAK_HP_THRESHOLDS_PCT == [66, 33]


# ---------------------------------------------------------------------------
# D1 — resolve_constant helper
# ---------------------------------------------------------------------------


class TestResolveConstant:
    """resolve_constant returns fallback when guild_config is None or attr absent."""

    def test_none_returns_fallback(self):
        result = resolve_constant(None, "CLOAK_SET_VALUE", 0.99)
        assert result == pytest.approx(0.99)

    def test_object_with_attr_returns_override(self):
        cfg = types.SimpleNamespace(CLOAK_SET_VALUE=0.10)
        result = resolve_constant(cfg, "CLOAK_SET_VALUE", 0.25)
        assert result == pytest.approx(0.10)

    def test_object_missing_attr_returns_fallback(self):
        cfg = types.SimpleNamespace()
        result = resolve_constant(cfg, "CLOAK_SET_VALUE", 0.25)
        assert result == pytest.approx(0.25)

    def test_zero_is_a_valid_override_not_treated_as_missing(self):
        """resolve_constant uses `is None` check — 0 / 0.0 are valid overrides."""
        cfg = types.SimpleNamespace(SCANNER_TIER_B_BONUS_PP=0)
        result = resolve_constant(cfg, "SCANNER_TIER_B_BONUS_PP", 5)
        assert result == 0


# ---------------------------------------------------------------------------
# D5 — combat_balance passthrough
# ---------------------------------------------------------------------------


class TestCombatBalancePassthrough:
    """weapon_accuracy is a Phase-1 passthrough; SUBTYPE_ACCURACY_MOD is empty."""

    def test_passthrough_typical_accuracy(self):
        weapon = WeaponStats(name="Rail Gun", dps=25.0)
        assert weapon_accuracy(0.6, weapon) == pytest.approx(0.6)

    def test_passthrough_zero(self):
        weapon = WeaponStats(name="Rail Gun", dps=0.0)
        assert weapon_accuracy(0.0, weapon) == pytest.approx(0.0)

    def test_passthrough_full(self):
        weapon = WeaponStats(name="Rail Gun", dps=10.0)
        assert weapon_accuracy(1.0, weapon) == pytest.approx(1.0)

    def test_subtype_accuracy_mod_is_empty_dict(self):
        assert SUBTYPE_ACCURACY_MOD == {}
        assert isinstance(SUBTYPE_ACCURACY_MOD, dict)


# ---------------------------------------------------------------------------
# D2 / D4 / D6 — dataclass shapes
# ---------------------------------------------------------------------------


class TestDataclassShape:
    """CombatEvent, ShipLoadout, and WeaponStats have the correct field structure."""

    # CombatEvent (D4)

    def test_combat_event_defaults(self):
        evt = CombatEvent(tick=0, type="fight_start", actor=None, target=None)
        assert evt.tick == 0
        assert evt.type == "fight_start"
        assert evt.actor is None
        assert evt.target is None
        assert evt.data == {}

    def test_combat_event_with_data_payload(self):
        evt = CombatEvent(tick=100, type="damage", actor="Specter", target="Raider", data={"amount": 50})
        assert evt.data == {"amount": 50}
        assert evt.actor == "Specter"

    def test_combat_event_is_frozen(self):
        evt = CombatEvent(tick=0, type="fight_start", actor=None, target=None)
        with pytest.raises(AttributeError):
            evt.tick = 1  # type: ignore[misc]

    def test_combat_event_type_vocabulary(self):
        assert CombatEventType.fight_start == "fight_start"
        assert CombatEventType.fight_end == "fight_end"
        assert CombatEventType.regen == "regen"
        assert CombatEventType.weapon_fire == "weapon_fire"
        assert CombatEventType.damage == "damage"
        assert CombatEventType.module_activation == "module_activation"
        assert CombatEventType.cooldown_end == "cooldown_end"
        assert CombatEventType.layer_depleted == "layer_depleted"
        assert CombatEventType.distance == "distance"

    # ShipLoadout.manual_turret_mode (D2)

    def test_ship_loadout_manual_turret_mode_defaults_false(self):
        loadout = ShipLoadout(ship_name="Betty", base_armour=95)
        assert loadout.manual_turret_mode is False

    def test_ship_loadout_manual_turret_mode_explicit_true(self):
        loadout = ShipLoadout(ship_name="Betty", base_armour=95, manual_turret_mode=True)
        assert loadout.manual_turret_mode is True

    def test_ship_loadout_is_frozen(self):
        loadout = ShipLoadout(ship_name="Betty", base_armour=95)
        with pytest.raises(AttributeError):
            loadout.manual_turret_mode = True  # type: ignore[misc]

    # WeaponStats.accuracy_modifier removed (D6)

    def test_weapon_stats_accuracy_modifier_removed(self):
        """WeaponStats no longer accepts accuracy_modifier — field was removed in D6."""
        with pytest.raises(TypeError):
            WeaponStats(name="Rail Gun", dps=25.0, accuracy_modifier=1.0)  # type: ignore[call-arg]

    def test_module_stats_accuracy_modifier_intact(self):
        """ModuleStats.accuracy_modifier (scanner bonus) is unchanged."""
        mod = ModuleStats(name="Hiroto Proscan", accuracy_modifier=0.10)
        assert mod.accuracy_modifier == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# D3 — LoadoutBuilder plumbing
# ---------------------------------------------------------------------------


class TestLoadoutBuilderPlumbing:
    """LoadoutBuilder surfaces manual_turret_mode from criminal dict and PlayerShip."""

    def test_from_criminal_ship_defaults_false_when_key_absent(self):
        """Criminal dict without manual_turret_mode → False."""
        loadout = LoadoutBuilder.from_criminal_ship({"ship_name": "Betty", "ship_armour": 95})
        assert loadout.manual_turret_mode is False

    def test_from_criminal_ship_explicit_true(self):
        """Criminal dict with manual_turret_mode: True → True."""
        loadout = LoadoutBuilder.from_criminal_ship(
            {"ship_name": "Betty", "ship_armour": 95, "manual_turret_mode": True}
        )
        assert loadout.manual_turret_mode is True

    def test_from_criminal_ship_explicit_false(self):
        """Criminal dict with manual_turret_mode: False → False (explicit)."""
        loadout = LoadoutBuilder.from_criminal_ship(
            {"ship_name": "Betty", "ship_armour": 95, "manual_turret_mode": False}
        )
        assert loadout.manual_turret_mode is False

    @pytest.mark.asyncio
    async def test_from_player_reads_manual_turret_mode_via_getattr(self):
        """from_player reads manual_turret_mode from PlayerShip via getattr (T2 forward-compat)."""
        # 1 mock: player_repo
        player = MagicMock()
        player.active_ship_id = 10

        player_ship = MagicMock()
        player_ship.ship_name = "Specter"
        player_ship.weapons = []
        player_ship.turrets = []
        player_ship.modules = []
        player_ship.manual_turret_mode = True  # simulate T2 column present

        ship = MagicMock()
        ship.armour = 200

        player_repo = MagicMock()
        player_repo.get_by_id = AsyncMock(return_value=player)

        def _result(val):
            r = MagicMock()
            r.scalars.return_value.first.return_value = val
            return r

        db = MagicMock()
        db.execute = AsyncMock(side_effect=[_result(player_ship), _result(ship)])

        # 2nd mock: item_repo (no items equipped)
        item_repo = MagicMock()
        item_repo.get_by_name = AsyncMock(return_value=None)

        with (
            patch("persist.repositories.player_repository.PlayerRepository", return_value=player_repo),
            patch("persist.repositories.item_repository.ItemRepository", return_value=item_repo),
        ):
            loadout = await LoadoutBuilder.from_player(db, player_id=1)

        assert loadout.manual_turret_mode is True

    @pytest.mark.asyncio
    async def test_from_player_defaults_false_when_attr_absent(self):
        """from_player falls back to False when PlayerShip lacks manual_turret_mode (pre-T2 getattr path)."""
        # 1 mock: player_repo
        player = MagicMock()
        player.active_ship_id = 10

        # spec excludes manual_turret_mode — getattr(..., False) must supply the default
        player_ship = MagicMock(spec=["ship_name", "weapons", "turrets", "modules"])
        player_ship.ship_name = "Specter"
        player_ship.weapons = []
        player_ship.turrets = []
        player_ship.modules = []

        ship = MagicMock()
        ship.armour = 200

        player_repo = MagicMock()
        player_repo.get_by_id = AsyncMock(return_value=player)

        def _result(val):
            r = MagicMock()
            r.scalars.return_value.first.return_value = val
            return r

        db = MagicMock()
        db.execute = AsyncMock(side_effect=[_result(player_ship), _result(ship)])

        # 2nd mock: item_repo (no items equipped)
        item_repo = MagicMock()
        item_repo.get_by_name = AsyncMock(return_value=None)

        with (
            patch("persist.repositories.player_repository.PlayerRepository", return_value=player_repo),
            patch("persist.repositories.item_repository.ItemRepository", return_value=item_repo),
        ):
            loadout = await LoadoutBuilder.from_player(db, player_id=99)

        assert loadout.manual_turret_mode is False
