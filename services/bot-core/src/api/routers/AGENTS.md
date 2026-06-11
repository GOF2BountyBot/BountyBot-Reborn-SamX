# AGENTS.md - api/routers

FastAPI routers for bot-core. 16 router modules live here, plus the `announcements/` subpackage.

---

## Router Auto-Discovery

`main.py` uses `pkgutil.iter_modules()` to scan this package at startup (`include_routers()`, called from `create_app()`). Any module that exposes a `router` attribute is automatically mounted at `/api/v1`. **There is no manual registration** — creating a file here is sufficient.

```python
# main.py — auto-discovery loop (simplified; the real loop also counts
# included/skipped modules and logs import failures without aborting)
for _finder, name, _ispkg in pkgutil.iter_modules(routers.__path__):
    module = importlib.import_module(f"{routers.__name__}.{name}")
    router = getattr(module, "router", None)
    if router:
        app.include_router(router, prefix="/api/v1")
```

This means:
- The `router` variable name must be exactly `router` (lowercase)
- The scan is **not recursive**: a subpackage is only included if its `__init__.py` exposes a `router`. `announcements/__init__.py` does not, so the `APIRouter(prefix="/time")` defined in `announcements/time_announcement.py` is **not** mounted by auto-discovery (its tests build their own app and mount it manually)
- Tags come from each router's own `APIRouter(tags=[...])` declaration — the module filename is only used for discovery logging

---

## All 16 Routers

| File | Prefix | Tags | Purpose |
|---|---|---|---|
| `about.py` | `/about` | about | Browse game data: category list, objects per category, object lookup by name/alias/ID, ship render-info for blender-service |
| `admin.py` | `/admin` | admin | Admin operations: guild initialize/reset/uninstall/cleanup, player credits/XP/reset, inventory add, give/remove item & ship, shop refresh/config, system health, guild stats; authorized via `ADMIN_USER_IDS` env var; mutations recorded via `AuditService.log_action()` |
| `bounties.py` | `/bounties` | bounties | Bounty lifecycle: check system, combat-bonus, list active, route, spawn, loadout, map render, clear guild bounties, admin-spawn |
| `combat_log.py` | `/combat-log` | combat-log | Read API for the `/combat-log` Discord command: list recent fights for a player (autocomplete feed), full battle detail (404 unless `user_id` is one of the combatants) |
| `config.py` | `/config` | config | Guild configuration: read/update, shop config, reset, admin role, starting credits, XP thresholds, validation, defaults, bounty config, per-guild game-constants overrides + reset |
| `data.py` | `/data` | data | POST `/{category}` triggers an upsert of seed JSON from `import_data/<category>/`; GET `/categories` lists valid categories |
| `discord_message.py` | `/discord-message` | discord-message | Persistent Discord message references: CRUD + lookups by composite key, guild, channel, type, reference |
| `duels.py` | `/duels` | duels | Duel challenge lifecycle: outgoing, pending, challenge, accept, reject, cancel, pending-all, admin-cancel-all, admin-cancel |
| `health.py` | `/health` | health | Health checks: comprehensive (``""``), `/simple`, `/readiness`, `/liveness`, `/database` |
| `inventory.py` | `/inventory` | inventory | Player inventory: list (`include_ships` query param), summary (`include_ships`), add, remove, transfer, search, item count, equip-compatibility validate, consolidate |
| `players.py` | `/players` | players | Player management: create-or-get, read, list by guild, credits, XP, prestige, statistics, promotion-status, combat-preflight, promote, demote, loadout, cooldown reset, transfer credits |
| `scheduler.py` | *(none)* | job-scheduler | APScheduler management: list/get jobs, add one-time/recurring jobs, update, delete (single / all / by guild), `/reset` |
| `ships.py` | `/ships` | ships | **Player ship** management (not ship definitions — those live under `/about`): list player ships, create (grant), active ship get/set, loadout get/update, nickname, equip-check/equip/unequip, delete, transfer |
| `shops.py` | `/shops` | shops | Guild shop: items by tier, summary, purchase, purchase-ship, sell, sell-ship, refresh, stats, items by tech level, refresh-status, single item, prices |
| `systems.py` | `/systems` | systems | Star system graph: GET `/route` (A* pathfinding), GET `/route/map` (PNG render, bounded LRU cache) |
| `users.py` | `/users` | users | Discord user accounts: create, read, update, list, get-or-create |

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

Services and repositories are injected via FastAPI's `Depends()`. Each router defines a local factory (some are `async def`, some plain `def` — both work with `Depends`):

```python
async def get_player_service():
    return PlayerService()
```

This creates a fresh service instance per request. Services instantiate their own repositories in `__init__()`. This pattern avoids global state and is straightforward to mock in tests.

---

## Session Management in Routers

Most routers acquire sessions with the async context manager from `persist.database.manager`:

```python
from persist.database.manager import get_db_session

async with get_db_session() as db:
    result = await service.do_something(db, ...)
```

`get_db_session()` is a `@asynccontextmanager` that acquires and releases a session from the pool. The `db` session is passed down to service and repository calls — it is never stored as module-level state.

`about.py` and `systems.py` instead define a local yielding dependency and inject the session directly:

```python
async def get_db() -> AsyncGenerator[AsyncSession]:
    async with db_manager.get_session() as session:
        yield session

@router.get(...)
async def endpoint(..., db: AsyncSession = Depends(get_db)):
    ...
```

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

### Unprocessable input (422) — e.g. invalid item type
```python
except InvalidItemTypeError as e:
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
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

The `scheduler.py` router does not define its own path prefix — its routes carry the full sub-path (`/jobs`, `/reset`), so they mount at `/api/v1/jobs` and `/api/v1/reset`. It accesses the `AsyncIOScheduler` instance from `request.app.state.scheduler`:

```python
def _get_scheduler(req: Request):
    scheduler = getattr(req.app.state, "scheduler", None)
    if scheduler is None:
        raise HTTPException(status_code=503, detail="Scheduler is not available. ...")
    return scheduler
```

Jobs are added via `scheduler.add_job(run_job, trigger=..., args=[job_id, payload], id=job_id)`. The `_DEFAULT_JOB_IDS` frozenset (`bounty_spawn_default`, `shop_refresh_default`, `temperature_decay_default`) is used to always include those jobs in guild-filtered listings and to reject one-time jobs that try to reuse a reserved ID.

---

## Health Router Notes

The comprehensive health check at `GET /api/v1/health` (route path `""` under the `/health` prefix) reads:
- `request.app.state.db_manager` for DB connectivity info
- `request.app.state.schema_manager` for schema version info

Additional probes: `/health/simple`, `/health/readiness` (503 when DB unreachable), `/health/liveness`, `/health/database`.

`HealthFilter` in `main.py` (installed in the `__main__` uvicorn block) suppresses health-check requests from uvicorn's access log to avoid spam.

---

## How to Add a New Router

1. **Create the file** `api/routers/<name>.py` with `router = APIRouter(prefix="/<name>", tags=["<name>"])`.
2. **Create schemas** in `api/schemas/<name>_schema.py`.
3. **No registration needed** — auto-discovered by `main.py`.
4. **Add tests** in `tests/api/test_<name>_router.py`.

If the router lives in a subpackage (like `announcements/`), the package's `__init__.py` must expose a `router` attribute or it will not be mounted — auto-discovery does not recurse into packages.

---

*Last updated: 2026-06-11*
