"""Integration tests for PlayerRepository using SQLite in-memory database."""

import pytest
from persist.models.player import Player
from persist.models.user import User
from persist.repositories.player_repository import PlayerRepository
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def repo() -> PlayerRepository:
    return PlayerRepository()


async def _create_user(db: AsyncSession, user_id: int = 1, username: str = "testuser") -> User:
    """Helper to create a user record (required FK for players)."""
    user = User(id=user_id, discord_username=username)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _create_player(
    db: AsyncSession,
    repo: PlayerRepository,
    user_id: int,
    guild_id: int,
    credits: int = 100,
) -> Player:
    """Helper to create a player record."""
    player = Player(user_id=user_id, guild_id=guild_id, credits=credits)
    return await repo.add(db, player)


# -- get_by_id -----------------------------------------------------------------


async def test_get_by_id_returns_player(db_session: AsyncSession, repo: PlayerRepository):
    await _create_user(db_session, 1)
    player = await _create_player(db_session, repo, user_id=1, guild_id=1000)

    result = await repo.get_by_id(db_session, player.id)

    assert result is not None
    assert result.user_id == 1
    assert result.guild_id == 1000


async def test_get_by_id_returns_none_for_missing(db_session: AsyncSession, repo: PlayerRepository):
    result = await repo.get_by_id(db_session, 999)
    assert result is None


# -- get_by_name ---------------------------------------------------------------


async def test_get_by_name_raises_not_implemented(db_session: AsyncSession, repo: PlayerRepository):
    with pytest.raises(NotImplementedError):
        await repo.get_by_name(db_session, "anything")


# -- list_all ------------------------------------------------------------------


async def test_list_all(db_session: AsyncSession, repo: PlayerRepository):
    await _create_user(db_session, 1)
    await _create_player(db_session, repo, user_id=1, guild_id=100)
    await _create_player(db_session, repo, user_id=1, guild_id=200)

    players = await repo.list_all(db_session)
    assert len(players) == 2


async def test_list_all_empty(db_session: AsyncSession, repo: PlayerRepository):
    players = await repo.list_all(db_session)
    assert players == []


# -- add -----------------------------------------------------------------------


async def test_add_persists_player(db_session: AsyncSession, repo: PlayerRepository):
    await _create_user(db_session, 1)
    player = await _create_player(db_session, repo, user_id=1, guild_id=500, credits=250)

    assert player.id is not None
    assert player.credits == 250
    assert player.tier == "Bronze"
    assert player.xp == 0

    fetched = await repo.get_by_id(db_session, player.id)
    assert fetched is not None
    assert fetched.credits == 250


# -- remove --------------------------------------------------------------------


async def test_remove_deletes_player(db_session: AsyncSession, repo: PlayerRepository):
    await _create_user(db_session, 1)
    player = await _create_player(db_session, repo, user_id=1, guild_id=500)

    await repo.remove(db_session, player)

    result = await repo.get_by_id(db_session, player.id)
    assert result is None


# -- create_or_update ----------------------------------------------------------


async def test_create_or_update_creates_new(db_session: AsyncSession, repo: PlayerRepository):
    await _create_user(db_session, 10)

    player = await repo.create_or_update(db_session, {
        "user_id": 10,
        "guild_id": 3000,
        "credits": 500,
    })

    assert player.user_id == 10
    assert player.guild_id == 3000
    assert player.credits == 500


async def test_create_or_update_updates_existing(db_session: AsyncSession, repo: PlayerRepository):
    await _create_user(db_session, 10)
    await _create_player(db_session, repo, user_id=10, guild_id=3000, credits=100)

    player = await repo.create_or_update(db_session, {
        "user_id": 10,
        "guild_id": 3000,
        "credits": 999,
    })

    assert player.credits == 999


async def test_create_or_update_raises_without_required_fields(db_session: AsyncSession, repo: PlayerRepository):
    with pytest.raises(ValueError, match="Both user_id and guild_id are required"):
        await repo.create_or_update(db_session, {"user_id": 1})


# -- get_by_user_and_guild -----------------------------------------------------


async def test_get_by_user_and_guild_found(db_session: AsyncSession, repo: PlayerRepository):
    await _create_user(db_session, 20)
    await _create_player(db_session, repo, user_id=20, guild_id=4000)

    result = await repo.get_by_user_and_guild(db_session, user_id=20, guild_id=4000)

    assert result is not None
    assert result.user_id == 20
    assert result.guild_id == 4000


async def test_get_by_user_and_guild_not_found(db_session: AsyncSession, repo: PlayerRepository):
    result = await repo.get_by_user_and_guild(db_session, user_id=99, guild_id=99)
    assert result is None


# -- get_players_by_guild ------------------------------------------------------


async def test_get_players_by_guild(db_session: AsyncSession, repo: PlayerRepository):
    await _create_user(db_session, 30, "user30")
    await _create_user(db_session, 31, "user31")
    await _create_player(db_session, repo, user_id=30, guild_id=5000)
    await _create_player(db_session, repo, user_id=31, guild_id=5000)
    await _create_player(db_session, repo, user_id=30, guild_id=6000)  # different guild

    players = await repo.get_players_by_guild(db_session, guild_id=5000)

    assert len(players) == 2
    user_ids = {p.user_id for p in players}
    assert user_ids == {30, 31}


# -- get_players_by_user ------------------------------------------------------


async def test_get_players_by_user(db_session: AsyncSession, repo: PlayerRepository):
    await _create_user(db_session, 40)
    await _create_player(db_session, repo, user_id=40, guild_id=7000)
    await _create_player(db_session, repo, user_id=40, guild_id=8000)

    players = await repo.get_players_by_user(db_session, user_id=40)

    assert len(players) == 2
    guild_ids = {p.guild_id for p in players}
    assert guild_ids == {7000, 8000}


# -- update_credits ------------------------------------------------------------


async def test_update_credits(db_session: AsyncSession, repo: PlayerRepository):
    """update_credits should correctly update the player's credits value."""
    await _create_user(db_session, 50)
    player = await _create_player(db_session, repo, user_id=50, guild_id=9000, credits=100)

    result = await repo.update_credits(db_session, player.id, 500)

    assert result is not None
    assert result.credits == 500


# -- update_xp ----------------------------------------------------------------


async def test_update_xp(db_session: AsyncSession, repo: PlayerRepository):
    await _create_user(db_session, 60)
    player = await _create_player(db_session, repo, user_id=60, guild_id=10000)

    result = await repo.update_xp(db_session, player.id, 1500)

    assert result is not None
    assert result.xp == 1500


# -- update_tier ---------------------------------------------------------------


async def test_update_tier(db_session: AsyncSession, repo: PlayerRepository):
    await _create_user(db_session, 70)
    player = await _create_player(db_session, repo, user_id=70, guild_id=11000)

    result = await repo.update_tier(db_session, player.id, "Gold")

    assert result is not None
    assert result.tier == "Gold"


async def test_update_tier_invalid(db_session: AsyncSession, repo: PlayerRepository):
    await _create_user(db_session, 71)
    player = await _create_player(db_session, repo, user_id=71, guild_id=11001)

    with pytest.raises(ValueError, match="Invalid tier"):
        await repo.update_tier(db_session, player.id, "Diamond")


# -- update_active_ship --------------------------------------------------------


async def test_update_active_ship(db_session: AsyncSession, repo: PlayerRepository):
    await _create_user(db_session, 80)
    player = await _create_player(db_session, repo, user_id=80, guild_id=12000)

    # Set to None (no active ship)
    result = await repo.update_active_ship(db_session, player.id, None)

    assert result is not None
    assert result.active_ship_id is None
