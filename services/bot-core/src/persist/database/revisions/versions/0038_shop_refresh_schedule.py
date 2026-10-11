"""Per-guild shop refresh schedule overrides + retire dead per-row interval.

Adds three columns to guild_configs:
  - shop_refresh_interval_hours (Integer, nullable) — override for refresh cadence (1–24 h)
  - shop_refresh_jitter_pct     (Integer, nullable) — random ±% variation (0–50)
  - shop_next_refresh_at        (DateTime tz, nullable) — rolling state; set by the shop_refresh executor

Drops one column from guild_shops:
  - refresh_interval_hours — was per-row; superseded by the per-guild config columns above.
    Dead code since the scheduled-job system took over refresh scheduling.

Idempotent: guards on sa.inspect so CI's create_all + migration-run both work.

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | Sequence[str] | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GC = "guild_configs"
_GS = "guild_shops"
_ADD_COLS = (
    ("shop_refresh_interval_hours", sa.Column("shop_refresh_interval_hours", sa.Integer, nullable=True)),
    ("shop_refresh_jitter_pct", sa.Column("shop_refresh_jitter_pct", sa.Integer, nullable=True)),
    ("shop_next_refresh_at", sa.Column("shop_next_refresh_at", sa.DateTime(timezone=True), nullable=True)),
)
_DROP_COL = "refresh_interval_hours"


def _gc_cols(inspector: sa.engine.reflection.Inspector) -> set[str]:
    return {c["name"] for c in inspector.get_columns(_GC)}


def _gs_cols(inspector: sa.engine.reflection.Inspector) -> set[str]:
    return {c["name"] for c in inspector.get_columns(_GS)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_gc = _gc_cols(inspector)
    for col_name, col_obj in _ADD_COLS:
        if col_name not in existing_gc:
            op.add_column(_GC, col_obj)
    existing_gs = _gs_cols(inspector)
    if _DROP_COL in existing_gs:
        op.drop_column(_GS, _DROP_COL)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_gs = _gs_cols(inspector)
    if _DROP_COL not in existing_gs:
        op.add_column(
            _GS,
            sa.Column(_DROP_COL, sa.Integer, nullable=False, server_default="12"),
        )
    existing_gc = _gc_cols(inspector)
    for col_name, _ in reversed(_ADD_COLS):
        if col_name in existing_gc:
            op.drop_column(_GC, col_name)
