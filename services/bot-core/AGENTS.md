# AGENTS.md - bot-core Service

This file provides guidance for AI agents working on the bot-core service.

---

## Service Overview

**bot-core** is the main FastAPI application that provides core game logic, player management, ship systems, inventory, and shop functionality for the BountyBot Discord game.

---

## Technology Stack

- **FastAPI** - Web framework
- **SQLAlchemy** - ORM (no migration system - tables created on startup)
- **PostgreSQL** - Database
- **bblogger** - Logging utility (from shared library)

---

## Directory Structure

```
services/bot-core/
├── Dockerfile
├── requirements.txt
├── import_data/              # Game asset JSON files
│   ├── ship/                 # Ship definitions
│   ├── module/               # Ship modules
│   ├── primary_weapon/       # Primary weapons
│   ├── secondary_weapon/     # Secondary weapons
│   ├── criminal/             # NPC criminals
│   ├── Test-GameAssets.ps1  # Test script
│   └── Update-GameAssets.ps1 # Import script
└── src/
    ├── main.py               # FastAPI app entry point
    ├── api/
    │   ├── routers/          # API endpoints
    │   │   ├── about.py
    │   │   ├── admin.py
    │   │   ├── config.py
    │   │   ├── data.py
    │   │   ├── discord_message.py
    │   │   ├── health.py
    │   │   ├── inventory.py
    │   │   ├── players.py
    │   │   ├── scheduler.py
    │   │   ├── ships.py
    │   │   ├── shops.py
    │   │   └── users.py
    │   └── schemas/          # Request/response models
    ├── persist/
    │   ├── database/           # Database connection & setup
    │   │   ├── manager.py
    │   │   └── circuit_breaker.py
    │   ├── models/           # SQLAlchemy models
    │   ├── repositories/     # Data access layer
    │   └── schemas/          # Schema management
    ├── services/             # Business logic
    ├── message_builders/     # Message formatting
    └── utils/                # Utilities
```

---

## Database Models

Key models in `src/persist/models/`:
- `player.py` - Player data
- `user.py` - Discord user data
- `ship.py` - Ship definitions
- `module.py` - Ship modules
- `primary_weapon.py` / `secondary_weapon.py` / `turret_weapon.py` - Weapons
- `inventory.py` - Player inventory
- `guild_config.py` - Server-specific configuration
- `guild_shop.py` - Server-specific shop items

---

## API Endpoints

All endpoints are prefixed with `/api/v1/`. Key routers:
- `/health` - Health check
- `/players` - Player management
- `/ships` - Ship queries
- `/shops` - Shop management
- `/inventory` - Player inventory
- `/admin` - Admin operations
- `/config` - Configuration

---

## Adding New Features

### Adding a New API Endpoint

1. Create a new router file in `src/api/routers/`
2. Define request/response schemas in `src/api/schemas/`
3. Register the router in `src/main.py`

### Adding a New Database Model

1. Create model in `src/persist/models/`
2. Create repository in `src/persist/repositories/`
3. Create service in `src/services/`
4. Tables are created automatically on startup if they don't exist

### Importing Game Assets

Use the PowerShell scripts in `import_data/`:
- `Test-GameAssets.ps1` - Validates JSON files
- `Update-GameAssets.ps1` - Imports data into database

---

## Health Check

- Endpoint: `GET /api/v1/health`
- Returns service status and database connection info

---

## Environment Variables

See root `.env.example`. Key variables:
- `POSTGRES_HOST`, `POSTGRES_PORT`
- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`

---

*Last updated: 2026-03-07*
