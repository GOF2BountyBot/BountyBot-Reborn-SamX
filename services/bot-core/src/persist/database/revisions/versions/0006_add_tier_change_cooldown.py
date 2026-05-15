"""Add tier-change cooldown: guild override + per-player cooldown_end.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-13

Adds:
- ``guild_configs.tier_change_cooldown`` (Integer, NULL = use GameConstants default)
- ``players.tier_change_cooldown_end`` (DateTime(tz=True), NULL = no active cooldown)

Both columns are nullable; idempotent column-existence guards mirror revision 0005.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    guild_cols = {col["name"] for col in inspector.get_columns("guild_configs")}
    if "tier_change_cooldown" not in guild_cols:
        op.add_column("guild_configs", sa.Column("tier_change_cooldown", sa.Integer(), nullable=True))

    player_cols = {col["name"] for col in inspector.get_columns("players")}
    if "tier_change_cooldown_end" not in player_cols:
        op.add_column(
            "players",
            sa.Column("tier_change_cooldown_end", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    player_cols = {col["name"] for col in inspector.get_columns("players")}
    if "tier_change_cooldown_end" in player_cols:
        op.drop_column("players", "tier_change_cooldown_end")

    guild_cols = {col["name"] for col in inspector.get_columns("guild_configs")}
    if "tier_change_cooldown" in guild_cols:
        op.drop_column("guild_configs", "tier_change_cooldown")
