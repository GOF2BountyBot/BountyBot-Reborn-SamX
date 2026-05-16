# Design: Autocomplete Cache Overhaul

**Status**: Fully implemented — Phases 1–8 complete  
**Implementation date**: 2026-05-16  
**Last updated**: 2026-05-16  
**Scope**: `services/discord-gateway` (primary) + minor additive changes to `services/bot-core`  
**Effort estimate**: ~1,400 LoC source, ~1,200 LoC tests across 8 phases  

---

## Problem Statement

Discord autocomplete handlers fire on every keystroke. The hard performance requirement is **≤100ms internal response time** (target ~50ms) with **zero network I/O on the hot path**.

A full audit of all ~37 autocomplete handlers in the discord-gateway service found that roughly 15–20 make 1–3 HTTP calls to bot-core **per keystroke**. The root cause is two endemic patterns:

1. **Uncached player resolution** — `resolve_player_id()` in `autocomplete_helpers.py` calls `POST /api/v1/players/` (a player upsert) on every keystroke. Used by 12+ handlers across shopCog, inventoryCog, duelCog, and adminCog.

2. **Uncached inventory/ships lookups** — `player_equippable_autocomplete()` makes THREE HTTP calls per keystroke (player POST + inventory GET + ships GET). All shared helpers make at least two.

### Current Severity by Handler

| Handler | Severity | HTTP calls/keystroke |
|---------|----------|----------------------|
| `inventoryCog.equip_autocomplete` | 🔴 CRITICAL | 2 |
| `inventoryCog.unequip_autocomplete` | 🔴 CRITICAL | 3 |
| `autocomplete_helpers.player_equippable_autocomplete` | 🔴 CRITICAL | 3 |
| `schedulerCog.job_id_autocomplete` | 🔴 CRITICAL | 1 |
| `shopCog.sell_item_autocomplete` | 🔴 CRITICAL | 2 |
| `shopCog.buy_item_autocomplete` | 🟠 HIGH | 1 (shop cached, player tier not) |
| `duelCog.pending_duel_autocomplete` | 🟠 HIGH | 2 |
| `duelCog.outgoing_duel_autocomplete` | 🟠 HIGH | 2 |
| `bountyCog.bounty_autocomplete` | 🟠 HIGH | 1 |
| All `autocomplete_helpers.py` helpers | 🟠 HIGH | 1–3 each |
| `normalize_for_search()` in hot loop | 🟡 MEDIUM | 0 HTTP, but NFKD alloc per item per keystroke |
| `aboutCog`, `devCog`, `helpCog`, static lists | 🟢 GOOD | 0 — reference implementations |

---

## Design Constraints

- **RAM is not a concern** — cache aggressively, keep everything warm
- **No startup surge** — must not hammer bot-core with hundreds of simultaneous HTTP calls at `on_ready`
- **Keep caches live and fresh** — proactively-warmed model, not lazy TTL-driven
- **APScheduler available** — bot-core already runs APScheduler; gateway will add a lightweight memory-backed instance
- **No new external infrastructure** — in-process memory only, no Redis, no message bus

---

## Architecture Overview

### Core Strategy

Replace the lazy "cold miss → return `[]` → background warm" model with **proactively-warmed caches** that stay current through scheduled refresh jobs and event-driven pushes. Cold misses become the rare exception (a player the bot has never seen before), not normal operating mode.

Three complementary mechanisms:

1. **Startup warm** — staggered APScheduler jobs fill all active-player caches within ~60s of `on_ready`, without blocking bot startup or surging bot-core
2. **Scheduled refresh** — gateway APScheduler jobs keep player/inventory/ships caches current; bot-core pushes shop and bounty data to gateway on every change
3. **Explicit invalidation** — command success paths immediately invalidate (or write-through) affected cache entries

---

## 1. Cache Architecture

### 1.A Shared User-Data Caches (`utils/autocomplete_state.py` — new module)

Module-level singletons shared across all cogs. A cross-cog shared module is necessary because `/buy` in shopCog must invalidate the inventory cache that `inventoryCog.equip_autocomplete` reads — impossible if each cog owns its own instance.

| Cache | Key | Value | TTL | Refresh cadence |
|-------|-----|-------|-----|-----------------|
| `player_cache` | `(guild_id: int, user_id: int)` | full player `dict` (id, tier, credits, xp, …) | 15 min | Every 10 min (scheduled) |
| `inventory_cache` | `(guild_id: int, player_id: int)` | `list[NormalizedChoice]` | 10 min | Every 5 min round-robin (scheduled) |
| `ships_cache` | `(guild_id: int, player_id: int)` | `list[NormalizedChoice]` | 10 min | Every 5 min round-robin (scheduled) |

TTLs are **defensive depth only** (≥ 2× refresh interval). The scheduled refresher is the primary freshness mechanism; TTL ensures stale data eventually evicts if a refresh job silently dies.

### 1.B Per-Cog Live-Data Caches

Stay on each cog because invalidation is cog-local and TTLs are short enough that no scheduled refresh is needed.

| Cache | Owner | Key | TTL | Refresh |
|-------|-------|-----|-----|---------|
| `_shop_cache` | shopCog | `(guild_id, tier)` | None (push-only) | bot-core push on every `shop_refresh_executor` run; 15-min safety-net gateway pull |
| `_bounty_cache` | bountyCog | `guild_id` | 60s | bot-core push on every spawn/expire; TTL as safety net |
| `_pending_duel_cache` | duelCog | `(guild_id, player_id)` | 30s | Explicit invalidation only |
| `_outgoing_duel_cache` | duelCog | `(guild_id, player_id)` | 30s | Explicit invalidation only |
| `_job_cache` | schedulerCog | `"all"` | 120s | Every 60s (scheduled) |

### 1.C `NormalizedChoice` Pre-computation

`normalize_for_search(label)` currently called on every item inside the hot-path loop per keystroke (NFKD decomposition + string replace + lower). Move this to cache-fill time.

```python
class NormalizedChoice(NamedTuple):
    label: str    # display label, e.g. "Laser Cannon (1,500c)"
    value: str    # Choice value passed to the command
    norm: str     # pre-computed normalize_for_search(label)
    raw: dict     # underlying dict for fields helpers may need
```

Hot-path loop becomes a pure substring scan:

```python
items = state.inventory_cache.peek((guild_id, player_id)) or []
norm_q = normalize_for_search(current)
return [app_commands.Choice(name=it.label[:100], value=it.value)
        for it in items if norm_q in it.norm][:25]
```

---

## 2. `AutocompleteCache` Enhancements

### `peek(key) -> V | None`

Synchronous, non-blocking, never calls `refresh_fn`. Returns the stored value if present and unexpired; `None` otherwise. Used by autocomplete hot paths.

```python
def peek(self, key: K) -> V | None:
    """Return cached value without refresh or None if missing/expired.
    Synchronous. Never awaits, never raises, never mutates the cache."""
    entry = self._store.get(key)
    if entry is None:
        return None
    if self._ttl is not None and (self._monotonic() - entry.stored_at) > self._ttl:
        return None
    return entry.value
```

### `schedule_refresh(key) -> None`

Fire-and-forget background refresh for cold-miss paths:

```python
def schedule_refresh(self, key: K) -> None:
    """Fire asyncio.create_task to refresh this key in the background.
    No-op if refresh_fn is None. Safe to call when already warm
    (lock in get() suppresses duplicate refreshes)."""
    if self._refresh_fn is None:
        return
    asyncio.create_task(self.get(key), name=f"warm-{self._name}-{key}")
```

### `max_entries` LRU cap (optional)

New constructor param `max_entries: int | None = None`. When set, evicts the oldest entry (by `stored_at`) on `set()` when the store exceeds the cap. Provides a memory safety net for very large deployments.

### Canonical hot-path pattern

```python
items = self._cache.peek(key)
if items is None:
    self._cache.schedule_refresh(key)
    return []   # next keystroke will be warm
# ... filter and return
```

---

## 3. `autocomplete_state.py` — Shared Module (New File)

**Path**: `services/discord-gateway/src/utils/autocomplete_state.py`

```python
"""Module-level shared caches for discord-gateway autocomplete.

All cogs share one view of per-user state. Invalidation calls in one cog
(e.g. /buy invalidating inventory) are immediately visible to autocomplete
handlers in other cogs (e.g. /equip).

init() must be called once from GatewayBot.setup_hook() using the bot-owned
httpx.AsyncClient. The bot owns this client's lifecycle (close on bot.close()).
"""

class NormalizedChoice(NamedTuple): ...

# Module-globals — configured by init()
player_cache:    AutocompleteCache[tuple[int, int], dict]
inventory_cache: AutocompleteCache[tuple[int, int], list[NormalizedChoice]]
ships_cache:     AutocompleteCache[tuple[int, int], list[NormalizedChoice]]

def init(http_client: httpx.AsyncClient, api_base: str) -> None:
    """Idempotent. First call wins. Subsequent calls are no-ops."""

# Getters (peek-first; schedule_refresh on cold miss; return None)
async def get_player(guild_id: int, user_id: int) -> dict | None: ...
async def get_player_id(guild_id: int, user_id: int) -> int | None: ...

# Write-through (use after commands that have the fresh value in hand)
def set_player(guild_id: int, user_id: int, player: dict) -> None: ...
def set_inventory(guild_id: int, player_id: int, items: list[NormalizedChoice]) -> None: ...
def set_ships(guild_id: int, player_id: int, ships: list[NormalizedChoice]) -> None: ...

# Invalidation (use when command mutates state but doesn't have fresh value)
def invalidate_player(guild_id: int, user_id: int) -> None: ...
def invalidate_inventory(guild_id: int, player_id: int) -> None: ...
def invalidate_ships(guild_id: int, player_id: int) -> None: ...

# Maintenance
def clear_all() -> None: ...  # called by /reload_autocomplete
```

**Key design choice — module-level singletons vs. per-cog instances:**  
Module-level wins because `/buy` (shopCog) must invalidate the inventory cache that `inventoryCog.equip_autocomplete` reads. With per-cog instances that's impossible. With a shared module, `autocomplete_state.invalidate_inventory(guild_id, player_id)` is called from shopCog and the effect is immediately visible to inventoryCog.

**HTTP client ownership:**  
`autocomplete_state` uses a **bot-owned** `httpx.AsyncClient` created in `bot.py` lifespan. This client is never owned by any cog, so it outlives any cog reload. Stored at `app.state.autocomplete_http`.

---

## 4. Startup Warm Strategy

### Gateway APScheduler (new — lightweight, memory job store)

Add `AsyncIOScheduler(jobstores={"default": MemoryJobStore()})` to `bot.py` lifespan. No DB, no persistence — these are warm jobs that simply restart with the bot.

```python
# bot.py lifespan startup:
scheduler = AsyncIOScheduler(jobstores={"default": MemoryJobStore()}, timezone="UTC")
scheduler.start()
app.state.scheduler = scheduler

# bot.py on_ready:
_register_autocomplete_warm_jobs(scheduler, bot)

# bot.py lifespan shutdown:
scheduler.shutdown(wait=False)
await app.state.autocomplete_http.aclose()
```

### Stage 1 — Bulk player warm (one call per guild, staggered)

Scheduled in `on_ready`, **15s initial delay**, **200ms stagger** per guild:

```python
for i, guild in enumerate(bot.guilds):
    scheduler.add_job(
        warm_guild_players,
        "date",
        run_date=now + timedelta(seconds=15 + i * 0.2),
        args=[guild.id],
        id=f"warm-guild-{guild.id}",
    )
```

`warm_guild_players(guild_id)`:
- Calls `GET /api/v1/players/guild/{guild_id}?active_within_days=7&limit=500`
- Paginates with `skip` until fewer than `limit` rows returned
- For each player: `autocomplete_state.set_player(guild_id, player["user_id"], player)`
- One HTTP call → hundreds of player cache entries

At 50 guilds with 200ms stagger: last guild starts warming at ~25s. At 1000 guilds: ~215s (all still non-blocking and behind the bot's live endpoint).

### Stage 2 — Per-player loadout warm (staggered, capped concurrency)

Fired after Stage 1 completes for each guild, using `asyncio.Semaphore(AUTOCOMPLETE_WARM_CONCURRENCY)` (default 4):

```python
async def warm_active_player_loadout(guild_id: int, player_id: int) -> None:
    """Two HTTP calls → fills inventory_cache and ships_cache for one player."""
    async with _warm_semaphore:
        # GET /api/v1/inventory/player/{player_id}  → set_inventory(...)
        # GET /api/v1/ships/player/{player_id}      → set_ships(...)
```

At 4 concurrent and 200ms per call, warming 500 players takes ~25 seconds. During this time the bot is fully serving requests — any user who hits autocomplete before their entry is warm gets the cold-miss path (return `[]`, schedule immediate warm).

### "Active player" definition

Players with `Player.updated_at >= now() - 7 days`. Configurable via `AUTOCOMPLETE_WARM_ACTIVE_DAYS` (default 7, set to 0 to warm everyone).

Requires a **one-line bot-core change**: add `active_within_days: int | None = None` query param to `GET /api/v1/players/guild/{guild_id}`.

---

## 5. Scheduled Refresh (Keeping Caches Live)

### Gateway APScheduler recurring jobs

Registered in `on_ready` alongside the one-time warm jobs:

```python
# Player bulk re-warm — one bulk call per guild every 10 min
scheduler.add_job(refresh_all_guild_players, "interval",
                  minutes=AUTOCOMPLETE_PLAYER_REFRESH_MINUTES,
                  id="autocomplete-player-refresh")

# Inventory + ships round-robin — steady drip, capped concurrency
scheduler.add_job(refresh_loadouts_round_robin, "interval",
                  minutes=AUTOCOMPLETE_LOADOUT_REFRESH_MINUTES,
                  id="autocomplete-loadout-refresh")

# Scheduler jobs cache
scheduler.add_job(refresh_jobs_cache, "interval",
                  seconds=60, id="autocomplete-jobs-refresh")

# Shop safety-net (push is primary; this covers bot-core restart edge cases)
scheduler.add_job(refresh_shop_cache_safety_net, "interval",
                  minutes=15, id="autocomplete-shop-safety-net")
```

`refresh_loadouts_round_robin` iterates the set of currently-cached `(guild_id, player_id)` keys at a steady pace, gated by the 4-wide semaphore. With 500 active players and a 5-min interval: ~3.3 loadout calls/second — well within bot-core capacity.

### bot-core push (shop + bounty)

bot-core executors POST payloads to new internal gateway endpoints after each relevant mutation. Gateway receives, normalizes into `NormalizedChoice` list, and `set()`s the cache. Cache lag = one network RTT.

**New gateway endpoints** (`src/api/routers/internal_autocomplete.py`):

```
POST /api/v1/internal/autocomplete/shop-cache/{guild_id}/{tier}
    Body: { items: [...] }   # full shop stock — same shape as GET /shops/guild/{gid}/tier/{tier}

POST /api/v1/internal/autocomplete/bounty-cache/{guild_id}
    Body: { bounties: [...] }
```

Protected by `X-Internal-Auth: {INTERNAL_AUTH_TOKEN}` shared-secret header (both services read from env).

**bot-core executor changes:**

`shop_refresh_executor.py` — after each `refresh_shop()` call, POST the new stock to the gateway endpoint (identical non-fatal pattern as the existing shop announcement POST).

`bounty_spawn_executor.py` / `bounty_expire_executor.py` — after spawn/expire, POST the updated active bounty list for the guild to the gateway endpoint.

---

## 6. Invalidation Matrix

Invalidation is called in **command success paths, after `raise_for_status()` succeeds**, wrapped in try/except so a cache error never aborts a successful transaction. Use `set_*` (write-through) when the fresh value is already in hand; use `invalidate_*` otherwise.

```python
# Template — copy into every relevant success path:
try:
    autocomplete_state.invalidate_inventory(interaction.guild_id, player_id)
    autocomplete_state.invalidate_ships(interaction.guild_id, player_id)
except Exception:
    flogger.warning(f"/equip: cache invalidation failed for player_id={player_id}; transaction still succeeded")
```

| Cache | Invalidated/set by | Notes |
|-------|-------------------|-------|
| `player_cache` | `/promote`, `/demote`, `/prestige` → `set_player` | Use set_player (fresh value already in response) |
| `player_cache` | `/profile` → `set_player` | Same |
| `player_cache` | `/buy`, `/sell` → `invalidate_player` | Credits changed; no fresh player in hand |
| `player_cache` | `/admin_player` mutations → `invalidate_player` | Target user's cache |
| `inventory_cache` | `/buy`, `/sell` → `invalidate_inventory` | |
| `inventory_cache` | `/equip`, `/unequip` → `invalidate_inventory` | Cargo qty changes |
| `inventory_cache` | `/give` (giver + recipient) → `invalidate_inventory` × 2 | Both parties |
| `inventory_cache` | `/admin_player give_item`, `remove_item` → `invalidate_inventory` | Target player |
| `ships_cache` | `/setactive`, `/nickname` → `invalidate_ships` | |
| `ships_cache` | `/equip`, `/unequip` → `invalidate_ships` | Loadout changes |
| `ships_cache` | `/buy` if `item_type == "ship"` → `invalidate_ships` | New ship acquired |
| `ships_cache` | `/give` ship variant → `invalidate_ships` × 2 | Both parties |
| `ships_cache` | `/admin_player give_ship` → `invalidate_ships` | Target player |
| `_pending_duel_cache` | `/duel-accept`, `/duel-reject`, `/duel-challenge` (target) | |
| `_outgoing_duel_cache` | `/duel-challenge` (challenger), `/duel-cancel`, `/duel-reject` (challenger) | |
| All shared caches | `/reload_autocomplete` → `autocomplete_state.clear_all()` | |

---

## 7. Memory Footprint

Per-entry estimates (Python overhead included):
- **Player dict** (~20 fields, mostly ints + 3 short strings): ~850 bytes
- **Inventory** (`NormalizedChoice` × ~25 items): ~400 bytes/item → ~10 KB/player
- **Ships** (`NormalizedChoice` × ~4 ships): ~500 bytes/ship → ~2 KB/player
- **Per active player total**: ~13 KB

| Scenario | Active players | Cache memory |
|----------|---------------|--------------|
| 10 guilds × 50 players | 500 | ~6.5 MB |
| 50 guilds × 100 players | 5,000 | ~65 MB |
| 200 guilds × 200 players | 40,000 | ~520 MB |

Shop/bounty/duel/job caches: ≤5 MB even at ceiling scale.

**Recommendation**: No memory cap needed for scenarios A and B. At scenario C, configure `AUTOCOMPLETE_INVENTORY_MAX_ENTRIES` and `AUTOCOMPLETE_SHIPS_MAX_ENTRIES` (LRU eviction via `max_entries` on `AutocompleteCache`).

---

## 8. Bot-Core Changes Required

Minimal — one additive query param:

```
GET /api/v1/players/guild/{guild_id}
    ?skip: int = 0
    &limit: int = 100
    &tier: str | None = None
    &active_within_days: int | None = None   ← NEW
```

One-line addition to the query in `PlayerService.get_players_by_guild` (or its repo):

```python
if active_within_days is not None:
    query = query.where(Player.updated_at >= datetime.now(UTC) - timedelta(days=active_within_days))
```

---

## 9. Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `AUTOCOMPLETE_WARM_ACTIVE_DAYS` | `7` | Players active within this many days are warmed on startup. `0` = warm everyone. |
| `AUTOCOMPLETE_WARM_CONCURRENCY` | `4` | Max concurrent inventory/ships fetches during warm + scheduled refresh. |
| `AUTOCOMPLETE_WARM_GUILD_STAGGER_MS` | `200` | Spacing between per-guild warm jobs at startup (ms). |
| `AUTOCOMPLETE_PLAYER_REFRESH_MINUTES` | `10` | Interval for player_cache bulk re-warm. |
| `AUTOCOMPLETE_LOADOUT_REFRESH_MINUTES` | `5` | Interval for inventory/ships round-robin re-warm. |
| `AUTOCOMPLETE_INVENTORY_MAX_ENTRIES` | *(unset)* | LRU cap on inventory_cache. Set for very large deployments. |
| `AUTOCOMPLETE_SHIPS_MAX_ENTRIES` | *(unset)* | LRU cap on ships_cache. |
| `INTERNAL_AUTH_TOKEN` | *(required)* | Shared secret for bot-core → gateway internal push endpoints. |

---

## 10. Implementation Phases

| Phase | Scope | Files touched | Risk | LoC |
|-------|-------|--------------|------|-----|
| **1** | `AutocompleteCache.peek()` + `schedule_refresh()` + `max_entries` LRU | `cogs/_shared/autocomplete_cache.py` | Very low — additive | +50 src / +80 tests |
| **2** | `autocomplete_state.py` shared module. Add `active_within_days` to bot-core players endpoint. | `utils/autocomplete_state.py` (new), `bot-core/routers/players.py`, `player_service.py` or repo | Low — dormant | +280 src / +220 tests |
| **3** | Gateway APScheduler in `bot.py` + all warm/refresh job functions | `bot.py`, new `utils/autocomplete_warm.py` | Medium — new gateway infra | +200 src / +120 tests |
| **4** | Rewire `autocomplete_helpers.py` to read from shared state (peek + warm-on-miss) | `utils/autocomplete_helpers.py` | Medium — 5-cog reach | +80/−150 src / +150 tests |
| **5a** | Invalidation wiring into command success paths (all 6 cogs, full matrix) | playerCog, shopCog, inventoryCog, shipsCog, duelCog, adminCog | Medium — sensitive commands | +250 src / +200 tests |
| **5b** | bot-core push endpoints in gateway + executor changes to POST shop/bounty | `src/api/routers/internal_autocomplete.py` (new), `bot-core/executors/shop_refresh_executor.py`, `bounty_spawn_executor.py`, `bounty_expire_executor.py` | Medium — bot-core additive | +200 src / +150 tests |
| **6** | Rewrite per-cog autocomplete handlers to use `peek()` (shopCog, inventoryCog, duelCog, schedulerCog, adminCog, bountyCog) | 6 cog files | Low–medium | +200/−400 src / +150 tests |
| **7–8** | Live-data caches finalised + pre-normalize labels in all refresh_fns + dead HTTP call removal | All modified cogs | Very low | +150/−60 src / +100 tests |
| **TOTAL** | | | | **~1,400 src / ~1,200 tests** |

### Ordering rationale

- **Phases 1–3**: Infrastructure only. Bot behaviour unchanged. Zero regression risk. Merge independently.
- **Phase 4**: First user-visible perf win. Every cog using shared helpers (shipsCog, inventoryCog, adminCog, etc.) gets fast autocomplete immediately.
- **Phase 5a**: Must follow Phase 4 for correctness — no stale cargo after `/buy`.
- **Phase 5b**: bot-core integration. Can run in parallel with 5a if separate branches.
- **Phases 6–8**: Cleanup pass. By this point shared caches are reliable; the rewrites are mechanical.

---

## 11. Acceptance Criteria

### Performance

- **AC-PERF-1** — Every autocomplete handler returns within **100ms** at p99 when the cache is warm. Target p50 ≤ 50ms.
- **AC-PERF-2** — Under steady-state operation (>10 min after startup), no autocomplete handler issues any network request. Verified by mocking the bot-owned HTTP client and asserting zero calls across 100 simulated keystrokes per active handler.
- **AC-PERF-3** — On a true cold miss, the handler returns `[]` within **20ms** without awaiting any HTTP response and schedules a background refresh.

### Startup

- **AC-WARM-1** — Within 60 seconds of `on_ready`, `player_cache` contains entries for every player with `updated_at` within `AUTOCOMPLETE_WARM_ACTIVE_DAYS` for every connected guild.
- **AC-WARM-2** — During the first 30 seconds after `on_ready`, no more than `N + AUTOCOMPLETE_WARM_CONCURRENCY` concurrent HTTP requests are issued to bot-core, where N = number of connected guilds.
- **AC-WARM-3** — When `shop_refresh_executor` completes, the gateway `shop_cache` for every refreshed `(guild_id, tier)` reflects new stock within **5 seconds** without the gateway issuing a GET to bot-core.
- **AC-WARM-4** — Scheduled refresh jobs run at configured cadences; missing a single tick is non-fatal and the next tick re-warms affected entries.

### Coherence

- **AC-COHERE-1** — After `/buy` succeeds, the next `/equip` autocomplete keystroke for that user reflects the updated cargo quantity.
- **AC-COHERE-2** — After `/sell` succeeds, the next `/equip` autocomplete keystroke reflects the decreased cargo quantity.
- **AC-COHERE-3** — After `/equip` or `/unequip` succeeds, both equip and unequip autocompletes reflect the new loadout.
- **AC-COHERE-4** — After `/setactive` succeeds, `/equip` and `/unequip` autocompletes reflect the new active ship's loadout.
- **AC-COHERE-5** — After `/promote`, `/demote`, or `/prestige` succeeds, `/buy` autocomplete reflects the user's new tier.
- **AC-COHERE-6** — After `/duel-accept` or `/duel-reject` succeeds, the corresponding duel no longer appears in duel autocomplete for that user.

### Robustness

- **AC-ROB-1** — When a scheduled refresh fails (bot-core unreachable), the last-known-good cached value is served until the TTL expires or explicit invalidation occurs.
- **AC-ROB-2** — Concurrent keystrokes for the same `(guild_id, user_id)` do not trigger more than one outstanding refresh request.
- **AC-ROB-3** — A cache invalidation failure does not abort a successful command. A warning is logged; the user sees command success.
- **AC-ROB-4** — `/reload_autocomplete` clears all shared caches via `autocomplete_state.clear_all()`.

### Staleness Bounds (safety-net TTLs)

- **AC-STALE-1** — `player_cache` entries are no older than 15 minutes without explicit refresh or invalidation.
- **AC-STALE-2** — `inventory_cache` and `ships_cache` entries are no older than 10 minutes.
- **AC-STALE-3** — `_bounty_cache` entries are no older than 60 seconds.
- **AC-STALE-4** — Duel caches are no older than 30 seconds.
- **AC-STALE-5** — `_job_cache` entries are no older than 120 seconds.

### Backward Compatibility

- **AC-COMPAT-1** — Existing public signatures of all functions in `autocomplete_helpers.py` are preserved unchanged.
- **AC-COMPAT-2** — `AutocompleteCache.get()` semantics are unchanged. `peek()`, `schedule_refresh()`, and `max_entries` are additive.
- **AC-COMPAT-3** — All existing autocomplete tests continue to pass without modification.

---

## 12. Files Created / Modified

### New files (discord-gateway)
| File | Purpose |
|------|---------|
| `src/utils/autocomplete_state.py` | Shared module-level caches, NormalizedChoice, init, getters, invalidators |
| `src/utils/autocomplete_warm.py` | Startup warm + scheduled refresh job functions |
| `src/api/routers/internal_autocomplete.py` | Internal push endpoints for bot-core → gateway shop/bounty cache updates |
| `src/api/schemas/internal_schemas.py` | Pydantic schemas for push payloads |
| `tests/utils/test_autocomplete_state.py` | Unit tests for shared state module |
| `tests/utils/test_autocomplete_warm.py` | Unit tests for warm/refresh jobs |
| `tests/api/test_internal_autocomplete.py` | Tests for push endpoints |

### Modified files (discord-gateway)
| File | Change |
|------|--------|
| `src/cogs/_shared/autocomplete_cache.py` | Add `peek()`, `schedule_refresh()`, `max_entries` LRU |
| `src/utils/autocomplete_helpers.py` | Rewire all helpers to use shared state (preserve signatures) |
| `src/bot.py` | Add APScheduler, bot-owned HTTP client, `autocomplete_state.init()` |
| `src/cogs/shopCog.py` | Invalidation on /buy, /sell; rewrite buy/sell autocomplete to use peek() |
| `src/cogs/inventoryCog.py` | Invalidation on /equip, /unequip; rewrite equip/unequip autocomplete |
| `src/cogs/playerCog.py` | set_player() write-through on /promote, /demote, /prestige, /profile |
| `src/cogs/shipsCog.py` | Invalidation on /setactive, /nickname |
| `src/cogs/duelCog.py` | Add _pending_duel_cache, _outgoing_duel_cache; invalidations on challenge/accept/reject |
| `src/cogs/bountyCog.py` | Add _bounty_cache; receive push from bot-core |
| `src/cogs/adminCog.py` | Invalidations on give_item, remove_item, give_ship, admin_player mutations |
| `src/cogs/schedulerCog.py` | Add _job_cache with 60s scheduled refresh |

### New files (bot-core)
*(None — only modifications)*

### Modified files (bot-core)
| File | Change |
|------|--------|
| `src/api/routers/players.py` | Add `active_within_days` optional query param |
| `src/services/player_service.py` (or repo) | Add WHERE clause for `active_within_days` |
| `src/utils/executors/shop_refresh_executor.py` | POST to gateway push endpoint after each tier refresh |
| `src/utils/executors/bounty_spawn_executor.py` | POST updated bounty list to gateway after spawn |
| `src/utils/executors/bounty_expire_executor.py` | POST updated bounty list to gateway after expiry |

---

*Design by: Researcher (audit) + Architect (design)*  
*Ready for implementation starting at Phase 1.*
