"""
Ship repository for the BountyBot inventory system.

Handles database operations for PlayerShip entities including
ship ownership, loadout management, and active ship tracking.
"""

from typing import Any, Dict, List, Optional

from shared import bblogger
from persist.interfaces.repository_interface import IRepository
from persist.models.player_ship import PlayerShip
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

flogger = bblogger.get_logger("ship-repository")

class PlayerShipRepository(IRepository[PlayerShip]):

    async def get_by_id(self, db: AsyncSession, obj_id: int) -> Optional[PlayerShip]:
        """Get ship by ID."""
        try:
            return await db.get(PlayerShip, obj_id)
        except Exception as e:
            flogger.error(f"Error getting ship by ID {obj_id}: {e}")
            raise

    async def get_by_name(self, db: AsyncSession, name: str) -> Optional[PlayerShip]:
        """Not applicable for ships - they don't have unique names."""
        raise NotImplementedError("Ships don't have globally unique names")

    async def list_all(self, db: AsyncSession) -> List[PlayerShip]:
        """Get all ships."""
        try:
            result = await db.execute(select(PlayerShip))
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error listing all ships: {e}")
            raise

    async def add(self, db: AsyncSession, obj: PlayerShip) -> PlayerShip:
        """Add new ship to database."""
        try:
            db.add(obj)
            await db.commit()
            await db.refresh(obj)
            flogger.info(f"Added ship: {obj.ship_name} for player {obj.player_id}")
            return obj
        except Exception as e:
            flogger.error(f"Error adding ship: {e}")
            await db.rollback()
            raise

    async def create_or_update(self, db: AsyncSession, raw: dict) -> PlayerShip:
        """Create or update ship from raw data."""
        try:
            player_id = raw.get("player_id")
            ship_name = raw.get("ship_name")

            if not player_id or not ship_name:
                raise ValueError("player_id and ship_name are required")

            # For ships, we typically create new instances rather than update
            # unless we're updating an existing ship by ID
            ship_id = raw.get("id")

            if ship_id:
                # Update existing ship
                ship = await self.get_by_id(db, ship_id)
                if ship:
                    for key, value in raw.items():
                        if hasattr(ship, key) and key not in ['id', 'player_id', 'created_at']:
                            setattr(ship, key, value)
                    await db.commit()
                    await db.refresh(ship)
                    flogger.debug(f"Updated ship: {ship_id}")
                    return ship

            # Create new ship
            ship = PlayerShip(**raw)
            return await self.add(db, ship)

        except Exception as e:
            flogger.error(f"Error creating/updating ship: {e}")
            raise

    async def remove(self, db: AsyncSession, obj: PlayerShip) -> None:
        """Remove ship from database."""
        try:
            await db.delete(obj)
            await db.commit()
            flogger.info(f"Removed ship: {obj.id}")
        except Exception as e:
            flogger.error(f"Error removing ship {obj.id}: {e}")
            await db.rollback()
            raise

    async def get_player_ships(self, db: AsyncSession, player_id: int) -> List[PlayerShip]:
        """Get all ships owned by a player."""
        try:
            result = await db.execute(
                select(PlayerShip)
                .where(PlayerShip.player_id == player_id)
                .order_by(PlayerShip.is_active.desc(), PlayerShip.created_at)
            )
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error getting ships for player {player_id}: {e}")
            raise

    async def get_active_ship(self, db: AsyncSession, player_id: int) -> Optional[PlayerShip]:
        """Get the active ship for a player."""
        try:
            result = await db.execute(
                select(PlayerShip).where(
                    and_(
                        PlayerShip.player_id == player_id,
                        PlayerShip.is_active == True
                    )
                )
            )
            return result.scalars().first()
        except Exception as e:
            flogger.error(f"Error getting active ship for player {player_id}: {e}")
            raise

    async def set_active_ship(self, db: AsyncSession, player_id: int, ship_id: int) -> PlayerShip:
        """Set a ship as the active ship for a player."""
        try:
            # Verify ship belongs to player
            ship = await self.get_by_id(db, ship_id)
            if not ship or ship.player_id != player_id:
                raise ValueError(f"Ship {ship_id} not found or doesn't belong to player {player_id}")

            # Deactivate all other ships for this player
            await db.execute(
                update(PlayerShip)
                .where(PlayerShip.player_id == player_id)
                .values(is_active=False)
            )

            # Activate the target ship
            await db.execute(
                update(PlayerShip)
                .where(PlayerShip.id == ship_id)
                .values(is_active=True)
            )

            await db.commit()

            # Refresh and return the ship
            await db.refresh(ship)
            flogger.info(f"Set ship {ship_id} as active for player {player_id}")
            return ship

        except Exception as e:
            flogger.error(f"Error setting active ship {ship_id} for player {player_id}: {e}")
            await db.rollback()
            raise

    async def update_loadout(
        self,
        db: AsyncSession,
        ship_id: int,
        loadout: Dict[str, List[str]]
    ) -> PlayerShip:
        """Update a ship's equipment loadout."""
        try:
            ship = await self.get_by_id(db, ship_id)
            if not ship:
                raise ValueError(f"Ship {ship_id} not found")

            # Update loadout fields
            if "weapons" in loadout:
                ship.weapons = loadout["weapons"]
            if "modules" in loadout:
                ship.modules = loadout["modules"]
            if "turrets" in loadout:
                ship.turrets = loadout["turrets"]

            await db.commit()
            await db.refresh(ship)

            flogger.debug(f"Updated loadout for ship {ship_id}")
            return ship

        except Exception as e:
            flogger.error(f"Error updating loadout for ship {ship_id}: {e}")
            await db.rollback()
            raise

    async def add_equipment(
        self,
        db: AsyncSession,
        ship_id: int,
        equipment_type: str,
        item_name: str
    ) -> PlayerShip:
        """Add a piece of equipment to a ship's loadout."""
        try:
            ship = await self.get_by_id(db, ship_id)
            if not ship:
                raise ValueError(f"Ship {ship_id} not found")

            # Get current loadout
            current_loadout = {}
            if equipment_type == "weapons":
                current_loadout = ship.weapons or []
            elif equipment_type == "modules":
                current_loadout = ship.modules or []
            elif equipment_type == "turrets":
                current_loadout = ship.turrets or []
            else:
                raise ValueError(f"Invalid equipment type: {equipment_type}")

            # Add item to loadout
            updated_loadout = list(current_loadout)
            updated_loadout.append(item_name)

            # Update ship
            await self.update_loadout(db, ship_id, {equipment_type: updated_loadout})

            flogger.debug(f"Added {item_name} to {equipment_type} on ship {ship_id}")
            return ship

        except Exception as e:
            flogger.error(f"Error adding equipment to ship {ship_id}: {e}")
            raise

    async def remove_equipment(
        self,
        db: AsyncSession,
        ship_id: int,
        equipment_type: str,
        item_name: str
    ) -> PlayerShip:
        """Remove a piece of equipment from a ship's loadout."""
        try:
            ship = await self.get_by_id(db, ship_id)
            if not ship:
                raise ValueError(f"Ship {ship_id} not found")

            # Get current loadout
            current_loadout = []
            if equipment_type == "weapons":
                current_loadout = ship.weapons or []
            elif equipment_type == "modules":
                current_loadout = ship.modules or []
            elif equipment_type == "turrets":
                current_loadout = ship.turrets or []
            else:
                raise ValueError(f"Invalid equipment type: {equipment_type}")

            # Remove item from loadout
            if item_name not in current_loadout:
                raise ValueError(f"Item {item_name} not equipped in {equipment_type}")

            updated_loadout = list(current_loadout)
            updated_loadout.remove(item_name)

            # Update ship
            await self.update_loadout(db, ship_id, {equipment_type: updated_loadout})

            flogger.debug(f"Removed {item_name} from {equipment_type} on ship {ship_id}")
            return ship

        except Exception as e:
            flogger.error(f"Error removing equipment from ship {ship_id}: {e}")
            raise

    async def update_nickname(self, db: AsyncSession, ship_id: int, nickname: str) -> PlayerShip:
        """Update a ship's nickname."""
        try:
            ship = await self.get_by_id(db, ship_id)
            if not ship:
                raise ValueError(f"Ship {ship_id} not found")

            ship.nickname = nickname
            await db.commit()
            await db.refresh(ship)

            flogger.debug(f"Updated nickname for ship {ship_id}: {nickname}")
            return ship

        except Exception as e:
            flogger.error(f"Error updating nickname for ship {ship_id}: {e}")
            await db.rollback()
            raise

    async def get_ships_by_name(self, db: AsyncSession, player_id: int, ship_name: str) -> List[PlayerShip]:
        """Get all ships of a specific type owned by a player."""
        try:
            result = await db.execute(
                select(PlayerShip).where(
                    and_(
                        PlayerShip.player_id == player_id,
                        PlayerShip.ship_name == ship_name
                    )
                ).order_by(PlayerShip.created_at)
            )
            return list(result.scalars().all())
        except Exception as e:
            flogger.error(f"Error getting ships by name for player {player_id}: {e}")
            raise

    async def get_ship_loadout_summary(self, db: AsyncSession, ship_id: int) -> Dict[str, Any]:
        """Get a summary of a ship's current loadout."""
        try:
            ship = await self.get_by_id(db, ship_id)
            if not ship:
                raise ValueError(f"Ship {ship_id} not found")

            return {
                "ship_id": ship.id,
                "ship_name": ship.ship_name,
                "nickname": ship.nickname,
                "is_active": ship.is_active,
                "weapons": ship.weapons or [],
                "modules": ship.modules or [],
                "turrets": ship.turrets or [],
                "weapons_count": len(ship.weapons) if ship.weapons else 0,
                "modules_count": len(ship.modules) if ship.modules else 0,
                "turrets_count": len(ship.turrets) if ship.turrets else 0
            }

        except Exception as e:
            flogger.error(f"Error getting loadout summary for ship {ship_id}: {e}")
            raise
