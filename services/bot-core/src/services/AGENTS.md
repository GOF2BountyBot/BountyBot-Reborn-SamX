# AGENTS.md - services

Business logic layer for bot-core. All 17 service modules live here + 1 normalizer helper.

---

## Item-Type Vocabulary & Normalizer Contract (A.36 fix, 2026-04-22)

**Storage invariant**: `player_inventories.item_type` and `guild_shops.item_type` always store
**concrete types** only: `ship`, `primary_weapon`, `secondary_weapon`, `turret_weapon`, `module`.
Generic aliases (`weapon`, `turret`) are NEVER persisted.

**Normalizer module**: `_item_type_normalizer.py` provides:
```python
expand_item_type_to_concrete(item_type, *, context: Literal["catalog", "playable"]) -> tuple[str, ...]
```
- `context="catalog"` — returns all concrete types including `secondary_weapon` (for browsing)
- `context="playable"` — returns only `GameConstants.CURRENTLY_ENABLED_TYPES` (surface-gated)
- Raises `InvalidItemTypeError` (subclass of `ValueError`) for unknown types or disabled concrete types

**Surface gating**: `GameConstants.CURRENTLY_ENABLED_TYPES` is the SINGLE lever controlling
secondary-weapon exposure. To enable secondary weapons: add `"secondary_weapon"` to this frozenset.

**Write-site rule**: all calls to `inventory_repo.add_item()` MUST use concrete types.
Use `equipment_service.item_discriminator_to_concrete_type(item.type)` to resolve from STI discriminator.

### /sell — Server-side type and tier resolution (A.42/A.42b/A.42c, 2026-04-22)

`ShopService.sell_item(db, player_id, item_name, quantity)` no longer accepts `item_type` or `target_tier` parameters:

- **A.42b**: `item_type` is resolved internally by calling `inventory_repo.get_player_items_by_name(db, player_id, item_name)`. The concrete type comes from the player's inventory row — no generic alias is ever passed to a write path.
- **A.42c**: `target_tier` always equals `player.tier` (read from the fetched player record). Items always land in the player's current tier shop, consistent with `/buy` tier-gating.
- **Cross-type collision guard**: if `get_player_items_by_name` returns rows with two different `item_type` values for the same item_name, `InvalidItemTypeError` is raised with a helpful message. This is impossible with the current catalog (146 items, all distinct names) but guarded defensively.

`SellRequest` schema (`shops_schema.py`) now has only `player_id`, `item_name`, and `quantity` fields. Extra fields (e.g. stale client sending `item_type` or `target_tier`) are silently ignored by Pydantic default behavior (no `extra='forbid'`).

`InventoryRepository.get_player_items_by_name(db, player_id, item_name)` — new method added to support this flow. Returns all inventory rows for a player matching the given item_name (regardless of concrete type).

---

## Service Layer Purpose

Services contain **all business logic**. Routers handle HTTP concerns only (parsing requests, returning responses). Repositories handle data access only (SQL queries). Logic that coordinates multiple repositories or enforces game rules lives in services.

```
Router  →  Service  →  Repository  →  Database
          (business   (SQL queries)
           logic)
```

---

## Constructor Injection Pattern

Services instantiate their own repositories in `__init__()`. No arguments are required to construct a service:

```python
class PlayerService:
    def __init__(self):
        self.player_repo = PlayerRepository()
        self.user_repo = UserRepository()
        self.config_repo = ConfigRepository()
```

Services do **not** store `AsyncSession` objects — sessions are passed per-call:

```python
async def get_or_create_player(self, db: AsyncSession, discord_id: int, ...) -> Player:
    user = await self.user_repo.get_or_create_user(db, discord_id, ...)
    ...
```

---

## All 17 Service Modules

### audit_service.py — `AuditService`

- All methods are **static** (no instance state)
- Records admin mutations to `AdminAuditLog` table
- **Failures are swallowed** — audit logging never blocks the primary operation
- Called by admin router endpoints and anywhere an admin mutation occurs

```python
await AuditService.log_action(
    db, user_id=123, action="guild_reset", guild_id=456,
    resource_type="guild", resource_id="456", details={"reason": "test"}
)
```

---

### bounty_service.py — `BountyService`

Core bounty system business logic:
- `spawn_bounty(db, guild_id, division)` — selects a random criminal, generates a route using PathfindingService, sets reward, creates the Bounty record
- `check_system(db, bounty_id, user_id, system_name)` — records a system check; returns proximity hints
- `expire_bounty(db, bounty_id)` — marks bounty as expired; optionally schedules respawn
- `resolve_bounty(db, bounty_id, winner_user_id)` — awards credits and XP to winner via PlayerService
- `clear_bounties(db, guild_id, tier=None)` — admin soft-clear; also cleans up Discord announcements AND any orphaned `bounty_expire` / `bounty_respawn` scheduler jobs linked to the cleared bounty IDs (A.11). Scheduler cleanup runs via HTTP to the scheduler API **after** the DB commit, mirroring the announcement-cleanup pattern: scheduler-side failures are non-fatal and logged as warnings. Return dict includes `scheduler_jobs_deleted` for observability.

**Scheduler-cleanup pattern** (A.11): orphaned jobs are located by payload content (`args[1]["job_type"]` ∈ {`bounty_expire`, `bounty_respawn`} AND `args[1]["bounty_id"]` in cleared set) rather than by deterministic job IDs, because bounty job IDs are random UUIDs. 404 responses on DELETE are treated as already-fired and NOT logged.

Uses: `CriminalRepository`, `BountyRepository`, `PathfindingService`, `SystemGraphService`, `PlayerService`, `TemperatureService`

---

### combat_models.py — Dataclasses (NOT a service)

**This file contains only dataclasses and protocols — no service class.** Do not confuse with `combat_service.py`.

Key types:
- `WeaponStats` — frozen dataclass: `name`, `dps`, optional `fire_rate`, `damage_per_shot`, `accuracy_modifier`
- `ModuleStats` — frozen dataclass: `name`, `module_type`, effect fields
- `ShipLoadout` — frozen dataclass: assembles ship + weapons + modules into one structure
- `CombatStats` — computed stats from a loadout
- `FightStats` / `FightResults` — output of a combat resolution
- `CombatResolver` — `Protocol` defining `resolve(attacker, defender) -> FightResults`

---

### combat_service.py — `CombatService`

Duel combat resolution:
- `build_loadout(db, player_id)` — fetches player's active ship, equipped weapons and modules from DB; returns `ShipLoadout`
- `resolve_combat(attacker_loadout, defender_loadout)` — uses `SimpleTTKResolver` (time-to-kill model) to simulate combat; applies DUEL_VARIANCE_PERCENT random factor
- Returns `FightResults` with winner, damage log, turn count

Uses: `PlayerRepository`, `PlayerShipRepository`, `InventoryRepository`, `EquipmentService`

---

### config_service.py — `ConfigService`

Guild configuration management (no auto-create — `/admin_setup` is the only path that creates a `guild_configs` row):
- `get_guild_config(db, guild_id)` — returns config summary; raises `GuildNotConfiguredError` if absent
- `get_bounty_config(db, guild_id)` — returns bounty config; raises `GuildNotConfiguredError` if absent
- `update_bounty_config(db, guild_id, updates)` — updates bounty fields; raises `GuildNotConfiguredError` if absent
- `create_or_update_config(db, config_data)` — the admin_setup creation path; always creates/updates
- `reset_to_defaults(db, guild_id)` — admin reset; wipes existing config and recreates defaults
- Provides starting_credits, channel IDs, and other per-guild settings to other services

Custom exception: `services.exceptions.GuildNotConfiguredError` (carries `guild_id` attribute).

Uses: `ConfigRepository`

---

### division_service.py — `DivisionService`

Static utility methods for player division logic:
- `get_division_for_level(level)` — maps player level to `"bronze"`, `"silver"`, or `"gold"` using `GameConstants.DIVISION_BOUNDARIES`
- `get_division_boundaries()` — returns `{division: (min_level, max_level)}` dict
- No instance state; all methods are `@staticmethod`

Uses: `GameConstants`

---

### duel_service.py — `DuelService`

Duel challenge lifecycle:
- `create_challenge(db, guild_id, challenger_id, target_id, stakes)` — validates both players exist, have sufficient credits, creates `DuelRequest`
- `accept_duel(db, duel_id)` — calls CombatService to resolve; awards/deducts credits; updates win/loss stats
- `decline_duel(db, duel_id)` — marks as declined; refunds any locked credits
- `expire_duels(db)` — bulk expire all duels past `expires_at`; called by `duel_expire_executor`

Uses: `DuelRepository`, `PlayerRepository`, `CombatService`

---

### equipment_service.py — `EquipmentService`

Equipment management:
- `equip_item(db, player_id, player_ship_id, item_name, item_type)` — validates equip limits from `GameConstants.MODULE_EQUIP_LIMITS`; updates `PlayerShip` loadout JSON
- `unequip_item(db, player_id, player_ship_id, item_name, item_type)` — removes item from loadout; moves to inventory
- Enforces per-module-type limits (e.g., max 1 ArmourModule, unlimited CabinModule)

Uses: `PlayerShipRepository`, `InventoryRepository`, `GameConstants`

---

### game_constants.py — `GameConstants`

Centralized game constants class. **Operational constants can be overridden via environment variables** prefixed with `BOUNTYBOT_`:

```bash
BOUNTYBOT_MAX_BOUNTIES_PER_DIVISION=10
BOUNTYBOT_CHECK_COOLDOWN=120
BOUNTYBOT_BOUNTY_DELAY_RANDOM_MIN=3
```

Call `GameConstants.load()` at application startup to apply overrides. **Non-operational constants** (XP boundaries, division definitions, module equip limits) are intentionally excluded from runtime overrides to maintain game balance.

Key constant groups:
- `DIVISION_NAMES` / `DIVISION_BOUNDARIES` — player division definitions
- `XP_LEVEL_BOUNDARIES` — XP required per level (levels 0–10)
- `MODULE_EQUIP_LIMITS` — per-module-type equip limits dict
- `BOUNTY_DELAY_RANDOM_MIN/MAX` — bounty spawn frequency
- `MAX_BOUNTIES_PER_DIVISION` — bounty cap (temperature-adjusted)
- `SHOP_DEFAULT_*_NUM` — shop stock counts per category

---

### game_maths.py — Pure Functions

Module-level functions only (no class):
- `calculate_user_level(xp: int) -> int` — returns player level 1–10 using `GameConstants.XP_LEVEL_BOUNDARIES`
- `calculate_xp_for_level(level: int) -> int` — returns XP threshold for the given level

---

### inventory_service.py — `InventoryService`

Player inventory management:
- `buy_item(db, player_id, item_name, item_type, guild_id)` — validates credits, deducts from player, adds to inventory
- `sell_item(db, player_id, item_name, item_type)` — removes from inventory, adds credits to player
- `transfer_item(db, source_player_id, target_player_id, item_name, item_type)` — item transfer between players
- `get_player_inventory(db, player_id)` — returns full inventory list

Uses: `InventoryRepository`, `PlayerRepository`, `ShopRepository`, `ItemRepository`

---

### map_renderer.py — `MapRenderer`

Pillow-based star map image generation:
- `render_map(systems, highlighted_systems, route)` — generates PNG image of the system graph with highlighted nodes and drawn route
- Uses `PIL.Image`, `PIL.ImageDraw` for rendering
- Returns `bytes` (PNG image data) for HTTP response

Uses: `SystemRepository`, `SystemGraphService`

---

### pathfinding_service.py — `PathfindingService`

A* pathfinding over the star system graph:
- `find_path(db, start_system, end_system)` — returns list of system names representing shortest route
- `MAX_ROUTE_LENGTH` limit from `GameConstants` prevents runaway searches
- Returns `None` if no path exists within the limit

Uses: `SystemRepository`, `SystemGraphService`

---

### player_service.py — `PlayerService`

Core player management:
- `get_or_create_player(db, discord_id, guild_id, discord_username)` — creates user if needed, creates player with `starting_credits` from guild config
- `update_player_credits(db, player_id, new_credits, update_lifetime)` — sets absolute credit balance; optionally updates lifetime_credits
- `update_player_xp(db, player_id, new_xp)` — sets XP; **auto-computes tier** from level boundaries; calls `update_tier` if advancement detected
- `prestige_player(db, player_id)` — requires level 10; resets XP, credits, tier, inventory; increments `prestige_count`; preserves ships, duel stats, bounty stats
- `transfer_credits(db, source_id, target_id, amount)` — atomic transfer using `get_by_id_for_update` to prevent race conditions
- `get_player_statistics(db, player_id)` — assembles comprehensive stats dict

Uses: `PlayerRepository`, `UserRepository`, `ConfigRepository`, `DivisionService`, `GameConstants`

---

### shop_service.py — `ShopService`

Multi-tier shop system:
- `generate_shop_stock(db, guild_id)` — generates a new shop with `SHOP_DEFAULT_*_NUM` items per category; items are tier-appropriate for the guild's player base
- `refresh_shop(db, guild_id)` — clears current stock, calls `generate_shop_stock()`; called by `shop_refresh_executor`
- `buy_item(db, player_id, item_name, guild_id)` — validates tier eligibility, deducts credits, adds item to inventory
- `sell_item(db, player_id, item_name, quantity=1)` — sells item back for a fraction of its value

Uses: `ShopRepository`, `ShipRepository`, `PrimaryWeaponRepository`, `SecondaryWeaponRepository`, `TurretWeaponRepository`, `ModuleRepository`, `PlayerRepository`, `InventoryRepository`, `PlayerShipRepository`, `ConfigRepository`

---

### system_graph_service.py — `SystemGraphService`

Star system adjacency graph:
- `build_graph(db)` — loads all systems from DB; constructs adjacency dict `{system_name: [connected_system_names]}`
- `get_graph()` — returns cached graph (rebuilds if cache is empty)
- Used by `PathfindingService` and `MapRenderer`

Uses: `SystemRepository`

---

### temperature_service.py — `TemperatureService`

Guild activity temperature:
- `get_max_bounties(temperature)` — static method; returns max bounties per division based on activity level; higher temperature → more bounties (up to `MAX_BOUNTIES_PER_DIVISION`)
- `apply_decay(current_temp, decay_rate)` — static; applies `GUILD_ACTIVITY_DECAY_RATE` decay
- `calculate_temperature(player_count)` — computes temperature from active players using `ACTIVITY_TEMP_PER_PLAYER`

---

## Service Interaction Map

```
DuelService ──── CombatService ──── EquipmentService
               │                  │
               └─── PlayerService ─┴─── InventoryService
                          │
                          └─── ConfigService
                          │
                          └─── DivisionService ──── GameConstants

BountyService ─── PathfindingService ─── SystemGraphService
             │
             └─── TemperatureService ──── GameConstants
             │
             └─── PlayerService (for reward distribution)

ShopService ──── PlayerService
            │
            └─── InventoryService
```

---

## How to Add a New Service

1. **Create the file** `services/<name>_service.py`
2. **Instantiate repositories** in `__init__(self)`:
   ```python
   from persist.repositories.my_repo import MyRepository

   class MyService:
       def __init__(self):
           self.my_repo = MyRepository()
   ```
3. **Inject `AsyncSession` per call** — never store `db` on the instance
4. **Add tests** in `tests/services/test_<name>_service.py`
5. **Add a `Depends()` factory** in the relevant router if it needs HTTP exposure

---

*Last updated: 2026-03-16*
