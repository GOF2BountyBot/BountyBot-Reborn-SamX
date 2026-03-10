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

## Testing

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

The project includes a comprehensive API test harness in `src/api-test.py`. This is a self-contained test runner that:

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

*Last updated: 2026-03-10*
