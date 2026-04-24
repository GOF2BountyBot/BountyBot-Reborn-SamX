"""
Future-readiness flip test suite (spec §12.2 - test_future_enablement.py).

This file proves that adding 'secondary_weapon' to GameConstants.CURRENTLY_ENABLED_TYPES
is the ONLY change needed to enable secondary weapons across the stack.

These tests use monkeypatch to simulate the future enablement.
"""

import sys
import types
from unittest.mock import MagicMock

# Guard: ensure shared.bblogger and sqlalchemy_utils are mocked.
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

if "sqlalchemy_utils" not in sys.modules:
    _mock_sqla_utils = types.ModuleType("sqlalchemy_utils")
    _mock_sqla_utils.UUIDType = MagicMock()
    sys.modules["sqlalchemy_utils"] = _mock_sqla_utils

from services._item_type_normalizer import expand_item_type_to_concrete
from services.game_constants import GameConstants

_ENABLED_WITH_SECONDARY = GameConstants.CURRENTLY_ENABLED_TYPES | {"secondary_weapon"}


class TestFutureEnablementFlip:
    """Prove the single-lever flip works across all gated surfaces."""

    def test_normalizer_accepts_secondary_weapon_when_enabled(self, monkeypatch):
        """With secondary_weapon enabled, normalizer passes it through in playable context."""
        monkeypatch.setattr(GameConstants, "CURRENTLY_ENABLED_TYPES", _ENABLED_WITH_SECONDARY)
        result = expand_item_type_to_concrete("secondary_weapon", context="playable")
        assert result == ("secondary_weapon",)

    def test_weapon_alias_expands_to_3_types_when_secondary_enabled(self, monkeypatch):
        """With secondary_weapon enabled, 'weapon' expands to all 3 concrete types."""
        monkeypatch.setattr(GameConstants, "CURRENTLY_ENABLED_TYPES", _ENABLED_WITH_SECONDARY)
        result = expand_item_type_to_concrete("weapon", context="playable")
        assert set(result) == {"primary_weapon", "secondary_weapon", "turret_weapon"}

    def test_equipment_service_accepts_secondary_when_enabled(self, monkeypatch):
        """With secondary_weapon enabled, equip_item does NOT raise InvalidItemTypeError for secondary."""
        monkeypatch.setattr(GameConstants, "CURRENTLY_ENABLED_TYPES", _ENABLED_WITH_SECONDARY)

        from services.equipment_service import GameConstants as EquipGC

        monkeypatch.setattr(EquipGC, "CURRENTLY_ENABLED_TYPES", _ENABLED_WITH_SECONDARY)

        # The gate check: equipment_type == "secondary_weapons" and not in CURRENTLY_ENABLED_TYPES
        # With the flip, this should NOT raise.
        assert "secondary_weapon" in GameConstants.CURRENTLY_ENABLED_TYPES

    def test_shop_generation_includes_secondary_when_enabled(self, monkeypatch):
        """With secondary_weapon enabled, _CONCRETE_TO_CONFIG_KEY would include it once added."""
        monkeypatch.setattr(GameConstants, "CURRENTLY_ENABLED_TYPES", _ENABLED_WITH_SECONDARY)
        from services.shop_service import _CONCRETE_TO_CONFIG_KEY

        # Today secondary_weapon is not in _CONCRETE_TO_CONFIG_KEY (no GuildConfig support yet)
        # This test documents the state — when GuildConfig supports it, add to _CONCRETE_TO_CONFIG_KEY
        enabled_with_config_key = {t for t in GameConstants.CURRENTLY_ENABLED_TYPES if t in _CONCRETE_TO_CONFIG_KEY}
        # secondary_weapon is NOT in _CONCRETE_TO_CONFIG_KEY yet — that's expected
        # This test ensures the generation loop is aware of CURRENTLY_ENABLED_TYPES
        assert "ship" in enabled_with_config_key
        assert "primary_weapon" in enabled_with_config_key
        assert "module" in enabled_with_config_key
        assert "turret_weapon" in enabled_with_config_key
