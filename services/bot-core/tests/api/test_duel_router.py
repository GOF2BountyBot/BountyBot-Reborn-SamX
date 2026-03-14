"""Tests for the duel API router endpoints.

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


def make_mock_duel(**overrides):
    """Build a MagicMock that looks like a DuelRequest ORM object."""
    now = datetime(2026, 1, 1, 12, 0, 0)
    expires = datetime(2026, 1, 2, 12, 0, 0)
    defaults = dict(
        id=1,
        guild_id=67890,
        challenger_id=100,
        target_id=200,
        stakes=500,
        status="pending",
        created_at=now,
        expires_at=expires,
    )
    defaults.update(overrides)
    duel = MagicMock()
    for k, v in defaults.items():
        setattr(duel, k, v)
    return duel


def make_mock_player(player_id=100, credits=1000, name="TestPlayer"):
    """Build a MagicMock that looks like a Player ORM object."""
    player = MagicMock()
    player.id = player_id
    player.credits = credits
    player.name = name
    return player


def make_mock_fight_result(
    winner_name="Ship A",
    loser_name="Ship B",
    is_stalemate=False,
):
    """Build a MagicMock that looks like a FightResult."""
    fight = MagicMock()
    fight.winner_name = winner_name
    fight.loser_name = loser_name
    fight.is_stalemate = is_stalemate
    return fight


def make_mock_accept_result(
    duel_id=1,
    is_stalemate=False,
    winner_name="Ship A",
    loser_name="Ship B",
    credits_transferred=500,
    stakes=500,
    challenger_id=100,
    challenger_credits=1500,
    target_id=200,
    target_credits=500,
):
    """Build the dict returned by DuelService.accept_duel."""
    fight = make_mock_fight_result(
        winner_name=winner_name,
        loser_name=loser_name,
        is_stalemate=is_stalemate,
    )
    challenger = make_mock_player(player_id=challenger_id, credits=challenger_credits)
    target = make_mock_player(player_id=target_id, credits=target_credits)
    return {
        "fight_results": fight,
        "challenger": challenger,
        "target": target,
        "stakes": stakes,
        "credits_transferred": credits_transferred,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_duel_service():
    service = AsyncMock()
    service.create_challenge = AsyncMock(return_value=make_mock_duel())
    service.accept_duel = AsyncMock(return_value=make_mock_accept_result())
    service.reject_duel = AsyncMock(return_value=make_mock_duel(status="rejected"))
    return service


@pytest.fixture
def test_app(mock_duel_service):
    app = FastAPI()
    from api.routers.duels import get_duel_service
    from api.routers.duels import router as duels_router

    app.include_router(duels_router, prefix="/api/v1")
    app.dependency_overrides[get_duel_service] = lambda: mock_duel_service
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
# 1. POST /duels/challenge
# ===========================================================================


class TestCreateChallenge:
    """Tests for POST /api/v1/duels/challenge."""

    @patch("api.routers.duels.get_db_session")
    def test_create_challenge_success(self, mock_get_db, client, mock_duel_service):
        """Returns the created duel request on success."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.create_challenge = AsyncMock(
            return_value=make_mock_duel(
                id=1,
                challenger_id=100,
                target_id=200,
                stakes=500,
                status="pending",
            )
        )

        response = client.post(
            "/api/v1/duels/challenge",
            json={
                "challenger_id": 100,
                "target_id": 200,
                "stakes": 500,
                "guild_id": 67890,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 1
        assert data["challenger_id"] == 100
        assert data["target_id"] == 200
        assert data["stakes"] == 500
        assert data["status"] == "pending"

    @patch("api.routers.duels.get_db_session")
    def test_create_challenge_insufficient_credits(self, mock_get_db, client, mock_duel_service):
        """Returns 400 when challenger or target lacks sufficient credits."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.create_challenge = AsyncMock(
            side_effect=ValueError("Challenger has insufficient credits: has 100, needs 500.")
        )

        response = client.post(
            "/api/v1/duels/challenge",
            json={
                "challenger_id": 100,
                "target_id": 200,
                "stakes": 500,
                "guild_id": 67890,
            },
        )

        assert response.status_code == 400
        assert "insufficient credits" in response.json()["detail"].lower()

    @patch("api.routers.duels.get_db_session")
    def test_create_challenge_self_challenge(self, mock_get_db, client, mock_duel_service):
        """Returns 400 when a player tries to challenge themselves."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.create_challenge = AsyncMock(
            side_effect=ValueError("A player cannot challenge themselves to a duel.")
        )

        response = client.post(
            "/api/v1/duels/challenge",
            json={
                "challenger_id": 100,
                "target_id": 100,
                "stakes": 0,
                "guild_id": 67890,
            },
        )

        assert response.status_code == 400
        assert "challenge themselves" in response.json()["detail"].lower()

    @patch("api.routers.duels.get_db_session")
    def test_create_challenge_duplicate_pending(self, mock_get_db, client, mock_duel_service):
        """Returns 400 when a pending duel between the same players already exists."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.create_challenge = AsyncMock(
            side_effect=ValueError(
                "A pending duel already exists between player 100 and player 200 in guild 67890."
            )
        )

        response = client.post(
            "/api/v1/duels/challenge",
            json={
                "challenger_id": 100,
                "target_id": 200,
                "stakes": 0,
                "guild_id": 67890,
            },
        )

        assert response.status_code == 400
        assert "pending duel already exists" in response.json()["detail"].lower()


# ===========================================================================
# 2. POST /duels/{duel_id}/accept
# ===========================================================================


class TestAcceptDuel:
    """Tests for POST /api/v1/duels/{duel_id}/accept."""

    @patch("api.routers.duels.get_db_session")
    def test_accept_duel_winner_result(self, mock_get_db, client, mock_duel_service):
        """Returns fight result with winner/loser and credit transfer on decisive outcome."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.accept_duel = AsyncMock(
            return_value=make_mock_accept_result(
                duel_id=1,
                is_stalemate=False,
                winner_name="Ship A",
                loser_name="Ship B",
                credits_transferred=500,
                stakes=500,
                challenger_id=100,
                challenger_credits=1500,
                target_id=200,
                target_credits=500,
            )
        )

        response = client.post("/api/v1/duels/1/accept")

        assert response.status_code == 200
        data = response.json()
        assert data["duel_id"] == 1
        assert data["is_stalemate"] is False
        assert data["winner_name"] == "Ship A"
        assert data["loser_name"] == "Ship B"
        assert data["credits_transferred"] == 500
        assert data["challenger_credits"] == 1500
        assert data["target_credits"] == 500

    @patch("api.routers.duels.get_db_session")
    def test_accept_duel_stalemate(self, mock_get_db, client, mock_duel_service):
        """Returns stalemate result with zero credits transferred."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.accept_duel = AsyncMock(
            return_value=make_mock_accept_result(
                is_stalemate=True,
                winner_name="",
                loser_name="",
                credits_transferred=0,
                stakes=500,
                challenger_credits=1000,
                target_credits=1000,
            )
        )

        response = client.post("/api/v1/duels/1/accept")

        assert response.status_code == 200
        data = response.json()
        assert data["is_stalemate"] is True
        assert data["credits_transferred"] == 0

    @patch("api.routers.duels.get_db_session")
    def test_accept_duel_not_found(self, mock_get_db, client, mock_duel_service):
        """Returns 404 when the duel does not exist."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.accept_duel = AsyncMock(
            side_effect=ValueError("Duel request with ID 999 not found.")
        )

        response = client.post("/api/v1/duels/999/accept")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @patch("api.routers.duels.get_db_session")
    def test_accept_duel_already_resolved(self, mock_get_db, client, mock_duel_service):
        """Returns 400 when the duel is not in pending status."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.accept_duel = AsyncMock(
            side_effect=ValueError(
                "Duel 1 cannot be accepted — current status is 'completed'."
            )
        )

        response = client.post("/api/v1/duels/1/accept")

        assert response.status_code == 400
        assert "cannot be accepted" in response.json()["detail"].lower()


# ===========================================================================
# 3. POST /duels/{duel_id}/reject
# ===========================================================================


class TestRejectDuel:
    """Tests for POST /api/v1/duels/{duel_id}/reject."""

    @patch("api.routers.duels.get_db_session")
    def test_reject_duel_success(self, mock_get_db, client, mock_duel_service):
        """Returns the updated duel with rejected status."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.reject_duel = AsyncMock(
            return_value=make_mock_duel(
                id=1,
                status="rejected",
            )
        )

        response = client.post("/api/v1/duels/1/reject")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 1
        assert data["status"] == "rejected"

    @patch("api.routers.duels.get_db_session")
    def test_reject_duel_not_found(self, mock_get_db, client, mock_duel_service):
        """Returns 404 when the duel does not exist."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.reject_duel = AsyncMock(
            side_effect=ValueError("Duel request with ID 999 not found.")
        )

        response = client.post("/api/v1/duels/999/reject")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @patch("api.routers.duels.get_db_session")
    def test_reject_duel_already_resolved(self, mock_get_db, client, mock_duel_service):
        """Returns 400 when duel cannot be rejected (not in pending status)."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.reject_duel = AsyncMock(
            side_effect=ValueError(
                "Duel 1 cannot be rejected — current status is 'completed'."
            )
        )

        response = client.post("/api/v1/duels/1/reject")

        assert response.status_code == 400
        assert "cannot be rejected" in response.json()["detail"].lower()
