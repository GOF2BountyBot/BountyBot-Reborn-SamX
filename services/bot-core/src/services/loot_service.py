"""LootService — static loot-cache owner + per-guild roll/chance facade (T3).

This service is the stateful, DB-aware half of the PvC looting system.  It owns
the in-memory caches that the latency-sensitive kill path needs (LOOT_JOURNAL
§5.8.4 static-cache requirement) and the per-guild knob resolution; all actual
probability/selection arithmetic is delegated to the pure, RNG-injectable
:mod:`services.loot_engine`.

Cache lifecycle mirrors ``ShopService.preload_static_data`` /
``clear_static_cache``: build once at startup (or on a seed-data reload), never
per request.  The cached content (LOOT_JOURNAL §5.8.4):

* **Band-1 base pool** — every lootable Weapon (primary/secondary/turret) +
  Module whose ``Item.type`` is not one of the three excluded module kinds, each
  tagged with its ``tech_level`` and concrete ``item_type``.  The ±TL window is
  applied as an in-memory filter per criminal — never re-queried.
* **Band-2 pool** — commodities in ``{ore_core, rare}``.
* **Band-3 pool** — commodities in ``{booze, technical, ore, standard, waste}``.
* **Tractor→chance static map (M-5)** — the 4 beams (keyed by name) → the T2
  chance knobs.

The pricing/value cache the loot path needs (commodity ``Item.value`` for the
C-2 sell price) is NOT duplicated here — T1 already preloads it into
``ShopService._price_cache`` (LOOT_JOURNAL §5.8.4 reuse note); each commodity
``LootCandidate`` carries its own ``value`` for callers that want it inline.
"""

from __future__ import annotations

import random

from persist.repositories.commodity_repository import CommodityRepository
from persist.repositories.module_repository import ModuleRepository
from persist.repositories.primary_weapon_repository import PrimaryWeaponRepository
from persist.repositories.secondary_weapon_repository import SecondaryWeaponRepository
from persist.repositories.turret_weapon_repository import TurretWeaponRepository
from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

from services import loot_engine
from services.game_constants import GameConstants, resolve_constant
from services.loot_engine import BandConfig, LootCandidate, LootRoll

flogger = bblogger.get_logger("loot-service")

# Module kinds that are NOT lootable (LOOT_JOURNAL §3).  Keyed on Item.type,
# which holds the module-kind string in the real schema (verified against the DB:
# module rows carry type="TractorBeamModule"/"JumpDriveModule"/… ).
EXCLUDED_MODULE_TYPES: frozenset[str] = frozenset({"JumpDriveModule", "TimeExtenderModule", "ShieldInjectorModule"})

#: Item.type that marks an equipped tractor beam (m-1 detection key).
TRACTOR_BEAM_TYPE = "TractorBeamModule"

# Commodity subcategory → band membership (LOOT_JOURNAL §3 / §5.8.4 m-2).
BAND2_SUBCATEGORIES: frozenset[str] = frozenset({"ore_core", "rare"})
BAND3_SUBCATEGORIES: frozenset[str] = frozenset({"booze", "technical", "ore", "standard", "waste"})

# M-5 tractor beam tech-level → chance TIER ordinal (1..4).  Only these 4 beams
# will ever exist (LOOT_JOURNAL §4/M-5); each has a distinct TL (4/5/7/8) so the
# TL is an unambiguous key into the chance tiers.  The tier ordinal — NOT the
# default chance value — is the stable key for per-guild override resolution
# (T5 fold-in: re-keying by value would collapse two tiers that share a default).
_TRACTOR_TL_TO_TIER: dict[int, int] = {4: 1, 5: 2, 7: 3, 8: 4}

# Tier ordinal → the GameConstants chance-knob attribute name.
_TIER_TO_KNOB: dict[int, str] = {
    1: "LOOT_CHANCE_TRACTOR_T1",
    2: "LOOT_CHANCE_TRACTOR_T2",
    3: "LOOT_CHANCE_TRACTOR_T3",
    4: "LOOT_CHANCE_TRACTOR_T4",
}


class LootService:
    """Owns the static loot cache and resolves per-guild loot knobs."""

    def __init__(self) -> None:
        self.primary_weapon_repo = PrimaryWeaponRepository()
        self.secondary_weapon_repo = SecondaryWeaponRepository()
        self.turret_weapon_repo = TurretWeaponRepository()
        self.module_repo = ModuleRepository()
        self.commodity_repo = CommodityRepository()

        # In-memory static caches — populated by preload_static_data(), rebuilt
        # only on a seed reload (LOOT_JOURNAL §5.8.4).  None ⇒ not yet loaded.
        self._band1_pool: list[LootCandidate] | None = None
        self._band2_pool: list[LootCandidate] | None = None
        self._band3_pool: list[LootCandidate] | None = None
        # Tractor beam name → loot-chance knob default (the M-5 static map).
        self._tractor_chance_map: dict[str, int] | None = None
        # Tractor beam name → tier ordinal (1..4) — the stable per-guild override
        # key (T5 fold-in); built alongside the chance map at preload.
        self._tractor_tier_map: dict[str, int] | None = None

    # ------------------------------------------------------------------
    # Cache lifecycle (mirrors ShopService)
    # ------------------------------------------------------------------

    async def preload_static_data(self, db: AsyncSession) -> None:
        """Build the Band pools + tractor chance map once (idempotent rebuild).

        Call at startup and on any seed-data reload.  Re-querying here is the ONLY
        place loot touches the item tables; the kill path reads these caches.
        """
        modules = await self.module_repo.list_all(db)
        primaries = await self.primary_weapon_repo.list_all(db)
        secondaries = await self.secondary_weapon_repo.list_all(db)
        turrets = await self.turret_weapon_repo.list_all(db)
        commodities = await self.commodity_repo.list_all(db)

        band1: list[LootCandidate] = []
        for m in modules:
            if getattr(m, "type", None) in EXCLUDED_MODULE_TYPES:
                continue
            band1.append(
                LootCandidate(
                    item_type="module",
                    name=m.name,
                    tech_level=getattr(m, "tech_level", None),
                    value=getattr(m, "value", 0) or 0,
                )
            )
        for w in primaries:
            band1.append(self._weapon_candidate(w, "primary_weapon"))
        for w in secondaries:
            band1.append(self._weapon_candidate(w, "secondary_weapon"))
        for w in turrets:
            band1.append(self._weapon_candidate(w, "turret_weapon"))

        band2: list[LootCandidate] = []
        band3: list[LootCandidate] = []
        for c in commodities:
            sub = getattr(c, "subcategory", None)
            cand = LootCandidate(
                item_type="commodity",
                name=c.name,
                tech_level=getattr(c, "tech_level", None),
                value=getattr(c, "value", 0) or 0,
            )
            if sub in BAND2_SUBCATEGORIES:
                band2.append(cand)
            elif sub in BAND3_SUBCATEGORIES:
                band3.append(cand)
            # plasma / mission (and any unknown) are deliberately excluded (§3).

        self._band1_pool = band1
        self._band2_pool = band2
        self._band3_pool = band3
        self._tractor_chance_map, self._tractor_tier_map = self._build_tractor_maps(modules)

        flogger.info(
            f"Preloaded loot static data: band1={len(band1)} (weapons+modules), "
            f"band2={len(band2)} (ore_core/rare), band3={len(band3)} (bulk commodities), "
            f"tractor_beams={len(self._tractor_chance_map)}"
        )

    def clear_static_cache(self) -> None:
        """Drop the cached pools/map (forces a rebuild on next preload)."""
        self._band1_pool = None
        self._band2_pool = None
        self._band3_pool = None
        self._tractor_chance_map = None
        self._tractor_tier_map = None

    @property
    def is_loaded(self) -> bool:
        """True once :meth:`preload_static_data` has populated the caches."""
        return self._band1_pool is not None and self._tractor_chance_map is not None

    # ------------------------------------------------------------------
    # Cache builders
    # ------------------------------------------------------------------

    @staticmethod
    def _weapon_candidate(weapon: object, item_type: str) -> LootCandidate:
        return LootCandidate(
            item_type=item_type,  # type: ignore[arg-type]
            name=getattr(weapon, "name"),  # noqa: B009 — uniform getattr style
            tech_level=getattr(weapon, "tech_level", None),
            value=getattr(weapon, "value", 0) or 0,
        )

    @staticmethod
    def _build_tractor_maps(modules: list) -> tuple[dict[str, int], dict[str, int]]:
        """Build the M-5 beam maps from the module rows.

        Returns ``(chance_map, tier_map)``:

        * ``chance_map`` — beam NAME → default chance value (the static map the
          win-branch resolver matches an equipped module-name list against).
        * ``tier_map`` — beam NAME → tier ordinal (1..4), the STABLE key for
          per-guild override resolution (T5 fold-in: re-keying overrides by chance
          value would collapse two tiers that share a default; the tier ordinal
          never collides).

        Both key by beam NAME (resolved via each beam's TL → tier).  An unexpected
        tractor TL (data drift) maps to the no-tractor chance with NO tier entry
        (so override resolution falls back to the no-tractor chance) and warns.
        """
        chance_map: dict[str, int] = {}
        tier_map: dict[str, int] = {}
        for m in modules:
            if getattr(m, "type", None) != TRACTOR_BEAM_TYPE:
                continue
            tl = getattr(m, "tech_level", None)
            tier = _TRACTOR_TL_TO_TIER.get(tl) if tl is not None else None
            if tier is None:
                flogger.warning(f"tractor beam '{m.name}' has unexpected TL {tl}; mapping to no-tractor chance")
                chance_map[m.name] = GameConstants.LOOT_CHANCE_NO_TRACTOR
            else:
                chance_map[m.name] = getattr(GameConstants, _TIER_TO_KNOB[tier])
                tier_map[m.name] = tier
        return chance_map, tier_map

    # ------------------------------------------------------------------
    # Per-guild config resolution → BandConfig for the pure engine
    # ------------------------------------------------------------------

    @staticmethod
    def resolve_band_config(guild_config: object | None) -> BandConfig:
        """Resolve all numeric loot knobs (per-guild override → default) into a BandConfig.

        Passing ``None`` for *guild_config* yields the pure GameConstants defaults
        — used by tests and by any caller without guild context.
        """
        r = resolve_constant
        return BandConfig(
            band1_select_pct=r(guild_config, "loot_band1_select_pct", GameConstants.LOOT_BAND1_SELECT_PCT),
            band2_select_pct=r(guild_config, "loot_band2_select_pct", GameConstants.LOOT_BAND2_SELECT_PCT),
            band3_select_pct=r(guild_config, "loot_band3_select_pct", GameConstants.LOOT_BAND3_SELECT_PCT),
            tl_window=r(guild_config, "loot_band1_tl_window", GameConstants.LOOT_BAND1_TL_WINDOW),
            band1_qty=(
                r(guild_config, "loot_band1_qty_min", GameConstants.LOOT_BAND1_QTY_MIN),
                r(guild_config, "loot_band1_qty_mode", GameConstants.LOOT_BAND1_QTY_MODE),
                r(guild_config, "loot_band1_qty_max", GameConstants.LOOT_BAND1_QTY_MAX),
            ),
            band2_qty=(
                r(guild_config, "loot_band2_qty_min", GameConstants.LOOT_BAND2_QTY_MIN),
                r(guild_config, "loot_band2_qty_mode", GameConstants.LOOT_BAND2_QTY_MODE),
                r(guild_config, "loot_band2_qty_max", GameConstants.LOOT_BAND2_QTY_MAX),
            ),
            band3_qty=(
                r(guild_config, "loot_band3_qty_min", GameConstants.LOOT_BAND3_QTY_MIN),
                r(guild_config, "loot_band3_qty_mode", GameConstants.LOOT_BAND3_QTY_MODE),
                r(guild_config, "loot_band3_qty_max", GameConstants.LOOT_BAND3_QTY_MAX),
            ),
            min_tl=GameConstants.MIN_TECH_LEVEL,
            max_tl=GameConstants.MAX_TECH_LEVEL,
        )

    def resolve_tractor_chance_map(self, guild_config: object | None) -> dict[str, int]:
        """Return the beam-name → chance map with per-guild chance overrides applied.

        The cached ``_tractor_chance_map`` holds the GameConstants-default chances;
        here we re-resolve each beam's tier against *guild_config* so T5 can pass a
        guild cfg.  Requires the cache to be loaded.

        T5 fold-in (M-5 re-key): each beam is re-resolved against its OWN tier knob,
        keyed by the beam's TL-derived chance TIER (``loot_chance_tractor_t1..t4``),
        NOT by its default chance VALUE.  Keying by value would collapse two tiers
        that happen to share a default (e.g. if T2 and T3 were both tuned to 40),
        silently dropping a beam from the override lookup.  Re-deriving the tier
        per beam from the cached default-value→tier map (built once at preload from
        the unambiguous TL keys) keeps each beam on its own knob even under a value
        collision.
        """
        if self._tractor_chance_map is None:
            raise RuntimeError("LootService cache not loaded; call preload_static_data first")
        # Per-tier knob fields, keyed by tier ordinal — NEVER by chance value (M-5).
        tier_field: dict[int, tuple[str, int]] = {
            1: ("loot_chance_tractor_t1", GameConstants.LOOT_CHANCE_TRACTOR_T1),
            2: ("loot_chance_tractor_t2", GameConstants.LOOT_CHANCE_TRACTOR_T2),
            3: ("loot_chance_tractor_t3", GameConstants.LOOT_CHANCE_TRACTOR_T3),
            4: ("loot_chance_tractor_t4", GameConstants.LOOT_CHANCE_TRACTOR_T4),
        }
        if self._tractor_tier_map is None:
            raise RuntimeError("LootService cache not loaded; call preload_static_data first")
        resolved: dict[str, int] = {}
        for name in self._tractor_chance_map:
            tier = self._tractor_tier_map.get(name)
            field_fallback = tier_field.get(tier) if tier is not None else None
            if field_fallback is None:
                resolved[name] = resolve_constant(
                    guild_config, "loot_chance_no_tractor", GameConstants.LOOT_CHANCE_NO_TRACTOR
                )
            else:
                field, fallback = field_fallback
                resolved[name] = resolve_constant(guild_config, field, fallback)
        return resolved

    def equipped_tractor_name(self, equipped_module_names: list[str]) -> str | None:
        """Return the first equipped module name that is a known tractor beam, else None.

        Used by T5's win-branch loot write to resolve the equipped beam's identity
        (for the T6 ``tractor_emoji`` render).  Tractor beams are unique-equip (m-1)
        so there is at most one.  Requires the cache to be loaded.
        """
        if self._tractor_chance_map is None:
            raise RuntimeError("LootService cache not loaded; call preload_static_data first")
        for name in equipped_module_names:
            if name in self._tractor_chance_map:
                return name
        return None

    # ------------------------------------------------------------------
    # Public roll / chance API (consumed by T4 at spawn, T5 at win)
    # ------------------------------------------------------------------

    def roll_loot(self, criminal_tl: int, rng: random.Random, guild_config: object | None = None) -> LootRoll | None:
        """Roll the single item a criminal carries (band → item → qty).

        ``criminal_tl`` is ``Bounty.tech_level`` (LOOT_JOURNAL §7.3).  Returns a
        :class:`~services.loot_engine.LootRoll`, or ``None`` if the chosen band's
        pool is empty.  Requires the cache to be loaded.
        """
        if self._band1_pool is None or self._band2_pool is None or self._band3_pool is None:
            raise RuntimeError("LootService cache not loaded; call preload_static_data first")
        cfg = self.resolve_band_config(guild_config)
        return loot_engine.roll_loot(cfg, self._band1_pool, self._band2_pool, self._band3_pool, criminal_tl, rng)

    def loot_chance(self, equipped_module_names: list[str], guild_config: object | None = None) -> int:
        """Resolve the loot chance (int %) for an equipped-module-name list (M-5).

        ``equipped_module_names`` is the win-branch ``player_loadout`` module list
        (LOOT_JOURNAL §7.6).  No/unknown beam → the no-tractor chance.
        """
        chance_map = self.resolve_tractor_chance_map(guild_config)
        no_tractor = resolve_constant(guild_config, "loot_chance_no_tractor", GameConstants.LOOT_CHANCE_NO_TRACTOR)
        return loot_engine.tractor_chance(equipped_module_names, chance_map, no_tractor)

    @staticmethod
    def roll_loot_success(chance_pct: int, rng: random.Random) -> bool:
        """Roll the tractor success gate (rng < chance) — thin testable helper."""
        return loot_engine.tractor_success(chance_pct, rng)
