"""
Game Constants module for BountyBot.

Centralizes all magic numbers from the legacy system. Key operational constants
can be overridden via environment variables with the prefix ``BOUNTYBOT_``.

Example::

    BOUNTYBOT_MAX_BOUNTIES_PER_DIVISION=10
    BOUNTYBOT_CHECK_COOLDOWN=120

Non-operational constants (XP boundaries, division boundaries, module equip
limits) remain hardcoded to maintain game balance integrity.
"""

import os
from typing import Any

from shared import bblogger

_flogger = bblogger.get_logger(__name__)


class GameConstants:
    """Centralized game constants. All values match the legacy system defaults.

    Override any overridable constant via environment variable
    ``BOUNTYBOT_{CONSTANT_NAME}``.

    Call :meth:`load` at application startup to apply environment overrides.
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _env_int(key: str, default: int) -> int:
        """Return *int* value from ``BOUNTYBOT_{key}`` env var, or *default*."""
        return int(os.environ.get(f"BOUNTYBOT_{key}", default))

    @staticmethod
    def _env_float(key: str, default: float) -> float:
        """Return *float* value from ``BOUNTYBOT_{key}`` env var, or *default*."""
        return float(os.environ.get(f"BOUNTYBOT_{key}", default))

    @staticmethod
    def _env_int_list(key: str, default: list[int]) -> list[int]:
        """Return list[int] from ``BOUNTYBOT_{key}`` env var (comma-separated), or *default*."""
        raw = os.environ.get(f"BOUNTYBOT_{key}")
        if raw is None or not raw.strip():
            return default
        return [int(x.strip()) for x in raw.split(",") if x.strip()]

    # ------------------------------------------------------------------
    # Tech Levels
    # ------------------------------------------------------------------

    MIN_TECH_LEVEL: int = 1
    MAX_TECH_LEVEL: int = 10

    # Per-division TL draw centre scalars — the per-guild columns added in revision
    # 0028 are nullable and resolve via resolve_constant(cfg, "division_tl_center_{div}", ...).
    # These four global scalars are the fallback when the column is NULL.
    DIVISION_TL_CENTER_BRONZE: int = 1
    DIVISION_TL_CENTER_SILVER: int = 3
    DIVISION_TL_CENTER_GOLD: int = 6
    DIVISION_TL_CENTER_PLATINUM: int = 8

    # Legacy dict kept as a derived view for any existing callers and tests that
    # read DIVISION_TL_CENTERS directly.  The sole live consumption site,
    # game_maths.pick_division_tech_level(), has been updated to accept an
    # explicit center int instead of reading this dict (issue #70 flatten).
    # Do not add new readers of this dict — use the scalar constants above.
    DIVISION_TL_CENTERS: dict[str, int] = {
        "bronze": 1,
        "silver": 3,
        "gold": 6,
        "platinum": 8,
    }

    # Maximum tech level for criminal loadouts per division.
    # Bronze is capped low to ensure new players with Betty can compete.
    # Flat scalar equivalents (issue #70 flatten; per-guild columns division_max_tl_{div}).
    # Do not add new readers of the dict below — use these scalars via resolve_flattened().
    DIVISION_MAX_TL_BRONZE: int = 2
    DIVISION_MAX_TL_SILVER: int = 4
    DIVISION_MAX_TL_GOLD: int = 7
    DIVISION_MAX_TL_PLATINUM: int = 10

    # Legacy dict: derived view rebuilt from scalars in load() (issue #70 flatten).
    # Do not add new readers — use the DIVISION_MAX_TL_* scalars above.
    # Kept for fallback chain in resolve_flattened() and legacy JSONB compatibility.
    DIVISION_MAX_TL: dict[str, int] = {
        "bronze": 2,  # Betty-class only (TL 0-2)
        "silver": 4,  # Mid-tier ships
        "gold": 7,  # High-tier ships
        "platinum": 10,  # No effective cap
    }

    # ------------------------------------------------------------------
    # Divisions / Levels — REMOVED in B.48
    # ------------------------------------------------------------------
    # ``DIVISION_NAMES``, ``DIVISION_BOUNDARIES``, and ``XP_LEVEL_BOUNDARIES``
    # were deleted in B.48 along with the rest of the vestigial level/division
    # progression system. Player progression now uses only the configurable
    # per-guild ``xp_thresholds`` JSON (Bronze/Silver/Gold/Platinum + optional
    # Prestige) on the GuildConfig row.

    # ------------------------------------------------------------------
    # XP Reward Multiplier
    # ------------------------------------------------------------------

    BOUNTY_REWARD_TO_XP_GAIN_MULT: float = 0.1

    # ------------------------------------------------------------------
    # Bounty Winner Reserve Factor
    # Fraction of the total bounty reward held back as the winner's
    # guaranteed payout, protecting the captor from heavy consolation
    # payouts. The remainder is the consolation pool, split evenly
    # across route systems for non-winner checkers.
    # Override via: BOUNTYBOT_BOUNTY_WINNER_RESERVE_FACTOR=0.25
    # ------------------------------------------------------------------

    BOUNTY_WINNER_RESERVE_FACTOR: float = 0.25

    # ------------------------------------------------------------------
    # Bounty Prize-Pool Scaler (per division)
    # Multiplies the full bounty prize pool (the legacy per-sys seed × route
    # length) by a per-division factor BEFORE the winner-reserve split, so both
    # the winner reserve and the consolation pool scale together. Defaults to
    # 1.0 (no change) for every division except silver. Real-spawn data showed
    # silver paid ≈ bronze (median ~6k vs ~5.8k) despite a much harder, mandatory
    # fight — a dead rung. The geometric-ideal midpoint of bronze→gold is ~2.4×;
    # 2.0 is a deliberately more conservative default (silver ≈ 2.05× bronze,
    # ~12k median) that removes the dead rung without fully closing to the ladder.
    # Tune per-guild via the bounty_division_reward_mult_{div} columns (issue #70 flatten).
    # Flat scalar equivalents; do not add new readers of the dict below.
    BOUNTY_DIVISION_REWARD_MULT_BRONZE: float = 1.0
    BOUNTY_DIVISION_REWARD_MULT_SILVER: float = 2.0
    BOUNTY_DIVISION_REWARD_MULT_GOLD: float = 1.0
    BOUNTY_DIVISION_REWARD_MULT_PLATINUM: float = 1.0

    # Legacy dict: derived view rebuilt from scalars in load() (issue #70 flatten).
    # Do not add new readers — use the BOUNTY_DIVISION_REWARD_MULT_* scalars above.
    BOUNTY_DIVISION_REWARD_MULT: dict[str, float] = {
        "bronze": 1.0,
        "silver": 2.0,
        "gold": 1.0,
        "platinum": 1.0,
    }

    # ------------------------------------------------------------------
    # Bronze combat-bonus multiplier (issue #51)
    # The optional post-capture duel a Bronze player can attempt awards a bonus
    # equal to (winner_reward × fraction) on a win, where the fraction scales
    # with the player's prestige_count:
    #     fraction = min(CAP, BASE + PER_PRESTIGE × prestige_count)
    # Defaults give 40% at 0★, +10%/★, capped at 100% (reached at 6★).
    # Env overrides: BOUNTYBOT_BRONZE_COMBAT_BONUS_{BASE_MULT,PER_PRESTIGE,CAP}.
    BRONZE_COMBAT_BONUS_BASE_MULT: float = 0.40
    BRONZE_COMBAT_BONUS_PER_PRESTIGE: float = 0.10
    BRONZE_COMBAT_BONUS_CAP: float = 1.00

    # ------------------------------------------------------------------
    # Ship Price Thresholds (10 entries; index 0 == TL1)
    # ------------------------------------------------------------------

    SHIP_PRICE_THRESHOLDS: list[int] = [
        50_000,
        100_000,
        200_000,
        500_000,
        1_000_000,
        2_000_000,
        5_000_000,
        7_000_000,
        7_500_000,
        999_999_999,
    ]

    # ------------------------------------------------------------------
    # Bounty System
    # Retired rev 0031: MAX_BOUNTIES_PER_DIVISION, SHIP_VALUE_REWARD_PERCENTAGE,
    # CRIMINAL_EQUIP_DAMAGELESS_WEAPON_CHANCE — no live readers.
    # ------------------------------------------------------------------

    CLOSE_BOUNTY_THRESHOLD: int = 4  # systems ahead for proximity hint
    CRIMINAL_MAX_GEAR_UPGRADE: int = 1  # TL levels above criminal
    MAX_ROUTE_LENGTH: int = 50  # A* pathfinding limit
    MIN_ROUTE_SYSTEMS: int = 3  # reject too-short routes (no adjacent-gate 2-system hunts)
    # "Recently spotted" look-ahead window B is rolled once per bounty from
    # [0, RECENTLY_SPOTTED_MAX_WINDOW] at spawn and persisted on the bounty.
    # A checked system shows "recently spotted" iff it is 1..B stops before the
    # answer; B=0 means the bounty shows no "recently spotted" hint at all.
    # Rolling B per-bounty (and never revealing it) stops players from
    # triangulating the exact answer the way the old fixed 1–2 window allowed.
    RECENTLY_SPOTTED_MAX_WINDOW: int = 3
    # Waypoint routes: a bounty route may be lengthened by routing through 1 or 2
    # random intermediate "waypoint" systems (A→B→C / A→B→C→D), each leg an
    # independent A* hop with earlier-leg systems blocked so the whole route stays
    # simple (no repeats). Rolled per spawn as a cascade: dual first, else single,
    # else the standard A→C. A waypoint must keep ≥ BOUNTY_WAYPOINT_MIN_DEGREE
    # available neighbours after earlier legs are removed. If no simple waypoint
    # route can be built within BOUNTY_WAYPOINT_ATTEMPTS, generation falls back to
    # a standard A→C route so a spawn never fails on routing.
    BOUNTY_SINGLE_WAYPOINT_PROB: float = 0.33  # P(1 waypoint), rolled only if dual failed
    BOUNTY_DUAL_WAYPOINT_PROB: float = 0.10  # P(2 waypoints), rolled first
    BOUNTY_WAYPOINT_ATTEMPTS: int = 20  # endpoint/waypoint re-rolls before fallback to A→C
    BOUNTY_WAYPOINT_MIN_DEGREE: int = 2  # min available neighbours a waypoint must retain

    # ------------------------------------------------------------------
    # Criminal loadout balance (BALANCE_JOURNAL §A — Thread 3 & Thread 4)
    # All per-guild overridable via the matching snake_case GuildConfig column,
    # resolved through resolve_constant(cfg, "<key>", GameConstants.<NAME>).
    # ------------------------------------------------------------------

    # Thread 3 — primary long-range selection.
    # A primary weapon is LONG iff range_m > LONG_RANGE_THRESHOLD_M, else SHORT.
    # Per-guild override: long_range_threshold_m on GuildConfig.
    LONG_RANGE_THRESHOLD_M: int = 2600

    # Floor share of long-range primaries per ship (ceil(pct * max_primaries)),
    # plus the per-remaining-slot long roll. GLOBAL float in [0.0, 1.0].
    # Per-guild override: criminal_long_range_pct on GuildConfig.
    CRIMINAL_LONG_RANGE_PCT: float = 0.50

    # Per-slot ±1 TL-band pick weights for primary selection (center=target TL).
    # Flat scalar equivalents (issue #70 flatten; per-guild columns primary_tl_band_weight_{key}).
    # Do not add new readers of the dict below — use these scalars via resolve_flattened().
    PRIMARY_TL_BAND_WEIGHT_CENTER: int = 70
    PRIMARY_TL_BAND_WEIGHT_MINUS1: int = 20
    PRIMARY_TL_BAND_WEIGHT_PLUS1: int = 10

    # Legacy dict: derived view rebuilt from scalars in load() (issue #70 flatten).
    # Do not add new readers — use the PRIMARY_TL_BAND_WEIGHT_* scalars above.
    PRIMARY_TL_BAND_WEIGHTS: dict[str, int] = {"center": 70, "minus1": 20, "plus1": 10}

    # Thread 4 — criminal two-gate module Gate-1 equip chances by division (%).
    # Flat scalar equivalents (issue #70 flatten; per-guild columns criminal_{name}_chance_{div}).
    # Do not add new readers of the dicts below — use these scalars via resolve_flattened().
    CRIMINAL_CLOAK_CHANCE_BRONZE: int = 0
    CRIMINAL_CLOAK_CHANCE_SILVER: int = 25
    CRIMINAL_CLOAK_CHANCE_GOLD: int = 66
    CRIMINAL_CLOAK_CHANCE_PLATINUM: int = 100
    CRIMINAL_BOOSTER_CHANCE_BRONZE: int = 50
    CRIMINAL_BOOSTER_CHANCE_SILVER: int = 100
    CRIMINAL_BOOSTER_CHANCE_GOLD: int = 100
    CRIMINAL_BOOSTER_CHANCE_PLATINUM: int = 100
    CRIMINAL_EMERGENCY_CHANCE_BRONZE: int = 0
    CRIMINAL_EMERGENCY_CHANCE_SILVER: int = 25
    CRIMINAL_EMERGENCY_CHANCE_GOLD: int = 50
    CRIMINAL_EMERGENCY_CHANCE_PLATINUM: int = 100
    CRIMINAL_WEAPONMOD_CHANCE_BRONZE: int = 0
    CRIMINAL_WEAPONMOD_CHANCE_SILVER: int = 25
    CRIMINAL_WEAPONMOD_CHANCE_GOLD: int = 50
    CRIMINAL_WEAPONMOD_CHANCE_PLATINUM: int = 100

    # Legacy dicts: derived views rebuilt from scalars in load() (issue #70 flatten).
    # Do not add new readers — use the CRIMINAL_*_CHANCE_* scalar constants above.
    # Kept for fallback chain in resolve_flattened() and legacy JSONB compatibility.
    CRIMINAL_CLOAK_CHANCE_BY_DIVISION: dict[str, int] = {"bronze": 0, "silver": 25, "gold": 66, "platinum": 100}
    CRIMINAL_BOOSTER_CHANCE_BY_DIVISION: dict[str, int] = {"bronze": 50, "silver": 100, "gold": 100, "platinum": 100}
    CRIMINAL_EMERGENCY_CHANCE_BY_DIVISION: dict[str, int] = {"bronze": 0, "silver": 25, "gold": 50, "platinum": 100}
    CRIMINAL_WEAPONMOD_CHANCE_BY_DIVISION: dict[str, int] = {"bronze": 0, "silver": 25, "gold": 50, "platinum": 100}

    # Thread 6 — exclude primarily-EMP weapons (emp_damage > real_damage) from
    # CRIMINAL primary + secondary selection.  Default ON: emp_damage is a
    # phase-2+ deferred feature (engine applies 0 HP delta), so EMP-dominant
    # weapons do ~no real damage → free player win.  Behavioral toggle, not a
    # numeric knob — auto-disable (set False) cleanly once EMP mechanics ship.
    # Per-guild-only override: criminal_exclude_emp_weapons on GuildConfig
    # (no env form, matching the dict knobs above).
    CRIMINAL_EXCLUDE_EMP_WEAPONS: bool = True

    # ------------------------------------------------------------------
    # CI-17: Criminal secondary weapons (owner-decision knobs #1–#3)
    # All four constants are tunable here; nowhere else.
    # ------------------------------------------------------------------

    # Knob #1 / #2 — rounds granted per subtype for criminal secondaries.
    # nuke=1 prevents unwinnable alpha-strikes; other subtypes use flat counts.
    CRIMINAL_SECONDARY_ROUNDS: dict[str, int] = {
        "nuke": 1,
        "missile": 5,
        "rocket": 5,
        "cluster-missile": 3,
        "shock-blast": 2,
    }

    # Knob #3 — exclude secondary weapons whose damage column is ≤ this value.
    # Default 1 drops damage==0 (zero-damage, never fires) AND damage==1 (dmg=1
    # Fireworks — a 1-dmg nuke is pure dead weight; owner may lower to 0 to
    # include it).
    CRIMINAL_SECONDARY_MIN_DAMAGE: int = 1

    # ------------------------------------------------------------------
    # Activity / Temperature — RETIRED rev 0031
    # GUILD_ACTIVITY_DECAY_RATE, MIN_GUILD_ACTIVITY, ACTIVITY_TEMP_PER_PLAYER
    # removed; temperature subsystem was never fully wired (owner-approved).
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Bounty Spawn Check Interval (minutes)
    # Spawn-orchestrator cron step; seeded into the persisted APScheduler
    # store on first boot.  Env changes need POST /scheduler/reset.
    # ENV VAR: BOUNTYBOT_BOUNTY_SPAWN_CHECK_INTERVAL_MINUTES
    # (renamed from BOUNTYBOT_BOUNTY_DELAY_RANDOM_MIN in rev 0031)
    # ------------------------------------------------------------------

    BOUNTY_SPAWN_CHECK_INTERVAL_MINUTES: int = 5
    BOUNTY_SPAWN_JITTER: int = 180  # Up to 3 minutes of random offset on each spawn check

    # ------------------------------------------------------------------
    # Timers (seconds)
    # ------------------------------------------------------------------

    # SHOP_REFRESH_INTERVAL — retired rev 0031 (zero readers; the shop_refresh
    # scheduler job cron is defined in DEFAULT_SCHEDULER_JOBS, not from this).
    CHECK_COOLDOWN: int = 180  # 3 minutes
    DUEL_REQUEST_EXPIRY: int = 86400  # 1 day
    TIER_CHANGE_COOLDOWN: int = 86400  # 24 hours — gates /promote and /demote

    # ------------------------------------------------------------------
    # DB Data Retention (db_retention_default scheduled job)
    # ------------------------------------------------------------------
    # Terminal-state rows in ``bounty`` and ``duel_requests`` add no
    # game-relevant value once their aggregate counters have been
    # written to the ``players`` table. Audit logs are preserved
    # out-of-band via scheduled pg_backup.
    #
    # Overridable via ``BOUNTYBOT_BOUNTY_RETENTION_HOURS``,
    # ``BOUNTYBOT_DUEL_RETENTION_HOURS``, ``BOUNTYBOT_AUDIT_RETENTION_DAYS``.

    BOUNTY_RETENTION_HOURS: int = 24
    DUEL_RETENTION_HOURS: int = 24
    AUDIT_RETENTION_DAYS: int = 30
    # Overridable via ``BOUNTYBOT_EVENT_METRICS_RETENTION_DAYS``.
    EVENT_METRICS_RETENTION_DAYS: int = 30

    # ------------------------------------------------------------------
    # Shop Stock Generation
    # Retired rev 0031: SHOP_DEFAULT_SHIPS_NUM, SHOP_DEFAULT_WEAPONS_NUM,
    # SHOP_DEFAULT_MODULES_NUM, SHOP_DEFAULT_TURRETS_NUM, SHOP_DEFAULT_TOOLS_NUM,
    # TURRET_SPAWN_PROBABILITY — no live readers; columns dropped in migration 0031.
    # ------------------------------------------------------------------

    # Secondary weapons are consumable rounds; scale the rolled shop quantity so a
    # single refresh cycle (6h default) can supply multiple players. Heavy ordnance
    # (nuke, shock-blast, cluster-missile) scales less than standard ammo (missile,
    # rocket, ...). An item whose subtype is missing/unknown gets the STANDARD scaler.
    SHOP_HEAVY_SECONDARY_SUBTYPES: frozenset[str] = frozenset({"nuke", "shock-blast", "cluster-missile"})
    SHOP_SECONDARY_QTY_SCALER_HEAVY: int = 5
    SHOP_SECONDARY_QTY_SCALER_STANDARD: int = 10

    # ------------------------------------------------------------------
    # Shop Module Buckets
    # ------------------------------------------------------------------
    # Membership mirrors the criminal loadout classification in bounty_service
    # (_MODULE_PRIORITY_ORDER / _FILLER_A_TYPES / _FILLER_B_TYPES /
    # _NEVER_EQUIP_TYPES) with TWO shop-specific overrides:
    #   (1) TractorBeamModule is moved from FILLER into COMBAT (it gates PvC
    #       loot, so it is first-class in the shop).
    #   (2) JUNK is removed from shop draws entirely.
    # Defined literally here (not imported) because game_constants is a leaf
    # module and must not import bounty_service. The disjoint+coverage assertion
    # below is the drift guard (21 distinct module types total in the catalog).
    SHOP_JUNK_MODULE_TYPES: frozenset[str] = frozenset(
        {
            "TransfusionBeamModule",
            "ShieldInjectorModule",
            "TimeExtenderModule",
            "JumpDriveModule",
        }
    )
    SHOP_FILLER_MODULE_TYPES: frozenset[str] = frozenset(
        {
            "GammaShieldModule",
            "SpectralFilterModule",
            "RepairBeamModule",
            "SignatureModule",
            "MiningDrillModule",
            "CompressorModule",
            "CabinModule",
        }
    )
    SHOP_COMBAT_MODULE_TYPES: frozenset[str] = frozenset(
        {
            "ScannerModule",
            "ArmourModule",
            "ShieldModule",
            "CloakModule",
            "BoosterModule",
            "EmergencySystemModule",
            "RepairBotModule",
            "PrimaryWeaponModModule",
            "ThrusterModule",
            "TractorBeamModule",
        }
    )
    SHOP_COMBAT_MODULE_PROB: float = 0.75

    # Probability (0.0-1.0) that a shop refresh draws its batch TL from the
    # tier's in-band range (uniform over [LO, HI]) instead of the out-of-band
    # taper. This is the *guaranteed* tier-matched fraction.
    #   0.0 -> never in-band (all draws come from the out-of-band taper)
    #   1.0 -> every refresh is tier-matched
    # The two buckets are mutually exclusive (the taper covers only TLs OUTSIDE
    # [LO, HI]), so this value is the exact in-band rate, not a lower bound.
    # Deliberately a scalar float (not a per-division dict) so the issue #70
    # per-guild override audit can add it as a plain nullable column.
    SHOP_BANDED_TL_WEIGHT: float = 0.7

    # Per-tier in-band TL range for shop batch draws [LO, HI] (inclusive).
    # The banded bucket draws uniformly within [LO, HI]; the out-of-band bucket
    # tapers exponentially away from these edges (see SHOP_*TIER_TL_DECAY).
    # Flat scalars (not a dict) so each maps to a plain nullable column under the
    # issue #70 per-guild override refactor; mirrors where the criminal TL bands
    # are headed. Kept self-contained (own HI, not reused from DIVISION_MAX_TL).
    SHOP_TL_BAND_LO_BRONZE: int = 1
    SHOP_TL_BAND_HI_BRONZE: int = 2
    SHOP_TL_BAND_LO_SILVER: int = 1
    SHOP_TL_BAND_HI_SILVER: int = 4
    SHOP_TL_BAND_LO_GOLD: int = 4
    SHOP_TL_BAND_HI_GOLD: int = 7
    SHOP_TL_BAND_LO_PLATINUM: int = 7
    SHOP_TL_BAND_HI_PLATINUM: int = 10

    # Out-of-band taper decay factors (0.0-1.0) for the shop batch TL draw.
    # Each step away from the band edge multiplies the weight by the decay:
    # the level just above HI gets full weight, the next UPTIER*full, etc.
    # (likewise below LO with DOWNTIER). Smaller = steeper falloff = rarer.
    # UPTIER is gentle (players up-gear toward the next tier); DOWNTIER is steep
    # (suppress off-tier junk below the band).
    SHOP_UPTIER_TL_DECAY: float = 0.6
    SHOP_DOWNTIER_TL_DECAY: float = 0.45

    # ------------------------------------------------------------------
    # Shop Rank Counts — RETIRED rev 0031
    # NUM_SHIP_RANKS, NUM_WEAPON_RANKS, NUM_MODULE_RANKS, NUM_TURRET_RANKS
    # — hardcoded structure constants with no live readers; removed.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Duels
    # Retired rev 0031: DUEL_LOG_MAX_LENGTH, DUEL_CLOAK_CHANCE — no live readers.
    # DUEL_VARIANCE_PERCENT — retired in T10 (SimpleTTKResolver removed; TickResolver has no variance).
    # BOUNTY_PVC_ARMOUR_BUFF_FACTOR — retired in T10 (replaced by PVC_DAMAGE_REDUCTION §3).
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Item Type Vocabulary
    # ------------------------------------------------------------------

    # All concrete item types present in the data model (used for browsing/catalog).
    # "commodity" is a first-class concrete type (PvC loot C-1): it is pure cargo —
    # validated/priced/sellable, but NEVER stocked in a GuildShop (that gate is the
    # separate _CONCRETE_TO_CONFIG_KEY map in shop_service.py, which has no commodity).
    CATALOG_ITEM_TYPES: frozenset[str] = frozenset(
        {"ship", "primary_weapon", "secondary_weapon", "turret_weapon", "module", "commodity"}
    )

    # Retired rev 0031: PLAYABLE_ITEM_TYPES — identical to CURRENTLY_ENABLED_TYPES;
    # no live readers distinct from CATALOG_ITEM_TYPES / CURRENTLY_ENABLED_TYPES.

    # Concrete item types exposed on the user-facing economy/equip surface TODAY.
    # secondary_weapon is included; the shop excludes deferred subtypes (emp-bomb,
    # mine, sentry-gun) via DEFERRED_SECONDARY_SUBTYPES in combat_models.py.
    # commodity is included (PvC loot C-1) — first-class cargo, but never shop-stocked
    # (shop gating is _CONCRETE_TO_CONFIG_KEY, not this set).
    # This is the SINGLE lever that gates item-type exposure across all
    # economy/loadout flows — no scattered if-branches needed.
    CURRENTLY_ENABLED_TYPES: frozenset[str] = frozenset(
        {"ship", "primary_weapon", "secondary_weapon", "turret_weapon", "module", "commodity"}
    )

    # Generic alias → concrete type expansion (catalog-flavoured; includes all types).
    # Playable-flavoured expansion is derived at runtime by filtering against CURRENTLY_ENABLED_TYPES.
    GENERIC_TO_CONCRETE_EXPANSION: dict[str, tuple[str, ...]] = {
        "ship": ("ship",),
        "module": ("module",),
        "weapon": ("primary_weapon", "secondary_weapon", "turret_weapon"),
        "turret": ("turret_weapon",),
    }

    # ------------------------------------------------------------------
    # Inventory
    # ------------------------------------------------------------------

    # MAX_SHIP_NICKNAME_LENGTH: enforced global — 100 chars (rev 0031; was 30).
    # The DB column player_ships.nickname is String(100).
    # Validated in UpdateNicknameRequest (min_length=1, max_length=100).
    MAX_SHIP_NICKNAME_LENGTH: int = 100
    # KAAMO_MAX_CAPACITY — retired (issue #70): Kaamo storage capacity is not a
    # mechanic and never will be; the override chain was a silent no-op.

    # ------------------------------------------------------------------
    # Loot (PvC) — tunable knobs (LOOT_JOURNAL §8, T2).
    # All per-guild overridable via the matching snake_case GuildConfig column,
    # resolved through resolve_constant(cfg, "<key>", GameConstants.<NAME>), and
    # env-overridable via BOUNTYBOT_<NAME> (wired in load() with _track_int/_track_float).
    # LOOT_DROP_CHANCE stays a FIXED constant (m-5) — no column, no env, no override.
    # ------------------------------------------------------------------

    # §5.3 — loot-roll chance by equipped tractor-beam tier. INT PERCENT (0–100),
    # matching the 0020 chance-knob convention (e.g. duel_cloak_chance). No beam = 0%.
    LOOT_CHANCE_TRACTOR_T1: int = 20  # % — AB-1 "Retractor"
    LOOT_CHANCE_TRACTOR_T2: int = 40  # % — AB-2 "Glue Gun"
    LOOT_CHANCE_TRACTOR_T3: int = 60  # % — AB-3 "Kingfisher"
    LOOT_CHANCE_TRACTOR_T4: int = 80  # % — AB-4 "Octopus"
    LOOT_CHANCE_NO_TRACTOR: int = 0  # % — no tractor beam equipped

    # §5.8.4 — band-select weights. INT PERCENT (0–100); sum to 100 at defaults.
    LOOT_BAND1_SELECT_PCT: int = 10  # % — Band 1 (Weapons + Modules)
    LOOT_BAND2_SELECT_PCT: int = 20  # % — Band 2 (ore_core, rare)
    LOOT_BAND3_SELECT_PCT: int = 70  # % — Band 3 (bulk commodities)

    # §5.8.4 — Band-1 item must be within ±this many TL of the criminal.
    LOOT_BAND1_TL_WINDOW: int = 1

    # §5.8.1–.3 — per-band quantity triangular (MIN, MODE, MAX). Integer counts.
    LOOT_BAND1_QTY_MIN: int = 1
    LOOT_BAND1_QTY_MAX: int = 3
    LOOT_BAND1_QTY_MODE: int = 1
    LOOT_BAND2_QTY_MIN: int = 4
    LOOT_BAND2_QTY_MAX: int = 12
    LOOT_BAND2_QTY_MODE: int = 8
    LOOT_BAND3_QTY_MIN: int = 10
    LOOT_BAND3_QTY_MAX: int = 22
    LOOT_BAND3_QTY_MODE: int = 16

    # §5.7 / C-2 — Commodity sell payout = Item.value × quantity × this FLOAT fraction.
    # 1.0 = pay 100% face value. Commodities sell as a pure credit sink (units are
    # destroyed, never added to a GuildShop) — see shop_service.sell_item.
    LOOT_COMMODITY_SELL_FRACTION: float = 1.0

    # ------------------------------------------------------------------
    # Demotion

    # % of credits deducted on /demote. Per-guild override: demotion_credit_penalty_pct on GuildConfig.
    DEMOTION_CREDIT_PENALTY_PCT: int = 10

    # Classic Mode

    CLASSIC_CREDITS_PER_CHECK: int = 1000
    # B.48: ``CLASSIC_DIVISION_NAME`` removed alongside DIVISION_NAMES.
    # No production code depended on it; classic_mode players are still
    # tracked via player.tier (default "Bronze") and the player.classic_mode
    # boolean column.

    # ------------------------------------------------------------------
    # Module Equip Limits
    # Positive = max allowed; -1 = unlimited; 0 = not equippable
    # ------------------------------------------------------------------

    MODULE_EQUIP_LIMITS: dict[str, int] = {
        "ArmourModule": 1,
        "BoosterModule": 1,
        "CabinModule": -1,
        "CloakModule": 1,
        "CompressorModule": -1,
        "EmergencySystemModule": 1,
        "GammaShieldModule": 1,
        "JumpDriveModule": 0,
        "MiningDrillModule": 1,
        "PrimaryWeaponModModule": 1,
        "RepairBeamModule": 1,
        "RepairBotModule": 1,
        "ScannerModule": 1,
        "ShieldInjectorModule": 1,
        "ShieldModule": 1,
        "SignatureModule": 1,
        "SpectralFilterModule": 1,
        "ThrusterModule": 1,
        "TimeExtenderModule": 1,
        "TractorBeamModule": 1,
        "TransfusionBeamModule": 1,
    }

    # ------------------------------------------------------------------
    # Combat System — Future Mechanics — RETIRED rev 0031
    # DEFAULT_ACCURACY, DEFAULT_EVASION, CLOAK_ACCURACY_PENALTY,
    # SCANNER_ACCURACY_BONUS, THRUSTER_EVASION_BONUS, SHIELD_RECHARGE_RATE,
    # REPAIR_BOT_HEAL_RATE, BOOSTER_DPS_MULTIPLIER, COMBAT_TICK_RATE,
    # PERSISTENT_DAMAGE_DECAY_RATE — all placeholder zeros/ones with no live
    # readers; removed to avoid confusion with the live Phase-1 constants below.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Combat System — Phase-1 Constants (Appendix A, COMBAT_SPEC_LOCKED.md)
    # All overridable via BOUNTYBOT_<NAME> env var and per-guild override.
    # ------------------------------------------------------------------

    # Accuracy system (§5)
    CLOAK_SET_VALUE: float = 0.25
    BOOSTER_ACCURACY_DEBUFF_FACTOR: float = 0.10
    THRUSTER_ACCURACY_BONUS_FACTOR: float = 0.10
    AUTO_TURRET_ACCURACY_MULTIPLIER: float = 0.85
    PLAYER_BASE_ACCURACY: float = 0.60
    NPC_BASE_ACCURACY: float = 0.50
    ACCURACY_CLAMP_MIN: float = 0.05
    ACCURACY_CLAMP_MAX: float = 0.99
    SCANNER_TIER_B_BONUS_PP: int = 5
    SCANNER_TIER_C_BONUS_PP: int = 10

    # Repair bots (§3 / §7.6)
    KETAR_I_REPAIR_PCT_PER_SEC: float = 0.02
    KETAR_II_REPAIR_PCT_PER_SEC: float = 0.04

    # Tick / timing (§1)
    TICK_MS: int = 10
    MAX_FIGHT_TICKS: int = 60000

    # Distance model (§2)
    STARTING_DISTANCE_M: int = 5000
    BASE_SHIP_SPEED_MPS: int = 150
    MIN_DISTANCE_M: int = 300
    THRUSTER_WINDOW_M: int = 750
    SHOCK_BLAST_TRIGGER_RANGE_M: int = 500  # shock-blast only fires inside this range (m)

    # HP-threshold activation lists (§7.2 / §7.3 / §8)
    CLOAK_HP_THRESHOLDS_PCT: list[int] = [66, 33]
    BOOSTER_HP_THRESHOLDS_PCT: list[int] = [80, 60, 40, 20]

    # EmergencySystem (§7.7)
    EMERGENCY_SYSTEM_INVULN_S: int = 10

    # Nuke (§6.2) — two-regime detonation window + yield interference (D-014, 2026-06-10)
    NUKE_MAGNITUDE_SCALE: float = 0.10  # R = magnitude_m × scale (world→5km-field normalization)
    NUKE_FRIENDLY_FACTOR: float = 0.50  # self-damage global knob (firer at position 0)
    NUKE_RANGE_REGIME_THRESHOLD_M: int = 1000  # LR/CR regime boundary
    NUKE_LR_NEAR_FRAC: float = 0.40  # LR window = [NEAR_FRAC×d, d] — no overshoot at range
    NUKE_CR_SHORT_M: int = 600  # CR window short edge: max(0, d − 600)
    NUKE_CR_OVERSHOOT_M: int = 400  # CR window far edge: d + 400
    NUKE_STACK_FALLOFF: float = 0.5  # per-side yield interference: mult = falloff ** prior_detonations

    # PvC damage reduction — Keith T. Maxwell bonus (§3)
    PVC_DAMAGE_REDUCTION: float = 0.33

    # Combat log retention (§12) — scoped per battle type (issue #86).
    # Bounty (PvC) logs are transient; PvP (duel) logs are kept far longer.
    # PvP window of 0 = never prune (permanent) — the disk-bounded default is 1 year.
    COMBAT_LOG_BOUNTY_RETENTION_HOURS: int = 48
    COMBAT_LOG_PVP_RETENTION_HOURS: int = 8760

    # CI-21: layer_depleted re-emit fraction (latch clears when layer recovers ≥ this fraction of max).
    # Override via: BOUNTYBOT_COMBAT_LAYER_REEMIT_FRACTION=0.25
    COMBAT_LAYER_REEMIT_FRACTION: float = 0.25

    # ------------------------------------------------------------------
    # Combat log recap denoising knobs (Phase 1 + 2)
    # ------------------------------------------------------------------

    # Minimum total occurrences of a same-key CYCLIC event (globally across the whole
    # timeline) before all occurrences are collapsed into a single aggregate row.
    # Keys with fewer total occurrences than this threshold pass through unchanged.
    # Override via: BOUNTYBOT_RECAP_COLLAPSE_MIN_RUN=3
    RECAP_COLLAPSE_MIN_RUN: int = 3

    # Maximum silence gap (seconds) between consecutive Key Events before a cyclic
    # fill event is inserted to avoid long blank stretches in the recap.
    # Override via: BOUNTYBOT_RECAP_GAP_FILL_S=20.0
    RECAP_GAP_FILL_S: float = 20.0

    # Minimum number of times a nuke/shock weapon must fire before the low-impact
    # detonations are grouped into a summary line.
    # Override via: BOUNTYBOT_RECAP_NUKE_SUMMARY_MIN_COUNT=3
    RECAP_NUKE_SUMMARY_MIN_COUNT: int = 3

    # A nuke detonation is "significant" (kept as its own line) if its opponent damage
    # is ≥ this fraction of the weapon's best opponent-damage in the fight.
    # Override via: BOUNTYBOT_RECAP_NUKE_SIGNIFICANCE_FRACTION=0.25
    RECAP_NUKE_SIGNIFICANCE_FRACTION: float = 0.25

    # ------------------------------------------------------------------
    # Environment variable overrides (operational constants only)
    # ------------------------------------------------------------------

    @classmethod
    def load(cls) -> None:
        """Apply environment variable overrides for operational constants.

        Call once at application startup (e.g. in ``main.py``).  Constants
        that govern game-balance (XP boundaries, division definitions, module
        equip limits) are intentionally excluded from runtime overrides.
        """
        _flogger.info("GameConstants.load() — applying environment variable overrides")
        _overrides: list[str] = []

        def _track_int(key: str, default: int) -> int:
            val = cls._env_int(key, default)
            if os.environ.get(f"BOUNTYBOT_{key}") is not None:
                _overrides.append(f"{key}={val}")
            return val

        def _track_float(key: str, default: float) -> float:
            val = cls._env_float(key, default)
            if os.environ.get(f"BOUNTYBOT_{key}") is not None:
                _overrides.append(f"{key}={val}")
            return val

        # Division TL draw centres (issue #70 flatten of DIVISION_TL_CENTERS dict)
        cls.DIVISION_TL_CENTER_BRONZE = _track_int("DIVISION_TL_CENTER_BRONZE", 1)
        cls.DIVISION_TL_CENTER_SILVER = _track_int("DIVISION_TL_CENTER_SILVER", 3)
        cls.DIVISION_TL_CENTER_GOLD = _track_int("DIVISION_TL_CENTER_GOLD", 6)
        cls.DIVISION_TL_CENTER_PLATINUM = _track_int("DIVISION_TL_CENTER_PLATINUM", 8)
        # Keep derived dict in sync with updated scalars for legacy callers
        cls.DIVISION_TL_CENTERS = {
            "bronze": cls.DIVISION_TL_CENTER_BRONZE,
            "silver": cls.DIVISION_TL_CENTER_SILVER,
            "gold": cls.DIVISION_TL_CENTER_GOLD,
            "platinum": cls.DIVISION_TL_CENTER_PLATINUM,
        }

        # JSONB flatten — division_max_tl (issue #70, revision 0030)
        cls.DIVISION_MAX_TL_BRONZE = _track_int("DIVISION_MAX_TL_BRONZE", 2)
        cls.DIVISION_MAX_TL_SILVER = _track_int("DIVISION_MAX_TL_SILVER", 4)
        cls.DIVISION_MAX_TL_GOLD = _track_int("DIVISION_MAX_TL_GOLD", 7)
        cls.DIVISION_MAX_TL_PLATINUM = _track_int("DIVISION_MAX_TL_PLATINUM", 10)
        cls.DIVISION_MAX_TL = {
            "bronze": cls.DIVISION_MAX_TL_BRONZE,
            "silver": cls.DIVISION_MAX_TL_SILVER,
            "gold": cls.DIVISION_MAX_TL_GOLD,
            "platinum": cls.DIVISION_MAX_TL_PLATINUM,
        }

        # JSONB flatten — bounty_division_reward_mult (issue #70, revision 0030)
        cls.BOUNTY_DIVISION_REWARD_MULT_BRONZE = _track_float("BOUNTY_DIVISION_REWARD_MULT_BRONZE", 1.0)
        cls.BOUNTY_DIVISION_REWARD_MULT_SILVER = _track_float("BOUNTY_DIVISION_REWARD_MULT_SILVER", 2.0)
        cls.BOUNTY_DIVISION_REWARD_MULT_GOLD = _track_float("BOUNTY_DIVISION_REWARD_MULT_GOLD", 1.0)
        cls.BOUNTY_DIVISION_REWARD_MULT_PLATINUM = _track_float("BOUNTY_DIVISION_REWARD_MULT_PLATINUM", 1.0)
        cls.BOUNTY_DIVISION_REWARD_MULT = {
            "bronze": cls.BOUNTY_DIVISION_REWARD_MULT_BRONZE,
            "silver": cls.BOUNTY_DIVISION_REWARD_MULT_SILVER,
            "gold": cls.BOUNTY_DIVISION_REWARD_MULT_GOLD,
            "platinum": cls.BOUNTY_DIVISION_REWARD_MULT_PLATINUM,
        }

        # JSONB flatten — primary_tl_band_weights (issue #70, revision 0030)
        cls.PRIMARY_TL_BAND_WEIGHT_CENTER = _track_int("PRIMARY_TL_BAND_WEIGHT_CENTER", 70)
        cls.PRIMARY_TL_BAND_WEIGHT_MINUS1 = _track_int("PRIMARY_TL_BAND_WEIGHT_MINUS1", 20)
        cls.PRIMARY_TL_BAND_WEIGHT_PLUS1 = _track_int("PRIMARY_TL_BAND_WEIGHT_PLUS1", 10)
        cls.PRIMARY_TL_BAND_WEIGHTS = {
            "center": cls.PRIMARY_TL_BAND_WEIGHT_CENTER,
            "minus1": cls.PRIMARY_TL_BAND_WEIGHT_MINUS1,
            "plus1": cls.PRIMARY_TL_BAND_WEIGHT_PLUS1,
        }

        # JSONB flatten — criminal chance scalars (issue #70, revision 0030)
        cls.CRIMINAL_CLOAK_CHANCE_BRONZE = _track_int("CRIMINAL_CLOAK_CHANCE_BRONZE", 0)
        cls.CRIMINAL_CLOAK_CHANCE_SILVER = _track_int("CRIMINAL_CLOAK_CHANCE_SILVER", 25)
        cls.CRIMINAL_CLOAK_CHANCE_GOLD = _track_int("CRIMINAL_CLOAK_CHANCE_GOLD", 66)
        cls.CRIMINAL_CLOAK_CHANCE_PLATINUM = _track_int("CRIMINAL_CLOAK_CHANCE_PLATINUM", 100)
        cls.CRIMINAL_CLOAK_CHANCE_BY_DIVISION = {
            "bronze": cls.CRIMINAL_CLOAK_CHANCE_BRONZE,
            "silver": cls.CRIMINAL_CLOAK_CHANCE_SILVER,
            "gold": cls.CRIMINAL_CLOAK_CHANCE_GOLD,
            "platinum": cls.CRIMINAL_CLOAK_CHANCE_PLATINUM,
        }
        cls.CRIMINAL_BOOSTER_CHANCE_BRONZE = _track_int("CRIMINAL_BOOSTER_CHANCE_BRONZE", 50)
        cls.CRIMINAL_BOOSTER_CHANCE_SILVER = _track_int("CRIMINAL_BOOSTER_CHANCE_SILVER", 100)
        cls.CRIMINAL_BOOSTER_CHANCE_GOLD = _track_int("CRIMINAL_BOOSTER_CHANCE_GOLD", 100)
        cls.CRIMINAL_BOOSTER_CHANCE_PLATINUM = _track_int("CRIMINAL_BOOSTER_CHANCE_PLATINUM", 100)
        cls.CRIMINAL_BOOSTER_CHANCE_BY_DIVISION = {
            "bronze": cls.CRIMINAL_BOOSTER_CHANCE_BRONZE,
            "silver": cls.CRIMINAL_BOOSTER_CHANCE_SILVER,
            "gold": cls.CRIMINAL_BOOSTER_CHANCE_GOLD,
            "platinum": cls.CRIMINAL_BOOSTER_CHANCE_PLATINUM,
        }
        cls.CRIMINAL_EMERGENCY_CHANCE_BRONZE = _track_int("CRIMINAL_EMERGENCY_CHANCE_BRONZE", 0)
        cls.CRIMINAL_EMERGENCY_CHANCE_SILVER = _track_int("CRIMINAL_EMERGENCY_CHANCE_SILVER", 25)
        cls.CRIMINAL_EMERGENCY_CHANCE_GOLD = _track_int("CRIMINAL_EMERGENCY_CHANCE_GOLD", 50)
        cls.CRIMINAL_EMERGENCY_CHANCE_PLATINUM = _track_int("CRIMINAL_EMERGENCY_CHANCE_PLATINUM", 100)
        cls.CRIMINAL_EMERGENCY_CHANCE_BY_DIVISION = {
            "bronze": cls.CRIMINAL_EMERGENCY_CHANCE_BRONZE,
            "silver": cls.CRIMINAL_EMERGENCY_CHANCE_SILVER,
            "gold": cls.CRIMINAL_EMERGENCY_CHANCE_GOLD,
            "platinum": cls.CRIMINAL_EMERGENCY_CHANCE_PLATINUM,
        }
        cls.CRIMINAL_WEAPONMOD_CHANCE_BRONZE = _track_int("CRIMINAL_WEAPONMOD_CHANCE_BRONZE", 0)
        cls.CRIMINAL_WEAPONMOD_CHANCE_SILVER = _track_int("CRIMINAL_WEAPONMOD_CHANCE_SILVER", 25)
        cls.CRIMINAL_WEAPONMOD_CHANCE_GOLD = _track_int("CRIMINAL_WEAPONMOD_CHANCE_GOLD", 50)
        cls.CRIMINAL_WEAPONMOD_CHANCE_PLATINUM = _track_int("CRIMINAL_WEAPONMOD_CHANCE_PLATINUM", 100)
        cls.CRIMINAL_WEAPONMOD_CHANCE_BY_DIVISION = {
            "bronze": cls.CRIMINAL_WEAPONMOD_CHANCE_BRONZE,
            "silver": cls.CRIMINAL_WEAPONMOD_CHANCE_SILVER,
            "gold": cls.CRIMINAL_WEAPONMOD_CHANCE_GOLD,
            "platinum": cls.CRIMINAL_WEAPONMOD_CHANCE_PLATINUM,
        }

        # Criminal secondary min damage (issue #70 batch)
        cls.CRIMINAL_SECONDARY_MIN_DAMAGE = _track_int("CRIMINAL_SECONDARY_MIN_DAMAGE", 1)

        # Bounty system
        # Retired rev 0031: MAX_BOUNTIES_PER_DIVISION, CRIMINAL_EQUIP_DAMAGELESS_WEAPON_CHANCE,
        # SHIP_VALUE_REWARD_PERCENTAGE — removed from load(); constants deleted.
        cls.CLOSE_BOUNTY_THRESHOLD = _track_int("CLOSE_BOUNTY_THRESHOLD", 4)
        cls.MAX_ROUTE_LENGTH = _track_int("MAX_ROUTE_LENGTH", 50)
        cls.MIN_ROUTE_SYSTEMS = _track_int("MIN_ROUTE_SYSTEMS", 3)
        cls.RECENTLY_SPOTTED_MAX_WINDOW = _track_int("RECENTLY_SPOTTED_MAX_WINDOW", 3)
        cls.BOUNTY_SINGLE_WAYPOINT_PROB = _track_float("BOUNTY_SINGLE_WAYPOINT_PROB", 0.33)
        cls.BOUNTY_DUAL_WAYPOINT_PROB = _track_float("BOUNTY_DUAL_WAYPOINT_PROB", 0.10)
        cls.BOUNTY_WAYPOINT_ATTEMPTS = _track_int("BOUNTY_WAYPOINT_ATTEMPTS", 20)
        cls.BOUNTY_WAYPOINT_MIN_DEGREE = _track_int("BOUNTY_WAYPOINT_MIN_DEGREE", 2)
        cls.CRIMINAL_MAX_GEAR_UPGRADE = _track_int("CRIMINAL_MAX_GEAR_UPGRADE", 1)

        # Criminal loadout balance — scalar knobs (dict knobs are per-guild-only, no env form)
        cls.LONG_RANGE_THRESHOLD_M = _track_int("LONG_RANGE_THRESHOLD_M", 2600)
        cls.CRIMINAL_LONG_RANGE_PCT = _track_float("CRIMINAL_LONG_RANGE_PCT", 0.50)

        # Loot (PvC) tunable knobs (LOOT_JOURNAL §8, T2). All scalar → env + per-guild.
        cls.LOOT_CHANCE_TRACTOR_T1 = _track_int("LOOT_CHANCE_TRACTOR_T1", 20)
        cls.LOOT_CHANCE_TRACTOR_T2 = _track_int("LOOT_CHANCE_TRACTOR_T2", 40)
        cls.LOOT_CHANCE_TRACTOR_T3 = _track_int("LOOT_CHANCE_TRACTOR_T3", 60)
        cls.LOOT_CHANCE_TRACTOR_T4 = _track_int("LOOT_CHANCE_TRACTOR_T4", 80)
        cls.LOOT_CHANCE_NO_TRACTOR = _track_int("LOOT_CHANCE_NO_TRACTOR", 0)
        cls.LOOT_BAND1_SELECT_PCT = _track_int("LOOT_BAND1_SELECT_PCT", 10)
        cls.LOOT_BAND2_SELECT_PCT = _track_int("LOOT_BAND2_SELECT_PCT", 20)
        cls.LOOT_BAND3_SELECT_PCT = _track_int("LOOT_BAND3_SELECT_PCT", 70)
        cls.LOOT_BAND1_TL_WINDOW = _track_int("LOOT_BAND1_TL_WINDOW", 1)
        cls.LOOT_BAND1_QTY_MIN = _track_int("LOOT_BAND1_QTY_MIN", 1)
        cls.LOOT_BAND1_QTY_MAX = _track_int("LOOT_BAND1_QTY_MAX", 3)
        cls.LOOT_BAND1_QTY_MODE = _track_int("LOOT_BAND1_QTY_MODE", 1)
        cls.LOOT_BAND2_QTY_MIN = _track_int("LOOT_BAND2_QTY_MIN", 4)
        cls.LOOT_BAND2_QTY_MAX = _track_int("LOOT_BAND2_QTY_MAX", 12)
        cls.LOOT_BAND2_QTY_MODE = _track_int("LOOT_BAND2_QTY_MODE", 8)
        cls.LOOT_BAND3_QTY_MIN = _track_int("LOOT_BAND3_QTY_MIN", 10)
        cls.LOOT_BAND3_QTY_MAX = _track_int("LOOT_BAND3_QTY_MAX", 22)
        cls.LOOT_BAND3_QTY_MODE = _track_int("LOOT_BAND3_QTY_MODE", 16)
        cls.LOOT_COMMODITY_SELL_FRACTION = _track_float("LOOT_COMMODITY_SELL_FRACTION", 1.0)

        # Activity / Temperature — RETIRED rev 0031
        # MIN_GUILD_ACTIVITY, ACTIVITY_TEMP_PER_PLAYER removed; temperature
        # subsystem was never fully wired (owner-approved).
        # GUILD_ACTIVITY_DECAY_INTERVAL also retired (timer for removed system).

        # Bounty spawn check interval (renamed from BOUNTY_DELAY_RANDOM_MIN rev 0031).
        # ENV: BOUNTYBOT_BOUNTY_SPAWN_CHECK_INTERVAL_MINUTES
        cls.BOUNTY_SPAWN_CHECK_INTERVAL_MINUTES = _track_int("BOUNTY_SPAWN_CHECK_INTERVAL_MINUTES", 5)
        # Retired rev 0031: BOUNTY_DELAY_RANDOM_MAX — no live readers.
        cls.BOUNTY_SPAWN_JITTER = _track_int("BOUNTY_SPAWN_JITTER", 180)

        # Timers
        cls.CHECK_COOLDOWN = _track_int("CHECK_COOLDOWN", 180)
        cls.DUEL_REQUEST_EXPIRY = _track_int("DUEL_REQUEST_EXPIRY", 86400)
        cls.TIER_CHANGE_COOLDOWN = _track_int("TIER_CHANGE_COOLDOWN", 86400)

        # DB Data Retention
        cls.BOUNTY_RETENTION_HOURS = _track_int("BOUNTY_RETENTION_HOURS", 24)
        cls.DUEL_RETENTION_HOURS = _track_int("DUEL_RETENTION_HOURS", 24)
        cls.AUDIT_RETENTION_DAYS = _track_int("AUDIT_RETENTION_DAYS", 30)
        cls.EVENT_METRICS_RETENTION_DAYS = _track_int("EVENT_METRICS_RETENTION_DAYS", 30)

        # Shop stock generation
        # Retired rev 0031: SHOP_DEFAULT_SHIPS_NUM, SHOP_DEFAULT_WEAPONS_NUM,
        # SHOP_DEFAULT_MODULES_NUM, SHOP_DEFAULT_TURRETS_NUM, SHOP_DEFAULT_TOOLS_NUM,
        # TURRET_SPAWN_PROBABILITY — removed from load(); constants deleted.
        cls.SHOP_SECONDARY_QTY_SCALER_HEAVY = _track_int("SHOP_SECONDARY_QTY_SCALER_HEAVY", 5)
        cls.SHOP_SECONDARY_QTY_SCALER_STANDARD = _track_int("SHOP_SECONDARY_QTY_SCALER_STANDARD", 10)
        cls.SHOP_COMBAT_MODULE_PROB = _track_float("SHOP_COMBAT_MODULE_PROB", 0.75)
        cls.SHOP_BANDED_TL_WEIGHT = _track_float("SHOP_BANDED_TL_WEIGHT", 0.7)
        cls.SHOP_TL_BAND_LO_BRONZE = _track_int("SHOP_TL_BAND_LO_BRONZE", 1)
        cls.SHOP_TL_BAND_HI_BRONZE = _track_int("SHOP_TL_BAND_HI_BRONZE", 2)
        cls.SHOP_TL_BAND_LO_SILVER = _track_int("SHOP_TL_BAND_LO_SILVER", 1)
        cls.SHOP_TL_BAND_HI_SILVER = _track_int("SHOP_TL_BAND_HI_SILVER", 4)
        cls.SHOP_TL_BAND_LO_GOLD = _track_int("SHOP_TL_BAND_LO_GOLD", 4)
        cls.SHOP_TL_BAND_HI_GOLD = _track_int("SHOP_TL_BAND_HI_GOLD", 7)
        cls.SHOP_TL_BAND_LO_PLATINUM = _track_int("SHOP_TL_BAND_LO_PLATINUM", 7)
        cls.SHOP_TL_BAND_HI_PLATINUM = _track_int("SHOP_TL_BAND_HI_PLATINUM", 10)
        cls.SHOP_UPTIER_TL_DECAY = _track_float("SHOP_UPTIER_TL_DECAY", 0.6)
        cls.SHOP_DOWNTIER_TL_DECAY = _track_float("SHOP_DOWNTIER_TL_DECAY", 0.45)

        # Duels
        # Retired rev 0031: DUEL_LOG_MAX_LENGTH, DUEL_CLOAK_CHANCE — no live readers.
        # DUEL_VARIANCE_PERCENT and BOUNTY_PVC_ARMOUR_BUFF_FACTOR retired in T10.

        # Inventory
        cls.MAX_SHIP_NICKNAME_LENGTH = _track_int("MAX_SHIP_NICKNAME_LENGTH", 100)

        # Demotion
        cls.DEMOTION_CREDIT_PENALTY_PCT = _track_int("DEMOTION_CREDIT_PENALTY_PCT", 10)

        # Classic mode
        cls.CLASSIC_CREDITS_PER_CHECK = _track_int("CLASSIC_CREDITS_PER_CHECK", 1000)

        # XP multiplier
        cls.BOUNTY_REWARD_TO_XP_GAIN_MULT = _track_float("BOUNTY_REWARD_TO_XP_GAIN_MULT", 0.1)

        # Bounty winner reserve factor
        cls.BOUNTY_WINNER_RESERVE_FACTOR = _track_float("BOUNTY_WINNER_RESERVE_FACTOR", 0.25)

        # Bronze combat-bonus multiplier (issue #51)
        cls.BRONZE_COMBAT_BONUS_BASE_MULT = _track_float("BRONZE_COMBAT_BONUS_BASE_MULT", 0.40)
        cls.BRONZE_COMBAT_BONUS_PER_PRESTIGE = _track_float("BRONZE_COMBAT_BONUS_PER_PRESTIGE", 0.10)
        cls.BRONZE_COMBAT_BONUS_CAP = _track_float("BRONZE_COMBAT_BONUS_CAP", 1.00)

        def _track_int_list(key: str, default: list[int]) -> list[int]:
            val = cls._env_int_list(key, default)
            if os.environ.get(f"BOUNTYBOT_{key}") is not None:
                _overrides.append(f"{key}={val}")
            return val

        # Combat System — Phase-1 Constants (Appendix A)
        cls.CLOAK_SET_VALUE = _track_float("CLOAK_SET_VALUE", 0.25)
        cls.BOOSTER_ACCURACY_DEBUFF_FACTOR = _track_float("BOOSTER_ACCURACY_DEBUFF_FACTOR", 0.10)
        cls.THRUSTER_ACCURACY_BONUS_FACTOR = _track_float("THRUSTER_ACCURACY_BONUS_FACTOR", 0.10)
        cls.AUTO_TURRET_ACCURACY_MULTIPLIER = _track_float("AUTO_TURRET_ACCURACY_MULTIPLIER", 0.85)
        cls.PLAYER_BASE_ACCURACY = _track_float("PLAYER_BASE_ACCURACY", 0.60)
        cls.NPC_BASE_ACCURACY = _track_float("NPC_BASE_ACCURACY", 0.50)
        cls.ACCURACY_CLAMP_MIN = _track_float("ACCURACY_CLAMP_MIN", 0.05)
        cls.ACCURACY_CLAMP_MAX = _track_float("ACCURACY_CLAMP_MAX", 0.99)
        cls.SCANNER_TIER_B_BONUS_PP = _track_int("SCANNER_TIER_B_BONUS_PP", 5)
        cls.SCANNER_TIER_C_BONUS_PP = _track_int("SCANNER_TIER_C_BONUS_PP", 10)
        cls.KETAR_I_REPAIR_PCT_PER_SEC = _track_float("KETAR_I_REPAIR_PCT_PER_SEC", 0.02)
        cls.KETAR_II_REPAIR_PCT_PER_SEC = _track_float("KETAR_II_REPAIR_PCT_PER_SEC", 0.04)
        cls.TICK_MS = _track_int("TICK_MS", 10)
        cls.MAX_FIGHT_TICKS = _track_int("MAX_FIGHT_TICKS", 60000)
        cls.STARTING_DISTANCE_M = _track_int("STARTING_DISTANCE_M", 5000)
        cls.BASE_SHIP_SPEED_MPS = _track_int("BASE_SHIP_SPEED_MPS", 150)
        cls.MIN_DISTANCE_M = _track_int("MIN_DISTANCE_M", 300)
        cls.THRUSTER_WINDOW_M = _track_int("THRUSTER_WINDOW_M", 750)
        cls.SHOCK_BLAST_TRIGGER_RANGE_M = _track_int("SHOCK_BLAST_TRIGGER_RANGE_M", 500)
        cls.CLOAK_HP_THRESHOLDS_PCT = _track_int_list("CLOAK_HP_THRESHOLDS_PCT", [66, 33])
        cls.BOOSTER_HP_THRESHOLDS_PCT = _track_int_list("BOOSTER_HP_THRESHOLDS_PCT", [80, 60, 40, 20])
        cls.EMERGENCY_SYSTEM_INVULN_S = _track_int("EMERGENCY_SYSTEM_INVULN_S", 10)
        cls.NUKE_MAGNITUDE_SCALE = _track_float("NUKE_MAGNITUDE_SCALE", 0.10)
        cls.NUKE_FRIENDLY_FACTOR = _track_float("NUKE_FRIENDLY_FACTOR", 0.50)
        cls.NUKE_RANGE_REGIME_THRESHOLD_M = _track_int("NUKE_RANGE_REGIME_THRESHOLD_M", 1000)
        cls.NUKE_LR_NEAR_FRAC = _track_float("NUKE_LR_NEAR_FRAC", 0.40)
        cls.NUKE_CR_SHORT_M = _track_int("NUKE_CR_SHORT_M", 600)
        cls.NUKE_CR_OVERSHOOT_M = _track_int("NUKE_CR_OVERSHOOT_M", 400)
        cls.NUKE_STACK_FALLOFF = _track_float("NUKE_STACK_FALLOFF", 0.5)
        cls.PVC_DAMAGE_REDUCTION = _track_float("PVC_DAMAGE_REDUCTION", 0.33)
        cls.COMBAT_LOG_BOUNTY_RETENTION_HOURS = _track_int("COMBAT_LOG_BOUNTY_RETENTION_HOURS", 48)
        cls.COMBAT_LOG_PVP_RETENTION_HOURS = _track_int("COMBAT_LOG_PVP_RETENTION_HOURS", 8760)
        cls.COMBAT_LAYER_REEMIT_FRACTION = _track_float("COMBAT_LAYER_REEMIT_FRACTION", 0.25)

        # Combat log recap denoising knobs (Phase 1 + 2 + 3)
        cls.RECAP_COLLAPSE_MIN_RUN = _track_int("RECAP_COLLAPSE_MIN_RUN", 3)
        cls.RECAP_GAP_FILL_S = _track_float("RECAP_GAP_FILL_S", 20.0)
        cls.RECAP_NUKE_SUMMARY_MIN_COUNT = _track_int("RECAP_NUKE_SUMMARY_MIN_COUNT", 3)
        cls.RECAP_NUKE_SIGNIFICANCE_FRACTION = _track_float("RECAP_NUKE_SIGNIFICANCE_FRACTION", 0.25)

        if _overrides:
            _flogger.info(f"GameConstants env overrides detected: {', '.join(_overrides)}")
        else:
            _flogger.info("GameConstants.load() — no env overrides, using defaults")


# ---------------------------------------------------------------------------
# Module-level invariant assertions — drift guards for the shop module buckets.
# All 21 distinct module Item.type discriminators must be covered exactly once.
# ---------------------------------------------------------------------------
assert GameConstants.SHOP_JUNK_MODULE_TYPES.isdisjoint(GameConstants.SHOP_FILLER_MODULE_TYPES), (
    "SHOP_JUNK_MODULE_TYPES and SHOP_FILLER_MODULE_TYPES share members: "
    f"{GameConstants.SHOP_JUNK_MODULE_TYPES & GameConstants.SHOP_FILLER_MODULE_TYPES}"
)
assert GameConstants.SHOP_JUNK_MODULE_TYPES.isdisjoint(GameConstants.SHOP_COMBAT_MODULE_TYPES), (
    "SHOP_JUNK_MODULE_TYPES and SHOP_COMBAT_MODULE_TYPES share members: "
    f"{GameConstants.SHOP_JUNK_MODULE_TYPES & GameConstants.SHOP_COMBAT_MODULE_TYPES}"
)
assert GameConstants.SHOP_FILLER_MODULE_TYPES.isdisjoint(GameConstants.SHOP_COMBAT_MODULE_TYPES), (
    "SHOP_FILLER_MODULE_TYPES and SHOP_COMBAT_MODULE_TYPES share members: "
    f"{GameConstants.SHOP_FILLER_MODULE_TYPES & GameConstants.SHOP_COMBAT_MODULE_TYPES}"
)
_ALL_SHOP_MODULE_TYPES = (
    GameConstants.SHOP_JUNK_MODULE_TYPES
    | GameConstants.SHOP_FILLER_MODULE_TYPES
    | GameConstants.SHOP_COMBAT_MODULE_TYPES
)
assert len(_ALL_SHOP_MODULE_TYPES) == 21, (
    f"Expected 21 total module types across all shop buckets, got {len(_ALL_SHOP_MODULE_TYPES)}"
)


def resolve_constant[T](guild_config: Any | None, field: str, fallback: T) -> T:
    """Resolve a GameConstants value with per-guild override.

    Returns guild_config.<field> if it exists and is not None, else `fallback`.
    A value of 0 or 0.0 is a valid override and is NOT treated as None.
    Pass None for guild_config when no per-guild context is available.
    """
    if guild_config is None:
        return fallback
    val = getattr(guild_config, field, None)
    if val is None:
        return fallback
    return val


def resolve_flattened[T](
    guild_config: Any | None,
    scalar_field: str,
    fallback: T,
) -> T:
    """Resolve a per-guild scalar from a flat scalar column (issue #70, revision 0033).

    Fallback chain (revision 0033 — JSONB dicts dropped):
    1. ``guild_config.<scalar_field>`` — flat scalar column (NULL = not set).
    2. ``fallback`` — global :class:`GameConstants` scalar constant.

    A value of 0 or 0.0 on the scalar column is a valid override and is NOT
    treated as None (same semantics as :func:`resolve_constant`).
    Pass None for guild_config when no per-guild context is available.
    """
    if guild_config is None:
        return fallback
    # 1. Try flat scalar column.
    val = getattr(guild_config, scalar_field, None)
    if val is not None:
        return val
    # 2. Global fallback.
    return fallback
