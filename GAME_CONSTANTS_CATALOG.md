# GameConstants Catalog

Complete inventory of every `GameConstants` value: what it does, who may tune it, and
within what bounds. Produced for issue #70 (per-guild override refactor); the
**Admin-facing** column is the seed text for the planned admin guild-config UI revamp —
each row's plain-English sentence becomes that setting's help text.

Generated 2026-08-24 against `dev` (audit @ `0f3de7f`, catalog on `feat/override-gating`).
Verified 2026-08-25: every row adversarially traced to live code (call-graph liveness + citation checks); 51 rows corrected. Code is the only source consulted — repo markdown docs are known to have drifted.
Update rows as batches of the #70 plan land; this file is the reference, the code is the truth.

## Reading this catalog

| Column | Meaning |
|---|---|
| **Scope** | `per-guild (wired)` = override works end-to-end today · `per-guild planned` / `convert planned` = becoming guild-settable per the #70 triage · `global-invariant` / `global-structural` / `global-infra` = intentionally not guild-settable (data-model invariants, structural sets, operator concerns like retention/CPU) · `retiring` = dead code being deleted · `owner-flag` = awaiting an owner decision |
| **Bounds** | Enforced ranges live in `services/bot-core/src/api/schemas/config_schema.py` (Pydantic `ge`/`le`); ranges marked `(proposed)` are recommendations for when that constant becomes settable, not yet enforced |
| **Technical** | Precise semantics: units, formula role, consumption site |
| **Admin-facing** | One sentence a non-technical guild admin understands — future setting help text |

**Validation contract** (enforced 2026-08-24): every settable override is strictly typed —
a bool field rejects `0`/`1`/`"true"`, an int field rejects `"5"`/`true`/`5.5` (plain ints are
still accepted for float fields) — and every numeric field carries sane `ge`/`le` bounds.
New per-guild settings MUST be simple scalars (int / float / bool / single string); the seven
existing JSONB dict fields are grandfathered.

**Retired 2026-08-24:** `KAAMO_MAX_CAPACITY` (owner decision: Kaamo storage capacity is not
a mechanic and never will be; the constant, column, and API/slash exposure were removed —
revision `0027`). It does not appear below.

## Bounty, Progression, Activity & Timers

### Tech Levels

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `MIN_TECH_LEVEL` | int | 1 | global-invariant | — | Hard floor for the TL scale; used as the iteration start in pick_random_item_tl() and all TL-range loops throughout bounty_service and shop_service. | (Global constant — the lowest possible equipment tech level in the game; cannot be changed per guild.) |
| `MAX_TECH_LEVEL` | int | 10 | global-invariant | — | Hard ceiling for the TL scale; bounds TL loops and schema validators for division_max_tl (1–10 per div); consumed across bounty_service, shop_service, and config_schema. | (Global constant — the highest possible equipment tech level in the game; cannot be changed per guild.) |
| `DIVISION_TL_CENTERS` | dict[str, int] | `{bronze:1, silver:3, gold:6, platinum:8}` | global (flatten candidate) | — | Per-division centre TL consumed in `game_maths.pick_division_tech_level()`, which is imported and called by `bounty_service.spawn_bounty` alongside the per-guild DIVISION_MAX_TL cap. A flatten refactor (issue #70) would split this into four scalar per-guild columns. | (Global — sets the target equipment tech level for criminals in each division; not yet per-guild.) |
| `DIVISION_MAX_TL` | dict[str, int] | `{bronze:2, silver:4, gold:7, platinum:10}` | per-guild (JSONB, wired) | 1–10 per div | Hard ceiling on criminal equipment TL per division, resolved via resolve_constant in bounty_service; Bronze is capped at 2 (Betty-class) so new players can compete. | The highest equipment tech level a criminal can carry per division — lower values make bounties easier to defeat. |

### Bounty Rewards & XP

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `BOUNTY_REWARD_TO_XP_GAIN_MULT` | float | 0.1 | per-guild (wired — except Bronze combat-bonus XP path in `_award_combat_bonus` which always uses the global default) | 0.0–100.0 | Multiplies the winner's total credit payout to yield XP; applied in bounty_service as `xp = int(total_winner_credits × mult)` after the winner-reserve split. The Bronze post-capture combat-bonus path reads the global directly (no per-guild resolution) — partial wiring gap. | How much XP players earn per credit gained from capturing a bounty (0.1 = 1 XP per 10 credits). |
| `BOUNTY_WINNER_RESERVE_FACTOR` | float | 0.25 | per-guild (wired) | 0.0–1.0 | Fraction of the total prize pool set aside as the winner's guaranteed payout; the remainder is the consolation pool split evenly across route systems in `bounty_service.spawn_bounty` (used to seed the per-system consolation payout stored on the Bounty row). | The share of a bounty's prize pool guaranteed to the player who caught the criminal, with the rest split among other checkers. |
| `BOUNTY_DIVISION_REWARD_MULT` | dict[str, float] | `{bronze:1.0, silver:2.0, gold:1.0, platinum:1.0}` | per-guild (JSONB, wired) | ≥0.0 per div (proposed ≤10.0) | Scales the full prize pool before the winner-reserve split in `bounty_service.spawn_bounty`; silver defaults to 2.0 to remove the dead-rung payout issue where silver rewards were near-equal to bronze despite a harder fight. | A per-division multiplier on bounty prize pools — silver defaults to 2× so rewards match difficulty. |

### Bronze Combat Bonus (Issue #51)

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `BRONZE_COMBAT_BONUS_BASE_MULT` | float | 0.40 | convert planned | 0.0–1.0 (proposed) | Base fraction of the winner's reward awarded as bonus credits after a successful Bronze post-capture duel; part of `min(CAP, BASE + PER_PRESTIGE × prestige_count)` in bounty_service. | The starting bonus percentage a Bronze player earns for winning the optional post-capture duel (40% of their bounty reward at 0 prestige stars). |
| `BRONZE_COMBAT_BONUS_PER_PRESTIGE` | float | 0.10 | convert planned | 0.0–0.5 (proposed) | Increment added to the Bronze combat bonus fraction per prestige_count star; consumed alongside BASE_MULT and CAP in bounty_service._bronze_combat_bonus_fraction. | How much the Bronze post-capture duel bonus grows per prestige star (+10% per star by default). |
| `BRONZE_COMBAT_BONUS_CAP` | float | 1.00 | convert planned | 0.0–2.0 (proposed) | Upper clamp on the Bronze combat bonus fraction; the formula `min(CAP, BASE + PER_PRESTIGE × n)` reaches 100% at 6★ with defaults, preventing the bonus from exceeding a full reward payout. | The maximum bonus a Bronze player can earn from the post-capture duel, as a fraction of their bounty reward (100% by default, reached at 6 prestige stars). |

### Bounty Routing & Capacity

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `MAX_BOUNTIES_PER_DIVISION` | int | 5 | **Retired rev 0031** | — | Never read by live code — the spawn executor caps per tier via the per-guild `bounty_max_per_tier` JSON (fallback 3); `TemperatureService.get_max_bounties()` was deleted in rev 0031. | Retired — had no effect. |
| `CLOSE_BOUNTY_THRESHOLD` | int | 4 | per-guild (wired) | 1–50 | Number of route stops ahead of the current system at which a "close" proximity hint is shown; resolved via resolve_constant and applied inline in `bounty_service._process_single_bounty_check`. | How many systems away a criminal must be before players see a "close" hint in their /check result. |
| `SHIP_VALUE_REWARD_PERCENTAGE` | float | 0.01 | **Retired rev 0031** | — | Originally 1% of the criminal's ship credit value added to the bounty reward; no live code path calls it — constant and column removed in rev 0031. | Retired — had no effect. |
| `CRIMINAL_EQUIP_DAMAGELESS_WEAPON_CHANCE` | int (%) | 20 | **Retired rev 0031** | — | Intended chance that a criminal receives a damageless weapon; superseded by the CRIMINAL_EXCLUDE_EMP_WEAPONS toggle — constant and column removed in rev 0031. | Retired — had no effect. |
| `CRIMINAL_MAX_GEAR_UPGRADE` | int | 1 | per-guild (wired) | 0–10 | Maximum TL steps above the criminal's base TL that spawned weapons and modules may be; resolved via resolve_constant in `bounty_service.generate_loadout`. | How many tech levels above a criminal's base level its weapons and modules can reach during spawn. |
| `MAX_ROUTE_LENGTH` | int | 50 | per-guild (override currently broken — pathfinder uses a shadow literal; wiring fix planned) | 1–500 | A* hop limit in pathfinding_service._find_route; the pathfinder reads a module-level shadow literal (`MAX_ROUTE_LENGTH = 50` at line 25) rather than the GuildConfig column, so per-guild overrides have no effect until the wiring fix lands. | The longest route (in star-system hops) the bot will plot for a bounty — currently fixed at 50 regardless of your guild setting until a fix is deployed. |
| `MIN_ROUTE_SYSTEMS` | int | 3 | per-guild (wired) | 2–50 | Routes shorter than this value are rejected and re-rolled in bounty_service._generate_route; prevents trivially short 2-system hunts near adjacent gates. Resolved via resolve_constant. | The shortest route (in systems) a bounty will ever spawn with — lower values allow easier "next door" hunts. |
| `RECENTLY_SPOTTED_MAX_WINDOW` | int | 3 | per-guild (wired) | 0–50 | Upper bound of the per-bounty window B rolled from [0, B] at spawn and stored on the bounty row; a system shows "recently spotted" iff it is 1..B stops before the answer, preventing players from triangulating the exact distance. 0 disables the hint entirely. | Controls how far ahead of the criminal's location a "recently spotted" hint can appear in /check — 0 disables the hint entirely. |

### Waypoint Routing

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `BOUNTY_SINGLE_WAYPOINT_PROB` | float | 0.33 | per-guild (column live; API exposure planned) | 0.0–1.0 (proposed) | Probability of a single-waypoint route (A→B→C) tried if the dual-waypoint roll fails; consumed in bounty_service._generate_waypoint_route. Column exists on GuildConfig; no API endpoint exposes it yet. | Chance that a bounty route passes through one intermediate waypoint system, making the hunt more varied (33% by default). |
| `BOUNTY_DUAL_WAYPOINT_PROB` | float | 0.10 | per-guild (column live; API exposure planned) | 0.0–1.0 (proposed) | Probability of a dual-waypoint route (A→B→C→D), rolled first; if it fails, BOUNTY_SINGLE_WAYPOINT_PROB is tried next in bounty_service._generate_waypoint_route. | Chance that a bounty route passes through two intermediate waypoint systems, creating the longest and most complex hunts (10% by default). |
| `BOUNTY_WAYPOINT_ATTEMPTS` | int | 20 | per-guild (column live; API exposure planned) | 1–100 (proposed) | Maximum endpoint and waypoint re-roll attempts before generation falls back to a standard A→C direct route in bounty_service._generate_waypoint_route; guards against infinite loops in sparse galaxy maps. | How many times the bot retries finding a valid waypoint route before falling back to a standard direct route. |
| `BOUNTY_WAYPOINT_MIN_DEGREE` | int | 2 | per-guild (column live; API exposure planned) | 1–10 (proposed) | Minimum gate-connected neighbours a candidate waypoint system must retain after earlier-leg systems are removed; enforced in `bounty_service._eligible_waypoints`. | The minimum number of connections a waypoint system must have to be used — prevents criminals from being routed through dead-end systems. |

### Criminal Loadout — Primary Weapons & Range

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `LONG_RANGE_THRESHOLD_M` | int (metres) | 2600 | per-guild (wired) | 0–50000 | Cutoff in metres; a primary weapon whose range_m exceeds this value is classified as LONG-range in `bounty_service._select_primaries`, feeding the CRIMINAL_LONG_RANGE_PCT floor calculation. | The weapon range (in metres) above which a primary weapon counts as "long-range" when arming a criminal. |
| `CRIMINAL_LONG_RANGE_PCT` | float | 0.50 | per-guild (wired) | 0.0–1.0 | Floor share of long-range primaries per criminal ship computed as `ceil(pct × max_primaries)`; remaining slots use a per-slot roll in `bounty_service._select_primaries`. | The minimum fraction of a criminal's primary weapons that must be long-range (50% by default). |
| `PRIMARY_TL_BAND_WEIGHTS` | dict[str, int] | `{center:70, minus1:20, plus1:10}` | per-guild (JSONB, wired) | ≥0 per key (proposed ≤1000) | Relative pick weights for the ±1 TL band around the target TL when selecting criminal primaries in `bounty_service._select_primaries`; keys must be exactly {center, minus1, plus1}. | How likely criminals are to carry weapons exactly at their tech level versus one level below or above (defaults favour exact-TL weapons 70% of the time). |

### Criminal Loadout — Module Gate Chances

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `CRIMINAL_CLOAK_CHANCE_BY_DIVISION` | dict[str, int] (%) | `{bronze:0, silver:25, gold:66, platinum:100}` | per-guild (JSONB, wired) | 0–100 per div | Chance (%) that a criminal receives a CloakModule in `bounty_service._select_modules` (the two-gate path, guarded by `random.randint(1,100) <= chance`); Bronze criminals never cloak, Platinum always do. | The percentage chance a criminal has a cloaking device per division — 0 means never, 100 means always. |
| `CRIMINAL_BOOSTER_CHANCE_BY_DIVISION` | dict[str, int] (%) | `{bronze:50, silver:100, gold:100, platinum:100}` | per-guild (JSONB, wired) | 0–100 per div | Chance (%) that a criminal receives a BoosterModule in `bounty_service._select_modules` (the two-gate path, guarded by `random.randint(1,100) <= chance`); Bronze at 50% gives new players a manageable fight while Silver+ always field one. | The percentage chance a criminal has a weapon booster per division. |
| `CRIMINAL_EMERGENCY_CHANCE_BY_DIVISION` | dict[str, int] (%) | `{bronze:0, silver:25, gold:50, platinum:100}` | per-guild (JSONB, wired) | 0–100 per div | Chance (%) that a criminal receives an EmergencySystemModule in `bounty_service._select_modules` (the two-gate path, guarded by `random.randint(1,100) <= chance`); scales with division difficulty. | The percentage chance a criminal has an emergency survival system per division — higher values make criminals harder to destroy. |
| `CRIMINAL_WEAPONMOD_CHANCE_BY_DIVISION` | dict[str, int] (%) | `{bronze:0, silver:25, gold:50, platinum:100}` | per-guild (JSONB, wired) | 0–100 per div | Chance (%) that a criminal receives a PrimaryWeaponModModule; scales weapon lethality per tier in `bounty_service._select_modules` (the two-gate path, guarded by `random.randint(1,100) <= chance`). | The percentage chance a criminal has a weapon damage modifier per division. |
| `CRIMINAL_EXCLUDE_EMP_WEAPONS` | bool | True | per-guild (wired) | — | When True, weapons where emp_damage > real_damage are excluded from criminal primary and secondary selection in bounty_service; designed as a temporary guard until EMP mechanics ship — set False once EMP damage is live. | Whether to exclude EMP-focused weapons from criminal loadouts (leave enabled until EMP combat mechanics are fully implemented). |

### Criminal Loadout — Secondary Weapons

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `CRIMINAL_SECONDARY_ROUNDS` | dict[str, int] | `{nuke:1, missile:5, rocket:5, cluster-missile:3, shock-blast:2}` | global (flatten candidate) | — | Round count assigned per secondary weapon subtype; consumed inline in `bounty_service.generate_loadout`; nuke is capped at 1 to prevent unwinnable alpha-strikes. | (Global — sets how many rounds each type of criminal secondary weapon carries; nuke is fixed at 1 to prevent one-shot kills.) |
| `CRIMINAL_SECONDARY_MIN_DAMAGE` | int | 1 | convert planned | 0–100 (proposed) | Minimum real damage a secondary weapon must deal to be included in criminal selection; default 1 excludes both zero-damage weapons and the 1-dmg Fireworks dummy round in bounty_service._select_secondary_weapons. | The minimum damage a secondary weapon must deal to appear in a criminal's loadout — raise to remove weak secondaries, lower to 0 to allow all weapons. |

### Activity & Temperature

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `GUILD_ACTIVITY_DECAY_RATE` | float | ~0.667 (2/3) | **Retired rev 0031** | — | Temperature subsystem removed in rev 0031 — `temperature_service` module deleted, guild_configs column dropped, executor replaced with no-op. Constant and column gone. | Retired — temperature subsystem removed. |
| `MIN_GUILD_ACTIVITY` | float | 1.0 | **Retired rev 0031** | — | Temperature subsystem removed in rev 0031 — `temperature_service` module deleted, guild_configs column dropped. Constant and column gone. | Retired — temperature subsystem removed. |
| `ACTIVITY_TEMP_PER_PLAYER` | int | 1 | **Retired rev 0031** | — | Temperature subsystem removed in rev 0031 — `temperature_service` module deleted. Constant gone. | Retired — temperature subsystem removed. |

### Bounty Spawn Timing

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `BOUNTY_SPAWN_CHECK_INTERVAL_MINUTES` | int (minutes) | 5 | global (owner-flag; scheduler-level) | 1–1440 | **Renamed from `BOUNTY_DELAY_RANDOM_MIN` in rev 0031** (env var: `BOUNTYBOT_BOUNTY_SPAWN_CHECK_INTERVAL_MINUTES`). Sets the cron recurrence step (in minutes) for the APScheduler `bounty_spawn_default` job in main.py: `f"*/{BOUNTY_SPAWN_CHECK_INTERVAL_MINUTES} * * * *"`. With the default of 5 the orchestrator fires every 5 minutes. Value only takes effect on first boot or after POST /scheduler/reset — the cron trigger is baked into the APScheduler job store at first registration; the seed block is skipped on every subsequent boot. | Controls how often the bot checks whether to spawn bounties (every 5 minutes by default). |
| `BOUNTY_DELAY_RANDOM_MAX` | int (minutes) | 7 | **Retired rev 0031** | — | Default 7. Referenced only by the dead `temperature_service.calculate_spawn_delay()` which was never called in production; both the constant and its guild_configs column were removed in rev 0031. | Retired — had no effect. |
| `BOUNTY_SPAWN_JITTER` | int (seconds) | 180 | global (owner-flag; scheduler-level) | — | Seconds of random offset added to each APScheduler spawn-check trigger in main.py; randomises the wall-clock minute within the cron window so multiple scheduler instances don't fire simultaneously. Not resolved per-guild. Value only takes effect on first boot or after POST /scheduler/reset — the jitter is baked into the job store row at first registration; the seed block is skipped on every subsequent boot. | (Global — not per-guild. Adds random spread to when the bounty spawn check fires each cycle.) |

### Timers

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `GUILD_ACTIVITY_DECAY_INTERVAL` | int (seconds) | 3600 | **Retired rev 0031** | — | Temperature subsystem removed in rev 0031 — temperature-decay job removed from DEFAULT_SCHEDULER_JOBS; its executor is now a no-op. Constant gone. | Retired — temperature subsystem removed. |
| `CHECK_COOLDOWN` | int (seconds) | 180 | per-guild (wired) | 0–86400 | Minimum seconds between a player's successive /check uses; enforced in bounty_service.check_system via resolve_constant against the player's last_check_at timestamp. | How long players must wait between /check uses (3 minutes by default). |
| `DUEL_REQUEST_EXPIRY` | int (seconds) | 86400 | per-guild (wired) | 0–2592000 | Seconds after which an open duel request is automatically expired; resolved via resolve_constant in `duel_service.create_challenge` and read during duel-expiry housekeeping. | How long a duel challenge stays open before it automatically expires (24 hours by default). |
| `TIER_CHANGE_COOLDOWN` | int (seconds) | 86400 | per-guild (wired) | 0–2592000 | Enforced wait between /promote or /demote operations per player; resolved via resolve_constant in player_service.check_tier_change_cooldown. | How long a player must wait after moving tiers before they can promote or demote again (24 hours by default). |

### Data Retention

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `BOUNTY_RETENTION_HOURS` | int (hours) | 24 | global-infra (retention) | — | Age threshold past which terminal-state bounty rows are pruned in db_retention_executor; aggregate stats are written to the players table before pruning, so no game-relevant data is lost. | (Global — controls how long completed bounty records are kept in the database before being purged.) |
| `DUEL_RETENTION_HOURS` | int (hours) | 24 | global-infra (retention) | — | Age threshold for purging terminal-state duel_requests rows in db_retention_executor; independent of combat log retention (COMBAT_LOG_PVP_RETENTION_HOURS). | (Global — controls how long completed duel records are kept in the database.) |
| `AUDIT_RETENTION_DAYS` | int (days) | 30 | global-infra (retention) | — | Age threshold for pruning AdminAuditLog rows in `db_retention_executor` (which passes the computed cutoff to `AdminAuditLogRepository.delete_older_than()`); full audit history is preserved separately via scheduled pg_backup. | (Global — controls how long admin action logs are retained in the live database before being purged; backups are kept separately.) |

### Demotion & Classic Mode

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `DEMOTION_CREDIT_PENALTY_PCT` | int (%) | 10 | per-guild (wired; slash exposure planned) | 0–100 | Percentage of a player's credits deducted on /demote; resolved via resolve_constant in player_service.demote_player. The /admin config slash command does not yet expose this field. | The percentage of credits a player loses when they are demoted to a lower tier (10% by default). |
| `CLASSIC_CREDITS_PER_CHECK` | int (credits) | 1000 | per-guild (override currently broken; wiring fix planned) | 0–1000000 | LIVE. Floors the per-system credit reward in `game_maths.reward_per_sys_check()` (`game_maths.py:188`); called as `_legacy_rps` in `bounty_service.spawn_bounty():1898`; `total_reward = _legacy_rps * len(route)` then feeds the division-reward multiplier, winner-reserve split, and per-system consolation payout stored on the Bounty row (`:1899–1916`). The `_legacy_rps` local name and the helper's "deprecated" docstring refer to the formula's lineage, not deadness — this value seeds the entire bounty prize pool. GuildConfig column exists but `resolve_constant` is not called at the read site (wiring fix planned: Unit D1). | Sets the minimum credit reward per system check that seeds every bounty prize pool — a higher floor raises all bounty payouts. Currently fixed at 1000 regardless of guild setting until the wiring fix (Unit D1) lands. |

### Retiring Constants

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `DUEL_LOG_MAX_LENGTH` | int | 10 | **Retired rev 0031** | — | Originally capped the number of entries in a duel combat log; no production code reads it since TickResolver replaced SimpleTTKResolver in T10. Constant removed in rev 0031. | Retired — had no effect. |
| `DUEL_CLOAK_CHANCE` | int (%) | 20 | **Retired rev 0031** | — | Formerly the chance (%) of a cloak trigger in a duel; superseded by the Phase-1 accuracy-based cloak model in T10's TickResolver. Constant and column removed in rev 0031. | Retired — had no effect. |

### Inventory

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `MAX_SHIP_NICKNAME_LENGTH` | int (characters) | 30 | owner-flag (unenforced; constant says 30, gateway text says 50) | — | Intended character limit for player ship nicknames; no validation layer reads this constant — the Discord modal enforces a 50-character limit, creating a silent mismatch. No production enforcement code exists. | (Owner-flag — ship nickname length is not currently enforced; the Discord modal allows up to 50 characters regardless of this setting.) |

## Shop, Loot & Economy

### Shop Stock Generation (Legacy / Retiring)

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `SHIP_PRICE_THRESHOLDS` | `list[int]` | `[50000, 100000, 200000, 500000, 1000000, 2000000, 5000000, 7000000, 7500000, 999999999]` | global-invariant | — | Ten credit breakpoints indexed 0–9 (TL 1–10); used by ship-classification logic to bucket ships by market value for criminal loadout and reward calculations. | Hard-coded price tiers that classify ships from cheapest (TL 1, 50 k credits) to most expensive (TL 10, uncapped); not tunable per guild. |
| `SHOP_REFRESH_INTERVAL` | `int` | `21600` | **Retired rev 0031** | — | Was the refresh cadence in seconds (6 hours); now dead code — shop refresh is driven entirely by the scheduled-jobs system. Constant removed in rev 0031. | Retired — had no effect. |
| `SHOP_DEFAULT_SHIPS_NUM` | `int` | `5` | **Retired rev 0031** | — | Former default count of ships stocked per refresh; superseded by the per-guild `item_count_ranges` JSON. Constant and column removed in rev 0031. | Retired — had no effect. |
| `SHOP_DEFAULT_WEAPONS_NUM` | `int` | `5` | **Retired rev 0031** | — | Former default weapon count per refresh; same retirement as `SHOP_DEFAULT_SHIPS_NUM`. Constant and column removed in rev 0031. | Retired — had no effect. |
| `SHOP_DEFAULT_MODULES_NUM` | `int` | `5` | **Retired rev 0031** | — | Former default module count per refresh; same retirement as `SHOP_DEFAULT_SHIPS_NUM`. Constant and column removed in rev 0031. | Retired — had no effect. |
| `SHOP_DEFAULT_TURRETS_NUM` | `int` | `2` | **Retired rev 0031** | — | Former default turret count per refresh; same retirement as `SHOP_DEFAULT_SHIPS_NUM`. Constant and column removed in rev 0031. | Retired — had no effect. |
| `SHOP_DEFAULT_TOOLS_NUM` | `int` | `0` | **Retired rev 0031** | — | Former default tool count per refresh; always 0, tools were never a live item type. Constant removed in rev 0031. | Retired — had no effect. |
| `TURRET_SPAWN_PROBABILITY` | `int` | `45` | **Retired rev 0031** | — | Was a percentage chance of spawning any turrets in a refresh cycle; dead — turret stocking governed by `item_count_ranges`. Constant and column removed in rev 0031. | Retired — had no effect. |

### Shop Secondary Weapons — Quantity Scaling

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `SHOP_HEAVY_SECONDARY_SUBTYPES` | `frozenset[str]` | `{"nuke", "shock-blast", "cluster-missile"}` | global-structural | — | Classifies secondary weapon subtypes as "heavy ordnance" for the quantity scaler branch inside `shop_service.ShopService.refresh_shop()`; any subtype missing from both sets gets the standard scaler. | Defines which secondary weapons (nukes, shock-blasts, cluster missiles) count as heavy — they stock in smaller bundles than standard ammo. |
| `SHOP_SECONDARY_QTY_SCALER_HEAVY` | `int` | `5` | convert planned | `1–50 (proposed)` | Multiplies the rolled per-item quantity for heavy secondary subtypes inside `shop_service.ShopService.refresh_shop()` (inline at the secondary_weapon branch); keeps high-damage ordnance scarcer than standard rounds across a full refresh cycle. | Controls how many rounds of heavy weapons (nukes, shock-blasts) appear per shop refresh — lower means fewer rounds stocked. |
| `SHOP_SECONDARY_QTY_SCALER_STANDARD` | `int` | `10` | convert planned | `1–100 (proposed)` | Multiplies the rolled per-item quantity for all secondary subtypes not in `SHOP_HEAVY_SECONDARY_SUBTYPES` inside `shop_service.ShopService.refresh_shop()`. | Controls how many rounds of standard ammo (missiles, rockets) appear per shop refresh — higher means more rounds stocked. |

### Shop Module Buckets

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `SHOP_JUNK_MODULE_TYPES` | `frozenset[str]` | `{"TransfusionBeamModule", "ShieldInjectorModule", "TimeExtenderModule", "JumpDriveModule"}` | global-structural | — | Modules excluded from shop draws via the junk filter in `shop_service.ShopService._get_random_item_by_tech_level()`; disjointness from the other two sets is asserted at import time as a drift guard across all 21 module types. | These module types never appear in the shop; they are reserved for future mechanics or are non-functional in the current system. |
| `SHOP_FILLER_MODULE_TYPES` | `frozenset[str]` | `{"GammaShieldModule", "SpectralFilterModule", "RepairBeamModule", "SignatureModule", "MiningDrillModule", "CompressorModule", "CabinModule"}` | global-structural | — | Non-combat modules drawn when `random.random() >= combat_module_prob` in `ShopService._get_random_item_by_tech_level()`; the bucket is chosen with probability `1 − SHOP_COMBAT_MODULE_PROB`. | Utility and support modules (mining drills, cabins, signature reducers, etc.) that stock as filler when the shop does not draw a combat-tier module. |
| `SHOP_COMBAT_MODULE_TYPES` | `frozenset[str]` | `{"ScannerModule", "ArmourModule", "ShieldModule", "CloakModule", "BoosterModule", "EmergencySystemModule", "RepairBotModule", "PrimaryWeaponModModule", "ThrusterModule", "TractorBeamModule"}` | global-structural | — | Combat-priority modules drawn when `random.random() < combat_module_prob` in `ShopService._get_random_item_by_tech_level()`; TractorBeamModule is intentionally elevated here (it gates PvC loot) despite being a filler-class module in criminal loadouts. | Combat and loot-enabling modules (armour, cloaks, boosters, tractor beams, etc.) that are prioritised in the shop draw so players can find them reliably. |
| `SHOP_COMBAT_MODULE_PROB` | `float` | `0.75` | per-guild (wired; slash exposure planned) | `0–1` | Probability that each module slot in a shop refresh draws from `SHOP_COMBAT_MODULE_TYPES`; resolved via `resolve_constant(config, "shop_combat_module_prob", GameConstants.SHOP_COMBAT_MODULE_PROB)` in `ShopService.refresh_shop()`, then passed as `combat_module_prob` to `_get_random_item_by_tech_level()`. | How often the shop stocks a combat-ready module (0 = always filler, 1 = always combat); default 0.75 means roughly 3 in 4 module slots are combat-tier. |

### Shop Tech-Level Banding

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `SHOP_BANDED_TL_WEIGHT` | `float` | `0.70` | per-guild read already live — column planned | `0–1 (proposed)` | Fraction of shop refreshes that draw their batch TL from the tier's in-band uniform range `[LO, HI]`; the remaining fraction draws from the exponential out-of-band taper; resolved via `resolve_constant` in `shop_service.ShopService._select_shop_tech_level()`. | How reliably the shop matches your division's gear tier (0 = always off-tier, 1 = always tier-matched); default 0.70 means 7 in 10 refreshes stock items squarely in your tier range. |
| `SHOP_TL_BAND_LO_BRONZE` | `int` | `1` | per-guild read already live — column planned | `1–10 (proposed)` | Lower bound of the in-band TL range for Bronze-tier shop refreshes; consumed via `getattr(GameConstants, f"SHOP_TL_BAND_LO_{key}")` in `shop_service.ShopService._select_shop_tech_level()`. | The lowest tech level that counts as "in tier" for Bronze shop draws; items below this appear only via the out-of-band taper. |
| `SHOP_TL_BAND_HI_BRONZE` | `int` | `2` | per-guild read already live — column planned | `1–10 (proposed)` | Upper bound of the in-band TL range for Bronze-tier shop refreshes; used alongside `SHOP_TL_BAND_LO_BRONZE` in `shop_service.ShopService._select_shop_tech_level()`. | The highest tech level that counts as "in tier" for Bronze shop draws; items above this appear only via the out-of-band taper. |
| `SHOP_TL_BAND_LO_SILVER` | `int` | `1` | per-guild read already live — column planned | `1–10 (proposed)` | Lower bound of the in-band TL range for Silver-tier shop refreshes; same draw logic as Bronze in `shop_service.ShopService._select_shop_tech_level()`. | The lowest tech level that counts as "in tier" for Silver shop draws. |
| `SHOP_TL_BAND_HI_SILVER` | `int` | `4` | per-guild read already live — column planned | `1–10 (proposed)` | Upper bound of the in-band TL range for Silver-tier shop refreshes. | The highest tech level that counts as "in tier" for Silver shop draws. |
| `SHOP_TL_BAND_LO_GOLD` | `int` | `4` | per-guild read already live — column planned | `1–10 (proposed)` | Lower bound of the in-band TL range for Gold-tier shop refreshes. | The lowest tech level that counts as "in tier" for Gold shop draws. |
| `SHOP_TL_BAND_HI_GOLD` | `int` | `7` | per-guild read already live — column planned | `1–10 (proposed)` | Upper bound of the in-band TL range for Gold-tier shop refreshes. | The highest tech level that counts as "in tier" for Gold shop draws. |
| `SHOP_TL_BAND_LO_PLATINUM` | `int` | `7` | per-guild read already live — column planned | `1–10 (proposed)` | Lower bound of the in-band TL range for Platinum-tier shop refreshes. | The lowest tech level that counts as "in tier" for Platinum shop draws. |
| `SHOP_TL_BAND_HI_PLATINUM` | `int` | `10` | per-guild read already live — column planned | `1–10 (proposed)` | Upper bound of the in-band TL range for Platinum-tier shop refreshes. | The highest tech level that counts as "in tier" for Platinum shop draws. |
| `SHOP_UPTIER_TL_DECAY` | `float` | `0.60` | per-guild read already live — column planned | `0–1 (proposed)` | Exponential decay factor applied per TL step above the band's HI in the out-of-band taper; smaller values make higher-TL items rarer; resolved via `resolve_constant` in `shop_service.ShopService._select_shop_tech_level()`. | How quickly above-tier items become rare in the shop (lower = steeper drop-off); default 0.60 allows moderate up-tier bleeding so players occasionally see next-tier gear. |
| `SHOP_DOWNTIER_TL_DECAY` | `float` | `0.45` | per-guild read already live — column planned | `0–1 (proposed)` | Exponential decay factor applied per TL step below the band's LO in the out-of-band taper; steeper than uptier to suppress off-tier junk; resolved via `resolve_constant` in `shop_service.ShopService._select_shop_tech_level()`. | How quickly below-tier items become rare in the shop (lower = steeper drop-off); default 0.45 is intentionally harsher than the uptier rate to keep junk off the shelves. |

### Item Type Vocabulary

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `NUM_SHIP_RANKS` | `int` | `10` | **Retired rev 0031** | — | Former rank-count constant used by legacy shop draw logic; dead — rank-based draws were replaced by TL-band draws. Constant removed in rev 0031. | Retired — had no effect. |
| `NUM_WEAPON_RANKS` | `int` | `10` | **Retired rev 0031** | — | Former weapon rank count; same retirement as `NUM_SHIP_RANKS`. Constant removed in rev 0031. | Retired — had no effect. |
| `NUM_MODULE_RANKS` | `int` | `7` | **Retired rev 0031** | — | Former module rank count; same retirement as `NUM_SHIP_RANKS`. Constant removed in rev 0031. | Retired — had no effect. |
| `NUM_TURRET_RANKS` | `int` | `3` | **Retired rev 0031** | — | Former turret rank count; same retirement as `NUM_SHIP_RANKS`. Constant removed in rev 0031. | Retired — had no effect. |
| `CATALOG_ITEM_TYPES` | `frozenset[str]` | `{"ship", "primary_weapon", "secondary_weapon", "turret_weapon", "module", "commodity"}` | global-structural | — | Complete set of concrete item-type discriminators present in the data model, including commodity (PvC loot cargo); used for browsing and catalog endpoints. Note: commodity is never stocked in a GuildShop — that gate is `_CONCRETE_TO_CONFIG_KEY` in `shop_service`. | The full list of item categories the bot knows about; changing this requires a code deploy, not a config change. |
| `PLAYABLE_ITEM_TYPES` | `frozenset[str]` | `{"ship", "primary_weapon", "secondary_weapon", "turret_weapon", "module", "commodity"}` | **Retired rev 0031** | — | Formerly a distinct set intended to diverge from `CATALOG_ITEM_TYPES` once deferred mechanics shipped; constant removed in rev 0031. | Retired — use `CURRENTLY_ENABLED_TYPES` instead. |
| `CURRENTLY_ENABLED_TYPES` | `frozenset[str]` | `{"ship", "primary_weapon", "secondary_weapon", "turret_weapon", "module", "commodity"}` | global-structural | — | Single authoritative lever gating item-type exposure across all economy and loadout flows; consumed in `shop_service.ShopService.refresh_shop()` to filter `_CONCRETE_TO_CONFIG_KEY` (shop_service.py:741), in `_item_type_normalizer.expand_item_type_to_concrete()` for all catalog/equip flows, and in `equipment_service.EquipmentService`. | The set of item types that are live in the economy today; anything not in this list is hidden from players even if data exists for it. |
| `GENERIC_TO_CONCRETE_EXPANSION` | `dict[str, tuple[str, ...]]` | `{"ship": ("ship",), "module": ("module",), "weapon": ("primary_weapon", "secondary_weapon", "turret_weapon"), "turret": ("turret_weapon",)}` | global-structural | — | Maps generic alias strings to one or more concrete `CATALOG_ITEM_TYPES` entries for catalog search; at runtime a playable-flavoured view is derived by filtering against `CURRENTLY_ENABLED_TYPES`. | Defines how shorthand terms like "weapon" expand to specific item categories in searches and commands. |
| `MODULE_EQUIP_LIMITS` | `dict[str, int]` | `{21-entry dict; positive = cap, -1 = unlimited, 0 = not equippable}` | global-structural | — | Per-module-type equip cap enforced in inventory/equip logic; `JumpDriveModule` is set to 0 (never equippable in current mechanics), `CabinModule` and `CompressorModule` are -1 (unlimited stacking). | Hard limits on how many of each module type a ship can equip; these are game-balance constraints and cannot be changed per guild. |

### Loot (PvC) — Tractor Beam Chances

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `LOOT_CHANCE_TRACTOR_T1` | `int` | `20` | per-guild (wired) | `0–100` | Integer percent chance of a successful loot roll when the player has a T1 tractor beam (AB-1 "Retractor") equipped; built into the tractor-beam chance map by `loot_service.LootService.resolve_tractor_chance_map()` via `_TRACTOR_TL_TO_TIER` (loot_service.py:63) → `_TIER_TO_KNOB`; per-guild override resolved in `LootService.loot_chance()` via `resolve_constant`. | Chance of looting a kill when using the weakest tractor beam; default 20% means roughly 1 in 5 bounties yield loot. |
| `LOOT_CHANCE_TRACTOR_T2` | `int` | `40` | per-guild (wired) | `0–100` | Integer percent loot-roll chance for T2 tractor beam (AB-2 "Glue Gun"); built into the tractor-beam chance map by `loot_service.LootService.resolve_tractor_chance_map()` via `_TRACTOR_TL_TO_TIER` → `_TIER_TO_KNOB`; per-guild override resolved in `LootService.loot_chance()` via `resolve_constant`. | Chance of looting a kill with the T2 tractor beam; default 40%. |
| `LOOT_CHANCE_TRACTOR_T3` | `int` | `60` | per-guild (wired) | `0–100` | Integer percent loot-roll chance for T3 tractor beam (AB-3 "Kingfisher"); same resolution path via `_TRACTOR_TL_TO_TIER` / `_TIER_TO_KNOB` in `loot_service.LootService.resolve_tractor_chance_map()` and `LootService.loot_chance()`. | Chance of looting a kill with the T3 tractor beam; default 60%. |
| `LOOT_CHANCE_TRACTOR_T4` | `int` | `80` | per-guild (wired) | `0–100` | Integer percent loot-roll chance for T4 tractor beam (AB-4 "Octopus"); same resolution path via `_TRACTOR_TL_TO_TIER` / `_TIER_TO_KNOB` in `loot_service.LootService.resolve_tractor_chance_map()` and `LootService.loot_chance()`. | Chance of looting a kill with the best tractor beam; default 80%. |
| `LOOT_CHANCE_NO_TRACTOR` | `int` | `0` | per-guild (wired) | `0–100` | Integer percent loot-roll chance when no tractor beam is equipped; resolved via `resolve_constant(guild_config, "loot_chance_no_tractor", GameConstants.LOOT_CHANCE_NO_TRACTOR)` in `loot_service.LootService.loot_chance()`, then passed as the `no_tractor` argument to the pure `loot_engine.tractor_chance()`. | Chance of looting a kill with no tractor beam equipped; default 0% means loot is impossible without the module. |

### Loot (PvC) — Band Selection & Quantity

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `LOOT_BAND1_SELECT_PCT` | `int` | `10` | per-guild (wired) | `0–100` | Integer percent weight for selecting Band 1 (weapons and modules) in `loot_engine.select_band`; weights are normalised to sum before use so they need not sum to exactly 100 after override. | How often a loot drop produces a weapon or module (Band 1); default 10% makes gear drops rare relative to commodities. |
| `LOOT_BAND2_SELECT_PCT` | `int` | `20` | per-guild (wired) | `0–100` | Integer percent weight for Band 2 (ore cores and rare commodities) in `loot_engine.select_band`. | How often a loot drop produces ore cores or rare cargo (Band 2); default 20%. |
| `LOOT_BAND3_SELECT_PCT` | `int` | `70` | per-guild (wired) | `0–100` | Integer percent weight for Band 3 (bulk commodities) in `loot_engine.select_band`; at defaults the three weights sum to 100. | How often a loot drop produces bulk cargo (Band 3); default 70% makes bulk drops the most common outcome. |
| `LOOT_BAND1_TL_WINDOW` | `int` | `1` | per-guild (wired) | `0–9` | Half-width of the TL window for Band-1 item selection in `loot_engine.band1_window_pool`; an item is eligible if `|item.tl − criminal.tl| ≤ window`; falls back to nearest-TL pool if the window yields no candidates. | How many TL steps above and below the criminal's gear level can appear in a Band-1 (weapon/module) drop; default 1 means only same- and adjacent-tier loot. |
| `LOOT_BAND1_QTY_MIN` | `int` | `1` | per-guild (wired) | `0–1000` | Minimum of the triangular quantity distribution for Band-1 drops; consumed as the `min` parameter when calling `random.triangular` in `loot_engine.roll_loot`. | Minimum number of items in a Band-1 (weapon/module) loot drop. |
| `LOOT_BAND1_QTY_MAX` | `int` | `3` | per-guild (wired) | `0–1000` | Maximum of the triangular quantity distribution for Band-1 drops; schema cross-validates `min <= mode <= max` when both sides are present in the same request. | Maximum number of items in a Band-1 (weapon/module) loot drop; default 3. |
| `LOOT_BAND1_QTY_MODE` | `int` | `1` | per-guild (wired) | `0–1000` | Mode (most likely value) of the triangular quantity distribution for Band-1 drops. | Most common number of items in a Band-1 (weapon/module) loot drop; default 1 (single-item drops are most likely). |
| `LOOT_BAND2_QTY_MIN` | `int` | `4` | per-guild (wired) | `0–1000` | Minimum of the triangular quantity distribution for Band-2 (ore core / rare commodity) drops; consumed alongside MODE and MAX in `loot_engine.roll_loot`. | Minimum number of items in a Band-2 (ore core / rare cargo) loot drop. |
| `LOOT_BAND2_QTY_MAX` | `int` | `12` | per-guild (wired) | `0–1000` | Maximum of the Band-2 quantity triangular distribution. | Maximum number of items in a Band-2 (ore core / rare cargo) loot drop; default 12. |
| `LOOT_BAND2_QTY_MODE` | `int` | `8` | per-guild (wired) | `0–1000` | Mode of the Band-2 quantity triangular distribution. | Most common number of items in a Band-2 (ore core / rare cargo) loot drop; default 8. |
| `LOOT_BAND3_QTY_MIN` | `int` | `10` | per-guild (wired) | `0–1000` | Minimum of the triangular quantity distribution for Band-3 (bulk commodity) drops. | Minimum number of items in a Band-3 (bulk cargo) loot drop. |
| `LOOT_BAND3_QTY_MAX` | `int` | `22` | per-guild (wired) | `0–1000` | Maximum of the Band-3 quantity triangular distribution. | Maximum number of items in a Band-3 (bulk cargo) loot drop; default 22. |
| `LOOT_BAND3_QTY_MODE` | `int` | `16` | per-guild (wired) | `0–1000` | Mode of the Band-3 quantity triangular distribution. | Most common number of items in a Band-3 (bulk cargo) loot drop; default 16. |
| `LOOT_COMMODITY_SELL_FRACTION` | `float` | `1.0` | per-guild (wired) | `0.0–10.0` | Multiplier on `Item.value × quantity` for commodity sell payouts in `shop_service.sell_item`; commodities are destroyed on sale (never re-stocked); resolved via `resolve_constant(guild_config, "loot_commodity_sell_fraction", …)`. | The fraction of face value players receive when selling looted cargo; default 1.0 = 100%, can be set above 1.0 for a bonus sell rate (up to 10×). |

## Combat Engine, Combat Log & Recap

### Pre-Phase-1 Placeholders (Retiring)

These ten constants were added to `GameConstants` as neutral stand-ins while the Phase-1 tick-based combat engine was designed. None is consumed by `TickResolver` in `combat_resolver.py`. All are flagged for removal once the column-drop migration lands.

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `DEFAULT_ACCURACY` | `float` | `1.0` | **Retired rev 0031** | — | Legacy placeholder for shot-hit probability (1.0 = always hit); the Phase-1 `TickResolver` derives attacker accuracy from the layered §5 formula. Constant removed in rev 0031. | Retired — had no effect. |
| `DEFAULT_EVASION` | `float` | `0.0` | **Retired rev 0031** | — | Legacy placeholder for dodge probability (0.0 = no evasion); the Phase-1 engine uses a hit-chance model rather than a separate evasion track. Constant removed in rev 0031. | Retired — had no effect. |
| `CLOAK_ACCURACY_PENALTY` | `float` | `0.0` | **Retired rev 0031** | — | Additive accuracy-penalty placeholder for cloaked target (0.0 = no effect); superseded by `CLOAK_SET_VALUE`. Constant removed in rev 0031. | Retired — had no effect. |
| `SCANNER_ACCURACY_BONUS` | `float` | `0.0` | **Retired rev 0031** | — | Flat accuracy-bonus placeholder for equipped scanner (0.0 = no bonus); superseded by tier-based `SCANNER_TIER_B_BONUS_PP` / `SCANNER_TIER_C_BONUS_PP`. Constant removed in rev 0031. | Retired — had no effect. |
| `THRUSTER_EVASION_BONUS` | `float` | `0.0` | **Retired rev 0031** | — | Evasion-bonus placeholder for thruster modules (0.0 = no bonus); superseded by `THRUSTER_ACCURACY_BONUS_FACTOR`. Constant removed in rev 0031. | Retired — had no effect. |
| `SHIELD_RECHARGE_RATE` | `float` | `0.0` | **Retired rev 0031** | — | Global shield-regeneration rate placeholder (0.0 = disabled); Phase-1 engine derives recharge from per-item `shield_recharge_ms`. Constant removed in rev 0031. | Retired — had no effect. |
| `REPAIR_BOT_HEAL_RATE` | `float` | `0.0` | **Retired rev 0031** | — | Repair-bot heal-rate placeholder (0.0 = disabled); superseded by `KETAR_I_REPAIR_PCT_PER_SEC` / `KETAR_II_REPAIR_PCT_PER_SEC`. Constant removed in rev 0031. | Retired — had no effect. |
| `BOOSTER_DPS_MULTIPLIER` | `float` | `1.0` | **Retired rev 0031** | — | DPS-multiplier placeholder while booster active (1.0 = neutral); superseded by Phase-1 booster mechanics. Constant removed in rev 0031. | Retired — had no effect. |
| `COMBAT_TICK_RATE` | `float` | `1.0` | **Retired rev 0031** | — | Tick-frequency-multiplier placeholder (1.0 = no change); Phase-1 resolver uses fixed 10 ms tick from `TICK_MS`. Constant removed in rev 0031. | Retired — had no effect. |
| `PERSISTENT_DAMAGE_DECAY_RATE` | `float` | `0.0` | **Retired rev 0031** | — | Cross-fight damage-persistence decay placeholder (0.0 = instant full heal); Phase-1 engine always starts at full HP. Constant removed in rev 0031. | Retired — had no effect. |

### Phase-1 Accuracy System (Per-Guild, Wiring Planned)

These constants govern the layered hit-chance formula defined in COMBAT_SPEC_LOCKED.md §5. Each has a corresponding `GuildConfig` column but `TickResolver.resolve()` currently reads `GameConstants` directly; per-guild resolution via `resolve_constant()` is planned.

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `CLOAK_SET_VALUE` | `float` | `0.25` | per-guild (wired, rev 0032) | `ge=0.05, le=0.99` (proposed) | While the target's cloak is active, the attacker's hit-chance is hard-set to this value, overriding the full §5 layered formula, then clamped to `[ACCURACY_CLAMP_MIN, ACCURACY_CLAMP_MAX]`; read as `_cloak_set` in `TickResolver.resolve()`. | Controls how hard it is to hit a cloaked ship — lower means cloaking is more effective (e.g. 0.05 = near-guaranteed miss). |
| `BOOSTER_ACCURACY_DEBUFF_FACTOR` | `float` | `0.10` | per-guild (wired, rev 0032) | `ge=0.0, le=1.0` (proposed) | Scales the active booster's `effect_pct` into a percentage-point accuracy debuff on the attacker (`debuff_pp = effect_pct × k_boost`); subtracted from hit-chance and clamped; read as `_k_boost` in `TickResolver.resolve()`. | Controls how much an active booster throws off enemy aim — higher makes boosting more disruptive to attackers. |
| `THRUSTER_ACCURACY_BONUS_FACTOR` | `float` | `0.10` | per-guild (wired, rev 0032) | `ge=0.0, le=1.0` (proposed) | Scales the equipped thruster's `effect_pct` into a close-range accuracy bonus (`bonus_pp = effect_pct × k_thruster × ramp`), where `ramp` rises linearly from 0 at `THRUSTER_WINDOW_M` to 1 at `MIN_DISTANCE_M`; read as `_k_thrust` in `TickResolver.resolve()`. | Controls how much thruster modules improve a ship's aim at close range — higher makes thruster modules more valuable in close-quarters fights. |
| `AUTO_TURRET_ACCURACY_MULTIPLIER` | `float` | `0.85` | per-guild (wired, rev 0032) | `ge=0.0, le=1.0` (proposed) | Multiplies the pilot's §5 accuracy (thruster bonus excluded) to produce auto-turret hit-chance (`auto_turret_acc = pilot_turret_acc × multiplier`), then clamped; read as `_auto_turret_multiplier` in `TickResolver.resolve()`. | Controls how accurately auto-turrets fire relative to the pilot — lower means turrets are a noticeably weaker supplement to main weapons. |
| `PLAYER_BASE_ACCURACY` | `float` | `0.60` | per-guild (wired, rev 0032) | `ge=0.0, le=1.0` (proposed) | Starting hit-chance fraction for player-side combatants before scanner, thruster, booster, and cloak modifiers are applied; read as `_player_base_acc` in `TickResolver.resolve()`. | Sets how accurate players are before any modules modify their aim — higher makes every player a better shot out of the box. |
| `NPC_BASE_ACCURACY` | `float` | `0.50` | per-guild (wired, rev 0032) | `ge=0.0, le=1.0` (proposed) | Starting hit-chance fraction for NPC/criminal combatants before modifiers; read as `_npc_base_acc` in `TickResolver.resolve()`. | Sets how accurate criminals are before any of their equipped modules apply — higher makes bounty fights harder across the board. |
| `SCANNER_TIER_B_BONUS_PP` | `int` | `5` | per-guild (wired, rev 0032) | `ge=0, le=50` (proposed) | Percentage-point accuracy bonus granted by a Tier-B scanner (Telta Quickscan / Ecoscan); passed as `tier_b_bonus_pp` to `resolve_scanner_tier()` in `_init_combatant()`, which returns a `ScannerTier`; also gates missile-tracking activation. | Sets the accuracy bonus from mid-tier scanner modules — higher makes scanners more valuable for hitting targets. |
| `SCANNER_TIER_C_BONUS_PP` | `int` | `10` | per-guild (wired, rev 0032) | `ge=0, le=50` (proposed) | Percentage-point accuracy bonus granted by a Tier-C scanner (Hiroto Proscan / Ultrascan); passed as `tier_c_bonus_pp` to `resolve_scanner_tier()` in `_init_combatant()`, which returns a `ScannerTier`; Tier-C also gates missile tracking (same threshold as Tier B). | Sets the accuracy bonus from top-tier scanner modules — higher makes high-end scanners noticeably more powerful than mid-tier ones. |

### Phase-1 Repair Bot (Per-Guild, Wiring Planned)

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `KETAR_I_REPAIR_PCT_PER_SEC` | `float` | `0.02` | per-guild planned (column exists; engine wiring planned) | `ge=0.0, le=1.0` (proposed) | Per-second hull+armour heal rate (fraction of combined max HP) for the Ketar Repair Bot I module; consumed in `loadout_builder.py` when building a ship's runtime module list: if the module is a `_REPAIR_BOT_MODULE_TYPE` and has no explicit `repair_pct_per_sec` seed key, falls back to this constant as the default Ketar I rate; the resulting `repair_rate` attribute is then read by `_init_combatant()` in `combat_resolver.py` and applied through an integer-flush accumulator each tick. COMBAT_SPEC_LOCKED.md §3 states 2.5 %/s; this constant is 0.02 (2 %/s) — confirm intended value before per-guild wiring lands. | Controls how fast the basic repair bot heals hull and armour during combat — higher means faster in-fight recovery. |
| `KETAR_II_REPAIR_PCT_PER_SEC` | `float` | `0.04` | per-guild planned (column exists; engine wiring planned) | `ge=0.0, le=1.0` (proposed) | Per-second hull+armour heal rate (fraction of combined max HP) for the Ketar Repair Bot II module; consumed in `loadout_builder.py` when building a ship's runtime module list: if the module is a `_REPAIR_BOT_MODULE_TYPE` and has no explicit `repair_pct_per_sec` seed key, falls back to this constant as the default Ketar II rate; the resulting `repair_rate` attribute is then read by `_init_combatant()` in `combat_resolver.py` and applied through an integer-flush accumulator each tick. COMBAT_SPEC_LOCKED.md §3 states 5.0 %/s; this constant is 0.04 (4 %/s) — confirm intended value before per-guild wiring lands. | Controls how fast the advanced repair bot heals hull and armour — higher means better combat survival for ships that equip it. |

### Phase-1 Distance Model (Per-Guild, Wiring Planned)

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `STARTING_DISTANCE_M` | `int` | `5000` | per-guild (wired, rev 0032) | `ge=300, le=50000` (proposed) | Initial separation between combatants at tick 0 (metres); also the distance restored after any shock-blast reset (`_shock_blast_apply()` in `combat_resolver.py`); read as the initial `current_distance` in `TickResolver.resolve()`. | Sets how far apart ships start when a fight begins — lower helps short-range weapons sooner; higher gives long-range ships more time to fire unopposed. |
| `BASE_SHIP_SPEED_MPS` | `int` | `150` | per-guild (wired, rev 0032) | `ge=1, le=5000` (proposed) | Base closing speed of each combatant (m/s); combined passive closure per tick is `2 × BASE_SHIP_SPEED_MPS × (tick_ms / 1000)`; read as `distance_delta` in `TickResolver.resolve()`. | Controls how fast ships close the distance every second — higher means fights reach close range quicker and long-range weapons have less time to dominate. |
| `MIN_DISTANCE_M` | `int` | `300` | per-guild (wired, rev 0032) | `ge=0, le=1000` (proposed) | Minimum combat separation floor (metres); passive closure stops here; the thruster accuracy ramp peaks here; read as `min_dist` in `TickResolver.resolve()`. | Sets the closest two ships can get — lower allows more extreme close-range bonuses; higher keeps fights at a comfortable minimum separation. |
| `THRUSTER_WINDOW_M` | `int` | `750` | per-guild (wired, rev 0032) | `ge=0, le=10000` (proposed) | Distance threshold (metres) at which the thruster accuracy ramp begins (`ramp = 0` at or beyond this, rising linearly to 1 at `MIN_DISTANCE_M`); read as `_thruster_window` in `TickResolver.resolve()`. | Controls at what range thruster modules start improving aim — a larger window means thrusters kick in earlier and across a wider stretch of the fight. |

### Phase-1 Emergency System & Nuke Basics (Per-Guild, Wiring Planned)

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `EMERGENCY_SYSTEM_INVULN_S` | `int` | `10` | per-guild (wired, rev 0032) | `ge=1, le=60` (proposed) | Duration of full invulnerability (seconds) granted when an Emergency System module fires; converted to milliseconds and passed to `_eval_emergency_system()` in `TickResolver.resolve()`. | Sets how long an Emergency System protects a ship from all damage after it triggers — lower makes the module less of a last-ditch lifesaver. |
| `NUKE_MAGNITUDE_SCALE` | `float` | `0.10` | per-guild (wired, rev 0032) | `ge=0.01, le=1.0` (proposed) | Converts a nuke's seed `magnitude_m` (10 000–40 000 m) to an effective blast radius at combat scale (`effective_magnitude = magnitude_m × scale`); applied per detonation in `TickResolver.resolve()`. | Controls how large nuke explosions are in combat — higher means blasts deal damage over a wider distance from the epicentre. |
| `NUKE_FRIENDLY_FACTOR` | `float` | `0.50` | per-guild (wired, rev 0032) | `ge=0.0, le=1.0` (proposed) | Fraction of nuke falloff damage applied to the firer (`self_damage = falloff_damage × friendly_factor`), also subject to `NUKE_STACK_FALLOFF` interference; read in `TickResolver.resolve()`. | Controls how much of a nuke's blast the shooter feels — lower makes nukes safer for the attacker; higher punishes reckless use. |

### Tick & Timing (Global, Column Being Dropped)

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `TICK_MS` | `int` | `10` | global (column being dropped) | — | Fixed simulation tick duration (milliseconds); all cooldowns, distances, and regen rates are multiples of this value; read as `tick_ms` in `TickResolver.resolve()` and as `_TICK_MS` in recap helpers in `combat_resolver.py`. | Internal combat speed setting — not adjustable per guild. |
| `MAX_FIGHT_TICKS` | `int` | `60000` | global (column being dropped) | — | Hard cap on ticks per fight (60 000 × 10 ms = 600 s); a fight that hits this limit ends without a winner; read as `max_ticks` in `TickResolver.resolve()`. | Internal fight-length limit — not adjustable per guild. |
| `ACCURACY_CLAMP_MIN` | `float` | `0.05` | global (column being dropped) | — | Absolute minimum hit-chance fraction (5 %); applied by `_clamp_accuracy()` in `combat_resolver.py` after every accuracy computation, ensuring no shot is guaranteed to miss. | Internal accuracy floor — not adjustable per guild. |
| `ACCURACY_CLAMP_MAX` | `float` | `0.99` | global (column being dropped) | — | Absolute maximum hit-chance fraction (99 %); applied by `_clamp_accuracy()` in `combat_resolver.py`, ensuring no weapon hits with absolute certainty. | Internal accuracy ceiling — not adjustable per guild. |
| `CLOAK_HP_THRESHOLDS_PCT` | `list[int]` | `[66, 33]` | global (column being dropped) | — | Descending list of hull-HP percentages at which a cloak module auto-activates; thresholds are re-armable after HP recovers above them; read as `_cloak_thresholds` in `TickResolver.resolve()`. | Internal cloak-trigger thresholds — not adjustable per guild. |
| `BOOSTER_HP_THRESHOLDS_PCT` | `list[int]` | `[80, 60, 40, 20]` | global (column being dropped) | — | Descending list of hull-HP percentages at which a booster module auto-activates; follows the same re-armable rule as cloaks; read as `_booster_thresholds` in `TickResolver.resolve()`. | Internal booster-trigger thresholds — not adjustable per guild. |

### Nuke & Shock-Blast Mechanics (Convert Planned)

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `NUKE_RANGE_REGIME_THRESHOLD_M` | `int` | `1000` | per-guild (wired, rev 0032) | `ge=0, le=10000` (proposed) | Distance boundary (metres) separating long-range from close-range nuke detonation windows in `_nuke_window()` (`combat_resolver.py`); above threshold the `NUKE_LR_NEAR_FRAC` window applies; at or below, the CR bracket applies. | Sets where nukes switch from a targeted far-shot pattern to a close-range artillery bracket — lower means the bracket applies across more of the fight. |
| `NUKE_LR_NEAR_FRAC` | `float` | `0.40` | per-guild (wired, rev 0032) | `ge=0.0, le=1.0` (proposed) | Long-range epicentre window near-edge as a fraction of current distance (`window = [NEAR_FRAC × d, d]`); controls how close the nearest possible epicentre can be to the firer when shooting long-range; used in `_nuke_window()`. | Controls long-range nuke self-risk — lower moves the near edge toward the firer, making nukes riskier to both sides at long distance. |
| `NUKE_CR_SHORT_M` | `int` | `600` | per-guild (wired, rev 0032) | `ge=0, le=5000` (proposed) | Close-range epicentre window short-edge offset (metres); window opens at `max(0, d − NUKE_CR_SHORT_M)`, letting the blast land short of the target; used in `_nuke_window()`. | Controls how far short of the target a close-range nuke can land — higher means wider spread toward the firer's own position. |
| `NUKE_CR_OVERSHOOT_M` | `int` | `400` | per-guild (wired, rev 0032) | `ge=0, le=5000` (proposed) | Close-range epicentre window far-edge offset (metres); window extends to `d + NUKE_CR_OVERSHOOT_M`, allowing overshoot past the target; used in `_nuke_window()`. | Controls how far past the target a close-range nuke can overshoot — higher means wider spread on the far side. |
| `NUKE_STACK_FALLOFF` | `float` | `0.5` | per-guild (wired, rev 0032) | `ge=0.0, le=1.0` (proposed) | Per-detonation yield interference multiplier (`stack_mult = falloff ** prior_detonations_this_side`); each successive nuke fired by one side multiplies total yield so stacking many nukes gives diminishing returns; applied in `TickResolver.resolve()`. | Controls how fast repeated nuke use loses impact — lower means loading many nukes gives far less extra damage than a single well-placed shot. |
| `SHOCK_BLAST_TRIGGER_RANGE_M` | `int` | `500` | per-guild (wired, rev 0032) | `ge=0, le=10000` (proposed) | Maximum range (metres) at which a shock-blast secondary will fire; beyond this range the weapon holds its cooldown to avoid wasting a round on a pointless distance reset; checked per tick in `TickResolver.resolve()`. | Sets how close ships must be before a shock-blast fires — lower means it only activates very close up; higher lets it push ships apart from further away. |

### Layer & Damage Mechanics (Convert Planned)

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `COMBAT_LAYER_REEMIT_FRACTION` | `float` | `0.25` | per-guild (wired, rev 0032) | `ge=0.0, le=1.0` (proposed) | Minimum fraction of a layer's maximum HP that must be recovered before a "layer depleted" event re-fires; prevents repeated event emission while a layer oscillates near zero; checked for the shield path in `combat_resolver.py` via `_reemit_frac`. | Controls how much a shield or armour layer must recharge before depletion is logged again — lower means it re-reports more readily after partial recovery. |

### PvC Damage Reduction (Per-Guild, Column Live)

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `PVC_DAMAGE_REDUCTION` | `float` | `0.33` | per-guild (column live; API exposure planned) | `ge=0.0, le=1.0` (proposed) | Fraction of each incoming damage event absorbed by the player-side combatant in PvC (bounty) fights only (`applied_damage = raw_damage × (1 − PVC_DAMAGE_REDUCTION)`), applied before shield → armour → hull stacking; stored in combat-log metadata by `combat_log_service.py` and resolved in the fight-service layer before `TickResolver` is called. | Controls how much of a criminal's damage a player absorbs in a bounty fight — lower means players take more damage; set to 0.0 to make PvC fights fully unmodified. |

### Combat Log Recap Denoising (Convert Planned)

These four constants tune the post-fight recap pipeline in `combat_recap.py`'s `build_recap_sections()`, which collapses repetitive tick-loop events into readable summaries for Discord output.

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `RECAP_COLLAPSE_MIN_RUN` | `int` | `3` | convert planned | `ge=1, le=100` (proposed) | Minimum total occurrences of a same-key cyclic event across the whole fight timeline before all occurrences are collapsed into a single aggregate "recurring" row; used as `collapse_min` in `build_recap_sections()`. | Sets how many times an event must repeat before it gets condensed into a summary line in fight recaps — lower groups more events; higher keeps more individual lines visible. |
| `RECAP_GAP_FILL_S` | `float` | `20.0` | convert planned | `ge=0.0, le=300.0` (proposed) | Maximum silence gap (seconds) between consecutive key events before a cyclic fill event is inserted near the midpoint to prevent blank stretches in the recap; used as `gap_fill_s` in `build_recap_sections()`. | Controls how long a recap can go without a notable event before a filler line is added — lower keeps recaps feeling more active during quiet moments. |
| `RECAP_NUKE_SUMMARY_MIN_COUNT` | `int` | `3` | convert planned | `ge=1, le=100` (proposed) | Minimum number of fires by a nuke or shock-blast weapon in a single fight before its low-impact detonations are grouped into a single summary line; used as `nuke_min` in `build_recap_sections()`. | Sets how many nuke or shock-blast shots are needed before minor ones get condensed into a summary — lower triggers the summary after fewer shots. |
| `RECAP_NUKE_SIGNIFICANCE_FRACTION` | `float` | `0.25` | convert planned | `ge=0.0, le=1.0` (proposed) | A nuke detonation is kept as its own recap line (not collapsed) if its opponent damage is at least this fraction of that weapon's best opponent damage in the fight; used as `nuke_frac` in `build_recap_sections()`. | Controls how impactful a nuke detonation must be to keep its own line — lower retains only the very best shots as standalone lines; higher keeps more detonations individually visible. |

### Combat Log Retention (Global-Infra, Operator-Only)

| Constant | Type | Default | Scope | Bounds | Technical | Admin-facing |
|---|---|---|---|---|---|---|
| `COMBAT_LOG_BOUNTY_RETENTION_HOURS` | `int` | `48` | global-infra (retention; operator-only) | — | Maximum age (hours) of PvC (bounty) combat-log rows before pruning by `execute_db_retention_job()` in `db_retention_executor.py`; overridable via `BOUNTYBOT_COMBAT_LOG_BOUNTY_RETENTION_HOURS`. | Operator setting — controls how long bounty fight logs are kept before automatic cleanup; set higher to retain history for longer. |
| `COMBAT_LOG_PVP_RETENTION_HOURS` | `int` | `8760` | global-infra (retention; operator-only) | — | Maximum age (hours) of PvP (duel) combat-log rows before pruning by `execute_db_retention_job()`; a value of `0` disables pruning entirely (permanent retention); overridable via `BOUNTYBOT_COMBAT_LOG_PVP_RETENTION_HOURS`. | Operator setting — controls how long duel fight logs are kept; set to 0 to keep duel logs permanently. |

## Conversion Cost & Refactor Units (plumbing trace, 2026-08-25)

This section records the results of a full call-graph plumbing trace against live code (branch `feat/override-gating`), classifying each constant by the difficulty of making it per-guild and grouping them into owner-approvable refactor units. Code is the sole source consulted — repo `.md` docs are known to have drifted.

The central question for every constant: **can a `GuildConfig` reach the point where the value is actually read?** The mechanism is `resolve_constant(guild_config, "field", GameConstants.X)` — it returns the override iff a `GuildConfig` object is in scope at the consumption point.

### Cost classes

| Class | Meaning |
|---|---|
| **trivial** | `GuildConfig` is already in scope at the read site — a one-line `resolve_constant` swap, a column-only addition, or list-membership exposure only |
| **modest** | Thread a param 1–2 call levels; caller already has cfg but the read site does not; ~10–100 LOC per unit |
| **significant** | Structural change required — the value crosses a process boundary (CPU pool) or sits deep in a module-level helper with no cfg path |

"Significant" units require explicit owner go/no-go before implementation begins.

### Per-constant cost table

All paths are `services/bot-core/src/` unless noted.

| Constant | Consumption chain | Guild-ctx at site? | Cost | Unit |
|---|---|---|---|---|
| MIN_DISTANCE_M | `TickResolver.resolve()` pre-loop bake — `guild_config` param exists but is never passed across `offload_cpu` | No | significant | A1 |
| BASE_SHIP_SPEED_MPS | same | No | significant | A1 |
| PLAYER_BASE_ACCURACY | same | No | significant | A1 |
| NPC_BASE_ACCURACY | same | No | significant | A1 |
| CLOAK_SET_VALUE | same | No | significant | A1 |
| AUTO_TURRET_ACCURACY_MULTIPLIER | same | No | significant | A1 |
| BOOSTER_ACCURACY_DEBUFF_FACTOR | same | No | significant | A1 |
| THRUSTER_ACCURACY_BONUS_FACTOR | same | No | significant | A1 |
| THRUSTER_WINDOW_M | same | No | significant | A1 |
| SCANNER_TIER_B_BONUS_PP | `_init_combatant():384` (module-level helper, no cfg) | No | significant | A1 |
| SCANNER_TIER_C_BONUS_PP | same | No | significant | A1 |
| STARTING_DISTANCE_M | `resolve():1410,2518` + `_shock_blast_apply():1143` (module-level) | Partial | significant | A1 |
| COMBAT_LAYER_REEMIT_FRACTION | `_tick_shield_regen():593`, `_tick_repair_bot_regen():662` (module-level) | No | significant | A1 |
| EMERGENCY_SYSTEM_INVULN_S | `resolve():2045-2046` (loop, struct in scope after refactor) | Partial | significant | A1 |
| NUKE_MAGNITUDE_SCALE | `resolve():1734` (loop) | Partial | significant | A1 |
| NUKE_FRIENDLY_FACTOR | `resolve():1743` (loop) | Partial | significant | A1 |
| NUKE_STACK_FALLOFF | `resolve():1737` (loop) | Partial | significant | A1 |
| SHOCK_BLAST_TRIGGER_RANGE_M | `resolve():1777` (loop) | Partial | significant | A1 |
| NUKE_RANGE_REGIME_THRESHOLD_M | `_nuke_window():1114` (module-level, no cfg) | No | significant | A1 |
| NUKE_LR_NEAR_FRAC | `_nuke_window():1115` | No | significant | A1 |
| NUKE_CR_SHORT_M | `_nuke_window():1117` | No | significant | A1 |
| NUKE_CR_OVERSHOOT_M | `_nuke_window():1118` | No | significant | A1 |
| KETAR_II_REPAIR_PCT_PER_SEC | `loadout_builder._module_stats_from_extra():113` (pre-fight, no cfg) | No | significant | A2 |
| KETAR_I_REPAIR_PCT_PER_SEC | same | No | significant | A2 |
| RECAP_COLLAPSE_MIN_RUN | `combat_recap.build_recap_sections():157` (no cfg param) | No | modest | B1 |
| RECAP_GAP_FILL_S | same | No | modest | B1 |
| RECAP_NUKE_SUMMARY_MIN_COUNT | same | No | modest | B1 |
| RECAP_NUKE_SIGNIFICANCE_FRACTION | same | No | modest | B1 |
| BRONZE_COMBAT_BONUS_BASE_MULT | `bounty_service._bronze_combat_bonus_fraction()` — callers have cfg, helper does not | No (helper) | modest | C |
| BRONZE_COMBAT_BONUS_PER_PRESTIGE | same | No (helper) | modest | C |
| BRONZE_COMBAT_BONUS_CAP | same | No (helper) | modest | C |
| CLASSIC_CREDITS_PER_CHECK | `game_maths.reward_per_sys_check():188` (floor) → `spawn_bounty():1898`; cfg adjacent in spawn_bounty | No (helper) | modest | D1 |
| MAX_ROUTE_LENGTH | `pathfinding_service.py:144` reads module literal `:25`, NOT `GameConstants` | No | modest | D2 |
| CRIMINAL_SECONDARY_MIN_DAMAGE | `generate_loadout():942`; `cfg` param in scope | Yes | trivial | D-trivial |
| SHOP_SECONDARY_QTY_SCALER_HEAVY | `refresh_shop():780-783`; `config` in scope | Yes | trivial | D-trivial |
| SHOP_SECONDARY_QTY_SCALER_STANDARD | same | Yes | trivial | D-trivial |
| 11 shop-TL knobs | already resolved per-guild via `resolve_constant`; column + schema exposure only | Yes | trivial | D-trivial |
| 5 expose-only orphans (waypoints ×4 + `pvc_damage_reduction`) | columns live / already resolved; API/slash exposure only | Yes | trivial | D-trivial |
| 4 slash-list additions | list membership only | n/a | trivial | D-trivial |

### Unit A1 — CombatTuning struct + engine/worker/preflight threading (SIGNIFICANT)

22 constants are stranded behind a deliberate `ProcessPoolExecutor` (forkserver) boundary in `combat_service.fight_ships`. All three fight entry points already hold guild context at the top level (`bounties.py:317-319`, `duel_service.accept_duel`, `combat_preflight_service.estimate`), but `fight_ships` passes `guild_config=None` to `run_fight` today — the reserved param and the C1a-4 ORM-boundary assert (`combat_service.py:303-305`, "guild_config must not be a live ORM model — extract scalar fields before offload") are pre-built scaffolding for this exact refactor.

Design sketch:
- New frozen, `slots=True` plain-scalar dataclass `CombatTuning` (int/float/bool only) in a DB-free leaf module (`combat_models.py` or new `combat_tuning.py`); add `CombatTuning.from_guild_config(cfg)` classmethod that calls `resolve_constant` for each field, returning a picklable struct.
- Build it once in `fight_ships` (satisfies the C1a-4 "extract scalar fields before offload" comment); pass as `tuning=<struct>` through `offload_cpu`. Extend the ORM-boundary assert to also reject a non-dataclass tuning.
- Thread `tuning` through `combat_worker.run_fight()` and `run_fight_batch()` (new `tuning=None` kwarg); wire into `resolver.resolve(tuning=…)`.
- In `resolve()`, replace the pre-loop bake block (`:1383-1404`) and all in-loop constant reads with `tuning.X`; pass the needed scalars into the five module-level helpers: `_init_combatant`, `_tick_shield_regen`, `_tick_repair_bot_regen`, `_nuke_window`, `_shock_blast_apply`. `tuning=None` falls back to `GameConstants` so all existing seeded tests pass unchanged.
- Entry-point wiring: `bounties.py:335` add `guild_config=guild_cfg` (already loaded); `duel_service.accept_duel` load and pass cfg.
- Mandatory preflight-parity step: `combat_preflight_service.estimate` builds a `CombatTuning` from its guild and passes `tuning=` into `offload_cpu(run_fight_batch, …)` (`:210`) — without this, win-rate previews simulate with default physics while real fights use per-guild physics (see preflight-parity bug note below).
- Schema: each exposed knob is a scalar column on `persist/models/guild_config.py` + a field in `api/schemas/config_schema.py` with `ge/le` bounds + one Alembic revision.

Estimated scale: ~7 code files + 1 migration; ~300–500 LOC (dominated by column/schema boilerplate if all 22 knobs get columns; the struct + threading is ~120 LOC and low-branching). Adding the struct + threading is the fixed one-time cost; each additional column afterward is cheap.

Risks:
- Determinism: LOW if `tuning=None` preserves `GameConstants` defaults; add a couple of tuning-override tests alongside.
- Picklability: struct must live in a DB-free leaf module; enforce via the existing `combat_worker` import discipline and extend the C1a-4 assert to `tuning`.
- Preflight/live consistency: mandatory — thread into `run_fight_batch` (see preflight-parity bug note).
- Column sprawl: 22 scalar columns is a wide surface; recommend landing a starter subset (e.g. `PLAYER_BASE_ACCURACY`, `NPC_BASE_ACCURACY`, nuke family) and deferring the rest to cheap follow-on column adds.
- Combat-log replay / blender-service: NULL risk — blender-service renders ship skins/textures and imports none of `combat_resolver`/`TickResolver`/`run_fight`/`FightResults`; the combat timeline is persisted as concrete events and never re-simulated.

**Recommendation: GO on the plumbing; owner picks the column subset.** The mechanism is low-risk and already scaffolded.

### Unit A2 — KETAR repair rates in the loadout-build path (SIGNIFICANT, small)

`KETAR_I/II_REPAIR_PCT_PER_SEC` are baked into `ModuleStats.repair_rate` at loadout-build time in `loadout_builder._module_stats_from_extra()` (`:113-115`), which runs pre-fight in the main process. `LoadoutBuilder.from_player`/`from_criminal_ship` take no `guild_config`; the builder already exposes a `repair_pct_per_sec` seed-key injection seam (`:108-110`).

Preferred design (if A1 lands): resolve the two rates in `CombatTuning` and apply the override in `_init_combatant` when reading `mod.repair_rate`, keeping the loadout builder cfg-free and folding A2 into A1 cleanly. Standalone alternative: thread `cfg` from `from_player`/`from_criminal_ship` down to `_module_stats_from_extra`.

Estimated scale: ~40–80 LOC, 1–2 files. Risks: touches the loadout-build path used by preflight and preview embeds; must not change picklability of `ShipLoadout`.

**Recommendation: LOW-PRIORITY-DEFER (or fold into A1).** Only 2 constants; awkward location; low gameplay leverage. Defer unless per-guild repair-bot tuning is specifically wanted.

### Unit B1 — Recap knobs (MODEST)

Four constants (`RECAP_COLLAPSE_MIN_RUN`, `RECAP_GAP_FILL_S`, `RECAP_NUKE_SUMMARY_MIN_COUNT`, `RECAP_NUKE_SIGNIFICANCE_FRACTION`) are read as local defaults in `combat_recap.build_recap_sections()` with no cfg param. Write path: `combat_log_service.persist()` is reached from `fight_ships` which already has `guild_config` (`:260`). Read path: `_get_detail_legacy_fallback():443` has the loaded `CombatLog` row carrying `guild_id` (`persist/models/combat_log.py:26`, non-nullable) — a `GuildConfig` is loadable there.

Design: add an optional cfg (or 4-field recap-tuning) param to `build_recap_sections`; thread it from `fight_ships` on the write path and load from `row.guild_id` on the read path. Recompute risk: if recap knobs change between fight time and view time, old-battle recaps recomputed in `_get_detail_legacy_fallback` will drift (cosmetic; legacy-fallback only).

Estimated scale: ~60–100 LOC, 3 files. **Recommendation: LOW-PRIORITY-DEFER.** These are display/summarisation thresholds with low per-guild value; the write path is latency-sensitive. Promote only if an admin explicitly wants per-guild recap formatting.

### Unit C — Bronze combat bonus helper (MODEST)

`_bronze_combat_bonus_fraction(prestige_count)` (`bounty_service.py:76-89`) reads all three `BRONZE_COMBAT_BONUS_*` constants directly. Both callers already hold cfg: `_process_single_bounty_check` (`:2296`, `cfg` param in scope) and `bounties.py:356` (`guild_cfg` loaded `:317-319`).

Design: add `cfg=None` to the helper; resolve the three constants via `resolve_constant` inside; both callers pass their cfg. One helper signature + two call sites.

Estimated scale: ~15–25 LOC, 2 files + 3 columns/schema + migration. Risks: negligible; pure function; defaults preserved. Note the catalog's flagged partial-wiring gap (Bronze XP path in `_award_combat_bonus` always reads the global) is a sibling worth closing in the same PR.

**Recommendation: GO.** Lowest-effort modest unit; both callers are cfg-ready.

### Unit D1 — CLASSIC_CREDITS_PER_CHECK floor (MODEST)

`reward_per_sys_check(tech_level, loadout_value)` (`game_maths.py:188`) floors the reward at `CLASSIC_CREDITS_PER_CHECK`; called as `_legacy_rps` in `bounty_service.spawn_bounty():1898`; `total_reward = _legacy_rps * len(route)` feeds the division-reward multiplier, winner-reserve split, and per-system consolation payout stored on the Bounty row (`:1899–1916`). `spawn_bounty` has `cfg` and already resolves other constants two lines below (`:1906,1911`).

Design: add `cfg=None` to `reward_per_sys_check`; resolve the floor via `resolve_constant`; thread `cfg` from the single caller (`:1898`).

Estimated scale: ~10–15 LOC, 2 files + 1 column/schema + migration. Risks: trivial; floor value, well-bounded.

**Recommendation: GO.** One caller; cfg already adjacent.

### Unit D2 — MAX_ROUTE_LENGTH literal removal + wiring (MODEST)

`pathfinding_service.py:144` reads a module literal `MAX_ROUTE_LENGTH = 50` (`:25`) that shadows `GameConstants.MAX_ROUTE_LENGTH`; the constant is registered as trackable (`:169,681`) but never read — any per-guild override set today silently no-ops. Callers: `spawn_bounty` (has cfg) → `_generate_route`/`_build_anchor_route` → the pathfinder (no cfg). Public `systems.py` route endpoints have no guild context and keep the `GameConstants` default.

Design: delete the module literal; thread an explicit hop-limit param from `spawn_bounty` (has cfg) → `_generate_route`/`_build_anchor_route` → the pathfinder (~2 levels). Systems.py endpoint passes the `GameConstants` default.

Estimated scale: ~30–50 LOC, 2–3 files + 1 column/schema + migration. Risks: pathfinder is shared by a guild-less public endpoint — pass the default there; don't force cfg where none exists.

**Recommendation: GO.** Also a correctness fix — the current per-guild override silently no-ops.

### Unit D-trivial — batch wire (23 constants)

- `CRIMINAL_SECONDARY_MIN_DAMAGE`: swap to `resolve_constant(cfg, …)`; `cfg` already in scope in `generate_loadout` (`:761`, read `:942`).
- `SHOP_SECONDARY_QTY_SCALER_HEAVY/_STANDARD`: swap to `resolve_constant(config, …)`; `config` loaded in `refresh_shop` (`:715`, read `:780-783`).
- 11 shop-TL knobs: consumption already calls `resolve_constant` per-guild; column + schema exposure only.
- 5 expose-only orphans (waypoints ×4 + `pvc_damage_reduction`): columns live and already resolved; API/slash exposure only.
- 4 slash-list additions: add to the `/admin_config_constants` allow-list only.

Estimated scale: one modest PR — a handful of `resolve_constant` swaps + column/schema adds + one migration. Risks: none structural; standard schema validation applies.

**Recommendation: GO.** Cheap, high-count win; good first PR to establish the column/schema pattern the other units reuse.

### Group E — DEFAULT_SCHEDULER_JOBS: seeded once on first boot

`BOUNTY_SPAWN_JITTER` and `BOUNTY_SPAWN_CHECK_INTERVAL_MINUTES` (renamed from `BOUNTY_DELAY_RANDOM_MIN` in rev 0031; env var: `BOUNTYBOT_BOUNTY_SPAWN_CHECK_INTERVAL_MINUTES`) feed `DEFAULT_SCHEDULER_JOBS` in `main.py`: cron `f"*/{GameConstants.BOUNTY_SPAWN_CHECK_INTERVAL_MINUTES} * * * *"` and `"jitter": GameConstants.BOUNTY_SPAWN_JITTER`. `register_default_jobs(scheduler)` fetches existing job IDs via `scheduler.get_jobs()` before registering; if a job ID is already present the loop skips with `continue` and **never reaches the trigger or jitter lines**. Jobs persist in Postgres via `SQLAlchemyJobStore(tablename="apscheduler_jobs")`; `scheduler.start()` loads persisted rows before `register_default_jobs` runs. Consequence:

- **First boot (empty table):** cron + jitter are baked from the current constant values into the persisted row.
- **Every later boot:** the rows exist — the seed block is skipped entirely. Changing the constant or env var and restarting has **no effect** on an existing deployment.

`PUT /jobs/{job_id}` (`scheduler.py:161-183`) modifies job args only, not cron/jitter. The **only** re-derive path is `POST /scheduler/reset` (`scheduler.py:231-250`), which calls `remove_all_jobs()` then re-registers from the current constants.

Implication: making `BOUNTY_SPAWN_JITTER` or `BOUNTY_SPAWN_CHECK_INTERVAL_MINUTES` per-guild via a column is meaningless while the scheduler is a single global APScheduler seeded once. Per-guild spawn cadence would require a different mechanism — reading cadence at job-run time from `GuildConfig` rather than baking it into the trigger at seed time. Both constants should remain `global-infra` / `owner-flag` with no columns until that mechanism exists.

### Preflight-parity bug (pre-existing)

Live PvC fights resolve the per-guild `pvc_damage_reduction` override via `resolve_constant(guild_cfg, "pvc_damage_reduction", …)` (`api/routers/bounties.py:320`), but `combat_preflight_service.run_fight_batch` passes the global default `pvc_damage_reduction=GameConstants.PVC_DAMAGE_REDUCTION` (`:213`). A guild that overrides PvC DR gets win-rate previews that do not match its actual fights.

This is a pre-existing bug, independent of A1, but the A1 preflight-parity step (threading `tuning` into `run_fight_batch`) is the natural fix. It also illustrates a general hazard: any per-guild combat knob will silently desync the preflight preview from the real fight unless explicitly threaded into `run_fight_batch`. The parity step is **mandatory** in Unit A1.
