# B.19 — Technical Recon Detail

*Companion to DEFECTS.md § B.19. All file:line citations are from HEAD (commit `815cd59`).*

---

## 1. Schema facts (verified from source)

`player_ships` table:
- `weapons`, `modules`, `turrets`, `secondary_weapons` — all `json` columns (see `player_ship.py` model)
- No FK constraint from any JSON value to `player_inventories`
- No DB-level enforcement that a name in `player_ships.weapons` corresponds to a `player_inventories` row

`player_inventories` table:
- Keyed on `(player_id, item_type, item_name)` — unique per `inventory_repository.get_player_item()`
- `item_type` must be a concrete type: `primary_weapon`, `secondary_weapon`, `turret_weapon`, `module`, `ship`
- No FK to `player_ships`

**The two tables are entirely decoupled at the schema level.** Consistency is a pure application-layer responsibility with no DB safety net.

---

## 2. Verified code paths per behavior

### (a) Modules absent from `player_inventories` but present in `player_ships.modules`

**Root cause**: `player_service.py:_create_starter_loadout()` (lines 108–139)

```python
# line 117–126: creates starter ship with modules baked in
starter_ship_data = {
    "player_id": player.id,
    "ship_name": "Betty",
    "is_active": True,
    "weapons": ["Nirai Impulse EX 1"],
    "modules": ["E2 Exoclad", "Telta Quickscan"],  # ← placed in ship JSON
    "turrets": [],
}
starter_ship = await player_ship_repo.create_or_update(db, starter_ship_data)

# line 132–133: only Micro Gun MK I is added to inventory; E2 Exoclad, Telta Quickscan, Nirai Impulse EX 1 are NOT
await inv_repo.add_item(db, player.id, "primary_weapon", "Micro Gun MK I", quantity=1)
```

This creates an immediate inventory deficit at player creation:
- `E2 Exoclad` → in `player_ships.modules`, 0 rows in `player_inventories`
- `Telta Quickscan` → in `player_ships.modules`, 0 rows in `player_inventories`
- `Nirai Impulse EX 1` → in `player_ships.weapons`, 0 rows in `player_inventories`

These phantom module references then propagate to every subsequent ship purchase (see behavior d).

**Transactional boundary**: Both tables written in the same session/commit chain (`add_item` uses `commit=True`). The inconsistency is semantic, not a transaction failure.

---

### (b) Same weapon equipped on multiple ships (single inventory row)

**Root cause**: `shop_service.py:purchase_ship()` (lines 323–363)

```python
# line 323: only runs if old_player_ship exists — regardless of sell_old_ship flag
if old_player_ship:
    # ...
    for equip_type in ("weapons", "modules", "turrets", "secondary_weapons"):
        old_items = list(getattr(old_player_ship, equip_type) or [])
        max_slots = slot_limits[equip_type]
        fitting = old_items[:max_slots]
        overflow = old_items[max_slots:]
        items_transferred[equip_type] = fitting
        # overflow items are returned to inventory (lines 344–357)

    # line 360–363: new ship gets the copied loadout
    new_player_ship.weapons = items_transferred["weapons"]
    new_player_ship.modules = items_transferred["modules"]
    new_player_ship.turrets = items_transferred["turrets"]
    new_player_ship.secondary_weapons = items_transferred["secondary_weapons"]

# ← MISSING: old_player_ship.weapons is NEVER cleared
# ← MISSING: old_player_ship.modules is NEVER cleared
```

After purchase:
- New ship's JSON = copy of old ship's JSON
- Old ship's JSON = UNCHANGED (still has all items)
- Inventory = unchanged (no items removed)

Both ships reference the same item names. `Micro Gun MK I` (qty=1 in inventory) is now referenced in `player_ships.weapons` for both Hera (id=5) AND Terran (id=7).

**Transactional boundary**: `shops.py:purchase_ship` router (line 152) wraps with `async with get_db_session() as db, db.begin()` → single transaction. The bug is semantic (old ship not cleared), not transactional.

---

### (c) Weapon reference on ship without inventory ownership (`M6 A4 "Raccoon"`)

**Combined root cause**: behaviors (a) + (b)

- M6 A4 "Raccoon" was legitimately purchased and was in inventory at step 2 of the action sequence
- Step 3 (`/equip` swap): `equipment_service.equip_item()` removed it from inventory (line 194) and added it to Hera's loadout (line 191) — correct
- Step 5 (`/buy` Terran Battlecruiser): `purchase_ship()` copied Hera's loadout (including M6 A4 "Raccoon") to Terran WITHOUT clearing Hera
- Result: M6 A4 "Raccoon" appears in `player_ships.weapons` for both Hera and Terran, with 0 inventory rows

---

### (d) Module references duplicated across all ships

**Root cause**: behaviors (a) + (b) compounded over multiple ship purchases

1. At player creation: Betty gets modules in JSON without inventory rows (behavior a)
2. When Hera was purchased (presumed earlier, active ship was Betty): `purchase_ship()` copied Betty's modules to Hera (behavior b) WITHOUT clearing Betty's modules
3. When Terran was purchased (step 5, active ship was Hera): `purchase_ship()` copied Hera's modules to Terran WITHOUT clearing Hera's modules
4. Result: `E2 Exoclad` and `Telta Quickscan` each appear in all 3 ships' `.modules` JSON columns

---

### (e) Buying a ship results in non-empty loadout (copied from active ship)

**Root cause**: `shop_service.py:purchase_ship()` lines 323–363 — the loadout copy block runs **unconditionally** whenever `old_player_ship` is set (i.e., whenever the player has an active ship).

This is probably intentional as a "loadout carry-over" feature, but it is BROKEN because:
1. The old ship's loadout is never cleared (items duplicated across ships)
2. No inventory rows are consumed (items were phantom to begin with, so nothing is deducted)

After step 5 (`/buy` Terran Battlecruiser), the Terran immediately shows the same weapons and modules as Hera. The Discord cog's `/ship` embed displays the Terran as ACTIVE immediately after purchase (confirmed by observation step 6 in the action sequence) — this is because `purchase_ship()` also sets the new ship as active (lines 385–386).

---

### (f) Differing weapon-slot counts not reconciled when switching active

**Root cause**: `player_ship_repository.py:set_active_ship()` (lines 128–166) only flips the `is_active` flag.

```python
# line 146–150: bulk deactivate
await db.execute(update(PlayerShip).where(...).values(is_active=False)...)

# line 154: single activate
ship.is_active = True

await db.commit()
```

No loadout inspection. No slot limit check. No overflow-to-cargo logic.

The router (`ships.py:set_active_ship`, lines 229–264) also calls `player_repo.update_active_ship()` to update `players.active_ship_id`, but neither operation touches the JSON loadout columns.

Betty has `max_primaries=1` (from the `ship` static data table), while Hera and Terran have `max_primaries=2`. After `/setactive` to Betty, Betty retains its stored 1-weapon loadout — no notification is shown to the user, and no items are moved to cargo.

---

## 3. Atomicity findings

| Flow | Router transaction boundary | player_ships | player_inventories | Verdict |
|------|----------------------------|-------------|-------------------|---------|
| `equip_item` | `get_db_session()` only (no `db.begin()`) | `add_equipment` → **own commit** | `remove_item` → **own commit** | **VIOLATES ATOMICITY**: two separate commits; crash between them leaves item on ship AND in inventory |
| `unequip_item` | `get_db_session()` only (no `db.begin()`) | `remove_equipment` → **own commit** | `add_item` → **own commit** | **VIOLATES ATOMICITY**: crash between them removes from ship but never adds to inventory |
| `purchase_ship` | `db.begin()` explicit (shops.py:152) | new ship created + loadout set | overflow items added via `commit=False` | **ATOMIC** within transaction; semantic bug (old ship not cleared) is separate |
| `sell_item` | `db.begin()` explicit (shops.py:186) | N/A | remove_item + credits update | **ATOMIC** |
| `sell_ship` (clear_equipment=True) | `db.begin()` explicit (shops.py:217) | ship deleted | items added to inventory | **ATOMIC** |
| `set_active_ship` | `get_db_session()` only (no `db.begin()`) | is_active flip | no change | **ATOMIC** (only touches player_ships) |
| `_create_starter_loadout` | Same session as player creation | Betty created with loadout | only Micro Gun MK I added | **SEMANTIC BUG**: modules/weapons on ship have no inventory entries — not a transaction failure |
| `prestige_player` | Same session, one commit | ship loadouts PRESERVED | inventory CLEARED | **VIOLATES CONSISTENCY**: post-prestige equipped items have no inventory rows |
| `admin_player Reset Player` | `get_db_session()` only | not touched | not touched | **CONSISTENT** (doesn't touch either table) |
| `admin_give_ship` | `get_db_session()` only | new ship created, empty loadout | not touched | **CONSISTENT** (empty loadout) |
| `admin_remove_ship` | `get_db_session()` only | ship deleted | items added from ship JSON | **CONSISTENT** (items returned to inventory; if items were phantom, they are now real — potential exploit) |
| `transfer_ship` | `db.begin()` explicit (ships.py:570) | loadout cleared + ownership changed | items added from ship JSON | **ATOMIC** (same phantom-item risk as admin_remove_ship) |
| `bounty resolve` | service manages | N/A | N/A (credits/XP only) | **CONSISTENT** (doesn't touch ships/inventory) |
| `duel accept/resolve` | service manages | N/A | N/A (credits only) | **CONSISTENT** (doesn't touch ships/inventory) |

---

## 4. Source-of-truth analysis

The code implicitly expects:
> A game item is in EXACTLY ONE place: either in `player_inventories` (quantity ≥ 1) or in `player_ships.*` JSON (listed under exactly one ship).

**Empirically, this invariant is violated from game start.**

`_create_starter_loadout` puts modules and weapons directly into the ship JSON without creating inventory rows. This was likely a shortcut: "starter items are pre-equipped, not in cargo." The problem is the equip/unequip surface then assumes all equipped items DO have inventory rows (unequip adds an inventory row — correct; equip REMOVES an inventory row — but there was no row to remove for starter items, so starter items can never be unequipped via the standard path without `ValueError: Item not found in player inventory`).

The actual canonical source of truth at runtime is: **neither table alone is sufficient**. The code uses:
- `player_ships.*` JSON as the displayed loadout (what `/ship` and `/loadout` show)
- `player_inventories` rows as the owned-but-not-equipped items (what `/inventory` shows)

But there is no reconciliation mechanism to ensure the union of these is consistent.

---

## 5. Exploit surface

`admin_remove_ship` (admin.py:1068–1095) and `transfer_ship` (ships.py:607–633) both unconditionally add items from a ship's JSON loadout to inventory without verifying those items have a real game origin. If a ship has phantom items (e.g., from starter loadout or loadout duplication), calling either of these flows CREATES NEW REAL INVENTORY ROWS for those phantom items. This is an item-generation exploit available to any admin, and implicitly to any player who can get an admin to remove a ship.

Example: Betty has phantom `E2 Exoclad` and `Telta Quickscan`. Admin calls `/admin_remove_ship` on Betty → both modules are added to inventory with quantity=1 each. These can then be sold for credits. If this is repeated for each of the 3 ships in the observed DB state, the player could extract 3× quantity of each module.

---

## 6. False speculations in the original entry

The original defect entry (B.19) was submitted without any code read:

> *"The semantic intent of equip / buy-ship / setactive against this schema (no service or repository code read)"*

So the entry correctly acknowledged it was unverified. Nothing in the entry was stated as verified code-level root cause. This recon has now verified all root causes from HEAD.

---

## 7. Open questions (read-only investigation cannot resolve)

1. Was the loadout-copy-on-ship-purchase designed intentionally (carry-over feature)? If so, the missing "clear old ship loadout" is a regression. If not, the whole block is undesired behavior.
2. Are there any other places where items are placed into `player_ships.*` JSON without corresponding inventory entries? (Criminal ship loadouts in `bounty.criminal_ship` JSON are separate — they don't interact with `player_inventories`.)
3. Was there ever a `secondary_weapons` column on Betty's starter ship? `_create_starter_loadout` does not set it, and the DB shows `secondary_weapons = NULL` for Betty (id=1). The `add_equipment`/`remove_equipment` methods in `player_ship_repository` do not handle `secondary_weapons` (line 210: `raise ValueError(f"Invalid equipment type: {equipment_type}")`). This means secondary weapons can only be placed via `update_loadout` or direct field assignment. Status: **open question** (related to B.2).
4. Severity of the phantom-item exploit if a non-admin player triggers it via `/sell-ship clear_equipment=True` — unclear if that flow is accessible to players.

---

*Recon completed: 2026-04-28 by developer (read-only investigation)*
