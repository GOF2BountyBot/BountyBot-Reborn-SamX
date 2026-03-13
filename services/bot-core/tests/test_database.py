"""Unit tests for DatabaseManager, CircuitBreaker, and TableNames.

shared.bblogger is mocked via conftest.py.  These tests exercise:

CircuitBreaker
  - Initial state is CLOSED
  - Successful calls keep the circuit CLOSED
  - Failures increment the failure counter
  - Reaching failure_threshold transitions to OPEN
  - OPEN state raises CircuitBreakerOpenException immediately
  - After recovery_timeout the circuit enters HALF_OPEN
  - A success in HALF_OPEN increments success_count; enough successes close it
  - A failure in HALF_OPEN re-opens the circuit

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
import time
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

from persist.database.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenException,
    CircuitState,
)
from persist.database.manager import DatabaseManager
from persist.database.tablenames import TableNames

# ===========================================================================
# CircuitBreaker tests
# ===========================================================================


class TestCircuitBreakerInitialState:
    """Verify the circuit breaker starts in the correct state."""

    def test_initial_state_is_closed(self):
        """Brand-new CircuitBreaker must be in the CLOSED state."""
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED

    def test_initial_failure_count_is_zero(self):
        """Failure counter must start at zero."""
        cb = CircuitBreaker()
        assert cb.failure_count == 0

    def test_initial_success_count_is_zero(self):
        """Success counter must start at zero."""
        cb = CircuitBreaker()
        assert cb.success_count == 0

    def test_initial_last_failure_time_is_none(self):
        """last_failure_time must be None before any failure."""
        cb = CircuitBreaker()
        assert cb.last_failure_time is None

    def test_custom_threshold_is_stored(self):
        """failure_threshold parameter must be honoured."""
        cb = CircuitBreaker(failure_threshold=3)
        assert cb.config.failure_threshold == 3

    def test_custom_recovery_timeout_is_stored(self):
        """recovery_timeout parameter must be honoured."""
        cb = CircuitBreaker(recovery_timeout=30)
        assert cb.config.recovery_timeout == 30


class TestCircuitBreakerClosedState:
    """Behaviour of the circuit breaker while CLOSED."""

    @pytest.mark.asyncio
    async def test_successful_call_returns_result(self):
        """A successful async call must return its result normally."""
        cb = CircuitBreaker()

        async def _success():
            return 42

        result = await cb.call(_success)
        assert result == 42

    @pytest.mark.asyncio
    async def test_successful_sync_call_returns_result(self):
        """A successful synchronous callable must also work."""
        cb = CircuitBreaker()

        def _sync():
            return "ok"

        result = await cb.call(_sync)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_successful_calls_keep_state_closed(self):
        """Multiple successes must leave the circuit CLOSED."""
        cb = CircuitBreaker(failure_threshold=3)

        async def _ok():
            return True

        for _ in range(5):
            await cb.call(_ok)

        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_failure_increments_counter(self):
        """Each expected exception must increment failure_count by 1."""
        cb = CircuitBreaker(failure_threshold=10)

        async def _fail():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await cb.call(_fail)

        assert cb.failure_count == 1

    @pytest.mark.asyncio
    async def test_multiple_failures_accumulate(self):
        """Consecutive failures must accumulate the counter."""
        cb = CircuitBreaker(failure_threshold=10)

        async def _fail():
            raise RuntimeError("boom")

        for _ in range(4):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)

        assert cb.failure_count == 4

    @pytest.mark.asyncio
    async def test_threshold_failures_open_circuit(self):
        """Exactly failure_threshold failures must open the circuit."""
        threshold = 3
        cb = CircuitBreaker(failure_threshold=threshold)

        async def _fail():
            raise RuntimeError("threshold hit")

        for _ in range(threshold):
            with pytest.raises(RuntimeError):
                await cb.call(_fail)

        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_unexpected_exception_does_not_count_as_failure(self):
        """An exception that is NOT expected_exception must not increment failure_count."""
        cb = CircuitBreaker(failure_threshold=3, expected_exception=ValueError)

        async def _runtime_fail():
            raise RuntimeError("not a ValueError")

        with pytest.raises(RuntimeError):
            await cb.call(_runtime_fail)

        # failure_count stays zero because RuntimeError != ValueError
        assert cb.failure_count == 0
        assert cb.state == CircuitState.CLOSED


class TestCircuitBreakerOpenState:
    """Behaviour of the circuit breaker while OPEN."""

    def _open_circuit(self, threshold: int = 2) -> CircuitBreaker:
        """Return a CircuitBreaker already in OPEN state."""
        cb = CircuitBreaker(failure_threshold=threshold)
        cb.state = CircuitState.OPEN
        cb.failure_count = threshold
        cb.last_failure_time = time.time()
        return cb

    @pytest.mark.asyncio
    async def test_open_state_raises_immediately(self):
        """An OPEN circuit must raise CircuitBreakerOpenException without calling func."""
        cb = self._open_circuit()

        called = []

        async def _would_be_called():
            called.append(True)
            return "result"

        with pytest.raises(CircuitBreakerOpenException):
            await cb.call(_would_be_called)

        assert called == [], "func should NOT have been invoked when circuit is OPEN"

    @pytest.mark.asyncio
    async def test_open_after_timeout_transitions_to_half_open(self):
        """After recovery_timeout the circuit must enter HALF_OPEN on the next call."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0)
        cb.state = CircuitState.OPEN
        cb.last_failure_time = time.time() - 1  # already past timeout

        async def _ok():
            return "half_open_success"

        # The circuit checks timeout, transitions to HALF_OPEN, then allows the call
        result = await cb.call(_ok)
        assert result == "half_open_success"
        # After one success in HALF_OPEN we're still not back to CLOSED yet
        # (success_threshold defaults to 3), so check state is not OPEN
        assert cb.state != CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_open_before_timeout_stays_open(self):
        """Before recovery_timeout elapses the circuit must stay OPEN."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=9999)
        cb.state = CircuitState.OPEN
        cb.last_failure_time = time.time()

        async def _ok():
            return "should not run"

        with pytest.raises(CircuitBreakerOpenException):
            await cb.call(_ok)

        assert cb.state == CircuitState.OPEN


class TestCircuitBreakerHalfOpenState:
    """Behaviour of the circuit breaker while HALF_OPEN."""

    def _half_open_circuit(self) -> CircuitBreaker:
        """Return a CircuitBreaker already in HALF_OPEN state."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0)
        cb.state = CircuitState.HALF_OPEN
        cb.last_failure_time = time.time() - 1
        return cb

    @pytest.mark.asyncio
    async def test_success_in_half_open_increments_success_count(self):
        """Each successful call in HALF_OPEN must increment success_count."""
        cb = self._half_open_circuit()

        async def _ok():
            return True

        await cb.call(_ok)
        assert cb.success_count == 1

    @pytest.mark.asyncio
    async def test_enough_successes_in_half_open_close_circuit(self):
        """After success_threshold successes in HALF_OPEN the circuit must CLOSE."""
        cb = self._half_open_circuit()
        threshold = cb.config.success_threshold

        async def _ok():
            return True

        for _ in range(threshold):
            await cb.call(_ok)

        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_closed_circuit_resets_success_count(self):
        """After closing, success_count must be reset to zero."""
        cb = self._half_open_circuit()
        threshold = cb.config.success_threshold

        async def _ok():
            return True

        for _ in range(threshold):
            await cb.call(_ok)

        assert cb.success_count == 0

    @pytest.mark.asyncio
    async def test_failure_in_half_open_reopens_circuit(self):
        """A failure in HALF_OPEN must immediately reopen the circuit."""
        cb = self._half_open_circuit()

        async def _fail():
            raise RuntimeError("half open fail")

        with pytest.raises(RuntimeError):
            await cb.call(_fail)

        assert cb.state == CircuitState.OPEN


class TestCircuitBreakerGetState:
    """Tests for CircuitBreaker.get_state()."""

    def test_get_state_returns_dict(self):
        """get_state() must return a dict."""
        cb = CircuitBreaker()
        state = cb.get_state()
        assert isinstance(state, dict)

    def test_get_state_contains_expected_keys(self):
        """get_state() dict must contain the documented keys."""
        cb = CircuitBreaker()
        state = cb.get_state()
        for key in ("state", "failure_count", "success_count", "last_failure_time",
                    "failure_threshold", "recovery_timeout"):
            assert key in state, f"Missing key: {key}"

    def test_get_state_reflects_current_state(self):
        """get_state() must reflect actual internal values."""
        cb = CircuitBreaker(failure_threshold=7, recovery_timeout=120)
        cb.state = CircuitState.OPEN
        cb.failure_count = 7

        state = cb.get_state()

        assert state["state"] == "open"
        assert state["failure_count"] == 7
        assert state["failure_threshold"] == 7
        assert state["recovery_timeout"] == 120


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
            "Bounty", "Criminal", "DiscordMessage", "GuildConfigs", "GuildShops",
            "Item", "Module", "PlayerInventories", "PlayerShips", "Players",
            "PrimaryWeapon", "SecondaryWeapon", "SchemaVersion", "Ship",
            "System", "TurretWeapon", "Users", "Weapon",
        }
        actual_names = {m.name for m in TableNames}
        assert expected_names == actual_names

    def test_values_are_lowercase_snake_case(self):
        """Table name values must be lowercase (snake_case) strings."""
        for member in TableNames:
            assert member.value == member.value.lower(), (
                f"{member.name}.value='{member.value}' is not lowercase"
            )
