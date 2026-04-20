"""Tests for the players API router endpoints.

Import path setup and sqlalchemy_utils mocking are handled by
tests/api/conftest.py which runs before this module is loaded.
"""

from unittest.mock import AsyncMock

import pytest
from api.routers.players import get_player_service as _get_player_service

# Import router at module load time so @patch decorators can resolve the module.
from api.routers.players import router as _players_router

# Import the conftest helper
from conftest import make_mock_player
from fastapi import FastAPI
from fastapi.testclient import TestClient


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
    service.prestige_player = AsyncMock(
        return_value={
            "player_id": 1,
            "prestige_count": 1,
            "level_before": 10,
            "division_before": "Elite",
        }
    )
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
    service.get_promotion_status = AsyncMock(
        return_value={
            "player_id": 1,
            "current_tier": "Bronze",
            "current_tier_level": 1,
            "eligible_tier": "Silver",
            "next_tier": "Silver",
            "can_promote": True,
            "xp": 1500,
            "xp_threshold_for_next": 1000,
            "xp_surplus_for_next": 500,
        }
    )
    service.promote_player = AsyncMock(
        return_value={
            "player_id": 1,
            "old_tier": "Bronze",
            "new_tier": "Silver",
            "xp": 1500,
            "eligible_for_next": False,
            "next_tier": "Gold",
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


@pytest.fixture(autouse=True)
def _patch_players_db(mock_db_session, monkeypatch):
    """Patch get_db_session for all players router tests automatically."""
    _, mock_cm = mock_db_session
    monkeypatch.setattr("api.routers.players.get_db_session", lambda: mock_cm)


# ---------------------------------------------------------------------------
# TestCreateOrGetPlayer
# ---------------------------------------------------------------------------


class TestCreateOrGetPlayer:
    """Tests for POST /players/ -> create_or_get_player."""

    def test_create_player_returns_201(self, client, mock_player_service):
        """Happy path: valid request returns 201 with player data."""
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

    def test_create_player_delegates_to_service(self, mock_db_session, client, mock_player_service):
        """Service delegation: service called with correct args."""
        mock_session, _ = mock_db_session

        client.post(
            "/api/v1/players/",
            json={"discord_id": 99999, "guild_id": 11111, "discord_username": "SomeUser"},
        )

        mock_player_service.get_or_create_player.assert_called_once_with(mock_session, 99999, 11111, "SomeUser")

    def test_create_player_without_username(self, mock_db_session, client, mock_player_service):
        """Happy path: discord_username is optional (None)."""
        mock_session, _ = mock_db_session

        response = client.post(
            "/api/v1/players/",
            json={"discord_id": 12345, "guild_id": 67890},
        )

        assert response.status_code == 201
        mock_player_service.get_or_create_player.assert_called_once_with(mock_session, 12345, 67890, None)

    def test_create_player_service_exception_returns_500(self, client, mock_player_service):
        """Server error: service raises Exception -> 500."""
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

    def test_create_player_response_shape(self, client, mock_player_service):
        """Response shape: all expected fields present in 201 response."""
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

    def test_get_player_returns_200(self, client, mock_player_service):
        """Happy path: existing player returns 200."""
        response = client.get("/api/v1/players/1")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 1
        assert data["credits"] == 100

    def test_get_player_delegates_to_repo(self, mock_db_session, client, mock_player_service):
        """Service delegation: repo.get_by_id called with correct player_id."""
        mock_session, _ = mock_db_session

        client.get("/api/v1/players/42")

        mock_player_service.player_repo.get_by_id.assert_called_once_with(mock_session, 42)

    def test_get_player_not_found_returns_404(self, client, mock_player_service):
        """Not found: repo returns None -> 404."""
        mock_player_service.player_repo.get_by_id.return_value = None

        response = client.get("/api/v1/players/999")

        assert response.status_code == 404
        assert "999" in response.json()["detail"]

    def test_get_player_service_exception_returns_500(self, client, mock_player_service):
        """Server error: repo raises Exception -> 500."""
        mock_player_service.player_repo.get_by_id.side_effect = Exception("connection lost")

        response = client.get("/api/v1/players/1")

        assert response.status_code == 500
        assert "Failed to get player" in response.json()["detail"]

    def test_get_player_response_has_correct_values(self, client, mock_player_service):
        """Happy path: returned data matches the mock player attributes."""
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

    def test_get_players_by_guild_no_tier_returns_200(self, client, mock_player_service):
        """Happy path: returns list of players for guild (no tier filter)."""
        response = client.get("/api/v1/players/guild/67890")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["guild_id"] == 67890

    def test_get_players_by_guild_uses_repo_when_no_tier(self, mock_db_session, client, mock_player_service):
        """Service delegation: no tier -> uses player_repo.get_players_by_guild."""
        mock_session, _ = mock_db_session

        client.get("/api/v1/players/guild/67890")

        mock_player_service.player_repo.get_players_by_guild.assert_called_once_with(mock_session, 67890)
        mock_player_service.get_players_by_tier.assert_not_called()

    def test_get_players_by_guild_with_tier_filter(self, mock_db_session, client, mock_player_service):
        """Happy path: with tier filter -> uses get_players_by_tier."""
        mock_session, _ = mock_db_session

        response = client.get("/api/v1/players/guild/67890?tier=Gold")

        assert response.status_code == 200
        mock_player_service.get_players_by_tier.assert_called_once_with(mock_session, 67890, "Gold")
        mock_player_service.player_repo.get_players_by_guild.assert_not_called()

    def test_get_players_by_guild_pagination(self, client, mock_player_service):
        """Pagination: skip and limit query params are respected."""
        # Create 5 players
        players = [make_mock_player(id=i) for i in range(1, 6)]
        mock_player_service.player_repo.get_players_by_guild.return_value = players

        response = client.get("/api/v1/players/guild/67890?skip=1&limit=2")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    def test_get_players_by_guild_empty_result(self, client, mock_player_service):
        """Happy path: guild with no players returns empty list."""
        mock_player_service.player_repo.get_players_by_guild.return_value = []

        response = client.get("/api/v1/players/guild/99999")

        assert response.status_code == 200
        assert response.json() == []

    def test_get_players_by_guild_service_exception_returns_500(self, client, mock_player_service):
        """Server error: service raises Exception -> 500."""
        mock_player_service.player_repo.get_players_by_guild.side_effect = Exception("oops")

        response = client.get("/api/v1/players/guild/67890")

        assert response.status_code == 500
        assert "Failed to get players" in response.json()["detail"]


# ---------------------------------------------------------------------------
# TestUpdatePlayerCredits
# ---------------------------------------------------------------------------


class TestUpdatePlayerCredits:
    """Tests for PUT /players/{player_id}/credits -> update_player_credits."""

    def test_update_credits_returns_200(self, client, mock_player_service):
        """Happy path: valid request updates credits and returns 200."""
        response = client.put(
            "/api/v1/players/1/credits",
            json={"credits": 500, "update_lifetime": True},
        )

        assert response.status_code == 200
        assert response.json()["credits"] == 500

    def test_update_credits_delegates_to_service(self, mock_db_session, client, mock_player_service):
        """Service delegation: update_player_credits called with correct args."""
        mock_session, _ = mock_db_session

        client.put(
            "/api/v1/players/42/credits",
            json={"credits": 250, "update_lifetime": False},
        )

        mock_player_service.update_player_credits.assert_called_once_with(mock_session, 42, 250, False)

    def test_update_credits_value_error_returns_400(self, client, mock_player_service):
        """Validation error: service raises ValueError -> 400."""
        mock_player_service.update_player_credits.side_effect = ValueError("Player not found")

        response = client.put(
            "/api/v1/players/999/credits",
            json={"credits": 100},
        )

        assert response.status_code == 400
        assert "Player not found" in response.json()["detail"]

    def test_update_credits_service_exception_returns_500(self, client, mock_player_service):
        """Server error: service raises Exception -> 500."""
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

    def test_update_credits_default_update_lifetime_true(self, mock_db_session, client, mock_player_service):
        """Default: update_lifetime defaults to True when not provided."""
        mock_session, _ = mock_db_session

        client.put("/api/v1/players/1/credits", json={"credits": 100})

        mock_player_service.update_player_credits.assert_called_once_with(mock_session, 1, 100, True)


# ---------------------------------------------------------------------------
# TestUpdatePlayerXP
# ---------------------------------------------------------------------------


class TestUpdatePlayerXP:
    """Tests for PUT /players/{player_id}/xp -> update_player_xp."""

    def test_update_xp_returns_200(self, client, mock_player_service):
        """Happy path: valid request updates XP and returns 200."""
        response = client.put("/api/v1/players/1/xp", json={"xp": 100})

        assert response.status_code == 200
        assert response.json()["xp"] == 100

    def test_update_xp_delegates_to_service(self, mock_db_session, client, mock_player_service):
        """Service delegation: update_player_xp called with correct args."""
        mock_session, _ = mock_db_session

        client.put("/api/v1/players/7/xp", json={"xp": 500})

        mock_player_service.update_player_xp.assert_called_once_with(mock_session, 7, 500)

    def test_update_xp_value_error_returns_400(self, client, mock_player_service):
        """Validation error: service raises ValueError -> 400."""
        mock_player_service.update_player_xp.side_effect = ValueError("Player 999 not found")

        response = client.put("/api/v1/players/999/xp", json={"xp": 100})

        assert response.status_code == 400
        assert "Player 999 not found" in response.json()["detail"]

    def test_update_xp_service_exception_returns_500(self, client, mock_player_service):
        """Server error: service raises Exception -> 500."""
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

    def test_update_xp_zero_is_valid(self, client, mock_player_service):
        """Boundary: xp=0 is valid (ge=0)."""
        response = client.put("/api/v1/players/1/xp", json={"xp": 0})

        assert response.status_code == 200

    def test_update_xp_max_boundary_is_valid(self, client, mock_player_service):
        """Boundary: xp=1,000,000 is valid (le=1000000)."""
        response = client.put("/api/v1/players/1/xp", json={"xp": 1_000_000})

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# TestPrestigePlayer
# ---------------------------------------------------------------------------


class TestPrestigePlayer:
    """Tests for POST /players/{player_id}/prestige -> prestige_player."""

    def test_prestige_player_returns_200(self, client, mock_player_service):
        """Happy path: valid player prestige returns 200 with prestige result data."""
        response = client.post("/api/v1/players/1/prestige")

        assert response.status_code == 200
        data = response.json()
        assert data["prestige_count"] == 1
        assert data["level_before"] == 10
        assert data["division_before"] == "Elite"
        assert data["player_id"] == 1

    def test_prestige_player_delegates_to_service(self, mock_db_session, client, mock_player_service):
        """Service delegation: prestige_player called with correct player_id."""
        mock_session, _ = mock_db_session

        client.post("/api/v1/players/55/prestige")

        mock_player_service.prestige_player.assert_called_once_with(mock_session, 55)

    def test_prestige_player_value_error_returns_400(self, client, mock_player_service):
        """Validation error: service raises ValueError -> 400 (e.g. player below level 10)."""
        err_msg = "Player must be level 10 to prestige (current level: 5)"
        mock_player_service.prestige_player.side_effect = ValueError(err_msg)

        response = client.post("/api/v1/players/1/prestige")

        assert response.status_code == 400
        assert "level 10" in response.json()["detail"]

    def test_prestige_player_service_exception_returns_500(self, client, mock_player_service):
        """Server error: service raises Exception -> 500."""
        mock_player_service.prestige_player.side_effect = Exception("fatal error")

        response = client.post("/api/v1/players/1/prestige")

        assert response.status_code == 500
        assert "Failed to prestige player" in response.json()["detail"]

    def test_prestige_player_increments_prestige_count(self, client, mock_player_service):
        """Happy path: returned prestige result shows incremented prestige_count."""
        mock_player_service.prestige_player.return_value = {
            "player_id": 1,
            "prestige_count": 3,
            "level_before": 10,
            "division_before": "Elite",
        }

        response = client.post("/api/v1/players/1/prestige")

        data = response.json()
        assert data["prestige_count"] == 3
        assert data["level_before"] == 10


# ---------------------------------------------------------------------------
# TestGetPlayerStatistics
# ---------------------------------------------------------------------------


class TestGetPlayerStatistics:
    """Tests for GET /players/{player_id}/statistics -> get_player_statistics."""

    def test_get_statistics_returns_200(self, client, mock_player_service):
        """Happy path: valid player ID returns 200 with statistics."""
        response = client.get("/api/v1/players/1/statistics")

        assert response.status_code == 200
        data = response.json()
        assert data["player_id"] == 1
        assert data["tier"] == "Bronze"
        assert data["tier_level"] == 1
        assert "bounty_stats" in data
        assert "duel_stats" in data

    def test_get_statistics_delegates_to_service(self, mock_db_session, client, mock_player_service):
        """Service delegation: get_player_statistics called with correct player_id."""
        mock_session, _ = mock_db_session

        client.get("/api/v1/players/77/statistics")

        mock_player_service.get_player_statistics.assert_called_once_with(mock_session, 77)

    def test_get_statistics_value_error_returns_404(self, client, mock_player_service):
        """Not found: service raises ValueError -> 404."""
        mock_player_service.get_player_statistics.side_effect = ValueError("Player 999 not found")

        response = client.get("/api/v1/players/999/statistics")

        assert response.status_code == 404
        assert "Player 999 not found" in response.json()["detail"]

    def test_get_statistics_service_exception_returns_500(self, client, mock_player_service):
        """Server error: service raises Exception -> 500."""
        mock_player_service.get_player_statistics.side_effect = Exception("query failed")

        response = client.get("/api/v1/players/1/statistics")

        assert response.status_code == 500
        assert "Failed to get player statistics" in response.json()["detail"]

    def test_get_statistics_response_shape(self, client, mock_player_service):
        """Response shape: all expected top-level fields present."""
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

    def test_get_statistics_bounty_and_duel_stats_are_dicts(self, client, mock_player_service):
        """Response validation: bounty_stats and duel_stats are dictionaries."""
        response = client.get("/api/v1/players/1/statistics")

        data = response.json()
        assert isinstance(data["bounty_stats"], dict)
        assert isinstance(data["duel_stats"], dict)

    def test_get_statistics_custom_values(self, client, mock_player_service):
        """Happy path: response reflects the stats returned by the service."""
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


# ---------------------------------------------------------------------------
# TestTransferCredits
# ---------------------------------------------------------------------------


class TestTransferCredits:
    """Tests for POST /players/transfer."""

    def test_valid_transfer_returns_200(self, client, mock_player_service):
        """Happy path: valid transfer returns 200 with correct response body."""
        mock_player_service.transfer_credits = AsyncMock(
            return_value={
                "source_player_id": 1,
                "target_player_id": 2,
                "amount": 100,
                "source_remaining_credits": 400,
                "target_new_credits": 200,
            }
        )

        response = client.post(
            "/api/v1/players/transfer",
            json={"source_player_id": 1, "target_player_id": 2, "amount": 100},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["source_player_id"] == 1
        assert data["target_player_id"] == 2
        assert data["amount"] == 100
        assert data["source_remaining_credits"] == 400
        assert data["target_new_credits"] == 200

    def test_insufficient_credits_returns_400(self, client, mock_player_service):
        """Service raises ValueError for insufficient credits -> 400."""
        mock_player_service.transfer_credits = AsyncMock(
            side_effect=ValueError("Insufficient credits: have 50, need 200")
        )

        response = client.post(
            "/api/v1/players/transfer",
            json={"source_player_id": 1, "target_player_id": 2, "amount": 200},
        )

        assert response.status_code == 400
        assert "Insufficient credits" in response.json()["detail"]

    def test_self_transfer_returns_400(self, client, mock_player_service):
        """Service raises ValueError for self-transfer -> 400."""
        mock_player_service.transfer_credits = AsyncMock(side_effect=ValueError("Cannot transfer credits to yourself"))

        response = client.post(
            "/api/v1/players/transfer",
            json={"source_player_id": 5, "target_player_id": 5, "amount": 50},
        )

        assert response.status_code == 400
        assert "Cannot transfer credits to yourself" in response.json()["detail"]

    def test_zero_amount_returns_422(self, client):
        """Request validation: amount=0 is rejected by Pydantic -> 422."""
        response = client.post(
            "/api/v1/players/transfer",
            json={"source_player_id": 1, "target_player_id": 2, "amount": 0},
        )

        assert response.status_code == 422

    def test_negative_amount_returns_422(self, client):
        """Request validation: negative amount is rejected by Pydantic -> 422."""
        response = client.post(
            "/api/v1/players/transfer",
            json={"source_player_id": 1, "target_player_id": 2, "amount": -5},
        )

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# TestGetPromotionStatus
# ---------------------------------------------------------------------------


class TestGetPromotionStatus:
    """Tests for GET /players/{player_id}/promotion-status -> get_promotion_status."""

    def test_get_promotion_status_returns_200(self, client, mock_player_service):
        """Happy path: returns 200 with promotion status data."""
        response = client.get("/api/v1/players/1/promotion-status")

        assert response.status_code == 200
        data = response.json()
        assert data["player_id"] == 1
        assert data["current_tier"] == "Bronze"
        assert data["can_promote"] is True
        assert data["next_tier"] == "Silver"

    def test_get_promotion_status_delegates_to_service(self, mock_db_session, client, mock_player_service):
        """Service delegation: get_promotion_status called with correct player_id."""
        mock_session, _ = mock_db_session

        client.get("/api/v1/players/42/promotion-status")

        mock_player_service.get_promotion_status.assert_called_once_with(mock_session, 42)

    def test_get_promotion_status_not_found_returns_404(self, client, mock_player_service):
        """Not found: service raises ValueError with 'not found' -> 404."""
        mock_player_service.get_promotion_status.side_effect = ValueError("Player 999 not found")

        response = client.get("/api/v1/players/999/promotion-status")

        assert response.status_code == 404
        assert "999" in response.json()["detail"]

    def test_get_promotion_status_service_exception_returns_500(self, client, mock_player_service):
        """Server error: service raises Exception -> 500."""
        mock_player_service.get_promotion_status.side_effect = Exception("db error")

        response = client.get("/api/v1/players/1/promotion-status")

        assert response.status_code == 500
        assert "Failed to get promotion status" in response.json()["detail"]

    def test_get_promotion_status_response_shape(self, client, mock_player_service):
        """Response shape: all expected fields present."""
        response = client.get("/api/v1/players/1/promotion-status")

        data = response.json()
        expected_keys = {
            "player_id",
            "current_tier",
            "current_tier_level",
            "eligible_tier",
            "next_tier",
            "can_promote",
            "xp",
            "xp_threshold_for_next",
            "xp_surplus_for_next",
        }
        assert expected_keys.issubset(data.keys())

    def test_get_promotion_status_platinum_player(self, client, mock_player_service):
        """Platinum player shows can_promote=False, next_tier=None."""
        mock_player_service.get_promotion_status.return_value = {
            "player_id": 1,
            "current_tier": "Platinum",
            "current_tier_level": 4,
            "eligible_tier": "Platinum",
            "next_tier": None,
            "can_promote": False,
            "xp": 20000,
            "xp_threshold_for_next": None,
            "xp_surplus_for_next": None,
        }

        response = client.get("/api/v1/players/1/promotion-status")

        assert response.status_code == 200
        data = response.json()
        assert data["next_tier"] is None
        assert data["can_promote"] is False


# ---------------------------------------------------------------------------
# TestPromotePlayer
# ---------------------------------------------------------------------------


class TestPromotePlayer:
    """Tests for PUT /players/{player_id}/promote -> promote_player."""

    def test_promote_player_returns_200(self, client, mock_player_service):
        """Happy path: valid promotion returns 200 with promote result."""
        response = client.put("/api/v1/players/1/promote")

        assert response.status_code == 200
        data = response.json()
        assert data["player_id"] == 1
        assert data["old_tier"] == "Bronze"
        assert data["new_tier"] == "Silver"
        assert data["xp"] == 1500

    def test_promote_player_delegates_to_service(self, mock_db_session, client, mock_player_service):
        """Service delegation: promote_player called with correct player_id."""
        mock_session, _ = mock_db_session

        client.put("/api/v1/players/7/promote")

        mock_player_service.promote_player.assert_called_once_with(mock_session, 7)

    def test_promote_not_eligible_returns_400(self, client, mock_player_service):
        """Not eligible: service raises ValueError -> 400."""
        mock_player_service.promote_player.side_effect = ValueError(
            "Not eligible for promotion. Need 1,000 XP for Silver, currently have 500"
        )

        response = client.put("/api/v1/players/1/promote")

        assert response.status_code == 400
        assert "Not eligible" in response.json()["detail"]

    def test_promote_at_max_tier_returns_400(self, client, mock_player_service):
        """Already at max tier: service raises ValueError -> 400."""
        mock_player_service.promote_player.side_effect = ValueError("Already at maximum tier (Platinum)")

        response = client.put("/api/v1/players/1/promote")

        assert response.status_code == 400
        assert "maximum tier" in response.json()["detail"]

    def test_promote_player_not_found_returns_404(self, client, mock_player_service):
        """Not found: service raises ValueError with 'not found' -> 404."""
        mock_player_service.promote_player.side_effect = ValueError("Player 999 not found")

        response = client.put("/api/v1/players/999/promote")

        assert response.status_code == 404

    def test_promote_player_service_exception_returns_500(self, client, mock_player_service):
        """Server error: service raises Exception -> 500."""
        mock_player_service.promote_player.side_effect = Exception("unexpected error")

        response = client.put("/api/v1/players/1/promote")

        assert response.status_code == 500
        assert "Failed to promote player" in response.json()["detail"]

    def test_promote_player_response_shape(self, client, mock_player_service):
        """Response shape: all expected fields present."""
        response = client.put("/api/v1/players/1/promote")

        data = response.json()
        expected_keys = {"player_id", "old_tier", "new_tier", "xp", "eligible_for_next", "next_tier"}
        assert expected_keys.issubset(data.keys())

    def test_promote_player_eligible_for_next(self, client, mock_player_service):
        """eligible_for_next is True when XP qualifies for further promotion."""
        mock_player_service.promote_player.return_value = {
            "player_id": 1,
            "old_tier": "Bronze",
            "new_tier": "Silver",
            "xp": 20000,
            "eligible_for_next": True,
            "next_tier": "Gold",
        }

        response = client.put("/api/v1/players/1/promote")

        data = response.json()
        assert data["eligible_for_next"] is True
        assert data["next_tier"] == "Gold"


# ===========================================================================
# Gap 1: Empty-State / Null-Result Tests — Players
# ===========================================================================


class TestGetPlayerStatisticsNonexistent:
    """Gap 1: statistics endpoint for a player that does not exist → 404 (not 500)."""

    def test_get_player_statistics_nonexistent_player(self, client, mock_player_service):
        """GET /players/{id}/statistics for a missing player → 404.

        Previously this could return a 500 if the service raised an unhandled error.
        The router should catch ValueError and return 404 instead.
        """
        mock_player_service.get_player_statistics.side_effect = ValueError("Player 99999 not found")

        response = client.get("/api/v1/players/99999/statistics")

        assert response.status_code == 404
        data = response.json()
        assert "99999" in data["detail"] or "not found" in data["detail"].lower()

    def test_get_player_statistics_nonexistent_player_not_500(self, client, mock_player_service):
        """Confirms a missing player for statistics returns <500 status code."""
        mock_player_service.get_player_statistics.side_effect = ValueError("Player 77777 not found")

        response = client.get("/api/v1/players/77777/statistics")

        # Must not be an internal server error for a simple "not found" case
        assert response.status_code != 500


class TestGetPromotionStatusNonexistent:
    """Gap 1: promotion-status endpoint for a player that does not exist → 404 (not 500)."""

    def test_get_promotion_status_nonexistent_player(self, client, mock_player_service):
        """GET /players/{id}/promotion-status for a missing player → 404.

        Ensures the endpoint does not propagate as a 500 when the player is absent.
        """
        mock_player_service.get_promotion_status.side_effect = ValueError("Player 88888 not found")

        response = client.get("/api/v1/players/88888/promotion-status")

        assert response.status_code == 404
        data = response.json()
        assert "88888" in data["detail"] or "not found" in data["detail"].lower()

    def test_get_promotion_status_nonexistent_player_not_500(self, client, mock_player_service):
        """Confirms a missing player for promotion-status returns <500 status code."""
        mock_player_service.get_promotion_status.side_effect = ValueError("Player 55555 not found")

        response = client.get("/api/v1/players/55555/promotion-status")

        assert response.status_code != 500


# ===========================================================================
# GET /players/{player_id}/loadout
# ===========================================================================


class TestGetPlayerLoadout:
    """Tests for GET /players/{player_id}/loadout endpoint."""

    def _make_mock_player_ship(
        self, ship_id=1, ship_name="Betty", nickname=None, weapons=None, modules=None, turrets=None
    ):
        from unittest.mock import MagicMock

        ps = MagicMock()
        ps.id = ship_id
        ps.ship_name = ship_name
        ps.nickname = nickname
        ps.weapons = weapons or ["Nirai Impulse EX 1"]
        ps.modules = modules or ["E2 Exoclad", "Telta Quickscan"]
        ps.turrets = turrets or []
        return ps

    def _make_mock_ship_static(self, name="Betty", armour=200, emoji="🛸"):
        from unittest.mock import MagicMock

        ship = MagicMock()
        ship.name = name
        ship.armour = armour
        ship.emoji = emoji
        return ship

    def _make_mock_module(
        self, name="E2 Exoclad", module_type="ArmourModule", value=1070, tech_level=1, extra_atts=None
    ):
        from unittest.mock import MagicMock

        mod = MagicMock()
        mod.name = name
        mod.type = module_type
        mod.value = value
        mod.tech_level = tech_level
        mod.emoji = f"<:{name.lower().replace(' ', '')}:123>"
        mod.extra_atts = extra_atts or {"armour": 40}
        return mod

    def _make_mock_weapon(self, name="Nirai Impulse EX 1", dps=7.5, value=2500):
        from unittest.mock import MagicMock

        wpn = MagicMock()
        wpn.name = name
        wpn.dps = dps
        wpn.value = value
        wpn.emoji = f"<:{name.lower().replace(' ', '')}:456>"
        return wpn

    def test_loadout_player_not_found_returns_404(self, client, mock_player_service):
        """GET /players/999/loadout → 404 when player not found."""
        mock_player_service.player_repo.get_by_id.return_value = None

        response = client.get("/api/v1/players/999/loadout")

        assert response.status_code == 404

    def test_loadout_no_active_ship_returns_no_ship_message(self, client, mock_player_service):
        """GET /players/1/loadout → 200 with no-ship message when active_ship_id is None."""
        player = make_mock_player(active_ship_id=None)
        mock_player_service.player_repo.get_by_id.return_value = player

        response = client.get("/api/v1/players/1/loadout")

        assert response.status_code == 200
        data = response.json()
        assert data["ship_name"] is None
        assert data["message"] == "No active ship"

    def test_loadout_active_ship_query_is_executed(self, client, mock_player_service, mock_db_session):
        """GET /players/1/loadout with active ship → endpoint attempts to query the DB for the ship."""
        from unittest.mock import AsyncMock, MagicMock

        mock_session, _ = mock_db_session

        # Player has an active ship
        player = make_mock_player(active_ship_id=10)
        mock_player_service.player_repo.get_by_id.return_value = player

        # Make execute return None (no PlayerShip found) — endpoint falls back to "No active ship"
        no_result = MagicMock()
        no_result.scalars.return_value.first.return_value = None
        mock_session.execute = AsyncMock(return_value=no_result)

        response = client.get("/api/v1/players/1/loadout")

        assert response.status_code == 200
        data = response.json()
        # PlayerShip lookup returned None → treated as no active ship
        assert data["ship_name"] is None

    def test_loadout_server_error_returns_500(self, client, mock_player_service):
        """GET /players/1/loadout → 500 when unexpected exception occurs."""
        mock_player_service.player_repo.get_by_id.side_effect = Exception("DB exploded")

        response = client.get("/api/v1/players/1/loadout")

        assert response.status_code == 500
        assert "Failed to get player loadout" in response.json()["detail"]

    def test_loadout_without_include_cargo_has_empty_cargo(self, client, mock_player_service):
        """GET /players/1/loadout without include_cargo → cargo field is empty list."""
        player = make_mock_player(active_ship_id=None)
        mock_player_service.player_repo.get_by_id.return_value = player

        response = client.get("/api/v1/players/1/loadout")

        assert response.status_code == 200
        data = response.json()
        assert "cargo" in data
        assert data["cargo"] == []

    def test_loadout_cargo_field_present_by_default(self, client, mock_player_service):
        """GET /players/1/loadout response always includes cargo field in schema."""
        player = make_mock_player(active_ship_id=None)
        mock_player_service.player_repo.get_by_id.return_value = player

        response = client.get("/api/v1/players/1/loadout")

        assert response.status_code == 200
        data = response.json()
        # cargo field must always be present in the response
        assert "cargo" in data


class TestGetPlayerLoadoutWithCargo:
    """Tests for GET /players/{player_id}/loadout?include_cargo=true (cargo hold)."""

    def test_loadout_include_cargo_false_returns_empty_cargo(self, client, mock_player_service):
        """include_cargo=false → cargo list is empty even if player has inventory items."""
        player = make_mock_player(active_ship_id=None)
        mock_player_service.player_repo.get_by_id.return_value = player

        response = client.get("/api/v1/players/1/loadout?include_cargo=false")

        assert response.status_code == 200
        data = response.json()
        assert data["cargo"] == []

    def test_loadout_include_cargo_true_no_active_ship_still_returns_empty_cargo(self, client, mock_player_service):
        """include_cargo=true but no active ship → cargo is empty (early return path)."""
        player = make_mock_player(active_ship_id=None)
        mock_player_service.player_repo.get_by_id.return_value = player

        response = client.get("/api/v1/players/1/loadout?include_cargo=true")

        assert response.status_code == 200
        data = response.json()
        assert data["ship_name"] is None
        assert data["message"] == "No active ship"
        # cargo field must always be present
        assert "cargo" in data

    def _make_active_ship_db_responses(self, mock_session, player_ship, ship_static):
        """Helper: wire up mock_session.execute to return player_ship then ship_static."""
        from unittest.mock import AsyncMock, MagicMock

        player_ship_result = MagicMock()
        player_ship_result.scalars.return_value.first.return_value = player_ship

        ship_result = MagicMock()
        ship_result.scalars.return_value.first.return_value = ship_static

        mock_session.execute = AsyncMock(side_effect=[player_ship_result, ship_result])

    def _make_player_ship(self):
        from unittest.mock import MagicMock

        player_ship = MagicMock()
        player_ship.id = 10
        player_ship.ship_name = "Betty"
        player_ship.nickname = None
        player_ship.weapons = []
        player_ship.modules = []
        player_ship.turrets = []
        return player_ship

    def _make_ship_static(self):
        from unittest.mock import MagicMock

        ship_static = MagicMock()
        ship_static.name = "Betty"
        ship_static.armour = 200
        ship_static.emoji = "🛸"
        return ship_static

    def test_loadout_with_cargo_returns_items(self, client, mock_player_service, mock_db_session):
        """include_cargo=true with active ship & inventory → cargo list populated."""
        from unittest.mock import AsyncMock, MagicMock

        mock_session, _ = mock_db_session

        # Player with an active ship
        player = make_mock_player(active_ship_id=10)
        mock_player_service.player_repo.get_by_id.return_value = player

        player_ship = self._make_player_ship()
        ship_static = self._make_ship_static()
        self._make_active_ship_db_responses(mock_session, player_ship, ship_static)

        # Inventory items
        inv_item_1 = MagicMock()
        inv_item_1.item_name = "Nirai Impulse EX 1"
        inv_item_1.item_type = "weapon"
        inv_item_1.quantity = 2

        inv_item_2 = MagicMock()
        inv_item_2.item_name = "E2 Exoclad"
        inv_item_2.item_type = "module"
        inv_item_2.quantity = 1

        game_item_with_emoji = MagicMock()
        game_item_with_emoji.emoji = "⚡"

        # Patch at the source module level (deferred imports)
        mock_item_repo = MagicMock()
        mock_item_repo.get_by_name = AsyncMock(return_value=game_item_with_emoji)

        mock_inv_repo = MagicMock()
        mock_inv_repo.get_player_items = AsyncMock(return_value=[inv_item_1, inv_item_2])

        from unittest.mock import patch

        with (
            patch("persist.repositories.item_repository.ItemRepository", return_value=mock_item_repo),
            patch("persist.repositories.inventory_repository.InventoryRepository", return_value=mock_inv_repo),
        ):
            response = client.get("/api/v1/players/1/loadout?include_cargo=true")

        assert response.status_code == 200
        data = response.json()
        assert len(data["cargo"]) == 2

        # Check cargo item names
        cargo_names = [c["item_name"] for c in data["cargo"]]
        assert "Nirai Impulse EX 1" in cargo_names
        assert "E2 Exoclad" in cargo_names

    def test_loadout_cargo_item_schema(self, client, mock_player_service, mock_db_session):
        """include_cargo=true → cargo items have correct schema fields."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_session, _ = mock_db_session

        player = make_mock_player(active_ship_id=10)
        mock_player_service.player_repo.get_by_id.return_value = player

        player_ship = self._make_player_ship()
        ship_static = self._make_ship_static()
        self._make_active_ship_db_responses(mock_session, player_ship, ship_static)

        inv_item = MagicMock()
        inv_item.item_name = "Blast Rifle"
        inv_item.item_type = "weapon"
        inv_item.quantity = 3

        game_item = MagicMock()
        game_item.emoji = "🔫"

        mock_item_repo = MagicMock()
        mock_item_repo.get_by_name = AsyncMock(return_value=game_item)

        mock_inv_repo = MagicMock()
        mock_inv_repo.get_player_items = AsyncMock(return_value=[inv_item])

        with (
            patch("persist.repositories.item_repository.ItemRepository", return_value=mock_item_repo),
            patch("persist.repositories.inventory_repository.InventoryRepository", return_value=mock_inv_repo),
        ):
            response = client.get("/api/v1/players/1/loadout?include_cargo=true")

        assert response.status_code == 200
        data = response.json()
        cargo = data["cargo"]
        assert len(cargo) == 1

        item = cargo[0]
        assert item["item_name"] == "Blast Rifle"
        assert item["item_type"] == "weapon"
        assert item["quantity"] == 3
        assert "emoji" in item

    def test_loadout_cargo_item_no_game_item_emoji_is_none(self, client, mock_player_service, mock_db_session):
        """include_cargo=true → cargo item emoji is None when item has no matching game item."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_session, _ = mock_db_session

        player = make_mock_player(active_ship_id=10)
        mock_player_service.player_repo.get_by_id.return_value = player

        player_ship = self._make_player_ship()
        ship_static = self._make_ship_static()
        self._make_active_ship_db_responses(mock_session, player_ship, ship_static)

        inv_item = MagicMock()
        inv_item.item_name = "Unknown Item"
        inv_item.item_type = "module"
        inv_item.quantity = 1

        mock_item_repo = MagicMock()
        mock_item_repo.get_by_name = AsyncMock(return_value=None)

        mock_inv_repo = MagicMock()
        mock_inv_repo.get_player_items = AsyncMock(return_value=[inv_item])

        with (
            patch("persist.repositories.item_repository.ItemRepository", return_value=mock_item_repo),
            patch("persist.repositories.inventory_repository.InventoryRepository", return_value=mock_inv_repo),
        ):
            response = client.get("/api/v1/players/1/loadout?include_cargo=true")

        assert response.status_code == 200
        data = response.json()
        cargo = data["cargo"]
        assert len(cargo) == 1
        assert cargo[0]["emoji"] is None

    def test_loadout_cargo_empty_when_no_inventory(self, client, mock_player_service, mock_db_session):
        """include_cargo=true but player has no inventory items → cargo is empty list."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_session, _ = mock_db_session

        player = make_mock_player(active_ship_id=10)
        mock_player_service.player_repo.get_by_id.return_value = player

        player_ship = self._make_player_ship()
        ship_static = self._make_ship_static()
        self._make_active_ship_db_responses(mock_session, player_ship, ship_static)

        mock_item_repo = MagicMock()
        mock_item_repo.get_by_name = AsyncMock(return_value=None)

        mock_inv_repo = MagicMock()
        mock_inv_repo.get_player_items = AsyncMock(return_value=[])

        with (
            patch("persist.repositories.item_repository.ItemRepository", return_value=mock_item_repo),
            patch("persist.repositories.inventory_repository.InventoryRepository", return_value=mock_inv_repo),
        ):
            response = client.get("/api/v1/players/1/loadout?include_cargo=true")

        assert response.status_code == 200
        data = response.json()
        assert data["cargo"] == []

    def test_loadout_cargo_quantity_preserved(self, client, mock_player_service, mock_db_session):
        """Cargo item quantity > 1 is correctly preserved in response."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_session, _ = mock_db_session

        player = make_mock_player(active_ship_id=10)
        mock_player_service.player_repo.get_by_id.return_value = player

        player_ship = self._make_player_ship()
        ship_static = self._make_ship_static()
        self._make_active_ship_db_responses(mock_session, player_ship, ship_static)

        inv_item = MagicMock()
        inv_item.item_name = "Rapid Fire Cannon"
        inv_item.item_type = "turret"
        inv_item.quantity = 5

        mock_item_repo = MagicMock()
        mock_item_repo.get_by_name = AsyncMock(return_value=None)

        mock_inv_repo = MagicMock()
        mock_inv_repo.get_player_items = AsyncMock(return_value=[inv_item])

        with (
            patch("persist.repositories.item_repository.ItemRepository", return_value=mock_item_repo),
            patch("persist.repositories.inventory_repository.InventoryRepository", return_value=mock_inv_repo),
        ):
            response = client.get("/api/v1/players/1/loadout?include_cargo=true")

        assert response.status_code == 200
        data = response.json()
        assert data["cargo"][0]["quantity"] == 5


# ---------------------------------------------------------------------------
# TestCooldownReset
# ---------------------------------------------------------------------------


class TestCooldownReset:
    """Tests for PUT /players/{guild_id}/{user_id}/cooldown/reset."""

    def _make_user(self, user_id=42, discord_id=99999):
        from unittest.mock import MagicMock

        u = MagicMock()
        u.id = user_id
        u.discord_id = discord_id
        return u

    def _make_player_obj(self, player_id=7, bounty_cooldown_end=None):
        from unittest.mock import MagicMock

        p = MagicMock()
        p.id = player_id
        p.bounty_cooldown_end = bounty_cooldown_end
        return p

    def test_reset_cooldown_success_returns_200(self, client, mock_db_session):
        """Happy path: user and player found → cooldown reset → 200."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_session, _ = mock_db_session

        user = self._make_user()
        player = self._make_player_obj()

        mock_user_repo = MagicMock()
        mock_user_repo.get_by_id = AsyncMock(return_value=user)
        mock_player_repo = MagicMock()
        mock_player_repo.get_by_user_and_guild = AsyncMock(return_value=player)

        with (
            patch("persist.repositories.user_repository.UserRepository", return_value=mock_user_repo),
            patch("persist.repositories.player_repository.PlayerRepository", return_value=mock_player_repo),
        ):
            response = client.put("/api/v1/players/12345/99999/cooldown/reset")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "player" in data["message"].lower()

    def test_reset_cooldown_sets_cooldown_to_none(self, client, mock_db_session):
        """Cooldown reset sets bounty_cooldown_end to None on the player object."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from datetime import UTC, datetime, timedelta

        mock_session, _ = mock_db_session

        future = datetime.now(UTC) + timedelta(seconds=120)
        user = self._make_user()
        player = self._make_player_obj(bounty_cooldown_end=future)

        mock_user_repo = MagicMock()
        mock_user_repo.get_by_id = AsyncMock(return_value=user)
        mock_player_repo = MagicMock()
        mock_player_repo.get_by_user_and_guild = AsyncMock(return_value=player)

        with (
            patch("persist.repositories.user_repository.UserRepository", return_value=mock_user_repo),
            patch("persist.repositories.player_repository.PlayerRepository", return_value=mock_player_repo),
        ):
            client.put("/api/v1/players/12345/99999/cooldown/reset")

        assert player.bounty_cooldown_end is None

    def test_reset_cooldown_user_not_found_returns_404(self, client, mock_db_session):
        """Returns 404 when Discord user is not found."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_session, _ = mock_db_session

        mock_user_repo = MagicMock()
        mock_user_repo.get_by_id = AsyncMock(return_value=None)
        mock_player_repo = MagicMock()

        with (
            patch("persist.repositories.user_repository.UserRepository", return_value=mock_user_repo),
            patch("persist.repositories.player_repository.PlayerRepository", return_value=mock_player_repo),
        ):
            response = client.put("/api/v1/players/12345/99999/cooldown/reset")

        assert response.status_code == 404

    def test_reset_cooldown_player_not_found_returns_404(self, client, mock_db_session):
        """Returns 404 when player is not found for the given user+guild."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_session, _ = mock_db_session

        user = self._make_user()
        mock_user_repo = MagicMock()
        mock_user_repo.get_by_id = AsyncMock(return_value=user)
        mock_player_repo = MagicMock()
        mock_player_repo.get_by_user_and_guild = AsyncMock(return_value=None)

        with (
            patch("persist.repositories.user_repository.UserRepository", return_value=mock_user_repo),
            patch("persist.repositories.player_repository.PlayerRepository", return_value=mock_player_repo),
        ):
            response = client.put("/api/v1/players/12345/99999/cooldown/reset")

        assert response.status_code == 404
