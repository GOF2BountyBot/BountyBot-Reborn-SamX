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

from services.division_service import DivisionService
from services.exceptions import GuildNotConfiguredError
from services.game_constants import GameConstants
from services.game_maths import calculate_user_level

flogger = bblogger.get_logger("player-service")

# Tier ordering constants
_TIER_ORDER = {"Bronze": 1, "Silver": 2, "Gold": 3, "Platinum": 4}
_TIER_NAMES = {1: "Bronze", 2: "Silver", 3: "Gold", 4: "Platinum"}


class PlayerService:
    def __init__(self):
        self.user_repo = UserRepository()
        self.player_repo = PlayerRepository()
        self.config_repo = ConfigRepository()

    async def get_or_create_player(
        self, db: AsyncSession, discord_id: int, guild_id: int, discord_username: str | None = None
    ) -> Player:
        """
        Get existing player or create new one with starter loadout.

        This is the main entry point for player management when a user
        first interacts with the bot in a guild.

        Raises GuildNotConfiguredError if no guild_configs row exists
        (i.e. /admin_setup has not been run for this guild).
        """
        try:
            # Check if player already exists for this guild (before config check to avoid
            # penalising guilds where a player was created before this guard was added).
            existing_player = await self.player_repo.get_by_user_and_guild(db, discord_id, guild_id)
            if existing_player:
                flogger.debug(f"Found existing player {existing_player.id} for user {discord_id} in guild {guild_id}")
                return existing_player

            # New player path — guild must have a config row first.
            config = await self.config_repo.get_by_guild_id(db, guild_id)
            if not config:
                flogger.warning(f"Cannot create player for user {discord_id} in guild {guild_id}: guild not configured")
                raise GuildNotConfiguredError(guild_id)

            # Ensure user exists
            user = await self.user_repo.get_or_create_user(db, discord_id, discord_username)

            # Create new player with starter configuration
            player = await self._create_new_player(db, user, guild_id)
            flogger.info(f"Created new player {player.id} for user {discord_id} in guild {guild_id}")

            return player

        except GuildNotConfiguredError:
            raise
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
                xp=0,
                xp_surplus=0,
                classic_mode=False,
                guild_transfer_cooldown=None,
                bounty_cooldown_end=None,
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
            from persist.repositories.player_ship_repository import PlayerShipRepository

            player_ship_repo = PlayerShipRepository()

            # Create starter PlayerShip record linking the player to the "Betty" ship
            starter_ship_data = {
                "player_id": player.id,
                "ship_name": "Betty",
                "is_active": True,
                "weapons": ["Nirai Impulse EX 1"],
                "modules": ["E2 Exoclad", "Telta Quickscan"],
                "turrets": [],
            }

            starter_ship = await player_ship_repo.create_or_update(db, starter_ship_data)

            # Update player's active ship reference (PlayerShip.id, not Ship.id)
            await self.player_repo.update_active_ship(db, player.id, starter_ship.id)

            # Add Micro Gun MK I to player's cargo inventory
            inv_repo = InventoryRepository()
            await inv_repo.add_item(db, player.id, "primary_weapon", "Micro Gun MK I", quantity=1)

            flogger.info("Created starter loadout for player %s", player.id)

        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error("Error creating starter loadout for player %s: %s", player.id, e)
            raise

    async def update_player_credits(
        self, db: AsyncSession, player_id: int, new_credits: int, update_lifetime: bool = True
    ) -> Player:
        """Update player credits and optionally lifetime credits."""
        try:
            player = await self.player_repo.get_by_id(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            if new_credits < 0:
                raise ValueError("Credits cannot be negative")

            # Update lifetime credits if this is an increase
            if update_lifetime and new_credits > player.credits:
                credit_increase = new_credits - player.credits
                player.lifetime_credits += credit_increase

            player.credits = new_credits
            player.new_credits = new_credits  # backward-compat alias (not a DB column)
            await db.commit()
            await db.refresh(player)

            flogger.debug(f"Updated credits for player {player_id}: {new_credits}")
            return player

        except Exception as e:
            flogger.error(f"Error updating credits for player {player_id}: {e}")
            raise

    async def update_player_xp(self, db: AsyncSession, player_id: int, xp: int) -> Player:
        """Update player XP. Tier is NOT auto-advanced; use promote_player() to advance tier."""
        try:
            player = await self.player_repo.get_by_id(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            if xp < 0:
                xp = 0  # Clamp to 0
            elif xp > 1000000:
                xp = 1000000  # Clamp to max

            player.xp = xp

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

    async def get_promotion_status(self, db: AsyncSession, player_id: int) -> dict:
        """Get promotion eligibility status for a player."""
        try:
            player = await self.player_repo.get_by_id(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            config = await self.config_repo.get_by_guild_id(db, player.guild_id)
            thresholds = config.xp_thresholds if config else {"Silver": 1000, "Gold": 5000, "Platinum": 15000}

            current_level = _TIER_ORDER.get(player.tier, 1)
            eligible_tier = self._calculate_tier_from_xp(player.xp, thresholds)
            eligible_level = _TIER_ORDER.get(eligible_tier, 1)

            next_level = current_level + 1
            next_tier = _TIER_NAMES.get(next_level)  # None if at Platinum

            can_promote = next_tier is not None and eligible_level >= next_level

            xp_threshold = thresholds.get(next_tier) if next_tier else None
            xp_surplus = (player.xp - xp_threshold) if (can_promote and xp_threshold is not None) else None

            return {
                "player_id": player.id,
                "current_tier": player.tier,
                "current_tier_level": current_level,
                "eligible_tier": eligible_tier,
                "next_tier": next_tier,
                "can_promote": can_promote,
                "xp": player.xp,
                "xp_threshold_for_next": xp_threshold,
                "xp_surplus_for_next": xp_surplus,
            }

        except Exception as e:
            flogger.error(f"Error getting promotion status for player {player_id}: {e}")
            raise

    async def promote_player(self, db: AsyncSession, player_id: int) -> dict:
        """Promote a player to the next tier if eligible."""
        try:
            player = await self.player_repo.get_by_id(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            current_level = _TIER_ORDER.get(player.tier, 1)
            if current_level >= 4:  # Platinum
                raise ValueError("Already at maximum tier (Platinum)")

            config = await self.config_repo.get_by_guild_id(db, player.guild_id)
            thresholds = config.xp_thresholds if config else {"Silver": 1000, "Gold": 5000, "Platinum": 15000}

            eligible_tier = self._calculate_tier_from_xp(player.xp, thresholds)
            eligible_level = _TIER_ORDER.get(eligible_tier, 1)

            next_level = current_level + 1
            next_tier = _TIER_NAMES[next_level]

            if eligible_level < next_level:
                threshold = thresholds.get(next_tier, 0)
                raise ValueError(
                    f"Not eligible for promotion. Need {threshold:,} XP for {next_tier}, currently have {player.xp:,}"
                )

            old_tier = player.tier
            player.tier = next_tier
            await db.commit()
            await db.refresh(player)

            flogger.info(f"Player {player_id} promoted from {old_tier} to {next_tier}")

            # Check if eligible for further promotion
            further_level = next_level + 1
            further_tier = _TIER_NAMES.get(further_level)
            eligible_for_next = further_tier is not None and eligible_level >= further_level

            return {
                "player_id": player.id,
                "old_tier": old_tier,
                "new_tier": next_tier,
                "xp": player.xp,
                "eligible_for_next": eligible_for_next,
                "next_tier": further_tier,
            }

        except Exception as e:
            flogger.error(f"Error promoting player {player_id}: {e}")
            raise

    async def prestige_player(self, db: AsyncSession, player_id: int) -> dict:
        """Prestige a player — reset progress, increment prestige counter.

        Requirements:
        - Player must be level 10 (max level) to prestige
        - Resets: xp, xp_surplus, credits, tier, inventory
        - Preserves: lifetime_credits, ships, prestige_count, duel stats, bounty stats
        - Kaamo storage preservation: not yet implemented (future feature)

        Returns dict with:
        - player_id: int
        - prestige_count: int (new count after increment)
        - level_before: int
        - division_before: str
        """
        try:
            player = await self.player_repo.get_by_id(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            # Check level, not tier
            current_level = calculate_user_level(player.xp)
            if current_level < 10:
                raise ValueError(f"Player must be level 10 to prestige (current level: {current_level})")

            # Record state before prestige
            level_before = current_level
            division_before = DivisionService.get_division_for_level(level_before)

            # Reset progression
            player.xp = 0
            player.xp_surplus = 0
            player.credits = 0  # Reset credits (legacy gives 0 starting credits on prestige)
            player.tier = "Bronze"
            player.prestige_count += 1
            # Note: lifetime_credits, ships, duel stats, bounty stats are preserved

            # Clear inventory (preserving Kaamo storage in future)
            # For now: clear all non-ship inventory items
            # TODO: When Kaamo storage is implemented, preserve those items
            from persist.repositories.inventory_repository import InventoryRepository

            inventory_repo = InventoryRepository()
            await inventory_repo.clear_player_inventory(db, player_id)

            await db.commit()
            await db.refresh(player)

            flogger.info(f"Player {player_id} prestiged (count: {player.prestige_count})")

            return {
                "player_id": player_id,
                "prestige_count": player.prestige_count,
                "level_before": level_before,
                "division_before": division_before,
            }

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
                "bounty_stats": {"systems_checked": player.systems_checked, "bounty_wins": player.bounty_wins},
                "duel_stats": {
                    "wins": player.duel_wins,
                    "losses": player.duel_losses,
                    "win_rate": round(duel_win_rate, 2),
                    "credits_won": player.duel_credits_won,
                    "credits_lost": player.duel_credits_lost,
                    "net_credits": net_duel_credits,
                },
                "created_at": player.created_at.isoformat(),
                "updated_at": player.updated_at.isoformat(),
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

    async def transfer_credits(
        self,
        db: AsyncSession,
        source_player_id: int,
        target_player_id: int,
        amount: int,
    ) -> dict[str, Any]:
        """Transfer credits from one player to another.

        Uses SELECT … FOR UPDATE to lock both player rows within a single
        transaction, preventing TOCTOU race conditions where two concurrent
        transfers could read the same balance.

        Args:
            db: Database session
            source_player_id: Player sending credits
            target_player_id: Player receiving credits
            amount: Number of credits to transfer (must be >= 1)

        Returns:
            Dict with transfer details

        Raises:
            ValueError: If validation fails
        """
        # Validate amount >= 1
        if amount < 1:
            raise ValueError("Transfer amount must be at least 1 credit")

        # Validate source != target
        if source_player_id == target_player_id:
            raise ValueError("Cannot transfer credits to yourself")

        # Transaction is owned by the caller (router).
        # Lock both rows to prevent concurrent modifications.
        # Always lock in consistent ID order to prevent deadlocks.
        ids_ordered = sorted([source_player_id, target_player_id])
        locked = {}
        for pid in ids_ordered:
            player = await self.player_repo.get_by_id_for_update(db, pid)
            if not player:
                raise ValueError(f"Player {pid} not found")
            locked[pid] = player

        source = locked[source_player_id]
        target = locked[target_player_id]

        # Check source has enough credits (under lock — no TOCTOU)
        if source.credits < amount:
            raise ValueError(f"Insufficient credits: have {source.credits}, need {amount}")

        source_new = source.credits - amount
        target_new = target.credits + amount
        await self.player_repo.update_credits(db, source_player_id, source_new, commit=False)
        await self.player_repo.update_credits(db, target_player_id, target_new, commit=False)

        flogger.info(f"Transferred {amount} credits from player {source_player_id} to player {target_player_id}")

        return {
            "source_player_id": source_player_id,
            "target_player_id": target_player_id,
            "amount": amount,
            "source_remaining_credits": source_new,
            "target_new_credits": target_new,
        }

    async def add_xp(self, db: AsyncSession, player_id: int, xp_amount: int) -> dict:
        """Add XP to a player and handle level-up detection.

        Returns dict with:
        - player_id: int
        - xp_added: int
        - xp_total: int
        - level_before: int
        - level_after: int
        - leveled_up: bool
        - division_before: str
        - division_after: str
        - division_changed: bool
        """
        try:
            player = await self.player_repo.get_by_id(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            level_before = calculate_user_level(player.xp)
            division_before = DivisionService.get_division_for_level(level_before)

            player.xp += xp_amount

            level_after = calculate_user_level(player.xp)
            leveled_up = level_after > level_before

            # Always update xp_surplus so it stays fresh (not just on level-up)
            boundaries = GameConstants.XP_LEVEL_BOUNDARIES
            idx = min(level_after, len(boundaries) - 1)
            player.xp_surplus = player.xp - boundaries[idx]

            if leveled_up:
                flogger.info(
                    f"Player {player_id} leveled up: {level_before} -> {level_after} (surplus: {player.xp_surplus})"
                )

            division_after = DivisionService.get_division_for_level(level_after)
            division_changed = division_after != division_before

            await db.commit()
            await db.refresh(player)

            flogger.debug(
                f"Added {xp_amount} XP to player {player_id}: total={player.xp}, level={level_before}->{level_after}"
            )

            return {
                "player_id": player_id,
                "xp_added": xp_amount,
                "xp_total": player.xp,
                "level_before": level_before,
                "level_after": level_after,
                "leveled_up": leveled_up,
                "division_before": division_before,
                "division_after": division_after,
                "division_changed": division_changed,
            }

        except Exception as e:
            flogger.error(f"Error adding XP for player {player_id}: {e}")
            raise

    @staticmethod
    def get_level(xp: int) -> int:
        """Return player level (0-10) from XP.

        Thin wrapper around :func:`~services.game_maths.calculate_user_level`.

        Args:
            xp: Player's accumulated XP.

        Returns:
            Level in the range [0, 10].
        """
        return calculate_user_level(xp)

    @staticmethod
    def check_level_up(xp_before: int, xp_after: int) -> dict:
        """Check if an XP change caused a level-up.

        Returns dict with:
        - level_before: int
        - level_after: int
        - leveled_up: bool
        - division_before: str
        - division_after: str
        - division_changed: bool
        """
        level_before = calculate_user_level(xp_before)
        level_after = calculate_user_level(xp_after)
        division_before = DivisionService.get_division_for_level(level_before)
        division_after = DivisionService.get_division_for_level(level_after)

        return {
            "level_before": level_before,
            "level_after": level_after,
            "leveled_up": level_after > level_before,
            "division_before": division_before,
            "division_after": division_after,
            "division_changed": division_after != division_before,
        }
