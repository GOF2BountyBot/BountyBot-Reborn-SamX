import asyncio
import importlib
import os
import pkgutil
import sys
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

import discord
import uvicorn
from discord.ext import commands
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from shared import bblogger

# Add the current directory to path for relative imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.command_utils import get_command_handler


# ─── Bot Configuration ──────────────────────────────────────────────────────────
class GatewayBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True

        super().__init__(command_prefix="!", intents=intents, application_id=int(os.getenv("BOTAPPID", "0")))

        self.flogger = bblogger.get_logger("discord-gateway")
        self.startup_complete = False
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
        if not self.startup_complete:
            await self.sync_commands()
            self.startup_complete = True
            self.flogger.info("Commands synced")

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

    bot = GatewayBot()
    app.state.bot = bot

    # Wrapper coroutine to catch and log exceptions from bot.start()
    async def bot_task_wrapper():
        try:
            await bot.start(token, reconnect=True)
        except Exception as e:
            flogger.critical(f"Discord bot task failed: {str(e)}", exc_info=True)
            raise

    app.state.bot_task = asyncio.create_task(bot_task_wrapper())
    flogger.info("✅ Discord bot task launched")

    yield  # ←── your routes run here

    # Shutdown
    flogger.info("🛑 API shutting down…")
    await bot.close()
    app.state.bot_task.cancel()
    try:
        await app.state.bot_task
    except asyncio.CancelledError:
        flogger.info("✅ Bot task cancelled")


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
