# Logging Work Plan — BountyBot-Reborn-SamX

**Created**: 2026-03-16
**Status**: Ready to execute
**Total files requiring changes**: ~50 across 3 services

---

## Overview

An audit of 133 production source files found that 7 files have **zero logger initialization**, and ~19 files have critically insufficient logging for their complexity. Two files serve as the gold standard:

- `services/bot-core/src/api/routers/discord_message.py`
- `services/bot-core/src/api/routers/announcements/time_announcement.py`

### Logging API

All logging uses `bblogger` from `services/shared/bblogger.py`:

```python
from shared import bblogger
flogger = bblogger.get_logger("component-name")
```

Levels: `TRACE` (5) < `DEBUG` (10) < `INFO` (20) < `WARNING` (30) < `ERROR` (40) < `CRITICAL` (50)

Global control via `LOG_LEVEL` env var (already in `.env.example`).

### Logging Standards

| Level | Use for |
|-------|---------|
| `TRACE` | Internal state dumps, loop iterations, variable values during computation |
| `DEBUG` | Entry/exit of functions, parameters received, intermediate results, payloads |
| `INFO` | Successful user-facing operations, startup milestones, job completion |
| `WARNING` | Recoverable issues, fallback paths, retries, missing optional data |
| `ERROR` | Failed operations, caught exceptions, API errors (always include entity IDs) |
| `CRITICAL` | Unrecoverable failures, service cannot continue |

### Pattern (gold standard)

```python
# Every endpoint/method entry
flogger.info(f"Operation starting: entity_id={id}, guild={guild_id}")
flogger.debug(f"Full payload: {payload}")

# Before external calls
flogger.debug(f"Calling service: url={url}, data={data}")

# After external calls
flogger.debug(f"Response received: status={resp.status_code}")
flogger.trace(f"Response body: {resp.json()}")

# On success
flogger.info(f"Operation completed: entity_id={id}")

# On error
flogger.error(f"Operation failed: entity_id={id}, error={e}")
# or for full traceback:
flogger.exception(f"Unexpected error in operation: entity_id={id}")
```

### Delegation Pattern

| Agent | Use for |
|-------|---------|
| `@researcher` | Exploration, cataloging, reading files, simple/mechanical tasks |
| `@developer` | Complex coding: refactoring, adding logging to files, new features |
| `@architect` | Detailed analysis, design decisions, highly-complex problems |

---

## Phase 1 — CRITICAL (Fix immediately)

These files have **zero logger initialization** or **zero logging on critical infrastructure**.

### 1.1 bot-core: `generic_repository.py` — BASE CRUD CLASS, ZERO LOGGING

**File**: `services/bot-core/src/persist/repositories/generic_repository.py`
**Issue**: 8 methods, 0 log statements. This is the parent class for ~15 repositories.
**Delegate to**: `@developer`
**Action**:
- Add `flogger = bblogger.get_logger("generic-repository")`
- Add TRACE on every method entry with parameters: `add()`, `get_by_id()`, `get_by_name()`, `get_by_alias()`, `list_all()`, `remove()`, `create_or_update()`
- Add DEBUG on successful operations with result info
- Add ERROR in all exception handlers with entity context
- Note: child repos that override methods already have some logging; the base class needs it for the default implementations

### 1.2 bot-core: `migration_manager.py` — 14 METHODS, ZERO LOGGING

**File**: `services/bot-core/src/persist/database/migration_manager.py`
**Issue**: ALL migration operations are completely silent — ensure_current(), upgrade(), downgrade(), auto_generate(), etc.
**Delegate to**: `@developer`
**Action**:
- Add `flogger = bblogger.get_logger("migration-manager")`
- `ensure_current()`: INFO at start ("Checking for pending migrations..."), INFO on completion ("Migrations applied" or "Already at head")
- `upgrade(target)`: INFO with target revision
- `downgrade(target)`: WARNING (downgrades are risky) with target revision
- `auto_generate(message)`: INFO with description
- `from_async_url()` / `from_env()`: DEBUG with connection URL (mask password!)
- All error paths: ERROR with exception context

### 1.3 bot-core: `data.py` router — ZERO LOGGER, ZERO LOGGING

**File**: `services/bot-core/src/api/routers/data.py`
**Issue**: Bulk game data endpoint has NO LOGGER and NO LOGGING at all.
**Delegate to**: `@developer`
**Action**:
- Add `flogger = bblogger.get_logger("data-router")`
- Add INFO on endpoint entry with category parameter
- Add DEBUG with result count
- Add ERROR in exception handlers

### 1.4 bot-core: All 7 executor files — NO MODULE-LEVEL LOGGER

**Files** (all in `services/bot-core/src/utils/executors/`):
1. `bounty_spawn_executor.py`
2. `bounty_expire_executor.py`
3. `bounty_respawn_executor.py`
4. `duel_expire_executor.py`
5. `shop_refresh_executor.py`
6. `temperature_decay_executor.py`
7. `time_announcement_executor.py`

**Issue**: All 7 files have 11-23 logging calls but NO module-level `flogger = bblogger.get_logger(...)`. They use deferred imports (inside function body) — verify if logger is created inside the function or if the calls are broken.
**Delegate to**: `@researcher` first (verify the actual pattern), then `@developer` to fix
**Action**:
- Read each file to confirm the logging pattern
- If logger is created inline in the function body, standardize to module-level (or at minimum ensure it works)
- Ensure consistent logger naming convention: `"executor-<job_type>"`

### 1.5 bot-core: `scheduler.py` router — LOGGER ANOMALY

**File**: `services/bot-core/src/api/routers/scheduler.py`
**Issue**: Has 22 logging calls but unclear if `flogger` is properly defined at module level.
**Delegate to**: `@researcher` (verify), then `@developer` if fix needed
**Action**:
- Verify logger initialization
- Standardize to `flogger = bblogger.get_logger("scheduler-router")` if missing

---

## Phase 2 — HIGH PRIORITY (This sprint)

These files have loggers but critically insufficient coverage on important business logic.

### 2.1 bot-core: `combat_service.py` — NO ERROR LOGS

**File**: `services/bot-core/src/services/combat_service.py`
**Issue**: 9 functions, 4 log statements. Combat resolution has ZERO error logging.
**Delegate to**: `@developer`
**Action**:
- Add DEBUG at entry of each combat method with combatant IDs
- Add TRACE for combat calculation steps (damage, TTK, etc.)
- Add ERROR in all exception handlers
- Add INFO on combat resolution completion with winner/loser

### 2.2 bot-core: `equipment_service.py` — 8 FUNCTIONS MOSTLY SILENT

**File**: `services/bot-core/src/services/equipment_service.py`
**Issue**: 10 functions, 4 log statements. Equip/unequip operations are largely unlogged.
**Delegate to**: `@developer`
**Action**:
- Add DEBUG at entry of equip/unequip with player_id, ship_id, item details
- Add TRACE for validation steps (slot availability, type checks)
- Add INFO on successful equip/unequip
- Add WARNING when equip fails validation (slot full, wrong type)
- Add ERROR on unexpected failures

### 2.3 bot-core: `duel_service.py` — NO ERROR LOGS

**File**: `services/bot-core/src/services/duel_service.py`
**Issue**: 8 functions, 5 log statements. No error handling logs.
**Delegate to**: `@developer`
**Action**:
- Add DEBUG at entry of create_challenge, accept_duel, decline_duel, resolve_duel
- Add INFO on duel state transitions (created, accepted, declined, resolved)
- Add ERROR in all exception handlers with duel_id context
- Add WARNING on duel validation failures (self-duel, insufficient stakes)

### 2.4 bot-core: `system_graph_service.py` — GRAPH OPERATIONS MOSTLY SILENT

**File**: `services/bot-core/src/services/system_graph_service.py`
**Issue**: 9 functions, 4 log statements. Graph construction and traversal mostly silent.
**Delegate to**: `@developer`
**Action**:
- Add INFO on graph load/initialization with node and edge count
- Add DEBUG on graph queries (get_neighbors, get_path)
- Add TRACE for A* pathfinding steps (frontier exploration)
- Add ERROR on graph operation failures

### 2.5 bot-core: `temperature_service.py` — NO ERROR LOGS

**File**: `services/bot-core/src/services/temperature_service.py`
**Issue**: 5 functions, 3 log statements. No error handling logs.
**Delegate to**: `@developer`
**Action**:
- Add DEBUG on temperature calculations
- Add INFO on temperature decay application with guild_id
- Add ERROR on failures

### 2.6 bot-core: `manager.py` (DatabaseManager) — MISSING DEBUG ON CONNECTION OPS

**File**: `services/bot-core/src/persist/database/manager.py`
**Issue**: 16 functions, no DEBUG logs on critical connection operations.
**Delegate to**: `@developer`
**Action**:
- Add DEBUG on session creation/release
- Add DEBUG on pool statistics
- Add TRACE on connection lifecycle events
- Keep existing INFO/ERROR logs

### 2.7 bot-core: `circuit_breaker.py` — NO DEBUG ON STATE TRANSITIONS

**File**: `services/bot-core/src/persist/database/circuit_breaker.py`
**Issue**: 6 functions, 5 log statements. No debug on state transitions.
**Delegate to**: `@developer`
**Action**:
- Add DEBUG on every state transition (CLOSED→OPEN, OPEN→HALF_OPEN, HALF_OPEN→CLOSED)
- Add DEBUG on failure count increment
- Add TRACE on each call attempt

---

## Phase 3 — MEDIUM PRIORITY (Next sprint)

### 3.1 bot-core: Weapon/Item/Module repositories (8 files)

**Files**:
- `criminal_repository.py` (3 funcs, 1 log)
- `discord_message_repository.py` (9 funcs, 3 logs)
- `item_repository.py` (8 funcs, 2 logs)
- `module_repository.py` (3 funcs, 1 log)
- `primary_weapon_repository.py` (3 funcs, 1 log)
- `secondary_weapon_repository.py` (3 funcs, 1 log)
- `ship_repository.py` (3 funcs, 1 log)
- `system_repository.py` (3 funcs, 1 log)
- `turret_weapon_repository.py` (3 funcs, 1 log)
- `weapon_repository.py` (1 func, 0 logs)

**Delegate to**: `@developer` (batch — these are all similar)
**Action**: Add TRACE on query methods entry/exit with parameters; add DEBUG on mutations; add ERROR on failures.

### 3.2 bot-core: `schema_manager.py`

**File**: `services/bot-core/src/persist/database/schema_manager.py`
**Issue**: 6 functions, 3 log statements. Schema version checks mostly silent.
**Delegate to**: `@developer`
**Action**: Add DEBUG on version lookups and comparisons.

### 3.3 bot-core: Routers with gaps

**Files**:
- `about.py` (8 funcs — no INFO logs, 2 list endpoints silent)
- `duels.py` (5 funcs — no debug on challenge endpoints)
- `health.py` (5 funcs — no info on success)
- `systems.py` (2 funcs — pathfinding endpoints mostly error-only)

**Delegate to**: `@developer` (batch)
**Action**: Apply gold-standard logging pattern (entry INFO, payload DEBUG, response DEBUG, error ERROR).

### 3.4 discord-gateway: Cogs with low INFO coverage

**Files**:
- `inventoryCog.py` (16 funcs, 1 INFO log)
- `playerCog.py` (10 funcs, 1 INFO log)
- `shipsCog.py` (12 funcs, 1 INFO log)

**Delegate to**: `@developer` (batch)
**Action**: Add INFO at command entry with guild_id + user_id; ensure all error handlers log.

### 3.5 discord-gateway: Routers with low DEBUG

**Files**:
- `health.py` (3 funcs, 2 logs — no info on success)
- `tags.py` (10 funcs — no debug logs)

**Delegate to**: `@developer`
**Action**: Add DEBUG for request details; INFO for successful operations.

### 3.6 discord-gateway: Utils

**Files**: All files in `services/discord-gateway/src/utils/`
**Delegate to**: `@researcher` (audit first), then `@developer`
**Action**: Verify logging presence; add DEBUG on utility function entry/exit.

### 3.7 blender-service: Services with gaps

**Files**:
- `job_queue_service.py` (12 funcs, 4 logs — queue operations mostly silent)
- `render_config_service.py` (5 funcs, 2 logs — config updates silent)
- `image_utils.py` (4 funcs, 0 logs — no logger)

**Delegate to**: `@developer`
**Action**: Add DEBUG on job state transitions; INFO on config changes; add logger to image_utils.

### 3.8 blender-service: Routers with gaps

**Files**:
- `health.py` (3 funcs, 2 logs)
- `jobs.py` (3 funcs, 8 logs — verify coverage)

**Delegate to**: `@developer`
**Action**: Ensure all endpoints have entry/exit logging.

### 3.9 bot-core: message_builders/base.py

**File**: `services/bot-core/src/message_builders/base.py`
**Issue**: Abstract base class, 4 methods, 0 logs. Low priority since concrete implementations have logging.
**Delegate to**: `@developer`
**Action**: Add DEBUG in any non-abstract methods.

---

## Phase 4 — MIGRATION FIX

### 4.1 Add `compare_server_default=True` to Alembic env.py

**File**: `services/bot-core/src/persist/database/revisions/env.py`
**Delegate to**: `@developer`
**Action**: Add `compare_server_default=True` to `context.configure()` in `run_migrations_online()`.

### 4.2 Document migration workflow

**Files**: `services/bot-core/AGENTS.md`, `services/bot-core/src/persist/database/AGENTS.md`
**Delegate to**: `@developer`
**Action**: Add a note that:
- 0001 creates all tables from current ORM metadata (moving target — intentional for fresh installs)
- Future schema changes MUST use `run_migration revision -m "..."` to generate proper `op.add_column()` / `op.drop_column()` migrations
- NOT NULL columns need `server_default` in the migration for existing rows
- Example migration included in AGENTS.md

---

## Execution Summary

| Phase | Files | Effort | Agent |
|-------|-------|--------|-------|
| Phase 1 (Critical) | 11 files | ~2 hours | `@developer` |
| Phase 2 (High) | 7 files | ~2 hours | `@developer` |
| Phase 3 (Medium) | ~30 files | ~4 hours | `@developer` (batch) |
| Phase 4 (Migration) | 3 files | ~30 min | `@developer` |
| **Total** | **~50 files** | **~8.5 hours** | |

---

*Last updated: 2026-03-16*
