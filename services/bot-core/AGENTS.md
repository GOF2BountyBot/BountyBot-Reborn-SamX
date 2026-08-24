# AGENTS.md - bot-core Service

Authoritative reference for AI agents doing maintenance, troubleshooting, or feature work on the **bot-core** service.

---

## Service Overview

**bot-core** is the central FastAPI service powering the Galaxy on Fire 2 Discord game. It provides:

- Core game logic: bounty hunting, duels, tick-based combat resolution, player progression
- Player management: per-guild isolated game state, XP/tier advancement, prestige
- Ship systems: ship inventory, equipment loadouts, skin management
- Shop system: tier-gated guild shops with periodic refresh
- Inventory management: player items, Kaamo station storage
- Combat logs: persisted fight records served to the `/combat-log` Discord command
- Scheduled jobs: bounty spawning/expiry, shop refresh, temperature decay, DB backup/retention
- Database management: async SQLAlchemy ORM, Alembic migrations
- REST API: 16 auto-discovered routers, all under `/api/v1/`

---

## Technology Stack

| Technology | Version / Notes |
|---|---|
| **FastAPI** | Web framework; CORS middleware, OpenAPI docs at `/docs` and `/redoc` |
| **SQLAlchemy** | Async ORM with `AsyncSession`; `asyncpg` dialect |
| **Alembic** | Database migrations; applied by `docker-entrypoint.sh` (`run_migration upgrade`) and re-checked on startup via `MigrationManager.ensure_current()` |
| **APScheduler** | `AsyncIOScheduler` with SQLAlchemy job store (`apscheduler_jobs` table) |
| **PostgreSQL 18** | Primary database; accessed via asyncpg |
| **Pydantic v2** | Request/response schemas; `ConfigDict(from_attributes=True)`, `.model_dump()` |
| **Pillow (PIL)** | Map rendering in `map_renderer.py` |
| **httpx** | Async HTTP client used by executors and `gateway_push.py` to call discord-gateway and the local scheduler API |
| **orjson** | Fast JSON serialization (P4-T2 codec work) |
| **tenacity** | Transient-only HTTP retry with Full Jitter (`shared/http_retry.py`) |
| **bblogger** | Custom logging utility from `services/shared/bblogger.py`; vendored into `src/shared/` (with `http_retry.py`) so host runs/tests resolve it; Docker overlays the canonical copy at build time |
| **pytest** | Test runner; `asyncio_mode = auto` (root `pyproject.toml` and service-local `pytest.ini`) |
| **Ruff** | Linter/formatter; `target-version = "py313"`, `line-length = 120` |

---

## Directory Structure

```
services/bot-core/
├── Dockerfile                          # Container build (Python 3.13-slim-trixie, venv, gosu entrypoint)
├── docker-entrypoint.sh                # Root: chown /app/data → gosu botuser: run migrations, start main.py
├── pytest.ini                          # asyncio_mode=auto, module-scoped fixture loop
├── requirements.txt                    # Python dependencies
├── import_data/                        # Seed JSON files for game assets
│   ├── ship/                           # Ship definitions (one JSON per ship)
│   ├── module/                         # Ship module definitions
│   ├── primary_weapon/                 # Primary weapon definitions
│   ├── secondary_weapon/               # Secondary weapon definitions
│   ├── turret_weapon/                  # Turret weapon definitions
│   ├── commodity/                      # Tradeable commodity definitions
│   ├── criminal/                       # NPC criminal definitions
│   └── system/                         # Star system node definitions
└── src/
    ├── main.py                         # FastAPI app factory, lifespan, router auto-discovery
    ├── api/
    │   ├── routers/                    # 16 FastAPI routers (auto-discovered by main.py)
    │   │   ├── about.py                # /about — game data browsing
    │   │   ├── admin.py                # /admin — admin operations (ADMIN_USER_IDS auth, audit-logged)
    │   │   │                           #   NOT mounted (package __init__ exposes no router)
    │   │   ├── bounties.py             # /bounties — bounty lifecycle
    │   │   ├── combat_log.py           # /combat-log — persisted fight records (list + detail)
    │   │   ├── config.py               # /config — guild configuration
    │   │   ├── data.py                 # /data — seed-data upsert trigger + category list
    │   │   ├── discord_message.py      # /discord-message — persistent message refs
    │   │   ├── duels.py                # /duels — duel challenge lifecycle
    │   │   ├── health.py               # /health — service + DB health checks
    │   │   ├── inventory.py            # /inventory — player inventory management
    │   │   ├── players.py              # /players — player CRUD, XP, credits, prestige, promotion
    │   │   ├── scheduler.py            # /jobs — APScheduler job management
    │   │   ├── ships.py                # /ships — player ship ownership & loadouts
    │   │   ├── shops.py                # /shops — guild shop management
    │   │   ├── systems.py              # /systems — route finding + route map rendering
    │   │   └── users.py                # /users — Discord user accounts
    │   └── schemas/                    # 15 Pydantic v2 schema modules
    │       ├── about_schema.py
    │       ├── admin_schema.py
    │       ├── bounty_schema.py
    │       ├── combat_log_schema.py
    │       ├── config_schema.py
    │       ├── discord_message_schema.py
    │       ├── duel_schema.py
    │       ├── health_schema.py
    │       ├── inventory_schema.py
    │       ├── loadout_schema.py
    │       ├── players_schema.py
    │       ├── scheduler_schema.py
    │       ├── ships_schema.py
    │       ├── shops_schema.py
    │       └── users_schema.py
    ├── compute/
    │   └── combat_worker.py            # DB-free, picklable process-pool leaf running TickResolver fights
    ├── shared/                         # Vendored shared libs (kept in sync with services/shared/)
    │   ├── bblogger.py                 # Logging utility (TRACE level, LOG_LEVEL/LOG_FILE/LOG_TO_FILE env)
    │   └── http_retry.py               # tenacity-based transient-only retry (Full Jitter, 3 attempts)
    ├── persist/
    │   ├── database/                   # DB engine, sessions, migrations
    │   │   ├── manager.py              # DatabaseManager singleton (db_manager)
    │   │   ├── migration_manager.py    # MigrationManager wrapping Alembic
    │   │   ├── tablenames.py           # TableNames enum (single source of truth)
    │   │   ├── run_migration.py        # CLI tool for manual Alembic commands
    │   │   ├── alembic.ini             # Alembic configuration
    │   │   └── revisions/              # Alembic env.py + version scripts (0001 … 0018)
    │   ├── interfaces/
    │   │   └── repository_interface.py # IRepository[T] abstract base class
    │   ├── models/                     # 22 SQLAlchemy ORM models + Base (auto-imported)
    │   ├── repositories/               # 22 data-access repositories
    │   └── schemas/
    │       └── schema_manager.py       # SchemaManager + initialize_schema() for health checks
    ├── services/                       # 27 business-logic modules
    │   ├── _item_type_normalizer.py    # Private helper: item type normalization
    │   ├── _transaction_guards.py      # Private helper: @requires_transaction guard
    │   ├── audit_service.py
    │   ├── bounty_service.py
    │   ├── combat_balance.py           # Pure balance math: accuracy, thruster ramp, booster debuff, scanner tiers
    │   ├── combat_log_service.py       # Persist + query combat logs (list_for_player, get_detail)
    │   ├── combat_models.py            # Dataclasses + CombatResolver protocol (NOT a service)
    │   ├── combat_preflight_service.py # Pre-flight estimate before combat resolution
    │   ├── combat_resolver.py          # TickResolver — tick-based combat engine
    │   ├── combat_service.py
    │   ├── config_service.py
    │   ├── duel_service.py
    │   ├── equipment_service.py
    │   ├── exceptions.py               # Service-layer exception definitions
    │   ├── game_constants.py           # GameConstants class (env-overridable)
    │   ├── game_maths.py               # Pure math helpers (TL/reward formulas)
    │   ├── inventory_service.py
    │   ├── loadout_builder.py          # Constructs player ship loadouts
    │   ├── loadout_consistency_service.py  # Validates loadout consistency
    │   ├── loadout_effect_service.py   # Calculates loadout stat effects
    │   ├── loadout_response_service.py # Builds loadout API responses
    │   ├── map_renderer.py             # Pillow-based star map rendering
    │   ├── pathfinding_service.py      # A* pathfinding across system graph
    │   ├── player_service.py
    │   ├── shop_service.py
    │   ├── system_graph_service.py
    │   └── temperature_service.py
    │   └── builders/
    └── utils/                          # NOTE: utils/__init__.py auto-imports every module in the package
        ├── auto_seeder.py              # Idempotent startup seeder from import_data/
        ├── bounty_announcement_payload.py  # Bounty announcement/payout embed builders
        ├── data_loader.py              # JSON-file loader for game assets
        ├── emoji_service.py            # Discord application-emoji lookup (BOTTOKEN/BOTAPPID)
        ├── executor_holder.py          # Module-level holder for process/thread pools
        ├── gateway_push.py             # Non-fatal bot-core → gateway cache-push helpers (X-Internal-Auth)
        ├── job_executor.py             # JobExecutor dispatcher + run_job() entry point
        ├── offload.py                  # offload_cpu()/offload_io() seam onto the executor pools
        ├── scheduler_holder.py         # Module-level holder for the AsyncIOScheduler
        ├── shop_announcement.py        # Shop-refresh announcement embed builder + POST to gateway
        └── executors/                  # 10 async job executor modules
            ├── bounty_expire_executor.py
            ├── bounty_failsafe_cleanup_executor.py
            ├── bounty_respawn_executor.py
            ├── bounty_spawn_executor.py
            ├── db_retention_executor.py
            ├── duel_expire_executor.py
            ├── pg_backup_executor.py
            ├── shop_refresh_executor.py
            ├── temperature_decay_executor.py
```

---

## Startup Flow

`include_routers()` runs at app construction time (`create_app()`), before the lifespan starts. `main.py` then runs through the following sequence inside the `lifespan` async context manager:

```
1. db_manager.initialize()
   └── Creates AsyncEngine + sessionmaker with asyncpg
   └── Connection pooling (DB_POOL_SIZE, DB_MAX_OVERFLOW, etc.)

2. MigrationManager.from_async_url(connection_string).ensure_current()
   └── Converts asyncpg URL → psycopg2 URL
   └── Runs Alembic "upgrade head" if any pending revisions exist
   └── BLOCKS startup if migration fails
   └── (docker-entrypoint.sh has already run `run_migration upgrade` before main.py)

3. initialize_schema(db_manager)
   └── Builds SchemaManager for health-check endpoints (informational only)
   └── Stores on app.state.schema_manager and app.state.db_manager

4. MapRenderer + SystemGraphService pre-warm (P3-T7, non-fatal)
   └── One shared instance each on app.state.map_renderer / app.state.system_graph

5. run_stale_state_recovery_sweep()  (B.14, non-fatal)
   └── Bulk-marks stale active bounties / pending duels as expired
   └── B.23b: deletes Discord announcements of stale bounties (best-effort)

6. auto_seed_data()
   └── Checks each of 8 game-data tables (ship, primary_weapon, secondary_weapon,
       turret_weapon, module, criminal, system, commodity)
   └── Loads JSON files from import_data/<category>/ if table is empty
   └── Serialized across uvicorn workers via fcntl file lock; non-fatal

7. AsyncIOScheduler initialization (serialized via fcntl lock /tmp/bountybot_scheduler_init.lock)
   └── Creates sync SQLAlchemy engine (postgresql:// not postgresql+asyncpg://)
   └── SQLAlchemyJobStore persists jobs to apscheduler_jobs table
   └── scheduler.start(); instance stored on app.state.scheduler AND in
       utils/scheduler_holder (B.23a — executors schedule jobs directly, no HTTP hop)
   └── register_default_jobs() — idempotent, skips already-registered jobs:
       - bounty_spawn_default              (every BOUNTY_DELAY_RANDOM_MIN min, default 5; jitter 180 s)
       - shop_refresh_default              (every 6 hours)
       - temperature_decay_default         (every 1 hour)
       - bounty_failsafe_cleanup_default   (every hour at :30)
       - pg_backup_default                 (every 3 hours at :15)
       - db_retention_default              (daily at 03:45 UTC)
   └── run_stale_respawn_recovery() — re-fires bounty respawns missed while offline

8. Executor pools (forkserver — fork is unsafe in a multithreaded process)
   └── multiprocessing.set_forkserver_preload(["compute.combat_worker"])
   └── ProcessPoolExecutor(max_workers=PROCESS_POOL_WORKERS, default 3)
   └── ThreadPoolExecutor(max_workers=THREAD_POOL_WORKERS, default max(4, 2×process))
   └── Registered in utils/executor_holder; consumed via utils/offload.py

9. yield (serve requests)

10. Shutdown: scheduler.shutdown(wait=False), process/thread pool shutdown(wait=True),
    db_manager.shutdown()
```

---

## All 16 Routers

| File | Prefix | Tags | Key Endpoints |
|---|---|---|---|
| `about.py` | `/about` | about | GET /categories, GET /categories/{category}/objects, GET /object/name/{name}, GET /object/alias/{alias}, GET /object/{category}/{id}, GET /ships/{name}/render-info |
| `admin.py` | `/admin` | admin | POST /guilds/initialize, POST /guilds/{id}/reset, DELETE /guilds/{id}/uninstall, DELETE /guilds/{id}/cleanup, PUT /players/credits, PUT /players/xp, POST /players/{id}/reset, POST /players/inventory/add, POST /shops/refresh, PUT /shops/config, GET /system/health, GET /guilds/{id}/stats, POST /give-item, POST /remove-item, POST /give-ship, POST /remove-ship — mutations audit-logged via AuditService |
| `bounties.py` | `/bounties` | bounties | POST /check, POST /combat-bonus, GET / (active), GET /{id}/route, POST /spawn, GET /{id}/loadout, GET /{id}/map, DELETE /guild/{id}/clear, POST /guild/{id}/admin-spawn |
| `combat_log.py` | `/combat-log` | combat-log | GET ?user_id&guild_id&limit (newest-first list), GET /{battle_id}?user_id (404 unless requester is a combatant) |
| `config.py` | `/config` | config | GET/PUT /guild/{id}, PUT /guild/{id}/shop, POST /guild/{id}/reset, PUT /guild/{id}/admin-role/{role_id}, PUT /guild/{id}/starting-credits/{n}, PUT /guild/{id}/xp-thresholds, GET /guild/{id}/validate, GET /guilds, GET/PUT /guild/{id}/bounty, GET /defaults, GET /guild/{id}/game-constants, POST /guild/{id}/game-constants/reset |
| `data.py` | `/data` | data | POST /{category} (upsert seed JSON), GET /categories |
| `discord_message.py` | `/discord-message` | discord-message | POST, PUT, GET (composite-key/type/guild/channel lookups), DELETE — persistent Discord message reference management |
| `duels.py` | `/duels` | duels | GET /outgoing, GET /pending, POST /challenge, POST /{id}/accept, POST /{id}/reject, POST /{id}/cancel, GET /pending-all, POST /admin-cancel-all, POST /{id}/admin-cancel |
| `health.py` | `/health` | health | GET (comprehensive), GET /simple, GET /readiness, GET /liveness, GET /database |
| `inventory.py` | `/inventory` | inventory | GET /player/{id}?include_ships, GET /player/{id}/summary?include_ships, POST /add, POST /remove, POST /transfer, GET /player/{id}/search, GET /player/{id}/item/{name}/count, GET /player/{id}/validate/{ship}/{item}, POST /player/{id}/consolidate — `include_ships=true` adds the player's INACTIVE ships (active ship counts as equipped) |
| `players.py` | `/players` | players | POST / (create-or-get), GET /{id}, GET /guild/{id}, PUT /{id}/credits, PUT /{id}/xp, POST /{id}/prestige, GET /{id}/statistics, GET /{id}/promotion-status, GET /{id}/combat-preflight, PUT /{id}/promote, PUT /{id}/demote, GET /{id}/loadout, PUT /{guild_id}/{user_id}/cooldown/reset, POST /transfer |
| `scheduler.py` | *(none)* | job-scheduler | GET /jobs, GET /jobs/{id}, POST /jobs (one-time), POST /jobs/recurring, PUT /jobs/{id}, DELETE /jobs/all, DELETE /jobs/guild/{id}, DELETE /jobs/{id}, POST /reset |
| `ships.py` | `/ships` | ships | GET /player/{id}, GET /{ship_id}, POST / (grant), GET /player/{id}/active, PUT /{id}/set-active, PUT /{id}/loadout, PUT /{id}/nickname, POST /{id}/equip-check, POST /{id}/equip, POST /{id}/unequip, GET /{id}/loadout, DELETE /{id}, POST /transfer — player ship ownership (ship *definitions* live under /about) |
| `shops.py` | `/shops` | shops | GET /guild/{id}/tier/{tier}, GET /guild/{id}/summary, POST /purchase, POST /purchase-ship, POST /sell, POST /sell-ship, POST /refresh, GET /guild/{id}/tier/{tier}/stats, GET …/tech-level/{tl}, GET /guild/{id}/refresh-status, GET /item/{shop_item_id}, PUT /guild/{id}/prices |
| `systems.py` | `/systems` | systems | GET /route?start&end (A* pathfinding), GET /route/map (PNG, bounded LRU cache) |
| `users.py` | `/users` | users | POST /, GET /{user_id}, PUT /{user_id}, GET /, POST /{user_id}/get-or-create |

> **Router auto-discovery**: `main.py` uses `pkgutil.iter_modules()` to scan `api/routers/`. Any module in that package that exposes a `router` attribute is automatically mounted at `/api/v1`. No manual registration in `main.py` is needed. The scan is non-recursive: subpackages are only mounted if their `__init__.py` exposes a `router`.

---

## All 22 Models (+ Base)

| File | Class | Parent | Table Name | Purpose |
|---|---|---|---|---|
| `base.py` | `Base` | `DeclarativeBase` | — | SQLAlchemy declarative base; all models inherit from this |
| `admin_audit_log.py` | `AdminAuditLog` | `Base` | `admin_audit_logs` | Immutable audit trail for admin mutations (table name hardcoded — the one model NOT in `TableNames`) |
| `bounty.py` | `Bounty` | `Base` | `bounty` | Active bounty: criminal route, reward, checked systems, status, respawn_time |
| `combat_log.py` | `CombatLog` | `Base` | `combat_log` | One row per resolved fight; denormalized combatant names/user IDs, winner, full event-tick timeline in JSONB `data` |
| `commodity.py` | `Commodity` | `Item` | `commodity` | STI leaf: tradeable commodity; adds tech_level, subcategory, extra_atts; `polymorphic_identity='commodity'` |
| `criminal.py` | `Criminal` | `Base` | `criminal` | NPC criminal definitions: faction, is_player flag, aliases |
| `discord_message.py` | `DiscordMessage` | `Base` | `discord_message` | Persistent Discord message reference (guild, channel, message IDs) |
| `duel_request.py` | `DuelRequest` | `Base` | `duel_requests` | Duel challenge: challenger, target, stakes, status, expiry |
| `guild_config.py` | `GuildConfig` | `Base` | `guild_configs` | Per-guild bot configuration: starting credits, channels, settings, per-guild game-constant overrides |
| `guild_shop.py` | `GuildShop` | `Base` | `guild_shops` | Guild shop inventory; `tech_level` stores each ITEM's real tech level (sell-back: catalog TL, ships via `ship_tech_level_for_value`; refresh: the per-item drawn TL — the batch TL only appears in `refresh_details` for announcements) |
| `item.py` | `Item` | `Base` | `item` | **STI root**: columns: id, name, aliases, built_in, emoji, icon, value, wiki, type |
| `weapon.py` | `Weapon` | `Item` | `weapon` | STI intermediate: adds tech_level, extra_atts (JSON); `polymorphic_identity='weapon'` |
| `module.py` | `Module` | `Item` | `module` | Ship module items; adds tech_level, max_equipped, extra_atts; subtype discrimination via `Item.type` (STI discriminator — no separate `module_type` column) |
| `primary_weapon.py` | `PrimaryWeapon` | `Weapon` | `primary_weapon` | Primary weapon; adds dps; `polymorphic_identity='primary_weapon'` |
| `secondary_weapon.py` | `SecondaryWeapon` | `Weapon` | `secondary_weapon` | Secondary weapon; adds dps, ammo; `polymorphic_identity='secondary_weapon'` |
| `turret_weapon.py` | `TurretWeapon` | `Weapon` | `turret_weapon` | Turret weapon; adds dps; `polymorphic_identity='turret_weapon'` |
| `player.py` | `Player` | `Base` | `players` | Per-guild player state: credits, XP, tier, duel stats, bounty stats, active_ship_id |
| `player_inventory.py` | `PlayerInventory` | `Base` | `player_inventories` | Player → item ownership with quantity and location (equipped/kaamo/ship) |
| `player_ship.py` | `PlayerShip` | `Base` | `player_ships` | Player → ship association with loadout (equipped weapons, modules) and nickname. The `manual_turret_mode` column was DROPPED by migration 0018 — turret/primary switching is now range-driven inside the resolver |
| `schema_version.py` | `SchemaVersion` | `Base` | `schema` | Alembic migration tracking; read by SchemaManager for health checks |
| `ship.py` | `Ship` | `Base` | `ship` | Ship definitions: stats, weapon slots, compatible skins, model filenames, extra_atts |
| `system.py` | `System` | `Base` | `system` | Star system node: neighbours (adjacency), faction, coordinates, security |
| `user.py` | `User` | `Base` | `users` | Discord user account: discord_id, username; links to multiple Players |

### Model Inheritance (STI)

```
Base (DeclarativeBase)
├── Item  [table: item]  ← STI root, discriminator: type column
│   ├── Commodity  [table: commodity]
│   ├── Module  [table: module]
│   └── Weapon  [table: weapon]  ← intermediate
│       ├── PrimaryWeapon  [table: primary_weapon]
│       ├── SecondaryWeapon  [table: secondary_weapon]
│       └── TurretWeapon  [table: turret_weapon]
├── AdminAuditLog  [table: admin_audit_logs]
├── Bounty  [table: bounty]
├── CombatLog  [table: combat_log]
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

> The `models/__init__.py` uses `pkgutil.walk_packages` to auto-import all model modules, ensuring all classes are registered with SQLAlchemy's mapper before any session is created. (`Ship`, `Criminal`, and `System` carry a cosmetic `polymorphic_identity` in `__mapper_args__` but inherit `Base` directly — they are NOT part of the `Item` STI hierarchy.)

---

## All 22 Repositories

| File | Class | Purpose |
|---|---|---|
| `generic_repository.py` | `GenericRepository[T]` | Base implementation of `IRepository[T]`: add, create_or_update, get_by_id, get_by_name, get_by_names, get_by_alias, list_all, remove |
| `admin_audit_log_repository.py` | `AdminAuditLogRepository` | Minimal stub: `count`, `delete_older_than` (used by db_retention executor). Writes still go through `AuditService.log_action`. |
| `bounty_repository.py` | `BountyRepository` | Bounty CRUD + get_by_id_for_update, get_active_by_guild, get_active_by_guild_and_division, count_active_by_guild_and_division, delete_terminal_older_than, delete_by_guild_id |
| `combat_log_repository.py` | `CombatLogRepository` | CombatLog add/list + get_subpath_for_detail (JSONB sub-path read), list_for_player, delete_by_guild_id, delete_older_than |
| `commodity_repository.py` | `CommodityRepository` | Commodity upsert (create_or_update) on top of GenericRepository |
| `config_repository.py` | `ConfigRepository` | GuildConfig CRUD + get_by_guild_id, create_default_config, update_shop_config, reset_to_defaults, update_admin_role, update_starting_credits, update_xp_thresholds, get_config_summary |
| `criminal_repository.py` | `CriminalRepository` | Criminal upsert on top of GenericRepository |
| `discord_message_repository.py` | `DiscordMessageRepository` | DiscordMessage CRUD + composite-key / type / guild / channel / reference lookups and deletes |
| `duel_repository.py` | `DuelRepository` | DuelRequest CRUD + get_by_id_for_update, get_pending_by_players, get_pending_by_challenger, get_pending_by_target, get_active_by_guild, update_status, delete_expired, delete_terminal_older_than |
| `inventory_repository.py` | `InventoryRepository` | PlayerInventory CRUD + get_player_items(_by_types/_by_name), get_player_item(_by_types), add_item, remove_item, update_quantity, get_item_count_by_type |
| `item_repository.py` | `ItemRepository` | Item base queries: get_by_name(_any_type), get_all_by_tech_level, get_random_by_tech_level, get_count |
| `module_repository.py` | `ModuleRepository` | Module get_by_name + upsert (subtype queries use `Item.type` STI discriminator, not a `module_type` column) |
| `player_repository.py` | `PlayerRepository` | Player CRUD + get_by_id_for_update (FOR UPDATE lock), get_by_ids, get_by_user_and_guild, get_players_by_guild, get_players_by_user, get_guild_stats, update_credits, update_xp, update_tier, update_active_ship |
| `player_ship_repository.py` | `PlayerShipRepository` | PlayerShip CRUD + get_player_ships, get_active_ship, set_active_ship, update_loadout, add_equipment, remove_equipment, update_nickname, get_ships_by_name, get_ship_loadout_summary |
| `primary_weapon_repository.py` | `PrimaryWeaponRepository` | PrimaryWeapon get_by_name + upsert |
| `secondary_weapon_repository.py` | `SecondaryWeaponRepository` | SecondaryWeapon get_by_name + upsert |
| `ship_repository.py` | `ShipRepository` | Ship upsert on top of GenericRepository |
| `shop_repository.py` | `ShopRepository` | GuildShop CRUD + get_shop_items(_by_types), get_shop_item_by_name, update_quantity, clear_shop_tier, clear_all_guild_shops, get_guild_shops_summary, get_items_by_tech_level, update_prices, get_items_due_for_refresh, get_shop_statistics |
| `system_repository.py` | `SystemRepository` | System upsert on top of GenericRepository |
| `turret_weapon_repository.py` | `TurretWeaponRepository` | TurretWeapon get_by_name + upsert |
| `user_repository.py` | `UserRepository` | User CRUD + get_by_discord_id, get_by_ids, get_or_create_user |
| `weapon_repository.py` | `WeaponRepository` | Weapon base queries (parent of primary/secondary/turret repos) |

> See `src/persist/repositories/AGENTS.md` for the full repository pattern documentation.

---

## All 27 Service Modules

| File | Key Class(es) | Purpose |
|---|---|---|
| `_item_type_normalizer.py` | *(private helper)* | Internal item type normalization utilities |
| `_transaction_guards.py` | `requires_transaction` | Decorator guard for methods that must run inside a caller-owned transaction |
| `audit_service.py` | `AuditService` | Static `log_action()` only; records admin mutations to AdminAuditLog; failures are swallowed (never block primary operation) |
| `bounty_service.py` | `BountyService`, `CheckResult`, `CheckResponse`, `MultiCheckResponse`, `RewardInfo` | spawn_bounty, check_bounty, calc/distribute_rewards, expire_bounty, escape_bounty, respawn_bounty, clear_bounties, generate_loadout, select_criminal |
| `combat_balance.py` | `ScannerTier` + pure functions | Balance math: weapon/pilot accuracy, thruster ramp (attacker-side), booster debuff (defender-side), scanner tier resolution |
| `combat_log_service.py` | `CombatLogService` | persist(), list_for_player(), get_detail() — backs the /combat-log router |
| `combat_models.py` | `WeaponStats`, `ModuleStats`, `UpgradeStats`, `ShipLoadout`, `CombatStats`, `FightStats`, `FightResults`, `CombatMeta`, `CombatEvent`, `CombatEventType`, `CombatResolver` (protocol) | Pure dataclasses + protocol; NOT a service class. `WeaponStats.automatic` distinguishes auto- vs manual-turrets; there is no per-ship manual_turret_mode flag |
| `combat_preflight_service.py` | `CombatPreflightService`, `PreflightVerdict`, `PreflightResult` | estimate() — pre-flight verdict before combat resolution |
| `combat_resolver.py` | `TickResolver` | Tick-based combat engine: shields/armour, module effects, secondaries, turrets. Manual turrets (automatic=False) fire ONLY while no primary weapon is in range (range-driven gap-closer); auto-turrets fire whenever in range |
| `combat_service.py` | `CombatService` | Stat collection + fight orchestration; `fight_ships` is async and routes exclusively through TickResolver (SimpleTTKResolver retired), running in a process-pool worker via `offload_cpu` |
| `config_service.py` | `ConfigService` | GuildConfig CRUD with defaults, shop/bounty config, admin role, XP thresholds, guild uninstall/clear, per-guild game-constants reset |
| `duel_service.py` | `DuelService` | create_challenge, accept_duel, reject_duel, cancel_duel, expire_duel, cancel_all_pending_duels, cancel_underfunded_duels, pending/outgoing queries; calls CombatService |
| `equipment_service.py` | `EquipmentService` | equip_item/unequip_item/equip_check on a PlayerShip; per-class module limits from `GameConstants.MODULE_EQUIP_LIMITS` |
| `exceptions.py` | *(exception classes)* | Service-layer exception definitions |
| `game_constants.py` | `GameConstants`, `resolve_constant()` | Centralized constants class; operational constants overridable via `BOUNTYBOT_{KEY}` env vars (call `GameConstants.load()` at startup); `resolve_constant()` layers per-guild overrides on top |
| `game_maths.py` | `pick_random_item_tl()`, `reward_per_sys_check()`, `ship_tech_level_for_value()` | Pure math helpers (TL selection, bounty reward formula, ship value → TL) |
| `inventory_service.py` | `InventoryService` | get_player_inventory / get_inventory_summary (both accept `include_ships` — when true, inactive player ships are listed/counted; the active ship counts as equipped), add/remove/transfer items, search, validate compatibility, consolidate; owns inventory/player/player_ship/ship/weapon/module repos |
| `loadout_builder.py` | `LoadoutBuilder` | Constructs `ShipLoadout` dataclasses from equipped items |
| `loadout_consistency_service.py` | `LoadoutConsistencyService` | Validates loadout consistency and constraint enforcement |
| `loadout_effect_service.py` | `LoadoutEffectService` | Calculates stat effects of a ship loadout |
| `loadout_response_service.py` | `LoadoutResponseService` | Builds the shared `LoadoutResponse` payload (players + bounties endpoints) |
| `map_renderer.py` | `MapRenderer` | Pillow-based star map image generation; shared instance pre-warmed at startup |
| `pathfinding_service.py` | `PathfindingService`, `PathfindingError` | A* pathfinding over the star system graph |
| `player_service.py` | `PlayerService`, `TierChangeCooldownError` | get_or_create_player, update_player_credits/xp, promotion-status / promote / demote (tier-change cooldown), prestige_player, transfer_credits, get_player_statistics |
| `shop_service.py` | `ShopService` | get_shop_items, purchase_item/ship, sell_item/ship, refresh_shop; tier-gated; `_get_item_tech_level` resolves each item's REAL tech level for `guild_shops.tech_level` (catalog TL for items, `ship_tech_level_for_value` for ships) |
| `system_graph_service.py` | `SystemGraphService`, `SystemNode` | Loads system adjacency graph; shared instance pre-warmed at startup; used by PathfindingService |
| `temperature_service.py` | `TemperatureService` | Static helpers: raise/decay temperature, get_max_bounties(temperature), calculate_spawn_delay; guild activity temperature affects spawn cap |

> See `src/services/AGENTS.md` for service interaction patterns and constructor injection details.

---

## All 15 Schema Modules

| File | Key Classes | Purpose |
|---|---|---|
| `about_schema.py` | `ItemResponse` + `ShipResponse`, `ModuleResponse`, weapon/criminal/system/commodity responses | Game data browsing response models |
| `admin_schema.py` | `InitializeGuildRequest`, `UpdatePlayerCreditsRequest`, `AdminGiveItemRequest`, `SystemHealthResponse`, … | Admin operation request/response |
| `bounty_schema.py` | `BountyResponse`, `BountyPublicResponse`, `BountyCheckRequest`, `BountyCheckResponse`, `CombatBonusRequest/Response` | Bounty lifecycle request/response |
| `combat_log_schema.py` | `CombatLogListItem`, `CombatLogDetail`, `CombatantSummary`, `KeyEvent` | Combat log list + detail payloads |
| `config_schema.py` | `GameConstantsOverridesMixin`, `GuildConfigResponse`, `UpdateConfigRequest`, bounty-config models | Guild configuration |
| `discord_message_schema.py` | `DiscordMessageRequest`, `DiscordMessageResponse`, `EmbedPayloadDict` | Discord message persistence |
| `duel_schema.py` | `DuelRequestCreate`, `DuelRequestResponse`, `DuelResultResponse` | Duel challenge lifecycle |
| `health_schema.py` | `HealthResponse`, `SimpleHealthResponse` | Health check payloads |
| `inventory_schema.py` | `InventoryItemResponse`, `InventorySummaryResponse`, `AddItemRequest`, `TransferItemRequest` | Inventory management |
| `loadout_schema.py` | `LoadoutResponse` + `ShipStats`, `LoadoutWeaponItem`, `LoadoutModuleItem`, `CargoItem`, `EffectItem` | Shared loadout payload (players + bounties) |
| `players_schema.py` | `PlayerResponse`, `CreatePlayerRequest`, `UpdateCreditsRequest`, `UpdateXPRequest`, `TransferCreditsRequest`, `PrestigeResponse`, `PromoteResponse`, `DemoteResponse`, `TierChangeCooldownResponse` | Player management |
| `scheduler_schema.py` | `JobInfo`, `OneTimeJob`, `RecurringJob`, `UpdateJob` | APScheduler job management |
| `ships_schema.py` | `ShipResponse`, `ShipLoadoutSummaryResponse`, `EquipItemRequest`, `TransferShipRequest`, … | Player ship management |
| `shops_schema.py` | `ShopItemResponse`, `ShopSummaryResponse`, `PurchaseRequest`, `SellRequest`, `TransactionResponse` | Shop transaction requests/responses |
| `users_schema.py` | `UserResponse`, `CreateUserRequest`, `UpdateUserRequest` | Discord user account management |

All schemas use **Pydantic v2** conventions:
```python
model_config = ConfigDict(from_attributes=True)   # NOT class Config
obj.model_dump()                                   # NOT obj.dict()
```

> See `src/api/schemas/AGENTS.md` for the full schema inventory and conventions.

---

## Database / Persistence Layer

### DatabaseManager (`persist/database/manager.py`)

Singleton `db_manager` instance manages the entire async connection lifecycle:

- `db_manager.initialize()` — creates `AsyncEngine` + `async_sessionmaker`; call once on startup
- `db_manager.get_session()` — async context manager yielding an `AsyncSession`
- `get_db_session()` — module-level alias for `db_manager.get_session()`
- `db_manager.get_health_info()` — connectivity/pool info for the health endpoints
- `db_manager.shutdown()` — disposes engine; call on shutdown
- Connection pool config via env vars: `DB_POOL_SIZE` (default 40), `DB_MAX_OVERFLOW` (20), `DB_POOL_TIMEOUT` (30), `DB_POOL_RECYCLE` (3600), `DB_ECHO` (false)

### MigrationManager (`persist/database/migration_manager.py`)

Wraps Alembic to provide zero-friction schema management:

- `MigrationManager.from_async_url(url)` — converts asyncpg URL to sync psycopg2 URL
- `MigrationManager.from_env()` — builds sync URL from `POSTGRES_*` env vars
- `.ensure_current()` — runs `alembic upgrade head` if any pending revisions; **called on every startup**
- `.auto_generate(message)` — generates a new autogenerated revision
- `.detect_pending()` / `.get_current_revision()` / `.get_head_revision()` / `.history()` — introspection
- `.downgrade(target)` — manual rollback (upgrades go through `ensure_current()` or the CLI)

### SchemaManager (`persist/schemas/schema_manager.py`)

Reads the `schema` table (SchemaVersion model) to provide version info for health checks (`initialize_schema(db_manager)` builds it at startup). Not involved in migration logic.

### TableNames Enum (`persist/database/tablenames.py`)

Single source of truth for database table names. Use `TableNames.X.value` in model `__tablename__` declarations — never hardcode strings. (Known exception: `admin_audit_log.py` hardcodes `"admin_audit_logs"`.)

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

## Compute & Shared

### compute/combat_worker.py

DB-free, picklable process-pool leaf: `run_fight()` executes a TickResolver fight inside a forkserver worker. **Import discipline is strict** — the module must never import fastapi/sqlalchemy/persist/utils.executors at top level (a forkserver child imports it in a fresh interpreter). It lives at `compute/` (not under `utils/`) precisely to avoid `utils/__init__.py`'s auto-importer.

### shared/ (vendored)

- `bblogger.py` — logging utility with TRACE level; configured via `LOG_LEVEL`, `LOG_FILE`, `LOG_TO_FILE`
- `http_retry.py` — tenacity-based transient-only retry: 3 attempts, Full-Jitter exponential backoff, retries connection errors/timeouts/5xx/429 only

The canonical copies live in `services/shared/`; the Docker build overlays them via `COPY ./services/shared /app/src/shared`. The in-repo `src/shared/` copy lets host-side runs and tests import `shared.*` directly.

---

## Utils

> `utils/__init__.py` auto-imports **every** module in the package (`pkgutil.walk_packages`) — importing `utils` pulls in all executors and their transitive deps. Keep process-pool leaf code out of this package (see `compute/`).

### auto_seeder.py

Idempotent startup seeder. For each category in `SEED_CATEGORIES` (`ship`, `primary_weapon`, `secondary_weapon`, `turret_weapon`, `module`, `criminal`, `system`, `commodity`):
1. Checks if the table is empty
2. If empty, calls `load_data(category)` to import all JSON files from `import_data/<category>/`
3. Missing directories are skipped gracefully (non-fatal)

Cross-worker safe via an fcntl file lock (`/tmp/bountybot_auto_seed.lock`).

### data_loader.py

`load_data(category)` reads all `.json` files from `import_data/<category>/` and calls `repo.create_or_update()` for each. `get_repository(category)` maps category strings to repository instances. Module seeds get emoji placeholders resolved via `EmojiService`.

### emoji_service.py

Fetches Discord **application emojis** via the Discord HTTP API (requires `BOTTOKEN` and `BOTAPPID` env vars) and normalizes object names to emoji names. Used by `data_loader.py` at seed time.

### executor_holder.py / scheduler_holder.py / offload.py

- `executor_holder` — module-level set/get for the process and thread pools built at startup
- `scheduler_holder` — module-level set/get for the `AsyncIOScheduler` (B.23a: executors schedule jobs directly instead of HTTP round-trips)
- `offload.py` — the single seam for moving work off the event loop: `offload_cpu()` → ProcessPoolExecutor (args/returns MUST be picklable), `offload_io()` → ThreadPoolExecutor

### gateway_push.py

Non-fatal bot-core → discord-gateway cache-push helpers (`push_combatlog_invalidate*`). Builds SSRF-guarded URLs from `DISCORD_GATEWAY_HOST`/`GATEWAY_PORT`, authenticates with `INTERNAL_AUTH_TOKEN` (`x-internal-auth` header), and swallows all failures as warnings.

### bounty_announcement_payload.py / shop_announcement.py

Embed builders for bounty announcements/payouts and shop-refresh announcements POSTed to the gateway.

### job_executor.py

`JobExecutor.execute(job_id, payload)` dispatches on `payload["job_type"]`:

| job_type | Executor function |
|---|---|
| `shop_refresh` | `execute_shop_refresh_job` |
| `bounty_spawn_orchestrate` | `execute_bounty_spawn_orchestrate_job` |
| `bounty_spawn_one` | `execute_bounty_spawn_one_job` |
| `bounty_expire` | `execute_bounty_expire_job` |
| `bounty_respawn` | `execute_bounty_respawn_job` |
| `bounty_failsafe_cleanup` | `execute_bounty_failsafe_cleanup_job` |
| `duel_expire` | `execute_duel_expire_job` |
| `temperature_decay` | `execute_temperature_decay_job` |
| `pg_backup` | `execute_pg_backup_job` |
| `db_retention` | `execute_db_retention_job` |

`run_job(job_id, payload)` is the APScheduler entry point (must be picklable).

### Executors (`utils/executors/`)

| File | job_type(s) | Trigger | Purpose |
|---|---|---|---|
| `bounty_spawn_executor.py` | `bounty_spawn_orchestrate`, `bounty_spawn_one` | Every N min (default 5, jittered) / one-time per tier | Orchestrate: check each guild×division for open slots and fan out staggered one-time spawn jobs; spawn: call BountyService.spawn_bounty(), schedule expiry, announce to discord-gateway |
| `bounty_expire_executor.py` | `bounty_expire` | One-time at bounty.end_time | Mark bounty expired; delete its announcement; optionally schedule respawn |
| `bounty_failsafe_cleanup_executor.py` | `bounty_failsafe_cleanup` | Every hour at :30 | Clean up stale/orphaned bounty state that missed normal expiry |
| `bounty_respawn_executor.py` | `bounty_respawn` | One-time at bounty.respawn_time | Regenerate route, flip escaped bounty back to active, announce respawn |
| `db_retention_executor.py` | `db_retention` | Daily at 03:45 UTC | Delete old rows: terminal bounties (24 h), terminal duels (24 h), audit logs (30 d), bounty combat logs (48 h), PvP duel combat logs (1 yr; 0=permanent) — windows from GameConstants |
| `duel_expire_executor.py` | `duel_expire` | Via /jobs API (no default job) | Find pending duels past `expires_at`; mark as expired |
| `pg_backup_executor.py` | `pg_backup` | Every 3 hours at :15 | zstd-compressed PostgreSQL dump to `BACKUP_DIR` (default `/app/data/backups`); retains `BACKUP_RETAIN_DAYS` (default 7) |
| `shop_refresh_executor.py` | `shop_refresh` | Every 6 hours | Call ShopService.refresh_shop() for all guild configs |
| `temperature_decay_executor.py` | `temperature_decay` | Every 1 hour | Apply guild temperature decay via TemperatureService |

> All executor functions use **deferred imports** (imports inside the function body) to avoid transitive ORM dependencies at module load time — important for clean test isolation.

> See `src/utils/executors/AGENTS.md` for full executor documentation.

---

## Import Data

JSON files in `import_data/` are the source of truth for game assets. They are loaded into the database by `auto_seeder.py` on first startup (or on demand via `POST /api/v1/data/{category}`).

| Directory | Model | Notes |
|---|---|---|
| `ship/` | `Ship` | One JSON file per ship; includes stats, weapon slots, skin info |
| `module/` | `Module` | One JSON per module; includes `type` (STI discriminator, e.g. `ArmourModule`), tech_level |
| `primary_weapon/` | `PrimaryWeapon` | One JSON per weapon; includes dps, tech_level |
| `secondary_weapon/` | `SecondaryWeapon` | One JSON per weapon; includes dps, ammo, tech_level |
| `turret_weapon/` | `TurretWeapon` | One JSON per weapon; includes dps, tech_level |
| `commodity/` | `Commodity` | One JSON per commodity; subcategory, tech_level, price metadata in extra_atts |
| `criminal/` | `Criminal` | One JSON per criminal NPC; includes faction |
| `system/` | `System` | One JSON per system node; includes neighbours (adjacency list) |

---

## Testing

- **166 test files**
- Runner: `pytest` with `asyncio_mode = auto` (root `pyproject.toml` + service-local `pytest.ini`)

### Running Tests

**IMPORTANT — always log to file.** Without capturing output, any failure detail is lost and requires a full re-run to recover.

```bash
# Full suite (from /proj):
cd /proj/services/bot-core && timeout 300 python -m pytest tests/ -q --tb=short 2>&1 | tee /tmp/test-botcore.log | tail -20

# Targeted subset (faster iteration):
cd /proj/services/bot-core && timeout 120 python -m pytest tests/services/ -q --tb=short 2>&1 | tee /tmp/test-botcore-services.log | tail -20
cd /proj/services/bot-core && timeout 120 python -m pytest tests/api/ -q --tb=short 2>&1 | tee /tmp/test-botcore-api.log | tail -20

# Single file:
cd /proj/services/bot-core && timeout 60 python -m pytest tests/services/test_combat_preflight_service.py -q --tb=short 2>&1 | tee /tmp/test-single.log | tail -20

# Grep failures without re-running:
grep -A 20 "FAILED\|ERROR" /tmp/test-botcore.log
```

### Test Organization

```
tests/
├── conftest.py             # Mocks shared.bblogger; loads REAL shared.http_retry from src/; shared fixtures
├── fixtures/
│   └── game_data.py        # Real game data fixtures (ships, weapons, criminals, systems)
├── api/                    # Router-level tests (23 files)
│   ├── conftest.py         # API test fixtures (TestClient, app setup)
│   └── test_*.py           # One file per router (plus loadout, etc.)
├── services/               # Service-level tests (51 files)
│   ├── conftest.py         # Service test fixtures
│   └── test_*.py
├── repositories/           # Repository tests
│   └── test_*.py
├── integration/            # Integration tests (live-DB flows, retention, cross-session persistence)
└── test_*.py               # Top-level: executor, migration, startup, offload/worker, concurrency tests
```

### conftest.py Setup

The top-level `conftest.py` does the following before any test:
1. Inserts `src/` and `tests/` on `sys.path`
2. Mocks `shared.bblogger` (flat mock module) so app code imports cleanly
3. Loads the **real** `shared.http_retry` from `src/shared/` so executor imports resolve
4. Provides shared fixtures (`mock_db_manager`, etc.)

### Key Testing Conventions

- **Max 2 mocks per test** — prefer real objects with deterministic inputs (see `tests/AGENTS.md`)
- Use `fixtures/game_data.py` for real game entity fixtures (ships, weapons, etc.)
- Executor tests use deferred imports; mock `db_manager.get_session()` as an async context manager

---

## Common Tasks

### Add a New Router

1. Create `src/api/routers/<name>.py` with a `router = APIRouter(prefix="/<name>", tags=["<name>"])` attribute
2. Create `src/api/schemas/<name>_schema.py` with Pydantic v2 models
3. **No registration needed** — `main.py` auto-discovers routers via `pkgutil.iter_modules()` (non-recursive — subpackages need a `router` in their `__init__.py`)
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
4. Register the job via the scheduler API or `DEFAULT_SCHEDULER_JOBS` in `main.py`

---

## Code Standards

- **Python**: 3.13+
- **Linter**: Ruff (`target-version = "py313"`, `line-length = 120`) — configured in `/proj/pyproject.toml`
- **Pydantic**: v2 only — `ConfigDict(from_attributes=True)`, `.model_dump()`, never `.dict()` or `class Config`
- **Tests**: Max 2 mocks per test; prefer real objects (see `tests/AGENTS.md`)
- **Logging**: Use `bblogger.get_logger("component-name")`; INFO for normal ops, ERROR for failures, DEBUG for diagnostics; always include entity IDs
- **Admin mutations**: Must call `AuditService.log_action()` to produce an `AdminAuditLog` record
- **Error handling**: Repositories use `try/except` with `await db.rollback()` on write failures when they own the commit (`commit=True`); with `commit=False` rollback is the caller's responsibility
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
| `DB_POOL_SIZE` | `40` | SQLAlchemy connection pool size |
| `DB_MAX_OVERFLOW` | `20` | Max connections above pool_size |
| `DB_POOL_TIMEOUT` | `30` | Connection wait timeout (seconds) |
| `DB_POOL_RECYCLE` | `3600` | Connection recycle interval (seconds) |
| `DB_ECHO` | `false` | Log all SQL statements |
| `BOT_HOST` | `0.0.0.0` | Uvicorn bind host |
| `BOT_PORT` / `PORT` | `8000` | Uvicorn bind port |
| `ACCESS_LOG` | `true` | Enable uvicorn access logging |
| `WORKERS` | `1` | Uvicorn worker count (startup is flock-serialized for >1) |
| `PROCESS_POOL_WORKERS` | `3` | Combat process pool size (must be explicit — Py 3.13 ignores cgroup quota) |
| `THREAD_POOL_WORKERS` | `max(4, 2×process)` | IO thread pool size |
| `DISCORD_GATEWAY_HOST` | `discord-gateway` | Discord gateway service hostname |
| `GATEWAY_PORT` | `7999` | Discord gateway service port |
| `INTERNAL_AUTH_TOKEN` | *(empty)* | `x-internal-auth` header for gateway cache-push calls |
| `EXECUTOR_HOST` | `bot-core` | Self-referencing hostname for executor → own-API calls |
| `EXECUTOR_PORT` | `8000` | Self-referencing port for executor → own-API calls |
| `ADMIN_USER_IDS` | *(empty = allow all, dev mode)* | Comma-separated Discord user IDs allowed on /admin endpoints |
| `BOTTOKEN` / `BOTAPPID` | *(required by EmojiService)* | Discord app credentials for application-emoji lookup at seed time |
| `BACKUP_DIR` | `/app/data/backups` | pg_backup output directory |
| `BACKUP_RETAIN_DAYS` | `7` | pg_backup retention window |
| `LOG_LEVEL` / `LOG_FILE` / `LOG_TO_FILE` | `INFO` / `app.log` / `true` | bblogger configuration |
| `BOUNTYBOT_*` | (varies) | Override any operational `GameConstants` (e.g. `BOUNTYBOT_MAX_BOUNTIES_PER_DIVISION=10`) |
| `BOUNTYBOT_BOUNTY_RETENTION_HOURS` | `24` | Terminal-state bounty rows older than this are deleted by `db_retention_default` |
| `BOUNTYBOT_DUEL_RETENTION_HOURS` | `24` | Terminal-state duel_requests rows older than this are deleted by `db_retention_default` |
| `BOUNTYBOT_AUDIT_RETENTION_DAYS` | `30` | admin_audit_logs rows older than this are deleted by `db_retention_default`; long-term audit history is preserved via pg_backup |
| `BOUNTYBOT_COMBAT_LOG_BOUNTY_RETENTION_HOURS` | `48` | bounty (PvC) combat_log rows older than this are deleted by `db_retention_default` |
| `BOUNTYBOT_COMBAT_LOG_PVP_RETENTION_HOURS` | `8760` | PvP (duel) combat_log rows older than this are deleted; `0` = never prune (permanent) |

---

## Docker

- **Exposed port**: `8000` (`BOT_PORT` mapping in compose)
- **Volume mount**: `./mappings/bot-core:/app/data` — log output, pg backups, persistent data
- **Health check**: `GET /api/v1/health/` — returns HTTP 200 when healthy
- **Startup**: `docker-entrypoint.sh` runs as root to chown `/app/data`, then drops to `botuser` via gosu, runs `python -m persist.database.run_migration upgrade`, and starts `python /app/src/main.py` (uvicorn with uvloop + httptools; no auto-reload). `HealthFilter` suppresses health-check spam in access logs
- **bblogger / http_retry**: Copied from `services/shared/` into the image at build time (`COPY ./services/shared /app/src/shared`)

---

*Last updated: 2026-06-11*
