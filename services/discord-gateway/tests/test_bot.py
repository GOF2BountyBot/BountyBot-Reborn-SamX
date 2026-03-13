import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# Import discord_mock_utils for consistent mock patterns
from tests.mocks.discord_mock_utils import DiscordMockUtils

# Create module-level mock utilities
_mock_utils = DiscordMockUtils()

# Setup mock shared.bblogger module
_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_args, **_kwargs):
    """Return a MagicMock that already has common log-level methods."""
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    return logger


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

# Ensure real discord is used (not a hand-rolled fake from another test module)
for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import discord
from discord.ext import commands


def create_mock_cog_file(cog_name):
    """Create a mock cog file for testing."""
    cog_content = f"""
import discord
from discord.ext import commands
from shared import bblogger

flogger = bblogger.get_logger('test-cog-{cog_name}')

class {cog_name}Cog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        flogger.debug('Initializing {cog_name}Cog')

    @commands.command()
    async def test_command(self, ctx):
        await ctx.send('Test command from {cog_name}Cog')

async def setup(bot):
    await bot.add_cog({cog_name}Cog(bot))
"""
    return cog_content


@pytest.fixture
def mock_bot():
    """Create a mock GatewayBot instance for testing."""
    bot = MagicMock(spec=commands.Bot)
    bot.user = MagicMock(spec=discord.ClientUser)
    bot.user.id = 123456789
    bot.user.name = "TestBot"
    bot.is_ready = MagicMock(return_value=True)
    bot.add_cog = MagicMock()
    bot.remove_cog = MagicMock()
    bot.tree = MagicMock()
    bot.tree.copy_global_to = MagicMock()
    bot.tree.sync = AsyncMock()
    bot.get_guild = MagicMock()
    bot.get_channel = MagicMock()
    bot.get_member = MagicMock()
    bot.guilds = []
    bot.flogger = MagicMock()
    bot.startup_complete = False
    return bot


def _evict_discord_modules():
    """Remove cached discord/source modules so they re-import with real discord."""
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


@pytest.fixture
def mock_gateway_bot():
    """Create a mock GatewayBot-like instance with proper initialization."""
    _evict_discord_modules()
    from bot import GatewayBot

    # Use MagicMock so we can freely set attributes without hitting property restrictions
    bot = MagicMock(spec=GatewayBot)
    bot.user = MagicMock()
    bot.user.id = 123456789
    bot.user.name = "TestBot"
    bot.is_ready = MagicMock(return_value=True)
    bot.add_cog = MagicMock()
    bot.remove_cog = MagicMock()
    bot.tree = MagicMock()
    bot.tree.copy_global_to = MagicMock()
    bot.tree.sync = AsyncMock()
    bot.get_guild = MagicMock()
    bot.get_channel = MagicMock()
    bot.get_member = MagicMock()
    bot.guilds = []
    bot.flogger = MagicMock()
    bot.startup_complete = False
    # Attach real async methods from GatewayBot
    bot.on_ready = GatewayBot.on_ready.__get__(bot, GatewayBot)
    bot.sync_commands = GatewayBot.sync_commands.__get__(bot, GatewayBot)
    bot.setup_hook = GatewayBot.setup_hook.__get__(bot, GatewayBot)
    return bot


class TestGatewayBotInitialization:
    """Tests for GatewayBot initialization and setup."""

    def test_bot_initialization(self, mock_gateway_bot):
        """GatewayBot should initialize with correct intents and properties."""
        from bot import GatewayBot

        bot = GatewayBot()
        assert bot.command_prefix == "!"
        assert bot.intents.message_content is True
        assert bot.intents.guilds is True
        assert bot.intents.members is True
        assert bot.flogger is not None
        assert bot.startup_complete is False

    @patch("os.getenv")
    def test_bot_application_id(self, mock_getenv, mock_gateway_bot):
        """GatewayBot should use BOTAPPID from environment."""
        mock_getenv.return_value = "987654321"

        from bot import GatewayBot

        bot = GatewayBot()
        assert bot.application_id == 987654321

    @patch("os.getenv")
    def test_bot_default_application_id(self, mock_getenv, mock_gateway_bot):
        """GatewayBot should default to 0 if BOTAPPID not set."""
        # Simulate os.getenv returning the default value (env var not set)
        mock_getenv.side_effect = lambda key, default=None: default

        from bot import GatewayBot

        bot = GatewayBot()
        assert bot.application_id == 0


class TestSetupHook:
    """Tests for bot setup_hook method."""

    @patch("os.listdir")
    @patch("os.path.join")
    def test_setup_hook_loads_cogs(self, mock_join, mock_listdir, mock_gateway_bot):
        """setup_hook should load all cog files except template, disabled, and test ones."""
        mock_listdir.return_value = ["aboutCog.py", "adminCog.py", "templateCog.py", "disabledCog.py", "testCog.py"]
        mock_join.return_value = "src/cogs/aboutCog.py"

        bot = mock_gateway_bot
        bot.flogger = MagicMock()

        # Mock load_extension
        bot.load_extension = AsyncMock()

        # Call setup_hook
        asyncio.run(bot.setup_hook())

        # Should load aboutCog and adminCog, skip templateCog, disabledCog, and testCog
        bot.load_extension.assert_any_call("cogs.aboutCog")
        bot.load_extension.assert_any_call("cogs.adminCog")
        assert call("cogs.templateCog") not in bot.load_extension.call_args_list
        assert call("cogs.disabledCog") not in bot.load_extension.call_args_list
        assert call("cogs.testCog") not in bot.load_extension.call_args_list

        bot.flogger.info.assert_called_with("=== SETUP HOOK COMPLETED (2 cogs) ===")

    @patch("os.listdir")
    @patch("os.path.join")
    def test_setup_hook_error_handling(self, mock_join, mock_listdir, mock_gateway_bot):
        """setup_hook should handle cog loading errors gracefully."""
        mock_listdir.return_value = ["errorCog.py"]
        mock_join.return_value = "src/cogs/errorCog.py"

        bot = mock_gateway_bot
        bot.flogger = MagicMock()

        # Mock load_extension to raise exception
        bot.load_extension = AsyncMock(side_effect=Exception("Test error"))

        # Call setup_hook
        with pytest.raises(Exception):  # noqa: B017
            asyncio.run(bot.setup_hook())

        bot.flogger.error.assert_called_with("✗ Cog load failed errorCog.py: Test error")


class TestOnReadyEvent:
    """Tests for on_ready event handler."""

    def test_on_ready_logs_in(self, mock_gateway_bot):
        """on_ready should log bot login information."""
        bot = mock_gateway_bot
        bot.flogger = MagicMock()
        bot.user = MagicMock()
        bot.user.name = "TestBot"
        bot.user.id = 123456789
        bot.user.__str__ = MagicMock(return_value="TestBot")

        asyncio.run(bot.on_ready())

        bot.flogger.info.assert_any_call("Bot logged in as TestBot (123456789)")

    def test_on_ready_syncs_commands_once(self, mock_gateway_bot):
        """on_ready should sync commands only once when startup_complete is False."""
        bot = mock_gateway_bot
        bot.flogger = MagicMock()
        bot.user = MagicMock()
        bot.user.name = "TestBot"
        bot.user.id = 123456789
        bot.startup_complete = False
        bot.sync_commands = AsyncMock()

        asyncio.run(bot.on_ready())

        bot.sync_commands.assert_called_once()
        assert bot.startup_complete is True
        bot.flogger.info.assert_called_with("Commands synced")

    def test_on_ready_does_not_resync(self, mock_gateway_bot):
        """on_ready should not resync commands if startup_complete is True."""
        bot = mock_gateway_bot
        bot.flogger = MagicMock()
        bot.user = MagicMock()
        bot.user.name = "TestBot"
        bot.user.id = 123456789
        bot.startup_complete = True
        bot.sync_commands = AsyncMock()

        asyncio.run(bot.on_ready())

        bot.sync_commands.assert_not_called()
        assert call("Commands synced") not in bot.flogger.info.call_args_list


class TestSyncCommands:
    """Tests for sync_commands method."""

    @patch("discord.Object")
    def test_sync_commands_global_only(self, mock_object, mock_gateway_bot):
        """sync_commands should sync globally if bot has no guilds."""
        bot = mock_gateway_bot
        bot.guilds = []
        bot.tree.sync = AsyncMock()

        asyncio.run(bot.sync_commands())

        bot.tree.sync.assert_called_once_with()
        bot.tree.copy_global_to.assert_not_called()

    @patch("discord.Object")
    def test_sync_commands_guild_specific(self, mock_object, mock_gateway_bot):
        """sync_commands should sync per guild if bot has guilds."""
        guild = MagicMock()
        guild.id = 987654321
        bot = mock_gateway_bot
        bot.guilds = [guild]
        bot.tree.sync = AsyncMock()
        bot.tree.copy_global_to = MagicMock()

        asyncio.run(bot.sync_commands())

        bot.tree.copy_global_to.assert_called_once_with(guild=guild)
        bot.tree.sync.assert_called_once_with(guild=mock_object.return_value)
        mock_object.assert_called_once_with(id=987654321)


class TestCogManagement:
    """Tests for cog loading and management."""

    @patch("os.listdir")
    @patch("os.path.join")
    def test_load_all_cogs(self, mock_join, mock_listdir, mock_gateway_bot):
        """Should be able to load all cog files dynamically, skipping template/disabled/test cogs."""
        mock_listdir.return_value = ["healthCog.py", "testCog.py", "templateCog.py"]
        mock_join.return_value = "src/cogs/healthCog.py"

        bot = mock_gateway_bot
        bot.load_extension = AsyncMock()
        bot.flogger = MagicMock()

        asyncio.run(bot.setup_hook())

        # healthCog should be loaded; testCog and templateCog must be skipped
        bot.load_extension.assert_called_once_with("cogs.healthCog")
        assert call("cogs.testCog") not in bot.load_extension.call_args_list
        assert call("cogs.templateCog") not in bot.load_extension.call_args_list


class TestErrorHandling:
    """Tests for error handling in bot methods."""

    def test_setup_hook_with_invalid_cog(self, mock_gateway_bot):
        """setup_hook should handle invalid cog files gracefully."""
        bot = mock_gateway_bot
        bot.flogger = MagicMock()
        bot.load_extension = AsyncMock(side_effect=Exception("Extension not found"))

        # Mock listdir to include invalid file
        with (
            patch("os.listdir", return_value=["invalidCog.py"]),
            patch("os.path.join", return_value="src/cogs/invalidCog.py"),
            pytest.raises(Exception),  # noqa: B017
        ):
            asyncio.run(bot.setup_hook())

        bot.flogger.error.assert_called()


if __name__ == "__main__":
    pytest.main([__file__])
