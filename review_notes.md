# BountyBot-Reborn-SamX Repository Review Notes

**Date:** 2026-03-07  
**Reviewer:** AI Assistant  
**Purpose:** Repository exploration, README update, AGENTS.md creation, and submodule verification

---

## 1. Conceptual Architecture Overview

This is a **multi-service Docker-based monorepo** that powers BountyBot - a game-related Discord bot with associated backend services.

### 1.1 Core Services

| Service | Technology | Purpose | Port |
|---------|------------|---------|------|
| **db** | PostgreSQL | Central database | 5432 |
| **bot-core** | FastAPI | Core game logic, players, ships, inventory, shops | 8000 |
| **discord-gateway** | Discord.py + FastAPI | Discord bot gateway + REST API | 7999 |
| **blender-service** | Python + Blender | GPU-enabled rendering/automation | 8001 |
| **shared** | Python | Common logging library (bblogger.py) | N/A |

### 1.2 Service Dependencies (from docker-compose.yml)

```
discord-gateway → blender-service → bot-core → db
```

---

## 2. Directory Structure

### 2.1 Root Level
- `docker-compose.yml` - Standard stack (all services)
- `docker-compose-gpu.yml` - GPU-enabled stack (blender with NVIDIA)
- `.env.example` - Environment template
- `.gitmodules` - Git submodule configuration

### 2.2 `/services/` Directory

| Path | Description |
|------|-------------|
| `services/bot-core/` | Main FastAPI application with: <br>- API routers (players, ships, shops, inventory, admin, config, etc.) <br>- Database models (SQLAlchemy) <br>- Repositories (data access layer) <br>- Services (business logic) <br>- Database migrations (Alembic) |
| `services/discord-gateway/` | Discord bot with: <br>- Cogs (admin, about, dev, health, inventory, player, ships, shop, skins) <br>- REST API (channels, guilds, messages, permissions, roles, threads, users) <br>- Schemas for request/response |
| `services/blender-service/` | Blender automation API: <br>- FastAPI-based HTTP service <br>- Health checks <br>- Submodule: `src/lib/AEPi` (external Python lib) |
| `services/database/` | Empty placeholder (DB uses `postgres:latest` image directly) |
| `services/shared/` | Common utilities: <br>- `bblogger.py` - Logging utility copied into each service |

### 2.3 `/mappings/` Directory (Persistent Data)

| Path | Purpose |
|------|---------|
| `mappings/postgres-data/` | PostgreSQL database files |
| `mappings/bot-core/` | Application logs |
| `mappings/discord-gateway/` | Discord gateway data |
| `mappings/blender-renderer/` | Blender rendering data |

---

## 3. Git Submodules

### 3.1 Current Submodules

| Path | URL | Status |
|------|-----|--------|
| `services/blender-service/src/lib/AEPi` | https://github.com/Trimatix/AEPi.git | ✅ Properly initialized and checked out (version 0.8.3.2) |

### 3.2 Verification

```bash
$ git submodule status
231670c6dc204d3bd2c99c4e816fcd81315bf00a services/blender-service/src/lib/AEPi (0.8.3.2-5-g231670c)
```

The submodule is properly initialized and tracked.

---

## 4. Existing README.md Assessment

The existing README.md provides:
- ✅ Project overview
- ✅ Service descriptions
- ✅ Local development instructions
- ✅ Production deployment notes
- ⚠️ Minor inaccuracies to address:
  - Port mismatch: discord-gateway shows port 8080 but docker-compose uses 7999
  - Missing information about the shared library usage

---

## 5. Key Observations

### 5.1 Game Data
The `services/bot-core/import_data/` directory contains JSON files for:
- Ships (deep science, grey, kaamo club, etc.)
- Modules (armour, boosters, cabins, cloaks, compressors, shields, thrusters, etc.)
- Weapons (primary, secondary - auto cannons, beam lasers, missiles, nukes, etc.)
- Criminals (NPC data)

### 5.2 Database Schema
Uses SQLAlchemy ORM with Alembic for migrations. Key models:
- Player, User, Guild
- Ship, Module, Weapon (primary/secondary/turret)
- Inventory, Shop
- Discord message configurations

### 5.3 Discord Integration
Rich Discord bot with:
- Multiple cogs for different features
- REST API layer for programmatic access
- Permission management
- Message templating system

---

## 6. Action Items Completed

- [x] Repository structure exploration
- [x] README assessment
- [x] Submodule verification
- [x] Conceptual architecture mapping
- [ ] Update README.md
- [ ] Create root AGENTS.md
- [ ] Create service-specific AGENTS.md files
- [ ] Create review_notes.md (this file)

---

## 7. Notes for Future Development

1. **Docker Volume Mounts**: Each service has persistent data mounted from `./mappings/`
2. **GPU Support**: Separate `docker-compose-gpu.yml` for NVIDIA rendering
3. **Health Checks**: Implemented for db and blender-service
4. **Shared Library**: The `services/shared/` folder contains `bblogger.py` which gets copied into each built service

---

*This file was created during repository review on 2026-03-07*
