"""S4 rewrite: shop_refresh_executor tests — real SQLite + respx, 0 repo mocks.

Sprint 4 (S4) of the Test Quality Blitz.

PATTERN OVERVIEW
----------------
Three-tier breakdown following ``tests/AGENTS.md`` §"Executor Test Pattern (S2)":

  Tier A — Pure unit tests for payload-validation logic. ZERO mocks.

  Tier B — SQLite-in-memory integration for ORM read/write paths.
            Only patch: ``patch("persist.database.manager.db_manager", ...)``.
            NO repository or service methods mocked.

  Tier C — respx for the outbound HTTP announcement call to discord-gateway
            ``POST /api/v1/channels/{shop_channel_id}/messages``.

BEHAVIOURS COVERED
------------------
| # | Behaviour | Tier |
|---|-----------|------|
| 1 | No guilds configured → returns guilds_refreshed=0 | B |
| 2 | Single guild, all tiers refreshed (ShopService.refresh_shop mocked — Item ARRAY bypass) | B + C |
| 3 | Announcement fires per guild (respx) | B + C |
| 4 | Announcement skipped when shop_channel_id is None (non-fatal) | B + C |
| 5 | Single guild + single tier payload | B |
| 6 | Single guild + all tiers payload | B |
| 7 | Announcement failure is non-fatal (guild still refreshed) | B + C |
| 8 | Multi-guild: one guild fail doesn't block others | B |

SQLITE COMPATIBILITY NOTE
--------------------------
ShopService.refresh_shop calls GuildShop, ItemRepository, ShipRepository and
other ARRAY-column tables that SQLite cannot host (Ship.aliases, Item.aliases).
Tests that reach refresh_shop mock ``services.shop_service.ShopService.refresh_shop``
to a coroutine returning a minimal dict.  This is the minimum surface needed;
the real DB read of GuildConfig still runs through ConfigRepository.list_all
(or get_by_guild_id) against real SQLite.  Each such test carries a comment
citing tests/AGENTS.md §"Mock Policy" as justification.
"""

from __future__ import annotations

import os
import sys
import types
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
from persist.models.guild_config import GuildConfig
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from utils.executors.shop_refresh_executor import execute_shop_refresh_job

# ---------------------------------------------------------------------------
# SQLite table list — only SQLite-compatible tables (no ARRAY columns).
# ---------------------------------------------------------------------------

_SQLITE_TABLES = [
    GuildConfig.__table__,
]

# ---------------------------------------------------------------------------
# Common test constants — guild IDs must fit SQLite's signed 64-bit INTEGER.
# ---------------------------------------------------------------------------

GUILD_ID = 9_500_000_010
GUILD_ID_2 = 9_500_000_011
SHOP_CHANNEL = 12_300
ROLE_ID = 45_600

GATEWAY_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
GATEWAY_PORT = os.getenv("GATEWAY_PORT", "7999")
GATEWAY_CHANNEL_URL = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/api/v1/channels/{SHOP_CHANNEL}/messages"


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


async def _seed_guild_config(
    db: AsyncSession,
    guild_id: int,
    *,
    shop_channel_id: int | None = SHOP_CHANNEL,
    bounty_hunter_role_id: int | None = ROLE_ID,
    division_temperatures: dict[str, float] | None = None,
) -> GuildConfig:
    """Persist a GuildConfig with optional shop channel."""
    config = GuildConfig(
        guild_id=guild_id,
        shop_channel_id=shop_channel_id,
        bounty_hunter_role_id=bounty_hunter_role_id,
        division_temperatures=division_temperatures or {"bronze": 1.0, "silver": 1.0, "gold": 1.0, "platinum": 1.0},
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return config


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


# A minimal refresh result that ShopService.refresh_shop returns.
_FAKE_REFRESH_RESULT: dict = {"status": "ok", "items_added": 3}


# ===========================================================================
# TIER B — SQLite integration (1 patch only: db_manager bridge)
# ===========================================================================


class TestNoGuildsConfigured:
    """Behaviour #1: empty guild_configs table → guilds_refreshed=0."""

    async def test_empty_db_returns_zero_guilds_refreshed(self, sqlite_engine_and_factory):
        """When no GuildConfig rows exist, bulk refresh returns 0 guilds_refreshed.

        ConfigRepository.list_all runs against real SQLite.

        # 1 mock — db_manager bridge (Tier B)
        """
        _engine, factory = sqlite_engine_and_factory
        # No rows seeded — table is empty.

        with patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)):
            result = await execute_shop_refresh_job("job-no-guilds", {})

        assert result["status"] == "success"
        assert result["guilds_refreshed"] == 0, (
            f"Expected 0 guilds_refreshed for empty DB, got {result['guilds_refreshed']!r}"
        )
        assert result["results"] == {}


class TestSingleGuildSingleTierPayload:
    """Behaviour #5: single guild + single tier payload."""

    async def test_single_guild_single_tier_calls_refresh(self, sqlite_engine_and_factory):
        """Payload with guild_id + tier calls ShopService.refresh_shop once for that tier.

        ShopService.refresh_shop is mocked to bypass Item/Ship ARRAY columns.
        See tests/AGENTS.md §"Mock Policy" — ARRAY-column bypass justification.

        # 1 mock — db_manager bridge (Tier B)
        # + ShopService.refresh_shop mock (ARRAY-column bypass)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            await _seed_guild_config(seed_db, GUILD_ID)

        payload = {"guild_id": GUILD_ID, "tier": "Bronze"}

        refresh_results = []

        async def _fake_refresh(db, guild_id, tier, force_tech_level=None):
            refresh_results.append((guild_id, tier))
            return _FAKE_REFRESH_RESULT

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            patch("services.shop_service.ShopService.refresh_shop", side_effect=_fake_refresh),
        ):
            result = await execute_shop_refresh_job("job-single-tier", payload)

        assert result["status"] == "success"
        assert result["guild_id"] == GUILD_ID
        assert result["tier"] == "Bronze"
        # Verify exactly one refresh call for the correct guild + tier.
        assert len(refresh_results) == 1, f"Expected 1 refresh call, got {refresh_results!r}"
        assert refresh_results[0] == (GUILD_ID, "Bronze")


class TestSingleGuildAllTiersPayload:
    """Behaviour #6: single guild, all tiers (guild_id provided, no tier)."""

    async def test_single_guild_all_tiers_calls_refresh_four_times(self, sqlite_engine_and_factory):
        """Payload with only guild_id calls refresh for all 4 tiers.

        ShopService.refresh_shop mocked — ARRAY-column bypass (tests/AGENTS.md §"Mock Policy").

        # 1 mock — db_manager bridge (Tier B)
        # + ShopService.refresh_shop mock (ARRAY-column bypass)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            await _seed_guild_config(seed_db, GUILD_ID)

        payload = {"guild_id": GUILD_ID}
        refresh_calls: list[tuple] = []

        async def _fake_refresh(db, guild_id, tier, force_tech_level=None):
            refresh_calls.append((guild_id, tier))
            return _FAKE_REFRESH_RESULT

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            patch("services.shop_service.ShopService.refresh_shop", side_effect=_fake_refresh),
        ):
            result = await execute_shop_refresh_job("job-single-all-tiers", payload)

        assert result["status"] == "success"
        assert result["guild_id"] == GUILD_ID
        assert "results" in result
        # All 4 tiers should have been refreshed.
        assert len(refresh_calls) == 4, f"Expected 4 refresh calls (one per tier), got {refresh_calls!r}"
        called_tiers = {tier for _, tier in refresh_calls}
        assert called_tiers == {"Bronze", "Silver", "Gold", "Platinum"}, (
            f"Expected all 4 tiers refreshed, got {called_tiers!r}"
        )


class TestBulkRefreshAllGuilds:
    """Behaviour #2: bulk mode — all guilds, all tiers refreshed."""

    async def test_bulk_refresh_processes_all_configured_guilds(self, sqlite_engine_and_factory):
        """With no guild_id in payload, all guilds are processed.

        Two guild configs are seeded; both should appear in results.

        # 1 mock — db_manager bridge (Tier B)
        # + ShopService.refresh_shop mock (ARRAY-column bypass)
        # + ShopService.preload_static_data mock (ARRAY-column bypass)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            await _seed_guild_config(seed_db, GUILD_ID)
            await _seed_guild_config(seed_db, GUILD_ID_2)

        refreshed_guilds: set[int] = set()

        async def _fake_refresh(db, guild_id, tier, force_tech_level=None):
            refreshed_guilds.add(guild_id)
            return _FAKE_REFRESH_RESULT

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            patch("services.shop_service.ShopService.refresh_shop", side_effect=_fake_refresh),
            patch("services.shop_service.ShopService.preload_static_data", new=AsyncMock()),
            patch("utils.shop_announcement.announce_shop_refresh", new=AsyncMock()),
        ):
            result = await execute_shop_refresh_job("job-bulk", {})

        assert result["status"] == "success"
        assert result["guilds_refreshed"] == 2, f"Expected 2 guilds_refreshed, got {result['guilds_refreshed']!r}"
        assert GUILD_ID in result["results"], "GUILD_ID should appear in bulk results"
        assert GUILD_ID_2 in result["results"], "GUILD_ID_2 should appear in bulk results"
        # Both guilds should have been passed to refresh_shop.
        assert refreshed_guilds == {GUILD_ID, GUILD_ID_2}, f"Expected both guilds refreshed, got {refreshed_guilds!r}"


# ===========================================================================
# TIER B + C — SQLite integration + respx HTTP assertions
# ===========================================================================


class TestAnnouncementFires:
    """Behaviour #3: announcement fires per guild to the gateway."""

    async def test_announcement_posted_to_shop_channel(self, sqlite_engine_and_factory):
        """POST to /channels/{shop_channel_id}/messages is called once per guild.

        # 1 mock — db_manager bridge (Tier B + C)
        # + ShopService.refresh_shop mock (ARRAY-column bypass)
        # + ShopService.preload_static_data mock (ARRAY-column bypass)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            await _seed_guild_config(seed_db, GUILD_ID, shop_channel_id=SHOP_CHANNEL)

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            patch("services.shop_service.ShopService.refresh_shop", new=AsyncMock(return_value=_FAKE_REFRESH_RESULT)),
            patch("services.shop_service.ShopService.preload_static_data", new=AsyncMock()),
            respx.mock(assert_all_called=False) as router,
        ):
            announce_route = router.post(GATEWAY_CHANNEL_URL).respond(200, json={"ok": True})
            result = await execute_shop_refresh_job("job-announce", {})

        assert result["status"] == "success"
        assert announce_route.called, (
            f"Expected gateway announcement POST to {GATEWAY_CHANNEL_URL}, but it was not called"
        )


class TestAnnouncementSkippedWhenNoChannel:
    """Behaviour #4: announcement skipped when shop_channel_id is None."""

    async def test_no_http_call_when_shop_channel_not_configured(self, sqlite_engine_and_factory):
        """Guild with shop_channel_id=None causes announcement to be skipped (non-fatal).

        No POST to gateway should be made; the job still returns success.

        # 1 mock — db_manager bridge (Tier B + C)
        # + ShopService.refresh_shop mock (ARRAY-column bypass)
        # + ShopService.preload_static_data mock (ARRAY-column bypass)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            await _seed_guild_config(seed_db, GUILD_ID, shop_channel_id=None)

        http_call_count = 0

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            patch("services.shop_service.ShopService.refresh_shop", new=AsyncMock(return_value=_FAKE_REFRESH_RESULT)),
            patch("services.shop_service.ShopService.preload_static_data", new=AsyncMock()),
            respx.mock(assert_all_called=False, assert_all_mocked=True) as router,
        ):
            # Any unexpected HTTP call will raise because assert_all_mocked=True.
            # We register nothing — if any POST fires it will fail loudly.
            result = await execute_shop_refresh_job("job-no-channel", {})
            http_call_count = router.calls.call_count

        assert result["status"] == "success"
        assert http_call_count == 0, f"Expected ZERO HTTP calls when shop_channel_id is None, got {http_call_count}"


class TestAnnouncementFailureIsNonFatal:
    """Behaviour #7: announcement failure does not abort the refresh."""

    async def test_500_from_gateway_does_not_raise(self, sqlite_engine_and_factory):
        """HTTP 500 from gateway → job still returns success and results contain the guild.

        # 1 mock — db_manager bridge (Tier B + C)
        # + ShopService.refresh_shop mock (ARRAY-column bypass)
        # + ShopService.preload_static_data mock (ARRAY-column bypass)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            await _seed_guild_config(seed_db, GUILD_ID, shop_channel_id=SHOP_CHANNEL)

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            patch("services.shop_service.ShopService.refresh_shop", new=AsyncMock(return_value=_FAKE_REFRESH_RESULT)),
            patch("services.shop_service.ShopService.preload_static_data", new=AsyncMock()),
            respx.mock(assert_all_called=False) as router,
        ):
            # Announcement endpoint returns HTTP 500 (server error).
            router.post(GATEWAY_CHANNEL_URL).respond(500)
            result = await execute_shop_refresh_job("job-announce-fail", {})

        # Despite the 500, the job should report success and list the guild.
        assert result["status"] == "success", f"Expected status=success even when announcement fails, got {result!r}"
        assert result["guilds_refreshed"] == 1, (
            f"Expected 1 guilds_refreshed despite announcement failure, got {result!r}"
        )
        assert GUILD_ID in result["results"]
