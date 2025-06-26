import os
import discord
from discord.ext import commands
import shared.logging as logging

logger = logging.get_logger("discord-gateway-bot.py")

class GatewayBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents,
                         application_id=int(os.getenv("BOTAPPID", "0")))

    async def setup_hook(self):
        for filename in os.listdir("src/cogs"):
            if filename.endswith(".py"):
                await self.load_extension(f"cogs.{filename[:-3]}")
                logger.info(f"Loaded cog: {filename}")
        await self.wait_until_ready()
        guilds = [discord.Object(id=g.id) for g in self.guilds]
        await self.tree.sync(guild=guilds)
        logger.info(f"Synced slash commands to guilds: {[g.id for g in self.guilds]}")

if __name__ == "__main__":
    token = os.getenv("BOTTOKEN")
    if not token:
        logger.error("BOTTOKEN not set")
        exit(1)
    bot = GatewayBot()
    bot.run(token)
