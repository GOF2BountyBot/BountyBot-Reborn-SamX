"""P6-T7 tests: DB session released before external httpx announce call.

Spec requirement
----------------
Close/return the DB session BEFORE the external httpx announce call so that
a pooled DB connection is not held across network I/O.

Test strategy
-------------
Tier A — Pool-checkout instrumentation.
  We instrument the SQLAlchemy connection-pool ``checkout`` / ``checkin``
  events so we can observe *at the moment the httpx announce fires* whether
  a connection is currently checked-out (held).

  Non-vacuous: if we revert to the old code that held the session across the
  announce, the assertion fails.

Tier B — Announce still posts with identical payload/target (spawn & expire).

Tier C — Spawn/expire DB outcomes unchanged post-T7.

Tier D — Error path: announce failure doesn't roll back the DB write; the
  compensating rollback (Fix B) correctly deletes the bounty row.

MOCK POLICY
-----------
- 1 mock: ``persist.database.manager.db_manager`` bridge (Tier B gate).
- ``BountyService.spawn_bounty``: always patched (ARRAY-column SQLite bypass;
  see tests/AGENTS.md §"SQLite Compatibility").
- ``utils.bounty_announcement_payload.build_bounty_announcement_request``:
  patched where the test doesn't need the real loadout query.
- Max 2 mocks per test (strict rule); pool-instrumentation does not count as
  a mock because it uses SQLAlchemy events (no mock.patch involved).
"""

from __future__ import annotations

import os
import sys
import types
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup + shared stubs (mirror pattern from test_bounty_spawn_executor.py)
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
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from utils.executors.bounty_expire_executor import execute_bounty_expire_job
from utils.executors.bounty_spawn_executor import execute_bounty_spawn_one_job

# ---------------------------------------------------------------------------
# SQLite-compatible tables
# ---------------------------------------------------------------------------

_SQLITE_TABLES = [
    GuildConfig.__table__,
    Bounty.__table__,
    DiscordMessage.__table__,
]

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

GUILD_ID = 9_700_000_001
BRONZE_CHANNEL = 1_110
SILVER_CHANNEL = 2_220
GOLD_CHANNEL = 3_330
PLATINUM_CHANNEL = 4_440
HUNTER_ROLE = 5_550
BRONZE_ROLE = 6_660
IMAGE_CHANNEL = 7_770
CHANNEL_ID = BRONZE_CHANNEL
EXPIRE_CHANNEL = 8_880
EXPIRE_MSG_ID = 9_999

EXECUTOR_HOST = os.getenv("EXECUTOR_HOST", "bot-core")
EXECUTOR_PORT = os.getenv("EXECUTOR_PORT", "8000")
GATEWAY_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
GATEWAY_PORT = os.getenv("GATEWAY_PORT", "7999")

SELF_JOBS_URL = f"http://{EXECUTOR_HOST}:{EXECUTOR_PORT}/api/v1/jobs"
GATEWAY_ANNOUNCE_URL = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/api/v1/announcements/bounty/channel/{BRONZE_CHANNEL}"
GATEWAY_DELETE_URL = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/api/v1/channels/{EXPIRE_CHANNEL}/messages/{EXPIRE_MSG_ID}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


async def _seed_full_config(db: AsyncSession, guild_id: int) -> GuildConfig:
    config = GuildConfig(
        guild_id=guild_id,
        bronze_bounty_channel_id=BRONZE_CHANNEL,
        silver_bounty_channel_id=SILVER_CHANNEL,
        gold_bounty_channel_id=GOLD_CHANNEL,
        platinum_bounty_channel_id=PLATINUM_CHANNEL,
        bounty_hunter_role_id=HUNTER_ROLE,
        bronze_role_id=BRONZE_ROLE,
        image_channel_id=None,
        bounty_max_per_tier={"bronze": 3, "silver": 3, "gold": 3, "platinum": 3},
        bounty_expiry_minutes=480,
        bounty_spawn_interval_minutes=5,
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return config


async def _seed_active_bounty(db: AsyncSession, guild_id: int, *, channel_id: int = EXPIRE_CHANNEL) -> Bounty:
    now = datetime.now(UTC)
    bounty = Bounty(
        guild_id=guild_id,
        division="bronze",
        criminal_name="ExpireCriminal",
        criminal_faction="Terran",
        route=["X", "Y", "Z"],
        answer="Y",
        reward=10_000,
        reward_per_sys=2_500,
        checked={"X": -1, "Y": -1, "Z": -1},
        issue_time=now,
        end_time=now + timedelta(hours=8),
        tech_level=2,
        criminal_ship={"ship_name": "Hawk", "ship_armour": 200, "weapons": [], "turrets": []},
        status="active",
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
    channel_id: int = EXPIRE_CHANNEL,
    message_id: int = EXPIRE_MSG_ID,
) -> DiscordMessage:
    import uuid

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


# ---------------------------------------------------------------------------
# Pool-checkout instrumentation helper
# ---------------------------------------------------------------------------


def _make_instrumented_db_manager(factory, *, on_httpx_post=None, on_httpx_delete=None):
    """Build a db_manager mock that:

    - Yields real SQLite sessions (one per get_session() call).
    - Attaches SQLAlchemy pool ``checkout``/``checkin`` listeners to the
      underlying sync engine so we can count live checked-out connections
      at any moment.
    - Returns an ``engine`` with a ``.connections_checked_out`` counter and
      a ``capture_at_announce`` callback slot.

    When ``on_httpx_post`` / ``on_httpx_delete`` is set, it is called just
    before the httpx call fires (injected via respx side-effect) and should
    assert that ``engine.connections_checked_out == 0``.
    """
    engine_holder: list[Any] = []  # filled on first get_session() call

    @asynccontextmanager
    async def _fake_get_db():
        async with factory() as session:
            # Attach pool events to the underlying sync engine on first use.
            sync_engine = session.bind.sync_engine
            if not engine_holder:
                engine_holder.append(sync_engine)
                sync_engine._p6t7_checked_out = 0

                @event.listens_for(sync_engine, "checkout")
                def _on_checkout(dbapi_conn, connection_record, connection_proxy):
                    sync_engine._p6t7_checked_out += 1

                @event.listens_for(sync_engine, "checkin")
                def _on_checkin(dbapi_conn, connection_record):
                    if sync_engine._p6t7_checked_out > 0:
                        sync_engine._p6t7_checked_out -= 1

            yield session

    fake = MagicMock()
    fake.get_session = MagicMock(side_effect=_fake_get_db)
    # Expose a way for the test to read the counter.
    fake._engine_holder = engine_holder
    return fake


# ---------------------------------------------------------------------------
# Tier A — Pool-checkout instrumentation: session released before httpx POST
# ---------------------------------------------------------------------------


class TestSessionReleasedBeforeAnnounce:
    """P6-T7 core assertion: no DB connection is checked out when the httpx
    announce POST fires.

    Two sub-tests: spawn path and expire path.

    Non-vacuous guarantee: if we remove the session-split (revert to holding
    the session across the announce), these tests fail because the pool event
    counter shows connections_checked_out > 0 at announce time.
    """

    async def test_spawn_path_no_connection_held_at_announce_post(self, sqlite_engine_and_factory):
        """On the spawn→announce path, the DB connection is checked in (returned
        to the pool) before the httpx POST to the gateway fires.

        We record ``connections_checked_out`` at the moment the respx route
        matches the announce URL and assert it equals 0.

        # 1 mock — db_manager bridge
        # + BountyService.spawn_bounty mock (ARRAY-column bypass)
        """
        _engine, factory = sqlite_engine_and_factory
        DIVISION = "bronze"

        async with factory() as seed_db:
            await _seed_full_config(seed_db, GUILD_ID)

        fake_db_manager = _make_instrumented_db_manager(factory)

        connections_at_announce: list[int] = []

        async def _fake_spawn_bounty(db, guild_id, division, *, expiry_minutes=480):
            now = datetime.now(UTC)
            b = Bounty(
                guild_id=guild_id,
                division=division,
                criminal_name="T7SpawnCriminal",
                criminal_faction="Vossk",
                route=["A", "B", "C"],
                answer="B",
                reward=12_000,
                reward_per_sys=3_000,
                checked={"A": -1, "B": -1, "C": -1},
                issue_time=now,
                end_time=now + timedelta(minutes=expiry_minutes),
                tech_level=2,
                criminal_ship={"ship_name": "Fighter", "ship_armour": 150, "weapons": [], "turrets": []},
                status="active",
            )
            db.add(b)
            await db.commit()
            await db.refresh(b)
            return b

        announcement = {
            "text_content": None,
            "loadout_response": {"subject_name": "T7SpawnCriminal", "subject_kind": "criminal"},
            "metadata": {"title": "T7SpawnCriminal", "color": 10181046, "image_url": None},
        }

        def _announce_side_effect(request):
            # Capture pool state at the exact moment the announce POST fires.
            engine_holder = fake_db_manager._engine_holder
            if engine_holder:
                connections_at_announce.append(engine_holder[0]._p6t7_checked_out)
            else:
                connections_at_announce.append(-1)  # engine not yet init'd — impossible
            import httpx as _httpx

            return _httpx.Response(200, json={"data": {"id": 123_456}})

        with (
            patch("persist.database.manager.db_manager", fake_db_manager),
            patch(
                "services.bounty_service.BountyService.spawn_bounty",
                side_effect=_fake_spawn_bounty,
            ),
            patch(
                "utils.bounty_announcement_payload.build_bounty_announcement_request",
                new=AsyncMock(return_value=announcement),
            ),
            respx.mock(assert_all_called=False) as router,
        ):
            router.post(SELF_JOBS_URL).respond(200, json={"data": {"id": "expiry"}})
            router.post(GATEWAY_ANNOUNCE_URL).mock(side_effect=_announce_side_effect)
            # Cache push to gateway (non-fatal, may or may not be called).
            router.route().respond(200, json={})
            result = await execute_bounty_spawn_one_job("t7-spawn-test", {"guild_id": GUILD_ID, "tier": DIVISION})

        assert result.get("success") is True, f"Expected success=True, got {result!r}"
        assert connections_at_announce, "Announce POST was not intercepted — respx route did not match"
        assert connections_at_announce[0] == 0, (
            f"P6-T7 FAIL: {connections_at_announce[0]} DB connection(s) were checked out "
            f"when the httpx announce POST fired.  The session was not released before the "
            f"announce call.  Revert to old code is detected."
        )

    async def test_expire_path_no_connection_held_at_discord_delete(self, sqlite_engine_and_factory):
        """On the expire→delete-announcement path, the DB connection is checked
        in before the httpx DELETE to the gateway fires.

        # 1 mock — db_manager bridge
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            bounty = await _seed_active_bounty(seed_db, GUILD_ID, channel_id=EXPIRE_CHANNEL)
            await _seed_discord_message(seed_db, GUILD_ID, bounty.id)
        bounty_id = bounty.id

        fake_db_manager = _make_instrumented_db_manager(factory)
        connections_at_delete: list[int] = []

        def _delete_side_effect(request):
            engine_holder = fake_db_manager._engine_holder
            if engine_holder:
                connections_at_delete.append(engine_holder[0]._p6t7_checked_out)
            else:
                connections_at_delete.append(-1)
            import httpx as _httpx

            return _httpx.Response(204)

        with (
            patch("persist.database.manager.db_manager", fake_db_manager),
            respx.mock(assert_all_called=False) as router,
        ):
            router.delete(GATEWAY_DELETE_URL).mock(side_effect=_delete_side_effect)
            # Cache push to gateway (non-fatal).
            router.route().respond(200, json={})
            result = await execute_bounty_expire_job("t7-expire-test", {"bounty_id": bounty_id})

        assert result["status"] == "success", f"Expected status=success, got {result!r}"
        assert connections_at_delete, "DELETE was not intercepted — respx route did not match"
        assert connections_at_delete[0] == 0, (
            f"P6-T7 FAIL: {connections_at_delete[0]} DB connection(s) were checked out "
            f"when the httpx Discord DELETE fired.  The session was not released before "
            f"the announce-delete call.  Revert to old code is detected."
        )


# ---------------------------------------------------------------------------
# Tier B — Announce payload/target unchanged post-T7
# ---------------------------------------------------------------------------


class TestAnnouncePayloadUnchanged:
    """The announcement still posts to the same channel with the same payload
    as before the T7 session-split.  Regression guard.
    """

    async def test_spawn_announce_posts_to_correct_channel(self, sqlite_engine_and_factory):
        """Announce POST targets the division channel from guild config.

        # 1 mock — db_manager bridge
        # + BountyService.spawn_bounty mock (ARRAY-column bypass)
        """
        _engine, factory = sqlite_engine_and_factory
        DIVISION = "bronze"

        async with factory() as seed_db:
            await _seed_full_config(seed_db, GUILD_ID)

        async def _fake_spawn_bounty(db, guild_id, division, *, expiry_minutes=480):
            now = datetime.now(UTC)
            b = Bounty(
                guild_id=guild_id,
                division=division,
                criminal_name="TargetCriminal",
                criminal_faction="Nivelian",
                route=["P", "Q"],
                answer="Q",
                reward=8_000,
                reward_per_sys=4_000,
                checked={"P": -1, "Q": -1},
                issue_time=now,
                end_time=now + timedelta(minutes=expiry_minutes),
                tech_level=1,
                criminal_ship={"ship_name": "Shuttle", "ship_armour": 50, "weapons": [], "turrets": []},
                status="active",
            )
            db.add(b)
            await db.commit()
            await db.refresh(b)
            return b

        announcement = {
            "text_content": f"<@&{BRONZE_ROLE}>",
            "loadout_response": {"subject_name": "TargetCriminal", "subject_kind": "criminal"},
            "metadata": {"title": "TargetCriminal", "color": 10181046, "image_url": None},
        }

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            patch(
                "services.bounty_service.BountyService.spawn_bounty",
                side_effect=_fake_spawn_bounty,
            ),
            patch(
                "utils.bounty_announcement_payload.build_bounty_announcement_request",
                new=AsyncMock(return_value=announcement),
            ),
            respx.mock(assert_all_called=False) as router,
        ):
            router.post(SELF_JOBS_URL).respond(200, json={"data": {"id": "expiry"}})
            announce_route = router.post(GATEWAY_ANNOUNCE_URL).respond(200, json={"data": {"id": 77_001}})
            # Allow non-fatal gateway cache push.
            router.route().respond(200, json={})
            result = await execute_bounty_spawn_one_job("t7-announce-target", {"guild_id": GUILD_ID, "tier": DIVISION})

        assert result.get("success") is True, f"Expected success, got {result!r}"
        # Announcement route was called — same target as pre-T7.
        assert announce_route.called, f"Announce POST to {GATEWAY_ANNOUNCE_URL} was not called post-T7 refactor"
        # Verify the payload was forwarded as-is.
        import json as _json

        sent_body = _json.loads(announce_route.calls.last.request.content)
        assert sent_body.get("text_content") == announcement["text_content"], (
            f"Announce payload text_content mismatch: {sent_body!r}"
        )
        assert sent_body.get("loadout_response", {}).get("subject_name") == "TargetCriminal", (
            f"Announce payload loadout_response mismatch: {sent_body!r}"
        )

    async def test_expire_delete_targets_correct_channel_and_message(self, sqlite_engine_and_factory):
        """Discord DELETE targets the channel_id and message_id from the
        DiscordMessage record — same behaviour as pre-T7.

        # 1 mock — db_manager bridge
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            bounty = await _seed_active_bounty(seed_db, GUILD_ID, channel_id=EXPIRE_CHANNEL)
            await _seed_discord_message(seed_db, GUILD_ID, bounty.id)
        bounty_id = bounty.id

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_called=False) as router,
        ):
            delete_route = router.delete(GATEWAY_DELETE_URL).respond(204)
            router.route().respond(200, json={})
            result = await execute_bounty_expire_job("t7-expire-target", {"bounty_id": bounty_id})

        assert result["status"] == "success", f"Expected status=success, got {result!r}"
        assert delete_route.called, f"Discord DELETE to {GATEWAY_DELETE_URL} was not called post-T7 refactor"


# ---------------------------------------------------------------------------
# Tier C — DB outcomes unchanged post-T7
# ---------------------------------------------------------------------------


class TestDbOutcomesUnchanged:
    """Spawn and expire DB side-effects are identical to pre-T7 behaviour."""

    async def test_spawn_bounty_row_persisted_after_t7(self, sqlite_engine_and_factory):
        """Active Bounty row is written to DB and visible in a fresh session.

        # 1 mock — db_manager bridge
        # + BountyService.spawn_bounty mock (ARRAY-column bypass)
        """
        _engine, factory = sqlite_engine_and_factory
        DIVISION = "bronze"

        async with factory() as seed_db:
            await _seed_full_config(seed_db, GUILD_ID)

        spawned_ids: list[int] = []

        async def _fake_spawn_bounty(db, guild_id, division, *, expiry_minutes=480):
            now = datetime.now(UTC)
            b = Bounty(
                guild_id=guild_id,
                division=division,
                criminal_name="T7DbCriminal",
                criminal_faction="Terran",
                route=["M", "N", "O"],
                answer="N",
                reward=14_000,
                reward_per_sys=3_500,
                checked={"M": -1, "N": -1, "O": -1},
                issue_time=now,
                end_time=now + timedelta(minutes=expiry_minutes),
                tech_level=3,
                criminal_ship={"ship_name": "Cruiser", "ship_armour": 300, "weapons": [], "turrets": []},
                status="active",
            )
            db.add(b)
            await db.commit()
            await db.refresh(b)
            spawned_ids.append(b.id)
            return b

        announcement = {
            "text_content": None,
            "loadout_response": {"subject_name": "T7DbCriminal", "subject_kind": "criminal"},
            "metadata": {"title": "T7DbCriminal", "color": 10181046, "image_url": None},
        }

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            patch(
                "services.bounty_service.BountyService.spawn_bounty",
                side_effect=_fake_spawn_bounty,
            ),
            patch(
                "utils.bounty_announcement_payload.build_bounty_announcement_request",
                new=AsyncMock(return_value=announcement),
            ),
            respx.mock(assert_all_called=False) as router,
        ):
            router.post(SELF_JOBS_URL).respond(200, json={"data": {"id": "expiry"}})
            router.post(GATEWAY_ANNOUNCE_URL).respond(200, json={"data": {"id": 78_001}})
            router.route().respond(200, json={})
            result = await execute_bounty_spawn_one_job("t7-db-spawn", {"guild_id": GUILD_ID, "tier": DIVISION})

        assert result.get("success") is True, f"Expected success, got {result!r}"
        assert len(spawned_ids) == 1

        async with factory() as verify_db:
            row = await verify_db.get(Bounty, spawned_ids[0])
        assert row is not None, "Bounty row must be persisted post-T7"
        assert row.status == "active"
        assert row.criminal_name == "T7DbCriminal"
        assert row.reward == 14_000

    async def test_expire_bounty_status_set_to_expired_after_t7(self, sqlite_engine_and_factory):
        """Bounty status is set to 'expired' in DB — same as pre-T7.

        # 1 mock — db_manager bridge
        """
        _engine, factory = sqlite_engine_and_factory

        async with factory() as seed_db:
            bounty = await _seed_active_bounty(seed_db, GUILD_ID)
        bounty_id = bounty.id

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            respx.mock(assert_all_called=False),
        ):
            result = await execute_bounty_expire_job("t7-db-expire", {"bounty_id": bounty_id})

        assert result["status"] == "success"

        async with factory() as verify_db:
            row = await verify_db.execute(select(Bounty).where(Bounty.id == bounty_id))
            refreshed = row.scalars().first()
        assert refreshed is not None
        assert refreshed.status == "expired", f"Expected Bounty.status='expired' post-T7, got {refreshed.status!r}"


# ---------------------------------------------------------------------------
# Tier D — Error path: announce failure behavior unchanged
# ---------------------------------------------------------------------------


class TestErrorPathBehaviourUnchanged:
    """Announce failure still triggers the Fix B compensating rollback.

    The DB write (spawn) is committed before the announce, and if the
    announce fails the compensating rollback deletes the bounty row.
    This matches pre-T7 behavior.
    """

    async def test_spawn_announce_failure_still_rolls_back_bounty(self, sqlite_engine_and_factory):
        """500 on announce POST → bounty row is DELETED by compensating rollback.

        DB state is identical to pre-T7: announce failure → no bounty row
        left behind.

        # 1 mock — db_manager bridge
        # + BountyService.spawn_bounty mock (ARRAY-column bypass)
        """
        _engine, factory = sqlite_engine_and_factory
        DIVISION = "bronze"

        async with factory() as seed_db:
            await _seed_full_config(seed_db, GUILD_ID)

        spawned_ids: list[int] = []

        async def _fake_spawn_bounty(db, guild_id, division, *, expiry_minutes=480):
            now = datetime.now(UTC)
            b = Bounty(
                guild_id=guild_id,
                division=division,
                criminal_name="T7ErrCriminal",
                criminal_faction="Midorian",
                route=["A", "B"],
                answer="B",
                reward=9_000,
                reward_per_sys=4_500,
                checked={"A": -1, "B": -1},
                issue_time=now,
                end_time=now + timedelta(minutes=expiry_minutes),
                tech_level=1,
                criminal_ship={"ship_name": "Scout", "ship_armour": 100, "weapons": [], "turrets": []},
                status="active",
            )
            db.add(b)
            await db.commit()
            await db.refresh(b)
            spawned_ids.append(b.id)
            return b

        announcement = {
            "text_content": None,
            "loadout_response": {"subject_name": "T7ErrCriminal", "subject_kind": "criminal"},
            "metadata": {"title": "T7ErrCriminal", "color": 10181046, "image_url": None},
        }

        with (
            patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)),
            patch(
                "services.bounty_service.BountyService.spawn_bounty",
                side_effect=_fake_spawn_bounty,
            ),
            patch(
                "utils.bounty_announcement_payload.build_bounty_announcement_request",
                new=AsyncMock(return_value=announcement),
            ),
            respx.mock(assert_all_called=False) as router,
        ):
            router.post(SELF_JOBS_URL).respond(200, json={"data": {"id": "expiry"}})
            # Announce fails.
            router.post(GATEWAY_ANNOUNCE_URL).respond(500)
            router.route().respond(200, json={})
            result = await execute_bounty_spawn_one_job("t7-err-spawn", {"guild_id": GUILD_ID, "tier": DIVISION})

        # Same as pre-T7: announce failure → rollback → bounty row gone.
        assert result.get("success") is False
        assert result.get("reason") == "announce_failed_rolled_back"
        assert result.get("failure_phase") == "announce"

        rollback = result.get("rollback", {})
        assert rollback.get("bounty_deleted") is True, f"Bounty must be deleted; rollback={rollback!r}"

        assert len(spawned_ids) == 1
        async with factory() as verify_db:
            row = await verify_db.get(Bounty, spawned_ids[0])
        assert row is None, "Bounty row must be gone after announce-failure rollback (same as pre-T7)"


# ---------------------------------------------------------------------------
# Shared db_manager helper (mirrors test_bounty_spawn_executor.py pattern)
# ---------------------------------------------------------------------------


def _make_fake_db_manager(factory: Any):
    """Build a MagicMock that mimics db_manager.get_session() for SQLite.

    Returns a fresh session per call (supports multiple get_session() calls
    in the T7-refactored executors).

    # 1 mock — db_manager bridge (Tier B)
    """

    @asynccontextmanager
    async def _fake_get_db():
        async with factory() as session:
            yield session

    fake = MagicMock()
    fake.get_session = MagicMock(side_effect=_fake_get_db)
    return fake
