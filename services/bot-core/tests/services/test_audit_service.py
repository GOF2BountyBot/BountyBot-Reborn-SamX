"""
Unit tests for AuditService.

Strategy:
- Happy-path tests run against a REAL in-memory SQLite AsyncSession + the REAL
  AdminAuditLog model, then SELECT the persisted row back to assert the real
  write/serialisation behaviour (no session mock).
- Graceful-degradation tests keep a faithful (spec'd) AsyncSession mock, because
  they inject a commit()/rollback() failure that a real committed session cannot
  naturally produce — a genuine failure-boundary mock.
- Tests cover: successful log creation, graceful DB failure, JSON serialisation.
"""

from __future__ import annotations

import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Bootstrap shared.bblogger stub so the module can be imported without the
# actual shared library installed in the test environment.
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

# Stub sqlalchemy_utils (pulled in transitively via models __init__ auto-import)
if "sqlalchemy_utils" not in sys.modules:
    _sqla_utils = types.ModuleType("sqlalchemy_utils")
    _sqla_utils.UUIDType = MagicMock()
    sys.modules["sqlalchemy_utils"] = _sqla_utils

# AdminAuditLog is already registered in sys.modules by the auto-import in
# persist/models/__init__.py (triggered above). Import via the canonical path
# so we reference the same class object that AuditService uses.
from persist.models.admin_audit_log import AdminAuditLog
from persist.models.base import Base
from services.audit_service import AuditService

# ---------------------------------------------------------------------------
# Real in-memory SQLite session (AdminAuditLog is ARRAY/UUID-free → SQLite-creatable)
# ---------------------------------------------------------------------------


@pytest.fixture
async def audit_engine():
    """Fresh in-memory SQLite engine with only the admin_audit_logs table."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[AdminAuditLog.__table__])
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(audit_engine) -> AsyncSession:
    """A real AsyncSession over the in-memory engine (no mock)."""
    session_factory = async_sessionmaker(audit_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_db_session() -> AsyncMock:
    """Return a FAITHFUL AsyncSession mock (spec'd) for failure-injection tests only.

    Used where a real committed session cannot naturally fail (commit/rollback raising).
    spec=AsyncSession keeps the surface honest: add() is sync (MagicMock), commit/rollback
    are awaitable (AsyncMock).
    """
    db = AsyncMock(spec=AsyncSession)
    db.add = MagicMock()  # add() is synchronous on SQLAlchemy sessions
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLogActionSuccess:
    """Happy-path: audit entry created and committed to a REAL SQLite session."""

    async def _fetch_all(self, db_session) -> list[AdminAuditLog]:
        return list((await db_session.execute(select(AdminAuditLog))).scalars().all())

    @pytest.mark.asyncio
    async def test_adds_entry_and_commits(self, db_session):
        """log_action persists an AdminAuditLog row that is readable back after commit."""
        await AuditService.log_action(
            db_session,
            user_id=123456,
            action="guild_reset",
            guild_id=999,
            resource_type="guild",
            resource_id="999",
            details={"preserve_players": True},
        )

        # Committed → the row is readable back from the real DB in a fresh SELECT.
        rows = await self._fetch_all(db_session)
        assert len(rows) == 1
        entry = rows[0]
        assert entry.user_id == 123456
        assert entry.action == "guild_reset"
        assert entry.guild_id == 999
        assert entry.resource_type == "guild"
        assert entry.resource_id == "999"
        assert entry.status == "success"
        assert entry.id is not None  # autoincrement PK assigned on flush/commit

    @pytest.mark.asyncio
    async def test_details_serialised_as_json(self, db_session):
        """details dict is stored as a JSON string in the persisted audit log entry."""
        details_payload = {"player_id": 42, "credits": 500}

        await AuditService.log_action(
            db_session,
            user_id=1,
            action="credits_update",
            details=details_payload,
        )

        rows = await self._fetch_all(db_session)
        assert len(rows) == 1
        assert rows[0].details is not None
        assert json.loads(rows[0].details) == details_payload

    @pytest.mark.asyncio
    async def test_none_details_stored_as_none(self, db_session):
        """When details=None, the persisted column value is also None (no JSON dump)."""
        await AuditService.log_action(
            db_session,
            user_id=7,
            action="player_reset",
        )

        rows = await self._fetch_all(db_session)
        assert len(rows) == 1
        assert rows[0].details is None

    @pytest.mark.asyncio
    async def test_custom_status_propagated(self, db_session):
        """Explicit status value (e.g. 'failed') is persisted on the entry."""
        await AuditService.log_action(
            db_session,
            user_id=1,
            action="shop_refresh",
            status="failed",
        )

        rows = await self._fetch_all(db_session)
        assert len(rows) == 1
        assert rows[0].status == "failed"


class TestLogActionGracefulDegradation:
    """AuditService must never raise even when the DB operation fails."""

    @pytest.mark.asyncio
    async def test_commit_failure_does_not_raise(self):
        """If commit() raises, log_action swallows the exception and rolls back."""
        db = make_db_session()
        db.commit.side_effect = RuntimeError("DB unavailable")

        # Must not raise
        await AuditService.log_action(
            db,
            user_id=99,
            action="guild_initialize",
        )

        db.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rollback_failure_also_swallowed(self):
        """If both commit() and rollback() raise, log_action still doesn't raise."""
        db = make_db_session()
        db.commit.side_effect = RuntimeError("commit failed")
        db.rollback.side_effect = RuntimeError("rollback also failed")

        # Must not raise
        await AuditService.log_action(
            db,
            user_id=11,
            action="guild_uninstall",
        )
