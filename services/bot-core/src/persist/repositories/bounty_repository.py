"""
Bounty repository for the BountyBot system.

Handles database operations for Bounty entities including guild-scoped
queries, CRUD operations, and division-level filtering.
"""

from datetime import datetime

from shared import bblogger
from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from persist.interfaces.repository_interface import IRepository
from persist.models.bounty import Bounty

flogger = bblogger.get_logger("bounty-repository")


class BountyRepository(IRepository[Bounty]):
    # ------------------------------------------------------------------ #
    # IRepository abstract method implementations                          #
    # ------------------------------------------------------------------ #

    async def get_by_id(self, db: AsyncSession, obj_id: int) -> Bounty | None:
        """Get bounty by primary key."""
        try:
            return await db.get(Bounty, obj_id)
        except Exception as e:
            flogger.error(f"Error getting bounty by ID {obj_id}: {e}")
            raise

    async def get_by_name(self, db: AsyncSession, name: str) -> Bounty | None:
        """Not applicable for bounties."""
        raise NotImplementedError("Bounties are not queried by name")

    async def list_all(self, db: AsyncSession) -> list[Bounty]:
        """Get all bounties."""
        try:
            result = await db.execute(select(Bounty))
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error listing all bounties: {e}")
            raise

    async def add(self, db: AsyncSession, obj: Bounty, *, commit: bool = True) -> Bounty:
        """Add a new bounty to the database.

        Args:
            commit: When False, flush without committing (caller owns transaction).
        """
        try:
            db.add(obj)
            if commit:
                await db.commit()
            else:
                await db.flush()
            await db.refresh(obj)
            flogger.info(f"Added new bounty: {obj.id} in guild {obj.guild_id}")
            return obj
        except Exception as e:
            flogger.error(f"Error adding bounty: {e}")
            if commit:
                await db.rollback()
            raise

    async def create_or_update(self, db: AsyncSession, raw: dict, *, commit: bool = True) -> Bounty:
        """Create or update a bounty from raw data."""
        raise NotImplementedError("Use create() and update() methods directly")

    async def remove(self, db: AsyncSession, obj: Bounty, *, commit: bool = True) -> None:
        """Remove a bounty from the database.

        Args:
            commit: When False, flush without committing (caller owns transaction).
        """
        try:
            await db.delete(obj)
            if commit:
                await db.commit()
            else:
                await db.flush()
            flogger.info(f"Removed bounty: {obj.id}")
        except Exception as e:
            flogger.error(f"Error removing bounty {obj.id}: {e}")
            if commit:
                await db.rollback()
            raise

    # ------------------------------------------------------------------ #
    # Domain-specific methods                                              #
    # ------------------------------------------------------------------ #

    async def get_active_by_guild(self, db: AsyncSession, guild_id: int) -> list[Bounty]:
        """Get all currently-active bounties for a given guild.

        Filters on BOTH status='active' AND end_time > NOW() (defensive dual-layer
        guard — B.14 fix).  The end_time filter ensures that bounties whose
        expire-job was lost (e.g. on app restart) are never surfaced as active,
        even if their status was not flipped to 'expired' by the executor.

        Methods that intentionally omit the time filter:
          - list_all()         — admin/history read; returns every bounty regardless of status
          - clear_active_by_guild() — bulk admin clear; acts on status only, time irrelevant
          - count_active_by_guild_and_division() — slot-counting for spawn logic; also time-filtered (see below)
        """
        try:
            # B.14: exclude stale bounties past end_time
            result = await db.execute(
                select(Bounty).where(
                    and_(
                        Bounty.guild_id == guild_id,
                        Bounty.status == "active",
                        Bounty.end_time > func.now(),  # pylint: disable=not-callable
                    )
                )
            )
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error getting active bounties for guild {guild_id}: {e}")
            raise

    async def get_active_by_guild_and_division(self, db: AsyncSession, guild_id: int, division: str) -> list[Bounty]:
        """Get all currently-active bounties for a given guild and division.

        Filters on BOTH status='active' AND end_time > NOW() (defensive dual-layer
        guard — B.14 fix).  Mirrors get_active_by_guild(); see its docstring for
        rationale and the list of intentionally un-filtered methods.
        """
        try:
            # B.14: exclude stale bounties past end_time
            result = await db.execute(
                select(Bounty).where(
                    and_(
                        Bounty.guild_id == guild_id,
                        Bounty.division == division,
                        Bounty.status == "active",
                        Bounty.end_time > func.now(),  # pylint: disable=not-callable
                    )
                )
            )
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error getting active bounties for guild {guild_id} division {division}: {e}")
            raise

    async def create(self, db: AsyncSession, bounty: Bounty, *, commit: bool = True) -> Bounty:
        """Create a new bounty — alias for add()."""
        return await self.add(db, bounty, commit=commit)

    async def update(self, db: AsyncSession, bounty: Bounty, *, commit: bool = True) -> Bounty:
        """Persist changes to an existing bounty.

        Args:
            commit: When False, flush without committing (caller owns transaction).
        """
        try:
            if commit:
                await db.commit()
            else:
                await db.flush()
            await db.refresh(bounty)
            flogger.info(f"Updated bounty: {bounty.id}")
            return bounty
        except Exception as e:
            flogger.error(f"Error updating bounty {bounty.id}: {e}")
            if commit:
                await db.rollback()
            raise

    async def delete(self, db: AsyncSession, bounty: Bounty, *, commit: bool = True) -> None:
        """Delete a bounty — alias for remove()."""
        await self.remove(db, bounty, commit=commit)

    async def delete_terminal_older_than(
        self,
        db: AsyncSession,
        cutoff: datetime,
        *,
        terminal_statuses: tuple[str, ...] = ("completed", "expired", "cleared"),
        commit: bool = True,
    ) -> int:
        """Delete bounty rows in a terminal status whose ``updated_at`` is older than ``cutoff``.

        Per-player aggregate stats (``bounty_wins``, ``systems_checked``,
        ``lifetime_credits``) are kept on the ``players`` table, so historical
        bounty rows have no game-relevant value once they reach a terminal
        state. This method is the data-retention worker.

        DOCUMENTED EXCEPTION to the ORM-mutation rule (see
        ``persist/repositories/AGENTS.md``): bulk DELETE that returns only the
        row count, never returns Bounty model objects. Uses Core DELETE with
        ``synchronize_session="fetch"`` so any identity-mapped Bounty rows in
        the session are correctly expired.

        Filters on ``updated_at`` (NOT ``created_at``) so a freshly-transitioned
        bounty is not immediately purged in the same retention window.

        Args:
            db: Async database session.
            cutoff: Rows with ``updated_at < cutoff`` are eligible for deletion.
            terminal_statuses: Statuses considered terminal (default:
                completed, expired, cleared). 'escaped' is intentionally
                excluded — escaped bounties may still respawn.
            commit: When False, flush without committing (caller owns transaction).

        Returns:
            Count of deleted rows.
        """
        try:
            result = await db.execute(
                delete(Bounty)
                .where(
                    and_(
                        Bounty.status.in_(terminal_statuses),
                        Bounty.updated_at < cutoff,
                    )
                )
                .execution_options(synchronize_session="fetch")
            )
            if commit:
                await db.commit()
            else:
                await db.flush()
            count = result.rowcount or 0
            flogger.info(
                f"Deleted {count} terminal bounty row(s) older than {cutoff.isoformat()} "
                f"(statuses={list(terminal_statuses)})"
            )
            return count
        except Exception as e:
            flogger.error(f"Error deleting terminal bounties older than {cutoff.isoformat()}: {e}")
            if commit:
                await db.rollback()
            raise

    async def count(self, db: AsyncSession) -> int:
        """Return total number of bounties."""
        try:
            result = await db.execute(select(func.count()).select_from(Bounty))  # pylint: disable=not-callable
            return result.scalar_one()
        except Exception as e:
            flogger.error(f"Error counting bounties: {e}")
            raise

    async def clear_active_by_guild(
        self, db: AsyncSession, guild_id: int, tier: str | None = None, *, commit: bool = True
    ) -> list[int]:
        """Set all matching active bounties to status='cleared'.

        DOCUMENTED EXCEPTION to the ORM-mutation rule (see
        ``persist/repositories/AGENTS.md``): this is a legitimate bulk operation
        that updates N rows in one statement and returns only IDs (never returns
        Bounty model objects). To remain identity-map safe, the Core UPDATE uses
        ``synchronize_session="fetch"`` so any identity-mapped Bounty rows in the
        session are correctly expired/refreshed.

        Args:
            db: Async database session.
            guild_id: Discord guild ID.
            tier: Optional division filter (e.g. 'bronze', 'silver', 'gold').
                  If None, clears all active bounties for the guild.
            commit: When False, flush without committing (caller owns transaction).

        Returns:
            List of cleared bounty IDs.
        """
        try:
            conditions = [
                Bounty.guild_id == guild_id,
                Bounty.status == "active",
            ]
            if tier is not None:
                conditions.append(Bounty.division == tier.lower())

            # Fetch matching IDs first
            result = await db.execute(select(Bounty.id).where(and_(*conditions)))
            bounty_ids = list(result.scalars().all())

            if not bounty_ids:
                return []

            # Bulk update to 'cleared'.
            # synchronize_session="fetch" is required: it forces SQLAlchemy to
            # re-fetch matching rows so any identity-mapped Bounty instances in
            # this session are correctly expired/refreshed.
            await db.execute(
                update(Bounty)
                .where(Bounty.id.in_(bounty_ids))
                .values(status="cleared")
                .execution_options(synchronize_session="fetch")
            )
            if commit:
                await db.commit()
            else:
                await db.flush()
            flogger.info(f"Cleared {len(bounty_ids)} bounties for guild {guild_id} tier={tier}")
            return bounty_ids

        except Exception as e:
            flogger.error(f"Error clearing active bounties for guild {guild_id} tier={tier}: {e}")
            if commit:
                await db.rollback()
            raise

    async def count_active_by_guild_and_division(self, db: AsyncSession, guild_id: int, division: str) -> int:
        """Return count of currently-active bounties for a guild and division.

        Also filters on end_time > NOW() (B.14 fix) so that stale bounties whose
        expire-job was lost do not count against the spawn slot limit, preventing
        the spawn executor from being permanently blocked by un-expired rows.
        """
        try:
            # B.14: exclude stale bounties past end_time
            result = await db.execute(
                select(func.count())  # pylint: disable=not-callable
                .select_from(Bounty)
                .where(
                    and_(
                        Bounty.guild_id == guild_id,
                        Bounty.division == division,
                        Bounty.status == "active",
                        Bounty.end_time > func.now(),  # pylint: disable=not-callable
                    )
                )
            )
            return result.scalar_one()
        except Exception as e:
            flogger.error(f"Error counting active bounties for guild {guild_id} division {division}: {e}")
            raise
