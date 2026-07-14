"""Unit tests for cargo_utils.compute_free_cargo (LOOT_JOURNAL §5.4 / §7.1).

Covers the additive CompressorModule stacking fix (issue #36): two or more
compressors add their bonus fractions together instead of multiplying.

Design notes:
- Max 2 mocks per test (per /proj/AGENTS.md): `db` and `inventory_repo`.
- `db.get` / `db.execute` are stubbed as a lightweight dispatcher, mirroring
  the pattern in test_loadout_response_service.py.
- All tests are async (asyncio_mode = auto from pytest.ini).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


def _make_db(*, player_ship, ship, module_factory):
    from persist.models.module import Module as ModuleModel
    from persist.models.ship import Ship as ShipModel

    async def _get(model, obj_id):
        return player_ship

    async def _execute(stmt):
        result = MagicMock()
        model = stmt.column_descriptions[0]["entity"] if stmt.column_descriptions else None
        if model is ShipModel:
            result.scalars.return_value.first.return_value = ship
        elif model is ModuleModel:
            name = stmt.whereclause.right.value
            result.scalars.return_value.first.return_value = module_factory(name)
        else:
            result.scalars.return_value.first.return_value = None
        return result

    db = MagicMock()
    db.get = _get
    db.execute = _execute
    return db


def _compressor(name, cargo_multiplier):
    return SimpleNamespace(name=name, type="CompressorModule", extra_atts={"cargoMultiplier": cargo_multiplier})


async def test_two_compressors_stack_additively_not_multiplicatively():
    """+25% and +100% compressors together give +125% (cap 45), not +150% (cap 50)."""
    from services.cargo_utils import compute_free_cargo

    player = SimpleNamespace(id=1, active_ship_id=10)
    player_ship = SimpleNamespace(id=10, ship_name="Interceptor", modules=["AutoPacker 2", "Rhoda Blackhole"])
    ship = SimpleNamespace(name="Interceptor", cargo=20)

    def module_factory(name):
        if name == "AutoPacker 2":
            return _compressor("AutoPacker 2", 1.25)
        if name == "Rhoda Blackhole":
            return _compressor("Rhoda Blackhole", 2.0)
        return None

    db = _make_db(player_ship=player_ship, ship=ship, module_factory=module_factory)
    inventory_repo = MagicMock()
    inventory_repo.get_player_items = AsyncMock(return_value=[])

    free, current_load, effective_cap = await compute_free_cargo(db, inventory_repo, player)

    # Additive: 20 × (1 + 0.25 + 1.00) = 20 × 2.25 = 45 (not 20 × 1.25 × 2.0 = 50)
    assert effective_cap == 45
    assert current_load == 0
    assert free == 45


async def test_single_compressor_unaffected_by_stacking_fix():
    """A single compressor's cap is unchanged by the additive-stacking fix."""
    from services.cargo_utils import compute_free_cargo

    player = SimpleNamespace(id=1, active_ship_id=10)
    player_ship = SimpleNamespace(id=10, ship_name="Interceptor", modules=["AutoPacker 2"])
    ship = SimpleNamespace(name="Interceptor", cargo=20)

    db = _make_db(
        player_ship=player_ship,
        ship=ship,
        module_factory=lambda n: _compressor("AutoPacker 2", 1.25) if n == "AutoPacker 2" else None,
    )
    inventory_repo = MagicMock()
    inventory_repo.get_player_items = AsyncMock(return_value=[])

    _, _, effective_cap = await compute_free_cargo(db, inventory_repo, player)

    assert effective_cap == 25
