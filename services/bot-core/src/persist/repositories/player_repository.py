"""
Player repository for the BountyBot inventory system.

Handles database operations for Player entities including guild-isolated
player management, progression tracking, and statistics.
"""


from shared import bblogger
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from persist.interfaces.repository_interface import IRepository
from persist.models.player import Player

flogger = bblogger.get_logger("player-repository")

class PlayerRepository(IRepository[Player]):

    async def get_by_id(self, db: AsyncSession, obj_id: int) -> Player | None:
        """Get player by ID."""
        try:
            return await db.get(Player, obj_id)
        except Exception as e:
            flogger.error(f"Error getting player by ID {obj_id}: {e}")
            raise

    async def get_by_id_for_update(self, db: AsyncSession, obj_id: int) -> Player | None:
        """Get player by ID with SELECT ... FOR UPDATE row-level lock.

        Use this inside a transaction when you need to read-then-modify
        credit balances (or any field) to prevent TOCTOU race conditions.
        The lock is held until the enclosing transaction commits or rolls back.
        """
        try:
            result = await db.execute(
                select(Player).where(Player.id == obj_id).with_for_update()
            )
            return result.scalars().first()
        except Exception as e:
            flogger.error(f"Error getting player (FOR UPDATE) by ID {obj_id}: {e}")
            raise

    async def get_by_name(self, db: AsyncSession, name: str) -> Player | None:
        """Not applicable for players - they don't have names."""
        raise NotImplementedError("Players don't have searchable names")

    async def count(self, db: AsyncSession) -> int:
        """Return total number of players."""
        try:
            result = await db.execute(select(func.count()).select_from(Player))  # pylint: disable=not-callable
            return result.scalar_one()
        except Exception as e:
            flogger.error(f"Error counting players: {e}")
            raise

    async def list_all(self, db: AsyncSession) -> list[Player]:
        """Get all players."""
        try:
            result = await db.execute(select(Player))
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error listing all players: {e}")
            raise

    async def add(self, db: AsyncSession, obj: Player) -> Player:
        """Add new player to database."""
        try:
            db.add(obj)
            await db.commit()
            await db.refresh(obj)
            flogger.info(f"Added new player: {obj.id} for user {obj.user_id} in guild {obj.guild_id}")
            return obj
        except Exception as e:
            flogger.error(f"Error adding player: {e}")
            await db.rollback()
            raise

    async def create_or_update(self, db: AsyncSession, raw: dict) -> Player:
        """Create or update player from raw data."""
        try:
            user_id = raw.get("user_id")
            guild_id = raw.get("guild_id")

            if not user_id or not guild_id:
                raise ValueError("Both user_id and guild_id are required")

            # Try to get existing player
            player = await self.get_by_user_and_guild(db, user_id, guild_id)

            if player:
                # Update existing player
                for key, value in raw.items():
                    if hasattr(player, key) and key not in ['id', 'created_at']:
                        setattr(player, key, value)
                await db.commit()
                await db.refresh(player)
                flogger.debug(f"Updated player: {player.id}")
            else:
                # Create new player
                player = Player(**raw)
                player = await self.add(db, player)
                flogger.info(f"Created new player for user {user_id} in guild {guild_id}")

            return player
        except Exception as e:
            flogger.error(f"Error creating/updating player: {e}")
            raise

    async def remove(self, db: AsyncSession, obj: Player) -> None:
        """Remove player from database."""
        try:
            await db.delete(obj)
            await db.commit()
            flogger.info(f"Removed player: {obj.id}")
        except Exception as e:
            flogger.error(f"Error removing player {obj.id}: {e}")
            await db.rollback()
            raise

    async def get_by_user_and_guild(self, db: AsyncSession, user_id: int, guild_id: int) -> Player | None:
        """Get player by user ID and guild ID combination."""
        try:
            result = await db.execute(
                select(Player).where(
                    and_(Player.user_id == user_id, Player.guild_id == guild_id)
                )
            )
            return result.scalars().first()
        except Exception as e:
            flogger.error(f"Error getting player for user {user_id} in guild {guild_id}: {e}")
            raise

    async def get_players_by_guild(self, db: AsyncSession, guild_id: int) -> list[Player]:
        """Get all players in a specific guild."""
        try:
            result = await db.execute(
                select(Player).where(Player.guild_id == guild_id)
            )
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error getting players for guild {guild_id}: {e}")
            raise

    async def get_players_by_user(self, db: AsyncSession, user_id: int) -> list[Player]:
        """Get all players for a specific user across all guilds."""
        try:
            result = await db.execute(
                select(Player).where(Player.user_id == user_id)
            )
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error getting players for user {user_id}: {e}")
            raise

    async def update_credits(
        self, db: AsyncSession, player_id: int, new_credits: int, *, commit: bool = True
    ) -> Player:
        """Update player credits.

        Args:
            db: Database session.
            player_id: Player whose credits to update.
            new_credits: The new absolute credit balance.
            commit: If True (default), commit immediately.  Pass ``False``
                when this call is part of a larger transaction managed by the
                caller (e.g. inside ``async with db.begin()``).
        """
        try:
            await db.execute(
                update(Player)
                .where(Player.id == player_id)
                .values(credits=new_credits)
            )
            if commit:
                await db.commit()
            else:
                await db.flush()

            player = await self.get_by_id(db, player_id)
            flogger.debug(f"Updated credits for player {player_id}: {new_credits}")
            return player
        except Exception as e:
            flogger.error(f"Error updating credits for player {player_id}: {e}")
            if commit:
                await db.rollback()
            raise

    async def update_xp(self, db: AsyncSession, player_id: int, xp: int) -> Player:
        """Update player XP."""
        try:
            await db.execute(
                update(Player)
                .where(Player.id == player_id)
                .values(xp=xp)
            )
            await db.commit()

            player = await self.get_by_id(db, player_id)
            flogger.debug(f"Updated XP for player {player_id}: {xp}")
            return player
        except Exception as e:
            flogger.error(f"Error updating XP for player {player_id}: {e}")
            await db.rollback()
            raise

    async def update_tier(self, db: AsyncSession, player_id: int, tier: str) -> Player:
        """Update player tier."""
        try:
            valid_tiers = ["Bronze", "Silver", "Gold", "Platinum"]
            if tier not in valid_tiers:
                raise ValueError(f"Invalid tier: {tier}. Must be one of {valid_tiers}")

            await db.execute(
                update(Player)
                .where(Player.id == player_id)
                .values(tier=tier)
            )
            await db.commit()

            player = await self.get_by_id(db, player_id)
            flogger.info(f"Updated tier for player {player_id}: {tier}")
            return player
        except Exception as e:
            flogger.error(f"Error updating tier for player {player_id}: {e}")
            await db.rollback()
            raise

    async def update_active_ship(self, db: AsyncSession, player_id: int, ship_id: int | None) -> Player:
        """Update player's active ship."""
        try:
            await db.execute(
                update(Player)
                .where(Player.id == player_id)
                .values(active_ship_id=ship_id)
            )
            await db.commit()

            player = await self.get_by_id(db, player_id)
            flogger.debug(f"Updated active ship for player {player_id}: {ship_id}")
            return player
        except Exception as e:
            flogger.error(f"Error updating active ship for player {player_id}: {e}")
            await db.rollback()
            raise
