"""
Unit tests for AuditService.

Strategy:
- Mock the AsyncSession and AdminAuditLog add/commit/rollback behaviour.
- Max 2 mocks per test (session + one additional where needed).
- Tests cover: successful log creation, graceful DB failure, JSON serialisation.
"""

from __future__ import annotations

import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

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
from services.audit_service import AuditService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_db_session() -> AsyncMock:
    """Return a minimal AsyncSession mock with add, commit, and rollback."""
    db = AsyncMock()
    db.add = MagicMock()          # add() is synchronous on SQLAlchemy sessions
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLogActionSuccess:
    """Happy-path: audit entry created and committed."""

    @pytest.mark.asyncio
    async def test_adds_entry_and_commits(self):
        """log_action adds an AdminAuditLog record and commits the session."""
        db = make_db_session()

        await AuditService.log_action(
            db,
            user_id=123456,
            action="guild_reset",
            guild_id=999,
            resource_type="guild",
            resource_id="999",
            details={"preserve_players": True},
        )

        # db.add() must have been called once with an AdminAuditLog instance
        assert db.add.call_count == 1
        added_obj = db.add.call_args[0][0]
        assert isinstance(added_obj, AdminAuditLog)
        assert added_obj.user_id == 123456
        assert added_obj.action == "guild_reset"
        assert added_obj.guild_id == 999
        assert added_obj.resource_type == "guild"
        assert added_obj.resource_id == "999"
        assert added_obj.status == "success"

        db.commit.assert_awaited_once()
        db.rollback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_details_serialised_as_json(self):
        """details dict is stored as a JSON string in the audit log entry."""
        db = make_db_session()
        details_payload = {"player_id": 42, "credits": 500}

        await AuditService.log_action(
            db,
            user_id=1,
            action="credits_update",
            details=details_payload,
        )

        added_obj = db.add.call_args[0][0]
        assert added_obj.details is not None
        parsed = json.loads(added_obj.details)
        assert parsed == details_payload

    @pytest.mark.asyncio
    async def test_none_details_stored_as_none(self):
        """When details=None, the DB column value is also None (no JSON dump)."""
        db = make_db_session()

        await AuditService.log_action(
            db,
            user_id=7,
            action="player_reset",
        )

        added_obj = db.add.call_args[0][0]
        assert added_obj.details is None

    @pytest.mark.asyncio
    async def test_custom_status_propagated(self):
        """Explicit status value (e.g. 'failed') is stored on the entry."""
        db = make_db_session()

        await AuditService.log_action(
            db,
            user_id=1,
            action="shop_refresh",
            status="failed",
        )

        added_obj = db.add.call_args[0][0]
        assert added_obj.status == "failed"


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
