# Package G Design — Loadout↔Inventory Consistency Overhaul (B.19)

*Companion to `/proj/DEFECTS.md` § B.19 and `/proj/recon/B19-recon.md`. All file:line references are HEAD (commit `815cd59`).*

This is a **design-only** document. Implementation will follow in a separate dispatch executed by the same architect.

---

## Goal & contract

Eliminate the loadout↔inventory consistency violations that produce behaviours (a)–(f) of B.19 and the admin item-generation exploit, **without** schema changes to `player_ships` or `player_inventories`. Lock the dual-table model down at the application layer with one new consistency service, transactional discipline at the router boundary, and a one-shot data-repair migration for existing records.

The fix must:
1. Stop the cross-ship duplication that compounds at each `/buy ship`.
2. Make `/equip`, `/unequip`, `/setactive`, `/buy ship`, `/sell-ship`, `/transfer ship`, `admin_remove_ship`, `prestige` all leave `(player_ships.*, player_inventories)` in a self-consistent state.
3. Repair every player's existing corrupt state on first deployment.
4. Be small enough to land in **one cycle**.

---

## Source-of-truth contract

The recon revealed there is currently no documented contract; the runtime behaviour relies on implicit consistency that the code does not enforce. Package G adopts the following explicit contract.

### 1. Per-table semantic role

| Table | Role | Update rule |
|---|---|---|
| `player_ships.{weapons,modules,turrets,secondary_weapons}` JSON | **Equipped slots** of one specific ship instance | Mutated only when the player physically equips/unequips/transfers/sells/discards |
| `player_inventories` rows | **Cargo / unequipped storage** for a player | Mutated only when items are bought, sold, looted, equipped (decrement), unequipped (increment), gifted, prestiged, etc. |

### 2. Total-ownership formula

For any player `P` and any item name `N`:

```
owned(P, N) = sum(qty for inventory_rows where player_id=P and item_name=N)
            + sum(1 for slot_reference in any player_ships JSON
                       where ship.player_id=P and N in slot)
```

`owned(P, N)` is the only quantity that should ever change in response to player-visible actions. It is conserved across `equip`/`unequip` (move between summands), increased by `buy`/`reward`/`admin_give`, and decreased by `sell`/`admin_remove`/`prestige`/`transfer to other player`.

### 3. Hard invariants

- **I1 — No duplication across ships of one player.** For any item name `N` and any player `P`, the total number of slot references to `N` across all of `P`'s ships' JSON columns must not exceed the total number of "equipped instances" the player legitimately holds. Concretely, the design fix enforces the simpler stricter rule: **a single named instance of an item never appears in more than one slot reference at the same time** (i.e. duplication-of-a-single-instance is forbidden; the player may legitimately own 3 distinct copies of *Micro Gun MK I* and have all 3 referenced once each across one or more ships).

- **I2 — No materialisation from nothing.** A flow may add slot references *only* by decrementing inventory or by an explicit "issue" event (admin_give, starter creation, reward). A flow may add inventory rows *only* by decrementing slot references, by a "consume" event (buy/loot), or by the explicit issue path.

- **I3 — Atomicity across both tables.** Any single user action that mutates both `player_ships.*` and `player_inventories` for a player must occur in **one DB transaction**. A crash mid-flow must leave the player in their pre-action state.

- **I4 — Active ship is always in valid configuration.** The player's active ship's slot counts must not exceed the static ship row's `max_*` limits. Any operation that could violate this (e.g. `set_active_ship` to a smaller ship) must reconcile by evacuating overflow to inventory atomically.

### 4. What is NOT enforced (deferred)

- Per-item-class equip limits across multiple ships (e.g. owning the same unique-class module on two ships) — out of scope; the existing `MODULE_EQUIP_LIMITS` only governs same-ship.
- Cargo capacity caps — currently unenforced in the codebase; not changed here.
- Reverse FK from JSON values to inventory rows — schema change, out of scope (see § Data model decision).

---

## Data model decision (Option A vs B)

**Decision: Option A — keep JSON columns, fix consistency at the application layer.**

### Why not Option B (relational `player_ship_slots`)

A schema move from `player_ships.{weapons,modules,turrets,secondary_weapons}` JSON to a `player_ship_slots(id, player_ship_id, slot_kind, slot_index, item_name)` table with a check or trigger linking to `player_inventories` (or to a stable per-player item-instance table) is the architecturally correct answer. It enables DB-level enforcement of I1–I3 via FK + unique constraint and removes whole categories of bugs.

It is rejected here because:

1. **Read-site blast radius.** 17 services and 15 routers consume the JSON columns directly: `LoadoutResponseService`, `loadout_builder`, `loadout_effect_service`, `combat_service.build_loadout`, `equipment_service.equip_check`, every `/ship` and `/loadout` embed builder, every admin and transfer flow. All would need to be migrated to the new shape.
2. **API response shape change.** `ShipResponse.weapons|modules|turrets` are list-of-string fields exposed across both bot-core and discord-gateway. Cogs assemble embeds directly off these. Wire-format compatibility shim would be a separate sub-project.
3. **Test surface.** 162 test files, of which ~40+ touch ship loadout shape directly.
4. **Data migration risk.** Migrating live JSON data into a new relational shape is reversible only with care and would extend the deploy window past one cycle.

Estimated cost — Option A: **~1 cycle**. Option B: **~3–4 cycles** plus deploy risk.

Option A buys correctness today; Option B remains on the table as a longer-horizon architectural follow-up if new defects in this surface appear.

### Cost of Option A

The price of Option A is that we replace a schema-level invariant (impossible to violate) with a service-level invariant (possible to bypass via direct repository calls or future code paths). To mitigate:

- **One canonical mutation choke-point** — a new `LoadoutConsistencyService` that wraps every cross-table mutation. Repositories remain dumb data-access; services orchestrate; routers own the transaction.
- **Lint-style audit** — document in `services/AGENTS.md` that direct calls to `inventory_repo.add_item` / `inventory_repo.remove_item` paired with `player_ship_repo.add_equipment` / `remove_equipment` outside `LoadoutConsistencyService` are **forbidden**. Future PRs that violate this should be rejected at review.

---

## Invariant enforcement strategy

### New module: `services/loadout_consistency_service.py`

Single class, no instance state beyond repository handles (constructor-injection pattern, per `services/AGENTS.md`).

```python
class LoadoutConsistencyService:
    def __init__(self):
        self.player_ship_repo = PlayerShipRepository()
        self.inventory_repo = InventoryRepository()
        self.item_repo = ItemRepository()
        self.ship_repo = ShipRepository()
```

### Public API (all methods take `db` and never commit; caller owns the transaction)

| Method | Purpose |
|---|---|
| `equip_one(db, player_id, ship_id, item_name, equipment_type=None) -> dict` | Atomic: validate, decrement inventory row, append to ship slot list. Replaces the body of `EquipmentService.equip_item` |
| `unequip_one(db, player_id, ship_id, item_name, equipment_type=None) -> dict` | Atomic: remove from ship slot list, increment inventory row. Replaces the body of `EquipmentService.unequip_item` |
| `transfer_loadout_to_new_ship(db, player_id, src_ship, dst_ship, slot_limits) -> dict` | Used by `purchase_ship`. Moves fitting items to `dst_ship`, overflow items to inventory, **clears src_ship slots**. Net: each name appears in exactly one new place |
| `evacuate_ship_loadout_to_inventory(db, ship) -> list[str]` | Used by `sell_ship clear_equipment=True`, `transfer_ship`, `admin_remove_ship`. Moves all equipped items from `ship.*` JSON to inventory, clears `ship.*` JSON. Operates only on items already legitimately referenced on this ship — never materialises from nothing (I2) |
| `reconcile_active_ship_slots(db, player_id, target_ship_id) -> dict` | Used by `set_active_ship`. Compares target ship's current loadout against static ship's `max_*`; evacuates overflow to inventory; returns a structured report so router/cog can show "X items moved to cargo" |
| `repair_player(db, player_id, *, dry_run=False) -> dict` | Used by the data-fixup migration AND available as an admin tool. Detects duplicate slot references across ships for one player and reconciles per the rules in § Data fixup migration |

### Choke-point rule

After Package G:
- `EquipmentService.equip_item` and `.unequip_item` become thin wrappers that delegate to `LoadoutConsistencyService.equip_one` / `.unequip_one` (preserving public signature for callers).
- `ShopService.purchase_ship` calls `LoadoutConsistencyService.transfer_loadout_to_new_ship` instead of inlining the slot-copy block.
- `ShopService.sell_ship`, the `transfer_ship` router, and `admin_remove_ship` all call `LoadoutConsistencyService.evacuate_ship_loadout_to_inventory` instead of inlining their own loops.
- `set_active_ship` (the router) calls `LoadoutConsistencyService.reconcile_active_ship_slots` before flipping `is_active`.
- `_create_starter_loadout` calls `LoadoutConsistencyService.equip_one` in a loop instead of writing JSON directly.

This collapses 4–5 places that mutate slot+inventory together into 1 module that gets the invariant right once.

### Where invariants are checked

- **Service layer (`LoadoutConsistencyService`)** is responsible for enforcing I1, I2 on each operation by construction. No "post-hoc validation" — the methods are written so that they cannot violate the invariants.
- **Repository layer** stays dumb (current pattern, per `repositories/AGENTS.md`). No invariant logic added there.
- **Router layer** owns the transaction (I3) per § Transactional boundary policy.
- **Audit/repair function** (`repair_player`) is the safety net for legacy corrupt data and for anything the choke-point misses; runs once via migration and is also exposed as an admin tool.

---

## Transactional boundary policy

**Decision: router owns the transaction; services use `commit=False` throughout. Apply universally to every flow that touches loadout or inventory.**

This is the existing pattern from A.42/A.44 (`/buy ship`, `/sell`, `/sell-ship`, `/transfer ship`) — it works and is the reference implementation. Package G extends it to `/equip`, `/unequip`, `/set-active`, and `_create_starter_loadout`.

### Router pattern

```python
@router.post("/{ship_id}/equip", response_model=ShipResponse)
async def equip_item(...):
    try:
        async with get_db_session() as db, db.begin():
            result = await equipment_service.equip_item(db, ...)
            return ShipResponse(...)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
```

### Service-side change requirements

Every method called from the router during a transactional flow must:
1. Accept a `commit: bool = True` parameter where it currently has none, OR
2. Be refactored to never call `db.commit()` itself — relying entirely on the caller.

For Package G, the simpler approach is option (2) for the new `LoadoutConsistencyService` (it never commits, full stop) and option (1) for the existing `equipment_service.equip_item`/`unequip_item` wrappers (kept for backward compatibility with any direct test or non-router caller).

### Repository changes

`PlayerShipRepository.set_active_ship`, `add_equipment`, `remove_equipment`, `update_loadout`, `update_nickname` currently each call `await db.commit()`. We need a `commit: bool = True` parameter on each of these for the new transactional callers, mirroring the pattern already used by `InventoryRepository` (see `inventory_repository.py:42`).

This is a small, mechanical change — six methods × ~5 lines each = ~30 LOC.

---

## Per-flow specifications

For each affected flow: current behaviour → new behaviour → invariants enforced → atomicity boundary.

### `_create_starter_loadout` (player_service.py:108)

**Current:** Writes Betty with weapons + modules baked into JSON; adds Micro Gun MK I to inventory. Three items end up in JSON with no inventory-row history (recon root cause #1).

**New:**
1. Create the `PlayerShip` row for Betty with **empty** slot lists (`weapons=[]`, `modules=[]`, `turrets=[]`, `secondary_weapons=[]`).
2. Add all four starter items to inventory: `Nirai Impulse EX 1` (×1, `primary_weapon`), `E2 Exoclad` (×1, `module`), `Telta Quickscan` (×1, `module`), `Micro Gun MK I` (×1, `primary_weapon`).
3. Call `LoadoutConsistencyService.equip_one(db, player.id, betty.id, "Nirai Impulse EX 1", commit=False)`.
4. Call `equip_one` for `E2 Exoclad`, then for `Telta Quickscan`.
5. (Micro Gun MK I stays in inventory — slots are full at 1/1; this matches today's observable state.)
6. Caller (the player-creation router or the existing `_create_new_player`) owns the transaction.

**Net DB state — identical to today's intended state:** Betty has `weapons=["Nirai Impulse EX 1"]`, `modules=["E2 Exoclad","Telta Quickscan"]`. Inventory has 1 row for `Micro Gun MK I`. **But every item's path was through the choke-point**, so I2 holds and the audit trail is correct.

**Invariants enforced:** I1 (equip_one ensures one slot reference per equip), I2 (every JSON entry came from a decremented inventory row), I3 (router-owned transaction).

**Atomicity:** the existing `_create_new_player` chain is one logical unit. Move the implicit transaction explicit by having the caller (the players router's create-or-get endpoint) wrap in `db.begin()` if it does not already.

---

### `purchase_ship` (shop_service.py:225)

**Current:** Copies old ship's loadout to new ship (fitting subset), pushes overflow to inventory, BUT never clears old ship's loadout. Old ship retains its JSON; new ship gets a copy. Recon root cause #2.

**New:** Replace the inline loop (lines 307–363) with one call:

```python
transfer_result = await loadout_service.transfer_loadout_to_new_ship(
    db,
    player_id=player_id,
    src_ship=old_player_ship,        # may be None
    dst_ship=new_player_ship,
    slot_limits=slot_limits,
    commit=False,
)
```

`transfer_loadout_to_new_ship` semantics:
1. If `src_ship is None`: do nothing. Return zero-counts dict.
2. For each of (`weapons`, `modules`, `turrets`, `secondary_weapons`):
   - Read `src_items = list(getattr(src_ship, kind) or [])`.
   - `fitting = src_items[:slot_limits[kind]]`; `overflow = src_items[slot_limits[kind]:]`.
   - Resolve concrete item type for each overflow item via `ItemRepository.get_by_name_any_type` + `item_discriminator_to_concrete_type` (existing pattern, line 345 in current shop_service).
   - For each overflow item: `inventory_repo.add_item(..., commit=False)`.
   - Set `dst_ship[kind] = fitting`.
   - **Set `src_ship[kind] = []` (the missing clear step).**
3. Return `{transferred: int, overflowed: int, breakdown: {weapons: [...], modules: [...], ...}}`.

**Note on the "loadout carry-over" feature (recon open Q #1):** we treat it as **intentional and kept**. It provides a useful UX (your new ship is ready to fly with what you had). The fix is making it correct, not removing it. A follow-up issue may revisit whether to add a `keep_old_loadout` flag, but that is out of scope.

**Note on `sell_old_ship=True`:** when the old ship is being traded in (deleted), the clear step is moot but harmless — we still set `src_ship.weapons = []` etc. before `db.delete(src_ship)` so that a transient flush in between sees a consistent state.

**Invariants enforced:** I1 (each name appears on exactly the new ship after the call), I2 (overflow goes through inventory), I3 (router already wraps in `db.begin()` per shops.py:152).

---

### `set_active_ship` (player_ship_repository.py:128 + ships.py:229)

**Current:** Repository flips `is_active`. Router additionally calls `player_repo.update_active_ship`. No slot reconciliation (recon root cause #3). Two separate commits (atomicity break).

**New:**

Repository `set_active_ship` gains `commit: bool = True` parameter (new): when `False`, replaces `db.commit()` with `db.flush()`.

Router `PUT /ships/{ship_id}/set-active` becomes:

```python
async with get_db_session() as db, db.begin():
    # 1. Reconcile target ship's slots against its static max_* if it exceeds.
    reconcile = await loadout_service.reconcile_active_ship_slots(
        db, player_id=player_id, target_ship_id=ship_id, commit=False
    )
    # 2. Flip active flag (single transaction, single flush)
    ship = await player_ship_repo.set_active_ship(db, player_id, ship_id, commit=False)
    await player_repo.update_active_ship(db, player_id, ship_id, commit=False)
    # response includes reconcile.evacuated_items so cog can render notice
```

`reconcile_active_ship_slots` semantics:
1. Load the target `PlayerShip`. Load static `Ship` row.
2. For each slot kind: compute `current = list(target.kind)`, `cap = static.max_<kind>`.
3. If `len(current) > cap`: split into `keep = current[:cap]` and `evacuated = current[cap:]`. Set `target.kind = keep`. For each `evacuated` item, push to inventory via concrete type resolution.
4. Return `{"evacuated_items": {weapons: [...], modules: [...], ...}, "any_evacuated": bool}`.

**UX note for cog (out-of-scope informational):** the response's `evacuated_items` field lets the cog show a "🟡 Switched to Betty. The following items were moved to your cargo: …" notice. The cog change is small and will be picked up by the cog when bot-core's response shape evolves; *the design here only specifies the bot-core side*.

**Alternative considered and rejected:** prevent the switch when overflow would occur, requiring manual `/unequip` first. Rejected because (i) it converts a one-click action into many, (ii) the player has no way to unequip from a not-yet-active ship via the current cog UI, (iii) auto-evacuating to cargo is non-destructive.

**Invariants enforced:** I3, I4.

---

### `equip` / `unequip` (ships.py:381 / ships.py:432)

**Current:** Router uses bare `get_db_session()`. `EquipmentService` calls `add_equipment` (own commit) then `remove_item` (own commit). Crash between → ship has the item AND inventory still has it. Two separate commits, double-mutation risk if the second one fails. Recon § 3.

**New:**

Router wraps both calls in `async with get_db_session() as db, db.begin():` (the existing A.42/A.44 pattern). `EquipmentService.equip_item` and `.unequip_item` are refactored to call `LoadoutConsistencyService.equip_one` / `.unequip_one`, which:
- Use `add_equipment(db, ..., commit=False)` (new param on the repo)
- Use `remove_item(db, ..., commit=False)` (existing param)
- Never call `db.commit()` themselves
- Validate ownership, slot availability, MODULE_EQUIP_LIMITS up-front and raise `ValueError` (mapped to 400) if invalid — no partial state.

**Invariants enforced:** I3 (single transaction; either both writes happen or neither does), preserves I1/I2 (which the existing logic already does correctly, modulo atomicity).

---

### `sell_ship` / `transfer_ship` / `admin_remove_ship`

**Current:** Each inlines its own loop that resolves item types and pushes ship JSON entries to inventory (shop_service.py:592, ships.py:610, admin.py:1079). Each call site is correct in isolation but **blindly trusts that ship JSON entries correspond to legitimately-owned items** — which, given the duplication bug from `purchase_ship`, can be false. Result: phantom items get materialised into real inventory rows on these flows (recon § 5: the admin item-generation exploit).

**New:**
1. Replace each inline loop with a call to `LoadoutConsistencyService.evacuate_ship_loadout_to_inventory(db, ship, commit=False)`.
2. `evacuate_ship_loadout_to_inventory` does the same item-type resolution as today (concrete types via STI discriminator) but additionally **clears the ship's slot lists** as part of the same transaction. This is invariant-preserving but more importantly it makes the flow idempotent: calling it twice on the same ship produces no items the second time.
3. **Anti-duplication guard.** Before evacuation, the helper checks that the ship's slot references **are not currently duplicated on any of the player's other ships**. If a duplicate is detected (legacy corrupt data only — should not occur after migration), the helper logs a warning and removes the duplicate from the *other* ship rather than minting a new inventory row. The "winning" copy goes to inventory; the "losing" copy is silently dropped. This closes the exploit surface even on legacy data that slipped past the migration.
4. `admin_remove_ship` and `transfer_ship`: routers should already wrap in `async with db.begin()` (admin.py currently uses bare session — see § Atomicity gap below). `transfer_ship` already uses `db.begin()` (ships.py:570).

#### Atomicity gap in `admin_remove_ship`

`admin_remove_ship` currently uses `async with get_db_session() as db:` only — no `db.begin()`. The `inventory_repo.add_item(...)` call at admin.py:1098 commits implicitly, then `player_ship_repo.remove(db, ship)` at admin.py:1102 commits separately. **Add `db.begin()` here too** (mirrors `/transfer ship`).

**Invariants enforced:** I1, I2 (anti-duplication guard prevents materialisation from nothing), I3 (transaction wrap).

---

### `prestige_player` (player_service.py:294)

**Current:** Resets XP, credits, tier, prestige_count; clears `player_inventories`; **preserves all `player_ships.*` JSON loadouts**. Result: post-prestige equipped items are phantoms with no inventory provenance. Recon § 3 atomicity table.

**New:** Two acceptable resolutions. Recommended choice in **bold**.

- **Option P1 (recommended): clear ship loadouts alongside inventory.** Aligns with prestige's "reset progress" intent. After prestige, every ship the player owns has empty slots. Player may re-equip from a now-empty inventory only via future `/buy` and `/equip`. Simplest semantics; mirrors the visual intent ("prestige wipes your gear").

- Option P2 (rejected): rebuild inventory from ship JSON before clearing inventory. Means the player keeps everything they had equipped, dressed up as cargo. Conflicts with "reset" intent and lets the player keep arbitrarily many items by stuffing them on inactive ships pre-prestige.

**Implementation under Option P1:**
1. Iterate the player's ships; for each, set `weapons = modules = turrets = secondary_weapons = []`.
2. Then call `clear_player_inventory` as today.
3. All in one transaction. The existing `prestige_player` already commits at the end; convert to caller-managed transaction or keep but ensure both sets of writes are in the same commit (they are, under the current single `db.commit()` at line 339).

The router (`POST /players/{id}/prestige`) should wrap in `db.begin()` for consistency with the new pattern (currently does not — players.py uses bare session).

**Invariants enforced:** I1 (no orphan slot refs), I2 (clearing both sides keeps total ownership = 0 after wipe), I3 (single transaction).

---

## Data fixup migration (existing corrupt records)

**Decision: one-shot Alembic data migration.** Reproducible, bounded, runs once, leaves a record. Not a startup sweep (would re-execute on every restart and potentially mask new bugs); not an admin command (requires manual operator intervention per guild and won't fix all guilds reliably).

### Migration shape

`services/bot-core/src/persist/database/revisions/versions/<rev>_b19_repair_loadout_consistency.py`

```python
revision = "..."
down_revision = "<previous head>"
```

The migration does NOT modify schema (Option A). It runs a Python data-fixup pass.

### Repair logic per player

For each `player_id` in `players`:

1. Load all of the player's `player_ships` rows (active first).
2. Build `seen: dict[(item_name, kind), (winning_ship_id, slot_index)]`.
3. For each ship in iteration order:
   - For each `kind` in (`weapons`, `modules`, `turrets`, `secondary_weapons`):
     - For each item name in that ship's slot list:
       - If `(item_name, kind)` not in `seen`: keep this reference; record it as the winner.
       - Else: this is a duplicate. Remove it from this ship's slot list. **Do not** create an inventory row (preserves I2: no materialisation from nothing during repair).
4. Persist updated slot lists for any modified ships.
5. Validate (post-condition): for the player, no item name appears in slot references of more than one ship × one kind position.

### Tie-breaking ("winning ship")

Iteration order: active ship first, then non-active by `id` ascending. Rationale: the active ship is the one the player visually has equipped right now; preserving its loadout minimises observable disruption.

### Phantom-starter handling

For each player, after the dedupe pass, the player will have at most one slot reference per `(item_name, kind)`. Some of those may still be "phantom" in the strict sense (no historical inventory row). **The migration deliberately does not touch them.** Phantoms on a single ship cause no exploit (admin_remove_ship / transfer_ship will properly evacuate them once, with no duplicate to inflate the count). Leaving them in place preserves player UX (Betty still has her starter modules equipped).

### Idempotency

The migration is naturally idempotent: a re-run on already-clean data finds no duplicates and does nothing.

### Logging

Each modification is logged with `flogger.warning("B.19 repair: removed duplicate %s from player_ship %d (kept on player_ship %d)", item_name, losing_ship_id, winning_ship_id)`. A summary line at the end reports total players scanned, total duplicates removed, total ships modified.

### Performance

For ~1000 guilds × ~10 players × ~3 ships, this is ~30,000 rows of `player_ships` to scan. The migration is single-process and can complete inside the standard MigrationManager `ensure_current` startup window.

### Rollback

Down-migration is a no-op (we only deleted illegitimate duplicate references; "restoring" them would re-introduce the bug). Document this explicitly in the migration file.

---

## Test strategy

This is the largest test surface in the project and the design must reflect that.

### 1. Service-layer unit tests (`tests/services/test_loadout_consistency_service.py`)

New file. **Max 2 mocks per test** (per `tests/AGENTS.md`). Use real `PlayerShip`, `PlayerInventory`, `Ship` instances with fixture data.

Methods covered (≥3 tests each):
- `equip_one` — happy path; missing inventory; slot full; module-class limit conflict
- `unequip_one` — happy path; not equipped; auto-detect type; auto-detect failure with fallback scan
- `transfer_loadout_to_new_ship` — happy path same slot counts; new ship has fewer slots (overflow); new ship has more slots; src is None; src has empty slots; src has secondary_weapons
- `evacuate_ship_loadout_to_inventory` — happy path; ship with empty slots (no-op); ship with item not in item table (logged warning); ship whose item is duplicated on another player ship (anti-duplication guard removes the other side)
- `reconcile_active_ship_slots` — happy path no overflow; weapons overflow; modules overflow; multiple kinds overflow; target ship not owned by player
- `repair_player` — clean state (no-op); single duplicate weapons; modules duplicated across 3 ships; mixed duplicates across kinds; dry_run mode reports without mutating

### 2. Per-flow service tests (extend existing files)

- `tests/services/test_player_service.py` — add `_create_starter_loadout` test that asserts post-state: 1 inventory row (Micro Gun MK I), 1 ship with weapons=[Nirai], modules=[E2,Telta]. Add `prestige_player` test asserting both inventory AND ship slots are cleared.
- `tests/services/test_shop_service.py` — extend `purchase_ship` tests: assert `old_player_ship.weapons == []` after the call (regression for the "never cleared" bug). Add slot-count-mismatch overflow test.
- `tests/services/test_equipment_service.py` — verify `equip_item` and `unequip_item` delegate to `LoadoutConsistencyService` and propagate the `commit=False` semantics.

### 3. Router/integration tests (`tests/api/`)

- `tests/api/test_ships_router.py` — add `set_active_ship` test that switches from a 2-weapon ship to a 1-weapon ship and asserts response contains the evacuated item AND inventory row exists. Add `equip` test that simulates a mid-flow exception and asserts both tables roll back together (via mocked exception in the second repo call).
- `tests/api/test_shops_router.py` — full purchase_ship integration: starting state with old ship loadout → buy ship → assert old ship JSON empty, new ship JSON populated, inventory unchanged for fitting items, inventory increased for overflow.
- `tests/api/test_admin_router.py` — adversarial: pre-state ship has duplicated phantom item also on another player ship → call admin_remove_ship → assert inventory row count for that item did NOT increase by 2 (the anti-duplication guard kicked in).

### 4. Property tests (new — `tests/integration/test_loadout_consistency_property.py`)

Use a deterministic random seed and a small action vocabulary:

```
ACTIONS = [
    ("equip", item_name, ship_id),
    ("unequip", item_name, ship_id),
    ("buy_ship", shop_item_id),
    ("sell_ship", ship_id),
    ("transfer_ship", ship_id, other_player),
    ("set_active", ship_id),
]
```

Property tested:
> After any sequence of N actions (N up to 50), for every player and every item name `N`,
> the sum `(slot references) + (inventory quantity)` equals the player's "ledger" — a
> separately maintained ground-truth count incremented on `buy`/`reward` and decremented on
> `sell`/`transfer-out`.

Run on 50 random seeds × 20 sequence lengths. Failure produces the action sequence that broke consistency, which is itself a regression test seed.

This is the strongest test in the package: it catches any bug where a flow loses or gains an item.

### 5. Adversarial / exploit tests

In `tests/api/test_admin_router.py`:
- Set up a player with a phantom item duplicated across 3 ships (legacy state).
- Call `admin_remove_ship` on each in sequence.
- Assert: only ONE inventory row is created across the three calls (the first one finds the live reference and evacuates it; the next two find the cleared slots and do nothing; the anti-duplication guard during the first call removes references from the other two).

In `tests/api/test_ships_router.py`:
- Same but for `transfer_ship` repeated against a phantom-dup state.

### 6. Migration test (`tests/test_migration_b19_repair.py`)

- Build an in-memory SQLite DB (or a transactional Postgres fixture) with the corrupt state observed in the recon (Betty/Hera/Terran with duplicated modules).
- Run the migration.
- Assert: post-state has each module in exactly one ship; player iteration finds no duplicates.

### Test count summary

| Category | Approx new tests |
|---|---|
| `LoadoutConsistencyService` units | 25 |
| Per-flow service additions | 10 |
| Router integration | 10 |
| Property tests | 1 (with 1000+ generated cases) |
| Adversarial / exploit | 4 |
| Migration | 3 |
| **Total** | **~53 new tests** |

---

## Backward compatibility

### Player data

Existing player records are corrupt by definition (every account has at least the starter-derived phantoms). The data-fixup migration handles this. **No deploy can skip the migration**; `MigrationManager.ensure_current()` will block startup if migrations are pending, which is the existing safety mechanism.

### API responses

No changes to `ShipResponse`, `TransactionResponse`, `EquipCheckResponse`. The `set_active_ship` endpoint *gains* a `evacuated_items` field on its response; existing consumers that ignore unknown fields (Pydantic default) are unaffected. Discord-gateway cogs that want to display the evacuation notice can pick it up; the design does not require them to do so in this cycle.

### Test breakage

Several existing tests mock around the bug we're fixing. Expected breakage:
- Tests that pre-populate `player_ships.modules` with names that have no inventory row, then exercise an admin/transfer flow expecting to see those names in inventory afterward. After the fix, the anti-duplication guard or the empty-pre-state will not produce those names. **These tests are testing the bug.** They should be updated to match the new contract.
- Tests that assert `equip_item` and `unequip_item` are non-atomic (e.g. that asserted state mid-flow). Should be removed.

The recon's existing tests verifying the **observed** corrupt state (e.g. anything checking that buying a new ship copies the loadout to both ships) need to be updated to reflect the new behaviour (loadout transfers to the new ship and the old ship is empty).

Estimated test churn: ~10–15 existing tests will need adjustment.

### Direct repository callers

Code that calls `player_ship_repo.add_equipment` / `remove_equipment` directly (bypassing the new service) is rare. A grep across services/routers should find none post-refactor; the only legitimate callers will be `LoadoutConsistencyService` internals and the existing `update_loadout` path used by the `PUT /ships/{id}/loadout` endpoint (which is an admin-style direct override and is not an invariant-checked path — flag as a follow-up issue).

---

## Out-of-scope decisions

Things considered and explicitly **not** in Package G:

1. **Option B relational `player_ship_slots` refactor.** Rejected for cycle-cost reasons (§ Data model decision). Kept on the long-horizon list.
2. **Cargo capacity caps.** Currently unenforced; out of scope.
3. **Cross-ship per-class equip limits.** E.g. owning the same unique-class module on two ships. The existing `MODULE_EQUIP_LIMITS` only governs same-ship; cross-ship enforcement is a game-balance question, not a consistency question.
4. **`PUT /ships/{id}/loadout` admin override.** This endpoint allows arbitrary loadout assignment without inventory reconciliation. It is currently used by no documented flow but exists in the API. Flag as a **follow-up issue**: either delete the endpoint, or wrap it in the consistency service like every other path. Out of scope for Package G.
5. **`keep_old_loadout=False` flag on `purchase_ship`.** Discussed under § purchase_ship; out of scope. Follow-up issue.
6. **Discord-gateway cog UX changes** (e.g. the "X items moved to cargo" toast on `/setactive`). The design provides the data on the bot-core side; the cog consumption is a separate package.
7. **B.2 `secondary_weapons` NULL on starter Betty.** Adjacent issue; the new `_create_starter_loadout` writes `secondary_weapons=[]` (line 124 already does this), so B.2 may close incidentally, but the design does not depend on it.
8. **Auditing every existing direct mutation site for invariant compliance.** A one-time grep is included in the implementation order, but a recurring audit infrastructure is not built.
9. **Data export tooling for affected players** (e.g. proactive "your ship lost an item" notice). Not feasible without per-player history; the migration is silent.

---

## Implementation order

The implementation phase will execute the following order strictly. Each step is independently testable.

### Step 1 — Repository plumbing (foundation)

- Add `commit: bool = True` parameter to:
  - `PlayerShipRepository.set_active_ship`
  - `PlayerShipRepository.add_equipment`
  - `PlayerShipRepository.remove_equipment`
  - `PlayerShipRepository.update_loadout`
  - `PlayerShipRepository.update_nickname`
  - `PlayerRepository.update_active_ship` (verify whether it needs the param; add if not present)
- Each: when `commit=False`, replace `db.commit()` with `db.flush()`; on exception, only rollback when `commit=True`.
- No behaviour change for existing callers (all pass `True` by default).
- Tests: extend `tests/repositories/test_player_ship_repository.py` to cover `commit=False` semantics.

### Step 2 — `LoadoutConsistencyService` (new module)

- Create `services/loadout_consistency_service.py` with all six public methods.
- Internal helpers: `_resolve_concrete_type`, `_get_static_ship_caps`, `_remove_one_slot_reference_from_other_ships`.
- Add `services/AGENTS.md` entry documenting the choke-point rule.
- Tests: `tests/services/test_loadout_consistency_service.py` (≥25 tests, see § Test strategy).

### Step 3 — Refactor `EquipmentService.equip_item` / `unequip_item`

- Delegate to `LoadoutConsistencyService.equip_one` / `.unequip_one`.
- Preserve public signatures and return-dict shape (callers in tests / cogs should not break).
- Tests: existing `tests/services/test_equipment_service.py` should still pass; add 2–3 tests verifying delegation.

### Step 4 — Fix `_create_starter_loadout`

- Refactor per § Per-flow specifications.
- Verify post-state matches recon's "intended" Betty layout.
- Tests: extend `tests/services/test_player_service.py`.

### Step 5 — Fix `purchase_ship`

- Replace inline loop with `LoadoutConsistencyService.transfer_loadout_to_new_ship`.
- Verify old ship is cleared; verify overflow goes to inventory.
- Tests: extend `tests/services/test_shop_service.py`.

### Step 6 — Fix `set_active_ship` (router)

- Add `LoadoutConsistencyService.reconcile_active_ship_slots` call.
- Wrap router in `async with db.begin()`.
- Add `commit=False` to inner repo calls.
- Add `evacuated_items` to response.
- Tests: extend `tests/api/test_ships_router.py`.

### Step 7 — Wrap `/equip` and `/unequip` routers

- Add `async with db.begin()` to both.
- Verify `EquipmentService` no longer commits internally.
- Tests: rollback-on-mid-flow-exception test in router test file.

### Step 8 — Fix `prestige_player`

- Add ship-loadout clearing per § Per-flow specifications (Option P1).
- Wrap router in `db.begin()`.
- Tests: extend `tests/services/test_player_service.py`.

### Step 9 — Refactor `sell_ship`, `transfer_ship`, `admin_remove_ship`

- Replace inline evacuation loops with `LoadoutConsistencyService.evacuate_ship_loadout_to_inventory`.
- Add `db.begin()` to `admin_remove_ship` router (currently missing).
- Tests: adversarial tests (§ Test strategy 5).

### Step 10 — Data-fixup migration

- Generate revision: `python -m persist.database.run_migration revision -m "B19 repair loadout consistency"`.
- Implement `upgrade()` per § Data fixup migration.
- `downgrade()` is a no-op with documentation.
- Tests: `tests/test_migration_b19_repair.py`.

### Step 11 — Property tests

- `tests/integration/test_loadout_consistency_property.py` with the action vocabulary.
- Run with at least 1000 generated cases.

### Step 12 — Documentation

- Update `services/AGENTS.md` with the choke-point rule.
- Update `repositories/AGENTS.md` with the new `commit` parameters on `PlayerShipRepository` methods.
- Update `DEFECTS.md` § B.19 with the resolution note (will happen at commit time, not as part of this design).

### Sanity gates

After step 5: cross-ship duplication should already be fixed for new actions (regression test on `purchase_ship`).
After step 7: atomicity holes are closed.
After step 10: existing corrupt data is repaired.
After step 11: invariants are mechanically verified.

---

## Effort estimate

### Files touched

| File | Type | Δ LOC (approx) |
|---|---|---|
| `services/loadout_consistency_service.py` | new | +350 |
| `services/equipment_service.py` | refactor | -50, +30 |
| `services/player_service.py` (`_create_starter_loadout`, `prestige_player`) | refactor | -20, +40 |
| `services/shop_service.py` (`purchase_ship`, `sell_ship`) | refactor | -50, +20 |
| `persist/repositories/player_ship_repository.py` | param additions | +30 |
| `persist/repositories/player_repository.py` | param check | +5 |
| `api/routers/ships.py` (`equip`, `unequip`, `set_active_ship`, `transfer_ship`) | wrap transactions | +25 |
| `api/routers/admin.py` (`admin_remove_ship`) | wrap transaction, refactor | +5, -20, +5 |
| `api/routers/shops.py` | (already wrapped) | 0 |
| `api/routers/players.py` (`prestige`) | wrap transaction | +3 |
| `api/schemas/ships_schema.py` | add `evacuated_items` | +10 |
| `persist/database/revisions/versions/<rev>_b19_repair.py` | new migration | +120 |
| `services/AGENTS.md` | doc update | +30 |
| `persist/repositories/AGENTS.md` | doc update | +15 |
| **Production code subtotal** |  | **~+650 / -140 net +510** |
| `tests/services/test_loadout_consistency_service.py` | new | +600 |
| `tests/services/test_player_service.py` | extend | +80 |
| `tests/services/test_shop_service.py` | extend | +120 |
| `tests/services/test_equipment_service.py` | adjust | +30, -20 |
| `tests/api/test_ships_router.py` | extend | +150 |
| `tests/api/test_shops_router.py` | extend | +80 |
| `tests/api/test_admin_router.py` | extend (adversarial) | +100 |
| `tests/integration/test_loadout_consistency_property.py` | new | +200 |
| `tests/test_migration_b19_repair.py` | new | +120 |
| `tests/repositories/test_player_ship_repository.py` | extend | +50 |
| **Test code subtotal** |  | **~+1530** |
| **Grand total** |  | **~+2040 / -160 net ~+1880 LOC** |

### Test count

~53 new tests + adjustments to ~10–15 existing tests.

### Calendar estimate

**One cycle (Ralph loop dispatch)** for the implementation phase, given:
- The architectural blueprint (this document) eliminates design questions.
- The choke-point pattern means each per-flow change is small (replace a loop with a call).
- The migration is straightforward and bounded.
- The property test gives a strong correctness signal before merge.

If the property test surfaces a non-trivial bug (likely, given the surface area), allow a half-cycle buffer for fixup.

### Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Migration corrupts a player with unusual state | low | Migration is read-then-write per player, idempotent, logged. Pre-flight dry-run mode available via `repair_player(dry_run=True)`. |
| Property test reveals a design flaw | medium | The 12-step order isolates each fix, so any regression is localised. |
| Cog-side breakage from `set_active_ship` response shape | low | Pydantic ignores unknown fields by default; cogs continue to work without picking up the new field. |
| Test churn larger than estimated | medium | Allocate buffer; ~10–15 existing tests may need adjustment. |
| Concurrent cycles touching the same files | low | Coordinate with adjacent packages (none currently in flight on these files per `DEFECTS.md`). |

---

*Design completed: 2026-04-29 by Architect. Ready for implementation dispatch.*
