"""
Loadout Builder for BountyBot combat system.

Builds ShipLoadout objects from various data sources:
- Player's active ship with equipped items (from DB)
- Criminal ship JSON dict (from bounty's criminal_ship JSONB column)

This separates loadout construction from the combat simulation,
making both easier to test independently.
"""

from __future__ import annotations

from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

from services.combat_models import ModuleStats, ShipLoadout, WeaponStats

flogger = bblogger.get_logger("loadout-builder")

# Sentinel value for "key not found" to distinguish from None
_MISSING = object()


def _get_extra(extra: dict, snake_key: str, camel_key: str, default):
    """Retrieve a value from extra_atts, trying snake_case then camelCase.

    Args:
        extra: The extra_atts dict (may have camelCase or snake_case keys).
        snake_key: Snake-case key to look up first (e.g. "dps_multiplier").
        camel_key: Camel-case key as fallback (e.g. "dpsMultiplier").
        default: Default value if neither key is present.

    Returns:
        The found value, or ``default`` if neither key exists.
    """
    val = extra.get(snake_key, _MISSING)
    if val is _MISSING:
        val = extra.get(camel_key, default)
    return val


def _module_stats_from_extra(name: str, extra: dict) -> ModuleStats:
    """Build a ModuleStats from a module's extra_atts dict.

    Maps both snake_case and camelCase variant keys to handle
    data stored by the module_repository (camelCase, from JSON files)
    and any future snake_case data sources.

    Args:
        name: Module display name.
        extra: The extra_atts dict from the Module ORM model or criminal_ship dict.

    Returns:
        ModuleStats with all relevant combat fields populated.
    """
    armour = int(_get_extra(extra, "armour", "armour", 0))
    armour_multiplier = float(_get_extra(extra, "armour_multiplier", "armourMultiplier", 1.0))
    shield = int(_get_extra(extra, "shield", "shield", 0))
    shield_multiplier = float(_get_extra(extra, "shield_multiplier", "shieldMultiplier", 1.0))
    dps = int(_get_extra(extra, "dps", "dps", 0))
    dps_multiplier = float(_get_extra(extra, "dps_multiplier", "dpsMultiplier", 1.0))

    flogger.trace(
        f"Module stats for {name!r}: armour={armour}, armour_mult={armour_multiplier}, "
        f"shield={shield}, shield_mult={shield_multiplier}, dps={dps}, dps_mult={dps_multiplier}"
    )

    return ModuleStats(
        name=name,
        armour=armour,
        armour_multiplier=armour_multiplier,
        shield=shield,
        shield_multiplier=shield_multiplier,
        dps=dps,
        dps_multiplier=dps_multiplier,
    )


class LoadoutBuilder:
    """Builds ShipLoadout objects from various data sources for combat simulation."""

    @staticmethod
    async def from_player(db: AsyncSession, player_id: int) -> ShipLoadout:
        """Build ShipLoadout from a player's active ship and equipped items.

        Steps:
        1. Get player's active PlayerShip (via PlayerRepository + PlayerShipRepository)
        2. Get the Ship model for base armour stats
        3. For each equipped weapon name → look up in DB → create WeaponStats(name, dps)
        4. For each equipped turret name → look up in DB → create WeaponStats(name, dps)
        5. For each equipped module name → look up in DB → create ModuleStats from extra_atts
        6. Return ShipLoadout(ship_name, base_armour, weapons, turrets, modules)

        If the player has no active ship, returns a default unarmed loadout.

        Args:
            db: SQLAlchemy async session.
            player_id: Primary key of the Player.

        Returns:
            ShipLoadout ready for use in CombatService.
        """
        from persist.models.module import Module
        from persist.models.player_ship import PlayerShip
        from persist.models.ship import Ship
        from persist.repositories.item_repository import ItemRepository
        from persist.repositories.player_repository import PlayerRepository
        from sqlalchemy import select

        flogger.debug(f"Building player loadout for player_id={player_id}")

        player_repo = PlayerRepository()
        item_repo = ItemRepository()

        # 1. Get player
        player = await player_repo.get_by_id(db, player_id)
        if player is None:
            flogger.warning(f"Player {player_id} not found — returning default unarmed loadout")
            return ShipLoadout(ship_name="Unarmed", base_armour=100)

        # 2. Get active PlayerShip (explicit query to avoid lazy-loading issues)
        if not player.active_ship_id:
            flogger.debug(f"Player {player_id} has no active ship — returning default unarmed loadout")
            return ShipLoadout(ship_name="Unarmed", base_armour=100)

        ps_result = await db.execute(select(PlayerShip).where(PlayerShip.id == player.active_ship_id))
        player_ship = ps_result.scalars().first()

        if player_ship is None:
            flogger.warning(f"PlayerShip {player.active_ship_id} not found — returning default unarmed loadout")
            return ShipLoadout(ship_name="Unarmed", base_armour=100)

        ship_name = player_ship.ship_name

        # 3. Get static Ship data for base armour
        ship_result = await db.execute(select(Ship).where(Ship.name == ship_name))
        ship = ship_result.scalars().first()
        base_armour = ship.armour if ship else 100

        flogger.debug(f"Player {player_id} active ship: {ship_name!r}, base_armour={base_armour}")

        # 4. Build weapon stats
        weapons: list[WeaponStats] = []
        for w_name in player_ship.weapons or []:
            item = await item_repo.get_by_name(db, w_name, item_type="primary_weapon")
            if item is None:
                item = await item_repo.get_by_name(db, w_name)
            dps = float(getattr(item, "dps", 0) or 0) if item else 0.0
            weapons.append(WeaponStats(name=w_name, dps=dps))
            flogger.trace(f"Weapon {w_name!r} dps={dps}")

        # 5. Build turret stats
        turrets: list[WeaponStats] = []
        for t_name in player_ship.turrets or []:
            item = await item_repo.get_by_name(db, t_name, item_type="turret_weapon")
            if item is None:
                item = await item_repo.get_by_name(db, t_name)
            dps = float(getattr(item, "dps", 0) or 0) if item else 0.0
            turrets.append(WeaponStats(name=t_name, dps=dps))
            flogger.trace(f"Turret {t_name!r} dps={dps}")

        # 6. Build module stats
        modules: list[ModuleStats] = []
        for m_name in player_ship.modules or []:
            mod_result = await db.execute(select(Module).where(Module.name == m_name))
            mod = mod_result.scalars().first()
            if mod is None:
                item = await item_repo.get_by_name(db, m_name, item_type="module")
                mod = item
            if mod:
                extra = mod.extra_atts or {}
                modules.append(_module_stats_from_extra(m_name, extra))
            else:
                flogger.debug(f"Module {m_name!r} not found in DB — using zero-effect ModuleStats")
                modules.append(ModuleStats(name=m_name))

        flogger.debug(
            f"Player {player_id} loadout built: ship={ship_name!r}, base_armour={base_armour}, "
            f"weapons={len(weapons)}, turrets={len(turrets)}, modules={len(modules)}"
        )

        return ShipLoadout(
            ship_name=ship_name,
            base_armour=base_armour,
            weapons=weapons,
            turrets=turrets,
            modules=modules,
        )

    @staticmethod
    def from_criminal_ship(criminal_ship: dict) -> ShipLoadout:
        """Build ShipLoadout from a bounty's criminal_ship JSON dict.

        The criminal_ship dict is produced by BountyService._build_criminal_loadout_dict()
        and stored in the ``Bounty.criminal_ship`` JSONB column:

        {
            "ship_name": "Betty",
            "ship_emoji": "...",
            "ship_armour": 95,
            "armor_hp": 95,
            "shield_hp": 0,
            "total_hp": 95,
            "weapons": [{"name": "...", "emoji": "...", "dps": 5.2, "value": 1000}],
            "turrets": [{"name": "...", "emoji": "...", "dps": 3.1, "value": 500}],
            "modules": [
                {
                    "name": "...", "emoji": "...", "type": "ArmourModule",
                    "value": 500, "tech_level": 1,
                    "extra_atts": {"armour": 40, ...},
                }
            ],
        }

        Steps:
        1. Extract ship_name, ship_armour (base armour)
        2. For each weapon dict → create WeaponStats(name, dps)
        3. For each turret dict → create WeaponStats(name, dps)
        4. For each module dict → create ModuleStats from extra_atts
        5. Return ShipLoadout

        Args:
            criminal_ship: Dict from Bounty.criminal_ship JSONB column.

        Returns:
            ShipLoadout ready for use in CombatService.
        """
        ship_name = criminal_ship.get("ship_name", "Unknown")
        base_armour = criminal_ship.get("ship_armour", 100)

        flogger.debug(f"Building criminal loadout: ship={ship_name!r}, base_armour={base_armour}")

        # Weapons
        weapons: list[WeaponStats] = []
        for w in criminal_ship.get("weapons", []):
            dps = float(w.get("dps", 0) or 0)
            weapons.append(WeaponStats(name=w["name"], dps=dps))
            flogger.trace(f"Criminal weapon {w['name']!r} dps={dps}")

        # Turrets
        turrets: list[WeaponStats] = []
        for t in criminal_ship.get("turrets", []):
            dps = float(t.get("dps", 0) or 0)
            turrets.append(WeaponStats(name=t["name"], dps=dps))
            flogger.trace(f"Criminal turret {t['name']!r} dps={dps}")

        # Modules
        modules: list[ModuleStats] = []
        for m in criminal_ship.get("modules", []):
            extra = m.get("extra_atts") or {}
            modules.append(_module_stats_from_extra(m["name"], extra))

        flogger.debug(
            f"Criminal loadout built: ship={ship_name!r}, base_armour={base_armour}, "
            f"weapons={len(weapons)}, turrets={len(turrets)}, modules={len(modules)}"
        )

        return ShipLoadout(
            ship_name=ship_name,
            base_armour=base_armour,
            weapons=weapons,
            turrets=turrets,
            modules=modules,
        )
