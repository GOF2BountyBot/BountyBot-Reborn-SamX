# AGENTS.md - discord-gateway/src/api/routers

This file provides detailed guidance for AI agents working on REST API routers in this directory.

---

## Overview

This directory contains **10 FastAPI router modules** that expose Discord functionality as a REST API. These routers allow other services (like bot-core's scheduled jobs, external integrations) to perform Discord actions programmatically without going through Discord's slash command interface.

The routers bridge the gap: they receive HTTP requests and translate them into Discord API calls via the live `GatewayBot` instance.

---

## Auto-Discovery

Routers are auto-discovered by `bot.py`'s `create_app()` function:

```python
routers_pkg = importlib.import_module("api.routers")
for _, modname, ispkg in pkgutil.iter_modules(routers_pkg.__path__):
    if not ispkg:
        mod = importlib.import_module(f"api.routers.{modname}")
        if hasattr(mod, "router"):
            app.include_router(mod.router, prefix="/api/v1", tags=[modname])
```

**Rules:**
- Any module in this package that defines a `router` attribute is automatically included
- All routers are mounted under `/api/v1`
- The module name becomes the OpenAPI tag
- No manual registration in `bot.py` or `server.py` is needed

---

## Router Inventory

| File | Path Prefix | Endpoints | Purpose |
|------|-------------|-----------|---------|
| `health.py` | `/health` | `GET /health`, `GET /health/simple`, `GET /health/liveness` | Service health checks |
| `guilds.py` | `/guilds` | Guild CRUD, role list, role create/edit/delete | Guild and role management |
| `channels.py` | `/channels` | Channel CRUD, message send/edit/delete | Text/voice channel operations |
| `categories.py` | `/categories` | Category CRUD | Category channel management |
| `messages.py` | `/messages` | Message send, edit, delete, fetch | Guild-agnostic message operations |
| `roles.py` | `/roles` | Role CRUD at guild level | Role management |
| `users.py` | `/users` | User/member lookup, guild membership | User account queries |
| `permissions.py` | `/permissions` | Permission overwrite get/set/delete | Channel permission management |
| `tags.py` | `/tags` | Forum tag CRUD | Forum channel tag management |
| `threads.py` | `/threads` | Thread create/archive/list | Thread management |

---

## Standard Router Pattern

Every router follows this structure:

```python
"""
Module docstring describing what this router does.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from shared import bblogger

from api.schemas.some_schemas import SomeRequest, SomeResponse
from utils.discord_helpers import get_entity_or_404, handle_discord_exception, resolve_bot

flogger = bblogger.get_logger("gateway-some-api-router")

router = APIRouter(
    prefix="/some-resource",
    tags=["some-resource"],
    responses={404: {"description": "Not found"}},
)


@router.get(
    "/{resource_id}",
    response_model=SomeResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a resource",
    description="Full description for OpenAPI docs",
)
async def get_resource(resource_id: int, request: Request) -> SomeResponse:
    """
    Endpoint docstring for developers.
    """
    bot = await resolve_bot(request)
    
    try:
        entity = await get_entity_or_404(
            bot.get_something,      # cache lookup function
            bot.fetch_something,    # API fetch function  
            resource_id,
            "resource_type"         # used in error messages
        )
        return SomeConverter.entity_to_payload(entity)
    except HTTPException:
        raise
    except Exception as exc:
        await handle_discord_exception(f"fetch resource {resource_id}", exc)
```

---

## Accessing the Discord Bot

All routers need the live `GatewayBot` instance to make Discord API calls.

### `resolve_bot(request)`

```python
from utils.discord_helpers import resolve_bot

bot = await resolve_bot(request)
```

This function:
1. Gets `request.app.state.bot`
2. Validates it is a `commands.Bot` instance
3. Waits up to 15 seconds for the bot to be ready (`bot.wait_until_ready()`)
4. Raises `HTTP 503` if the bot is not ready
5. Raises `HTTP 500` if the bot instance is invalid

**Always use `resolve_bot()` as the first call in any route handler that needs Discord.**

### `get_entity_or_404(get_func, fetch_func, entity_id, entity_type)`

```python
from utils.discord_helpers import get_entity_or_404

# Try cache first, then fetch from Discord API
guild = await get_entity_or_404(
    bot.get_guild,    # synchronous cache lookup
    bot.fetch_guild,  # async Discord API call
    guild_id,
    "guild"           # string used in error messages
)
```

This function:
1. Calls `get_func(entity_id)` — synchronous, Discord cache
2. If not found, calls `await fetch_func(entity_id)` — async, Discord API
3. On `discord.NotFound` → raises `HTTP 404`
4. On `discord.Forbidden` → raises `HTTP 403`
5. On other Discord errors → delegates to `handle_discord_exception()`

### `handle_discord_exception(operation, exc)`

```python
from utils.discord_helpers import handle_discord_exception

try:
    await guild.create_role(name="New Role")
except Exception as exc:
    await handle_discord_exception("create role in guild", exc)
```

Maps Discord exceptions to HTTP exceptions:
- `discord.NotFound` → `HTTP 404`
- `discord.Forbidden` → `HTTP 403`
- `discord.HTTPException` with 4xx status → appropriate `4xx`
- `discord.HTTPException` with 5xx status → `HTTP 502 Bad Gateway`
- Other exceptions → `HTTP 500`

---

## Converter Pattern

Routers never return raw Discord objects. They use converters from `utils/discord_converters.py` to transform Discord objects into Pydantic schemas:

```python
from utils.discord_converters import GuildConverter, ChannelConverter, RoleConverter, UserConverter

# Guild
guild_payload = GuildConverter.guild_to_summary(guild)

# Channel
channel_payload = ChannelConverter.channel_to_detail(channel)

# Role
role_payload = RoleConverter.role_to_payload(role)

# User / Member
user_payload = UserConverter.user_to_payload(user)
member_payload = UserConverter.member_to_payload(member)

# Message
message_payload = MessageConverter.message_to_payload(message)
```

---

## Response Schema Conventions

All responses extend `BaseResponse` from `api/schemas/base_schemas.py`:

```python
from api.schemas.base_schemas import BaseResponse

class MyResponse(BaseResponse):
    # BaseResponse includes: status, timestamp
    data: MyData
    ...
```

For list responses, use a `list[Model]` return type directly or wrap in a `BaseResponse` subclass with a `data: list[Model]` field.

---

## Health Router

`health.py` is special — it does **not** call `resolve_bot()`. It reports the gateway service's own health:

```
GET /api/v1/health          → HealthCheckResponse (Python version, platform, checks)
GET /api/v1/health/simple   → SimpleHealthResponse (minimal status)
GET /api/v1/health/liveness → {"status": "alive"}
```

The `HealthFilter` in `server.py` suppresses these endpoints from uvicorn access logs to reduce noise.

---

## Validation Helpers

Use helpers from `discord_helpers.py` to validate Discord-specific constraints:

```python
from utils.discord_helpers import validate_guild_channel_relationship, validate_channel_type

# Ensure channel belongs to the expected guild
validate_guild_channel_relationship(channel, guild_id)

# Ensure channel is of expected type
validate_channel_type(channel, ["text", "voice"], channel_id)
```

Both raise `HTTP 400` if validation fails.

---

## Adding a New Router

1. **Create** `src/api/routers/my_resource.py`

2. **Define** the router with a prefix:
   ```python
   router = APIRouter(
       prefix="/my-resource",
       tags=["my-resource"],
       responses={404: {"description": "Not found"}},
   )
   ```

3. **Add schemas** in `src/api/schemas/my_schemas.py` if needed:
   ```python
   from pydantic import BaseModel, ConfigDict
   
   class MyResourceResponse(BaseModel):
       model_config = ConfigDict(from_attributes=True)
       id: int
       name: str
   ```

4. **Add a converter** in `src/utils/discord_converters.py` if you need to transform Discord objects

5. **Write tests** in `tests/api/test_my_resource.py`

6. **No registration needed** — the auto-discovery handles it

---

## Testing Routers

### Test Setup

```python
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock

class TestMyRouter:
    @pytest.fixture
    def mock_bot(self):
        bot = MagicMock()
        bot.is_ready.return_value = True
        bot.wait_until_ready = AsyncMock()
        return bot

    @pytest.fixture
    def client(self, mock_bot):
        from bot import create_app
        app = create_app()
        app.state.bot = mock_bot
        return TestClient(app)

    def test_get_resource_success(self, client, mock_bot):
        mock_guild = MagicMock()
        mock_guild.id = 123
        mock_guild.name = "Test Guild"
        mock_bot.get_guild.return_value = mock_guild

        response = client.get("/api/v1/guilds/123")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 123

    def test_get_resource_not_found(self, client, mock_bot):
        mock_bot.get_guild.return_value = None
        mock_bot.fetch_guild = AsyncMock(side_effect=discord.NotFound(...))

        response = client.get("/api/v1/guilds/999")
        assert response.status_code == 404
```

### Test Coverage Requirements
- ✅ Success case with valid entity
- ✅ 404 — entity not found
- ✅ 403 — forbidden (no access)
- ✅ 503 — bot not ready
- ✅ Request body validation (for POST/PUT/PATCH)

---

## Error Response Format

All error responses from routers follow FastAPI's standard:

```json
{
  "detail": "Descriptive error message"
}
```

HTTP status codes used:
| Code | When |
|------|------|
| 200 | Success |
| 201 | Created |
| 204 | Deleted (no content) |
| 400 | Bad request / validation error |
| 403 | Forbidden (bot lacks Discord permissions) |
| 404 | Discord entity not found |
| 500 | Unexpected internal error |
| 502 | Discord upstream error |
| 503 | Bot not ready |

---

## API Documentation

Interactive docs are available at:
- **Swagger UI**: `GET /docs`
- **ReDoc**: `GET /redoc`
- **OpenAPI schema**: `GET /openapi.json`

All routers, request bodies, and response models appear automatically because FastAPI generates docs from type annotations.

---

*Last updated: 2026-03-16*
