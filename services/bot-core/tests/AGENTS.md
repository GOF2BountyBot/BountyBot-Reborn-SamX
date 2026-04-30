# AGENTS.md - bot-core tests

Test conventions for the bot-core service.

---

## Test Layout

```
tests/
├── conftest.py             # Service-level fixtures (mocked bblogger, mock_db_manager)
├── fixtures/
│   └── game_data.py        # Real game data fixtures
├── api/                    # Router-level tests (one file per router)
├── services/               # Service-level tests (one file per service)
├── repositories/           # Repository-level tests
├── integration/            # Integration tests with real SQLite-in-memory DB
└── test_*.py               # Top-level: executor, migration, startup tests
```

---

## Core Testing Conventions

- **Max 2 mocks per test** — prefer real objects with deterministic inputs.
- See `tests/services/test_combat_service.py` as the reference pattern for service tests.
- Use `tests/fixtures/game_data.py` for real game entity fixtures.
- `pytest` with `asyncio_mode = auto` (configured in root `pyproject.toml`).

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

The 20 cross-table operations covered by AC-8 are enumerated in
`/proj/recon/B34-remediation-spec.md` §6.1 and implemented in
`tests/integration/test_cross_session_persistence.py`. Future
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

*Last updated: 2026-04-30*
