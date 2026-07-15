"""
Shared utility for idempotent BountyBot Discord infrastructure setup.

Provides ensure_bountybot_infrastructure() which creates (or finds existing)
the required role, category, and text channels for a guild.
"""

from collections.abc import Callable

import discord
from shared import bblogger

flogger = bblogger.get_logger("discord-gateway-GuildSetup")

# ── Constants ──────────────────────────────────────────────────────────────────

_BOUNTYBOT_CATEGORY = "BountyBot"
_BOUNTY_HUNTER_ROLE = "Bounty Hunter"
_SHOP_ANNOUNCEMENTS_ROLE = "Shop Announcements"

_TIER_ROLE_NAMES = {
    "bronze": "Bounty Hunter Bronze",
    "silver": "Bounty Hunter Silver",
    "gold": "Bounty Hunter Gold",
    "platinum": "Bounty Hunter Platinum",
}


# ── Overwrite factories ────────────────────────────────────────────────────────

# Discord.py exposes a few permission names as aliases for the same underlying
# bit (e.g. "view_channel"/"read_messages", "manage_emojis"/"manage_expressions"/
# "manage_emojis_and_stickers"). Dedupe by bit value — keeping one canonical
# name per bit — so a hard-deny built from every known permission never passes
# two different names asserting different values for the same bit. Plain
# alphabetical tie-breaking would pick the deprecated "read_messages" over
# "view_channel" (r < v), which matters here because _read_only_overwrites()
# overrides "view_channel" by name — an unpreferred alias would silently
# become a second, order-dependent dict key for the same bit instead of being
# overridden. _PREFERRED_PERMISSION_NAMES pins the modern name per alias group.
_PREFERRED_PERMISSION_NAMES = frozenset(
    {
        "view_channel",
        "manage_roles",
        "use_external_emojis",
        "use_external_stickers",
        "manage_emojis_and_stickers",
        "create_polls",
    }
)


def _all_permission_names() -> list[str]:
    canonical_by_bit: dict[int, str] = {}
    for name, bit in discord.Permissions.VALID_FLAGS.items():
        if bit not in canonical_by_bit or name in _PREFERRED_PERMISSION_NAMES:
            canonical_by_bit[bit] = name
    return sorted(canonical_by_bit.values())


_ALL_PERMISSION_NAMES = _all_permission_names()


def _read_only_overwrites(
    guild: discord.Guild,
    bounty_hunter_role: discord.Role | None,
) -> dict:
    """
    Hard-deny channel overwrite for read-only bounty-board / shop channels.

    Every known permission is explicitly denied for BOTH @everyone and Bounty
    Hunter EXCEPT view_channel and read_message_history — members may see the
    channel and read its history (and receive the bot's @-mentions there,
    which isn't permission-gated) but cannot take ANY action in it. A channel
    that only denies send_messages is not read-only: several other
    permissions (use_application_commands, thread creation/posting, reactions,
    etc.) default to whatever the guild's BASE role permissions grant unless
    explicitly denied per-channel — that gap is exactly how issue #47
    happened (regular members could run slash commands directly in the shop
    and bounty-board channels).

    @everyone:     view_channel=DENY (fully hidden), everything else DENY
    Bounty Hunter: view_channel=ALLOW, read_message_history=ALLOW, everything else DENY
    Bot:           view=ALLOW, send=ALLOW, manage_messages=ALLOW

    Only denies permissions the bot itself currently holds in this guild
    (via guild.me.guild_permissions). Discord rejects a channel overwrite
    that touches ANY permission bit the acting bot/user doesn't hold at the
    guild level — even to deny it — as an anti-privilege-escalation rule.
    A bot invited with a curated permission set (no ban_members,
    administrator, manage_guild, etc.) got a 403 "Missing Permissions" and
    silently failed to create these channels at all when this tried to
    touch every known permission unconditionally.
    """
    bot_perms = guild.me.guild_permissions
    controllable = [name for name in _ALL_PERMISSION_NAMES if getattr(bot_perms, name, False)]
    deny_all = dict.fromkeys(controllable, False)

    everyone_kwargs = dict(deny_all)  # view_channel stays False: fully hidden

    bounty_hunter_kwargs = dict(deny_all)
    bounty_hunter_kwargs["view_channel"] = True
    bounty_hunter_kwargs["read_message_history"] = True

    ow: dict = {
        guild.default_role: discord.PermissionOverwrite(**everyone_kwargs),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_messages=True,
        ),
    }
    if bounty_hunter_role is not None:
        ow[bounty_hunter_role] = discord.PermissionOverwrite(**bounty_hunter_kwargs)
    return ow


def _hunting_overwrites(
    guild: discord.Guild,
    bounty_hunter_role: discord.Role | None,
) -> dict:
    """
    Channel overwrite for #bounty-hunting.

    @everyone:     view=DENY, send=DENY
    Bounty Hunter: view=ALLOW, send=ALLOW, read_history=ALLOW, use_app_cmds=ALLOW
    Bot:           view=ALLOW, send=ALLOW, manage_messages=ALLOW
    """
    ow: dict = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False,
            send_messages=False,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_messages=True,
        ),
    }
    if bounty_hunter_role is not None:
        ow[bounty_hunter_role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            use_application_commands=True,
        )
    return ow


def _discussion_overwrites(
    guild: discord.Guild,
    bounty_hunter_role: discord.Role | None,
) -> dict:
    """
    Channel overwrite for #bounty-discussions.

    @everyone:     view=DENY, send=DENY
    Bounty Hunter: view=ALLOW, send=ALLOW, read_history=ALLOW, use_app_cmds=DENY
    Bot:           view=ALLOW, send=ALLOW, manage_messages=ALLOW
    """
    ow: dict = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False,
            send_messages=False,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_messages=True,
        ),
    }
    if bounty_hunter_role is not None:
        ow[bounty_hunter_role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            use_application_commands=False,
        )
    return ow


def _image_overwrites(
    guild: discord.Guild,
    bounty_hunter_role: discord.Role | None,
) -> dict:
    """
    Channel overwrite for #bot-images.

    @everyone:     view=DENY, send=DENY (hidden from all users)
    Bounty Hunter: view=DENY, send=DENY (hidden from all users)
    Bot:           view=ALLOW, send=ALLOW, attach_files=ALLOW
    """
    ow: dict = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False,
            send_messages=False,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            attach_files=True,
        ),
    }
    if bounty_hunter_role is not None:
        ow[bounty_hunter_role] = discord.PermissionOverwrite(
            view_channel=False,
            send_messages=False,
        )
    return ow


# ── Channel specifications ─────────────────────────────────────────────────────
# Each entry: (channel_name, result_dict_key, overwrite_factory)
# overwrite_factory signature: (guild, bounty_hunter_role | None) -> dict

_CHANNEL_SPECS: list[tuple[str, str, Callable]] = [
    ("bronze-bounty-board", "bronze_bounty_channel_id", _read_only_overwrites),
    ("silver-bounty-board", "silver_bounty_channel_id", _read_only_overwrites),
    ("gold-bounty-board", "gold_bounty_channel_id", _read_only_overwrites),
    ("platinum-bounties", "platinum_bounty_channel_id", _read_only_overwrites),
    ("shop", "shop_channel_id", _read_only_overwrites),
    ("bounty-hunting", "hunting_channel_id", _hunting_overwrites),
    ("bounty-discussions", "discussion_channel_id", _discussion_overwrites),
    ("bot-images", "image_channel_id", _image_overwrites),
]


# ── Private helpers ────────────────────────────────────────────────────────────


async def _find_or_create_role(guild: discord.Guild) -> discord.Role | None:
    """
    Find (case-insensitive) or create the '@Bounty Hunter' role.

    Returns the role, or None if creation failed.
    """
    # Use a plain for-loop (not discord.utils.find) so that guild.roles may be
    # any regular iterable without accidentally triggering async-iterator behaviour.
    roles: list = list(guild.roles) if guild.roles else []
    existing = next((r for r in roles if r.name.lower() == _BOUNTY_HUNTER_ROLE.lower()), None)
    if existing is not None:
        flogger.debug(f"Role '{_BOUNTY_HUNTER_ROLE}' already exists in guild {guild.id}")
        return existing

    try:
        role = await guild.create_role(
            name=_BOUNTY_HUNTER_ROLE,
            mentionable=True,
            hoist=False,
        )
        flogger.info(f"Created role '{_BOUNTY_HUNTER_ROLE}' in guild {guild.id}")
        return role
    except discord.Forbidden:
        flogger.warning(f"Missing permissions to create role '{_BOUNTY_HUNTER_ROLE}' in guild {guild.id}")
        return None
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(f"Error creating role '{_BOUNTY_HUNTER_ROLE}' in guild {guild.id}: {e}")
        return None


async def _find_or_create_tier_roles(guild: discord.Guild) -> dict[str, int | None]:
    """
    Find (case-insensitive) or create the 4 tier-specific '@Bounty Hunter {Tier}' roles.

    These roles are mentionable so the bot can @-mention them in bounty announcements.
    They do NOT need channel permission overwrites — channel visibility is controlled
    by the general '@Bounty Hunter' role.

    Returns a dict:
        {
            "bronze_role_id": int | None,
            "silver_role_id": int | None,
            "gold_role_id": int | None,
            "platinum_role_id": int | None,
        }
    """
    roles: list = list(guild.roles) if guild.roles else []
    result: dict[str, int | None] = {
        "bronze_role_id": None,
        "silver_role_id": None,
        "gold_role_id": None,
        "platinum_role_id": None,
    }

    for tier_key, role_name in _TIER_ROLE_NAMES.items():
        existing = next((r for r in roles if r.name.lower() == role_name.lower()), None)
        if existing is not None:
            flogger.debug(f"Tier role '{role_name}' already exists in guild {guild.id}")
            result[f"{tier_key}_role_id"] = existing.id
            continue

        try:
            role = await guild.create_role(
                name=role_name,
                mentionable=True,
                hoist=False,
            )
            flogger.info(f"Created tier role '{role_name}' in guild {guild.id}")
            result[f"{tier_key}_role_id"] = role.id
        except discord.Forbidden:
            flogger.warning(f"Missing permissions to create tier role '{role_name}' in guild {guild.id}")
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error creating tier role '{role_name}' in guild {guild.id}: {e}")

    return result


async def _find_or_create_category(
    guild: discord.Guild,
    bounty_hunter_role: discord.Role | None,
) -> discord.CategoryChannel | None:
    """
    Find (case-insensitive) or create the 'BountyBot' category with permission overwrites.

    Returns the category, or None if creation failed.
    """
    existing = discord.utils.find(
        lambda c: c.name.lower() == _BOUNTYBOT_CATEGORY.lower(),
        guild.categories,
    )
    if existing is not None:
        flogger.debug(f"Category '{_BOUNTYBOT_CATEGORY}' already exists in guild {guild.id}")
        return existing

    try:
        overwrites: dict = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_messages=True,
            ),
        }
        if bounty_hunter_role is not None:
            overwrites[bounty_hunter_role] = discord.PermissionOverwrite(view_channel=True)

        category = await guild.create_category(_BOUNTYBOT_CATEGORY, overwrites=overwrites)
        flogger.info(f"Created category '{_BOUNTYBOT_CATEGORY}' in guild {guild.id}")
        return category
    except discord.Forbidden:
        flogger.warning(f"Missing permissions to create category '{_BOUNTYBOT_CATEGORY}' in guild {guild.id}")
        return None
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(f"Error creating category in guild {guild.id}: {e}")
        return None


async def _find_or_create_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    channel_name: str,
    overwrites: dict,
) -> discord.TextChannel | None:
    """
    Find (case-insensitive) or create a text channel under the given category.

    Returns the channel, or None if creation failed.
    """
    existing = discord.utils.find(
        lambda ch: ch.name.lower() == channel_name.lower(),
        category.channels,
    )
    if existing is not None:
        flogger.debug(f"Channel '#{channel_name}' already exists in guild {guild.id}")
        return existing

    try:
        channel = await guild.create_text_channel(
            channel_name,
            category=category,
            overwrites=overwrites,
        )
        flogger.info(f"Created channel '#{channel_name}' in guild {guild.id}")
        return channel
    except discord.Forbidden:
        flogger.warning(f"Missing permissions to create channel '#{channel_name}' in guild {guild.id}")
        return None
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(f"Error creating channel '#{channel_name}' in guild {guild.id}: {e}")
        return None


# ── Public API ─────────────────────────────────────────────────────────────────


async def _find_or_create_shop_announcements_role(guild: discord.Guild) -> discord.Role | None:
    """Find (case-insensitive) or create the '@Shop Announcements' role.

    This mentionable role is used for opt-in shop refresh notifications.
    It does NOT control any channel access — only used for @-mentions.

    Returns the role, or None if creation failed.
    """
    roles: list = list(guild.roles) if guild.roles else []
    existing = next((r for r in roles if r.name.lower() == _SHOP_ANNOUNCEMENTS_ROLE.lower()), None)
    if existing is not None:
        flogger.debug(f"Role '{_SHOP_ANNOUNCEMENTS_ROLE}' already exists in guild {guild.id}")
        return existing

    try:
        role = await guild.create_role(
            name=_SHOP_ANNOUNCEMENTS_ROLE,
            mentionable=True,
            hoist=False,
        )
        flogger.info(f"Created role '{_SHOP_ANNOUNCEMENTS_ROLE}' in guild {guild.id}")
        return role
    except discord.Forbidden:
        flogger.warning(f"Missing permissions to create role '{_SHOP_ANNOUNCEMENTS_ROLE}' in guild {guild.id}")
        return None
    except Exception as e:  # pylint: disable=broad-exception-caught
        flogger.error(f"Error creating role '{_SHOP_ANNOUNCEMENTS_ROLE}' in guild {guild.id}: {e}")
        return None


async def ensure_bountybot_infrastructure(guild: discord.Guild) -> dict:
    """
    Idempotently create or find BountyBot Discord infrastructure.

    Creates (or finds existing):
    - "@Bounty Hunter" role
    - "@Bounty Hunter Bronze/Silver/Gold/Platinum" tier roles (mentionable, for @-mentions)
    - "@Shop Announcements" role (mentionable, for shop refresh @-mentions)
    - "BountyBot" category
    - 8 text channels under the category

    Returns dict with keys:
        category_id, bronze_bounty_channel_id, silver_bounty_channel_id,
        gold_bounty_channel_id, platinum_bounty_channel_id, shop_channel_id,
        hunting_channel_id, discussion_channel_id, image_channel_id,
        bounty_hunter_role_id, bronze_role_id, silver_role_id, gold_role_id,
        platinum_role_id, shop_announcements_role_id
    All values are int (Discord snowflake IDs) or None if creation failed.
    """
    result: dict[str, int | None] = {
        "category_id": None,
        "bronze_bounty_channel_id": None,
        "silver_bounty_channel_id": None,
        "gold_bounty_channel_id": None,
        "platinum_bounty_channel_id": None,
        "shop_channel_id": None,
        "hunting_channel_id": None,
        "discussion_channel_id": None,
        "image_channel_id": None,
        "bounty_hunter_role_id": None,
        "bronze_role_id": None,
        "silver_role_id": None,
        "gold_role_id": None,
        "platinum_role_id": None,
        "shop_announcements_role_id": None,
    }

    # ── Step 1: find or create @Bounty Hunter role ────────────────────────────
    bounty_hunter_role = await _find_or_create_role(guild)
    if bounty_hunter_role is not None:
        result["bounty_hunter_role_id"] = bounty_hunter_role.id

    # ── Step 1b: find or create tier roles (Bronze/Silver/Gold/Platinum) ────────
    tier_role_ids = await _find_or_create_tier_roles(guild)
    result.update(tier_role_ids)

    # ── Step 1c: find or create @Shop Announcements role ─────────────────────
    shop_announcements_role = await _find_or_create_shop_announcements_role(guild)
    if shop_announcements_role is not None:
        result["shop_announcements_role_id"] = shop_announcements_role.id

    # ── Step 2: find or create BountyBot category ─────────────────────────────
    category = await _find_or_create_category(guild, bounty_hunter_role)
    if category is None:
        # Return early; all channel IDs remain None
        return result

    result["category_id"] = category.id

    # ── Step 3: find or create each text channel ──────────────────────────────
    for channel_name, result_key, overwrite_factory in _CHANNEL_SPECS:
        overwrites = overwrite_factory(guild, bounty_hunter_role)
        channel = await _find_or_create_channel(guild, category, channel_name, overwrites)
        result[result_key] = channel.id if channel is not None else None

    # ── Backward-compatibility aliases ────────────────────────────────────────
    # These keys are retained for the transition period while SEG-03 updates
    # callers (setupCog, adminCog) to use the new key names.
    result["bounty_channel_id"] = result["bronze_bounty_channel_id"]
    result["general_channel_id"] = result["discussion_channel_id"]

    return result
