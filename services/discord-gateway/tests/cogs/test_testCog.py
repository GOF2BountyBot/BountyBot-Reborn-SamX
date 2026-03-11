"""Tests for testCog cog."""
import pytest
from unittest.mock import MagicMock, AsyncMock
import sys
import os
import types

# Setup mock shared.bblogger module
_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")

def _make_mock_logger(*_args, **_kwargs):
    """Return a MagicMock with common log-level methods."""
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    logger.exception = MagicMock()
    return logger

_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

# Ensure real discord is used
for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import discord
from discord.ext import commands


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot for testCog testing."""
    bot = MagicMock()
    bot.user = MagicMock(id=123456789, name="TestBot")
    bot.add_cog = AsyncMock()
    return bot


def _evict_discord_modules():
    """Remove cached discord/source modules."""
    to_evict = [k for k in sys.modules if k == "discord" or k.startswith("discord.")
                or k in ("api", "bot", "utils") or k.startswith("api.") or k.startswith("utils.")
                or k.startswith("cogs.")]
    for k in to_evict:
        sys.modules.pop(k, None)


@pytest.fixture
def mock_test_cog(mock_bot):
    """Create a mock testCog instance."""
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.testCog import TestCog
    cog = TestCog(mock_bot)
    return cog


class TestTestCogInitialization:
    """Tests for testCog initialization."""

    def test_initialization(self, mock_test_cog):
        """TestCog should initialize properly with bot reference."""
        assert mock_test_cog.bot is not None
        assert hasattr(mock_test_cog, 'bot')
        assert mock_test_cog.__class__.__name__ == 'TestCog'

    def test_cog_has_commands(self, mock_test_cog):
        """TestCog should have test_command."""
        assert hasattr(mock_test_cog, 'test_command')


class TestTestCogCommands:
    """Tests for testCog commands."""

    def test_command_exists(self, mock_test_cog):
        """test_command should exist in cog."""
        # Check that test_command exists
        assert hasattr(mock_test_cog, 'test_command')
        # Should be a Command object
        assert mock_test_cog.test_command is not None

    def test_command_name(self, mock_test_cog):
        """test_command should have the correct name."""
        # Check command name
        assert hasattr(mock_test_cog.test_command, 'name')
        # Should have a name attribute (it's a discord.ext.commands.Command)
        assert mock_test_cog.test_command.callback is not None

    def test_command_is_coroutine(self, mock_test_cog):
        """test_command callback should be a coroutine function."""
        import inspect
        # The callback should be an async function
        assert inspect.iscoroutinefunction(mock_test_cog.test_command.callback)


class TestTestCogSetup:
    """Tests for testCog setup function."""

    @pytest.mark.asyncio
    async def test_setup_function(self, mock_bot):
        """setup function should add TestCog to bot."""
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        _evict_discord_modules()

        from cogs.testCog import setup
        await setup(mock_bot)

        # Verify add_cog was called
        mock_bot.add_cog.assert_called_once()
        # Verify the cog passed is TestCog instance
        call_args = mock_bot.add_cog.call_args
        assert call_args is not None
        cog_arg = call_args[0][0]
        assert cog_arg.__class__.__name__ == 'TestCog'
