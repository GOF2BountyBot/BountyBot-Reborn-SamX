"""Event service — core tallying for custom stat-race challenges (issue #30, spec §3).

Slice 1: record() and standings() only.
Slice 2: hooks into combat/duel/bounty services.
Slice 3: start_event(), end_event(), payout, announcements.
"""

from __future__ import annotations

import os
import traceback
from datetime import UTC, datetime, timedelta

import httpx
from persist.models.game_event import GameEvent, GameEventMetric
from persist.repositories.config_repository import ConfigRepository
from shared import bblogger
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from services.event_types import EVENT_TYPES, render_rules, resolve_metrics

_GATEWAY_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
_GATEWAY_PORT = os.getenv("GATEWAY_PORT", "7999")
_GATEWAY_BASE_URL = f"http://{_GATEWAY_HOST}:{_GATEWAY_PORT}/api/v1"

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


async def on_tier_change(session: AsyncSession, player) -> None:
    """Delete division-scoped metric rows for a player on tier change (spec §3).

    When a player promotes or demotes, their accumulations in division-gated events
    become irrelevant (they no longer belong to that division). This purges those rows
    so they start fresh in the new tier without needing re-baseline machinery.

    Non-fatal — same rule as record().  Called from player_service promote/demote only
    (prestige and admin reset are intentionally excluded — spec §3 / §12 cut-list).
    """
    try:
        from sqlalchemy import delete

        result = await session.execute(
            select(GameEvent).where(
                GameEvent.guild_id == player.guild_id,
                GameEvent.state == "active",
            )
        )
        active_events = result.scalars().all()

        division_event_ids = [ev.id for ev in active_events if (ev.params or {}).get("division")]
        if not division_event_ids:
            return

        await session.execute(
            delete(GameEventMetric).where(
                GameEventMetric.event_id.in_(division_event_ids),
                GameEventMetric.player_id == player.id,
            )
        )
        await session.flush()
        flogger.info(
            f"on_tier_change: deleted division-scoped metrics for player_id={player.id} "
            f"guild={player.guild_id} event_ids={division_event_ids}"
        )
    except Exception as exc:
        flogger.error(
            f"on_tier_change failed player_id={getattr(player, 'id', '?')} "
            f"guild={getattr(player, 'guild_id', '?')}: {exc}"
        )


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

    result = await session.execute(select(GameEventMetric).where(GameEventMetric.event_id == event.id))
    rows = result.scalars().all()

    # Fold rows by player
    by_player: dict[int, dict[str, float]] = {}
    for row in rows:
        by_player.setdefault(row.player_id, {})[row.metric] = float(row.value)

    params = event.params or {}
    effective_min = params.get("min_fights", et.default_min_fights)

    out: list[tuple[int, float, bool]] = []
    for pid, metrics in by_player.items():
        val = et.value(metrics) if et.value is not None else next(iter(metrics.values()), 0.0)
        qual = True
        if et.qualified is not None:
            qual = et.qualified(metrics)
        qual = qual and metrics.get(et.activity, 0) >= effective_min
        out.append((pid, val, qual))

    out.sort(key=lambda t: t[1], reverse=True)
    return out


# ---------------------------------------------------------------------------
# Display name helper (used by both event_service and events router)
# ---------------------------------------------------------------------------


def display_name(player) -> str:
    """Canonical display name for a Player ORM object with a loaded .user relationship."""
    return (
        (player.display_name if player.display_name else None)
        or (player.user.display_name if player.user and player.user.display_name else None)
        or (player.user.discord_username if player.user else None)
        or f"#{player.id}"
    )


# ---------------------------------------------------------------------------
# Query helpers — extracted from router so they run against real DB in tests
# ---------------------------------------------------------------------------


async def live_standings(session: AsyncSession, event: GameEvent) -> list[dict]:
    """Compute current standings for a live event, ranking qualified players only.

    Unqualified players are included but shown at rank=None so callers can
    display them without mixing them into the competition ranking.

    Returns: list of dicts sorted descending by value, shape:
        {player_id, value, qualified, rank (int for qualified, None for unqualified)}
    """
    from persist.models.player import Player
    from sqlalchemy.orm import selectinload

    raw = await standings(session, event)

    # Rank among qualified only (competition ranking: 1 + count with strictly higher val)
    qual_vals = [v for _, v, q in raw if q]

    player_ids = [pid for pid, _, _ in raw]
    pl_result = await session.execute(
        select(Player).options(selectinload(Player.user)).where(Player.id.in_(player_ids))
    )
    players_by_id = {p.id: p for p in pl_result.scalars().all()}

    et = EVENT_TYPES.get(event.type_slug)
    out: list[dict] = []
    for pid, val, qual in raw:
        p = players_by_id.get(pid)
        rk: int | None = (1 + sum(1 for v in qual_vals if v > val)) if qual else None
        out.append(
            {
                "player_id": pid,
                "user_id": p.user_id if p else 0,
                "display_name": display_name(p) if p else f"#{pid}",
                "value": val,
                "value_display": et.fmt(val) if et else str(val),
                "qualified": qual,
                "rank": rk,
            }
        )
    return out


async def final_standings(session: AsyncSession, event: GameEvent) -> list[dict]:
    """Read finalised standings from event_results (state=ended events only).

    Returns: list of dicts sorted by rank, shape:
        {player_id, user_id, display_name, value, qualified, rank}
    """
    from persist.models.game_event import EventResult
    from persist.models.player import Player
    from sqlalchemy.orm import selectinload

    er_result = await session.execute(
        select(EventResult).where(EventResult.event_id == event.id).order_by(EventResult.rank)
    )
    results = er_result.scalars().all()
    player_ids = [r.player_id for r in results]
    pl_result = await session.execute(
        select(Player).options(selectinload(Player.user)).where(Player.id.in_(player_ids))
    )
    players_by_id = {p.id: p for p in pl_result.scalars().all()}

    et = EVENT_TYPES.get(event.type_slug)
    out: list[dict] = []
    for r in results:
        p = players_by_id.get(r.player_id)
        val = r.value or 0.0
        out.append(
            {
                "player_id": r.player_id,
                "user_id": p.user_id if p else 0,
                "display_name": display_name(p) if p else f"#{r.player_id}",
                "value": val,
                "value_display": et.fmt(val) if et else str(val),
                "qualified": bool(r.qualified),
                "rank": r.rank or 0,
            }
        )
    return out


async def medals(session: AsyncSession, guild_id: int, type_slug: str | None = None) -> list[dict]:
    """Aggregate medal counts per player for a guild (Olympic ordering).

    Returns: list of dicts sorted by gold desc, silver desc, bronze desc, events desc.
        {player_id, user_id, display_name, gold, silver, bronze, events}
    """
    from persist.models.game_event import EventResult
    from persist.models.player import Player
    from sqlalchemy.orm import selectinload

    # ponytail: qualified is an Integer(0/1) column; is_(True) → "IS TRUE" fails on Postgres.
    # Use == 1 until the column is migrated to Boolean (follow-up task).
    q = select(EventResult).where(EventResult.guild_id == guild_id, EventResult.qualified == 1)
    if type_slug:
        q = q.where(EventResult.type_slug == type_slug)
    result = await session.execute(q)
    rows = result.scalars().all()

    player_ids = list({r.player_id for r in rows})
    pl_result = await session.execute(
        select(Player).options(selectinload(Player.user)).where(Player.id.in_(player_ids))
    )
    players_by_id = {p.id: p for p in pl_result.scalars().all()}

    agg: dict[int, dict] = {}
    for r in rows:
        pid = r.player_id
        if pid not in agg:
            agg[pid] = {"gold": 0, "silver": 0, "bronze": 0, "events": 0}
        agg[pid]["events"] += 1
        rk = r.rank or 99
        if rk == 1:
            agg[pid]["gold"] += 1
        elif rk == 2:
            agg[pid]["silver"] += 1
        elif rk == 3:
            agg[pid]["bronze"] += 1

    entries: list[dict] = []
    for pid, counts in agg.items():
        p = players_by_id.get(pid)
        entries.append(
            {
                "player_id": pid,
                "user_id": p.user_id if p else 0,
                "display_name": display_name(p) if p else f"#{pid}",
                **counts,
            }
        )

    entries.sort(key=lambda e: (-e["gold"], -e["silver"], -e["bronze"], -e["events"]))
    return entries


# ---------------------------------------------------------------------------
# Slice 3 helpers
# ---------------------------------------------------------------------------


def _ordinal(n: int) -> str:
    """1 → '1st', 2 → '2nd', 3 → '3rd', 4 → '4th', …"""
    suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10 if n % 100 not in (11, 12, 13) else 0, "th")
    return f"{n}{suf}"


def _validate_prize_ranges(prizes: list) -> None:
    """Raise ValueError if any two ranked prize slots have overlapping rank ranges."""
    ranked = [(p.rank_from, p.rank_to) for p in prizes if p.rank_from is not None]
    for i, (af, at) in enumerate(ranked):
        for j, (bf, bt) in enumerate(ranked):
            if i >= j:
                continue
            at_ = at if at is not None else af
            bt_ = bt if bt is not None else bf
            if af <= bt_ and bf <= at_:
                raise ValueError(f"Prize rank ranges overlap: {af}–{at} and {bf}–{bt}")


def _format_prize_list(prizes: list) -> str:
    """Human-readable prize list for start announcement embed."""
    lines: list[str] = []
    for p in sorted(prizes, key=lambda x: (x.rank_from is None, x.rank_from or 0)):
        if p.rank_from is None:
            place = "Participation"
        elif p.rank_from == p.rank_to:
            place = f"{_ordinal(p.rank_from)} Place"
        else:
            place = f"Top {p.rank_to}"
        reward = f"{p.qty:,} credits" if p.kind == "credits" else f"{p.qty}× {p.item_ref or '?'}"
        lines.append(f"**{place}:** {reward}")
    return "\n".join(lines) if lines else "None"


async def announce(guild_id: int, channel_id: int | None, embed: dict, text_content: str | None) -> None:
    """POST an embed to the gateway channel messages endpoint. Non-fatal."""
    if channel_id is None:
        flogger.warning(f"event_announce: guild={guild_id} no channel configured — skipping")
        return
    payload = {"content": embed, "text_content": text_content, "message_type": "default"}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_GATEWAY_BASE_URL}/channels/{channel_id}/messages",
                json=payload,
                timeout=10,
            )
        resp.raise_for_status()
        flogger.info(f"event_announce: guild={guild_id} channel={channel_id} posted OK")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"event_announce: guild={guild_id} channel={channel_id} failed: {exc}")
        flogger.trace(traceback.format_exc())


async def _fetch_member_discord_ids(guild_id: int) -> set[int] | None:
    """Return set of Discord user IDs currently in the guild. None on failure (log and skip filter)."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{_GATEWAY_BASE_URL}/guilds/{guild_id}/members",
                # ponytail: no pagination; guilds > 5000 members would silently forfeit — paginate when one exists
                params={"limit": 5000},
                timeout=10,
            )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        return {int(m["user"]["id"]) for m in data if "user" in m}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.warning(f"_fetch_member_discord_ids: guild={guild_id} failed: {exc} — skipping membership filter")
        return None


# ---------------------------------------------------------------------------
# Slice 3 — event lifecycle
# ---------------------------------------------------------------------------


async def start_event(session: AsyncSession, event: GameEvent) -> tuple[int, int | None, dict, str | None] | None:
    """Activate a scheduled or draft event: validate, set state=active, flush.

    Caller posts the returned announcement AFTER commit; posting before commit is a double-payout path.

    Raises ValueError (player-readable) on validation failure so the tick executor
    can log and skip this event without blocking others.

    Returns the (guild_id, channel_id, embed, text_content) announcement tuple, or None
    if no channel is configured (announcement skipped).
    """
    from persist.models.game_event import GameEventPrize

    if event.state not in ("draft", "scheduled"):
        raise ValueError(f"Cannot start event in state={event.state!r} (event_id={event.id})")

    prizes_result = await session.execute(select(GameEventPrize).where(GameEventPrize.event_id == event.id))
    prizes = prizes_result.scalars().all()
    _validate_prize_ranges(prizes)

    config = await _config_repo.get_by_guild_id(session, event.guild_id)
    if not config or not config.discussion_channel_id:
        raise ValueError(
            f"Guild {event.guild_id} has no discussion_channel_id configured — configure it before starting events"
        )

    now = datetime.now(UTC)
    event.state = "active"
    event.started_at = now
    event.ends_at = now + timedelta(days=event.duration_days)
    event.scheduled_start_at = None
    event.updated_at = now
    await session.flush()

    flogger.info(
        f"start_event: event_id={event.id} guild={event.guild_id} type={event.type_slug} "
        f"started_at={now.isoformat()} ends_at={event.ends_at.isoformat()}"
    )

    # Build announcement (caller posts it AFTER commit)
    et = EVENT_TYPES.get(event.type_slug)
    rid = config.event_announcements_role_id
    role_mention = f"<@&{rid}>" if rid else None
    params = event.params or {}
    effective_min_fights = params.get("min_fights", et.default_min_fights if et else 1)
    min_duel_stakes: int = config.event_min_duel_stakes if config else 1000
    rules_rendered = (
        render_rules(
            et,
            min_stakes=min_duel_stakes,
            min_fights=effective_min_fights,
            division=params.get("division"),
            params=params,
        )
        if et
        else ""
    )
    embed = {
        "title": f"🏆 {et.display_name if et else event.type_slug} Event Started!",
        "description": rules_rendered,
        "color": 9699539,  # purple #941733
        "fields": [
            {"name": "Ends", "value": f"<t:{int(event.ends_at.timestamp())}:R>", "inline": True},
            {"name": "Prizes", "value": _format_prize_list(prizes), "inline": False},
        ],
    }
    if not config.discussion_channel_id:
        return None
    return (event.guild_id, config.discussion_channel_id, embed, role_mention)


async def end_event(
    session: AsyncSession,
    event: GameEvent,
    *,
    payout: bool,
    reason: str | None = None,
    actor_user_id: int | None = None,
) -> dict:
    """End an active event: idempotent state transition, optional payout, audit.

    Caller posts the returned announcement AFTER commit; posting before commit is a double-payout path.

    Returns a summary dict with an "announcement" key containing the
    (guild_id, channel_id, embed, text_content) tuple to post, or None if nothing to post.
    The caller owns the transaction (flush only; caller commits).
    """
    from persist.models.game_event import EventResult, GameEventPrize
    from persist.models.player import Player
    from persist.repositories.ship_repository import ShipRepository
    from sqlalchemy.orm import selectinload

    from services.audit_service import AuditService
    from services.inventory_service import InventoryService

    new_state = "ended" if payout else "cancelled"
    now = datetime.now(UTC)

    # Concurrent-end guard: lock the row before checking state.
    # with_for_update() is a no-op on SQLite; on Postgres it serialises concurrent end_event
    # calls so only the first can pass the active-state check in the UPDATE below.
    # ponytail: per-row lock is sufficient; upgrade to advisory lock only if contention spikes.
    await session.execute(select(GameEvent).where(GameEvent.id == event.id).with_for_update())

    # Idempotency: UPDATE only if currently active
    result = await session.execute(
        update(GameEvent)
        .where(GameEvent.id == event.id, GameEvent.state == "active")
        .values(state=new_state, updated_at=now, ends_at=now)  # ends_at = when it actually ended
    )
    if result.rowcount == 0:
        flogger.info(f"end_event: event_id={event.id} not active (rowcount=0) — idempotent no-op")
        return {}

    # Sync ORM object to avoid stale reads
    event.state = new_state
    event.updated_at = now

    config = await _config_repo.get_by_guild_id(session, event.guild_id)
    channel_id = config.discussion_channel_id if config else None
    et = EVENT_TYPES.get(event.type_slug)

    if not payout:
        embed = {
            "title": f"❌ {et.display_name if et else event.type_slug} Event Cancelled",
            "description": reason or "Event cancelled.",
            "color": 16711680,
            "fields": [],
        }
        ann = (event.guild_id, channel_id, embed, None) if channel_id else None
        return {"status": "cancelled", "announcement": ann}

    # --- Payout path ---

    all_standings = await standings(session, event)
    qual = [(pid, val) for pid, val, q in all_standings if q]

    if not qual:
        flogger.info(f"end_event: event_id={event.id} no qualified players — skipping payout")
        ann_embed = {
            "title": f"🏁 {et.display_name if et else event.type_slug} Event Ended",
            "description": "No qualified players.",
            "color": 7506394,
            "fields": [],
        }
        ann = (event.guild_id, channel_id, ann_embed, None) if channel_id else None
        return {"status": "none", "ranked_players": 0, "announcement": ann}

    # Load Player objects (with User for display names and user_id for membership filter)
    player_result = await session.execute(
        select(Player).options(selectinload(Player.user)).where(Player.id.in_([pid for pid, _ in qual]))
    )
    players_by_id: dict[int, Player] = {p.id: p for p in player_result.scalars().all()}

    # Membership filter: drop departed players (lesser evil = pay them; log if gateway fails)
    member_ids = await _fetch_member_discord_ids(event.guild_id)
    if member_ids is not None:
        qual = [(pid, val) for pid, val in qual if players_by_id.get(pid) and players_by_id[pid].user_id in member_ids]

    # Competition rank: 1 + count of players with strictly greater value
    def _rank(player_val: float) -> int:
        return 1 + sum(1 for _, v in qual if v > player_val)

    ranked: list[tuple[int, float, int]] = [(pid, val, _rank(val)) for pid, val in qual]

    # Load prize slots
    prize_result = await session.execute(select(GameEventPrize).where(GameEventPrize.event_id == event.id))
    prizes = prize_result.scalars().all()

    inv_svc = InventoryService()
    ship_repo = ShipRepository()

    # per-player prize text and status parts
    prize_parts: dict[int, list[str]] = {pid: [] for pid, _, _ in ranked}
    status_parts: dict[int, list[str]] = {pid: [] for pid, _, _ in ranked}

    for slot in prizes:
        # Determine eligible players for this slot
        if slot.rank_from is None:  # participation
            slot_players = ranked
        else:
            rank_to = slot.rank_to if slot.rank_to is not None else slot.rank_from
            slot_players = [(pid, val, rk) for pid, val, rk in ranked if slot.rank_from <= rk <= rank_to]

        for pid, _val, rk in slot_players:
            player = players_by_id.get(pid)
            if player is None:
                continue
            rank_label = _ordinal(rk) if slot.rank_from is not None else "participation"
            try:
                if slot.kind == "credits":
                    from services.player_service import PlayerService  # deferred to avoid circular import

                    # ponytail: update_player_credits uses FOR UPDATE — redundant here since payout
                    # is one-shot (event state already "ended" via idempotency guard above), but
                    # reusing the service ensures lifetime_credits is updated consistently.
                    # commit=False: caller (router / tick executor) owns the transaction via db.begin().
                    await PlayerService().update_player_credits(
                        session, player.id, player.credits + slot.qty, commit=False
                    )
                    prize_parts[pid].append(f"{rank_label}: {slot.qty:,} credits")
                elif slot.kind == "item":
                    if not slot.item_ref:
                        raise ValueError("item prize missing item_ref")
                    item_details = await inv_svc.get_item_details(session, slot.item_ref)
                    if not item_details:
                        raise ValueError(f"Item {slot.item_ref!r} not in game catalog")
                    await inv_svc.add_item_to_inventory(
                        session, pid, item_details["type"], slot.item_ref, slot.qty, commit=False
                    )
                    prize_parts[pid].append(f"{rank_label}: {slot.qty}× {slot.item_ref}")
                elif slot.kind == "ship":
                    if not slot.item_ref:
                        raise ValueError("ship prize missing item_ref")
                    game_ship = await ship_repo.get_by_name(session, slot.item_ref)
                    if not game_ship:
                        raise ValueError(f"Ship {slot.item_ref!r} not in game catalog")
                    for _ in range(slot.qty):
                        await inv_svc.grant_ship(session, player, game_ship)
                    prize_parts[pid].append(f"{rank_label}: {slot.qty}× {slot.item_ref}")
                else:
                    raise ValueError(f"Unknown prize kind={slot.kind!r}")
                status_parts[pid].append("ok")
            except Exception as exc:  # pylint: disable=broad-exception-caught
                flogger.error(
                    f"end_event: mint failed event_id={event.id} player_id={pid} "
                    f"slot_id={slot.id} kind={slot.kind}: {exc}"
                )
                status_parts[pid].append("partial")

    # Write event_results rows (one per qualified + member player)
    # status ∈ {"ok", "partial", "none"}: failure details are in the audit log
    for pid, val, rk in ranked:
        prize_text = "; ".join(prize_parts.get(pid, [])) or "—"
        parts = status_parts.get(pid, [])
        if not parts:
            status_text = "none"
        elif all(s == "ok" for s in parts):
            status_text = "ok"
        else:
            status_text = "partial"
        session.add(
            EventResult(
                event_id=event.id,
                guild_id=event.guild_id,
                type_slug=event.type_slug,
                player_id=pid,
                rank=rk,
                value=val,
                qualified=True,
                prize=prize_text[:256],  # column is String(256)
                status=status_text,
                awarded_at=now,
            )
        )
    await session.flush()

    # Audit log (commit=False — caller commits)
    winner_summary = {
        str(pid): {"rank": rk, "value": val, "prize": "; ".join(prize_parts.get(pid, []))}
        for pid, val, rk in ranked
        if rk <= 3
    }
    failures = [
        f"player_id={pid}: partial mint failure (slot details in ERROR log)"
        for pid, parts in status_parts.items()
        if any(s == "partial" for s in parts)
    ]
    await AuditService.log_action(
        session,
        user_id=actor_user_id or event.created_by_user_id or 0,
        action="event_payout",
        guild_id=event.guild_id,
        resource_type="event",
        resource_id=str(event.id),
        details={"winners": winner_summary, "failures": failures},
        commit=False,
    )

    # End announcement
    rid = config.event_announcements_role_id if config else None
    role_mention = f"<@&{rid}>" if rid else None

    # Build standings fields for prize-winning ranks
    standing_lines: list[str] = []
    for pid, val, rk in ranked[:10]:  # cap at 10 to fit embed
        player = players_by_id.get(pid)
        name = display_name(player) if player else f"#{pid}"
        val_str = et.fmt(val) if et else str(val)
        pz = "; ".join(prize_parts.get(pid, []))
        suffix = f" · {pz}" if pz else ""
        standing_lines.append(f"{_ordinal(rk)}: **{name}** — {val_str}{suffix}")
    # Collect @mentions of placed winners (rank ≤ highest prize rank, capped at 10)
    top_mention_pids = {pid for pid, _, rk in ranked if rk <= 3}
    mention_str = " ".join(f"<@{players_by_id[pid].user_id}>" for pid in top_mention_pids if pid in players_by_id)
    text_content = " ".join(filter(None, [role_mention, mention_str])) or None

    embed_fields: list[dict] = [{"name": "Participants", "value": str(len(ranked)), "inline": True}]
    participation_slot = next((s for s in prizes if s.rank_from is None), None)
    if participation_slot:
        part_prize = (
            f"{participation_slot.qty:,} credits"
            if participation_slot.kind == "credits"
            else f"{participation_slot.qty}× {participation_slot.item_ref or '?'}"
        )
        embed_fields.append(
            {"name": "Participation", "value": f"{part_prize} — {len(ranked)} recipients", "inline": True}
        )
    embed = {
        "title": f"🏁 {et.display_name if et else event.type_slug} Event Ended",
        "description": "\n".join(standing_lines) or "No qualified finishers.",
        "color": 3066993,
        "fields": embed_fields,
    }
    ann = (event.guild_id, channel_id, embed, text_content) if channel_id else None
    return {"status": "ok", "ranked_players": len(ranked), "errors": len(failures), "announcement": ann}
