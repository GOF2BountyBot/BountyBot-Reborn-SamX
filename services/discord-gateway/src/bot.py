import os
import pkgutil
import importlib
import asyncio
from contextlib import asynccontextmanager

import discord
from discord.ext import commands

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

import shared.bblogger as bblogger

# ─── Bot Configuration ──────────────────────────────────────────────────────────
class GatewayBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            application_id=int(os.getenv("BOTAPPID", "0"))
        )

        self.flogger = bblogger.get_logger("discord-gateway")
        self.startup_complete = False

    async def setup_hook(self):
        self.flogger.info("=== SETUP HOOK STARTED ===")

        # Load cogs
        count = 0
        for fn in os.listdir("src/cogs"):
            if fn.endswith(".py") and not any(x in fn for x in ("template", "disabled")):
                try:
                    await self.load_extension(f"cogs.{fn[:-3]}")
                    count += 1
                    self.flogger.info(f"✓ Loaded cog {fn}")
                except Exception as e:
                    self.flogger.error(f"✗ Cog load failed {fn}: {e}")
                    raise
        self.flogger.info(f"=== SETUP HOOK COMPLETED ({count} cogs) ===")

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
ACCESS_LOG   = os.getenv("ACCESS_LOG", "true").lower() == "true"

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
    # .start() is a coroutine that never returns until bot closes
    app.state.bot_task = asyncio.create_task(bot.start(token, reconnect=True))
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
        lifespan=lifespan
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
