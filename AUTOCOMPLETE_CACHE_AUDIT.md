# Autocomplete Cache Audit & Uniform Cold-Fill Refactor — Design / Scoping Doc

**Service:** `discord-gateway`
**Status:** SCOPING ONLY — no production code changed by this document.
**Author:** architect (Opus) · **Date:** 2026-06-09 · **Branch context:** `dev`
**Implementer:** a second architect implements directly from this doc; a tester adversarially verifies against it.

---

## 0. TL;DR — what changed vs the seed audit

The seed audit's heuristic scan had window-bleed errors (it warned us). After reading every handler:

- **Handler count:** 36 `async def *autocomplete*` symbols exist, but **one is not an autocomplete callback** — `devCog.reload_autocomplete` is the `/reload_autocomplete` *command* body (signature `(self, interaction)`, no `current`). **There are 35 real autocomplete callbacks across 11 cogs.**
- **Bucket A (already correct):** seed listed 3. **Reality: 13.** The seed missed that `inventoryCog.item/equip/give_ship` and `shipsCog.setactive/ship` delegate to `utils/autocomplete_helpers.py`, which **already** does the full two-gate `get_with_timeout(1.0)` cold-fill. They were mis-bucketed into C because the helpers retain (but ignore) `http_client`/`api_base` params.
- **Bucket B (peek+schedule → get_with_timeout):** seed listed 6. **Reality: 6 — confirmed correct.**
- **Bucket C (live HTTP, needs real cache work):** seed listed ~11. **Reality: 3** — `combatLogCog.battle`, `adminCog.admin_duel`, `adminCog.player_ship`. (And `player_ship` is fixable by *reusing* the existing `ships_cache`, no new cache class.)
- **Bucket D (static catalogs):** seed mostly right; corrected memberships below. The real work here is **self-heal `refresh_fn`** to kill the D-010 class bug.
- **Specific seed errors corrected:** `playerCog.tier` is **static** (`self._valid_tiers`), not a `/leaderboard` HTTP handler. `skinsCog.skinnable_ship` reads the cached `_ship_skins`, not live HTTP. `bountyCog.system` is the static `/check` catalog (the seed already self-corrected this). `devCog.reload` is not an autocomplete handler at all.

Net implementation surface: **6 B-swaps, 3 C-handlers (2 new caches + 1 cache reuse), ~6 D self-heals.**

---

## 1. Governing principle & the uniform gold-standard contract

> **Principle (product owner):** EVERY slash command with an autocomplete/dropdown parameter MUST be backed by a properly-scoped, actively-maintained in-memory cache. No exceptions — not even trivial or per-user dropdowns (explicitly: the per-user combat-log dropdown). The cache supplies *suggestions only*; the backend re-validates under lock and remains authoritative, so cache lag can never cause a double-action.

### 1.1 Why this shape (web-confirmed constraints)

- **Autocomplete cannot be deferred.** Unlike command interactions, an `APPLICATION_COMMAND_AUTOCOMPLETE` interaction has exactly **one** response and **no `defer()`**; you must respond within **3 seconds** or the dropdown soft-fails (shows an error in the client; the command itself still works). Sources:
  - discord.js Guide — *Autocomplete*: "autocomplete interactions must receive a response within 3 seconds. You cannot defer the response to an autocomplete interaction." <https://discordjs.guide/slash-commands/autocomplete>
  - Discord4J — *Auto Complete*: "autocomplete cannot be deferred and only has one way to respond. The bot must respond within 3 seconds, failure to do so will result in a 'soft failure' in the discord client." <https://docs.discord4j.com/interactions/auto-complete>
  - Sapphire — *Autocomplete*: "You cannot defer auto-completes … You must respond to the interaction within 3 seconds." <https://sapphirejs.dev/docs/Guide/interaction-handlers/autocomplete/>
  - Discord official — *Receiving and Responding*: "you must send an initial response within 3 seconds of receiving the event. If the 3 second deadline is exceeded, the token will be invalidated." <https://discord.com/developers/docs/interactions/receiving-and-responding>
- **Industry best practice == this codebase's pattern.** r/Discordjs (autocomplete >3s thread): "I ended up fetching all data on `ready` and using that cached data in the global scope for my autocomplete interactions, along with updating the data periodically (or on every DB update)." <https://www.reddit.com/r/Discordjs/comments/1f2krwl/> — i.e. pre-warm + periodic refresh + invalidate-on-mutation. That is exactly `autocomplete_state` + `autocomplete_warm` + the invalidate helpers.
- **Budget:** two sequential `get_with_timeout(…, timeout=1.0)` cold-fills (player-id gate + data gate) ≈ 2 s worst case, leaving ~1 s headroom inside the 3 s window. shopCog already ships this. **No handler may chain more than two 1.0 s cold-fills.** A third gate must be served from `peek` only (degrade-on-miss).

### 1.2 The gold-standard handler contract (copy-paste checklist)

For **every** autocomplete handler, the implementer must satisfy ALL of:

```
[ ] SCOPED KEY. The cache key matches the data's natural scope, keyed on
    IMMUTABLE IDs (Discord snowflakes / bot-core player_id), never display names:
      - guild-wide data         -> key = guild_id
      - per-(guild,user) data   -> key = (guild_id, discord_user_id)
      - per-(guild,player) data -> key = (guild_id, bot_core_player_id)
      - static catalog          -> key = "all" or a category/sub-key string
[ ] FAST PATH. First line of data access is a synchronous peek(key) — zero I/O,
    no lock. A warm cache must NEVER await.
[ ] COLD-FILL. On peek miss, do NOT `schedule_refresh(); return []`. Instead:
        val = cache.peek(key)
        if val is None:
            val = await cache.get_with_timeout(key, timeout=1.0)
        if val is None:
            return []   # truly cold + refresh slower than 1s; next keystroke warm
    The 0th keystroke on a cold cache MUST populate (slow, single fetch, never empty).
[ ] TWO-GATE COLD-FILL. If the handler resolves player_id first
    (player_cache) then reads a data cache, cold-fill BOTH gates with 1.0s each.
    Never cold-fill a THIRD gate (peek-only / degrade).
[ ] PRE-WARM REGISTERED. The cache's active keys are refreshed by a job in
    utils/autocomplete_warm.py (startup wave + recurring interval). New caches
    MUST be registered there.
[ ] INVALIDATE-ON-MUTATION. Every command success-path / backend event that
    changes the underlying data invalidates (or pushes to) the cache key.
    See the per-handler invalidation matrix (§2).
[ ] BACKEND-AUTHORITATIVE. The command body re-resolves/re-validates against
    bot-core under FOR UPDATE locks. Autocomplete output is a hint only.
[ ] DEGRADE SILENTLY. The whole body is wrapped so ANY exception returns [].
    Autocomplete has no user-visible error surface.
[ ] BUDGET. <= two 1.0s cold-fills; warm path is zero-await.
```

A handler is **compliant** iff every box is checked or a box is explicitly waived with a one-line technical justification recorded in this doc (uniformity is the default; divergence requires a citable reason).

---

## 2. Exhaustive per-handler table (35 callbacks, 11 cogs)

Legend — **Current pattern:** `cold-fill` = already uses `get_with_timeout`; `peek+sched` = the bug; `live-HTTP` = HTTP per keystroke; `static-peek` = synchronous static list/peek with no refresh_fn; `static-list` = plain Python list (not even an AutocompleteCache). **Bucket:** A/B/C/D per §0.

| # | Cog | Handler | Command(s) | Scope / cache key | Backend source / refresh_fn | Must-invalidate-on (matrix) | Current | Target | Phase |
|---|-----|---------|-----------|-------------------|-----------------------------|------------------------------|---------|--------|-------|
| 1 | shopCog | `buy_item_autocomplete` | `/buy` | player_cache `(g,u)` + `_shop_cache (g,tier)` | player `_refresh_player`; shop `_fetch_tier_shop` | `/buy`,`/sell` (shop+player+inv); shop-refresh push | cold-fill | **A (keep)** | — |
| 2 | shopCog | `sell_item_autocomplete` | `/sell` | player `(g,u)` + inventory `(g,pid)` + ships `(g,pid)` | player/inv/ships refresh_fns | `/buy`,`/sell`,`/equip`,`/unequip`,`/give`,`/setactive` | cold-fill | **A (keep)** | — |
| 3 | shopCog | `tier_autocomplete` | `/buy`(tier?) | static `_valid_tiers` (list) | none (enum) | never | static-list | **D (no refresh_fn needed)** | 3 |
| 4 | shopCog | `item_type_autocomplete` | `/shop` | static `_valid_item_types` (list) | none (enum) | never | static-list | **D (no refresh_fn needed)** | 3 |
| 5 | inventoryCog | `item_autocomplete` | `/item` | player `(g,u)` + inventory `(g,pid)` (via helper) | helper two-gate | `/buy`,`/sell`,`/equip`,`/unequip`,`/give` | cold-fill | **A (keep)** | — |
| 6 | inventoryCog | `equip_autocomplete` | `/equip` | player `(g,u)` + inventory `(g,pid)` (via helper) | helper two-gate | equip/unequip/buy/sell/give | cold-fill | **A (keep)** | — |
| 7 | inventoryCog | `unequip_autocomplete` | `/unequip` | player `(g,u)` + ships `(g,pid)` | player/ships refresh_fns | equip/unequip/setactive | cold-fill | **A (keep)** | — |
| 8 | inventoryCog | `give_item_autocomplete` | `/give item` | player `(g,u)` + inventory `(g,pid)` | player/inv refresh_fns | `/give`,`/buy`,`/sell`,`/equip`,`/unequip` | **peek+sched** | **B → cold-fill (or route via helper)** | 1 ✅ DONE |
| 9 | inventoryCog | `give_ship_autocomplete` | `/give ship` | player `(g,u)` + ships `(g,pid)` (via helper) | helper two-gate | give/setactive/buy-ship/sell-ship | cold-fill | **A (keep)** | — |
| 10 | shipsCog | `setactive_autocomplete` | `/setactive` | player `(g,u)` + ships `(g,pid)` (via helper) | helper two-gate | setactive/buy-ship/sell-ship/give-ship | cold-fill | **A (keep)** | — |
| 11 | shipsCog | `ship_autocomplete` | `/ship`,`/nickname` | player `(g,u)` + ships `(g,pid)` (via helper) | helper two-gate | setactive/nickname/buy-ship/sell-ship/give-ship | cold-fill | **A (keep)** | — |
| 12 | duelCog | `pending_duel_autocomplete` | `/duel-accept`,`/duel-reject` | player `(g,u)` + `_pending_duel_cache (g,pid)` | player refresh; duel push + `_fetch` refresh_fn | challenge/accept/reject/cancel/expire (push) | **peek+sched (both gates)** | **B → cold-fill both gates** | 1 ✅ DONE |
| 13 | duelCog | `outgoing_duel_autocomplete` | `/duel-cancel` | player `(g,u)` + `_outgoing_duel_cache (g,pid)` | player refresh; duel push + refresh_fn | challenge/accept/reject/cancel/expire (push) | **peek+sched (both gates)** | **B → cold-fill both gates** | 1 ✅ DONE |
| 14 | bountyCog | `system_autocomplete` | `/check` | static `_systems_cache "all"` (TTL=None, **no refresh_fn**) | preload `_preload_data` | never (catalog) | static-peek | **D + self-heal refresh_fn** | 3 |
| 15 | bountyCog | `bounty_autocomplete` | `/bounty` | `_bounty_cache (g)` primary + player `(g,u)` tier-filter | `_fetch_bounties`; player peek | spawn/expire/claim (push) | **peek+sched (primary gate)** | **B → cold-fill primary gate** | 1 ✅ DONE |
| 16 | schedulerCog | `job_id_autocomplete` | `/scheduler_remove` etc. | `_job_cache "all"` | `_fetch_jobs` | job add/remove/pause (+2min refresh) | **peek+sched** | **B → cold-fill** | 1 ✅ DONE |
| 17 | adminCog | `render_setting_autocomplete` | `/admin_render_config` | static `_render_settings` (list) | preload `_preload_render_settings` | never (catalog) | static-list | **D (no refresh_fn needed)** | 3 |
| 18 | adminCog | `tier_autocomplete` | admin tier cmds | static `_valid_tiers` (list) | none (enum) | never | static-list | **D (no refresh_fn needed)** | 3 |
| 19 | adminCog | `item_name_autocomplete` | `/admin_give_item` | `_item_catalog` per-category (TTL=None, **no refresh_fn**) | `_preload_static_catalogs` | never (catalog) | static-peek (via `.get`) | **D + self-heal refresh_fn** | 3 |
| 20 | adminCog | `remove_item_autocomplete` | `/admin_remove_item` | **target** player `(g,target_u)` + inventory `(g,target_pid)`; catalog fallback | player/inv refresh; catalog | `/buy`,`/sell`,`/equip`,`/unequip`,`/give`,`/admin_give_item`,`/admin_remove_item` (target player) | **peek+sched** | **B → cold-fill both gates (keep fallback)** | 1 ✅ DONE |
| 21 | adminCog | `game_ship_autocomplete` | `/admin_*ship*` | `_ship_catalog "all"` (TTL=None, **no refresh_fn**) | `_preload_static_catalogs` | never (catalog) | static-peek (via `.get`) | **D + self-heal refresh_fn** | 3 |
| 22 | adminCog | `player_ship_autocomplete` | `/admin_remove_ship` | **target** player `(g,target_u)` + **live GET /ships/player** | resolve helper (cold-fill) + **HTTP per keystroke** | target's ships: setactive/sell-ship/give-ship/admin-remove-ship | resolve cold-fill **+ live-HTTP** | **C → reuse `ships_cache (g,target_pid)`** | 2 ✅ DONE |
| 23 | adminCog | `constants_autocomplete` | `/admin_config_constants` | static `_GAME_CONSTANT_FIELDS` (tuple) | none (in-code) | never | static-list | **D (no refresh_fn needed)** | 3 |
| 24 | adminCog | `admin_duel_autocomplete` | `/admin_duel` | guild-scoped pending duels — **live GET /duels/pending-all** | **HTTP per keystroke** | challenge/accept/reject/cancel/admin-cancel/expire (guild) | **live-HTTP** | **C → NEW `_admin_pending_duel_cache (g)`** | 2 ✅ DONE |
| 25 | combatLogCog | `battle_autocomplete` | `/combat-log` | per-user — **live GET /combat-log** | **HTTP per keystroke** | fight finished for that user: PvC `/check`, PvP `/duel-accept` | **live-HTTP** | **C → NEW per-user `combatlog_cache (g,u)`** | 2 ✅ DONE |
| 26 | playerCog | `tier_autocomplete` | `/leaderboard` | static `_valid_tiers` (list) | none (enum) | never | static-list | **D (no refresh_fn needed)** | 3 |
| 27 | skinsCog | `ship_autocomplete` | `/ship_skin` | `_ship_skins.keys()` (TTL=None, **no refresh_fn**) | `_preload_ship_skins` | never (catalog) | static-peek | **D + self-heal (size-guard, see §4)** | 3 |
| 28 | skinsCog | `skin_autocomplete` | `/ship_skin` | `_ship_skins[ship]` keyed by ship name | `_preload_ship_skins` | never (catalog) | static-peek | **D + self-heal refresh_fn (per-ship)** | 3 |
| 29 | skinsCog | `skinnable_ship_autocomplete` | `/render_ship` etc. | `_ship_skins.keys()` + `_ship_render_info` dict | `_preload_ship_skins` | never (catalog) | static-peek | **D + self-heal (size-guard, see §4)** | 3 |
| 30 | aboutCog | `category_autocomplete` | `/about` | `_categories_cache "all"` (TTL=None, **no refresh_fn**) | `_preload_data` | never (catalog) | static-peek | **D + self-heal refresh_fn** | 3 |
| 31 | aboutCog | `system_autocomplete` | `/about`(legacy) | `_objects_cache "system"` | `_preload_data` | never (catalog) | static-peek | **D + self-heal refresh_fn** | 3 |
| 32 | aboutCog | `object_autocomplete` | `/about` | `_objects_cache[category]` keyed by category | `_preload_data` | never (catalog) | static-peek | **D + self-heal refresh_fn (per-category)** | 3 |
| 33 | devCog | `category_autocomplete` | `/load_data` | static `_categories` (list, preloaded) | `_preload_categories` | on `/load_data` schema change (rare) | static-list | **D + self-heal (size-guard reload)** | 3 |
| 34 | helpCog | `_user_category_autocomplete` | `/help` | in-code tuple `_USER_CATEGORY_ORDER` | none (in-code) | never | static-list | **D (no refresh_fn — see §6 Q4)** | 3 |
| 35 | helpCog | `_admin_category_autocomplete` | `/admin_help` | in-code tuple `_ADMIN_CATEGORY_ORDER` | none (in-code) | never | static-list | **D (no refresh_fn — see §6 Q4)** | 3 |

> **Not a handler (excluded from the 35):** `devCog.reload_autocomplete` — `/reload_autocomplete` command body.

### 2.1 Invalidation matrix (write-side — who drops which key)

This is the dual of the read table: the command/event success-paths that must call invalidate/push. Existing call sites already cover the shared loadout caches; the new entries (★) are added by this refactor.

| Mutation / event | Owner | Keys invalidated / pushed |
|---|---|---|
| `/buy` success | shopCog | `_shop_cache (g,tier)`, `player (g,u)`, `inventory (g,pid)`, [`ships (g,pid)` if ship] — *exists* |
| `/sell` / sell-ship success | shopCog | `_shop_cache (g,tier)`, `player (g,u)`, `inventory (g,pid)`, [`ships (g,pid)` if ship] — *exists* |
| `/equip`,`/unequip` success | inventoryCog | `inventory (g,pid)`, `ships (g,pid)` — *exists* |
| `/give` (item/ship/credits) success | inventoryCog | source+target `inventory`/`ships`/`player` — *exists; verify target keys* |
| `/setactive`,`/nickname` success | shipsCog | `ships (g,pid)` — *exists* |
| `/admin_give_item`,`/admin_remove_item` success | adminCog | ★ target `inventory (g,target_pid)`, `player (g,target_u)` |
| `/admin_remove_ship` success | adminCog | ★ target `ships (g,target_pid)` |
| shop tier refresh | bot-core → push `/internal/autocomplete/shop-cache/{g}/{tier}` | `_shop_cache (g,tier)` — *exists* |
| bounty spawn/expire | bot-core → push `/internal/autocomplete/bounty-cache/{g}` | `_bounty_cache (g)` — *exists* |
| duel challenge/accept/reject/cancel/expire | bot-core → push `/internal/autocomplete/duel-cache/{g}/{pid}` | `_pending/_outgoing_duel_cache (g,pid)` — *exists* |
| duel challenge/accept/reject/cancel/admin-cancel/expire | bot-core | ★ push/invalidate **guild-level** `_admin_pending_duel_cache (g)` |
| **fight finished (PvC `/check`, PvP `/duel-accept`)** | bot-core writes combat_log row | ★ push/invalidate per-user `combatlog_cache (g,u)` for BOTH combatants |
| `/scheduler_*` job add/remove/pause | schedulerCog / scheduler | `_job_cache "all"` (+2-min refresh) — *exists* |
| `/reload_autocomplete` | devCog | `clear()` all + preload; **static caches must self-heal via refresh_fn (§4)** |

---

## 3. New-cache designs for Bucket C

Only **3** handlers are genuine C, and one of them reuses an existing cache.

### 3.1 `combatLogCog` — NEW per-user combat-log cache (`/combat-log`)

The flagship per-user case the product owner named explicitly.

```python
# combatLogCog.__init__
self._combatlog_cache: AutocompleteCache[tuple[int, int], list[dict]] = AutocompleteCache(
    ttl_seconds=float(os.getenv("AUTOCOMPLETE_COMBATLOG_TTL_SECONDS", "120")),  # dead-man switch
    refresh_fn=self._fetch_combat_log,
    name="combatlog",
    max_entries=int(os.getenv("AUTOCOMPLETE_COMBATLOG_MAX_ENTRIES", "2000")),   # per-user → MUST bound (LRU)
)

async def _fetch_combat_log(self, key: tuple[int, int]) -> list[dict]:
    guild_id, user_id = key
    resp = await self.http_client.get(
        f"{api_base}/combat-log",
        params={"user_id": user_id, "guild_id": guild_id, "limit": 25},
        timeout=3.0,
    )
    resp.raise_for_status()
    items = resp.json()
    for it in items:               # Phase-7 style: pre-compute _norm at fill time
        it["_norm"] = (_make_choice_label(it)).lower()
    return items
```

- **Scope key:** `(guild_id, discord_user_id)` — the listing endpoint keys on the invoker's Discord id directly (no player-id resolution; single gate, so a single 1.0 s cold-fill is well within budget).
- **`battle_autocomplete` rewrite:** `peek` → `get_with_timeout((g,u), 1.0)` → build choices from `_make_choice_label` filtering on `current`. Drop the inline GET.
- **`max_entries` LRU is mandatory** here because key cardinality = every user who ever fought. Sizing: 2000 entries × ~25 small dicts is a few MB; tune via env. (See §6 Q2.)
- **Warm registration:** add `warm_guild_combatlog_caches(bot, guild_id)` (Stage-2, fires after player_cache warm, semaphore-gated) iterating warmed `(g,u)` keys → `cache.get((g,u))`; plus a recurring `refresh_combatlog_round_robin` (interval ~5 min) over current keys. Register both in `register_warm_jobs`.

**Consistency mechanism — DECISION: bot-core push-to-invalidate + short-TTL dead-man switch (NOT full-list push).**

Justification:
1. The mutation (a finished fight writing a `combat_log` row) is **bot-core-owned**. The gateway has no reliable success-path hook for PvP — `/duel-accept` resolves combat inside bot-core; even PvC `/check` writes the row server-side. So the gateway **cannot** self-invalidate on fight completion. A bot-core push is required.
2. Pushing the full 25-row list per user after every fight (like shop/bounty/duel) is wasteful here because the key space is huge and per-user, and two combatants' lists change per fight. A **lightweight invalidate push** (drop the key; next keystroke cold-fills) is cheaper and bounded by LRU.
3. The short TTL (120 s) is a dead-man switch: if a push is ever missed, the stale list self-corrects within 2 minutes. Combat-log is a read-only history view, so brief staleness is harmless (backend re-validates combatant membership on `/combat-log/{id}` anyway — the detail fetch 404s if you weren't in the fight).

```
NEW gateway endpoint:  POST /internal/autocomplete/combatlog-cache/{guild_id}/{user_id}   (204, invalidate only)
  -> CombatLogCog._combatlog_cache.invalidate((guild_id, user_id))
NEW bot-core call site: combat resolution finalizer (both PvC and PvP), once per combatant
  -> POST the invalidate for each of (guild, c1_discord_id) and (guild, c2_discord_id)
```

This mirrors the existing `internal_autocomplete.py` shape (X-Internal-Auth, graceful 503/no-op if cog absent). Schema: reuse a trivial empty body or add `CombatLogCacheInvalidate` (no payload fields needed).

### 3.2 `adminCog.admin_duel` — NEW guild-scoped pending-duel cache (`/admin_duel`)

```python
# adminCog.__init__
self._admin_pending_duel_cache: AutocompleteCache[int, list[dict]] = AutocompleteCache(
    ttl_seconds=float(os.getenv("AUTOCOMPLETE_ADMIN_DUEL_TTL_SECONDS", "300")),
    refresh_fn=self._fetch_admin_pending_duels,
    name="adminCog-pending-duels",
)

async def _fetch_admin_pending_duels(self, guild_id: int) -> list[dict]:
    resp = await self.http_client.get(
        f"{api_base}/duels/pending-all", params={"guild_id": guild_id}, timeout=3.0
    )
    resp.raise_for_status()
    return resp.json()
```

- **Scope key:** `guild_id` (guild-wide list of all pending duels — this is the admin view, distinct from the per-player `_pending_duel_cache` in DuelCog).
- **`admin_duel_autocomplete` rewrite:** keep the "⚠️ Cancel ALL" sentinel first; then `peek(g)` → `get_with_timeout(g, 1.0)` → build choices. Drop the inline GET.
- **Consistency:** **push from the SAME bot-core duel mutation events** that already fire `/duel-cache/{g}/{pid}`. Add a guild-level push `POST /internal/autocomplete/admin-duel-cache/{guild_id}` (full list — guild-level cardinality is low, so a list push is fine here) OR a simple invalidate. **DECISION: invalidate-and-cold-fill** (simplest; the existing per-player duel push already exists, and admin_duel is low-traffic) backed by the 300 s TTL dead-man switch and the recurring refresh job. Justify the divergence from the per-player *push* model: admin_duel is rare/low-traffic, so cold-fill latency is acceptable and avoids a second payload shape.
- **Warm registration:** add to the existing `warm_guild_duel_caches` / `refresh_duel_caches` jobs — one extra `cog._admin_pending_duel_cache.get(guild_id)` per guild.

### 3.3 `adminCog.player_ship` — REUSE existing `ships_cache` (no new cache)

`player_ship_autocomplete` already resolves the target player via `resolve_player_id` (cold-fill, good) but then does a **live `GET /ships/player/{player_id}`** per keystroke. The shared `autocomplete_state.ships_cache` is keyed `(guild_id, player_id)` and is already warmed for active players. Reuse it:

```python
# inside player_ship_autocomplete, target_user resolved -> player_id:
sc = autocomplete_state.ships_cache
ships_nc = sc.peek((guild_id, player_id)) if sc else None
if ships_nc is None and sc is not None:
    ships_nc = await sc.get_with_timeout((guild_id, player_id), timeout=1.0)
if ships_nc is not None:
    # build choices from nc.raw ship_name; fall through to game-data catalog on None
```

- This is the **second** gate; `resolve_player_id` is the first. Two 1.0 s cold-fills, within budget.
- **No new cache, no new warm job, no new invalidation** — `ships_cache` is already invalidated by setactive/sell-ship/give-ship and refreshed by `refresh_loadouts_round_robin`. The only addition: ★ `invalidate_ships` on `/admin_remove_ship` success (matrix row).
- **Fallback preserved:** when the target player isn't in cache (e.g. never warmed / guild not configured), fall through to the existing `_ship_catalog` game-data fallback. Document this as the intended degrade path (not every guild member is warmed).

---

## 4. Static-catalog self-heal (Bucket D) — kill the D-010 class bug

**Root cause of D-010:** static caches (`aboutCog._categories_cache/_objects_cache`, `bountyCog._systems_cache`, `adminCog._item_catalog/_ship_catalog`, `skinsCog._ship_skins`) are constructed with `ttl_seconds=None, refresh_fn=None` and populated once via `create_task(self._preload_*())`. Because `get()` with no `refresh_fn` returns `None` on a missing key, a `clear()` (from `/reload_autocomplete`) leaves them **permanently empty** until cog reload. The current band-aid is the explicit "do NOT clear `_systems_cache`" carve-out in `devCog.reload_autocomplete` (lines ~177-180). That is fragile and per-cache.

**Fix — give every static AutocompleteCache a `refresh_fn` that re-runs the same per-key loader the preload uses.** Then a cleared key lazily re-fills on the next `get`/`get_with_timeout`, and `/reload_autocomplete` can simply `clear()` uniformly — the carve-out is deleted.

Pattern (refactor `_preload_*` into a per-key fetch reused as `refresh_fn`):

```python
# bountyCog — systems
async def _fetch_systems(self, _key: str) -> list[str]:
    resp = await self.http_client.get(f"{api_base}/.../systems", timeout=3.0)
    resp.raise_for_status()
    return [s["name"] for s in resp.json()]

self._systems_cache = AutocompleteCache(ttl_seconds=None, refresh_fn=self._fetch_systems, name="bounty-systems")
# _preload_data calls self._systems_cache.get("all"); handler uses get_with_timeout("all", 1.0) on peek-miss.
```

Apply the same shape to:
- `aboutCog._categories_cache` (`refresh_fn(_)` → categories), `_objects_cache` (`refresh_fn(category)` → that category's objects; `"system"` is just one category key).
- `adminCog._item_catalog` (`refresh_fn(category)` → that item-type's names), `_ship_catalog` (`refresh_fn("all")`).
- `skinsCog._ship_skins` for the **keyed** lookup `skin_autocomplete` (`refresh_fn(ship_name)` → that ship's skins).

**Justified divergence — `keys()`-enumerating handlers can't be lazily filled by a per-key refresh_fn.** `skinsCog.ship_autocomplete`, `skinsCog.skinnable_ship_autocomplete`, `aboutCog.category_autocomplete`, and `devCog.category_autocomplete` enumerate the *full key set* (`.keys()` or a preloaded list). A per-key `refresh_fn` cannot repopulate an empty key set because there is no key to fetch. Self-heal these with a **handler-level size guard** instead:

```python
if self._ship_skins.size == 0:
    await self._preload_ship_skins()   # idempotent; re-runs the bulk loader
```

Wrap the guard in try/except (autocomplete must never raise) and treat the preload as cheap-enough to run inline within the 3 s budget on the rare empty-cache path (it is only empty immediately after `/reload_autocomplete` or a failed startup preload). Document this as the canonical self-heal for enumeration handlers.

**Plain static lists (no AutocompleteCache):** `shopCog._valid_tiers/_valid_item_types`, `adminCog._render_settings/_valid_tiers/_GAME_CONSTANT_FIELDS`, `playerCog._valid_tiers`, `helpCog` tuples. These are never blanked by `clear()` and never change at runtime → **no refresh_fn needed** (enum/in-code constants). This is a deliberate, citable divergence from "every handler backed by a cache": a frozen in-process enum *is* the cache; adding a backend round-trip would be pure overhead. (Open question §6 Q4 confirms with the owner.)

**Post-fix:** delete the `/reload_autocomplete` D-010 carve-out and let `clear()` apply uniformly to `_systems_cache` (and friends); the next `/check` keystroke cold-fills.

---

## 5. Phased implementation plan + test strategy

Sequence: **B (low-risk swaps) → C (new caches) → D (self-heal).** One subagent at a time (sequential). Each phase ends green on the canonical fixed-order pytest gate run from `services/discord-gateway`.

### Test harness conventions (all phases)
- Run on **host**, from `services/discord-gateway`: `python -m pytest tests/ -p no:randomly -q | tee /tmp/dg_<phase>.log` then grep the log (never rerun to change a grep). Gate = CI-order green; a phase owns only NEW order-couplings it introduces.
- Mirror existing suites: `tests/.../test_autocomplete_cache.py` (primitive), `test_shopCog.py`, `test_inventoryCog.py`, `test_duelCog.py`, etc.
- Real objects preferred, ≤2 mocks. The cold-fill assertions exploit the existing `_monotonic` injection + a stub `refresh_fn` (real `AutocompleteCache`, no mocking of the primitive).

### Phase 1 — Bucket B swaps (6 handlers) · owner: **developer** subagent
Files: `duelCog.py` (#12,#13), `bountyCog.py` (#15), `schedulerCog.py` (#16), `adminCog.py` (#20 remove_item), `inventoryCog.py` (#8 give_item).
- Mechanical: replace `val = cache.peek(k); if val is None: cache.schedule_refresh(k); return []` with the cold-fill block (§1.2). Two-gate handlers cold-fill BOTH gates (#8, #12, #13, #20). bounty (#15) cold-fills the primary `_bounty_cache` gate; the player tier-filter stays peek-only degrade (third-gate rule). For #8 give_item, **prefer routing through a shared inventory helper** (uniformity with give_ship/item which already delegate) — if low-risk, otherwise inline cold-fill.
- **Tests (assert per handler):**
  - *cold-fill populates 0th keystroke:* cache empty + stub refresh_fn returning N rows → first `await handler(current="")` returns the rows (not `[]`).
  - *warm path zero-await:* pre-`set` the key → handler returns rows; assert refresh_fn call-count stays 0 (peek only).
  - *timeout degrade:* refresh_fn sleeps > 1.0 s → handler returns `[]` and does NOT raise; a subsequent peek (after the shielded refresh lands) is warm (proves `shield` semantics).
  - *both-gate cold-fill:* player_cache cold + data cache cold → one fetch each, rows returned.
  - *budget:* assert total awaited time ≤ ~2.1 s with both gates timing out (use injected clock / fake refresh, not real sleep where possible).

### Phase 2 — Bucket C new caches (3 handlers) · owner: **developer**, then **tester** QA
Files: `combatLogCog.py` (#25 + `_combatlog_cache` + `_fetch_combat_log`), `adminCog.py` (#24 + `_admin_pending_duel_cache`; #22 reuse ships_cache), `api/routers/internal_autocomplete.py` (+combatlog invalidate endpoint; +optional admin-duel push), `api/schemas/internal_schemas.py` (+ invalidate schema if needed), `utils/autocomplete_warm.py` (+combatlog warm/refresh jobs; +admin-duel warm), bot-core combat finalizer + duel mutation paths (cross-service ★ push call sites).
- **Cross-service contract check (architect verifies):** confirm the bot-core combat finalizer fires for BOTH PvC and PvP and has both combatants' Discord ids + guild id at that point; confirm `X-Internal-Auth` wiring. This is the one place a wrong call costs weeks — validate against live containers via the Docker exec curl patterns before merge.
- **Tests:**
  - combatlog: cold-fill on first keystroke; LRU eviction at `max_entries`; invalidate-endpoint drops the key (next keystroke cold-fills); detail fetch still 404s for non-combatants (backend-authoritative — unchanged).
  - admin_duel: "Cancel ALL" sentinel always first; cold-fill; TTL dead-man self-correct.
  - player_ship: warm ships_cache hit → no HTTP; cold target → cold-fill; uncached target → catalog fallback (assert fallback path, not empty).
  - internal router: 204 on push/invalidate; 401 on bad auth; graceful no-op when cog absent (mirror existing duel/bounty tests).

### Phase 3 — Bucket D self-heal (~6 caches + size-guards) · owner: **developer**
Files: `bountyCog.py` (#14), `aboutCog.py` (#30,#31,#32), `adminCog.py` (#19,#21), `skinsCog.py` (#27,#28,#29), `devCog.py` (#33, + remove the `/reload_autocomplete` carve-out).
- Refactor `_preload_*` into per-key fetchers reused as `refresh_fn`; add size-guard self-heal to enumeration handlers; delete the D-010 carve-out.
- **Tests:**
  - *D-010 regression (the headline test):* preload `_systems_cache`, call the equivalent of `clear()`, then `await system_autocomplete(current="")` → returns the systems again (proves self-heal). Repeat for `_item_catalog`, `_ship_catalog`, `_objects_cache`.
  - *size-guard:* clear `_ship_skins` → `skinnable_ship_autocomplete` re-preloads and returns names (assert size>0 after).
  - *plain-enum handlers:* assert they filter correctly and never hit HTTP (no refresh_fn) — codifies the §6 Q4 divergence.
  - *`/reload_autocomplete` end-to-end:* clear_all + preload, then every static dropdown is non-empty on next call.

### Regression / invariant guard (all phases, loadout subsystem)
The loadout/inventory subsystem is historically fragile (owned = cargo + equipped; unequip-before-sell). Any change touching inventory/ships caches or their invalidation MUST keep the existing `test_inventoryCog`/`test_shopCog` loadout-invariant tests green and add: invalidate-then-cold-fill round-trip after `/equip`,`/unequip`,`/sell-ship`,`/give` reflects the correct cargo vs equipped counts. Test exhaustively — this is the area most likely to silently break.

---

## 6. Open design questions for the product owner

1. **combat-log consistency — push-to-invalidate vs full-list push?** This doc chose **invalidate + short-TTL dead-man** (per-user high cardinality, bot-core-owned mutation). If you want the *next* keystroke to be guaranteed warm (zero cold-fill even right after a fight), we'd instead push the full 25-row list (heavier, two combatants per fight). Recommendation: start with invalidate; revisit only if the 1.0 s post-fight cold-fill is user-visible.
2. **Per-user cache `max_entries` sizing.** Proposed `combatlog_cache` LRU = 2000, `AUTOCOMPLETE_COMBATLOG_TTL=120 s`. Comfortable for the dev guild; if you anticipate many large guilds, do we raise the LRU cap (memory) or shorten TTL (more cold-fills)? Same question generalizes to any future per-user cache.
3. **admin_duel — invalidate vs join the existing duel push.** Chosen: invalidate + 300 s TTL (low traffic). Alternatively push the guild list from the same duel events for instant freshness at the cost of a second payload shape. Acceptable as-is?
4. **Do static enums truly need a backend `refresh_fn`, or is startup preload / in-code constant sufficient?** The governing principle says "every handler backed by a cache." Plain enums (tiers, item types, help categories, render settings, game-constant field names) are frozen in-process constants — a backend round-trip adds latency with zero correctness benefit. This doc treats the frozen in-process list **as** the cache (documented divergence). Confirm that satisfies the principle, or specify which enums you want promoted to backend-refreshed caches anyway (uniformity-at-all-costs reading).
5. **`/give` target-key invalidation completeness.** The matrix flags ★ verification that `/give` invalidates the *recipient's* inventory/ships/player keys (not just the giver's). Confirm we should add recipient-side invalidation if the current code only drops the giver's keys (likely a latent staleness bug for the recipient's next autocomplete).

---

## Appendix A — files touched by phase (for the implementer)

- **Phase 1:** `src/cogs/duelCog.py`, `bountyCog.py`, `schedulerCog.py`, `adminCog.py`, `inventoryCog.py` (+ `utils/autocomplete_helpers.py` if give_item is routed through a helper).
- **Phase 2:** `src/cogs/combatLogCog.py`, `src/cogs/adminCog.py`, `src/api/routers/internal_autocomplete.py`, `src/api/schemas/internal_schemas.py`, `src/utils/autocomplete_warm.py`, + bot-core combat finalizer & duel mutation call sites (cross-service).
- **Phase 3:** `src/cogs/bountyCog.py`, `aboutCog.py`, `adminCog.py`, `skinsCog.py`, `devCog.py`.
- **Infra (unchanged, referenced):** `src/cogs/_shared/autocomplete_cache.py`, `src/utils/autocomplete_state.py`.
- **Tests:** mirror under `services/discord-gateway/tests/...` per existing `test_*Cog.py` / `test_autocomplete_cache.py` layout.
