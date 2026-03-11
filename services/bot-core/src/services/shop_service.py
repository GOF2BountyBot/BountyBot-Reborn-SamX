"""
Complete Shop Service for the BountyBot inventory system.

Handles business logic for multi-tier shop management including:
- Tier-based shop access control
- Automatic shop generation and refresh
- Purchase and sell transactions
- Item pricing and availability
"""

import random
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from persist.models.guild_shop import GuildShop
from persist.repositories.config_repository import ConfigRepository
from persist.repositories.inventory_repository import InventoryRepository
from persist.repositories.player_repository import PlayerRepository
from persist.repositories.shop_repository import ShopRepository
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

flogger = bblogger.get_logger("shop-service")

class ShopService:
    def __init__(self):
        self.shop_repo = ShopRepository()
        self.config_repo = ConfigRepository()
        self.player_repo = PlayerRepository()
        self.inventory_repo = InventoryRepository()

    # Valid tiers and item types
    VALID_TIERS = ["Bronze", "Silver", "Gold", "Platinum"]
    VALID_ITEM_TYPES = ["ship", "weapon", "module", "turret"]

    async def get_shop_items(
        self,
        db: AsyncSession,
        guild_id: int,
        tier: str,
        item_type: Optional[str] = None
    ) -> List[GuildShop]:
        """
        Get shop items for a specific guild tier.
        Optionally filter by item type.
        """
        try:
            if tier not in self.VALID_TIERS:
                raise ValueError(f"Invalid tier: {tier}")

            if item_type and item_type not in self.VALID_ITEM_TYPES:
                raise ValueError(f"Invalid item type: {item_type}")

            # Check if shop needs refresh
            await self._check_and_refresh_shop(db, guild_id, tier)

            # Get shop items
            items = await self.shop_repo.get_shop_items(db, guild_id, tier, item_type)

            flogger.debug(f"Retrieved {len(items)} items from {tier} shop in guild {guild_id}")
            return items

        except Exception as e:
            flogger.error(f"Error getting shop items for guild {guild_id}, tier {tier}: {e}")
            raise

    async def purchase_item(
        self,
        db: AsyncSession,
        player_id: int,
        shop_item_id: int,
        quantity: int = 1
    ) -> Dict[str, Any]:
        """
        Purchase an item from the shop.

        Returns transaction details including cost and remaining shop quantity.
        """
        try:
            # Get player and validate
            player = await self.player_repo.get_by_id(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            # Get shop item
            shop_item = await self.shop_repo.get_by_id(db, shop_item_id)
            if not shop_item:
                raise ValueError(f"Shop item {shop_item_id} not found")

            # Validate tier access
            if not self._can_access_tier(player.tier, shop_item.tier):
                raise ValueError(f"Player tier {player.tier} cannot access {shop_item.tier} shop")

            # Check quantity availability
            if shop_item.quantity < quantity:
                raise ValueError(f"Insufficient quantity. Available: {shop_item.quantity}, Requested: {quantity}")

            # Calculate total cost
            total_cost = shop_item.price * quantity

            # Check player credits
            if player.credits < total_cost:
                raise ValueError(f"Insufficient credits. Cost: {total_cost}, Available: {player.credits}")

            # Perform transaction atomically
            async with db.begin():
                # Deduct credits from player
                await self.player_repo.update_credits(db, player_id, player.credits - total_cost)

                # Add item to player inventory
                await self.inventory_repo.add_item(
                    db, player_id, shop_item.item_type, shop_item.item_name, quantity
                )

                # Remove item from shop
                new_shop_quantity = shop_item.quantity - quantity
                if new_shop_quantity <= 0:
                    # Remove item entirely if quantity reaches 0
                    await self.shop_repo.remove(db, shop_item)
                else:
                    # Update shop quantity
                    await self.shop_repo.update_quantity(db, shop_item_id, new_shop_quantity)

            transaction_details = {
                "player_id": player_id,
                "item_type": shop_item.item_type,
                "item_name": shop_item.item_name,
                "quantity": quantity,
                "unit_price": shop_item.price,
                "total_cost": total_cost,
                "remaining_credits": player.credits - total_cost,
                "remaining_shop_quantity": new_shop_quantity
            }

            flogger.info(f"Player {player_id} purchased {quantity}x {shop_item.item_name} for {total_cost} credits")
            return transaction_details

        except Exception as e:
            flogger.error(f"Error purchasing item {shop_item_id} for player {player_id}: {e}")
            raise

    async def sell_item(
        self,
        db: AsyncSession,
        player_id: int,
        item_type: str,
        item_name: str,
        quantity: int = 1,
        target_tier: str = "Bronze"
    ) -> Dict[str, Any]:
        """
        Sell an item back to the shop.

        Items are added to the specified tier shop and player receives credits.
        """
        try:
            # Get player and validate
            player = await self.player_repo.get_by_id(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            # Validate inputs
            if item_type not in self.VALID_ITEM_TYPES:
                raise ValueError(f"Invalid item type: {item_type}")

            if target_tier not in self.VALID_TIERS:
                raise ValueError(f"Invalid target tier: {target_tier}")

            # Check if player has the item
            inventory_item = await self.inventory_repo.get_player_item(
                db, player_id, item_type, item_name
            )
            if not inventory_item or inventory_item.quantity < quantity:
                available = inventory_item.quantity if inventory_item else 0
                raise ValueError(f"Insufficient item quantity. Available: {available}, Requested: {quantity}")

            # Get guild config for sale price calculation
            config = await self.config_repo.get_by_guild_id(db, player.guild_id)
            sale_price_factor = config.sale_price_factor if config else 0.8

            # Calculate item price (we need to get this from static data)
            # For now, using a base price - this should be integrated with static item data
            base_price = await self._get_item_base_price(item_name)
            unit_sell_price = int(base_price * sale_price_factor)
            total_sell_value = unit_sell_price * quantity

            # Perform transaction atomically
            async with db.begin():
                # Remove item from player inventory
                await self.inventory_repo.remove_item(db, player_id, item_type, item_name, quantity)

                # Add credits to player
                await self.player_repo.update_credits(db, player_id, player.credits + total_sell_value)

                # Add item to target shop
                await self._add_item_to_shop(
                    db, player.guild_id, target_tier, item_type,
                    item_name, quantity, base_price)

            transaction_details = {
                "player_id": player_id,
                "item_type": item_type,
                "item_name": item_name,
                "quantity": quantity,
                "unit_sell_price": unit_sell_price,
                "total_sell_value": total_sell_value,
                "new_credits": player.credits + total_sell_value,
                "target_shop_tier": target_tier
            }

            flogger.info(f"Player {player_id} sold {quantity}x {item_name} for {total_sell_value} credits")
            return transaction_details

        except Exception as e:
            flogger.error(f"Error selling item {item_name} for player {player_id}: {e}")
            raise

    async def refresh_shop(
        self,
        db: AsyncSession,
        guild_id: int,
        tier: str,
        force_tech_level: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Refresh a shop's inventory based on guild configuration.

        Optionally force a specific tech level instead of random selection.
        """
        try:
            if tier not in self.VALID_TIERS:
                raise ValueError(f"Invalid tier: {tier}")

            if force_tech_level is not None and (force_tech_level < 1 or force_tech_level > 9):
                raise ValueError("Tech level must be between 1 and 9")

            # Get guild configuration
            config = await self.config_repo.get_by_guild_id(db, guild_id)
            if not config:
                # Create default config if none exists
                config = await self.config_repo.create_default_config(db, guild_id)

            # Clear existing shop items for this tier
            await self.shop_repo.clear_shop_tier(db, guild_id, tier)

            # Determine tech level
            shop_tech_level = force_tech_level if force_tech_level else random.randint(1, 9)

            # Generate new shop inventory
            generated_items = []

            for item_type in self.VALID_ITEM_TYPES:
                count_range = config.get_count_range(item_type)
                quantity_range = config.get_quantity_range(item_type)

                item_count = random.randint(count_range["min"], count_range["max"])

                for _ in range(item_count):
                    # Select item based on tech level probabilities
                    item_tech_level = self._select_item_tech_level(shop_tech_level, config.tech_level_probabilities)
                    item_quantity = random.randint(quantity_range["min"], quantity_range["max"])

                    # Get random item of the selected tech level
                    item_name = await self._get_random_item_by_tech_level(item_type, item_tech_level)
                    if not item_name:
                        continue  # Skip if no items available at this tech level

                    # Calculate price
                    base_price = await self._get_item_base_price(item_name)

                    # Create shop item
                    shop_item_data = {
                        "guild_id": guild_id,
                        "tier": tier,
                        "tech_level": shop_tech_level,
                        "item_type": item_type,
                        "item_name": item_name,
                        "quantity": item_quantity,
                        "price": base_price,
                        "last_restocked": datetime.now(UTC)
                    }

                    shop_item = await self.shop_repo.create_or_update(db, shop_item_data)
                    generated_items.append(shop_item)

            refresh_details = {
                "guild_id": guild_id,
                "tier": tier,
                "tech_level": shop_tech_level,
                "items_generated": len(generated_items),
                "refresh_time": datetime.now(UTC).isoformat()
            }

            flogger.info(f"Refreshed {tier} shop for guild {guild_id}: {len(generated_items)} items generated")
            return refresh_details

        except Exception as e:
            flogger.error(f"Error refreshing shop for guild {guild_id}, tier {tier}: {e}")
            raise

    async def _check_and_refresh_shop(self, db: AsyncSession, guild_id: int, tier: str) -> None:
        """Check if shop needs refresh and refresh if necessary."""
        try:
            # Get shop items to check last refresh time
            items = await self.shop_repo.get_shop_items(db, guild_id, tier)

            if not items:
                # No items means shop needs initial generation
                await self.refresh_shop(db, guild_id, tier)
                return

            # Check if any items are due for refresh
            needs_refresh = any(item.is_refresh_due() for item in items)

            if needs_refresh:
                await self.refresh_shop(db, guild_id, tier)
                flogger.info(f"Auto-refreshed {tier} shop for guild {guild_id}")

        except Exception as e:
            flogger.error(f"Error checking shop refresh for guild {guild_id}, tier {tier}: {e}")
            raise

    def _can_access_tier(self, player_tier: str, shop_tier: str) -> bool:
        """Check if a player tier can access a shop tier."""
        tier_levels = {"Bronze": 1, "Silver": 2, "Gold": 3, "Platinum": 4}
        player_level = tier_levels.get(player_tier, 1)
        shop_level = tier_levels.get(shop_tier, 1)
        return player_level >= shop_level

    def _select_item_tech_level(self, shop_tech_level: int, probabilities: Dict[str, float]) -> int:
        """Select item tech level based on shop tech level and probability distribution."""
        same_level_prob = probabilities.get("same_level", 0.7)
        one_lower_prob = probabilities.get("one_lower", 0.2)
        _two_lower_prob = probabilities.get("two_lower", 0.1)

        rand = random.random()

        if rand < same_level_prob:
            return shop_tech_level
        if rand < same_level_prob + one_lower_prob:
            return max(1, shop_tech_level - 1)
        return max(1, shop_tech_level - 2)

    async def _get_random_item_by_tech_level(self, item_type: str, tech_level: int) -> Optional[str]:
        """Get a random item name by type and tech level from static data."""
        # TODO: Integrate with existing static data loading system
        # For now, return placeholder items
        placeholder_items = {
            "ship": [f"Ship_{tech_level}_{i}" for i in range(1, 6)],
            "weapon": [f"Weapon_{tech_level}_{i}" for i in range(1, 6)],
            "module": [f"Module_{tech_level}_{i}" for i in range(1, 6)],
            "turret": [f"Turret_{tech_level}_{i}" for i in range(1, 6)]
        }

        items = placeholder_items.get(item_type, [])
        return random.choice(items) if items else None

    async def _get_item_base_price(self, item_name: str) -> int:
        """Get base price for an item from static data."""
        # TODO: Integrate with existing static data system
        # For now, return placeholder prices based on item name patterns
        if "1" in item_name:
            return random.randint(100, 500)
        if "2" in item_name:
            return random.randint(500, 1000)
        return random.randint(1000, 5000)

    async def _add_item_to_shop(
        self,
        db: AsyncSession,
        guild_id: int,
        tier: str,
        item_type: str,
        item_name: str,
        quantity: int,
        base_price: int
    ) -> None:
        """Add an item to a shop (used when players sell items)."""
        try:
            # Check if item already exists in shop
            existing_item = await self.shop_repo.get_shop_item_by_name(db, guild_id, tier, item_name)

            if existing_item:
                # Update quantity
                new_quantity = existing_item.quantity + quantity
                await self.shop_repo.update_quantity(db, existing_item.id, new_quantity)
            else:
                # Create new shop item
                shop_item_data = {
                    "guild_id": guild_id,
                    "tier": tier,
                    "tech_level": 1,  # Default tech level for sold items
                    "item_type": item_type,
                    "item_name": item_name,
                    "quantity": quantity,
                    "price": base_price,
                    "last_restocked": datetime.now(UTC)
                }
                await self.shop_repo.create_or_update(db, shop_item_data)

        except Exception as e:
            flogger.error(f"Error adding item to shop: {e}")
            raise
