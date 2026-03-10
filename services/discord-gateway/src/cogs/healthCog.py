import os
import discord
from discord import app_commands
from discord.ext import commands
import shared.bblogger as bblogger
import httpx
from cogs.adminCog import is_admin

flogger = bblogger.get_logger("discord-gateway-HealthCog")
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"HealthCog loading with api_base: {api_base}")

class HealthCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http_client = httpx.AsyncClient()
        flogger.debug("HealthCog initialized")

    async def cog_unload(self):
        await self.http_client.aclose()

    @app_commands.command(name="ping", description="Pong + latency")
    @is_admin()
    async def ping(self, interaction: discord.Interaction):
        latency_ms = round(self.bot.latency * 1000)
        # ephemeral: visible only to the user who invoked the command
        await interaction.response.send_message(f"Pong! Latency is {latency_ms} ms", ephemeral=True)
        flogger.debug(f"/ping by {interaction.user} in guild {interaction.guild_id}: {latency_ms} ms")

    @ping.error
    async def ping_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingRole):
            await interaction.response.send_message("❌ You need the 'developer' role.", ephemeral=True)
            flogger.warning(f"Unauthorized /ping by {interaction.user} in guild {interaction.guild_id}")
        else:
            # use the cog logger and send an ephemeral error response
            flogger.exception("Error in /ping", exc_info=error)
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)
                else:
                    await interaction.followup.send("⚠️ An error occurred.", ephemeral=True)
            except Exception:
                # ensure we don't raise while handling errors
                pass

    @app_commands.command(
        name="health",
        description="Check the health of the BountyBot API service."
    )
    @is_admin()
    async def health(self, interaction: discord.Interaction):
        """Calls the /health endpoint and reports status."""
        flogger.trace("/health command invoked by user...")
        # defer so we can do processing; we'll send an ephemeral followup
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            flogger.trace("Executing API request to bot service...")
            resp = await self.http_client.get(f"{api_base}/health", timeout=2.0)
            resp.raise_for_status()
            data = resp.json()
            flogger.trace("Response received successfully: " + str(data))
            flogger.trace("Parsing response...")
            # Extracting information from the response
            status = data.get("status", "unknown")
            timestamp = data.get("timestamp", "N/A")
            version = data.get("version", "N/A")
            service = data.get("service", "N/A")
            environment = data.get("environment", {})
            checks = data.get("checks", {})
            database_info = data.get("database_check", {})
            schema_info = data.get("schema_check", {})

            flogger.trace("Building Discord response...")
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

            # Add database information to the embed
            if database_info:
                db_status = database_info.get("status", "unknown")
                db_connectivity = database_info.get("connectivity", False)
                db_error = database_info.get("error", None)
                db_connection_pool = database_info.get("connection_pool", {})

                db_details = (
                    f"**Status:** {db_status}\n"
                    f"**Connectivity:** {'✅' if db_connectivity else '❌'}\n"
                    f"**Error:** {db_error or 'None'}\n"
                    f"**Connection Pool:**\n"
                    f"- Size: {db_connection_pool.get('size', 'N/A')}\n"
                    f"- Checked In: {db_connection_pool.get('checked_in', 'N/A')}\n"
                    f"- Checked Out: {db_connection_pool.get('checked_out', 'N/A')}\n"
                    f"- Overflow: {db_connection_pool.get('overflow', 'N/A')}"
                )
                embed.add_field(name="Database", value=db_details, inline=False)

            # Add schema information to the embed
            if schema_info:
                schema_status = schema_info.get("status", "unknown")
                schema_current_version = schema_info.get("current_version", "N/A")
                schema_expected_version = schema_info.get("expected_version", "N/A")
                schema_table_exists = schema_info.get("schema_table_exists", False)
                schema_version_match = schema_info.get("version_match", False)
                schema_error = schema_info.get("error", None)

                schema_details = (
                    f"**Status:** {schema_status}\n"
                    f"**Current Version:** {schema_current_version}\n"
                    f"**Expected Version:** {schema_expected_version}\n"
                    f"**Schema Table Exists:** {'✅' if schema_table_exists else '❌'}\n"
                    f"**Version Match:** {'✅' if schema_version_match else '❌'}\n"
                    f"**Error:** {schema_error or 'None'}"
                )
                embed.add_field(name="Schema", value=schema_details, inline=False)

            # send the health embed as ephemeral (visible only to the invoking user)
            await interaction.followup.send(content=emoji, embed=embed, ephemeral=True)
        except httpx.HTTPError as e:
            emoji = "❌"
            msg = f"Health check failed: `{e}`"

            embed = discord.Embed(
                title="BountyBot API Health",
                description=msg,
                color=discord.Colour.red()
            )
            embed.set_footer(text=f"Checked via {api_base}/health")
            try:
                await interaction.followup.send(content=emoji, embed=embed, ephemeral=True)
            except Exception:
                # swallow any followup/send errors to avoid raising during error handling
                pass
        flogger.trace("/health command end")

async def setup(bot: commands.Bot):
    flogger.debug(f"Setting up HealthCog...")
    await bot.add_cog(HealthCog(bot))
    flogger.info("HealthCog loaded")