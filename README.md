# BountyBot-Reborn-SamX

A containerized, GPU-ready micro-service stack powering a Discord bot for a space combat and trading game. The bot supports bounty hunting, ship customization, PvP duels, and a full in-game economy — backed by a PostgreSQL database and optional GPU-accelerated 3D rendering via Blender.

**Scale:** 254 source files · 293 test files · 8,500+ tests across all services.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Game Features](#game-features)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Environment Configuration](#environment-configuration)
- [Development](#development)
- [Project Structure](#project-structure)
- [API Reference](#api-reference)
- [Discord Commands](#discord-commands)
- [GPU Rendering](#gpu-rendering)
- [Admin Guide](#admin-guide)
- [Contributing and AI Agent Guidance](#contributing-and-ai-agent-guidance)
- [License](#license)

---

## Architecture Overview

The stack is composed of four services:

| Service | Tech Stack | Port | Purpose |
|---------|------------|------|---------|
| `db` | PostgreSQL 18 | 5432 | Central database |
| `bot-core` | FastAPI + SQLAlchemy + Alembic | 8000 | Core game logic, REST API, scheduled jobs |
| `discord-gateway` | FastAPI + Discord.py | 7999 | Discord bot and internal REST bridge |
| `blender-service` | FastAPI + Blender + PIL + CUDA | 8001 | GPU rendering, texture compositing, AEI conversion |

**Data flow:**

```
discord-gateway  ⇄  bot-core  →  db
discord-gateway  →  blender-service
```

Discord users interact with `discord-gateway` via slash commands. The gateway calls `bot-core` for all game state and `blender-service` for rendering operations. `bot-core` is the sole writer to the PostgreSQL database and pushes announcements and autocomplete-cache updates back to the gateway.

---

## Game Features

The bot implements the following game systems:

- **Bounty hunting** — Bounties spawn on NPC criminals across star systems. Players check systems, track criminals, and claim bounties for credits. The number of simultaneously active bounties is capped per tier via the per-guild `bounty_max_per_tier` config. Winning a bounty fight also yields **loot** pulled from the criminal: every criminal carries one cargo item (advertised pre-fight), and a player who wins the combat with an equipped **tractor beam** pulls it (chance scales 20–80% by beam tier; no beam = no loot), subject to free cargo space. Loot is tunable via 19 per-guild knobs.
- **Ship management** — Players own ships, set an active ship, equip modules and weapons, assign custom nicknames, and browse ship catalogues.
- **PvP duels** — Players challenge each other to credit-stake duels. Combat is resolved server-side by a tick-based combat engine with per-guild tunables; after-action reports are persisted and reviewable via `/combat-log`.
- **Economy** — Guild-specific shops stock items at tiered prices. Players buy and sell from rotating inventory refreshed on a schedule. **Commodities** are a first-class cargo type (acquired as loot): they are never shop-stocked and sell as a face-value sink. A finite per-ship cargo cap is enforced — an over-cap player is locked out of leaving station (no `/check`, no duels) until back under cap.
- **Ship skinning** — 3D ship previews are rendered with custom texture compositing via Blender. Textures can be converted to game-native AEI format (ETC1 for Android, DXT5 for PC).
- **Star system navigation** — A\* pathfinding over the connected star system graph, exposed via `/make-route`.
- **Progression** — Division and tier system (Bronze → Silver → Gold → Platinum) with XP and prestige mechanics.
- **Guild configuration** — Per-server settings managed by admins: shop config, bot admin roles, channel assignments.

---

## Prerequisites

- **Docker + Docker Compose** — required for all services
- **NVIDIA drivers + nvidia-docker runtime** — optional, required for GPU-accelerated rendering
- **Python 3.13+** — for local development and running tests outside Docker
- **PostgreSQL client tools** — optional, for direct database inspection

---

## Quick Start

```bash
# Clone the repository
git clone <repo-url>
cd BountyBot-Reborn-SamX

# Initialize submodules (AEPi library for AEI format conversion)
git submodule update --init --recursive

# Configure environment
cp .env.example .env
# Edit .env with your Discord bot token and other required settings

# Start the stack (without GPU)
docker compose up --build

# Start with GPU support
docker compose -f docker-compose-gpu.yml up --build

# Production: pre-built GHCR images, no source build required
docker compose -f docker-compose.prod.yml up -d        # CPU rendering
docker compose -f docker-compose.prod-gpu.yml up -d    # GPU rendering
```

Once running, Swagger UI is available at:
- bot-core: `http://localhost:8000/docs`
- discord-gateway: `http://localhost:7999/docs`
- blender-service: `http://localhost:8001/docs`

---

## Environment Configuration

Copy `.env.example` to `.env` and configure the following categories:

| Category | Key Variables | Notes |
|----------|--------------|-------|
| **Discord** | `BOTTOKEN`, `BOTAPPID` | Required. Bot token from Discord Developer Portal. |
| **Database** | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB` | Defaults work for Docker Compose. |
| **Connection pool** | `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_RECYCLE` | Tunable for production load. |
| **Service endpoints** | `BOT_API_BASE_URL`, `GATEWAY_HOST`, `GATEWAY_PORT`, `BLENDER_HOST`, `BLENDER_PORT` | Internal routing between containers. |
| **Blender** | `DO_WARMUP`, `GAME_OBJS_FILEID` | `DO_WARMUP=true` pre-compiles CUDA shaders on first boot (~3-5 min, one-time). |
| **Logging** | `LOG_LEVEL`, `LOG_FILE`, `LOG_TO_FILE` | Uses `bblogger` with TRACE/DEBUG/INFO/ERROR levels. |
| **Admin** | `ADMIN_USER_IDS`, `DEVELOPERS` | `ADMIN_USER_IDS`: comma-separated Discord user IDs allowed to call bot-core's `/admin` REST endpoints; leave empty to allow all (dev only). `DEVELOPERS`: comma-separated Discord user IDs with developer override on admin slash commands and exclusive access to super-admin commands (scheduler, data loading, render-config mutation). |
| **Server** | `HOST`, `PORT`, `RELOAD`, `ACCESS_LOG` | `RELOAD=true` enables hot-reload for development. |

---

## Development

### Running Tests

Tests are run with `pytest` from each **service directory** (not the repository root — running from the root makes the top-level `services/` package shadow each service's `src/services`, producing false `ModuleNotFoundError`s). `asyncio_mode = auto` is configured in `pyproject.toml`.

```bash
cd services/bot-core         && python -m pytest tests/ --tb=short -q   # 4,951 tests
cd services/discord-gateway  && python -m pytest tests/ --tb=short -q   # 3,327 tests
cd services/blender-service  && python -m pytest tests/ --tb=short -q   # 271 tests
```

### Linting

The project uses [Ruff](https://docs.astral.sh/ruff/) (`target-version = "py313"`, `line-length = 120`), configured in `pyproject.toml`.

```bash
python -m ruff check              # Check for issues
python -m ruff check --fix        # Auto-fix where possible
python -m ruff format             # Format code
```

### Database Migrations (bot-core)

Migrations are managed via [Alembic](https://alembic.sqlalchemy.org/) and auto-applied on startup by `MigrationManager.ensure_current()`.

```bash
# Inside the bot-core container (or with PYTHONPATH pointed at src/):
python run_migration.py revision --autogenerate -m "describe the change"
python run_migration.py upgrade head
```

Migration scripts live in `services/bot-core/src/persist/database/revisions/versions/`.

### Database Backups

Automated backups run inside the **bot-core** container via APScheduler every 3 hours (at :15 past 00:00, 03:00, 06:00, …). Dumps are written to the bot-core data volume and organised by date:

```
mappings/bot-core/backups/
└── YYYY-MM-DD/
    ├── bountydb_HH-MM-SS.sql.zst
    └── bountydb_HH-MM-SS.sql.zst
```

Backups are compressed with **zstandard** (level 10) and retained for **7 days**. Directories older than 7 days are automatically removed after each successful run. A safety threshold of 250 KiB prevents a corrupt or empty dump from overwriting a good backup.

**Environment variables** (all optional — defaults shown):

| Variable | Default | Purpose |
|----------|---------|---------|
| `BACKUP_DIR` | `/app/data/backups` | Root directory for backup files inside the container |
| `BACKUP_RETAIN_DAYS` | `7` | Number of days of backup directories to retain |

#### Restoring a backup

To restore from a compressed dump, run the following **inside the `bountybot-db` container** (or from any host with `psql` and `zstd` available and network access to the database):

```bash
# 1. Identify the backup file to restore from (on the host):
ls mappings/bot-core/backups/

# 2. Decompress and restore (substitute your actual values):
BACKUP_FILE="mappings/bot-core/backups/2026-01-01/bountydb_03-15-00.sql.zst"
DB_HOST="localhost"   # or "bounty_db" if running inside a container on the same network
DB_PORT="5432"
DB_USER="bounty"
DB_NAME="bountydb"

# Terminate existing connections so DROP DATABASE succeeds:
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d postgres \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DB_NAME' AND pid <> pg_backend_pid();"

# Drop and recreate the target database:
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d postgres -c "DROP DATABASE IF EXISTS \"$DB_NAME\";"
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d postgres -c "CREATE DATABASE \"$DB_NAME\";"

# Decompress and pipe into psql:
zstd -dc "$BACKUP_FILE" | psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME
```

> **Note:** After a restore, restart bot-core so that Alembic re-validates the schema and any in-memory state is refreshed. APScheduler job state is stored in the database and will be restored along with application data.

### Data Retention

A scheduled cleanup job (`db_retention_default`) runs **daily at 03:45 UTC** inside the bot-core container. It bounds the growth of high-churn tables whose terminal-state rows have no game-relevant value once per-player aggregate stats have been written to the `players` table.

Four independent passes (each in its own DB session — one failure does not abort the others):

| Table | Filter | Default retention | Override |
|-------|--------|--------------------|----------|
| `bounty` | `status IN ('completed','expired','cleared')` AND `updated_at < now() - N` | **24 hours** | `BOUNTYBOT_BOUNTY_RETENTION_HOURS` |
| `duel_requests` | `status IN ('completed','expired','cancelled','rejected','declined')` AND `created_at < now() - N` | **24 hours** | `BOUNTYBOT_DUEL_RETENTION_HOURS` |
| `admin_audit_logs` | `timestamp < now() - N` | **30 days** | `BOUNTYBOT_AUDIT_RETENTION_DAYS` |
| `combat_log` | `created_at < now() - N` | **72 hours** | `BOUNTYBOT_COMBAT_LOG_RETENTION_HOURS` |

**Per-player stats are preserved.** The following counters live on the `players` table and are never touched by retention: `bounty_wins`, `systems_checked`, `lifetime_credits`, `duel_wins`, `duel_losses`, `duel_credits_won`, `duel_credits_lost`.

**'escaped' bounties are NOT deleted** — they remain eligible for respawn.

Audit history is preserved long-term out-of-band via the `pg_backup_default` job above. If you need to query historical admin actions older than the retention window, restore the most recent backup that includes the period of interest.

---

## Project Structure

```
BountyBot-Reborn-SamX/
├── docker-compose.yml           # Standard stack (local builds)
├── docker-compose-gpu.yml       # GPU-enabled stack (local builds)
├── docker-compose.prod.yml      # Production stack (pre-built GHCR images)
├── docker-compose.prod-gpu.yml  # Production stack with GPU rendering
├── .env.example                 # Environment variable template
├── pyproject.toml               # Ruff + pytest configuration
├── AGENTS.md                    # AI agent guidance (comprehensive)
├── README.md                    # This file
├── LICENSE                      # GNU General Public License v3
├── mappings/                    # Persistent data volumes (bind-mounted)
│   ├── postgres-data/           # PostgreSQL data directory
│   ├── bot-core/                # bot-core logs
│   ├── discord-gateway/         # Gateway data
│   └── blender-renderer/        # Blender render output
└── services/
    ├── bot-core/                # Core game logic API (FastAPI + SQLAlchemy)
    ├── discord-gateway/         # Discord bot + internal REST bridge
    ├── blender-service/         # GPU rendering pipeline (Blender + PIL)
    ├── database/                # PostgreSQL placeholder (uses stock image)
    └── shared/                  # Shared utilities (bblogger.py)
```

Each service directory contains its own `AGENTS.md` with detailed service-specific guidance.

---

## API Reference

All services expose REST endpoints under `/api/v1/`. Full interactive documentation is available via Swagger UI at `http://localhost:{port}/docs`.

### bot-core (port 8000) — 16 routers

| Prefix | Purpose |
|--------|---------|
| `/about` | Bot and game data info |
| `/admin` | Admin operations (audit-logged) |
| `/bounties` | Bounty CRUD and lifecycle |
| `/combat-log` | Combat after-action report list + detail |
| `/config` | Guild configuration |
| `/data` | Game data lookups |
| `/discord-message` | Persistent Discord message references |
| `/duels` | Duel challenge lifecycle |
| `/health` | Health check |
| `/inventory` | Player inventory management |
| `/players` | Player game state |
| `/jobs`, `/reset` | APScheduler job management (scheduler router, no shared prefix) |
| `/ships` | Ship definitions |
| `/shops` | Guild shop management |
| `/systems` | Star system graph and routing |
| `/users` | Discord user accounts |

### discord-gateway (port 7999) — 12 routers

Internal REST routers for programmatic Discord access: `categories`, `channels`, `guilds`, `health`, `messages`, `permissions`, `roles`, `tags`, `threads`, `users`, `announcements`, `internal_autocomplete`.

### blender-service (port 8001) — 6 routers

| Prefix | Purpose |
|--------|---------|
| `/textures` | PIL compositing, PNG → AEI conversion, format listing |
| `/render` | Synchronous and async Blender renders |
| `/jobs` | Async job polling and result download |
| `/config` | Runtime render configuration |
| `/cache` | Clear `/tmp` cache and view usage statistics |
| `/health` | Comprehensive, simple, and liveness health checks |

---

## Discord Commands

Commands are grouped by category. All commands are Discord slash commands unless noted.

| Category | Commands |
|----------|---------|
| **Game** | `/check`, `/bounties`, `/route`, `/criminal-loadout` |
| **Ships** | `/ships`, `/ship`, `/setactive`, `/nickname`, `/loadout` |
| **Combat** | `/duel-challenge`, `/duel-accept`, `/duel-reject`, `/duel-cancel`, `/combat-log` |
| **Economy** | `/shop`, `/shops`, `/buy`, `/sell`, `/give` |
| **Inventory** | `/inventory`, `/search`, `/item`, `/equip`, `/unequip` |
| **Player** | `/profile`, `/register`, `/leaderboard`, `/promote`, `/demote`, `/prestige`, `/notifications`, `/unregister` |
| **Events** | `/events`, `/event_leaderboard` |
| **Skins** | `/ship_skin`, `/render_skin`, `/make_skin_texture` |
| **Admin** | `/admin_setup`, `/admin_player`, `/admin_config`, `/admin_config_shop`, `/admin_config_bounty`, `/admin_config_xp`, `/admin_config_validate`, `/admin_config_constants` (+ `_view`, `_reset`), `/admin_give_item`, `/admin_remove_item`, `/admin_give_ship`, `/admin_remove_ship`, `/admin_cooldown_reset`, `/admin_spawn_bounty`, `/admin_clear_bounties`, `/admin_duel`, `/admin_combat_log`, `/admin_refresh_shop`, `/admin_guild_stats`, `/admin_check`, `/admin_uninstall`, `/admin_help`, `/render_config`, `/render_cache_clear`, `/admin_event_create`, `/admin_event_view`, `/admin_event_edit`, `/admin_event_add_prize`, `/admin_event_remove_prize`, `/admin_event_start`, `/admin_event_end`, `/admin_event_delete`, `/admin_event_list`, `/admin_sync_roles` |
| **Scheduler** (super-admin) | `/scheduler_list`, `/scheduler_view`, `/scheduler_update`, `/scheduler_delete`, `/admin_reset_scheduler`, `/admin_clear_scheduler` |
| **Info** | `/about`, `/list_category`, `/make-route`, `/ping`, `/health`, `/help` |
| **Dev** (super-admin) | `/load_data`, `/reload_autocomplete`, `/force_reload_caches` |

Admin commands require the invoking user to be listed in the `DEVELOPERS` environment variable, hold the Discord Administrator permission, or hold the configured Bot Admin role. Scheduler and dev commands (and `/render_config` mutations) are super-admin: `DEVELOPERS` only. See [`ADMIN.md`](./ADMIN.md) for the full admin reference.

---

## GPU Rendering

The `blender-service` provides GPU-accelerated 3D rendering:

- **Renderer**: Blender in headless mode with the CYCLES engine
- **Acceleration**: CUDA for NVIDIA GPUs (`docker-compose-gpu.yml` includes GPU passthrough)
- **CPU fallback**: Automatically used when no GPU is detected
- **Asset pipeline**: Game 3D objects are downloaded automatically from Google Drive on first container start via `gdown` + `7z` (controlled by `GAME_OBJS_FILEID`)
- **CUDA warmup**: When `DO_WARMUP=true`, CUDA shaders are pre-compiled on startup (~3-5 minutes, one-time cost). Set to `false` for faster deploys with slower first-render times
- **Texture formats**: Output textures can be converted to game-native AEI format — ETC1 for Android, DXT5 for PC

For local development without re-downloading assets, mount your local game objects directory:

```
old-refs/items/ships/  →  /app/data/game-objects/items/ships/
```

---

## Admin Guide

For server admins: per-guild configuration, shop/bounty tunables, player management commands, and emergency reset procedures are documented in [`ADMIN.md`](./ADMIN.md).

---

## Contributing and AI Agent Guidance

The primary reference for contributors (human or AI) is [`AGENTS.md`](./AGENTS.md), which documents:

- Full directory structure and file inventory
- Database schema and model inheritance hierarchy
- All API endpoints with request/response details
- Scheduled job configuration
- Code standards (Python 3.13+, Ruff, Pydantic v2, max 2 mocks per test)
- Migration system details

Each service also has its own `AGENTS.md` with service-specific conventions:

- `services/bot-core/AGENTS.md`
- `services/discord-gateway/AGENTS.md`
- `services/blender-service/AGENTS.md`
- `services/discord-gateway/src/cogs/AGENTS.md` (cog patterns and testing conventions)

**Key code standards:**

- Python 3.13+, type hints throughout
- Ruff linting: `line-length = 120`, `target-version = "py313"`
- Pydantic v2: use `ConfigDict(from_attributes=True)` and `.model_dump()` (not deprecated `class Config` or `.dict()`)
- Tests: maximum 2 mocks per test; prefer real objects with deterministic inputs
- All admin mutations must call `audit_service.log()` to produce an `AdminAuditLog` record
- All repositories use `try/except/rollback` error handling
- Logging via `bblogger.py`: INFO for normal operations, ERROR for failures, DEBUG for diagnostics; always include entity IDs in log messages

---

## License

This project is licensed under the **GNU General Public License v3.0**. See the [LICENSE](./LICENSE) file for the full license text.
