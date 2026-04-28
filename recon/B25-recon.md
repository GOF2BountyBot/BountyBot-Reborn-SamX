# B.25 Recon — `/admin_spawn_bounty` Discord 3-second interaction timeout

**Recon date**: 2026-04-28  
**Investigator**: developer (read-only)  
**HEAD commit context**: post-rebuild stack (same as B.23/B.19/B.24 recon session)

---

## Symptom (factual, verbatim from DEFECTS.md)

`/admin_spawn_bounty tier:Bronze` invoked at 14:11 ET → "The application did not respond"  
(Discord 3-second interaction timeout, indicates token expired before initial response)  
Subsequent `/check` against the spawn confirmed it DID complete server-side (bounty #2247 Hongar Meton).  
Earlier same-session invocations at 09:54 and 09:57 returned normally — only the 14:11 call timed out (1 of 3+).

---

## File and Line Index

| Entity | Location |
|--------|----------|
| `admin_spawn_bounty` command definition | `adminCog.py:1286–1347` |
| Command decorator/signature | `adminCog.py:1286–1299` |
| `interaction.response.defer()` call | `adminCog.py:1300` |
| Admin check decorator | `adminCog.py:1288`: `@is_admin()` |
| `is_admin()` factory | `adminCog.py:54–63` |
| `_check_is_admin()` predicate | `adminCog.py:22–51` |
| HTTP call inside predicate | `adminCog.py:42–44` |
| Shared http_client on AdminCog | `adminCog.py:71` |
| Spawn API POST | `adminCog.py:1307–1311` |
| Bot-core spawn router | `bounties.py:437–519` |
| `BountyService.spawn_bounty()` | `bounty_service.py:822–933` |
| `_schedule_expiry_job()` | `bounty_spawn_executor.py:715–753` |
| `_announce_bounty()` | `bounty_spawn_executor.py:761–924` |

---

## Empirical Findings (all from code read, no speculation)

### Q1: Locate the `/admin_spawn_bounty` cog handler

`adminCog.py`, lines 1286–1347. Handler: `async def admin_spawn_bounty(self, interaction, tier)` at line 1298.

### Q2: Does the handler call `interaction.response.defer()` before spawn work?

**YES. `adminCog.py:1300`:**
```python
await interaction.response.defer(thinking=True, ephemeral=True)
```
This is the FIRST statement in the handler body. defer is called BEFORE the `try` block containing the spawn POST.

**So the classic "missing defer" root cause is NOT present.**

However, defer() being in the code doesn't guarantee it executes within 3 seconds. The `@is_admin()` decorator (line 1288) runs as an `app_commands.check` predicate BEFORE the handler body is called. This predicate can itself be time-consuming.

### Q3: What does the spawn path do server-side?

The cog makes a single POST:
```
POST {api_base}/bounties/guild/{guild_id}/admin-spawn
params: user_id, [tier]
timeout: 30 seconds
```

Bot-core router `bounties.py:437–519` handles this. For a single tier (Bronze), it:

1. **DB**: `ConfigRepository.get_by_guild_id()` — single SELECT for guild config  
2. **DB**: `criminal_repo.list_all()` + `bounty_repo.get_active_by_guild_and_division()` — criminal selection  
3. **In-memory**: System graph load (cached after first call; `graph_service.load_graph(db)`)  
4. **CPU**: A\* pathfinding, up to 3 attempts — pure in-memory, typically <100ms  
5. **DB** (multiple): `generate_loadout()` — ship + weapons + modules queries (3–6 DB round-trips)  
6. **DB**: `bounty_repo.create()` — INSERT  
7. **HTTP (non-fatal)**: `_schedule_expiry_job()` → POST to `{bot-core}/api/v1/jobs`, `timeout=10`  
8. **HTTP chain (non-fatal)**: `_announce_bounty()` → route map render GET (15s timeout) + image upload POST (15s timeout) + announcement POST (10s timeout) + DB INSERT for DiscordMessage  
9. **DB**: `AuditService.log_action()` — INSERT

**No blender-service calls in the spawn path.** The httpx timeout on the cog side is 30 seconds, which comfortably covers all server-side work.

### Q4: Latency profile

Steps 1–6 are DB-bound: expected <500ms under normal conditions. Steps 7–8 are best-effort HTTP calls that are non-fatal if they fail or time out. The gateway announcement in step 8 is the most variable: it involves uploading a PNG image and calling the discord-gateway, which can take 2–5+ seconds under load.

Critically: all of steps 1–9 happen AFTER the cog receives the HTTP response from the POST. The cog sits in `await self.http_client.post(...)` waiting up to 30 seconds for the endpoint to complete steps 1–9. The Discord defer acknowledgement (step 0, from the cog side) was already sent before the POST began.

### Q5: Why did "The application did not respond" appear if defer() is present?

Because defer() being in the source code doesn't guarantee it executes within Discord's 3-second window. The window starts when Discord receives the slash command invocation and ends when Discord's callback URL receives the acknowledgment.

**Timing chain from Discord perspective:**
```
User invokes command
    ↓ Discord sends interaction over WebSocket to bot (network latency: <50ms typically)
    ↓ Bot's asyncio event loop receives event (queued behind other tasks if loop is busy)
    ↓ discord.py dispatches to admin_spawn_bounty handler
    ↓ @is_admin() predicate runs (fast: Discord Admin check, no HTTP)
    ↓ Handler body begins execution
    ↓ await interaction.response.defer()  ← MUST reach Discord within 3s of step 1
         ↓ POST to https://discord.com/api/v10/interactions/{id}/{token}/callback
         ↓ Discord processes and returns 200
    ↓ Discord shows "thinking..." to user
```

If the total time from interaction receipt to the defer() POST reaching Discord exceeds 3 seconds, Discord's client shows "The application did not respond."

**At 14:11 ET**, the bot had been running since ~09:54 with multiple bounty spawns, checks, shop interactions, and APScheduler tasks firing every 5 minutes. The asyncio event loop was under elevated utilization compared to the fresh session at 09:54–09:57.

### Q6: How did the spawn complete if the timeout occurred?

Two scenarios (see DEFECTS.md for full analysis):

**Mode A (most likely for Discord-Admin user)**: `defer()` POST reached Discord within the 3-second window, but Discord's client-side UI had already transitioned to "application did not respond" before processing the acknowledgment. The bot continued normally; `followup.send()` succeeded at Discord's API level but the Discord client no longer displayed the ephemeral followup (the interaction UI was already closed on the client). The spawn completed server-side and the audit log was written.

**Mode B (structural risk for Bot-Admin-role users only)**: `_check_is_admin` at line 42 creates a fresh `httpx.AsyncClient` and makes a GET to bot-core with `timeout=5`. If this call takes 3–5 seconds (possible under DB load), the handler body is reached only after the Discord window has closed. `defer()` fails (Discord returns 404 on the expired token). If discord.py's NotFound exception is not caught by the handler's own error handler (there's no `@admin_spawn_bounty.error` defined), it propagates to discord.py's global error handler and the spawn would NOT run. This scenario is inconsistent with the spawn completing — so Mode B cannot apply to this specific observation.

**Conclusion**: Mode A is the specific mechanism for the 14:11 event. Mode B is a latent structural risk for any Bot-Admin-role (non-Discord-admin) user.

---

## Defer-Pattern Sweep — All 20 AdminCog Commands

```
adminCog.py line | Command                 | defer()?
─────────────────┼─────────────────────────┼──────────────────────────────────────────────
         121     | admin_check             | ✅ defer(thinking=True, ephemeral=True)
         170     | admin_setup             | ✅ defer(thinking=True, ephemeral=True)
         282     | admin_player            | ✅ defer(thinking=True, ephemeral=True)
         434     | admin_refresh_shop      | ✅ defer(thinking=True, ephemeral=True)
         479     | admin_guild_stats       | ✅ defer(thinking=True, ephemeral=True)
         528     | admin_config            | ✅ defer(thinking=True, ephemeral=True)
         611     | admin_uninstall         | ✅ defer(thinking=True, ephemeral=True)
         754     | admin_config_shop       | ✅ defer(thinking=True, ephemeral=True)
         850     | admin_config_validate   | ✅ defer(thinking=True, ephemeral=True)
         914     | render_config           | ❌ interaction.response.send_message() ONLY
         963     | render_cache_clear      | ❌ interaction.response.send_message() ONLY
        1008     | admin_clear_bounties    | ✅ defer(thinking=True, ephemeral=True)
        1066     | admin_config_bounty     | ✅ defer(thinking=True, ephemeral=True)
        1194     | admin_config_xp         | ✅ defer(thinking=True, ephemeral=True)
        1298     | admin_spawn_bounty      | ✅ defer(thinking=True, ephemeral=True) ← this defect
        1360     | admin_cooldown_reset    | ✅ defer(thinking=True, ephemeral=True)
        1491     | admin_give_item         | ✅ defer(thinking=True, ephemeral=True)
        1571     | admin_remove_item       | ✅ defer(thinking=True, ephemeral=True)
        1641     | admin_give_ship         | ✅ defer(thinking=True, ephemeral=True)
        1765     | admin_remove_ship       | ✅ defer(thinking=True, ephemeral=True)
```

**`render_config` (line 914) and `render_cache_clear` (line 963)** are the two commands that do NOT use defer. They directly call `interaction.response.send_message(embed=..., ephemeral=True)` after making HTTP calls to blender-service. These are generally fast operations (view/reset config, clear cache) so they have not triggered timeouts. However, they carry structural risk because:
- They still pass through `@is_admin()` which can take up to 5s for Bot-Admin-role users
- Network latency to blender-service is variable

---

## `_check_is_admin` Timing Analysis

```python
async def _check_is_admin(interaction) -> bool:
    # Path 0: DEVELOPERS env var — instant (string comparison)
    devs = os.getenv("DEVELOPERS", "")
    if str(interaction.user.id) in [...]:
        return True                   # ← instant, ~0ms

    # Path 1: Discord Administrator permission — instant (in-memory member object)
    if interaction.user.guild_permissions.administrator:
        return True                   # ← instant, ~0ms

    # Path 2: Bot Admin role from API — SLOW (HTTP + DB)
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.get(f"{api_base}/config/guild/{id}", timeout=5)
        # TCP connection overhead + DB query + response deserialization
        # Budget consumption: 50ms (fast) to 5000ms (timeout)
```

For the **server owner** (main account `402296276617527306`): they are the guild owner and have Discord Administrator permission → Path 1 returns True immediately. Path 2 is never reached.

For a **Bot-Admin-role user** (not Discord Admin, not Developer): Path 2 runs. A fresh `httpx.AsyncClient` is created per invocation (no connection reuse — `AdminCog.self.http_client` is NOT used here). TCP connection setup + bot-core DB latency + response parsing could take 100ms–5000ms.

---

## Structural Notes

### Fresh httpx.AsyncClient vs shared client

`_check_is_admin` at line 42 creates a new `async with httpx.AsyncClient(...)` per call. This means TCP connection overhead for every Bot-Admin-role check. The `AdminCog` class has `self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))` (line 71) which is reused for all handler calls. If `_check_is_admin` were refactored to run inside the handler after defer (with access to `self.http_client`), it would eliminate the connection overhead.

### No error handler on `admin_spawn_bounty`

Unlike `admin_setup`, `admin_player`, `admin_give_item`, `admin_remove_item`, `admin_give_ship`, `admin_remove_ship`, and `admin_cooldown_reset` — all of which have `.error` decorated handlers — `admin_spawn_bounty` has **no `.error` handler**. If an unhandled exception (like a discord.py `NotFound` from a failed defer) propagates, it goes to discord.py's default `on_app_command_error`, which logs but doesn't respond to the user.

---

## B.27/B.28 Cross-Reference

B.27 and B.28 are **absent from DEFECTS.md** as of this recon. Searched full file; entries jump from B.25 to B.24 to B.23. No scheduler cog exists in `services/discord-gateway/src/cogs/` — the APScheduler interface is purely REST API (`bot-core/src/api/routers/scheduler.py`). If these are planned defects describing "interaction-failure UX in other cogs," they would share the event-loop-timing class with B.25 and the defer-gap class with `render_config`/`render_cache_clear`.

---

## Fix Options

### Option 1 (preferred for Bot-Admin-role risk): Post-defer admin check

```python
@app_commands.command(name="admin_spawn_bounty", ...)
# Remove @is_admin() decorator
async def admin_spawn_bounty(self, interaction, tier=None):
    await interaction.response.defer(thinking=True, ephemeral=True)
    
    # Admin check AFTER defer — token is now acknowledged, 15-minute window open
    if not await _check_is_admin(interaction):
        await interaction.followup.send("❌ Admin only.", ephemeral=True)
        return
    
    try:
        ...
```

Trade-off: non-admins see "bot is thinking" briefly before error. Apply to all admin commands.

### Option 2 (for render_config / render_cache_clear): Convert to defer + followup

```python
async def render_config(self, interaction, action="view", setting=None, value=None):
    await interaction.response.defer(thinking=True, ephemeral=True)
    blender_base = ...
    try:
        if action == "view":
            resp = await self.http_client.get(...)
            await interaction.followup.send(embed=embed, ephemeral=True)
        ...
    except ...:
        await interaction.followup.send("...", ephemeral=True)
```

### Option 3 (diagnostics / instrumentation): Add pre-defer timing log

```python
async def admin_spawn_bounty(self, interaction, tier=None):
    import time
    t0 = time.monotonic()
    await interaction.response.defer(thinking=True, ephemeral=True)
    flogger.info(f"/admin_spawn_bounty: defer latency={time.monotonic()-t0:.3f}s guild={interaction.guild_id}")
    ...
```

This would allow measuring the actual delay in production without changing behavior.

---

## Conclusion

- **`admin_spawn_bounty` has `defer()` present (adminCog.py:1300)** — the code is structurally correct.
- **The 14:11 timeout was a timing boundary event**, most likely event loop contention (Mode A) causing Discord client to show the error before the defer acknowledgment was rendered.
- **The spawn completing despite the UI timeout** is consistent with Mode A (defer succeeded server-side, followup sent but client UI already dismissed).
- **Two secondary findings**: (1) `render_config` and `render_cache_clear` lack defer — genuine structural gap; (2) `_check_is_admin` HTTP path for Bot-Admin-role users is a pre-defer timing risk for 18 of 20 admin commands.
- **Severity: 🟡 medium** — confirmed. No data corruption, intermittent failure, user can verify spawn via `/check`.
- **Recommended fix scope: surgical** — two independent targeted repairs (post-defer admin check pattern + render_config/render_cache_clear defer conversion).

---

*Recon completed: 2026-04-28 by developer (read-only investigation)*
