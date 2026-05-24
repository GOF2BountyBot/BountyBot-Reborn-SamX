"""
User repository for the BountyBot inventory system.

Handles database operations for User entities including creation,
retrieval, and user management operations.
"""

from shared import bblogger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from persist.interfaces.repository_interface import IRepository
from persist.models.user import User

flogger = bblogger.get_logger("user-repository")


class UserRepository(IRepository[User]):
    async def get_by_id(self, db: AsyncSession, obj_id: int) -> User | None:
        """Get user by Discord ID."""
        try:
            return await db.get(User, obj_id)
        except Exception as e:
            flogger.error(f"Error getting user by ID {obj_id}: {e}")
            raise

    async def get_by_name(self, db: AsyncSession, name: str) -> User | None:
        """Get user by Discord username."""
        try:
            result = await db.execute(select(User).where(User.discord_username == name))
            return result.scalars().first()
        except Exception as e:
            flogger.error(f"Error getting user by name {name}: {e}")
            raise

    async def get_by_discord_id(self, db: AsyncSession, discord_id: int) -> User | None:
        """Get user by Discord ID (snowflake).

        Args:
            db: Async database session.
            discord_id: Discord user ID (snowflake) to look up.

        Returns:
            User instance if found, None otherwise.

        Raises:
            Exception: Re-raised from underlying get_by_id on database errors.
        """
        try:
            return await self.get_by_id(db, discord_id)
        except Exception as e:
            flogger.error(f"Error getting user by Discord ID {discord_id}: {e}")
            raise

    async def count(self, db: AsyncSession) -> int:
        """Return total number of users."""
        try:
            result = await db.execute(select(func.count()).select_from(User))  # pylint: disable=not-callable
            return result.scalar_one()
        except Exception as e:
            flogger.error(f"Error counting users: {e}")
            raise

    async def list_all(self, db: AsyncSession) -> list[User]:
        """Get all users."""
        try:
            result = await db.execute(select(User))
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error listing all users: {e}")
            raise

    async def add(self, db: AsyncSession, obj: User, *, commit: bool = True) -> User:
        """Add new user to database.

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
            flogger.info(f"Added new user: {obj.id}")
            return obj
        except Exception as e:
            flogger.error(f"Error adding user {obj.id}: {e}")
            if commit:
                await db.rollback()
            raise

    async def create_or_update(self, db: AsyncSession, raw: dict, *, commit: bool = True) -> User:
        """Create or update user from raw data.

        Args:
            commit: When False, flush without committing (caller owns transaction).
        """
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
                if "display_name" in raw and raw["display_name"] is not None:
                    user.display_name = raw["display_name"]
                if commit:
                    await db.commit()
                else:
                    await db.flush()
                await db.refresh(user)
                flogger.debug(f"Updated user: {user_id}")
            else:
                # Create new user
                user = User(
                    id=user_id,
                    discord_username=raw.get("discord_username"),
                    display_name=raw.get("display_name"),
                )
                user = await self.add(db, user, commit=commit)
                flogger.info(f"Created new user: {user_id}")

            return user
        except Exception as e:
            flogger.error(f"Error creating/updating user: {e}")
            raise

    async def remove(self, db: AsyncSession, obj: User, *, commit: bool = True) -> None:
        """Remove user from database.

        Args:
            commit: When False, flush without committing (caller owns transaction).
        """
        try:
            await db.delete(obj)
            if commit:
                await db.commit()
            else:
                await db.flush()
            flogger.info(f"Removed user: {obj.id}")
        except Exception as e:
            flogger.error(f"Error removing user {obj.id}: {e}")
            if commit:
                await db.rollback()
            raise

    async def get_or_create_user(
        self,
        db: AsyncSession,
        discord_id: int,
        username: str | None = None,
        display_name: str | None = None,
        *,
        commit: bool = True,
    ) -> User:
        """Get existing user or create new one.

        Args:
            username: Discord username to set/update.
            display_name: Discord display name (server nickname or global display name).
                When provided, updates the stored value. When None, does not overwrite
                an existing value.
            commit: When False, any new-user creation or username update flushes
                without committing (caller owns transaction).
        """
        try:
            user = await self.get_by_id(db, discord_id)
            if not user:
                user = User(id=discord_id, discord_username=username, display_name=display_name)
                user = await self.add(db, user, commit=commit)
                flogger.info(f"Auto-created user for Discord ID: {discord_id}")
            else:
                changed = False
                if username and user.discord_username != username:
                    # Update username if provided and different
                    user.discord_username = username
                    changed = True
                if display_name is not None and user.display_name != display_name:
                    # Update display_name if provided and different
                    user.display_name = display_name
                    changed = True
                if changed:
                    if commit:
                        await db.commit()
                    else:
                        await db.flush()
                    await db.refresh(user)
                    flogger.debug(f"Updated user info for user {discord_id}")

            return user
        except Exception as e:
            flogger.error(f"Error getting/creating user {discord_id}: {e}")
            raise
