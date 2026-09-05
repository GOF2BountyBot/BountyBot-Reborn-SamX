"""Game event models — custom stat-race challenges (issue #30).

Four tables per spec §2:
  game_events          — one row per event (draft/scheduled/active/ended/cancelled)
  game_event_prizes    — prize pool rows for an event
  game_event_metrics   — per-player metric accumulators (soft enrolment, upserted by hooks)
  event_results        — permanent post-payout snapshot (written on ended only)
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

# Portable JSON: Postgres uses JSONB; SQLite unit tests fall back to JSON.
_JSONB = JSON().with_variant(JSONB(), "postgresql")

from persist.database.tablenames import TableNames
from persist.models.base import Base


class GameEvent(Base):
    __tablename__ = TableNames.GameEvents.value
    __table_args__ = (
        Index("ix_game_events_guild_state", "guild_id", "state"),
        # Template names are unique per guild (only rows in state='template' carry a name).
        Index(
            "ux_game_events_template_name",
            "guild_id",
            "name",
            unique=True,
            postgresql_where=text("state = 'template'"),
            sqlite_where=text("state = 'template'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    type_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    # params holds type-specific keys: division, weapon, subtype, module — only what the type needs
    params: Mapped[dict] = mapped_column(_JSONB, nullable=False, server_default="{}")
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft"
    )  # draft/scheduled/active/ended/cancelled/template
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)  # templates only
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    scheduled_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GameEventPrize(Base):
    __tablename__ = TableNames.GameEventPrizes.value

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{TableNames.GameEvents.value}.id", ondelete="CASCADE"), nullable=False
    )
    # rank_from / rank_to: both NULL = participation; k..k = per-rank; 1..N = Top N
    rank_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rank_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # credits/item/ship
    item_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)


class GameEventMetric(Base):
    __tablename__ = TableNames.GameEventMetrics.value
    __table_args__ = (
        # PK (event_id, player_id, metric) enforced at DB level via primary_key cols below
    )

    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{TableNames.GameEvents.value}.id", ondelete="CASCADE"), primary_key=True, nullable=False
    )
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{TableNames.Players.value}.id", ondelete="CASCADE"), primary_key=True, nullable=False
    )
    metric: Mapped[str] = mapped_column(String(128), primary_key=True, nullable=False)
    # Numeric for precision on credit sums; Float acceptable on SQLite (no Numeric dialect there)
    value: Mapped[float] = mapped_column(Numeric(precision=20, scale=4).with_variant(Float(), "sqlite"), nullable=False)


class EventResult(Base):
    __tablename__ = TableNames.EventResults.value
    __table_args__ = (
        Index("ix_event_results_guild_player", "guild_id", "player_id"),
        Index("ix_event_results_guild_slug", "guild_id", "type_slug"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey(f"{TableNames.GameEvents.value}.id"), nullable=False)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    type_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{TableNames.Players.value}.id", ondelete="CASCADE"), nullable=False
    )
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    qualified: Mapped[bool | None] = mapped_column(Integer, nullable=True)  # stored as 0/1 on SQLite
    prize: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    awarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
