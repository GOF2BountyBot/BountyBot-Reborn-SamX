# BountyBot-Reborn-SamX

A containerized, GPU-ready micro-service stack powering a Discord bot for a space combat and trading game. The bot supports bounty hunting, ship customization, PvP duels, and a full in-game economy — backed by a PostgreSQL database and optional GPU-accelerated 3D rendering via Blender.

**Scale:** 185 source files · 162 test files · 3,717+ tests across all services.

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
discord-gateway  →  blender-service  →  bot-core  →  db
```

Discord users interact with `discord-gateway` via slash commands. The gateway calls `bot-core` for all game state and `blender-service` for rendering operations. `bot-core` is the sole writer to the PostgreSQL database.

---

## Game Features

The bot implements the following game systems:

- **Bounty hunting** — Bounties spawn on NPC criminals across star systems. Players check systems, track criminals, and claim bounties for credits. Bounty spawn density is governed by a per-guild **activity temperature** that rises when bounties are claimed and decays hourly (×2/3, floored at 1.0) — idle guilds get fewer concurrent bounties; active guilds get more.
- **Ship management** — Players own ships, set an active ship, equip modules and weapons, assign custom nicknames, and browse ship catalogues.
- **PvP duels** — Players challenge each other to credit-stake duels. Combat is resolved server-side with configurable rules.
- **Economy** — Guild-specific shops stock items at tiered prices. Players buy and sell from rotating inventory refreshed on a schedule.
- **Ship skinning** — 3D ship previews are rendered with custom texture compositing via Blender. Textures can be converted to game-native AEI format (ETC1 for Android, DXT5 for PC).
- **Star system navigation** — A\* pathfinding over the connected star system graph, exposed via `/make-route`.
- **Progression** — Division and tier system (Bronze → Silver → Gold → Platinum) with XP and prestige mechanics.
- **Guild configuration** — Per-server settings managed by admins: shop config, bot admin roles, channel assignments.

---

## Prerequisites

- **Docker + Docker Compose** — required for all services
- **NVIDIA drivers + nvidia-docker runtime** — optional, required for GPU-accelerated rendering
- **Python 3.12+** — for local development and running tests outside Docker
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
| **Logging** | `LOG_LEVEL`, `LOG_FILE`, `ENABLE_FILE_LOGGING` | Uses `bblogger` with TRACE/DEBUG/INFO/ERROR levels. |
| **Admin** | `ADMIN_USER_IDS` | Comma-separated Discord user IDs with admin access. Leave empty to allow all (dev only). |
| **Server** | `HOST`, `PORT`, `RELOAD`, `ACCESS_LOG` | `RELOAD=true` enables hot-reload for development. |

---

## Development

### Running Tests

Tests are run with `pytest` from the repository root. `asyncio_mode = auto` is configured in `pyproject.toml`.

```bash
# All services
python -m pytest --tb=short -q

# Individual services
python -m pytest services/bot-core/tests/ --tb=short -q          # ~25s, 2,239 tests
python -m pytest services/discord-gateway/tests/ --tb=short -q   # ~8 min, 1,374 tests
python -m pytest services/blender-service/tests/ --tb=short -q   # ~3s, 104 tests
```

### Linting

The project uses [Ruff](https://docs.astral.sh/ruff/) (`target-version = "py312"`, `line-length = 120`), configured in `pyproject.toml`.

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

Three independent passes (each in its own DB session — one failure does not abort the others):

| Table | Filter | Default retention | Override |
|-------|--------|--------------------|----------|
| `bounty` | `status IN ('completed','expired','cleared')` AND `updated_at < now() - N` | **24 hours** | `BOUNTYBOT_BOUNTY_RETENTION_HOURS` |
| `duel_requests` | `status IN ('completed','expired','cancelled','rejected','declined')` AND `created_at < now() - N` | **24 hours** | `BOUNTYBOT_DUEL_RETENTION_HOURS` |
| `admin_audit_logs` | `timestamp < now() - N` | **30 days** | `BOUNTYBOT_AUDIT_RETENTION_DAYS` |

**Per-player stats are preserved.** The following counters live on the `players` table and are never touched by retention: `bounty_wins`, `systems_checked`, `lifetime_credits`, `duel_wins`, `duel_losses`, `duel_credits_won`, `duel_credits_lost`.

**'escaped' bounties are NOT deleted** — they remain eligible for respawn.

Audit history is preserved long-term out-of-band via the `pg_backup_default` job above. If you need to query historical admin actions older than the retention window, restore the most recent backup that includes the period of interest.

---

## Project Structure

```
BountyBot-Reborn-SamX/
├── docker-compose.yml           # Standard stack
├── docker-compose-gpu.yml       # GPU-enabled stack
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

### bot-core (port 8000) — 15 routers

| Prefix | Purpose |
|--------|---------|
| `/about` | Bot and game data info |
| `/admin` | Admin operations (audit-logged) |
| `/bounties` | Bounty CRUD and lifecycle |
| `/config` | Guild configuration |
| `/data` | Game data lookups |
| `/discord-messages` | Persistent Discord message references |
| `/duels` | Duel challenge lifecycle |
| `/health` | Health check |
| `/inventory` | Player inventory management |
| `/players` | Player game state |
| `/scheduler` | APScheduler job management |
| `/ships` | Ship definitions |
| `/shops` | Guild shop management |
| `/systems` | Star system graph and routing |
| `/users` | Discord user accounts |

### discord-gateway (port 7999) — 10 routers

Internal REST routers for programmatic Discord access: `categories`, `channels`, `guilds`, `health`, `messages`, `permissions`, `roles`, `tags`, `threads`, `users`.

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
| **Ships** | `/ships`, `/ship`, `/setactive`, `/nickname` |
| **Combat** | `/duel-challenge`, `/duel-accept`, `/duel-reject` |
| **Economy** | `/shop`, `/shops`, `/buy`, `/sell` |
| **Inventory** | `/inventory`, `/search`, `/item`, `/equip`, `/unequip` |
| **Player** | `/profile`, `/leaderboard`, `/prestige` |
| **Skins** | `/ship_skin`, `/render_skin`, `/make_skin_texture` |
| **Admin** | `/admin_setup`, `/admin_player`, `/admin_config`, `/admin_refresh_shop`, `/admin_guild_stats`, `/admin_uninstall`, `/render_config`, `/render_cache_clear` |
| **Info** | `/about`, `/list_category`, `/make-route`, `/ping`, `/health` |
| **Dev** | `/load_data`, `/reload_autocomplete` |

Admin and dev commands require the invoking user to be listed in `ADMIN_USER_IDS`, hold the Discord Administrator permission, or hold the configured Bot Admin role.

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

## Contributing and AI Agent Guidance

The primary reference for contributors (human or AI) is [`AGENTS.md`](./AGENTS.md), which documents:

- Full directory structure and file inventory
- Database schema and model inheritance hierarchy
- All API endpoints with request/response details
- Scheduled job configuration
- Code standards (Python 3.12+, Ruff, Pydantic v2, max 2 mocks per test)
- Migration system details

Each service also has its own `AGENTS.md` with service-specific conventions:

- `services/bot-core/AGENTS.md`
- `services/discord-gateway/AGENTS.md`
- `services/blender-service/AGENTS.md`
- `services/discord-gateway/src/cogs/AGENTS.md` (cog patterns and testing conventions)

**Key code standards:**

- Python 3.12+, type hints throughout
- Ruff linting: `line-length = 120`, `target-version = "py312"`
- Pydantic v2: use `ConfigDict(from_attributes=True)` and `.model_dump()` (not deprecated `class Config` or `.dict()`)
- Tests: maximum 2 mocks per test; prefer real objects with deterministic inputs
- All admin mutations must call `audit_service.log()` to produce an `AdminAuditLog` record
- All repositories use `try/except/rollback` error handling
- Logging via `bblogger.py`: INFO for normal operations, ERROR for failures, DEBUG for diagnostics; always include entity IDs in log messages

---

## License

This project is licensed under the **GNU General Public License v3.0**. See the [LICENSE](./LICENSE) file for the full license text.
