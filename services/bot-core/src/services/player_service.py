"""
Player Service for the BountyBot inventory system.

Handles business logic for player management including creation,
progression, and guild-isolated operations.
"""

from typing import Any

from persist.models.player import Player
from persist.models.user import User
from persist.repositories.config_repository import ConfigRepository
from persist.repositories.player_repository import PlayerRepository
from persist.repositories.user_repository import UserRepository
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

flogger = bblogger.get_logger("player-service")

class PlayerService:
    def __init__(self):
        self.user_repo = UserRepository()
        self.player_repo = PlayerRepository()
        self.config_repo = ConfigRepository()

    async def get_or_create_player(
        self,
        db: AsyncSession,
        discord_id: int,
        guild_id: int,
        discord_username: str | None = None
    ) -> Player:
        """
        Get existing player or create new one with starter loadout.

        This is the main entry point for player management when a user
        first interacts with the bot in a guild.
        """
        try:
            # Ensure user exists
            user = await self.user_repo.get_or_create_user(db, discord_id, discord_username)

            # Check if player exists for this guild
            player = await self.player_repo.get_by_user_and_guild(db, discord_id, guild_id)

            if player:
                flogger.debug(f"Found existing player {player.id} for user {discord_id} in guild {guild_id}")
                return player

            # Create new player with starter configuration
            player = await self._create_new_player(db, user, guild_id)
            flogger.info(f"Created new player {player.id} for user {discord_id} in guild {guild_id}")

            return player

        except Exception as e:
            flogger.error(f"Error getting/creating player for user {discord_id} in guild {guild_id}: {e}")
            raise

    async def _create_new_player(self, db: AsyncSession, user: User, guild_id: int) -> Player:
        """Create a new player with default configuration and starter loadout."""
        try:
            # Get guild configuration for starting credits
            config = await self.config_repo.get_by_guild_id(db, guild_id)
            starting_credits = config.starting_credits if config else 0

            # Create player with default values
            player = Player(
                user_id=user.id,
                guild_id=guild_id,
                credits=starting_credits,
                tier="Bronze",
                xp=0
            )

            player = await self.player_repo.add(db, player)

            # Create starter loadout
            await self._create_starter_loadout(db, player)

            return player

        except Exception as e:
            flogger.error(f"Error creating new player: {e}")
            raise

    async def _create_starter_loadout(self, db: AsyncSession, player: Player) -> None:
        """Create the starter ship and equipment for a new player."""
        try:
            from persist.repositories.inventory_repository import InventoryRepository
            from persist.repositories.ship_repository import ShipRepository

            ship_repo = ShipRepository()
            _inventory_repo = InventoryRepository()

            # Create starter ship "Betty" with basic equipment
            starter_ship_data = {
                "player_id": player.id,
                "ship_name": "Betty",
                "is_active": True,
                "weapons": ["Micro Gun MK I"],
                "modules": ["Telta Quickscan", "E2 Exoclad", "IMT Extract 1.3"],
                "turrets": []
            }

            starter_ship = await ship_repo.create_or_update(db, starter_ship_data)

            # Update player's active ship
            await self.player_repo.update_active_ship(db, player.id, starter_ship.id)

            flogger.info("Created starter loadout for player %s", player.id)

        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error("Error creating starter loadout for player %s: %s", player.id, e)
            raise

    async def update_player_credits(
        self,
        db: AsyncSession,
        player_id: int,
        new_credits: int,
        update_lifetime: bool = True
    ) -> Player:
        """Update player new_credits and optionally lifetime new_credits."""
        try:
            player = await self.player_repo.get_by_id(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            if new_credits < 0:
                raise ValueError("Credits cannot be negative")

            # Update lifetime new_credits if this is an increase
            if update_lifetime and new_credits > player.new_credits:
                credit_increase = new_credits - player.new_credits
                player.lifetime_credits += credit_increase

            player.new_credits = new_credits
            await db.commit()
            await db.refresh(player)

            flogger.debug(f"Updated new_credits for player {player_id}: {new_credits}")
            return player

        except Exception as e:
            flogger.error(f"Error updating new_credits for player {player_id}: {e}")
            raise

    async def update_player_xp(self, db: AsyncSession, player_id: int, xp: int) -> Player:
        """Update player XP and check for tier advancement."""
        try:
            player = await self.player_repo.get_by_id(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            if xp < 0:
                xp = 0  # Clamp to 0
            elif xp > 1000000:
                xp = 1000000  # Clamp to max

            old_tier = player.tier
            player.xp = xp

            # Check for tier advancement
            config = await self.config_repo.get_by_guild_id(db, player.guild_id)
            if config:
                new_tier = self._calculate_tier_from_xp(xp, config.xp_thresholds)
                if new_tier != old_tier:
                    player.tier = new_tier
                    flogger.info(f"Player {player_id} advanced from {old_tier} to {new_tier}")

            await db.commit()
            await db.refresh(player)

            flogger.debug(f"Updated XP for player {player_id}: {xp}")
            return player

        except Exception as e:
            flogger.error(f"Error updating XP for player {player_id}: {e}")
            raise

    def _calculate_tier_from_xp(self, xp: int, thresholds: dict[str, int]) -> str:
        """Calculate player tier based on XP and thresholds."""
        if xp >= thresholds.get("Platinum", 15000):
            return "Platinum"
        if xp >= thresholds.get("Gold", 5000):
            return "Gold"
        if xp >= thresholds.get("Silver", 1000):
            return "Silver"
        return "Bronze"

    async def prestige_player(self, db: AsyncSession, player_id: int) -> Player:
        """Reset player to Bronze tier but increment prestige count."""
        try:
            player = await self.player_repo.get_by_id(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            if player.tier != "Platinum":
                raise ValueError("Player must be Platinum tier to prestige")

            # Reset to Bronze but keep some benefits
            player.tier = "Bronze"
            player.xp = 0
            player.prestige_count += 1
            # Note: lifetime_credits and ships are kept as prestige benefits

            await db.commit()
            await db.refresh(player)

            flogger.info(f"Player {player_id} prestiged (count: {player.prestige_count})")
            return player

        except Exception as e:
            flogger.error(f"Error prestiging player {player_id}: {e}")
            raise

    async def get_player_statistics(self, db: AsyncSession, player_id: int) -> dict[str, Any]:
        """Get comprehensive player statistics."""
        try:
            player = await self.player_repo.get_by_id(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            # Calculate additional statistics
            total_duels = player.duel_wins + player.duel_losses
            duel_win_rate = (player.duel_wins / total_duels * 100) if total_duels > 0 else 0
            net_duel_credits = player.duel_credits_won - player.duel_credits_lost

            return {
                "player_id": player.id,
                "tier": player.tier,
                "tier_level": player.tier_level,
                "xp": player.xp,
                "prestige_count": player.prestige_count,
                "credits": player.credits,
                "lifetime_credits": player.lifetime_credits,
                "bounty_stats": {
                    "systems_checked": player.systems_checked,
                    "bounty_wins": player.bounty_wins
                },
                "duel_stats": {
                    "wins": player.duel_wins,
                    "losses": player.duel_losses,
                    "win_rate": round(duel_win_rate, 2),
                    "credits_won": player.duel_credits_won,
                    "credits_lost": player.duel_credits_lost,
                    "net_credits": net_duel_credits
                },
                "created_at": player.created_at.isoformat(),
                "updated_at": player.updated_at.isoformat()
            }

        except Exception as e:
            flogger.error(f"Error getting statistics for player {player_id}: {e}")
            raise

    async def get_players_by_tier(self, db: AsyncSession, guild_id: int, tier: str) -> list[Player]:
        """Get all players in a guild with a specific tier."""
        try:
            players = await self.player_repo.get_players_by_guild(db, guild_id)
            return [p for p in players if p.tier == tier]
        except Exception as e:
            flogger.error(f"Error getting players by tier {tier} in guild {guild_id}: {e}")
            raise
