# AGENTS.md - discord-gateway Service

This file provides guidance for AI agents working on the discord-gateway service.

---

## Service Overview

**discord-gateway** is the Discord bot gateway service that provides both a Discord bot (via Discord.py) and a REST API for programmatic access to Discord features.

---

## Technology Stack

- **FastAPI** - REST API framework
- **Discord.py** - Discord bot library
- **SQLAlchemy** - ORM (no migration system)
- **PostgreSQL** - Database (via bot-core)
- **bblogger** - Logging utility (from shared library)

---

## Directory Structure

```
services/discord-gateway/
├── Dockerfile
├── requirements.txt
├── test-cleanup.sh
└── src/
    ├── bot.py                 # Discord bot entry point
    ├── api/
    │   ├── server.py         # FastAPI server setup
    │   ├── routers/           # REST API endpoints
    │   │   ├── categories.py
    │   │   ├── channels.py
    │   │   ├── guilds.py
    │   │   ├── health.py
    │   │   ├── messages.py
    │   │   ├── permissions.py
    │   │   ├── roles.py
    │   │   ├── tags.py
    │   │   ├── threads.py
    │   │   └── users.py
    │   └── schemas/           # Request/response models
    ├── cogs/                  # Discord bot cogs
    │   ├── aboutCog.py
    │   ├── adminCog.py
    │   ├── devCog.py
    │   ├── healthCog.py
    │   ├── inventoryCog.py
    │   ├── playerCog.py
    │   ├── shipsCog.py
    │   ├── shopCog.py
    │   ├── skinsCog.py
    │   └── templateCog.py
    └── utils/                 # Utility functions
        ├── discord_converters.py
        ├── discord_helpers.py
        ├── embed_converter.py
        └── permission_utils.py
```

---

## Discord Cogs

The bot uses Discord.py cogs for modular command organization:
- **aboutCog** - Bot information commands
- **adminCog** - Administrative commands
- **devCog** - Developer-only commands
- **healthCog** - Health/status commands
- **inventoryCog** - Player inventory management
- **playerCog** - Player management
- **shipsCog** - Ship information
- **shopCog** - Shop functionality
- **skinsCog** - Ship skin management
- **templateCog** - Template commands

---

## REST API

The service exposes REST endpoints for programmatic Discord access:
- `/api/v1/health` - Health check
- `/api/v1/guilds` - Guild management
- `/api/v1/channels` - Channel operations
- `/api/v1/messages` - Message operations
- `/api/v1/roles` - Role management
- `/api/v1/users` - User operations
- `/api/v1/permissions` - Permission checking
- And more...

---

## Adding New Features

### Adding a New Discord Cog

1. Create a new cog file in `src/cogs/`
2. Inherit from `commands.Cog`
3. Register commands using `@commands.command()` or `@app_commands.Command`
4. Load the cog in `bot.py`

### Adding a New REST API Endpoint

1. Create a new router file in `src/api/routers/`
2. Define request/response schemas in `src/api/schemas/`
3. Register the router in `src/api/server.py`

---

## Health Check

- Endpoint: `GET /api/v1/health`
- Returns service status

---

## Environment Variables

See root `.env.example`. Key variables:
- `DISCORD_BOT_TOKEN` - Discord bot token
- `BOT_CORE_URL` - URL to bot-core service

---

*Last updated: 2026-03-07*
