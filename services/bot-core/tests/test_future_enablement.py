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

import os

# The tests/ dir carries a `services` test-subpackage that shadows the real
# src/services package whenever tests/ precedes src/ on sys.path. Force src to
# the FRONT so `from services...` resolves to the application package even when
# this module is collected in isolation.
_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, _SRC)

import pytest
from persist.models.base import Base
from persist.models.player_ship import PlayerShip
from services._item_type_normalizer import expand_item_type_to_concrete
from services.game_constants import GameConstants
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_ENABLED_WITH_SECONDARY = GameConstants.CURRENTLY_ENABLED_TYPES | {"secondary_weapon"}


class _FakeSecondaryItem:
    """Minimal faithful stand-in for a SecondaryWeapon Item row.

    The Item table has ARRAY columns (aliases) that SQLite cannot host, so the
    single ``item_repo.get_by_name_any_type`` lookup is the one boundary that is
    stubbed.  ``equip_check`` only reads ``.type`` off the returned item, and the
    gate + real ship lookup then run for real against SQLite.
    """

    type = "SecondaryWeapon"
    id = 1
    name = "Disruptor"


@pytest.fixture
async def empty_ship_session():
    """Real SQLite AsyncSession with an empty player_ships table.

    player_ships is JSONB-only (no ARRAY columns) so it is SQLite-hostable; an
    empty table makes ``ship_repo.get_by_id`` return None so the post-gate path
    raises a ValueError rather than InvalidItemTypeError.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[PlayerShip.__table__])
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


class TestSecondaryWeaponEnablementGate:
    """The secondary-weapon gate in EquipmentService.equip_check is the single lever."""

    async def test_equip_check_rejects_secondary_when_gated_off(self, empty_ship_session, monkeypatch):
        """With secondary_weapon NOT enabled, equip_check raises InvalidItemTypeError.

        Anti-vacuous companion to the flip test: proves the gate actually bites
        BEFORE any ship/inventory lookup when the lever is off.
        """
        from unittest.mock import AsyncMock

        from services.equipment_service import EquipmentService
        from services.exceptions import InvalidItemTypeError

        # Ensure the lever is OFF for this test regardless of ambient config.
        monkeypatch.setattr(
            GameConstants,
            "CURRENTLY_ENABLED_TYPES",
            GameConstants.CURRENTLY_ENABLED_TYPES - {"secondary_weapon"},
        )
        import services.equipment_service as equip_mod

        monkeypatch.setattr(equip_mod.GameConstants, "CURRENTLY_ENABLED_TYPES", GameConstants.CURRENTLY_ENABLED_TYPES)

        svc = EquipmentService()
        svc.item_repo.get_by_name_any_type = AsyncMock(return_value=_FakeSecondaryItem())

        with pytest.raises(InvalidItemTypeError):
            await svc.equip_check(empty_ship_session, player_id=1, ship_id=1, item_name="Disruptor")

    async def test_equip_check_accepts_secondary_when_enabled(self, empty_ship_session, monkeypatch):
        """With secondary_weapon enabled, equip_check passes the gate — no InvalidItemTypeError.

        Flipping the single lever ``secondary_weapon`` into CURRENTLY_ENABLED_TYPES
        must let a secondary weapon through the defense-in-depth gate.  We drive the
        REAL equip_check: past the gate it reaches the real ship lookup, which finds
        no ship in the empty SQLite table and raises a ValueError — proving the gate
        was cleared (the old test only asserted the set membership it monkeypatched).
        """
        from unittest.mock import AsyncMock

        from services.equipment_service import EquipmentService
        from services.exceptions import InvalidItemTypeError

        monkeypatch.setattr(GameConstants, "CURRENTLY_ENABLED_TYPES", _ENABLED_WITH_SECONDARY)
        import services.equipment_service as equip_mod

        monkeypatch.setattr(equip_mod.GameConstants, "CURRENTLY_ENABLED_TYPES", _ENABLED_WITH_SECONDARY)

        svc = EquipmentService()
        svc.item_repo.get_by_name_any_type = AsyncMock(return_value=_FakeSecondaryItem())

        # Past the gate the code hits the real (empty) ship lookup → ValueError,
        # NOT InvalidItemTypeError.  Assert specifically that the gate did not bite.
        with pytest.raises(ValueError) as exc_info:
            await svc.equip_check(empty_ship_session, player_id=1, ship_id=1, item_name="Disruptor")
        assert not isinstance(exc_info.value, InvalidItemTypeError), (
            "Gate still rejected secondary weapon after the enablement flip"
        )
        assert "not currently enabled" not in str(exc_info.value).lower()


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

    def test_shop_generation_includes_secondary_when_enabled(self, monkeypatch):
        """secondary_weapon is now in _CONCRETE_TO_CONFIG_KEY mapped to its own 'secondary_weapon' key."""
        monkeypatch.setattr(GameConstants, "CURRENTLY_ENABLED_TYPES", _ENABLED_WITH_SECONDARY)
        from services.shop_service import _CONCRETE_TO_CONFIG_KEY

        # CI-11: secondary_weapon now has its own dedicated config key
        assert "secondary_weapon" in _CONCRETE_TO_CONFIG_KEY
        assert _CONCRETE_TO_CONFIG_KEY["secondary_weapon"] == "secondary_weapon"

        enabled_with_config_key = {t for t in GameConstants.CURRENTLY_ENABLED_TYPES if t in _CONCRETE_TO_CONFIG_KEY}
        assert "ship" in enabled_with_config_key
        assert "primary_weapon" in enabled_with_config_key
        assert "secondary_weapon" in enabled_with_config_key
        assert "module" in enabled_with_config_key
        assert "turret_weapon" in enabled_with_config_key
