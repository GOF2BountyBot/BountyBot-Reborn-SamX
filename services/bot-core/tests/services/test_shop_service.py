"""
Unit tests for ShopService.

The shared.bblogger module is mocked via sys.modules BEFORE any service
module is imported (see conftest.py at the tests/ root).
"""

import sys
import types
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

from services.shop_service import ShopService  # noqa: I001


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
    db.begin = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(), __aexit__=AsyncMock()))
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
def service(mock_shop_repo, mock_config_repo, mock_player_repo, mock_inventory_repo) -> ShopService:
    svc = ShopService()
    svc.shop_repo = mock_shop_repo
    svc.config_repo = mock_config_repo
    svc.player_repo = mock_player_repo
    svc.inventory_repo = mock_inventory_repo
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
    async def test_raises_when_shop_item_not_found(
        self, service, mock_db, mock_player_repo, mock_shop_repo
    ):
        """ValueError raised when shop item does not exist."""
        mock_player_repo.get_by_id.return_value = _make_player()
        mock_shop_repo.get_by_id.return_value = None

        with pytest.raises(ValueError, match="Shop item 55 not found"):
            await service.purchase_item(mock_db, player_id=1, shop_item_id=55)

    @pytest.mark.asyncio
    async def test_raises_when_player_tier_too_low(
        self, service, mock_db, mock_player_repo, mock_shop_repo
    ):
        """ValueError raised when player cannot access the shop tier."""
        player = _make_player(tier="Bronze")
        shop_item = _make_shop_item(tier="Gold")  # Requires Gold+
        mock_player_repo.get_by_id.return_value = player
        mock_shop_repo.get_by_id.return_value = shop_item

        with pytest.raises(ValueError, match="cannot access"):
            await service.purchase_item(mock_db, player_id=1, shop_item_id=10)

    @pytest.mark.asyncio
    async def test_raises_when_insufficient_shop_quantity(
        self, service, mock_db, mock_player_repo, mock_shop_repo
    ):
        """ValueError raised when shop has fewer than requested quantity."""
        player = _make_player(tier="Bronze", credits=5000)
        shop_item = _make_shop_item(tier="Bronze", quantity=1, price=100)
        mock_player_repo.get_by_id.return_value = player
        mock_shop_repo.get_by_id.return_value = shop_item

        with pytest.raises(ValueError, match="Insufficient quantity"):
            await service.purchase_item(mock_db, player_id=1, shop_item_id=10, quantity=5)

    @pytest.mark.asyncio
    async def test_raises_when_insufficient_credits(
        self, service, mock_db, mock_player_repo, mock_shop_repo
    ):
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
        """shop_repo.remove is called when shop quantity hits 0 after purchase."""
        player = _make_player(tier="Bronze", credits=500)
        shop_item = _make_shop_item(tier="Bronze", quantity=1, price=100)
        mock_player_repo.get_by_id.return_value = player
        mock_shop_repo.get_by_id.return_value = shop_item

        await service.purchase_item(mock_db, player_id=1, shop_item_id=10, quantity=1)

        mock_shop_repo.remove.assert_awaited_once_with(mock_db, shop_item)

    @pytest.mark.asyncio
    async def test_updates_quantity_when_partial_purchase(
        self, service, mock_db, mock_player_repo, mock_shop_repo
    ):
        """shop_repo.update_quantity is called for partial purchase."""
        player = _make_player(tier="Bronze", credits=1000)
        shop_item = _make_shop_item(tier="Bronze", quantity=5, price=100)
        mock_player_repo.get_by_id.return_value = player
        mock_shop_repo.get_by_id.return_value = shop_item

        await service.purchase_item(mock_db, player_id=1, shop_item_id=10, quantity=2)

        mock_shop_repo.update_quantity.assert_awaited_once_with(mock_db, 10, 3)


# ===========================================================================
# Tests: sell_item
# ===========================================================================


class TestSellItem:
    """Tests for ShopService.sell_item."""

    @pytest.mark.asyncio
    async def test_successful_sell_returns_transaction_details(
        self, service, mock_db, mock_player_repo, mock_inventory_repo, mock_config_repo, mock_shop_repo
    ):
        """Returns transaction details on a successful sale."""
        player = _make_player(guild_id=999, credits=500)
        inventory_item = _make_inventory_item(quantity=2)
        config = _make_config(sale_price_factor=0.8)

        mock_player_repo.get_by_id.return_value = player
        mock_inventory_repo.get_player_item.return_value = inventory_item
        mock_config_repo.get_by_guild_id.return_value = config
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
        assert result["unit_sell_price"] == 400  # 500 * 0.8
        assert result["total_sell_value"] == 400
        assert result["new_credits"] == 900  # 500 + 400

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
            await service.sell_item(
                mock_db, player_id=1, item_type="weapon", item_name="Gun", target_tier="Diamond"
            )

    @pytest.mark.asyncio
    async def test_raises_when_item_not_in_inventory(
        self, service, mock_db, mock_player_repo, mock_inventory_repo
    ):
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
    async def test_uses_default_sale_factor_when_no_config(
        self, service, mock_db, mock_player_repo, mock_inventory_repo, mock_config_repo, mock_shop_repo
    ):
        """Uses 0.8 sale price factor when no guild config exists."""
        player = _make_player(credits=0)
        mock_player_repo.get_by_id.return_value = player
        mock_inventory_repo.get_player_item.return_value = _make_inventory_item(quantity=1)
        mock_config_repo.get_by_guild_id.return_value = None  # No config
        mock_shop_repo.get_shop_item_by_name.return_value = None

        service._get_item_base_price = AsyncMock(return_value=1000)

        result = await service.sell_item(
            mock_db, player_id=1, item_type="weapon", item_name="Gun", quantity=1
        )

        assert result["unit_sell_price"] == 800  # 1000 * 0.8


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
    async def test_creates_default_config_when_none_exists(
        self, service, mock_db, mock_config_repo, mock_shop_repo
    ):
        """If no config, create_default_config is called."""
        mock_config_repo.get_by_guild_id.return_value = None
        default_config = _make_config()
        mock_config_repo.create_default_config.return_value = default_config

        service._get_random_item_by_tech_level = AsyncMock(return_value=None)  # Skip item creation

        await service.refresh_shop(mock_db, guild_id=999, tier="Bronze", force_tech_level=1)

        mock_config_repo.create_default_config.assert_awaited_once_with(mock_db, 999)

    @pytest.mark.asyncio
    async def test_clears_existing_shop_items_before_refresh(
        self, service, mock_db, mock_config_repo, mock_shop_repo
    ):
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


class TestGetRandomItemByTechLevel:
    """Tests for ShopService._get_random_item_by_tech_level."""

    @pytest.mark.asyncio
    async def test_returns_item_name_for_valid_type(self, service):
        """Returns a non-None string for a recognised item type."""
        result = await service._get_random_item_by_tech_level("weapon", 3)

        assert result is not None
        assert isinstance(result, str)
        assert "3" in result  # Placeholder format: Weapon_3_N

    @pytest.mark.asyncio
    async def test_returns_item_for_all_valid_types(self, service):
        """Returns a non-None item for each valid item type."""
        for item_type in ShopService.VALID_ITEM_TYPES:
            result = await service._get_random_item_by_tech_level(item_type, 5)
            assert result is not None

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_type(self, service):
        """Returns None when item type is not in placeholder_items dict."""
        result = await service._get_random_item_by_tech_level("banana", 1)

        assert result is None


# ===========================================================================
# Tests: _get_item_base_price
# ===========================================================================


class TestGetItemBasePrice:
    """Tests for ShopService._get_item_base_price."""

    @pytest.mark.asyncio
    async def test_returns_price_range_for_level_1(self, service):
        """Item name containing '1' returns a price in [100, 500]."""
        price = await service._get_item_base_price("Weapon_1_3")

        assert 100 <= price <= 500

    @pytest.mark.asyncio
    async def test_returns_price_range_for_level_2(self, service):
        """Item name containing '2' (but not '1') returns a price in [500, 1000]."""
        price = await service._get_item_base_price("Ship_2_4")

        assert 500 <= price <= 1000

    @pytest.mark.asyncio
    async def test_returns_price_range_for_high_level(self, service):
        """Item name without '1' or '2' returns a price in [1000, 5000]."""
        # Use a name with no '1' or '2' digits
        price = await service._get_item_base_price("Module_5_3")

        assert 1000 <= price <= 5000


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
            mock_db, guild_id=999, tier="Bronze", item_type="weapon",
            item_name="Gun", quantity=2, base_price=300
        )

        mock_shop_repo.update_quantity.assert_awaited_once_with(mock_db, 20, 5)  # 3 + 2
        mock_shop_repo.create_or_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_new_shop_item(self, service, mock_db, mock_shop_repo):
        """When no existing shop item, create_or_update is called with full item data."""
        mock_shop_repo.get_shop_item_by_name.return_value = None

        await service._add_item_to_shop(
            mock_db, guild_id=999, tier="Silver", item_type="module",
            item_name="Shield", quantity=1, base_price=500
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
                mock_db, guild_id=999, tier="Bronze", item_type="weapon",
                item_name="Gun", quantity=1, base_price=100
            )
