"""
User repository for the BountyBot inventory system.

Handles database operations for User entities including creation,
retrieval, and user management operations.
"""

from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
import shared.bblogger as bblogger
from persist.interfaces.repository_interface import IRepository
from persist.models.user import User

flogger = bblogger.get_logger("user-repository")

class UserRepository(IRepository[User]):
    
    async def get_by_id(self, db: AsyncSession, user_id: int) -> Optional[User]:
        """Get user by Discord ID."""
        try:
            return await db.get(User, user_id)
        except Exception as e:
            flogger.error(f"Error getting user by ID {user_id}: {e}")
            raise

    async def get_by_name(self, db: AsyncSession, name: str) -> Optional[User]:
        """Get user by Discord username."""
        try:
            result = await db.execute(
                select(User).where(User.discord_username == name)
            )
            return result.scalars().first()
        except Exception as e:
            flogger.error(f"Error getting user by name {name}: {e}")
            raise

    async def list_all(self, db: AsyncSession) -> List[User]:
        """Get all users."""
        try:
            result = await db.execute(select(User))
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error listing all users: {e}")
            raise

    async def add(self, db: AsyncSession, user: User) -> User:
        """Add new user to database."""
        try:
            db.add(user)
            await db.commit()
            await db.refresh(user)
            flogger.info(f"Added new user: {user.id}")
            return user
        except Exception as e:
            flogger.error(f"Error adding user {user.id}: {e}")
            await db.rollback()
            raise

    async def create_or_update(self, db: AsyncSession, raw: dict) -> User:
        """Create or update user from raw data."""
        try:
            user_id = raw.get("id")
            if not user_id:
                raise ValueError("User ID is required")
                
            # Try to get existing user
            user = await self.get_by_id(db, user_id)
            
            if user:
                # Update existing user
                if "discord_username" in raw:
                    user.discord_username = raw["discord_username"]
                await db.commit()
                await db.refresh(user)
                flogger.debug(f"Updated user: {user_id}")
            else:
                # Create new user
                user = User(
                    id=user_id,
                    discord_username=raw.get("discord_username")
                )
                user = await self.add(db, user)
                flogger.info(f"Created new user: {user_id}")
                
            return user
        except Exception as e:
            flogger.error(f"Error creating/updating user: {e}")
            raise

    async def remove(self, db: AsyncSession, user: User) -> None:
        """Remove user from database."""
        try:
            await db.delete(user)
            await db.commit()
            flogger.info(f"Removed user: {user.id}")
        except Exception as e:
            flogger.error(f"Error removing user {user.id}: {e}")
            await db.rollback()
            raise

    async def get_or_create_user(self, db: AsyncSession, discord_id: int, username: str = None) -> User:
        """Get existing user or create new one."""
        try:
            user = await self.get_by_id(db, discord_id)
            if not user:
                user = User(id=discord_id, discord_username=username)
                user = await self.add(db, user)
                flogger.info(f"Auto-created user for Discord ID: {discord_id}")
            elif username and user.discord_username != username:
                # Update username if provided and different
                user.discord_username = username
                await db.commit()
                await db.refresh(user)
                flogger.debug(f"Updated username for user {discord_id}")
                
            return user
        except Exception as e:
            flogger.error(f"Error getting/creating user {discord_id}: {e}")
            raise