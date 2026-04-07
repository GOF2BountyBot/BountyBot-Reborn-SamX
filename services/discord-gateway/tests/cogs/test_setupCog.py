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

    def test_on_guild_join_sends_welcome_to_system_channel(self, mock_setup_cog):
        """on_guild_join should send a welcome message to the system channel."""
        import discord

        guild = _make_mock_guild()

        system_channel = MagicMock(spec=discord.TextChannel)
        system_channel.name = "general"
        system_channel.send = AsyncMock()
        guild.system_channel = system_channel

        asyncio.run(mock_setup_cog.on_guild_join(guild))

        # Welcome message was sent
        system_channel.send.assert_called_once()
        embed_arg = system_channel.send.call_args[1]["embed"]
        assert embed_arg is not None
        # Should mention /admin_setup
        assert "admin_setup" in embed_arg.description

    def test_on_guild_join_falls_back_to_writable_channel(self, mock_setup_cog):
        """on_guild_join should fall back to first writable text channel if no system channel."""
        import discord

        guild = _make_mock_guild()
        guild.system_channel = None

        writable_channel = MagicMock(spec=discord.TextChannel)
        writable_channel.name = "chat"
        writable_channel.send = AsyncMock()
        writable_channel.permissions_for = MagicMock(return_value=MagicMock(send_messages=True))
        guild.text_channels = [writable_channel]

        asyncio.run(mock_setup_cog.on_guild_join(guild))

        writable_channel.send.assert_called_once()

    def test_on_guild_join_no_channel_available(self, mock_setup_cog):
        """on_guild_join should not raise if no writable channel is found."""
        guild = _make_mock_guild()
        guild.system_channel = None
        guild.text_channels = []

        # Should NOT raise
        asyncio.run(mock_setup_cog.on_guild_join(guild))

    def test_on_guild_join_permission_error_graceful(self, mock_setup_cog):
        """on_guild_join should handle Discord Forbidden errors gracefully."""
        import discord

        guild = _make_mock_guild()

        system_channel = MagicMock(spec=discord.TextChannel)
        system_channel.name = "general"
        system_channel.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "Forbidden"))
        guild.system_channel = system_channel

        # Should NOT raise
        asyncio.run(mock_setup_cog.on_guild_join(guild))

    def test_on_guild_join_does_not_create_infrastructure(self, mock_setup_cog):
        """on_guild_join should NOT create channels, categories, or roles."""
        import discord

        guild = _make_mock_guild()

        system_channel = MagicMock(spec=discord.TextChannel)
        system_channel.name = "general"
        system_channel.send = AsyncMock()
        guild.system_channel = system_channel

        asyncio.run(mock_setup_cog.on_guild_join(guild))

        # No infrastructure creation — only /admin_setup should create channels/roles
        guild.create_category.assert_not_called()
        guild.create_text_channel.assert_not_called()
        guild.fetch_roles.assert_not_called()

    def test_on_guild_join_generic_error_graceful(self, mock_setup_cog):
        """on_guild_join should handle unexpected errors gracefully."""
        import discord

        guild = _make_mock_guild()

        system_channel = MagicMock(spec=discord.TextChannel)
        system_channel.name = "general"
        system_channel.send = AsyncMock(side_effect=RuntimeError("unexpected"))
        guild.system_channel = system_channel

        # Should NOT raise
        asyncio.run(mock_setup_cog.on_guild_join(guild))


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
