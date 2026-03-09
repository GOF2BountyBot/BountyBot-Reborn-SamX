# Testing/QC Framework Implementation Plan

**Project:** BountyBot-Reborn-SamX  
**Purpose:** Establish professional-grade testing and QC infrastructure  
**Target:** Autonomous implementation by Claude Code/Opus agent

---

## Current State

| Component | Status |
|-----------|--------|
| Linting | ❌ None |
| Unit Tests | ❌ None |
| Integration Tests | ✅ api-test.py exists (~4k lines) |
| Shared Test Infra | ❌ None |
| CI/CD | ❌ None |

---

## Architectural Decisions

### 1. Shared Test Infrastructure Location: `utils/`

**Rationale:** For monorepos, keeping test infrastructure separate from runtime shared code (in `services/shared/`) is cleaner. The `utils/` folder is already present and designated for common utilities.

### 2. HTTP Client: httpx (already migrated)

The codebase has already been migrated from `requests` → `httpx`. Use httpx for:
- API testing with pytest
- Async test patterns

### 3. Linter: pylint

Selected for thoroughness over speed (user preference).

---

## Implementation Guide

### Phase 1: Linting Infrastructure

**Goal:** Add pylint with project-specific configuration

#### 1.1 Add Dependencies

Update `services/bot-core/requirements.txt` and `services/discord-gateway/requirements.txt`:

```
pylint>=3.0.0
```

#### 1.2 Create Configuration Files

Create `utils/pylintrc`:

```ini
[BASIC]
good-names=i,j,k,ex,_,id,db

[MESSAGES CONTROL]
disable=C0111,C0103,R0903,R0913

[FORMAT]
max-line-length=120

[DESIGN]
max-args=8
max-attributes=12
```

Create `pyproject.toml` in root:

```toml
[tool.pylint]
rcfile = "utils/pylintrc"

[tool.pytest.ini_options]
testpaths = ["services"]
python_files = "test_*.py"
python_classes = "Test*"
python_functions = "test_*"
addopts = "-v --tb=short"
```

---

### Phase 2: Unit Testing Infrastructure

**Goal:** Set up pytest with shared fixtures

#### 2.1 Add Test Dependencies

For both services:

```
pytest>=7.0.0
pytest-asyncio>=0.21.0
pytest-cov>=4.0.0
pytest-mock>=3.10.0
faker>=18.0.0
```

#### 2.2 Create Root-Level Test Infrastructure

Create directory structure:

```
utils/
├── __init__.py
├── conftest.py              # Root fixtures
├── pytest.ini               # Pytest config
├── pylintrc                 # Linter config
├── test_fixtures/
│   ├── __init__.py
│   ├── sample_data.py      # Fake data generators
│   └── mocks.py            # Common mock objects
└── helpers/
    ├── __init__.py
    └── assertions.py      # Custom assertions
```

#### 2.3 Create Root conftest.py

```python
import pytest
import asyncio

@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture
def sample_guild():
    """Sample guild data for tests."""
    return {"id": 123456789, "name": "Test Guild"}

# Add more shared fixtures as needed
```

#### 2.4 Create Service-Level Test Directories

For each service that needs tests:

```
services/bot-core/tests/
├── __init__.py
├── conftest.py           # Service-specific fixtures
├── api/                  # API endpoint tests
│   └── test_health.py
├── services/            # Business logic tests
└── repositories/        # Data layer tests

services/discord-gateway/tests/
├── __init__.py
├── conftest.py
├── cogs/                # Cog tests
├── utils/               # Utility function tests
└── schemas/             # Schema validation tests
```

---

### Phase 3: CI/CD Integration

**Goal:** GitHub Actions workflow for automated QC

Create `.github/workflows/test.yml`:

```yaml
name: Quality Control

on: [push, pull_request]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install pylint
        run: pip install pylint
      - name: Run pylint
        run: |
          pylint services/bot-core/src services/discord-gateway/src \
            --rcfile=utils/pylintrc

  unit-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install -r services/bot-core/requirements.txt
          pip install pytest pytest-cov pytest-asyncio
      - name: Run bot-core tests
        run: |
          cd services/bot-core
          pytest tests/ --cov=src --cov-report=xml
      - name: Run discord-gateway tests  
        run: |
          cd services/discord-gateway
          pytest tests/ --cov=src --cov-report=xml
```

---

### Phase 4: Quick-Win Tests

**Goal:** Add high-value unit tests quickly

#### 4.1 bot-core: Health Endpoint

```python
# services/bot-core/tests/api/test_health.py
def test_health_endpoint_returns_200():
    from fastapi.testclient import TestClient
    from main import app
    
    client = TestClient(app)
    response = client.get("/api/v1/health")
    
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
```

#### 4.2 discord-gateway: Utility Functions

```python
# services/discord-gateway/tests/utils/test_permission_utils.py
def test_permission_calculation():
    # Test permission calculation logic
    pass
```

#### 4.3 discord-gateway: Schema Validation

```python
# services/discord-gateway/tests/schemas/test_channel_schemas.py
def test_channel_schema_validation():
    from schemas.channel_schemas import ChannelCreate
    # Test Pydantic validation
```

---

## What NOT to Include in CI/CD

These require running services and should remain manual/development-time:

- ❌ Integration tests (api-test.py) - requires live services
- ❌ API interaction tests - requires running endpoints
- ❌ Cross-service tests - requires docker-compose stack
- ❌ Discord API tests - requires bot token

---

## Implementation Order

1. **Add dependencies** to both services' requirements.txt
2. **Create** `utils/` directory structure with conftest.py
3. **Create** `utils/pylintrc` configuration
4. **Create** `pyproject.toml` in root
5. **Create** GitHub Actions workflow
6. **Add quick-win tests** for health endpoints and utilities
7. **Expand coverage** to repositories, services, cogs

---

## Notes for Implementation

- **Keep api-test.py as-is** - it's a valuable integration test harness, can be converted to pytest later
- **Use fixtures** - prefer pytest fixtures over manual setup/teardown
- **Mock external services** - Discord API, bot-core API should be mocked in unit tests
- **Use in-memory SQLite** - for repository tests, use in-memory DB, not PostgreSQL
- **Async tests** - use pytest-asyncio for async functions, mark with `@pytest.mark.asyncio`

---

*This plan is designed for autonomous implementation by Claude Code/Opus. The agent should use judgment on specific implementation details while adhering to the architectural decisions and structure outlined above.*
