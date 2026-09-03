"""Event service — core tallying for custom stat-race challenges (issue #30, spec §3).

Slice 1: record() and standings() only.
Slice 2 (TODO): hooks into combat/duel/bounty services.
"""

from __future__ import annotations

from persist.models.game_event import GameEvent, GameEventMetric
from persist.repositories.config_repository import ConfigRepository
from shared import bblogger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.event_types import EVENT_TYPES, resolve_metrics

flogger = bblogger.get_logger("event-service")

_config_repo = ConfigRepository()


async def record(
    session: AsyncSession,
    player,  # Player ORM object — needs .guild_id, .tier (str), .user_id (int)
    contrib: dict[str, float],
    *,
    context: str,
    stakes: int | None = None,
) -> None:
    """Upsert event metric rows for a player contribution (spec §3).

    - Loads active events for the player's guild in one query.
    - Applies duel-stakes filter (context=="duel" && stakes < event_min_duel_stakes → skip).
    - Skips division-scoped events where player.tier != params["division"].
    - For each consumed metric does a native upsert (sum or max).
    - Never raises — non-fatal, same rule as _increment_player_stats.
    - Flushes only; the caller owns the transaction.
    """
    try:
        # Load guild config for stakes floor
        config = await _config_repo.get_by_guild_id(session, player.guild_id)
        min_stakes: int = config.event_min_duel_stakes if config else 1000

        # One query: all active events for this guild
        result = await session.execute(
            select(GameEvent).where(
                GameEvent.guild_id == player.guild_id,
                GameEvent.state == "active",
            )
        )
        active_events = result.scalars().all()

        if not active_events:
            return

        dialect = session.bind.dialect.name  # type: ignore[union-attr]

        for event in active_events:
            et = EVENT_TYPES.get(event.type_slug)
            if et is None:
                flogger.warning(f"Unknown event type slug={event.type_slug!r} event_id={event.id} — skipping")
                continue

            # Duel-stakes filter
            if context == "duel" and (stakes is None or stakes < min_stakes):
                continue

            # Division gate
            params = event.params or {}
            if "division" in params and player.tier != params["division"]:
                continue

            # Expand parameterised metric keys
            metric_modes = resolve_metrics(event.type_slug, params)

            for metric_key, agg_mode in metric_modes.items():
                contrib_value = contrib.get(metric_key, 0.0)
                if contrib_value == 0.0 and agg_mode == "sum":
                    # Nothing to add; still upsert for max so we create the row if absent
                    # but for sum skip the round-trip entirely.
                    continue

                await _upsert_metric(session, dialect, event.id, player.id, metric_key, contrib_value, agg_mode)

        await session.flush()

    except Exception as exc:
        flogger.error(
            f"event_service.record failed player_id={getattr(player, 'id', '?')} "
            f"guild={getattr(player, 'guild_id', '?')} context={context}: {exc}"
        )
        # Non-fatal — never abort the caller


async def _upsert_metric(
    session: AsyncSession,
    dialect: str,
    event_id: int,
    player_id: int,
    metric: str,
    contrib_value: float,
    agg_mode: str,
) -> None:
    """Native upsert: INSERT … ON CONFLICT DO UPDATE for sum or max aggregation.

    Uses the dialect-appropriate insert to avoid ORM read-modify-write under concurrent writes.
    SQLite's func.max(a, b) is the two-argument MAX scalar (same semantics as GREATEST on PG).
    # ponytail: dialect branch covers sqlite+postgresql only; add others if deployed there.
    """
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        _insert = pg_insert
        _max_fn = func.greatest
    else:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        _insert = sqlite_insert
        _max_fn = func.max

    stmt = _insert(GameEventMetric).values(
        event_id=event_id,
        player_id=player_id,
        metric=metric,
        value=contrib_value,
    )
    if agg_mode == "max":
        stmt = stmt.on_conflict_do_update(
            index_elements=["event_id", "player_id", "metric"],
            set_={"value": _max_fn(GameEventMetric.value, stmt.excluded.value)},
        )
    else:
        stmt = stmt.on_conflict_do_update(
            index_elements=["event_id", "player_id", "metric"],
            set_={"value": GameEventMetric.value + stmt.excluded.value},
        )
    await session.execute(stmt)


async def standings(
    session: AsyncSession,
    event: GameEvent,
) -> list[tuple[int, float, bool]]:
    """Read metric rows for an event and compute (player_id, value, qualified) tuples.

    Pure Python fold over the rows — no SQL aggregation.
    Returns list sorted descending by value.
    """
    et = EVENT_TYPES.get(event.type_slug)
    if et is None:
        flogger.warning(f"standings: unknown slug={event.type_slug!r} event_id={event.id}")
        return []

    result = await session.execute(
        select(GameEventMetric).where(GameEventMetric.event_id == event.id)
    )
    rows = result.scalars().all()

    # Fold rows by player
    by_player: dict[int, dict[str, float]] = {}
    for row in rows:
        by_player.setdefault(row.player_id, {})[row.metric] = float(row.value)

    out: list[tuple[int, float, bool]] = []
    for pid, metrics in by_player.items():
        val = et.value(metrics) if et.value is not None else next(iter(metrics.values()), 0.0)
        qual = True
        if et.qualified is not None:
            qual = et.qualified(metrics)
        if et.min_fights is not None:
            qual = qual and metrics.get("fights", 0) >= et.min_fights
        out.append((pid, val, qual))

    out.sort(key=lambda t: t[1], reverse=True)
    return out
