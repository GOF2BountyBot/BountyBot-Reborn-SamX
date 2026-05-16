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
            "tier_before": "Platinum",
            "xp_before": 50000,
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
    service.demote_player = AsyncMock(
        return_value={
            "player_id": 1,
            "old_tier": "Silver",
            "new_tier": "Bronze",
            "xp": 1500,
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

        mock_player_service.get_or_create_player.assert_called_once_with(
            mock_session, 99999, 11111, "SomeUser", display_name=None
        )

    def test_create_player_without_username(self, mock_db_session, client, mock_player_service):
        """Happy path: discord_username is optional (None)."""
        mock_session, _ = mock_db_session

        response = client.post(
            "/api/v1/players/",
            json={"discord_id": 12345, "guild_id": 67890},
        )

        assert response.status_code == 201
        mock_player_service.get_or_create_player.assert_called_once_with(
            mock_session, 12345, 67890, None, display_name=None
        )

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

        mock_player_service.player_repo.get_players_by_guild.assert_called_once_with(
            mock_session, 67890, active_within_days=None
        )
        mock_player_service.get_players_by_tier.assert_not_called()

    def test_get_players_by_guild_with_tier_filter(self, mock_db_session, client, mock_player_service):
        """Happy path: with tier filter -> uses get_players_by_tier."""
        mock_session, _ = mock_db_session

        response = client.get("/api/v1/players/guild/67890?tier=Gold")

        assert response.status_code == 200
        mock_player_service.get_players_by_tier.assert_called_once_with(
            mock_session, 67890, "Gold", active_within_days=None
        )
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

    def test_get_players_by_guild_active_within_days_returns_200(self, mock_db_session, client, mock_player_service):
        """active_within_days=7: returns 200 and passes filter param to repo."""
        mock_session, _ = mock_db_session

        response = client.get("/api/v1/players/guild/67890?active_within_days=7")

        assert response.status_code == 200
        mock_player_service.player_repo.get_players_by_guild.assert_called_once_with(
            mock_session, 67890, active_within_days=7
        )

    def test_get_players_by_guild_active_within_days_zero_passes_zero(
        self, mock_db_session, client, mock_player_service
    ):
        """active_within_days=0: valid (ge=0), passes 0 to repo (no filter applied by repo)."""
        mock_session, _ = mock_db_session

        response = client.get("/api/v1/players/guild/67890?active_within_days=0")

        assert response.status_code == 200
        mock_player_service.player_repo.get_players_by_guild.assert_called_once_with(
            mock_session, 67890, active_within_days=0
        )

    def test_get_players_by_guild_active_within_days_negative_returns_422(self, client, mock_player_service):
        """active_within_days < 0: rejected by Pydantic ge=0 constraint -> 422."""
        response = client.get("/api/v1/players/guild/67890?active_within_days=-1")

        assert response.status_code == 422

    def test_get_players_by_guild_active_within_days_with_tier(
        self, mock_db_session, client, mock_player_service
    ):
        """active_within_days + tier: both params passed through to service."""
        mock_session, _ = mock_db_session

        response = client.get("/api/v1/players/guild/67890?tier=Gold&active_within_days=7")

        assert response.status_code == 200
        mock_player_service.get_players_by_tier.assert_called_once_with(
            mock_session, 67890, "Gold", active_within_days=7
        )


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
        # B.48: level_before/division_before replaced with tier_before/xp_before.
        assert data["tier_before"] == "Platinum"
        assert data["xp_before"] == 50000
        assert data["player_id"] == 1

    def test_prestige_player_delegates_to_service(self, mock_db_session, client, mock_player_service):
        """Service delegation: prestige_player called with correct player_id."""
        mock_session, _ = mock_db_session

        client.post("/api/v1/players/55/prestige")

        mock_player_service.prestige_player.assert_called_once_with(mock_session, 55)

    def test_prestige_player_value_error_returns_400(self, client, mock_player_service):
        """Validation error: service raises ValueError -> 400 (e.g. XP below prestige threshold)."""
        err_msg = "Not eligible for prestige. Need 50,000 XP to prestige, currently have 35"
        mock_player_service.prestige_player.side_effect = ValueError(err_msg)

        response = client.post("/api/v1/players/1/prestige")

        assert response.status_code == 400
        assert "prestige" in response.json()["detail"].lower()

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
            "tier_before": "Platinum",
            "xp_before": 70000,
        }

        response = client.post("/api/v1/players/1/prestige")

        data = response.json()
        assert data["prestige_count"] == 3
        assert data["tier_before"] == "Platinum"


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
# GET /players/{player_id}/loadout — unified LoadoutResponse schema
# ===========================================================================


@pytest.fixture
def mock_loadout_response_service():
    """Mock LoadoutResponseService injected via FastAPI dependency override."""
    from unittest.mock import AsyncMock, MagicMock

    service = MagicMock()
    service.build_player_loadout = AsyncMock()
    return service


@pytest.fixture
def loadout_test_app(mock_loadout_response_service, mock_player_service):
    """App that overrides both PlayerService and LoadoutResponseService dependencies."""
    from api.routers.players import (
        get_loadout_response_service as _get_loadout_rs,
    )

    app = FastAPI()
    app.include_router(_players_router, prefix="/api/v1")
    app.dependency_overrides[_get_player_service] = lambda: mock_player_service
    app.dependency_overrides[_get_loadout_rs] = lambda: mock_loadout_response_service
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def loadout_client(loadout_test_app):
    return TestClient(loadout_test_app)


class TestGetPlayerLoadout:
    """Tests for GET /players/{player_id}/loadout — unified schema."""

    @staticmethod
    def _make_player_response(**overrides):
        """Build a minimal valid LoadoutResponse dict (as the service would return)."""
        from api.schemas.loadout_schema import LoadoutResponse

        defaults = dict(subject_kind="player", subject_name="Alice", player_id=1)
        defaults.update(overrides)
        return LoadoutResponse(**defaults)

    def test_loadout_player_not_found_returns_404(self, loadout_client, mock_loadout_response_service):
        """Service returns None → router raises 404."""
        mock_loadout_response_service.build_player_loadout.return_value = None

        response = loadout_client.get("/api/v1/players/999/loadout")

        assert response.status_code == 404
        assert "999" in response.json()["detail"]

    def test_loadout_no_active_ship_returns_no_ship_message(self, loadout_client, mock_loadout_response_service):
        """Service returns message='No active ship' → 200 with that message."""
        mock_loadout_response_service.build_player_loadout.return_value = self._make_player_response(
            message="No active ship"
        )

        response = loadout_client.get("/api/v1/players/1/loadout")

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "No active ship"
        assert data["ship_name"] is None
        assert data["subject_kind"] == "player"

    def test_loadout_server_error_returns_500(self, loadout_client, mock_loadout_response_service):
        """Service raises → router returns 500."""
        mock_loadout_response_service.build_player_loadout.side_effect = Exception("DB exploded")

        response = loadout_client.get("/api/v1/players/1/loadout")

        assert response.status_code == 500
        assert "Failed to get player loadout" in response.json()["detail"]

    def test_loadout_delegates_with_include_cargo_false(self, loadout_client, mock_loadout_response_service):
        """include_cargo is passed to the service (default false)."""
        mock_loadout_response_service.build_player_loadout.return_value = self._make_player_response()

        loadout_client.get("/api/v1/players/1/loadout")

        call = mock_loadout_response_service.build_player_loadout.call_args
        assert call.kwargs["include_cargo"] is False

    def test_loadout_delegates_with_include_cargo_true(self, loadout_client, mock_loadout_response_service):
        mock_loadout_response_service.build_player_loadout.return_value = self._make_player_response()

        loadout_client.get("/api/v1/players/1/loadout?include_cargo=true")

        call = mock_loadout_response_service.build_player_loadout.call_args
        assert call.kwargs["include_cargo"] is True

    def test_loadout_passes_viewer_discord_id_to_service(self, loadout_client, mock_loadout_response_service):
        """viewer_discord_id query param is passed to the service verbatim."""
        mock_loadout_response_service.build_player_loadout.return_value = self._make_player_response()

        loadout_client.get("/api/v1/players/1/loadout?viewer_discord_id=402296276617527306")

        call = mock_loadout_response_service.build_player_loadout.call_args
        assert call.kwargs["viewer_discord_id"] == 402296276617527306

    def test_loadout_viewer_discord_id_optional(self, loadout_client, mock_loadout_response_service):
        """viewer_discord_id is optional — omitting it passes None to the service."""
        mock_loadout_response_service.build_player_loadout.return_value = self._make_player_response()

        loadout_client.get("/api/v1/players/1/loadout")

        call = mock_loadout_response_service.build_player_loadout.call_args
        assert call.kwargs["viewer_discord_id"] is None


class TestLoadoutResponseShape:
    """The new endpoint returns the unified LoadoutResponse schema (spec §2.1)."""

    @staticmethod
    def _make_full_response(**overrides):
        from api.schemas.loadout_schema import (
            EffectItem,
            LoadoutModuleItem,
            LoadoutResponse,
            LoadoutWeaponItem,
            ShipStats,
        )

        defaults = dict(
            subject_kind="player",
            subject_name="Alice",
            subject_mention="<@123>",
            player_id=1,
            user_discord_id=123,
            ship_name="Wraith",
            ship_nickname="Betty",
            ship_icon="https://cdn/wraith.png",
            ship_emoji="<:wraith:1>",
            thumbnail_url="https://cdn/wraith.png",
            ship_stats=ShipStats(
                armour=95,
                cargo=20,
                handling=60,
                hp=320,
                dps=42.5,
                total_value=15000,
                max_primaries=2,
                max_secondaries=0,
                max_turrets=0,
                max_modules=4,
            ),
            weapons=[
                LoadoutWeaponItem(name="Pulse Laser", emoji="<:pulse:1>", dps=12.0, value=1000),
            ],
            turrets=[],
            modules=[
                LoadoutModuleItem(
                    name="D'iol",
                    emoji="<:diol:1>",
                    type="ArmourModule",
                    value=500,
                    tech_level=1,
                    effects=[EffectItem(label="Armour", value="40")],
                    combat_tier="combat",
                ),
                LoadoutModuleItem(
                    name="AutoPacker",
                    emoji="<:pack:1>",
                    type="CompressorModule",
                    value=300,
                    tech_level=2,
                    effects=[EffectItem(label="Cargo Bonus", value="×1.25")],
                    combat_tier="utility",
                ),
            ],
            cargo=[],
            cargo_total_count=0,
        )
        defaults.update(overrides)
        return LoadoutResponse(**defaults)

    def test_full_response_shape(self, loadout_client, mock_loadout_response_service):
        mock_loadout_response_service.build_player_loadout.return_value = self._make_full_response()

        response = loadout_client.get("/api/v1/players/1/loadout")

        assert response.status_code == 200
        data = response.json()
        # Discriminator
        assert data["subject_kind"] == "player"
        assert data["subject_name"] == "Alice"
        assert data["subject_mention"] == "<@123>"
        # Identity
        assert data["player_id"] == 1
        assert data["user_discord_id"] == 123
        # Ship
        assert data["ship_name"] == "Wraith"
        assert data["ship_nickname"] == "Betty"
        assert data["ship_icon"] == "https://cdn/wraith.png"
        assert data["thumbnail_url"] == "https://cdn/wraith.png"
        # Stats
        assert data["ship_stats"]["armour"] == 95
        assert data["ship_stats"]["cargo"] == 20
        assert data["ship_stats"]["handling"] == 60
        assert data["ship_stats"]["hp"] == 320
        assert data["ship_stats"]["dps"] == 42.5
        assert data["ship_stats"]["total_value"] == 15000
        assert data["ship_stats"]["max_primaries"] == 2
        assert data["ship_stats"]["max_modules"] == 4

    def test_module_includes_effects_and_combat_tier(self, loadout_client, mock_loadout_response_service):
        """Each LoadoutModuleItem carries an ordered effects list and a combat_tier tag."""
        mock_loadout_response_service.build_player_loadout.return_value = self._make_full_response()

        response = loadout_client.get("/api/v1/players/1/loadout")

        data = response.json()
        modules = data["modules"]
        assert len(modules) == 2

        armour_mod = modules[0]
        assert armour_mod["type"] == "ArmourModule"
        assert armour_mod["combat_tier"] == "combat"
        assert armour_mod["effects"] == [{"label": "Armour", "value": "40"}]

        compressor = modules[1]
        assert compressor["type"] == "CompressorModule"
        assert compressor["combat_tier"] == "utility"
        assert compressor["effects"] == [{"label": "Cargo Bonus", "value": "×1.25"}]

    def test_weapons_have_dps_and_value(self, loadout_client, mock_loadout_response_service):
        mock_loadout_response_service.build_player_loadout.return_value = self._make_full_response()

        response = loadout_client.get("/api/v1/players/1/loadout")

        weapons = response.json()["weapons"]
        assert len(weapons) == 1
        assert weapons[0]["name"] == "Pulse Laser"
        assert weapons[0]["dps"] == 12.0
        assert weapons[0]["value"] == 1000

    def test_empty_cargo_defaults(self, loadout_client, mock_loadout_response_service):
        """When include_cargo=False (default), response has cargo=[] and cargo_total_count=0."""
        mock_loadout_response_service.build_player_loadout.return_value = self._make_full_response()

        response = loadout_client.get("/api/v1/players/1/loadout")

        data = response.json()
        assert data["cargo"] == []
        assert data["cargo_total_count"] == 0

    def test_response_always_includes_cargo_field(self, loadout_client, mock_loadout_response_service):
        """The cargo field is always present in the schema even when empty."""
        mock_loadout_response_service.build_player_loadout.return_value = self._make_full_response()

        response = loadout_client.get("/api/v1/players/1/loadout")

        data = response.json()
        assert "cargo" in data
        assert "cargo_total_count" in data


class TestGetPlayerLoadoutWithCargo:
    """Tests for GET /players/{player_id}/loadout?include_cargo=true."""

    @staticmethod
    def _make_response_with_cargo(**overrides):
        from api.schemas.loadout_schema import CargoItem, LoadoutResponse

        defaults = dict(
            subject_kind="player",
            subject_name="Alice",
            player_id=1,
            ship_name="Wraith",
            cargo=[
                CargoItem(item_name="Nirai Impulse EX 1", item_type="weapon", quantity=2, emoji="⚡"),
                CargoItem(item_name="E2 Exoclad", item_type="module", quantity=1, emoji="🛡️"),
            ],
            cargo_total_count=3,
        )
        defaults.update(overrides)
        return LoadoutResponse(**defaults)

    def test_include_cargo_true_populates_cargo(self, loadout_client, mock_loadout_response_service):
        mock_loadout_response_service.build_player_loadout.return_value = self._make_response_with_cargo()

        response = loadout_client.get("/api/v1/players/1/loadout?include_cargo=true")

        assert response.status_code == 200
        data = response.json()
        assert data["cargo_total_count"] == 3
        cargo = data["cargo"]
        assert len(cargo) == 2
        names = {c["item_name"] for c in cargo}
        assert names == {"Nirai Impulse EX 1", "E2 Exoclad"}

    def test_cargo_item_schema_fields_present(self, loadout_client, mock_loadout_response_service):
        from api.schemas.loadout_schema import CargoItem, LoadoutResponse

        mock_loadout_response_service.build_player_loadout.return_value = LoadoutResponse(
            subject_kind="player",
            subject_name="Alice",
            player_id=1,
            cargo=[CargoItem(item_name="Blast Rifle", item_type="weapon", quantity=3, emoji="🔫")],
            cargo_total_count=3,
        )

        response = loadout_client.get("/api/v1/players/1/loadout?include_cargo=true")

        item = response.json()["cargo"][0]
        assert item["item_name"] == "Blast Rifle"
        assert item["item_type"] == "weapon"
        assert item["quantity"] == 3
        assert item["emoji"] == "🔫"

    def test_cargo_emoji_null_when_missing(self, loadout_client, mock_loadout_response_service):
        from api.schemas.loadout_schema import CargoItem, LoadoutResponse

        mock_loadout_response_service.build_player_loadout.return_value = LoadoutResponse(
            subject_kind="player",
            subject_name="Alice",
            player_id=1,
            cargo=[CargoItem(item_name="Unknown", item_type="module", quantity=1)],
            cargo_total_count=1,
        )

        response = loadout_client.get("/api/v1/players/1/loadout?include_cargo=true")

        assert response.json()["cargo"][0]["emoji"] is None

    def test_include_cargo_false_returns_empty_cargo(self, loadout_client, mock_loadout_response_service):
        from api.schemas.loadout_schema import LoadoutResponse

        # Simulate service honoring include_cargo=false by returning empty cargo list.
        mock_loadout_response_service.build_player_loadout.return_value = LoadoutResponse(
            subject_kind="player", subject_name="Alice", player_id=1, cargo=[], cargo_total_count=0
        )

        response = loadout_client.get("/api/v1/players/1/loadout?include_cargo=false")

        assert response.status_code == 200
        assert response.json()["cargo"] == []


# ---------------------------------------------------------------------------
# LoadoutResponseService — unit-level tests (exercise business logic directly)
# ---------------------------------------------------------------------------


class TestLoadoutResponseServicePlayerPath:
    """Integration-lite tests for LoadoutResponseService.build_player_loadout."""

    def _make_service_with_repos(self, *, player, player_ship, ship, module_factory, user=None):
        """Build a LoadoutResponseService with its repos stubbed to deterministic values.

        `module_factory(name)` returns a mock Module-like object (or None).
        `ship` may be a SimpleNamespace or None.
        """
        from unittest.mock import AsyncMock, MagicMock

        from services.loadout_response_service import LoadoutResponseService

        svc = LoadoutResponseService()
        svc.player_repo = MagicMock()
        svc.player_repo.get_by_id = AsyncMock(return_value=player)

        svc.user_repo = MagicMock()
        svc.user_repo.get_by_id = AsyncMock(return_value=user)

        svc.item_repo = MagicMock()
        svc.item_repo.get_by_name = AsyncMock(return_value=None)

        svc.inventory_repo = MagicMock()
        svc.inventory_repo.get_player_items = AsyncMock(return_value=[])

        svc.bounty_repo = MagicMock()
        svc.criminal_repo = MagicMock()

        # Build a db session whose execute() dispatches based on the statement type.
        from sqlalchemy.sql.elements import BinaryExpression  # noqa: F401

        async def _execute(stmt):
            # Crude but deterministic: read the table name from the statement text
            from persist.models.module import Module as ModuleModel
            from persist.models.player_ship import PlayerShip as PlayerShipModel
            from persist.models.ship import Ship as ShipModel

            result = MagicMock()
            model = stmt.column_descriptions[0]["entity"] if stmt.column_descriptions else None
            if model is PlayerShipModel:
                result.scalars.return_value.first.return_value = player_ship
            elif model is ShipModel:
                result.scalars.return_value.first.return_value = ship
            elif model is ModuleModel:
                # Pull name from the WHERE clause via right side of the comparison
                try:
                    name = stmt.whereclause.right.value
                except Exception:
                    name = None
                result.scalars.return_value.first.return_value = module_factory(name)
            else:
                result.scalars.return_value.first.return_value = None
            return result

        db = MagicMock()
        db.execute = _execute
        return svc, db

    async def test_player_not_found_returns_none(self):
        from unittest.mock import AsyncMock, MagicMock

        from services.loadout_response_service import LoadoutResponseService

        svc = LoadoutResponseService()
        svc.player_repo = MagicMock()
        svc.player_repo.get_by_id = AsyncMock(return_value=None)

        result = await svc.build_player_loadout(MagicMock(), 999, include_cargo=False)
        assert result is None

    async def test_player_no_active_ship_returns_message(self):
        from unittest.mock import AsyncMock, MagicMock

        from services.loadout_response_service import LoadoutResponseService

        player = MagicMock()
        player.id = 1
        player.user_id = 42
        player.active_ship_id = None

        user = MagicMock()
        user.discord_username = "Alice"

        svc = LoadoutResponseService()
        svc.player_repo = MagicMock()
        svc.player_repo.get_by_id = AsyncMock(return_value=player)
        svc.user_repo = MagicMock()
        svc.user_repo.get_by_id = AsyncMock(return_value=user)

        result = await svc.build_player_loadout(MagicMock(), 1, include_cargo=False)
        assert result is not None
        assert result.message == "No active ship"
        assert result.subject_name == "Alice"
        assert result.subject_kind == "player"

    async def test_viewer_discord_id_populates_mention(self):
        from unittest.mock import AsyncMock, MagicMock

        from services.loadout_response_service import LoadoutResponseService

        player = MagicMock()
        player.id = 1
        player.user_id = 42
        player.active_ship_id = None

        svc = LoadoutResponseService()
        svc.player_repo = MagicMock()
        svc.player_repo.get_by_id = AsyncMock(return_value=player)
        svc.user_repo = MagicMock()
        svc.user_repo.get_by_id = AsyncMock(return_value=None)

        result = await svc.build_player_loadout(MagicMock(), 1, include_cargo=False, viewer_discord_id=999)
        assert result.subject_mention == "<@999>"

    async def test_full_loadout_with_modules_populates_effects(self):
        """End-to-end: modules are resolved, effects pre-formatted, combat_tier set."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        player = SimpleNamespace(id=1, user_id=42, active_ship_id=10)
        user = SimpleNamespace(discord_username="Alice")

        player_ship = SimpleNamespace(
            id=10,
            ship_name="Wraith",
            nickname="Betty",
            weapons=["Pulse Laser"],
            modules=["D'iol", "AutoPacker 2"],
            turrets=[],
        )
        ship = SimpleNamespace(
            name="Wraith",
            armour=95,
            cargo=20,
            emoji="<:wraith:1>",
            icon="https://cdn/wraith.png",
            handling=60,
            max_primaries=2,
            max_secondaries=0,
            max_turrets=0,
            max_modules=4,
        )

        def module_factory(name):
            if name == "D'iol":
                return SimpleNamespace(
                    name="D'iol",
                    emoji="<:diol:1>",
                    type="ArmourModule",
                    value=500,
                    tech_level=1,
                    extra_atts={"armour": 40},
                )
            if name == "AutoPacker 2":
                return SimpleNamespace(
                    name="AutoPacker 2",
                    emoji="<:pack:1>",
                    type="CompressorModule",
                    value=300,
                    tech_level=2,
                    extra_atts={"cargoMultiplier": 1.25},
                )
            return None

        svc, db = self._make_service_with_repos(
            player=player,
            player_ship=player_ship,
            ship=ship,
            module_factory=module_factory,
            user=user,
        )
        # Pulse Laser needs to resolve via ItemRepository
        svc.item_repo.get_by_name = AsyncMock(return_value=SimpleNamespace(emoji="<:pulse:1>", dps=12.0, value=1000))

        result = await svc.build_player_loadout(db, 1, include_cargo=False)

        assert result is not None
        assert result.subject_kind == "player"
        assert result.subject_name == "Alice"
        assert result.ship_name == "Wraith"
        assert result.ship_nickname == "Betty"
        assert result.ship_icon == "https://cdn/wraith.png"
        assert result.thumbnail_url == "https://cdn/wraith.png"
        assert result.ship_stats.armour == 95
        # Effective cargo = base 20 × 1.25 (CompressorModule) = 25
        assert result.ship_stats.cargo == 25
        assert result.ship_stats.handling == 60
        # HP = base 95 + armour bonus 40 + shield 0 = 135
        assert result.ship_stats.hp == 135
        # DPS = 12.0 (only weapon)
        assert result.ship_stats.dps == 12.0
        # Total value = 1000 (pulse) + 500 + 300 = 1800
        assert result.ship_stats.total_value == 1800
        assert result.ship_stats.max_primaries == 2
        assert result.ship_stats.max_modules == 4

        # Module effects
        assert len(result.modules) == 2
        diol = result.modules[0]
        assert diol.type == "ArmourModule"
        assert diol.combat_tier == "combat"
        assert [(e.label, e.value) for e in diol.effects] == [("Armour", "40")]
        compressor = result.modules[1]
        assert compressor.type == "CompressorModule"
        assert compressor.combat_tier == "utility"
        assert [(e.label, e.value) for e in compressor.effects] == [("Cargo Bonus", "×1.25")]

    async def test_include_cargo_false_returns_empty_cargo(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock

        player = SimpleNamespace(id=1, user_id=42, active_ship_id=None)
        svc = self._make_service_with_repos(
            player=player, player_ship=None, ship=None, module_factory=lambda _: None, user=None
        )[0]
        svc.inventory_repo.get_player_items = AsyncMock(
            return_value=[
                SimpleNamespace(
                    item_name="X",
                    item_type="weapon",
                    quantity=1,
                )
            ]
        )

        result = await svc.build_player_loadout(MagicMock(), 1, include_cargo=False)
        assert result.cargo == []
        assert result.cargo_total_count == 0
        # inventory_repo NOT called because active_ship_id is None → early return anyway
        svc.inventory_repo.get_player_items.assert_not_called()

    async def test_unknown_module_type_renders_name_only(self):
        """Spec §2.5: unknown module type → empty effects list, still in response."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        player = SimpleNamespace(id=1, user_id=42, active_ship_id=10)
        user = SimpleNamespace(discord_username="Alice")
        player_ship = SimpleNamespace(
            id=10,
            ship_name="Wraith",
            nickname=None,
            weapons=[],
            modules=["FutureMod"],
            turrets=[],
        )
        ship = SimpleNamespace(
            name="Wraith",
            armour=100,
            cargo=10,
            emoji=None,
            icon=None,
            handling=50,
            max_primaries=1,
            max_secondaries=0,
            max_turrets=0,
            max_modules=1,
        )

        def module_factory(name):
            if name == "FutureMod":
                return SimpleNamespace(
                    name="FutureMod",
                    emoji="<:fm:1>",
                    type="SomeFutureModule",
                    value=100,
                    tech_level=1,
                    extra_atts={"unknown_key": 42},
                )
            return None

        svc, db = self._make_service_with_repos(
            player=player,
            player_ship=player_ship,
            ship=ship,
            module_factory=module_factory,
            user=user,
        )
        svc.item_repo.get_by_name = AsyncMock(return_value=None)

        result = await svc.build_player_loadout(db, 1, include_cargo=False)
        mod = result.modules[0]
        assert mod.type == "SomeFutureModule"
        assert mod.effects == []
        # Unknown types default to combat tier (fail-safe visible)
        assert mod.combat_tier == "combat"


# ---------------------------------------------------------------------------
# End of loadout test section
# ---------------------------------------------------------------------------


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

        _, _ = mock_db_session

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
        from datetime import UTC, datetime, timedelta
        from unittest.mock import AsyncMock, MagicMock, patch

        _, _ = mock_db_session

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

        _, _ = mock_db_session

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

        _, _ = mock_db_session

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

    def test_reset_tier_change_cooldown_clears_tier_change_cooldown_end(self, client, mock_db_session):
        """cooldown_type=tier_change clears tier_change_cooldown_end, not bounty_cooldown_end."""
        from datetime import UTC, datetime, timedelta
        from unittest.mock import AsyncMock, MagicMock, patch

        _, _ = mock_db_session

        future = datetime.now(UTC) + timedelta(hours=12)
        user = self._make_user()
        player = self._make_player_obj()
        player.tier_change_cooldown_end = future
        player.bounty_cooldown_end = future  # should be untouched

        mock_user_repo = MagicMock()
        mock_user_repo.get_by_id = AsyncMock(return_value=user)
        mock_player_repo = MagicMock()
        mock_player_repo.get_by_user_and_guild = AsyncMock(return_value=player)

        with (
            patch("persist.repositories.user_repository.UserRepository", return_value=mock_user_repo),
            patch("persist.repositories.player_repository.PlayerRepository", return_value=mock_player_repo),
        ):
            response = client.put("/api/v1/players/12345/99999/cooldown/reset?cooldown_type=tier_change")

        assert response.status_code == 200
        assert player.tier_change_cooldown_end is None
        assert player.bounty_cooldown_end == future  # untouched

    def test_reset_all_clears_both_cooldowns(self, client, mock_db_session):
        """cooldown_type=all clears both bounty_cooldown_end and tier_change_cooldown_end."""
        from datetime import UTC, datetime, timedelta
        from unittest.mock import AsyncMock, MagicMock, patch

        _, _ = mock_db_session

        future = datetime.now(UTC) + timedelta(hours=12)
        user = self._make_user()
        player = self._make_player_obj(bounty_cooldown_end=future)
        player.tier_change_cooldown_end = future

        mock_user_repo = MagicMock()
        mock_user_repo.get_by_id = AsyncMock(return_value=user)
        mock_player_repo = MagicMock()
        mock_player_repo.get_by_user_and_guild = AsyncMock(return_value=player)

        with (
            patch("persist.repositories.user_repository.UserRepository", return_value=mock_user_repo),
            patch("persist.repositories.player_repository.PlayerRepository", return_value=mock_player_repo),
        ):
            response = client.put("/api/v1/players/12345/99999/cooldown/reset?cooldown_type=all")

        assert response.status_code == 200
        assert player.bounty_cooldown_end is None
        assert player.tier_change_cooldown_end is None

    def test_invalid_cooldown_type_returns_400(self, client, mock_db_session):
        """Unknown cooldown_type value returns 400."""
        from unittest.mock import AsyncMock, MagicMock, patch

        _, _ = mock_db_session

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
            response = client.put("/api/v1/players/12345/99999/cooldown/reset?cooldown_type=invalid")

        assert response.status_code == 400


# ===========================================================================
# TestDemotePlayer
# ===========================================================================


class TestDemotePlayer:
    """Tests for PUT /players/{player_id}/demote -> demote_player."""

    def test_demote_player_returns_200(self, client, mock_player_service):
        """Happy path: valid demotion returns 200 with demote result."""
        response = client.put("/api/v1/players/1/demote")

        assert response.status_code == 200
        data = response.json()
        assert data["player_id"] == 1
        assert data["old_tier"] == "Silver"
        assert data["new_tier"] == "Bronze"

    def test_demote_player_delegates_to_service(self, mock_db_session, client, mock_player_service):
        """Service delegation: demote_player called with correct player_id."""
        mock_session, _ = mock_db_session

        client.put("/api/v1/players/7/demote")

        mock_player_service.demote_player.assert_called_once_with(mock_session, 7)

    def test_demote_at_min_tier_returns_400(self, client, mock_player_service):
        """Already at minimum tier: service raises ValueError -> 400."""
        mock_player_service.demote_player.side_effect = ValueError("Already at minimum tier (Bronze)")

        response = client.put("/api/v1/players/1/demote")

        assert response.status_code == 400
        assert "minimum tier" in response.json()["detail"]

    def test_demote_player_not_found_returns_404(self, client, mock_player_service):
        """Player not found: service raises ValueError with 'not found' -> 404."""
        mock_player_service.demote_player.side_effect = ValueError("Player 999 not found")

        response = client.put("/api/v1/players/999/demote")

        assert response.status_code == 404

    def test_demote_on_cooldown_returns_429(self, client, mock_player_service):
        """Tier-change cooldown active: TierChangeCooldownError -> 429."""
        from datetime import UTC, datetime, timedelta

        from services.player_service import TierChangeCooldownError

        cooldown_end = datetime.now(UTC) + timedelta(hours=20)
        mock_player_service.demote_player.side_effect = TierChangeCooldownError(
            "Cooldown active", cooldown_end=cooldown_end
        )

        response = client.put("/api/v1/players/1/demote")

        assert response.status_code == 429
        detail = response.json()["detail"]
        assert "cooldown_end" in detail

    def test_demote_player_response_shape(self, client, mock_player_service):
        """Response shape: all expected DemoteResponse fields present."""
        response = client.put("/api/v1/players/1/demote")

        data = response.json()
        assert {"player_id", "old_tier", "new_tier", "xp"}.issubset(data.keys())

    def test_demote_service_exception_returns_500(self, client, mock_player_service):
        """Unexpected service error -> 500."""
        mock_player_service.demote_player.side_effect = Exception("db error")

        response = client.put("/api/v1/players/1/demote")

        assert response.status_code == 500
        assert "Failed to demote" in response.json()["detail"]


# ===========================================================================
# TestPromotePlayerCooldown — 429 path for /promote
# ===========================================================================


class TestPromotePlayerCooldown:
    """Tests for the HTTP 429 path on PUT /players/{player_id}/promote."""

    def test_promote_on_cooldown_returns_429(self, client, mock_player_service):
        """TierChangeCooldownError from service -> 429 with cooldown_end in detail."""
        from datetime import UTC, datetime, timedelta

        from services.player_service import TierChangeCooldownError

        cooldown_end = datetime.now(UTC) + timedelta(hours=20)
        mock_player_service.promote_player.side_effect = TierChangeCooldownError(
            "Cooldown active", cooldown_end=cooldown_end
        )

        response = client.put("/api/v1/players/1/promote")

        assert response.status_code == 429
        detail = response.json()["detail"]
        assert "cooldown_end" in detail

    def test_promote_429_detail_contains_iso_timestamp(self, client, mock_player_service):
        """The 429 detail dict includes a parseable ISO cooldown_end timestamp."""
        from datetime import UTC, datetime, timedelta

        from services.player_service import TierChangeCooldownError

        cooldown_end = datetime.now(UTC) + timedelta(hours=20)
        mock_player_service.promote_player.side_effect = TierChangeCooldownError(
            "Cooldown active", cooldown_end=cooldown_end
        )

        response = client.put("/api/v1/players/1/promote")

        detail = response.json()["detail"]
        # Should be parseable as ISO datetime
        parsed = datetime.fromisoformat(detail["cooldown_end"])
        assert parsed > datetime.now(UTC)


# ===========================================================================
# TestCombatPreflightEndpoint
# ===========================================================================


class TestCombatPreflightEndpoint:
    """Tests for GET /players/{player_id}/combat-preflight."""

    def _make_preflight_result(self, verdict="green", player_win_rate=0.9, criminal_win_rate=0.1):
        from types import SimpleNamespace

        from services.combat_preflight_service import PreflightVerdict

        verdict_map = {
            "green": PreflightVerdict.GREEN,
            "yellow": PreflightVerdict.YELLOW,
            "red": PreflightVerdict.RED,
            "no_data": PreflightVerdict.NO_DATA,
        }
        return SimpleNamespace(
            verdict=verdict_map[verdict],
            player_win_rate=player_win_rate,
            criminal_win_rate=criminal_win_rate,
            sims_run=20,
            target_tier="Silver",
            sample_size=3,
        )

    def test_combat_preflight_returns_200(self, client, mock_db_session):
        """GET combat-preflight returns 200 with verdict and win rates."""
        from unittest.mock import AsyncMock, MagicMock, patch

        _, _ = mock_db_session

        player = MagicMock()
        player.guild_id = 1

        preflight_result = self._make_preflight_result("green")

        with (
            patch("api.routers.players.CombatPreflightService") as mock_cpf_cls,
            patch("services.player_service.PlayerService") as mock_ps_cls,
        ):
            mock_ps = MagicMock()
            mock_ps.player_repo.get_by_id = AsyncMock(return_value=player)
            mock_ps_cls.return_value = mock_ps

            mock_cpf = MagicMock()
            mock_cpf.estimate = AsyncMock(return_value=preflight_result)
            mock_cpf_cls.return_value = mock_cpf

            response = client.get("/api/v1/players/1/combat-preflight?target_tier=Silver")

        assert response.status_code == 200
        data = response.json()
        assert data["verdict"] == "green"
        assert "player_win_rate" in data
        assert "criminal_win_rate" in data

    def test_combat_preflight_player_not_found_returns_404(self, client, mock_db_session):
        """Returns 404 when player is not found."""
        from unittest.mock import AsyncMock, MagicMock, patch

        _, _ = mock_db_session

        with patch("services.player_service.PlayerService") as mock_ps_cls:
            mock_ps = MagicMock()
            mock_ps.player_repo.get_by_id = AsyncMock(return_value=None)
            mock_ps_cls.return_value = mock_ps

            response = client.get("/api/v1/players/999/combat-preflight?target_tier=Silver")

        assert response.status_code == 404

    def test_combat_preflight_response_shape(self, client, mock_db_session):
        """Response includes all expected keys."""
        from unittest.mock import AsyncMock, MagicMock, patch

        _, _ = mock_db_session

        player = MagicMock()
        player.guild_id = 1
        preflight_result = self._make_preflight_result("no_data", 0.0, 0.0)

        with (
            patch("api.routers.players.CombatPreflightService") as mock_cpf_cls,
            patch("services.player_service.PlayerService") as mock_ps_cls,
        ):
            mock_ps = MagicMock()
            mock_ps.player_repo.get_by_id = AsyncMock(return_value=player)
            mock_ps_cls.return_value = mock_ps

            mock_cpf = MagicMock()
            mock_cpf.estimate = AsyncMock(return_value=preflight_result)
            mock_cpf_cls.return_value = mock_cpf

            response = client.get("/api/v1/players/1/combat-preflight?target_tier=Silver")

        data = response.json()
        expected_keys = {"verdict", "player_win_rate", "criminal_win_rate", "sims_run", "target_tier", "sample_size"}
        assert expected_keys.issubset(data.keys())
