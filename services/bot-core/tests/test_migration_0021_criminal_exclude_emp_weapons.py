"""Migration tests for 0021_criminal_exclude_emp_weapons (BALANCE_JOURNAL §A Thread 6).

Verifies that:
- upgrade() adds the criminal_exclude_emp_weapons nullable column to guild_configs
  (idempotent via inspector guard)
- downgrade() drops the column (idempotent via inspector guard)
- Re-running upgrade()/downgrade() is a safe no-op + round-trips

Mirrors the 0020 migration test (SQLite structural + rollback-safe Postgres).
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

_TABLE = "guild_configs"
_NEW_COLS = ("criminal_exclude_emp_weapons",)

_MIGRATION_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "src",
        "persist",
        "database",
        "revisions",
        "versions",
        "0021_criminal_exclude_emp_weapons.py",
    )
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("migration_0021_real", _MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _create_guild_configs_table(engine: sa.engine.Engine) -> None:
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
    inspector = sa.inspect(engine)
    return {col["name"] for col in inspector.get_columns(_TABLE)}


def _build_mock_op(engine: sa.engine.Engine, conn: sa.engine.Connection) -> MagicMock:
    mock_op = MagicMock()
    mock_op.get_bind.return_value = conn

    def _add_column(table: str, column: sa.Column, **_kwargs):
        with engine.begin() as c:
            c.execute(text(f"ALTER TABLE {table} ADD COLUMN {column.name} TEXT"))

    def _drop_column(table: str, col_name: str, **_kwargs):
        inspector = sa.inspect(engine)
        existing = [col["name"] for col in inspector.get_columns(table)]
        if col_name not in existing:
            return
        remaining = [c for c in existing if c != col_name]
        cols_sql = ", ".join(remaining)
        with engine.begin() as c:
            c.execute(text(f"CREATE TABLE {table}_bak AS SELECT {cols_sql} FROM {table}"))
            c.execute(text(f"DROP TABLE {table}"))
            c.execute(text(f"ALTER TABLE {table}_bak RENAME TO {table}"))

    mock_op.add_column.side_effect = _add_column
    mock_op.drop_column.side_effect = _drop_column
    return mock_op


class TestMigration0021Structure:
    def test_upgrade_adds_column(self):
        engine = create_engine("sqlite:///:memory:")
        _create_guild_configs_table(engine)
        for col in _NEW_COLS:
            assert col not in _col_names(engine)

        mod = _load_migration_module()
        with engine.connect() as conn:
            mod.op = _build_mock_op(engine, conn)
            mod.upgrade()

        cols = _col_names(engine)
        for col in _NEW_COLS:
            assert col in cols, f"{col} missing after upgrade; got {cols}"

    def test_upgrade_is_idempotent(self):
        engine = create_engine("sqlite:///:memory:")
        _create_guild_configs_table(engine)
        mod = _load_migration_module()
        with engine.connect() as conn:
            mod.op = _build_mock_op(engine, conn)
            mod.upgrade()
            mod.upgrade()
        cols = _col_names(engine)
        for col in _NEW_COLS:
            assert col in cols

    def test_downgrade_drops_column(self):
        engine = create_engine("sqlite:///:memory:")
        _create_guild_configs_table(engine)
        mod = _load_migration_module()
        with engine.connect() as conn:
            mod.op = _build_mock_op(engine, conn)
            mod.upgrade()
        for col in _NEW_COLS:
            assert col in _col_names(engine)
        with engine.connect() as conn:
            mod.op = _build_mock_op(engine, conn)
            mod.downgrade()
        cols = _col_names(engine)
        for col in _NEW_COLS:
            assert col not in cols

    def test_downgrade_is_idempotent(self):
        engine = create_engine("sqlite:///:memory:")
        _create_guild_configs_table(engine)
        mod = _load_migration_module()
        with engine.connect() as conn:
            mod.op = _build_mock_op(engine, conn)
            mod.downgrade()
        cols = _col_names(engine)
        for col in _NEW_COLS:
            assert col not in cols

    def test_upgrade_then_downgrade_round_trips(self):
        engine = create_engine("sqlite:///:memory:")
        _create_guild_configs_table(engine)
        original = _col_names(engine)
        mod = _load_migration_module()
        with engine.connect() as conn:
            mod.op = _build_mock_op(engine, conn)
            mod.upgrade()
        with engine.connect() as conn:
            mod.op = _build_mock_op(engine, conn)
            mod.downgrade()
        assert _col_names(engine) == original


from tests.pg_env import PG_SYNC_URL as _PG_SYNC_URL
from tests.pg_env import pg_skip_reason

_PG_SKIP = pg_skip_reason()
_PG_MARK = pytest.mark.skipif(bool(_PG_SKIP), reason=_PG_SKIP or "")


@contextlib.contextmanager
def _rollback_conn(engine: sa.engine.Engine):
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            yield conn
        finally:
            if trans.is_active:
                trans.rollback()


def _col_nullable_pg(conn: sa.engine.Connection, table: str, column: str) -> str | None:
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
class TestMigration0021Postgres:
    @pytest.fixture(scope="class")
    def pg_engine(self):
        engine = create_engine(_PG_SYNC_URL, echo=False)
        yield engine
        engine.dispose()

    def test_upgrade_column_is_nullable(self, pg_engine):
        mod = _load_migration_module()
        with _rollback_conn(pg_engine) as conn:
            mod.op = _build_mock_op_pg(conn)
            mod.upgrade()
            for col in _NEW_COLS:
                assert _col_nullable_pg(conn, _TABLE, col) == "YES"

    def test_upgrade_is_idempotent_on_postgres(self, pg_engine):
        mod = _load_migration_module()
        with _rollback_conn(pg_engine) as conn:
            mod.op = _build_mock_op_pg(conn)
            mod.upgrade()
            mod.upgrade()
            for col in _NEW_COLS:
                assert _col_nullable_pg(conn, _TABLE, col) == "YES"

    def test_downgrade_drops_column(self, pg_engine):
        mod = _load_migration_module()
        with _rollback_conn(pg_engine) as conn:
            mod.op = _build_mock_op_pg(conn)
            mod.upgrade()
            mod.downgrade()
            for col in _NEW_COLS:
                assert _col_nullable_pg(conn, _TABLE, col) is None
