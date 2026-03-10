# BountyBot-Reborn-SamX

> A containerised, **GPU-ready** micro-service stack that powers the next iteration of **BountyBot**.
> Technologies: FastAPI, PostgreSQL, CUDA, Blender, Docker-Compose, and **Discord** gateway integrations.


## Table of Contents
1. [Project Layout](#1-project-layout)
2. [Service Overview](#2-service-overview)
3. [Database Schema](#3-database-schema)
4. [Game Assets & Import Data](#4-game-assets--import-data)
5. [Local Development](#5-local-development)
6. [GPU Deployment](#6-gpu-deployment)
7. [Production Deployment](#7-production-deployment)
8. [Environment Variables](#8-environment-variables)
9. [Health-checks & Observability](#9-health-checks--observability)
10. [Shared Library](#10-shared-library)
11. [Test Suite](#11-test-suite)
12. [Development Notes](#12-development-notes)

---

## 1. Project Layout

~~~text
BountyBot-Reborn-SamX
├── docker-compose.yml              # Standard stack (CPU)
├── docker-compose-gpu.yml          # GPU-enabled stack (CUDA)
├── .env.example                    # Environment template
├── .gitmodules                     # Git submodule configuration
├── README.md
├── services/
│   ├── bot-core/                   # FastAPI application + PostgreSQL ORM
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── src/                   # Main application code
│   │   └── import_data/           # JSON game assets (ships, weapons, etc.)
│   ├── discord-gateway/           # Discord bot + REST API
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── src/
│   │   │   ├── bot.py             # Discord bot entry point
│   │   │   ├── api/               # FastAPI REST endpoints
│   │   │   └── cogs/              # Discord command modules
│   ├── blender-service/           # Blender automation (GPU rendering)
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── src/
│   ├── database/                   # (Reserved for future DB migrations)
│   └── shared/                     # Shared utilities
│       ├── bblogger.py             # Logging utility (used by all services)
│       └── __init__.py
├── mappings/                       # Host-mounted persistent volumes
│   ├── postgres-data/              # PostgreSQL data directory
│   ├── bot-core/                  # bot-core persistent data
│   ├── discord-gateway/           # Discord gateway persistent data
│   └── blender-renderer/          # Blender render output
└── ...
~~~

---

## 2. Service Overview

### `db` (PostgreSQL)
* **Image**: `postgres:latest`
* **Container Name**: `bounty_db`
* **Networks**: `botnetv2`
* **Ports**: `5432:5432`
* **Volumes**
  * `/etc/localtime:/etc/localtime:ro`
  * `/etc/timezone:/etc/timezone:ro`
  * `./mappings/postgres-data:/var/lib/postgresql/data`
* **Environment**
  * `PUID` (default: `1000`)
  * `PGID` (default: `1000`)
  * `POSTGRES_USER=bounty`
  * `POSTGRES_PASSWORD=bounty`
  * `POSTGRES_DB=bountydb`
* **Health-check**: waits for PostgreSQL readiness (`pg_isready`)

---

### `bot-core`
* **Build Context**: `./`
* **Dockerfile**: [`./services/bot-core/Dockerfile`](services/bot-core/Dockerfile)
* **Container Name**: `bot-core`
* **Networks**: `botnetv2`
* **Ports**: `8000:8000`
* **Depends On**: `db` (healthy)
* **Volumes**
  * `/etc/localtime:/etc/localtime:ro`
  * `/etc/timezone:/etc/timezone:ro`
  * `./mappings/bot-core:/app/data`
* **Environment**
  * `PUID` / `PGID` (default: `1000`)
* **Description**: FastAPI REST API for game data management, database operations, and scheduler. Provides endpoints at `/api/v1/*`

---

### `discord-gateway`
* **Build Context**: `./`
* **Dockerfile**: [`./services/discord-gateway/Dockerfile`](services/discord-gateway/Dockerfile)
* **Container Name**: `discord-gateway`
* **Networks**: `botnetv2`
* **Ports**: `7999:7999` ⚠️ **Note: README previously incorrectly stated 8080**
* **Depends On**: `bot-core` (healthy), `blender-service` (healthy), `db` (healthy)
* **Volumes**
  * `/etc/localtime:/etc/localtime:ro`
  * `/etc/timezone:/etc/timezone:ro`
  * `./mappings/discord-gateway:/app/data`
* **Environment**
  * `PUID` / `PGID` (default: `1000`)
* **Description**: Discord bot with Slash Commands (cogs) and REST API endpoints. Connects to Discord Gateway and exposes HTTP endpoints for bot interactions.

---

### `blender-service`
* **Build Context**: `./`
* **Dockerfile**: [`./services/blender-service/Dockerfile`](services/blender-service/Dockerfile)
* **Container Name**: `blender-service`
* **Networks**: `botnetv2`
* **Ports**: `8001:8001`
* **Depends On**: `bot-core` (healthy)
* **Environment**
  * `PUID` / `PGID` (default: `1000`)
  * `NVIDIA_VISIBLE_DEVICES=all` (optional – enable GPU)
* **Volumes**
  * `/etc/localtime:/etc/localtime:ro`
  * `/etc/timezone:/etc/timezone:ro`
  * `./mappings/blender-renderer:/app/data`
* **Health-check**: `curl -s -o /dev/null -f http://localhost:8001/api/v1/health/`
* **Description**: Blender automation service for GPU-based 3D rendering. Uses AEPi submodule for Python-Blender integration.

---

## 3. Database Schema

The bot-core service uses **SQLAlchemy** with **asyncpg** for async database operations.

### Schema Version
- **Current Version**: `1.0.0`
- **Tracking**: A `schema_version` table tracks the current schema version
- **Migration**: Currently uses SQLAlchemy's `Base.metadata.create_all()` for automatic table creation

### Database Models

Located in [`services/bot-core/src/persist/models/`](services/bot-core/src/persist/models/):

| Model | File | Description |
|-------|------|-------------|
| `User` | [`user.py`](services/bot-core/src/persist/models/user.py) | Discord user records |
| `Player` | [`player.py`](services/bot-core/src/persist/models/player.py) | Game player data |
| `PlayerInventory` | [`player_inventory.py`](services/bot-core/src/persist/models/player_inventory.py) | Player inventory items |
| `PlayerShip` | [`player_ship.py`](services/bot-core/src/persist/models/player_ship.py) | Player-owned ships |
| `Ship` | [`ship.py`](services/bot-core/src/persist/models/ship.md) | Ship catalog data |
| `Item` | [`item.py`](services/bot-core/src/persist/models/item.py) | Generic item data |
| `Module` | [`module.py`](services/bot-core/src/persist/models/module.py) | Ship modules |
| `Weapon` | [`weapon.py`](services/bot-core/src/persist/models/weapon.py) | Weapon base |
| `PrimaryWeapon` | [`primary_weapon.py`](services/bot-core/src/persist/models/primary_weapon.py) | Primary weapons |
| `SecondaryWeapon` | [`secondary_weapon.py`](services/bot-core/src/persist/models/secondary_weapon.py) | Secondary weapons |
| `TurretWeapon` | [`turret_weapon.py`](services/bot-core/src/persist/models/turret_weapon.py) | Turret weapons |
| `Criminal` | [`criminal.py`](services/bot-core/src/persist/models/criminal.py) | Bounty targets |
| `GuildConfig` | [`guild_config.py`](services/bot-core/src/persist/models/guild_config.py) | Discord guild settings |
| `GuildShop` | [`guild_shop.py`](services/bot-core/src/persist/models/guild_shop.py) | Per-guild shop data |
| `DiscordMessage` | [`discord_message.py`](services/bot-core/src/persist/models/discord_message.py) | Cached messages |
| `SchemaVersion` | [`schema_version.py`](services/bot-core/src/persist/models/schema_version.py) | Schema tracking |

### Database Connection
- **Connection String**: `postgresql+asyncpg://bounty:bounty@db:5432/bountydb`
- **Pool Settings** (configurable via `.env`):
  - `DB_POOL_SIZE` (default: 20)
  - `DB_MAX_OVERFLOW` (default: 30)
  - `DB_POOL_RECYCLE` (default: 3600 seconds)

---

## 4. Game Assets & Import Data

The project includes game asset data from **Galaxy on Fire 2** (GoF2).

### Import Data Structure

Located in [`services/bot-core/import_data/`](services/bot-core/import_data/):

| Category | Directory | Description |
|----------|-----------|-------------|
| Ships | `ship/` | Ship definitions (90+ ships) |
| Primary Weapons | `primary_weapon/` | Primary weapon definitions |
| Secondary Weapons | `secondary_weapon/` | Secondary weapon definitions |
| Turret Weapons | `turret_weapon/` | Turret weapon definitions |
| Modules | `module/` | Ship modules (shields, engines, etc.) |
| Criminals | `criminal/` | Bounty target NPCs |
| Systems | `system/` | Galaxy map systems |
| Commodities | `commodity/` | Trade goods |

### Data Loading

The [`data_loader.py`](services/bot-core/src/utils/data_loader.py) module handles loading JSON files into the database:

- **Function**: `load_data(category: str)` - Loads all JSON files from `import_data/{category}/`
- **Upsert**: Uses `create_or_update()` pattern to handle existing records
- **Emoji Resolution**: Automatically resolves Discord emojis for item names

### Asset Update Scripts

PowerShell scripts for managing game assets:

1. **[`Test-GameAssets.ps1`](services/bot-core/import_data/Test-GameAssets.ps1)**
   - Validates JSON files against image URLs
   - Reports match rate for assets
   - Usage: `.\Test-GameAssets.ps1 -ImportDataPath ".\services\bot-core\import_data" -ImageUrlsFile ".\image_urls.txt"`

2. **[`Update-GameAssets.ps1`](services/bot-core/import_data/Update-GameAssets.ps1)**
   - Updates JSON files with correct icon URLs and ship skin data
   - Supports WhatIf mode for testing
   - Usage: `.\Update-GameAssets.ps1 -ImportDataPath ".\services\bot-core\import_data" -ImageUrlsFile ".\image_urls.txt"`

### Asset Source
- **Google Drive**: [Game Assets Folder](https://drive.google.com/drive/folders/1RYnlCVXbBc7FGPKvYaGvCHmobL_ZvCP8)
- **File ID** (configurable via `GAME_OBJS_FILEID`): `1Z7S3ZtE7siZuSKuEob8cMmMicHXzVZLx`

---

## 5. Local Development

### Prerequisites
* Docker + Docker-Compose
* (Optional) NVIDIA drivers + `nvidia-docker` for GPU rendering

### Quick-start

~~~bash
# 1. Clone repository
git clone https://github.com/your-repo/BountyBot-Reborn-SamX.git
cd BountyBot-Reborn-SamX

# 2. Initialize submodules (AEPi for Blender)
git submodule update --init --recursive

# 3. Copy environment example
cp .env.example .env   # then edit values as required

# 4. Build & run
docker compose up --build
~~~

The stack should now be reachable on:
* `http://localhost:8000` – FastAPI docs (bot-core)
* `http://localhost:7999` – Discord gateway REST API (previously incorrectly documented as 8080)
* `http://localhost:8001` – Blender service (if running)

---

## 6. GPU Deployment

For GPU-accelerated rendering with Blender:

~~~bash
# Use the GPU compose file
docker compose -f docker-compose-gpu.yml up -d
~~~

**Requirements**:
* NVIDIA GPU
* NVIDIA Driver installed
* `nvidia-docker` runtime configured

**Configuration**:
- Set `NVIDIA_VISIBLE_DEVICES=all` in environment
- The Blender service will perform a warmup render on first use (configurable via `DO_WARMUP=true/false`)

---

## 7. Production Deployment

1. Set all secrets in `.env` (or your secrets manager)
2. Map persistent volumes to durable storage
3. Run with: `docker compose -f docker-compose.yml up -d`

**For GPU production**:
~~~bash
docker compose -f docker-compose-gpu.yml up -d
~~~

---

## 8. Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BOTAPPID` | (required) | Discord Application ID |
| `BOTTOKEN` | (required) | Discord Bot Token |
| `POSTGRES_USER` | `bounty` | Database username |
| `POSTGRES_PASSWORD` | `bounty` | Database password |
| `POSTGRES_HOST` | `db` | Database host |
| `POSTGRES_PORT` | `5432` | Database port |
| `POSTGRES_DB` | `bountydb` | Database name |
| `DB_POOL_SIZE` | `20` | Connection pool size |
| `DB_MAX_OVERFLOW` | `30` | Max pool overflow |
| `DB_POOL_RECYCLE` | `3600` | Pool recycle seconds |
| `DB_ECHO` | `false` | SQL query logging |
| `HOST` | `0.0.0.0` | bot-core listen address |
| `PORT` | `8000` | bot-core listen port |
| `GATEWAY_HOST` | `0.0.0.0` | Discord gateway listen address |
| `GATEWAY_PORT` | `7999` | Discord gateway listen port |
| `BLENDER_HOST` | `0.0.0.0` | Blender service listen address |
| `BLENDER_PORT` | `8001` | Blender service listen port |
| `RELOAD` | `true` | Enable auto-reload (dev only) |
| `LOG_LEVEL` | `TRACE` | Logging level |
| `LOG_FILE` | `/app/data/logs/app.log` | Log file path |
| `ENABLE_FILE_LOGGING` | `true` | Enable file logging |
| `BOT_API_BASE_URL` | `http://bot-core:8000/api/v1` | API base URL for gateway |
| `HEALTH_CHECK_TIMEOUT` | `5` | Health check timeout (seconds) |
| `DO_WARMUP` | `true` | GPU warmup render |
| `GAME_OBJS_FILEID` | (see .env.example) | Google Drive file ID for assets |

---

## 9. Health-checks & Observability

| Service | Endpoint | Check |
|---------|----------|-------|
| `db` | Internal | `pg_isready -d bountydb -U bounty` |
| `bot-core` | `/api/v1/health/` | Database connectivity + schema version |
| `discord-gateway` | `/api/v1/health/` | Bot connection + API health |
| `blender-service` | `/api/v1/health/` | Blender availability |

---

## 10. Shared Library

The [`services/shared/`](services/shared/) directory contains shared utilities:

### bblogger.py
A dependency-free logging helper used by all services:

```python
import shared.bblogger as bblogger
flogger = bblogger.get_logger("module-name")
```

**Features**:
- TRACE level (finest-grained, below DEBUG)
- Color-formatted console output
- Optional rotating file handler
- Environment variable configuration (`LOG_LEVEL`, `LOG_FILE`, `LOG_TO_FILE`)

---

## 11. Test Suite

**Current Status**: The discord-gateway service has comprehensive test coverage:

### Running Tests

#### Unit Tests (Pytest)

Run all unit tests:
```bash
pytest
```

Run tests with coverage:
```bash
pytest --cov=src --cov-report=html
```

Generate coverage report:
```bash
coverage run -m pytest
coverage report
```

View HTML coverage report:
```bash
open htmlcov/index.html
```

Run specific test files:
```bash
pytest tests/cogs/test_healthCog.py
pytest tests/api/test_users.py
```

#### API Integration Tests

The discord-gateway service includes a comprehensive API test harness in `src/api-test.py`. This is a self-contained test runner that:

- Tests all API endpoints
- Creates disposable resources with "test-" prefix
- Automatically cleans up all created resources
- Provides detailed audit logging
- Handles rate limits with configurable delays

Run the API test harness:
```bash
python src/api-test.py
```

#### Test Options

The API test harness supports these command-line options:

```bash
python src/api-test.py --help
```

Common options:
- `--base-url`: API base URL (default: http://localhost:7999)
- `--guild-id`: Test guild ID (default: 711548456019296289)
- `--user-id`: Test user ID (default: 640882072516427787)
- `--delay`: Delay between tests (default: 2 seconds)
- `--validation-delay`: Delay for validation (default: 5 seconds)
- `--log-file`: Log file path (default: /app/data/logs/app.log)
- `--cleanup-file`: Cleanup log file path (default: /app/data/logs/created_objects.log)

---

## 12. Development Notes

### API Documentation
- **bot-core**: `http://localhost:8000/docs` (Swagger UI)
- **bot-core**: `http://localhost:8000/redoc` (ReDoc)
- **discord-gateway**: `http://localhost:7999/docs` (if enabled)

### Discord Bot Commands
The Discord gateway uses "Cogs" for command organization (located in [`services/discord-gateway/src/cogs/`](services/discord-gateway/src/cogs/)):
- `aboutCog.py` - Bot information commands
- `adminCog.py` - Administrative commands
- `devCog.py` - Developer-only commands
- `healthCog.py` - Health/status commands
- `inventoryCog.py` - Inventory management
- `playerCog.py` - Player data commands
- `shipsCog.py` - Ship management
- `shopCog.py` - Shop/economy commands
- `skinsCog.py` - Ship skin commands

### Database Initialization
On first run, the bot-core service automatically:
1. Creates all tables (if not exist)
2. Sets the schema version to `1.0.0`

### Submodules
- **AEPi**: Python library for Blender automation
  - Path: `services/blender-service/src/lib/AEPi`
  - URL: https://github.com/Trimatix/AEPi.git
  - Initialize with: `git submodule update --init --recursive`

---

## License

See [`LICENSE`](LICENSE) file for details.
