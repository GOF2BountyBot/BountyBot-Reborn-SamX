"""
Duel repository for the BountyBot system.

Handles database operations for DuelRequest entities including guild-scoped
queries, CRUD operations, and status management.
"""

from datetime import datetime

from shared import bblogger
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from persist.interfaces.repository_interface import IRepository
from persist.models.duel_request import DuelRequest

flogger = bblogger.get_logger("duel-repository")


class DuelRepository(IRepository[DuelRequest]):
    # ------------------------------------------------------------------ #
    # IRepository abstract method implementations                          #
    # ------------------------------------------------------------------ #

    async def get_by_id(self, db: AsyncSession, obj_id: int) -> DuelRequest | None:
        """Get duel request by primary key."""
        try:
            return await db.get(DuelRequest, obj_id)
        except Exception as e:
            flogger.error(f"Error getting duel request by ID {obj_id}: {e}")
            raise

    async def get_by_name(self, db: AsyncSession, name: str) -> DuelRequest | None:
        """Not applicable for duel requests."""
        raise NotImplementedError("Duel requests are not queried by name")

    async def list_all(self, db: AsyncSession) -> list[DuelRequest]:
        """Get all duel requests."""
        try:
            result = await db.execute(select(DuelRequest))
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error listing all duel requests: {e}")
            raise

    async def add(self, db: AsyncSession, obj: DuelRequest) -> DuelRequest:
        """Add a new duel request to the database."""
        try:
            db.add(obj)
            await db.commit()
            await db.refresh(obj)
            flogger.info(f"Added new duel request: {obj.id} challenger={obj.challenger_id} target={obj.target_id}")
            return obj
        except Exception as e:
            flogger.error(f"Error adding duel request: {e}")
            await db.rollback()
            raise

    async def create_or_update(self, db: AsyncSession, raw: dict) -> DuelRequest:
        """Create or update a duel request from raw data."""
        raise NotImplementedError("Use create() and update_status() methods directly")

    async def remove(self, db: AsyncSession, obj: DuelRequest) -> None:
        """Remove a duel request from the database."""
        try:
            db.delete(obj)
            await db.commit()
            flogger.info(f"Removed duel request: {obj.id}")
        except Exception as e:
            flogger.error(f"Error removing duel request {obj.id}: {e}")
            await db.rollback()
            raise

    # ------------------------------------------------------------------ #
    # Domain-specific methods                                              #
    # ------------------------------------------------------------------ #

    async def create(self, db: AsyncSession, duel: DuelRequest) -> DuelRequest:
        """Create a new duel request — alias for add()."""
        return await self.add(db, duel)

    async def get_pending_by_players(
        self,
        db: AsyncSession,
        challenger_id: int,
        target_id: int,
        guild_id: int,
    ) -> DuelRequest | None:
        """Find a pending duel request between two specific players in a guild."""
        try:
            result = await db.execute(
                select(DuelRequest).where(
                    and_(
                        DuelRequest.challenger_id == challenger_id,
                        DuelRequest.target_id == target_id,
                        DuelRequest.guild_id == guild_id,
                        DuelRequest.status == "pending",
                    )
                )
            )
            return result.scalars().first()
        except Exception as e:
            flogger.error(
                f"Error getting pending duel for challenger={challenger_id} target={target_id} guild={guild_id}: {e}"
            )
            raise

    async def update_status(self, db: AsyncSession, duel_id: int, new_status: str) -> DuelRequest | None:
        """Update the status of a duel request."""
        try:
            duel = await db.get(DuelRequest, duel_id)
            if duel is None:
                return None
            duel.status = new_status
            try:
                await db.commit()
                await db.refresh(duel)
            except Exception:
                await db.rollback()
                raise
            flogger.info(f"Updated duel request {duel_id} status to {new_status!r}")
            return duel
        except Exception as e:
            flogger.error(f"Error updating duel request {duel_id} status: {e}")
            raise

    async def delete_expired(self, db: AsyncSession, current_time: datetime) -> int:
        """Delete all expired duel requests. Returns count of deleted rows."""
        try:
            result = await db.execute(
                delete(DuelRequest).where(
                    and_(
                        DuelRequest.expires_at.isnot(None),
                        DuelRequest.expires_at <= current_time,
                        DuelRequest.status == "pending",
                    )
                )
            )
            await db.commit()
            count = result.rowcount
            flogger.info(f"Deleted {count} expired duel requests")
            return count
        except Exception as e:
            flogger.error(f"Error deleting expired duel requests: {e}")
            await db.rollback()
            raise

    async def get_active_by_guild(self, db: AsyncSession, guild_id: int) -> list[DuelRequest]:
        """Get all currently-pending duel requests for a given guild.

        Filters on BOTH status='pending' AND (expires_at IS NULL OR expires_at > NOW())
        (B.14 sibling fix — same defensive dual-layer pattern as BountyRepository).
        Duels without an expires_at are treated as non-expiring and always included.

        Methods intentionally left without the time filter:
          - delete_expired()  — explicitly operates on expired rows; time filter is its purpose
          - get_pending_by_players() — point-lookup before accepting; include even at-expiry-edge
        """
        try:
            result = await db.execute(
                select(DuelRequest).where(
                    and_(
                        DuelRequest.guild_id == guild_id,
                        DuelRequest.status == "pending",
                        # B.14 sibling: exclude duels that have passed their expiry
                        (DuelRequest.expires_at.is_(None) | (DuelRequest.expires_at > func.now())),
                    )
                )
            )
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error getting active duel requests for guild {guild_id}: {e}")
            raise

    async def get_pending_by_target(self, db: AsyncSession, target_id: int, guild_id: int) -> list[DuelRequest]:
        """Get all currently-pending duel requests where the given player is the target.

        Used for autocomplete: shows challenges the user needs to accept/reject.
        Applies the same expires_at > NOW() guard (B.14 sibling fix) so that
        stale un-expired duels do not pollute autocomplete results.
        """
        try:
            result = await db.execute(
                select(DuelRequest).where(
                    and_(
                        DuelRequest.target_id == target_id,
                        DuelRequest.guild_id == guild_id,
                        DuelRequest.status == "pending",
                        # B.14 sibling: exclude duels that have passed their expiry
                        (DuelRequest.expires_at.is_(None) | (DuelRequest.expires_at > func.now())),
                    )
                )
            )
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error getting pending duels for target={target_id} guild={guild_id}: {e}")
            raise
