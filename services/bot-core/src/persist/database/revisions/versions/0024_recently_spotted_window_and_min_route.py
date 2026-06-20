"""Add per-bounty spotted_window + min_route/spotted-window guild overrides.

Two additive, nullable changes supporting the randomized "recently spotted"
window and the minimum-route-length rule:

bounty:
- spotted_window  (Integer)  — per-bounty look-ahead width B, rolled at spawn
                               from [0, recently_spotted_max_window]. NULL ==
                               legacy bounty (callers fall back to fixed 2).

guild_configs (NULL == use the matching GameConstants default):
- min_route_systems           (Integer)  — GameConstants.MIN_ROUTE_SYSTEMS (3)
- recently_spotted_max_window (Integer)  — GameConstants.RECENTLY_SPOTTED_MAX_WINDOW (3)

Idempotent: guards every op with inspector checks so fresh-install DBs (where
revision 0001 already materialised these columns from current ORM metadata) are
no-ops, while existing DBs receive the additive change. Mirrors revisions 0023
/ 0022 / 0021.

Revision ID: 0024
Revises: 0023
Create Date: 2026-06-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | Sequence[str] | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table_name, column_name, sqlalchemy_type)
_NEW_COLUMNS: tuple[tuple[str, str, sa.types.TypeEngine], ...] = (
    ("bounty", "spotted_window", sa.Integer()),
    ("guild_configs", "min_route_systems", sa.Integer()),
    ("guild_configs", "recently_spotted_max_window", sa.Integer()),
)


def _cols(inspector: sa.engine.reflection.Inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    cache: dict[str, set[str]] = {}
    for table, col_name, col_type in _NEW_COLUMNS:
        existing = cache.get(table)
        if existing is None:
            existing = _cols(inspector, table)
            cache[table] = existing
        if col_name not in existing:
            op.add_column(table, sa.Column(col_name, col_type, nullable=True))
            existing.add(col_name)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    cache: dict[str, set[str]] = {}
    for table, col_name, _col_type in reversed(_NEW_COLUMNS):
        existing = cache.get(table)
        if existing is None:
            existing = _cols(inspector, table)
            cache[table] = existing
        if col_name in existing:
            op.drop_column(table, col_name)
            existing.discard(col_name)
