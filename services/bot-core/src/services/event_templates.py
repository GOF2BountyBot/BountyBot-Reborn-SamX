"""Out-of-the-box (OOB) event templates.

Definitions live in ``event_templates.json`` next to this module. They are seeded into a guild when its
config is first created (``/admin_setup``) and re-synced for every configured guild at bot startup, which
is the upgrade path for definition changes. A guild's copy is only overwritten while it is *unmodified*:
``created_by_user_id IS NULL`` (seeded, not admin-created) and ``created_at == updated_at`` (every admin
edit — settings or prizes — bumps ``updated_at``). Modified copies and admin templates with the same name
are left alone.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from persist.database.manager import get_db_session
from persist.models.game_event import GameEvent, GameEventPrize
from persist.models.guild_config import GuildConfig
from persist.repositories.ship_repository import ShipRepository
from shared import bblogger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.inventory_service import InventoryService

flogger = bblogger.get_logger("event-templates")

_DEFINITIONS = Path(__file__).with_name("event_templates.json")


def load_oob_templates(path: Path = _DEFINITIONS) -> list[dict[str, Any]]:
    """Read and validate the OOB definitions. Raises ValueError on a malformed file (a bug, not runtime data)."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    defs = doc["templates"]
    # ponytail: the router owns param validation; import lazily to avoid a services -> api import at module load
    from api.routers.events import _validate_params  # pylint: disable=import-outside-toplevel

    seen: set[str] = set()
    for d in defs:
        name = d.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64 or name in seen:
            raise ValueError(f"OOB template has a missing/duplicate/over-long name: {name!r}")
        seen.add(name)
        if not 1 <= int(d.get("duration_days", 7)) <= 60:
            raise ValueError(f"OOB template {name!r}: duration_days out of range")
        _validate_params(d["type_slug"], d.get("params", {}))  # HTTPException on a bad slug/param
        for p in d.get("prizes", []):
            if p.get("kind") not in ("credits", "item", "ship") or int(p.get("qty", 0)) < 1:
                raise ValueError(f"OOB template {name!r}: bad prize {p!r}")
            if p["kind"] != "credits" and not p.get("item_ref"):
                raise ValueError(f"OOB template {name!r}: {p['kind']} prize needs item_ref")
    return defs


async def _prize_ref_exists(db: AsyncSession, kind: str, item_ref: str | None) -> bool:
    if kind == "item":
        return bool(await InventoryService().get_item_details(db, item_ref))  # type: ignore[arg-type]
    if kind == "ship":
        return bool(await ShipRepository().get_by_name(db, item_ref))  # type: ignore[arg-type]
    return True


async def _write_prizes(db: AsyncSession, event_id: int, name: str, prizes: list[dict[str, Any]]) -> None:
    for p in prizes:
        if not await _prize_ref_exists(db, p["kind"], p.get("item_ref")):
            flogger.warning(f"OOB template {name!r}: {p['kind']} {p.get('item_ref')!r} not in catalog — prize skipped")
            continue
        db.add(
            GameEventPrize(
                event_id=event_id,
                rank_from=p.get("rank_from"),
                rank_to=p.get("rank_to"),
                kind=p["kind"],
                item_ref=p.get("item_ref"),
                qty=int(p["qty"]),
            )
        )


async def sync_guild_templates(
    db: AsyncSession, guild_id: int, defs: list[dict[str, Any]] | None = None, *, commit: bool = True
) -> dict[str, int]:
    """Seed missing OOB templates for one guild and refresh unmodified ones. Returns created/updated/skipped counts."""
    defs = load_oob_templates() if defs is None else defs
    now = datetime.now(UTC)
    counts = {"created": 0, "updated": 0, "skipped": 0}
    for d in defs:
        row = (
            await db.execute(
                select(GameEvent).where(
                    GameEvent.guild_id == guild_id, GameEvent.state == "template", GameEvent.name == d["name"]
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = GameEvent(
                guild_id=guild_id,
                type_slug=d["type_slug"],
                params=dict(d.get("params", {})),
                duration_days=int(d.get("duration_days", 7)),
                state="template",
                name=d["name"],
                created_by_user_id=None,  # NULL marks a seeded row
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            await db.flush()
            await _write_prizes(db, row.id, d["name"], d.get("prizes", []))
            counts["created"] += 1
        elif row.created_by_user_id is None and row.created_at == row.updated_at:
            row.type_slug = d["type_slug"]
            row.params = dict(d.get("params", {}))
            row.duration_days = int(d.get("duration_days", 7))
            # updated_at deliberately stays equal to created_at: still an unmodified OOB copy
            await db.execute(delete(GameEventPrize).where(GameEventPrize.event_id == row.id))
            await _write_prizes(db, row.id, d["name"], d.get("prizes", []))
            counts["updated"] += 1
        else:
            counts["skipped"] += 1
    if commit:
        await db.commit()
    else:
        await db.flush()
    flogger.info(f"sync_guild_templates: guild={guild_id} {counts}")
    return counts


async def sync_all_guild_templates() -> dict[str, int]:
    """Startup fallback/upgrade: sync every configured guild, one session each; a failing guild never stops the rest."""
    defs = load_oob_templates()
    async with get_db_session() as db:
        guild_ids = list((await db.execute(select(GuildConfig.guild_id))).scalars().all())
    totals = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
    for gid in guild_ids:
        try:
            async with get_db_session() as db:
                counts = await sync_guild_templates(db, gid, defs)
            for k in ("created", "updated", "skipped"):
                totals[k] += counts[k]
        except Exception as exc:  # pylint: disable=broad-exception-caught
            totals["failed"] += 1
            flogger.error(f"sync_all_guild_templates: guild={gid} failed: {exc}", exc_info=True)
    flogger.info(f"sync_all_guild_templates: guilds={len(guild_ids)} {totals}")
    return totals
