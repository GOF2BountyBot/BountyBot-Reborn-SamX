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
    """Build a MagicMock that looks like a DuelRequest ORM object.

    All fields that DuelRequestResponse validates are explicitly set so that
    model_validate(duel) does not trip on MagicMock attribute access.
    challenger_name is None by default (it is populated by the router, not the ORM).
    """
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
        challenger_name=None,  # not on ORM; populated by router after get_pending_for_target
        target_name=None,  # not on ORM; populated by router after get_outgoing_for_challenger
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
    winner_side: int | None = None,
):
    """Build a MagicMock that looks like a FightResult.

    winner_side must be a real int (1, 2) or None — never left as a MagicMock
    auto-attribute, which would be truthy and silently mis-route winner_player_id
    resolution in the router.  Defaults to None (stalemate/unknown).
    """
    fight = MagicMock()
    fight.winner_name = winner_name
    fight.loser_name = loser_name
    fight.is_stalemate = is_stalemate
    fight.winner_side = winner_side
    return fight


def make_mock_accept_result(
    duel_id=1,
    is_stalemate=False,
    winner_name="Ship A",
    loser_name="Ship B",
    winner_side: int | None = None,
    credits_transferred=500,
    stakes=500,
    challenger_id=100,
    challenger_credits=1500,
    challenger_name=None,
    target_id=200,
    target_credits=500,
    target_name=None,
):
    """Build the dict returned by DuelService.accept_duel.

    winner_side is forwarded to make_mock_fight_result so that the router's
    getattr(fight, "winner_side", None) receives a real int or None — not a
    truthy MagicMock auto-attribute that would silently mis-route winner_player_id.
    """
    fight = make_mock_fight_result(
        winner_name=winner_name,
        loser_name=loser_name,
        is_stalemate=is_stalemate,
        winner_side=winner_side,
    )
    challenger = make_mock_player(player_id=challenger_id, credits=challenger_credits)
    target = make_mock_player(player_id=target_id, credits=target_credits)
    return {
        "fight_results": fight,
        "challenger": challenger,
        "target": target,
        "stakes": stakes,
        "credits_transferred": credits_transferred,
        "challenger_name": challenger_name,
        "target_name": target_name,
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
    # get_duel returns a duel with target_id=200 by default
    service.get_duel = AsyncMock(return_value=make_mock_duel(target_id=200))
    # B.64 / B.65 new methods
    service.cancel_duel = AsyncMock(return_value=make_mock_duel(status="cancelled"))
    service.get_outgoing_for_challenger = AsyncMock(return_value=[])
    # admin touch-up new methods
    service.get_all_pending_for_guild = AsyncMock(return_value=[])
    service.cancel_all_pending_duels = AsyncMock(return_value=[])
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
            side_effect=ValueError("A pending duel already exists between player 100 and player 200 in guild 67890.")
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

    @patch("api.routers.duels.get_db_session")
    def test_create_challenge_unexpected_exception_returns_safe_500(self, mock_get_db, client, mock_duel_service):
        """B.15 fix: unhandled service exception returns 500 with safe message, not raw exception text.

        Previously any non-ValueError from the service would surface as a raw
        FastAPI 500 potentially leaking internal details.  After the fix the
        router catches it and returns a generic safe message.
        """
        _configure_db_mock(mock_get_db)
        mock_duel_service.create_challenge = AsyncMock(
            side_effect=RuntimeError("sqlalchemy internal: connection pool exhausted")
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

        assert response.status_code == 500
        detail = response.json()["detail"]
        # Safe generic message — no raw exception text leaked
        assert "internal error" in detail.lower()
        assert "sqlalchemy" not in detail.lower()
        assert "pool exhausted" not in detail.lower()


# ===========================================================================
# 2. POST /duels/{duel_id}/accept
# ===========================================================================


class TestAcceptDuel:
    """Tests for POST /api/v1/duels/{duel_id}/accept."""

    @patch("api.routers.duels.get_db_session")
    def test_accept_duel_winner_result(self, mock_get_db, client, mock_duel_service):
        """Returns fight result with winner/loser and credit transfer on decisive outcome."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.get_duel = AsyncMock(return_value=make_mock_duel(id=1, target_id=200))
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

        response = client.post("/api/v1/duels/1/accept?user_id=200")

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
        mock_duel_service.get_duel = AsyncMock(return_value=make_mock_duel(id=1, target_id=200))
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

        response = client.post("/api/v1/duels/1/accept?user_id=200")

        assert response.status_code == 200
        data = response.json()
        assert data["is_stalemate"] is True
        assert data["credits_transferred"] == 0

    @patch("api.routers.duels.get_db_session")
    def test_accept_duel_not_found(self, mock_get_db, client, mock_duel_service):
        """Returns 404 when the duel does not exist."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.get_duel = AsyncMock(side_effect=ValueError("Duel request with ID 999 not found."))

        response = client.post("/api/v1/duels/999/accept?user_id=200")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @patch("api.routers.duels.get_db_session")
    def test_accept_duel_already_resolved(self, mock_get_db, client, mock_duel_service):
        """Returns 400 when the duel is not in pending status."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.get_duel = AsyncMock(return_value=make_mock_duel(id=1, target_id=200))
        mock_duel_service.accept_duel = AsyncMock(
            side_effect=ValueError("Duel 1 cannot be accepted — current status is 'completed'.")
        )

        response = client.post("/api/v1/duels/1/accept?user_id=200")

        assert response.status_code == 400
        assert "cannot be accepted" in response.json()["detail"].lower()

    @patch("api.routers.duels.get_db_session")
    def test_accept_duel_wrong_user_returns_403(self, mock_get_db, client, mock_duel_service):
        """Returns 403 when user_id does not match duel target_id."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.get_duel = AsyncMock(return_value=make_mock_duel(id=1, target_id=200))

        # user_id=999 is NOT the target (200)
        response = client.post("/api/v1/duels/1/accept?user_id=999")

        assert response.status_code == 403
        assert "only the challenged player" in response.json()["detail"].lower()

    @patch("api.routers.duels.get_db_session")
    def test_accept_duel_unexpected_exception_returns_safe_500(self, mock_get_db, client, mock_duel_service):
        """B.15 sibling fix: unhandled accept exception returns safe 500, not raw exception text."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.get_duel = AsyncMock(return_value=make_mock_duel(id=1, target_id=200))
        mock_duel_service.accept_duel = AsyncMock(side_effect=RuntimeError("sqlalchemy internal: deadlock detected"))

        response = client.post("/api/v1/duels/1/accept?user_id=200")

        assert response.status_code == 500
        detail = response.json()["detail"]
        assert "internal error" in detail.lower()
        assert "deadlock" not in detail.lower()

    @patch("api.routers.duels.get_db_session")
    def test_accept_duel_includes_challenger_and_target_names(self, mock_get_db, client, mock_duel_service):
        """B.61: accept response includes challenger_name and target_name from service.

        The cog's _build_accept_embed() reads data.get("challenger_name") and
        data.get("target_name") for the Final Balances field. The router must
        pass both values through from the service result dict.
        """
        _configure_db_mock(mock_get_db)
        mock_duel_service.get_duel = AsyncMock(return_value=make_mock_duel(id=1, target_id=200))
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
                challenger_name="ChallengerX",
                target_id=200,
                target_credits=500,
                target_name="TargetY",
            )
        )

        response = client.post("/api/v1/duels/1/accept?user_id=200")

        assert response.status_code == 200
        data = response.json()
        assert data["challenger_name"] == "ChallengerX"
        assert data["target_name"] == "TargetY"

    @patch("api.routers.duels.get_db_session")
    def test_accept_duel_names_none_when_unresolved(self, mock_get_db, client, mock_duel_service):
        """B.61: challenger_name and target_name are None when user lookup fails (defensive)."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.get_duel = AsyncMock(return_value=make_mock_duel(id=1, target_id=200))
        mock_duel_service.accept_duel = AsyncMock(
            return_value=make_mock_accept_result(
                challenger_name=None,
                target_name=None,
            )
        )

        response = client.post("/api/v1/duels/1/accept?user_id=200")

        assert response.status_code == 200
        data = response.json()
        assert data["challenger_name"] is None
        assert data["target_name"] is None

    @patch("api.routers.duels.get_db_session")
    def test_accept_duel_includes_after_action_summary(self, mock_get_db, client, mock_duel_service):
        """CI-2: accept response includes after-action summary fields (duration_s, combatants, outcome)."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.get_duel = AsyncMock(return_value=make_mock_duel(id=1, target_id=200))

        # Build a fight_results with real metadata mimicking the tick-resolver output
        fight = make_mock_fight_result(winner_name="Ship A", loser_name="Ship B", is_stalemate=False)
        fight.combat_log_id = 77
        fight.metadata = {
            "summary": {
                "outcome": "win",
                "reason": "hp_depleted",
                "duration_ticks": 1500,
                "combatants": {
                    "1": {
                        "name": "Ship A",
                        "ship": "Ship A",
                        "final_hp": {"shield": 0, "armour": 50, "hull": 120},
                        "damage_dealt": 250,
                        "damage_taken": 80,
                        "shots_fired": 60,
                        "shots_hit": 40,
                        "accuracy": 0.667,
                    },
                    "2": {
                        "name": "Ship B",
                        "ship": "Ship B",
                        "final_hp": {"shield": 0, "armour": 0, "hull": 0},
                        "damage_dealt": 80,
                        "damage_taken": 250,
                        "shots_fired": 55,
                        "shots_hit": 30,
                        "accuracy": 0.545,
                    },
                },
            },
            "metadata": {"tick_ms": 10, "total_ticks": 1500, "resolver": "tick_v1", "pvc_damage_reduction": 0.0},
        }
        challenger = make_mock_player(player_id=100, credits=1500)
        target = make_mock_player(player_id=200, credits=500)
        mock_duel_service.accept_duel = AsyncMock(
            return_value={
                "fight_results": fight,
                "challenger": challenger,
                "target": target,
                "stakes": 500,
                "credits_transferred": 500,
                "challenger_name": "challenger_player",
                "target_name": "target_player",
            }
        )

        response = client.post("/api/v1/duels/1/accept?user_id=200")

        assert response.status_code == 200
        data = response.json()
        # After-action summary fields present
        assert data["outcome"] == "win"
        assert data["reason"] == "hp_depleted"
        assert data["duration_ticks"] == 1500
        assert abs(data["duration_s"] - 15.0) < 0.01  # 1500 * 10ms / 1000
        assert data["combat_log_id"] == 77
        cb = data["combatants"]
        assert cb is not None
        assert cb["1"]["damage_dealt"] == 250
        assert cb["2"]["final_hp"]["hull"] == 0
        # Legacy fields still present
        assert data["winner_name"] == "Ship A"
        assert data["credits_transferred"] == 500

    @patch("api.routers.duels.get_db_session")
    def test_accept_duel_summary_fields_none_when_no_metadata(self, mock_get_db, client, mock_duel_service):
        """CI-2: after-action summary fields are null when fight has no real metadata."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.get_duel = AsyncMock(return_value=make_mock_duel(id=1, target_id=200))
        # make_mock_accept_result uses MagicMock for fight → metadata is a MagicMock, not a real dict
        mock_duel_service.accept_duel = AsyncMock(return_value=make_mock_accept_result())

        response = client.post("/api/v1/duels/1/accept?user_id=200")

        assert response.status_code == 200
        data = response.json()
        assert data["outcome"] is None
        assert data["duration_ticks"] is None
        assert data["duration_s"] is None
        assert data["combatants"] is None

    # ------------------------------------------------------------------
    # P2-T8a: winner_player_id resolved from winner_side, not from name
    # ------------------------------------------------------------------

    @patch("api.routers.duels.get_db_session")
    def test_accept_duel_winner_player_id_challenger_wins(self, mock_get_db, client, mock_duel_service):
        """P2-T8a: winner_side=1 → winner_player_id equals challenger snowflake (not target).

        Anti-vacuous: swapping the side→id mapping in the router (i.e. side==1 → target.id)
        would return target_id (200) instead of challenger_id (100), failing this assertion.
        """
        _configure_db_mock(mock_get_db)
        CHALLENGER_ID = 100
        TARGET_ID = 200
        mock_duel_service.get_duel = AsyncMock(
            return_value=make_mock_duel(id=1, challenger_id=CHALLENGER_ID, target_id=TARGET_ID)
        )
        mock_duel_service.accept_duel = AsyncMock(
            return_value=make_mock_accept_result(
                duel_id=1,
                is_stalemate=False,
                winner_name="Ship A",
                loser_name="Ship B",
                winner_side=1,  # challenger wins
                credits_transferred=500,
                stakes=500,
                challenger_id=CHALLENGER_ID,
                challenger_credits=1500,
                target_id=TARGET_ID,
                target_credits=500,
            )
        )

        response = client.post(f"/api/v1/duels/1/accept?user_id={TARGET_ID}")

        assert response.status_code == 200
        data = response.json()
        # winner_side=1 → challenger is the winner
        assert data["winner_player_id"] == CHALLENGER_ID, (
            f"expected challenger_id={CHALLENGER_ID}, got {data['winner_player_id']}"
        )
        # Confirm it is NOT the target — wrong mapping must fail
        assert data["winner_player_id"] != TARGET_ID

    @patch("api.routers.duels.get_db_session")
    def test_accept_duel_winner_player_id_target_wins(self, mock_get_db, client, mock_duel_service):
        """P2-T8a: winner_side=2 → winner_player_id equals target snowflake (not challenger).

        Anti-vacuous: swapping the side→id mapping in the router (i.e. side==2 → challenger.id)
        would return challenger_id (100) instead of target_id (200), failing this assertion.
        """
        _configure_db_mock(mock_get_db)
        CHALLENGER_ID = 100
        TARGET_ID = 200
        mock_duel_service.get_duel = AsyncMock(
            return_value=make_mock_duel(id=1, challenger_id=CHALLENGER_ID, target_id=TARGET_ID)
        )
        mock_duel_service.accept_duel = AsyncMock(
            return_value=make_mock_accept_result(
                duel_id=1,
                is_stalemate=False,
                winner_name="Ship B",
                loser_name="Ship A",
                winner_side=2,  # target wins
                credits_transferred=500,
                stakes=500,
                challenger_id=CHALLENGER_ID,
                challenger_credits=500,
                target_id=TARGET_ID,
                target_credits=1500,
            )
        )

        response = client.post(f"/api/v1/duels/1/accept?user_id={TARGET_ID}")

        assert response.status_code == 200
        data = response.json()
        # winner_side=2 → target is the winner
        assert data["winner_player_id"] == TARGET_ID, (
            f"expected target_id={TARGET_ID}, got {data['winner_player_id']}"
        )
        # Confirm it is NOT the challenger — wrong mapping must fail
        assert data["winner_player_id"] != CHALLENGER_ID

    @patch("api.routers.duels.get_db_session")
    def test_accept_duel_winner_player_id_stalemate_is_none(self, mock_get_db, client, mock_duel_service):
        """P2-T8a: stalemate (winner_side=None, is_stalemate=True) → winner_player_id is None."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.get_duel = AsyncMock(
            return_value=make_mock_duel(id=1, challenger_id=100, target_id=200)
        )
        mock_duel_service.accept_duel = AsyncMock(
            return_value=make_mock_accept_result(
                duel_id=1,
                is_stalemate=True,
                winner_name="",
                loser_name="",
                winner_side=None,
                credits_transferred=0,
                stakes=500,
                challenger_id=100,
                challenger_credits=1000,
                target_id=200,
                target_credits=1000,
            )
        )

        response = client.post("/api/v1/duels/1/accept?user_id=200")

        assert response.status_code == 200
        data = response.json()
        assert data["winner_player_id"] is None
        assert data["is_stalemate"] is True


# ===========================================================================
# 3. POST /duels/{duel_id}/reject
# ===========================================================================


class TestRejectDuel:
    """Tests for POST /api/v1/duels/{duel_id}/reject."""

    @patch("api.routers.duels.get_db_session")
    def test_reject_duel_success(self, mock_get_db, client, mock_duel_service):
        """Returns the updated duel with rejected status."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.get_duel = AsyncMock(return_value=make_mock_duel(id=1, target_id=200))
        mock_duel_service.reject_duel = AsyncMock(
            return_value=make_mock_duel(
                id=1,
                status="rejected",
            )
        )

        response = client.post("/api/v1/duels/1/reject?user_id=200")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 1
        assert data["status"] == "rejected"

    @patch("api.routers.duels.get_db_session")
    def test_reject_duel_not_found(self, mock_get_db, client, mock_duel_service):
        """Returns 404 when the duel does not exist."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.get_duel = AsyncMock(side_effect=ValueError("Duel request with ID 999 not found."))

        response = client.post("/api/v1/duels/999/reject?user_id=200")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @patch("api.routers.duels.get_db_session")
    def test_reject_duel_already_resolved(self, mock_get_db, client, mock_duel_service):
        """Returns 400 when duel cannot be rejected (not in pending status)."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.get_duel = AsyncMock(return_value=make_mock_duel(id=1, target_id=200))
        mock_duel_service.reject_duel = AsyncMock(
            side_effect=ValueError("Duel 1 cannot be rejected — current status is 'completed'.")
        )

        response = client.post("/api/v1/duels/1/reject?user_id=200")

        assert response.status_code == 400
        assert "cannot be rejected" in response.json()["detail"].lower()

    @patch("api.routers.duels.get_db_session")
    def test_reject_duel_wrong_user_returns_403(self, mock_get_db, client, mock_duel_service):
        """Returns 403 when user_id does not match duel target_id."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.get_duel = AsyncMock(return_value=make_mock_duel(id=1, target_id=200))

        # user_id=999 is NOT the target (200)
        response = client.post("/api/v1/duels/1/reject?user_id=999")

        assert response.status_code == 403
        assert "only the challenged player" in response.json()["detail"].lower()

    @patch("api.routers.duels.get_db_session")
    def test_reject_duel_unexpected_exception_returns_safe_500(self, mock_get_db, client, mock_duel_service):
        """B.15 sibling fix: unhandled reject exception returns safe 500, not raw exception text."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.get_duel = AsyncMock(return_value=make_mock_duel(id=1, target_id=200))
        mock_duel_service.reject_duel = AsyncMock(side_effect=RuntimeError("sqlalchemy internal: session closed"))

        response = client.post("/api/v1/duels/1/reject?user_id=200")

        assert response.status_code == 500
        detail = response.json()["detail"]
        assert "internal error" in detail.lower()
        assert "session closed" not in detail.lower()


# ===========================================================================
# 4. GET /duels/pending
# ===========================================================================


class TestGetPendingDuels:
    """Tests for GET /api/v1/duels/pending."""

    @patch("api.routers.duels.get_db_session")
    def test_get_pending_duels_returns_list(self, mock_get_db, client, mock_duel_service):
        """Returns list of pending duels where the user is the target.

        get_pending_for_target now returns list[tuple[DuelRequest, str | None]]
        so challenger_name is populated on each response object.
        """
        _configure_db_mock(mock_get_db)
        mock_duel_service.get_pending_for_target = AsyncMock(
            return_value=[
                (make_mock_duel(id=1, target_id=200, challenger_id=100, status="pending"), "SamAccountX"),
                (make_mock_duel(id=2, target_id=200, challenger_id=300, status="pending"), None),
            ]
        )

        response = client.get("/api/v1/duels/pending", params={"user_id": 200, "guild_id": 67890})

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["id"] == 1
        assert data[0]["challenger_name"] == "SamAccountX"
        assert data[1]["id"] == 2
        assert data[1]["challenger_name"] is None

    @patch("api.routers.duels.get_db_session")
    def test_get_pending_duels_empty_list(self, mock_get_db, client, mock_duel_service):
        """Returns empty list when no pending duels exist for the user."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.get_pending_for_target = AsyncMock(return_value=[])

        response = client.get("/api/v1/duels/pending", params={"user_id": 999, "guild_id": 67890})

        assert response.status_code == 200
        assert response.json() == []

    @patch("api.routers.duels.get_db_session")
    def test_get_pending_duels_challenger_name_in_response(self, mock_get_db, client, mock_duel_service):
        """challenger_name is passed through to the response when resolved."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.get_pending_for_target = AsyncMock(
            return_value=[
                (make_mock_duel(id=5, target_id=200, challenger_id=100, stakes=500, status="pending"), "GunnerX"),
            ]
        )

        response = client.get("/api/v1/duels/pending", params={"user_id": 200, "guild_id": 67890})

        assert response.status_code == 200
        data = response.json()
        assert data[0]["challenger_name"] == "GunnerX"
        assert data[0]["stakes"] == 500


# ===========================================================================
# 5. GET /duels/outgoing  (B.64)
# ===========================================================================


class TestGetOutgoingDuels:
    """Tests for GET /api/v1/duels/outgoing."""

    @patch("api.routers.duels.get_db_session")
    def test_get_outgoing_duels_returns_list(self, mock_get_db, client, mock_duel_service):
        """Returns list of pending duels where the user is the challenger."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.get_outgoing_for_challenger = AsyncMock(
            return_value=[
                (make_mock_duel(id=1, challenger_id=100, target_id=200, status="pending"), "TargetAlpha"),
                (make_mock_duel(id=2, challenger_id=100, target_id=300, status="pending"), None),
            ]
        )

        response = client.get("/api/v1/duels/outgoing", params={"user_id": 100, "guild_id": 67890})

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["id"] == 1
        assert data[0]["target_name"] == "TargetAlpha"
        assert data[1]["id"] == 2
        assert data[1]["target_name"] is None

    @patch("api.routers.duels.get_db_session")
    def test_get_outgoing_duels_empty_list(self, mock_get_db, client, mock_duel_service):
        """Returns empty list when no outgoing duels exist for the user."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.get_outgoing_for_challenger = AsyncMock(return_value=[])

        response = client.get("/api/v1/duels/outgoing", params={"user_id": 100, "guild_id": 67890})

        assert response.status_code == 200
        assert response.json() == []


# ===========================================================================
# 6. POST /duels/{duel_id}/cancel  (B.64 — challenger self-cancel)
# ===========================================================================


class TestCancelDuel:
    """Tests for POST /api/v1/duels/{duel_id}/cancel."""

    @patch("api.routers.duels.get_db_session")
    def test_cancel_duel_success(self, mock_get_db, client, mock_duel_service):
        """Returns the cancelled duel on success."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.cancel_duel = AsyncMock(return_value=make_mock_duel(id=1, status="cancelled"))

        response = client.post("/api/v1/duels/1/cancel?user_id=100")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 1
        assert data["status"] == "cancelled"

    @patch("api.routers.duels.get_db_session")
    def test_cancel_duel_not_found(self, mock_get_db, client, mock_duel_service):
        """Returns 404 when the duel does not exist."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.cancel_duel = AsyncMock(side_effect=ValueError("Duel not found."))

        response = client.post("/api/v1/duels/999/cancel?user_id=100")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @patch("api.routers.duels.get_db_session")
    def test_cancel_duel_not_pending(self, mock_get_db, client, mock_duel_service):
        """Returns 400 when the duel is not pending."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.cancel_duel = AsyncMock(side_effect=ValueError("Only pending duels can be cancelled."))

        response = client.post("/api/v1/duels/1/cancel?user_id=100")

        assert response.status_code == 400
        assert "only pending duels" in response.json()["detail"].lower()

    @patch("api.routers.duels.get_db_session")
    def test_cancel_duel_not_challenger(self, mock_get_db, client, mock_duel_service):
        """Returns 400 when the requesting user is not the challenger."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.cancel_duel = AsyncMock(side_effect=ValueError("Only the challenger can cancel a duel."))

        response = client.post("/api/v1/duels/1/cancel?user_id=200")

        assert response.status_code == 400
        assert "only the challenger" in response.json()["detail"].lower()

    @patch("api.routers.duels.get_db_session")
    def test_cancel_duel_unexpected_exception_returns_500(self, mock_get_db, client, mock_duel_service):
        """Unexpected service exception returns safe 500."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.cancel_duel = AsyncMock(side_effect=RuntimeError("DB error"))

        response = client.post("/api/v1/duels/1/cancel?user_id=100")

        assert response.status_code == 500
        assert "internal error" in response.json()["detail"].lower()


# ===========================================================================
# 7. POST /duels/{duel_id}/admin-cancel  (B.65 — admin cancel)
# ===========================================================================


class TestAdminCancelDuel:
    """Tests for POST /api/v1/duels/{duel_id}/admin-cancel."""

    @patch("api.routers.duels.get_db_session")
    def test_admin_cancel_success(self, mock_get_db, client, mock_duel_service):
        """Returns the cancelled duel on admin cancel success."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.cancel_duel = AsyncMock(return_value=make_mock_duel(id=1, status="cancelled"))

        response = client.post("/api/v1/duels/1/admin-cancel?admin_user_id=123456789")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 1
        assert data["status"] == "cancelled"

    @patch("api.routers.duels.get_db_session")
    def test_admin_cancel_duel_not_found(self, mock_get_db, client, mock_duel_service):
        """Returns 404 when the duel does not exist."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.cancel_duel = AsyncMock(side_effect=ValueError("Duel not found."))

        response = client.post("/api/v1/duels/999/admin-cancel?admin_user_id=123456789")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @patch("api.routers.duels.get_db_session")
    def test_admin_cancel_not_pending(self, mock_get_db, client, mock_duel_service):
        """Returns 400 when the duel is not pending."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.cancel_duel = AsyncMock(side_effect=ValueError("Only pending duels can be cancelled."))

        response = client.post("/api/v1/duels/1/admin-cancel?admin_user_id=123456789")

        assert response.status_code == 400
        assert "only pending duels" in response.json()["detail"].lower()

    @patch("api.routers.duels.get_db_session")
    def test_admin_cancel_unexpected_exception_returns_500(self, mock_get_db, client, mock_duel_service):
        """Unexpected service exception returns safe 500."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.cancel_duel = AsyncMock(side_effect=RuntimeError("DB error"))

        response = client.post("/api/v1/duels/1/admin-cancel?admin_user_id=123456789")

        assert response.status_code == 500
        assert "internal error" in response.json()["detail"].lower()


# ===========================================================================
# 8. GET /duels/pending-all  (admin autocomplete endpoint)
# ===========================================================================


class TestGetAllPendingDuels:
    """Tests for GET /api/v1/duels/pending-all."""

    @patch("api.routers.duels.get_db_session")
    def test_get_all_pending_returns_list_with_both_names(self, mock_get_db, client, mock_duel_service):
        """Returns list of all pending duels with challenger_name and target_name populated."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.get_all_pending_for_guild = AsyncMock(
            return_value=[
                (make_mock_duel(id=1, challenger_id=100, target_id=200, stakes=500), "ChallengerX", "TargetY"),
                (make_mock_duel(id=2, challenger_id=300, target_id=400, stakes=0), None, "TargetZ"),
            ]
        )

        response = client.get("/api/v1/duels/pending-all", params={"guild_id": 67890})

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["id"] == 1
        assert data[0]["challenger_name"] == "ChallengerX"
        assert data[0]["target_name"] == "TargetY"
        assert data[1]["id"] == 2
        assert data[1]["challenger_name"] is None
        assert data[1]["target_name"] == "TargetZ"

    @patch("api.routers.duels.get_db_session")
    def test_get_all_pending_empty_guild(self, mock_get_db, client, mock_duel_service):
        """Returns empty list when guild has no pending duels."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.get_all_pending_for_guild = AsyncMock(return_value=[])

        response = client.get("/api/v1/duels/pending-all", params={"guild_id": 99999})

        assert response.status_code == 200
        assert response.json() == []

    @patch("api.routers.duels.get_db_session")
    def test_get_all_pending_service_error_returns_500(self, mock_get_db, client, mock_duel_service):
        """Returns 500 when service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.get_all_pending_for_guild = AsyncMock(side_effect=RuntimeError("DB failure"))

        response = client.get("/api/v1/duels/pending-all", params={"guild_id": 67890})

        assert response.status_code == 500
        assert "failed to retrieve" in response.json()["detail"].lower()


# ===========================================================================
# 9. POST /duels/admin-cancel-all  (bulk admin cancel)
# ===========================================================================


class TestAdminCancelAllDuels:
    """Tests for POST /api/v1/duels/admin-cancel-all."""

    @patch("api.routers.duels.AuditService")
    @patch("api.routers.duels.get_db_session")
    def test_admin_cancel_all_returns_count_and_ids(self, mock_get_db, mock_audit, client, mock_duel_service):
        """Returns cancelled_count and duel_ids when duels are cancelled."""
        _configure_db_mock(mock_get_db)
        mock_audit.log_action = AsyncMock()
        mock_duel_service.cancel_all_pending_duels = AsyncMock(
            return_value=[
                make_mock_duel(id=1, status="cancelled"),
                make_mock_duel(id=2, status="cancelled"),
            ]
        )

        response = client.post(
            "/api/v1/duels/admin-cancel-all",
            params={"guild_id": 67890, "admin_user_id": 123456789},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["cancelled_count"] == 2
        assert set(data["duel_ids"]) == {1, 2}

    @patch("api.routers.duels.AuditService")
    @patch("api.routers.duels.get_db_session")
    def test_admin_cancel_all_zero_duels(self, mock_get_db, mock_audit, client, mock_duel_service):
        """Returns cancelled_count=0 and empty duel_ids when no pending duels exist."""
        _configure_db_mock(mock_get_db)
        mock_audit.log_action = AsyncMock()
        mock_duel_service.cancel_all_pending_duels = AsyncMock(return_value=[])

        response = client.post(
            "/api/v1/duels/admin-cancel-all",
            params={"guild_id": 67890, "admin_user_id": 123456789},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["cancelled_count"] == 0
        assert data["duel_ids"] == []

    @patch("api.routers.duels.get_db_session")
    def test_admin_cancel_all_service_error_returns_500(self, mock_get_db, client, mock_duel_service):
        """Returns 500 when service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_duel_service.cancel_all_pending_duels = AsyncMock(side_effect=RuntimeError("DB failure"))

        response = client.post(
            "/api/v1/duels/admin-cancel-all",
            params={"guild_id": 67890, "admin_user_id": 123456789},
        )

        assert response.status_code == 500
        assert "internal error" in response.json()["detail"].lower()
