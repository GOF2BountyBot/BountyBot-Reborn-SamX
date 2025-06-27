import logging
import os
import discord
from discord.ext import commands
import shared.logging as logging

# Enhanced logging configuration for debugging
def setup_debug_logging():
    """Set up comprehensive logging for Discord bot debugging"""

    # Set up Discord.py's internal logging
    discord_logger = logging.get_logger("discord-gateway-bot.py")

    return discord_logger

class DebugBot(commands.Bot):
    def __init__(self):
        # Enable debug logging
        self.debug_logger = setup_debug_logging()

        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            application_id=int(os.getenv("BOTAPPID", "0"))
        )

        self.logger = logging.get_logger("discord-gateway-debug")
        self.startup_complete = False

    async def setup_hook(self):
        self.logger.info("=== SETUP HOOK STARTED ===")
        self.logger.info(f"Python version: {os.sys.version}")
        self.logger.info(f"Discord.py version: {discord.__version__}")
        self.logger.info(f"Bot user: {self.user}")
        self.logger.info(f"Bot ID: {self.user.id if self.user else 'Not logged in yet'}")

        # Load cogs with detailed logging
        cog_count = 0
        for filename in os.listdir("src/cogs"):
            if filename.endswith(".py"):
                try:
                    self.logger.info(f"Loading cog: {filename}")
                    await self.load_extension(f"cogs.{filename[:-3]}")
                    cog_count += 1
                    self.logger.info(f"✓ Successfully loaded: {filename}")
                except Exception as e:
                    self.logger.error(f"✗ Failed to load {filename}: {e}")

        self.logger.info(f"=== SETUP HOOK COMPLETED - Loaded {cog_count} cogs ===")
        # DO NOT call wait_until_ready() here!

    async def on_ready(self):
        self.logger.info("=== ON_READY EVENT FIRED ===")
        self.logger.info(f"Bot logged in as: {self.user}")
        self.logger.info(f"Bot ID: {self.user.id}")
        self.logger.info(f"Bot is in {len(self.guilds)} guilds:")

        for guild in self.guilds:
            self.logger.info(f"  - {guild.name} (ID: {guild.id})")

        # Sync commands if not already done
        if not self.startup_complete:
            await self.sync_commands()
            self.startup_complete = True

        self.logger.info("=== BOT IS FULLY READY ===")

    async def sync_commands(self):
        self.logger.info("=== STARTING COMMAND SYNC ===")
        try:
            # Show commands before sync
            commands_before = self.tree.get_commands()
            self.logger.info(f"Commands before sync: {len(commands_before)}")
            for cmd in commands_before:
                self.logger.info(f"  - {cmd.name}: {cmd.description}")

            # Sync commands
            synced = await self.tree.sync()
            self.logger.info(f"✓ Successfully synced {len(synced)} commands")

            # Log synced commands
            for cmd in synced:
                self.logger.info(f"  - Synced: {cmd.name}")

        except Exception as e:
            self.logger.error(f"✗ Failed to sync commands: {e}")
            self.logger.exception("Command sync error details:")

    async def on_error(self, event, *args, **kwargs):
        self.logger.error(f"Error in event {event}")
        self.logger.exception("Event error details:")

    async def on_command_error(self, ctx, error):
        self.logger.error(f"Command error in {ctx.command}: {error}")
        self.logger.exception("Command error details:")

if __name__ == "__main__":
    # Check environment variables
    token = os.getenv("BOTTOKEN")
    app_id = os.getenv("BOTAPPID")

    if not token:
        print("ERROR: BOTTOKEN environment variable not set!")
        exit(1)

    if not app_id:
        print("WARNING: BOTAPPID environment variable not set!")

    print("=== STARTING DEBUG BOT ===")
    print(f"Token: {'*' * (len(token) - 4)}{token[-4:]}")
    print(f"App ID: {app_id}")

    bot = DebugBot()

    # Run with debug logging disabled for the library's default handler
    # We're using our custom handler instead
    bot.run(token, log_handler=None)