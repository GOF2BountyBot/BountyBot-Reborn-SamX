"""
Migration tests for P4-T9 — 0017_p4t9_json_to_jsonb_fragile.

These tests MUST run against real Postgres (not SQLite) because:
  - SQLite has no JSONB type.
  - ALTER COLUMN ... TYPE JSONB USING ::jsonb is PostgreSQL-only DDL.
  - JSONB sub-path operators (->, ->>) are PostgreSQL-only.

The tests run against the Postgres resolved by tests/pg_env.py (CI service
container or the dev stack). All DDL lives inside a transaction that is ALWAYS
rolled back (_rollback_conn), so a test run leaves the database schema exactly
as it found it — at alembic head. The synthetic_player fixture commits its
rows (and deletes them in teardown) so they are visible to the tests' txns.

Cases:
  (a) upgrade(): every player_ships JSON column becomes 'jsonb' in information_schema.
  (b) downgrade(): every player_ships column reverts to 'json'.
  (c) player_inventories columns are NOT touched (model has no JSON columns).
  (d) value-identical round-trip: all player_ships JSON column values survive up+down.
  (e) array-order preserved: weapons/modules/turrets/secondary_weapons slot lists
      maintain element order after up+down (loadout slot order is gameplay-critical).
  (f) secondary_ammo dict round-trip: dict values and keys preserved after up+down.
  (g) SQLite table-creation smoke test: PlayerShip model still creates tables on SQLite
      using the with_variant approach (no JSONB error).
  (h) no orphaned JSONB index after upgrade+downgrade.
  (i) owned=cargo+equipped invariant: a synthetic player's loadout round-trips cleanly,
      confirming the invariant is preserved across the type change.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import json
import os
import sys
import types
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text

# ---------------------------------------------------------------------------
# Module-level mocks — must happen before any app code is imported.
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
        "0017_p4t9_json_to_jsonb_fragile.py",
    )
)

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))


def _load_migration_module():
    """Load the 0017 migration module via importlib (avoids Alembic env)."""
    spec = importlib.util.spec_from_file_location("migration_0017_real", _MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Expected columns: (table, column_name)
# ---------------------------------------------------------------------------

_PLAYER_SHIP_COLS = [
    ("player_ships", "weapons"),
    ("player_ships", "modules"),
    ("player_ships", "turrets"),
    ("player_ships", "secondary_weapons"),
    ("player_ships", "secondary_ammo"),
]

# Synthetic test IDs — chosen to be well outside real data ranges.
_TEST_GUILD_ID = 999_999_977
_TEST_USER_ID = 999_999_977_001


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
    schema-touching statement must go through this rollback-only connection.
    """
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            yield conn
        finally:
            if trans.is_active:
                trans.rollback()


@pytest.fixture(scope="function")
def synthetic_player(pg_sync_engine):
    """Insert a synthetic user + player + player_ship for round-trip tests.

    Yields (player_id, ship_id).
    Cleans up after the test regardless of pass/fail.
    """
    # Representative loadout: slot arrays with known ordering + ammo dict.
    _weapons = ["Micro Gun MK I", "Scatter Gun MK I"]
    _modules = ["Shield Booster MK I"]
    _turrets = ["Auto Turret MK I", "Plasma Turret MK I"]
    _secondary_weapons = ["Homing Missile"]
    _secondary_ammo = {"Homing Missile": 12}

    user_id = _TEST_USER_ID
    guild_id = _TEST_GUILD_ID
    player_id = None
    ship_id = None

    with pg_sync_engine.begin() as conn:
        _disable_fk(conn)
        # Insert user — include created_at/updated_at (no DB-level default; model uses Python default)
        conn.execute(
            text(
                "INSERT INTO users (id, discord_username, created_at, updated_at) "
                "VALUES (:uid, 'test_p4t9_user', NOW(), NOW()) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"uid": user_id},
        )
        # Insert player — supply all NOT NULL columns that have no DB-level default
        result = conn.execute(
            text(
                "INSERT INTO players "
                "(user_id, guild_id, credits, lifetime_credits, systems_checked, bounty_wins, "
                "xp, tier, prestige_count, duel_wins, duel_losses, duel_credits_won, "
                "duel_credits_lost, xp_surplus, classic_mode, created_at, updated_at) "
                "VALUES (:uid, :gid, 10000, 0, 0, 0, 0, 'Bronze', 0, 0, 0, 0, 0, 0, true, NOW(), NOW()) "
                "RETURNING id"
            ),
            {"uid": user_id, "gid": guild_id},
        )
        player_id = result.fetchone()[0]
        # Insert player_ship with representative loadout — include created_at
        result = conn.execute(
            text(
                "INSERT INTO player_ships "
                "(player_id, ship_name, is_active, weapons, modules, turrets, "
                "secondary_weapons, secondary_ammo, created_at) "
                "VALUES (:pid, 'Wasp', true, :w, :m, :t, :sw, :sa, NOW()) RETURNING id"
            ),
            {
                "pid": player_id,
                "w": json.dumps(_weapons),
                "m": json.dumps(_modules),
                "t": json.dumps(_turrets),
                "sw": json.dumps(_secondary_weapons),
                "sa": json.dumps(_secondary_ammo),
            },
        )
        ship_id = result.fetchone()[0]
        # Insert cargo to represent owned=cargo+equipped invariant
        # One copy of 'Micro Gun MK I' in cargo; one equipped in the weapons slot list.
        conn.execute(
            text(
                "INSERT INTO player_inventories (player_id, item_type, item_name, quantity, acquired_at) "
                "VALUES (:pid, 'primary_weapon', 'Micro Gun MK I', 1, NOW())"
            ),
            {"pid": player_id},
        )
        _enable_fk(conn)

    yield player_id, ship_id, _weapons, _modules, _turrets, _secondary_weapons, _secondary_ammo

    # Cleanup
    with pg_sync_engine.begin() as conn:
        _disable_fk(conn)
        conn.execute(text("UPDATE players SET active_ship_id = NULL WHERE id = :pid"), {"pid": player_id})
        conn.execute(text("DELETE FROM player_inventories WHERE player_id = :pid"), {"pid": player_id})
        conn.execute(text("DELETE FROM player_ships WHERE player_id = :pid"), {"pid": player_id})
        conn.execute(text("DELETE FROM players WHERE id = :pid"), {"pid": player_id})
        conn.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
        _enable_fk(conn)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMigration0017JsonToJsonbFragile:
    """P4-T9 migration — JSON → JSONB for fragile player_ships columns (Postgres only)."""

    # ------------------------------------------------------------------ #
    # (a) upgrade() changes every player_ships JSON column to jsonb       #
    # ------------------------------------------------------------------ #

    def test_a_upgrade_columns_become_jsonb(self, pg_sync_engine):
        """After upgrade(), all player_ships JSON columns report data_type='jsonb'."""
        mod = _load_migration_module()

        with _rollback_conn(pg_sync_engine) as conn:
            mock_op = _build_mock_op(conn)
            mod.op = mock_op
            mod.upgrade()

            for table, column in _PLAYER_SHIP_COLS:
                col_type = _col_type(conn, table, column)
                assert col_type == "jsonb", f"Expected {table}.{column} to be 'jsonb' after upgrade, got '{col_type}'"

    # ------------------------------------------------------------------ #
    # (b) downgrade() restores every player_ships column to json          #
    # ------------------------------------------------------------------ #

    def test_b_downgrade_columns_revert_to_json(self, pg_sync_engine):
        """After upgrade() + downgrade(), all player_ships columns report data_type='json'."""
        mod = _load_migration_module()

        with _rollback_conn(pg_sync_engine) as conn:
            mock_op = _build_mock_op(conn)
            mod.op = mock_op
            mod.upgrade()
            mod.downgrade()

            for table, column in _PLAYER_SHIP_COLS:
                col_type = _col_type(conn, table, column)
                assert col_type == "json", f"Expected {table}.{column} to be 'json' after downgrade, got '{col_type}'"

    # ------------------------------------------------------------------ #
    # (c) player_inventories has no JSON columns — migration skips it     #
    # ------------------------------------------------------------------ #

    def test_c_player_inventories_has_no_json_columns(self, pg_sync_engine):
        """Confirm player_inventories has no JSON/JSONB columns (by design).

        The PlayerInventory model uses Integer/String/DateTime only.
        This test asserts P4-T9 correctly excludes it.
        """
        with pg_sync_engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'player_inventories' "
                    "AND data_type IN ('json', 'jsonb')"
                )
            )
            json_cols = result.fetchall()
            assert json_cols == [], (
                f"player_inventories unexpectedly has JSON/JSONB columns: {json_cols}. "
                "The P4-T9 scope assumption (no JSON in player_inventories) is violated."
            )

    # ------------------------------------------------------------------ #
    # (d) value-identical round-trip: all player_ships JSON columns       #
    # ------------------------------------------------------------------ #

    def test_d_value_identical_roundtrip(self, pg_sync_engine, synthetic_player):
        """Values in all player_ships JSON columns survive upgrade+downgrade value-identically."""
        mod = _load_migration_module()
        _player_id, ship_id, *_ = synthetic_player

        def _fetch_ship(conn):
            result = conn.execute(
                text(
                    "SELECT weapons, modules, turrets, secondary_weapons, secondary_ammo "
                    "FROM player_ships WHERE id = :sid"
                ),
                {"sid": ship_id},
            )
            return result.fetchone()

        # Capture before state
        with pg_sync_engine.connect() as conn:
            before_row = _fetch_ship(conn)

        # Upgrade → downgrade → read back, all inside one rolled-back txn. The
        # casts are genuinely applied to the row within the transaction, so
        # the comparison still proves value fidelity.
        with _rollback_conn(pg_sync_engine) as conn:
            mock_op = _build_mock_op(conn)
            mod.op = mock_op
            mod.upgrade()
            mod.downgrade()

            after_row = _fetch_ship(conn)

        col_names = ["weapons", "modules", "turrets", "secondary_weapons", "secondary_ammo"]
        for col_name, b_val, a_val in zip(col_names, before_row, after_row, strict=True):
            b_parsed = json.loads(b_val) if isinstance(b_val, str) else b_val
            a_parsed = json.loads(a_val) if isinstance(a_val, str) else a_val
            assert b_parsed == a_parsed, (
                f"player_ships.{col_name} ship_id={ship_id}: value changed after up+down.\n"
                f"  before: {b_parsed!r}\n"
                f"  after:  {a_parsed!r}"
            )

    # ------------------------------------------------------------------ #
    # (e) array-order preserved: slot lists after up+down                 #
    # ------------------------------------------------------------------ #

    def test_e_array_order_preserved_slot_lists(self, pg_sync_engine, synthetic_player):
        """Loadout slot list element order is preserved through upgrade+downgrade.

        JSONB preserves array order — this is explicitly verified because loadout
        slot order is gameplay-critical (slot 0 != slot 1 for weapon assignments).
        """
        mod = _load_migration_module()
        _player_id, ship_id, exp_weapons, exp_modules, exp_turrets, exp_secondaries, _ = synthetic_player

        # Upgrade → downgrade → read back, all inside one rolled-back txn
        with _rollback_conn(pg_sync_engine) as conn:
            mock_op = _build_mock_op(conn)
            mod.op = mock_op
            mod.upgrade()
            mod.downgrade()

            result = conn.execute(
                text("SELECT weapons, modules, turrets, secondary_weapons FROM player_ships WHERE id = :sid"),
                {"sid": ship_id},
            )
            row = result.fetchone()
            assert row is not None, f"player_ships row {ship_id} not found after up+down"

        weapons_val, modules_val, turrets_val, sec_val = row
        weapons = json.loads(weapons_val) if isinstance(weapons_val, str) else weapons_val
        modules = json.loads(modules_val) if isinstance(modules_val, str) else modules_val
        turrets = json.loads(turrets_val) if isinstance(turrets_val, str) else turrets_val
        secondaries = json.loads(sec_val) if isinstance(sec_val, str) else sec_val

        assert weapons == exp_weapons, f"weapons order changed: expected {exp_weapons}, got {weapons}"
        assert modules == exp_modules, f"modules order changed: expected {exp_modules}, got {modules}"
        assert turrets == exp_turrets, f"turrets order changed: expected {exp_turrets}, got {turrets}"
        assert secondaries == exp_secondaries, (
            f"secondary_weapons order changed: expected {exp_secondaries}, got {secondaries}"
        )

    # ------------------------------------------------------------------ #
    # (f) secondary_ammo dict round-trip                                  #
    # ------------------------------------------------------------------ #

    def test_f_secondary_ammo_dict_roundtrip(self, pg_sync_engine, synthetic_player):
        """secondary_ammo dict values and keys are preserved after upgrade+downgrade.

        JSONB normalises object key order (not guaranteed by spec), but all keys
        and values must be present and correct.
        """
        mod = _load_migration_module()
        _player_id, ship_id, *_, exp_ammo = synthetic_player

        # Upgrade → downgrade → read back, all inside one rolled-back txn
        with _rollback_conn(pg_sync_engine) as conn:
            mock_op = _build_mock_op(conn)
            mod.op = mock_op
            mod.upgrade()
            mod.downgrade()

            result = conn.execute(
                text("SELECT secondary_ammo FROM player_ships WHERE id = :sid"),
                {"sid": ship_id},
            )
            row = result.fetchone()
            assert row is not None
            ammo_val = row[0]
            ammo = json.loads(ammo_val) if isinstance(ammo_val, str) else ammo_val

        assert ammo == exp_ammo, f"secondary_ammo changed after up+down.\n  expected: {exp_ammo}\n  got: {ammo}"

    # ------------------------------------------------------------------ #
    # (g) SQLite table-creation smoke test (with_variant approach)        #
    # ------------------------------------------------------------------ #

    def test_g_sqlite_table_creation_smoke(self):
        """PlayerShip model still creates tables on SQLite (with_variant keeps it working).

        This verifies that the JSON().with_variant(JSONB(), "postgresql") approach
        does not break the SQLite unit-test suite.
        """
        if _SRC_DIR not in sys.path:
            sys.path.insert(0, _SRC_DIR)

        from persist.models.base import Base
        from persist.models.player_ship import PlayerShip  # noqa: F401
        from sqlalchemy import create_engine as ce
        from sqlalchemy.pool import StaticPool

        sqlite_engine = ce(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        try:
            meta = sa.MetaData()
            # player_ships has a FK → players; players has a FK → users.
            # Include all three to satisfy FK resolution on SQLite.
            # None of these tables have ARRAY columns — all SQLite-compatible.
            sqlite_safe = {"users", "players", "player_ships"}
            for table in Base.metadata.sorted_tables:
                if table.name in sqlite_safe:
                    table.to_metadata(meta)
            meta.create_all(sqlite_engine)
        except Exception as exc:
            raise AssertionError(f"SQLite table creation failed — with_variant broken on PlayerShip: {exc}") from exc
        finally:
            sqlite_engine.dispose()

    # ------------------------------------------------------------------ #
    # (h) no orphaned JSONB index after upgrade+downgrade                 #
    # ------------------------------------------------------------------ #

    def test_h_no_orphaned_jsonb_index_after_downgrade(self, pg_sync_engine):
        """After upgrade+downgrade, no new JSONB-only indexes remain on player_ships.

        P4-T9 adds no JSONB indexes, so this is a safety check.
        """
        mod = _load_migration_module()

        def _get_player_ship_indexes(conn) -> set[str]:
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
                    "  AND t.relname = 'player_ships' "
                    "  AND tp.typname IN ('json', 'jsonb') "
                )
            )
            return {row[0] for row in result.fetchall()}

        with _rollback_conn(pg_sync_engine) as conn:
            indexes_before = _get_player_ship_indexes(conn)

            mock_op = _build_mock_op(conn)
            mod.op = mock_op
            mod.upgrade()
            mod.downgrade()

            indexes_after = _get_player_ship_indexes(conn)

        new_indexes = indexes_after - indexes_before
        assert not new_indexes, f"Orphaned JSONB-only indexes found after downgrade: {new_indexes}"

    # ------------------------------------------------------------------ #
    # (i) owned=cargo+equipped invariant preserved across type change     #
    # ------------------------------------------------------------------ #

    def test_i_owned_equals_cargo_plus_equipped(self, pg_sync_engine, synthetic_player):
        """owned=cargo+equipped invariant is preserved across upgrade+downgrade.

        The synthetic player has:
          - 1 cargo copy of 'Micro Gun MK I' in player_inventories
          - weapons=['Micro Gun MK I', 'Scatter Gun MK I'] in player_ships

        After up+down, owned(MicroGunMKI) = cargo(1) + equipped(1) = 2 must still hold.
        This is the critical invariant that has broken before in the loadout subsystem.
        """
        mod = _load_migration_module()
        player_id, ship_id, *_ = synthetic_player

        # Upgrade → downgrade → read back, all inside one rolled-back txn
        with _rollback_conn(pg_sync_engine) as conn:
            mock_op = _build_mock_op(conn)
            mod.op = mock_op
            mod.upgrade()
            mod.downgrade()

            # Read back cargo and equipped counts
            inv_result = conn.execute(
                text("SELECT quantity FROM player_inventories WHERE player_id = :pid AND item_name = 'Micro Gun MK I'"),
                {"pid": player_id},
            )
            inv_row = inv_result.fetchone()
            cargo_qty = inv_row[0] if inv_row else 0

            ship_result = conn.execute(
                text("SELECT weapons FROM player_ships WHERE id = :sid"),
                {"sid": ship_id},
            )
            ship_row = ship_result.fetchone()
            assert ship_row is not None, f"player_ships row {ship_id} missing after up+down"
            weapons_val = ship_row[0]
            weapons = json.loads(weapons_val) if isinstance(weapons_val, str) else weapons_val
            equipped_count = weapons.count("Micro Gun MK I")

        # owned = cargo + equipped must hold
        owned = cargo_qty + equipped_count
        assert owned == 2, (
            f"owned=cargo+equipped invariant broken after up+down: "
            f"cargo={cargo_qty}, equipped={equipped_count}, total={owned} (expected 2)"
        )
        assert cargo_qty == 1, f"Cargo qty changed unexpectedly: {cargo_qty}"
        assert equipped_count == 1, f"Equipped count changed unexpectedly: {equipped_count}"
