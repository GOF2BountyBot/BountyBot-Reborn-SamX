"""Unit tests for SchemaManager – mock-based (no real database).

Covers all methods in persist/schemas/schema_manager.py which currently has
0% coverage.
"""

import os
import sys
from contextlib import asynccontextmanager
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Mock shared.bblogger BEFORE any src imports
# ---------------------------------------------------------------------------
_mock_shared = ModuleType("shared")
_mock_shared.bblogger = MagicMock()
_mock_shared.bblogger.get_logger = MagicMock(return_value=MagicMock())
sys.modules.setdefault("shared", _mock_shared)
sys.modules.setdefault("shared.bblogger", _mock_shared.bblogger)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from persist.schemas.schema_manager import (
    CURRENT_SCHEMA_VERSION,
    SchemaManager,
    initialize_schema,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scalars_result(items) -> MagicMock:
    """Mimic execute() → .scalars().first() chain."""
    scalars_mock = MagicMock()
    scalars_mock.first = MagicMock(return_value=items[0] if items else None)
    scalars_mock.all = MagicMock(return_value=items)
    result_mock = MagicMock()
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    return result_mock


def _make_schema_version(version: str = CURRENT_SCHEMA_VERSION) -> MagicMock:
    obj = MagicMock()
    obj.version = version
    return obj


def _mock_db_manager_with_session(session_mock):
    """Create a db_manager mock whose get_session() yields session_mock."""
    db_manager = MagicMock()

    @asynccontextmanager
    async def _get_session():
        yield session_mock

    db_manager.get_session = _get_session
    db_manager.engine = MagicMock()
    return db_manager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock()
    return session


@pytest.fixture
def mock_db_manager(mock_session):
    return _mock_db_manager_with_session(mock_session)


@pytest.fixture
def manager(mock_db_manager):
    return SchemaManager(mock_db_manager)


# ===================================================================
# __init__
# ===================================================================

class TestInit:
    def test_stores_db_manager(self, mock_db_manager):
        mgr = SchemaManager(mock_db_manager)
        assert mgr.db_manager is mock_db_manager


# ===================================================================
# initialize_database
# ===================================================================

class TestInitializeDatabase:
    @pytest.mark.asyncio
    async def test_initialize_with_create_all_true(self, manager, mock_session):
        """When run_create_all=True, create_tables_if_not_exist is called."""
        # Mock create_tables_if_not_exist
        manager.create_tables_if_not_exist = AsyncMock()
        manager._verify_schema_version = AsyncMock()

        await manager.initialize_database(run_create_all=True)

        manager.create_tables_if_not_exist.assert_awaited_once()
        manager._verify_schema_version.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_initialize_with_create_all_false(self, manager, mock_session):
        """When run_create_all=False (default), skip create_tables."""
        manager.create_tables_if_not_exist = AsyncMock()
        manager._verify_schema_version = AsyncMock()

        await manager.initialize_database(run_create_all=False)

        manager.create_tables_if_not_exist.assert_not_awaited()
        manager._verify_schema_version.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_initialize_default_skips_create(self, manager, mock_session):
        """Default call skips create_tables."""
        manager.create_tables_if_not_exist = AsyncMock()
        manager._verify_schema_version = AsyncMock()

        await manager.initialize_database()

        manager.create_tables_if_not_exist.assert_not_awaited()


# ===================================================================
# create_tables_if_not_exist
# ===================================================================

class TestCreateTablesIfNotExist:
    @pytest.mark.asyncio
    async def test_create_tables_success(self, manager):
        """Calls run_sync(Base.metadata.create_all) via engine.begin()."""
        mock_conn = AsyncMock()
        mock_conn.run_sync = AsyncMock()

        @asynccontextmanager
        async def _begin():
            yield mock_conn

        manager.db_manager.engine.begin = _begin

        await manager.create_tables_if_not_exist()

        mock_conn.run_sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_tables_error_raises(self, manager):
        """Exception during create_all is re-raised."""
        @asynccontextmanager
        async def _begin():
            raise Exception("DDL failure")
            yield  # noqa: unreachable  # pylint: disable=unreachable

        manager.db_manager.engine.begin = _begin

        with pytest.raises(Exception, match="DDL failure"):
            await manager.create_tables_if_not_exist()


# ===================================================================
# _verify_schema_version
# ===================================================================

class TestVerifySchemaVersion:
    @pytest.mark.asyncio
    async def test_first_time_creates_version_row(self, manager, mock_session):
        """When no SchemaVersion row exists, one is created."""
        mock_session.execute = AsyncMock(return_value=_make_scalars_result([]))

        await manager._verify_schema_version()

        mock_session.add.assert_called_once()
        added_obj = mock_session.add.call_args[0][0]
        assert added_obj.version == CURRENT_SCHEMA_VERSION
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_version_matches_logs_info(self, manager, mock_session):
        """When DB version matches current, just logs info."""
        sv = _make_schema_version(CURRENT_SCHEMA_VERSION)
        mock_session.execute = AsyncMock(return_value=_make_scalars_result([sv]))

        await manager._verify_schema_version()

        mock_session.add.assert_not_called()
        mock_session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_version_mismatch_logs_warning(self, manager, mock_session):
        """When DB version differs, a warning is logged (no crash)."""
        sv = _make_schema_version("0.9.0")
        mock_session.execute = AsyncMock(return_value=_make_scalars_result([sv]))

        await manager._verify_schema_version()

        mock_session.add.assert_not_called()
        mock_session.commit.assert_not_awaited()


# ===================================================================
# get_current_version
# ===================================================================

class TestGetCurrentVersion:
    @pytest.mark.asyncio
    async def test_returns_version_string(self, manager, mock_session):
        sv = _make_schema_version("1.0.0")
        mock_session.execute = AsyncMock(return_value=_make_scalars_result([sv]))

        result = await manager.get_current_version()

        assert result == "1.0.0"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_row(self, manager, mock_session):
        mock_session.execute = AsyncMock(return_value=_make_scalars_result([]))

        result = await manager.get_current_version()

        assert result is None


# ===================================================================
# get_schema_health_info
# ===================================================================

class TestGetSchemaHealthInfo:
    @pytest.mark.asyncio
    async def test_health_info_version_match(self, manager, mock_session):
        sv = _make_schema_version(CURRENT_SCHEMA_VERSION)
        mock_session.execute = AsyncMock(return_value=_make_scalars_result([sv]))

        info = await manager.get_schema_health_info()

        assert info["version"] == CURRENT_SCHEMA_VERSION
        assert info["expected_version"] == CURRENT_SCHEMA_VERSION
        assert info["version_match"] is True

    @pytest.mark.asyncio
    async def test_health_info_version_mismatch(self, manager, mock_session):
        sv = _make_schema_version("0.5.0")
        mock_session.execute = AsyncMock(return_value=_make_scalars_result([sv]))

        info = await manager.get_schema_health_info()

        assert info["version"] == "0.5.0"
        assert info["version_match"] is False

    @pytest.mark.asyncio
    async def test_health_info_exception_returns_error_dict(self, manager, mock_session):
        mock_session.execute = AsyncMock(side_effect=Exception("DB unreachable"))

        info = await manager.get_schema_health_info()

        assert info["status"] == "error"
        assert "DB unreachable" in info["error"]


# ===================================================================
# initialize_schema (module-level factory)
# ===================================================================

class TestInitializeSchema:
    @pytest.mark.asyncio
    async def test_factory_creates_and_initializes(self, mock_session):
        db_manager = _mock_db_manager_with_session(mock_session)
        mock_session.execute = AsyncMock(return_value=_make_scalars_result([]))

        result = await initialize_schema(db_manager)

        assert isinstance(result, SchemaManager)
        assert result.db_manager is db_manager
        # verify_schema_version was called (it adds a SchemaVersion row)
        mock_session.add.assert_called_once()
