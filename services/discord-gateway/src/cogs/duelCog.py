import os

import discord
import httpx
from cogs._shared.autocomplete_cache import AutocompleteCache
from cogs._shared.http_error_handler import report_api_error
from discord import app_commands
from discord.ext import commands
from shared import bblogger
from utils.autocomplete_utils import normalize_for_search
from utils.timestamp_utils import iso_to_discord_ts

from utils import autocomplete_state

# Set up logger
flogger = bblogger.get_logger("discord-gateway-DuelCog")

# Define any environment variables or constants here
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"duelCog loading with API_BASE_URL: {api_base}")

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


class DuelCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._pending_duel_cache: AutocompleteCache[tuple[int, int], list] = AutocompleteCache(
            ttl_seconds=1800.0,  # 30 min dead-man switch; refresh job runs every 5 min
            refresh_fn=self._fetch_pending_duels,
            name="duelCog-pending-duels",
        )
        self._outgoing_duel_cache: AutocompleteCache[tuple[int, int], list] = AutocompleteCache(
            ttl_seconds=1800.0,  # 30 min dead-man switch; refresh job runs every 5 min
            refresh_fn=self._fetch_outgoing_duels,
            name="duelCog-outgoing-duels",
        )
        flogger.debug("DuelCog initialized")

    async def cog_unload(self):
        await self.http_client.aclose()

    async def _fetch_pending_duels(self, key: tuple[int, int]) -> list:
        """Refresh pending duels (where the player is the target) from bot-core.

        Args:
            key: ``(guild_id, player_id)`` tuple.

        Returns:
            List of pending duel dicts.

        Phase 7: Pre-computes ``_norm`` on each duel dict at fill time so the
        hot-path autocomplete scan never calls ``normalize_for_search`` per duel.
        """
        guild_id, player_id = key
        try:
            resp = await self.http_client.get(
                f"{api_base}/duels/pending",
                params={"user_id": player_id, "guild_id": guild_id},
                timeout=5,
            )
            if resp.status_code != 200:
                return []
            duels = resp.json()
            # Pre-compute _norm at fill time — hot path uses pre-computed value.
            for d in duels:
                duel_id = d.get("id", "")
                stakes = d.get("stakes", 0)
                challenger_name = d.get("challenger_name")
                if challenger_name:
                    label = (
                        f"{challenger_name} — {stakes:,}cr stakes" if stakes else f"{challenger_name} — friendly duel"
                    )
                else:
                    label = f"Duel #{duel_id} — {stakes:,}cr stakes" if stakes else f"Duel #{duel_id} — friendly duel"
                d["_norm"] = normalize_for_search(label)
            return duels
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    async def _fetch_outgoing_duels(self, key: tuple[int, int]) -> list:
        """Refresh outgoing duels (where the player is the challenger) from bot-core.

        Args:
            key: ``(guild_id, player_id)`` tuple.

        Returns:
            List of outgoing duel dicts.

        Phase 7: Pre-computes ``_norm`` on each duel dict at fill time so the
        hot-path autocomplete scan never calls ``normalize_for_search`` per duel.
        """
        guild_id, player_id = key
        try:
            resp = await self.http_client.get(
                f"{api_base}/duels/outgoing",
                params={"user_id": player_id, "guild_id": guild_id},
                timeout=5,
            )
            if resp.status_code != 200:
                return []
            duels = resp.json()
            # Pre-compute _norm at fill time — hot path uses pre-computed value.
            for d in duels:
                duel_id = d.get("id", "")
                stakes = d.get("stakes", 0)
                target_name = d.get("target_name")
                if target_name:
                    label = f"{target_name} — {stakes:,}cr stakes" if stakes else f"{target_name} — friendly duel"
                else:
                    label = f"Duel #{duel_id} — {stakes:,}cr stakes" if stakes else f"Duel #{duel_id} — friendly duel"
                d["_norm"] = normalize_for_search(label)
            return duels
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

    async def pending_duel_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Zero-HTTP autocomplete for pending duels where the user is the target.

        Phase 6: Reads player_id from autocomplete_state.player_cache (peek), then
        reads pending duels from _pending_duel_cache (peek). On any cold miss,
        schedules a background refresh and returns [].
        """
        try:
            guild_id = interaction.guild_id
            user_id = interaction.user.id

            # GATE 1 (cold-fill): resolve player_id from shared player cache.
            if autocomplete_state.player_cache is None:
                return []
            player_entry = autocomplete_state.player_cache.peek((guild_id, user_id))
            if player_entry is None:
                player_entry = await autocomplete_state.player_cache.get_with_timeout((guild_id, user_id), timeout=1.0)
            if player_entry is None:
                return []
            player_id = player_entry.get("id")
            if not player_id:
                return []

            # GATE 2 (cold-fill): pending duel cache. Two 1.0s gates ≈ 2s worst case,
            # within the 3s autocomplete budget.
            duels = self._pending_duel_cache.peek((guild_id, player_id))
            if duels is None:
                duels = await self._pending_duel_cache.get_with_timeout((guild_id, player_id), timeout=1.0)
            if duels is None:
                return []

            norm_current = normalize_for_search(current)
            choices = []
            for d in duels:
                duel_id = d["id"]
                stakes = d.get("stakes", 0)
                challenger_name = d.get("challenger_name")
                if challenger_name:
                    label = (
                        f"{challenger_name} — {stakes:,}cr stakes" if stakes else f"{challenger_name} — friendly duel"
                    )
                else:
                    label = f"Duel #{duel_id} — {stakes:,}cr stakes" if stakes else f"Duel #{duel_id} — friendly duel"
                # Phase 7: use pre-computed _norm; fall back to on-the-fly for older cache entries.
                norm_label = d.get("_norm") or normalize_for_search(label)
                if norm_current in norm_label:
                    choices.append(app_commands.Choice(name=label[:100], value=str(duel_id)))
            return choices[:25]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    async def outgoing_duel_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Zero-HTTP autocomplete for outgoing duels where the user is the challenger (for /duel-cancel).

        Phase 6: Reads player_id from autocomplete_state.player_cache (peek), then
        reads outgoing duels from _outgoing_duel_cache (peek). On any cold miss,
        schedules a background refresh and returns [].
        """
        try:
            guild_id = interaction.guild_id
            user_id = interaction.user.id

            # GATE 1 (cold-fill): resolve player_id from shared player cache.
            if autocomplete_state.player_cache is None:
                return []
            player_entry = autocomplete_state.player_cache.peek((guild_id, user_id))
            if player_entry is None:
                player_entry = await autocomplete_state.player_cache.get_with_timeout((guild_id, user_id), timeout=1.0)
            if player_entry is None:
                return []
            player_id = player_entry.get("id")
            if not player_id:
                return []

            # GATE 2 (cold-fill): outgoing duel cache. Two 1.0s gates ≈ 2s worst case,
            # within the 3s autocomplete budget.
            duels = self._outgoing_duel_cache.peek((guild_id, player_id))
            if duels is None:
                duels = await self._outgoing_duel_cache.get_with_timeout((guild_id, player_id), timeout=1.0)
            if duels is None:
                return []

            norm_current = normalize_for_search(current)
            choices = []
            for d in duels:
                duel_id = d["id"]
                stakes = d.get("stakes", 0)
                target_name = d.get("target_name")
                if target_name:
                    label = f"{target_name} — {stakes:,}cr stakes" if stakes else f"{target_name} — friendly duel"
                else:
                    label = f"Duel #{duel_id} — {stakes:,}cr stakes" if stakes else f"Duel #{duel_id} — friendly duel"
                # Phase 7: use pre-computed _norm; fall back to on-the-fly for older cache entries.
                norm_label = d.get("_norm") or normalize_for_search(label)
                if norm_current in norm_label:
                    choices.append(app_commands.Choice(name=label[:100], value=str(duel_id)))
            return choices[:25]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    # ------------------------------------------------------------------
    # /duel-challenge <target> [stakes]
    # ------------------------------------------------------------------

    @app_commands.command(name="duel-challenge", description="Challenge another player to a duel")
    @app_commands.describe(
        target="The player to challenge",
        stakes="Credits to wager (default: 0 for a friendly duel)",
    )
    async def duel_challenge(
        self,
        interaction: discord.Interaction,
        target: discord.User,
        stakes: int = 0,
    ):
        """Challenge a player to a duel."""
        await interaction.response.defer(thinking=True)
        flogger.info(
            f"/duel-challenge invoked: guild={interaction.guild_id} user={interaction.user.id}"
            f" target={target.id} stakes={stakes}"
        )

        try:
            # Resolve Discord user IDs to internal player PKs
            try:
                challenger_player_id = await self._get_player_id(
                    interaction.user.id,
                    interaction.guild_id,
                    display_name=getattr(interaction.user, "display_name", None),
                )
            except httpx.HTTPStatusError:
                await interaction.followup.send(_GUILD_NOT_CONFIGURED_MSG, ephemeral=True)
                return
            if challenger_player_id is None:
                await interaction.followup.send(
                    "❌ Could not find your player profile. Have you run `/register`?", ephemeral=True
                )
                return

            try:
                target_player_id = await self._get_player_id(
                    target.id,
                    interaction.guild_id,
                    display_name=getattr(target, "display_name", None),
                )
            except httpx.HTTPStatusError:
                await interaction.followup.send(_GUILD_NOT_CONFIGURED_MSG, ephemeral=True)
                return
            if target_player_id is None:
                await interaction.followup.send("❌ Could not find target player profile.", ephemeral=True)
                return

            resp = await self.http_client.post(
                f"{api_base}/duels/challenge",
                json={
                    "challenger_id": challenger_player_id,
                    "target_id": target_player_id,
                    "stakes": stakes,
                    "guild_id": interaction.guild_id,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            embed = self._build_challenge_embed(interaction.user, target, data, stakes)
            await interaction.followup.send(content=target.mention, embed=embed)
            duel_id = data.get("id", "?")
            flogger.info(
                f"/duel-challenge success: guild={interaction.guild_id} user={interaction.user.id}"
                f" target={target.id} stakes={stakes} duel_id={duel_id}"
            )

            # Invalidate duel caches: challenger's outgoing, target's pending
            try:
                self._outgoing_duel_cache.invalidate((interaction.guild_id, challenger_player_id))
                self._pending_duel_cache.invalidate((interaction.guild_id, target_player_id))
            except Exception:  # pylint: disable=broad-exception-caught
                flogger.warning(
                    f"/duel-challenge: duel cache invalidation failed for duel_id={duel_id}; "
                    "transaction still succeeded"
                )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                try:
                    detail = e.response.json().get("detail", str(e))
                except Exception:  # pylint: disable=broad-exception-caught
                    detail = str(e)
                await interaction.followup.send(f"❌ {detail}", ephemeral=True)
            else:
                flogger.error(
                    f"/duel-challenge API error: guild={interaction.guild_id} user={interaction.user.id}"
                    f" target={target.id} status={e.response.status_code}"
                )
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"/duel-challenge error: guild={interaction.guild_id} user={interaction.user.id}"
                f" target={target.id} error={e}"
            )
            await interaction.followup.send("⚠️ An error occurred while creating the duel challenge.", ephemeral=True)

    def _build_challenge_embed(
        self,
        challenger: discord.User,
        target: discord.User,
        data: dict,
        stakes: int,
    ) -> discord.Embed:
        """Build an embed for a successful duel challenge."""
        duel_id = data.get("id", "?")
        stakes_str = f"**{stakes:,}** credits" if stakes else "**Friendly duel** (no stakes)"

        embed = discord.Embed(
            title="⚔️ Duel Challenge Issued!",
            description=(
                f"{challenger.mention} has challenged {target.mention} to a duel!\n\n"
                f"**Stakes:** {stakes_str}\n"
                f"**Duel ID:** #{duel_id}"
            ),
            color=discord.Color.orange(),
        )
        expires_at = data.get("expires_at")
        if expires_at:
            expiry_str = f"Challenge expires {iso_to_discord_ts(expires_at, 'R')}."
        else:
            expiry_str = "Challenge expires in **24 hours**."
        embed.add_field(
            name="📋 Instructions",
            value=(f"{target.mention}: Use `/duel-accept` to accept or `/duel-reject` to decline.\n{expiry_str}"),
            inline=False,
        )
        return embed

    @duel_challenge.error
    async def duel_challenge_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /duel-challenge", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    # ------------------------------------------------------------------
    # /duel-accept <duel>
    # ------------------------------------------------------------------

    @app_commands.command(name="duel-accept", description="Accept a pending duel challenge")
    @app_commands.describe(duel="Select a pending duel challenge to accept")
    @app_commands.autocomplete(duel=pending_duel_autocomplete)
    async def duel_accept(self, interaction: discord.Interaction, duel: str):
        """Accept a pending duel challenge and resolve combat."""
        await interaction.response.defer(thinking=True)
        flogger.info(f"/duel-accept invoked: guild={interaction.guild_id} user={interaction.user.id} duel={duel}")

        try:
            duel_id = int(duel)
        except ValueError:
            await interaction.followup.send(
                "❌ Invalid duel selection. Please select from the dropdown.",
                ephemeral=True,
            )
            return

        try:
            # Resolve Discord user ID to internal player PK for authorization
            try:
                player_id = await self._get_player_id(
                    interaction.user.id,
                    interaction.guild_id,
                    display_name=getattr(interaction.user, "display_name", None),
                )
            except httpx.HTTPStatusError:
                await interaction.followup.send(_GUILD_NOT_CONFIGURED_MSG, ephemeral=True)
                return
            if player_id is None:
                await interaction.followup.send(
                    "❌ Could not find your player profile. Have you run `/register`?", ephemeral=True
                )
                return

            resp = await self.http_client.post(
                f"{api_base}/duels/{duel_id}/accept",
                params={"user_id": player_id},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            embed = self._build_accept_embed(duel_id, data)
            await interaction.followup.send(embed=embed)
            is_stalemate = data.get("is_stalemate", False)
            flogger.info(
                f"/duel-accept success: guild={interaction.guild_id} user={interaction.user.id}"
                f" duel_id={duel_id} stalemate={is_stalemate}"
            )

            # Invalidate duel caches: accepter's pending, challenger's outgoing
            try:
                self._pending_duel_cache.invalidate((interaction.guild_id, player_id))
                challenger_player_id = data.get("challenger_id")
                if challenger_player_id is not None:
                    self._outgoing_duel_cache.invalidate((interaction.guild_id, challenger_player_id))
            except Exception:  # pylint: disable=broad-exception-caught
                flogger.warning(
                    f"/duel-accept: duel cache invalidation failed for duel_id={duel_id}; transaction still succeeded"
                )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                await interaction.followup.send("❌ Duel not found.", ephemeral=True)
            elif e.response.status_code == 403:
                await interaction.followup.send("❌ You can only accept duels that were issued to you.", ephemeral=True)
            elif e.response.status_code == 400:
                try:
                    detail = e.response.json().get("detail", str(e))
                except Exception:  # pylint: disable=broad-exception-caught
                    detail = str(e)
                await interaction.followup.send(f"❌ {detail}", ephemeral=True)
            else:
                flogger.error(
                    f"/duel-accept API error: guild={interaction.guild_id} user={interaction.user.id}"
                    f" duel_id={duel_id} status={e.response.status_code}"
                )
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"/duel-accept error: guild={interaction.guild_id} user={interaction.user.id}"
                f" duel_id={duel_id} error={e}"
            )
            await interaction.followup.send("⚠️ An error occurred while accepting the duel.", ephemeral=True)

    def _build_accept_embed(self, duel_id: int, data: dict) -> discord.Embed:
        """Build an embed for a completed duel (accept result).

        Renders actual after-action stats (final HP, damage dealt, accuracy, duration)
        from the tick-resolver summary when available.  Falls back gracefully to the
        legacy TTK-comparison approach for old responses without summary data.
        """
        is_stalemate = data.get("is_stalemate", False)
        credits_transferred = data.get("credits_transferred", 0)
        stakes = data.get("stakes", 0)

        challenger_id = data.get("challenger_id")
        challenger_name = data.get("challenger_name") or f"Player {challenger_id}"
        challenger_credits = data.get("challenger_credits", 0)
        target_id = data.get("target_id")
        target_name = data.get("target_name") or f"Player {target_id}"
        target_credits = data.get("target_credits", 0)

        # ------------------------------------------------------------------
        # Determine winner/loser display names.
        #
        # When the tick-resolver summary is present (combatants block), resolve
        # winner by who survived (final_hp.hull > 0), NOT by ship-name equality.
        # Ship-name equality is unreliable when both players fly the same ship
        # (e.g. both in "Betty") — whoever won, c1 hull > 0 iff challenger survived.
        #
        # Slot mapping (invariant from duel_service / fight_ships call order):
        #   combatants["1"] = challenger (loadout1)
        #   combatants["2"] = target     (loadout2)
        #
        # Stalemate / both-survive / both-zero: caller must set is_stalemate=True
        # before reaching this block; we honour that flag above and never fabricate
        # a winner in ambiguous hull states.
        #
        # Legacy TTK heuristic is preserved for old responses that have no
        # combatants block.
        # ------------------------------------------------------------------
        combatants = data.get("combatants") or {}
        c1 = combatants.get("1") or {}
        c2 = combatants.get("2") or {}

        winner_ship = data.get("winner_name", "")

        try:
            if c1 or c2:
                # Tick-resolver path: winner resolved by final hull HP.
                c1_hull = (c1.get("final_hp") or {}).get("hull", 0) or 0
                c2_hull = (c2.get("final_hp") or {}).get("hull", 0) or 0
                if c1_hull > 0 and c2_hull <= 0:
                    # Challenger survived; target did not.
                    winner_display, loser_display = challenger_name, target_name
                elif c2_hull > 0 and c1_hull <= 0:
                    # Target survived; challenger did not.
                    winner_display, loser_display = target_name, challenger_name
                else:
                    # Both alive, both dead, or missing data — treat as stalemate.
                    # is_stalemate should already be True in this case; fall back
                    # gracefully so the embed still shows something sensible.
                    winner_display = winner_ship or "Unknown"
                    loser_display = data.get("loser_name", "Unknown") or "Unknown"
            else:
                # Legacy TTK heuristic (pre-summary data, no combatants block)
                challenger_hp = data.get("challenger_hp", 0) or 0
                challenger_dps = data.get("challenger_dps", 0) or 0
                target_hp = data.get("target_hp", 0) or 0
                target_dps = data.get("target_dps", 0) or 0
                if challenger_dps > 0 and target_dps > 0:
                    challenger_ttk = challenger_hp / target_dps
                    target_ttk = target_hp / challenger_dps
                    if challenger_ttk > target_ttk:
                        winner_display, loser_display = challenger_name, target_name
                    else:
                        winner_display, loser_display = target_name, challenger_name
                else:
                    winner_display = winner_ship or "Unknown"
                    loser_display = data.get("loser_name", "Unknown") or "Unknown"
        except Exception:  # pylint: disable=broad-exception-caught
            winner_display = winner_ship or "Unknown"
            loser_display = data.get("loser_name", "Unknown") or "Unknown"

        # ------------------------------------------------------------------
        # Duration line
        # ------------------------------------------------------------------
        duration_s: float | None = data.get("duration_s")
        if is_stalemate:
            if duration_s is not None:
                outcome_str = f"⚔️ Duel Complete — Stalemate in {duration_s:.1f}s"
            else:
                outcome_str = "⚔️ Duel Complete — Stalemate"
        else:
            if duration_s is not None:
                outcome_str = f"⚔️ Duel Complete — {winner_display} won in {duration_s:.1f}s"
            else:
                outcome_str = f"⚔️ Duel Complete — {winner_display} won"

        # ------------------------------------------------------------------
        # Build embed
        # ------------------------------------------------------------------
        if is_stalemate:
            embed = discord.Embed(
                title=outcome_str,
                description=(
                    f"**Duel #{duel_id}** ended in a stalemate!\n\n"
                    "Neither combatant could overcome the other.\n"
                    "No credits were transferred."
                ),
                color=discord.Color.yellow(),
            )
        else:
            embed = discord.Embed(
                title=outcome_str,
                description=(
                    f"**Duel #{duel_id}** has been resolved!\n\n"
                    f"🏆 **Winner:** {winner_display}\n"
                    f"💀 **Loser:** {loser_display}\n"
                    f"💰 **Credits transferred:** {credits_transferred:,}"
                ),
                color=discord.Color.green(),
            )
            if stakes:
                embed.add_field(
                    name="💳 Final Balances",
                    value=(
                        f"{challenger_name}: **{challenger_credits:,}** cr\n{target_name}: **{target_credits:,}** cr"
                    ),
                    inline=False,
                )

        # ------------------------------------------------------------------
        # After-action combat summary field (actual stats from tick resolver)
        # Compact worded format — no "You" (duel uses real player names), no header line.
        # ------------------------------------------------------------------
        def _hp_str_duel(hp_block: dict) -> str:
            shield = hp_block.get("shield", 0)
            armour = hp_block.get("armour", 0)
            hull = hp_block.get("hull", 0)
            return f"Shield {shield} · Armour {armour} · Hull {hull}"

        def _combatant_block_duel(cb: dict, label: str, survived: bool) -> str:
            """Render one combatant block for a duel.

            label already contains the ship name in the format '{Name} (Ship)' so
            no extra ship suffix is appended here.
            """
            final_hp = cb.get("final_hp") or {}
            hp_str = _hp_str_duel(final_hp)
            dealt = cb.get("damage_dealt", 0)
            fired = cb.get("shots_fired", 0)
            hit = cb.get("shots_hit", 0)
            acc_pct = round((cb.get("accuracy") or 0) * 100)
            acc_str = f"{acc_pct}% acc ({hit}/{fired})" if fired > 0 else "n/a"
            status = "survived" if survived else "destroyed"
            return f"**{label}** — {status}\n  {hp_str}  ·  dealt {dealt} · {acc_str}"

        if c1 or c2:
            c1_hull_val = ((c1.get("final_hp") or {}).get("hull") or 0) if c1 else 0
            c2_hull_val = ((c2.get("final_hp") or {}).get("hull") or 0) if c2 else 0
            c1_survived_duel = c1_hull_val > 0
            c2_survived_duel = c2_hull_val > 0
            c1_ship_duel = c1.get("ship") or "?"
            c2_ship_duel = c2.get("ship") or "?"
            summary_lines = [
                _combatant_block_duel(c1, f"{challenger_name} ({c1_ship_duel})", c1_survived_duel),
                _combatant_block_duel(c2, f"{target_name} ({c2_ship_duel})", c2_survived_duel),
            ]
            summary_text = "\n".join(summary_lines)
            if len(summary_text) > 1024:
                summary_text = summary_text[:1021] + "…"
            embed.add_field(name="⚔️ Combat Stats", value=summary_text, inline=False)

        return embed

    @duel_accept.error
    async def duel_accept_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /duel-accept", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    # ------------------------------------------------------------------
    # /duel-reject <duel>
    # ------------------------------------------------------------------

    @app_commands.command(name="duel-reject", description="Reject a pending duel challenge")
    @app_commands.describe(duel="Select a pending duel challenge to reject")
    @app_commands.autocomplete(duel=pending_duel_autocomplete)
    async def duel_reject(self, interaction: discord.Interaction, duel: str):
        """Reject a pending duel challenge."""
        await interaction.response.defer(thinking=True)
        flogger.info(f"/duel-reject invoked: guild={interaction.guild_id} user={interaction.user.id} duel={duel}")

        try:
            duel_id = int(duel)
        except ValueError:
            await interaction.followup.send(
                "❌ Invalid duel selection. Please select from the dropdown.",
                ephemeral=True,
            )
            return

        try:
            # Resolve Discord user ID to internal player PK for authorization
            try:
                player_id = await self._get_player_id(
                    interaction.user.id,
                    interaction.guild_id,
                    display_name=getattr(interaction.user, "display_name", None),
                )
            except httpx.HTTPStatusError:
                await interaction.followup.send(_GUILD_NOT_CONFIGURED_MSG, ephemeral=True)
                return
            if player_id is None:
                await interaction.followup.send(
                    "❌ Could not find your player profile. Have you run `/register`?", ephemeral=True
                )
                return

            resp = await self.http_client.post(
                f"{api_base}/duels/{duel_id}/reject",
                params={"user_id": player_id},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            embed = discord.Embed(
                title="🚫 Duel Rejected",
                description=(f"**Duel #{duel_id}** has been rejected.\nThe challenge has been declined."),
                color=discord.Color.red(),
            )
            if data.get("challenger_name"):
                embed.add_field(
                    name="Details",
                    value=f"Challenger: {data['challenger_name']}",
                    inline=False,
                )
            await interaction.followup.send(embed=embed)
            flogger.info(
                f"/duel-reject success: guild={interaction.guild_id} user={interaction.user.id} duel_id={duel_id}"
            )

            # Invalidate duel caches: rejecter's pending, challenger's outgoing
            try:
                self._pending_duel_cache.invalidate((interaction.guild_id, player_id))
                challenger_player_id = data.get("challenger_id")
                if challenger_player_id is not None:
                    self._outgoing_duel_cache.invalidate((interaction.guild_id, challenger_player_id))
            except Exception:  # pylint: disable=broad-exception-caught
                flogger.warning(
                    f"/duel-reject: duel cache invalidation failed for duel_id={duel_id}; transaction still succeeded"
                )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                await interaction.followup.send("❌ Duel not found.", ephemeral=True)
            elif e.response.status_code == 403:
                await interaction.followup.send("❌ You can only reject duels that were issued to you.", ephemeral=True)
            elif e.response.status_code == 400:
                try:
                    detail = e.response.json().get("detail", str(e))
                except Exception:  # pylint: disable=broad-exception-caught
                    detail = str(e)
                await interaction.followup.send(f"❌ {detail}", ephemeral=True)
            else:
                flogger.error(
                    f"/duel-reject API error: guild={interaction.guild_id} user={interaction.user.id}"
                    f" duel_id={duel_id} status={e.response.status_code}"
                )
                await report_api_error(interaction, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"/duel-reject error: guild={interaction.guild_id} user={interaction.user.id}"
                f" duel_id={duel_id} error={e}"
            )
            await interaction.followup.send("⚠️ An error occurred while rejecting the duel.", ephemeral=True)

    @duel_reject.error
    async def duel_reject_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /duel-reject", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    # ------------------------------------------------------------------
    # /duel-cancel <duel>  (B.64 — challenger self-cancel)
    # ------------------------------------------------------------------

    @app_commands.command(name="duel-cancel", description="Cancel a duel challenge you issued")
    @app_commands.describe(duel="Select an outgoing duel challenge to cancel")
    @app_commands.autocomplete(duel=outgoing_duel_autocomplete)
    async def duel_cancel(self, interaction: discord.Interaction, duel: str):
        """Cancel a pending duel challenge that the invoking user issued."""
        await interaction.response.defer(thinking=True)
        flogger.info(f"/duel-cancel invoked: guild={interaction.guild_id} user={interaction.user.id} duel={duel}")

        try:
            duel_id = int(duel)
        except ValueError:
            await interaction.followup.send(
                "❌ Invalid duel selection. Please select from the dropdown.",
                ephemeral=True,
            )
            return

        try:
            # Resolve Discord user ID to internal player PK for authorization
            try:
                player_id = await self._get_player_id(
                    interaction.user.id,
                    interaction.guild_id,
                    display_name=getattr(interaction.user, "display_name", None),
                )

            except httpx.HTTPStatusError:
                await interaction.followup.send(_GUILD_NOT_CONFIGURED_MSG, ephemeral=True)
                return
            if player_id is None:
                await interaction.followup.send(
                    "❌ Could not find your player profile. Have you run `/register`?", ephemeral=True
                )
                return

            resp = await self.http_client.post(
                f"{api_base}/duels/{duel_id}/cancel",
                params={"user_id": player_id},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            target_name = data.get("target_name") or f"Player {data.get('target_id', '?')}"
            embed = discord.Embed(
                title="✅ Duel Cancelled",
                description=(f"Your challenge against **{target_name}** has been withdrawn."),
                color=discord.Color.orange(),
            )
            embed.add_field(name="Duel ID", value=f"#{duel_id}", inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(
                f"/duel-cancel success: guild={interaction.guild_id} user={interaction.user.id} duel_id={duel_id}"
            )

            # Invalidate duel caches: canceller's outgoing, target's pending
            try:
                self._outgoing_duel_cache.invalidate((interaction.guild_id, player_id))
                target_player_id = data.get("target_id")
                if target_player_id is not None:
                    self._pending_duel_cache.invalidate((interaction.guild_id, target_player_id))
            except Exception:  # pylint: disable=broad-exception-caught
                flogger.warning(
                    f"/duel-cancel: duel cache invalidation failed for duel_id={duel_id}; transaction still succeeded"
                )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                await interaction.followup.send("❌ Duel not found.", ephemeral=True)
            elif e.response.status_code == 400:
                try:
                    detail = e.response.json().get("detail", str(e))
                except Exception:  # pylint: disable=broad-exception-caught
                    detail = str(e)
                await interaction.followup.send(f"❌ {detail}", ephemeral=True)
            else:
                flogger.error(
                    f"/duel-cancel API error: guild={interaction.guild_id} user={interaction.user.id}"
                    f" duel_id={duel_id} status={e.response.status_code}"
                )
                await interaction.followup.send("⚠️ An error occurred while cancelling the duel.", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"/duel-cancel error: guild={interaction.guild_id} user={interaction.user.id}"
                f" duel_id={duel_id} error={e}"
            )
            await interaction.followup.send("⚠️ An error occurred while cancelling the duel.", ephemeral=True)

    @duel_cancel.error
    async def duel_cancel_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /duel-cancel", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)


async def setup(bot: commands.Bot):
    flogger.debug("Setting up DuelCog...")
    await bot.add_cog(DuelCog(bot))
    flogger.info("DuelCog loaded")
