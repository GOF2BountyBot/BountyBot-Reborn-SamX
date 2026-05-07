# DEFECTS_TEST_REVAMP.md

Suspected production-code issues discovered while reading source files during
the Test Quality Blitz. **None of these have been fixed** — production code
is assumed Working As Intended (WAI). Logged here for triage by the
appropriate engineering owner.

Format:
- **Severity**: low | medium | high
- **File**: source file path with line range
- **Discovered during**: which sprint
- **Fix policy**: do NOT fix as part of the test revamp

---

## S2 — Sprint 2 (Test Quality Blitz, 2026-05-07)

### S2-OBS-01 — `next_spawn_check_at` is dead code in `execute_bounty_spawn_orchestrate_job`

- **Severity**: low (cosmetic / lint-grade)
- **File**: `services/bot-core/src/utils/executors/bounty_spawn_executor.py`,
  lines 199-201 (orchestrator) and lines 576, 594-599, 670-684 (legacy
  `execute_bounty_spawn_job`).
- **Observation**: The orchestrator's docstring (line 150-153) and inline
  comment explicitly state the `next_spawn_check_at` gate was removed per
  architect recommendation C1, yet the legacy `execute_bounty_spawn_job`
  still reads, gates on, and writes the column (lines 576, 594, 670-684).
  Both paths are reachable: `execute_bounty_spawn_job` is dispatched for
  the `bounty_spawn` job_type, which is still triggered by the admin spawn
  endpoint per `job_executor.py` line 62-64.
- **Risk**: Two divergent spawn paths with different gating semantics.
  Admin-triggered spawns observe `next_spawn_check_at`; default scheduled
  spawns ignore it. Could surface as "admin spawn does nothing" if the
  field happened to be populated.
- **Recommended fix policy**: Out of scope for test revamp. File a follow-up
  ticket to either (a) remove the gate from the legacy path for consistency
  with the orchestrator, or (b) document the divergence explicitly.
- **Fix authority**: production code owner, NOT the test team.

### S2-OBS-02 — `execute_bounty_spawn_job` (legacy) has unused `temperature` payload field

- **Severity**: low
- **File**: `services/bot-core/src/utils/executors/bounty_spawn_executor.py`,
  line 516.
- **Observation**: `_payload_temperature` is read from the payload (with a
  default of 5.0) but never used; the prefixed `_` variable name signals the
  author knew this. The function's docstring (lines 492-495) still claims
  the field is consumed by `TemperatureService.get_max_bounties()`, but
  the body actually reads `division_temperatures` from the guild config
  instead (line 612).
- **Risk**: Stale docstring; could confuse callers writing payloads that
  expect `temperature` to be honoured. No behavioural impact.
- **Recommended fix policy**: Doc-only. Update docstring during normal
  maintenance; not a test revamp concern.

### S2-OBS-03 — `_announce_bounty` swallows criminal-icon lookup errors silently at DEBUG

- **Severity**: low
- **File**: `services/bot-core/src/utils/executors/bounty_spawn_executor.py`,
  lines 882-893.
- **Observation**: The criminal-icon lookup (`criminal_repo.get_by_name`) is
  wrapped in a broad `try/except` whose handler logs at DEBUG level only.
  If the criminal lookup ever started failing systematically (e.g. due to a
  collation mismatch on `criminal.name`), the announcements would silently
  ship without icons and operators would have no visible warning.
- **Risk**: Operational visibility. The other non-fatal failures in the
  same function log at WARNING (e.g. line 872, 926); icon failure should
  probably match.
- **Recommended fix policy**: Out of scope. Suggest WARNING-level escalation
  in a future maintenance pass.

### S2-OBS-04 — Race window between capacity check and spawn in `execute_bounty_spawn_one_job`

- **Severity**: medium (acknowledged design trade-off, not a regression)
- **File**: `services/bot-core/src/utils/executors/bounty_spawn_executor.py`,
  lines 423-435.
- **Observation**: The "benign race" comment (line 430) is accurate but the
  re-check itself is not transaction-locked — two concurrent
  `bounty_spawn_one` jobs for the same `(guild, tier)` could both observe
  `active_count < max_for_tier` and both spawn, exceeding the cap by 1.
  The orchestrator's `count_active + queued_count >= max_for_tier` check
  (line 241) tries to prevent this by counting queued jobs, but the
  queued-count read and the schedule-job HTTP POST (lines 280-285) are
  also not atomic. APScheduler's job-store INSERT is the only true
  serialisation point.
- **Risk**: Under high concurrent load (e.g. multi-orchestrator restart),
  guilds could occasionally see `max_for_tier + 1` active bounties. The
  expiry job catches it eventually, but it is a real correctness gap.
- **Recommended fix policy**: Out of scope for test revamp. Architectural
  ticket to wrap the count-and-spawn in `SELECT ... FOR UPDATE` on a
  guild-tier rate-limit row, or to make the spawn idempotent against the
  cap.

### S2-OBS-05 — `text("SELECT COUNT(*) FROM apscheduler_jobs WHERE id LIKE :pattern")` couples executor to APScheduler internals

- **Severity**: low (architectural)
- **File**: `services/bot-core/src/utils/executors/bounty_spawn_executor.py`,
  lines 232-239.
- **Observation**: The orchestrator queries the `apscheduler_jobs` table
  directly via raw SQL. APScheduler's table schema is not part of its
  public API; an APScheduler version bump that renames the column or
  removes the `id LIKE` index would silently break this query. The
  router-based `GET /api/v1/jobs` endpoint already exposes a list of
  scheduled jobs (used elsewhere in the codebase, e.g.
  `bounty_service.clear_bounties` lines 770-772) and could substitute.
- **Risk**: Future APScheduler upgrade hazard.
- **Recommended fix policy**: Out of scope. Architecture follow-up.

### S2-OBS-06 — `BountyAnnouncementBuilder` reference in test file's docstring is stale

- **Severity**: trivia
- **File**: `services/bot-core/tests/test_bounty_spawn_executor.py`,
  line 18.
- **Observation**: Test file docstring claims `_announce_bounty` "Uses
  BountyAnnouncementBuilder (rich embed) instead of basic embed", but
  per `utils/executors/AGENTS.md` line 117, the builder was removed in
  A.48 (2026-04-27) and replaced with `build_bounty_announcement_request`
  + gateway-side rendering. Any S3 rewrite should align the test
  docstring with the current architecture.
- **Recommended fix policy**: Will be naturally corrected during the S3
  rewrite.

---

## SQLite Compatibility Concerns (Test-Side, Not Production Bugs)

Logged here so the S3 developer is aware:

1. **`Criminal.aliases` is `ARRAY(String)`** — SQLite cannot host this table.
   Tests that need criminal lookups must mock `BountyService.spawn_bounty` or
   `CriminalRepository.get_by_name` directly. The reference test sidesteps
   this by exercising the capacity-reached path which never reaches the
   criminal lookup.

2. **`System.coordinates / neighbours` are `ARRAY(...)`** — same issue.
   Pathfinding tests cannot run on SQLite.

3. **`Item / Module / Weapon` STI tables include `aliases ARRAY`** — same
   issue. Loadout-generation tests cannot run on SQLite.

4. **`apscheduler_jobs` is not part of the bot-core models** — created at
   runtime by APScheduler. The orchestrator's `text("SELECT COUNT(*)
   FROM apscheduler_jobs WHERE id LIKE :pattern")` query will return 0
   on SQLite (table absent → SQLite raises, but it is wrapped inside
   the orchestrator's outer try/except — *check this assumption*).
   Tests covering the orchestrator's queued-count branch should create
   the table manually before invocation:

   ```python
   await db.execute(text("CREATE TABLE apscheduler_jobs (id TEXT PRIMARY KEY)"))
   ```

5. **`func.now()` works on SQLite** (resolves to `CURRENT_TIMESTAMP`), so
   the `end_time > now()` filter in `count_active_by_guild_and_division`
   is portable.

6. **`BigInteger` snowflakes wider than ~2^63 will overflow SQLite's
   INTEGER affinity** — use representative test guild_ids in the 32-bit
   range. Real Discord snowflakes work in PostgreSQL but not in SQLite
   under aiosqlite.

---

*Last updated: 2026-05-07 by Architect agent (S2)*
