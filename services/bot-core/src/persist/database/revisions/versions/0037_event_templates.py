"""Event templates: named, non-running events (state='template') admins instantiate drafts from.

Adds game_events.name (String(64), nullable) and a partial unique index on
(guild_id, name) WHERE state = 'template'. Idempotent (CI runs create_all first).

Revision ID: 0037
Revises: 0036
Create Date: 2026-09-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | Sequence[str] | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "game_events"
_COL = "name"
_IDX = "ux_game_events_template_name"


def _cols(inspector: sa.engine.reflection.Inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}


def _indexes(inspector: sa.engine.reflection.Inspector, table: str) -> set[str]:
    return {i["name"] for i in inspector.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _COL not in _cols(inspector, _TABLE):
        op.add_column(_TABLE, sa.Column(_COL, sa.String(64), nullable=True))
    if _IDX not in _indexes(inspector, _TABLE):
        op.create_index(
            _IDX,
            _TABLE,
            ["guild_id", _COL],
            unique=True,
            postgresql_where=sa.text("state = 'template'"),
            sqlite_where=sa.text("state = 'template'"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _IDX in _indexes(inspector, _TABLE):
        op.drop_index(_IDX, table_name=_TABLE)
    if _COL in _cols(inspector, _TABLE):
        op.drop_column(_TABLE, _COL)
