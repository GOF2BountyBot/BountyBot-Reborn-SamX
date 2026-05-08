import asyncio
import os

import discord
import httpx
from cogs._shared.http_error_handler import report_api_error
from cogs._shared.loadout_embed import build_loadout_embed, build_loadout_error_embed
from discord import app_commands
from discord.ext import commands
from shared import bblogger
from utils.autocomplete_utils import fuzzy_filter, normalize_for_search, resolve_system_name
from utils.timestamp_utils import iso_to_discord_ts

# Set up logger
flogger = bblogger.get_logger("discord-gateway-BountyCog")

# Define any environment variables or constants here
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"bountyCog loading with API_BASE_URL: {api_base}")

_VALID_DIVISIONS = ["bronze", "silver", "gold", "platinum"]

# Message shown when the guild hasn't been set up via /admin_setup
_GUILD_NOT_CONFIGURED_MSG = (
    "⚠️ This server hasn't been set up yet. An admin must run `/admin_setup` "
    "to initialize BountyBot before you can use this command."
)


def _is_guild_not_configured(exc: httpx.HTTPStatusError) -> bool:
    """Return True if the HTTPStatusError is a 'guild not configured' 400 response."""
    if exc.response.status_code != 400:
        return False
    try:
        detail = exc.response.json().get("detail", "")
        return "not configured" in detail.lower() or "admin_setup" in detail.lower()
    except Exception:  # pylint: disable=broad-exception-caught
        return False


class BountyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._systems: list[str] = []

        # Preload system data at startup
        bot.loop.create_task(self._preload_data())
        flogger.debug("BountyCog initialized")

    async def cog_unload(self):
        await self.http_client.aclose()

    async def _preload_data(self):
        """Preload star system names at startup for autocomplete (with retries)."""
        await self.bot.wait_until_ready()
        delays = [5, 10, 20, 40, 60]
        for attempt, delay in enumerate(delays, start=1):
            try:
                flogger.info("BountyCog: Starting preload of system data (attempt %d/%d)...", attempt, len(delays))
                resp = await self.http_client.get(f"{api_base}/about/categories/system/objects", timeout=10)
                resp.raise_for_status()
                systems = resp.json()
                self._systems = [s.get("name", "") for s in systems if s.get("name")]
                flogger.info("BountyCog: Preloaded %d system names", len(self._systems))
                return  # Success — exit
            except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as e:
                flogger.warning(
                    "BountyCog: Preload attempt %d/%d failed: %s — retrying in %ds", attempt, len(delays), e, delay
                )
                await asyncio.sleep(delay)
            except Exception as e:  # pylint: disable=broad-exception-caught
                flogger.warning("BountyCog: Unexpected error on preload attempt %d/%d: %s", attempt, len(delays), e)
                await asyncio.sleep(delay)
        flogger.error("BountyCog: All preload attempts exhausted. System autocomplete will be empty.")
        self._systems = []

    async def _get_player_id(self, user_id: int, guild_id: int, display_name: str | None = None) -> int | None:
        """Resolve a Discord user ID to a game player ID via the upsert endpoint.

        Re-raises httpx.HTTPStatusError for guild-not-configured responses so callers
        can surface a user-friendly message.
        """
        try:
            user_data = {
                "discord_id": user_id,
                "guild_id": guild_id,
                "discord_username": None,
                "display_name": display_name,
            }
            resp = await self.http_client.post(f"{api_base}/players/", json=user_data, timeout=5)
            resp.raise_for_status()
            return resp.json().get("id")
        except httpx.HTTPStatusError as e:
            if _is_guild_not_configured(e):
                raise
            return None
        except Exception:  # pylint: disable=broad-exception-caught
            return None

    # ------------------------------------------------------------------
    # Autocomplete
    # ------------------------------------------------------------------

    async def division_autocomplete(
        self,
        _interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for division selection."""
        norm_current = normalize_for_search(current)
        return [
            app_commands.Choice(name=div.title(), value=div)
            for div in _VALID_DIVISIONS
            if norm_current in normalize_for_search(div)
        ]

    async def system_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for star system names — includes ALL systems (game balance)."""
        return [app_commands.Choice(name=name, value=name) for name in fuzzy_filter(current, self._systems)]

    async def bounty_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Live autocomplete for active bounties — fetches current bounties."""
        try:
            resp = await self.http_client.get(
                f"{api_base}/bounties/",
                params={"guild_id": interaction.guild_id},
                timeout=5,
            )
            resp.raise_for_status()
            bounties = resp.json()
            norm_current = normalize_for_search(current)
            choices = []
            for b in bounties:
                label = (
                    f"{b['criminal_name']} ({b['division'].title()}, T{b.get('tech_level', '?')}) — {b['reward']:,}cr"
                )
                if norm_current in normalize_for_search(label):
                    choices.append(app_commands.Choice(name=label[:100], value=str(b["id"])))
            return choices[:25]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    # ------------------------------------------------------------------
    # /check <system>
    # ------------------------------------------------------------------

    @app_commands.command(name="check", description="Check a star system for a bounty target")
    @app_commands.describe(system="The star system name to check")
    @app_commands.autocomplete(system=system_autocomplete)
    async def check(self, interaction: discord.Interaction, system: str):
        """Check a system for bounties."""
        await interaction.response.defer(thinking=True)
        flogger.info(f"/check invoked: guild={interaction.guild_id} user={interaction.user.id} system={system}")

        # Only resolve if we have systems loaded — if preload failed, pass through
        # and let bot-core return 404 for invalid names
        if self._systems:
            resolved = resolve_system_name(system, self._systems)
            if resolved is None:
                await interaction.followup.send(
                    f"❌ Unknown system `{system}`. Use autocomplete or check the spelling.",
                    ephemeral=True,
                )
                return
            system = resolved
        else:
            flogger.debug("/check: _systems not loaded, passing typed value through to bot-core: %s", system)

        try:
            player_id = await self._get_player_id(
                interaction.user.id,
                interaction.guild_id,
                display_name=getattr(interaction.user, "display_name", None),
            )
            if player_id is None:
                await interaction.followup.send("❌ Player not found. Use `/profile` first.", ephemeral=True)
                return

            resp = await self.http_client.post(
                f"{api_base}/bounties/check",
                json={"player_id": player_id, "system_name": system},
                params={"guild_id": interaction.guild_id},
                timeout=10,
            )

            if resp.status_code == 429:
                await interaction.followup.send(
                    "⏱️ You are on cooldown. Please wait before checking again.",
                    ephemeral=True,
                )
                return

            resp.raise_for_status()
            data = resp.json()
            result = data.get("result", "")
            message = data.get("message", "")
            outcomes = data.get("outcomes") or []

            # Handle on_cooldown as ephemeral message (not an embed)
            # On_cooldown is always a single-outcome top-level result.
            if result == "on_cooldown":
                cooldown_until = data.get("cooldown_until")
                if cooldown_until:
                    await interaction.followup.send(
                        f"⏱️ You can check again <t:{cooldown_until}:R>",
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(f"⏱️ {message}", ephemeral=True)
                return

            # Build the user-facing reply.
            #   0 outcomes (defensive — bot-core always emits at least one) → fall
            #     back to top-level fields for the embed.
            #   1 outcome  → single-bounty embed (legacy behaviour).
            #   2+ outcomes → consolidated multi-bounty embed listing each
            #     bounty's per-bounty result (B.12 multi-bounty fix).
            if len(outcomes) > 1:
                embed = self._build_multi_check_embed(system, outcomes)
            else:
                # Single-outcome path: prefer outcomes[0] (new shape) but fall
                # back to top-level fields for legacy bot-core responses.
                outcome_data = outcomes[0] if outcomes else data
                outcome_data = dict(outcome_data)
                outcome_data["system_name"] = system
                embed = self._build_check_embed(outcome_data)

            await interaction.followup.send(embed=embed)
            flogger.info(
                f"/check success: guild={interaction.guild_id} user={interaction.user.id}"
                f" system={system} result={result} result_count={len(outcomes)}"
            )

            # If the player's tier changed on ANY outcome, update their Discord role.
            new_tier = data.get("new_tier") or next(
                (o.get("new_tier") for o in outcomes if o.get("new_tier")),
                None,
            )
            if new_tier:
                try:
                    config_resp = await self.http_client.get(
                        f"{api_base}/config/guild/{interaction.guild_id}", timeout=5
                    )
                    config_resp.raise_for_status()
                    config = config_resp.json()
                    guild = interaction.guild
                    new_role_id = config.get(f"{new_tier.lower()}_role_id")
                    if new_role_id:
                        new_role = guild.get_role(new_role_id)
                        if new_role and new_role not in interaction.user.roles:
                            # Remove old tier roles, add new one
                            old_tier_roles = []
                            for tier_key in ("bronze_role_id", "silver_role_id", "gold_role_id", "platinum_role_id"):
                                rid = config.get(tier_key)
                                if rid:
                                    old_role = guild.get_role(rid)
                                    if old_role and old_role in interaction.user.roles and old_role != new_role:
                                        old_tier_roles.append(old_role)
                            if old_tier_roles:
                                await interaction.user.remove_roles(*old_tier_roles, reason="BountyBot tier change")
                            await interaction.user.add_roles(new_role, reason=f"BountyBot promoted to {new_tier}")
                            flogger.info(
                                f"Updated tier role for user {interaction.user.id}: {new_tier} "
                                f"in guild {interaction.guild_id}"
                            )
                except Exception as e:  # pylint: disable=broad-exception-caught
                    flogger.warning(f"Failed to update tier role: {e}")

        except httpx.HTTPStatusError as e:
            if _is_guild_not_configured(e):
                await interaction.followup.send(_GUILD_NOT_CONFIGURED_MSG, ephemeral=True)
            elif e.response.status_code == 429:
                await interaction.followup.send(
                    "⏱️ You are on cooldown. Please wait before checking again.",
                    ephemeral=True,
                )
            else:
                flogger.error(
                    f"/check API error: guild={interaction.guild_id} user={interaction.user.id}"
                    f" status={e.response.status_code}"
                )
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"/check error: guild={interaction.guild_id} user={interaction.user.id} error={e}")
            await interaction.followup.send("⚠️ An error occurred while checking the system.", ephemeral=True)

    def _summarize_outcome_line(self, outcome: dict) -> tuple[str, str]:
        """Build (title, value) for one bounty's row inside a multi-bounty embed.

        Used by :meth:`_build_multi_check_embed` to render N independent
        per-bounty results in a single consolidated reply.
        """
        result = outcome.get("result", "")
        criminal_name = outcome.get("criminal_name") or f"Bounty #{outcome.get('bounty_id')}"
        title_prefix = f"🎯 {criminal_name}"

        if result == "correct":
            combat_won = outcome.get("combat_won")
            reward = outcome.get("reward", 0) or 0
            total_reward = outcome.get("total_reward", reward) or reward
            bonus_won = outcome.get("bonus_won", False)

            if combat_won is False:
                # Silver+ combat loss — checks reset for this bounty
                return (
                    f"💀 {criminal_name}",
                    "Combat loss — system checks reset for this bounty.",
                )
            if bonus_won:
                return (
                    f"{title_prefix} — Captured!",
                    f"💰 **{total_reward:,}** credits (2× combat bonus!)",
                )
            return (
                f"{title_prefix} — Captured!",
                f"💰 **{reward:,}** credits",
            )
        if result == "incorrect":
            recently_spotted = outcome.get("recently_spotted", False)
            if recently_spotted:
                return (
                    f"👀 {criminal_name}",
                    "Recently spotted here — they're close!",
                )
            return (
                f"❌ {criminal_name}",
                "System checked — bounty not here.",
            )
        if result == "already_checked":
            return (
                f"🔁 {criminal_name}",
                "System was already checked.",
            )
        # not_found / unknown — should not normally appear inside outcomes when len > 1
        return (
            title_prefix,
            outcome.get("message") or "No bounty here.",
        )

    def _build_multi_check_embed(self, system: str, outcomes: list[dict]) -> discord.Embed:
        """Build a consolidated multi-bounty embed for /check (B.12 fix).

        Picks an aggregate color reflecting the most "newsworthy" outcome —
        green if any capture, red if any combat loss, otherwise default.
        Each outcome contributes one field to the embed.
        """
        any_capture = any(o.get("result") == "correct" and o.get("combat_won") is not False for o in outcomes)
        any_loss = any(o.get("result") == "correct" and o.get("combat_won") is False for o in outcomes)

        if any_capture:
            color = discord.Color.green()
            title = "🎯 Multiple Bounties Updated — Capture!"
        elif any_loss:
            color = discord.Color.dark_red()
            title = "💥 Multiple Bounties Updated"
        else:
            color = discord.Color.blue()
            title = "🗺️ Multiple Bounties Updated"

        description = f"**{system}** affected **{len(outcomes)}** active bounty(s) in your division."
        embed = discord.Embed(title=title, description=description, color=color)

        for outcome in outcomes:
            field_name, field_value = self._summarize_outcome_line(outcome)
            # Discord embed field limits: name ≤256, value ≤1024.
            embed.add_field(
                name=field_name[:256],
                value=field_value[:1024] if field_value else "—",
                inline=False,
            )

        # Combat summaries: append once per outcome that had combat.
        for outcome in outcomes:
            combat = outcome.get("combat_result")
            if combat:
                criminal_name = outcome.get("criminal_name") or f"Bounty #{outcome.get('bounty_id')}"
                summary = self._format_combat_summary(combat)
                embed.add_field(
                    name=f"⚔️ Combat — {criminal_name}",
                    value=summary[:1024],
                    inline=False,
                )

        return embed

    @staticmethod
    def _format_combat_summary(combat: dict) -> str:
        """Format combat results into a readable embed field."""
        s1 = combat.get("ship1_stats", {})
        s2 = combat.get("ship2_stats", {})

        lines = []
        # Player stats
        lines.append(f"**Your Ship** ({s1.get('ship_name', '?')})")
        lines.append(f"HP: {s1.get('raw_hp', 0)} → {s1.get('varied_hp', 0)} | DPS: {s1.get('raw_dps', 0):.1f}")
        ttk1 = s1.get("ttk")
        lines.append(f"Time to Kill: {f'{ttk1:.1f}s' if ttk1 is not None else '∞'}")
        lines.append("")
        # Criminal stats
        lines.append(f"**Criminal Ship** ({s2.get('ship_name', '?')})")
        lines.append(f"HP: {s2.get('raw_hp', 0)} → {s2.get('varied_hp', 0)} | DPS: {s2.get('raw_dps', 0):.1f}")
        ttk2 = s2.get("ttk")
        lines.append(f"Time to Kill: {f'{ttk2:.1f}s' if ttk2 is not None else '∞'}")

        if combat.get("is_stalemate"):
            lines.append("\n**Result:** Stalemate")

        return "\n".join(lines)

    def _build_check_embed(self, data: dict) -> discord.Embed:
        """Build an embed for the /check command based on the full response data dict."""
        result = data.get("result", "")
        system = data.get("system_name", "")
        message = data.get("message", "")

        if result == "correct":
            combat_won = data.get("combat_won")
            criminal_name = data.get("criminal_name", "Unknown")

            if combat_won is False:
                # Criminal escaped after combat loss — checks have been reset
                embed = discord.Embed(
                    title="💀 Combat Defeat!",
                    description=(
                        f"**{criminal_name}** defeated you and escaped!\n"
                        "All system checks have been reset — the hunt continues!"
                    ),
                    color=discord.Color.dark_red(),
                )
                if message:
                    embed.add_field(name="Result", value=message, inline=False)
                combat = data.get("combat_result")
                if combat:
                    embed.add_field(
                        name="⚔️ Combat Summary",
                        value=self._format_combat_summary(combat),
                        inline=False,
                    )
            else:
                # Successful capture (bronze with/without bonus, or silver/gold/platinum win)
                embed = discord.Embed(
                    title="🎯 Bounty Captured!",
                    description=f"**{criminal_name}** has been captured!",
                    color=discord.Color.green(),
                )
                reward = data.get("reward", 0)
                total_reward = data.get("total_reward", reward)
                bonus_won = data.get("bonus_won", False)

                if bonus_won:
                    embed.add_field(
                        name="💰 Reward",
                        value=f"**{total_reward:,}** credits (2× combat bonus!)",
                        inline=False,
                    )
                else:
                    embed.add_field(name="💰 Reward", value=f"**{reward:,}** credits", inline=False)

                # Show combat stats if available
                combat = data.get("combat_result")
                if combat:
                    embed.add_field(
                        name="⚔️ Combat Summary",
                        value=self._format_combat_summary(combat),
                        inline=False,
                    )
                if message:
                    embed.add_field(name="Result", value=message, inline=False)
        elif result == "captured":
            # Backward-compatible handler: treated same as correct+combat_won=True (Bronze capture)
            embed = discord.Embed(
                title="🎯 Bounty Captured!",
                description=f"**{data.get('criminal_name', 'Unknown')}** has been captured!",
                color=discord.Color.green(),
            )
            reward = data.get("reward", 0)
            total_reward = data.get("total_reward", reward)
            bonus_won = data.get("bonus_won", False)

            if bonus_won:
                embed.add_field(
                    name="💰 Reward",
                    value=f"**{total_reward:,}** credits (2× combat bonus!)",
                    inline=False,
                )
            else:
                embed.add_field(name="💰 Reward", value=f"**{reward:,}** credits", inline=False)

            combat = data.get("combat_result")
            if combat:
                embed.add_field(
                    name="⚔️ Combat Summary",
                    value=self._format_combat_summary(combat),
                    inline=False,
                )
        elif result == "combat_win":
            # Backward-compatible handler: treated same as correct+combat_won=True (Silver+ capture)
            embed = discord.Embed(
                title="⚔️ Combat Victory!",
                description=f"You defeated **{data.get('criminal_name', 'Unknown')}** in combat!",
                color=discord.Color.green(),
            )
            reward = data.get("reward", 0)
            embed.add_field(name="💰 Reward", value=f"**{reward:,}** credits", inline=False)
            combat = data.get("combat_result")
            if combat:
                embed.add_field(
                    name="⚔️ Combat Summary",
                    value=self._format_combat_summary(combat),
                    inline=False,
                )
        elif result == "combat_loss":
            # Kept for backward compatibility (not returned by current bot-core but may exist in future)
            embed = discord.Embed(
                title="💀 Combat Defeat!",
                description=(
                    f"**{data.get('criminal_name', 'Unknown')}** defeated you and escaped!\n"
                    "All system checks have been reset — the hunt continues!"
                ),
                color=discord.Color.dark_red(),
            )
            combat = data.get("combat_result")
            if combat:
                embed.add_field(
                    name="⚔️ Combat Summary",
                    value=self._format_combat_summary(combat),
                    inline=False,
                )
        elif result == "incorrect":
            recently_spotted = data.get("recently_spotted", False)
            if recently_spotted:
                embed = discord.Embed(
                    title="👀 Recently Spotted!",
                    description=f"**{system}** — The target was recently here. They're close!",
                    color=discord.Color.orange(),
                )
            else:
                embed = discord.Embed(
                    title="❌ System Checked",
                    description=f"**{system}** — System checked, bounty not here.",
                    color=discord.Color.red(),
                )
            if message:
                embed.add_field(name="Intel", value=message, inline=False)
        elif result == "already_checked":
            embed = discord.Embed(
                title="🔁 Already Checked",
                description=f"**{system}** — This system has already been checked.",
                color=discord.Color.yellow(),
            )
            if message:
                embed.add_field(name="Note", value=message, inline=False)
        else:  # not_found or unknown
            embed = discord.Embed(
                title="🔍 No Bounty",
                description=f"**{system}** — No bounty found at this system.",
                color=discord.Color.orange(),
            )
            if message:
                embed.add_field(name="Note", value=message, inline=False)
        return embed

    @check.error
    async def check_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /check", exc_info=error)
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)
            else:
                await interaction.followup.send("⚠️ An error occurred.", ephemeral=True)
        except Exception:  # pylint: disable=broad-exception-caught
            pass  # Interaction fully expired, nothing we can do

    # ------------------------------------------------------------------
    # /bounties [division]
    # ------------------------------------------------------------------

    @app_commands.command(name="bounties", description="List active bounties in this guild")
    @app_commands.describe(division="Filter by division (bronze, silver, gold)")
    @app_commands.autocomplete(division=division_autocomplete)
    async def bounties(
        self,
        interaction: discord.Interaction,
        division: str | None = None,
    ):
        """List active bounties."""
        await interaction.response.defer(thinking=True)
        flogger.info(f"/bounties invoked: guild={interaction.guild_id} user={interaction.user.id} division={division}")

        try:
            params: dict = {"guild_id": interaction.guild_id}
            if division:
                params["division"] = division

            resp = await self.http_client.get(
                f"{api_base}/bounties/",
                params=params,
                timeout=10,
            )
            resp.raise_for_status()
            bounty_list = resp.json()

            if not bounty_list:
                title = "📋 Active Bounties"
                if division:
                    title += f" — {division.title()}"
                embed = discord.Embed(
                    title=title,
                    description="No active bounties at this time.",
                    color=discord.Color.light_grey(),
                )
                await interaction.followup.send(embed=embed)
                return

            title = "📋 Active Bounties"
            if division:
                title += f" — {division.title()}"

            embed = discord.Embed(
                title=title,
                description=f"**{len(bounty_list)}** active bounty(s)",
                color=discord.Color.blue(),
            )

            for bounty in bounty_list:
                # Only count systems actually checked (value != -1 means checked by a player)
                systems_checked = sum(1 for v in bounty.get("checked", {}).values() if v != -1)
                total_systems = len(bounty.get("route", []))
                end_time = bounty.get("end_time")
                time_str = ""
                if end_time:
                    time_str = f" | Expires {iso_to_discord_ts(end_time, 'R')}"

                faction = bounty.get("criminal_faction") or ""
                faction_str = f" ({faction})" if faction else ""

                embed.add_field(
                    name=f"🎯 {bounty['criminal_name']}{faction_str} — {bounty['division'].title()}",
                    value=(
                        f"💰 Reward: **{bounty['reward']:,}** cr"
                        f" (+{bounty['reward_per_sys']:,}/sys)\n"
                        f"🗺️ Systems: {systems_checked}/{total_systems} checked"
                        f"{time_str}\n"
                        f"🆔 Bounty ID: {bounty['id']}"
                    ),
                    inline=False,
                )

            embed.set_footer(text="Use /route <bounty_id> to see the route")
            await interaction.followup.send(embed=embed)
            flogger.info(
                f"/bounties success: guild={interaction.guild_id} user={interaction.user.id} count={len(bounty_list)}"
            )

        except httpx.HTTPStatusError as e:
            if _is_guild_not_configured(e):
                await interaction.followup.send(_GUILD_NOT_CONFIGURED_MSG, ephemeral=True)
            else:
                flogger.error(
                    f"/bounties API error: guild={interaction.guild_id} user={interaction.user.id}"
                    f" status={e.response.status_code}"
                )
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"/bounties error: guild={interaction.guild_id} user={interaction.user.id} error={e}")
            await interaction.followup.send("⚠️ An error occurred while fetching bounties.", ephemeral=True)

    @bounties.error
    async def bounties_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /bounties", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    # ------------------------------------------------------------------
    # /route <bounty>
    # ------------------------------------------------------------------

    @app_commands.command(name="route", description="Show the route for a bounty")
    @app_commands.describe(bounty="Select a bounty or enter bounty ID")
    @app_commands.autocomplete(bounty=bounty_autocomplete)
    async def route(self, interaction: discord.Interaction, bounty: str):
        """Show bounty route."""
        await interaction.response.defer(thinking=True)
        flogger.info(f"/route invoked: guild={interaction.guild_id} user={interaction.user.id} bounty={bounty}")

        try:
            bounty_id = int(bounty)
        except ValueError:
            await interaction.followup.send(
                "❌ Invalid bounty selection. Please select from the dropdown or enter a numeric ID.",
                ephemeral=True,
            )
            return

        try:
            resp = await self.http_client.get(
                f"{api_base}/bounties/{bounty_id}/route",
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            criminal_name = data.get("criminal_name", "Unknown")
            route_systems = data.get("route", [])
            checked = data.get("checked", {})
            status = data.get("status", "active")
            division = data.get("division") or ""
            division_str = f" | Tier: **{division.title()}**" if division else ""

            embed = discord.Embed(
                title=f"🗺️ Route — {criminal_name}",
                description=f"Bounty #{bounty_id} | Status: **{status.title()}**{division_str}",
                color=discord.Color.blurple(),
            )

            if not route_systems:
                embed.add_field(name="Route", value="No systems in route.", inline=False)
            else:
                # B.24: use server-computed 3-state system_statuses for visual distinction
                system_statuses = data.get("system_statuses") or {}
                route_lines = []
                for i, system_name in enumerate(route_systems, start=1):
                    status = system_statuses.get(system_name)
                    if status == "recently_spotted":
                        route_lines.append(f"{i}. **~~{system_name}~~** 🔍")
                    elif status in ("checked", "found"):
                        route_lines.append(f"{i}. ~~{system_name}~~ ✅")
                    else:
                        route_lines.append(f"{i}. {system_name}")
                embed.add_field(
                    name=f"Systems ({len(route_systems)} total)",
                    value="\n".join(route_lines),
                    inline=False,
                )

            # Only count systems actually checked (value != -1 means checked by a player)
            systems_checked = sum(1 for v in checked.values() if v != -1)
            embed.set_footer(text=f"{systems_checked}/{len(route_systems)} systems checked")

            await interaction.followup.send(embed=embed)
            flogger.info(
                f"/route success: guild={interaction.guild_id} user={interaction.user.id}"
                f" bounty_id={bounty_id} systems={len(route_systems)}"
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                await interaction.followup.send("❌ Bounty not found.", ephemeral=True)
            else:
                flogger.error(
                    f"/route API error: guild={interaction.guild_id} user={interaction.user.id}"
                    f" bounty_id={bounty_id} status={e.response.status_code}"
                )
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"/route error: guild={interaction.guild_id} user={interaction.user.id} bounty_id={bounty_id} error={e}"
            )
            await interaction.followup.send("⚠️ An error occurred while fetching the route.", ephemeral=True)

    @route.error
    async def route_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /route", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    # ------------------------------------------------------------------
    # /criminal-loadout <bounty>
    # ------------------------------------------------------------------

    @app_commands.command(name="criminal-loadout", description="Show the criminal's ship loadout for a bounty")
    @app_commands.describe(bounty="Select a bounty or enter bounty ID")
    @app_commands.autocomplete(bounty=bounty_autocomplete)
    async def criminal_loadout(self, interaction: discord.Interaction, bounty: str):
        """Show criminal loadout using the shared embed builder."""
        await interaction.response.defer(thinking=True)  # always public
        flogger.info(
            f"/criminal-loadout invoked: guild={interaction.guild_id} user={interaction.user.id} bounty={bounty}"
        )

        try:
            bounty_id = int(bounty)
        except ValueError:
            await interaction.followup.send(
                "❌ Invalid bounty selection. Please select from the dropdown or enter a numeric ID.",
                ephemeral=True,
            )
            return

        try:
            resp = await self.http_client.get(
                f"{api_base}/bounties/{bounty_id}/loadout",
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            # Error path (e.g. "Criminal ship data unavailable") — always ephemeral.
            if data.get("message"):
                embed = build_loadout_error_embed(
                    title=f"Loadout — {data.get('subject_name', 'Unknown')}",
                    description=data["message"],
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Criminal path always public; cargo header always shown (viewer_is_owner_or_admin=True).
            embed = build_loadout_embed(data, viewer_is_owner_or_admin=True)
            await interaction.followup.send(embed=embed)
            flogger.info(
                f"/criminal-loadout success: guild={interaction.guild_id} user={interaction.user.id}"
                f" bounty_id={bounty_id} criminal={data.get('subject_name')}"
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                await interaction.followup.send("❌ Bounty not found.", ephemeral=True)
            else:
                flogger.error(
                    f"/criminal-loadout API error: guild={interaction.guild_id} user={interaction.user.id}"
                    f" bounty_id={bounty_id} status={e.response.status_code}"
                )
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"/criminal-loadout error: guild={interaction.guild_id} user={interaction.user.id}"
                f" bounty_id={bounty_id} error={e}"
            )
            await interaction.followup.send("⚠️ An error occurred while fetching the loadout.", ephemeral=True)

    @criminal_loadout.error
    async def criminal_loadout_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        flogger.exception("Error in /criminal-loadout", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)


async def setup(bot: commands.Bot):
    flogger.debug("Setting up BountyCog...")
    await bot.add_cog(BountyCog(bot))
    flogger.info("BountyCog loaded")
