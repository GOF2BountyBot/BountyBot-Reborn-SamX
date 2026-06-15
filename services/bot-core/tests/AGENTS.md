# AGENTS.md - bot-core tests

Test conventions for the bot-core service.

---

## Test Layout

```
tests/
├── conftest.py             # Root fixtures (mocked shared.bblogger, real shared.http_retry,
│                           #   mock_db_manager, test_app/client, seed_* game-data fixtures)
├── fixtures/
│   ├── game_data.py        # Real game data fixtures
│   └── golden_route_abc.png  # Golden image for map-renderer tests
├── api/                    # Router-level tests (one file per router; own conftest.py)
├── services/               # Service-level tests (one file per service; own conftest.py)
├── repositories/           # Repository-level tests
├── integration/            # Integration tests with real SQLite-in-memory DB (own conftest.py)
└── test_*.py               # Top-level: executor, migration, concurrency/locking, startup tests
```

---

## Core Testing Conventions

- **Max 2 mocks per test** — prefer real objects with deterministic inputs.
- See `tests/services/test_combat_service.py` as the reference pattern for service tests.
- Use `tests/fixtures/game_data.py` for real game entity fixtures.
- `pytest` with `asyncio_mode = auto` (configured in `services/bot-core/pytest.ini`, which governs the canonical from-bot-core invocation; the repo-root `pyproject.toml` sets the same value).

---

## Anti-Pattern: AsyncMock on Repo Methods with Side Effects

`AsyncMock`-based mocks of repository methods that have **ORM side effects**
(identity-map mutations, attribute expirations, flush/commit timing, etc.) MASK
entire bug classes from the test layer. The most notable case:

- `player_repository.update_credits`, `update_xp`, `update_tier`, `update_active_ship`
- `shop_repository.update_quantity`
- `inventory_repository.update_quantity`
- `player_ship_repository.set_active_ship`
- `bounty_repository.clear_active_by_guild`

These methods previously contained the Core-UPDATE + identity-map anti-pattern
that produced the doubled-credit bug in `shop_service.sell_item` /
`sell_ship` (April 2026). Service-level tests using `repo.update_credits =
AsyncMock()` did not reproduce the bug because the mock returned a fresh
`MagicMock` (or `None`) instead of triggering SQLAlchemy's identity-map
refresh on the caller's `player` instance.

### Rule

When testing a service that calls repo methods affecting ORM identity-map state,
prefer **integration tests** with a real (SQLite-in-memory) session over
AsyncMock-based service tests. See `tests/integration/test_response_body_consistency.py`
for the canonical pattern: assert that the API response body's credit/quantity
values match the DB row's values byte-for-byte.

If you MUST mock a repo update method in a service test, document the assumption
clearly in a comment and add a sibling integration test that exercises the
real ORM path.

### Existing AsyncMock-based tests (NOT rewritten in the Option B PR)

The following service-level tests use AsyncMock on update methods. They still
pass after the Option B refactor (the public contract is unchanged), but they
do NOT exercise the identity-map behavior:

- `tests/services/test_shop_service.py` — `update_credits`, `update_quantity` mocks
- `tests/services/test_player_service.py` — `update_credits` mocks
- `tests/services/test_inventory_service.py` — `update_quantity` mocks

A follow-up cleanup PR may convert these to either:
1. Pure unit tests that mock the entire repo and assert call args only (no return-value reliance), OR
2. Integration tests using the SQLite-in-memory pattern.

---

## Integration Test Pattern (`tests/integration/`)

Integration tests use a per-test fresh SQLite engine + session factory. Tables
that contain SQLAlchemy ARRAY columns (Ship, Item, Module STI tables) are NOT
included in the SQLite schema; tests that need them mock the lookup at the
repo boundary.

See:
- `tests/integration/conftest.py` — base SQLite fixtures (`db_session`, `async_engine`)
- `tests/integration/test_transaction_ownership_endpoints.py` — full ASGI + DB
  patching pattern
- `tests/integration/test_response_body_consistency.py` — response-vs-DB consistency
  pattern (Option B defense)
- `tests/integration/test_cross_session_persistence.py` — B.34 remediation
  AC-8 cross-session-reload tests (20 cross-table operations)

---

## Cross-Session Reload Rule (B.34, 2026-04-30)

Every service method that performs cross-table writes MUST have at least
one integration test in `tests/integration/` that:

  1. Opens session A from a fresh per-test engine.
  2. Performs the operation under test.
  3. **Closes session A entirely.**
  4. Opens a FRESH session B from the same engine.
  5. Queries DB through B and asserts persistence (or non-persistence
     for rollback paths).

Mock-only tests in `tests/services/` are insufficient for methods in the
flush-only set produced by `tests/test_transaction_discipline.py`'s
WRITES_FLUSH_ONLY classifier. The mocks return success regardless of
whether commit was actually called — this is the exact failure mode that
let B.34 land in production. Mock-based service tests can add coverage
but they do NOT substitute for the cross-session-reload integration
assertion.

The 20 cross-table operations covered by AC-8 are implemented in
`tests/integration/test_cross_session_persistence.py` (the original
enumeration lived in the B.34 remediation spec, which no longer exists
in the repo — the test file is now the canonical list). Future
cross-table operations must extend that file (or a sibling) with a
matching test.

---

## Transaction Discipline Linter (B.34, 2026-04-30)

`tests/test_transaction_discipline.py` runs as part of the normal pytest
suite. It fails CI when any router function calls a flush-only service
method without wrapping in `async with db.begin():` or committing
explicitly.

If you encounter a false positive that legitimately should not be wrapped
(e.g. dynamic dispatch the AST cannot reason about), add the suppression
comment described in `services/bot-core/src/persist/repositories/AGENTS.md`
under "Transaction Discipline Enforcement". Production-code suppressions
require a documented justification in the same commit message.

---

## Executor Test Pattern (S2 — definitive)

The `utils/executors/` modules are scheduled-job dispatchers that compose
multiple repositories, services, and outbound HTTP calls. Their original
test suite (notably `test_bounty_spawn_executor.py`, 1785 lines /
~357 mocks) used `AsyncMock` for every collaborator and asserted only on
`mock.assert_called_once()` — a textbook mock-overuse anti-pattern that
masked entire defect classes (capacity-gate arithmetic, ORM identity-map
confusion, eligibility-guard logic, HTTP body shape).

The S2 pattern below replaces that anti-pattern. **All new executor tests
written from 2026-05 onward MUST follow this pattern.** The S3 rewrite has
landed: `test_bounty_spawn_executor.py` was rewritten against this pattern
(real SQLite + respx, 0 repo mocks) and the interim
`test_bounty_spawn_executor_ref.py` reference file was folded into it and
deleted.

The canonical reference suite is now:

> `tests/test_bounty_spawn_executor.py`

Read it first before writing or modifying any executor test.

### Three-Tier Breakdown

| Tier | What it covers | Mock budget |
|------|----------------|-------------|
| **A — Pure unit** | Pure helpers in the executor module: `_is_guild_fully_configured`, `_get_division_channel_id`, `_get_division_role_id`, payload-validation early returns (e.g. missing `guild_id` / `tier` in `execute_bounty_spawn_one_job`). | **0 mocks.** Pass `SimpleNamespace` or plain dicts; assert on the return value. |
| **B — SQLite integration** | ORM read/write paths reachable from the executor: `count_active_by_guild_and_division`, `ConfigRepository.list_all`, `ConfigRepository.get_by_guild_id`, capacity-reached short-circuits, eligibility-guard skips. | **1 patch only:** `persist.database.manager.db_manager` is patched to yield a real `AsyncSession` from a SQLite-in-memory engine. NO repositories or services are mocked. |
| **C — respx HTTP boundary** | The outbound HTTP surfaces: (1) self-scheduling at `EXECUTOR_HOST:EXECUTOR_PORT/api/v1/jobs` — note this is now only the FALLBACK path; since P6-T8 the primary scheduling path is a direct in-process `scheduler_holder.get_scheduler().add_job(...)` call, exercised by patching `utils.scheduler_holder.get_scheduler` (see Mock Policy below); (2) gateway announcement at `DISCORD_GATEWAY_HOST:GATEWAY_PORT/api/v1/announcements/...` plus map upload to `/channels/{cid}/upload`. | **respx** intercepts `httpx.AsyncClient` calls. Assert on URL, JSON body shape, request count. The `assert_all_called=False, assert_all_mocked=True` defaults are recommended — known calls are matched, unexpected calls fail loudly. |

A single test may legitimately span Tier B + Tier C (the reference test
does so). Tier A tests typically live as small standalone functions
that take no fixtures.

### Mock Policy

#### Permitted at the executor layer

1. **`respx`** for any outbound `httpx.AsyncClient` call. This is the
   ONLY supported mechanism for asserting on HTTP boundaries — do not
   monkey-patch `httpx.AsyncClient` directly.
2. **A single `patch("persist.database.manager.db_manager", ...)`** that
   substitutes a `MagicMock` whose `.get_session` returns a fresh
   `@asynccontextmanager` factory yielding a real SQLite session. This
   is a *bridging* patch, not a behavioural one — no executor logic
   runs inside the mock.
3. **`patch("services.bounty_service.BountyService.spawn_bounty", ...)`
   and equivalents** are permitted ONLY for happy-path tests that need
   to bypass tables containing PostgreSQL `ARRAY(String)` columns
   (Criminal, System, Item, Module, Weapon STI tables) — see "SQLite
   Compatibility" below. The substitute should be a coroutine that
   inserts a real `Bounty` ORM instance into the SQLite session and
   returns it. Document the patch with a comment citing this AGENTS.md
   section.
4. **`patch("utils.scheduler_holder.get_scheduler", return_value=mock_scheduler)`**
   for the direct in-process scheduling path (P6-T8): the orchestrator
   counts queued jobs via `get_scheduler().get_jobs()` and schedules
   one-time jobs via `get_scheduler().add_job(...)`. A `MagicMock`
   scheduler with a stubbed `get_jobs()` return is the supported way to
   seed the queued count or capture `add_job` calls; returning `None`
   exercises the HTTP fallback (then assert via respx).

#### Forbidden at the executor layer

- Mocking `BountyRepository`, `ConfigRepository`, `CriminalRepository`,
  `DiscordMessageRepository`, or any other repository class. Use real
  SQLite instead.
- Mocking `AsyncSession` directly. Use a real session from a SQLite
  engine.
- Mocking the `BountyService` constructor or any non-`spawn_bounty`
  method. Tests that need narrower service behaviour belong in
  `tests/services/test_bounty_service.py`, not here.
- Asserting solely on `mock.assert_called_once()` / `assert_called_with()`
  for repository calls. The whole point of the rewrite is that the
  test asserts on real computed values (returned dicts, persisted DB
  state, intercepted HTTP request bodies).

### Patch Target — Deferred Imports

All executor functions use deferred imports (e.g.
`from persist.database.manager import db_manager` inside the function
body). The bound name therefore lives in the SOURCE module, not in the
executor's module namespace. The canonical patch target is:

```python
with patch("persist.database.manager.db_manager", fake_db_manager):
    result = await execute_bounty_spawn_one_job("job-id", payload)
```

Patching `utils.executors.bounty_spawn_executor.db_manager` will fail
with `AttributeError` because the executor module never bound that
name at module scope.

### Fixture Scope Recommendations

| Fixture | Recommended scope | Rationale |
|---------|-------------------|-----------|
| `sqlite_engine_and_factory` | `function` | Fresh DB per test prevents cross-test bleed-through; SQLite in-memory creation is < 50 ms so the cost is negligible. |
| `http_recorder` (respx) | `function` | Each test asserts on its own call history. |
| Seed helpers (`_seed_full_config`, `_seed_active_bounty`) | Plain `async def` helpers, NOT fixtures. | Each test seeds different shapes; promoting to fixtures forces `parametrize` gymnastics. |
| Common payload dicts | Inline literals or module-level constants. | They are tiny and per-test variations are common. |

For executor tests that share read-only seed data (e.g. a multi-guild
matrix test), promoting `sqlite_engine_and_factory` to `module` scope
with explicit truncation between tests is acceptable — but only after
demonstrating measurable wall-clock improvement.

### SQLite Compatibility

The integration conftest's SQLite schema (`_SQLITE_TABLES` in
`tests/integration/conftest.py`) includes only tables with
SQLite-compatible column types: `User`, `Player`, `GuildConfig`,
`GuildShop`, `PlayerInventory`, `PlayerShip`, and `Bounty` (Bounty is
JSON-only and SQLite-safe — already part of the shared list, so executor
tests get it for free).

Tables that contain `sqlalchemy.dialects.postgresql.ARRAY` columns
**cannot** be created on SQLite:

- `Criminal` (`aliases: ARRAY(String)`)
- `System` (`aliases: ARRAY(String)`, `coordinates: ARRAY(Integer)`, `neighbours: ARRAY(String)`)
- `Item` / STI children (`aliases: ARRAY(String)`)
- `Ship` (`aliases`, `builtin_modules`, `assets` — note `compatible_skins` is `JSON`, not ARRAY)

Tests that need these tables must either (a) live in
`tests/integration/` against a containerised PostgreSQL test database
(future work — not yet provisioned), or (b) mock the single service
method that would otherwise need them (`BountyService.spawn_bounty` is
the canonical example — see "Mock Policy" above).

For PostgreSQL-specific functions used by the executor (`func.now()`),
SQLite's parser is forgiving — `func.now()` resolves to
`CURRENT_TIMESTAMP`. The orchestrator's queued-jobs count no longer
queries `apscheduler_jobs` with raw SQL (P0-T3): it reads
`scheduler_holder.get_scheduler().get_jobs()`, which returns 0 queued
jobs when the holder is unset in tests. If a test needs the queued
count to be non-zero, patch `utils.scheduler_holder.get_scheduler`
with a mock scheduler whose `get_jobs()` returns fake job objects
(see Mock Policy item 4).

### Bounty-Spawn Behaviours Covered (S3 — implemented)

The S3 rewrite of `test_bounty_spawn_executor.py` covers the following
behaviours using the three-tier pattern (the file header carries its own
coverage table). Use this list as the regression checklist when touching
the executor or its tests.

| # | Behaviour | Tier | Notes |
|---|-----------|------|-------|
| 1 | `_is_guild_fully_configured` returns False when any of the 5 IDs is None | A | One test per missing field. |
| 2 | `_get_division_channel_id` and `_get_division_role_id` mappings (incl. tier-role fallback to `bounty_hunter_role_id`) | A | Pure dispatch, no DB. |
| 3 | Orchestrator skips guilds that fail eligibility | B | Persist a partially-configured guild; assert `tier_results` empty. |
| 4 | Orchestrator skips tiers when `bounty_max_per_tier[tier] == 0` | B | Verify `reason: "tier_disabled"` in result. |
| 5 | Orchestrator skips when `active + queued >= max_for_tier` | B + scheduler_holder mock | Queued count comes from `get_scheduler().get_jobs()` (P0-T3) — seed it via the mock. |
| 6 | Orchestrator schedules one-time jobs via direct `add_job` (P6-T8) | B + scheduler_holder mock | Assert `add_job` call args (run_date, `bounty_spawn_one` payload shape). |
| 7 | Orchestrator continues across tiers when one schedule call fails | B + scheduler_holder mock | Make `add_job` raise for one tier; assert other tiers still queued (`reason: "schedule_error"` for the failed one). |
| 8 | `execute_bounty_spawn_one_job` rejects payload missing `guild_id` / `tier` | A | Returns `{"success": False, "reason": "missing_payload"}` — no DB. |
| 9 | `execute_bounty_spawn_one_job` returns `guild_not_configured` when GuildConfig absent or partially configured | B | Persist no row / partial row. |
| 10 | `execute_bounty_spawn_one_job` returns `tier_not_configured` when channel/role missing for the tier | B | |
| 11 | `execute_bounty_spawn_one_job` returns `capacity_reached` (benign race) | B + C zero-call assertion | **The reference test.** |
| 12 | Happy path: spawns a bounty, schedules expiry, announces to gateway | B + C, with `BountyService.spawn_bounty` mocked to insert a Bounty | Assert announcement body via respx; verify `DiscordMessage` row persisted. |
| 13 | Map upload failure does not abort announcement | B + C | respx returns 500 on `/channels/{cid}/upload`; gateway announce still fires. |
| 14 | Gateway announcement failure is non-fatal | B + C | respx returns 500 on `/announcements/...`; bounty row still committed. |
| 15 | Expiry-scheduling failure is non-fatal | B + C | respx returns 500 on fallback `/jobs` route. |
| 16 | Reward / route values match `BountyService` outputs (regression for `total_reward / consolation_pool` accounting) | B + mocked `spawn_bounty` returning a hand-built Bounty | Asserts on `result["bounty_id"]` and the persisted Bounty row's `reward` field. |

### Developer Checklist

Before merging an executor test:

- [ ] Test imports respect the deferred-import pattern (`patch("persist.database.manager.db_manager", ...)`, NOT `patch("utils.executors.<name>.db_manager", ...)`).
- [ ] Real SQLite session is used for any DB read/write — no `AsyncMock` on repository methods.
- [ ] HTTP boundaries use `respx` — no monkey-patching of `httpx.AsyncClient`.
- [ ] Assertions read real DB state through a FRESH session (cross-session-reload rule above).
- [ ] No `mock.assert_called_once()` standing in for a value assertion.
- [ ] Mock count is documented inline (`# 1 mock — db_manager bridge`).
- [ ] `bounty_max_per_tier`, `end_time`, and other JSON / timezone-aware fields seeded explicitly — no implicit defaults.
- [ ] If `BountyService.spawn_bounty` is mocked, a comment cites this AGENTS.md section as justification.

---

*Last updated: 2026-06-11 — doc-vs-code reconciliation: S3 executor-test rewrite recorded (canonical reference is now `test_bounty_spawn_executor.py`; `_ref` file deleted); scheduler_holder mock added to the Mock Policy (P6-T8 direct scheduling / P0-T3 no raw apscheduler_jobs SQL); SQLite table list updated (Bounty included in `_SQLITE_TABLES`); Ship/System ARRAY-column lists corrected; B.34 spec path removed (file no longer exists).*
