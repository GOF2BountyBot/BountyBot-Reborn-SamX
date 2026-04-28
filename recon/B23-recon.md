# B.23 Recon — Bounty Expiry: Listing Stale Rows & Orphaned Announcement Messages

**Defect**: B.23  
**Recon date**: 2026-04-28  
**Investigator**: Developer (read-only)  
**Commit examined**: HEAD (`815cd59`)

---

## 1. Verified Code Paths

### 1.1 `/bounties` Router Path

**File**: `services/bot-core/src/api/routers/bounties.py` lines 241–253  
**Handler**: `GET /bounties/` → `list_bounties()`

```python
@router.get("/", response_model=list[BountyPublicResponse])
async def list_bounties(guild_id, division, service):
    async with get_db_session() as db:
        if division:
            bounties = await service.bounty_repo.get_active_by_guild_and_division(db, guild_id, division)
        else:
            bounties = await service.bounty_repo.get_active_by_guild(db, guild_id)
        return [BountyPublicResponse.model_validate(b) for b in bounties]
```

**Finding**: Both code paths — unfiltered `/bounties` and `/bounties division:bronze` — call the **B.14-fixed** repository methods. Both `get_active_by_guild()` (bounty_repository.py:76–102) and `get_active_by_guild_and_division()` (bounty_repository.py:104–125) include the `end_time > func.now()` clause. **The listing filter is present and correct in HEAD.**

### 1.2 "Expires N ago" Display — Client-Side Relative Rendering

**File**: `services/discord-gateway/src/cogs/bountyCog.py` lines 618–621  
**File**: `services/discord-gateway/src/utils/timestamp_utils.py` lines 6–25

```python
end_time = bounty.get("end_time")
if end_time:
    time_str = f" | Expires {iso_to_discord_ts(end_time, 'R')}"
```

`iso_to_discord_ts(end_time, 'R')` converts the ISO `end_time` string into a **Discord timestamp tag** `<t:UNIX_TS:R>`. The "R" style is **rendered by the Discord client** as a relative time string ("4 minutes ago", "in 2 minutes"). **This text is entirely client-side** — the cog only passes the raw UNIX timestamp; Discord renders it relative to when the user views the message.

**Critical implication**: If `list_bounties()` returns a bounty with a past `end_time`, Discord will correctly display "N minutes ago". This is real evidence that the endpoint **did return bounties with past `end_time` values**.

### 1.3 B.14 Filter Caveat — Database vs. Application-Server Clock

`func.now()` in the SQLAlchemy query resolves to the **PostgreSQL server clock** (i.e., `NOW()` at query execution time on the DB). The `end_time` stored in the bounty row was set at **spawn time** using `datetime.now(UTC)` inside `bounty_service.spawn_bounty()`, which runs on the **bot-core application server** clock.

If there is any clock skew between the PostgreSQL host and the bot-core container, a bounty whose `end_time` is very close to the database's `NOW()` could pass or fail the filter differently than expected. However, the observations showed bounties **3–4 minutes past** `end_time`, making sub-minute clock skew an insufficient sole explanation.

### 1.4 Expire Executor Scheduling Path

Expiry jobs are scheduled via `_schedule_expiry_job()` in `bounty_spawn_executor.py` (lines 715–753). This is called from two code paths:

| Caller | File:Lines | Notes |
|--------|-----------|-------|
| `execute_bounty_spawn_one_job` | `bounty_spawn_executor.py:458–463` | Non-fatal: exception caught+logged, spawn continues |
| `execute_bounty_spawn_job` (legacy/admin) | `bounty_spawn_executor.py:652` | **Not wrapped in try/except** — exception propagates up |
| `admin_spawn_bounties` router | `bounties.py:480–486` | Non-fatal: exception caught+logged |

In `_schedule_expiry_job()` (lines 715–753):
1. It checks `bounty.end_time is None` and returns early (logs warning).
2. Generates a random UUID as `expiry_job_id`.
3. **CRITICAL**: Posts `{"run_at": bounty.end_time.isoformat(), "payload": {...}}` to `POST /api/v1/jobs`.
4. Any HTTP exception is caught and logged (non-fatal) — **the spawn does not retry on scheduling failure**.

The `expiry_job_id` variable is logged in the success message, but the `body` sent to the scheduler does **not include a `job_id` field** — the scheduler generates its own ID. The logged `expiry_job_id` is never actually used as the scheduler job ID.

### 1.5 Expire Executor Flow

**File**: `services/bot-core/src/utils/executors/bounty_expire_executor.py`

```
execute_bounty_expire_job(job_id, payload):
  1. Extract bounty_id from payload
  2. open DB session
  3. bounty_obj = bounty_repo.get_by_id(db, bounty_id)       # fetches regardless of status
  4. bounty = bounty_service.expire_bounty(db, bounty_id)    # sets status='expired'; returns None if not active
  5. if bounty_obj is not None: _delete_bounty_announcement(job_id, bounty_obj, db)
  6. close DB session
```

`_delete_bounty_announcement()` (lines 104–145):
1. Looks up `DiscordMessage` by `(guild_id, "bounty_announcement", bounty.id)`.
2. If found: `DELETE /api/v1/channels/{channel_id}/messages/{message_id}` to gateway.
3. Deletes the `DiscordMessage` DB record.
4. Entire function is non-fatal (outer `try/except` swallows all errors and logs a warning).

**Confirmed**: If the expire executor fires, it **does** delete the Discord announcement (file: `bounty_expire_executor.py:129–136`).

### 1.6 APScheduler Persistence

**File**: `services/bot-core/src/main.py` lines 293–301

```python
sync_url = db_manager._connection_string.replace("postgresql+asyncpg", "postgresql")
sync_engine = create_engine(sync_url, ...)
jobstores = {"default": SQLAlchemyJobStore(engine=sync_engine, tablename="apscheduler_jobs")}
scheduler = AsyncIOScheduler(jobstores=jobstores, timezone="UTC")
```

**APScheduler uses a PostgreSQL-backed jobstore** (`apscheduler_jobs` table). One-time jobs ARE persisted to the DB and survive restarts **if** the restart happens before their `run_date`. 

However, **APScheduler's default behavior for past-due jobs**: when the scheduler starts, it processes jobs whose `run_date` is in the past. The default `misfire_grace_time` (when not explicitly set) causes APScheduler to skip jobs that fired more than a threshold time ago. In APScheduler 3.x, the default `misfire_grace_time` is **1 second** — meaning any one-time job whose `run_date` is > 1 second in the past when the scheduler starts will be **silently dropped** as a misfired job.

This is the key interaction with the startup recovery sweep: the sweep (in `run_stale_state_recovery_sweep()`, `main.py:95–159`) runs **before** `scheduler.start()` (line 303) and marks stale bounties as `status='expired'` in the DB. But the APScheduler jobs for those bounties (stored in `apscheduler_jobs`) are **not cleaned up by the sweep** — they will be picked up by APScheduler on startup and may or may not fire depending on misfire behavior.

### 1.7 Startup Recovery Sweep — What It Does and Doesn't Do

**File**: `services/bot-core/src/main.py:95–159` (`run_stale_state_recovery_sweep`)

The sweep executes:
```sql
UPDATE bounty SET status='expired'
WHERE status='active' AND end_time < NOW()
```

**It does NOT**:
- Delete Discord announcement messages for the marked-expired bounties
- Remove `DiscordMessage` DB records for those bounties
- Delete the corresponding APScheduler `bounty_expire` jobs from `apscheduler_jobs`

This means after a restart:
- DB bounties are correctly marked `status='expired'`
- Announcement messages remain visible in Discord channels (**zombie announcements**)
- `DiscordMessage` records remain in DB
- APScheduler expire jobs for those bounties remain in `apscheduler_jobs` and will be processed by the starting scheduler

When APScheduler processes those stale expire jobs at startup:
- The expire executor calls `expire_bounty()` which returns `None` (bounty already `status='expired'`)
- The executor STILL calls `_delete_bounty_announcement()` — this is the "always delete announcement" path
- If the gateway is up, the Discord message IS deleted; the DB record IS cleaned up

**So the sweep + APScheduler startup job processing combination DOES eventually delete announcements** — but only if the APScheduler jobs haven't exceeded the misfire grace window. If the bot was offline for a long time (e.g., hours), those one-time expire jobs fire well past their `run_date` and **may be dropped as misfires without the announcement deletion path running**.

---

## 2. Failure Mode Analysis

### Mode A: Expire Job Never Scheduled (Evidence-Supported)

`_schedule_expiry_job()` fires an HTTP POST to `POST /api/v1/jobs`. This call can fail if:
- Bot-core's own HTTP server hasn't fully started yet (race condition at spawn time)
- Network timeout (10s) exceeded
- Scheduler router unavailable (e.g., scheduler initialization failed)

The failure is **non-fatal and non-retried** in the new `execute_bounty_spawn_one_job` path (lines 458–463 in bounty_spawn_executor.py). The spawn succeeds, the announcement posts, but no expire job is ever registered. The bounty will then persist indefinitely until:
- The B.14 filter hides it from listings after `end_time` passes (listing fixed)
- The announcement persists until manual deletion or next restart + APScheduler processing
- **No automatic announcement cleanup occurs**

This is **the most likely failure mode for Oluchi Erland's bounty**: if the expiry job failed to schedule at spawn time (11:13 UTC), and no restart occurred before 14:08, the announcement would remain until either a restart or manual cleanup.

### Mode B: Expire Job Scheduled but Dropped as Misfire

If the bot restarted between spawn time and `end_time`, the APScheduler job survives in `apscheduler_jobs`. If the bot was offline at `end_time` and the restart occurs more than `misfire_grace_time` (default: 1 second) after `run_date`, APScheduler silently drops the job. The startup recovery sweep marks the bounty expired (DB-side) but does NOT delete the announcement.

**Evidence**: The 13:08 UTC sweep "marked 12 stale bounties" — these 12 announcements may have been left as zombies if the corresponding expire jobs were dropped as misfires.

### Mode C: Expire Job Scheduled but Executor Failed Silently

The executor wraps the entire execution body in `try/except Exception` (line 93) and **re-raises** on failure. APScheduler will mark the job as failed if an exception propagates out. This is a less likely "silent failure" because it DOES raise. However, if the gateway is unreachable (timeout after 10s), the HTTP call raises and the entire executor fails — the APScheduler job is then marked as failed/misfired. The announcement is not deleted.

### Mode D: B.14 Filter Paradox (The Listing Observation)

The listing observation (4 bounties showing "expires N ago" at 13:53 UTC) is **inconsistent with the B.14 filter being in effect**, because `get_active_by_guild()` includes `end_time > func.now()`. 

Two possible explanations:
1. **Clock skew**: bot-core app clock and PostgreSQL `NOW()` differ slightly. The bounties' `end_time` was set using the app server clock; the filter uses DB clock. Sub-minute skew would not explain 3–4 minute differences.
2. **Session-level time evaluation**: SQLAlchemy's `func.now()` in a query translates to PostgreSQL's `NOW()`, which is the **transaction start time** in PostgreSQL. In certain session isolation levels, `NOW()` could return a timestamp from earlier in the session. However, the `get_active_by_guild()` call opens a fresh session per request — this is unlikely to explain large discrepancies.
3. **DB timezone mismatch**: If PostgreSQL's timezone is not UTC and the comparison is not timezone-aware, the comparison `end_time > NOW()` could behave unexpectedly. The Bounty model stores `end_time` from `datetime.now(UTC)`. If the DB timezone is set differently, `NOW()` could return a local-time value that doesn't correctly compare against UTC-stored timestamps.

**Most plausible explanation for the listing issue**: at 13:53 UTC, the bounties (spawned at ~13:43 with 10-minute expiry) reached `end_time` at ~13:53. The listing happened within seconds of expiry. **The "expires N ago" could represent the Discord client rendering the timestamp at a later moment** than when the API call was made — i.e., the API response was correct at the time of the HTTP GET, but by the time Discord rendered the embed (seconds later), the timestamps had tipped past `end_time`. Discord timestamps are rendered client-side at display time, not at send time.

This is the most parsimonious explanation: the bounties were still technically active (within seconds of `end_time`) when the API responded; the Discord client rendered the relative timestamp at a slightly later moment when they had already expired. The B.14 filter **was working correctly** — the "expires N ago" is a display artifact of near-boundary timing, not a filter bypass.

---

## 3. Recovery Sweep Coverage

| Action | Startup Sweep (`run_stale_state_recovery_sweep`) | Notes |
|--------|--------------------------------------------------|-------|
| Mark stale bounties `status='expired'` in DB | ✅ Done | Bulk UPDATE |
| Delete Discord announcement messages | ❌ Not done | No gateway call |
| Delete `DiscordMessage` DB records | ❌ Not done | No cleanup |
| Remove stale APScheduler expire jobs | ❌ Not done | Jobs remain in `apscheduler_jobs` |

The sweep's gap means stale bounties from offline periods leave **zombie announcement messages** in Discord channels that can persist indefinitely (until the APScheduler jobs fire on startup, IF they haven't misfired). If the APScheduler jobs misfire (dropped as past-due), the announcements are orphaned permanently until a future `/admin_clear_bounties` or manual deletion.

---

## 4. APScheduler Misfire Mechanics

APScheduler 3.x `DateTrigger` (used for `run_at` one-time jobs): when the scheduler starts after being offline, it checks each pending job's `next_run_time`. If `now - next_run_time > misfire_grace_time`, the job is **skipped (misfired)**. The default `misfire_grace_time` for `AsyncIOScheduler` when not explicitly set is `None` (APScheduler 3.x actually defaults to `None`, meaning jobs are never dropped for being late — **they run immediately**).

**Correction**: In APScheduler 3.x, if `misfire_grace_time` is `None`, past-due jobs are run immediately. If set to a value, jobs past the grace window are dropped. The bot-core config does NOT set `misfire_grace_time` explicitly (main.py lines 300–301). With the default (`None`), past-due one-time expire jobs **will fire immediately on scheduler startup**.

This means Mode B above is less likely than initially thought. With default APScheduler settings and no `misfire_grace_time` configured, the stale expire jobs **should** fire at startup and delete the announcements. The actual failure is more likely Mode A (job never scheduled at all).

---

## 5. Summary: Most Probable Root Cause

The evidence points most strongly to **Mode A** as the primary failure for the Oluchi Erland announcement:

1. At spawn time (~11:13 UTC), `_schedule_expiry_job()` was called.
2. The HTTP POST to `POST /api/v1/jobs` either failed (timeout, transient error) or was silently swallowed.
3. No expire job was ever registered in `apscheduler_jobs`.
4. The bounty's `end_time` passed at ~11:23 UTC (10 minute expiry).
5. The B.14 filter correctly hid the bounty from subsequent listings (not returned in 14:08 listing).
6. The announcement remained in `#bronze-bounty-board` with no executor ever firing to delete it.
7. The startup sweep at 13:08 UTC marked the bounty as expired (DB-side) but did not clean up the announcement.

**For the listing observation** (4 bounties "expires N ago"): these were near-boundary timing artifacts — the bounties were within seconds of expiry when the API responded; Discord's client-side relative rendering made them appear past-due in the embed. The B.14 filter was working.

---

## 6. Open Questions (Unresolved Read-Only)

1. **`misfire_grace_time` behavior**: APScheduler's actual default for `AsyncIOScheduler` with SQLAlchemy jobstore when `misfire_grace_time` is not set — needs empirical verification. If it defaults to 1 second (some versions), Mode B is also a significant risk path.

2. **`_schedule_expiry_job` error at spawn time**: Can we confirm by log inspection whether the expiry job HTTP call failed for bounty IDs 2032–2038 or for the Oluchi Erland bounty? Logs for the 11:13 UTC spawn would show `BountySpawnOne[...] failed to schedule expiry` if this occurred.

3. **APScheduler job ID not passed**: `_schedule_expiry_job` generates `expiry_job_id = str(uuid.uuid4())` (line 727) but never includes it in the `body` sent to the scheduler (lines 734–737). The scheduler auto-assigns an ID. The logged `expiry_job_id` is fictional — it can never be used to look up or cancel the job. This is a latent operational issue but not the cause of this defect.

4. **`end_time` timezone**: The `BountyPublicResponse` schema returns `end_time: datetime | None`. When serialized by FastAPI/Pydantic, if the datetime has no timezone info (naive), the comparison with `func.now()` (PostgreSQL UTC) could behave unexpectedly. Needs verification of `Bounty.end_time` column type and stored values.

---

## 7. Recommended Fix Scope

**Surgical** — two targeted fixes:

### Fix A: Make `_schedule_expiry_job` failure observable and retriable
- Add a warning-level log with the full HTTP response body on failure (currently only logs the exception).
- Consider scheduling via the APScheduler Python API directly (not HTTP) to eliminate the inter-process HTTP dependency at spawn time. This would be the most reliable fix — scheduling happens atomically with the spawn.

### Fix B: Include announcement cleanup in startup recovery sweep
In `run_stale_state_recovery_sweep()`:
1. After marking bounties expired, collect their IDs.
2. For each ID, call `_delete_bounty_announcement()` (or the same gateway + DB cleanup logic used by `clear_bounties()`).
3. This ensures zombie announcements from offline-missed expiry jobs are cleaned up on next startup.

**Risk**: The gateway may not be ready at startup sweep time (sweep runs before scheduler start, gateway is a separate service). The announcement deletion is already non-fatal in other paths; the same pattern applies here.

**Recon completed**: 2026-04-28 by developer (read-only investigation)
