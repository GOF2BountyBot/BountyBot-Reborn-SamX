"""Migration tests for 0036_event_announcements_role.

Verifies:
- upgrade() adds event_announcements_role_id to guild_configs (nullable BigInteger)
- upgrade() adds event_notifications_enabled to players (NOT NULL, default true)
- Both ops are idempotent (inspector guard)
- downgrade() removes both columns
- Existing player rows backfill to true

SQLite structural tests + optional Postgres rollback-safe tests.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text

# ---------------------------------------------------------------------------
# Module-level mocks
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

_GC_TABLE = "guild_configs"
_P_TABLE = "players"
_GC_COL = "event_announcements_role_id"
_P_COL = "event_notifications_enabled"

_MIGRATION_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "src",
        "persist",
        "database",
        "revisions",
        "versions",
        "0036_event_announcements_role.py",
    )
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("migration_0036_real", _MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------


def _create_tables(engine: sa.engine.Engine) -> None:
    meta = sa.MetaData()
    sa.Table(
        _GC_TABLE,
        meta,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False, unique=True),
    )
    sa.Table(
        _P_TABLE,
        meta,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("credits", sa.Integer, nullable=False, server_default="10000"),
    )
    meta.create_all(engine)


def _col_names(engine: sa.engine.Engine, table: str) -> set[str]:
    inspector = sa.inspect(engine)
    return {col["name"] for col in inspector.get_columns(table)}


def _insert_player(engine: sa.engine.Engine) -> int:
    with engine.begin() as conn:
        result = conn.execute(text(f"INSERT INTO {_P_TABLE} (guild_id, user_id) VALUES (1, 1)"))
        return result.lastrowid


def _get_player(engine: sa.engine.Engine, player_id: int) -> dict:
    with engine.connect() as conn:
        row = conn.execute(text(f"SELECT * FROM {_P_TABLE} WHERE id = :id"), {"id": player_id}).mappings().fetchone()
        return dict(row) if row else {}


def _build_mock_op(engine: sa.engine.Engine, conn: sa.engine.Connection) -> MagicMock:
    mock_op = MagicMock()
    mock_op.get_bind.return_value = conn

    def _add_column(table: str, column: sa.Column, **_kw):
        default_val = "'true'" if "bool" in str(column.type).lower() else "NULL"
        with engine.begin() as c:
            c.execute(text(f"ALTER TABLE {table} ADD COLUMN {column.name} TEXT DEFAULT {default_val}"))

    def _drop_column(table: str, col_name: str, **_kw):
        inspector = sa.inspect(engine)
        existing = [c["name"] for c in inspector.get_columns(table)]
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


# ---------------------------------------------------------------------------
# Tests: structural (SQLite)
# ---------------------------------------------------------------------------


class TestMigration0036Structure:
    def test_upgrade_adds_gc_column(self):
        engine = create_engine("sqlite:///:memory:")
        _create_tables(engine)
        assert _GC_COL not in _col_names(engine, _GC_TABLE)

        mod = _load_migration_module()
        with engine.connect() as conn:
            mod.op = _build_mock_op(engine, conn)
            mod.upgrade()

        assert _GC_COL in _col_names(engine, _GC_TABLE), f"{_GC_COL} missing from {_GC_TABLE}"

    def test_upgrade_adds_player_column(self):
        engine = create_engine("sqlite:///:memory:")
        _create_tables(engine)
        assert _P_COL not in _col_names(engine, _P_TABLE)

        mod = _load_migration_module()
        with engine.connect() as conn:
            mod.op = _build_mock_op(engine, conn)
            mod.upgrade()

        assert _P_COL in _col_names(engine, _P_TABLE), f"{_P_COL} missing from {_P_TABLE}"

    def test_upgrade_is_idempotent(self):
        engine = create_engine("sqlite:///:memory:")
        _create_tables(engine)

        mod = _load_migration_module()
        with engine.connect() as conn:
            mock_op = _build_mock_op(engine, conn)
            mod.op = mock_op
            mod.upgrade()
            mod.upgrade()  # second call must not raise

        assert _GC_COL in _col_names(engine, _GC_TABLE)
        assert _P_COL in _col_names(engine, _P_TABLE)

    def test_downgrade_drops_both_columns(self):
        engine = create_engine("sqlite:///:memory:")
        _create_tables(engine)

        mod = _load_migration_module()
        with engine.connect() as conn:
            mock_op = _build_mock_op(engine, conn)
            mod.op = mock_op
            mod.upgrade()

        assert _GC_COL in _col_names(engine, _GC_TABLE)
        assert _P_COL in _col_names(engine, _P_TABLE)

        with engine.connect() as conn:
            mock_op = _build_mock_op(engine, conn)
            mod.op = mock_op
            mod.downgrade()

        assert _GC_COL not in _col_names(engine, _GC_TABLE)
        assert _P_COL not in _col_names(engine, _P_TABLE)

    def test_downgrade_is_idempotent(self):
        engine = create_engine("sqlite:///:memory:")
        _create_tables(engine)

        mod = _load_migration_module()
        with engine.connect() as conn:
            mock_op = _build_mock_op(engine, conn)
            mod.op = mock_op
            mod.downgrade()  # columns absent — must not raise

    def test_upgrade_backfills_existing_player_row(self):
        engine = create_engine("sqlite:///:memory:")
        _create_tables(engine)
        pid = _insert_player(engine)

        mod = _load_migration_module()
        with engine.connect() as conn:
            mock_op = _build_mock_op(engine, conn)
            mod.op = mock_op
            mod.upgrade()

        row = _get_player(engine, pid)
        assert _P_COL in row, f"{_P_COL} missing from row after upgrade"
        val = row[_P_COL]
        assert val in ("true", 1, True, "1"), f"Expected truthy default, got {val!r}"


# ---------------------------------------------------------------------------
# Tests: Postgres (rollback-safe)
# ---------------------------------------------------------------------------

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


def _col_type_pg(conn: sa.engine.Connection, table: str, column: str) -> str | None:
    row = conn.execute(
        text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :tbl AND column_name = :col"
        ),
        {"tbl": table, "col": column},
    ).fetchone()
    return row[0] if row else None


def _col_nullable_pg(conn: sa.engine.Connection, table: str, column: str) -> str | None:
    row = conn.execute(
        text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :tbl AND column_name = :col"
        ),
        {"tbl": table, "col": column},
    ).fetchone()
    return row[0] if row else None


def _build_mock_op_pg(conn: sa.engine.Connection) -> MagicMock:
    mock_op = MagicMock()
    mock_op.get_bind.return_value = conn

    def _add_column(table: str, column: sa.Column, **_kw):
        if hasattr(column.type, "precision"):  # BigInteger / Numeric
            type_ddl = "BIGINT"
        elif str(column.type).upper().startswith("BOOL"):
            nullable_kw = "" if column.nullable else "NOT NULL"
            default_kw = f"DEFAULT {column.server_default.arg}" if column.server_default else ""
            conn.execute(
                text(
                    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
                    f"{column.name} BOOLEAN {nullable_kw} {default_kw}"
                )
            )
            return
        else:
            type_ddl = "BIGINT"
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column.name} {type_ddl}"))

    def _drop_column(table: str, col_name: str, **_kw):
        conn.execute(text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {col_name}"))

    mock_op.add_column.side_effect = _add_column
    mock_op.drop_column.side_effect = _drop_column
    return mock_op


@_PG_MARK
class TestMigration0036Postgres:
    @pytest.fixture(scope="class")
    def pg_engine(self):
        engine = create_engine(_PG_SYNC_URL, echo=False)
        yield engine
        engine.dispose()

    def test_upgrade_player_column_is_boolean_not_null(self, pg_engine):
        mod = _load_migration_module()
        with _rollback_conn(pg_engine) as conn:
            mod.op = _build_mock_op_pg(conn)
            mod.upgrade()
            dtype = _col_type_pg(conn, _P_TABLE, _P_COL)
            nullable = _col_nullable_pg(conn, _P_TABLE, _P_COL)
            assert dtype == "boolean", f"Expected boolean, got {dtype!r}"
            assert nullable == "NO", f"Expected NOT NULL, got {nullable!r}"

    def test_upgrade_gc_column_is_bigint_nullable(self, pg_engine):
        mod = _load_migration_module()
        with _rollback_conn(pg_engine) as conn:
            mod.op = _build_mock_op_pg(conn)
            mod.upgrade()
            dtype = _col_type_pg(conn, _GC_TABLE, _GC_COL)
            nullable = _col_nullable_pg(conn, _GC_TABLE, _GC_COL)
            assert dtype == "bigint", f"Expected bigint, got {dtype!r}"
            assert nullable == "YES", f"Expected nullable, got {nullable!r}"

    def test_upgrade_is_idempotent(self, pg_engine):
        mod = _load_migration_module()
        with _rollback_conn(pg_engine) as conn:
            mod.op = _build_mock_op_pg(conn)
            mod.upgrade()
            mod.upgrade()

    def test_downgrade_drops_both(self, pg_engine):
        mod = _load_migration_module()
        with _rollback_conn(pg_engine) as conn:
            mod.op = _build_mock_op_pg(conn)
            mod.upgrade()
            mod.downgrade()
            assert _col_type_pg(conn, _P_TABLE, _P_COL) is None
            assert _col_type_pg(conn, _GC_TABLE, _GC_COL) is None
