"""CI-18: Add unique constraint on player_inventories(player_id, item_type, item_name).

Prevents duplicate inventory rows for the same item.  A defensive dedup
pre-flight (merge quantities into the lowest-id row, delete duplicates) runs
BEFORE creating the constraint so a dirty DB with pre-existing duplicates
cannot crash the boot-loop.

Idempotent: inspector-guarded so fresh-install DBs (where the ORM metadata
already declared the constraint) are a no-op.

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | None = None
depends_on: str | None = None

_TABLE = "player_inventories"
_UQ = "uq_player_inventories_player_item"
_COLS = ("player_id", "item_type", "item_name")


def _uniques(insp: sa.engine.reflection.Inspector) -> set[str]:
    return {c["name"] for c in insp.get_unique_constraints(_TABLE)}


def upgrade() -> None:
    """Add unique constraint (idempotent, with dedup pre-flight)."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if _UQ in _uniques(insp):
        return  # fresh-install DB already has it from ORM metadata

    # Dedup pre-flight: merge quantities into the lowest-id row, then delete duplicates.
    # This prevents the subsequent create_unique_constraint from failing on dirty DBs.
    op.execute(
        sa.text(
            f"UPDATE {_TABLE} t SET quantity = s.total FROM ("
            f"  SELECT MIN(id) AS keep_id, player_id, item_type, item_name, SUM(quantity) AS total"
            f"  FROM {_TABLE} GROUP BY player_id, item_type, item_name HAVING COUNT(*) > 1"
            f") s WHERE t.id = s.keep_id"
        )
    )
    op.execute(
        sa.text(
            f"DELETE FROM {_TABLE} t USING ("
            f"  SELECT id, MIN(id) OVER (PARTITION BY player_id, item_type, item_name) AS keep_id FROM {_TABLE}"
            f") d WHERE t.id = d.id AND t.id <> d.keep_id"
        )
    )
    op.create_unique_constraint(_UQ, _TABLE, list(_COLS))


def downgrade() -> None:
    """Drop unique constraint (idempotent)."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if _UQ in _uniques(insp):
        op.drop_constraint(_UQ, _TABLE, type_="unique")
