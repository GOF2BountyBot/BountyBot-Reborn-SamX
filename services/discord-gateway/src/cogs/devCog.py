import os
import discord
from discord import app_commands
from discord.ext import commands
import shared.logging as logging
import requests


logger = logging.get_logger("discord-gateway-DevCog")
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
logger.debug(f"devCog loading with api_base: {api_base}")

def is_developer():
    # return app_commands.checks.has_role("developer")
    return True

class HealthCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Pong + latency")
    #@is_developer()
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

    @app_commands.command(
        name="health",
        description="Check the health of the BountyBot API service."
    )
    #@is_developer()
    async def health(self, interaction: discord.Interaction):
        """Calls the /health endpoint and reports status."""
        logger.trace("/health command invoked by user...")
        await interaction.response.defer(thinking=True)
        try:
            logger.trace("Executing API request to bot service...")
            resp = requests.get(f"{api_base}/health", timeout=2.0)
            resp.raise_for_status()
            data = resp.json()
            logger.trace("Parsing response...")
            # Extracting information from the response
            status = data.get("status", "unknown")
            timestamp = data.get("timestamp", "N/A")
            version = data.get("version", "N/A")
            service = data.get("service", "N/A")
            environment = data.get("environment", {})
            checks = data.get("checks", {})

            logger.trace("Building Discord response...")
            # Determine the emoji based on status
            if status == "healthy":
                emoji = "✅"
                color = discord.Colour.green()
            else:
                emoji = "❌"
                color = discord.Colour.red()

            # Create the embed
            embed = discord.Embed(
                title=f"BountyBot API Health - {status}",
                description=f"**Service:** {service}\n**Version:** {version}\n**Timestamp:** {timestamp}",
                color=color
            )

            # Add environment details to the embed
            if environment:
                env_details = "\n".join([f"{key}: {value}" for key, value in environment.items()])
                embed.add_field(name="Environment", value=env_details, inline=False)

            # Add checks to the embed
            if checks:
                check_details = "\n".join([f"{key}: {'✅' if value else '❌'}" for key, value in checks.items()])
                embed.add_field(name="Checks", value=check_details, inline=False)
            await interaction.followup.send(content=emoji, embed=embed)
        except requests.RequestException as e:
            emoji = "❌"
            msg = f"Health check failed: `{e}`"

            embed = discord.Embed(
                title="BountyBot API Health",
                description=msg,
                color=discord.Colour.red()
            )
            embed.set_footer(text=f"Checked via {api_base}/health")
            await interaction.followup.send(content=emoji, embed=embed)
        logger.trace("/health command end")

async def setup(bot: commands.Bot):
    logger.debug(f"Setting up devCog...")
    await bot.add_cog(HealthCog(bot))
    logger.info("devCog loaded")
