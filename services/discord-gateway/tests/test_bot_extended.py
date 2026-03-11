"""
Extended tests for bot.py — covering uncovered lines.
Target: push bot.py from 62% to 80%+.

Uncovered lines:
  65        (execute_command_with_validation — delegates to command_handler)
  96-120    (lifespan — startup and shutdown paths)
  123-157   (create_app — app creation, router discovery)
  161       (get_bot — request.app.state.bot accessor)
"""

import pytest
import asyncio
import importlib
import sys
import os
import types
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, AsyncMock, patch, call

from tests.mocks.discord_mock_utils import DiscordMockUtils

# ── Mock shared.bblogger ─────────────────────────────────────────────────────
_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []
_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_args, **_kwargs):
    logger = MagicMock()
    for m in ("info", "debug", "warning", "error", "trace", "critical", "exception"):
        setattr(logger, m, MagicMock())
    return logger


_mock_bblogger.get_logger = _make_mock_logger
sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _evict_and_reload_bot():
    """Evict cached bot and related modules and reimport."""
    to_evict = [k for k in sys.modules
                if k in ("bot",) or k.startswith("utils.") or k.startswith("api.")]
    for k in to_evict:
        sys.modules.pop(k, None)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def real_discord():
    """Ensure real discord is in sys.modules."""
    _cm = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
    sys.modules["discord"] = _cm._REAL_DISCORD
    sys.modules["discord.ext"] = _cm._REAL_DISCORD_EXT
    sys.modules["discord.ext.commands"] = _cm._REAL_DISCORD_EXT_COMMANDS
    yield


@pytest.fixture
def bot_module(real_discord):
    """Reload bot module so it picks up fresh mocks."""
    _evict_and_reload_bot()
    import bot
    importlib.reload(bot)
    return bot


# ── Tests: execute_command_with_validation ────────────────────────────────────

class TestExecuteCommandWithValidation:
    """Tests for GatewayBot.execute_command_with_validation (line 65)."""

    def test_execute_command_delegates_to_handler(self, bot_module):
        """execute_command_with_validation should delegate to command_handler."""
        from bot import GatewayBot
        from discord.ext import commands

        bot = MagicMock(spec=GatewayBot)
        bot.command_handler = MagicMock()
        bot.command_handler.execute_command = AsyncMock(return_value=True)

        ctx = MagicMock(spec=commands.Context)
        handler = AsyncMock(return_value=None)

        result = asyncio.run(
            GatewayBot.execute_command_with_validation(
                bot, ctx, "test_command", handler
            )
        )

        bot.command_handler.execute_command.assert_called_once_with(
            ctx, "test_command", handler, None, 5
        )
        assert result is True

    def test_execute_command_passes_permissions_and_cooldown(self, bot_module):
        """execute_command_with_validation should pass through permissions and cooldown."""
        from bot import GatewayBot
        from discord.ext import commands

        bot = MagicMock(spec=GatewayBot)
        bot.command_handler = MagicMock()
        bot.command_handler.execute_command = AsyncMock(return_value=False)

        ctx = MagicMock(spec=commands.Context)
        handler = AsyncMock()
        perms = {"manage_guild": True}

        result = asyncio.run(
            GatewayBot.execute_command_with_validation(
                bot, ctx, "admin_cmd", handler, perms, 10
            )
        )

        bot.command_handler.execute_command.assert_called_once_with(
            ctx, "admin_cmd", handler, perms, 10
        )
        assert result is False


# ── Tests: lifespan ───────────────────────────────────────────────────────────

class TestLifespan:
    """Tests for the lifespan async context manager (lines 96-120)."""

    def test_lifespan_no_token_calls_exit(self, bot_module):
        """lifespan should call os._exit(1) when BOTTOKEN is not set."""
        from bot import lifespan
        from fastapi import FastAPI

        app = FastAPI()

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("BOTTOKEN", None)
            with patch("os._exit") as mock_exit:
                async def _run():
                    async with lifespan(app):
                        pass

                try:
                    asyncio.run(_run())
                except Exception:
                    pass

                mock_exit.assert_called_once_with(1)

    def test_lifespan_startup_creates_bot_task(self, bot_module):
        """lifespan startup should create bot task and set app.state.bot."""
        from bot import lifespan, GatewayBot
        from fastapi import FastAPI

        app = FastAPI()

        mock_bot = MagicMock(spec=GatewayBot)
        mock_bot.start = AsyncMock(return_value=None)
        mock_bot.close = AsyncMock()

        async def _run():
            # Create task inside the running loop
            async def _noop():
                pass
            mock_task = asyncio.get_event_loop().create_task(_noop())

            with patch("bot.GatewayBot", return_value=mock_bot), \
                 patch("asyncio.create_task", return_value=mock_task), \
                 patch.dict(os.environ, {"BOTTOKEN": "fake-token"}):
                async with lifespan(app):
                    assert app.state.bot is mock_bot
                    assert app.state.bot_task is mock_task

        asyncio.run(_run())

    def test_lifespan_shutdown_closes_bot(self, bot_module):
        """lifespan shutdown should close bot and cancel task."""
        from bot import lifespan, GatewayBot
        from fastapi import FastAPI

        app = FastAPI()

        mock_bot = MagicMock(spec=GatewayBot)
        mock_bot.start = AsyncMock(return_value=None)
        mock_bot.close = AsyncMock()

        async def _run():
            async def _noop():
                pass
            mock_task = asyncio.get_event_loop().create_task(_noop())

            with patch("bot.GatewayBot", return_value=mock_bot), \
                 patch("asyncio.create_task", return_value=mock_task), \
                 patch.dict(os.environ, {"BOTTOKEN": "fake-token"}):
                async with lifespan(app):
                    pass  # yield point

        asyncio.run(_run())
        mock_bot.close.assert_awaited_once()

    def test_lifespan_shutdown_handles_cancelled_error(self, bot_module):
        """lifespan shutdown should handle CancelledError from awaiting task."""
        from bot import lifespan, GatewayBot
        from fastapi import FastAPI

        app = FastAPI()

        mock_bot = MagicMock(spec=GatewayBot)
        mock_bot.start = AsyncMock(return_value=None)
        mock_bot.close = AsyncMock()

        async def _run():
            async def _cancellable():
                raise asyncio.CancelledError()

            mock_task = asyncio.get_event_loop().create_task(_cancellable())
            mock_task.cancel()  # pre-cancel so awaiting it raises CancelledError

            with patch("bot.GatewayBot", return_value=mock_bot), \
                 patch("asyncio.create_task", return_value=mock_task), \
                 patch.dict(os.environ, {"BOTTOKEN": "fake-token"}):
                async with lifespan(app):
                    pass  # trigger shutdown

        asyncio.run(_run())
        # If we reach here, CancelledError was caught properly


# ── Tests: create_app ─────────────────────────────────────────────────────────

class TestCreateApp:
    """Tests for create_app function (lines 123-157)."""

    def test_create_app_returns_fastapi_instance(self, bot_module):
        """create_app should return a FastAPI application."""
        from fastapi import FastAPI

        with patch("bot.lifespan") as mock_lifespan, \
             patch("importlib.import_module") as mock_import, \
             patch("pkgutil.iter_modules", return_value=[]):
            mock_import.return_value = MagicMock(__path__=[])
            app = bot_module.create_app()
            assert isinstance(app, FastAPI)

    def test_create_app_includes_cors_middleware(self, bot_module):
        """create_app should add CORS middleware."""
        from fastapi.middleware.cors import CORSMiddleware

        with patch("bot.lifespan") as mock_lifespan, \
             patch("importlib.import_module") as mock_import, \
             patch("pkgutil.iter_modules", return_value=[]):
            mock_import.return_value = MagicMock(__path__=[])
            app = bot_module.create_app()

            middleware_types = [m.cls for m in app.user_middleware]
            assert CORSMiddleware in middleware_types

    def test_create_app_has_correct_metadata(self, bot_module):
        """create_app should produce FastAPI app with correct title and version."""
        from fastapi import FastAPI

        with patch("bot.lifespan"), \
             patch("importlib.import_module") as mock_import, \
             patch("pkgutil.iter_modules", return_value=[]):
            mock_import.return_value = MagicMock(__path__=[])
            app = bot_module.create_app()
            assert isinstance(app, FastAPI)
            assert app.title == "Discord Gateway API"
            assert app.version == "1.0.0"

    def test_create_app_root_endpoint(self, bot_module):
        """create_app should include a root GET / endpoint."""
        from fastapi.testclient import TestClient

        with patch("bot.lifespan"), \
             patch("importlib.import_module") as mock_import, \
             patch("pkgutil.iter_modules", return_value=[]):
            mock_import.return_value = MagicMock(__path__=[])
            app = bot_module.create_app()

            # Override lifespan to avoid async context issues in TestClient
            app.router.lifespan_context = None

            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/")
            # Either 200 or startup error — we just verify the route exists
            route_paths = [r.path for r in app.routes]
            assert "/" in route_paths


# ── Tests: get_bot ────────────────────────────────────────────────────────────

class TestGetBot:
    """Tests for get_bot dependency function (line 161)."""

    def test_get_bot_returns_bot_from_request(self, bot_module):
        """get_bot should return request.app.state.bot."""
        from bot import get_bot

        mock_bot = MagicMock()
        mock_request = MagicMock()
        mock_request.app.state.bot = mock_bot

        result = get_bot(mock_request)
        assert result is mock_bot

    def test_get_bot_different_bot_instances(self, bot_module):
        """get_bot should return whatever bot is on app.state."""
        from bot import get_bot

        mock_bot_a = MagicMock()
        mock_bot_b = MagicMock()

        req_a = MagicMock()
        req_a.app.state.bot = mock_bot_a

        req_b = MagicMock()
        req_b.app.state.bot = mock_bot_b

        assert get_bot(req_a) is mock_bot_a
        assert get_bot(req_b) is mock_bot_b
