"""Migration tests for 0023_shop_combat_module_prob.

Verifies that:
- upgrade() adds the nullable shop_combat_module_prob Float column to guild_configs
  (idempotent via inspector guard)
- downgrade() drops the column (idempotent via inspector guard)
- The REAL upgrade()/downgrade() functions run without error (smoke test)
- Re-running upgrade()/downgrade() is a safe no-op

SQLite approach (matching test_migration_0022 pattern):
- Structural tests use an in-memory SQLite engine with a minimal guild_configs
  table lacking the new column.
- The _build_mock_op helper wires op.get_bind() to a real SQLite connection so
  sa.inspect(bind) works correctly.

Postgres path (matching test_migration_0022 pattern):
- When the Postgres test DB is reachable, additional tests run directly against
  it inside rolled-back transactions (DDL is transactional on Postgres) to
  verify the column is nullable. These leave ZERO trace on the live DB.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text

# ---------------------------------------------------------------------------
# Module-level mocks (same pattern as other migration tests).
# ---------------------------------------------------------------------------

if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

if "sqlalchemy_utils" not in sys.modules:
    _mock_sqla_utils = types.ModuleType("sqlalchemy_utils")
    _mock_sqla_utils.UUIDType = MagicMock()
    sys.modules["sqlalchemy_utils"] = _mock_sqla_utils

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TABLE = "guild_configs"
_NEW_COLS = ("shop_combat_module_prob",)

# ---------------------------------------------------------------------------
# Migration module loader
# ---------------------------------------------------------------------------

_MIGRATION_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "src",
        "persist",
        "database",
        "revisions",
        "versions",
        "0023_shop_combat_module_prob.py",
    )
)


def _load_migration_module():
    """Load the 0023 migration module via importlib (avoids Alembic env)."""
    spec = importlib.util.spec_from_file_location("migration_0023_real", _MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------


def _create_guild_configs_table(engine: sa.engine.Engine) -> None:
    """Create a minimal guild_configs table WITHOUT the new override column."""
    meta = sa.MetaData()
    sa.Table(
        _TABLE,
        meta,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("starting_credits", sa.Integer, nullable=False, server_default="0"),
    )
    meta.create_all(engine)


def _col_names(engine: sa.engine.Engine) -> set[str]:
    """Return the set of column names in the guild_configs table."""
    inspector = sa.inspect(engine)
    return {col["name"] for col in inspector.get_columns(_TABLE)}


def _build_mock_op(engine: sa.engine.Engine, conn: sa.engine.Connection) -> MagicMock:
    """Build a MagicMock that stands in for ``alembic.op``, wired to the given connection.

    - ``get_bind()`` returns the live connection so ``sa.inspect(bind)`` works.
    - ``add_column()`` executes a real ALTER TABLE on ``engine``.
    - ``drop_column()`` recreates the table without the dropped column (SQLite workaround).
    """
    mock_op = MagicMock()
    mock_op.get_bind.return_value = conn

    def _add_column(table: str, column: sa.Column, **_kwargs):
        with engine.begin() as c:
            c.execute(text(f"ALTER TABLE {table} ADD COLUMN {column.name} TEXT"))

    def _drop_column(table: str, col_name: str, **_kwargs):
        """SQLite workaround: recreate table without the dropped column."""
        inspector = sa.inspect(engine)
        existing = [col["name"] for col in inspector.get_columns(table)]
        if col_name not in existing:
            return  # idempotent no-op
        remaining = [c for c in existing if c != col_name]
        cols_sql = ", ".join(remaining)
        with engine.begin() as c:
            c.execute(text(f"CREATE TABLE {table}_bak AS SELECT {cols_sql} FROM {table}"))
            c.execute(text(f"DROP TABLE {table}"))
            c.execute(text(f"ALTER TABLE {table}_bak RENAME TO {table}"))

    mock_op.add_column.side_effect = _add_column
    mock_op.drop_column.side_effect = _drop_column
    return mock_op


# ---------------------------------------------------------------------------
# Tests: structural (SQLite)
# ---------------------------------------------------------------------------


class TestMigration0023Structure:
    """0023 structural tests using in-memory SQLite.

    These verify add/drop idempotency and column existence without requiring
    a live Postgres instance.
    """

    def test_upgrade_adds_column(self):
        """upgrade() adds the shop_combat_module_prob column."""
        engine = create_engine("sqlite:///:memory:")
        _create_guild_configs_table(engine)

        for col in _NEW_COLS:
            assert col not in _col_names(engine)

        mod = _load_migration_module()
        with engine.connect() as conn:
            mock_op = _build_mock_op(engine, conn)
            mod.op = mock_op
            mod.upgrade()

        cols = _col_names(engine)
        for col in _NEW_COLS:
            assert col in cols, f"{col} missing after upgrade; got {cols}"

    def test_upgrade_is_idempotent(self):
        """Running upgrade() twice on an already-upgraded schema is a no-op (no error)."""
        engine = create_engine("sqlite:///:memory:")
        _create_guild_configs_table(engine)

        mod = _load_migration_module()
        with engine.connect() as conn:
            mock_op = _build_mock_op(engine, conn)
            mod.op = mock_op
            mod.upgrade()  # first run adds column
            mod.upgrade()  # second run: inspector sees column exists, skips add_column

        cols = _col_names(engine)
        for col in _NEW_COLS:
            assert col in cols

    def test_downgrade_drops_column(self):
        """downgrade() removes the shop_combat_module_prob column."""
        engine = create_engine("sqlite:///:memory:")
        _create_guild_configs_table(engine)

        mod = _load_migration_module()
        with engine.connect() as conn:
            mock_op = _build_mock_op(engine, conn)
            mod.op = mock_op
            mod.upgrade()

        for col in _NEW_COLS:
            assert col in _col_names(engine)

        with engine.connect() as conn:
            mock_op = _build_mock_op(engine, conn)
            mod.op = mock_op
            mod.downgrade()

        cols = _col_names(engine)
        for col in _NEW_COLS:
            assert col not in cols, f"{col} still present after downgrade; got {cols}"

    def test_downgrade_is_idempotent(self):
        """Running downgrade() when column is absent is a no-op (no error)."""
        engine = create_engine("sqlite:///:memory:")
        _create_guild_configs_table(engine)

        mod = _load_migration_module()
        with engine.connect() as conn:
            mock_op = _build_mock_op(engine, conn)
            mod.op = mock_op
            # Column not present — downgrade should not raise
            mod.downgrade()

        cols = _col_names(engine)
        for col in _NEW_COLS:
            assert col not in cols

    def test_upgrade_then_downgrade_round_trips(self):
        """upgrade() then downgrade() returns the schema to its original column set."""
        engine = create_engine("sqlite:///:memory:")
        _create_guild_configs_table(engine)
        original = _col_names(engine)

        mod = _load_migration_module()
        with engine.connect() as conn:
            mock_op = _build_mock_op(engine, conn)
            mod.op = mock_op
            mod.upgrade()
        with engine.connect() as conn:
            mock_op = _build_mock_op(engine, conn)
            mod.op = mock_op
            mod.downgrade()

        assert _col_names(engine) == original


# ---------------------------------------------------------------------------
# Tests: Postgres (rollback-safe)
# ---------------------------------------------------------------------------

from tests.pg_env import PG_SYNC_URL as _PG_SYNC_URL
from tests.pg_env import pg_skip_reason

_PG_SKIP = pg_skip_reason()
_PG_MARK = pytest.mark.skipif(bool(_PG_SKIP), reason=_PG_SKIP or "")


@contextlib.contextmanager
def _rollback_conn(engine: sa.engine.Engine):
    """Connection inside a transaction that is ALWAYS rolled back.

    Postgres DDL is transactional, so upgrade()/downgrade() exercised through
    this connection leaves zero trace in the target database.
    """
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            yield conn
        finally:
            if trans.is_active:
                trans.rollback()


def _col_nullable_pg(conn: sa.engine.Connection, table: str, column: str) -> str | None:
    """Return 'YES' or 'NO' for is_nullable, or None if the column is absent."""
    result = conn.execute(
        text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :tbl AND column_name = :col"
        ),
        {"tbl": table, "col": column},
    )
    row = result.fetchone()
    return row[0] if row else None


def _build_mock_op_pg(conn: sa.engine.Connection) -> MagicMock:
    """Build a MagicMock for alembic.op wired to a live Postgres connection."""
    mock_op = MagicMock()
    mock_op.get_bind.return_value = conn

    def _add_column(table: str, column: sa.Column, **_kwargs):
        col_type = column.type.compile(dialect=conn.dialect)
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column.name} {col_type}"))

    def _drop_column(table: str, col_name: str, **_kwargs):
        conn.execute(text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {col_name}"))

    mock_op.add_column.side_effect = _add_column
    mock_op.drop_column.side_effect = _drop_column
    return mock_op


@_PG_MARK
class TestMigration0023Postgres:
    """0023 Postgres tests — run against the live Postgres, always rolled back.

    All DDL runs inside a transaction that is rolled back at the end, leaving
    the live DB untouched.
    """

    @pytest.fixture(scope="class")
    def pg_engine(self):
        engine = create_engine(_PG_SYNC_URL, echo=False)
        yield engine
        engine.dispose()

    def test_upgrade_column_is_nullable(self, pg_engine):
        """After upgrade(), shop_combat_module_prob exists and is nullable."""
        mod = _load_migration_module()

        with _rollback_conn(pg_engine) as conn:
            mock_op = _build_mock_op_pg(conn)
            mod.op = mock_op
            mod.upgrade()

            for col in _NEW_COLS:
                nullable = _col_nullable_pg(conn, _TABLE, col)
                assert nullable == "YES", f"Expected nullable for {_TABLE}.{col}, nullable={nullable!r}"

    def test_upgrade_is_idempotent_on_postgres(self, pg_engine):
        """Running upgrade() twice on Postgres is a no-op (IF NOT EXISTS guard)."""
        mod = _load_migration_module()

        with _rollback_conn(pg_engine) as conn:
            mock_op = _build_mock_op_pg(conn)
            mod.op = mock_op
            mod.upgrade()
            mod.upgrade()  # second call must not raise

            for col in _NEW_COLS:
                assert _col_nullable_pg(conn, _TABLE, col) == "YES"

    def test_downgrade_drops_column(self, pg_engine):
        """After upgrade() + downgrade(), shop_combat_module_prob is absent."""
        mod = _load_migration_module()

        with _rollback_conn(pg_engine) as conn:
            mock_op = _build_mock_op_pg(conn)
            mod.op = mock_op
            mod.upgrade()
            mod.downgrade()

            for col in _NEW_COLS:
                nullable = _col_nullable_pg(conn, _TABLE, col)
                assert nullable is None, f"Column {_TABLE}.{col} still present after downgrade"
