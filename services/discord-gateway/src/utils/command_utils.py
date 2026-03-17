import os
import time
from collections.abc import Callable
from typing import Any

import discord
from discord.ext import commands
from shared import bblogger


class CommandValidator:
    """Centralized command validation and permission system"""

    def __init__(self):
        self.logger = bblogger.get_logger("discord-gateway-CommandValidator")
        self.command_permissions: dict[str, dict[str, Any]] = {}
        self.cooldown_cache: dict[str, dict[str, float]] = {}
        self.command_registry: dict[str, dict[str, Any]] = {}

    def register_command(self, name: str, description: str, permissions: dict[str, Any] | None = None):
        """Register a command with its permissions and metadata"""
        self.logger.debug(f"register_command: name={name}")
        if permissions is None:
            permissions = {}

        self.command_registry[name] = {
            "description": description,
            "permissions": permissions,
            "registered_at": time.time(),
        }
        self.logger.debug(f"Registered command: {name}")

    def validate_permissions(self, command_name: str, user: discord.User, guild: discord.Guild | None = None) -> bool:
        """Check if user has permissions to execute command"""
        self.logger.debug(f"validate_permissions: command={command_name} user={user.id} guild={guild.id if guild else None}")
        if command_name not in self.command_registry:
            self.logger.warning(f"Command {command_name} not registered")
            return False

        command_data = self.command_registry[command_name]
        permissions = command_data.get("permissions", {})

        # Check role-based permissions
        if guild and "required_roles" in permissions:
            member = guild.get_member(user.id)
            if member:
                for required_role in permissions["required_roles"]:
                    if not any(role.name == required_role for role in member.roles):
                        self.logger.debug(f"User {user.id} missing required role: {required_role}")
                        return False

        if permissions.get("admin_only") and not self.is_admin(user, guild):
            self.logger.debug(f"User {user.id} not admin for admin-only command")
            return False

        # Check developer permissions
        if permissions.get("dev_only") and not self.is_developer(user):
            self.logger.debug(f"User {user.id} not developer for dev-only command")
            return False

        return True

    def check_cooldown(self, command_name: str, user_id: int, cooldown_seconds: int = 5) -> bool:
        """Check if command is on cooldown for user"""
        self.logger.debug(f"check_cooldown: command={command_name} user={user_id} cooldown={cooldown_seconds}s")
        key = f"{command_name}:{user_id}"
        current_time = time.time()

        if key in self.cooldown_cache:
            last_used = self.cooldown_cache[key]
            if current_time - last_used < cooldown_seconds:
                self.logger.debug(f"User {user_id} still on cooldown for {command_name}")
                return False  # Still on cooldown

        # Update cooldown
        self.cooldown_cache[key] = current_time
        return True

    def is_admin(self, user: discord.User, guild: discord.Guild | None = None) -> bool:
        """Check if user is an admin in the guild"""
        self.logger.debug(f"is_admin: user={user.id} guild={guild.id if guild else None}")
        if not guild:
            self.logger.debug(f"is_admin: no guild provided for user {user.id}")
            return False

        member = guild.get_member(user.id)
        if not member:
            self.logger.debug(f"is_admin: user {user.id} not a member of guild {guild.id}")
            return False

        # Check for admin role or specific admin permissions
        admin_roles = ["Admin", "Moderator", "Administrator"]
        is_admin_result = any(role.name in admin_roles or role.permissions.administrator for role in member.roles)
        self.logger.debug(f"is_admin: user {user.id} admin status = {is_admin_result}")
        return is_admin_result

    def is_developer(self, user: discord.User) -> bool:
        """Check if user is a developer"""
        self.logger.debug(f"is_developer: user={user.id}")
        # For now, check against a hardcoded list of developer IDs
        developer_ids = os.getenv("DEVELOPER_IDS", "").split(",")
        is_dev = str(user.id) in [d.strip() for d in developer_ids if d.strip()]
        self.logger.debug(f"is_developer: user {user.id} developer status = {is_dev}")
        return is_dev

    def get_command_info(self, command_name: str) -> dict[str, Any] | None:
        """Get information about a registered command"""
        self.logger.debug(f"get_command_info: command={command_name}")
        return self.command_registry.get(command_name)


class CommandHandler:
    """Handles command execution with validation and error handling"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.validator = CommandValidator()
        self.logger = bblogger.get_logger("discord-gateway-CommandHandler")

    async def execute_command(
        self,
        ctx: commands.Context,
        command_name: str,
        handler: Callable[[commands.Context], Any],
        permissions: dict[str, Any] | None = None,
        cooldown_seconds: int = 5,
    ) -> bool:
        """Execute a command with validation and error handling"""
        self.logger.debug(
            f"execute_command: command={command_name} user={ctx.author.id} guild={ctx.guild.id if ctx.guild else None}"
        )
        user = ctx.author
        guild = ctx.guild

        # Register the command
        self.validator.register_command(command_name, ctx.command.description if ctx.command else "", permissions)

        # Check permissions
        if not self.validator.validate_permissions(command_name, user, guild):
            self.logger.debug(f"execute_command: permission denied for {command_name} user={user.id}")
            await self._send_permission_error(ctx)
            return False

        # Check cooldown
        if not self.validator.check_cooldown(command_name, user.id, cooldown_seconds):
            self.logger.debug(f"execute_command: cooldown active for {command_name} user={user.id}")
            await self._send_cooldown_error(ctx, cooldown_seconds)
            return False

        try:
            # Execute the command
            self.logger.debug(f"execute_command: executing handler for {command_name}")
            await handler(ctx)
            self.logger.debug(f"execute_command: successfully completed {command_name}")
            return True
        except commands.CommandError as e:
            self.logger.error(f"Command error in {command_name} user={user.id}: {e}")
            await self._send_command_error(ctx, str(e))
            return False
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.logger.error(f"Error executing command {command_name} user={user.id}", exc_info=e)
            await self._send_generic_error(ctx)
            return False

    async def _send_permission_error(self, ctx: commands.Context):
        """Send permission denied error"""
        self.logger.debug(f"_send_permission_error: sending to user={ctx.author.id}")
        embed = discord.Embed(
            title="🔒 Permission Denied",
            description="You don't have permission to use this command.",
            color=discord.Color.red(),
        )
        await ctx.respond(embed=embed, ephemeral=True)

    async def _send_cooldown_error(self, ctx: commands.Context, cooldown_seconds: int):
        """Send cooldown error"""
        self.logger.debug(f"_send_cooldown_error: user={ctx.author.id} cooldown={cooldown_seconds}s")
        embed = discord.Embed(
            title="⏰ Command Cooldown",
            description=f"Please wait {cooldown_seconds} seconds before using this command again.",
            color=discord.Color.orange(),
        )
        await ctx.respond(embed=embed, ephemeral=True)

    async def _send_command_error(self, ctx: commands.Context, error_message: str):
        """Send command-specific error"""
        self.logger.debug(f"_send_command_error: user={ctx.author.id} error={error_message}")
        embed = discord.Embed(
            title="❌ Command Error", description=f"An error occurred: {error_message}", color=discord.Color.red()
        )
        await ctx.respond(embed=embed, ephemeral=True)

    async def _send_generic_error(self, ctx: commands.Context):
        """Send generic error"""
        self.logger.debug(f"_send_generic_error: user={ctx.author.id}")
        embed = discord.Embed(
            title="⚠️  An error occurred",
            description="Something went wrong while processing your command.",
            color=discord.Color.red(),
        )
        await ctx.respond(embed=embed, ephemeral=True)


# Global command handler instance
_command_handler = None


def get_command_handler(bot: commands.Bot) -> CommandHandler:
    """Get or create the global command handler"""
    global _command_handler
    if _command_handler is None:
        _command_handler = CommandHandler(bot)
        _command_handler.logger.debug("get_command_handler: created new CommandHandler instance")
    return _command_handler
