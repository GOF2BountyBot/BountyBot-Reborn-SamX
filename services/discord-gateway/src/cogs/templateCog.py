import os

import discord
from shared import bblogger
from discord import app_commands
from discord.ext import commands

# Set up logger
flogger = bblogger.get_logger("discord-gateway-TemplateCog")

# Define any environment variables or constants here
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"templateCog loading with API_BASE_URL: {api_base}")

def is_developer():
    # Example role check, uncomment and configure as needed
    # return app_commands.checks.has_role("developer")
    return True

class TemplateCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        flogger.debug("TemplateCog initialized")

    @app_commands.command(name="example", description="Example command")
    #@is_developer()
    async def example(self, interaction: discord.Interaction):
        """Example command to demonstrate functionality."""
        await interaction.response.send_message("This is an example command.")
        flogger.debug(f"/example by {interaction.user} in guild {interaction.guild_id}")

    @example.error
    async def example_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingRole):
            await interaction.response.send_message("❌ You need the 'developer' role.", ephemeral=True)
            flogger.warning(f"Unauthorized /example by {interaction.user} in guild {interaction.guild_id}")
        else:
            flogger.exception("Error in /example", exc_info=error)
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

async def setup(bot: commands.Bot):
    flogger.debug("Setting up templateCog...")
    await bot.add_cog(TemplateCog(bot))
    flogger.info("templateCog loaded")
