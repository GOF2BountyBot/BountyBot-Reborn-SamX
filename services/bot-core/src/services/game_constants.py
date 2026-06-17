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

    # Maximum tech level for criminal loadouts per division.
    # Bronze is capped low to ensure new players with Betty can compete.
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
    # ------------------------------------------------------------------

    MAX_BOUNTIES_PER_DIVISION: int = 5
    CLOSE_BOUNTY_THRESHOLD: int = 4  # systems ahead for proximity hint
    SHIP_VALUE_REWARD_PERCENTAGE: float = 0.01  # 1% of criminal's ship value
    CRIMINAL_EQUIP_DAMAGELESS_WEAPON_CHANCE: int = 20  # %
    CRIMINAL_MAX_GEAR_UPGRADE: int = 1  # TL levels above criminal
    MAX_ROUTE_LENGTH: int = 50  # A* pathfinding limit

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
    # Activity / Temperature
    # ------------------------------------------------------------------

    GUILD_ACTIVITY_DECAY_RATE: float = 2 / 3  # ~0.667
    MIN_GUILD_ACTIVITY: float = 1.0
    ACTIVITY_TEMP_PER_PLAYER: int = 1

    # ------------------------------------------------------------------
    # Bounty Spawn Delay (minutes)
    # ------------------------------------------------------------------

    BOUNTY_DELAY_RANDOM_MIN: int = 5
    BOUNTY_DELAY_RANDOM_MAX: int = 7
    BOUNTY_SPAWN_JITTER: int = 180  # Up to 3 minutes of random offset on each spawn check

    # ------------------------------------------------------------------
    # Timers (seconds)
    # ------------------------------------------------------------------

    GUILD_ACTIVITY_DECAY_INTERVAL: int = 3600  # 1 hour
    SHOP_REFRESH_INTERVAL: int = 21600  # 6 hours
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

    # ------------------------------------------------------------------
    # Shop Stock Generation
    # ------------------------------------------------------------------

    SHOP_DEFAULT_SHIPS_NUM: int = 5
    SHOP_DEFAULT_WEAPONS_NUM: int = 5
    SHOP_DEFAULT_MODULES_NUM: int = 5
    SHOP_DEFAULT_TURRETS_NUM: int = 2
    SHOP_DEFAULT_TOOLS_NUM: int = 0
    TURRET_SPAWN_PROBABILITY: int = 45  # %

    # Secondary weapons are consumable rounds; scale the rolled shop quantity so a
    # single refresh cycle (6h default) can supply multiple players. Heavy ordnance
    # scales less than standard ammo (missile, rocket, cluster-missile, ...).
    # An item whose subtype is missing/unknown gets the STANDARD scaler.
    SHOP_HEAVY_SECONDARY_SUBTYPES: frozenset[str] = frozenset({"nuke", "shock-blast", "cluster-missile"})
    SHOP_SECONDARY_QTY_SCALER_HEAVY: int = 5
    SHOP_SECONDARY_QTY_SCALER_STANDARD: int = 10

    # ------------------------------------------------------------------
    # Shop Rank Counts
    # ------------------------------------------------------------------

    NUM_SHIP_RANKS: int = 10
    NUM_WEAPON_RANKS: int = 10
    NUM_MODULE_RANKS: int = 7
    NUM_TURRET_RANKS: int = 3

    # ------------------------------------------------------------------
    # Duels
    # ------------------------------------------------------------------

    DUEL_LOG_MAX_LENGTH: int = 10
    DUEL_CLOAK_CHANCE: int = 20  # %
    # DUEL_VARIANCE_PERCENT — retired in T10 (SimpleTTKResolver removed; TickResolver has no variance).
    # BOUNTY_PVC_ARMOUR_BUFF_FACTOR — retired in T10 (replaced by PVC_DAMAGE_REDUCTION §3).

    # ------------------------------------------------------------------
    # Item Type Vocabulary
    # ------------------------------------------------------------------

    # All concrete item types present in the data model (used for browsing/catalog).
    CATALOG_ITEM_TYPES: frozenset[str] = frozenset(
        {"ship", "primary_weapon", "secondary_weapon", "turret_weapon", "module"}
    )

    # All concrete item types the data model has slots for (must match CATALOG_ITEM_TYPES
    # once all mechanics are enabled; currently identical).
    PLAYABLE_ITEM_TYPES: frozenset[str] = frozenset(
        {"ship", "primary_weapon", "secondary_weapon", "turret_weapon", "module"}
    )

    # Concrete item types exposed on the user-facing economy/equip surface TODAY.
    # secondary_weapon is included; the shop excludes deferred subtypes (emp-bomb,
    # mine, sentry-gun) via DEFERRED_SECONDARY_SUBTYPES in combat_models.py.
    # This is the SINGLE lever that gates item-type exposure across all
    # economy/loadout flows — no scattered if-branches needed.
    CURRENTLY_ENABLED_TYPES: frozenset[str] = frozenset(
        {"ship", "primary_weapon", "secondary_weapon", "turret_weapon", "module"}
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

    MAX_SHIP_NICKNAME_LENGTH: int = 30
    KAAMO_MAX_CAPACITY: int = 70

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
    # Combat System — Future Mechanics (placeholders, currently unused)
    # ------------------------------------------------------------------

    # Accuracy: fraction of shots that hit (1.0 = 100% accuracy)
    DEFAULT_ACCURACY: float = 1.0
    DEFAULT_EVASION: float = 0.0

    # Equipment effect placeholders (all neutral/zero = no effect)
    CLOAK_ACCURACY_PENALTY: float = 0.0
    SCANNER_ACCURACY_BONUS: float = 0.0
    THRUSTER_EVASION_BONUS: float = 0.0

    # Shield/repair mechanics (0.0 = disabled)
    SHIELD_RECHARGE_RATE: float = 0.0
    REPAIR_BOT_HEAL_RATE: float = 0.0

    # Booster (1.0 = neutral, no boost)
    BOOSTER_DPS_MULTIPLIER: float = 1.0

    # Tick-based simulation (for future combat resolver)
    COMBAT_TICK_RATE: float = 1.0

    # Persistent damage (0.0 = instant full heal between fights)
    PERSISTENT_DAMAGE_DECAY_RATE: float = 0.0

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
    KETAR_I_REPAIR_PCT_PER_SEC: float = 0.025
    KETAR_II_REPAIR_PCT_PER_SEC: float = 0.050

    # Tick / timing (§1)
    TICK_MS: int = 10
    MAX_FIGHT_TICKS: int = 18000

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

    # Combat log retention (§12)
    COMBAT_LOG_RETENTION_HOURS: int = 72

    # CI-21: layer_depleted re-emit fraction (latch clears when layer recovers ≥ this fraction of max).
    # Override via: BOUNTYBOT_COMBAT_LAYER_REEMIT_FRACTION=0.25
    COMBAT_LAYER_REEMIT_FRACTION: float = 0.25

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

        # Bounty system
        cls.MAX_BOUNTIES_PER_DIVISION = _track_int("MAX_BOUNTIES_PER_DIVISION", 5)
        cls.CLOSE_BOUNTY_THRESHOLD = _track_int("CLOSE_BOUNTY_THRESHOLD", 4)
        cls.MAX_ROUTE_LENGTH = _track_int("MAX_ROUTE_LENGTH", 50)
        cls.CRIMINAL_EQUIP_DAMAGELESS_WEAPON_CHANCE = _track_int("CRIMINAL_EQUIP_DAMAGELESS_WEAPON_CHANCE", 20)
        cls.CRIMINAL_MAX_GEAR_UPGRADE = _track_int("CRIMINAL_MAX_GEAR_UPGRADE", 1)
        cls.SHIP_VALUE_REWARD_PERCENTAGE = _track_float("SHIP_VALUE_REWARD_PERCENTAGE", 0.01)

        # Activity
        cls.MIN_GUILD_ACTIVITY = _track_float("MIN_GUILD_ACTIVITY", 1.0)
        cls.ACTIVITY_TEMP_PER_PLAYER = _track_int("ACTIVITY_TEMP_PER_PLAYER", 1)

        # Bounty spawn delay
        cls.BOUNTY_DELAY_RANDOM_MIN = _track_int("BOUNTY_DELAY_RANDOM_MIN", 5)
        cls.BOUNTY_DELAY_RANDOM_MAX = _track_int("BOUNTY_DELAY_RANDOM_MAX", 7)
        cls.BOUNTY_SPAWN_JITTER = _track_int("BOUNTY_SPAWN_JITTER", 180)

        # Timers
        cls.GUILD_ACTIVITY_DECAY_INTERVAL = _track_int("GUILD_ACTIVITY_DECAY_INTERVAL", 3600)
        cls.SHOP_REFRESH_INTERVAL = _track_int("SHOP_REFRESH_INTERVAL", 21600)
        cls.CHECK_COOLDOWN = _track_int("CHECK_COOLDOWN", 180)
        cls.DUEL_REQUEST_EXPIRY = _track_int("DUEL_REQUEST_EXPIRY", 86400)
        cls.TIER_CHANGE_COOLDOWN = _track_int("TIER_CHANGE_COOLDOWN", 86400)

        # DB Data Retention
        cls.BOUNTY_RETENTION_HOURS = _track_int("BOUNTY_RETENTION_HOURS", 24)
        cls.DUEL_RETENTION_HOURS = _track_int("DUEL_RETENTION_HOURS", 24)
        cls.AUDIT_RETENTION_DAYS = _track_int("AUDIT_RETENTION_DAYS", 30)

        # Shop stock generation
        cls.SHOP_DEFAULT_SHIPS_NUM = _track_int("SHOP_DEFAULT_SHIPS_NUM", 5)
        cls.SHOP_DEFAULT_WEAPONS_NUM = _track_int("SHOP_DEFAULT_WEAPONS_NUM", 5)
        cls.SHOP_DEFAULT_MODULES_NUM = _track_int("SHOP_DEFAULT_MODULES_NUM", 5)
        cls.SHOP_DEFAULT_TURRETS_NUM = _track_int("SHOP_DEFAULT_TURRETS_NUM", 2)
        cls.SHOP_DEFAULT_TOOLS_NUM = _track_int("SHOP_DEFAULT_TOOLS_NUM", 0)
        cls.TURRET_SPAWN_PROBABILITY = _track_int("TURRET_SPAWN_PROBABILITY", 45)
        cls.SHOP_SECONDARY_QTY_SCALER_HEAVY = _track_int("SHOP_SECONDARY_QTY_SCALER_HEAVY", 5)
        cls.SHOP_SECONDARY_QTY_SCALER_STANDARD = _track_int("SHOP_SECONDARY_QTY_SCALER_STANDARD", 10)

        # Duels
        cls.DUEL_LOG_MAX_LENGTH = _track_int("DUEL_LOG_MAX_LENGTH", 10)
        cls.DUEL_CLOAK_CHANCE = _track_int("DUEL_CLOAK_CHANCE", 20)
        # DUEL_VARIANCE_PERCENT and BOUNTY_PVC_ARMOUR_BUFF_FACTOR retired in T10.

        # Inventory
        cls.MAX_SHIP_NICKNAME_LENGTH = _track_int("MAX_SHIP_NICKNAME_LENGTH", 30)
        cls.KAAMO_MAX_CAPACITY = _track_int("KAAMO_MAX_CAPACITY", 70)

        # Demotion
        cls.DEMOTION_CREDIT_PENALTY_PCT = _track_int("DEMOTION_CREDIT_PENALTY_PCT", 10)

        # Classic mode
        cls.CLASSIC_CREDITS_PER_CHECK = _track_int("CLASSIC_CREDITS_PER_CHECK", 1000)

        # XP multiplier
        cls.BOUNTY_REWARD_TO_XP_GAIN_MULT = _track_float("BOUNTY_REWARD_TO_XP_GAIN_MULT", 0.1)

        # Bounty winner reserve factor
        cls.BOUNTY_WINNER_RESERVE_FACTOR = _track_float("BOUNTY_WINNER_RESERVE_FACTOR", 0.25)

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
        cls.KETAR_I_REPAIR_PCT_PER_SEC = _track_float("KETAR_I_REPAIR_PCT_PER_SEC", 0.025)
        cls.KETAR_II_REPAIR_PCT_PER_SEC = _track_float("KETAR_II_REPAIR_PCT_PER_SEC", 0.050)
        cls.TICK_MS = _track_int("TICK_MS", 10)
        cls.MAX_FIGHT_TICKS = _track_int("MAX_FIGHT_TICKS", 18000)
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
        cls.COMBAT_LOG_RETENTION_HOURS = _track_int("COMBAT_LOG_RETENTION_HOURS", 72)
        cls.COMBAT_LAYER_REEMIT_FRACTION = _track_float("COMBAT_LAYER_REEMIT_FRACTION", 0.25)

        if _overrides:
            _flogger.info(f"GameConstants env overrides detected: {', '.join(_overrides)}")
        else:
            _flogger.info("GameConstants.load() — no env overrides, using defaults")


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
