import logging
import os
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
from aiohttp import web
import shared.logging as logging

class GatewayBot(commands.Bot):
    def __init__(self):
        # Enable debug logging
        # self.debug_logger = setup_debug_logging()

        intents = discord.Intents.default()
        intents.message_content = True
        #intents.members = True
        #intents.presences = True
        #intents.reactions = True
        #intents.messages = True
        #intents.guilds = True
        

        super().__init__(
            command_prefix="!",
            intents=intents,
            application_id=int(os.getenv("BOTAPPID", "0"))
        )

        self.logger = logging.get_logger("discord-gateway")
        self.startup_complete = False

    async def _start_health_server(self, host: str = "0.0.0.0", port: int = 8080):
        """Tiny HTTP server that answers /health.
        Returns 200 only after discord.py says the bot is ready.
        Docker will poll this URL in its HEALTHCHECK.
        """
        app = web.Application()

        async def health(request):
            if self.is_ready():
                return web.json_response(
                    {"status": "ok", "latency_ms": round(self.latency * 1000)}
                )
            return web.json_response({"status": "starting"}, status=503)

        app.router.add_get("/health", health)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        self.logger.info(f"Health-check server listening on {host}:{port}")
        self._health_runner = runner        # keep a reference to avoid GC


    async def setup_hook(self):
        self.logger.info("=== SETUP HOOK STARTED ===")
        self.logger.debug(f"Python version: {os.sys.version}")
        self.logger.debug(f"Discord.py version: {discord.__version__}")
        self.logger.info(f"Bot user: {self.user}")
        self.logger.debug(f"Bot ID: {self.user.id if self.user else 'Not logged in yet'}")

        # Start health server early to provide feedback during startup
        await self._start_health_server()

        # Load cogs with detailed logging
        cog_count = 0
        for filename in os.listdir("src/cogs"):
            if filename.endswith(".py"):
                try:
                    self.logger.debug(f"Loading cog: {filename}")
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
        self.logger.debug(f"Bot ID: {self.user.id}")
        self.logger.debug(f"Bot is in {len(self.guilds)} guilds:")

        for guild in self.guilds:
            self.logger.trace(f"  - {guild.name} (ID: {guild.id})")

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
            self.logger.debug(f"Commands before sync: {len(commands_before)}")
            for cmd in commands_before:
                self.logger.trace(f"  - {cmd.name}: {cmd.description}")
            
            if self.guilds:
                self.logger.debug("Starting command sync...")
                # Sync to all guilds the bot is in
                self.logger.debug("=== STARTING GUILD-SPECIFIC SYNC ===")
                for guild in self.guilds:
                    try:
                        synced = await self.tree.sync(guild=discord.Object(id=guild.id))
                        self.logger.trace(f"✓ Synced {len(synced)} commands to {guild.name}")
                    except Exception as e:
                        self.logger.error(f"✗ Failed to sync to {guild.name}: {e}")
                        if "403" in str(e):
                            self.logger.error("Bot missing application.commands scope!")

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

    print("=== STARTING BOT ===")
    print(f"Token: {'*' * (len(token) - 4)}{token[-4:]}")
    print(f"App ID: {app_id}")

    bot = GatewayBot()

    # Run with debug logging disabled for the library's default handler
    # We're using our custom handler instead
    bot.run(token, log_handler=None)
    # bot.run(token, log_level=3)