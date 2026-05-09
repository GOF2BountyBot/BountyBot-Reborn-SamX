# AGENTS.md - api/routers

FastAPI routers for bot-core. All 15 router modules live here.

---

## Router Auto-Discovery

`main.py` uses `pkgutil.iter_modules()` to scan this package at startup. Any module that exposes a `router` attribute of type `APIRouter` is automatically mounted at `/api/v1`. **There is no manual registration** — creating a file here is sufficient.

```python
# main.py — auto-discovery loop
for _finder, name, _ispkg in pkgutil.iter_modules(routers.__path__):
    module = importlib.import_module(f"{routers.__name__}.{name}")
    router = getattr(module, "router", None)
    if router:
        app.include_router(router, prefix="/api/v1")
```

This means:
- The `router` variable name must be exactly `router` (lowercase)
- Subdirectories (`announcements/`) are only included if they themselves have a `router`
- The module filename becomes the tag by default

---

## All 15 Routers

| File | Prefix | Tags | Purpose |
|---|---|---|---|
| `about.py` | `/about` | about | Browse game data: ships, modules, weapons, criminals, systems by name/ID |
| `admin.py` | `/admin` | admin | Admin operations: guild reset, credit adjustment, audit log query; all writes produce `AdminAuditLog` records |
| `bounties.py` | `/bounties` | bounties | Bounty lifecycle: list active, spawn, check system, expire, resolve |
| `config.py` | `/config` | config | Guild configuration CRUD: create/read/update GuildConfig |
| `data.py` | `/data` | data | Bulk game data by category enum (ships, weapons, modules, criminals, systems) |
| `discord_message.py` | `/discord-messages` | discord-messages | Persistent Discord message references: CRUD for channel/message ID tracking |
| `duels.py` | `/duels` | duels | Duel challenge lifecycle: challenge, accept, decline, resolve, list pending |
| `health.py` | `/health` | health | Health check: comprehensive (DB + schema version) and simple endpoint |
| `inventory.py` | `/inventory` | inventory | Player inventory: list, equip, unequip, sell, transfer between players |
| `players.py` | `/players` | players | Player management: create-or-get, read, list by guild, update credits/XP, prestige, transfer credits, statistics |
| `scheduler.py` | `/jobs` | job-scheduler | APScheduler management: list jobs, add one-time/recurring jobs, delete, update |
| `ships.py` | `/ships` | ships | Ship definitions: list all, get by ID, get by name |
| `shops.py` | `/shops` | shops | Guild shop: list items, refresh stock, buy item, sell item |
| `systems.py` | `/systems` | systems | Star system graph: list all, get by name, A* pathfinding between two systems |
| `users.py` | `/users` | users | Discord user accounts: create-or-get, read by discord_id, list player characters |

---

## Standard Router Structure

Every router module follows this template:

```python
from fastapi import APIRouter, Depends, HTTPException, status
from persist.database.manager import get_db_session
from services.my_service import MyService
from shared import bblogger
from api.schemas.my_schema import MyRequest, MyResponse

flogger = bblogger.get_logger("my-router")

router = APIRouter(
    prefix="/my-resource",
    tags=["my-resource"],
    responses={
        404: {"description": "Not found"},
        500: {"description": "Internal server error"},
    }
)

# Dependency injection
async def get_my_service() -> MyService:
    return MyService()

@router.get("/{resource_id}", response_model=MyResponse)
async def get_resource(
    resource_id: int,
    service: MyService = Depends(get_my_service),
):
    try:
        async with get_db_session() as db:
            result = await service.get_something(db, resource_id)
            if not result:
                raise HTTPException(status_code=404, detail="Not found")
            return MyResponse(...)
    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error getting resource {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal error") from e
```

---

## Dependency Injection Pattern

Services are injected via FastAPI's `Depends()`. Each router defines a local async factory:

```python
async def get_player_service() -> PlayerService:
    return PlayerService()
```

This creates a fresh service instance per request. Services instantiate their own repositories in `__init__()`. This pattern avoids global state and is straightforward to mock in tests.

---

## Session Management in Routers

All database access uses the async context manager from `persist.database.manager`:

```python
from persist.database.manager import get_db_session

async with get_db_session() as db:
    result = await service.do_something(db, ...)
```

`get_db_session()` is a `@asynccontextmanager` that acquires and releases a session from the pool. The `db` session is passed down to service and repository calls — it is never stored as module-level state.

---

## Standard Response Patterns

### Success with response_model
```python
@router.get("/{id}", response_model=MyResponse)
async def get_item(id: int, ...) -> MyResponse:
    ...
    return MyResponse(field=value, ...)
```

### 404 Not Found
```python
if not result:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Item {id} not found")
```

### Validation Error (400)
```python
except ValueError as e:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
```

### Conflict (409) — e.g. IntegrityError
```python
except IntegrityError as e:
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Record conflict") from e
```

### Generic 500
```python
except Exception as e:
    flogger.error(f"Error in endpoint: {e}")
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed") from e
```

Always re-raise `HTTPException` without wrapping (`except HTTPException: raise`).

---

## Scheduler Router Notes

The `scheduler.py` router does not define its own path prefix — it mounts at the root level (`/api/v1/jobs`). It accesses the `AsyncIOScheduler` instance from `request.app.state.scheduler`:

```python
def _get_scheduler(req: Request):
    scheduler = getattr(req.app.state, "scheduler", None)
    if scheduler is None:
        raise HTTPException(status_code=503, detail="Scheduler unavailable")
    return scheduler
```

Jobs are added via `scheduler.add_job(run_job, trigger=..., args=[job_id, payload], id=job_id)`.

---

## Health Router Notes

The health check at `/api/v1/health/` (trailing slash required) reads:
- `request.app.state.db_manager` for DB connectivity info
- `request.app.state.schema_manager` for schema version info

`HealthFilter` in `main.py` suppresses health-check requests from uvicorn's access log to avoid spam.

---

## How to Add a New Router

1. **Create the file** `api/routers/<name>.py` with `router = APIRouter(prefix="/<name>", tags=["<name>"])`.
2. **Create schemas** in `api/schemas/<name>_schema.py`.
3. **No registration needed** — auto-discovered by `main.py`.
4. **Add tests** in `tests/api/test_<name>_router.py`.

If the router needs a subpackage (like `announcements/`), ensure the package's `__init__.py` exposes a `router` attribute, or create individual router files within it.

---

*Last updated: 2026-03-16*
