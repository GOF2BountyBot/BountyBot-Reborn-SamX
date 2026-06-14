import asyncio
import importlib
import os
import pkgutil
import sys
from collections.abc import Callable
from contextlib import asynccontextmanager
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

    yield  # ←── your routes run here

    # Shutdown
    flogger.info("🛑 API shutting down…")
    await bot.close()
    # Cancel the background health probe if it is still retrying.
    probe_task = getattr(app.state, "probe_task", None)
    if probe_task is not None:
        probe_task.cancel()
        try:
            await probe_task
        except asyncio.CancelledError:
            pass
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
