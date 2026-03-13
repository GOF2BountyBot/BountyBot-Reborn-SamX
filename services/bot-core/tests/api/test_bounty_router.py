"""Tests for the bounty API router endpoints.

Import path setup and sqlalchemy_utils mocking are handled by
tests/api/conftest.py which runs before this module is loaded.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def make_mock_bounty(**overrides):
    """Build a MagicMock that looks like a Bounty ORM object."""
    now = datetime(2026, 1, 1, 12, 0, 0)
    defaults = dict(
        id=1,
        guild_id=67890,
        division="Alpha",
        criminal_name="Dark Mage",
        criminal_faction="Void Syndicate",
        route=["Sol", "Proxima", "Tau Ceti"],
        answer="Tau Ceti",
        reward=5000,
        reward_per_sys=1000,
        checked={"Sol": 1, "Proxima": 2},
        issue_time=now,
        end_time=None,
        tech_level=3,
        criminal_ship={"name": "Interceptor", "class": "Fighter"},
        status="active",
        escape_count=0,
        win_user_id=None,
    )
    defaults.update(overrides)
    bounty = MagicMock()
    for k, v in defaults.items():
        setattr(bounty, k, v)
    return bounty


def make_check_response(result_value="correct", bounty_id=1, message=""):
    """Build a mock CheckResponse-like object."""
    from services.bounty_service import CheckResponse, CheckResult

    result_map = {
        "correct": CheckResult.CORRECT,
        "incorrect": CheckResult.INCORRECT,
        "not_found": CheckResult.NOT_FOUND,
        "on_cooldown": CheckResult.ON_COOLDOWN,
        "already_checked": CheckResult.ALREADY_CHECKED,
    }
    return CheckResponse(
        result=result_map[result_value],
        bounty_id=bounty_id,
        message=message,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_bounty_service():
    service = AsyncMock()
    service.bounty_repo = AsyncMock()
    service.bounty_repo.get_active_by_guild = AsyncMock(return_value=[make_mock_bounty()])
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock(
        return_value=[make_mock_bounty()]
    )
    service.bounty_repo.get_by_id = AsyncMock(return_value=make_mock_bounty())
    service.check_bounty = AsyncMock(return_value=make_check_response("correct"))
    service.spawn_bounty = AsyncMock(return_value=make_mock_bounty())
    return service


@pytest.fixture
def test_app(mock_bounty_service):
    app = FastAPI()
    from api.routers.bounties import get_bounty_service
    from api.routers.bounties import router as bounties_router

    app.include_router(bounties_router, prefix="/api/v1")
    app.dependency_overrides[get_bounty_service] = lambda: mock_bounty_service
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


# ---------------------------------------------------------------------------
# Helper: configure get_db_session mock as async context manager
# ---------------------------------------------------------------------------


def _configure_db_mock(mock_get_db):
    """Configure mock_get_db to act as an async context manager."""
    mock_session = AsyncMock()
    mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_session


# ===========================================================================
# 1. POST /bounties/check
# ===========================================================================


class TestCheckBounty:
    """Tests for POST /api/v1/bounties/check."""

    @patch("api.routers.bounties.get_db_session")
    def test_check_bounty_correct(self, mock_get_db, client, mock_bounty_service):
        """Returns CORRECT result when the system is the answer."""
        _configure_db_mock(mock_get_db)
        mock_bounty_service.check_bounty = AsyncMock(
            return_value=make_check_response("correct", bounty_id=1, message="Correct!")
        )

        response = client.post(
            "/api/v1/bounties/check?guild_id=67890",
            json={"player_id": 42, "system_name": "Tau Ceti"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"] == "correct"
        assert data["bounty_id"] == 1
        assert data["message"] == "Correct!"

    @patch("api.routers.bounties.get_db_session")
    def test_check_bounty_incorrect(self, mock_get_db, client, mock_bounty_service):
        """Returns INCORRECT result when system is in route but not the answer."""
        _configure_db_mock(mock_get_db)
        mock_bounty_service.check_bounty = AsyncMock(
            return_value=make_check_response("incorrect", bounty_id=1, message="Wrong system.")
        )

        response = client.post(
            "/api/v1/bounties/check?guild_id=67890",
            json={"player_id": 42, "system_name": "Sol"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"] == "incorrect"
        assert data["bounty_id"] == 1

    @patch("api.routers.bounties.get_db_session")
    def test_check_bounty_not_found(self, mock_get_db, client, mock_bounty_service):
        """Returns NOT_FOUND result when no active bounty exists for the guild."""
        _configure_db_mock(mock_get_db)
        mock_bounty_service.check_bounty = AsyncMock(
            return_value=make_check_response("not_found", bounty_id=None, message="No bounty.")
        )

        response = client.post(
            "/api/v1/bounties/check?guild_id=67890",
            json={"player_id": 42, "system_name": "Unknown"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"] == "not_found"
        assert data["bounty_id"] is None

    @patch("api.routers.bounties.get_db_session")
    def test_check_bounty_on_cooldown(self, mock_get_db, client, mock_bounty_service):
        """Returns ON_COOLDOWN result when the player is on cooldown."""
        _configure_db_mock(mock_get_db)
        mock_bounty_service.check_bounty = AsyncMock(
            return_value=make_check_response(
                "on_cooldown", bounty_id=1, message="Please wait before checking again."
            )
        )

        response = client.post(
            "/api/v1/bounties/check?guild_id=67890",
            json={"player_id": 42, "system_name": "Proxima"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"] == "on_cooldown"
        assert "wait" in data["message"].lower()


# ===========================================================================
# 2. GET /bounties/
# ===========================================================================


class TestListBounties:
    """Tests for GET /api/v1/bounties/."""

    @patch("api.routers.bounties.get_db_session")
    def test_list_bounties_with_division(self, mock_get_db, client, mock_bounty_service):
        """Returns filtered bounties when division query param is provided."""
        _configure_db_mock(mock_get_db)
        mock_bounty = make_mock_bounty(division="Alpha")
        mock_bounty_service.bounty_repo.get_active_by_guild_and_division = AsyncMock(
            return_value=[mock_bounty]
        )

        response = client.get("/api/v1/bounties/?guild_id=67890&division=Alpha")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["division"] == "Alpha"
        # Ensure answer is NOT in public response
        assert "answer" not in data[0]
        mock_bounty_service.bounty_repo.get_active_by_guild_and_division.assert_called_once()

    @patch("api.routers.bounties.get_db_session")
    def test_list_bounties_no_division(self, mock_get_db, client, mock_bounty_service):
        """Returns all active bounties when no division filter is given."""
        _configure_db_mock(mock_get_db)
        mock_bounty = make_mock_bounty()
        mock_bounty_service.bounty_repo.get_active_by_guild = AsyncMock(
            return_value=[mock_bounty]
        )

        response = client.get("/api/v1/bounties/?guild_id=67890")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        mock_bounty_service.bounty_repo.get_active_by_guild.assert_called_once()

    @patch("api.routers.bounties.get_db_session")
    def test_list_bounties_empty(self, mock_get_db, client, mock_bounty_service):
        """Returns empty list when no active bounties exist."""
        _configure_db_mock(mock_get_db)
        mock_bounty_service.bounty_repo.get_active_by_guild = AsyncMock(return_value=[])

        response = client.get("/api/v1/bounties/?guild_id=67890")

        assert response.status_code == 200
        assert response.json() == []


# ===========================================================================
# 3. GET /bounties/{bounty_id}/route
# ===========================================================================


class TestGetBountyRoute:
    """Tests for GET /api/v1/bounties/{bounty_id}/route."""

    @patch("api.routers.bounties.get_db_session")
    def test_get_route_success(self, mock_get_db, client, mock_bounty_service):
        """Returns route and checked status for a valid bounty."""
        _configure_db_mock(mock_get_db)
        mock_bounty = make_mock_bounty(
            id=1,
            route=["Sol", "Proxima", "Tau Ceti"],
            checked={"Sol": 1},
            status="active",
        )
        mock_bounty_service.bounty_repo.get_by_id = AsyncMock(return_value=mock_bounty)

        response = client.get("/api/v1/bounties/1/route")

        assert response.status_code == 200
        data = response.json()
        assert data["bounty_id"] == 1
        assert data["criminal_name"] == "Dark Mage"
        assert data["route"] == ["Sol", "Proxima", "Tau Ceti"]
        assert data["checked"] == {"Sol": 1}
        assert data["status"] == "active"

    @patch("api.routers.bounties.get_db_session")
    def test_get_route_not_found(self, mock_get_db, client, mock_bounty_service):
        """Returns 404 when bounty does not exist."""
        _configure_db_mock(mock_get_db)
        mock_bounty_service.bounty_repo.get_by_id = AsyncMock(return_value=None)

        response = client.get("/api/v1/bounties/999/route")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


# ===========================================================================
# 4. POST /bounties/spawn
# ===========================================================================


class TestSpawnBounty:
    """Tests for POST /api/v1/bounties/spawn."""

    @patch("api.routers.bounties.get_db_session")
    def test_spawn_bounty_success(self, mock_get_db, client, mock_bounty_service):
        """Returns the created bounty on success."""
        _configure_db_mock(mock_get_db)
        mock_bounty = make_mock_bounty()
        mock_bounty_service.spawn_bounty = AsyncMock(return_value=mock_bounty)

        response = client.post(
            "/api/v1/bounties/spawn",
            json={"guild_id": 67890, "division": "Alpha", "tech_level": 3},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 1
        assert data["guild_id"] == 67890
        assert data["division"] == "Alpha"
        assert data["criminal_name"] == "Dark Mage"
        assert data["status"] == "active"

    @patch("api.routers.bounties.get_db_session")
    def test_spawn_bounty_fails(self, mock_get_db, client, mock_bounty_service):
        """Returns 400 when service cannot spawn (no criminals or systems)."""
        _configure_db_mock(mock_get_db)
        mock_bounty_service.spawn_bounty = AsyncMock(return_value=None)

        response = client.post(
            "/api/v1/bounties/spawn",
            json={"guild_id": 67890, "division": "Beta", "tech_level": 5},
        )

        assert response.status_code == 400
        assert "failed" in response.json()["detail"].lower()


# ===========================================================================
# 5. GET /bounties/{bounty_id}/loadout
# ===========================================================================


class TestGetBountyLoadout:
    """Tests for GET /api/v1/bounties/{bounty_id}/loadout."""

    @patch("api.routers.bounties.get_db_session")
    def test_get_loadout_success(self, mock_get_db, client, mock_bounty_service):
        """Returns criminal ship loadout for a valid bounty."""
        _configure_db_mock(mock_get_db)
        mock_bounty = make_mock_bounty(
            id=1,
            criminal_name="Dark Mage",
            criminal_ship={"name": "Interceptor", "class": "Fighter"},
            tech_level=3,
        )
        mock_bounty_service.bounty_repo.get_by_id = AsyncMock(return_value=mock_bounty)

        response = client.get("/api/v1/bounties/1/loadout")

        assert response.status_code == 200
        data = response.json()
        assert data["bounty_id"] == 1
        assert data["criminal_name"] == "Dark Mage"
        assert data["criminal_ship"] == {"name": "Interceptor", "class": "Fighter"}
        assert data["tech_level"] == 3

    @patch("api.routers.bounties.get_db_session")
    def test_get_loadout_not_found(self, mock_get_db, client, mock_bounty_service):
        """Returns 404 when bounty does not exist."""
        _configure_db_mock(mock_get_db)
        mock_bounty_service.bounty_repo.get_by_id = AsyncMock(return_value=None)

        response = client.get("/api/v1/bounties/999/loadout")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()
