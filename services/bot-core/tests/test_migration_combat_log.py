"""Tests for the 0011 combat_log migration.

Covers:
  - Migration file structure (revision chain, up/down symmetry)
  - Model-level schema verification via SQLAlchemy inspect
  - SQLite-backed table creation (validates CREATE TABLE DDL is correct)
  - NPC invariant documented via model attributes
  - Index declarations on the model
"""

import importlib
import os
import sys
from types import ModuleType
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Mock shared.bblogger and sqlalchemy_utils before any src imports
# ---------------------------------------------------------------------------
_mock_shared = ModuleType("shared")
_mock_shared.bblogger = MagicMock()
_mock_shared.bblogger.get_logger = MagicMock(return_value=MagicMock())
sys.modules.setdefault("shared", _mock_shared)
sys.modules.setdefault("shared.bblogger", _mock_shared.bblogger)

_mock_sau = ModuleType("sqlalchemy_utils")
_mock_sau.UUIDType = MagicMock()
sys.modules.setdefault("sqlalchemy_utils", _mock_sau)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from persist.models.base import Base
from persist.models.combat_log import CombatLog
from persist.models.guild_config import GuildConfig
from persist.models.player import Player
from persist.models.player_ship import PlayerShip
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Migration file structure tests (no DB needed)
# ---------------------------------------------------------------------------


def test_migration_revision_id():
    """0011 migration has the expected revision identifier."""
    import persist.database.revisions.versions  # noqa: F401

    revisions_dir = os.path.join(os.path.dirname(__file__), "..", "src", "persist", "database", "revisions", "versions")
    migration_path = os.path.join(revisions_dir, "0011_combat_log_and_phase1_stats.py")
    assert os.path.exists(migration_path), "Migration file 0011 must exist"

    spec = importlib.util.spec_from_file_location("migration_0011", migration_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.revision == "0011"
    assert mod.down_revision == "0010"
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)


# ---------------------------------------------------------------------------
# Model schema verification — Player new columns
# ---------------------------------------------------------------------------


def test_player_has_lifetime_counter_columns():
    cols = {c.name: c for c in Player.__table__.columns}
    for col_name in ("total_fights", "total_nukes_fired", "total_module_activations"):
        assert col_name in cols, f"Player must have column {col_name!r}"
        col = cols[col_name]
        assert not col.nullable, f"{col_name} must be NOT NULL"
        assert col.server_default is not None, f"{col_name} must have server_default='0'"


# ---------------------------------------------------------------------------
# Model schema verification — PlayerShip.manual_turret_mode dropped (0018)
# ---------------------------------------------------------------------------


def test_player_ship_manual_turret_mode_dropped():
    """Retired by range-driven turret switching; column dropped in revision 0018."""
    cols = {c.name: c for c in PlayerShip.__table__.columns}
    assert "manual_turret_mode" not in cols


# ---------------------------------------------------------------------------
# Model schema verification — CombatLog table
# ---------------------------------------------------------------------------


def test_combat_log_tablename():
    assert CombatLog.__tablename__ == "combat_log"


def test_combat_log_columns_present():
    required = {
        "id",
        "guild_id",
        "context",
        "combatant1_name",
        "combatant2_name",
        "combatant1_user_id",
        "combatant2_user_id",
        "winner_name",
        "is_stalemate",
        "data",
        "created_at",
    }
    actual = {c.name for c in CombatLog.__table__.columns}
    assert required <= actual, f"Missing columns: {required - actual}"


def test_combat_log_nullability():
    cols = {c.name: c for c in CombatLog.__table__.columns}
    # Must NOT be nullable
    for must_not_null in (
        "id",
        "guild_id",
        "context",
        "combatant1_name",
        "combatant2_name",
        "is_stalemate",
        "data",
        "created_at",
    ):
        assert not cols[must_not_null].nullable, f"{must_not_null} must be NOT NULL"
    # Must allow NULL (NPC side)
    for must_null in ("combatant1_user_id", "combatant2_user_id", "winner_name"):
        assert cols[must_null].nullable, f"{must_null} must be nullable"


def test_combat_log_indexes():
    """Two single-column indexes exist; no composite covering both columns."""
    index_names = {idx.name for idx in CombatLog.__table__.indexes}
    assert "ix_combat_log_combatant1_user_id" in index_names
    assert "ix_combat_log_combatant2_user_id" in index_names

    # Neither index covers BOTH user_id columns (no composite)
    for idx in CombatLog.__table__.indexes:
        col_names = {c.name for c in idx.columns}
        assert not ({"combatant1_user_id", "combatant2_user_id"} <= col_names), (
            "Found a composite index covering both combatant user_id columns — "
            "spec requires two separate single-column indexes"
        )


# ---------------------------------------------------------------------------
# Model schema verification — GuildConfig Appendix A columns
# ---------------------------------------------------------------------------


_APPENDIX_A_COLUMNS = [
    "cloak_set_value",
    "booster_accuracy_debuff_factor",
    "thruster_accuracy_bonus_factor",
    "auto_turret_accuracy_multiplier",
    "player_base_accuracy",
    "npc_base_accuracy",
    # accuracy_clamp_min / accuracy_clamp_max — RETIRED rev 0031 (columns dropped; global-only)
    "scanner_tier_b_bonus_pp",
    "scanner_tier_c_bonus_pp",
    "ketar_i_repair_pct_per_sec",
    "ketar_ii_repair_pct_per_sec",
    # tick_ms / max_fight_ticks — RETIRED rev 0031 (columns dropped; global-only)
    "starting_distance_m",
    "base_ship_speed_mps",
    "min_distance_m",
    "thruster_window_m",
    # cloak_hp_thresholds_pct / booster_hp_thresholds_pct — RETIRED rev 0031 (CSV columns dropped)
    "emergency_system_invuln_s",
    "nuke_magnitude_scale",
    "nuke_friendly_factor",
    "pvc_damage_reduction",
    # combat_log_retention_hours — RETIRED rev 0031 (column dropped)
]


def test_guild_config_has_appendix_a_columns():
    col_names = {c.name for c in GuildConfig.__table__.columns}
    missing = [c for c in _APPENDIX_A_COLUMNS if c not in col_names]
    assert not missing, f"GuildConfig missing Appendix A columns: {missing}"


def test_guild_config_appendix_a_columns_all_nullable():
    cols = {c.name: c for c in GuildConfig.__table__.columns}
    non_nullable = [c for c in _APPENDIX_A_COLUMNS if not cols[c].nullable]
    assert not non_nullable, (
        f"Appendix A columns on GuildConfig must be nullable (NULL = use global default): {non_nullable}"
    )


# ---------------------------------------------------------------------------
# SQLite-backed table creation — validates DDL correctness
# ---------------------------------------------------------------------------

_COMBAT_LOG_TABLES = [CombatLog.__table__]


@pytest.fixture
async def sqlite_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_COMBAT_LOG_TABLES)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(sqlite_engine) -> AsyncSession:
    factory = async_sessionmaker(sqlite_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


async def test_combat_log_table_created_in_sqlite(sqlite_engine):
    """combat_log table can be created from ORM metadata (DDL is correct)."""
    async with sqlite_engine.connect() as conn:
        result = await conn.run_sync(lambda c: sa_inspect(c).get_table_names())
    assert "combat_log" in result


async def test_combat_log_indexes_created_in_sqlite(sqlite_engine):
    """Both single-column indexes are created on the SQLite combat_log table."""
    async with sqlite_engine.connect() as conn:
        indexes = await conn.run_sync(lambda c: sa_inspect(c).get_indexes("combat_log"))
    index_names = {idx["name"] for idx in indexes}
    assert "ix_combat_log_combatant1_user_id" in index_names
    assert "ix_combat_log_combatant2_user_id" in index_names


async def test_default_backfill_server_default_zero(sqlite_engine):
    """Player lifetime counter columns carry server_default='0' for existing-row backfill."""
    cols = {c.name: c for c in Player.__table__.columns}
    for col_name in ("total_fights", "total_nukes_fired", "total_module_activations"):
        sd = cols[col_name].server_default
        assert sd is not None
        # SQLAlchemy wraps the text; verify the raw value is "0"
        assert "0" in str(sd.arg)


# ---------------------------------------------------------------------------
# Real migration DDL — drives the actual 0011 upgrade()/downgrade() against a
# live Postgres inside an always-rolled-back transaction (0015/0016/0017
# pattern).  Uses a real alembic Operations bound to the live connection so the
# migration's op.create_table / add_column / create_index / drop_* calls run
# for real — the migration's own DDL is exercised, not just the ORM metadata.
# ---------------------------------------------------------------------------

import contextlib
import importlib.util

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.pg_env import PG_SYNC_URL as _PG_SYNC_URL
from tests.pg_env import pg_skip_reason

_PG_SKIP = pg_skip_reason()
_PG_MARK = pytest.mark.skipif(bool(_PG_SKIP), reason=_PG_SKIP or "")

_MIGRATION_0011_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "src",
        "persist",
        "database",
        "revisions",
        "versions",
        "0011_combat_log_and_phase1_stats.py",
    )
)


def _load_migration_0011():
    """Load the real 0011 migration module via importlib (avoids Alembic env)."""
    spec = importlib.util.spec_from_file_location("migration_0011_real", _MIGRATION_0011_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@contextlib.contextmanager
def _rollback_conn(engine: sa.engine.Engine):
    """Connection inside a transaction that is ALWAYS rolled back."""
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            yield conn
        finally:
            if trans.is_active:
                trans.rollback()


@_PG_MARK
class TestMigration0011RealDDL:
    """Drive the real 0011 upgrade()/downgrade() against live Postgres.

    All DDL runs inside a rolled-back transaction so the live schema (already at
    head) is untouched.  We downgrade first (dropping combat_log + the indexes)
    then upgrade again, so both directions of the shipped migration are proven.
    """

    @pytest.fixture(scope="class")
    def pg_engine(self):
        engine = sa.create_engine(_PG_SYNC_URL, echo=False)
        yield engine
        engine.dispose()

    def _op(self, conn: sa.engine.Connection) -> Operations:
        return Operations(MigrationContext.configure(conn))

    def test_downgrade_then_upgrade_round_trips_combat_log(self, pg_engine):
        """Real downgrade() drops combat_log; real upgrade() recreates it + indexes."""
        mod = _load_migration_0011()

        with _rollback_conn(pg_engine) as conn:
            conn.execute(sa.text("SET session_replication_role = 'replica'"))
            insp = sa.inspect(conn)
            assert insp.has_table("combat_log"), "precondition: combat_log exists at head"

            mod.op = self._op(conn)
            mod.downgrade()
            insp = sa.inspect(conn)
            assert not insp.has_table("combat_log"), "downgrade() must drop the combat_log table"

            mod.op = self._op(conn)
            mod.upgrade()
            insp = sa.inspect(conn)
            assert insp.has_table("combat_log"), "upgrade() must recreate the combat_log table"

            index_names = {i["name"] for i in insp.get_indexes("combat_log")}
            assert "ix_combat_log_combatant1_user_id" in index_names
            assert "ix_combat_log_combatant2_user_id" in index_names

    def test_upgrade_is_idempotent_at_head(self, pg_engine):
        """Real upgrade() on an already-migrated schema is a no-op (inspector guards)."""
        mod = _load_migration_0011()

        with _rollback_conn(pg_engine) as conn:
            conn.execute(sa.text("SET session_replication_role = 'replica'"))
            mod.op = self._op(conn)
            mod.upgrade()  # every guard sees the objects already present → no-op
            mod.upgrade()  # second run must also not raise

            insp = sa.inspect(conn)
            assert insp.has_table("combat_log")

    def test_upgrade_recreates_combat_log_columns(self, pg_engine):
        """After downgrade()+upgrade(), combat_log has the expected column set + nullability."""
        mod = _load_migration_0011()

        with _rollback_conn(pg_engine) as conn:
            conn.execute(sa.text("SET session_replication_role = 'replica'"))
            mod.op = self._op(conn)
            mod.downgrade()
            mod.op = self._op(conn)
            mod.upgrade()

            insp = sa.inspect(conn)
            cols = {c["name"]: c for c in insp.get_columns("combat_log")}
            required = {
                "id",
                "guild_id",
                "context",
                "combatant1_name",
                "combatant2_name",
                "combatant1_user_id",
                "combatant2_user_id",
                "winner_name",
                "is_stalemate",
                "data",
                "created_at",
            }
            assert required <= set(cols), f"Missing columns after upgrade: {required - set(cols)}"
            # NPC side is nullable; core identity columns are not.
            assert cols["combatant1_user_id"]["nullable"] is True
            assert cols["combatant2_user_id"]["nullable"] is True
            assert cols["guild_id"]["nullable"] is False
            assert cols["is_stalemate"]["nullable"] is False
