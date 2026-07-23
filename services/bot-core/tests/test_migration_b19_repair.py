"""
Migration tests for Package G B.19 — 0002_b19_repair_loadout_consistency.

These tests drive the REAL migration ``upgrade()`` against a live Postgres
(the same rolled-back-transaction pattern proven by the 0015/0016/0017 suites),
NOT an in-test reimplementation of the repair contract.  Postgres is required
because the migration's dedup pass reads/writes the ``player_ships`` JSONB slot
columns with ``CAST(:val AS json)`` and orders by ``is_active DESC`` — SQLite's
JSON semantics and the empirical corrupt state differ enough that only the real
engine exercises the shipped SQL faithfully.

Builds the empirical B.19 corrupt state (Betty/Hera/Terran with E2 Exoclad and
Telta Quickscan duplicated across all three ships) via raw inserts for a
synthetic player, runs the real ``upgrade()`` through a thin op translator, and
asserts the repair migration:

  - removes the duplicate slot references
  - keeps each (item_name, kind) on exactly one ship
  - preserves the active ship's references (active wins)
  - is idempotent (re-running on already-clean data is a no-op)
  - down-migration is a real no-op (per spec) that does not alter data

All DDL/DML runs inside a transaction that is ALWAYS rolled back, so the tests
leave zero trace on the target database.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import sys
import types
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa

# ---------------------------------------------------------------------------
# Module-level mocks (mirroring the other migration test modules).
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
# Postgres connection — resolved from POSTGRES_* env vars.
# ---------------------------------------------------------------------------
from tests.pg_env import PG_SYNC_URL as _PG_SYNC_URL
from tests.pg_env import pg_skip_reason

_PG_SKIP = pg_skip_reason()
pytestmark = pytest.mark.skipif(bool(_PG_SKIP), reason=_PG_SKIP or "")

_TABLE = "player_ships"

# Synthetic ids well outside real game data to avoid collisions.  Ordering
# matters to the migration (active first, then id ascending), so the ids are
# chosen so that Hera < Terran.
_TEST_PLAYER_ID = 990_000_001
_BETTY_ID = 990_000_101  # active ship
_HERA_ID = 990_000_102
_TERRAN_ID = 990_000_103

_SLOT_KINDS = ("weapons", "modules", "turrets", "secondary_weapons")

_MIGRATION_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "src",
        "persist",
        "database",
        "revisions",
        "versions",
        "0002_b19_repair_loadout_consistency.py",
    )
)


def _load_migration_module():
    """Load the real 0002 migration module via importlib (avoids Alembic env)."""
    spec = importlib.util.spec_from_file_location("migration_0002_real", _MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build_mock_op(conn: sa.engine.Connection) -> MagicMock:
    """Build a MagicMock for alembic.op wired to a live sync connection.

    The 0002 migration only ever calls ``op.get_bind()`` and then issues raw
    ``bind.execute(sa.text(...))`` statements itself, so the translator needs
    only to return the live connection from ``get_bind()``.
    """
    mock_op = MagicMock()
    mock_op.get_bind.return_value = conn
    return mock_op


@contextlib.contextmanager
def _rollback_conn(engine: sa.engine.Engine):
    """Connection inside a transaction that is ALWAYS rolled back."""
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            yield conn
        finally:
            if trans.is_active:
                trans.rollback()


def _disable_fk(conn: sa.engine.Connection) -> None:
    conn.execute(sa.text("SET session_replication_role = 'replica'"))


def _delete_test_rows(conn: sa.engine.Connection) -> None:
    conn.execute(sa.text(f"DELETE FROM {_TABLE} WHERE player_id = :pid"), {"pid": _TEST_PLAYER_ID})


def _insert_ship(
    conn: sa.engine.Connection,
    ship_id: int,
    ship_name: str,
    is_active: bool,
    *,
    weapons,
    modules,
    turrets,
    secondary_weapons,
) -> None:
    """Raw-insert a player_ships row with JSONB slot columns.

    Slot values are passed through ``CAST(:val AS jsonb)`` so we can hand in
    JSON text (or NULL) for the JSONB columns without an ORM.
    """
    conn.execute(
        sa.text(
            f"INSERT INTO {_TABLE} "
            "(id, player_id, ship_name, is_active, weapons, modules, turrets, secondary_weapons, created_at) "
            "VALUES (:id, :pid, :name, :active, "
            "CAST(:weapons AS jsonb), CAST(:modules AS jsonb), CAST(:turrets AS jsonb), "
            "CAST(:secondary AS jsonb), NOW())"
        ),
        {
            "id": ship_id,
            "pid": _TEST_PLAYER_ID,
            "name": ship_name,
            "active": is_active,
            "weapons": None if weapons is None else json.dumps(weapons),
            "modules": None if modules is None else json.dumps(modules),
            "turrets": None if turrets is None else json.dumps(turrets),
            "secondary": None if secondary_weapons is None else json.dumps(secondary_weapons),
        },
    )


def _seed_corrupt_state(conn: sa.engine.Connection) -> None:
    """Insert the empirical B.19 corrupt state for the synthetic player.

    Betty (active), Hera and Terran all carry the same E2 Exoclad + Telta
    Quickscan modules; Hera and Terran share the same weapon pair.
    """
    _insert_ship(
        conn,
        _BETTY_ID,
        "Betty",
        True,
        weapons=["Nirai Impulse EX 1"],
        modules=["E2 Exoclad", "Telta Quickscan"],
        turrets=[],
        secondary_weapons=None,
    )
    _insert_ship(
        conn,
        _HERA_ID,
        "Hera",
        False,
        weapons=["Micro Gun MK I", 'M6 A4 "Raccoon"'],
        modules=["E2 Exoclad", "Telta Quickscan"],
        turrets=[],
        secondary_weapons=[],
    )
    _insert_ship(
        conn,
        _TERRAN_ID,
        "Terran Battlecruiser",
        False,
        weapons=["Micro Gun MK I", 'M6 A4 "Raccoon"'],
        modules=["E2 Exoclad", "Telta Quickscan"],
        turrets=[],
        secondary_weapons=[],
    )


def _read_ship_slots(conn: sa.engine.Connection) -> dict[int, dict[str, list]]:
    """Read back the synthetic player's ships' slot lists, normalising to lists."""
    rows = conn.execute(
        sa.text(
            f"SELECT id, weapons, modules, turrets, secondary_weapons FROM {_TABLE} WHERE player_id = :pid ORDER BY id"
        ),
        {"pid": _TEST_PLAYER_ID},
    ).fetchall()
    out: dict[int, dict[str, list]] = {}
    for row in rows:
        sid = row[0]
        out[sid] = {}
        for idx, kind in enumerate(_SLOT_KINDS, start=1):
            raw = row[idx]
            if raw is None:
                out[sid][kind] = []
            elif isinstance(raw, list):
                out[sid][kind] = list(raw)
            elif isinstance(raw, str):
                try:
                    out[sid][kind] = list(json.loads(raw))
                except (TypeError, ValueError):
                    out[sid][kind] = []
            else:
                out[sid][kind] = []
    return out


@pytest.fixture(scope="function")
def pg_sync_engine():
    """Synchronous Postgres engine for migration DDL/DML tests."""
    engine = sa.create_engine(_PG_SYNC_URL, echo=False)
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Tests — every case drives the REAL mod.upgrade()/mod.downgrade().
# ---------------------------------------------------------------------------


def test_repair_dedupes_b19_empirical_corrupt_state(pg_sync_engine):
    """The empirical B.19 corrupt state (Betty/Hera/Terran with duplicated
    modules) is deduplicated by the real upgrade() so each (name, kind) appears
    on exactly one ship."""
    mod = _load_migration_module()

    with _rollback_conn(pg_sync_engine) as conn:
        _disable_fk(conn)
        _delete_test_rows(conn)
        _seed_corrupt_state(conn)

        mod.op = _build_mock_op(conn)
        mod.upgrade()

        slots = _read_ship_slots(conn)

    # Active ship (Betty) keeps both modules.
    assert slots[_BETTY_ID]["modules"] == ["E2 Exoclad", "Telta Quickscan"]
    # Hera and Terran lose their duplicate modules.
    assert slots[_HERA_ID]["modules"] == []
    assert slots[_TERRAN_ID]["modules"] == []
    # Hera is the first non-active ship by id, so it keeps the shared weapons.
    assert slots[_HERA_ID]["weapons"] == ["Micro Gun MK I", 'M6 A4 "Raccoon"']
    # Terran's duplicates of Hera's weapons are removed.
    assert slots[_TERRAN_ID]["weapons"] == []
    # Betty's unique weapon is untouched.
    assert slots[_BETTY_ID]["weapons"] == ["Nirai Impulse EX 1"]


def test_repair_is_idempotent(pg_sync_engine):
    """A second run of the real upgrade() on already-clean data does not mutate any rows."""
    mod = _load_migration_module()

    with _rollback_conn(pg_sync_engine) as conn:
        _disable_fk(conn)
        _delete_test_rows(conn)
        _seed_corrupt_state(conn)

        mod.op = _build_mock_op(conn)
        mod.upgrade()
        snapshot_a = _read_ship_slots(conn)
        mod.upgrade()
        snapshot_b = _read_ship_slots(conn)

    assert snapshot_a == snapshot_b
    # Sanity: the first upgrade actually cleaned the duplicates (not a vacuous
    # "no-op == no-op" comparison).
    assert snapshot_a[_HERA_ID]["modules"] == []
    assert snapshot_a[_BETTY_ID]["modules"] == ["E2 Exoclad", "Telta Quickscan"]


def test_repair_preserves_unique_items(pg_sync_engine):
    """An item that appears on exactly one ship is left alone by the real upgrade()."""
    mod = _load_migration_module()

    with _rollback_conn(pg_sync_engine) as conn:
        _disable_fk(conn)
        _delete_test_rows(conn)
        _insert_ship(
            conn,
            _BETTY_ID,
            "Betty",
            True,
            weapons=["Pulse Laser"],
            modules=[],
            turrets=[],
            secondary_weapons=[],
        )
        _insert_ship(
            conn,
            _HERA_ID,
            "Hera",
            False,
            weapons=["Rail Gun"],
            modules=[],
            turrets=[],
            secondary_weapons=[],
        )

        mod.op = _build_mock_op(conn)
        mod.upgrade()

        slots = _read_ship_slots(conn)

    assert slots[_BETTY_ID]["weapons"] == ["Pulse Laser"]
    assert slots[_HERA_ID]["weapons"] == ["Rail Gun"]


def test_downgrade_after_upgrade_is_safe_noop(pg_sync_engine):
    """G.5: the real downgrade() is a no-op and does not alter the repaired data.

    The design spec says: 'Restoring [duplicate slot references] would re-introduce
    the bug, so the down-migration is intentionally empty.'  This verifies:
      1. The real upgrade() deduplicates correctly.
      2. The real downgrade() returns None and does NOT re-introduce duplicates.
    """
    mod = _load_migration_module()

    with _rollback_conn(pg_sync_engine) as conn:
        _disable_fk(conn)
        _delete_test_rows(conn)
        _seed_corrupt_state(conn)

        mod.op = _build_mock_op(conn)
        mod.upgrade()
        snapshot_after_upgrade = _read_ship_slots(conn)

        # Upgrade worked: active ship kept its modules, non-active cleaned.
        assert snapshot_after_upgrade[_BETTY_ID]["modules"] == ["E2 Exoclad", "Telta Quickscan"]
        assert snapshot_after_upgrade[_HERA_ID]["modules"] == []

        # Real downgrade() — must return None and must not alter data.
        result = mod.downgrade()
        assert result is None

        snapshot_after_downgrade = _read_ship_slots(conn)

    assert snapshot_after_downgrade == snapshot_after_upgrade
