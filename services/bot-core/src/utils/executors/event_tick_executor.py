"""Event tick executor — every-5-minute sweep to start scheduled and end active events.

Each event is processed in its own session so a failure on one event does
not block the others (non-fatal design, same pattern as bounty_failsafe_cleanup).

Imports are deferred to function scope (executor-wide convention).
"""

import traceback
from datetime import UTC, datetime

from shared.bblogger import get_logger

flogger = get_logger("event-tick-executor")


async def execute_event_tick_job(job_id: str, payload: dict) -> dict:
    """Tick handler: start due scheduled events and end due active events.

    Returns
    -------
    dict
        {"status": "success", "started": n, "ended": m, "errors": k}
    """
    from persist.database.manager import db_manager
    from persist.models.game_event import GameEvent
    from services.event_service import announce, end_event, start_event
    from sqlalchemy import and_, or_, select

    start_ts = datetime.now(UTC)
    flogger.info(f"EventTick[{job_id}] START")

    now = start_ts
    started = 0
    ended = 0
    errors = 0

    # One read session to collect IDs of events due for action.
    # ponytail: limit=500 cap prevents runaway on misconfigured data; raise if ever needed.
    async with db_manager.get_session() as read_db:
        result = await read_db.execute(
            select(GameEvent.id, GameEvent.state)
            .where(
                or_(
                    and_(GameEvent.state == "scheduled", GameEvent.scheduled_start_at <= now),
                    and_(GameEvent.state == "active", GameEvent.ends_at <= now),
                )
            )
            .limit(500)
        )
        due_events = result.all()  # list of (id, state) Row objects

    flogger.info(f"EventTick[{job_id}] found {len(due_events)} due event(s)")

    for row in due_events:
        event_id, event_state = row.id, row.state
        try:
            async with db_manager.get_session() as db:
                ev = await db.get(GameEvent, event_id)
                if ev is None:
                    flogger.warning(f"EventTick[{job_id}] event_id={event_id} vanished — skip")
                    continue
                if event_state == "scheduled" and ev.state == "scheduled":
                    ann = await start_event(db, ev)
                    await db.commit()
                    started += 1
                    flogger.info(f"EventTick[{job_id}] started event_id={event_id} guild={ev.guild_id}")
                    if ann:
                        try:
                            await announce(*ann)
                        except Exception as _ann_exc:  # pylint: disable=broad-exception-caught
                            flogger.error(f"EventTick[{job_id}] announce failed event_id={event_id}: {_ann_exc}")
                    try:
                        from utils.event_cache_push import _push_events_cache

                        await _push_events_cache(ev.guild_id)
                    except Exception as _push_exc:  # pylint: disable=broad-exception-caught
                        flogger.warning(f"EventTick[{job_id}] cache push failed event_id={event_id}: {_push_exc}")
                elif event_state == "active" and ev.state == "active":
                    result = await end_event(db, ev, payout=True)
                    await db.commit()
                    ended += 1
                    flogger.info(f"EventTick[{job_id}] ended event_id={event_id} guild={ev.guild_id}")
                    ann = result.get("announcement") if result else None
                    if ann:
                        try:
                            await announce(*ann)
                        except Exception as _ann_exc:  # pylint: disable=broad-exception-caught
                            flogger.error(f"EventTick[{job_id}] announce failed event_id={event_id}: {_ann_exc}")
                    try:
                        from utils.event_cache_push import _push_events_cache

                        await _push_events_cache(ev.guild_id)
                    except Exception as _push_exc:  # pylint: disable=broad-exception-caught
                        flogger.warning(f"EventTick[{job_id}] cache push failed event_id={event_id}: {_push_exc}")
                else:
                    flogger.debug(
                        f"EventTick[{job_id}] event_id={event_id} state changed to {ev.state!r} since read — skip"
                    )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            errors += 1
            flogger.error(f"EventTick[{job_id}] event_id={event_id} state={event_state} failed: {exc}")
            flogger.trace(traceback.format_exc())

    duration = (datetime.now(UTC) - start_ts).total_seconds()
    flogger.info(f"EventTick[{job_id}] DONE in {duration:.2f}s — started={started} ended={ended} errors={errors}")
    return {"status": "success", "started": started, "ended": ended, "errors": errors}
