import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# Import discord_mock_utils for consistent mock patterns
from tests.mocks.discord_mock_utils import DiscordMockUtils

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


@pytest.fixture(scope="module")
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


def _make_interaction():
    """Build a spec'd interaction so a typo'd attribute access fails loudly.

    `interaction.user.guild_permissions.administrator` is left as an
    auto-vivified truthy MagicMock attribute (User/Member aren't spec'd here)
    so `_check_is_admin` short-circuits True without an HTTP call, matching
    the existing convention used across this file's happy-path tests.
    """
    import discord

    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.user = MagicMock()
    interaction.user.id = 987654321
    interaction.guild_id = 123456789
    return interaction


@pytest.fixture(scope="module")
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
    import cogs.healthCog as health_module

    cog = health_module.HealthCog(mock_bot)
    return cog


class TestHealthCogInitialization:
    """Tests for healthCog initialization."""

    def test_initialization(self, mock_health_cog):
        """healthCog should initialize properly with bot reference."""
        assert mock_health_cog.bot is not None
        # The cog uses the module-level flogger
        assert _module_logger is not None
        _module_logger.debug.assert_called_with("HealthCog initialized")


class TestHealthCommands:
    """Tests for healthCog commands."""

    def test_health_command(self, mock_health_cog):
        """health command should render a healthy embed: green colour, check emoji, all sections."""
        import discord

        interaction = _make_interaction()

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

        call_kwargs = interaction.followup.send.call_args.kwargs
        assert call_kwargs["content"] == "✅"
        assert call_kwargs["ephemeral"] is True

        embed = call_kwargs["embed"]
        assert isinstance(embed, discord.Embed)
        assert embed.title == "BountyBot API Health - healthy"
        assert embed.colour == discord.Colour.green()
        assert "BountyBot" in embed.description
        assert "1.0.0" in embed.description

        fields_by_name = {f.name: f.value for f in embed.fields}
        assert fields_by_name["Environment"] == "env: test"
        assert "db: ✅" in fields_by_name["Checks"]
        assert "api: ✅" in fields_by_name["Checks"]
        assert "**Status:** healthy" in fields_by_name["Database"]
        assert "**Connectivity:** ✅" in fields_by_name["Database"]
        assert "Size: 10" in fields_by_name["Database"]
        assert "**Status:** healthy" in fields_by_name["Schema"]
        assert "**Version Match:** ✅" in fields_by_name["Schema"]

    def test_health_command_api_error(self, mock_health_cog):
        """health command should render a red error embed on httpx.HTTPError."""
        import discord
        import httpx

        interaction = _make_interaction()

        mock_health_cog.http_client.get = AsyncMock(side_effect=httpx.HTTPError("API error"))

        # Call command using .callback()
        asyncio.run(mock_health_cog.health.callback(mock_health_cog, interaction))

        # Verify error handling
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()

        call_kwargs = interaction.followup.send.call_args.kwargs
        assert call_kwargs["content"] == "❌"
        assert call_kwargs["ephemeral"] is True

        embed = call_kwargs["embed"]
        assert isinstance(embed, discord.Embed)
        assert embed.title == "BountyBot API Health"
        assert embed.colour == discord.Colour.red()
        assert "Health check failed" in embed.description
        assert "API error" in embed.description
        assert embed.footer.text.startswith("Checked via")


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
        """health command should render a red error embed on connection failure."""
        import discord
        import httpx

        interaction = _make_interaction()

        mock_health_cog.http_client.get = AsyncMock(side_effect=httpx.HTTPError("Connection failed"))

        # Call command using .callback()
        asyncio.run(mock_health_cog.health.callback(mock_health_cog, interaction))

        # Verify error handling
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()

        call_kwargs = interaction.followup.send.call_args.kwargs
        assert call_kwargs["content"] == "❌"
        embed = call_kwargs["embed"]
        assert isinstance(embed, discord.Embed)
        assert embed.colour == discord.Colour.red()
        assert "Connection failed" in embed.description


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
        """health command should display unhealthy status with red color and cross emoji."""
        import discord

        interaction = _make_interaction()

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

        call_kwargs = interaction.followup.send.call_args.kwargs
        # emoji/status/colour must reflect the non-"healthy" branch, not the happy path
        assert call_kwargs["content"] == "❌"

        embed = call_kwargs["embed"]
        assert isinstance(embed, discord.Embed)
        assert embed.title == "BountyBot API Health - degraded"
        assert embed.colour == discord.Colour.red()

        fields_by_name = {f.name: f.value for f in embed.fields}
        assert "db: ❌" in fields_by_name["Checks"]
        assert "**Status:** unhealthy" in fields_by_name["Database"]
        assert "**Connectivity:** ❌" in fields_by_name["Database"]
        assert "**Error:** Connection timeout" in fields_by_name["Database"]
        assert "**Status:** unknown" in fields_by_name["Schema"]
        assert "**Version Match:** ❌" in fields_by_name["Schema"]
        assert "**Error:** Version mismatch" in fields_by_name["Schema"]
        # empty "environment" dict must NOT add a field (falsy-guard branch)
        assert "Environment" not in fields_by_name


class TestHealthCommandEmptyFields:
    """Tests for health command with empty/missing fields."""

    def test_health_command_empty_environment(self, mock_health_cog):
        """health command should omit fields entirely when their source dicts are empty."""
        import discord

        interaction = _make_interaction()

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

        call_kwargs = interaction.followup.send.call_args.kwargs
        assert call_kwargs["content"] == "✅"
        embed = call_kwargs["embed"]
        assert isinstance(embed, discord.Embed)
        assert embed.colour == discord.Colour.green()
        # every optional section is gated on a truthy dict, so all four are skipped
        assert embed.fields == []


class TestHealthCommandFollowupError:
    """Tests for health command error recovery."""

    def test_health_command_error_followup_exception_handled(self, mock_health_cog):
        """health command error path should swallow a followup.send failure but still
        have attempted to send the correct red error embed before it raised."""
        import discord
        import httpx

        interaction = _make_interaction()
        interaction.followup.send = AsyncMock(side_effect=Exception("Followup failed"))

        mock_health_cog.http_client.get = AsyncMock(side_effect=httpx.HTTPError("API error"))

        # Call command using .callback() - should not raise because error path has exception handling
        asyncio.run(mock_health_cog.health.callback(mock_health_cog, interaction))

        # Verify response was attempted
        interaction.response.defer.assert_called_once()

        # the call was made (and recorded) before the AsyncMock side_effect raised
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args.kwargs
        assert call_kwargs["content"] == "❌"
        embed = call_kwargs["embed"]
        assert isinstance(embed, discord.Embed)
        assert embed.colour == discord.Colour.red()
        assert "API error" in embed.description


class TestCogSetup:
    """Tests for cog setup function."""

    def test_setup_function(self, mock_bot):
        """setup function should add healthCog to bot."""
        import cogs.healthCog as health_module

        asyncio.run(health_module.setup(mock_bot))

        mock_bot.add_cog.assert_called_once()


# ===========================================================================
# Cross-1: Defer fires BEFORE admin check in /health command
# ===========================================================================


class TestCrossOneHealthDeferBeforeAdminCheck:
    """Cross-1: /health defers before _check_is_admin() to avoid consuming the 3-second
    Discord budget before the interaction is acknowledged.
    """

    def test_health_defer_before_admin_check(self, mock_health_cog):
        """Cross-1: /health must defer before the inline admin check fires.

        Patches cogs.healthCog._check_is_admin (the local name bound by the import)
        rather than cogs.adminCog._check_is_admin.
        """
        import cogs.healthCog as health_module

        original = health_module._check_is_admin
        call_order = []

        async def track_defer(*a, **kw):
            call_order.append("defer")

        async def fake_not_admin(interaction):
            call_order.append("admin_check")
            return False

        interaction = MagicMock()
        interaction.guild_id = 123456789
        interaction.user = MagicMock()
        interaction.user.id = 987654321
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = False
        interaction.user.roles = []
        interaction.response = AsyncMock()
        interaction.response.defer = track_defer
        interaction.followup = AsyncMock()

        health_module._check_is_admin = fake_not_admin
        try:
            asyncio.run(mock_health_cog.health.callback(mock_health_cog, interaction))
        finally:
            health_module._check_is_admin = original

        assert "defer" in call_order
        assert "admin_check" in call_order
        assert call_order.index("defer") < call_order.index("admin_check"), (
            "defer must fire before admin check in /health"
        )

    def test_health_non_admin_rejected_via_followup(self, mock_health_cog):
        """Cross-1: Non-admin user rejected via followup.send (post-defer) in /health."""
        import cogs.healthCog as health_module

        original = health_module._check_is_admin

        async def fake_not_admin(interaction):
            return False

        interaction = MagicMock()
        interaction.guild_id = 123456789
        interaction.user = MagicMock()
        interaction.user.id = 987654321
        interaction.response = AsyncMock()
        interaction.followup = AsyncMock()

        health_module._check_is_admin = fake_not_admin
        try:
            asyncio.run(mock_health_cog.health.callback(mock_health_cog, interaction))
        finally:
            health_module._check_is_admin = original

        # defer must have been called first
        interaction.response.defer.assert_awaited_once()
        # rejection via followup (not send_message)
        interaction.followup.send.assert_awaited_once()
        msg = str(interaction.followup.send.call_args)
        assert "admin" in msg.lower() or "privilege" in msg.lower()


if __name__ == "__main__":
    pytest.main([__file__])
