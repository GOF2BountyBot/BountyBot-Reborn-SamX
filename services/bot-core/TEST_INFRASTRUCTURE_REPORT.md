# Bot-Core Test Infrastructure - Comprehensive Report

**Date**: 2026-03-11  
**Status**: READ-ONLY Investigation  
**Objective**: Document existing test infrastructure to inform new test implementation

---

## 1. EXISTING TEST FILES

### Test Directory Structure
```
/proj/services/bot-core/tests/
├── __init__.py                       # Empty init
├── conftest.py                       # Root fixtures
├── test_database.py                  # Database utility tests (23,673 bytes)
├── test_message_builders.py          # Message builder tests (16,677 bytes)
├── test_utils.py                     # Utility function tests (33,834 bytes)
├── api/
│   ├── __init__.py
│   ├── test_health.py                # Health check endpoint tests (existing)
│   └── test_schemas.py               # Pydantic schema validation tests (1,560+ lines)
├── integration/
│   ├── __init__.py
│   ├── conftest.py                   # Integration test fixtures (SQLite in-memory)
│   ├── test_config_repository.py
│   ├── test_inventory_repository.py
│   ├── test_player_repository.py
│   ├── test_ship_repository.py
│   ├── test_shop_repository.py
│   └── test_user_repository.py
├── repositories/
│   ├── __init__.py
│   ├── test_criminal_repository.py
│   ├── test_discord_message_repository.py
│   ├── test_generic_repository.py
│   ├── test_item_repository.py
│   ├── test_module_repository.py
│   ├── test_ship_repository.py
│   ├── test_system_repository.py
│   └── test_weapon_repositories.py
├── services/
│   ├── __init__.py
│   ├── test_config_service.py
│   ├── test_inventory_service.py
│   ├── test_player_service.py
│   └── test_shop_service.py
```

### Complete Test File List (30 files)
1. `/proj/services/bot-core/tests/__init__.py`
2. `/proj/services/bot-core/tests/conftest.py` ⭐ Root fixtures
3. `/proj/services/bot-core/tests/test_database.py`
4. `/proj/services/bot-core/tests/test_message_builders.py`
5. `/proj/services/bot-core/tests/test_utils.py`
6. `/proj/services/bot-core/tests/api/__init__.py`
7. `/proj/services/bot-core/tests/api/test_health.py` ⭐ Existing pattern
8. `/proj/services/bot-core/tests/api/test_schemas.py` ⭐ Comprehensive schema tests
9. `/proj/services/bot-core/tests/integration/__init__.py`
10. `/proj/services/bot-core/tests/integration/conftest.py` ⭐ Integration fixtures
11. `/proj/services/bot-core/tests/integration/test_config_repository.py`
12. `/proj/services/bot-core/tests/integration/test_inventory_repository.py`
13. `/proj/services/bot-core/tests/integration/test_player_repository.py`
14. `/proj/services/bot-core/tests/integration/test_ship_repository.py`
15. `/proj/services/bot-core/tests/integration/test_shop_repository.py`
16. `/proj/services/bot-core/tests/integration/test_user_repository.py`
17. `/proj/services/bot-core/tests/repositories/test_criminal_repository.py`
18. `/proj/services/bot-core/tests/repositories/test_discord_message_repository.py`
19. `/proj/services/bot-core/tests/repositories/test_generic_repository.py`
20. `/proj/services/bot-core/tests/repositories/test_item_repository.py`
21. `/proj/services/bot-core/tests/repositories/test_module_repository.py`
22. `/proj/services/bot-core/tests/repositories/test_ship_repository.py`
23. `/proj/services/bot-core/tests/repositories/test_system_repository.py`
24. `/proj/services/bot-core/tests/repositories/test_weapon_repositories.py`
25. `/proj/services/bot-core/tests/services/test_config_service.py`
26. `/proj/services/bot-core/tests/services/test_inventory_service.py`
27. `/proj/services/bot-core/tests/services/test_player_service.py`
28. `/proj/services/bot-core/tests/services/test_shop_service.py`

**Note**: Directories marked with 📁 contain tests for the data layer (repositories and services), not API routers.

---

## 2. CONFTEST.PY CONTENTS

### Root conftest.py (`tests/conftest.py`)

**Purpose**: Service-specific fixtures for bot-core tests

**Key Fixtures**:

#### 1. `mock_db_manager` (AsyncMock)
- Type: `AsyncMock`
- Methods:
  - `initialize()` → AsyncMock
  - `shutdown()` → MagicMock
  - `get_health_info()` → AsyncMock returning dict with:
    - `connectivity`: True
    - `status`: "healthy"
    - `host`: "localhost"
    - `port`: 5432
    - `database`: "test_db"
- Attribute:
  - `_connection_string`: "postgresql+asyncpg://test:test@localhost:5432/test_db"

#### 2. `mock_schema_manager` (AsyncMock)
- Type: `AsyncMock`
- Methods:
  - `get_schema_health_info()` → AsyncMock returning dict with:
    - `version_match`: True
    - `current_version`: "1.0.0"
    - `expected_version`: "1.0.0"
    - `status`: "current"

#### 3. `test_app` (FastAPI)
- Depends on: `mock_db_manager`, `mock_schema_manager`
- Configuration:
  - Title: "BountyBot API Test"
  - Includes health router at `/api/v1` prefix
  - Sets `app.state.db_manager = mock_db_manager`
  - Sets `app.state.schema_manager = mock_schema_manager`

#### 4. `client` (TestClient)
- Depends on: `test_app`
- Type: FastAPI `TestClient`
- Used for synchronous HTTP requests in tests

**Shared mocks (before any imports)**:
```python
# shared.bblogger module mock (external dependency)
_mock_shared = types.ModuleType("shared")
_mock_bblogger = types.ModuleType("shared.bblogger")
_mock_bblogger.get_logger = _make_mock_logger  # Returns MagicMock with all logging methods
sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger
```

### Integration conftest.py (`tests/integration/conftest.py`)

**Purpose**: Integration test fixtures using SQLite in-memory database

**Key Fixtures**:

#### 1. `async_engine`
- Type: SQLAlchemy AsyncEngine
- Database: SQLite in-memory (`:memory:`)
- Creates tables on setup (SQLite-compatible models only):
  - `User.__table__`
  - `Player.__table__`
  - `GuildConfig.__table__`
  - `GuildShop.__table__`
  - `PlayerInventory.__table__`
  - `PlayerShip.__table__`
- Drops tables on teardown

#### 2. `db_session`
- Type: `AsyncSession`
- Depends on: `async_engine`
- Provides fresh session per test
- Uses `async_sessionmaker` with `expire_on_commit=False`

**Pre-import mocks**:
```python
# shared.bblogger
_mock_shared.bblogger.get_logger = MagicMock(return_value=MagicMock())

# sqlalchemy_utils (only used for UUIDType in DiscordMessage model)
_mock_sau.UUIDType = MagicMock()
```

---

## 3. EXISTING TEST PATTERNS

### Pattern 1: Health Router Tests (`test_health.py`)

**File**: `/proj/services/bot-core/tests/api/test_health.py` (103 lines)

**Test Classes and Methods**:
1. `TestSimpleHealth` - GET `/api/v1/health/simple`
   - `test_simple_health_returns_200()` - Validates simple health response

2. `TestLiveness` - GET `/api/v1/health/liveness`
   - `test_liveness_returns_alive()` - Validates liveness response

3. `TestHealthCheck` - GET `/api/v1/health/` (comprehensive)
   - `test_health_check_returns_healthy_when_db_accessible()` - Validates full health response structure

4. `TestReadiness` - GET `/api/v1/health/readiness`
   - `test_readiness_returns_ready_when_db_accessible()` - Happy path
   - `test_readiness_returns_503_when_no_db_manager()` - Tests standalone failure scenario

5. `TestDatabaseHealth` - GET `/api/v1/health/database`
   - `test_database_health_returns_info()` - Validates database + schema info

**Key Patterns**:
- Uses `client` fixture (TestClient)
- Tests use `request.app.state.db_manager` and `request.app.state.schema_manager`
- Creates standalone test app when needed (e.g., for failure scenarios)
- Verifies response structure, HTTP status codes, and data accuracy

### Pattern 2: Schema Validation Tests (`test_schemas.py`)

**File**: `/proj/services/bot-core/tests/api/test_schemas.py` (1,560+ lines)

**Coverage**: Comprehensive Pydantic schema tests for all API schemas

**Test Classes** (30+ classes):
- Health schemas: `TestHealthResponseSchema`, `TestSimpleHealthResponseSchema`
- About schemas: `TestItemResponseSchema`, `TestModuleResponseSchema`, `TestWeaponResponseSchema`, etc.
- Admin schemas: `TestInitializeGuildRequestSchema`, `TestGuildInitializationResponseSchema`, etc.
- Config schemas: `TestGuildConfigResponseSchema`, `TestUpdateConfigRequestSchema`, etc.
- Inventory schemas: `TestInventoryItemResponseSchema`, `TestAddItemRequestSchema`, etc.
- Players schemas: `TestPlayerResponseSchema`, `TestUpdateCreditsRequestSchema`, etc.
- Ships schemas: `TestShipsShipResponseSchema`, `TestCreateShipRequestSchema`, etc.
- Shops schemas: `TestShopItemResponseSchema`, `TestPurchaseRequestSchema`, etc.

**Test Methods Per Class**:
- `test_valid_construction()` - Happy path
- `test_with_optional_fields()` - Optional field handling
- `test_missing_required_raises()` - ValidationError for missing required fields
- `test_wrong_type_for_FIELD_raises()` - ValidationError for incorrect types
- `test_boundary_conditions()` - Edge cases (zero, max values, etc.)

**Key Patterns**:
- Uses `pytest.raises(ValidationError)` for error paths
- Tests all required and optional fields
- Validates field constraints (ranges, enums, etc.)
- No database or service calls (pure schema validation)

### Pattern 3: Integration Repository Tests

**Example**: `/proj/services/bot-core/tests/integration/test_player_repository.py`

**Test Structure**:
```python
@pytest.fixture
def repo() -> PlayerRepository:
    return PlayerRepository()

async def test_get_by_id_returns_player(db_session: AsyncSession, repo: PlayerRepository):
    # Setup: Create user and player
    await _create_user(db_session, 1)
    player = await _create_player(db_session, repo, user_id=1, guild_id=1000)
    
    # Execute: Call repository method
    result = await repo.get_by_id(db_session, player.id)
    
    # Assert: Verify result
    assert result is not None
    assert result.user_id == 1
```

**Key Patterns**:
- Uses `db_session` fixture (SQLite in-memory)
- Tests repository CRUD operations
- Uses helper functions (`_create_user()`, `_create_player()`) for setup
- Tests both success and error paths
- Verifies database state after operations

---

## 4. ROUTER SOURCE FILES & DEPENDENCY INJECTION

### Router Files Located at
`/proj/services/bot-core/src/api/routers/`

### All 13 Router Files

| Router | Purpose | Endpoints | Dependency Injection Pattern |
|--------|---------|-----------|------------------------------|
| `health.py` | Health checks | 5 GET | `request.app.state` |
| `players.py` | Player management | 7 | `Depends(get_player_service)` |
| `ships.py` | Ship management | 11 | `Depends(get_ship_service)` |
| `shops.py` | Shop operations | 10 | `Depends(get_shop_service)` |
| `inventory.py` | Inventory ops | 9 | `Depends(get_inventory_service)` |
| `admin.py` | Admin operations | 10 | Multiple: `get_player_service`, `get_shop_service`, `get_config_service` |
| `config.py` | Guild config | 10 | `Depends(get_config_service)` |
| `users.py` | User management | 5 | `Depends(get_user_service)` |
| `data.py` | Data queries | 2 | Direct imports (no DI) |
| `about.py` | Game asset info | 5 | Direct imports (no DI) |
| `scheduler.py` | Job scheduler | 7 | `request.app.state` |
| `discord_message.py` | Message storage | 7 | `Depends(get_discord_message_service)` |
| `announcements/` | Announcements | 8 | Complex nested structure |

### Dependency Injection Pattern Summary

**Pattern A: `request.app.state` Access** (health.py, scheduler.py)
```python
async def health_check(request: Request) -> HealthResponse:
    db_manager = request.app.state.db_manager
    schema_manager = request.app.state.schema_manager
```

**Pattern B: `Depends()` with Service DI** (most routers)
```python
async def get_player_service():
    return PlayerService()

@router.post("/")
async def create_player(
    request: CreatePlayerRequest,
    player_service: PlayerService = Depends(get_player_service)
):
```

**Pattern C: Multiple Service Dependencies** (admin.py)
```python
async def get_player_service():
    return PlayerService()

async def get_shop_service():
    return ShopService()

async def get_config_service():
    return ConfigService()

@router.post("/guilds/initialize")
async def initialize_guild(
    request: InitializeGuildRequest,
    config_service: ConfigService = Depends(get_config_service),
    shop_service: ShopService = Depends(get_shop_service)
):
```

### Database Session Access Pattern

**All Routers** use the same pattern:
```python
from persist.database.manager import get_db_session

async def create_player(
    request: CreatePlayerRequest,
    player_service: PlayerService = Depends(get_player_service)
):
    async with get_db_session() as db:
        player = await player_service.get_or_create_player(db, ...)
```

**Import Path**: `from persist.database.manager import get_db_session`

**Implementation**: `/proj/services/bot-core/src/persist/database/manager.py` line 265
```python
def get_db_session():
    """Convenience function to get a database session."""
    return db_manager.get_session()
```

**Return Type**: Async context manager that returns `AsyncSession`

### Detailed Router Endpoints

#### 1. **health.py** (5 endpoints)
- Pattern: `request.app.state`
- Endpoints:
  - `GET /health/` - Comprehensive health check
  - `GET /health/simple` - Simple status
  - `GET /health/readiness` - Readiness probe
  - `GET /health/liveness` - Liveness probe
  - `GET /health/database` - Database health details
- Accesses: `request.app.state.db_manager`, `request.app.state.schema_manager`

#### 2. **players.py** (7 endpoints)
- `POST /players/` - Create/get player
- `GET /players/{player_id}` - Get player
- `GET /players/guild/{guild_id}` - List players in guild
- `PUT /players/{player_id}/credits` - Update credits
- `PUT /players/{player_id}/xp` - Update XP
- `POST /players/{player_id}/prestige` - Prestige action
- `GET /players/{player_id}/statistics` - Player stats

#### 3. **ships.py** (11 endpoints)
- `GET /ships/player/{player_id}` - List ships
- `GET /ships/{ship_id}` - Get ship
- `POST /ships/` - Create ship
- `GET /ships/player/{player_id}/active` - Get active ship
- `PUT /ships/{ship_id}/set-active` - Set active ship
- `PUT /ships/{ship_id}/loadout` - Update loadout
- `PUT /ships/{ship_id}/nickname` - Update nickname
- `POST /ships/{ship_id}/equip` - Equip item
- `POST /ships/{ship_id}/unequip` - Unequip item
- `GET /ships/{ship_id}/loadout` - Get loadout summary
- `DELETE /ships/{ship_id}` - Delete ship

#### 4. **shops.py** (10 endpoints) - Similar DI pattern
#### 5. **inventory.py** (9 endpoints) - Similar DI pattern
#### 6. **admin.py** (10 endpoints) - Multiple service dependencies
#### 7. **config.py** (10 endpoints) - ConfigService dependency

---

## 5. SCHEMA FILES

### All 12 Schema Files
Located at `/proj/services/bot-core/src/api/schemas/`

| File | Purpose | Classes |
|------|---------|---------|
| `health_schema.py` | Health responses | `HealthResponse`, `SimpleHealthResponse` |
| `about_schema.py` | Game asset info | `ItemResponse`, `ModuleResponse`, `WeaponResponse`, `PrimaryWeaponResponse`, `SecondaryWeaponResponse`, `TurretWeaponResponse`, `ShipResponse`, `CriminalResponse`, `SystemResponse` |
| `admin_schema.py` | Admin requests/responses | `InitializeGuildRequest`, `GuildInitializationResponse`, `UpdatePlayerCreditsRequest`, `UpdatePlayerXPRequest`, `AddInventoryItemRequest`, `RemoveInventoryItemRequest`, `RefreshShopRequest`, `UpdateShopConfigRequest`, `SystemHealthResponse` |
| `config_schema.py` | Config management | `GuildConfigResponse`, `ConfigValidationResponse`, `UpdateConfigRequest`, `UpdateXPThresholdsRequest`, `UpdateShopConfigRequest` |
| `discord_message_schema.py` | Discord messages | `EmbedPayloadDict`, `DiscordMessageRequest`, `DiscordMessageResponse` |
| `inventory_schema.py` | Inventory items | `InventoryItemResponse`, `InventorySummaryResponse`, `AddItemRequest`, `RemoveItemRequest`, `TransferItemRequest`, `ItemTransactionResponse` |
| `players_schema.py` | Player data | `PlayerResponse`, `PlayerStatisticsResponse`, `CreatePlayerRequest`, `UpdateCreditsRequest`, `UpdateXPRequest`, `UpdateTierRequest` |
| `ships_schema.py` | Ship data | `ShipResponse`, `ShipLoadoutSummaryResponse`, `CreateShipRequest`, `UpdateLoadoutRequest`, `UpdateNicknameRequest`, `EquipItemRequest`, `UnequipItemRequest` |
| `shops_schema.py` | Shop items | `ShopItemResponse`, `ShopSummaryResponse`, `PurchaseRequest`, `SellRequest`, `TransactionResponse`, `RefreshShopRequest` |
| `users_schema.py` | User data | `UserResponse`, `CreateUserRequest`, `UpdateUserRequest` |
| `scheduler_schema.py` | Job scheduling | `OneTimeJob`, `RecurringJob`, `JobInfo`, `UpdateJob` |
| `announcements/` | Announcement schemas | (Complex structure) |

---

## 6. REQUIREMENTS.TXT ANALYSIS

**File**: `/proj/services/bot-core/requirements.txt` (28 lines)

### Testing & QC ✅ All Present
```
pylint>=3.0.0             # Code linting
pytest>=7.0.0             # Test framework ✅
pytest-asyncio>=0.21.0    # Async test support ✅
pytest-cov>=4.0.0         # Coverage reporting ✅
pytest-mock>=3.10.0       # Mocking utilities ✅
faker>=18.0.0             # Fake data generation ✅
```

**Verdict**: Full pytest suite with async support available.

---

## 7. PYTEST CONFIGURATION

### Configuration Files
**Search Results**: No pytest.ini, pyproject.toml, setup.cfg, or tox.ini found in `/proj/services/bot-core/`

### Running Tests
```bash
cd /proj/services/bot-core
python -m pytest tests/ -v                    # Run all tests
python -m pytest tests/api/ -v                # Run API tests only
python -m pytest tests/api/test_health.py -v # Run specific test file
```

---

## 8. API ROUTER TESTING GUIDE

**File**: `/proj/services/bot-core/API_ROUTER_TESTING_GUIDE.md` (256 lines)

### Recommended Testing Approach

#### Pattern 1: Health-Style (app.state access)
```python
@pytest.fixture
def test_app(mock_db_manager):
    app = FastAPI()
    from api.routers.health import router as health_router
    app.include_router(health_router, prefix="/api/v1")
    app.state.db_manager = mock_db_manager
    return app
```

#### Pattern 2: Service Injection (Depends)
```python
@pytest.fixture
def test_app(mock_player_service):
    app = FastAPI()
    from api.routers.players import router as players_router, get_player_service
    app.include_router(players_router, prefix="/api/v1")
    app.dependency_overrides[get_player_service] = lambda: mock_player_service
    yield app
    app.dependency_overrides.clear()
```

#### Pattern 3: Mocking get_db_session
```python
@pytest.fixture(autouse=True)
def mock_db_session():
    mock_session = AsyncMock()
    class FakeContextManager:
        async def __aenter__(self):
            return mock_session
        async def __aexit__(self, *args):
            pass
    with patch("persist.database.manager.get_db_session", 
               return_value=FakeContextManager()):
        yield mock_session
```

### Testing Checklist per Endpoint
1. ✅ Happy path - Valid request returns expected status and response
2. ✅ Validation - Invalid request returns 422
3. ✅ Not found - Service error returns 404
4. ✅ Server error - Unexpected exception returns 500
5. ✅ Response shape - Matches declared `response_model`
6. ✅ Service delegation - Service method called with correct args

### Router Priority
| Router | Endpoints | Priority |
|--------|-----------|----------|
| players.py | 7 | **HIGH** |
| ships.py | 11 | **HIGH** |
| shops.py | 10 | **HIGH** |
| inventory.py | 9 | **MEDIUM** |
| admin.py | 10 | **MEDIUM** |
| Others | - | LOW |

---

## 9. SUMMARY OF FINDINGS

### ✅ Strengths
1. **Comprehensive existing tests** - 30 test files covering utilities, schemas, and repositories
2. **Well-documented patterns** - API_ROUTER_TESTING_GUIDE.md provides clear examples
3. **Full pytest suite available** - All dependencies present
4. **Consistent router patterns** - Either `app.state` or `Depends()` pattern throughout
5. **Integration test infrastructure** - SQLite in-memory fixtures for data layer testing
6. **Shared fixtures** - Root conftest.py provides reusable mocks

### ⚠️ Gaps
1. **No API router tests** - No tests for `/players`, `/ships`, `/shops`, `/inventory`, `/admin`, `/config` endpoints
2. **No pytest configuration file** - Default discovery used

### 📋 Ready to Implement
- Player router tests (7 endpoints) - HIGH PRIORITY
- Ships router tests (11 endpoints) - HIGH PRIORITY
- Shops router tests (10 endpoints) - HIGH PRIORITY
- Inventory router tests (9 endpoints) - MEDIUM PRIORITY
- Admin router tests (10 endpoints) - MEDIUM PRIORITY
