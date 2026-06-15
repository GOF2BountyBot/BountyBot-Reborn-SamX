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
        """When a functioning engine is present, status must be 'healthy'."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        # Build a mock engine with a pool that answers all pool stat calls
        mock_pool = MagicMock()
        mock_pool.size = MagicMock(return_value=10)
        mock_pool.checkedin = MagicMock(return_value=8)
        mock_pool.checkedout = MagicMock(return_value=2)
        mock_pool.overflow = MagicMock(return_value=0)
        mock_pool.status = MagicMock(return_value="Pool size: 10  Connections in pool: 8")

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.pool = mock_pool
        mock_engine.connect = MagicMock(return_value=mock_conn)

        mgr._engine = mock_engine

        info = await mgr.get_health_info()

        assert info["status"] == "healthy"
        assert info["connectivity"] is True

    @pytest.mark.asyncio
    async def test_get_health_info_unhealthy_on_exception(self):
        """When the connection raises, status must be 'unhealthy'."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=Exception("connection refused"))
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.connect = MagicMock(return_value=mock_conn)
        mgr._engine = mock_engine

        info = await mgr.get_health_info()

        assert info["status"] == "unhealthy"
        assert info["connectivity"] is False


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
        """get_connection() must yield the connection from engine.connect()."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.connect = MagicMock(return_value=mock_conn)
        mgr._engine = mock_engine

        received = []
        async with mgr.get_connection() as conn:
            received.append(conn)

        assert received[0] is mock_conn


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


class TestDatabaseManagerShutdown:
    """Tests for DatabaseManager.shutdown()."""

    @pytest.mark.asyncio
    async def test_shutdown_clears_engine(self):
        """shutdown() must set _engine to None."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        mock_sync_engine = MagicMock()
        mock_engine = MagicMock()
        mock_engine.sync_engine = mock_sync_engine
        mgr._engine = mock_engine

        await mgr.shutdown()

        assert mgr._engine is None

    @pytest.mark.asyncio
    async def test_shutdown_clears_session_factory(self):
        """shutdown() must set _session_factory to None."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        mock_sync_engine = MagicMock()
        mock_engine = MagicMock()
        mock_engine.sync_engine = mock_sync_engine
        mgr._engine = mock_engine
        mgr._session_factory = MagicMock()

        await mgr.shutdown()

        assert mgr._session_factory is None

    @pytest.mark.asyncio
    async def test_shutdown_calls_dispose(self):
        """shutdown() must dispose the sync engine pool."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        mock_sync_engine = MagicMock()
        mock_engine = MagicMock()
        mock_engine.sync_engine = mock_sync_engine
        mgr._engine = mock_engine

        await mgr.shutdown()

        mock_sync_engine.dispose.assert_called_once()

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
        """If _engine is already set, initialize() should return immediately."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()
        mgr._engine = MagicMock()  # pretend already initialized

        # Should not raise, should not create a new engine
        await mgr.initialize()
        # Engine should still be the same mock
        assert mgr._engine is not None

    @pytest.mark.asyncio
    async def test_initialize_failure_raises(self):
        """If create_async_engine fails, initialize() should raise."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        with (
            patch("persist.database.manager.create_async_engine", side_effect=Exception("engine fail")),
            pytest.raises(Exception, match="engine fail"),
        ):
            await mgr.initialize()


class TestDatabaseManagerTestConnection:
    """Tests for _test_connection() retry logic – lines 104-128."""

    @pytest.mark.asyncio
    async def test_test_connection_success_first_attempt(self):
        """Connection succeeds on first attempt."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1
        mock_conn.execute = AsyncMock(return_value=mock_result)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.connect = MagicMock(return_value=mock_conn)
        mgr._engine = mock_engine

        await mgr._test_connection()

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
        """If an exception occurs inside the session context, rollback is called."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        mock_session = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_session)
        mgr._session_factory = mock_factory

        with pytest.raises(ValueError, match="test error"):
            async with mgr.get_session():
                raise ValueError("test error")

        mock_session.rollback.assert_awaited_once()


class TestDatabaseManagerSessionAutoCommit:
    """AC-7 tests: get_session() auto-commits on clean exit when a transaction is active."""

    @pytest.mark.asyncio
    async def test_get_session_auto_commits_on_clean_exit_when_in_transaction(self):
        """If session.in_transaction() is True at clean exit, auto-commit fires."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        mock_session = AsyncMock()
        mock_session.in_transaction = MagicMock(return_value=True)
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_session)
        mgr._session_factory = mock_factory

        async with mgr.get_session() as db:
            # Caller does not commit explicitly; AC-7 should auto-commit.
            assert db is mock_session

        mock_session.commit.assert_awaited_once()
        mock_session.rollback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_session_does_not_double_commit_when_not_in_transaction(self):
        """If caller already committed (in_transaction()==False), AC-7 must not commit again."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        mock_session = AsyncMock()
        mock_session.in_transaction = MagicMock(return_value=False)
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_session)
        mgr._session_factory = mock_factory

        async with mgr.get_session() as db:
            assert db is mock_session

        # In-transaction is False (caller committed) → no auto-commit
        mock_session.commit.assert_not_awaited()
        mock_session.rollback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_session_rolls_back_when_auto_commit_fails(self):
        """If the auto-commit itself fails, rollback is attempted before re-raising."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        mock_session = AsyncMock()
        mock_session.in_transaction = MagicMock(return_value=True)
        mock_session.commit = AsyncMock(side_effect=RuntimeError("commit fail"))
        mock_session.rollback = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_session)
        mgr._session_factory = mock_factory

        with pytest.raises(RuntimeError, match="commit fail"):
            async with mgr.get_session():
                pass

        mock_session.commit.assert_awaited_once()
        mock_session.rollback.assert_awaited_once()


class TestDatabaseManagerExecuteSql:
    """Tests for execute_sql() error path – lines 171-178."""

    @pytest.mark.asyncio
    async def test_execute_sql_sqlalchemy_error(self):
        """SQLAlchemyError is caught, logged, and re-raised."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=SQLAlchemyError("sql fail"))

        mock_begin = AsyncMock()
        mock_begin.__aenter__ = AsyncMock(return_value=None)
        mock_begin.__aexit__ = AsyncMock(return_value=False)
        mock_conn.begin = MagicMock(return_value=mock_begin)

        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.connect = MagicMock(return_value=mock_conn)
        mgr._engine = mock_engine

        with pytest.raises(SQLAlchemyError):
            await mgr.execute_sql("SELECT 1")


class TestDatabaseManagerTableExists:
    """Tests for table_exists() error path – lines 188-196."""

    @pytest.mark.asyncio
    async def test_table_exists_engine_none_returns_false(self):
        """When engine is None, table_exists returns False."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        result = await mgr.table_exists("some_table")
        assert result is False

    @pytest.mark.asyncio
    async def test_table_exists_sqlalchemy_error_returns_false(self):
        """When inspector raises SQLAlchemyError, returns False."""
        with patch("persist.database.manager.bblogger"):
            mgr = DatabaseManager()

        mock_engine = MagicMock()
        mock_engine.sync_engine = MagicMock()
        mgr._engine = mock_engine

        with patch("persist.database.manager.inspect", side_effect=SQLAlchemyError("inspect fail")):
            result = await mgr.table_exists("some_table")

        assert result is False


class TestModuleLevelConvenienceFunctions:
    """Tests for module-level convenience functions – lines 264, 268, 272, 276."""

    def test_get_db_connection_returns_context_manager(self):
        """get_db_connection() should delegate to db_manager.get_connection()."""
        with patch("persist.database.manager.db_manager") as mock_mgr:
            mock_mgr.get_connection = MagicMock(return_value="conn_ctx")
            result = get_db_connection()
            assert result == "conn_ctx"

    def test_get_db_session_returns_context_manager(self):
        """get_db_session() should delegate to db_manager.get_session()."""
        with patch("persist.database.manager.db_manager") as mock_mgr:
            mock_mgr.get_session = MagicMock(return_value="session_ctx")
            result = get_db_session()
            assert result == "session_ctx"

    @pytest.mark.asyncio
    async def test_module_execute_sql_delegates(self):
        """execute_sql() should delegate to db_manager.execute_sql()."""
        with patch("persist.database.manager.db_manager") as mock_mgr:
            mock_mgr.execute_sql = AsyncMock(return_value="result")
            result = await module_execute_sql("SELECT 1")
            assert result == "result"

    @pytest.mark.asyncio
    async def test_module_table_exists_delegates(self):
        """table_exists() should delegate to db_manager.table_exists()."""
        with patch("persist.database.manager.db_manager") as mock_mgr:
            mock_mgr.table_exists = AsyncMock(return_value=True)
            result = await module_table_exists("players")
            assert result is True


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
