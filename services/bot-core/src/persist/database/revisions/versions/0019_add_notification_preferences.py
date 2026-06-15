"""Add persisted notification-preference flags to players (D-019).

Adds two NOT NULL boolean columns to ``players``:
- ``bounty_notifications_enabled`` — wants @-mentions for bounty announcements
- ``shop_notifications_enabled``   — wants @-mentions for shop announcements

Both carry ``server_default='true'`` so EXISTING rows backfill to True (opted-in),
matching today's default behaviour. This mirrors the in-repo precedent of revision
0011, which added NOT NULL integer counters to ``players`` with ``server_default='0'``
to backfill existing rows on the ``ALTER TABLE ... ADD COLUMN`` statement.

Backfill side-effect (accepted, one-time): a user who had previously *opted out* of
bounty notifications (under the old role-membership-only scheme they simply lacked the
tier role) is backfilled to ``True`` here and will re-gain the tier role on their next
``/profile`` / ``/promote``. This is a deliberate, acceptable migration side-effect —
there is no persisted signal from the old scheme to distinguish opted-out from
just-unregistered or brand-new, so we default everyone to the historical default.

Idempotent: guards every op with inspector checks so fresh-install DBs (where revision
0001 already materialised these columns from current ORM metadata) are no-ops, while
existing DBs receive the additive change.

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_COLUMNS = ("bounty_notifications_enabled", "shop_notifications_enabled")


def _cols(inspector: sa.engine.reflection.Inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    player_cols = _cols(inspector, "players")
    for col_name in _NEW_COLUMNS:
        if col_name not in player_cols:
            # server_default='true' backfills existing rows to opted-in (True).
            op.add_column(
                "players",
                sa.Column(col_name, sa.Boolean(), nullable=False, server_default="true"),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    player_cols = _cols(inspector, "players")
    for col_name in reversed(_NEW_COLUMNS):
        if col_name in player_cols:
            op.drop_column("players", col_name)
