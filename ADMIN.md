# ADMIN.md — BountyBot-Reborn-SamX Admin Guide

This document covers every per-guild tunable, how to view and update each setting, and how to reset to safe defaults when something goes wrong.

All slash commands require the invoking user to be one of:
- Listed in the `ADMIN_USER_IDS` environment variable (comma-separated Discord user IDs)
- Holding the Discord **Administrator** permission
- Holding the configured **Bot Admin role** for the guild

---

## Table of Contents

- [First-Time Setup](#first-time-setup)
- [Guild Setup and Teardown](#guild-setup-and-teardown)
- [Core Config](#core-config)
  - [Admin Role](#admin-role)
  - [Starting Credits](#starting-credits)
  - [Sale Price Factor](#sale-price-factor)
  - [Viewing and Validating Config](#viewing-and-validating-config)
  - [Full Config Reset](#full-config-reset)
- [Progression Tunables](#progression-tunables)
  - [XP Thresholds](#xp-thresholds)
- [Bounty System Tunables](#bounty-system-tunables)
  - [Max Active Bounties](#max-active-bounties)
  - [Bounty Expiry](#bounty-expiry)
  - [Spawn Interval](#spawn-interval)
- [Activity Temperature System](#activity-temperature-system)
- [Shop Tunables](#shop-tunables)
  - [Item Type Count Ranges](#item-type-count-ranges)
  - [Item Quantity Ranges](#item-quantity-ranges)
  - [Tech Level Probabilities](#tech-level-probabilities)
- [Per-Guild Game Constant Overrides](#per-guild-game-constant-overrides)
  - [Viewing Overrides](#viewing-overrides)
  - [Setting an Override](#setting-an-override)
  - [Resetting Overrides](#resetting-overrides)
  - [Full Override Reference Table](#full-override-reference-table)
- [Player Management](#player-management)
  - [Viewing Player Stats](#viewing-player-stats)
  - [Adjusting Credits](#adjusting-credits)
  - [Adjusting XP](#adjusting-xp)
  - [Resetting a Player](#resetting-a-player)
  - [Clearing Cooldowns](#clearing-cooldowns)
  - [Giving and Removing Items](#giving-and-removing-items)
  - [Giving and Removing Ships](#giving-and-removing-ships)
- [Bounty Management](#bounty-management)
  - [Clearing Active Bounties](#clearing-active-bounties)
  - [Force-Spawning Bounties](#force-spawning-bounties)
- [Shop Management](#shop-management)
- [Render Service Management](#render-service-management)
- [Guild Diagnostics](#guild-diagnostics)
- [Permission Checking](#permission-checking)
- [Emergency Procedures](#emergency-procedures)

---

## First-Time Setup

After adding the bot to a server, an admin must run:

```
/admin_setup admin_role:@YourAdminRole [starting_credits:500]
```

This creates all required Discord infrastructure (channels, roles, category) and initialises the guild's database records. The command is **idempotent** — it is safe to re-run; it will adopt existing channels/roles rather than creating duplicates.

**What gets created:**

| Type | Name |
|------|------|
| Category | BountyBot |
| Channels | `#bronze-bounty-board`, `#silver-bounty-board`, `#gold-bounty-board`, `#platinum-bounty-board`, `#shop`, `#bounty-hunting`, `#bounty-discussions`, `#bot-images` (private) |
| Roles | `Bounty Hunter`, `Bounty Hunter Bronze`, `Bounty Hunter Silver`, `Bounty Hunter Gold`, `Bounty Hunter Platinum`, `Shop Announcements` |

After setup, run `/admin_config_validate` to confirm the config is clean.

---

## Guild Setup and Teardown

### Re-run Setup

```
/admin_setup admin_role:@Role [starting_credits:N]
```

Safe to run at any time. Existing channels and roles are found and reused; missing ones are created.

### Full Uninstall

> **Warning:** Irreversible. All player records, shops, bounties, and config are permanently deleted.

```
/admin_uninstall
```

A confirmation dialog is shown before any data is deleted. The bot also removes all created channels, the category, and all created roles from Discord.

---

## Core Config

### Admin Role

The role that grants bot-admin privileges within the guild.

| | |
|---|---|
| **View** | `/admin_config action:View Config` |
| **Set** | `/admin_config action:Set Admin Role admin_role:@Role` |
| **Reset** | `/admin_config action:Reset to Defaults` |

### Starting Credits

Credits given to a player when their account is first created.

| | |
|---|---|
| **View** | `/admin_config action:View Config` |
| **Set** | `/admin_config action:Set Starting Credits starting_credits:500` |
| **Reset** | `/admin_config action:Reset to Defaults` (resets to `0`) |

**Constraint:** Must be `>= 0`.  
**Note:** Only affects newly created players. Existing players are unaffected.

### Sale Price Factor

The fraction of an item's base value a player receives when selling to the shop.

| | |
|---|---|
| **View** | `/admin_config action:View Config` |
| **Set** | `/admin_config_shop sale_factor:0.75` |
| **Reset** | `/admin_config action:Reset to Defaults` (resets to `0.8`) |

**Constraint:** Must be `> 0` and `<= 1`. Default `0.8` = players sell at 80% of base price.

### Viewing and Validating Config

```
/admin_config action:View Config
```

Shows: admin role, starting credits, sale price factor, XP thresholds, timestamps.

```
/admin_config_validate
```

Runs server-side validation and returns:
- **Errors** (must fix before the bot operates correctly): mismatched XP thresholds, invalid TL probability sum, sale factor out of range, item count ranges where min > max.
- **Warnings** (non-blocking): starting credits of 0.

**Run this after any config change.**

### Full Config Reset

Resets all settings to hardcoded defaults. Channel and role IDs are set to `NULL` (Discord infrastructure is not deleted).

```
/admin_config action:Reset to Defaults
```

After a full reset you must re-run `/admin_setup` to re-link the existing Discord infrastructure.

---

## Progression Tunables

### XP Thresholds

Controls how much XP players need to advance to each tier.

| | |
|---|---|
| **View** | `/admin_config_xp action:View` |
| **Set** | `/admin_config_xp action:Update silver:1000 gold:5000 platinum:15000 [prestige:50000]` |
| **Reset** | `/admin_config action:Reset to Defaults` |

**Defaults:** Silver `1000` · Gold `5000` · Platinum `15000` · Prestige `50000`

**Constraints:**
- All three of Silver, Gold, Platinum are required when updating
- Must be strictly ascending: Silver < Gold < Platinum
- If `prestige` is provided: Prestige > Platinum
- All values must be `> 0`

**Effect:** When a player's XP crosses a threshold (via bounty wins or `/admin_player action:Set XP`), their tier advances automatically and their Discord role is updated.

---

## Bounty System Tunables

### Max Active Bounties

The maximum number of simultaneously active bounties per tier. The actual cap is `min(max_per_tier, temperature_cap)` — see [Activity Temperature System](#activity-temperature-system).

| | |
|---|---|
| **View** | `/admin_config_bounty action:View` |
| **Set** | `/admin_config_bounty action:Update max_bronze:3 max_silver:3 max_gold:3 max_platinum:3` |
| **Reset** | `/admin_config action:Reset to Defaults` (resets all to `3`) |

**Constraint:** Each tier value must be `0–20`. Setting a tier to `0` disables bounty spawning for that tier.

### Bounty Expiry

How long a bounty remains active before it auto-expires if not resolved.

| | |
|---|---|
| **View** | `/admin_config_bounty action:View` |
| **Set** | `/admin_config_bounty action:Update expiry_minutes:480` |
| **Reset** | `/admin_config action:Reset to Defaults` (resets to `480` min = 8 hours) |

**Constraint:** `10–10080` minutes (10 minutes to 1 week).

### Spawn Interval

How often the spawn-check job runs to potentially add a new bounty.

| | |
|---|---|
| **View** | `/admin_config_bounty action:View` (shows `next_spawn_check_at`) |
| **Set** | `/admin_config_bounty action:Update spawn_interval:60` |
| **Reset** | `/admin_config action:Reset to Defaults` (resets to `60` minutes) |

**Constraint:** `5–1440` minutes. The job only spawns a bounty if the active count is below the temperature-adjusted cap.

---

## Activity Temperature System

Each division has an **activity temperature** that dynamically scales how many bounties can be active at once, and how quickly new ones respawn.

**How temperature works:**
- Rises by `activity_temp_per_player` (default: `1`) each time any player in the division checks a system
- Decays every hour by multiplying by `guild_activity_decay_rate` (default: `0.667` ≈ halving every 1.5 hours)
- Never drops below `min_guild_activity` (default: `1.0`)

**Effect on active bounty cap:**
```
cap = min(max_per_tier, max(1, floor(temperature)))
```

| Temperature | Effective Cap (with max_per_tier=3) |
|------------|--------------------------------------|
| 1.0 (idle) | 1 |
| 2.0 | 2 |
| 3.0+ (active) | 3 (full cap) |

**Per-guild temperature overrides** (set via `/admin_config_constants`):

| Setting | Default | What It Controls |
|---------|---------|-----------------|
| `guild_activity_decay_rate` | `0.667` | Hourly decay multiplier (`0` = instant decay, `1.0` = no decay) |
| `min_guild_activity` | `1.0` | Temperature floor (minimum concurrent bounty count) |
| `activity_temp_per_player` | `1` | Temperature increase per player system check |

**Current temperatures** are shown in `/admin_config_bounty action:View`.

---

## Shop Tunables

### Item Type Count Ranges

Controls how many distinct item types appear in the shop on each refresh.

| | |
|---|---|
| **View** | `/admin_config_shop` (with no parameters) |
| **Set** | `/admin_config_shop ship_count_min:2 ship_count_max:5 weapon_count_min:3 weapon_count_max:6` |
| **Reset** | `/admin_config action:Reset to Defaults` (all reset to min `3`, max `5`) |

**Available parameters:** `ship_count_min/max`, `weapon_count_min/max`, `module_count_min/max`, `turret_count_min/max`

**Constraint:** `min >= 1`, `min <= max`. Min and max must be provided together per item type.

### Item Quantity Ranges

Controls how many copies of each item are stocked per listing.

Set via direct API call or `/admin_config_constants`:

**Defaults:**
| Item Type | Min Qty | Max Qty |
|-----------|---------|---------|
| Ships | 1 | 1 |
| Weapons | 2 | 4 |
| Modules | 2 | 4 |
| Turrets | 2 | 4 |

**Constraint:** `min >= 1`, `min <= max`.

### Tech Level Probabilities

When generating shop stock, the probability distribution for item tech level relative to the tier's target TL.

| | |
|---|---|
| **View** | `/admin_config action:View Config` |
| **Set** | Via API: `PUT /api/v1/config/guild/{guild_id}/shop` with `{"tech_level_probabilities": {"same_level": 0.70, "one_lower": 0.20, "two_lower": 0.10}}` |
| **Reset** | `/admin_config action:Reset to Defaults` |

**Defaults:** `same_level: 0.70`, `one_lower: 0.20`, `two_lower: 0.10`

**Constraint:** All three keys required; values must be `0.0–1.0`; must sum to `1.0` (±0.01 tolerance).

---

## Per-Guild Game Constant Overrides

Any of the 27 global `GameConstants` values can be overridden per-guild. When set to `NULL` (the default), the global constant applies. When set to a value, only this guild uses that value.

### Viewing Overrides

```
# List all 27 constants with current values (NULL shown as *default*)
/admin_config_constants

# See only constants that have been explicitly set for this guild
/admin_config_constants_view
```

### Setting an Override

```
/admin_config_constants setting:check_cooldown int_value:120
/admin_config_constants setting:duel_variance_percent float_value:0.10
/admin_config_constants setting:division_max_tl json_value:{"bronze":3,"silver":5,"gold":8,"platinum":10}
```

Use `int_value` for integer fields, `float_value` for float fields, `json_value` for dict fields.

### Resetting Overrides

```
# Reset a single override to the global default (NULL)
/admin_config_constants_reset setting:check_cooldown

# Reset ALL overrides for this guild (shows confirmation dialog first)
/admin_config_constants_reset
```

### Full Override Reference Table

| Setting Name | Type | Default | Units | What It Controls |
|---|---|---|---|---|
| `close_bounty_threshold` | int | `4` | systems | How many systems ahead a criminal must be to show a "close" proximity hint |
| `max_route_length` | int | `50` | systems | Maximum length of a bounty criminal's A* route |
| `check_cooldown` | int | `180` | seconds | Cooldown between a player's `/check` commands |
| `duel_request_expiry` | int | `86400` | seconds | Time before a pending duel challenge auto-expires (default: 24 hours) |
| `tier_change_cooldown` | int | `86400` | seconds | Cooldown between tier advances/demotions (default: 24 hours) |
| `bounty_delay_random_min` | int | `5` | minutes | Min random delay for bounty respawn after expiry |
| `bounty_delay_random_max` | int | `7` | minutes | Max random delay for bounty respawn after expiry |
| `bounty_spawn_jitter` | int | `180` | seconds | Random timing jitter added to each spawn check |
| `guild_activity_decay_rate` | float | `0.667` | multiplier/hr | Hourly temperature decay multiplier (`0`=instant, `1`=no decay) |
| `min_guild_activity` | float | `1.0` | temperature | Temperature floor per division |
| `activity_temp_per_player` | int | `1` | temp units | Temperature rise per player system check |
| `ship_value_reward_percentage` | float | `0.01` | fraction | Fraction of criminal ship value used as bounty reward (0.01 = 1%) |
| `criminal_equip_damageless_weapon_chance` | int | `20` | percent | % chance a criminal equips a cosmetic/zero-DPS weapon |
| `criminal_max_gear_upgrade` | int | `1` | TL levels | Max TL above criminal's base TL their gear can be |
| `bounty_reward_to_xp_gain_mult` | float | `0.1` | multiplier | XP = reward_credits × this multiplier |
| `bounty_winner_reserve_factor` | float | `0.25` | fraction | Fraction of reward guaranteed to winner (rest split as consolation) |
| `bounty_pvc_armour_buff_factor` | float | `1.5` | multiplier | Player armour multiplier in player-vs-criminal combat (1.5 = +50%) |
| `duel_variance_percent` | float | `0.05` | fraction | Random variance on duel TTK calculations (0.05 = ±5%) |
| `duel_cloak_chance` | int | `20` | percent | % chance a Cloak Module activates during a duel turn |
| `division_max_tl` | dict | `{"bronze":2,"silver":4,"gold":7,"platinum":10}` | TL | Max criminal tech level per division |
| `shop_default_ships_num` | int | `5` | count | Default ship listing count per shop refresh |
| `shop_default_weapons_num` | int | `5` | count | Default weapon listing count per shop refresh |
| `shop_default_modules_num` | int | `5` | count | Default module listing count per shop refresh |
| `shop_default_turrets_num` | int | `2` | count | Default turret listing count per shop refresh |
| `turret_spawn_probability` | int | `45` | percent | % chance a shop slot generates a turret-type item |
| `kaamo_max_capacity` | int | `70` | items | Max items a player can store in Kaamo station |
| `demotion_credit_penalty_pct` | int | `10` | percent | % of credits deducted on tier demotion |
| `classic_credits_per_check` | int | `1000` | credits | Credits per system check for classic-mode players |

---

## Player Management

### Viewing Player Stats

```
/admin_player user:@Player action:View Stats
```

Shows: tier, XP, credits, lifetime credits, prestige count, bounty/duel win/loss counts, registration date.

### Adjusting Credits

```
# Set to an exact amount (does NOT update lifetime credits)
/admin_player user:@Player action:Set Credits credits:5000

# Add to current balance (DOES update lifetime credits)
/admin_player user:@Player action:Add Credits credits:1000
```

**Note:** Negative values are accepted for `Add Credits` (subtracts), but will not reduce credits below 0.

### Adjusting XP

```
/admin_player user:@Player action:Set XP xp:3500
```

Setting XP above a tier threshold automatically advances the player's tier and updates their Discord role.

### Resetting a Player

Resets credits to `starting_credits`, XP to 0, tier to Bronze, all win/loss counters to 0, prestige count to 0. Does **not** delete ships or inventory.

```
/admin_player user:@Player action:Reset Player
```

### Clearing Cooldowns

```
# Clear bounty check cooldown (allow immediate /check)
/admin_player user:@Player action:Reset Bounty Cooldown

# Clear tier-change cooldown
/admin_player user:@Player action:Reset Tier-Change Cooldown
```

### Giving and Removing Items

```
# Give an item (autocompletes from game catalog)
/admin_give_item user:@Player item_name:Plasma Cannon [quantity:2]

# Remove an item (autocompletes from player's inventory)
/admin_remove_item user:@Player item_name:Plasma Cannon [quantity:1]
```

Ships cannot be given via this command — use `/admin_give_ship`.

### Giving and Removing Ships

```
# Give a ship (starts inactive, empty loadout)
/admin_give_ship user:@Player ship_name:Interceptor MK2

# Remove a ship (evacuates all equipped items to inventory first)
/admin_remove_ship user:@Player ship_name:Interceptor MK2
```

**Note:** You cannot remove a player's only ship.

---

## Bounty Management

### Clearing Active Bounties

Shows a confirmation dialog. Deletes active bounties and associated Discord announcements and scheduler jobs.

```
# Clear all tiers
/admin_clear_bounties

# Clear a specific tier only
/admin_clear_bounties tier:Bronze
```

### Force-Spawning Bounties

Bypasses temperature and count checks — spawns bounties immediately regardless of activity level.

```
# Spawn 1 bounty on each tier
/admin_spawn_bounty

# Spawn on a specific tier with quantity
/admin_spawn_bounty tier:Silver quantity:3
```

**Constraint:** quantity `1–10`.

---

## Shop Management

### Force-Refreshing the Shop

```
# Refresh all 4 tiers
/admin_refresh_shop

# Refresh one tier only
/admin_refresh_shop tier:Gold

# Force all items to a specific tech level
/admin_refresh_shop tier:Bronze force_tech_level:3
```

After refresh, the shop announcement is posted to `#shop` and the autocomplete cache is updated.

---

## Render Service Management

These commands target the **blender-service** (not bot-core).

```
# View current render config (resolution limits, sample limits, concurrency)
/render_config action:view

# Update a render setting (DEVELOPERS env var required)
/render_config action:set setting:max_concurrent_renders value:2

# Reset all render settings to env-var defaults (DEVELOPERS env var required)
/render_config action:reset

# Clear the /tmp render cache
/render_cache_clear
```

**Note:** `set` and `reset` actions require the invoking user to be in the `DEVELOPERS` environment variable — regular bot-admin role is not sufficient.

---

## Guild Diagnostics

```
# View player count, tier distribution, average/total credits and XP
/admin_guild_stats

# View and validate full config
/admin_config action:View Config
/admin_config_validate

# View bounty config and active counts per tier
/admin_config_bounty action:View

# View all per-guild game constant overrides
/admin_config_constants_view
```

---

## Permission Checking

```
# Check if a user has bot-admin rights and why
/admin_check user:@User
```

Returns which rule grants them access: developer override, Discord Administrator permission, or configured admin role.

---

## Emergency Procedures

### Bad Config — Validation Errors

1. Run `/admin_config_validate` to identify the specific error
2. Fix the flagged field using the appropriate command above
3. Re-run `/admin_config_validate` to confirm clean

### Bounties Stuck / Not Spawning

1. Run `/admin_config_bounty action:View` — check `next_spawn_check_at` and active counts
2. If temperature is low, `/admin_spawn_bounty` to force-seed activity
3. If counts are wrong, `/admin_clear_bounties` then `/admin_spawn_bounty`

### Temperature Too High (Too Many Bounties)

1. Run `/admin_config_bounty action:View` to see current temperatures
2. Temporarily lower `max_per_tier` via `/admin_config_bounty action:Update max_bronze:1 ...`
3. Or accelerate decay: `/admin_config_constants setting:guild_activity_decay_rate float_value:0.3`
4. Restore normal values once temperature naturally decays

### Temperature Too Low (Too Few Bounties)

Raise the floor:
```
/admin_config_constants setting:min_guild_activity float_value:3.0
```

Or slow decay:
```
/admin_config_constants setting:guild_activity_decay_rate float_value:0.9
```

### Accidental Config Reset (Channels/Roles Unlinked)

After a `/admin_config action:Reset to Defaults`, channel and role IDs are set to NULL but Discord objects are not deleted. Re-run `/admin_setup admin_role:@Role` to re-link them — it will find and adopt the existing infrastructure.

### Full Reset Without Wiping Players

The bot-core API supports a guild reset that preserves player records:

```bash
# Via bot-core API directly (not a slash command):
curl -X POST http://localhost:8000/api/v1/admin/guilds/{guild_id}/reset?preserve_players=true
```

This resets config to defaults and clears bounties/shops but leaves player accounts, XP, credits, and inventory intact.

---

*Last updated: 2026-05-24*
