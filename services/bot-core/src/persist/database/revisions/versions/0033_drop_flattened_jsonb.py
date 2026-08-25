"""Drop the 7 deprecated JSONB dict columns from guild_configs (issue #70, revision 0033).

Phase B of the two-phase JSONB flatten (Release N+1):
  - Drop the 7 JSONB source columns that were backfilled into 27 scalars in revision 0030.
  - Downgrade re-adds the 7 columns (JSONB, nullable) and re-serializes them from the
    flat scalar columns so a code rollback has data to read.

Columns dropped:
  division_max_tl                     JSONB  (backfilled to division_max_tl_{bronze,silver,gold,platinum})
  bounty_division_reward_mult         JSONB  (backfilled to bounty_division_reward_mult_{bronze,...})
  primary_tl_band_weights             JSONB  (backfilled to primary_tl_band_weight_{center,minus1,plus1})
  criminal_cloak_chance_by_division   JSONB  (backfilled to criminal_cloak_chance_{bronze,...})
  criminal_booster_chance_by_division JSONB  (backfilled to criminal_booster_chance_{bronze,...})
  criminal_emergency_chance_by_division JSONB (backfilled to criminal_emergency_chance_{bronze,...})
  criminal_weaponmod_chance_by_division JSONB (backfilled to criminal_weaponmod_chance_{bronze,...})

Revision ID: 0033
Revises: 0032
Create Date: 2026-08-25
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0033"
down_revision: str | Sequence[str] | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Portable JSON type: PostgreSQL uses JSONB; SQLite falls back to JSON.
_JSONB_TYPE = sa.JSON().with_variant(JSONB(), "postgresql")

# The 7 JSONB columns being dropped.
_DROP_COLUMNS: tuple[str, ...] = (
    "division_max_tl",
    "bounty_division_reward_mult",
    "primary_tl_band_weights",
    "criminal_cloak_chance_by_division",
    "criminal_booster_chance_by_division",
    "criminal_emergency_chance_by_division",
    "criminal_weaponmod_chance_by_division",
)

# Downgrade re-serialization spec:
# (jsonb_col, scalar_col_list)
# Each scalar_col_list is [(key, scalar_col), ...] — key is the dict key in the re-built JSONB.
_RESERIALIZE_SPEC: tuple[tuple[str, list[tuple[str, str]]], ...] = (
    (
        "division_max_tl",
        [
            ("bronze", "division_max_tl_bronze"),
            ("silver", "division_max_tl_silver"),
            ("gold", "division_max_tl_gold"),
            ("platinum", "division_max_tl_platinum"),
        ],
    ),
    (
        "bounty_division_reward_mult",
        [
            ("bronze", "bounty_division_reward_mult_bronze"),
            ("silver", "bounty_division_reward_mult_silver"),
            ("gold", "bounty_division_reward_mult_gold"),
            ("platinum", "bounty_division_reward_mult_platinum"),
        ],
    ),
    (
        "primary_tl_band_weights",
        [
            ("center", "primary_tl_band_weight_center"),
            ("minus1", "primary_tl_band_weight_minus1"),
            ("plus1", "primary_tl_band_weight_plus1"),
        ],
    ),
    (
        "criminal_cloak_chance_by_division",
        [
            ("bronze", "criminal_cloak_chance_bronze"),
            ("silver", "criminal_cloak_chance_silver"),
            ("gold", "criminal_cloak_chance_gold"),
            ("platinum", "criminal_cloak_chance_platinum"),
        ],
    ),
    (
        "criminal_booster_chance_by_division",
        [
            ("bronze", "criminal_booster_chance_bronze"),
            ("silver", "criminal_booster_chance_silver"),
            ("gold", "criminal_booster_chance_gold"),
            ("platinum", "criminal_booster_chance_platinum"),
        ],
    ),
    (
        "criminal_emergency_chance_by_division",
        [
            ("bronze", "criminal_emergency_chance_bronze"),
            ("silver", "criminal_emergency_chance_silver"),
            ("gold", "criminal_emergency_chance_gold"),
            ("platinum", "criminal_emergency_chance_platinum"),
        ],
    ),
    (
        "criminal_weaponmod_chance_by_division",
        [
            ("bronze", "criminal_weaponmod_chance_bronze"),
            ("silver", "criminal_weaponmod_chance_silver"),
            ("gold", "criminal_weaponmod_chance_gold"),
            ("platinum", "criminal_weaponmod_chance_platinum"),
        ],
    ),
)


def _cols(inspector: sa.engine.reflection.Inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    """Drop the 7 deprecated JSONB columns (inspector-guarded for idempotency)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = _cols(inspector, "guild_configs")

    for col_name in _DROP_COLUMNS:
        if col_name in existing:
            op.drop_column("guild_configs", col_name)


def _downgrade_reserialize_postgres(bind: sa.engine.Connection, existing: set[str]) -> None:
    """Re-serialize scalar columns back into JSONB dicts on PostgreSQL using jsonb_build_object."""
    for jsonb_col, key_scalar_pairs in _RESERIALIZE_SPEC:
        if jsonb_col not in existing:
            continue
        # Only re-serialize rows where at least one scalar is non-NULL.
        # Build the jsonb_build_object expression from scalars that exist.
        available = [(key, scalar) for key, scalar in key_scalar_pairs if scalar in existing]
        if not available:
            continue
        # Coalesce to NULL when all scalars are NULL (no data to re-serialize for this row).
        kv_args = ", ".join(f"'{key}', {scalar}" for key, scalar in available)
        null_check = " AND ".join(f"{scalar} IS NULL" for _, scalar in available)
        bind.execute(
            sa.text(f"UPDATE guild_configs SET {jsonb_col} = jsonb_build_object({kv_args}) WHERE NOT ({null_check})")
        )


def _downgrade_reserialize_sqlite(bind: sa.engine.Connection, existing: set[str]) -> None:
    """Re-serialize scalar columns back into JSONB dicts on SQLite (test suite)."""
    # Collect all unique scalar column names we need to read.
    scalar_cols = sorted({scalar for _, pairs in _RESERIALIZE_SPEC for _, scalar in pairs if scalar in existing})
    if not scalar_cols:
        return

    rows = bind.execute(sa.text("SELECT id, " + ", ".join(scalar_cols) + " FROM guild_configs")).fetchall()

    for row in rows:
        row_dict = dict(zip(["id", *scalar_cols], row, strict=False))
        updates: dict[str, str | None] = {}

        for jsonb_col, key_scalar_pairs in _RESERIALIZE_SPEC:
            if jsonb_col not in existing:
                continue
            rebuilt: dict[str, object] = {}
            for key, scalar in key_scalar_pairs:
                if scalar not in existing:
                    continue
                val = row_dict.get(scalar)
                if val is not None:
                    rebuilt[key] = val
            if rebuilt:
                updates[jsonb_col] = json.dumps(rebuilt)

        if updates:
            set_clause = ", ".join(f"{col} = :{col}" for col in updates)
            bind.execute(
                sa.text(f"UPDATE guild_configs SET {set_clause} WHERE id = :_id"),
                {**updates, "_id": row_dict["id"]},
            )


def downgrade() -> None:
    """Re-add the 7 JSONB columns (guarded) and re-serialize from scalar columns."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = _cols(inspector, "guild_configs")

    # Re-add each missing column.
    for col_name in _DROP_COLUMNS:
        if col_name not in existing:
            op.add_column("guild_configs", sa.Column(col_name, _JSONB_TYPE, nullable=True))

    # Re-fetch after additions.
    existing = _cols(inspector, "guild_configs")

    # Re-serialize scalars → JSONB.
    dialect = bind.dialect.name
    if dialect == "postgresql":
        _downgrade_reserialize_postgres(bind, existing)
    else:
        _downgrade_reserialize_sqlite(bind, existing)
