"""Startup warm and scheduled refresh job functions for autocomplete caches.

Called from bot.py lifespan after on_ready. Jobs are registered with an
in-process APScheduler (MemoryJobStore). All jobs are non-blocking and
non-fatal — failures log a WARNING and return; they do not propagate to
the scheduler.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import utils.autocomplete_state as autocomplete_state
from utils.autocomplete_state import NormalizedChoice
from utils.autocomplete_utils import normalize_for_search

flogger = logging.getLogger("discord-gateway-autocomplete-warm")

# ---------------------------------------------------------------------------
# Concurrency semaphore for per-player loadout warm
# ---------------------------------------------------------------------------

_warm_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    """Return (creating if needed) the module-level semaphore for loadout warm concurrency."""
    global _warm_semaphore
    if _warm_semaphore is None:
        concurrency = int(os.getenv("AUTOCOMPLETE_WARM_CONCURRENCY", "4"))
        _warm_semaphore = asyncio.Semaphore(concurrency)
    return _warm_semaphore


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
            # Fetch inventory
            inv_resp = await client.get(
                f"{api_base}/inventory/player/{player_id}",
                timeout=10.0,
            )
            inv_resp.raise_for_status()
            items = inv_resp.json()

            inventory_choices: list[NormalizedChoice] = []
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
                inventory_choices.append(NormalizedChoice(label=label, value=value, norm=norm, raw=item))

            autocomplete_state.set_inventory(guild_id, player_id, inventory_choices)

        except Exception as exc:  # pylint: disable=broad-exception-caught
            flogger.warning(
                f"warm_active_player_loadout: inventory fetch failed for "
                f"guild_id={guild_id} player_id={player_id}: {type(exc).__name__}: {exc}"
            )
            return

        try:
            # Fetch ships
            ships_resp = await client.get(
                f"{api_base}/ships/player/{player_id}",
                timeout=10.0,
            )
            ships_resp.raise_for_status()
            ships = ships_resp.json()

            ships_choices: list[NormalizedChoice] = []
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
                ships_choices.append(NormalizedChoice(label=label, value=value, norm=norm, raw=ship))

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
            resp = await client.get(url, timeout=10.0)
            resp.raise_for_status()
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


async def refresh_jobs_cache(bot) -> None:
    """Scheduled job: invalidate the scheduler jobs cache so it re-warms on next access.

    Gracefully no-ops if SchedulerCog is not loaded or does not yet have _job_cache
    (Phase 6 adds _job_cache to schedulerCog).
    """
    try:
        cog = bot.get_cog("SchedulerCog")
        if cog is None:
            flogger.warning("refresh_jobs_cache: SchedulerCog not found on bot; skipping")
            return
        if hasattr(cog, "_job_cache"):
            cog._job_cache.invalidate("all")
            flogger.debug("refresh_jobs_cache: invalidated SchedulerCog._job_cache")
        else:
            flogger.debug("refresh_jobs_cache: SchedulerCog has no _job_cache yet (Phase 6 pending); no-op")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.warning(f"refresh_jobs_cache: failed: {type(exc).__name__}: {exc}")


async def refresh_shop_cache_safety_net(bot) -> None:
    """Scheduled job: safety-net pull for shop cache.

    Gets the ShopCog and, if it has _shop_cache, triggers a re-warm for each
    currently-cached (guild_id, tier) key by calling get() on the cache (which
    triggers the refresh_fn if the entry is expired or absent).

    Non-fatal: logs WARNING on any error and returns without raising.
    """
    try:
        cog = bot.get_cog("ShopCog")
        if cog is None:
            flogger.warning("refresh_shop_cache_safety_net: ShopCog not found on bot; skipping")
            return
        if not hasattr(cog, "_shop_cache"):
            flogger.warning("refresh_shop_cache_safety_net: ShopCog has no _shop_cache; skipping")
            return

        keys = list(cog._shop_cache.keys())
        for guild_id, tier in keys:
            await cog._shop_cache.get((guild_id, tier))

        flogger.info(f"refresh_shop_cache_safety_net: refreshed {len(keys)} shop cache entries")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.warning(f"refresh_shop_cache_safety_net: failed: {type(exc).__name__}: {exc}")


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

    # One-time warm jobs: one per guild, staggered by stagger_ms milliseconds
    for i, guild in enumerate(bot.guilds):
        run_date = now + timedelta(seconds=15 + i * stagger_ms / 1000)
        scheduler.add_job(
            warm_guild_players,
            "date",
            run_date=run_date,
            args=[guild.id],
            id=f"warm-guild-{guild.id}",
            replace_existing=True,
        )
    flogger.info(f"register_warm_jobs: scheduled {len(bot.guilds)} guild warm jobs")

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
        seconds=60,
        id="autocomplete-jobs-refresh",
        args=[bot],
        replace_existing=True,
    )
    scheduler.add_job(
        refresh_shop_cache_safety_net,
        "interval",
        minutes=15,
        id="autocomplete-shop-safety-net",
        args=[bot],
        replace_existing=True,
    )
    flogger.info("register_warm_jobs: registered 4 recurring refresh jobs")
