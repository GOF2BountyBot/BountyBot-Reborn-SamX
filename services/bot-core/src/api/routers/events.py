"""Events API router — custom stat-race challenges (issue #30, spec §5–6).

All mutations audit via AuditService.log_action(..., commit=False) inside
the transaction. Announcements are posted AFTER commit (announce-after-commit
contract — see event_service docstrings).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query
from persist.database.manager import get_db_session
from persist.models.game_event import GameEvent, GameEventPrize
from persist.repositories.config_repository import ConfigRepository
from persist.repositories.ship_repository import ShipRepository
from services.audit_service import AuditService
from services.combat_resolver import _ACTIVATION_MODULES
from services.event_types import EVENT_TYPES, render_rules
from services.inventory_service import InventoryService
from shared import bblogger
from sqlalchemy import func, select
from utils.event_cache_push import _push_events_cache

from api.routers.admin import verify_admin_permissions
from api.schemas.events_schema import (
    AddPrizeRequest,
    CreateEventRequest,
    EndEventRequest,
    EventDetailResponse,
    EventListItem,
    EventResponse,
    EventTypeInfo,
    MedalEntry,
    PrizeResponse,
    StandingEntry,
    StartEventRequest,
)
from services import event_service

flogger = bblogger.get_logger("events-router")

router = APIRouter(prefix="/events", tags=["events"])

_config_repo = ConfigRepository()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_KNOWN_PARAM_KEYS = {"division", "weapon", "subtype", "module", "min_fights"}
_VALID_DIVISIONS = {"Bronze", "Silver", "Gold", "Platinum"}
_VALID_SUBTYPES: frozenset[str] = frozenset({
    "nuke", "rocket", "missile", "cluster-missile", "emp-bomb", "shock-blast", "ionizing-missile",
})
# resolver: emp-bomb is a no-op; shock-blast/ionizing-missile deal 0 HP → cannot score a fire event
_SCORABLE_FIRE_SUBTYPES: frozenset[str] = _VALID_SUBTYPES - {"emp-bomb"}
# resolver: emp-bomb is a no-op; shock-blast/ionizing-missile deal 0 HP → cannot be a killing blow
_SCORABLE_KILL_WEAPONS: frozenset[str] = (_VALID_SUBTYPES - {"emp-bomb", "shock-blast", "ionizing-missile"}) | {
    "primary", "turret"
}
_VALID_WEAPON_VALS: frozenset[str] = _VALID_SUBTYPES | {"primary", "turret"}
_VALID_STATES: frozenset[str] = frozenset({"draft", "scheduled", "active", "ended", "cancelled"})


def _load_event_or_404(event: GameEvent | None, event_id: int, guild_id: int | None = None) -> GameEvent:
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    if guild_id is not None and event.guild_id != guild_id:
        raise HTTPException(status_code=403, detail="Event belongs to another guild")
    return event


def _assert_state_or_409(event: GameEvent, *allowed: str) -> None:
    if event.state not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"Event {event.id} is in state={event.state!r}; expected one of {list(allowed)}",
        )


def _validate_params(type_slug: str, params: dict) -> None:
    et = EVENT_TYPES.get(type_slug)
    if et is None:
        raise HTTPException(status_code=400, detail=f"Unknown event type: {type_slug!r}")
    # Infer which param keys this type accepts from its metric templates
    accepted: set[str] = set()
    required: set[str] = set()
    for tmpl in et.metrics:
        for key in _KNOWN_PARAM_KEYS:
            if f"{{{key}}}" in tmpl:
                accepted.add(key)
                if key not in {"division", "min_fights"}:
                    required.add(key)
    # division and min_fights are accepted by all types but never required
    accepted |= {"division", "min_fights"}
    # Require placeholder params to be present (e.g. secondary_fired needs subtype)
    for key in required:
        if key not in params:
            raise HTTPException(
                status_code=400,
                detail=f"Param {key!r} is required for type {type_slug!r}",
            )
    for key in params:
        if key not in accepted:
            raise HTTPException(status_code=400, detail=f"Param {key!r} not accepted by type {type_slug!r}")
    if "division" in params and params["division"] not in _VALID_DIVISIONS:
        raise HTTPException(
            status_code=400,
            detail=f"division must be one of {sorted(_VALID_DIVISIONS)}, got {params['division']!r}",
        )
    if "subtype" in params:
        valid_subtypes = _SCORABLE_FIRE_SUBTYPES if type_slug == "secondary_fired" else _VALID_SUBTYPES
        if params["subtype"] not in valid_subtypes:
            raise HTTPException(
                status_code=400,
                detail=f"subtype must be one of {sorted(valid_subtypes)}, got {params['subtype']!r}",
            )
    if "module" in params and params["module"] not in _ACTIVATION_MODULES:
        raise HTTPException(
            status_code=400,
            detail=f"module must be one of {sorted(_ACTIVATION_MODULES)}, got {params['module']!r}",
        )
    if "weapon" in params:
        valid_weapons = _SCORABLE_KILL_WEAPONS if type_slug == "kills_by_weapon" else _VALID_WEAPON_VALS
        if params["weapon"] not in valid_weapons:
            raise HTTPException(
                status_code=400,
                detail=f"weapon must be one of {sorted(valid_weapons)}, got {params['weapon']!r}",
            )
    if "min_fights" in params:
        mf = params["min_fights"]
        if not isinstance(mf, int) or mf < 0:
            raise HTTPException(status_code=400, detail=f"min_fights must be a non-negative integer, got {mf!r}")


def _type_param_keys(type_slug: str) -> list[str]:
    et = EVENT_TYPES[type_slug]
    keys: list[str] = []
    for tmpl in et.metrics:
        for key in _KNOWN_PARAM_KEYS:
            if f"{{{key}}}" in tmpl and key not in keys:
                keys.append(key)
    if "division" not in keys:
        keys.append("division")
    if "min_fights" not in keys:
        keys.append("min_fights")
    return keys


def _type_param_values(type_slug: str) -> dict[str, list[str]]:
    """Return allowed values per param key for the given type slug."""
    out: dict[str, list[str]] = {}
    if type_slug == "secondary_fired":
        out["subtype"] = sorted(_SCORABLE_FIRE_SUBTYPES)
    elif type_slug == "kills_by_weapon":
        out["weapon"] = sorted(_SCORABLE_KILL_WEAPONS)
    elif type_slug == "module_activations":
        out["module"] = sorted(_ACTIVATION_MODULES)
    return out



# ---------------------------------------------------------------------------
# GET /events/types — slice 5 gateway prereq
# ---------------------------------------------------------------------------


@router.get("/types", response_model=list[EventTypeInfo])
async def list_event_types():
    """Return the full registry for the gateway selector and /events command."""
    return [
        EventTypeInfo(
            slug=et.slug,
            display_name=et.display_name,
            category=et.category,
            params=_type_param_keys(et.slug),
            param_values=_type_param_values(et.slug),
            rules_template=et.rules_text,
        )
        for et in EVENT_TYPES.values()
    ]


# ---------------------------------------------------------------------------
# POST /events — create draft event
# ---------------------------------------------------------------------------


@router.post("", response_model=EventResponse, status_code=201)
async def create_event(body: CreateEventRequest, user_id: int = Query(...)):
    if not await verify_admin_permissions(body.guild_id, user_id):
        raise HTTPException(status_code=403, detail="Admin permissions required")
    _validate_params(body.type_slug, body.params)

    now = datetime.now(UTC)
    async with get_db_session() as db:
        async with db.begin():
            event = GameEvent(
                guild_id=body.guild_id,
                type_slug=body.type_slug,
                params=body.params,
                duration_days=body.duration_days,
                state="draft",
                created_by_user_id=user_id,
                created_at=now,
                updated_at=now,
            )
            db.add(event)
            await db.flush()
            await AuditService.log_action(
                db,
                user_id=user_id,
                action="event_create",
                guild_id=body.guild_id,
                resource_type="event",
                resource_id=str(event.id),
                details={"type_slug": body.type_slug, "params": body.params, "duration_days": body.duration_days},
                commit=False,
            )
        await db.refresh(event)
    flogger.info(f"create_event: event_id={event.id} guild={body.guild_id} type={body.type_slug} by user={user_id}")
    await _push_events_cache(body.guild_id)
    return EventResponse.model_validate(event)


# ---------------------------------------------------------------------------
# DELETE /events/{event_id}
# ---------------------------------------------------------------------------


@router.delete("/{event_id}", status_code=204)
async def delete_event(event_id: int, guild_id: int = Query(...), user_id: int = Query(...)):
    if not await verify_admin_permissions(guild_id, user_id):
        raise HTTPException(status_code=403, detail="Admin permissions required")
    async with get_db_session() as db, db.begin():
        result = await db.execute(select(GameEvent).where(GameEvent.id == event_id))
        event = _load_event_or_404(result.scalar_one_or_none(), event_id, guild_id)
        _assert_state_or_409(event, "draft", "scheduled", "cancelled")
        await db.delete(event)
        await AuditService.log_action(
            db,
            user_id=user_id,
            action="event_delete",
            guild_id=event.guild_id,
            resource_type="event",
            resource_id=str(event_id),
            details={"state": event.state},
            commit=False,
        )
    flogger.info(f"delete_event: event_id={event_id} guild={event.guild_id} by user={user_id}")
    await _push_events_cache(guild_id)


# ---------------------------------------------------------------------------
# POST /events/{event_id}/prizes
# ---------------------------------------------------------------------------


@router.post("/{event_id}/prizes", response_model=PrizeResponse, status_code=201)
async def add_prize(event_id: int, body: AddPrizeRequest, guild_id: int = Query(...), user_id: int = Query(...)):
    if not await verify_admin_permissions(guild_id, user_id):
        raise HTTPException(status_code=403, detail="Admin permissions required")

    # Validate rank range consistency
    if (body.rank_from is None) != (body.rank_to is None):
        raise HTTPException(status_code=400, detail="rank_from and rank_to must both be set or both be null")
    if body.rank_from is not None and body.rank_to is not None and body.rank_from > body.rank_to:
        raise HTTPException(status_code=400, detail="rank_from must be <= rank_to")

    # credits => no item_ref
    if body.kind == "credits" and body.item_ref is not None:
        raise HTTPException(status_code=400, detail="item_ref must be null for credits prizes")
    if body.kind in ("item", "ship") and not body.item_ref:
        raise HTTPException(status_code=400, detail=f"item_ref is required for {body.kind} prizes")

    async with get_db_session() as db:
        async with db.begin():
            # Validate item/ship existence inside the transaction — avoids autobegin before begin()
            # which would cause InvalidRequestError: "A transaction is already begun on this Session".
            if body.kind == "item":
                inv_svc = InventoryService()
                item_details = await inv_svc.get_item_details(db, body.item_ref)  # type: ignore[arg-type]
                if not item_details:
                    raise HTTPException(status_code=400, detail=f"Item {body.item_ref!r} not in game catalog")
            elif body.kind == "ship":
                ship_repo = ShipRepository()
                game_ship = await ship_repo.get_by_name(db, body.item_ref)  # type: ignore[arg-type]
                if not game_ship:
                    raise HTTPException(status_code=400, detail=f"Ship {body.item_ref!r} not in game catalog")

            ev_result = await db.execute(select(GameEvent).where(GameEvent.id == event_id))
            event = _load_event_or_404(ev_result.scalar_one_or_none(), event_id, guild_id)
            _assert_state_or_409(event, "draft", "active")

            # Overlap / participation-duplicate check
            prize_q = await db.execute(select(GameEventPrize).where(GameEventPrize.event_id == event_id))
            existing = prize_q.scalars().all()
            if body.rank_from is None:
                if any(p.rank_from is None for p in existing):
                    raise HTTPException(status_code=400, detail="Participation prize already exists")
            else:
                new_from, new_to = body.rank_from, body.rank_to or body.rank_from
                for p in existing:
                    if p.rank_from is None:
                        continue
                    ex_from, ex_to = p.rank_from, p.rank_to if p.rank_to is not None else p.rank_from
                    if new_from <= ex_to and ex_from <= new_to:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Prize rank range {new_from}–{new_to} overlaps existing slot {ex_from}–{ex_to}",
                        )

            prize = GameEventPrize(
                event_id=event_id,
                rank_from=body.rank_from,
                rank_to=body.rank_to,
                kind=body.kind,
                item_ref=body.item_ref,
                qty=body.qty,
            )
            db.add(prize)
            await db.flush()
            await AuditService.log_action(
                db,
                user_id=user_id,
                action="event_add_prize",
                guild_id=event.guild_id,
                resource_type="event",
                resource_id=str(event_id),
                details={"prize_id": prize.id, "kind": body.kind, "rank_from": body.rank_from, "rank_to": body.rank_to},
                commit=False,
            )
        await db.refresh(prize)
    flogger.info(f"add_prize: event_id={event_id} prize_id={prize.id} kind={body.kind} by user={user_id}")
    await _push_events_cache(guild_id)
    return PrizeResponse.model_validate(prize)


# ---------------------------------------------------------------------------
# DELETE /events/{event_id}/prizes/{prize_id}
# ---------------------------------------------------------------------------


@router.delete("/{event_id}/prizes/{prize_id}", status_code=204)
async def delete_prize(event_id: int, prize_id: int, guild_id: int = Query(...), user_id: int = Query(...)):
    if not await verify_admin_permissions(guild_id, user_id):
        raise HTTPException(status_code=403, detail="Admin permissions required")
    async with get_db_session() as db, db.begin():
        ev_result = await db.execute(select(GameEvent).where(GameEvent.id == event_id))
        event = _load_event_or_404(ev_result.scalar_one_or_none(), event_id, guild_id)
        _assert_state_or_409(event, "draft")

        pr_result = await db.execute(
            select(GameEventPrize).where(GameEventPrize.id == prize_id, GameEventPrize.event_id == event_id)
        )
        prize = pr_result.scalar_one_or_none()
        if prize is None:
            raise HTTPException(status_code=404, detail=f"Prize {prize_id} not found on event {event_id}")
        await db.delete(prize)
        await AuditService.log_action(
            db,
            user_id=user_id,
            action="event_delete_prize",
            guild_id=event.guild_id,
            resource_type="event",
            resource_id=str(event_id),
            details={"prize_id": prize_id},
            commit=False,
        )
    flogger.info(f"delete_prize: event_id={event_id} prize_id={prize_id} by user={user_id}")
    await _push_events_cache(guild_id)


# ---------------------------------------------------------------------------
# POST /events/{event_id}/start
# ---------------------------------------------------------------------------


@router.post("/{event_id}/start")
async def start_event(event_id: int, body: StartEventRequest, guild_id: int = Query(...), user_id: int = Query(...)):
    if not await verify_admin_permissions(guild_id, user_id):
        raise HTTPException(status_code=403, detail="Admin permissions required")

    now = datetime.now(UTC)
    announcement = None
    scheduled_result: dict | None = None

    async with get_db_session() as db, db.begin():
        ev_result = await db.execute(select(GameEvent).where(GameEvent.id == event_id))
        event = _load_event_or_404(ev_result.scalar_one_or_none(), event_id, guild_id)
        _assert_state_or_409(event, "draft", "scheduled")

        if body.scheduled_start_at is not None:
            t = body.scheduled_start_at
            if t.tzinfo is None:
                t = t.replace(tzinfo=UTC)
            if t <= now:
                raise HTTPException(status_code=400, detail="scheduled_start_at must be in the future")
            if t > now + timedelta(days=90):
                raise HTTPException(status_code=400, detail="scheduled_start_at must be within 90 days")
            event.state = "scheduled"
            event.scheduled_start_at = t
            event.updated_at = now
            await db.flush()
            await AuditService.log_action(
                db,
                user_id=user_id,
                action="event_schedule",
                guild_id=event.guild_id,
                resource_type="event",
                resource_id=str(event_id),
                details={"scheduled_start_at": t.isoformat()},
                commit=False,
            )
            flogger.info(f"start_event: event_id={event_id} scheduled at {t.isoformat()} by user={user_id}")
            scheduled_result = {"status": "scheduled", "scheduled_start_at": t.isoformat()}
        else:
            # Immediate start — state already gated above to draft|scheduled
            try:
                announcement = await event_service.start_event(db, event)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            await AuditService.log_action(
                db,
                user_id=user_id,
                action="event_start",
                guild_id=event.guild_id,
                resource_type="event",
                resource_id=str(event_id),
                details={},
                commit=False,
            )

    # Both branches: push AFTER commit (announce-after-commit contract)
    await _push_events_cache(guild_id)
    if scheduled_result is not None:
        return scheduled_result
    if announcement:
        await event_service.announce(*announcement)
    flogger.info(f"start_event: event_id={event_id} started by user={user_id}")
    return {"status": "active", "event_id": event_id}


# ---------------------------------------------------------------------------
# POST /events/{event_id}/end
# ---------------------------------------------------------------------------


@router.post("/{event_id}/end")
async def end_event(event_id: int, body: EndEventRequest, guild_id: int = Query(...), user_id: int = Query(...)):
    if not await verify_admin_permissions(guild_id, user_id):
        raise HTTPException(status_code=403, detail="Admin permissions required")

    summary: dict = {}
    announcement = None

    async with get_db_session() as db, db.begin():
        ev_result = await db.execute(select(GameEvent).where(GameEvent.id == event_id))
        event = _load_event_or_404(ev_result.scalar_one_or_none(), event_id, guild_id)
        _assert_state_or_409(event, "active")

        summary = await event_service.end_event(
            db, event, payout=body.payout, reason=body.reason, actor_user_id=user_id
        )
        announcement = summary.pop("announcement", None)

        await AuditService.log_action(
            db,
            user_id=user_id,
            action="event_end",
            guild_id=event.guild_id,
            resource_type="event",
            resource_id=str(event_id),
            details={"payout": body.payout, "reason": body.reason},
            commit=False,
        )

    # announce-after-commit contract
    if announcement:
        await event_service.announce(*announcement)
    flogger.info(f"end_event: event_id={event_id} payout={body.payout} by user={user_id}")
    await _push_events_cache(guild_id)
    return summary


# ---------------------------------------------------------------------------
# GET /events/guild/{guild_id}  (read — no gate)
# ---------------------------------------------------------------------------


@router.get("/guild/{guild_id}", response_model=list[EventListItem])
async def list_guild_events(guild_id: int, state: str | None = Query(default=None)):
    """List events for a guild, optionally filtered by state (comma-separated)."""
    states: list[str] | None = None
    if state:
        states = [s.strip() for s in state.split(",")]
        for s in states:
            if s not in _VALID_STATES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown state {s!r}; valid: {sorted(_VALID_STATES)}",
                )

    async with get_db_session() as db:
        q = select(GameEvent).where(GameEvent.guild_id == guild_id)
        if states:
            q = q.where(GameEvent.state.in_(states))
        q = q.order_by(GameEvent.id)
        result = await db.execute(q)
        events = result.scalars().all()

        # Prize counts in one query
        prize_count_q = (
            select(GameEventPrize.event_id, func.count().label("cnt"))
            .where(GameEventPrize.event_id.in_([e.id for e in events]))
            .group_by(GameEventPrize.event_id)
        )
        pc_result = await db.execute(prize_count_q)
        prize_counts = {row.event_id: row.cnt for row in pc_result}

    out: list[EventListItem] = []
    for ev in events:
        et = EVENT_TYPES.get(ev.type_slug)
        out.append(
            EventListItem(
                id=ev.id,
                guild_id=ev.guild_id,
                type_slug=ev.type_slug,
                type_display=et.display_name if et else ev.type_slug,
                state=ev.state,
                params=ev.params or {},
                duration_days=ev.duration_days,
                scheduled_start_at=ev.scheduled_start_at,
                started_at=ev.started_at,
                ends_at=ev.ends_at,
                prize_count=prize_counts.get(ev.id, 0),
            )
        )
    return out


# ---------------------------------------------------------------------------
# GET /events/{event_id}  (read — no gate)
# ---------------------------------------------------------------------------


@router.get("/{event_id}", response_model=EventDetailResponse)
async def get_event(event_id: int):
    async with get_db_session() as db:
        ev_result = await db.execute(select(GameEvent).where(GameEvent.id == event_id))
        event = _load_event_or_404(ev_result.scalar_one_or_none(), event_id)
        pr_result = await db.execute(select(GameEventPrize).where(GameEventPrize.event_id == event_id))
        prizes = pr_result.scalars().all()
        config = await _config_repo.get_by_guild_id(db, event.guild_id)

    et = EVENT_TYPES.get(event.type_slug)
    params = event.params or {}
    effective_min_fights = params.get("min_fights", et.default_min_fights if et else 1)
    min_duel_stakes: int = config.event_min_duel_stakes if config else 1000
    rendered = (
        render_rules(
            et,
            min_stakes=min_duel_stakes,
            min_fights=effective_min_fights,
            division=params.get("division"),
            params=params,
        )
        if et else ""
    )
    return EventDetailResponse(
        **EventResponse.model_validate(event).model_dump(),
        prizes=[PrizeResponse.model_validate(p) for p in prizes],
        rules_text=rendered,
        effective_min_fights=effective_min_fights,
    )


# ---------------------------------------------------------------------------
# GET /events/{event_id}/standings  (read — no gate)
# ---------------------------------------------------------------------------


@router.get("/{event_id}/standings", response_model=list[StandingEntry])
async def get_standings(event_id: int):
    async with get_db_session() as db:
        ev_result = await db.execute(select(GameEvent).where(GameEvent.id == event_id))
        event = _load_event_or_404(ev_result.scalar_one_or_none(), event_id)

        if event.state == "ended":
            rows = await event_service.final_standings(db, event)
        else:
            rows = await event_service.live_standings(db, event)

    return [StandingEntry(**row) for row in rows]


# ---------------------------------------------------------------------------
# GET /events/guild/{guild_id}/medals  (read — no gate)
# ---------------------------------------------------------------------------


@router.get("/guild/{guild_id}/medals", response_model=list[MedalEntry])
async def get_medals(guild_id: int, type_slug: str | None = Query(default=None)):
    async with get_db_session() as db:
        rows = await event_service.medals(db, guild_id, type_slug=type_slug)
    return [MedalEntry(**row) for row in rows]
