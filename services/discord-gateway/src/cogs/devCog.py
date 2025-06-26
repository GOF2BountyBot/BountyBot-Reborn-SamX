import discord
from discord import app_commands
from discord.ext import commands
import shared.logging as logging


logger = logging.get_logger("discord-gateway-DevCog")

def is_developer():
    # return app_commands.checks.has_role("developer")
    return True

class PingCog(commands.Cog):
    """Cog containing /ping restricted to 'developer' role."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Pong + latency")
    @is_developer()
    async def ping(self, interaction: discord.Interaction):
        latency_ms = round(self.bot.latency * 1000)
        await interaction.response.send_message(f"Pong! Latency is {latency_ms} ms")
        logger.debug(f"/ping by {interaction.user} in guild {interaction.guild_id}: {latency_ms} ms")

    @ping.error
    async def ping_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingRole):
            await interaction.response.send_message("❌ You need the 'developer' role.", ephemeral=True)
            logger.warning(f"Unauthorized /ping by {interaction.user} in guild {interaction.guild_id}")
        else:
            logger.exception("Error in /ping", exc_info=error)
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(PingCog(bot))
    logger.info("devCog loaded")
