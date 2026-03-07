# Test Coverage Analysis and Implementation Plan

**Date:** 2026-03-07  
**Purpose:** Document existing tests, identify gaps, and propose a prioritized plan

---

## Executive Summary

| Service | Test Files | Testing Framework | Coverage Type |
|---------|------------|------------------|---------------|
| bot-core | 0 | ❌ None | **Zero** |
| discord-gateway | 1 (`api-test.py`) | ❌ Manual (requests) | Integration only |
| shared | 0 | ❌ None | N/A |

---

## Part 1: Current State Analysis

### 1.1 bot-core Service

**Existing Tests:** NONE

**Current Test "Infrastructure":**
- No `tests/` directory
- No `pytest`, `unittest`, or any testing framework in `requirements.txt`
- No `conftest.py` fixtures
- No mocking utilities

**What SHOULD have tests:**
- All API Routers (11 routers: about, admin, config, data, discord_message, health, inventory, players, scheduler, ships, shops, users)
- All Services (config_service, inventory_service, player_service, shop_service)
- All Repositories (15 repositories)
- All Database Models (17 models)
- All Utility functions (data_loader, emoji_service, job_executor)
- Message builders
- Database manager and circuit breaker

### 1.2 discord-gateway Service

**Existing Tests:**
- [`services/discord-gateway/src/api-test.py`](services/discord-gateway/src/api-test.py) (~4,255 lines)
- [`services/discord-gateway/test-cleanup.sh`](services/discord-gateway/test-cleanup.sh)

**Testing Approach:**
- Custom integration test harness using Python `requests` library
- No pytest/unittest
- Tests running API endpoints against live service
- Test suites: Messages, Users, Roles, Guilds, Categories, Channels, Forums, Permissions, Health

**Design Principles (from author):**
The `api-test.py` was designed with the following principles:

1. **Self-Cleaning**: 100% guaranteed to be fully self-cleaning - any type of failure still gets cleaned up. In the event cleanup fails, object IDs are logged for manual cleanup.

2. **Zero-Trust**: Doesn't rely on the return from the API (which could be wrong). Any PUT/POST/PATCH/DELETE request has a 2nd API call to the GET endpoint to confirm that the changes ACTUALLY happened downstream.

3. **Self-Contained**: Fully self-contained - anything needed for GET testing is created via POST first (with few exceptions).

**What's NOT tested:**
- All Discord Cogs (10 cogs: aboutCog, adminCog, devCog, healthCog, inventoryCog, playerCog, shipsCog, shopCog, skinsCog, templateCog)
- All utility functions (discord_converters, discord_helpers, embed_converter, permission_utils)
- All Pydantic schemas
- Bot startup/shutdown logic

---

## Part 2: Gap Analysis

### Priority Matrix

| Priority | Component | Risk Level | Reason |
|----------|-----------|------------|--------|
| **P0** | bot-core API endpoints | Critical | Core game logic, no tests at all |
| **P0** | bot-core repositories | Critical | Data layer integrity |
| **P0** | bot-core services | Critical | Business logic |
| **P1** | discord-gateway cogs | High | Bot commands could break |
| **P1** | discord-gateway utilities | Medium | Helper functions are used everywhere |
| **P1** | bot-core models | Medium | Schema changes could break |
| **P2** | discord-gateway schemas | Low | Pydantic validation |
| **P2** | bot-core utilities | Low | Helper functions |

---

## Part 3: Implementation Plan

### Phase 1: Infrastructure Setup (Do First)

1. **Add Testing Dependencies**
   
   For `services/bot-core/requirements.txt`:
   ```
   pytest>=7.0.0
   pytest-asyncio>=0.21.0
   pytest-cov>=4.0.0
   httpx>=0.24.0
   pytest-mock>=3.10.0
   ```
   
   For `services/discord-gateway/requirements.txt`:
   ```
   pytest>=7.0.0
   pytest-asyncio>=0.21.0
   pytest-mock>=3.10.0
   ```

2. **Create Test Directory Structure**
   ```
   services/bot-core/tests/
   ├── __init__.py
   ├── conftest.py              # Shared fixtures
   ├── api/
   │   ├── test_players.py
   │   ├── test_ships.py
   │   ├── test_shops.py
   │   └── test_inventory.py
   ├── services/
   │   ├── test_player_service.py
   │   ├── test_shop_service.py
   │   └── test_inventory_service.py
   └── repositories/
       ├── test_player_repository.py
       └── test_ship_repository.py
   
   services/discord-gateway/tests/
   ├── __init__.py
   ├── conftest.py
   ├── cogs/
   │   ├── test_admin_cog.py
   │   └── test_player_cog.py
   └── utils/
       ├── test_discord_helpers.py
       └── test_permission_utils.py
   ```

3. **Create Shared Fixtures (conftest.py)**
   - Database session fixture
   - Test client fixtures
   - Mock fixtures for external services
   - Sample data fixtures

### Phase 2: bot-core Tests (P0)

**Priority Order:**

1. **Health Endpoint Tests** (`test_health.py`)
   - Test `/api/v1/health` returns 200
   - Test response structure
   - Test database connection status

2. **Repository Tests**
   - Test player_repository CRUD operations
   - Test ship_repository queries
   - Test shop_repository operations
   - Use in-memory SQLite for testing

3. **Service Tests**
   - Test player_service business logic
   - Test shop_service pricing calculations
   - Test inventory_service item management

4. **API Endpoint Tests**
   - Test key endpoints with FastAPI TestClient
   - Test request/response validation
   - Test error handling

### Phase 3: discord-gateway Tests (P1)

**Priority Order:**

1. **Utility Function Tests**
   - Test permission calculation logic
   - Test Discord converter functions
   - Test embed builder functions

2. **Cog Tests** (Mock-based)
   - Test admin commands
   - Test player commands
   - Test shop commands

3. **Schema Tests**
   - Test Pydantic model validation
   - Test serialization/deserialization

---

## Part 4: Test Strategy Recommendations

### 4.1 Testing Pyramid

```
        /\
       /  \      E2E / Integration (api-test.py)
      /----\     (existing, keep as-is)
     /      \
    /--------\   Unit Tests (pytest)
   /          \  (ADD: focus here first)
  /------------\ 
```

### 4.2 Recommended Approach

1. **Keep existing api-test.py** - It's a valuable integration testing framework with excellent design principles:
   - Self-cleaning (100% guaranteed cleanup, with manual cleanup logs if needed)
   - Zero-trust (verifies changes with GET after PUT/POST/PATCH/DELETE)
   - Self-contained (creates test data via POST before GET tests)
2. **Start with Unit Tests** - Fast to write, fast to run
3. **Use SQLite for database tests** - No external dependencies
4. **Mock external services** - Discord API, bot-core API

### 4.3 CI/CD Integration

Add to existing CI pipeline:
```yaml
# Example GitHub Actions
- name: Run bot-core tests
  run: |
    cd services/bot-core
    pip install -r requirements.txt
    pytest tests/ --cov=src --cov-report=xml

- name: Run discord-gateway tests
  run: |
    cd services/discord-gateway
    pip install -r requirements.txt
    pytest tests/ --cov=src --cov-report=xml
```

---

## Part 5: Quick Wins (Can Be Done Immediately)

### 5.1 High-Value, Low-Effort Tests

| Test | Value | Effort |
|------|-------|--------|
| Health endpoint tests | High | Low |
| Repository CRUD tests | High | Medium |
| Permission utility tests | High | Low |
| Schema validation tests | Medium | Low |

### 5.2 Recommended Starting Point

1. **bot-core**: Start with health endpoint tests and player repository
2. **discord-gateway**: Start with permission_utils tests

---

## Appendix: Test File Naming Convention

- Unit tests: `test_<module_name>.py`
- Integration tests: `integration_<feature>.py`
- Fixtures: `conftest.py`

---

*This plan was generated on 2026-03-07*
