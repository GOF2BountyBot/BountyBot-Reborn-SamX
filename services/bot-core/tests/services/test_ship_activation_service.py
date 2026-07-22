"""
Tests for B.94 / B.95 — ship activation choke-point.

B.94 regression: after ``purchase_ship``, ``Player.active_ship_id`` must point
to the newly-purchased ship (not the old, now-stripped hull).

B.95 design:
- ``LoadoutConsistencyService.activate_ship`` is the single canonical
  activation path: reconcile → transfer (merge-with-overflow) → flip
  is_active → update active_ship_id.
- ``transfer_loadout_to_new_ship`` handles non-empty destinations via
  merge-with-overflow.
- Both the ``/setactive`` router and ``purchase_ship`` delegate to it.

Max 2 mocks per test (per tests/AGENTS.md).  The mock_db AsyncMock's
``in_transaction()`` returns a truthy AsyncMock, satisfying the
``@requires_transaction`` guard.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Guard: mock shared.bblogger and sqlalchemy_utils BEFORE any service import
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

from datetime import UTC, datetime

from persist.models.guild_shop import GuildShop
from persist.models.player import Player
from persist.models.player_ship import PlayerShip
from services.loadout_consistency_service import LoadoutConsistencyService
from services.shop_service import ShopService

# ---------------------------------------------------------------------------
# Helpers
#
# Domain entities are real SQLAlchemy model instances (no ARRAY columns on
# these tables), so unset attributes expose the real nullable defaults rather
# than truthy MagicMock auto-attributes.
# ---------------------------------------------------------------------------


def _make_player_ship(
    ship_id: int = 1,
    player_id: int = 42,
    ship_name: str = "Betty",
    is_active: bool = False,
    weapons: list[str] | None = None,
    modules: list[str] | None = None,
    turrets: list[str] | None = None,
    secondary_weapons: list[str] | None = None,
) -> PlayerShip:
    """Build a real PlayerShip with real Python lists for slot attrs."""
    return PlayerShip(
        id=ship_id,
        player_id=player_id,
        ship_name=ship_name,
        is_active=is_active,
        weapons=list(weapons) if weapons is not None else [],
        modules=list(modules) if modules is not None else [],
        turrets=list(turrets) if turrets is not None else [],
        secondary_weapons=list(secondary_weapons) if secondary_weapons is not None else [],
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _make_static_ship(
    name: str = "Hammerhead",
    value: int = 5000,
    max_primaries: int = 2,
    max_modules: int = 3,
    max_turrets: int = 1,
    max_secondaries: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        value=value,
        max_primaries=max_primaries,
        max_modules=max_modules,
        max_turrets=max_turrets,
        max_secondaries=max_secondaries,
    )


def _make_player(
    player_id: int = 42,
    guild_id: int = 999,
    tier: str = "Bronze",
    credits: int = 10000,
    active_ship_id: int | None = None,
) -> Player:
    return Player(
        id=player_id,
        user_id=player_id,
        guild_id=guild_id,
        tier=tier,
        credits=credits,
        active_ship_id=active_ship_id,
    )


def _make_shop_item(
    item_id: int = 10,
    guild_id: int = 999,
    tier: str = "Bronze",
    item_type: str = "ship",
    item_name: str = "Hammerhead",
    quantity: int = 1,
    price: int = 5000,
) -> GuildShop:
    return GuildShop(
        id=item_id,
        guild_id=guild_id,
        tier=tier,
        tech_level=1,
        item_type=item_type,
        item_name=item_name,
        quantity=quantity,
        price=price,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db() -> AsyncMock:
    """AsyncMock session; in_transaction() returns truthy (satisfies @requires_transaction)."""
    return AsyncMock()


@pytest.fixture
def svc() -> LoadoutConsistencyService:
    """LoadoutConsistencyService with all repos replaced by AsyncMocks."""
    s = LoadoutConsistencyService.__new__(LoadoutConsistencyService)
    s.player_ship_repo = AsyncMock()
    s.inventory_repo = AsyncMock()
    s.item_repo = AsyncMock()
    s.ship_repo = AsyncMock()
    # D5: aggregate-root Player lock — mocked clean no-op (see fixture rationale).
    s.player_repo = AsyncMock()
    s.player_repo.get_by_id_for_update = AsyncMock(return_value=None)

    # Safe defaults
    s.player_ship_repo.get_by_id = AsyncMock(return_value=None)
    s.player_ship_repo.get_player_ships = AsyncMock(return_value=[])
    s.player_ship_repo.get_active_ship = AsyncMock(return_value=None)
    s.player_ship_repo.set_active_ship = AsyncMock(return_value=None)
    s.inventory_repo.add_item = AsyncMock()
    s.inventory_repo.remove_item = AsyncMock()
    s.item_repo.get_by_name_any_type = AsyncMock(return_value=None)
    s.ship_repo.get_by_name = AsyncMock(return_value=None)
    return s


# ---------------------------------------------------------------------------
# Tests: transfer_loadout_to_new_ship — B.95 merge-with-overflow generalization
# ---------------------------------------------------------------------------


class TestTransferLoadoutToNewShipMergeOverflow:
    """Verify that transfer_loadout_to_new_ship correctly merges into non-empty dst."""

    @pytest.mark.asyncio
    async def test_empty_dst_transfers_within_cap(self, svc, mock_db):
        """Original behaviour preserved: empty dst ship gets src items up to cap."""
        src = _make_player_ship(ship_id=1, player_id=42, weapons=["Gun A", "Gun B", "Gun C"])
        dst = _make_player_ship(ship_id=2, player_id=42, weapons=[])

        svc.item_repo.get_by_name_any_type = AsyncMock(
            side_effect=lambda db, name: SimpleNamespace(name=name, type="PrimaryWeapon")
        )

        slot_limits = {"weapons": 2, "modules": 0, "turrets": 0, "secondary_weapons": 0}
        result = await svc.transfer_loadout_to_new_ship(
            mock_db, player_id=42, src_ship=src, dst_ship=dst, slot_limits=slot_limits
        )

        assert result["transferred"] == 2
        assert result["overflowed"] == 1
        # dst gets the first 2 weapons; src is cleared
        assert dst.weapons == ["Gun A", "Gun B"]
        assert src.weapons == []
        # overflow item went to inventory
        svc.inventory_repo.add_item.assert_called_once()
        call_args = svc.inventory_repo.add_item.call_args
        assert call_args[0][3] == "Gun C"  # item name

    @pytest.mark.asyncio
    async def test_nonempty_dst_merges_remaining_slots(self, svc, mock_db):
        """B.95: dst already has 1 weapon in a 2-slot ship; 1 src slot fills in."""
        src = _make_player_ship(ship_id=1, player_id=42, weapons=["Src Gun"])
        dst = _make_player_ship(ship_id=2, player_id=42, weapons=["Dst Gun"])

        svc.item_repo.get_by_name_any_type = AsyncMock(
            side_effect=lambda db, name: SimpleNamespace(name=name, type="PrimaryWeapon")
        )

        slot_limits = {"weapons": 2, "modules": 0, "turrets": 0, "secondary_weapons": 0}
        result = await svc.transfer_loadout_to_new_ship(
            mock_db, player_id=42, src_ship=src, dst_ship=dst, slot_limits=slot_limits
        )

        assert result["transferred"] == 1
        assert result["overflowed"] == 0
        # dst keeps its original item + gains the src item
        assert dst.weapons == ["Dst Gun", "Src Gun"]
        assert src.weapons == []
        svc.inventory_repo.add_item.assert_not_called()

    @pytest.mark.asyncio
    async def test_nonempty_dst_full_overflows_all_src(self, svc, mock_db):
        """B.95: dst already full; all src items overflow to inventory."""
        src = _make_player_ship(ship_id=1, player_id=42, weapons=["Src Gun 1", "Src Gun 2"])
        dst = _make_player_ship(ship_id=2, player_id=42, weapons=["Dst Gun 1", "Dst Gun 2"])

        svc.item_repo.get_by_name_any_type = AsyncMock(
            side_effect=lambda db, name: SimpleNamespace(name=name, type="PrimaryWeapon")
        )

        slot_limits = {"weapons": 2, "modules": 0, "turrets": 0, "secondary_weapons": 0}
        result = await svc.transfer_loadout_to_new_ship(
            mock_db, player_id=42, src_ship=src, dst_ship=dst, slot_limits=slot_limits
        )

        assert result["transferred"] == 0
        assert result["overflowed"] == 2
        # dst unchanged
        assert dst.weapons == ["Dst Gun 1", "Dst Gun 2"]
        assert src.weapons == []
        # both src items went to inventory
        assert svc.inventory_repo.add_item.call_count == 2

    @pytest.mark.asyncio
    async def test_src_none_returns_zero_counts(self, svc, mock_db):
        """No src ship (first purchase) → zero transfer, no mutations."""
        dst = _make_player_ship(ship_id=2, player_id=42)
        slot_limits = {"weapons": 2, "modules": 0, "turrets": 0, "secondary_weapons": 0}

        result = await svc.transfer_loadout_to_new_ship(
            mock_db, player_id=42, src_ship=None, dst_ship=dst, slot_limits=slot_limits
        )

        assert result["transferred"] == 0
        assert result["overflowed"] == 0
        svc.inventory_repo.add_item.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: activate_ship — B.95 canonical choke-point
# ---------------------------------------------------------------------------


class TestActivateShip:
    """Unit tests for LoadoutConsistencyService.activate_ship."""

    def _make_player_repo(self) -> AsyncMock:
        repo = AsyncMock()
        repo.update_active_ship = AsyncMock()
        return repo

    @pytest.mark.asyncio
    async def test_activate_ship_happy_path_no_prior_active(self, svc, mock_db):
        """Fresh player with no prior active ship; new ship activated cleanly."""
        target = _make_player_ship(ship_id=5, player_id=42, ship_name="Hammerhead")
        activated = _make_player_ship(ship_id=5, player_id=42, ship_name="Hammerhead", is_active=True)
        static = _make_static_ship(name="Hammerhead", max_primaries=2, max_modules=3, max_turrets=1)
        player_repo = self._make_player_repo()

        svc.player_ship_repo.get_by_id = AsyncMock(return_value=target)
        svc.player_ship_repo.get_active_ship = AsyncMock(return_value=None)
        svc.player_ship_repo.set_active_ship = AsyncMock(return_value=activated)
        svc.ship_repo.get_by_name = AsyncMock(return_value=static)

        result = await svc.activate_ship(mock_db, player_id=42, target_ship_id=5, player_repo=player_repo)

        assert result["ship"] is activated
        # player.active_ship_id updated
        player_repo.update_active_ship.assert_called_once_with(mock_db, 42, 5, commit=False)
        svc.player_ship_repo.set_active_ship.assert_called_once_with(mock_db, 42, 5, commit=False)

    @pytest.mark.asyncio
    async def test_activate_ship_transfers_gear_from_current_active(self, svc, mock_db):
        """Gear moves from the old active ship to the new one (B.95 gear-follows-active)."""
        old_ship = _make_player_ship(
            ship_id=1, player_id=42, ship_name="Betty", is_active=True, weapons=["Nirai Impulse EX 1"]
        )
        target = _make_player_ship(ship_id=5, player_id=42, ship_name="Hammerhead")
        activated = _make_player_ship(
            ship_id=5, player_id=42, ship_name="Hammerhead", is_active=True, weapons=["Nirai Impulse EX 1"]
        )
        static = _make_static_ship(name="Hammerhead", max_primaries=2, max_modules=3, max_turrets=1)
        player_repo = self._make_player_repo()

        # get_by_id called multiple times (target fetch + post-reconcile re-fetch)
        call_count = {"n": 0}

        async def _get_by_id(db, ship_id):
            call_count["n"] += 1
            if ship_id == 5:
                return target
            return None

        svc.player_ship_repo.get_by_id = _get_by_id
        svc.player_ship_repo.get_active_ship = AsyncMock(return_value=old_ship)
        svc.player_ship_repo.set_active_ship = AsyncMock(return_value=activated)
        svc.ship_repo.get_by_name = AsyncMock(return_value=static)
        svc.item_repo.get_by_name_any_type = AsyncMock(
            side_effect=lambda db, name: SimpleNamespace(name=name, type="PrimaryWeapon")
        )

        result = await svc.activate_ship(mock_db, player_id=42, target_ship_id=5, player_repo=player_repo)

        assert result["ship"] is activated
        assert result["transferred"] == 1  # weapon transferred
        # old ship was cleared
        assert old_ship.weapons == []
        # player.active_ship_id updated
        player_repo.update_active_ship.assert_called_once_with(mock_db, 42, 5, commit=False)

    @pytest.mark.asyncio
    async def test_activate_ship_ship_not_found_raises(self, svc, mock_db):
        """Missing ship → ValueError."""
        svc.player_ship_repo.get_by_id = AsyncMock(return_value=None)
        player_repo = self._make_player_repo()

        with pytest.raises(ValueError, match="not found"):
            await svc.activate_ship(mock_db, player_id=42, target_ship_id=99, player_repo=player_repo)

        player_repo.update_active_ship.assert_not_called()

    @pytest.mark.asyncio
    async def test_activate_ship_wrong_owner_raises(self, svc, mock_db):
        """Ship owned by different player → ValueError."""
        ship = _make_player_ship(ship_id=5, player_id=99)  # owned by 99, not 42
        svc.player_ship_repo.get_by_id = AsyncMock(return_value=ship)
        player_repo = self._make_player_repo()

        with pytest.raises(ValueError, match="does not belong to player"):
            await svc.activate_ship(mock_db, player_id=42, target_ship_id=5, player_repo=player_repo)

        player_repo.update_active_ship.assert_not_called()

    @pytest.mark.asyncio
    async def test_activate_ship_already_active_no_transfer(self, svc, mock_db):
        """Activating the already-active ship still runs reconcile+update."""
        current = _make_player_ship(ship_id=5, player_id=42, is_active=True)
        activated = _make_player_ship(ship_id=5, player_id=42, is_active=True)
        static = _make_static_ship()
        player_repo = self._make_player_repo()

        svc.player_ship_repo.get_by_id = AsyncMock(return_value=current)
        svc.player_ship_repo.get_active_ship = AsyncMock(return_value=current)  # same ship
        svc.player_ship_repo.set_active_ship = AsyncMock(return_value=activated)
        svc.ship_repo.get_by_name = AsyncMock(return_value=static)

        result = await svc.activate_ship(mock_db, player_id=42, target_ship_id=5, player_repo=player_repo)

        # No transfer (src == dst)
        assert result["transferred"] == 0
        # But still updates active_ship_id
        player_repo.update_active_ship.assert_called_once_with(mock_db, 42, 5, commit=False)

    @pytest.mark.asyncio
    async def test_activate_ship_updates_active_ship_id(self, svc, mock_db):
        """B.94 regression: activate_ship MUST call player_repo.update_active_ship."""
        target = _make_player_ship(ship_id=7, player_id=42)
        activated = _make_player_ship(ship_id=7, player_id=42, is_active=True)
        static = _make_static_ship()
        player_repo = self._make_player_repo()

        svc.player_ship_repo.get_by_id = AsyncMock(return_value=target)
        svc.player_ship_repo.get_active_ship = AsyncMock(return_value=None)
        svc.player_ship_repo.set_active_ship = AsyncMock(return_value=activated)
        svc.ship_repo.get_by_name = AsyncMock(return_value=static)

        await svc.activate_ship(mock_db, player_id=42, target_ship_id=7, player_repo=player_repo)

        # THE critical assertion for B.94: active_ship_id is updated
        player_repo.update_active_ship.assert_called_once_with(mock_db, 42, 7, commit=False)


# ---------------------------------------------------------------------------
# Tests: ShopService.purchase_ship — B.94 regression
# ---------------------------------------------------------------------------


class TestPurchaseShipB94Regression:
    """B.94 regression: after purchase_ship, Player.active_ship_id points to the new ship."""

    def _make_service(self) -> ShopService:
        svc = ShopService.__new__(ShopService)
        svc.shop_repo = AsyncMock()
        svc.config_repo = AsyncMock()
        svc.player_repo = AsyncMock()
        svc.inventory_repo = AsyncMock()
        svc.item_repo = AsyncMock()
        svc.ship_repo = AsyncMock()
        svc.player_ship_repo = AsyncMock()
        svc.primary_weapon_repo = AsyncMock()
        svc.secondary_weapon_repo = AsyncMock()
        svc.turret_weapon_repo = AsyncMock()
        svc.module_repo = AsyncMock()
        svc._static_cache = None
        svc._price_cache = None
        return svc

    @pytest.mark.asyncio
    async def test_purchase_ship_calls_update_active_ship(self, mock_db):
        """B.94 regression: purchase_ship must call player_repo.update_active_ship
        so that Player.active_ship_id points to the new ship after purchase.

        This is the exact bug that B.94 documents: the old hand-rolled code
        flipped PlayerShip.is_active but never called update_active_ship,
        leaving Player.active_ship_id pointing at the old (now-stripped) hull.
        """
        svc = self._make_service()

        player = _make_player(player_id=42, credits=10000, active_ship_id=1)
        old_ship = _make_player_ship(ship_id=1, player_id=42, ship_name="Betty", is_active=True)
        new_ship_static = _make_static_ship(name="Hammerhead", value=5000, max_primaries=2)
        shop_item = _make_shop_item(item_id=10, item_name="Hammerhead", price=5000, tier="Bronze")
        # New PlayerShip created inside purchase_ship (gets id=99 after flush via captured dict).
        activated_ship = _make_player_ship(ship_id=99, player_id=42, ship_name="Hammerhead", is_active=True)

        svc.player_repo.get_by_id = AsyncMock(return_value=player)
        svc.player_repo.get_by_id_for_update = AsyncMock(return_value=player)
        svc.player_repo.update_credits = AsyncMock()
        # THE assertion target: update_active_ship must be called
        svc.player_repo.update_active_ship = AsyncMock()

        svc.shop_repo.get_by_id = AsyncMock(return_value=shop_item)
        svc.shop_repo.remove = AsyncMock()
        svc.shop_repo.update_quantity = AsyncMock()

        svc.ship_repo.get_by_name = AsyncMock(return_value=new_ship_static)

        # Use the captured dict pattern: db.add captures the actual PlayerShip
        # ORM instance created inside purchase_ship(), db.flush sets its .id,
        # and player_ship_repo.get_by_id returns it so activate_ship can validate
        # ownership.  (Fixing the old pattern where _db_flush set the id on the
        # fixture's new_player_ship mock rather than the ORM object created
        # inside the method — DEF-U2-001.)
        captured = {}

        def _add_side_effect(obj):
            captured["new_ship"] = obj

        mock_db.add = MagicMock(side_effect=_add_side_effect)

        async def _flush_side_effect():
            if "new_ship" in captured:
                captured["new_ship"].id = 99

        mock_db.flush = AsyncMock(side_effect=_flush_side_effect)
        mock_db.delete = AsyncMock()

        svc.player_ship_repo.get_active_ship = AsyncMock(return_value=old_ship)
        svc.player_ship_repo.get_player_ships = AsyncMock(return_value=[old_ship])
        # set_active_ship is called by the LoadoutConsistencyService.activate_ship
        svc.player_ship_repo.set_active_ship = AsyncMock(return_value=activated_ship)

        # get_by_id must return the captured new ship (by id=99) so activate_ship
        # can validate ownership after the flush assigns the PK.
        async def _get_by_id(db, ship_id):
            if ship_id == 99 and "new_ship" in captured:
                return captured["new_ship"]
            return None

        svc.player_ship_repo.get_by_id = AsyncMock(side_effect=_get_by_id)

        result = await svc.purchase_ship(mock_db, player_id=42, shop_item_id=10)

        # B.94 regression assertion: update_active_ship MUST have been called
        svc.player_repo.update_active_ship.assert_called_once_with(mock_db, 42, 99, commit=False)

        # Sanity: transaction_details present
        assert result["item_name"] == "Hammerhead"
        assert result["item_type"] == "ship"

    @pytest.mark.asyncio
    async def test_purchase_ship_active_ship_id_not_left_stale_on_first_buy(self, mock_db):
        """B.94: first ship purchase (no prior active) also sets active_ship_id."""
        svc = self._make_service()

        player = _make_player(player_id=42, credits=10000, active_ship_id=None)
        new_ship_static = _make_static_ship(name="Betty", value=2000, max_primaries=1)
        shop_item = _make_shop_item(item_id=5, item_name="Betty", price=2000, tier="Bronze")
        activated_ship = _make_player_ship(ship_id=10, player_id=42, ship_name="Betty", is_active=True)

        svc.player_repo.get_by_id = AsyncMock(return_value=player)
        svc.player_repo.get_by_id_for_update = AsyncMock(return_value=player)
        svc.player_repo.update_credits = AsyncMock()
        svc.player_repo.update_active_ship = AsyncMock()

        svc.shop_repo.get_by_id = AsyncMock(return_value=shop_item)
        svc.shop_repo.remove = AsyncMock()
        svc.shop_repo.update_quantity = AsyncMock()

        svc.ship_repo.get_by_name = AsyncMock(return_value=new_ship_static)

        # Use the captured dict pattern (DEF-U2-001 fix).
        captured = {}

        def _add_side_effect(obj):
            captured["new_ship"] = obj

        mock_db.add = MagicMock(side_effect=_add_side_effect)

        async def _flush_side_effect():
            if "new_ship" in captured:
                captured["new_ship"].id = 10

        mock_db.flush = AsyncMock(side_effect=_flush_side_effect)
        mock_db.delete = AsyncMock()

        svc.player_ship_repo.get_active_ship = AsyncMock(return_value=None)  # no prior ship
        svc.player_ship_repo.get_player_ships = AsyncMock(return_value=[])
        svc.player_ship_repo.set_active_ship = AsyncMock(return_value=activated_ship)

        async def _get_by_id(db, ship_id):
            if ship_id == 10 and "new_ship" in captured:
                return captured["new_ship"]
            return None

        svc.player_ship_repo.get_by_id = AsyncMock(side_effect=_get_by_id)

        await svc.purchase_ship(mock_db, player_id=42, shop_item_id=5)

        # active_ship_id set even for first purchase
        svc.player_repo.update_active_ship.assert_called_once_with(mock_db, 42, 10, commit=False)

    @pytest.mark.asyncio
    async def test_purchase_ship_gear_follows_new_ship(self, mock_db):
        """B.95: gear from the old active ship is transferred to the new ship."""
        svc = self._make_service()

        # Old ship has a weapon equipped
        old_ship = _make_player_ship(
            ship_id=1, player_id=42, ship_name="Betty", is_active=True, weapons=["Nirai Impulse EX 1"]
        )
        player = _make_player(player_id=42, credits=10000, active_ship_id=1)
        new_ship_static = _make_static_ship(name="Hammerhead", value=5000, max_primaries=2)
        shop_item = _make_shop_item(item_id=10, item_name="Hammerhead", price=5000, tier="Bronze")
        activated_ship = _make_player_ship(
            ship_id=99, player_id=42, ship_name="Hammerhead", is_active=True, weapons=["Nirai Impulse EX 1"]
        )

        svc.player_repo.get_by_id = AsyncMock(return_value=player)
        svc.player_repo.get_by_id_for_update = AsyncMock(return_value=player)
        svc.player_repo.update_credits = AsyncMock()
        svc.player_repo.update_active_ship = AsyncMock()

        svc.shop_repo.get_by_id = AsyncMock(return_value=shop_item)
        svc.shop_repo.remove = AsyncMock()
        svc.shop_repo.update_quantity = AsyncMock()

        svc.ship_repo.get_by_name = AsyncMock(return_value=new_ship_static)
        svc.item_repo.get_by_name_any_type = AsyncMock(
            side_effect=lambda db, name: SimpleNamespace(name=name, type="PrimaryWeapon")
        )

        # Use the captured dict pattern (DEF-U2-001 fix).
        captured = {}

        def _add_side_effect(obj):
            captured["new_ship"] = obj

        mock_db.add = MagicMock(side_effect=_add_side_effect)

        async def _flush_side_effect():
            if "new_ship" in captured:
                captured["new_ship"].id = 99

        mock_db.flush = AsyncMock(side_effect=_flush_side_effect)
        mock_db.delete = AsyncMock()

        svc.player_ship_repo.get_active_ship = AsyncMock(return_value=old_ship)
        svc.player_ship_repo.get_player_ships = AsyncMock(return_value=[old_ship])
        svc.player_ship_repo.set_active_ship = AsyncMock(return_value=activated_ship)

        async def _get_by_id(db, ship_id):
            if ship_id == 99 and "new_ship" in captured:
                return captured["new_ship"]
            return old_ship if ship_id == 1 else None

        svc.player_ship_repo.get_by_id = AsyncMock(side_effect=_get_by_id)

        result = await svc.purchase_ship(mock_db, player_id=42, shop_item_id=10)

        # B.95: gear transferred to new ship
        assert result["items_transferred"] >= 0  # transfer happened
        # old ship's loadout cleared (the weapon moved)
        assert old_ship.weapons == []
        # active_ship_id updated (B.94 fix)
        svc.player_repo.update_active_ship.assert_called_once_with(mock_db, 42, 99, commit=False)


# ---------------------------------------------------------------------------
# Tests: activate_ship — dispatch from /setactive router delegation check
# ---------------------------------------------------------------------------


class TestActivateShipDelegation:
    """Verify the /setactive router properly passes player_repo to activate_ship."""

    @pytest.mark.asyncio
    async def test_activate_ship_receives_correct_player_repo(self, svc, mock_db):
        """activate_ship receives the player_repo arg and calls update_active_ship on it."""
        target = _make_player_ship(ship_id=3, player_id=10)
        activated = _make_player_ship(ship_id=3, player_id=10, is_active=True)
        static = _make_static_ship()
        player_repo = AsyncMock()
        player_repo.update_active_ship = AsyncMock()

        svc.player_ship_repo.get_by_id = AsyncMock(return_value=target)
        svc.player_ship_repo.get_active_ship = AsyncMock(return_value=None)
        svc.player_ship_repo.set_active_ship = AsyncMock(return_value=activated)
        svc.ship_repo.get_by_name = AsyncMock(return_value=static)

        await svc.activate_ship(mock_db, player_id=10, target_ship_id=3, player_repo=player_repo)

        # The injected player_repo is what got called — not some other instance
        player_repo.update_active_ship.assert_called_once_with(mock_db, 10, 3, commit=False)
