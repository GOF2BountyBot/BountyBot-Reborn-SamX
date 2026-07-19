"""
Unit tests for ShopService.

The shared.bblogger module is mocked via sys.modules BEFORE any service
module is imported (see conftest.py at the tests/ root).
"""

import sys
import types
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Guard: ensure shared.bblogger and sqlalchemy_utils are mocked before
# importing service code. The models/__init__.py auto-imports discord_message
# which requires sqlalchemy_utils (not installed in the test environment).
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

from services.exceptions import InvalidItemTypeError
from services.game_constants import GameConstants
from services.shop_service import ShopService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_player(
    player_id: int = 1,
    guild_id: int = 999,
    tier: str = "Bronze",
    credits: int = 1000,
) -> MagicMock:
    p = MagicMock()
    p.id = player_id
    p.guild_id = guild_id
    p.tier = tier
    p.credits = credits
    return p


def _make_shop_item(
    item_id: int = 10,
    guild_id: int = 999,
    tier: str = "Bronze",
    item_type: str = "weapon",
    item_name: str = "Micro Gun MK I",
    quantity: int = 5,
    price: int = 200,
    last_restocked: datetime | None = None,
) -> MagicMock:
    item = MagicMock()
    item.id = item_id
    item.guild_id = guild_id
    item.tier = tier
    item.item_type = item_type
    item.item_name = item_name
    item.quantity = quantity
    item.price = price
    item.last_restocked = last_restocked or datetime(2025, 1, 1, tzinfo=UTC)
    item.is_refresh_due = MagicMock(return_value=False)
    return item


def _make_inventory_item(
    quantity: int = 3,
    item_name: str = "Micro Gun MK I",
    item_type: str = "weapon",
) -> MagicMock:
    item = MagicMock()
    item.quantity = quantity
    item.item_name = item_name
    item.item_type = item_type
    return item


def _make_ship_static(
    name: str = "Hammerhead",
    value: int = 5000,
    max_primaries: int = 2,
    max_modules: int = 2,
    max_turrets: int = 1,
    max_secondaries: int = 0,
) -> MagicMock:
    """Create a mock static Ship model."""
    ship = MagicMock()
    ship.name = name
    ship.value = value
    ship.max_primaries = max_primaries
    ship.max_modules = max_modules
    ship.max_turrets = max_turrets
    ship.max_secondaries = max_secondaries
    return ship


def _make_player_ship(
    ship_id: int = 100,
    player_id: int = 1,
    ship_name: str = "Hammerhead",
    is_active: bool = True,
    weapons: list | None = None,
    modules: list | None = None,
    turrets: list | None = None,
) -> MagicMock:
    """Create a mock PlayerShip instance."""
    ps = MagicMock()
    ps.id = ship_id
    ps.player_id = player_id
    ps.ship_name = ship_name
    ps.is_active = is_active
    ps.weapons = weapons if weapons is not None else []
    ps.modules = modules if modules is not None else []
    ps.turrets = turrets if turrets is not None else []
    ps.secondary_weapons = []
    return ps


def _make_config(
    sale_price_factor: float = 0.8,
    tech_level_probabilities: dict | None = None,
    xp_thresholds: dict | None = None,
) -> MagicMock:
    config = MagicMock()
    config.sale_price_factor = sale_price_factor
    config.tech_level_probabilities = tech_level_probabilities or {
        "same_level": 0.7,
        "one_lower": 0.2,
        "two_lower": 0.1,
    }
    config.get_count_range = MagicMock(return_value={"min": 1, "max": 2})
    config.get_quantity_range = MagicMock(return_value={"min": 1, "max": 3})
    return config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()

    @asynccontextmanager
    async def _mock_begin():
        yield

    db.begin = _mock_begin
    return db


@pytest.fixture
def mock_shop_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_shop_items = AsyncMock(return_value=[])
    repo.get_by_id = AsyncMock(return_value=None)
    repo.create_or_update = AsyncMock()
    repo.remove = AsyncMock()
    repo.update_quantity = AsyncMock()
    repo.clear_shop_tier = AsyncMock()
    repo.clear_all_guild_shops = AsyncMock()
    repo.get_shop_item_by_name = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def mock_config_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_by_guild_id = AsyncMock(return_value=None)
    repo.create_default_config = AsyncMock()
    return repo


@pytest.fixture
def mock_player_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=None)
    repo.get_by_id_for_update = AsyncMock(return_value=None)
    repo.update_credits = AsyncMock()
    return repo


@pytest.fixture
def mock_inventory_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_player_item = AsyncMock(return_value=None)
    repo.get_player_items_by_name = AsyncMock(return_value=[])  # A.42b: used by sell_item
    repo.add_item = AsyncMock()
    repo.remove_item = AsyncMock()
    return repo


@pytest.fixture
def mock_ship_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_by_name = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def mock_primary_weapon_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_by_name = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def mock_secondary_weapon_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_by_name = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def mock_turret_weapon_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_by_name = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def mock_module_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_by_name = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def mock_player_ship_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=None)
    repo.get_active_ship = AsyncMock(return_value=None)
    repo.set_active_ship = AsyncMock()
    return repo


@pytest.fixture
def mock_item_repo() -> AsyncMock:
    """Mock ItemRepository — used by write-site concrete type resolution."""
    repo = AsyncMock()
    # Default: return None (no item found; tests can override per-item)
    repo.get_by_name_any_type = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def service(
    mock_shop_repo,
    mock_config_repo,
    mock_player_repo,
    mock_inventory_repo,
    mock_ship_repo,
    mock_player_ship_repo,
    mock_primary_weapon_repo,
    mock_secondary_weapon_repo,
    mock_turret_weapon_repo,
    mock_module_repo,
    mock_item_repo,
) -> ShopService:
    svc = ShopService()
    svc.shop_repo = mock_shop_repo
    svc.config_repo = mock_config_repo
    svc.player_repo = mock_player_repo
    svc.inventory_repo = mock_inventory_repo
    svc.item_repo = mock_item_repo
    svc.ship_repo = mock_ship_repo
    svc.player_ship_repo = mock_player_ship_repo
    svc.primary_weapon_repo = mock_primary_weapon_repo
    svc.secondary_weapon_repo = mock_secondary_weapon_repo
    svc.turret_weapon_repo = mock_turret_weapon_repo
    svc.module_repo = mock_module_repo
    return svc


# ===========================================================================
# Tests: get_shop_items
# ===========================================================================


class TestGetShopItems:
    """Tests for ShopService.get_shop_items."""

    @pytest.mark.asyncio
    async def test_returns_shop_items_for_valid_tier(self, service, mock_db, mock_shop_repo):
        """Items for a valid tier are returned."""
        items = [_make_shop_item(), _make_shop_item(item_id=11, item_name="Laser")]
        mock_shop_repo.get_shop_items.return_value = items
        # Shop already has items, so no refresh needed
        service._check_and_refresh_shop = AsyncMock()

        result = await service.get_shop_items(mock_db, guild_id=999, tier="Bronze")

        assert result == items

    @pytest.mark.asyncio
    async def test_raises_for_invalid_tier(self, service, mock_db):
        """ValueError raised for an unrecognised tier."""
        with pytest.raises(ValueError, match="Invalid tier"):
            await service.get_shop_items(mock_db, guild_id=999, tier="Diamond")

    @pytest.mark.asyncio
    async def test_raises_for_invalid_item_type_filter(self, service, mock_db):
        """InvalidItemTypeError raised for an unrecognised item type filter (A.33 fix)."""
        service._check_and_refresh_shop = AsyncMock()
        with pytest.raises(InvalidItemTypeError):
            await service.get_shop_items(mock_db, guild_id=999, tier="Bronze", item_type="banana")

    @pytest.mark.asyncio
    async def test_passes_item_type_filter_to_repo(self, service, mock_db, mock_shop_repo):
        """item_type filter is forwarded to the repository using concrete type (A.36 fix)."""
        service._check_and_refresh_shop = AsyncMock()
        mock_shop_repo.get_shop_items.return_value = []

        # "weapon" generic alias expands to ("primary_weapon", "turret_weapon") today;
        # service should call get_shop_items_by_types for multi-type expansion
        mock_shop_repo.get_shop_items_by_types = AsyncMock(return_value=[])
        await service.get_shop_items(mock_db, guild_id=999, tier="Silver", item_type="weapon")

        # "weapon" expands to 2+ concrete types → should use get_shop_items_by_types
        mock_shop_repo.get_shop_items_by_types.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_all_valid_tiers_accepted(self, service, mock_db, mock_shop_repo):
        """All four valid tiers pass validation."""
        service._check_and_refresh_shop = AsyncMock()
        mock_shop_repo.get_shop_items.return_value = []

        for tier in ShopService.VALID_TIERS:
            await service.get_shop_items(mock_db, guild_id=999, tier=tier)

    @pytest.mark.asyncio
    async def test_re_raises_repo_exception(self, service, mock_db, mock_shop_repo):
        """Exceptions from the repository propagate."""
        service._check_and_refresh_shop = AsyncMock()
        mock_shop_repo.get_shop_items.side_effect = RuntimeError("db gone")

        with pytest.raises(RuntimeError, match="db gone"):
            await service.get_shop_items(mock_db, guild_id=999, tier="Bronze")


# ===========================================================================
# Tests: purchase_item
# ===========================================================================


class TestPurchaseItem:
    """Tests for ShopService.purchase_item."""

    @pytest.mark.asyncio
    async def test_successful_purchase_returns_transaction_details(
        self, service, mock_db, mock_player_repo, mock_shop_repo
    ):
        """Returns transaction details for a valid purchase."""
        player = _make_player(tier="Silver", credits=500)
        shop_item = _make_shop_item(tier="Silver", quantity=5, price=100)
        mock_player_repo.get_by_id.return_value = player
        # purchase_item re-fetches under lock; use same player object so credits is a real int
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_shop_repo.get_by_id.return_value = shop_item

        result = await service.purchase_item(mock_db, player_id=1, shop_item_id=10, quantity=2)

        assert result["player_id"] == 1
        assert result["quantity"] == 2
        assert result["unit_price"] == 100
        assert result["total_cost"] == 200
        assert result["remaining_credits"] == 300
        assert result["remaining_shop_quantity"] == 3

    @pytest.mark.asyncio
    async def test_raises_when_player_not_found(self, service, mock_db, mock_player_repo):
        """ValueError raised when player does not exist."""
        mock_player_repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="Player 99 not found"):
            await service.purchase_item(mock_db, player_id=99, shop_item_id=10)

    @pytest.mark.asyncio
    async def test_raises_when_shop_item_not_found(self, service, mock_db, mock_player_repo, mock_shop_repo):
        """ValueError raised when shop item does not exist."""
        mock_player_repo.get_by_id.return_value = _make_player()
        mock_player_repo.get_by_id_for_update.return_value = _make_player()
        mock_shop_repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="Shop item 55 not found"):
            await service.purchase_item(mock_db, player_id=1, shop_item_id=55)

    @pytest.mark.asyncio
    async def test_raises_when_player_tier_too_low(self, service, mock_db, mock_player_repo, mock_shop_repo):
        """ValueError raised when player cannot access the shop tier."""
        player = _make_player(tier="Bronze")
        shop_item = _make_shop_item(tier="Gold")  # Requires Gold+
        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_shop_repo.get_by_id.return_value = shop_item

        with pytest.raises(ValueError, match="cannot access"):
            await service.purchase_item(mock_db, player_id=1, shop_item_id=10)

    @pytest.mark.asyncio
    async def test_raises_when_insufficient_shop_quantity(self, service, mock_db, mock_player_repo, mock_shop_repo):
        """ValueError raised when shop has fewer than requested quantity."""
        player = _make_player(tier="Bronze", credits=5000)
        shop_item = _make_shop_item(tier="Bronze", quantity=1, price=100)
        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_shop_repo.get_by_id.return_value = shop_item

        with pytest.raises(ValueError, match="Insufficient quantity"):
            await service.purchase_item(mock_db, player_id=1, shop_item_id=10, quantity=5)

    @pytest.mark.asyncio
    async def test_raises_when_insufficient_credits(self, service, mock_db, mock_player_repo, mock_shop_repo):
        """ValueError raised when player cannot afford the purchase."""
        player = _make_player(tier="Bronze", credits=50)
        shop_item = _make_shop_item(tier="Bronze", quantity=5, price=200)
        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_shop_repo.get_by_id.return_value = shop_item

        with pytest.raises(ValueError, match="Insufficient credits"):
            await service.purchase_item(mock_db, player_id=1, shop_item_id=10, quantity=1)

    @pytest.mark.asyncio
    async def test_removes_shop_item_when_quantity_reaches_zero(
        self, service, mock_db, mock_player_repo, mock_shop_repo
    ):
        """db.delete is called when shop quantity hits 0 after purchase."""
        player = _make_player(tier="Bronze", credits=500)
        shop_item = _make_shop_item(tier="Bronze", quantity=1, price=100)
        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_shop_repo.get_by_id.return_value = shop_item

        await service.purchase_item(mock_db, player_id=1, shop_item_id=10, quantity=1)

        mock_db.delete.assert_awaited_once_with(shop_item)

    @pytest.mark.asyncio
    async def test_updates_quantity_when_partial_purchase(self, service, mock_db, mock_player_repo, mock_shop_repo):
        """Shop item quantity is updated for partial purchase."""
        player = _make_player(tier="Bronze", credits=1000)
        shop_item = _make_shop_item(tier="Bronze", quantity=5, price=100)
        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_shop_repo.get_by_id.return_value = shop_item

        await service.purchase_item(mock_db, player_id=1, shop_item_id=10, quantity=2)

        assert shop_item.quantity == 3


# ===========================================================================
# Tests: sell_item
# ===========================================================================


class TestSellItem:
    """Tests for ShopService.sell_item.

    A.42b: sell_item no longer accepts item_type or target_tier.
    - item_type is resolved from inventory row by item_name.
    - target_tier always matches player.tier (A.42c).
    """

    @pytest.mark.asyncio
    async def test_successful_sell_returns_transaction_details(
        self, service, mock_db, mock_player_repo, mock_inventory_repo, mock_shop_repo
    ):
        """Happy-path: /sell Micro Gun MK I → concrete item_type resolved, lands in player.tier shop.

        Acceptance criterion (A.42 regression): player with primary_weapon in inventory
        can sell by item_name only. Response item_type is the concrete 'primary_weapon'
        (not the generic 'weapon' that _SELL_TYPE_MAP used to downgrade to).
        """
        player = _make_player(guild_id=999, credits=500, tier="Bronze")
        inventory_item = _make_inventory_item(quantity=2, item_type="primary_weapon")
        mock_player_repo.get_by_id.return_value = player
        # sell_item re-fetches under lock; use same player object so credits is a real int
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_inventory_repo.get_player_items_by_name.return_value = [inventory_item]
        mock_shop_repo.get_shop_item_by_name.return_value = None  # No existing shop item

        # Mock _get_item_base_price to return a deterministic value
        service._get_item_base_price = AsyncMock(return_value=500)

        result = await service.sell_item(mock_db, player_id=1, item_name="Micro Gun MK I", quantity=1)

        assert result["player_id"] == 1
        assert result["item_type"] == "primary_weapon"  # concrete type, NOT the generic 'weapon' (A.42 fix)
        assert result["item_name"] == "Micro Gun MK I"
        assert result["quantity"] == 1
        assert result["unit_sell_price"] == 500  # full value, no tax
        assert result["total_sell_value"] == 500
        assert result["new_credits"] == 1000  # 500 + 500
        assert result["target_shop_tier"] == "Bronze"  # player.tier, not a param (A.42c)

        # Verify inventory lookup was by name (not by type)
        mock_inventory_repo.get_player_items_by_name.assert_awaited_once_with(mock_db, 1, "Micro Gun MK I")

    @pytest.mark.asyncio
    async def test_raises_when_player_not_found(self, service, mock_db, mock_player_repo):
        """ValueError raised when player does not exist."""
        mock_player_repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="Player 7 not found"):
            await service.sell_item(mock_db, player_id=7, item_name="Gun")

    @pytest.mark.asyncio
    async def test_raises_when_item_not_in_inventory(self, service, mock_db, mock_player_repo, mock_inventory_repo):
        """ValueError raised when player does not own the item (empty inventory lookup).

        B.7: Error message must NOT contain numeric player_id; must use 'your inventory'.
        """
        mock_player_repo.get_by_id.return_value = _make_player()
        mock_player_repo.get_by_id_for_update.return_value = _make_player()
        mock_inventory_repo.get_player_items_by_name.return_value = []  # not found

        with pytest.raises(ValueError, match="not found in your inventory") as exc_info:
            await service.sell_item(mock_db, player_id=1, item_name="Missing Gun")

        # B.7: numeric player_id must NOT appear in user-facing error text
        assert "1" not in str(exc_info.value), "player_id must not appear in error message"

    @pytest.mark.asyncio
    async def test_raises_when_insufficient_inventory_quantity(
        self, service, mock_db, mock_player_repo, mock_inventory_repo
    ):
        """ValueError raised when player has fewer than requested quantity."""
        mock_player_repo.get_by_id.return_value = _make_player()
        mock_player_repo.get_by_id_for_update.return_value = _make_player()
        inventory_item = _make_inventory_item(quantity=1, item_type="primary_weapon")
        mock_inventory_repo.get_player_items_by_name.return_value = [inventory_item]

        with pytest.raises(ValueError, match="Insufficient item quantity"):
            await service.sell_item(mock_db, player_id=1, item_name="Gun", quantity=5)

    @pytest.mark.asyncio
    async def test_raises_for_cross_type_name_collision(self, service, mock_db, mock_player_repo, mock_inventory_repo):
        """InvalidItemTypeError raised when item_name appears under multiple concrete types.

        This is an impossible scenario with the current item catalog (verified: 146 items,
        146 distinct names, zero cross-type name collisions), but the service guards
        defensively to preserve the invariant that sells are always unambiguous writes.
        """
        mock_player_repo.get_by_id.return_value = _make_player()
        mock_player_repo.get_by_id_for_update.return_value = _make_player()
        # Mock two rows for the same item_name but different concrete types
        row_a = _make_inventory_item(quantity=1, item_type="primary_weapon", item_name="AmbiguousItem")
        row_b = _make_inventory_item(quantity=1, item_type="turret_weapon", item_name="AmbiguousItem")
        mock_inventory_repo.get_player_items_by_name.return_value = [row_a, row_b]

        with pytest.raises(InvalidItemTypeError, match="Ambiguous item"):
            await service.sell_item(mock_db, player_id=1, item_name="AmbiguousItem")

    @pytest.mark.asyncio
    async def test_sell_item_uses_full_value_no_tax(
        self, service, mock_db, mock_player_repo, mock_inventory_repo, mock_shop_repo
    ):
        """sell_item credits full base value (no sell tax)."""
        player = _make_player(credits=0)
        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        inventory_item = _make_inventory_item(quantity=1, item_type="primary_weapon")
        mock_inventory_repo.get_player_items_by_name.return_value = [inventory_item]
        mock_shop_repo.get_shop_item_by_name.return_value = None

        service._get_item_base_price = AsyncMock(return_value=1000)

        result = await service.sell_item(mock_db, player_id=1, item_name="Gun", quantity=1)

        assert result["unit_sell_price"] == 1000  # full value, no tax

    @pytest.mark.asyncio
    async def test_sell_item_uses_player_tier_as_target_shop(
        self, service, mock_db, mock_player_repo, mock_inventory_repo, mock_shop_repo
    ):
        """target_shop_tier in result always matches player.tier (A.42c — no param accepted).

        Verifies that a Gold-tier player's item lands in the Gold shop, not Bronze default.
        """
        player = _make_player(credits=100, tier="Gold")
        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        inventory_item = _make_inventory_item(quantity=1, item_type="module")
        mock_inventory_repo.get_player_items_by_name.return_value = [inventory_item]
        mock_shop_repo.get_shop_item_by_name.return_value = None

        service._get_item_base_price = AsyncMock(return_value=200)

        result = await service.sell_item(mock_db, player_id=1, item_name="Shield Module")

        assert result["target_shop_tier"] == "Gold"  # player.tier, not "Bronze" default (A.42c)


# ===========================================================================
# Tests: refresh_shop
# ===========================================================================


class TestRefreshShop:
    """Tests for ShopService.refresh_shop."""

    @pytest.mark.asyncio
    async def test_raises_for_invalid_tier(self, service, mock_db):
        """ValueError raised for an unrecognised tier."""
        with pytest.raises(ValueError, match="Invalid tier"):
            await service.refresh_shop(mock_db, guild_id=999, tier="Diamond")

    @pytest.mark.asyncio
    async def test_raises_for_tech_level_zero(self, service, mock_db):
        """ValueError raised when forced tech level is 0."""
        with pytest.raises(ValueError, match="Tech level must be between 1 and 10"):
            await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=0)

    @pytest.mark.asyncio
    async def test_raises_for_tech_level_eleven(self, service, mock_db):
        """ValueError raised when forced tech level is 11 (above new ceiling of 10)."""
        with pytest.raises(ValueError, match="Tech level must be between 1 and 10"):
            await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=11)

    @pytest.mark.asyncio
    async def test_returns_refresh_details(self, service, mock_db, mock_config_repo, mock_shop_repo):
        """Refresh returns a dict with guild_id, tier, tech_level and item counts."""
        config = _make_config()
        mock_config_repo.get_by_guild_id.return_value = config

        # Mock internal helpers to return deterministic items
        service._get_random_item_by_tech_level = AsyncMock(return_value="TestItem")
        service._get_item_base_price = AsyncMock(return_value=300)
        mock_shop_repo.create_or_update = AsyncMock(return_value=_make_shop_item())

        result = await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=5)

        assert result["guild_id"] == 999
        assert result["tier"] == "Bronze"
        assert result["tech_level"] == 5
        assert "items_generated" in result
        assert "refresh_time" in result

    @pytest.mark.asyncio
    async def test_bronze_refresh_prefers_lower_tech_level_band(self, service, mock_db, mock_config_repo, mock_shop_repo):
        """Bronze refreshes should bias toward the lower tech-level band."""
        config = _make_config()
        mock_config_repo.get_by_guild_id.return_value = config
        service._get_random_item_by_tech_level = AsyncMock(return_value=None)
        service._get_item_base_price = AsyncMock(return_value=100)
        mock_shop_repo.clear_shop_tier = AsyncMock()
        mock_shop_repo.create_or_update = AsyncMock(return_value=_make_shop_item())

        with patch("services.shop_service.random.randint", side_effect=lambda a, b: b):
            result = await service.refresh_shop(mock_db, guild_id=999, tier="Bronze")

        assert result["tech_level"] == 3

    @pytest.mark.asyncio
    async def test_ship_selection_respects_requested_tech_level(self, service, mock_db):
        """Ship selection should ignore ships whose price-derived TL does not match the requested shop TL."""
        ship = _make_ship_static(name="HighTechShip", value=1_000_000)
        service._static_cache = {"ship": [ship], "weapon": [], "secondary": [], "turret": [], "module": []}

        result = await service._get_random_item_by_tech_level(mock_db, "ship", 1)

        assert result is None

    @pytest.mark.asyncio
    async def test_rows_carry_item_tech_level_not_batch(self, service, mock_db, mock_config_repo, mock_shop_repo):
        """Shop rows store the ITEM's drawn tech level; the batch TL stays in refresh_details only.

        Draws may land at TL-1/TL-2 of the batch level, and the listing renders
        T{tech_level} per item — storing the batch TL mislabeled those items.
        """
        config = _make_config()
        mock_config_repo.get_by_guild_id.return_value = config

        # Unique name per type — refresh dedupes drawn names, so a constant
        # name would produce a single row for whichever type iterates first.
        service._get_random_item_by_tech_level = AsyncMock(
            side_effect=lambda db, item_type, tl, **kwargs: f"{item_type}-item"
        )
        service._get_item_base_price = AsyncMock(return_value=300)
        service._select_item_tech_level = MagicMock(return_value=3)  # drawn two below batch
        # For modules, row TL is resolved via _get_item_tech_level (catalog TL after step-down).
        # Mock it to return 3 so the assertion below works for all non-ship types.
        service._get_item_tech_level = AsyncMock(return_value=3)
        mock_shop_repo.create_or_update = AsyncMock(return_value=_make_shop_item())

        result = await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=5)

        assert result["tech_level"] == 5  # batch TL preserved for the announcement
        non_ship_rows = [
            call.args[1]
            for call in mock_shop_repo.create_or_update.call_args_list
            if call.args[1]["item_type"] != "ship"
        ]
        assert non_ship_rows, "Test setup: expected at least one non-ship row"
        assert all(row["tech_level"] == 3 for row in non_ship_rows)

        # Ship rows derive TL from credit value (300 → TL1 under locked thresholds)
        ship_rows = [
            call.args[1]
            for call in mock_shop_repo.create_or_update.call_args_list
            if call.args[1]["item_type"] == "ship"
        ]
        for row in ship_rows:
            assert row["tech_level"] == 1

    @pytest.mark.asyncio
    async def test_raises_guild_not_configured_when_none_exists(
        self, service, mock_db, mock_config_repo, mock_shop_repo
    ):
        """If no config, GuildNotConfiguredError is raised (no auto-create)."""
        from services.config_service import GuildNotConfiguredError

        mock_config_repo.get_by_guild_id.return_value = None

        with pytest.raises(GuildNotConfiguredError) as exc_info:
            await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=1)

        assert exc_info.value.guild_id == 999
        mock_config_repo.create_default_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_clears_existing_shop_items_before_refresh(self, service, mock_db, mock_config_repo, mock_shop_repo):
        """clear_shop_tier is always called before generating new items."""
        config = _make_config()
        mock_config_repo.get_by_guild_id.return_value = config

        service._get_random_item_by_tech_level = AsyncMock(return_value=None)

        await service.refresh_shop(mock_db, guild_id=999, tier="Silver", force_tech_level=3)

        mock_shop_repo.clear_shop_tier.assert_awaited_once_with(mock_db, 999, "Silver")

    @pytest.mark.asyncio
    async def test_skips_item_when_no_item_found_at_tech_level(
        self, service, mock_db, mock_config_repo, mock_shop_repo
    ):
        """Items where _get_random_item_by_tech_level returns None are skipped."""
        config = _make_config()
        mock_config_repo.get_by_guild_id.return_value = config

        service._get_random_item_by_tech_level = AsyncMock(return_value=None)

        result = await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=2)

        assert result["items_generated"] == 0
        mock_shop_repo.create_or_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_refresh_shop_produces_real_asset_names(self, service, mock_db, mock_config_repo, mock_shop_repo):
        """Shop refresh populates items using real game asset names from the data layer.

        The service delegates item selection to _get_random_item_by_tech_level.
        This test verifies that when the helper returns a real game-data item name
        (e.g. "Micro Gun MK I" from import_data/primary_weapon/), that name
        propagates through to the shop_repo.create_or_update call, confirming
        the refresh pipeline produces real asset names rather than placeholders.
        """
        # "Micro Gun MK I" is a real primary weapon defined in import_data/primary_weapon/
        # "128MJ Railgun" is another real primary weapon from import_data/primary_weapon/
        real_item_names = ["Micro Gun MK I", "128MJ Railgun"]
        call_count = 0

        async def _fake_get_random(db, item_type, tech_level, **kwargs):
            nonlocal call_count
            name = real_item_names[call_count % len(real_item_names)]
            call_count += 1
            return name

        config = _make_config()
        mock_config_repo.get_by_guild_id.return_value = config
        service._get_random_item_by_tech_level = _fake_get_random
        service._get_item_base_price = AsyncMock(return_value=500)
        created_items = []

        item_types_written = []

        async def _fake_create_or_update(db, item_data):
            shop_item = _make_shop_item(item_name=item_data["item_name"])
            created_items.append(item_data["item_name"])
            item_types_written.append(item_data["item_type"])
            return shop_item

        mock_shop_repo.create_or_update = _fake_create_or_update

        result = await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=1)

        # Shop was refreshed and items were generated
        assert result["items_generated"] > 0
        # Every generated item name is a real game asset name (not a placeholder)
        for name in created_items:
            assert name in real_item_names, f"Unexpected item name in shop: {name!r}"

        # DEF-A42-002 / A.36 regression guard: item_type written to guild_shops must ALWAYS
        # be a concrete type — never a generic alias ('weapon', 'turret').
        _VALID_CONCRETE_TYPES = {"ship", "primary_weapon", "secondary_weapon", "turret_weapon", "module"}
        for t in item_types_written:
            assert t in _VALID_CONCRETE_TYPES, f"generic alias '{t}' written to guild_shops.item_type — A.36 regression"
        assert "weapon" not in item_types_written, "generic alias 'weapon' must NOT be written to guild_shops"
        assert "turret" not in item_types_written, "generic alias 'turret' must NOT be written to guild_shops"


# ===========================================================================
# Tests: refresh_shop returns 'items' key (Task 0002 Sub-task B fix)
# ===========================================================================


class TestRefreshShopReturnsItemsList:
    """Task 0002 Sub-task B: refresh_shop result dict must include 'items' key.

    Root cause of the empty-store announcement bug: the executor called
    `tier_results[t].get("items") or []` but refresh_shop never set that key,
    so the announcement always received an empty list.

    Fix: refresh_shop now includes 'items': generated_items in its return dict.
    """

    @pytest.mark.asyncio
    async def test_refresh_shop_result_contains_items_key(self, service, mock_db, mock_config_repo, mock_shop_repo):
        """refresh_shop return dict must have 'items' key pointing to generated items.

        # Uses existing service/mock_db/mock_config_repo/mock_shop_repo fixtures.
        """
        config = _make_config()
        mock_config_repo.get_by_guild_id.return_value = config
        service._get_random_item_by_tech_level = AsyncMock(return_value="LaserCannon")
        service._get_item_base_price = AsyncMock(return_value=500)

        generated_item = _make_shop_item(item_name="LaserCannon")
        mock_shop_repo.create_or_update = AsyncMock(return_value=generated_item)

        result = await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=1)

        # The fix: result dict must contain 'items' key
        assert "items" in result, (
            "refresh_shop result must contain 'items' key — this is the Sub-task B fix for "
            "the empty-store announcement bug."
        )
        # The items list must not be None
        assert result["items"] is not None, "result['items'] must not be None"
        # items_generated must match the length of the items list
        assert result["items_generated"] == len(result["items"]), (
            f"items_generated={result['items_generated']} must equal len(items)={len(result['items'])}"
        )

    @pytest.mark.asyncio
    async def test_refresh_shop_empty_result_has_empty_items_list(
        self, service, mock_db, mock_config_repo, mock_shop_repo
    ):
        """When no items are generated, 'items' key is an empty list (not missing/None)."""
        config = _make_config()
        mock_config_repo.get_by_guild_id.return_value = config
        # Return None from item selection → no items generated
        service._get_random_item_by_tech_level = AsyncMock(return_value=None)

        result = await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=9)

        assert "items" in result, "refresh_shop result must contain 'items' key even when 0 items generated"
        assert result["items"] == [], f"Expected empty list when no items generated, got: {result['items']!r}"
        assert result["items_generated"] == 0


# ===========================================================================
# Tests: _can_access_tier (synchronous helper)
# ===========================================================================


class TestCanAccessTier:
    """Tests for ShopService._can_access_tier."""

    def test_bronze_can_access_bronze(self):
        svc = ShopService.__new__(ShopService)
        assert svc._can_access_tier("Bronze", "Bronze") is True

    def test_silver_cannot_access_bronze(self):
        """Strict same-tier policy: Silver cannot buy from Bronze shop."""
        svc = ShopService.__new__(ShopService)
        assert svc._can_access_tier("Silver", "Bronze") is False

    def test_bronze_cannot_access_silver(self):
        svc = ShopService.__new__(ShopService)
        assert svc._can_access_tier("Bronze", "Silver") is False

    def test_platinum_can_only_access_platinum(self):
        """Strict same-tier policy: Platinum can only access Platinum shop."""
        svc = ShopService.__new__(ShopService)
        assert svc._can_access_tier("Platinum", "Platinum") is True
        assert svc._can_access_tier("Platinum", "Gold") is False
        assert svc._can_access_tier("Platinum", "Silver") is False
        assert svc._can_access_tier("Platinum", "Bronze") is False

    def test_unknown_tier_treated_as_bronze_level(self):
        """Unknown tier defaults to level 1 (Bronze equivalent)."""
        svc = ShopService.__new__(ShopService)
        # Unknown tier defaults to level 1 (Bronze equivalent)
        assert svc._can_access_tier("Unknown", "Bronze") is True
        assert svc._can_access_tier("Unknown", "Silver") is False


# ===========================================================================
# Tests: _select_item_tech_level (synchronous helper)
# ===========================================================================


class TestSelectItemTechLevel:
    """Tests for ShopService._select_item_tech_level."""

    def test_returns_same_level_when_rand_within_same_prob(self):
        """With rand=0.0 (< same_level_prob of 0.7), same tech level is returned."""
        svc = ShopService.__new__(ShopService)
        probs = {"same_level": 0.7, "one_lower": 0.2, "two_lower": 0.1}

        with patch("services.shop_service.random.random", return_value=0.0):
            result = svc._select_item_tech_level(5, probs)

        assert result == 5

    def test_returns_one_lower_when_rand_in_second_band(self):
        """With rand between same_level_prob and same+one_lower, one lower is returned."""
        svc = ShopService.__new__(ShopService)
        probs = {"same_level": 0.7, "one_lower": 0.2, "two_lower": 0.1}

        with patch("services.shop_service.random.random", return_value=0.75):
            result = svc._select_item_tech_level(5, probs)

        assert result == 4

    def test_returns_two_lower_when_rand_above_both_probs(self):
        """With rand > same+one_lower, two lower is returned."""
        svc = ShopService.__new__(ShopService)
        probs = {"same_level": 0.7, "one_lower": 0.2, "two_lower": 0.1}

        with patch("services.shop_service.random.random", return_value=0.95):
            result = svc._select_item_tech_level(5, probs)

        assert result == 3

    def test_clamps_to_minimum_tech_level_one(self):
        """Tech level is clamped to 1 even when calculation goes below."""
        svc = ShopService.__new__(ShopService)
        probs = {"same_level": 0.0, "one_lower": 0.0, "two_lower": 1.0}

        with patch("services.shop_service.random.random", return_value=0.99):
            result = svc._select_item_tech_level(1, probs)

        assert result >= 1


# ===========================================================================
# Tests: _check_and_refresh_shop
# ===========================================================================


class TestCheckAndRefreshShop:
    """Tests for ShopService._check_and_refresh_shop."""

    @pytest.mark.asyncio
    async def test_triggers_refresh_when_shop_empty(self, service, mock_db, mock_shop_repo):
        """refresh_shop is called when shop has no items."""
        mock_shop_repo.get_shop_items.return_value = []
        service.refresh_shop = AsyncMock()

        await service._check_and_refresh_shop(mock_db, guild_id=999, tier="Bronze")

        service.refresh_shop.assert_awaited_once_with(mock_db, 999, "Bronze")

    @pytest.mark.asyncio
    async def test_does_not_refresh_when_items_fresh(self, service, mock_db, mock_shop_repo):
        """refresh_shop is NOT called when items are not due for refresh."""
        items = [_make_shop_item()]
        items[0].is_refresh_due.return_value = False
        mock_shop_repo.get_shop_items.return_value = items
        service.refresh_shop = AsyncMock()

        await service._check_and_refresh_shop(mock_db, guild_id=999, tier="Bronze")

        service.refresh_shop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_triggers_refresh_when_item_due(self, service, mock_db, mock_shop_repo):
        """refresh_shop is called when at least one item is due for refresh."""
        items = [_make_shop_item()]
        items[0].is_refresh_due.return_value = True
        mock_shop_repo.get_shop_items.return_value = items
        service.refresh_shop = AsyncMock()

        await service._check_and_refresh_shop(mock_db, guild_id=999, tier="Silver")

        service.refresh_shop.assert_awaited_once_with(mock_db, 999, "Silver")

    @pytest.mark.asyncio
    async def test_reraises_exception(self, service, mock_db, mock_shop_repo):
        """Exceptions from shop_repo.get_shop_items propagate."""
        mock_shop_repo.get_shop_items.side_effect = RuntimeError("connection lost")

        with pytest.raises(RuntimeError, match="connection lost"):
            await service._check_and_refresh_shop(mock_db, guild_id=999, tier="Bronze")


# ===========================================================================
# Tests: _get_random_item_by_tech_level
# ===========================================================================


def _make_db_item(name: str, tech_level: int = 3, shop_spawn_rate: float | None = None) -> MagicMock:
    """Create a mock DB item with name and optional tech_level / shop_spawn_rate."""
    item = MagicMock()
    item.name = name
    item.tech_level = tech_level
    item.shop_spawn_rate = shop_spawn_rate
    return item


class TestGetRandomItemByTechLevel:
    """Tests for ShopService._get_random_item_by_tech_level (DB-backed)."""

    @pytest.mark.asyncio
    async def test_returns_weapon_name_from_repo(self, service, mock_db, mock_primary_weapon_repo):
        """Returns the name of a weapon fetched from the primary_weapon_repo."""
        weapon = _make_db_item("Micro Gun MK I", tech_level=3)
        mock_primary_weapon_repo.list_all = AsyncMock(return_value=[weapon])

        result = await service._get_random_item_by_tech_level(mock_db, "weapon", 3)

        assert result == "Micro Gun MK I"

    @pytest.mark.asyncio
    async def test_returns_module_name_from_repo(self, service, mock_db, mock_module_repo):
        """Returns the name of a non-junk module fetched from the module_repo (combat bucket)."""
        module = _make_db_item("Shield Generator", tech_level=2)
        module.type = "ShieldModule"  # combat bucket — always drawable
        mock_module_repo.list_all = AsyncMock(return_value=[module])

        # Force combat bucket by passing prob=1.0
        result = await service._get_random_item_by_tech_level(mock_db, "module", 2, combat_module_prob=1.0)

        assert result == "Shield Generator"

    @pytest.mark.asyncio
    async def test_returns_turret_name_from_repo(self, service, mock_db, mock_turret_weapon_repo):
        """Returns the name of a turret fetched from the turret_weapon_repo."""
        turret = _make_db_item("Dual Turret", tech_level=5)
        mock_turret_weapon_repo.list_all = AsyncMock(return_value=[turret])

        result = await service._get_random_item_by_tech_level(mock_db, "turret", 5)

        assert result == "Dual Turret"

    @pytest.mark.asyncio
    async def test_returns_ship_name_weighted_by_spawn_rate(self, service, mock_db, mock_ship_repo):
        """Returns a ship name; ships are selected by shop_spawn_rate weight."""
        ship = _make_db_item("Viper", shop_spawn_rate=2.5)
        mock_ship_repo.list_all = AsyncMock(return_value=[ship])

        result = await service._get_random_item_by_tech_level(mock_db, "ship", 1)

        assert result == "Viper"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_items_at_tech_level(self, service, mock_db, mock_primary_weapon_repo):
        """Returns None when the DB contains no items at the requested tech level."""
        # Return a weapon at tech_level=1, but we request tech_level=9 → no match
        weapon = _make_db_item("Micro Gun MK I", tech_level=1)
        mock_primary_weapon_repo.list_all = AsyncMock(return_value=[weapon])

        result = await service._get_random_item_by_tech_level(mock_db, "weapon", 9)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_repo_empty(self, service, mock_db, mock_ship_repo):
        """Returns None when the ship repository is empty."""
        mock_ship_repo.list_all = AsyncMock(return_value=[])

        result = await service._get_random_item_by_tech_level(mock_db, "ship", 1)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_type(self, service, mock_db):
        """Returns None when item type is not recognised."""
        result = await service._get_random_item_by_tech_level(mock_db, "banana", 1)

        assert result is None


# ===========================================================================
# Tests: _get_item_base_price
# ===========================================================================


class TestGetItemBasePrice:
    """Tests for ShopService._get_item_base_price (DB-backed)."""

    @pytest.mark.asyncio
    async def test_returns_item_value_from_repo(self, service, mock_db, mock_primary_weapon_repo):
        """Returns the value field from the item found in a repository."""
        weapon = MagicMock()
        weapon.value = 750
        mock_primary_weapon_repo.get_by_name = AsyncMock(return_value=weapon)
        # Ensure ship_repo returns None so search continues to primary_weapon_repo
        service.ship_repo.get_by_name = AsyncMock(return_value=None)

        price = await service._get_item_base_price(mock_db, "Micro Gun MK I")

        assert price == 750

    @pytest.mark.asyncio
    async def test_returns_ship_value_when_found_first(self, service, mock_db, mock_ship_repo):
        """Returns the ship's value when the item is found in ship_repo first."""
        ship = MagicMock()
        ship.value = 5000
        mock_ship_repo.get_by_name = AsyncMock(return_value=ship)

        price = await service._get_item_base_price(mock_db, "Viper")

        assert price == 5000

    @pytest.mark.asyncio
    async def test_returns_zero_when_item_not_found(self, service, mock_db):
        """Returns 0 when the item is not found in any repository."""
        # All repos return None (default fixture setup). T1 added commodity_repo as
        # the last fallback — it must also miss for the 0 result.
        service.commodity_repo.get_by_name = AsyncMock(return_value=None)
        price = await service._get_item_base_price(mock_db, "NonExistentItem")

        assert price == 0


# ===========================================================================
# Tests: _add_item_to_shop
# ===========================================================================


class TestAddItemToShop:
    """Tests for ShopService._add_item_to_shop."""

    @pytest.mark.asyncio
    async def test_updates_existing_item_quantity(self, service, mock_db, mock_shop_repo):
        """When item already exists in shop, its quantity is updated."""
        existing = _make_shop_item(item_id=20, quantity=3)
        mock_shop_repo.get_shop_item_by_name.return_value = existing

        await service._add_item_to_shop(
            mock_db, guild_id=999, tier="Bronze", item_type="weapon", item_name="Gun", quantity=2, base_price=300
        )

        mock_shop_repo.update_quantity.assert_awaited_once_with(mock_db, 20, 5, commit=True)  # 3 + 2
        mock_shop_repo.create_or_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_new_shop_item(self, service, mock_db, mock_shop_repo):
        """When no existing shop item, create_or_update is called with full item data."""
        mock_shop_repo.get_shop_item_by_name.return_value = None

        await service._add_item_to_shop(
            mock_db, guild_id=999, tier="Silver", item_type="module", item_name="Shield", quantity=1, base_price=500
        )

        mock_shop_repo.create_or_update.assert_awaited_once()
        call_kwargs = mock_shop_repo.create_or_update.call_args[0]
        item_data = call_kwargs[1]
        assert item_data["item_name"] == "Shield"
        assert item_data["tier"] == "Silver"
        assert item_data["quantity"] == 1
        assert item_data["price"] == 500

    @pytest.mark.asyncio
    async def test_reraises_exception(self, service, mock_db, mock_shop_repo):
        """Exceptions from shop_repo propagate out of _add_item_to_shop."""
        mock_shop_repo.get_shop_item_by_name.side_effect = RuntimeError("lookup failed")

        with pytest.raises(RuntimeError, match="lookup failed"):
            await service._add_item_to_shop(
                mock_db, guild_id=999, tier="Bronze", item_type="weapon", item_name="Gun", quantity=1, base_price=100
            )

    @pytest.mark.asyncio
    async def test_new_item_carries_catalog_tech_level(self, service, mock_db, mock_shop_repo, mock_module_repo):
        """A freshly-sold item's shop row stores the item's REAL catalog tech level, not 1."""
        mock_shop_repo.get_shop_item_by_name.return_value = None
        mock_module_repo.get_by_name.return_value = MagicMock(tech_level=4)

        await service._add_item_to_shop(
            mock_db, guild_id=999, tier="Silver", item_type="module", item_name="Shield", quantity=1, base_price=500
        )

        item_data = mock_shop_repo.create_or_update.call_args[0][1]
        assert item_data["tech_level"] == 4

    @pytest.mark.asyncio
    async def test_new_ship_tech_level_derived_from_value(self, service, mock_db, mock_shop_repo):
        """Sold-back ships (no catalog tech_level) derive TL from credit value."""
        from services.game_maths import ship_tech_level_for_value

        mock_shop_repo.get_shop_item_by_name.return_value = None
        ship_value = 150_000  # between thresholds → TL3 with locked SHIP_PRICE_THRESHOLDS

        await service._add_item_to_shop(
            mock_db,
            guild_id=999,
            tier="Gold",
            item_type="ship",
            item_name="Hatsuyuki",
            quantity=1,
            base_price=ship_value,
        )

        item_data = mock_shop_repo.create_or_update.call_args[0][1]
        assert item_data["tech_level"] == ship_tech_level_for_value(ship_value)
        assert item_data["tech_level"] == 3

    @pytest.mark.asyncio
    async def test_unknown_item_falls_back_to_tl1(self, service, mock_db, mock_shop_repo):
        """Items missing from the catalog fall back to TL1 (all repos return None)."""
        mock_shop_repo.get_shop_item_by_name.return_value = None

        await service._add_item_to_shop(
            mock_db, guild_id=999, tier="Bronze", item_type="module", item_name="Ghost", quantity=1, base_price=100
        )

        item_data = mock_shop_repo.create_or_update.call_args[0][1]
        assert item_data["tech_level"] == 1

    @pytest.mark.asyncio
    async def test_existing_item_skips_tech_level_lookup(self, service, mock_db, mock_shop_repo, mock_module_repo):
        """Quantity-bump path never touches the catalog repos."""
        mock_shop_repo.get_shop_item_by_name.return_value = _make_shop_item(item_id=20, quantity=3)

        await service._add_item_to_shop(
            mock_db, guild_id=999, tier="Bronze", item_type="module", item_name="Shield", quantity=1, base_price=100
        )

        mock_module_repo.get_by_name.assert_not_awaited()
        mock_shop_repo.create_or_update.assert_not_awaited()


# ===========================================================================
# Tests: purchase_ship
# ===========================================================================


def _setup_purchase_ship_mocks(
    mock_db,
    mock_player_repo,
    mock_player_ship_repo,
    new_player_ship_id: int = 99,
) -> None:
    """Wire up the db.flush side-effect and player_ship_repo.get_by_id so
    activate_ship (called by purchase_ship) can find the newly-created PlayerShip.

    The service does db.add(new_player_ship) → db.flush() to get the new ship's
    auto-generated PK.  In unit tests the mock session does not auto-assign PKs,
    so we simulate it via flush's side_effect.

    activate_ship then calls player_ship_repo.get_by_id(target_ship_id) to
    validate the ship; we return the new ship object so it passes ownership checks.
    """
    # Capture the PlayerShip that was added so we can set its id and return it from
    # player_ship_repo.get_by_id.  We rely on the fact that db.add is called exactly
    # once for the new PlayerShip (inside purchase_ship).
    captured = {}

    def _add_side_effect(obj):
        captured["new_ship"] = obj

    mock_db.add = MagicMock(side_effect=_add_side_effect)

    async def _flush_side_effect():
        if "new_ship" in captured:
            captured["new_ship"].id = new_player_ship_id

    mock_db.flush = AsyncMock(side_effect=_flush_side_effect)

    # player_ship_repo.get_by_id is called by activate_ship to validate ownership.
    async def _get_by_id(db, ship_id):
        if ship_id == new_player_ship_id and "new_ship" in captured:
            return captured["new_ship"]
        return None

    mock_player_ship_repo.get_by_id = AsyncMock(side_effect=_get_by_id)

    # activate_ship calls player_repo.update_active_ship at the end.
    mock_player_repo.update_active_ship = AsyncMock()

    # set_active_ship must return an activated ship (not None) so activate_ship
    # doesn't blow up on attribute access.
    mock_player_ship_repo.set_active_ship = AsyncMock(
        return_value=_make_player_ship(ship_id=new_player_ship_id, is_active=True)
    )


class TestPurchaseShip:
    """Tests for ShopService.purchase_ship."""

    @pytest.mark.asyncio
    async def test_ship_buy_credits_deducted_new_ship_created_old_ship_kept(
        self, service, mock_db, mock_player_repo, mock_shop_repo, mock_ship_repo, mock_player_ship_repo
    ):
        """Buy ship: credits deducted, new ship added, old ship stays as inactive PlayerShip."""
        player = _make_player(guild_id=999, credits=10_000)
        shop_item = _make_shop_item(item_type="ship", item_name="Hammerhead", price=5000)
        new_ship_static = _make_ship_static(
            name="Hammerhead", value=5000, max_primaries=2, max_modules=2, max_turrets=1
        )
        old_player_ship = _make_player_ship(ship_id=50, ship_name="Crow")
        old_ship_static = _make_ship_static(name="Crow", value=2000)

        mock_player_repo.get_by_id.return_value = player
        # purchase_ship re-fetches under lock; use same player object so credits is a real int
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_shop_repo.get_by_id.return_value = shop_item
        mock_ship_repo.get_by_name.side_effect = lambda db, name: (
            new_ship_static if name == "Hammerhead" else old_ship_static
        )
        mock_player_ship_repo.get_active_ship.return_value = old_player_ship
        mock_player_ship_repo.get_player_ships = AsyncMock(return_value=[old_player_ship])
        _setup_purchase_ship_mocks(mock_db, mock_player_repo, mock_player_ship_repo)

        result = await service.purchase_ship(mock_db, player_id=1, shop_item_id=10)

        assert result["item_name"] == "Hammerhead"
        assert result["remaining_credits"] == 5000  # 10000 - 5000
        assert result["trade_in_value"] == 0
        assert result["net_cost"] == 5000
        # Old ship must NOT be deleted — it stays as an inactive PlayerShip
        mock_db.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ship_buy_item_transfer_within_slot_limits(
        self, service, mock_db, mock_player_repo, mock_shop_repo, mock_ship_repo, mock_player_ship_repo
    ):
        """Items from old ship transferred to new ship within slot limits."""
        player = _make_player(credits=10_000)
        shop_item = _make_shop_item(item_type="ship", item_name="Raider", price=3000)
        new_ship_static = _make_ship_static(name="Raider", max_primaries=2, max_modules=1, max_turrets=0)
        # Old ship has 2 weapons, 1 module, 0 turrets (all fit in new ship)
        old_player_ship = _make_player_ship(
            ship_name="Crow",
            weapons=["Gun A", "Gun B"],
            modules=["Shield"],
            turrets=[],
        )
        old_ship_static = _make_ship_static(name="Crow", value=1000)

        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_shop_repo.get_by_id.return_value = shop_item
        mock_ship_repo.get_by_name.side_effect = lambda db, name: (
            new_ship_static if name == "Raider" else old_ship_static
        )
        mock_player_ship_repo.get_active_ship.return_value = old_player_ship
        mock_player_ship_repo.get_player_ships = AsyncMock(return_value=[old_player_ship])
        _setup_purchase_ship_mocks(mock_db, mock_player_repo, mock_player_ship_repo)

        result = await service.purchase_ship(mock_db, player_id=1, shop_item_id=10)

        # All 3 items (2 weapons + 1 module) transferred, none unequipped
        assert result["items_transferred"] == 3
        assert result["items_unequipped_to_inventory"] == 0

    @pytest.mark.asyncio
    async def test_ship_buy_overflow_items_unequipped_to_inventory(
        self,
        service,
        mock_db,
        mock_player_repo,
        mock_shop_repo,
        mock_ship_repo,
        mock_player_ship_repo,
        mock_inventory_repo,
        mock_item_repo,
    ):
        """Items that don't fit on new ship are unequipped to inventory with concrete types (A.36 fix)."""
        player = _make_player(credits=10_000)
        shop_item = _make_shop_item(item_type="ship", item_name="Sparrow", price=2000)
        # New ship has only 1 weapon slot, 0 modules, 0 turrets
        new_ship_static = _make_ship_static(name="Sparrow", max_primaries=1, max_modules=0, max_turrets=0)
        # Old ship has 3 weapons (2 overflow), 2 modules (2 overflow)
        old_player_ship = _make_player_ship(
            ship_name="Hammerhead",
            weapons=["Gun A", "Gun B", "Gun C"],
            modules=["Shield X", "Shield Y"],
            turrets=[],
        )
        old_ship_static = _make_ship_static(name="Hammerhead", value=1500)

        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_shop_repo.get_by_id.return_value = shop_item
        mock_ship_repo.get_by_name.side_effect = lambda db, name: (
            new_ship_static if name == "Sparrow" else old_ship_static
        )
        mock_player_ship_repo.get_active_ship.return_value = old_player_ship
        mock_player_ship_repo.get_player_ships = AsyncMock(return_value=[old_player_ship])
        _setup_purchase_ship_mocks(mock_db, mock_player_repo, mock_player_ship_repo)

        # Set up item_repo to return items with concrete STI discriminators
        def _make_item_mock(type_str: str):
            m = MagicMock()
            m.type = type_str
            return m

        async def _get_by_name_any_type(db, name):
            if name in ("Gun A", "Gun B", "Gun C"):
                return _make_item_mock("PrimaryWeapon")
            if name in ("Shield X", "Shield Y"):
                return _make_item_mock("ShieldModule")
            return None

        mock_item_repo.get_by_name_any_type.side_effect = _get_by_name_any_type

        result = await service.purchase_ship(mock_db, player_id=1, shop_item_id=10)

        # 1 weapon fits, 2 weapons + 2 modules overflow = 4 items unequipped
        assert result["items_transferred"] == 1
        assert result["items_unequipped_to_inventory"] == 4
        # inventory_repo.add_item called 4 times for the overflow items
        assert mock_inventory_repo.add_item.await_count == 4
        # A.36 regression guard: verify CONCRETE types used
        add_calls = mock_inventory_repo.add_item.call_args_list
        item_types_used = {call.args[2] for call in add_calls if len(call.args) >= 3}
        assert "weapon" not in item_types_used, "generic alias 'weapon' must not be written"
        assert "turret" not in item_types_used, "generic alias 'turret' must not be written"

    @pytest.mark.asyncio
    async def test_ship_buy_insufficient_credits_no_trade_in_raises(
        self, service, mock_db, mock_player_repo, mock_shop_repo, mock_ship_repo, mock_player_ship_repo
    ):
        """ValueError raised when player cannot afford new ship."""
        player = _make_player(credits=1000)
        shop_item = _make_shop_item(item_type="ship", item_name="Hammerhead", price=5000)
        new_ship_static = _make_ship_static(name="Hammerhead", value=5000)
        old_player_ship = _make_player_ship(ship_name="Crow")
        old_ship_static = _make_ship_static(name="Crow", value=3000)

        mock_player_repo.get_by_id.return_value = player
        # Credit check is done under lock; provide player so the ValueError is raised correctly
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_shop_repo.get_by_id.return_value = shop_item
        mock_ship_repo.get_by_name.side_effect = lambda db, name: (
            new_ship_static if name == "Hammerhead" else old_ship_static
        )
        mock_player_ship_repo.get_active_ship.return_value = old_player_ship

        with pytest.raises(ValueError, match="Insufficient credits"):
            await service.purchase_ship(mock_db, player_id=1, shop_item_id=10)

    @pytest.mark.asyncio
    async def test_buy_non_ship_via_purchase_ship_raises(self, service, mock_db, mock_player_repo, mock_shop_repo):
        """ValueError raised when shop item is not a ship."""
        player = _make_player(credits=10_000)
        shop_item = _make_shop_item(item_type="weapon", item_name="Pulse Laser", price=200)

        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_shop_repo.get_by_id.return_value = shop_item

        with pytest.raises(ValueError, match="not a ship"):
            await service.purchase_ship(mock_db, player_id=1, shop_item_id=10)

    @pytest.mark.asyncio
    async def test_ship_buy_first_ship_no_active_ship(
        self, service, mock_db, mock_player_repo, mock_shop_repo, mock_ship_repo, mock_player_ship_repo
    ):
        """First ship purchase succeeds when player has no active ship."""
        player = _make_player(credits=10_000)
        shop_item = _make_shop_item(item_type="ship", item_name="Crow", price=3000)
        new_ship_static = _make_ship_static(name="Crow", value=3000, max_primaries=2, max_modules=1, max_turrets=0)

        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_shop_repo.get_by_id.return_value = shop_item
        mock_ship_repo.get_by_name.return_value = new_ship_static
        # No active ship
        mock_player_ship_repo.get_active_ship.return_value = None
        mock_player_ship_repo.get_player_ships = AsyncMock(return_value=[])
        _setup_purchase_ship_mocks(mock_db, mock_player_repo, mock_player_ship_repo)

        result = await service.purchase_ship(mock_db, player_id=1, shop_item_id=10)

        assert result["item_name"] == "Crow"
        assert result["remaining_credits"] == 7000  # 10000 - 3000
        assert result["trade_in_value"] == 0
        assert result["items_transferred"] == 0

    @pytest.mark.asyncio
    async def test_ship_buy_shop_stock_decremented(
        self, service, mock_db, mock_player_repo, mock_shop_repo, mock_ship_repo, mock_player_ship_repo
    ):
        """Shop stock is decremented after ship purchase."""
        player = _make_player(credits=10_000)
        shop_item = _make_shop_item(item_type="ship", item_name="Hammerhead", price=5000, quantity=3)
        new_ship_static = _make_ship_static(name="Hammerhead", value=5000)

        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_shop_repo.get_by_id.return_value = shop_item
        mock_ship_repo.get_by_name.return_value = new_ship_static
        mock_player_ship_repo.get_active_ship.return_value = None
        mock_player_ship_repo.get_player_ships = AsyncMock(return_value=[])
        _setup_purchase_ship_mocks(mock_db, mock_player_repo, mock_player_ship_repo)

        await service.purchase_ship(mock_db, player_id=1, shop_item_id=10)

        # quantity was 3, should be 2 after purchase
        mock_shop_repo.update_quantity.assert_awaited_once_with(mock_db, 10, 2, commit=False)

    @pytest.mark.asyncio
    async def test_ship_buy_shop_item_removed_when_last_stock(
        self, service, mock_db, mock_player_repo, mock_shop_repo, mock_ship_repo, mock_player_ship_repo
    ):
        """Shop item removed when last stock is purchased."""
        player = _make_player(credits=10_000)
        shop_item = _make_shop_item(item_type="ship", item_name="Hammerhead", price=5000, quantity=1)
        new_ship_static = _make_ship_static(name="Hammerhead", value=5000)

        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_shop_repo.get_by_id.return_value = shop_item
        mock_ship_repo.get_by_name.return_value = new_ship_static
        mock_player_ship_repo.get_active_ship.return_value = None
        mock_player_ship_repo.get_player_ships = AsyncMock(return_value=[])
        _setup_purchase_ship_mocks(mock_db, mock_player_repo, mock_player_ship_repo)

        await service.purchase_ship(mock_db, player_id=1, shop_item_id=10)

        mock_shop_repo.remove.assert_awaited_once_with(mock_db, shop_item, commit=False)


# ===========================================================================
# Tests: sell_ship
# ===========================================================================


class TestSellShip:
    """Tests for ShopService.sell_ship."""

    @pytest.mark.asyncio
    async def test_sell_inactive_ship_success_credits_and_removal(
        self, service, mock_db, mock_player_repo, mock_player_ship_repo, mock_ship_repo, mock_shop_repo
    ):
        """Selling an inactive ship credits the player its full value and removes the ship."""
        player = _make_player(guild_id=999, credits=1000)
        player_ship = _make_player_ship(ship_id=200, player_id=1, ship_name="Crow", is_active=False)
        ship_static = _make_ship_static(name="Crow", value=3000)

        mock_player_repo.get_by_id.return_value = player
        # sell_ship re-fetches under lock; use same player object so credits is a real int
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_player_ship_repo.get_by_id.return_value = player_ship
        mock_ship_repo.get_by_name.return_value = ship_static
        mock_shop_repo.get_shop_item_by_name.return_value = None

        result = await service.sell_ship(mock_db, player_id=1, ship_id=200)

        assert result["player_id"] == 1
        assert result["item_name"] == "Crow"
        assert result["sell_value"] == 3000
        assert result["new_credits"] == 4000  # 1000 + 3000
        mock_db.delete.assert_awaited_once_with(player_ship)
        mock_player_repo.update_credits.assert_awaited_once_with(mock_db, 1, 4000, commit=False)

    @pytest.mark.asyncio
    async def test_sell_active_ship_raises_value_error(self, service, mock_db, mock_player_repo, mock_player_ship_repo):
        """Attempting to sell the active ship raises ValueError."""
        player = _make_player(guild_id=999, credits=1000)
        active_ship = _make_player_ship(ship_id=100, player_id=1, is_active=True)

        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_player_ship_repo.get_by_id.return_value = active_ship

        with pytest.raises(ValueError, match="Cannot sell active ship"):
            await service.sell_ship(mock_db, player_id=1, ship_id=100)

    @pytest.mark.asyncio
    async def test_sell_ship_clear_equipment_unequips_items_to_inventory(
        self,
        service,
        mock_db,
        mock_player_repo,
        mock_player_ship_repo,
        mock_ship_repo,
        mock_shop_repo,
        mock_inventory_repo,
        mock_item_repo,
    ):
        """With clear_equipment=True, all equipped items are moved to inventory with concrete types (A.36 fix)."""
        player = _make_player(guild_id=999, credits=500)
        player_ship = _make_player_ship(
            ship_id=201,
            player_id=1,
            ship_name="Hammerhead",
            is_active=False,
            weapons=["Gun A", "Gun B"],
            modules=["Shield"],
            turrets=[],
        )
        ship_static = _make_ship_static(name="Hammerhead", value=5000)

        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_player_ship_repo.get_by_id.return_value = player_ship
        mock_ship_repo.get_by_name.return_value = ship_static
        mock_shop_repo.get_shop_item_by_name.return_value = None

        # Set up item_repo to return items with concrete STI discriminators
        def _make_item_mock(type_str: str):
            m = MagicMock()
            m.type = type_str
            return m

        async def _get_by_name_any_type(db, name):
            if name in ("Gun A", "Gun B"):
                return _make_item_mock("PrimaryWeapon")
            if name == "Shield":
                return _make_item_mock("ShieldModule")
            return None

        mock_item_repo.get_by_name_any_type.side_effect = _get_by_name_any_type

        result = await service.sell_ship(mock_db, player_id=1, ship_id=201, clear_equipment=True)

        # 2 weapons + 1 module = 3 items unequipped
        assert result["items_unequipped_to_inventory"] == 3
        assert mock_inventory_repo.add_item.await_count == 3
        # A.36 regression guard: verify CONCRETE types used
        add_calls = mock_inventory_repo.add_item.call_args_list
        item_types_used = {call.args[2] for call in add_calls if len(call.args) >= 3}
        assert "weapon" not in item_types_used, "generic alias 'weapon' must not be written"
        assert "turret" not in item_types_used, "generic alias 'turret' must not be written"

    @pytest.mark.asyncio
    async def test_sell_ship_not_belonging_to_player_raises(
        self, service, mock_db, mock_player_repo, mock_player_ship_repo
    ):
        """ValueError raised when ship does not belong to the player."""
        player = _make_player(player_id=1, guild_id=999, credits=1000)
        other_players_ship = _make_player_ship(
            ship_id=300,
            player_id=99,
            is_active=False,  # owned by player 99
        )

        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_player_ship_repo.get_by_id.return_value = other_players_ship

        with pytest.raises(ValueError, match="does not belong to player"):
            await service.sell_ship(mock_db, player_id=1, ship_id=300)

    @pytest.mark.asyncio
    async def test_sell_ship_not_found_raises(self, service, mock_db, mock_player_repo, mock_player_ship_repo):
        """ValueError raised when ship ID does not exist."""
        player = _make_player(guild_id=999, credits=1000)

        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_player_ship_repo.get_by_id.return_value = None  # ship not found

        with pytest.raises(ValueError, match="Ship 999 not found"):
            await service.sell_ship(mock_db, player_id=1, ship_id=999)

    @pytest.mark.asyncio
    async def test_sell_ship_no_equipment_success(
        self, service, mock_db, mock_player_repo, mock_player_ship_repo, mock_ship_repo, mock_shop_repo
    ):
        """Selling a ship with no equipment succeeds without inventory changes."""
        player = _make_player(guild_id=999, credits=200)
        player_ship = _make_player_ship(
            ship_id=202, player_id=1, ship_name="Sparrow", is_active=False, weapons=[], modules=[], turrets=[]
        )
        ship_static = _make_ship_static(name="Sparrow", value=1500)

        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_player_ship_repo.get_by_id.return_value = player_ship
        mock_ship_repo.get_by_name.return_value = ship_static
        mock_shop_repo.get_shop_item_by_name.return_value = None

        result = await service.sell_ship(mock_db, player_id=1, ship_id=202)

        assert result["items_unequipped_to_inventory"] == 0
        assert result["sell_value"] == 1500
        assert result["new_credits"] == 1700  # 200 + 1500
        mock_db.delete.assert_awaited_once_with(player_ship)

    @pytest.mark.asyncio
    async def test_sell_ship_credits_are_full_value_no_tax(
        self, service, mock_db, mock_player_repo, mock_player_ship_repo, mock_ship_repo, mock_shop_repo
    ):
        """Credits received equal the ship's full base value — no sell tax applied."""
        player = _make_player(guild_id=999, credits=0)
        player_ship = _make_player_ship(ship_id=203, player_id=1, ship_name="Viper", is_active=False)
        ship_static = _make_ship_static(name="Viper", value=8000)

        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_player_ship_repo.get_by_id.return_value = player_ship
        mock_ship_repo.get_by_name.return_value = ship_static
        mock_shop_repo.get_shop_item_by_name.return_value = None

        result = await service.sell_ship(mock_db, player_id=1, ship_id=203)

        # Full value: 8000, no 0.8 factor applied
        assert result["sell_value"] == 8000
        assert result["new_credits"] == 8000  # 0 + 8000

    @pytest.mark.asyncio
    async def test_sell_ship_added_to_target_shop_tier(
        self, service, mock_db, mock_player_repo, mock_player_ship_repo, mock_ship_repo, mock_shop_repo
    ):
        """Sold ship is added to the specified shop tier."""
        player = _make_player(guild_id=999, credits=100)
        player_ship = _make_player_ship(ship_id=204, player_id=1, ship_name="Crow", is_active=False)
        ship_static = _make_ship_static(name="Crow", value=2000)

        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_player_ship_repo.get_by_id.return_value = player_ship
        mock_ship_repo.get_by_name.return_value = ship_static
        mock_shop_repo.get_shop_item_by_name.return_value = None

        result = await service.sell_ship(mock_db, player_id=1, ship_id=204, target_tier="Silver")

        assert result["target_shop_tier"] == "Silver"
        # Verify the shop repo was asked to create the item
        mock_shop_repo.create_or_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sell_ship_invalid_target_tier_raises(self, service, mock_db, mock_player_repo):
        """ValueError raised for an invalid target tier."""
        mock_player_repo.get_by_id.return_value = _make_player()

        with pytest.raises(ValueError, match="Invalid target tier"):
            await service.sell_ship(mock_db, player_id=1, ship_id=1, target_tier="Diamond")


# ===========================================================================
# Tests: CI-5 — secondary weapon shop exclusion of deferred subtypes
# ===========================================================================


def _make_secondary_weapon(
    name: str,
    tech_level: int = 3,
    subtype: str = "rocket",
    value: int = 200,
) -> MagicMock:
    """Create a mock SecondaryWeapon DB object with the inner extra_atts nesting used in real data.

    Seed JSON stores subtype inside extra_atts → extra_atts → subtype.
    The secondary_weapon_repository stores ``extra_atts`` key in the outer JSON blob,
    so on the ORM object: item.extra_atts = {"extra_atts": {"subtype": ..., ...}}.
    """
    item = MagicMock()
    item.name = name
    item.tech_level = tech_level
    item.value = value
    item.extra_atts = {"extra_atts": {"subtype": subtype}}
    return item


class TestShopExcludesDeferredSecondarySubtypes:
    """CI-5: shop candidate pool must exclude emp-bomb / mine / sentry-gun secondaries.

    Two-part assertion per spec:
    1. _get_random_item_by_tech_level never returns a deferred-subtype weapon.
    2. refresh_shop generates at least one secondary when canonical subtypes are available.
    """

    @pytest.mark.asyncio
    async def test_deferred_subtypes_excluded_from_candidate_pool(self, service, mock_db, mock_secondary_weapon_repo):
        """_get_random_item_by_tech_level skips emp-bomb / mine / sentry-gun weapons."""
        # Populate repo with one deferred weapon per deferred subtype plus a canonical one
        deferred = [
            _make_secondary_weapon("EMP GL I", tech_level=3, subtype="emp-bomb"),
            _make_secondary_weapon("AMR Saber", tech_level=3, subtype="mine"),
            _make_secondary_weapon("Berger SG-100", tech_level=3, subtype="sentry-gun"),
        ]
        canonical = _make_secondary_weapon("Jet Rocket", tech_level=3, subtype="rocket")
        mock_secondary_weapon_repo.list_all = AsyncMock(return_value=[*deferred, canonical])

        # Run 20 draws — all must come back as the canonical weapon or None (never a deferred one)
        deferred_names = {"EMP GL I", "AMR Saber", "Berger SG-100"}
        for _ in range(20):
            result = await service._get_random_item_by_tech_level(mock_db, "secondary_weapon", 3)
            assert result not in deferred_names, (
                f"_get_random_item_by_tech_level returned a deferred-subtype weapon: {result!r}"
            )

    @pytest.mark.asyncio
    async def test_canonical_secondary_still_included(self, service, mock_db, mock_secondary_weapon_repo):
        """At least one canonical secondary weapon must be selectable when available."""
        canonical_weapons = [
            _make_secondary_weapon("Jet Rocket", tech_level=3, subtype="rocket"),
            _make_secondary_weapon("Mamba EMP", tech_level=3, subtype="missile"),
            _make_secondary_weapon("AMR Extinctor", tech_level=3, subtype="nuke"),
        ]
        mock_secondary_weapon_repo.list_all = AsyncMock(return_value=canonical_weapons)

        result = await service._get_random_item_by_tech_level(mock_db, "secondary_weapon", 3)
        canonical_names = {w.name for w in canonical_weapons}
        assert result in canonical_names, f"Expected a canonical secondary weapon name, got: {result!r}"

    @pytest.mark.asyncio
    async def test_returns_none_when_only_deferred_subtypes_at_tech_level(
        self, service, mock_db, mock_secondary_weapon_repo
    ):
        """Returns None when every secondary at the requested tech level is deferred."""
        deferred = [
            _make_secondary_weapon("EMP GL I", tech_level=4, subtype="emp-bomb"),
            _make_secondary_weapon("EMP GL II", tech_level=4, subtype="emp-bomb"),
            _make_secondary_weapon("Ksann'k", tech_level=4, subtype="mine"),
        ]
        mock_secondary_weapon_repo.list_all = AsyncMock(return_value=deferred)

        result = await service._get_random_item_by_tech_level(mock_db, "secondary_weapon", 4)
        assert result is None, f"Expected None when only deferred subtypes exist at tech level, got: {result!r}"

    @pytest.mark.asyncio
    async def test_refresh_shop_includes_secondary_weapon_type(
        self, service, mock_db, mock_config_repo, mock_shop_repo, mock_secondary_weapon_repo
    ):
        """refresh_shop now generates secondary_weapon items (CI-5 gate).

        Verifies that secondary_weapon enters the candidate pool and that
        the item_type written to guild_shops is the concrete "secondary_weapon"
        string (never a generic alias).
        """
        config = _make_config()
        mock_config_repo.get_by_guild_id.return_value = config

        # Canonical secondary at tech level 1 — should be selected
        canonical = _make_secondary_weapon("Jet Rocket", tech_level=1, subtype="rocket", value=150)
        mock_secondary_weapon_repo.list_all = AsyncMock(return_value=[canonical])

        # Capture item_type values written to the shop
        item_types_written: list[str] = []

        async def _fake_create_or_update(db, item_data):
            item_types_written.append(item_data["item_type"])
            return _make_shop_item(item_name=item_data["item_name"], item_type=item_data["item_type"])

        mock_shop_repo.create_or_update = _fake_create_or_update

        # Override _get_random_item_by_tech_level to return the canonical secondary for
        # the secondary_weapon type and None for everything else (keeps test focused)
        async def _fake_get_random(db, item_type, tech_level, **kwargs):
            if item_type == "secondary_weapon":
                return "Jet Rocket"
            return None

        service._get_random_item_by_tech_level = _fake_get_random
        service._get_item_base_price = AsyncMock(return_value=150)

        result = await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=1)

        assert "secondary_weapon" in item_types_written, (
            "refresh_shop must write secondary_weapon items to the shop (CI-5)"
        )
        assert "weapon" not in item_types_written, (
            "generic alias 'weapon' must NOT be written to guild_shops.item_type (A.36 regression)"
        )
        assert result["items_generated"] >= 1


# ===========================================================================
# Tests: CI-11 — secondary_weapon gets its own shop-count range
# ===========================================================================


def _make_config_with_separate_ranges(
    primary_count_range: dict | None = None,
    secondary_count_range: dict | None = None,
    primary_qty_range: dict | None = None,
    secondary_qty_range: dict | None = None,
) -> MagicMock:
    """Build a mock GuildConfig whose get_count_range/get_quantity_range dispatch correctly.

    Simulates the real GuildConfig.get_count_range() which now looks up
    'weapon' for primary_weapon and 'secondary_weapon' for secondary_weapon.
    """
    primary_count = primary_count_range or {"min": 3, "max": 5}
    secondary_count = secondary_count_range or {"min": 3, "max": 5}
    primary_qty = primary_qty_range or {"min": 2, "max": 4}
    secondary_qty = secondary_qty_range or {"min": 2, "max": 4}

    count_map = {
        "ship": {"min": 3, "max": 5},
        "weapon": primary_count,
        "secondary_weapon": secondary_count,
        "module": {"min": 3, "max": 5},
        "turret": {"min": 3, "max": 5},
    }
    qty_map = {
        "ship": {"min": 1, "max": 1},
        "weapon": primary_qty,
        "secondary_weapon": secondary_qty,
        "module": {"min": 2, "max": 4},
        "turret": {"min": 2, "max": 4},
    }

    config = MagicMock()
    config.tech_level_probabilities = {"same_level": 1.0, "one_lower": 0.0, "two_lower": 0.0}
    config.get_count_range = MagicMock(side_effect=lambda k: count_map.get(k, {"min": 1, "max": 1}))
    config.get_quantity_range = MagicMock(side_effect=lambda k: qty_map.get(k, {"min": 1, "max": 1}))
    return config


class TestCI11SecondaryWeaponOwnRange:
    """CI-11: secondary_weapon draws from its own count/quantity range, not from 'weapon'."""

    def test_concrete_to_config_key_maps_secondary_to_own_key(self):
        """_CONCRETE_TO_CONFIG_KEY now maps secondary_weapon → 'secondary_weapon', not 'weapon'."""
        from services.shop_service import _CONCRETE_TO_CONFIG_KEY

        assert _CONCRETE_TO_CONFIG_KEY["secondary_weapon"] == "secondary_weapon", (
            "secondary_weapon must map to its own config key 'secondary_weapon' (CI-11)"
        )
        assert _CONCRETE_TO_CONFIG_KEY["primary_weapon"] == "weapon", (
            "primary_weapon must still map to 'weapon' config key"
        )

    def test_concrete_to_config_key_all_expected_entries(self):
        """_CONCRETE_TO_CONFIG_KEY contains all 5 expected concrete types."""
        from services.shop_service import _CONCRETE_TO_CONFIG_KEY

        assert set(_CONCRETE_TO_CONFIG_KEY.keys()) == {
            "ship",
            "primary_weapon",
            "secondary_weapon",
            "module",
            "turret_weapon",
        }

    @pytest.mark.asyncio
    async def test_secondary_draws_from_own_range_not_weapon_range(
        self, service, mock_db, mock_config_repo, mock_shop_repo
    ):
        """With secondary_weapon_count_range={min:1,max:1}, exactly 1 secondary is generated.

        If secondary piggybacked on the weapon range ({min:3,max:5}), we'd expect 3-5
        secondaries; with its own range {min:1,max:1} we expect exactly 1.
        Meanwhile primary_weapon range is {min:3,max:5} → still generates primaries.
        """
        config = _make_config_with_separate_ranges(
            primary_count_range={"min": 3, "max": 5},
            secondary_count_range={"min": 1, "max": 1},  # hard limit: exactly 1 secondary
            primary_qty_range={"min": 1, "max": 1},
            secondary_qty_range={"min": 1, "max": 1},
        )
        mock_config_repo.get_by_guild_id.return_value = config

        # Track per-type counts
        type_counts: dict[str, int] = {}

        async def _fake_create_or_update(db, item_data):
            t = item_data["item_type"]
            type_counts[t] = type_counts.get(t, 0) + 1
            return _make_shop_item(item_name=item_data["item_name"], item_type=t)

        mock_shop_repo.create_or_update = _fake_create_or_update

        # Return distinct names per call so dedup doesn't collapse them.
        # Each call gets a unique index suffix; the type prefix ensures
        # secondary draws never use the weapon namespace.
        _call_counters: dict[str, int] = {}

        async def _fake_get_random(db, item_type, tech_level, **kwargs):
            _call_counters[item_type] = _call_counters.get(item_type, 0) + 1
            return f"Fake_{item_type}_{_call_counters[item_type]}"

        service._get_random_item_by_tech_level = _fake_get_random
        service._get_item_base_price = AsyncMock(return_value=100)

        await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=1)

        secondary_count = type_counts.get("secondary_weapon", 0)
        assert secondary_count == 1, (
            f"With secondary_weapon_count_range={{min:1,max:1}}, expected exactly 1 secondary, "
            f"got {secondary_count}. This indicates secondary still draws from weapon range."
        )

        primary_count = type_counts.get("primary_weapon", 0)
        assert primary_count >= 3, (
            f"primary_weapon range {{min:3,max:5}} should still yield ≥3 primaries, got {primary_count}"
        )

    @pytest.mark.asyncio
    async def test_primary_and_secondary_draw_from_independent_ranges(
        self, service, mock_db, mock_config_repo, mock_shop_repo
    ):
        """Primary and secondary draw from completely independent ranges (CI-11 core invariant).

        Set secondary to {min:0,max:0} (no secondaries) while primary is {min:2,max:2}.
        Expect: exactly 2 primaries, 0 secondaries.
        """
        # Note: GuildConfig.get_count_range fallback for unknown keys is {"min":1,"max":1},
        # so we use min:2 for primary and a custom config with 0 for secondary.
        count_map = {
            "ship": {"min": 0, "max": 0},
            "weapon": {"min": 2, "max": 2},
            "secondary_weapon": {"min": 0, "max": 0},
            "module": {"min": 0, "max": 0},
            "turret": {"min": 0, "max": 0},
        }
        qty_map: dict[str, dict] = {}

        config = MagicMock()
        config.tech_level_probabilities = {"same_level": 1.0, "one_lower": 0.0, "two_lower": 0.0}
        config.get_count_range = MagicMock(side_effect=lambda k: count_map.get(k, {"min": 0, "max": 0}))
        config.get_quantity_range = MagicMock(side_effect=lambda k: qty_map.get(k, {"min": 1, "max": 1}))
        mock_config_repo.get_by_guild_id.return_value = config

        type_counts: dict[str, int] = {}

        async def _fake_create_or_update(db, item_data):
            t = item_data["item_type"]
            type_counts[t] = type_counts.get(t, 0) + 1
            return _make_shop_item(item_name=item_data["item_name"], item_type=t)

        mock_shop_repo.create_or_update = _fake_create_or_update

        # Return distinct names per call so dedup doesn't collapse draws to one item.
        # Each call gets a unique index suffix; the type prefix ensures
        # secondary draws never bleed into the weapon namespace.
        _call_counters2: dict[str, int] = {}

        async def _fake_get_random(db, item_type, tech_level, **kwargs):
            _call_counters2[item_type] = _call_counters2.get(item_type, 0) + 1
            return f"Fake_{item_type}_{_call_counters2[item_type]}"

        service._get_random_item_by_tech_level = _fake_get_random
        service._get_item_base_price = AsyncMock(return_value=100)

        await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=1)

        assert type_counts.get("secondary_weapon", 0) == 0, (
            "secondary_weapon_count_range {min:0,max:0} must produce 0 secondaries"
        )
        assert type_counts.get("primary_weapon", 0) == 2, (
            f"weapon count range {{min:2,max:2}} must produce exactly 2 primaries, "
            f"got {type_counts.get('primary_weapon', 0)}"
        )

    @pytest.mark.asyncio
    async def test_deferred_subtype_regression_with_secondary_count_greater_than_zero(
        self, service, mock_db, mock_config_repo, mock_shop_repo, mock_secondary_weapon_repo
    ):
        """Many-iteration refresh with secondary count>0 → zero emp-bomb/mine/sentry-gun in shop.

        Even when secondaries are actively being generated (count=2), the deferred
        subtype filter must hold and never allow emp-bomb, mine, or sentry-gun through.
        Uses a config where only secondaries are generated (count=2), all other types=0,
        so the ship_repo empty list error is avoided.
        """
        # Canonical secondary + all three deferred subtypes
        canonical = _make_secondary_weapon("Jet Rocket", tech_level=1, subtype="rocket")
        deferred = [
            _make_secondary_weapon("EMP GL I", tech_level=1, subtype="emp-bomb"),
            _make_secondary_weapon("Ksann'k", tech_level=1, subtype="mine"),
            _make_secondary_weapon("Berger SG-100", tech_level=1, subtype="sentry-gun"),
        ]
        mock_secondary_weapon_repo.list_all = AsyncMock(return_value=[canonical, *deferred])

        # Config: only secondary_weapon generates items (all other counts = 0)
        count_map = {
            "ship": {"min": 0, "max": 0},
            "weapon": {"min": 0, "max": 0},
            "secondary_weapon": {"min": 2, "max": 2},
            "module": {"min": 0, "max": 0},
            "turret": {"min": 0, "max": 0},
        }
        config = MagicMock()
        config.tech_level_probabilities = {"same_level": 1.0, "one_lower": 0.0, "two_lower": 0.0}
        config.get_count_range = MagicMock(side_effect=lambda k: count_map.get(k, {"min": 0, "max": 0}))
        config.get_quantity_range = MagicMock(return_value={"min": 1, "max": 1})
        mock_config_repo.get_by_guild_id.return_value = config

        names_generated: list[str] = []
        deferred_names = {"EMP GL I", "Ksann'k", "Berger SG-100"}

        async def _fake_create_or_update(db, item_data):
            if item_data["item_type"] == "secondary_weapon":
                names_generated.append(item_data["item_name"])
            return _make_shop_item(item_name=item_data["item_name"], item_type=item_data["item_type"])

        mock_shop_repo.create_or_update = _fake_create_or_update
        service._get_item_base_price = AsyncMock(return_value=100)

        # Run 10 iterations; each should generate 2 secondaries, all must be canonical
        for _ in range(10):
            names_generated.clear()
            # Reset the static cache so real DB call goes through secondary_weapon_repo
            service._static_cache = None
            await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=1)

            for name in names_generated:
                assert name not in deferred_names, (
                    f"Deferred subtype weapon '{name}' must never appear in shop (deferred-subtype regression)"
                )


# ===========================================================================
# Tests: secondary-weapon shop quantity scalers (consumable rounds)
# ===========================================================================


def _make_config_secondary_only(qty_min: int = 3, qty_max: int = 3) -> MagicMock:
    """Config that generates exactly one secondary per refresh and nothing else.

    Fixed quantity range (default min=max=3) makes the scaled quantity
    deterministic: rolled 3 × scaler.
    """
    count_map = {
        "ship": {"min": 0, "max": 0},
        "weapon": {"min": 0, "max": 0},
        "secondary_weapon": {"min": 1, "max": 1},
        "module": {"min": 0, "max": 0},
        "turret": {"min": 0, "max": 0},
    }
    config = MagicMock()
    config.tech_level_probabilities = {"same_level": 1.0, "one_lower": 0.0, "two_lower": 0.0}
    config.get_count_range = MagicMock(side_effect=lambda k: count_map.get(k, {"min": 0, "max": 0}))
    config.get_quantity_range = MagicMock(return_value={"min": qty_min, "max": qty_max})
    return config


class TestSecondaryQuantityScalers:
    """Secondaries are consumable rounds: refresh_shop multiplies the rolled
    quantity by SHOP_SECONDARY_QTY_SCALER_HEAVY for nuke/shock-blast and
    SHOP_SECONDARY_QTY_SCALER_STANDARD for everything else."""

    async def _refresh_and_capture_quantity(
        self, service, mock_db, mock_config_repo, mock_shop_repo, mock_secondary_weapon_repo, weapon
    ) -> int:
        """Run a secondary-only refresh drawing `weapon` and return the written quantity."""
        mock_config_repo.get_by_guild_id.return_value = _make_config_secondary_only(qty_min=3, qty_max=3)
        mock_secondary_weapon_repo.get_by_name = AsyncMock(return_value=weapon)

        quantities: list[int] = []

        async def _fake_create_or_update(db, item_data):
            quantities.append(item_data["quantity"])
            return _make_shop_item(item_name=item_data["item_name"], item_type=item_data["item_type"])

        mock_shop_repo.create_or_update = _fake_create_or_update

        async def _fake_get_random(db, item_type, tech_level, **kwargs):
            return weapon.name if item_type == "secondary_weapon" else None

        service._get_random_item_by_tech_level = _fake_get_random
        service._get_item_base_price = AsyncMock(return_value=100)

        await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=1)

        assert len(quantities) == 1, f"Expected exactly 1 secondary written, got {len(quantities)}"
        return quantities[0]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("subtype", ["nuke", "shock-blast", "cluster-missile"])
    async def test_heavy_subtypes_use_heavy_scaler(
        self, service, mock_db, mock_config_repo, mock_shop_repo, mock_secondary_weapon_repo, subtype
    ):
        """nuke/shock-blast/cluster-missile: rolled 3 × HEAVY scaler (5) = 15."""
        weapon = _make_secondary_weapon("AMR Extinctor", tech_level=1, subtype=subtype)
        qty = await self._refresh_and_capture_quantity(
            service, mock_db, mock_config_repo, mock_shop_repo, mock_secondary_weapon_repo, weapon
        )
        expected = 3 * GameConstants.SHOP_SECONDARY_QTY_SCALER_HEAVY
        assert qty == expected, (
            f"{subtype}: expected 3×{GameConstants.SHOP_SECONDARY_QTY_SCALER_HEAVY}={expected}, got {qty}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("subtype", ["missile", "rocket"])
    async def test_standard_subtypes_use_standard_scaler(
        self, service, mock_db, mock_config_repo, mock_shop_repo, mock_secondary_weapon_repo, subtype
    ):
        """missile/rocket: rolled 3 × STANDARD scaler (10) = 30."""
        weapon = _make_secondary_weapon("Jet Rocket", tech_level=1, subtype=subtype)
        qty = await self._refresh_and_capture_quantity(
            service, mock_db, mock_config_repo, mock_shop_repo, mock_secondary_weapon_repo, weapon
        )
        expected = 3 * GameConstants.SHOP_SECONDARY_QTY_SCALER_STANDARD
        assert qty == expected, (
            f"{subtype}: expected 3×{GameConstants.SHOP_SECONDARY_QTY_SCALER_STANDARD}={expected}, got {qty}"
        )

    @pytest.mark.asyncio
    async def test_missing_subtype_falls_back_to_standard_scaler(
        self, service, mock_db, mock_config_repo, mock_shop_repo, mock_secondary_weapon_repo
    ):
        """A secondary with no subtype in extra_atts gets the STANDARD scaler."""
        weapon = MagicMock()
        weapon.name = "Mystery Launcher"
        weapon.tech_level = 1
        weapon.value = 100
        weapon.extra_atts = {}
        qty = await self._refresh_and_capture_quantity(
            service, mock_db, mock_config_repo, mock_shop_repo, mock_secondary_weapon_repo, weapon
        )
        assert qty == 3 * GameConstants.SHOP_SECONDARY_QTY_SCALER_STANDARD

    @pytest.mark.asyncio
    async def test_non_secondary_types_are_not_scaled(self, service, mock_db, mock_config_repo, mock_shop_repo):
        """Primary weapons keep the raw rolled quantity (no scaler applied)."""
        count_map = {
            "ship": {"min": 0, "max": 0},
            "weapon": {"min": 1, "max": 1},
            "secondary_weapon": {"min": 0, "max": 0},
            "module": {"min": 0, "max": 0},
            "turret": {"min": 0, "max": 0},
        }
        config = MagicMock()
        config.tech_level_probabilities = {"same_level": 1.0, "one_lower": 0.0, "two_lower": 0.0}
        config.get_count_range = MagicMock(side_effect=lambda k: count_map.get(k, {"min": 0, "max": 0}))
        config.get_quantity_range = MagicMock(return_value={"min": 3, "max": 3})
        mock_config_repo.get_by_guild_id.return_value = config

        quantities: list[int] = []

        async def _fake_create_or_update(db, item_data):
            quantities.append(item_data["quantity"])
            return _make_shop_item(item_name=item_data["item_name"], item_type=item_data["item_type"])

        mock_shop_repo.create_or_update = _fake_create_or_update

        async def _fake_get_random(db, item_type, tech_level, **kwargs):
            return "Micro Gun MK I" if item_type == "primary_weapon" else None

        service._get_random_item_by_tech_level = _fake_get_random
        service._get_item_base_price = AsyncMock(return_value=100)

        await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=1)

        assert quantities == [3], f"primary_weapon quantity must stay at rolled 3 (unscaled), got {quantities}"

    @pytest.mark.asyncio
    async def test_subtype_lookup_uses_static_cache_when_warm(self, service, mock_db, mock_secondary_weapon_repo):
        """_get_secondary_subtype_by_name reads the static cache (no repo call) when preloaded."""
        weapon = _make_secondary_weapon("AMR Extinctor", tech_level=1, subtype="nuke")
        service._static_cache = {"secondary": [weapon]}
        mock_secondary_weapon_repo.get_by_name = AsyncMock(
            side_effect=AssertionError("repo must not be queried when static cache is warm")
        )

        subtype = await service._get_secondary_subtype_by_name(mock_db, "AMR Extinctor")
        assert subtype == "nuke"

        # Name missing from cache → "" without falling back to the repo
        assert await service._get_secondary_subtype_by_name(mock_db, "Nonexistent") == ""

    @pytest.mark.asyncio
    async def test_subtype_lookup_falls_back_to_repo_when_cache_cold(
        self, service, mock_db, mock_secondary_weapon_repo
    ):
        """_get_secondary_subtype_by_name queries the repo when no static cache is loaded."""
        weapon = _make_secondary_weapon("Jet Rocket", tech_level=1, subtype="rocket")
        service._static_cache = None
        mock_secondary_weapon_repo.get_by_name = AsyncMock(return_value=weapon)

        assert await service._get_secondary_subtype_by_name(mock_db, "Jet Rocket") == "rocket"

        # Unknown item → ""
        mock_secondary_weapon_repo.get_by_name = AsyncMock(return_value=None)
        assert await service._get_secondary_subtype_by_name(mock_db, "Nonexistent") == ""


# ===========================================================================
# T1 (PvC loot C-1): commodity economy citizenship in ShopService.
#   - pricing: commodity Item.value resolves (was 0 before this wiring)
#   - selling: commodity is a face-value SINK (never stocked in a GuildShop)
#   - refresh: commodities are NEVER generated into shop stock
# ===========================================================================


class TestCommodityPricing:
    """T1/C-1: _get_item_base_price resolves a commodity's Item.value."""

    @pytest.mark.asyncio
    async def test_commodity_price_resolves_to_item_value_slow_path(self, service, mock_db):
        """Slow path (no preload cache): a commodity's value comes from commodity_repo,
        not 0. The five non-commodity repos miss; commodity_repo provides Item.value."""
        commodity = MagicMock()
        commodity.value = 1234
        service.commodity_repo.get_by_name = AsyncMock(return_value=commodity)
        # All other repos already default to get_by_name -> None.

        price = await service._get_item_base_price(mock_db, "Booze")

        assert price == 1234  # NOT 0 (the pre-T1 behaviour for commodities)

    @pytest.mark.asyncio
    async def test_preload_caches_commodity_prices(self, service, mock_db):
        """preload_static_data adds commodity prices to the price cache so a cached
        lookup resolves a commodity's Item.value (and shop generation is unaffected)."""
        # Empty non-commodity static lists; one commodity with a real value.
        for repo_name in (
            "ship_repo",
            "primary_weapon_repo",
            "secondary_weapon_repo",
            "module_repo",
            "turret_weapon_repo",
        ):
            getattr(service, repo_name).list_all = AsyncMock(return_value=[])
        commodity = MagicMock()
        commodity.name = "Booze"
        commodity.value = 999
        service.commodity_repo.list_all = AsyncMock(return_value=[commodity])

        await service.preload_static_data(mock_db)

        # Cached lookup resolves the commodity price.
        price = await service._get_item_base_price(mock_db, "Booze")
        assert price == 999


class TestSellCommoditySink:
    """T1/C-1/§5.7: selling a commodity pays Item.value × qty × fraction and
    DESTROYS the units — it must never be added to a GuildShop."""

    @pytest.mark.asyncio
    async def test_commodity_sell_pays_face_value_and_does_not_stock_shop(
        self, service, mock_db, mock_player_repo, mock_inventory_repo
    ):
        """Commodity sell: credits = value×qty×fraction; _add_item_to_shop NOT called;
        no shop write occurs (pure sink)."""
        player = _make_player(credits=100, tier="Bronze")
        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        inv = _make_inventory_item(quantity=16, item_type="commodity", item_name="Booze")
        mock_inventory_repo.get_player_items_by_name.return_value = [inv]

        service._get_item_base_price = AsyncMock(return_value=50)
        # Spy on the shop-add path to prove it is never invoked for a commodity.
        service._add_item_to_shop = AsyncMock()

        with patch.object(GameConstants, "LOOT_COMMODITY_SELL_FRACTION", 1.0):
            result = await service.sell_item(mock_db, player_id=1, item_name="Booze", quantity=16)

        # value (50) × qty (16) × fraction (1.0) = 800
        assert result["item_type"] == "commodity"
        assert result["unit_sell_price"] == 50
        assert result["total_sell_value"] == 800
        assert result["new_credits"] == 900  # 100 + 800
        assert result["target_shop_tier"] is None
        assert result["sunk"] is True
        # The units are removed from cargo (destroyed)...
        mock_inventory_repo.remove_item.assert_awaited_once_with(mock_db, 1, "commodity", "Booze", 16, commit=False)
        # ...but NEVER added to any GuildShop.
        service._add_item_to_shop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_commodity_sell_applies_fraction(self, service, mock_db, mock_player_repo, mock_inventory_repo):
        """LOOT_COMMODITY_SELL_FRACTION scales the payout (read from GameConstants)."""
        player = _make_player(credits=0, tier="Bronze")
        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        inv = _make_inventory_item(quantity=10, item_type="commodity", item_name="Ore")
        mock_inventory_repo.get_player_items_by_name.return_value = [inv]
        service._get_item_base_price = AsyncMock(return_value=100)
        service._add_item_to_shop = AsyncMock()

        with patch.object(GameConstants, "LOOT_COMMODITY_SELL_FRACTION", 0.5):
            result = await service.sell_item(mock_db, player_id=1, item_name="Ore", quantity=10)

        # value (100) × 0.5 = 50/unit; × qty 10 = 500
        assert result["unit_sell_price"] == 50
        assert result["total_sell_value"] == 500
        service._add_item_to_shop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_commodity_sell_single_truncation_no_underpay(
        self, service, mock_db, mock_player_repo, mock_inventory_repo
    ):
        """§9 C-2 rounding regression: payout truncates ONCE on the full product.

        value=1, fraction=0.5, qty=10. The correct credit is int(1*0.5*10)=5.
        The old per-unit-first math computed int(1*0.5)=0 per unit, then *10 = 0,
        silently destroying the cargo for zero credits. This test FAILS against the
        old math (credits 0) and passes against the single-truncation fix (credits 5).
        """
        player = _make_player(credits=0, tier="Bronze")
        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        inv = _make_inventory_item(quantity=10, item_type="commodity", item_name="Scrap")
        mock_inventory_repo.get_player_items_by_name.return_value = [inv]
        service._get_item_base_price = AsyncMock(return_value=1)
        service._add_item_to_shop = AsyncMock()

        with patch.object(GameConstants, "LOOT_COMMODITY_SELL_FRACTION", 0.5):
            result = await service.sell_item(mock_db, player_id=1, item_name="Scrap", quantity=10)

        # int(1 * 0.5 * 10) == 5 — NOT int(0.5)*10 == 0.
        assert result["total_sell_value"] == 5, "single-truncation product must credit 5, not 0"
        assert result["new_credits"] == 5  # 0 + 5
        # Per-unit display is derived from the credited total (5 // 10 == 0).
        assert result["unit_sell_price"] == 0
        # The credited balance is what update_credits actually received.
        mock_player_repo.update_credits.assert_awaited_once_with(mock_db, 1, 5, commit=False)
        service._add_item_to_shop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_commodity_sell_value_zero_credits_nothing(
        self, service, mock_db, mock_player_repo, mock_inventory_repo
    ):
        """A value-0 commodity credits 0, destroys the units, raises no error, no shop add."""
        player = _make_player(credits=250, tier="Bronze")
        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        inv = _make_inventory_item(quantity=4, item_type="commodity", item_name="Dust")
        mock_inventory_repo.get_player_items_by_name.return_value = [inv]
        service._get_item_base_price = AsyncMock(return_value=0)
        service._add_item_to_shop = AsyncMock()

        with patch.object(GameConstants, "LOOT_COMMODITY_SELL_FRACTION", 1.0):
            result = await service.sell_item(mock_db, player_id=1, item_name="Dust", quantity=4)

        assert result["total_sell_value"] == 0
        assert result["unit_sell_price"] == 0
        assert result["new_credits"] == 250  # unchanged
        assert result["sunk"] is True
        assert result["target_shop_tier"] is None
        # Units destroyed even though payout is 0.
        mock_inventory_repo.remove_item.assert_awaited_once_with(mock_db, 1, "commodity", "Dust", 4, commit=False)
        # Balance unchanged but the credit write still happens with the same value.
        mock_player_repo.update_credits.assert_awaited_once_with(mock_db, 1, 250, commit=False)
        # Never stocked into any GuildShop.
        service._add_item_to_shop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_commodity_sell_partial_stack(self, service, mock_db, mock_player_repo, mock_inventory_repo):
        """Partial sell (16 in cargo, sell 6): credits int(value*fraction*6); no shop add."""
        player = _make_player(credits=0, tier="Bronze")
        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        inv = _make_inventory_item(quantity=16, item_type="commodity", item_name="Ore")
        mock_inventory_repo.get_player_items_by_name.return_value = [inv]
        service._get_item_base_price = AsyncMock(return_value=30)
        service._add_item_to_shop = AsyncMock()

        with patch.object(GameConstants, "LOOT_COMMODITY_SELL_FRACTION", 0.5):
            result = await service.sell_item(mock_db, player_id=1, item_name="Ore", quantity=6)

        # int(30 * 0.5 * 6) == 90.
        assert result["total_sell_value"] == 90
        assert result["quantity"] == 6
        assert result["new_credits"] == 90  # 0 + 90
        mock_player_repo.update_credits.assert_awaited_once_with(mock_db, 1, 90, commit=False)
        # Only the sold 6 are removed; the remove_item call carries qty=6 (cargo decremented to 10).
        mock_inventory_repo.remove_item.assert_awaited_once_with(mock_db, 1, "commodity", "Ore", 6, commit=False)
        service._add_item_to_shop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_commodity_sell_single_unit(self, service, mock_db, mock_player_repo, mock_inventory_repo):
        """qty=1 single-unit stack sold entirely: correct credit, row removed, no shop add."""
        player = _make_player(credits=10, tier="Bronze")
        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        inv = _make_inventory_item(quantity=1, item_type="commodity", item_name="Gem")
        mock_inventory_repo.get_player_items_by_name.return_value = [inv]
        service._get_item_base_price = AsyncMock(return_value=40)
        service._add_item_to_shop = AsyncMock()

        with patch.object(GameConstants, "LOOT_COMMODITY_SELL_FRACTION", 0.5):
            result = await service.sell_item(mock_db, player_id=1, item_name="Gem", quantity=1)

        # int(40 * 0.5 * 1) == 20.
        assert result["total_sell_value"] == 20
        assert result["unit_sell_price"] == 20  # 20 // 1
        assert result["new_credits"] == 30  # 10 + 20
        mock_player_repo.update_credits.assert_awaited_once_with(mock_db, 1, 30, commit=False)
        mock_inventory_repo.remove_item.assert_awaited_once_with(mock_db, 1, "commodity", "Gem", 1, commit=False)
        service._add_item_to_shop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_commodity_sell_credits_update_awaited_with_resulting_balance(
        self, service, mock_db, mock_player_repo, mock_inventory_repo
    ):
        """update_credits is awaited exactly once with the correct resulting balance.

        Locks the sink's credit write to the player's starting credits PLUS the
        single-truncation payout — not merely that the return dict holds a number.
        """
        player = _make_player(credits=137, tier="Bronze")
        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        inv = _make_inventory_item(quantity=3, item_type="commodity", item_name="Spice")
        mock_inventory_repo.get_player_items_by_name.return_value = [inv]
        service._get_item_base_price = AsyncMock(return_value=25)
        service._add_item_to_shop = AsyncMock()

        with patch.object(GameConstants, "LOOT_COMMODITY_SELL_FRACTION", 0.5):
            result = await service.sell_item(mock_db, player_id=1, item_name="Spice", quantity=3)

        # payout = int(25 * 0.5 * 3) == 37; resulting balance = 137 + 37 == 174.
        assert result["total_sell_value"] == 37
        assert result["new_credits"] == 174
        mock_player_repo.update_credits.assert_awaited_once_with(mock_db, 1, 174, commit=False)
        service._add_item_to_shop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_commodity_sell_uses_per_guild_fraction_override(
        self, service, mock_db, mock_player_repo, mock_inventory_repo, mock_config_repo
    ):
        """T11 fix: a guild whose guild_configs.loot_commodity_sell_fraction override is
        SET pays int(value × override × qty) — the PER-GUILD value — even though the
        GameConstants default is the catalog default (1.0). This proves the per-guild
        column now takes effect at the sell site (regression for the T2-deferred gap
        where the sell site read GameConstants directly and ignored the column)."""
        player = _make_player(guild_id=4242, credits=0, tier="Bronze")
        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        inv = _make_inventory_item(quantity=10, item_type="commodity", item_name="Ore")
        mock_inventory_repo.get_player_items_by_name.return_value = [inv]
        service._get_item_base_price = AsyncMock(return_value=100)
        service._add_item_to_shop = AsyncMock()

        # Per-guild override = 0.5; GameConstants default left at the catalog default 1.0.
        guild_config = _make_config()
        guild_config.loot_commodity_sell_fraction = 0.5
        mock_config_repo.get_by_guild_id.return_value = guild_config

        with patch.object(GameConstants, "LOOT_COMMODITY_SELL_FRACTION", 1.0):
            result = await service.sell_item(mock_db, player_id=1, item_name="Ore", quantity=10)

        # PER-GUILD: int(100 × 0.5 × 10) == 500, NOT the default int(100 × 1.0 × 10) == 1000.
        assert result["total_sell_value"] == 500
        assert result["unit_sell_price"] == 50
        # The seller's own guild config was consulted.
        mock_config_repo.get_by_guild_id.assert_awaited_once_with(mock_db, 4242)
        service._add_item_to_shop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_commodity_sell_null_override_falls_back_to_default(
        self, service, mock_db, mock_player_repo, mock_inventory_repo, mock_config_repo
    ):
        """T11 fix: a guild whose loot_commodity_sell_fraction column is NULL falls back
        to the GameConstants default (here the catalog default 1.0), so the payout is the
        full face value. NULL ⇒ default is the documented per-guild resolution semantics."""
        player = _make_player(guild_id=7, credits=0, tier="Bronze")
        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        inv = _make_inventory_item(quantity=10, item_type="commodity", item_name="Ore")
        mock_inventory_repo.get_player_items_by_name.return_value = [inv]
        service._get_item_base_price = AsyncMock(return_value=100)
        service._add_item_to_shop = AsyncMock()

        # Column NULL on an otherwise-configured guild.
        guild_config = _make_config()
        guild_config.loot_commodity_sell_fraction = None
        mock_config_repo.get_by_guild_id.return_value = guild_config

        with patch.object(GameConstants, "LOOT_COMMODITY_SELL_FRACTION", 1.0):
            result = await service.sell_item(mock_db, player_id=1, item_name="Ore", quantity=10)

        # NULL override ⇒ default 1.0 ⇒ full face value 100 × 10 == 1000.
        assert result["total_sell_value"] == 1000
        assert result["unit_sell_price"] == 100
        service._add_item_to_shop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_weapon_sell_still_stocks_shop_regression(
        self, service, mock_db, mock_player_repo, mock_inventory_repo
    ):
        """Regression: selling a Weapon/Module is UNCHANGED — it still stocks the
        player's tier GuildShop via _add_item_to_shop (no sink branch)."""
        player = _make_player(credits=0, tier="Bronze")
        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        inv = _make_inventory_item(quantity=1, item_type="primary_weapon", item_name="Gun")
        mock_inventory_repo.get_player_items_by_name.return_value = [inv]
        service._get_item_base_price = AsyncMock(return_value=300)
        service._add_item_to_shop = AsyncMock()

        result = await service.sell_item(mock_db, player_id=1, item_name="Gun", quantity=1)

        assert result["item_type"] == "primary_weapon"
        assert result["target_shop_tier"] == "Bronze"
        assert "sunk" not in result
        # The weapon IS stocked into the shop.
        service._add_item_to_shop.assert_awaited_once()
        shop_call = service._add_item_to_shop.await_args
        assert shop_call.args[3] == "primary_weapon"  # concrete_type
        assert shop_call.args[4] == "Gun"  # item_name


class TestRefreshExcludesCommodities:
    """T1/C-1 regression: refresh_shop must NOT generate commodities into shop
    stock even though 'commodity' is now in CURRENTLY_ENABLED_TYPES — the shop
    gate is _CONCRETE_TO_CONFIG_KEY (which has no commodity entry)."""

    @pytest.mark.asyncio
    async def test_refresh_never_stocks_commodity(self, service, mock_db, mock_config_repo, mock_shop_repo):
        """No generated shop row has item_type='commodity', and the commodity type is
        never passed to the item-selection helper."""
        from services.shop_service import _CONCRETE_TO_CONFIG_KEY

        # Sanity: 'commodity' is enabled but has no shop config key → excluded from generation.
        assert "commodity" in GameConstants.CURRENTLY_ENABLED_TYPES
        assert "commodity" not in _CONCRETE_TO_CONFIG_KEY

        config = _make_config()
        mock_config_repo.get_by_guild_id.return_value = config

        seen_types: list[str] = []

        async def _fake_get_random(db, item_type, tl, **kwargs):
            seen_types.append(item_type)
            return f"{item_type}-item"

        service._get_random_item_by_tech_level = _fake_get_random
        service._get_item_base_price = AsyncMock(return_value=300)
        mock_shop_repo.create_or_update = AsyncMock(return_value=_make_shop_item())

        await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=5)

        # The selection helper is never asked for a commodity...
        assert "commodity" not in seen_types
        # ...and no generated shop row is a commodity.
        generated_types = {call.args[1]["item_type"] for call in mock_shop_repo.create_or_update.call_args_list}
        assert "commodity" not in generated_types


# ===========================================================================
# Tests: Module bucket draw (PART 5 coverage)
# ===========================================================================


def _make_module(name: str, module_type: str, tech_level: int) -> MagicMock:
    """Create a mock Module with .type, .tech_level, and .name set."""
    m = MagicMock()
    m.name = name
    m.type = module_type
    m.tech_level = tech_level
    return m


class TestModuleBucketDraw:
    """Tests for the new module-draw logic in _get_random_item_by_tech_level.

    Uses the _static_cache injection pattern so no DB calls are made.
    """

    @pytest.mark.asyncio
    async def test_junk_never_drawn(self, service, mock_db):
        """JUNK module types are never returned regardless of bucket selection."""
        from services.game_constants import GameConstants

        # Build a pool with one junk module + one combat module at the same TL.
        junk_type = next(iter(GameConstants.SHOP_JUNK_MODULE_TYPES))
        combat_type = next(iter(GameConstants.SHOP_COMBAT_MODULE_TYPES))
        junk_module = _make_module("Junk1", junk_type, 5)
        combat_module = _make_module("Combat1", combat_type, 5)
        service._static_cache = {"module": [junk_module, combat_module]}

        results: set[str] = set()
        for prob in (0.0, 1.0):
            for _ in range(50):
                result = await service._get_random_item_by_tech_level(mock_db, "module", 5, combat_module_prob=prob)
                if result is not None:
                    results.add(result)

        assert "Junk1" not in results, f"Junk module appeared in results: {results}"

    @pytest.mark.asyncio
    async def test_combat_filler_split_75_25(self, service, mock_db):
        """At prob=0.75 the combat bucket is drawn ~75% of the time (N=2000 draws)."""
        import random

        from services.game_constants import GameConstants

        combat_type = next(iter(GameConstants.SHOP_COMBAT_MODULE_TYPES))
        filler_type = next(iter(GameConstants.SHOP_FILLER_MODULE_TYPES))
        combat_module = _make_module("CombatItem", combat_type, 5)
        filler_module = _make_module("FillerItem", filler_type, 5)
        service._static_cache = {"module": [combat_module, filler_module]}

        random.seed(42)
        results = []
        for _ in range(2000):
            result = await service._get_random_item_by_tech_level(mock_db, "module", 5, combat_module_prob=0.75)
            results.append(result)

        combat_count = results.count("CombatItem")
        combat_fraction = combat_count / len(results)
        assert 0.70 <= combat_fraction <= 0.80, (
            f"Expected combat fraction ~0.75, got {combat_fraction:.3f} ({combat_count}/2000)"
        )

    @pytest.mark.asyncio
    async def test_step_down_when_combat_empty_at_requested_tl(self, service, mock_db):
        """When combat bucket has no items at TL9, steps down to find TL8 item."""
        from services.game_constants import GameConstants

        combat_type = next(iter(GameConstants.SHOP_COMBAT_MODULE_TYPES))
        # Only a TL8 combat module in pool — no TL9 combat module
        tl8_combat = _make_module("Armour8", combat_type, 8)
        service._static_cache = {"module": [tl8_combat]}

        result = await service._get_random_item_by_tech_level(mock_db, "module", 9, combat_module_prob=1.0)

        assert result == "Armour8", f"Expected TL8 step-down, got {result!r}"

    @pytest.mark.asyncio
    async def test_row_tech_level_reflects_step_down(
        self, service, mock_db, mock_config_repo, mock_shop_repo, mock_module_repo
    ):
        """refresh_shop stores TL8 (actual catalog TL) not TL9 (band TL) for a stepped-down module."""
        from services.game_constants import GameConstants

        config = _make_config()
        config.shop_combat_module_prob = 1.0  # force combat bucket always
        mock_config_repo.get_by_guild_id.return_value = config

        combat_type = next(iter(GameConstants.SHOP_COMBAT_MODULE_TYPES))
        tl8_combat = _make_module("StepDownArmour", combat_type, 8)
        # Provide all cache keys so the ship/weapon/turret/secondary paths don't KeyError
        service._static_cache = {
            "ship": [],
            "weapon": [],
            "secondary": [],
            "turret": [],
            "module": [tl8_combat],
        }

        # _get_item_base_price: return a value for pricing
        service._get_item_base_price = AsyncMock(return_value=1000)
        # _get_item_tech_level: mock so it returns 8 (the actual catalog TL)
        service._get_item_tech_level = AsyncMock(return_value=8)
        mock_shop_repo.create_or_update = AsyncMock(return_value=_make_shop_item())
        mock_shop_repo.clear_shop_tier = AsyncMock()

        await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=9)

        # Find the module upsert call
        module_calls = [
            call for call in mock_shop_repo.create_or_update.call_args_list if call.args[1].get("item_type") == "module"
        ]
        assert module_calls, "No module upsert calls made"
        row_tl = module_calls[0].args[1]["tech_level"]
        assert row_tl == 8, f"Expected row tech_level=8 (step-down), got {row_tl}"

    @pytest.mark.asyncio
    async def test_item_tech_level_resolves_from_cache_without_db(self, service, mock_db, mock_module_repo):
        """_get_item_tech_level reads a module's actual TL from the warm static
        cache and does NOT hit module_repo.get_by_name (no per-draw DB round-trip
        during a bulk refresh)."""
        from services.game_constants import GameConstants

        combat_type = next(iter(GameConstants.SHOP_COMBAT_MODULE_TYPES))
        cached_module = _make_module("Armour8", combat_type, 8)
        service._static_cache = {"module": [cached_module]}
        # Make a DB lookup loud: if the fast-path is bypassed the test fails.
        mock_module_repo.get_by_name = AsyncMock(side_effect=AssertionError("DB hit despite warm cache"))

        tl = await service._get_item_tech_level(mock_db, "module", "Armour8", base_price=0)

        assert tl == 8, f"Expected cached TL 8, got {tl}"
        mock_module_repo.get_by_name.assert_not_called()

    @pytest.mark.asyncio
    async def test_ceiling_tl10_accepted(self, service, mock_db, mock_config_repo, mock_shop_repo):
        """refresh_shop with force_tech_level=10 succeeds (ceiling raised)."""
        config = _make_config()
        mock_config_repo.get_by_guild_id.return_value = config
        service._get_random_item_by_tech_level = AsyncMock(return_value=None)
        service._get_item_base_price = AsyncMock(return_value=0)
        mock_shop_repo.clear_shop_tier = AsyncMock()
        mock_shop_repo.create_or_update = AsyncMock(return_value=_make_shop_item())

        # Should NOT raise
        result = await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=10)
        assert result["tech_level"] == 10

    @pytest.mark.asyncio
    async def test_ceiling_tl11_rejected(self, service, mock_db):
        """refresh_shop with force_tech_level=11 raises ValueError."""
        with pytest.raises(ValueError, match="Tech level must be between 1 and 10"):
            await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=11)

    @pytest.mark.asyncio
    async def test_ceiling_tl0_rejected(self, service, mock_db):
        """refresh_shop with force_tech_level=0 raises ValueError."""
        with pytest.raises(ValueError, match="Tech level must be between 1 and 10"):
            await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=0)

    @pytest.mark.asyncio
    async def test_tl10_armor_reachable_with_combat_prob_one(self, service, mock_db):
        """A TL10 ArmourModule is returned when combat_module_prob=1.0 and TL10 is requested."""
        tl10_armour = _make_module("TL10Armour", "ArmourModule", 10)
        service._static_cache = {"module": [tl10_armour]}

        result = await service._get_random_item_by_tech_level(mock_db, "module", 10, combat_module_prob=1.0)

        assert result == "TL10Armour"

    @pytest.mark.asyncio
    async def test_tl1_draw_does_not_crash(self, service, mock_db):
        """A TL1 module draw succeeds without error when filler has TL1 items."""
        filler_type = next(iter(GameConstants.SHOP_FILLER_MODULE_TYPES))
        tl1_filler = _make_module("TL1Filler", filler_type, 1)
        service._static_cache = {"module": [tl1_filler]}

        result = await service._get_random_item_by_tech_level(mock_db, "module", 1, combat_module_prob=0.0)

        assert result == "TL1Filler"

    @pytest.mark.asyncio
    async def test_returns_none_gracefully_when_bucket_empty(self, service, mock_db):
        """Returns None gracefully when the chosen bucket has no items at any TL."""
        # Only filler in pool, but combat bucket requested
        filler_type = next(iter(GameConstants.SHOP_FILLER_MODULE_TYPES))
        filler_only = _make_module("FillerOnly", filler_type, 5)
        service._static_cache = {"module": [filler_only]}

        result = await service._get_random_item_by_tech_level(mock_db, "module", 5, combat_module_prob=1.0)

        assert result is None


# ===========================================================================
# Tests: QA additions — per-guild override path + cold-cache TL fallback
# ===========================================================================


class TestPerGuildCombatProbOverride:
    """Verify that a per-guild shop_combat_module_prob override is honoured
    end-to-end through refresh_shop -> _get_random_item_by_tech_level.

    This test exercises the resolve_constant path without short-circuiting the
    module-draw code via a pre-mocked _get_random_item_by_tech_level.
    """

    @pytest.mark.asyncio
    async def test_per_guild_override_100pct_only_combat_drawn(self, service, mock_db, mock_config_repo):
        """When guild config sets shop_combat_module_prob=1.0, every draw uses combat bucket."""
        from services.game_constants import GameConstants

        combat_type = next(iter(GameConstants.SHOP_COMBAT_MODULE_TYPES))
        filler_type = next(iter(GameConstants.SHOP_FILLER_MODULE_TYPES))
        combat_module = _make_module("CombatItem", combat_type, 5)
        filler_module = _make_module("FillerItem", filler_type, 5)
        service._static_cache = {
            "ship": [],
            "weapon": [],
            "secondary": [],
            "turret": [],
            "module": [combat_module, filler_module],
        }

        # Guild config with explicit shop_combat_module_prob override = 1.0
        config = MagicMock()
        config.tech_level_probabilities = {"same_level": 1.0, "one_lower": 0.0, "two_lower": 0.0}
        config.get_count_range = MagicMock(
            side_effect=lambda k: {"min": 0, "max": 0} if k != "module" else {"min": 5, "max": 5}
        )
        config.get_quantity_range = MagicMock(return_value={"min": 1, "max": 1})
        config.shop_combat_module_prob = 1.0  # explicit per-guild float override
        mock_config_repo.get_by_guild_id.return_value = config

        shop_items = []

        async def _fake_create_or_update(db, item_data):
            shop_items.append(item_data.get("item_name"))
            return _make_shop_item(item_name=item_data["item_name"])

        service.shop_repo = MagicMock()
        service.shop_repo.clear_shop_tier = AsyncMock()
        service.shop_repo.create_or_update = _fake_create_or_update
        service._get_item_base_price = AsyncMock(return_value=100)
        service._get_item_tech_level = AsyncMock(return_value=5)

        await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=5)

        # With prob=1.0 every module draw must land in the combat bucket (CombatItem, not FillerItem).
        assert "FillerItem" not in shop_items, (
            "Per-guild override shop_combat_module_prob=1.0 should exclude FillerItem"
        )
        assert "CombatItem" in shop_items, "CombatItem must be drawn when combat_prob=1.0"

    @pytest.mark.asyncio
    async def test_null_column_falls_back_to_default_75pct(self, service, mock_db, mock_config_repo):
        """NULL shop_combat_module_prob column falls back to 0.75 default."""
        from services.game_constants import GameConstants, resolve_constant

        config = MagicMock()
        config.shop_combat_module_prob = None  # NULL -> resolve_constant must fall back to 0.75
        mock_config_repo.get_by_guild_id.return_value = config

        resolved_prob = resolve_constant(config, "shop_combat_module_prob", GameConstants.SHOP_COMBAT_MODULE_PROB)
        assert resolved_prob == 0.75, f"NULL column must resolve to 0.75 default, got {resolved_prob!r}"


class TestGetItemTechLevelColdCachePath:
    """Verify the cold-cache slow path in _get_item_tech_level works correctly."""

    @pytest.mark.asyncio
    async def test_cold_cache_reads_from_repo(self, service, mock_db, mock_module_repo):
        """With no static cache, _get_item_tech_level falls back to repo lookup."""
        service._static_cache = None  # force cold path
        module_obj = MagicMock()
        module_obj.tech_level = 6
        mock_module_repo.get_by_name = AsyncMock(return_value=module_obj)

        tl = await service._get_item_tech_level(mock_db, "module", "SomeModule", base_price=0)

        assert tl == 6
        mock_module_repo.get_by_name.assert_awaited_once_with(mock_db, "SomeModule")

    @pytest.mark.asyncio
    async def test_cold_cache_missing_item_falls_back_to_tl1(self, service, mock_db, mock_module_repo):
        """Cold path with missing item falls back to TL=1."""
        service._static_cache = None
        mock_module_repo.get_by_name = AsyncMock(return_value=None)

        tl = await service._get_item_tech_level(mock_db, "module", "Ghost", base_price=0)

        assert tl == 1

    @pytest.mark.asyncio
    async def test_warm_cache_tech_level_zero_is_returned_not_skipped(self, service, mock_db):
        """Warm-cache fast path: tech_level=0 must be returned (not fall through to TL=1 default).

        Regression for the old ``if tech_level:`` truthiness bug where 0 was falsy
        and silently caused a fallback to 1.  The fix is ``if tech_level is not None:``.
        """
        cached_module = MagicMock()
        cached_module.name = "TL0Module"
        cached_module.tech_level = 0  # zero is falsy but valid
        service._static_cache = {"module": [cached_module], "weapon": [], "turret": [], "ship": [], "secondary": []}

        tl = await service._get_item_tech_level(mock_db, "module", "TL0Module", base_price=0)

        # Must return 0, not fall through to 1
        assert tl == 0

    @pytest.mark.asyncio
    async def test_cold_cache_tech_level_zero_is_returned_not_skipped(self, service, mock_db, mock_module_repo):
        """Cold-cache slow path: tech_level=0 must be returned (not fall through to TL=1 default).

        Regression for the same truthiness bug on the slow path.
        """
        service._static_cache = None
        module_obj = MagicMock()
        module_obj.tech_level = 0  # zero is falsy but valid
        mock_module_repo.get_by_name = AsyncMock(return_value=module_obj)

        tl = await service._get_item_tech_level(mock_db, "module", "TL0Module", base_price=0)

        # Must return 0, not fall through to 1
        assert tl == 0
