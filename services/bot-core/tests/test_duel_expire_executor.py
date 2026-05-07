"""S4 rewrite: duel_expire_executor tests — real SQLite + respx, 0 repo mocks.

Sprint 4 (S4) of the Test Quality Blitz.

PATTERN OVERVIEW
----------------
Three-tier breakdown following ``tests/AGENTS.md`` §"Executor Test Pattern (S2)":

  Tier A — Payload validation. ZERO mocks.

  Tier B — SQLite-in-memory integration for ORM read/write paths.
            Only patch: ``patch("persist.database.manager.db_manager", ...)``.
            NO repository methods mocked.

  Tier C — respx for the outbound POST notification to discord-gateway
            ``POST /api/v1/messages``.

BEHAVIOURS COVERED
------------------
| # | Behaviour | Tier |
|---|-----------|------|
| 1 | Missing duel_id → status=error | A |
| 2 | Duel not found → status=skipped (DuelService.expire_duel raises ValueError) | B |
| 3 | Already-expired or wrong-status duel → status=skipped | B |
| 4 | Pending duel is marked expired in DB | B |
| 5 | Notification posted to gateway with correct body | B + C |
| 6 | Gateway notification failure is non-fatal | B + C |
| 7 | Cross-session reload confirms duel status persisted | B |

SQLITE COMPATIBILITY NOTE
--------------------------
DuelRequest has no ARRAY columns and is fully SQLite-compatible.
DuelService.expire_duel calls DuelRepository.get_by_id and DuelRepository.update_status —
both are pure ORM operations.

DuelService constructor imports CombatService + LoadoutBuilder; to avoid
import-chain issues with ARRAY tables, the DuelService is imported lazily
inside the executor via deferred imports. The executor's deferred import
means we patch ``persist.database.manager.db_manager`` (not the executor
module namespace) per the canonical S2 pattern.
"""

from __future__ import annotations

import os
import sys
import types
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup and stub registration
# ---------------------------------------------------------------------------

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_shared.bblogger = MagicMock()  # type: ignore[attr-defined]
    _mock_shared.bblogger.get_logger = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_shared.bblogger  # type: ignore[arg-type]

if "sqlalchemy_utils" not in sys.modules:
    _mock_sau = types.ModuleType("sqlalchemy_utils")
    _mock_sau.UUIDType = MagicMock()  # type: ignore[attr-defined]
    sys.modules["sqlalchemy_utils"] = _mock_sau

# ---------------------------------------------------------------------------
# Application imports
# ---------------------------------------------------------------------------

import pytest
import respx
from persist.models.base import Base
from persist.models.duel_request import DuelRequest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from utils.executors.duel_expire_executor import execute_duel_expire_job

# ---------------------------------------------------------------------------
# SQLite table list
# ---------------------------------------------------------------------------

_SQLITE_TABLES = [
    DuelRequest.__table__,
]

# ---------------------------------------------------------------------------
# Common test constants
# ---------------------------------------------------------------------------

GUILD_ID = 9_500_000_040
CHALLENGER_ID = 101
TARGET_ID = 102
STAKES = 500

GATEWAY_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
GATEWAY_PORT = os.getenv("GATEWAY_PORT", "7999")
GATEWAY_MESSAGES_URL = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/api/v1/messages"


# ===========================================================================
# Shared fixtures
# ===========================================================================


@pytest.fixture
async def sqlite_engine_and_factory():
    """Yield a fresh SQLite in-memory engine + session factory per test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_SQLITE_TABLES)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield engine, factory
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all, tables=_SQLITE_TABLES)
        await engine.dispose()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_duel(
    db: AsyncSession,
    guild_id: int,
    *,
    status: str = "pending",
    stakes: int = STAKES,
) -> DuelRequest:
    """Persist a DuelRequest row."""
    now = datetime.now(UTC)
    duel = DuelRequest(
        guild_id=guild_id,
        challenger_id=CHALLENGER_ID,
        target_id=TARGET_ID,
        stakes=stakes,
        status=status,
        created_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    db.add(duel)
    await db.commit()
    await db.refresh(duel)
    return duel


def _make_fake_db_manager(factory: Any):
    """Build a MagicMock that mimics db_manager.get_session() for SQLite.

    # 1 mock — db_manager bridge (Tier B)
    """

    @asynccontextmanager
    async def _fake_get_db():
        async with factory() as session:
            yield session

    fake = MagicMock()
    fake.get_session = MagicMock(side_effect=_fake_get_db)
    return fake


# ===========================================================================
# TIER A — Pure unit tests (ZERO mocks)
# ===========================================================================


class TestPayloadValidation:
    """Behaviour #1: missing duel_id → status=error."""

    async def test_missing_duel_id_returns_error(self):
        """Empty payload → {status: error, reason: missing duel_id}.

        No DB or HTTP calls — pure payload guard.
        """
        result = await execute_duel_expire_job("test-job", {})
        assert result["status"] == "error", f"Expected status=error, got {result!r}"
        assert result["duel_id"] is None

    async def test_explicit_none_duel_id_returns_error(self):
        """Explicit None duel_id → status=error."""
        result = await execute_duel_expire_job("test-job", {"duel_id": None})
        assert result["status"] == "error"


# ===========================================================================
# TIER B — SQLite integration (1 patch only: db_manager bridge)
# ===========================================================================


class TestDuelNotFound:
    """Behaviour #2: duel not found → status=skipped."""

    async def test_nonexistent_duel_id_returns_skipped(self, sqlite_engine_and_factory):
        """DuelService.expire_duel raises ValueError for missing duel → status=skipped.

        # 1 mock — db_manager bridge (Tier B)
        """
        _engine, factory = sqlite_engine_and_factory
        payload = {"duel_id": 999_999}

        with patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)):
            result = await execute_duel_expire_job("test-job", payload)

        assert result["status"] == "skipped", f"Expected status=skipped, got {result!r}"
        assert result["duel_id"] == 999_999


class TestAlreadyExpiredOrWrongStatus:
    """Behaviour #3: already-expired or wrong-status duel → status=skipped."""

    async def test_already_expired_duel_returns_skipped(self, sqlite_engine_and_factory):
        """A duel with status='expired' causes DuelService to raise ValueError → skipped.

        # 1 mock — db_manager bridge (Tier B)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            duel = await _seed_duel(seed_db, GUILD_ID, status="expired")
        duel_id = duel.id

        with patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)):
            result = await execute_duel_expire_job("test-job", {"duel_id": duel_id})

        assert result["status"] == "skipped", f"Expected status=skipped for already-expired duel, got {result!r}"
        assert result["duel_id"] == duel_id

    async def test_completed_duel_returns_skipped(self, sqlite_engine_and_factory):
        """A duel with status='completed' causes DuelService to raise ValueError → skipped.

        # 1 mock — db_manager bridge (Tier B)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            duel = await _seed_duel(seed_db, GUILD_ID, status="completed")
        duel_id = duel.id

        with patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)):
            result = await execute_duel_expire_job("test-job", {"duel_id": duel_id})

        assert result["status"] == "skipped"


class TestPendingDuelExpired:
    """Behaviour #4: pending duel is marked expired in DB."""

    async def test_pending_duel_status_set_to_expired(self, sqlite_engine_and_factory):
        """Executor calls expire_duel; DB row status changes from 'pending' to 'expired'.

        Cross-session reload verifies the status was persisted.

        # 1 mock — db_manager bridge (Tier B)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            duel = await _seed_duel(seed_db, GUILD_ID, status="pending")
        duel_id = duel.id

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_called=False),  # Allow non-fatal HTTP notification.
        ):
            result = await execute_duel_expire_job("test-job", {"duel_id": duel_id})

        assert result["status"] == "success", f"Expected status=success, got {result!r}"
        assert result["duel_id"] == duel_id

        # Cross-session reload: verify the DB row was updated.
        async with factory() as verify_db:
            row = await verify_db.execute(select(DuelRequest).where(DuelRequest.id == duel_id))
            refreshed = row.scalars().first()

        assert refreshed is not None
        assert refreshed.status == "expired", (
            f"Expected DuelRequest.status='expired' after expire job, got {refreshed.status!r}"
        )

    async def test_result_contains_duel_id(self, sqlite_engine_and_factory):
        """Executor result dict includes the correct duel_id.

        # 1 mock — db_manager bridge (Tier B)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            duel = await _seed_duel(seed_db, GUILD_ID, status="pending")
        duel_id = duel.id

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_called=False),
        ):
            result = await execute_duel_expire_job("test-job", {"duel_id": duel_id})

        assert result["duel_id"] == duel_id, f"Expected duel_id={duel_id} in result, got {result!r}"


# ===========================================================================
# TIER B + C — SQLite integration + respx HTTP assertions
# ===========================================================================


class TestGatewayNotification:
    """Behaviour #5: notification posted to gateway with correct body."""

    async def test_notification_body_contains_required_fields(self, sqlite_engine_and_factory):
        """POST to /messages contains guild_id, message_type=duel_expire, content fields.

        # 1 mock — db_manager bridge (Tier B + C)
        """
        import json as _json

        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            duel = await _seed_duel(seed_db, GUILD_ID, status="pending", stakes=STAKES)
        duel_id = duel.id

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_called=False) as router,
        ):
            notify_route = router.post(GATEWAY_MESSAGES_URL).respond(200, json={"ok": True})
            result = await execute_duel_expire_job("test-job", {"duel_id": duel_id})

        assert result["status"] == "success"
        assert notify_route.called, f"Expected POST to {GATEWAY_MESSAGES_URL}"

        req_body = _json.loads(notify_route.calls.last.request.content)

        assert req_body.get("guild_id") == GUILD_ID, (
            f"Expected guild_id={GUILD_ID} in notification body, got {req_body!r}"
        )
        assert req_body.get("message_type") == "duel_expire", f"Expected message_type=duel_expire, got {req_body!r}"
        content = req_body.get("content", {})
        assert content.get("duel_id") == duel_id, f"Expected duel_id in content, got {content!r}"
        assert content.get("challenger_id") == CHALLENGER_ID
        assert content.get("target_id") == TARGET_ID
        assert content.get("stakes") == STAKES


class TestNotificationFailureNonFatal:
    """Behaviour #6: gateway notification failure is non-fatal."""

    async def test_500_from_gateway_does_not_abort_expiry(self, sqlite_engine_and_factory):
        """HTTP 500 on gateway notification → duel still expired in DB.

        # 1 mock — db_manager bridge (Tier B + C)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            duel = await _seed_duel(seed_db, GUILD_ID, status="pending")
        duel_id = duel.id

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_called=False) as router,
        ):
            router.post(GATEWAY_MESSAGES_URL).respond(500)
            result = await execute_duel_expire_job("test-job", {"duel_id": duel_id})

        assert result["status"] == "success", f"Expected status=success despite 500 from gateway, got {result!r}"

        # Cross-session reload: duel status must be 'expired' despite notification failure.
        async with factory() as verify_db:
            row = await verify_db.execute(select(DuelRequest).where(DuelRequest.id == duel_id))
            refreshed = row.scalars().first()

        assert refreshed is not None
        assert refreshed.status == "expired", (
            f"Expected DuelRequest.status='expired' even after gateway failure, got {refreshed.status!r}"
        )

    async def test_connection_error_does_not_abort_expiry(self, sqlite_engine_and_factory):
        """Network connection error on notification → duel still expired in DB.

        # 1 mock — db_manager bridge (Tier B + C)
        """
        import httpx as _httpx

        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            duel = await _seed_duel(seed_db, GUILD_ID, status="pending")
        duel_id = duel.id

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_called=False) as router,
        ):
            router.post(GATEWAY_MESSAGES_URL).mock(side_effect=_httpx.ConnectError("timeout"))
            result = await execute_duel_expire_job("test-job", {"duel_id": duel_id})

        assert result["status"] == "success", f"Expected status=success despite network error, got {result!r}"
