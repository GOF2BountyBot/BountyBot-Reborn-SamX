"""Tests for command utility functions and classes."""

import importlib
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from utils.command_utils import CommandValidator

from tests.mocks.discord_mock_utils import DiscordMockUtils

# Setup mock modules
_mock_discord = DiscordMockUtils.create_mock_discord_module()


@pytest.fixture(autouse=True)
def _ensure_real_discord_for_command_utils():
    """
    Ensure the real discord module is in sys.modules and reload
    utils.command_utils before each test so that its module-level
    ``commands`` reference (from ``from discord.ext import commands``) points
    to the real discord.ext.commands, not a test-file fake left over from
    test_discord_converters.py.
    Uses conftest's saved references which are captured before any test file
    can pollute sys.modules.
    """
    _cm = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
    sys.modules["discord"] = _cm._REAL_DISCORD
    sys.modules["discord.ext"] = _cm._REAL_DISCORD_EXT
    sys.modules["discord.ext.commands"] = _cm._REAL_DISCORD_EXT_COMMANDS

    import utils.command_utils as _cu_mod

    importlib.reload(_cu_mod)
    yield


class TestCommandValidator:
    """Tests for CommandValidator class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.validator = CommandValidator()

    def test_register_command_basic(self):
        """Test registering a basic command without permissions."""
        self.validator.register_command("test_cmd", "A test command")

        assert "test_cmd" in self.validator.command_registry
        assert self.validator.command_registry["test_cmd"]["description"] == "A test command"
        assert "registered_at" in self.validator.command_registry["test_cmd"]

    def test_register_command_with_permissions(self):
        """Test registering a command with specific permissions."""
        permissions = {"admin_only": True, "required_roles": ["Moderator"]}
        self.validator.register_command("admin_cmd", "Admin command", permissions)

        assert self.validator.command_registry["admin_cmd"]["permissions"] == permissions

    def test_register_command_multiple(self):
        """Test registering multiple commands."""
        self.validator.register_command("cmd1", "First command")
        self.validator.register_command("cmd2", "Second command")

        assert len(self.validator.command_registry) == 2
        assert "cmd1" in self.validator.command_registry
        assert "cmd2" in self.validator.command_registry

    def test_validate_permissions_unregistered_command(self):
        """Test permission validation for unregistered command returns False."""
        mock_user = MagicMock()
        mock_user.id = 123

        result = self.validator.validate_permissions("nonexistent", mock_user)
        assert result is False

    def test_validate_permissions_no_restrictions(self):
        """Test validation passes when command has no restrictions."""
        self.validator.register_command("public_cmd", "Public command")
        mock_user = MagicMock()
        mock_user.id = 123

        result = self.validator.validate_permissions("public_cmd", mock_user)
        assert result is True

    def test_validate_permissions_admin_only_false(self):
        """Test validation fails for non-admin on admin-only command."""
        permissions = {"admin_only": True}
        self.validator.register_command("admin_cmd", "Admin command", permissions)

        mock_user = MagicMock()
        mock_user.id = 123
        mock_guild = MagicMock()
        mock_member = MagicMock()
        mock_role = MagicMock()
        mock_role.name = "User"
        mock_role.permissions.administrator = False
        mock_member.roles = [mock_role]
        mock_guild.get_member.return_value = mock_member

        result = self.validator.validate_permissions("admin_cmd", mock_user, mock_guild)
        assert result is False

    def test_validate_permissions_dev_only(self):
        """Test validation for dev-only command."""
        with patch.dict(os.environ, {"DEVELOPER_IDS": "123,456"}):
            permissions = {"dev_only": True}
            self.validator.register_command("dev_cmd", "Dev command", permissions)

            mock_user = MagicMock()
            mock_user.id = 123

            result = self.validator.validate_permissions("dev_cmd", mock_user)
            assert result is True

    def test_validate_permissions_dev_only_non_dev(self):
        """Test validation fails for non-dev on dev-only command."""
        with patch.dict(os.environ, {"DEVELOPER_IDS": "123,456"}):
            permissions = {"dev_only": True}
            self.validator.register_command("dev_cmd", "Dev command", permissions)

            mock_user = MagicMock()
            mock_user.id = 999  # Not in developer list

            result = self.validator.validate_permissions("dev_cmd", mock_user)
            assert result is False

    def test_validate_permissions_required_roles(self):
        """Test validation passes when user has required role."""
        permissions = {"required_roles": ["Moderator"]}
        self.validator.register_command("mod_cmd", "Mod command", permissions)

        mock_user = MagicMock()
        mock_user.id = 123
        mock_guild = MagicMock()
        mock_member = MagicMock()
        mock_role = MagicMock()
        mock_role.name = "Moderator"
        mock_member.roles = [mock_role]
        mock_guild.get_member.return_value = mock_member

        result = self.validator.validate_permissions("mod_cmd", mock_user, mock_guild)
        assert result is True

    def test_validate_permissions_required_roles_missing(self):
        """Test validation fails when user lacks required role."""
        permissions = {"required_roles": ["Moderator"]}
        self.validator.register_command("mod_cmd", "Mod command", permissions)

        mock_user = MagicMock()
        mock_user.id = 123
        mock_guild = MagicMock()
        mock_member = MagicMock()
        mock_role = MagicMock()
        mock_role.name = "User"
        mock_member.roles = [mock_role]
        mock_guild.get_member.return_value = mock_member

        result = self.validator.validate_permissions("mod_cmd", mock_user, mock_guild)
        assert result is False

    def test_check_cooldown_first_use(self):
        """Test cooldown check on first command use."""
        result = self.validator.check_cooldown("cmd", 123)
        assert result is True

    def test_check_cooldown_within_window(self):
        """Test cooldown check within cooldown window."""
        self.validator.check_cooldown("cmd", 123, cooldown_seconds=1)
        # Should be on cooldown immediately
        result = self.validator.check_cooldown("cmd", 123, cooldown_seconds=1)
        assert result is False

    def test_check_cooldown_after_expiry(self):
        """Test cooldown check after cooldown expires."""
        self.validator.check_cooldown("cmd", 123, cooldown_seconds=0)
        # Add a small delay to ensure cooldown expires
        time.sleep(0.1)
        result = self.validator.check_cooldown("cmd", 123, cooldown_seconds=0)
        assert result is True

    def test_check_cooldown_different_users(self):
        """Test cooldown is per-user."""
        self.validator.check_cooldown("cmd", 123, cooldown_seconds=10)
        # Different user should not be on cooldown
        result = self.validator.check_cooldown("cmd", 456, cooldown_seconds=10)
        assert result is True

    def test_is_admin_with_admin_role(self):
        """Test admin check when user has admin role."""
        mock_user = MagicMock()
        mock_user.id = 123
        mock_guild = MagicMock()
        mock_member = MagicMock()
        mock_role = MagicMock()
        mock_role.name = "Admin"
        mock_role.permissions.administrator = False
        mock_member.roles = [mock_role]
        mock_guild.get_member.return_value = mock_member

        result = self.validator.is_admin(mock_user, mock_guild)
        assert result is True

    def test_is_admin_with_administrator_permission(self):
        """Test admin check when user has administrator permission."""
        mock_user = MagicMock()
        mock_user.id = 123
        mock_guild = MagicMock()
        mock_member = MagicMock()
        mock_role = MagicMock()
        mock_role.name = "User"
        mock_role.permissions.administrator = True
        mock_member.roles = [mock_role]
        mock_guild.get_member.return_value = mock_member

        result = self.validator.is_admin(mock_user, mock_guild)
        assert result is True

    def test_is_admin_no_guild(self):
        """Test admin check without guild returns False."""
        mock_user = MagicMock()
        result = self.validator.is_admin(mock_user, None)
        assert result is False

    def test_is_admin_member_not_found(self):
        """Test admin check when member not found returns False."""
        mock_user = MagicMock()
        mock_guild = MagicMock()
        mock_guild.get_member.return_value = None

        result = self.validator.is_admin(mock_user, mock_guild)
        assert result is False

    def test_is_developer_true(self):
        """Test developer check for developer user."""
        with patch.dict(os.environ, {"DEVELOPER_IDS": "123,456"}):
            mock_user = MagicMock()
            mock_user.id = 123

            result = self.validator.is_developer(mock_user)
            assert result is True

    def test_is_developer_false(self):
        """Test developer check for non-developer user."""
        with patch.dict(os.environ, {"DEVELOPER_IDS": "123,456"}):
            mock_user = MagicMock()
            mock_user.id = 999

            result = self.validator.is_developer(mock_user)
            assert result is False

    def test_is_developer_empty_env(self):
        """Test developer check with empty developer list."""
        with patch.dict(os.environ, {"DEVELOPER_IDS": ""}):
            mock_user = MagicMock()
            mock_user.id = 123

            result = self.validator.is_developer(mock_user)
            assert result is False

    def test_get_command_info_existing(self):
        """Test getting info for registered command."""
        self.validator.register_command("cmd", "A command", {"admin_only": True})
        info = self.validator.get_command_info("cmd")

        assert info is not None
        assert info["description"] == "A command"
        assert info["permissions"]["admin_only"] is True

    def test_get_command_info_nonexistent(self):
        """Test getting info for non-existent command returns None."""
        info = self.validator.get_command_info("nonexistent")
        assert info is None


class TestCommandHandler:
    """Tests for CommandHandler class."""

    def setup_method(self):
        """Set up test fixtures."""
        # Re-import from the (potentially reloaded) module so that the handler
        # uses the current discord.ext.commands reference, not a stale one from
        # when the module was first imported during test collection.
        import utils.command_utils as _cu

        self.mock_bot = MagicMock()
        self.handler = _cu.CommandHandler(self.mock_bot)

    @pytest.mark.asyncio
    async def test_execute_command_success(self):
        """Test successful command execution."""
        mock_ctx = AsyncMock()
        mock_ctx.author = MagicMock(id=123)
        mock_ctx.guild = MagicMock()
        mock_ctx.command = MagicMock(description="Test command")

        mock_handler = AsyncMock()

        result = await self.handler.execute_command(mock_ctx, "test_cmd", mock_handler)

        assert result is True
        mock_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_command_permission_denied(self):
        """Test command execution with permission denial."""
        mock_ctx = AsyncMock()
        mock_ctx.author = MagicMock(id=123)
        mock_ctx.guild = MagicMock()
        mock_ctx.command = MagicMock(description="Admin command")

        permissions = {"admin_only": True}
        mock_handler = AsyncMock()

        result = await self.handler.execute_command(mock_ctx, "admin_cmd", mock_handler, permissions=permissions)

        assert result is False
        mock_handler.assert_not_called()
        mock_ctx.respond.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_command_cooldown(self):
        """Test command execution with cooldown."""
        mock_ctx = AsyncMock()
        mock_ctx.author = MagicMock(id=123)
        mock_ctx.guild = MagicMock()
        mock_ctx.command = MagicMock(description="Cooldown command")

        mock_handler = AsyncMock()

        # First execution
        await self.handler.execute_command(mock_ctx, "cooldown_cmd", mock_handler, cooldown_seconds=10)

        # Reset mock to track second call
        mock_ctx.reset_mock()

        # Second execution (should be on cooldown)
        result = await self.handler.execute_command(mock_ctx, "cooldown_cmd", mock_handler, cooldown_seconds=10)

        assert result is False
        mock_ctx.respond.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_command_handler_error(self):
        """Test command execution with handler error."""
        mock_ctx = AsyncMock()
        mock_ctx.author = MagicMock(id=123)
        mock_ctx.guild = MagicMock()
        mock_ctx.command = MagicMock(description="Error command")

        # Handler that raises CommandError
        from discord.ext import commands

        mock_handler = AsyncMock(side_effect=commands.CommandError("Test error"))

        result = await self.handler.execute_command(mock_ctx, "error_cmd", mock_handler)

        assert result is False

    @pytest.mark.asyncio
    async def test_execute_command_generic_error(self):
        """Test command execution with generic error."""
        mock_ctx = AsyncMock()
        mock_ctx.author = MagicMock(id=123)
        mock_ctx.guild = MagicMock()
        mock_ctx.command = MagicMock(description="Generic error command")

        # Handler that raises generic error
        mock_handler = AsyncMock(side_effect=ValueError("Unexpected error"))

        result = await self.handler.execute_command(mock_ctx, "error_cmd", mock_handler)

        assert result is False

    def test_send_permission_error(self):
        """Test permission error embed creation."""
        mock_ctx = AsyncMock()

        # Call private method directly for testing
        import asyncio

        asyncio.run(self.handler._send_permission_error(mock_ctx))

        assert mock_ctx.respond.called

    def test_send_cooldown_error(self):
        """Test cooldown error embed creation."""
        mock_ctx = AsyncMock()

        # Call private method directly for testing
        import asyncio

        asyncio.run(self.handler._send_cooldown_error(mock_ctx, 5))

        assert mock_ctx.respond.called

    def test_send_command_error(self):
        """Test command error embed creation."""
        mock_ctx = AsyncMock()

        # Call private method directly for testing
        import asyncio

        asyncio.run(self.handler._send_command_error(mock_ctx, "Test error"))

        assert mock_ctx.respond.called

    def test_send_generic_error(self):
        """Test generic error embed creation."""
        mock_ctx = AsyncMock()

        # Call private method directly for testing
        import asyncio

        asyncio.run(self.handler._send_generic_error(mock_ctx))

        assert mock_ctx.respond.called


class TestGetCommandHandler:
    """Tests for get_command_handler function."""

    def test_get_command_handler_creates_instance(self):
        """Test that get_command_handler creates an instance on first call."""
        # Reset global state and use the reloaded module to avoid stale class references.
        import utils.command_utils

        utils.command_utils._command_handler = None

        mock_bot = MagicMock()
        handler = utils.command_utils.get_command_handler(mock_bot)

        assert isinstance(handler, utils.command_utils.CommandHandler)
        assert handler.bot == mock_bot

    def test_get_command_handler_returns_same_instance(self):
        """Test that get_command_handler returns the same instance on subsequent calls."""
        import utils.command_utils

        utils.command_utils._command_handler = None

        mock_bot1 = MagicMock()
        handler1 = utils.command_utils.get_command_handler(mock_bot1)

        mock_bot2 = MagicMock()
        handler2 = utils.command_utils.get_command_handler(mock_bot2)

        # Should be the same instance
        assert handler1 is handler2
