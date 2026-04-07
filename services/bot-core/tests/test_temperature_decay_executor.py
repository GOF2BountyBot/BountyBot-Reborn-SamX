"""
Unit tests for utils.executors.temperature_decay_executor.

Tests verify:
 - Decay formula: temperature is multiplied by 2/3, floored at 1.0
 - All three divisions are decayed when no division filter is specified
 - Single-division filter is respected
 - Missing/None division_temperatures are treated as 1.0 per division
 - Guilds with no config entry are handled gracefully
 - Bulk mode (no guild_id) processes all configured guilds
 - No-guilds configured returns early with zero count
 - Updated temperatures are persisted via ConfigRepository
 - job_executor.py dispatches temperature_decay job_type

IMPORTANT: shared.bblogger is mocked BEFORE any source imports (via
conftest.py, with a belt-and-suspenders guard below).

Because temperature_decay_executor uses deferred (in-function) imports, we
pre-register stub modules in sys.modules so deferred imports inside
execute_temperature_decay_job resolve without requiring a live database.
"""

import os as _os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Guard: mock shared / shared.bblogger before importing any source modules.
# conftest.py handles this at collection time; guard is here for standalone runs.
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")

    def _make_logger(name: str = "test") -> MagicMock:
        logger = MagicMock()
        for m in ("info", "debug", "warning", "error", "trace", "critical"):
            setattr(logger, m, MagicMock())
        return logger

    _mock_bblogger.get_logger = _make_logger
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

# Ensure src is on the path.
_SRC = _os.path.join(_os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# ---------------------------------------------------------------------------
# Pre-register stub modules so deferred imports in temperature_decay_executor
# work without requiring a live database or installed ORM extras.
# ---------------------------------------------------------------------------


def _ensure_stub(module_path: str, **attrs) -> types.ModuleType:
    """Create and register a stub module if not already present."""
    if module_path not in sys.modules:
        mod = types.ModuleType(module_path)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[module_path] = mod
    return sys.modules[module_path]


# Stub for persist.database.manager — only db_manager attribute needed.
_mock_db_mgr_instance = MagicMock()
_ensure_stub("persist.database.manager", db_manager=_mock_db_mgr_instance)

# Stubs for repositories and services.
_MockConfigRepository = MagicMock()
_ensure_stub("persist.repositories.config_repository", ConfigRepository=_MockConfigRepository)

_MockTemperatureService = MagicMock()
_ensure_stub("services.temperature_service", TemperatureService=_MockTemperatureService)

# Ensure parent package stubs exist.
_ensure_stub("persist")
_ensure_stub("persist.database")
_ensure_stub("persist.repositories")
_ensure_stub("services")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_guild_config(
    guild_id: int,
    division_temperatures: dict[str, float] | None = None,
) -> MagicMock:
    """Build a mock GuildConfig-like object."""
    cfg = MagicMock()
    cfg.guild_id = guild_id
    cfg.division_temperatures = division_temperatures
    return cfg


def _mock_session_ctx(session: AsyncMock) -> MagicMock:
    """Return an async context manager that yields *session*."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _configure_db_manager(mock_db: AsyncMock) -> None:
    """Configure the stub db_manager to yield *mock_db* on get_session()."""
    mgr = sys.modules["persist.database.manager"].db_manager
    mgr.get_session = MagicMock(return_value=_mock_session_ctx(mock_db))


def _configure_config_repo(
    guild_configs: list,
    single_config: MagicMock | None = None,
) -> AsyncMock:
    """Configure ConfigRepository stubs.

    *guild_configs* is returned by ``list_all``.
    *single_config* is returned by ``get_by_guild_id`` (None → not found).
    The repo's ``update_division_temperatures`` is set up as a no-op AsyncMock.
    """
    mock_repo = AsyncMock()
    mock_repo.list_all = AsyncMock(return_value=guild_configs)
    mock_repo.get_by_guild_id = AsyncMock(return_value=single_config)
    mock_repo.update_division_temperatures = AsyncMock(return_value=single_config)
    sys.modules["persist.repositories.config_repository"].ConfigRepository = MagicMock(return_value=mock_repo)
    return mock_repo


def _configure_temperature_service_real() -> None:
    """Point TemperatureService.decay_temperature to the real implementation."""
    import importlib

    # Temporarily remove the stub so we can import the real module.
    ts_stub = sys.modules.pop("services.temperature_service", None)
    try:
        # The real module requires game_constants; stub that too if needed.
        _ensure_stub(
            "services.game_constants",
            GameConstants=_build_real_game_constants_stub(),
        )
        real_ts = importlib.import_module("services.temperature_service")
        sys.modules["services.temperature_service"] = real_ts
    except Exception:
        # If the real module can't be loaded, restore the stub.
        if ts_stub is not None:
            sys.modules["services.temperature_service"] = ts_stub
        raise


def _build_real_game_constants_stub():
    """Return a GameConstants-like class with the values needed by TemperatureService."""

    class _GC:
        GUILD_ACTIVITY_DECAY_RATE: float = 2 / 3
        MIN_GUILD_ACTIVITY: float = 1.0
        ACTIVITY_TEMP_PER_PLAYER: int = 1
        MAX_BOUNTIES_PER_DIVISION: int = 5
        BOUNTY_DELAY_RANDOM_MIN: int = 5
        BOUNTY_DELAY_RANDOM_MAX: int = 7

    return _GC


def _configure_temperature_service_mock(decay_side_effect=None) -> MagicMock:
    """Configure TemperatureService.decay_temperature as a MagicMock.

    If *decay_side_effect* is provided it is used as the side_effect; otherwise
    the mock applies the real 2/3 decay formula.
    """
    mock_ts = sys.modules["services.temperature_service"].TemperatureService

    if decay_side_effect is not None:
        mock_ts.decay_temperature = MagicMock(side_effect=decay_side_effect)
    else:
        # Default: apply real formula so we can assert precise values.
        def _real_decay(temp: float) -> float:
            decayed = temp * (2 / 3)
            return max(1.0, round(decayed, 1))

        mock_ts.decay_temperature = MagicMock(side_effect=_real_decay)

    return mock_ts


# ===========================================================================
# Tests: decay calculation
# ===========================================================================


class TestDecayCalculation:
    """Verify the 2/3 decay factor and 1.0 floor using the mocked service."""

    @pytest.mark.asyncio
    async def test_temperature_5_decays_to_3_3(self):
        """5.0 x 2/3 ≈ 3.333 → rounded to 3.3."""
        from utils.executors.temperature_decay_executor import execute_temperature_decay_job

        mock_db = AsyncMock()
        _configure_db_manager(mock_db)
        _configure_temperature_service_mock()

        cfg = _make_guild_config(guild_id=100, division_temperatures={"bronze": 5.0, "silver": 1.0, "gold": 1.0})
        _configure_config_repo(guild_configs=[cfg])

        result = await execute_temperature_decay_job("job-calc-5", {"job_type": "temperature_decay"})

        assert result["status"] == "success"
        bronze_result = result["results"][100]["bronze"]
        assert bronze_result["before"] == pytest.approx(5.0)
        assert bronze_result["after"] == pytest.approx(3.3)

    @pytest.mark.asyncio
    async def test_temperature_at_floor_stays_1_0(self):
        """1.0 x 2/3 = 0.67 → floored at 1.0."""
        from utils.executors.temperature_decay_executor import execute_temperature_decay_job

        mock_db = AsyncMock()
        _configure_db_manager(mock_db)
        _configure_temperature_service_mock()

        cfg = _make_guild_config(guild_id=200, division_temperatures={"bronze": 1.0, "silver": 1.0, "gold": 1.0})
        _configure_config_repo(guild_configs=[cfg])

        result = await execute_temperature_decay_job("job-floor", {"job_type": "temperature_decay"})

        for div in ("bronze", "silver", "gold"):
            assert result["results"][200][div]["after"] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_temperature_10_decays_to_6_7(self):
        """10.0 x 2/3 ≈ 6.666 → rounded to 6.7."""
        from utils.executors.temperature_decay_executor import execute_temperature_decay_job

        mock_db = AsyncMock()
        _configure_db_manager(mock_db)
        _configure_temperature_service_mock()

        cfg = _make_guild_config(guild_id=300, division_temperatures={"bronze": 10.0, "silver": 10.0, "gold": 10.0})
        _configure_config_repo(guild_configs=[cfg])

        result = await execute_temperature_decay_job("job-10", {"job_type": "temperature_decay"})

        for div in ("bronze", "silver", "gold"):
            assert result["results"][300][div]["after"] == pytest.approx(6.7)

    @pytest.mark.asyncio
    async def test_missing_division_temperature_defaults_to_1_0(self):
        """If a division is absent from division_temperatures, it is treated as 1.0."""
        from utils.executors.temperature_decay_executor import execute_temperature_decay_job

        mock_db = AsyncMock()
        _configure_db_manager(mock_db)
        _configure_temperature_service_mock()

        # Only 'bronze' is present; 'silver' and 'gold' are missing.
        cfg = _make_guild_config(guild_id=400, division_temperatures={"bronze": 3.0})
        _configure_config_repo(guild_configs=[cfg])

        result = await execute_temperature_decay_job("job-missing", {"job_type": "temperature_decay"})

        # silver and gold should start at 1.0 and stay at 1.0 (floor).
        assert result["results"][400]["silver"]["before"] == pytest.approx(1.0)
        assert result["results"][400]["silver"]["after"] == pytest.approx(1.0)
        assert result["results"][400]["gold"]["before"] == pytest.approx(1.0)
        assert result["results"][400]["gold"]["after"] == pytest.approx(1.0)
        # Bronze decays normally.
        assert result["results"][400]["bronze"]["before"] == pytest.approx(3.0)
        assert result["results"][400]["bronze"]["after"] == pytest.approx(2.0)

    @pytest.mark.asyncio
    async def test_none_division_temperatures_defaults_to_1_0(self):
        """If division_temperatures is None (new guild), all divisions default to 1.0."""
        from utils.executors.temperature_decay_executor import execute_temperature_decay_job

        mock_db = AsyncMock()
        _configure_db_manager(mock_db)
        _configure_temperature_service_mock()

        cfg = _make_guild_config(guild_id=500, division_temperatures=None)
        _configure_config_repo(guild_configs=[cfg])

        result = await execute_temperature_decay_job("job-none-temps", {"job_type": "temperature_decay"})

        for div in ("bronze", "silver", "gold"):
            assert result["results"][500][div]["before"] == pytest.approx(1.0)
            assert result["results"][500][div]["after"] == pytest.approx(1.0)


# ===========================================================================
# Tests: division filtering
# ===========================================================================


class TestDivisionFiltering:
    """Verify that the division payload field restricts which division is processed."""

    @pytest.mark.asyncio
    async def test_only_specified_division_is_decayed(self):
        """When division='Silver', only silver is in the results."""
        from utils.executors.temperature_decay_executor import execute_temperature_decay_job

        mock_db = AsyncMock()
        _configure_db_manager(mock_db)
        _configure_temperature_service_mock()

        cfg = _make_guild_config(
            guild_id=600,
            division_temperatures={"bronze": 5.0, "silver": 5.0, "gold": 5.0},
        )
        # guild_id in payload → single-guild mode uses get_by_guild_id.
        _configure_config_repo(guild_configs=[], single_config=cfg)

        result = await execute_temperature_decay_job(
            "job-div-filter",
            {"job_type": "temperature_decay", "guild_id": 600, "division": "Silver"},
        )

        gid_result = result["results"][600]
        # Only silver should appear.
        assert "silver" in gid_result
        assert "bronze" not in gid_result
        assert "gold" not in gid_result
        assert result["total_decays"] == 1

    @pytest.mark.asyncio
    async def test_division_filter_case_insensitive(self):
        """Division filter is normalised to lowercase before lookup."""
        from utils.executors.temperature_decay_executor import execute_temperature_decay_job

        mock_db = AsyncMock()
        _configure_db_manager(mock_db)
        _configure_temperature_service_mock()

        cfg = _make_guild_config(
            guild_id=700,
            division_temperatures={"bronze": 3.0, "silver": 3.0, "gold": 3.0},
        )
        # guild_id in payload → single-guild mode uses get_by_guild_id.
        _configure_config_repo(guild_configs=[], single_config=cfg)

        # Pass 'GOLD' in upper case — executor should normalise.
        result = await execute_temperature_decay_job(
            "job-case",
            {"job_type": "temperature_decay", "guild_id": 700, "division": "GOLD"},
        )

        gid_result = result["results"][700]
        assert "gold" in gid_result
        assert gid_result["gold"]["before"] == pytest.approx(3.0)
        assert gid_result["gold"]["after"] == pytest.approx(2.0)

    @pytest.mark.asyncio
    async def test_all_three_divisions_decayed_when_no_filter(self):
        """When no division is specified, all four divisions are decayed."""
        from utils.executors.temperature_decay_executor import execute_temperature_decay_job

        mock_db = AsyncMock()
        _configure_db_manager(mock_db)
        _configure_temperature_service_mock()

        cfg = _make_guild_config(
            guild_id=800,
            division_temperatures={"bronze": 3.0, "silver": 4.5, "gold": 9.0, "platinum": 1.0},
        )
        _configure_config_repo(guild_configs=[cfg])

        result = await execute_temperature_decay_job("job-all-div", {"job_type": "temperature_decay"})

        gid_result = result["results"][800]
        assert "bronze" in gid_result
        assert "silver" in gid_result
        assert "gold" in gid_result
        assert "platinum" in gid_result
        assert result["total_decays"] == 4


# ===========================================================================
# Tests: bulk mode
# ===========================================================================


class TestBulkMode:
    """Verify bulk (no guild_id) and single-guild modes."""

    @pytest.mark.asyncio
    async def test_bulk_mode_processes_all_guilds(self):
        """No guild_id → all configured guilds are processed."""
        from utils.executors.temperature_decay_executor import execute_temperature_decay_job

        mock_db = AsyncMock()
        _configure_db_manager(mock_db)
        _configure_temperature_service_mock()

        cfg1 = _make_guild_config(10, {"bronze": 3.0, "silver": 1.0, "gold": 1.0, "platinum": 1.0})
        cfg2 = _make_guild_config(20, {"bronze": 6.0, "silver": 6.0, "gold": 6.0, "platinum": 1.0})
        _configure_config_repo(guild_configs=[cfg1, cfg2])

        result = await execute_temperature_decay_job("job-bulk", {"job_type": "temperature_decay"})

        assert result["status"] == "success"
        assert result["guilds_processed"] == 2
        assert result["total_decays"] == 8  # 4 divisions x 2 guilds
        assert 10 in result["results"]
        assert 20 in result["results"]

    @pytest.mark.asyncio
    async def test_bulk_mode_no_guilds_returns_zero(self):
        """Bulk mode with no configured guilds returns guilds_processed=0."""
        from utils.executors.temperature_decay_executor import execute_temperature_decay_job

        mock_db = AsyncMock()
        _configure_db_manager(mock_db)
        _configure_temperature_service_mock()
        _configure_config_repo(guild_configs=[])

        result = await execute_temperature_decay_job("job-no-guilds", {"job_type": "temperature_decay"})

        assert result["status"] == "success"
        assert result["guilds_processed"] == 0
        assert result["total_decays"] == 0

    @pytest.mark.asyncio
    async def test_single_guild_mode(self):
        """guild_id in payload → only that guild is processed."""
        from utils.executors.temperature_decay_executor import execute_temperature_decay_job

        mock_db = AsyncMock()
        _configure_db_manager(mock_db)
        _configure_temperature_service_mock()

        cfg = _make_guild_config(999, {"bronze": 5.0, "silver": 5.0, "gold": 5.0})
        _configure_config_repo(guild_configs=[], single_config=cfg)

        result = await execute_temperature_decay_job(
            "job-single",
            {"job_type": "temperature_decay", "guild_id": 999},
        )

        assert result["guilds_processed"] == 1
        assert 999 in result["results"]

    @pytest.mark.asyncio
    async def test_single_guild_not_found_returns_zero(self):
        """When guild_id is given but config doesn't exist, guilds_processed=0."""
        from utils.executors.temperature_decay_executor import execute_temperature_decay_job

        mock_db = AsyncMock()
        _configure_db_manager(mock_db)
        _configure_temperature_service_mock()

        # get_by_guild_id returns None — guild not configured.
        _configure_config_repo(guild_configs=[], single_config=None)

        result = await execute_temperature_decay_job(
            "job-notfound",
            {"job_type": "temperature_decay", "guild_id": 12345},
        )

        assert result["status"] == "success"
        assert result["guilds_processed"] == 0
        assert result["total_decays"] == 0


# ===========================================================================
# Tests: persistence
# ===========================================================================


class TestPersistence:
    """Verify that decayed temperatures are written back to the database."""

    @pytest.mark.asyncio
    async def test_update_division_temperatures_called_per_guild(self):
        """ConfigRepository.update_division_temperatures is called once per guild."""
        from utils.executors.temperature_decay_executor import execute_temperature_decay_job

        mock_db = AsyncMock()
        _configure_db_manager(mock_db)
        _configure_temperature_service_mock()

        cfg = _make_guild_config(111, {"bronze": 3.0, "silver": 1.0, "gold": 1.0})
        repo = _configure_config_repo(guild_configs=[cfg])

        await execute_temperature_decay_job("job-persist", {"job_type": "temperature_decay"})

        repo.update_division_temperatures.assert_awaited_once()
        call_args = repo.update_division_temperatures.call_args
        saved_guild_id = call_args.args[1]
        saved_temps = call_args.args[2]

        assert saved_guild_id == 111
        assert saved_temps["bronze"] == pytest.approx(2.0)  # 3.0 x 2/3
        assert saved_temps["silver"] == pytest.approx(1.0)  # floor
        assert saved_temps["gold"] == pytest.approx(1.0)  # floor

    @pytest.mark.asyncio
    async def test_update_called_with_correct_temperatures_multi_guild(self):
        """update_division_temperatures is called once per guild with right values."""
        from utils.executors.temperature_decay_executor import execute_temperature_decay_job

        mock_db = AsyncMock()
        _configure_db_manager(mock_db)
        _configure_temperature_service_mock()

        cfg1 = _make_guild_config(1, {"bronze": 3.0, "silver": 3.0, "gold": 3.0})
        cfg2 = _make_guild_config(2, {"bronze": 1.5, "silver": 1.5, "gold": 1.5})
        repo = _configure_config_repo(guild_configs=[cfg1, cfg2])

        await execute_temperature_decay_job("job-persist-multi", {"job_type": "temperature_decay"})

        assert repo.update_division_temperatures.await_count == 2


# ===========================================================================
# Tests: job_executor dispatch
# ===========================================================================


class TestJobExecutorDispatch:
    """Verify job_executor.py routes temperature_decay to the right function."""

    @pytest.mark.asyncio
    async def test_job_executor_dispatches_temperature_decay(self):
        """JobExecutor.execute routes temperature_decay payload to execute_temperature_decay_job."""
        from utils.job_executor import JobExecutor

        executor = JobExecutor()
        payload = {"job_type": "temperature_decay"}

        mock_fn = AsyncMock(return_value={"status": "success"})
        with patch("utils.job_executor.execute_temperature_decay_job", mock_fn):
            await executor.execute("job-dispatch-decay", payload)

        mock_fn.assert_awaited_once_with("job-dispatch-decay", payload)

    @pytest.mark.asyncio
    async def test_job_executor_does_not_dispatch_decay_for_bounty_spawn(self):
        """bounty_spawn payloads do NOT trigger execute_temperature_decay_job."""
        from utils.job_executor import JobExecutor

        executor = JobExecutor()
        payload = {"job_type": "bounty_spawn", "guild_id": 555, "division": "Gold"}

        mock_decay_fn = AsyncMock()
        mock_spawn_fn = AsyncMock(return_value={"status": "success"})

        with (
            patch("utils.job_executor.execute_temperature_decay_job", mock_decay_fn),
            patch("utils.job_executor.execute_bounty_spawn_job", mock_spawn_fn),
        ):
            await executor.execute("job-spawn-not-decay", payload)

        mock_decay_fn.assert_not_awaited()
        mock_spawn_fn.assert_awaited_once()
