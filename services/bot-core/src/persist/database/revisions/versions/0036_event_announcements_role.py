"""Event announcements role + player opt-in flag (issue #30, slice 6).

Adds two columns:
  guild_configs.event_announcements_role_id  — BigInteger, nullable (role ID)
  players.event_notifications_enabled        — Boolean NOT NULL, server_default='true'

Both are the third instance of the Shop Announcements pattern.

Revision ID: 0036
Revises: 0035
Create Date: 2026-09-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: str | Sequence[str] | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GC_COL = "event_announcements_role_id"
_P_COL = "event_notifications_enabled"


def _cols(inspector: sa.engine.reflection.Inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    gc_cols = _cols(inspector, "guild_configs")
    if _GC_COL not in gc_cols:
        op.add_column(
            "guild_configs",
            sa.Column(_GC_COL, sa.BigInteger(), nullable=True),
        )

    p_cols = _cols(inspector, "players")
    if _P_COL not in p_cols:
        op.add_column(
            "players",
            sa.Column(_P_COL, sa.Boolean(), nullable=False, server_default="true"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    p_cols = _cols(inspector, "players")
    if _P_COL in p_cols:
        op.drop_column("players", _P_COL)

    gc_cols = _cols(inspector, "guild_configs")
    if _GC_COL in gc_cols:
        op.drop_column("guild_configs", _GC_COL)
