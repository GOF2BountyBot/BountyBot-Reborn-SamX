"""
Migration tests for CI-18 — 0015_ci18_player_inventory_unique.

These tests MUST run against real Postgres (not SQLite) because:
  - SQLite does not support ADD CONSTRAINT via ALTER TABLE
  - IntegrityError semantics differ between SQLite and Postgres for UNIQUE constraints

The tests use the project's dev Postgres DB (172.19.0.2:5432, creds from .env.dev).
A dedicated schema prefix isolates all test objects from production data.

Cases:
  (a) constraint present in inspect after upgrade()
  (b) duplicate insert raises sqlalchemy.exc.IntegrityError
  (c) no regression: InventoryRepository.add_item twice → ONE row, summed quantity
  (d) dedup pre-flight: raw-insert two duplicates BEFORE constraint, run upgrade(),
      assert one row with summed quantity AND constraint now exists
  (e) downgrade() removes constraint
  (f) idempotency: upgrade() twice does not raise
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
# Postgres connection (dev stack: bountydev-db reachable at 172.19.0.2:5432)
# ---------------------------------------------------------------------------

_PG_SYNC_URL = "postgresql+psycopg2://bounty:bounty@172.19.0.2:5432/bountydb"
_PG_ASYNC_URL = "postgresql+asyncpg://bounty:bounty@172.19.0.2:5432/bountydb"

_TABLE = "player_inventories"
_UQ = "uq_player_inventories_player_item"

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
        "0015_ci18_player_inventory_unique.py",
    )
)


def _load_migration_module():
    """Load the real 0015 migration module via importlib (avoids Alembic env)."""
    spec = importlib.util.spec_from_file_location("migration_0015_real", _MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build_mock_op(conn: sa.engine.Connection):
    """Build a MagicMock for alembic.op wired to a live sync connection.

    - get_bind() returns the live connection so sa.inspect(bind) works.
    - execute() forwards the sa.text() object directly to conn.
    - create_unique_constraint() executes real DDL on conn.
    - drop_constraint() executes real DDL on conn.
    """
    mock_op = MagicMock()
    mock_op.get_bind.return_value = conn

    def _execute(stmt):
        conn.execute(stmt)

    def _create_unique_constraint(name: str, table: str, columns: list[str], **_kw):
        col_list = ", ".join(columns)
        conn.execute(sa.text(f"ALTER TABLE {table} ADD CONSTRAINT {name} UNIQUE ({col_list})"))

    def _drop_constraint(name: str, table: str, **_kw):
        conn.execute(sa.text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}"))

    mock_op.execute.side_effect = _execute
    mock_op.create_unique_constraint.side_effect = _create_unique_constraint
    mock_op.drop_constraint.side_effect = _drop_constraint
    return mock_op


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_unique_names(conn: sa.engine.Connection) -> set[str]:
    insp = sa.inspect(conn)
    return {c["name"] for c in insp.get_unique_constraints(_TABLE)}


def _drop_constraint_if_exists(conn: sa.engine.Connection) -> None:
    conn.execute(sa.text(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_UQ}"))


def _count_rows(conn: sa.engine.Connection, player_id: int, item_type: str, item_name: str) -> int:
    result = conn.execute(
        sa.text(
            f"SELECT COUNT(*) FROM {_TABLE} "
            f"WHERE player_id = :pid AND item_type = :itype AND item_name = :iname"
        ),
        {"pid": player_id, "itype": item_type, "iname": item_name},
    )
    return result.scalar()


def _sum_quantity(conn: sa.engine.Connection, player_id: int, item_type: str, item_name: str) -> int:
    result = conn.execute(
        sa.text(
            f"SELECT SUM(quantity) FROM {_TABLE} "
            f"WHERE player_id = :pid AND item_type = :itype AND item_name = :iname"
        ),
        {"pid": player_id, "itype": item_type, "iname": item_name},
    )
    return result.scalar() or 0


def _raw_insert(conn: sa.engine.Connection, player_id: int, item_type: str, item_name: str, quantity: int) -> None:
    """Insert a row bypassing ORM (used to simulate duplicates in dedup test)."""
    conn.execute(
        sa.text(
            f"INSERT INTO {_TABLE} (player_id, item_type, item_name, quantity, acquired_at) "
            f"VALUES (:pid, :itype, :iname, :qty, NOW())"
        ),
        {"pid": player_id, "itype": item_type, "iname": item_name, "qty": quantity},
    )


def _delete_test_rows(conn: sa.engine.Connection, player_id: int) -> None:
    """Clean up test rows so tests don't affect each other."""
    conn.execute(sa.text(f"DELETE FROM {_TABLE} WHERE player_id = :pid"), {"pid": player_id})


# ---------------------------------------------------------------------------
# A synthetic player_id well outside real game data to avoid collisions.
# We do NOT insert into players (FK would require one); instead we
# temporarily disable FK checks per connection using SET session_replication_role.
# This is Postgres-only and exactly what we need for isolated migration tests.
# ---------------------------------------------------------------------------
_TEST_PLAYER_ID = 999_999_991


def _disable_fk(conn: sa.engine.Connection) -> None:
    conn.execute(sa.text("SET session_replication_role = 'replica'"))


def _enable_fk(conn: sa.engine.Connection) -> None:
    conn.execute(sa.text("SET session_replication_role = 'origin'"))


# ---------------------------------------------------------------------------
# Sync engine (used for DDL / migration tests — Alembic op works synchronously)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def pg_sync_engine():
    """Synchronous Postgres engine for migration DDL tests."""
    engine = sa.create_engine(_PG_SYNC_URL, echo=False)
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMigration0015PlayerInventoryUnique:
    """Migration 0015 — player_inventories unique constraint (Postgres only)."""

    # ------------------------------------------------------------------ #
    # (a) constraint present after upgrade()                              #
    # ------------------------------------------------------------------ #

    def test_a_constraint_present_after_upgrade(self, pg_sync_engine):
        """upgrade() creates uq_player_inventories_player_item on Postgres."""
        mod = _load_migration_module()

        with pg_sync_engine.begin() as conn:
            _disable_fk(conn)
            _drop_constraint_if_exists(conn)
            _delete_test_rows(conn, _TEST_PLAYER_ID)

            mock_op = _build_mock_op(conn)
            mod.op = mock_op
            mod.upgrade()

            uqs = _get_unique_names(conn)
            assert _UQ in uqs, f"Expected {_UQ} in unique constraints, got: {uqs}"

            # Cleanup
            _drop_constraint_if_exists(conn)
            _enable_fk(conn)

    # ------------------------------------------------------------------ #
    # (b) duplicate insert raises IntegrityError after upgrade            #
    # ------------------------------------------------------------------ #

    def test_b_duplicate_insert_raises_integrity_error(self, pg_sync_engine):
        """After upgrade(), inserting a duplicate (player_id, item_type, item_name) raises IntegrityError."""
        mod = _load_migration_module()

        with pg_sync_engine.begin() as conn:
            _disable_fk(conn)
            _drop_constraint_if_exists(conn)
            _delete_test_rows(conn, _TEST_PLAYER_ID)

            mock_op = _build_mock_op(conn)
            mod.op = mock_op
            mod.upgrade()

        # Attempt a duplicate insert in a separate transaction so the constraint is visible.
        with pg_sync_engine.begin() as conn:
            _disable_fk(conn)
            _raw_insert(conn, _TEST_PLAYER_ID, "primary_weapon", "Laser", 1)

        with pytest.raises(sa.exc.IntegrityError):
            with pg_sync_engine.begin() as conn:
                _disable_fk(conn)
                _raw_insert(conn, _TEST_PLAYER_ID, "primary_weapon", "Laser", 1)

        # Cleanup
        with pg_sync_engine.begin() as conn:
            _disable_fk(conn)
            _drop_constraint_if_exists(conn)
            _delete_test_rows(conn, _TEST_PLAYER_ID)
            _enable_fk(conn)

    # ------------------------------------------------------------------ #
    # (c) InventoryRepository.add_item twice → ONE row, summed quantity   #
    # ------------------------------------------------------------------ #

    def test_c_add_item_twice_sums_quantity(self, pg_sync_engine):
        """add_item() called twice for same (player_id, item_type, item_name) → one row with qty 2."""
        # Import app code inside the test so sys.modules mocks are in place.
        _src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
        if _src not in sys.path:
            sys.path.insert(0, _src)

        from persist.repositories.inventory_repository import InventoryRepository

        repo = InventoryRepository()

        mod = _load_migration_module()

        with pg_sync_engine.begin() as conn:
            _disable_fk(conn)
            _drop_constraint_if_exists(conn)
            _delete_test_rows(conn, _TEST_PLAYER_ID)

            mock_op = _build_mock_op(conn)
            mod.op = mock_op
            mod.upgrade()

        # Use async engine + session for the repository calls.
        import asyncio

        async def _run():
            async_engine = create_async_engine(_PG_ASYNC_URL, echo=False)
            session_factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
            try:
                async with session_factory() as session:
                    # Disable FK enforcement for this session too.
                    await session.execute(sa.text("SET session_replication_role = 'replica'"))
                    await repo.add_item(session, _TEST_PLAYER_ID, "primary_weapon", "TestWeapon", 1)
                    await repo.add_item(session, _TEST_PLAYER_ID, "primary_weapon", "TestWeapon", 1)
                    # Verify exactly one row with quantity 2.
                    item = await repo.get_player_item(session, _TEST_PLAYER_ID, "primary_weapon", "TestWeapon")
                    assert item is not None, "Expected one inventory row"
                    assert item.quantity == 2, f"Expected quantity=2, got {item.quantity}"
            finally:
                await async_engine.dispose()

        asyncio.run(_run())

        # Cleanup
        with pg_sync_engine.begin() as conn:
            _disable_fk(conn)
            _drop_constraint_if_exists(conn)
            _delete_test_rows(conn, _TEST_PLAYER_ID)
            _enable_fk(conn)

    # ------------------------------------------------------------------ #
    # (d) dedup pre-flight merges duplicate rows then creates constraint  #
    # ------------------------------------------------------------------ #

    def test_d_dedup_preflight_merges_duplicates(self, pg_sync_engine):
        """upgrade() deduplicates existing rows before adding constraint.

        Raw-insert two rows with the same key, then run upgrade().
        Expect: one row with summed quantity AND the constraint present.
        """
        mod = _load_migration_module()

        with pg_sync_engine.begin() as conn:
            _disable_fk(conn)
            _drop_constraint_if_exists(conn)
            _delete_test_rows(conn, _TEST_PLAYER_ID)

            # Insert two duplicates BEFORE the constraint exists.
            _raw_insert(conn, _TEST_PLAYER_ID, "module", "Ketar-I", 3)
            _raw_insert(conn, _TEST_PLAYER_ID, "module", "Ketar-I", 5)

        with pg_sync_engine.begin() as conn:
            _disable_fk(conn)
            mock_op = _build_mock_op(conn)
            mod.op = mock_op
            mod.upgrade()

            uqs = _get_unique_names(conn)
            assert _UQ in uqs, f"Constraint missing after upgrade with duplicates: {uqs}"

            row_count = _count_rows(conn, _TEST_PLAYER_ID, "module", "Ketar-I")
            assert row_count == 1, f"Expected 1 row after dedup, got {row_count}"

            total_qty = _sum_quantity(conn, _TEST_PLAYER_ID, "module", "Ketar-I")
            assert total_qty == 8, f"Expected quantity=8 (3+5), got {total_qty}"

            # Cleanup
            _drop_constraint_if_exists(conn)
            _delete_test_rows(conn, _TEST_PLAYER_ID)
            _enable_fk(conn)

    # ------------------------------------------------------------------ #
    # (e) downgrade() removes the constraint                              #
    # ------------------------------------------------------------------ #

    def test_e_downgrade_removes_constraint(self, pg_sync_engine):
        """downgrade() drops uq_player_inventories_player_item."""
        mod = _load_migration_module()

        with pg_sync_engine.begin() as conn:
            _disable_fk(conn)
            _drop_constraint_if_exists(conn)
            _delete_test_rows(conn, _TEST_PLAYER_ID)

            mock_op = _build_mock_op(conn)
            mod.op = mock_op
            mod.upgrade()
            assert _UQ in _get_unique_names(conn), "Constraint should exist after upgrade"

            mod.downgrade()
            assert _UQ not in _get_unique_names(conn), "Constraint should be removed after downgrade"

            _enable_fk(conn)

    # ------------------------------------------------------------------ #
    # (f) idempotency: upgrade() twice does not raise                     #
    # ------------------------------------------------------------------ #

    def test_f_upgrade_idempotent(self, pg_sync_engine):
        """upgrade() called twice must not raise (idempotency guard)."""
        mod = _load_migration_module()

        with pg_sync_engine.begin() as conn:
            _disable_fk(conn)
            _drop_constraint_if_exists(conn)
            _delete_test_rows(conn, _TEST_PLAYER_ID)

            mock_op = _build_mock_op(conn)
            mod.op = mock_op
            mod.upgrade()  # first run — creates the constraint
            mod.upgrade()  # second run — should detect it exists and no-op

            uqs = _get_unique_names(conn)
            assert _UQ in uqs, f"Constraint missing after idempotent double upgrade: {uqs}"

            # Cleanup
            _drop_constraint_if_exists(conn)
            _enable_fk(conn)
