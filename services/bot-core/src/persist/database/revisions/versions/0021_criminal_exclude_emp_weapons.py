"""Add criminal_exclude_emp_weapons override column to guild_configs (BALANCE_JOURNAL §A Thread 6).

Adds a single nullable per-guild override column mirroring the B.49 NULL-means-default
pattern. NULL means "use GameConstants.CRIMINAL_EXCLUDE_EMP_WEAPONS (True)" — the service
layer resolves the actual value via resolve_constant().

Column (nullable, default NULL):
- criminal_exclude_emp_weapons (Boolean) — Thread 6 EMP-dominant weapon exclusion toggle

Idempotent: guards the op with an inspector check so fresh-install DBs (where revision
0001 already materialised this column from current ORM metadata) are a no-op, while
existing DBs receive the additive change. Mirrors revision 0020.

Revision ID: 0021
Revises: 0020
Create Date: 2026-06-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (column_name, sqlalchemy_type) — order is the apply order; reversed for downgrade.
_NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (("criminal_exclude_emp_weapons", sa.Boolean()),)


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
