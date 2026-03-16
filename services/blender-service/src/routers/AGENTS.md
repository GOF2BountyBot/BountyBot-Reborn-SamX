# AGENTS.md - blender-service/src/routers/

This file provides guidance for AI agents working on the routers layer of blender-service.

---

## Overview

The `routers/` package contains all FastAPI route handlers for blender-service. There are 6 router modules plus an `__init__.py`. All routers are **auto-discovered** by `main.py` — no manual registration is needed.

---

## Auto-Discovery Pattern

`main.py::include_routers()` uses `pkgutil.iter_modules(routers.__path__)` to iterate every module in this package. For each module that has a `router` attribute (an `APIRouter` instance), it calls:

```python
app.include_router(router, prefix="/api/v1", tags=[modname])
```

**Consequence**: Drop a new `.py` file here with a `router = APIRouter(...)` and it is automatically live at the next startup. No edits to `main.py` required.

**Requirement**: Every router module **must** expose a module-level `router` attribute of type `APIRouter`. If the attribute is missing, `main.py` logs a warning and skips the module.

---

## URL Structure

```
/api/v1/{router_prefix}/{endpoint_path}
```

The `/api/v1` prefix is applied globally by `include_routers()`. Each router then defines its own `prefix` on the `APIRouter` constructor (e.g., `prefix="/render"`).

| Router file | `APIRouter` prefix | Effective base URL |
|-------------|--------------------|--------------------|
| `cache.py` | `/cache` | `/api/v1/cache` |
| `config.py` | `/config` | `/api/v1/config` |
| `health.py` | `/health` | `/api/v1/health` |
| `jobs.py` | `/jobs` | `/api/v1/jobs` |
| `render.py` | `/render` | `/api/v1/render` |
| `textures.py` | `/textures` | `/api/v1/textures` |

---

## Accessing Shared Services

Services initialised in `lifespan()` are stored on `app.state`. Routers access them via the `Request` object:

```python
from fastapi import APIRouter, Request

router = APIRouter(prefix="/my-prefix")

@router.get("/endpoint")
async def my_endpoint(request: Request):
    render_config = request.app.state.render_config   # RenderConfigService
    job_queue = request.app.state.job_queue           # JobQueueService
    ...
```

| `app.state` attribute | Type | Set in |
|-----------------------|------|--------|
| `render_config` | `RenderConfigService` | `lifespan()` in `main.py` |
| `job_queue` | `JobQueueService` | `lifespan()` in `main.py` |

If you add a new shared service, initialise it in the `lifespan()` function in `main.py` and store it on `app.state` there.

---

## Temp File Management Conventions

Render operations write temporary files to `/tmp/blender_render_{uuid}/`. Follow these rules:

| Scenario | Cleanup responsibility |
|----------|----------------------|
| **Sync render** (`POST /render/`) | Router cleans up temp dir in a `finally` block after reading bytes |
| **Async render** (`POST /render/async`) | Temp dir is **not** cleaned by the router — background job needs the texture file. `JobQueueService` cleanup loop removes the result file when the job expires; OS or a future GC pass reclaims the dir |
| **Render failure** | `RenderService` **preserves** the temp dir on failure (for debugging); the router still cleans the upload dir in its `finally` |
| **Cache clear** | `POST /cache/clear` uses `shutil.rmtree` to delete all `/tmp/blender_render_*` dirs manually |

**Pattern for sync operations**:
```python
render_id = str(uuid.uuid4())
temp_dir = Path(f"/tmp/blender_render_{render_id}")
temp_dir.mkdir(parents=True, exist_ok=True)
try:
    # ... do work ...
    image_bytes = result_path.read_bytes()
finally:
    shutil.rmtree(temp_dir, ignore_errors=True)
```

---

## Error Handling Conventions

Routers are responsible for mapping service exceptions to HTTP responses:

| Exception / condition | HTTP status | Notes |
|-----------------------|-------------|-------|
| `ValueError` from `validate_params()` | 400 Bad Request | Invalid resolution or sample count |
| `RenderError` from `RenderService` | 500 Internal Server Error | Blender subprocess failed |
| `AEIConversionError` with "not available" | 422 Unprocessable Entity | AEPi library missing |
| `AEIConversionError` (other) | 400 Bad Request | Codec failure |
| File/path not found | 404 Not Found | Job expired, ship_path missing, etc. |
| Job not yet complete | 409 Conflict | Polled too early |
| Upload read failure | 400 Bad Request | Corrupted or unreadable upload |
| Unexpected exception | 500 Internal Server Error | Always wrap with `raise ... from exc` |

Use `raise HTTPException(status_code=..., detail=str(exc)) from exc` to preserve the exception chain.

---

## Request/Response Patterns

### Multipart Form Uploads

Render and texture endpoints use `UploadFile` + `Form` fields (not JSON body):

```python
from fastapi import APIRouter, File, Form, UploadFile

@router.post("/endpoint")
async def my_endpoint(
    image: UploadFile = File(..., description="PNG image"),
    param: str = Form(..., description="Some parameter"),
    optional: int = Form(default=64, description="Optional int"),
):
    data = await image.read()
    ...
```

### Streaming Responses

All image-returning endpoints use `StreamingResponse`:

```python
from io import BytesIO
from fastapi.responses import StreamingResponse

output = BytesIO(image_bytes)
output.seek(0)
return StreamingResponse(
    output,
    media_type="image/png",
    headers={"Content-Disposition": "inline; filename=result.png"},
)
```

For AEI binary output, use `media_type="application/octet-stream"` with `attachment` disposition.

---

## Endpoint Documentation Standards

Every endpoint should have `summary` and `description` args on the decorator:

```python
@router.post(
    "/my-endpoint",
    summary="Short one-line description",
    description="Longer description of what this does, inputs, and outputs.",
    status_code=status.HTTP_200_OK,
)
```

Use `response_class=StreamingResponse` for endpoints that return images or binary files.

---

## Logging

Use `bblogger` with a consistent logger name:

```python
from shared import bblogger
flogger = bblogger.get_logger("blender-<router-name>-api-router")
```

Log levels:
- `INFO` — request received (with key params), response returned (with byte size)
- `WARNING` — validation errors, missing optional files, job not found
- `ERROR` — unexpected failures, failed file reads
- `DEBUG` — detailed internals (sizes, paths, intermediate values)

Always include identifying context (job IDs, file paths, sizes) in log messages.

---

## Complete Endpoint Inventory

### `cache.py`
| Method | Path | Status | Response |
|--------|------|--------|----------|
| `POST` | `/api/v1/cache/clear` | 200 | `{cleared_directories, freed_bytes, freed_mb, errors}` |
| `GET` | `/api/v1/cache/stats` | 200 | `{cache_directories, total_bytes, total_mb}` |

### `config.py`
| Method | Path | Status | Response |
|--------|------|--------|----------|
| `GET` | `/api/v1/config/render` | 200 | `RenderConfig.to_dict()` |
| `PUT` | `/api/v1/config/render` | 200 | Updated `RenderConfig.to_dict()` |
| `POST` | `/api/v1/config/render/reset` | 200 | Reset `RenderConfig.to_dict()` |

### `health.py`
| Method | Path | Status | Response |
|--------|------|--------|----------|
| `GET` | `/api/v1/health/` | 200/503 | `HealthResponse` (status, timestamp, version, environment, checks) |
| `GET` | `/api/v1/health/simple` | 200 | `SimpleHealthResponse` (status, timestamp) |
| `GET` | `/api/v1/health/liveness` | 200 | `{"status": "alive"}` |

### `jobs.py`
| Method | Path | Status | Response |
|--------|------|--------|----------|
| `GET` | `/api/v1/jobs/` | 200 | `list[RenderJob.to_dict()]` |
| `GET` | `/api/v1/jobs/{job_id}` | 200/404 | `RenderJob.to_dict()` |
| `GET` | `/api/v1/jobs/{job_id}/result` | 200/404/409 | `StreamingResponse` (image/png) |

### `render.py`
| Method | Path | Status | Response |
|--------|------|--------|----------|
| `POST` | `/api/v1/render/` | 200/400/422/500 | `StreamingResponse` (image/png) |
| `POST` | `/api/v1/render/async` | 202/400/422 | `{job_id, status, poll_url}` |

### `textures.py`
| Method | Path | Status | Response |
|--------|------|--------|----------|
| `POST` | `/api/v1/textures/composite` | 200/400/404/422 | `StreamingResponse` (image/png) |
| `POST` | `/api/v1/textures/convert` | 200/400/422/500 | `StreamingResponse` (application/octet-stream) |
| `GET` | `/api/v1/textures/health` | 200 | `{"status": "ok"}` |

---

## How to Add a New Router

1. Create `src/routers/my_router.py`:
   ```python
   """
   Brief module docstring.
   """
   from fastapi import APIRouter, Request
   from shared import bblogger

   flogger = bblogger.get_logger("blender-my-router-api-router")

   router = APIRouter(
       prefix="/my-prefix",
       tags=["my-prefix"],
       responses={
           400: {"description": "Bad request"},
       },
   )

   @router.get("/endpoint", summary="Short description")
   async def my_endpoint(request: Request) -> dict:
       """Detailed docstring."""
       return {"result": "ok"}
   ```

2. The router is automatically discovered on next startup — **no changes to `main.py` needed**.

3. Add tests in `tests/test_my_router.py`.

4. Run `ruff check src/routers/my_router.py` to verify code style.

---

*Last updated: 2026-03-16*
