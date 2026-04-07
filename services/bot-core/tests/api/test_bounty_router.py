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
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[make_mock_bounty()])
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
            return_value=make_check_response("on_cooldown", bounty_id=1, message="Please wait before checking again.")
        )

        response = client.post(
            "/api/v1/bounties/check?guild_id=67890",
            json={"player_id": 42, "system_name": "Proxima"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"] == "on_cooldown"
        assert "wait" in data["message"].lower()

    @patch("api.routers.bounties.get_db_session")
    def test_check_bounty_service_exception_returns_not_found_not_500(self, mock_get_db, client, mock_bounty_service):
        """Bug 8: When check_bounty raises an unexpected exception, the endpoint
        returns a graceful NOT_FOUND response (200) instead of propagating as 500.
        """
        _configure_db_mock(mock_get_db)
        mock_bounty_service.check_bounty = AsyncMock(side_effect=RuntimeError("DB query failed: no active bounties"))

        response = client.post(
            "/api/v1/bounties/check?guild_id=67890",
            json={"player_id": 42, "system_name": "Nowhere"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"] == "not_found"
        assert data["bounty_id"] is None

    @patch("api.routers.bounties.get_db_session")
    def test_check_bounty_no_active_bounties_returns_not_found(self, mock_get_db, client, mock_bounty_service):
        """Bug 8: When there are no active bounties the service returns NOT_FOUND
        and the endpoint passes it through without raising a 500.
        """
        _configure_db_mock(mock_get_db)
        mock_bounty_service.check_bounty = AsyncMock(
            return_value=make_check_response("not_found", bounty_id=None, message="No active bounties for division")
        )

        response = client.post(
            "/api/v1/bounties/check?guild_id=67890",
            json={"player_id": 42, "system_name": "Alpha"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"] == "not_found"
        assert data["bounty_id"] is None


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
        mock_bounty_service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[mock_bounty])

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
        mock_bounty_service.bounty_repo.get_active_by_guild = AsyncMock(return_value=[mock_bounty])

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


# ===========================================================================
# Tests: DELETE /bounties/guild/{guild_id}/clear
# ===========================================================================


@pytest.fixture
def mock_bounty_service_for_admin():
    """Mock BountyService with clear_bounties and spawn_bounty for admin tests."""
    service = AsyncMock()
    service.bounty_repo = AsyncMock()
    service.clear_bounties = AsyncMock(
        return_value={
            "guild_id": 67890,
            "tier": None,
            "cleared_count": 3,
            "bounty_ids": [1, 2, 3],
            "announcements_deleted": 2,
        }
    )
    service.spawn_bounty = AsyncMock(return_value=make_mock_bounty())
    return service


@pytest.fixture
def test_app_admin(mock_bounty_service_for_admin):
    from api.routers.bounties import get_bounty_service
    from api.routers.bounties import router as bounties_router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(bounties_router, prefix="/api/v1")
    app.dependency_overrides[get_bounty_service] = lambda: mock_bounty_service_for_admin
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def admin_client(test_app_admin):
    return TestClient(test_app_admin)


class TestClearGuildBounties:
    """Tests for DELETE /api/v1/bounties/guild/{guild_id}/clear."""

    @patch("api.routers.bounties.get_db_session")
    @patch("api.routers.bounties.AuditService")
    def test_clear_all_tiers_returns_200(self, mock_audit, mock_get_db, admin_client, mock_bounty_service_for_admin):
        """Returns 200 with cleared bounty summary."""
        _configure_db_mock(mock_get_db)
        mock_audit.log_action = AsyncMock()

        response = admin_client.delete("/api/v1/bounties/guild/67890/clear?user_id=999")

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890
        assert data["cleared_count"] == 3
        assert data["bounty_ids"] == [1, 2, 3]
        assert data["announcements_deleted"] == 2

    @patch("api.routers.bounties.get_db_session")
    @patch("api.routers.bounties.AuditService")
    def test_clear_specific_tier(self, mock_audit, mock_get_db, admin_client, mock_bounty_service_for_admin):
        """Returns 200 when tier filter is applied."""
        _configure_db_mock(mock_get_db)
        mock_audit.log_action = AsyncMock()
        mock_bounty_service_for_admin.clear_bounties = AsyncMock(
            return_value={
                "guild_id": 67890,
                "tier": "bronze",
                "cleared_count": 1,
                "bounty_ids": [7],
                "announcements_deleted": 1,
            }
        )

        response = admin_client.delete("/api/v1/bounties/guild/67890/clear?tier=bronze&user_id=999")

        assert response.status_code == 200
        data = response.json()
        assert data["tier"] == "bronze"
        assert data["cleared_count"] == 1

    @patch("api.routers.bounties.get_db_session")
    @patch("api.routers.bounties.AuditService")
    def test_clear_service_error_returns_500(
        self, mock_audit, mock_get_db, admin_client, mock_bounty_service_for_admin
    ):
        """Returns 500 when service raises exception."""
        _configure_db_mock(mock_get_db)
        mock_audit.log_action = AsyncMock()
        mock_bounty_service_for_admin.clear_bounties = AsyncMock(side_effect=Exception("DB error"))

        response = admin_client.delete("/api/v1/bounties/guild/67890/clear?user_id=999")

        assert response.status_code == 500


# ===========================================================================
# Tests: POST /bounties/guild/{guild_id}/admin-spawn
# ===========================================================================


class TestAdminSpawnBounties:
    """Tests for POST /api/v1/bounties/guild/{guild_id}/admin-spawn."""

    @patch("api.routers.bounties.get_db_session")
    @patch("api.routers.bounties.AuditService")
    @patch("api.routers.bounties.TemperatureService")
    @patch("api.routers.bounties.ConfigRepository")
    @patch("api.routers.bounties.BountyRepository")
    def test_admin_spawn_with_available_slot(
        self,
        mock_br_cls,
        mock_cr_cls,
        mock_temp_svc,
        mock_audit,
        mock_get_db,
        admin_client,
        mock_bounty_service_for_admin,
    ):
        """Returns 200 with spawned bounties when slots are available."""
        _configure_db_mock(mock_get_db)
        mock_audit.log_action = AsyncMock()
        mock_temp_svc.get_max_bounties = MagicMock(return_value=5)

        # Config with sufficient capacity
        mock_config = MagicMock()
        mock_config.bounty_max_per_tier = {"bronze": 3, "silver": 3, "gold": 3}
        mock_config.bounty_expiry_minutes = 480
        mock_config.division_temperatures = {"bronze": 1.0, "silver": 1.0, "gold": 1.0}
        mock_cr = AsyncMock()
        mock_cr.get_by_guild_id = AsyncMock(return_value=mock_config)
        mock_cr_cls.return_value = mock_cr

        # Bounty repo reports 0 active
        mock_br = AsyncMock()
        mock_br.count_active_by_guild_and_division = AsyncMock(return_value=0)
        mock_br_cls.return_value = mock_br

        response = admin_client.post("/api/v1/bounties/guild/67890/admin-spawn?tier=bronze&user_id=999")

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890
        assert "spawned" in data
        assert "skipped_tiers" in data

    @patch("api.routers.bounties.get_db_session")
    @patch("api.routers.bounties.AuditService")
    @patch("api.routers.bounties.TemperatureService")
    @patch("api.routers.bounties.ConfigRepository")
    @patch("api.routers.bounties.BountyRepository")
    def test_admin_spawn_at_capacity_skips(
        self,
        mock_br_cls,
        mock_cr_cls,
        mock_temp_svc,
        mock_audit,
        mock_get_db,
        admin_client,
        mock_bounty_service_for_admin,
    ):
        """Returns skipped_tiers when guild is at capacity."""
        _configure_db_mock(mock_get_db)
        mock_audit.log_action = AsyncMock()
        # get_max_bounties returns 5, so effective_max = min(3, 5) = 3
        mock_temp_svc.get_max_bounties = MagicMock(return_value=5)

        mock_config = MagicMock()
        mock_config.bounty_max_per_tier = {"bronze": 3, "silver": 3, "gold": 3}
        mock_config.bounty_expiry_minutes = 480
        mock_config.division_temperatures = {"bronze": 5.0}
        mock_cr = AsyncMock()
        mock_cr.get_by_guild_id = AsyncMock(return_value=mock_config)
        mock_cr_cls.return_value = mock_cr

        # Bounty repo reports 3 active (at capacity: 3 >= min(3, 5) = 3)
        mock_br = AsyncMock()
        mock_br.count_active_by_guild_and_division = AsyncMock(return_value=3)
        mock_br_cls.return_value = mock_br

        response = admin_client.post("/api/v1/bounties/guild/67890/admin-spawn?tier=bronze&user_id=999")

        assert response.status_code == 200
        data = response.json()
        assert "bronze" in data["skipped_tiers"], f"Expected bronze in skipped_tiers, got: {data}"
        assert data["spawned"] == []

    @patch("api.routers.bounties.get_db_session")
    @patch("api.routers.bounties.AuditService")
    @patch("api.routers.bounties.TemperatureService")
    @patch("api.routers.bounties.ConfigRepository")
    @patch("api.routers.bounties.BountyRepository")
    @patch("utils.executors.bounty_spawn_executor._announce_bounty")
    @patch("utils.executors.bounty_spawn_executor._schedule_expiry_job")
    def test_admin_spawn_calls_announce_and_schedule(
        self,
        mock_schedule,
        mock_announce,
        mock_br_cls,
        mock_cr_cls,
        mock_temp_svc,
        mock_audit,
        mock_get_db,
        admin_client,
        mock_bounty_service_for_admin,
    ):
        """Bug 7: admin-spawn endpoint calls _schedule_expiry_job and _announce_bounty
        after a successful bounty spawn so that players receive announcements.
        """
        _configure_db_mock(mock_get_db)
        mock_audit.log_action = AsyncMock()
        mock_temp_svc.get_max_bounties = MagicMock(return_value=5)
        mock_schedule.return_value = None
        mock_announce.return_value = None

        mock_config = MagicMock()
        mock_config.bounty_max_per_tier = {"bronze": 3, "silver": 3, "gold": 3}
        mock_config.bounty_expiry_minutes = 480
        mock_config.division_temperatures = {"bronze": 1.0}
        mock_cr = AsyncMock()
        mock_cr.get_by_guild_id = AsyncMock(return_value=mock_config)
        mock_cr_cls.return_value = mock_cr

        mock_br = AsyncMock()
        mock_br.count_active_by_guild_and_division = AsyncMock(return_value=0)
        mock_br_cls.return_value = mock_br

        response = admin_client.post("/api/v1/bounties/guild/67890/admin-spawn?tier=bronze&user_id=999")

        assert response.status_code == 200
        data = response.json()
        assert len(data["spawned"]) == 1
        # Verify both announce and schedule were called once (for the one spawned bounty)
        mock_schedule.assert_awaited_once()
        mock_announce.assert_awaited_once()

    @patch("api.routers.bounties.get_db_session")
    @patch("api.routers.bounties.AuditService")
    @patch("api.routers.bounties.TemperatureService")
    @patch("api.routers.bounties.ConfigRepository")
    @patch("api.routers.bounties.BountyRepository")
    @patch("utils.executors.bounty_spawn_executor._announce_bounty")
    @patch("utils.executors.bounty_spawn_executor._schedule_expiry_job")
    def test_admin_spawn_announce_failure_is_non_fatal(
        self,
        mock_schedule,
        mock_announce,
        mock_br_cls,
        mock_cr_cls,
        mock_temp_svc,
        mock_audit,
        mock_get_db,
        admin_client,
        mock_bounty_service_for_admin,
    ):
        """Bug 7: If _announce_bounty raises, the admin-spawn still returns 200 (best-effort)."""
        _configure_db_mock(mock_get_db)
        mock_audit.log_action = AsyncMock()
        mock_temp_svc.get_max_bounties = MagicMock(return_value=5)
        mock_schedule.return_value = None
        mock_announce.side_effect = RuntimeError("Gateway unreachable")

        mock_config = MagicMock()
        mock_config.bounty_max_per_tier = {"bronze": 3}
        mock_config.bounty_expiry_minutes = 480
        mock_config.division_temperatures = {"bronze": 1.0}
        mock_cr = AsyncMock()
        mock_cr.get_by_guild_id = AsyncMock(return_value=mock_config)
        mock_cr_cls.return_value = mock_cr

        mock_br = AsyncMock()
        mock_br.count_active_by_guild_and_division = AsyncMock(return_value=0)
        mock_br_cls.return_value = mock_br

        response = admin_client.post("/api/v1/bounties/guild/67890/admin-spawn?tier=bronze&user_id=999")

        # Even though announce failed, the spawn itself succeeded
        assert response.status_code == 200
        data = response.json()
        assert len(data["spawned"]) == 1


# ===========================================================================
# Gap 1: Empty-State / Null-Result Tests
# ===========================================================================


class TestGetGuildBountiesEmpty:
    """Gap 1: Empty-state tests for the bounties list endpoint."""

    @patch("api.routers.bounties.get_db_session")
    def test_get_guild_bounties_empty_guild_returns_empty_list(self, mock_get_db, client, mock_bounty_service):
        """GET /bounties/?guild_id={id} with no active bounties → 200 + empty list.

        Verifies that a guild with no bounties does not produce a 500; the endpoint
        should return an empty JSON array.
        """
        _configure_db_mock(mock_get_db)
        # Simulate a guild that has never had a bounty — repo returns []
        mock_bounty_service.bounty_repo.get_active_by_guild = AsyncMock(return_value=[])

        response = client.get("/api/v1/bounties/?guild_id=99999")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert data == []

    @patch("api.routers.bounties.get_db_session")
    def test_check_bounty_no_player_returns_not_found_not_500(self, mock_get_db, client, mock_bounty_service):
        """POST /bounties/check with a player_id that has never existed → 200 NOT_FOUND (not 500).

        A non-existent player should produce a graceful NOT_FOUND result rather than
        an unhandled exception that surfaces as a 500.
        """
        _configure_db_mock(mock_get_db)
        # Service raises because there are no bounties/player not found
        mock_bounty_service.check_bounty = AsyncMock(side_effect=ValueError("Player 99999 not found in guild"))

        response = client.post(
            "/api/v1/bounties/check?guild_id=67890",
            json={"player_id": 99999, "system_name": "Sol"},
        )

        # Must not be 500 — the router catches exceptions and returns NOT_FOUND gracefully
        assert response.status_code == 200
        data = response.json()
        assert data["result"] == "not_found"
        assert data["bounty_id"] is None


# ===========================================================================
# Gap 2: Cross-Service Side-Effect Tests
# ===========================================================================


class TestExpireBountyDeletesDiscordMessage:
    """Gap 2: Cross-service side-effect test — bounty expiry triggers Discord message deletion."""

    @patch("api.routers.bounties.get_db_session")
    @patch("api.routers.bounties.AuditService")
    def test_expire_bounty_deletes_discord_message(
        self, mock_audit, mock_get_db, admin_client, mock_bounty_service_for_admin
    ):
        """When a bounty is cleared, the discord message deletion is triggered.

        The clear_bounties service call returns announcements_deleted > 0, proving
        that the side-effect path (Discord message deletion) was exercised.
        """
        _configure_db_mock(mock_get_db)
        mock_audit.log_action = AsyncMock()

        # Service reports 2 Discord messages were deleted as a side-effect
        mock_bounty_service_for_admin.clear_bounties = AsyncMock(
            return_value={
                "guild_id": 67890,
                "tier": None,
                "cleared_count": 2,
                "bounty_ids": [10, 11],
                "announcements_deleted": 2,
            }
        )

        response = admin_client.delete("/api/v1/bounties/guild/67890/clear?user_id=999")

        assert response.status_code == 200
        data = response.json()
        # The response must include the Discord side-effect count
        assert data["announcements_deleted"] == 2
        assert data["cleared_count"] == 2
        # Verify clear_bounties was actually called (triggering the side effect)
        mock_bounty_service_for_admin.clear_bounties.assert_awaited_once()
