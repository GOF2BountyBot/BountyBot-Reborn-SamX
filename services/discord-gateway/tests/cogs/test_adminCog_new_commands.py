"""
Tests for newly added adminCog commands:
  - /admin_uninstall  (with confirmation)
  - /admin_config_shop
  - /admin_config_validate
"""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# -------------------------------------------------------------------------
# Bootstrap: mock shared.bblogger before any cog imports
# -------------------------------------------------------------------------

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")

_module_logger = None


def _make_mock_logger(*_args, **_kwargs):
    global _module_logger
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    _module_logger = logger
    return logger


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

# Ensure real discord is used
for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from tests.mocks.discord_mock_utils import DiscordMockUtils

# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------


def _evict_discord_modules():
    to_evict = [
        k
        for k in sys.modules
        if k == "discord"
        or k.startswith("discord.")
        or k in ("api", "bot", "utils")
        or k.startswith("api.")
        or k.startswith("utils.")
        or k.startswith("cogs.")
    ]
    for k in to_evict:
        sys.modules.pop(k, None)


def _create_mock_interaction(guild_id: int = 987654321):
    interaction = DiscordMockUtils.create_mock_interaction()
    interaction.guild_id = guild_id
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.guild.name = "Test Guild"
    interaction.guild.icon = None
    return interaction


def _create_mock_user(user_id: int = 111111111, name: str = "TestUser"):
    user = DiscordMockUtils.create_mock_user(user_id=user_id, username=name)
    user.display_name = name
    return user


# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------


@pytest.fixture
def mock_bot():
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    return bot


@pytest.fixture
def mock_admin_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.adminCog import AdminCog

    cog = AdminCog(mock_bot)
    return cog


# -------------------------------------------------------------------------
# /admin_uninstall tests
# -------------------------------------------------------------------------


class TestAdminUninstall:
    """Tests for the /admin_uninstall command."""

    def test_admin_uninstall_with_correct_confirmation(self, mock_admin_cog):
        """/admin_uninstall should proceed when confirm == 'CONFIRM-DELETE'."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        uninstall_resp = MagicMock()
        uninstall_resp.status_code = 200
        uninstall_resp.raise_for_status = MagicMock()
        uninstall_resp.json.return_value = {
            "guild_id": 987654321,
            "message": "Bot completely uninstalled from guild 987654321",
            "removed_counts": {"players": 5, "shops": 4, "configs": 1},
            "warning": "All data has been permanently deleted",
        }
        mock_admin_cog.http_client.delete = AsyncMock(return_value=uninstall_resp)

        asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction, "CONFIRM-DELETE"))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        mock_admin_cog.http_client.delete.assert_called_once()
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_admin_uninstall_with_wrong_confirmation(self, mock_admin_cog):
        """/admin_uninstall should show warning embed when confirmation is wrong."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction, "wrong-string"))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        # API should NOT have been called
        mock_admin_cog.http_client.delete = AsyncMock()
        mock_admin_cog.http_client.delete.assert_not_called()
        # Warning embed should have been sent
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_admin_uninstall_without_confirm_param(self, mock_admin_cog):
        """/admin_uninstall without confirm parameter should show warning embed."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction, None))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        # Warning embed — not the uninstall confirmation
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        # The embed title should mention "WARNING"
        assert "WARNING" in embed.title or "warning" in embed.title.lower()

    def test_admin_uninstall_api_error(self, mock_admin_cog):
        """/admin_uninstall should handle API errors gracefully."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        import httpx

        mock_req = MagicMock()
        http_error = httpx.HTTPStatusError("Forbidden", request=mock_req, response=MagicMock(status_code=403))
        mock_admin_cog.http_client.delete = AsyncMock(side_effect=http_error)

        asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction, "CONFIRM-DELETE"))

        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "❌" in call_args

    def test_admin_uninstall_generic_error(self, mock_admin_cog):
        """/admin_uninstall should handle unexpected errors gracefully."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.delete = AsyncMock(side_effect=Exception("Connection error"))

        asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction, "CONFIRM-DELETE"))

        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "⚠️" in call_args


# -------------------------------------------------------------------------
# /admin_config_shop tests
# -------------------------------------------------------------------------


class TestAdminConfigShop:
    """Tests for /admin_config_shop command."""

    def _make_shop_cfg_response(self):
        """Build a mock HTTP response matching the real GuildConfigResponse shape.

        shop_config mirrors what config_repository.get_config_summary() returns:
        nested item_count_ranges / quantity_ranges / tech_level_probabilities.
        """
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "guild_id": 987654321,
            "configured": True,
            "admin_role_configured": False,
            "starting_credits": 0,
            "sale_price_factor": 0.75,
            "xp_thresholds": {"Silver": 1000, "Gold": 5000, "Platinum": 15000},
            "shop_config": {
                "item_count_ranges": {
                    "ships": {"min": 2, "max": 4},
                    "weapons": {"min": 3, "max": 6},
                    "modules": {"min": 2, "max": 5},
                    "turrets": {"min": 1, "max": 3},
                },
                "quantity_ranges": {
                    "ships": {"min": 1, "max": 1},
                    "weapons": {"min": 2, "max": 4},
                    "modules": {"min": 2, "max": 4},
                    "turrets": {"min": 2, "max": 4},
                },
                "tech_level_probabilities": {"same_level": 0.7, "one_lower": 0.2, "two_lower": 0.1},
            },
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }
        return resp

    def test_admin_config_shop_sends_item_count_ranges(self, mock_admin_cog):
        """/admin_config_shop should PUT item_count_ranges to the shop endpoint.

        The payload must use the nested UpdateShopConfigRequest structure, NOT
        the flat fields (ship_count_min, etc.) that Pydantic would silently ignore.
        Both min and max must be present before a type's range is included.
        """
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.put = AsyncMock(return_value=self._make_shop_cfg_response())

        asyncio.run(
            mock_admin_cog.admin_config_shop.callback(
                mock_admin_cog,
                interaction,
                ship_count_min=2,
                ship_count_max=4,
                weapon_count_min=None,
                weapon_count_max=None,
                module_count_min=None,
                module_count_max=None,
                turret_count_min=None,
                turret_count_max=None,
                sale_factor=None,
            )
        )

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        # shop endpoint called once (no sale_factor → no second call)
        mock_admin_cog.http_client.put.assert_called_once()

        shop_call_json = mock_admin_cog.http_client.put.call_args[1]["json"]
        # Must use nested item_count_ranges, never flat keys
        assert "item_count_ranges" in shop_call_json
        assert shop_call_json["item_count_ranges"]["ships"] == {"min": 2, "max": 4}
        # Weapon was not provided, so it must be absent
        assert "weapons" not in shop_call_json["item_count_ranges"]
        # Old flat keys must NOT appear
        assert "ship_count_min" not in shop_call_json
        assert "ship_count_max" not in shop_call_json

        interaction.followup.send.assert_called_once()
        send_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in send_kwargs

    def test_admin_config_shop_sale_factor_uses_general_config_endpoint(self, mock_admin_cog):
        """When sale_factor is provided it triggers a second PUT to the general config endpoint.

        sale_price_factor is not part of UpdateShopConfigRequest so it must be
        sent separately via PUT /config/guild/{id}.
        """
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.put = AsyncMock(return_value=self._make_shop_cfg_response())

        asyncio.run(
            mock_admin_cog.admin_config_shop.callback(
                mock_admin_cog,
                interaction,
                ship_count_min=2,
                ship_count_max=4,
                weapon_count_min=None,
                weapon_count_max=None,
                module_count_min=None,
                module_count_max=None,
                turret_count_min=None,
                turret_count_max=None,
                sale_factor=0.75,
            )
        )

        # Two PUT calls: shop endpoint + general config endpoint
        assert mock_admin_cog.http_client.put.call_count == 2
        call_urls = [call[0][0] for call in mock_admin_cog.http_client.put.call_args_list]
        assert any("/shop" in url for url in call_urls), "One call must hit the /shop endpoint"
        assert any("/shop" not in url for url in call_urls), "Second call must hit general config endpoint"

        # General config call must contain sale_price_factor, not sale_factor
        general_call = next(call for call in mock_admin_cog.http_client.put.call_args_list if "/shop" not in call[0][0])
        general_json = general_call[1]["json"]
        assert "sale_price_factor" in general_json
        assert general_json["sale_price_factor"] == 0.75
        assert "sale_factor" not in general_json

    def test_admin_config_shop_omits_range_when_only_min_provided(self, mock_admin_cog):
        """A range is omitted entirely when only one bound is given.

        bot-core requires both min and max, so sending a partial dict would
        cause a 400 validation error.  The cog must skip the range silently.
        """
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.put = AsyncMock(return_value=self._make_shop_cfg_response())

        asyncio.run(
            mock_admin_cog.admin_config_shop.callback(
                mock_admin_cog,
                interaction,
                ship_count_min=2,  # only min, no max
                ship_count_max=None,
                weapon_count_min=None,
                weapon_count_max=None,
                module_count_min=None,
                module_count_max=None,
                turret_count_min=None,
                turret_count_max=None,
                sale_factor=None,
            )
        )

        shop_call_json = mock_admin_cog.http_client.put.call_args[1]["json"]
        # item_count_ranges either absent or empty — ships must not be present
        ranges = shop_call_json.get("item_count_ranges", {})
        assert "ships" not in ranges

    def test_admin_config_shop_no_params(self, mock_admin_cog):
        """/admin_config_shop with no params should still call shop API (just guild_id)."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.put = AsyncMock(return_value=self._make_shop_cfg_response())

        asyncio.run(
            mock_admin_cog.admin_config_shop.callback(
                mock_admin_cog,
                interaction,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            )
        )

        mock_admin_cog.http_client.put.assert_called_once()
        interaction.followup.send.assert_called_once()

    def test_admin_config_shop_api_error(self, mock_admin_cog):
        """/admin_config_shop should handle API errors gracefully."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        import httpx

        mock_req = MagicMock()
        http_error = httpx.HTTPStatusError("Bad Request", request=mock_req, response=MagicMock(status_code=400))
        mock_admin_cog.http_client.put = AsyncMock(side_effect=http_error)

        asyncio.run(
            mock_admin_cog.admin_config_shop.callback(
                mock_admin_cog,
                interaction,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            )
        )

        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "❌" in call_args

    def test_admin_config_shop_generic_error(self, mock_admin_cog):
        """/admin_config_shop should handle unexpected errors gracefully."""
        interaction = _create_mock_interaction()

        mock_admin_cog.http_client.put = AsyncMock(side_effect=Exception("Unexpected error"))

        asyncio.run(
            mock_admin_cog.admin_config_shop.callback(
                mock_admin_cog,
                interaction,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            )
        )

        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "⚠️" in call_args


# -------------------------------------------------------------------------
# /admin_config_validate tests
# -------------------------------------------------------------------------


class TestAdminConfigValidate:
    """Tests for /admin_config_validate command."""

    def test_admin_config_validate_displays_pass(self, mock_admin_cog):
        """/admin_config_validate should display a passing result."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        validate_resp = MagicMock()
        validate_resp.status_code = 200
        validate_resp.raise_for_status = MagicMock()
        validate_resp.json.return_value = {
            "valid": True,
            "errors": [],
            "warnings": ["Shop has minimal inventory config"],
            "guild_id": 987654321,
        }
        mock_admin_cog.http_client.get = AsyncMock(return_value=validate_resp)

        asyncio.run(mock_admin_cog.admin_config_validate.callback(mock_admin_cog, interaction))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        mock_admin_cog.http_client.get.assert_called_once()
        url = mock_admin_cog.http_client.get.call_args[0][0]
        assert "config/guild" in url
        assert "validate" in url

        interaction.followup.send.assert_called_once()
        send_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in send_kwargs
        embed = send_kwargs["embed"]
        assert "Valid" in embed.title or "valid" in embed.title.lower()

    def test_admin_config_validate_displays_failure(self, mock_admin_cog):
        """/admin_config_validate should display a failing result with errors."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        validate_resp = MagicMock()
        validate_resp.status_code = 200
        validate_resp.raise_for_status = MagicMock()
        validate_resp.json.return_value = {
            "valid": False,
            "errors": ["Admin role not configured", "Shop has no items"],
            "warnings": [],
            "guild_id": 987654321,
        }
        mock_admin_cog.http_client.get = AsyncMock(return_value=validate_resp)

        asyncio.run(mock_admin_cog.admin_config_validate.callback(mock_admin_cog, interaction))

        interaction.followup.send.assert_called_once()
        send_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in send_kwargs

    def test_admin_config_validate_api_error(self, mock_admin_cog):
        """/admin_config_validate should handle API errors gracefully."""
        interaction = _create_mock_interaction()

        import httpx

        mock_req = MagicMock()
        http_error = httpx.HTTPStatusError("Server error", request=mock_req, response=MagicMock(status_code=500))
        mock_admin_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_admin_cog.admin_config_validate.callback(mock_admin_cog, interaction))

        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "❌" in call_args

    def test_admin_config_validate_generic_error(self, mock_admin_cog):
        """/admin_config_validate should handle unexpected errors gracefully."""
        interaction = _create_mock_interaction()

        mock_admin_cog.http_client.get = AsyncMock(side_effect=Exception("Connection failed"))

        asyncio.run(mock_admin_cog.admin_config_validate.callback(mock_admin_cog, interaction))

        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "⚠️" in call_args


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
