"""S4 rewrite: bounty_expire_executor tests — real SQLite + respx, 0 repo mocks.

Sprint 4 (S4) of the Test Quality Blitz.

PATTERN OVERVIEW
----------------
Three-tier breakdown following ``tests/AGENTS.md`` §"Executor Test Pattern (S2)":

  Tier A — Payload validation. ZERO mocks.

  Tier B — SQLite-in-memory integration for ORM read/write paths.
            Only patch: ``patch("persist.database.manager.db_manager", ...)``.
            NO repository or service methods mocked.

  Tier C — respx for the outbound DELETE call to discord-gateway
            ``DELETE /api/v1/channels/{channel_id}/messages/{message_id}``.

BEHAVIOURS COVERED
------------------
| # | Behaviour | Tier |
|---|-----------|------|
| 1 | Missing bounty_id → status=error | A |
| 2 | Bounty not found → status=skipped | B |
| 3 | Active bounty is expired (status=expired in DB) | B |
| 4 | Already-captured bounty returns status=success (expire_bounty returns None) | B |
| 5 | Discord announcement deleted via gateway (respx) | B + C |
| 6 | Gateway DELETE failure is non-fatal | B + C |
| 7 | No DiscordMessage record → skipped gracefully | B |
| 8 | Cross-session reload confirms status persisted | B |

SQLITE COMPATIBILITY NOTE
--------------------------
Bounty and DiscordMessage tables are SQLite-compatible (JSON / CHAR columns
only — no ARRAY(String) columns). Both tables are created in the SQLite schema
for all Tier B tests.

BountyService.expire_bounty calls BountyRepository.get_by_id and BountyRepository.update —
both are pure ORM operations that run fine on SQLite.
"""

from __future__ import annotations

import os
import sys
import types
import uuid
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
from persist.models.discord_message import DiscordMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from utils.executors.bounty_expire_executor import execute_bounty_expire_job

# ---------------------------------------------------------------------------
# SQLite table list
# ---------------------------------------------------------------------------

_SQLITE_TABLES = [
    Bounty.__table__,
    DiscordMessage.__table__,
]

# ---------------------------------------------------------------------------
# Common test constants
# ---------------------------------------------------------------------------

GUILD_ID = 9_500_000_020
CHANNEL_ID = 88_001
MESSAGE_ID = 99_001

GATEWAY_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
GATEWAY_PORT = os.getenv("GATEWAY_PORT", "7999")
GATEWAY_DELETE_URL = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/api/v1/channels/{CHANNEL_ID}/messages/{MESSAGE_ID}"


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


async def _seed_active_bounty(db: AsyncSession, guild_id: int, *, status: str = "active") -> Bounty:
    """Persist a Bounty row (active by default, future end_time)."""
    now = datetime.now(UTC)
    bounty = Bounty(
        guild_id=guild_id,
        division="bronze",
        criminal_name="TestCriminal",
        criminal_faction="Terran",
        route=["Alpha", "Beta", "Gamma"],
        answer="Beta",
        reward=12_000,
        reward_per_sys=3_000,
        checked={"Alpha": -1, "Beta": -1, "Gamma": -1},
        issue_time=now,
        end_time=now + timedelta(hours=8),
        tech_level=2,
        criminal_ship={"ship_name": "Hawk", "ship_armour": 200, "weapons": [], "turrets": []},
        status=status,
    )
    db.add(bounty)
    await db.commit()
    await db.refresh(bounty)
    return bounty


async def _seed_discord_message(
    db: AsyncSession,
    guild_id: int,
    bounty_id: int,
    *,
    channel_id: int = CHANNEL_ID,
    message_id: int = MESSAGE_ID,
) -> DiscordMessage:
    """Persist a DiscordMessage record for a bounty announcement."""
    msg = DiscordMessage(
        id=uuid.uuid4(),
        guild_id=guild_id,
        channel_id=channel_id,
        message_id=message_id,
        message_type="bounty_announcement",
        embed_payload="{}",
        reference_id=bounty_id,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return msg


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

        No DB or HTTP calls required.
        """
        result = await execute_bounty_expire_job("test-job", {})
        assert result["status"] == "error", f"Expected status=error, got {result!r}"
        assert "missing" in result.get("reason", "").lower(), f"Expected 'missing' in reason, got {result!r}"
        assert result["bounty_id"] is None

    async def test_bounty_id_none_returns_error(self):
        """Explicit None bounty_id → status=error."""
        result = await execute_bounty_expire_job("test-job", {"bounty_id": None})
        assert result["status"] == "error"


# ===========================================================================
# TIER B — SQLite integration (1 patch only: db_manager bridge)
# ===========================================================================


class TestBountyNotFound:
    """Behaviour #2: bounty not found → status=skipped."""

    async def test_nonexistent_bounty_id_returns_skipped(self, sqlite_engine_and_factory):
        """When no Bounty row exists for the given ID, executor returns status=skipped.

        # 1 mock — db_manager bridge (Tier B)
        """
        _engine, factory = sqlite_engine_and_factory
        payload = {"bounty_id": 999_999}

        with patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)):
            result = await execute_bounty_expire_job("test-job", payload)

        assert result["status"] == "skipped", f"Expected status=skipped, got {result!r}"
        assert result["bounty_id"] == 999_999


class TestActiveBountyExpired:
    """Behaviour #3: active bounty is expired (status=expired in DB)."""

    async def test_active_bounty_status_set_to_expired(self, sqlite_engine_and_factory):
        """Executor calls expire_bounty; DB row status changes from 'active' to 'expired'.

        Cross-session reload verifies the status was persisted.

        # 1 mock — db_manager bridge (Tier B)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            bounty = await _seed_active_bounty(seed_db, GUILD_ID)
        bounty_id = bounty.id

        payload = {"bounty_id": bounty_id}

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_called=False),  # Allow non-fatal HTTP (announcement delete) to no-op.
        ):
            result = await execute_bounty_expire_job("test-job", payload)

        assert result["status"] == "success", f"Expected status=success, got {result!r}"
        assert result["bounty_id"] == bounty_id

        # Cross-session reload: verify the DB row was updated.
        async with factory() as verify_db:
            row = await verify_db.execute(select(Bounty).where(Bounty.id == bounty_id))
            refreshed = row.scalars().first()

        assert refreshed is not None
        assert refreshed.status == "expired", (
            f"Expected Bounty.status='expired' after expire job, got {refreshed.status!r}"
        )

    async def test_return_value_contains_bounty_id(self, sqlite_engine_and_factory):
        """Executor result dict includes the correct bounty_id.

        # 1 mock — db_manager bridge (Tier B)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            bounty = await _seed_active_bounty(seed_db, GUILD_ID)
        bounty_id = bounty.id

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_called=False),
        ):
            result = await execute_bounty_expire_job("test-job", {"bounty_id": bounty_id})

        assert result["bounty_id"] == bounty_id, f"Expected bounty_id={bounty_id} in result, got {result!r}"


class TestAlreadyCapturedBounty:
    """Behaviour #4: already-captured bounty returns status=success (expire returns None)."""

    async def test_captured_bounty_returns_success(self, sqlite_engine_and_factory):
        """A bounty with status='captured' causes expire_bounty to return None.

        The executor still returns status=success (bounty_obj is not None,
        only the expire call returns None).

        # 1 mock — db_manager bridge (Tier B)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            bounty = await _seed_active_bounty(seed_db, GUILD_ID, status="captured")
        bounty_id = bounty.id

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_called=False),
        ):
            result = await execute_bounty_expire_job("test-job", {"bounty_id": bounty_id})

        # Status is 'success' — bounty_obj existed, expire_bounty returned None (already done).
        assert result["status"] == "success", f"Expected status=success for already-captured bounty, got {result!r}"
        assert result["bounty_id"] == bounty_id


class TestNoDiscordMessageGraceful:
    """Behaviour #7: no DiscordMessage record → gracefully skipped."""

    async def test_no_discord_message_does_not_raise(self, sqlite_engine_and_factory):
        """Expiring a bounty with no matching DiscordMessage is non-fatal.

        # 1 mock — db_manager bridge (Tier B)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            bounty = await _seed_active_bounty(seed_db, GUILD_ID)
        bounty_id = bounty.id
        # No DiscordMessage seeded — the DELETE attempt should be skipped gracefully.

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_called=False, assert_all_mocked=True),
        ):
            # No routes registered — any unexpected HTTP call fails loudly.
            result = await execute_bounty_expire_job("test-job", {"bounty_id": bounty_id})

        assert result["status"] == "success"


# ===========================================================================
# TIER B + C — SQLite integration + respx HTTP assertions
# ===========================================================================


class TestDiscordMessageDeleted:
    """Behaviour #5: Discord announcement deleted via gateway."""

    async def test_delete_called_for_existing_announcement(self, sqlite_engine_and_factory):
        """When a DiscordMessage row exists, executor DELETEs it from gateway.

        # 1 mock — db_manager bridge (Tier B + C)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            bounty = await _seed_active_bounty(seed_db, GUILD_ID)
            await _seed_discord_message(seed_db, GUILD_ID, bounty.id)
        bounty_id = bounty.id

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_called=False) as router,
        ):
            delete_route = router.delete(GATEWAY_DELETE_URL).respond(204)
            result = await execute_bounty_expire_job("test-job", {"bounty_id": bounty_id})

        assert result["status"] == "success"
        assert delete_route.called, f"Expected DELETE to {GATEWAY_DELETE_URL}, but it was not called"

        # Cross-session reload: DiscordMessage row should be deleted from DB.
        async with factory() as verify_db:
            rows = await verify_db.execute(select(DiscordMessage).where(DiscordMessage.reference_id == bounty_id))
            remaining = list(rows.scalars().all())

        assert len(remaining) == 0, (
            f"Expected DiscordMessage row to be deleted after expire, got {len(remaining)} remaining"
        )


class TestGatewayDeleteFailureNonFatal:
    """Behaviour #6: gateway DELETE failure is non-fatal."""

    async def test_500_from_gateway_does_not_abort_expiry(self, sqlite_engine_and_factory):
        """HTTP 500 on the Discord message DELETE → bounty still expired in DB.

        # 1 mock — db_manager bridge (Tier B + C)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            bounty = await _seed_active_bounty(seed_db, GUILD_ID)
            await _seed_discord_message(seed_db, GUILD_ID, bounty.id)
        bounty_id = bounty.id

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_called=False) as router,
        ):
            # Simulate 500 from gateway DELETE.
            router.delete(GATEWAY_DELETE_URL).respond(500)
            result = await execute_bounty_expire_job("test-job", {"bounty_id": bounty_id})

        # Job should still return success despite the HTTP failure.
        assert result["status"] == "success", f"Expected status=success even when gateway DELETE fails, got {result!r}"

        # Cross-session reload: bounty status must be 'expired' despite HTTP failure.
        async with factory() as verify_db:
            row = await verify_db.execute(select(Bounty).where(Bounty.id == bounty_id))
            refreshed = row.scalars().first()

        assert refreshed is not None
        assert refreshed.status == "expired", (
            f"Expected Bounty.status='expired' even after gateway failure, got {refreshed.status!r}"
        )

    async def test_404_from_gateway_is_acceptable(self, sqlite_engine_and_factory):
        """HTTP 404 on the Discord message DELETE is treated as OK (already deleted).

        # 1 mock — db_manager bridge (Tier B + C)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            bounty = await _seed_active_bounty(seed_db, GUILD_ID)
            await _seed_discord_message(seed_db, GUILD_ID, bounty.id)
        bounty_id = bounty.id

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_called=False) as router,
        ):
            # 404 is listed as an acceptable response in the executor (message already gone).
            router.delete(GATEWAY_DELETE_URL).respond(404)
            result = await execute_bounty_expire_job("test-job", {"bounty_id": bounty_id})

        assert result["status"] == "success", (
            f"Expected status=success for 404 (message already deleted), got {result!r}"
        )
