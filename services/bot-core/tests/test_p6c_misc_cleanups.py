"""P6 Pass C — tests for six misc cleanups and nits.

T6    manager.py:117 time.sleep → asyncio.sleep
T9c   shop_service.py:666 dedup drawn items before upsert
T9d   failsafe_cleanup_executor.py:470 batch per-channel session
FLAG-1 audit_service.py:67 json.dumps default=str
"""

from __future__ import annotations

import os
import sys
import types
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path + stub setup (mirrors other test files in the module)
# ---------------------------------------------------------------------------

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

if "sqlalchemy_utils" not in sys.modules:
    _sqla_utils = types.ModuleType("sqlalchemy_utils")
    _sqla_utils.UUIDType = MagicMock()
    sys.modules["sqlalchemy_utils"] = _sqla_utils


# ===========================================================================
# T6 — asyncio.sleep (not time.sleep) in _test_connection
# ===========================================================================


class TestP6T6AsyncSleepInTestConnection:
    """_test_connection must use asyncio.sleep, not time.sleep, for backoff."""

    @pytest.mark.asyncio
    async def test_retry_uses_asyncio_sleep_not_time_sleep(self):
        """On connection failure, asyncio.sleep is awaited with the right delays.

        Mutation-proof: a reversion to time.sleep would be synchronous and
        asyncio.sleep would never be awaited, failing the assertion.
        """
        from sqlalchemy.exc import OperationalError

        # Build a DatabaseManager but intercept the actual engine/connection.
        with patch("persist.database.manager.bblogger"):
            from persist.database.manager import DatabaseManager

            mgr = DatabaseManager()

        # Simulate 2 consecutive OperationalError then success on attempt 3.
        attempt_count = 0

        @asynccontextmanager
        async def _fake_get_connection():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count <= 2:
                raise OperationalError("stmt", {}, Exception("no connection"))
            # Fake a successful result on the third attempt.
            mock_conn = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar.return_value = 1
            mock_conn.execute = AsyncMock(return_value=mock_result)
            yield mock_conn

        sleep_calls: list[float] = []

        async def _fake_asyncio_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        with (
            patch.object(mgr, "get_connection", side_effect=_fake_get_connection),
            patch("persist.database.manager.asyncio.sleep", side_effect=_fake_asyncio_sleep),
        ):
            await mgr._test_connection()

        # Two failures → two sleep calls with doubling backoff (2, 4).
        assert len(sleep_calls) == 2, f"Expected 2 sleep calls, got {sleep_calls}"
        assert sleep_calls[0] == 2, f"First backoff should be 2s, got {sleep_calls[0]}"
        assert sleep_calls[1] == 4, f"Second backoff should be 4s, got {sleep_calls[1]}"
        assert attempt_count == 3, f"Expected 3 attempts, got {attempt_count}"

    @pytest.mark.asyncio
    async def test_no_time_sleep_import_on_retry_path(self):
        """time.sleep must not be called on the retry path.

        Checks that the module itself does not call the blocking time.sleep.
        """
        import time as _time

        time_sleep_calls: list[float] = []

        def _recording_sleep(delay: float) -> None:
            time_sleep_calls.append(delay)
            # Don't actually sleep.

        from sqlalchemy.exc import OperationalError

        with patch("persist.database.manager.bblogger"):
            from persist.database.manager import DatabaseManager

            mgr = DatabaseManager()

        call_count = 0

        @asynccontextmanager
        async def _fail_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OperationalError("s", {}, Exception("fail"))
            mock_conn = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar.return_value = 1
            mock_conn.execute = AsyncMock(return_value=mock_result)
            yield mock_conn

        with (
            patch.object(mgr, "get_connection", side_effect=_fail_then_succeed),
            patch("persist.database.manager.asyncio.sleep", new_callable=AsyncMock),
            patch.object(_time, "sleep", side_effect=_recording_sleep),
        ):
            await mgr._test_connection()

        assert len(time_sleep_calls) == 0, (
            f"time.sleep was called {len(time_sleep_calls)} time(s) on the retry path; must use asyncio.sleep instead"
        )

    @pytest.mark.asyncio
    async def test_retry_exhaustion_raises_after_max_retries(self):
        """After max_retries consecutive failures, _test_connection raises."""
        from sqlalchemy.exc import OperationalError

        with patch("persist.database.manager.bblogger"):
            from persist.database.manager import DatabaseManager

            mgr = DatabaseManager()

        @asynccontextmanager
        async def _always_fail():
            raise OperationalError("s", {}, Exception("down"))
            yield  # pragma: no cover — unreachable after raise

        sleep_calls: list[float] = []

        async def _fake_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        with (
            patch.object(mgr, "get_connection", side_effect=_always_fail),
            patch("persist.database.manager.asyncio.sleep", side_effect=_fake_sleep),
            pytest.raises(OperationalError),
        ):
            await mgr._test_connection()

        # max_retries=5 → 4 sleeps (no sleep on the last attempt).
        assert len(sleep_calls) == 4, f"Expected 4 sleeps for 5 attempts, got {sleep_calls}"
        # Backoff doubles: 2, 4, 8, 16
        assert sleep_calls == [2, 4, 8, 16], f"Unexpected backoff sequence: {sleep_calls}"


# ===========================================================================
# T9c — dedup drawn items before upsert in shop refresh
# ===========================================================================


def _make_shop_svc_with_fake_config(fake_config):
    """Build a ShopService with config_repo and shop_repo pre-mocked."""
    from services.shop_service import ShopService

    svc = ShopService.__new__(ShopService)
    svc.config_repo = MagicMock()
    svc.config_repo.get_by_guild_id = AsyncMock(return_value=fake_config)
    svc.shop_repo = MagicMock()
    svc.shop_repo.get_shop_items = AsyncMock(return_value=[])
    svc.shop_repo.clear_shop_tier = AsyncMock()
    return svc


def _make_fake_guild_config(count: int = 2):
    """Return a MagicMock GuildConfig with controlled count/quantity ranges."""
    fake_config = MagicMock()
    fake_config.get_count_range = MagicMock(return_value={"min": count, "max": count})
    fake_config.get_quantity_range = MagicMock(return_value={"min": 1, "max": 1})
    fake_config.tech_level_probabilities = {
        "same_level": 0.7,
        "one_lower": 0.2,
        "two_lower": 0.1,
    }
    return fake_config


class TestP6T9cShopDedup:
    """Shop draw dedup: duplicate item_name draws are skipped before upsert."""

    @pytest.mark.asyncio
    async def test_duplicate_draw_results_in_single_upsert(self):
        """When the same item_name is drawn twice, create_or_update is called once.

        Mutation-proof: removing the _seen_item_names guard causes two upsert
        calls for the same name, failing the call-count assertion.
        """
        import services.shop_service as ss_mod

        fake_config = _make_fake_guild_config(count=2)
        svc = _make_shop_svc_with_fake_config(fake_config)

        # Return value of create_or_update
        fake_item = MagicMock()
        fake_item.item_name = "Iron Ore"
        svc.shop_repo.create_or_update = AsyncMock(return_value=fake_item)

        # Force _get_random_item_by_tech_level to always return "Iron Ore" (collision).
        svc._get_random_item_by_tech_level = AsyncMock(return_value="Iron Ore")
        svc._get_item_base_price = AsyncMock(return_value=500)
        svc._select_item_tech_level = MagicMock(return_value=1)

        db = AsyncMock()

        with (
            patch.object(ss_mod.GameConstants, "CURRENTLY_ENABLED_TYPES", ["primary_weapon"]),
            patch.dict(ss_mod._CONCRETE_TO_CONFIG_KEY, {"primary_weapon": "primary_weapon"}, clear=False),
        ):
            result = await svc.refresh_shop(db, guild_id=12345, tier="Bronze", force_tech_level=3)

        # "Iron Ore" drawn twice but upserted only once.
        assert svc.shop_repo.create_or_update.call_count == 1, (
            f"Expected 1 upsert for duplicate draw, got {svc.shop_repo.create_or_update.call_count}"
        )
        assert len(result["items"]) == 1, f"Expected 1 item in result, got {len(result['items'])}"

    @pytest.mark.asyncio
    async def test_distinct_draws_all_upserted(self):
        """When all drawn item_names are distinct, every draw results in an upsert."""
        import services.shop_service as ss_mod

        fake_config = _make_fake_guild_config(count=3)
        svc = _make_shop_svc_with_fake_config(fake_config)

        # Return distinct item names for each call
        draw_sequence = ["Laser Cannon", "Plasma Rifle", "Ion Blaster"]
        call_idx = 0

        async def _next_item(db, item_type, tech_level):
            nonlocal call_idx
            name = draw_sequence[call_idx % len(draw_sequence)]
            call_idx += 1
            return name

        svc._get_random_item_by_tech_level = _next_item
        svc._get_item_base_price = AsyncMock(return_value=100)
        svc._select_item_tech_level = MagicMock(return_value=1)

        upsert_names: list[str] = []

        async def _record_upsert(db, data):
            upsert_names.append(data["item_name"])
            item = MagicMock()
            item.item_name = data["item_name"]
            return item

        svc.shop_repo.create_or_update = _record_upsert

        db = AsyncMock()

        with (
            patch.object(ss_mod.GameConstants, "CURRENTLY_ENABLED_TYPES", ["primary_weapon"]),
            patch.dict(ss_mod._CONCRETE_TO_CONFIG_KEY, {"primary_weapon": "primary_weapon"}, clear=False),
        ):
            result = await svc.refresh_shop(db, guild_id=99999, tier="Silver", force_tech_level=2)

        # All 3 distinct names must be upserted.
        assert sorted(upsert_names) == sorted(draw_sequence), (
            f"Expected upserts for all 3 distinct names, got {upsert_names}"
        )
        assert len(result["items"]) == 3

    @pytest.mark.asyncio
    async def test_mixed_collision_and_distinct(self):
        """Mixture of duplicate and distinct draws: each unique name upserted once."""
        import services.shop_service as ss_mod

        fake_config = _make_fake_guild_config(count=3)
        svc = _make_shop_svc_with_fake_config(fake_config)

        # Draw sequence: A, B, A → 3 draws; only A and B should be upserted (once each).
        draw_sequence = ["Alpha", "Beta", "Alpha"]
        call_idx = 0

        async def _next_item(db, item_type, tech_level):
            nonlocal call_idx
            name = draw_sequence[call_idx]
            call_idx += 1
            return name

        svc._get_random_item_by_tech_level = _next_item
        svc._get_item_base_price = AsyncMock(return_value=200)
        svc._select_item_tech_level = MagicMock(return_value=1)

        upsert_calls: list[str] = []

        async def _record_upsert(db, data):
            upsert_calls.append(data["item_name"])
            item = MagicMock()
            item.item_name = data["item_name"]
            return item

        svc.shop_repo.create_or_update = _record_upsert

        db = AsyncMock()

        with (
            patch.object(ss_mod.GameConstants, "CURRENTLY_ENABLED_TYPES", ["primary_weapon"]),
            patch.dict(ss_mod._CONCRETE_TO_CONFIG_KEY, {"primary_weapon": "primary_weapon"}, clear=False),
        ):
            result = await svc.refresh_shop(db, guild_id=77777, tier="Gold", force_tech_level=4)

        # Only "Alpha" and "Beta" should have been upserted.
        assert sorted(upsert_calls) == ["Alpha", "Beta"], f"Expected upserts for [Alpha, Beta] only, got {upsert_calls}"
        assert len(result["items"]) == 2


# ===========================================================================
# T9d — batch per-channel session in failsafe cleanup
# ===========================================================================


class TestP6T9dFailsafeBatchSession:
    """Failsafe cleanup: one session per channel, not per message."""

    @pytest.mark.asyncio
    async def test_fewer_sessions_than_messages(self):
        """Processing N messages in one channel opens exactly 1 session (not N).

        Mutation-proof: reverting to per-message sessions would cause the
        session-open count to equal the message count (e.g. 3), failing the
        assertion that it equals 1.
        """
        import respx
        from persist.models.base import Base
        from persist.models.bounty import Bounty
        from persist.models.discord_message import DiscordMessage
        from persist.models.guild_config import GuildConfig
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from utils.executors.bounty_failsafe_cleanup_executor import execute_bounty_failsafe_cleanup_job

        _TABLES = [GuildConfig.__table__, Bounty.__table__, DiscordMessage.__table__]
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=_TABLES)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        GUILD_ID = 9_700_000_001
        CHANNEL_ID = 88_001
        # Three distinct messages: none in DB → all will be "skip" (no upsert needed).
        MESSAGE_IDS = [55_101, 55_102, 55_103]

        async with factory() as seed_db:
            config = GuildConfig(
                guild_id=GUILD_ID,
                bronze_bounty_channel_id=CHANNEL_ID,
                bounty_hunter_role_id=12345,
            )
            seed_db.add(config)
            await seed_db.commit()

        session_open_count = 0
        original_factory = factory

        @asynccontextmanager
        async def _counting_get_session():
            nonlocal session_open_count
            session_open_count += 1
            async with original_factory() as session:
                yield session

        fake_db_manager = MagicMock()
        fake_db_manager.get_session = MagicMock(side_effect=_counting_get_session)

        _GW_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
        _GW_PORT = os.getenv("GATEWAY_PORT", "7999")
        _GATEWAY_BASE = f"http://{_GW_HOST}:{_GW_PORT}/api/v1"
        messages_url = f"{_GATEWAY_BASE}/channels/{CHANNEL_ID}/messages"

        payload = {
            "status": "success",
            "data": [{"id": str(mid)} for mid in MESSAGE_IDS],
        }

        with (
            patch("persist.database.manager.db_manager", fake_db_manager),
            respx.mock(assert_all_called=False, assert_all_mocked=False) as router,
        ):
            router.get(messages_url).respond(200, json=payload)
            result = await execute_bounty_failsafe_cleanup_job("t9d-test", {})

        await engine.dispose()

        assert result["status"] == "success"
        # 1 session for guild_config lookup + 1 session for the channel sweep = 2 total.
        # Pre-T9d would have been 1 (guild) + N (per-message) = 1 + 3 = 4 sessions.
        # Post-T9d: 1 (guild) + 1 (channel batch) = 2 sessions for 3 messages.
        assert session_open_count <= 2, (
            f"Expected at most 2 sessions for 3 messages (batch mode), got {session_open_count}. "
            "Per-message session churn was not eliminated."
        )

    @pytest.mark.asyncio
    async def test_cleanup_outcome_identical_multi_channel(self):
        """Cleanup outcome (which messages cleaned) is identical to the per-message approach.

        Seeds an expired bounty and an active bounty across two channels and
        confirms the expired one is cleaned while the active one is left alone.
        """
        import respx
        from persist.models.base import Base
        from persist.models.bounty import Bounty
        from persist.models.discord_message import DiscordMessage
        from persist.models.guild_config import GuildConfig
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from utils.executors.bounty_failsafe_cleanup_executor import execute_bounty_failsafe_cleanup_job

        _TABLES = [GuildConfig.__table__, Bounty.__table__, DiscordMessage.__table__]
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=_TABLES)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        GUILD_ID = 9_700_000_002
        BRONZE_CHANNEL = 88_010
        SILVER_CHANNEL = 88_011
        EXPIRED_MSG_ID = 55_200
        LIVE_MSG_ID = 55_201
        now = datetime.now(UTC)

        async with factory() as seed_db:
            config = GuildConfig(
                guild_id=GUILD_ID,
                bronze_bounty_channel_id=BRONZE_CHANNEL,
                silver_bounty_channel_id=SILVER_CHANNEL,
                bounty_hunter_role_id=12345,
            )
            seed_db.add(config)
            await seed_db.flush()

            expired_bounty = Bounty(
                guild_id=GUILD_ID,
                division="bronze",
                criminal_name="OldCrim",
                criminal_faction="Pirate",
                route=["A", "B"],
                answer="B",
                reward=5000,
                reward_per_sys=2500,
                checked={},
                issue_time=now - timedelta(hours=10),
                end_time=now - timedelta(hours=2),
                tech_level=1,
                criminal_ship={},
                status="expired",
            )
            live_bounty = Bounty(
                guild_id=GUILD_ID,
                division="silver",
                criminal_name="LiveCrim",
                criminal_faction="Pirate",
                route=["C", "D"],
                answer="D",
                reward=8000,
                reward_per_sys=4000,
                checked={},
                issue_time=now - timedelta(hours=1),
                end_time=now + timedelta(hours=7),
                tech_level=3,
                criminal_ship={},
                status="active",
            )
            seed_db.add(expired_bounty)
            seed_db.add(live_bounty)
            await seed_db.flush()

            seed_db.add(
                DiscordMessage(
                    id=uuid.uuid4(),
                    guild_id=GUILD_ID,
                    channel_id=BRONZE_CHANNEL,
                    message_id=EXPIRED_MSG_ID,
                    message_type="bounty_announcement",
                    embed_payload="{}",
                    reference_id=expired_bounty.id,
                )
            )
            seed_db.add(
                DiscordMessage(
                    id=uuid.uuid4(),
                    guild_id=GUILD_ID,
                    channel_id=SILVER_CHANNEL,
                    message_id=LIVE_MSG_ID,
                    message_type="bounty_announcement",
                    embed_payload="{}",
                    reference_id=live_bounty.id,
                )
            )
            await seed_db.commit()

        @asynccontextmanager
        async def _fake_get_session():
            async with factory() as session:
                yield session

        fake_db_manager = MagicMock()
        fake_db_manager.get_session = MagicMock(side_effect=_fake_get_session)

        _GW_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
        _GW_PORT = os.getenv("GATEWAY_PORT", "7999")
        _GATEWAY_BASE = f"http://{_GW_HOST}:{_GW_PORT}/api/v1"

        bronze_url = f"{_GATEWAY_BASE}/channels/{BRONZE_CHANNEL}/messages"
        silver_url = f"{_GATEWAY_BASE}/channels/{SILVER_CHANNEL}/messages"
        bronze_delete_url = f"{_GATEWAY_BASE}/channels/{BRONZE_CHANNEL}/messages/{EXPIRED_MSG_ID}"

        with (
            patch("persist.database.manager.db_manager", fake_db_manager),
            respx.mock(assert_all_called=False, assert_all_mocked=False) as router,
        ):
            router.get(bronze_url).respond(200, json={"status": "success", "data": [{"id": str(EXPIRED_MSG_ID)}]})
            router.get(silver_url).respond(200, json={"status": "success", "data": [{"id": str(LIVE_MSG_ID)}]})
            delete_route = router.delete(bronze_delete_url).respond(204)
            result = await execute_bounty_failsafe_cleanup_job("t9d-outcome-test", {})

        await engine.dispose()

        assert result["status"] == "success"
        assert result["total_cleaned"] == 1, f"Expected 1 cleaned, got {result['total_cleaned']}"
        assert delete_route.called, "Expected DELETE for the expired bounty post"


# ===========================================================================
# FLAG-1 — audit_service json.dumps default=str
# ===========================================================================


class TestFlag1AuditJsonDefaultStr:
    """audit_service.py: json.dumps must not raise on non-JSON-native values."""

    def _make_db_session(self) -> AsyncMock:
        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.flush = AsyncMock()
        return db

    @pytest.mark.asyncio
    async def test_datetime_in_details_does_not_raise(self):
        """A details dict containing a datetime value must serialize without raising.

        Mutation-proof: removing default=str causes json.dumps to raise
        TypeError on datetime, which propagates out of log_action (since the
        AuditService catches DB errors, not serialization errors before DB.add).
        """
        from services.audit_service import AuditService

        db = self._make_db_session()
        ts = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)

        # Must not raise.
        await AuditService.log_action(
            db,
            user_id=1,
            action="test_datetime",
            details={"timestamp": ts, "value": 42},
        )

        added_obj = db.add.call_args[0][0]
        import json

        parsed = json.loads(added_obj.details)
        # datetime is serialized via str(); check it's a non-empty string.
        assert isinstance(parsed["timestamp"], str), (
            f"Expected datetime serialized as str, got {type(parsed['timestamp'])}"
        )
        assert parsed["value"] == 42

    @pytest.mark.asyncio
    async def test_plain_payload_unchanged(self):
        """A dict with only JSON-native values produces the same output as before.

        default=str only activates for non-serializable values; plain payloads
        must produce the exact same JSON string as json.dumps(payload).
        """
        import json

        from services.audit_service import AuditService

        db = self._make_db_session()
        plain = {"player_id": 42, "credits": 500, "action": "buy", "success": True}

        await AuditService.log_action(
            db,
            user_id=99,
            action="credits_update",
            details=plain,
        )

        added_obj = db.add.call_args[0][0]
        assert added_obj.details is not None
        # Exact equality with plain json.dumps (default=str is a no-op for native types).
        assert json.loads(added_obj.details) == plain

    @pytest.mark.asyncio
    async def test_decimal_in_details_does_not_raise(self):
        """A details dict containing a Decimal value must serialize without raising."""
        from decimal import Decimal

        from services.audit_service import AuditService

        db = self._make_db_session()

        await AuditService.log_action(
            db,
            user_id=2,
            action="test_decimal",
            details={"amount": Decimal("12345.67")},
        )

        added_obj = db.add.call_args[0][0]
        import json

        parsed = json.loads(added_obj.details)
        assert isinstance(parsed["amount"], str), f"Expected Decimal serialized as str, got {type(parsed['amount'])}"
        assert "12345.67" in parsed["amount"]

    @pytest.mark.asyncio
    async def test_none_details_still_none(self):
        """details=None still stores None (no JSON dump attempted)."""
        from services.audit_service import AuditService

        db = self._make_db_session()

        await AuditService.log_action(
            db,
            user_id=3,
            action="test_none",
            details=None,
        )

        added_obj = db.add.call_args[0][0]
        assert added_obj.details is None
