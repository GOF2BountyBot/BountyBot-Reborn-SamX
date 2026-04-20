"""
Unit tests for GameConstants.

Verifies that all legacy default values are correctly defined and that
environment-variable overrides work as expected.
"""

import os

import pytest
from services.game_constants import GameConstants

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_constants() -> None:
    """Restore all overridable class attributes to their hardcoded defaults."""
    GameConstants.MAX_BOUNTIES_PER_DIVISION = 5
    GameConstants.CLOSE_BOUNTY_THRESHOLD = 4
    GameConstants.MAX_ROUTE_LENGTH = 50
    GameConstants.CRIMINAL_EQUIP_DAMAGELESS_WEAPON_CHANCE = 20
    GameConstants.CRIMINAL_MAX_GEAR_UPGRADE = 1
    GameConstants.SHIP_VALUE_REWARD_PERCENTAGE = 0.01
    GameConstants.MIN_GUILD_ACTIVITY = 1.0
    GameConstants.ACTIVITY_TEMP_PER_PLAYER = 1
    GameConstants.BOUNTY_DELAY_RANDOM_MIN = 5
    GameConstants.BOUNTY_DELAY_RANDOM_MAX = 7
    GameConstants.BOUNTY_SPAWN_JITTER = 180
    GameConstants.GUILD_ACTIVITY_DECAY_INTERVAL = 3600
    GameConstants.SHOP_REFRESH_INTERVAL = 21600
    GameConstants.CHECK_COOLDOWN = 180
    GameConstants.DUEL_REQUEST_EXPIRY = 86400
    GameConstants.SHOP_DEFAULT_SHIPS_NUM = 5
    GameConstants.SHOP_DEFAULT_WEAPONS_NUM = 5
    GameConstants.SHOP_DEFAULT_MODULES_NUM = 5
    GameConstants.SHOP_DEFAULT_TURRETS_NUM = 2
    GameConstants.SHOP_DEFAULT_TOOLS_NUM = 0
    GameConstants.TURRET_SPAWN_PROBABILITY = 45
    GameConstants.DUEL_VARIANCE_PERCENT = 0.05
    GameConstants.DUEL_LOG_MAX_LENGTH = 10
    GameConstants.DUEL_CLOAK_CHANCE = 20
    GameConstants.MAX_SHIP_NICKNAME_LENGTH = 30
    GameConstants.KAAMO_MAX_CAPACITY = 70
    GameConstants.CLASSIC_CREDITS_PER_CHECK = 1000
    GameConstants.BOUNTY_REWARD_TO_XP_GAIN_MULT = 0.1


# ---------------------------------------------------------------------------
# Tech Levels
# ---------------------------------------------------------------------------


class TestTechLevels:
    def test_min_tech_level(self) -> None:
        assert GameConstants.MIN_TECH_LEVEL == 1

    def test_max_tech_level(self) -> None:
        assert GameConstants.MAX_TECH_LEVEL == 10


# ---------------------------------------------------------------------------
# Division Max TL
# ---------------------------------------------------------------------------


class TestDivisionMaxTL:
    def test_division_max_tl_is_dict(self) -> None:
        assert isinstance(GameConstants.DIVISION_MAX_TL, dict)

    def test_bronze_max_tl(self) -> None:
        """Bronze is capped at TL 2 so new Betty-class players can compete."""
        assert GameConstants.DIVISION_MAX_TL["bronze"] == 2

    def test_silver_max_tl(self) -> None:
        assert GameConstants.DIVISION_MAX_TL["silver"] == 5

    def test_gold_max_tl(self) -> None:
        assert GameConstants.DIVISION_MAX_TL["gold"] == 8

    def test_platinum_max_tl(self) -> None:
        assert GameConstants.DIVISION_MAX_TL["platinum"] == 10

    def test_all_expected_divisions_present(self) -> None:
        expected = {"bronze", "silver", "gold", "platinum"}
        assert set(GameConstants.DIVISION_MAX_TL.keys()) == expected

    def test_bronze_cap_lower_than_silver(self) -> None:
        assert GameConstants.DIVISION_MAX_TL["bronze"] < GameConstants.DIVISION_MAX_TL["silver"]

    def test_silver_cap_lower_than_gold(self) -> None:
        assert GameConstants.DIVISION_MAX_TL["silver"] < GameConstants.DIVISION_MAX_TL["gold"]

    def test_gold_cap_lower_than_platinum(self) -> None:
        assert GameConstants.DIVISION_MAX_TL["gold"] < GameConstants.DIVISION_MAX_TL["platinum"]

    def test_all_caps_within_valid_tl_range(self) -> None:
        for div, cap in GameConstants.DIVISION_MAX_TL.items():
            assert GameConstants.MIN_TECH_LEVEL <= cap <= GameConstants.MAX_TECH_LEVEL, (
                f"Division {div!r} cap {cap} outside valid TL range"
            )


# ---------------------------------------------------------------------------
# Divisions
# ---------------------------------------------------------------------------


class TestDivisions:
    def test_division_names_order(self) -> None:
        assert GameConstants.DIVISION_NAMES == ["bronze", "silver", "gold"]

    def test_division_boundaries_length(self) -> None:
        assert len(GameConstants.DIVISION_BOUNDARIES) == 3

    def test_bronze_boundary(self) -> None:
        assert GameConstants.DIVISION_BOUNDARIES[0] == (0, 3)

    def test_silver_boundary(self) -> None:
        assert GameConstants.DIVISION_BOUNDARIES[1] == (4, 7)

    def test_gold_boundary(self) -> None:
        assert GameConstants.DIVISION_BOUNDARIES[2] == (8, 10)

    def test_boundaries_align_with_names(self) -> None:
        """Each boundary tuple index corresponds to the same-index division name."""
        for name, bounds in zip(GameConstants.DIVISION_NAMES, GameConstants.DIVISION_BOUNDARIES, strict=True):
            min_lvl, max_lvl = bounds
            assert min_lvl <= max_lvl, f"{name}: min must be <= max"


# ---------------------------------------------------------------------------
# XP Level Boundaries
# ---------------------------------------------------------------------------


class TestXPLevelBoundaries:
    def test_length_is_11(self) -> None:
        assert len(GameConstants.XP_LEVEL_BOUNDARIES) == 11

    def test_level_0_sentinel(self) -> None:
        assert GameConstants.XP_LEVEL_BOUNDARIES[0] == -1

    def test_level_1_boundary(self) -> None:
        assert GameConstants.XP_LEVEL_BOUNDARIES[1] == 0

    def test_level_2_boundary(self) -> None:
        assert GameConstants.XP_LEVEL_BOUNDARIES[2] == 1050

    def test_level_5_boundary(self) -> None:
        assert GameConstants.XP_LEVEL_BOUNDARIES[5] == 10000

    def test_level_10_boundary(self) -> None:
        assert GameConstants.XP_LEVEL_BOUNDARIES[10] == 1_000_000

    def test_boundaries_are_strictly_increasing(self) -> None:
        """XP requirements must grow with each level (excluding sentinel at index 0)."""
        for i in range(1, len(GameConstants.XP_LEVEL_BOUNDARIES) - 1):
            assert GameConstants.XP_LEVEL_BOUNDARIES[i] < GameConstants.XP_LEVEL_BOUNDARIES[i + 1], (
                f"Level {i} boundary {GameConstants.XP_LEVEL_BOUNDARIES[i]} is not "
                f"less than level {i + 1} boundary {GameConstants.XP_LEVEL_BOUNDARIES[i + 1]}"
            )


# ---------------------------------------------------------------------------
# XP Reward Multiplier
# ---------------------------------------------------------------------------


class TestXPRewardMultiplier:
    def test_bounty_reward_to_xp_gain_mult(self) -> None:
        assert pytest.approx(GameConstants.BOUNTY_REWARD_TO_XP_GAIN_MULT) == 0.1


# ---------------------------------------------------------------------------
# Ship Price Thresholds
# ---------------------------------------------------------------------------


class TestShipPriceThresholds:
    def test_length_is_10(self) -> None:
        assert len(GameConstants.SHIP_PRICE_THRESHOLDS) == 10

    def test_tl1_threshold(self) -> None:
        assert GameConstants.SHIP_PRICE_THRESHOLDS[0] == 50_000

    def test_tl10_threshold(self) -> None:
        assert GameConstants.SHIP_PRICE_THRESHOLDS[9] == 999_999_999

    def test_thresholds_are_strictly_increasing(self) -> None:
        for i in range(len(GameConstants.SHIP_PRICE_THRESHOLDS) - 1):
            assert GameConstants.SHIP_PRICE_THRESHOLDS[i] < GameConstants.SHIP_PRICE_THRESHOLDS[i + 1]


# ---------------------------------------------------------------------------
# Bounty System
# ---------------------------------------------------------------------------


class TestBountySystem:
    def test_max_bounties_per_division(self) -> None:
        assert GameConstants.MAX_BOUNTIES_PER_DIVISION == 5

    def test_close_bounty_threshold(self) -> None:
        assert GameConstants.CLOSE_BOUNTY_THRESHOLD == 4

    def test_ship_value_reward_percentage(self) -> None:
        assert pytest.approx(GameConstants.SHIP_VALUE_REWARD_PERCENTAGE) == 0.01

    def test_criminal_equip_damageless_weapon_chance(self) -> None:
        assert GameConstants.CRIMINAL_EQUIP_DAMAGELESS_WEAPON_CHANCE == 20

    def test_criminal_max_gear_upgrade(self) -> None:
        assert GameConstants.CRIMINAL_MAX_GEAR_UPGRADE == 1

    def test_max_route_length(self) -> None:
        assert GameConstants.MAX_ROUTE_LENGTH == 50


# ---------------------------------------------------------------------------
# Activity / Temperature
# ---------------------------------------------------------------------------


class TestActivityTemperature:
    def test_guild_activity_decay_rate(self) -> None:
        assert pytest.approx(GameConstants.GUILD_ACTIVITY_DECAY_RATE) == 2 / 3

    def test_min_guild_activity(self) -> None:
        assert pytest.approx(GameConstants.MIN_GUILD_ACTIVITY) == 1.0

    def test_activity_temp_per_player(self) -> None:
        assert GameConstants.ACTIVITY_TEMP_PER_PLAYER == 1


# ---------------------------------------------------------------------------
# Bounty Spawn Delay
# ---------------------------------------------------------------------------


class TestBountySpawnDelay:
    def test_bounty_delay_random_min(self) -> None:
        assert GameConstants.BOUNTY_DELAY_RANDOM_MIN == 5

    def test_bounty_delay_random_max(self) -> None:
        assert GameConstants.BOUNTY_DELAY_RANDOM_MAX == 7

    def test_min_less_than_max(self) -> None:
        assert GameConstants.BOUNTY_DELAY_RANDOM_MIN < GameConstants.BOUNTY_DELAY_RANDOM_MAX

    def test_bounty_spawn_jitter_default(self) -> None:
        """BOUNTY_SPAWN_JITTER default is 180 seconds (up to 3 min random offset)."""
        assert GameConstants.BOUNTY_SPAWN_JITTER == 180

    def test_bounty_spawn_jitter_is_positive(self) -> None:
        """BOUNTY_SPAWN_JITTER must be a positive integer."""
        assert isinstance(GameConstants.BOUNTY_SPAWN_JITTER, int)
        assert GameConstants.BOUNTY_SPAWN_JITTER > 0


# ---------------------------------------------------------------------------
# Timers
# ---------------------------------------------------------------------------


class TestTimers:
    def test_guild_activity_decay_interval(self) -> None:
        assert GameConstants.GUILD_ACTIVITY_DECAY_INTERVAL == 3600

    def test_shop_refresh_interval(self) -> None:
        assert GameConstants.SHOP_REFRESH_INTERVAL == 21600

    def test_check_cooldown(self) -> None:
        assert GameConstants.CHECK_COOLDOWN == 180

    def test_duel_request_expiry(self) -> None:
        assert GameConstants.DUEL_REQUEST_EXPIRY == 86400


# ---------------------------------------------------------------------------
# Shop Stock Generation
# ---------------------------------------------------------------------------


class TestShopStockGeneration:
    def test_shop_default_ships_num(self) -> None:
        assert GameConstants.SHOP_DEFAULT_SHIPS_NUM == 5

    def test_shop_default_weapons_num(self) -> None:
        assert GameConstants.SHOP_DEFAULT_WEAPONS_NUM == 5

    def test_shop_default_modules_num(self) -> None:
        assert GameConstants.SHOP_DEFAULT_MODULES_NUM == 5

    def test_shop_default_turrets_num(self) -> None:
        assert GameConstants.SHOP_DEFAULT_TURRETS_NUM == 2

    def test_shop_default_tools_num(self) -> None:
        assert GameConstants.SHOP_DEFAULT_TOOLS_NUM == 0

    def test_turret_spawn_probability(self) -> None:
        assert GameConstants.TURRET_SPAWN_PROBABILITY == 45


# ---------------------------------------------------------------------------
# Shop Rank Counts
# ---------------------------------------------------------------------------


class TestShopRankCounts:
    def test_num_ship_ranks(self) -> None:
        assert GameConstants.NUM_SHIP_RANKS == 10

    def test_num_weapon_ranks(self) -> None:
        assert GameConstants.NUM_WEAPON_RANKS == 10

    def test_num_module_ranks(self) -> None:
        assert GameConstants.NUM_MODULE_RANKS == 7

    def test_num_turret_ranks(self) -> None:
        assert GameConstants.NUM_TURRET_RANKS == 3


# ---------------------------------------------------------------------------
# Duels
# ---------------------------------------------------------------------------


class TestDuels:
    def test_duel_variance_percent(self) -> None:
        assert pytest.approx(GameConstants.DUEL_VARIANCE_PERCENT) == 0.05

    def test_duel_log_max_length(self) -> None:
        assert GameConstants.DUEL_LOG_MAX_LENGTH == 10

    def test_duel_cloak_chance(self) -> None:
        assert GameConstants.DUEL_CLOAK_CHANCE == 20


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


class TestInventory:
    def test_max_ship_nickname_length(self) -> None:
        assert GameConstants.MAX_SHIP_NICKNAME_LENGTH == 30

    def test_kaamo_max_capacity(self) -> None:
        assert GameConstants.KAAMO_MAX_CAPACITY == 70


# ---------------------------------------------------------------------------
# Classic Mode
# ---------------------------------------------------------------------------


class TestClassicMode:
    def test_classic_credits_per_check(self) -> None:
        assert GameConstants.CLASSIC_CREDITS_PER_CHECK == 1000

    def test_classic_division_name(self) -> None:
        assert GameConstants.CLASSIC_DIVISION_NAME == "bronze"


# ---------------------------------------------------------------------------
# Module Equip Limits
# ---------------------------------------------------------------------------


class TestModuleEquipLimits:
    def test_module_equip_limits_is_dict(self) -> None:
        assert isinstance(GameConstants.MODULE_EQUIP_LIMITS, dict)

    def test_armour_module_limit(self) -> None:
        assert GameConstants.MODULE_EQUIP_LIMITS["ArmourModule"] == 1

    def test_cabin_module_unlimited(self) -> None:
        """CabinModule should be unlimited (-1)."""
        assert GameConstants.MODULE_EQUIP_LIMITS["CabinModule"] == -1

    def test_compressor_module_unlimited(self) -> None:
        assert GameConstants.MODULE_EQUIP_LIMITS["CompressorModule"] == -1

    def test_jump_drive_module_not_equippable(self) -> None:
        """JumpDriveModule should be 0 (not equippable)."""
        assert GameConstants.MODULE_EQUIP_LIMITS["JumpDriveModule"] == 0

    def test_cloak_module_limit(self) -> None:
        assert GameConstants.MODULE_EQUIP_LIMITS["CloakModule"] == 1

    def test_shield_module_limit(self) -> None:
        assert GameConstants.MODULE_EQUIP_LIMITS["ShieldModule"] == 1

    def test_all_21_modules_present(self) -> None:
        expected_modules = {
            "ArmourModule",
            "BoosterModule",
            "CabinModule",
            "CloakModule",
            "CompressorModule",
            "EmergencySystemModule",
            "GammaShieldModule",
            "JumpDriveModule",
            "MiningDrillModule",
            "PrimaryWeaponModModule",
            "RepairBeamModule",
            "RepairBotModule",
            "ScannerModule",
            "ShieldInjectorModule",
            "ShieldModule",
            "SignatureModule",
            "SpectralFilterModule",
            "ThrusterModule",
            "TimeExtenderModule",
            "TractorBeamModule",
            "TransfusionBeamModule",
        }
        assert set(GameConstants.MODULE_EQUIP_LIMITS.keys()) == expected_modules


# ---------------------------------------------------------------------------
# Environment Variable Overrides
# ---------------------------------------------------------------------------


class TestEnvVarOverride:
    def teardown_method(self) -> None:
        """Reset class attributes after every test to avoid cross-test pollution."""
        _reset_constants()

    def test_max_bounties_per_division_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BOUNTYBOT_MAX_BOUNTIES_PER_DIVISION", "10")
        GameConstants.load()
        assert GameConstants.MAX_BOUNTIES_PER_DIVISION == 10

    def test_check_cooldown_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BOUNTYBOT_CHECK_COOLDOWN", "60")
        GameConstants.load()
        assert GameConstants.CHECK_COOLDOWN == 60

    def test_shop_refresh_interval_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BOUNTYBOT_SHOP_REFRESH_INTERVAL", "7200")
        GameConstants.load()
        assert GameConstants.SHOP_REFRESH_INTERVAL == 7200

    def test_duel_request_expiry_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BOUNTYBOT_DUEL_REQUEST_EXPIRY", "3600")
        GameConstants.load()
        assert GameConstants.DUEL_REQUEST_EXPIRY == 3600

    def test_duel_variance_percent_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BOUNTYBOT_DUEL_VARIANCE_PERCENT", "0.10")
        GameConstants.load()
        assert pytest.approx(GameConstants.DUEL_VARIANCE_PERCENT) == 0.10

    def test_ship_value_reward_percentage_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BOUNTYBOT_SHIP_VALUE_REWARD_PERCENTAGE", "0.05")
        GameConstants.load()
        assert pytest.approx(GameConstants.SHIP_VALUE_REWARD_PERCENTAGE) == 0.05

    def test_bounty_spawn_jitter_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BOUNTYBOT_BOUNTY_SPAWN_JITTER", "60")
        GameConstants.load()
        assert GameConstants.BOUNTY_SPAWN_JITTER == 60

    def test_env_var_reverts_after_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """After removing the env var and calling load() again the default is restored."""
        monkeypatch.setenv("BOUNTYBOT_MAX_BOUNTIES_PER_DIVISION", "99")
        GameConstants.load()
        assert GameConstants.MAX_BOUNTIES_PER_DIVISION == 99

        monkeypatch.delenv("BOUNTYBOT_MAX_BOUNTIES_PER_DIVISION")
        GameConstants.load()
        assert GameConstants.MAX_BOUNTIES_PER_DIVISION == 5

    def test_load_without_env_vars_keeps_defaults(self) -> None:
        """Calling load() with no env vars set must not change any default value."""
        # Ensure the test-relevant env vars are absent (they normally are)
        for key in [
            "BOUNTYBOT_MAX_BOUNTIES_PER_DIVISION",
            "BOUNTYBOT_CHECK_COOLDOWN",
            "BOUNTYBOT_DUEL_VARIANCE_PERCENT",
        ]:
            os.environ.pop(key, None)

        GameConstants.load()

        assert GameConstants.MAX_BOUNTIES_PER_DIVISION == 5
        assert GameConstants.CHECK_COOLDOWN == 180
        assert pytest.approx(GameConstants.DUEL_VARIANCE_PERCENT) == 0.05


# ---------------------------------------------------------------------------
# Type Conversion
# ---------------------------------------------------------------------------


class TestTypeConversion:
    def teardown_method(self) -> None:
        _reset_constants()

    def test_int_conversion_from_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Env vars are always strings; _env_int must convert correctly."""
        result = GameConstants._env_int("SOME_INT_KEY_THAT_DOES_NOT_EXIST", 42)
        assert result == 42
        assert isinstance(result, int)

    def test_float_conversion_from_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Env vars are always strings; _env_float must convert correctly."""
        result = GameConstants._env_float("SOME_FLOAT_KEY_THAT_DOES_NOT_EXIST", 3.14)
        assert pytest.approx(result) == 3.14
        assert isinstance(result, float)

    def test_env_int_reads_env_var_as_int(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BOUNTYBOT_KAAMO_MAX_CAPACITY", "100")
        GameConstants.load()
        assert GameConstants.KAAMO_MAX_CAPACITY == 100
        assert isinstance(GameConstants.KAAMO_MAX_CAPACITY, int)
        _reset_constants()

    def test_env_float_reads_env_var_as_float(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BOUNTYBOT_DUEL_VARIANCE_PERCENT", "0.15")
        GameConstants.load()
        assert pytest.approx(GameConstants.DUEL_VARIANCE_PERCENT) == 0.15
        assert isinstance(GameConstants.DUEL_VARIANCE_PERCENT, float)
        _reset_constants()
