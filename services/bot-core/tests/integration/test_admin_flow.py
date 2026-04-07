"""
Integration tests for admin workflow using a real SQLite in-memory database.

Tests the end-to-end admin path:
  create guild config → register user/player → query data → update config
"""

import pytest
from persist.models.guild_config import GuildConfig
from persist.models.player import Player
from persist.models.user import User
from persist.repositories.config_repository import ConfigRepository
from persist.repositories.player_repository import PlayerRepository
from persist.repositories.user_repository import UserRepository
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config_repo() -> ConfigRepository:
    return ConfigRepository()


@pytest.fixture
def player_repo() -> PlayerRepository:
    return PlayerRepository()


@pytest.fixture
def user_repo() -> UserRepository:
    return UserRepository()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _create_guild_config(
    db: AsyncSession,
    guild_id: int = 8001,
    starting_credits: int = 500,
    admin_role_id: int | None = None,
) -> GuildConfig:
    config = GuildConfig(
        guild_id=guild_id,
        starting_credits=starting_credits,
        admin_role_id=admin_role_id,
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return config


async def _create_user(db: AsyncSession, user_id: int, username: str = "test_user") -> User:
    user = User(id=user_id, discord_username=username)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _create_player(
    db: AsyncSession,
    user_id: int,
    guild_id: int,
    credits: int = 500,
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


# ---------------------------------------------------------------------------
# Tests: guild config management
# ---------------------------------------------------------------------------


async def test_create_guild_config(
    db_session: AsyncSession,
    config_repo: ConfigRepository,
):
    """A guild config can be created and retrieved by guild_id."""
    _config = await _create_guild_config(db_session, guild_id=8001, starting_credits=750)

    result = await config_repo.get_by_guild_id(db_session, guild_id=8001)

    assert result is not None
    assert result.guild_id == 8001
    assert result.starting_credits == 750


async def test_guild_config_defaults_are_correct(
    db_session: AsyncSession,
    config_repo: ConfigRepository,
):
    """A newly created guild config has sensible default values."""
    _config = await _create_guild_config(db_session, guild_id=8002)

    result = await config_repo.get_by_guild_id(db_session, guild_id=8002)

    assert result is not None
    assert result.admin_role_id is None
    assert result.sale_price_factor == pytest.approx(0.8)
    assert "Silver" in result.xp_thresholds
    assert "Gold" in result.xp_thresholds
    assert "Platinum" in result.xp_thresholds


async def test_create_default_config_via_repo(
    db_session: AsyncSession,
    config_repo: ConfigRepository,
):
    """ConfigRepository.create_default_config produces a complete, queryable config."""
    _config = await config_repo.create_default_config(db_session, guild_id=8003)

    fetched = await config_repo.get_by_guild_id(db_session, guild_id=8003)

    assert fetched is not None
    assert fetched.guild_id == 8003
    assert fetched.ship_count_range == {"min": 3, "max": 5}
    assert fetched.weapon_count_range == {"min": 3, "max": 5}


async def test_update_guild_config_admin_role(
    db_session: AsyncSession,
    config_repo: ConfigRepository,
):
    """Updating a guild config admin_role_id persists correctly."""
    _config = await _create_guild_config(db_session, guild_id=8004, admin_role_id=None)

    # Update admin role via create_or_update
    updated = await config_repo.create_or_update(db_session, {"guild_id": 8004, "admin_role_id": 99999})

    assert updated.admin_role_id == 99999

    # Confirm persisted
    fetched = await config_repo.get_by_guild_id(db_session, guild_id=8004)
    assert fetched.admin_role_id == 99999


# ---------------------------------------------------------------------------
# Tests: player data in a guild
# ---------------------------------------------------------------------------


async def test_add_player_data(
    db_session: AsyncSession,
    player_repo: PlayerRepository,
):
    """A player can be added to a guild and retrieved by user+guild combination."""
    await _create_guild_config(db_session, guild_id=8005)
    user = await _create_user(db_session, user_id=300001, username="admin_tester")
    _player = await _create_player(db_session, user_id=user.id, guild_id=8005, credits=1500)

    result = await player_repo.get_by_user_and_guild(db_session, user_id=user.id, guild_id=8005)

    assert result is not None
    assert result.credits == 1500
    assert result.guild_id == 8005
    assert result.user_id == user.id


async def test_list_all_guild_configs(
    db_session: AsyncSession,
    config_repo: ConfigRepository,
):
    """All registered guild configs are returned by list_all."""
    await _create_guild_config(db_session, guild_id=8010)
    await _create_guild_config(db_session, guild_id=8011)
    await _create_guild_config(db_session, guild_id=8012)

    configs = await config_repo.list_all(db_session)

    guild_ids = {c.guild_id for c in configs}
    assert 8010 in guild_ids
    assert 8011 in guild_ids
    assert 8012 in guild_ids


async def test_get_players_by_guild(
    db_session: AsyncSession,
    player_repo: PlayerRepository,
):
    """Players in a specific guild can be listed; players from other guilds are excluded."""
    await _create_guild_config(db_session, guild_id=8013)
    await _create_guild_config(db_session, guild_id=8014)

    user1 = await _create_user(db_session, user_id=300010, username="player_a")
    user2 = await _create_user(db_session, user_id=300011, username="player_b")
    user3 = await _create_user(db_session, user_id=300012, username="player_c")

    await _create_player(db_session, user_id=user1.id, guild_id=8013)
    await _create_player(db_session, user_id=user2.id, guild_id=8013)
    await _create_player(db_session, user_id=user3.id, guild_id=8014)  # different guild

    guild_players = await player_repo.get_players_by_guild(db_session, guild_id=8013)

    assert len(guild_players) == 2
    player_user_ids = {p.user_id for p in guild_players}
    assert user1.id in player_user_ids
    assert user2.id in player_user_ids
    assert user3.id not in player_user_ids
