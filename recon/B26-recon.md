# B26-recon.md — Autocomplete Preload + Cache Framework (Static + Shop-Cached Data)

**Recon scope**: Broadened from original B.26 (only `/check system:` and `/buy item_id:`) to cover every autocomplete handler in the discord-gateway service.  
**Method**: Read-only source inspection of HEAD. All file:line references verified against current codebase.  
**Date**: 2026-04-28

---

## 1. Handler-by-Handler Breakdown

### 1.1 ALREADY CORRECT — Preloaded at Startup

These handlers serve from in-memory cache populated by `bot.loop.create_task(self._preload_*())` in `__init__`. No HTTP call on keystrokes. ✅

---

#### `aboutCog.py` — `category_autocomplete` (line 96)
- **Parameter**: `category` on `/about`, `/list_category`
- **Data source**: `self._categories` — populated by `GET /about/categories` in `_preload_data()` (line 47)
- **Preload hook**: `bot.loop.create_task(self._preload_data())` at line 35
- **Cache shape**: `list[str]` — category name strings
- **Classification**: STATIC — category list changes only on schema update

---

#### `aboutCog.py` — `system_autocomplete` (line 108)
- **Parameter**: `start`, `end` on `/make-route`
- **Data source**: `self._objects_by_category.get("system", [])` — populated by per-category `GET /about/categories/system/objects` in `_preload_data()` (line 55)
- **Preload hook**: same `_preload_data()` as above
- **Cache shape**: `dict[str, list[dict]]` keyed by category name
- **Classification**: STATIC — system catalog is seed data, changes only on re-seed

---

#### `aboutCog.py` — `object_autocomplete` (line 121)
- **Parameter**: `name` on `/about`
- **Data source**: `self._objects_by_category[category]` — full preloaded catalog
- **Preload hook**: same `_preload_data()`
- **Cache shape**: `dict[str, list[dict]]`
- **Classification**: STATIC — all game object names are catalog data

---

#### `bountyCog.py` — `system_autocomplete` (line 112)
- **Parameter**: `system` on `/check`
- **Data source**: `self._systems` — list of system name strings
- **Preload hook**: `bot.loop.create_task(self._preload_data())` at line 47; includes retry logic (5 attempts, exponential backoff: 5s, 10s, 20s, 40s, 60s)
- **HTTP call**: `GET /about/categories/system/objects` (line 60) — same endpoint as aboutCog
- **Cache shape**: `list[str]`
- **Classification**: STATIC — same system catalog, duplicated fetch vs. aboutCog (minor inefficiency)
- **Note**: The two `system_autocomplete` implementations (bountyCog vs. aboutCog) independently preload the same data. A shared singleton cache would eliminate one fetch.

---

#### `devCog.py` — `category_autocomplete` (line 47)
- **Parameter**: `category` on `/load_data`
- **Data source**: `self._categories` — populated by `GET /data/categories` in `_preload_categories()` (line 34)
- **Preload hook**: `bot.loop.create_task(self._preload_categories())` at line 26
- **Cache shape**: `list[str]` + virtual `"All"` entry
- **Classification**: STATIC — data category list is code-defined, changes only on deployment

---

#### `adminCog.py` — `render_setting_autocomplete` (line 97)
- **Parameter**: `setting` on `/render_config`
- **Data source**: `self._render_settings` — populated by `GET /config/render` from blender-service in `_preload_render_settings()` (line 84)
- **Preload hook**: `bot.loop.create_task(self._preload_render_settings())` at line 72; 3 attempts with exponential backoff
- **Cache shape**: `list[str]` — JSON keys from render config response
- **Classification**: STATIC — render config keys are blender-service deployment constants, not game state

---

#### `skinsCog.py` — `ship_autocomplete` (line 303)
- **Parameter**: `ship` on `/ship_skin`
- **Data source**: `self._ship_skins` dict keys — ship names
- **Preload hook**: `bot.loop.create_task(self._preload_ship_skins())` at line 254; fetches all ships + per-ship compatible skins
- **Cache shape**: `dict[str, list[str]]` — ship name → list of skin names
- **Classification**: STATIC — skin catalog is game asset data

---

#### `skinsCog.py` — `skin_autocomplete` (line 314)
- **Parameter**: `skin` on `/ship_skin`, `/render_skin`, `/make_skin_texture`
- **Data source**: `self._ship_skins[ship]` — preloaded skin names for chosen ship
- **Cache shape**: `dict[str, list[str]]` (same as above)
- **Classification**: STATIC

---

#### `skinsCog.py` — `skinnable_ship_autocomplete` (line 331)
- **Parameter**: `ship` on `/render_skin`, `/make_skin_texture`
- **Data source**: `self._ship_skins` + `self._ship_render_info` — both preloaded
- **Classification**: STATIC

---

### 1.2 CORRECTLY HARDCODED — Trivial In-Memory Lists

These handlers filter against a small, code-defined constant list. No HTTP calls, no preload needed. ✅

---

#### `bountyCog.py` — `division_autocomplete` (line 99)
- **Parameter**: `division` on `/bounties`
- **Data source**: module-level constant `_VALID_DIVISIONS = ["bronze", "silver", "gold", "platinum"]`
- **Classification**: STATIC (hardcoded constant) — 4 values, effectively immutable
- **HTTP calls**: None

---

#### `helpCog.py` — `_user_category_autocomplete` (line 411) and `_admin_category_autocomplete` (line 420)
- **Parameters**: `category` on `/help` and `/admin_help`
- **Data source**: module-level lists `_USER_CATEGORY_ORDER` and `_ADMIN_CATEGORY_ORDER`
- **Classification**: STATIC (hardcoded) — mirrors command taxonomy, never changes at runtime
- **HTTP calls**: None
- **Note**: These are module-level free functions (not cog methods), since HelpCog has no HTTP client

---

#### `adminCog.py` — `tier_autocomplete` (line 108)
- **Parameter**: `tier` on `/admin_refresh_shop`
- **Data source**: `self._valid_tiers = ["Bronze", "Silver", "Gold", "Platinum"]` — hardcoded in `__init__`
- **Classification**: STATIC (hardcoded constant)
- **HTTP calls**: None

---

#### `shopCog.py` — `tier_autocomplete` (line 65) and `item_type_autocomplete` (line 76)
- **Parameters**: `tier` and `item_type` on `/shop`
- **Data source**: `self._valid_tiers` and `self._valid_item_types` — hardcoded in `__init__`
- **Classification**: STATIC (hardcoded constants)
- **HTTP calls**: None

---

### 1.3 STATIC — NOT YET PRELOADED ❌ (Root Cause of B.26 Broadened Scope)

These handlers query static game catalog data on every keystroke. They should preload at startup.

---

#### `adminCog.py` — `item_name_autocomplete` (line 1429)
- **Parameters**: `item_name` on `/admin_give_item` (line 1489) and `/admin_remove_item` (line 1569)
- **Data source**: For each of 4 categories (`primary_weapon`, `secondary_weapon`, `turret_weapon`, `module`) calls `GET /data/{category}` (lines 1439–1448). Up to **4 HTTP calls per keystroke**.
- **HTTP calls per keystroke**: Up to 4 (breaks early at 25 choices)
- **Data nature**: STATIC — game item catalog from seed data. Changes only when `/load_data` is run by a dev.
- **Fix**: Preload all 4 categories at startup into `self._item_catalog: dict[str, list[str]]`; refresh on demand via existing `/reload_autocomplete` command.

---

#### `adminCog.py` — `game_ship_autocomplete` (line 1454)
- **Parameters**: `ship_name` on `/admin_give_ship` (line 1639)
- **Data source**: `GET /about/ships` (line 1460) — ship catalog endpoint. **1 HTTP call per keystroke**.
- **HTTP calls per keystroke**: 1
- **Data nature**: STATIC — ship template catalog. Same data as skinsCog preloads in `_preload_ship_skins`.
- **Fix**: Preload ship names list at startup alongside `_render_settings` in `_preload_render_settings()` or a new `_preload_static_data()` method.
- **Note**: `adminCog.player_ship_autocomplete` (line 1696, for `/admin_remove_ship`) correctly falls back to this same `GET /about/ships` call; once the catalog is preloaded, the fallback branch should read from cache instead.

---

### 1.4 SHOP-CACHED — NOT YET CACHED ❌

These handlers query shop inventory data that is stable between 6-hour refreshes.

---

#### `shopCog.py` — `buy_item_autocomplete` (line 239)
- **Parameter**: `item_id` on `/buy` (line 268)
- **Data source**: 
  1. `POST /players/` to resolve player tier (1 call)
  2. `GET /shops/guild/{guild_id}/tier/{tier}` for **each accessible tier** (1–4 calls depending on player tier)
- **HTTP calls per keystroke**: 2 to 5 (player resolution + 1 to 4 tier queries)
- **Data nature**: SHOP-CACHED — shop inventory is fixed per tier per guild between 6-hour refresh cycles. Only mutated by successful `/buy` and `/sell` transactions within the period.
- **Fix**: Cache `dict[guild_id, dict[tier, list[ShopItem]]]` populated at startup and invalidated on refresh events and buy/sell transactions.

---

### 1.5 PER-PLAYER-DYNAMIC — Correctly Per-Keystroke ✅

These handlers query data that varies per-user and per-moment. No caching appropriate.

---

#### `bountyCog.py` — `bounty_autocomplete` (line 123)
- **Parameter**: `bounty` on `/route` (line 669) and `/criminal-loadout` (line 758)
- **Data source**: `GET /bounties/?guild_id=` — active bounties list
- **HTTP calls per keystroke**: 1
- **Data nature**: PER-PLAYER-DYNAMIC — bounties spawn and expire on schedules; the list can change between keystrokes if the bot is active
- **Verdict**: Correctly live. Caching would risk showing expired bounties.

---

#### `shopCog.py` — `sell_item_autocomplete` (line 377)
- **Parameter**: `item` on `/sell` (line 414)
- **Data source**: `POST /players/` + `GET /inventory/player/{id}` — player's personal inventory
- **HTTP calls per keystroke**: 2
- **Data nature**: PER-PLAYER-DYNAMIC — player's inventory changes after each buy/sell/equip/give
- **Verdict**: Correctly live.

---

#### `inventoryCog.py` — `item_autocomplete` (line 454)
- **Parameter**: `item_name` on `/item` (line 483)
- **Delegates to**: `player_inventory_autocomplete` in `utils/autocomplete_helpers.py` (line 471)
- **Data source**: `POST /players/` + `GET /inventory/player/{id}` — player's personal inventory
- **HTTP calls per keystroke**: 2
- **Data nature**: PER-PLAYER-DYNAMIC
- **Verdict**: Correctly live.

---

#### `inventoryCog.py` — `equip_autocomplete` (line 553)
- **Parameter**: `item_name` on `/equip` (line 664)
- **Data source**: `POST /players/` + `GET /inventory/player/{id}` + `GET /ships/player/{id}` (for equipped item exclusion)
- **HTTP calls per keystroke**: 3
- **Data nature**: PER-PLAYER-DYNAMIC — equippable set changes after every equip/unequip/buy/sell
- **Verdict**: Correctly live. Complex state (excludes already-equipped items requires active ship query).

---

#### `inventoryCog.py` — `unequip_autocomplete` (line 612)
- **Parameter**: `item_name` on `/unequip` (line 812)
- **Data source**: `POST /players/` + `GET /ships/player/{id}` + `GET /ships/{id}/loadout`
- **HTTP calls per keystroke**: 3
- **Data nature**: PER-PLAYER-DYNAMIC — equipped item set is player state
- **Verdict**: Correctly live.

---

#### `inventoryCog.py` — `give_item_autocomplete` (line 895)
- **Parameter**: `item` on `/give` (line 981)
- **Data source**: `POST /players/` + `GET /inventory/player/{id}`
- **HTTP calls per keystroke**: 2
- **Data nature**: PER-PLAYER-DYNAMIC
- **Verdict**: Correctly live.

---

#### `inventoryCog.py` — `give_ship_autocomplete` (line 931)
- **Parameter**: `ship` on `/give` (line 981)
- **Data source**: `POST /players/` + `GET /ships/player/{id}` (excludes active ship)
- **HTTP calls per keystroke**: 2
- **Data nature**: PER-PLAYER-DYNAMIC
- **Verdict**: Correctly live.

---

#### `shipsCog.py` — `setactive_autocomplete` (line 166)
- **Parameter**: `ship_id` on `/setactive` (line 294)
- **Delegates to**: `player_ships_autocomplete` in `utils/autocomplete_helpers.py`
- **Data source**: `POST /players/` + `GET /ships/player/{id}`
- **HTTP calls per keystroke**: 2
- **Data nature**: PER-PLAYER-DYNAMIC — player's owned ships
- **Verdict**: Correctly live.

---

#### `shipsCog.py` — `ship_autocomplete` (line 176)
- **Parameter**: `ship_id` on `/ship` (line 182) and `/nickname` (line 371)
- **Delegates to**: `player_ships_autocomplete`
- **Data source**: same as `setactive_autocomplete`
- **HTTP calls per keystroke**: 2
- **Data nature**: PER-PLAYER-DYNAMIC
- **Verdict**: Correctly live.

---

#### `adminCog.py` — `player_ship_autocomplete` (line 1696)
- **Parameter**: `ship_name` on `/admin_remove_ship` (line 1763)
- **Data source**: 
  - Primary path: `POST /players/` + `GET /ships/player/{id}` (target player's ships) — PER-PLAYER-DYNAMIC
  - Fallback path: `GET /about/ships` when target user not yet selected or resolution fails — STATIC
- **HTTP calls per keystroke**: 2 (primary) or 1 (fallback)
- **Classification**: PER-PLAYER-DYNAMIC (primary path) / STATIC (fallback)
- **Note**: The fallback `GET /about/ships` call would benefit from the same ship catalog preload as `game_ship_autocomplete` above.

---

#### `duelCog.py` — `pending_duel_autocomplete` (line 32)
- **Parameter**: `duel` on `/duel-accept` (line 164) and `/duel-reject` (line 278)
- **Data source**: `GET /duels/pending?user_id={id}&guild_id={id}` — pending duels targeting the invoking user
- **HTTP calls per keystroke**: 1
- **Data nature**: PER-PLAYER-DYNAMIC — active duel challenges are time-sensitive (15-minute expiry window typically)
- **Verdict**: Correctly live. Stale data would show expired/accepted duels.

---

### 1.6 OTHER — Justifiably Live But Could Be Improved

---

#### `schedulerCog.py` — `job_id_autocomplete` (line 35)
- **Parameter**: `job_id` on `/scheduler_view` (line 140), `/scheduler_update` (line 221), `/scheduler_delete` (line 300)
- **Data source**: `GET /jobs` — full APScheduler job list
- **HTTP calls per keystroke**: 1
- **Data nature**: LOW-FREQUENCY DYNAMIC — APScheduler jobs change rarely (only on job registration/deletion/trigger). Typically 3–10 jobs in the entire system.
- **Audience**: Admin-only commands. Very low usage frequency.
- **Classification**: OTHER — job list is small and admin-only; the cost of staleness (showing a just-deleted job ID) is trivial. A short TTL cache (5 minutes) would eliminate all overhead for the normal non-admin user, but the command is admin-only anyway so the net benefit is minimal.
- **Verdict**: Acceptable as-is per O.2 (this was previously scoped and accepted). If caching framework lands, could easily add 5-minute TTL cache.

---

## 2. Complete Summary Table

| # | Cog | Handler (line) | Parameter | Command(s) | HTTP calls/keystroke | Classification | Status |
|---|-----|----------------|-----------|------------|----------------------|----------------|--------|
| 1 | aboutCog | `category_autocomplete` (96) | `category` | `/about`, `/list_category` | 0 (preloaded) | STATIC | ✅ Preloaded |
| 2 | aboutCog | `system_autocomplete` (108) | `start`, `end` | `/make-route` | 0 (preloaded) | STATIC | ✅ Preloaded |
| 3 | aboutCog | `object_autocomplete` (121) | `name` | `/about` | 0 (preloaded) | STATIC | ✅ Preloaded |
| 4 | bountyCog | `division_autocomplete` (99) | `division` | `/bounties` | 0 (hardcoded) | STATIC | ✅ Hardcoded |
| 5 | bountyCog | `system_autocomplete` (112) | `system` | `/check` | 0 (preloaded) | STATIC | ✅ Preloaded |
| 6 | devCog | `category_autocomplete` (47) | `category` | `/load_data` | 0 (preloaded) | STATIC | ✅ Preloaded |
| 7 | helpCog | `_user_category_autocomplete` (411) | `category` | `/help` | 0 (hardcoded) | STATIC | ✅ Hardcoded |
| 8 | helpCog | `_admin_category_autocomplete` (420) | `category` | `/admin_help` | 0 (hardcoded) | STATIC | ✅ Hardcoded |
| 9 | adminCog | `render_setting_autocomplete` (97) | `setting` | `/render_config` | 0 (preloaded) | STATIC | ✅ Preloaded |
| 10 | adminCog | `tier_autocomplete` (108) | `tier` | `/admin_refresh_shop` | 0 (hardcoded) | STATIC | ✅ Hardcoded |
| 11 | shopCog | `tier_autocomplete` (65) | `tier` | `/shop` | 0 (hardcoded) | STATIC | ✅ Hardcoded |
| 12 | shopCog | `item_type_autocomplete` (76) | `item_type` | `/shop` | 0 (hardcoded) | STATIC | ✅ Hardcoded |
| 13 | skinsCog | `ship_autocomplete` (303) | `ship` | `/ship_skin` | 0 (preloaded) | STATIC | ✅ Preloaded |
| 14 | skinsCog | `skin_autocomplete` (314) | `skin` | `/ship_skin`, `/render_skin`, `/make_skin_texture` | 0 (preloaded) | STATIC | ✅ Preloaded |
| 15 | skinsCog | `skinnable_ship_autocomplete` (331) | `ship` | `/render_skin`, `/make_skin_texture` | 0 (preloaded) | STATIC | ✅ Preloaded |
| **16** | **adminCog** | **`item_name_autocomplete` (1429)** | **`item_name`** | **`/admin_give_item`, `/admin_remove_item`** | **1–4** | **STATIC** | **❌ Not preloaded** |
| **17** | **adminCog** | **`game_ship_autocomplete` (1454)** | **`ship_name`** | **`/admin_give_ship`** | **1** | **STATIC** | **❌ Not preloaded** |
| **18** | **shopCog** | **`buy_item_autocomplete` (239)** | **`item_id`** | **`/buy`** | **2–5** | **SHOP-CACHED** | **❌ Not cached** |
| 19 | bountyCog | `bounty_autocomplete` (123) | `bounty` | `/route`, `/criminal-loadout` | 1 | PER-PLAYER-DYNAMIC | ✅ Correctly live |
| 20 | shopCog | `sell_item_autocomplete` (377) | `item` | `/sell` | 2 | PER-PLAYER-DYNAMIC | ✅ Correctly live |
| 21 | inventoryCog | `item_autocomplete` (454) | `item_name` | `/item` | 2 | PER-PLAYER-DYNAMIC | ✅ Correctly live |
| 22 | inventoryCog | `equip_autocomplete` (553) | `item_name` | `/equip` | 3 | PER-PLAYER-DYNAMIC | ✅ Correctly live |
| 23 | inventoryCog | `unequip_autocomplete` (612) | `item_name` | `/unequip` | 3 | PER-PLAYER-DYNAMIC | ✅ Correctly live |
| 24 | inventoryCog | `give_item_autocomplete` (895) | `item` | `/give` | 2 | PER-PLAYER-DYNAMIC | ✅ Correctly live |
| 25 | inventoryCog | `give_ship_autocomplete` (931) | `ship` | `/give` | 2 | PER-PLAYER-DYNAMIC | ✅ Correctly live |
| 26 | shipsCog | `setactive_autocomplete` (166) | `ship_id` | `/setactive` | 2 | PER-PLAYER-DYNAMIC | ✅ Correctly live |
| 27 | shipsCog | `ship_autocomplete` (176) | `ship_id` | `/ship`, `/nickname` | 2 | PER-PLAYER-DYNAMIC | ✅ Correctly live |
| 28 | adminCog | `player_ship_autocomplete` (1696) | `ship_name` | `/admin_remove_ship` | 2 (primary) / 1 (fallback) | PER-PLAYER-DYNAMIC / STATIC fallback | ✅ Acceptable (fallback improvable) |
| 29 | duelCog | `pending_duel_autocomplete` (32) | `duel` | `/duel-accept`, `/duel-reject` | 1 | PER-PLAYER-DYNAMIC | ✅ Correctly live |
| 30 | schedulerCog | `job_id_autocomplete` (35) | `job_id` | `/scheduler_view`, `/scheduler_update`, `/scheduler_delete` | 1 | OTHER (low-freq, admin-only) | ✅ Acceptable as-is |

**Total handlers**: 30  
**Already correct**: 27 (90%)  
**Needing fix**: 3 (10%)

---

## 3. The Reference Pattern — `aboutCog._preload_data()`

**File**: `services/discord-gateway/src/cogs/aboutCog.py`  
**Lines**: 40–94 (preload function), 35 (startup trigger)

```python
# __init__ (line 28–35):
class AboutCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._categories: list[str] = []                        # cache storage
        self._objects_by_category: dict[str, list[dict]] = {}  # cache storage
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        bot.loop.create_task(self._preload_data())              # schedule preload

# Preload function (lines 40–94):
async def _preload_data(self):
    await self.bot.wait_until_ready()   # wait for Discord ready signal
    try:
        resp = await self.http_client.get(f"{api_base}/about/categories", timeout=5)
        resp.raise_for_status()
        self._categories = resp.json()   # populate cache
        for category in self._categories:
            try:
                resp = await self.http_client.get(
                    f"{api_base}/about/categories/{category}/objects", timeout=10
                )
                resp.raise_for_status()
                self._objects_by_category[category] = resp.json()
            except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError, Exception):
                self._objects_by_category[category] = []  # graceful degradation
    except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError, Exception):
        self._categories = []
        self._objects_by_category = {}

# Usage in autocomplete (lines 96–106):
async def category_autocomplete(self, _interaction, current):
    norm_current = normalize_for_search(current)
    return [
        app_commands.Choice(name=cat.replace("_", " ").title(), value=cat)
        for cat in self._categories
        if norm_current in normalize_for_search(cat)
    ][:25]
```

**Key properties of the pattern**:
1. `bot.loop.create_task()` in `__init__` — schedules the preload without blocking init
2. `await self.bot.wait_until_ready()` inside preload — ensures Discord connection before API calls
3. Graceful degradation — outer + inner try/except; empty list is safe fallback
4. Specific exception types caught before broad `Exception` fallback — better diagnostics
5. Cache is simple instance state — no TTL, no expiry for truly static data
6. The `/dev reload_autocomplete` command (devCog.py:110) can trigger manual refresh

---

## 4. Shop Refresh Timing and Gateway Awareness

### When does bot-core shop refresh occur?

**Default schedule**: Every 6 hours, registered as `shop_refresh_default` in APScheduler.  
**Executor**: `services/bot-core/src/utils/executors/shop_refresh_executor.py`  
**Trigger**: `execute_shop_refresh_job()` called by APScheduler via `run_job()`.

### Does bot-core notify discord-gateway when a refresh happens?

**Yes — via Discord message announcement.** In `shop_refresh_executor.py` (lines 115–118):

```python
# After each guild is refreshed:
await _announce_shop_refresh(job_id, gid, shop_channel_id, bounty_hunter_role_id)
```

This posts an embed to the guild's `#shop` channel via `POST /api/v1/channels/{shop_channel_id}/messages` on the discord-gateway REST API.

**However**: This announcement is a Discord embed to users — it does NOT include any structured signal to the bot process to invalidate a cache. The gateway receives a REST call to post a message, not a "shop refreshed" event to act on.

**Current gap**: The gateway has no programmatic hook to learn that shop inventory has changed. There is no:
- Shared message queue / Redis pub-sub channel
- Webhook with structured payload
- Polling endpoint for "last refresh timestamp"
- In-memory event bus between bot-core and discord-gateway

### How would gateway-side cache invalidation work?

Three viable design options (recon only — no fix here):

**Option A — Piggyback on the existing Discord channel announcement (low-complexity)**  
`shop_refresh_executor._announce_shop_refresh()` could additionally call a gateway-internal REST endpoint (e.g. `POST /api/v1/internal/shop-cache-invalidate?guild_id=X`) that triggers the ShopCog to reload its cache. The gateway already has an internal REST server.  
*Pros*: No new infrastructure. *Cons*: Couples shop cache invalidation to announcement path; announcement failures would also skip invalidation.

**Option B — Add a dedicated bot-core → gateway notification endpoint (clean architecture)**  
Add `POST /api/v1/internal/shop-refreshed` on the gateway. Bot-core calls this after each successful shop refresh. The ShopCog listens and updates its cache.  
*Pros*: Clean separation. *Cons*: Requires new gateway REST endpoint + cog method.

**Option C — Periodic cache TTL (simplest, eventual consistency)**  
ShopCog caches shop inventory with a 30-minute TTL. On cache miss or expiry, re-fetches the tier's inventory. No event mechanism needed.  
*Pros*: Dead simple, no cross-service changes. *Cons*: Up to 30-minute staleness window after refresh (acceptable for shop autocomplete — stale items simply don't exist when player tries to buy).

### When does a player buy/sell from a shop?

Buy transaction: `shopCog.buy()` calls `POST /api/v1/shops/purchase` or `POST /api/v1/shops/purchase-ship` on bot-core.

**Current flow**: Bot-core decrements `GuildShop.quantity` for the purchased item. The gateway's potential shop cache would become stale immediately after any purchase.

**Cache invalidation on buy**: Since the buy *command* runs in the gateway (in `shopCog.buy()`), the gateway could invalidate its own shop cache for `(guild_id, tier)` immediately after a successful purchase — no cross-service event needed. The invalidation point is already in cog code.

**Cache invalidation on sell**: Same — the sell *command* runs in `shopCog.sell()`. The gateway could invalidate the cache for the player's tier after a successful sell.

---

## 5. Bot Startup Hook Enumeration

All preload hooks found in HEAD:

| Cog | Method | Hook location | Data fetched |
|-----|--------|---------------|--------------|
| `AboutCog` | `_preload_data()` | `__init__` line 35 | All categories + all objects per category |
| `BountyCog` | `_preload_data()` | `__init__` line 47 | Star system names |
| `DevCog` | `_preload_categories()` | `__init__` line 26 | Data category list |
| `AdminCog` | `_preload_render_settings()` | `__init__` line 72 | Blender render config key names |
| `SkinsCog` | `_preload_ship_skins()` | `__init__` line 254 | All ships + their compatible skins |

**Pattern**: All use `bot.loop.create_task(self._preload_*())` in `__init__`, and `await self.bot.wait_until_ready()` at the start of the preload function.

**`/reload_autocomplete` command** (`devCog.py` lines 110–150): Manually triggers re-preload on `AboutCog._preload_data`, `DevCog._preload_categories`, and `SkinsCog._preload_ship_skins`. Does **not** currently list `BountyCog._preload_data` or `AdminCog._preload_render_settings` — those are absent from the `targets` list (line 120–125).

---

## 6. Estimated HTTP Call Savings

Assumptions: 5 concurrent users, 8 keystrokes per autocomplete interaction, all interacting with the 3 problematic handlers.

### Current (unoptimized) HTTP calls for problematic handlers:

| Handler | Calls/keystroke | Keystrokes/use | Calls/use | Users | Calls/session |
|---------|----------------|-----------------|-----------|-------|---------------|
| `item_name_autocomplete` (admin only) | ~4 | 8 | 32 | 1 admin | 32 |
| `game_ship_autocomplete` (admin only) | 1 | 8 | 8 | 1 admin | 8 |
| `buy_item_autocomplete` | 2–5 (avg ~3) | 8 | 24 | 5 users | 120 |

**Total rough estimate**: ~160 HTTP calls per session just for these 3 handlers.

At 5 users, 10 autocomplete interactions/hour:
- `buy_item_autocomplete`: 5 users × 10 interactions × 24 calls = **1,200 calls/hour**
- Admin handlers: much lower frequency, ~40 calls/hour
- **Total avoidable calls**: ~1,240/hour → **~0 after caching**

The per-player-dynamic handlers account for ~5 HTTP calls/keystroke each. These are legitimately necessary and total approximately:
- 10 interactive per-player autocompletes × 5 users × 8 keystrokes × 2–3 calls = **800–1,200 calls/hour** (unavoidable)

---

## 7. Sibling Sweep — Additional Context

### Duplicate system catalog fetch
Both `aboutCog` and `bountyCog` independently preload the system catalog from `GET /about/categories/system/objects`. A shared singleton (e.g., a module-level `_SYSTEM_CACHE` in a shared utils module, or a shared bot-level cache) would eliminate one startup HTTP call. Minor efficiency concern; not a latency issue since both preload at startup.

### `adminCog.player_ship_autocomplete` fallback
Lines 1743–1752: When target user is not yet selected, this handler calls `GET /about/ships` per keystroke. Once `game_ship_autocomplete` is preloaded, this fallback branch should read from the same preloaded list.

### `/reload_autocomplete` coverage gap
The `devCog.reload_autocomplete` targets list (line 120–125) covers only `AboutCog`, `DevCog`, and `SkinsCog`. If `AdminCog` gets new preloaded caches (`_item_catalog`, `_ship_catalog`), `BountyCog._preload_data`, and `ShopCog._shop_cache`, these should be added to the reload list.

---

**Recon completed**: 2026-04-28 by developer (read-only investigation)
