"""Add display_name column to players table.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-06

Adds a nullable String(100) column ``display_name`` to the ``players`` table.
This column stores the Discord per-guild display name (server nickname >
global display name > username), refreshed on every interaction.

Non-destructive: nullable, no server default required.  Existing players
will have NULL until they next interact with the bot.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add display_name column to players (nullable).

    Guards against fresh installs where migration 0001 already creates all
    tables from current ORM metadata (including this column), which would
    cause a ProgrammingError: column already exists on op.add_column().
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = [col["name"] for col in inspector.get_columns("players")]
    if "display_name" not in existing_columns:
        op.add_column(
            "players",
            sa.Column("display_name", sa.String(100), nullable=True),
        )


def downgrade() -> None:
    """Remove display_name column from players."""
    op.drop_column("players", "display_name")
