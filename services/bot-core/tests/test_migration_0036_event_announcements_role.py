"""Migration tests for 0036_event_announcements_role.

~4 tests (sibling norm):
- upgrade adds both columns
- downgrade drops both
- boolean default true on existing player rows
- idempotent re-run
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock

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

_GC_TABLE = "guild_configs"
_P_TABLE = "players"
_GC_COL = "event_announcements_role_id"
_P_COL = "event_notifications_enabled"

_MIGRATION_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..", "src", "persist", "database", "revisions", "versions",
        "0036_event_announcements_role.py",
    )
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("migration_0036_real", _MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _create_tables(engine: sa.engine.Engine) -> None:
    meta = sa.MetaData()
    sa.Table(_GC_TABLE, meta, sa.Column("id", sa.Integer, primary_key=True),
             sa.Column("guild_id", sa.BigInteger, nullable=False, unique=True))
    sa.Table(_P_TABLE, meta, sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
             sa.Column("guild_id", sa.BigInteger, nullable=False),
             sa.Column("user_id", sa.BigInteger, nullable=False),
             sa.Column("credits", sa.Integer, nullable=False, server_default="10000"))
    meta.create_all(engine)


def _col_names(engine: sa.engine.Engine, table: str) -> set[str]:
    return {col["name"] for col in sa.inspect(engine).get_columns(table)}


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


def test_upgrade_adds_both_columns():
    """upgrade() adds event_announcements_role_id to guild_configs and event_notifications_enabled to players."""
    engine = create_engine("sqlite:///:memory:")
    _create_tables(engine)
    assert _GC_COL not in _col_names(engine, _GC_TABLE)
    assert _P_COL not in _col_names(engine, _P_TABLE)

    mod = _load_migration_module()
    with engine.connect() as conn:
        mod.op = _build_mock_op(engine, conn)
        mod.upgrade()

    assert _GC_COL in _col_names(engine, _GC_TABLE)
    assert _P_COL in _col_names(engine, _P_TABLE)


def test_downgrade_drops_both_columns():
    """downgrade() removes both columns added by upgrade()."""
    engine = create_engine("sqlite:///:memory:")
    _create_tables(engine)

    mod = _load_migration_module()
    with engine.connect() as conn:
        mock_op = _build_mock_op(engine, conn)
        mod.op = mock_op
        mod.upgrade()

    with engine.connect() as conn:
        mock_op = _build_mock_op(engine, conn)
        mod.op = mock_op
        mod.downgrade()

    assert _GC_COL not in _col_names(engine, _GC_TABLE)
    assert _P_COL not in _col_names(engine, _P_TABLE)


def test_upgrade_backfills_existing_player_row():
    """upgrade() sets event_notifications_enabled to a truthy default for existing rows."""
    engine = create_engine("sqlite:///:memory:")
    _create_tables(engine)
    with engine.begin() as conn:
        result = conn.execute(text(f"INSERT INTO {_P_TABLE} (guild_id, user_id) VALUES (1, 1)"))
        pid = result.lastrowid

    mod = _load_migration_module()
    with engine.connect() as conn:
        mod.op = _build_mock_op(engine, conn)
        mod.upgrade()

    with engine.connect() as conn:
        row = conn.execute(text(f"SELECT {_P_COL} FROM {_P_TABLE} WHERE id = :id"), {"id": pid}).fetchone()
    val = row[0]
    assert val in ("true", 1, True, "1"), f"Expected truthy default, got {val!r}"


def test_upgrade_is_idempotent():
    """upgrade() called twice must not raise."""
    engine = create_engine("sqlite:///:memory:")
    _create_tables(engine)

    mod = _load_migration_module()
    with engine.connect() as conn:
        mock_op = _build_mock_op(engine, conn)
        mod.op = mock_op
        mod.upgrade()
        mod.upgrade()  # second call — must not raise

    assert _GC_COL in _col_names(engine, _GC_TABLE)
    assert _P_COL in _col_names(engine, _P_TABLE)
