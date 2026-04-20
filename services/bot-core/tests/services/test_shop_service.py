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
) -> MagicMock:
    """Create a mock static Ship model."""
    ship = MagicMock()
    ship.name = name
    ship.value = value
    ship.max_primaries = max_primaries
    ship.max_modules = max_modules
    ship.max_turrets = max_turrets
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
) -> ShopService:
    svc = ShopService()
    svc.shop_repo = mock_shop_repo
    svc.config_repo = mock_config_repo
    svc.player_repo = mock_player_repo
    svc.inventory_repo = mock_inventory_repo
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
        """ValueError raised for an unrecognised item type filter."""
        with pytest.raises(ValueError, match="Invalid item type"):
            await service.get_shop_items(mock_db, guild_id=999, tier="Bronze", item_type="banana")

    @pytest.mark.asyncio
    async def test_passes_item_type_filter_to_repo(self, service, mock_db, mock_shop_repo):
        """item_type filter is forwarded to the repository."""
        service._check_and_refresh_shop = AsyncMock()
        mock_shop_repo.get_shop_items.return_value = []

        await service.get_shop_items(mock_db, guild_id=999, tier="Silver", item_type="weapon")

        mock_shop_repo.get_shop_items.assert_awaited_once_with(mock_db, 999, "Silver", "weapon")

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
        shop_item = _make_shop_item(tier="Bronze", quantity=5, price=100)
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
        mock_shop_repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="Shop item 55 not found"):
            await service.purchase_item(mock_db, player_id=1, shop_item_id=55)

    @pytest.mark.asyncio
    async def test_raises_when_player_tier_too_low(self, service, mock_db, mock_player_repo, mock_shop_repo):
        """ValueError raised when player cannot access the shop tier."""
        player = _make_player(tier="Bronze")
        shop_item = _make_shop_item(tier="Gold")  # Requires Gold+
        mock_player_repo.get_by_id.return_value = player
        mock_shop_repo.get_by_id.return_value = shop_item

        with pytest.raises(ValueError, match="cannot access"):
            await service.purchase_item(mock_db, player_id=1, shop_item_id=10)

    @pytest.mark.asyncio
    async def test_raises_when_insufficient_shop_quantity(self, service, mock_db, mock_player_repo, mock_shop_repo):
        """ValueError raised when shop has fewer than requested quantity."""
        player = _make_player(tier="Bronze", credits=5000)
        shop_item = _make_shop_item(tier="Bronze", quantity=1, price=100)
        mock_player_repo.get_by_id.return_value = player
        mock_shop_repo.get_by_id.return_value = shop_item

        with pytest.raises(ValueError, match="Insufficient quantity"):
            await service.purchase_item(mock_db, player_id=1, shop_item_id=10, quantity=5)

    @pytest.mark.asyncio
    async def test_raises_when_insufficient_credits(self, service, mock_db, mock_player_repo, mock_shop_repo):
        """ValueError raised when player cannot afford the purchase."""
        player = _make_player(tier="Bronze", credits=50)
        shop_item = _make_shop_item(tier="Bronze", quantity=5, price=200)
        mock_player_repo.get_by_id.return_value = player
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
    """Tests for ShopService.sell_item."""

    @pytest.mark.asyncio
    async def test_successful_sell_returns_transaction_details(
        self, service, mock_db, mock_player_repo, mock_inventory_repo, mock_shop_repo
    ):
        """Returns transaction details on a successful sale at full value (no tax)."""
        player = _make_player(guild_id=999, credits=500)
        inventory_item = _make_inventory_item(quantity=2)

        mock_player_repo.get_by_id.return_value = player
        # sell_item re-fetches under lock; use same player object so credits is a real int
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_inventory_repo.get_player_item.return_value = inventory_item
        mock_shop_repo.get_shop_item_by_name.return_value = None  # No existing shop item

        # Mock _get_item_base_price to return a deterministic value
        service._get_item_base_price = AsyncMock(return_value=500)

        result = await service.sell_item(
            mock_db, player_id=1, item_type="weapon", item_name="Micro Gun MK I", quantity=1
        )

        assert result["player_id"] == 1
        assert result["item_type"] == "weapon"
        assert result["item_name"] == "Micro Gun MK I"
        assert result["quantity"] == 1
        assert result["unit_sell_price"] == 500  # full value, no tax
        assert result["total_sell_value"] == 500
        assert result["new_credits"] == 1000  # 500 + 500

    @pytest.mark.asyncio
    async def test_raises_when_player_not_found(self, service, mock_db, mock_player_repo):
        """ValueError raised when player does not exist."""
        mock_player_repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="Player 7 not found"):
            await service.sell_item(mock_db, player_id=7, item_type="weapon", item_name="Gun")

    @pytest.mark.asyncio
    async def test_raises_for_invalid_item_type(self, service, mock_db, mock_player_repo):
        """ValueError raised for unrecognised item type."""
        mock_player_repo.get_by_id.return_value = _make_player()

        with pytest.raises(ValueError, match="Invalid item type"):
            await service.sell_item(mock_db, player_id=1, item_type="banana", item_name="Gun")

    @pytest.mark.asyncio
    async def test_raises_for_invalid_target_tier(self, service, mock_db, mock_player_repo):
        """ValueError raised for unrecognised target tier."""
        mock_player_repo.get_by_id.return_value = _make_player()

        with pytest.raises(ValueError, match="Invalid target tier"):
            await service.sell_item(mock_db, player_id=1, item_type="weapon", item_name="Gun", target_tier="Diamond")

    @pytest.mark.asyncio
    async def test_raises_when_item_not_in_inventory(self, service, mock_db, mock_player_repo, mock_inventory_repo):
        """ValueError raised when player does not own the item."""
        mock_player_repo.get_by_id.return_value = _make_player()
        mock_inventory_repo.get_player_item.return_value = None

        with pytest.raises(ValueError, match="Insufficient item quantity"):
            await service.sell_item(mock_db, player_id=1, item_type="weapon", item_name="Missing Gun")

    @pytest.mark.asyncio
    async def test_raises_when_insufficient_inventory_quantity(
        self, service, mock_db, mock_player_repo, mock_inventory_repo
    ):
        """ValueError raised when player has fewer than requested quantity."""
        mock_player_repo.get_by_id.return_value = _make_player()
        mock_inventory_repo.get_player_item.return_value = _make_inventory_item(quantity=1)

        with pytest.raises(ValueError, match="Insufficient item quantity"):
            await service.sell_item(mock_db, player_id=1, item_type="weapon", item_name="Gun", quantity=5)

    @pytest.mark.asyncio
    async def test_sell_item_uses_full_value_no_tax(
        self, service, mock_db, mock_player_repo, mock_inventory_repo, mock_shop_repo
    ):
        """sell_item credits full base value (no sell tax)."""
        player = _make_player(credits=0)
        mock_player_repo.get_by_id.return_value = player
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_inventory_repo.get_player_item.return_value = _make_inventory_item(quantity=1)
        mock_shop_repo.get_shop_item_by_name.return_value = None

        service._get_item_base_price = AsyncMock(return_value=1000)

        result = await service.sell_item(mock_db, player_id=1, item_type="weapon", item_name="Gun", quantity=1)

        assert result["unit_sell_price"] == 1000  # full value, no tax


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
        with pytest.raises(ValueError, match="Tech level must be between 1 and 9"):
            await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=0)

    @pytest.mark.asyncio
    async def test_raises_for_tech_level_ten(self, service, mock_db):
        """ValueError raised when forced tech level is 10."""
        with pytest.raises(ValueError, match="Tech level must be between 1 and 9"):
            await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=10)

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

        async def _fake_get_random(db, item_type, tech_level):
            nonlocal call_count
            name = real_item_names[call_count % len(real_item_names)]
            call_count += 1
            return name

        config = _make_config()
        mock_config_repo.get_by_guild_id.return_value = config
        service._get_random_item_by_tech_level = _fake_get_random
        service._get_item_base_price = AsyncMock(return_value=500)
        created_items = []

        async def _fake_create_or_update(db, item_data):
            shop_item = _make_shop_item(item_name=item_data["item_name"])
            created_items.append(item_data["item_name"])
            return shop_item

        mock_shop_repo.create_or_update = _fake_create_or_update

        result = await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=1)

        # Shop was refreshed and items were generated
        assert result["items_generated"] > 0
        # Every generated item name is a real game asset name (not a placeholder)
        for name in created_items:
            assert name in real_item_names, f"Unexpected item name in shop: {name!r}"


# ===========================================================================
# Tests: _can_access_tier (synchronous helper)
# ===========================================================================


class TestCanAccessTier:
    """Tests for ShopService._can_access_tier."""

    def test_bronze_can_access_bronze(self):
        svc = ShopService.__new__(ShopService)
        assert svc._can_access_tier("Bronze", "Bronze") is True

    def test_silver_can_access_bronze(self):
        svc = ShopService.__new__(ShopService)
        assert svc._can_access_tier("Silver", "Bronze") is True

    def test_bronze_cannot_access_silver(self):
        svc = ShopService.__new__(ShopService)
        assert svc._can_access_tier("Bronze", "Silver") is False

    def test_platinum_can_access_all_tiers(self):
        svc = ShopService.__new__(ShopService)
        for tier in ["Bronze", "Silver", "Gold", "Platinum"]:
            assert svc._can_access_tier("Platinum", tier) is True

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
        """Returns the name of a module fetched from the module_repo."""
        module = _make_db_item("Shield Generator", tech_level=2)
        mock_module_repo.list_all = AsyncMock(return_value=[module])

        result = await service._get_random_item_by_tech_level(mock_db, "module", 2)

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
        # All repos return None (default fixture setup)
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

        mock_shop_repo.update_quantity.assert_awaited_once_with(mock_db, 20, 5)  # 3 + 2
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


# ===========================================================================
# Tests: purchase_ship
# ===========================================================================


class TestPurchaseShip:
    """Tests for ShopService.purchase_ship."""

    @pytest.mark.asyncio
    async def test_ship_buy_keep_old_credits_deducted_new_ship_created(
        self, service, mock_db, mock_player_repo, mock_shop_repo, mock_ship_repo, mock_player_ship_repo
    ):
        """Keep-old path: credits deducted, new ship added, old ship still exists."""
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

        result = await service.purchase_ship(mock_db, player_id=1, shop_item_id=10, sell_old_ship=False)

        assert result["item_name"] == "Hammerhead"
        assert result["remaining_credits"] == 5000  # 10000 - 5000
        assert result["trade_in_value"] == 0
        assert result["net_cost"] == 5000
        # Old ship repo delete should NOT be called
        mock_db.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ship_buy_sell_old_credits_adjusted_with_trade_in(
        self, service, mock_db, mock_player_repo, mock_shop_repo, mock_ship_repo, mock_player_ship_repo
    ):
        """Sell-old path: credits adjusted with trade-in, old ship removed."""
        player = _make_player(guild_id=999, credits=4000)
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

        result = await service.purchase_ship(mock_db, player_id=1, shop_item_id=10, sell_old_ship=True)

        # 4000 + 2000 (trade-in) - 5000 (new ship) = 1000
        assert result["remaining_credits"] == 1000
        assert result["trade_in_value"] == 2000
        assert result["net_cost"] == 3000  # 5000 - 2000
        # Old ship should be deleted
        mock_db.delete.assert_awaited_once_with(old_player_ship)

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

        result = await service.purchase_ship(mock_db, player_id=1, shop_item_id=10, sell_old_ship=False)

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
    ):
        """Items that don't fit on new ship are unequipped to inventory."""
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

        result = await service.purchase_ship(mock_db, player_id=1, shop_item_id=10, sell_old_ship=False)

        # 1 weapon fits, 2 weapons + 2 modules overflow = 4 items unequipped
        assert result["items_transferred"] == 1
        assert result["items_unequipped_to_inventory"] == 4
        # inventory_repo.add_item called 4 times for the overflow items
        assert mock_inventory_repo.add_item.await_count == 4

    @pytest.mark.asyncio
    async def test_ship_buy_insufficient_credits_no_trade_in_raises(
        self, service, mock_db, mock_player_repo, mock_shop_repo, mock_ship_repo, mock_player_ship_repo
    ):
        """ValueError raised when player cannot afford new ship (keep-old path)."""
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
            await service.purchase_ship(mock_db, player_id=1, shop_item_id=10, sell_old_ship=False)

    @pytest.mark.asyncio
    async def test_ship_buy_insufficient_credits_with_trade_in_raises(
        self, service, mock_db, mock_player_repo, mock_shop_repo, mock_ship_repo, mock_player_ship_repo
    ):
        """ValueError raised when player + trade-in cannot afford new ship."""
        player = _make_player(credits=100)
        shop_item = _make_shop_item(item_type="ship", item_name="Hammerhead", price=5000)
        new_ship_static = _make_ship_static(name="Hammerhead", value=5000)
        old_player_ship = _make_player_ship(ship_name="Crow")
        old_ship_static = _make_ship_static(name="Crow", value=2000)

        mock_player_repo.get_by_id.return_value = player
        # Credit check is done under lock; provide player so the ValueError is raised correctly
        mock_player_repo.get_by_id_for_update.return_value = player
        mock_shop_repo.get_by_id.return_value = shop_item
        mock_ship_repo.get_by_name.side_effect = lambda db, name: (
            new_ship_static if name == "Hammerhead" else old_ship_static
        )
        mock_player_ship_repo.get_active_ship.return_value = old_player_ship

        with pytest.raises(ValueError, match="Insufficient credits"):
            await service.purchase_ship(mock_db, player_id=1, shop_item_id=10, sell_old_ship=True)

    @pytest.mark.asyncio
    async def test_buy_non_ship_via_purchase_ship_raises(self, service, mock_db, mock_player_repo, mock_shop_repo):
        """ValueError raised when shop item is not a ship."""
        player = _make_player(credits=10_000)
        shop_item = _make_shop_item(item_type="weapon", item_name="Pulse Laser", price=200)

        mock_player_repo.get_by_id.return_value = player
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

        result = await service.purchase_ship(mock_db, player_id=1, shop_item_id=10, sell_old_ship=False)

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

        await service.purchase_ship(mock_db, player_id=1, shop_item_id=10)

        # quantity was 3, should be 2 after purchase
        mock_shop_repo.update_quantity.assert_awaited_once_with(mock_db, 10, 2)

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

        await service.purchase_ship(mock_db, player_id=1, shop_item_id=10)

        mock_shop_repo.remove.assert_awaited_once_with(mock_db, shop_item)


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
    ):
        """With clear_equipment=True, all equipped items are moved to inventory before selling."""
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

        result = await service.sell_ship(mock_db, player_id=1, ship_id=201, clear_equipment=True)

        # 2 weapons + 1 module = 3 items unequipped
        assert result["items_unequipped_to_inventory"] == 3
        assert mock_inventory_repo.add_item.await_count == 3

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
        mock_player_ship_repo.get_by_id.return_value = other_players_ship

        with pytest.raises(ValueError, match="does not belong to player"):
            await service.sell_ship(mock_db, player_id=1, ship_id=300)

    @pytest.mark.asyncio
    async def test_sell_ship_not_found_raises(self, service, mock_db, mock_player_repo, mock_player_ship_repo):
        """ValueError raised when ship ID does not exist."""
        player = _make_player(guild_id=999, credits=1000)

        mock_player_repo.get_by_id.return_value = player
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
