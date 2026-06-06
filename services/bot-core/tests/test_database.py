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

        with patch("persist.database.manager.time.sleep"):
            await mgr._test_connection()

        assert call_count == 3

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

        with patch("persist.database.manager.time.sleep"), pytest.raises(OperationalError):
            await mgr._test_connection()

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
