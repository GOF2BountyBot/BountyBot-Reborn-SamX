import discord
from cogs.adminCog import is_admin
from discord import app_commands
from discord.ext import commands
from shared import bblogger

# Set up logger
flogger = bblogger.get_logger("discord-gateway-HelpCog")
flogger.debug("helpCog loading")

# ---------------------------------------------------------------------------
# Admin-command detection constants
# ---------------------------------------------------------------------------

# Cog class names whose commands are considered admin-only
_ADMIN_COG_NAMES: frozenset[str] = frozenset(
    {
        "AdminCog",
        "SchedulerCog",
        "DevCog",
        "HealthCog",
    }
)

# Explicit command-name prefixes that mark a command as admin-only
_ADMIN_NAME_PREFIXES: tuple[str, ...] = (
    "admin_",
    "scheduler_",
)

# Explicit command names that are admin-only (not covered by prefix rules)
_ADMIN_COMMAND_NAMES: frozenset[str] = frozenset(
    {
        "ping",
        "health",
        "load_data",
        "reload_autocomplete",
        "force_reload_caches",
        "render_config",
        "render_cache_clear",
    }
)

# ---------------------------------------------------------------------------
# Category mapping — command name → user-facing category label
# ---------------------------------------------------------------------------

_COMMAND_CATEGORIES: dict[str, str] = {
    # Player Profile
    "profile": "Player Profile",
    "register": "Player Profile",
    "leaderboard": "Player Profile",
    "prestige": "Player Profile",
    "promote": "Player Profile",
    "demote": "Player Profile",
    "notifications": "Player Profile",
    "unregister": "Player Profile",
    # Bounty Hunting
    "check": "Bounty Hunting",
    "bounties": "Bounty Hunting",
    "route": "Bounty Hunting",
    "criminal-loadout": "Bounty Hunting",
    # Shop & Economy
    "shop": "Shop & Economy",
    "buy": "Shop & Economy",
    "sell": "Shop & Economy",
    "shops": "Shop & Economy",
    # Inventory & Equipment
    "inventory": "Inventory & Equipment",
    "search": "Inventory & Equipment",
    "item": "Inventory & Equipment",
    "equip": "Inventory & Equipment",
    "unequip": "Inventory & Equipment",
    "give": "Inventory & Equipment",
    "loadout": "Inventory & Equipment",
    # Ships
    "ships": "Ships",
    "ship": "Ships",
    "setactive": "Ships",
    "nickname": "Ships",
    # Dueling
    "duel-challenge": "Dueling",
    "duel-accept": "Dueling",
    "duel-reject": "Dueling",
    "duel-cancel": "Dueling",
    # Combat
    "combat-log": "Combat",
    "combat-log-pvp": "Combat",
    "combat-log-bounty": "Combat",
    # Game Data
    "about": "Game Data",
    "list_category": "Game Data",
    "make-route": "Game Data",
    # Skins & Rendering
    "ship_skin": "Skins & Rendering",
    "render_skin": "Skins & Rendering",
    "make_skin_texture": "Skins & Rendering",
    # Events
    "events": "Events",
    "event_leaderboard": "Events",
}

# Category labels in the order they should appear for users
_USER_CATEGORY_ORDER: list[str] = [
    "Player Profile",
    "Bounty Hunting",
    "Shop & Economy",
    "Inventory & Equipment",
    "Ships",
    "Dueling",
    "Combat",
    "Events",
    "Game Data",
    "Skins & Rendering",
]

# Short descriptions for each user-facing category
_USER_CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "Player Profile": "Register, view your stats, change tiers, leaderboards, manage notifications",
    "Bounty Hunting": "Hunt criminals across the galaxy for credits and XP",
    "Shop & Economy": "Buy and sell ships, weapons, modules",
    "Inventory & Equipment": "Manage your items and equipped loadout",
    "Ships": "View and manage your owned ships",
    "Dueling": "Challenge other players to combat",
    "Combat": "Review the details of your past battles",
    "Events": "Admin-run challenges: live and upcoming events, rules, standings, and the medal table",
    "Game Data": "Browse ships, weapons, criminals, and star systems",
    "Skins & Rendering": "Customize your ship appearance",
}

# Admin category labels in the order they should appear
_ADMIN_CATEGORY_MAPPING: dict[str, str] = {
    "admin_setup": "Admin — Setup",
    "admin_uninstall": "Admin — Setup",
    "admin_check": "Admin — Setup",
    "admin_sync_roles": "Admin — Setup",
    "admin_player": "Admin — Players",
    "admin_give_item": "Admin — Players",
    "admin_give_ship": "Admin — Players",
    "admin_remove_item": "Admin — Players",
    "admin_remove_ship": "Admin — Players",
    "admin_config": "Admin — Config",
    "admin_config_shop": "Admin — Config",
    "admin_config_xp": "Admin — Config",
    "admin_spawn_bounty": "Admin — Bounties",
    "admin_clear_bounties": "Admin — Bounties",
    "admin_cooldown_reset": "Admin — Bounties",
    "admin_config_bounty": "Admin — Bounties",
    "admin_refresh_shop": "Admin — Bounties",
    "admin_combat_log": "Admin — Combat",
    "admin_duel": "Admin — Combat",
    "admin_event_create": "Admin — Events",
    "admin_event_edit": "Admin — Events",
    "admin_event_view": "Admin — Events",
    "admin_event_add_prize": "Admin — Events",
    "admin_event_remove_prize": "Admin — Events",
    "admin_event_start": "Admin — Events",
    "admin_event_end": "Admin — Events",
    "admin_event_delete": "Admin — Events",
    "admin_event_list": "Admin — Events",
    "admin_guild_stats": "Admin — Stats",
    "render_config": "Admin — Render",
    "render_cache_clear": "Admin — Render",
    "load_data": "Admin — Dev Tools",
    "reload_autocomplete": "Admin — Dev Tools",
    "force_reload_caches": "Admin — Dev Tools",
    "ping": "Admin — Health",
    "health": "Admin — Health",
    "scheduler_list": "Admin — Scheduler",
    "scheduler_view": "Admin — Scheduler",
    "scheduler_update": "Admin — Scheduler",
    "scheduler_delete": "Admin — Scheduler",
    "admin_reset_scheduler": "Admin — Scheduler",
    "admin_clear_scheduler": "Admin — Scheduler",
}

_ADMIN_CATEGORY_ORDER: list[str] = [
    "Admin — Setup",
    "Admin — Players",
    "Admin — Config",
    "Admin — Bounties",
    "Admin — Combat",
    "Admin — Events",
    "Admin — Stats",
    "Admin — Render",
    "Admin — Health",
    "Admin — Dev Tools",
    "Admin — Scheduler",
]

# Short descriptions for each admin category
_ADMIN_CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "Admin — Setup": "Initialize and uninstall the bot for this guild, sync notification roles",
    "Admin — Players": "Manage player stats, credits, items, and ships",
    "Admin — Config": "View and update guild configuration settings",
    "Admin — Bounties": "Spawn, clear, and configure bounty settings",
    "Admin — Combat": "Review player battles and manage pending duels",
    "Admin — Events": "Create, prize, start, end, and delete stat-race events",
    "Admin — Stats": "View guild-wide statistics and reports",
    "Admin — Render": "Configure Blender render settings and clear cache",
    "Admin — Health": "Health checks and latency probes",
    "Admin — Dev Tools": "Data loading and cache reload utilities",
    "Admin — Scheduler": "Manage APScheduler jobs",
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _is_admin_command(cmd: app_commands.Command) -> bool:
    """Return True if *cmd* should be considered admin-only.

    Detection order (per task spec):
    1. Has ``default_permissions`` set to a ``discord.Permissions`` object whose
       ``.administrator`` flag is True (set via ``@app_commands.default_permissions(administrator=True)``).
    2. Command name starts with an admin prefix.
    3. Command name is in the explicit admin-name set.
    4. The cog that owns the command is one of the known admin cogs.
    """
    # 1. Discord permission gate — administrator flag on default_permissions counts as admin-gated
    if cmd.default_permissions is not None and cmd.default_permissions.administrator:
        return True

    # 2. Name-prefix check
    if any(cmd.name.startswith(prefix) for prefix in _ADMIN_NAME_PREFIXES):
        return True

    # 3. Explicit name check
    if cmd.name in _ADMIN_COMMAND_NAMES:
        return True

    # 4. Cog origin check
    if cmd.binding is not None:
        cog_class_name = type(cmd.binding).__name__
        if cog_class_name in _ADMIN_COG_NAMES:
            return True

    return False


def _normalize_category(value: str) -> str:
    """Normalize a category name for case-insensitive lookup.

    Strips whitespace, converts to lowercase, and removes separators
    (underscores, spaces, dashes, em-dashes, ampersands) to allow
    flexible matching such as 'bounty_hunting' → 'Bounty Hunting'.
    """
    return value.lower().replace("_", "").replace(" ", "").replace("-", "").replace("—", "").replace("&", "").strip()


def _format_params(cmd: app_commands.Command) -> str:
    """Format command parameters into a human-readable string.

    Returns something like:
        Parameters:
          • user (required): The user to act on
          • credits (optional): Amount of credits
    or empty string if no parameters.
    """
    try:
        params = cmd.parameters
    except AttributeError:
        return ""

    if not params:
        return ""

    lines = ["**Parameters:**"]
    for param in params:
        try:
            name = param.name
            required = param.required
            description = param.description or "No description"
            req_label = "required" if required else "optional"
            lines.append(f"  • **{name}** ({req_label}): {description}")
        except AttributeError:
            continue

    return "\n".join(lines) if len(lines) > 1 else ""


def _build_overview_embed(
    categories: list[str],
    descriptions: dict[str, str],
    cmd_counts: dict[str, int],
    title: str,
    intro: str,
    footer: str,
    color: discord.Color,
) -> discord.Embed:
    """Build an overview embed listing categories with descriptions and command counts."""
    embed = discord.Embed(title=title, description=intro, color=color)

    for cat in categories:
        count = cmd_counts.get(cat, 0)
        if count == 0:
            continue
        desc = descriptions.get(cat, "")
        field_value = f"{desc}\n*{count} command{'s' if count != 1 else ''}*"
        embed.add_field(name=cat, value=field_value, inline=False)

    embed.set_footer(text=footer)
    return embed


def _build_detail_embed(
    category: str,
    description: str,
    cmds: list[app_commands.Command],
    color: discord.Color,
    emoji: str = "📖",
) -> discord.Embed:
    """Build a detail embed for a single category showing command descriptions and params."""
    embed = discord.Embed(
        title=f"{emoji} {category}",
        description=description or "No description available.",
        color=color,
    )

    if not cmds:
        embed.add_field(name="Commands", value="No commands available.", inline=False)
        return embed

    for cmd in sorted(cmds, key=lambda c: c.name):
        cmd_desc = cmd.description or "No description"
        param_str = _format_params(cmd)
        field_value = cmd_desc
        if param_str:
            field_value = f"{cmd_desc}\n{param_str}"
        # Truncate field_value to Discord's 1024 char embed field limit
        if len(field_value) > 1024:
            field_value = field_value[:1021] + "..."
        embed.add_field(name=f"/{cmd.name}", value=field_value, inline=False)

    return embed


# ---------------------------------------------------------------------------
# Autocomplete helpers (static lists — no live bot.tree filtering needed)
# ---------------------------------------------------------------------------


async def _user_category_autocomplete(
    _interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Return matching user-facing category names for autocomplete."""
    norm = current.lower()
    return [app_commands.Choice(name=cat, value=cat) for cat in _USER_CATEGORY_ORDER if norm in cat.lower()][:25]


async def _admin_category_autocomplete(
    _interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Return matching admin category names for autocomplete."""
    norm = current.lower()
    return [app_commands.Choice(name=cat, value=cat) for cat in _ADMIN_CATEGORY_ORDER if norm in cat.lower()][:25]


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class HelpCog(commands.Cog):
    """Provides /help and /admin_help command discovery."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        flogger.debug("HelpCog initialized")

    # No HTTP client needed — operates purely on bot.tree introspection

    # ------------------------------------------------------------------
    # /help [category]
    # ------------------------------------------------------------------

    @app_commands.command(name="help", description="Show available bot commands")
    @app_commands.describe(category="Category to drill into (leave blank for overview)")
    @app_commands.autocomplete(category=_user_category_autocomplete)
    async def help_cmd(self, interaction: discord.Interaction, category: str | None = None):
        """List user-facing slash commands. Without category shows overview; with category shows details."""
        flogger.info(f"/help invoked: guild={interaction.guild_id} user={interaction.user.id} category={category!r}")

        # Introspect all registered commands
        all_commands: list[app_commands.Command] = [
            cmd for cmd in self.bot.tree.get_commands() if isinstance(cmd, app_commands.Command)
        ]

        # Filter to user-facing commands only
        user_commands = [cmd for cmd in all_commands if not _is_admin_command(cmd)]

        # Exclude /help and /admin_help themselves
        user_commands = [cmd for cmd in user_commands if cmd.name not in ("help", "admin_help")]

        if category is None:
            # Overview mode: show all categories with counts
            categorised: dict[str, list[app_commands.Command]] = {}
            for cmd in user_commands:
                cat = _COMMAND_CATEGORIES.get(cmd.name)
                if cat:
                    categorised.setdefault(cat, []).append(cmd)

            cmd_counts = {cat: len(cmds) for cat, cmds in categorised.items()}

            embed = _build_overview_embed(
                categories=_USER_CATEGORY_ORDER,
                descriptions=_USER_CATEGORY_DESCRIPTIONS,
                cmd_counts=cmd_counts,
                title="📖 BountyBot Help",
                intro="Use `/help category:<name>` to see detailed command info for a category.",
                footer="Admins: use /admin_help to see admin commands",
                color=discord.Color.blurple(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            flogger.info(f"/help overview success: guild={interaction.guild_id} user={interaction.user.id}")

        else:
            # Detail mode: find the matching category (case-insensitive)
            norm_input = _normalize_category(category)
            matched_cat: str | None = None
            for cat in _USER_CATEGORY_ORDER:
                if _normalize_category(cat) == norm_input:
                    matched_cat = cat
                    break

            if matched_cat is None:
                available = ", ".join(_USER_CATEGORY_ORDER)
                await interaction.response.send_message(
                    f"❌ Category **'{category}'** not found.\nAvailable categories: {available}",
                    ephemeral=True,
                )
                return

            # Get commands for this category
            cat_cmds = [cmd for cmd in user_commands if _COMMAND_CATEGORIES.get(cmd.name) == matched_cat]
            desc = _USER_CATEGORY_DESCRIPTIONS.get(matched_cat, "")

            embed = _build_detail_embed(
                category=matched_cat,
                description=desc,
                cmds=cat_cmds,
                color=discord.Color.blurple(),
                emoji="📖",
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            flogger.info(
                f"/help detail success: guild={interaction.guild_id} user={interaction.user.id} "
                f"category={matched_cat!r} commands={len(cat_cmds)}"
            )

    # ------------------------------------------------------------------
    # /admin_help [category]
    # ------------------------------------------------------------------

    @app_commands.command(name="admin_help", description="Show admin-only bot commands")
    @app_commands.describe(category="Admin category to drill into (leave blank for overview)")
    @app_commands.autocomplete(category=_admin_category_autocomplete)
    @is_admin()
    # Cross-1 audit: /admin_help has ZERO HTTP calls in its command body — it only
    # introspects the bot's command tree synchronously and calls send_message (no defer).
    # The @is_admin() decorator makes one HTTP call for Bot-Admin users, but since
    # there is no subsequent async I/O in the handler, the total async budget used is
    # just the is_admin check itself.  Post-defer refactor is not warranted; keeping
    # the existing pattern with this documented rationale.
    async def admin_help_cmd(self, interaction: discord.Interaction, category: str | None = None):
        """List admin slash commands. Without category shows overview; with category shows details."""
        flogger.info(
            f"/admin_help invoked: guild={interaction.guild_id} user={interaction.user.id} category={category!r}"
        )

        # Introspect all registered commands
        all_commands: list[app_commands.Command] = [
            cmd for cmd in self.bot.tree.get_commands() if isinstance(cmd, app_commands.Command)
        ]

        # Filter to admin commands only
        admin_commands = [cmd for cmd in all_commands if _is_admin_command(cmd)]

        # Exclude /help and /admin_help themselves
        admin_commands = [cmd for cmd in admin_commands if cmd.name not in ("help", "admin_help")]

        if category is None:
            # Overview mode: show all admin categories with counts
            categorised: dict[str, list[app_commands.Command]] = {}
            for cmd in admin_commands:
                cat = _ADMIN_CATEGORY_MAPPING.get(cmd.name)
                if cat is None and cmd.binding is not None:
                    cog_name = type(cmd.binding).__name__
                    if "Scheduler" in cog_name:
                        cat = "Admin — Scheduler"
                    elif "Dev" in cog_name:
                        cat = "Admin — Dev Tools"
                    elif "Health" in cog_name:
                        cat = "Admin — Health"
                if cat:
                    categorised.setdefault(cat, []).append(cmd)

            cmd_counts = {cat: len(cmds) for cat, cmds in categorised.items()}

            embed = _build_overview_embed(
                categories=_ADMIN_CATEGORY_ORDER,
                descriptions=_ADMIN_CATEGORY_DESCRIPTIONS,
                cmd_counts=cmd_counts,
                title="🔐 Admin Help",
                intro="Use `/admin_help category:<name>` to see detailed info for an admin category.",
                footer="Admin access required for all commands listed here",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            flogger.info(f"/admin_help overview success: guild={interaction.guild_id} user={interaction.user.id}")

        else:
            # Detail mode: find the matching admin category (case-insensitive)
            norm_input = _normalize_category(category)
            matched_cat: str | None = None
            for cat in _ADMIN_CATEGORY_ORDER:
                if _normalize_category(cat) == norm_input:
                    matched_cat = cat
                    break

            if matched_cat is None:
                available = ", ".join(_ADMIN_CATEGORY_ORDER)
                await interaction.response.send_message(
                    f"❌ Admin category **'{category}'** not found.\nAvailable categories: {available}",
                    ephemeral=True,
                )
                return

            # Get commands for this category
            cat_cmds = []
            for cmd in admin_commands:
                cmd_cat = _ADMIN_CATEGORY_MAPPING.get(cmd.name)
                if cmd_cat is None and cmd.binding is not None:
                    cog_name = type(cmd.binding).__name__
                    if "Scheduler" in cog_name:
                        cmd_cat = "Admin — Scheduler"
                    elif "Dev" in cog_name:
                        cmd_cat = "Admin — Dev Tools"
                    elif "Health" in cog_name:
                        cmd_cat = "Admin — Health"
                if cmd_cat == matched_cat:
                    cat_cmds.append(cmd)

            desc = _ADMIN_CATEGORY_DESCRIPTIONS.get(matched_cat, "")

            embed = _build_detail_embed(
                category=matched_cat,
                description=desc,
                cmds=cat_cmds,
                color=discord.Color.red(),
                emoji="🔐",
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            flogger.info(
                f"/admin_help detail success: guild={interaction.guild_id} user={interaction.user.id} "
                f"category={matched_cat!r} commands={len(cat_cmds)}"
            )

    @help_cmd.error
    async def help_cmd_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /help", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    @admin_help_cmd.error
    async def admin_help_cmd_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /admin_help", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)


async def setup(bot: commands.Bot):
    flogger.debug("Setting up HelpCog...")
    await bot.add_cog(HelpCog(bot))
    flogger.info("HelpCog loaded")
