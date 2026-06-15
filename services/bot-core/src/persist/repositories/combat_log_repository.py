"""CombatLogRepository — CRUD access for combat_log rows.

Rows are immutable post-insert (no update path). The NPC invariant
(at least one combatant_user_id is non-NULL) is enforced in add().

T10 will call delete_older_than() from db_retention_executor.
"""

from datetime import datetime
from typing import Any

from shared import bblogger
from sqlalchemy import Row, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from persist.interfaces.repository_interface import IRepository
from persist.models.combat_log import CombatLog

flogger = bblogger.get_logger("combat-log-repository")


class CombatLogRepository(IRepository[CombatLog]):
    # ------------------------------------------------------------------ #
    # IRepository abstract method implementations                          #
    # ------------------------------------------------------------------ #

    async def add(self, db: AsyncSession, obj: CombatLog, *, commit: bool = True) -> CombatLog:
        """Persist a new CombatLog row.

        Enforces the NPC invariant: at least one of combatant1_user_id /
        combatant2_user_id must be non-NULL (NPC-vs-NPC never occurs — §12).
        Raises ValueError before touching the DB if both are NULL.
        """
        if obj.combatant1_user_id is None and obj.combatant2_user_id is None:
            raise ValueError(
                "NPC invariant violated: both combatant1_user_id and combatant2_user_id are NULL. "
                "At least one combatant must be a real player (§12)."
            )
        try:
            db.add(obj)
            if commit:
                await db.commit()
            else:
                await db.flush()
            await db.refresh(obj)
            flogger.info(
                f"Added combat_log id={obj.id} context={obj.context!r} "
                f"c1_user={obj.combatant1_user_id} c2_user={obj.combatant2_user_id}"
            )
            return obj
        except Exception as e:
            flogger.error(f"Error adding combat_log: {e}")
            if commit:
                await db.rollback()
            raise

    async def get_by_id(self, db: AsyncSession, obj_id: int) -> CombatLog | None:
        """Fetch a CombatLog row by primary key."""
        try:
            return await db.get(CombatLog, obj_id)
        except Exception as e:
            flogger.error(f"Error fetching combat_log id={obj_id}: {e}")
            raise

    async def get_subpath_for_detail(self, db: AsyncSession, obj_id: int) -> Row[Any] | None:
        """Fetch only the sub-paths needed for get_detail, skipping the multi-MB timeline.

        P4-T7b: JSONB sub-path select — selects data->'summary', data->'metadata',
        data->'key_events' server-side (via SQLAlchemy column index accessors on the
        _JSONB column) so the timeline sub-key is never shipped or loaded into Python.

        On PostgreSQL (prod/JSONB) the emitted SQL is:
            SELECT ..., data -> 'summary', data -> 'metadata', data -> 'key_events' ...
        On SQLite (unit-test suite/JSON) the emitted SQL is:
            SELECT ..., JSON_QUOTE(JSON_EXTRACT(data, '$."summary"')), ...

        Both dialects deserialize the JSON sub-values automatically: the returned
        Row fields ``summary``, ``metadata``, and ``key_events`` are already Python
        dicts/lists (not raw JSON strings).

        Returns:
            A SQLAlchemy Row namedtuple with fields:
              id, guild_id, context, combatant1_name, combatant2_name,
              combatant1_user_id, combatant2_user_id, winner_name, is_stalemate,
              created_at, summary, metadata, key_events
            or None if no row with that id exists.

        Note: key_events is None when the stored data blob has no "key_events" key
        (legacy row written before P4-T7a). Callers must handle this case by falling
        back to get_by_id() + _extract_key_events (see CombatLogService.get_detail).
        """
        try:
            stmt = select(
                CombatLog.id,
                CombatLog.guild_id,
                CombatLog.context,
                CombatLog.combatant1_name,
                CombatLog.combatant2_name,
                CombatLog.combatant1_user_id,
                CombatLog.combatant2_user_id,
                CombatLog.winner_name,
                CombatLog.is_stalemate,
                CombatLog.created_at,
                CombatLog.data["summary"].label("summary"),
                CombatLog.data["metadata"].label("metadata"),
                CombatLog.data["key_events"].label("key_events"),
            ).where(CombatLog.id == obj_id)
            result = await db.execute(stmt)
            return result.one_or_none()
        except Exception as e:
            flogger.error(f"Error fetching combat_log subpath id={obj_id}: {e}")
            raise

    async def get_by_name(self, db: AsyncSession, name: str) -> CombatLog | None:
        """Not applicable — combat_log is not queried by name."""
        raise NotImplementedError("combat_log is not queried by name")

    async def list_all(self, db: AsyncSession) -> list[CombatLog]:
        """Return all combat_log rows (unordered)."""
        try:
            result = await db.execute(select(CombatLog))
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error listing all combat_log rows: {e}")
            raise

    async def create_or_update(self, db: AsyncSession, raw: dict, *, commit: bool = True) -> CombatLog:
        """Not applicable — combat_log rows are immutable post-insert."""
        raise NotImplementedError("combat_log rows are immutable post-insert; use add() instead")

    async def remove(self, db: AsyncSession, obj: CombatLog, *, commit: bool = True) -> None:
        """Delete a single CombatLog row."""
        try:
            await db.delete(obj)
            if commit:
                await db.commit()
            else:
                await db.flush()
            flogger.info(f"Removed combat_log id={obj.id}")
        except Exception as e:
            flogger.error(f"Error removing combat_log id={obj.id}: {e}")
            if commit:
                await db.rollback()
            raise

    # ------------------------------------------------------------------ #
    # Domain-specific methods                                              #
    # ------------------------------------------------------------------ #

    async def list_for_player(
        self,
        db: AsyncSession,
        user_id: int,
        limit: int = 20,
        guild_id: int | None = None,
    ) -> list[CombatLog]:
        """Return the most recent fights involving a player (§12 canonical query).

        Matches rows where the player appears as either combatant, ordered
        newest-first. NPC fights (NULL user_id on that side) are naturally
        excluded from the player's history.

        Args:
            db:       Async DB session.
            user_id:  Discord user ID to filter by (either combatant slot).
            limit:    Maximum rows to return (default 20).
            guild_id: When provided, restrict to this guild only (for autocomplete).
        """
        try:
            stmt = select(CombatLog).where(
                or_(
                    CombatLog.combatant1_user_id == user_id,
                    CombatLog.combatant2_user_id == user_id,
                )
            )
            if guild_id is not None:
                stmt = stmt.where(CombatLog.guild_id == guild_id)
            stmt = stmt.order_by(CombatLog.created_at.desc()).limit(limit)
            result = await db.execute(stmt)
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error listing combat_log for user_id={user_id} guild_id={guild_id}: {e}")
            raise

    async def delete_by_guild_id(self, db: AsyncSession, guild_id: int, *, commit: bool = True) -> int:
        """Hard-delete ALL combat_log rows for a guild (full uninstall cascade).

        Mirrors ``delete_older_than`` style (bulk Core DELETE,
        ``synchronize_session="fetch"``).  Called exclusively from
        ``ConfigService.uninstall_guild()`` — not from any routine cleanup path.

        Args:
            db:        Async database session.
            guild_id:  Discord guild ID whose combat_log rows will be destroyed.
            commit:    When False, flush without committing (caller owns transaction).

        Returns:
            Count of deleted rows.
        """
        try:
            result = await db.execute(
                delete(CombatLog).where(CombatLog.guild_id == guild_id).execution_options(synchronize_session="fetch")
            )
            if commit:
                await db.commit()
            else:
                await db.flush()
            count = result.rowcount or 0
            flogger.info(f"Hard-deleted {count} combat_log row(s) for guild {guild_id} (uninstall)")
            return count
        except Exception as e:
            flogger.error(f"Error hard-deleting combat_log rows for guild {guild_id}: {e}")
            if commit:
                await db.rollback()
            raise

    async def delete_older_than(self, db: AsyncSession, cutoff: datetime, *, commit: bool = True) -> int:
        """Bulk-delete combat_log rows whose created_at is older than cutoff.

        Returns the count of deleted rows. Called by T10's db_retention_executor
        extension; T2 only lands the method.

        Uses synchronize_session='fetch' (bulk DELETE exception — see
        persist/repositories/AGENTS.md).
        """
        try:
            result = await db.execute(
                delete(CombatLog).where(CombatLog.created_at < cutoff).execution_options(synchronize_session="fetch")
            )
            if commit:
                await db.commit()
            else:
                await db.flush()
            count = result.rowcount or 0
            flogger.info(f"Deleted {count} combat_log row(s) older than {cutoff.isoformat()}")
            return count
        except Exception as e:
            flogger.error(f"Error deleting combat_log rows older than {cutoff.isoformat()}: {e}")
            if commit:
                await db.rollback()
            raise
