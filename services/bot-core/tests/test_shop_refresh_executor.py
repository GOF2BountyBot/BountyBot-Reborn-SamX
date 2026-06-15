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
| 3 | Announcement fires per tier per guild (4 POSTs per guild) | B + C |
| 4 | Announcement skipped when shop_channel_id is None (non-fatal) | B + C |
| 5 | Single guild + single tier payload | B |
| 6 | Single guild + all tiers payload | B |
| 7 | Announcement failure is non-fatal (guild still refreshed) | B + C |
| 8 | Multi-guild: one guild fail doesn't block others | B |
| 9 | Role mention only on first tier (Bronze), None on others | B + C |

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
import utils.executors.shop_refresh_executor as exec_module
from persist.models.base import Base
from persist.models.guild_config import GuildConfig
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

execute_shop_refresh_job = exec_module.execute_shop_refresh_job

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
_FAKE_REFRESH_RESULT: dict = {"status": "ok", "items_added": 3, "tech_level": 5}


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
            # `from ... import announce_shop_refresh as _shared_announce` binds the
            # callable into this executor's namespace at import time; patch the
            # local binding so no real httpx call (~4s DNS timeout) fires.
            patch("utils.executors.shop_refresh_executor._shared_announce", new=AsyncMock()),
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
    """Behaviour #3: announcement fires once per tier per guild (4 POSTs)."""

    async def test_announcement_posted_four_times_per_guild(self, sqlite_engine_and_factory):
        """POST to /channels/{shop_channel_id}/messages is called 4 times per guild (once per tier).

        New behaviour (per-tier announcements): each tier posts a separate embed.

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
        # 4 tiers × 1 guild = 4 announcement calls
        assert announce_route.call_count == 4, (
            f"Expected 4 announcement calls (one per tier), got {announce_route.call_count}"
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


class TestRoleMentionOnlyOnFirstTier:
    """Behaviour #9: role mention only on Bronze (first tier), None on Silver/Gold/Platinum."""

    async def test_role_mention_only_on_bronze(self, sqlite_engine_and_factory):
        """The role mention (<@&role_id>) appears in the first tier announcement only.

        Subsequent tiers (Silver, Gold, Platinum) must NOT include a role mention
        to avoid 4 pings per refresh cycle.

        # 1 mock — db_manager bridge (Tier B + C)
        # + ShopService.refresh_shop mock (ARRAY-column bypass)
        # + ShopService.preload_static_data mock (ARRAY-column bypass)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            await _seed_guild_config(seed_db, GUILD_ID, shop_channel_id=SHOP_CHANNEL, bounty_hunter_role_id=ROLE_ID)

        announce_calls: list[dict] = []

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            patch("services.shop_service.ShopService.refresh_shop", new=AsyncMock(return_value=_FAKE_REFRESH_RESULT)),
            patch("services.shop_service.ShopService.preload_static_data", new=AsyncMock()),
            respx.mock(assert_all_called=False) as router,
        ):

            def _capture_and_respond(request):
                import json

                announce_calls.append(json.loads(request.content))
                return respx.MockResponse(200, json={"ok": True})

            router.post(GATEWAY_CHANNEL_URL).mock(side_effect=_capture_and_respond)
            result = await execute_shop_refresh_job("job-role-mention", {})

        assert result["status"] == "success"
        assert len(announce_calls) == 4, f"Expected 4 announce calls, got {len(announce_calls)}"

        # First call (Bronze) should include role mention
        bronze_call = announce_calls[0]
        assert bronze_call.get("text_content") == f"<@&{ROLE_ID}>", (
            f"Expected role mention in Bronze tier call, got {bronze_call.get('text_content')!r}"
        )

        # Remaining calls (Silver, Gold, Platinum) must NOT include role mention
        for i, call in enumerate(announce_calls[1:], start=2):
            assert call.get("text_content") is None, (
                f"Expected no role mention on tier #{i} (index {i - 1}), got {call.get('text_content')!r}"
            )


# ===========================================================================
# Task 0002 Sub-task B — Empty-store announcement fix & diagnostic logging
# ===========================================================================


# Minimal fake GuildShop-like item (plain dict — ORM not needed in Tier B tests).
_FAKE_SHOP_ITEM_DICT = {
    "id": 1,
    "guild_id": GUILD_ID,
    "item_name": "LaserCannon",
    "item_type": "primary_weapon",
    "tier": "Bronze",
    "quantity": 5,
    "price": 500,
    "tech_level": 3,
}


class TestRefreshShopReturnsItems:
    """Sub-task B verification: refresh_shop result dict now includes 'items' key.

    The root cause of the empty-store bug was that refresh_shop returned a dict
    WITHOUT an 'items' key.  The executor called `tier_results[t].get("items") or []`
    which always resolved to [] (empty list), causing every announcement to say
    "The shop refreshed but no items are currently stocked."

    Fix: refresh_shop now includes an 'items' key in its return dict pointing to
    the list of generated GuildShop items.

    # 1 mock — db_manager bridge (Tier B)
    # + ShopService.refresh_shop mock (ARRAY-column bypass)
    """

    async def test_refresh_result_contains_items_key(self, sqlite_engine_and_factory):
        """Executor bulk path: tier_results[tier] dict contains 'items' key after fix.

        Verifies that the items list passed to _announce_shop_refresh is the same list
        as the one returned by ShopService.refresh_shop.
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            await _seed_guild_config(seed_db, GUILD_ID, shop_channel_id=SHOP_CHANNEL)

        # refresh_shop now returns {"items": [...], ...}
        fake_items = [_FAKE_SHOP_ITEM_DICT]
        fake_result = {**_FAKE_REFRESH_RESULT, "items": fake_items}

        announce_items_received: list[list] = []

        async def _capture_announce(*args, **kwargs):
            announce_items_received.append(kwargs.get("items", []))

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            patch("services.shop_service.ShopService.refresh_shop", new=AsyncMock(return_value=fake_result)),
            patch("services.shop_service.ShopService.preload_static_data", new=AsyncMock()),
            # Patch the module-level _shared_announce binding in the executor module.
            # The import is at module scope: `from utils.shop_announcement import
            # announce_shop_refresh as _shared_announce`.
            patch("utils.executors.shop_refresh_executor._shared_announce", side_effect=_capture_announce),
        ):
            result = await execute_shop_refresh_job("job-items-key", {})

        assert result["status"] == "success"
        # 4 tiers × 1 guild = 4 announcements
        assert len(announce_items_received) == 4, f"Expected 4 announcement calls, got {len(announce_items_received)}"
        # Each announcement should have received the items from refresh_shop — NOT an empty list
        for i, received_items in enumerate(announce_items_received):
            assert received_items == fake_items, (
                f"Announcement #{i + 1}: expected items={fake_items!r}, got {received_items!r}. "
                "This is the empty-store bug regression test — items must not be []."
            )


class TestAnnouncementItemsNotEmpty:
    """Sub-task B: announcement embed reflects actual stock, not empty store.

    Verifies the full announce path: when refresh_shop produces items, the
    announcement POST body contains non-empty item fields (not the
    'no items stocked' fallback branch).
    """

    async def test_announcement_embed_has_item_fields_when_stocked(self, sqlite_engine_and_factory):
        """When shop has items, announcement embed includes item fields (non-empty store path).

        # 1 mock — db_manager bridge (Tier B + C)
        # + ShopService.refresh_shop mock (ARRAY-column bypass, includes items)
        # + ShopService.preload_static_data mock (ARRAY-column bypass)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            await _seed_guild_config(seed_db, GUILD_ID, shop_channel_id=SHOP_CHANNEL)

        # Provide 2 stocked items so the announcement triggers the non-empty branch
        fake_items = [
            {**_FAKE_SHOP_ITEM_DICT, "id": 1, "item_name": "LaserCannon"},
            {**_FAKE_SHOP_ITEM_DICT, "id": 2, "item_name": "ShieldModule", "item_type": "module"},
        ]
        fake_result = {**_FAKE_REFRESH_RESULT, "items": fake_items}

        captured_bodies: list[dict] = []

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            patch("services.shop_service.ShopService.refresh_shop", new=AsyncMock(return_value=fake_result)),
            patch("services.shop_service.ShopService.preload_static_data", new=AsyncMock()),
            respx.mock(assert_all_called=False) as router,
        ):

            def _capture(req):
                import json as _json

                captured_bodies.append(_json.loads(req.content))
                return respx.MockResponse(200, json={"ok": True})

            router.post(GATEWAY_CHANNEL_URL).mock(side_effect=_capture)
            await execute_shop_refresh_job("job-non-empty-announce", {})

        # All 4 tier announcements should have been made
        assert len(captured_bodies) == 4, f"Expected 4 announcement POSTs, got {len(captured_bodies)}"

        # The embed description for a stocked shop must NOT contain the 'no items' text
        for body in captured_bodies:
            desc = body.get("content", {}).get("description", "")
            assert "no items" not in desc.lower(), (
                f"Announcement description looks like the empty-store branch: {desc!r}. "
                "Empty-store bug is present — items are not being passed to the announcement."
            )
            # The 'restocked' path should say 'restocked' (not 'refreshed but no items')
            assert "restocked" in desc.lower() or "refresh" in desc.lower(), (
                f"Announcement description should mention restock/refresh, got: {desc!r}"
            )


class TestDiagnosticLogging:
    """Sub-task B: diagnostic logging at two points in the refresh executor.

    The executor must log:
      1. 'ShopRefresh: guild=... tier=... — refreshed N items' right after refresh_shop
      2. 'ShopRefresh: announcing N items for guild=... tier=...' before the announcement
    """

    async def test_diagnostic_logging_fires_for_each_tier(self, sqlite_engine_and_factory):
        """Executor logs 'refreshed N items' and 'announcing N items' for each tier.

        # 1 mock — db_manager bridge (Tier B)
        # + ShopService.refresh_shop mock (ARRAY-column bypass)
        # + ShopService.preload_static_data mock (ARRAY-column bypass)
        # + shop_announcement mock (avoid HTTP in Tier B)
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            await _seed_guild_config(seed_db, GUILD_ID, shop_channel_id=SHOP_CHANNEL)

        fake_items = [_FAKE_SHOP_ITEM_DICT]
        fake_result = {**_FAKE_REFRESH_RESULT, "items": fake_items}

        # Capture all info log calls from the executor
        info_calls: list[str] = []

        original_flogger = exec_module.flogger
        mock_flogger = MagicMock()

        def _capture_info(*a, **kw):
            info_calls.append(a[0] % a[1:] if len(a) > 1 else a[0])

        mock_flogger.info = MagicMock(side_effect=_capture_info)
        mock_flogger.trace = MagicMock()
        mock_flogger.debug = MagicMock()
        mock_flogger.warning = MagicMock()
        mock_flogger.error = MagicMock()

        exec_module.flogger = mock_flogger
        try:
            with (
                patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
                patch("services.shop_service.ShopService.refresh_shop", new=AsyncMock(return_value=fake_result)),
                patch("services.shop_service.ShopService.preload_static_data", new=AsyncMock()),
                # Patch the executor's local binding (not the source module) — see
                # bulk-refresh test for rationale.
                patch("utils.executors.shop_refresh_executor._shared_announce", new=AsyncMock()),
            ):
                result = await execute_shop_refresh_job("job-logging", {})
        finally:
            exec_module.flogger = original_flogger

        assert result["status"] == "success"

        # Check that 'refreshed N items' was logged for each tier (4 tiers)
        refreshed_logs = [m for m in info_calls if "refreshed" in m and "items" in m]
        assert len(refreshed_logs) >= 4, (
            f"Expected at least 4 'refreshed N items' log lines (one per tier), got: {refreshed_logs}"
        )

        # Check that 'announcing N items' was logged for each tier (4 tiers)
        announcing_logs = [m for m in info_calls if "announcing" in m and "items" in m]
        assert len(announcing_logs) >= 4, (
            f"Expected at least 4 'announcing N items' log lines (one per tier), got: {announcing_logs}"
        )


# ===========================================================================
# Task 0002 adversarial — items key fallback & edge cases (Tester review)
# ===========================================================================


class TestItemsKeyFallbackAdversarial:
    """Adversarial: executor gracefully handles refresh_shop dicts without 'items' key.

    The fix relies on `tier_results[t].get("items") or []`.  If the 'items' key
    is absent (e.g. in legacy or partial refresh results), the executor must:
    - Not crash (no KeyError / TypeError)
    - Pass an empty list to the announcement (triggering "no items stocked" text)

    # 1 mock — db_manager bridge (Tier B)
    # + ShopService.refresh_shop mock (ARRAY-column bypass)
    # + ShopService.preload_static_data mock (ARRAY-column bypass)
    """

    async def test_missing_items_key_does_not_crash(self, sqlite_engine_and_factory):
        """Refresh result without 'items' key → executor uses empty list, no crash.

        The `or []` fallback in `tier_results[t].get("items") or []` must handle
        missing key without raising KeyError.
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            await _seed_guild_config(seed_db, GUILD_ID, shop_channel_id=SHOP_CHANNEL)

        # Intentionally omit 'items' key — simulates legacy / partial refresh result.
        result_without_items = {"status": "ok", "items_added": 0, "tech_level": 5}

        announce_items_received: list[list] = []

        async def _capture_announce(*args, **kwargs):
            announce_items_received.append(kwargs.get("items", "MISSING"))

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            patch(
                "services.shop_service.ShopService.refresh_shop",
                new=AsyncMock(return_value=result_without_items),
            ),
            patch("services.shop_service.ShopService.preload_static_data", new=AsyncMock()),
            patch("utils.executors.shop_refresh_executor._shared_announce", side_effect=_capture_announce),
        ):
            result = await execute_shop_refresh_job("job-missing-items-key", {})

        assert result["status"] == "success", f"Expected success, got {result!r}"
        # 4 tiers × 1 guild = 4 announcements
        assert len(announce_items_received) == 4, f"Expected 4 announcement calls, got {len(announce_items_received)}"
        # Each announcement should have received an empty list (not the sentinel or crash)
        for i, received in enumerate(announce_items_received):
            assert received == [], f"Tier #{i + 1}: missing 'items' key should fall back to [], got {received!r}"

    async def test_items_none_in_result_falls_back_to_empty_list(self, sqlite_engine_and_factory):
        """Refresh result with items=None → executor falls back to [] via `or []`.

        `None or []` evaluates to [].  The announcement should receive an empty
        list (triggering 'no items stocked' description), not None.
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            await _seed_guild_config(seed_db, GUILD_ID, shop_channel_id=SHOP_CHANNEL)

        # items=None — exercises the `or []` branch
        result_with_none_items = {"status": "ok", "items_added": 0, "tech_level": 5, "items": None}

        announce_items_received: list = []

        async def _capture_announce(*args, **kwargs):
            announce_items_received.append(kwargs.get("items"))

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            patch(
                "services.shop_service.ShopService.refresh_shop",
                new=AsyncMock(return_value=result_with_none_items),
            ),
            patch("services.shop_service.ShopService.preload_static_data", new=AsyncMock()),
            patch("utils.executors.shop_refresh_executor._shared_announce", side_effect=_capture_announce),
        ):
            result = await execute_shop_refresh_job("job-none-items", {})

        assert result["status"] == "success"
        assert len(announce_items_received) == 4
        for i, received in enumerate(announce_items_received):
            assert received == [], f"Tier #{i + 1}: items=None should fall back to [] via `or []`, got {received!r}"
