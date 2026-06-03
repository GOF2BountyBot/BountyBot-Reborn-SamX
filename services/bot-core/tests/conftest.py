"""Service-specific fixtures for bot-core tests."""

import contextlib
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Expose the fixtures/ package so game_data helpers are importable everywhere.
sys.path.insert(0, os.path.dirname(__file__))

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


# Register the `real_push` marker so pytest does not warn about it.
def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_push: opt out of the autouse gateway-push stubs; for tests that "
        "actually exercise utils.executors.*._push_* or api.routers.duels._push_*.",
    )


# Targets stubbed by the autouse fixture below. Each entry is a fully-qualified
# import path. The push helpers all do `async with httpx.AsyncClient() as c:`
# against http://discord-gateway:7999/... — that host does not resolve in the
# test environment, so each call hangs on a ~4s DNS-resolution timeout. Test
# files in tests/api/ and tests/test_shop_refresh_executor.py do not stub them,
# and several tests fire 4-8 such calls per test, accounting for ~85% of total
# suite runtime as of 2026-06-02. Stub at the helper boundary (cheaper than
# patching httpx itself; respx-based tests still work for non-stubbed paths).
_PUSH_STUB_TARGETS = (
    "utils.executors.shop_refresh_executor._push_shop_cache",
    "utils.executors.bounty_spawn_executor._push_bounty_cache",
    "utils.executors.bounty_expire_executor._push_bounty_cache_expire",
    "api.routers.duels._push_duel_cache",
    "api.routers.duels._push_duel_caches_for_players",
    "services.bounty_service.BountyService._push_bounty_cache_after_capture",
)


@pytest.fixture(autouse=True)
def _stub_gateway_push_helpers(request):
    """Stub bot-core push-to-gateway helpers to avoid DNS-timeout cost.

    Tests asserting real push behavior must be decorated with
    @pytest.mark.real_push (see tests/test_executor_push_phase5b.py).
    """
    if request.node.get_closest_marker("real_push"):
        yield
        return

    from unittest.mock import patch as _patch

    started = []
    for target in _PUSH_STUB_TARGETS:
        try:
            p = _patch(target, new=AsyncMock())
            p.start()
            started.append(p)
        except (AttributeError, ModuleNotFoundError, ImportError):
            # Target's module not importable in this test's context — skip silently.
            pass
    try:
        yield
    finally:
        for p in started:
            with contextlib.suppress(RuntimeError):
                p.stop()


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
        display_name=None,
        xp_surplus=0,
        guild_transfer_cooldown=None,
        classic_mode=False,
        bounty_cooldown_end=None,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )
    defaults.update(overrides)
    player = MagicMock()
    for k, v in defaults.items():
        setattr(player, k, v)
    return player


# ---------------------------------------------------------------------------
# Game-data fixtures — sourced from real import_data/ JSON files.
# Each fixture returns a list of SimpleNamespace objects whose attributes
# mirror the corresponding SQLAlchemy model columns.
# ---------------------------------------------------------------------------


@pytest.fixture
def seed_ships():
    """Return 5 Ship-like SimpleNamespace objects from real import_data."""
    from fixtures.game_data import get_seed_ships

    return get_seed_ships()


@pytest.fixture
def seed_primary_weapons():
    """Return 5 PrimaryWeapon-like SimpleNamespace objects from real import_data."""
    from fixtures.game_data import get_seed_primary_weapons

    return get_seed_primary_weapons()


@pytest.fixture
def seed_secondary_weapons():
    """Return 4 SecondaryWeapon-like SimpleNamespace objects from real import_data."""
    from fixtures.game_data import get_seed_secondary_weapons

    return get_seed_secondary_weapons()


@pytest.fixture
def seed_turret_weapons():
    """Return 4 TurretWeapon-like SimpleNamespace objects from real import_data."""
    from fixtures.game_data import get_seed_turret_weapons

    return get_seed_turret_weapons()


@pytest.fixture
def seed_modules():
    """Return 6 Module-like SimpleNamespace objects from real import_data."""
    from fixtures.game_data import get_seed_modules

    return get_seed_modules()


@pytest.fixture
def seed_criminals():
    """Return 5 Criminal-like SimpleNamespace objects from real import_data."""
    from fixtures.game_data import get_seed_criminals

    return get_seed_criminals()


@pytest.fixture
def seed_systems():
    """Return 5 System-like SimpleNamespace objects from real import_data."""
    from fixtures.game_data import get_seed_systems

    return get_seed_systems()
