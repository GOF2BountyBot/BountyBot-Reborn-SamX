"""Tests for notifications-related schema and config_repository changes.

Covers:
- GuildConfigResponse includes shop_announcements_role_id
- UpdateConfigRequest includes shop_announcements_role_id
- InitializeGuildRequest accepts shop_announcements_role_id
- GuildInitializationResponse includes shop_announcements_role_id
- ConfigRepository.get_config_summary includes shop_announcements_role_id
- ConfigRepository.reset_to_defaults preserves shop_announcements_role_id
- Admin router initialize_guild passes shop_announcements_role_id to config
"""

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Path and mock setup (mirrors other api test files)
# ---------------------------------------------------------------------------

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

# Mock shared / bblogger
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_config(guild_id=12345, shop_announcements_role_id=99999):
    """Build a minimal GuildConfig mock with shop_announcements_role_id."""
    cfg = MagicMock()
    cfg.guild_id = guild_id
    cfg.admin_role_id = None
    cfg.starting_credits = 0
    cfg.sale_price_factor = 0.8
    cfg.xp_thresholds = {"Silver": 1000, "Gold": 5000, "Platinum": 15000}
    cfg.shop_config = {}
    cfg.category_id = None
    cfg.shop_channel_id = None
    cfg.bronze_bounty_channel_id = None
    cfg.silver_bounty_channel_id = None
    cfg.gold_bounty_channel_id = None
    cfg.platinum_bounty_channel_id = None
    cfg.hunting_channel_id = None
    cfg.discussion_channel_id = None
    cfg.image_channel_id = None
    cfg.bounty_hunter_role_id = None
    cfg.bronze_role_id = None
    cfg.silver_role_id = None
    cfg.gold_role_id = None
    cfg.platinum_role_id = None
    cfg.shop_announcements_role_id = shop_announcements_role_id
    cfg.bounty_max_per_tier = {"bronze": 3, "silver": 3, "gold": 3, "platinum": 3}
    cfg.bounty_expiry_minutes = 480
    cfg.bounty_spawn_interval_minutes = 60
    cfg.next_spawn_check_at = None
    cfg.created_at = MagicMock()
    cfg.created_at.isoformat.return_value = "2026-01-01T00:00:00"
    cfg.updated_at = MagicMock()
    cfg.updated_at.isoformat.return_value = "2026-01-01T00:00:00"
    cfg.ship_count_range = {"min": 3, "max": 5}
    cfg.weapon_count_range = {"min": 3, "max": 5}
    cfg.module_count_range = {"min": 3, "max": 5}
    cfg.turret_count_range = {"min": 3, "max": 5}
    cfg.ship_quantity_range = {"min": 1, "max": 1}
    cfg.weapon_quantity_range = {"min": 2, "max": 4}
    cfg.module_quantity_range = {"min": 2, "max": 4}
    cfg.turret_quantity_range = {"min": 2, "max": 4}
    cfg.tech_level_probabilities = {"same_level": 0.70, "one_lower": 0.20, "two_lower": 0.10}
    return cfg


# ---------------------------------------------------------------------------
# Schema Tests
# ---------------------------------------------------------------------------


class TestGuildConfigResponseSchema:
    """GuildConfigResponse must include shop_announcements_role_id."""

    def test_schema_accepts_shop_announcements_role_id(self):
        """GuildConfigResponse should accept shop_announcements_role_id field."""
        from api.schemas.config_schema import GuildConfigResponse

        response = GuildConfigResponse(
            guild_id=12345,
            configured=True,
            admin_role_configured=False,
            starting_credits=0,
            sale_price_factor=0.8,
            xp_thresholds={"Silver": 1000, "Gold": 5000, "Platinum": 15000},
            shop_config={},
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
            shop_announcements_role_id=99999,
        )
        assert response.shop_announcements_role_id == 99999

    def test_schema_defaults_shop_announcements_role_id_to_none(self):
        """GuildConfigResponse.shop_announcements_role_id defaults to None."""
        from api.schemas.config_schema import GuildConfigResponse

        response = GuildConfigResponse(
            guild_id=12345,
            configured=True,
            admin_role_configured=False,
            starting_credits=0,
            sale_price_factor=0.8,
            xp_thresholds={"Silver": 1000, "Gold": 5000, "Platinum": 15000},
            shop_config={},
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
        )
        assert response.shop_announcements_role_id is None


class TestUpdateConfigRequestSchema:
    """UpdateConfigRequest must include shop_announcements_role_id."""

    def test_schema_accepts_shop_announcements_role_id(self):
        """UpdateConfigRequest should accept shop_announcements_role_id."""
        from api.schemas.config_schema import UpdateConfigRequest

        req = UpdateConfigRequest(guild_id=12345, shop_announcements_role_id=55555)
        assert req.shop_announcements_role_id == 55555

    def test_schema_defaults_shop_announcements_role_id_to_none(self):
        """UpdateConfigRequest.shop_announcements_role_id defaults to None."""
        from api.schemas.config_schema import UpdateConfigRequest

        req = UpdateConfigRequest(guild_id=12345)
        assert req.shop_announcements_role_id is None


class TestInitializeGuildRequestSchema:
    """InitializeGuildRequest must include shop_announcements_role_id."""

    def test_schema_accepts_shop_announcements_role_id(self):
        """InitializeGuildRequest should accept shop_announcements_role_id."""
        from api.schemas.admin_schema import InitializeGuildRequest

        req = InitializeGuildRequest(guild_id=12345, shop_announcements_role_id=77777)
        assert req.shop_announcements_role_id == 77777

    def test_schema_defaults_shop_announcements_role_id_to_none(self):
        """InitializeGuildRequest.shop_announcements_role_id defaults to None."""
        from api.schemas.admin_schema import InitializeGuildRequest

        req = InitializeGuildRequest(guild_id=12345)
        assert req.shop_announcements_role_id is None


class TestGuildInitializationResponseSchema:
    """GuildInitializationResponse must include shop_announcements_role_id."""

    def test_schema_includes_shop_announcements_role_id(self):
        """GuildInitializationResponse should include shop_announcements_role_id."""
        from api.schemas.admin_schema import GuildInitializationResponse

        resp = GuildInitializationResponse(
            guild_id=12345,
            admin_role_id=None,
            shops_created=4,
            config_created=True,
            message="ok",
            shop_announcements_role_id=88888,
        )
        assert resp.shop_announcements_role_id == 88888

    def test_schema_defaults_shop_announcements_role_id_to_none(self):
        """GuildInitializationResponse.shop_announcements_role_id defaults to None."""
        from api.schemas.admin_schema import GuildInitializationResponse

        resp = GuildInitializationResponse(
            guild_id=12345,
            admin_role_id=None,
            shops_created=4,
            config_created=True,
            message="ok",
        )
        assert resp.shop_announcements_role_id is None


# ---------------------------------------------------------------------------
# ConfigRepository Tests
# ---------------------------------------------------------------------------


class TestConfigRepositoryGetSummary:
    """ConfigRepository.get_config_summary must include shop_announcements_role_id."""

    @pytest.mark.asyncio
    async def test_get_config_summary_includes_shop_announcements_role_id(self):
        """get_config_summary must include shop_announcements_role_id in the result."""
        from persist.repositories.config_repository import ConfigRepository

        repo = ConfigRepository()
        mock_db = AsyncMock()

        cfg = _make_mock_config(guild_id=12345, shop_announcements_role_id=99999)

        # Use async_generator pattern since get_by_guild_id is async
        async def _mock_get_by_guild_id(db, gid):
            return cfg

        repo.get_by_guild_id = _mock_get_by_guild_id
        result = await repo.get_config_summary(mock_db, 12345)

        assert result["shop_announcements_role_id"] == 99999

    @pytest.mark.asyncio
    async def test_get_config_summary_no_role_returns_none(self):
        """get_config_summary returns None for shop_announcements_role_id when not set."""
        from persist.repositories.config_repository import ConfigRepository

        repo = ConfigRepository()
        mock_db = AsyncMock()

        cfg = _make_mock_config(guild_id=12345, shop_announcements_role_id=None)

        async def _mock_get_by_guild_id(db, gid):
            return cfg

        repo.get_by_guild_id = _mock_get_by_guild_id
        result = await repo.get_config_summary(mock_db, 12345)

        assert result["shop_announcements_role_id"] is None


class TestConfigRepositoryResetToDefaults:
    """ConfigRepository.reset_to_defaults must preserve shop_announcements_role_id."""

    def test_reset_preserves_shop_announcements_role_id(self):
        """reset_to_defaults source must list shop_announcements_role_id in _PRESERVED_FIELDS."""
        import os

        repo_path = os.path.join(_SRC_DIR, "persist", "repositories", "config_repository.py")
        with open(repo_path) as f:
            source = f.read()
        assert "shop_announcements_role_id" in source, (
            "shop_announcements_role_id must be in _PRESERVED_FIELDS in reset_to_defaults"
        )


# ---------------------------------------------------------------------------
# Admin Router Tests
# ---------------------------------------------------------------------------


class TestAdminRouterInitializeGuildWithShopAnnouncements:
    """Admin router initialize_guild must pass shop_announcements_role_id."""

    @pytest.fixture
    def mock_config_service(self):
        service = AsyncMock()
        service.create_or_update_config = AsyncMock()
        return service

    @pytest.fixture
    def mock_shop_service(self):
        service = AsyncMock()
        service.refresh_shop = AsyncMock(return_value={"refreshed": True})
        return service

    @pytest.fixture
    def test_app(self, mock_config_service, mock_shop_service):
        app = FastAPI()
        from api.routers.admin import (
            get_config_service,
            get_shop_service,
        )
        from api.routers.admin import router as admin_router

        app.include_router(admin_router, prefix="/api/v1")
        app.dependency_overrides[get_config_service] = lambda: mock_config_service
        app.dependency_overrides[get_shop_service] = lambda: mock_shop_service
        yield app
        app.dependency_overrides.clear()

    @pytest.fixture
    def client(self, test_app):
        return TestClient(test_app)

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.AuditService")
    def test_initialize_guild_passes_shop_announcements_role_id(
        self, mock_audit, mock_get_db, client, mock_config_service
    ):
        """initialize_guild should pass shop_announcements_role_id to config_data."""
        # Setup DB mock
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_audit.log_action = AsyncMock()

        payload = {
            "guild_id": 67890,
            "admin_role_id": 11111,
            "starting_credits": 0,
            "shop_announcements_role_id": 55555,
        }
        response = client.post("/api/v1/admin/guilds/initialize?user_id=67890", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["shop_announcements_role_id"] == 55555

        # Verify create_or_update_config was called with shop_announcements_role_id
        call_kwargs = mock_config_service.create_or_update_config.call_args
        config_data = call_kwargs[0][1]  # second positional arg is config_data dict
        assert config_data["shop_announcements_role_id"] == 55555

    @patch("api.routers.admin.get_db_session")
    @patch("api.routers.admin.AuditService")
    def test_initialize_guild_without_shop_announcements_role_id(
        self, mock_audit, mock_get_db, client, mock_config_service
    ):
        """initialize_guild should work with shop_announcements_role_id as None."""
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_audit.log_action = AsyncMock()

        payload = {"guild_id": 67890, "admin_role_id": 11111, "starting_credits": 0}
        response = client.post("/api/v1/admin/guilds/initialize?user_id=67890", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["shop_announcements_role_id"] is None
