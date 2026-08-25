"""Flatten 7 JSONB per-guild override dicts to 27 scalar columns (issue #70, revision 0030).

Phase A of the two-phase JSONB flatten (Release N):
  - Add 27 nullable scalar columns to guild_configs (inspector-guarded, idempotent).
  - Backfill scalar columns from existing JSONB rows where the source column exists
    and the scalar is NULL (idempotent re-run safe).
  - Retain the 7 JSONB source columns (dropped in Phase B, next release) so a
    code-only rollback still finds legacy values.

Columns added (integer unless noted):
  division_max_tl_{bronze,silver,gold,platinum}                       — int
  bounty_division_reward_mult_{bronze,silver,gold,platinum}           — float
  primary_tl_band_weight_{center,minus1,plus1}                       — int
  criminal_{cloak,booster,emergency,weaponmod}_chance_{bronze,silver,gold,platinum}  — int (×16)

Backfill strategy:
  PostgreSQL: single guarded UPDATE per source column using JSONB arrow operators and casts.
  SQLite (test suite): Python-side row loop using JSON string parsing (json_extract unavailable
  in older SQLite versions bundled with CPython; simple key lookup is portable).
  Dialect detected via ``bind.dialect.name``.

NOTE: The 7 JSONB source columns are NOT dropped here; they are removed in revision 0031
(Phase B) once the migration window has closed. See the phase-B migration docstring.

Revision ID: 0030
Revises: 0029
Create Date: 2026-08-25
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | Sequence[str] | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (column_name, sqlalchemy_type) — 27 new scalar columns, all nullable.
_NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    # division_max_tl flat scalars
    ("division_max_tl_bronze", sa.Integer()),
    ("division_max_tl_silver", sa.Integer()),
    ("division_max_tl_gold", sa.Integer()),
    ("division_max_tl_platinum", sa.Integer()),
    # bounty_division_reward_mult flat scalars
    ("bounty_division_reward_mult_bronze", sa.Float()),
    ("bounty_division_reward_mult_silver", sa.Float()),
    ("bounty_division_reward_mult_gold", sa.Float()),
    ("bounty_division_reward_mult_platinum", sa.Float()),
    # primary_tl_band_weights flat scalars
    ("primary_tl_band_weight_center", sa.Integer()),
    ("primary_tl_band_weight_minus1", sa.Integer()),
    ("primary_tl_band_weight_plus1", sa.Integer()),
    # criminal_cloak_chance flat scalars
    ("criminal_cloak_chance_bronze", sa.Integer()),
    ("criminal_cloak_chance_silver", sa.Integer()),
    ("criminal_cloak_chance_gold", sa.Integer()),
    ("criminal_cloak_chance_platinum", sa.Integer()),
    # criminal_booster_chance flat scalars
    ("criminal_booster_chance_bronze", sa.Integer()),
    ("criminal_booster_chance_silver", sa.Integer()),
    ("criminal_booster_chance_gold", sa.Integer()),
    ("criminal_booster_chance_platinum", sa.Integer()),
    # criminal_emergency_chance flat scalars
    ("criminal_emergency_chance_bronze", sa.Integer()),
    ("criminal_emergency_chance_silver", sa.Integer()),
    ("criminal_emergency_chance_gold", sa.Integer()),
    ("criminal_emergency_chance_platinum", sa.Integer()),
    # criminal_weaponmod_chance flat scalars
    ("criminal_weaponmod_chance_bronze", sa.Integer()),
    ("criminal_weaponmod_chance_silver", sa.Integer()),
    ("criminal_weaponmod_chance_gold", sa.Integer()),
    ("criminal_weaponmod_chance_platinum", sa.Integer()),
)

# JSONB source → list of (scalar_col, key, cast) tuples.
# ``cast`` is "int" or "float" — used to build PostgreSQL cast expression and
# Python type coercion for the SQLite row-loop path.
_BACKFILL_SPEC: tuple[tuple[str, list[tuple[str, str, str]]], ...] = (
    (
        "division_max_tl",
        [
            ("division_max_tl_bronze", "bronze", "int"),
            ("division_max_tl_silver", "silver", "int"),
            ("division_max_tl_gold", "gold", "int"),
            ("division_max_tl_platinum", "platinum", "int"),
        ],
    ),
    (
        "bounty_division_reward_mult",
        [
            ("bounty_division_reward_mult_bronze", "bronze", "float"),
            ("bounty_division_reward_mult_silver", "silver", "float"),
            ("bounty_division_reward_mult_gold", "gold", "float"),
            ("bounty_division_reward_mult_platinum", "platinum", "float"),
        ],
    ),
    (
        "primary_tl_band_weights",
        [
            ("primary_tl_band_weight_center", "center", "int"),
            ("primary_tl_band_weight_minus1", "minus1", "int"),
            ("primary_tl_band_weight_plus1", "plus1", "int"),
        ],
    ),
    (
        "criminal_cloak_chance_by_division",
        [
            ("criminal_cloak_chance_bronze", "bronze", "int"),
            ("criminal_cloak_chance_silver", "silver", "int"),
            ("criminal_cloak_chance_gold", "gold", "int"),
            ("criminal_cloak_chance_platinum", "platinum", "int"),
        ],
    ),
    (
        "criminal_booster_chance_by_division",
        [
            ("criminal_booster_chance_bronze", "bronze", "int"),
            ("criminal_booster_chance_silver", "silver", "int"),
            ("criminal_booster_chance_gold", "gold", "int"),
            ("criminal_booster_chance_platinum", "platinum", "int"),
        ],
    ),
    (
        "criminal_emergency_chance_by_division",
        [
            ("criminal_emergency_chance_bronze", "bronze", "int"),
            ("criminal_emergency_chance_silver", "silver", "int"),
            ("criminal_emergency_chance_gold", "gold", "int"),
            ("criminal_emergency_chance_platinum", "platinum", "int"),
        ],
    ),
    (
        "criminal_weaponmod_chance_by_division",
        [
            ("criminal_weaponmod_chance_bronze", "bronze", "int"),
            ("criminal_weaponmod_chance_silver", "silver", "int"),
            ("criminal_weaponmod_chance_gold", "gold", "int"),
            ("criminal_weaponmod_chance_platinum", "platinum", "int"),
        ],
    ),
)


def _cols(inspector: sa.engine.reflection.Inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}


def _backfill_postgres(bind: sa.engine.Connection, existing: set[str]) -> None:
    """Backfill scalar columns from JSONB source columns on PostgreSQL."""
    for src_col, mappings in _BACKFILL_SPEC:
        if src_col not in existing:
            # Source JSONB column absent (fresh install already has scalars, no JSONB).
            continue
        # Build SET clause: only assign scalar columns that exist and are NULL.
        for scalar_col, key, cast in mappings:
            if scalar_col not in existing:
                continue
            # Only backfill where the JSONB source is non-NULL and the scalar is still NULL
            # (idempotent: re-running will not overwrite values set since last run).
            pg_cast = "::int" if cast == "int" else "::float"
            bind.execute(
                sa.text(
                    f"UPDATE guild_configs"
                    f" SET {scalar_col} = ({src_col}->>'%s'){pg_cast}"
                    f" WHERE {src_col} IS NOT NULL AND {scalar_col} IS NULL" % key
                )
            )


def _backfill_sqlite(bind: sa.engine.Connection, existing: set[str]) -> None:
    """Backfill scalar columns from JSONB source columns on SQLite (test suite)."""
    rows = bind.execute(
        sa.text("SELECT id, " + ", ".join(src for src, _ in _BACKFILL_SPEC if src in existing) + " FROM guild_configs")
    ).fetchall()  # noqa: E501
    src_cols = [src for src, _ in _BACKFILL_SPEC if src in existing]

    for row in rows:
        row_dict = dict(zip(["id", *src_cols], row, strict=False))
        updates: dict[str, object] = {}

        for src_col, mappings in _BACKFILL_SPEC:
            if src_col not in existing:
                continue
            raw = row_dict.get(src_col)
            if raw is None:
                continue
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(data, dict):
                continue

            for scalar_col, key, cast in mappings:
                if scalar_col not in existing:
                    continue
                val = data.get(key)
                if val is None:
                    continue
                try:
                    coerced = int(val) if cast == "int" else float(val)
                except (ValueError, TypeError):
                    continue
                updates[scalar_col] = coerced

        if updates:
            set_clause = ", ".join(f"{col} = :{col}" for col in updates)
            bind.execute(
                sa.text(f"UPDATE guild_configs SET {set_clause} WHERE id = :_id"),
                {**updates, "_id": row_dict["id"]},
            )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing = _cols(inspector, "guild_configs")

    # 1. Add any missing scalar columns (idempotent).
    for col_name, col_type in _NEW_COLUMNS:
        if col_name not in existing:
            op.add_column(
                "guild_configs",
                sa.Column(col_name, col_type, nullable=True),
            )

    # Re-fetch column set after additions so the backfill can reference new cols.
    existing = _cols(inspector, "guild_configs")

    # 2. Backfill from JSONB sources (dialect-aware).
    dialect = bind.dialect.name
    if dialect == "postgresql":
        _backfill_postgres(bind, existing)
    else:
        # SQLite fallback (test suite uses in-memory SQLite).
        _backfill_sqlite(bind, existing)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing = _cols(inspector, "guild_configs")
    for col_name, _col_type in reversed(_NEW_COLUMNS):
        if col_name in existing:
            op.drop_column("guild_configs", col_name)
