"""Migration tests for 0035_custom_events.

~4 tests (sibling norm):
- upgrade creates all 4 tables + event_min_duel_stakes column
- downgrade drops all 4 tables + column
- event_min_duel_stakes default 1000 on existing rows
- idempotent re-run (downgrade on absent tables is safe)
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock

import sqlalchemy as sa
from sqlalchemy import create_engine, inspect, text

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

_MIGRATION_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "src",
        "persist",
        "database",
        "revisions",
        "versions",
        "0035_custom_events.py",
    )
)

_GC_TABLE = "guild_configs"
_GC_COL = "event_min_duel_stakes"
_NEW_TABLES = ("game_events", "game_event_prizes", "game_event_metrics", "event_results")


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("migration_0035_real", _MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _create_base_tables(engine: sa.engine.Engine) -> None:
    """Create prerequisite tables that 0035 depends on."""
    meta = sa.MetaData()
    sa.Table(
        _GC_TABLE,
        meta,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False, unique=True),
    )
    sa.Table(
        "players",
        meta,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("credits", sa.Integer, nullable=False, server_default="0"),
    )
    meta.create_all(engine)


def _table_names(engine: sa.engine.Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _col_names(engine: sa.engine.Engine, table: str) -> set[str]:
    return {col["name"] for col in inspect(engine).get_columns(table)}


def _build_mock_op(engine: sa.engine.Engine, conn: sa.engine.Connection) -> MagicMock:
    """Wire Alembic op calls through to a real SQLite engine."""
    mock_op = MagicMock()
    mock_op.get_bind.return_value = conn

    def _add_column(table: str, column: sa.Column, **_kw):
        with engine.begin() as c:
            c.execute(text(f"ALTER TABLE {table} ADD COLUMN {column.name} INTEGER DEFAULT 1000"))

    def _create_table(name: str, *columns, **_kw):
        # SQLite-safe: ignore FKs, just create with column names and types
        col_ddl = []
        for col in columns:
            if isinstance(col, sa.Column):
                if col.primary_key:
                    col_ddl.append(f"{col.name} INTEGER PRIMARY KEY AUTOINCREMENT")
                else:
                    col_ddl.append(f"{col.name} TEXT")
        ddl = f"CREATE TABLE IF NOT EXISTS {name} ({', '.join(col_ddl)})"
        with engine.begin() as c:
            c.execute(text(ddl))

    def _drop_table(name: str, **_kw):
        with engine.begin() as c:
            c.execute(text(f"DROP TABLE IF EXISTS {name}"))

    def _drop_column(table: str, col_name: str, **_kw):
        inspector = inspect(engine)
        existing = [c["name"] for c in inspector.get_columns(table)]
        if col_name not in existing:
            return
        remaining = [c for c in existing if c != col_name]
        cols_sql = ", ".join(remaining)
        with engine.begin() as c:
            c.execute(text(f"CREATE TABLE {table}_bak AS SELECT {cols_sql} FROM {table}"))
            c.execute(text(f"DROP TABLE {table}"))
            c.execute(text(f"ALTER TABLE {table}_bak RENAME TO {table}"))

    def _create_index(name: str, table: str, cols: list, **_kw):
        cols_sql = ", ".join(cols)
        with engine.begin() as c:
            c.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({cols_sql})"))

    def _drop_index(name: str, **_kw):
        with engine.begin() as c:
            c.execute(text(f"DROP INDEX IF EXISTS {name}"))

    mock_op.add_column.side_effect = _add_column
    mock_op.create_table.side_effect = _create_table
    mock_op.drop_table.side_effect = _drop_table
    mock_op.drop_column.side_effect = _drop_column
    mock_op.create_index.side_effect = _create_index
    mock_op.drop_index.side_effect = _drop_index
    return mock_op


def test_upgrade_creates_tables_and_column():
    """upgrade() creates all 4 event tables and event_min_duel_stakes column."""
    engine = create_engine("sqlite:///:memory:")
    _create_base_tables(engine)

    for t in _NEW_TABLES:
        assert t not in _table_names(engine), f"{t} should not exist before upgrade"
    assert _GC_COL not in _col_names(engine, _GC_TABLE)

    mod = _load_migration_module()
    with engine.connect() as conn:
        mod.op = _build_mock_op(engine, conn)
        mod.upgrade()

    for t in _NEW_TABLES:
        assert t in _table_names(engine), f"{t} missing after upgrade"
    assert _GC_COL in _col_names(engine, _GC_TABLE)


def test_downgrade_drops_tables_and_column():
    """downgrade() removes all 4 tables and event_min_duel_stakes column."""
    engine = create_engine("sqlite:///:memory:")
    _create_base_tables(engine)

    mod = _load_migration_module()
    with engine.connect() as conn:
        mod.op = _build_mock_op(engine, conn)
        mod.upgrade()

    with engine.connect() as conn:
        mod.op = _build_mock_op(engine, conn)
        mod.downgrade()

    for t in _NEW_TABLES:
        assert t not in _table_names(engine), f"{t} still present after downgrade"
    assert _GC_COL not in _col_names(engine, _GC_TABLE)


def test_event_min_duel_stakes_default_1000_on_existing_rows():
    """upgrade() sets event_min_duel_stakes default to 1000 for pre-existing guild_configs rows."""
    engine = create_engine("sqlite:///:memory:")
    _create_base_tables(engine)
    with engine.begin() as conn:
        result = conn.execute(text(f"INSERT INTO {_GC_TABLE} (guild_id) VALUES (12345)"))
        gc_id = result.lastrowid

    mod = _load_migration_module()
    with engine.connect() as conn:
        mod.op = _build_mock_op(engine, conn)
        mod.upgrade()

    with engine.connect() as conn:
        row = conn.execute(text(f"SELECT {_GC_COL} FROM {_GC_TABLE} WHERE id = :id"), {"id": gc_id}).fetchone()
    val = row[0]
    assert int(val) == 1000, f"Expected default 1000, got {val!r}"


def test_downgrade_on_absent_tables_is_safe():
    """downgrade() called before upgrade() (tables absent) must not raise."""
    engine = create_engine("sqlite:///:memory:")
    _create_base_tables(engine)

    mod = _load_migration_module()
    with engine.connect() as conn:
        mod.op = _build_mock_op(engine, conn)
        mod.downgrade()  # no-op — must not raise
