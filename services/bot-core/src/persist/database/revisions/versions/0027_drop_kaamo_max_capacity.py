"""Drop the retired kaamo_max_capacity override column from guild_configs.

Owner decision (issue #70, 2026-08-24): Kaamo storage capacity "is not a thing
and never will be" — no code has ever read the column, so any value stored in
it was a silent no-op. The constant, schema field, API/slash exposure, and this
column are all removed together.

Idempotent: guards every op with inspector checks so a DB that already lacks
the column (fresh install built from current ORM metadata) is a no-op, while
existing DBs receive the drop. Mirrors the guard style of revisions 0023-0026.

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | Sequence[str] | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMN = "kaamo_max_capacity"


def _cols(inspector: sa.engine.reflection.Inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _COLUMN in _cols(inspector, "guild_configs"):
        op.drop_column("guild_configs", _COLUMN)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _COLUMN not in _cols(inspector, "guild_configs"):
        op.add_column(
            "guild_configs",
            sa.Column(_COLUMN, sa.Integer(), nullable=True),
        )
