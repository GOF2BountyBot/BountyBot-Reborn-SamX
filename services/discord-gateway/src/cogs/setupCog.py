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
    # on_guild_join — auto-setup when bot is added to a new guild
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        """Handle bot joining a new guild: initialize API and create channels."""
        flogger.info(f"Bot joined guild {guild.id} ({guild.name})")

        # 1. Create (or find) BountyBot channels via shared utility
        from utils.guild_setup import ensure_bountybot_infrastructure

        channel_ids = await ensure_bountybot_infrastructure(guild)

        # 2. Initialize guild via bot-core API (include channel IDs)
        try:
            init_payload: dict = {"guild_id": guild.id}
            if channel_ids.get("category_id") is not None:
                init_payload["category_id"] = channel_ids["category_id"]
            if channel_ids.get("bounty_channel_id") is not None:
                init_payload["bounty_channel_id"] = channel_ids["bounty_channel_id"]
            if channel_ids.get("shop_channel_id") is not None:
                init_payload["shop_channel_id"] = channel_ids["shop_channel_id"]
            if channel_ids.get("general_channel_id") is not None:
                init_payload["general_channel_id"] = channel_ids["general_channel_id"]

            resp = await self.http_client.post(
                f"{api_base}/admin/guilds/initialize",
                json=init_payload,
                timeout=30,
            )
            resp.raise_for_status()
            flogger.info(f"Guild {guild.id} initialized via API")
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.warning(f"Failed to initialize guild {guild.id} via API: {e}")
            # Continue even if API init fails — channels are already set up

        # 3. Send welcome message to the general channel
        try:
            welcome_channel = None

            # Prefer the general channel returned by the infrastructure utility
            general_channel_id = channel_ids.get("general_channel_id")
            if general_channel_id is not None:
                welcome_channel = guild.get_channel(general_channel_id)

            # Fall back to guild's system channel or first writable text channel
            if welcome_channel is None:
                welcome_channel = guild.system_channel
            if welcome_channel is None:
                welcome_channel = next(
                    (c for c in guild.text_channels if c.permissions_for(guild.me).send_messages),
                    None,
                )

            if welcome_channel is not None:
                embed = discord.Embed(
                    title="🚀 Welcome to BountyBot!",
                    description=(
                        "Thanks for adding **BountyBot** to your server! "
                        "BountyBot is an immersive space-bounty hunting game for Discord.\n\n"
                        "Get started with the commands below:"
                    ),
                    color=discord.Color.blurple(),
                )
                embed.add_field(
                    name="/admin_setup",
                    value="Configure your admin role and initialize the bot.",
                    inline=False,
                )
                embed.add_field(
                    name="/admin_config",
                    value="Customize bot settings for your server.",
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
