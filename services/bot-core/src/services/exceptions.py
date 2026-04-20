"""
Service-layer exceptions for the BountyBot system.

These exceptions are defined in a separate module to avoid circular imports
and to allow them to be imported without pulling in SQLAlchemy dependencies.
"""


class GuildNotConfiguredError(Exception):
    """Raised when a guild has no guild_configs row (admin_setup not run yet)."""

    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        super().__init__(f"Guild {guild_id} has not been configured. An admin must run /admin_setup first.")
