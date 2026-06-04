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
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from persist.models.bounty import Bounty
from persist.models.criminal import Criminal
from persist.repositories.bounty_repository import BountyRepository
from persist.repositories.config_repository import ConfigRepository
from persist.repositories.criminal_repository import CriminalRepository
from persist.repositories.item_repository import ItemRepository
from persist.repositories.player_repository import PlayerRepository
from persist.repositories.secondary_weapon_repository import SecondaryWeaponRepository
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

from services.combat_models import DEFERRED_SECONDARY_SUBTYPES, ShipLoadout
from services.combat_service import CombatService
from services.game_constants import GameConstants, resolve_constant
from services.game_maths import (
    pick_random_item_tl,
    reward_per_sys_check,
    ship_tech_level_for_value,
)
from services.pathfinding_service import PathfindingService
from services.system_graph_service import SystemGraphService

flogger = bblogger.get_logger("bounty-service")


def _extract_weapon_combat_fields(item) -> dict:
    """Extract combat fields from a weapon ORM object's extra_atts.

    DB storage nests combat-relevant snake_case fields inside an inner
    ``extra_atts`` dict (e.g. ``{"extra_atts": {"loading_speed_ms": 220, ...}}``).
    The canonical unwrap pattern is ``outer.get("extra_atts", outer)`` — fall
    back to the outer dict for flat/legacy seeds.

    Returns a dict with keys: ``damage_per_shot``, ``loading_speed_ms``,
    ``range_m``, ``subtype``.  All default to safe zero/empty values so the
    dict is always present even for weapons that lack extra_atts entirely.
    """
    outer: dict = getattr(item, "extra_atts", None) or {}
    inner: dict = outer.get("extra_atts", outer) if isinstance(outer, dict) else {}
    return {
        "damage_per_shot": inner.get("damage_per_shot"),
        "loading_speed_ms": int(inner.get("loading_speed_ms", 0) or 0),
        "range_m": float(inner.get("range_m", 0.0) or 0.0),
        "subtype": inner.get("subtype", "") or "",
    }


def get_secondary_subtype(item) -> str:
    """Unwrap the secondary-weapon subtype from an ORM object's extra_atts.

    The subtype lives in the INNER extra_atts dict (DB nesting pattern).
    This is the single-source implementation; shop_service imports this
    function to avoid duplicating the unwrap logic (drift risk).

    Args:
        item: Any object with an ``extra_atts`` attribute (SecondaryWeapon ORM
              instance, SimpleNamespace, or similar).

    Returns:
        Subtype string (e.g. ``"nuke"``, ``"missile"``), or empty string if absent.
    """
    outer: dict = getattr(item, "extra_atts", None) or {}
    inner: dict = outer.get("extra_atts", outer) if isinstance(outer, dict) else {}
    return inner.get("subtype", "") if isinstance(inner, dict) else ""


def _extract_secondary_combat_fields(item) -> dict:
    """Extract ALL combat fields from a secondary-weapon ORM object.

    Unlike ``_extract_weapon_combat_fields`` (which omits secondary-specific
    fields and reads ``damage_per_shot``), this helper reads the ``damage``
    column (the secondary weapon's explosion/hit damage) and also extracts
    ``burst_count``, ``emp_damage``, ``magnitude_m``, and ``steerable`` which
    are required for the tick resolver to fire the weapon correctly.

    DB nesting pattern identical to primaries/turrets:
        outer = item.extra_atts  (e.g. ``{"loading speed": ..., "extra_atts": {...}}``)
        inner = outer["extra_atts"]  (snake_case combat fields live here)

    Args:
        item: SecondaryWeapon ORM instance (or SimpleNamespace with matching attrs).

    Returns:
        Dict with keys: ``damage``, ``loading_speed_ms``, ``range_m``, ``subtype``,
        ``burst_count``, ``emp_damage``, ``magnitude_m``, ``steerable``.
        All default to safe zero/empty values.
    """
    outer: dict = getattr(item, "extra_atts", None) or {}
    inner: dict = outer.get("extra_atts", outer) if isinstance(outer, dict) else {}
    # ``damage`` comes from the ORM column (item.damage), NOT from extra_atts.
    damage = int(getattr(item, "damage", 0) or 0)
    return {
        "damage": damage,
        "loading_speed_ms": int(inner.get("loading_speed_ms", 0) or 0),
        "range_m": float(inner.get("range_m", 0.0) or 0.0),
        "subtype": inner.get("subtype", "") or "",
        "burst_count": int(inner.get("burst_count", 0) or 0),
        "emp_damage": int(inner.get("emp_damage", 0) or 0),
        "magnitude_m": float(inner.get("magnitude_m", 0.0) or 0.0),
        "steerable": bool(inner.get("steerable", False)),
    }


# Sentinel values used in bounty.checked maps.
# >0 = player_id who locked the slot; <0 = special state.
UNCHECKED = -1  # System has not been checked yet — fair game.
FORFEITED_CHECK = -2  # System checked by a player who has since promoted past this
# bounty's division. Slot stays "claimed" (other players see
# ALREADY_CHECKED), but the original checker is no longer
# eligible for the per-system payout. See scrub_player_checks_below_tier.


def _serialize_fight_results(fight_results) -> dict | None:
    """Serialize a FightResults dataclass to a plain dict for API responses.

    Returns None if fight_results is None. Includes the combat_log_id when present.
    variance_percent is omitted (retired in T10 alongside SimpleTTKResolver).

    pvc_damage_reduction is included from FightResults.metadata; it is 0.0 for
    PvP fights and the configured DR value (e.g. 0.33) for PvC bounty fights.

    Also includes the actual after-action summary (final_hp, damage_dealt,
    damage_taken, shots_fired, shots_hit, accuracy, outcome, reason,
    duration_ticks) from metadata["summary"] when available.  Consumers that
    only need the legacy projection fields can ignore the new keys; they are
    always optional so the shape stays backward-compatible.

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

    metadata = getattr(fight_results, "metadata", None) or {}
    inner_meta = metadata.get("metadata", {}) or {}
    summary = metadata.get("summary", {}) or {}
    pvc_damage_reduction = float(inner_meta.get("pvc_damage_reduction", metadata.get("pvc_damage_reduction", 0.0)))
    tick_ms = int(inner_meta.get("tick_ms", 10))
    duration_ticks = int(summary.get("duration_ticks", 0))
    duration_s = (duration_ticks * tick_ms) / 1000.0 if duration_ticks else None

    result: dict = {
        "winner_name": fight_results.winner_name,
        "loser_name": fight_results.loser_name,
        "is_stalemate": fight_results.is_stalemate,
        "ship1_stats": _stats_to_dict(fight_results.ship1_stats),
        "ship2_stats": _stats_to_dict(fight_results.ship2_stats),
        "combat_log_id": fight_results.combat_log_id,
        "pvc_damage_reduction": pvc_damage_reduction,
        # After-action summary fields (populated from tick-resolver summary; None when unavailable)
        "outcome": summary.get("outcome"),
        "reason": summary.get("reason"),
        "duration_ticks": duration_ticks or None,
        "duration_s": duration_s,
        "combatants": summary.get("combatants"),
    }
    return result


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
    # Payout breakdown (populated on CORRECT/capture outcomes so the cog can render the full embed)
    reward_per_sys: int | None = None
    route_length: int | None = None
    # Per-player payout breakdown: list of dicts with player_display_name, role, amount
    # Populated on CORRECT/capture outcomes so the cog can render the full per-player breakdown.
    payout_breakdown: list[dict] = field(default_factory=list)
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
        self.config_repo = ConfigRepository()
        self.graph_service = SystemGraphService()
        self.pathfinding_service = PathfindingService(self.graph_service)
        self.combat_service = CombatService()

    # ------------------------------------------------------------------
    # Criminal Selection
    # ------------------------------------------------------------------

    async def select_criminal(self, db: AsyncSession, guild_id: int, division: str) -> Criminal | None:
        """Select a random criminal for a new bounty in (guild, division).

        Algorithm:
            1. Load all non-player criminals.
            2. Filter out criminals already active in *this division* of the guild.
            3. If any remain → pick uniformly across factions, then uniformly within
               the chosen faction (matches legacy faction-uniform behaviour).
            4. If the filter empties the pool ("pool exhausted" — every criminal
               already has an active bounty in this division), log a WARNING and
               fall back to the FULL non-player pool, permitting same-division
               reuse. This supports large/active guilds with high
               ``bounty_max_per_tier`` values that exceed the criminal pool.
            5. Only return ``None`` when the criminal table itself is empty
               (configuration/seeding failure).

        Concurrency note: select_criminal is NOT itself race-safe across
        concurrent transactions. Race-safety for the spawn pipeline is provided
        by the orchestrator's gap-aware fire-time scheduling combined with the
        early-commit pattern in ``execute_bounty_spawn_one_job``. Two concurrent
        spawn jobs for the same (guild, division) firing within milliseconds
        could still pick the same criminal here; the gap-aware scheduling in the
        orchestrator (≥10s spacing) ensures the prior bounty has committed
        before the next read happens.

        Args:
            db:        Async database session.
            guild_id:  Discord guild ID.
            division:  Division name (e.g. "bronze", "silver", "gold", "platinum").

        Returns:
            A Criminal object, or None if no non-player criminals exist at all.
        """
        # Load all non-player criminals (the full eligible pool).
        all_criminals: list[Criminal] = await self.criminal_repo.list_all(db)
        full_pool = [c for c in all_criminals if not c.is_player]

        if not full_pool:
            flogger.error(
                f"select_criminal: criminal table contains no non-player criminals "
                f"(guild={guild_id} division={division}) — check seed data"
            )
            return None

        # Filter out criminals already active in THIS division of this guild.
        active_bounties = await self.bounty_repo.get_active_by_guild_and_division(db, guild_id, division)
        active_names = {b.criminal_name for b in active_bounties}
        available = [c for c in full_pool if c.name not in active_names]

        if not available:
            # Pool exhausted: every non-player criminal already has an active
            # bounty in this division. Allow same-division reuse.
            flogger.warning(
                f"select_criminal: criminal pool exhausted for guild={guild_id} "
                f"division={division} ({len(active_names)} active, "
                f"{len(full_pool)} non-player criminals) — allowing same-division reuse"
            )
            available = full_pool

        # Pick a random faction, then a random criminal from that faction.
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

    async def generate_loadout(self, db: AsyncSession, tech_level: int, cfg=None) -> dict:
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
                "ship_max_secondaries": 0,
                "weapons": [],
                "modules": [],
                "turrets": [],
                "secondaries": [],
                "total_value": 0,
            }

        # item_tl is one below criminal TL (minimum 1)
        item_tl = max(1, tech_level - 1)

        # ----------------------------------------------------------------
        # Ship selection
        # ----------------------------------------------------------------
        _criminal_max_gear_upgrade = resolve_constant(
            cfg, "criminal_max_gear_upgrade", GameConstants.CRIMINAL_MAX_GEAR_UPGRADE
        )
        ship_tl = await self.find_item_tl(
            db,
            center=item_tl,
            min_tl=GameConstants.MIN_TECH_LEVEL,
            max_tl=GameConstants.MAX_TECH_LEVEL,
            upper_bound=_criminal_max_gear_upgrade,
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
                "ship_max_secondaries": 0,
                "weapons": [],
                "modules": [],
                "turrets": [],
                "secondaries": [],
                "total_value": 0,
            }

        # ----------------------------------------------------------------
        # Primary weapon selection
        # ----------------------------------------------------------------
        equipped_weapons = []
        _criminal_equip_damageless_chance = resolve_constant(
            cfg, "criminal_equip_damageless_weapon_chance", GameConstants.CRIMINAL_EQUIP_DAMAGELESS_WEAPON_CHANCE
        )
        if ship.max_primaries > 0:
            weapon_tl = await self.find_item_tl(
                db,
                center=item_tl,
                min_tl=GameConstants.MIN_TECH_LEVEL,
                max_tl=GameConstants.MAX_TECH_LEVEL,
                upper_bound=_criminal_max_gear_upgrade,
                item_type="primary_weapon",
            )
            if weapon_tl != -1:
                all_weapons = await self.item_repo.get_all_by_tech_level(db, weapon_tl, item_type="primary_weapon")
                damaging = [w for w in all_weapons if w.dps > 0]
                non_damaging = [w for w in all_weapons if w.dps <= 0]

                for _ in range(ship.max_primaries):
                    # 20% chance to pick a non-damaging weapon (if available)
                    pick_non_damaging = non_damaging and random.randint(1, 100) <= _criminal_equip_damageless_chance
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
                upper_bound=_criminal_max_gear_upgrade,
                item_type="turret_weapon",
            )
            if turret_tl != -1:
                all_turrets = await self.item_repo.get_all_by_tech_level(db, turret_tl, item_type="turret_weapon")
                for _ in range(ship.max_turrets):
                    if all_turrets:
                        equipped_turrets.append(random.choice(all_turrets))

        turret_value = sum(getattr(t, "value", 0) for t in equipped_turrets)

        # ----------------------------------------------------------------
        # Secondary weapon selection (CI-17)
        # Subtype-aware pool: never hand out deferred subtypes or dead-weight
        # (zero-damage) items.  Sample distinct-by-name WITHOUT replacement.
        # Graceful empty: max_secondaries==0 or empty pool → secondaries=[]
        # ----------------------------------------------------------------
        equipped_secondaries: list = []
        if getattr(ship, "max_secondaries", 0) > 0:
            # Build candidate pool across a TL window, mirroring find_item_tl's
            # bidirectional intent without calling it (that can return a TL
            # populated only by deferred/dead-weight items with no fallback).
            _sw_repo = SecondaryWeaponRepository()
            _all_secondary = await _sw_repo.list_all(db)

            # Compute TL window: prefer item_tl, search down to MIN_TECH_LEVEL
            # then up by criminal_max_gear_upgrade (mirrors primary/turret logic).
            _tl_candidates: list[int] = list(range(item_tl, GameConstants.MIN_TECH_LEVEL - 1, -1)) + list(
                range(item_tl + 1, min(GameConstants.MAX_TECH_LEVEL, item_tl + _criminal_max_gear_upgrade) + 1)
            )
            _seen_names: set[str] = set()
            for _sw in _all_secondary:
                if getattr(_sw, "tech_level", -1) not in _tl_candidates:
                    continue
                _subtype = get_secondary_subtype(_sw)
                if _subtype in DEFERRED_SECONDARY_SUBTYPES:
                    continue
                _sw_damage = int(getattr(_sw, "damage", 0) or 0)
                if _sw_damage <= GameConstants.CRIMINAL_SECONDARY_MIN_DAMAGE:
                    continue
                if _sw.name not in _seen_names:
                    _seen_names.add(_sw.name)
                    equipped_secondaries.append(_sw)

            # Sample min(max_secondaries, pool_size) distinct items WITHOUT replacement
            n_pick = min(ship.max_secondaries, len(equipped_secondaries))
            if n_pick > 0:
                equipped_secondaries = random.sample(equipped_secondaries, n_pick)
            else:
                equipped_secondaries = []

        # Knob #4: count each secondary's value ONCE per equipped type (not scaled by rounds).
        secondary_value = sum(getattr(sw, "value", 0) for sw in equipped_secondaries)
        total_value = ship.value + weapon_value + module_value + turret_value + secondary_value

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
            "ship_max_secondaries": getattr(ship, "max_secondaries", 0),
            "weapons": [
                {
                    "name": w.name,
                    "emoji": getattr(w, "emoji", None),
                    "value": w.value,
                    "dps": w.dps,
                    **_extract_weapon_combat_fields(w),
                }
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
                {
                    "name": t.name,
                    "emoji": getattr(t, "emoji", None),
                    "value": t.value,
                    "dps": t.dps,
                    **_extract_weapon_combat_fields(t),
                }
                for t in equipped_turrets
            ],
            "secondaries": [
                {
                    "name": sw.name,
                    "emoji": getattr(sw, "emoji", None),
                    "value": sw.value,
                    "dps": float(getattr(sw, "dps", 0) or 0),
                    "rounds": max(1, GameConstants.CRIMINAL_SECONDARY_ROUNDS.get(get_secondary_subtype(sw), 1)),
                    **_extract_secondary_combat_fields(sw),
                }
                for sw in equipped_secondaries
            ],
            "total_value": total_value,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

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

    async def scrub_player_checks_outside_tier(
        self,
        db: AsyncSession,
        player_id: int,
        guild_id: int,
        new_tier: str,
    ) -> int:
        """Replace this player's checked entries with FORFEITED_CHECK on all active
        bounties the player can no longer reach after a tier change.

        Called by ``PlayerService.promote_player``, ``demote_player``, and
        ``prestige_player`` (each is a tier transition that orphans existing checks).

        Semantics (see /promote design notes):
        - The systems remain "checked" — other players in the affected divisions
          still see ALREADY_CHECKED for them (the ``checked.get(...) != -1`` guard
          treats any non-(-1) entry as locked).
        - The transitioning player's per-system payout is forfeited on capture:
          the payout iterator in ``calc_rewards`` skips any entry with
          ``checker_id <= 0``.
        - The forfeited credits stay un-issued; capture-bonus and other checkers'
          per-system payouts come out of the original pool unchanged.

        Iterates every division except the player's new tier. In practice only
        the player's prior-tier division has matching checks (the /check division
        filter prevents cross-tier checks at write time), so most divisions are
        zero-work scans — but covering all of them keeps the function correct
        regardless of direction (promote / demote / prestige).

        Args:
            db:        Async database session (caller-owned transaction; this method
                       uses ``commit=False``).
            player_id: Player whose check entries should be scrubbed.
            guild_id:  Guild scope.
            new_tier:  The tier the player has just transitioned to (canonical names:
                       "Bronze" / "Silver" / "Gold" / "Platinum").

        Returns:
            Number of bounties mutated.
        """
        new_tier_lower = (new_tier or "").lower()
        affected_divisions = [d for d in ("bronze", "silver", "gold", "platinum") if d != new_tier_lower]
        if not affected_divisions:
            return 0

        from sqlalchemy.orm.attributes import flag_modified

        mutated_count = 0
        for division in affected_divisions:
            active = await self.bounty_repo.get_active_by_guild_and_division(db, guild_id, division)
            for bounty in active:
                checked = dict(bounty.checked)
                changed = False
                for system_name, checker_id in list(checked.items()):
                    if checker_id == player_id:
                        checked[system_name] = FORFEITED_CHECK
                        changed = True
                if changed:
                    bounty.checked = checked
                    # The JSON column otherwise doesn't reliably bump updated_at on
                    # dict mutations — flag it explicitly so the row is dirtied.
                    flag_modified(bounty, "checked")
                    mutated_count += 1
        flogger.info(
            f"scrub_player_checks_below_tier: player_id={player_id} guild_id={guild_id} "
            f"new_tier={new_tier} mutated_bounties={mutated_count}"
        )
        return mutated_count

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
        Awards ``bonus_credits`` (equal to the full winner payout) to the player,
        effectively doubling their total payout.  Also awards XP proportional to
        the bonus credits via ``BOUNTY_REWARD_TO_XP_GAIN_MULT``.

        Args:
            db:            Async database session.
            player_id:     ID of the player to award.
            bonus_credits: Credits to add (should equal the full winner payout).
        """
        player = await self.player_repo.get_by_id(db, player_id)
        if player is None:
            flogger.warning(f"Cannot award combat bonus: player {player_id} not found")
            return
        # Note: guild_config not available in this helper — use global default
        bonus_xp = int(bonus_credits * GameConstants.BOUNTY_REWARD_TO_XP_GAIN_MULT)
        player.credits += bonus_credits
        player.lifetime_credits += bonus_credits
        if not player.classic_mode:
            player.xp += bonus_xp
        flogger.info(f"Awarded {bonus_credits:,} combat bonus (+{bonus_xp} XP) to player {player_id}")

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
        # Load per-guild config for override resolution
        cfg = await self.config_repo.get_by_guild_id(db, guild_id)

        # Step 1: Select criminal
        criminal = await self.select_criminal(db, guild_id, division)
        if criminal is None:
            flogger.warning(f"No available criminals for guild={guild_id} div={division}")
            return None

        # Step 2: Determine tech level
        if tech_level is None:
            # Division TL centers: bronze=1, silver=3, gold=6, platinum=8
            division_tl_map = {"bronze": 1, "silver": 3, "gold": 6, "platinum": 8}
            center_tl = division_tl_map.get(division, 5)
            tech_level = pick_random_item_tl(center_tl)
            # Enforce per-division TL cap so new players are never overwhelmed
            _division_max_tl = resolve_constant(cfg, "division_max_tl", GameConstants.DIVISION_MAX_TL)
            max_tl = _division_max_tl.get(division, 10)
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
        loadout = await self.generate_loadout(db, tech_level, cfg=cfg)

        # Step 6: Calculate reward using the winner-reserve / consolation-pool model.
        # The total reward is seeded by the legacy per-sys formula, but reward_per_sys
        # is now derived from the consolation pool (total minus the winner's reserve),
        # split evenly across the route length.
        _legacy_rps = reward_per_sys_check(tech_level, loadout["total_value"])
        total_reward = _legacy_rps * len(route)

        _winner_reserve_factor = resolve_constant(
            cfg, "bounty_winner_reserve_factor", GameConstants.BOUNTY_WINNER_RESERVE_FACTOR
        )
        winner_reserve = int(total_reward * _winner_reserve_factor)
        consolation_pool = total_reward - winner_reserve
        rps = consolation_pool // len(route) if route else 0

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
        bounties_to_announce: list[tuple[Bounty, bool, CheckResponse]] = []  # (bounty, captured, outcome)
        cooldown_applied = False
        cfg = await self.config_repo.get_by_guild_id(db, guild_id)
        cooldown_seconds = resolve_constant(cfg, "check_cooldown", GameConstants.CHECK_COOLDOWN)
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
                cfg=cfg,
            )
            outcomes.append(outcome)
            if announce_info is not None:
                announce_bounty, announce_captured = announce_info
                bounties_to_announce.append((announce_bounty, announce_captured, outcome))

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
        for bounty, captured, _outcome in bounties_to_announce:
            try:
                await self._edit_bounty_announcement(db, bounty, captured=captured)
            except Exception as e:  # pylint: disable=broad-exception-caught
                flogger.warning(
                    f"Per-bounty announcement edit failed (bounty {bounty.id}, "
                    f"player {player_id}, system {system_name!r}): {e}"
                )
            # Capture payout embed is rendered by the cog from the check response fields.

        # If any bounty was captured, push the updated active bounty list to the gateway
        # autocomplete cache so the captured bounty is immediately removed from the /route
        # and /bounties dropdowns.  Non-fatal — a push failure never blocks the check response.
        any_captured = any(outcome.result == CheckResult.CORRECT for outcome in outcomes)
        if any_captured:
            await self._push_bounty_cache_after_capture(db, guild_id)

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
        cfg=None,
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
                    criminal_name=bounty.criminal_name,
                    message=f"System {system_name} already checked",
                    division=division,
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
                rewards = await self.calc_rewards(db, bounty, cfg=cfg)
                await self.distribute_rewards(db, bounty, rewards)
                payout_breakdown = await self._build_payout_breakdown(db, rewards)

                winner_reward = next((r.credits_earned for r in rewards if r.is_winner), 0)

                bonus_won = False
                total_reward = winner_reward
                if not _no_ship:
                    _pvc_dr = resolve_constant(cfg, "pvc_damage_reduction", GameConstants.PVC_DAMAGE_REDUCTION)
                    fight_results = await self.combat_service.fight_ships(
                        player_loadout,
                        criminal_loadout,
                        context="bounty_bonus",
                        log_result=True,
                        pvc_damage_reduction=_pvc_dr,
                        session=db,
                        guild_id=player.guild_id,
                        combatant1_user_id=player.user_id,
                        combatant2_user_id=None,  # NPC side
                    )
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
                        reward_per_sys=getattr(bounty, "reward_per_sys", None),
                        route_length=len(list(getattr(bounty, "route", None) or [])),
                        payout_breakdown=payout_breakdown,
                    ),
                    (bounty, True),
                )

            # SILVER / GOLD / PLATINUM: Mandatory combat gate.
            if _no_ship:
                duel_won = True
                fight_results = None
            else:
                _pvc_dr_silver = resolve_constant(cfg, "pvc_damage_reduction", GameConstants.PVC_DAMAGE_REDUCTION)
                fight_results = await self.combat_service.fight_ships(
                    player_loadout,
                    criminal_loadout,
                    context="bounty_pvc",
                    log_result=True,
                    pvc_damage_reduction=_pvc_dr_silver,
                    session=db,
                    guild_id=player.guild_id,
                    combatant1_user_id=player.user_id,
                    combatant2_user_id=None,  # NPC side
                )
                duel_won = (fight_results.winner_name == player_loadout.ship_name) or fight_results.is_stalemate

            if duel_won:
                rewards = await self.calc_rewards(db, bounty, cfg=cfg)
                await self.distribute_rewards(db, bounty, rewards)
                payout_breakdown = await self._build_payout_breakdown(db, rewards)
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
                        total_reward=winner_reward,
                        combat_result=_serialize_fight_results(fight_results) if fight_results else None,
                        reward_per_sys=getattr(bounty, "reward_per_sys", None),
                        route_length=len(list(getattr(bounty, "route", None) or [])),
                        payout_breakdown=payout_breakdown,
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
            close_threshold = resolve_constant(cfg, "close_bounty_threshold", GameConstants.CLOSE_BOUNTY_THRESHOLD)
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
                criminal_name=bounty.criminal_name,
                message=inc_message,
                division=division,
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

    async def _push_bounty_cache_after_capture(self, db: AsyncSession, guild_id: int) -> None:
        """Push the updated active bounty list to the gateway autocomplete cache after a capture.

        Called from check_bounty when at least one bounty was captured so that the
        captured bounty is immediately removed from the /route and /bounties autocomplete
        dropdowns without waiting for the next spawn/expire push or TTL expiry.

        Non-fatal — logs a warning on failure and never blocks the check response.

        Args:
            db:       Async database session (within an active session block).
            guild_id: The Discord guild ID to push bounties for.
        """
        try:
            import os

            import httpx

            bounties_raw = await self.bounty_repo.get_active_by_guild(db, guild_id)

            # Serialise ORM objects to plain dicts (exclude SQLAlchemy internal keys).
            bounty_dicts: list[dict] = []
            for b in bounties_raw:
                if isinstance(b, dict):
                    bounty_dicts.append(b)
                else:
                    d = {k: v for k, v in b.__dict__.items() if not k.startswith("_")}
                    # Convert ALL datetime fields to ISO strings for JSON serialisation.
                    for key, val in list(d.items()):
                        if hasattr(val, "isoformat"):
                            d[key] = val.isoformat()
                    bounty_dicts.append(d)

            gateway_host = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
            gateway_port = os.getenv("GATEWAY_PORT", "7999")
            gateway_url = f"http://{gateway_host}:{gateway_port}/api/v1"
            token = os.getenv("INTERNAL_AUTH_TOKEN", "")
            headers = {"X-Internal-Auth": token} if token else {}

            # SSRF guard: coerce to int — non-numeric values raise ValueError,
            # caught by the surrounding try/except as a warning.
            safe_guild = int(guild_id)
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{gateway_url}/internal/autocomplete/bounty-cache/{quote(str(safe_guild), safe='')}",
                    json={"bounties": bounty_dicts},
                    headers=headers,
                    timeout=5.0,
                )
            resp.raise_for_status()
            flogger.debug(
                f"check_bounty: pushed bounty cache after capture for guild={guild_id} remaining={len(bounty_dicts)}"
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.warning(
                f"check_bounty: failed to push bounty cache to gateway after capture for guild={guild_id}: {e}"
            )

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

    async def _post_capture_payout(self, db: AsyncSession, guild_id: int, bounty, outcome) -> None:
        """Non-fatal: POST a payout embed to hunting_channel_id after a capture.

        Resolves the winner's display name from their user record, builds a short
        "💰 Payout" embed, and POSTs it to the guild's configured hunting_channel_id.

        Args:
            db:       Async database session.
            guild_id: Discord guild ID.
            bounty:   The captured Bounty ORM instance.
            outcome:  The CheckResponse for this capture (carries reward info).
        """
        import os

        import httpx as _httpx
        from persist.repositories.config_repository import ConfigRepository
        from persist.repositories.user_repository import UserRepository
        from utils.bounty_announcement_payload import build_capture_payout_embed

        try:
            config_repo = ConfigRepository()
            config = await config_repo.get_by_guild_id(db, guild_id)
            if not config:
                return
            hunting_channel_id = getattr(config, "hunting_channel_id", None)
            if not hunting_channel_id:
                return

            # Resolve winner display name: prefer display_name, fall back to discord_username
            winner_name = "A bounty hunter"
            win_user_id = getattr(bounty, "win_user_id", None)
            if win_user_id:
                user_repo = UserRepository()
                user = await user_repo.get_by_discord_id(db, win_user_id)
                if user:
                    winner_name = (
                        getattr(user, "display_name", None)
                        or getattr(user, "discord_username", None)
                        or "A bounty hunter"
                    )

            reward = getattr(outcome, "reward", None) or getattr(bounty, "reward", 0)
            total_reward = getattr(outcome, "total_reward", None)
            bonus_won = getattr(outcome, "bonus_won", False)

            # Pass reward_per_sys and route_length from the bounty if available
            reward_per_sys = getattr(bounty, "reward_per_sys", None)
            route = getattr(bounty, "route", None)
            route_length = len(list(route)) if route is not None else None

            embed_dict = build_capture_payout_embed(
                criminal_name=bounty.criminal_name,
                division=getattr(bounty, "division", ""),
                reward=reward,
                winner_name=winner_name,
                total_reward=total_reward,
                bonus_won=bonus_won,
                reward_per_sys=reward_per_sys,
                route_length=route_length,
                combat_result=getattr(outcome, "combat_result", None),
            )

            gateway_host = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
            gateway_port = os.getenv("GATEWAY_PORT", "7999")
            gateway_url = f"http://{gateway_host}:{gateway_port}/api/v1"

            async with _httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{gateway_url}/channels/{hunting_channel_id}/messages",
                    json={"content": embed_dict, "text_content": None},
                    timeout=5.0,
                )
            resp.raise_for_status()
            flogger.debug(
                f"_post_capture_payout: posted payout embed for bounty={bounty.id} "
                f"guild={guild_id} channel={hunting_channel_id}"
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.warning(f"_post_capture_payout: failed for bounty={bounty.id}: {e}")

    # ------------------------------------------------------------------
    # Reward Calculation & Distribution
    # ------------------------------------------------------------------

    async def calc_rewards(
        self,
        db: AsyncSession,
        bounty: Bounty,
        cfg=None,
    ) -> list[RewardInfo]:
        """Calculate rewards for all contributors to a completed bounty.

        Algorithm (winner-reserve / consolation-pool model):
        1. Compute winner_reserve = max(0, floor(bounty.reward * BOUNTY_WINNER_RESERVE_FACTOR))
        2. consolation_pool = bounty.reward - winner_reserve
        3. For each non-winner player: credit_reward = reward_per_sys * systems_checked
           (deducted from consolation_pool; pool is never allowed below 0)
        4. Winner gets: winner_reserve + max(0, remaining consolation_pool)
        5. XP rules:
           - Failed checkers: XP = 0 (no XP for missed checks)
           - Winner: xp_earned = int(total_winner_credits * BOUNTY_REWARD_TO_XP_GAIN_MULT)

        Args:
            db:     Async database session (unused directly, kept for API consistency).
            bounty: Completed bounty to calculate rewards for.

        Returns:
            List of RewardInfo for each contributing player.
        """
        _winner_reserve_factor = resolve_constant(
            cfg, "bounty_winner_reserve_factor", GameConstants.BOUNTY_WINNER_RESERVE_FACTOR
        )
        winner_reserve = max(0, int(bounty.reward * _winner_reserve_factor))
        consolation_pool = bounty.reward - winner_reserve
        rps = bounty.reward_per_sys
        winner_id = bounty.checked.get(bounty.answer, -1)

        # Count systems checked per player
        player_checks: dict[int, int] = {}
        for _system, checker_id in bounty.checked.items():
            # Sentinel values (< 0): -1 = unchecked, -2 = checked-but-forfeited
            # (player promoted past this bounty's division before payout).
            # Both are excluded from reward distribution.
            if checker_id > 0:
                player_checks[checker_id] = player_checks.get(checker_id, 0) + 1

        rewards: list[RewardInfo] = []

        # Non-winner contributors: pay rps * checks from consolation pool; XP = 0
        for player_id, check_count in player_checks.items():
            if player_id == winner_id:
                continue
            credit_reward = rps * check_count
            # Cap deduction so consolation_pool never goes negative
            credit_reward = min(credit_reward, consolation_pool)
            consolation_pool -= credit_reward

            rewards.append(
                RewardInfo(
                    player_id=player_id,
                    credits_earned=credit_reward,
                    xp_earned=0,  # No XP for failed (non-winning) checkers
                    is_winner=False,
                    systems_checked_count=check_count,
                )
            )

        # Winner gets winner_reserve + any remaining consolation pool
        if winner_id != -1:
            remaining_consolation = max(0, consolation_pool)
            total_winner_credits = winner_reserve + remaining_consolation
            _xp_mult = resolve_constant(
                cfg, "bounty_reward_to_xp_gain_mult", GameConstants.BOUNTY_REWARD_TO_XP_GAIN_MULT
            )
            xp_reward = int(total_winner_credits * _xp_mult)
            winner_checks = player_checks.get(winner_id, 0)

            rewards.append(
                RewardInfo(
                    player_id=winner_id,
                    credits_earned=total_winner_credits,
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
        # Store the Discord user ID (User.id = snowflake), NOT the player table PK.
        # modified_players already holds the fetched Player objects; the winning
        # player's .user_id FK is the Discord snowflake we need.
        _winning_player = next(
            (p for p in modified_players if any(r.player_id == p.id and r.is_winner for r in rewards)),
            None,
        )
        bounty.win_user_id = _winning_player.user_id if _winning_player else None
        await self.bounty_repo.update(db, bounty, commit=False)
        await db.commit()

        # Refresh all modified players for accurate state.
        for player in modified_players:
            await db.refresh(player)

        return rewards

    async def _build_payout_breakdown(
        self,
        db: AsyncSession,
        rewards: list[RewardInfo],
    ) -> list[dict]:
        """Build a per-player payout breakdown list for embed rendering.

        Fetches each player by ID to get their display_name, then assembles
        one dict per player with player_display_name, role, and amount.

        Args:
            db:      Async database session.
            rewards: Reward list from :meth:`calc_rewards` (post-distribution).

        Returns:
            List of dicts with keys: player_display_name, role, amount.
            role is 'capture claim' for the winner, 'system check' for others.
        """
        payout_breakdown: list[dict] = []
        for reward in rewards:
            player = await self.player_repo.get_by_id(db, reward.player_id)
            if player is None:
                continue
            # Use display_name if available and non-empty, else fall back to str(user_id)
            display_name = getattr(player, "display_name", None)
            if not display_name:
                display_name = str(getattr(player, "user_id", reward.player_id))
            role = "capture claim" if reward.is_winner else "system check"
            payout_breakdown.append(
                {
                    "player_display_name": display_name,
                    "role": role,
                    "amount": reward.credits_earned,
                }
            )
        return payout_breakdown

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
