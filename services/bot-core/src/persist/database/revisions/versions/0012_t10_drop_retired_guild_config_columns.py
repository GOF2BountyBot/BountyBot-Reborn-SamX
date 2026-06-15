"""T10: Drop retired guild_config columns — duel_variance_percent + bounty_pvc_armour_buff_factor.

SimpleTTKResolver and the armour-buff model are retired in T10; the per-guild
override columns that exposed them are no longer needed.

- duel_variance_percent: was the per-guild override for DUEL_VARIANCE_PERCENT
  (SimpleTTKResolver ±% variance). No application code reads this column after T10.
- bounty_pvc_armour_buff_factor: was the per-guild override for the armour-buff
  factor applied to the player in PvC fights. Replaced by pvc_damage_reduction (§3).

Idempotent: guards each DROP with an inspector existence check (same pattern as 0005).
Safe to run on fresh-install DBs where the column may already be absent.

Reversible: down() re-adds both columns as nullable Float with no server default,
restoring the pre-T10 schema (no data is restored — these were tunable knobs with
no game-state implications).

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | None = None
depends_on: str | None = None

# Columns to drop (name, type for reversible down())
_RETIRED_COLUMNS: list[tuple[str, sa.types.TypeEngine]] = [
    ("duel_variance_percent", sa.Float()),
    ("bounty_pvc_armour_buff_factor", sa.Float()),
]

_TABLE = "guild_configs"


def upgrade() -> None:
    """Drop retired per-guild override columns (idempotent)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns(_TABLE)}
    for col_name, _col_type in _RETIRED_COLUMNS:
        if col_name in existing:
            op.drop_column(_TABLE, col_name)


def downgrade() -> None:
    """Re-add retired columns as nullable Float (no data restored)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns(_TABLE)}
    for col_name, col_type in reversed(_RETIRED_COLUMNS):
        if col_name not in existing:
            op.add_column(_TABLE, sa.Column(col_name, col_type, nullable=True))
