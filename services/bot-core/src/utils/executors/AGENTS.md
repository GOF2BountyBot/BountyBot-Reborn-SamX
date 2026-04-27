# AGENTS.md - utils/executors

APScheduler job executor modules for bot-core. All 7 executor functions live here.

---

## Executor Pattern

Each file in this directory exports a single top-level async function:

```python
async def execute_<job_type>_job(job_id: str, payload: dict) -> dict:
    ...
    return {"status": "success", ...}
```

### Entry Point

`utils/job_executor.py` dispatches to these functions via `JobExecutor.execute()`:

```python
async def execute(self, job_id: str, payload: dict):
    job_type = payload.get("job_type")
    if job_type == "bounty_spawn":
        return await execute_bounty_spawn_job(job_id, payload)
    elif job_type == "shop_refresh":
        return await execute_shop_refresh_job(job_id, payload)
    # ... etc.
```

`run_job(job_id, payload)` is the APScheduler-callable entry point (must be picklable).

---

## Deferred Imports

**All executors use deferred imports** — ORM-related imports (`db_manager`, repositories, services) are placed inside the function body, not at module level:

```python
async def execute_bounty_spawn_job(job_id: str, payload: dict) -> dict:
    # Deferred — avoids transitive ORM dependencies at module load time
    from persist.database.manager import db_manager
    from persist.repositories.bounty_repository import BountyRepository
    from services.bounty_service import BountyService
    ...
```

**Why**: This allows the executor module to be safely imported in test environments without a live database or all ORM dependencies being available at import time. It also avoids circular import issues.

---

## Service Instantiation in Executors

Executors create **fresh service and repository instances** inside `async with db_manager.get_session() as db:`:

```python
async with db_manager.get_session() as db:
    bounty_service = BountyService()
    bounty_repo = BountyRepository()
    config_repo = ConfigRepository()
    # ... use them
```

There are no long-lived service instances across job executions.

---

## HTTP Calls to Other Services

Executors that need to communicate with other services (discord-gateway, bot-core scheduler) use `httpx.AsyncClient`:

```python
async with httpx.AsyncClient() as client:
    resp = await client.post(
        f"{_GATEWAY_BASE_URL}/messages",
        json=announcement,
        timeout=10,
    )
resp.raise_for_status()
```

**Service endpoint configuration** (via env vars):

| Variable | Default | Used by |
|---|---|---|
| `EXECUTOR_HOST` | `bot-core` | Scheduler API calls (self-referencing) |
| `EXECUTOR_PORT` | `8000` | Scheduler API calls |
| `DISCORD_GATEWAY_HOST` | `discord-gateway` | Announcement calls to gateway |
| `GATEWAY_PORT` | `7999` | Announcement calls to gateway |

**All HTTP failures are non-fatal** — errors are logged but do NOT propagate to prevent cascading failure of the spawn operation.

---

## All 7 Executors

### bounty_spawn_executor.py

**Function**: `execute_bounty_spawn_job(job_id, payload)`  
**Triggered by**: `bounty_spawn_default` (every N minutes, default 5) or on-demand  
**Payload fields**:
- `guild_id` (optional) — process only this guild; omit for bulk (all guilds)
- `division` (optional) — process only this division; omit for all three
- `temperature` (optional, default 5.0) — activity temperature to compute max_bounties

**Flow**:
1. Compute `max_bounties = TemperatureService.get_max_bounties(temperature)`
2. For each guild config (or just `guild_id` if provided):
3. For each division (`Bronze`, `Silver`, `Gold`):
4. Count active bounties via `BountyRepository.get_active_by_guild_and_division()`
5. If slot available: call `BountyService.spawn_bounty(db, guild_id, division)`
6. Schedule expiry job via `POST /api/v1/jobs` (one-time at `bounty.end_time`)
7. Announce via `POST {GATEWAY_BASE_URL}/announcements/bounty/channel/{cid}` (non-fatal if fails)

**Returns**: `{"status": "success", "guilds_processed": N, "total_spawned": M, "results": {...}}`

**A.48 announcement payload (post-2026-04-27)**: `_announce_bounty()` builds the request via `utils.bounty_announcement_payload.build_bounty_announcement_request(db, bounty, criminal_icon=..., route_map_url=..., bounty_hunter_role_id=..., captured=False)`. The body is a structured dict (`text_content` + `loadout_response` + `metadata`); the gateway renders the final embed using `cogs/_shared/loadout_embed.build_loadout_embed`. The old per-channel `/channels/{cid}/messages` POST and the pre-rendered `BountyAnnouncementBuilder` were removed. Edit-on-capture still flows through `BountyService._edit_bounty_announcement` and posts to the gateway's PUT counterpart at `/announcements/bounty/channel/{cid}/message/{mid}`.

---

### bounty_expire_executor.py

**Function**: `execute_bounty_expire_job(job_id, payload)`  
**Triggered by**: One-time job scheduled by `bounty_spawn_executor` at `bounty.end_time`  
**Payload fields**:
- `bounty_id` — the bounty to expire
- `guild_id` — guild context
- `division` — division context

**Flow**:
1. Fetch bounty by ID
2. If still active: call `BountyService.expire_bounty(db, bounty_id)`
3. Announce expiry to discord-gateway (non-fatal)

---

### bounty_respawn_executor.py

**Function**: `execute_bounty_respawn_job(job_id, payload)`  
**Triggered by**: One-time job scheduled when a bounty is marked `escaped` (see `BountyService.escape_bounty()` which computes `respawn_time`; the scheduling call site is currently in the gateway / admin flow, not bot-core itself).  
**Payload fields**:
- `bounty_id` (int, required) — the escaped bounty's ID. This is the ONLY field the executor reads.

**Flow**:
1. Extracts `bounty_id` from payload (returns `{"status": "error"}` if missing).
2. Calls `BountyService.respawn_bounty(db, bounty_id)` — regenerates the A* route and answer while keeping the same criminal, resets status to `active`.
3. Returns `{"status": "skipped"}` if `respawn_bounty()` returns `None` (bounty not found, wrong status, or route-generation failure).
4. Announces the respawn to discord-gateway via `POST {GATEWAY_BASE_URL}/messages` (non-fatal if it fails).

**Payload-shape contract consumed by A.11 cleanup**: `BountyService.clear_bounties()` filters scheduled jobs by `payload["job_type"] == "bounty_respawn"` AND `payload["bounty_id"]` ∈ `{cleared_bounty_ids}`. Any future scheduler call site that writes a `bounty_respawn` job MUST include `bounty_id` in the payload to remain discoverable by that cleanup pass.

---

### duel_expire_executor.py

**Function**: `execute_duel_expire_job(job_id, payload)`  
**Triggered by**: Periodic job or one-time at duel `expires_at`  
**Payload fields**: optional `guild_id` filter

**Flow**:
1. Calls `DuelService.expire_duels(db)` (or `expire_duels_by_guild(db, guild_id)`)
2. Marks all pending duels past `expires_at` as `"expired"`
3. Refunds any locked credits

---

### shop_refresh_executor.py

**Function**: `execute_shop_refresh_job(job_id, payload)`  
**Triggered by**: `shop_refresh_default` (every 6 hours)  
**Payload fields**: optional `guild_id` filter

**Flow**:
1. If `guild_id` provided: refresh that guild only
2. Otherwise: enumerate all guild configs via `ConfigRepository.list_all()`
3. For each guild: call `ShopService.refresh_shop(db, guild_id)`
4. Announce refresh to discord-gateway (non-fatal)

---

### temperature_decay_executor.py

**Function**: `execute_temperature_decay_job(job_id, payload)`  
**Triggered by**: `temperature_decay_default` (every 1 hour)  
**Payload fields**: none required

**Flow**:
1. For each guild config: fetch current temperature (activity level)
2. Apply `TemperatureService.apply_decay(current_temp, decay_rate)`
3. Clamp to `MIN_GUILD_ACTIVITY`
4. Persist updated temperature to guild config

---

### time_announcement_executor.py

**Function**: `execute_time_announcement_job(job_id, payload)`  
**Triggered by**: On-demand (scheduled dynamically)  
**Payload fields**: `guild_id`, `announcement_type`, message content fields

**Flow**:
1. Uses `MessageBuilderFactory.create_builder("time_announcement")` to build the embed payload
2. POSTs the announcement to `{GATEWAY_BASE_URL}/messages`
3. Response is logged; failure is non-fatal

---

## How to Add a New Executor

1. **Create the file** `utils/executors/<job_type>_executor.py`:

   ```python
   """My new executor — brief description."""

   from shared.bblogger import get_logger

   flogger = get_logger("my-job-executor")

   async def execute_my_job(job_id: str, payload: dict) -> dict:
       # Deferred imports
       from persist.database.manager import db_manager
       from services.my_service import MyService

       flogger.info(f"MyJob[{job_id}] START")

       async with db_manager.get_session() as db:
           service = MyService()
           result = await service.do_work(db, payload)

       flogger.info(f"MyJob[{job_id}] done")
       return {"status": "success", "result": result}
   ```

2. **Register in `job_executor.py`**:
   ```python
   from utils.executors.my_executor import execute_my_job

   # In JobExecutor.execute():
   if payload.get("job_type") == "my_job":
       return await execute_my_job(job_id, payload)
   ```

3. **Schedule the job** — either via `register_default_jobs()` in `main.py` for recurring jobs, or via `POST /api/v1/jobs` for one-time jobs.

4. **Add tests** in `tests/test_<job_type>_executor.py`.

---

## Testing Executors

Executor tests mock the database session and services:

```python
@pytest.mark.asyncio
async def test_execute_bounty_spawn_job():
    with patch("utils.executors.bounty_spawn_executor.db_manager") as mock_db:
        mock_session = AsyncMock()
        mock_db.get_session.return_value.__aenter__.return_value = mock_session
        # ... set up mocks for repositories and services
        result = await execute_bounty_spawn_job("test-job", {"job_type": "bounty_spawn"})
        assert result["status"] == "success"
```

The deferred import pattern means patches must target the executor module's namespace (e.g., `utils.executors.bounty_spawn_executor.db_manager`), not the original module.

---

*Last updated: 2026-03-16*
