"""
Bounty Service for the BountyBot system.

Handles business logic for bounty generation including:
- Criminal selection (faction-aware, division-filtered)
- Ship and equipment loadout generation via bidirectional TL search
- Tech-level appropriate gear assignment with damage-weapon preference
- Full bounty spawning via A* pathfinding route generation
- Bounty checking mechanic with cooldown and proximity hint support
"""

import enum
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from persist.models.bounty import Bounty
from persist.models.criminal import Criminal
from persist.repositories.bounty_repository import BountyRepository
from persist.repositories.criminal_repository import CriminalRepository
from persist.repositories.item_repository import ItemRepository
from persist.repositories.player_repository import PlayerRepository
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

from services.game_constants import GameConstants
from services.game_maths import (
    calculate_user_level,
    pick_random_item_tl,
    reward_per_sys_check,
    ship_tech_level_for_value,
)
from services.pathfinding_service import PathfindingService
from services.system_graph_service import SystemGraphService

flogger = bblogger.get_logger("bounty-service")


class CheckResult(enum.Enum):
    """Result codes for the bounty check mechanic."""

    NOT_FOUND = "not_found"
    ALREADY_CHECKED = "already_checked"
    INCORRECT = "incorrect"
    CORRECT = "correct"
    ON_COOLDOWN = "on_cooldown"


@dataclass
class CheckResponse:
    """Response object returned by :meth:`BountyService.check_bounty`."""

    result: CheckResult
    bounty_id: int | None = None
    message: str = ""
    proximity_hint: bool = False
    distance_to_answer: int | None = None


@dataclass
class RewardInfo:
    """Reward info for a single player."""

    player_id: int
    credits_earned: int
    xp_earned: int
    is_winner: bool = False
    systems_checked_count: int = 0
    level_before: int = 0
    level_after: int = 0
    leveled_up: bool = False


class BountyService:
    """Service for bounty generation and criminal selection logic."""

    def __init__(self) -> None:
        self.bounty_repo = BountyRepository()
        self.criminal_repo = CriminalRepository()
        self.item_repo = ItemRepository()
        self.player_repo = PlayerRepository()
        self.graph_service = SystemGraphService()
        self.pathfinding_service = PathfindingService(self.graph_service)

    # ------------------------------------------------------------------
    # Criminal Selection
    # ------------------------------------------------------------------

    async def select_criminal(
        self, db: AsyncSession, guild_id: int, division: str
    ) -> Criminal | None:
        """Select a random criminal not already active in this division.

        Loads all non-player criminals, filters out those already active
        in the given guild+division, then picks a random faction and a
        random criminal from that faction — matching legacy behaviour.

        Args:
            db:        Async database session.
            guild_id:  Discord guild ID.
            division:  Division name (e.g. "bronze", "silver", "gold").

        Returns:
            A Criminal object, or None if no available criminals exist.
        """
        # Load all non-player criminals
        all_criminals: list[Criminal] = await self.criminal_repo.list_all(db)
        available = [c for c in all_criminals if not c.is_player]

        # Get active bounties for this guild+division
        active_bounties = await self.bounty_repo.get_active_by_guild_and_division(
            db, guild_id, division
        )
        active_names = {b.criminal_name for b in active_bounties}

        # Filter out criminals already active in this division
        available = [c for c in available if c.name not in active_names]

        if not available:
            flogger.info(
                f"No available criminals for guild {guild_id} division {division}"
            )
            return None

        # Pick a random faction, then a random criminal from that faction
        factions = list({c.faction for c in available})
        chosen_faction = random.choice(factions)
        faction_criminals = [c for c in available if c.faction == chosen_faction]
        return random.choice(faction_criminals)

    # ------------------------------------------------------------------
    # Tech-Level Search
    # ------------------------------------------------------------------

    async def find_item_tl(
        self,
        db: AsyncSession,
        center: int,
        min_tl: int,
        max_tl: int,
        upper_bound: int,
        item_type: str,
    ) -> int:
        """Bidirectional search for a tech level that has items available.

        Searches downward from *center* to *min_tl* first, then upward
        from *center + 1* to ``min(max_tl, center + upper_bound)``.

        Ships are handled specially because they have no ``tech_level``
        column — we derive their TL from value via
        :func:`ship_tech_level_for_value`.

        Args:
            db:          Async database session.
            center:      Starting (preferred) tech level.
            min_tl:      Minimum tech level to search.
            max_tl:      Maximum tech level to search.
            upper_bound: How many TL levels above *center* to search upward.
            item_type:   Item type string (e.g. ``"ship"``, ``"primary_weapon"``).

        Returns:
            The first tech level that has at least one matching item, or
            ``-1`` if no items are found in the search range.
        """
        if item_type == "ship":
            return await self._find_ship_tl(db, center, min_tl, max_tl, upper_bound)

        # Downward search: center → min_tl
        tl = center
        while tl >= min_tl:
            items = await self.item_repo.get_all_by_tech_level(db, tl, item_type=item_type)
            if items:
                return tl
            tl -= 1

        # Upward search: center+1 → min(max_tl, center + upper_bound)
        if center < max_tl:
            tl = center + 1
            max_search = min(max_tl, center + upper_bound)
            while tl <= max_search:
                items = await self.item_repo.get_all_by_tech_level(
                    db, tl, item_type=item_type
                )
                if items:
                    return tl
                tl += 1

        return -1

    async def _find_ship_tl(
        self,
        db: AsyncSession,
        center: int,
        min_tl: int,
        max_tl: int,
        upper_bound: int,
    ) -> int:
        """Variant of find_item_tl for ships (value-derived TL).

        Ships have no tech_level column; we filter all ships by
        ``ship_tech_level_for_value(ship.value)``.
        """
        from persist.models.ship import Ship
        from sqlalchemy import select

        result = await db.execute(select(Ship))
        all_ships = list(result.scalars().all())

        def ships_at_tl(target_tl: int) -> list:
            return [s for s in all_ships if ship_tech_level_for_value(s.value) == target_tl]

        # Downward search
        tl = center
        while tl >= min_tl:
            if ships_at_tl(tl):
                return tl
            tl -= 1

        # Upward search
        if center < max_tl:
            tl = center + 1
            max_search = min(max_tl, center + upper_bound)
            while tl <= max_search:
                if ships_at_tl(tl):
                    return tl
                tl += 1

        return -1

    # ------------------------------------------------------------------
    # Loadout Generation
    # ------------------------------------------------------------------

    async def generate_loadout(self, db: AsyncSession, tech_level: int) -> dict:
        """Generate a criminal's ship loadout for the given tech level.

        At tech level 0 a fixed beginner loadout (Betty) is returned.
        Otherwise selects a ship at the appropriate tech level and equips
        primary weapons and modules following the legacy behaviour.

        Args:
            db:          Async database session.
            tech_level:  Criminal tech level (0-10).

        Returns:
            Dict containing ship info, equipped weapons, modules, and
            ``total_value``.
        """
        if tech_level == 0:
            return {
                "ship_name": "Betty",
                "ship_value": 0,
                "ship_max_primaries": 0,
                "ship_max_modules": 0,
                "ship_max_turrets": 0,
                "weapons": [],
                "modules": [],
                "turrets": [],
                "total_value": 0,
            }

        # item_tl is one below criminal TL (minimum 1)
        item_tl = max(1, tech_level - 1)

        # ----------------------------------------------------------------
        # Ship selection
        # ----------------------------------------------------------------
        ship_tl = await self.find_item_tl(
            db,
            center=item_tl,
            min_tl=GameConstants.MIN_TECH_LEVEL,
            max_tl=GameConstants.MAX_TECH_LEVEL,
            upper_bound=GameConstants.CRIMINAL_MAX_GEAR_UPGRADE,
            item_type="ship",
        )

        ship = None
        if ship_tl != -1:
            from persist.models.ship import Ship
            from sqlalchemy import select

            result = await db.execute(select(Ship))
            all_ships = list(result.scalars().all())
            matching_ships = [
                s for s in all_ships if ship_tech_level_for_value(s.value) == ship_tl
            ]
            if matching_ships:
                ship = random.choice(matching_ships)

        if ship is None:
            # Fallback: pick any ship
            from persist.models.ship import Ship
            from sqlalchemy import select

            result = await db.execute(select(Ship))
            all_ships = list(result.scalars().all())
            if all_ships:
                ship = random.choice(all_ships)

        if ship is None:
            flogger.warning(f"No ships available for tech_level={tech_level}")
            return {
                "ship_name": "Unknown",
                "ship_value": 0,
                "ship_max_primaries": 0,
                "ship_max_modules": 0,
                "ship_max_turrets": 0,
                "weapons": [],
                "modules": [],
                "turrets": [],
                "total_value": 0,
            }

        # ----------------------------------------------------------------
        # Primary weapon selection
        # ----------------------------------------------------------------
        equipped_weapons = []
        if ship.max_primaries > 0:
            weapon_tl = await self.find_item_tl(
                db,
                center=item_tl,
                min_tl=GameConstants.MIN_TECH_LEVEL,
                max_tl=GameConstants.MAX_TECH_LEVEL,
                upper_bound=GameConstants.CRIMINAL_MAX_GEAR_UPGRADE,
                item_type="primary_weapon",
            )
            if weapon_tl != -1:
                all_weapons = await self.item_repo.get_all_by_tech_level(
                    db, weapon_tl, item_type="primary_weapon"
                )
                damaging = [w for w in all_weapons if w.dps > 0]
                non_damaging = [w for w in all_weapons if w.dps <= 0]

                for _ in range(ship.max_primaries):
                    # 20% chance to pick a non-damaging weapon (if available)
                    pick_non_damaging = (
                        non_damaging
                        and random.randint(1, 100)
                        <= GameConstants.CRIMINAL_EQUIP_DAMAGELESS_WEAPON_CHANCE
                    )
                    pool = non_damaging if pick_non_damaging else (damaging or all_weapons)
                    if pool:
                        equipped_weapons.append(random.choice(pool))

        # ----------------------------------------------------------------
        # Module selection
        # ----------------------------------------------------------------
        equipped_modules = []
        if ship.max_modules > 0:
            # Resolve all modules at item_tl for generic slots
            generic_modules = await self.item_repo.get_all_by_tech_level(
                db, item_tl, item_type="module"
            )

            # Slot 1: armour module guaranteed at TL > 1
            if tech_level > 1 and len(equipped_modules) < ship.max_modules:
                armour_mod = await self._find_typed_module(db, "armour", item_tl)
                if armour_mod:
                    equipped_modules.append(armour_mod)

            # Slot 2: shield module guaranteed at TL > 3
            if tech_level > 3 and len(equipped_modules) < ship.max_modules:
                shield_mod = await self._find_typed_module(db, "shield", item_tl)
                if shield_mod:
                    equipped_modules.append(shield_mod)

            # Fill remaining slots with random modules at item_tl
            while len(equipped_modules) < ship.max_modules and generic_modules:
                equipped_modules.append(random.choice(generic_modules))

        # ----------------------------------------------------------------
        # Calculate total value
        # ----------------------------------------------------------------
        weapon_value = sum(getattr(w, "value", 0) for w in equipped_weapons)
        module_value = sum(getattr(m, "value", 0) for m in equipped_modules)
        total_value = ship.value + weapon_value + module_value

        return {
            "ship_name": ship.name,
            "ship_value": ship.value,
            "ship_max_primaries": ship.max_primaries,
            "ship_max_modules": ship.max_modules,
            "ship_max_turrets": ship.max_turrets,
            "weapons": [
                {"name": w.name, "value": w.value, "dps": w.dps}
                for w in equipped_weapons
            ],
            "modules": [
                {"name": m.name, "value": m.value, "tech_level": m.tech_level}
                for m in equipped_modules
            ],
            "turrets": [],
            "total_value": total_value,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _find_typed_module(
        self, db: AsyncSession, module_keyword: str, item_tl: int
    ):
        """Find the first module whose name contains *module_keyword* (case-insensitive).

        Searches at *item_tl* first, then broadens to all tech levels.

        Args:
            db:             Async database session.
            module_keyword: Substring to match in module name (e.g. ``"armour"``).
            item_tl:        Preferred tech level to search first.

        Returns:
            A matching module object, or None if none found.
        """
        # Search at item_tl first
        modules_at_tl = await self.item_repo.get_all_by_tech_level(
            db, item_tl, item_type="module"
        )
        keyword_lower = module_keyword.lower()
        matches = [m for m in modules_at_tl if keyword_lower in m.name.lower()]
        if matches:
            return random.choice(matches)

        # Broaden search across all TLs
        for tl in range(GameConstants.MIN_TECH_LEVEL, GameConstants.MAX_TECH_LEVEL + 1):
            if tl == item_tl:
                continue
            modules = await self.item_repo.get_all_by_tech_level(
                db, tl, item_type="module"
            )
            matches = [m for m in modules if keyword_lower in m.name.lower()]
            if matches:
                return random.choice(matches)

        return None

    # ------------------------------------------------------------------
    # Bounty Spawning
    # ------------------------------------------------------------------

    async def spawn_bounty(
        self,
        db: AsyncSession,
        guild_id: int,
        division: str,
        tech_level: int | None = None,
    ) -> Bounty | None:
        """Spawn a new bounty for a guild division.

        Orchestrates the full bounty generation:
        1. Select criminal (exclude active ones)
        2. Determine tech level (if not provided, use pick_random_item_tl)
        3. Generate route via A* pathfinding (up to 3 attempts)
        4. Select answer (random system from route)
        5. Generate criminal loadout
        6. Calculate reward
        7. Set timing
        8. Initialize checked dict
        9. Persist to database

        Args:
            db:         Async database session.
            guild_id:   Discord guild ID.
            division:   Division name (e.g. "bronze", "silver", "gold").
            tech_level: Optional override for criminal tech level.

        Returns:
            Bounty object if spawn succeeds, None if it fails.
        """
        # Step 1: Select criminal
        criminal = await self.select_criminal(db, guild_id, division)
        if criminal is None:
            flogger.warning(f"No available criminals for guild={guild_id} div={division}")
            return None

        # Step 2: Determine tech level
        if tech_level is None:
            # Division TL centers: bronze=2, silver=5, gold=8
            division_tl_map = {"bronze": 2, "silver": 5, "gold": 8}
            center_tl = division_tl_map.get(division, 5)
            tech_level = pick_random_item_tl(center_tl)

        # Step 3: Generate route (up to 3 attempts)
        await self.graph_service.load_graph(db)

        jump_gate_systems = self.graph_service.get_systems_with_jump_gates()
        if len(jump_gate_systems) < 2:
            flogger.warning("Not enough systems with jump gates for route generation")
            return None

        route = None
        for attempt in range(3):
            start = random.choice(jump_gate_systems)
            end = random.choice(jump_gate_systems)
            while end == start:
                end = random.choice(jump_gate_systems)

            result = self.pathfinding_service.make_route(start, end)
            if isinstance(result, list):
                route = result
                break
            # If PathfindingError, retry
            flogger.debug(f"Route attempt {attempt + 1} failed: {result}")

        if route is None:
            flogger.warning(f"Failed to generate route after 3 attempts for guild={guild_id}")
            return None

        # Step 4: Select answer
        answer = random.choice(route)

        # Step 5: Generate loadout
        loadout = await self.generate_loadout(db, tech_level)

        # Step 6: Calculate reward
        rps = reward_per_sys_check(tech_level, loadout["total_value"])
        total_reward = rps * len(route)

        # Step 7: Set timing
        issue_time = datetime.now(UTC)
        end_time = issue_time + timedelta(days=len(route))

        # Step 8: Initialize checked dict
        checked = {system: -1 for system in route}

        # Step 9: Create bounty and persist
        bounty = Bounty(
            guild_id=guild_id,
            division=division,
            criminal_name=criminal.name,
            criminal_faction=criminal.faction,
            route=route,
            answer=answer,
            reward=total_reward,
            reward_per_sys=rps,
            checked=checked,
            issue_time=issue_time,
            end_time=end_time,
            tech_level=tech_level,
            criminal_ship=loadout,
            status="active",
        )
        created = await self.bounty_repo.create(db, bounty)
        flogger.info(
            f"Spawned bounty {created.id}: {criminal.name} in {division} for guild {guild_id}"
        )
        return created

    # ------------------------------------------------------------------
    # Bounty Check Mechanic
    # ------------------------------------------------------------------

    async def check_bounty(
        self,
        db: AsyncSession,
        player_id: int,
        system_name: str,
        guild_id: int,
    ) -> CheckResponse:
        """Check a star system against active bounty routes.

        Validates the player's cooldown, identifies which (if any) active
        bounty for the player's division contains *system_name*, records the
        check, and returns a :class:`CheckResponse` indicating the outcome.

        Args:
            db:          Async database session.
            player_id:   ID of the player performing the check.
            system_name: Name of the star system being checked.
            guild_id:    Discord guild ID.

        Returns:
            A :class:`CheckResponse` with the appropriate :class:`CheckResult`.
        """
        # Step 1: Get player
        player = await self.player_repo.get_by_id(db, player_id)
        if player is None:
            return CheckResponse(result=CheckResult.NOT_FOUND, message="Player not found")

        # Step 2: Check cooldown
        now = datetime.now(UTC)
        if player.bounty_cooldown_end and player.bounty_cooldown_end > now:
            remaining = (player.bounty_cooldown_end - now).total_seconds()
            return CheckResponse(
                result=CheckResult.ON_COOLDOWN,
                message=f"On cooldown for {int(remaining)} more seconds",
            )

        # Step 3: Determine player's division
        division = "bronze" if player.classic_mode else player.tier.lower() if player.tier else "bronze"

        # Step 4: Get active bounties for this division
        active_bounties = await self.bounty_repo.get_active_by_guild_and_division(
            db, guild_id, division
        )

        # Step 5: Check system against all active bounties
        for bounty in active_bounties:
            if system_name not in bounty.route:
                continue

            # System is in this bounty's route
            checked = dict(bounty.checked)  # Copy to modify

            if checked.get(system_name, -1) != -1:
                # Already checked by someone
                return CheckResponse(
                    result=CheckResult.ALREADY_CHECKED,
                    bounty_id=bounty.id,
                    message=f"System {system_name} already checked",
                )

            # Mark system as checked by this player
            checked[system_name] = player_id
            bounty.checked = checked

            # Apply cooldown
            cooldown_seconds = getattr(GameConstants, "CHECK_COOLDOWN", 180)
            player.bounty_cooldown_end = now + timedelta(seconds=cooldown_seconds)

            # Check if this is the answer
            if bounty.answer == system_name:
                # CORRECT — found the criminal!
                await self.bounty_repo.update(db, bounty)
                await db.commit()
                await db.refresh(player)
                flogger.info(
                    f"Player {player_id} found {bounty.criminal_name} at {system_name} "
                    f"(bounty {bounty.id})"
                )
                return CheckResponse(
                    result=CheckResult.CORRECT,
                    bounty_id=bounty.id,
                    message=f"Found {bounty.criminal_name}!",
                )

            # INCORRECT — system was in the route but not the answer
            # Check for proximity hint
            proximity_hint = False
            distance = None
            try:
                answer_idx = bounty.route.index(bounty.answer)
                system_idx = bounty.route.index(system_name)
                distance = answer_idx - system_idx
                close_threshold = getattr(GameConstants, "CLOSE_BOUNTY_THRESHOLD", 4)
                if 0 < distance < close_threshold:
                    proximity_hint = True
            except (ValueError, IndexError):
                pass

            await self.bounty_repo.update(db, bounty)
            await db.commit()
            await db.refresh(player)
            flogger.debug(
                f"Player {player_id} checked {system_name} on bounty {bounty.id}: incorrect"
            )
            return CheckResponse(
                result=CheckResult.INCORRECT,
                bounty_id=bounty.id,
                message=f"No sign of {bounty.criminal_name} at {system_name}",
                proximity_hint=proximity_hint,
                distance_to_answer=distance,
            )

        # System not found in any active bounty
        return CheckResponse(
            result=CheckResult.NOT_FOUND,
            message=f"System {system_name} not in any active bounty route",
        )

    # ------------------------------------------------------------------
    # Reward Calculation & Distribution
    # ------------------------------------------------------------------

    async def calc_rewards(
        self,
        db: AsyncSession,
        bounty: Bounty,
    ) -> list[RewardInfo]:
        """Calculate rewards for all contributors to a completed bounty.

        Algorithm:
        1. Identify all players who checked systems (checked[system] != -1)
        2. Each non-winner contributor gets reward_per_sys * systems_they_checked
        3. Winner gets all remaining credits from the pool
        4. XP = credits_earned * BOUNTY_REWARD_TO_XP_GAIN_MULT

        Args:
            db:     Async database session (unused directly, kept for API consistency).
            bounty: Completed bounty to calculate rewards for.

        Returns:
            List of RewardInfo for each contributing player.
        """
        credits_pool = bounty.reward
        rps = bounty.reward_per_sys
        winner_id = bounty.checked.get(bounty.answer, -1)

        # Count systems checked per player
        player_checks: dict[int, int] = {}
        for _system, checker_id in bounty.checked.items():
            if checker_id != -1:  # -1 means unchecked
                player_checks[checker_id] = player_checks.get(checker_id, 0) + 1

        rewards: list[RewardInfo] = []

        # Non-winner contributors
        for player_id, check_count in player_checks.items():
            if player_id == winner_id:
                continue
            credit_reward = rps * check_count
            credits_pool -= credit_reward
            xp_reward = int(credit_reward * GameConstants.BOUNTY_REWARD_TO_XP_GAIN_MULT)

            rewards.append(
                RewardInfo(
                    player_id=player_id,
                    credits_earned=credit_reward,
                    xp_earned=xp_reward,
                    is_winner=False,
                    systems_checked_count=check_count,
                )
            )

        # Winner gets remaining pool
        if winner_id != -1:
            credits_pool = max(0, credits_pool)  # Safety
            xp_reward = int(credits_pool * GameConstants.BOUNTY_REWARD_TO_XP_GAIN_MULT)
            winner_checks = player_checks.get(winner_id, 0)

            rewards.append(
                RewardInfo(
                    player_id=winner_id,
                    credits_earned=credits_pool,
                    xp_earned=xp_reward,
                    is_winner=True,
                    systems_checked_count=winner_checks,
                )
            )

        return rewards

    async def distribute_rewards(
        self,
        db: AsyncSession,
        bounty: Bounty,
        rewards: list[RewardInfo],
    ) -> list[RewardInfo]:
        """Apply calculated rewards to players and update bounty status.

        For each player:
        1. Add credits to player
        2. Add XP to player (skipped for classic mode players)
        3. Increment systems_checked count
        4. Increment bounty_wins for the winner
        5. Check for level-up
        6. Update bounty status to 'completed'

        Args:
            db:      Async database session.
            bounty:  The bounty being completed.
            rewards: Pre-calculated reward list from :meth:`calc_rewards`.

        Returns:
            Updated RewardInfo list with level information populated.
        """
        for reward in rewards:
            player = await self.player_repo.get_by_id(db, reward.player_id)
            if player is None:
                flogger.warning(
                    f"Player {reward.player_id} not found during reward distribution"
                )
                continue

            # Record level before
            reward.level_before = calculate_user_level(player.xp)

            # Apply credits
            player.credits += reward.credits_earned
            player.lifetime_credits += reward.credits_earned

            # Apply XP (skip for classic mode players)
            if not player.classic_mode:
                player.xp += reward.xp_earned

            # Update stats
            player.systems_checked += reward.systems_checked_count
            if reward.is_winner:
                player.bounty_wins += 1

            # Check for level-up
            reward.level_after = calculate_user_level(player.xp)
            reward.leveled_up = reward.level_after > reward.level_before

            # Persist player changes
            await db.commit()
            await db.refresh(player)

        # Update bounty status
        bounty.status = "completed"
        bounty.win_user_id = next(
            (r.player_id for r in rewards if r.is_winner), None
        )
        await self.bounty_repo.update(db, bounty)

        return rewards

    # ------------------------------------------------------------------
    # Bounty Expiry
    # ------------------------------------------------------------------

    async def expire_bounty(
        self,
        db: AsyncSession,
        bounty_id: int,
    ) -> Bounty | None:
        """Mark a bounty as expired.

        Called when a bounty's end_time is reached without being completed.
        Sets status to 'expired' and persists.

        Args:
            db: Async database session.
            bounty_id: ID of the bounty to expire.

        Returns:
            Updated Bounty object, or None if bounty not found.
        """
        bounty = await self.bounty_repo.get_by_id(db, bounty_id)
        if bounty is None:
            flogger.warning(f"Cannot expire bounty {bounty_id}: not found")
            return None

        if bounty.status != "active":
            flogger.warning(f"Cannot expire bounty {bounty_id}: status is {bounty.status}")
            return None

        bounty.status = "expired"
        await self.bounty_repo.update(db, bounty)
        flogger.info(f"Bounty {bounty_id} expired: {bounty.criminal_name}")
        return bounty

    # ------------------------------------------------------------------
    # Bounty Escape
    # ------------------------------------------------------------------

    async def escape_bounty(
        self,
        db: AsyncSession,
        bounty_id: int,
    ) -> tuple[Bounty | None, int]:
        """Mark a bounty as escaped and calculate respawn delay.

        Called when a player loses the combat duel against the criminal.
        Sets status to 'escaped', increments escape_count, and calculates
        respawn delay based on route length (1 minute per system in route).

        The respawn itself is NOT triggered here — the caller (scheduler)
        is responsible for scheduling the respawn after the returned delay.

        Args:
            db: Async database session.
            bounty_id: ID of the bounty where criminal escaped.

        Returns:
            Tuple of (updated Bounty, respawn_delay_minutes).
            Returns (None, 0) if bounty not found.
        """
        bounty = await self.bounty_repo.get_by_id(db, bounty_id)
        if bounty is None:
            flogger.warning(f"Cannot escape bounty {bounty_id}: not found")
            return None, 0

        if bounty.status != "active":
            flogger.warning(f"Cannot escape bounty {bounty_id}: status is {bounty.status}")
            return None, 0

        bounty.status = "escaped"
        bounty.escape_count += 1

        # Respawn delay = len(route) minutes
        respawn_delay = len(bounty.route) if bounty.route else 5
        bounty.respawn_time = datetime.now(UTC) + timedelta(minutes=respawn_delay)

        await self.bounty_repo.update(db, bounty)
        flogger.info(
            f"Bounty {bounty_id} escaped (count: {bounty.escape_count}), "
            f"respawn in {respawn_delay} minutes"
        )
        return bounty, respawn_delay

    # ------------------------------------------------------------------
    # Bounty Respawn
    # ------------------------------------------------------------------

    async def respawn_bounty(
        self,
        db: AsyncSession,
        bounty_id: int,
    ) -> Bounty | None:
        """Respawn an escaped bounty with a new route and answer.

        Keeps the same criminal but generates a fresh route via A*
        and picks a new answer. Resets checked dict and status to 'active'.

        Args:
            db: Async database session.
            bounty_id: ID of the escaped bounty to respawn.

        Returns:
            Updated Bounty object with new route/answer, or None on failure.
        """
        bounty = await self.bounty_repo.get_by_id(db, bounty_id)
        if bounty is None:
            flogger.warning(f"Cannot respawn bounty {bounty_id}: not found")
            return None

        if bounty.status != "escaped":
            flogger.warning(f"Cannot respawn bounty {bounty_id}: status is {bounty.status}")
            return None

        # Generate new route (same logic as spawn_bounty step 3)
        await self.graph_service.load_graph(db)
        jump_gate_systems = self.graph_service.get_systems_with_jump_gates()

        if len(jump_gate_systems) < 2:
            flogger.warning("Not enough systems for respawn route")
            return None

        route = None
        for _attempt in range(3):
            start = random.choice(jump_gate_systems)
            end = random.choice(jump_gate_systems)
            while end == start:
                end = random.choice(jump_gate_systems)

            result = self.pathfinding_service.make_route(start, end)
            if isinstance(result, list):
                route = result
                break

        if route is None:
            flogger.warning(f"Failed to generate respawn route for bounty {bounty_id}")
            return None

        # New answer and checked dict
        answer = random.choice(route)
        checked = {system: -1 for system in route}

        # Update bounty
        bounty.route = route
        bounty.answer = answer
        bounty.checked = checked
        bounty.status = "active"
        bounty.respawn_time = None

        # Update end_time based on new route length
        bounty.end_time = datetime.now(UTC) + timedelta(days=len(route))

        await self.bounty_repo.update(db, bounty)
        flogger.info(
            f"Bounty {bounty_id} respawned: {bounty.criminal_name} "
            f"with new route ({len(route)} systems)"
        )
        return bounty
