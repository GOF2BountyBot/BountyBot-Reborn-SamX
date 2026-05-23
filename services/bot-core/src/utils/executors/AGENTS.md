# AGENTS.md - utils/executors

APScheduler job executor modules for bot-core. All 8 executor functions live here.

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
    if job_type == "bounty_spawn_orchestrate":
        return await execute_bounty_spawn_orchestrate_job(job_id, payload)
    elif job_type == "bounty_spawn_one":
        return await execute_bounty_spawn_one_job(job_id, payload)
    elif job_type == "shop_refresh":
        return await execute_shop_refresh_job(job_id, payload)
    # ... etc.
```

`run_job(job_id, payload)` is the APScheduler-callable entry point (must be picklable).

---

## Deferred Imports

**All executors use deferred imports** — ORM-related imports (`db_manager`, repositories, services) are placed inside the function body, not at module level:

```python
async def execute_bounty_expire_job(job_id: str, payload: dict) -> dict:
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

## Gateway Push Contract

`shop_refresh_executor`, `bounty_spawn_executor`, and `bounty_expire_executor` all POST
cache-update payloads to the gateway after each relevant mutation:

- `POST /api/v1/internal/autocomplete/shop-cache/{guild_id}/{tier}` — after shop refresh
- `POST /api/v1/internal/autocomplete/bounty-cache/{guild_id}` — after spawn/expire

All pushes are non-fatal (try/except + warning). The gateway's 6-minute periodic refresh
covers any missed pushes. Requires `INTERNAL_AUTH_TOKEN` env var in both services.

---

## All 8 Executors

### bounty_spawn_executor.py

Two entry points (both dispatched from `job_executor.py`):

**`execute_bounty_spawn_orchestrate_job(job_id, payload)`** — Orchestrator  
**Triggered by**: `bounty_spawn_default` cron (every N minutes, default 5)  
**Payload fields**: none required  
**Flow**:
1. For each guild config × division: count active bounties + queued spawn jobs
2. If slot available: schedule a randomised one-time `bounty_spawn_one` job with ±25% window
3. Returns job counts queued per guild

**`execute_bounty_spawn_one_job(job_id, payload)`** — One-shot spawner  
**Triggered by**: one-time APScheduler job created by the orchestrator  
**Payload fields**:
- `guild_id` — guild to spawn for
- `division` — tier division (`bronze`, `silver`, `gold`, `platinum`)
- `expiry_minutes` — bounty lifetime in minutes

**Flow**:
1. Re-checks capacity (handles benign race with concurrent orchestrator ticks)
2. Calls `BountyService.spawn_bounty(db, guild_id, division, expiry_minutes)`
3. Schedules expiry job via `POST /api/v1/jobs` (one-time at `bounty.end_time`)
4. Pushes bounty cache to gateway (`POST /internal/autocomplete/bounty-cache/{guild_id}`)
5. Announces via `POST {GATEWAY_BASE_URL}/announcements/bounty/channel/{cid}` (non-fatal)

**Returns**: `{"status": "success", "bounty_id": N, ...}`

**A.48 announcement payload**: `_announce_bounty()` builds the request via `utils.bounty_announcement_payload.build_bounty_announcement_request(db, bounty, criminal_icon=..., route_map_url=..., bounty_hunter_role_id=..., captured=False)`. The body is a structured dict (`text_content` + `loadout_response` + `metadata`); the gateway renders the final embed using `cogs/_shared/loadout_embed.build_loadout_embed`. Edit-on-capture flows through `BountyService._edit_bounty_announcement` → gateway PUT at `/announcements/bounty/channel/{cid}/message/{mid}`.

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

### bounty_failsafe_cleanup_executor.py

**Function**: `execute_bounty_failsafe_cleanup_job(job_id, payload)`  
**Triggered by**: `bounty_failsafe_cleanup_default` (every hour at :30 — offset from temperature_decay at :00)  
**Payload fields**:
- `guild_id` (int, optional) — restrict sweep to one guild; omit for all guilds

**Problem it solves (B.83)**:  
The primary cleanup path (`bounty_expire_executor`) fires a one-time job at `bounty.end_time`. When that job is silently dropped (gateway timeout, APScheduler restart, transient DB error), the Discord post stays visible in the channel permanently even though the bounty has expired or been captured.

**Strategy (Discord-first)**:
1. For each guild config, iterate all configured bounty channel IDs (one per division).
2. Fetch the most recent messages from each channel via the gateway `GET /channels/{cid}/messages?limit=100`.
3. For every message ID returned, look it up in the `discord_message` table (`message_type = "bounty_announcement"`).
4. Fetch the referenced bounty from the DB and classify it:
   - **active + end_time > now** → legitimately live, leave it alone
   - **active + end_time ≤ now** → stale active (expire job was lost); set `status = "expired"`, then delete post
   - **non-active status** (expired, captured, cleared, …) → delete post
   - **bounty row missing / null reference_id** → orphan announcement; delete post
5. For all non-live cases: DELETE the Discord post via gateway + remove the `DiscordMessage` DB record.

**Non-fatal design**: failures in any individual guild, channel, or message are logged and skipped; they never abort the sweep for remaining items.

**Returns**: `{"status": "success", "guilds_processed": N, "total_messages_inspected": M, "total_cleaned": K, "total_errors": E, "results": {...}}`

---

### db_retention_executor.py

**Function**: `execute_db_retention_job(job_id, payload)`
**Triggered by**: `db_retention_default` (daily at 03:45 UTC — well clear of all hourly / 3-hourly jobs)
**Payload fields**: none required (reserved for future per-call overrides)

**Purpose**: Bounded growth of high-churn tables whose terminal-state rows have
no game-relevant value once per-player aggregate stats have been written to the
`players` table.

**Three independent passes** (each in its own DB session — one failure does not
abort the others):

1. `bounty` — delete rows where `status IN ('completed','expired','cleared')`
   AND `updated_at < now() - BOUNTY_RETENTION_HOURS` (default 24h).
   *Uses `updated_at` so freshly-transitioned rows are not insta-purged.*
   *'escaped' status is intentionally excluded — escaped bounties may respawn.*
2. `duel_requests` — delete rows where
   `status IN ('completed','expired','cancelled','rejected','declined')`
   AND `created_at < now() - DUEL_RETENTION_HOURS` (default 24h).
3. `admin_audit_logs` — delete rows where
   `timestamp < now() - AUDIT_RETENTION_DAYS` (default 30d).
   *Audit history is preserved out-of-band by `pg_backup_default`.*

**Per-player aggregates kept intact**: `players.bounty_wins`,
`players.systems_checked`, `players.lifetime_credits`, `players.duel_wins`,
`players.duel_losses`, `players.duel_credits_won`, `players.duel_credits_lost`.

**Overrides**: `BOUNTYBOT_BOUNTY_RETENTION_HOURS`, `BOUNTYBOT_DUEL_RETENTION_HOURS`,
`BOUNTYBOT_AUDIT_RETENTION_DAYS` (all integers; processed in `GameConstants.load()`).

**Non-fatal design**: each pass is wrapped in `try/except`; failures are logged
at WARNING with `type(e).__name__` for diagnosability and added to
`result["errors"]`. The executor always returns `{"status": "success", ...}` so
APScheduler does not retry.

**Returns**: `{"status": "success", "bounties_deleted": N, "duels_deleted": M, "audit_logs_deleted": K, "errors": [...]}`

**Repositories used**: `BountyRepository.delete_terminal_older_than`,
`DuelRepository.delete_terminal_older_than`, `AdminAuditLogRepository.delete_older_than`.
The audit log repository was added in this work as a minimal stub (count +
delete-older-than only); writes still go through `AuditService.log_action`.

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

*Last updated: 2026-05-16*
