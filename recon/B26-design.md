# Package E Design — Autocomplete Preload + Cache Framework (B.26)

**Status**: design-complete, ready for developer dispatch
**Author**: architect (design-only; no code written)
**Date**: 2026-04-29
**Companion docs**: `/proj/recon/B26-recon.md`, `DEFECTS.md` §B.26

---

## Goal

Eliminate per-keystroke HTTP traffic from the three autocomplete handlers whose
underlying data is either **STATIC** (game catalog, immutable between
re-seeds) or **SHOP-CACHED** (refreshed every 6 hours plus on-demand admin
refresh, mutated only by buy/sell transactions):

| # | Cog | Handler | Class | Calls/keystroke today |
|---|-----|---------|-------|----------------------|
| 16 | `adminCog` | `item_name_autocomplete` (line 1429) | STATIC | 1–4 |
| 17 | `adminCog` | `game_ship_autocomplete` (line 1454) | STATIC | 1 |
| 18 | `shopCog` | `buy_item_autocomplete` (line 239) | SHOP-CACHED | 2–5 |

After Package E lands, all three handlers serve from in-memory state with
zero HTTP calls per keystroke under steady-state conditions.

---

## Design summary (1 paragraph)

Introduce one small reusable cache helper, `AutocompleteCache`, in
`services/discord-gateway/src/cogs/_shared/autocomplete_cache.py`. It is a
key→value store with optional TTL and an optional async refresh callable.
Apply it three places: (1) `AdminCog` gets an item-catalog preload
(`_preload_item_catalog`) and a ship-catalog preload
(`_preload_ship_catalog`), wired alongside the existing
`_preload_render_settings`, both using `TTL=None` (never expire — STATIC).
(2) `ShopCog` gets a per-`(guild_id, tier)` shop cache populated lazily on
first autocomplete miss with `TTL=300s` (5 minutes), self-invalidated on
successful `/buy` and `/sell`. (3) `DevCog._reload_autocomplete` gains entries
for the new caches so admins can force a manual refresh. No cross-service
events, no bot-core changes — simple, local, reversible.

---

## Cache helper API

### Location

```
services/discord-gateway/src/cogs/_shared/autocomplete_cache.py
```

`_shared/` is the established home for cog-adjacent helpers (per
`services/discord-gateway/src/cogs/AGENTS.md` §"`_shared/` — Cog-Adjacent
Helpers"). The cog auto-loader does not descend into `_shared/`, so adding a
file there will not be misinterpreted as a cog.

### Public API (specification, not code)

```text
class AutocompleteCache[K, V]:
    """In-memory key→value cache with optional TTL and refresh callable.

    Typical uses:
      - Static preload (TTL=None, refresh_fn=None): set(...) at startup;
        get(...) reads forever; invalidate / clear available for manual reload.
      - Lazy TTL cache (TTL=300, refresh_fn=async_loader): get(...) returns
        a fresh value on miss/expiry by awaiting refresh_fn(key); subsequent
        hits within the TTL window are O(1) with no HTTP.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float | None = None,
        refresh_fn: Callable[[K], Awaitable[V]] | None = None,
        name: str = "autocomplete-cache",
    ) -> None: ...

    async def get(self, key: K) -> V | None:
        """Return the cached value, refreshing it via refresh_fn if missing or
        expired. Returns None on cache miss when no refresh_fn is configured,
        or when the refresh_fn raises and no prior value is cached.

        Stale-on-error policy: if refresh_fn raises and a previously-cached
        value exists, return that stale value and log WARNING. Better-stale-
        than-empty for an autocomplete UI.
        """

    def set(self, key: K, value: V) -> None:
        """Explicit set. Resets the per-entry timestamp. Used by startup
        preloads and by post-transaction refresh paths (if any). Synchronous
        because it does no I/O."""

    def invalidate(self, key: K) -> None:
        """Drop a single key. Idempotent. Used by /buy and /sell post-success
        hooks and by the manual /reload_autocomplete path."""

    def clear(self) -> None:
        """Drop everything. Used by /reload_autocomplete to force full reload."""

    def keys(self) -> list[K]:
        """Snapshot of current keys (for debugging / health endpoints)."""

    @property
    def size(self) -> int: ...
```

### Concurrency notes

- Discord.py runs the entire bot on a single asyncio event loop, so there is
  no real-thread contention. The only race is two coroutines calling
  `get(key)` on a cold cache simultaneously — both could fire `refresh_fn`.
- Mitigation: an `asyncio.Lock` per cache instance, acquired only inside the
  `get(...)` cold/expired path around the `refresh_fn` await + set. A single
  shared lock is acceptable for our scale (≤4 keys per cache, ≤handful of
  concurrent users); per-key locks are over-engineering and explicitly
  rejected. (Worst-case effect: a brief serialization on cold start; not
  observable to users.)
- All mutating operations (`set`, `invalidate`, `clear`) are synchronous
  dict ops and do not require the lock.

### Storage shape (internal, illustrative — not part of public API)

```text
self._store: dict[K, _Entry[V]]
class _Entry: value: V; stored_at: float (monotonic seconds)
```

Expiry check inside `get`:
`expired = ttl is not None and (monotonic() - entry.stored_at) > ttl`

### Imports / dependencies

- Stdlib only: `asyncio`, `time.monotonic`, `typing`.
- Logger: `from shared import bblogger`. Logger name pattern
  `discord-gateway-AutocompleteCache.<name>`.

---

## Per-handler implementation plan

### A. `adminCog.item_name_autocomplete` — STATIC preload

**Current** (`adminCog.py:1429–1452`): on every keystroke, loops over
`primary_weapon`, `secondary_weapon`, `turret_weapon`, `module` and issues
`GET /api/v1/data/{category}` per category, breaking early at 25 matches.

**Target**:

- Add to `AdminCog.__init__`:
  ```text
  self._item_catalog = AutocompleteCache[str, list[str]](ttl_seconds=None,
      name="adminCog-item-catalog")
  ```
  Keys are category names (`"primary_weapon"`, …); values are lists of item
  display names, sorted, lower-case-deduped at preload time.
- Add a new private method `_preload_item_catalog(self)` modeled exactly on
  the existing `_preload_render_settings` (`adminCog.py:78–95`):
  - `await self.bot.wait_until_ready()`
  - For each of the 4 categories, `GET /api/v1/data/{category}` with
    `timeout=10`. On success, call
    `self._item_catalog.set(category, [obj["name"] for obj in resp.json()])`.
  - Use the same exponential-backoff retry pattern documented in
    `bountyCog._preload_data` (5 attempts: 5s, 10s, 20s, 40s, 60s). Catch
    `httpx.TimeoutException`, `httpx.HTTPStatusError`, `httpx.RequestError`
    explicitly, then a broad `Exception` fallback. Log each retry at WARNING.
  - On terminal failure, leave the cache empty for that category — the
    existing `set(...)` of an empty list yields a graceful empty
    autocomplete.
- Schedule it from `__init__`:
  `bot.loop.create_task(self._preload_item_catalog())`. (Same call site
  pattern as the existing render-settings preload.)
- Rewrite `item_name_autocomplete` (lines 1429–1452) to read directly from
  `self._item_catalog`:
  - Determine which category to query from
    `interaction.namespace.item_type` (cog already does this on line 1432).
  - `cached = await self._item_catalog.get(category) or []`. With no TTL,
    `get` is effectively a synchronous dict lookup, but using `await` keeps
    the interface uniform and tolerates a future TTL switch.
  - Filter via `normalize_for_search(current)` (existing helper); cap at 25
    `app_commands.Choice` entries.
- **Bonus opportunity (in scope)**: `adminCog.player_ship_autocomplete`
  fallback (line 1696, recon §7.2) currently calls `GET /about/ships` per
  keystroke when no target user is selected. Once `_ship_catalog` exists
  (next handler), update that fallback to read from cache — single-line
  change, costs nothing extra.

### B. `adminCog.game_ship_autocomplete` — STATIC preload

**Current** (`adminCog.py:1454–1471`): single `GET /api/v1/about/ships` per
keystroke.

**Target**:

- Add to `AdminCog.__init__`:
  ```text
  self._ship_catalog = AutocompleteCache[str, list[str]](ttl_seconds=None,
      name="adminCog-ship-catalog")
  ```
  One conventional key: `"all"`. Value: list of ship-template names.
- Extend the same `_preload_item_catalog` method (rename to
  `_preload_static_catalogs` to keep one preload entry point) to also fetch
  `GET /api/v1/about/ships` and populate `self._ship_catalog.set("all",
  [s["name"] for s in resp.json()])`. Reuse the same retry / backoff /
  graceful-empty pattern.
- Rewrite `game_ship_autocomplete` to read from
  `await self._ship_catalog.get("all") or []`, filter by `current`, cap at 25.
- Update `player_ship_autocomplete` fallback branch as noted above.

### C. `shopCog.buy_item_autocomplete` — SHOP-CACHED with TTL

**Current** (`shopCog.py:239–264`): per keystroke, `POST /players/`
(resolve player tier) + `GET /shops/guild/{gid}/tier/{T}` for each
accessible tier from Bronze up to player tier (1–4 calls). Total: 2–5
HTTP calls per keystroke.

**Target — Option A (TTL-only, 5 minutes)**:

- Add to `ShopCog.__init__`:
  ```text
  self._shop_cache = AutocompleteCache[tuple[int, str], list[dict]](
      ttl_seconds=300.0,
      refresh_fn=self._fetch_tier_shop,
      name="shopCog-shop-cache",
  )
  ```
  Key: `(guild_id, tier)` — a 2-tuple of int and the canonical tier string
  (`"Bronze" | "Silver" | "Gold" | "Platinum"`). Value: list of shop-item
  dicts from `GET /api/v1/shops/guild/{gid}/tier/{T}` (already the cog's
  current API contract; keep the response shape as-is).
- Add private async method `_fetch_tier_shop(key)` that takes the
  `(guild_id, tier)` tuple and performs the existing single GET call (with
  `timeout=5`, matching current code). On non-200, return `[]` (graceful
  empty); the cache helper will store the empty list under the same TTL.
- Rewrite `buy_item_autocomplete`:
  - Continue to call `self._get_player_data(...)` live (1 HTTP call) to
    determine player tier — player tier is per-user state and not part of
    this design's caching scope (see "Out-of-scope decisions" #4).
  - For each accessible tier from Bronze up to player tier:
    `items = await self._shop_cache.get((interaction.guild_id, tier)) or []`
  - Filter `items` against `normalize_for_search(current)`, build choices,
    cap at 25 across all tiers (existing behavior).
- **Invalidation triggers (own-cog, no cross-service event)**:
  - In `shopCog.buy` (line 269), after `resp.raise_for_status()` on the
    purchase POST succeeds (lines 326–327), call
    `self._shop_cache.invalidate((interaction.guild_id, shop_item["tier"]))`.
    This drops the cache entry whose stock just decremented; the next
    autocomplete pulls fresh.
  - In `shopCog.sell` (line 414, the `/sell` slash command), after a
    successful sell response, invalidate
    `(interaction.guild_id, player["tier"])`. Sells deposit into the
    player's current tier shop (per A.42c convention in
    `cogs/AGENTS.md`), so that's the only tier whose inventory grows.
- **Lazy-load, not preload at startup**: shops are per-guild × per-tier, the
  bot may be in many guilds, and most guild shops will never be looked at.
  Lazy is strictly better than eager here.

**Net savings**: 2–5 HTTP calls per keystroke → 1 HTTP call (player) per
keystroke + amortized 1 fetch per `(guild, tier)` per 5 minutes. At recon's
load estimate (5 users × 10 interactions/hour × 8 keystrokes), shop-tier
fetches drop from ~960/hour to ~12–48/hour (one per `(guild, tier)` per
5-min window).

---

## Startup hook integration

Follow the established pattern documented in `cogs/AGENTS.md` §"Autocomplete
Pattern" and demonstrated by five existing preloads (`AboutCog`, `BountyCog`,
`DevCog`, `AdminCog._preload_render_settings`, `SkinsCog`):

1. Cache instances are created in `__init__`.
2. Each preload coroutine starts with `await self.bot.wait_until_ready()`.
3. The preload is scheduled via
   `bot.loop.create_task(self._preload_static_catalogs())` at the end of
   `__init__`. Non-blocking; cog construction returns immediately.
4. `bot.py` requires no changes — auto-discovery handles cog loading.

For `AdminCog` specifically, **prefer adding a single new method
`_preload_static_catalogs` that fetches both item categories and the ship
catalog**, scheduled with one `create_task` call. This keeps the existing
`_preload_render_settings` separate (different upstream service:
blender-service vs bot-core) and avoids tangling failure modes.

For `ShopCog`, **no new startup preload** — the cache is purely lazy /
TTL-driven. Only `__init__` changes (add the cache instance).

For `DevCog._reload_autocomplete` (lines 110–150 of `devCog.py`), update the
`targets` list to include three new entries:

| Target name | Action |
|---|---|
| `AdminCog._preload_static_catalogs` | re-await the preload |
| `BountyCog._preload_data` | already missing per recon §7.3 — add as part of this work |
| `AdminCog._preload_render_settings` | already missing per recon §7.3 — add |
| `ShopCog._shop_cache.clear()` | drop the entire shop cache; next autocomplete will lazy-refresh |

The DevCog spec change is small (3–4 list entries plus minor branching for
the cache-vs-method case). It is in scope for Package E.

---

## Failure mode policy

| Failure | Behavior | Justification |
|---|---|---|
| Preload fails at startup (network, 5xx from bot-core) | 5 retries with exponential backoff (5s, 10s, 20s, 40s, 60s); on terminal failure leave cache empty. Autocomplete returns empty list. | Mirrors `bountyCog._preload_data`. Empty dropdown is a clear UI signal that something is wrong; admin can `/reload_autocomplete` once the upstream is healthy. |
| Lazy refresh (`get` cold/expired) raises | Return last-known value if the cache holds one (stale-on-error); log WARNING with key + exception class. If no prior value, return `None`; caller treats as empty list. | Better-stale-than-empty for autocomplete UX. The user sees plausible options instead of a blank dropdown during a transient bot-core blip. The 5-minute TTL guarantees the stale value is at most 5 min + bot-core-outage-duration old. |
| `/buy` or `/sell` succeeds but invalidate raises | Suppress and log WARNING (the cache helper's `invalidate` is dict-pop, so the only failure mode is programmer error). Do not reflect to the user — the transaction itself succeeded. | Cache invalidation is a best-effort optimization; correctness is restored at most 5 minutes later via TTL. |
| `/reload_autocomplete` invoked while a lazy refresh is in flight | The clear/invalidate races with the in-flight `refresh_fn`. Acceptable: clear wipes the dict; the refresh finishes and re-stores; net effect is one extra fetch. The `asyncio.Lock` prevents inconsistent-state writes. | Operationally rare; never user-facing. |
| Bot-core returns 200 with empty body | Cache the empty list. Next refresh after TTL re-fetches. | Empty-shop-tier is a legitimate state (e.g., right after a refresh that produced no Bronze items); not an error. |

**Reuse, do not duplicate**, the existing exception-catching idiom from
`aboutCog._preload_data`:

```text
except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError, Exception):
    ...  # graceful degradation
```

---

## Test strategy

Two test layers. Total estimate: ~25–35 new tests.

### Layer 1 — unit tests for `AutocompleteCache`

File: `services/discord-gateway/tests/cogs/_shared/test_autocomplete_cache.py`

| # | Test | Asserts |
|---|---|---|
| 1 | `set` then `get` returns the value | basic round-trip |
| 2 | `get` on cold cache with no `refresh_fn` returns `None` | miss semantics |
| 3 | `get` on cold cache with `refresh_fn` invokes it and stores result | lazy-load |
| 4 | Second `get` within TTL does not call `refresh_fn` | hit semantics |
| 5 | `get` after TTL expiry calls `refresh_fn` again | TTL behavior (use `freezegun` or pass a `monotonic` injection — recommend the latter to avoid the dep) |
| 6 | `invalidate(key)` drops only that key; others remain | scoped invalidation |
| 7 | `clear()` drops all keys | nuke |
| 8 | `refresh_fn` raises with prior value cached → returns stale value, logs WARNING | stale-on-error policy |
| 9 | `refresh_fn` raises with no prior value → returns `None` | hard miss |
| 10 | TTL=None entries never expire even after long elapsed time | static-cache mode |
| 11 | Concurrent `get(key)` from two coroutines on a cold cache invokes `refresh_fn` exactly once | lock correctness |
| 12 | `keys()` and `size` reflect current state | observability |

Mocks: max 1 — the `refresh_fn` itself (a `MagicMock`/`AsyncMock`).
Everything else is real. Time injection via constructor parameter or
monkey-patching `time.monotonic` keeps the test deterministic.

### Layer 2 — cog integration tests

File `services/discord-gateway/tests/cogs/test_adminCog.py` (extend):

| # | Test | Asserts |
|---|---|---|
| 13 | `_preload_static_catalogs` populates `_item_catalog` for all 4 categories on success | preload happy path |
| 14 | `_preload_static_catalogs` populates `_ship_catalog["all"]` on success | preload happy path |
| 15 | Preload retries on transient HTTPStatusError (e.g., 503) and eventually succeeds | retry behavior |
| 16 | Preload terminal failure leaves caches empty, no exception bubbles | graceful degrade |
| 17 | `item_name_autocomplete` after preload returns choices without making any HTTP call | the actual fix |
| 18 | `game_ship_autocomplete` after preload returns choices without making any HTTP call | the actual fix |
| 19 | `item_name_autocomplete` filtering by `current` substring works | preserves UX |
| 20 | `player_ship_autocomplete` fallback branch reads from `_ship_catalog`, no HTTP | bonus fix verification |

File `services/discord-gateway/tests/cogs/test_shopCog.py` (extend):

| # | Test | Asserts |
|---|---|---|
| 21 | `buy_item_autocomplete` cold cache fetches once per accessible tier; second invocation uses cache (zero new HTTP) | lazy-load + hit |
| 22 | `buy_item_autocomplete` after TTL expiry refetches | TTL |
| 23 | `buy` success invalidates the purchased item's tier cache only | scoped invalidate |
| 24 | `sell` success invalidates the seller's tier cache only | scoped invalidate |
| 25 | `buy_item_autocomplete` returns empty list when player resolution fails | unchanged behavior |
| 26 | `buy_item_autocomplete` shows items from all accessible tiers up to player tier (Silver player sees Bronze + Silver) | unchanged behavior |

File `services/discord-gateway/tests/cogs/test_devCog.py` (extend):

| # | Test | Asserts |
|---|---|---|
| 27 | `/reload_autocomplete` invokes `AdminCog._preload_static_catalogs` | reload coverage |
| 28 | `/reload_autocomplete` clears `ShopCog._shop_cache` | reload coverage |
| 29 | `/reload_autocomplete` invokes `BountyCog._preload_data` and `AdminCog._preload_render_settings` (recon §7.3 gap) | reload coverage |

Per project standard (`cogs/AGENTS.md`): **max 2 mocks per test**. The cog
integration tests should mock the `httpx.AsyncClient.get/post` methods and
nothing else; cache, interaction, and choices are all real objects.

---

## Backward compatibility

- **Public Discord-facing surface** — slash command names, parameter names,
  parameter types, autocomplete display labels, and ordering: **unchanged**.
- **Autocomplete callable signatures** —
  `(self, interaction: discord.Interaction, current: str) -> list[Choice]`:
  **unchanged**.
- **HTTP API contracts** — no bot-core or blender-service endpoint changes.
  Gateway-internal REST API: no changes.
- **Database schema** — no changes. No migration needed.
- **Configuration / env vars** — no new variables. (TTL is a code constant in
  `shopCog.py`. If runtime tunability is later wanted, that's a separate
  enhancement.)
- **Dependencies** — no new packages; stdlib + existing httpx.
- **Tests** — existing tests must continue to pass; new behavior is additive.

---

## Out-of-scope decisions

These were considered and explicitly excluded from Package E scope. Each is
listed with its trade-off so the rationale is preserved for future work.

1. **Option B — bot-core publishes shop-refresh event to gateway.** Rejected
   for Package E. Adds a new cross-service contract (REST endpoint or shared
   bus), requires bot-core changes, and the staleness window we'd close is
   only the 5-minute TTL — not worth the coupling. Can be added later as a
   targeted enhancement if 5-min staleness ever proves user-visible.

2. **Option C — very-short TTL (10–30 sec).** Rejected. At 30s TTL, savings
   drop substantially (~120 fetches/hour per (guild,tier) instead of 12) for
   negligible UX improvement. 5 min is the right balance.

3. **Sharing the system catalog between `aboutCog` and `bountyCog`** (recon
   §7.1). Rejected for Package E — both already preload at startup, the
   savings is one HTTP call at boot, and the refactor would touch both cogs.
   File a separate small ticket if pursuing.

4. **Caching the player-tier resolution** (`POST /players/`) used by
   `buy_item_autocomplete`. Rejected for Package E. Player tier rarely
   changes but is per-user state with prestige/tier-up side effects;
   incorrect caching here could mislead a tiered-up player. Keep live (1
   HTTP call). Net savings (2–5 → 1) is already an 80% reduction; chasing
   that last call is not worth the staleness risk.

5. **TTL caching for `schedulerCog.job_id_autocomplete`** (recon §1.6).
   Rejected for Package E. Admin-only, low-frequency. Once the framework
   exists, adding a 5-minute cache is a one-liner — defer to a follow-up if
   anyone notices.

6. **Per-key locking on `AutocompleteCache`.** Rejected. Single shared
   `asyncio.Lock` is sufficient at our scale (≤4 keys per cache, low
   concurrency). Per-key locks add code without meaningful benefit.

7. **External cache (Redis) or shared-state cache between gateway processes.**
   Rejected. The gateway is currently a single-process service. If horizontal
   scaling is ever introduced, this design needs revisiting — but that is
   not on any roadmap as of 2026-04-29.

8. **Persisting cache across restarts.** Rejected. Preload at startup is
   already cheap (≤5 HTTP calls total at boot for the new caches);
   persistence would add complexity for no gain.

---

## Implementation order for developer dispatch

The developer should implement in this order; each step ends in a fully
testable / commitable state.

1. **Create `AutocompleteCache`** at
   `services/discord-gateway/src/cogs/_shared/autocomplete_cache.py` with
   the API specified above. Add unit tests (Layer 1, tests #1–12). All tests
   must pass before moving on.

2. **Wire AdminCog static catalogs**: add `_item_catalog` and `_ship_catalog`
   instances to `__init__`; add `_preload_static_catalogs`; schedule via
   `bot.loop.create_task`; rewrite `item_name_autocomplete` and
   `game_ship_autocomplete` to read from the caches; update
   `player_ship_autocomplete` fallback branch. Extend test_adminCog (tests
   #13–20). Verify zero HTTP calls on autocomplete after preload.

3. **Wire ShopCog shop cache**: add `_shop_cache` to `__init__` with
   `_fetch_tier_shop` as `refresh_fn`; rewrite `buy_item_autocomplete` to
   loop over tiers calling `_shop_cache.get`; add `invalidate` calls in
   `buy` and `sell` post-success paths. Extend test_shopCog (tests
   #21–26).

4. **Update DevCog `/reload_autocomplete`** targets list to include the new
   cache reset paths plus the recon §7.3 gaps (`BountyCog._preload_data`,
   `AdminCog._preload_render_settings`). Extend test_devCog (tests
   #27–29).

5. **Manual smoke test in dev guild** `1490693399307616276`:
   - Restart stack; observe preload INFO logs in `bountybot-discord-gateway`.
   - Run `/admin_give_item` and verify autocomplete is instant.
   - Run `/admin_give_ship` and verify autocomplete is instant.
   - Run `/buy` twice in quick succession; confirm via discord-gateway logs
     that the second invocation hits the cache (no `GET /shops/...` calls).
   - Run `/buy` to actual purchase, then `/buy` again; verify cache was
     invalidated for that tier.
   - Run `/dev reload_autocomplete`; verify caches reload cleanly.

6. **Update `DEFECTS.md` B.26** with a "FIXED in commit `<sha>` (Package E,
   <date>)" annotation, per project convention.

7. **No AGENTS.md changes required**, but a short note may optionally be
   added to `services/discord-gateway/src/cogs/_shared/__init__.py`'s
   docstring or to `cogs/AGENTS.md` §"Autocomplete Pattern" pointing future
   readers at `AutocompleteCache` as the canonical implementation.

---

## File-by-file change estimate

| File | Action | Approx LOC delta | Notes |
|---|---|---|---|
| `services/discord-gateway/src/cogs/_shared/autocomplete_cache.py` | NEW | +120 | The cache helper itself, with docstrings. |
| `services/discord-gateway/src/cogs/adminCog.py` | EDIT | +60 / −20 | Two new cache fields, new `_preload_static_catalogs` method (~40 LOC mirroring `_preload_render_settings`), rewritten `item_name_autocomplete` (~10 LOC), rewritten `game_ship_autocomplete` (~5 LOC), one-line fallback fix in `player_ship_autocomplete`. |
| `services/discord-gateway/src/cogs/shopCog.py` | EDIT | +35 / −15 | One cache field, one `_fetch_tier_shop` helper (~10 LOC), rewritten `buy_item_autocomplete` (~15 LOC), two `invalidate` calls in `buy` and `sell`. |
| `services/discord-gateway/src/cogs/devCog.py` | EDIT | +15 | Extend `targets` list with 3–4 new entries; tiny branch to handle the `cache.clear()` case alongside method-call cases. |
| `services/discord-gateway/tests/cogs/_shared/test_autocomplete_cache.py` | NEW | +250 | Unit tests #1–12. |
| `services/discord-gateway/tests/cogs/test_adminCog.py` | EDIT | +200 | Tests #13–20. |
| `services/discord-gateway/tests/cogs/test_shopCog.py` | EDIT | +180 | Tests #21–26. |
| `services/discord-gateway/tests/cogs/test_devCog.py` | EDIT | +60 | Tests #27–29. |
| `DEFECTS.md` | EDIT | +1 | FIXED annotation on B.26. |

**Total**: 1 new helper module + 1 new test module + 6 edited files.
**Estimated developer time**: 0.5–1 day for an engineer familiar with the
discord-gateway codebase. No infrastructure work, no migrations, no
cross-service coordination.
