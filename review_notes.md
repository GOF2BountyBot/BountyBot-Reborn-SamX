# Review Notes - BountyBot-Reborn-SamX

*Last updated: 2026-03-11*

---

## bot-core: Linting & Testing (2026-03-11)

### Linting Setup

**Tool:** Ruff (configured in `/proj/pyproject.toml`)

**Rule sets enabled:** `E, F, W, I, UP, B, SIM, RUF`
- `E` - pycodestyle errors
- `F` - Pyflakes
- `W` - pycodestyle warnings
- `I` - isort import ordering
- `UP` - pyupgrade (Python 3.12 modernization)
- `B` - flake8-bugbear
- `SIM` - flake8-simplify
- `RUF` - Ruff-specific rules

**Ignored rules (with rationale):**
- `E712` - true/false comparison (SQLAlchemy filter style: `Column == True`)
- `E402` - module-level imports not at top (needed for sys.path setup)
- `B008` - function-call-in-default-argument (FastAPI `Depends()` pattern)
- `RUF012` - mutable-class-default (SQLAlchemy model columns)
- `UP046` - non-PEP695 generic class (keeping `Generic[T]` for clarity)
- `F821` - undefined names in `persist/models/*.py` (SQLAlchemy `Mapped[]` forward refs)

**Lint fixes applied:**
- 459 auto-fixed (type annotations modernized to Python 3.12 style)
- 20 manually fixed (ambiguous unicode chars, collapsible ifs, implicit Optional, StrEnum)
- **Status: All checks passing**

### Test Suite

**Framework:** pytest + pytest-asyncio + pytest-mock + pytest-cov

**Total: 795 tests passing, 1 skipped**

| Test Category | File Count | Test Count | Approach |
|---------------|-----------|------------|----------|
| Schema validation | 1 | 213 | Direct Pydantic construction + ValidationError |
| Health endpoints | 1 | 6 | FastAPI TestClient with mocked state |
| Service layer (unit) | 4 | 182 | AsyncMock repos, mocked bblogger |
| Utilities (unit) | 1 | 54 | Patched external calls |
| Message builders (unit) | 1 | 40 | Direct construction + factory |
| Generic repository (unit) | 1 | 26 | Mocked AsyncSession |
| Specialized repos (unit) | 7 | 101 | Mocked AsyncSession, field mapping verification |
| Database utils (unit) | 1 | 59 | Circuit breaker state machine, manager health |
| Repository integration | 6 | 140 | SQLite in-memory via aiosqlite |

**Coverage by layer:**

| Layer | Coverage | Notes |
|-------|----------|-------|
| Schemas (`api/schemas/`) | 100% | All 11 schema files |
| Services | 98-100% | config 100%, player 100%, shop 100%, inventory 98% |
| Utilities | 87-100% | emoji 100%, job_executor 100%, data_loader 98% |
| Message builders | 95-100% | factory 100%, base 100%, time_announcement 95% |
| Generic repository | 100% | All CRUD operations |
| Circuit breaker | 99% | Full state machine |
| Specialized repositories | 73-83% | Integration tests via SQLite; uncovered = exception handlers |
| API routers | 0-71% | Only health router tested; see API testing guide |
| Database manager | 58% | Init/shutdown paths need real engine |
| **Overall** | **61%** | Up from 0% (no tests existed before health tests) |

### Integration Test Setup

**Location:** `services/bot-core/tests/integration/`
**Database:** SQLite in-memory via `aiosqlite` (no external dependencies)
**Approach:** Real SQLAlchemy operations against actual tables

**SQLite-compatible tables tested:**
- User, Player, GuildConfig, GuildShop, PlayerInventory, PlayerShip

**Tables excluded from SQLite tests (ARRAY columns):**
- Item, Ship, Criminal, System, Module, Weapon types
- These models use `ARRAY(String)` which is PostgreSQL-specific
- Covered by mock-based unit tests instead

### Known Issues Found

1. **Bug: `PlayerRepository.update_credits()`** at `player_repository.py:138` uses `.values(new_credits=new_credits)` but the Player model column is named `credits`. This causes a `CompileError` at runtime. The integration test documents this.

2. **SQLite datetime limitation:** `GuildShop.is_refresh_due()` compares timezone-aware `datetime.now(UTC)` against SQLite-stored naive datetimes, causing `TypeError`. Works correctly with PostgreSQL. Test skipped with annotation.

3. **Pydantic v2 deprecation warnings:** 4 schema files use class-based `Config` instead of `ConfigDict`. Non-breaking but should be migrated.

---

## SQLite as Alternative Backend

### Feasibility Assessment

**Verdict: Feasible with moderate effort (2-3 days), but not recommended for production.**

**What works today:**
- 6 of 17 models fully compatible (proven by integration tests)
- Core CRUD operations, foreign keys, JSON columns, BigInteger all work

**What needs work for full SQLite support:**
- 9 models use `ARRAY(String)` - needs `TypeDecorator` that maps to JSON on SQLite
- `GenericRepository.get_by_alias()` uses PostgreSQL `.any()` operator - needs dialect-aware fallback
- `DatabaseManager` connection string is hardcoded for PostgreSQL
- Timezone-aware datetime handling differs

**Recommendation:**
- Keep PostgreSQL for production (concurrency, ARRAY support, timezone handling)
- SQLite works great for dev/testing (already proven)
- Only invest in full SQLite support if edge deployment without Docker is needed

---

## discord-gateway: Existing API Test Suite

**Location:** `services/discord-gateway/src/api-test.py` (~4,863 lines, ~180+ test cases)
**Cleanup helper:** `services/discord-gateway/test-cleanup.sh`

**Approach:** HTTP-based integration tests against a live discord-gateway instance
- 8 test suites: Messages, Users, Roles, Guilds, Categories, Channels, Forum, Health
- Deep write validation (POST -> GET -> compare)
- Automatic cleanup via atexit + signal handlers
- JSON audit logging for debugging

**Relevant patterns for bot-core API testing:**
- Response envelope normalization (handles multiple API formats)
- Resource lifecycle management (create -> test -> cleanup)
- Field comparison validation functions
- Exit codes for CI/CD integration

**Note:** This suite tests the discord-gateway REST API which proxies to the Discord API. It requires a live bot instance. Bot-core API testing can use FastAPI's TestClient instead, avoiding the need for a live backend. See the API router testing guide below.

---

## API Router Testing Guide

See `/proj/services/bot-core/API_ROUTER_TESTING_GUIDE.md` for the complete approach to testing bot-core API routers without a live backend.
