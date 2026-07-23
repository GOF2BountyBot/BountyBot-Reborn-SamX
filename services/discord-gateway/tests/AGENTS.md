# AGENTS.md - discord-gateway/tests

Testing conventions for the discord-gateway service test suite.

---

## Cross-Service HTTP Tests: Use respx, Not Mock Objects

### The Rule

Any test that exercises code making HTTP calls to **bot-core** or **blender-service**
must use **`respx`** to mock those calls. Direct patching of `http_client.get`,
`http_client.post`, or similar as `AsyncMock(return_value=...)` is **forbidden** for
preload and any other gateway→external HTTP paths.

```python
# WRONG — forbidden for cross-service calls
cog.http_client.get = AsyncMock(return_value=MagicMock(json=lambda: [...]))

# CORRECT — use respx
import httpx, respx

with respx.mock(assert_all_called=True) as mock_router:
    mock_router.get("http://bot-core:8000/api/v1/about/categories/ship/objects").mock(
        return_value=httpx.Response(200, json=[{"name": "Eagle", "id": 1}])
    )
    asyncio.run(cog._preload_ship_skins())
```

### Why This Matters

The `AsyncMock` pattern is **tautological**: it only tests "does the code parse
the mock's return value correctly?" — it never asks "does the real server actually
serve this URL/method?" Bugs in the URL, HTTP method, or response shape pass
silently because the mock is pre-loaded with the expected shape.

**B.33 incident (2026-04-29):** `adminCog._preload_static_catalogs` shipped with
three simultaneous bugs — wrong HTTP method (GET vs POST), nonexistent URL
(`/about/ships`), and wrong response shape — all masked by 277 lines of tests that
mocked `http_client.get` with canned `list[dict]` responses. Every cold boot
produced 8 minutes of error log noise and left item/ship autocomplete empty. A
single respx test asserting the exact URL would have caught all three bugs before
merge.

### When You Must Use respx

- Any `_preload_*` method that calls bot-core or blender-service
- Any test of a cog command that makes an HTTP call to bot-core or blender-service
  where the correctness of the URL or method is not otherwise verified
- Integration-style tests that assert end-to-end behavior through real HTTP
  request/response shapes

### Fixture Pattern for Cogs with Replaced http_client

Some test fixtures replace `cog.http_client` with a `MagicMock` for general
command tests. Preload tests must reinstall a real `httpx.AsyncClient` so respx
can intercept it. **Always register a finalizer to close the client** — leaking
live `httpx.AsyncClient` instances causes resource warnings across test runs:

```python
def _with_real_client(self, cog, request):
    """Replace cog.http_client with a real httpx.AsyncClient for respx interception.

    Registers a pytest finalizer to close the client after the test so no
    httpx.AsyncClient instances are leaked between tests.
    """
    import httpx

    cog.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    request.addfinalizer(lambda: asyncio.run(cog.http_client.aclose()))
    return cog
```

Call this helper (passing the pytest `request` fixture) at the start of any
preload test that needs respx. The finalizer runs automatically after the test
completes, regardless of pass or fail.

### Parent Principle

This rule is the gateway-service application of the "max 2 mocks per test" and
"prefer real objects with deterministic inputs" policy from
`services/bot-core/tests/AGENTS.md`. Mocking the transport layer is permissible
only through respx, which enforces URL and method matching as first-class
assertions.

---

## General Test Conventions

See `services/discord-gateway/AGENTS.md` (service-level) for the full test
structure, fixture patterns, and coverage requirements.

---

*Added: 2026-04-29 (B.33 remediation)*
