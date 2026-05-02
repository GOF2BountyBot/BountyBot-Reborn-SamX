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

from services.combat_models import ShipLoadout, WeaponStats
from services.combat_service import CombatService
from services.game_constants import GameConstants
from services.game_maths import (
    pick_random_item_tl,
    reward_per_sys_check,
    ship_tech_level_for_value,
)
from services.pathfinding_service import PathfindingService
from services.system_graph_service import SystemGraphService

flogger = bblogger.get_logger("bounty-service")


def _serialize_fight_results(fight_results) -> dict | None:
    """Serialize a FightResults dataclass to a plain dict for API responses.

    Returns None if fight_results is None.

    Args:
        fight_results: FightResults instance (or None).

    Returns:
        Dict representation suitable for JSON serialization, or None.
    """
    if fight_results is None:
        return None

    def _stats_to_dict(fs) -> dict:
        return {
            "ship_name": fs.ship_name,
            "raw_hp": fs.raw_hp,
            "raw_dps": fs.raw_dps,
            "varied_hp": fs.varied_hp,
            "varied_dps": fs.varied_dps,
            "ttk": fs.ttk,
        }

    return {
        "winner_name": fight_results.winner_name,
        "loser_name": fight_results.loser_name,
        "is_stalemate": fight_results.is_stalemate,
        "ship1_stats": _stats_to_dict(fight_results.ship1_stats),
        "ship2_stats": _stats_to_dict(fight_results.ship2_stats),
        "variance_percent": fight_results.variance_percent,
    }


class CheckResult(enum.Enum):
    """Result codes for the bounty check mechanic."""

    NOT_FOUND = "not_found"
    ALREADY_CHECKED = "already_checked"
    INCORRECT = "incorrect"
    CORRECT = "correct"
    ON_COOLDOWN = "on_cooldown"


@dataclass
class CheckResponse:  # pylint: disable=too-many-instance-attributes
    """Per-bounty outcome of a single :meth:`BountyService.check_bounty` invocation.

    A single ``/check`` call may produce one or more :class:`CheckResponse`
    objects (one per bounty in the player's division whose route contains the
    checked system). They are aggregated inside a :class:`MultiCheckResponse`.
    """

    result: CheckResult
    bounty_id: int | None = None
    message: str = ""
    proximity_hint: bool = False
    distance_to_answer: int | None = None
    combat_won: bool | None = None  # None if no combat, True/False if combat occurred
    new_tier: str | None = None  # If the player's tier changed after this check
    # Division / criminal metadata (populated on CORRECT results)
    division: str | None = None
    criminal_name: str | None = None
    reward: int | None = None
    # Combat result details
    combat_result: dict | None = None  # FightResults serialized as dict
    # Bronze-specific fields
    bonus_won: bool = False  # True if bronze player won the optional combat bonus
    total_reward: int | None = None  # Final reward earned (may be 2x for bronze win)
    criminal_ship: dict | None = None  # Criminal ship data; returned for bronze so cog can offer bonus
    # Recently spotted: criminal was at this system 1-2 stops ago
    recently_spotted: bool = False
    # Cooldown timestamp (Unix): when the cooldown expires (populated on ON_COOLDOWN results)
    cooldown_until: int | None = None


@dataclass
class MultiCheckResponse:
    """Aggregate response for a single ``/check`` invocation.

    ``/check`` on a system may affect multiple bounties simultaneously when
    several active bounties in the player's division share that system.
    :class:`MultiCheckResponse` collects one :class:`CheckResponse` per
    affected bounty in :attr:`outcomes`.

    For pre-multi-bounty callers (single-outcome cases) attribute access
    transparently delegates to ``outcomes[0]`` so old code that did
    ``result.result``, ``result.bounty_id`` etc. keeps working.
    """

    outcomes: list[CheckResponse]
    # Aggregated/top-level fields useful to the gateway:
    cooldown_until: int | None = None
    division: str | None = None

    def __getattr__(self, name: str):
        # Only invoked for attributes NOT defined on the dataclass itself.
        # Delegates to the first outcome when present; raises otherwise.
        outcomes = self.__dict__.get("outcomes")
        if outcomes:
            first = outcomes[0]
            if hasattr(first, name):
                return getattr(first, name)
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")


@dataclass
class RewardInfo:
    """Reward info for a single player.

    B.48: removed vestigial ``level_before`` / ``level_after`` / ``leveled_up``
    fields when the hardcoded level system was deleted. Bounty rewards never
    auto-advance tier (tier change requires explicit ``/promote``), so there
    is no level-equivalent surface to expose here.
    """

    player_id: int
    credits_earned: int
    xp_earned: int
    is_winner: bool = False
    systems_checked_count: int = 0


class BountyService:
    """Service for bounty generation and criminal selection logic."""

    def __init__(self) -> None:
        self.bounty_repo = BountyRepository()
        self.criminal_repo = CriminalRepository()
        self.item_repo = ItemRepository()
        self.player_repo = PlayerRepository()
        self.graph_service = SystemGraphService()
        self.pathfinding_service = PathfindingService(self.graph_service)
        self.combat_service = CombatService()

    # ------------------------------------------------------------------
    # Criminal Selection
    # ------------------------------------------------------------------

    async def select_criminal(self, db: AsyncSession, guild_id: int, division: str) -> Criminal | None:
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
        active_bounties = await self.bounty_repo.get_active_by_guild_and_division(db, guild_id, division)
        active_names = {b.criminal_name for b in active_bounties}

        # Filter out criminals already active in this division
        available = [c for c in available if c.name not in active_names]

        if not available:
            flogger.info(f"No available criminals for guild {guild_id} division {division}")
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
                items = await self.item_repo.get_all_by_tech_level(db, tl, item_type=item_type)
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
                "ship_armour": 50,
                "armor_hp": 50,
                "shield_hp": 0,
                "total_hp": 50,
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

            result = await db.execute(select(Ship).where(Ship.max_primaries > 0))
            all_ships = list(result.scalars().all())
            matching_ships = [s for s in all_ships if ship_tech_level_for_value(s.value) == ship_tl]
            if matching_ships:
                ship = random.choice(matching_ships)

        if ship is None:
            # Fallback: pick any combat-capable ship (max_primaries > 0)
            from persist.models.ship import Ship
            from sqlalchemy import select

            result = await db.execute(select(Ship).where(Ship.max_primaries > 0))
            all_ships = list(result.scalars().all())
            if not all_ships:
                flogger.warning("No combat-capable ships (max_primaries > 0) found in DB — this should never happen")
            if all_ships:
                ship = random.choice(all_ships)

        if ship is None:
            flogger.warning(f"No ships available for tech_level={tech_level}")
            return {
                "ship_name": "Unknown",
                "ship_value": 0,
                "ship_armour": 100,
                "armor_hp": 100,
                "shield_hp": 0,
                "total_hp": 100,
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
                all_weapons = await self.item_repo.get_all_by_tech_level(db, weapon_tl, item_type="primary_weapon")
                damaging = [w for w in all_weapons if w.dps > 0]
                non_damaging = [w for w in all_weapons if w.dps <= 0]

                for _ in range(ship.max_primaries):
                    # 20% chance to pick a non-damaging weapon (if available)
                    pick_non_damaging = (
                        non_damaging and random.randint(1, 100) <= GameConstants.CRIMINAL_EQUIP_DAMAGELESS_WEAPON_CHANCE
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
            generic_modules = await self.item_repo.get_all_by_tech_level(db, item_tl, item_type="module")

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

            # Fill remaining slots with random modules at item_tl, respecting type-class uniqueness
            # Track equipped module type counts using MODULE_EQUIP_LIMITS (type-class, not name-based)
            equipped_type_counts: dict[str, int] = {}
            for m in equipped_modules:
                mtype = getattr(m, "type", "")
                equipped_type_counts[mtype] = equipped_type_counts.get(mtype, 0) + 1

            def _can_equip(module) -> bool:
                mtype = getattr(module, "type", "")
                limit = GameConstants.MODULE_EQUIP_LIMITS.get(mtype, -1)
                if limit == 0:
                    return False
                if limit == -1:
                    return True
                return equipped_type_counts.get(mtype, 0) < limit

            available_pool = [m for m in generic_modules if _can_equip(m)]
            while len(equipped_modules) < ship.max_modules and available_pool:
                chosen = random.choice(available_pool)
                equipped_modules.append(chosen)
                mtype = getattr(chosen, "type", "")
                equipped_type_counts[mtype] = equipped_type_counts.get(mtype, 0) + 1
                # Re-filter pool based on updated type counts
                available_pool = [m for m in available_pool if _can_equip(m)]

        # ----------------------------------------------------------------
        # Calculate partial values (weapons + modules) before turret selection
        # ----------------------------------------------------------------
        weapon_value = sum(getattr(w, "value", 0) for w in equipped_weapons)
        module_value = sum(getattr(m, "value", 0) for m in equipped_modules)

        # ----------------------------------------------------------------
        # Turret weapon selection
        # ----------------------------------------------------------------
        equipped_turrets = []
        if ship.max_turrets > 0:
            turret_tl = await self.find_item_tl(
                db,
                center=item_tl,
                min_tl=GameConstants.MIN_TECH_LEVEL,
                max_tl=GameConstants.MAX_TECH_LEVEL,
                upper_bound=GameConstants.CRIMINAL_MAX_GEAR_UPGRADE,
                item_type="turret_weapon",
            )
            if turret_tl != -1:
                all_turrets = await self.item_repo.get_all_by_tech_level(db, turret_tl, item_type="turret_weapon")
                for _ in range(ship.max_turrets):
                    if all_turrets:
                        equipped_turrets.append(random.choice(all_turrets))

        turret_value = sum(getattr(t, "value", 0) for t in equipped_turrets)
        total_value = ship.value + weapon_value + module_value + turret_value

        # ----------------------------------------------------------------
        # Calculate HP from base ship + modules
        # ----------------------------------------------------------------
        base_armour = getattr(ship, "armour", 100)
        module_armour = 0
        shield_hp = 0
        for m in equipped_modules:
            mtype = getattr(m, "type", "")
            extra = getattr(m, "extra_atts", {}) or {}
            if mtype == "ArmourModule":
                module_armour += extra.get("armour", 0)
            elif mtype in ("ShieldModule", "GammaShieldModule"):
                shield_hp += extra.get("shield", 0)

        armor_hp = base_armour + module_armour
        total_hp = armor_hp + shield_hp

        return {
            "ship_name": ship.name,
            "ship_emoji": getattr(ship, "emoji", None),
            "ship_value": ship.value,
            "ship_armour": base_armour,
            "armor_hp": armor_hp,
            "shield_hp": shield_hp,
            "total_hp": total_hp,
            "ship_max_primaries": ship.max_primaries,
            "ship_max_modules": ship.max_modules,
            "ship_max_turrets": ship.max_turrets,
            "weapons": [
                {"name": w.name, "emoji": getattr(w, "emoji", None), "value": w.value, "dps": w.dps}
                for w in equipped_weapons
            ],
            "modules": [
                {
                    "name": m.name,
                    "emoji": getattr(m, "emoji", None),
                    "value": m.value,
                    "tech_level": m.tech_level,
                    "type": getattr(m, "type", ""),
                    "extra_atts": getattr(m, "extra_atts", {}) or {},
                }
                for m in equipped_modules
            ],
            "turrets": [
                {"name": t.name, "emoji": getattr(t, "emoji", None), "value": t.value, "dps": t.dps}
                for t in equipped_turrets
            ],
            "total_value": total_value,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_criminal_loadout(self, criminal_ship: dict) -> ShipLoadout:
        """Build a ShipLoadout from a bounty's criminal_ship JSONB data.

        Args:
            criminal_ship: Dict containing criminal ship data from bounty JSONB column.

        Returns:
            ShipLoadout ready for combat resolution.
        """
        weapons = [WeaponStats(name=w["name"], dps=w.get("dps", 0)) for w in criminal_ship.get("weapons", [])]
        turrets = [WeaponStats(name=t["name"], dps=t.get("dps", 0)) for t in criminal_ship.get("turrets", [])]
        return ShipLoadout(
            ship_name=criminal_ship.get("ship_name", "Unknown"),
            base_armour=criminal_ship.get("ship_armour", 100),
            weapons=weapons,
            turrets=turrets,
        )

    def _build_player_loadout(self, player, player_ship=None) -> ShipLoadout:
        """Build a minimal ShipLoadout from a player's active ship.

        If the player has no active ship, a default unarmed loadout is used.

        Args:
            player:      Player ORM instance.
            player_ship: Explicitly loaded PlayerShip instance (avoids lazy-loading).
                         If None, falls back to ``player.active_ship`` for backward
                         compatibility (e.g. in tests using SimpleNamespace).

        Returns:
            ShipLoadout with minimal fields populated.
        """
        ship = player_ship if player_ship is not None else getattr(player, "active_ship", None)
        if ship is not None:
            ship_name = getattr(ship, "ship_name", None) or "Unknown"
            base_armour = getattr(ship, "armour", 100)
        else:
            ship_name = "Unarmed"
            base_armour = 100
        return ShipLoadout(ship_name=ship_name, base_armour=base_armour)

    async def _find_typed_module(self, db: AsyncSession, module_keyword: str, item_tl: int):
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
        modules_at_tl = await self.item_repo.get_all_by_tech_level(db, item_tl, item_type="module")
        keyword_lower = module_keyword.lower()
        matches = [m for m in modules_at_tl if keyword_lower in m.name.lower()]
        if matches:
            return random.choice(matches)

        # Broaden search across all TLs
        for tl in range(GameConstants.MIN_TECH_LEVEL, GameConstants.MAX_TECH_LEVEL + 1):
            if tl == item_tl:
                continue
            modules = await self.item_repo.get_all_by_tech_level(db, tl, item_type="module")
            matches = [m for m in modules if keyword_lower in m.name.lower()]
            if matches:
                return random.choice(matches)

        return None

    async def _reset_bounty_checks(self, db: AsyncSession, bounty) -> None:
        """Reset a bounty after a combat loss (Silver+): clear all checks, pick new location.

        When a Silver/Gold/Platinum player loses combat, the bounty "escapes" within
        its current route. All checked systems are cleared and a new correct location
        is randomly selected from the existing route. The bounty remains active so
        other players can continue hunting.

        Args:
            db:     Async database session.
            bounty: Active Bounty ORM instance to reset.
        """
        route = bounty.route  # list of system names
        # Clear all checked entries — every system is unchecked (-1)
        bounty.checked = {sys: -1 for sys in route}
        # Pick a new correct location randomly from the route
        bounty.answer = random.choice(route)
        await self.bounty_repo.update(db, bounty)
        flogger.info(f"Bounty {bounty.id}: checks reset after combat loss. New answer: {bounty.answer!r}")

    async def _award_combat_bonus(self, db: AsyncSession, player_id: int, bonus_credits: int) -> None:
        """Award a combat bonus to a bronze-division player.

        Called when a Bronze player wins the optional post-capture combat.
        Awards ``bonus_credits`` (equal to the base reward) to the player,
        effectively doubling their total payout.

        Args:
            db:            Async database session.
            player_id:     ID of the player to award.
            bonus_credits: Credits to add (should equal the base bounty reward).
        """
        player = await self.player_repo.get_by_id(db, player_id)
        if player is None:
            flogger.warning(f"Cannot award combat bonus: player {player_id} not found")
            return
        player.credits += bonus_credits
        player.lifetime_credits += bonus_credits
        flogger.info(f"Awarded {bonus_credits:,} combat bonus to player {player_id}")

    # ------------------------------------------------------------------
    # Bounty Spawning
    # ------------------------------------------------------------------

    async def clear_bounties(self, db: AsyncSession, guild_id: int, tier: str | None = None) -> dict:
        """Clear active bounties for a guild (and optionally a specific tier).

        Sets matching active bounties to status='cleared', deletes the actual
        Discord announcement messages via the discord-gateway API, then removes
        the DiscordMessage DB records.

        Args:
            db:       Async database session.
            guild_id: Discord guild ID.
            tier:     Optional division filter ('bronze', 'silver', 'gold').
                      If None, clears all tiers.

        Returns:
            Dict with keys: guild_id, tier, cleared_count, bounty_ids, announcements_deleted.
        """
        import os

        import httpx
        from persist.repositories.discord_message_repository import DiscordMessageRepository

        gateway_host = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
        gateway_port = os.getenv("GATEWAY_PORT", "7999")
        gateway_url = f"http://{gateway_host}:{gateway_port}/api/v1"

        # Clear active bounties at the repository level
        bounty_ids = await self.bounty_repo.clear_active_by_guild(db, guild_id, tier)

        # Delete corresponding Discord announcement messages (gateway + DB)
        announcements_deleted = 0
        if bounty_ids:
            try:
                msg_repo = DiscordMessageRepository()
                for bounty_id in bounty_ids:
                    try:
                        # Fetch the Discord message record to get the message_id
                        discord_msg = await msg_repo.get_by_guild_type_and_reference(
                            db, guild_id, "bounty_announcement", bounty_id
                        )
                        if discord_msg is not None:
                            # Best-effort: delete the actual Discord message via gateway
                            try:
                                channel_id = discord_msg.channel_id
                                async with httpx.AsyncClient() as client:
                                    resp = await client.delete(
                                        f"{gateway_url}/channels/{channel_id}/messages/{discord_msg.message_id}",
                                        timeout=10,
                                    )
                                # 404 is acceptable — message may have been manually deleted
                                if resp.status_code not in (200, 204, 404):
                                    flogger.warning(
                                        f"Non-fatal: gateway returned {resp.status_code} "
                                        f"deleting Discord message {discord_msg.message_id} "
                                        f"for bounty {bounty_id}"
                                    )
                                else:
                                    flogger.info(
                                        f"Deleted Discord message {discord_msg.message_id} for bounty {bounty_id}"
                                    )
                            except Exception as gw_exc:
                                flogger.warning(
                                    f"Non-fatal: failed to delete Discord message "
                                    f"{discord_msg.message_id} for bounty {bounty_id}: {gw_exc}"
                                )

                        # Delete the DB record regardless of gateway result
                        deleted = await msg_repo.delete_by_guild_type_and_reference(
                            db, guild_id, "bounty_announcement", bounty_id
                        )
                        if deleted:
                            announcements_deleted += 1
                    except Exception as e:
                        flogger.warning(f"Non-fatal: failed to delete announcement for bounty {bounty_id}: {e}")
            except Exception as e:
                flogger.warning(f"Non-fatal: failed to delete announcements for guild {guild_id}: {e}")

        # A.11: Clean up any scheduled bounty_expire / bounty_respawn jobs that
        # reference the bounties we just cleared. Orphaned jobs would otherwise
        # fire against already-cleared bounties (non-fatal downstream but noisy).
        # We use the scheduler REST API rather than querying apscheduler_jobs
        # directly, matching the HTTP-boundary pattern already used for gateway
        # announcements above. Failures here are non-fatal — the DB clear and
        # announcement cleanup remain authoritative.
        scheduler_jobs_deleted = 0
        if bounty_ids:
            executor_host = os.getenv("EXECUTOR_HOST", "bot-core")
            executor_port = os.getenv("EXECUTOR_PORT", "8000")
            scheduler_url = f"http://{executor_host}:{executor_port}/api/v1"
            bounty_id_set = set(bounty_ids)

            try:
                async with httpx.AsyncClient() as client:
                    list_resp = await client.get(f"{scheduler_url}/jobs", timeout=10)
                    if list_resp.status_code == 200:
                        for job in list_resp.json():
                            try:
                                args = job.get("args") or []
                                if len(args) < 2 or not isinstance(args[1], dict):
                                    continue
                                payload = args[1]
                                job_type = payload.get("job_type")
                                if job_type not in ("bounty_expire", "bounty_respawn"):
                                    continue
                                if payload.get("bounty_id") not in bounty_id_set:
                                    continue

                                job_id = job.get("id")
                                del_resp = await client.delete(
                                    f"{scheduler_url}/jobs/{job_id}",
                                    timeout=10,
                                )
                                # 404 is acceptable — job already fired or
                                # was removed concurrently.
                                if del_resp.status_code in (200, 204):
                                    scheduler_jobs_deleted += 1
                                    flogger.info(
                                        f"Deleted scheduler job {job_id} "
                                        f"(type={job_type}, bounty_id={payload.get('bounty_id')}, "
                                        f"guild_id={guild_id})"
                                    )
                                elif del_resp.status_code == 404:
                                    # Silent — expected for already-fired jobs.
                                    pass
                                else:
                                    flogger.warning(
                                        f"Non-fatal: scheduler returned {del_resp.status_code} "
                                        f"deleting job {job_id} for bounty {payload.get('bounty_id')}"
                                    )
                            except Exception as job_exc:  # pylint: disable=broad-exception-caught
                                flogger.warning(f"Non-fatal: failed to process scheduler job during cleanup: {job_exc}")
                    else:
                        flogger.warning(
                            f"Non-fatal: scheduler list returned {list_resp.status_code} "
                            f"during clear_bounties for guild {guild_id}"
                        )
            except Exception as e:  # pylint: disable=broad-exception-caught
                flogger.warning(f"Non-fatal: scheduler cleanup failed for guild {guild_id}: {e}")

        flogger.info(
            f"Cleared {len(bounty_ids)} bounties for guild {guild_id} tier={tier}, "
            f"deleted {announcements_deleted} announcements, "
            f"removed {scheduler_jobs_deleted} scheduler jobs"
        )
        return {
            "guild_id": guild_id,
            "tier": tier,
            "cleared_count": len(bounty_ids),
            "bounty_ids": bounty_ids,
            "announcements_deleted": announcements_deleted,
            "scheduler_jobs_deleted": scheduler_jobs_deleted,
        }

    async def spawn_bounty(
        self,
        db: AsyncSession,
        guild_id: int,
        division: str,
        tech_level: int | None = None,
        expiry_minutes: int | None = None,
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
            db:             Async database session.
            guild_id:       Discord guild ID.
            division:       Division name (e.g. "bronze", "silver", "gold").
            tech_level:     Optional override for criminal tech level.
            expiry_minutes: Optional override for bounty expiry duration in minutes.
                            Defaults to 480 (8 hours) if not provided.

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
            # Division TL centers: bronze=1, silver=5, gold=8, platinum=9
            division_tl_map = {"bronze": 1, "silver": 5, "gold": 8, "platinum": 9}
            center_tl = division_tl_map.get(division, 5)
            tech_level = pick_random_item_tl(center_tl)
            # Enforce per-division TL cap so new players are never overwhelmed
            max_tl = GameConstants.DIVISION_MAX_TL.get(division, 10)
            tech_level = min(tech_level, max_tl)

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
        expiry = expiry_minutes if expiry_minutes is not None else 480
        end_time = issue_time + timedelta(minutes=expiry)

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
        flogger.info(f"Spawned bounty {created.id}: {criminal.name} in {division} for guild {guild_id}")
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
    ) -> MultiCheckResponse:
        """Check a star system against ALL active bounties for the player's division.

        Identifies every active bounty in the player's division whose route
        contains *system_name* and records the check on each. A single
        ``/check`` invocation may therefore terminate, mark "already checked",
        or "incorrect" for multiple bounties simultaneously when their routes
        overlap (B.12 fix).

        Behaviour summary:
        - Player not found → single :class:`CheckResponse` with NOT_FOUND.
        - Cooldown active  → single :class:`CheckResponse` with ON_COOLDOWN.
        - System not in any active route → single CheckResponse with NOT_FOUND.
        - Otherwise → one CheckResponse per matched bounty.

        Atomicity: all per-bounty mutations are committed in a single
        transaction. Announcement edits run AFTER the commit and are
        non-fatal — a failure to update one bounty's announcement does not
        roll back the credit / state changes for the others.

        Idempotency: the per-bounty "already checked" guard (``checked[system]
        != -1``) is unchanged. A second invocation of ``/check`` for the same
        ``(player, system)`` pair will see the system already marked and emit
        ALREADY_CHECKED for those bounties — the second call is safe and
        will not double-credit.

        Cooldown: applied ONCE per ``/check`` invocation regardless of how
        many bounties were touched (B.12 design decision — players are not
        penalised for hunting overlapping bounties).

        Args:
            db:          Async database session.
            player_id:   ID of the player performing the check.
            system_name: Name of the star system being checked.
            guild_id:    Discord guild ID.

        Returns:
            A :class:`MultiCheckResponse` whose ``outcomes`` list contains
            one :class:`CheckResponse` per bounty processed (or a single
            top-level outcome for cooldown / no-match cases).
        """
        # Step 1: Get player
        player = await self.player_repo.get_by_id(db, player_id)
        if player is None:
            return MultiCheckResponse(
                outcomes=[CheckResponse(result=CheckResult.NOT_FOUND, message="Player not found")],
            )

        # Step 2: Check cooldown
        now = datetime.now(UTC)
        if player.bounty_cooldown_end and player.bounty_cooldown_end > now:
            remaining = (player.bounty_cooldown_end - now).total_seconds()
            cooldown_until = int(player.bounty_cooldown_end.timestamp())
            return MultiCheckResponse(
                outcomes=[
                    CheckResponse(
                        result=CheckResult.ON_COOLDOWN,
                        message=f"On cooldown for {int(remaining)} more seconds",
                        cooldown_until=cooldown_until,
                    )
                ],
                cooldown_until=cooldown_until,
            )

        # Step 3: Determine player's division
        division = "bronze" if player.classic_mode else player.tier.lower() if player.tier else "bronze"

        # Step 4: Get active bounties for this division
        active_bounties = await self.bounty_repo.get_active_by_guild_and_division(db, guild_id, division)

        # Step 5: Filter to bounties whose route contains the system
        matching_bounties = [b for b in active_bounties if system_name in b.route]

        if not matching_bounties:
            # System not in any active bounty route
            return MultiCheckResponse(
                outcomes=[
                    CheckResponse(
                        result=CheckResult.NOT_FOUND,
                        message=f"System {system_name} not in any active bounty route",
                    )
                ],
                division=division,
            )

        # Step 6: Process each matching bounty.
        # We process all bounties in-memory first (mutating state), commit ONCE
        # at the end, and then run announcement edits per outcome.
        outcomes: list[CheckResponse] = []
        bounties_to_announce: list[tuple[Bounty, bool]] = []  # (bounty, captured)
        cooldown_applied = False
        cooldown_seconds = getattr(GameConstants, "CHECK_COOLDOWN", 180)
        tier_before = player.tier

        for bounty in matching_bounties:
            outcome, announce_info = await self._process_single_bounty_check(
                db,
                player=player,
                player_id=player_id,
                bounty=bounty,
                system_name=system_name,
                division=division,
                now=now,
            )
            outcomes.append(outcome)
            if announce_info is not None:
                bounties_to_announce.append(announce_info)

            # Apply cooldown ONCE per /check call, only after a real (non-already-checked) state change.
            if not cooldown_applied and outcome.result in (CheckResult.CORRECT, CheckResult.INCORRECT):
                player.bounty_cooldown_end = now + timedelta(seconds=cooldown_seconds)
                cooldown_applied = True

        # Single transactional commit covering all per-bounty mutations.
        await db.commit()
        await db.refresh(player)

        # Refresh tier-change tracking ONCE for the whole invocation
        # (any tier promotion was caused by reward distribution above).
        tier_after = player.tier
        new_tier = tier_after if tier_after != tier_before else None
        if new_tier is not None:
            for outcome in outcomes:
                if outcome.result == CheckResult.CORRECT and outcome.combat_won:
                    outcome.new_tier = new_tier
                    break  # Tier-change applies once; surface on the first capture outcome.

        # Per-bounty announcement edits. Each is best-effort & non-fatal —
        # _edit_bounty_announcement already swallows its own exceptions.
        for bounty, captured in bounties_to_announce:
            try:
                await self._edit_bounty_announcement(db, bounty, captured=captured)
            except Exception as e:  # pylint: disable=broad-exception-caught
                flogger.warning(
                    f"Per-bounty announcement edit failed (bounty {bounty.id}, "
                    f"player {player_id}, system {system_name!r}): {e}"
                )

        # Per-bounty structured logging for observability (B.12)
        for outcome in outcomes:
            flogger.info(
                f"check_bounty outcome: bounty_id={outcome.bounty_id} player_id={player_id}"
                f" system={system_name!r} result={outcome.result.value}"
            )

        # cooldown_until on MultiCheckResponse is reserved for the ON_COOLDOWN
        # case (clients show the user when they can retry). It is NOT populated
        # when a new cooldown is just applied — that information lives in the
        # player's state on the next call.
        return MultiCheckResponse(
            outcomes=outcomes,
            cooldown_until=None,
            division=division,
        )

    async def _process_single_bounty_check(
        self,
        db: AsyncSession,
        *,
        player,
        player_id: int,
        bounty: Bounty,
        system_name: str,
        division: str,
        now: datetime,
    ) -> tuple[CheckResponse, tuple[Bounty, bool] | None]:
        """Process a single bounty for a /check invocation.

        Mutates ``bounty`` and ``player`` state in-memory but does NOT
        commit — the caller commits once for all bounties (atomicity).
        Returns the per-bounty :class:`CheckResponse` and an optional
        ``(bounty, captured)`` tuple identifying which announcement to
        re-render after the commit.

        Note: combat (silver+) is run independently per bounty, mirroring
        the original single-bounty contract. Multi-bounty terminal hits
        therefore distribute rewards independently per matching bounty.
        """
        # System is in this bounty's route
        checked = dict(bounty.checked)  # Copy to modify

        if checked.get(system_name, -1) != -1:
            # Already checked by someone — no state change, no announce.
            return (
                CheckResponse(
                    result=CheckResult.ALREADY_CHECKED,
                    bounty_id=bounty.id,
                    message=f"System {system_name} already checked",
                ),
                None,
            )

        # Mark system as checked by this player
        checked[system_name] = player_id
        bounty.checked = checked

        # Check if this is the answer
        if bounty.answer == system_name:
            # CORRECT — found the criminal!
            await self.bounty_repo.update(db, bounty)

            flogger.info(f"Player {player_id} found {bounty.criminal_name} at {system_name} (bounty {bounty.id})")

            # Division-based combat gating
            is_bronze = division == "bronze" or player.classic_mode

            # Load player ship (used for all non-classic-mode cases)
            player_ship = None
            if hasattr(player, "active_ship_id") and player.active_ship_id is not None:
                from persist.models.player_ship import PlayerShip

                player_ship = await db.get(PlayerShip, player.active_ship_id)
            else:
                # Fallback for SimpleNamespace test objects that expose active_ship directly
                player_ship = getattr(player, "active_ship", None)

            _no_ship = player_ship is None

            # Build loadouts for combat (always — used for both bronze bonus and silver+ gate)
            from services.loadout_builder import LoadoutBuilder

            fight_results = None
            player_loadout = await LoadoutBuilder.from_player(db, player_id)
            criminal_loadout = LoadoutBuilder.from_criminal_ship(bounty.criminal_ship or {})

            if is_bronze:
                # BRONZE: Auto-capture always succeeds. Optional combat bonus.
                rewards = await self.calc_rewards(db, bounty)
                await self.distribute_rewards(db, bounty, rewards)

                winner_reward = next((r.credits_earned for r in rewards if r.is_winner), 0)

                bonus_won = False
                total_reward = winner_reward
                if not _no_ship:
                    fight_results = self.combat_service.fight_ships(player_loadout, criminal_loadout)
                    combat_player_won = (
                        fight_results.winner_name == player_loadout.ship_name
                    ) or fight_results.is_stalemate
                    if combat_player_won:
                        bonus_won = True
                        total_reward = winner_reward * 2
                        await self._award_combat_bonus(db, player_id, winner_reward)

                bonus_msg = " (2x combat bonus!)" if bonus_won else ""
                return (
                    CheckResponse(
                        result=CheckResult.CORRECT,
                        bounty_id=bounty.id,
                        message=f"Bounty captured! +{total_reward:,}cr{bonus_msg}",
                        combat_won=True,
                        division=division,
                        criminal_name=bounty.criminal_name,
                        reward=winner_reward,
                        combat_result=_serialize_fight_results(fight_results) if fight_results else None,
                        bonus_won=bonus_won,
                        total_reward=total_reward,
                        criminal_ship=bounty.criminal_ship,
                    ),
                    (bounty, True),
                )

            # SILVER / GOLD / PLATINUM: Mandatory combat gate.
            if _no_ship:
                duel_won = True
                fight_results = None
            else:
                fight_results = self.combat_service.fight_ships(player_loadout, criminal_loadout)
                duel_won = (fight_results.winner_name == player_loadout.ship_name) or fight_results.is_stalemate

            if duel_won:
                rewards = await self.calc_rewards(db, bounty)
                await self.distribute_rewards(db, bounty, rewards)
                winner_reward = next((r.credits_earned for r in rewards if r.is_winner), 0)
                return (
                    CheckResponse(
                        result=CheckResult.CORRECT,
                        bounty_id=bounty.id,
                        message=f"Combat victory! Defeated {bounty.criminal_name}! +{winner_reward:,}cr",
                        combat_won=True,
                        division=division,
                        criminal_name=bounty.criminal_name,
                        reward=winner_reward,
                        combat_result=_serialize_fight_results(fight_results) if fight_results else None,
                    ),
                    (bounty, True),
                )

            # LOSS: Criminal escapes checks — reset bounty location
            await self._reset_bounty_checks(db, bounty)
            return (
                CheckResponse(
                    result=CheckResult.CORRECT,
                    bounty_id=bounty.id,
                    message=f"{bounty.criminal_name} defeated you in combat and escaped!",
                    combat_won=False,
                    division=division,
                    criminal_name=bounty.criminal_name,
                    combat_result=_serialize_fight_results(fight_results) if fight_results else None,
                ),
                (bounty, False),
            )

        # INCORRECT — system was in the route but not the answer
        proximity_hint = False
        recently_spotted = False
        distance = None
        try:
            answer_idx = bounty.route.index(bounty.answer)
            system_idx = bounty.route.index(system_name)
            distance = answer_idx - system_idx
            close_threshold = getattr(GameConstants, "CLOSE_BOUNTY_THRESHOLD", 4)
            if 0 < distance < close_threshold:
                proximity_hint = True
            # recently_spotted: criminal was here 1-2 stops ago (answer is 1-2 stops ahead)
            if 1 <= distance <= 2:
                recently_spotted = True
        except (ValueError, IndexError):
            pass

        await self.bounty_repo.update(db, bounty)
        flogger.debug(f"Player {player_id} checked {system_name} on bounty {bounty.id}: incorrect")

        if recently_spotted:
            inc_message = f"{bounty.criminal_name} was recently spotted here! They're close..."
        else:
            inc_message = f"No sign of {bounty.criminal_name} at {system_name}"

        return (
            CheckResponse(
                result=CheckResult.INCORRECT,
                bounty_id=bounty.id,
                message=inc_message,
                proximity_hint=proximity_hint,
                distance_to_answer=distance,
                recently_spotted=recently_spotted,
            ),
            (bounty, False),
        )

    # ------------------------------------------------------------------
    # Bounty Announcement Live-Edit
    # ------------------------------------------------------------------

    async def _edit_bounty_announcement(self, db: AsyncSession, bounty: Bounty, captured: bool = False) -> None:
        """Edit the bounty's Discord announcement to reflect updated checked systems.

        Post-A.48 unified rendering: the gateway is the rendering authority. We
        post the structured payload (LoadoutResponse + metadata) and let the
        gateway re-render with the shared `build_loadout_embed`.

        Non-fatal — if the message lookup or edit fails, logs a warning and continues.

        Args:
            db:       Async database session.
            bounty:   The bounty whose announcement should be updated.
            captured: When True, the embed shows the "CAPTURED" state (green
                      color, updated title, "**Captured**" Bounty Ends value).
        """
        try:
            import os

            import httpx
            from persist.repositories.criminal_repository import (  # pylint: disable=reimported,redefined-outer-name
                CriminalRepository,
            )
            from persist.repositories.discord_message_repository import DiscordMessageRepository
            from utils.bounty_announcement_payload import build_bounty_announcement_request

            msg_repo = DiscordMessageRepository()
            discord_msg = await msg_repo.get_by_guild_type_and_reference(
                db, bounty.guild_id, "bounty_announcement", bounty.id
            )

            if discord_msg is None:
                flogger.debug(f"No announcement message found for bounty {bounty.id}, skipping edit")
                return

            # Look up the criminal's icon (non-fatal if not found)
            criminal_icon: str | None = None
            try:
                criminal_repo = CriminalRepository()
                criminal = await criminal_repo.get_by_name(db, bounty.criminal_name)
                if criminal is not None:
                    criminal_icon = getattr(criminal, "icon", None) or None
                    flogger.debug(
                        f"_edit_bounty_announcement: criminal icon for {bounty.criminal_name!r}: {criminal_icon!r}"
                    )
            except Exception as _icon_exc:  # pylint: disable=broad-exception-caught
                flogger.debug(
                    f"_edit_bounty_announcement: could not fetch criminal icon for "
                    f"{bounty.criminal_name!r}: {_icon_exc}"
                )

            # Build the structured edit payload (LoadoutResponse + metadata).
            # role mention suppressed on edits; route_map_url=None preserves the
            # image URL that was set on the original send (the gateway's edit
            # path doesn't unset the image when image_url=None unless we pass
            # set_image; in practice the original embed retains the route map).
            edit_payload = await build_bounty_announcement_request(
                db,
                bounty,
                criminal_icon=criminal_icon,
                route_map_url=None,
                bounty_hunter_role_id=None,
                captured=captured,
            )

            gateway_host = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
            gateway_port = os.getenv("GATEWAY_PORT", "7999")
            gateway_url = f"http://{gateway_host}:{gateway_port}/api/v1"

            channel_id = discord_msg.channel_id
            async with httpx.AsyncClient() as client:
                resp = await client.put(
                    f"{gateway_url}/announcements/bounty/channel/{channel_id}/message/{discord_msg.message_id}",
                    json=edit_payload,
                    timeout=10,
                )
            resp.raise_for_status()
            flogger.info(f"Edited bounty announcement for bounty {bounty.id} (message {discord_msg.message_id})")

        except Exception as e:
            flogger.warning(f"Failed to edit bounty announcement for bounty {bounty.id}: {e}")

    async def _delete_bounty_announcement(self, db: AsyncSession, bounty: Bounty) -> None:
        """Delete the bounty announcement from Discord and clean up the DB record.

        Non-fatal — exceptions are caught and logged without propagating.

        Args:
            db:     Async database session.
            bounty: The bounty whose announcement should be deleted.
        """
        try:
            import os

            import httpx
            from persist.repositories.discord_message_repository import DiscordMessageRepository

            msg_repo = DiscordMessageRepository()
            discord_msg = await msg_repo.get_by_guild_type_and_reference(
                db, bounty.guild_id, "bounty_announcement", bounty.id
            )

            flogger.debug(
                f"_delete_bounty_announcement: bounty_id={bounty.id}, "
                f"message_found={discord_msg is not None}, "
                f"reference_id={getattr(discord_msg, 'reference_id', None) if discord_msg else None}"
            )

            if discord_msg is None:
                flogger.debug(f"No announcement message found for bounty {bounty.id}, skipping delete")
                return

            gateway_host = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
            gateway_port = os.getenv("GATEWAY_PORT", "7999")
            gateway_url = f"http://{gateway_host}:{gateway_port}/api/v1"

            try:
                channel_id = discord_msg.channel_id
                async with httpx.AsyncClient() as client:
                    resp = await client.delete(
                        f"{gateway_url}/channels/{channel_id}/messages/{discord_msg.message_id}",
                        timeout=10,
                    )
                # 404 is OK — message may have been manually deleted
                if resp.status_code not in (200, 204, 404):
                    resp.raise_for_status()
                flogger.info(f"Deleted Discord message {discord_msg.message_id} for bounty {bounty.id}")
            except Exception as e:
                flogger.warning(f"Failed to delete Discord message for bounty {bounty.id}: {e}")

            await msg_repo.delete_by_guild_type_and_reference(db, bounty.guild_id, "bounty_announcement", bounty.id)
            flogger.info(f"Deleted announcement record for completed/escaped bounty {bounty.id}")

        except Exception as e:
            flogger.warning(f"Failed to delete announcement for bounty {bounty.id}: {e}")

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
        5. Update bounty status to 'completed'

        B.48: previously also computed level-up flags, now removed.

        Args:
            db:      Async database session.
            bounty:  The bounty being completed.
            rewards: Pre-calculated reward list from :meth:`calc_rewards`.

        Returns:
            Updated RewardInfo list (post-mutation).
        """
        modified_players = []
        for reward in rewards:
            player = await self.player_repo.get_by_id(db, reward.player_id)
            if player is None:
                flogger.warning(f"Player {reward.player_id} not found during reward distribution")
                continue

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

            # B.48: no level-up detection — the level concept was deleted
            # along with the hardcoded XP_LEVEL_BOUNDARIES.

            modified_players.append(player)

        # Update bounty status (commit=False; this service owns the explicit commit below).
        # B.34 closeout: previously this method relied on bounty_repo.update()'s
        # default commit=True to flush ALL pending changes (the direct ORM
        # mutations on modified_players above). That works only by accident —
        # if any future change set commit=False here, the cross-table player
        # mutations would silently roll back. Now the service owns the
        # transaction explicitly. (Note: distribute_rewards is called from
        # check_bounty which already issues an explicit db.commit() at the end
        # of its loop, so this commit is the inner cross-table flush; the outer
        # check_bounty commit is a no-op when no further changes are pending.)
        bounty.status = "completed"
        bounty.win_user_id = next((r.player_id for r in rewards if r.is_winner), None)
        await self.bounty_repo.update(db, bounty, commit=False)
        await db.commit()

        # Refresh all modified players for accurate state.
        for player in modified_players:
            await db.refresh(player)

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
        flogger.info(f"Bounty {bounty_id} escaped (count: {bounty.escape_count}), respawn in {respawn_delay} minutes")
        return bounty, respawn_delay

    # ------------------------------------------------------------------
    # Bounty Respawn
    # ------------------------------------------------------------------

    async def respawn_bounty(
        self,
        db: AsyncSession,
        bounty_id: int,
        expiry_minutes: int | None = None,
    ) -> Bounty | None:
        """Respawn an escaped bounty with a new route and answer.

        Keeps the same criminal but generates a fresh route via A*
        and picks a new answer. Resets checked dict and status to 'active'.

        Args:
            db:             Async database session.
            bounty_id:      ID of the escaped bounty to respawn.
            expiry_minutes: Optional override for bounty expiry in minutes.
                            Defaults to 480 (8 hours) if not provided.

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

        # Update end_time based on expiry_minutes (or default 480 minutes)
        expiry = expiry_minutes if expiry_minutes is not None else 480
        bounty.end_time = datetime.now(UTC) + timedelta(minutes=expiry)

        await self.bounty_repo.update(db, bounty)
        flogger.info(f"Bounty {bounty_id} respawned: {bounty.criminal_name} with new route ({len(route)} systems)")
        return bounty
