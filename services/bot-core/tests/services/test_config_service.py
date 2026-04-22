"""
Unit tests for ConfigService.

The shared.bblogger module is mocked via sys.modules BEFORE any service
module is imported (see conftest.py at the tests/ root).
"""

import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Guard: ensure shared.bblogger and sqlalchemy_utils are mocked before
# importing service code. The models/__init__.py auto-imports discord_message
# which requires sqlalchemy_utils (not installed in the test environment).
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

if "sqlalchemy_utils" not in sys.modules:
    _mock_sqla_utils = types.ModuleType("sqlalchemy_utils")
    _mock_sqla_utils.UUIDType = MagicMock()
    sys.modules["sqlalchemy_utils"] = _mock_sqla_utils

from services.config_service import ConfigService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    guild_id: int = 999,
    starting_credits: int = 500,
    sale_price_factor: float = 0.8,
    xp_thresholds: dict | None = None,
    tech_level_probabilities: dict | None = None,
) -> MagicMock:
    config = MagicMock()
    config.guild_id = guild_id
    config.starting_credits = starting_credits
    config.sale_price_factor = sale_price_factor
    config.xp_thresholds = xp_thresholds or {"Silver": 1000, "Gold": 5000, "Platinum": 15000}
    config.tech_level_probabilities = tech_level_probabilities or {
        "same_level": 0.7,
        "one_lower": 0.2,
        "two_lower": 0.1,
    }
    config.get_count_range = MagicMock(return_value={"min": 1, "max": 3})
    return config


def _make_player(player_id: int = 1) -> MagicMock:
    p = MagicMock()
    p.id = player_id
    return p


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_config_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_by_guild_id = AsyncMock(return_value=None)
    repo.create_default_config = AsyncMock()
    repo.create_or_update = AsyncMock()
    repo.get_config_summary = AsyncMock(return_value={"guild_id": 999})
    repo.update_shop_config = AsyncMock()
    repo.reset_to_defaults = AsyncMock()
    repo.update_admin_role = AsyncMock()
    repo.update_starting_credits = AsyncMock()
    repo.update_xp_thresholds = AsyncMock()
    repo.get_all_guild_configs = AsyncMock(return_value=[])
    repo.delete_guild_config = AsyncMock(return_value=True)
    return repo


@pytest.fixture
def mock_player_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_players_by_guild = AsyncMock(return_value=[])
    repo.remove = AsyncMock()
    return repo


@pytest.fixture
def mock_shop_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.clear_all_guild_shops = AsyncMock()
    return repo


@pytest.fixture
def service(mock_config_repo, mock_player_repo, mock_shop_repo) -> ConfigService:
    svc = ConfigService()
    svc.config_repo = mock_config_repo
    svc.player_repo = mock_player_repo
    svc.shop_repo = mock_shop_repo
    return svc


# ===========================================================================
# Tests: get_guild_config
# ===========================================================================


class TestGetGuildConfig:
    """Tests for ConfigService.get_guild_config."""

    @pytest.mark.asyncio
    async def test_returns_existing_config_summary(self, service, mock_db, mock_config_repo):
        """When config exists, returns the config summary without creating a new one."""
        mock_config_repo.get_by_guild_id.return_value = _make_config()
        mock_config_repo.get_config_summary.return_value = {"guild_id": 999, "starting_credits": 500}

        result = await service.get_guild_config(mock_db, guild_id=999)

        assert result["guild_id"] == 999
        mock_config_repo.create_default_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raises_guild_not_configured_when_absent(self, service, mock_db, mock_config_repo):
        """When no config exists, GuildNotConfiguredError is raised (no auto-create)."""
        from services.config_service import GuildNotConfiguredError

        mock_config_repo.get_by_guild_id.return_value = None

        with pytest.raises(GuildNotConfiguredError) as exc_info:
            await service.get_guild_config(mock_db, guild_id=999)

        assert exc_info.value.guild_id == 999
        mock_config_repo.create_default_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_re_raises_exception(self, service, mock_db, mock_config_repo):
        """Exceptions from config_repo propagate."""
        mock_config_repo.get_by_guild_id.side_effect = RuntimeError("db error")

        with pytest.raises(RuntimeError, match="db error"):
            await service.get_guild_config(mock_db, guild_id=999)


# ===========================================================================
# Tests: create_or_update_config
# ===========================================================================


class TestCreateOrUpdateConfig:
    """Tests for ConfigService.create_or_update_config."""

    @pytest.mark.asyncio
    async def test_creates_config_with_valid_data(self, service, mock_db, mock_config_repo):
        """Config is created/updated and the summary is returned."""
        config_data = {"guild_id": 999, "starting_credits": 250}
        mock_config_repo.get_config_summary.return_value = {"guild_id": 999, "starting_credits": 250}

        result = await service.create_or_update_config(mock_db, config_data)

        mock_config_repo.create_or_update.assert_awaited_once()
        assert result["guild_id"] == 999

    @pytest.mark.asyncio
    async def test_raises_when_guild_id_missing(self, service, mock_db):
        """ValueError raised when guild_id is absent from data."""
        with pytest.raises(ValueError, match="guild_id is required"):
            await service.create_or_update_config(mock_db, {"starting_credits": 100})

    @pytest.mark.asyncio
    async def test_raises_for_negative_starting_credits(self, service, mock_db):
        """ValueError raised when starting_credits is negative."""
        config_data = {"guild_id": 999, "starting_credits": -100}

        with pytest.raises(ValueError, match="Starting credits cannot be negative"):
            await service.create_or_update_config(mock_db, config_data)

    @pytest.mark.asyncio
    async def test_raises_for_invalid_sale_price_factor(self, service, mock_db):
        """ValueError raised when sale_price_factor is out of range."""
        config_data = {"guild_id": 999, "sale_price_factor": 1.5}

        with pytest.raises(ValueError, match="Sale price factor"):
            await service.create_or_update_config(mock_db, config_data)

    @pytest.mark.asyncio
    async def test_raises_for_zero_sale_price_factor(self, service, mock_db):
        """ValueError raised when sale_price_factor is 0."""
        config_data = {"guild_id": 999, "sale_price_factor": 0}

        with pytest.raises(ValueError, match="Sale price factor"):
            await service.create_or_update_config(mock_db, config_data)

    @pytest.mark.asyncio
    async def test_raises_for_invalid_xp_thresholds_order(self, service, mock_db):
        """ValueError raised when XP thresholds are not in ascending order."""
        config_data = {
            "guild_id": 999,
            "xp_thresholds": {"Silver": 5000, "Gold": 1000, "Platinum": 15000},
        }

        with pytest.raises(ValueError, match="ascending order"):
            await service.create_or_update_config(mock_db, config_data)

    @pytest.mark.asyncio
    async def test_raises_for_missing_xp_tier(self, service, mock_db):
        """ValueError raised when a required XP tier is missing."""
        config_data = {
            "guild_id": 999,
            "xp_thresholds": {"Silver": 1000, "Gold": 5000},
        }

        with pytest.raises(ValueError, match="Invalid or missing threshold for Platinum"):
            await service.create_or_update_config(mock_db, config_data)


# ===========================================================================
# Tests: update_shop_config
# ===========================================================================


class TestUpdateShopConfig:
    """Tests for ConfigService.update_shop_config."""

    @pytest.mark.asyncio
    async def test_updates_shop_config_successfully(self, service, mock_db, mock_config_repo):
        """Valid shop config is saved and summary returned."""
        updates = {
            "guild_id": 999,
            "tech_level_probabilities": {"same_level": 0.7, "one_lower": 0.2, "two_lower": 0.1},
        }
        mock_config_repo.get_config_summary.return_value = {"guild_id": 999}

        result = await service.update_shop_config(mock_db, updates)

        mock_config_repo.update_shop_config.assert_awaited_once()
        assert result["guild_id"] == 999

    @pytest.mark.asyncio
    async def test_raises_when_guild_id_missing(self, service, mock_db):
        """ValueError raised when guild_id is absent."""
        with pytest.raises(ValueError, match="guild_id is required"):
            await service.update_shop_config(mock_db, {"tech_level_probabilities": {}})

    @pytest.mark.asyncio
    async def test_raises_for_invalid_probabilities(self, service, mock_db):
        """ValueError raised when probabilities do not sum to 1.0."""
        updates = {
            "guild_id": 999,
            "tech_level_probabilities": {"same_level": 0.5, "one_lower": 0.5, "two_lower": 0.5},
        }

        with pytest.raises(ValueError, match=r"sum to 1\.0"):
            await service.update_shop_config(mock_db, updates)

    @pytest.mark.asyncio
    async def test_raises_for_invalid_range_format(self, service, mock_db):
        """ValueError raised when a range field has wrong format."""
        # wrong type - should be dict, not str
        updates = {"guild_id": 999, "ship_count_range": "1-3"}

        with pytest.raises(ValueError, match="Invalid range format"):
            await service.update_shop_config(mock_db, updates)

    @pytest.mark.asyncio
    async def test_raises_when_min_greater_than_max(self, service, mock_db):
        """ValueError raised when range min > max."""
        updates = {"guild_id": 999, "weapon_count_range": {"min": 5, "max": 2}}

        with pytest.raises(ValueError, match="Min cannot be greater than max"):
            await service.update_shop_config(mock_db, updates)

    @pytest.mark.asyncio
    async def test_raises_when_min_less_than_one(self, service, mock_db):
        """ValueError raised when range min < 1."""
        updates = {"guild_id": 999, "module_count_range": {"min": 0, "max": 3}}

        with pytest.raises(ValueError, match="Min value must be >= 1"):
            await service.update_shop_config(mock_db, updates)

    @pytest.mark.asyncio
    async def test_item_count_ranges_unpacked_to_flat_fields(self, service, mock_db, mock_config_repo):
        """item_count_ranges nested dict is unpacked to flat ORM field names.

        The UpdateShopConfigRequest schema exposes item_count_ranges; the
        repository's update_shop_config expects ship_count_range, etc.
        The service must translate between the two.
        """
        mock_config_repo.get_config_summary.return_value = {"guild_id": 999}

        updates = {
            "guild_id": 999,
            "item_count_ranges": {
                "ships": {"min": 2, "max": 4},
                "weapons": {"min": 3, "max": 6},
            },
        }
        await service.update_shop_config(mock_db, updates)

        # Repo must be called with the unpacked flat field names
        call_args = mock_config_repo.update_shop_config.call_args[0][1]
        assert "ship_count_range" in call_args
        assert call_args["ship_count_range"] == {"min": 2, "max": 4}
        assert "weapon_count_range" in call_args
        assert call_args["weapon_count_range"] == {"min": 3, "max": 6}
        # item_count_ranges itself must have been consumed, not passed to the repo
        assert "item_count_ranges" not in call_args

    @pytest.mark.asyncio
    async def test_quantity_ranges_unpacked_to_flat_fields(self, service, mock_db, mock_config_repo):
        """quantity_ranges nested dict is unpacked to flat ORM field names."""
        mock_config_repo.get_config_summary.return_value = {"guild_id": 999}

        updates = {
            "guild_id": 999,
            "quantity_ranges": {
                "ships": {"min": 1, "max": 1},
                "modules": {"min": 2, "max": 4},
            },
        }
        await service.update_shop_config(mock_db, updates)

        call_args = mock_config_repo.update_shop_config.call_args[0][1]
        assert "ship_quantity_range" in call_args
        assert call_args["ship_quantity_range"] == {"min": 1, "max": 1}
        assert "module_quantity_range" in call_args
        assert call_args["module_quantity_range"] == {"min": 2, "max": 4}
        assert "quantity_ranges" not in call_args

    @pytest.mark.asyncio
    async def test_item_count_ranges_validates_nested_ranges(self, service, mock_db):
        """Validation still applies after unpacking item_count_ranges."""
        updates = {
            "guild_id": 999,
            "item_count_ranges": {
                "ships": {"min": 5, "max": 2},  # min > max
            },
        }
        with pytest.raises(ValueError, match="Min cannot be greater than max"):
            await service.update_shop_config(mock_db, updates)


# ===========================================================================
# Tests: reset_to_defaults
# ===========================================================================


class TestResetToDefaults:
    """Tests for ConfigService.reset_to_defaults."""

    @pytest.mark.asyncio
    async def test_resets_and_returns_summary(self, service, mock_db, mock_config_repo):
        """reset_to_defaults is called and the config summary is returned."""
        mock_config_repo.get_config_summary.return_value = {"guild_id": 999}

        result = await service.reset_to_defaults(mock_db, guild_id=999)

        mock_config_repo.reset_to_defaults.assert_awaited_once_with(mock_db, 999)
        assert result["guild_id"] == 999

    @pytest.mark.asyncio
    async def test_re_raises_exception(self, service, mock_db, mock_config_repo):
        """Exceptions from config_repo propagate."""
        mock_config_repo.reset_to_defaults.side_effect = RuntimeError("db gone")

        with pytest.raises(RuntimeError, match="db gone"):
            await service.reset_to_defaults(mock_db, guild_id=999)


# ===========================================================================
# Tests: update_admin_role
# ===========================================================================


class TestUpdateAdminRole:
    """Tests for ConfigService.update_admin_role."""

    @pytest.mark.asyncio
    async def test_updates_role_successfully(self, service, mock_db, mock_config_repo):
        """update_admin_role is called and summary returned."""
        mock_config_repo.get_config_summary.return_value = {"guild_id": 999}

        result = await service.update_admin_role(mock_db, guild_id=999, role_id=12345)

        mock_config_repo.update_admin_role.assert_awaited_once_with(mock_db, 999, 12345)
        assert result["guild_id"] == 999

    @pytest.mark.asyncio
    async def test_raises_for_zero_role_id(self, service, mock_db):
        """ValueError raised when role_id is 0."""
        with pytest.raises(ValueError, match="Invalid role ID"):
            await service.update_admin_role(mock_db, guild_id=999, role_id=0)

    @pytest.mark.asyncio
    async def test_raises_for_negative_role_id(self, service, mock_db):
        """ValueError raised when role_id is negative."""
        with pytest.raises(ValueError, match="Invalid role ID"):
            await service.update_admin_role(mock_db, guild_id=999, role_id=-1)


# ===========================================================================
# Tests: update_starting_credits
# ===========================================================================


class TestUpdateStartingCredits:
    """Tests for ConfigService.update_starting_credits."""

    @pytest.mark.asyncio
    async def test_updates_credits_successfully(self, service, mock_db, mock_config_repo):
        """update_starting_credits is called and summary returned."""
        mock_config_repo.get_config_summary.return_value = {"guild_id": 999}

        result = await service.update_starting_credits(mock_db, guild_id=999, new_credits=300)

        mock_config_repo.update_starting_credits.assert_awaited_once_with(mock_db, 999, 300)
        assert result["guild_id"] == 999

    @pytest.mark.asyncio
    async def test_zero_credits_allowed(self, service, mock_db, mock_config_repo):
        """Zero starting credits is valid."""
        mock_config_repo.get_config_summary.return_value = {"guild_id": 999}

        await service.update_starting_credits(mock_db, guild_id=999, new_credits=0)

        mock_config_repo.update_starting_credits.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_for_negative_credits(self, service, mock_db):
        """ValueError raised for negative starting credits."""
        with pytest.raises(ValueError, match="cannot be negative"):
            await service.update_starting_credits(mock_db, guild_id=999, new_credits=-100)


# ===========================================================================
# Tests: update_xp_thresholds
# ===========================================================================


class TestUpdateXpThresholds:
    """Tests for ConfigService.update_xp_thresholds."""

    @pytest.mark.asyncio
    async def test_updates_thresholds_successfully(self, service, mock_db, mock_config_repo):
        """Valid thresholds are saved and summary returned."""
        thresholds = {"Silver": 1000, "Gold": 5000, "Platinum": 15000}
        mock_config_repo.get_config_summary.return_value = {"guild_id": 999}

        result = await service.update_xp_thresholds(mock_db, guild_id=999, thresholds=thresholds)

        mock_config_repo.update_xp_thresholds.assert_awaited_once_with(mock_db, 999, thresholds)
        assert result["guild_id"] == 999

    @pytest.mark.asyncio
    async def test_raises_for_missing_tier(self, service, mock_db):
        """ValueError raised when a required tier is absent."""
        thresholds = {"Silver": 1000, "Gold": 5000}  # Missing Platinum

        with pytest.raises(ValueError, match="Missing threshold for Platinum"):
            await service.update_xp_thresholds(mock_db, guild_id=999, thresholds=thresholds)

    @pytest.mark.asyncio
    async def test_raises_for_non_positive_threshold(self, service, mock_db):
        """ValueError raised when a threshold value is <= 0."""
        thresholds = {"Silver": 0, "Gold": 5000, "Platinum": 15000}

        with pytest.raises(ValueError, match="must be positive"):
            await service.update_xp_thresholds(mock_db, guild_id=999, thresholds=thresholds)

    @pytest.mark.asyncio
    async def test_raises_for_non_ascending_order(self, service, mock_db):
        """ValueError raised when thresholds are not strictly ascending."""
        thresholds = {"Silver": 5000, "Gold": 1000, "Platinum": 15000}

        with pytest.raises(ValueError, match="ascending order"):
            await service.update_xp_thresholds(mock_db, guild_id=999, thresholds=thresholds)


# ===========================================================================
# Tests: clear_guild_players
# ===========================================================================


class TestClearGuildPlayers:
    """Tests for ConfigService.clear_guild_players."""

    @pytest.mark.asyncio
    async def test_removes_all_guild_players(self, service, mock_db, mock_player_repo):
        """Each player is removed; counts are tracked correctly."""
        players = [_make_player(i) for i in range(3)]
        mock_player_repo.get_players_by_guild.return_value = players

        result = await service.clear_guild_players(mock_db, guild_id=999)

        assert result["players"] == 3
        assert mock_player_repo.remove.await_count == 3

    @pytest.mark.asyncio
    async def test_returns_zero_counts_when_no_players(self, service, mock_db, mock_player_repo):
        """Returns 0 counts when the guild has no players."""
        mock_player_repo.get_players_by_guild.return_value = []

        result = await service.clear_guild_players(mock_db, guild_id=999)

        assert result["players"] == 0

    @pytest.mark.asyncio
    async def test_re_raises_repo_exception(self, service, mock_db, mock_player_repo):
        """Exceptions from player_repo propagate."""
        mock_player_repo.get_players_by_guild.side_effect = RuntimeError("gone")

        with pytest.raises(RuntimeError, match="gone"):
            await service.clear_guild_players(mock_db, guild_id=999)


# ===========================================================================
# Tests: uninstall_guild
# ===========================================================================


class TestUninstallGuild:
    """Tests for ConfigService.uninstall_guild."""

    @pytest.mark.asyncio
    async def test_uninstalls_guild_completely(
        self, service, mock_db, mock_player_repo, mock_config_repo, mock_shop_repo
    ):
        """Players, shops and config are all cleared."""
        players = [_make_player(i) for i in range(2)]
        mock_player_repo.get_players_by_guild.return_value = players
        mock_config_repo.delete_guild_config.return_value = True

        result = await service.uninstall_guild(mock_db, guild_id=999)

        assert result["players"] == 2
        assert result["config"] == 1
        mock_shop_repo.clear_all_guild_shops.assert_awaited_once_with(mock_db, 999)
        mock_config_repo.delete_guild_config.assert_awaited_once_with(mock_db, 999)

    @pytest.mark.asyncio
    async def test_config_count_zero_when_config_not_deleted(
        self, service, mock_db, mock_player_repo, mock_config_repo, mock_shop_repo
    ):
        """config count is 0 when delete_guild_config returns False."""
        mock_player_repo.get_players_by_guild.return_value = []
        mock_config_repo.delete_guild_config.return_value = False

        result = await service.uninstall_guild(mock_db, guild_id=999)

        assert result["config"] == 0

    @pytest.mark.asyncio
    async def test_reraises_exception(self, service, mock_db, mock_player_repo, mock_config_repo):
        """Exceptions from clear_guild_players propagate out of uninstall_guild."""
        mock_player_repo.get_players_by_guild.side_effect = RuntimeError("cascade fail")

        with pytest.raises(RuntimeError, match="cascade fail"):
            await service.uninstall_guild(mock_db, guild_id=999)


# ===========================================================================
# Tests: get_all_guild_configs
# ===========================================================================


class TestGetAllGuildConfigs:
    """Tests for ConfigService.get_all_guild_configs."""

    @pytest.mark.asyncio
    async def test_returns_all_configs(self, service, mock_db, mock_config_repo):
        """Returns all guild configs from the repository."""
        mock_config_repo.get_all_guild_configs.return_value = [{"guild_id": 1}, {"guild_id": 2}]

        result = await service.get_all_guild_configs(mock_db)

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_none(self, service, mock_db, mock_config_repo):
        """Returns empty list when no configs exist."""
        mock_config_repo.get_all_guild_configs.return_value = []

        result = await service.get_all_guild_configs(mock_db)

        assert result == []

    @pytest.mark.asyncio
    async def test_reraises_exception(self, service, mock_db, mock_config_repo):
        """Exceptions from config_repo.get_all_guild_configs propagate."""
        mock_config_repo.get_all_guild_configs.side_effect = RuntimeError("query failed")

        with pytest.raises(RuntimeError, match="query failed"):
            await service.get_all_guild_configs(mock_db)


# ===========================================================================
# Tests: validate_config_compatibility
# ===========================================================================


class TestValidateConfigCompatibility:
    """Tests for ConfigService.validate_config_compatibility."""

    @pytest.mark.asyncio
    async def test_returns_invalid_when_no_config(self, service, mock_db, mock_config_repo):
        """Returns valid=False when guild has no configuration."""
        mock_config_repo.get_by_guild_id.return_value = None

        result = await service.validate_config_compatibility(mock_db, guild_id=999)

        assert result["valid"] is False
        assert "No configuration found" in result["errors"]

    @pytest.mark.asyncio
    async def test_returns_valid_for_correct_config(self, service, mock_db, mock_config_repo):
        """Returns valid=True when configuration passes all checks."""
        config = _make_config(
            sale_price_factor=0.8,
            xp_thresholds={"Silver": 1000, "Gold": 5000, "Platinum": 15000},
            tech_level_probabilities={"same_level": 0.7, "one_lower": 0.2, "two_lower": 0.1},
        )
        mock_config_repo.get_by_guild_id.return_value = config

        result = await service.validate_config_compatibility(mock_db, guild_id=999)

        assert result["valid"] is True
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_reports_error_for_invalid_xp_order(self, service, mock_db, mock_config_repo):
        """Validation errors include XP threshold ordering issues."""
        config = _make_config(
            xp_thresholds={"Silver": 6000, "Gold": 5000, "Platinum": 15000},  # Silver > Gold
            tech_level_probabilities={"same_level": 0.7, "one_lower": 0.2, "two_lower": 0.1},
        )
        mock_config_repo.get_by_guild_id.return_value = config

        result = await service.validate_config_compatibility(mock_db, guild_id=999)

        assert result["valid"] is False
        assert any("Silver" in e for e in result["errors"])

    @pytest.mark.asyncio
    async def test_reports_warning_for_zero_starting_credits(self, service, mock_db, mock_config_repo):
        """A warning is added when starting_credits is 0."""
        config = _make_config(
            starting_credits=0,
            tech_level_probabilities={"same_level": 0.7, "one_lower": 0.2, "two_lower": 0.1},
        )
        mock_config_repo.get_by_guild_id.return_value = config

        result = await service.validate_config_compatibility(mock_db, guild_id=999)

        assert any("Starting credits" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_error_gold_gte_platinum(self, service, mock_db, mock_config_repo):
        """Validation error when Gold XP threshold >= Platinum threshold."""
        config = _make_config(
            xp_thresholds={"Silver": 1000, "Gold": 15000, "Platinum": 15000},
            tech_level_probabilities={"same_level": 0.7, "one_lower": 0.2, "two_lower": 0.1},
        )
        mock_config_repo.get_by_guild_id.return_value = config

        result = await service.validate_config_compatibility(mock_db, guild_id=999)

        assert result["valid"] is False
        assert any("Gold" in e for e in result["errors"])

    @pytest.mark.asyncio
    async def test_error_probability_not_sum_to_one(self, service, mock_db, mock_config_repo):
        """Validation error when tech level probabilities do not sum to 1.0."""
        config = _make_config(
            tech_level_probabilities={"same_level": 0.5, "one_lower": 0.5, "two_lower": 0.5},
        )
        mock_config_repo.get_by_guild_id.return_value = config

        result = await service.validate_config_compatibility(mock_db, guild_id=999)

        assert result["valid"] is False
        assert any("probabilities" in e.lower() for e in result["errors"])

    @pytest.mark.asyncio
    async def test_error_invalid_sale_price_factor(self, service, mock_db, mock_config_repo):
        """Validation error when sale_price_factor is out of valid range."""
        config = _make_config(
            sale_price_factor=0.0,  # Must be > 0
            tech_level_probabilities={"same_level": 0.7, "one_lower": 0.2, "two_lower": 0.1},
        )
        mock_config_repo.get_by_guild_id.return_value = config

        result = await service.validate_config_compatibility(mock_db, guild_id=999)

        assert result["valid"] is False
        assert any("sale price" in e.lower() for e in result["errors"])

    @pytest.mark.asyncio
    async def test_error_count_range_min_gt_max(self, service, mock_db, mock_config_repo):
        """Validation error when item count range has min > max."""
        config = _make_config(
            tech_level_probabilities={"same_level": 0.7, "one_lower": 0.2, "two_lower": 0.1},
        )

        # Override get_count_range to return bad range for 'ship' type
        def _bad_count_range(item_type: str) -> dict:
            if item_type == "ship":
                return {"min": 5, "max": 2}
            return {"min": 1, "max": 3}

        config.get_count_range = MagicMock(side_effect=_bad_count_range)
        mock_config_repo.get_by_guild_id.return_value = config

        result = await service.validate_config_compatibility(mock_db, guild_id=999)

        assert result["valid"] is False
        assert any("min > max" in e for e in result["errors"])

    @pytest.mark.asyncio
    async def test_error_count_range_min_lt_one(self, service, mock_db, mock_config_repo):
        """Validation error when item count range min is less than 1."""
        config = _make_config(
            tech_level_probabilities={"same_level": 0.7, "one_lower": 0.2, "two_lower": 0.1},
        )

        # Override get_count_range to return min=0 for 'weapon' type
        def _zero_min_range(item_type: str) -> dict:
            if item_type == "weapon":
                return {"min": 0, "max": 3}
            return {"min": 1, "max": 3}

        config.get_count_range = MagicMock(side_effect=_zero_min_range)
        mock_config_repo.get_by_guild_id.return_value = config

        result = await service.validate_config_compatibility(mock_db, guild_id=999)

        assert result["valid"] is False
        assert any("min must be >= 1" in e for e in result["errors"])

    @pytest.mark.asyncio
    async def test_reraises_exception(self, service, mock_db, mock_config_repo):
        """Exceptions from config_repo.get_by_guild_id propagate."""
        mock_config_repo.get_by_guild_id.side_effect = RuntimeError("db timeout")

        with pytest.raises(RuntimeError, match="db timeout"):
            await service.validate_config_compatibility(mock_db, guild_id=999)


# ===========================================================================
# Tests: _validate_config_data (coverage for lines 289-290)
# ===========================================================================


class TestValidateConfigData:
    """Tests for ConfigService._validate_config_data."""

    @pytest.mark.asyncio
    async def test_xp_thresholds_not_dict_raises(self, service):
        """ValueError raised when xp_thresholds is not a dict."""
        config_data = {
            "guild_id": 999,
            "xp_thresholds": "not-a-dict",
        }

        with pytest.raises(ValueError, match="must be a dictionary"):
            await service._validate_config_data(config_data)


# ===========================================================================
# Tests: _validate_shop_config (coverage for lines 309-315)
# ===========================================================================


class TestValidateShopConfig:
    """Tests for ConfigService._validate_shop_config."""

    @pytest.mark.asyncio
    async def test_probs_not_dict_raises(self, service):
        """ValueError raised when tech_level_probabilities is not a dict."""
        config_updates = {
            "guild_id": 999,
            "tech_level_probabilities": "not-a-dict",
        }

        with pytest.raises(ValueError, match="must be a dictionary"):
            await service._validate_shop_config(config_updates)

    @pytest.mark.asyncio
    async def test_individual_prob_out_of_range(self, service):
        """ValueError raised when an individual probability is > 1."""
        config_updates = {
            "guild_id": 999,
            "tech_level_probabilities": {
                "same_level": 1.5,  # > 1: invalid
                "one_lower": 0.2,
                "two_lower": 0.1,
            },
        }

        with pytest.raises(ValueError, match="Invalid probability"):
            await service._validate_shop_config(config_updates)

    @pytest.mark.asyncio
    async def test_individual_prob_negative(self, service):
        """ValueError raised when an individual probability is < 0."""
        config_updates = {
            "guild_id": 999,
            "tech_level_probabilities": {
                "same_level": -0.1,  # < 0: invalid
                "one_lower": 0.8,
                "two_lower": 0.3,
            },
        }

        with pytest.raises(ValueError, match="Invalid probability"):
            await service._validate_shop_config(config_updates)


# ===========================================================================
# Tests: get_bounty_config
# ===========================================================================


class TestGetBountyConfig:
    """Tests for ConfigService.get_bounty_config."""

    @pytest.mark.asyncio
    async def test_returns_config_with_defaults(self, service, mock_db, mock_config_repo):
        """Returns bounty config with default values when config exists but fields are None."""
        from unittest.mock import MagicMock

        cfg = MagicMock()
        cfg.bounty_max_per_tier = None
        cfg.bounty_expiry_minutes = None
        cfg.bounty_spawn_interval_minutes = None
        cfg.next_spawn_check_at = None
        mock_config_repo.get_by_guild_id.return_value = cfg

        result = await service.get_bounty_config(mock_db, guild_id=1000)

        assert result["guild_id"] == 1000
        assert result["max_bounties_per_tier"] == {"bronze": 3, "silver": 3, "gold": 3}
        assert result["bounty_expiry_minutes"] == 480
        assert result["bounty_spawn_interval_minutes"] == 60
        assert result["next_spawn_check_at"] is None

    @pytest.mark.asyncio
    async def test_returns_custom_values_when_set(self, service, mock_db, mock_config_repo):
        """Returns explicitly set bounty config values."""
        from datetime import UTC, datetime
        from unittest.mock import MagicMock

        ts = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        cfg = MagicMock()
        cfg.bounty_max_per_tier = {"bronze": 5, "silver": 5, "gold": 5}
        cfg.bounty_expiry_minutes = 240
        cfg.bounty_spawn_interval_minutes = 30
        cfg.next_spawn_check_at = ts
        mock_config_repo.get_by_guild_id.return_value = cfg

        result = await service.get_bounty_config(mock_db, guild_id=2000)

        assert result["max_bounties_per_tier"] == {"bronze": 5, "silver": 5, "gold": 5}
        assert result["bounty_expiry_minutes"] == 240
        assert result["bounty_spawn_interval_minutes"] == 30
        assert result["next_spawn_check_at"] == ts.isoformat()

    @pytest.mark.asyncio
    async def test_raises_guild_not_configured_when_not_found(self, service, mock_db, mock_config_repo):
        """Raises GuildNotConfiguredError when guild has no config (no auto-create)."""
        from services.config_service import GuildNotConfiguredError

        mock_config_repo.get_by_guild_id.return_value = None

        with pytest.raises(GuildNotConfiguredError) as exc_info:
            await service.get_bounty_config(mock_db, guild_id=3000)

        assert exc_info.value.guild_id == 3000
        mock_config_repo.create_default_config.assert_not_awaited()


# ===========================================================================
# Tests: update_bounty_config
# ===========================================================================


class TestUpdateBountyConfig:
    """Tests for ConfigService.update_bounty_config."""

    @pytest.mark.asyncio
    async def test_update_expiry_minutes(self, service, mock_db, mock_config_repo):
        """Updates bounty_expiry_minutes on the config."""
        from unittest.mock import AsyncMock, MagicMock

        cfg = MagicMock()
        cfg.bounty_max_per_tier = {"bronze": 3, "silver": 3, "gold": 3}
        cfg.bounty_expiry_minutes = 480
        cfg.bounty_spawn_interval_minutes = 60
        cfg.next_spawn_check_at = None
        mock_config_repo.get_by_guild_id.return_value = cfg
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        await service.update_bounty_config(mock_db, guild_id=1000, updates={"bounty_expiry_minutes": 120})

        assert cfg.bounty_expiry_minutes == 120
        mock_db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_update_max_per_tier_valid(self, service, mock_db, mock_config_repo):
        """Updates bounty_max_per_tier with valid values."""
        from unittest.mock import AsyncMock, MagicMock

        cfg = MagicMock()
        cfg.bounty_max_per_tier = {"bronze": 3, "silver": 3, "gold": 3}
        cfg.bounty_expiry_minutes = 480
        cfg.bounty_spawn_interval_minutes = 60
        cfg.next_spawn_check_at = None
        mock_config_repo.get_by_guild_id.return_value = cfg
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        new_tiers = {"bronze": 5, "silver": 4, "gold": 3}
        await service.update_bounty_config(mock_db, guild_id=1000, updates={"max_bounties_per_tier": new_tiers})

        assert cfg.bounty_max_per_tier == new_tiers

    @pytest.mark.asyncio
    async def test_invalid_tier_key_raises(self, service, mock_db, mock_config_repo):
        """Raises ValueError for unknown tier keys."""
        from unittest.mock import MagicMock

        cfg = MagicMock()
        mock_config_repo.get_by_guild_id.return_value = cfg

        with pytest.raises(ValueError, match="Invalid tier keys"):
            await service.update_bounty_config(
                mock_db, guild_id=1000, updates={"max_bounties_per_tier": {"diamond": 3}}
            )


class TestUpdateBountyConfigPlatinumTier:
    """Regression tests for A.9 — platinum tier acceptance in max_bounties_per_tier."""

    @pytest.mark.asyncio
    async def test_update_bounty_config_accepts_platinum(self, service, mock_db, mock_config_repo):
        """A.9: `platinum` is accepted as a valid tier key."""
        from unittest.mock import AsyncMock, MagicMock

        cfg = MagicMock()
        cfg.bounty_max_per_tier = {"bronze": 3, "silver": 3, "gold": 3, "platinum": 3}
        cfg.bounty_expiry_minutes = 480
        cfg.bounty_spawn_interval_minutes = 60
        cfg.next_spawn_check_at = None
        mock_config_repo.get_by_guild_id.return_value = cfg
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        await service.update_bounty_config(mock_db, guild_id=1000, updates={"max_bounties_per_tier": {"platinum": 10}})

        assert cfg.bounty_max_per_tier == {"platinum": 10}

    @pytest.mark.asyncio
    async def test_update_bounty_config_accepts_mixed_with_platinum(self, service, mock_db, mock_config_repo):
        """A.9: mixed-tier payload including platinum persists correctly."""
        from unittest.mock import AsyncMock, MagicMock

        cfg = MagicMock()
        cfg.bounty_max_per_tier = {"bronze": 3, "silver": 3, "gold": 3, "platinum": 3}
        cfg.bounty_expiry_minutes = 480
        cfg.bounty_spawn_interval_minutes = 60
        cfg.next_spawn_check_at = None
        mock_config_repo.get_by_guild_id.return_value = cfg
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        new_tiers = {"bronze": 5, "silver": 10, "gold": 15, "platinum": 20}
        await service.update_bounty_config(mock_db, guild_id=1000, updates={"max_bounties_per_tier": new_tiers})

        assert cfg.bounty_max_per_tier == new_tiers

    @pytest.mark.asyncio
    async def test_update_bounty_config_rejects_unknown_tier_mentions_platinum_in_error(
        self, service, mock_db, mock_config_repo
    ):
        """A.9 regression: error message must list `platinum` (guards against 3-tier wording)."""
        from unittest.mock import MagicMock

        cfg = MagicMock()
        mock_config_repo.get_by_guild_id.return_value = cfg

        with pytest.raises(ValueError, match="platinum"):
            await service.update_bounty_config(
                mock_db, guild_id=1000, updates={"max_bounties_per_tier": {"diamond": 5}}
            )

    @pytest.mark.asyncio
    async def test_update_bounty_config_platinum_out_of_range(self, service, mock_db, mock_config_repo):
        """A.9: range bounds still apply to platinum — 21 is rejected."""
        from unittest.mock import MagicMock

        cfg = MagicMock()
        mock_config_repo.get_by_guild_id.return_value = cfg

        with pytest.raises(ValueError, match="between 0 and 20"):
            await service.update_bounty_config(
                mock_db, guild_id=1000, updates={"max_bounties_per_tier": {"platinum": 21}}
            )

    @pytest.mark.asyncio
    async def test_tier_value_out_of_range_raises(self, service, mock_db, mock_config_repo):
        """Raises ValueError when tier value > 20."""
        from unittest.mock import MagicMock

        cfg = MagicMock()
        mock_config_repo.get_by_guild_id.return_value = cfg

        with pytest.raises(ValueError, match="between 0 and 20"):
            await service.update_bounty_config(
                mock_db, guild_id=1000, updates={"max_bounties_per_tier": {"bronze": 25}}
            )

    @pytest.mark.asyncio
    async def test_expiry_out_of_range_raises(self, service, mock_db, mock_config_repo):
        """Raises ValueError when expiry_minutes out of allowed range."""
        from unittest.mock import MagicMock

        cfg = MagicMock()
        mock_config_repo.get_by_guild_id.return_value = cfg

        with pytest.raises(ValueError, match="bounty_expiry_minutes must be between"):
            await service.update_bounty_config(mock_db, guild_id=1000, updates={"bounty_expiry_minutes": 5})

    @pytest.mark.asyncio
    async def test_spawn_interval_out_of_range_raises(self, service, mock_db, mock_config_repo):
        """Raises ValueError when spawn_interval_minutes out of range."""
        from unittest.mock import MagicMock

        cfg = MagicMock()
        mock_config_repo.get_by_guild_id.return_value = cfg

        with pytest.raises(ValueError, match="bounty_spawn_interval_minutes must be between"):
            await service.update_bounty_config(mock_db, guild_id=1000, updates={"bounty_spawn_interval_minutes": 2000})
