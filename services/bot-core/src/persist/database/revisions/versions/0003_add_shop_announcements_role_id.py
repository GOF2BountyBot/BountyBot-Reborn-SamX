"""Add shop_announcements_role_id column to guild_configs.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-05

Adds a nullable BigInteger column ``shop_announcements_role_id`` to the
``guild_configs`` table.  This column stores the Discord role ID used for
@-mentioning players who have opted into shop refresh announcements.

Non-destructive: nullable, no server default required.  Existing guild configs
will have NULL until they re-run /admin_setup.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add shop_announcements_role_id column to guild_configs (nullable).

    Guards against fresh installs where migration 0001 already creates all
    tables from current ORM metadata (including this column), which would
    cause a ProgrammingError: column already exists on op.add_column().
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = [col["name"] for col in inspector.get_columns("guild_configs")]
    if "shop_announcements_role_id" not in existing_columns:
        op.add_column(
            "guild_configs",
            sa.Column("shop_announcements_role_id", sa.BigInteger(), nullable=True),
        )


def downgrade() -> None:
    """Remove shop_announcements_role_id column from guild_configs."""
    op.drop_column("guild_configs", "shop_announcements_role_id")
