# B.30 Recon — `PUT /api/v1/jobs/{job_id}` silently wipes payload on unrecognized body shape

**Recon date**: 2026-04-28  
**Method**: read-only source inspection  
**Status**: complete

---

## 1. Route Handler

**File**: `services/bot-core/src/api/routers/scheduler.py`  
**Lines**: 161–183

```python
@router.put("/jobs/{job_id}")
async def update_job(req: Request, job_id: str, update: UpdateJob):
    ...
    new_args = [job_id, update.payload]          # line 175
    sched.modify_job(job_id, args=new_args)       # line 177
    return {"status": "updated", "job_id": job_id}
```

The route has no error handling for the schema-mismatch case because Pydantic accepts the body silently (see section 2). The route simply uses `update.payload` directly.

---

## 2. Request Schema

**File**: `services/bot-core/src/api/schemas/scheduler_schema.py`  
**Lines**: 38–44

```python
class UpdateJob(BaseModel):
    """
    Model for updating the 'payload' of an existing job.
    Matches the shape of the original payload passed at scheduling time.
    """

    payload: dict | None = {}
```

**Key characteristics**:
- **ONE declared field**: `payload: dict | None`  
- **Default value**: `{}` (empty dict) — the direct source of the data-wipe behavior  
- **No `model_config`**: `scheduler_schema.py` is the **only** schema module in `services/bot-core/src/api/schemas/` that sets no `model_config` at all
- **No explicit `extra` policy**: Pydantic v2 default is `extra="ignore"` — unrecognized fields are silently dropped without error
- **No required field**: because `payload` has a default, any body — including `{}`, `{"args": [...]}`, or even an empty body — parses without error

---

## 3. End-to-End Flow: Wrong-body Wipe Scenario

When caller sends `{"args": ["bounty_spawn_default", {"job_type": "bounty_spawn_orchestrate"}]}`:

| Step | Location | What happens |
|------|----------|-------------|
| 1 | FastAPI/Pydantic | Receives request body `{"args": [...]}` |
| 2 | `UpdateJob.__init__` | `args` is not a declared field → Pydantic `extra="ignore"` silently drops it |
| 3 | `UpdateJob.__init__` | `payload` not provided → uses default: `{}` |
| 4 | `scheduler.py:175` | `new_args = [job_id, {}]` |
| 5 | `scheduler.py:177` | `sched.modify_job(job_id, args=["bounty_spawn_default", {}])` |
| 6 | APScheduler | Replaces the job's `args` tuple with `("bounty_spawn_default", {})` |
| 7 | Response | HTTP 200 `{"status": "updated", "job_id": "bounty_spawn_default"}` |
| 8 | Subsequent GET | `args = ["bounty_spawn_default", {}]` — payload wipe confirmed |

**Source of `{}`**: line 44, `payload: dict | None = {}`. The mutable-default-in-Pydantic is safe (Pydantic creates a new dict per instance), but the value `{}` is semantically dangerous when used as the UPDATE payload.

---

## 4. Extra-field Policy Sweep

### 4a. Scheduler router — all request models

| Schema | File:lines | Required fields? | Extra policy | Vulnerable? |
|--------|------------|-----------------|--------------|-------------|
| `UpdateJob` | `scheduler_schema.py:38-44` | None | `extra="ignore"` (default) | **YES** — all fields optional |
| `OneTimeJob` | `scheduler_schema.py:8-23` | None (all optional) | `extra="ignore"` (default) | **Partial** — route checks `run_at`/`delay_seconds` (line 119-121), returns 400 if both absent |
| `RecurringJob` | `scheduler_schema.py:26-28` | `cron: str` (required) | `extra="ignore"` (default) | **No** — missing `cron` → Pydantic 422 |

**Unique vulnerability**: `UpdateJob` is the only scheduler request model where wrong-field input succeeds completely end-to-end without any guard. `OneTimeJob` has a business-logic guard (400 if no timing info), `RecurringJob` has a required field (422 if missing).

### 4b. Other bot-core request schemas

Exhaustive search of `services/bot-core/src/api/schemas/` confirms:
- **No schema sets `extra="forbid"` or `extra="allow"`** — `grep -r "extra=" schemas/` returns no matches
- All other request schemas that use `model_config` set only `from_attributes=True` (ORM mapping for responses, not requests)
- All other request schemas have at **least one required field** (e.g., `guild_id`, `player_id`, `credits`, `item_type`) — sending a completely wrong body shape would trigger Pydantic 422 on the missing required field before any business logic runs

**Example safe schemas** (required field guards):
- `BountyCheckRequest`: `player_id: int` (required), `system_name: str` (required)
- `UpdatePlayerCreditsRequest`: `player_id: int = Field(ge=1)` (required), `credits: int = Field(ge=0)` (required)
- `AdminGiveItemRequest`: 5 fields, all required or typed Literal
- `InitializeGuildRequest`: `guild_id: int = Field(ge=1)` (required)

**Conclusion**: `UpdateJob` is uniquely vulnerable among all bot-core request schemas because it is the only write-endpoint model with zero required fields and a semantically destructive default value.

---

## 5. Gateway Cog Wire Shape

**File**: `services/discord-gateway/src/cogs/schedulerCog.py`  
**Lines**: 241–244

```python
resp = await self.http_client.put(
    f"{api_base}/jobs/{job_id}",
    json={"payload": payload},   # ← CORRECT field name
    timeout=10,
)
```

The `/scheduler_update` slash command:
1. Takes `payload_json: str` parameter from Discord
2. Parses it with `json.loads(payload_json)` (line 232) — if this fails, returns JSON parse error to user
3. Sends `{"payload": payload}` — the **correct** field name

**The gateway cog is NOT affected**. It sends the correct field name. The defect is only triggered by direct API calls using an incorrect field name.

---

## 6. Cross-Reference with B.32

### B.30 — `PUT /api/v1/jobs/{job_id}` (bot-core)

| Aspect | Detail |
|--------|--------|
| Mechanism | Pydantic `extra="ignore"` silently drops unknown fields; `payload` defaults to `{}` |
| Layer | Schema validation layer |
| Consequence | **Destructive write** — existing job payload replaced with `{}` |
| Severity | 🟠 high |
| Visibility | Zero — HTTP 200 with `{"status": "updated"}` |

### B.32 — `PUT /api/v1/config/render` (blender-service)

| Aspect | Detail |
|--------|--------|
| Mechanism | Route accepts raw `dict`, service `update()` uses `hasattr` to filter keys |
| Layer | Service layer (`render_config_service.py:76-96`) |
| Consequence | **Silent no-op** — unknown key ignored, no field changed, success returned |
| Severity | 🟡 medium |
| Visibility | Zero — HTTP 200 with full config dict (looked like success) |

**Same class**: both are write endpoints that accept unrecognized input without validation error.  
**Different mechanisms**: Pydantic schema (B.30) vs service-layer `hasattr` filter (B.32).  
**Different consequences**: destructive write (B.30) vs misleading no-op (B.32).

The blender-service `update()` method even logs a WARNING at line 91: `"Config update: N unknown key(s) ignored: [...]"` — but this warning is not surfaced to the caller. B.32 has better internal logging than B.30 (B.30 logs nothing about the mismatch).

---

## 7. Open Questions

1. **Intentional silent-ignore design?** The blender-service router docstring explicitly says _"Only valid field names are accepted; unknown keys are silently ignored"_ — this is intentional design for blender. Was `UpdateJob` also intentionally lenient, or was it just an oversight?
2. **APScheduler serialization of `{}`**: When `args = [job_id, {}]` is persisted in `apscheduler_jobs` (PostgreSQL), does APScheduler serialize `{}` as an empty pickle? Does it compare against the previous args and short-circuit if equal? (Should be no; `modify_job` always writes.)
3. **Other callers of PUT /jobs**: Are there any executor modules that call `PUT /api/v1/jobs/{job_id}` directly (not via the scheduler cog)? A search for `PUT` calls to the scheduler API in executor files would confirm.

---

## 8. Recommended Fix

**Scope**: surgical — 1–3 line change.

Add `model_config = ConfigDict(extra="forbid")` to `UpdateJob`:

```python
from pydantic import BaseModel, ConfigDict

class UpdateJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: dict | None = {}
```

With `extra="forbid"`, sending `{"args": [...]}` would return HTTP 422 Unprocessable Entity immediately, before any APScheduler interaction.

**Defense-in-depth** (optional): apply the same to `OneTimeJob` and `RecurringJob` — the 400/422 guards they already have would fire before the extra-field check, but explicit `extra="forbid"` is cleaner.

**Alternative** (also valid): make `payload` a required field (remove `= {}`). Callers would then be forced to explicitly provide it. The gateway cog already does this correctly. This is more breaking for callers that omit payload intentionally, but aligns better with explicit-is-better-than-implicit.

---

*Recon completed: 2026-04-28 by developer (read-only investigation)*
