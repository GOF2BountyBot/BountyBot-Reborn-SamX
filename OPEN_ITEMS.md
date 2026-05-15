# BountyBot Open Items

Last updated: 2026-05-15

---

## 1. Test Quality / CI

| ID | Sev | Summary | Notes |
|----|-----|---------|-------|
| DEF-S11-003 | 🔵 | 285 bare `assert_called_once()` patterns remain across test suites | Verify none are the sole assertion in their test; delete or replace any that are |

---

## 2. Production Defects — Open

| ID | Sev | Summary | Notes |
|----|-----|---------|-------|
| B.82 | 🔵 | Combat summary embed should surface the PvC armour buff (Keith T Maxwell bonus) | Add `pvc_armour_buff_applied: bool` + `pvc_armour_buff_factor: float` to `/combat-bonus` response (`bot-core/src/api/routers/bounties.py`); read in `bountyCog.py` combat summary embed builder |
| B.67 | 🔵 | `duel_expire` executor has no bulk sweep mode — requires `duel_id` in payload; firing without one returns error and does nothing | Option A: add bulk mode when `duel_id` omitted (expire all past `expires_at`) in `bot-core/src/utils/executors/duel_expire_executor.py`. Option B: document the limitation. |
| B.63 | 🟡 | Duel result embed is ambiguous when both players use the same ship model (both show e.g. "Betty") | Blocked by B.62. Files: `bot-core/src/api/routers/duels.py`, `discord-gateway/src/cogs/duelCog.py` |
| B.62 | 🟡 | No `display_name` column — all player-facing name fields show `discord_username` (e.g. `samx.ai`) instead of display name (e.g. `SamAccountX`) | Requires: Alembic migration adding `display_name: str \| None` to `users` table; populate on `/register` from `interaction.user.display_name`; update all name-resolution in cogs and bot-core |

---

## 3. Production Defects — Closed

Fixed-in-code items are treated as closed. Live re-test is confirmatory only.

| ID | Sev | Summary | Evidence |
|----|-----|---------|---------|
| B.86 | 🟠 | Bounty 546 post-mortem: `/promote` mid-tier combat loss reset route to all-`-1`, player soft-locked on "No Bounty" until expiry | Fixed on branch `feat/promote-flow-correctness`: promote/demote ConfirmView flow + 24h tier-change cooldown + forfeit sentinel `-2` + strict same-tier shop + 20-sim combat preflight. Commits `8c6437b`..`b1448bd` |
| B.85 | 🔵 | No distinct `WRONG_TIER` result for `/check` against a bounty outside player's tier | Won't-fix: bounty routes only live while the bounty is active, and `/promote` forfeit-scrub already clears tier-mismatched references |
| B.87 | 🔵 | No credit refund for forfeited checks on `/promote` | Won't-fix (testing context): affected player already granted 1,000,000 cr quick-start |
| B.80 | 🔵 | `/admin_give_item` `item_type` param removed | `adminCog.py:1732-1802` |
| B.77 | 🟡 | A* heuristic → `0.0` constant (Dijkstra) | `pathfinding_service.py:59-65` |
| B.74 | 🟠 | AEI dimension snapped to nearest 4px | `aei_conversion_service.py:98-104` |
| B.73 | 🟡 | skinsCog preload retry `[5,10,20,40,60]s` | `skinsCog.py:261-304` |
| B.59 | 🔵 | `CombatBonusRequest.base_reward` missing `Field(ge=0)` guard | Fixed: `bounty_schema.py:127` — `base_reward: int = Field(ge=0, ...)` |
| B.58 | 🟡 | `combat_bonus` silently returned `won=True, bonus_credits=0` when player not found | Fixed: `bounties.py:200-203` — 404 guard added before loadout build |
| B.57 | 🟠 | PvC armour buff + unified PvP/PvC fight path | `combat_service.py`, `game_constants.py` |
| B.55 | 🟠 | Duel accept uses `varied_hp` (not broken `challenger_health`) | `duels.py:199,219-220` |
| B.53 | 🟠 | Prestige swaps tier roles (removes old, adds Bronze) | `playerCog.py:368-413` |
| B.52 | 🟡 | Criminal ship selection filters `max_primaries > 0` | `bounty_service.py:368,379` |
| B.51 | 🟠 | duelCog resolves Discord IDs → player PKs via `_get_player_id()` | `duelCog.py:155-184` |
| B.48 | 🟠 | Prestige resets to starter Betty; level/division system deleted | `player_service.py:336-447` |
| B.63 | 🟡 | Duel result embed shows player names not ship names | `duelCog.py:368-372` |
| B.61 | 🔵 | Accept embed includes `target_name` | `duels.py:51-53` |
| B.60 | 🔵 | Duel autocomplete shows challenger/target name not duel ID | `duelCog.py:88-91` |
| B.39 | 🟡 | `/promote` removes old tier role when adding new one | `playerCog.py:488-533` |
| DEF-S11-004 | 🔵 | `test_setupCog.py` — F401 (`discord` imported but unused) | Verified clean: `ruff check` passes with no errors |

---

## 4. Enhancements / Future Work

| ID | Sev | Summary | Notes |
|----|-----|---------|-------|
| ENH-01 | 🔵 | Shop item TNN re-indexing — replace global auto-increment IDs with human-readable `TNN` codes (`T`=tier digit, `NN`=position within tier, e.g. `105` = Bronze item #5) | **Tabled for later.** Fully scoped and designed — see Appendix A. Prerequisite for the per-tier shop announcement (ENH-02) showing actionable codes. Recommended implementation: display-layer only (no DB migration). ~6h effort. Requires coordinated changes in `shopCog.py`, `shops.py` router, `shops_schema.py`, and a new `shop_tnn.py` helper. Also requires a new gateway `PUT /api/v1/cache/shop/{guild_id}/{tier}` push endpoint and a per-user tier cache to hit the ≤100ms autocomplete budget — see Appendix B. |
| ENH-02 | 🔵 | Per-tier shop refresh announcement — post new inventory embed to shop channel for each tier refreshed, with tier-specific color and bold header | **Tabled for later** (depends on ENH-01 for TNN codes in the embed). Fully scoped and designed — see Appendix A. ~2h effort once ENH-01 is done. Changes: `shop_refresh_executor.py` (per-tier loop), `shop_announcement.py` (inventory embed builder + tier colors). No DB changes. |
| CI-01 | 🔵 | Extend GitHub Actions workflow to pre-build and push all service images to GHCR | Build 4 images: `bot-core`, `discord-gateway`, `blender-service` (CUDA), `blender-service` (non-CUDA / CPU-only). Tag by commit SHA + `latest`. Use GHCR (free, no pull limits, no inactive expiry). Also review rebasing `bot-core` and `discord-gateway` base images to something slimmer (e.g. `python:3.12-slim`) to reduce layer size. **Requires a local dev story**: `docker-compose.yml` must remain usable for fully local builds without needing GHCR credentials — likely via `docker-compose.override.yml` or a `--profile` split. |
| B.88 | 🔵 | `/admin_config_constants` UX is rough — needs redesign | Flagged during `feat/promote-flow-correctness` work; deferred out of that PR. Constant-editing admin flow suffers param sprawl and poor discoverability — wants a usability pass (grouped categories, search/autocomplete, clearer reset semantics). |
| B.84 | 🔵 | Prefix command support for high-use commands (mobile / legacy Discord client compatibility) | For users on mobile using 3rd-party Discord apps that don't support slash commands. **Approach**: Separate prefix layer (Approach B from researcher report in `activity.md`) — add `@commands.command()` wrappers alongside existing slash commands, sharing business logic via private `_do_x()` methods. **Scope**: High-use candidates only — suggested starters: `!check <system>`, `!bounties`, `!duel <@user> [stakes]`, `!buy <item_id> <qty>`, `!sell <item_name> <qty>`, `!profile`, `!inventory`. **Hard/skip**: `/equip`, `/ship`, `/setactive`, `/route` (exotic system names with accented characters — worse UX without autocomplete), all skins commands. **Required supporting work**: (1) `!help` — lists all available prefix commands with one-line descriptions; (2) `!help <command>` — shows syntax, arguments, and an example; (3) Per-guild configurable prefix stored in `guild_configs` — default `!` is likely already in use by other bots; expose via `/admin_config` or new `guild_configs.prefix` column + Alembic migration; `GatewayBot` must use `get_prefix()` async callable instead of static string. **Admin check prerequisite**: `is_admin()` currently only handles `discord.Interaction` — needs refactor to also accept `commands.Context`. `message_content` intent and `commands.Bot` base class are already correctly configured. |

### Resolved Enhancements

| ID | Sev | Summary | Evidence |
|----|-----|---------|---------|
| B.83 | 🟢 | Intermittent: expired bounties occasionally not auto-cleaned | **Fixed**: Added `bounty_failsafe_cleanup_executor` — hourly Discord-first sweep; classifies each channel post against DB (live/expired/captured/orphan); deletes stale posts + DB records. Registered as `bounty_failsafe_cleanup_default` cron at :30 past every hour. 17 tests. |
| B.50 | 🟢 | Replace typed `CONFIRM` strings with button dialogs (prestige/uninstall/clear-bounties) | **Fixed**: `ConfirmView` implemented and wired to all three flows. Committed `6e3edfa`. |
| B.49 | 🟢 | Per-guild game constants (audit hardcoded values for configurability) | **Fixed**: 25 new nullable `GuildConfig` columns, `resolve_constant()` helper, Alembic migration `0005`, 3 new admin slash commands (`/admin_config_constants`, `/admin_config_constants_view`, `/admin_config_constants_reset`), 67 new tests. Committed `6e3edfa`. |

---

## 5. Stack Rebuild Checklist

When the stack is next rebuilt:

- [ ] Update `GAME_OBJS_FILEID` in `.env` — archive ID `1oGwq6fm4OwGAYvwG94hEEVGt5e7a5Z1_` (already set)
- [ ] Bot invite URL: `https://discord.com/oauth2/authorize?client_id=1379827884851593256&permissions=2416438320&integration_type=0&scope=bot`
- [ ] Bot must join server **before** `/admin_setup` so game roles land below bot's managed role
- [ ] Run `/admin_setup` → `/profile` both accounts → reseed ships → apply golden config below

### Golden Config Commands (run after `/admin_setup` + `/profile`)

```bash
GID=1490693399307616276

# Reseed ships (required after every rebuild — applies skinnable fixes)
sudo docker exec bountybot-bot-core curl -s -X POST http://localhost:8000/api/v1/data/ship

# Compressed XP thresholds (Silver:10, Gold:20, Platinum:30, Prestige:50)
sudo docker exec bountybot-bot-core curl -s -X PUT -H 'Content-Type: application/json' \
  "http://localhost:8000/api/v1/config/guild/$GID/xp-thresholds" \
  -d '{"guild_id":'$GID',"thresholds":{"Silver":10,"Gold":20,"Platinum":30,"Prestige":50}}'

# Compressed bounty timers (spawn 5min, expire 10min, max 20 per tier)
sudo docker exec bountybot-bot-core curl -s -X PUT -H 'Content-Type: application/json' \
  "http://localhost:8000/api/v1/config/guild/$GID/bounty" \
  -d '{"guild_id":'$GID',"bounty_spawn_interval_minutes":5,"bounty_expiry_minutes":10,"max_bounties_per_tier":{"bronze":20,"silver":20,"gold":20,"platinum":20}}'
```

### Frequent DB / API Queries

```bash
# Player state
sudo docker exec bountybot-db psql -U bounty -d bountydb -c \
  "SELECT p.id, u.discord_username, p.tier, p.xp, p.credits, p.prestige_count FROM players p JOIN users u ON p.user_id = u.id ORDER BY p.id;"

# Guild config
sudo docker exec bountybot-bot-core curl -s http://localhost:8000/api/v1/config/guild/1490693399307616276 | python3 -m json.tool

# Active bounties
sudo docker exec bountybot-db psql -U bounty -d bountydb -c \
  "SELECT id, division, criminal_name, reward, status FROM bounty WHERE guild_id=1490693399307616276 AND status='active' LIMIT 10;"

# Skinnable ships
sudo docker exec bountybot-db psql -U bounty -d bountydb -c \
  "SELECT name, skinnable, texture_regions FROM ship WHERE skinnable=true ORDER BY texture_regions, name;"
```

---

## 6. Account Context

| Account | Username | Discord ID | Notes |
|---------|----------|------------|-------|
| Main | `samx.ai` / `SamAccountX` | `402296276617527306` | In `DEVELOPERS` env var — super-admin for scheduler/dev commands |
| Alt | `general_failure.` | `970691862035841048` | Has `@Bounty Bot Admin` role |

**Bot user**: `1379827884851593256` (BountyBot-SamX)

---

## 7. Architecture Reference

### Bot Discord Permissions
- **Permissions integer**: `2416438320` (minimum required set)
- **Includes**: VIEW_CHANNEL, SEND_MESSAGES, MANAGE_MESSAGES, EMBED_LINKS, ATTACH_FILES, READ_MESSAGE_HISTORY, MENTION_EVERYONE, USE_EXTERNAL_EMOJIS, MANAGE_CHANNELS, MANAGE_GUILD, MANAGE_ROLES, USE_APPLICATION_COMMANDS

### Super-Admin Gate
- `_check_is_super_admin()` / `is_super_admin()` in `adminCog.py`
- Checks `DEVELOPERS` env var only — no role fallback, no Discord Administrator fallback
- Applied to: all 6 schedulerCog commands + 2 devCog commands (`/load_data`, `/reload_autocomplete`)

### Preload Retry Pattern (standard for all cogs)
```python
delays = [5, 10, 20, 40, 60]
for attempt, delay in enumerate(delays, start=1):
    try:
        flogger.info("XxxCog: Starting preload (attempt %d/%d)...", attempt, len(delays))
        # ... fetch ...
        return
    except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as e:
        flogger.warning("XxxCog: Attempt %d/%d failed: %s — retrying in %ds", attempt, len(delays), e, delay)
        await asyncio.sleep(delay)
    except Exception as e:
        flogger.warning("XxxCog: Unexpected error attempt %d/%d: %s", attempt, len(delays), e)
        await asyncio.sleep(delay)
flogger.error("XxxCog: All preload attempts exhausted.")
```

### Ship Skinning Data Model
- `skinnable=true` + `compatibleSkins` populated → static image support via `/ship_skin`
- `skinnable=true` + `compatibleSkins={}` → Blender render only (Phantom XT, Salvéhn)
- `texture_regions` → number of mask regions for Blender compositing (1, 2, or 3)
- All 65 ships have `skinnable=true`

### GDrive Asset Archive
- **File ID**: `1oGwq6fm4OwGAYvwG94hEEVGt5e7a5Z1_` — 15.2GB upscaled assets (set in `.env`)
- Archive must contain folder named `"game objects"` (with space) — entrypoint renames to `game-objects`
- Entrypoint skips download if `.bmp` or `.jpg` already found in target dir

### Loadout Consistency Equip Guard (B.41)
- `loadout_consistency_service.equip_one()` raises `ValueError("No unequipped copies remain")` when all copies already equipped across ships
- `inventoryCog` equip autocomplete filters out fully-equipped items (`qty > 0` in cargo)

---

## Appendix A — Shop TNN Re-indexing & Per-Tier Announcement (ENH-01 / ENH-02)

*Researched and designed 2026-05-15. Tabled pending prioritization.*

### A.1 Feature Descriptions

**ENH-01: TNN Shop Codes**
Replace `ID: {global_auto_increment}` in the `/shop` embed and `/buy` parameter with a human-readable tier-position code:
- Format: `TNN` where `T` = tier digit (1=Bronze, 2=Silver, 3=Gold, 4=Platinum), `NN` = 1-based position within that tier (zero-padded, e.g. `05`)
- Examples: `/buy item: 105` = Bronze item #5; `/buy item: 310` = Gold item #10
- Codes are **ephemeral** — they change every shop refresh. Guild is implicit from the Discord channel (existing pattern).
- Encoding: `code = T * 100 + NN`, range `[101, 499]`. Parse: `T = code // 100`, `NN = code % 100`.

**ENH-02: Per-Tier Shop Refresh Announcement**
After each shop refresh, post one embed per tier to the guild's shop channel showing the new inventory with tier-specific color, bold header, and item listings. Depends on ENH-01 so listings show TNN codes players can immediately use with `/buy`.

### A.2 Current State

- `guild_shops.id` is a global auto-increment integer PK. No position/slot concept exists.
- `/shop` embed shows `ID: {raw_global_id}`. Footer says "Use /buy <item_id>".
- `/buy` slash command takes `item_id: int` (the raw PK), posted directly to `POST /api/v1/shops/purchase`.
- Shop refresh (`shop_refresh_executor.py`) posts a single generic "Shop Refreshed!" announcement per guild (all tiers, no inventory listing, hardcoded blue color).
- No tier→color mapping exists anywhere in the codebase.

### A.3 ENH-01 Design (Display Layer Only — No DB Migration)

**Recommended approach**: compute position dynamically at API response time. No schema changes.

**Canonical sort key** (update `shop_repository.get_shop_items`):
```python
ORDER BY item_type ASC, item_name ASC, id ASC   # id as tiebreaker
```

**New shared helper** — create in both services (duplicate, ~10 lines each):
- `services/bot-core/src/utils/shop_tnn.py`
- `services/discord-gateway/src/utils/shop_tnn.py`

```python
TIER_DIGITS = {"Bronze": 1, "Silver": 2, "Gold": 3, "Platinum": 4}
DIGIT_TIERS = {v: k for k, v in TIER_DIGITS.items()}

def canonical_sort_key(item) -> tuple:
    return (item.item_type, item.item_name, item.id)

def encode_tnn(tier: str, position: int) -> str:
    return f"{TIER_DIGITS[tier]}{position:02d}"

def assign_positions(items: list) -> list[tuple[int, object]]:
    return list(enumerate(sorted(items, key=canonical_sort_key), start=1))
```

**Schema additions** (`shops_schema.py` — additive, no breaking change):
```python
position_within_tier: int   # raw 1-based ordinal
tnn_code: str               # pre-formatted "105" for direct embed use
# Also add: model_config = ConfigDict(from_attributes=True)  ← pre-existing violation
```

**Router change** (`shops.py` — `get_shop_items`): sort items, assign positions, populate new fields before building `ShopItemResponse`.

**Cog changes** (`shopCog.py`):
- `/shop` embed: replace `ID: {item['id']}` with `Code: {item['tnn_code']}`; update footer text
- `/buy` parameter: remains `item_id: int`; cog validates `101 ≤ code ≤ 499` and `code % 100 ≥ 1`; decodes `T` and `NN`; resolves TNN → global PK via cache (see A.5); rejects invalid codes with friendly error
- Autocomplete `buy_item_autocomplete`: change `Choice.value` from `item["id"]` to TNN integer (so typed and autocomplete paths share one decode path)
- Hard-cut on legacy raw IDs: reject `code > 499` with "codes changed — re-run /shop" message. No transition period needed (codes are ephemeral anyway).
- Add DM-context guard: `if interaction.guild_id is None: return ephemeral error`

**Tier cross-check improvement**: decode `T` from the typed TNN first; if `decoded_tier ≠ player.tier`, reject immediately (no HTTP round-trip needed — improves on current behavior).

### A.4 ENH-02 Design (Per-Tier Announcement)

**Tier → embed color mapping** (add to `shop_announcement.py`):
```python
_TIER_COLORS = {
    "bronze":   13467442,  # #CD7F32
    "silver":   12632256,  # #C0C0C0
    "gold":     16766720,  # #FFD700
    "platinum": 15066082,  # #E5E4E2
}
_DEFAULT_SHOP_COLOR = 3447003  # #3498DB — fallback
```

**`announce_shop_refresh` extended signature** (backwards-compatible):
```python
async def announce_shop_refresh(
    caller_label: str, guild_id: int, channel_id: int | None,
    bounty_hunter_role_id: int | None = None,
    tier: str | None = None,
    items: list | None = None,      # NEW — list[GuildShop] ORM objects
    tech_level: int | None = None,  # NEW — shown in embed title
) -> None:
```

**Embed structure per tier** (when `items` provided):
- Title: `"🛒 {Tier} Shop Refreshed — Tech Level {N}"`
- Color: tier-specific from `_TIER_COLORS`
- One field per item type (Ships / Primary Weapons / Secondary Weapons / Turret Weapons / Modules) — omit empty groups
- Each item line: `` `{tnn_code}` — {item_name} — {price:,}c (x{quantity}) ``
- Field value truncated to 1024 chars with `"… and N more"` suffix if needed
- Footer: `"Use /buy <code> to purchase · /shop to browse"`

**Executor change** (`shop_refresh_executor.py`):
- Hoist `shop_channel_id` and role resolution above the tier loop (one check per guild, not four)
- Call `_announce_shop_refresh(...)` once per tier **inside** the loop, passing `tier=t`, `items=result["items"]`, `tech_level=result["tech_level"]`
- Pass role mention only on first tier (Bronze) — avoid 4 pings per refresh cycle
- Remove the existing single end-of-loop `_announce_shop_refresh(tier=None)` call

**Zero-item tier**: still post, description `"The {tier} shop refreshed but no items are currently stocked."` — no item fields but still tier-colored.

### A.5 Autocomplete Performance — Multi-Guild Caching (Critical Prerequisite)

**Hard requirement**: autocomplete must respond in ≤100ms (target ~50ms), zero I/O on the hot path.

**Current state**: the `AutocompleteCache` in `shopCog` fires HTTP calls on cold miss, AND `buy_item_autocomplete` calls `_get_player_data()` (a player upsert POST) on **every keystroke**. The player upsert alone consumes 20–80ms. The current implementation cannot meet the ≤100ms requirement.

**Three changes required** (all must land with ENH-01):

**Change 1 — Pre-computed immutable snapshots**
Replace raw dict caching with a frozen, slotted `ShopItemView` dataclass that pre-computes `label` and `label_norm` (NFKD-normalized search string) at snapshot-build time. The autocomplete loop becomes 50 substring comparisons — ~0.1ms.

New file: `services/discord-gateway/src/cogs/_shared/shop_cache.py`
```python
@dataclass(frozen=True, slots=True)
class ShopItemView:
    item_id: int        # global PK — used as Choice.value (TNN int after ENH-01)
    item_name: str
    price: int
    item_type: str
    tech_level: int | None
    quantity: int
    label: str          # pre-built display string, capped at 100 chars
    label_norm: str     # normalize_for_search(label), cached once

ShopTierSnapshot = tuple[ShopItemView, ...]  # immutable, pre-sorted by price asc
```
Add a `peek(key) -> V | None` synchronous method to `AutocompleteCache` (dict lookup only — no TTL check, no refresh_fn call).

**Change 2 — Push-on-refresh (bot-core → gateway)**
New gateway endpoint: `PUT /api/v1/cache/shop/{guild_id}/{tier}` — accepts the new item list from bot-core, builds a `ShopTierSnapshot`, stores it in-memory via `cog.push_tier_snapshot(...)`.

New files:
- `services/discord-gateway/src/api/routers/cache.py` (PUT/DELETE/GET endpoints)
- `services/discord-gateway/src/api/schemas/cache_schemas.py` (`ShopCachePushRequest`)

`shop_refresh_executor.py` adds `_push_tier_cache(guild_id, tier, items)` helper — called after each tier refresh, all 4 tiers gathered in parallel via `asyncio.gather`. Non-fatal (same try/swallow pattern as `_announce_shop_refresh`).

**Drop the 5-minute TTL** — event-driven invalidation only (refresh push + post-purchase `invalidate()`). A time-based TTL now only creates guaranteed cold-path events with no correctness benefit.

**Change 3 — Per-user tier cache (60s TTL)**
```python
self._player_tier_cache: AutocompleteCache[tuple[int, int], str] = AutocompleteCache(
    ttl_seconds=60.0, refresh_fn=self._fetch_player_tier, name="shopCog-player-tier"
)
```
First keystroke in a 60s window pays one HTTP call; all remaining keystrokes are free. Cold miss: return `[]` + background `asyncio.create_task` warm.

**Resulting hot path** (warm caches, zero I/O):
```python
async def buy_item_autocomplete(self, interaction, current):
    if interaction.guild_id is None: return []
    tier = self._player_tier_cache.peek((gid, uid))       # dict lookup
    if tier is None: asyncio.create_task(warm_tier...); return []
    snapshot = self._shop_cache.peek((gid, tier))          # dict lookup
    if snapshot is None: asyncio.create_task(warm_shop...); return []
    # 50-element substring scan, ~0.1ms
    norm = normalize_for_search(current) if current else ""
    return [Choice(name=v.label, value=v.item_id) for v in snapshot
            if not norm or norm in v.label_norm][:25]
```

**Memory**: ~80 KB/guild (4 tiers × 50 items × ~395 bytes). At 1,000 guilds ≈ 80 MB — within budget.

**Cold path behavior**: return `[]` + schedule one background warm task. User's first character shows no suggestions for ~150ms; every subsequent character hits warm cache. No thundering-herd on bot restart.

### A.6 Implementation Order (across ENH-01, ENH-02, and autocomplete fix)

1. **Phase A — TNN Foundation**: `shop_tnn.py` helpers; `ORDER BY ... id` tiebreaker in repo; `position_within_tier` + `tnn_code` in schema/router; tests
2. **Phase B — Cog Migration**: `/shop` display, `/buy` TNN decode + cache resolution, autocomplete value format, DM guards, edge case tests
3. **Phase C — Autocomplete Performance** (must land with Phase B): `ShopItemView` + `peek()`; push endpoint; player tier cache; remove TTL; executor push calls
4. **Phase D — Announcement** (ENH-02): refactor `shop_announcement.py` to accept `items` + render TNN lines; per-tier executor loop; tier colors

### A.7 Files Impacted

| File | Change | Feature |
|------|--------|---------|
| `bot-core/src/persist/repositories/shop_repository.py` | Add `id` tiebreaker to `ORDER BY` | ENH-01 |
| `bot-core/src/api/schemas/shops_schema.py` | Add `position_within_tier`, `tnn_code`; fix `ConfigDict` | ENH-01 |
| `bot-core/src/api/routers/shops.py` | Compute positions in `get_shop_items` response | ENH-01 |
| `bot-core/src/utils/shop_tnn.py` | NEW — `encode_tnn`, `assign_positions`, `canonical_sort_key` | ENH-01 |
| `bot-core/src/utils/executors/shop_refresh_executor.py` | Per-tier announce loop; `_push_tier_cache` helper | ENH-01/02 |
| `bot-core/src/utils/shop_announcement.py` | Tier colors; `items`/`tech_level` params; inventory embed | ENH-02 |
| `discord-gateway/src/cogs/shopCog.py` | TNN display/decode/resolve; autocomplete; tier cache | ENH-01 |
| `discord-gateway/src/utils/shop_tnn.py` | NEW — duplicate of bot-core helper | ENH-01 |
| `discord-gateway/src/cogs/_shared/shop_cache.py` | NEW — `ShopItemView`, `ShopTierSnapshot`, `build_shop_snapshot` | Perf |
| `discord-gateway/src/cogs/_shared/autocomplete_cache.py` | Add `peek()` synchronous accessor | Perf |
| `discord-gateway/src/api/routers/cache.py` | NEW — push/invalidate/stats endpoints | Perf |
| `discord-gateway/src/api/schemas/cache_schemas.py` | NEW — `ShopCachePushRequest`, stats response | Perf |

### A.8 Effort Estimates

| Work | Effort |
|------|--------|
| ENH-01 TNN (Phase A + B) | ~6h |
| Autocomplete perf fix (Phase C) | ~4h |
| ENH-02 Announcement (Phase D) | ~2h |
| **Total** | **~12h** |

---

## Appendix B — Bounty Announcement Field Reorder (implemented 2026-05-15)

*Design completed 2026-05-15. Ready for immediate implementation.*

### B.1 Change

Move "Route" and "Checked Systems" fields from `suffix_fields` (rendered after loadout) to `prefix_fields` (rendered before "Active Ship"), so the embed reads:

```
Difficulty | Reward Pool | Bounty Ends   ← existing prefix (inline)
Route                                    ← moved up
Checked Systems                          ← moved up
Active Ship                              ← unchanged
Ship Stats                               ← unchanged
[Loadout sections]                       ← unchanged
[Map image]                              ← always at bottom via set_image(), unchanged
```

### B.2 Implementation

**Single file, two-line logical change** in `services/bot-core/src/utils/bounty_announcement_payload.py`:

```python
# In build_bounty_announcement_request():
prefix_fields = _build_prefix_fields(bounty, captured)
prefix_fields.extend(_build_suffix_fields(bounty))   # Route + Checked Systems now before Active Ship

metadata = {
    ...
    "prefix_fields": prefix_fields,
    "suffix_fields": [],              # empty — content moved to prefix
}
```

`_build_prefix_fields()`, `_build_suffix_fields()`, `_build_route_value()`, and `_build_checked_systems_value()` are all unchanged. The gateway's `loadout_embed.py` is unchanged. No DB changes. No migrations.

**Captured state**: works correctly — with `suffix_fields=[]` an empty list is falsy, the gateway's existing `if suffix_fields:` guard is a no-op, and Route/Checked render from `prefix_fields` as normal.

**Map image**: `embed.set_image()` always renders at the embed bottom regardless of field order — no change needed.
