# AGENTS.md - utils/executors

APScheduler job executor modules for bot-core. All 10 executor modules (11 entry-point functions — `bounty_spawn_executor.py` exports two) live here.

---

## Executor Pattern

Each file in this directory exports a single top-level async function:

```python
async def execute_<job_type>_job(job_id: str, payload: dict) -> dict:
    ...
    return {"status": "success", ...}
```

### Entry Point

`utils/job_executor.py` dispatches to these functions via `JobExecutor.execute()` on `payload["job_type"]`. The full dispatch set: `shop_refresh`, `bounty_spawn_orchestrate`, `bounty_spawn_one`, `bounty_expire`, `bounty_respawn`, `bounty_failsafe_cleanup`, `duel_expire`, `temperature_decay`, `pg_backup`, `db_retention`.

```python
async def execute(self, job_id: str, payload: dict):
    if payload.get("job_type") == "bounty_spawn_orchestrate":
        return await execute_bounty_spawn_orchestrate_job(job_id, payload)
    if payload.get("job_type") == "bounty_spawn_one":
        return await execute_bounty_spawn_one_job(job_id, payload)
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
| `EXECUTOR_HOST` | `bot-core` | `bounty_spawn_executor` HTTP-fallback scheduling and `BountyService.clear_bounties` A.11 cleanup (`/api/v1/jobs`) |
| `EXECUTOR_PORT` | `8000` | Same as above |
| `DISCORD_GATEWAY_HOST` | `discord-gateway` | Announcement + cache calls to gateway (`/api/v1/...`) |
| `GATEWAY_PORT` | `7999` | Announcement + cache calls to gateway |

**Scheduling is direct-first (P6-T8 / B.23a):** executors that schedule follow-up jobs (orchestrator → `bounty_spawn_one`; spawn → `bounty_expire`) call `utils.scheduler_holder.get_scheduler().add_job(run_job, trigger="date", ...)` in-process. The expiry path falls back to `POST {EXECUTOR}/api/v1/jobs` only when the scheduler holder is unavailable.

**All HTTP failures are non-fatal** — errors are logged but do NOT propagate to prevent cascading failure of the spawn operation.

---

## Gateway Push Contract

`shop_refresh_executor`, `bounty_spawn_executor`, `bounty_expire_executor`, and
`duel_expire_executor` all POST cache-update payloads to the gateway after each
relevant mutation:

- `POST /api/v1/internal/autocomplete/shop-cache/{guild_id}/{tier}` — after shop refresh
- `POST /api/v1/internal/autocomplete/bounty-cache/{guild_id}` — after spawn/expire
- `POST /api/v1/internal/autocomplete/duel-cache` — after duel expiry

All pushes are non-fatal (try/except + warning). The gateway's 6-minute periodic refresh
covers any missed pushes. Requires `INTERNAL_AUTH_TOKEN` env var in both services.

---

## All 10 Executors

### bounty_spawn_executor.py

Two entry points (both dispatched from `job_executor.py`):

**`execute_bounty_spawn_orchestrate_job(job_id, payload)`** — Orchestrator  
**Triggered by**: `bounty_spawn_default` cron (`*/{BOUNTY_DELAY_RANDOM_MIN} * * * *`, default every 5 minutes)  
**Payload fields**: none required  
**Flow**:
1. For each guild config × tier: skip guilds failing `_is_guild_fully_configured`; count active bounties (`count_active_by_guild_and_division`) + already-queued `bounty_spawn_one` jobs (read via `scheduler_holder.get_scheduler().get_jobs()`)
2. If slot available: compute a gap-aware fire time (`_compute_next_fire_time`: ideal spacing from existing issue times, clamped to `now + 5s` lead, plus bounded jitter `±min(15, 0.25 × interval)` minutes) and schedule a one-time `bounty_spawn_one` job **directly on the in-process scheduler** (P6-T8 — no HTTP loopback)
3. Returns job counts queued per guild/tier

**`execute_bounty_spawn_one_job(job_id, payload)`** — One-shot spawner  
**Triggered by**: one-time APScheduler job created by the orchestrator  
**Payload fields**:
- `guild_id` — guild to spawn for
- `tier` — tier (`bronze`, `silver`, `gold`, `platinum`); missing `guild_id`/`tier` → `{"success": False, "reason": "missing_payload"}`

**Flow**:
1. Re-checks guild/tier configuration and capacity (handles benign race with concurrent orchestrator ticks)
2. Calls `BountyService.spawn_bounty(db, guild_id, tier, expiry_minutes=expiry_minutes)` (expiry from `config.bounty_expiry_minutes`, fallback 480)
3. Schedules the expiry job at `bounty.end_time` — direct in-process scheduler first (B.23a), `POST {EXECUTOR}/api/v1/jobs` fallback
4. Pushes bounty cache to gateway (`POST /internal/autocomplete/bounty-cache/{guild_id}`)
5. Announces via `POST {GATEWAY_BASE_URL}/announcements/bounty/channel/{cid}` (non-fatal)

**Returns**: `{"status": "success", "bounty_id": N, ...}`

Pure helpers (Tier-A-testable, no DB): `_is_guild_fully_configured`, `_get_division_channel_id`, `_get_division_role_id`.

**A.48 announcement payload**: `_announce_bounty()` builds the request via `utils.bounty_announcement_payload.build_bounty_announcement_request(db, bounty, criminal_icon=..., route_map_url=..., bounty_hunter_role_id=..., captured=False)`. The body is a structured dict (`text_content` + `loadout_response` + `metadata`); the gateway renders the final embed using `cogs/_shared/loadout_embed.build_loadout_embed`. Edit-on-capture flows through `BountyService._edit_bounty_announcement` → gateway PUT at `/announcements/bounty/channel/{cid}/message/{mid}`.

---

### bounty_expire_executor.py

**Function**: `execute_bounty_expire_job(job_id, payload)`  
**Triggered by**: One-time job scheduled by `bounty_spawn_executor` at `bounty.end_time`  
**Payload fields**:
- `bounty_id` — the bounty to expire (the only field the executor reads; the scheduling site also writes `guild_id` + `division` for A.11 cleanup discoverability)

**Flow** (session released before network I/O — P6-T7):
1. Session A: fetch bounty by ID (regardless of status), call `BountyService.expire_bounty(db, bounty_id)` (internal guard skips already-captured/completed bounties), commit, close
2. ALWAYS delete the Discord announcement via gateway (`_delete_bounty_announcement`, non-fatal) — even if the bounty was already captured
3. Push updated bounty cache to gateway (`_push_bounty_cache_expire`, non-fatal)

---

### bounty_respawn_executor.py

**Function**: `execute_bounty_respawn_job(job_id, payload)`  
**Triggered by**: a one-time `bounty_respawn` job, or directly by the startup recovery sweep `run_stale_respawn_recovery()` in `main.py` (re-fires missed respawns for `status='escaped'` bounties whose `respawn_time` has passed). `BountyService.escape_bounty()` computes `respawn_time`, but no live in-repo call site currently schedules the one-shot job — the recovery sweep is the only in-repo invoker (verified 2026-06-11).  
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
**Triggered by**: One-time job scheduled at the duel's timeout  
**Payload fields**: `duel_id` — the single duel to expire

**Flow**:
1. Calls `DuelService.expire_duel(db, duel_id)` to set that duel's status to `expired` (a `ValueError` for already-resolved duels is handled as a no-op)
2. Posts an expiry notification to the gateway `POST {GATEWAY_BASE_URL}/messages` (non-fatal)
3. Pushes the duel autocomplete cache (`POST /internal/autocomplete/duel-cache`, non-fatal)

No credits move on expiry — pending stakes are an implicit reservation, not a transfer (see `services/AGENTS.md` duel invariant).

---

### shop_refresh_executor.py

**Function**: `execute_shop_refresh_job(job_id, payload)`  
**Triggered by**: `shop_refresh_default` (cron `0 */6 * * *` — every 6 hours)  
**Payload fields**: optional `guild_id`, `tier`, `force_tech_level`

**Flow**:
1. If `guild_id` provided: refresh that guild only
2. Otherwise: enumerate all guild configs via `ConfigRepository.list_all()`
3. For each guild × tier: call `ShopService.refresh_shop(db, guild_id, tier)`
4. Post one announcement per refreshed tier to `POST {GATEWAY_BASE_URL}/channels/{shop_channel_id}/messages` (logic in `utils.shop_announcement.announce_shop_refresh`, wrapped by `_announce_shop_refresh`; non-fatal)
5. Push the refreshed stock to the gateway autocomplete cache (Phase 5b, non-fatal)

---

### temperature_decay_executor.py

**Function**: `execute_temperature_decay_job(job_id, payload)`  
**Triggered by**: `temperature_decay_default` (cron `0 * * * *` — hourly at :00)  
**Payload fields**: none required

**Flow**:
1. Enumerate all guild configs via `ConfigRepository.list_all()`
2. For each guild, read per-division temperatures from `GuildConfig.division_temperatures` (divisions hardcoded in the module since B.48)
3. Apply `TemperatureService.decay_temperature()` to each division's value (×2/3, floored at 1.0, one decimal place)
4. Persist via `ConfigRepository.update_division_temperatures()`
5. Return a per-guild decay summary dict

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

**Four independent passes** (each in its own DB session — one failure does not
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
4. `combat_log` — delete rows where
   `created_at < now() - COMBAT_LOG_RETENTION_HOURS` (default 72h).

**Per-player aggregates kept intact**: `players.bounty_wins`,
`players.systems_checked`, `players.lifetime_credits`, `players.duel_wins`,
`players.duel_losses`, `players.duel_credits_won`, `players.duel_credits_lost`.

**Overrides**: `BOUNTYBOT_BOUNTY_RETENTION_HOURS`, `BOUNTYBOT_DUEL_RETENTION_HOURS`,
`BOUNTYBOT_AUDIT_RETENTION_DAYS`, `BOUNTYBOT_COMBAT_LOG_RETENTION_HOURS`
(all integers; processed in `GameConstants.load()`).

**Non-fatal design**: each pass is wrapped in `try/except`; failures are logged
at WARNING with `type(e).__name__` for diagnosability and added to
`result["errors"]`. The executor always returns `{"status": "success", ...}` so
APScheduler does not retry.

**Returns**: `{"status": "success", "bounties_deleted": N, "duels_deleted": M, "audit_logs_deleted": K, "combat_logs_deleted": L, "errors": [...]}`

**Repositories used**: `BountyRepository.delete_terminal_older_than`,
`DuelRepository.delete_terminal_older_than`, `AdminAuditLogRepository.delete_older_than`,
`CombatLogRepository.delete_older_than`.
The audit log repository is a minimal stub (count + delete-older-than only);
writes still go through `AuditService.log_action`.

---

### pg_backup_executor.py

**Function**: `execute_pg_backup_job(job_id, payload)`  
**Triggered by**: `pg_backup_default` (cron `15 */3 * * *` — :15 past every 3rd hour, offset from shop_refresh at :00 and failsafe cleanup at :30)  
**Payload fields**: none required

**Purpose**: `pg_dump | zstd -10` of the game database to a date-partitioned
path under the bot-core data volume:
`/app/data/backups/YYYY-MM-DD/bountydb_HH-MM-SS.sql.zst`.

**Safety guarantees**:
- Writes to a temp file (`<target>.tmp.PID`) then atomically renames.
- Skips the rename if the dump is smaller than 250 KiB (corruption guard).
- Backup directories older than `BACKUP_RETAIN_DAYS` (default 7) are removed after each successful dump.
- Errors are logged and **re-raised** (unlike the other executors) so APScheduler records the failure.

**Env vars**: `POSTGRES_HOST` (default `bounty_db`), `POSTGRES_PORT`, `POSTGRES_DB`,
`POSTGRES_USER`, `POSTGRES_PASSWORD`, `BACKUP_DIR`, `BACKUP_RETAIN_DAYS`.

**No ORM**: calls the `pg_dump` binary via `subprocess` (off-loaded with
`utils.offload.offload_io`) — never opens a SQLAlchemy session, so it has no
deferred-import section.

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

3. **Schedule the job** — via `register_default_jobs()` in `main.py` for recurring jobs; for one-time jobs prefer a direct in-process `scheduler_holder.get_scheduler().add_job(run_job, trigger="date", ...)` (P6-T8), or `POST /api/v1/jobs` from outside the process.

4. **Add tests** in `tests/test_<job_type>_executor.py`.

---

## Testing Executors

Follow the S2/S3 three-tier pattern in `tests/AGENTS.md` ("Executor Test Pattern"):
real SQLite sessions instead of repository mocks, `respx` for HTTP boundaries,
and zero-mock tests for pure helpers. The canonical reference suite is
`tests/test_bounty_spawn_executor.py`.

**Patch target — deferred imports**: because executors bind `db_manager` inside
the function body, the name lives in the SOURCE module. Patch
`persist.database.manager.db_manager` — patching
`utils.executors.<name>.db_manager` fails with `AttributeError` (the executor
module never binds that name at module scope):

```python
with patch("persist.database.manager.db_manager", fake_db_manager):
    result = await execute_bounty_spawn_one_job("job-id", payload)
```

---

*Last updated: 2026-07-22 — doc-vs-code reconciliation: 9 executors after time_announcement prototype removal (pg_backup added, db_retention 4th combat_log pass); direct in-process scheduling (P6-T8/B.23a) documented; spawn-one payload corrected to guild_id/tier; duel_expire corrected to single-duel expire_duel; temperature decay corrected to per-division ×2/3 model; duel-cache push added; testing section aligned with tests/AGENTS.md (patch source module, not executor namespace).*
