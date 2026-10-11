"""Migration tests for 0038_shop_refresh_schedule.

guild_configs gains 3 columns; guild_shops loses refresh_interval_hours.
Tests: upgrade adds/drops, idempotent, downgrade reverses, round-trip.
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

_MIGRATION_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "src",
        "persist",
        "database",
        "revisions",
        "versions",
        "0038_shop_refresh_schedule.py",
    )
)

_ADD_COLS = ("shop_refresh_interval_hours", "shop_refresh_jitter_pct", "shop_next_refresh_at")
_DROP_COL = "refresh_interval_hours"


def _load():
    spec = importlib.util.spec_from_file_location("migration_0038_real", _MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _engine():
    engine = create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(text("CREATE TABLE guild_configs (guild_id BIGINT PRIMARY KEY, prefix VARCHAR(10))"))
        c.execute(
            text(
                "CREATE TABLE guild_shops "
                "(id INTEGER PRIMARY KEY, guild_id BIGINT NOT NULL, "
                "tier VARCHAR(20) NOT NULL, refresh_interval_hours INTEGER NOT NULL DEFAULT 12)"
            )
        )
    return engine


def _gc_cols(engine) -> set[str]:
    return {c["name"] for c in sa.inspect(engine).get_columns("guild_configs")}


def _gs_cols(engine) -> set[str]:
    return {c["name"] for c in sa.inspect(engine).get_columns("guild_shops")}


def _mock_op(engine):
    op = MagicMock()
    op.get_bind.return_value = engine.connect()

    def add_column(table, column, **_):
        col_type = "DATETIME" if "DateTime" in type(column.type).__name__ else "INTEGER"
        with engine.begin() as c:
            c.execute(text(f"ALTER TABLE {table} ADD COLUMN {column.name} {col_type}"))

    def drop_column(table, col, **_):
        with engine.begin() as c:
            c.execute(text(f"ALTER TABLE {table} DROP COLUMN {col}"))

    op.add_column.side_effect = add_column
    op.drop_column.side_effect = drop_column
    return op


# ---------------------------------------------------------------------------
# SQLite structural tests
# ---------------------------------------------------------------------------


def test_upgrade_adds_gc_columns_and_drops_gs_column():
    engine, mod = _engine(), _load()
    mod.op = _mock_op(engine)
    mod.upgrade()
    gc = _gc_cols(engine)
    gs = _gs_cols(engine)
    for col in _ADD_COLS:
        assert col in gc, f"Expected guild_configs.{col} after upgrade"
    assert _DROP_COL not in gs, "Expected guild_shops.refresh_interval_hours dropped after upgrade"


def test_upgrade_is_idempotent():
    engine, mod = _engine(), _load()
    mod.op = _mock_op(engine)
    mod.upgrade()
    mod.op = _mock_op(engine)
    mod.upgrade()  # second run must be a no-op
    assert mod.op.add_column.call_count == 0
    assert mod.op.drop_column.call_count == 0


def test_downgrade_reverses_upgrade():
    engine, mod = _engine(), _load()
    mod.op = _mock_op(engine)
    mod.upgrade()
    mod.op = _mock_op(engine)
    mod.downgrade()
    gc = _gc_cols(engine)
    gs = _gs_cols(engine)
    for col in _ADD_COLS:
        assert col not in gc, f"Expected guild_configs.{col} removed after downgrade"
    assert _DROP_COL in gs, "Expected guild_shops.refresh_interval_hours restored after downgrade"


def test_downgrade_is_idempotent():
    engine, mod = _engine(), _load()
    mod.op = _mock_op(engine)
    mod.downgrade()  # no upgrade first — columns not there / _DROP_COL already present
    mod.op = _mock_op(engine)
    mod.downgrade()
    assert mod.op.add_column.call_count == 0
    assert mod.op.drop_column.call_count == 0


def test_round_trip():
    engine, mod = _engine(), _load()
    mod.op = _mock_op(engine)
    mod.upgrade()
    mod.op = _mock_op(engine)
    mod.downgrade()
    # After round-trip: back to baseline
    gc = _gc_cols(engine)
    gs = _gs_cols(engine)
    for col in _ADD_COLS:
        assert col not in gc
    assert _DROP_COL in gs
    # Upgrade again to verify repeatable
    mod.op = _mock_op(engine)
    mod.upgrade()
    gc = _gc_cols(engine)
    gs = _gs_cols(engine)
    for col in _ADD_COLS:
        assert col in gc
    assert _DROP_COL not in gs
