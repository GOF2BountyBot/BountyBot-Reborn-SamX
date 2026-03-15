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

# Channels to create inside the BountyBot category
_BOUNTYBOT_CATEGORY = "BountyBot"
_GAME_CHANNELS = ["bounty-board", "shop", "general"]


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

        # 1. Initialize guild via bot-core API
        try:
            resp = await self.http_client.post(
                f"{api_base}/admin/guilds/initialize",
                json={"guild_id": guild.id},
                timeout=30,
            )
            resp.raise_for_status()
            flogger.info(f"Guild {guild.id} initialized via API")
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.warning(f"Failed to initialize guild {guild.id} via API: {e}")
            # Continue even if API init fails — try to set up channels

        # 2. Create BountyBot category and channels
        category = None
        try:
            # Check if category already exists
            existing_category = discord.utils.get(guild.categories, name=_BOUNTYBOT_CATEGORY)
            if existing_category:
                category = existing_category
                flogger.debug(f"Category '{_BOUNTYBOT_CATEGORY}' already exists in guild {guild.id}")
            else:
                # Build overwrites so the bot can always send messages
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=False),
                    guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                }
                category = await guild.create_category(_BOUNTYBOT_CATEGORY, overwrites=overwrites)
                flogger.info(f"Created category '{_BOUNTYBOT_CATEGORY}' in guild {guild.id}")

            # Create channels under the category (skip if they already exist)
            for channel_name in _GAME_CHANNELS:
                existing_channel = discord.utils.get(category.channels, name=channel_name)
                if existing_channel is None:
                    await guild.create_text_channel(channel_name, category=category)
                    flogger.info(f"Created channel '#{channel_name}' in guild {guild.id}")
                else:
                    flogger.debug(f"Channel '#{channel_name}' already exists in guild {guild.id}")

        except discord.Forbidden:
            flogger.warning(f"Missing permissions to create channels in guild {guild.id}")
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error creating channels in guild {guild.id}: {e}")

        # 3. Send welcome message to general or first text channel
        try:
            welcome_channel = None

            # Prefer the 'general' channel we just created (or that already exists)
            if category:
                welcome_channel = discord.utils.get(category.channels, name="general")

            # Fall back to guild's system channel or first text channel
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
