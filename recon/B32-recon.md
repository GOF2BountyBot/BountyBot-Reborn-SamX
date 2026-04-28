# B.32 Recon — `/render_config action:set setting:samples` Silent No-Op

**Date**: 2026-04-28  
**Investigator**: developer (read-only)  
**Defect**: B.32 · 🟡 medium  
**Symptom**: `/render_config action:set setting:samples value:64` returns `"✅ Updated samples = 64"` but no field is mutated.

---

## 1. Investigation Scope

Empirical, read-only code inspection at HEAD. No source changes. Goal: trace the request from Discord slash command → cog → blender-service router → config service → config object, and determine whether any mutation occurred.

---

## 2. Code Path Inventory

### 2.1 Cog — AdminCog (`services/discord-gateway/src/cogs/adminCog.py`)

#### Startup: `_preload_render_settings()` — lines 78–95
```python
async def _preload_render_settings(self):
    blender_base = os.getenv("BLENDER_API_BASE_URL", "http://blender-service:8001/api/v1")
    await self.bot.wait_until_ready()
    for attempt in range(3):
        try:
            resp = await self.http_client.get(f"{blender_base}/config/render", timeout=10)
            resp.raise_for_status()
            self._render_settings = list(resp.json().keys())   # ['max_res_x', 'max_res_y', ...]
            return
        except Exception as exc:
            ...
```
- Fetches `GET /api/v1/config/render` from blender-service on bot startup
- Stores the **keys** of the response dict as the authoritative list of valid setting names
- These are exactly: `max_res_x`, `max_res_y`, `min_res_x`, `min_res_y`, `max_samples`, `min_samples`, `default_res_x`, `default_res_y`, `default_samples`, `max_concurrent_renders`, `job_ttl_hours`
- **`samples` is NOT in this list**

#### Autocomplete: `render_setting_autocomplete()` — lines 97–106
```python
async def render_setting_autocomplete(
    self, _interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    norm_current = normalize_for_search(current)
    return [
        app_commands.Choice(name=s, value=s)
        for s in self._render_settings
        if norm_current in normalize_for_search(s)
    ][:25]
```
- Provides valid field names as Discord autocomplete choices
- Since `setting` parameter is `str | None` (not `app_commands.Choice`), **Discord does not enforce these choices** — freeform text is accepted

#### Command: `render_config()` — lines 914–945 (set branch: 934–945)
```python
@app_commands.autocomplete(setting=render_setting_autocomplete)
async def render_config(
    self,
    interaction: discord.Interaction,
    action: Literal["view", "set", "reset"] = "view",
    setting: str | None = None,
    value: int | None = None,
) -> None:
    ...
    elif action == "set":
        if not setting or value is None:
            await interaction.response.send_message(
                "⚠️ Usage: `/render_config set <setting> <value>`", ephemeral=True
            )
            return
        resp = await self.http_client.put(
            f"{blender_base}/config/render",
            json={setting: value},          # setting = "samples", value = 64
        )
        resp.raise_for_status()
        await interaction.response.send_message(f"✅ Updated `{setting}` = `{value}`", ephemeral=True)
```

**Critical observations:**
1. The only guard is `if not setting or value is None` — it does NOT check `setting in self._render_settings`
2. The cog sends `json={"samples": 64}` directly to the PUT endpoint
3. After `resp.raise_for_status()` (HTTP 200 → no exception raised), **the cog immediately reports "✅ Updated"**
4. The cog **never reads `resp.json()`** — it does not compare before/after values, does not check `ignored_fields`, does nothing to verify the mutation occurred

### 2.2 Blender-Service Router — `config.py`

#### `PUT /config/render` — lines 26–35
```python
@router.put("/render", summary="Update render settings")
async def update_render_config(request: Request, updates: dict) -> dict:
    """Update one or more render settings.

    Only valid field names are accepted; unknown keys are silently ignored.
    """
    config_service = request.app.state.render_config
    flogger.info(f"PUT /config/render called with updates: {updates}")
    updated = config_service.update(updates)
    return updated.to_dict()
```

**Observations:**
- `updates: dict` — no Pydantic schema, no field validation at the router layer
- FastAPI accepts any JSON object body as a `dict`
- Calls `config_service.update({"samples": 64})`
- Returns `updated.to_dict()` (the full config dict) with HTTP 200
- **No 422 or error returned when all keys are ignored** — the router has no way to distinguish "applied 1 field" from "applied 0 fields"
- Router docstring explicitly documents "unknown keys are silently ignored" — this is intentional design

### 2.3 Config Service — `render_config_service.py`

#### `RenderConfig` dataclass — lines 15–52
```python
@dataclass
class RenderConfig:
    max_res_x: int = 3840
    max_res_y: int = 2160
    min_res_x: int = 352
    min_res_y: int = 240
    max_samples: int = 128
    min_samples: int = 1
    default_res_x: int = 3840
    default_res_y: int = 2160
    default_samples: int = 128
    max_concurrent_renders: int = 2
    job_ttl_hours: int = 1
```
**No field named `samples`.** Only `max_samples`, `min_samples`, `default_samples`.

#### `update()` method — lines 76–96
```python
def update(self, updates: dict) -> RenderConfig:
    applied_updates = []
    ignored_keys = []
    for key, value in updates.items():
        if hasattr(self._config, key):                  # hasattr(config, "samples") → FALSE
            old_value = getattr(self._config, key)
            setattr(self._config, key, value)
            flogger.info(f"Config updated: {key} = {value} (was {old_value})")
            applied_updates.append(key)
        else:
            flogger.debug(f"Ignoring unknown config key: {key}")   # logs "samples"
            ignored_keys.append(key)
    if ignored_keys:
        flogger.warning(f"Config update: {len(ignored_keys)} unknown key(s) ignored: {ignored_keys}")
        # ^ logs WARNING: "Config update: 1 unknown key(s) ignored: ['samples']"
    if applied_updates:
        flogger.info(f"Config update complete: {len(applied_updates)} field(s) applied: {applied_updates}")
    else:
        flogger.warning("Config update: no valid fields provided")
        # ^ also logs WARNING: "Config update: no valid fields provided"
    return self._config      # returns UNCHANGED config object
```

**`hasattr(RenderConfig_instance, "samples")` returns `False`** because the field does not exist. The key is logged at DEBUG level, then a WARNING is logged at lines 91 and 95, but this is entirely internal to the blender-service process. The router has no way to know applied_updates is empty, so it returns HTTP 200 with the unchanged config.

---

## 3. Mutation Trace

```
User: /render_config action:set setting:samples value:64
  │
  ├── adminCog.py:940-942
  │     PUT http://blender-service:8001/api/v1/config/render
  │     Body: {"samples": 64}
  │
  ├── config.py:26-35 (router)
  │     updates = {"samples": 64}
  │     config_service.update({"samples": 64})
  │
  ├── render_config_service.py:76-96 (service)
  │     for key="samples", value=64:
  │       hasattr(self._config, "samples") → False
  │       → flogger.debug("Ignoring unknown config key: samples")
  │     ignored_keys = ["samples"]
  │     applied_updates = []
  │     → flogger.warning("Config update: 1 unknown key(s) ignored: ['samples']")
  │     → flogger.warning("Config update: no valid fields provided")
  │     return self._config  ← UNCHANGED
  │
  ├── config.py:35 (router)
  │     return updated.to_dict()  ← full config, all original values
  │     HTTP 200
  │
  └── adminCog.py:944-945 (cog)
        resp.raise_for_status()  → no exception (HTTP 200)
        send_message("✅ Updated `samples` = `64`")
        ← FALSE SUCCESS, body never read
```

**Result: Zero fields mutated. `samples` discarded. Config unchanged. User told ✅.**

---

## 4. Allowlist Analysis

| Layer | Mechanism | Enforced? |
|---|---|---|
| Discord autocomplete | `_render_settings` (preloaded from API) | ❌ No — `str` param type, Discord allows freeform |
| Cog set handler | None | n/a |
| Router | None — raw `dict` param | n/a |
| Service `update()` | `hasattr` filter | ✅ Correct behavior, but not surfaced as error |

The autocomplete works correctly as a UX hint but provides no enforcement. A user who ignores the suggestions (or a developer using the API directly) can submit any key without receiving an error.

---

## 5. Cross-Reference: B.30 vs B.32

| Dimension | B.30 (`PUT /jobs/{job_id}`, bot-core) | B.32 (`PUT /config/render`, blender-service) |
|---|---|---|
| Mechanism | Pydantic `extra="ignore"` on `UpdateJob` schema | Service-layer `hasattr` filter in `update()` |
| Where filtering happens | Schema validation (before service) | Service layer (after parsing) |
| Unknown input consequence | All unrecognized fields stripped; `payload` defaults to `{}` → **destructive write** | All unrecognized fields silently ignored → **no-op** |
| HTTP response on unrecognized input | 200 with `{"status": "updated"}` | 200 with full (unchanged) config dict |
| Response body read by cog? | No — scheduler cog reports success on 200 | No — admin cog reports success on 200 |
| Internal logging | Nothing logged about field mismatch | WARNING logged at service level (lines 91, 95) |
| Severity | 🟠 high (destructive) | 🟡 medium (misleading no-op) |

**Same class of defect**: write endpoints accept unrecognized input without emitting a validation error. Callers (cogs) report success based solely on HTTP 200 without verifying mutation. Different mechanisms and different consequences — B.30 is more dangerous (destructive); B.32 is less dangerous (nothing changes) but arguably more confusing (false positive success).

The blender-service even has better internal telemetry (WARNING logs), but these are not surfaced to the caller. This is a design decision documented in the router docstring ("unknown keys silently ignored") that has not been communicated back to the Discord cog layer.

---

## 6. Severity Assessment

🟡 **Medium confirmed.** Rationale:
- Admin-only command — blast radius limited
- No data corruption or destructive write
- Config object never wrongly mutated (actually a form of safety)
- Consequence is false confidence: admins believe they changed render settings when they did not
- `samples` is a plausible near-miss for `default_samples` / `max_samples` — easy to trigger by an admin who knows the render system but not the exact field names
- Could cause prolonged render quality/performance issues if admins believe they've constrained concurrent renders or sample counts

---

## 7. Fix Recommendations

### Fix A — Cog-side guard (surgical, safest, immediate)
**File**: `services/discord-gateway/src/cogs/adminCog.py` · `render_config` command, `action == "set"` branch  
**Change**: validate `setting` against `self._render_settings` before making the API call

```python
elif action == "set":
    if not setting or value is None:
        await interaction.response.send_message(
            "⚠️ Usage: `/render_config set <setting> <value>`", ephemeral=True
        )
        return
    # ADD: validate setting name
    if setting not in self._render_settings:
        valid = ", ".join(f"`{s}`" for s in self._render_settings)
        await interaction.response.send_message(
            f"⚠️ Unknown setting `{setting}`. Valid settings: {valid}", ephemeral=True
        )
        return
    resp = await self.http_client.put(...)
```

**Effect**: User gets a clear error instead of a false success. If `_render_settings` failed to preload (empty list), this would block all set operations — a safe failure mode.

### Fix B — Router/service 422 on all-ignored update (defense-in-depth)
**File**: `services/blender-service/src/routers/config.py` OR `services/blender-service/src/services/render_config_service.py`  
**Change**: Return 422 when the update dict contains zero valid fields

Option B1 — raise in service:
```python
def update(self, updates: dict) -> RenderConfig:
    ...
    if not applied_updates and ignored_keys:
        valid = list(self._config.__dataclass_fields__)
        raise ValueError(f"No valid fields provided. Valid fields: {valid}")
    return self._config
```

Option B2 — check in router:
```python
@router.put("/render")
async def update_render_config(request: Request, updates: dict) -> dict:
    config_service = request.app.state.render_config
    valid_fields = list(RenderConfig.__dataclass_fields__)
    unknown = [k for k in updates if k not in valid_fields]
    if unknown and not any(k in valid_fields for k in updates):
        raise HTTPException(status_code=422, detail=f"No valid fields. Valid: {valid_fields}")
    updated = config_service.update(updates)
    return updated.to_dict()
```

### Fix C — Cog response verification (supplementary)
**File**: `services/discord-gateway/src/cogs/adminCog.py`  
**Change**: Read response body and verify the field value changed

```python
resp.raise_for_status()
response_config = resp.json()
if str(response_config.get(setting)) != str(value):
    await interaction.response.send_message(
        f"⚠️ Setting `{setting}` was not applied. It may be an unrecognized field name.", ephemeral=True
    )
    return
await interaction.response.send_message(f"✅ Updated `{setting}` = `{value}`", ephemeral=True)
```

**Scope**: Fix A alone is surgical (2–4 lines). Fixes A + B together are the most robust defense.

---

## 8. Open Questions

1. Are there any other callers of `PUT /api/v1/config/render` (scripts, integration tests, other cogs) that may silently depend on the lenient "accept-anything" behavior?
2. Should the `GET /config/render` response include a `_schema` or `_writable_fields` key so callers can self-validate without a separate discovery endpoint?
3. Should `PUT /config/render` return a richer response: `{applied_fields: [...], ignored_fields: [...], config: {...}}`?
4. Is the "silent ignore" design documented anywhere besides the router docstring? Was it intentional for partial-update flexibility, or just an oversight?

---

**Recon completed**: 2026-04-28 by developer (read-only investigation)
