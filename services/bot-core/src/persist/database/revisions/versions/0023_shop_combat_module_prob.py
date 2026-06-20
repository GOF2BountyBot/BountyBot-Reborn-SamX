"""Add shop_combat_module_prob nullable column to guild_configs.

Adds ONE nullable Float per-guild override column for the shop module-draw
combat/filler split probability. NULL means "use GameConstants.SHOP_COMBAT_MODULE_PROB
default (0.75)". Resolved via resolve_constant() in the service layer.

Column:
- shop_combat_module_prob  (Float)  — combat-bucket draw probability (0.0–1.0)

Idempotent: guards every op with inspector checks so fresh-install DBs (where
revision 0001 already materialised these columns from current ORM metadata) are
no-ops, while existing DBs receive the additive change. Mirrors revisions 0022
/ 0021 / 0020.

Revision ID: 0023
Revises: 0022
Create Date: 2026-06-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | Sequence[str] | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (column_name, sqlalchemy_type) — single entry for the shop combat prob knob.
_NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (("shop_combat_module_prob", sa.Float()),)


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
