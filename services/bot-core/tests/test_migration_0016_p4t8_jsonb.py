"""
Migration tests for P4-T8 — 0016_p4t8_json_to_jsonb_non_fragile.

These tests MUST run against real Postgres (not SQLite) because:
  - SQLite has no JSONB type.
  - ALTER COLUMN ... TYPE JSONB USING ::jsonb is PostgreSQL-only DDL.
  - JSONB sub-path operators (->, ->>) are PostgreSQL-only.

The tests run against the Postgres resolved by tests/pg_env.py (CI service
container or the dev stack). All DDL and synthetic rows live inside a
transaction that is ALWAYS rolled back (_rollback_conn), so a test run leaves
the database schema and data exactly as it found them — at alembic head.

Cases:
  (a) upgrade(): every affected column becomes 'jsonb' in information_schema.
  (b) downgrade(): every affected column reverts to 'json'.
  (c) JSONB sub-path operator resolves against combat_log.data after upgrade.
  (d) value-identical round-trip: all affected column values survive up+down.
  (e) array-order preserved: bounty.route list element order unchanged after up+down.
  (f) idempotent downgrade: running downgrade() on a JSON column is a no-op (CAST ok).
  (g) SQLite table-creation smoke test: all affected models still create tables on SQLite.
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
# Module-level mocks (shared / sqlalchemy_utils must be present before
# any app code is imported at collection time).
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
# Postgres connection — resolved from POSTGRES_* env vars (CI service
# container) with the bountydev-db docker-bridge dev stack as the fallback.
# ---------------------------------------------------------------------------

from tests.pg_env import PG_SYNC_URL as _PG_SYNC_URL
from tests.pg_env import pg_skip_reason

_PG_SKIP = pg_skip_reason()
pytestmark = pytest.mark.skipif(bool(_PG_SKIP), reason=_PG_SKIP or "")

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
        "0016_p4t8_json_to_jsonb_non_fragile.py",
    )
)

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))


def _load_migration_module():
    """Load the 0016 migration module via importlib (avoids Alembic env)."""
    spec = importlib.util.spec_from_file_location("migration_0016_real", _MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Expected columns: (table, column_name)
# ---------------------------------------------------------------------------

_EXPECTED_COLUMNS = [
    ("combat_log", "data"),
    ("guild_configs", "ship_count_range"),
    ("guild_configs", "weapon_count_range"),
    ("guild_configs", "secondary_weapon_count_range"),
    ("guild_configs", "module_count_range"),
    ("guild_configs", "turret_count_range"),
    ("guild_configs", "ship_quantity_range"),
    ("guild_configs", "weapon_quantity_range"),
    ("guild_configs", "secondary_weapon_quantity_range"),
    ("guild_configs", "module_quantity_range"),
    ("guild_configs", "turret_quantity_range"),
    ("guild_configs", "tech_level_probabilities"),
    ("guild_configs", "xp_thresholds"),
    ("guild_configs", "division_temperatures"),
    ("guild_configs", "bounty_max_per_tier"),
    ("guild_configs", "division_max_tl"),
    ("bounty", "route"),
    ("bounty", "checked"),
    ("bounty", "criminal_ship"),
    ("ship", "extra_atts"),
    ("commodity", "extra_atts"),
    ("weapon", "extra_atts"),
    ("module", "extra_atts"),
]

# Columns that must NOT be touched by this migration (fragile; P4-T9 scope):
_UNTOUCHED_PLAYER_SHIP_COLS = [
    ("player_ships", "weapons"),
    ("player_ships", "modules"),
    ("player_ships", "turrets"),
    ("player_ships", "secondary_weapons"),
    ("player_ships", "secondary_ammo"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _col_type(conn: sa.engine.Connection, table: str, column: str) -> str:
    """Return the data_type for a column from information_schema."""
    result = conn.execute(
        text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :tbl AND column_name = :col"
        ),
        {"tbl": table, "col": column},
    )
    row = result.fetchone()
    if row is None:
        raise AssertionError(f"Column {table}.{column} not found in information_schema")
    return row[0]


def _build_mock_op(conn: sa.engine.Connection):
    """Build a MagicMock for alembic.op wired to a live sync connection."""
    mock_op = MagicMock()
    mock_op.get_bind.return_value = conn

    def _alter_column(table: str, column: str, type_=None, postgresql_using: str | None = None, **_kw):
        if type_ is None:
            raise ValueError("type_ is required")
        # Get the actual SQL type string from the SQLAlchemy type object.
        # JSONB → 'JSONB'; JSON → 'JSON'
        from sqlalchemy.dialects.postgresql import JSONB as _JSONB_TYPE

        sql_type = "JSONB" if isinstance(type_, _JSONB_TYPE) else "JSON"
        using_clause = f" USING {postgresql_using}" if postgresql_using else ""
        conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {sql_type}{using_clause}"))

    mock_op.alter_column.side_effect = _alter_column
    return mock_op


def _disable_fk(conn: sa.engine.Connection) -> None:
    conn.execute(text("SET session_replication_role = 'replica'"))


def _enable_fk(conn: sa.engine.Connection) -> None:
    conn.execute(text("SET session_replication_role = 'origin'"))


# ---------------------------------------------------------------------------
# Test player IDs / keys well outside real data ranges
# ---------------------------------------------------------------------------

_TEST_GUILD_ID = 999_999_991
_TEST_BOUNTY_ID_SEED = 0  # We'll use BIGSERIAL; track the inserted ID


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def pg_sync_engine():
    """Synchronous Postgres engine for migration DDL tests."""
    engine = create_engine(_PG_SYNC_URL, echo=False)
    yield engine
    engine.dispose()


@contextlib.contextmanager
def _rollback_conn(engine):
    """Connection inside a transaction that is ALWAYS rolled back.

    Postgres DDL is transactional, so upgrade()/downgrade() exercised through
    this connection leaves zero trace in the target database. These tests
    previously committed their final state, silently drifting whatever DB they
    ran against away from alembic head (columns reverted to json) — every
    statement must go through this rollback-only connection instead.
    """
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            yield conn
        finally:
            if trans.is_active:
                trans.rollback()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMigration0016JsonToJsonb:
    """P4-T8 migration — JSON → JSONB for non-fragile columns (Postgres only)."""

    # ------------------------------------------------------------------ #
    # (a) upgrade() changes every affected column to jsonb                #
    # ------------------------------------------------------------------ #

    def test_a_upgrade_columns_become_jsonb(self, pg_sync_engine):
        """After upgrade(), all affected columns report data_type='jsonb'."""
        mod = _load_migration_module()

        with _rollback_conn(pg_sync_engine) as conn:
            mock_op = _build_mock_op(conn)
            mod.op = mock_op
            mod.upgrade()

            for table, column in _EXPECTED_COLUMNS:
                col_type = _col_type(conn, table, column)
                assert col_type == "jsonb", f"Expected {table}.{column} to be 'jsonb' after upgrade, got '{col_type}'"

    # ------------------------------------------------------------------ #
    # (b) downgrade() restores every affected column to json              #
    # ------------------------------------------------------------------ #

    def test_b_downgrade_columns_revert_to_json(self, pg_sync_engine):
        """After upgrade() + downgrade(), all affected columns report data_type='json'."""
        mod = _load_migration_module()

        with _rollback_conn(pg_sync_engine) as conn:
            mock_op = _build_mock_op(conn)
            mod.op = mock_op
            mod.upgrade()
            mod.downgrade()

            for table, column in _EXPECTED_COLUMNS:
                col_type = _col_type(conn, table, column)
                assert col_type == "json", f"Expected {table}.{column} to be 'json' after downgrade, got '{col_type}'"

    # ------------------------------------------------------------------ #
    # (c) player_ships columns remain json (untouched by P4-T8)           #
    # ------------------------------------------------------------------ #

    def test_c_player_ship_cols_untouched(self, pg_sync_engine):
        """player_ships.* columns must not be MODIFIED by 0016 — they are P4-T9 scope.

        Relative assertion: whatever type the columns have before upgrade()
        (jsonb at head, json on a pre-0017 DB), they must be identical after.
        """
        mod = _load_migration_module()

        with _rollback_conn(pg_sync_engine) as conn:
            types_before = {(t, c): _col_type(conn, t, c) for t, c in _UNTOUCHED_PLAYER_SHIP_COLS}

            mock_op = _build_mock_op(conn)
            mod.op = mock_op
            mod.upgrade()

            for table, column in _UNTOUCHED_PLAYER_SHIP_COLS:
                col_type = _col_type(conn, table, column)
                assert col_type == types_before[(table, column)], (
                    f"{table}.{column} changed from '{types_before[(table, column)]}' to "
                    f"'{col_type}' — 0016 must not touch P4-T9-scope columns"
                )

    # ------------------------------------------------------------------ #
    # (d) JSONB sub-path operator resolves against combat_log.data        #
    # ------------------------------------------------------------------ #

    def test_d_jsonb_subpath_operator_resolves(self, pg_sync_engine):
        """After upgrade, data->'summary' resolves without error on combat_log.data.

        This is a capability check for P4-T7b (sub-path select); the actual
        read-path optimization assertion lives in P4-T7b. Here we only
        confirm the operator works at the DB level.

        We insert a synthetic row, run the sub-path query, then clean up.
        """
        mod = _load_migration_module()
        test_guild_id = _TEST_GUILD_ID

        import json

        data_blob = json.dumps(
            {
                "schema_version": 1,
                "summary": {"combatants": {"1": {"ship": "TestShip1"}, "2": {"ship": "TestShip2"}}},
                "metadata": {"fight_id": "test-p4t8-subpath"},
                "key_events": ["event_a", "event_b"],
                "timeline": [{"tick": 1, "type": "weapon_fire"}],
            }
        )

        with _rollback_conn(pg_sync_engine) as conn:
            mock_op = _build_mock_op(conn)
            mod.op = mock_op
            mod.upgrade()

            _disable_fk(conn)

            # Insert a synthetic combat_log row (rolled back with everything else)
            result = conn.execute(
                text(
                    "INSERT INTO combat_log "
                    "(guild_id, context, combatant1_name, combatant2_name, "
                    "winner_name, is_stalemate, data, created_at) "
                    "VALUES (:gid, 'test_p4t8', 'Fighter1', 'Fighter2', "
                    "'Fighter1', false, :data, NOW()) "
                    "RETURNING id"
                ),
                {"gid": test_guild_id, "data": data_blob},
            )
            inserted_id = result.fetchone()[0]

            # JSONB sub-path queries see the in-transaction type change —
            # Postgres DDL is visible to the transaction that performed it.
            result = conn.execute(
                text("SELECT data->'summary' FROM combat_log WHERE id = :id"),
                {"id": inserted_id},
            )
            row = result.fetchone()
            assert row is not None, "Expected a row from combat_log sub-path query"
            summary = row[0]
            assert summary is not None, "data->'summary' should not be null"
            # The summary contains 'combatants'
            assert "combatants" in summary, f"Expected 'combatants' in summary, got: {summary}"

            # Also verify data->'key_events' works
            result2 = conn.execute(
                text("SELECT data->'key_events' FROM combat_log WHERE id = :id"),
                {"id": inserted_id},
            )
            ke_row = result2.fetchone()
            assert ke_row is not None
            key_events = ke_row[0]
            assert key_events == ["event_a", "event_b"], f"Expected ['event_a', 'event_b'], got {key_events}"

    # ------------------------------------------------------------------ #
    # (e) value-identical round-trip: all affected columns                #
    # ------------------------------------------------------------------ #

    def test_e_value_identical_roundtrip(self, pg_sync_engine):
        """Values in all affected columns survive upgrade+downgrade value-identically.

        We capture the current values before upgrade, then compare after downgrade.
        NULL columns are skipped. This tests real seed data in the dev DB.
        """
        mod = _load_migration_module()

        def _fetch_values(conn, table, column):
            """Return list of (id, parsed_value) for non-null rows."""
            # Try common PK names: id, then first column
            try:
                result = conn.execute(text(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL LIMIT 10"))
            except Exception:
                result = conn.execute(
                    text(f"SELECT 1 AS id, {column} FROM {table} WHERE {column} IS NOT NULL LIMIT 10")
                )
            return result.fetchall()

        # Capture before state
        before_values: dict[tuple[str, str], list] = {}
        with pg_sync_engine.connect() as conn:
            for table, column in _EXPECTED_COLUMNS:
                rows = _fetch_values(conn, table, column)
                before_values[(table, column)] = rows

        # Upgrade → downgrade → compare, all inside one rolled-back txn. The
        # casts are genuinely applied to the rows within the transaction, so
        # the comparison still proves value fidelity.
        import json

        with _rollback_conn(pg_sync_engine) as conn:
            mock_op = _build_mock_op(conn)
            mod.op = mock_op
            mod.upgrade()
            mod.downgrade()

            for table, column in _EXPECTED_COLUMNS:
                after_rows = _fetch_values(conn, table, column)
                before_rows = before_values[(table, column)]

                assert len(after_rows) == len(before_rows), (
                    f"{table}.{column}: row count changed: {len(before_rows)} → {len(after_rows)}"
                )
                for (b_id, b_val), (a_id, a_val) in zip(before_rows, after_rows, strict=True):
                    assert b_id == a_id, f"{table}.{column}: row id changed: {b_id} → {a_id}"
                    # Both may come back as dict/list (psycopg2 auto-parses JSON/JSONB)
                    # or as str; normalize to Python objects for comparison
                    b_parsed = json.loads(b_val) if isinstance(b_val, str) else b_val
                    a_parsed = json.loads(a_val) if isinstance(a_val, str) else a_val
                    assert b_parsed == a_parsed, (
                        f"{table}.{column} id={b_id}: value changed after up+down.\n"
                        f"  before: {b_parsed!r}\n"
                        f"  after:  {a_parsed!r}"
                    )

    # ------------------------------------------------------------------ #
    # (f) array-order preserved: bounty.route                             #
    # ------------------------------------------------------------------ #

    def test_f_array_order_preserved_bounty_route(self, pg_sync_engine):
        """bounty.route list element order is preserved through upgrade+downgrade.

        JSONB normalizes object key order but MUST preserve array order.
        This test inserts a synthetic bounty with a known route, then
        verifies order is identical after up+down.
        """
        mod = _load_migration_module()

        import json

        test_route = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon"]
        test_checked = {s: -1 for s in test_route}

        with _rollback_conn(pg_sync_engine) as conn:
            _disable_fk(conn)
            # Insert synthetic bounty — all NOT NULL columns must be supplied.
            # The row is rolled back with the rest of the transaction.
            result = conn.execute(
                text(
                    "INSERT INTO bounty "
                    "(guild_id, division, criminal_name, route, answer, reward, reward_per_sys, "
                    "checked, tech_level, status, issue_time, escape_count, created_at, updated_at) "
                    "VALUES (:gid, 'bronze', 'TestCriminal', :route, 'Alpha', 1000, 200, "
                    ":checked, 5, 'active', NOW(), 0, NOW(), NOW()) "
                    "RETURNING id"
                ),
                {
                    "gid": _TEST_GUILD_ID,
                    "route": json.dumps(test_route),
                    "checked": json.dumps(test_checked),
                },
            )
            bounty_id = result.fetchone()[0]

            # Upgrade then downgrade — the casts are applied to the row in-txn
            mock_op = _build_mock_op(conn)
            mod.op = mock_op
            mod.upgrade()
            mod.downgrade()

            # Read back route and verify order preserved
            result = conn.execute(
                text("SELECT route FROM bounty WHERE id = :id"),
                {"id": bounty_id},
            )
            row = result.fetchone()
            assert row is not None, "Synthetic bounty row not found after up+down"
            route_val = row[0]
            route_parsed = json.loads(route_val) if isinstance(route_val, str) else route_val
            assert route_parsed == test_route, (
                f"Array order changed after up+down.\n  expected: {test_route}\n  got:      {route_parsed}"
            )

    # ------------------------------------------------------------------ #
    # (g) SQLite table-creation smoke test (models still work on SQLite)  #
    # ------------------------------------------------------------------ #

    def test_g_sqlite_table_creation_smoke(self):
        """All affected model classes still create tables on SQLite (no JSONB error).

        This verifies the with_variant approach keeps SQLite working for
        the unit-test suite.
        """
        if _SRC_DIR not in sys.path:
            sys.path.insert(0, _SRC_DIR)

        from persist.models.base import Base
        from persist.models.bounty import Bounty  # noqa: F401
        from persist.models.combat_log import CombatLog  # noqa: F401
        from persist.models.commodity import Commodity  # noqa: F401
        from persist.models.guild_config import GuildConfig  # noqa: F401
        from persist.models.module import Module  # noqa: F401
        from persist.models.ship import Ship  # noqa: F401
        from persist.models.weapon import Weapon  # noqa: F401
        from sqlalchemy import create_engine as ce
        from sqlalchemy.pool import StaticPool

        sqlite_engine = ce(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        # This must not raise — if with_variant is wrong, SQLite will complain
        # about unknown JSONB type.
        try:
            # Only create tables that are SQLite-compatible (no ARRAY columns).
            # Ship, Weapon, Module have ARRAY columns — skip them for SQLite.
            # But the with_variant must still be importable without error.
            meta = sa.MetaData()
            # Create only the JSON-bearing tables without ARRAY columns:
            for table in Base.metadata.sorted_tables:
                if table.name in ("bounty", "combat_log", "guild_configs"):
                    table.to_metadata(meta)
            meta.create_all(sqlite_engine)
        except Exception as exc:
            raise AssertionError(f"SQLite table creation failed — with_variant broken: {exc}") from exc
        finally:
            sqlite_engine.dispose()

    # ------------------------------------------------------------------ #
    # (h) no orphaned JSONB index after downgrade                         #
    # ------------------------------------------------------------------ #

    def test_h_no_orphaned_jsonb_index_after_downgrade(self, pg_sync_engine):
        """After upgrade+downgrade, no new JSONB-only indexes remain.

        P4-T8 adds no JSONB indexes, so this is a safety check.
        """
        mod = _load_migration_module()

        def _get_json_col_indexes(conn) -> set[str]:
            """Return index names on JSON/JSONB columns in affected tables."""
            tables = {t for t, _ in _EXPECTED_COLUMNS}
            result = conn.execute(
                text(
                    "SELECT i.relname AS index_name "
                    "FROM pg_index ix "
                    "JOIN pg_class t ON t.oid = ix.indrelid "
                    "JOIN pg_class i ON i.oid = ix.indexrelid "
                    "JOIN pg_namespace n ON n.oid = t.relnamespace "
                    "JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(ix.indkey) "
                    "JOIN pg_type tp ON tp.oid = a.atttypid "
                    "WHERE n.nspname = 'public' "
                    "  AND t.relname = ANY(:tables) "
                    "  AND tp.typname IN ('json', 'jsonb') "
                ),
                {"tables": list(tables)},
            )
            return {row[0] for row in result.fetchall()}

        with _rollback_conn(pg_sync_engine) as conn:
            indexes_before = _get_json_col_indexes(conn)

            mock_op = _build_mock_op(conn)
            mod.op = mock_op
            mod.upgrade()
            mod.downgrade()

            indexes_after = _get_json_col_indexes(conn)

        new_indexes = indexes_after - indexes_before
        assert not new_indexes, f"Orphaned JSONB-only indexes found after downgrade: {new_indexes}"
