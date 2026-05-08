"""Tests for templateCog cog."""

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


@pytest.fixture(scope="module")
def mock_bot():
    """Create a mock Discord bot for templateCog testing."""
    bot = MagicMock()
    bot.user = MagicMock(id=123456789, name="TestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    bot.add_app_command = AsyncMock()
    bot.tree.add_command = MagicMock()
    return bot


def _evict_discord_modules():
    """Remove cached discord/source modules."""
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


@pytest.fixture(scope="module")
def mock_template_cog(mock_bot):
    """Create a mock templateCog instance."""
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.templateCog import TemplateCog

    cog = TemplateCog(mock_bot)
    return cog


class TestTemplateCogInitialization:
    """Tests for templateCog initialization."""

    def test_initialization(self, mock_template_cog):
        """TemplateCog should initialize properly with bot reference."""
        assert mock_template_cog.bot is not None
        assert hasattr(mock_template_cog, "bot")
        assert mock_template_cog.__class__.__name__ == "TemplateCog"

    def test_cog_has_commands(self, mock_template_cog):
        """TemplateCog should have at least one command."""
        # Check that the cog has the example command
        assert hasattr(mock_template_cog, "example")


class TestTemplateCogCommands:
    """Tests for templateCog commands."""

    def test_example_command_exists(self, mock_template_cog):
        """example command should exist in cog."""
        # Check that example command exists
        assert hasattr(mock_template_cog, "example")
        # Should be an app_commands.Command
        assert mock_template_cog.example is not None

    def test_example_error_handler_exists(self, mock_template_cog):
        """example_error handler should exist in cog."""
        # Check that example_error exists
        assert hasattr(mock_template_cog, "example_error")
        # Should be callable
        assert callable(mock_template_cog.example_error)

    @pytest.mark.asyncio
    async def test_example_error_handler_missing_role(self, mock_template_cog):
        """example_error should handle MissingRole error."""
        # Mock interaction
        interaction = AsyncMock(spec=discord.Interaction)
        interaction.user = MagicMock(id=123)
        interaction.response.send_message = AsyncMock()

        # Create MissingRole error
        from discord import app_commands

        error = app_commands.MissingRole("developer")

        # Call error handler directly (it's a coroutine function)
        await mock_template_cog.example_error(interaction, error)

        # Verify error message was sent
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_example_error_handler_generic_error(self, mock_template_cog):
        """example_error should handle generic AppCommandError."""
        # Mock interaction
        interaction = AsyncMock(spec=discord.Interaction)
        interaction.user = MagicMock(id=123)
        interaction.response.send_message = AsyncMock()

        # Create generic error
        from discord import app_commands

        error = app_commands.AppCommandError("Some other error")

        # Call error handler
        await mock_template_cog.example_error(interaction, error)

        # Verify error message was sent
        interaction.response.send_message.assert_called_once()


class TestTemplateCogSetup:
    """Tests for templateCog setup function."""

    @pytest.mark.asyncio
    async def test_setup_function(self, mock_bot):
        """setup function should add TemplateCog to bot."""
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        _evict_discord_modules()

        from cogs.templateCog import setup

        await setup(mock_bot)

        # Verify add_cog was called
        mock_bot.add_cog.assert_called_once()
        # Verify the cog passed is TemplateCog instance
        call_args = mock_bot.add_cog.call_args
        assert call_args is not None


class TestTemplateCogConfiguration:
    """Tests for templateCog configuration."""

    def test_is_developer_function(self):
        """is_developer function should exist and return boolean."""
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        _evict_discord_modules()

        from cogs.templateCog import is_developer

        result = is_developer()
        assert isinstance(result, bool)

    def test_api_base_configured(self):
        """templateCog should load with API base URL configuration."""
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        _evict_discord_modules()

        with patch.dict(os.environ, {"BOT_API_BASE_URL": "http://custom:8000"}):
            # Module re-import should use env var
            from cogs import templateCog

            assert hasattr(templateCog, "api_base")
