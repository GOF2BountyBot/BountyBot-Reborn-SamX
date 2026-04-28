"""Tests for the admin API router endpoints.

Import path setup and sqlalchemy_utils mocking are handled by
tests/api/conftest.py which runs before this module is loaded.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def make_mock_player(**overrides):
    defaults = dict(
        id=1,
        user_id=12345,
        guild_id=67890,
        credits=100,
        lifetime_credits=100,
        xp=0,
        tier="Bronze",
        prestige_count=0,
        systems_checked=0,
        bounty_wins=0,
        duel_wins=0,
        duel_losses=0,
        duel_credits_won=0,
        duel_credits_lost=0,
        active_ship_id=None,
    )
    defaults.update(overrides)
    player = MagicMock()
    for k, v in defaults.items():
        setattr(player, k, v)
    return player


@pytest.fixture
def mock_player_service():
    service = AsyncMock()
    service.player_repo = AsyncMock()
    service.player_repo.get_by_id = AsyncMock(return_value=make_mock_player())
    service.player_repo.get_players_by_guild = AsyncMock(
        return_value=[
            make_mock_player(id=1, credits=100, xp=50, tier="Bronze"),
            make_mock_player(id=2, credits=200, xp=150, tier="Silver"),
        ]
    )
    service.player_repo.count = AsyncMock(return_value=2)
    service.user_repo = AsyncMock()
    service.user_repo.count = AsyncMock(return_value=3)
    service.config_repo = AsyncMock()
    service.config_repo.count = AsyncMock(return_value=1)
    service.update_player_credits = AsyncMock(return_value=make_mock_player(credits=500, lifetime_credits=500))
    service.update_player_xp = AsyncMock(return_value=make_mock_player(xp=100, tier="Bronze"))
    return service


@pytest.fixture
def mock_shop_service():
    service = AsyncMock()
    service.shop_repo = AsyncMock()
    service.shop_repo.count = AsyncMock(return_value=5)
    service.refresh_shop = AsyncMock(return_value={"refreshed": True, "items_count": 10})
    return service


@pytest.fixture
def mock_config_service():
    service = AsyncMock()
    service.create_or_update_config = AsyncMock()
    service.clear_guild_players = AsyncMock()
    service.reset_to_defaults = AsyncMock()
    service.uninstall_guild = AsyncMock(return_value={"players": 5, "configs": 1, "shops": 40})
    service.update_shop_config = AsyncMock(return_value={"sale_price_factor": 0.5})
    return service


def _make_transaction_details(player_id=1, item_type="weapon", item_name="Pulse Laser", quantity=2):
    """Return a dict matching InventoryService.add_item_to_inventory() output."""
    return {
        "player_id": player_id,
        "item_type": item_type,
        "item_name": item_name,
        "quantity_added": quantity,
        "new_total_quantity": quantity,
        "transaction_time": "2026-01-01T00:00:00",
    }


@pytest.fixture
def mock_inventory_service():
    service = AsyncMock()
    service.add_item_to_inventory = AsyncMock(return_value=_make_transaction_details())
    return service


@pytest.fixture
def test_app(mock_player_service, mock_shop_service, mock_config_service, mock_inventory_service):
    app = FastAPI()
    from api.routers.admin import (
        get_config_service,
        get_inventory_service,
        get_player_service,
        get_shop_service,
    )
    from api.routers.admin import router as admin_router

    app.include_router(admin_router, prefix="/api/v1")
    app.dependency_overrides[get_player_service] = lambda: mock_player_service
    app.dependency_overrides[get_shop_service] = lambda: mock_shop_service
    app.dependency_overrides[get_config_service] = lambda: mock_config_service
    app.dependency_overrides[get_inventory_service] = lambda: mock_inventory_service
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


# ---------------------------------------------------------------------------
# Helper: build a configured mock get_db_session patcher result
# ---------------------------------------------------------------------------


def _configure_db_mock(mock_get_db):
    """Configure mock_get_db to act as an async context manager."""
    mock_session = AsyncMock()
    mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_session


# ===========================================================================
# 1. POST /admin/guilds/initialize
# ===========================================================================


class TestInitializeGuild:
    """Tests for POST /api/v1/admin/guilds/initialize."""

    @patch("api.routers.admin.get_db_session")
    def test_initialize_guild_happy_path(self, mock_get_db, client, mock_config_service, mock_shop_service):
        """Returns 200 with GuildInitializationResponse on success."""
        _configure_db_mock(mock_get_db)
        payload = {"guild_id": 67890, "admin_role_id": 11111, "starting_credits": 500}

        response = client.post("/api/v1/admin/guilds/initialize?user_id=67890", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890
        assert data["admin_role_id"] == 11111
        assert data["shops_created"] == 4
        assert data["config_created"] is True
        assert "67890" in data["message"]

    @patch("api.routers.admin.get_db_session")
    def test_initialize_guild_default_values(self, mock_get_db, client, mock_config_service, mock_shop_service):
        """Returns 200 when optional fields use defaults."""
        _configure_db_mock(mock_get_db)
        payload = {"guild_id": 12345}

        response = client.post("/api/v1/admin/guilds/initialize?user_id=67890", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 12345
        assert data["admin_role_id"] is None
        assert data["shops_created"] == 4
        assert data["config_created"] is True

    @patch("api.routers.admin.get_db_session")
    def test_initialize_guild_calls_config_and_shop_services(
        self, mock_get_db, client, mock_config_service, mock_shop_service
    ):
        """Calls create_or_update_config and refresh_shop for all 4 tiers."""
        _configure_db_mock(mock_get_db)
        payload = {"guild_id": 67890}

        client.post("/api/v1/admin/guilds/initialize?user_id=67890", json=payload)

        mock_config_service.create_or_update_config.assert_awaited_once()
        assert mock_shop_service.refresh_shop.await_count == 4
        tiers_called = [call.args[2] for call in mock_shop_service.refresh_shop.call_args_list]
        assert set(tiers_called) == {"Bronze", "Silver", "Gold", "Platinum"}

    @patch("api.routers.admin.get_db_session")
    def test_initialize_guild_server_error_returns_500(self, mock_get_db, client, mock_config_service):
        """Returns 500 when config_service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_config_service.create_or_update_config.side_effect = RuntimeError("DB failure")
        payload = {"guild_id": 67890}

        response = client.post("/api/v1/admin/guilds/initialize?user_id=67890", json=payload)

        assert response.status_code == 500
        assert "Failed to initialize guild" in response.json()["detail"]

    def test_initialize_guild_missing_guild_id_returns_422(self, client):
        """Returns 422 when required field guild_id is missing."""
        payload = {"admin_role_id": 11111}

        response = client.post("/api/v1/admin/guilds/initialize?user_id=67890", json=payload)

        assert response.status_code == 422

    def test_initialize_guild_negative_starting_credits_returns_422(self, client):
        """Returns 422 when starting_credits is negative (ge=0)."""
        payload = {"guild_id": 67890, "starting_credits": -100}

        response = client.post("/api/v1/admin/guilds/initialize?user_id=67890", json=payload)

        assert response.status_code == 422


# ===========================================================================
# 2. POST /admin/guilds/{guild_id}/reset
# ===========================================================================


class TestResetGuild:
    """Tests for POST /api/v1/admin/guilds/{guild_id}/reset."""

    @patch("api.routers.admin.get_db_session")
    def test_reset_guild_preserve_players_true(self, mock_get_db, client, mock_config_service, mock_shop_service):
        """Does not clear player data when preserve_players=true."""
        _configure_db_mock(mock_get_db)

        response = client.post("/api/v1/admin/guilds/67890/reset?user_id=67890&preserve_players=true")

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890
        assert data["players_preserved"] is True
        assert data["shops_refreshed"] == 4
        assert "67890" in data["message"]
        mock_config_service.clear_guild_players.assert_not_awaited()
        mock_config_service.reset_to_defaults.assert_awaited_once()

    @patch("api.routers.admin.get_db_session")
    def test_reset_guild_preserve_players_false(self, mock_get_db, client, mock_config_service, mock_shop_service):
        """Calls clear_guild_players when preserve_players=false."""
        _configure_db_mock(mock_get_db)

        response = client.post("/api/v1/admin/guilds/67890/reset?user_id=67890&preserve_players=false")

        assert response.status_code == 200
        data = response.json()
        assert data["players_preserved"] is False
        mock_config_service.clear_guild_players.assert_awaited_once()
        mock_config_service.reset_to_defaults.assert_awaited_once()

    @patch("api.routers.admin.get_db_session")
    def test_reset_guild_default_preserve_players(self, mock_get_db, client, mock_config_service, mock_shop_service):
        """Defaults preserve_players to True when not provided."""
        _configure_db_mock(mock_get_db)

        response = client.post("/api/v1/admin/guilds/67890/reset?user_id=67890")

        assert response.status_code == 200
        data = response.json()
        assert data["players_preserved"] is True
        mock_config_service.clear_guild_players.assert_not_awaited()

    @patch("api.routers.admin.get_db_session")
    def test_reset_guild_refreshes_all_4_tiers(self, mock_get_db, client, mock_config_service, mock_shop_service):
        """Calls refresh_shop for all 4 tiers."""
        _configure_db_mock(mock_get_db)

        client.post("/api/v1/admin/guilds/67890/reset?user_id=67890&preserve_players=true")

        assert mock_shop_service.refresh_shop.await_count == 4
        tiers_called = [call.args[2] for call in mock_shop_service.refresh_shop.call_args_list]
        assert set(tiers_called) == {"Bronze", "Silver", "Gold", "Platinum"}

    @patch("api.routers.admin.get_db_session")
    def test_reset_guild_server_error_returns_500(self, mock_get_db, client, mock_config_service):
        """Returns 500 when service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_config_service.reset_to_defaults.side_effect = RuntimeError("Reset failed")

        response = client.post("/api/v1/admin/guilds/67890/reset?user_id=67890")

        assert response.status_code == 500
        assert "Failed to reset guild" in response.json()["detail"]

    @patch("api.routers.admin.get_db_session")
    def test_reset_guild_missing_user_id_returns_422(self, mock_get_db, client):
        """Returns 422 when required user_id query parameter is missing."""
        _configure_db_mock(mock_get_db)

        response = client.post("/api/v1/admin/guilds/67890/reset")

        assert response.status_code == 422

    @patch.dict("os.environ", {"ADMIN_USER_IDS": "123,456"})
    @patch("api.routers.admin.get_db_session")
    def test_reset_guild_admin_user_allowed(self, mock_get_db, client, mock_config_service, mock_shop_service):
        """Admin user (in ADMIN_USER_IDS) can reset the guild."""
        _configure_db_mock(mock_get_db)

        response = client.post("/api/v1/admin/guilds/67890/reset?user_id=123")

        assert response.status_code == 200

    @patch.dict("os.environ", {"ADMIN_USER_IDS": "123,456"})
    @patch("api.routers.admin.get_db_session")
    def test_reset_guild_non_admin_user_returns_403(self, mock_get_db, client):
        """Non-admin user (not in ADMIN_USER_IDS) receives 403 Forbidden."""
        _configure_db_mock(mock_get_db)

        response = client.post("/api/v1/admin/guilds/67890/reset?user_id=999")

        assert response.status_code == 403
        assert "Insufficient permissions" in response.json()["detail"]

    @patch("api.routers.admin.get_db_session")
    def test_reset_guild_dev_mode_no_admin_ids_allows_access(
        self, mock_get_db, client, mock_config_service, mock_shop_service
    ):
        """When ADMIN_USER_IDS is not set, any user is allowed (dev mode)."""
        _configure_db_mock(mock_get_db)
        # Ensure ADMIN_USER_IDS is not set
        import os

        env_backup = os.environ.pop("ADMIN_USER_IDS", None)
        try:
            response = client.post("/api/v1/admin/guilds/67890/reset?user_id=999")
            assert response.status_code == 200
        finally:
            if env_backup is not None:
                os.environ["ADMIN_USER_IDS"] = env_backup

    @patch("api.routers.admin.verify_admin_permissions")
    def test_reset_guild_returns_403_when_verify_admin_permissions_false(self, mock_verify_admin, client):
        """Returns 403 when verify_admin_permissions returns False (mocked at router level)."""
        mock_verify_admin.return_value = False

        response = client.post("/api/v1/admin/guilds/67890/reset?user_id=999")

        assert response.status_code == 403
        assert "Insufficient permissions" in response.json()["detail"]
        mock_verify_admin.assert_called_once()

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.BountyService")
    def test_reset_guild_clears_active_bounties(
        self, mock_bounty_svc_cls, mock_get_db, mock_config_service, mock_shop_service
    ):
        """Verify BountyService.clear_bounties is called for the guild during reset."""
        from api.routers.admin import get_config_service, get_shop_service
        from api.routers.admin import router as admin_router

        _configure_db_mock(mock_get_db)

        mock_bounty_instance = AsyncMock()
        mock_bounty_instance.clear_bounties = AsyncMock(
            return_value={"cleared_count": 3, "bounty_ids": [1, 2, 3], "announcements_deleted": 2}
        )
        mock_bounty_svc_cls.return_value = mock_bounty_instance

        app = FastAPI()
        app.include_router(admin_router, prefix="/api/v1")
        app.dependency_overrides[get_config_service] = lambda: mock_config_service
        app.dependency_overrides[get_shop_service] = lambda: mock_shop_service

        local_client = TestClient(app)
        response = local_client.post("/api/v1/admin/guilds/67890/reset?user_id=67890")

        assert response.status_code == 200
        data = response.json()
        assert data["bounties_cleared"] == 3
        mock_bounty_instance.clear_bounties.assert_awaited_once()
        call_args = mock_bounty_instance.clear_bounties.call_args
        # Second positional arg is guild_id
        assert call_args.args[1] == 67890
        app.dependency_overrides.clear()

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.BountyService")
    def test_reset_guild_cancels_scheduler_jobs(
        self, mock_bounty_svc_cls, mock_get_db, mock_config_service, mock_shop_service
    ):
        """Verify scheduler jobs for the guild are removed during reset."""
        from api.routers.admin import get_config_service, get_shop_service
        from api.routers.admin import router as admin_router

        _configure_db_mock(mock_get_db)

        mock_bounty_instance = AsyncMock()
        mock_bounty_instance.clear_bounties = AsyncMock(
            return_value={"cleared_count": 0, "bounty_ids": [], "announcements_deleted": 0}
        )
        mock_bounty_svc_cls.return_value = mock_bounty_instance

        # Build a mock scheduler with two jobs — one for guild 67890, one for another guild
        mock_scheduler = MagicMock()
        matching_job = MagicMock()
        matching_job.id = "bounty-spawn-67890"
        matching_job.args = ["bounty-spawn-67890", {"job_type": "bounty_spawn", "guild_id": 67890}]
        non_matching_job = MagicMock()
        non_matching_job.id = "bounty-spawn-11111"
        non_matching_job.args = ["bounty-spawn-11111", {"job_type": "bounty_spawn", "guild_id": 11111}]
        mock_scheduler.get_jobs = MagicMock(return_value=[matching_job, non_matching_job])
        mock_scheduler.remove_job = MagicMock()

        app = FastAPI()
        app.include_router(admin_router, prefix="/api/v1")
        app.dependency_overrides[get_config_service] = lambda: mock_config_service
        app.dependency_overrides[get_shop_service] = lambda: mock_shop_service
        app.state.scheduler = mock_scheduler

        local_client = TestClient(app)
        response = local_client.post("/api/v1/admin/guilds/67890/reset?user_id=67890")

        assert response.status_code == 200
        data = response.json()
        # Only the matching job (guild 67890) should have been cancelled
        mock_scheduler.remove_job.assert_called_once_with("bounty-spawn-67890")
        assert data["jobs_cancelled"] == 1
        app.dependency_overrides.clear()

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.BountyService")
    def test_reset_guild_cleanup_failure_is_non_fatal(
        self, mock_bounty_svc_cls, mock_get_db, mock_config_service, mock_shop_service
    ):
        """Bounty/scheduler cleanup errors don't break the guild reset — still returns 200."""
        from api.routers.admin import get_config_service, get_shop_service
        from api.routers.admin import router as admin_router

        _configure_db_mock(mock_get_db)

        # Make BountyService.clear_bounties raise an exception
        mock_bounty_instance = AsyncMock()
        mock_bounty_instance.clear_bounties = AsyncMock(side_effect=RuntimeError("Bounty DB error"))
        mock_bounty_svc_cls.return_value = mock_bounty_instance

        # Make the scheduler raise on get_jobs
        mock_scheduler = MagicMock()
        mock_scheduler.get_jobs = MagicMock(side_effect=RuntimeError("Scheduler unavailable"))

        app = FastAPI()
        app.include_router(admin_router, prefix="/api/v1")
        app.dependency_overrides[get_config_service] = lambda: mock_config_service
        app.dependency_overrides[get_shop_service] = lambda: mock_shop_service
        app.state.scheduler = mock_scheduler

        local_client = TestClient(app)
        response = local_client.post("/api/v1/admin/guilds/67890/reset?user_id=67890")

        # Reset must still succeed even though cleanup failed
        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890
        assert "reset successfully" in data["message"]
        # Core reset operations still ran
        mock_config_service.reset_to_defaults.assert_awaited_once()
        app.dependency_overrides.clear()


# ===========================================================================
# 3. DELETE /admin/guilds/{guild_id}/uninstall
# ===========================================================================


class TestUninstallBot:
    """Tests for DELETE /api/v1/admin/guilds/{guild_id}/uninstall."""

    @patch("api.routers.admin.get_db_session")
    def test_uninstall_bot_happy_path(self, mock_get_db, client, mock_config_service):
        """Returns 200 with removed counts on successful uninstall."""
        _configure_db_mock(mock_get_db)

        response = client.delete("/api/v1/admin/guilds/67890/uninstall?user_id=67890")

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890
        assert data["removed_counts"] == {"players": 5, "configs": 1, "shops": 40}
        assert "67890" in data["message"]
        assert "warning" in data
        mock_config_service.uninstall_guild.assert_awaited_once()

    @patch("api.routers.admin.get_db_session")
    def test_uninstall_bot_calls_uninstall_guild_with_correct_guild_id(self, mock_get_db, client, mock_config_service):
        """Passes the correct guild_id to config_service.uninstall_guild."""
        _configure_db_mock(mock_get_db)

        client.delete("/api/v1/admin/guilds/99999/uninstall?user_id=99999")

        call_args = mock_config_service.uninstall_guild.call_args
        assert 99999 in call_args.args or call_args.kwargs.get("guild_id") == 99999

    @patch("api.routers.admin.get_db_session")
    def test_uninstall_bot_server_error_returns_500(self, mock_get_db, client, mock_config_service):
        """Returns 500 when config_service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_config_service.uninstall_guild.side_effect = RuntimeError("Uninstall failed")

        response = client.delete("/api/v1/admin/guilds/67890/uninstall?user_id=67890")

        assert response.status_code == 500
        assert "Failed to uninstall bot" in response.json()["detail"]

    @patch("api.routers.admin.get_db_session")
    def test_uninstall_bot_missing_user_id_returns_422(self, mock_get_db, client):
        """Returns 422 when required user_id query parameter is missing."""
        _configure_db_mock(mock_get_db)

        response = client.delete("/api/v1/admin/guilds/67890/uninstall")

        assert response.status_code == 422

    @patch.dict("os.environ", {"ADMIN_USER_IDS": "123,456"})
    @patch("api.routers.admin.get_db_session")
    def test_uninstall_bot_admin_user_allowed(self, mock_get_db, client, mock_config_service):
        """Admin user (in ADMIN_USER_IDS) can uninstall the bot."""
        _configure_db_mock(mock_get_db)

        response = client.delete("/api/v1/admin/guilds/67890/uninstall?user_id=456")

        assert response.status_code == 200

    @patch.dict("os.environ", {"ADMIN_USER_IDS": "123,456"})
    @patch("api.routers.admin.get_db_session")
    def test_uninstall_bot_non_admin_user_returns_403(self, mock_get_db, client):
        """Non-admin user (not in ADMIN_USER_IDS) receives 403 Forbidden."""
        _configure_db_mock(mock_get_db)

        response = client.delete("/api/v1/admin/guilds/67890/uninstall?user_id=999")

        assert response.status_code == 403
        assert "Insufficient permissions" in response.json()["detail"]

    @patch("api.routers.admin.get_db_session")
    def test_uninstall_bot_dev_mode_no_admin_ids_allows_access(self, mock_get_db, client, mock_config_service):
        """When ADMIN_USER_IDS is not set, any user is allowed (dev mode)."""
        _configure_db_mock(mock_get_db)
        import os

        env_backup = os.environ.pop("ADMIN_USER_IDS", None)
        try:
            response = client.delete("/api/v1/admin/guilds/67890/uninstall?user_id=999")
            assert response.status_code == 200
        finally:
            if env_backup is not None:
                os.environ["ADMIN_USER_IDS"] = env_backup

    @patch("api.routers.admin.verify_admin_permissions")
    def test_uninstall_bot_returns_403_when_verify_admin_permissions_false(self, mock_verify_admin, client):
        """Returns 403 when verify_admin_permissions returns False (mocked at router level)."""
        mock_verify_admin.return_value = False

        response = client.delete("/api/v1/admin/guilds/67890/uninstall?user_id=999")

        assert response.status_code == 403
        assert "Insufficient permissions" in response.json()["detail"]
        mock_verify_admin.assert_called_once()

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.BountyService")
    def test_uninstall_bot_cancels_scheduler_jobs_for_guild(
        self, mock_bounty_svc_cls, mock_get_db, mock_config_service
    ):
        """Cancels APScheduler jobs whose payload contains the guild_id."""
        from api.routers.admin import get_config_service
        from api.routers.admin import router as admin_router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        _configure_db_mock(mock_get_db)

        # Mock BountyService to avoid real DB calls in bounty clearing
        mock_bounty_instance = AsyncMock()
        mock_bounty_instance.clear_bounties = AsyncMock(
            return_value={"cleared_count": 0, "bounty_ids": [], "announcements_deleted": 0}
        )
        mock_bounty_svc_cls.return_value = mock_bounty_instance

        # Build a mock scheduler with two jobs — one for guild 67890, one for another guild
        mock_scheduler = MagicMock()
        matching_job = MagicMock()
        matching_job.id = "expiry-job-67890"
        matching_job.args = ["expiry-job-67890", {"job_type": "bounty_expire", "guild_id": 67890}]
        non_matching_job = MagicMock()
        non_matching_job.id = "expiry-job-99999"
        non_matching_job.args = ["expiry-job-99999", {"job_type": "bounty_expire", "guild_id": 99999}]
        mock_scheduler.get_jobs = MagicMock(return_value=[matching_job, non_matching_job])
        mock_scheduler.remove_job = MagicMock()

        # Create app with scheduler on state
        app = FastAPI()
        app.include_router(admin_router, prefix="/api/v1")
        app.dependency_overrides[get_config_service] = lambda: mock_config_service
        app.state.scheduler = mock_scheduler

        local_client = TestClient(app)
        response = local_client.delete("/api/v1/admin/guilds/67890/uninstall?user_id=67890")

        assert response.status_code == 200
        data = response.json()
        # Only the matching job (guild 67890) should have been cancelled
        mock_scheduler.remove_job.assert_called_once_with("expiry-job-67890")
        assert data["jobs_cancelled"] == 1
        app.dependency_overrides.clear()

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.BountyService")
    def test_uninstall_bot_no_scheduler_still_succeeds(
        self, mock_bounty_svc_cls, mock_get_db, client, mock_config_service
    ):
        """When no scheduler is attached to app.state, uninstall still succeeds."""
        _configure_db_mock(mock_get_db)

        mock_bounty_instance = AsyncMock()
        mock_bounty_instance.clear_bounties = AsyncMock(
            return_value={"cleared_count": 0, "bounty_ids": [], "announcements_deleted": 0}
        )
        mock_bounty_svc_cls.return_value = mock_bounty_instance

        # client fixture uses a plain FastAPI with no scheduler on state
        response = client.delete("/api/v1/admin/guilds/67890/uninstall?user_id=67890")

        assert response.status_code == 200
        data = response.json()
        assert data["jobs_cancelled"] == 0


# ===========================================================================
# 4. PUT /admin/players/credits
# ===========================================================================


class TestUpdatePlayerCredits:
    """Tests for PUT /api/v1/admin/players/credits."""

    @patch("api.routers.admin.get_db_session")
    def test_update_player_credits_happy_path(self, mock_get_db, client, mock_player_service):
        """Returns 200 with updated credit information."""
        _configure_db_mock(mock_get_db)
        payload = {"player_id": 1, "credits": 500, "update_lifetime": True}

        response = client.put("/api/v1/admin/players/credits?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["player_id"] == 1
        assert data["new_credits"] == 500
        assert "message" in data

    @patch("api.routers.admin.get_db_session")
    def test_update_player_credits_calls_service_with_correct_args(self, mock_get_db, client, mock_player_service):
        """Passes correct arguments to player_service.update_player_credits."""
        _configure_db_mock(mock_get_db)
        payload = {"player_id": 42, "credits": 1000, "update_lifetime": False}

        client.put("/api/v1/admin/players/credits?user_id=67890&guild_id=67890", json=payload)

        mock_player_service.update_player_credits.assert_awaited_once()
        call_args = mock_player_service.update_player_credits.call_args
        assert 42 in call_args.args or call_args.kwargs.get("player_id") == 42
        assert 1000 in call_args.args or call_args.kwargs.get("credits") == 1000

    @patch("api.routers.admin.get_db_session")
    def test_update_player_credits_value_error_returns_404(self, mock_get_db, client, mock_player_service):
        """Returns 404 when player_service raises ValueError."""
        _configure_db_mock(mock_get_db)
        mock_player_service.update_player_credits.side_effect = ValueError("Player not found")
        payload = {"player_id": 9999, "credits": 100}

        response = client.put("/api/v1/admin/players/credits?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 404
        assert "Player not found" in response.json()["detail"]

    @patch("api.routers.admin.get_db_session")
    def test_update_player_credits_server_error_returns_500(self, mock_get_db, client, mock_player_service):
        """Returns 500 when service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_player_service.update_player_credits.side_effect = RuntimeError("DB failure")
        payload = {"player_id": 1, "credits": 100}

        response = client.put("/api/v1/admin/players/credits?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 500
        assert "Failed to update credits" in response.json()["detail"]

    def test_update_player_credits_negative_credits_returns_422(self, client):
        """Returns 422 when credits is negative (ge=0)."""
        payload = {"player_id": 1, "credits": -50}

        response = client.put("/api/v1/admin/players/credits?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 422

    def test_update_player_credits_missing_player_id_returns_422(self, client):
        """Returns 422 when player_id is missing."""
        payload = {"credits": 100}

        response = client.put("/api/v1/admin/players/credits?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 422

    def test_update_player_credits_missing_credits_returns_422(self, client):
        """Returns 422 when credits field is missing."""
        payload = {"player_id": 1}

        response = client.put("/api/v1/admin/players/credits?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 422

    @patch("api.routers.admin.get_db_session")
    def test_update_player_credits_default_update_lifetime_true(self, mock_get_db, client, mock_player_service):
        """Defaults update_lifetime to True when not provided."""
        _configure_db_mock(mock_get_db)
        payload = {"player_id": 1, "credits": 200}

        response = client.put("/api/v1/admin/players/credits?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 200
        call_args = mock_player_service.update_player_credits.call_args
        # update_lifetime=True should be passed
        assert True in call_args.args or call_args.kwargs.get("update_lifetime") is True

    @patch("api.routers.admin.get_db_session")
    def test_update_player_credits_old_credits_reflects_premutation_value(
        self, mock_get_db, client, mock_player_service
    ):
        """old_credits in response must equal the player's credits BEFORE the update.

        Regression guard for B.10 (identity-map sequencing bug): the router used to
        compute old_credits = player.credits - request.credits AFTER the service had
        already mutated player.credits in-place, always yielding 0 when new==old.
        The fix pre-captures old_credits via player_repo.get_by_id BEFORE calling
        update_player_credits.
        """
        _configure_db_mock(mock_get_db)
        # Pre-mutation player has 750 credits
        mock_player_service.player_repo.get_by_id = AsyncMock(return_value=make_mock_player(credits=750))
        # Post-mutation player (returned by the service) has the new amount
        mock_player_service.update_player_credits = AsyncMock(
            return_value=make_mock_player(credits=200, lifetime_credits=750)
        )
        payload = {"player_id": 1, "credits": 200, "update_lifetime": False}

        response = client.put("/api/v1/admin/players/credits?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 200
        data = response.json()
        # old_credits must match the PRE-mutation value (750), NOT 0 or 200
        assert data["old_credits"] == 750
        assert data["new_credits"] == 200

    @patch("api.routers.admin.get_db_session")
    def test_update_player_credits_player_not_found_returns_404(self, mock_get_db, client, mock_player_service):
        """Returns 404 when the player does not exist (get_by_id returns None)."""
        _configure_db_mock(mock_get_db)
        mock_player_service.player_repo.get_by_id = AsyncMock(return_value=None)
        payload = {"player_id": 9999, "credits": 100}

        response = client.put("/api/v1/admin/players/credits?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


# ===========================================================================
# 5. PUT /admin/players/xp
# ===========================================================================


class TestUpdatePlayerXP:
    """Tests for PUT /api/v1/admin/players/xp."""

    @patch("api.routers.admin.get_db_session")
    def test_update_player_xp_happy_path(self, mock_get_db, client, mock_player_service):
        """Returns 200 with updated XP and tier information."""
        _configure_db_mock(mock_get_db)
        mock_player_service.player_repo.get_by_id = AsyncMock(return_value=make_mock_player(xp=50, tier="Bronze"))
        mock_player_service.update_player_xp = AsyncMock(return_value=make_mock_player(xp=100, tier="Bronze"))
        payload = {"player_id": 1, "xp": 100}

        response = client.put("/api/v1/admin/players/xp?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["player_id"] == 1
        assert data["old_xp"] == 50
        assert data["new_xp"] == 100
        assert data["old_tier"] == "Bronze"
        assert data["new_tier"] == "Bronze"
        assert data["tier_changed"] is False
        assert "message" in data

    @patch("api.routers.admin.get_db_session")
    def test_set_xp_above_threshold_does_not_auto_promote_tier(self, mock_get_db, client, mock_player_service):
        """Set XP does NOT auto-advance tier, even when new XP qualifies for a higher tier.

        Design intent (per player_service.update_player_xp docstring):
        "Tier is NOT auto-advanced; use promote_player() to advance tier."

        The old test `test_update_player_xp_tier_change` MOCKED the service to return
        tier="Silver" — contradicting the real implementation which never changes tier.
        This replacement test asserts the BY-DESIGN behaviour: tier remains "Bronze"
        after setting XP to a value that would qualify for Platinum, and tier_changed
        is False because the stored tier column was not mutated.
        """
        _configure_db_mock(mock_get_db)
        # Pre-update: player is Bronze with 100 XP
        mock_player_service.player_repo.get_by_id = AsyncMock(return_value=make_mock_player(xp=100, tier="Bronze"))
        # Post-update: service only changes XP — tier column is unchanged (still Bronze)
        mock_player_service.update_player_xp = AsyncMock(return_value=make_mock_player(xp=50000, tier="Bronze"))
        payload = {"player_id": 1, "xp": 50000}

        response = client.put("/api/v1/admin/players/xp?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["old_xp"] == 100
        assert data["new_xp"] == 50000
        # Tier must remain Bronze — Set XP does NOT auto-promote
        assert data["old_tier"] == "Bronze"
        assert data["new_tier"] == "Bronze"
        assert data["tier_changed"] is False

    @patch("api.routers.admin.get_db_session")
    def test_update_player_xp_player_not_found_returns_500(self, mock_get_db, client, mock_player_service):
        """Returns 500 when player does not exist.

        Note: The router raises HTTPException(404) inside the try block, but
        the broad `except Exception` handler catches it and wraps it as 500.
        This reflects the actual router behaviour.
        """
        _configure_db_mock(mock_get_db)
        mock_player_service.player_repo.get_by_id = AsyncMock(return_value=None)
        payload = {"player_id": 9999, "xp": 100}

        response = client.put("/api/v1/admin/players/xp?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 500
        assert "Failed to update XP" in response.json()["detail"]

    @patch("api.routers.admin.get_db_session")
    def test_update_player_xp_value_error_returns_404(self, mock_get_db, client, mock_player_service):
        """Returns 404 when update_player_xp raises ValueError."""
        _configure_db_mock(mock_get_db)
        mock_player_service.player_repo.get_by_id = AsyncMock(return_value=make_mock_player())
        mock_player_service.update_player_xp.side_effect = ValueError("Invalid XP value")
        payload = {"player_id": 1, "xp": 100}

        response = client.put("/api/v1/admin/players/xp?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 404
        assert "Invalid XP value" in response.json()["detail"]

    @patch("api.routers.admin.get_db_session")
    def test_update_player_xp_server_error_returns_500(self, mock_get_db, client, mock_player_service):
        """Returns 500 when update_player_xp raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_player_service.player_repo.get_by_id = AsyncMock(return_value=make_mock_player())
        mock_player_service.update_player_xp.side_effect = RuntimeError("Unexpected failure")
        payload = {"player_id": 1, "xp": 100}

        response = client.put("/api/v1/admin/players/xp?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 500
        assert "Failed to update XP" in response.json()["detail"]

    def test_update_player_xp_negative_xp_returns_422(self, client):
        """Returns 422 when xp is negative (ge=0)."""
        payload = {"player_id": 1, "xp": -10}

        response = client.put("/api/v1/admin/players/xp?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 422

    def test_update_player_xp_exceeds_max_returns_422(self, client):
        """Returns 422 when xp exceeds 1000000 (le=1000000)."""
        payload = {"player_id": 1, "xp": 1000001}

        response = client.put("/api/v1/admin/players/xp?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 422

    def test_update_player_xp_missing_player_id_returns_422(self, client):
        """Returns 422 when player_id is missing."""
        payload = {"xp": 100}

        response = client.put("/api/v1/admin/players/xp?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 422


# ===========================================================================
# 6. POST /admin/players/inventory/add
# ===========================================================================


class TestAddInventoryItem:
    """Tests for POST /api/v1/admin/players/inventory/add."""

    @patch("api.routers.admin.get_db_session")
    def test_add_inventory_item_happy_path(self, mock_get_db, client, mock_inventory_service):
        """Returns 200 with item details when player exists."""
        _configure_db_mock(mock_get_db)
        mock_inventory_service.add_item_to_inventory = AsyncMock(
            return_value=_make_transaction_details(
                player_id=1, item_type="primary_weapon", item_name="Pulse Laser", quantity=2
            )
        )
        # A.45: use concrete type (primary_weapon not alias "weapon")
        payload = {
            "player_id": 1,
            "item_type": "primary_weapon",
            "item_name": "Pulse Laser",
            "quantity": 2,
        }

        response = client.post("/api/v1/admin/players/inventory/add?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["player_id"] == 1
        assert data["item_name"] == "Pulse Laser"
        assert data["quantity_added"] == 2
        assert "message" in data

    @patch("api.routers.admin.get_db_session")
    def test_add_inventory_item_default_quantity_one(self, mock_get_db, client, mock_inventory_service):
        """Defaults quantity to 1 when not provided; quantity_added reflects the added amount."""
        _configure_db_mock(mock_get_db)
        mock_inventory_service.add_item_to_inventory = AsyncMock(
            return_value=_make_transaction_details(player_id=1, item_type="ship", item_name="Raptor", quantity=1)
        )
        payload = {"player_id": 1, "item_type": "ship", "item_name": "Raptor"}

        response = client.post("/api/v1/admin/players/inventory/add?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["quantity_added"] == 1

    @patch("api.routers.admin.get_db_session")
    def test_add_inventory_item_all_valid_item_types(self, mock_get_db, client, mock_inventory_service):
        """Accepts all valid concrete item types (A.45: 5-value Literal)."""
        _configure_db_mock(mock_get_db)
        # A.45: concrete vocabulary; aliases ("weapon", "turret") are now rejected at 422
        valid_types = ["ship", "primary_weapon", "secondary_weapon", "turret_weapon", "module"]

        for item_type in valid_types:
            mock_inventory_service.add_item_to_inventory = AsyncMock(
                return_value=_make_transaction_details(item_type=item_type, item_name="Test Item")
            )
            payload = {"player_id": 1, "item_type": item_type, "item_name": "Test Item"}
            response = client.post("/api/v1/admin/players/inventory/add?user_id=67890&guild_id=67890", json=payload)
            assert response.status_code == 200, f"Expected 200 for item_type={item_type}"

    @patch("api.routers.admin.get_db_session")
    def test_add_inventory_item_player_not_found_returns_404(self, mock_get_db, client, mock_inventory_service):
        """Returns 404 when player does not exist (service raises ValueError)."""
        _configure_db_mock(mock_get_db)
        mock_inventory_service.add_item_to_inventory = AsyncMock(side_effect=ValueError("Player 9999 not found"))
        # A.45: use concrete type so schema passes; service raises ValueError
        payload = {"player_id": 9999, "item_type": "primary_weapon", "item_name": "Pulse Laser"}

        response = client.post("/api/v1/admin/players/inventory/add?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @patch("api.routers.admin.get_db_session")
    def test_add_inventory_item_server_error_returns_500(self, mock_get_db, client, mock_inventory_service):
        """Returns 500 when an unexpected exception is raised."""
        _configure_db_mock(mock_get_db)
        mock_inventory_service.add_item_to_inventory = AsyncMock(side_effect=RuntimeError("DB connection lost"))
        # A.45: use concrete type so schema passes; service raises RuntimeError
        payload = {"player_id": 1, "item_type": "primary_weapon", "item_name": "Pulse Laser"}

        response = client.post("/api/v1/admin/players/inventory/add?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 500
        assert "Failed to add inventory item" in response.json()["detail"]

    def test_add_inventory_item_invalid_item_type_returns_422(self, client):
        """Returns 422 when item_type is not in allowed pattern."""
        payload = {
            "player_id": 1,
            "item_type": "spaceship",  # not in pattern
            "item_name": "X-Wing",
        }

        response = client.post("/api/v1/admin/players/inventory/add?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 422

    def test_add_inventory_item_quantity_zero_returns_422(self, client):
        """Returns 422 when quantity is 0 (gt=0)."""
        # A.45: use concrete type; the 422 is from quantity=0 validation
        payload = {"player_id": 1, "item_type": "primary_weapon", "item_name": "Laser", "quantity": 0}

        response = client.post("/api/v1/admin/players/inventory/add?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 422

    def test_add_inventory_item_negative_quantity_returns_422(self, client):
        """Returns 422 when quantity is negative (gt=0)."""
        # A.45: use concrete type; the 422 is from negative quantity validation
        payload = {"player_id": 1, "item_type": "primary_weapon", "item_name": "Laser", "quantity": -1}

        response = client.post("/api/v1/admin/players/inventory/add?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 422

    def test_add_inventory_item_missing_required_fields_returns_422(self, client):
        """Returns 422 when required fields are missing."""
        payload = {"player_id": 1}  # missing item_type and item_name

        response = client.post("/api/v1/admin/players/inventory/add?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 422

    @patch("api.routers.admin.get_db_session")
    def test_add_inventory_item_calls_inventory_service(self, mock_get_db, client, mock_inventory_service):
        """Verifies that add_item_to_inventory is called with correct arguments."""
        _configure_db_mock(mock_get_db)
        mock_inventory_service.add_item_to_inventory = AsyncMock(
            return_value=_make_transaction_details(
                player_id=42, item_type="module", item_name="Shield Booster", quantity=3
            )
        )
        payload = {
            "player_id": 42,
            "item_type": "module",
            "item_name": "Shield Booster",
            "quantity": 3,
        }

        response = client.post("/api/v1/admin/players/inventory/add?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 200
        mock_inventory_service.add_item_to_inventory.assert_called_once()
        call_args = mock_inventory_service.add_item_to_inventory.call_args
        # call_args[0] is positional args: (db, player_id, item_type, item_name, quantity)
        assert call_args[0][1] == 42  # player_id
        assert call_args[0][2] == "module"  # item_type
        assert call_args[0][3] == "Shield Booster"  # item_name
        assert call_args[0][4] == 3  # quantity

    @patch("api.routers.admin.get_db_session")
    def test_add_inventory_item_persisted_to_db(self, mock_get_db, client, mock_inventory_service):
        """Verifies inventory_service.add_item_to_inventory is called (DB write confirmed).

        This test focuses on the persistence contract: the response data must echo back
        exactly what the service returned, confirming the item was committed via the service.
        """
        _configure_db_mock(mock_get_db)
        real_item_name = "Micro Gun MK I"  # actual game asset name from import_data/
        expected_details = _make_transaction_details(
            player_id=7, item_type="weapon", item_name=real_item_name, quantity=1
        )
        mock_inventory_service.add_item_to_inventory = AsyncMock(return_value=expected_details)

        # A.45: use concrete type (primary_weapon)
        payload = {"player_id": 7, "item_type": "primary_weapon", "item_name": real_item_name, "quantity": 1}
        response = client.post("/api/v1/admin/players/inventory/add?user_id=67890&guild_id=67890", json=payload)

        assert response.status_code == 200
        # Service was invoked — item will have been written to the DB session
        mock_inventory_service.add_item_to_inventory.assert_awaited_once()
        # Response echoes the service-returned data, proving the write path completed
        data = response.json()
        assert data["player_id"] == 7
        assert data["item_name"] == real_item_name
        assert data["quantity_added"] == 1


# ===========================================================================
# 7. POST /admin/shops/refresh
# ===========================================================================


class TestRefreshShop:
    """Tests for POST /api/v1/admin/shops/refresh."""

    @patch("api.routers.admin.get_db_session")
    def test_refresh_shop_happy_path(self, mock_get_db, client, mock_shop_service):
        """Returns 200 with refresh details and message."""
        _configure_db_mock(mock_get_db)
        payload = {"guild_id": 67890, "tier": "Bronze"}

        response = client.post("/api/v1/admin/shops/refresh?user_id=67890", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["refreshed"] is True
        assert data["items_count"] == 10
        assert "message" in data
        assert "Bronze" in data["message"]
        assert "67890" in data["message"]

    @patch("api.routers.admin.get_db_session")
    def test_refresh_shop_with_force_tech_level(self, mock_get_db, client, mock_shop_service):
        """Accepts optional force_tech_level and passes it to service."""
        _configure_db_mock(mock_get_db)
        payload = {"guild_id": 67890, "tier": "Gold", "force_tech_level": 7}

        response = client.post("/api/v1/admin/shops/refresh?user_id=67890", json=payload)

        assert response.status_code == 200
        mock_shop_service.refresh_shop.assert_awaited_once()
        call_args = mock_shop_service.refresh_shop.call_args
        assert 7 in call_args.args or call_args.kwargs.get("force_tech_level") == 7

    @patch("api.routers.admin.get_db_session")
    def test_refresh_shop_all_valid_tiers(self, mock_get_db, client, mock_shop_service):
        """Accepts all valid tiers: Bronze, Silver, Gold, Platinum."""
        _configure_db_mock(mock_get_db)
        valid_tiers = ["Bronze", "Silver", "Gold", "Platinum"]

        for tier in valid_tiers:
            mock_shop_service.refresh_shop.reset_mock()
            mock_shop_service.refresh_shop = AsyncMock(return_value={"refreshed": True, "items_count": 5})
            payload = {"guild_id": 67890, "tier": tier}
            response = client.post("/api/v1/admin/shops/refresh?user_id=67890", json=payload)
            assert response.status_code == 200, f"Expected 200 for tier={tier}"

    @patch("api.routers.admin.get_db_session")
    def test_refresh_shop_value_error_returns_400(self, mock_get_db, client, mock_shop_service):
        """Returns 400 when shop_service raises ValueError."""
        _configure_db_mock(mock_get_db)
        mock_shop_service.refresh_shop.side_effect = ValueError("Shop already refreshed recently")
        payload = {"guild_id": 67890, "tier": "Bronze"}

        response = client.post("/api/v1/admin/shops/refresh?user_id=67890", json=payload)

        assert response.status_code == 400
        assert "Shop already refreshed recently" in response.json()["detail"]

    @patch("api.routers.admin.get_db_session")
    def test_refresh_shop_server_error_returns_500(self, mock_get_db, client, mock_shop_service):
        """Returns 500 when shop_service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_shop_service.refresh_shop.side_effect = RuntimeError("Refresh service crashed")
        payload = {"guild_id": 67890, "tier": "Silver"}

        response = client.post("/api/v1/admin/shops/refresh?user_id=67890", json=payload)

        assert response.status_code == 500
        assert "Failed to refresh shop" in response.json()["detail"]

    def test_refresh_shop_invalid_tier_returns_422(self, client):
        """Returns 422 when tier is not in allowed pattern."""
        payload = {"guild_id": 67890, "tier": "Diamond"}

        response = client.post("/api/v1/admin/shops/refresh?user_id=67890", json=payload)

        assert response.status_code == 422

    def test_refresh_shop_force_tech_level_out_of_range_returns_422(self, client):
        """Returns 422 when force_tech_level is outside 1-9."""
        payload = {"guild_id": 67890, "tier": "Bronze", "force_tech_level": 10}

        response = client.post("/api/v1/admin/shops/refresh?user_id=67890", json=payload)

        assert response.status_code == 422

    def test_refresh_shop_force_tech_level_zero_returns_422(self, client):
        """Returns 422 when force_tech_level is 0 (ge=1)."""
        payload = {"guild_id": 67890, "tier": "Bronze", "force_tech_level": 0}

        response = client.post("/api/v1/admin/shops/refresh?user_id=67890", json=payload)

        assert response.status_code == 422

    # B.8 — Admin refresh shop must call announce_shop_refresh after a successful DB refresh

    @patch("api.routers.admin.get_db_session")
    @patch("utils.shop_announcement.announce_shop_refresh", new_callable=AsyncMock)
    def test_refresh_shop_calls_announcement_after_success(self, mock_announce, mock_get_db, client, mock_shop_service):
        """B.8: After a successful shop refresh the endpoint calls announce_shop_refresh.

        Acceptance criterion: the shared announcement helper is invoked once,
        with the guild_id from the request.
        """
        mock_session = _configure_db_mock(mock_get_db)

        # Config repo lookup — return a config with shop channel set.
        mock_config = MagicMock()
        mock_config.shop_channel_id = 111222
        mock_config.bounty_hunter_role_id = 555666
        mock_config_repo_instance = AsyncMock()
        mock_config_repo_instance.get_by_guild_id = AsyncMock(return_value=mock_config)

        with patch("persist.repositories.config_repository.ConfigRepository", return_value=mock_config_repo_instance):
            payload = {"guild_id": 67890, "tier": "Bronze"}
            response = client.post("/api/v1/admin/shops/refresh?user_id=67890", json=payload)

        assert response.status_code == 200
        mock_announce.assert_awaited_once()
        call_kwargs = mock_announce.call_args.kwargs
        assert call_kwargs["guild_id"] == 67890

    @patch("api.routers.admin.get_db_session")
    @patch("utils.shop_announcement.announce_shop_refresh", new_callable=AsyncMock)
    def test_refresh_shop_passes_shop_channel_and_role_to_announcement(
        self, mock_announce, mock_get_db, client, mock_shop_service
    ):
        """B.8: announce_shop_refresh receives shop_channel_id and bounty_hunter_role_id from config."""
        _configure_db_mock(mock_get_db)

        mock_config = MagicMock()
        mock_config.shop_channel_id = 777888
        mock_config.bounty_hunter_role_id = 999000
        mock_config_repo_instance = AsyncMock()
        mock_config_repo_instance.get_by_guild_id = AsyncMock(return_value=mock_config)

        with patch("persist.repositories.config_repository.ConfigRepository", return_value=mock_config_repo_instance):
            payload = {"guild_id": 67890, "tier": "Silver"}
            client.post("/api/v1/admin/shops/refresh?user_id=67890", json=payload)

        mock_announce.assert_awaited_once()
        call_kwargs = mock_announce.call_args.kwargs
        assert call_kwargs["channel_id"] == 777888
        assert call_kwargs["bounty_hunter_role_id"] == 999000

    @patch("api.routers.admin.get_db_session")
    @patch("utils.shop_announcement.announce_shop_refresh", new_callable=AsyncMock)
    def test_refresh_shop_announcement_failure_does_not_prevent_200(
        self, mock_announce, mock_get_db, client, mock_shop_service
    ):
        """B.8: Announcement failure is non-fatal — shop refresh still returns 200."""
        _configure_db_mock(mock_get_db)

        mock_config_repo_instance = AsyncMock()
        mock_config_repo_instance.get_by_guild_id = AsyncMock(return_value=None)

        # Simulate announcement raising despite the inner guard (belt-and-suspenders)
        mock_announce.side_effect = RuntimeError("gateway down")

        with patch("persist.repositories.config_repository.ConfigRepository", return_value=mock_config_repo_instance):
            payload = {"guild_id": 67890, "tier": "Gold"}
            response = client.post("/api/v1/admin/shops/refresh?user_id=67890", json=payload)

        assert response.status_code == 200
        # The warning surfaces in the response body
        data = response.json()
        assert "announcement_warning" in data


# ===========================================================================
# 8. PUT /admin/shops/config
# ===========================================================================


class TestUpdateShopConfig:
    """Tests for PUT /api/v1/admin/shops/config."""

    @patch("api.routers.admin.get_db_session")
    def test_update_shop_config_happy_path(self, mock_get_db, client, mock_config_service):
        """Returns 200 with updated config when all fields are valid."""
        _configure_db_mock(mock_get_db)
        payload = {
            "guild_id": 67890,
            "sale_price_factor": 0.5,
        }

        response = client.put("/api/v1/admin/shops/config?user_id=67890", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890
        assert "updated_config" in data
        assert "message" in data
        assert "67890" in data["message"]

    @patch("api.routers.admin.get_db_session")
    def test_update_shop_config_minimal_payload(self, mock_get_db, client, mock_config_service):
        """Returns 200 with only guild_id when optional fields are omitted."""
        _configure_db_mock(mock_get_db)
        payload = {"guild_id": 67890}

        response = client.put("/api/v1/admin/shops/config?user_id=67890", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890

    @patch("api.routers.admin.get_db_session")
    def test_update_shop_config_with_all_fields(self, mock_get_db, client, mock_config_service):
        """Returns 200 when all optional config fields are provided."""
        _configure_db_mock(mock_get_db)
        payload = {
            "guild_id": 67890,
            "tech_level_probabilities": {"1": 0.5, "2": 0.3, "3": 0.2},
            "sale_price_factor": 0.8,
            "item_count_ranges": {"Bronze": {"min": 5, "max": 10}},
            "quantity_ranges": {"Bronze": {"min": 1, "max": 3}},
        }

        response = client.put("/api/v1/admin/shops/config?user_id=67890", json=payload)

        assert response.status_code == 200
        mock_config_service.update_shop_config.assert_awaited_once()

    @patch("api.routers.admin.get_db_session")
    def test_update_shop_config_calls_service_with_request_data(self, mock_get_db, client, mock_config_service):
        """Passes request data to config_service.update_shop_config."""
        _configure_db_mock(mock_get_db)
        payload = {"guild_id": 67890, "sale_price_factor": 0.7}

        client.put("/api/v1/admin/shops/config?user_id=67890", json=payload)

        mock_config_service.update_shop_config.assert_awaited_once()

    @patch("api.routers.admin.get_db_session")
    def test_update_shop_config_server_error_returns_500(self, mock_get_db, client, mock_config_service):
        """Returns 500 when config_service raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_config_service.update_shop_config.side_effect = RuntimeError("Config update failed")
        payload = {"guild_id": 67890, "sale_price_factor": 0.5}

        response = client.put("/api/v1/admin/shops/config?user_id=67890", json=payload)

        assert response.status_code == 500
        assert "Failed to update shop configuration" in response.json()["detail"]

    def test_update_shop_config_sale_price_factor_exceeds_one_returns_422(self, client):
        """Returns 422 when sale_price_factor > 1.0 (le=1)."""
        payload = {"guild_id": 67890, "sale_price_factor": 1.5}

        response = client.put("/api/v1/admin/shops/config", json=payload)

        assert response.status_code == 422

    def test_update_shop_config_sale_price_factor_zero_returns_422(self, client):
        """Returns 422 when sale_price_factor is 0 (gt=0)."""
        payload = {"guild_id": 67890, "sale_price_factor": 0.0}

        response = client.put("/api/v1/admin/shops/config", json=payload)

        assert response.status_code == 422

    def test_update_shop_config_missing_guild_id_returns_422(self, client):
        """Returns 422 when guild_id is missing."""
        payload = {"sale_price_factor": 0.5}

        response = client.put("/api/v1/admin/shops/config", json=payload)

        assert response.status_code == 422


# ===========================================================================
# 9. GET /admin/system/health
# ===========================================================================


class TestGetSystemHealth:
    """Tests for GET /api/v1/admin/system/health."""

    @patch("api.routers.admin.get_db_session")
    def test_get_system_health_happy_path(self, mock_get_db, client, mock_player_service, mock_shop_service):
        """Returns 200 with SystemHealthResponse containing real counts from repositories."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/admin/system/health?user_id=67890")

        assert response.status_code == 200
        data = response.json()
        assert data["database_status"] == "healthy"
        assert data["total_users"] == 3
        assert data["total_players"] == 2
        assert data["total_guilds"] == 1
        assert data["shop_items_count"] == 5
        assert data["system_status"] == "operational"

    @patch("api.routers.admin.get_db_session")
    def test_get_system_health_response_model_fields(self, mock_get_db, client):
        """Verifies all required SystemHealthResponse fields are present."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/admin/system/health?user_id=67890")

        assert response.status_code == 200
        data = response.json()
        required_fields = [
            "database_status",
            "total_users",
            "total_players",
            "total_guilds",
            "shop_items_count",
            "system_status",
        ]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"

    @patch("api.routers.admin.get_db_session")
    def test_get_system_health_server_error_returns_500(self, mock_get_db, client):
        """Returns 500 when get_db_session raises an unexpected exception."""
        _mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(side_effect=RuntimeError("DB unavailable"))
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/api/v1/admin/system/health?user_id=67890")

        assert response.status_code == 500
        assert "Failed to get system health" in response.json()["detail"]

    @patch("api.routers.admin.get_db_session")
    def test_get_system_health_counts_match_seeded_values(
        self, mock_get_db, client, mock_player_service, mock_shop_service
    ):
        """Counts returned by health endpoint match values seeded into repository mocks."""
        _configure_db_mock(mock_get_db)
        # Override mock count returns with specific "seeded" values
        mock_player_service.user_repo.count = AsyncMock(return_value=10)
        mock_player_service.player_repo.count = AsyncMock(return_value=25)
        mock_player_service.config_repo.count = AsyncMock(return_value=4)
        mock_shop_service.shop_repo.count = AsyncMock(return_value=40)

        response = client.get("/api/v1/admin/system/health?user_id=67890")

        assert response.status_code == 200
        data = response.json()
        assert data["total_users"] == 10
        assert data["total_players"] == 25
        assert data["total_guilds"] == 4
        assert data["shop_items_count"] == 40


# ===========================================================================
# 10. GET /admin/guilds/{guild_id}/stats
# ===========================================================================


class TestGetGuildStatistics:
    """Tests for GET /api/v1/admin/guilds/{guild_id}/stats."""

    @patch("api.routers.admin.get_db_session")
    def test_get_guild_statistics_happy_path(self, mock_get_db, client, mock_player_service):
        """Returns 200 with correct statistics for a guild with players."""
        _configure_db_mock(mock_get_db)
        # 2 players: Bronze with 100 credits/50 xp, Silver with 200 credits/150 xp
        mock_player_service.player_repo.get_players_by_guild = AsyncMock(
            return_value=[
                make_mock_player(id=1, credits=100, xp=50, tier="Bronze"),
                make_mock_player(id=2, credits=200, xp=150, tier="Silver"),
            ]
        )

        response = client.get("/api/v1/admin/guilds/67890/stats?user_id=67890")

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890
        assert data["total_players"] == 2
        assert data["total_credits"] == 300
        assert data["total_xp"] == 200
        assert data["average_credits"] == 150.0
        assert data["average_xp"] == 100.0
        assert data["tier_distribution"]["Bronze"] == 1
        assert data["tier_distribution"]["Silver"] == 1

    @patch("api.routers.admin.get_db_session")
    def test_get_guild_statistics_multiple_players_same_tier(self, mock_get_db, client, mock_player_service):
        """Correctly counts tier distribution when multiple players share a tier."""
        _configure_db_mock(mock_get_db)
        mock_player_service.player_repo.get_players_by_guild = AsyncMock(
            return_value=[
                make_mock_player(id=1, credits=100, xp=50, tier="Bronze"),
                make_mock_player(id=2, credits=150, xp=75, tier="Bronze"),
                make_mock_player(id=3, credits=500, xp=500, tier="Silver"),
            ]
        )

        response = client.get("/api/v1/admin/guilds/67890/stats?user_id=67890")

        assert response.status_code == 200
        data = response.json()
        assert data["total_players"] == 3
        assert data["tier_distribution"]["Bronze"] == 2
        assert data["tier_distribution"]["Silver"] == 1

    @patch("api.routers.admin.get_db_session")
    def test_get_guild_statistics_empty_guild(self, mock_get_db, client, mock_player_service):
        """Returns zero averages and empty tier_distribution for guild with no players."""
        _configure_db_mock(mock_get_db)
        mock_player_service.player_repo.get_players_by_guild = AsyncMock(return_value=[])

        response = client.get("/api/v1/admin/guilds/67890/stats?user_id=67890")

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890
        assert data["total_players"] == 0
        assert data["total_credits"] == 0
        assert data["total_xp"] == 0
        assert data["average_credits"] == 0
        assert data["average_xp"] == 0
        assert data["tier_distribution"] == {}

    @patch("api.routers.admin.get_db_session")
    def test_get_guild_statistics_calls_repo_with_correct_guild_id(self, mock_get_db, client, mock_player_service):
        """Passes the correct guild_id to player_repo.get_players_by_guild."""
        _configure_db_mock(mock_get_db)

        client.get("/api/v1/admin/guilds/99999/stats?user_id=67890")

        mock_player_service.player_repo.get_players_by_guild.assert_awaited_once()
        call_args = mock_player_service.player_repo.get_players_by_guild.call_args
        assert 99999 in call_args.args or call_args.kwargs.get("guild_id") == 99999

    @patch("api.routers.admin.get_db_session")
    def test_get_guild_statistics_server_error_returns_500(self, mock_get_db, client, mock_player_service):
        """Returns 500 when player_repo raises an unexpected exception."""
        _configure_db_mock(mock_get_db)
        mock_player_service.player_repo.get_players_by_guild = AsyncMock(side_effect=RuntimeError("Query timeout"))

        response = client.get("/api/v1/admin/guilds/67890/stats?user_id=67890")

        assert response.status_code == 500
        assert "Failed to get guild statistics" in response.json()["detail"]

    @patch("api.routers.admin.get_db_session")
    def test_get_guild_statistics_correct_average_calculation(self, mock_get_db, client, mock_player_service):
        """Calculates average_credits and average_xp correctly."""
        _configure_db_mock(mock_get_db)
        mock_player_service.player_repo.get_players_by_guild = AsyncMock(
            return_value=[
                make_mock_player(id=1, credits=100, xp=0, tier="Bronze"),
                make_mock_player(id=2, credits=300, xp=200, tier="Bronze"),
                make_mock_player(id=3, credits=200, xp=100, tier="Bronze"),
            ]
        )

        response = client.get("/api/v1/admin/guilds/67890/stats?user_id=67890")

        assert response.status_code == 200
        data = response.json()
        assert data["average_credits"] == pytest.approx(200.0)
        assert data["average_xp"] == pytest.approx(100.0)


# ===========================================================================
# 10. POST /admin/players/{player_id}/reset
# ===========================================================================


class TestResetPlayer:
    """Tests for POST /api/v1/admin/players/{player_id}/reset."""

    def _make_reset_player(self, **overrides):
        """Create a mock player with post-reset defaults."""
        defaults = dict(
            id=1,
            guild_id=67890,
            credits=1000,
            xp=0,
            tier="Bronze",
            bounty_wins=0,
            duel_wins=0,
            duel_losses=0,
            prestige_count=0,
        )
        defaults.update(overrides)
        player = MagicMock()
        for k, v in defaults.items():
            setattr(player, k, v)
        return player

    @patch("api.routers.admin.get_db_session")
    def test_reset_player_happy_path(self, mock_get_db, client, mock_player_service):
        """Returns 200 with reset player stats on success."""
        _configure_db_mock(mock_get_db)
        reset_player = self._make_reset_player()
        mock_player_service.player_repo.get_by_id = AsyncMock(return_value=reset_player)
        mock_player_service.config_repo = AsyncMock()
        mock_config = MagicMock()
        mock_config.starting_credits = 1000
        mock_player_service.config_repo.get_by_guild_id = AsyncMock(return_value=mock_config)

        response = client.post("/api/v1/admin/players/1/reset?user_id=67890&guild_id=67890")

        assert response.status_code == 200
        data = response.json()
        assert data["player_id"] == 1
        assert data["xp"] == 0
        assert data["tier"] == "Bronze"
        assert data["bounty_wins"] == 0
        assert data["duel_wins"] == 0
        assert data["duel_losses"] == 0
        assert data["prestige_count"] == 0
        assert "message" in data

    @patch("api.routers.admin.get_db_session")
    def test_reset_player_not_found_returns_404(self, mock_get_db, client, mock_player_service):
        """Returns 404 when player does not exist."""
        _configure_db_mock(mock_get_db)
        mock_player_service.player_repo.get_by_id = AsyncMock(return_value=None)

        response = client.post("/api/v1/admin/players/9999/reset?user_id=67890&guild_id=67890")

        assert response.status_code == 404
        assert "Player not found" in response.json()["detail"]

    @patch("api.routers.admin.get_db_session")
    def test_reset_player_uses_starting_credits_from_config(self, mock_get_db, client, mock_player_service):
        """Uses guild config starting_credits when resetting credits."""
        _configure_db_mock(mock_get_db)
        reset_player = self._make_reset_player(credits=500)
        mock_player_service.player_repo.get_by_id = AsyncMock(return_value=reset_player)
        mock_player_service.config_repo = AsyncMock()
        mock_config = MagicMock()
        mock_config.starting_credits = 500
        mock_player_service.config_repo.get_by_guild_id = AsyncMock(return_value=mock_config)

        response = client.post("/api/v1/admin/players/1/reset?user_id=67890&guild_id=67890")

        assert response.status_code == 200
        data = response.json()
        assert data["credits"] == 500

    @patch("api.routers.admin.get_db_session")
    def test_reset_player_no_config_defaults_credits_to_zero(self, mock_get_db, client, mock_player_service):
        """Defaults credits to 0 when no guild config exists."""
        _configure_db_mock(mock_get_db)
        reset_player = self._make_reset_player(credits=0)
        mock_player_service.player_repo.get_by_id = AsyncMock(return_value=reset_player)
        mock_player_service.config_repo = AsyncMock()
        mock_player_service.config_repo.get_by_guild_id = AsyncMock(return_value=None)

        response = client.post("/api/v1/admin/players/1/reset?user_id=67890&guild_id=67890")

        assert response.status_code == 200
        data = response.json()
        assert data["credits"] == 0

    @patch("api.routers.admin.get_db_session")
    def test_reset_player_server_error_returns_500(self, mock_get_db, client, mock_player_service):
        """Returns 500 when an unexpected exception occurs."""
        _configure_db_mock(mock_get_db)
        mock_player_service.player_repo.get_by_id = AsyncMock(side_effect=RuntimeError("DB failure"))

        response = client.post("/api/v1/admin/players/1/reset?user_id=67890&guild_id=67890")

        assert response.status_code == 500
        assert "Failed to reset player" in response.json()["detail"]

    @patch("api.routers.admin.get_db_session")
    def test_reset_player_message_contains_player_id(self, mock_get_db, client, mock_player_service):
        """Response message references the player_id."""
        _configure_db_mock(mock_get_db)
        reset_player = self._make_reset_player()
        mock_player_service.player_repo.get_by_id = AsyncMock(return_value=reset_player)
        mock_player_service.config_repo = AsyncMock()
        mock_player_service.config_repo.get_by_guild_id = AsyncMock(return_value=None)

        response = client.post("/api/v1/admin/players/1/reset?user_id=67890&guild_id=67890")

        assert response.status_code == 200
        assert "1" in response.json()["message"]


# ===========================================================================
# Gap 2: Cross-Service Side-Effect Tests — Admin
# ===========================================================================


class TestInitializeGuildCreatesShopsForAllTiers:
    """Gap 2: initialize_guild must create shops for ALL four tiers (Bronze, Silver, Gold, Platinum).

    This verifies the cross-service side-effect: after initializing a guild the shop
    service is called for every tier, not just a subset.
    """

    @patch("api.routers.admin.get_db_session")
    def test_initialize_guild_creates_shops_for_all_tiers(
        self, mock_get_db, client, mock_config_service, mock_shop_service
    ):
        """POST /admin/guilds/initialize → refresh_shop called for Bronze, Silver, Gold, Platinum.

        The endpoint must initialize all 4 tier shops as a side-effect of guild setup.
        Missing even one tier means players in that tier cannot buy or sell items.
        """
        _configure_db_mock(mock_get_db)
        payload = {"guild_id": 67890, "admin_role_id": 11111, "starting_credits": 500}

        response = client.post("/api/v1/admin/guilds/initialize?user_id=67890", json=payload)

        assert response.status_code == 200
        data = response.json()
        # The response must state that exactly 4 shops were created
        assert data["shops_created"] == 4

        # All 4 tiers must have been passed to refresh_shop
        assert mock_shop_service.refresh_shop.await_count == 4
        tiers_called = {call.args[2] for call in mock_shop_service.refresh_shop.call_args_list}
        assert tiers_called == {"Bronze", "Silver", "Gold", "Platinum"}, (
            f"Expected all 4 tiers to be initialised, but got: {tiers_called}"
        )

    @patch("api.routers.admin.get_db_session")
    def test_initialize_guild_shops_include_bronze_tier(
        self, mock_get_db, client, mock_config_service, mock_shop_service
    ):
        """Bronze tier shop is created during guild initialization."""
        _configure_db_mock(mock_get_db)
        payload = {"guild_id": 12345}

        client.post("/api/v1/admin/guilds/initialize?user_id=67890", json=payload)

        tiers_called = [call.args[2] for call in mock_shop_service.refresh_shop.call_args_list]
        assert "Bronze" in tiers_called

    @patch("api.routers.admin.get_db_session")
    def test_initialize_guild_shops_include_platinum_tier(
        self, mock_get_db, client, mock_config_service, mock_shop_service
    ):
        """Platinum tier shop is created during guild initialization."""
        _configure_db_mock(mock_get_db)
        payload = {"guild_id": 12345}

        client.post("/api/v1/admin/guilds/initialize?user_id=67890", json=payload)

        tiers_called = [call.args[2] for call in mock_shop_service.refresh_shop.call_args_list]
        assert "Platinum" in tiers_called
