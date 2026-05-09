"""S4 rewrite: temperature_decay_executor tests — real SQLite, 0 repo mocks.

Sprint 4 (S4) of the Test Quality Blitz.

PATTERN OVERVIEW
----------------
Three-tier breakdown following ``tests/AGENTS.md`` §"Executor Test Pattern (S2)":

  Tier A — Pure unit tests. ZERO mocks.

  Tier B — SQLite-in-memory integration for ORM read/write paths.
            Only patch: ``patch("persist.database.manager.db_manager", ...)``.
            NO repository or service methods mocked.

  (No Tier C — temperature decay makes no HTTP calls.)

BEHAVIOURS COVERED
------------------
| # | Behaviour | Tier |
|---|-----------|------|
| 1 | No guilds configured → guilds_processed=0, total_decays=0 | B |
| 2 | Decay formula: value * 2/3, floor at 1.0, round to 1 dp | A |
| 3 | Single guild: all 4 divisions decayed and persisted | B |
| 4 | Cross-session reload confirms temperatures persisted | B |
| 5 | Guild not in DB (single-guild mode) → guilds_processed=0 | B |
| 6 | Single-guild payload processes only that guild | B |
| 7 | Division filter in payload limits decay to one division | B |
| 8 | Default temperature (None/missing) treated as 1.0 | B |
| 9 | Multi-guild: each guild's temperatures independently decayed | B |

SQLITE COMPATIBILITY NOTE
--------------------------
GuildConfig has no ARRAY columns and is fully SQLite-compatible.
ConfigRepository.list_all, get_by_guild_id, and update_division_temperatures
are pure ORM operations that run on SQLite.

TemperatureService.decay_temperature is a static pure-math function —
it is exercised directly in Tier A tests without any DB involvement.
"""

from __future__ import annotations

import os
import sys
import types
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock, patch

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
from persist.models.base import Base
from persist.models.guild_config import GuildConfig
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from utils.executors.temperature_decay_executor import execute_temperature_decay_job

# ---------------------------------------------------------------------------
# SQLite table list
# ---------------------------------------------------------------------------

_SQLITE_TABLES = [
    GuildConfig.__table__,
]

# ---------------------------------------------------------------------------
# Common test constants
# ---------------------------------------------------------------------------

GUILD_ID = 9_500_000_050
GUILD_ID_2 = 9_500_000_051


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
    division_temperatures: dict[str, float] | None = None,
) -> GuildConfig:
    """Persist a GuildConfig with given division temperatures."""
    config = GuildConfig(
        guild_id=guild_id,
        division_temperatures=division_temperatures
        or {
            "bronze": 3.0,
            "silver": 2.0,
            "gold": 1.5,
            "platinum": 1.0,
        },
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


# ===========================================================================
# TIER A — Pure unit tests (ZERO mocks)
# ===========================================================================


class TestDecayFormula:
    """Behaviour #2: decay formula — value * 2/3, floor at 1.0, round to 1 dp."""

    def test_decay_of_3_0(self):
        """3.0 * (2/3) = 2.0 → 2.0 (no floor needed)."""
        from services.temperature_service import TemperatureService

        result = TemperatureService.decay_temperature(3.0)
        assert result == 2.0, f"Expected 3.0 → 2.0, got {result!r}"

    def test_decay_of_1_5(self):
        """1.5 * (2/3) = 1.0 → 1.0 (exactly floor)."""
        from services.temperature_service import TemperatureService

        result = TemperatureService.decay_temperature(1.5)
        assert result == 1.0, f"Expected 1.5 → 1.0, got {result!r}"

    def test_decay_floor_at_one(self):
        """Values below 1.5 decay to floor (1.0)."""
        from services.temperature_service import TemperatureService

        result = TemperatureService.decay_temperature(1.0)
        assert result == 1.0, f"Expected 1.0 → 1.0 (floor), got {result!r}"

    def test_decay_of_high_temperature(self):
        """High temperature: 9.0 * (2/3) = 6.0."""
        from services.temperature_service import TemperatureService

        result = TemperatureService.decay_temperature(9.0)
        assert result == 6.0, f"Expected 9.0 → 6.0, got {result!r}"

    def test_decay_rounding(self):
        """Result is rounded to 1 decimal place."""
        from services.temperature_service import TemperatureService

        # 2.0 * (2/3) = 1.3333... → rounds to 1.3
        result = TemperatureService.decay_temperature(2.0)
        assert result == 1.3, f"Expected 2.0 → 1.3 (rounded), got {result!r}"


# ===========================================================================
# TIER B — SQLite integration (1 patch only: db_manager bridge)
# ===========================================================================


class TestNoGuildsConfigured:
    """Behaviour #1: empty guild_configs → guilds_processed=0, total_decays=0."""

    async def test_empty_db_returns_zero_processed(self, sqlite_engine_and_factory):
        """When no GuildConfig rows exist, bulk decay returns 0.

        # 1 mock — db_manager bridge (Tier B)
        """
        _engine, factory = sqlite_engine_and_factory
        # No rows seeded.

        with patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)):
            result = await execute_temperature_decay_job("job-empty", {})

        assert result["status"] == "success"
        assert result["guilds_processed"] == 0, f"Expected guilds_processed=0, got {result['guilds_processed']!r}"
        assert result["total_decays"] == 0
        assert result["results"] == {}


class TestSingleGuildAllDivisions:
    """Behaviour #3: single guild — all 4 divisions decayed and persisted."""

    async def test_all_four_divisions_decayed(self, sqlite_engine_and_factory):
        """Bulk mode with one guild decays all four divisions.

        # 1 mock — db_manager bridge (Tier B)
        """
        _engine, factory = sqlite_engine_and_factory

        initial_temps = {"bronze": 3.0, "silver": 2.0, "gold": 1.5, "platinum": 1.0}

        async with factory() as seed_db:
            await _seed_guild_config(seed_db, GUILD_ID, division_temperatures=initial_temps)

        with patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)):
            result = await execute_temperature_decay_job("job-four-divs", {})

        assert result["status"] == "success"
        assert result["guilds_processed"] == 1
        assert result["total_decays"] == 4, f"Expected 4 decays (one per division), got {result['total_decays']!r}"

        # Verify per-division before/after values in result.
        guild_result = result["results"][GUILD_ID]
        assert "bronze" in guild_result
        assert "silver" in guild_result
        assert "gold" in guild_result
        assert "platinum" in guild_result

        # Verify computed 'after' values match the expected formula.
        assert guild_result["bronze"]["before"] == 3.0
        assert guild_result["bronze"]["after"] == 2.0  # 3.0 * 2/3 = 2.0

        assert guild_result["silver"]["before"] == 2.0
        assert guild_result["silver"]["after"] == 1.3  # 2.0 * 2/3 = 1.333 → 1.3

        assert guild_result["gold"]["before"] == 1.5
        assert guild_result["gold"]["after"] == 1.0  # 1.5 * 2/3 = 1.0

        assert guild_result["platinum"]["before"] == 1.0
        assert guild_result["platinum"]["after"] == 1.0  # floor at 1.0


class TestCrossSessionPersistence:
    """Behaviour #4: cross-session reload confirms temperatures persisted."""

    async def test_decayed_temperatures_persisted_to_db(self, sqlite_engine_and_factory):
        """After decay job, a fresh session reads the updated temperatures.

        # 1 mock — db_manager bridge (Tier B)
        """
        _engine, factory = sqlite_engine_and_factory

        initial_temps = {"bronze": 3.0, "silver": 3.0, "gold": 3.0, "platinum": 3.0}

        async with factory() as seed_db:
            await _seed_guild_config(seed_db, GUILD_ID, division_temperatures=initial_temps)

        with patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)):
            result = await execute_temperature_decay_job("job-persist", {})

        assert result["status"] == "success"

        # Cross-session reload: verify DB was actually updated.
        async with factory() as verify_db:
            row = await verify_db.execute(select(GuildConfig).where(GuildConfig.guild_id == GUILD_ID))
            config = row.scalars().first()

        assert config is not None
        stored = config.division_temperatures
        assert stored is not None

        # All temperatures should be decayed from 3.0 → 2.0.
        for div in ["bronze", "silver", "gold", "platinum"]:
            assert stored[div] == 2.0, f"Expected {div} temperature=2.0 after decay, got {stored[div]!r}"


class TestSingleGuildModeGuildNotFound:
    """Behaviour #5: guild not in DB (single-guild mode) → guilds_processed=0."""

    async def test_unknown_guild_id_returns_zero_processed(self, sqlite_engine_and_factory):
        """When guild_id is provided but no GuildConfig row exists, returns 0.

        # 1 mock — db_manager bridge (Tier B)
        """
        _engine, factory = sqlite_engine_and_factory
        # No rows seeded.

        with patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)):
            result = await execute_temperature_decay_job("job-unknown-guild", {"guild_id": GUILD_ID})

        assert result["status"] == "success"
        assert result["guilds_processed"] == 0, f"Expected 0 guilds_processed for unknown guild, got {result!r}"
        assert result["total_decays"] == 0


class TestSingleGuildPayload:
    """Behaviour #6: single-guild payload processes only that guild."""

    async def test_single_guild_payload_processes_only_that_guild(self, sqlite_engine_and_factory):
        """With guild_id in payload, only that guild is decayed (not others).

        # 1 mock — db_manager bridge (Tier B)
        """
        _engine, factory = sqlite_engine_and_factory

        temps_g1 = {"bronze": 3.0, "silver": 3.0, "gold": 3.0, "platinum": 3.0}
        temps_g2 = {"bronze": 5.0, "silver": 5.0, "gold": 5.0, "platinum": 5.0}
        async with factory() as seed_db:
            await _seed_guild_config(seed_db, GUILD_ID, division_temperatures=temps_g1)
            await _seed_guild_config(seed_db, GUILD_ID_2, division_temperatures=temps_g2)

        with patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)):
            result = await execute_temperature_decay_job("job-single-guild", {"guild_id": GUILD_ID})

        assert result["status"] == "success"
        assert result["guilds_processed"] == 1, f"Expected 1 guilds_processed (only GUILD_ID), got {result!r}"
        assert GUILD_ID in result["results"], "GUILD_ID should be in results"
        assert GUILD_ID_2 not in result["results"], "GUILD_ID_2 should NOT be in results"

        # GUILD_ID_2 temperatures should be unchanged.
        async with factory() as verify_db:
            row = await verify_db.execute(select(GuildConfig).where(GuildConfig.guild_id == GUILD_ID_2))
            config2 = row.scalars().first()

        assert config2 is not None
        # GUILD_ID_2 was NOT decayed — should still be 5.0 for all divisions.
        for div in ["bronze", "silver", "gold", "platinum"]:
            assert config2.division_temperatures[div] == 5.0, (
                f"Expected {div}=5.0 for GUILD_ID_2 (untouched), got {config2.division_temperatures[div]!r}"
            )


class TestDivisionFilterInPayload:
    """Behaviour #7: division filter limits decay to one division."""

    async def test_division_filter_limits_decay_to_one_division(self, sqlite_engine_and_factory):
        """With guild_id + division in payload, only that division is decayed.

        # 1 mock — db_manager bridge (Tier B)
        """
        _engine, factory = sqlite_engine_and_factory

        initial_temps = {"bronze": 3.0, "silver": 3.0, "gold": 3.0, "platinum": 3.0}

        async with factory() as seed_db:
            await _seed_guild_config(seed_db, GUILD_ID, division_temperatures=initial_temps)

        with patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)):
            result = await execute_temperature_decay_job(
                "job-div-filter",
                {"guild_id": GUILD_ID, "division": "bronze"},
            )

        assert result["status"] == "success"
        assert result["total_decays"] == 1, f"Expected 1 decay (bronze only), got {result['total_decays']!r}"

        # Only bronze should be in the per-guild result.
        guild_result = result["results"][GUILD_ID]
        assert "bronze" in guild_result
        # Other divisions should NOT have been processed.
        assert "silver" not in guild_result
        assert "gold" not in guild_result

        # Cross-session reload: only bronze changed.
        async with factory() as verify_db:
            row = await verify_db.execute(select(GuildConfig).where(GuildConfig.guild_id == GUILD_ID))
            config = row.scalars().first()

        assert config.division_temperatures["bronze"] == 2.0, (
            f"Expected bronze=2.0 after decay, got {config.division_temperatures['bronze']!r}"
        )
        # Others unchanged.
        assert config.division_temperatures["silver"] == 3.0, "Silver should not have been decayed"


class TestDefaultTemperature:
    """Behaviour #8: default temperature (None/missing key) treated as 1.0."""

    async def test_missing_division_key_defaults_to_1_0(self, sqlite_engine_and_factory):
        """A GuildConfig with no stored temperature for a division defaults to 1.0.

        When the 'gold' key is missing from division_temperatures, the executor
        should treat it as 1.0 (the default), decay to 1.0 (floor), and persist.

        # 1 mock — db_manager bridge (Tier B)
        """
        _engine, factory = sqlite_engine_and_factory

        # Only bronze and silver stored; gold and platinum are missing.
        partial_temps = {"bronze": 3.0, "silver": 2.0}

        async with factory() as seed_db:
            await _seed_guild_config(seed_db, GUILD_ID, division_temperatures=partial_temps)

        with patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)):
            result = await execute_temperature_decay_job("job-default-temp", {})

        assert result["status"] == "success"
        guild_result = result["results"][GUILD_ID]

        # Gold was missing — default 1.0 applied, decay keeps it at 1.0 (floor).
        assert guild_result["gold"]["before"] == 1.0, (
            f"Expected gold before=1.0 (default), got {guild_result['gold']['before']!r}"
        )
        assert guild_result["gold"]["after"] == 1.0, (
            f"Expected gold after=1.0 (floor), got {guild_result['gold']['after']!r}"
        )


class TestMultiGuildDecay:
    """Behaviour #9: multi-guild — each guild's temperatures independently decayed."""

    async def test_two_guilds_independently_decayed(self, sqlite_engine_and_factory):
        """Both guilds decayed; GUILD_ID with 3.0 → 2.0, GUILD_ID_2 with 6.0 → 4.0.

        # 1 mock — db_manager bridge (Tier B)
        """
        _engine, factory = sqlite_engine_and_factory

        temps1 = {"bronze": 3.0, "silver": 3.0, "gold": 3.0, "platinum": 3.0}
        temps2 = {"bronze": 6.0, "silver": 6.0, "gold": 6.0, "platinum": 6.0}

        async with factory() as seed_db:
            await _seed_guild_config(seed_db, GUILD_ID, division_temperatures=temps1)
            await _seed_guild_config(seed_db, GUILD_ID_2, division_temperatures=temps2)

        with patch("persist.database.manager.db_manager", _make_fake_db_manager(factory)):
            result = await execute_temperature_decay_job("job-multi-guild", {})

        assert result["status"] == "success"
        assert result["guilds_processed"] == 2
        assert result["total_decays"] == 8  # 4 divisions × 2 guilds

        # GUILD_ID: 3.0 → 2.0
        g1_bronze = result["results"][GUILD_ID]["bronze"]
        assert g1_bronze["before"] == 3.0
        assert g1_bronze["after"] == 2.0

        # GUILD_ID_2: 6.0 → 4.0
        g2_bronze = result["results"][GUILD_ID_2]["bronze"]
        assert g2_bronze["before"] == 6.0
        assert g2_bronze["after"] == 4.0

        # Cross-session reload to verify both guilds persisted.
        async with factory() as verify_db:
            row1 = await verify_db.execute(select(GuildConfig).where(GuildConfig.guild_id == GUILD_ID))
            cfg1 = row1.scalars().first()
            row2 = await verify_db.execute(select(GuildConfig).where(GuildConfig.guild_id == GUILD_ID_2))
            cfg2 = row2.scalars().first()

        assert cfg1.division_temperatures["bronze"] == 2.0
        assert cfg2.division_temperatures["bronze"] == 4.0
