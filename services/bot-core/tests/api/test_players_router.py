"""Tests for the players API router endpoints.

Import path setup and sqlalchemy_utils mocking are handled by
tests/api/conftest.py which runs before this module is loaded.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Import router at module load time so @patch decorators can resolve the module.
from api.routers.players import router as _players_router, get_player_service as _get_player_service  # noqa: E402

# Import the conftest helper
from conftest import make_mock_player


@pytest.fixture
def mock_player_service():
    """Mock PlayerService with all methods."""
    service = AsyncMock()
    service.player_repo = AsyncMock()
    # Configure defaults - individual tests override as needed
    service.get_or_create_player = AsyncMock(return_value=make_mock_player())
    service.player_repo.get_by_id = AsyncMock(return_value=make_mock_player())
    service.player_repo.get_players_by_guild = AsyncMock(return_value=[make_mock_player()])
    service.get_players_by_tier = AsyncMock(return_value=[make_mock_player()])
    service.update_player_credits = AsyncMock(return_value=make_mock_player(credits=500))
    service.update_player_xp = AsyncMock(return_value=make_mock_player(xp=100))
    service.prestige_player = AsyncMock(return_value=make_mock_player(prestige_count=1, xp=0, tier="Bronze"))
    service.get_player_statistics = AsyncMock(
        return_value={
            "player_id": 1,
            "tier": "Bronze",
            "tier_level": 1,
            "xp": 0,
            "prestige_count": 0,
            "credits": 100,
            "lifetime_credits": 100,
            "bounty_stats": {"wins": 0, "total": 0},
            "duel_stats": {"wins": 0, "losses": 0, "credits_won": 0, "credits_lost": 0},
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }
    )
    return service


@pytest.fixture
def test_app(mock_player_service):
    app = FastAPI()
    app.include_router(_players_router, prefix="/api/v1")
    app.dependency_overrides[_get_player_service] = lambda: mock_player_service
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


# ---------------------------------------------------------------------------
# TestCreateOrGetPlayer
# ---------------------------------------------------------------------------


class TestCreateOrGetPlayer:
    """Tests for POST /players/ -> create_or_get_player."""

    @patch("api.routers.players.get_db_session")
    def test_create_player_returns_201(self, mock_get_db, client, mock_player_service):
        """Happy path: valid request returns 201 with player data."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.post(
            "/api/v1/players/",
            json={"discord_id": 12345, "guild_id": 67890, "discord_username": "TestUser"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["id"] == 1
        assert data["user_id"] == 12345
        assert data["guild_id"] == 67890
        assert data["credits"] == 100
        assert data["tier"] == "Bronze"

    @patch("api.routers.players.get_db_session")
    def test_create_player_delegates_to_service(self, mock_get_db, client, mock_player_service):
        """Service delegation: service called with correct args."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        client.post(
            "/api/v1/players/",
            json={"discord_id": 99999, "guild_id": 11111, "discord_username": "SomeUser"},
        )

        mock_player_service.get_or_create_player.assert_called_once_with(mock_session, 99999, 11111, "SomeUser")

    @patch("api.routers.players.get_db_session")
    def test_create_player_without_username(self, mock_get_db, client, mock_player_service):
        """Happy path: discord_username is optional (None)."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.post(
            "/api/v1/players/",
            json={"discord_id": 12345, "guild_id": 67890},
        )

        assert response.status_code == 201
        mock_player_service.get_or_create_player.assert_called_once_with(mock_session, 12345, 67890, None)

    @patch("api.routers.players.get_db_session")
    def test_create_player_service_exception_returns_500(self, mock_get_db, client, mock_player_service):
        """Server error: service raises Exception -> 500."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_player_service.get_or_create_player.side_effect = Exception("DB exploded")

        response = client.post(
            "/api/v1/players/",
            json={"discord_id": 12345, "guild_id": 67890},
        )

        assert response.status_code == 500
        assert "Failed to create or get player" in response.json()["detail"]

    def test_create_player_missing_required_fields_returns_422(self, client):
        """Request validation: missing discord_id and guild_id -> 422."""
        response = client.post("/api/v1/players/", json={"discord_username": "OnlyName"})
        assert response.status_code == 422

    def test_create_player_invalid_discord_id_type_returns_422(self, client):
        """Request validation: non-integer discord_id -> 422."""
        response = client.post(
            "/api/v1/players/",
            json={"discord_id": "not-an-int", "guild_id": 67890},
        )
        assert response.status_code == 422

    @patch("api.routers.players.get_db_session")
    def test_create_player_response_shape(self, mock_get_db, client, mock_player_service):
        """Response shape: all expected fields present in 201 response."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.post(
            "/api/v1/players/",
            json={"discord_id": 12345, "guild_id": 67890},
        )

        data = response.json()
        expected_keys = {
            "id",
            "user_id",
            "guild_id",
            "credits",
            "lifetime_credits",
            "systems_checked",
            "bounty_wins",
            "xp",
            "tier",
            "prestige_count",
            "duel_wins",
            "duel_losses",
            "duel_credits_won",
            "duel_credits_lost",
            "active_ship_id",
            "created_at",
            "updated_at",
        }
        assert expected_keys.issubset(data.keys())


# ---------------------------------------------------------------------------
# TestGetPlayer
# ---------------------------------------------------------------------------


class TestGetPlayer:
    """Tests for GET /players/{player_id} -> get_player."""

    @patch("api.routers.players.get_db_session")
    def test_get_player_returns_200(self, mock_get_db, client, mock_player_service):
        """Happy path: existing player returns 200."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/api/v1/players/1")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 1
        assert data["credits"] == 100

    @patch("api.routers.players.get_db_session")
    def test_get_player_delegates_to_repo(self, mock_get_db, client, mock_player_service):
        """Service delegation: repo.get_by_id called with correct player_id."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        client.get("/api/v1/players/42")

        mock_player_service.player_repo.get_by_id.assert_called_once_with(mock_session, 42)

    @patch("api.routers.players.get_db_session")
    def test_get_player_not_found_returns_404(self, mock_get_db, client, mock_player_service):
        """Not found: repo returns None -> 404."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_player_service.player_repo.get_by_id.return_value = None

        response = client.get("/api/v1/players/999")

        assert response.status_code == 404
        assert "999" in response.json()["detail"]

    @patch("api.routers.players.get_db_session")
    def test_get_player_service_exception_returns_500(self, mock_get_db, client, mock_player_service):
        """Server error: repo raises Exception -> 500."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_player_service.player_repo.get_by_id.side_effect = Exception("connection lost")

        response = client.get("/api/v1/players/1")

        assert response.status_code == 500
        assert "Failed to get player" in response.json()["detail"]

    @patch("api.routers.players.get_db_session")
    def test_get_player_response_has_correct_values(self, mock_get_db, client, mock_player_service):
        """Happy path: returned data matches the mock player attributes."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_player_service.player_repo.get_by_id.return_value = make_mock_player(
            id=7, user_id=777, guild_id=888, credits=999, tier="Gold"
        )

        response = client.get("/api/v1/players/7")

        data = response.json()
        assert data["id"] == 7
        assert data["user_id"] == 777
        assert data["guild_id"] == 888
        assert data["credits"] == 999
        assert data["tier"] == "Gold"


# ---------------------------------------------------------------------------
# TestGetPlayersByGuild
# ---------------------------------------------------------------------------


class TestGetPlayersByGuild:
    """Tests for GET /players/guild/{guild_id} -> get_players_by_guild."""

    @patch("api.routers.players.get_db_session")
    def test_get_players_by_guild_no_tier_returns_200(self, mock_get_db, client, mock_player_service):
        """Happy path: returns list of players for guild (no tier filter)."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/api/v1/players/guild/67890")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["guild_id"] == 67890

    @patch("api.routers.players.get_db_session")
    def test_get_players_by_guild_uses_repo_when_no_tier(self, mock_get_db, client, mock_player_service):
        """Service delegation: no tier -> uses player_repo.get_players_by_guild."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        client.get("/api/v1/players/guild/67890")

        mock_player_service.player_repo.get_players_by_guild.assert_called_once_with(mock_session, 67890)
        mock_player_service.get_players_by_tier.assert_not_called()

    @patch("api.routers.players.get_db_session")
    def test_get_players_by_guild_with_tier_filter(self, mock_get_db, client, mock_player_service):
        """Happy path: with tier filter -> uses get_players_by_tier."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/api/v1/players/guild/67890?tier=Gold")

        assert response.status_code == 200
        mock_player_service.get_players_by_tier.assert_called_once_with(mock_session, 67890, "Gold")
        mock_player_service.player_repo.get_players_by_guild.assert_not_called()

    @patch("api.routers.players.get_db_session")
    def test_get_players_by_guild_pagination(self, mock_get_db, client, mock_player_service):
        """Pagination: skip and limit query params are respected."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
        # Create 5 players
        players = [make_mock_player(id=i) for i in range(1, 6)]
        mock_player_service.player_repo.get_players_by_guild.return_value = players

        response = client.get("/api/v1/players/guild/67890?skip=1&limit=2")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    @patch("api.routers.players.get_db_session")
    def test_get_players_by_guild_empty_result(self, mock_get_db, client, mock_player_service):
        """Happy path: guild with no players returns empty list."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_player_service.player_repo.get_players_by_guild.return_value = []

        response = client.get("/api/v1/players/guild/99999")

        assert response.status_code == 200
        assert response.json() == []

    @patch("api.routers.players.get_db_session")
    def test_get_players_by_guild_service_exception_returns_500(self, mock_get_db, client, mock_player_service):
        """Server error: service raises Exception -> 500."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_player_service.player_repo.get_players_by_guild.side_effect = Exception("oops")

        response = client.get("/api/v1/players/guild/67890")

        assert response.status_code == 500
        assert "Failed to get players" in response.json()["detail"]


# ---------------------------------------------------------------------------
# TestUpdatePlayerCredits
# ---------------------------------------------------------------------------


class TestUpdatePlayerCredits:
    """Tests for PUT /players/{player_id}/credits -> update_player_credits."""

    @patch("api.routers.players.get_db_session")
    def test_update_credits_returns_200(self, mock_get_db, client, mock_player_service):
        """Happy path: valid request updates credits and returns 200."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.put(
            "/api/v1/players/1/credits",
            json={"credits": 500, "update_lifetime": True},
        )

        assert response.status_code == 200
        assert response.json()["credits"] == 500

    @patch("api.routers.players.get_db_session")
    def test_update_credits_delegates_to_service(self, mock_get_db, client, mock_player_service):
        """Service delegation: update_player_credits called with correct args."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        client.put(
            "/api/v1/players/42/credits",
            json={"credits": 250, "update_lifetime": False},
        )

        mock_player_service.update_player_credits.assert_called_once_with(mock_session, 42, 250, False)

    @patch("api.routers.players.get_db_session")
    def test_update_credits_value_error_returns_400(self, mock_get_db, client, mock_player_service):
        """Validation error: service raises ValueError -> 400."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_player_service.update_player_credits.side_effect = ValueError("Player not found")

        response = client.put(
            "/api/v1/players/999/credits",
            json={"credits": 100},
        )

        assert response.status_code == 400
        assert "Player not found" in response.json()["detail"]

    @patch("api.routers.players.get_db_session")
    def test_update_credits_service_exception_returns_500(self, mock_get_db, client, mock_player_service):
        """Server error: service raises Exception -> 500."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_player_service.update_player_credits.side_effect = Exception("unexpected")

        response = client.put("/api/v1/players/1/credits", json={"credits": 100})

        assert response.status_code == 500
        assert "Failed to update credits" in response.json()["detail"]

    def test_update_credits_negative_value_returns_422(self, client):
        """Request validation: negative credits -> 422 (ge=0 constraint)."""
        response = client.put("/api/v1/players/1/credits", json={"credits": -50})
        assert response.status_code == 422

    def test_update_credits_missing_credits_field_returns_422(self, client):
        """Request validation: missing required 'credits' field -> 422."""
        response = client.put("/api/v1/players/1/credits", json={"update_lifetime": True})
        assert response.status_code == 422

    @patch("api.routers.players.get_db_session")
    def test_update_credits_default_update_lifetime_true(self, mock_get_db, client, mock_player_service):
        """Default: update_lifetime defaults to True when not provided."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        client.put("/api/v1/players/1/credits", json={"credits": 100})

        mock_player_service.update_player_credits.assert_called_once_with(mock_session, 1, 100, True)


# ---------------------------------------------------------------------------
# TestUpdatePlayerXP
# ---------------------------------------------------------------------------


class TestUpdatePlayerXP:
    """Tests for PUT /players/{player_id}/xp -> update_player_xp."""

    @patch("api.routers.players.get_db_session")
    def test_update_xp_returns_200(self, mock_get_db, client, mock_player_service):
        """Happy path: valid request updates XP and returns 200."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.put("/api/v1/players/1/xp", json={"xp": 100})

        assert response.status_code == 200
        assert response.json()["xp"] == 100

    @patch("api.routers.players.get_db_session")
    def test_update_xp_delegates_to_service(self, mock_get_db, client, mock_player_service):
        """Service delegation: update_player_xp called with correct args."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        client.put("/api/v1/players/7/xp", json={"xp": 500})

        mock_player_service.update_player_xp.assert_called_once_with(mock_session, 7, 500)

    @patch("api.routers.players.get_db_session")
    def test_update_xp_value_error_returns_400(self, mock_get_db, client, mock_player_service):
        """Validation error: service raises ValueError -> 400."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_player_service.update_player_xp.side_effect = ValueError("Player 999 not found")

        response = client.put("/api/v1/players/999/xp", json={"xp": 100})

        assert response.status_code == 400
        assert "Player 999 not found" in response.json()["detail"]

    @patch("api.routers.players.get_db_session")
    def test_update_xp_service_exception_returns_500(self, mock_get_db, client, mock_player_service):
        """Server error: service raises Exception -> 500."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_player_service.update_player_xp.side_effect = Exception("db gone")

        response = client.put("/api/v1/players/1/xp", json={"xp": 100})

        assert response.status_code == 500
        assert "Failed to update XP" in response.json()["detail"]

    def test_update_xp_negative_value_returns_422(self, client):
        """Request validation: negative XP -> 422 (ge=0 constraint)."""
        response = client.put("/api/v1/players/1/xp", json={"xp": -1})
        assert response.status_code == 422

    def test_update_xp_exceeds_max_returns_422(self, client):
        """Request validation: XP > 1,000,000 -> 422 (le=1000000 constraint)."""
        response = client.put("/api/v1/players/1/xp", json={"xp": 1_000_001})
        assert response.status_code == 422

    def test_update_xp_missing_field_returns_422(self, client):
        """Request validation: missing xp field -> 422."""
        response = client.put("/api/v1/players/1/xp", json={})
        assert response.status_code == 422

    @patch("api.routers.players.get_db_session")
    def test_update_xp_zero_is_valid(self, mock_get_db, client, mock_player_service):
        """Boundary: xp=0 is valid (ge=0)."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.put("/api/v1/players/1/xp", json={"xp": 0})

        assert response.status_code == 200

    @patch("api.routers.players.get_db_session")
    def test_update_xp_max_boundary_is_valid(self, mock_get_db, client, mock_player_service):
        """Boundary: xp=1,000,000 is valid (le=1000000)."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.put("/api/v1/players/1/xp", json={"xp": 1_000_000})

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# TestPrestigePlayer
# ---------------------------------------------------------------------------


class TestPrestigePlayer:
    """Tests for POST /players/{player_id}/prestige -> prestige_player."""

    @patch("api.routers.players.get_db_session")
    def test_prestige_player_returns_200(self, mock_get_db, client, mock_player_service):
        """Happy path: valid player prestige returns 200 with updated data."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.post("/api/v1/players/1/prestige")

        assert response.status_code == 200
        data = response.json()
        assert data["prestige_count"] == 1
        assert data["xp"] == 0
        assert data["tier"] == "Bronze"

    @patch("api.routers.players.get_db_session")
    def test_prestige_player_delegates_to_service(self, mock_get_db, client, mock_player_service):
        """Service delegation: prestige_player called with correct player_id."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        client.post("/api/v1/players/55/prestige")

        mock_player_service.prestige_player.assert_called_once_with(mock_session, 55)

    @patch("api.routers.players.get_db_session")
    def test_prestige_player_value_error_returns_400(self, mock_get_db, client, mock_player_service):
        """Validation error: service raises ValueError -> 400 (e.g. already max prestige)."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_player_service.prestige_player.side_effect = ValueError("Player not at max tier")

        response = client.post("/api/v1/players/1/prestige")

        assert response.status_code == 400
        assert "Player not at max tier" in response.json()["detail"]

    @patch("api.routers.players.get_db_session")
    def test_prestige_player_service_exception_returns_500(self, mock_get_db, client, mock_player_service):
        """Server error: service raises Exception -> 500."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_player_service.prestige_player.side_effect = Exception("fatal error")

        response = client.post("/api/v1/players/1/prestige")

        assert response.status_code == 500
        assert "Failed to prestige player" in response.json()["detail"]

    @patch("api.routers.players.get_db_session")
    def test_prestige_player_increments_prestige_count(self, mock_get_db, client, mock_player_service):
        """Happy path: returned player shows incremented prestige_count."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_player_service.prestige_player.return_value = make_mock_player(
            prestige_count=3, xp=0, tier="Bronze", credits=100
        )

        response = client.post("/api/v1/players/1/prestige")

        data = response.json()
        assert data["prestige_count"] == 3
        assert data["xp"] == 0


# ---------------------------------------------------------------------------
# TestGetPlayerStatistics
# ---------------------------------------------------------------------------


class TestGetPlayerStatistics:
    """Tests for GET /players/{player_id}/statistics -> get_player_statistics."""

    @patch("api.routers.players.get_db_session")
    def test_get_statistics_returns_200(self, mock_get_db, client, mock_player_service):
        """Happy path: valid player ID returns 200 with statistics."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/api/v1/players/1/statistics")

        assert response.status_code == 200
        data = response.json()
        assert data["player_id"] == 1
        assert data["tier"] == "Bronze"
        assert data["tier_level"] == 1
        assert "bounty_stats" in data
        assert "duel_stats" in data

    @patch("api.routers.players.get_db_session")
    def test_get_statistics_delegates_to_service(self, mock_get_db, client, mock_player_service):
        """Service delegation: get_player_statistics called with correct player_id."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        client.get("/api/v1/players/77/statistics")

        mock_player_service.get_player_statistics.assert_called_once_with(mock_session, 77)

    @patch("api.routers.players.get_db_session")
    def test_get_statistics_value_error_returns_404(self, mock_get_db, client, mock_player_service):
        """Not found: service raises ValueError -> 404."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_player_service.get_player_statistics.side_effect = ValueError("Player 999 not found")

        response = client.get("/api/v1/players/999/statistics")

        assert response.status_code == 404
        assert "Player 999 not found" in response.json()["detail"]

    @patch("api.routers.players.get_db_session")
    def test_get_statistics_service_exception_returns_500(self, mock_get_db, client, mock_player_service):
        """Server error: service raises Exception -> 500."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_player_service.get_player_statistics.side_effect = Exception("query failed")

        response = client.get("/api/v1/players/1/statistics")

        assert response.status_code == 500
        assert "Failed to get player statistics" in response.json()["detail"]

    @patch("api.routers.players.get_db_session")
    def test_get_statistics_response_shape(self, mock_get_db, client, mock_player_service):
        """Response shape: all expected top-level fields present."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/api/v1/players/1/statistics")

        data = response.json()
        expected_keys = {
            "player_id",
            "tier",
            "tier_level",
            "xp",
            "prestige_count",
            "credits",
            "lifetime_credits",
            "bounty_stats",
            "duel_stats",
            "created_at",
            "updated_at",
        }
        assert expected_keys.issubset(data.keys())

    @patch("api.routers.players.get_db_session")
    def test_get_statistics_bounty_and_duel_stats_are_dicts(self, mock_get_db, client, mock_player_service):
        """Response validation: bounty_stats and duel_stats are dictionaries."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/api/v1/players/1/statistics")

        data = response.json()
        assert isinstance(data["bounty_stats"], dict)
        assert isinstance(data["duel_stats"], dict)

    @patch("api.routers.players.get_db_session")
    def test_get_statistics_custom_values(self, mock_get_db, client, mock_player_service):
        """Happy path: response reflects the stats returned by the service."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_player_service.get_player_statistics.return_value = {
            "player_id": 42,
            "tier": "Gold",
            "tier_level": 3,
            "xp": 7500,
            "prestige_count": 2,
            "credits": 50000,
            "lifetime_credits": 120000,
            "bounty_stats": {"wins": 10, "total": 15},
            "duel_stats": {"wins": 5, "losses": 3, "credits_won": 2000, "credits_lost": 800},
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-03-01T00:00:00",
        }

        response = client.get("/api/v1/players/42/statistics")

        data = response.json()
        assert data["player_id"] == 42
        assert data["tier"] == "Gold"
        assert data["tier_level"] == 3
        assert data["xp"] == 7500
        assert data["prestige_count"] == 2
        assert data["bounty_stats"]["wins"] == 10
        assert data["duel_stats"]["credits_won"] == 2000
