# AGENTS.md - src/shared

Cross-service shared library as seen from inside bot-core. Three files live
here: `__init__.py` (empty), `bblogger.py`, `http_retry.py`.

---

## Relationship to `services/shared/` — READ BEFORE EDITING

The **canonical, git-tracked source** is the top-level `/proj/services/shared/`
directory. This directory (`services/bot-core/src/shared/`) is an **untracked
working-tree mirror** of it (currently byte-identical) that exists so host-side
runs and tests resolve `shared.*` imports the same way the container does:

- Every service image copies the canonical dir to the same in-container path:
  `COPY ./services/shared /app/src/shared` (bot-core `Dockerfile` line 94;
  discord-gateway line 80; blender-service line 147). Inside the container,
  `shared` is therefore a sibling package of `api`/`services`/`persist`/`utils`,
  and `from shared.bblogger import get_logger` just works.
- On the host, `tests/conftest.py` puts `src/` on `sys.path`, installs a
  **MagicMock-based stub** for `shared` / `shared.bblogger` before any app
  import, and then loads the **real** `shared.http_retry` from
  `src/shared/http_retry.py` via `importlib` (so retry-policy tests such as
  `tests/test_d9t1_http_retry_helper.py` exercise the actual code).

**Edit rule**: make changes in `/proj/services/shared/` and mirror them here
byte-for-byte (or vice versa) — the two copies must not drift, because the
image builds from `services/shared/` while host tests read `src/shared/`.

---

## bblogger.py — logging helper

Tiny, dependency-free. Single public function:

```python
from shared.bblogger import get_logger
flogger = get_logger(__name__)   # returns a _SafeLogger (LoggerAdapter)
```

- Adds a custom `TRACE` level (numeric 5, below DEBUG) and a `.trace()` method.
- The returned `_SafeLogger` adapter scrubs `\r`/`\n` from the message and
  %-format args before logging — centralised log-injection sanitisation
  (CodeQL `py/log-injection`); call sites need no changes.
- Env config: `LOG_LEVEL` (default `INFO`), `LOG_FILE` (default `app.log`),
  `LOG_TO_FILE` (default `true`).
- Handlers: colorised stdout always; `RotatingFileHandler` (5 MB × 3 backups)
  when file logging is on. If the log directory/file cannot be created, file
  logging is disabled gracefully instead of raising.
- Idempotent per logger name: returns early if handlers are already attached.

---

## http_retry.py — transient-only HTTP retry

Tenacity-based (v9+) retry helper for idempotent `httpx` calls. Back-off is
*Full Jitter* exponential: `uniform(0, min(10s, 1 * 2^attempt))`.

**Policy (LOCKED — X5 constraint):**
- 3 total attempts (1 initial + 2 retries) — `TRANSIENT_STOP`.
- Retries ONLY on transient failures: httpx connect/read/write/pool timeouts,
  connect/network/protocol errors, and `HTTPStatusError` with status in
  `_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}`.
- NEVER retries other 4xx (400/401/403/404/409/422 …) or non-httpx exceptions.

Public API:
- `with_transient_retry(fn, *args, **kwargs)` — awaits `fn(...)` under the
  policy; calls `raise_for_status()` on `httpx.Response` results so bad-status
  responses feed the retry predicate; re-raises the last exception on
  exhaustion (`reraise=True`).
- `make_transient_retry(**kwargs) -> AsyncRetrying` — for the
  `async for attempt in ...` form; extra kwargs (e.g. `before_sleep`) forwarded.
- Policy pieces: `TRANSIENT_WAIT`, `TRANSIENT_STOP`, `TRANSIENT_RETRY`,
  `_is_transient`, `_RETRYABLE_STATUSES`; re-exports `retry_if_exception_type`.

**X5 usage constraint**: apply ONLY to idempotent cache-set POSTs and warm
GETs. NEVER wrap announce/upload POSTs — retrying those double-posts to
Discord.

---

*Last updated: 2026-06-11 — initial version.*
