"""
Contract tests for _item_type_normalizer.expand_item_type_to_concrete.

These tests verify the normalization contract defined in INVENTORY_VOCAB_FIX_DESIGN_SPEC.md §4.

Spec traceability:
- test_catalog_context_includes_secondary_weapon         → spec §12.2
- test_playable_context_excludes_secondary_weapon_today → spec §12.2
- test_concrete_type_passthrough_catalog                → spec §4.1
- test_concrete_type_raises_if_disabled_playable        → spec §4.1
- test_generic_weapon_expansion_catalog                 → spec §12.2
- test_generic_weapon_expansion_playable                → spec §12.2
- test_unknown_alias_raises                             → spec §12.2
"""

import sys
import types
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Guard: ensure shared.bblogger is mocked before importing service modules.
# ---------------------------------------------------------------------------
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
from services.exceptions import InvalidItemTypeError
from services.game_constants import GameConstants

# ===========================================================================
# Catalog context: all types visible, including secondary_weapon
# ===========================================================================


class TestCatalogContext:
    """Tests for context='catalog' (browsing / read-only paths)."""

    def test_concrete_type_passthrough_catalog(self):
        """Concrete types pass through unchanged in catalog context."""
        for ct in ("ship", "primary_weapon", "secondary_weapon", "turret_weapon", "module"):
            result = expand_item_type_to_concrete(ct, context="catalog")
            assert result == (ct,), f"Expected ({ct!r},), got {result}"

    def test_catalog_context_includes_secondary_weapon(self):
        """secondary_weapon is accessible in catalog context even when gated."""
        result = expand_item_type_to_concrete("secondary_weapon", context="catalog")
        assert result == ("secondary_weapon",)

    def test_generic_weapon_expansion_catalog(self):
        """'weapon' alias expands to all 3 weapon concrete types in catalog context."""
        result = expand_item_type_to_concrete("weapon", context="catalog")
        assert set(result) == {"primary_weapon", "secondary_weapon", "turret_weapon"}
        assert len(result) == 3

    def test_generic_turret_expansion_catalog(self):
        """'turret' alias expands to 'turret_weapon' in catalog context."""
        result = expand_item_type_to_concrete("turret", context="catalog")
        assert result == ("turret_weapon",)

    def test_generic_ship_passthrough_catalog(self):
        """'ship' is both a generic alias and a concrete type; expands to ('ship',)."""
        result = expand_item_type_to_concrete("ship", context="catalog")
        assert result == ("ship",)

    def test_generic_module_passthrough_catalog(self):
        """'module' is both an alias and a concrete type; expands to ('module',)."""
        result = expand_item_type_to_concrete("module", context="catalog")
        assert result == ("module",)

    def test_unknown_alias_raises(self):
        """Unknown alias raises InvalidItemTypeError in catalog context."""
        with pytest.raises(InvalidItemTypeError, match="Unknown item type"):
            expand_item_type_to_concrete("banana", context="catalog")

    def test_uppercase_raises(self):
        """Uppercase or capitalized types are not accepted (types are lowercase)."""
        with pytest.raises(InvalidItemTypeError):
            expand_item_type_to_concrete("Weapon", context="catalog")


# ===========================================================================
# Playable context: secondary_weapon gated out today
# ===========================================================================


class TestPlayableContext:
    """Tests for context='playable' (economy/equip write paths)."""

    def test_playable_context_includes_secondary_weapon(self):
        """secondary_weapon is now in CURRENTLY_ENABLED_TYPES (CI-5: shop sells canonical secondaries)."""
        assert "secondary_weapon" in GameConstants.CURRENTLY_ENABLED_TYPES
        result = expand_item_type_to_concrete("secondary_weapon", context="playable")
        assert result == ("secondary_weapon",)

    def test_concrete_type_raises_if_disabled_playable(self, monkeypatch):
        """Concrete type that is in CATALOG but NOT in CURRENTLY_ENABLED_TYPES raises.

        As of T1 (PvC loot) every catalog type — including 'commodity' — is enabled,
        so there is no longer a permanently-disabled catalog type to use as the example.
        We temporarily disable 'commodity' (keeping it in CATALOG_ITEM_TYPES) to prove
        the gate still rejects a cataloged-but-disabled concrete type on the write path.
        """
        from services.exceptions import InvalidItemTypeError as _ITE

        disabled = GameConstants.CURRENTLY_ENABLED_TYPES - {"commodity"}
        monkeypatch.setattr(GameConstants, "CURRENTLY_ENABLED_TYPES", disabled)
        assert "commodity" in GameConstants.CATALOG_ITEM_TYPES  # still cataloged
        with pytest.raises(_ITE):
            expand_item_type_to_concrete("commodity", context="playable")

    def test_generic_weapon_expansion_playable(self):
        """'weapon' in playable context expands to primary_weapon + secondary_weapon + turret_weapon."""
        result = expand_item_type_to_concrete("weapon", context="playable")
        assert "secondary_weapon" in result
        assert "primary_weapon" in result
        assert "turret_weapon" in result
        assert len(result) == 3

    def test_all_enabled_concrete_types_accepted_playable(self):
        """All CURRENTLY_ENABLED_TYPES pass through in playable context."""
        for ct in GameConstants.CURRENTLY_ENABLED_TYPES:
            result = expand_item_type_to_concrete(ct, context="playable")
            assert result == (ct,), f"Expected ({ct!r},), got {result}"

    def test_unknown_alias_raises_playable(self):
        """Unknown alias raises InvalidItemTypeError in playable context."""
        with pytest.raises(InvalidItemTypeError, match="Unknown item type"):
            expand_item_type_to_concrete("potato", context="playable")

    def test_generic_module_playable(self):
        """'module' is in CURRENTLY_ENABLED_TYPES; expands correctly."""
        result = expand_item_type_to_concrete("module", context="playable")
        assert result == ("module",)

    def test_generic_turret_playable(self):
        """'turret' alias expands to 'turret_weapon' in playable context."""
        result = expand_item_type_to_concrete("turret", context="playable")
        assert result == ("turret_weapon",)


# ===========================================================================
# Future-readiness flip test (spec §12.2)
# ===========================================================================


class TestFutureEnablement:
    """Verify the single-constant flip works correctly.

    Monkeypatches GameConstants.CURRENTLY_ENABLED_TYPES to include
    secondary_weapon and verifies all gated paths open up.
    """

    def test_enabling_secondary_weapon_opens_playable_context(self, monkeypatch):
        """When secondary_weapon is added to CURRENTLY_ENABLED_TYPES, playable context allows it."""
        monkeypatch.setattr(
            GameConstants,
            "CURRENTLY_ENABLED_TYPES",
            GameConstants.CURRENTLY_ENABLED_TYPES | {"secondary_weapon"},
        )
        result = expand_item_type_to_concrete("secondary_weapon", context="playable")
        assert result == ("secondary_weapon",)

    def test_enabling_secondary_weapon_expands_weapon_alias(self, monkeypatch):
        """When secondary_weapon is enabled, 'weapon' expands to all 3 concrete weapon types."""
        monkeypatch.setattr(
            GameConstants,
            "CURRENTLY_ENABLED_TYPES",
            GameConstants.CURRENTLY_ENABLED_TYPES | {"secondary_weapon"},
        )
        result = expand_item_type_to_concrete("weapon", context="playable")
        assert "secondary_weapon" in result
        assert "primary_weapon" in result
        assert "turret_weapon" in result
        assert len(result) == 3
