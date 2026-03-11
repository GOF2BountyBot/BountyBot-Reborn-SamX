# API Router Testing Guide - bot-core

This guide explains how to test bot-core API routers without requiring a live database, Discord API, or any external services.

---

## Approach: FastAPI TestClient with Mocked Dependencies

Bot-core routers follow a consistent pattern:
1. Router endpoint receives request
2. Gets `db_manager` from `request.app.state` or uses `Depends()` for service injection
3. Opens a DB session via `get_db_session()` context manager
4. Calls service/repository methods
5. Returns response

We can test each router by:
- Creating a FastAPI test app with the router included
- Mocking `app.state.db_manager` and the `get_db_session` context manager
- Overriding `Depends()` injections with mock services
- Using `TestClient` for synchronous HTTP requests

No live backend is needed.

---

## Pattern 1: Health-style Routers (app.state access)

The health router accesses dependencies via `request.app.state`. This is already tested in `tests/api/test_health.py`:

```python
# tests/conftest.py pattern
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock

@pytest.fixture
def test_app(mock_db_manager, mock_schema_manager):
    app = FastAPI()
    from api.routers.health import router as health_router
    app.include_router(health_router, prefix="/api/v1")
    app.state.db_manager = mock_db_manager
    app.state.schema_manager = mock_schema_manager
    return app

@pytest.fixture
def client(test_app):
    return TestClient(test_app)
```

---

## Pattern 2: Service-injected Routers (Depends)

Most routers (players, ships, shops, inventory, etc.) use FastAPI dependency injection:

```python
# In the router file:
async def get_player_service():
    return PlayerService()

@router.post("/")
async def create_player(
    request: CreatePlayerRequest,
    player_service: PlayerService = Depends(get_player_service)
):
    async with get_db_session() as db:
        player = await player_service.get_or_create_player(db, ...)
        return PlayerResponse(...)
```

To test these, override dependencies and mock `get_db_session`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def mock_player_service():
    service = AsyncMock()
    # Configure return values for each method
    service.get_or_create_player = AsyncMock(return_value=MagicMock(
        id=1, user_id=12345, guild_id=67890, credits=100,
        lifetime_credits=100, systems_checked=0, bounty_wins=0,
        xp=0, tier="Bronze", prestige_count=0,
        duel_wins=0, duel_losses=0, duel_credits_won=0, duel_credits_lost=0,
        active_ship_id=None, created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00"
    ))
    return service


@pytest.fixture
def test_app(mock_player_service):
    app = FastAPI()
    from api.routers.players import router as players_router, get_player_service
    app.include_router(players_router, prefix="/api/v1")

    # Override dependency
    app.dependency_overrides[get_player_service] = lambda: mock_player_service
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


class TestCreatePlayer:
    @patch("api.routers.players.get_db_session")
    def test_create_player_returns_201(self, mock_get_db, client, mock_player_service):
        # Mock the async context manager for get_db_session
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.post("/api/v1/players/", json={
            "discord_id": 12345,
            "guild_id": 67890
        })

        assert response.status_code == 201
        data = response.json()
        assert data["user_id"] == 12345
        assert data["guild_id"] == 67890
        assert data["tier"] == "Bronze"

    @patch("api.routers.players.get_db_session")
    def test_create_player_with_username(self, mock_get_db, client, mock_player_service):
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.post("/api/v1/players/", json={
            "discord_id": 12345,
            "guild_id": 67890,
            "discord_username": "testuser"
        })

        assert response.status_code == 201
        mock_player_service.get_or_create_player.assert_awaited_once()

    @patch("api.routers.players.get_db_session")
    def test_create_player_service_error_returns_500(self, mock_get_db, client, mock_player_service):
        mock_session = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_player_service.get_or_create_player.side_effect = Exception("DB error")

        response = client.post("/api/v1/players/", json={
            "discord_id": 12345,
            "guild_id": 67890
        })

        assert response.status_code == 500
```

---

## Pattern 3: Mocking `get_db_session` Globally

Since most routers use `get_db_session()` from `persist.database.manager`, you can create a reusable fixture:

```python
@pytest.fixture(autouse=True)
def mock_db_session():
    """Globally mock get_db_session for all router tests."""
    mock_session = AsyncMock()

    class FakeContextManager:
        async def __aenter__(self):
            return mock_session
        async def __aexit__(self, *args):
            pass

    with patch("persist.database.manager.get_db_session", return_value=FakeContextManager()):
        yield mock_session
```

---

## What to Test for Each Router

For each endpoint, test:

1. **Happy path** - Valid request returns expected status code and response shape
2. **Validation** - Invalid request body returns 422 (FastAPI auto-validates Pydantic schemas)
3. **Not found** - Service raises ValueError/similar -> router returns 404
4. **Server error** - Service raises unexpected exception -> router returns 500
5. **Response shape** - Verify response matches the declared `response_model`
6. **Service delegation** - Verify the service method was called with correct arguments

---

## Router Priority for Testing

| Router | Endpoints | Complexity | Priority |
|--------|-----------|------------|----------|
| `players.py` | 7 | Medium | High - core game flow |
| `ships.py` | 11 | High | High - complex loadout logic |
| `shops.py` | 8 | High | High - transaction logic |
| `inventory.py` | 6 | Medium | Medium |
| `admin.py` | 8 | Medium | Medium |
| `config.py` | 5 | Low | Low |
| `users.py` | 5 | Low | Low |
| `data.py` | 2 | Low | Low |
| `scheduler.py` | 5 | Medium | Low |
| `discord_message.py` | 6 | Medium | Low |
| `about.py` | 7 | Low | Low |
| `announcements/` | 8 | Medium | Low |

---

## File Organization

```
tests/
├── conftest.py                    # Shared fixtures (bblogger mock, TestClient)
├── api/
│   ├── test_health.py             # Already exists
│   ├── test_schemas.py            # Already exists
│   ├── test_players_router.py     # New
│   ├── test_ships_router.py       # New
│   ├── test_shops_router.py       # New
│   ├── test_inventory_router.py   # New
│   └── ...
```

---

## Key Differences from discord-gateway API Tests

| Aspect | discord-gateway `api-test.py` | bot-core approach |
|--------|------------------------------|-------------------|
| Backend | Live Discord API required | No external services |
| Client | `requests` HTTP library | FastAPI `TestClient` |
| Database | Real PostgreSQL | Mocked or SQLite in-memory |
| Cleanup | Explicit delete calls | No cleanup needed (mocks) |
| Speed | Slow (network calls) | Fast (in-process) |
| CI/CD | Needs live bot token | Runs anywhere |

The discord-gateway suite is valuable for end-to-end validation. The bot-core approach here is for fast, isolated unit/integration tests that run in CI without any infrastructure.

---

## Getting Started

1. Pick a router from the priority table above
2. Read the router source to understand its endpoints and dependencies
3. Create a test file following Pattern 2
4. Mock the service layer (not the repository - that's already tested)
5. Test happy paths, error paths, and validation
6. Run: `python -m pytest services/bot-core/tests/api/ -v --tb=short`

---

## Framework Alignment: bot-core vs discord-gateway

Both bot-core and discord-gateway use **identical testing frameworks and tooling**. Differences in mock targets reflect architectural differences, not framework divergence.

### Shared Framework

| Aspect | Implementation |
|--------|-----------------|
| **Test Runner** | pytest |
| **HTTP Testing** | FastAPI `TestClient` (synchronous) |
| **Async Support** | pytest-asyncio |
| **Mocking** | unittest.mock (`AsyncMock`, `MagicMock`, `@patch`) |
| **Dependency Injection** | FastAPI's `app.dependency_overrides` |
| **Test Organization** | Class-based test groups (`class TestEndpointName`) |

### pytest Configuration (identical)

Both services use the same pytest plugins in their requirements:
- `pytest`
- `pytest-asyncio` (for async test functions)
- `pytest-cov` (coverage reporting)
- `pytest-mock` (enhanced mock fixtures)

### Why Mock Targets Differ

The **only difference** is what gets mocked, and this is **architecturally necessary**:

| Service | Primary Dependencies | Mock Strategy |
|---------|----------------------|---------------|
| **bot-core** | PostgreSQL (SQLAlchemy ORM) | Mock service/repository layer → DB access |
| **discord-gateway** | Discord API (discord.py client) | Mock Discord bot/API → Discord SDK calls |

Both services mock at the **boundary of their primary backend**. This is good design: it isolates the router logic (what we test) from the backend transport (what we mock).

### Shared Design Patterns

#### 1. Dependency Overrides
Both use FastAPI's built-in dependency override pattern:

```python
# bot-core
app.dependency_overrides[get_player_service] = lambda: mock_player_service

# discord-gateway
# (uses same pattern for any Depends() injections)
```

#### 2. Async Context Manager Mocking
Both handle FastAPI's async context managers the same way:

```python
# Bot-core example
mock_session = AsyncMock()
mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

# discord-gateway example (same pattern for Discord API calls)
mock_method = AsyncMock(return_value=expected_result)
```

#### 3. Class-Based Test Organization
Both organize tests into logical groups:

```python
# Both services use this pattern
class TestCreatePlayer:  # or TestGetUser
    def test_happy_path(self, client, mock_service):
        ...
    
    def test_error_handling(self, client, mock_service):
        ...
```

### Discord-Specific Patterns (Not Applicable to bot-core)

The discord-gateway service has **additional** requirements that bot-core doesn't need:

#### 1. Real Discord Exception Classes
Discord.py requires actual exception instances for `except` clauses to work:

```python
# This ONLY works if discord.NotFound is the real class, not MagicMock
except discord.NotFound:
    return 404  # Caught!

# This fails with MagicMock — isinstance() checks don't work
except MagicMock():  # ← isinstance will return False
    return 404  # Never reached
```

**Solution**: discord-gateway's `conftest.py` saves references to real `discord` classes before tests modify `sys.modules`, then restores them. This is specific to Discord.py's design and not needed for bot-core.

#### 2. Per-Test Module Reloading
Some discord-gateway tests reload the `api.routers.users` module (or others) to force fresh imports:

```python
importlib.reload(api.routers.users)  # Force reimport with current sys.modules state
```

**Why**: When test suite runs, different test files may inject different fakes into `sys.modules["discord"]`. To ensure a router picks up the correct discord reference for that test, we reload it.

**Not needed in bot-core**: The mocked services (PlayerService, etc.) are pure Python classes, not from external SDKs with exception hierarchies. Reloading isn't necessary.

### Conftest Fixture Hierarchy

Both services structure conftest fixtures in tiers:

| Tier | bot-core | discord-gateway | Purpose |
|------|----------|-----------------|---------|
| **Import Setup** | Path corrections, sqlalchemy_utils mock | Path corrections, shared.bblogger mock, discord exception saving | Ensure modules can be imported correctly |
| **Service/Mock Factories** | `make_mock_player()`, `mock_player_service` | `mock_discord_bot()`, `mock_discord_guild()`, etc. | Reusable mock objects |
| **App & Client** | `test_app()`, `client` | `test_app()`, `client` | FastAPI app + TestClient for each test |

### Conclusion

**No framework difference exists between bot-core and discord-gateway.**

Both services:
- Use pytest + FastAPI TestClient + unittest.mock
- Organize tests by class
- Use dependency overrides
- Mock async boundaries the same way
- Follow identical conftest patterns for setup

The differences in mock targets and special handling (real Discord exceptions, module reloading) are **architectural necessities**, not framework choices. They exist because:
- bot-core's backend is PostgreSQL (a synchronous data store accessed via SQLAlchemy)
- discord-gateway's backend is the Discord API (an async SDK with strict exception handling)

Each service mocks its **own backend boundary** appropriately. The testing framework itself is identical.
