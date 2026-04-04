"""
Shared utility for idempotent BountyBot Discord infrastructure setup.

Provides ensure_bountybot_infrastructure() which creates (or finds existing)
the required category and text channels for a guild.
"""

import discord
from shared import bblogger

flogger = bblogger.get_logger("discord-gateway-GuildSetup")

# ── Constants ──────────────────────────────────────────────────────────────────

_BOUNTYBOT_CATEGORY = "BountyBot"

# channel_name → result dict key
_CHANNEL_KEY_MAP: dict[str, str] = {
    "bounty-board": "bounty_channel_id",
    "shop": "shop_channel_id",
    "general": "general_channel_id",
}


async def ensure_bountybot_infrastructure(guild: discord.Guild) -> dict:
    """
    Idempotently create or find BountyBot Discord infrastructure.

    Creates (or finds existing):
    - "BountyBot" category
    - "bounty-board" text channel (under category)
    - "shop" text channel (under category)
    - "general" text channel (under category)

    Returns dict with keys:
        category_id, bounty_channel_id, shop_channel_id, general_channel_id
    All values are int (Discord snowflake IDs) or None if creation failed.
    """
    result: dict[str, int | None] = {
        "category_id": None,
        "bounty_channel_id": None,
        "shop_channel_id": None,
        "general_channel_id": None,
    }

    # ── Step 1: find or create the BountyBot category ────────────────────────
    category: discord.CategoryChannel | None = None
    try:
        existing_category = discord.utils.find(
            lambda c: c.name.lower() == _BOUNTYBOT_CATEGORY.lower(),
            guild.categories,
        )
        if existing_category is not None:
            category = existing_category
            flogger.debug(f"Category '{_BOUNTYBOT_CATEGORY}' already exists in guild {guild.id}")
        else:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=False),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            }
            category = await guild.create_category(_BOUNTYBOT_CATEGORY, overwrites=overwrites)
            flogger.info(f"Created category '{_BOUNTYBOT_CATEGORY}' in guild {guild.id}")

        result["category_id"] = category.id

    except discord.Forbidden:
        flogger.warning(f"Missing permissions to create category '{_BOUNTYBOT_CATEGORY}' in guild {guild.id}")
        return result
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(f"Error creating category in guild {guild.id}: {e}")
        return result

    # ── Step 2: find or create each text channel under the category ───────────
    for channel_name, result_key in _CHANNEL_KEY_MAP.items():
        try:
            existing_channel = discord.utils.get(category.channels, name=channel_name)
            if existing_channel is not None:
                result[result_key] = existing_channel.id
                flogger.debug(f"Channel '#{channel_name}' already exists in guild {guild.id}")
            else:
                new_channel = await guild.create_text_channel(channel_name, category=category)
                result[result_key] = new_channel.id
                flogger.info(f"Created channel '#{channel_name}' in guild {guild.id}")

        except discord.Forbidden:
            flogger.warning(f"Missing permissions to create channel '#{channel_name}' in guild {guild.id}")
            result[result_key] = None
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error creating channel '#{channel_name}' in guild {guild.id}: {e}")
            result[result_key] = None

    return result
