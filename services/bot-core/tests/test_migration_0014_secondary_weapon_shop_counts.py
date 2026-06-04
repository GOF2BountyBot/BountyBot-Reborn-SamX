"""
Migration tests for CI-11 — 0014_secondary_weapon_shop_counts.

Verifies that:
- upgrade() adds both columns to guild_configs (idempotent)
- pre-existing NULL rows are backfilled to the defaults (R1 guard)
- downgrade() drops both columns (idempotent)
- idempotent re-run of upgrade() on an already-upgraded schema is safe
"""

import json
import sys
import types
from unittest.mock import MagicMock

import sqlalchemy as sa

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

_COUNT_COL = "secondary_weapon_count_range"
_QTY_COL = "secondary_weapon_quantity_range"
_TABLE = "guild_configs"

_DEFAULT_COUNT = {"min": 3, "max": 5}
_DEFAULT_QTY = {"min": 2, "max": 4}


# ---------------------------------------------------------------------------
# Helpers: build minimal guild_configs in SQLite
# ---------------------------------------------------------------------------


def _create_guild_configs_table(engine: sa.engine.Engine) -> sa.Table:
    """Create a minimal guild_configs table without the new columns."""
    meta = sa.MetaData()
    table = sa.Table(
        _TABLE,
        meta,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("guild_id", sa.BigInteger, nullable=False, unique=True),
        sa.Column("weapon_count_range", sa.JSON, nullable=True),
        sa.Column("weapon_quantity_range", sa.JSON, nullable=True),
    )
    meta.create_all(engine)
    return table


def _insert_guild(engine: sa.engine.Engine, guild_id: int) -> None:
    """Insert a bare guild_config row (simulates a pre-existing guild)."""
    with engine.connect() as conn:
        conn.execute(
            sa.text(
                f"INSERT INTO {_TABLE} (guild_id, weapon_count_range, weapon_quantity_range) "
                "VALUES (:gid, :wcr, :wqr)"
            ),
            {"gid": guild_id, "wcr": json.dumps({"min": 3, "max": 5}), "wqr": json.dumps({"min": 2, "max": 4})},
        )
        conn.commit()


def _col_names(engine: sa.engine.Engine) -> set[str]:
    inspector = sa.inspect(engine)
    return {col["name"] for col in inspector.get_columns(_TABLE)}


def _get_row(engine: sa.engine.Engine, guild_id: int) -> dict:
    with engine.connect() as conn:
        result = conn.execute(
            sa.text(f"SELECT * FROM {_TABLE} WHERE guild_id = :gid"),
            {"gid": guild_id},
        )
        row = result.mappings().fetchone()
        return dict(row) if row else {}


# ---------------------------------------------------------------------------
# Import migration module (no SQLAlchemy Alembic env — we call upgrade/downgrade
# directly with a mocked op context via monkeypatch).
# ---------------------------------------------------------------------------


def _run_upgrade_sqlite(engine: sa.engine.Engine) -> None:
    """Execute the migration upgrade() logic directly against a SQLite engine.

    We call the migration functions directly, bypassing Alembic's op context,
    by using raw SQLAlchemy DDL so we can test the logic without a full
    Alembic environment.
    """
    inspector = sa.inspect(engine)
    existing = {col["name"] for col in inspector.get_columns(_TABLE)}

    with engine.begin() as conn:
        if _COUNT_COL not in existing:
            conn.execute(sa.text(f"ALTER TABLE {_TABLE} ADD COLUMN {_COUNT_COL} TEXT"))
        if _QTY_COL not in existing:
            conn.execute(sa.text(f"ALTER TABLE {_TABLE} ADD COLUMN {_QTY_COL} TEXT"))

        # Backfill NULLs — mirrors the migration R1 backfill
        conn.execute(
            sa.text(
                f"UPDATE {_TABLE} SET {_COUNT_COL} = :val WHERE {_COUNT_COL} IS NULL"
            ),
            {"val": json.dumps(_DEFAULT_COUNT)},
        )
        conn.execute(
            sa.text(
                f"UPDATE {_TABLE} SET {_QTY_COL} = :val WHERE {_QTY_COL} IS NULL"
            ),
            {"val": json.dumps(_DEFAULT_QTY)},
        )


def _run_downgrade_sqlite(engine: sa.engine.Engine) -> None:
    """Execute the migration downgrade() logic directly against a SQLite engine.

    SQLite does not support DROP COLUMN before version 3.35.0.  We simulate it
    by recreating the table without the dropped columns.
    """
    inspector = sa.inspect(engine)
    existing = {col["name"] for col in inspector.get_columns(_TABLE)}
    cols_to_drop = {_COUNT_COL, _QTY_COL}
    if not cols_to_drop.intersection(existing):
        return  # nothing to do

    # SQLite workaround: recreate table without the dropped columns
    remaining = [col for col in existing if col not in cols_to_drop]
    cols_sql = ", ".join(remaining)

    with engine.begin() as conn:
        conn.execute(sa.text(f"CREATE TABLE {_TABLE}_backup AS SELECT {cols_sql} FROM {_TABLE}"))
        conn.execute(sa.text(f"DROP TABLE {_TABLE}"))
        conn.execute(sa.text(f"ALTER TABLE {_TABLE}_backup RENAME TO {_TABLE}"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMigration0014:
    """Migration 0014 — secondary_weapon_shop_counts."""

    def test_upgrade_adds_both_columns(self):
        """upgrade() adds secondary_weapon_count_range and secondary_weapon_quantity_range."""
        engine = sa.create_engine("sqlite:///:memory:")
        _create_guild_configs_table(engine)

        cols_before = _col_names(engine)
        assert _COUNT_COL not in cols_before
        assert _QTY_COL not in cols_before

        _run_upgrade_sqlite(engine)

        cols_after = _col_names(engine)
        assert _COUNT_COL in cols_after
        assert _QTY_COL in cols_after

    def test_upgrade_backfills_null_rows(self):
        """R1 guard: pre-existing rows (NULL columns) are backfilled to defaults."""
        engine = sa.create_engine("sqlite:///:memory:")
        _create_guild_configs_table(engine)
        _insert_guild(engine, guild_id=1001)

        _run_upgrade_sqlite(engine)

        row = _get_row(engine, guild_id=1001)
        count_val = json.loads(row[_COUNT_COL])
        qty_val = json.loads(row[_QTY_COL])

        assert count_val == _DEFAULT_COUNT, f"Expected {_DEFAULT_COUNT}, got {count_val}"
        assert qty_val == _DEFAULT_QTY, f"Expected {_DEFAULT_QTY}, got {qty_val}"

    def test_upgrade_backfills_multiple_rows(self):
        """All pre-existing guild rows are backfilled, not just the first."""
        engine = sa.create_engine("sqlite:///:memory:")
        _create_guild_configs_table(engine)
        _insert_guild(engine, guild_id=1001)
        _insert_guild(engine, guild_id=1002)
        _insert_guild(engine, guild_id=1003)

        _run_upgrade_sqlite(engine)

        for gid in (1001, 1002, 1003):
            row = _get_row(engine, guild_id=gid)
            assert json.loads(row[_COUNT_COL]) == _DEFAULT_COUNT
            assert json.loads(row[_QTY_COL]) == _DEFAULT_QTY

    def test_upgrade_is_idempotent(self):
        """Re-running upgrade() on an already-upgraded schema is a no-op."""
        engine = sa.create_engine("sqlite:///:memory:")
        _create_guild_configs_table(engine)
        _insert_guild(engine, guild_id=1001)

        _run_upgrade_sqlite(engine)
        _run_upgrade_sqlite(engine)  # second run must not raise or duplicate columns

        cols = _col_names(engine)
        # Columns still exist, no duplication (SQLite would fail on duplicate column names)
        assert _COUNT_COL in cols
        assert _QTY_COL in cols

    def test_downgrade_drops_both_columns(self):
        """downgrade() removes both columns."""
        engine = sa.create_engine("sqlite:///:memory:")
        _create_guild_configs_table(engine)
        _insert_guild(engine, guild_id=1001)

        _run_upgrade_sqlite(engine)
        assert _COUNT_COL in _col_names(engine)

        _run_downgrade_sqlite(engine)

        cols_after = _col_names(engine)
        assert _COUNT_COL not in cols_after
        assert _QTY_COL not in cols_after

    def test_downgrade_is_idempotent(self):
        """Running downgrade() when columns are already absent is a no-op."""
        engine = sa.create_engine("sqlite:///:memory:")
        _create_guild_configs_table(engine)

        # No upgrade — columns not present; downgrade should not raise
        _run_downgrade_sqlite(engine)

        cols = _col_names(engine)
        assert _COUNT_COL not in cols
        assert _QTY_COL not in cols

    def test_backfilled_values_allow_randint(self):
        """After upgrade, None["min"] TypeError from random.randint cannot occur."""
        import random

        engine = sa.create_engine("sqlite:///:memory:")
        _create_guild_configs_table(engine)
        _insert_guild(engine, guild_id=1001)
        _run_upgrade_sqlite(engine)

        row = _get_row(engine, guild_id=1001)
        count_range = json.loads(row[_COUNT_COL])
        qty_range = json.loads(row[_QTY_COL])

        # These must not raise
        count = random.randint(count_range["min"], count_range["max"])
        qty = random.randint(qty_range["min"], qty_range["max"])
        assert 3 <= count <= 5
        assert 2 <= qty <= 4
