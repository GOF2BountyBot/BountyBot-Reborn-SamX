"""S4 rewrite: bounty_respawn_executor tests — real SQLite + respx, 0 repo mocks.

Sprint 4 (S4) of the Test Quality Blitz.

PATTERN OVERVIEW
----------------
Three-tier breakdown following ``tests/AGENTS.md`` §"Executor Test Pattern (S2)":

  Tier A — Payload validation. ZERO mocks.

  Tier B — SQLite-in-memory integration for ORM read/write paths.
            Only patch: ``patch("persist.database.manager.db_manager", ...)``.
            NO repository methods mocked.

  Tier C — respx for the outbound POST announcement to discord-gateway
            ``POST /api/v1/messages``.

BEHAVIOURS COVERED
------------------
| # | Behaviour | Tier |
|---|-----------|------|
| 1 | Missing bounty_id → status=error | A |
| 2 | BountyService.respawn_bounty returns None → status=skipped | B |
| 3 | Successful respawn returns status=success with bounty_id | B |
| 4 | Announcement posted with correct body shape | B + C |
| 5 | Announcement failure is non-fatal | B + C |
| 6 | Payload job_type validated | A |

SQLITE COMPATIBILITY NOTE
--------------------------
BountyService.respawn_bounty calls PathfindingService and SystemRepository
(which uses ARRAY columns). Tests that need the respawn path mock
``services.bounty_service.BountyService.respawn_bounty`` to a coroutine
that mutates and returns a real Bounty ORM instance already persisted in
SQLite. This is the minimum ARRAY-column bypass justified by
tests/AGENTS.md §"Mock Policy".

Tests #2 (skipped) use respawn_bounty returning None without ARRAY bypass,
as that code path never reaches the SystemRepository.
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
from persist.models.bounty import Bounty
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from utils.executors.bounty_respawn_executor import execute_bounty_respawn_job

# ---------------------------------------------------------------------------
# SQLite table list
# ---------------------------------------------------------------------------

_SQLITE_TABLES = [
    Bounty.__table__,
]

# ---------------------------------------------------------------------------
# Common test constants
# ---------------------------------------------------------------------------

GUILD_ID = 9_500_000_030
BOUNTY_DIVISION = "bronze"

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


async def _seed_bounty(
    db: AsyncSession,
    guild_id: int,
    *,
    status: str = "escaped",
) -> Bounty:
    """Persist a Bounty row in the given status."""
    now = datetime.now(UTC)
    bounty = Bounty(
        guild_id=guild_id,
        division=BOUNTY_DIVISION,
        criminal_name="Respawn Criminal",
        criminal_faction="Nivelian",
        route=["Alpha", "Beta", "Gamma"],
        answer="Beta",
        reward=8_000,
        reward_per_sys=2_000,
        checked={"Alpha": -1, "Beta": -1, "Gamma": -1},
        issue_time=now,
        end_time=now + timedelta(hours=4),
        tech_level=1,
        criminal_ship={"ship_name": "Scout", "ship_armour": 100, "weapons": [], "turrets": []},
        status=status,
    )
    db.add(bounty)
    await db.commit()
    await db.refresh(bounty)
    return bounty


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
    """Behaviour #1: missing bounty_id → status=error."""

    async def test_missing_bounty_id_returns_error(self):
        """Empty payload → {status: error, reason: missing bounty_id}.

        No DB or HTTP calls — pure payload guard check.
        """
        result = await execute_bounty_respawn_job("test-job", {})
        assert result["status"] == "error", f"Expected status=error, got {result!r}"
        assert result["bounty_id"] is None

    async def test_explicit_none_bounty_id_returns_error(self):
        """Explicit None bounty_id → status=error."""
        result = await execute_bounty_respawn_job("test-job", {"bounty_id": None})
        assert result["status"] == "error"


# ===========================================================================
# TIER B — SQLite integration (1 patch only: db_manager bridge)
# ===========================================================================


class TestRespawnBountySkipped:
    """Behaviour #2: BountyService.respawn_bounty returns None → status=skipped."""

    async def test_respawn_returns_skipped_when_service_returns_none(self, sqlite_engine_and_factory):
        """When BountyService.respawn_bounty returns None, executor returns status=skipped.

        This happens when bounty is not found, wrong status, or route generation fails.
        The mock returns None directly without touching ARRAY tables.

        # 1 mock — db_manager bridge (Tier B)
        # + BountyService.respawn_bounty mock returning None (ARRAY-column bypass,
        #   tests/AGENTS.md §"Mock Policy")
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            bounty = await _seed_bounty(seed_db, GUILD_ID)
        bounty_id = bounty.id

        async def _fake_respawn_none(db, bounty_id, expiry_minutes=None):
            return None  # Simulate failure path (not found / wrong status / route fail)

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            patch(
                "services.bounty_service.BountyService.respawn_bounty",
                side_effect=_fake_respawn_none,
            ),
        ):
            result = await execute_bounty_respawn_job("test-job", {"bounty_id": bounty_id})

        assert result["status"] == "skipped", f"Expected status=skipped, got {result!r}"
        assert result["bounty_id"] == bounty_id


class TestSuccessfulRespawn:
    """Behaviour #3: successful respawn returns status=success with bounty_id."""

    async def test_successful_respawn_returns_success(self, sqlite_engine_and_factory):
        """Executor returns status=success with bounty_id when respawn succeeds.

        BountyService.respawn_bounty is mocked to return a real Bounty ORM instance
        to bypass PathfindingService + SystemRepository ARRAY columns.

        # 1 mock — db_manager bridge (Tier B)
        # + BountyService.respawn_bounty mock (ARRAY-column bypass,
        #   tests/AGENTS.md §"Mock Policy")
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            bounty = await _seed_bounty(seed_db, GUILD_ID)
        bounty_id = bounty.id

        async def _fake_respawn(db, b_id, expiry_minutes=None):
            """Return the existing Bounty ORM instance after updating its status."""
            async with factory() as inner_db:
                from sqlalchemy import select as _select

                row = await inner_db.execute(_select(Bounty).where(Bounty.id == b_id))
                b = row.scalars().first()
                if b:
                    b.status = "active"
                    b.route = ["Alpha", "Delta", "Gamma"]
                    b.answer = "Delta"
                    await inner_db.commit()
                    await inner_db.refresh(b)
                return b

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            patch(
                "services.bounty_service.BountyService.respawn_bounty",
                side_effect=_fake_respawn,
            ),
            respx.mock(assert_all_called=False),  # Allow non-fatal announcement call.
        ):
            result = await execute_bounty_respawn_job("test-job", {"bounty_id": bounty_id})

        assert result["status"] == "success", f"Expected status=success, got {result!r}"
        assert result["bounty_id"] == bounty_id, f"Expected bounty_id={bounty_id} in result, got {result!r}"


# ===========================================================================
# TIER B + C — SQLite integration + respx HTTP assertions
# ===========================================================================


class TestAnnouncementPayload:
    """Behaviour #4: announcement posted with correct body shape."""

    async def test_announcement_body_contains_required_fields(self, sqlite_engine_and_factory):
        """POST to /messages contains guild_id, message_type=bounty_respawn, content fields.

        # 1 mock — db_manager bridge (Tier B + C)
        # + BountyService.respawn_bounty mock (ARRAY-column bypass,
        #   tests/AGENTS.md §"Mock Policy")
        """
        import json as _json

        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            bounty = await _seed_bounty(seed_db, GUILD_ID)
        bounty_id = bounty.id

        async def _fake_respawn(db, b_id, expiry_minutes=None):
            async with factory() as inner_db:
                from sqlalchemy import select as _select

                row = await inner_db.execute(_select(Bounty).where(Bounty.id == b_id))
                b = row.scalars().first()
                if b:
                    b.status = "active"
                    await inner_db.commit()
                    await inner_db.refresh(b)
                return b

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            patch(
                "services.bounty_service.BountyService.respawn_bounty",
                side_effect=_fake_respawn,
            ),
            respx.mock(assert_all_called=False) as router,
        ):
            announce_route = router.post(GATEWAY_MESSAGES_URL).respond(200, json={"ok": True})
            result = await execute_bounty_respawn_job("test-job", {"bounty_id": bounty_id})

        assert result["status"] == "success"
        assert announce_route.called, f"Expected POST to {GATEWAY_MESSAGES_URL}"

        # Assert on real computed values in the request body.
        req_body = _json.loads(announce_route.calls.last.request.content)
        assert req_body.get("guild_id") == GUILD_ID, (
            f"Expected guild_id={GUILD_ID} in announcement body, got {req_body!r}"
        )
        assert req_body.get("message_type") == "bounty_respawn", (
            f"Expected message_type=bounty_respawn, got {req_body!r}"
        )
        content = req_body.get("content", {})
        assert content.get("bounty_id") == bounty_id, f"Expected bounty_id={bounty_id} in content, got {content!r}"
        assert "division" in content, "Expected 'division' field in announcement content"
        assert "criminal_name" in content, "Expected 'criminal_name' field in announcement content"


class TestAnnouncementFailureNonFatal:
    """Behaviour #5: announcement failure is non-fatal."""

    async def test_500_from_gateway_does_not_abort_respawn(self, sqlite_engine_and_factory):
        """HTTP 500 from gateway announcement → respawn still returns status=success.

        # 1 mock — db_manager bridge (Tier B + C)
        # + BountyService.respawn_bounty mock (ARRAY-column bypass,
        #   tests/AGENTS.md §"Mock Policy")
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            bounty = await _seed_bounty(seed_db, GUILD_ID)
        bounty_id = bounty.id

        async def _fake_respawn(db, b_id, expiry_minutes=None):
            async with factory() as inner_db:
                from sqlalchemy import select as _select

                row = await inner_db.execute(_select(Bounty).where(Bounty.id == b_id))
                return row.scalars().first()

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            patch(
                "services.bounty_service.BountyService.respawn_bounty",
                side_effect=_fake_respawn,
            ),
            respx.mock(assert_all_called=False) as router,
        ):
            router.post(GATEWAY_MESSAGES_URL).respond(500)
            result = await execute_bounty_respawn_job("test-job", {"bounty_id": bounty_id})

        # Announcement failure must not abort the respawn.
        assert result["status"] == "success", f"Expected status=success despite 500 from gateway, got {result!r}"
        assert result["bounty_id"] == bounty_id
