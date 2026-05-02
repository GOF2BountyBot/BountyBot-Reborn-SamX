# AGENTS.md - bot-core Service

Authoritative reference for AI agents doing maintenance, troubleshooting, or feature work on the **bot-core** service.

---

## Service Overview

**bot-core** is the central FastAPI service powering the Galaxy on Fire 2 Discord game. It provides:

- Core game logic: bounty hunting, duels, combat resolution, player progression
- Player management: per-guild isolated game state, XP/tier advancement, prestige
- Ship systems: ship inventory, equipment loadouts, skin management
- Shop system: tier-gated guild shops with periodic refresh
- Inventory management: player items, Kaamo station storage
- Scheduled jobs: bounty spawning/expiry, shop refresh, temperature decay, duel expiry
- Database management: async SQLAlchemy ORM, Alembic migrations, circuit breaker
- REST API: 15 auto-discovered routers, all under `/api/v1/`

---

## Technology Stack

| Technology | Version / Notes |
|---|---|
| **FastAPI** | Web framework; CORS middleware, OpenAPI docs at `/docs` and `/redoc` |
| **SQLAlchemy** | Async ORM with `AsyncSession`; `asyncpg` dialect |
| **Alembic** | Database migrations; auto-applied on startup via `MigrationManager.ensure_current()` |
| **APScheduler** | `AsyncIOScheduler` with SQLAlchemy job store (`apscheduler_jobs` table) |
| **PostgreSQL 18** | Primary database; accessed via asyncpg |
| **Pydantic v2** | Request/response schemas; `ConfigDict(from_attributes=True)`, `.model_dump()` |
| **Pillow (PIL)** | Map rendering in `map_renderer.py` |
| **httpx** | Async HTTP client used by executors to call discord-gateway and scheduler APIs |
| **bblogger** | Custom logging utility from `services/shared/bblogger.py`; provides TRACE level |
| **pytest** | Test runner; `asyncio_mode = auto` (configured in root `pyproject.toml`) |
| **Ruff** | Linter/formatter; `target-version = "py312"`, `line-length = 120` |

---

## Directory Structure

```
services/bot-core/
├── Dockerfile                          # Container build (Python 3.12, copies shared/bblogger)
├── requirements.txt                    # Python dependencies
├── import_data/                        # Seed JSON files for game assets
│   ├── ship/                           # Ship definitions (one JSON per ship)
│   ├── module/                         # Ship module definitions
│   ├── primary_weapon/                 # Primary weapon definitions
│   ├── secondary_weapon/               # Secondary weapon definitions
│   ├── turret_weapon/                  # Turret weapon definitions
│   ├── criminal/                       # NPC criminal definitions
│   └── system/                         # Star system node definitions
└── src/
    ├── main.py                         # FastAPI app factory, lifespan, router auto-discovery
    ├── api/
    │   ├── routers/                    # 15 FastAPI routers (auto-discovered by main.py)
    │   │   ├── about.py                # /about — game data browsing
    │   │   ├── admin.py                # /admin — admin operations (audit-logged)
    │   │   ├── bounties.py             # /bounties — bounty lifecycle
    │   │   ├── config.py               # /config — guild configuration
    │   │   ├── data.py                 # /data — game data lookups
    │   │   ├── discord_message.py      # /discord-messages — persistent message refs
    │   │   ├── duels.py                # /duels — duel challenge lifecycle
    │   │   ├── health.py               # /health — service + DB health check
    │   │   ├── inventory.py            # /inventory — player inventory management
    │   │   ├── players.py              # /players — player CRUD, XP, credits, prestige
    │   │   ├── scheduler.py            # /jobs — APScheduler job management
    │   │   ├── ships.py                # /ships — ship definitions API
    │   │   ├── shops.py                # /shops — guild shop management
    │   │   ├── systems.py              # /systems — star system graph
    │   │   └── users.py                # /users — Discord user accounts
    │   └── schemas/                    # 13 Pydantic v2 schema modules
    │       ├── about_schema.py
    │       ├── admin_schema.py
    │       ├── bounty_schema.py
    │       ├── config_schema.py
    │       ├── discord_message_schema.py
    │       ├── duel_schema.py
    │       ├── health_schema.py
    │       ├── inventory_schema.py
    │       ├── players_schema.py
    │       ├── scheduler_schema.py
    │       ├── ships_schema.py
    │       ├── shops_schema.py
    │       └── users_schema.py
    ├── persist/
    │   ├── database/                   # DB engine, sessions, migrations, circuit breaker
    │   │   ├── manager.py              # DatabaseManager singleton (db_manager)
    │   │   ├── circuit_breaker.py      # CircuitBreaker (CLOSED/OPEN/HALF_OPEN)
    │   │   ├── migration_manager.py    # MigrationManager wrapping Alembic
    │   │   ├── schema_manager.py       # SchemaManager for version health checks
    │   │   ├── tablenames.py           # TableNames enum (single source of truth)
    │   │   ├── run_migration.py        # CLI tool for manual Alembic commands
    │   │   ├── alembic.ini             # Alembic configuration
    │   │   └── revisions/              # Alembic env.py + version scripts
    │   ├── interfaces/
    │   │   └── repository_interface.py # IRepository[T] abstract base class
    │   ├── models/                     # 21 SQLAlchemy ORM models (auto-imported)
    │   ├── repositories/               # 19 data-access repositories
    │   └── schemas/                    # schema_manager.py (legacy location)
    ├── services/                       # 16 business-logic modules (B.48: division_service removed)
    │   ├── audit_service.py
    │   ├── bounty_service.py
    │   ├── combat_models.py            # Dataclasses only (NOT a service)
    │   ├── combat_service.py
    │   ├── config_service.py
    │   ├── duel_service.py
    │   ├── equipment_service.py
    │   ├── game_constants.py           # GameConstants class (env-overridable)
    │   ├── game_maths.py               # Pure math helpers (TL/reward formulas)
    │   ├── inventory_service.py
    │   ├── map_renderer.py             # Pillow-based star map rendering
    │   ├── pathfinding_service.py      # A* pathfinding across system graph
    │   ├── player_service.py
    │   ├── shop_service.py
    │   ├── system_graph_service.py
    │   └── temperature_service.py
    ├── message_builders/               # Discord embed payload builder framework
    │   ├── base.py                     # MessagePayloadBuilder abstract base
    │   ├── factory.py                  # MessageBuilderFactory (registry pattern)
    │   └── builders/
    │       └── time_announcement.py    # TimeAnnouncementBuilder
    └── utils/
        ├── auto_seeder.py              # Idempotent startup seeder from import_data/
        ├── data_loader.py              # JSON-file loader for game assets
        ├── emoji_service.py            # Emoji lookup helper
        ├── job_executor.py             # JobExecutor dispatcher + run_job() entry point
        └── executors/                  # 7 async job executor modules
            ├── bounty_expire_executor.py
            ├── bounty_respawn_executor.py
            ├── bounty_spawn_executor.py
            ├── duel_expire_executor.py
            ├── shop_refresh_executor.py
            ├── temperature_decay_executor.py
            └── time_announcement_executor.py
```

---

## Startup Flow

`main.py` runs through the following sequence inside the `lifespan` async context manager:

```
1. db_manager.initialize()
   └── Creates AsyncEngine + sessionmaker with asyncpg
   └── Connection pooling (DB_POOL_SIZE, DB_MAX_OVERFLOW, etc.)

2. MigrationManager.from_async_url(connection_string).ensure_current()
   └── Converts asyncpg URL → psycopg2 URL
   └── Runs Alembic "upgrade head" if any pending revisions exist
   └── BLOCKS startup if migration fails

3. initialize_schema(db_manager)
   └── Builds SchemaManager for health-check endpoints (informational only)
   └── Stores on app.state.schema_manager and app.state.db_manager

4. auto_seed_data()
   └── Checks each of 7 game-data tables (ship, primary_weapon, secondary_weapon,
       turret_weapon, module, criminal, system)
   └── Loads JSON files from import_data/<category>/ if table is empty
   └── Non-fatal: errors are logged, startup continues

5. AsyncIOScheduler initialization
   └── Creates sync SQLAlchemy engine (postgresql:// not postgresql+asyncpg://)
   └── SQLAlchemyJobStore persists jobs to apscheduler_jobs table
   └── scheduler.start()
   └── register_default_jobs() — idempotent, skips already-registered jobs:
       - bounty_spawn_default  (every N minutes, default 5)
       - shop_refresh_default  (every 6 hours)
       - temperature_decay_default (every 1 hour)

6. include_routers()
   └── pkgutil.iter_modules() scans api/routers/
   └── Each module exposing a `router` attribute is mounted at /api/v1
   └── All routers auto-discovered — no manual registration needed

7. yield (serve requests)

8. Shutdown: scheduler.shutdown(wait=False), db_manager.shutdown()
```

---

## All 15 Routers

| File | Prefix | Tags | Key Endpoints |
|---|---|---|---|
| `about.py` | `/about` | about | GET /ships, GET /modules, GET /weapons, GET /criminals, GET /systems |
| `admin.py` | `/admin` | admin | POST /guild-reset, POST /credits, GET /audit-log — all writes produce AdminAuditLog |
| `bounties.py` | `/bounties` | bounties | GET /guild/{guild_id}, POST / (spawn), PUT /{id}/check, PUT /{id}/expire |
| `config.py` | `/config` | config | GET /{guild_id}, POST /, PUT /{guild_id} — guild-specific bot configuration |
| `data.py` | `/data` | data | GET /{category} — bulk game data by category enum |
| `discord_message.py` | `/discord-messages` | discord-messages | GET, POST, DELETE — persistent Discord message reference management |
| `duels.py` | `/duels` | duels | POST /challenge, POST /{id}/accept, POST /{id}/decline, POST /{id}/resolve |
| `health.py` | `/health` | health | GET / (comprehensive), GET /simple — DB connectivity + schema version |
| `inventory.py` | `/inventory` | inventory | GET /player/{id}, POST /equip, POST /unequip, POST /sell, POST /transfer |
| `players.py` | `/players` | players | POST / (create-or-get), GET /{id}, GET /guild/{id}, PUT /{id}/credits, PUT /{id}/xp, POST /{id}/prestige, GET /{id}/statistics, POST /transfer |
| `scheduler.py` | `/jobs` | job-scheduler | GET /jobs, POST /jobs (one-time), POST /jobs/recurring, DELETE /jobs/{id}, PUT /jobs/{id} |
| `ships.py` | `/ships` | ships | GET /, GET /{id}, GET /name/{name} — ship definition lookups |
| `shops.py` | `/shops` | shops | GET /guild/{id}, POST /guild/{id}/refresh, POST /guild/{id}/buy, POST /guild/{id}/sell |
| `systems.py` | `/systems` | systems | GET /, GET /{name}, GET /path/{from}/{to} — star system graph and A* pathfinding |
| `users.py` | `/users` | users | GET /{discord_id}, POST / (create-or-get), GET /{id}/players |

> **Router auto-discovery**: `main.py` uses `pkgutil.iter_modules()` to scan `api/routers/`. Any module in that package that exposes a `router` attribute is automatically mounted at `/api/v1`. No manual registration in `main.py` is needed.

---

## All 21 Models

| File | Class | Parent | Table Name | Purpose |
|---|---|---|---|---|
| `base.py` | `Base` | `DeclarativeBase` | — | SQLAlchemy declarative base; all models inherit from this |
| `admin_audit_log.py` | `AdminAuditLog` | `Base` | `admin_audit_logs` | Immutable audit trail for all admin mutations |
| `bounty.py` | `Bounty` | `Base` | `bounty` | Active bounty: criminal route, reward, checked systems, status |
| `criminal.py` | `Criminal` | `Base` | `criminal` | NPC criminal definitions: faction, ship, tech level |
| `discord_message.py` | `DiscordMessage` | `Base` | `discord_message` | Persistent Discord message reference (guild, channel, message IDs) |
| `duel_request.py` | `DuelRequest` | `Base` | `duel_requests` | Duel challenge: challenger, target, stakes, status, expiry |
| `guild_config.py` | `GuildConfig` | `Base` | `guild_configs` | Per-guild bot configuration: starting credits, channels, settings |
| `guild_shop.py` | `GuildShop` | `Base` | `guild_shops` | Guild shop inventory: item listings with tier requirements |
| `item.py` | `Item` | `Base` | `item` | **STI root**: all purchasable items; columns: id, name, aliases, built_in, emoji, icon, value, wiki, type |
| `weapon.py` | `Weapon` | `Item` | `weapon` | STI intermediate: adds tech_level, extra_atts (JSON); `polymorphic_identity='weapon'` |
| `module.py` | `Module` | `Item` | `module` | Ship module items; adds tech_level, max_equipped, extra_atts; subtype discrimination via `Item.type` (STI discriminator — no separate `module_type` column) |
| `primary_weapon.py` | `PrimaryWeapon` | `Weapon` | `primary_weapon` | Primary weapon; adds dps; `polymorphic_identity='primary_weapon'` |
| `secondary_weapon.py` | `SecondaryWeapon` | `Weapon` | `secondary_weapon` | Secondary weapon; adds dps, ammo; `polymorphic_identity='secondary_weapon'` |
| `turret_weapon.py` | `TurretWeapon` | `Weapon` | `turret_weapon` | Turret weapon; adds dps; `polymorphic_identity='turret_weapon'` |
| `player.py` | `Player` | `Base` | `players` | Per-guild player state: credits, XP, tier, duel stats, bounty stats, active_ship_id |
| `player_inventory.py` | `PlayerInventory` | `Base` | `player_inventories` | Player → item ownership with quantity and location (equipped/kaamo/ship) |
| `player_ship.py` | `PlayerShip` | `Base` | `player_ships` | Player → ship association with loadout (equipped weapons, modules) and nickname |
| `schema_version.py` | `SchemaVersion` | `Base` | `schema` | Alembic migration tracking; read by SchemaManager for health checks |
| `ship.py` | `Ship` | `Base` | `ship` | Ship definitions: stats, weapon slots, compatible skins, model filenames |
| `system.py` | `System` | `Base` | `system` | Star system node: connections (adjacency), faction, position |
| `user.py` | `User` | `Base` | `users` | Discord user account: discord_id, username; links to multiple Players |

### Model Inheritance (STI)

```
Base (DeclarativeBase)
├── Item  [table: item]  ← STI root, discriminator: type column
│   ├── Module  [table: module]
│   └── Weapon  [table: weapon]  ← abstract intermediate
│       ├── PrimaryWeapon  [table: primary_weapon]
│       ├── SecondaryWeapon  [table: secondary_weapon]
│       └── TurretWeapon  [table: turret_weapon]
├── AdminAuditLog  [table: admin_audit_logs]
├── Bounty  [table: bounty]
├── Criminal  [table: criminal]
├── DiscordMessage  [table: discord_message]
├── DuelRequest  [table: duel_requests]
├── GuildConfig  [table: guild_configs]
├── GuildShop  [table: guild_shops]
├── Player  [table: players]
├── PlayerInventory  [table: player_inventories]
├── PlayerShip  [table: player_ships]
├── SchemaVersion  [table: schema]
├── Ship  [table: ship]
├── System  [table: system]
└── User  [table: users]
```

> The `models/__init__.py` uses `pkgutil.walk_packages` to auto-import all model modules, ensuring all classes are registered with SQLAlchemy's mapper before any session is created.

---

## All 19 Repositories

| File | Class | Purpose |
|---|---|---|
| `generic_repository.py` | `GenericRepository[T]` | Base implementation of `IRepository[T]`: add, get_by_id, get_by_name, get_by_alias, list_all, remove |
| `bounty_repository.py` | `BountyRepository` | Bounty CRUD + get_active_by_guild, get_active_by_guild_and_division, count_active_by_guild_and_division |
| `config_repository.py` | `ConfigRepository` | GuildConfig CRUD + get_by_guild_id |
| `criminal_repository.py` | `CriminalRepository` | Criminal CRUD + faction/tech-level filtering |
| `discord_message_repository.py` | `DiscordMessageRepository` | DiscordMessage CRUD + lookup by guild/channel/type |
| `duel_repository.py` | `DuelRepository` | DuelRequest CRUD + get_pending_by_guild, get_by_challenger_and_target |
| `inventory_repository.py` | `InventoryRepository` | PlayerInventory CRUD + get_by_player, get_by_player_and_item, get_equipped_items |
| `item_repository.py` | `ItemRepository` | Item base queries + get_by_type |
| `module_repository.py` | `ModuleRepository` | Module CRUD + filter by tech_level (subtype queries use `Item.type` STI discriminator, not a `module_type` column) |
| `player_repository.py` | `PlayerRepository` | Player CRUD + get_by_user_and_guild, get_players_by_guild, update_credits, update_xp, update_tier, get_by_id_for_update (FOR UPDATE lock) |
| `player_ship_repository.py` | `PlayerShipRepository` | PlayerShip CRUD + get_by_player, get_by_player_and_ship |
| `primary_weapon_repository.py` | `PrimaryWeaponRepository` | PrimaryWeapon CRUD + filter by tech_level |
| `secondary_weapon_repository.py` | `SecondaryWeaponRepository` | SecondaryWeapon CRUD + filter by tech_level |
| `ship_repository.py` | `ShipRepository` | Ship CRUD + filter by manufacturer, skinnable |
| `shop_repository.py` | `ShopRepository` | GuildShop CRUD + get_by_guild, clear_guild_shop |
| `system_repository.py` | `SystemRepository` | System CRUD + get_connected_systems |
| `turret_weapon_repository.py` | `TurretWeaponRepository` | TurretWeapon CRUD + filter by tech_level |
| `user_repository.py` | `UserRepository` | User CRUD + get_by_discord_id, get_or_create_user |
| `weapon_repository.py` | `WeaponRepository` | Weapon base queries (parent of primary/secondary/turret repos) |

> See `src/persist/repositories/AGENTS.md` for the full repository pattern documentation.

---

## All 16 Services

| File | Key Class(es) | Purpose |
|---|---|---|
| `audit_service.py` | `AuditService` | Static methods only; records admin mutations to AdminAuditLog; failures are swallowed (never block primary operation) |
| `bounty_service.py` | `BountyService` | spawn_bounty, check_system, expire_bounty, resolve_bounty; uses CriminalRepository, SystemRepository, PathfindingService |
| `combat_models.py` | `WeaponStats`, `ModuleStats`, `ShipLoadout`, `CombatStats`, `FightResults`, `CombatResolver` (protocol) | Pure dataclasses + protocol; NOT a service class; imported by CombatService |
| `combat_service.py` | `CombatService` | Duel combat resolution using SimpleTTKResolver; assembles loadouts, runs combat simulation |
| `config_service.py` | `ConfigService` | GuildConfig CRUD with defaults; provides starting_credits, channel IDs |
| `division_service.py` | *(REMOVED in B.48)* | Level/division progression system was deleted; tier progression is now driven by `xp_thresholds` JSON in GuildConfig |
| `duel_service.py` | `DuelService` | create_challenge, accept_duel, decline_duel, resolve_duel, expire_duels; calls CombatService |
| `equipment_service.py` | `EquipmentService` | Equip/unequip weapons and modules to PlayerShip; enforces per-type equip limits from GameConstants |
| `game_constants.py` | `GameConstants` | Centralized constants class; operational constants overridable via `BOUNTYBOT_{KEY}` env vars; call `GameConstants.load()` at startup |
| `game_maths.py` | `pick_random_item_tl()`, `reward_per_sys_check()`, `ship_tech_level_for_value()` | Pure math helpers (TL selection, bounty reward formula). B.48: `calculate_user_level()` was removed. |
| `inventory_service.py` | `InventoryService` | buy_item, sell_item, transfer_item, equip/unequip wrappers; calls ShopRepository, PlayerRepository |
| `map_renderer.py` | `MapRenderer` | Pillow-based star map image generation; renders SystemGraph as PNG |
| `pathfinding_service.py` | `PathfindingService` | A* pathfinding over the star system graph; MAX_ROUTE_LENGTH from GameConstants |
| `player_service.py` | `PlayerService` | get_or_create_player, update_credits, update_xp (auto-tier), prestige_player, transfer_credits, get_player_statistics |
| `shop_service.py` | `ShopService` | generate_shop_stock, refresh_shop, buy_item, sell_item; tier-gated; uses ShipRepository, weapon/module repos |
| `system_graph_service.py` | `SystemGraphService` | Loads system adjacency graph; used by PathfindingService |
| `temperature_service.py` | `TemperatureService` | Static: get_max_bounties(temperature); guild activity temperature affects spawn cap |

> See `src/services/AGENTS.md` for service interaction patterns and constructor injection details.

---

## All 13 Schema Modules

| File | Key Classes | Purpose |
|---|---|---|
| `about_schema.py` | `ShipResponse`, `ModuleResponse`, `PrimaryWeaponResponse`, etc. | Game data browsing response models |
| `admin_schema.py` | `GuildResetRequest`, `CreditUpdateRequest`, `AuditLogResponse` | Admin operation request/response |
| `bounty_schema.py` | `BountyResponse`, `SpawnBountyRequest`, `CheckSystemRequest` | Bounty lifecycle request/response |
| `config_schema.py` | `GuildConfigResponse`, `CreateConfigRequest`, `UpdateConfigRequest` | Guild configuration |
| `discord_message_schema.py` | `DiscordMessageResponse`, `CreateMessageRequest` | Discord message persistence |
| `duel_schema.py` | `DuelChallengeRequest`, `DuelResponse`, `DuelResultResponse` | Duel challenge lifecycle |
| `health_schema.py` | `HealthResponse`, `SimpleHealthResponse` | Health check payloads |
| `inventory_schema.py` | `InventoryResponse`, `EquipRequest`, `SellRequest` | Inventory management |
| `players_schema.py` | `PlayerResponse`, `CreatePlayerRequest`, `UpdateCreditsRequest`, `UpdateXPRequest`, `TransferCreditsRequest`, `PrestigeResponse` | Player management |
| `scheduler_schema.py` | `JobInfo`, `OneTimeJob`, `RecurringJob`, `UpdateJob` | APScheduler job management |
| `ships_schema.py` | `ShipResponse`, `ShipListResponse` | Ship definition responses |
| `shops_schema.py` | `ShopResponse`, `BuyRequest`, `SellRequest` | Shop transaction requests/responses |
| `users_schema.py` | `UserResponse`, `CreateUserRequest` | Discord user account management |

All schemas use **Pydantic v2** conventions:
```python
model_config = ConfigDict(from_attributes=True)   # NOT class Config
obj.model_dump()                                   # NOT obj.dict()
```

---

## Database / Persistence Layer

### DatabaseManager (`persist/database/manager.py`)

Singleton `db_manager` instance manages the entire async connection lifecycle:

- `db_manager.initialize()` — creates `AsyncEngine` + `async_sessionmaker`; call once on startup
- `db_manager.get_session()` — async context manager yielding an `AsyncSession`
- `get_db_session()` — FastAPI dependency alias for `db_manager.get_session()`
- `db_manager.shutdown()` — disposes engine; call on shutdown
- Connection pool config via env vars: `DB_POOL_SIZE` (default 10), `DB_MAX_OVERFLOW` (20), `DB_POOL_TIMEOUT` (30), `DB_POOL_RECYCLE` (3600), `DB_ECHO` (false)

### CircuitBreaker (`persist/database/circuit_breaker.py`)

Three-state fault-tolerance wrapper for DB operations:

| State | Behaviour |
|---|---|
| `CLOSED` | Normal operation; failures increment counter |
| `OPEN` | All calls immediately rejected; entered after `failure_threshold` failures |
| `HALF_OPEN` | Limited calls allowed to test recovery; re-opens on failure |

Default config: `failure_threshold=5`, `recovery_timeout=60s`, `success_threshold=3`.

### MigrationManager (`persist/database/migration_manager.py`)

Wraps Alembic to provide zero-friction schema management:

- `MigrationManager.from_async_url(url)` — converts asyncpg URL to sync psycopg2 URL
- `MigrationManager.from_env()` — builds sync URL from `POSTGRES_*` env vars
- `.ensure_current()` — runs `alembic upgrade head` if any pending revisions; **called on every startup**
- `.auto_generate(message)` — generates a new autogenerated revision
- `.upgrade(target)` / `.downgrade(target)` — manual migration control

### SchemaManager (`persist/schemas/schema_manager.py`)

Reads the `schema` table (SchemaVersion model) to provide version info for health checks. Not involved in migration logic.

### TableNames Enum (`persist/database/tablenames.py`)

Single source of truth for all database table names. Always use `TableNames.X.value` in model `__tablename__` declarations — never hardcode strings.

### run_migration.py CLI

```bash
# Inside container (from /app/src):
python -m persist.database.run_migration upgrade       # apply pending
python -m persist.database.run_migration downgrade -1  # roll back one
python -m persist.database.run_migration revision -m "describe change"
python -m persist.database.run_migration current
python -m persist.database.run_migration history
```

> See `src/persist/database/AGENTS.md` for full database layer documentation.

---

## Utils

### auto_seeder.py

Idempotent startup seeder. For each category in `SEED_CATEGORIES` (`ship`, `primary_weapon`, `secondary_weapon`, `turret_weapon`, `module`, `criminal`, `system`):
1. Checks if the table is empty
2. If empty, calls `load_data(category)` to import all JSON files from `import_data/<category>/`
3. Missing directories are skipped gracefully (non-fatal)

### data_loader.py

`load_data(category)` reads all `.json` files from `import_data/<category>/` and calls `repo.create_or_update()` for each. `get_repository(category)` maps category strings to repository instances.

### emoji_service.py

Provides emoji lookup helpers for game entities. Used by routers when building responses that include emoji identifiers.

### job_executor.py

`JobExecutor.execute(job_id, payload)` dispatches on `payload["job_type"]`:

| job_type | Executor function |
|---|---|
| `time_announcement` | `execute_time_announcement_job` |
| `shop_refresh` | `execute_shop_refresh_job` |
| `bounty_spawn` | `execute_bounty_spawn_job` |
| `bounty_expire` | `execute_bounty_expire_job` |
| `bounty_respawn` | `execute_bounty_respawn_job` |
| `duel_expire` | `execute_duel_expire_job` |
| `temperature_decay` | `execute_temperature_decay_job` |

`run_job(job_id, payload)` is the APScheduler entry point (must be picklable).

### Executors (`utils/executors/`)

| File | job_type | Trigger | Purpose |
|---|---|---|---|
| `bounty_spawn_executor.py` | `bounty_spawn` | Every N min (default 5) | Check each guild×division for open slots; call BountyService.spawn_bounty(); schedule expiry job; announce to discord-gateway |
| `bounty_expire_executor.py` | `bounty_expire` | One-time at bounty.end_time | Mark bounty expired; optionally schedule respawn |
| `bounty_respawn_executor.py` | `bounty_respawn` | One-time after expiry | Trigger a new bounty spawn for the same division/guild |
| `duel_expire_executor.py` | `duel_expire` | Periodic or one-time | Find pending duels past `expires_at`; mark as expired |
| `shop_refresh_executor.py` | `shop_refresh` | Every 6 hours | Call ShopService.refresh_shop() for all guild configs |
| `temperature_decay_executor.py` | `temperature_decay` | Every 1 hour | Apply GuildActivity temperature decay via TemperatureService |
| `time_announcement_executor.py` | `time_announcement` | On-demand | Build and POST a time-based announcement to discord-gateway |

> All executor functions use **deferred imports** (imports inside the function body) to avoid transitive ORM dependencies at module load time — important for clean test isolation.

> See `src/utils/executors/AGENTS.md` for full executor documentation.

---

## Message Builders

| File | Class | Purpose |
|---|---|---|
| `base.py` | `MessagePayloadBuilder` | Abstract base; requires `build_payload()`, `extract_data()`, `get_message_type()`, `validate_input()` |
| `factory.py` | `MessageBuilderFactory` | Registry pattern; `create_builder(message_type)` returns the appropriate builder; `register_builder()` for extension |
| `builders/time_announcement.py` | `TimeAnnouncementBuilder` | Builds Discord embed payload for time-based announcements |

To add a new message type:
1. Create `builders/<type>.py` implementing `MessagePayloadBuilder`
2. Register in `MessageBuilderFactory._builders` dict in `factory.py`

---

## Import Data

JSON files in `import_data/` are the source of truth for game assets. They are loaded into the database by `auto_seeder.py` on first startup.

| Directory | Model | Notes |
|---|---|---|
| `ship/` | `Ship` | One JSON file per ship; includes stats, weapon slots, skin info |
| `module/` | `Module` | One JSON per module; includes `type` (STI discriminator, e.g. `ArmourModule`), tech_level |
| `primary_weapon/` | `PrimaryWeapon` | One JSON per weapon; includes dps, tech_level |
| `secondary_weapon/` | `SecondaryWeapon` | One JSON per weapon; includes dps, ammo, tech_level |
| `turret_weapon/` | `TurretWeapon` | One JSON per weapon; includes dps, tech_level |
| `criminal/` | `Criminal` | One JSON per criminal NPC; includes faction, ship, tech_level |
| `system/` | `System` | One JSON per system node; includes connections (adjacency list) |

---

## Testing

- **85 test files**
- Runner: `pytest` with `asyncio_mode = auto` (root `pyproject.toml`)
- Coverage target: ≥ 80%

### Test Organization

```
tests/
├── conftest.py             # Service-level fixtures (mocked bblogger, mock_db_manager, mock_schema_manager)
├── fixtures/
│   └── game_data.py        # Real game data fixtures (ships, weapons, criminals, systems)
├── api/                    # Router-level tests (21 files)
│   ├── conftest.py         # API test fixtures (TestClient, app setup)
│   └── test_*.py           # One file per router
├── services/               # Service-level tests (19 files)
│   ├── conftest.py         # Service test fixtures
│   └── test_*.py           # One file per service module
├── repositories/           # Repository tests
│   └── test_*.py
├── integration/            # Integration tests (require live DB or heavier mocking)
└── test_*.py               # Top-level: executor tests, migration tests, startup tests
```

### conftest.py Setup

The top-level `conftest.py` does the following before any test:
1. Mocks `shared.bblogger` to avoid import errors (shared library not on test path)
2. Provides `mock_db_manager` fixture with `AsyncMock` for all DB operations
3. Provides `mock_schema_manager` fixture
4. Sets up `TestClient` wrapping the FastAPI app

### Key Testing Conventions

- **Max 2 mocks per test** — prefer real objects with deterministic inputs
- See `tests/services/test_combat_service.py` as the reference pattern for service tests
- Use `fixtures/game_data.py` for real game entity fixtures (ships, weapons, etc.)
- Executor tests use deferred imports; mock `db_manager.get_session()` as an async context manager

---

## Common Tasks

### Add a New Router

1. Create `src/api/routers/<name>.py` with a `router = APIRouter(prefix="/<name>", tags=["<name>"])` attribute
2. Create `src/api/schemas/<name>_schema.py` with Pydantic v2 models
3. **No registration needed** — `main.py` auto-discovers routers via `pkgutil.iter_modules()`
4. Add tests in `tests/api/test_<name>_router.py`

### Add a New Model

1. Create `src/persist/models/<name>.py` inheriting from `Base` (or `Item`/`Weapon` for STI)
2. Use `TableNames` enum for `__tablename__` (add entry to `tablenames.py` first)
3. **No `__init__.py` update needed** — models are auto-imported by `pkgutil.walk_packages`
4. Generate migration: `python -m persist.database.run_migration revision -m "add <name>"`
5. Apply migration: `python -m persist.database.run_migration upgrade`

### Add a New Repository

1. Create `src/persist/repositories/<name>_repository.py`
2. Extend either `GenericRepository[ModelClass]` or `IRepository[ModelClass]` directly
3. Always wrap operations in `try/except` with `await db.rollback()` on failure

### Add a New Service

1. Create `src/services/<name>_service.py`
2. Instantiate required repositories in `__init__(self)`
3. Business logic goes here — not in routers, not in repositories

### Add a New Migration

```bash
# Inside the bot-core container:
cd /app/src
python -m persist.database.run_migration revision -m "describe change"
# Edit the generated file in persist/database/revisions/versions/
python -m persist.database.run_migration upgrade
```

### Add a New Executor

1. Create `src/utils/executors/<job_type>_executor.py` exporting `execute_<job_type>_job(job_id, payload) -> dict`
2. Use deferred imports inside the function body
3. Add dispatch case in `job_executor.py`'s `JobExecutor.execute()`
4. Register the job via the scheduler API or `register_default_jobs()` in `main.py`

---

## Code Standards

- **Python**: 3.12+
- **Linter**: Ruff (`target-version = "py312"`, `line-length = 120`) — configured in `/proj/pyproject.toml`
- **Pydantic**: v2 only — `ConfigDict(from_attributes=True)`, `.model_dump()`, never `.dict()` or `class Config`
- **Tests**: Max 2 mocks per test; prefer real objects; reference `test_combat_service.py`
- **Logging**: Use `bblogger.get_logger("component-name")`; INFO for normal ops, ERROR for failures, DEBUG for diagnostics; always include entity IDs
- **Admin mutations**: Must call `AuditService.log_action()` to produce an `AdminAuditLog` record
- **Error handling**: All repositories use `try/except` with `await db.rollback()` on write failures
- **Secrets**: Never hardcode credentials; always use environment variables

---

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_HOST` | `bounty_db` | PostgreSQL hostname |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `POSTGRES_DB` | `bountydb` | Database name |
| `POSTGRES_USER` | `bounty` | Database username |
| `POSTGRES_PASSWORD` | `bounty` | Database password |
| `DB_POOL_SIZE` | `10` | SQLAlchemy connection pool size |
| `DB_MAX_OVERFLOW` | `20` | Max connections above pool_size |
| `DB_POOL_TIMEOUT` | `30` | Connection wait timeout (seconds) |
| `DB_POOL_RECYCLE` | `3600` | Connection recycle interval (seconds) |
| `DB_ECHO` | `false` | Log all SQL statements |
| `BOT_HOST` | `0.0.0.0` | Uvicorn bind host |
| `BOT_PORT` / `PORT` | `8000` | Uvicorn bind port |
| `ACCESS_LOG` | `true` | Enable uvicorn access logging |
| `DISCORD_GATEWAY_HOST` | `discord-gateway` | Discord gateway service hostname |
| `GATEWAY_PORT` | `7999` | Discord gateway service port |
| `EXECUTOR_HOST` | `bot-core` | Self-referencing hostname for scheduler API calls |
| `EXECUTOR_PORT` | `8000` | Self-referencing port for scheduler API calls |
| `BOUNTYBOT_*` | (varies) | Override any operational `GameConstants` (e.g. `BOUNTYBOT_MAX_BOUNTIES_PER_DIVISION=10`) |

---

## Docker

- **Exposed port**: `8000`
- **Volume mount**: `./mappings/bot-core:/app/data` — log output and persistent data
- **Health check**: `GET /api/v1/health/` — returns HTTP 200 when healthy
- **Startup**: Uvicorn with `reload=True` in development mode; `HealthFilter` suppresses health-check spam in access logs
- **bblogger**: Copied from `services/shared/bblogger.py` into the image at build time

---

*Last updated: 2026-03-16*
