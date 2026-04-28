# BountyBot Defects & Anomalies

Single source of truth for E2E-discovered defects. Add new entries at the **top** of the relevant status section.

**Severity**: 🔴 blocker · 🟠 high · 🟡 medium · 🔵 low · ℹ️ info
**Status**: open · deferred · fixed · fixed-pending-verify · closed · withdrawn

Cross-ref: `E2E_TEST_CHECKLIST.md` (test-item references). All commit SHAs are local to `samx-wip`.

---

## OPEN

### A.31 — `/list_category ... tech_level:N` always returns empty
🟡 medium · Phase 2.12 · 2026-04-22

`tech_level` filter on `/list_category` returns 0 results across module / primary_weapon / secondary_weapon / turret_weapon / criminal categories.

**Root cause**: cog preloads via `GET /about/categories/{category}/objects`, which only returns `id`/`name`/`aliases`/`emoji` (`bot-core/src/api/routers/about.py:102-109`). Cog filters client-side by `o.get("tech_level") == N` (`aboutCog.py:370`) — returns `None` for every row → always empty.

**Fix**: include `tech_level` in the preload response shape; update `test_aboutCog.py:1296` to match real shape (currently mocks the missing field). Affects all 5 categories that share the preload.

---

### A.30 — Gateway list endpoints return `category_id: null` on child channels
🔵 low · Phase 1.1 · 2026-04-22

`GET /guilds/{gid}/channels` and `GET /categories/{cat_id}/channels` return `category_id: null` for every BountyBot child channel. Single-fetch `GET /channels/{id}` returns the correct value. No runtime impact (internal callers use `guild_configs.*_channel_id`).

**Fix**: audit `ChannelConverter.channel_to_summary()` vs `channel_to_detail()` — likely missing `category_id` in the summary shape used by list endpoints.

---

### A.34 — `/ship` and `/nickname` styling/UX gaps
🔵 low · Phase 3.2/3.3 · 2026-04-22

Three sub-issues, same surface area:
- **a** — `/ship` autocomplete dropdown shows literal `🟢` prefix from `player_ships_autocomplete` (active-ship marker leaks into selection list)
- **b** — `/ship` detail embed style differs from `/loadout` (uses different builder); should delegate to `loadout_embed.build_loadout_embed()`
- **c** — `/nickname` autocomplete has same `🟢` leak as (a)

**Fix**: add `show_active_indicator: bool = False` parameter to `player_ships_autocomplete()`; refactor `/ship` detail handler to delegate to shared builder.

---

### A.32 — `Mp'zzzm Thrust` module renders alias `:mpzzzm:` instead of emoji
🔵 low · Phase 2.9 · 2026-04-22

Single-row data gap: 1 of 66 modules. Either upload `mpzzzm` emoji to guild or rewrite seed data alias. Check `bot-core/import_data/module/` for the entry.

---

### A.25 — `/unregister` on unconfigured guild shows generic error
🔵 low · Phase 0.5.3 · 2026-04-20

Alt account in unconfigured guild gets `⚠️ An error occurred while removing the role.` because `playerCog./unregister` does `GET /api/v1/config/guild/{id}` which returns 404 → broad `except` swallows.

**Fix**: catch `httpx.HTTPStatusError` with `status_code == 404`, treat as "not registered" → friendly message. Reuse `_is_guild_not_configured()` helper from shopCog/bountyCog.

---

### A.10 — Checklist 1.1 undercounts roles + channels
🔵 low · Phase 1.1 · 2026-04-21

Doc-only. `/admin_setup` creates 5 bounty roles (generic + Bronze/Silver/Gold/Platinum) and 8 artifacts (1 category + 7 channels including `#platinum-bounties`). Checklist undercounts. Also `#platinum-bounties` breaks naming convention (peer channels are `*-bounty-board`); decide rename or document asymmetry.

---

### Cosmetic batch B.1–B.7 (all 🔵 low, 2026-04-22 PM unless noted)

| ID | Surface | Issue |
|---|---|---|
| B.2 | Starter loadout | `player_ships.secondary_weapons` is `NULL` not `[]` on starter Betty (A.36 column not backfilled in `seed_loadout`). Harmless until equip code expects a list. |
| B.3 | `/ship` embed | Redundant `Type: Betty` field (Betty is the name, not a type). Should be `Class:` or omitted. |
| B.4 | `/equip` swap-flow | Swap-confirmation dropdown lacks clear "select to swap" affordance. Works, but discoverability gap. |
| B.7 | `/sell` error | "item not found" leaks numeric `player_id`: `"... not found in player 2's inventory"`. Should read `"... not found in your inventory"`. Phase 4.11. |

---

### O.1 — `/setactive` autocomplete dropdown empty (intermittent)
🔵 low · Phase 4.12 · 2026-04-27

Alt's `/setactive` autocomplete didn't populate; user typed "3" manually. Bot-core API returned both ships in ~6ms; gateway helper `player_ships_autocomplete` runs cleanly with no logged errors. Most likely Discord client cache or silent exception in helper's broad `except Exception → []`.

**Diagnostic logging added** in commit `439cd79` (per-exception log line). Re-test on next clean DB run; if still empty, log message will reveal the silent failure.

---

## DEFERRED

### A.18 — Shop tier randomization redesign (pseudo-banded cascade)
🟡 medium · Phase 1.1 · 2026-04-19

`ShopService.refresh_shop()` picks `shop_tech_level = random.randint(1, 9)` independent of tier. Bronze can stock tech-9 endgame gear with equal probability as Platinum. Combined with sparse turret seed data (10 turrets at TL 5/6/9 only), 90% of refreshes under-stock turrets. Algorithm silently skips when filter returns empty pool — no logging, no fallback.

**Design (agreed)**: pseudo-banded two-stage probability cascade.
- **Stage 1** — `tier → shop_tech_level`: per-tier probability matrix; Gaussian-like mode at tier depth (Bronze→TL1-2, Silver→TL4, Gold→TL7, Platinum→TL9), ≥0.5% tail outside band for rare cross-tier surprises.
- **Stage 2** — `shop_tech_level → item_tech_level`: existing logic at `_select_item_tech_level()` (70% same / 20% −1 / 10% −2). Optional refinement: add `+1` for true Gaussian.

**Required alongside redesign**:
- Empty-pool fallback cascade in `_get_random_item_by_tech_level()` — try requested TL, then ±1, then any TL within tier band, then any TL at all; log a WARNING when fallback triggers.
- Per-tier item-count defaults that reflect seed data realities.
- Validation logging at refresh time (counts vs config minimums).
- Fix NULL `techLevel` in `plasma_collectors.pe_fusion_h2.json`.

**Compound probability example**: Bronze TL9 item ≈ 0.005 × 0.70 = ~0.35% (rare but nonzero, matches design intent).

**Code refs**: `bot-core/src/services/shop_service.py:578` (refresh_shop), `:701` (_get_random_item_by_tech_level).

---

### A.39 — `/item` UX cleanup bundle
🔵 low · Phase 5.5 · 2026-04-22 · post-release

- **a** — Remove `item_type` parameter; resolve concrete type from `item_name` via Item STI (consistent with A.36/A.37/A.42 pattern). Cross-type collisions architecturally impossible (146 distinct names).
- **b** — Use per-item emoji in embed title (parity with `/inventory` and `/search`).

---

### A.41 — Guarantee ≥1 of each enabled category in initial shops
🔵 low · 2026-04-22

Initial shop generation can legitimately produce 0 turret_weapon rows (RNG variance). `shop_refresh` cycles show ~25% of tier-slots have turrets, statistically plausible but UX-confusing ("turrets don't work" reports).

**Fix**: at `/admin_setup` initial generation only, guarantee ≥1 of each `CURRENTLY_ENABLED_TYPES` per tier. Post-refresh remains probabilistic.

---

### A.24 — `/health` Schema subsection redesign
🔵 low · Phase 0.2 · 2026-04-20

`/health` Schema fields render as `Status: unknown / Current Version: N/A / blank`. Top-level health is correct; only the embed subsection is wrong.

**Root cause**: contract mismatch. `SchemaManager.get_schema_health_info()` returns 3 keys (`version`, `expected_version`, `version_match`); `healthCog.py:121-138` reads 6 keys (`status`, `current_version`, `expected_version`, `schema_table_exists`, `version_match`, `error`). Also: nothing tied to live Alembic state — only reads legacy `schema` table (single row `1.0.0`).

**Fix**: redesign to surface Alembic current revision + expected head + match status + optional ORM metadata drift. Align `healthCog` field consumption. Add response-schema contract test.

---

### A.23 — Test suite over-mocking + sub-threshold coverage audit
🟡 medium · cross-cutting · post-E2E

Many tests exceed project's "max 2 mocks per test" standard (`AGENTS.md`). Several files below coverage threshold. Cross-references NC-002 / NC-003 from prior sessions.

**Scope**: enumerate violators; refactor to real objects (reference: `test_combat_service.py`); decide per low-coverage file (add tests / document exemption / remove dead code).

**Prereq**: complete E2E manual pass first.

---

### A.20 — `/ping` visible to non-admins despite `default_permissions` decorators
🔵 low · Phase 1.5.6 · 2026-04-19, reconfirmed 2026-04-21

All other A.4 admin commands now hidden correctly; only `/ping` still leaks into Alt's autocomplete. Runtime `is_admin()` check still blocks execution (CheckFailure logged). `healthCog.py:25-32` has both decorators in the same pattern as working commands. Likely Discord client cache or per-command sync quirk.

---

## FIXED

| ID | Summary | Commit | Verified |
|---|---|---|---|
| **B.14-sibling** | Stale-respawn recovery sweep for `status='escaped'` past `respawn_time` | `815cd59` | ✅ live 2026-04-28 |
| **B.14** | Bounty/duel listing time filter + startup recovery sweep (12 stale bounties expired on first boot) | `db79c60` | ✅ live 2026-04-28 |
| **B.12** | `/check` processes ALL matching bounties on shared system (was: first-match-only via early returns at `bounty_service.py:1040,1096,1135`) | `ee81738` | pending |
| **B.15** | `/duel-challenge` ungraceful 500 on transient errors | `36f760e` | pending |
| **B.13** | `/check` recently-visited map drop + universal embed-image preservation via shared `preserve_embed_image()` helper | `46ac33a`, `edb8664` | pending |
| **B.11** | `/admin_give_ship` autocomplete | `46ac33a` | pending |
| **B.10** | `/admin_set_credits` old-value display | `46ac33a` | pending |
| **B.16** | `/admin_set_xp` old-tier display | `46ac33a` | pending |
| **B.8** | `/admin_refresh_shop` announcement (siblings `/admin_spawn_bounty` + `/admin_clear_bounties` empirically already worked — TODO comments removed) | `46ac33a`, `edb8664` | pending |
| **B.7** | `/sell`, `/equip`, `/unequip` error messages leaking numeric `player_id` | `439cd79` | pending |
| **B.6** | `/buy` embed `Item Type: Primary_Weapon` raw DB-concrete leak | `823c13d` | ✅ live 2026-04-27 |
| **B.5** | `/sell` embed `Item Type` raw leak; `c8b5fef` doubled-credits fix | `823c13d`, `c8b5fef` | pending |
| **A.48** | Bounty announcement embed Loadout field exceeded Discord 1024-char limit (24 of 1100 historical bounties affected at ~2.2%); silent post-failure | `72e3b31`, `d3ef0f9` | ✅ live 2026-04-28 |
| **A.47** | `/ships/transfer` Option Y transaction ownership | `3e73940` | pending |
| **A.46** | inventoryCog/adminCog/shopCog autocomplete choice values now concrete; display labels use `replace('_',' ').title()` | `3e73940` | pending |
| **A.45** | Inventory/admin request schemas use `Literal[concrete-types]` instead of regex pattern; 422 before service | `3e73940` | pending |
| **A.44** | `shop_service.sell_item` / `buy_ship` / `sell_ship` / `inventory.transfer_item_between_players` / `player.transfer_credits` drop `async with db.begin()` (router owns transaction); repo helpers threaded with `commit=False` | `3e73940` | pending |
| **A.43** | `InventorySummaryResponse` schema + router use concrete keys (no `weapon`/`turret` aliases) | `3e73940` | pending |
| **A.42 + D1** | `/sell` UX (drop `item_type`/`target_tier` params; resolve concrete type server-side); D1 `inventory_repository.get_inventory_summary()` initialized aggregation dict with generic alias keys → permanently 0 weapon/turret counts | `7351a1a` | pending |
| **A.38** | Secondary weapons leak into economy/loadout flows. `CURRENTLY_ENABLED_TYPES = {primary_weapon, turret_weapon, module, ship}` in `game_constants.py` | `3ad15b8` | pending |
| **A.37** | `/equip`, `/unequip` drop `equipment_type` param; new `player_equippable_autocomplete` + `player_equipped_autocomplete` helpers | `3ad15b8` | pending |
| **A.36** | Inventory API vocabulary mismatch (service `weapon`/`turret` aliases vs DB concrete `primary_weapon`/etc.); new `_item_type_normalizer.py`; reads expand aliases, writes require concrete via `InvalidItemTypeError → 422`; `admin.py:1026-1054` + `ships.py:612-620` write-site corruption fixed via STI lookup | `3ad15b8` | pending |
| **A.35** | `/inventory` `item_type` param now `app_commands.Choice` (matches `/item` pattern) | `3ad15b8` | pending |
| **A.33** | 404→422 for invalid item_type input; new `InvalidItemTypeError(ValueError)` mapped at router level on /inventory/add, /remove, /transfer | `3ad15b8` | pending |
| **A.29** | Numeric ID parameters lacked autocomplete on `/ship`, `/nickname`, `/item`. New `autocomplete_helpers.py` (`resolve_player_id`, `player_ships_autocomplete`, `player_inventory_autocomplete`); `ship_id` param type changed `int → str` | `f95b516` | pending |
| **A.28** | `GET /ships/{ship_id}` used `ShipRepository` (definition table) instead of `PlayerShipRepository` → 500 with `'Ship' object has no attribute 'player_id'`. 7 misused routes converted; 9 new regression tests with real ORM instances | `f95b516` | pending |
| **A.27** | `/list_category` truncated at 50 items but footer warned only above 100 | `65cbe5c` | pending |
| **A.26** | `/list_category` chunks list across fields all named `"Objects"` (continuation now uses `"\u200e"` zero-width spacer); `loadout_embed.py` moved `utils/ → cogs/_shared/` | `65cbe5c` | pending |
| **A.22** | Bounty spawns across all 4 tiers were synchronized (now per-tier randomized cadence) | (code-verified pre-rebuild) | pending |
| **A.21** | Shop refresh announcement posted to `#bounty-hunting` instead of `#shop`; role mention inside embed (didn't ping). Now posts to `shop_channel_id` with role mention in `text_content` | (code-verified pre-rebuild) | pending |
| **A.19** | Checklist referenced `/register` but command was `/profile`. Added `/register` alias delegating to shared `_display_profile()` handler | `65cbe5c` | pending |
| **A.16** | Postgres startup race on fresh DB volume: weak `pg_isready` healthcheck + no migration retry. Healthcheck strengthened to authenticated `psql SELECT 1`; 5x retry loop in `migration_manager.ensure_current()` | (verified 2026-04-21) | ✅ |
| **A.12** | Checklist Session Setup script contained A.9 platinum bug (tied to A.9 resolution) | `65cbe5c` | ✅ |
| **A.11** | Cleared bounties left zombie expire jobs in APScheduler. `BountyService.clear_bounties()` now cleans up both `bounty_expire` AND `bounty_respawn` orphan jobs by `bounty_id` payload match. **Orthogonal gap**: `BountyService.escape_bounty()` writes `bounty.respawn_time` but no code schedules a respawn job from it (separate issue, not yet investigated) | `65cbe5c` | pending |
| **A.9** | Bounty config validator rejected `platinum` in `max_bounties_per_tier`; spawner already produced platinum bounties (writer/reader disagreement) | `65cbe5c` | ✅ |
| **A.6** | Bounty spawn executor fired against un-setup guilds. Eligibility guard added | (code-verified) | pending |
| **A.5** | Help command set: new `/help` (user) + `/admin_help` (admin) commands | `helpCog.py` | ✅ live 2026-04-21 |
| **A.4** | Admin slash commands visible in non-admin autocomplete. Largely fixed; only `/ping` still leaks (tracked as A.20) | (decorator audit) | mostly ✅ |
| **A.3** | shopCog actively corrupted `users.discord_username` to `"temp"` on every invocation; cycled with `/profile`. Audit pass confirmed zero remaining offenders | (audit 2026-04-21) | ✅ |

---

## CLOSED / WITHDRAWN

| ID | Reason |
|---|---|
| **A.1** | Starter Betty + Nirai Impulse EX 1 + Micro Gun MK I cargo state matches authoritative spec (`player_service.py:104-121`). Not a bug. |
| **A.2** | Secondary weapons not yet implemented (planned: rockets/missiles/bombs, single-use). Their absence from starter loadout is correct. Stale `IMT Extract 1.3` reference in older checklist drafts. |
| **A.17** | "4 shops" wording in `/admin_setup` correctly refers to 4 tier-shop containers. Label is accurate. |
| **A.40** | "0 turrets across tiers" was RNG variance (5 forced refreshes showed normal distribution), not a systemic defect. Enhancement opportunity logged as A.41. |

---

*Last updated: 2026-04-28*
