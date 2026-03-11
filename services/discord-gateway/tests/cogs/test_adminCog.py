import pytest
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock
import sys
import os
import types
import asyncio
from datetime import datetime

# Import discord_mock_utils for consistent mock patterns
from tests.mocks.discord_mock_utils import DiscordMockUtils


# Create module-level mock utilities
_mock_utils = DiscordMockUtils()

# Setup mock shared.bblogger module
_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")

# Track the module-level logger
_module_logger = None


def _make_mock_logger(*_args, **_kwargs):
    """Return a MagicMock that already has common log-level methods."""
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

# Ensure real discord is used (not a hand-rolled fake from another test module)
for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import discord
from discord.ext import commands


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot for adminCog testing."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    bot.get_member = MagicMock()
    bot.flogger = MagicMock()
    return bot


def _evict_discord_modules():
    """Remove cached discord/source modules so they re-import with real discord."""
    to_evict = [k for k in sys.modules if k == "discord" or k.startswith("discord.")
                or k in ("api", "bot", "utils") or k.startswith("api.") or k.startswith("utils.")
                or k.startswith("cogs.")]
    for k in to_evict:
        sys.modules.pop(k, None)


@pytest.fixture
def mock_admin_cog(mock_bot):
    """Create a mock adminCog instance."""
    # Re-assert this file's own mock so that when adminCog is re-imported below
    # it calls *our* _make_mock_logger (which populates _module_logger).
    # Without this, whichever test file was imported last "owns" the shared
    # sys.modules["shared.bblogger"] entry and the other file's _module_logger
    # stays None.
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.adminCog import AdminCog
    cog = AdminCog(mock_bot)
    return cog


def _create_mock_interaction():
    """Create a properly mocked interaction with all necessary attributes."""
    return DiscordMockUtils.create_mock_interaction()


def _create_mock_user(user_id=111111111, name="TestUser", is_admin=False):
    """Create a properly mocked user with string properties."""
    user = DiscordMockUtils.create_mock_user(user_id=user_id, username=name)
    user.display_avatar = MagicMock()
    user.display_avatar.url = "https://example.com/avatar.jpg"
    user.guild_permissions = MagicMock()
    user.guild_permissions.administrator = is_admin
    return user


class TestAdminCogInitialization:
    """Tests for adminCog initialization."""

    def test_initialization(self, mock_admin_cog):
        """adminCog should initialize properly with bot reference."""
        global _module_logger
        assert mock_admin_cog.bot is not None
        # The cog uses the module-level flogger
        assert _module_logger is not None
        _module_logger.debug.assert_called_with("AdminCog initialized")
        assert mock_admin_cog._valid_tiers == ["Bronze", "Silver", "Gold", "Platinum"]


class TestAdminCheckCommand:
    """Tests for admin_check command."""

    @patch("cogs.adminCog.httpx.AsyncClient")
    def test_admin_check_developer_override(self, mock_httpx_client, mock_admin_cog):
        """admin_check should detect developer override."""
        # Mock interaction
        interaction = _create_mock_interaction()
        user = _create_mock_user()
        interaction.user = user

        # Mock developer override
        with patch.dict(os.environ, {"DEVELOPERS": "111111111"}):
            asyncio.run(mock_admin_cog.admin_check.callback(mock_admin_cog, interaction, user))

        # Verify response
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "**has** bot-admin rights" in call_args
        assert "Developer override" in call_args

    @patch("cogs.adminCog.httpx.AsyncClient")
    def test_admin_check_discord_admin(self, mock_httpx_client, mock_admin_cog):
        """admin_check should detect Discord Administrator permission."""
        # Mock interaction
        interaction = _create_mock_interaction()
        user = _create_mock_user(is_admin=True)
        interaction.user = user
        interaction.guild_id = 987654321

        # Mock guild and member
        guild = MagicMock()
        member = MagicMock()
        member.guild_permissions = MagicMock()
        member.guild_permissions.administrator = True
        # get_member is sync, fetch_member is async
        guild.get_member = MagicMock(return_value=member)
        guild.fetch_member = AsyncMock(return_value=member)
        mock_admin_cog.bot.get_guild = MagicMock(return_value=guild)

        asyncio.run(mock_admin_cog.admin_check.callback(mock_admin_cog, interaction, user))

        # Verify response
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "**has** bot-admin rights" in call_args
        assert "Discord Administrator permission" in call_args

    def test_admin_check_bot_admin_role(self, mock_admin_cog):
        """admin_check should detect Bot Admin role."""
        # Mock interaction
        interaction = _create_mock_interaction()
        user = _create_mock_user(is_admin=False)
        interaction.user = user
        interaction.guild_id = 987654321

        # Mock guild and member with role
        guild = MagicMock()
        role = MagicMock()
        role.id = 222222222
        member = MagicMock()
        member.roles = [role]
        member.guild_permissions = MagicMock()
        member.guild_permissions.administrator = False

        guild.get_member = MagicMock(return_value=member)
        guild.fetch_member = AsyncMock(return_value=member)
        mock_admin_cog.bot.get_guild = MagicMock(return_value=guild)

        # Mock API response with admin role - patch the cog's http_client
        api_response = MagicMock()
        api_response.status_code = 200
        api_response.json.return_value = {"admin_role_id": 222222222}
        mock_admin_cog.http_client.get = AsyncMock(return_value=api_response)

        asyncio.run(mock_admin_cog.admin_check.callback(mock_admin_cog, interaction, user))

        # Verify response
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "**has** bot-admin rights" in call_args
        assert "Assigned Bot Admin role" in call_args

    def test_admin_check_no_admin_rights(self, mock_admin_cog):
        """admin_check should correctly identify users without admin rights."""
        # Mock interaction
        interaction = _create_mock_interaction()
        user = _create_mock_user(is_admin=False)
        interaction.user = user
        interaction.guild_id = 987654321

        # Mock guild without admin role
        guild = MagicMock()
        member = MagicMock()
        member.roles = []
        member.guild_permissions = MagicMock()
        member.guild_permissions.administrator = False

        guild.get_member = MagicMock(return_value=member)
        guild.fetch_member = AsyncMock(return_value=member)
        mock_admin_cog.bot.get_guild = MagicMock(return_value=guild)

        # Mock API response with no admin role - patch the cog's http_client
        api_response = MagicMock()
        api_response.status_code = 200
        api_response.json.return_value = {"admin_role_id": None}
        mock_admin_cog.http_client.get = AsyncMock(return_value=api_response)

        asyncio.run(mock_admin_cog.admin_check.callback(mock_admin_cog, interaction, user))

        # Verify response
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "**does not have** bot-admin rights" in call_args


class TestAdminSetupCommand:
    """Tests for admin_setup command."""

    @patch("cogs.adminCog.httpx.AsyncClient")
    def test_admin_setup_with_role(self, mock_httpx_client, mock_admin_cog):
        """admin_setup should work with provided admin role."""
        # Mock interaction
        interaction = _create_mock_interaction()
        interaction.guild = MagicMock()
        interaction.guild.id = 987654321
        interaction.guild.name = "Test Guild"
        interaction.guild.icon = None
        user = _create_mock_user()
        interaction.user = user

        # Mock provided role
        role = MagicMock()
        role.id = 222222222
        type(role).mention = PropertyMock(return_value="<@&222222222>")

        # Mock HTTP client
        mock_client = MagicMock()
        mock_httpx_client.return_value = mock_client

        # Mock API responses
        guild_create_resp = MagicMock()
        guild_create_resp.status_code = 200
        guild_create_resp.json.return_value = {"data": {"id": 987654321}}

        init_resp = MagicMock()
        init_resp.status_code = 200
        init_resp.json.return_value = {
            "message": "Guild initialized successfully",
            "guild_id": 987654321,
            "shops_created": 4
        }

        mock_client.post.side_effect = [guild_create_resp, init_resp]
        mock_client.aclose = AsyncMock()

        asyncio.run(mock_admin_cog.admin_setup.callback(mock_admin_cog, interaction, role, 1000))

        # Verify behavior
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()

    @patch("cogs.adminCog.httpx.AsyncClient")
    def test_admin_setup_no_role(self, mock_httpx_client, mock_admin_cog):
        """admin_setup should create role if not provided."""
        # Mock interaction
        interaction = _create_mock_interaction()
        interaction.guild = MagicMock()
        interaction.guild.id = 987654321
        interaction.guild.name = "Test Guild"

        # Mock role creation
        created_role = MagicMock()
        created_role.id = 222222222
        type(created_role).mention = PropertyMock(return_value="<@&222222222>")
        interaction.guild.create_role = AsyncMock(return_value=created_role)

        user = _create_mock_user()
        interaction.user = user

        # Mock HTTP client
        mock_client = MagicMock()
        mock_httpx_client.return_value = mock_client

        # Mock API responses
        role_create_resp = MagicMock()
        role_create_resp.status_code = 200
        role_create_resp.json.return_value = {"data": {"id": 222222222}}

        init_resp = MagicMock()
        init_resp.status_code = 200
        init_resp.json.return_value = {
            "message": "Guild initialized successfully",
            "guild_id": 987654321,
            "shops_created": 4
        }

        mock_client.post.side_effect = [role_create_resp, init_resp]
        mock_client.aclose = AsyncMock()

        # Mock guild.get_role to return the created role
        mock_admin_cog.bot.get_guild = MagicMock(return_value=interaction.guild)
        interaction.guild.get_role = MagicMock(return_value=created_role)

        asyncio.run(mock_admin_cog.admin_setup.callback(mock_admin_cog, interaction, None, 1000))

        # Verify behavior
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()


class TestAdminPlayerCommand:
    """Tests for admin_player command."""

    @patch("cogs.adminCog.httpx.AsyncClient")
    def test_admin_player_view_stats(self, mock_httpx_client, mock_admin_cog):
        """admin_player should show player statistics."""
        # Mock interaction
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        # Mock user
        user = _create_mock_user(user_id=111111111, name="Test User")

        # Mock HTTP client
        mock_client = MagicMock()
        mock_httpx_client.return_value = mock_client

        # Mock API responses
        player_create_resp = MagicMock()
        player_create_resp.status_code = 200
        player_create_resp.json.return_value = {
            "id": 1,
            "discord_id": 111111111,
            "guild_id": 987654321,
            "tier": "Bronze",
            "xp": 100,
            "credits": 500,
            "lifetime_credits": 500,
            "prestige_count": 0,
            "created_at": "2024-01-01T00:00:00"
        }

        stats_resp = MagicMock()
        stats_resp.status_code = 200
        stats_resp.json.return_value = {
            "total_games": 5,
            "total_victory": 2,
            "total_defeat": 3
        }

        mock_client.post.return_value = player_create_resp
        mock_client.get.return_value = stats_resp
        mock_client.aclose = AsyncMock()

        asyncio.run(mock_admin_cog.admin_player.callback(
            mock_admin_cog, interaction, user, "view_stats", None, None
        ))

        # Verify behavior
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()

    @patch("cogs.adminCog.httpx.AsyncClient")
    def test_admin_player_set_credits(self, mock_httpx_client, mock_admin_cog):
        """admin_player should set player credits."""
        # Mock interaction
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        # Mock user
        user = _create_mock_user(user_id=111111111, name="Test User")

        # Mock HTTP client
        mock_client = MagicMock()
        mock_httpx_client.return_value = mock_client

        # Mock API responses
        player_create_resp = MagicMock()
        player_create_resp.status_code = 200
        player_create_resp.json.return_value = {"id": 1}

        update_resp = MagicMock()
        update_resp.status_code = 200
        update_resp.json.return_value = {
            "old_credits": 500,
            "new_credits": 1000
        }

        mock_client.post.return_value = player_create_resp
        mock_client.put.return_value = update_resp
        mock_client.aclose = AsyncMock()

        asyncio.run(mock_admin_cog.admin_player.callback(
            mock_admin_cog, interaction, user, "set_credits", 1000, None
        ))

        # Verify behavior
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()


class TestErrorHandling:
    """Tests for error handling in adminCog."""

    @patch("cogs.adminCog.httpx.AsyncClient")
    def test_admin_setup_api_error(self, mock_httpx_client, mock_admin_cog):
        """admin_setup should handle API errors gracefully."""
        # Mock interaction
        interaction = _create_mock_interaction()
        interaction.guild = MagicMock()
        interaction.guild.id = 987654321
        user = _create_mock_user()
        interaction.user = user

        # Mock HTTP client with error
        mock_client = MagicMock()
        mock_httpx_client.return_value = mock_client
        mock_client.post = AsyncMock(side_effect=Exception("API error"))
        mock_client.aclose = AsyncMock()

        asyncio.run(mock_admin_cog.admin_setup.callback(mock_admin_cog, interaction, None, 1000))

        # Verify error handling
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()


class TestCogSetup:
    """Tests for cog setup function."""

    def test_setup_function(self, mock_bot):
        """setup function should add adminCog to bot."""
        from cogs.adminCog import setup

        asyncio.run(setup(mock_bot))

        mock_bot.add_cog.assert_called_once()


if __name__ == '__main__':
    pytest.main([__file__])
