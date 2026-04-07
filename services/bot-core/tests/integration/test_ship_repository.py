"""Integration tests for PlayerShipRepository using SQLite in-memory database."""

import pytest
from persist.models.player import Player
from persist.models.player_ship import PlayerShip
from persist.models.user import User
from persist.repositories.player_ship_repository import PlayerShipRepository
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def repo() -> PlayerShipRepository:
    return PlayerShipRepository()


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


async def _add_ship(
    db: AsyncSession,
    repo: PlayerShipRepository,
    player_id: int,
    ship_name: str = "Falcon",
    nickname: str | None = None,
    is_active: bool = False,
) -> PlayerShip:
    ship = PlayerShip(
        player_id=player_id,
        ship_name=ship_name,
        nickname=nickname,
        is_active=is_active,
    )
    return await repo.add(db, ship)


# -- get_by_id -----------------------------------------------------------------


async def test_get_by_id(db_session: AsyncSession, repo: PlayerShipRepository):
    player = await _setup_player(db_session)
    ship = await _add_ship(db_session, repo, player.id)

    result = await repo.get_by_id(db_session, ship.id)

    assert result is not None
    assert result.ship_name == "Falcon"


async def test_get_by_id_not_found(db_session: AsyncSession, repo: PlayerShipRepository):
    result = await repo.get_by_id(db_session, 999)
    assert result is None


# -- get_by_name ---------------------------------------------------------------


async def test_get_by_name_raises(db_session: AsyncSession, repo: PlayerShipRepository):
    with pytest.raises(NotImplementedError):
        await repo.get_by_name(db_session, "anything")


# -- list_all ------------------------------------------------------------------


async def test_list_all(db_session: AsyncSession, repo: PlayerShipRepository):
    player = await _setup_player(db_session)
    await _add_ship(db_session, repo, player.id, "Ship1")
    await _add_ship(db_session, repo, player.id, "Ship2")

    ships = await repo.list_all(db_session)
    assert len(ships) == 2


# -- add -----------------------------------------------------------------------


async def test_add_persists_ship(db_session: AsyncSession, repo: PlayerShipRepository):
    player = await _setup_player(db_session)
    ship = await _add_ship(db_session, repo, player.id, "Eagle", nickname="MyEagle")

    assert ship.id is not None
    assert ship.ship_name == "Eagle"
    assert ship.nickname == "MyEagle"
    assert ship.is_active is False


# -- remove --------------------------------------------------------------------


async def test_remove_deletes_ship(db_session: AsyncSession, repo: PlayerShipRepository):
    player = await _setup_player(db_session)
    ship = await _add_ship(db_session, repo, player.id)

    await repo.remove(db_session, ship)

    result = await repo.get_by_id(db_session, ship.id)
    assert result is None


# -- create_or_update ----------------------------------------------------------


async def test_create_or_update_creates_new(db_session: AsyncSession, repo: PlayerShipRepository):
    player = await _setup_player(db_session)

    ship = await repo.create_or_update(
        db_session,
        {
            "player_id": player.id,
            "ship_name": "Cobra",
        },
    )

    assert ship.ship_name == "Cobra"
    assert ship.id is not None


async def test_create_or_update_updates_existing(db_session: AsyncSession, repo: PlayerShipRepository):
    player = await _setup_player(db_session)
    original = await _add_ship(db_session, repo, player.id, "Cobra")

    ship = await repo.create_or_update(
        db_session,
        {
            "id": original.id,
            "player_id": player.id,
            "ship_name": "Cobra",
            "nickname": "Striker",
        },
    )

    assert ship.nickname == "Striker"


async def test_create_or_update_raises_without_required(db_session: AsyncSession, repo: PlayerShipRepository):
    with pytest.raises(ValueError, match="player_id and ship_name are required"):
        await repo.create_or_update(db_session, {"player_id": 1})


# -- get_player_ships ----------------------------------------------------------


async def test_get_player_ships(db_session: AsyncSession, repo: PlayerShipRepository):
    player = await _setup_player(db_session)
    await _add_ship(db_session, repo, player.id, "Ship1")
    await _add_ship(db_session, repo, player.id, "Ship2")

    ships = await repo.get_player_ships(db_session, player.id)
    assert len(ships) == 2


async def test_get_player_ships_empty(db_session: AsyncSession, repo: PlayerShipRepository):
    player = await _setup_player(db_session)

    ships = await repo.get_player_ships(db_session, player.id)
    assert ships == []


# -- get_active_ship -----------------------------------------------------------


async def test_get_active_ship(db_session: AsyncSession, repo: PlayerShipRepository):
    player = await _setup_player(db_session)
    await _add_ship(db_session, repo, player.id, "Inactive", is_active=False)
    active = await _add_ship(db_session, repo, player.id, "Active", is_active=True)

    result = await repo.get_active_ship(db_session, player.id)

    assert result is not None
    assert result.id == active.id
    assert result.ship_name == "Active"


async def test_get_active_ship_none(db_session: AsyncSession, repo: PlayerShipRepository):
    player = await _setup_player(db_session)
    await _add_ship(db_session, repo, player.id, "Inactive", is_active=False)

    result = await repo.get_active_ship(db_session, player.id)
    assert result is None


# -- set_active_ship -----------------------------------------------------------


async def test_set_active_ship(db_session: AsyncSession, repo: PlayerShipRepository):
    player = await _setup_player(db_session)
    ship1 = await _add_ship(db_session, repo, player.id, "Ship1", is_active=True)
    ship2 = await _add_ship(db_session, repo, player.id, "Ship2", is_active=False)

    result = await repo.set_active_ship(db_session, player.id, ship2.id)

    assert result.is_active is True

    # ship1 should now be inactive
    refreshed_ship1 = await repo.get_by_id(db_session, ship1.id)
    assert refreshed_ship1.is_active is False


async def test_set_active_ship_wrong_player(db_session: AsyncSession, repo: PlayerShipRepository):
    player1 = await _setup_player(db_session, user_id=1, guild_id=1000)
    player2 = await _setup_player(db_session, user_id=2, guild_id=2000)
    ship = await _add_ship(db_session, repo, player1.id)

    with pytest.raises(ValueError, match="not found or doesn't belong"):
        await repo.set_active_ship(db_session, player2.id, ship.id)


# -- update_loadout ------------------------------------------------------------


async def test_update_loadout(db_session: AsyncSession, repo: PlayerShipRepository):
    player = await _setup_player(db_session)
    ship = await _add_ship(db_session, repo, player.id)

    result = await repo.update_loadout(
        db_session,
        ship.id,
        {
            "weapons": ["Laser", "Missile"],
            "modules": ["Shield"],
            "turrets": ["AutoTurret"],
        },
    )

    assert result.weapons == ["Laser", "Missile"]
    assert result.modules == ["Shield"]
    assert result.turrets == ["AutoTurret"]


async def test_update_loadout_partial(db_session: AsyncSession, repo: PlayerShipRepository):
    player = await _setup_player(db_session)
    ship = await _add_ship(db_session, repo, player.id)
    await repo.update_loadout(db_session, ship.id, {"weapons": ["Laser"]})

    # Update only modules, weapons should remain
    result = await repo.update_loadout(db_session, ship.id, {"modules": ["Armor"]})

    assert result.weapons == ["Laser"]
    assert result.modules == ["Armor"]


async def test_update_loadout_ship_not_found(db_session: AsyncSession, repo: PlayerShipRepository):
    with pytest.raises(ValueError, match=r"Ship .* not found"):
        await repo.update_loadout(db_session, 999, {"weapons": []})


# -- add_equipment -------------------------------------------------------------


async def test_add_equipment(db_session: AsyncSession, repo: PlayerShipRepository):
    player = await _setup_player(db_session)
    ship = await _add_ship(db_session, repo, player.id)

    await repo.add_equipment(db_session, ship.id, "weapons", "Laser")

    refreshed = await repo.get_by_id(db_session, ship.id)
    assert "Laser" in refreshed.weapons


async def test_add_equipment_invalid_type(db_session: AsyncSession, repo: PlayerShipRepository):
    player = await _setup_player(db_session)
    ship = await _add_ship(db_session, repo, player.id)

    with pytest.raises(ValueError, match="Invalid equipment type"):
        await repo.add_equipment(db_session, ship.id, "shields", "XShield")


# -- remove_equipment ----------------------------------------------------------


async def test_remove_equipment(db_session: AsyncSession, repo: PlayerShipRepository):
    player = await _setup_player(db_session)
    ship = await _add_ship(db_session, repo, player.id)
    await repo.update_loadout(db_session, ship.id, {"weapons": ["Laser", "Missile"]})

    await repo.remove_equipment(db_session, ship.id, "weapons", "Laser")

    refreshed = await repo.get_by_id(db_session, ship.id)
    assert refreshed.weapons == ["Missile"]


async def test_remove_equipment_not_equipped(db_session: AsyncSession, repo: PlayerShipRepository):
    player = await _setup_player(db_session)
    ship = await _add_ship(db_session, repo, player.id)
    await repo.update_loadout(db_session, ship.id, {"weapons": ["Laser"]})

    with pytest.raises(ValueError, match="not equipped"):
        await repo.remove_equipment(db_session, ship.id, "weapons", "Ghost")


# -- update_nickname -----------------------------------------------------------


async def test_update_nickname(db_session: AsyncSession, repo: PlayerShipRepository):
    player = await _setup_player(db_session)
    ship = await _add_ship(db_session, repo, player.id, "Falcon")

    result = await repo.update_nickname(db_session, ship.id, "SpeedDemon")

    assert result.nickname == "SpeedDemon"


async def test_update_nickname_ship_not_found(db_session: AsyncSession, repo: PlayerShipRepository):
    with pytest.raises(ValueError, match=r"Ship .* not found"):
        await repo.update_nickname(db_session, 999, "Name")


# -- get_ships_by_name ---------------------------------------------------------


async def test_get_ships_by_name(db_session: AsyncSession, repo: PlayerShipRepository):
    player = await _setup_player(db_session)
    await _add_ship(db_session, repo, player.id, "Falcon")
    await _add_ship(db_session, repo, player.id, "Falcon")  # second copy
    await _add_ship(db_session, repo, player.id, "Eagle")

    falcons = await repo.get_ships_by_name(db_session, player.id, "Falcon")
    assert len(falcons) == 2

    eagles = await repo.get_ships_by_name(db_session, player.id, "Eagle")
    assert len(eagles) == 1


# -- get_ship_loadout_summary --------------------------------------------------


async def test_get_ship_loadout_summary(db_session: AsyncSession, repo: PlayerShipRepository):
    player = await _setup_player(db_session)
    ship = await _add_ship(db_session, repo, player.id, "Falcon", nickname="Beast")
    await repo.update_loadout(
        db_session,
        ship.id,
        {
            "weapons": ["Laser", "Missile"],
            "modules": ["Shield"],
            "turrets": [],
        },
    )

    summary = await repo.get_ship_loadout_summary(db_session, ship.id)

    assert summary["ship_name"] == "Falcon"
    assert summary["nickname"] == "Beast"
    assert summary["weapons_count"] == 2
    assert summary["modules_count"] == 1
    assert summary["turrets_count"] == 0


async def test_get_ship_loadout_summary_not_found(db_session: AsyncSession, repo: PlayerShipRepository):
    with pytest.raises(ValueError, match=r"Ship .* not found"):
        await repo.get_ship_loadout_summary(db_session, 999)
