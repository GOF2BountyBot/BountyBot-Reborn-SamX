"""Bounty failsafe cleanup executor tests — real SQLite + respx, ≤2 mocks.

Follows the S2 three-tier executor test pattern documented in tests/AGENTS.md.

BEHAVIOURS COVERED
------------------
| # | Behaviour | Tier |
|---|-----------|------|
| 1 | No guild configs → returns success, 0 cleaned | B |
| 2 | Guild without bounty channel configured → channel skipped gracefully | B |
| 3 | Message not in discord_message table → classified as skip (not our post) | B + C |
| 4 | Expired bounty post → Discord post deleted, DB record removed | B + C |
| 5 | Captured bounty post → Discord post deleted, DB record removed | B + C |
| 6 | Active bounty with future end_time → classified as live, left alone | B + C |
| 7 | Active bounty with past end_time → stale, status set to expired, post deleted | B + C |
| 8 | Bounty row missing (orphan announcement) → post deleted | B + C |
| 9 | gateway channel fetch failure → error counted, sweep continues for other divs | B + C |
| 10 | Discord DELETE 404 is treated as success (message already deleted) | B + C |
| 11 | Discord DELETE 500 is non-fatal; DB record still cleaned | B + C |
| 12 | guild_id payload filter restricts sweep to one guild | B |

SQLITE NOTES
------------
Bounty, GuildConfig, and DiscordMessage are all SQLite-compatible (JSON/CHAR columns only).
No ARRAY(String) columns — all three tables can be created on SQLite in-memory.
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
# Path setup and stub registration (mirrors other executor test files)
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
from persist.models.guild_config import GuildConfig
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from utils.executors.bounty_failsafe_cleanup_executor import execute_bounty_failsafe_cleanup_job

# ---------------------------------------------------------------------------
# SQLite tables for this test module
# ---------------------------------------------------------------------------

_SQLITE_TABLES = [
    GuildConfig.__table__,
    Bounty.__table__,
    DiscordMessage.__table__,
]

# ---------------------------------------------------------------------------
# Common constants
# ---------------------------------------------------------------------------

GUILD_ID = 9_600_000_001
BRONZE_CHANNEL_ID = 77_001
SILVER_CHANNEL_ID = 77_002
GOLD_CHANNEL_ID = 77_003
PLATINUM_CHANNEL_ID = 77_004

MESSAGE_ID_1 = 55_001
MESSAGE_ID_2 = 55_002

_GW_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
_GW_PORT = os.getenv("GATEWAY_PORT", "7999")
_GATEWAY_BASE = f"http://{_GW_HOST}:{_GW_PORT}/api/v1"

# URL patterns used by respx
_MESSAGES_URL = f"{_GATEWAY_BASE}/channels/{BRONZE_CHANNEL_ID}/messages"
_DELETE_URL = f"{_GATEWAY_BASE}/channels/{BRONZE_CHANNEL_ID}/messages/{MESSAGE_ID_1}"


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
async def sqlite_engine_and_factory():
    """Fresh SQLite in-memory engine + session factory per test."""
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


async def _seed_guild_config(
    db: AsyncSession,
    guild_id: int = GUILD_ID,
    *,
    bronze_channel_id: int | None = BRONZE_CHANNEL_ID,
    silver_channel_id: int | None = None,
    gold_channel_id: int | None = None,
    platinum_channel_id: int | None = None,
    bounty_hunter_role_id: int | None = 12345,
) -> GuildConfig:
    """Persist a minimal GuildConfig with configurable bounty channels."""
    config = GuildConfig(
        guild_id=guild_id,
        bronze_bounty_channel_id=bronze_channel_id,
        silver_bounty_channel_id=silver_channel_id,
        gold_bounty_channel_id=gold_channel_id,
        platinum_bounty_channel_id=platinum_channel_id,
        bounty_hunter_role_id=bounty_hunter_role_id,
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return config


async def _seed_bounty(
    db: AsyncSession,
    guild_id: int = GUILD_ID,
    *,
    status: str = "active",
    end_time_offset_hours: float = 8.0,
) -> Bounty:
    """Persist a Bounty row. end_time_offset_hours can be negative for past end_time."""
    now = datetime.now(UTC)
    bounty = Bounty(
        guild_id=guild_id,
        division="bronze",
        criminal_name="TestCriminal",
        criminal_faction="Terran",
        route=["Alpha", "Beta"],
        answer="Beta",
        reward=10_000,
        reward_per_sys=5_000,
        checked={"Alpha": -1, "Beta": -1},
        issue_time=now,
        end_time=now + timedelta(hours=end_time_offset_hours),
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
    guild_id: int = GUILD_ID,
    bounty_id: int | None = None,
    *,
    channel_id: int = BRONZE_CHANNEL_ID,
    message_id: int = MESSAGE_ID_1,
    message_type: str = "bounty_announcement",
) -> DiscordMessage:
    """Persist a DiscordMessage record."""
    msg = DiscordMessage(
        id=uuid.uuid4(),
        guild_id=guild_id,
        channel_id=channel_id,
        message_id=message_id,
        message_type=message_type,
        embed_payload="{}",
        reference_id=bounty_id,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return msg


def _make_fake_db_manager(factory: Any):
    """Bridge db_manager.get_session() → real SQLite session.

    # 1 mock — db_manager bridge (Tier B)
    """

    @asynccontextmanager
    async def _fake_get_db():
        async with factory() as session:
            yield session

    fake = MagicMock()
    fake.get_session = MagicMock(side_effect=_fake_get_db)
    return fake


def _channel_messages_payload(message_ids: list[int]) -> dict:
    """Build a minimal gateway /channels/{id}/messages JSON response."""
    return {
        "status": "success",
        "data": [{"id": str(mid)} for mid in message_ids],
    }


# ===========================================================================
# TIER B — No guild configs → early exit
# ===========================================================================


class TestNoGuildConfigs:
    """Behaviour #1: no guild configs → returns success with 0 cleaned."""

    async def test_no_guild_configs_returns_success(self, sqlite_engine_and_factory):
        """Empty guild_configs table → sweep exits early without any HTTP calls.

        # 1 mock — db_manager bridge (Tier B)
        """
        _engine, factory = sqlite_engine_and_factory

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_mocked=True),  # No HTTP calls allowed.
        ):
            result = await execute_bounty_failsafe_cleanup_job("test-job", {})

        assert result["status"] == "success"
        assert result["guilds_processed"] == 0
        assert result["total_cleaned"] == 0


# ===========================================================================
# TIER B — Guild with no channel configured → channel skipped
# ===========================================================================


class TestNoBountyChannelConfigured:
    """Behaviour #2: guild without a bounty channel → channel skipped."""

    async def test_guild_with_no_channels_skips_all_divisions(self, sqlite_engine_and_factory):
        """All division channel IDs are None → no gateway calls, 0 inspected.

        # 1 mock — db_manager bridge (Tier B)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            await _seed_guild_config(
                seed_db,
                bronze_channel_id=None,
                silver_channel_id=None,
                gold_channel_id=None,
                platinum_channel_id=None,
            )

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_mocked=True),  # No HTTP calls allowed.
        ):
            result = await execute_bounty_failsafe_cleanup_job("test-job", {})

        assert result["status"] == "success"
        assert result["total_messages_inspected"] == 0
        assert result["total_cleaned"] == 0


# ===========================================================================
# TIER B + C — Message not in DB → classified as skip
# ===========================================================================


class TestMessageNotInDatabase:
    """Behaviour #3: Discord message has no matching discord_message record → skip."""

    async def test_unknown_message_is_skipped(self, sqlite_engine_and_factory):
        """Gateway returns a message ID we don't recognise → no deletion, no error.

        # 1 mock — db_manager bridge (Tier B + C)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            await _seed_guild_config(seed_db)
            # No DiscordMessage seeded — this message is foreign to us.

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_called=False, assert_all_mocked=False) as router,
        ):
            # Gateway returns one message we don't know about.
            router.get(_MESSAGES_URL).respond(200, json=_channel_messages_payload([MESSAGE_ID_1]))
            # Ensure DELETE is never called.
            delete_route = router.delete(_DELETE_URL).respond(204)

            result = await execute_bounty_failsafe_cleanup_job("test-job", {})

        assert result["status"] == "success"
        assert result["total_cleaned"] == 0
        assert not delete_route.called, "DELETE should NOT be called for an unrecognised message"


# ===========================================================================
# TIER B + C — Expired bounty post → cleaned
# ===========================================================================


class TestExpiredBountyPost:
    """Behaviour #4: expired bounty post → Discord post deleted, DB record removed."""

    async def test_expired_bounty_post_is_cleaned(self, sqlite_engine_and_factory):
        """Bounty with status='expired' → Discord post deleted + DiscordMessage row removed.

        Cross-session reload confirms DiscordMessage is gone.

        # 1 mock — db_manager bridge (Tier B + C)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            await _seed_guild_config(seed_db)
            bounty = await _seed_bounty(seed_db, status="expired")
            await _seed_discord_message(seed_db, bounty_id=bounty.id)
        bounty_id = bounty.id

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_called=False) as router,
        ):
            router.get(_MESSAGES_URL).respond(200, json=_channel_messages_payload([MESSAGE_ID_1]))
            delete_route = router.delete(_DELETE_URL).respond(204)

            result = await execute_bounty_failsafe_cleanup_job("test-job", {})

        assert result["status"] == "success"
        assert result["total_cleaned"] == 1
        assert delete_route.called, "Expected DELETE call for expired bounty post"

        # Cross-session reload: DiscordMessage row should be gone.
        async with factory() as verify_db:
            rows = await verify_db.execute(
                select(DiscordMessage).where(DiscordMessage.reference_id == bounty_id)
            )
            remaining = list(rows.scalars().all())

        assert len(remaining) == 0, (
            f"Expected DiscordMessage to be deleted after expired bounty cleanup, got {len(remaining)}"
        )


# ===========================================================================
# TIER B + C — Captured bounty post → cleaned
# ===========================================================================


class TestCapturedBountyPost:
    """Behaviour #5: captured bounty post → Discord post deleted, DB record removed."""

    async def test_captured_bounty_post_is_cleaned(self, sqlite_engine_and_factory):
        """Bounty with status='captured' → Discord post deleted + DiscordMessage row removed.

        # 1 mock — db_manager bridge (Tier B + C)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            await _seed_guild_config(seed_db)
            bounty = await _seed_bounty(seed_db, status="captured")
            await _seed_discord_message(seed_db, bounty_id=bounty.id)
        bounty_id = bounty.id

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_called=False) as router,
        ):
            router.get(_MESSAGES_URL).respond(200, json=_channel_messages_payload([MESSAGE_ID_1]))
            delete_route = router.delete(_DELETE_URL).respond(204)

            result = await execute_bounty_failsafe_cleanup_job("test-job", {})

        assert result["status"] == "success"
        assert result["total_cleaned"] == 1
        assert delete_route.called

        # Cross-session reload: DiscordMessage row should be gone.
        async with factory() as verify_db:
            rows = await verify_db.execute(
                select(DiscordMessage).where(DiscordMessage.reference_id == bounty_id)
            )
            remaining = list(rows.scalars().all())
        assert len(remaining) == 0


# ===========================================================================
# TIER B + C — Live active bounty → left alone
# ===========================================================================


class TestLiveActiveBounty:
    """Behaviour #6: active bounty with future end_time → left alone."""

    async def test_live_bounty_not_cleaned(self, sqlite_engine_and_factory):
        """Active bounty with end_time in the future → no deletion whatsoever.

        # 1 mock — db_manager bridge (Tier B + C)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            await _seed_guild_config(seed_db)
            # end_time 8 hours in the future → genuinely live
            bounty = await _seed_bounty(seed_db, status="active", end_time_offset_hours=8.0)
            await _seed_discord_message(seed_db, bounty_id=bounty.id)
        bounty_id = bounty.id

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_called=False, assert_all_mocked=False) as router,
        ):
            router.get(_MESSAGES_URL).respond(200, json=_channel_messages_payload([MESSAGE_ID_1]))
            delete_route = router.delete(_DELETE_URL).respond(204)

            result = await execute_bounty_failsafe_cleanup_job("test-job", {})

        assert result["status"] == "success"
        assert result["total_cleaned"] == 0, "Live bounty should NOT be cleaned"
        assert not delete_route.called, "DELETE should NOT be called for a live bounty"

        # Cross-session reload: DiscordMessage row still present.
        async with factory() as verify_db:
            rows = await verify_db.execute(
                select(DiscordMessage).where(DiscordMessage.reference_id == bounty_id)
            )
            remaining = list(rows.scalars().all())
        assert len(remaining) == 1, "DiscordMessage should still exist for live bounty"


# ===========================================================================
# TIER B + C — Stale active bounty (past end_time) → expired + cleaned
# ===========================================================================


class TestStaleActiveBounty:
    """Behaviour #7: active bounty with past end_time → set expired, post deleted."""

    async def test_stale_active_bounty_is_expired_and_cleaned(self, sqlite_engine_and_factory):
        """Active bounty with end_time in the past → status set to 'expired', post deleted.

        Cross-session reload confirms Bounty.status == 'expired' and DiscordMessage gone.

        # 1 mock — db_manager bridge (Tier B + C)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            await _seed_guild_config(seed_db)
            # end_time 2 hours in the PAST → stale active
            bounty = await _seed_bounty(seed_db, status="active", end_time_offset_hours=-2.0)
            await _seed_discord_message(seed_db, bounty_id=bounty.id)
        bounty_id = bounty.id

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_called=False) as router,
        ):
            router.get(_MESSAGES_URL).respond(200, json=_channel_messages_payload([MESSAGE_ID_1]))
            delete_route = router.delete(_DELETE_URL).respond(204)

            result = await execute_bounty_failsafe_cleanup_job("test-job", {})

        assert result["status"] == "success"
        assert result["total_cleaned"] == 1
        assert delete_route.called

        # Cross-session reload: Bounty status should now be 'expired'.
        async with factory() as verify_db:
            row = await verify_db.execute(select(Bounty).where(Bounty.id == bounty_id))
            refreshed = row.scalars().first()
        assert refreshed is not None
        assert refreshed.status == "expired", (
            f"Expected Bounty.status='expired' for stale active bounty, got {refreshed.status!r}"
        )

        # DiscordMessage should also be removed.
        async with factory() as verify_db2:
            msg_rows = await verify_db2.execute(
                select(DiscordMessage).where(DiscordMessage.reference_id == bounty_id)
            )
            remaining = list(msg_rows.scalars().all())
        assert len(remaining) == 0, "DiscordMessage should be deleted for stale active bounty"


# ===========================================================================
# TIER B + C — Bounty row missing (orphan) → post deleted
# ===========================================================================


class TestOrphanAnnouncement:
    """Behaviour #8: bounty row deleted but discord_message still exists → post deleted."""

    async def test_orphan_announcement_is_cleaned(self, sqlite_engine_and_factory):
        """DiscordMessage record points to a bounty_id that no longer exists in DB.

        Expected: Discord post deleted, DiscordMessage record removed.

        # 1 mock — db_manager bridge (Tier B + C)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            await _seed_guild_config(seed_db)
            # Seed a DiscordMessage pointing to a non-existent bounty_id
            await _seed_discord_message(seed_db, bounty_id=99_999)

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_called=False) as router,
        ):
            router.get(_MESSAGES_URL).respond(200, json=_channel_messages_payload([MESSAGE_ID_1]))
            delete_route = router.delete(_DELETE_URL).respond(204)

            result = await execute_bounty_failsafe_cleanup_job("test-job", {})

        assert result["status"] == "success"
        assert result["total_cleaned"] == 1
        assert delete_route.called, "DELETE should be called for orphan announcement"

        # Cross-session reload: DiscordMessage should be gone.
        async with factory() as verify_db:
            msg_rows = await verify_db.execute(select(DiscordMessage))
            remaining = list(msg_rows.scalars().all())
        assert len(remaining) == 0


# ===========================================================================
# TIER B + C — Gateway channel fetch failure → error counted, sweep continues
# ===========================================================================


class TestChannelFetchFailure:
    """Behaviour #9: gateway GET /channels/{id}/messages fails → error counted, not fatal."""

    async def test_channel_fetch_error_is_non_fatal(self, sqlite_engine_and_factory):
        """HTTP 500 on channel message fetch → error counter incremented, sweep returns success.

        # 1 mock — db_manager bridge (Tier B + C)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            await _seed_guild_config(seed_db)

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_called=False) as router,
        ):
            # Simulate gateway failure for the bronze channel.
            router.get(_MESSAGES_URL).respond(500)

            result = await execute_bounty_failsafe_cleanup_job("test-job", {})

        assert result["status"] == "success"
        assert result["total_errors"] >= 1
        assert result["total_cleaned"] == 0


# ===========================================================================
# TIER B + C — Discord DELETE 404 treated as success
# ===========================================================================


class TestGatewayDelete404:
    """Behaviour #10: Discord DELETE returns 404 → treated as success (already gone)."""

    async def test_delete_404_is_non_fatal(self, sqlite_engine_and_factory):
        """A 404 from the gateway DELETE is acceptable — message may have been manually deleted.

        # 1 mock — db_manager bridge (Tier B + C)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            await _seed_guild_config(seed_db)
            bounty = await _seed_bounty(seed_db, status="expired")
            await _seed_discord_message(seed_db, bounty_id=bounty.id)

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_called=False) as router,
        ):
            router.get(_MESSAGES_URL).respond(200, json=_channel_messages_payload([MESSAGE_ID_1]))
            router.delete(_DELETE_URL).respond(404)

            result = await execute_bounty_failsafe_cleanup_job("test-job", {})

        assert result["status"] == "success"
        assert result["total_cleaned"] == 1


# ===========================================================================
# TIER B + C — Discord DELETE 500 is non-fatal; DB record still cleaned
# ===========================================================================


class TestGatewayDelete500:
    """Behaviour #11: Discord DELETE returns 500 → non-fatal; DB record still removed."""

    async def test_delete_500_does_not_prevent_db_cleanup(self, sqlite_engine_and_factory):
        """HTTP 500 on Discord DELETE → warning logged, DiscordMessage DB record still deleted.

        # 1 mock — db_manager bridge (Tier B + C)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            await _seed_guild_config(seed_db)
            bounty = await _seed_bounty(seed_db, status="expired")
            await _seed_discord_message(seed_db, bounty_id=bounty.id)
        bounty_id = bounty.id

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_called=False) as router,
        ):
            router.get(_MESSAGES_URL).respond(200, json=_channel_messages_payload([MESSAGE_ID_1]))
            router.delete(_DELETE_URL).respond(500)

            result = await execute_bounty_failsafe_cleanup_job("test-job", {})

        # Job-level result should still count this as cleaned (best-effort).
        assert result["status"] == "success"

        # DiscordMessage should still be removed from DB despite Discord failure.
        async with factory() as verify_db:
            rows = await verify_db.execute(
                select(DiscordMessage).where(DiscordMessage.reference_id == bounty_id)
            )
            remaining = list(rows.scalars().all())
        assert len(remaining) == 0, (
            "DiscordMessage record should be deleted even when Discord DELETE returns 500"
        )


# ===========================================================================
# TIER B — guild_id payload filter restricts sweep
# ===========================================================================


class TestGuildIdFilter:
    """Behaviour #12: guild_id in payload restricts sweep to that guild only."""

    async def test_guild_id_filter_skips_other_guilds(self, sqlite_engine_and_factory):
        """Two guilds configured; sweep with guild_id=GUILD_A only processes GUILD_A.

        No HTTP calls expected (channels return empty, or not called at all for GUILD_B).

        # 1 mock — db_manager bridge (Tier B)
        """
        GUILD_A = GUILD_ID
        GUILD_B = GUILD_ID + 1
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            await _seed_guild_config(seed_db, GUILD_A)
            await _seed_guild_config(seed_db, GUILD_B)

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_called=False, assert_all_mocked=False) as router,
        ):
            # Only GUILD_A's bronze channel should be queried.
            router.get(_MESSAGES_URL).respond(200, json=_channel_messages_payload([]))

            result = await execute_bounty_failsafe_cleanup_job(
                "test-job", {"guild_id": GUILD_A}
            )

        # Only 1 guild processed.
        assert result["guilds_processed"] == 1
        assert GUILD_A in result["results"]
        assert GUILD_B not in result["results"]
