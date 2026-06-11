import asyncio
import os

import discord
import httpx
from cogs._shared.autocomplete_cache import AutocompleteCache
from cogs._shared.http_error_handler import report_api_error
from cogs._shared.loadout_embed import build_loadout_embed, build_loadout_error_embed
from discord import app_commands
from discord.ext import commands
from shared import bblogger
from utils.autocomplete_utils import fuzzy_filter, normalize_for_search, resolve_system_name
from utils.timestamp_utils import iso_to_discord_ts

from utils import autocomplete_state

# Set up logger
flogger = bblogger.get_logger("discord-gateway-BountyCog")

# Define any environment variables or constants here
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"bountyCog loading with API_BASE_URL: {api_base}")

_VALID_DIVISIONS = ["bronze", "silver", "gold", "platinum"]

# Canonical tier color palette (matches OPEN_ITEMS.md Appendix A ENH-02)
TIER_COLORS: dict[str, int] = {
    "bronze": 0xCD7F32,  # 13467442
    "silver": 0xC0C0C0,  # 12632256
    "gold": 0xFFD700,  # 16766720
    "platinum": 0xE5E4E2,  # 15066082
}

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
        # Static catalog: star system names — TTL=None (never expires).
        # SELF-HEAL (kills the D-010 class bug): a refresh_fn re-runs the same per-key
        # loader the preload uses, so a clear() (from /reload_autocomplete) lazily
        # re-fills on the next get/get_with_timeout instead of staying empty forever.
        self._systems_cache: AutocompleteCache[str, list[str]] = AutocompleteCache(
            ttl_seconds=None,
            refresh_fn=self._fetch_systems,
            name="bounty-systems",
        )
        # Bounty cache: keyed by guild_id (int), stores list of bounty dicts, TTL=1200s (20 min)
        # Dead-man switch only — periodic bounty_cache_refresh job resets TTL every 10 min
        self._bounty_cache: AutocompleteCache[int, list[dict]] = AutocompleteCache(
            ttl_seconds=1200.0,
            refresh_fn=self._fetch_bounties,
            name="bountyCog-bounties",
        )

        # Preload system data at startup
        bot.loop.create_task(self._preload_data())
        flogger.debug("BountyCog initialized")

    async def cog_unload(self):
        await self.http_client.aclose()

    async def _fetch_systems(self, _key: str) -> list[str]:
        """Refresh the star-system catalog. Reused as _systems_cache.refresh_fn AND
        by _preload_data, so a cleared cache self-heals on the next get().

        Raises on HTTP error so AutocompleteCache applies its stale-on-error policy.
        """
        resp = await self.http_client.get(f"{api_base}/about/categories/system/objects", timeout=10)
        resp.raise_for_status()
        systems = resp.json()
        return [s.get("name", "") for s in systems if s.get("name")]

    async def _preload_data(self):
        """Preload star system names at startup for autocomplete (with retries)."""
        await self.bot.wait_until_ready()
        delays = [5, 10, 20, 40, 60]
        for attempt, delay in enumerate(delays, start=1):
            try:
                flogger.info("BountyCog: Starting preload of system data (attempt %d/%d)...", attempt, len(delays))
                # Use the cache's own loader so preload and self-heal share one code path.
                system_names = await self._fetch_systems("all")
                self._systems_cache.set("all", system_names)
                flogger.info("BountyCog: Preloaded %d system names", len(system_names))
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
        self._systems_cache.set("all", [])

    async def _fetch_bounties(self, key: int) -> list[dict]:
        """Fetch all active bounties for a guild from bot-core. Called by _bounty_cache on miss/expiry.

        Args:
            key: guild_id (int).

        Returns:
            List of bounty dicts from GET /api/v1/bounties/?guild_id=<guild_id>.

        Phase 7: Pre-computes ``_norm`` on each bounty dict at fill time so the
        hot-path autocomplete scan never calls ``normalize_for_search`` per bounty.
        """
        try:
            resp = await self.http_client.get(
                f"{api_base}/bounties/",
                params={"guild_id": key},
                timeout=5,
            )
            if resp.status_code != 200:
                return []
            bounties = resp.json()
            # Pre-compute _norm at fill time — hot path uses pre-computed value.
            for b in bounties:
                label = (
                    f"{b.get('criminal_name', '')} "
                    f"({b.get('division', '').title()}, T{b.get('tech_level', '?')}) "
                    f"— {b.get('reward', 0):,}cr"
                )
                b["_norm"] = normalize_for_search(label)
            return bounties
        except Exception:  # pylint: disable=broad-exception-caught
            return []

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

    async def system_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for star system names — includes ALL systems (game balance)."""
        # Self-heal: peek first; on a miss (e.g. right after /reload_autocomplete cleared
        # the cache) cold-fill via the refresh_fn within the 1.0s budget so the dropdown
        # is never permanently empty (kills the D-010 class bug).
        systems = self._systems_cache.peek("all")
        if systems is None:
            try:
                systems = await self._systems_cache.get_with_timeout("all", timeout=1.0)
            except Exception:  # pylint: disable=broad-exception-caught
                systems = None
        return [app_commands.Choice(name=name, value=name) for name in fuzzy_filter(current, systems or [])]

    async def bounty_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Zero-HTTP autocomplete for active bounties.

        Phase 6: Reads from _bounty_cache with peek() — no HTTP call per keystroke.
        On cold miss, schedules a background refresh and returns [] immediately.

        Player tier for filtering is read from autocomplete_state.player_cache (peek).
        If player cache misses, falls back to showing ALL bounties (graceful degradation
        matching existing behaviour).

        When bounty_spawn_executor pushes to the gateway, _bounty_cache.set(guild_id, bounties)
        is called so the next keystroke is served from cache.
        """
        try:
            guild_id = interaction.guild_id
            user_id = interaction.user.id

            # HOT PATH: peek bounty cache — no HTTP. On cold miss, cold-fill the
            # PRIMARY gate within the 1.0s budget so the 0th keystroke is never empty.
            bounties = self._bounty_cache.peek(guild_id)
            if bounties is None:
                bounties = await self._bounty_cache.get_with_timeout(guild_id, timeout=1.0)
            if bounties is None:
                return []

            # Attempt tier filtering from player cache — graceful degradation on miss.
            # THIRD-GATE RULE: the player tier-filter stays peek-only (degrade on miss);
            # the budget is already spent on the primary bounty gate.
            player_tier: str | None = None
            if autocomplete_state.player_cache is not None:
                player_entry = autocomplete_state.player_cache.peek((guild_id, user_id))
                if player_entry is not None:
                    tier = player_entry.get("tier", "")
                    if tier:
                        player_tier = tier.lower()

            norm_current = normalize_for_search(current)
            choices = []
            for b in bounties:
                # Filter by player's tier if we know it (graceful degradation if we don't)
                if player_tier and b.get("division", "").lower() != player_tier:
                    continue
                label = (
                    f"{b['criminal_name']} ({b['division'].title()}, T{b.get('tech_level', '?')}) — {b['reward']:,}cr"
                )
                # Phase 7: use pre-computed _norm; fall back to on-the-fly for bounties
                # pushed via internal endpoint before this phase (old shape).
                norm_label = b.get("_norm") or normalize_for_search(label)
                if norm_current in norm_label:
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
        _systems_list = self._systems_cache.peek("all") or []
        if _systems_list:
            resolved = resolve_system_name(system, _systems_list)
            if resolved is None:
                await interaction.followup.send(
                    f"❌ Unknown system `{system}`. Use autocomplete or check the spelling.",
                    ephemeral=True,
                )
                return
            system = resolved
        else:
            flogger.debug("/check: systems cache empty, passing typed value through to bot-core: %s", system)

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
                timeout=20,
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
                await interaction.followup.send(embed=embed)
            else:
                # Single-outcome path: prefer outcomes[0] (new shape) but fall
                # back to top-level fields for legacy bot-core responses.
                outcome_data = outcomes[0] if outcomes else data
                outcome_data = dict(outcome_data)
                outcome_data["system_name"] = system
                # Inject winner name so the capture embed can show "Claimed by".
                outcome_data["winner_name"] = interaction.user.display_name or str(interaction.user)
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
                # Silver+ combat loss or stalemate — checks reset for this bounty
                if (outcome.get("combat_result") or {}).get("is_stalemate"):
                    return (
                        f"⚔️ {criminal_name}",
                        "Stalemate — criminal escaped; system checks reset for this bounty.",
                    )
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

        # Combat summaries and payout breakdowns: append once per outcome that had combat/capture.
        for outcome in outcomes:
            criminal_name = outcome.get("criminal_name") or f"Bounty #{outcome.get('bounty_id')}"
            combat = outcome.get("combat_result")
            if combat:
                summary = self._format_combat_summary(combat, criminal_name=criminal_name)
                embed.add_field(
                    name=f"⚔️ Combat — {criminal_name}",
                    value=summary[:1024],
                    inline=False,
                )

            # Payout breakdown for capture outcomes (result=correct, not a combat loss).
            if outcome.get("result") == "correct" and outcome.get("combat_won") is not False:
                breakdown = outcome.get("payout_breakdown") or []
                if breakdown:
                    breakdown_sorted = sorted(breakdown, key=lambda x: x.get("amount", 0), reverse=True)
                    lines = []
                    for entry in breakdown_sorted:
                        icon = "🏆" if entry.get("role") == "capture claim" else "🔍"
                        name = entry.get("player_display_name", "Unknown")
                        role = entry.get("role", "")
                        amount = entry.get("amount", 0)
                        lines.append(f"{icon} {name} — {role} — {amount:,} cr")
                    embed.add_field(
                        name=f"💰 Payout — {criminal_name}",
                        value="\n".join(lines)[:1024],
                        inline=False,
                    )

        return embed

    @staticmethod
    def _format_combat_summary(
        combat: dict,
        *,
        combat_won: bool | None = None,  # retained for API compat — NOT used to drive outcome header (CI-12)
        criminal_name: str | None = None,
    ) -> str:
        """Format actual after-action combat results into a readable embed field.

        Uses the tick-resolver summary (combatants block with final HP, damage dealt,
        and accuracy) when available.  Falls back gracefully when summary data is
        absent (e.g. pre-existing logs from before the tick resolver was deployed).

        Args:
            combat: The combat_result dict from the API response.
            combat_won: Retained for API compatibility — ignored for the outcome header
                        (CI-12 fix).  Outcome is always derived from actual hull data.
            criminal_name: Display name of the criminal (passed in by callers that have
                           it; defaults to "Criminal" when absent).

        The field value is truncated to 1024 characters to stay within Discord limits.

        Layout (compact worded format, owner-approved):
            ⚔️ Combat vs {criminal_name} — {Victory|Defeat|Stalemate} in {duration}s

            You ({player_ship}) — {survived|destroyed}
              Shield {s} · Armour {a} · Hull {h}  ·  dealt {dmg} · {acc}% acc ({hits}/{fired})

            {criminal_name} ({criminal_ship}) — {survived|destroyed}
              Shield {s} · Armour {a} · Hull {h}  ·  dealt {dmg} · {acc}% acc ({hits}/{fired})
        """
        combatants = (combat.get("combatants") or {}) if combat else {}
        c1 = combatants.get("1", {}) or {}
        c2 = combatants.get("2", {}) or {}

        # Duration / outcome — derived from ACTUAL hull data, not the combat_won flag.
        duration_s: float | None = combat.get("duration_s") if combat else None
        is_stalemate = combat.get("is_stalemate", False) if combat else False
        outcome_field: str | None = combat.get("outcome") if combat else None

        # Resolve per-combatant survival from final hull (CI-12 fix).
        c1_hull = ((c1.get("final_hp") or {}).get("hull") or 0) if c1 else 0
        c2_hull = ((c2.get("final_hp") or {}).get("hull") or 0) if c2 else 0
        c1_survived = c1_hull > 0
        c2_survived = c2_hull > 0

        # Determine match outcome from actual fight data.
        if c1 or c2:
            # Tick-resolver data present: derive from hull.
            if is_stalemate or outcome_field == "stalemate":
                match_outcome = "Stalemate"
            elif c1_survived and not c2_survived:
                match_outcome = "Victory"
            elif c2_survived and not c1_survived:
                match_outcome = "Defeat"
            else:
                # Both alive or both destroyed — treat as stalemate.
                match_outcome = "Stalemate"
        else:
            # Legacy path: no combatants block; use is_stalemate flag.
            match_outcome = "Stalemate" if (is_stalemate or outcome_field == "stalemate") else "Victory"

        crim_label = criminal_name or "Criminal"

        # Header line
        if duration_s is not None:
            header = f"⚔️ Combat vs {crim_label} — {match_outcome} in {duration_s:.1f}s"
        else:
            header = f"⚔️ Combat vs {crim_label} — {match_outcome}"

        def _hp_str(hp_block: dict) -> str:
            shield = hp_block.get("shield", 0)
            armour = hp_block.get("armour", 0)
            hull = hp_block.get("hull", 0)
            return f"Shield {shield} · Armour {armour} · Hull {hull}"

        def _combatant_block(cb: dict, label: str, survived: bool) -> str:
            """Render one combatant block.

            label already contains the ship name in the format 'You (Ship)' or
            '{Name} (Ship)' so no extra ship suffix is appended here.
            """
            final_hp = cb.get("final_hp") or {}
            hp_str = _hp_str(final_hp)
            dealt = cb.get("damage_dealt", 0)
            fired = cb.get("shots_fired", 0)
            hit = cb.get("shots_hit", 0)
            acc_pct = round((cb.get("accuracy") or 0) * 100)
            acc_str = f"{acc_pct}% acc ({hit}/{fired})" if fired > 0 else "n/a"
            status = "survived" if survived else "destroyed"
            return f"**{label}** — {status}\n  {hp_str}  ·  dealt {dealt} · {acc_str}"

        lines: list[str] = [header]

        if c1 or c2:
            player_ship = c1.get("ship") or "?"
            crim_ship = c2.get("ship") or "?"
            lines.append(_combatant_block(c1, f"You ({player_ship})", c1_survived))
            lines.append(_combatant_block(c2, f"{crim_label} ({crim_ship})", c2_survived))
        else:
            # Fallback: legacy projection fields (pre-tick-resolver data)
            s1 = (combat.get("ship1_stats") or {}) if combat else {}
            s2 = (combat.get("ship2_stats") or {}) if combat else {}
            lines.append(f"**Your Ship** ({s1.get('ship_name', '?')})")
            lines.append(f"**Criminal Ship** ({s2.get('ship_name', '?')})")

        pvc_dr = (combat.get("pvc_damage_reduction") or 0.0) if combat else 0.0
        if pvc_dr > 0:
            lines.append(f"🛡️ PvC damage reduction: {round(pvc_dr * 100)}% active")

        text = "\n".join(lines)
        # Truncate defensively to stay within Discord's 1024-char field limit
        if len(text) > 1024:
            text = text[:1021] + "…"
        return text

    def _get_tier_color(self, tier: str | None) -> discord.Color:
        """Return a discord.Color for the given tier string (case-insensitive).

        Falls back to discord.Color.blue() when tier is None or unknown so that
        no embed is ever left without a color.
        """
        if tier:
            color_int = TIER_COLORS.get(tier.lower())
            if color_int is not None:
                return discord.Color(color_int)
        return discord.Color.blue()

    def _build_check_embed(self, data: dict) -> discord.Embed:
        """Build an embed for the /check command based on the full response data dict."""
        result = data.get("result", "")
        system = data.get("system_name", "")
        message = data.get("message", "")
        tier = data.get("division")  # bounty tier for color-coding

        if result == "correct":
            combat_won = data.get("combat_won")
            criminal_name = data.get("criminal_name", "Unknown")

            if combat_won is False:
                # Criminal escaped after combat loss OR stalemate — checks have been reset
                _is_stalemate = bool((data.get("combat_result") or {}).get("is_stalemate"))
                if _is_stalemate:
                    embed = discord.Embed(
                        title="⚔️ Stalemate!",
                        description=(
                            f"**{criminal_name}** fought you to a standstill and escaped!\n"
                            "All system checks have been reset — the hunt continues!"
                        ),
                        color=discord.Color.dark_red(),
                    )
                else:
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
                        value=self._format_combat_summary(combat, criminal_name=criminal_name),
                        inline=False,
                    )
            else:
                # Successful capture — render the full payout embed so this
                # single /check response is the only message the channel sees.
                embed = self._build_capture_embed(data)
        elif result == "captured":
            # Backward-compatible handler: treated same as correct+combat_won=True (Bronze capture)
            embed = self._build_capture_embed(data)
        elif result == "combat_win":
            # Backward-compatible handler: treated same as correct+combat_won=True (Silver+ capture)
            embed = self._build_capture_embed(data)
        elif result == "combat_loss":
            # Kept for backward compatibility (not returned by current bot-core but may exist in future)
            _crim_name_loss = data.get("criminal_name", "Unknown")
            embed = discord.Embed(
                title="💀 Combat Defeat!",
                description=(
                    f"**{_crim_name_loss}** defeated you and escaped!\n"
                    "All system checks have been reset — the hunt continues!"
                ),
                color=discord.Color.dark_red(),
            )
            combat = data.get("combat_result")
            if combat:
                embed.add_field(
                    name="⚔️ Combat Summary",
                    value=self._format_combat_summary(combat, criminal_name=_crim_name_loss),
                    inline=False,
                )
        elif result == "incorrect":
            recently_spotted = data.get("recently_spotted", False)
            if recently_spotted:
                embed = discord.Embed(
                    title="👀 Recently Spotted!",
                    description=f"**{system}** — The target was recently here. They're close!",
                    color=self._get_tier_color(tier),
                )
            else:
                embed = discord.Embed(
                    title="❌ System Checked",
                    description=f"**{system}** — System checked, bounty not here.",
                    color=self._get_tier_color(tier),
                )
            if message:
                embed.add_field(name="Intel", value=message, inline=False)
        elif result == "already_checked":
            embed = discord.Embed(
                title="🔁 Already Checked",
                description=f"**{system}** — This system has already been checked.",
                color=self._get_tier_color(tier),
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

    def _build_capture_embed(self, data: dict) -> discord.Embed:
        """Build the full capture payout embed for a successful /check capture.

        Consolidates the combat summary and payout breakdown into a single embed
        so that the /check response IS the only capture message in the channel.
        """
        criminal_name = data.get("criminal_name", "Unknown")
        tier = data.get("division")
        reward = data.get("reward", 0)
        total_reward = data.get("total_reward") or reward
        bonus_won = data.get("bonus_won", False)
        winner_name = data.get("winner_name") or "A bounty hunter"

        embed = discord.Embed(
            title="🎯 Bounty Captured!",
            description=f"**{criminal_name}** has been brought in.",
            color=self._get_tier_color(tier),
        )

        # Combat summary (capture = player won)
        combat = data.get("combat_result")
        if combat:
            embed.add_field(
                name="⚔️ Combat Summary",
                value=self._format_combat_summary(combat, criminal_name=criminal_name),
                inline=False,
            )

        # "Captured by" — single inline label:value (no Division field)
        embed.add_field(name="", value=f"Captured by: **{winner_name}**", inline=True)

        # Payout breakdown — per-player if available, else fall back to total
        breakdown = data.get("payout_breakdown") or []
        if breakdown:
            # Sort descending by amount (winner first)
            breakdown = sorted(breakdown, key=lambda x: x.get("amount", 0), reverse=True)
            lines = []
            for entry in breakdown:
                icon = "🏆" if entry.get("role") == "capture claim" else "🔍"
                name = entry.get("player_display_name", "Unknown")
                role = entry.get("role", "")
                amount = entry.get("amount", 0)
                lines.append(f"{icon} {name} — {role} — {amount:,} cr")
            embed.add_field(name="💰 Payout Breakdown", value="\n".join(lines), inline=False)
        elif bonus_won:
            embed.add_field(
                name="💰 Total Payout",
                value=f"**{total_reward:,} cr** (2× combat bonus!)",
                inline=False,
            )
        else:
            embed.add_field(name="💰 Total Payout", value=f"**{total_reward:,} cr**", inline=False)

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
    # /bounties [show_all]
    # ------------------------------------------------------------------

    @app_commands.command(name="bounties", description="List active bounties in this guild")
    @app_commands.describe(show_all="Show bounties across all tiers (default: your tier only)")
    async def bounties(
        self,
        interaction: discord.Interaction,
        show_all: bool = False,
    ):
        """List active bounties.

        By default shows only bounties in the invoking player's current tier.
        Pass show_all=True to see every active bounty across all tiers.
        """
        await interaction.response.defer(thinking=True)
        flogger.info(f"/bounties invoked: guild={interaction.guild_id} user={interaction.user.id} show_all={show_all}")

        try:
            title_suffix = ""

            if not show_all:
                # Resolve player tier — try cache first (graceful degradation on miss)
                player_tier: str | None = None
                if autocomplete_state.player_cache is not None:
                    player_entry = autocomplete_state.player_cache.peek((interaction.guild_id, interaction.user.id))
                    if player_entry is not None:
                        player_tier = player_entry.get("tier") or None

                if player_tier is None:
                    # Cache miss — fall back to HTTP to get the player's tier
                    player_resp = await self.http_client.post(
                        f"{api_base}/players/",
                        json={
                            "discord_id": interaction.user.id,
                            "guild_id": interaction.guild_id,
                            "discord_username": None,
                            "display_name": getattr(interaction.user, "display_name", None),
                        },
                        timeout=10,
                    )
                    player_resp.raise_for_status()
                    player_data = player_resp.json()
                    player_tier = player_data.get("tier") or "Bronze"

                division = player_tier.lower()
                title_suffix = f" — {player_tier} Tier"
            else:
                division = None
                title_suffix = " — All Tiers"

            # Try cache first for bounty list — fall back to HTTP on miss or empty cache.
            # Use truthiness check (not `is not None`) so a stale empty list doesn't
            # suppress an HTTP fetch that would return real bounties.
            guild_id = interaction.guild_id
            cached_bounties = self._bounty_cache.peek(guild_id)
            if cached_bounties:
                # Filter by division if needed
                if division is not None:
                    bounty_list = [b for b in cached_bounties if b.get("division", "").lower() == division]
                else:
                    bounty_list = list(cached_bounties)
            else:
                # Cache miss — fetch from HTTP
                params: dict = {"guild_id": guild_id}
                if division is not None:
                    params["division"] = division
                resp = await self.http_client.get(
                    f"{api_base}/bounties/",
                    params=params,
                    timeout=10,
                )
                resp.raise_for_status()
                bounty_list = resp.json()

            title = f"📋 Active Bounties{title_suffix}"

            if not bounty_list:
                embed = discord.Embed(
                    title=title,
                    description="No active bounties at this time.",
                    color=discord.Color.light_grey(),
                )
                await interaction.followup.send(embed=embed)
                return

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
