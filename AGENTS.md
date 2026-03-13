# AGENTS.md - BountyBot-Reborn-SamX

This file provides guidance for AI agents working on this codebase.

---

## Project Overview

**BountyBot-Reborn-SamX** is a containerized, GPU-ready micro-service stack powering a game-related Discord bot. The project uses FastAPI, PostgreSQL, Discord.py, CUDA, Blender, and Docker-Compose.

---

## Architecture

### Services

| Service | Tech Stack | Port | Purpose |
|---------|------------|------|---------|
| `db` | PostgreSQL | 5432 | Central database |
| `bot-core` | FastAPI | 8000 | Core game logic, API |
| `discord-gateway` | FastAPI + Discord.py | 7999 | Discord bot + REST API |
| `blender-service` | FastAPI + Blender | 8001 | GPU rendering/automation |

### Data Flow

```
discord-gateway → blender-service → bot-core → db
```

---

## Directory Structure

```
BountyBot-Reborn-SamX/
├── docker-compose.yml          # Standard stack
├── docker-compose-gpu.yml      # GPU-enabled stack
├── .env.example                # Environment template
├── .gitmodules                # Git submodules
├── AGENTS.md                  # This file
├── README.md                  # Project documentation
├── review_notes.md            # Repository review notes
├── mappings/                  # Persistent data volumes
│   ├── postgres-data/         # PostgreSQL data
│   ├── bot-core/              # bot-core logs
│   ├── discord-gateway/       # Gateway data
│   └── blender-renderer/      # Blender output
└── services/
    ├── bot-core/              # Main FastAPI application
    ├── discord-gateway/       # Discord bot + REST API
    ├── blender-service/       # Blender automation
    ├── database/              # Empty (uses postgres image)
    └── shared/                # Common logging library
```

---

## Key Technologies

- **FastAPI** - Web framework for bot-core and discord-gateway
- **Discord.py** - Discord bot library
- **SQLAlchemy** - Database ORM (no migration system)
- **PostgreSQL** - Primary database
- **Blender** - 3D rendering (GPU-accelerated)
- **Docker-Compose** - Container orchestration

---

## Working with This Project

### Prerequisites

- Docker + Docker-Compose
- (Optional) NVIDIA drivers + `nvidia-docker` for GPU rendering
- Python 3.x for local development
- PostgreSQL client for database work

### Initial Setup

1. Clone the repository
2. Copy `.env.example` to `.env` and configure
3. Initialize submodules: `git submodule update --init --recursive`
4. Build and run: `docker compose up --build`

### Database Migrations

Tables are created automatically on startup if they don't exist. There is currently no migration system - schema changes require manual database updates.

### Game Asset Data

Located in `services/bot-core/import_data/`:
- `ship/` - Ship definitions
- `module/` - Ship modules
- `primary_weapon/` - Primary weapons
- `secondary_weapon/` - Secondary weapons
- `criminal/` - NPC criminal data

### Shared Library

The `services/shared/` directory contains `bblogger.py`, a dependency-free logging utility that gets copied into each built service.

---

## Common Tasks

### Adding a New API Endpoint (bot-core)

1. Create a new router file in `services/bot-core/src/api/routers/`
2. Define schemas in `services/bot-core/src/api/schemas/`
3. Register the router in `services/bot-core/src/main.py`

### Adding a New Discord Cog

1. Create a new cog file in `services/discord-gateway/src/cogs/`
2. Register it in the bot setup

### Adding Database Models

1. Create model in `services/bot-core/src/persist/models/`
2. Create repository in `services/bot-core/src/persist/repositories/`
3. Create service in `services/bot-core/src/services/`
4. Run Alembic migration

### New Services Added (Phase 1)

The following services were added in Phase 1:
- `services/bot-core/src/services/game_constants.py` — Centralized game constants with env var overrides
- `services/bot-core/src/services/game_maths.py` — Tech level probability, reward formulas, level calculation
- `services/bot-core/src/services/system_graph_service.py` — Star system adjacency graph with caching
- `services/bot-core/src/services/pathfinding_service.py` — A* shortest path algorithm
- `services/bot-core/src/persist/repositories/item_repository.py` — Unified item lookup across all model types

---

## Health Check Endpoints

- `bot-core`: `GET /api/v1/health`
- `discord-gateway`: `GET /api/v1/health`
- `blender-service`: `GET /api/v1/health/`

---

*Last updated: 2026-03-13*
