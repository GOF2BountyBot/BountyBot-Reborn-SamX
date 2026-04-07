"""
Bounty repository for the BountyBot system.

Handles database operations for Bounty entities including guild-scoped
queries, CRUD operations, and division-level filtering.
"""

from shared import bblogger
from sqlalchemy import and_, func, select, update
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

    async def add(self, db: AsyncSession, obj: Bounty) -> Bounty:
        """Add a new bounty to the database."""
        try:
            db.add(obj)
            await db.commit()
            await db.refresh(obj)
            flogger.info(f"Added new bounty: {obj.id} in guild {obj.guild_id}")
            return obj
        except Exception as e:
            flogger.error(f"Error adding bounty: {e}")
            await db.rollback()
            raise

    async def create_or_update(self, db: AsyncSession, raw: dict) -> Bounty:
        """Create or update a bounty from raw data."""
        raise NotImplementedError("Use create() and update() methods directly")

    async def remove(self, db: AsyncSession, obj: Bounty) -> None:
        """Remove a bounty from the database."""
        try:
            db.delete(obj)
            await db.commit()
            flogger.info(f"Removed bounty: {obj.id}")
        except Exception as e:
            flogger.error(f"Error removing bounty {obj.id}: {e}")
            await db.rollback()
            raise

    # ------------------------------------------------------------------ #
    # Domain-specific methods                                              #
    # ------------------------------------------------------------------ #

    async def get_active_by_guild(self, db: AsyncSession, guild_id: int) -> list[Bounty]:
        """Get all active bounties for a given guild."""
        try:
            result = await db.execute(
                select(Bounty).where(
                    and_(
                        Bounty.guild_id == guild_id,
                        Bounty.status == "active",
                    )
                )
            )
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error getting active bounties for guild {guild_id}: {e}")
            raise

    async def get_active_by_guild_and_division(self, db: AsyncSession, guild_id: int, division: str) -> list[Bounty]:
        """Get all active bounties for a given guild and division."""
        try:
            result = await db.execute(
                select(Bounty).where(
                    and_(
                        Bounty.guild_id == guild_id,
                        Bounty.division == division,
                        Bounty.status == "active",
                    )
                )
            )
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error getting active bounties for guild {guild_id} division {division}: {e}")
            raise

    async def create(self, db: AsyncSession, bounty: Bounty) -> Bounty:
        """Create a new bounty — alias for add()."""
        return await self.add(db, bounty)

    async def update(self, db: AsyncSession, bounty: Bounty) -> Bounty:
        """Persist changes to an existing bounty."""
        try:
            await db.commit()
            await db.refresh(bounty)
            flogger.info(f"Updated bounty: {bounty.id}")
            return bounty
        except Exception as e:
            flogger.error(f"Error updating bounty {bounty.id}: {e}")
            await db.rollback()
            raise

    async def delete(self, db: AsyncSession, bounty: Bounty) -> None:
        """Delete a bounty — alias for remove()."""
        await self.remove(db, bounty)

    async def count(self, db: AsyncSession) -> int:
        """Return total number of bounties."""
        try:
            result = await db.execute(select(func.count()).select_from(Bounty))  # pylint: disable=not-callable
            return result.scalar_one()
        except Exception as e:
            flogger.error(f"Error counting bounties: {e}")
            raise

    async def clear_active_by_guild(self, db: AsyncSession, guild_id: int, tier: str | None = None) -> list[int]:
        """Set all matching active bounties to status='cleared'.

        Args:
            db: Async database session.
            guild_id: Discord guild ID.
            tier: Optional division filter (e.g. 'bronze', 'silver', 'gold').
                  If None, clears all active bounties for the guild.

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

            # Bulk update to 'cleared'
            await db.execute(update(Bounty).where(Bounty.id.in_(bounty_ids)).values(status="cleared"))
            await db.commit()
            flogger.info(f"Cleared {len(bounty_ids)} bounties for guild {guild_id} tier={tier}")
            return bounty_ids

        except Exception as e:
            flogger.error(f"Error clearing active bounties for guild {guild_id} tier={tier}: {e}")
            await db.rollback()
            raise

    async def count_active_by_guild_and_division(self, db: AsyncSession, guild_id: int, division: str) -> int:
        """Return count of active bounties for a guild and division."""
        try:
            result = await db.execute(
                select(func.count())  # pylint: disable=not-callable
                .select_from(Bounty)
                .where(
                    and_(
                        Bounty.guild_id == guild_id,
                        Bounty.division == division,
                        Bounty.status == "active",
                    )
                )
            )
            return result.scalar_one()
        except Exception as e:
            flogger.error(f"Error counting active bounties for guild {guild_id} division {division}: {e}")
            raise
