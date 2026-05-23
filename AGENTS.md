# AGENTS.md - BountyBot-Reborn-SamX

This file provides guidance for AI agents working on this codebase. Each service also has its own `AGENTS.md` with more detailed service-specific guidance — always read the relevant service-level file before making changes within that service.

---

## Project Overview

**BountyBot-Reborn-SamX** is a containerized, GPU-ready micro-service stack powering a game-related Discord bot. The project uses FastAPI, PostgreSQL, Discord.py, CUDA, Blender, Alembic, PIL, and Docker-Compose. The stack has 185 source files and 162 test files across all services.

---

## Architecture

### Services

| Service | Tech Stack | Port | Purpose |
|---------|------------|------|---------|
| `db` | PostgreSQL 18 | 5432 | Central database |
| `bot-core` | FastAPI + SQLAlchemy + Alembic | 8000 | Core game logic, API, scheduled jobs |
| `discord-gateway` | FastAPI + Discord.py | 7999 | Discord bot + REST API; includes proactively-warmed in-process autocomplete cache |
| `blender-service` | FastAPI + Blender + PIL + CUDA | 8001 | GPU rendering, texture compositing, AEI conversion |

### Data Flow

```
discord-gateway → blender-service → bot-core → db
```

### Codebase Statistics

| Service | Source files | Test files | Routers | Models | Repos | Services | Cogs | Schemas |
|---------|-------------|------------|---------|--------|-------|----------|------|---------|
| bot-core | 124 | 84 | 15 | 21 | 19 | 16 | — | 13 |
| discord-gateway | 43 | 63 | 10 | — | — | — | 14 | 7 |
| blender-service | 17 | 14 | 6 | — | — | 6 | — | — |
| **TOTAL** | **184** | **161** | **31** | **21** | **19** | **22** | **14** | **20** |

---

## Directory Structure

```
BountyBot-Reborn-SamX/
├── docker-compose.yml              # Standard stack
├── docker-compose-gpu.yml          # GPU-enabled stack
├── .env.example                    # Environment template
├── .gitmodules                     # Git submodules
├── AGENTS.md                       # This file
├── README.md                       # Project documentation
├── pyproject.toml                  # Ruff config, shared tool settings
├── mappings/                       # Persistent data volumes (bind-mounted)
│   ├── postgres-data/              # PostgreSQL data directory
│   ├── bot-core/                   # bot-core logs
│   ├── discord-gateway/            # Gateway data
│   └── blender-renderer/           # Blender render output
└── services/
    ├── bot-core/                   # Main FastAPI application
    │   ├── Dockerfile
    │   ├── requirements.txt
    │   ├── import_data/            # Seed data (JSON files per category)
    │   │   ├── ship/
    │   │   ├── module/
    │   │   ├── primary_weapon/
    │   │   ├── secondary_weapon/
    │   │   ├── turret_weapon/
    │   │   ├── criminal/
    │   │   └── system/
    │   ├── src/
    │   │   ├── main.py             # App entrypoint, lifespan, router registration
    │   │   ├── api/
    │   │   │   ├── routers/        # 15 FastAPI routers (auto-discovered)
    │   │   │   └── schemas/        # 13 Pydantic schema modules
    │   │   ├── persist/
    │   │   │   ├── database/       # DB engine, MigrationManager, Alembic config
    │   │   │   │   ├── manager.py
    │   │   │   │   ├── migration_manager.py
    │   │   │   │   ├── run_migration.py
    │   │   │   │   ├── alembic.ini
    │   │   │   │   └── revisions/  # Alembic env + version scripts
    │   │   │   ├── interfaces/     # Abstract repository protocols
    │   │   │   ├── models/         # 21 SQLAlchemy ORM models
    │   │   │   └── repositories/   # 19 CRUD repositories
    │   │   ├── services/           # 16 business-logic service modules (B.48: division_service removed)
    │   │   ├── message_builders/   # Discord embed builder framework
    │   │   └── utils/
    │   │       ├── auto_seeder.py
    │   │       ├── data_loader.py
    │   │       ├── emoji_service.py
    │   │       ├── job_executor.py
    │   │       └── executors/      # 7 APScheduler job executors
    │   └── tests/
    ├── discord-gateway/            # Discord bot + internal REST API
    │   ├── Dockerfile
    │   ├── requirements.txt
    │   ├── src/
    │   │   ├── bot.py              # Discord.py bot setup
    │   │   ├── api/
    │   │   │   ├── routers/        # 10 internal REST routers
    │   │   │   ├── schemas/        # 7 Pydantic schema modules
    │   │   │   └── server.py       # FastAPI app factory
    │   │   ├── cogs/               # 14 Discord slash-command cogs
    │   │   └── utils/              # Helpers: embeds, converters, permissions
    │   └── tests/
    ├── blender-service/            # Blender render + texture pipeline
    │   ├── Dockerfile
    │   ├── docker-entrypoint.sh    # Auto-downloads game objects (gdown + 7z), CUDA warmup
    │   ├── requirements.txt
    │   ├── src/
    │   │   ├── main.py
    │   │   ├── routers/            # 6 router modules (health, render, jobs, textures, config, cache)
    │   │   ├── services/           # 6 service modules
    │   │   ├── assets/             # _render.py Blender script, cube.blend default scene
    │   │   ├── lib/
    │   │   │   └── AEPi/           # Git submodule: AEI format library
    │   │   └── utils/
    │   └── tests/
    ├── database/                   # Empty placeholder (uses stock postgres image)
    └── shared/
        └── bblogger.py             # Dependency-free logging utility (copied into each service)
```

---

## Key Technologies

- **FastAPI** — Web framework for bot-core, discord-gateway, and blender-service
- **Discord.py** — Discord bot library (slash commands via cogs)
- **SQLAlchemy** — Async ORM with declarative base and single-table inheritance
- **Alembic** — Database migrations managed by `MigrationManager`; auto-applied on startup
- **APScheduler** — In-process scheduled jobs (bounty spawn, shop refresh, temperature decay)
- **PostgreSQL 18** — Primary database (19 application tables + `apscheduler_jobs`)
- **Blender** — 3D rendering, GPU-accelerated via CUDA
- **PIL / Pillow** — Texture compositing pipeline
- **AEPi** (submodule) — AEI image format conversion library
- **Ruff** — Linter/formatter (target-version `py312`, line-length 120)
- **pytest** — Test runner (`asyncio_mode=auto`)
- **Docker-Compose** — Container orchestration (standard + GPU variants)

---

## Working with This Project

### Prerequisites

- Docker + Docker-Compose
- (Optional) NVIDIA drivers + `nvidia-docker` for GPU rendering
- Python 3.12+ for local development
- PostgreSQL client for database work

### Initial Setup

1. Clone the repository
2. Copy `.env.example` to `.env` and configure
3. Initialize submodules: `git submodule update --init --recursive`
4. Build and run: `docker compose up --build`

### Autocomplete Cache Environment Variables (discord-gateway)

The discord-gateway runs a proactively-warmed in-process autocomplete cache. The following env vars control its behavior (all optional — defaults shown):

| Variable | Default | Purpose |
|----------|---------|---------|
| `AUTOCOMPLETE_WARM_ACTIVE_DAYS` | `7` | Players active within N days are warmed on startup; 0 = warm everyone |
| `AUTOCOMPLETE_WARM_CONCURRENCY` | `16` | Max concurrent inventory/ships fetches during warm + refresh |
| `AUTOCOMPLETE_WARM_GUILD_STAGGER_MS` | `200` | Spacing between per-guild warm jobs at startup (ms) |
| `AUTOCOMPLETE_PLAYER_REFRESH_MINUTES` | `10` | Interval for player_cache bulk re-warm |
| `AUTOCOMPLETE_LOADOUT_REFRESH_MINUTES` | `5` | Interval for inventory/ships round-robin re-warm |
| `AUTOCOMPLETE_INVENTORY_MAX_ENTRIES` | *(unset)* | LRU cap on inventory_cache; unset = no cap |
| `AUTOCOMPLETE_SHIPS_MAX_ENTRIES` | *(unset)* | LRU cap on ships_cache; unset = no cap |
| `INTERNAL_AUTH_TOKEN` | *(unset)* | Shared secret for bot-core → gateway internal push endpoints; both services must match |

See `services/discord-gateway/AGENTS.md` → *Autocomplete Cache Architecture* for the full design.

### Game Asset Data

Located in `services/bot-core/import_data/`:
- `ship/` — Ship definitions
- `module/` — Ship modules
- `primary_weapon/` — Primary weapons
- `secondary_weapon/` — Secondary weapons
- `turret_weapon/` — Turret weapons
- `criminal/` — NPC criminal data
- `system/` — Star system definitions

### Shared Library

`services/shared/bblogger.py` — dependency-free logging utility with TRACE level, colored console output, and optional rotating file handler. Configured via `LOG_LEVEL`, `LOG_FILE`, and `LOG_TO_FILE` environment variables. Copied into each service image at build time.

### blender-service Asset Pipeline

`docker-entrypoint.sh` auto-downloads game 3D objects from Google Drive using `gdown` and `7z` on container startup. A CUDA kernel warmup script compiles shaders on first boot (controlled by `DO_WARMUP` env var).

For local development without re-downloading assets, mount the directory:
```
old-refs/items/ships/  →  /app/data/game-objects/items/ships/
```

---

## Migration System

bot-core uses **Alembic** managed through `MigrationManager`.

- On startup, `main.py` calls `MigrationManager.ensure_current()` to auto-apply any pending migrations.
- Manual migration commands are available via `run_migration.py` CLI.
- Alembic config lives in `services/bot-core/src/persist/database/alembic.ini`.
- Migration version scripts live in `services/bot-core/src/persist/database/revisions/versions/`.
- The `schema_version` table tracks applied revisions.

**Adding a new migration:**
```bash
# Inside bot-core container or with PYTHONPATH set:
python run_migration.py revision --autogenerate -m "describe the change"
python run_migration.py upgrade head
```

---

## Database Schema

### Application Tables (19)

| Table | Model | Notes |
|-------|-------|-------|
| `admin_audit_logs` | AdminAuditLog | Audit trail for all admin mutations |
| `bounty` | Bounty | Active bounties on criminals |
| `criminal` | Criminal | NPC criminal definitions |
| `discord_message` | DiscordMessage | Persistent Discord message references |
| `duel_requests` | DuelRequest | Duel challenge lifecycle |
| `guild_configs` | GuildConfig | Per-guild bot configuration |
| `guild_shops` | GuildShop | Per-guild shop item listings |
| `item` | Item (abstract) | Single-table inheritance root |
| `module` | Module | Ship module items |
| `player_inventories` | PlayerInventory | Player → item ownership |
| `player_ships` | PlayerShip | Player → ship associations |
| `players` | Player | Player game state |
| `primary_weapon` | PrimaryWeapon | Primary weapon items |
| `schema` | SchemaVersion | Alembic migration tracking |
| `secondary_weapon` | SecondaryWeapon | Secondary weapon items |
| `ship` | Ship | Ship definitions |
| `system` | System | Star system nodes |
| `turret_weapon` | TurretWeapon | Turret weapon items |
| `users` | User | Discord user accounts |
| `weapon` | Weapon (abstract) | Weapon inheritance intermediate |

**Plus:** `apscheduler_jobs` — managed automatically by APScheduler.

### Model Inheritance Hierarchy

```
Base
├── Item  (single-table inheritance, discriminator: item_type)
│   ├── Module
│   └── Weapon  (abstract intermediate)
│       ├── PrimaryWeapon
│       ├── SecondaryWeapon
│       └── TurretWeapon
├── AdminAuditLog
├── Bounty
├── Criminal
├── DiscordMessage
├── DuelRequest
├── GuildConfig
├── GuildShop
├── Player
├── PlayerInventory
├── PlayerShip
├── SchemaVersion
├── Ship
├── System
└── User
```

---

## Scheduled Jobs

APScheduler runs in-process within bot-core. Jobs and their default schedules:

| Job | Executor | Default Schedule |
|-----|----------|-----------------|
| `bounty_spawn_default` | bounty_spawn_executor | Every N minutes (env-configurable) |
| `shop_refresh_default` | shop_refresh_executor | Every 6 hours |
| `temperature_decay_default` | temperature_decay_executor | Every 1 hour |

Additional executors (triggered on demand or by other jobs):
- `bounty_expire_executor` — expires old bounties
- `bounty_respawn_executor` — respawns criminals after bounty cleared
- `duel_expire_executor` — expires pending duel challenges
- `time_announcement_executor` — posts time-based announcements

---

## API Reference

### bot-core routers (all under `/api/v1/`)

| Router | Path prefix | Purpose |
|--------|-------------|---------|
| about | `/about` | Bot/server info |
| admin | `/admin` | Admin operations (audit-logged) |
| bounties | `/bounties` | Bounty CRUD and lifecycle |
| config | `/config` | Guild configuration |
| data | `/data` | Game data lookups |
| discord_message | `/discord-message` | Discord message persistence |
| duels | `/duels` | Duel challenge lifecycle |
| health | `/health` | Health check |
| inventory | `/inventory` | Player inventory management |
| players | `/players` | Player game state |
| scheduler | `/jobs` | APScheduler job management (router file: `scheduler.py`, tag: `job-scheduler`) |
| ships | `/ships` | Ship definitions |
| shops | `/shops` | Guild shop management |
| systems | `/systems` | Star system graph |
| users | `/users` | Discord user accounts |

### discord-gateway routers (all under `/api/v1/`)

10 internal REST routers mirror the cog commands for inter-service communication.

### blender-service routers (all under `/api/v1/`)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/textures/composite` | PIL texture compositing |
| POST | `/textures/convert-aei` | PNG → AEI format conversion |
| GET | `/textures/formats` | List supported AEI formats |
| POST | `/render/` | Synchronous Blender render |
| POST | `/render/async` | Async render (returns job ID) |
| GET | `/jobs/{job_id}` | Poll async job status |
| GET | `/jobs/{job_id}/result` | Download completed render |
| GET | `/config/render` | Get current render config |
| PUT | `/config/render` | Update render config at runtime |
| POST | `/config/render/reset` | Reset render config to defaults |
| POST | `/cache/clear` | Clear `/tmp` render cache |
| GET | `/cache/stats` | Cache usage statistics |
| GET | `/health/` | Comprehensive health check |
| GET | `/health/simple` | Simple health check |
| GET | `/health/liveness` | Liveness probe (k8s-compatible) |

---

## Discord Cogs (discord-gateway)

| Cog | Purpose |
|-----|---------|
| aboutCog | Bot and server information |
| adminCog | Admin commands (requires elevated permissions) |
| bountyCog | Bounty hunting commands |
| devCog | Developer/debug utilities |
| duelCog | Duel challenge and resolution |
| healthCog | Health check slash command |
| inventoryCog | Player inventory interactions |
| playerCog | Player profile and stats |
| setupCog | Guild setup and configuration |
| shipsCog | Ship browsing and management |
| shopCog | Guild shop interactions |
| skinsCog | Ship skin management (blender-service integration) |
| templateCog | Template/scaffold for new cogs |
| testCog | Test harness cog |

---

## Common Tasks

### Adding a New API Endpoint (bot-core)

1. Create a new router file in `services/bot-core/src/api/routers/`
2. Define schemas in `services/bot-core/src/api/schemas/`
3. Register the router in `services/bot-core/src/main.py`
4. Add tests in `services/bot-core/tests/`

### Adding a New Discord Cog

1. Create a new cog file in `services/discord-gateway/src/cogs/`
2. Register it in the bot setup in `src/bot.py`
3. Add tests in `services/discord-gateway/tests/`

### Adding a New Database Model

1. Create model in `services/bot-core/src/persist/models/`
2. Add to `models/__init__.py`
3. Create repository in `services/bot-core/src/persist/repositories/`
4. Create service in `services/bot-core/src/services/`
5. Generate and apply Alembic migration (see Migration System above)

### Adding a New Scheduled Job

1. Create executor in `services/bot-core/src/utils/executors/`
2. Register job in the scheduler setup within `main.py`
3. Add tests in `services/bot-core/tests/`

### Adding a blender-service Endpoint

1. Create or update router in `services/blender-service/src/routers/`
2. Implement service logic in `services/blender-service/src/services/`
3. Register router in `services/blender-service/src/main.py`
4. Add tests in `services/blender-service/tests/`

---

## Code Standards

- **Python version**: 3.12+
- **Linter/formatter**: Ruff (`target-version = "py312"`, `line-length = 120`) — configured in `/proj/pyproject.toml`
- **Pydantic schemas**: Use `model_config = ConfigDict(from_attributes=True)` (NOT deprecated `class Config`). Use `.model_dump()` (NOT deprecated `.dict()`).
- **Tests**: Max 2 mocks per test. Prefer real objects with deterministic inputs. See `test_combat_service.py` as the reference pattern.
- **Test runner**: `pytest` with `asyncio_mode = auto` (configured in `pyproject.toml`)
- **Test command pattern**: ALWAYS pipe to `tee` so output is captured for later `grep` without re-running. Full suite runs take 5–15 minutes; lost output = wasted time:
  ```bash
  # bot-core
  cd /proj/services/bot-core && timeout 300 python -m pytest tests/ -q --tb=short 2>&1 | tee /tmp/test-botcore.log | tail -20
  # discord-gateway (cogs only — fastest useful subset)
  cd /proj/services/discord-gateway && timeout 300 python -m pytest tests/cogs/ -q --tb=short 2>&1 | tee /tmp/test-gateway-cogs.log | tail -20
  # Grep failures from captured log without re-running:
  grep -A 20 "FAILED\|ERROR" /tmp/test-botcore.log
  ```
- **Error handling**: All repositories use `try/except/rollback`. All cog HTTP clients use a 10-second timeout with retry logic.
- **Logging**: All services use `bblogger.py`. Log at INFO for normal operations, ERROR for failures, DEBUG for diagnostic detail. Always include entity IDs in log messages.
- **Admin mutations**: Must call `audit_service.log()` to produce an `AdminAuditLog` record.

---

## Health Check Endpoints

| Service | Endpoint | Notes |
|---------|----------|-------|
| `bot-core` | `GET /api/v1/health` | |
| `discord-gateway` | `GET /api/v1/health` | |
| `blender-service` | `GET /api/v1/health/` | Comprehensive (Blender binary, CUDA, disk) |
| `blender-service` | `GET /api/v1/health/simple` | Quick status |
| `blender-service` | `GET /api/v1/health/liveness` | Liveness probe |

---

## Docker & Discord Operational Reference

### Docker Access

All commands require `sudo docker`. Containers:

| Container | Port | Exec curl base |
|-----------|------|----------------|
| `bountybot-bot-core` | 8000 | `sudo docker exec bountybot-bot-core curl -s http://localhost:8000/api/v1/...` |
| `bountybot-discord-gateway` | 7999 | `sudo docker exec bountybot-discord-gateway curl -s http://localhost:7999/api/v1/...` |
| `bountybot-blender-service` | 8001 | `sudo docker exec bountybot-blender-service curl -s http://localhost:8001/api/v1/...` |
| `bountybot-db` | 5432 | `sudo docker exec bountybot-db psql -U <user> -d <db>` |

Logs: `sudo docker logs bountybot-<service> --tail N 2>&1`

### Dev Discord Server

| Entity | ID |
|--------|----|
| Dev server (guild) | `1490693399307616276` |
| Owner/main account | `402296276617527306` |
| Alt-user (normal perms) | `970691862035841048` |
| Bot user | `1379827884851593256` (BountyBot-SamX) |

### Key Discord Gateway API Endpoints (for testing/verification)

```
GET  /api/v1/guilds                                    — List all guilds
GET  /api/v1/guilds/{guild_id}/channels                — List guild channels
GET  /api/v1/guilds/{guild_id}/roles                   — List guild roles
GET  /api/v1/guilds/{guild_id}/members                 — List guild members
GET  /api/v1/guilds/{guild_id}/categories              — List guild categories
GET  /api/v1/channels/{channel_id}/messages?limit=N    — List channel messages (newest first)
GET  /api/v1/messages/{message_id}                     — Get specific message
```

### Key Bot-Core API Endpoints (for testing/verification)

```
GET  /api/v1/config/guild/{guild_id}                   — Guild config (channels, roles, settings)
GET  /api/v1/players/{guild_id}/{user_id}              — Player state
GET  /api/v1/bounties/guild/{guild_id}                 — Active bounties
GET  /api/v1/health                                    — Health check
```

---

*Last updated: 2026-04-10*
