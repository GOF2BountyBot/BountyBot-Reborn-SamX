# Task 0002 — Shop Item Stats Display + Empty-Store Announcement Fix

## Overview

Two shop-related issues bundled together. Both touch overlapping shop files (`shopCog.py`, `shop_announcement.py`, `shop_refresh_executor.py`).

---

## Sub-task A: Add Item Stats to Shop Item Lines

**Problem:** The `/shop` embed shows items with only name, tier, quantity, and price. Key stats (DPS for weapons, Shield/Armour HP for modules, Hull HP for ships) are not shown, making it hard to evaluate items without equipping them.

**Desired format** (matching `/loadout` display style):
```
Primary Weapons <1/4>
 M6 A4 "Raccoon" | DPS: 92.3

Modules <3/16>
 Telta Quickscan
 Particle Shield | Shield: 380
 T'yol | Armour: 250
```

**Fix:**

1. Identify how `/loadout` builds its stat-annotated item lines — read the loadout message builder (likely in `services/bot-core/src/message_builders/` or `services/discord-gateway/src/cogs/`) and replicate the stat extraction pattern.

2. Add a helper function `_format_shop_item_stats(item: dict) -> str` in `services/discord-gateway/src/cogs/shopCog.py`:
   - **Primary/Secondary/Turret Weapons:** append `| DPS: {dps}` if `dps` field present and non-zero
   - **Modules:** append `| Shield: {shield}` or `| Armour: {armour}` (check `extra_atts` JSON or top-level fields — confirm field names from model)
   - **Ships:** append `| Hull: {hull_hp}` if `hull_hp` present
   - Items with no relevant stat (e.g. utility modules with no shield/armour): return empty string (no suffix)

3. Update the shop item display loop in `shopCog.py` to call `_format_shop_item_stats(item)` and append the result to each item line.

4. Verify the bot-core shop endpoint response schema (`shops_schema.py`) includes all needed stat fields. If any are missing, add them to the response schema and ensure the router populates them from the ORM model.

**Stat field reference (confirm against actual models):**
- `primary_weapon`, `secondary_weapon`, `turret_weapon`: `dps` (float)
- `module`: `shield` (int), `armour` (int) — may be in `extra_atts` JSON blob
- `ship`: `hull_hp` (int)

**Notes:**
- Match the `/loadout` formatting exactly for consistency (pipe separator, no trailing pipe if no stats)
- Round DPS to 1 decimal place: `f"{dps:.1f}"`
- Use `| Shield: {n}` / `| Armour: {n}` (not both on same line unless module has both)

---

## Sub-task B: Fix Empty-Store Announcement Bug

**Problem:** The shop refresh announcement incorrectly says the store is empty even when items have been successfully stocked.

**Root cause (from researcher):** Likely a transaction timing issue — the announcement fires before the shop refresh DB transaction is committed, so the item fetch returns an empty list.

**Fix:**

1. Read `services/bot-core/src/utils/executors/shop_refresh_executor.py` fully — trace the exact order of:
   - `ShopService.refresh_shop()` call
   - DB session commit
   - Item re-fetch for announcement
   - `announce_shop_refresh()` call

2. Confirm whether the item fetch for announcement uses the same open transaction as the refresh, or a fresh session. If the same session/transaction, the items may not yet be visible to the query.

3. **Fix option A (preferred):** Move the item fetch for announcement to AFTER an explicit commit. Ensure the item query opens a new DB session (or uses `expire_on_commit=True` behavior correctly).

4. **Fix option B (fallback):** Pass the already-fetched item list from `refresh_shop()` directly to the announcement function rather than re-fetching — avoids the timing issue entirely.

5. Add diagnostic logging at two points (regardless of which fix is applied):
   ```python
   flogger.info("ShopRefresh: guild=%s tier=%s — refreshed %d items", guild_id, tier, len(items))
   flogger.info("ShopRefresh: announcing %d items for guild=%s tier=%s", len(announce_items), guild_id, tier)
   ```

6. Check `services/bot-core/src/utils/shop_announcement.py` — ensure the "empty store" branch condition is `if not items` (not `if items is None` or some other falsy check that might misfire).

**Files to touch:**
- `services/bot-core/src/utils/executors/shop_refresh_executor.py`
- `services/bot-core/src/utils/shop_announcement.py`
- `services/bot-core/src/services/shop_service.py` (if refresh_shop needs to return items)

---

## Testing Requirements

Run the full bot-core test suite after all changes:
```bash
cd /proj/services/bot-core && timeout 300 python -m pytest tests/ -q --tb=short 2>&1 | tee /tmp/test-botcore-0002.log | tail -30
```

Run discord-gateway cog tests:
```bash
cd /proj/services/discord-gateway && timeout 300 python -m pytest tests/cogs/ -q --tb=short 2>&1 | tee /tmp/test-gateway-0002.log | tail -30
```

Run Ruff linting on all modified files:
```bash
cd /proj && ruff check services/bot-core/src services/discord-gateway/src 2>&1 | head -50
```

All tests must pass. No new Ruff errors.

---

## Completion Criteria

- [ ] Shop item lines include DPS for weapons, Shield/Armour for modules, Hull HP for ships
- [ ] Items with no relevant stat show no suffix (clean, no empty pipes)
- [ ] Stat format matches `/loadout` display style
- [ ] Shop refresh announcement correctly reflects actual item count (no false "empty" messages)
- [ ] Diagnostic logging added to shop refresh executor
- [ ] All tests pass, no Ruff errors
