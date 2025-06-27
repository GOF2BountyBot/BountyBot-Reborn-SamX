import os
import discord
from discord.ext import commands
import shared.logging as logging

logger = logging.get_logger("discord-gateway-bot.py")

class GatewayBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents,
                        application_id=int(os.getenv("BOTAPPID", "0")))

    async def setup_hook(self):
        # Load cogs during setup - this is fine
        for filename in os.listdir("src/cogs"):
            if filename.endswith(".py"):
                await self.load_extension(f"cogs.{filename[:-3]}")
                logger.info(f"Loaded cog: {filename}")
        logger.info("Setup hook completed - bot will now proceed to ready")

    async def on_ready(self):
        # This event fires when the bot is fully ready
        logger.info(f"Bot is ready! Logged in as {self.user}")
        logger.info("Checking guilds...")

        # Now sync slash commands - this is the proper place to do it
        try:
            if self.guilds:
                logger.info("Starting command sync...")
                # Sync to all guilds the bot is in
                logger.info("=== STARTING GUILD-SPECIFIC SYNC ===")
                for guild in self.guilds:
                    try:
                        synced = await self.tree.sync(guild=discord.Object(id=guild.id))
                        logger.info(f"✓ Synced {len(synced)} commands to {guild.name}")
                    except Exception as e:
                        logger.error(f"✗ Failed to sync to {guild.name}: {e}")
                        if "403" in str(e):
                            logger.error("Bot missing application.commands scope!")
            else:
                logger.warning("Bot is not in any guilds - no commands to sync")
        except Exception as e:
            logger.error(f"Failed to sync commands: {e}")

if __name__ == "__main__":
    token = os.getenv("BOTTOKEN")
    if not token:
        logger.error("BOTTOKEN not set")
        exit(1)
    bot = GatewayBot()
    bot.run(token)