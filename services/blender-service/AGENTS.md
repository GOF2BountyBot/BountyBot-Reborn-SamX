# AGENTS.md - blender-service

This file provides guidance for AI agents working on the blender-service. Always read this file before making changes within this service.

---

## Service Overview

**blender-service** is a GPU-accelerated rendering and texture-processing micro-service for the BountyBot-Reborn-SamX stack. It provides:

- **Synchronous and asynchronous 3D ship rendering** via Blender (headless CYCLES engine)
- **Multi-layer texture compositing** using PIL/Pillow (skinBase + mask-file pipeline)
- **AEI image format conversion** (Android/PC game image formats) via the AEPi submodule

It is an **internal service** — no browser clients, no CORS, no authentication layer. It receives requests from `discord-gateway` and returns rendered images as streaming PNG/binary responses.

---

## Technology Stack

| Technology | Role |
|------------|------|
| **FastAPI** | Web framework, auto-router discovery |
| **Blender** (headless, CYCLES engine) | 3D rendering via subprocess |
| **PIL / Pillow** | Texture compositing, image trimming, format conversion |
| **AEPi** (git submodule) | AEI (Abyss Engine Image) format encoding — ETC1/DXT5/DXT1 |
| **asyncio** | Async render job queue with semaphore concurrency control |
| **CUDA / nvidia-smi** | Optional GPU acceleration for Blender CYCLES |
| **gdown** | Google Drive asset downloader (used in docker-entrypoint.sh) |
| **7zip (7z)** | Archive extraction for game-object assets |
| **bblogger** | Shared logging utility (copied from `services/shared/`) |
| **uvicorn** | ASGI server |

---

## Directory Structure

```
services/blender-service/
├── AGENTS.md                       # This file
├── Dockerfile                      # Container build
├── docker-entrypoint.sh            # Asset pipeline + CUDA warmup + app launch
├── requirements.txt                # Python dependencies
├── src/
│   ├── main.py                     # FastAPI app factory, lifespan, router auto-discovery
│   ├── assets/
│   │   ├── _render.py              # Blender-side render script (runs inside bpy environment)
│   │   └── cube.blend              # Default Blender scene file used for all renders
│   ├── lib/
│   │   └── AEPi/                   # Git submodule: https://github.com/Trimatix/AEPi.git
│   │       └── src/AEPi/           # AEI codec library (AEI, CompressionFormat)
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── cache.py                # POST /cache/clear, GET /cache/stats
│   │   ├── config.py               # GET/PUT /config/render, POST /config/render/reset
│   │   ├── health.py               # GET /health/, /health/simple, /health/liveness
│   │   ├── jobs.py                 # GET /jobs/, /jobs/{id}, /jobs/{id}/result
│   │   ├── render.py               # POST /render/ (sync), POST /render/async
│   │   └── textures.py             # POST /textures/composite, /textures/convert, GET /textures/health
│   ├── services/
│   │   ├── __init__.py
│   │   ├── aei_conversion_service.py       # AEI format conversion via AEPi
│   │   ├── image_utils.py                  # is_square(), crop_to_square(), stretch_to_square()
│   │   ├── job_queue_service.py            # Async job queue, RenderJob, JobStatus
│   │   ├── render_config_service.py        # Runtime render settings (RenderConfig dataclass)
│   │   ├── render_service.py               # Blender subprocess pipeline, image trimming
│   │   └── texture_compositing_service.py  # Multi-layer PIL compositing
│   └── utils/
│       └── __init__.py
└── tests/
    └── ...                         # 14 test files, 104 tests
```

---

## Startup Flow

```
docker-entrypoint.sh
  ├── check_dependencies()          — verify 7z and gdown are on PATH
  ├── check_directory()             — look for .bmp/.jpg under /app/data/game-objects/
  ├── (if missing) download_and_extract()
  │     ├── gdown $GAME_OBJS_FILEID → /tmp/downloaded_file.7z
  │     └── 7z x → /app/data/game-objects/
  ├── GPU detection: nvidia-smi --list-gpus
  ├── (if GPU found) optional CUDA warmup: blender -b -P /tmp/warmup.py
  │     └── controlled by DO_WARMUP env var (true/false)
  └── (if GAME_OBJECTS_READY) python /app/src/main.py
       └── exits 1 if assets not available — app will NOT start

main.py → create_app()
  ├── FastAPI app instantiated with lifespan handler
  ├── lifespan startup:
  │     ├── app.state.render_config = RenderConfigService()   (reads env vars)
  │     ├── app.state.job_queue = JobQueueService(max_concurrent=2)
  │     └── asyncio.create_task(job_queue.start_cleanup_loop())
  ├── include_routers(app):
  │     └── pkgutil.iter_modules(routers.__path__) → auto-imports all router modules
  │         → any module with a `router` attribute is included under /api/v1/
  └── lifespan shutdown:
        ├── cleanup_task.cancel()
        └── job_queue.shutdown()
```

**Key startup invariant**: The app will not start if game-object assets are not present at `/app/data/game-objects/`. This is enforced by `docker-entrypoint.sh`, which exits with code 1 on asset failure. Since `/app/data` is bind-mounted to the host (`./mappings/blender-renderer`), game-objects persist across container rebuilds.

---

## Asset Pipeline (docker-entrypoint.sh)

The entrypoint script runs before the Python app and manages Galaxy on Fire 2 3D game objects:

| Step | Detail |
|------|--------|
| **Asset check** | Searches `/app/data/game-objects/` for any `.bmp` or `.jpg` file (case-insensitive) |
| **Download** | Uses `gdown $GAME_OBJS_FILEID` to fetch a `.7z` archive from Google Drive |
| **Extraction** | `7z x` extracts to `/app/data/` (so `game-objects/` lands at `/app/data/game-objects/`) |
| **GPU detection** | `nvidia-smi --list-gpus` — detects NVIDIA GPU presence |
| **CUDA warmup** | If GPU detected AND `DO_WARMUP=true`: runs a 1-sample 64×64 Blender render to pre-compile CUDA kernels (3–5 min, one-time) |
| **CPU fallback** | If no GPU: Blender will use CPU rendering automatically |
| **Launch gate** | App only starts if `GAME_OBJECTS_READY=true`; otherwise exits with code 1 |

**Local dev shortcut**: Mount pre-downloaded assets: `./old-refs/items/ships/ → /app/data/game-objects/items/ships/`

---

## API Endpoints — Complete Reference

All endpoints are mounted under `/api/v1/`.

### `/cache` — Cache Management (`routers/cache.py`)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/cache/clear` | Delete all `/tmp/blender_render_*` directories; returns `{cleared_directories, freed_bytes, freed_mb, errors}` |
| `GET` | `/cache/stats` | Return current cache usage `{cache_directories, total_bytes, total_mb}` |

### `/config` — Render Configuration (`routers/config.py`)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/config/render` | Return all current `RenderConfig` field values |
| `PUT` | `/config/render` | Update one or more config fields (unknown keys silently ignored); body is a plain `dict` |
| `POST` | `/config/render/reset` | Reset all config fields to env-var defaults |

Config is accessed via `request.app.state.render_config` (`RenderConfigService`).

### `/health` — Health Checks (`routers/health.py`)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health/` | Comprehensive: returns `HealthResponse` with status, version, environment, checks dict |
| `GET` | `/health/simple` | Lightweight: `{status, timestamp}` — for load balancers |
| `GET` | `/health/liveness` | Liveness probe: `{status: "alive"}` — for orchestrators |

Health check requests are filtered from uvicorn access logs (`HealthFilter`).

### `/jobs` — Async Job Management (`routers/jobs.py`)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/jobs/` | List all active (non-expired) jobs |
| `GET` | `/jobs/{job_id}` | Get status + metadata for a specific job; 404 if not found/expired |
| `GET` | `/jobs/{job_id}/result` | Download completed PNG; 409 if not complete, 404 if expired |

Job state is accessed via `request.app.state.job_queue` (`JobQueueService`).

### `/render` — 3D Ship Rendering (`routers/render.py`)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/render/` | **Sync render**: multipart form upload → waits for Blender → returns PNG stream |
| `POST` | `/render/async` | **Async render**: queues job, returns immediately `{job_id, status, poll_url}` |

Both endpoints accept: `texture` (UploadFile), `model_path` (str), `res_x` (int, default 1920), `res_y` (int, default 1080), `num_samples` (int, default 64).

Temp files are created at `/tmp/blender_render_{uuid}/`. Sync renders clean up immediately; async renders keep the directory until the job expires.

### `/textures` — Texture Operations (`routers/textures.py`)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/textures/composite` | Multi-layer texture compositing; returns PNG stream |
| `POST` | `/textures/convert` | Convert PNG to AEI format (etc1/dxt5/dxt1); returns binary octet-stream |
| `GET` | `/textures/health` | Simple liveness check for the textures router |

---

## Services — Complete Reference

All services live in `src/services/`. See `src/services/AGENTS.md` for detailed patterns.

### `aei_conversion_service.py` — AEI Conversion

| Item | Detail |
|------|--------|
| Class | `AEIConversionService` |
| Exception | `AEIConversionError` |
| Constant | `SUPPORTED_FORMATS: dict[str, str]` — `{"etc1": "ETC1", "dxt5": "DXT5", "dxt1": "DXT1"}` |
| Key method | `convert_to_aei(image, target_format, quality) → BytesIO` |
| Notes | Gracefully handles missing AEPi library (warns at import, raises `AEIConversionError` at runtime). Image auto-converted to RGBA before encoding. |

### `image_utils.py` — Image Utility Functions

| Function | Purpose |
|----------|---------|
| `is_square(image)` | Returns `True` if width == height |
| `crop_to_square(image)` | Centre-crops to `min(w, h)` × `min(w, h)`; odd pixel goes to bottom/right |
| `stretch_to_square(image)` | Resizes to `max(w, h)` × `max(w, h)` using LANCZOS resampling |
| `check_and_report_square(image)` | Diagnostic dict: `{is_square, width, height, difference, longer_side}` |

Pure functions, no state, no I/O.

### `job_queue_service.py` — Async Job Queue

| Item | Detail |
|------|--------|
| Class | `JobQueueService` |
| Dataclass | `RenderJob` |
| Enum | `JobStatus` — `QUEUED`, `PROCESSING`, `COMPLETE`, `FAILED` |
| Concurrency | `asyncio.Semaphore(max_concurrent)` — default 2 simultaneous renders |
| Queue limit | `max_queue_size` (default 100) — raises `ValueError` when full |
| Storage | In-memory `dict[str, RenderJob]`; **no persistence** — all jobs lost on restart |
| Task GC | Active tasks stored in `_active_tasks: set[asyncio.Task]` to prevent garbage collection |
| Stuck-job detection | Jobs in `PROCESSING` for > 30 minutes are auto-marked `FAILED` |
| TTL | Completed/failed jobs expire 1 hour after `completed_at` |
| Cleanup loop | `start_cleanup_loop(interval_seconds=300)` — called as background asyncio task in lifespan |
| Job IDs | 8-character hex UUID prefix (e.g., `"a1b2c3d4"`) |

### `render_config_service.py` — Runtime Render Configuration

| Item | Detail |
|------|--------|
| Class | `RenderConfigService` |
| Dataclass | `RenderConfig` |
| Storage | In-memory; **no persistence** — reset on restart |
| Defaults | Read from env vars at init (see Environment Variables section) |
| Key fields | `max_res_x/y`, `min_res_x/y`, `max/min_samples`, `default_res_x/y`, `default_samples`, `max_concurrent_renders`, `job_ttl_hours` |
| `update(dict)` | Applies only known fields; unknown keys are silently ignored |
| `reset()` | Re-initialises from env vars by calling `__init__()` again |

### `render_service.py` — Blender Subprocess Pipeline

| Item | Detail |
|------|--------|
| Class | `RenderService` |
| Exception | `RenderError` |
| Blender detection | Checks `/usr/bin/blender`, `/usr/local/bin/blender`, then `blender` on `$PATH` |
| Assets | Uses `src/assets/cube.blend` as scene, `src/assets/_render.py` as Blender script |
| Key method | `render_ship(model_path, texture_path, output_path, res_x, res_y, num_samples) → Path` |
| Subprocess | `asyncio.create_subprocess_exec()` — non-blocking |
| Temp dir | `/tmp/blender_render_{uuid}/` — cleaned up on success; **preserved on failure** for debugging |
| MTL handling | Copies the OBJ's `.mtl` to temp dir; creates empty MTL if none found |
| Communication | Passes `RENDER_ARGS_PATH` env var pointing to a 6-line `render_vars` file |
| Output trimming | `trim()` static method — crops transparent/background borders using `ImageChops` |

### `texture_compositing_service.py` — PIL Texture Compositing

| Item | Detail |
|------|--------|
| Class | `TextureCompositingService` |
| Stateless | Pure computation; no file I/O; no instance state |
| Key method | `composite_textures(base_texture, skin_base, region_textures, region_masks, disabled_regions) → Image` |
| Algorithm | 1) Start with `base_texture` (RGBA); 2) alpha-composite `skinBase.png` on top; 3) for each region: apply custom texture OR base_texture (disabled) through inverted mask; 4) return RGB |
| Mask convention | Masks are **inverted** before use (Gimp convention is opposite to Pillow) |
| Output | RGB image (alpha stripped) |

---

## Render Pipeline — Detailed Walkthrough

### Synchronous Render (`POST /render/`)

```
Client POSTs multipart form (texture file + model_path + render params)
  │
  ├── validate_params() — check resolution/samples within RenderConfig bounds
  ├── Save texture → /tmp/blender_render_{uuid}/texture.png
  ├── RenderService.render_ship()
  │     ├── Copy OBJ's .mtl → /tmp/blender_render_{uuid}/
  │     ├── Write render_vars (6-line file)
  │     ├── asyncio.create_subprocess_exec(blender -b cube.blend -P _render.py)
  │     │     └── env: RENDER_ARGS_PATH=/tmp/blender_render_{uuid}/render_vars
  │     ├── await proc.communicate()  (blocks until Blender exits)
  │     ├── Check returncode == 0
  │     ├── Check output PNG exists
  │     ├── PIL trim (crop transparent borders)
  │     └── Clean up temp dir (on success only)
  ├── Read PNG bytes
  ├── Clean up /tmp/blender_render_{uuid}/
  └── Return StreamingResponse (image/png)
```

### Asynchronous Render (`POST /render/async`)

```
Client POSTs multipart form
  │
  ├── validate_params()
  ├── job_queue.create_job() → RenderJob (status=QUEUED)
  ├── Save texture → /tmp/blender_render_{job_id}/texture.png
  │    └── (temp dir NOT cleaned here — background task reads from it)
  ├── job_queue.submit_job(job, render_coro)
  │     └── asyncio.create_task(_process_job()) — runs in background
  └── Return 202 {job_id, status: "queued", poll_url}

Background task (_process_job):
  ├── Acquire semaphore (max 2 concurrent)
  ├── RenderService.render_ship() — same as sync pipeline above
  ├── On success: job.status = COMPLETE, job.result_path = output.png path
  └── On failure: job.status = FAILED, job.error_message = str(exc)

Client polls:
  GET /api/v1/jobs/{job_id}  → {status, ...}
  GET /api/v1/jobs/{job_id}/result  → StreamingResponse (image/png)  [when COMPLETE]
```

### _render.py — Blender-Side Script

`src/assets/_render.py` runs **inside** Blender's Python environment. It **cannot** import any FastAPI/service code.

- Reads `RENDER_ARGS_PATH` env var (falls back to `/tmp/render_vars`)
- Parses 6-line `render_vars` file: `WIDTHxHEIGHT`, output path, OBJ path, texture path, samples, MTL path
- Appends `map_Kd <texture_path>` to the temp MTL, imports OBJ, renders with CYCLES
- Attempts CUDA GPU setup; falls back to CPU on failure
- Cleans up the appended `map_Kd` line from the MTL after render

**Do not import bpy outside this file** — bpy only exists inside Blender's embedded Python.

---

## Texture Compositing Pipeline

```
POST /textures/composite (multipart form):
  base_texture          — region 0 underlayer (RGBA PNG)
  ship_path             — path to .bbship directory on disk
  region_textures[]     — optional per-region overlay images
  region_indices        — comma-separated mask indices for each region_texture
  disabled_regions      — comma-separated mask indices to revert to base_texture
  square_mode           — "none" | "crop" | "stretch"

Server-side:
  ├── Validate ship_path exists and is a directory
  ├── Load skinBase.png from ship_path
  ├── Apply square_mode to base_texture if requested
  ├── Load mask files: {ship_path}/mask{N}.jpg for each needed index
  └── TextureCompositingService.composite_textures()
        ├── RGBA(base_texture)
        ├── alpha_composite(skinBase)
        └── for each mask N (1..max):
              ├── if N in region_textures: apply custom texture via inverted mask
              ├── elif N in disabled_regions: apply base_texture via inverted mask
              └── else: skip
        → RGB result → PNG StreamingResponse
```

---

## AEPi Submodule

| Field | Value |
|-------|-------|
| Path | `src/lib/AEPi/` |
| URL | https://github.com/Trimatix/AEPi.git |
| Init | `git submodule update --init --recursive` |
| Source | `src/lib/AEPi/src/AEPi/` |
| Imports | `from AEPi import AEI, CompressionFormat` |
| Formats | `ETC1` (Android), `DXT5` (PC with alpha), `DXT1` (PC, no alpha) |
| Graceful degradation | Import failure is caught; raises `AEIConversionError` at runtime |
| Test exclusion | AEPi's own tests are excluded via a `conftest.py` at the submodule root |

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `NVIDIA_VISIBLE_DEVICES` | (unset) | Set to `all` to expose GPUs in Docker |
| `DO_WARMUP` | `false` | Set to `true` to pre-compile CUDA kernels at container startup (3–5 min delay) |
| `GAME_OBJS_FILEID` | (required) | Google Drive file ID for the game-objects `.7z` archive |
| `BLENDER_HOST` | `0.0.0.0` | Uvicorn bind host |
| `BLENDER_PORT` / `PORT` | `8001` | Uvicorn bind port |
| `ACCESS_LOG` | `true` | Set to `false` to disable uvicorn access logging |
| `RENDER_MAX_RES_X` | `3840` | Maximum render width |
| `RENDER_MAX_RES_Y` | `2160` | Maximum render height |
| `RENDER_DEFAULT_RES_X` | `1920` | Default render width |
| `RENDER_DEFAULT_RES_Y` | `1080` | Default render height |
| `RENDER_DEFAULT_SAMPLES` | `64` | Default CYCLES samples |
| `RENDER_MAX_SAMPLES` | `128` | Maximum CYCLES samples |
| `RENDER_MAX_CONCURRENT` | `2` | Maximum simultaneous Blender renders |

---

## Docker Configuration

| Item | Value |
|------|-------|
| Port | `8001` |
| GPU support | Via `docker-compose-gpu.yml` with `NVIDIA_VISIBLE_DEVICES=all` |
| Volume mount | `./mappings/blender-renderer:/app/data` |
| Health check (standard) | `curl -s -o /dev/null -f http://localhost:8001/api/v1/health/simple` — `start_period: 90s` |
| Health check (GPU) | Same endpoint — `start_period: 360s` (allows time for CUDA warmup) |
| Entrypoint | `docker-entrypoint.sh` (asset pipeline + app launch) |

---

## Testing

- **Location**: `services/blender-service/tests/`
- **Count**: 14 test files, 104 tests
- **Runner**: `pytest` with `asyncio_mode=auto`
- **Linter**: Ruff (`target-version = "py312"`, `line-length = 120`)
- **Max mocks per test**: 2 (project-wide standard)
- **Pydantic**: Use `model_config = ConfigDict(from_attributes=True)` and `.model_dump()` (not deprecated `.dict()`)

Run tests from the service directory:
```bash
cd services/blender-service
pytest tests/ -v
```

---

## Common Tasks

### Adding a New Router

1. Create `src/routers/my_router.py` with:
   ```python
   from fastapi import APIRouter
   router = APIRouter(prefix="/my-prefix", tags=["my-prefix"])

   @router.get("/endpoint")
   async def my_endpoint(): ...
   ```
2. The router is **auto-discovered** — no registration needed in `main.py`.
3. Add tests in `tests/`.
4. See `src/routers/AGENTS.md` for full patterns.

### Adding a New Service

1. Create `src/services/my_service.py`.
2. If it needs to be shared across requests, initialise it in the lifespan in `main.py` and store on `app.state`.
3. Access from routers via `request.app.state.my_service`.
4. See `src/services/AGENTS.md` for full patterns.

### Adding a New Endpoint to an Existing Router

1. Add a new `@router.get/post/put/delete` decorated function to the relevant file in `src/routers/`.
2. Follow existing parameter and error-handling patterns in that file.
3. Add tests in `tests/`.

---

## Code Standards

- **Python**: 3.12+
- **Linter/formatter**: Ruff (`target-version = "py312"`, `line-length = 120`) — configured in `/proj/pyproject.toml`
- **Pydantic schemas**: `ConfigDict(from_attributes=True)`, `.model_dump()` (not `.dict()`)
- **Max mocks per test**: 2
- **Logging**: Use `bblogger.get_logger("blender-<component>")`. Log INFO for normal ops, ERROR for failures, DEBUG for diagnostic detail. Always include render/job IDs.
- **Error handling**: Routers catch service exceptions and map them to appropriate HTTP status codes. Services raise typed exceptions (`RenderError`, `AEIConversionError`).
- **No CORS**: Internal service only — no browser clients.

---

*Last updated: 2026-03-16*
