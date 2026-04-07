"""
Integration tests for inventory lifecycle using a real SQLite in-memory database.

Tests the end-to-end path:
  seed player → add items → query → remove items → verify quantities
"""

import pytest
from persist.models.guild_config import GuildConfig
from persist.models.player import Player
from persist.models.player_inventory import PlayerInventory
from persist.models.user import User
from persist.repositories.inventory_repository import InventoryRepository
from persist.repositories.player_repository import PlayerRepository
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def inventory_repo() -> InventoryRepository:
    return InventoryRepository()


@pytest.fixture
def player_repo() -> PlayerRepository:
    return PlayerRepository()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _create_user(db: AsyncSession, user_id: int = 500001) -> User:
    user = User(id=user_id, discord_username=f"user_{user_id}")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _create_guild_config(db: AsyncSession, guild_id: int = 7001) -> GuildConfig:
    config = GuildConfig(guild_id=guild_id, starting_credits=500)
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return config


async def _create_player(
    db: AsyncSession,
    user_id: int,
    guild_id: int,
    credits: int = 500,
) -> Player:
    player = Player(
        user_id=user_id,
        guild_id=guild_id,
        credits=credits,
    )
    db.add(player)
    await db.commit()
    await db.refresh(player)
    return player


async def _add_inventory_item(
    db: AsyncSession,
    repo: InventoryRepository,
    player_id: int,
    item_type: str = "weapon",
    item_name: str = "Test Weapon",
    quantity: int = 1,
) -> PlayerInventory:
    return await repo.add_item(db, player_id, item_type, item_name, quantity)


# ---------------------------------------------------------------------------
# Tests: adding items to inventory
# ---------------------------------------------------------------------------


async def test_add_item_to_empty_inventory(
    db_session: AsyncSession,
    inventory_repo: InventoryRepository,
):
    """Adding an item to an empty inventory creates a new record with the correct quantity."""
    await _create_guild_config(db_session, guild_id=7001)
    user = await _create_user(db_session, user_id=500001)
    player = await _create_player(db_session, user_id=user.id, guild_id=7001)

    await _add_inventory_item(db_session, inventory_repo, player.id, "weapon", "Pulse Laser", quantity=3)

    items = await inventory_repo.get_player_items(db_session, player.id)
    assert len(items) == 1
    assert items[0].item_name == "Pulse Laser"
    assert items[0].quantity == 3


async def test_add_same_item_increases_quantity(
    db_session: AsyncSession,
    inventory_repo: InventoryRepository,
):
    """Adding the same item multiple times stacks the quantity rather than creating duplicates."""
    await _create_guild_config(db_session, guild_id=7002)
    user = await _create_user(db_session, user_id=500002)
    player = await _create_player(db_session, user_id=user.id, guild_id=7002)

    await _add_inventory_item(db_session, inventory_repo, player.id, "module", "Shield", quantity=2)
    await _add_inventory_item(db_session, inventory_repo, player.id, "module", "Shield", quantity=3)

    items = await inventory_repo.get_player_items(db_session, player.id, item_type="module")
    assert len(items) == 1
    assert items[0].quantity == 5


async def test_add_multiple_different_items(
    db_session: AsyncSession,
    inventory_repo: InventoryRepository,
):
    """Multiple different items can be added; each appears as a separate inventory entry."""
    await _create_guild_config(db_session, guild_id=7003)
    user = await _create_user(db_session, user_id=500003)
    player = await _create_player(db_session, user_id=user.id, guild_id=7003)

    await _add_inventory_item(db_session, inventory_repo, player.id, "weapon", "Blaster", quantity=1)
    await _add_inventory_item(db_session, inventory_repo, player.id, "module", "Thruster", quantity=2)
    await _add_inventory_item(db_session, inventory_repo, player.id, "turret", "Heavy Cannon", quantity=1)

    all_items = await inventory_repo.get_player_items(db_session, player.id)
    assert len(all_items) == 3
    names = {i.item_name for i in all_items}
    assert "Blaster" in names
    assert "Thruster" in names
    assert "Heavy Cannon" in names


# ---------------------------------------------------------------------------
# Tests: querying inventory
# ---------------------------------------------------------------------------


async def test_query_inventory_filtered_by_type(
    db_session: AsyncSession,
    inventory_repo: InventoryRepository,
):
    """Filtering by item_type returns only matching items for the player."""
    await _create_guild_config(db_session, guild_id=7004)
    user = await _create_user(db_session, user_id=500004)
    player = await _create_player(db_session, user_id=user.id, guild_id=7004)

    await _add_inventory_item(db_session, inventory_repo, player.id, "weapon", "Railgun")
    await _add_inventory_item(db_session, inventory_repo, player.id, "weapon", "Shotgun")
    await _add_inventory_item(db_session, inventory_repo, player.id, "module", "Armor Plate")

    weapons = await inventory_repo.get_player_items(db_session, player.id, item_type="weapon")
    assert len(weapons) == 2
    weapon_names = {w.item_name for w in weapons}
    assert "Railgun" in weapon_names
    assert "Shotgun" in weapon_names

    modules = await inventory_repo.get_player_items(db_session, player.id, item_type="module")
    assert len(modules) == 1
    assert modules[0].item_name == "Armor Plate"


async def test_get_specific_inventory_item(
    db_session: AsyncSession,
    inventory_repo: InventoryRepository,
):
    """A specific item can be retrieved by player_id, item_type, and item_name."""
    await _create_guild_config(db_session, guild_id=7005)
    user = await _create_user(db_session, user_id=500005)
    player = await _create_player(db_session, user_id=user.id, guild_id=7005)

    await _add_inventory_item(db_session, inventory_repo, player.id, "weapon", "Ion Disruptor", quantity=4)

    item = await inventory_repo.get_player_item(db_session, player.id, "weapon", "Ion Disruptor")

    assert item is not None
    assert item.item_name == "Ion Disruptor"
    assert item.quantity == 4


async def test_inventory_summary_by_type(
    db_session: AsyncSession,
    inventory_repo: InventoryRepository,
):
    """The inventory summary correctly aggregates quantities per item type."""
    await _create_guild_config(db_session, guild_id=7006)
    user = await _create_user(db_session, user_id=500006)
    player = await _create_player(db_session, user_id=user.id, guild_id=7006)

    await _add_inventory_item(db_session, inventory_repo, player.id, "weapon", "Blaster Mk1", quantity=2)
    await _add_inventory_item(db_session, inventory_repo, player.id, "weapon", "Blaster Mk2", quantity=1)
    await _add_inventory_item(db_session, inventory_repo, player.id, "module", "Hull Plating", quantity=3)

    summary = await inventory_repo.get_inventory_summary(db_session, player.id)

    assert summary["weapon"] == 3  # 2 + 1
    assert summary["module"] == 3
    assert summary["total_items"] == 6


# ---------------------------------------------------------------------------
# Tests: removing items from inventory
# ---------------------------------------------------------------------------


async def test_remove_partial_item_quantity(
    db_session: AsyncSession,
    inventory_repo: InventoryRepository,
):
    """Removing a partial quantity decrements the item count; the record persists."""
    await _create_guild_config(db_session, guild_id=7007)
    user = await _create_user(db_session, user_id=500007)
    player = await _create_player(db_session, user_id=user.id, guild_id=7007)

    await _add_inventory_item(db_session, inventory_repo, player.id, "weapon", "Scatter Cannon", quantity=5)

    await inventory_repo.remove_item(db_session, player.id, "weapon", "Scatter Cannon", quantity=2)

    item = await inventory_repo.get_player_item(db_session, player.id, "weapon", "Scatter Cannon")
    assert item is not None
    assert item.quantity == 3


async def test_remove_all_of_item_deletes_record(
    db_session: AsyncSession,
    inventory_repo: InventoryRepository,
):
    """Removing the entire quantity of an item deletes its inventory record."""
    await _create_guild_config(db_session, guild_id=7008)
    user = await _create_user(db_session, user_id=500008)
    player = await _create_player(db_session, user_id=user.id, guild_id=7008)

    await _add_inventory_item(db_session, inventory_repo, player.id, "turret", "Turret X", quantity=2)

    await inventory_repo.remove_item(db_session, player.id, "turret", "Turret X", quantity=2)

    item = await inventory_repo.get_player_item(db_session, player.id, "turret", "Turret X")
    assert item is None

    all_items = await inventory_repo.get_player_items(db_session, player.id)
    assert len(all_items) == 0


async def test_remove_item_not_in_inventory_raises_error(
    db_session: AsyncSession,
    inventory_repo: InventoryRepository,
):
    """Attempting to remove an item that doesn't exist raises a ValueError."""
    await _create_guild_config(db_session, guild_id=7009)
    user = await _create_user(db_session, user_id=500009)
    player = await _create_player(db_session, user_id=user.id, guild_id=7009)

    with pytest.raises(ValueError, match="not found"):
        await inventory_repo.remove_item(db_session, player.id, "weapon", "Non Existent Weapon", quantity=1)


async def test_clear_player_inventory(
    db_session: AsyncSession,
    inventory_repo: InventoryRepository,
):
    """Clearing a player's inventory removes all records and returns the count deleted."""
    await _create_guild_config(db_session, guild_id=7010)
    user = await _create_user(db_session, user_id=500010)
    player = await _create_player(db_session, user_id=user.id, guild_id=7010)

    await _add_inventory_item(db_session, inventory_repo, player.id, "weapon", "Gun A", quantity=1)
    await _add_inventory_item(db_session, inventory_repo, player.id, "module", "Module B", quantity=2)

    deleted_count = await inventory_repo.clear_player_inventory(db_session, player.id)

    assert deleted_count == 2

    remaining = await inventory_repo.get_player_items(db_session, player.id)
    assert len(remaining) == 0


# ---------------------------------------------------------------------------
# Tests: update quantity directly
# ---------------------------------------------------------------------------


async def test_update_quantity_persists(
    db_session: AsyncSession,
    inventory_repo: InventoryRepository,
):
    """Directly updating an inventory item quantity persists the new value."""
    await _create_guild_config(db_session, guild_id=7011)
    user = await _create_user(db_session, user_id=500011)
    player = await _create_player(db_session, user_id=user.id, guild_id=7011)

    item = await _add_inventory_item(db_session, inventory_repo, player.id, "weapon", "Pulse Rifle", quantity=1)

    updated = await inventory_repo.update_quantity(db_session, item.id, 10)

    assert updated.quantity == 10

    fetched = await inventory_repo.get_by_id(db_session, item.id)
    assert fetched.quantity == 10
