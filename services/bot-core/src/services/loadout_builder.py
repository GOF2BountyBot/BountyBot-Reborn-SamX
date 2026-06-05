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
from services.game_constants import GameConstants

flogger = bblogger.get_logger("loadout-builder")

# Sentinel value for "key not found" to distinguish from None
_MISSING = object()

# Fallback constants for legacy/malformed criminal weapon dicts that lack combat fields.
# _DEFAULT_WEAPON_CADENCE_MS: assume a 1-second fire cycle when loading_speed_ms is absent.
# _DEFAULT_WEAPON_RANGE_M: non-zero floor so the range gate never silently blocks all shots.
#   Chosen below real T1 weapon ranges (1300-1400 m) but above 0 so the weapon fires once
#   ships close to within ~800 m; guards against fully-malformed JSONB with missing range.
_DEFAULT_WEAPON_CADENCE_MS: int = 1000
_DEFAULT_WEAPON_RANGE_M: float = 800.0


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


def _module_stats_from_extra(name: str, extra: dict, *, module_type: str = "") -> ModuleStats:
    """Build a ModuleStats from a module's extra_atts dict.

    Maps both snake_case and camelCase variant keys to handle
    data stored by the module_repository (camelCase, from JSON files)
    and any future snake_case data sources.

    The ``extra`` argument is the OUTER extra_atts dict as stored in the DB.
    For T8 fields (effect_pct, effect_duration_ms, loading_speed_ms), the
    authoritative values live in the INNER extra_atts nested dict:

        outer = mod.extra_atts  (e.g. {"duration": 10, "extra_atts": {"duration_ms": 10000, ...}})
        inner = outer.get("extra_atts", outer)   ← combat-relevant snake_case fields live here

    The HP/DPS fields (armour, shield, etc.) are also checked in the inner dict
    as a fallback, because they live in the inner extra_atts in some module types.

    Args:
        name: Module display name.
        extra: The OUTER extra_atts dict from the Module ORM model or criminal_ship dict.
        module_type: STI discriminator string (Item.type), e.g. "CloakModule". Empty string
                     for legacy callers that do not supply it.

    Returns:
        ModuleStats with all relevant combat fields populated.
    """
    # Resolve inner extra_atts (T6/T7/T8 pattern: combat-relevant fields live in nested dict)
    inner: dict = extra.get("extra_atts", extra) if isinstance(extra, dict) else {}

    armour = int(_get_extra(extra, "armour", "armour", 0))
    armour_multiplier = float(_get_extra(extra, "armour_multiplier", "armourMultiplier", 1.0))
    shield = int(_get_extra(extra, "shield", "shield", 0))
    shield_multiplier = float(_get_extra(extra, "shield_multiplier", "shieldMultiplier", 1.0))
    dps = int(_get_extra(extra, "dps", "dps", 0))
    dps_multiplier = float(_get_extra(extra, "dps_multiplier", "dpsMultiplier", 1.0))

    # Also check inner dict for HP/DPS fields (some modules store them there)
    if armour == 0:
        armour = int(_get_extra(inner, "armour", "armour", 0))
    if shield == 0:
        shield = int(_get_extra(inner, "shield", "shield", 0))

    # T5 PrimaryWeaponMod fields (also in inner)
    damage_pct = int(_get_extra(inner, "damage_pct", "damagePct", 0))
    fire_rate_pct = int(_get_extra(inner, "fire_rate_pct", "fireRatePct", 0))

    # Shield regen fields (T3 — also in inner)
    shield_recharge_ms = int(_get_extra(inner, "shield_recharge_ms", "shieldRechargeMs", 0))
    shield_recharge_rate = float(_get_extra(inner, "shield_recharge_rate", "shieldRechargeRate", 0.0))
    repair_rate = float(_get_extra(inner, "repair_rate", "repairRate", 0.0))

    # RepairBotModule override: map seed HPps → locked pct constants.
    # Prefer an explicit repair_pct_per_sec seed key (future-proof); fall back to
    # HPps thresholding (>=15 → II, else → I — never inert even for unknown future bots).
    if module_type == "RepairBotModule":
        explicit = _get_extra(inner, "repair_pct_per_sec", "repairPctPerSec", None)
        if explicit is not None:
            repair_rate = float(explicit)  # future authoritative seed key (zero code edit)
        else:
            hpps = int(_get_extra(inner, "HPps", "HPps", 0) or 0)
            if hpps >= 15:
                repair_rate = GameConstants.KETAR_II_REPAIR_PCT_PER_SEC  # id129 HPps=15
            else:
                repair_rate = GameConstants.KETAR_I_REPAIR_PCT_PER_SEC  # id122 HPps=7 + safe default

    # T8 activation-rule fields — all in inner extra_atts
    effect_pct = float(_get_extra(inner, "effect_pct", "effectPct", 0.0))
    # Cloak/Booster store effect window as "duration_ms" in seed; T8 reads it as effect_duration_ms
    effect_duration_ms = int(_get_extra(inner, "duration_ms", "durationMs", 0))
    loading_speed_ms = int(_get_extra(inner, "loading_speed_ms", "loadingSpeedMs", 0))

    flogger.trace(
        f"Module stats for {name!r}: type={module_type!r}, armour={armour}, armour_mult={armour_multiplier}, "
        f"shield={shield}, shield_mult={shield_multiplier}, dps={dps}, dps_mult={dps_multiplier}, "
        f"effect_pct={effect_pct}, effect_duration_ms={effect_duration_ms}, loading_speed_ms={loading_speed_ms}"
    )

    return ModuleStats(
        name=name,
        armour=armour,
        armour_multiplier=armour_multiplier,
        shield=shield,
        shield_multiplier=shield_multiplier,
        dps=dps,
        dps_multiplier=dps_multiplier,
        shield_recharge_ms=shield_recharge_ms,
        shield_recharge_rate=shield_recharge_rate,
        repair_rate=repair_rate,
        module_type=module_type,
        damage_pct=damage_pct,
        fire_rate_pct=fire_rate_pct,
        effect_pct=effect_pct,
        effect_duration_ms=effect_duration_ms,
        loading_speed_ms=loading_speed_ms,
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

        # 4. Build primary weapon stats (D0.5 T6 true-up: populate damage_per_shot/loading_speed_ms/range_m)
        # DB extra_atts structure: {"builtIn": ..., "extra_atts": {"loading_speed_ms": ..., "range_m": ..., ...}}
        # The combat-relevant snake_case fields live in the INNER extra_atts dict.
        weapons: list[WeaponStats] = []
        for w_name in player_ship.weapons or []:
            item = await item_repo.get_by_name(db, w_name, item_type="primary_weapon")
            if item is None:
                item = await item_repo.get_by_name(db, w_name)
            dps = float(getattr(item, "dps", 0) or 0) if item else 0.0
            outer = getattr(item, "extra_atts", None) or {}
            # Unpack inner extra_atts (DB nesting); fall back to outer for legacy flat dicts
            inner = outer.get("extra_atts", outer) if isinstance(outer, dict) else {}
            dmg = inner.get("damage_per_shot") or inner.get("damage") or getattr(item, "damage_per_shot", None)
            dmg_val = float(dmg) if dmg is not None else None
            spd = int(inner.get("loading_speed_ms", 0) or 0)
            rng_m = float(inner.get("range_m", 0.0) or 0.0)
            weapons.append(
                WeaponStats(name=w_name, dps=dps, damage_per_shot=dmg_val, loading_speed_ms=spd, range_m=rng_m)
            )
            flogger.trace(
                f"Weapon {w_name!r} dps={dps} damage_per_shot={dmg_val} loading_speed_ms={spd} range_m={rng_m}"
            )

        # 5. Build turret stats (T7 true-up: populate automatic/subtype/loading_speed_ms/range_m)
        # DB layout: TurretWeapon.automatic is a typed DB column (not in extra_atts).
        # Inner extra_atts contains: loading_speed_ms, range_m, damage_per_shot, subtype (plasma-collectors only).
        # damage_per_shot is read from seed when present; derived as dps × loading_speed_ms/1000 as fallback.
        from persist.models.turret_weapon import TurretWeapon
        from sqlalchemy import select as _select_tw  # avoid name collision with earlier `select`

        turrets: list[WeaponStats] = []
        for t_name in player_ship.turrets or []:
            tw_result = await db.execute(_select_tw(TurretWeapon).where(TurretWeapon.name == t_name))
            tw_item = tw_result.scalars().first()
            if tw_item is None:
                tw_item = await item_repo.get_by_name(db, t_name, item_type="turret_weapon")
            if tw_item is None:
                tw_item = await item_repo.get_by_name(db, t_name)
            dps = float(getattr(tw_item, "dps", 0) or 0) if tw_item else 0.0
            # automatic: typed DB column on TurretWeapon (True=auto-turret, False=manual-turret)
            tw_automatic = bool(getattr(tw_item, "automatic", False)) if tw_item else False
            tw_outer = getattr(tw_item, "extra_atts", None) or {}
            # Unpack inner extra_atts (DB nesting); fall back to outer for legacy flat dicts (tests)
            tw_extra = tw_outer.get("extra_atts", tw_outer) if isinstance(tw_outer, dict) else {}
            tw_spd = int(tw_extra.get("loading_speed_ms", 0) or 0)
            tw_rng = float(tw_extra.get("range_m", 0.0) or 0.0)
            tw_subtype = tw_extra.get("subtype", "")
            # Prefer explicit damage_per_shot from seed; derive from dps × cycle_time only as fallback
            _tw_dmg_explicit = tw_extra.get("damage_per_shot")
            tw_dmg: float | None = None
            if _tw_dmg_explicit is not None:
                tw_dmg = float(_tw_dmg_explicit)
            elif tw_spd > 0 and dps > 0:
                tw_dmg = dps * tw_spd / 1000.0
            turrets.append(
                WeaponStats(
                    name=t_name,
                    dps=dps,
                    damage_per_shot=tw_dmg,
                    loading_speed_ms=tw_spd,
                    range_m=tw_rng,
                    subtype=tw_subtype,
                    automatic=tw_automatic,
                )
            )
            flogger.trace(
                f"Turret {t_name!r} dps={dps} automatic={tw_automatic} subtype={tw_subtype!r} "
                f"loading_speed_ms={tw_spd} range_m={tw_rng} damage_per_shot={tw_dmg}"
            )

        # 6. Build module stats
        # Pass module_type (Item.type STI discriminator) so T5/T8 detection works with builder-fed loadouts.
        modules: list[ModuleStats] = []
        for m_name in player_ship.modules or []:
            mod_result = await db.execute(select(Module).where(Module.name == m_name))
            mod = mod_result.scalars().first()
            if mod is None:
                item = await item_repo.get_by_name(db, m_name, item_type="module")
                mod = item
            if mod:
                extra = mod.extra_atts or {}
                mod_type = getattr(mod, "type", "") or ""
                modules.append(_module_stats_from_extra(m_name, extra, module_type=mod_type))
            else:
                flogger.debug(f"Module {m_name!r} not found in DB — using zero-effect ModuleStats")
                modules.append(ModuleStats(name=m_name))

        # 6a. Build secondary weapon stats (D0.5 T6)
        # DB extra_atts structure: {"builtIn": ..., "loading speed": ..., "extra_atts": {"loading_speed_ms": ..., ...}}
        # Combat-relevant snake_case fields (subtype, loading_speed_ms, range_m, burst_count, emp_damage,
        # magnitude_m, steerable) live in the INNER extra_atts dict; damage comes from sw_item.damage column.
        # Local import matches the pattern used for Module/PlayerShip/Ship above;
        # all ORM model imports are kept function-local to avoid SQLAlchemy setup ordering issues.
        from persist.models.secondary_weapon import SecondaryWeapon

        # CI-16: read secondary_ammo sidecar dict (ship-level rounds per weapon name)
        _secondary_ammo_map: dict[str, int] = dict(getattr(player_ship, "secondary_ammo", None) or {})

        secondary_weapons: list[WeaponStats] = []
        for sw_name in getattr(player_ship, "secondary_weapons", None) or []:
            sw_result = await db.execute(select(SecondaryWeapon).where(SecondaryWeapon.name == sw_name))
            sw_item = sw_result.scalars().first()
            if sw_item is None:
                sw_item = await item_repo.get_by_name(db, sw_name, item_type="secondary_weapon")
            if sw_item is None:
                sw_item = await item_repo.get_by_name(db, sw_name)
            sw_outer = getattr(sw_item, "extra_atts", None) or {}
            # Unpack inner extra_atts (DB nesting); fall back to outer for legacy flat dicts (tests)
            sw_extra = sw_outer.get("extra_atts", sw_outer) if isinstance(sw_outer, dict) else {}
            sw_dps = float(getattr(sw_item, "dps", 0) or 0) if sw_item else 0.0
            sw_damage = int(getattr(sw_item, "damage", 0) or 0) if sw_item else 0
            sw_spd = int(sw_extra.get("loading_speed_ms", 0) or 0)
            sw_rng = float(sw_extra.get("range_m", 0.0) or 0.0)
            sw_subtype = sw_extra.get("subtype", "")
            sw_burst = int(sw_extra.get("burst_count", 0) or 0)
            sw_emp = int(sw_extra.get("emp_damage", 0) or 0)
            sw_mag = float(sw_extra.get("magnitude_m", 0.0) or 0.0)
            sw_steer = bool(sw_extra.get("steerable", False))
            # CI-16: look up ammo from sidecar; None = infinite (weapon not in map or map absent)
            sw_ammo: int | None = _secondary_ammo_map.get(sw_name)
            secondary_weapons.append(
                WeaponStats(
                    name=sw_name,
                    dps=sw_dps,
                    damage_per_shot=float(sw_damage),
                    loading_speed_ms=sw_spd,
                    range_m=sw_rng,
                    subtype=sw_subtype,
                    burst_count=sw_burst,
                    emp_damage=sw_emp,
                    magnitude_m=sw_mag,
                    steerable=sw_steer,
                    ammo=sw_ammo,
                )
            )
            flogger.trace(
                f"Secondary {sw_name!r} subtype={sw_subtype!r} damage={sw_damage} "
                f"loading_speed_ms={sw_spd} range_m={sw_rng} ammo={sw_ammo!r}"
            )

        # T8: Read ship's built-in modules (e.g. U'tool for Scimitar/Specter) for §10 supersession
        ship_builtin_modules: list[str] = list(getattr(ship, "builtin_modules", None) or [])

        flogger.debug(
            f"Player {player_id} loadout built: ship={ship_name!r}, base_armour={base_armour}, "
            f"weapons={len(weapons)}, turrets={len(turrets)}, modules={len(modules)}, "
            f"secondary_weapons={len(secondary_weapons)}, builtin_modules={ship_builtin_modules}"
        )

        return ShipLoadout(
            ship_name=ship_name,
            base_armour=base_armour,
            manual_turret_mode=getattr(player_ship, "manual_turret_mode", False),
            weapons=weapons,
            turrets=turrets,
            modules=modules,
            secondary_weapons=secondary_weapons,
            builtin_modules=ship_builtin_modules,
        )

    @staticmethod
    def from_criminal_ship(criminal_ship: dict) -> ShipLoadout:
        """Build ShipLoadout from a bounty's criminal_ship JSON dict.

        The criminal_ship dict is produced by BountyService.generate_loadout()
        and stored in the ``Bounty.criminal_ship`` JSONB column:

        {
            "ship_name": "Betty",
            "ship_emoji": "...",
            "ship_armour": 95,
            "armor_hp": 95,
            "shield_hp": 0,
            "total_hp": 95,
            "weapons": [
                {
                    "name": "...", "emoji": "...", "dps": 5.2, "value": 1000,
                    "damage_per_shot": 8.0, "loading_speed_ms": 600,
                    "range_m": 1400.0, "subtype": "blaster",
                }
            ],
            "turrets": [
                {
                    "name": "...", "emoji": "...", "dps": 3.1, "value": 500,
                    "damage_per_shot": 4.0, "loading_speed_ms": 800,
                    "range_m": 1200.0, "subtype": "",
                }
            ],
            "modules": [
                {
                    "name": "...", "emoji": "...", "type": "ArmourModule",
                    "value": 500, "tech_level": 1,
                    "extra_atts": {"armour": 40, ...},
                }
            ],
            "secondaries": [
                {
                    "name": "...", "emoji": "...", "dps": 0.0, "value": 5000,
                    "damage": 800, "loading_speed_ms": 3000,
                    "range_m": 2000.0, "subtype": "nuke",
                    "burst_count": 0, "emp_damage": 0,
                    "magnitude_m": 500.0, "steerable": True,
                    "rounds": 1,
                }
            ],
        }

        Steps:
        1. Extract ship_name, ship_armour (base armour)
        2. For each weapon dict → create WeaponStats(name, dps)
        3. For each turret dict → create WeaponStats(name, dps)
        4. For each module dict → create ModuleStats from extra_atts
        5. For each secondary dict → create WeaponStats with full combat fields
        6. Return ShipLoadout

        Args:
            criminal_ship: Dict from Bounty.criminal_ship JSONB column.

        Returns:
            ShipLoadout ready for use in CombatService.
        """
        ship_name = criminal_ship.get("ship_name", "Unknown")
        base_armour = criminal_ship.get("ship_armour", 100)
        manual_turret_mode = criminal_ship.get("manual_turret_mode", False)

        flogger.debug(f"Building criminal loadout: ship={ship_name!r}, base_armour={base_armour}")

        # Weapons
        weapons: list[WeaponStats] = []
        for w in criminal_ship.get("weapons", []):
            dps = float(w.get("dps", 0) or 0)
            w_spd = int(w.get("loading_speed_ms", 0) or 0)
            w_rng = float(w.get("range_m", 0.0) or 0.0)
            w_subtype = w.get("subtype", "") or ""
            # Derive damage_per_shot from dps × loading_speed_ms/1000 when not explicit
            # (self-healing fallback for legacy JSONB rows that pre-date Change A)
            w_damage = w.get("damage_per_shot")
            if w_damage is None:
                cadence = w_spd if w_spd > 0 else _DEFAULT_WEAPON_CADENCE_MS
                if dps > 0:
                    w_damage = dps * cadence / 1000.0
            # Supply a non-zero range floor so the fire gate never silently blocks the weapon
            if not w_rng:
                w_rng = _DEFAULT_WEAPON_RANGE_M
            weapons.append(
                WeaponStats(
                    name=w["name"],
                    dps=dps,
                    damage_per_shot=float(w_damage) if w_damage is not None else None,
                    loading_speed_ms=w_spd,
                    range_m=w_rng,
                    subtype=w_subtype,
                )
            )
            flogger.trace(
                f"Criminal weapon {w['name']!r} dps={dps} damage_per_shot={w_damage} "
                f"loading_speed_ms={w_spd} range_m={w_rng} subtype={w_subtype!r}"
            )

        # Turrets (T7 true-up: populate automatic/subtype/loading_speed_ms/range_m from criminal dict)
        turrets: list[WeaponStats] = []
        for t in criminal_ship.get("turrets", []):
            dps = float(t.get("dps", 0) or 0)
            t_automatic = bool(t.get("automatic", False))
            t_spd = int(t.get("loading_speed_ms", 0) or 0)
            t_rng = float(t.get("range_m", 0.0) or 0.0)
            t_subtype = t.get("subtype", "") or ""
            # Derive damage_per_shot from dps × loading_speed_ms/1000 when not explicit
            # (self-healing fallback for legacy JSONB — mirrors weapon block above)
            t_damage = t.get("damage_per_shot")
            if t_damage is None and dps > 0:
                cadence = t_spd if t_spd > 0 else _DEFAULT_WEAPON_CADENCE_MS
                t_damage = dps * cadence / 1000.0
            # Supply a non-zero range floor so the fire gate never silently blocks the turret
            if not t_rng:
                t_rng = _DEFAULT_WEAPON_RANGE_M
            turrets.append(
                WeaponStats(
                    name=t["name"],
                    dps=dps,
                    damage_per_shot=float(t_damage) if t_damage is not None else None,
                    loading_speed_ms=t_spd,
                    range_m=t_rng,
                    subtype=t_subtype,
                    automatic=t_automatic,
                )
            )
            flogger.trace(
                f"Criminal turret {t['name']!r} dps={dps} automatic={t_automatic} subtype={t_subtype!r} "
                f"loading_speed_ms={t_spd} range_m={t_rng}"
            )

        # Modules — pass module_type from the criminal_ship dict entry (T5/T8 detection)
        modules: list[ModuleStats] = []
        for m in criminal_ship.get("modules", []):
            extra = m.get("extra_atts") or {}
            mod_type = m.get("type", "") or ""
            modules.append(_module_stats_from_extra(m["name"], extra, module_type=mod_type))

        # Secondaries (CI-17): read loop mirroring the player block in from_player.
        # Each dict carries the full combat-field set (damage, loading_speed_ms, range_m,
        # subtype, burst_count, emp_damage, magnitude_m, steerable) plus rounds (ammo).
        # damage_per_shot is set from the stored "damage" field (NOT "dps"), because
        # secondary weapons carry per-shot damage in that column.
        secondary_weapons: list[WeaponStats] = []
        for sw in criminal_ship.get("secondaries", []):
            sw_name = sw.get("name", "")
            if not sw_name:
                continue
            sw_dps = float(sw.get("dps", 0) or 0)
            # "damage" carries per-shot damage for secondaries (NOT damage_per_shot)
            sw_damage = int(sw.get("damage", 0) or 0)
            sw_spd = int(sw.get("loading_speed_ms", 0) or 0)
            sw_rng = float(sw.get("range_m", 0.0) or 0.0)
            sw_subtype = sw.get("subtype", "") or ""
            sw_burst = int(sw.get("burst_count", 0) or 0)
            sw_emp = int(sw.get("emp_damage", 0) or 0)
            sw_mag = float(sw.get("magnitude_m", 0.0) or 0.0)
            sw_steer = bool(sw.get("steerable", False))
            # CI-17: rounds from the stored "rounds" field; floor at 1 so it always fires
            sw_rounds = int(sw.get("rounds", 1) or 1)
            sw_rounds = max(1, sw_rounds)
            secondary_weapons.append(
                WeaponStats(
                    name=sw_name,
                    dps=sw_dps,
                    damage_per_shot=float(sw_damage),
                    loading_speed_ms=sw_spd,
                    range_m=sw_rng,
                    subtype=sw_subtype,
                    burst_count=sw_burst,
                    emp_damage=sw_emp,
                    magnitude_m=sw_mag,
                    steerable=sw_steer,
                    ammo=sw_rounds,
                )
            )
            flogger.trace(
                f"Criminal secondary {sw_name!r} subtype={sw_subtype!r} damage={sw_damage} "
                f"loading_speed_ms={sw_spd} range_m={sw_rng} rounds={sw_rounds}"
            )

        # T8: built-in modules from criminal_ship dict (e.g. Scimitar has U'tool built-in)
        criminal_builtin_modules: list[str] = list(criminal_ship.get("builtin_modules") or [])

        flogger.debug(
            f"Criminal loadout built: ship={ship_name!r}, base_armour={base_armour}, "
            f"weapons={len(weapons)}, turrets={len(turrets)}, modules={len(modules)}, "
            f"secondary_weapons={len(secondary_weapons)}, builtin_modules={criminal_builtin_modules}"
        )

        return ShipLoadout(
            ship_name=ship_name,
            base_armour=base_armour,
            manual_turret_mode=manual_turret_mode,
            weapons=weapons,
            turrets=turrets,
            modules=modules,
            secondary_weapons=secondary_weapons,
            builtin_modules=criminal_builtin_modules,
        )
