# Live-Fire Testing Analysis: Discord-Gateway Patterns Applied to Bot-Core

**Document Version:** 1.0  
**Date:** 2026-03-11  
**Audience:** Bot-Core Development Team, QA Engineers  
**Scope:** Comprehensive analysis of discord-gateway's `api-test.py` and `test-cleanup.sh` patterns with recommendations for bot-core adoption.

---

## Executive Summary

The discord-gateway service implements a sophisticated **self-contained API test harness** that demonstrates best practices for testing integration-heavy services. This document analyzes the patterns used and proposes a modernized approach for bot-core that:

1. **Keeps the strengths:** Zero-trust verification, automatic LIFO cleanup, audit logging, dependency ordering
2. **Modernizes the approach:** Uses pytest instead of standalone script, httpx async for performance, proper CI/CD integration
3. **Adds bot-core specifics:** Tests real PostgreSQL, dependency trees for game resources, transaction rollback support

---

## 1. Discord-Gateway api-test.py Architecture

### 1.1 Overall Structure

The `api-test.py` script (~4,863 lines) implements a **monolithic test runner** with:

```python
# Global State Management
├── TEST_RESULTS: List[Dict]          # Test result tracking
├── CLEANUP_QUEUE: List[Dict]         # LIFO resource cleanup queue
├── CLEANUP_LOCK: threading.Lock      # Thread-safe cleanup coordination
├── ARGS: argparse.Namespace          # CLI configuration
└── LOGGER: logging.Logger            # Dual output (file + console)

# Architecture Layers
├── Base HTTP Layer
│   ├── api_call(method, path, body, headers) → Tuple[httpx.Response, Optional[str]]
│   └── HTTP error handling and logging
│
├── Validation Layer
│   ├── validate_object(request_body, get_response_json) → Tuple[bool, str]
│   ├── _validate_recursive(req, resp) → Field-by-field comparison
│   ├── compare_lists_by_set(a, b) → Array order-insensitive matching
│   └── Response envelope unwrapping (canonical + legacy formats)
│
├── Cleanup Management
│   ├── schedule_cleanup() → Add to LIFO queue + audit log
│   ├── cleanup_all() → Execute reverse-order DELETE operations
│   ├── Signal handlers (SIGINT, SIGTERM)
│   └── atexit hook for guaranteed cleanup
│
├── Test Result Recording
│   ├── record_result() → Append to TEST_RESULTS + console/file logging
│   └── Color-coded output (GREEN/RED/YELLOW)
│
├── BaseTests Base Class
│   ├── run_simple_expected() → POST/PUT/DELETE + auto-cleanup
│   ├── run_validation() → POST → GET → field validation
│   ├── run_forbidden() → Mark test as skipped
│   ├── _extract_created_id() → Robust ID extraction from response envelopes
│   └── _normalize_data_to_list() → Flexible response normalization
│
└── Test Suite Classes (inherited from BaseTests)
    ├── MessageTests (16 test methods)
    ├── UserTests (12 test methods)
    ├── RoleTests
    ├── GuildTests
    ├── CategoryTests
    ├── ChannelTests
    ├── ForumTests
    ├── PermissionsTests
    ├── HealthTests
    └── Each suite: run_all() orchestrates test execution
```

### 1.2 Connection/Configuration Pattern

**CLI Arguments:**
```bash
python src/api-test.py \
  --base-url http://localhost:7999 \
  --guild-id 711548456019296289 \
  --user-id 640882072516427787 \
  --bot-id 721309941369012284 \
  --delay 2 \
  --validation-delay 5 \
  --log-file /app/data/logs/app.log \
  --cleanup-log /app/data/logs/created_objects.log
```

**Configuration Resolution:**
- Defaults embedded in script (e.g., `DEFAULT_BASE_URL`, `DEFAULT_GUILD_ID`)
- CLI args override defaults via `argparse`
- Global `ARGS` namespace used throughout
- No environment variable support (improvement opportunity)

**HTTP Client Setup:**
```python
# Per-request httpx.Client (not pooled)
def api_call(method: str, path: str, *, body=None, headers=None):
    url = ARGS.base_url.rstrip("/") + path
    with httpx.Client() as client:
        resp = client.request(
            method=method, url=url, json=body, 
            headers=hdrs, timeout=REQUEST_TIMEOUT
        )
```
**Issue:** Creates a new client per request (inefficient). Should use persistent session.

### 1.3 Zero-Trust Verification Model: POST → GET → Compare

**The "zero-trust" pattern** assumes the POST response may be incomplete, cached, or wrong. Validation uses a three-step process:

```python
def run_validation(
    name: str,
    method: str,
    path: str,
    body: dict,
    get_path: str,
    validate_fn: Optional[Callable] = None
) -> None:
    """
    Step 1: POST (or PUT) with request body
    Step 2: Wait for eventual consistency (vdelay)
    Step 3: GET the resource from authoritative endpoint
    Step 4: Field-by-field comparison of request_body vs GET response
    """
    # Step 1: Make the mutation request
    resp = api_call(method, path, body=body)
    
    # Step 1.5: Extract created resource ID
    rid = self._extract_created_id(resp.json())
    
    # Step 2: Wait for eventual consistency (default 5 seconds)
    time.sleep(self.vdelay)
    
    # Step 3: GET the resource using extracted ID
    # Supports template URLs like "/api/v1/messages/{id}"
    get_path_used = get_path.format(id=rid)
    gresp = api_call("GET", get_path_used)
    
    # Step 4: Validate fields
    data = gresp.json()
    ok, reason = validate_object(body, data)
    record_result(name, method, path, resp.status_code, ok, reason)
```

**Strengths:**
- Decouples request acknowledgment from persistence guarantee
- Catches silent data loss or transformation bugs
- Works with async/eventual-consistency backends
- Supports field-level validation (not just status codes)

**Implementation Details:**

1. **Request Body → GET Comparison** (`validate_object`):
```python
def validate_object(request_body: Dict, get_response_json: Dict) -> Tuple[bool, str]:
    """
    Validate that the GET response contains all fields from request_body.
    Handles response envelope unwrapping.
    """
    # Unwrap canonical API envelope: {"data": {...}}
    resp_obj = get_response_json
    if isinstance(resp_obj, dict) and "data" in resp_obj:
        resp_obj = resp_obj["data"]
    
    # Unwrap known single-resource wrappers: {"message": {...}}, {"role": {...}}, etc.
    if isinstance(resp_obj, dict):
        for wrapper in ("message", "guild", "member", "role", "channel", "category", "thread", "tag"):
            if wrapper in resp_obj and isinstance(resp_obj[wrapper], (dict, list)):
                resp_obj = resp_obj[wrapper]
                break
    
    # Recursive field validation
    for k, v in request_body.items():
        ok, reason = _validate_recursive(v, resp_obj[k])
        if not ok:
            return False, f"field '{k}': {reason}"
    return True, ""
```

2. **Array Comparison** (order-insensitive):
```python
def compare_lists_by_set(a: List[Any], b: List[Any]) -> bool:
    """Compare arrays as sets (ignoring order and duplicates)"""
    set_a = {json.dumps(x, sort_keys=True) for x in a}
    set_b = {json.dumps(x, sort_keys=True) for x in b}
    return set_a == set_b
```

### 1.4 Cleanup Strategy: LIFO Queue, Signal Handlers, atexit Hooks

**Cleanup is the most sophisticated aspect of the harness:**

```python
# Global cleanup state
CLEANUP_QUEUE: List[Dict] = []  # LIFO stack of resources to delete
CLEANUP_LOCK = threading.Lock()  # Protect against concurrent access
CLEANUP_DONE = False             # Ensure cleanup runs exactly once
CLEANUP_FAILED = False           # Track cleanup errors for exit code

# Schedule a resource for cleanup
def schedule_cleanup(
    test_name: str,
    resource_type: str,
    resource_id: Union[str, int],
    delete_method: str,
    delete_uri: str,
    delete_body: Optional[dict] = None
) -> None:
    """
    Add cleanup entry to both:
    1. In-memory CLEANUP_QUEUE (for immediate cleanup)
    2. Audit log file (for manual recovery)
    """
    entry = {
        "timestamp": now_iso(),
        "test_name": test_name,
        "resource_type": resource_type,
        "resource_id": str(resource_id),
        "delete_method": delete_method,
        "delete_uri": delete_uri,
        "delete_body": delete_body,
        "cleanup_result": None  # Filled during cleanup
    }
    with CLEANUP_LOCK:
        CLEANUP_QUEUE.append(entry)
    safe_append_cleanup(entry)  # Append to JSONL file

# Execute cleanup in reverse order (LIFO)
def cleanup_all() -> None:
    global CLEANUP_FAILED, CLEANUP_DONE
    if CLEANUP_DONE:
        return  # Idempotent: runs only once
    
    with CLEANUP_LOCK:
        items = list(reversed(CLEANUP_QUEUE))  # LIFO order
    
    for entry in items:
        rt, rid = entry["resource_type"], entry["resource_id"]
        meth, uri = entry["delete_method"], entry["delete_uri"]
        
        resp = api_call(meth, uri)  # Execute DELETE
        
        if resp is None:
            res = "network-error"
            CLEANUP_FAILED = True
        elif resp.status_code in (200, 204):
            res = f"success:{resp.status_code}"
        else:
            res = f"failed-status:{resp.status_code}"
            CLEANUP_FAILED = True
        
        # Update audit log with cleanup result
        safe_append_cleanup({**entry, "cleanup_result": res})
        time.sleep(0.3)  # Rate limit cleanup
    
    CLEANUP_DONE = True

# Guarantee cleanup execution
atexit.register(cleanup_all)
signal.signal(signal.SIGINT, _on_signal)   # Ctrl+C
signal.signal(signal.SIGTERM, _on_signal)  # SIGTERM
sys.excepthook = _handle_uncaught          # Uncaught exceptions
```

**Strengths:**
1. **LIFO ordering:** Deletes resources in reverse creation order, respecting dependencies
2. **Guaranteed execution:** atexit + signal handlers ensure cleanup even on crash
3. **Audit trail:** JSON-lines log of all operations for debugging manual cleanup
4. **Idempotency:** `CLEANUP_DONE` flag prevents double-cleanup
5. **Resilience:** Continues cleanup even on DELETE failures

**Example Audit Log:**
```json
{"timestamp": "2026-03-11T12:34:56.789Z", "test_name": "POST create message", "resource_type": "message", "resource_id": "1234567890", "delete_method": "DELETE", "delete_uri": "/api/v1/messages/1234567890", "delete_body": null, "cleanup_result": null}
{"timestamp": "2026-03-11T12:34:58.123Z", "test_name": "POST create message", "resource_type": "message", "resource_id": "1234567890", "delete_method": "DELETE", "delete_uri": "/api/v1/messages/1234567890", "delete_body": null, "cleanup_result": "success:204"}
```

### 1.5 Rate Limiting Approach

**Per-test delays** prevent rate limiting:

```python
class BaseTests:
    def __init__(self, guild_id:int, user_id:int, delay:float, vdelay:float):
        self.delay = delay          # Default 2 seconds (between tests)
        self.vdelay = vdelay        # Default 5 seconds (validation wait)
    
    def wait(self):
        time.sleep(self.delay)      # After each test
    
    def wait_valid(self):
        time.sleep(self.vdelay)     # After POST, before GET validation

# In test methods:
def test_create_valid(self, cid: int):
    self.run_validation(name, "POST", path, body, "/api/v1/messages/{id}")
    # Implicit self.wait() at end of run_validation
```

**Additional rate limiting in cleanup:**
```python
time.sleep(0.3)  # Between DELETE operations
```

**CLI override:**
```bash
python api-test.py --delay 5 --validation-delay 10
```

### 1.6 Audit Trail: JSON-Lines Cleanup Log

**Purpose:** Enable manual recovery if cleanup fails.

**Format (JSONL, one object per line):**
```json
{"timestamp": "...", "test_name": "...", "resource_type": "message", "resource_id": "123", "delete_method": "DELETE", "delete_uri": "/api/v1/messages/123", "delete_body": null, "cleanup_result": null}
{"timestamp": "...", "test_name": "...", "resource_type": "message", "resource_id": "123", "delete_method": "DELETE", "delete_uri": "/api/v1/messages/123", "delete_body": null, "cleanup_result": "success:204"}
```

**Fields:**
- `timestamp`: ISO 8601 when resource was created
- `test_name`: Which test created it
- `resource_type`: "message", "role", "channel", etc.
- `resource_id`: The created resource's ID
- `delete_method`: HTTP method for cleanup (always "DELETE")
- `delete_uri`: Full URI path for cleanup
- `delete_body`: Request body for cleanup (usually null)
- `cleanup_result`: "success:204", "failed-status:500", "network-error", null (pending)

**Lifecycle:**
1. Entry created with `cleanup_result: null` when resource is made
2. Entry updated with result when cleanup executes
3. Manual inspection after failures: grep for `null` cleanup_results

### 1.7 Test Data Isolation: test- Prefix Naming

All created resources use `test-` prefix for easy identification and manual cleanup:

```python
# Channel name with timestamp
body = {"name": f"test-channel-{int(time.time())}"}

# Role name
role_body = {"name": f"test-role-{int(time.time())}"}

# Message content
body = {"content": {"title": "test-message", "description": now_iso()}}

# Guild name
guild_body = {"name": f"test-guild-{int(time.time())}"}
```

**Benefits:**
- Visual identification in Discord (shows "test-" prefix)
- Easy cleanup via Discord client if automation fails: delete anything starting with "test-"
- Isolation from production test data

### 1.8 Response Unwrapping: Canonical Envelope, Legacy, Direct

**Problem:** API responses may use different wrapping conventions:
- Canonical: `{"status": 200, "data": {...}, "timestamp": "...", "message": "..."}`
- Legacy: `{"message": {...}}`
- Direct: `{...}` (no wrapper)

**Solution - Robust unwrapping pipeline:**

```python
def _extract_created_id(self, resp_json: Any) -> Optional[int]:
    """Extract created resource ID from various response formats."""
    if not isinstance(resp_json, dict):
        return None
    
    j = resp_json
    
    # Step 1: Unwrap canonical envelope
    if "data" in j and isinstance(j["data"], (dict, list)):
        j = j["data"]
    
    # Step 2: Unwrap single-resource wrappers
    for wrapper in ("message", "guild", "member", "role", "channel", "category", "thread", "tag"):
        if wrapper in j and isinstance(j[wrapper], (dict, list)):
            j = j[wrapper]
            break
    
    # Step 3: Look for id fields
    if isinstance(j, dict):
        for key in ("message_id", "id"):  # Priority order
            if key in j:
                try:
                    return int(j[key])
                except Exception:
                    pass
        # Try nested common resource keys
        for key in ("category", "channel", "role", "thread", "tag", "message"):
            tmp = j.get(key)
            if isinstance(tmp, dict) and "id" in tmp:
                try:
                    return int(tmp["id"])
                except Exception:
                    pass
    return None
```

**Used in validation:**
```python
def validate_object(request_body: Dict, get_response_json: Dict) -> Tuple[bool, str]:
    resp_obj = get_response_json
    
    # 1) Unwrap canonical envelope
    if isinstance(resp_obj, dict) and "data" in resp_obj:
        resp_obj = resp_obj["data"]
    
    # 2) Unwrap known single-resource wrappers
    if isinstance(resp_obj, dict):
        known_wrappers = ("message", "guild", "member", "role", "channel", "category", "thread", "tag")
        for wrapper in known_wrappers:
            if wrapper in resp_obj and isinstance(resp_obj[wrapper], (dict, list)):
                resp_obj = resp_obj[wrapper]
                break
    
    # 3) Continue with field validation...
```

### 1.9 Error Reporting and Color-Coded Output

**Console Output:**
```python
color = GREEN if passed else (YELLOW if skipped else RED)
line = f"[{tag}] {name} | {method} {uri} | {status_code}"
sys.stdout.write(color + line + RESET + "\n")
```

**Example output:**
```
[PASS] POST create valid | POST /api/v1/channels/123/messages | 201
[FAIL] POST invalid embed | POST /api/v1/channels/123/messages | 400 | expected 2xx, got 400
[SKIP] POST forbidden | POST /api/v1/channels/123/messages | None | SKIPPED (no auth)
```

**Summary Report:**
```
=== SUMMARY: total=125 exec=120 pass=118 fail=2 skip=5 ===
--- PASSED TESTS ---
[GREEN] POST create valid | POST /api/v1/messages | 201
...
--- FAILED TESTS ---
[RED] POST invalid embed | POST /api/v1/messages | 400 | expected 2xx, got 400
...
--- SKIPPED TESTS ---
[YELLOW] POST forbidden | POST /api/v1/messages | None | SKIPPED (no auth)
...
=== TOTAL RUNTIME: 4m 32.15s ===
```

**Exit Code:**
- `0` if no failures and cleanup successful
- `1` if any test failed or cleanup had errors

---

## 2. test-cleanup.sh Analysis

### 2.1 Purpose and Design

The cleanup script is a **standalone safety mechanism** for manual resource cleanup if the test runner's cleanup phase fails or is interrupted.

### 2.2 Parsing the Cleanup Log

**Input:** JSON-lines file from `--cleanup-log` option:
```json
{"delete_method": "DELETE", "delete_uri": "/api/v1/messages/123", "delete_body": null, ...}
```

**Parsing with jq:**
```bash
jq -cr '
  select(.delete_method == "DELETE")  # Filter for DELETE entries
  | "\(.delete_method) \(.delete_uri) \((.delete_body // null) | @json)"' \
  "$LOGFILE"
# Output: DELETE /api/v1/messages/123 null
```

**Shell Parsing:**
```bash
while IFS= read -r line; do
    method="${line%% *}"           # Extract method (DELETE)
    rest="${line#* }"              # Remove method
    uri="${rest%% *}"              # Extract URI (/api/v1/messages/123)
    body="${rest#* }"              # Extract body (null or JSON)
done
```

### 2.3 DELETE Replay Mechanism

**Curl execution:**
```bash
# Build command dynamically
cmd=(curl -s -X "$method" "$BASE_URL$uri" -H "Content-Type: application/json")

# Only add body if non-null
if [[ "$body" != null ]]; then
    cmd+=(-d "$body")
fi

# Execute (|| true prevents script exit on failure)
"${cmd[@]}" || true
```

**Example replays:**
```bash
>>> DELETE http://localhost:7999/api/v1/messages/123 null...
>>> DELETE http://localhost:7999/api/v1/channels/456 null...
>>> DELETE http://localhost:7999/api/v1/roles/789 null...
```

### 2.4 Idempotency and Safety

**Idempotency achieved through:**
1. **No state modifications:** Script only reads the log and sends DELETE requests
2. **Curl `-s` (silent) mode:** Doesn't fail on errors; `|| true` continues on failure
3. **Reverse LIFO order:** DELETE operations happened in reverse creation order (already handled by test runner)
4. **404 safe:** DELETE of already-deleted resource returns 404 (safe)

**Limitations:**
- Doesn't preserve LIFO order if entries added out of sequence
- Assumes deletion is idempotent (may fail for resources with dependencies)
- No verification of cleanup success (should check response status)

---

## 3. Strengths Worth Replicating for bot-core

### 3.1 Zero-Trust Verification (Never Trust Mutation Response Alone)

**Why it matters for bot-core:**
- PostgreSQL queries can have transaction isolation quirks
- Timing-of-check to timing-of-use (TOCTOU) vulnerabilities
- Data transformation bugs between request and persistence

**Pattern to replicate:**
```python
# ALWAYS validate persistence:
# 1. POST to create resource
# 2. GET from authoritative query (SELECT FROM database)
# 3. Compare request body fields vs SELECT result fields

# Don't trust:
# - HTTP response from mutation
# - In-process memory state
# - Cache layers
```

**Example for bot-core:**
```python
@pytest.mark.asyncio
async def test_create_player_zero_trust():
    """
    Create a player via POST /api/v1/players
    Validate it exists in database via GET /api/v1/players/{id}
    """
    # Create
    player_data = {"discord_id": 123456789, "name": "TestPlayer"}
    post_resp = await client.post("/api/v1/players", json=player_data)
    assert post_resp.status_code == 201
    player_id = post_resp.json()["data"]["id"]
    
    # Wait for eventual consistency (or transactions complete)
    await asyncio.sleep(0.1)
    
    # Verify via authoritative GET
    get_resp = await client.get(f"/api/v1/players/{player_id}")
    assert get_resp.status_code == 200
    db_player = get_resp.json()["data"]
    
    # Assert request body fields are in GET response
    assert db_player["discord_id"] == player_data["discord_id"]
    assert db_player["name"] == player_data["name"]
    
    # Schedule cleanup
    cleanup_queue.append(("DELETE", f"/api/v1/players/{player_id}"))
```

### 3.2 Automatic Cleanup with Dependency Ordering (LIFO)

**Why it matters for bot-core:**
- Game resources have deep dependency trees:
  - Guild Config ← Shop Items, Players
  - Players ← Ships, Inventory
  - Ships ← Equipment (weapons, modules)
- Foreign key constraints prevent deletion in wrong order

**Pattern to replicate:**
```python
# Track all resource creation in LIFO order
cleanup_queue = []

def cleanup_all():
    """Delete in reverse creation order (LIFO)"""
    for resource_type, resource_id in reversed(cleanup_queue):
        api_call("DELETE", f"/api/v1/{resource_type}/{resource_id}")

# In tests:
async def test_player_with_ships():
    # Create guild config
    config_resp = await client.post("/api/v1/configs", ...)
    guild_id = config_resp.json()["data"]["id"]
    cleanup_queue.append(("configs", guild_id))
    
    # Create player (depends on guild)
    player_resp = await client.post("/api/v1/players", ...)
    player_id = player_resp.json()["data"]["id"]
    cleanup_queue.append(("players", player_id))
    
    # Create ship (depends on player)
    ship_resp = await client.post(f"/api/v1/players/{player_id}/ships", ...)
    ship_id = ship_resp.json()["data"]["id"]
    cleanup_queue.append(("ships", ship_id))
    
    # Cleanup: ship deleted first, then player, then guild (LIFO)
```

### 3.3 Signal/Exception Handlers for Cleanup Safety

**Why it matters for bot-core:**
- CI/CD timeouts or test runner crashes leave resources
- Keyboard interrupt (Ctrl+C) during testing
- Unhandled exceptions in test setup

**Pattern to replicate:**
```python
import atexit
import signal

# Guarantee cleanup even on abnormal exit
atexit.register(cleanup_all)
signal.signal(signal.SIGINT, _on_signal_cleanup)   # Ctrl+C
signal.signal(signal.SIGTERM, _on_signal_cleanup)  # SIGTERM from CI/CD

def _on_signal_cleanup(signum, frame):
    logging.warning(f"Signal {signum} received — cleaning up")
    cleanup_all()
    sys.exit(1)

sys.excepthook = _handle_uncaught  # Catch unhandled exceptions
```

### 3.4 Audit Logging for Debugging

**Why it matters for bot-core:**
- Understand what resources were created but not cleaned up
- Manually fix database if cleanup fails
- Trace test flow through logs

**Pattern to replicate:**
```json
# Log entry when resource created
{"timestamp": "2026-03-11T12:34:56Z", "action": "create", "resource_type": "player", "resource_id": "42", "test_name": "test_player_creation"}

# Log entry when cleanup executed
{"timestamp": "2026-03-11T12:34:58Z", "action": "cleanup", "resource_type": "player", "resource_id": "42", "method": "DELETE", "uri": "/api/v1/players/42", "result": "success:204"}

# Log entry on cleanup failure
{"timestamp": "2026-03-11T12:34:59Z", "action": "cleanup", "resource_type": "player", "resource_id": "42", "method": "DELETE", "uri": "/api/v1/players/42", "result": "failed-status:404"}
```

### 3.5 Configurable Rate Limiting

**Why it matters for bot-core:**
- Database query timeouts under load
- PostgreSQL connection pool exhaustion
- Prevents cascading failures

**Pattern to replicate:**
```bash
pytest \
  --test-delay 1.0 \           # Delay between tests (seconds)
  --validation-delay 0.5 \     # Delay before validation GET
  --cleanup-delay 0.2          # Delay between cleanup DELETEs

# Environment variables as fallback
export BOT_CORE_TEST_DELAY=1.0
export BOT_CORE_VALIDATION_DELAY=0.5
```

---

## 4. Weaknesses and Improvement Opportunities

### 4.1 Sequential-Only Execution

**Current limitation:**
- All tests run sequentially (one after another)
- No test parallelization
- Long total runtime (~4m for discord-gateway)

**Improvement for bot-core:**
- Use pytest's `-n` flag for parallel execution
- Requires test isolation (separate databases per test, or transaction rollback)
- Can reduce 4m runtime to <1m with 4 workers

### 4.2 No pytest Integration

**Current limitation:**
- Standalone Python script, not a pytest test suite
- No pytest plugins (coverage, markers, xfail)
- Cannot integrate with CI/CD naturally
- Hard to exclude tests (would require code edits)

**Improvement for bot-core:**
```python
# Use pytest classes and fixtures instead
@pytest.mark.asyncio
class TestPlayers:
    @pytest.fixture(autouse=True)
    async def cleanup(self):
        yield
        # Cleanup happens here
        await cleanup_all()
    
    async def test_create_player(self, async_client):
        # Test code here
        pass

# Run: pytest tests/live_fire/test_players.py -v --tb=short
```

### 4.3 Hard-Coded IDs

**Current limitation:**
```python
DEFAULT_GUILD_ID: int = 711548456019296289  # Hard-coded
DEFAULT_USER_ID: int = 640882072516427787
DEFAULT_BOT_ID: int = 721309941369012284
```

**Improvement for bot-core:**
- Use environment variables (`.env.test`)
- Create test fixtures for IDs
- Support dynamic test data (create guild/player on the fly)

### 4.4 No Authentication Testing

**Current limitation:**
- Assumes all endpoints are public or authentication is disabled
- `run_forbidden()` just marks tests as skipped, doesn't actually test auth

**Improvement for bot-core:**
```python
@pytest.mark.asyncio
async def test_create_player_with_auth():
    # Test with valid token
    headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
    resp = await client.post("/api/v1/players", headers=headers, ...)
    assert resp.status_code == 201
    
    # Test with invalid token
    bad_headers = {"Authorization": "Bearer invalid"}
    resp = await client.post("/api/v1/players", headers=bad_headers, ...)
    assert resp.status_code == 401

@pytest.mark.asyncio
async def test_create_player_no_auth():
    # Test without token (if endpoint requires it)
    resp = await client.post("/api/v1/players", ...)
    assert resp.status_code == 401  # Unauthorized
```

### 4.5 Sync-Only HTTP

**Current limitation:**
```python
with httpx.Client() as client:
    resp = client.request(...)  # Blocking
```

**Improvement for bot-core:**
```python
# Use httpx async client
async with httpx.AsyncClient() as client:
    resp = await client.request(...)  # Non-blocking, can parallelize
```

---

## 5. Recommended bot-core Live-Fire Testing Approach

### 5.1 Modernized Architecture

**Instead of standalone script, use pytest + async:**

```
services/bot-core/tests/
├── conftest.py                  # Fixtures, async setup/teardown
├── live_fire/
│   ├── __init__.py
│   ├── test_players.py          # Player creation, validation, cleanup
│   ├── test_ships.py            # Ship creation, inventory, equipment
│   ├── test_shops.py            # Shop items, pricing
│   ├── test_config.py           # Guild config
│   └── conftest.py              # Live-fire specific fixtures
└── ...other test dirs...
```

### 5.2 Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **pytest not standalone script** | Native CI/CD integration, plugin ecosystem, parametrization |
| **httpx async client** | Non-blocking, enables parallelization, faster execution |
| **Zero-trust validation** | Catches data persistence bugs, validates database (not just API) |
| **LIFO cleanup with dependency ordering** | Respects foreign key constraints |
| **Transaction rollback alternative** | For isolation, can rollback test db changes instead of DELETEs |
| **JSON audit logging** | Debugging and manual recovery |
| **test- prefix for all data** | Visual identification, easy manual cleanup |
| **Environment variable configuration** | CI/CD friendly, no code edits needed |
| **Structured logging** | Parseable logs for CI/CD integration |

### 5.3 File Structure and Example Implementation

#### 5.3.1 conftest.py (Root Level)

```python
# services/bot-core/tests/conftest.py
import asyncio
import os
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, AsyncGenerator
import pytest
import httpx
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
import yaml

# =====================================================================
# Configuration & Logging
# =====================================================================

LOG_DIR = "/tmp/bot-core-tests"
os.makedirs(LOG_DIR, exist_ok=True)

AUDIT_LOG_FILE = f"{LOG_DIR}/live_fire_audit.jsonl"
CLEANUP_LOG_FILE = f"{LOG_DIR}/live_fire_cleanup.jsonl"

# Setup root logger
logger = logging.getLogger("bot_core_tests")
logger.setLevel(logging.DEBUG)

# File handler for audit log
audit_handler = logging.FileHandler(AUDIT_LOG_FILE, mode="w")
audit_handler.setLevel(logging.DEBUG)
audit_handler.setFormatter(logging.Formatter("%(message)s"))

# Console handler with colors
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter(
    "%(asctime)s | %(levelname)-5s | %(message)s"
))

logger.addHandler(audit_handler)
logger.addHandler(console_handler)

# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture(scope="session")
def event_loop():
    """Create async event loop for session"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="session")
async def test_db_engine():
    """Create test database engine"""
    db_url = os.getenv(
        "BOT_CORE_TEST_DB_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/bountybot_test"
    )
    engine = create_async_engine(
        db_url,
        poolclass=NullPool,  # No connection pooling for test isolation
        echo=False
    )
    
    # Create test tables
    async with engine.begin() as conn:
        # await conn.run_sync(Base.metadata.drop_all)
        # await conn.run_sync(Base.metadata.create_all)
        pass
    
    yield engine
    
    await engine.dispose()

@pytest.fixture
async def async_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Create async HTTP client for API testing"""
    base_url = os.getenv("BOT_CORE_TEST_URL", "http://localhost:8000")
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        yield client

@pytest.fixture
async def db_session(test_db_engine):
    """Create test database session with rollback"""
    async with AsyncSession(test_db_engine) as session:
        yield session
        # Rollback all changes after test
        await session.rollback()

@pytest.fixture
def cleanup_queue() -> List[Dict[str, Any]]:
    """LIFO cleanup queue for test resources"""
    return []

@pytest.fixture
async def cleanup_fixture(cleanup_queue, async_client):
    """Cleanup all created resources after test"""
    yield
    
    # Execute cleanup in LIFO order
    for entry in reversed(cleanup_queue):
        method, uri = entry["method"], entry["uri"]
        body = entry.get("body")
        
        try:
            resp = await async_client.request(method, uri, json=body)
            result = f"{method}:{resp.status_code}"
        except Exception as e:
            result = f"error:{str(e)}"
        
        # Log cleanup result
        audit_log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "cleanup",
            "resource_type": entry["resource_type"],
            "resource_id": entry["resource_id"],
            "method": method,
            "uri": uri,
            "result": result
        }
        logger.debug(yaml.dump(audit_log_entry))
```

#### 5.3.2 live_fire/conftest.py

```python
# services/bot-core/tests/live_fire/conftest.py
import pytest
from datetime import datetime, timezone
import yaml

@pytest.fixture
def test_prefix():
    """Prefix for all test-created resources"""
    return f"test-{int(datetime.now(timezone.utc).timestamp())}-"

@pytest.fixture
def audit_logger(caplog):
    """Logger for audit trail"""
    import logging
    return logging.getLogger("live_fire_audit")

def log_resource_created(audit_logger, resource_type: str, resource_id, test_name: str):
    """Log resource creation to audit trail"""
    audit_logger.debug(yaml.dump({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "create",
        "resource_type": resource_type,
        "resource_id": resource_id,
        "test_name": test_name
    }))
```

#### 5.3.3 live_fire/test_players.py (Example Test Suite)

```python
# services/bot-core/tests/live_fire/test_players.py
import pytest
import httpx
from typing import Dict, Any
from datetime import datetime, timezone
import yaml

class TestPlayersLiveFire:
    """Live-fire tests for player API with zero-trust validation"""
    
    @pytest.fixture(autouse=True)
    async def setup_teardown(self, cleanup_fixture):
        """Auto-cleanup after each test"""
        yield cleanup_fixture
    
    async def test_create_player_zero_trust(
        self,
        async_client: httpx.AsyncClient,
        cleanup_queue: list,
        test_prefix: str,
        audit_logger
    ):
        """
        Test player creation with zero-trust verification:
        1. POST /api/v1/players with test data
        2. GET /api/v1/players/{id} to verify persistence
        3. Assert request fields exist in GET response
        """
        # Test data
        player_data = {
            "discord_id": 123456789,
            "name": f"{test_prefix}Player1"
        }
        
        # Step 1: POST to create player
        post_resp = await async_client.post(
            "/api/v1/players",
            json=player_data,
            headers={"Content-Type": "application/json"}
        )
        assert post_resp.status_code == 201, f"POST failed: {post_resp.text}"
        
        # Extract created player ID from response
        post_json = post_resp.json()
        player_id = post_json.get("data", post_json).get("id")
        assert player_id is not None, "No ID in response"
        
        # Log creation
        audit_logger.info(yaml.dump({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "create",
            "resource_type": "player",
            "resource_id": player_id,
            "test_name": "test_create_player_zero_trust"
        }))
        
        # Schedule cleanup
        cleanup_queue.append({
            "resource_type": "player",
            "resource_id": player_id,
            "method": "DELETE",
            "uri": f"/api/v1/players/{player_id}"
        })
        
        # Step 2: GET to verify persistence (zero-trust)
        await asyncio.sleep(0.1)  # Wait for eventual consistency
        get_resp = await async_client.get(f"/api/v1/players/{player_id}")
        assert get_resp.status_code == 200, f"GET failed: {get_resp.text}"
        
        # Step 3: Validate request body fields are in GET response
        db_player = get_resp.json().get("data", get_resp.json())
        assert db_player["discord_id"] == player_data["discord_id"]
        assert db_player["name"] == player_data["name"]
    
    async def test_player_with_ships_dependency_ordering(
        self,
        async_client: httpx.AsyncClient,
        cleanup_queue: list,
        test_prefix: str
    ):
        """
        Test that cleanup respects dependency ordering (LIFO):
        1. Create guild config
        2. Create player (depends on guild)
        3. Create ship (depends on player)
        4. Verify cleanup happens in reverse: ship → player → guild
        """
        # Create guild config
        guild_data = {"name": f"{test_prefix}Guild1"}
        guild_resp = await async_client.post("/api/v1/configs", json=guild_data)
        guild_id = guild_resp.json()["data"]["id"]
        cleanup_queue.append({
            "resource_type": "guild",
            "resource_id": guild_id,
            "method": "DELETE",
            "uri": f"/api/v1/configs/{guild_id}"
        })
        
        # Create player (depends on guild)
        player_data = {
            "discord_id": 987654321,
            "name": f"{test_prefix}Player2",
            "guild_id": guild_id
        }
        player_resp = await async_client.post("/api/v1/players", json=player_data)
        player_id = player_resp.json()["data"]["id"]
        cleanup_queue.append({
            "resource_type": "player",
            "resource_id": player_id,
            "method": "DELETE",
            "uri": f"/api/v1/players/{player_id}"
        })
        
        # Create ship (depends on player)
        ship_data = {
            "name": f"{test_prefix}Ship1",
            "player_id": player_id
        }
        ship_resp = await async_client.post(
            f"/api/v1/players/{player_id}/ships",
            json=ship_data
        )
        ship_id = ship_resp.json()["data"]["id"]
        cleanup_queue.append({
            "resource_type": "ship",
            "resource_id": ship_id,
            "method": "DELETE",
            "uri": f"/api/v1/ships/{ship_id}"
        })
        
        # Verify all created
        assert guild_id is not None
        assert player_id is not None
        assert ship_id is not None
        
        # Cleanup will execute in LIFO order:
        # 1. DELETE /api/v1/ships/{ship_id} (last created, first deleted)
        # 2. DELETE /api/v1/players/{player_id} (second)
        # 3. DELETE /api/v1/configs/{guild_id} (first created, last deleted)
```

#### 5.3.4 pytest.ini Configuration

```ini
# services/bot-core/pytest.ini
[pytest]
# Async marker
asyncio_mode = auto
markers =
    live_fire: Live-fire integration tests against real database
    slow: Slow tests
    requires_auth: Tests that require authentication
    
# Test discovery
python_files = test_*.py
python_classes = Test*
python_functions = test_*

# Output
addopts = 
    -v
    --tb=short
    --strict-markers
    -p no:warnings
    
testpaths = tests
```

### 5.4 Running Live-Fire Tests

```bash
# Run all live-fire tests
pytest tests/live_fire/ -v

# Run with specific markers
pytest -m live_fire -v

# Run with pytest-xdist parallelization (4 workers)
pytest tests/live_fire/ -n auto -v

# Run with coverage
pytest tests/live_fire/ --cov=src --cov-report=html

# Run specific test
pytest tests/live_fire/test_players.py::TestPlayersLiveFire::test_create_player_zero_trust -v

# Run with custom timeouts
pytest tests/live_fire/ --test-delay 2.0 --validation-delay 1.0

# Run with verbose logging
pytest tests/live_fire/ -v --log-cli-level=DEBUG
```

---

## 6. Resource Dependency Tree for bot-core

### 6.1 Comprehensive Dependency Map

```
Guild Config
    │
    ├── Discord Message
    ├── Announcements
    │   └── Time Announcements
    │
    ├── Shop Config
    │   ├── Shop Items
    │   │   └── Item (base game data)
    │   │
    │   └── Criminal Config
    │       └── Criminal (base game data)
    │
    └── Players
        ├── Discord User (external)
        │
        ├── Player Inventory
        │   ├── Item (base game data)
        │   └── Quantity tracking
        │
        ├── Player Ship
        │   ├── Ship (base game data — immutable)
        │   │
        │   └── Ship Equipment
        │       ├── Module (base game data)
        │       ├── Primary Weapon (base game data)
        │       └── Secondary Weapon (base game data)
        │
        └── Player Skin
            └── Skin (cosmetic — base game data)
```

### 6.2 Cleanup Order (LIFO)

**Creation sequence:**
1. Guild Config (created first)
2. Players (depends on Guild Config)
3. Player Inventory (depends on Players)
4. Player Ships (depends on Players)
5. Ship Equipment (depends on Ships)

**Cleanup sequence (LIFO — reverse creation):**
```
5. DELETE /api/v1/ships/{ship_id}/equipment/{equipment_id}  ← Last created, first deleted
4. DELETE /api/v1/ships/{ship_id}
3. DELETE /api/v1/players/{player_id}/inventory/{inventory_id}
2. DELETE /api/v1/players/{player_id}
1. DELETE /api/v1/configs/{guild_config_id}                ← First created, last deleted
```

### 6.3 Foreign Key Constraints

```sql
-- Base game data (immutable in tests)
ships(id)                          ← Ship master data
modules(id)                        ← Module master data
primary_weapons(id)                ← Weapon master data
secondary_weapons(id)              ← Weapon master data
items(id)                          ← Item master data
criminals(id)                      ← Criminal master data

-- Test-mutable resources
guild_configs(id)

players(id)
    ├── FOREIGN KEY guild_id → guild_configs(id)
    └── FOREIGN KEY discord_id (external, not constrained)

player_ships(id)
    ├── FOREIGN KEY player_id → players(id)
    └── FOREIGN KEY ship_id → ships(id)

ship_equipment(id)
    ├── FOREIGN KEY ship_id → player_ships(id)
    ├── FOREIGN KEY module_id → modules(id)
    ├── FOREIGN KEY primary_weapon_id → primary_weapons(id)
    └── FOREIGN KEY secondary_weapon_id → secondary_weapons(id)

player_inventory(id)
    ├── FOREIGN KEY player_id → players(id)
    └── FOREIGN KEY item_id → items(id)

guild_shop_config(id)
    ├── FOREIGN KEY guild_id → guild_configs(id)
    └── FOREIGN KEY criminal_id → criminals(id)

shop_items(id)
    ├── FOREIGN KEY guild_shop_id → guild_shop_config(id)
    └── FOREIGN KEY item_id → items(id)
```

### 6.4 Example Cleanup Function for bot-core

```python
# services/bot-core/tests/live_fire/cleanup.py
import asyncio
from typing import List, Dict, Any
from datetime import datetime, timezone
import httpx

async def cleanup_resources(
    async_client: httpx.AsyncClient,
    cleanup_queue: List[Dict[str, Any]]
):
    """
    Execute cleanup in LIFO order (reverse creation).
    
    Respects foreign key constraints:
    - ship_equipment must be deleted before ships
    - player_inventory must be deleted before players
    - players must be deleted before guild_configs
    """
    cleanup_order_priority = {
        "ship_equipment": 5,    # Delete first (highest priority)
        "player_inventory": 4,
        "player_ships": 3,
        "players": 2,
        "guild_configs": 1      # Delete last (lowest priority)
    }
    
    # Sort by priority (higher first)
    sorted_queue = sorted(
        reversed(cleanup_queue),  # LIFO
        key=lambda x: cleanup_order_priority.get(x["resource_type"], 0),
        reverse=True
    )
    
    results = []
    for entry in sorted_queue:
        method, uri = entry["method"], entry["uri"]
        resource_type, resource_id = entry["resource_type"], entry["resource_id"]
        
        try:
            resp = await async_client.request(method, uri)
            result = f"{method}:{resp.status_code}"
            success = resp.status_code in (200, 204)
        except Exception as e:
            result = f"error:{str(e)}"
            success = False
        
        results.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "cleanup",
            "resource_type": resource_type,
            "resource_id": resource_id,
            "method": method,
            "uri": uri,
            "result": result,
            "success": success
        })
        
        # Small delay between cleanup operations
        await asyncio.sleep(0.2)
    
    return results
```

---

## 7. Implementation Roadmap for bot-core

### Phase 1: Foundation (Week 1-2)

- [ ] Create `tests/live_fire/` directory structure
- [ ] Implement root `conftest.py` with async fixtures
- [ ] Set up pytest configuration
- [ ] Create async HTTP client fixture with proper error handling

### Phase 2: Core Testing Framework (Week 2-3)

- [ ] Implement zero-trust validation helper functions
- [ ] Create LIFO cleanup queue mechanism
- [ ] Add audit logging (JSON-lines format)
- [ ] Implement signal handlers for guaranteed cleanup
- [ ] Add test prefix naming convention

### Phase 3: First Test Suite (Week 3-4)

- [ ] Implement Player tests (create, update, delete with zero-trust)
- [ ] Implement Ship tests (dependency on Player)
- [ ] Test cleanup ordering respects foreign keys
- [ ] Test rate limiting and delays

### Phase 4: Expansion & CI/CD (Week 4-5)

- [ ] Add tests for remaining resources (Inventory, Equipment, Config, Shops)
- [ ] Add authentication tests (if applicable)
- [ ] Integrate with CI/CD pipeline
- [ ] Add parallel test execution (pytest-xdist)
- [ ] Generate coverage reports

### Phase 5: Documentation & Tooling (Week 5)

- [ ] Document test patterns and best practices
- [ ] Create manual cleanup script (similar to test-cleanup.sh)
- [ ] Add GitHub Actions workflow
- [ ] Create developer guide for writing new live-fire tests

---

## 8. Conclusion and Recommendations

### Key Takeaways

1. **Zero-trust validation** is the most important pattern to adopt — it catches subtle data persistence bugs that status code checking misses
2. **LIFO cleanup with dependency ordering** prevents foreign key errors and ensures test isolation
3. **Audit logging and signal handlers** guarantee cleanup even in failure scenarios
4. **pytest + async + httpx** modernizes the approach for better CI/CD integration and performance
5. **Explicit resource dependency trees** are essential for bot-core due to complex game entity relationships

### Recommended Next Steps

1. **Start with Phase 1-2:** Get the foundation in place (conftest, fixtures, cleanup queue)
2. **Implement one full test suite:** Player creation/deletion with full cleanup validation
3. **Validate cleanup ordering:** Ensure DELETE operations respect foreign key constraints
4. **Integrate with CI/CD:** Add to GitHub Actions, set thresholds for failure
5. **Iterate:** Add more test suites, improve patterns based on lessons learned

### Critical Success Factors

| Factor | How to Achieve |
|--------|----------------|
| **Reliability** | Zero-trust validation + signal handlers + atexit hooks |
| **Maintainability** | Use pytest, conftest fixtures, reusable patterns |
| **Performance** | Async HTTP client, proper pooling, rate limiting configuration |
| **Debuggability** | JSON audit logs, structured logging, explicit error messages |
| **CI/CD Integration** | Environment variable configuration, standard pytest markers |

---

## Appendix: Quick Reference

### Discord-Gateway Pattern Summary

```python
# 1. Zero-trust validation
POST /api/v1/resource → GET /api/v1/resource/{id} → validate_object(request_body, get_response)

# 2. LIFO cleanup
CLEANUP_QUEUE.append({"resource_id": id, "delete_uri": uri})
cleanup_all()  # for item in reversed(CLEANUP_QUEUE): DELETE

# 3. Audit logging
safe_append_cleanup({"action": "create|cleanup", "result": "success|failed", ...})

# 4. Signal handlers
atexit.register(cleanup_all)
signal.signal(signal.SIGINT, _on_signal)
sys.excepthook = _handle_uncaught
```

### Bot-Core Recommended Pattern

```python
# conftest.py
@pytest.fixture
async def cleanup_fixture(cleanup_queue, async_client):
    yield
    for entry in reversed(cleanup_queue):
        await async_client.request(entry["method"], entry["uri"])

# test_players.py
@pytest.mark.asyncio
async def test_create_player(async_client, cleanup_queue):
    # POST
    resp = await async_client.post("/api/v1/players", json=data)
    player_id = resp.json()["data"]["id"]
    cleanup_queue.append({"method": "DELETE", "uri": f"/api/v1/players/{player_id}"})
    
    # GET (zero-trust)
    resp = await async_client.get(f"/api/v1/players/{player_id}")
    assert resp.json()["data"]["discord_id"] == data["discord_id"]
```

---

**Document prepared by:** Bot-Core Development Team  
**Last updated:** 2026-03-11  
**Status:** Final - Ready for Implementation
