"""Startup warm and scheduled refresh job functions for autocomplete caches.

Called from bot.py lifespan after on_ready. Jobs are registered with an
in-process APScheduler (MemoryJobStore). All jobs are non-blocking and
non-fatal — failures log a WARNING and return; they do not propagate to
the scheduler.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from shared import bblogger
from shared.http_retry import with_transient_retry

import utils.autocomplete_state as autocomplete_state
from utils.autocomplete_utils import normalize_for_search

flogger = bblogger.get_logger("discord-gateway-autocomplete-warm")

# ---------------------------------------------------------------------------
# Concurrency semaphore for per-player loadout warm
# ---------------------------------------------------------------------------

_warm_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    """Return (creating if needed) the module-level semaphore for loadout warm concurrency."""
    global _warm_semaphore
    if _warm_semaphore is None:
        concurrency = int(os.getenv("AUTOCOMPLETE_WARM_CONCURRENCY", "16"))
        _warm_semaphore = asyncio.Semaphore(concurrency)
    return _warm_semaphore


# ---------------------------------------------------------------------------
# Wave 0: guild-wide shop and bounty cache warm (runs before per-user warm)
# ---------------------------------------------------------------------------


async def warm_guild_shop_cache(bot, guild_id: int) -> None:
    """Warm all four shop tiers for one guild on startup.

    Looks up the ShopCog, then for each tier calls
    ``cog._shop_cache.get((guild_id, tier))`` which triggers
    ``_fetch_tier_shop`` on a miss and writes the result into cache.

    Non-fatal: logs WARNING on any error and returns without raising.
    """
    try:
        cog = bot.get_cog("ShopCog")
        if cog is None:
            flogger.warning(f"warm_guild_shop_cache({guild_id}): ShopCog not found on bot; skipping")
            return
        for tier in ["Bronze", "Silver", "Gold", "Platinum"]:
            try:
                await cog._shop_cache.get((guild_id, tier))
                flogger.debug(f"warm_guild_shop_cache({guild_id}): warmed tier={tier}")
            except Exception as exc:  # pylint: disable=broad-exception-caught
                flogger.warning(
                    f"warm_guild_shop_cache({guild_id}): failed to warm tier={tier}: {type(exc).__name__}: {exc}"
                )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.warning(f"warm_guild_shop_cache({guild_id}): unexpected error: {type(exc).__name__}: {exc}")


async def warm_guild_bounty_cache(bot, guild_id: int) -> None:
    """Warm the bounty autocomplete cache for one guild on startup.

    Looks up the BountyCog; if it has ``_bounty_cache``, calls
    ``cog._bounty_cache.get(guild_id)`` to trigger a refresh.

    Non-fatal: logs WARNING on any error and returns without raising.
    """
    try:
        cog = bot.get_cog("BountyCog")
        if cog is None:
            flogger.warning(f"warm_guild_bounty_cache({guild_id}): BountyCog not found on bot; skipping")
            return
        if hasattr(cog, "_bounty_cache"):
            await cog._bounty_cache.get(guild_id)
            flogger.debug(f"warm_guild_bounty_cache({guild_id}): bounty cache warmed")
        else:
            flogger.debug(f"warm_guild_bounty_cache({guild_id}): BountyCog has no _bounty_cache; skipping")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.warning(f"warm_guild_bounty_cache({guild_id}): unexpected error: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Stage 2: per-player loadout warm
# ---------------------------------------------------------------------------


async def warm_active_player_loadout(guild_id: int, player_id: int) -> None:
    """Fetch and cache inventory + ships for one player. Semaphore-throttled.

    Calls GET /inventory/player/{player_id} and GET /ships/player/{player_id},
    builds NormalizedChoice lists, and populates the shared inventory and ships
    caches via autocomplete_state.set_inventory / set_ships.

    Non-fatal: logs WARNING on any error and returns without raising.
    """
    async with _get_semaphore():
        client = autocomplete_state.get_http_client()
        api_base = autocomplete_state.get_api_base()

        if client is None or api_base is None:
            flogger.warning("warm_active_player_loadout: autocomplete_state not initialized; skipping")
            return

        try:
            # Fetch inventory (idempotent GET — retried on transient failures;
            # with_transient_retry calls raise_for_status() internally)
            inv_resp = await with_transient_retry(
                client.get,
                f"{api_base}/inventory/player/{player_id}",
                timeout=10.0,
            )
            items = inv_resp.json()

            inventory_choices: list[autocomplete_state.NormalizedChoice] = []
            for item in items:
                item_name = item.get("item_name") or ""
                item_type = item.get("item_type") or ""
                quantity = item.get("quantity") or 0
                if not item_name:
                    continue
                qty_suffix = f" [x{quantity}]" if quantity and quantity > 1 else ""
                label = f"{item_name} ({item_type.replace('_', ' ').title()}){qty_suffix}"
                value = str(item.get("id", item_name))
                norm = normalize_for_search(label)
                inventory_choices.append(
                    autocomplete_state.NormalizedChoice(label=label, value=value, norm=norm, raw=item)
                )

            autocomplete_state.set_inventory(guild_id, player_id, inventory_choices)

        except Exception as exc:  # pylint: disable=broad-exception-caught
            flogger.warning(
                f"warm_active_player_loadout: inventory fetch failed for "
                f"guild_id={guild_id} player_id={player_id}: {type(exc).__name__}: {exc}"
            )
            return

        try:
            # Fetch ships (idempotent GET — retried on transient failures;
            # with_transient_retry calls raise_for_status() internally)
            ships_resp = await with_transient_retry(
                client.get,
                f"{api_base}/ships/player/{player_id}",
                timeout=10.0,
            )
            ships = ships_resp.json()

            ships_choices: list[autocomplete_state.NormalizedChoice] = []
            for ship in ships:
                nickname = ship.get("nickname") or ""
                name = ship.get("name") or ship.get("ship_name") or ""
                display_name = nickname or name
                ship_type = ship.get("ship_type") or ship.get("type") or ""
                is_active = ship.get("is_active", False)

                active_prefix = "⚡ " if is_active else ""
                label = f"{display_name} ({active_prefix}{ship_type})"
                value = str(ship.get("player_ship_id") or ship.get("id") or "")
                norm = normalize_for_search(label)
                ships_choices.append(autocomplete_state.NormalizedChoice(label=label, value=value, norm=norm, raw=ship))

            autocomplete_state.set_ships(guild_id, player_id, ships_choices)

        except Exception as exc:  # pylint: disable=broad-exception-caught
            flogger.warning(
                f"warm_active_player_loadout: ships fetch failed for "
                f"guild_id={guild_id} player_id={player_id}: {type(exc).__name__}: {exc}"
            )


# ---------------------------------------------------------------------------
# Stage 1: bulk player warm per guild
# ---------------------------------------------------------------------------


async def warm_guild_players(guild_id: int) -> None:
    """Warm player cache for one guild by paginating GET /players/guild/{guild_id}.

    Stage 1 of the startup warm:
    - Paginates with limit=500 until fewer than 500 rows returned.
    - Calls autocomplete_state.set_player() for each player.

    Stage 2 fires after Stage 1: iterates warmed player keys for this guild
    and concurrently calls warm_active_player_loadout() for each (semaphore-gated).

    Non-fatal: logs WARNING on any error and returns without raising.
    """
    client = autocomplete_state.get_http_client()
    api_base = autocomplete_state.get_api_base()

    if client is None or api_base is None:
        flogger.warning(f"warm_guild_players({guild_id}): autocomplete_state not initialized; skipping")
        return

    active_within_days = int(os.getenv("AUTOCOMPLETE_WARM_ACTIVE_DAYS", "7"))
    limit = 500
    skip = 0
    total_loaded = 0

    try:
        while True:
            url = (
                f"{api_base}/players/guild/{guild_id}?active_within_days={active_within_days}&limit={limit}&skip={skip}"
            )
            # Idempotent GET — retried on transient failures (5xx/timeout/connect);
            # with_transient_retry calls raise_for_status() internally.
            resp = await with_transient_retry(client.get, url, timeout=10.0)
            players = resp.json()

            for player in players:
                user_id = player.get("user_id") or player.get("discord_id")
                if user_id is not None:
                    autocomplete_state.set_player(guild_id, user_id, player)

            total_loaded += len(players)
            flogger.debug(f"warm_guild_players({guild_id}): loaded {len(players)} players (skip={skip})")

            if len(players) < limit:
                break
            skip += limit

    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.warning(
            f"warm_guild_players({guild_id}): failed after loading {total_loaded} players: {type(exc).__name__}: {exc}"
        )
        return

    flogger.info(f"warm_guild_players({guild_id}): Stage 1 complete — {total_loaded} players loaded")

    # Stage 2: warm loadouts for every player we just loaded
    warmed_player_ids: list[int] = []
    for g, uid in list(autocomplete_state.player_cache.keys()):
        if g == guild_id:
            cached_player = autocomplete_state.player_cache.peek((g, uid))
            if cached_player is not None:
                p_id = cached_player.get("id")
                if p_id is not None:
                    warmed_player_ids.append(p_id)

    if warmed_player_ids:
        tasks = [asyncio.create_task(warm_active_player_loadout(guild_id, p_id)) for p_id in warmed_player_ids]
        await asyncio.gather(*tasks)
        flogger.info(
            f"warm_guild_players({guild_id}): Stage 2 complete — {len(warmed_player_ids)} loadout warm tasks dispatched"
        )


# ---------------------------------------------------------------------------
# Scheduled refresh jobs
# ---------------------------------------------------------------------------


async def refresh_all_guild_players(bot) -> None:
    """Scheduled job: re-warm player cache for all guilds the bot is in.

    Iterates bot.guilds and calls warm_guild_players for each. Non-fatal:
    logs WARNING on any error and returns without raising.
    """
    try:
        for guild in bot.guilds:
            await warm_guild_players(guild.id)
        flogger.info(f"refresh_all_guild_players: refreshed {len(bot.guilds)} guilds")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.warning(f"refresh_all_guild_players: failed: {type(exc).__name__}: {exc}")


async def refresh_loadouts_round_robin() -> None:
    """Scheduled job: fire loadout warm tasks for all currently-cached (guild_id, player_id) keys.

    Fire-and-forget via asyncio.create_task — the semaphore throttles concurrency.
    Non-fatal: logs WARNING on any error and returns without raising.
    """
    try:
        if autocomplete_state.inventory_cache is None:
            flogger.warning("refresh_loadouts_round_robin: inventory_cache not initialized; skipping")
            return

        keys = list(autocomplete_state.inventory_cache.keys())
        for guild_id, player_id in keys:
            asyncio.create_task(  # noqa: RUF006
                warm_active_player_loadout(guild_id, player_id),
                name=f"loadout-refresh-{guild_id}-{player_id}",
            )
        flogger.debug(f"refresh_loadouts_round_robin: dispatched {len(keys)} loadout refresh tasks")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.warning(f"refresh_loadouts_round_robin: failed: {type(exc).__name__}: {exc}")


async def refresh_duel_caches(bot) -> None:
    """Scheduled job: round-robin refresh of duel caches for all currently-cached players.

    Iterates all currently-cached (guild_id, player_id) keys in both
    _pending_duel_cache and _outgoing_duel_cache and calls get() on each to
    reset the TTL. Fire-and-forget via asyncio.create_task; semaphore-throttled.
    Non-fatal: logs WARNING on any error and returns without raising.
    """
    try:
        cog = bot.get_cog("DuelCog")
        if cog is None:
            flogger.warning("refresh_duel_caches: DuelCog not found on bot; skipping")
            return

        sem = _get_semaphore()

        async def _refresh_pending(key: tuple) -> None:
            async with sem:
                try:
                    await cog._pending_duel_cache.get(key)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    flogger.warning(
                        f"refresh_duel_caches: pending refresh failed key={key}: {type(exc).__name__}: {exc}"
                    )

        async def _refresh_outgoing(key: tuple) -> None:
            async with sem:
                try:
                    await cog._outgoing_duel_cache.get(key)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    flogger.warning(
                        f"refresh_duel_caches: outgoing refresh failed key={key}: {type(exc).__name__}: {exc}"
                    )

        pending_keys = list(cog._pending_duel_cache.keys()) if hasattr(cog, "_pending_duel_cache") else []
        outgoing_keys = list(cog._outgoing_duel_cache.keys()) if hasattr(cog, "_outgoing_duel_cache") else []

        tasks = []
        for key in pending_keys:
            tasks.append(asyncio.create_task(_refresh_pending(key), name=f"duel-pending-refresh-{key}"))
        for key in outgoing_keys:
            tasks.append(asyncio.create_task(_refresh_outgoing(key), name=f"duel-outgoing-refresh-{key}"))

        if tasks:
            await asyncio.gather(*tasks)
        flogger.debug(
            f"refresh_duel_caches: dispatched {len(pending_keys)} pending + {len(outgoing_keys)} outgoing refresh tasks"
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.warning(f"refresh_duel_caches: failed: {type(exc).__name__}: {exc}")


async def warm_guild_duel_caches(bot, guild_id: int) -> None:
    """Stage 2 startup warm for duel caches.

    For each player already in player_cache for this guild, schedules background
    get() calls on _pending_duel_cache and _outgoing_duel_cache (semaphore-throttled).
    Non-fatal: logs WARNING on any error and returns without raising.
    """
    try:
        cog = bot.get_cog("DuelCog")
        if cog is None:
            flogger.warning(f"warm_guild_duel_caches({guild_id}): DuelCog not found; skipping")
            return

        sem = _get_semaphore()

        async def _warm_player(player_id: int) -> None:
            async with sem:
                try:
                    await cog._pending_duel_cache.get((guild_id, player_id))
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    flogger.warning(
                        f"warm_guild_duel_caches({guild_id}): pending warm failed "
                        f"player_id={player_id}: {type(exc).__name__}: {exc}"
                    )
                try:
                    await cog._outgoing_duel_cache.get((guild_id, player_id))
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    flogger.warning(
                        f"warm_guild_duel_caches({guild_id}): outgoing warm failed "
                        f"player_id={player_id}: {type(exc).__name__}: {exc}"
                    )

        # Collect player IDs from player_cache for this guild
        player_ids: list[int] = []
        for g, uid in list(autocomplete_state.player_cache.keys()):
            if g == guild_id:
                cached_player = autocomplete_state.player_cache.peek((g, uid))
                if cached_player is not None:
                    p_id = cached_player.get("id")
                    if p_id is not None:
                        player_ids.append(p_id)

        if player_ids:
            tasks = [
                asyncio.create_task(_warm_player(p_id), name=f"duel-warm-{guild_id}-{p_id}") for p_id in player_ids
            ]
            await asyncio.gather(*tasks)
            flogger.info(f"warm_guild_duel_caches({guild_id}): warmed {len(player_ids)} players' duel caches")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.warning(f"warm_guild_duel_caches({guild_id}): unexpected error: {type(exc).__name__}: {exc}")


async def warm_guild_admin_duel_cache(bot, guild_id: int) -> None:
    """Stage-2 startup warm for the AdminCog guild-scoped pending-duel cache.

    Calls cog._admin_pending_duel_cache.get(guild_id) which triggers
    _fetch_admin_pending_duels on a miss. Non-fatal.
    """
    try:
        cog = bot.get_cog("AdminCog")
        if cog is None or not hasattr(cog, "_admin_pending_duel_cache"):
            flogger.debug(f"warm_guild_admin_duel_cache({guild_id}): AdminCog/_admin_pending_duel_cache absent; skip")
            return
        await cog._admin_pending_duel_cache.get(guild_id)
        flogger.debug(f"warm_guild_admin_duel_cache({guild_id}): admin-duel cache warmed")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.warning(f"warm_guild_admin_duel_cache({guild_id}): unexpected error: {type(exc).__name__}: {exc}")


async def refresh_admin_duel_cache(bot) -> None:
    """Scheduled job: round-robin refresh of AdminCog's guild-scoped admin-duel caches.

    Iterates currently-cached guild keys and calls get() on each to reset the TTL.
    Non-fatal.
    """
    try:
        cog = bot.get_cog("AdminCog")
        if cog is None or not hasattr(cog, "_admin_pending_duel_cache"):
            flogger.debug("refresh_admin_duel_cache: AdminCog/_admin_pending_duel_cache absent; skip")
            return
        keys = list(cog._admin_pending_duel_cache.keys())
        for guild_id in keys:
            try:
                await cog._admin_pending_duel_cache.get(guild_id)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                flogger.warning(f"refresh_admin_duel_cache: failed guild={guild_id}: {type(exc).__name__}: {exc}")
        flogger.debug(f"refresh_admin_duel_cache: refreshed {len(keys)} guild admin-duel caches")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.warning(f"refresh_admin_duel_cache: failed: {type(exc).__name__}: {exc}")


async def warm_guild_combatlog_caches(bot, guild_id: int) -> None:
    """Stage-2 startup warm for the per-user combat-log cache.

    Fires AFTER player_cache warm. For each player already in player_cache for this
    guild, schedules a background get((guild_id, discord_user_id)) on the CombatLogCog
    cache (semaphore-throttled). Non-fatal.
    """
    try:
        cog = bot.get_cog("CombatLogCog")
        if cog is None or not hasattr(cog, "_combatlog_cache"):
            flogger.debug(f"warm_guild_combatlog_caches({guild_id}): CombatLogCog/_combatlog_cache absent; skip")
            return
        if autocomplete_state.player_cache is None:
            return

        sem = _get_semaphore()

        async def _warm_user(user_id: int) -> None:
            async with sem:
                try:
                    await cog._combatlog_cache.get((guild_id, user_id))
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    flogger.warning(
                        f"warm_guild_combatlog_caches({guild_id}): warm failed "
                        f"user_id={user_id}: {type(exc).__name__}: {exc}"
                    )

        user_ids = [uid for g, uid in list(autocomplete_state.player_cache.keys()) if g == guild_id]
        if user_ids:
            tasks = [asyncio.create_task(_warm_user(uid), name=f"combatlog-warm-{guild_id}-{uid}") for uid in user_ids]
            await asyncio.gather(*tasks)
            flogger.info(f"warm_guild_combatlog_caches({guild_id}): warmed {len(user_ids)} users' combat-log caches")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.warning(f"warm_guild_combatlog_caches({guild_id}): unexpected error: {type(exc).__name__}: {exc}")


async def refresh_combatlog_round_robin(bot) -> None:
    """Scheduled job: round-robin refresh of all currently-cached combat-log keys.

    Fire-and-forget via asyncio.create_task — the semaphore throttles concurrency.
    Non-fatal. Short TTL means this mostly keeps hot keys warm; missed invalidate
    pushes also self-heal here.
    """
    try:
        cog = bot.get_cog("CombatLogCog")
        if cog is None or not hasattr(cog, "_combatlog_cache"):
            flogger.debug("refresh_combatlog_round_robin: CombatLogCog/_combatlog_cache absent; skip")
            return

        sem = _get_semaphore()

        async def _refresh_one(key: tuple) -> None:
            async with sem:
                try:
                    await cog._combatlog_cache.get(key)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    flogger.warning(
                        f"refresh_combatlog_round_robin: refresh failed key={key}: {type(exc).__name__}: {exc}"
                    )

        keys = list(cog._combatlog_cache.keys())
        for key in keys:
            asyncio.create_task(_refresh_one(key), name=f"combatlog-refresh-{key}")  # noqa: RUF006
        flogger.debug(f"refresh_combatlog_round_robin: dispatched {len(keys)} combat-log refresh tasks")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.warning(f"refresh_combatlog_round_robin: failed: {type(exc).__name__}: {exc}")


async def refresh_bounty_cache(bot) -> None:
    """Scheduled job: refresh bounty cache for all guilds the bot is in.

    Iterates bot.guilds and calls cog._bounty_cache.get(guild.id) for each —
    this triggers _fetch_bounties on miss/expiry and resets the TTL.
    Semaphore-throttled to AUTOCOMPLETE_WARM_CONCURRENCY.
    Non-fatal: logs WARNING on any error and returns without raising.
    """
    try:
        cog = bot.get_cog("BountyCog")
        if cog is None:
            flogger.warning("refresh_bounty_cache: BountyCog not found on bot; skipping")
            return
        if not hasattr(cog, "_bounty_cache"):
            flogger.warning("refresh_bounty_cache: BountyCog has no _bounty_cache; skipping")
            return

        sem = _get_semaphore()

        async def _refresh_one(guild_id: int) -> None:
            async with sem:
                try:
                    await cog._bounty_cache.get(guild_id)
                    flogger.debug(f"refresh_bounty_cache: refreshed guild_id={guild_id}")
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    flogger.warning(
                        f"refresh_bounty_cache: failed for guild_id={guild_id}: {type(exc).__name__}: {exc}"
                    )

        tasks = [asyncio.create_task(_refresh_one(guild.id), name=f"bounty-refresh-{guild.id}") for guild in bot.guilds]
        if tasks:
            await asyncio.gather(*tasks)
        flogger.info(f"refresh_bounty_cache: refreshed {len(tasks)} guilds")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.warning(f"refresh_bounty_cache: failed: {type(exc).__name__}: {exc}")


async def refresh_jobs_cache(bot) -> None:
    """Scheduled job: refresh the scheduler jobs cache in-place.

    Calls cog._job_cache.get("all") which triggers _fetch_jobs and resets the TTL.
    This is preferred over invalidate() so the old value stays valid until the
    refresh completes (no cache-hole window).

    Gracefully no-ops if SchedulerCog is not loaded or does not yet have _job_cache.
    """
    try:
        cog = bot.get_cog("SchedulerCog")
        if cog is None:
            flogger.warning("refresh_jobs_cache: SchedulerCog not found on bot; skipping")
            return
        if hasattr(cog, "_job_cache"):
            await cog._job_cache.get("all")
            flogger.debug("refresh_jobs_cache: refreshed SchedulerCog._job_cache")
        else:
            flogger.debug("refresh_jobs_cache: SchedulerCog has no _job_cache yet; no-op")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.warning(f"refresh_jobs_cache: failed: {type(exc).__name__}: {exc}")


async def refresh_shop_cache(bot) -> None:
    """Scheduled job: periodic shop cache refresh (runs every 6 minutes).

    Gets the ShopCog and, if it has _shop_cache, triggers a re-warm for each
    currently-cached (guild_id, tier) key by calling get() on the cache (which
    triggers the refresh_fn if the entry is expired or absent).

    Non-fatal: logs WARNING on any error and returns without raising.
    """
    try:
        cog = bot.get_cog("ShopCog")
        if cog is None:
            flogger.warning("refresh_shop_cache: ShopCog not found on bot; skipping")
            return
        if not hasattr(cog, "_shop_cache"):
            flogger.warning("refresh_shop_cache: ShopCog has no _shop_cache; skipping")
            return

        keys = list(cog._shop_cache.keys())
        for guild_id, tier in keys:
            await cog._shop_cache.get((guild_id, tier))

        flogger.info(f"refresh_shop_cache: refreshed {len(keys)} shop cache entries")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.warning(f"refresh_shop_cache: failed: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Job registration
# ---------------------------------------------------------------------------


def register_warm_jobs(scheduler: AsyncIOScheduler, bot) -> None:
    """Register all autocomplete warm and refresh jobs with the given scheduler.

    Called from GatewayBot.on_ready (once) after startup_complete is set.
    Uses a staggered initial delay so guild warm jobs don't all fire at once.

    Args:
        scheduler: The bot's in-process AsyncIOScheduler (started in bot.py lifespan).
        bot: The GatewayBot instance (provides bot.guilds).
    """
    stagger_ms = int(os.getenv("AUTOCOMPLETE_WARM_GUILD_STAGGER_MS", "200"))
    now = datetime.now(UTC)

    # Wave 0: guild-wide shop and bounty cache warm, staggered starting at 5s.
    # Runs BEFORE the per-user player warm so shop/bounty data is ready when
    # the first user interacts right after bot startup (B-P2).
    for i, guild in enumerate(bot.guilds):
        wave0_run_date = now + timedelta(seconds=5 + i * stagger_ms / 1000)
        scheduler.add_job(
            warm_guild_shop_cache,
            "date",
            run_date=wave0_run_date,
            args=[bot, guild.id],
            id=f"warm-shop-{guild.id}",
            replace_existing=True,
        )
        scheduler.add_job(
            warm_guild_bounty_cache,
            "date",
            run_date=wave0_run_date,
            args=[bot, guild.id],
            id=f"warm-bounty-{guild.id}",
            replace_existing=True,
        )
    flogger.info(f"register_warm_jobs: scheduled {len(bot.guilds)} Wave 0 guild shop+bounty warm jobs")

    # Wave 1: per-user player warm, starting at 8s (B-P3: was 15s).
    # Starts 3s after Wave 0 (5s + 3s) so guild-wide data is ready first.
    for i, guild in enumerate(bot.guilds):
        run_date = now + timedelta(seconds=8 + i * stagger_ms / 1000)
        scheduler.add_job(
            warm_guild_players,
            "date",
            run_date=run_date,
            args=[guild.id],
            id=f"warm-guild-{guild.id}",
            replace_existing=True,
        )
        # Wave 1b: duel cache warm fires after player cache is populated (~35s)
        duel_warm_date = now + timedelta(seconds=35 + i * stagger_ms / 1000)
        scheduler.add_job(
            warm_guild_duel_caches,
            "date",
            run_date=duel_warm_date,
            args=[bot, guild.id],
            id=f"warm-duel-{guild.id}",
            replace_existing=True,
        )
        # Wave 1b: admin-duel (guild-scoped) cache warm alongside duel warm.
        scheduler.add_job(
            warm_guild_admin_duel_cache,
            "date",
            run_date=duel_warm_date,
            args=[bot, guild.id],
            id=f"warm-admin-duel-{guild.id}",
            replace_existing=True,
        )
        # Wave 1b: per-user combat-log cache warm (depends on warmed player_cache).
        scheduler.add_job(
            warm_guild_combatlog_caches,
            "date",
            run_date=duel_warm_date,
            args=[bot, guild.id],
            id=f"warm-combatlog-{guild.id}",
            replace_existing=True,
        )
    flogger.info(
        f"register_warm_jobs: scheduled {len(bot.guilds)} guild warm jobs + duel/admin-duel/combatlog cache warm jobs"
    )

    # One-shot Wave 2: warm the job cache ~30s after startup so the first
    # /scheduler_* command is always a cache hit.
    scheduler.add_job(
        refresh_jobs_cache,
        "date",
        run_date=now + timedelta(seconds=30),
        args=[bot],
        id="warm-jobs-cache-startup",
        replace_existing=True,
    )
    flogger.info("register_warm_jobs: scheduled 1 one-shot job cache warm (T+30s)")

    # Recurring refresh jobs
    player_refresh_min = int(os.getenv("AUTOCOMPLETE_PLAYER_REFRESH_MINUTES", "10"))
    loadout_refresh_min = int(os.getenv("AUTOCOMPLETE_LOADOUT_REFRESH_MINUTES", "5"))

    scheduler.add_job(
        refresh_all_guild_players,
        "interval",
        minutes=player_refresh_min,
        id="autocomplete-player-refresh",
        args=[bot],
        replace_existing=True,
    )
    scheduler.add_job(
        refresh_loadouts_round_robin,
        "interval",
        minutes=loadout_refresh_min,
        id="autocomplete-loadout-refresh",
        replace_existing=True,
    )
    scheduler.add_job(
        refresh_jobs_cache,
        "interval",
        minutes=2,
        id="autocomplete-jobs-refresh",
        args=[bot],
        replace_existing=True,
    )
    scheduler.add_job(
        refresh_shop_cache,
        "interval",
        minutes=6,
        id="autocomplete-shop-refresh",
        args=[bot],
        replace_existing=True,
    )
    scheduler.add_job(
        refresh_bounty_cache,
        "interval",
        minutes=10,
        id="bounty-cache-refresh",
        args=[bot],
        replace_existing=True,
    )
    scheduler.add_job(
        refresh_duel_caches,
        "interval",
        minutes=5,
        id="duel-cache-refresh",
        args=[bot],
        replace_existing=True,
    )
    scheduler.add_job(
        refresh_admin_duel_cache,
        "interval",
        minutes=5,
        id="admin-duel-cache-refresh",
        args=[bot],
        replace_existing=True,
    )
    combatlog_refresh_min = int(os.getenv("AUTOCOMPLETE_COMBATLOG_REFRESH_MINUTES", "5"))
    scheduler.add_job(
        refresh_combatlog_round_robin,
        "interval",
        minutes=combatlog_refresh_min,
        id="combatlog-cache-refresh",
        args=[bot],
        replace_existing=True,
    )
    flogger.info("register_warm_jobs: registered 8 recurring refresh jobs")
