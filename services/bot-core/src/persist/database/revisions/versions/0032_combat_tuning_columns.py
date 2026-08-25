"""Add 7 new per-guild combat-engine tuning columns to guild_configs (issue #70, unit A1, revision 0032).

These columns complete the nuke / shock-blast / layer-reemit constant set that could not
land in earlier revisions because no GuildConfig columns existed for them yet.

New columns (all nullable — NULL == "use the global GameConstants default"):
  nuke_range_regime_threshold_m   Integer   boundary between LR and CR nuke windows
  nuke_lr_near_frac               Float     LR near-edge fraction of current distance
  nuke_cr_short_m                 Integer   CR window short-edge offset (metres)
  nuke_cr_overshoot_m             Integer   CR window far-edge offset (metres)
  nuke_stack_falloff              Float     per-detonation yield interference multiplier
  shock_blast_trigger_range_m     Integer   max range (metres) at which shock-blast fires
  combat_layer_reemit_fraction    Float     min recovery fraction before re-depleted fires
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | Sequence[str] | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (column_name, sqlalchemy_type) — 7 new nullable columns.
_NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("nuke_range_regime_threshold_m", sa.Integer()),
    ("nuke_lr_near_frac", sa.Float()),
    ("nuke_cr_short_m", sa.Integer()),
    ("nuke_cr_overshoot_m", sa.Integer()),
    ("nuke_stack_falloff", sa.Float()),
    ("shock_blast_trigger_range_m", sa.Integer()),
    ("combat_layer_reemit_fraction", sa.Float()),
)


def _cols(inspector: sa.engine.reflection.Inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = _cols(inspector, "guild_configs")

    for col_name, col_type in _NEW_COLUMNS:
        if col_name not in existing:
            op.add_column("guild_configs", sa.Column(col_name, col_type, nullable=True))


def downgrade() -> None:
    """Drop the 7 new columns."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = _cols(inspector, "guild_configs")

    for col_name, _col_type in _NEW_COLUMNS:
        if col_name in existing:
            op.drop_column("guild_configs", col_name)
