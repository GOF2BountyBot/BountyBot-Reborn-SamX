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

    # ------------------------------------------------------------------
    # Tech Levels
    # ------------------------------------------------------------------

    MIN_TECH_LEVEL: int = 1
    MAX_TECH_LEVEL: int = 10

    # ------------------------------------------------------------------
    # Divisions
    # ------------------------------------------------------------------

    DIVISION_NAMES: list[str] = ["bronze", "silver", "gold"]
    # (min_level, max_level) per division — index matches DIVISION_NAMES
    DIVISION_BOUNDARIES: list[tuple[int, int]] = [(0, 3), (4, 7), (8, 10)]

    # ------------------------------------------------------------------
    # XP Level Boundaries (11 entries; index == level)
    # ------------------------------------------------------------------

    XP_LEVEL_BOUNDARIES: list[int] = [
        -1,       # level 0  (sentinel)
        0,        # level 1
        1050,     # level 2
        2000,     # level 3
        3500,     # level 4
        10000,    # level 5
        18000,    # level 6
        61000,    # level 7
        71000,    # level 8
        90000,    # level 9
        1000000,  # level 10
    ]

    # ------------------------------------------------------------------
    # XP Reward Multiplier
    # ------------------------------------------------------------------

    BOUNTY_REWARD_TO_XP_GAIN_MULT: float = 0.1

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
    CLOSE_BOUNTY_THRESHOLD: int = 4          # systems ahead for proximity hint
    SHIP_VALUE_REWARD_PERCENTAGE: float = 0.01  # 1% of criminal's ship value
    CRIMINAL_EQUIP_DAMAGELESS_WEAPON_CHANCE: int = 20  # %
    CRIMINAL_MAX_GEAR_UPGRADE: int = 1       # TL levels above criminal
    MAX_ROUTE_LENGTH: int = 50               # A* pathfinding limit

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

    # ------------------------------------------------------------------
    # Timers (seconds)
    # ------------------------------------------------------------------

    GUILD_ACTIVITY_DECAY_INTERVAL: int = 3600   # 1 hour
    SHOP_REFRESH_INTERVAL: int = 21600           # 6 hours
    CHECK_COOLDOWN: int = 180                    # 3 minutes
    DUEL_REQUEST_EXPIRY: int = 86400             # 1 day

    # ------------------------------------------------------------------
    # Shop Stock Generation
    # ------------------------------------------------------------------

    SHOP_DEFAULT_SHIPS_NUM: int = 5
    SHOP_DEFAULT_WEAPONS_NUM: int = 5
    SHOP_DEFAULT_MODULES_NUM: int = 5
    SHOP_DEFAULT_TURRETS_NUM: int = 2
    SHOP_DEFAULT_TOOLS_NUM: int = 0
    TURRET_SPAWN_PROBABILITY: int = 45  # %

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

    DUEL_VARIANCE_PERCENT: float = 0.05  # ±5%
    DUEL_LOG_MAX_LENGTH: int = 10
    DUEL_CLOAK_CHANCE: int = 20  # %

    # ------------------------------------------------------------------
    # Inventory
    # ------------------------------------------------------------------

    MAX_SHIP_NICKNAME_LENGTH: int = 30
    KAAMO_MAX_CAPACITY: int = 70

    # ------------------------------------------------------------------
    # Classic Mode
    # ------------------------------------------------------------------

    CLASSIC_CREDITS_PER_CHECK: int = 1000
    CLASSIC_DIVISION_NAME: str = "bronze"

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
    # Environment variable overrides (operational constants only)
    # ------------------------------------------------------------------

    @classmethod
    def load(cls) -> None:
        """Apply environment variable overrides for operational constants.

        Call once at application startup (e.g. in ``main.py``).  Constants
        that govern game-balance (XP boundaries, division definitions, module
        equip limits) are intentionally excluded from runtime overrides.
        """
        # Bounty system
        cls.MAX_BOUNTIES_PER_DIVISION = cls._env_int("MAX_BOUNTIES_PER_DIVISION", 5)
        cls.CLOSE_BOUNTY_THRESHOLD = cls._env_int("CLOSE_BOUNTY_THRESHOLD", 4)
        cls.MAX_ROUTE_LENGTH = cls._env_int("MAX_ROUTE_LENGTH", 50)
        cls.CRIMINAL_EQUIP_DAMAGELESS_WEAPON_CHANCE = cls._env_int(
            "CRIMINAL_EQUIP_DAMAGELESS_WEAPON_CHANCE", 20
        )
        cls.CRIMINAL_MAX_GEAR_UPGRADE = cls._env_int("CRIMINAL_MAX_GEAR_UPGRADE", 1)
        cls.SHIP_VALUE_REWARD_PERCENTAGE = cls._env_float(
            "SHIP_VALUE_REWARD_PERCENTAGE", 0.01
        )

        # Activity
        cls.MIN_GUILD_ACTIVITY = cls._env_float("MIN_GUILD_ACTIVITY", 1.0)
        cls.ACTIVITY_TEMP_PER_PLAYER = cls._env_int("ACTIVITY_TEMP_PER_PLAYER", 1)

        # Bounty spawn delay
        cls.BOUNTY_DELAY_RANDOM_MIN = cls._env_int("BOUNTY_DELAY_RANDOM_MIN", 5)
        cls.BOUNTY_DELAY_RANDOM_MAX = cls._env_int("BOUNTY_DELAY_RANDOM_MAX", 7)

        # Timers
        cls.GUILD_ACTIVITY_DECAY_INTERVAL = cls._env_int(
            "GUILD_ACTIVITY_DECAY_INTERVAL", 3600
        )
        cls.SHOP_REFRESH_INTERVAL = cls._env_int("SHOP_REFRESH_INTERVAL", 21600)
        cls.CHECK_COOLDOWN = cls._env_int("CHECK_COOLDOWN", 180)
        cls.DUEL_REQUEST_EXPIRY = cls._env_int("DUEL_REQUEST_EXPIRY", 86400)

        # Shop stock generation
        cls.SHOP_DEFAULT_SHIPS_NUM = cls._env_int("SHOP_DEFAULT_SHIPS_NUM", 5)
        cls.SHOP_DEFAULT_WEAPONS_NUM = cls._env_int("SHOP_DEFAULT_WEAPONS_NUM", 5)
        cls.SHOP_DEFAULT_MODULES_NUM = cls._env_int("SHOP_DEFAULT_MODULES_NUM", 5)
        cls.SHOP_DEFAULT_TURRETS_NUM = cls._env_int("SHOP_DEFAULT_TURRETS_NUM", 2)
        cls.SHOP_DEFAULT_TOOLS_NUM = cls._env_int("SHOP_DEFAULT_TOOLS_NUM", 0)
        cls.TURRET_SPAWN_PROBABILITY = cls._env_int("TURRET_SPAWN_PROBABILITY", 45)

        # Duels
        cls.DUEL_VARIANCE_PERCENT = cls._env_float("DUEL_VARIANCE_PERCENT", 0.05)
        cls.DUEL_LOG_MAX_LENGTH = cls._env_int("DUEL_LOG_MAX_LENGTH", 10)
        cls.DUEL_CLOAK_CHANCE = cls._env_int("DUEL_CLOAK_CHANCE", 20)

        # Inventory
        cls.MAX_SHIP_NICKNAME_LENGTH = cls._env_int("MAX_SHIP_NICKNAME_LENGTH", 30)
        cls.KAAMO_MAX_CAPACITY = cls._env_int("KAAMO_MAX_CAPACITY", 70)

        # Classic mode
        cls.CLASSIC_CREDITS_PER_CHECK = cls._env_int("CLASSIC_CREDITS_PER_CHECK", 1000)

        # XP multiplier
        cls.BOUNTY_REWARD_TO_XP_GAIN_MULT = cls._env_float(
            "BOUNTY_REWARD_TO_XP_GAIN_MULT", 0.1
        )
