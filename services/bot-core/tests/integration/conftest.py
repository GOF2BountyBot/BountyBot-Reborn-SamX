"""Integration test fixtures using SQLite in-memory database."""

import os
import sys
from types import ModuleType
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Make src/ importable when pytest is invoked targeting only this directory
# (the parent tests/conftest.py would otherwise be skipped).
# ---------------------------------------------------------------------------

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# ---------------------------------------------------------------------------
# Mock external dependencies before any application imports.
# ---------------------------------------------------------------------------

# shared.bblogger - the shared logging library is not on the test Python path.
_mock_shared = ModuleType("shared")
_mock_shared.bblogger = MagicMock()  # type: ignore[attr-defined]
_mock_shared.bblogger.get_logger = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]
sys.modules.setdefault("shared", _mock_shared)
sys.modules.setdefault("shared.bblogger", _mock_shared.bblogger)  # type: ignore[arg-type]

# sqlalchemy_utils - only used by DiscordMessage model for UUIDType.
_mock_sau = ModuleType("sqlalchemy_utils")
_mock_sau.UUIDType = MagicMock()  # type: ignore[attr-defined]
sys.modules.setdefault("sqlalchemy_utils", _mock_sau)

# ---------------------------------------------------------------------------
# Now it is safe to import application code.
# ---------------------------------------------------------------------------

import pytest
from persist.models.admin_audit_log import AdminAuditLog
from persist.models.base import Base
from persist.models.bounty import Bounty
from persist.models.duel_request import DuelRequest
from persist.models.guild_config import GuildConfig
from persist.models.guild_shop import GuildShop
from persist.models.player import Player
from persist.models.player_inventory import PlayerInventory
from persist.models.player_ship import PlayerShip
from persist.models.user import User
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Collect only the tables that are SQLite-compatible (no ARRAY columns).
_SQLITE_TABLES = [
    User.__table__,
    Player.__table__,
    GuildConfig.__table__,
    GuildShop.__table__,
    PlayerInventory.__table__,
    PlayerShip.__table__,
    Bounty.__table__,
    DuelRequest.__table__,
    AdminAuditLog.__table__,
]


@pytest.fixture
async def async_engine():
    """Create a fresh SQLite in-memory engine for each test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_SQLITE_TABLES)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all, tables=_SQLITE_TABLES)
    await engine.dispose()


@pytest.fixture
async def db_session(async_engine) -> AsyncSession:
    """Create a fresh database session for each test."""
    session_factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# P2-T2: thread-pool fixture for fight_ships-exercising tests
#
# fight_ships now calls offload_cpu(run_fight, ...) which requires a pool
# registered in executor_holder.  A ThreadPoolExecutor is used here for
# speed and correctness — run_fight is pure and correct in a thread.
#
# Import executor_holder at MODULE-IMPORT time (not inside the fixture body)
# so the fixture always targets the same canonical module object that
# combat_service.offload_cpu → utils.offload.get_process_pool references,
# regardless of whether tests/test_offload.py has swapped sys.modules entries
# before this fixture executes.  Mirrors the same pattern in
# tests/services/conftest.py.
# ---------------------------------------------------------------------------

import concurrent.futures

import utils.executor_holder as _holder


@pytest.fixture(scope="session", autouse=True)
def _integration_thread_pool_for_offload():
    """Register a ThreadPoolExecutor for offload_cpu in integration tests.

    Mirrors the same fixture in tests/services/conftest.py.
    Scope: session.  Torn down + holder reset after the session.

    Registers against _holder (the module-level canonical import) to ensure
    order-independence with respect to tests that temporarily swap sys.modules.
    """
    # Only set if not already set by a sibling fixture (idempotent guard).
    if _holder._process_pool is None:
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-int-combat")
        _holder.set_process_pool(pool)
        yield
        pool.shutdown(wait=True)
        _holder._process_pool = None
    else:
        yield
