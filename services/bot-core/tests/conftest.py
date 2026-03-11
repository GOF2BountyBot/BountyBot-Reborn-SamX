"""Service-specific fixtures for bot-core tests."""

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Add the src directory to the path so imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
# Add the tests directory to the path so test helpers (e.g. conftest) are importable
sys.path.insert(0, os.path.dirname(__file__))

# Mock the shared.bblogger module before any app code imports it.
# The shared library lives outside bot-core and is not on the test Python path.
_mock_shared = types.ModuleType("shared")
_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(name: str = "test") -> MagicMock:
    """Create a mock logger with all standard logging methods."""
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    return logger


_mock_bblogger.get_logger = _make_mock_logger

_mock_shared.bblogger = _mock_bblogger

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger


@pytest.fixture
def mock_db_manager():
    """Mock database manager that avoids real DB connections."""
    manager = AsyncMock()
    manager.initialize = AsyncMock()
    manager.shutdown = MagicMock()
    manager.get_health_info = AsyncMock(
        return_value={
            "connectivity": True,
            "status": "healthy",
            "host": "localhost",
            "port": 5432,
            "database": "test_db",
        }
    )
    manager._connection_string = "postgresql+asyncpg://test:test@localhost:5432/test_db"
    return manager


@pytest.fixture
def mock_schema_manager():
    """Mock schema manager."""
    manager = AsyncMock()
    manager.get_schema_health_info = AsyncMock(
        return_value={
            "version_match": True,
            "current_version": "1.0.0",
            "expected_version": "1.0.0",
            "status": "current",
        }
    )
    return manager


@pytest.fixture
def test_app(mock_db_manager, mock_schema_manager):
    """Create a test FastAPI app without real DB or scheduler."""
    app = FastAPI(title="BountyBot API Test")

    # Import routers individually to avoid auto-discovery issues
    from api.routers.health import router as health_router

    app.include_router(health_router, prefix="/api/v1")

    # Set up mock state
    app.state.db_manager = mock_db_manager
    app.state.schema_manager = mock_schema_manager

    return app


@pytest.fixture
def client(test_app):
    """Create a test client for the bot-core API."""
    return TestClient(test_app)


@pytest.fixture
def mock_db_session():
    """Create a mock async database session for router tests."""
    return AsyncMock()


@pytest.fixture
def mock_db_context(mock_db_session):
    """Create a mock get_db_session context manager.

    Use with patch("api.routers.ROUTER_MODULE.get_db_session") in individual tests.
    Returns (mock_get_db_function, mock_session) tuple.
    """
    mock_get_db = MagicMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_db_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_get_db.return_value = mock_ctx
    return mock_get_db, mock_db_session


def make_mock_player(**overrides):
    """Create a mock player object with sensible defaults."""
    from datetime import datetime

    defaults = dict(
        id=1,
        user_id=12345,
        guild_id=67890,
        credits=100,
        lifetime_credits=100,
        systems_checked=0,
        bounty_wins=0,
        xp=0,
        tier="Bronze",
        prestige_count=0,
        duel_wins=0,
        duel_losses=0,
        duel_credits_won=0,
        duel_credits_lost=0,
        active_ship_id=None,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )
    defaults.update(overrides)
    player = MagicMock()
    for k, v in defaults.items():
        setattr(player, k, v)
    return player
