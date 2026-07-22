"""Tests for shop_announcements_role_id role selection in shop_refresh_executor.

Drives the REAL ``execute_shop_refresh_job`` (real SQLite GuildConfig + respx),
mirroring the Tier-B/C pattern in ``test_shop_refresh_executor.py``.  Verifies
that the executor:

  - prefers ``shop_announcements_role_id`` over ``bounty_hunter_role_id`` for the
    first-tier (Bronze) mention,
  - falls back to ``bounty_hunter_role_id`` when the shop role is None,
  - emits NO role mention when both are None,
  - only mentions on the first tier (Bronze), never on Silver/Gold/Platinum.

The previous version of this file re-implemented the selection expression inside
the test and asserted on the copy, plus grepped source files for substrings —
neither exercised any production code.  Real column presence is now proven via
``GuildConfig.__table__`` introspection rather than a source-text search.
"""

from __future__ import annotations

import json
import os
import sys
import types
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup + stub registration (mirrors test_shop_refresh_executor.py).
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

import pytest
import respx
import utils.executors.shop_refresh_executor as exec_module
from persist.models.base import Base
from persist.models.guild_config import GuildConfig
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

execute_shop_refresh_job = exec_module.execute_shop_refresh_job

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SQLITE_TABLES = [GuildConfig.__table__]

GUILD_ID = 9_600_000_010
SHOP_CHANNEL = 12_500
SHOP_ANN_ROLE = 44_444
BH_ROLE = 33_333

GATEWAY_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
GATEWAY_PORT = os.getenv("GATEWAY_PORT", "7999")
GATEWAY_CHANNEL_URL = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/api/v1/channels/{SHOP_CHANNEL}/messages"

_FAKE_REFRESH_RESULT: dict = {"status": "ok", "items_added": 3, "tech_level": 5, "items": []}


# ---------------------------------------------------------------------------
# Fixtures / helpers (same shape as test_shop_refresh_executor.py)
# ---------------------------------------------------------------------------


@pytest.fixture
async def sqlite_engine_and_factory():
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


async def _seed_guild_config(
    db: AsyncSession,
    *,
    shop_channel_id: int | None = SHOP_CHANNEL,
    shop_announcements_role_id: int | None = None,
    bounty_hunter_role_id: int | None = None,
) -> GuildConfig:
    config = GuildConfig(
        guild_id=GUILD_ID,
        shop_channel_id=shop_channel_id,
        shop_announcements_role_id=shop_announcements_role_id,
        bounty_hunter_role_id=bounty_hunter_role_id,
        division_temperatures={"bronze": 1.0, "silver": 1.0, "gold": 1.0, "platinum": 1.0},
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return config


def _make_fake_db_manager(factory: Any):
    """MagicMock db_manager whose get_session() yields a real SQLite session.

    # 1 mock — db_manager bridge (Tier B/C)
    """

    @asynccontextmanager
    async def _fake_get_db():
        async with factory() as session:
            yield session

    fake = MagicMock()
    fake.get_session = MagicMock(side_effect=_fake_get_db)
    return fake


async def _run_and_capture_mentions(factory, router) -> list:
    """Run the bulk refresh and return the ordered list of per-tier text_content values."""
    captured: list = []

    def _capture(request):
        body = json.loads(request.content)
        captured.append(body.get("text_content"))
        return respx.MockResponse(200, json={"ok": True})

    router.post(GATEWAY_CHANNEL_URL).mock(side_effect=_capture)

    with (
        patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
        patch("services.shop_service.ShopService.refresh_shop", new=AsyncMock(return_value=_FAKE_REFRESH_RESULT)),
        patch("services.shop_service.ShopService.preload_static_data", new=AsyncMock()),
    ):
        result = await execute_shop_refresh_job("job-role", {})

    assert result["status"] == "success"
    return captured


# ---------------------------------------------------------------------------
# Real column-presence check (introspection, not source grep)
# ---------------------------------------------------------------------------


def test_guild_config_declares_shop_announcements_role_id_column():
    """GuildConfig ORM model actually declares the shop_announcements_role_id column."""
    cols = GuildConfig.__table__.c
    assert "shop_announcements_role_id" in cols, "GuildConfig must declare shop_announcements_role_id"
    assert cols["shop_announcements_role_id"].nullable is True


# ---------------------------------------------------------------------------
# Behavioural role-selection tests (real executor + respx)
# ---------------------------------------------------------------------------


class TestShopAnnouncementsRoleSelection:
    """Role mention is driven by the executor's real config-read + selection logic."""

    async def test_shop_announcements_role_preferred_over_bounty_hunter(self, sqlite_engine_and_factory):
        """shop_announcements_role_id (int) is used for the Bronze mention over bounty_hunter_role_id."""
        _engine, factory = sqlite_engine_and_factory
        async with factory() as db:
            await _seed_guild_config(db, shop_announcements_role_id=SHOP_ANN_ROLE, bounty_hunter_role_id=BH_ROLE)

        with respx.mock(assert_all_called=False) as router:
            mentions = await _run_and_capture_mentions(factory, router)

        assert len(mentions) == 4, f"Expected 4 tier announcements, got {mentions!r}"
        assert mentions[0] == f"<@&{SHOP_ANN_ROLE}>", f"Bronze must mention shop role, got {mentions[0]!r}"
        for m in mentions[1:]:
            assert m is None, f"Non-Bronze tiers must not mention any role, got {m!r}"

    async def test_falls_back_to_bounty_hunter_when_shop_role_none(self, sqlite_engine_and_factory):
        """With shop_announcements_role_id None, Bronze mention falls back to bounty_hunter_role_id."""
        _engine, factory = sqlite_engine_and_factory
        async with factory() as db:
            await _seed_guild_config(db, shop_announcements_role_id=None, bounty_hunter_role_id=BH_ROLE)

        with respx.mock(assert_all_called=False) as router:
            mentions = await _run_and_capture_mentions(factory, router)

        assert mentions[0] == f"<@&{BH_ROLE}>", f"Bronze must fall back to bounty hunter role, got {mentions[0]!r}"
        for m in mentions[1:]:
            assert m is None

    async def test_no_mention_when_both_roles_none(self, sqlite_engine_and_factory):
        """With both role IDs None, no tier includes a role mention."""
        _engine, factory = sqlite_engine_and_factory
        async with factory() as db:
            await _seed_guild_config(db, shop_announcements_role_id=None, bounty_hunter_role_id=None)

        with respx.mock(assert_all_called=False) as router:
            mentions = await _run_and_capture_mentions(factory, router)

        assert len(mentions) == 4
        assert all(m is None for m in mentions), f"Expected no mentions, got {mentions!r}"
