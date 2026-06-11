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
├── Dockerfile                      # Legacy CUDA build (not referenced by any compose file)
├── Dockerfile.cpu                  # CPU-only build (python:3.13-slim-trixie) — used by docker-compose.yml
├── Dockerfile.gpu                  # CUDA build (nvidia/cuda runtime) — used by docker-compose-gpu.yml
├── docker-entrypoint.sh            # Privilege normalization + asset pipeline + CUDA warmup + app launch
├── requirements.txt                # Python dependencies
├── src/
│   ├── main.py                     # FastAPI app factory, lifespan, router auto-discovery
│   ├── assets/
│   │   ├── _mtl_utils.py           # Pure-Python MTL patching (patch_all_mtl_blocks) — testable outside Blender
│   │   ├── _render.py              # Blender-side render script (runs inside bpy environment)
│   │   └── cube.blend              # Default Blender scene file used for all renders
│   ├── lib/
│   │   └── AEPi/                   # Git submodule: https://github.com/Trimatix/AEPi.git
│   │       └── src/AEPi/           # AEI codec library (AEI, CompressionFormat)
│   ├── routers/
│   │   ├── AGENTS.md
│   │   ├── __init__.py
│   │   ├── cache.py                # POST /cache/clear, GET /cache/stats
│   │   ├── config.py               # GET/PUT /config/render, POST /config/render/reset
│   │   ├── health.py               # GET /health/, /health/simple, /health/liveness
│   │   ├── jobs.py                 # GET /jobs/, /jobs/{id}, /jobs/{id}/result
│   │   ├── render.py               # POST /render/ (sync), POST /render/async
│   │   └── textures.py             # POST /textures/composite, /textures/convert, GET /textures/health
│   ├── services/
│   │   ├── AGENTS.md
│   │   ├── __init__.py
│   │   ├── aei_conversion_service.py       # AEI format conversion via AEPi
│   │   ├── image_utils.py                  # is_square(), crop_to_square(), stretch_to_square()
│   │   ├── job_queue_service.py            # Async job queue, RenderJob, JobStatus
│   │   ├── render_config_service.py        # Runtime render settings (RenderConfig dataclass)
│   │   ├── render_service.py               # Blender subprocess pipeline, image trimming
│   │   └── texture_compositing_service.py  # Multi-layer PIL compositing
│   └── utils/
│       ├── __init__.py
│       └── safe_path.py            # Path-traversal validation (BLENDER_DATA_ROOT containment)
└── tests/
    ├── conftest.py                 # sys.path setup + BLENDER_DATA_ROOT=/tmp for path validation
    ├── test_safe_path.py
    ├── assets/                     # test_render_script_logic.py (_mtl_utils / render script logic)
    ├── routers/                    # 6 router test files
    └── services/                   # 6 service test files (14 test files total)
```

---

## Startup Flow

```
docker-entrypoint.sh
  ├── privilege normalization: if running as root (GPU image),
  │     mkdir -p /app/data/game-objects, chown -R botuser:botuser /app/data,
  │     chmod -R u+rwX /app/data, then re-exec itself via `gosu botuser`
  │     (the CPU image sets USER botuser at build time, so this block is a no-op there)
  ├── check_dependencies()          — verify 7z and gdown are on PATH (exit 1 if missing)
  ├── check_directory()             — look for .bmp/.jpg under /app/data/game-objects/
  ├── (if missing) download_and_extract()
  │     ├── gdown $GAME_OBJS_FILEID → /tmp/downloaded_file.7z
  │     └── 7z x → /app/data/, then rename extracted "game objects/" → "game-objects/"
  ├── GPU detection: nvidia-smi --list-gpus
  ├── (if GPU found) optional CUDA warmup: blender -b -P /tmp/warmup.py
  │     └── controlled by DO_WARMUP env var (true/false)
  └── (if GAME_OBJECTS_READY) /opt/venv/bin/python /app/src/main.py
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

main.py __main__ → uvicorn.run("main:app", workers=4, loop="uvloop", http="httptools")
```

**Key startup invariant**: The app will not start if game-object assets are not present at `/app/data/game-objects/`. This is enforced by `docker-entrypoint.sh`, which exits with code 1 on asset failure. Since `/app/data` is bind-mounted to the host (`./mappings/blender-renderer`), game-objects persist across container rebuilds.

---

## Asset Pipeline (docker-entrypoint.sh)

The entrypoint script runs before the Python app and manages Galaxy on Fire 2 3D game objects:

| Step | Detail |
|------|--------|
| **Privilege normalization** | If running as root (GPU image): `chown -R botuser:botuser /app/data`, `chmod -R u+rwX /app/data`, then re-exec as `botuser` via `gosu`. Makes the bind-mounted `/app/data` writable by the app user |
| **Asset check** | Searches `/app/data/game-objects/` for any `.bmp` or `.jpg` file (case-insensitive); logs `DIAG[boot]` instrumentation (uid/gid, file counts) before the check |
| **Download** | Uses `gdown $GAME_OBJS_FILEID` to fetch a `.7z` archive from Google Drive |
| **Extraction** | `7z x` extracts to `/app/data/`; the archive's `game objects/` dir (with a space) is renamed (or merged) to `/app/data/game-objects/` |
| **GPU detection** | `nvidia-smi --list-gpus` — detects NVIDIA GPU presence |
| **CUDA warmup** | If GPU detected AND `DO_WARMUP=true`: runs a 1-sample 64×64 Blender render to pre-compile CUDA kernels (one-time; the GPU compose healthcheck allows `start_period: 360s` for this) |
| **CPU fallback** | If no GPU: Blender will use CPU rendering automatically |
| **Launch gate** | App only starts if `GAME_OBJECTS_READY=true`; otherwise exits with code 1 |

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
| `PUT` | `/config/render` | Update one or more config fields; body is a plain `dict`. Unknown keys are ignored, but B.32: rejects with 422 if **no** recognised field is present. B.91: rejects with 422 if the result would violate a config invariant (`min ≤ default ≤ max`, positivity) |
| `POST` | `/config/render/reset` | Reset all config fields to env-var defaults |

Config is accessed via `request.app.state.render_config` (`RenderConfigService`).

### `/health` — Health Checks (`routers/health.py`)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health/` | Comprehensive: returns `HealthResponse` with status, timestamp, version, service, environment, checks dict |
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

Both endpoints accept: `texture` (UploadFile), `model_path` (str), `res_x` (int, default 1280), `res_y` (int, default 720), `num_samples` (int, default 32). `model_path` is validated against `BLENDER_DATA_ROOT` via `utils/safe_path.validate_user_path_http()` — paths outside the data root return 400.

Temp files are created at `/tmp/blender_render_{uuid}/` (sync) / `/tmp/blender_render_{job_id}/` (async). Sync renders clean up immediately; async renders keep the directory until the job expires.

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
| Notes | Gracefully handles missing AEPi library (warns at import, raises `AEIConversionError` at runtime). Image auto-converted to RGBA before encoding; dimensions snapped to the nearest multiple of 4 (NEAREST resize) to satisfy AEPi alignment. Validates format and quality (1–3), raising `AEIConversionError` on bad values. |

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
| `update(dict)` | Applies only known fields; unknown keys ignored. B.91: validates the candidate config first and raises `RenderConfigError` (no mutation) on invariant breach |
| `reset()` | Re-initialises from env vars by calling `__init__()` again |
| B.91 grouping | `RenderConfig.PARAM_GROUPS` / `to_grouped_dict()` group the 11 settings into `resolution_limits` / `sample_limits` / `defaults` / `concurrency`; `validate()` enforces the semantic invariants |

### `render_service.py` — Blender Subprocess Pipeline

| Item | Detail |
|------|--------|
| Class | `RenderService` |
| Exception | `RenderError` |
| Blender detection | Checks `/usr/bin/blender`, `/usr/local/bin/blender`, then `blender` on `$PATH` |
| Assets | Uses `src/assets/cube.blend` as scene, `src/assets/_render.py` as Blender script |
| Key methods | `clamp_params(res_x, res_y, num_samples) → ClampResult` (B.93), `render_ship(model_path, texture_path, output_path, res_x, res_y, num_samples) → Path` |
| Path validation | `render_ship()` re-validates `model_path` against `BLENDER_DATA_ROOT` via `utils/safe_path.validate_user_path()` (defence-in-depth — routers validate first) |
| Subprocess | `asyncio.create_subprocess_exec()` — non-blocking |
| Temp dir | `/tmp/blender_render_{uuid}/` — cleaned up on success; **preserved on failure** for debugging |
| OBJ/MTL handling | Copies **both** the OBJ and its `.mtl` to the temp dir (Blender resolves `mtllib` relative to the OBJ); falls back to `material.mtl` in the OBJ's dir, creates an empty MTL if none found |
| Communication | Passes `RENDER_ARGS_PATH` env var pointing to a 6-line `render_vars` file |
| Output trimming | `trim()` static method — RGBA images are cropped via the alpha channel's `getbbox()`; non-RGBA images fall back to an `ImageChops.difference` against the top-left pixel |

### `texture_compositing_service.py` — PIL Texture Compositing

| Item | Detail |
|------|--------|
| Class | `TextureCompositingService` |
| Stateless | Pure computation; no file I/O; no instance state |
| Key method | `composite_textures(base_texture, skin_base, region_textures, region_masks, disabled_regions) → Image` |
| Algorithm | 1) Start with `base_texture` (RGBA); 2) alpha-composite `skinBase.png` on top; 3) for each region: apply custom texture OR base_texture (disabled) through inverted mask; 4) return RGB |
| Size handling | `skinBase`, region textures, and masks are resized (LANCZOS) to match the base texture's size when they differ |
| Mask convention | Masks are **inverted** before use (Gimp convention is opposite to Pillow) |
| Output | RGB image (alpha stripped) |

---

## Render Pipeline — Detailed Walkthrough

### Synchronous Render (`POST /render/`)

```
Client POSTs multipart form (texture file + model_path + render params)
  │
  ├── validate_user_path_http(model_path) — 400 if outside BLENDER_DATA_ROOT
  ├── clamp_params() — clamp resolution/samples to RenderConfig bounds (B.93; never rejects)
  ├── Save texture → /tmp/blender_render_{uuid}/texture.png
  ├── RenderService.render_ship()
  │     ├── Copy OBJ + its .mtl → /tmp/blender_render_{uuid}/
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
  ├── validate_user_path_http(model_path) — 400 if outside BLENDER_DATA_ROOT
  ├── clamp_params() — clamp resolution/samples to RenderConfig bounds (B.93; never rejects)
  ├── job_queue.create_job() → RenderJob (status=QUEUED; raises ValueError → 500 if queue full)
  ├── Save texture → /tmp/blender_render_{job_id}/texture.png
  │    └── (temp dir NOT cleaned here — background task reads from it; cleaned immediately on upload failure)
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

`src/assets/_render.py` runs **inside** Blender's Python environment. It **cannot** import any FastAPI/service code. (It does import `_mtl_utils.py` from the same directory — a pure-Python module with no `bpy` dependency, kept separate so it can be unit-tested outside Blender.)

- Reads `RENDER_ARGS_PATH` env var (falls back to `/tmp/render_vars`)
- Parses 6-line `render_vars` file: `WIDTHxHEIGHT`, output path, temp OBJ path, texture path, samples, temp MTL path
- Rewrites the temp MTL via `_mtl_utils.patch_all_mtl_blocks()` — injects a `map_Kd <texture>` line (relative path) into **every** `newmtl` block, replacing any existing `map_Kd` lines
- Imports the OBJ, then applies a pure **Emission shader** node tree (Image Texture → Emission → Material Output) to every mesh material, creating new materials for meshes/slots without one — lighting-independent skin reproduction
- Renders with CYCLES: `film_transparent = True`, RGBA PNG output (enables alpha-based trim), OpenImageDenoise denoising
- GPU setup tries **OptiX → CUDA → CPU** in that order (OptiX needs `libnvoptix.so`, unavailable under WSL2)
- After the render, restores the original MTL content (aids debugging when the temp dir is preserved on failure)

**Do not import bpy outside this file** — bpy only exists inside Blender's embedded Python.

---

## Texture Compositing Pipeline

```
POST /textures/composite (multipart form):
  base_texture          — optional upload: region 0 underlayer (RGBA PNG)
  base_texture_path     — optional disk path to the base texture (e.g. the ship's
                          diffuse BMP); used when base_texture is not uploaded.
                          422 if neither is provided; upload wins if both are.
  ship_path             — path to .bbship directory on disk
  region_textures[]     — optional per-region overlay images
  region_indices        — comma-separated mask indices for each region_texture
  disabled_regions      — comma-separated mask indices to revert to base_texture
  square_mode           — "none" | "crop" | "stretch"

Server-side:
  ├── validate_user_path_http(ship_path) — 400 if outside BLENDER_DATA_ROOT
  │     (base_texture_path is validated the same way when used)
  ├── Validate ship_path exists and is a directory
  ├── Load skinBase.png from ship_path
  ├── Apply square_mode to base_texture if requested
  ├── Load mask files: {ship_path}/mask{N}.png (upscaled assets), falling back
  │     to mask{N}.jpg (original assets), for each needed index
  └── TextureCompositingService.composite_textures()
        ├── RGBA(base_texture)
        ├── alpha_composite(skinBase)   (skinBase resized to base size if needed)
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
| Test exclusion | AEPi's own tests are excluded via `--ignore=services/blender-service/src/lib/AEPi` in the root `pyproject.toml` pytest `addopts` |

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `DO_WARMUP` | (unset → off) | Set to `true` to pre-compile CUDA kernels at container startup |
| `GAME_OBJS_FILEID` | (required) | Google Drive file ID for the game-objects `.7z` archive |
| `BLENDER_HOST` | `0.0.0.0` | Uvicorn bind host |
| `BLENDER_PORT` / `PORT` | `8001` | Uvicorn bind port (also the published compose port) |
| `ACCESS_LOG` | `true` | Set to `false` to disable uvicorn access logging |
| `BLENDER_DATA_ROOT` | `/app/data` | Root that all user-supplied filesystem paths must resolve under (`utils/safe_path.py`); tests set it to `/tmp` |
| `RENDER_MAX_RES_X` | `1920` | Maximum render width (defaults sized for a 4-core / 8GB CPU VPS) |
| `RENDER_MAX_RES_Y` | `1080` | Maximum render height |
| `RENDER_DEFAULT_RES_X` | `1280` | Default render width (720p) |
| `RENDER_DEFAULT_RES_Y` | `720` | Default render height |
| `RENDER_DEFAULT_SAMPLES` | `32` | Default CYCLES samples |
| `RENDER_MAX_SAMPLES` | `64` | Maximum CYCLES samples |
| `RENDER_MAX_CONCURRENT` | `1` | Sets the `max_concurrent_renders` config field. **Note**: not currently wired to the job queue — `main.py` constructs `JobQueueService(max_concurrent=2)` with a hardcoded value |

---

## Docker Configuration

| Item | Value |
|------|-------|
| Port | `${BLENDER_PORT:-8001}` |
| Dockerfiles | `Dockerfile.cpu` (`python:3.13-slim-trixie`, Blender 4.5.10 tarball) for `docker-compose.yml`; `Dockerfile.gpu` (`nvidia/cuda:13.0.0-cudnn-runtime-ubuntu24.04`, Blender 4.5.10 tarball, installs `gosu`) for `docker-compose-gpu.yml`. The plain `Dockerfile` is a legacy CUDA build not referenced by any compose file |
| Runtime user | GPU image: starts as **root**, entrypoint chowns `/app/data` then drops to `botuser` via `gosu` (commit 18660e5 — fixes bind-mount permissions; `docker exec` defaults to root). CPU image: `USER botuser` at build time (entrypoint root-check is a no-op) |
| GPU support | `docker-compose-gpu.yml` via `deploy.resources.reservations.devices` (`driver: nvidia`, `count: 1`, `capabilities: [gpu]`) |
| Volume mount | `./mappings/blender-renderer:/app/data` (bind mount — game-objects persist across rebuilds) |
| Health check (standard) | `curl -s -o /dev/null -f http://localhost:${BLENDER_PORT:-8001}/api/v1/health/` — `start_period: 90s` |
| Health check (GPU) | Same endpoint — `start_period: 360s` (allows time for CUDA warmup) |
| Entrypoint | `docker-entrypoint.sh` run with `& tail -f /dev/null` (container stays up for debugging if the app crashes) |

---

## Testing

- **Location**: `services/blender-service/tests/`
- **Count**: 14 test files (`assets/` 1, `routers/` 6, `services/` 6, `test_safe_path.py`)
- **Runner**: `pytest` with `asyncio_mode=auto` (configured in the root `pyproject.toml`); `tests/conftest.py` sets `BLENDER_DATA_ROOT=/tmp` so path validation accepts pytest `tmp_path` fixtures
- **Linter**: Ruff (`target-version = "py313"`, `line-length = 120`)
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

- **Python**: 3.13+
- **Linter/formatter**: Ruff (`target-version = "py313"`, `line-length = 120`) — configured in `/proj/pyproject.toml`
- **Pydantic schemas**: `ConfigDict(from_attributes=True)`, `.model_dump()` (not `.dict()`)
- **Max mocks per test**: 2
- **Logging**: Use `bblogger.get_logger("blender-<component>")`. Log INFO for normal ops, ERROR for failures, DEBUG for diagnostic detail. Always include render/job IDs.
- **Error handling**: Routers catch service exceptions and map them to appropriate HTTP status codes. Services raise typed exceptions (`RenderError`, `AEIConversionError`).
- **No CORS**: Internal service only — no browser clients.

---

*Last updated: 2026-06-11*
