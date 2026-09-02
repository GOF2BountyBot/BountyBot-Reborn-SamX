"""Wire sale_price_factor: default 0.8 -> 1.0 and migrate existing rows.

Context: `guild_configs.sale_price_factor` (documented "players sell at 80% of
base value") was never read by ShopService — every item sell paid full value,
so all guilds have always experienced 1:1 sell-back. Revision 0034 wires the
factor into ShopService.sell_item (issue #97) and, to preserve that live
behaviour, sets the column default to 1.0 AND brings every existing row to 1.0.

Why ALL rows, not just those at the old 0.8 default: because the factor never
took effect, no guild ever experienced a haircut regardless of its stored
value — so setting every row to 1.0 is what actually guarantees "no economy
change on deploy". An admin who now wants a sell-side credit sink sets the
factor explicitly (it finally works). Ships remain exempt (sell_ship is 1:1 by
design and is unaffected).

Idempotent (setting to 1.0 is naturally repeatable). Downgrade does not restore
prior per-row values — they conveyed no behaviour and are not recorded — so it
is a documented no-op on data.

Revision ID: 0034
Revises: 0033
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | Sequence[str] | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    if _has_column("guild_configs", "sale_price_factor"):
        op.execute("UPDATE guild_configs SET sale_price_factor = 1.0")


def downgrade() -> None:
    # Prior per-row values are not recoverable (the factor was unwired, so its
    # value conveyed no behaviour and was not preserved). No-op by design.
    pass
