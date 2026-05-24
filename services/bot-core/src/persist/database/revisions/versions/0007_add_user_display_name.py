"""Add display_name column to users table (B.62).

Revision ID: 0007
Revises: 6510020b3335
Create Date: 2026-05-21

Adds a nullable VARCHAR display_name column to the ``users`` table.
This column stores the Discord display name (server nickname or global
display name), refreshed on every /profile interaction.

Non-destructive: nullable, no server default required. Existing users
will have NULL until they next interact with the bot.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "6510020b3335"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add display_name column to users (nullable).

    Guards against fresh installs where migration 0001 already creates all
    tables from current ORM metadata (including this column), which would
    cause a ProgrammingError: column already exists on op.add_column().
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = [col["name"] for col in inspector.get_columns("users")]
    if "display_name" not in existing_columns:
        op.add_column(
            "users",
            sa.Column("display_name", sa.String(), nullable=True),
        )


def downgrade() -> None:
    """Remove display_name column from users."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = [col["name"] for col in inspector.get_columns("users")]
    if "display_name" in existing_columns:
        op.drop_column("users", "display_name")
