"""Integration tests for UserRepository using SQLite in-memory database."""

import pytest
from persist.models.user import User
from persist.repositories.user_repository import UserRepository
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def repo() -> UserRepository:
    return UserRepository()


# -- get_by_id ----------------------------------------------------------------


async def test_get_by_id_returns_user(db_session: AsyncSession, repo: UserRepository):
    """Retrieving a user by ID should return the correct record."""
    user = User(id=100, discord_username="alice")
    db_session.add(user)
    await db_session.commit()

    result = await repo.get_by_id(db_session, 100)

    assert result is not None
    assert result.id == 100
    assert result.discord_username == "alice"


async def test_get_by_id_returns_none_for_missing(db_session: AsyncSession, repo: UserRepository):
    """Querying a non-existent ID should return None."""
    result = await repo.get_by_id(db_session, 999)
    assert result is None


# -- get_by_name ---------------------------------------------------------------


async def test_get_by_name_returns_user(db_session: AsyncSession, repo: UserRepository):
    """Finding a user by Discord username should succeed."""
    user = User(id=200, discord_username="bob")
    db_session.add(user)
    await db_session.commit()

    result = await repo.get_by_name(db_session, "bob")

    assert result is not None
    assert result.id == 200
    assert result.discord_username == "bob"


async def test_get_by_name_returns_none_for_missing(db_session: AsyncSession, repo: UserRepository):
    """Searching for a non-existent username should return None."""
    result = await repo.get_by_name(db_session, "nobody")
    assert result is None


# -- list_all ------------------------------------------------------------------


async def test_list_all_returns_all_users(db_session: AsyncSession, repo: UserRepository):
    """list_all should return every user in the database."""
    db_session.add_all(
        [
            User(id=1, discord_username="u1"),
            User(id=2, discord_username="u2"),
            User(id=3, discord_username="u3"),
        ]
    )
    await db_session.commit()

    users = await repo.list_all(db_session)

    assert len(users) == 3
    ids = {u.id for u in users}
    assert ids == {1, 2, 3}


async def test_list_all_empty(db_session: AsyncSession, repo: UserRepository):
    """list_all on an empty table should return an empty list."""
    users = await repo.list_all(db_session)
    assert users == []


# -- add -----------------------------------------------------------------------


async def test_add_persists_user(db_session: AsyncSession, repo: UserRepository):
    """Adding a user should persist it to the database."""
    user = User(id=300, discord_username="charlie")
    result = await repo.add(db_session, user)

    assert result.id == 300
    assert result.discord_username == "charlie"
    assert result.created_at is not None

    # Verify via a fresh query
    fetched = await repo.get_by_id(db_session, 300)
    assert fetched is not None
    assert fetched.discord_username == "charlie"


# -- create_or_update ----------------------------------------------------------


async def test_create_or_update_creates_new_user(db_session: AsyncSession, repo: UserRepository):
    """create_or_update should create a user when none exists."""
    result = await repo.create_or_update(db_session, {"id": 400, "discord_username": "dave"})

    assert result.id == 400
    assert result.discord_username == "dave"


async def test_create_or_update_updates_existing_user(db_session: AsyncSession, repo: UserRepository):
    """create_or_update should update the username of an existing user."""
    # Create initial
    await repo.add(db_session, User(id=500, discord_username="eve"))

    # Update
    result = await repo.create_or_update(db_session, {"id": 500, "discord_username": "eve_updated"})

    assert result.id == 500
    assert result.discord_username == "eve_updated"


async def test_create_or_update_raises_without_id(db_session: AsyncSession, repo: UserRepository):
    """create_or_update should raise ValueError when id is missing."""
    with pytest.raises(ValueError, match="User ID is required"):
        await repo.create_or_update(db_session, {"discord_username": "no_id"})


# -- remove --------------------------------------------------------------------


async def test_remove_deletes_user(db_session: AsyncSession, repo: UserRepository):
    """Removing a user should delete it from the database."""
    user = User(id=600, discord_username="frank")
    await repo.add(db_session, user)

    await repo.remove(db_session, user)

    result = await repo.get_by_id(db_session, 600)
    assert result is None


# -- get_or_create_user --------------------------------------------------------


async def test_get_or_create_user_creates_new(db_session: AsyncSession, repo: UserRepository):
    """get_or_create_user should create a user when none exists."""
    result = await repo.get_or_create_user(db_session, discord_id=700, username="grace")

    assert result.id == 700
    assert result.discord_username == "grace"


async def test_get_or_create_user_returns_existing(db_session: AsyncSession, repo: UserRepository):
    """get_or_create_user should return an existing user without modification
    when the username is unchanged."""
    await repo.add(db_session, User(id=800, discord_username="heidi"))

    result = await repo.get_or_create_user(db_session, discord_id=800, username="heidi")

    assert result.id == 800
    assert result.discord_username == "heidi"


async def test_get_or_create_user_updates_username(db_session: AsyncSession, repo: UserRepository):
    """get_or_create_user should update the username if it changed."""
    await repo.add(db_session, User(id=900, discord_username="ivan"))

    result = await repo.get_or_create_user(db_session, discord_id=900, username="ivan_new")

    assert result.id == 900
    assert result.discord_username == "ivan_new"
