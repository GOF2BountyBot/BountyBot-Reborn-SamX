"""LoadoutResponseService — builds a unified LoadoutResponse for players and bounties.

Used by both `/players/{id}/loadout` and `/bounties/{id}/loadout` routers to
produce a shared `LoadoutResponse` shape consumed by the discord-gateway
loadout embed builder.

Spec reference: §2.6 of LOADOUT_EMBED_DESIGN_SPEC.md.
"""

from __future__ import annotations

import contextlib

from api.schemas.loadout_schema import (
    CargoItem,
    LoadoutModuleItem,
    LoadoutResponse,
    LoadoutWeaponItem,
    ShipStats,
)
from shared import bblogger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.loadout_effect_service import LoadoutEffectService

flogger = bblogger.get_logger("loadout-response-service")


# ---------------------------------------------------------------------------
# A.48 — criminal-only module dedup configuration
# ---------------------------------------------------------------------------
# Module subtypes that may be repeat-fill at bounty spawn time and whose
# duplicates produce purely visual noise (no gameplay-meaningful information
# loss). Per A.48, only these are deduped — and ONLY for criminals. Player
# loadouts are NEVER deduped.
_DEDUP_CRIMINAL_MODULE_TYPES = frozenset({"CabinModule", "CompressorModule"})


def _apply_criminal_module_dedup(items: list) -> list:
    """Collapse runs of identical CabinModule/CompressorModule entries.

    Group `LoadoutModuleItem` entries by `(name, type)` pair. For groups whose
    `type ∈ _DEDUP_CRIMINAL_MODULE_TYPES` and N>1, replace the group with a
    SINGLE representative item whose `name` becomes ``"{original_name} x{N}"``.
    All other items pass through unchanged, preserving original ordering.

    This is a pure presentation-layer transformation: the underlying
    `bounty.criminal_ship` JSON is not modified, and combat resolution still
    uses the raw, non-deduped loadout. Player loadouts MUST NEVER be passed
    through this function.

    Args:
        items: Original list of LoadoutModuleItem instances.

    Returns:
        New list with eligible duplicates collapsed.
    """
    # Count occurrences per (name, type) for eligible types.
    counts: dict[tuple[str, str], int] = {}
    for it in items:
        if it.type in _DEDUP_CRIMINAL_MODULE_TYPES:
            key = (it.name, it.type)
            counts[key] = counts.get(key, 0) + 1

    if not counts or all(n <= 1 for n in counts.values()):
        # Nothing to dedup — return original list as-is.
        return list(items)

    # Walk the list once; emit eligible items only on first occurrence,
    # rewriting `.name` to `"<original> x<N>"` when N > 1. Drop subsequent
    # eligible duplicates. Non-eligible items pass through unchanged.
    seen_eligible: set[tuple[str, str]] = set()
    out: list = []
    for it in items:
        if it.type in _DEDUP_CRIMINAL_MODULE_TYPES:
            key = (it.name, it.type)
            if key in seen_eligible:
                continue
            seen_eligible.add(key)
            n = counts[key]
            if n > 1:
                # Build a fresh model_copy with the rewritten name.
                # model_copy preserves emoji/effects/combat_tier/value/etc.
                new_item = it.model_copy(update={"name": f"{it.name} x{n}"})
                out.append(new_item)
            else:
                out.append(it)
        else:
            out.append(it)
    return out


class LoadoutResponseService:
    """Builds a LoadoutResponse for either a player's active ship or a bounty's criminal ship."""

    def __init__(self) -> None:
        # Lazy imports so the service remains testable without triggering ORM setup.
        from persist.repositories.bounty_repository import BountyRepository
        from persist.repositories.criminal_repository import CriminalRepository
        from persist.repositories.inventory_repository import InventoryRepository
        from persist.repositories.item_repository import ItemRepository
        from persist.repositories.player_repository import PlayerRepository
        from persist.repositories.user_repository import UserRepository

        self.player_repo = PlayerRepository()
        self.bounty_repo = BountyRepository()
        self.criminal_repo = CriminalRepository()
        self.item_repo = ItemRepository()
        self.inventory_repo = InventoryRepository()
        self.user_repo = UserRepository()

    # ------------------------------------------------------------------
    # Player path
    # ------------------------------------------------------------------

    async def build_player_loadout(
        self,
        db: AsyncSession,
        player_id: int,
        include_cargo: bool,
        viewer_discord_id: int | None = None,
    ) -> LoadoutResponse | None:
        """Build a LoadoutResponse for a player's active ship.

        Returns None if the player does not exist (router converts to 404).
        If the player has no active ship, returns a LoadoutResponse with
        `message="No active ship"` so the gateway can render an error embed.
        """
        from persist.models.player_ship import PlayerShip
        from persist.models.ship import Ship

        player = await self.player_repo.get_by_id(db, player_id)
        if not player:
            flogger.debug(f"Player {player_id} not found for loadout")
            return None

        subject_name = await self._resolve_player_name(db, player)
        subject_mention = f"<@{viewer_discord_id}>" if viewer_discord_id else None

        # No active ship: early return with message
        if not player.active_ship_id:
            flogger.debug(f"Player {player_id} has no active ship")
            return LoadoutResponse(
                subject_kind="player",
                subject_name=subject_name,
                subject_mention=subject_mention,
                player_id=player.id,
                user_discord_id=player.user_id,
                message="No active ship",
            )

        # Resolve PlayerShip
        ps_result = await db.execute(select(PlayerShip).where(PlayerShip.id == player.active_ship_id))
        player_ship = ps_result.scalars().first()
        if not player_ship:
            flogger.warning(f"Player {player_id} has active_ship_id={player.active_ship_id} but no PlayerShip row")
            return LoadoutResponse(
                subject_kind="player",
                subject_name=subject_name,
                subject_mention=subject_mention,
                player_id=player.id,
                user_discord_id=player.user_id,
                message="No active ship",
            )

        # Static ship data
        ship_result = await db.execute(select(Ship).where(Ship.name == player_ship.ship_name))
        ship = ship_result.scalars().first()

        # Equipped items
        equipped_weapons = player_ship.weapons or []
        equipped_modules = player_ship.modules or []
        equipped_turrets = player_ship.turrets or []
        equipped_secondaries = getattr(player_ship, "secondary_weapons", None) or []
        secondary_ammo: dict = getattr(player_ship, "secondary_ammo", None) or {}

        # Build weapon/turret items (shared helper)
        weapon_items, weapon_dps = await self._build_weapon_items(db, equipped_weapons, "primary_weapon")
        turret_items, turret_dps = await self._build_weapon_items(db, equipped_turrets, "turret_weapon")
        secondary_items = await self._build_secondary_items(db, equipped_secondaries, secondary_ammo)
        total_dps = round(weapon_dps + turret_dps, 1)

        # Build module items (with effects + combat_tier) and compute HP bonuses
        module_items, armor_bonus, shield_hp, compressor_multiplier = await self._build_player_module_items(
            db, equipped_modules
        )

        base_armour = ship.armour if ship else 0
        armor_hp = base_armour + armor_bonus
        total_hp = armor_hp + shield_hp

        base_cargo = ship.cargo if ship else 0
        effective_cargo = round(base_cargo * compressor_multiplier) if base_cargo else base_cargo

        total_value = (
            sum(w.value or 0 for w in weapon_items)
            + sum(t.value or 0 for t in turret_items)
            + sum(m.value or 0 for m in module_items)
        )

        # Cargo items (only when requested)
        cargo_items: list[CargoItem] = []
        cargo_total_count = 0
        if include_cargo:
            cargo_items, cargo_total_count = await self._build_cargo_items(db, player_id)

        ship_stats = ShipStats(
            armour=base_armour if ship else None,
            cargo=effective_cargo if ship else None,
            handling=ship.handling if ship else None,
            hp=total_hp,
            dps=total_dps,
            total_value=total_value,
            max_primaries=ship.max_primaries if ship else None,
            max_secondaries=ship.max_secondaries if ship else None,
            max_turrets=ship.max_turrets if ship else None,
            max_modules=ship.max_modules if ship else None,
        )

        ship_icon = ship.icon if ship and ship.icon else None

        return LoadoutResponse(
            subject_kind="player",
            subject_name=subject_name,
            subject_mention=subject_mention,
            player_id=player.id,
            user_discord_id=player.user_id,
            ship_name=player_ship.ship_name,
            ship_nickname=player_ship.nickname,
            ship_icon=ship_icon,
            ship_emoji=ship.emoji if ship else None,
            thumbnail_url=ship_icon,
            ship_stats=ship_stats,
            weapons=weapon_items,
            turrets=turret_items,
            secondaries=secondary_items,
            modules=module_items,
            cargo=cargo_items,
            cargo_total_count=cargo_total_count,
        )

    async def _resolve_player_name(self, db: AsyncSession, player) -> str:
        """Resolve player display name from User.discord_username (fallback: f'Player {id}')."""
        try:
            user = await self.user_repo.get_by_id(db, player.user_id)
            if user and user.discord_username:
                return user.discord_username
        except Exception as e:  # defensive — lookup failures should never 500 the loadout
            flogger.debug(f"User lookup failed for player {player.id}: {e}")
        return f"Player {player.id}"

    async def _build_weapon_items(
        self, db: AsyncSession, names: list[str], item_type: str
    ) -> tuple[list[LoadoutWeaponItem], float]:
        items: list[LoadoutWeaponItem] = []
        total_dps = 0.0
        for name in names:
            item = await self.item_repo.get_by_name(db, name, item_type=item_type)
            if item is None:
                item = await self.item_repo.get_by_name(db, name)
            dps = getattr(item, "dps", None) if item else None
            if dps:
                total_dps += dps
            items.append(
                LoadoutWeaponItem(
                    name=name,
                    emoji=item.emoji if item else None,
                    dps=dps,
                    value=item.value if item else None,
                )
            )
        return items, total_dps

    async def _build_secondary_items(
        self, db: AsyncSession, names: list[str], ammo: dict[str, int]
    ) -> list[LoadoutWeaponItem]:
        """Build secondary-weapon items for a player's loadout, attaching ammo counts.

        Args:
            names: Ordered list of equipped secondary weapon names
                   (from ``PlayerShip.secondary_weapons``).
            ammo:  Per-weapon ammo sidecar (``PlayerShip.secondary_ammo``),
                   mapping weapon name → remaining rounds.  May be empty.

        Returns:
            List of ``LoadoutWeaponItem`` with ``rounds`` populated from *ammo*
            (or ``None`` when the weapon is not in the ammo sidecar).
        """
        items: list[LoadoutWeaponItem] = []
        for name in names:
            item = await self.item_repo.get_by_name(db, name, item_type="secondary_weapon")
            if item is None:
                item = await self.item_repo.get_by_name(db, name)
            rounds = ammo.get(name) if ammo else None
            items.append(
                LoadoutWeaponItem(
                    name=name,
                    emoji=item.emoji if item else None,
                    dps=getattr(item, "dps", None) if item else None,
                    value=item.value if item else None,
                    rounds=rounds,
                )
            )
        return items

    async def _build_player_module_items(
        self, db: AsyncSession, names: list[str]
    ) -> tuple[list[LoadoutModuleItem], int, int, float]:
        """Build module items, returning (items, armor_bonus, shield_hp, compressor_multiplier)."""
        from persist.models.module import Module

        items: list[LoadoutModuleItem] = []
        armor_bonus = 0
        shield_hp = 0
        compressor_multiplier = 1.0
        for name in names:
            mod_result = await db.execute(select(Module).where(Module.name == name))
            mod = mod_result.scalars().first()
            if mod is None:
                item = await self.item_repo.get_by_name(db, name, item_type="module")
                mod = item

            if mod is None:
                # Item not found — render name-only, no stats contribution
                items.append(LoadoutModuleItem(name=name))
                continue

            extra = mod.extra_atts if isinstance(mod.extra_atts, dict) else {}
            mod_type = getattr(mod, "type", None)

            # HP / cargo contributions from known fields (only when legal)
            if extra:
                with contextlib.suppress(TypeError, ValueError):
                    armor_bonus += int(extra.get("armour", 0) or 0)
                with contextlib.suppress(TypeError, ValueError):
                    shield_hp += int(extra.get("shield", 0) or 0)
                if mod_type == "CompressorModule":
                    raw_mult = extra.get("cargoMultiplier", extra.get("cargo_multiplier"))
                    if raw_mult is not None:
                        with contextlib.suppress(TypeError, ValueError):
                            compressor_multiplier *= float(raw_mult)

            effects = LoadoutEffectService.format_module_effects(mod_type, extra)
            combat_tier = LoadoutEffectService.get_module_combat_tier(mod_type)

            items.append(
                LoadoutModuleItem(
                    name=name,
                    emoji=mod.emoji,
                    type=mod_type,
                    value=mod.value,
                    tech_level=getattr(mod, "tech_level", None),
                    effects=effects,
                    combat_tier=combat_tier,
                )
            )
        return items, armor_bonus, shield_hp, compressor_multiplier

    async def _build_cargo_items(self, db: AsyncSession, player_id: int) -> tuple[list[CargoItem], int]:
        inventory_items = await self.inventory_repo.get_player_items(db, player_id)
        cargo: list[CargoItem] = []
        total = 0
        for inv in inventory_items:
            emoji = None
            game_item = await self.item_repo.get_by_name(db, inv.item_name)
            if game_item:
                emoji = game_item.emoji
            cargo.append(
                CargoItem(
                    item_name=inv.item_name,
                    item_type=inv.item_type,
                    quantity=inv.quantity,
                    emoji=emoji,
                )
            )
            total += inv.quantity
        return cargo, total

    # ------------------------------------------------------------------
    # Bounty path
    # ------------------------------------------------------------------

    async def build_bounty_loadout(
        self,
        db: AsyncSession,
        bounty_id: int,
    ) -> LoadoutResponse | None:
        """Build a LoadoutResponse for a bounty's criminal ship.

        Returns None if the bounty does not exist (router converts to 404).
        If `criminal_ship` is missing, returns a response with
        `message="Criminal ship data unavailable"`.
        """
        from persist.models.ship import Ship

        bounty = await self.bounty_repo.get_by_id(db, bounty_id)
        if not bounty:
            flogger.debug(f"Bounty {bounty_id} not found for loadout")
            return None

        criminal_name = bounty.criminal_name or "Unknown Criminal"

        if not bounty.criminal_ship:
            flogger.warning(f"Bounty {bounty_id} has no criminal_ship data")
            return LoadoutResponse(
                subject_kind="criminal",
                subject_name=criminal_name,
                subject_description=bounty.criminal_faction,
                bounty_id=bounty.id,
                tech_level=bounty.tech_level,
                message="Criminal ship data unavailable",
            )

        criminal_ship: dict = bounty.criminal_ship
        ship_name = criminal_ship.get("ship_name")

        # Best-effort criminal lookup (for icon thumbnail)
        criminal = None
        try:
            criminal = await self.criminal_repo.get_by_name(db, criminal_name)
        except Exception as e:  # defensive — lookup failures should never 500 the loadout
            flogger.debug(f"Criminal lookup failed for {criminal_name!r}: {e}")

        # Best-effort ship lookup (for base stats)
        ship = None
        if ship_name:
            try:
                ship_result = await db.execute(select(Ship).where(Ship.name == ship_name))
                ship = ship_result.scalars().first()
            except Exception as e:
                flogger.debug(f"Ship lookup failed for {ship_name!r}: {e}")

        # Weapons / turrets / secondaries (already fully formed in the JSON)
        weapons_raw = criminal_ship.get("weapons") or []
        turrets_raw = criminal_ship.get("turrets") or []
        secondaries_raw = criminal_ship.get("secondaries") or []
        modules_raw = criminal_ship.get("modules") or []

        weapon_items = [LoadoutWeaponItem(**self._normalize_weapon_dict(w)) for w in weapons_raw]
        turret_items = [LoadoutWeaponItem(**self._normalize_weapon_dict(t)) for t in turrets_raw]
        secondary_items = [
            LoadoutWeaponItem(**self._normalize_weapon_dict(s, include_rounds=True)) for s in secondaries_raw
        ]

        module_items: list[LoadoutModuleItem] = []
        compressor_multiplier = 1.0
        for m in modules_raw:
            mod_type = m.get("type")
            extra = m.get("extra_atts") if isinstance(m.get("extra_atts"), dict) else {}
            if mod_type == "CompressorModule" and extra:
                raw_mult = extra.get("cargoMultiplier", extra.get("cargo_multiplier"))
                if raw_mult is not None:
                    with contextlib.suppress(TypeError, ValueError):
                        compressor_multiplier *= float(raw_mult)
            effects = LoadoutEffectService.format_module_effects(mod_type, extra)
            combat_tier = LoadoutEffectService.get_module_combat_tier(mod_type)
            module_items.append(
                LoadoutModuleItem(
                    name=m.get("name", "Unknown"),
                    emoji=m.get("emoji"),
                    type=mod_type,
                    value=m.get("value"),
                    tech_level=m.get("tech_level") or m.get("techLevel"),
                    effects=effects,
                    combat_tier=combat_tier,
                )
            )

        # A.48 — criminal-only presentation dedup. Applied here so it covers
        # both /criminal-loadout AND bounty announcements (both are criminal
        # rendering surfaces). Player loadouts go through build_player_loadout
        # which never invokes this helper.
        module_items = _apply_criminal_module_dedup(module_items)

        # DPS
        weapon_dps = sum((w.dps or 0.0) for w in weapon_items)
        turret_dps = sum((t.dps or 0.0) for t in turret_items)
        total_dps = round(weapon_dps + turret_dps, 1)

        # HP — prefer JSON-provided total_hp; else armor_hp from ship_armour; else None
        json_total_hp = criminal_ship.get("total_hp")
        if isinstance(json_total_hp, int):
            hp: int | None = json_total_hp
        else:
            armor_hp = criminal_ship.get("armor_hp")
            shield_hp = criminal_ship.get("shield_hp") or 0
            hp = armor_hp + int(shield_hp or 0) if isinstance(armor_hp, int) else None

        # Armour (base) — prefer ship_armour from JSON, fallback to ship.armour
        base_armour = criminal_ship.get("ship_armour")
        if base_armour is None and ship is not None:
            base_armour = ship.armour

        # Effective cargo capacity — ship.cargo × CompressorModule multipliers
        base_cargo = ship.cargo if ship else 0
        effective_cargo = round(base_cargo * compressor_multiplier) if base_cargo else base_cargo

        total_value = (
            sum(w.value or 0 for w in weapon_items)
            + sum(t.value or 0 for t in turret_items)
            + sum(m.value or 0 for m in module_items)
        )

        ship_stats = ShipStats(
            armour=base_armour,
            cargo=effective_cargo,
            handling=ship.handling if ship else None,
            hp=hp,
            dps=total_dps,
            total_value=total_value or None,
            max_primaries=ship.max_primaries if ship else None,
            max_secondaries=ship.max_secondaries if ship else None,
            max_turrets=ship.max_turrets if ship else None,
            max_modules=ship.max_modules if ship else None,
        )

        ship_icon = ship.icon if ship and ship.icon else None
        # Thumbnail prefers criminal portrait; falls back to None (gateway null-guards)
        thumbnail_url = criminal.icon if criminal and criminal.icon else None

        return LoadoutResponse(
            subject_kind="criminal",
            subject_name=criminal_name,
            subject_description=bounty.criminal_faction,
            bounty_id=bounty.id,
            tech_level=bounty.tech_level,
            ship_name=ship_name,
            ship_icon=ship_icon,
            ship_emoji=criminal_ship.get("ship_emoji"),
            thumbnail_url=thumbnail_url,
            ship_stats=ship_stats,
            weapons=weapon_items,
            turrets=turret_items,
            secondaries=secondary_items,
            modules=module_items,
            cargo=[],
            cargo_total_count=0,
        )

    @staticmethod
    def _normalize_weapon_dict(raw: dict, *, include_rounds: bool = False) -> dict:
        """Project the fields LoadoutWeaponItem needs out of a criminal_ship JSON weapon dict.

        Args:
            raw:            Raw weapon dict from criminal_ship JSON.
            include_rounds: When True, also extract the ``rounds`` field (secondary weapons).
        """
        result = {
            "name": raw.get("name", "Unknown"),
            "emoji": raw.get("emoji"),
            "dps": raw.get("dps"),
            "value": raw.get("value"),
        }
        if include_rounds:
            result["rounds"] = raw.get("rounds")
        return result
