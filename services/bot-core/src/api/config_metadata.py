"""
Admin-facing metadata for per-guild game-constant override fields (issue #70).

Deliverable 1 of the admin-config overhaul: human-readable descriptions for
every settable override field, seeded from GAME_CONSTANTS_CATALOG.md's
Admin-facing column.

Public symbols
--------------
FIELD_DESCRIPTIONS : dict[str, str]
    One admin-facing sentence per field.  Flat scalars derived from a parent
    JSONB row include the division/key qualifier.  Deprecated JSONB dict fields
    carry the standard deprecation notice.

FIELD_TO_CATALOG_ROW : dict[str, str]
    Maps each override field name to the UPPERCASE constant name in
    GAME_CONSTANTS_CATALOG.md that is the authoritative source for its
    description.  Dict-parent rows (PRIMARY_TL_BAND_WEIGHTS) cover their
    flat scalar children (primary_tl_band_weight_center etc.).

    Fields with no catalog row (starting_credits, sale_price_factor) are
    intentionally absent — they are config columns, not GameConstants rows.

DEPRECATED_FIELDS : frozenset[str]
    The 7 JSONB dict fields grandfathered from before the flatten refactor.
    Their descriptions say to use the per-division/per-key scalars instead.

NO_CATALOG_ROW_FIELDS : frozenset[str]
    Fields with no GAME_CONSTANTS_CATALOG.md row.  Flagged here for
    transparency; their descriptions are hand-authored below.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Deprecated JSONB dict fields
# ---------------------------------------------------------------------------

DEPRECATED_FIELDS: frozenset[str] = frozenset(
    {
        "division_max_tl",
        "bounty_division_reward_mult",
        "primary_tl_band_weights",
        "criminal_cloak_chance_by_division",
        "criminal_booster_chance_by_division",
        "criminal_emergency_chance_by_division",
        "criminal_weaponmod_chance_by_division",
    }
)

_DEPRECATED_DESCRIPTION = "Deprecated — set the per-division/per-key scalar settings instead; removed next release."

# ---------------------------------------------------------------------------
# Fields with no GAME_CONSTANTS_CATALOG.md row (config columns, not constants)
# ---------------------------------------------------------------------------

NO_CATALOG_ROW_FIELDS: frozenset[str] = frozenset(
    {
        "starting_credits",  # GuildConfig column, not a GameConstant
        "sale_price_factor",  # GuildConfig column, not a GameConstant
    }
)

# ---------------------------------------------------------------------------
# Field → catalog constant name
# ---------------------------------------------------------------------------
# Maps each override field name to the UPPERCASE constant in
# GAME_CONSTANTS_CATALOG.md that is the source of its Admin-facing description.
# Flat-scalar children of a JSONB parent map to the PARENT constant row.
# Fields in NO_CATALOG_ROW_FIELDS are omitted (no catalog row exists).
# ---------------------------------------------------------------------------

FIELD_TO_CATALOG_ROW: dict[str, str] = {
    # ------- deprecated JSONB dicts (map to themselves) -------
    "division_max_tl": "DIVISION_MAX_TL",
    "bounty_division_reward_mult": "BOUNTY_DIVISION_REWARD_MULT",
    "primary_tl_band_weights": "PRIMARY_TL_BAND_WEIGHTS",
    "criminal_cloak_chance_by_division": "CRIMINAL_CLOAK_CHANCE_BY_DIVISION",
    "criminal_booster_chance_by_division": "CRIMINAL_BOOSTER_CHANCE_BY_DIVISION",
    "criminal_emergency_chance_by_division": "CRIMINAL_EMERGENCY_CHANCE_BY_DIVISION",
    "criminal_weaponmod_chance_by_division": "CRIMINAL_WEAPONMOD_CHANCE_BY_DIVISION",
    # ------- division_max_tl flat scalars -------
    "division_max_tl_bronze": "DIVISION_MAX_TL",
    "division_max_tl_silver": "DIVISION_MAX_TL",
    "division_max_tl_gold": "DIVISION_MAX_TL",
    "division_max_tl_platinum": "DIVISION_MAX_TL",
    # ------- bounty_division_reward_mult flat scalars -------
    "bounty_division_reward_mult_bronze": "BOUNTY_DIVISION_REWARD_MULT",
    "bounty_division_reward_mult_silver": "BOUNTY_DIVISION_REWARD_MULT",
    "bounty_division_reward_mult_gold": "BOUNTY_DIVISION_REWARD_MULT",
    "bounty_division_reward_mult_platinum": "BOUNTY_DIVISION_REWARD_MULT",
    # ------- primary_tl_band_weights flat scalars -------
    "primary_tl_band_weight_center": "PRIMARY_TL_BAND_WEIGHTS",
    "primary_tl_band_weight_minus1": "PRIMARY_TL_BAND_WEIGHTS",
    "primary_tl_band_weight_plus1": "PRIMARY_TL_BAND_WEIGHTS",
    # ------- criminal chance flat scalars -------
    "criminal_cloak_chance_bronze": "CRIMINAL_CLOAK_CHANCE_BY_DIVISION",
    "criminal_cloak_chance_silver": "CRIMINAL_CLOAK_CHANCE_BY_DIVISION",
    "criminal_cloak_chance_gold": "CRIMINAL_CLOAK_CHANCE_BY_DIVISION",
    "criminal_cloak_chance_platinum": "CRIMINAL_CLOAK_CHANCE_BY_DIVISION",
    "criminal_booster_chance_bronze": "CRIMINAL_BOOSTER_CHANCE_BY_DIVISION",
    "criminal_booster_chance_silver": "CRIMINAL_BOOSTER_CHANCE_BY_DIVISION",
    "criminal_booster_chance_gold": "CRIMINAL_BOOSTER_CHANCE_BY_DIVISION",
    "criminal_booster_chance_platinum": "CRIMINAL_BOOSTER_CHANCE_BY_DIVISION",
    "criminal_emergency_chance_bronze": "CRIMINAL_EMERGENCY_CHANCE_BY_DIVISION",
    "criminal_emergency_chance_silver": "CRIMINAL_EMERGENCY_CHANCE_BY_DIVISION",
    "criminal_emergency_chance_gold": "CRIMINAL_EMERGENCY_CHANCE_BY_DIVISION",
    "criminal_emergency_chance_platinum": "CRIMINAL_EMERGENCY_CHANCE_BY_DIVISION",
    "criminal_weaponmod_chance_bronze": "CRIMINAL_WEAPONMOD_CHANCE_BY_DIVISION",
    "criminal_weaponmod_chance_silver": "CRIMINAL_WEAPONMOD_CHANCE_BY_DIVISION",
    "criminal_weaponmod_chance_gold": "CRIMINAL_WEAPONMOD_CHANCE_BY_DIVISION",
    "criminal_weaponmod_chance_platinum": "CRIMINAL_WEAPONMOD_CHANCE_BY_DIVISION",
    # ------- direct scalar mappings (field.upper() == catalog row) -------
    "criminal_max_gear_upgrade": "CRIMINAL_MAX_GEAR_UPGRADE",
    "bounty_reward_to_xp_gain_mult": "BOUNTY_REWARD_TO_XP_GAIN_MULT",
    "bounty_winner_reserve_factor": "BOUNTY_WINNER_RESERVE_FACTOR",
    "close_bounty_threshold": "CLOSE_BOUNTY_THRESHOLD",
    "max_route_length": "MAX_ROUTE_LENGTH",
    "min_route_systems": "MIN_ROUTE_SYSTEMS",
    "recently_spotted_max_window": "RECENTLY_SPOTTED_MAX_WINDOW",
    "check_cooldown": "CHECK_COOLDOWN",
    "duel_request_expiry": "DUEL_REQUEST_EXPIRY",
    "tier_change_cooldown": "TIER_CHANGE_COOLDOWN",
    "classic_credits_per_check": "CLASSIC_CREDITS_PER_CHECK",
    "demotion_credit_penalty_pct": "DEMOTION_CREDIT_PENALTY_PCT",
    "long_range_threshold_m": "LONG_RANGE_THRESHOLD_M",
    "criminal_long_range_pct": "CRIMINAL_LONG_RANGE_PCT",
    "criminal_exclude_emp_weapons": "CRIMINAL_EXCLUDE_EMP_WEAPONS",
    "criminal_secondary_min_damage": "CRIMINAL_SECONDARY_MIN_DAMAGE",
    "loot_chance_tractor_t1": "LOOT_CHANCE_TRACTOR_T1",
    "loot_chance_tractor_t2": "LOOT_CHANCE_TRACTOR_T2",
    "loot_chance_tractor_t3": "LOOT_CHANCE_TRACTOR_T3",
    "loot_chance_tractor_t4": "LOOT_CHANCE_TRACTOR_T4",
    "loot_chance_no_tractor": "LOOT_CHANCE_NO_TRACTOR",
    "loot_band1_select_pct": "LOOT_BAND1_SELECT_PCT",
    "loot_band2_select_pct": "LOOT_BAND2_SELECT_PCT",
    "loot_band3_select_pct": "LOOT_BAND3_SELECT_PCT",
    "loot_band1_tl_window": "LOOT_BAND1_TL_WINDOW",
    "loot_band1_qty_min": "LOOT_BAND1_QTY_MIN",
    "loot_band1_qty_max": "LOOT_BAND1_QTY_MAX",
    "loot_band1_qty_mode": "LOOT_BAND1_QTY_MODE",
    "loot_band2_qty_min": "LOOT_BAND2_QTY_MIN",
    "loot_band2_qty_max": "LOOT_BAND2_QTY_MAX",
    "loot_band2_qty_mode": "LOOT_BAND2_QTY_MODE",
    "loot_band3_qty_min": "LOOT_BAND3_QTY_MIN",
    "loot_band3_qty_max": "LOOT_BAND3_QTY_MAX",
    "loot_band3_qty_mode": "LOOT_BAND3_QTY_MODE",
    "loot_commodity_sell_fraction": "LOOT_COMMODITY_SELL_FRACTION",
    "shop_combat_module_prob": "SHOP_COMBAT_MODULE_PROB",
    "shop_secondary_qty_scaler_heavy": "SHOP_SECONDARY_QTY_SCALER_HEAVY",
    "shop_secondary_qty_scaler_standard": "SHOP_SECONDARY_QTY_SCALER_STANDARD",
    "shop_tl_band_lo_bronze": "SHOP_TL_BAND_LO_BRONZE",
    "shop_tl_band_hi_bronze": "SHOP_TL_BAND_HI_BRONZE",
    "shop_tl_band_lo_silver": "SHOP_TL_BAND_LO_SILVER",
    "shop_tl_band_hi_silver": "SHOP_TL_BAND_HI_SILVER",
    "shop_tl_band_lo_gold": "SHOP_TL_BAND_LO_GOLD",
    "shop_tl_band_hi_gold": "SHOP_TL_BAND_HI_GOLD",
    "shop_tl_band_lo_platinum": "SHOP_TL_BAND_LO_PLATINUM",
    "shop_tl_band_hi_platinum": "SHOP_TL_BAND_HI_PLATINUM",
    "shop_banded_tl_weight": "SHOP_BANDED_TL_WEIGHT",
    "shop_uptier_tl_decay": "SHOP_UPTIER_TL_DECAY",
    "shop_downtier_tl_decay": "SHOP_DOWNTIER_TL_DECAY",
    "bounty_single_waypoint_prob": "BOUNTY_SINGLE_WAYPOINT_PROB",
    "bounty_dual_waypoint_prob": "BOUNTY_DUAL_WAYPOINT_PROB",
    "bounty_waypoint_attempts": "BOUNTY_WAYPOINT_ATTEMPTS",
    "bounty_waypoint_min_degree": "BOUNTY_WAYPOINT_MIN_DEGREE",
    "pvc_damage_reduction": "PVC_DAMAGE_REDUCTION",
    "bronze_combat_bonus_base_mult": "BRONZE_COMBAT_BONUS_BASE_MULT",
    "bronze_combat_bonus_per_prestige": "BRONZE_COMBAT_BONUS_PER_PRESTIGE",
    "bronze_combat_bonus_cap": "BRONZE_COMBAT_BONUS_CAP",
    # ------- division_tl_center flat scalars -------
    # Catalog row is DIVISION_TL_CENTERS (the dict parent).
    "division_tl_center_bronze": "DIVISION_TL_CENTERS",
    "division_tl_center_silver": "DIVISION_TL_CENTERS",
    "division_tl_center_gold": "DIVISION_TL_CENTERS",
    "division_tl_center_platinum": "DIVISION_TL_CENTERS",
}

# ---------------------------------------------------------------------------
# Field descriptions (97 total: 95 _OVERRIDE_FIELDS + starting_credits +
# sale_price_factor).  Sentences end with a period.
# ---------------------------------------------------------------------------

FIELD_DESCRIPTIONS: dict[str, str] = {
    # ====== deprecated JSONB dicts ======
    "division_max_tl": _DEPRECATED_DESCRIPTION,
    "bounty_division_reward_mult": _DEPRECATED_DESCRIPTION,
    "primary_tl_band_weights": _DEPRECATED_DESCRIPTION,
    "criminal_cloak_chance_by_division": _DEPRECATED_DESCRIPTION,
    "criminal_booster_chance_by_division": _DEPRECATED_DESCRIPTION,
    "criminal_emergency_chance_by_division": _DEPRECATED_DESCRIPTION,
    "criminal_weaponmod_chance_by_division": _DEPRECATED_DESCRIPTION,
    # ====== division_max_tl flat scalars ======
    "division_max_tl_bronze": (
        "The highest equipment tech level a criminal can carry in the Bronze division"
        " — lower values make bounties easier to defeat."
    ),
    "division_max_tl_silver": (
        "The highest equipment tech level a criminal can carry in the Silver division"
        " — lower values make bounties easier to defeat."
    ),
    "division_max_tl_gold": (
        "The highest equipment tech level a criminal can carry in the Gold division"
        " — lower values make bounties easier to defeat."
    ),
    "division_max_tl_platinum": (
        "The highest equipment tech level a criminal can carry in the Platinum division"
        " — lower values make bounties easier to defeat."
    ),
    # ====== core scalars ======
    "criminal_max_gear_upgrade": (
        "How many tech levels above a criminal's base level its weapons and modules can reach during spawn."
    ),
    "bounty_reward_to_xp_gain_mult": (
        "How much XP players earn per credit gained from capturing a bounty (0.1 = 1 XP per 10 credits)."
    ),
    "bounty_winner_reserve_factor": (
        "The share of a bounty's prize pool guaranteed to the player who caught the"
        " criminal, with the rest split among other checkers."
    ),
    # ====== bounty_division_reward_mult flat scalars ======
    "bounty_division_reward_mult_bronze": (
        "A per-division multiplier on bounty prize pools for the Bronze division"
        " — silver defaults to 2× so rewards match difficulty."
    ),
    "bounty_division_reward_mult_silver": (
        "A per-division multiplier on bounty prize pools for the Silver division"
        " — silver defaults to 2× so rewards match difficulty."
    ),
    "bounty_division_reward_mult_gold": (
        "A per-division multiplier on bounty prize pools for the Gold division"
        " — silver defaults to 2× so rewards match difficulty."
    ),
    "bounty_division_reward_mult_platinum": (
        "A per-division multiplier on bounty prize pools for the Platinum division"
        " — silver defaults to 2× so rewards match difficulty."
    ),
    # ====== bounty routing ======
    "close_bounty_threshold": (
        'How many systems away a criminal must be before players see a "close" hint in their /check result.'
    ),
    "max_route_length": (
        "The longest route (in star-system hops) the bot will plot for a bounty"
        " — currently fixed at 50 regardless of your guild setting until a fix is deployed."
    ),
    "min_route_systems": (
        'The shortest route (in systems) a bounty will ever spawn with — lower values allow easier "next door" hunts.'
    ),
    "recently_spotted_max_window": (
        'Controls how far ahead of the criminal\'s location a "recently spotted" hint can'
        " appear in /check — 0 disables the hint entirely."
    ),
    # ====== timers ======
    "check_cooldown": ("How long players must wait between /check uses (3 minutes by default)."),
    "duel_request_expiry": (
        "How long a duel challenge stays open before it automatically expires (24 hours by default)."
    ),
    "tier_change_cooldown": (
        "How long a player must wait after moving tiers before they can promote or demote again (24 hours by default)."
    ),
    # ====== economy ======
    "classic_credits_per_check": (
        "Sets the minimum credit reward per system check that seeds every bounty prize"
        " pool — a higher floor raises all bounty payouts. Currently fixed at 1000"
        " regardless of guild setting until the wiring fix (Unit D1) lands."
    ),
    "demotion_credit_penalty_pct": (
        "The percentage of credits a player loses when they are demoted to a lower tier (10% by default)."
    ),
    # ====== criminal loadout — primary weapons ======
    "long_range_threshold_m": (
        'The weapon range (in metres) above which a primary weapon counts as "long-range" when arming a criminal.'
    ),
    "criminal_long_range_pct": (
        "The minimum fraction of a criminal's primary weapons that must be long-range (50% by default)."
    ),
    "primary_tl_band_weight_center": (
        "How likely criminals are to carry weapons exactly at their tech level (center"
        " weight) versus one level below or above — defaults favour exact-TL weapons"
        " 70% of the time."
    ),
    "primary_tl_band_weight_minus1": (
        "How likely criminals are to carry weapons one tech level below their target"
        " (minus-1 weight) — lower reduces below-tier spawns."
    ),
    "primary_tl_band_weight_plus1": (
        "How likely criminals are to carry weapons one tech level above their target"
        " (plus-1 weight) — lower reduces above-tier spawns."
    ),
    # ====== criminal loadout — modules ======
    "criminal_exclude_emp_weapons": (
        "Whether to exclude EMP-focused weapons from criminal loadouts"
        " (leave enabled until EMP combat mechanics are fully implemented)."
    ),
    # ====== criminal cloak chances (flat scalars) ======
    "criminal_cloak_chance_bronze": (
        "The percentage chance a criminal has a cloaking device in the Bronze division"
        " — 0 means never, 100 means always."
    ),
    "criminal_cloak_chance_silver": (
        "The percentage chance a criminal has a cloaking device in the Silver division"
        " — 0 means never, 100 means always."
    ),
    "criminal_cloak_chance_gold": (
        "The percentage chance a criminal has a cloaking device in the Gold division — 0 means never, 100 means always."
    ),
    "criminal_cloak_chance_platinum": (
        "The percentage chance a criminal has a cloaking device in the Platinum division"
        " — 0 means never, 100 means always."
    ),
    # ====== criminal booster chances (flat scalars) ======
    "criminal_booster_chance_bronze": ("The percentage chance a criminal has a weapon booster in the Bronze division."),
    "criminal_booster_chance_silver": ("The percentage chance a criminal has a weapon booster in the Silver division."),
    "criminal_booster_chance_gold": ("The percentage chance a criminal has a weapon booster in the Gold division."),
    "criminal_booster_chance_platinum": (
        "The percentage chance a criminal has a weapon booster in the Platinum division."
    ),
    # ====== criminal emergency chances (flat scalars) ======
    "criminal_emergency_chance_bronze": (
        "The percentage chance a criminal has an emergency survival system in the Bronze"
        " division — higher values make criminals harder to destroy."
    ),
    "criminal_emergency_chance_silver": (
        "The percentage chance a criminal has an emergency survival system in the Silver"
        " division — higher values make criminals harder to destroy."
    ),
    "criminal_emergency_chance_gold": (
        "The percentage chance a criminal has an emergency survival system in the Gold"
        " division — higher values make criminals harder to destroy."
    ),
    "criminal_emergency_chance_platinum": (
        "The percentage chance a criminal has an emergency survival system in the"
        " Platinum division — higher values make criminals harder to destroy."
    ),
    # ====== criminal weaponmod chances (flat scalars) ======
    "criminal_weaponmod_chance_bronze": (
        "The percentage chance a criminal has a weapon damage modifier in the Bronze division."
    ),
    "criminal_weaponmod_chance_silver": (
        "The percentage chance a criminal has a weapon damage modifier in the Silver division."
    ),
    "criminal_weaponmod_chance_gold": (
        "The percentage chance a criminal has a weapon damage modifier in the Gold division."
    ),
    "criminal_weaponmod_chance_platinum": (
        "The percentage chance a criminal has a weapon damage modifier in the Platinum division."
    ),
    # ====== criminal secondary weapons ======
    "criminal_secondary_min_damage": (
        "The minimum damage a secondary weapon must deal to appear in a criminal's"
        " loadout — raise to remove weak secondaries, lower to 0 to allow all weapons."
    ),
    # ====== loot knobs ======
    "loot_chance_tractor_t1": (
        "Chance of looting a kill when using the weakest tractor beam; default 20%"
        " means roughly 1 in 5 bounties yield loot."
    ),
    "loot_chance_tractor_t2": ("Chance of looting a kill with the T2 tractor beam; default 40%."),
    "loot_chance_tractor_t3": ("Chance of looting a kill with the T3 tractor beam; default 60%."),
    "loot_chance_tractor_t4": ("Chance of looting a kill with the best tractor beam; default 80%."),
    "loot_chance_no_tractor": (
        "Chance of looting a kill with no tractor beam equipped; default 0% means"
        " loot is impossible without the module."
    ),
    "loot_band1_select_pct": (
        "How often a loot drop produces a weapon or module (Band 1); default 10% makes"
        " gear drops rare relative to commodities."
    ),
    "loot_band2_select_pct": ("How often a loot drop produces ore cores or rare cargo (Band 2); default 20%."),
    "loot_band3_select_pct": (
        "How often a loot drop produces bulk cargo (Band 3); default 70% makes bulk drops the most common outcome."
    ),
    "loot_band1_tl_window": (
        "How many TL steps above and below the criminal's gear level can appear in a"
        " Band-1 (weapon/module) drop; default 1 means only same- and adjacent-tier loot."
    ),
    "loot_band1_qty_min": ("Minimum number of items in a Band-1 (weapon/module) loot drop."),
    "loot_band1_qty_max": ("Maximum number of items in a Band-1 (weapon/module) loot drop; default 3."),
    "loot_band1_qty_mode": (
        "Most common number of items in a Band-1 (weapon/module) loot drop; default 1"
        " (single-item drops are most likely)."
    ),
    "loot_band2_qty_min": ("Minimum number of items in a Band-2 (ore core / rare cargo) loot drop."),
    "loot_band2_qty_max": ("Maximum number of items in a Band-2 (ore core / rare cargo) loot drop; default 12."),
    "loot_band2_qty_mode": ("Most common number of items in a Band-2 (ore core / rare cargo) loot drop; default 8."),
    "loot_band3_qty_min": ("Minimum number of items in a Band-3 (bulk cargo) loot drop."),
    "loot_band3_qty_max": ("Maximum number of items in a Band-3 (bulk cargo) loot drop; default 22."),
    "loot_band3_qty_mode": ("Most common number of items in a Band-3 (bulk cargo) loot drop; default 16."),
    "loot_commodity_sell_fraction": (
        "The fraction of face value players receive when selling looted cargo; default"
        " 1.0 = 100%, can be set above 1.0 for a bonus sell rate (up to 10×)."
    ),
    # ====== shop ======
    "shop_combat_module_prob": (
        "How often the shop stocks a combat-ready module (0 = always filler, 1 = always"
        " combat); default 0.75 means roughly 3 in 4 module slots are combat-tier."
    ),
    "shop_secondary_qty_scaler_heavy": (
        "Controls how many rounds of heavy weapons (nukes, shock-blasts) appear per shop"
        " refresh — lower means fewer rounds stocked."
    ),
    "shop_secondary_qty_scaler_standard": (
        "Controls how many rounds of standard ammo (missiles, rockets) appear per shop"
        " refresh — higher means more rounds stocked."
    ),
    "shop_tl_band_lo_bronze": (
        'The lowest tech level that counts as "in tier" for Bronze shop draws; items'
        " below this appear only via the out-of-band taper."
    ),
    "shop_tl_band_hi_bronze": (
        'The highest tech level that counts as "in tier" for Bronze shop draws; items'
        " above this appear only via the out-of-band taper."
    ),
    "shop_tl_band_lo_silver": ('The lowest tech level that counts as "in tier" for Silver shop draws.'),
    "shop_tl_band_hi_silver": ('The highest tech level that counts as "in tier" for Silver shop draws.'),
    "shop_tl_band_lo_gold": ('The lowest tech level that counts as "in tier" for Gold shop draws.'),
    "shop_tl_band_hi_gold": ('The highest tech level that counts as "in tier" for Gold shop draws.'),
    "shop_tl_band_lo_platinum": ('The lowest tech level that counts as "in tier" for Platinum shop draws.'),
    "shop_tl_band_hi_platinum": ('The highest tech level that counts as "in tier" for Platinum shop draws.'),
    "shop_banded_tl_weight": (
        "How reliably the shop matches your division's gear tier (0 = always off-tier,"
        " 1 = always tier-matched); default 0.70 means 7 in 10 refreshes stock items"
        " squarely in your tier range."
    ),
    "shop_uptier_tl_decay": (
        "How quickly above-tier items become rare in the shop (lower = steeper"
        " drop-off); default 0.60 allows moderate up-tier bleeding so players"
        " occasionally see next-tier gear."
    ),
    "shop_downtier_tl_decay": (
        "How quickly below-tier items become rare in the shop (lower = steeper"
        " drop-off); default 0.45 is intentionally harsher than the uptier rate to"
        " keep junk off the shelves."
    ),
    # ====== division TL draw centres (flat scalars) ======
    "division_tl_center_bronze": (
        "The target equipment tech level for criminals in the Bronze division — lower means easier, more familiar gear."
    ),
    "division_tl_center_silver": ("The target equipment tech level for criminals in the Silver division."),
    "division_tl_center_gold": ("The target equipment tech level for criminals in the Gold division."),
    "division_tl_center_platinum": ("The target equipment tech level for criminals in the Platinum division."),
    # ====== waypoint routing ======
    "bounty_single_waypoint_prob": (
        "Chance that a bounty route passes through one intermediate waypoint system,"
        " making the hunt more varied (33% by default)."
    ),
    "bounty_dual_waypoint_prob": (
        "Chance that a bounty route passes through two intermediate waypoint systems,"
        " creating the longest and most complex hunts (10% by default)."
    ),
    "bounty_waypoint_attempts": (
        "How many times the bot retries finding a valid waypoint route before falling back to a standard direct route."
    ),
    "bounty_waypoint_min_degree": (
        "The minimum number of connections a waypoint system must have to be used"
        " — prevents criminals from being routed through dead-end systems."
    ),
    # ====== PvC damage reduction ======
    "pvc_damage_reduction": (
        "Controls how much of a criminal's damage a player absorbs in a bounty fight"
        " — lower means players take more damage; set to 0.0 to make PvC fights fully"
        " unmodified."
    ),
    # ====== Bronze combat bonus ======
    "bronze_combat_bonus_base_mult": (
        "The starting bonus percentage a Bronze player earns for winning the optional"
        " post-capture duel (40% of their bounty reward at 0 prestige stars)."
    ),
    "bronze_combat_bonus_per_prestige": (
        "How much the Bronze post-capture duel bonus grows per prestige star (+10% per star by default)."
    ),
    "bronze_combat_bonus_cap": (
        "The maximum bonus a Bronze player can earn from the post-capture duel, as a"
        " fraction of their bounty reward (100% by default, reached at 6 prestige stars)."
    ),
    # ====== Tier-1 core config scalars (not GameConstants) ======
    "starting_credits": ("The number of credits new players start with when they join this guild."),
    "sale_price_factor": (
        "The fraction of an item's face value that players receive when selling it at the Kaamo station."
    ),
}
