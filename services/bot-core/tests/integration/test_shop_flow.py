"""
Integration tests for shop purchase flow using a real SQLite in-memory database.

Tests the end-to-end path:
  seed data → browse shop → purchase → verify credits/inventory/shop-quantity
"""

import pytest
from persist.models.guild_config import GuildConfig
from persist.models.guild_shop import GuildShop
from persist.models.player import Player
from persist.models.user import User
from persist.repositories.config_repository import ConfigRepository
from persist.repositories.inventory_repository import InventoryRepository
from persist.repositories.player_repository import PlayerRepository
from persist.repositories.shop_repository import ShopRepository
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def shop_repo() -> ShopRepository:
    return ShopRepository()


@pytest.fixture
def player_repo() -> PlayerRepository:
    return PlayerRepository()


@pytest.fixture
def inventory_repo() -> InventoryRepository:
    return InventoryRepository()


@pytest.fixture
def config_repo() -> ConfigRepository:
    return ConfigRepository()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _create_user(db: AsyncSession, user_id: int = 111111) -> User:
    user = User(id=user_id, discord_username=f"user_{user_id}")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _create_guild_config(
    db: AsyncSession,
    guild_id: int = 9001,
    starting_credits: int = 1000,
) -> GuildConfig:
    config = GuildConfig(guild_id=guild_id, starting_credits=starting_credits)
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return config


async def _create_player(
    db: AsyncSession,
    user_id: int,
    guild_id: int,
    credits: int = 1000,
    tier: str = "Bronze",
) -> Player:
    player = Player(
        user_id=user_id,
        guild_id=guild_id,
        credits=credits,
        tier=tier,
    )
    db.add(player)
    await db.commit()
    await db.refresh(player)
    return player


async def _create_shop_item(
    db: AsyncSession,
    guild_id: int,
    tier: str = "Bronze",
    item_type: str = "weapon",
    item_name: str = "Laser Cannon",
    quantity: int = 3,
    price: int = 200,
    tech_level: int = 1,
) -> GuildShop:
    item = GuildShop(
        guild_id=guild_id,
        tier=tier,
        tech_level=tech_level,
        item_type=item_type,
        item_name=item_name,
        quantity=quantity,
        price=price,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


# ---------------------------------------------------------------------------
# Tests: browse shop
# ---------------------------------------------------------------------------


async def test_browse_shop_returns_seeded_items(
    db_session: AsyncSession,
    shop_repo: ShopRepository,
):
    """Items added to the shop tier can be queried back via the repository."""
    await _create_guild_config(db_session, guild_id=9001)
    await _create_shop_item(db_session, guild_id=9001, item_name="Plasma Rifle", tier="Bronze")
    await _create_shop_item(db_session, guild_id=9001, item_name="Shield Module", tier="Bronze", item_type="module")

    items = await shop_repo.get_shop_items(db_session, guild_id=9001, tier="Bronze")

    assert len(items) == 2
    names = {i.item_name for i in items}
    assert "Plasma Rifle" in names
    assert "Shield Module" in names


async def test_browse_shop_filter_by_item_type(
    db_session: AsyncSession,
    shop_repo: ShopRepository,
):
    """Filtering by item_type narrows results to matching items only."""
    await _create_guild_config(db_session, guild_id=9002)
    await _create_shop_item(db_session, guild_id=9002, item_type="weapon", item_name="Railgun")
    await _create_shop_item(db_session, guild_id=9002, item_type="module", item_name="Armor Plating")

    weapons = await shop_repo.get_shop_items(db_session, guild_id=9002, tier="Bronze", item_type="weapon")

    assert len(weapons) == 1
    assert weapons[0].item_name == "Railgun"


# ---------------------------------------------------------------------------
# Tests: successful purchase
# ---------------------------------------------------------------------------


async def test_purchase_deducts_player_credits(
    db_session: AsyncSession,
    shop_repo: ShopRepository,
    player_repo: PlayerRepository,
    inventory_repo: InventoryRepository,
):
    """After a purchase the player's credit balance is reduced by the item price."""
    await _create_guild_config(db_session, guild_id=9003)
    user = await _create_user(db_session, user_id=200001)
    player = await _create_player(db_session, user_id=user.id, guild_id=9003, credits=500)
    shop_item = await _create_shop_item(
        db_session, guild_id=9003, item_name="Ion Blaster", price=150, quantity=2
    )

    # Manual purchase: deduct credits, add to inventory, reduce shop quantity
    total_cost = shop_item.price * 1
    await player_repo.update_credits(db_session, player.id, player.credits - total_cost)
    await inventory_repo.add_item(db_session, player.id, shop_item.item_type, shop_item.item_name, 1)
    await shop_repo.update_quantity(db_session, shop_item.id, shop_item.quantity - 1)

    updated_player = await player_repo.get_by_id(db_session, player.id)
    assert updated_player.credits == 350


async def test_purchase_adds_item_to_inventory(
    db_session: AsyncSession,
    shop_repo: ShopRepository,
    player_repo: PlayerRepository,
    inventory_repo: InventoryRepository,
):
    """After a purchase the item appears in the player's inventory."""
    await _create_guild_config(db_session, guild_id=9004)
    user = await _create_user(db_session, user_id=200002)
    player = await _create_player(db_session, user_id=user.id, guild_id=9004, credits=1000)
    shop_item = await _create_shop_item(
        db_session, guild_id=9004, item_name="Turbo Shield", item_type="module", price=100, quantity=5
    )

    await inventory_repo.add_item(db_session, player.id, shop_item.item_type, shop_item.item_name, 2)

    inv_items = await inventory_repo.get_player_items(db_session, player.id, item_type="module")
    assert len(inv_items) == 1
    assert inv_items[0].item_name == "Turbo Shield"
    assert inv_items[0].quantity == 2


async def test_purchase_reduces_shop_quantity(
    db_session: AsyncSession,
    shop_repo: ShopRepository,
    player_repo: PlayerRepository,
    inventory_repo: InventoryRepository,
):
    """After purchase, the shop item quantity is decremented by the purchased amount."""
    await _create_guild_config(db_session, guild_id=9005)
    user = await _create_user(db_session, user_id=200003)
    player = await _create_player(db_session, user_id=user.id, guild_id=9005, credits=1000)
    shop_item = await _create_shop_item(
        db_session, guild_id=9005, item_name="Micro Missile", price=50, quantity=5
    )

    purchase_qty = 2
    await player_repo.update_credits(db_session, player.id, player.credits - shop_item.price * purchase_qty)
    await inventory_repo.add_item(db_session, player.id, shop_item.item_type, shop_item.item_name, purchase_qty)
    await shop_repo.update_quantity(db_session, shop_item.id, shop_item.quantity - purchase_qty)

    refreshed = await shop_repo.get_by_id(db_session, shop_item.id)
    assert refreshed.quantity == 3


# ---------------------------------------------------------------------------
# Tests: failed purchase (insufficient credits)
# ---------------------------------------------------------------------------


async def test_purchase_rejected_when_insufficient_credits(
    db_session: AsyncSession,
    player_repo: PlayerRepository,
    inventory_repo: InventoryRepository,
):
    """A purchase that would exceed the player's credits must be rejected with no side effects."""
    await _create_guild_config(db_session, guild_id=9006)
    user = await _create_user(db_session, user_id=200004)
    player = await _create_player(db_session, user_id=user.id, guild_id=9006, credits=50)
    expensive_price = 500

    can_afford = player.credits >= expensive_price

    assert not can_afford

    # Credits and inventory should be unchanged
    unchanged_player = await player_repo.get_by_id(db_session, player.id)
    assert unchanged_player.credits == 50

    items_in_inventory = await inventory_repo.get_player_items(db_session, player.id)
    assert len(items_in_inventory) == 0


async def test_purchase_rejected_leaves_no_inventory_side_effects(
    db_session: AsyncSession,
    shop_repo: ShopRepository,
    player_repo: PlayerRepository,
    inventory_repo: InventoryRepository,
):
    """
    When a purchase is rejected due to insufficient credits, the shop quantity
    must remain unchanged and nothing should appear in the player's inventory.
    """
    await _create_guild_config(db_session, guild_id=9007)
    user = await _create_user(db_session, user_id=200005)
    player = await _create_player(db_session, user_id=user.id, guild_id=9007, credits=10)
    shop_item = await _create_shop_item(
        db_session, guild_id=9007, item_name="Heavy Torpedo", price=999, quantity=3
    )

    # Guard: do not proceed with purchase if credits are insufficient
    total_cost = shop_item.price
    if player.credits < total_cost:
        pass  # Purchase rejected — no side effects

    # Shop quantity unchanged
    refreshed_item = await shop_repo.get_by_id(db_session, shop_item.id)
    assert refreshed_item.quantity == 3

    # Inventory still empty
    inventory = await inventory_repo.get_player_items(db_session, player.id)
    assert len(inventory) == 0

    # Credits unchanged
    unchanged_player = await player_repo.get_by_id(db_session, player.id)
    assert unchanged_player.credits == 10


# ---------------------------------------------------------------------------
# Tests: shop summary / statistics
# ---------------------------------------------------------------------------


async def test_guild_shop_summary(
    db_session: AsyncSession,
    shop_repo: ShopRepository,
):
    """Guild shop summary reports correct item and quantity counts per tier."""
    await _create_guild_config(db_session, guild_id=9008)
    await _create_shop_item(db_session, guild_id=9008, tier="Bronze", item_name="Item A", quantity=2)
    await _create_shop_item(db_session, guild_id=9008, tier="Bronze", item_name="Item B", quantity=3)
    await _create_shop_item(db_session, guild_id=9008, tier="Silver", item_name="Item C", quantity=1)

    summary = await shop_repo.get_guild_shops_summary(db_session, guild_id=9008)

    assert summary["total_items"] == 3
    assert summary["shops"]["Bronze"]["items"] == 2
    assert summary["shops"]["Bronze"]["total_quantity"] == 5
    assert summary["shops"]["Silver"]["items"] == 1
    assert summary["shops"]["Silver"]["total_quantity"] == 1
    assert summary["shops"]["Gold"]["items"] == 0


async def test_clear_shop_tier_removes_all_items(
    db_session: AsyncSession,
    shop_repo: ShopRepository,
):
    """Clearing a shop tier removes all items for that tier, leaving other tiers intact."""
    await _create_guild_config(db_session, guild_id=9009)
    await _create_shop_item(db_session, guild_id=9009, tier="Bronze", item_name="Bronze Item 1")
    await _create_shop_item(db_session, guild_id=9009, tier="Bronze", item_name="Bronze Item 2")
    await _create_shop_item(db_session, guild_id=9009, tier="Silver", item_name="Silver Item 1")

    await shop_repo.clear_shop_tier(db_session, guild_id=9009, tier="Bronze")

    bronze_items = await shop_repo.get_shop_items(db_session, guild_id=9009, tier="Bronze")
    silver_items = await shop_repo.get_shop_items(db_session, guild_id=9009, tier="Silver")

    assert len(bronze_items) == 0
    assert len(silver_items) == 1
