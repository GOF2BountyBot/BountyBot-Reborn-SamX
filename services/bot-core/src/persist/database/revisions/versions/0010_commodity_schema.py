"""Commodity catalog table.

Joined-table-inheritance child of `item` for the GOF2 trade/loot catalog.
Single table, no per-subcategory subclasses — `subcategory` is the sole
String discriminator; price/system/provenance data lives in `extra_atts`
JSON surfaced as read-only model properties.

Idempotent: skips creation if `commodity` already exists, mirroring the
defensive pattern of revisions 0008/0009. Fresh-install DBs (0001 builds
tables from ORM metadata) are a no-op; existing prod DBs get it created.

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("commodity"):
        op.create_table(
            "commodity",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("tech_level", sa.Integer(), nullable=True),
            sa.Column("subcategory", sa.String(), nullable=False),
            sa.Column("extra_atts", sa.JSON(), nullable=True, server_default=sa.text("'{}'::json")),
            sa.ForeignKeyConstraint(["id"], ["item.id"]),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("commodity"):
        op.drop_table("commodity")
