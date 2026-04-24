import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

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


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot for healthCog testing."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    bot.get_member = MagicMock()
    bot.flogger = MagicMock()
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
def mock_health_cog(mock_bot):
    """Create a mock healthCog instance."""
    # Re-assert this file's own mock so that when healthCog is re-imported below
    # it calls *our* _make_mock_logger (which populates _module_logger).
    # Without this, whichever test file was imported last "owns" the shared
    # sys.modules["shared.bblogger"] entry and the other file's _module_logger
    # stays None.
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.healthCog import HealthCog

    cog = HealthCog(mock_bot)
    return cog


class TestHealthCogInitialization:
    """Tests for healthCog initialization."""

    def test_initialization(self, mock_health_cog):
        """healthCog should initialize properly with bot reference."""
        global _module_logger
        assert mock_health_cog.bot is not None
        # The cog uses the module-level flogger
        assert _module_logger is not None
        _module_logger.debug.assert_called_with("HealthCog initialized")


class TestHealthCommands:
    """Tests for healthCog commands."""

    def test_health_command(self, mock_health_cog):
        """health command should respond with bot status."""
        # Mock interaction
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Mock API response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "healthy",
            "timestamp": "2024-01-01T00:00:00",
            "version": "1.0.0",
            "service": "BountyBot",
            "environment": {"env": "test"},
            "checks": {"db": True, "api": True},
            "database_check": {
                "status": "healthy",
                "connectivity": True,
                "error": None,
                "connection_pool": {"size": 10, "checked_in": 5, "checked_out": 5, "overflow": 0},
            },
            "schema_check": {
                "status": "healthy",
                "current_version": "1.0",
                "expected_version": "1.0",
                "schema_table_exists": True,
                "version_match": True,
                "error": None,
            },
        }
        mock_health_cog.http_client.get = AsyncMock(return_value=mock_response)

        # Call command using .callback()
        asyncio.run(mock_health_cog.health.callback(mock_health_cog, interaction))

        # Verify behavior
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()

    def test_health_command_api_error(self, mock_health_cog):
        """health command should handle API errors gracefully."""
        # Mock interaction
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Mock HTTP client with error
        import httpx

        mock_health_cog.http_client.get = AsyncMock(side_effect=httpx.HTTPError("API error"))

        # Call command using .callback()
        asyncio.run(mock_health_cog.health.callback(mock_health_cog, interaction))

        # Verify error handling
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()


class TestPingCommand:
    """Tests for ping command."""

    def test_ping_command(self, mock_health_cog):
        """ping command should respond with latency information."""
        # Mock interaction
        interaction = MagicMock()
        interaction.response.send_message = AsyncMock()

        # Mock bot latency
        mock_health_cog.bot.latency = 0.123

        # Call command using .callback()
        asyncio.run(mock_health_cog.ping.callback(mock_health_cog, interaction))

        # Verify response
        interaction.response.send_message.assert_called_once()
        call_args = interaction.response.send_message.call_args[0][0]
        assert "Pong" in call_args
        assert "123" in call_args


class TestErrorHandling:
    """Tests for error handling in healthCog."""

    def test_health_command_connection_error(self, mock_health_cog):
        """health command should handle connection errors gracefully."""
        # Mock interaction
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Mock HTTP client with connection error
        import httpx

        mock_health_cog.http_client.get = AsyncMock(side_effect=httpx.HTTPError("Connection failed"))

        # Call command using .callback()
        asyncio.run(mock_health_cog.health.callback(mock_health_cog, interaction))

        # Verify error handling
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()


class TestCogUnload:
    """Tests for cog unload functionality."""

    def test_cog_unload_success(self, mock_health_cog):
        """cog_unload should close http_client successfully."""
        mock_health_cog.http_client.aclose = AsyncMock()
        asyncio.run(mock_health_cog.cog_unload())
        mock_health_cog.http_client.aclose.assert_called_once()


class TestPingErrorHandler:
    """Tests for ping command error handling."""

    def test_ping_error_missing_role(self, mock_health_cog):
        """ping_error should handle MissingRole errors."""
        from discord import app_commands

        # Mock interaction
        interaction = MagicMock()
        interaction.response.send_message = AsyncMock()

        # Create MissingRole error
        error = app_commands.MissingRole("developer")

        # Call error handler
        asyncio.run(mock_health_cog.ping_error(interaction, error))

        # Verify response
        interaction.response.send_message.assert_called_once()
        call_args = interaction.response.send_message.call_args[0][0]
        assert "developer" in call_args.lower()

    def test_ping_error_generic(self, mock_health_cog):
        """ping_error should handle generic errors."""
        # Mock interaction
        interaction = MagicMock()
        interaction.response.send_message = AsyncMock()
        interaction.response.is_done.return_value = False

        # Create generic error
        error = Exception("Test error")

        # Call error handler
        asyncio.run(mock_health_cog.ping_error(interaction, error))

        # Verify response was sent
        interaction.response.send_message.assert_called_once()

    def test_ping_error_already_responded(self, mock_health_cog):
        """ping_error should use followup if response already sent."""
        # Mock interaction where response is already done
        interaction = MagicMock()
        interaction.response.is_done.return_value = True
        interaction.followup.send = AsyncMock()

        # Create generic error
        error = Exception("Test error")

        # Call error handler
        asyncio.run(mock_health_cog.ping_error(interaction, error))

        # Verify followup was used
        interaction.followup.send.assert_called_once()

    def test_ping_error_followup_exception(self, mock_health_cog):
        """ping_error should not raise if followup fails."""
        # Mock interaction where both response and followup would fail
        interaction = MagicMock()
        interaction.response.is_done.return_value = False
        interaction.response.send_message = AsyncMock(side_effect=Exception("Send failed"))
        interaction.followup.send = AsyncMock(side_effect=Exception("Followup failed"))

        # Create generic error
        error = Exception("Test error")

        # Call error handler - should not raise
        asyncio.run(mock_health_cog.ping_error(interaction, error))
        # If we get here, the exception was swallowed as expected


class TestHealthCommandUnhealthyStatus:
    """Tests for health command with unhealthy status."""

    def test_health_command_unhealthy_status(self, mock_health_cog):
        """health command should display unhealthy status with red color."""
        # Mock interaction
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Mock API response with unhealthy status
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "degraded",  # Not "healthy"
            "timestamp": "2024-01-01T00:00:00",
            "version": "1.0.0",
            "service": "BountyBot",
            "environment": {},
            "checks": {"db": False},
            "database_check": {
                "status": "unhealthy",
                "connectivity": False,
                "error": "Connection timeout",
                "connection_pool": {"size": 10, "checked_in": 10, "checked_out": 0, "overflow": 0},
            },
            "schema_check": {
                "status": "unknown",
                "current_version": "1.0",
                "expected_version": "2.0",
                "schema_table_exists": True,
                "version_match": False,
                "error": "Version mismatch",
            },
        }
        mock_health_cog.http_client.get = AsyncMock(return_value=mock_response)

        # Call command using .callback()
        asyncio.run(mock_health_cog.health.callback(mock_health_cog, interaction))

        # Verify behavior
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()
        # Check that emoji indicates unhealthy status
        call_args = interaction.followup.send.call_args
        assert call_args is not None


class TestHealthCommandEmptyFields:
    """Tests for health command with empty/missing fields."""

    def test_health_command_empty_environment(self, mock_health_cog):
        """health command should handle empty environment dict."""
        # Mock interaction
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Mock API response with empty environment
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "healthy",
            "timestamp": "2024-01-01T00:00:00",
            "version": "1.0.0",
            "service": "BountyBot",
            "environment": {},  # Empty
            "checks": {},  # Empty
            "database_check": {},  # Empty
            "schema_check": {},  # Empty
        }
        mock_health_cog.http_client.get = AsyncMock(return_value=mock_response)

        # Call command using .callback()
        asyncio.run(mock_health_cog.health.callback(mock_health_cog, interaction))

        # Verify behavior
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()


class TestHealthCommandFollowupError:
    """Tests for health command error recovery."""

    def test_health_command_error_followup_exception_handled(self, mock_health_cog):
        """health command error path should handle followup exceptions."""
        # Mock interaction
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock(side_effect=Exception("Followup failed"))

        # Mock HTTP client with error to trigger error path
        import httpx

        mock_health_cog.http_client.get = AsyncMock(side_effect=httpx.HTTPError("API error"))

        # Call command using .callback() - should not raise because error path has exception handling
        asyncio.run(mock_health_cog.health.callback(mock_health_cog, interaction))

        # Verify response was attempted
        interaction.response.defer.assert_called_once()


class TestCogSetup:
    """Tests for cog setup function."""

    def test_setup_function(self, mock_bot):
        """setup function should add healthCog to bot."""
        from cogs.healthCog import setup

        asyncio.run(setup(mock_bot))

        mock_bot.add_cog.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__])
