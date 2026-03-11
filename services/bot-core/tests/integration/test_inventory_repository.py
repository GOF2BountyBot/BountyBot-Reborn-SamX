"""Integration tests for InventoryRepository using SQLite in-memory database."""

import pytest
from persist.models.player import Player
from persist.models.player_inventory import PlayerInventory
from persist.models.user import User
from persist.repositories.inventory_repository import InventoryRepository
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def repo() -> InventoryRepository:
    return InventoryRepository()


async def _setup_player(db: AsyncSession, user_id: int = 1, guild_id: int = 1000) -> Player:
    """Create a user and player, returning the player."""
    user = User(id=user_id, discord_username=f"user{user_id}")
    db.add(user)
    await db.commit()

    player = Player(user_id=user_id, guild_id=guild_id, credits=100)
    db.add(player)
    await db.commit()
    await db.refresh(player)
    return player


# -- get_by_id -----------------------------------------------------------------


async def test_get_by_id(db_session: AsyncSession, repo: InventoryRepository):
    player = await _setup_player(db_session)
    item = PlayerInventory(player_id=player.id, item_type="weapon", item_name="Laser", quantity=2)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)

    result = await repo.get_by_id(db_session, item.id)

    assert result is not None
    assert result.item_name == "Laser"
    assert result.quantity == 2


async def test_get_by_id_not_found(db_session: AsyncSession, repo: InventoryRepository):
    result = await repo.get_by_id(db_session, 999)
    assert result is None


# -- get_by_name ---------------------------------------------------------------


async def test_get_by_name_raises(db_session: AsyncSession, repo: InventoryRepository):
    with pytest.raises(NotImplementedError):
        await repo.get_by_name(db_session, "anything")


# -- list_all ------------------------------------------------------------------


async def test_list_all(db_session: AsyncSession, repo: InventoryRepository):
    player = await _setup_player(db_session)
    db_session.add(PlayerInventory(player_id=player.id, item_type="weapon", item_name="A", quantity=1))
    db_session.add(PlayerInventory(player_id=player.id, item_type="module", item_name="B", quantity=1))
    await db_session.commit()

    items = await repo.list_all(db_session)
    assert len(items) == 2


# -- add -----------------------------------------------------------------------


async def test_add_persists_item(db_session: AsyncSession, repo: InventoryRepository):
    player = await _setup_player(db_session)
    item = PlayerInventory(player_id=player.id, item_type="ship", item_name="Falcon", quantity=1)
    result = await repo.add(db_session, item)

    assert result.id is not None
    assert result.item_name == "Falcon"

    fetched = await repo.get_by_id(db_session, result.id)
    assert fetched is not None


# -- remove --------------------------------------------------------------------


async def test_remove_deletes_item(db_session: AsyncSession, repo: InventoryRepository):
    player = await _setup_player(db_session)
    item = PlayerInventory(player_id=player.id, item_type="weapon", item_name="Plasma", quantity=1)
    await repo.add(db_session, item)

    await repo.remove(db_session, item)

    result = await repo.get_by_id(db_session, item.id)
    assert result is None


# -- create_or_update ----------------------------------------------------------


async def test_create_or_update_creates_new(db_session: AsyncSession, repo: InventoryRepository):
    player = await _setup_player(db_session)

    result = await repo.create_or_update(db_session, {
        "player_id": player.id,
        "item_type": "weapon",
        "item_name": "Blaster",
        "quantity": 3,
    })

    assert result.item_name == "Blaster"
    assert result.quantity == 3


async def test_create_or_update_adds_quantity_to_existing(db_session: AsyncSession, repo: InventoryRepository):
    player = await _setup_player(db_session)
    await repo.create_or_update(db_session, {
        "player_id": player.id,
        "item_type": "weapon",
        "item_name": "Blaster",
        "quantity": 3,
    })

    result = await repo.create_or_update(db_session, {
        "player_id": player.id,
        "item_type": "weapon",
        "item_name": "Blaster",
        "quantity": 2,
    })

    assert result.quantity == 5  # 3 + 2


async def test_create_or_update_raises_without_required(db_session: AsyncSession, repo: InventoryRepository):
    with pytest.raises(ValueError, match="player_id, item_type, and item_name are required"):
        await repo.create_or_update(db_session, {"player_id": 1})


# -- get_player_items ----------------------------------------------------------


async def test_get_player_items_all(db_session: AsyncSession, repo: InventoryRepository):
    player = await _setup_player(db_session)
    db_session.add(PlayerInventory(player_id=player.id, item_type="weapon", item_name="A", quantity=1))
    db_session.add(PlayerInventory(player_id=player.id, item_type="module", item_name="B", quantity=1))
    await db_session.commit()

    items = await repo.get_player_items(db_session, player.id)
    assert len(items) == 2


async def test_get_player_items_by_type(db_session: AsyncSession, repo: InventoryRepository):
    player = await _setup_player(db_session)
    db_session.add(PlayerInventory(player_id=player.id, item_type="weapon", item_name="A", quantity=1))
    db_session.add(PlayerInventory(player_id=player.id, item_type="module", item_name="B", quantity=1))
    await db_session.commit()

    weapons = await repo.get_player_items(db_session, player.id, item_type="weapon")
    assert len(weapons) == 1
    assert weapons[0].item_name == "A"


# -- get_player_item -----------------------------------------------------------


async def test_get_player_item_found(db_session: AsyncSession, repo: InventoryRepository):
    player = await _setup_player(db_session)
    db_session.add(PlayerInventory(player_id=player.id, item_type="weapon", item_name="Laser", quantity=5))
    await db_session.commit()

    result = await repo.get_player_item(db_session, player.id, "weapon", "Laser")

    assert result is not None
    assert result.quantity == 5


async def test_get_player_item_not_found(db_session: AsyncSession, repo: InventoryRepository):
    player = await _setup_player(db_session)
    result = await repo.get_player_item(db_session, player.id, "weapon", "Nonexistent")
    assert result is None


# -- add_item ------------------------------------------------------------------


async def test_add_item_creates_new(db_session: AsyncSession, repo: InventoryRepository):
    player = await _setup_player(db_session)

    result = await repo.add_item(db_session, player.id, "weapon", "Missile", 3)

    assert result.item_name == "Missile"
    assert result.quantity == 3


async def test_add_item_increases_existing(db_session: AsyncSession, repo: InventoryRepository):
    player = await _setup_player(db_session)
    await repo.add_item(db_session, player.id, "weapon", "Missile", 3)

    result = await repo.add_item(db_session, player.id, "weapon", "Missile", 2)

    assert result.quantity == 5  # 3 + 2


async def test_add_item_zero_quantity_raises(db_session: AsyncSession, repo: InventoryRepository):
    player = await _setup_player(db_session)

    with pytest.raises(ValueError, match="Quantity must be positive"):
        await repo.add_item(db_session, player.id, "weapon", "X", 0)


# -- remove_item ---------------------------------------------------------------


async def test_remove_item_reduces_quantity(db_session: AsyncSession, repo: InventoryRepository):
    player = await _setup_player(db_session)
    await repo.add_item(db_session, player.id, "weapon", "Rocket", 5)

    await repo.remove_item(db_session, player.id, "weapon", "Rocket", 2)

    item = await repo.get_player_item(db_session, player.id, "weapon", "Rocket")
    assert item is not None
    assert item.quantity == 3


async def test_remove_item_removes_entirely_when_zero(db_session: AsyncSession, repo: InventoryRepository):
    player = await _setup_player(db_session)
    await repo.add_item(db_session, player.id, "weapon", "Rocket", 3)

    await repo.remove_item(db_session, player.id, "weapon", "Rocket", 3)

    item = await repo.get_player_item(db_session, player.id, "weapon", "Rocket")
    assert item is None


async def test_remove_item_insufficient_raises(db_session: AsyncSession, repo: InventoryRepository):
    player = await _setup_player(db_session)
    await repo.add_item(db_session, player.id, "weapon", "Rocket", 2)

    with pytest.raises(ValueError, match="Insufficient quantity"):
        await repo.remove_item(db_session, player.id, "weapon", "Rocket", 5)


async def test_remove_item_not_found_raises(db_session: AsyncSession, repo: InventoryRepository):
    player = await _setup_player(db_session)

    with pytest.raises(ValueError, match="not found"):
        await repo.remove_item(db_session, player.id, "weapon", "Ghost", 1)


# -- update_quantity -----------------------------------------------------------


async def test_update_quantity(db_session: AsyncSession, repo: InventoryRepository):
    player = await _setup_player(db_session)
    item = PlayerInventory(player_id=player.id, item_type="weapon", item_name="Rail", quantity=1)
    await repo.add(db_session, item)

    result = await repo.update_quantity(db_session, item.id, 10)

    assert result.quantity == 10


async def test_update_quantity_negative_raises(db_session: AsyncSession, repo: InventoryRepository):
    player = await _setup_player(db_session)
    item = PlayerInventory(player_id=player.id, item_type="weapon", item_name="Rail", quantity=1)
    await repo.add(db_session, item)

    with pytest.raises(ValueError, match="Quantity cannot be negative"):
        await repo.update_quantity(db_session, item.id, -1)


# -- get_item_count_by_type ----------------------------------------------------


async def test_get_item_count_by_type(db_session: AsyncSession, repo: InventoryRepository):
    player = await _setup_player(db_session)
    db_session.add(PlayerInventory(player_id=player.id, item_type="weapon", item_name="A", quantity=3))
    db_session.add(PlayerInventory(player_id=player.id, item_type="weapon", item_name="B", quantity=2))
    db_session.add(PlayerInventory(player_id=player.id, item_type="module", item_name="C", quantity=4))
    await db_session.commit()

    weapon_count = await repo.get_item_count_by_type(db_session, player.id, "weapon")
    assert weapon_count == 5  # 3 + 2

    module_count = await repo.get_item_count_by_type(db_session, player.id, "module")
    assert module_count == 4


# -- get_inventory_summary -----------------------------------------------------


async def test_get_inventory_summary(db_session: AsyncSession, repo: InventoryRepository):
    player = await _setup_player(db_session)
    db_session.add(PlayerInventory(player_id=player.id, item_type="weapon", item_name="A", quantity=3))
    db_session.add(PlayerInventory(player_id=player.id, item_type="module", item_name="B", quantity=2))
    db_session.add(PlayerInventory(player_id=player.id, item_type="ship", item_name="C", quantity=1))
    await db_session.commit()

    summary = await repo.get_inventory_summary(db_session, player.id)

    assert summary["weapon"] == 3
    assert summary["module"] == 2
    assert summary["ship"] == 1
    assert summary["turret"] == 0
    assert summary["total_items"] == 6  # 3 + 2 + 1


async def test_get_inventory_summary_empty(db_session: AsyncSession, repo: InventoryRepository):
    player = await _setup_player(db_session)

    summary = await repo.get_inventory_summary(db_session, player.id)

    assert summary["total_items"] == 0
