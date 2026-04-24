"""
Shop repository for the BountyBot inventory system.

Handles database operations for GuildShop entities including
tier-based shop management, item queries, and inventory operations.
"""

from collections.abc import Sequence

from shared import bblogger
from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from persist.interfaces.repository_interface import IRepository
from persist.models.guild_shop import GuildShop

flogger = bblogger.get_logger("shop-repository")


class ShopRepository(IRepository[GuildShop]):
    async def get_by_id(self, db: AsyncSession, obj_id: int) -> GuildShop | None:
        """Get shop item by ID."""
        try:
            return await db.get(GuildShop, obj_id)
        except Exception as e:
            flogger.error(f"Error getting shop item by ID {obj_id}: {e}")
            raise

    async def get_by_name(self, db: AsyncSession, name: str) -> GuildShop | None:
        """Not applicable for shop items."""
        raise NotImplementedError("Shop items don't have searchable names without context")

    async def count(self, db: AsyncSession) -> int:
        """Return total number of shop items."""
        try:
            result = await db.execute(select(func.count()).select_from(GuildShop))  # pylint: disable=not-callable
            return result.scalar_one()
        except Exception as e:
            flogger.error(f"Error counting shop items: {e}")
            raise

    async def list_all(self, db: AsyncSession) -> list[GuildShop]:
        """Get all shop items."""
        try:
            result = await db.execute(select(GuildShop))
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error listing all shop items: {e}")
            raise

    async def add(self, db: AsyncSession, obj: GuildShop, commit: bool = True) -> GuildShop:
        """Add new shop item to database.

        Args:
            commit: When False, flush without committing (use when the caller owns
                the transaction, e.g. inside a router-level db.begin() context).
        """
        try:
            db.add(obj)
            if commit:
                await db.commit()
            else:
                await db.flush()
            await db.refresh(obj)
            flogger.info(f"Added shop item: {obj.item_name} to {obj.tier} shop in guild {obj.guild_id}")
            return obj
        except Exception as e:
            flogger.error(f"Error adding shop item: {e}")
            if commit:
                await db.rollback()
            raise

    async def create_or_update(self, db: AsyncSession, raw: dict, commit: bool = True) -> GuildShop:
        """Create or update shop item from raw data.

        Args:
            commit: When False, flush without committing (use when the caller owns
                the transaction, e.g. inside a router-level db.begin() context).
        """
        try:
            guild_id = raw.get("guild_id")
            tier = raw.get("tier")
            item_name = raw.get("item_name")

            if not all([guild_id, tier, item_name]):
                raise ValueError("guild_id, tier, and item_name are required")

            # Check if item already exists in this shop
            existing_item = await self.get_shop_item_by_name(db, guild_id, tier, item_name)

            if existing_item:
                # Update existing item
                for key, value in raw.items():
                    if hasattr(existing_item, key) and key not in ["id", "guild_id", "tier", "item_name"]:
                        setattr(existing_item, key, value)
                if commit:
                    await db.commit()
                else:
                    await db.flush()
                await db.refresh(existing_item)
                flogger.debug(f"Updated shop item: {item_name} in {tier} shop")
                return existing_item

            # Create new shop item
            shop_item = GuildShop(**raw)
            return await self.add(db, shop_item, commit=commit)

        except Exception as e:
            flogger.error(f"Error creating/updating shop item: {e}")
            raise

    async def remove(self, db: AsyncSession, obj: GuildShop, commit: bool = True) -> None:
        """Remove shop item from database.

        Args:
            commit: When False, flush without committing (use when the caller owns
                the transaction, e.g. inside a router-level db.begin() context).
        """
        try:
            await db.delete(obj)
            if commit:
                await db.commit()
            else:
                await db.flush()
            flogger.info(f"Removed shop item: {obj.item_name} from {obj.tier} shop")
        except Exception as e:
            flogger.error(f"Error removing shop item: {e}")
            if commit:
                await db.rollback()
            raise

    async def get_shop_items(
        self, db: AsyncSession, guild_id: int, tier: str, item_type: str | None = None
    ) -> list[GuildShop]:
        """Get all items in a specific guild shop tier, optionally filtered by type."""
        try:
            query = select(GuildShop).where(and_(GuildShop.guild_id == guild_id, GuildShop.tier == tier))

            if item_type:
                query = query.where(GuildShop.item_type == item_type)

            query = query.order_by(GuildShop.item_type, GuildShop.item_name)
            result = await db.execute(query)
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error getting shop items for guild {guild_id}, tier {tier}: {e}")
            raise

    async def get_shop_items_by_types(
        self, db: AsyncSession, guild_id: int, tier: str, item_types: Sequence[str]
    ) -> list[GuildShop]:
        """Get shop items filtered by a set of concrete item types.

        Used after generic alias expansion to fetch multiple concrete types in
        a single query.  Repositories always receive concrete types.
        """
        try:
            query = (
                select(GuildShop)
                .where(
                    and_(
                        GuildShop.guild_id == guild_id,
                        GuildShop.tier == tier,
                        GuildShop.item_type.in_(item_types),
                    )
                )
                .order_by(GuildShop.item_type, GuildShop.item_name)
            )
            result = await db.execute(query)
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error getting shop items by types {item_types} for guild {guild_id} tier {tier}: {e}")
            raise

    async def get_shop_item_by_name(
        self, db: AsyncSession, guild_id: int, tier: str, item_name: str
    ) -> GuildShop | None:
        """Get a specific item from a guild shop."""
        try:
            result = await db.execute(
                select(GuildShop).where(
                    and_(GuildShop.guild_id == guild_id, GuildShop.tier == tier, GuildShop.item_name == item_name)
                )
            )
            return result.scalars().first()
        except Exception as e:
            flogger.error(f"Error getting shop item {item_name} from {tier} shop in guild {guild_id}: {e}")
            raise

    async def update_quantity(
        self, db: AsyncSession, shop_item_id: int, new_quantity: int, commit: bool = True
    ) -> GuildShop:
        """Update the quantity of a shop item.

        Args:
            commit: When False, flush without committing (use when the caller owns
                the transaction, e.g. inside a router-level db.begin() context).
        """
        try:
            if new_quantity < 0:
                raise ValueError("Quantity cannot be negative")

            await db.execute(update(GuildShop).where(GuildShop.id == shop_item_id).values(quantity=new_quantity))
            if commit:
                await db.commit()
            else:
                await db.flush()

            item = await self.get_by_id(db, shop_item_id)
            flogger.debug(f"Updated shop item {shop_item_id} quantity: {new_quantity}")
            return item
        except Exception as e:
            flogger.error(f"Error updating quantity for shop item {shop_item_id}: {e}")
            if commit:
                await db.rollback()
            raise

    async def clear_shop_tier(self, db: AsyncSession, guild_id: int, tier: str) -> None:
        """Clear all items from a specific shop tier."""
        try:
            await db.execute(delete(GuildShop).where(and_(GuildShop.guild_id == guild_id, GuildShop.tier == tier)))
            await db.commit()
            flogger.info(f"Cleared all items from {tier} shop in guild {guild_id}")
        except Exception as e:
            flogger.error(f"Error clearing {tier} shop in guild {guild_id}: {e}")
            await db.rollback()
            raise

    async def clear_all_guild_shops(self, db: AsyncSession, guild_id: int) -> None:
        """Clear all shop items for a guild."""
        try:
            await db.execute(delete(GuildShop).where(GuildShop.guild_id == guild_id))
            await db.commit()
            flogger.info(f"Cleared all shops for guild {guild_id}")
        except Exception as e:
            flogger.error(f"Error clearing all shops for guild {guild_id}: {e}")
            await db.rollback()
            raise

    async def get_guild_shops_summary(self, db: AsyncSession, guild_id: int) -> dict:
        """Get a summary of all shops for a guild."""
        try:
            result = await db.execute(select(GuildShop).where(GuildShop.guild_id == guild_id))
            items = result.scalars().all()

            summary = {
                "guild_id": guild_id,
                "total_items": len(items),
                "shops": {
                    "Bronze": {"items": 0, "total_quantity": 0},
                    "Silver": {"items": 0, "total_quantity": 0},
                    "Gold": {"items": 0, "total_quantity": 0},
                    "Platinum": {"items": 0, "total_quantity": 0},
                },
            }

            for item in items:
                if item.tier in summary["shops"]:
                    summary["shops"][item.tier]["items"] += 1
                    summary["shops"][item.tier]["total_quantity"] += item.quantity

            return summary
        except Exception as e:
            flogger.error(f"Error getting shops summary for guild {guild_id}: {e}")
            raise

    async def get_items_by_tech_level(
        self, db: AsyncSession, guild_id: int, tier: str, tech_level: int
    ) -> list[GuildShop]:
        """Get all items of a specific tech level from a shop."""
        try:
            result = await db.execute(
                select(GuildShop)
                .where(and_(GuildShop.guild_id == guild_id, GuildShop.tier == tier, GuildShop.tech_level == tech_level))
                .order_by(GuildShop.item_type, GuildShop.item_name)
            )
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error getting items by tech level {tech_level}: {e}")
            raise

    async def update_prices(self, db: AsyncSession, guild_id: int, price_multiplier: float) -> int:
        """Update all shop prices for a guild by a multiplier."""
        try:
            if price_multiplier <= 0:
                raise ValueError("Price multiplier must be positive")

            result = await db.execute(
                update(GuildShop).where(GuildShop.guild_id == guild_id).values(price=GuildShop.price * price_multiplier)
            )
            await db.commit()

            updated_count = result.rowcount
            flogger.info(f"Updated {updated_count} shop item prices for guild {guild_id}")
            return updated_count
        except Exception as e:
            flogger.error(f"Error updating prices for guild {guild_id}: {e}")
            await db.rollback()
            raise

    async def get_items_due_for_refresh(self, db: AsyncSession, guild_id: int) -> list[GuildShop]:
        """Get all shop items that are due for refresh based on their intervals."""
        try:
            result = await db.execute(select(GuildShop).where(GuildShop.guild_id == guild_id))
            items = result.scalars().all()

            # Filter items that are due for refresh
            due_items = [item for item in items if item.is_refresh_due()]

            flogger.debug(f"Found {len(due_items)} items due for refresh in guild {guild_id}")
            return due_items
        except Exception as e:
            flogger.error(f"Error getting items due for refresh in guild {guild_id}: {e}")
            raise

    async def get_shop_statistics(self, db: AsyncSession, guild_id: int, tier: str) -> dict:
        """Get detailed statistics for a specific shop."""
        try:
            items = await self.get_shop_items(db, guild_id, tier)

            stats = {
                "guild_id": guild_id,
                "tier": tier,
                "total_items": len(items),
                "total_quantity": sum(item.quantity for item in items),
                "item_types": {},
                "tech_levels": {},
                "price_range": {"min": 0, "max": 0, "average": 0},
            }

            if items:
                # Calculate statistics
                prices = [item.price for item in items]
                stats["price_range"]["min"] = min(prices)
                stats["price_range"]["max"] = max(prices)
                stats["price_range"]["average"] = sum(prices) / len(prices)

                # Count by item type
                for item in items:
                    stats["item_types"][item.item_type] = stats["item_types"].get(item.item_type, 0) + 1
                    stats["tech_levels"][item.tech_level] = stats["tech_levels"].get(item.tech_level, 0) + 1

            return stats
        except Exception as e:
            flogger.error(f"Error getting statistics for {tier} shop in guild {guild_id}: {e}")
            raise
