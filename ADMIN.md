# ADMIN.md — BountyBot-Reborn-SamX Admin Guide

This document covers every per-guild tunable, how to view and update each setting, and how to reset to safe defaults when something goes wrong.

All admin slash commands require the invoking user to be one of:
- Listed in the `DEVELOPERS` environment variable (comma-separated Discord user IDs — developer override)
- Holding the Discord **Administrator** permission
- Holding the configured **Bot Admin role** for the guild

**Super-admin commands** (scheduler management, data loading, cache reloads, render-config mutation) accept **only** users listed in `DEVELOPERS` — no Administrator or Bot Admin role fallback.

Separately, bot-core's `/api/v1/admin/*` REST endpoints are gated by the `ADMIN_USER_IDS` environment variable (the caller passes `user_id`; an empty `ADMIN_USER_IDS` allows all — dev mode only). This matters when calling the API directly, e.g. via `curl`.

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
  - [Criminal Loadout Balance (per-guild)](#criminal-loadout-balance-per-guild)
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
- [Duel Management](#duel-management)
- [Combat Log Review](#combat-log-review)
- [Shop Management](#shop-management)
- [Render Service Management](#render-service-management)
- [Scheduler Management](#scheduler-management)
- [Developer / Data Commands](#developer--data-commands)
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
| Channels | `#bronze-bounty-board`, `#silver-bounty-board`, `#gold-bounty-board`, `#platinum-bounties`, `#shop`, `#bounty-hunting`, `#bounty-discussions`, `#bot-images` (private) |
| Roles | `Bounty Hunter`, `Bounty Hunter Bronze`, `Bounty Hunter Silver`, `Bounty Hunter Gold`, `Bounty Hunter Platinum`, `Shop Announcements`, `Event Announcements` |

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

The fraction of an item's base value a player receives when selling weapons,
modules, and turrets to the shop. **Ships are exempt — they always sell at full
(1:1) value.**

| | |
|---|---|
| **View** | `/admin_config action:View Config` |
| **Set** | `/admin_config_shop sale_factor:0.75` |
| **Reset** | `/admin_config action:Reset to Defaults` (resets to `1.0`) |

**Constraint:** Must be `> 0` and `<= 1`. Default `1.0` = players sell at full base
price. Lower it (e.g. `0.8` = 80%) to create a sell-side credit sink. (This factor
was historically unwired — all sells were 1:1; it was wired in rev 0034 with a 1.0
default so existing guilds' behaviour is unchanged until an admin lowers it.)

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

The maximum number of simultaneously active bounties per tier. This is the sole active cap — the activity temperature system was retired in rev 0031 (see [Activity Temperature System](#activity-temperature-system)).

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

Per-tier randomised fire-time window used by the spawn orchestrator when deciding whether to spawn a bounty on a given check.

| | |
|---|---|
| **View** | `/admin_config_bounty action:View` (shows `next_spawn_check_at`) |
| **Set** | `/admin_config_bounty action:Update spawn_interval:60` |
| **Reset** | `/admin_config action:Reset to Defaults` (resets to `60` minutes) |

**Constraint:** `5–1440` minutes. The job only spawns a bounty if the active count is below `bounty_max_per_tier`.

> **Important:** This setting does NOT control how often the spawn-check job runs. The cron cadence is hardcoded at `*/BOUNTY_DELAY_RANDOM_MIN` minutes (default: **every 5 minutes**) and is set at process startup in `main.py`. `bounty_spawn_interval_minutes` only sizes the per-tier jitter window inside the orchestrator.

---

## Activity Temperature System

> **Status: RETIRED in rev 0031.** The temperature subsystem was removed in migration 0031. The `division_temperatures` column was dropped from `guild_configs`, `temperature_service.py` was deleted, and the `temperature_decay_default` scheduler job was removed from `DEFAULT_SCHEDULER_JOBS`. The executor (`temperature_decay_executor.py`) is now a no-op shim — stale APScheduler rows that reference `temperature_decay_default` log a deprecation warning and return immediately without error. The active bounty cap is governed solely by `bounty_max_per_tier`.
>
> **If a stale `temperature_decay_default` job row exists in your APScheduler table**, it will fire harmlessly as a no-op until cleared. To remove it: call `POST /api/v1/scheduler/reset` or delete the row directly via `DELETE /api/v1/scheduler/jobs/temperature_decay_default`.

---

## Shop Tunables

### Item Type Count Ranges

Controls how many distinct item types appear in the shop on each refresh.

| | |
|---|---|
| **View** | `/admin_config_shop` (with no parameters) |
| **Set** | `/admin_config_shop ship_count_min:2 ship_count_max:5 weapon_count_min:3 weapon_count_max:6` |
| **Reset** | `/admin_config action:Reset to Defaults` (all reset to min `3`, max `5`) |

**Available parameters:** `ship_count_min/max`, `weapon_count_min/max`, `secondary_weapon_count_min/max`, `module_count_min/max`, `turret_count_min/max`

**Constraint:** `min >= 1`, `min <= max`. Min and max must be provided together per item type.

### Item Quantity Ranges

Controls how many copies of each item are stocked per listing.

Not settable via slash command — set via the API: `PUT /api/v1/config/guild/{guild_id}/shop` with a `quantity_ranges` body field (same endpoint as tech-level probabilities).

**Defaults:**
| Item Type | Min Qty | Max Qty |
|-----------|---------|---------|
| Ships | 1 | 1 |
| Primary weapons | 2 | 4 |
| Secondary weapons | 2 | 4 |
| Modules | 2 | 4 |
| Turrets | 2 | 4 |

**Constraint:** `min >= 1`, `min <= max`.

**Note:** Secondary weapons are consumable rounds, so the rolled quantity is scaled up at refresh time: ×10 for standard secondaries, ×5 for heavy subtypes (`nuke`, `shock-blast`). Governed by `GameConstants.SHOP_SECONDARY_QTY_SCALER_STANDARD` / `SHOP_SECONDARY_QTY_SCALER_HEAVY`.

### Tech Level Probabilities

When generating shop stock, the probability distribution for item tech level relative to the tier's target TL.

| | |
|---|---|
| **View** | Via API: `GET /api/v1/config/guild/{guild_id}` (`shop_config.tech_level_probabilities`) |
| **Set** | Via API: `PUT /api/v1/config/guild/{guild_id}/shop` with `{"tech_level_probabilities": {"same_level": 0.70, "one_lower": 0.20, "two_lower": 0.10}}` |
| **Reset** | `/admin_config action:Reset to Defaults` |

**Defaults:** `same_level: 0.70`, `one_lower: 0.20`, `two_lower: 0.10`

**Constraint:** All three keys required; values must be `0.0–1.0`; must sum to `1.0` (±0.01 tolerance).

---

## Per-Guild Game Constant Overrides

53 global `GameConstants` values can be overridden per-guild via the API (`_OVERRIDE_FIELDS` in bot-core's config router); 52 of them are settable through the `/admin_config_constants` slash command. The remaining 1 is **API-only** — it is absent from the gateway's `_GAME_CONSTANT_FIELDS` slash list: `demotion_credit_penalty_pct`. When set to `NULL` (the default), the global constant applies. When set to a value, only this guild uses that value. (The count rose by 19 with the PvC-loot knobs, migration 0022 — see the loot block in the table below.)

### Viewing Overrides

```
# List the 52 slash-settable constants with current values (NULL shown as *default*)
/admin_config_constants

# See only constants that have been explicitly set for this guild
/admin_config_constants_view
```

### Setting an Override

```
/admin_config_constants setting:check_cooldown int_value:120
/admin_config_constants setting:duel_variance_percent float_value:0.10
/admin_config_constants setting:division_max_tl_bronze int_value:3
/admin_config_constants setting:division_max_tl_silver int_value:5
```

Use `int_value` for integer fields, `float_value` for float fields, `bool_value` for on/off toggles.
(The `json_value` parameter was retired in revision 0033 — no dict-type settings remain.)

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
| `close_bounty_threshold` | int | `4` | systems | Proximity hint threshold — the "close" hint fires when the answer system is 1 to (threshold − 1) systems ahead of the checked system |
| `max_route_length` | int | `50` | systems | Maximum length of a bounty criminal's A* route |
| `check_cooldown` | int | `180` | seconds | Cooldown between a player's `/check` commands |
| `duel_request_expiry` | int | `86400` | seconds | Time before a pending duel challenge auto-expires (default: 24 hours) |
| `tier_change_cooldown` | int | `86400` | seconds | Cooldown between tier advances/demotions (default: 24 hours) |
| `criminal_max_gear_upgrade` | int | `1` | TL levels | Max TL above criminal's base TL their gear can be |
| `bounty_reward_to_xp_gain_mult` | float | `0.1` | multiplier | XP = reward_credits × this multiplier |
| `bounty_winner_reserve_factor` | float | `0.25` | fraction | Fraction of reward guaranteed to winner (rest split as consolation) |
| `division_max_tl` | dict | `{"bronze":2,"silver":4,"gold":7,"platinum":10}` | TL | Max criminal tech level per division |
| `demotion_credit_penalty_pct` | int | `10` | percent | % of credits deducted on tier demotion. **API-only** — accepted by `PUT /api/v1/config/guild/{guild_id}` but not offered/validated by the `/admin_config_constants` slash command. |
| `classic_credits_per_check` | int | `1000` | credits | Credit floor per system check that seeds every bounty prize pool. **LIVE** — consumed via `game_maths.reward_per_sys_check()` (floored here at this value), then `bounty_service.spawn_bounty()` uses the result as `total_reward = _legacy_rps × len(route)`, which feeds the division-reward multiplier, winner-reserve split, and per-system consolation payout. The `_legacy_rps` local name and the helper's "deprecated" docstring refer to the formula's lineage, not deadness. GuildConfig column exists but per-guild wiring is not yet live (Unit D1 planned). |

> `bounty_pvc_armour_buff_factor` and `duel_variance_percent` were retired in the T10 combat migration (the old `SimpleTTKResolver` was removed) and are no longer overridable.

### Criminal Loadout Balance (per-guild)

These knobs tune how bounty-criminal ships are equipped (primary-weapon range/TL selection and module equip odds). Each is a **per-guild override of the `GameConstants` default**: `NULL` (unset) falls back to the global default; a set value applies only to this guild. All are **slash-settable** via `/admin_config` (in addition to `PUT /api/v1/config/guild/{guild_id}`): use `int_value` for ints, `float_value` for floats, `bool_value` for toggles. Value correctness is enforced server-side by the config schema.

The per-division and per-key settings (e.g. `criminal_cloak_chance_bronze`, `division_max_tl_gold`) are individual scalar columns (set each key separately). The JSONB dict fields were retired in revision 0033 — use the flat scalars instead.

For the full selection mechanics, see `services/bot-core/src/services/AGENTS.md` → "Criminal loadout-generation algorithm" (Threads 1/3/4/6).

| Setting Name | Type | Default | What It Controls |
|---|---|---|---|
| `long_range_threshold_m` | int | `2600` | Thread 3 — a primary weapon is classified LONG iff its `range_m` exceeds this (metres); otherwise SHORT. Validated `>= 0`. |
| `criminal_long_range_pct` | float | `0.50` | Thread 3 — floor share of long-range primaries equipped per criminal ship (`ceil(pct × max_primaries)`), plus a per-remaining-slot long roll. Validated `0.0–1.0`. |
| `primary_tl_band_weight_center` / `_minus1` / `_plus1` | int | `70` / `20` / `10` | Thread 3 — relative pick weights for the ±1 tech-level band when selecting a criminal primary (`center` = target TL). Non-negative ints. |
| `criminal_cloak_chance_{bronze,...}` | int (%) | `0`/`25`/`66`/`100` | Thread 4 — Gate-1 percent chance a criminal equips a Cloak, per division. Validated `0–100`. |
| `criminal_booster_chance_{bronze,...}` | int (%) | `50`/`100`/`100`/`100` | Thread 4 — Gate-1 percent chance a criminal equips a Booster module, per division. Validated `0–100`. |
| `criminal_emergency_chance_{bronze,...}` | int (%) | `0`/`25`/`50`/`100` | Thread 4 — Gate-1 percent chance a criminal equips an Emergency System module, per division. Validated `0–100`. |
| `criminal_weaponmod_chance_{bronze,...}` | int (%) | `0`/`25`/`50`/`100` | Thread 4 — Gate-1 percent chance a criminal equips a Weapon Mod module, per division. Validated `0–100`. |
| `criminal_exclude_emp_weapons` | bool | `true` | Thread 6 — when on, excludes primarily-EMP weapons (`emp_damage > real_damage`) from criminal primary + secondary selection. Strict bool (`0`/`1`/`"true"` are rejected). Intended to auto-disable once EMP mechanics ship. |

### PvC Loot (per-guild)

These 19 scalar knobs tune the PvC looting system (loot pulled from a defeated criminal on a bounty win). Each is a per-guild override of the `GameConstants.LOOT_*` default added by **migration 0022** (`NULL` ⇒ global default), slash-settable via `/admin_config_constants` (`int_value` for the int knobs, `float_value` for `loot_commodity_sell_fraction`). The canonical spec is `COMBAT_SPEC_LOCKED.md §15`. **`LOOT_DROP_CHANCE` is a fixed 100% constant — it is NOT a per-guild override and has no row.**

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `loot_chance_tractor_t1` | int | `20` | % loot-roll chance with AB-1 "Retractor" (TL4) equipped |
| `loot_chance_tractor_t2` | int | `40` | % with AB-2 "Glue Gun" (TL5) |
| `loot_chance_tractor_t3` | int | `60` | % with AB-3 "Kingfisher" (TL7) |
| `loot_chance_tractor_t4` | int | `80` | % with AB-4 "Octopus" (TL8) |
| `loot_chance_no_tractor` | int | `0` | % with no tractor beam equipped |
| `loot_band1_select_pct` | int | `10` | % the drop is a Band-1 item (Weapons + Modules) |
| `loot_band2_select_pct` | int | `20` | % the drop is a Band-2 item (`ore_core`, `rare`) |
| `loot_band3_select_pct` | int | `70` | % the drop is a Band-3 item (bulk commodities) |
| `loot_band1_tl_window` | int | `1` | Band-1 item must be within ±this of the criminal's tech level |
| `loot_band1_qty_min` / `_mode` / `_max` | int | `1` / `1` / `3` | Band-1 triangular quantity (→ 50/33/17) |
| `loot_band2_qty_min` / `_mode` / `_max` | int | `4` / `8` / `12` | Band-2 triangular quantity (mean 8) |
| `loot_band3_qty_min` / `_mode` / `_max` | int | `10` / `16` / `22` | Band-3 triangular quantity (mean 16) |
| `loot_commodity_sell_fraction` | float | `1.0` | commodity sink payout = `Item.value` × qty × this |

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

# Standalone shortcut for the bounty check cooldown (same effect as Reset Bounty Cooldown)
/admin_cooldown_reset user:@Player
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

**Note:** You cannot remove the player's active ship when it is their only ship.

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

Bypasses the per-tier active-bounty cap and the spawn schedule — spawns bounties immediately.

```
# Spawn 1 bounty on each tier
/admin_spawn_bounty

# Spawn on a specific tier with quantity
/admin_spawn_bounty tier:Silver quantity:3
```

**Constraint:** quantity `1–10` (per tier).

---

## Duel Management

Cancel a pending duel (or all of them) without either player's involvement:

```
# Cancel one pending duel (autocomplete lists pending duels)
/admin_duel duel:<pick from autocomplete>

# Cancel ALL pending duels for this guild
/admin_duel duel:All
```

---

## Combat Log Review

Review any player's persisted after-action combat reports:

```
/admin_combat_log user:@Player battle:<pick from autocomplete>
```

The `battle` autocomplete lists the selected player's recent battles. The report renders exactly as if that player had run `/combat-log` themselves. The response is always ephemeral.

(The player-facing `/combat-log` accepts an optional `public:` flag, default `False`, to post the report publicly; errors stay ephemeral. Combat logs are retained for 72 hours by default — see `BOUNTYBOT_COMBAT_LOG_RETENTION_HOURS`.)

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

After refresh, the shop announcement is posted to `#shop` and the gateway's shop autocomplete cache is updated (both best-effort — a failed announcement or cache push does not fail the refresh).

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

## Events

Custom stat-race challenges let admins run time-limited competitions (e.g. "most bounty caps in 7 days"). The lifecycle is: **create → add prizes → start (now or scheduled) → auto-end at deadline + payout**.

### Running a Challenge

1. Create a draft event: `/admin_event_create type:<type> duration_days:<N>`
2. Add prizes: `/admin_event_add_prize event:<id> place:<1st|Top N|Participation> type:<Credits|Ship|…> qty:<N>`
   - Review the whole draft any time with `/admin_event_view event:<id>` (rules, settings, timing, prizes); change it with `/admin_event_edit event:<id> [duration_days] [division] [min_fights] [subtype|module|weapon]` — only the fields you pass change, `division:All divisions` removes a division filter. Drafts and scheduled events only; once active, end it and create a new one.
3. Start: `/admin_event_start event:<id>` (now) or `/admin_event_start event:<id> at:<YYYY-MM-DD HH:MM> utc_offset:<±N>`
4. The event ends automatically at the deadline; winners receive prizes and an end announcement is posted.
5. To end early with payout: `/admin_event_end event:<id> payout:Yes`
6. To cancel (no payout): `/admin_event_end event:<id> payout:No`

**Participation = did the activity.** Every event has a `min_fights` parameter (set at create via `/admin_event_create min_fights:<N>`; default is 10 for max/ratio types, 1 for everything else). A player qualifies — and receives the participation prize — if they reached that activity threshold, even with a primary score of 0. *Lossless-event example:* `duels_won`, `min_fights=3`, participation 3000 credits, stakes floor 1000 — a player who loses all three qualifying duels still breaks even.

**What counts per event family:**

| Family | What counts | Stakes filter |
|--------|------------|---------------|
| `duel` | Duels at or above `event_min_duel_stakes`. Stalemates count as fights but not wins/losses. | Yes — duels below the floor are ignored |
| `combat` | Duels at or above `event_min_duel_stakes`, plus all bounty fights. | Duels only |
| `bounty` | Bounty system checks and captures. | No |

**Self-damage note:** For `max_single_nuke_damage`, damage a player takes from their own nuke does not count toward their score — only hits landing on the opponent are tracked.

**Secondary scoring limits:** `secondary_fired` accepts `subtype` values `cluster-missile`, `ionizing-missile`, `missile`, `nuke`, `rocket`, `shock-blast` — `emp-bomb` is excluded because the resolver treats it as a deferred no-op and never emits a weapon-fire event. `kills_by_weapon` accepts `weapon` values `cluster-missile`, `missile`, `nuke`, `primary`, `rocket`, `turret` — `emp-bomb`, `shock-blast`, and `ionizing-missile` are excluded because `emp-bomb` fires no event and `shock-blast`/`ionizing-missile` deal 0 HP damage, making a killing blow impossible.

**Concrete in-game display:** When a player runs `/events event:<id>` or `/event_leaderboard event:<id>`, the embed shows the exact rules for that event instance — the live guild stakes floor, the `min_fights` threshold, and any division scope. No need to look these up manually; the bot derives them from current configuration.

A **notification role** (`Event Announcements`) is mentioned in start/end announcements when `event_announcements_role_id` is configured (set via `/admin_setup` — re-run after changing the role). Players opt in via `/notifications`.

### Admin Event Commands

| Command | Description |
|---------|-------------|
| `/admin_event_create` | Create a new draft event (type, duration, optional division/subtype/module/weapon params) |
| `/admin_event_view` | Show a draft/scheduled event in full: rules, settings, timing, prizes |
| `/admin_event_edit` | Change a draft/scheduled event's duration or params (only the fields you pass) |
| `/admin_event_add_prize` | Add a prize slot (1st–10th, Top N, or Participation; Credits/Ship/Primary/Secondary/Turret/Module) |
| `/admin_event_remove_prize` | Remove a prize from a draft event |
| `/admin_event_start` | Start an event immediately or schedule it at a future time |
| `/admin_event_end` | End an active event with or without payout |
| `/admin_event_delete` | Permanently delete a draft/scheduled/cancelled event |
| `/admin_event_list` | List events for this guild, optionally filtered by state |
| `/admin_sync_roles` | Force notification role sync for this guild (dry_run flag available) |

### Event Environment Variables

The following env vars control event-related behaviour (all optional — defaults shown):

| Variable | Default | Purpose |
|----------|---------|---------|
| `AUTOCOMPLETE_EVENTS_REFRESH_MINUTES` | `20` | TTL (dead-man switch) for the per-guild events autocomplete cache; bot-core pushes invalidations after every mutation |
| `NOTIFICATION_ROLE_SYNC_HOURS` | `24` | Interval for the background notification role sync job |
| `BOUNTYBOT_EVENT_METRICS_RETENTION_DAYS` | *(unset)* | If set, event metric rows older than this many days are pruned by the db_retention job |

---

## Scheduler Management

All scheduler commands are **super-admin only** (`DEVELOPERS` env var — no Administrator/Bot Admin fallback). They operate on bot-core's APScheduler job store.

```
# List all scheduled jobs (default recurring jobs + one-time jobs)
/scheduler_list

# View details of one job (autocomplete on job IDs)
/scheduler_view job_id:<id>

# Replace an existing job's payload (JSON string)
/scheduler_update job_id:<id> payload_json:{"...": ...}

# Delete a specific job
/scheduler_delete job_id:<id>

# Delete all one-time jobs scoped to THIS guild
/admin_clear_scheduler

# Wipe ALL jobs and re-register the default recurring jobs
/admin_reset_scheduler
```

> **Warning:** `/admin_reset_scheduler` is global, not per-guild — it calls bot-core's `POST /api/v1/reset`, which removes every job and re-registers the default recurring jobs from `main.py` (bounty spawn, shop refresh, bounty failsafe cleanup, pg backup, db retention). Note: `temperature_decay_default` was removed from default jobs in rev 0031 — it will NOT be re-added by a reset.

---

## Developer / Data Commands

Super-admin only (`DEVELOPERS` env var):

```
# Trigger a JSON → DB seed load for a data category (ship, module, criminal, …)
/load_data category:<category>

# Force-reload autocomplete source data in other cogs
/reload_autocomplete

# Drop and re-warm the gateway's in-process autocomplete caches
/force_reload_caches
```

The devCog also registers three prefix text commands gated to `DEVELOPERS` (bot command prefix: `COMMAND_PREFIX` env var, default `?p`): `snooze` (hide this bot's slash commands in the current guild), `wake` (reload all cogs and re-sync slash commands in the current guild), and `botstatus` (per-guild command-registration counts).

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
2. `/admin_spawn_bounty` to force-spawn immediately (bypasses the cap and schedule)
3. If counts are wrong, `/admin_clear_bounties` then `/admin_spawn_bounty`

### Too Many / Too Few Bounties

> The activity temperature system was **retired in rev 0031** (see [Activity Temperature System](#activity-temperature-system)). The active bounty cap is governed solely by `bounty_max_per_tier`. The procedures below reflect what actually changes spawn behaviour.

**Too many bounties:**
1. Run `/admin_config_bounty action:View` to see current per-tier counts.
2. Lower the per-tier cap via `/admin_config_bounty action:Update max_bronze:1 ...`.
3. Optionally clear existing surplus with `/admin_clear_bounties tier:<tier>`.

**Too few bounties:**
1. Raise the per-tier cap via `/admin_config_bounty action:Update max_bronze:5 ...`.
2. Force-seed with `/admin_spawn_bounty tier:<tier> quantity:N` (bypasses cooldowns and caps).

The `guild_activity_decay_rate` and `min_guild_activity` settings were retired in rev 0031 and are no longer settable.

### Accidental Config Reset (Channels/Roles Unlinked)

After a `/admin_config action:Reset to Defaults`, channel and role IDs are set to NULL but Discord objects are not deleted. Re-run `/admin_setup admin_role:@Role` to re-link them — it will find and adopt the existing infrastructure.

### Full Reset Without Wiping Players

The bot-core API supports a guild reset that preserves player records:

```bash
# Via bot-core API directly (not a slash command).
# user_id is required and must pass the ADMIN_USER_IDS check
# (empty ADMIN_USER_IDS = dev mode, any user_id accepted):
curl -X POST "http://localhost:8000/api/v1/admin/guilds/{guild_id}/reset?user_id={admin_user_id}&preserve_players=true"
```

This resets config to defaults (cancelling the guild's scheduled jobs first) and clears bounties/shops but leaves player accounts, XP, credits, and inventory intact. `preserve_players` defaults to `true`.

---

*Last updated: 2026-08-25 (rev 0031: 14 override fields retired — shop_default_*, turret_spawn_probability, activity/temperature subsystem, bounty timing dead constants, duel_cloak_chance, ship_value_reward_percentage, criminal_equip_damageless_weapon_chance; BOUNTY_DELAY_RANDOM_MIN renamed to BOUNTY_SPAWN_CHECK_INTERVAL_MINUTES; temperature_decay_default removed from default scheduler jobs; MAX_SHIP_NICKNAME_LENGTH raised 30→100). Prior: 2026-06-20 (PvC loot: +19 loot knobs / migration 0022, override count 34→53).*
