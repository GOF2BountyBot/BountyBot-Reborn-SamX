import asyncio
import importlib
import os
import pkgutil
import sys
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from typing import Any

import discord
import httpx
import uvicorn
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from discord.ext import commands
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from shared import bblogger

# Add the current directory to path for relative imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.autocomplete_state import init as init_autocomplete_state
from utils.command_utils import get_command_handler


# ─── Bot Configuration ──────────────────────────────────────────────────────────
class GatewayBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True

        super().__init__(
            command_prefix=os.getenv("COMMAND_PREFIX", "?p"),
            intents=intents,
            application_id=int(os.getenv("BOTAPPID", "0")),
            status=discord.Status.online,
        )

        self.flogger = bblogger.get_logger("discord-gateway")
        self.startup_complete = False
        self._warm_jobs_registered: bool = False
        self.command_handler = get_command_handler(self)

    async def setup_hook(self):
        self.flogger.info("=== SETUP HOOK STARTED ===")

        # Load cogs
        count = 0
        for fn in os.listdir("src/cogs"):
            if fn.endswith(".py") and not any(x in fn for x in ("template", "disabled", "test")):
                try:
                    await self.load_extension(f"cogs.{fn[:-3]}")
                    count += 1
                    self.flogger.info(f"✓ Loaded cog {fn}")
                except Exception as e:
                    self.flogger.error(f"✗ Cog load failed {fn}: {e}")
                    raise
        self.flogger.info(f"=== SETUP HOOK COMPLETED ({count} cogs) ===")

    async def execute_command_with_validation(
        self,
        ctx: commands.Context,
        command_name: str,
        handler: Callable[[commands.Context], Any],
        permissions: dict[str, Any] | None = None,
        cooldown_seconds: int = 5,
    ) -> bool:
        """Execute a command with validation and error handling"""
        return await self.command_handler.execute_command(ctx, command_name, handler, permissions, cooldown_seconds)

    async def on_ready(self):
        self.flogger.info(f"Bot logged in as {self.user} ({self.user.id})")
        await self.change_presence(status=discord.Status.online)
        if not self.startup_complete:
            if os.getenv("AUTO_SYNC_COMMANDS", "true").lower() == "true":
                await self.sync_commands()
                self.flogger.info("Commands synced")
            else:
                self.flogger.info("AUTO_SYNC_COMMANDS=false; skipping startup command sync (use wake to force-sync)")
            self.startup_complete = True

        if not self._warm_jobs_registered:
            self._warm_jobs_registered = True
            # _app_ref is set by the lifespan in bot.py before bot.start() is called.
            # Access app.state.scheduler so we can register warm jobs.
            if hasattr(self, "_app_ref") and self._app_ref is not None:
                _app = self._app_ref
                if hasattr(_app.state, "scheduler"):
                    from utils.autocomplete_warm import register_warm_jobs

                    register_warm_jobs(_app.state.scheduler, self)
                    self.flogger.info(f"Registered autocomplete warm jobs for {len(self.guilds)} guilds")

    async def sync_commands(self):
        # mirror original logic (global vs guild)
        if self.guilds:
            for g in self.guilds:
                self.tree.copy_global_to(guild=g)
                await self.tree.sync(guild=discord.Object(id=g.id))
        else:
            await self.tree.sync()


# ─── FastAPI + Lifespan ─────────────────────────────────────────────────────────
GATEWAY_HOST = os.getenv("GATEWAY_HOST", "0.0.0.0")
GATEWAY_PORT = int(os.getenv("GATEWAY_PORT", os.getenv("PORT", "8000")))
ACCESS_LOG = os.getenv("ACCESS_LOG", "true").lower() == "true"


async def _warm_on_boot(bot, autocomplete_http: httpx.AsyncClient, api_base: str) -> None:
    """One-shot startup pre-warm of the autocomplete caches.

    On a full `compose up --force-recreate`, bot-core boots AFTER the gateway, so the
    scheduler's fixed-offset initial warm waves fire before bot-core is reachable and
    fail; the caches then stay cold until the recurring refresh jobs cycle (minutes).
    This task closes that gap: it waits for the Discord bot AND bot-core to be ready,
    then runs a SINGLE warm pass so autocomplete is hot ASAP after a redeploy/restart.

    Intentionally one-and-done: ongoing cache freshness/self-heal is owned by the
    recurring scheduler jobs in register_warm_jobs(), which this does NOT touch. Runs
    as a non-blocking background task and self-terminates on completion or deadline.
    """
    flogger = bblogger.get_logger("discord-gateway-api-server")
    ready_timeout_s = float(os.getenv("AUTOCOMPLETE_PREWARM_BOT_READY_TIMEOUT_S", "60"))
    botcore_deadline_s = float(os.getenv("AUTOCOMPLETE_PREWARM_BOTCORE_DEADLINE_S", "180"))
    poll_interval_s = float(os.getenv("AUTOCOMPLETE_PREWARM_POLL_INTERVAL_S", "5"))
    stagger_ms = int(os.getenv("AUTOCOMPLETE_WARM_GUILD_STAGGER_MS", "200"))

    # 1) Wait for the Discord bot to connect (so bot.guilds is populated). Bounded,
    #    mirroring the existing discord_helpers.resolve_bot pattern.
    try:
        await asyncio.wait_for(bot.wait_until_ready(), timeout=ready_timeout_s)
    except TimeoutError:
        flogger.warning(
            f"warm-on-boot: Discord bot not ready within {ready_timeout_s:.0f}s — "
            "skipping startup pre-warm (recurring scheduler jobs will warm the caches)."
        )
        return

    # 2) Wait for bot-core to become reachable, bounded by a deadline.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + botcore_deadline_s
    while True:
        try:
            resp = await autocomplete_http.get(f"{api_base}/health", timeout=3.0)
            resp.raise_for_status()
            break
        except Exception as exc:  # pylint: disable=broad-exception-caught
            if loop.time() >= deadline:
                flogger.warning(
                    f"warm-on-boot: bot-core not reachable within {botcore_deadline_s:.0f}s "
                    f"({api_base!r}, last_error={exc!r}) — skipping startup pre-warm "
                    "(recurring scheduler jobs will warm the caches once it is up)."
                )
                return
            await asyncio.sleep(poll_interval_s)

    # 3) Single warm pass, in dependency order, lightly staggered per guild.
    # NOTE: this reuses the warm coroutines from autocomplete_warm directly; the call
    # list MIRRORS the initial waves in register_warm_jobs() — if a new initial-warm
    # cache is added there, add it here too. We deliberately register NO scheduler jobs;
    # ongoing freshness remains the scheduler's responsibility.
    from utils.autocomplete_warm import (
        refresh_jobs_cache,
        warm_guild_admin_duel_cache,
        warm_guild_bounty_cache,
        warm_guild_combatlog_caches,
        warm_guild_duel_caches,
        warm_guild_players,
        warm_guild_shop_cache,
    )

    guilds = list(bot.guilds)
    flogger.info(f"warm-on-boot: bot-core reachable; pre-warming autocomplete caches for {len(guilds)} guild(s)")
    for i, guild in enumerate(guilds):
        if i and stagger_ms:
            await asyncio.sleep(stagger_ms / 1000)
        gid = guild.id
        warm_steps = (
            ("shop", warm_guild_shop_cache, (bot, gid)),
            ("bounty", warm_guild_bounty_cache, (bot, gid)),
            ("players", warm_guild_players, (gid,)),
            ("duel", warm_guild_duel_caches, (bot, gid)),
            ("admin-duel", warm_guild_admin_duel_cache, (bot, gid)),
            ("combatlog", warm_guild_combatlog_caches, (bot, gid)),
        )
        # Each warm fn is individually non-fatal, but guard anyway so one failing cache
        # never aborts the rest of the pass.
        for label, fn, fn_args in warm_steps:
            try:
                await fn(*fn_args)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                flogger.warning(f"warm-on-boot: {label} warm failed for guild {gid}: {exc!r}")

    # Jobs cache is guild-agnostic — warm once.
    try:
        await refresh_jobs_cache(bot)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.warning(f"warm-on-boot: jobs cache warm failed: {exc!r}")

    flogger.info("warm-on-boot: startup pre-warm complete")


@asynccontextmanager
async def lifespan(app: FastAPI):
    flogger = bblogger.get_logger("discord-gateway-api-server")
    flogger.info("🚀 API starting up…")

    # Start Discord bot inside FastAPI event loop
    token = os.getenv("BOTTOKEN")
    if not token:
        flogger.critical("BOTTOKEN is not set, exiting.")
        os._exit(1)

    # Bot-owned HTTP client (lifecycle tied to bot process, not any cog)
    # BOT_API_BASE_URL is the single canonical env var for reaching bot-core.
    # All cogs use BOT_API_BASE_URL; this lifespan must match.
    api_base = os.getenv("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
    autocomplete_http = httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=3.0),
        headers={"Content-Type": "application/json"},
    )
    app.state.autocomplete_http = autocomplete_http
    init_autocomplete_state(autocomplete_http, api_base)
    flogger.info("Autocomplete state initialized with bot-owned HTTP client")

    # CI-19: Startup health probe with retry — surface misconfigured api_base at startup
    # rather than silently degrading to empty autocomplete caches.  Non-fatal: the bot
    # starts regardless so Discord commands still work.
    # Runs as a BACKGROUND task (not awaited inline) so it never blocks the lifespan
    # before `yield`. A blocking probe stalls the gateway HTTP server from coming up,
    # which in turn delays bot-core (its compose depends_on waits on the gateway's
    # healthcheck). On a full cold start bot-core is not up yet, so the probe is
    # expected to fail there; on a single-container gateway restart bot-core is already
    # up and the probe confirms reachability.
    # Retries mirror the cog preload retry pattern: a few attempts with backoff.
    # TESTING (TRUEUP-04): accepted as non-unit-testable in place — this nested closure
    # captures lifespan-scoped state (autocomplete_http, api_base) and cannot be imported.
    # Manual validation in a live environment: restart ONLY the gateway container while
    # bot-core is up and check the gateway log for "Autocomplete health probe OK";
    # restart the full stack cold and check for the probe-attempt INFO lines followed by
    # "Autocomplete health probe FAILED after 3 attempts" at WARNING (expected — bot-core
    # isn't up yet), then confirm autocomplete works once the warm jobs run.
    async def _autocomplete_health_probe() -> None:
        _probe_attempts = 3
        _probe_backoff_s = (1.0, 2.0)  # per-attempt wait BEFORE retry (used for attempts 1 and 2 only)
        _last_probe_exc: Exception | None = None
        for _attempt in range(1, _probe_attempts + 1):
            try:
                probe_resp = await autocomplete_http.get(f"{api_base}/health", timeout=3.0)
                probe_resp.raise_for_status()
                flogger.info(f"Autocomplete health probe OK (attempt {_attempt}): api_base={api_base}")
                return
            except Exception as _probe_exc:  # pylint: disable=broad-exception-caught
                _last_probe_exc = _probe_exc
                if _attempt < _probe_attempts:
                    _wait = _probe_backoff_s[_attempt - 1]
                    flogger.info(
                        f"Autocomplete health probe attempt {_attempt}/{_probe_attempts} failed "
                        f"(api_base={api_base!r}): {_probe_exc!r}. Retrying in {_wait:.0f}s…"
                    )
                    await asyncio.sleep(_wait)
        flogger.warning(
            f"Autocomplete health probe FAILED after {_probe_attempts} attempts — "
            f"bot-core not reachable at gateway startup (expected on a full cold start). "
            f"Recurring warm jobs will populate the autocomplete caches once bot-core is up. "
            f"api_base={api_base!r} last_error={_last_probe_exc!r}."
        )

    app.state.probe_task = asyncio.create_task(_autocomplete_health_probe())

    # In-process APScheduler (MemoryJobStore — no persistence needed for warm jobs)
    scheduler = AsyncIOScheduler(
        jobstores={"default": MemoryJobStore()},
        timezone="UTC",
    )
    scheduler.start()
    app.state.scheduler = scheduler
    flogger.info("Gateway APScheduler started (in-process MemoryJobStore)")

    bot = GatewayBot()
    # Give the bot a reference to the FastAPI app so on_ready can access app.state.scheduler
    bot._app_ref = app
    app.state.bot = bot

    # Wrapper coroutine to catch and log exceptions from bot.start()
    async def bot_task_wrapper():
        try:
            await bot.start(token, reconnect=True)
        except Exception as e:
            flogger.critical(f"Discord bot task failed: {e!s}", exc_info=True)
            raise

    app.state.bot_task = asyncio.create_task(bot_task_wrapper())
    flogger.info("✅ Discord bot task launched")

    # One-shot startup pre-warm (non-blocking, self-terminating). Closes the cold-cache
    # gap on a full cold start where bot-core boots after the gateway. Does NOT touch the
    # scheduler's recurring warm/refresh jobs.
    app.state.warm_on_boot_task = asyncio.create_task(_warm_on_boot(bot, autocomplete_http, api_base))
    flogger.info("warm-on-boot pre-warm task launched")

    yield  # ←── your routes run here

    # Shutdown
    flogger.info("🛑 API shutting down…")
    await bot.close()
    # Cancel the background health probe and warm-on-boot task if still running.
    for _task_name in ("probe_task", "warm_on_boot_task"):
        _bg_task = getattr(app.state, _task_name, None)
        if _bg_task is not None:
            _bg_task.cancel()
            with suppress(asyncio.CancelledError):
                await _bg_task
    app.state.bot_task.cancel()
    try:
        await app.state.bot_task
    except asyncio.CancelledError:
        flogger.info("✅ Bot task cancelled")

    app.state.scheduler.shutdown(wait=False)
    await app.state.autocomplete_http.aclose()
    flogger.info("Gateway APScheduler stopped; autocomplete HTTP client closed")


def create_app() -> FastAPI:
    flogger = bblogger.get_logger("discord-gateway-api-server")
    flogger.trace("Initializing FastAPI…")

    app = FastAPI(
        title="Discord Gateway API",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Auto-include routers from src/api/routers
    routers_pkg = importlib.import_module("api.routers")
    for _, modname, ispkg in pkgutil.iter_modules(routers_pkg.__path__):
        if not ispkg:
            mod = importlib.import_module(f"api.routers.{modname}")
            if hasattr(mod, "router"):
                app.include_router(mod.router, prefix="/api/v1", tags=[modname])
                flogger.info(f"✓ Included router {modname}")

    # root
    @app.get("/", tags=["root"])
    async def root():
        return {"message": "Discord Gateway API is running", "version": "1.0.0"}

    return app


# dependency for your routers
def get_bot(request: Request) -> GatewayBot:
    return request.app.state.bot


# ─── Launcher ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # direct run
    uvicorn.run(
        create_app(),
        host=GATEWAY_HOST,
        port=GATEWAY_PORT,
        access_log=ACCESS_LOG,
        log_level="info",
    )
