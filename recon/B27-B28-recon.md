# B.27 / B.28 Recon — Scheduler Slash Commands: Interaction Failure Patterns

**Recon date**: 2026-04-28  
**Investigator**: developer (read-only)  
**Companion entries**: B.27 and B.28 in `/proj/DEFECTS.md`

---

## 1. Cog File Location and Command Inventory

**Cog file**: `services/discord-gateway/src/cogs/schedulerCog.py` (475 lines)

`schedulerCog.py` IS auto-loaded by `bot.py:42–50` (`setup_hook` iterates `src/cogs/*.py`, excluding
filenames containing "template", "disabled", or "test"). `schedulerCog.py` matches none of those
exclusions and is loaded unconditionally.

> **Contradiction resolved**: The B.25 recon (filed prior) stated "No scheduler cog exists in
> `services/discord-gateway/src/cogs/`." That claim was **empirically false**. `schedulerCog.py`
> exists in HEAD and is auto-loaded. See §6 for full contradiction analysis.

### Commands in SchedulerCog

| Line | Command | defer? | Error handler? |
|------|---------|--------|----------------|
| 60 | `/scheduler_list` | ✅ line 65 | ✅ lines 127–131 |
| 137 | `/scheduler_view` | ✅ line 144 | ✅ lines 205–209 |
| 215 | `/scheduler_update` | ✅ line 225 | ✅ lines 287–291 |
| 297 | `/scheduler_delete` | ✅ line 304 | ✅ lines 347–351 |
| 357 | `/admin_reset_scheduler` | ✅ line 365 | ✅ lines 406–410 |
| 416 | `/admin_clear_scheduler` | ✅ line 424 | ✅ lines 465–469 |

All six commands defer **unconditionally** as the **first statement** in the handler body (before
any try block). All six have `.error` decorated handlers.

> **Note on `/scheduler_create`**: The task brief mentioned a `/scheduler_create` slash command.
> No such command exists in `schedulerCog.py` or anywhere in the codebase. One-time and recurring
> job scheduling are REST-API-only operations (`POST /api/v1/jobs`, `POST /api/v1/jobs/recurring`
> in `bot-core`). The brief was incorrect on this point.

---

## 2. Defer and Error Handler Pattern (All Commands)

Every scheduler command follows this identical skeleton:

```python
async def scheduler_<cmd>(self, interaction, ...):
    await interaction.response.defer(thinking=True, ephemeral=True)  # BEFORE try block
    
    try:
        resp = await self.http_client.<method>(...)
        resp.raise_for_status()
        ...
        await interaction.followup.send(embed=embed, ephemeral=True)
    
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            await interaction.followup.send("❌ <message>", ephemeral=True)
        elif e.response.status_code == 503:
            await interaction.followup.send("⚠️ Scheduler unavailable.", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)  # ← B.31b URL leak
    
    except Exception as e:
        await interaction.followup.send("⚠️ An error occurred.", ephemeral=True)

@scheduler_<cmd>.error
async def scheduler_<cmd>_error(self, interaction, error):
    flogger.exception(...)
    if not interaction.response.is_done():           # ← structural gap
        await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)
```

### Structural Gap in Error Handlers

The pattern `if not interaction.response.is_done(): interaction.response.send_message(...)` handles
two cases:

- **Check failures** (AppCommandError from `@is_admin()`): defer not yet called → `is_done()` is
  False → error handler sends the message → user sees "⚠️ An error occurred." ✅
- **Followup failures** (after defer): defer was called → `is_done()` is True → error handler does
  **nothing** → Discord shows "This interaction failed" with no user-facing message ❌

If `interaction.followup.send()` itself raises a `discord.HTTPException` (transient Discord API
error, rate limit, etc.) after `defer()` was already called, the exception propagates to the error
handler, which silently discards it. Discord then marks the interaction as failed. This gap is
present in **all six** scheduler command error handlers.

---

## 3. B.27 Root Cause Analysis — `/scheduler_view` with Nonexistent Job

### Code path (nonexistent job ID)

```
schedulerCog.py:144  await interaction.response.defer(thinking=True, ephemeral=True)
schedulerCog.py:150  resp = await self.http_client.get(f"{api_base}/jobs/nonexistent_job", timeout=10)
schedulerCog.py:151  resp.raise_for_status()                    # raises HTTPStatusError (404)
schedulerCog.py:185  except httpx.HTTPStatusError as e:
schedulerCog.py:186      if e.response.status_code == 404:      # True
schedulerCog.py:187          await interaction.followup.send(f"❌ Job `nonexistent_job` not found.", ephemeral=True)
                             ↑ THIS CAN RAISE discord.HTTPException on transient failure
schedulerCog.py:205  @scheduler_view.error
schedulerCog.py:208      if not interaction.response.is_done(): # False (defer was called)
                             ← DOES NOTHING — no fallback followup sent
```

### Bot-core 404 response (verified)

`bot-core/src/api/routers/scheduler.py:89–92`:
```python
job = _get_scheduler(req).get_job(job_id)
if not job:
    raise HTTPException(404, "Job not found")
```
Bot-core returns HTTP 404 with body `{"detail": "Job not found"}` for nonexistent jobs.
The gateway cog correctly catches this at line 186 and sends an appropriate followup. The 404
handler is NOT missing.

### Why "This interaction failed" occurred

The 404 case **is handled correctly** in the cog. The single observation of "This interaction
failed" was caused by one of:

1. **Transient Discord API failure** during `interaction.followup.send()` at line 187 raised a
   `discord.HTTPException`. This exception propagated to `scheduler_view_error`, which saw
   `is_done()=True` and did nothing. Discord timed out the interaction with no followup → "This
   interaction failed."

2. **Event loop contention** (less likely given the main account's instant @is_admin path and
   the sequential nature of the two commands). The server owner bypasses the HTTP path in
   `_check_is_admin` (line 37: Discord Administrator short-circuit), so there is zero pre-defer
   latency risk for this user.

**The structural root cause** (independent of the transient trigger) is the error handler's
missing `else` branch: when `is_done()` is True, there is no fallback `followup.send()`.

---

## 4. B.28 Root Cause Analysis — `/scheduler_update` Doubled Response

### Command flow for `payload_json:"invalid"`

```
schedulerCog.py:225  await interaction.response.defer(thinking=True, ephemeral=True)
                     ↑ Discord receives DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE (type 5)
                     ↑ Discord begins showing "thinking..." to user

schedulerCog.py:231  try:
schedulerCog.py:232      payload = json.loads("invalid")        # raises JSONDecodeError IMMEDIATELY
schedulerCog.py:233  except json.JSONDecodeError as e:
schedulerCog.py:234      await interaction.followup.send(
schedulerCog.py:235          f'❌ Invalid JSON payload: `{e}`\n\nExample: ...',
schedulerCog.py:236          ephemeral=True,
schedulerCog.py:237      )
schedulerCog.py:238      return
```

### Why the doubled response appears

The `json.loads("invalid")` call is **synchronous** and raises immediately (microseconds). The
time between `defer()` and `followup.send()` is effectively zero. Discord's API receives the
defer acknowledgment and the followup in near-simultaneous succession.

Two mechanisms can produce the observed doubled ephemeral:

**Mechanism A (Discord client race)**: Discord's client receives the deferred state and the
followup response nearly simultaneously. During state transition from "thinking..." → followup
content, the client may briefly render "This interaction failed" before the followup message
renders. This is a Discord client-side rendering artifact, not a bot-side bug.

**Mechanism B (transient followup failure)**: If `interaction.followup.send()` at line 234
raises a `discord.HTTPException` (acknowledgment error while the message was already delivered):
- Discord received and stored the followup (so user sees the error embed)
- The local raise propagates to `scheduler_update_error`, which sees `is_done()=True` and does nothing
- Discord shows "This interaction failed" as a secondary indicator

In either case, the **actual error message IS delivered** to the user (observed). The "This
interaction failed" is a secondary artifact.

### Structural contributor

JSON parsing (a synchronous, non-async operation) happens **after** `defer()`. For pure
synchronous validation errors, it is more correct to respond with
`interaction.response.send_message()` directly (no defer) since no async work is pending.
Deferring for a synchronous validation failure causes the unnecessary near-simultaneous defer +
followup, which is the precondition for Mechanism A above.

---

## 5. Sweep — Error Handling Completeness (All 5 HTTP Commands)

| Command | 404 | 422 | 500 | 503 | Network timeout |
|---------|-----|-----|-----|-----|-----------------|
| `/scheduler_list` | n/a (list endpoint) | n/a | generic `f"❌ API Error: {e}"` | ✅ explicit | ✅ broad except |
| `/scheduler_view` | ✅ explicit msg | generic | generic | ✅ explicit | ✅ broad except |
| `/scheduler_update` | ✅ explicit msg | n/a | generic | ✅ explicit | ✅ broad except |
| `/scheduler_delete` | ✅ explicit msg | n/a | generic | ✅ explicit | ✅ broad except |
| `/admin_reset_scheduler` | n/a | n/a | generic | ✅ explicit | ✅ broad except |
| `/admin_clear_scheduler` | n/a | n/a | generic | ✅ explicit | ✅ broad except |

All commands handle 404 and 503 explicitly. 5xx responses fall through to the generic
`f"❌ API Error: {e}"` branch, which leaks internal URLs (B.31b — see §7).

---

## 6. B.25 Contradiction — "No Scheduler Cog Exists"

The B.25 recon (`/proj/recon/B25-recon.md:192`) stated:
> "No scheduler cog exists in `services/discord-gateway/src/cogs/` — the APScheduler interface
> is purely REST API."

**Empirical disproof**: `schedulerCog.py` is present in HEAD at
`/proj/services/discord-gateway/src/cogs/schedulerCog.py`. Auto-loading via `bot.py:42–50`
(`setup_hook` uses `os.listdir("src/cogs")`, filters `.py` extension, excludes "template",
"disabled", "test"). The file is 475 lines and contains 6 slash commands. The B.25 investigator
either:

(a) Missed the file during a listing scan, or  
(b) The cog was added after the B.25 recon was written (but before B.27/B.28 were filed, since
    those entries reference `/scheduler_view` commands as having been run live).

The B.25 recon's speculation about B.27/B.28 ("would share the event-loop-timing class with
B.25") was directionally correct (timing is involved) but the structural cause is different: it
is the **error handler gap** (no followup after defer), not the is_admin timing risk. The main
account (Discord admin) bypasses the HTTP path in `_check_is_admin` entirely, so the B.25-class
timing risk does not apply to these specific observations.

---

## 7. B.31b Cross-Reference — Raw URL Leaks in schedulerCog.py

schedulerCog.py contains **6 occurrences** of the `f"❌ API Error: {e}"` pattern:

| Line | Command |
|------|---------|
| 122 | `/scheduler_list` |
| 197 | `/scheduler_view` |
| 279 | `/scheduler_update` |
| 339 | `/scheduler_delete` |
| 399 | `/admin_reset_scheduler` |
| 458 | `/admin_clear_scheduler` |

These are part of the 53-occurrence cross-cog count documented in B.31. When a 5xx error occurs
from bot-core, `httpx.HTTPStatusError.__str__()` renders the full URL
(`http://bot-core:8000/api/v1/jobs/...`) plus an MDN link. The `f"❌ API Error: {e}"` pattern is
a **separate concern from B.27/B.28** and would be addressed as part of the B.31b theme-bundle
fix, not the B.27/B.28 surgical fix.

---

## 8. O.2 Status — Autocomplete Already Implemented in HEAD

O.2 claims `/scheduler_view`, `/scheduler_update`, `/scheduler_delete` lack `job_id` autocomplete.

**Empirical finding**: `job_id_autocomplete` is fully implemented (schedulerCog.py:35–54) and
applied to all three commands:

- `/scheduler_view` — `@app_commands.autocomplete(job_id=job_id_autocomplete)` at line 140
- `/scheduler_update` — `@app_commands.autocomplete(job_id=job_id_autocomplete)` at line 221
- `/scheduler_delete` — `@app_commands.autocomplete(job_id=job_id_autocomplete)` at line 300

The autocomplete fetches the live job list from `GET /api/v1/jobs` on each keystroke (line 40),
builds labels as `"{job_id[:32]} ({trigger[:40]})"`, and returns up to 25 choices. **O.2 appears
already resolved in HEAD.**

Note: The autocomplete uses `self.http_client.get(f"{api_base}/jobs", timeout=5)` with the
**cog's shared** `httpx.AsyncClient` (not a new client per call, unlike `_check_is_admin`).
On any exception (including timeout), it returns an empty list (graceful degradation).

---

## 9. Recommended Fix Scope

Both B.27 and B.28 are in the same cog file and can be addressed in one PR:

### Fix 1 — Enhance all error handlers (addresses B.27's structural gap)

In each of the 6 `@scheduler_<cmd>.error` handlers, add an `else` branch:

```python
@scheduler_view.error
async def scheduler_view_error(self, interaction, error):
    flogger.exception("Error in /scheduler_view", exc_info=error)
    if not interaction.response.is_done():
        await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)
    else:
        # Deferred interaction — attempt followup fallback
        with suppress(Exception):
            await interaction.followup.send("⚠️ An error occurred.", ephemeral=True)
```

### Fix 2 — Move JSON validation before defer (addresses B.28's pre-condition)

In `scheduler_update`, validate `payload_json` before calling `defer()`:

```python
async def scheduler_update(self, interaction, job_id, payload_json):
    # Synchronous validation — no async work needed, respond directly
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as e:
        await interaction.response.send_message(
            f'❌ Invalid JSON payload: `{e}`\n\nExample: `{{"job_type": "bounty_spawn"}}`',
            ephemeral=True,
        )
        return
    
    # Only defer after validation passes (async work follows)
    await interaction.response.defer(thinking=True, ephemeral=True)
    ...
```

**Scope**: Surgical — two targeted changes to a single file (`schedulerCog.py`), no schema changes,
no bot-core changes. Both fixes can land in the same commit.

---

*Recon completed: 2026-04-28 by developer (read-only investigation)*
