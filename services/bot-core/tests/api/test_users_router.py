"""Tests for the users API router endpoints.

Import path setup and sqlalchemy_utils mocking are handled by
tests/api/conftest.py which runs before this module is loaded.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_user(**overrides):
    defaults = dict(
        id=12345,
        discord_username="TestUser#1234",
        display_name=None,
    )
    defaults.update(overrides)
    user = MagicMock()
    for k, v in defaults.items():
        setattr(user, k, v)
    # created_at and updated_at must have .isoformat() that returns a string;
    # MagicMock auto-generates sub-mocks for attribute access, so we override
    # the isoformat call to return a fixed string.
    user.created_at = MagicMock()
    user.created_at.isoformat = MagicMock(return_value="2026-01-01T00:00:00")
    user.updated_at = MagicMock()
    user.updated_at.isoformat = MagicMock(return_value="2026-01-01T00:00:00")
    return user


def _configure_db_mock(mock_get_db):
    """Configure mock_get_db to act as an async context manager."""
    mock_session = AsyncMock()
    mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_user_repo():
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=make_mock_user())
    repo.create_or_update = AsyncMock(return_value=make_mock_user())
    repo.list_all = AsyncMock(return_value=[make_mock_user()])
    repo.get_or_create_user = AsyncMock(return_value=make_mock_user())
    return repo


@pytest.fixture
def test_app(mock_user_repo):
    from api.routers.users import get_user_repository
    from api.routers.users import router as users_router

    app = FastAPI()
    app.include_router(users_router, prefix="/api/v1")
    app.dependency_overrides[get_user_repository] = lambda: mock_user_repo
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


# ===========================================================================
# 1. POST /users/
# ===========================================================================


class TestCreateUser:
    """Tests for POST /api/v1/users/."""

    @patch("api.routers.users.get_db_session")
    def test_create_user_happy_path(self, mock_get_db, client, mock_user_repo):
        """Returns 201 with UserResponse on success."""
        _configure_db_mock(mock_get_db)
        mock_user_repo.get_by_id = AsyncMock(return_value=None)  # user doesn't exist yet
        payload = {"id": 12345, "discord_username": "TestUser#1234"}

        response = client.post("/api/v1/users/", json=payload)

        assert response.status_code == 201
        data = response.json()
        assert data["id"] == 12345
        assert data["discord_username"] == "TestUser#1234"
        assert "created_at" in data
        assert "updated_at" in data

    @patch("api.routers.users.get_db_session")
    def test_create_user_without_username(self, mock_get_db, client, mock_user_repo):
        """Returns 201 when discord_username is omitted (optional field)."""
        _configure_db_mock(mock_get_db)
        mock_user_repo.get_by_id = AsyncMock(return_value=None)
        mock_user_repo.create_or_update = AsyncMock(return_value=make_mock_user(discord_username=None))
        payload = {"id": 12345}

        response = client.post("/api/v1/users/", json=payload)

        assert response.status_code == 201

    @patch("api.routers.users.get_db_session")
    def test_create_user_already_exists_returns_409(self, mock_get_db, client, mock_user_repo):
        """Returns 409 when user with same ID already exists."""
        _configure_db_mock(mock_get_db)
        mock_user_repo.get_by_id = AsyncMock(return_value=make_mock_user())  # exists
        payload = {"id": 12345, "discord_username": "TestUser#1234"}

        response = client.post("/api/v1/users/", json=payload)

        assert response.status_code == 409
        assert "already exists" in response.json()["detail"]

    @patch("api.routers.users.get_db_session")
    def test_create_user_server_error_returns_500(self, mock_get_db, client, mock_user_repo):
        """Returns 500 when an unexpected exception is raised."""
        _configure_db_mock(mock_get_db)
        mock_user_repo.get_by_id = AsyncMock(side_effect=RuntimeError("DB failure"))
        payload = {"id": 12345, "discord_username": "TestUser#1234"}

        response = client.post("/api/v1/users/", json=payload)

        assert response.status_code == 500
        assert "Failed to create user" in response.json()["detail"]

    def test_create_user_missing_id_returns_422(self, client):
        """Returns 422 when required field id is missing."""
        payload = {"discord_username": "TestUser#1234"}

        response = client.post("/api/v1/users/", json=payload)

        assert response.status_code == 422


# ===========================================================================
# 2. GET /users/{user_id}
# ===========================================================================


class TestGetUser:
    """Tests for GET /api/v1/users/{user_id}."""

    @patch("api.routers.users.get_db_session")
    def test_get_user_happy_path(self, mock_get_db, client, mock_user_repo):
        """Returns 200 with UserResponse when user exists."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/users/12345")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 12345
        assert data["discord_username"] == "TestUser#1234"
        assert "created_at" in data
        assert "updated_at" in data

    @patch("api.routers.users.get_db_session")
    def test_get_user_not_found_returns_404(self, mock_get_db, client, mock_user_repo):
        """Returns 404 when user doesn't exist."""
        _configure_db_mock(mock_get_db)
        mock_user_repo.get_by_id = AsyncMock(return_value=None)

        response = client.get("/api/v1/users/99999")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    @patch("api.routers.users.get_db_session")
    def test_get_user_server_error_returns_500(self, mock_get_db, client, mock_user_repo):
        """Returns 500 when an unexpected exception is raised."""
        _configure_db_mock(mock_get_db)
        mock_user_repo.get_by_id = AsyncMock(side_effect=RuntimeError("DB failure"))

        response = client.get("/api/v1/users/12345")

        assert response.status_code == 500
        assert "Failed to get user" in response.json()["detail"]

    def test_get_user_invalid_user_id_returns_422(self, client):
        """Returns 422 when user_id is not an integer."""
        response = client.get("/api/v1/users/not_an_int")

        assert response.status_code == 422


# ===========================================================================
# 3. PUT /users/{user_id}
# ===========================================================================


class TestUpdateUser:
    """Tests for PUT /api/v1/users/{user_id}."""

    @patch("api.routers.users.get_db_session")
    def test_update_user_happy_path(self, mock_get_db, client, mock_user_repo):
        """Returns 200 with updated UserResponse."""
        _configure_db_mock(mock_get_db)
        updated_user = make_mock_user(discord_username="NewName#9999")
        mock_user_repo.create_or_update = AsyncMock(return_value=updated_user)
        payload = {"discord_username": "NewName#9999"}

        response = client.put("/api/v1/users/12345", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 12345
        assert data["discord_username"] == "NewName#9999"

    @patch("api.routers.users.get_db_session")
    def test_update_user_not_found_returns_404(self, mock_get_db, client, mock_user_repo):
        """Returns 404 when user doesn't exist."""
        _configure_db_mock(mock_get_db)
        mock_user_repo.get_by_id = AsyncMock(return_value=None)
        payload = {"discord_username": "NewName#9999"}

        response = client.put("/api/v1/users/99999", json=payload)

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    @patch("api.routers.users.get_db_session")
    def test_update_user_server_error_returns_500(self, mock_get_db, client, mock_user_repo):
        """Returns 500 when an unexpected exception is raised."""
        _configure_db_mock(mock_get_db)
        mock_user_repo.get_by_id = AsyncMock(side_effect=RuntimeError("DB failure"))
        payload = {"discord_username": "NewName#9999"}

        response = client.put("/api/v1/users/12345", json=payload)

        assert response.status_code == 500
        assert "Failed to update user" in response.json()["detail"]

    @patch("api.routers.users.get_db_session")
    def test_update_user_empty_body_accepted(self, mock_get_db, client, mock_user_repo):
        """Returns 200 when body has no fields (all optional in UpdateUserRequest)."""
        _configure_db_mock(mock_get_db)
        payload = {}

        response = client.put("/api/v1/users/12345", json=payload)

        assert response.status_code == 200


# ===========================================================================
# 4. GET /users/
# ===========================================================================


class TestListUsers:
    """Tests for GET /api/v1/users/."""

    @patch("api.routers.users.get_db_session")
    def test_list_users_happy_path(self, mock_get_db, client, mock_user_repo):
        """Returns 200 with list of UserResponse."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/users/")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == 12345

    @patch("api.routers.users.get_db_session")
    def test_list_users_empty_list(self, mock_get_db, client, mock_user_repo):
        """Returns 200 with empty list when no users exist."""
        _configure_db_mock(mock_get_db)
        mock_user_repo.list_all = AsyncMock(return_value=[])

        response = client.get("/api/v1/users/")

        assert response.status_code == 200
        assert response.json() == []

    @patch("api.routers.users.get_db_session")
    def test_list_users_pagination(self, mock_get_db, client, mock_user_repo):
        """Pagination parameters are accepted."""
        _configure_db_mock(mock_get_db)
        users = [make_mock_user(id=i) for i in range(5)]
        mock_user_repo.list_all = AsyncMock(return_value=users)

        response = client.get("/api/v1/users/?skip=2&limit=2")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    @patch("api.routers.users.get_db_session")
    def test_list_users_server_error_returns_500(self, mock_get_db, client, mock_user_repo):
        """Returns 500 when an unexpected exception is raised."""
        _configure_db_mock(mock_get_db)
        mock_user_repo.list_all = AsyncMock(side_effect=RuntimeError("Query failed"))

        response = client.get("/api/v1/users/")

        assert response.status_code == 500
        assert "Failed to list users" in response.json()["detail"]


# ===========================================================================
# 5. POST /users/{user_id}/get-or-create
# ===========================================================================


class TestGetOrCreateUser:
    """Tests for POST /api/v1/users/{user_id}/get-or-create."""

    @patch("api.routers.users.get_db_session")
    def test_get_or_create_user_happy_path(self, mock_get_db, client, mock_user_repo):
        """Returns 200 with UserResponse on success."""
        _configure_db_mock(mock_get_db)

        response = client.post("/api/v1/users/12345/get-or-create")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 12345

    @patch("api.routers.users.get_db_session")
    def test_get_or_create_user_with_username(self, mock_get_db, client, mock_user_repo):
        """Returns 200 when optional discord_username is provided as query param."""
        _configure_db_mock(mock_get_db)

        response = client.post("/api/v1/users/12345/get-or-create?discord_username=TestUser%231234")

        assert response.status_code == 200

    @patch("api.routers.users.get_db_session")
    def test_get_or_create_user_calls_repo(self, mock_get_db, client, mock_user_repo):
        """Calls get_or_create_user with correct user_id."""
        _configure_db_mock(mock_get_db)

        client.post("/api/v1/users/99999/get-or-create")

        mock_user_repo.get_or_create_user.assert_awaited_once()
        call_args = mock_user_repo.get_or_create_user.call_args
        assert 99999 in call_args.args or call_args.kwargs.get("user_id") == 99999

    @patch("api.routers.users.get_db_session")
    def test_get_or_create_user_server_error_returns_500(self, mock_get_db, client, mock_user_repo):
        """Returns 500 when an unexpected exception is raised."""
        _configure_db_mock(mock_get_db)
        mock_user_repo.get_or_create_user = AsyncMock(side_effect=RuntimeError("DB failure"))

        response = client.post("/api/v1/users/12345/get-or-create")

        assert response.status_code == 500
        assert "Failed to get or create user" in response.json()["detail"]
