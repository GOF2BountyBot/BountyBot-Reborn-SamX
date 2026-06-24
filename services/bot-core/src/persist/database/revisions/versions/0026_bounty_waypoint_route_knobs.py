"""Add waypoint route-generation knobs to guild_configs.

Adds four nullable per-guild override columns for the waypoint bounty-route
feature. NULL means "use the matching GameConstants default":

- bounty_single_waypoint_prob  (Float)   — P(1 waypoint), default 0.33
- bounty_dual_waypoint_prob    (Float)   — P(2 waypoints), default 0.10
- bounty_waypoint_attempts     (Integer) — re-rolls before A→C fallback, default 20
- bounty_waypoint_min_degree   (Integer) — min retained waypoint degree, default 2

Resolved via resolve_constant() in BountyService when rolling the per-spawn
waypoint cascade. Adding waypoints lengthens routes (and, since reward scales
with len(route), the prize pool); these knobs let a guild dial that back.

Idempotent: guards every op with inspector checks so fresh-install DBs (where
revision 0001 already materialised these columns from current ORM metadata) are
no-ops, while existing DBs receive the additive change. Mirrors revisions 0025
/ 0024 / 0023.

Revision ID: 0026
Revises: 0025
Create Date: 2026-06-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | Sequence[str] | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (column_name, sqlalchemy_type) — four scalar waypoint knobs.
_NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("bounty_single_waypoint_prob", sa.Float()),
    ("bounty_dual_waypoint_prob", sa.Float()),
    ("bounty_waypoint_attempts", sa.Integer()),
    ("bounty_waypoint_min_degree", sa.Integer()),
)


def _cols(inspector: sa.engine.reflection.Inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing = _cols(inspector, "guild_configs")
    for col_name, col_type in _NEW_COLUMNS:
        if col_name not in existing:
            op.add_column(
                "guild_configs",
                sa.Column(col_name, col_type, nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing = _cols(inspector, "guild_configs")
    for col_name, _col_type in reversed(_NEW_COLUMNS):
        if col_name in existing:
            op.drop_column("guild_configs", col_name)
