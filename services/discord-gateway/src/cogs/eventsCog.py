"""Events cog — /admin_event_* admin commands + /events + /event_leaderboard player commands.

Custom stat-race challenges (issue #30, spec §6).
Cache pipeline mirrors bountyCog exactly (per-guild, TTL=1200s, warm+refresh+push).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta, timezone

import discord
import httpx
from cogs._shared.autocomplete_cache import AutocompleteCache
from cogs._shared.confirm_view import ConfirmView
from cogs.adminCog import _check_is_admin
from discord import app_commands
from discord.ext import commands
from shared import bblogger
from utils.autocomplete_utils import normalize_for_search
from utils.timestamp_utils import event_status_label, iso_to_discord_ts

flogger = bblogger.get_logger("discord-gateway-EventsCog")
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"eventsCog loading with API_BASE_URL: {api_base}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Static place → (rank_from, rank_to).  Participation → (None, None).
_PLACE_ORDINALS = {
    "1st": (1, 1), "2nd": (2, 2), "3rd": (3, 3), "4th": (4, 4),
    "5th": (5, 5), "6th": (6, 6), "7th": (7, 7), "8th": (8, 8),
    "9th": (9, 9), "10th": (10, 10),
}

# Static choices for /admin_event_add_prize `place` param.
_PLACE_CHOICES = [
    app_commands.Choice(name=p, value=p) for p in [*list(_PLACE_ORDINALS), "Top N", "Participation"]
]

# Static choices for /admin_event_add_prize `type` param.
_PRIZE_TYPE_CHOICES = [
    app_commands.Choice(name="Credits", value="Credits"),
    app_commands.Choice(name="Ship", value="Ship"),
    app_commands.Choice(name="Primary Weapon", value="Primary"),
    app_commands.Choice(name="Secondary Weapon", value="Secondary"),
    app_commands.Choice(name="Turret", value="Turret"),
    app_commands.Choice(name="Module", value="Module"),
]

# prize type → (kind, item_category)
_PRIZE_TYPE_MAP: dict[str, tuple[str, str | None]] = {
    "Credits": ("credits", None),
    "Ship": ("ship", None),
    "Primary": ("item", "primary_weapon"),
    "Secondary": ("item", "secondary_weapon"),
    "Turret": ("item", "turret_weapon"),
    "Module": ("item", "module"),
}

# Static UTC offset choices for /admin_event_start `utc_offset`.
_UTC_OFFSET_CHOICES = [
    app_commands.Choice(name="UTC-12 (Baker Island)", value="-12"),
    app_commands.Choice(name="UTC-11 (Samoa)", value="-11"),
    app_commands.Choice(name="UTC-10 (Hawaii)", value="-10"),
    app_commands.Choice(name="UTC-9 (Alaska)", value="-9"),
    app_commands.Choice(name="UTC-8 (Pacific US)", value="-8"),
    app_commands.Choice(name="UTC-7 (Mountain US)", value="-7"),
    app_commands.Choice(name="UTC-6 (Central US)", value="-6"),
    app_commands.Choice(name="UTC-5 (Eastern US)", value="-5"),
    app_commands.Choice(name="UTC-4 (Atlantic)", value="-4"),
    app_commands.Choice(name="UTC-3 (Brazil)", value="-3"),
    app_commands.Choice(name="UTC-2", value="-2"),
    app_commands.Choice(name="UTC-1 (Azores)", value="-1"),
    app_commands.Choice(name="UTC (London)", value="0"),
    app_commands.Choice(name="UTC+1 (Central Europe)", value="1"),
    app_commands.Choice(name="UTC+2 (Eastern Europe)", value="2"),
    app_commands.Choice(name="UTC+3 (Moscow)", value="3"),
    app_commands.Choice(name="UTC+4 (Gulf)", value="4"),
    app_commands.Choice(name="UTC+5 (Pakistan)", value="5"),
    app_commands.Choice(name="UTC+6 (Bangladesh)", value="6"),
    app_commands.Choice(name="UTC+7 (Thailand)", value="7"),
    app_commands.Choice(name="UTC+8 (Singapore)", value="8"),
    app_commands.Choice(name="UTC+9 (Japan/Korea)", value="9"),
    app_commands.Choice(name="UTC+10 (Sydney)", value="10"),
    app_commands.Choice(name="UTC+11 (Solomon Is)", value="11"),
    app_commands.Choice(name="UTC+12 (New Zealand)", value="12"),
]

# State filter choices for /admin_event_list.
_STATE_CHOICES = [
    app_commands.Choice(name="All", value="all"),
    app_commands.Choice(name="Draft", value="draft"),
    app_commands.Choice(name="Scheduled", value="scheduled"),
    app_commands.Choice(name="Active", value="active"),
    app_commands.Choice(name="Ended", value="ended"),
    app_commands.Choice(name="Cancelled", value="cancelled"),
]



def _prize_label(p: dict) -> str:
    """Human-readable prize label for autocomplete."""
    rank_from = p.get("rank_from")
    rank_to = p.get("rank_to")
    kind = p.get("kind", "?")
    item_ref = p.get("item_ref") or ""
    qty = p.get("qty", 1)

    if rank_from is None:
        place = "Participation"
    elif rank_from == rank_to:
        suffixes = {1: "st", 2: "nd", 3: "rd"}
        place = f"{rank_from}{suffixes.get(rank_from, 'th')}"
    else:
        place = f"Top {rank_to}"

    if kind == "credits":
        return f"#{p.get('id')} · {place} · {qty:,} credits"
    return f"#{p.get('id')} · {place} · {item_ref or kind} x{qty}"


class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))

        # Per-guild events cache — key = int guild_id, TTL = 1200s (20 min dead-man switch).
        # Refresh via bot-core push after every mutation; TTL is the fallback.
        self._events_cache: AutocompleteCache[int, list[dict]] = AutocompleteCache(
            ttl_seconds=1200.0,
            refresh_fn=self._fetch_events,
            name="events",
        )

        # Event-type registry: TTL=None (static), fetched from /events/types on first use.
        # ponytail: module-level dict would share across tests; cog-instance keeps it isolated.
        self._types_cache: AutocompleteCache[str, list[dict]] = AutocompleteCache(
            ttl_seconds=None,
            refresh_fn=self._fetch_event_types,
            name="events-types",
        )
        flogger.debug("EventsCog initialized")

    async def cog_unload(self):
        await self.http_client.aclose()

    # ------------------------------------------------------------------
    # Data fetchers
    # ------------------------------------------------------------------

    async def _fetch_events(self, guild_id: int) -> list[dict]:
        """Fetch all events for a guild. Precomputes _norm for hot-path autocomplete."""
        try:
            resp = await self.http_client.get(
                f"{api_base}/events/guild/{guild_id}",
                timeout=5,
            )
            if resp.status_code != 200:
                return []
            events = resp.json()
            for e in events:
                label = f"#{e.get('id')} · {e.get('type_display', e.get('type_slug', ''))} · {event_status_label(e)}"
                e["_norm"] = normalize_for_search(label)
            return events
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    async def _fetch_event_types(self, _key: str) -> list[dict]:
        """Fetch event type registry from bot-core /events/types. Raises on error."""
        resp = await self.http_client.get(f"{api_base}/events/types", timeout=5)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Autocomplete helpers
    # ------------------------------------------------------------------

    async def _events_autocomplete(
        self, interaction: discord.Interaction, current: str, *, states: set[str]
    ) -> list[app_commands.Choice[str]]:
        """Zero-HTTP hot-path event autocomplete, filtered by allowed states.

        Peek → cold-fill within 1.0s budget (same contract as bounty_autocomplete).
        """
        try:
            guild_id = interaction.guild_id
            events = self._events_cache.peek(guild_id)
            if events is None:
                events = await self._events_cache.get_with_timeout(guild_id, timeout=1.0)
            if events is None:
                return []
            norm_current = normalize_for_search(current)
            choices = []
            for e in events:
                if states and e.get("state") not in states:
                    continue
                label = (
                    f"#{e.get('id')} · {e.get('type_display', e.get('type_slug', ''))} · {event_status_label(e)}"
                )
                norm_label = e.get("_norm") or normalize_for_search(label)
                if norm_current in norm_label:
                    choices.append(app_commands.Choice(name=label[:100], value=str(e["id"])))
            return choices[:25]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    async def _type_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for event types — substring over registry fetched from /events/types."""
        try:
            types = self._types_cache.peek("all")
            if types is None:
                types = await self._types_cache.get_with_timeout("all", timeout=1.0)
            if types is None:
                return []
            norm_current = normalize_for_search(current)
            return [
                app_commands.Choice(
                    name=f"{t['category'].title()} · {t['display_name']}"[:100],
                    value=t["slug"],
                )
                for t in types
                if norm_current in normalize_for_search(t.get("display_name", ""))
                or norm_current in normalize_for_search(t.get("category", ""))
            ][:25]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    async def _item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Item autocomplete filtered by the already-chosen prize `type` in namespace.

        Uses AdminCog's item/ship catalog caches — import the live cog instance.
        """
        try:
            prize_type = getattr(interaction.namespace, "type", None)
            norm_current = normalize_for_search(current)

            admin_cog = self.bot.get_cog("AdminCog")
            if admin_cog is None:
                return []

            if prize_type == "Ship":
                names = admin_cog._ship_catalog.peek("all")
                if names is None:
                    names = await admin_cog._ship_catalog.get_with_timeout("all", timeout=1.0)
                names = names or []
                return [
                    app_commands.Choice(name=n, value=n)
                    for n in names
                    if norm_current in normalize_for_search(n)
                ][:25]

            if prize_type == "Credits":
                return []  # no item needed for credits

            # Item types: map to catalog category (reuse module-level _PRIZE_TYPE_MAP)
            _, _cat = _PRIZE_TYPE_MAP.get(prize_type or "", (None, None))
            category = _cat
            if not category:
                return []  # No type selected — no suggestions (less confusing than dumping everything)
            categories = (category,)

            choices: list[app_commands.Choice[str]] = []
            seen: set[str] = set()
            cold_fills = 0
            for cat in categories:
                cat_names = admin_cog._item_catalog.peek(cat)
                if cat_names is None and cold_fills < 2:
                    cat_names = await admin_cog._item_catalog.get_with_timeout(cat, timeout=1.0)
                    cold_fills += 1
                cat_names = cat_names or []
                for name in cat_names:
                    if name and name not in seen and norm_current in normalize_for_search(name):
                        seen.add(name)
                        choices.append(app_commands.Choice(name=name, value=name))
            return choices[:25]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    async def _prize_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for prizes on the selected event (cold HTTP call, admin-only path)."""
        try:
            event_id = getattr(interaction.namespace, "event", None)
            if not event_id:
                return []
            resp = await self.http_client.get(f"{api_base}/events/{event_id}", timeout=5)
            if resp.status_code != 200:
                return []
            data = resp.json()
            prizes = data.get("prizes", [])
            norm_current = normalize_for_search(current)
            choices = []
            for p in prizes:
                label = _prize_label(p)
                if norm_current in normalize_for_search(label):
                    choices.append(app_commands.Choice(name=label[:100], value=str(p["id"])))
            return choices[:25]
        except Exception:  # pylint: disable=broad-exception-caught
            return []

    # Per-command event selectors (per-state wrappers so @app_commands.autocomplete can bind them).
    async def _ac_event_draft_active(self, interaction, current):
        return await self._events_autocomplete(interaction, current, states={"draft", "active"})

    async def _ac_event_draft(self, interaction, current):
        return await self._events_autocomplete(interaction, current, states={"draft"})

    async def _ac_event_draft_scheduled(self, interaction, current):
        return await self._events_autocomplete(interaction, current, states={"draft", "scheduled"})

    async def _ac_event_active(self, interaction, current):
        return await self._events_autocomplete(interaction, current, states={"active"})

    async def _ac_event_deletable(self, interaction, current):
        return await self._events_autocomplete(interaction, current, states={"draft", "scheduled", "cancelled"})

    async def _ac_event_player(self, interaction, current):
        """Player-facing: scheduled/active/ended via _events_autocomplete, then drop ended > 7 days."""
        choices = await self._events_autocomplete(interaction, current, states={"scheduled", "active", "ended"})
        cutoff = datetime.now(UTC) - timedelta(days=7)
        # Post-filter: drop ended events older than 7 days.
        # Cache entries have "ends_at" and "state" attached by _fetch_events.
        guild_id = interaction.guild_id
        events = self._events_cache.peek(guild_id) or []
        by_id = {str(e["id"]): e for e in events}
        out = []
        for ch in choices:
            e = by_id.get(ch.value, {})
            if e.get("state") == "ended":
                ts_str = e.get("ends_at")
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if ts < cutoff:
                            continue
                    except (ValueError, TypeError):
                        pass
            out.append(ch)
        return out

    # ------------------------------------------------------------------
    # Shared helpers — reduce boilerplate in admin commands
    # ------------------------------------------------------------------

    async def _admin_gate(self, interaction: discord.Interaction) -> bool:
        """Defer ephemeral + check admin; sends ❌ and returns False if not admin."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ Admin privileges required.", ephemeral=True)
            return False
        return True

    async def _api(
        self, interaction: discord.Interaction, method: str, url: str, **kw
    ) -> httpx.Response | None:
        """Call the bot-core API; surface HTTPStatusError as ❌ followup, return None on error."""
        try:
            resp = await getattr(self.http_client, method)(url, **kw)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            await interaction.followup.send(f"❌ {_extract_detail(exc)}", ephemeral=True)
            return None
        except Exception as exc:  # pylint: disable=broad-exception-caught
            await interaction.followup.send(f"❌ Unexpected error: {exc}", ephemeral=True)
            return None

    async def _confirm(self, interaction: discord.Interaction, embed: discord.Embed, action: str) -> bool | None:
        """Show a ConfirmView, wait, return True/False/None (timeout)."""
        view = ConfirmView(action=action, timeout=60)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        await view.wait()
        if view.result is None:
            await interaction.followup.send("⏱️ Timed out — no changes made.", ephemeral=True)
        elif not view.result:
            await interaction.followup.send("❌ Cancelled — no changes made.", ephemeral=True)
        return view.result

    # ------------------------------------------------------------------
    # Admin commands
    # ------------------------------------------------------------------

    @app_commands.command(name="admin_event_create", description="[ADMIN] Create a new draft event")
    @app_commands.describe(
        type="Event type (autocomplete from registry)",
        duration_days="Duration in days (default 7)",
        division="Division filter: Bronze/Silver/Gold/Platinum",
        subtype="Weapon subtype (for weapon-type events)",
        module="Module type (for module events)",
        weapon="Weapon category (for weapon-kill events)",
    )
    @app_commands.autocomplete(type=_type_autocomplete)
    async def admin_event_create(
        self,
        interaction: discord.Interaction,
        type: str,
        duration_days: int = 7,
        division: str | None = None,
        subtype: str | None = None,
        module: str | None = None,
        weapon: str | None = None,
    ):
        if not await self._admin_gate(interaction):
            return

        params: dict[str, str] = {}
        if division:
            params["division"] = division
        if subtype:
            params["subtype"] = subtype
        if module:
            params["module"] = module
        if weapon:
            params["weapon"] = weapon

        resp = await self._api(
            interaction, "post", f"{api_base}/events",
            json={"guild_id": interaction.guild_id, "type_slug": type,
                  "duration_days": duration_days, "params": params},
            params={"user_id": interaction.user.id},
            timeout=10,
        )
        if resp is None:
            return

        event = resp.json()
        self._events_cache.invalidate(interaction.guild_id)
        flogger.info(
            f"/admin_event_create: guild={interaction.guild_id} event_id={event.get('id')}"
            f" by user={interaction.user.id}"
        )
        await interaction.followup.send(
            f"✅ Draft event **#{event['id']}** created (type `{type}`, {duration_days}d).\n"
            f"Use `/admin_event_add_prize` to add prizes, then `/admin_event_start` to launch.",
            ephemeral=True,
        )

    @app_commands.command(name="admin_event_add_prize", description="[ADMIN] Add a prize to a draft/active event")
    @app_commands.describe(
        event="Event to add the prize to (draft or active)",
        place="Prize place: 1st–10th, Top N, or Participation",
        type="Prize type",
        item="Item/ship name (leave blank for Credits)",
        qty="Quantity / credit amount",
        top_n="Upper rank for 'Top N' prizes (e.g. 5 for Top 5)",
    )
    @app_commands.autocomplete(event=_ac_event_draft_active, item=_item_autocomplete)
    @app_commands.choices(place=_PLACE_CHOICES, type=_PRIZE_TYPE_CHOICES)
    async def admin_event_add_prize(
        self,
        interaction: discord.Interaction,
        event: str,
        place: str,
        type: str,
        qty: int = 1,
        item: str | None = None,
        top_n: int = 10,
    ):
        if not await self._admin_gate(interaction):
            return

        if place in _PLACE_ORDINALS:
            rank_from, rank_to = _PLACE_ORDINALS[place]
        elif place == "Top N":
            rank_from, rank_to = 1, top_n
        else:
            rank_from, rank_to = None, None

        kind, _ = _PRIZE_TYPE_MAP.get(type, ("credits", None))
        body: dict = {"rank_from": rank_from, "rank_to": rank_to, "kind": kind, "qty": qty}
        if kind in ("item", "ship"):
            if not item:
                await interaction.followup.send(f"❌ `item` is required for {type} prizes.", ephemeral=True)
                return
            body["item_ref"] = item

        resp = await self._api(
            interaction, "post", f"{api_base}/events/{event}/prizes",
            json=body,
            params={"guild_id": interaction.guild_id, "user_id": interaction.user.id},
            timeout=10,
        )
        if resp is None:
            return

        self._events_cache.invalidate(interaction.guild_id)
        prize = resp.json()
        flogger.info(f"/admin_event_add_prize: event={event} prize_id={prize.get('id')} by user={interaction.user.id}")
        await interaction.followup.send(
            f"✅ Prize **#{prize['id']}** added to event #{event}: `{place}` — {type}" + (f" · {item}" if item else ""),
            ephemeral=True,
        )

    @app_commands.command(name="admin_event_remove_prize", description="[ADMIN] Remove a prize from a draft event")
    @app_commands.describe(
        event="Event to remove the prize from (draft only)",
        prize="Prize to remove (autocomplete from event's prizes)",
    )
    @app_commands.autocomplete(event=_ac_event_draft, prize=_prize_autocomplete)
    async def admin_event_remove_prize(
        self,
        interaction: discord.Interaction,
        event: str,
        prize: str,
    ):
        if not await self._admin_gate(interaction):
            return

        resp = await self._api(
            interaction, "delete", f"{api_base}/events/{event}/prizes/{prize}",
            params={"guild_id": interaction.guild_id, "user_id": interaction.user.id},
            timeout=10,
        )
        if resp is None:
            return

        self._events_cache.invalidate(interaction.guild_id)
        flogger.info(f"/admin_event_remove_prize: event={event} prize={prize} by user={interaction.user.id}")
        await interaction.followup.send(f"✅ Prize #{prize} removed from event #{event}.", ephemeral=True)

    @app_commands.command(name="admin_event_start", description="[ADMIN] Start an event now or schedule it")
    @app_commands.describe(
        event="Event to start (draft or scheduled)",
        at="Scheduled start time: YYYY-MM-DD HH:MM (leave blank for now)",
        utc_offset="Your timezone offset (default UTC)",
    )
    @app_commands.autocomplete(event=_ac_event_draft_scheduled)
    @app_commands.choices(utc_offset=_UTC_OFFSET_CHOICES)
    async def admin_event_start(
        self,
        interaction: discord.Interaction,
        event: str,
        at: str | None = None,
        utc_offset: str = "0",
    ):
        if not await self._admin_gate(interaction):
            return

        body: dict = {}
        scheduled_ts: datetime | None = None

        if at:
            try:
                naive = datetime.strptime(at, "%Y-%m-%d %H:%M")
            except ValueError:
                await interaction.followup.send(
                    "❌ Invalid time format. Use `YYYY-MM-DD HH:MM` (e.g. `2026-10-01 18:00`).",
                    ephemeral=True,
                )
                return

            offset_hours = float(utc_offset)
            tz = timezone(timedelta(hours=offset_hours))
            scheduled_ts = naive.replace(tzinfo=tz).astimezone(UTC)

            now = datetime.now(UTC)
            if scheduled_ts <= now:
                await interaction.followup.send("❌ Scheduled time must be in the future.", ephemeral=True)
                return
            if scheduled_ts > now + timedelta(days=90):
                await interaction.followup.send("❌ Scheduled time must be within 90 days.", ephemeral=True)
                return

            unix = int(scheduled_ts.timestamp())
            body["scheduled_start_at"] = scheduled_ts.isoformat()
            embed = discord.Embed(
                title="Confirm Schedule",
                description=(
                    f"Schedule event **#{event}** to start:\n"
                    f"**<t:{unix}:F>** (<t:{unix}:R>)\n\nClick **Confirm** to schedule."
                ),
                color=discord.Color.blue(),
            )
        else:
            embed = discord.Embed(
                title="Confirm Start",
                description=f"Start event **#{event}** **now**?\n\nClick **Confirm** to proceed.",
                color=discord.Color.green(),
            )

        if not await self._confirm(interaction, embed, f"start event #{event}"):
            return

        resp = await self._api(
            interaction, "post", f"{api_base}/events/{event}/start",
            json=body,
            params={"guild_id": interaction.guild_id, "user_id": interaction.user.id},
            timeout=10,
        )
        if resp is None:
            return

        self._events_cache.invalidate(interaction.guild_id)
        result = resp.json()
        status = result.get("status", "")
        flogger.info(f"/admin_event_start: event={event} status={status} by user={interaction.user.id}")
        if status == "scheduled" and scheduled_ts:
            unix = int(scheduled_ts.timestamp())
            await interaction.followup.send(
                f"✅ Event **#{event}** scheduled for <t:{unix}:F> (<t:{unix}:R>).", ephemeral=True
            )
        else:
            await interaction.followup.send(f"✅ Event **#{event}** is now **active**.", ephemeral=True)

    @app_commands.command(name="admin_event_end", description="[ADMIN] End an active event (with or without payout)")
    @app_commands.describe(
        event="Active event to end",
        payout="Pay out prizes to qualified players?",
        reason="Optional reason for ending early",
    )
    @app_commands.autocomplete(event=_ac_event_active)
    @app_commands.choices(
        payout=[
            app_commands.Choice(name="Yes — pay out prizes", value="yes"),
            app_commands.Choice(name="No — end without payout", value="no"),
        ]
    )
    async def admin_event_end(
        self,
        interaction: discord.Interaction,
        event: str,
        payout: str,
        reason: str | None = None,
    ):
        if not await self._admin_gate(interaction):
            return

        do_payout = payout.lower() == "yes"
        embed = discord.Embed(
            title="Confirm End Event",
            description=(
                f"End event **#{event}**?\n"
                f"Payout: **{'Yes' if do_payout else 'No'}**"
                + (f"\nReason: {reason}" if reason else "")
                + "\n\nThis cannot be undone."
            ),
            color=discord.Color.orange(),
        )
        if not await self._confirm(interaction, embed, f"end event #{event}"):
            return

        resp = await self._api(
            interaction, "post", f"{api_base}/events/{event}/end",
            json={"payout": do_payout, "reason": reason},
            params={"guild_id": interaction.guild_id, "user_id": interaction.user.id},
            timeout=30,
        )
        if resp is None:
            return

        self._events_cache.invalidate(interaction.guild_id)
        result = resp.json()
        winners = result.get("winners_count", 0)
        flogger.info(f"/admin_event_end: event={event} payout={do_payout} by user={interaction.user.id}")
        await interaction.followup.send(
            f"✅ Event **#{event}** ended."
            + (f" {winners} player(s) received prizes." if do_payout and winners else ""),
            ephemeral=True,
        )

    @app_commands.command(name="admin_event_delete", description="[ADMIN] Delete a draft/scheduled/cancelled event")
    @app_commands.describe(event="Event to permanently delete")
    @app_commands.autocomplete(event=_ac_event_deletable)
    async def admin_event_delete(
        self,
        interaction: discord.Interaction,
        event: str,
    ):
        if not await self._admin_gate(interaction):
            return

        embed = discord.Embed(
            title="⚠️ Confirm Delete",
            description=f"Permanently delete event **#{event}**?\n\nThis cannot be undone.",
            color=discord.Color.red(),
        )
        if not await self._confirm(interaction, embed, f"delete event #{event}"):
            return

        resp = await self._api(
            interaction, "delete", f"{api_base}/events/{event}",
            params={"guild_id": interaction.guild_id, "user_id": interaction.user.id},
            timeout=10,
        )
        if resp is None:
            return

        self._events_cache.invalidate(interaction.guild_id)
        flogger.info(f"/admin_event_delete: event={event} by user={interaction.user.id}")
        await interaction.followup.send(f"✅ Event **#{event}** deleted.", ephemeral=True)

    @app_commands.command(name="admin_event_list", description="[ADMIN] List events for this guild")
    @app_commands.describe(state="Filter by state (default: all)")
    @app_commands.choices(state=_STATE_CHOICES)
    async def admin_event_list(
        self,
        interaction: discord.Interaction,
        state: str = "all",
    ):
        if not await self._admin_gate(interaction):
            return

        params: dict = {"guild_id": interaction.guild_id}
        if state != "all":
            params["state"] = state

        resp = await self._api(
            interaction, "get", f"{api_base}/events/guild/{interaction.guild_id}",
            params=params, timeout=10,
        )
        if resp is None:
            return

        events = resp.json()
        if not events:
            await interaction.followup.send("No events found.", ephemeral=True)
            return

        lines = []
        for e in events:
            ts = ""
            if e.get("started_at"):
                ts = f" · started {iso_to_discord_ts(e['started_at'], 'R')}"
            elif e.get("scheduled_start_at"):
                ts = f" · starts {iso_to_discord_ts(e['scheduled_start_at'], 'R')}"
            lines.append(
                f"**#{e['id']}** `{e['state']}` {e.get('type_display', e.get('type_slug', '?'))}"
                f" ({e.get('duration_days', '?')}d){ts}"
            )

        embed = discord.Embed(
            title=f"Events — {interaction.guild.name}",
            description="\n".join(lines[:20]),
            color=discord.Color.blurple(),
        )
        if len(events) > 20:
            embed.set_footer(text=f"Showing first 20 of {len(events)} events")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # Player commands
    # ------------------------------------------------------------------

    @app_commands.command(name="events", description="View live and upcoming events (or details for one event)")
    @app_commands.describe(event="Event to view in detail")
    @app_commands.autocomplete(event=_ac_event_player)
    async def events(
        self,
        interaction: discord.Interaction,
        event: str | None = None,
    ):
        await interaction.response.defer(thinking=True)

        if event:
            # Detail view for a single event.
            try:
                resp = await self.http_client.get(f"{api_base}/events/{event}", timeout=10)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                await interaction.followup.send(f"❌ {_extract_detail(exc)}", ephemeral=True)
                return

            e = resp.json()
            et_display = e.get("type_display", e.get("type_slug", "?"))
            embed = discord.Embed(
                title=f"Event #{e['id']} — {et_display}",
                description=e.get("rules_text") or "No rules text.",
                color=discord.Color.gold(),
            )
            embed.add_field(name="State", value=e.get("state", "?"), inline=True)
            if e.get("started_at"):
                embed.add_field(name="Started", value=iso_to_discord_ts(e["started_at"], "F"), inline=True)
            if e.get("ends_at"):
                ends_ts = f"{iso_to_discord_ts(e['ends_at'], 'F')} ({iso_to_discord_ts(e['ends_at'], 'R')})"
                embed.add_field(name="Ends", value=ends_ts, inline=False)
            if e.get("scheduled_start_at"):
                sched_ts = iso_to_discord_ts(e["scheduled_start_at"], "F")
                embed.add_field(name="Scheduled Start", value=sched_ts, inline=False)

            prizes = e.get("prizes", [])
            if prizes:
                lines = [_prize_label(p) for p in prizes]
                embed.add_field(name="Prizes", value="\n".join(lines[:10]) or "None", inline=False)

            await interaction.followup.send(embed=embed)
        else:
            # Summary of active + scheduled events.
            try:
                resp = await self.http_client.get(
                    f"{api_base}/events/guild/{interaction.guild_id}",
                    params={"state": "active,scheduled"},
                    timeout=10,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                await interaction.followup.send(f"❌ {_extract_detail(exc)}", ephemeral=True)
                return

            events = resp.json()
            if not events:
                await interaction.followup.send("No active or upcoming events right now.", ephemeral=True)
            else:
                embed = discord.Embed(
                    title=f"Events — {interaction.guild.name}",
                    color=discord.Color.gold(),
                )
                for e in events[:10]:
                    et_display = e.get("type_display", e.get("type_slug", "?"))
                    if e.get("state") == "active" and e.get("ends_at"):
                        ts_str = f"Ends {iso_to_discord_ts(e['ends_at'], 'R')}"
                    elif e.get("scheduled_start_at"):
                        ts_str = f"Starts {iso_to_discord_ts(e['scheduled_start_at'], 'R')}"
                    else:
                        ts_str = e.get("state", "?")
                    embed.add_field(
                        name=f"#{e['id']} {et_display}",
                        value=f"{ts_str} · {e.get('prize_count', 0)} prize(s)",
                        inline=False,
                    )
                await interaction.followup.send(embed=embed)

        # Sync player notification roles — non-fatal, best-effort (slice 6 expands this).
        try:
            player_cog = self.bot.get_cog("PlayerCog")
            if player_cog is not None and isinstance(interaction.user, discord.Member):
                player_resp = await self.http_client.get(
                    f"{api_base}/players/",
                    params={"discord_id": interaction.user.id, "guild_id": interaction.guild_id},
                    timeout=5,
                )
                if player_resp.status_code == 200:
                    players = player_resp.json()
                    if players:
                        await player_cog._sync_player_notification_roles(
                            interaction.guild,
                            interaction.user,
                            interaction.guild_id,
                            players[0],
                        )
        except Exception:  # pylint: disable=broad-exception-caught
            pass  # non-fatal — primary command already responded

    @app_commands.command(
        name="event_leaderboard",
        description="View all-time medal tally, or standings for a specific event",
    )
    @app_commands.describe(
        event="Event for standings (top 10 + your rank)",
        type="Filter medals by event type",
    )
    @app_commands.autocomplete(event=_ac_event_player, type=_type_autocomplete)
    async def event_leaderboard(
        self,
        interaction: discord.Interaction,
        event: str | None = None,
        type: str | None = None,
    ):
        await interaction.response.defer(thinking=True)

        if event:
            # Standings for a specific event (event wins over type).
            try:
                resp = await self.http_client.get(f"{api_base}/events/{event}/standings", timeout=10)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                await interaction.followup.send(f"❌ {_extract_detail(exc)}", ephemeral=True)
                return

            rows = resp.json()
            if not rows:
                await interaction.followup.send("No standings yet for this event.", ephemeral=True)
                return

            embed = discord.Embed(title=f"Standings — Event #{event}", color=discord.Color.gold())
            caller_id = interaction.user.id
            caller_row = None
            lines = []
            for r in rows[:10]:
                medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(r["rank"], f"#{r['rank']}")
                qual = "" if r.get("qualified", True) else " *(unqualified)*"
                lines.append(f"{medal} **{r['display_name']}** — {r['value']:.1f}{qual}")
                if r.get("user_id") == caller_id and r["rank"] > 10:
                    caller_row = r
            embed.description = "\n".join(lines) or "No entries."
            if caller_row:
                embed.set_footer(
                    text=f"Your rank: #{caller_row['rank']} — {caller_row['value']:.1f}"
                    + ("" if caller_row.get("qualified", True) else " (unqualified)")
                )
            await interaction.followup.send(embed=embed)

        else:
            # All-time medal tally.
            params: dict = {}
            if type:
                params["type_slug"] = type
            try:
                resp = await self.http_client.get(
                    f"{api_base}/events/guild/{interaction.guild_id}/medals",
                    params=params,
                    timeout=10,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                await interaction.followup.send(f"❌ {_extract_detail(exc)}", ephemeral=True)
                return

            rows = resp.json()
            if not rows:
                await interaction.followup.send("No medals recorded yet.", ephemeral=True)
                return

            embed = discord.Embed(
                title=f"Medal Tally — {interaction.guild.name}" + (f" ({type})" if type else ""),
                color=discord.Color.gold(),
            )
            lines = []
            for r in rows[:15]:
                lines.append(
                    f"**{r['display_name']}** — 🥇{r['gold']} 🥈{r['silver']} 🥉{r['bronze']} · {r['events']} events"
                )
            embed.description = "\n".join(lines)
            await interaction.followup.send(embed=embed)


    @app_commands.command(
        name="admin_sync_roles",
        description="[ADMIN] Force notification role sync for this guild",
    )
    @app_commands.describe(dry_run="If True, count what would change without adding roles")
    async def admin_sync_roles(self, interaction: discord.Interaction, dry_run: bool = False) -> None:
        """Force the notification role sync for the current guild and reply with counts."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not await _check_is_admin(interaction):
            await interaction.followup.send("❌ This command requires admin privileges.", ephemeral=True)
            return

        from utils.autocomplete_warm import sync_guild_notification_roles

        guild = interaction.guild

        try:
            counts = await sync_guild_notification_roles(self.bot, guild, dry_run=dry_run)
            prefix = "ℹ️ Dry run — " if dry_run else ""
            lines = [
                f"{prefix}Players scanned: **{counts.get('players_scanned', 0)}**",
                f"Roles {'would be added' if dry_run else 'added'}: **{counts.get('roles_added', 0)}**",
                f"Members not found: **{counts.get('not_found', 0)}**",
                f"Failures: **{counts.get('failures', 0)}**",
            ]
            await interaction.followup.send("\n".join(lines), ephemeral=True)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            flogger.error(f"/admin_sync_roles error: guild={guild.id}: {exc}")
            await interaction.followup.send(f"❌ Sync error: {exc}", ephemeral=True)

    @admin_sync_roles.error
    async def admin_sync_roles_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /admin_sync_roles", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_detail(exc: httpx.HTTPStatusError) -> str:
    """Extract the `detail` field from a FastAPI error response, or return status text."""
    try:
        return exc.response.json().get("detail", str(exc.response.status_code))
    except Exception:  # pylint: disable=broad-exception-caught
        return str(exc.response.status_code)


async def setup(bot: commands.Bot):
    flogger.debug("Setting up eventsCog...")
    await bot.add_cog(EventsCog(bot))
    flogger.info("eventsCog loaded")
