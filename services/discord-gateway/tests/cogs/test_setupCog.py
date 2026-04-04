"""
Tests for setupCog.py — on_guild_join and on_guild_remove event handlers.
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


def _make_mock_guild(guild_id: int = 123456789, name: str = "Test Guild"):
    """Build a minimal mock discord.Guild."""
    import discord

    guild = MagicMock(spec=discord.Guild)
    guild.id = guild_id
    guild.name = name

    # Default role
    default_role = MagicMock()
    guild.default_role = default_role

    # Bot member
    me = MagicMock()
    me.id = 999999
    guild.me = me

    # Category / channel helpers
    guild.categories = []
    guild.text_channels = []
    guild.system_channel = None

    guild.create_category = AsyncMock(return_value=_make_mock_category(guild_id))
    guild.create_text_channel = AsyncMock(return_value=MagicMock())
    guild.fetch_roles = AsyncMock(return_value=[])

    return guild


def _make_mock_category(guild_id: int = 123456789):
    """Build a minimal mock discord.CategoryChannel."""
    import discord

    cat = MagicMock(spec=discord.CategoryChannel)
    cat.name = "BountyBot"
    cat.channels = []
    return cat


# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------


@pytest.fixture
def mock_bot():
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from discord.ext import commands

    bot = MagicMock(spec=commands.Bot)
    bot.add_cog = AsyncMock()
    return bot


@pytest.fixture
def mock_setup_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.setupCog import SetupCog

    cog = SetupCog(mock_bot)
    return cog


# -------------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------------


class TestOnGuildJoin:
    """Tests for on_guild_join event listener."""

    def test_on_guild_join_success(self, mock_setup_cog):
        """on_guild_join should initialize guild, create channels, and send welcome."""
        guild = _make_mock_guild()

        # Mock the bot's general channel to receive welcome message
        import discord

        general_channel = MagicMock(spec=discord.TextChannel)
        general_channel.name = "general"
        general_channel.id = 444
        general_channel.send = AsyncMock()
        general_channel.permissions_for = MagicMock(return_value=MagicMock(send_messages=True))

        # The category that gets created — channels list includes general so it is
        # found by ensure_bountybot_infrastructure (no creation needed for general).
        mock_cat = _make_mock_category()
        mock_cat.channels = [general_channel]
        guild.create_category = AsyncMock(return_value=mock_cat)

        # guild.get_channel returns the general channel (used by new welcome logic)
        guild.get_channel = MagicMock(return_value=general_channel)

        # API response: success
        api_resp = MagicMock()
        api_resp.status_code = 200
        api_resp.raise_for_status = MagicMock()
        mock_setup_cog.http_client.post = AsyncMock(return_value=api_resp)

        asyncio.run(mock_setup_cog.on_guild_join(guild))

        # API was called
        mock_setup_cog.http_client.post.assert_called_once()
        call_url = mock_setup_cog.http_client.post.call_args[0][0]
        assert "admin/guilds/initialize" in call_url

        # Category was created
        guild.create_category.assert_called_once()

        # Welcome message was sent to the general channel
        general_channel.send.assert_called_once()
        embed_arg = general_channel.send.call_args[1]["embed"]
        assert embed_arg is not None

    def test_on_guild_join_api_failure_graceful(self, mock_setup_cog):
        """on_guild_join should continue even if the API call fails."""
        guild = _make_mock_guild()

        import discord

        general_channel = MagicMock(spec=discord.TextChannel)
        general_channel.name = "general"
        general_channel.id = 444
        general_channel.send = AsyncMock()
        general_channel.permissions_for = MagicMock(return_value=MagicMock(send_messages=True))

        mock_cat = _make_mock_category()
        mock_cat.channels = [general_channel]
        guild.create_category = AsyncMock(return_value=mock_cat)

        # guild.get_channel returns the general channel (used by new welcome logic)
        guild.get_channel = MagicMock(return_value=general_channel)

        # API call fails
        mock_setup_cog.http_client.post = AsyncMock(side_effect=Exception("Connection refused"))

        # Should NOT raise — failure is handled gracefully
        asyncio.run(mock_setup_cog.on_guild_join(guild))

        # Channel creation still proceeds even when API fails
        guild.create_category.assert_called_once()
        # Welcome message should still be sent (channels were set up before API call)
        general_channel.send.assert_called_once()

    def test_on_guild_join_permission_error_graceful(self, mock_setup_cog):
        """on_guild_join should handle Discord permission errors when creating channels."""
        guild = _make_mock_guild()

        import discord

        # Category creation raises Forbidden
        class FakeResponse:
            status = 403
            reason = "Forbidden"

        guild.create_category = AsyncMock(side_effect=discord.Forbidden(FakeResponse(), "Missing Permissions"))
        guild.system_channel = None
        guild.text_channels = []

        # API success
        api_resp = MagicMock()
        api_resp.status_code = 200
        api_resp.raise_for_status = MagicMock()
        mock_setup_cog.http_client.post = AsyncMock(return_value=api_resp)

        # Should NOT raise
        asyncio.run(mock_setup_cog.on_guild_join(guild))

        # API was still called
        mock_setup_cog.http_client.post.assert_called_once()

    def test_on_guild_join_existing_category_skipped(self, mock_setup_cog):
        """on_guild_join should reuse existing BountyBot category instead of creating a new one."""
        import discord

        guild = _make_mock_guild()

        general_channel = MagicMock(spec=discord.TextChannel)
        general_channel.name = "general"
        general_channel.id = 444
        general_channel.send = AsyncMock()
        general_channel.permissions_for = MagicMock(return_value=MagicMock(send_messages=True))

        # The "BountyBot" category already exists
        existing_cat = MagicMock(spec=discord.CategoryChannel)
        existing_cat.name = "BountyBot"
        existing_cat.channels = [general_channel]
        guild.categories = [existing_cat]

        # guild.get_channel returns the general channel (used by new welcome logic)
        guild.get_channel = MagicMock(return_value=general_channel)

        # API success
        api_resp = MagicMock()
        api_resp.status_code = 200
        api_resp.raise_for_status = MagicMock()
        mock_setup_cog.http_client.post = AsyncMock(return_value=api_resp)

        asyncio.run(mock_setup_cog.on_guild_join(guild))

        # create_category should NOT have been called
        guild.create_category.assert_not_called()
        # Welcome message was still sent
        general_channel.send.assert_called_once()


class TestOnGuildRemove:
    """Tests for on_guild_remove event listener."""

    def test_on_guild_remove_logs_removal(self, mock_setup_cog):
        """on_guild_remove should log the removal event."""
        guild = _make_mock_guild()

        # Cleanup endpoint — any response is fine
        cleanup_resp = MagicMock()
        cleanup_resp.status_code = 200
        mock_setup_cog.http_client.delete = AsyncMock(return_value=cleanup_resp)

        asyncio.run(mock_setup_cog.on_guild_remove(guild))

        # Module-level logger should have logged an info message
        global _module_logger
        if _module_logger is not None:
            _module_logger.info.assert_called()

    def test_on_guild_remove_cleanup_failure_is_nonfatal(self, mock_setup_cog):
        """on_guild_remove should not raise if the cleanup API call fails."""
        guild = _make_mock_guild()

        mock_setup_cog.http_client.delete = AsyncMock(side_effect=Exception("Connection refused"))

        # Must not raise
        asyncio.run(mock_setup_cog.on_guild_remove(guild))


class TestSetupCogInit:
    """Tests for SetupCog initialization."""

    def test_initialization(self, mock_setup_cog):
        """SetupCog should initialize with bot reference and http client."""
        assert mock_setup_cog.bot is not None
        assert mock_setup_cog.http_client is not None

    def test_setup_function(self, mock_bot):
        """setup() should add SetupCog to the bot."""
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        _evict_discord_modules()
        from cogs.setupCog import setup

        asyncio.run(setup(mock_bot))
        mock_bot.add_cog.assert_called_once()

    def test_cog_unload_closes_http_client(self, mock_setup_cog):
        """cog_unload should close the HTTP client."""
        mock_setup_cog.http_client = MagicMock()
        mock_setup_cog.http_client.aclose = AsyncMock()

        asyncio.run(mock_setup_cog.cog_unload())

        mock_setup_cog.http_client.aclose.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
