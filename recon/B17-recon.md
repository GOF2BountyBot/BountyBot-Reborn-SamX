# B.17 Recon — `/admin_player action:Set XP` returns `old_xp` equal to new XP value

**Recon date**: 2026-04-28  
**Status**: Read-only investigation, no source changes.  
**HEAD commit**: `815cd59`

---

## 1. Verified Code Paths (HEAD)

### admin.py — `update_player_xp` handler (lines 429–475)

```
429: @router.put("/players/xp")
430: async def update_player_xp(
431:     request: UpdatePlayerXPRequest,
432:     user_id: int,
433:     guild_id: int,
434:     player_service: PlayerService = Depends(get_player_service),
435: ):
...
442:     try:
443:         async with get_db_session() as db:
444:             old_player = await player_service.player_repo.get_by_id(db, request.player_id)
445:             if not old_player:
446:                 raise HTTPException(status_code=404, detail="Player not found")
447:
448:             old_tier = old_player.tier        ← captured BEFORE mutation ✓
449:             player = await player_service.update_player_xp(db, request.player_id, request.xp)
...
461:             return {
462:                 "player_id": request.player_id,
463:                 "old_xp": old_player.xp,       ← BUG: reads AFTER mutation ✗
464:                 "new_xp": request.xp,
465:                 "old_tier": old_tier,           ← correctly uses pre-mutation capture ✓
466:                 "new_tier": player.tier,
467:                 "tier_changed": old_tier != player.tier,
468:                 "message": f"XP updated for player {request.player_id}",
469:             }
```

**Bug**: `old_player.xp` on line 463 is read AFTER `update_player_xp()` has mutated the same ORM instance via SQLAlchemy's identity map.

**Comparison — credits handler (CORRECT, lines 392–416)**:
```python
# Lines 392-394 in admin.py — comment explicitly calls this out:
# Pre-capture old_credits BEFORE the service mutates the player in-place
# (identity-map sequencing: after update_player_credits(), player.credits
# already holds the new value — reading it post-call yields 0 for old_credits)
old_player = await player_service.player_repo.get_by_id(db, request.player_id)
if not old_player:
    raise ValueError(f"Player {request.player_id} not found")
old_credits = old_player.credits   # ← captured before mutation ✓
player = await player_service.update_player_credits(...)
return {
    ...
    "old_credits": old_credits,    # ← uses captured value ✓
    "new_credits": request.credits,
```

The credits handler has the CORRECT pattern with an explanatory comment. The XP handler has the PRE-FETCH (`old_player = get_by_id(...)`) but is missing the value capture (`old_xp = old_player.xp`).

---

## 2. Identity-Map Mechanism (Root Cause)

SQLAlchemy's identity map ensures that within a single `AsyncSession`, calling `get_by_id(db, player_id)` twice returns **the same Python object**.

Execution sequence in admin.py `update_player_xp`:

1. `old_player = await player_service.player_repo.get_by_id(db, request.player_id)`  
   → SQLAlchemy identity map: allocates ORM object **A** (`A.xp = 15`, say).

2. `player = await player_service.update_player_xp(db, request.player_id, request.xp)`  
   → Inside `player_service.update_player_xp` (`player_service.py:170–192`):  
     → `player = await self.player_repo.get_by_id(db, player_id)` → identity map returns **same object A**  
     → `player.xp = xp` (= 16) → **A.xp is now 16**  
     → `await db.commit()` + `await db.refresh(player)` → A is refreshed from DB (A.xp = 16)

3. Back in admin.py: `old_player` still holds reference to **A**; `A.xp == 16` (the new value).

4. `"old_xp": old_player.xp` → evaluates to `16`, NOT the pre-mutation value `15`.

Result: both `old_xp` and `new_xp` in the API response are `16` (= the new value `request.xp`).

---

## 3. player_service.update_player_xp (player_service.py:170–192)

```python
async def update_player_xp(self, db: AsyncSession, player_id: int, xp: int) -> Player:
    """Update player XP. Tier is NOT auto-advanced; use promote_player() to advance tier."""
    try:
        player = await self.player_repo.get_by_id(db, player_id)   # same identity-map instance
        if not player:
            raise ValueError(f"Player {player_id} not found")

        if xp < 0:
            xp = 0
        elif xp > 1000000:
            xp = 1000000

        player.xp = xp        # ← mutates the identity-mapped instance in-place

        await db.commit()
        await db.refresh(player)
        return player
    except Exception as e:
        ...
```

The internal `get_by_id()` call on the same session returns the same ORM object. After `player.xp = xp`, the `old_player` variable in admin.py points to the same mutated object.

---

## 4. Commit History Cross-Reference

### c8b5fef ("refactor Core UPDATE anti-pattern to ORM-tracked setattr Option B")

Files changed: `shop_service.py`, repository files (`player_repository.py`, `shop_repository.py`, `inventory_repository.py`, `player_ship_repository.py`, `bounty_repository.py`), plus `services/AGENTS.md`, `tests/AGENTS.md`, integration tests.

**Did NOT touch `admin.py`**. The c8b5fef fix applied to `shop_service.sell_item`'s doubled-credits response bug via the `shop_service.py` return statement — not to the admin router's XP endpoint.

### 46ac33a ("fix(admin, announcements): B.8 + B.10 + B.11 + B.13 + B.16 admin and announcement cluster")

This commit:
- **B.10**: Added `old_credits = old_player.credits` capture in `update_player_credits` (admin.py ~line 398). Commit message says it was mirroring "the correct pattern already used by the Set XP action handler."
- **B.16**: Added `old_tier = old_player.tier` capture in `update_player_xp` (admin.py:448).

**Critical observation**: When B.10 was fixed, the developer believed the Set XP handler was "correct" because it had the pre-fetch (`old_player = get_by_id(...)`). The B.16 fix added `old_tier` capture correctly but both overlook that `old_xp` itself is never captured. The XP handler had the structural pre-fetch in place but NOT the value capture, creating a false impression of correctness.

**B.17 root cause in context**: B.17 is the residual gap from `46ac33a` — the same identity-map anti-pattern that B.10 fixed for credits, surviving in the XP handler for the `xp` field specifically.

---

## 5. Sibling Sweep — All admin_player Actions

**`/admin_player` action choices** (adminCog.py:272–279):

| Choice Name | value | API Endpoint | Old/New in Response | Pre-mutation Capture? | Status |
|-------------|-------|-------------|---------------------|----------------------|--------|
| Set Credits | `set_credits` | `PUT /admin/players/credits` | `old_credits`, `new_credits` | ✅ `old_credits = old_player.credits` (admin.py:398) | **PASS** |
| Add Credits | `add_credits` | `PUT /admin/players/credits` | No `old_*` (shows "Amount Added" + "New Total") | N/A — cog computes total client-side | **PASS (N/A)** |
| Set XP | `set_xp` | `PUT /admin/players/xp` | `old_xp`, `new_xp` | ❌ `old_player.xp` read after mutation (admin.py:463) | **FAIL** |
| View Stats | `view_stats` | `GET /players/{id}/statistics` | Read-only | N/A | **PASS** |
| Reset Player | `reset` | `POST /admin/players/{id}/reset` | No old/new delta | N/A | **PASS** |

---

## 6. Extended Admin Router Sweep

All other admin router endpoints checked for old/new player-stat capture patterns:

| Endpoint | Returns old/new delta? | Pattern correct? |
|----------|----------------------|-----------------|
| `POST /admin/guilds/initialize` | No | N/A |
| `POST /admin/guilds/{id}/reset` | No player stat delta | N/A |
| `DELETE /admin/guilds/{id}/uninstall` | `removed_counts` dict | N/A |
| `PUT /admin/players/credits` | `old_credits`, `new_credits` | ✅ PASS (B.10 fixed) |
| `PUT /admin/players/xp` | `old_xp`, `new_xp` | ❌ FAIL (B.17) |
| `POST /admin/players/{id}/reset` | Post-reset state only | N/A |
| `POST /admin/players/inventory/add` | Transaction details | N/A |
| `POST /admin/shops/refresh` | Shop details | N/A |
| `PUT /admin/shops/config` | Config dict | N/A |
| `GET /admin/system/health` | Read-only | N/A |
| `GET /admin/guilds/{id}/stats` | Read-only | N/A |
| `POST /admin/give-item` | Transaction details | N/A |
| `POST /admin/remove-item` | Transaction details | N/A |
| `POST /admin/give-ship` | Ship state | N/A |
| `POST /admin/remove-ship` | Ship + items_returned | N/A |

**Finding**: Only `PUT /admin/players/xp` is affected. All other endpoints either don't expose old/new deltas or correctly capture values before mutation.

---

## 7. Test Coverage Gap Analysis

### Existing test: `test_update_player_xp_happy_path` (test_admin_router.py:781–798)

```python
mock_player_service.player_repo.get_by_id = AsyncMock(
    return_value=make_mock_player(xp=50, tier="Bronze")
)
mock_player_service.update_player_xp = AsyncMock(
    return_value=make_mock_player(xp=100, tier="Bronze")
)
# assert data["old_xp"] == 50  ← PASSES despite the bug
```

**Why it passes despite the bug**:
- `make_mock_player(xp=50)` and `make_mock_player(xp=100)` create **two distinct MagicMock objects**
- `player_repo.get_by_id` returns mock-A (`xp=50`)
- `update_player_xp` returns mock-B (`xp=100`)
- `old_player` (mock-A) is never mutated — mock objects have independent attribute state
- `old_player.xp` remains `50` even after `update_player_xp()` is called
- The test asserts `old_xp == 50` → passes by coincidence (separate mock objects, not shared ORM instance)

**Why a real ORM session would fail**:
- Same `db` session → same identity map → `get_by_id` twice → same Python object
- `update_player_xp` sets `object.xp = 100` → `old_player.xp` is now `100`
- Response has `old_xp == 100`, `new_xp == 100` — both wrong

**Required fix to detect this in tests**:
An integration test using `tests/integration/` pattern (SQLite-in-memory `AsyncSession`) where both `player_repo.get_by_id` calls share the same session. The identity-map bug would surface because the two lookups return the same object, and the mutation would be visible through `old_player.xp`.

Reference pattern: `tests/integration/test_response_body_consistency.py` (added in c8b5fef for the shop_service doubled-credits case).

**Existing test note**: `test_set_xp_above_threshold_does_not_auto_promote_tier` (test_admin_router.py:801–829) also has the same mock structure and passes for the same reason.

---

## 8. Severity Assessment

🟡 medium — confirmed:
- Blast radius: admin-only command (`/admin_player`), only `action:Set XP`
- DB mutation: always correct (XP is set to the right value)
- Audit log: `AuditService.log_action` records `xp: request.xp` (correct) — audit trail is not corrupted
- Only the response embed is wrong: both `Old XP` and `New XP` show the new value
- Practical impact: admins cannot verify what the XP was before they set it

---

## 9. Recommended Fix

**Surgical** (1 capture + 1 usage change):

In `admin.py`, inside `update_player_xp` handler, add one line before the service call:

```python
old_player = await player_service.player_repo.get_by_id(db, request.player_id)
if not old_player:
    raise HTTPException(status_code=404, detail="Player not found")

old_tier = old_player.tier
old_xp = old_player.xp          # ← ADD THIS: capture before mutation
player = await player_service.update_player_xp(db, request.player_id, request.xp)

...
return {
    ...
    "old_xp": old_xp,            # ← CHANGE: use captured value, not old_player.xp
    "new_xp": request.xp,
    "old_tier": old_tier,
    ...
}
```

**Also recommended (test hardening)**:
Add an integration test in `tests/integration/` that verifies `response["old_xp"]` matches the pre-mutation DB value, mirroring the `test_response_body_consistency.py` pattern added for the c8b5fef credits fix.

---

**Recon completed**: 2026-04-28 by developer (read-only investigation)
