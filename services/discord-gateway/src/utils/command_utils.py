import os
import time
from typing import Any, Callable, Dict, Optional

import discord
from discord.ext import commands
from shared import bblogger


class CommandValidator:
    """Centralized command validation and permission system"""

    def __init__(self):
        self.logger = bblogger.get_logger("discord-gateway-CommandValidator")
        self.command_permissions: Dict[str, Dict[str, Any]] = {}
        self.cooldown_cache: Dict[str, Dict[str, float]] = {}
        self.command_registry: Dict[str, Dict[str, Any]] = {}

    def register_command(self, name: str, description: str, permissions: Dict[str, Any] = None):
        """Register a command with its permissions and metadata"""
        if permissions is None:
            permissions = {}

        self.command_registry[name] = {
            "description": description,
            "permissions": permissions,
            "registered_at": time.time()
        }
        self.logger.debug(f"Registered command: {name}")

    def validate_permissions(
        self, command_name: str, user: discord.User, guild: Optional[discord.Guild] = None
    ) -> bool:
        """Check if user has permissions to execute command"""
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

        # Check admin permissions
        if "admin_only" in permissions and permissions["admin_only"]:
            if not self.is_admin(user, guild):
                self.logger.debug(f"User {user.id} not admin for admin-only command")
                return False

        # Check developer permissions
        if "dev_only" in permissions and permissions["dev_only"]:
            if not self.is_developer(user):
                self.logger.debug(f"User {user.id} not developer for dev-only command")
                return False

        return True

    def check_cooldown(self, command_name: str, user_id: int, cooldown_seconds: int = 5) -> bool:
        """Check if command is on cooldown for user"""
        key = f"{command_name}:{user_id}"
        current_time = time.time()

        if key in self.cooldown_cache:
            last_used = self.cooldown_cache[key]
            if current_time - last_used < cooldown_seconds:
                return False  # Still on cooldown

        # Update cooldown
        self.cooldown_cache[key] = current_time
        return True

    def is_admin(self, user: discord.User, guild: Optional[discord.Guild] = None) -> bool:
        """Check if user is an admin in the guild"""
        if not guild:
            return False

        member = guild.get_member(user.id)
        if not member:
            return False

        # Check for admin role or specific admin permissions
        admin_roles = ["Admin", "Moderator", "Administrator"]
        for role in member.roles:
            if role.name in admin_roles or role.permissions.administrator:
                return True

        return False

    def is_developer(self, user: discord.User) -> bool:
        """Check if user is a developer"""
        # For now, check against a hardcoded list of developer IDs
        developer_ids = os.getenv("DEVELOPER_IDS", "").split(",")
        return str(user.id) in [d.strip() for d in developer_ids if d.strip()]

    def get_command_info(self, command_name: str) -> Optional[Dict[str, Any]]:
        """Get information about a registered command"""
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
        permissions: Dict[str, Any] = None,
        cooldown_seconds: int = 5
    ) -> bool:
        """Execute a command with validation and error handling"""
        user = ctx.author
        guild = ctx.guild

        # Register the command
        self.validator.register_command(
            command_name,
            ctx.command.description if ctx.command else "",
            permissions
        )

        # Check permissions
        if not self.validator.validate_permissions(command_name, user, guild):
            await self._send_permission_error(ctx)
            return False

        # Check cooldown
        if not self.validator.check_cooldown(command_name, user.id, cooldown_seconds):
            await self._send_cooldown_error(ctx, cooldown_seconds)
            return False

        try:
            # Execute the command
            await handler(ctx)
            return True
        except commands.CommandError as e:
            await self._send_command_error(ctx, str(e))
            return False
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.logger.error(f"Error executing command {command_name}", exc_info=e)
            await self._send_generic_error(ctx)
            return False

    async def _send_permission_error(self, ctx: commands.Context):
        """Send permission denied error"""
        embed = discord.Embed(
            title="🔒 Permission Denied",
            description="You don't have permission to use this command.",
            color=discord.Color.red()
        )
        await ctx.respond(embed=embed, ephemeral=True)

    async def _send_cooldown_error(self, ctx: commands.Context, cooldown_seconds: int):
        """Send cooldown error"""
        embed = discord.Embed(
            title="⏰ Command Cooldown",
            description=f"Please wait {cooldown_seconds} seconds before using this command again.",
            color=discord.Color.orange()
        )
        await ctx.respond(embed=embed, ephemeral=True)

    async def _send_command_error(self, ctx: commands.Context, error_message: str):
        """Send command-specific error"""
        embed = discord.Embed(
            title="❌ Command Error",
            description=f"An error occurred: {error_message}",
            color=discord.Color.red()
        )
        await ctx.respond(embed=embed, ephemeral=True)

    async def _send_generic_error(self, ctx: commands.Context):
        """Send generic error"""
        embed = discord.Embed(
            title="⚠️  An error occurred",
            description="Something went wrong while processing your command.",
            color=discord.Color.red()
        )
        await ctx.respond(embed=embed, ephemeral=True)

# Global command handler instance
_command_handler = None

def get_command_handler(bot: commands.Bot) -> CommandHandler:
    """Get or create the global command handler"""
    global _command_handler
    if _command_handler is None:
        _command_handler = CommandHandler(bot)
    return _command_handler
