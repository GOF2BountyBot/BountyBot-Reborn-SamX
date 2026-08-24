"""combatLogCog — /combat-log[-pvp|-bounty] and /admin_combat_log commands with autocomplete.

The /combat-log command lets a player look up their past battles.
Autocomplete is populated exclusively with the invoking user's fights in the
current guild, newest-first (cap 25 — Discord's autocomplete limit).

/combat-log-pvp and /combat-log-bounty (issue #86) are type-scoped variants:
identical detail rendering, but their autocomplete lists only duels or only
bounty fights respectively. The type is part of the autocomplete cache key
(not a client-side filter) so each list yields a full 25 rows of its own type.

/combat-log accepts an optional ``public`` flag (default False): the same
embed is posted publicly instead of ephemerally; errors stay ephemeral.

/admin_combat_log is the admin variant: a mandatory ``user`` param selects the
player, then the ``battle`` autocomplete lists that player's fights instead of
the invoker's.  The detail embed is identical to /combat-log; delivery is
always ephemeral (no ``public`` option).  Discord cannot enforce option fill-order, so when ``user`` is
still empty the battle autocomplete returns a single "Select a user first"
hint choice carrying a sentinel value the command body rejects.

Choice labels are disambiguated: same-opponent same-day collisions get an
ordinal counter (most-recent = highest).  Format:
  "#2 vs General_Failure · Duel · 2026-06-03 · WON"

The detail embed mirrors bountyCog._format_combat_summary style.
"""

import os

import discord
import httpx
from cogs._shared.autocomplete_cache import AutocompleteCache
from cogs.adminCog import _check_is_admin
from discord import app_commands
from discord.ext import commands
from shared import bblogger

flogger = bblogger.get_logger("discord-gateway-CombatLogCog")

api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"combatLogCog loading with API_BASE_URL: {api_base}")

# Context → human-readable label
_CONTEXT_LABELS: dict[str, str] = {
    "duel": "Duel",
    "bounty_bonus": "Bounty",
    "bounty_pvc": "Bounty",
}

# Outcome → emoji prefix
_OUTCOME_EMOJI: dict[str, str] = {
    "won": "WON",
    "lost": "LOST",
    "stalemate": "DRAW",
}

# Sentinel battle value returned by the /admin_combat_log battle autocomplete
# when the user param has not been filled in yet (Discord cannot enforce option
# fill-order).  Battle IDs are positive, so -1 can never collide.
_SELECT_USER_FIRST = -1


def _format_date(dt_str: str) -> str:
    """Return YYYY-MM-DD from an ISO 8601 datetime string."""
    try:
        return dt_str[:10]
    except Exception:  # pylint: disable=broad-exception-caught
        return dt_str


def _make_choice_label(item: dict) -> str:
    """Build the autocomplete choice label for one fight.

    CI-20: Full two-name format "C1 vs C2" (mirrors duel style).
    Format: "#<ordinal> <c1> vs <c2> · <Context> · <date> · <OUTCOME>"
    e.g.    "#2 H'Soc vs SamX · Duel · 2026-06-03 · WON"
    Falls back to "vs <opponent>" when combatant1/2 names are absent (old rows).
    Truncated to 100 chars (Discord limit).
    """
    ordinal = item.get("ordinal", 1)
    c1_name = item.get("combatant1_name", "")
    c2_name = item.get("combatant2_name", "")
    if c1_name and c2_name:
        vs_str = f"{c1_name} vs {c2_name}"
    else:
        # Old-row fallback: opponent_name only
        opponent = item.get("opponent_name", "Unknown")
        vs_str = f"vs {opponent}"
    context_label = _CONTEXT_LABELS.get(item.get("context", ""), item.get("context", "?"))
    date_str = _format_date(str(item.get("created_at", "")))
    outcome_str = _OUTCOME_EMOJI.get(item.get("outcome", ""), item.get("outcome", "?").upper())
    label = f"#{ordinal} {vs_str} · {context_label} · {date_str} · {outcome_str}"
    return label[:100]


class CombatLogCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        # Per-user combat-log autocomplete cache (the flagship per-user case).
        # Key = (guild_id, discord_user_id, duel_type) where duel_type ∈
        # {None, "pvp", "bounty"} — None backs /combat-log (all fights), the other
        # two back /combat-log-pvp and /combat-log-bounty. The type is part of the
        # key (not a client-side filter) so each command's 25-row cap yields 25 of
        # its own type — a mixed cache would let frequent 48h bounty rows crowd out
        # the rarer 1-year duels. High key cardinality (every user who ever fought)
        # → LRU max_entries is MANDATORY. Short TTL is a dead-man switch: even if a
        # bot-core invalidate push is ever missed, a stale list self-corrects within
        # the TTL window (combat-log is a read-only history).
        self._combatlog_cache: AutocompleteCache[tuple[int, int, str | None], list[dict]] = AutocompleteCache(
            ttl_seconds=float(os.getenv("AUTOCOMPLETE_COMBATLOG_TTL_SECONDS", "120")),
            refresh_fn=self._fetch_combat_log,
            name="combatlog",
            max_entries=int(os.getenv("AUTOCOMPLETE_COMBATLOG_MAX_ENTRIES", "2000")),
        )
        flogger.debug("CombatLogCog initialized")

    async def cog_unload(self):
        await self.http_client.aclose()

    async def _fetch_combat_log(self, key: tuple[int, int, str | None]) -> list[dict]:
        """Refresh one user's recent fights from bot-core. Called by _combatlog_cache.

        Pre-computes ``_norm`` (lowercased choice label) at fill time so the hot
        autocomplete path performs only a pure substring check per keystroke.
        ``duel_type`` (None|"pvp"|"bounty") scopes the fetch server-side.
        """
        guild_id, user_id, duel_type = key
        params: dict = {"user_id": user_id, "guild_id": guild_id, "limit": 25}
        if duel_type is not None:
            params["duel_type"] = duel_type
        resp = await self.http_client.get(
            f"{api_base}/combat-log",
            params=params,
            timeout=3.0,
        )
        resp.raise_for_status()
        items = resp.json()
        for it in items:
            it["_norm"] = _make_choice_label(it).lower()
        return items

    # ------------------------------------------------------------------
    # Autocomplete
    # ------------------------------------------------------------------

    async def _battle_choices(
        self, guild_id: int, user_id: int, current: str, duel_type: str | None = None
    ) -> list[app_commands.Choice[int]]:
        """Build battle autocomplete choices for one player's recent fights.

        Served from the per-user ``_combatlog_cache`` (key = (guild_id,
        discord_user_id, duel_type)). ``duel_type`` (None|"pvp"|"bounty") scopes
        the list to a battle type. On a peek miss, a single 1.0s cold-fill
        populates the cache (the listing endpoint keys on the player's Discord id
        directly — single gate, well within the 3s autocomplete budget). Values
        are battle IDs (int).
        """
        key = (guild_id, user_id, duel_type)
        items = self._combatlog_cache.peek(key)
        if items is None:
            items = await self._combatlog_cache.get_with_timeout(key, timeout=1.0)
        if items is None:
            return []

        norm_current = current.lower()
        choices: list[app_commands.Choice[int]] = []
        for item in items:
            norm_label = item.get("_norm") or _make_choice_label(item).lower()
            if norm_current in norm_label:
                choices.append(app_commands.Choice(name=_make_choice_label(item), value=item["id"]))
        return choices[:25]

    async def battle_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        """Populate /combat-log battle param with the invoker's recent fights (all types)."""
        try:
            guild_id = interaction.guild_id
            if guild_id is None:
                return []
            return await self._battle_choices(guild_id, interaction.user.id, current)
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    async def pvp_battle_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        """Populate /combat-log-pvp battle param with the invoker's recent duels."""
        try:
            guild_id = interaction.guild_id
            if guild_id is None:
                return []
            return await self._battle_choices(guild_id, interaction.user.id, current, duel_type="pvp")
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    async def bounty_battle_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        """Populate /combat-log-bounty battle param with the invoker's recent bounty fights."""
        try:
            guild_id = interaction.guild_id
            if guild_id is None:
                return []
            return await self._battle_choices(guild_id, interaction.user.id, current, duel_type="bounty")
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    async def admin_battle_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        """Populate /admin_combat_log battle param with the SELECTED user's fights.

        The target user is read from ``interaction.namespace`` (dependent
        autocomplete).  Per discord.py docs, in autocomplete interactions
        Discord may omit resolved data, so the value is a Member/User OR a
        bare ``discord.Object`` — all carry ``.id``, which is all we need.
        ``None`` means the user param is still unfilled: return a hint choice
        with the ``_SELECT_USER_FIRST`` sentinel instead of an empty dropdown.
        """
        try:
            guild_id = interaction.guild_id
            if guild_id is None:
                return []
            target = interaction.namespace.user
            if target is None:
                return [app_commands.Choice(name="⬅️ Select a user first", value=_SELECT_USER_FIRST)]
            return await self._battle_choices(guild_id, target.id, current)
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    # ------------------------------------------------------------------
    # /combat-log <battle>
    # ------------------------------------------------------------------

    async def _show_battle(
        self, interaction: discord.Interaction, battle: int, public: bool, *, command: str
    ) -> None:
        """Fetch and render one battle's after-action report.

        Shared body for /combat-log, /combat-log-pvp and /combat-log-bounty — the
        detail fetch is identical regardless of type (the battle id is already
        scoped by whichever command's autocomplete produced it). ``command`` is
        only used for log lines.
        """
        # Errors always stay ephemeral regardless of `public`; success follows `public`.
        await interaction.response.defer(thinking=True, ephemeral=not public)
        flogger.info(
            f"{command} invoked: guild={interaction.guild_id} user={interaction.user.id}"
            f" battle={battle} public={public}"
        )

        try:
            resp = await self.http_client.get(
                f"{api_base}/combat-log/{battle}",
                params={"user_id": interaction.user.id},
                timeout=10,
            )

            if resp.status_code == 404:
                await interaction.followup.send(
                    "❌ Battle not found or you are not a combatant in that fight.",
                    ephemeral=True,
                )
                return

            resp.raise_for_status()
            data = resp.json()

            embed = self._build_detail_embed(data, interaction.user)
            await interaction.followup.send(embed=embed, ephemeral=not public)
            flogger.info(
                f"{command} success: guild={interaction.guild_id} user={interaction.user.id}"
                f" battle={battle} public={public}"
            )

        except httpx.HTTPStatusError as exc:
            flogger.error(
                f"{command} API error: guild={interaction.guild_id} user={interaction.user.id}"
                f" battle={battle} status={exc.response.status_code}"
            )
            await interaction.followup.send("⚠️ An error occurred while fetching the battle report.", ephemeral=True)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"{command} error: guild={interaction.guild_id} user={interaction.user.id}"
                f" battle={battle} error={exc}"
            )
            await interaction.followup.send("⚠️ An error occurred while fetching the battle report.", ephemeral=True)

    @app_commands.command(name="combat-log", description="Review the details of a past battle")
    @app_commands.describe(
        battle="Select a battle from your history",
        public="Make the response visible to everyone (default: False — only you see it)",
    )
    @app_commands.autocomplete(battle=battle_autocomplete)
    async def combat_log(self, interaction: discord.Interaction, battle: int, public: bool = False):
        """Show the after-action report for a past battle (any type)."""
        await self._show_battle(interaction, battle, public, command="/combat-log")

    @app_commands.command(name="combat-log-pvp", description="Review the details of a past PvP duel")
    @app_commands.describe(
        battle="Select a duel from your history",
        public="Make the response visible to everyone (default: False — only you see it)",
    )
    @app_commands.autocomplete(battle=pvp_battle_autocomplete)
    async def combat_log_pvp(self, interaction: discord.Interaction, battle: int, public: bool = False):
        """Show the after-action report for a past PvP duel."""
        await self._show_battle(interaction, battle, public, command="/combat-log-pvp")

    @app_commands.command(name="combat-log-bounty", description="Review the details of a past bounty fight")
    @app_commands.describe(
        battle="Select a bounty fight from your history",
        public="Make the response visible to everyone (default: False — only you see it)",
    )
    @app_commands.autocomplete(battle=bounty_battle_autocomplete)
    async def combat_log_bounty(self, interaction: discord.Interaction, battle: int, public: bool = False):
        """Show the after-action report for a past bounty (PvC) fight."""
        await self._show_battle(interaction, battle, public, command="/combat-log-bounty")

    # ------------------------------------------------------------------
    # /admin_combat_log <user> <battle>
    # ------------------------------------------------------------------

    @app_commands.command(name="admin_combat_log", description="[ADMIN] Review the details of a player's past battle")
    @app_commands.describe(
        user="The player whose battle history to review",
        battle="Select a battle from the player's history",
    )
    @app_commands.autocomplete(battle=admin_battle_autocomplete)
    async def admin_combat_log(self, interaction: discord.Interaction, user: discord.User, battle: int):
        """Show the after-action report for any player's past battle (admin only).

        The detail endpoint is queried with the SELECTED user's id, so the
        combatant authorization check and the outcome POV both resolve exactly
        as if that player had invoked /combat-log themselves.
        """
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return

        flogger.info(
            f"/admin_combat_log invoked: guild={interaction.guild_id} admin={interaction.user.id}"
            f" target={user.id} battle={battle}"
        )

        if battle == _SELECT_USER_FIRST:
            await interaction.followup.send(
                "⚠️ Please select a user first, then choose one of their battles.",
                ephemeral=True,
            )
            return

        try:
            resp = await self.http_client.get(
                f"{api_base}/combat-log/{battle}",
                params={"user_id": user.id},
                timeout=10,
            )

            if resp.status_code == 404:
                await interaction.followup.send(
                    "❌ Battle not found or the selected user is not a combatant in that fight.",
                    ephemeral=True,
                )
                return

            resp.raise_for_status()
            data = resp.json()

            embed = self._build_detail_embed(data, interaction.user)
            await interaction.followup.send(embed=embed, ephemeral=True)
            flogger.info(
                f"/admin_combat_log success: guild={interaction.guild_id} admin={interaction.user.id}"
                f" target={user.id} battle={battle}"
            )

        except httpx.HTTPStatusError as exc:
            flogger.error(
                f"/admin_combat_log API error: guild={interaction.guild_id} admin={interaction.user.id}"
                f" target={user.id} battle={battle} status={exc.response.status_code}"
            )
            await interaction.followup.send("⚠️ An error occurred while fetching the battle report.", ephemeral=True)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            flogger.error(
                f"/admin_combat_log error: guild={interaction.guild_id} admin={interaction.user.id}"
                f" target={user.id} battle={battle} error={exc}"
            )
            await interaction.followup.send("⚠️ An error occurred while fetching the battle report.", ephemeral=True)

    # ------------------------------------------------------------------
    # Embed builder
    # ------------------------------------------------------------------

    def _build_detail_embed(self, data: dict, user: discord.User | discord.Member) -> discord.Embed:
        """Build the after-action report embed for one battle."""
        battle_id = data.get("id", "?")
        context = data.get("context", "")
        context_label = _CONTEXT_LABELS.get(context, context.title())
        outcome = data.get("outcome", "unknown")
        is_stalemate = data.get("is_stalemate", False)

        # Color based on outcome
        if is_stalemate or outcome == "stalemate":
            color = discord.Color.greyple()
        elif outcome == "won":
            color = discord.Color.green()
        else:
            color = discord.Color.red()

        outcome_str = _OUTCOME_EMOJI.get(outcome, outcome.upper())
        title = f"⚔️ Battle #{battle_id} — {context_label} — {outcome_str}"

        embed = discord.Embed(title=title, color=color)

        # --- Summary section ---
        c1 = data.get("combatant1", {})
        c2 = data.get("combatant2", {})
        pvc_dr = data.get("pvc_damage_reduction", 0.0) or 0.0

        def _hp_line(c: dict) -> str:
            start = c.get("start_hp", {})
            final = c.get("final_hp", {})
            start_total = start.get("hull", 0) + start.get("armour", 0) + start.get("shield", 0)
            final_total = final.get("hull", 0) + final.get("armour", 0) + final.get("shield", 0)
            acc = c.get("accuracy")
            acc_str = f" | Accuracy: {acc:.0%}" if acc is not None else ""
            return f"HP: {start_total} → {final_total}{acc_str} | Dmg dealt: {c.get('damage_dealt', 0)}"

        def _stats_line(c: dict) -> str:
            # Surfaces the otherwise-invisible primary-weapon work (shots fired vs hit)
            # plus secondary/module usage counts. DESIGN_COMBAT_LOG_RECAP §6.
            fired = c.get("shots_fired", 0)
            hit = c.get("shots_hit", 0)
            return (
                f"Shots: {fired} fired / {hit} hit "
                f"| Secondaries: {c.get('secondaries_fired', 0)} "
                f"| Modules: {c.get('modules_activated', 0)}"
            )

        summary_lines = [
            f"**{c1.get('name', '?')}** ({c1.get('ship', '?')})",
            _hp_line(c1),
            _stats_line(c1),
        ]
        if pvc_dr > 0:
            summary_lines.append(f"\U0001f6e1️ PvC damage reduction: {round(pvc_dr * 100)}%")
        summary_lines += [
            "",
            f"**{c2.get('name', '?')}** ({c2.get('ship', '?')})",
            _hp_line(c2),
            _stats_line(c2),
        ]

        duration_s = data.get("duration_s", 0.0)
        summary_lines.append(f"\nDuration: {duration_s:.1f}s")

        winner_name = data.get("winner_name")
        if is_stalemate:
            summary_lines.append("**Result:** Stalemate")
        elif winner_name:
            summary_lines.append(f"**Winner:** {winner_name}")

        summary_value = "\n".join(summary_lines)
        if len(summary_value) > 1024:
            summary_value = summary_value[:1023] + "…"
        embed.add_field(name="\U0001f4ca Summary", value=summary_value, inline=False)

        # --- Key Events + Recurring sections ---
        # Discord hard limits: 1024 chars per field value; 25 fields per embed; 6000 chars total.
        # Both Key Events and Recurring are packed into <=1024-char chunks and spilled into
        # headerless continuation fields (zero-width space name) as needed.  They share ONE
        # field-count budget and ONE aggregate-char budget.  Key Events has precedence: when the
        # ceiling forces drops, Recurring content is dropped before Key Events content.
        # Priority tiers gate Key Events BUDGET DROPPING only — never display order.
        # key_events are already in chronological order from the server; rendered as-is.
        _FIELD_LIMIT = 1024
        _DETAIL_MAX = 200  # max chars per detail string (cosmetic, not hard-limit)
        _ZWSP = "\u200b"  # zero-width space → headerless continuation field
        # Discord allows 25 fields; 1 is consumed by Summary → 24 available for both sections.
        _MAX_TOTAL_SECTION_FIELDS = 24
        # 6000-char aggregate budget guard (Discord rejects embeds > 6000 chars total).
        _EMBED_BUDGET = 5800

        # Priority tiers: used ONLY for deciding which Key Events to drop when over budget.
        # Lower tier = higher priority = protected from drops.  Display order is always
        # chronological (as received from the server — never re-sorted here).
        _PRIORITY: dict[str, int] = {
            "Outcome": 0,
            "Engagement": 1,
            "HP milestone (50%)": 2,
            "HP milestone (25%)": 2,
            "Layer depleted": 3,
            "Nuke detonation": 4,
            "Shock blast": 4,
            "Module activated": 5,
            "Weapon in range": 6,
            "Ammo depleted": 6,
        }
        _DEFAULT_PRIORITY = 3

        _KE_HEADER = "\U0001f3af Key Events"
        _REC_HEADER = "\U0001f501 Recurring"

        key_events: list[dict] = data.get("key_events", [])
        recurring: list[str] = data.get("recurring", [])

        def _pack_lines(lns: list[str]) -> list[str]:
            """Pack lines into <=1024-char field chunks."""
            cks: list[str] = []
            cur: list[str] = []
            cur_len = 0
            for line in lns:
                add = len(line) + (1 if cur else 0)
                if cur and cur_len + add > _FIELD_LIMIT:
                    cks.append("\n".join(cur))
                    cur, cur_len = [], 0
                    add = len(line)
                cur.append(line)
                cur_len += add
            if cur:
                cks.append("\n".join(cur))
            return cks

        # Set footer early so len(embed) is accurate during budget estimation.
        embed.set_footer(text=f"Battle ID #{battle_id} | Requested by {user.display_name}")

        if key_events:
            # Build lines in CHRONOLOGICAL order (key_events already sorted by time_s).
            # Priority stored per-event for budget-drop decisions only.
            lines_with_pri: list[tuple[int, str]] = []
            for ev in key_events:
                time_s = ev.get("time_s", 0.0)
                detail = ev.get("detail", "")
                if len(detail) > _DETAIL_MAX:
                    detail = detail[: _DETAIL_MAX - 1] + "…"
                pri = _PRIORITY.get(ev.get("event_type", ""), _DEFAULT_PRIORITY)
                lines_with_pri.append((pri, f"`{time_s:6.1f}s` {detail}"))

            # Pack recurring lines to estimate how many fields they need (for shared budget).
            rec_lines: list[str] = list(recurring) if recurring else []
            rec_chunks_est: list[str] = _pack_lines(rec_lines) if rec_lines else []

            def _est_total_size(ke_chunks: list[str], rc_chunks: list[str]) -> int:
                """Estimate total embed char cost for both sections together."""
                ke_char = sum(len(c) for c in ke_chunks)
                ke_hdr = len(_KE_HEADER) + (len(ke_chunks) - 1) * len(_ZWSP) if ke_chunks else 0
                rc_char = sum(len(c) for c in rc_chunks)
                rc_hdr = len(_REC_HEADER) + (len(rc_chunks) - 1) * len(_ZWSP) if rc_chunks else 0
                return len(embed) + ke_char + ke_hdr + rc_char + rc_hdr

            # Greedy budget drop: if over limit, repeatedly drop the lowest-priority Key Event
            # (highest _PRIORITY value, then latest index as tiebreak) until we fit.
            # Recurring lines are kept in full during this phase (KE is reduced first).
            # If after exhausting all KE drops we still exceed limits, Recurring is trimmed
            # during the emit phase below.
            kept_indices = list(range(len(lines_with_pri)))
            while True:
                kept_lines = [lines_with_pri[i][1] for i in kept_indices]
                ke_chunks_est = _pack_lines(kept_lines)
                over_fields = (len(ke_chunks_est) + len(rec_chunks_est)) > _MAX_TOTAL_SECTION_FIELDS
                est_size = _est_total_size(ke_chunks_est, rec_chunks_est)
                if not over_fields and est_size <= _EMBED_BUDGET:
                    break
                if not kept_indices:
                    break
                drop_idx = max(kept_indices, key=lambda i: (lines_with_pri[i][0], i))
                kept_indices.remove(drop_idx)

            dropped_ke_count = len(lines_with_pri) - len(kept_indices)
            kept_lines = [lines_with_pri[i][1] for i in kept_indices]
            ke_chunks = _pack_lines(kept_lines)

            # --- Emit Key Events fields ---
            ke_field_start_idx = len(embed.fields)  # embed field index where KE section starts
            shown_ke_chunks: list[str] = []
            for i, chunk in enumerate(ke_chunks):
                name = _KE_HEADER if i == 0 else _ZWSP
                value = chunk[:_FIELD_LIMIT]
                if shown_ke_chunks and len(embed) + len(name) + len(value) > _EMBED_BUDGET:
                    dropped_ke_count += sum(c.count("\n") + 1 for c in ke_chunks[i:])
                    break
                embed.add_field(name=name, value=value, inline=False)
                shown_ke_chunks.append(chunk)

            # Surface Key Events omission as a trailer on the last shown KE field.
            if dropped_ke_count and shown_ke_chunks:
                ke_trailer = f"\n…(+{dropped_ke_count} more event{'s' if dropped_ke_count != 1 else ''} omitted)"
                last_ke_embed_idx = ke_field_start_idx + len(shown_ke_chunks) - 1
                last_ke_name = _KE_HEADER if len(shown_ke_chunks) == 1 else _ZWSP
                last_ke_value = shown_ke_chunks[-1][: _FIELD_LIMIT - len(ke_trailer)] + ke_trailer
                embed.set_field_at(last_ke_embed_idx, name=last_ke_name, value=last_ke_value, inline=False)

            # --- Recurring section ---
            # Uses the same chunk-packing and headerless-continuation machinery as Key Events.
            # Remaining field and char budget after Key Events section.
            if rec_lines:
                fields_used_by_ke = len(shown_ke_chunks)
                rec_budget_fields = _MAX_TOTAL_SECTION_FIELDS - fields_used_by_ke
                rec_chunks = _pack_lines(rec_lines)
                dropped_rec_count = 0

                rec_field_start_idx = ke_field_start_idx + len(shown_ke_chunks)
                shown_rec_chunks: list[str] = []
                for i, chunk in enumerate(rec_chunks):
                    if len(shown_rec_chunks) >= rec_budget_fields:
                        # No more field slots for Recurring.
                        dropped_rec_count += chunk.count("\n") + 1
                        continue
                    name = _REC_HEADER if i == 0 else _ZWSP
                    value = chunk[:_FIELD_LIMIT]
                    if len(embed) + len(name) + len(value) > _EMBED_BUDGET:
                        dropped_rec_count += chunk.count("\n") + 1
                        continue
                    embed.add_field(name=name, value=value, inline=False)
                    shown_rec_chunks.append(chunk)

                # Surface Recurring omission as a trailer on the last shown Recurring field.
                if dropped_rec_count and shown_rec_chunks:
                    rec_trailer = (
                        f"\n…(+{dropped_rec_count} more bullet{'s' if dropped_rec_count != 1 else ''} omitted)"
                    )
                    last_rec_embed_idx = rec_field_start_idx + len(shown_rec_chunks) - 1
                    last_rec_name = _REC_HEADER if len(shown_rec_chunks) == 1 else _ZWSP
                    last_rec_value = shown_rec_chunks[-1][: _FIELD_LIMIT - len(rec_trailer)] + rec_trailer
                    embed.set_field_at(last_rec_embed_idx, name=last_rec_name, value=last_rec_value, inline=False)

            return embed

        # No key events: still render Recurring if present, then a placeholder KE field.
        embed.add_field(name=_KE_HEADER, value="*(no secondary weapons or modules used)*", inline=False)
        if recurring:
            rec_chunks_no_ke = _pack_lines(list(recurring))
            fields_used_no_ke = 1  # the placeholder KE field above
            rec_budget_no_ke = _MAX_TOTAL_SECTION_FIELDS - fields_used_no_ke
            for i, chunk in enumerate(rec_chunks_no_ke):
                if i >= rec_budget_no_ke:
                    break
                name = _REC_HEADER if i == 0 else _ZWSP
                value = chunk[:_FIELD_LIMIT]
                if len(embed) + len(name) + len(value) > _EMBED_BUDGET:
                    break
                embed.add_field(name=name, value=value, inline=False)

        return embed

    @combat_log.error
    async def combat_log_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /combat-log", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)

    @admin_combat_log.error
    async def admin_combat_log_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /admin_combat_log", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(CombatLogCog(bot))
