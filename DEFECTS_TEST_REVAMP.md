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
   FROM apscheduler_jobs WHERE id LIKE :pattern")` query **RAISES
   `sqlalchemy.exc.OperationalError`** on SQLite when the table is absent.
   The S2 DEFECTS doc stated the error would be caught by the orchestrator's
   outer `try/except` — this is **INCORRECT**; the error propagates because
   it occurs inside the `async with db_manager.get_session() as db:` block
   which is itself inside the outer try/except, but SQLAlchemy raises it
   as a fatal session error before the executor's broad catch can handle it
   gracefully.

   **S3 fix**: All orchestrator integration tests that test eligible guilds
   must create the table manually before invoking the executor:

   ```python
   await db.execute(text("CREATE TABLE IF NOT EXISTS apscheduler_jobs (id TEXT PRIMARY KEY)"))
   await db.commit()
   ```

   Ineligible-guild tests (eligibility guard causes `continue` before the
   apscheduler_jobs query) do NOT need the table.

5. **`func.now()` works on SQLite** (resolves to `CURRENT_TIMESTAMP`), so
   the `end_time > now()` filter in `count_active_by_guild_and_division`
   is portable.

6. **`BigInteger` snowflakes wider than ~2^63 will overflow SQLite's
   INTEGER affinity** — use representative test guild_ids in the 32-bit
   range. Real Discord snowflakes work in PostgreSQL but not in SQLite
   under aiosqlite.

---

---

## S3 — Sprint 3 (Test Quality Blitz, 2026-05-07)

### S3-OBS-01 — apscheduler_jobs error not caught by orchestrator's outer try/except

- **Severity**: low (SQLite-only, test-environment concern)
- **File**: `services/bot-core/src/utils/executors/bounty_spawn_executor.py`,
  lines 235-239 (queued-count raw SQL query inside the session block).
- **Observation**: The S2 DEFECTS doc (item #4 in SQLite Compatibility Concerns)
  predicted that the missing `apscheduler_jobs` table error would be "caught by
  the orchestrator's outer try/except". S3 SQLite integration testing confirmed
  this is incorrect: the `OperationalError` propagates out of the session block
  and is NOT swallowed by the outer `except Exception` because SQLAlchemy marks
  the session as invalid at that point. The table-absent error surfaces as a fatal
  test failure, not as a silent 0-count fallback.
- **Risk**: Production-only risk: in production, APScheduler always creates the
  `apscheduler_jobs` table at startup, so this SQLite-specific behavior never
  surfaces there. Test-environment concern only.
- **Recommended fix policy**: Out of scope for test revamp. Tests use
  `_create_apscheduler_table()` helper to pre-create the table.

### S3-OBS-02 — Eligibility guard skips guild entirely (not added to guild_results)

- **Severity**: low (documentation / test correctness)
- **File**: `services/bot-core/src/utils/executors/bounty_spawn_executor.py`, line 197.
- **Observation**: When `_is_guild_fully_configured(config)` returns False, the
  orchestrator calls `continue` BEFORE adding the guild to `guild_results`. The
  S3 test originally expected the guild to appear in `guild_results` with
  `queued=0`, but the actual behavior is that the guild is silently absent from
  `guild_results` entirely. This is WAI — the log message at line 193-196 is the
  only record of the skip.
- **Risk**: None — correct behavior. Tests corrected to match WAI.
- **Recommended fix policy**: None needed. Log message adequately records the skip.

*Last updated: 2026-05-07 by Developer agent (S3)*

---

## S5 — Sprint 5 (Structural Surgery on test_bounty_service.py, 2026-05-07)

### S5-OBS-01 — sys.modules class-identity conflict between integration and unit tests

- **Severity**: low (test-environment concern, no production impact)
- **File**: All `tests/integration/test_*.py` that purge `services.*` from `sys.modules`.
- **Observation**: When integration test files that purge `services.*` entries from
  `sys.modules` (e.g. `test_cross_session_persistence.py`) are collected in the same
  pytest run as `tests/services/test_bounty_service.py`, Python's class-identity
  check (`isinstance(obj, SomeClass)`) and enum value comparisons can fail because
  the class was loaded twice — once from the test discovery path and once from
  `src/` after the purge. The symptom is `assert <Enum.X: 'x'> == <Enum.X: 'x'>`
  printing as equal but `assert` failing (two distinct class objects).
  This is NOT a production bug — it only manifests in the test harness when
  both file types are collected together.
- **Risk**: Confusing test failures when running the full suite. Each file passes
  independently.
- **Fix applied in S5**: `test_bounty_service_integration.py` omits the `services.*`
  purge to preserve class identity with the co-collected unit tests. Other integration
  files retain the purge as they previously worked around this issue through
  collection ordering.
- **Recommended fix policy**: Future integration test files that call services
  directly (rather than via HTTP/ASGI) should follow the same pattern: purge
  only `api.*` and `persist.*`, not `services.*`.

### S5-OBS-02 — distribute_rewards contains an inner db.commit() inside check_bounty's outer commit loop

- **Severity**: low (double-commit is idempotent in SQLAlchemy but adds overhead)
- **File**: `services/bot-core/src/services/bounty_service.py`, line 1587.
- **Observation**: `distribute_rewards` issues `await db.commit()` internally. The
  calling method `check_bounty` also issues an explicit `await db.commit()` at
  line 1074 to commit all per-bounty mutations atomically. The inner commit (in
  `distribute_rewards`) fires first, committing credits/XP mutations. Then the outer
  commit in `check_bounty` fires, which is a no-op (nothing pending). This is WAI
  and intentional per the B.34 closure comment in the source (lines 1574-1583), but
  the double-commit pattern is non-obvious and could surprise future maintainers.
- **Risk**: None in production. Academic double-commit is safe in SQLAlchemy.
- **Recommended fix policy**: Document only. A future refactor could hoist commit
  authority exclusively to `check_bounty`, but this is out of scope for test revamp.

*Last updated: 2026-05-07 by Developer agent (S5)*

---

## S6 — Sprint 6 (Integration Test Coverage for shop/player/inventory/duel services, 2026-05-08)

### S6-OBS-01 — GuildConfig model has no `bounty_channel_id` / `announcement_channel_id` columns

- **Severity**: trivia (test-authoring friction only)
- **File**: `services/bot-core/src/persist/models/guild_config.py`
- **Observation**: During S6 integration test authoring, the initial seed helper passed
  `bounty_channel_id=111, announcement_channel_id=333` to the `GuildConfig()` constructor.
  These column names do not exist in the current model (the actual channel columns are
  `bronze_bounty_channel_id`, `silver_bounty_channel_id`, etc., and there is no generic
  `announcement_channel_id`). SQLAlchemy raised `TypeError: 'bounty_channel_id' is an
  invalid keyword argument for GuildConfig`. Corrected to `GuildConfig(guild_id=...,
  starting_credits=...)` only — all other columns have SQLAlchemy defaults.
- **Risk**: None in production. Test-authoring friction only.
- **Recommended fix policy**: None needed. Column names were clarified by reading the model.
  The AGENTS.md GuildConfig entry in bot-core AGENTS.md accurately lists all columns.

### S6-OBS-02 — ShipLoadout dataclass uses `base_armour` not `armour`

- **Severity**: trivia (test-authoring friction only)
- **File**: `services/bot-core/src/services/combat_models.py`, line 122
- **Observation**: `ShipLoadout` has `base_armour: int` (not `armour`). An S6 duel
  integration test initially constructed `ShipLoadout(armour=100, handling=50, ...)`,
  which raised `TypeError: ShipLoadout.__init__() got an unexpected keyword argument 'armour'`.
  Fixed to `ShipLoadout(base_armour=100, ...)`. The `base_handling` field exists but is
  an int default=0 (not a required arg) and does not appear in the interface for
  LoadoutBuilder.from_player calls.
- **Risk**: None in production. Test-authoring friction only.
- **Recommended fix policy**: None needed. Reading the dataclass definition resolves this.

### S6-OBS-03 — sell_item / sell_ship caller must commit — service uses commit=False pattern

- **Severity**: low (documentation gap, not a bug)
- **File**: `services/bot-core/src/services/shop_service.py`, `sell_item` (line ~490-495)
  and `sell_ship` (line ~594-595)
- **Observation**: Both `sell_item` and `sell_ship` call `player_repo.update_credits` and
  `inventory_repo.remove_item` with `commit=False`. The service docstrings say "Transaction
  is owned by the caller (router)." This means tests calling these service methods must
  issue an explicit `await session.commit()` after the call — otherwise the transaction
  never commits and cross-session assertions fail. This is WAI per the B.34 architecture,
  but it is not immediately obvious from the service signature alone.
- **Risk**: None in production — routers always wrap in `async with db.begin()`.
- **Recommended fix policy**: Documentation only. S6 integration tests correctly model
  this pattern (explicit `await session_a.commit()` after each sell_item/sell_ship call).

*Last updated: 2026-05-08 by Developer agent (S6)*
