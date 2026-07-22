"""Unit tests for DatabaseManager and TableNames.

shared.bblogger is mocked via conftest.py.  These tests exercise:

DatabaseManager
  - Starts in an uninitialised state (_engine is None)
  - get_health_info() returns "not_initialized" when engine is absent
  - get_health_info() returns healthy info when engine is mocked
  - get_connection() raises RuntimeError when not initialised
  - get_session() raises RuntimeError when not initialised
  - shutdown() clears internal state

TableNames
  - Every member value is a non-empty string
  - Specific expected names are present
"""

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure shared.bblogger is mocked BEFORE any src import
# (conftest.py does this project-wide; guard here for safety)
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from persist.database.manager import DatabaseManager
from persist.database.tablenames import TableNames
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import AsyncAdaptedQueuePool, StaticPool

# ---------------------------------------------------------------------------
# TRUEUP-05: real in-memory SQLite engine helper.
#
# A bare "sqlite+aiosqlite:///:memory:" URL defaults to SQLAlchemy's
# StaticPool (a single persistent connection — needed because a fresh
# connection to ":memory:" is a fresh, empty database). StaticPool does NOT
# implement the QueuePool introspection API (.size()/.checkedin()/
# .checkedout()/.overflow()/.status()) that DatabaseManager.get_health_info()
# calls on self._engine.pool. Passing poolclass=AsyncAdaptedQueuePool gives a
# real Pool object with that full API, matching what the production asyncpg
# engine uses. Tests that need cross-connection persistence within a single
# in-memory DB (the AC-7 auto-commit tests) use StaticPool instead — see
# _real_sqlite_engine_shared() below.
# ---------------------------------------------------------------------------


def _real_sqlite_engine(pool_size: int = 5, max_overflow: int = 5):
    """Real async SQLite engine with a real QueuePool-family pool object."""
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=AsyncAdaptedQueuePool,
        pool_size=pool_size,
        max_overflow=max_overflow,
    )


def _real_sqlite_engine_shared():
    """Real async SQLite engine backed by a single shared connection.

    StaticPool keeps one physical connection alive for the engine's lifetime,
    so data written via one checkout (one `get_session()`/`get_connection()`
    call) is visible to a later checkout against the *same* engine — needed
    to prove auto-commit/rollback actually persisted (or didn't), not just
    that the mock method was called.
    """
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


# ===========================================================================
# DatabaseManager tests
# ===========================================================================


class TestDatabaseManagerInitialState:
    """DatabaseManager must start uninitialised."""

    def test_engine_is_none_before_initialize(self):
        """_engine must be None before initialize() is called."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()
        assert mgr._engine is None

    def test_session_factory_is_none_before_initialize(self):
        """_session_factory must be None before initialize() is called."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()
        assert mgr._session_factory is None

    def test_connection_string_is_set_from_defaults(self):
        """_connection_string must be built from env defaults on construction."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()
        # Default host / db come from _load_config
        assert mgr._connection_string is not None
        assert "postgresql+asyncpg://" in mgr._connection_string

    def test_engine_property_returns_none_before_init(self):
        """engine property must return None before initialization."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()
        assert mgr.engine is None

    def test_metadata_property_is_available(self):
        """metadata property must be a MetaData-like object (not None)."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()
        assert mgr.metadata is not None


class TestDatabaseManagerGetHealthInfo:
    """Tests for DatabaseManager.get_health_info()."""

    @pytest.mark.asyncio
    async def test_get_health_info_not_initialized(self):
        """When engine is None, status must be 'not_initialized'."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        info = await mgr.get_health_info()

        assert info["status"] == "not_initialized"
        assert info["connectivity"] is False

    @pytest.mark.asyncio
    async def test_get_health_info_healthy_when_engine_present(self):
        """When a functioning engine is present, status must be 'healthy'.

        TRUEUP-05: real AsyncEngine + real QueuePool object — get_health_info()'s
        pool.size()/.checkedin()/.checkedout()/.overflow()/.status() calls and its
        `SELECT 1` connectivity probe all run against genuine SQLAlchemy/aiosqlite
        code paths, not a hand-rolled stand-in for the Pool API.
        """
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        mgr._engine = _real_sqlite_engine(pool_size=10, max_overflow=0)
        try:
            info = await mgr.get_health_info()

            assert info["status"] == "healthy"
            assert info["connectivity"] is True
            assert info["error"] is None
            pool_stats = info["connection_pool"]
            assert pool_stats["size"] == 10
            assert isinstance(pool_stats["checked_in"], int)
            assert isinstance(pool_stats["checked_out"], int)
            assert isinstance(pool_stats["overflow"], int)
            assert "Pool size" in pool_stats["status"]
        finally:
            await mgr._engine.dispose()

    @pytest.mark.asyncio
    async def test_get_health_info_unhealthy_on_exception(self):
        """When the connection raises, status must be 'unhealthy'.

        TRUEUP-05: real engine pointed at an unreachable SQLite file path (a
        directory that does not exist) — aiosqlite genuinely fails to open the
        database file and raises a real `sqlalchemy.exc.OperationalError`
        ("unable to open database file"), exercising the except-branch for real
        instead of an injected generic Exception.
        """
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        mgr._engine = create_async_engine(
            "sqlite+aiosqlite:////nonexistent_dir_trueup05/does_not_exist.db",
            poolclass=AsyncAdaptedQueuePool,
        )

        info = await mgr.get_health_info()

        assert info["status"] == "unhealthy"
        assert info["connectivity"] is False
        assert info["error"] is not None
        assert "unable to open database file" in info["error"]


class TestDatabaseManagerGetConnection:
    """Tests for DatabaseManager.get_connection()."""

    @pytest.mark.asyncio
    async def test_get_connection_raises_when_not_initialized(self):
        """get_connection() must raise RuntimeError when engine is None."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        with pytest.raises(RuntimeError, match="not initialized"):
            async with mgr.get_connection():
                pass

    @pytest.mark.asyncio
    async def test_get_connection_yields_connection_when_initialized(self):
        """get_connection() must yield a real, usable connection from engine.connect().

        TRUEUP-05: real engine — asserts on the real AsyncConnection type and
        runs a genuine query through the yielded connection, rather than
        asserting identity against a hand-built AsyncMock.
        """
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        mgr._engine = _real_sqlite_engine()
        try:
            received = []
            async with mgr.get_connection() as conn:
                received.append(conn)
                result = await conn.execute(text("SELECT 1 as n"))
                assert result.scalar() == 1

            assert isinstance(received[0], AsyncConnection)
        finally:
            await mgr._engine.dispose()


class TestDatabaseManagerGetSession:
    """Tests for DatabaseManager.get_session()."""

    @pytest.mark.asyncio
    async def test_get_session_raises_when_not_initialized(self):
        """get_session() must raise RuntimeError when session_factory is None."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        with pytest.raises(RuntimeError, match="not initialized"):
            async with mgr.get_session():
                pass

    @pytest.mark.asyncio
    async def test_get_session_yields_real_session_when_initialized(self):
        """get_session() must yield a real, usable AsyncSession from the session factory.

        TRUEUP-05: real engine + real sessionmaker(class_=AsyncSession) — same
        factory construction the production initialize() uses — rather than a
        MagicMock session_factory standing in for it.
        """
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        engine = _real_sqlite_engine()
        mgr._engine = engine
        mgr._session_factory = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        try:
            received = []
            async with mgr.get_session() as session:
                received.append(session)
                result = await session.execute(text("SELECT 1 as n"))
                assert result.scalar() == 1

            assert isinstance(received[0], AsyncSession)
        finally:
            await engine.dispose()


class TestDatabaseManagerShutdown:
    """Tests for DatabaseManager.shutdown()."""

    @pytest.mark.asyncio
    async def test_shutdown_clears_engine(self):
        """shutdown() must set _engine to None.

        TRUEUP-05: real engine, not a MagicMock with a bolted-on `.sync_engine`.
        """
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        mgr._engine = _real_sqlite_engine()

        await mgr.shutdown()

        assert mgr._engine is None

    @pytest.mark.asyncio
    async def test_shutdown_clears_session_factory(self):
        """shutdown() must set _session_factory to None."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        engine = _real_sqlite_engine()
        mgr._engine = engine
        mgr._session_factory = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

        await mgr.shutdown()

        assert mgr._session_factory is None

    @pytest.mark.asyncio
    async def test_shutdown_calls_dispose(self):
        """shutdown() must dispose the sync engine pool.

        TRUEUP-05: real engine with its real `.sync_engine.dispose` bound
        method wrapped (not replaced) via `wraps=` — the assertion proves
        shutdown() actually invokes dispose(), while dispose() itself still
        runs its genuine implementation (no accept-anything stand-in).
        """
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        engine = _real_sqlite_engine()
        mgr._engine = engine

        with patch.object(engine.sync_engine, "dispose", wraps=engine.sync_engine.dispose) as dispose_spy:
            await mgr.shutdown()

        dispose_spy.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_when_not_initialized_does_not_raise(self):
        """Calling shutdown() before initialize() must not raise."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        # Should complete without error even though _engine is None
        await mgr.shutdown()


# ===========================================================================
# TableNames tests
# ===========================================================================


class TestTableNames:
    """Tests for the TableNames enum."""

    def test_all_values_are_strings(self):
        """Every TableNames member must have a str value."""
        for member in TableNames:
            assert isinstance(member.value, str), f"{member.name} value is not a str"

    def test_all_values_are_non_empty(self):
        """Every TableNames value must be a non-empty string."""
        for member in TableNames:
            assert member.value.strip(), f"{member.name} has an empty value"

    def test_players_table_name(self):
        """TableNames.Players must equal 'players'."""
        assert TableNames.Players.value == "players"

    def test_users_table_name(self):
        """TableNames.Users must equal 'users'."""
        assert TableNames.Users.value == "users"

    def test_criminal_table_name(self):
        """TableNames.Criminal must equal 'criminal'."""
        assert TableNames.Criminal.value == "criminal"

    def test_guild_configs_table_name(self):
        """TableNames.GuildConfigs must equal 'guild_configs'."""
        assert TableNames.GuildConfigs.value == "guild_configs"

    def test_guild_shops_table_name(self):
        """TableNames.GuildShops must equal 'guild_shops'."""
        assert TableNames.GuildShops.value == "guild_shops"

    def test_ship_table_name(self):
        """TableNames.Ship must equal 'ship'."""
        assert TableNames.Ship.value == "ship"

    def test_module_table_name(self):
        """TableNames.Module must equal 'module'."""
        assert TableNames.Module.value == "module"

    def test_primary_weapon_table_name(self):
        """TableNames.PrimaryWeapon must equal 'primary_weapon'."""
        assert TableNames.PrimaryWeapon.value == "primary_weapon"

    def test_secondary_weapon_table_name(self):
        """TableNames.SecondaryWeapon must equal 'secondary_weapon'."""
        assert TableNames.SecondaryWeapon.value == "secondary_weapon"

    def test_turret_weapon_table_name(self):
        """TableNames.TurretWeapon must equal 'turret_weapon'."""
        assert TableNames.TurretWeapon.value == "turret_weapon"

    def test_schema_version_table_name(self):
        """TableNames.SchemaVersion must equal 'schema'."""
        assert TableNames.SchemaVersion.value == "schema"

    def test_player_inventories_table_name(self):
        """TableNames.PlayerInventories must equal 'player_inventories'."""
        assert TableNames.PlayerInventories.value == "player_inventories"

    def test_player_ships_table_name(self):
        """TableNames.PlayerShips must equal 'player_ships'."""
        assert TableNames.PlayerShips.value == "player_ships"

    def test_discord_message_table_name(self):
        """TableNames.DiscordMessage must equal 'discord_message'."""
        assert TableNames.DiscordMessage.value == "discord_message"

    def test_system_table_name(self):
        """TableNames.System must equal 'system'."""
        assert TableNames.System.value == "system"

    def test_item_table_name(self):
        """TableNames.Item must equal 'item'."""
        assert TableNames.Item.value == "item"

    def test_weapon_table_name(self):
        """TableNames.Weapon must equal 'weapon'."""
        assert TableNames.Weapon.value == "weapon"

    def test_enum_membership(self):
        """All expected names must exist as enum members."""
        expected_names = {
            "Bounty",
            "Commodity",
            "CombatLog",
            "Criminal",
            "DiscordMessage",
            "DuelRequest",
            "GuildConfigs",
            "GuildShops",
            "Item",
            "Module",
            "PlayerInventories",
            "PlayerShips",
            "Players",
            "PrimaryWeapon",
            "SecondaryWeapon",
            "SchemaVersion",
            "Ship",
            "System",
            "TurretWeapon",
            "Users",
            "Weapon",
        }
        actual_names = {m.name for m in TableNames}
        assert expected_names == actual_names

    def test_values_are_lowercase_snake_case(self):
        """Table name values must be lowercase (snake_case) strings."""
        for member in TableNames:
            assert member.value == member.value.lower(), f"{member.name}.value='{member.value}' is not lowercase"


# ===========================================================================
# Additional DatabaseManager coverage tests
# ===========================================================================

from persist.database.manager import (
    execute_sql as module_execute_sql,
)
from persist.database.manager import (
    get_db_connection,
    get_db_session,
)
from persist.database.manager import (
    table_exists as module_table_exists,
)
from sqlalchemy.exc import OperationalError, SQLAlchemyError


class TestDatabaseManagerInitialize:
    """Tests for initialize() – uncovered paths."""

    @pytest.mark.asyncio
    async def test_initialize_already_initialized_returns_early(self):
        """If _engine is already set, initialize() should return immediately.

        TRUEUP-05: real engine standing in for "already initialized" — the
        strengthened assertion (`is preexisting_engine`, not just `is not
        None`) proves initialize() truly took the early-return branch and
        did not replace it with a freshly created engine.
        """
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()
        preexisting_engine = _real_sqlite_engine()
        mgr._engine = preexisting_engine  # pretend already initialized
        try:
            # Should not raise, should not create a new engine
            await mgr.initialize()
            assert mgr._engine is preexisting_engine
        finally:
            await preexisting_engine.dispose()

    @pytest.mark.asyncio
    async def test_initialize_failure_raises(self):
        """If create_async_engine fails, initialize() should raise.

        TRUEUP-05: a genuinely invalid SQLAlchemy dialect+driver string
        ("sqlite+doesnotexist") makes the real `create_async_engine()` raise
        a real `NoSuchModuleError` at call time (dialect resolution happens
        eagerly, before any I/O) — no need to mock the function itself to
        force a failure.
        """
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()
        mgr._connection_string = "sqlite+doesnotexist:///:memory:"

        with pytest.raises(Exception, match="Can't load plugin"):
            await mgr.initialize()


class TestDatabaseManagerTestConnection:
    """Tests for _test_connection() retry logic – lines 104-128.

    TRUEUP-05 scope note: the happy path below now runs against a real engine.
    The three retry/backoff tests that follow are deliberately LEFT on
    constructed AsyncMock connections — per the worker brief's carve-out
    ("Keep tests that genuinely target ... logic as-is if a live engine can't
    express them — justify in a comment"), there is no way to make a real
    SQLite (or any real DB) connection fail transiently exactly N times then
    recover on a controlled schedule; that requires deterministic failure
    injection a live database cannot provide without infrastructure far
    beyond a unit test's scope (e.g. killing/restarting a real Postgres mid-
    test). The existing mocks are already "complete and true to the entity
    they represent" (house rule 2): they model the real `async with
    engine.connect() as conn: await conn.execute(...)` shape faithfully,
    including realistic `OperationalError` instances, and exist purely to
    control retry *timing/count*, not to fake a live-object contract.
    """

    @pytest.mark.asyncio
    async def test_test_connection_success_first_attempt(self):
        """Connection succeeds on first attempt.

        TRUEUP-05: real engine — no mocks at all needed for the pure success path.
        """
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        mgr._engine = _real_sqlite_engine()
        try:
            await mgr._test_connection()
        finally:
            await mgr._engine.dispose()

    @pytest.mark.asyncio
    async def test_test_connection_retries_on_operational_error(self):
        """On OperationalError, retries with backoff, then succeeds."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        mock_result = MagicMock()
        mock_result.scalar.return_value = 1

        call_count = 0

        async def _mock_execute(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise OperationalError("conn refused", {}, Exception())
            return mock_result

        mock_conn = AsyncMock()
        mock_conn.execute = _mock_execute
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.connect = MagicMock(return_value=mock_conn)
        mgr._engine = mock_engine

        mock_sleep = AsyncMock()
        with patch("persist.database.manager.asyncio.sleep", mock_sleep):
            await mgr._test_connection()

        assert call_count == 3
        # Two OperationalErrors → two sleeps with exponential backoff (2s, 4s)
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(2)
        mock_sleep.assert_any_call(4)

    @pytest.mark.asyncio
    async def test_test_connection_fails_after_max_retries(self):
        """After max_retries OperationalErrors, should raise."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=OperationalError("conn refused", {}, Exception()))
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.connect = MagicMock(return_value=mock_conn)
        mgr._engine = mock_engine

        mock_sleep = AsyncMock()
        with patch("persist.database.manager.asyncio.sleep", mock_sleep), pytest.raises(OperationalError):
            await mgr._test_connection()
        # max_retries=5 → 4 sleeps before the 5th attempt raises
        assert mock_sleep.call_count == 4

    @pytest.mark.asyncio
    async def test_test_connection_unexpected_error_raises_immediately(self):
        """Non-OperationalError raises immediately without retry."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=RuntimeError("unexpected"))
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.connect = MagicMock(return_value=mock_conn)
        mgr._engine = mock_engine

        with pytest.raises(RuntimeError, match="unexpected"):
            await mgr._test_connection()


class TestDatabaseManagerSessionErrorHandling:
    """Tests for get_session() error rollback – lines 154-160."""

    @pytest.mark.asyncio
    async def test_get_session_rolls_back_on_error(self):
        """If an exception occurs inside the session context, rollback is called.

        TRUEUP-05: real engine (StaticPool, single shared connection) + real
        sessionmaker. A real INSERT is issued, then the block raises; the
        instrumented (wraps=) real `rollback()` is asserted called, and the
        shared-connection engine lets us prove the row was genuinely NOT
        persisted afterwards — a stronger, end-to-end check than a call-count
        assertion on a fully synthetic AsyncMock session.
        """
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        engine = _real_sqlite_engine_shared()
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE t (id INTEGER)"))

        real_factory = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        created: list[AsyncSession] = []

        def factory():
            session = real_factory()
            session.rollback = AsyncMock(wraps=session.rollback)
            created.append(session)
            return session

        mgr._session_factory = factory
        try:
            with pytest.raises(ValueError, match="test error"):
                async with mgr.get_session() as db:
                    await db.execute(text("INSERT INTO t VALUES (1)"))
                    raise ValueError("test error")

            created[0].rollback.assert_awaited_once()

            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT COUNT(*) FROM t"))
                assert result.scalar() == 0, "rolled-back INSERT must not be persisted"
        finally:
            await engine.dispose()


class TestDatabaseManagerSessionAutoCommit:
    """AC-7 tests: get_session() auto-commits on clean exit when a transaction is active.

    TRUEUP-05: all three tests use a real engine + real sessionmaker(class_=
    AsyncSession) rather than a hand-built AsyncMock session. `commit`/
    `rollback` are wrapped (not replaced) via `wraps=` on the real bound
    method, so the interaction-count assertions (house rule: session hand-out
    should run against the real engine where feasible) still run the genuine
    SQLAlchemy commit/rollback implementation — persistence is verified
    end-to-end via a shared-connection (StaticPool) engine, not just asserted
    by mock call count.
    """

    @staticmethod
    def _spied_session_factory(engine, created: list[AsyncSession]):
        real_factory = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

        def factory():
            session = real_factory()
            session.commit = AsyncMock(wraps=session.commit)
            session.rollback = AsyncMock(wraps=session.rollback)
            created.append(session)
            return session

        return factory

    @pytest.mark.asyncio
    async def test_get_session_auto_commits_on_clean_exit_when_in_transaction(self):
        """If session.in_transaction() is True at clean exit, auto-commit fires."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        engine = _real_sqlite_engine_shared()
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE t (id INTEGER)"))

        created: list[AsyncSession] = []
        mgr._session_factory = self._spied_session_factory(engine, created)
        try:
            async with mgr.get_session() as db:
                # Caller does not commit explicitly; AC-7 should auto-commit.
                assert db is created[0]
                await db.execute(text("INSERT INTO t VALUES (1)"))

            created[0].commit.assert_awaited_once()
            created[0].rollback.assert_not_awaited()

            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT COUNT(*) FROM t"))
                assert result.scalar() == 1, "auto-committed INSERT must be persisted"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_get_session_does_not_double_commit_when_not_in_transaction(self):
        """If caller already committed (in_transaction()==False), AC-7 must not commit again."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        engine = _real_sqlite_engine_shared()
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE t (id INTEGER)"))

        created: list[AsyncSession] = []
        mgr._session_factory = self._spied_session_factory(engine, created)
        try:
            async with mgr.get_session() as db:
                assert db is created[0]
                await db.execute(text("INSERT INTO t VALUES (1)"))
                await db.commit()  # caller commits explicitly; in_transaction() is False after this

            # In-transaction is False (caller committed) → no auto-commit
            created[0].commit.assert_awaited_once()  # exactly the caller's explicit commit, not a second one
            created[0].rollback.assert_not_awaited()

            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT COUNT(*) FROM t"))
                assert result.scalar() == 1, "row must be committed exactly once, not duplicated"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_get_session_rolls_back_when_auto_commit_fails(self):
        """If the auto-commit itself fails, rollback is attempted before re-raising.

        A real commit cannot be made to fail on demand without corrupting the
        engine, so `commit` here is replaced (not wrapped) with a raising
        AsyncMock — the one interaction in this class that genuinely needs a
        synthetic failure, per house rule 3 (justified, single mock).
        `rollback` remains real (wraps=) so the recovery path itself is proven
        against genuine SQLAlchemy/aiosqlite behaviour.
        """
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        engine = _real_sqlite_engine_shared()
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE t (id INTEGER)"))

        real_factory = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        created: list[AsyncSession] = []

        def factory():
            session = real_factory()
            session.commit = AsyncMock(side_effect=RuntimeError("commit fail"))
            session.rollback = AsyncMock(wraps=session.rollback)
            created.append(session)
            return session

        mgr._session_factory = factory
        try:
            with pytest.raises(RuntimeError, match="commit fail"):
                async with mgr.get_session() as db:
                    await db.execute(text("INSERT INTO t VALUES (1)"))

            created[0].commit.assert_awaited_once()
            created[0].rollback.assert_awaited_once()
        finally:
            await engine.dispose()


class TestDatabaseManagerExecuteSql:
    """Tests for execute_sql() error path – lines 171-178."""

    @pytest.mark.asyncio
    async def test_execute_sql_sqlalchemy_error(self):
        """SQLAlchemyError is caught, logged, and re-raised.

        TRUEUP-05: real engine + genuinely invalid SQL ("no such table") —
        aiosqlite/SQLAlchemy raise a real `OperationalError` (a `SQLAlchemyError`
        subclass), so the except-branch in execute_sql() is exercised for real.
        """
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        mgr._engine = _real_sqlite_engine()
        try:
            with pytest.raises(SQLAlchemyError, match="no such table"):
                await mgr.execute_sql("SELECT * FROM nonexistent_table_xyz")
        finally:
            await mgr._engine.dispose()

    @pytest.mark.asyncio
    async def test_execute_sql_success_returns_result(self):
        """Happy path: execute_sql() returns the real Result for valid SQL.

        Added for TRUEUP-05 — the pre-existing suite only covered the error
        path for this method; a live engine makes the success path cheap to
        cover too.
        """
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        mgr._engine = _real_sqlite_engine()
        try:
            result = await mgr.execute_sql("SELECT 1 as n")
            assert result.scalar() == 1
        finally:
            await mgr._engine.dispose()


class TestDatabaseManagerTableExists:
    """Tests for table_exists() – both result values and the error path.

    History (TRUEUP-P5, FIXED): swapping the mocked `inspect()`/engine in this
    class for a REAL async engine surfaced a genuine production bug — the old
    code inspected `self._engine.sync_engine` directly from `async def` with
    no `conn.run_sync(...)` bridge, so it always raised `MissingGreenlet`,
    which `except SQLAlchemyError` swallowed into an UNCONDITIONAL `False`
    (verified against real aiosqlite and asyncpg engines — see
    TEST_SUITE_TRUEUP_FOLLOWUPS.md section R-bc-db-manager). The method now
    runs the inspection through the async connection's greenlet bridge and
    these tests assert the real true/false results.
    """

    @pytest.mark.asyncio
    async def test_table_exists_engine_none_returns_false(self):
        """When engine is None, table_exists returns False."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        result = await mgr.table_exists("some_table")
        assert result is False

    @pytest.mark.asyncio
    async def test_table_exists_sqlalchemy_error_returns_false(self):
        """When the connection genuinely fails, returns False.

        TRUEUP-05: real failure, not an injected one — an aiosqlite engine
        pointed at a file in a nonexistent directory raises a real
        `OperationalError` (a `SQLAlchemyError`) on connect, exercising the
        except branch for real.
        """
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        mgr._engine = create_async_engine("sqlite+aiosqlite:////nonexistent-dir/no-such/db.sqlite")
        try:
            result = await mgr.table_exists("some_table")
            assert result is False
        finally:
            await mgr._engine.dispose()

    @pytest.mark.asyncio
    async def test_table_exists_true_for_real_table_false_for_missing(self):
        """table_exists() reports the genuine state of a real database.

        History (TRUEUP-P5, fixed): before the run_sync bridge fix this
        returned False even for the just-created table — the demonstration
        test here asserted that wrong result with a pointer to
        R-bc-db-manager. Now asserts the real contract, both directions.
        """
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        engine = _real_sqlite_engine()
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE genuinely_present (id INTEGER)"))
        mgr._engine = engine
        try:
            assert await mgr.table_exists("genuinely_present") is True
            assert await mgr.table_exists("never_created") is False
        finally:
            await engine.dispose()


class TestModuleLevelConvenienceFunctions:
    """Tests for module-level convenience functions – lines 264, 268, 272, 276.

    TRUEUP-05: a real (uninitialized-is-fine) `DatabaseManager` instance
    swapped in for the module-level `db_manager` singleton, with `wraps=`
    spies on the delegated-to method — proves the module function truly
    calls through to the *same* DatabaseManager method and returns its real
    result, rather than asserting against a MagicMock singleton that would
    accept literally any attribute access.
    """

    def test_get_db_connection_returns_context_manager(self):
        """get_db_connection() should delegate to db_manager.get_connection()."""
        with patch("persist.database.manager.bblogger"):
            real_mgr = DatabaseManager()
        with (
            patch("persist.database.manager.db_manager", real_mgr),
            patch.object(real_mgr, "get_connection", wraps=real_mgr.get_connection) as spy,
        ):
            result = get_db_connection()
        spy.assert_called_once()
        # get_connection() is an @asynccontextmanager-wrapped generator function;
        # calling (without entering) it returns the real context-manager object.
        assert hasattr(result, "__aenter__") and hasattr(result, "__aexit__")

    def test_get_db_session_returns_context_manager(self):
        """get_db_session() should delegate to db_manager.get_session()."""
        with patch("persist.database.manager.bblogger"):
            real_mgr = DatabaseManager()
        with (
            patch("persist.database.manager.db_manager", real_mgr),
            patch.object(real_mgr, "get_session", wraps=real_mgr.get_session) as spy,
        ):
            result = get_db_session()
        spy.assert_called_once()
        assert hasattr(result, "__aenter__") and hasattr(result, "__aexit__")

    @pytest.mark.asyncio
    async def test_module_execute_sql_delegates(self):
        """execute_sql() should delegate to db_manager.execute_sql() and return its real Result."""
        with patch("persist.database.manager.bblogger"):
            real_mgr = DatabaseManager()
        real_mgr._engine = _real_sqlite_engine()
        try:
            with (
                patch("persist.database.manager.db_manager", real_mgr),
                patch.object(real_mgr, "execute_sql", wraps=real_mgr.execute_sql) as spy,
            ):
                result = await module_execute_sql("SELECT 1 as n")
            spy.assert_awaited_once_with("SELECT 1 as n", None)
            assert result.scalar() == 1
        finally:
            await real_mgr._engine.dispose()

    @pytest.mark.asyncio
    async def test_module_table_exists_delegates(self):
        """table_exists() should delegate to db_manager.table_exists() and return its real result.

        The table is created for real so the delegated call proves a genuine
        True round-trip (TRUEUP-P5 fixed — previously this could only ever
        observe False).
        """
        with patch("persist.database.manager.bblogger"):
            real_mgr = DatabaseManager()
        real_mgr._engine = _real_sqlite_engine()
        async with real_mgr._engine.begin() as conn:
            await conn.execute(text("CREATE TABLE players (id INTEGER)"))
        try:
            with (
                patch("persist.database.manager.db_manager", real_mgr),
                patch.object(real_mgr, "table_exists", wraps=real_mgr.table_exists) as spy,
            ):
                result = await module_table_exists("players")
            spy.assert_awaited_once_with("players", None)
            assert result is True
        finally:
            await real_mgr._engine.dispose()


# ===========================================================================
# P1-T6: Pool sizing tests
# ===========================================================================


class TestPoolSizingDefaults:
    """P1-T6: pool_size=40 / max_overflow=20 defaults and env overrides."""

    def test_default_pool_size_is_40(self):
        """pool_size must default to 40 when DB_POOL_SIZE is unset."""
        with patch("persist.database.manager.bblogger"), patch.dict(os.environ, {}, clear=False) as env:
            # Remove override if present so we exercise the true default
            env.pop("DB_POOL_SIZE", None)
            mgr = DatabaseManager()
        assert mgr._pool_config["pool_size"] == 40

    def test_default_max_overflow_is_20(self):
        """max_overflow must default to 20 when DB_MAX_OVERFLOW is unset."""
        with patch("persist.database.manager.bblogger"), patch.dict(os.environ, {}, clear=False) as env:
            env.pop("DB_MAX_OVERFLOW", None)
            mgr = DatabaseManager()
        assert mgr._pool_config["max_overflow"] == 20

    def test_env_override_pool_size(self):
        """DB_POOL_SIZE env var overrides the default pool_size."""
        env_overrides = {"DB_POOL_SIZE": "10", "DB_MAX_OVERFLOW": "5"}
        with patch("persist.database.manager.bblogger"), patch.dict(os.environ, env_overrides):
            mgr = DatabaseManager()
        assert mgr._pool_config["pool_size"] == 10

    def test_env_override_max_overflow(self):
        """DB_MAX_OVERFLOW env var overrides the default max_overflow."""
        env_overrides = {"DB_POOL_SIZE": "10", "DB_MAX_OVERFLOW": "5"}
        with patch("persist.database.manager.bblogger"), patch.dict(os.environ, env_overrides):
            mgr = DatabaseManager()
        assert mgr._pool_config["max_overflow"] == 5

    def test_default_total_pool_under_postgres_max_connections(self):
        """Default pool_size + max_overflow must stay safely under Postgres max_connections (100)."""
        with patch("persist.database.manager.bblogger"), patch.dict(os.environ, {}, clear=False) as env:
            env.pop("DB_POOL_SIZE", None)
            env.pop("DB_MAX_OVERFLOW", None)
            mgr = DatabaseManager()
        total = mgr._pool_config["pool_size"] + mgr._pool_config["max_overflow"]
        # Observed Postgres max_connections = 100; require total < 100.
        ceiling = DatabaseManager._POSTGRES_MAX_CONNECTIONS_FLOOR
        assert total < ceiling, f"total pool ({total}) must be < Postgres max_connections ({ceiling})"

    def test_default_total_satisfies_warm_load_formula(self):
        """Default total must satisfy: pool_size + max_overflow >= (2 * 16) + 10 = 42."""
        with patch("persist.database.manager.bblogger"), patch.dict(os.environ, {}, clear=False) as env:
            env.pop("DB_POOL_SIZE", None)
            env.pop("DB_MAX_OVERFLOW", None)
            mgr = DatabaseManager()
        total = mgr._pool_config["pool_size"] + mgr._pool_config["max_overflow"]
        autocomplete_warm_concurrency = 16  # default AUTOCOMPLETE_WARM_CONCURRENCY
        live_headroom = 10
        minimum_required = (2 * autocomplete_warm_concurrency) + live_headroom
        assert total >= minimum_required, (
            f"total pool ({total}) < required minimum ({minimum_required}) "
            f"for warm_concurrency={autocomplete_warm_concurrency} + headroom={live_headroom}"
        )

    def test_ceiling_check_raises_when_total_exceeds_max_connections(self):
        """Startup must raise ValueError when pool total >= Postgres max_connections ceiling."""
        # Set pool_size + max_overflow = 100 (equal to ceiling) → must raise
        with (
            patch("persist.database.manager.bblogger"),
            patch.dict(os.environ, {"DB_POOL_SIZE": "80", "DB_MAX_OVERFLOW": "20"}),
            pytest.raises(ValueError, match="DB pool too large"),
        ):
            DatabaseManager()

    def test_ceiling_check_passes_when_total_is_under_max_connections(self):
        """No ValueError when pool total is safely under max_connections ceiling."""
        env_overrides = {"DB_POOL_SIZE": "40", "DB_MAX_OVERFLOW": "20"}
        with patch("persist.database.manager.bblogger"), patch.dict(os.environ, env_overrides):
            mgr = DatabaseManager()
        assert mgr._pool_config["pool_size"] + mgr._pool_config["max_overflow"] == 60


def _live_db_conn_str() -> str | None:
    """
    Return a connection string to the live dev Postgres, or None if unreachable.

    When tests run on the HOST (outside docker) the DB may be reachable via:
      - localhost:15432  (published port — WSL2 sometimes blocks this)
      - the docker bridge IP on port 5432 (always works on Linux hosts)
    We try each candidate in order and return the first reachable one.
    """
    import socket
    import subprocess

    candidates: list[tuple[str, int]] = [
        ("127.0.0.1", int(os.getenv("HOST_DB_PORT", "15432"))),
    ]
    # Also probe via the docker bridge IP if docker is available
    try:
        fmt = "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"
        result = subprocess.run(
            ["sudo", "docker", "inspect", "bountydev-db", "--format", fmt],
            capture_output=True,
            text=True,
            timeout=5,
        )
        bridge_ip = result.stdout.strip()
        if bridge_ip:
            candidates.append((bridge_ip, 5432))
    except Exception:  # pragma: no cover
        pass

    for host, port in candidates:
        try:
            sock = socket.create_connection((host, port), timeout=1)
            sock.close()
            db_user = os.getenv("POSTGRES_USER", "bounty")
            db_pass = os.getenv("POSTGRES_PASSWORD", "bounty")
            db_name = os.getenv("POSTGRES_DB", "bountydb")
            return f"postgresql+asyncpg://{db_user}:{db_pass}@{host}:{port}/{db_name}"
        except OSError:
            continue
    return None  # DB not reachable from host


class TestPoolConcurrencyLive:
    """P1-T6: real concurrency checkout against live bountydev-db.

    Design: each task uses ``engine.connect()`` (not AsyncSession), which
    immediately checks out a pool connection and holds it for the duration of
    the ``async with`` block.  An ``asyncio.sleep(hold_seconds)`` inside the
    block ensures ALL tasks are suspended while holding their connection so
    that pool pressure is genuinely concurrent, not sequential.

    Anti-vacuousness proof: a separate test drives the same harness against a
    pool_size=5 / max_overflow=5 (total 10) engine with 32 held-concurrent
    checkouts and asserts it raises ``sqlalchemy.exc.TimeoutError`` ("QueuePool
    limit … reached").  The positive test (40/20) must then serve all 32 with
    no errors — proving the 40/20 pool genuinely absorbs 32 concurrent holders.
    """

    @pytest.mark.asyncio
    async def test_small_pool_exhausted_by_32_held_concurrent(self):
        """ANTI-VACUOUSNESS: pool_size=5/max_overflow=5 must raise TimeoutError with 32 held concurrent checkouts."""
        import asyncio

        from sqlalchemy import text
        from sqlalchemy.exc import TimeoutError as SQLATimeoutError
        from sqlalchemy.ext.asyncio import create_async_engine

        conn_str = _live_db_conn_str()
        if conn_str is None:
            pytest.skip("Live DB not reachable from host")

        # Total pool capacity = 10; 32 tasks will exhaust it with a short hold.
        engine = create_async_engine(
            conn_str,
            pool_size=5,
            max_overflow=5,
            pool_timeout=1,  # fail fast when pool is full
        )

        async def _hold_connection(i: int):
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT 1 as n"))
                val = result.scalar()
                # Hold the connection so all 32 are open simultaneously.
                await asyncio.sleep(2)
                return val

        concurrency = 32
        tasks = [_hold_connection(i) for i in range(concurrency)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        await engine.dispose()

        errors = [r for r in results if isinstance(r, Exception)]
        timeout_errors = [e for e in errors if isinstance(e, SQLATimeoutError)]
        assert timeout_errors, (
            "Expected SQLAlchemy TimeoutError (QueuePool limit) when 32 tasks hold "
            f"connections simultaneously against a pool_size=5/max_overflow=5 engine, "
            f"but got errors={errors}, successes={[r for r in results if r == 1]}"
        )

    @pytest.mark.asyncio
    async def test_32_concurrent_checkouts_no_timeout(self):
        """pool_size=40/max_overflow=20 must serve all 32 held-concurrent checkouts with no errors.

        Each task holds its connection for 0.2 s while suspended so that all 32
        are genuinely in-flight simultaneously.  The 40/20 pool (60 total) has
        ample headroom; no TimeoutError should occur.
        """
        import asyncio

        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        conn_str = _live_db_conn_str()
        if conn_str is None:
            pytest.skip("Live DB not reachable from host")

        engine = create_async_engine(
            conn_str,
            pool_size=40,
            max_overflow=20,
            pool_timeout=10,
        )

        async def _hold_connection(i: int):
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT 1 as n"))
                val = result.scalar()
                # Hold connection while suspended so all 32 are open at once.
                await asyncio.sleep(0.2)
                return val

        concurrency = 32
        tasks = [_hold_connection(i) for i in range(concurrency)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        await engine.dispose()

        errors = [r for r in results if isinstance(r, Exception)]
        assert not errors, f"Pool exhaustion or errors with 40/20 pool: {errors}"
        assert all(r == 1 for r in results), f"Unexpected query results: {results}"


class TestPoolWarnAtCeiling:
    """P1-T6 SHOULD FIX: warning fires when pool total exceeds 75% of Postgres max_connections."""

    def test_warn_logged_when_pool_approaches_ceiling(self):
        """pool_size=60 + max_overflow=20 = 80 > 75% of 100 → flogger.warning must be called."""
        with (
            patch("persist.database.manager.flogger") as mock_flogger,
            patch.dict(os.environ, {"DB_POOL_SIZE": "60", "DB_MAX_OVERFLOW": "20"}),
        ):
            DatabaseManager()

        mock_flogger.warning.assert_called_once()
        warning_msg = mock_flogger.warning.call_args[0][0]
        assert "approaching" in warning_msg, f"Expected 'approaching' in warning: {warning_msg!r}"
        assert "ceiling" in warning_msg, f"Expected 'ceiling' in warning: {warning_msg!r}"

    def test_no_warn_when_pool_is_under_75_percent(self):
        """pool_size=40 + max_overflow=20 = 60 ≤ 75% of 100 → flogger.warning must NOT be called."""
        with (
            patch("persist.database.manager.flogger") as mock_flogger,
            patch.dict(os.environ, {"DB_POOL_SIZE": "40", "DB_MAX_OVERFLOW": "20"}),
        ):
            DatabaseManager()

        mock_flogger.warning.assert_not_called()


def _live_db_env_overrides() -> dict[str, str] | None:
    """
    Return env-var overrides that point POSTGRES_* at the live bountydev-db,
    or None if the DB is not reachable from the current host.

    Uses the same discovery logic as _live_db_conn_str() but returns individual
    env vars so DatabaseManager._load_config() picks them up correctly.
    """
    import socket
    import subprocess

    candidates: list[tuple[str, int]] = [
        ("127.0.0.1", int(os.getenv("HOST_DB_PORT", "15432"))),
    ]
    try:
        fmt = "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"
        result = subprocess.run(
            ["sudo", "docker", "inspect", "bountydev-db", "--format", fmt],
            capture_output=True,
            text=True,
            timeout=5,
        )
        bridge_ip = result.stdout.strip()
        if bridge_ip:
            candidates.append((bridge_ip, 5432))
    except Exception:  # pragma: no cover
        pass

    for host, port in candidates:
        try:
            sock = socket.create_connection((host, port), timeout=1)
            sock.close()
            return {
                "POSTGRES_HOST": host,
                "POSTGRES_PORT": str(port),
                "POSTGRES_USER": os.getenv("POSTGRES_USER", "bounty"),
                "POSTGRES_PASSWORD": os.getenv("POSTGRES_PASSWORD", "bounty"),
                "POSTGRES_DB": os.getenv("POSTGRES_DB", "bountydb"),
            }
        except OSError:
            continue
    return None


class TestPoolEngineObjectLive:
    """P1-T6 NICE-TO-HAVE: verify pool config values reach the actual QueuePool object after initialize()."""

    @pytest.mark.asyncio
    async def test_engine_pool_size_and_max_overflow_after_initialize(self):
        """engine.pool.size() == 40 and pool._max_overflow == 20 after initialize()."""
        db_env = _live_db_env_overrides()
        if db_env is None:
            pytest.skip("Live DB not reachable from host")

        env_overrides = {**db_env, "DB_POOL_SIZE": "40", "DB_MAX_OVERFLOW": "20"}

        with patch("persist.database.manager.bblogger"), patch.dict(os.environ, env_overrides):
            mgr = DatabaseManager()
            await mgr.initialize()

        pool = mgr.engine.pool
        pool_size = pool.size()
        max_overflow = pool._max_overflow

        await mgr.shutdown()

        assert pool_size == 40, f"Expected pool.size()==40, got {pool_size}"
        assert max_overflow == 20, f"Expected pool._max_overflow==20, got {max_overflow}"
