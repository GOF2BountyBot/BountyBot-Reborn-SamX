"""
Player Service for the BountyBot inventory system.

Handles business logic for player management including creation,
progression, and guild-isolated operations.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from persist.models.player import Player
from persist.models.user import User
from persist.repositories.config_repository import ConfigRepository
from persist.repositories.player_repository import PlayerRepository
from persist.repositories.user_repository import UserRepository
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

from services.exceptions import GuildNotConfiguredError
from services.game_constants import GameConstants, resolve_constant

flogger = bblogger.get_logger("player-service")

# Tier ordering constants
_TIER_ORDER = {"Bronze": 1, "Silver": 2, "Gold": 3, "Platinum": 4}
_TIER_NAMES = {1: "Bronze", 2: "Silver", 3: "Gold", 4: "Platinum"}

# Default prestige XP threshold used when a guild's xp_thresholds JSON has no
# explicit "Prestige" key (B.48: backward-compat for guilds configured before
# the prestige threshold was made user-configurable).
_DEFAULT_PRESTIGE_XP_THRESHOLD = 50000


class TierChangeCooldownError(ValueError):
    """Raised when a player attempts /promote, /demote, or /prestige while their
    tier-change cooldown is still active.

    Subclasses ``ValueError`` for backward-compatibility with existing router
    error handling (which converts ValueError to HTTP 400), but carries a
    ``cooldown_end`` attribute (timezone-aware datetime) so dedicated handlers
    can convert to HTTP 429 with a structured response.
    """

    def __init__(self, message: str, cooldown_end: datetime):
        super().__init__(message)
        self.cooldown_end = cooldown_end


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
        discord_username: str | None = None,
        display_name: str | None = None,
    ) -> Player:
        """
        Get existing player or create new one with starter loadout.

        This is the main entry point for player management when a user
        first interacts with the bot in a guild.

        Package G B.19 / B.34: this method is a transaction-PARTICIPANT — all
        repo calls use ``commit=False``. The route caller MUST wrap in
        ``async with db.begin():`` for atomic creation across the full
        users/players/player_ships/player_inventories cluster.

        B.62: accepts ``display_name`` (Discord per-guild display name).
        When provided, it is written to the player row on every call so
        the stored value stays fresh even for existing players.

        Raises GuildNotConfiguredError if no guild_configs row exists
        (i.e. /admin_setup has not been run for this guild).
        """
        try:
            # Check if player already exists for this guild (before config check to avoid
            # penalising guilds where a player was created before this guard was added).
            existing_player = await self.player_repo.get_by_user_and_guild(db, discord_id, guild_id)
            if existing_player:
                flogger.debug(f"Found existing player {existing_player.id} for user {discord_id} in guild {guild_id}")
                # B.62: refresh display_name on every interaction when provided
                if display_name is not None:
                    existing_player.display_name = display_name
                # B.62: also keep the User.display_name fresh
                if display_name is not None or discord_username is not None:
                    await self.user_repo.get_or_create_user(
                        db, discord_id, discord_username, display_name, commit=False
                    )
                return existing_player

            # New player path — guild must have a config row first.
            config = await self.config_repo.get_by_guild_id(db, guild_id)
            if not config:
                flogger.warning(f"Cannot create player for user {discord_id} in guild {guild_id}: guild not configured")
                raise GuildNotConfiguredError(guild_id)

            # Ensure user exists (commit=False — caller's transaction owns the commit).
            # B.62: pass display_name so the User record is also kept up to date.
            user = await self.user_repo.get_or_create_user(db, discord_id, discord_username, display_name, commit=False)

            # Create new player with default configuration
            player = await self._create_new_player(db, user, guild_id)

            # B.62: set display_name on newly created player
            if display_name is not None:
                player.display_name = display_name

            flogger.info(f"Created new player {player.id} for user {discord_id} in guild {guild_id}")

            return player

        except GuildNotConfiguredError:
            raise
        except Exception as e:
            flogger.error(f"Error getting/creating player for user {discord_id} in guild {guild_id}: {e}")
            raise

    async def _create_new_player(self, db: AsyncSession, user: User, guild_id: int) -> Player:
        """Create a new player with default configuration and starter loadout.

        Package G B.19 / B.34 fix: this method is a transaction-PARTICIPANT.
        All repo calls use ``commit=False``. The caller (route) MUST wrap in
        ``async with db.begin():`` to commit the unit of work atomically. The
        I3 invariant guarantees players, player_ships, player_inventories, and
        active_ship_id all persist together or roll back together.
        """
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

            player = await self.player_repo.add(db, player, commit=False)

            # Create starter loadout (also commit=False end-to-end).
            await self._create_starter_loadout(db, player)

            return player

        except Exception as e:
            flogger.error(f"Error creating new player: {e}")
            raise

    async def _create_starter_loadout(self, db: AsyncSession, player: Player) -> None:
        """Create the starter ship and equipment for a new player.

        Package G (B.19) refactor: starter items now flow through the
        :class:`~services.loadout_consistency_service.LoadoutConsistencyService`
        choke-point so that every JSON slot reference is backed by an
        inventory-row decrement (invariant I2 — no materialisation from
        nothing).

        Net DB state — identical to the pre-fix intended state: Betty has
        ``weapons=["Nirai Impulse EX 1"]``, ``modules=["E2 Exoclad", "Telta
        Quickscan"]``; inventory has 1 row for ``Micro Gun MK I``.  The
        difference is provenance: every JSON entry was placed by
        ``equip_one`` after a corresponding inventory decrement.
        """
        try:
            from persist.repositories.inventory_repository import InventoryRepository
            from persist.repositories.player_ship_repository import PlayerShipRepository

            from services.loadout_consistency_service import LoadoutConsistencyService

            player_ship_repo = PlayerShipRepository()
            inv_repo = InventoryRepository()
            consistency = LoadoutConsistencyService()

            # 1. Create the PlayerShip row for Betty with EMPTY slot lists.
            starter_ship_data = {
                "player_id": player.id,
                "ship_name": "Betty",
                "is_active": True,
                "weapons": [],
                "modules": [],
                "turrets": [],
                "secondary_weapons": [],
            }
            starter_ship = await player_ship_repo.create_or_update(db, starter_ship_data, commit=False)

            # 2. Update player's active ship reference (PlayerShip.id, not Ship.id)
            await self.player_repo.update_active_ship(db, player.id, starter_ship.id, commit=False)

            # 3. Add all four starter items to inventory (concrete types).
            await inv_repo.add_item(db, player.id, "primary_weapon", "Nirai Impulse EX 1", quantity=1, commit=False)
            await inv_repo.add_item(db, player.id, "module", "E2 Exoclad", quantity=1, commit=False)
            await inv_repo.add_item(db, player.id, "module", "Telta Quickscan", quantity=1, commit=False)
            await inv_repo.add_item(db, player.id, "primary_weapon", "Micro Gun MK I", quantity=1, commit=False)

            # 4. Equip the three items that should start fitted on Betty.
            #    Each call decrements its inventory row and appends to the ship's
            #    slot list — preserving I2 by construction.
            await consistency.equip_one(
                db, player_id=player.id, ship_id=starter_ship.id, item_name="Nirai Impulse EX 1"
            )
            await consistency.equip_one(db, player_id=player.id, ship_id=starter_ship.id, item_name="E2 Exoclad")
            await consistency.equip_one(db, player_id=player.id, ship_id=starter_ship.id, item_name="Telta Quickscan")
            # Micro Gun MK I stays in cargo — Betty has only 1 primary slot.

            flogger.info("Created starter loadout for player %s", player.id)

        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error("Error creating starter loadout for player %s: %s", player.id, e)
            raise

    async def update_player_credits(
        self, db: AsyncSession, player_id: int, new_credits: int, update_lifetime: bool = True
    ) -> Player:
        """Update player credits and optionally lifetime credits."""
        try:
            # D5-T2: lock the aggregate-root Player row FIRST (FOR UPDATE) before the
            # credits/lifetime_credits read-modify-write.  PUT /players/{id}/credits
            # and the admin credits route reach this method as naked entry points
            # with no outer lock, so without this lock two concurrent admin credit
            # sets on the same player could interleave and lose an update (e.g. the
            # lifetime_credits accumulation, computed from the pre-read balance).
            # This method owns its own transaction (it commits below), so the lock
            # is held by the session autobegin until that commit — no router
            # db.begin() is required (and would conflict with the internal commit).
            player = await self.player_repo.get_by_id_for_update(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            if new_credits < 0:
                raise ValueError("Credits cannot be negative")

            # Update lifetime credits if this is an increase
            if update_lifetime and new_credits > player.credits:
                credit_increase = new_credits - player.credits
                player.lifetime_credits += credit_increase

            old_credits = player.credits
            player.credits = new_credits
            player.new_credits = new_credits  # backward-compat alias (not a DB column)

            # Auto-cancel any pending duels the player can no longer cover on
            # a credit decrease (admin set, etc.). Note: this method commits
            # internally; cancel_underfunded_duels runs commit=False so the
            # cancel and the credit update are captured in the same commit below.
            # Non-fatal: a failure here must never block a legitimate credit update.
            if new_credits < old_credits:
                try:
                    from services.duel_service import DuelService  # deferred to avoid circular import

                    await DuelService().cancel_underfunded_duels(db, player_id, commit=False)
                except Exception as _duel_exc:  # pylint: disable=broad-exception-caught
                    flogger.warning(
                        f"cancel_underfunded_duels failed after update_player_credits "
                        f"for player_id={player_id}: {_duel_exc}"
                    )

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
            # D5-T2: lock the aggregate-root Player row FIRST (FOR UPDATE) before the
            # XP write.  PUT /players/{id}/xp and the admin xp route are naked entry
            # points (no outer lock); the lock serialises concurrent same-player XP
            # sets so neither clobbers the other.  This method owns its transaction
            # (commits below); the lock is held by autobegin until that commit.
            player = await self.player_repo.get_by_id_for_update(db, player_id)
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
        """Get promotion eligibility status for a player.

        Includes an early cooldown advisory so callers can surface the cooldown
        error *before* showing a confirmation dialog — avoids the UX anti-pattern
        of "click Confirm, then get a 429".  ``on_cooldown`` and
        ``cooldown_ends_at`` are informational only; the authoritative cooldown
        enforcement still happens inside ``promote_player`` / ``demote_player``.
        """
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

            # Cooldown advisory — non-raising check so the caller gets structured
            # info rather than an exception at the status-check stage.
            on_cooldown = False
            cooldown_ends_at: str | None = None
            try:
                self._check_tier_change_cooldown(player)
            except TierChangeCooldownError as e:
                on_cooldown = True
                cooldown_ends_at = e.cooldown_end.isoformat()

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
                "on_cooldown": on_cooldown,
                "cooldown_ends_at": cooldown_ends_at,
            }

        except Exception as e:
            flogger.error(f"Error getting promotion status for player {player_id}: {e}")
            raise

    def _check_tier_change_cooldown(self, player: Player) -> None:
        """Raise TierChangeCooldownError if the player's tier-change cooldown is active."""
        end = player.tier_change_cooldown_end
        if end is None:
            return
        now = datetime.now(UTC)
        if end <= now:
            return
        remaining = int((end - now).total_seconds())
        raise TierChangeCooldownError(
            f"Tier change is on cooldown for {remaining}s (ends {end.isoformat()})",
            cooldown_end=end,
        )

    def _apply_tier_change_cooldown(self, player: Player, config) -> None:
        """Set player.tier_change_cooldown_end = now + tier_change_cooldown seconds.

        Reads the cooldown duration via resolve_constant (per-guild override falls
        back to GameConstants.TIER_CHANGE_COOLDOWN). Caller is responsible for the
        commit; this method only mutates the player instance.
        """
        cooldown_seconds = resolve_constant(config, "tier_change_cooldown", GameConstants.TIER_CHANGE_COOLDOWN)
        player.tier_change_cooldown_end = datetime.now(UTC) + timedelta(seconds=cooldown_seconds)

    async def _scrub_orphaned_checks_after_tier_change(
        self,
        db: AsyncSession,
        player_id: int,
        guild_id: int,
        new_tier: str,
    ) -> int:
        """Delegate to BountyService.scrub_player_checks_outside_tier.

        Imported locally to avoid a circular import between bounty_service and
        player_service (BountyService imports nothing from PlayerService, but
        a top-level import here would still introduce a load-order edge case
        through their shared repositories).
        """
        from services.bounty_service import BountyService

        bounty_service = BountyService()
        return await bounty_service.scrub_player_checks_outside_tier(
            db, player_id=player_id, guild_id=guild_id, new_tier=new_tier
        )

    async def promote_player(self, db: AsyncSession, player_id: int) -> dict:
        """Promote a player to the next tier if eligible.

        Enforces the per-guild tier-change cooldown (24h default) and forfeits
        the player's check entries on bounties they can no longer reach via the
        FORFEITED_CHECK sentinel (see BountyService.scrub_player_checks_outside_tier).
        """
        try:
            # D5-T2: lock the aggregate-root Player row FIRST (FOR UPDATE) before the
            # tier/cooldown read-modify-write.  PUT /players/{id}/promote is a naked
            # entry point; the lock serialises a concurrent promote/demote on the
            # same player so the cooldown check-then-set and tier change cannot race
            # (e.g. a double promotion past the intended tier).  This method owns its
            # transaction (commits below); autobegin holds the lock until commit.
            player = await self.player_repo.get_by_id_for_update(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            self._check_tier_change_cooldown(player)

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
            self._apply_tier_change_cooldown(player, config)
            scrubbed = await self._scrub_orphaned_checks_after_tier_change(
                db, player_id=player_id, guild_id=player.guild_id, new_tier=next_tier
            )
            await db.commit()
            await db.refresh(player)

            flogger.info(
                f"Player {player_id} promoted from {old_tier} to {next_tier} (scrubbed {scrubbed} cross-tier bounties)"
            )

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

    async def demote_player(self, db: AsyncSession, player_id: int) -> dict:
        """Demote a player to the previous tier.

        Mirrors ``promote_player`` for direction: enforces the tier-change cooldown,
        sets a fresh cooldown on success, and forfeits the player's checks on any
        bounty outside the new (lower) tier via the FORFEITED_CHECK sentinel.

        Demotion is unconditional on XP — a player can choose to drop a tier even
        when they meet the next-tier threshold (this is a deliberate gameplay
        choice, not an automatic effect of losing XP). Already at Bronze raises
        ValueError.
        """
        try:
            # D5-T2: lock the aggregate-root Player row FIRST (FOR UPDATE) before the
            # tier/cooldown/credit-penalty read-modify-write.  PUT /players/{id}/demote
            # is a naked entry point; the lock serialises a concurrent promote/demote
            # (and the demotion credit penalty, an RMW on credits) on the same player.
            # This method owns its transaction (commits below); autobegin holds the
            # lock until commit.
            player = await self.player_repo.get_by_id_for_update(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            self._check_tier_change_cooldown(player)

            current_level = _TIER_ORDER.get(player.tier, 1)
            if current_level <= 1:  # Bronze
                raise ValueError("Already at minimum tier (Bronze)")

            prev_level = current_level - 1
            prev_tier = _TIER_NAMES[prev_level]

            config = await self.config_repo.get_by_guild_id(db, player.guild_id)

            old_tier = player.tier
            player.tier = prev_tier
            self._apply_tier_change_cooldown(player, config)

            # Apply configurable credit penalty on demotion — clamped to 0 so credits never go negative.
            # Rate is per-guild (demotion_credit_penalty_pct on GuildConfig, 0-100).
            # NULL → falls back to GameConstants.DEMOTION_CREDIT_PENALTY_PCT (default 10%).
            penalty_pct = resolve_constant(
                config, "demotion_credit_penalty_pct", GameConstants.DEMOTION_CREDIT_PENALTY_PCT
            )
            penalty = int(player.credits * penalty_pct / 100)
            player.credits = max(0, player.credits - penalty)

            # Auto-cancel any pending duels the player can no longer cover after
            # the demotion credit penalty. Non-fatal: must never block demotion.
            if penalty > 0:
                try:
                    from services.duel_service import DuelService  # deferred to avoid circular import

                    await DuelService().cancel_underfunded_duels(db, player_id, commit=False)
                except Exception as _duel_exc:  # pylint: disable=broad-exception-caught
                    flogger.warning(
                        f"cancel_underfunded_duels failed after demote_player for player_id={player_id}: {_duel_exc}"
                    )

            scrubbed = await self._scrub_orphaned_checks_after_tier_change(
                db, player_id=player_id, guild_id=player.guild_id, new_tier=prev_tier
            )
            await db.commit()
            await db.refresh(player)

            flogger.info(
                f"Player {player_id} demoted from {old_tier} to {prev_tier} "
                f"(scrubbed {scrubbed} cross-tier bounties, penalty={penalty})"
            )

            return {
                "player_id": player.id,
                "old_tier": old_tier,
                "new_tier": prev_tier,
                "xp": player.xp,
                "penalty": penalty,
            }

        except Exception as e:
            flogger.error(f"Error demoting player {player_id}: {e}")
            raise

    def _get_prestige_threshold(self, thresholds: dict[str, int] | None) -> int:
        """Resolve the XP required to prestige from a guild's xp_thresholds JSON.

        Falls back to ``_DEFAULT_PRESTIGE_XP_THRESHOLD`` if the JSON is missing
        or has no ``"Prestige"`` key (backward-compat for guilds configured
        before B.48 added the per-guild Prestige threshold).
        """
        if not thresholds:
            return _DEFAULT_PRESTIGE_XP_THRESHOLD
        return int(thresholds.get("Prestige", _DEFAULT_PRESTIGE_XP_THRESHOLD))

    async def prestige_player(self, db: AsyncSession, player_id: int) -> dict:
        """Prestige a player — full reset to first-time-registration starter state.

        B.48: prestige is gated on the guild's configurable Prestige XP
        threshold (``xp_thresholds["Prestige"]``) rather than a hardcoded
        level==10 / 1,000,000 XP boundary. The legacy level/division system
        was removed entirely in B.48.

        B.49 (supersedes earlier B.48 behaviour): a successful prestige now
        resets the player to the EXACT starter Betty state produced by
        first-time ``/register``. Previously prestige preserved ship hulls
        while clearing loadouts, leaving prestige players with arbitrarily
        large fleets. The new contract: after prestige, the player owns
        exactly one ship (Betty, active) with the canonical starter loadout
        and the canonical starter inventory — identical, byte for byte, to a
        brand-new player.

        Requirements:
        - Player must have ``xp >= xp_thresholds["Prestige"]`` (default 50,000
          when key absent) to prestige.
        - Resets: xp=0, xp_surplus=0, credits=0, tier=Bronze; deletes ALL
          owned ships (not just loadouts) and the entire player_inventory;
          recreates Betty + starter loadout + starter inventory via
          :meth:`_create_starter_loadout`.
        - Preserves: lifetime_credits, prestige_count (incremented), duel
          stats, bounty stats. Kaamo storage handling out of scope here.

        The starter loadout is recreated through the same code path as
        ``/register`` (and therefore through the
        :class:`~services.loadout_consistency_service.LoadoutConsistencyService`
        choke-point), guaranteeing invariants I1 (no duplication) and I2
        (no materialisation from nothing).

        The caller (router) MUST wrap in ``async with db.begin()`` for
        atomicity; the service flushes but never commits.

        Returns dict with:
        - player_id: int
        - prestige_count: int (new count after increment)
        - tier_before: str (e.g. "Platinum")
        - xp_before: int
        """
        try:
            player = await self.player_repo.get_by_id(db, player_id)
            if not player:
                raise ValueError(f"Player {player_id} not found")

            # B.48: gate on configurable per-guild Prestige XP threshold.
            config = await self.config_repo.get_by_guild_id(db, player.guild_id)
            thresholds = config.xp_thresholds if config else None
            prestige_threshold = self._get_prestige_threshold(thresholds)

            if player.xp < prestige_threshold:
                raise ValueError(
                    f"Not eligible for prestige. Need {prestige_threshold:,} XP to prestige, "
                    f"currently have {player.xp:,}"
                )

            # Enforce tier-change cooldown (prestige is a tier transition).
            self._check_tier_change_cooldown(player)

            # Record state before prestige
            tier_before = player.tier
            xp_before = player.xp

            # Reset progression columns. Note: lifetime_credits, duel stats,
            # bounty stats are preserved by deliberately not touching them.
            player.xp = 0
            player.xp_surplus = 0
            player.credits = 0
            player.tier = "Bronze"
            player.prestige_count += 1
            self._apply_tier_change_cooldown(player, config)

            # Auto-cancel ALL pending duels — credits just reset to 0 so every
            # pending duel with stakes > 0 is now unbacked.
            # Non-fatal: must never block prestige.
            try:
                from services.duel_service import DuelService  # deferred to avoid circular import

                await DuelService().cancel_underfunded_duels(db, player_id, commit=False)
            except Exception as _duel_exc:  # pylint: disable=broad-exception-caught
                flogger.warning(
                    f"cancel_underfunded_duels failed after prestige_player for player_id={player_id}: {_duel_exc}"
                )

            # Forfeit checks on bounties outside the new (Bronze) tier.
            await self._scrub_orphaned_checks_after_tier_change(
                db, player_id=player_id, guild_id=player.guild_id, new_tier="Bronze"
            )

            # B.49: full reset to starter Betty state — delete every existing
            # ship and the entire inventory, then recreate via the canonical
            # starter-loadout path used by /register.
            from persist.repositories.inventory_repository import InventoryRepository
            from persist.repositories.player_ship_repository import PlayerShipRepository

            inventory_repo = InventoryRepository()
            player_ship_repo = PlayerShipRepository()

            # 1. Break the active_ship_id FK before deleting PlayerShip rows
            #    so the row delete doesn't violate the FK constraint.
            await self.player_repo.update_active_ship(db, player_id, None, commit=False)

            # 2. Delete every PlayerShip row for this player. This also drops
            #    every loadout JSON column, since the rows are gone.
            existing_ships = await player_ship_repo.get_player_ships(db, player_id)
            for ship in existing_ships:
                await player_ship_repo.remove(db, ship, commit=False)

            # 3. Wipe all inventory rows.
            await inventory_repo.clear_player_inventory(db, player_id, commit=False)

            # 4. Flush so the deletes are visible before the starter-loadout
            #    INSERTs (avoids identity-map conflicts on Betty re-insert).
            await db.flush()

            # 5. Recreate the starter Betty + loadout via the same code path
            #    a brand-new player would take. _create_starter_loadout sets
            #    Betty active and seeds inventory + equips through the
            #    LoadoutConsistencyService choke-point.
            await self._create_starter_loadout(db, player)

            await db.flush()

            flogger.info(
                f"Player {player_id} prestiged to starter Betty state "
                f"(count: {player.prestige_count}, tier_before: {tier_before}, xp_before: {xp_before})"
            )

            return {
                "player_id": player_id,
                "prestige_count": player.prestige_count,
                "tier_before": tier_before,
                "xp_before": xp_before,
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

    async def get_players_by_tier(
        self,
        db: AsyncSession,
        guild_id: int,
        tier: str,
        active_within_days: int | None = None,
    ) -> list[Player]:
        """Get all players in a guild with a specific tier.

        Args:
            db: Database session.
            guild_id: Guild to filter by.
            tier: Tier name to filter by (e.g. "Bronze", "Silver").
            active_within_days: When set and > 0, restricts to players active
                within this many days. Passed through to the repo. ``0`` means
                no filter (same as ``None``).
        """
        try:
            players = await self.player_repo.get_players_by_guild(db, guild_id, active_within_days=active_within_days)
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
        # Wrap repo calls so DB/ORM exceptions surface as friendly ValueError
        # (maps to HTTP 400) rather than leaking as raw 500s.
        ids_ordered = sorted([source_player_id, target_player_id])
        locked = {}
        for pid in ids_ordered:
            try:
                player = await self.player_repo.get_by_id_for_update(db, pid)
            except Exception as exc:
                flogger.error(f"DB error fetching player_id={pid} for transfer: {exc}", exc_info=True)
                raise ValueError(f"Player with ID {pid} could not be retrieved.") from exc
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

        # Auto-cancel any pending duels the SOURCE can no longer cover after
        # the transfer. Target is gaining credits — no cancellation needed.
        # Non-fatal: a failure here must never block a legitimate credit transfer.
        try:
            from services.duel_service import DuelService  # deferred to avoid circular import

            await DuelService().cancel_underfunded_duels(db, source_player_id, commit=False)
        except Exception as _duel_exc:  # pylint: disable=broad-exception-caught
            flogger.warning(
                f"cancel_underfunded_duels failed after transfer_credits "
                f"for source_player_id={source_player_id}: {_duel_exc}"
            )

        flogger.info(f"Transferred {amount} credits from player {source_player_id} to player {target_player_id}")

        return {
            "source_player_id": source_player_id,
            "target_player_id": target_player_id,
            "amount": amount,
            "source_remaining_credits": source_new,
            "target_new_credits": target_new,
        }

    # B.48: deleted vestigial level/division helpers — `add_xp`, `get_level`,
    # `check_level_up`. The level/division system was wholly internal and
    # replaced by the per-guild tier-threshold system.
