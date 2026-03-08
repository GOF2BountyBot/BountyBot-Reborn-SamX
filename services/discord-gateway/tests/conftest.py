"""Service-specific fixtures for discord-gateway tests."""
import sys
import os
import types
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Mock shared.bblogger BEFORE any application imports.
# The gateway source does ``import shared.bblogger as bblogger`` at module
# level, so we need fake modules registered in sys.modules before the import
# chain reaches them.
# ---------------------------------------------------------------------------

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []  # mark as package so sub-imports work

_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_args, **_kwargs):
    """Return a MagicMock that already has common log-level methods."""
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    return logger


_mock_bblogger.get_logger = _make_mock_logger  # type: ignore[attr-defined]

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

# ---------------------------------------------------------------------------
# Now it is safe to add the src directory and import application code.
# ---------------------------------------------------------------------------

import pytest
from unittest.mock import AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Add the src directory to the path so imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture
def mock_discord_bot():
    """Mock Discord bot instance."""
    bot = MagicMock()
    bot.user = MagicMock()
    bot.user.id = 123456789
    bot.user.name = "TestBot"
    bot.guilds = []
    bot.get_guild = MagicMock(return_value=None)
    bot.get_channel = MagicMock(return_value=None)
    bot.wait_until_ready = AsyncMock()
    return bot


@pytest.fixture
def test_app():
    """Create a test FastAPI app for discord-gateway."""
    app = FastAPI(title="Discord Gateway API Test")

    from api.routers.health import router as health_router

    app.include_router(health_router, prefix="/api/v1")

    return app


@pytest.fixture
def client(test_app):
    """Create a test client for the discord-gateway API."""
    return TestClient(test_app)
