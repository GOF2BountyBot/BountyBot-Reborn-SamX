"""Combat rewrite Phase-1 schema hooks.

Adds the catalog + state columns the tick-based combat resolver (and its
Phase-2 OOC-recovery hooks) need to land into. All columns are additive
and nullable — existing production data is untouched.

Columns added:

- ``ship.extra_atts`` (JSON, default ``{}``)
    Brings ``Ship`` into line with ``Weapon`` / ``Module``, which already
    own this escape hatch. PR-3's seed-enrichment loader will stash wiki
    fields here (mechanics prose, DLC tags, Android price, the Vossk
    Battlecruiser ``wiki_status: "missing"`` sentinel, etc.) without
    needing future migrations for cosmetic fields.

- ``players.current_hull / current_armour / current_shield`` (Integer, nullable)
- ``players.last_damage_at`` (DateTime tz, nullable)
    Phase-2 OOC-recovery hooks. Phase-1 always starts combat at full HP
    so these are populated but unused by the resolver. Landing the
    schema now avoids a second migration cycle when Phase-2 recovery
    jobs come online (25%/hr player recovery — see Entry 5).

- ``bounty.criminal_current_hull / criminal_current_armour / criminal_current_shield`` (Integer, nullable)
- ``bounty.criminal_last_damage_at`` (DateTime tz, nullable)
    Symmetric Phase-2 hooks for criminal NPCs (12.5%/hr recovery,
    guild-configurable). Same Phase-1 inertness as the player columns.

Follows the defensive ``inspector.get_columns()`` pattern established by
revision 0008: fresh-install DBs where 0001 already materialised the
columns from current ORM metadata are no-ops. Existing prod DBs (where
the columns are absent) get them added.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-26

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_columns(inspector: sa.engine.reflection.Inspector, table: str) -> set[str]:
    return {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    """Add Phase-1/Phase-2 combat schema hooks.

    Idempotent: skips any column that already exists on the target DB. This
    keeps fresh installs (where revision 0001 builds tables from current
    ORM metadata) compatible with existing deployments (where the columns
    are absent).
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- ship.extra_atts ----------------------------------------------------
    if "extra_atts" not in _existing_columns(inspector, "ship"):
        op.add_column(
            "ship",
            sa.Column(
                "extra_atts",
                sa.JSON(),
                nullable=True,
                server_default=sa.text("'{}'::json"),
            ),
        )

    # --- players: Phase-2 damage-tracking hooks ----------------------------
    player_cols = _existing_columns(inspector, "players")
    for col_name, col_type in (
        ("current_hull", sa.Integer()),
        ("current_armour", sa.Integer()),
        ("current_shield", sa.Integer()),
        ("last_damage_at", sa.DateTime(timezone=True)),
    ):
        if col_name not in player_cols:
            op.add_column("players", sa.Column(col_name, col_type, nullable=True))

    # --- bounty: criminal damage-tracking hooks ----------------------------
    bounty_cols = _existing_columns(inspector, "bounty")
    for col_name, col_type in (
        ("criminal_current_hull", sa.Integer()),
        ("criminal_current_armour", sa.Integer()),
        ("criminal_current_shield", sa.Integer()),
        ("criminal_last_damage_at", sa.DateTime(timezone=True)),
    ):
        if col_name not in bounty_cols:
            op.add_column("bounty", sa.Column(col_name, col_type, nullable=True))


def downgrade() -> None:
    """Reverse the combat Phase-1 schema additions.

    All columns are dropped only if present. Safe to run on a DB where 0009
    was partially applied or where a fresh install never had the columns
    via this revision in the first place.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    bounty_cols = _existing_columns(inspector, "bounty")
    for col_name in (
        "criminal_last_damage_at",
        "criminal_current_shield",
        "criminal_current_armour",
        "criminal_current_hull",
    ):
        if col_name in bounty_cols:
            op.drop_column("bounty", col_name)

    player_cols = _existing_columns(inspector, "players")
    for col_name in (
        "last_damage_at",
        "current_shield",
        "current_armour",
        "current_hull",
    ):
        if col_name in player_cols:
            op.drop_column("players", col_name)

    if "extra_atts" in _existing_columns(inspector, "ship"):
        op.drop_column("ship", "extra_atts")
