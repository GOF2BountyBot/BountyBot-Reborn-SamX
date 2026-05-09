# AGENTS.md - blender-service/src/services/

This file provides guidance for AI agents working on the services layer of blender-service.

---

## Overview

The `services/` package contains all business logic for blender-service. There are 6 service modules plus `image_utils.py` (pure functions) and an `__init__.py`.

Services are intentionally decoupled from FastAPI — they accept and return Python/PIL objects, not HTTP request/response types. File I/O that requires HTTP uploads or streaming responses is handled by the router layer before calling a service.

---

## Service Patterns

### Stateless vs. Stateful

| Service | Stateful? | Initialisation |
|---------|-----------|----------------|
| `RenderConfigService` | Yes — holds `RenderConfig` in memory | `lifespan()` in `main.py` → `app.state.render_config` |
| `JobQueueService` | Yes — holds job dict + semaphore + task set | `lifespan()` in `main.py` → `app.state.job_queue` |
| `RenderService` | Minimal — stores config ref and paths | Instantiated per-request in routers |
| `TextureCompositingService` | No | Module-level singleton in `textures.py` router |
| `AEIConversionService` | No | Module-level singleton in `textures.py` router |
| `image_utils.py` | N/A — pure functions | No instantiation needed |

### Lifecycle Services (app.state)

`RenderConfigService` and `JobQueueService` must live for the full application lifetime because they hold shared mutable state (config values and the job registry respectively). They are created once in `lifespan()` and attached to `app.state`:

```python
# In main.py lifespan():
app.state.render_config = RenderConfigService()
app.state.job_queue = JobQueueService(max_concurrent=2)
```

Routers access them via `request.app.state.render_config` and `request.app.state.job_queue`.

### Stateless Services

`TextureCompositingService` and `AEIConversionService` are instantiated as module-level singletons inside the router that uses them — they carry no per-request state:

```python
# At module level in textures.py:
_service = TextureCompositingService()
_aei_service = AEIConversionService()
```

`RenderService` is instantiated per-request because it accepts a `RenderConfig` that may change at runtime:

```python
render_config = request.app.state.render_config
service = RenderService(render_config.config if render_config is not None else None)
```

---

## Service Reference

### `aei_conversion_service.py`

**Purpose**: Converts PIL images to AEI (Abyss Engine Image) binary format using the AEPi submodule.

**Key types**:
- `AEIConversionService` — the service class
- `AEIConversionError` — exception raised on failure
- `SUPPORTED_FORMATS: dict[str, str]` — `{"etc1": "ETC1", "dxt5": "DXT5", "dxt1": "DXT1"}`

**Key method**:
```python
def convert_to_aei(
    self,
    image: Image.Image,
    target_format: str,   # "etc1", "dxt5", "dxt1"
    quality: int = 3,     # 1 (fast) to 3 (best)
) -> io.BytesIO:
```

Returns a `BytesIO` seeked to position 0, containing the raw AEI binary.

**AEPi availability**: AEPi is a git submodule. If the submodule is not initialised or its import fails, `AEI` and `CompressionFormat` are set to `None` at module level (graceful import fallback). The service raises `AEIConversionError("AEPi library is not available...")` at runtime rather than crashing at import time.

**Image handling**: Source image is auto-converted to RGBA before encoding, regardless of input mode.

---

### `image_utils.py`

**Purpose**: Pure helper functions for image squareness checking and transformation.

**Functions**:

| Function | Signature | Returns |
|----------|-----------|---------|
| `is_square` | `(image: Image.Image) → bool` | `True` if width == height |
| `crop_to_square` | `(image: Image.Image) → Image.Image` | Centre-cropped to `min(w, h)` square; odd pixel goes to bottom/right |
| `stretch_to_square` | `(image: Image.Image) → Image.Image` | Resized to `max(w, h)` square using LANCZOS |
| `check_and_report_square` | `(image: Image.Image) → dict` | `{is_square, width, height, difference, longer_side}` |

**No state, no I/O, no instantiation required.** Import and call directly:
```python
from services.image_utils import crop_to_square, stretch_to_square, is_square
```

---

### `job_queue_service.py`

**Purpose**: In-memory async job queue for render tasks, with semaphore-based concurrency control.

**Key types**:

```
JobStatus (StrEnum):  QUEUED | PROCESSING | COMPLETE | FAILED

RenderJob (dataclass):
  job_id: str                     # 8-char hex UUID prefix
  status: JobStatus
  created_at: datetime
  started_at: datetime | None
  completed_at: datetime | None
  result_path: str | None         # set when COMPLETE
  error_message: str | None       # set when FAILED
  model_path: str
  res_x: int
  res_y: int
  num_samples: int
  is_expired (property)           # True if completed > 1 hour ago
  to_dict() → dict

JobQueueService:
  __init__(max_concurrent=2, max_queue_size=100)
  create_job(model_path, res_x, res_y, num_samples) → RenderJob
  get_job(job_id) → RenderJob | None       # None if not found or expired
  list_jobs() → list[dict]
  submit_job(job, render_coro) → None      # schedules background asyncio.Task
  start_cleanup_loop(interval_seconds=300) # async — run as background task
  shutdown()                               # cancel cleanup + all active tasks
```

**Concurrency model**:
- `asyncio.Semaphore(max_concurrent)` limits simultaneous Blender processes
- Jobs beyond the semaphore limit queue naturally (Python task scheduler holds them)
- Hard queue limit via `max_queue_size`: `create_job()` raises `ValueError` when the count of QUEUED + PROCESSING jobs meets the limit

**Task GC prevention**: Background render tasks are stored in `_active_tasks: set[asyncio.Task]`. A `done_callback` removes each task when it finishes. This prevents Python's garbage collector from cancelling orphaned tasks.

**Stuck-job detection**: `RenderJob.is_expired` checks for jobs in `PROCESSING` status for > 30 minutes and auto-transitions them to `FAILED` with a timeout message. The job is not immediately deleted — it becomes eligible for cleanup on the next cycle.

**TTL/expiry**:
- Completed/failed jobs expire 1 hour after `completed_at`
- `get_job()` and `list_jobs()` call expiry checks lazily
- `start_cleanup_loop()` runs every 300 seconds to proactively purge expired jobs
- When a job is cleaned up, its `result_path` file is also deleted (`Path.unlink(missing_ok=True)`)

**No persistence**: All job state is in-memory. Jobs are lost on service restart.

**Important**: `start_cleanup_loop()` is an async coroutine that loops indefinitely. It must be wrapped in `asyncio.create_task()` and cancelled during shutdown:
```python
# In lifespan():
cleanup_task = asyncio.create_task(app.state.job_queue.start_cleanup_loop())
yield
cleanup_task.cancel()
app.state.job_queue.shutdown()
```

---

### `render_config_service.py`

**Purpose**: Centralised, runtime-mutable render configuration backed by environment variables.

**Key types**:

```
RenderConfig (dataclass):   # All fields mutable
  max_res_x: int            # default 3840
  max_res_y: int            # default 2160
  min_res_x: int            # default 352
  min_res_y: int            # default 240
  max_samples: int          # default 128
  min_samples: int          # default 1
  default_res_x: int        # default 1920
  default_res_y: int        # default 1080
  default_samples: int      # default 64
  max_concurrent_renders: int   # default 2
  job_ttl_hours: int            # default 1
  to_dict() → dict

RenderConfigService:
  __init__()            # reads env vars, creates RenderConfig
  config (property)     # returns current RenderConfig
  update(dict) → RenderConfig   # applies known fields only, silently ignores unknown
  reset() → RenderConfig        # re-runs __init__() to re-read env vars
```

**No persistence**: Config is in-memory. `reset()` re-reads env vars at call time.

**Env var mapping** (read at init / reset):

| Env var | `RenderConfig` field |
|---------|---------------------|
| `RENDER_MAX_RES_X` | `max_res_x` |
| `RENDER_MAX_RES_Y` | `max_res_y` |
| `RENDER_DEFAULT_RES_X` | `default_res_x` |
| `RENDER_DEFAULT_RES_Y` | `default_res_y` |
| `RENDER_DEFAULT_SAMPLES` | `default_samples` |
| `RENDER_MAX_SAMPLES` | `max_samples` |
| `RENDER_MAX_CONCURRENT` | `max_concurrent_renders` |

---

### `render_service.py`

**Purpose**: Orchestrates the full Blender rendering pipeline — from parameter validation to delivering a trimmed PNG.

**Key types**:
- `RenderService` — the service class
- `RenderError` — raised on Blender subprocess failure or missing output

**Constructor**:
```python
RenderService(config: RenderConfig | None = None)
```
If `config` is `None`, a default `RenderConfig()` is used. Routers should always pass `request.app.state.render_config.config` so the live runtime config is respected.

**Key public methods**:

| Method | Signature | Notes |
|--------|-----------|-------|
| `validate_params` | `(res_x, res_y, num_samples)` | Raises `ValueError` if out of config bounds |
| `trim` (static) | `(image: Image.Image) → Image.Image` | Crops transparent/background borders using `ImageChops.difference` |
| `render_ship` | `(model_path, texture_path, output_path, res_x, res_y, num_samples) → Path` | Full pipeline; async |

**Blender subprocess**:
- Command: `blender -b <cube.blend> -P <_render.py>`
- Environment variable `RENDER_ARGS_PATH` points to the 6-line `render_vars` file in the temp dir
- `asyncio.create_subprocess_exec()` — non-blocking, awaited via `proc.communicate()`
- Non-zero return code → `RenderError`
- Missing output file → `RenderError`

**MTL handling**:
- Looks for `{model_path}.mtl` (same stem), then `{model_path_dir}/material.mtl`
- Copies the MTL to the temp dir for concurrent safety (each render gets its own copy)
- Creates an empty MTL if none is found (Blender script appends `map_Kd` to it)

**render_vars format** (6 lines, written by `render_service.py`, read by `_render.py`):
```
{res_x}x{res_y}
{output_path}
{model_path}
{texture_path}
{num_samples}
{temp_mtl_path}
```

**Temp directory lifecycle**:
- Created at `render_ship()` start: `/tmp/blender_render_{uuid}/`
- Cleaned up by `render_service.py` on **success** via `shutil.rmtree`
- **Preserved on failure** to aid debugging — log message indicates the path
- The router (for sync renders) also has a `finally` cleanup — both run `ignore_errors=True`

**Blender discovery order**: `/usr/bin/blender` → `/usr/local/bin/blender` → `blender` (PATH)

---

### `texture_compositing_service.py`

**Purpose**: Composites multi-layer ship textures using PIL according to the game's skinning algorithm.

**Key type**: `TextureCompositingService` — stateless, all methods are pure.

**Key method**:
```python
def composite_textures(
    self,
    base_texture: Image.Image,       # region 0 underlayer (will be RGBA-converted)
    skin_base: Image.Image,          # skinBase.png from ship assets directory
    region_textures: dict[int, Image.Image],  # mask_index → overlay image
    region_masks: dict[int, Image.Image],     # mask_index → mask image
    disabled_regions: list[int] | None = None,  # revert these regions to base_texture
) -> Image.Image:                    # returns RGB (alpha stripped)
```

**Compositing algorithm** (step by step):
1. Convert `base_texture` to RGBA
2. Alpha-composite `skinBase` on top
3. Determine `max_layer_num = max(region_textures.keys() + disabled_regions)`
4. For each `mask_num` in `range(1, max_layer_num + 1)`:
   - If `mask_num` in `region_textures`: use the custom texture
   - Elif `mask_num` in `disabled_regions`: use `base_texture`
   - Else: skip
   - If no mask image for the region: log warning, skip
   - Invert the mask (Gimp convention ≠ Pillow convention)
   - `Image.composite(working_tex, new_tex, inverted_mask)`
5. Convert final image to RGB (strip alpha) and return

**Mask inversion**: The game's mask files follow Gimp conventions where white = transparent and black = opaque, which is the inverse of Pillow's composite convention. The service inverts every mask with `ImageOps.invert()` before use. RGBA masks are converted to RGB before inversion (Pillow's `invert` does not support RGBA).

**No I/O**: All images are passed in as `PIL.Image.Image` objects. File loading is handled by the router (`textures.py`).

---

## Adding a New Service

1. Create `src/services/my_service.py`:
   ```python
   """Brief docstring."""
   from shared import bblogger

   flogger = bblogger.get_logger("blender-my-service")

   class MyService:
       def __init__(self) -> None:
           ...

       def do_something(self, ...) -> ...:
           ...
   ```

2. **Decide on lifecycle**:
   - **Per-request** (no shared state): instantiate in the router function that needs it.
   - **Shared state** (needs to live for app lifetime): add to `lifespan()` in `main.py`:
     ```python
     app.state.my_service = MyService()
     ```
     Access in routers via `request.app.state.my_service`.
   - **Stateless singleton**: instantiate at module level inside the router file.

3. Add tests in `tests/test_my_service.py`.

4. Run `ruff check src/services/my_service.py` to verify code style.

---

## Error Handling Conventions

- Services raise **typed, specific exceptions** (e.g., `RenderError`, `AEIConversionError`).
- Services do **not** raise `HTTPException` — that is the router's responsibility.
- Use `raise MyError("message") from original_exc` to preserve exception chains.
- Services log errors at `ERROR` level before raising. Routers log at the appropriate level when catching.

---

## Code Standards

- **Python**: 3.12+
- **Linter/formatter**: Ruff (`target-version = "py312"`, `line-length = 120`) — `pyproject.toml` at repo root
- **Type hints**: Required on all public methods
- **Docstrings**: Use Sphinx-style (`:param:`, `:return:`, `:raises:`) on public methods
- **Pydantic**: Use `ConfigDict(from_attributes=True)` and `.model_dump()` (not deprecated `.dict()`)
- **Max mocks per test**: 2 (project-wide standard)
- **Logging**: `bblogger.get_logger("blender-<service-name>")` — INFO for normal ops, ERROR for failures, DEBUG for diagnostics

---

*Last updated: 2026-03-16*
