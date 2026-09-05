"""Custom events core — 4 new tables + guild_configs.event_min_duel_stakes (issue #30, slice 1).

Tables added:
  game_events          — one row per event (draft/scheduled/active/ended/cancelled)
  game_event_prizes    — prize pool entries for an event
  game_event_metrics   — per-player metric accumulators, PK (event_id, player_id, metric)
  event_results        — permanent post-payout snapshot (written on ended only)

Column added:
  guild_configs.event_min_duel_stakes INTEGER NOT NULL DEFAULT 1000
    Minimum duel-stakes (credits) for a duel contribution to count toward event tallies.
    Global per-guild; guild admins adjust via /admin_config. Default 1000 per spec §3.

JSON column: portable JSON().with_variant(JSONB(), "postgresql") — same pattern as
  combat_log and bounty models.

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0035"
down_revision: str | Sequence[str] | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Portable JSON type — same trick as combat_log.py
_JSONB = sa.JSON().with_variant(JSONB(), "postgresql")


def _cols(inspector: sa.engine.reflection.Inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}


def _indexes(inspector: sa.engine.reflection.Inspector, table: str) -> set[str]:
    return {i["name"] for i in inspector.get_indexes(table)}


def upgrade() -> None:
    """Idempotent: every op is guarded so fresh installs (where ``create_all`` already
    built the schema from the models before Alembic runs) and re-runs are no-ops."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # -- guild_configs: new column -----------------------------------------
    if "event_min_duel_stakes" not in _cols(inspector, "guild_configs"):
        op.add_column(
            "guild_configs",
            sa.Column(
                "event_min_duel_stakes",
                sa.Integer(),
                nullable=False,
                server_default="1000",
            ),
        )

    # -- game_events ----------------------------------------------------------
    if not inspector.has_table("game_events"):
        op.create_table(
            "game_events",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("guild_id", sa.BigInteger(), nullable=False),
            sa.Column("type_slug", sa.String(64), nullable=False),
            sa.Column("params", _JSONB, nullable=False, server_default="{}"),
            sa.Column("state", sa.String(16), nullable=False, server_default="draft"),
            sa.Column("duration_days", sa.Integer(), nullable=False, server_default="7"),
            sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    if "ix_game_events_guild_state" not in _indexes(inspector, "game_events"):
        op.create_index("ix_game_events_guild_state", "game_events", ["guild_id", "state"])

    # -- game_event_prizes ----------------------------------------------------
    if not inspector.has_table("game_event_prizes"):
        op.create_table(
            "game_event_prizes",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("event_id", sa.Integer(), nullable=False),
            sa.Column("rank_from", sa.Integer(), nullable=True),
            sa.Column("rank_to", sa.Integer(), nullable=True),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("item_ref", sa.String(128), nullable=True),
            sa.Column("qty", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["event_id"], ["game_events.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    # -- game_event_metrics ---------------------------------------------------
    if not inspector.has_table("game_event_metrics"):
        op.create_table(
            "game_event_metrics",
            sa.Column("event_id", sa.Integer(), nullable=False),
            sa.Column("player_id", sa.Integer(), nullable=False),
            sa.Column("metric", sa.String(128), nullable=False),
            sa.Column("value", sa.Numeric(precision=20, scale=4), nullable=False),
            sa.ForeignKeyConstraint(["event_id"], ["game_events.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("event_id", "player_id", "metric"),
        )

    # -- event_results --------------------------------------------------------
    if not inspector.has_table("event_results"):
        op.create_table(
            "event_results",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("event_id", sa.Integer(), nullable=False),
            sa.Column("guild_id", sa.BigInteger(), nullable=False),
            sa.Column("type_slug", sa.String(64), nullable=False),
            sa.Column("player_id", sa.Integer(), nullable=False),
            sa.Column("rank", sa.Integer(), nullable=True),
            sa.Column("value", sa.Float(), nullable=True),
            sa.Column("qualified", sa.Integer(), nullable=True),
            sa.Column("prize", sa.String(256), nullable=True),
            sa.Column("status", sa.String(32), nullable=True),
            sa.Column("awarded_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["event_id"], ["game_events.id"]),
            sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
    er_idx = _indexes(inspector, "event_results")
    if "ix_event_results_guild_player" not in er_idx:
        op.create_index("ix_event_results_guild_player", "event_results", ["guild_id", "player_id"])
    if "ix_event_results_guild_slug" not in er_idx:
        op.create_index("ix_event_results_guild_slug", "event_results", ["guild_id", "type_slug"])


def downgrade() -> None:
    op.drop_index("ix_event_results_guild_slug", table_name="event_results")
    op.drop_index("ix_event_results_guild_player", table_name="event_results")
    op.drop_table("event_results")
    op.drop_table("game_event_metrics")
    op.drop_table("game_event_prizes")
    op.drop_index("ix_game_events_guild_state", table_name="game_events")
    op.drop_table("game_events")
    op.drop_column("guild_configs", "event_min_duel_stakes")
