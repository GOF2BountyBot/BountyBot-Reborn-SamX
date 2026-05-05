import os

import discord
import httpx
from discord.ext import commands
from shared import bblogger

# Set up logger
flogger = bblogger.get_logger("discord-gateway-SetupCog")

# Base URL of the bot-core API
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"setupCog loading with API_BASE_URL: {api_base}")


class SetupCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        flogger.debug("SetupCog initialized")

    async def cog_unload(self):
        await self.http_client.aclose()

    # ------------------------------------------------------------------
    # on_guild_join — send welcome message when bot is added to a guild
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        """Handle bot joining a new guild: send a welcome message directing admins to /admin_setup."""
        flogger.info(f"Bot joined guild {guild.id} ({guild.name})")

        # Find a channel to send the welcome message
        try:
            welcome_channel = guild.system_channel
            if welcome_channel is None:
                welcome_channel = next(
                    (c for c in guild.text_channels if c.permissions_for(guild.me).send_messages),
                    None,
                )

            if welcome_channel is not None:
                embed = discord.Embed(
                    title="Welcome to BountyBot!",
                    description=(
                        "Thanks for adding **BountyBot** to your server! "
                        "BountyBot is an immersive space-bounty hunting game for Discord.\n\n"
                        "A server admin needs to run `/admin_setup` to initialize the bot, "
                        "create game channels, and configure permissions."
                    ),
                    color=discord.Color.blurple(),
                )
                embed.add_field(
                    name="/admin_setup",
                    value="Initialize the bot: creates roles, channels, and game infrastructure.",
                    inline=False,
                )
                embed.add_field(
                    name="/help",
                    value="Show a full list of available commands.",
                    inline=False,
                )
                embed.set_footer(text="BountyBot — Fly safe, pilot.")

                await welcome_channel.send(embed=embed)
                flogger.info(f"Welcome message sent to #{welcome_channel.name} in guild {guild.id}")
            else:
                flogger.warning(f"No suitable channel found to send welcome message in guild {guild.id}")

        except discord.Forbidden:
            flogger.warning(f"Missing permission to send welcome message in guild {guild.id}")
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error sending welcome message in guild {guild.id}: {e}")

        # Sync slash commands to the newly joined guild
        try:
            self.bot.tree.copy_global_to(guild=guild)
            await self.bot.tree.sync(guild=discord.Object(id=guild.id))
            flogger.info(f"Slash commands synced to guild {guild.id} ({guild.name})")
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.warning(f"Failed to sync commands to guild {guild.id}: {e}")

    # ------------------------------------------------------------------
    # on_guild_remove — cleanup when bot is removed from a guild
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        """Handle bot being removed from a guild."""
        flogger.info(f"Bot removed from guild {guild.id} ({guild.name})")

        # Optionally call cleanup endpoint — best-effort, non-blocking
        try:
            resp = await self.http_client.delete(
                f"{api_base}/admin/guilds/{guild.id}/cleanup",
                timeout=10,
            )
            if resp.status_code == 200:
                flogger.info(f"Cleanup API call succeeded for guild {guild.id}")
            else:
                flogger.debug(f"Cleanup endpoint returned {resp.status_code} for guild {guild.id}")
        except Exception as e:  # pylint: disable=broad-exception-caught
            # Cleanup failure is non-fatal
            flogger.debug(f"Cleanup API call failed for guild {guild.id} (non-fatal): {e}")


async def setup(bot: commands.Bot):
    flogger.debug("Setting up SetupCog...")
    await bot.add_cog(SetupCog(bot))
    flogger.info("SetupCog loaded")
