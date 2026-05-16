# Task 0001 — Bounty Payout Embed + /sell Inactive Ships + /demote Credit Penalty + Bounty Tier Color-Coding

## Overview

Four related fixes/features bundled for efficiency. All confirmed by researcher investigation.

---

## Sub-task A: Bounty Tier Color-Coding (check / capture / escape embeds)

**Problem:** Bounty check, capture, and escape result embeds have no tier-based color differentiation, making it hard to tell at a glance which tier a bounty belongs to.

**Fix:** Apply tier color-coding to all bounty result embeds (check, capture, escape/flee). Use the canonical tier color palette (already established in OPEN_ITEMS.md Appendix A for ENH-02):

```python
TIER_COLORS = {
    "bronze":   0xCD7F32,  # 13467442
    "silver":   0xC0C0C0,  # 12632256
    "gold":     0xFFD700,  # 16766720
    "platinum": 0xE5E4E2,  # 15066082
}
```

**Files to touch:**
- `services/discord-gateway/src/cogs/bountyCog.py` — find all embed constructors for check/capture/escape/flee result flows; set embed color from `TIER_COLORS[bounty_tier.lower()]`
- `services/bot-core/src/utils/bounty_announcement_payload.py` — if bounty spawn embeds also lack tier color, apply there too (check current embed color field)

**Notes:**
- Tier field on bounty responses is likely `division` or `tier` — confirm in the API response schema
- Use `.lower()` when looking up tier to be safe
- Add the `TIER_COLORS` dict as a module-level constant in `bountyCog.py` (or a shared utils file if one exists for cog constants)

---

## Sub-task B: Bounty Payout Catalog Embed (bounty cap announcements)

**Problem:** When a bounty cap is hit, only one embed is sent. A second embed cataloging active bounty payouts is missing.

**Fix:** Add a second embed to bounty cap announcements that shows a payout summary. The "Results" section (or equivalent) should be renamed to **"Payouts"** and incorporate the payout data.

**Embed design:**
- Title: `"💰 Active Bounty Payouts"`
- Color: use tier color (from Sub-task A palette) matching the capped tier
- Fields: group active bounties by tier, showing count and payout range per tier
  - e.g. `Bronze` → `3 active · 250–500 cr each`
  - e.g. `Silver` → `2 active · 1,000–2,500 cr each`
- Footer: `"Capture a bounty with /check"`

**Files to touch:**
- `services/bot-core/src/utils/bounty_announcement_payload.py` — add function `build_bounty_cap_payout_embed(active_bounties: list, capped_tier: str) -> dict` that returns a second embed dict
- `services/bot-core/src/utils/executors/bounty_spawn_executor.py` — find where the bounty cap announcement is sent; extend to include the second embed
- `services/bot-core/src/services/bounty_service.py` — if needed, add a query to fetch active bounties by guild for the payout summary
- `services/bot-core/src/persist/repositories/bounty_repository.py` — add `get_active_by_guild(guild_id)` if not already present

**Rename:** Any section currently labeled "Results" in bounty cap embeds should be renamed to "Payouts".

---

## Sub-task C: /sell — Include Inactive Ships in Inventory

**Problem:** The `/sell` command only shows items from `PlayerInventory`. Ships stored in `PlayerShip` (with `is_active=False`) are invisible and cannot be sold.

**Root cause (from researcher):** Inventory endpoint only queries `PlayerInventory` table; `PlayerShip` table is never joined.

**Fix:**
1. In `services/bot-core/src/persist/repositories/inventory_repository.py` — add method `get_player_ships(player_id: int) -> list[PlayerShip]` that returns all ships for the player
2. In `services/bot-core/src/services/inventory_service.py` (or equivalent sell service) — extend the sell-eligible item fetch to union `PlayerInventory` items with `PlayerShip` rows where `is_active=False`
3. In `services/discord-gateway/src/cogs/shopCog.py` (or whichever cog has `/sell`) — update the sell autocomplete and sell item list to include ships, displaying status indicator for inactive ships: e.g. `"Betty (inactive ship)"`
4. Ensure the sell endpoint in `services/bot-core/src/api/routers/` correctly handles selling a ship (deletes `PlayerShip` row, credits player)

**Data model notes:**
- `PlayerShip` has `is_active: bool` — inactive = not the player's currently-equipped ship
- Active ship (is_active=True) should NOT be sellable — keep that guard
- Ships have no `quantity` concept — treat as quantity=1 per row

---

## Sub-task D: /demote — Credit Penalty Not Applied

**Problem:** The `/demote` command demotes the player's tier and sets a cooldown but never deducts credits. No credit penalty is applied.

**Root cause (from researcher):** `demote_player()` in `player_service.py` has no credit deduction logic. Response schema has no `penalty` field.

**Fix:**
1. In `services/bot-core/src/services/player_service.py` — in `demote_player()`:
   - Calculate penalty: `penalty = int(player.credits * 0.10)` (10% of current credits)
   - Deduct: `player.credits = max(0, player.credits - penalty)`
   - Include `penalty` in the returned dict
2. In `services/bot-core/src/api/schemas/` — update `DemoteResponse` (or equivalent schema) to include `penalty: int`
3. In `services/discord-gateway/src/cogs/playerCog.py` — update the demote result embed to display the penalty:
   - Add field: `"Credit Penalty"` → `f"-{penalty:,} cr"`
   - Or inline in description: `f"Lost **{penalty:,}** credits due to demotion."`

**Notes:**
- If `player.credits` is already 0, penalty should be 0 (the `max(0, ...)` guard handles this)
- Check `OPEN_ITEMS.md` B.95 — the demote success embed may need to be public (not ephemeral); if so, fix that too while in the file

---

## Testing Requirements

Run the full bot-core test suite after all changes:
```bash
cd /proj/services/bot-core && timeout 300 python -m pytest tests/ -q --tb=short 2>&1 | tee /tmp/test-botcore-0001.log | tail -30
```

Run discord-gateway cog tests:
```bash
cd /proj/services/discord-gateway && timeout 300 python -m pytest tests/cogs/ -q --tb=short 2>&1 | tee /tmp/test-gateway-0001.log | tail -30
```

Run Ruff linting on all modified files:
```bash
cd /proj && ruff check services/bot-core/src services/discord-gateway/src --select ALL 2>&1 | head -50
```

All tests must pass. No new Ruff errors.

---

## Completion Criteria

- [ ] Bounty check/capture/escape embeds use tier colors
- [ ] Bounty spawn embeds use tier colors (if not already)
- [ ] Bounty cap announcement sends a second "Payouts" embed with tier-grouped payout data
- [ ] "Results" section renamed to "Payouts" where applicable
- [ ] `/sell` autocomplete and item list includes inactive ships (not active ship)
- [ ] Selling a ship correctly removes the `PlayerShip` row and credits the player
- [ ] `/demote` deducts 10% of credits and shows the penalty in the embed
- [ ] All tests pass, no Ruff errors
