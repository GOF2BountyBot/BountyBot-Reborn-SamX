# AGENTS.md - services

Business logic layer for bot-core. ~31 modules live here: service classes, the tick-combat engine and its support modules (`combat_resolver.py`, `combat_models.py`, `combat_balance.py`), the PvC-loot modules (`loot_service.py` + pure `loot_engine.py` + `cargo_utils.py`), pure-function/constants modules (`game_maths.py`, `game_constants.py`), and helpers (`_item_type_normalizer.py`, `_transaction_guards.py`, `exceptions.py`). (B.48: division_service.py was removed alongside the level/division progression system.)

---

## Duel Pending-Stakes Invariant (and auto-cancel contract)

A pending `DuelRequest` carries an implicit credit reservation for **both** the
challenger and the target equal to `stakes`. A player's **available balance** is:

```
available = player.credits - SUM(stakes WHERE status='pending' AND (challenger_id=P OR target_id=P))
```

Total exposure is counted **regardless of role** — a player who is challenger in
a 6k duel and target in another 6k duel has 12k total exposure.

### Validation

- **`DuelService.create_challenge`** — blocks creation when `challenger_available`
  or `target_available` is below the new stakes. Uses
  `duel_repo.get_total_pending_stakes_for_player(db, player_id)`.
- **`DuelService.accept_duel`** — re-validates under `FOR UPDATE` lock, excluding
  the current duel from the pending sum (it's being resolved, not additional
  exposure). Uses `exclude_duel_id=duel_id`.

### HARD RULE — Auto-cancel contract

Every credit-deduction site **MUST** call
`DuelService().cancel_underfunded_duels(db, player_id, commit=False)` after the
deduction and before the transaction commit.

**Current call sites:**

| Site | File | Notes |
|------|------|-------|
| `ShopService.purchase_item` | `shop_service.py` | after `player.credits -= total_cost` |
| `ShopService.purchase_ship` | `shop_service.py` | after `player_repo.update_credits` |
| `PlayerService.transfer_credits` | `player_service.py` | source side only; target is gaining |
| `PlayerService.update_player_credits` | `player_service.py` | decrease path only (`new_credits < old_credits`) |
| `PlayerService.demote_player` | `player_service.py` | only when `penalty > 0` |
| `PlayerService.prestige_player` | `player_service.py` | credits reset to 0 — all pending cancelled |
| `DuelService.accept_duel` | `duel_service.py` | loser side only, after credit transfer |

**New credit-deduction code paths MUST add the same call.** The deferred import
pattern `from services.duel_service import DuelService` avoids circular imports.

---

## Loadout & Inventory system — CANONICAL REFERENCE

> ### ⚠️ BE CAREFUL — most-broken subsystem in the codebase
>
> Loadout ↔ inventory ↔ equip/unequip/sell/buy/transfer is the **single most
> fragile, most-frequently-regressed area** of bot-core. Phantom items, dropped
> copies, double-counts, and silent cross-ship duplication have all shipped here
> before (bug classes B.19, B.34, B.41, B.94/B.95). **Read this entire section
> before touching any code path that mutates `player_ships.{weapons,modules,
> turrets,secondary_weapons}` or `player_inventories`.** Preserve the invariants
> below exactly, route every cross-table mutation through the
> `LoadoutConsistencyService` choke-point, and test the invariants exhaustively
> (per-flow, plus a "total owned is conserved" property test). When in doubt,
> escalate to the architect rather than improvising a new write path.

### Data model

Two tables back the system. **Static** catalog data (the item's stats, tech
level, slot it occupies) lives in the `item` STI hierarchy and the `ship` table;
**dynamic** ownership lives in the two tables below.

| Table | Columns of interest | Role |
|-------|---------------------|------|
| `player_ships` | `weapons`, `secondary_weapons`, `turrets`, `modules` (all `JSON` list-of-name columns); `ship_name`, `player_id`, `is_active`, `nickname` | **Equipped pool.** Each string entry in a slot list is one equipped copy. Slot caps come from the matching static `ship` row (`max_primaries` → `weapons`, `max_secondaries` → `secondary_weapons`, `max_turrets` → `turrets`, `max_modules` → `modules`). |
| `player_inventories` | `player_id`, `item_type` (concrete: `ship` / `primary_weapon` / `secondary_weapon` / `turret_weapon` / `module`), `item_name`, `quantity` | **Cargo pool.** One row per `(player_id, item_type, item_name)`; `quantity` is the loose (un-equipped) copy count. Model: `persist/models/player_inventory.py`. |

The relevant maps live in `equipment_service.py` (imported by
`loadout_consistency_service.py`): `_SLOT_MAP` maps each `equipment_type` to its
**static ship slot-cap field** (`weapons → max_primaries`,
`secondary_weapons → max_secondaries`, `turrets → max_turrets`,
`modules → max_modules`), and `_INVENTORY_TYPE_MAP` maps each `equipment_type` to
its **concrete inventory `item_type`** (`weapons → primary_weapon`,
`secondary_weapons → secondary_weapon`, `turrets → turret_weapon`,
`modules → module`). Note the deliberate naming skew: the JSON slot for primaries
is `weapons`, its cap field is `max_primaries`, and its inventory `item_type` is
`primary_weapon`. `VALID_EQUIPMENT_TYPES` = `{weapons, secondary_weapons,
modules, turrets}`.

> **DB-level uniqueness since CI-18 (2026-06-05).** `player_inventories` now has
> `UniqueConstraint("player_id", "item_type", "item_name",
> name="uq_player_inventories_player_item")` — declared in
> `persist/models/player_inventory.py` and created by migration
> `0015_ci18_player_inventory_unique.py` (which first merges any pre-existing
> duplicate rows into the lowest-id row). The "exactly one row per item"
> property is additionally upheld at the app layer by
> `InventoryRepository.add_item`, which calls `get_player_item` and increments
> an existing row instead of inserting a second. New write paths must go
> through `add_item` — a direct INSERT of a duplicate now raises an
> IntegrityError instead of silently corrupting counts.

### `secondary_ammo` sidecar — CI-16 (BUILT, 2026-06-03)

Secondary weapons are now consumable (ammo-limited). The chosen storage model is
a **JSON sidecar column** on `player_ships.secondary_ammo: dict[str, int]`.

**Conservation model (CI-16 canonical):**
```
owned(S) = cargo.quantity(S) + Σ_ships secondary_ammo[S]
```
The `secondary_weapons` slot-list entry is **pure slot occupancy — NOT a counted
copy** (deliberate divergence from primaries/turrets/modules where each slot
entry = 1 copy). `secondary_ammo[name]` = remaining rounds for that equipped
type. Key invariants:

- **Equip (new type):** whole cargo stack → `secondary_ammo[name]`; one slot
  entry appended; cargo quantity drops to 0.
- **Equip (already equipped, top-up):** whole cargo stack → `secondary_ammo[name]`
  increment; NO new slot entry; cargo quantity drops to 0.
- **Unequip:** whole remaining `secondary_ammo[name]` → cargo; slot entry removed;
  `secondary_ammo` key deleted.
- **Ship transfer (R1):** fitting secondaries move `secondary_ammo[S]` src→dst;
  overflow secondaries return `secondary_ammo[S]` rounds to cargo.
- **Evacuate (R2):** each secondary returns `secondary_ammo[S]` rounds to cargo.
- **Shop buy (equipped):** rounds added to `secondary_ammo[name]`; no cargo add.
- **Shop buy (not equipped):** rounds added to cargo (normal path).
- **Post-fight:** resolver decrements `secondary_ammo[S]` per fire trigger;
  write-back in `_consume_secondary_ammo`; auto-unequip when rounds reach 0.
- **`ammo=None`:** infinite (back-compat for criminal/legacy paths — no write-back).

SQLAlchemy JSON: **MUST reassign the whole dict**, never mutate in place, or writes
are silently lost. Migration: `0013_secondary_ammo.py` (`down_revision="0012"`).

### Two Separate Pools

The system uses **two completely separate pools** to track owned items. There is NO overlap between them.

| Pool | Table / Column | Meaning |
|------|---------------|---------|
| **Cargo** | `player_inventories.quantity` | Number of **unequipped** copies the player has in cargo |
| **Equipped** | `player_ships.weapons` / `modules` / `turrets` / `secondary_weapons` | JSON name arrays — each entry is one equipped copy |

**`player_inventories.quantity` is CARGO-ONLY — it does NOT include equipped copies.**

Equipped items live solely in the ship loadout JSON. They do NOT appear in `player_inventories`.

### Total Ownership Invariant

```
Total owned (item X, player P) = P.inventory.quantity(X) + sum(count of X across all of P's ships)
```

Example: player owns 3× Ridil Blaster
- `player_inventories`: `quantity=1` (1 in cargo)
- Ship A `weapons`: `["Ridil Blaster", "Ridil Blaster"]` (2 equipped)
- Total: 1 + 2 = 3 ✓

### Equip / Unequip Transaction

| Operation | `player_inventories.quantity` | `player_ships.{slot}` |
|-----------|------------------------------|-----------------------|
| **Equip** | Decreases by 1 (cargo copy consumed) | Item name appended |
| **Unequip** | Increases by 1 (cargo copy returned) | Item name removed |
| **Swap** | No net change | Item replaced in slot |

Both operations use `commit=False` — the calling router wraps with `db.begin()` for atomicity.

### B.41 Guard — Correct and Necessary

The guard in `LoadoutConsistencyService.equip_one()` (look for "No unequipped copies remain"):

```python
if inv_item.quantity <= 0:
    raise ValueError("No unequipped copies remain")
```

This prevents equipping when there are no cargo copies left to consume, which would create phantom items.

**Why `quantity <= 0` and NOT `already_equipped >= quantity`:**
`player_inventories.quantity` is **cargo-only**. With 1 equipped + 1 in cargo:
- `quantity = 1` (one cargo copy available to consume — equip should PASS)
- The old condition `already_equipped(1) >= quantity(1)` = True → incorrectly blocked it
- The correct condition `quantity <= 0` = False → correctly allows it

**The guard only runs when a slot is free** — it is skipped when slots are full (the swap path). For swaps: the cog unequips first (returning the copy to cargo, incrementing quantity), then equips (decrementing quantity). After the unequip, `quantity` increases so the guard passes on the subsequent equip call.

**Do not remove or weaken this guard.** It is the primary defence against inventory corruption.

### The INVARIANTS — hard rules, do not violate

These are the load-bearing rules. A change that breaks any of them is a bug,
regardless of whether tests are green.

1. **Pool separation.** Equipped copies live **only** in `player_ships` slot
   lists; loose copies live **only** in `player_inventories`. A given physical
   copy is in exactly one pool — never both, never neither.
2. **Conservation.** `Total owned(item, player) = cargo quantity + Σ equipped
   count across all of that player's ships`. Every mutation must preserve this:
   equip = −1 cargo / +1 slot; unequip = +1 cargo / −1 slot; swap = net zero;
   sell/transfer = −1 cargo only; ship purchase/sale moves items between slots
   and cargo without minting or dropping. Never double-count; never drop a copy.
3. **No materialisation from nothing (I2).** Every slot entry must trace back to
   an inventory decrement. Do not append a name to a slot list without a paired
   cargo decrement (the choke-point does both, atomically).
4. **No cross-ship duplication (I1).** The same physical copy must not appear in
   two ships' slot lists. `repair_player` and the `evacuate_*` anti-dup guard
   exist to clean up legacy violations — do not reintroduce the bug class.
5. **Slot caps + unique-equip (I4).** A ship's slot list length must never
   exceed its static cap (`max_primaries` / `max_secondaries` / `max_turrets` /
   `max_modules`). `MODULE_EQUIP_LIMITS` additionally caps how many of a given
   module *class* may be equipped at once. **These caps apply uniformly to
   players AND to auto-generated criminal/NPC loadouts** — `bounty_service`
   generates criminal gear by looping `range(ship.max_primaries)` /
   `range(ship.max_modules)` against the same static `ship` row, so a criminal
   never exceeds the caps a player would face on the same hull.
6. **`/unequip` before `/sell` (and before `/transfer`).** Sell and transfer
   operate on **cargo only**. See the dedicated subsection below for exactly how
   this is enforced — it is structural, not a guard you can grep for.
7. **Surface gating.** `GameConstants.CURRENTLY_ENABLED_TYPES` is the single
   lever that exposes a concrete item type to playable surfaces.
   `secondary_weapon` is **already enabled** (added prior to CI-16); `equip_one`
   and the type normalizer will correctly handle secondary weapons. Honour this
   gate in any new flow.

### Unequip-before-sell — how it is actually enforced

There is **no explicit "is this item equipped?" check** in the sell or transfer
paths, and you should not add one expecting it to be the enforcement point.
Enforcement is **structural**, falling out of invariant #1:

- `ShopService.sell_item` (`shop_service.py`, ≈line 409) resolves the item from
  `player_inventories` and calls `inventory_repo.remove_item`, which raises
  `ValueError("Insufficient item quantity ...")` when cargo `quantity` is too
  low. Equipped copies are **not in `player_inventories` at all**, so they are
  simply unreachable by sell — a fully-equipped, zero-cargo item cannot be sold
  until `/unequip` returns a copy to cargo. The `/sell` router
  (`api/routers/shops.py`) wraps the call in `db.begin()`.
- `InventoryService.transfer_item_between_players` (`inventory_service.py`) has
  the same property: it removes from the source player's **cargo** and adds to
  the target's cargo. Equipped gear is unreachable. ⚠️ Note transfer does a
  remove+add pair across two players **outside** the `LoadoutConsistencyService`
  choke-point — this is currently safe **only because both legs are cargo-only**
  (no slot mutation, so I1/I2 cannot be violated). If transfer is ever extended
  to move equipped gear directly, it **must** route through the choke-point.

If you ever see a sell/transfer path that reads or mutates `player_ships` slot
lists, that is a bug — flag it.

### JSON-column reassignment gotcha — MUST reassign, never mutate in place

`player_ships.{weapons,secondary_weapons,turrets,modules}` are SQLAlchemy `JSON`
columns. SQLAlchemy's default change tracking does **not** detect in-place
mutation of a `JSON`/`list` value — `ship.weapons.append(name)` will **silently
fail to persist**. Every write must **reassign the whole list**:

```python
# CORRECT — reassign a new list (change is tracked, persists on flush)
ship.weapons = list(current) + [item_name]

# WRONG — in-place mutation; change tracker never fires; the write is lost
ship.weapons.append(item_name)
```

The existing code already does this correctly: `PlayerShipRepository.update_loadout`
assigns `ship.weapons = loadout["weapons"]`; `add_equipment` / `remove_equipment`
build a fresh `list(...)` then call `update_loadout`; and
`LoadoutConsistencyService._set_slot` assigns `ship.weapons = list(items)`.
**Preserve this pattern in any new slot-mutating code.** (Alternatively
`flag_modified(ship, "weapons")` after an in-place mutation, but the codebase
convention is reassignment — match it.)

### Autocomplete Filter (discord-gateway `inventoryCog.equip_autocomplete`)

The cog filters the equip dropdown to `qty > already_equipped_on_active_ship`. It only counts the **active ship**, not all ships. This is intentional — typical UX is equipping on the active ship. In the rare case a player tries to equip on an inactive ship when all cargo copies are already equipped on other ships, the B.41 guard will catch it server-side.

---

## Criminal-Only Module Dedup Invariant (A.48 fix, 2026-04-27)

`LoadoutResponseService.build_bounty_loadout()` runs the criminal modules through
`_apply_criminal_module_dedup()` before returning. This collapses runs of identical
`CabinModule` or `CompressorModule` entries into a single `Name xN` representative.
**Only those two subtypes are deduped**; all other module types (Shield, Armour,
GammaShield, weapons, turrets) render individually as before.

**HARD INVARIANT — DO NOT VIOLATE**: `build_player_loadout()` MUST NEVER call the
dedup helper. Player loadouts are always shown verbatim. The dedup is purely a
presentation transform for criminals (the underlying `bounty.criminal_ship` JSON
is unchanged; combat resolution uses the raw, non-deduped loadout).

The dedup keeps both `/criminal-loadout` AND bounty-spawn announcements visually
clean, since both render through the same `LoadoutResponse` path. This was the
architectural unification that fixed A.48 (Discord HTTP 400 code 50035 from a
9× Rhoda Blackhole CompressorModule loadout exceeding the 1024-char field cap).

A second-line continuation-field split lives in the gateway's
`cogs/_shared/loadout_embed.build_loadout_embed`, providing a regression-proof
safety net for any future edge case the dedup doesn't cover.

---

## Criminal loadout-generation algorithm — CANONICAL REFERENCE (Threads 1/3/4/6, 2026-06-18)

This is the authoritative description of how `BountyService.generate_loadout`
builds a **criminal/NPC** loadout. Player loadouts are untouched by all of this.
Implemented in `bounty_service.py` (`_select_primaries` ~964, `_select_modules`
~1019, helpers `nearest_tl_pick` ~275 / `tl_band_pick` ~298, EMP guard
`_is_primarily_emp` ~248). Every numeric is a **tunable knob**: a
`GameConstants` default + a per-guild `GuildConfig` override resolved via
`resolve_constant(cfg, "<key>", default)`. No `import_data/` (game-data) edits
anywhere. **The DLC attribute on any item is informational only — never used as
an eligibility/gating filter.**

`item_tl = max(1, tech_level - 1)` is the "target TL" all nearest-TL / TL-band
picks aim at.

### Shared nearest-TL rule (`nearest_tl_pick`)
Pick the variant minimising `|variant.TL − item_tl|`. **Tie-break is
division-aware:** `gold`/`platinum` prefer the **higher** TL, `bronze`/`silver`
the **lower**. When the chosen TL has more than one variant, pick **uniformly at
random** among them. (A single-variant module like Emergency System resolves to
its sole entry regardless of criminal TL — this is why no separate "rarity
bypass" exists.)

### Thread 1 — cluster missiles are a HEAVY shop secondary
`"cluster-missile"` is in `GameConstants.SHOP_HEAVY_SECONDARY_SUBTYPES`
(`{"nuke", "shock-blast", "cluster-missile"}`), so its shop quantity scaler is
the heavy 5× (not the standard 10×) — 10–20 rounds/refresh, matching nukes. This
is a *supply-side* change in `shop_service.py`, not a loadout-generation change,
but it lives in the same balance pass.

### Thread 3 — PRIMARY long-range floor + ±1 TL-band pick (`_select_primaries`)
Replaces the old hard-pinned exact-`weapon_tl` random pick. Per ship:
- Classify any primary: **LONG iff `range_m > LONG_RANGE_THRESHOLD_M` (2600)**, else SHORT.
- **Floor:** `min_long = ceil(CRIMINAL_LONG_RANGE_PCT × max_primaries)` (pct default **0.50**, global). Those slots are forced LONG; each *remaining* slot independently rolls LONG at the same pct → total long ∈ `[min_long, max_primaries]` (floor guaranteed, RNG may exceed). `ceil` means 1-slot hulls are always long.
- **Per-slot pick is CATEGORY-FIRST:** the long/short gate picks the bucket, then `tl_band_pick` chooses the TL from the window `{item_tl−1, item_tl, item_tl+1}` weighted by `PRIMARY_TL_BAND_WEIGHTS` (`{center: 70, minus1: 20, plus1: 10}`), then a random weapon of that category at that TL.
- **Missing-band redistribution:** an out-of-bounds or empty *side* band donates its weight to the **other side** band ("push to other side"). An empty *center* band splits its weight **evenly** to both neighbours (e.g. target=TL4 LONG → TL3 55% / TL5 45%, the only interior center-empty case in the catalog).

### Thread 4 — MODULE selection by priority walk (`_select_modules`)
Walk a fixed priority order, filling slots until `len(equipped) == ship.max_modules`, then **STOP** (no displacement; a full hull short-circuits the rest of the walk, so later/gated categories are simply never reached). A failed Gate-1 roll leaves the slot for the next category. The order and gate kinds are module-level constants in `bounty_service.py`:

| # | Category (`Item.type`) | Gate | Knob |
|---|---|---|---|
| 1 | `ScannerModule` | guaranteed | — |
| 2 | `ArmourModule` | guaranteed | — (removes legacy TL>1 gate) |
| 3 | `ShieldModule` | guaranteed | — (removes legacy TL>3 gate) |
| 4 | `CloakModule` | two-gate | `criminal_cloak_chance_by_division` (B0/S25/G66/P100) |
| 5 | `BoosterModule` | two-gate | `criminal_booster_chance_by_division` (B50/S100/G100/P100) |
| 6 | `EmergencySystemModule` | two-gate | `criminal_emergency_chance_by_division` (B0/S25/G50/P100) |
| 7 | `RepairBotModule` | guaranteed | — |
| 8 | `PrimaryWeaponModModule` | two-gate | `criminal_weaponmod_chance_by_division` (B0/S25/G50/P100) |
| 9 | `ThrusterModule` | guaranteed | — |

- **Guaranteed:** always `nearest_tl_pick` into the slot if one is free.
- **Two-gate:** Gate-1 = `randint(1,100) <= resolve(<knob>[division])` (per-division equip %); on pass, Gate-2 = `nearest_tl_pick`.
- **Filler tail** (only if slots remain): **Filler-A** (`GammaShield, SpectralFilter, RepairBeam, Signature, MiningDrill, TractorBeam` — each at most once, drawn random-without-replacement) then **Filler-B** (`Compressor, Cabin` — drawn random-with-replacement, repeats to fill). Variant pick within a filler type uses the same nearest-TL rule.
- **Never equipped** (`_NEVER_EQUIP_TYPES`, import-time-asserted disjoint from every equippable list): `TransfusionBeam`, `ShieldInjector`, `TimeExtender` (misleading no-ops), `JumpDrive` (banned).

The 9 priority categories are exactly the module types wired into the combat engine; the filler types are combat-inert (loaded for fidelity, no effect).

### Thread 6 — exclude primarily-EMP criminal weapons (`_is_primarily_emp`)
A criminal primary OR secondary is dropped from the candidate pool **before**
selection when `emp_damage > real_damage`. `real_damage` is `damage_per_shot`
for primaries / the `damage` column for secondaries; the engine applies **0 HP**
from `emp_damage` (EMP is a deferred phase-2+ feature, see `COMBAT_SPEC_LOCKED.md`
§4), so a pure-EMP weapon would do no real damage and hand the player a free win.
Gated by the per-guild toggle **`criminal_exclude_emp_weapons`** (default
**ON** = `GameConstants.CRIMINAL_EXCLUDE_EMP_WEAPONS = True`). This is a
behavioral toggle, not a numeric knob, but follows the same `resolve_constant`
pattern; it **auto-disables cleanly** once real EMP mechanics ship. Filters
weapons like the Luna/Sol/Dia EMP primaries and Mamba/Neétha EMP secondaries;
KEEPS real-damage hybrids (e.g. Dephase EMP, where 120 real > 100 emp).

> **Stale-constant note:** `GameConstants.CRIMINAL_EQUIP_DAMAGELESS_WEAPON_CHANCE`
> (=20) is **dead** — defined + env-tracked but referenced by no selection path.
> Superseded by Thread-6's deterministic exclusion. Flagged for future cleanup
> (`OPEN_ITEMS.md`).

---

## Item-Type Vocabulary & Normalizer Contract (A.36 fix, 2026-04-22)

**Storage invariant**: `player_inventories.item_type` and `guild_shops.item_type` always store
**concrete types** only: `ship`, `primary_weapon`, `secondary_weapon`, `turret_weapon`, `module`,
`commodity`. Generic aliases (`weapon`, `turret`) are NEVER persisted. **`commodity` is a
first-class concrete type** (PvC loot, T1) — it is valid in `player_inventories` but NEVER in
`guild_shops`: commodities are pure cargo, never shop-stocked (see "Commodity is a first-class
type" below).

**Normalizer module**: `_item_type_normalizer.py` provides:
```python
expand_item_type_to_concrete(item_type, *, context: Literal["catalog", "playable"]) -> tuple[str, ...]
```
- `context="catalog"` — returns all concrete types including `secondary_weapon` (for browsing)
- `context="playable"` — returns only `GameConstants.CURRENTLY_ENABLED_TYPES` (surface-gated)
- Raises `InvalidItemTypeError` (subclass of `ValueError`) for unknown types or disabled concrete types

**Surface gating**: `GameConstants.CURRENTLY_ENABLED_TYPES` is the SINGLE lever controlling
secondary-weapon exposure. To enable secondary weapons: add `"secondary_weapon"` to this frozenset.

**Write-site rule**: all calls to `inventory_repo.add_item()` MUST use concrete types.
Use `equipment_service.item_discriminator_to_concrete_type(item.type)` to resolve from STI discriminator.

#### Commodity is a first-class type (PvC loot, T1)

`commodity` is the **6th concrete inventory type**. It is a member of all three economy
frozensets in `game_constants.py` — `CATALOG_ITEM_TYPES`, `PLAYABLE_ITEM_TYPES`,
`CURRENTLY_ENABLED_TYPES` — and is **concrete** (no `GENERIC_TO_CONCRETE_EXPANSION` alias entry).
Existence validation scans `CommodityRepository` (`inventory_service._validate_item_exists`);
pricing reads the base `Item.value` column (`shop_service._get_item_base_price` + its
`_price_cache`). `TransferItemRequest.item_type` (`inventory_schema.py`) includes `"commodity"`
so `/give` of a commodity does not 422.

> **DO NOT add `commodity` to `_CONCRETE_TO_CONFIG_KEY`** (`shop_service.py`). That separate map —
> NOT the three frozensets — is what gates shop stocking / purchasability. Commodities are pure
> cargo: **never buyable, never written to a `GuildShop`.** Selling a commodity is a face-value
> **sink** (`sell_item` commodity branch: destroy + credit, `Item.value × qty ×
> LOOT_COMMODITY_SELL_FRACTION`), never a shop restock. Commodities are never equipped, so the
> equip/unequip/swap/ammo machinery never applies to them.

### /sell — Server-side type and tier resolution (A.42/A.42b/A.42c, 2026-04-22)

`ShopService.sell_item(db, player_id, item_name, quantity)` no longer accepts `item_type` or `target_tier` parameters:

- **A.42b**: `item_type` is resolved internally by calling `inventory_repo.get_player_items_by_name(db, player_id, item_name)`. The concrete type comes from the player's inventory row — no generic alias is ever passed to a write path.
- **A.42c**: `target_tier` always equals `player.tier` (read from the fetched player record). Items always land in the player's current tier shop, consistent with `/buy` tier-gating.
- **Cross-type collision guard**: if `get_player_items_by_name` returns rows with two different `item_type` values for the same item_name, `InvalidItemTypeError` is raised with a helpful message. This is impossible with the current catalog (146 items, all distinct names) but guarded defensively.

`SellRequest` schema (`shops_schema.py`) now has only `player_id`, `item_name`, and `quantity` fields. Extra fields (e.g. stale client sending `item_type` or `target_tier`) are silently ignored by Pydantic default behavior (no `extra='forbid'`).

`InventoryRepository.get_player_items_by_name(db, player_id, item_name)` — new method added to support this flow. Returns all inventory rows for a player matching the given item_name (regardless of concrete type).

---

## Locally-Captured-Value Pattern for Repo Update Helpers

When updating an entity via a repo helper (`update_credits`, `update_xp`,
`update_quantity`, etc.), use the **locally-captured-value pattern**:

1. Compute the new value into a local variable BEFORE the update call.
2. Pass that local variable to the repo helper.
3. Use the local variable (NOT a re-read of `entity.col`) when assembling the
   response dict.

```python
# CORRECT — locally-captured-value pattern
new_credits = player.credits + total_sell_value
await self.player_repo.update_credits(db, player_id, new_credits, commit=False)
return {"new_credits": new_credits, ...}
```

```python
# WRONG — reads entity.col after the update; produced doubled-credit bug pre-2026-04-27
await self.player_repo.update_credits(db, player_id, player.credits + total_sell_value, commit=False)
return {"new_credits": player.credits + total_sell_value, ...}  # BUG: player.credits is now post-update
```

Why: even after the Option B refactor (ORM `setattr` instead of Core UPDATE),
the in-place-mutated instance reflects the NEW value immediately. Reading
`entity.col` after the update gives the post-update value, so any
`entity.col + delta` re-computation will double-apply the delta.

The locally-captured-value pattern is also used by `shop_service.buy_ship`,
`player_service.transfer_credits`, and `inventory_service.consolidate_inventory`.

---

## Service Layer Purpose

Services contain **all business logic**. Routers handle HTTP concerns only (parsing requests, returning responses). Repositories handle data access only (SQL queries). Logic that coordinates multiple repositories or enforces game rules lives in services.

```
Router  →  Service  →  Repository  →  Database
          (business   (SQL queries)
           logic)
```

---

## Constructor Injection Pattern

Services instantiate their own repositories in `__init__()`. No arguments are required to construct a service:

```python
class PlayerService:
    def __init__(self):
        self.player_repo = PlayerRepository()
        self.user_repo = UserRepository()
        self.config_repo = ConfigRepository()
```

Services do **not** store `AsyncSession` objects — sessions are passed per-call:

```python
async def get_or_create_player(self, db: AsyncSession, discord_id: int, ...) -> Player:
    user = await self.user_repo.get_or_create_user(db, discord_id, ...)
    ...
```

---

## Module Reference (all 27 modules)

### audit_service.py — `AuditService`

- All methods are **static** (no instance state)
- Records admin mutations to `AdminAuditLog` table
- **Failures are swallowed** — audit logging never blocks the primary operation
- Called by admin router endpoints and anywhere an admin mutation occurs

```python
await AuditService.log_action(
    db,
    user_id=123,
    action="guild_reset",
    guild_id=456,
    resource_type="guild",
    resource_id="456",
    details={"reason": "test"},
)
```

---

### bounty_service.py — `BountyService`

Core bounty system business logic:
- `spawn_bounty(db, guild_id, division, tech_level=None, expiry_minutes=None)` — full spawn orchestration: select criminal (excluding active ones), determine tech level, generate the A* route via `_generate_route` (≥ `min_route_systems`, best-effort — see below), pick the answer system, roll the per-bounty `spotted_window`, generate criminal loadout, calculate reward
- `_generate_route(jump_gate_systems, min_systems, attempts=8)` — picks random distinct jump-gate endpoints and runs A* shortest-path, retrying until the route has **≥ `min_systems`** systems (default `GameConstants.MIN_ROUTE_SYSTEMS`=3; per-guild `min_route_systems`). No adjacent-gate 2-system hunts. If no attempt reaches the minimum (tiny/sparse map) it returns the **longest** route found (logged) so a spawn never fails purely on a too-short route; returns `None` only if every attempt errored. Shared by `spawn_bounty` and `respawn_bounty`.
- `_roll_spotted_window(cfg)` — rolls the per-bounty "recently spotted" look-ahead width **B** from `[0, recently_spotted_max_window]` (default max `GameConstants.RECENTLY_SPOTTED_MAX_WINDOW`=3) and persists it as `Bounty.spotted_window`. **B=0 → that bounty shows no "recently spotted" hint at all.** See the canonical "Recently-spotted window" section below.
- `select_criminal` / `find_item_tl` / `generate_loadout(db, tech_level, division="bronze", cfg=None)` — spawn building blocks. `generate_loadout` takes a **`division`** arg (threaded in from `spawn_bounty` and `combat_preflight_service._synthesize_criminals`) because criminal primary + module selection is division-aware (per-division equip odds, nearest-TL tie-breaks). Internals: primaries via `_select_primaries` (long-range floor + ±1 TL-band pick), modules via `_select_modules` (fixed priority walk + two-gate per-division %), both EMP-filtered. See the **"Criminal loadout-generation algorithm"** canonical section below for the full rules. Turrets still loop `range(ship.max_turrets)`; everything is capped against the same static `ship` row players use.
- `check_bounty(...)` — records a system check; returns proximity hints via `CheckResult` / `CheckResponse` / `MultiCheckResponse`. The `recently_spotted` flag fires when the checked system is `1..B` stops before the answer, where **B = the per-bounty `spotted_window`** (resolved via `resolve_spotted_window`; legacy NULL → fixed 2). The separate `proximity_hint` flag (`0 < distance < close_bounty_threshold`) is unchanged. **T7 over-cap lockout** is the FIRST eval (plain read of `sum(player_inventories.quantity)` vs effective cap via `cargo_utils.is_over_cap`); an over-cap player is rejected before resolution with the `OVER_CAP` outcome (gateway renders `"Cargo Overloaded — NN/XX. Unable to leave station."`).
- **PvC loot (T4/T5).** `spawn_bounty` rolls the criminal's single loot item via `LootService.roll_loot` (anchored on `Bounty.tech_level`) and persists it in `Bounty.criminal_ship["cargo"]` = `{item_type, item_name, quantity}` — no migration. `_apply_loot_on_win` writes loot **only on a player combat WIN** (`fight_results.winner_side == 1`; Bronze hooks the `combat_player_won` bonus-fight branch, Silver+ gates on `fight_results is not None and winner_side == 1`) — never on capture/loss/stalemate/no-ship/PvP. It reads the persisted cargo (no re-roll), resolves the equipped tractor → chance map, gates on `free_cargo >= 1`, rolls, clamps to free space (§5.4), and writes via its **own player-locked transaction** (`add_item_to_inventory(commit=False)` + commit) — NOT composed into `distribute_rewards` and NOT atomic with rewards. Logs via `bblogger`; **no `audit_service` call** (player action). Returns a `LootOutcome` (outcome ∈ `looted/partial/failed/cargo_full/none`) surfaced on the check response (`loot` payload).
- `calc_rewards` / `distribute_rewards` — winner + consolation payouts (`RewardInfo`)
- `expire_bounty(db, bounty_id)` — marks bounty as expired
- `escape_bounty` / `respawn_bounty(db, bounty_id)` — escape computes `respawn_time`; respawn regenerates route/answer for the same criminal (via `_generate_route`, ≥ `min_route_systems`), **re-rolls `spotted_window`**, and resets status to `active`
- `_edit_bounty_announcement(db, bounty, captured=False)` — edits the Discord announcement via gateway PUT (A.48)
- `clear_bounties(db, guild_id, tier=None)` — admin soft-clear; also cleans up Discord announcements AND any orphaned `bounty_expire` / `bounty_respawn` scheduler jobs linked to the cleared bounty IDs (A.11). Scheduler cleanup runs via HTTP to the scheduler API **after** the DB commit, mirroring the announcement-cleanup pattern: scheduler-side failures are non-fatal and logged as warnings. Return dict includes `scheduler_jobs_deleted` for observability.

**Scheduler-cleanup pattern** (A.11): orphaned jobs are located by payload content (`args[1]["job_type"]` ∈ {`bounty_expire`, `bounty_respawn`} AND `args[1]["bounty_id"]` in cleared set) rather than by deterministic job IDs, because bounty job IDs are random UUIDs. 404 responses on DELETE are treated as already-fired and NOT logged.

**Route length + "recently-spotted" window (canonical).** Two coupled anti-triangulation rules on bounty discovery:

- **Minimum route length.** Every route has **≥ `min_route_systems`** systems (default `GameConstants.MIN_ROUTE_SYSTEMS`=3; per-guild `min_route_systems`, schema `ge=2`). Routes are A* shortest-paths between two random jump-gate systems, so the practical max is the galaxy graph's diameter; the floor just rejects adjacent-gate 2-system hunts. Enforced in `_generate_route` (retry-to-min, longest-found fallback).
- **Per-bounty spotted window B.** Rolled once at spawn/respawn from `[0, recently_spotted_max_window]` (default max `GameConstants.RECENTLY_SPOTTED_MAX_WINDOW`=3; per-guild `recently_spotted_max_window`, schema `ge=0`) and persisted on `Bounty.spotted_window`. A checked system shows **"recently spotted"** iff it is `1..B` stops before the answer. **B=0 ⇒ no hint at all for that bounty** (and a guild-wide `recently_spotted_max_window=0` disables the hint entirely). Replaces the old fixed `1..2` window, which let a player triangulate the exact answer from two adjacent checks. Legacy bounties (`spotted_window` NULL) fall back to the historical `2`.
- **Single source of truth.** `resolve_spotted_window(bounty)` + `is_recently_spotted(distance, window)` live in `utils/bounty_announcement_payload.py` and are shared by the check path (`bounty_service.check_bounty`), the live route embed (`_project_checked`), and the `GET /bounties/{id}/route` endpoint — so the three sites can never drift. Persistence is **migration `0024`** (`bounty.spotted_window` + the two `guild_configs` override columns).

Uses: `CriminalRepository`, `BountyRepository`, `ConfigRepository`, `ItemRepository`, `PlayerRepository`, `SecondaryWeaponRepository`, `PathfindingService`, `SystemGraphService`, `CombatService`, `LootService`, `game_maths`, `cargo_utils`, `loot_engine`

---

### combat_models.py — Dataclasses (NOT a service)

**This file contains only dataclasses and protocols — no service class.** Do not confuse with `combat_service.py`.

Key types:
- `WeaponStats` — frozen dataclass: `name`, `dps`, plus tick-resolver fields (`fire_rate`, `damage_per_shot`, `loading_speed_ms`, `range_m`), T6/T7 discriminator fields (`subtype`, `burst_count`, `emp_damage`, `magnitude_m`, `steerable`, `automatic`), and CI-16 `ammo` (`None` = infinite). There is NO `manual_turret_mode` anywhere — turret/primary switching is range-driven (see `combat_resolver.py` below)
- `ModuleStats` / `UpgradeStats` — frozen dataclasses: name + effect fields
- `ShipLoadout` — frozen dataclass: ship base stats + `weapons` / `turrets` / `secondary_weapons` / `modules` / `upgrades` / `builtin_modules`
- `CombatStats` — computed stats from a loadout
- `FightStats` / `FightResults` / `CombatMeta` / `CombatEvent` / `CombatEventType` — output of a combat resolution
- `CombatResolver` — `Protocol` for resolver strategies (implemented by `TickResolver`)

---

### combat_service.py — `CombatService`

Tick-based combat resolution (T3–T10):
- `fight_ships(loadout1, loadout2, *, context, log_result, pvc_damage_reduction, session, guild_id, ...)` — async;
  routes through `TickResolver` (offloaded to the process pool via `compute.combat_worker.run_fight` + `utils.offload.offload_cpu`); persists `combat_log` row + increments Player stat counters when `log_result=True`.
  `SimpleTTKResolver` and `DUEL_VARIANCE_PERCENT` are retired (T10). Use `pvc_damage_reduction=0.33` for PvC, `0.0` for PvP.
- `collect_stats(loadout)` / `get_dps` / `get_armour` / `get_shield` — legacy stat collection used by embed builders; still present.
- `_consume_secondary_ammo(...)` — post-fight write-back of `secondary_ammo` decrements (CI-16).
- Returns `FightResults` with `combat_log` timeline, metadata summary, and `combat_log_id`.

Uses: `CombatLogService` (deferred import), `PlayerRepository` (deferred import), `TickResolver` (from `combat_resolver.py`)

---

### combat_resolver.py — `TickResolver` (DB-free engine)

DB-free leaf module containing all combat-math symbols (constants, runtime dataclasses, helpers, the `TickResolver` class) so it can be imported in a forkserver process-pool child without pulling in SQLAlchemy/FastAPI/persist (P2-T0c split).

**Turret/primary switching is range-driven (2026-06-11, replaces `manual_turret_mode`):**
- Primaries always evaluate behind their per-weapon `range_m` gate.
- **Auto-turrets** (`automatic=True`) always fire on their own cooldown; accuracy = `pilot_turret_acc × GameConstants.AUTO_TURRET_ACCURACY_MULTIPLIER` (0.85).
- **Manual turrets** (`automatic=False`, non-plasma) fire ONLY while NO primary is in range — i.e. during the approach phase, after a shock-blast distance reset, or while a booster push holds the gap open. The instant any primary is in range (cooldown irrelevant) manual turrets go inert. A ship with zero primaries uses its manual turrets all fight. Accuracy = `pilot_primary_acc` (full §5, NOT 0.85-multiplied).
- The static per-ship `manual_turret_mode` flag is fully removed: gone from `ShipLoadout`, `_CombatantState`, `LoadoutBuilder`, the `PlayerShip` ORM, and the DB column (migration `0018_drop_manual_turret_mode.py`).

**Thread-5 chained module activations (2026-06-18, baseline — NOT tunable, no config flag):**
- **Activation caps removed.** Cloak (`_CloakRuntime`) and Booster (`_BoosterRuntime`) no longer carry a per-fight cap (was 2/4) nor one-shot threshold consumption — thresholds are **re-armable**. `activation_count` is retained as **telemetry only** and never gates. The sole gates in `_eval_hp_threshold_modules` are `cooldown_remaining_ms <= 0 AND effect_remaining_ms == 0`, PLUS a cloak **no-activate-while-invuln** guard (skip the whole cloak path while `es_runtime.invuln_remaining_ms > 0`).
- **Chain (`_try_activate_chained_module`):** Trigger A — when ES fires in `_eval_emergency_system` (~816), the **booster** activates if off cooldown (marker `trigger:"emergency_activate"`). Trigger B — when the invuln window ticks `>0 → 0` in the Phase-1 tick-down (~1500), the **cloak** activates if off cooldown (marker `trigger:"emergency_end"`). Both are one-shot at the trigger instant — on cooldown ⇒ lost, no retry; already-active is never refreshed/cut short.
- **Same-tick ES result:** ES✓ / Booster✓ (if off cd) / Cloak✗ — falls out of phase order (ES step 4a before HP-threshold step 5) + the cloak invuln guard; no bespoke arbitration.
- **Telemetry markers:** `module_activation` events carry `trigger:"emergency_activate"`/`"emergency_end"` (chained) vs `trigger_hp_pct` (normal crossing); all count toward `module_activations` stats. The detailed-log formatter (~2502) renders distinct phrasing per marker. Mirrors `COMBAT_SPEC_LOCKED.md` §7.7 / §8.

---

### combat_balance.py — Pure Functions

Combat balance hooks for the tick resolver (§5): `weapon_accuracy()`, `compute_pilot_accuracy()`, `thruster_ramp()`, `booster_debuff_pp()`, `resolve_scanner_tier()` / `ScannerTier`.

---

### combat_log_service.py — `CombatLogService`

Persists resolved fight records to `combat_log` (§12 / T10) and reads them back:
- `persist(...)` — one row per resolved fight; accepts plain-dict timelines (offload path) or `CombatEvent` dataclasses (P2-T6)
- `list_for_player` / `get_detail` — read side for `/combat-log`

---

### combat_preflight_service.py — `CombatPreflightService`

Monte-Carlo win-rate estimator (default 20 simulated fights) surfaced in the `/promote` confirmation embed. Advisory only — the `PreflightVerdict` never blocks an action.

---

### config_service.py — `ConfigService`

Guild configuration management (no auto-create — `/admin_setup` is the only path that creates a `guild_configs` row):
- `get_guild_config(db, guild_id)` — returns config summary; raises `GuildNotConfiguredError` if absent
- `get_bounty_config(db, guild_id)` — returns bounty config; raises `GuildNotConfiguredError` if absent
- `update_bounty_config(db, guild_id, updates)` — updates bounty fields; raises `GuildNotConfiguredError` if absent
- `create_or_update_config(db, config_data)` — the admin_setup creation path; always creates/updates
- `reset_to_defaults(db, guild_id)` — admin reset; wipes existing config and recreates defaults
- Provides starting_credits, channel IDs, and other per-guild settings to other services

Custom exception: `services.exceptions.GuildNotConfiguredError` (carries `guild_id` attribute).

Uses: `ConfigRepository`

---

### division_service.py — REMOVED in B.48

The `DivisionService` class and the level/division progression system were
deleted in B.48. Player progression is now driven entirely by the configurable
per-guild `xp_thresholds` JSON (Bronze/Silver/Gold/Platinum + optional
`Prestige`) on the `GuildConfig` row. Consult `player_service.promote_player`
and `player_service.prestige_player` for the canonical promotion/prestige flow.

---

### duel_service.py — `DuelService`

Duel challenge lifecycle:
- `create_challenge(...)` — validates both players exist and that **available** balances (see pending-stakes invariant above) cover the stakes; creates `DuelRequest`
- `create_challenge(...)` / `accept_duel(db, duel_id)` — both enforce the **T7 over-cap lockout** as the FIRST eval (the challenger when creating, the accepter when accepting — each is "leaving station"): if `is_over_cap(load, cap)` (from `cargo_utils`) they raise `OverCapError` (rendered by the gateway as `"Cargo Overloaded — NN/XX. Unable to leave station."`). Only the combat entries are gated — equip/unequip/buy are NOT.
- `accept_duel(db, duel_id)` — re-validates under `FOR UPDATE` locks (`get_by_id_for_update`); builds loadouts via `LoadoutBuilder` and resolves via `CombatService`; transfers credits; updates win/loss stats; runs `cancel_underfunded_duels` for the loser. **No PvC loot** — looting is bounty-only.
- `reject_duel(db, duel_id)` — marks as rejected
- `cancel_duel(...)` / `cancel_all_pending_duels(db, guild_id)` — challenger-side / admin cancellation
- `cancel_underfunded_duels(db, player_id, commit=False)` — the auto-cancel contract hook (see top of this doc)
- `expire_duel(db, duel_id)` — expires ONE duel past `expires_at`; called per-duel by `duel_expire_executor`
- read helpers: `get_duel`, `get_pending_for_target`, `get_outgoing_for_challenger`, `get_all_pending_for_guild`

Uses: `DuelRepository`, `PlayerRepository`, `UserRepository`, `ConfigRepository`, `CombatService`, `LoadoutBuilder`

---

### equipment_service.py — `EquipmentService`

Equipment management — thin wrappers that delegate to `LoadoutConsistencyService` (the B.19 choke-point):
- `equip_item(db, player_id, ship_id, item_name, equipment_type=None)` — delegates to `equip_one`
- `unequip_item(db, player_id, ship_id, item_name, equipment_type=None)` — delegates to `unequip_one`
- `equip_check(...)` — validation-only preview
- Module-class limits (`GameConstants.MODULE_EQUIP_LIMITS`, e.g. max 1 ArmourModule, unlimited CabinModule) are enforced inside the choke-point (`_validate_module_equip_limit`)
- Module-level helpers: `item_discriminator_to_concrete_type()`, plus the `_SLOT_MAP` / `_INVENTORY_TYPE_MAP` / `VALID_EQUIPMENT_TYPES` vocabulary (see canonical loadout section above)

Uses: `LoadoutConsistencyService` (deferred import), `PlayerShipRepository`, `InventoryRepository`, `ItemRepository`, `ModuleRepository`, `PlayerRepository`, `ShipRepository`, `GameConstants`

---

### game_constants.py — `GameConstants`

Centralized game constants class. **Operational constants can be overridden via environment variables** prefixed with `BOUNTYBOT_`:

```bash
BOUNTYBOT_MAX_BOUNTIES_PER_DIVISION=10
BOUNTYBOT_CHECK_COOLDOWN=120
BOUNTYBOT_BOUNTY_DELAY_RANDOM_MIN=3
```

Call `GameConstants.load()` at application startup to apply overrides. **Non-operational constants** (e.g. module equip limits, enabled-type gating) are intentionally excluded from runtime overrides to maintain game balance.

Key constant groups:
- `MODULE_EQUIP_LIMITS` — per-module-type equip limits dict
- `BOUNTY_DELAY_RANDOM_MIN/MAX` — bounty spawn frequency
- `MAX_BOUNTIES_PER_DIVISION` — bounty cap (temperature-adjusted)
- `SHOP_DEFAULT_*_NUM` — shop stock counts per category
- `CATALOG_ITEM_TYPES` / `PLAYABLE_ITEM_TYPES` / `CURRENTLY_ENABLED_TYPES` — the three economy frozensets; each now includes **all 6 concrete types** (`commodity` added for PvC loot, T1). `CURRENTLY_ENABLED_TYPES` is the surface-gating lever for secondary-weapon exposure.
- **Loot (PvC) tunable knobs (LOOT_JOURNAL §8 / COMBAT_SPEC_LOCKED §15, T2; per-guild overridable via `GuildConfig` + migration 0022)** — 19 scalar knobs: `LOOT_CHANCE_TRACTOR_T1..T4` (20/40/60/80) + `LOOT_CHANCE_NO_TRACTOR` (0); `LOOT_BAND{1,2,3}_SELECT_PCT` (10/20/70); `LOOT_BAND1_TL_WINDOW` (1); `LOOT_BAND{1,2,3}_QTY_{MIN,MODE,MAX}` (Band1 1/1/3, Band2 4/8/12, Band3 10/16/22); `LOOT_COMMODITY_SELL_FRACTION` (1.0). Each has a `BOUNTYBOT_<NAME>` env override (`_track_int`/`_track_float` in `load()`). **`LOOT_DROP_CHANCE` is a FIXED 100% constant — no env, no `GuildConfig` column, no override.**
- **Shop module-bucket knob (rebalance, 2026-06-20; COMBAT_SPEC_LOCKED §16)** — `SHOP_COMBAT_MODULE_PROB` (float **0.75**) = P(combat bucket) per module draw, filler = 1−this; env `BOUNTYBOT_SHOP_COMBAT_MODULE_PROB` + per-guild col via **migration 0023** + admin config API. The bucket-membership frozensets `SHOP_{JUNK,FILLER,COMBAT}_MODULE_TYPES` are **structural game data, NOT tunables** (no env/column; import-time disjoint+covers-all-21 `assert`). `MAX_TECH_LEVEL` raised to **10** (shop TL ceiling 9→10). See the "Shop module-bucket draw" subsection under `shop_service.py` below.
- `*_RETENTION_*` — db_retention windows (`BOUNTY_RETENTION_HOURS`, `DUEL_RETENTION_HOURS`, `AUDIT_RETENTION_DAYS`, `COMBAT_LOG_RETENTION_HOURS`)
- **Criminal loadout-balance knobs (Threads 1/3/4/6, 2026-06-18; per-guild overridable via `GuildConfig` + `resolve_constant`)** — `SHOP_HEAVY_SECONDARY_SUBTYPES` (now includes `cluster-missile`), `LONG_RANGE_THRESHOLD_M` (2600), `CRIMINAL_LONG_RANGE_PCT` (0.50), `PRIMARY_TL_BAND_WEIGHTS` (`{center:70, minus1:20, plus1:10}`), the four per-division equip-% dicts `CRIMINAL_{CLOAK,BOOSTER,EMERGENCY,WEAPONMOD}_CHANCE_BY_DIVISION`, and the toggle `CRIMINAL_EXCLUDE_EMP_WEAPONS` (default True). See the "Criminal loadout-generation algorithm" section above. `CRIMINAL_EQUIP_DAMAGELESS_WEAPON_CHANCE` (20) is **dead** (no consumer) — flagged for cleanup.
- B.48: `DIVISION_NAMES`, `DIVISION_BOUNDARIES`, and `XP_LEVEL_BOUNDARIES` were
  deleted along with the level/division progression system.

---

### game_maths.py — Pure Functions

Module-level functions only (no class):
- `pick_random_item_tl(shop_tl)` — TL probability kernel
- `reward_per_sys_check(tech_level, loadout_value)` — bounty reward formula
- `ship_tech_level_for_value(value)` — TL classification

B.48: `calculate_user_level` and `calculate_xp_for_level` were deleted.

---

### inventory_service.py — `InventoryService`

Player inventory (cargo) management:
- `get_player_inventory(db, player_id, item_type=None, include_ships=False)` — full inventory list; `item_type` accepts concrete types or generic aliases (normalizer-expanded). `include_ships=True` additionally lists the player's INACTIVE ships as cargo entries (ships live in `player_ships`, not `player_inventories`; the active ship is "equipped" and excluded) — default False so equip/sell consumers are unchanged
- `get_inventory_summary(db, player_id, include_ships=False)` — per-type counts; `include_ships` adds the inactive-ship count to the `ship` bucket
- `add_item_to_inventory` / `remove_item_from_inventory` — cargo-only mutations. `add_item_to_inventory(..., commit=False)` re-locks the player `FOR UPDATE` and is the txn-composable path the PvC loot write uses (see `bounty_service`).
- `_validate_item_exists` — now scans `CommodityRepository` too, so commodity writes resolve (PvC loot, T1)
- `transfer_item_between_players(...)` — cargo-only remove+add pair across two players (see "Unequip-before-sell" caveat above)
- `search_inventory`, `validate_item_compatibility`, `get_player_item_count`, `consolidate_inventory`

Uses: `InventoryRepository`, `PlayerRepository`, `PlayerShipRepository`, `ShipRepository`, `PrimaryWeaponRepository`, `SecondaryWeaponRepository`, `TurretWeaponRepository`, `ModuleRepository`, `CommodityRepository`

---

### map_renderer.py — `MapRenderer`

Pillow-based star map image generation over the base map shipped at `import_data/system-map.png`:
- `prewarm()` — preloads/caches the base image (P3-T1)
- `render_route(...)` / `render_route_for_bounty(...)` — PNG bytes with the route drawn over the map
- `render_route_offloaded(...)` — async wrapper that renders in the shared process pool (P3)
- Uses `PIL.Image`, `PIL.ImageDraw`; consumes `SystemGraphService` nodes for coordinates

---

### pathfinding_service.py — `PathfindingService`

A* pathfinding over the star system graph:
- `make_route(start, end)` — returns a list of system names, or a `PathfindingError` enum member on failure
- Module-level `MAX_ROUTE_LENGTH = 50` prevents runaway searches

Uses: `SystemGraphService`

---

### player_service.py — `PlayerService`

Core player management:
- `get_or_create_player(db, discord_id, guild_id, discord_username)` — creates user if needed, creates player with `starting_credits` from guild config
- `update_player_credits(db, player_id, new_credits, update_lifetime)` — sets absolute credit balance; optionally updates lifetime_credits
- `update_player_xp(db, player_id, xp)` — sets XP only. Tier is NOT auto-advanced; use `promote_player()` to explicitly cross a tier threshold.
- `promote_player(db, player_id)` / `demote_player(db, player_id)` — explicit tier change; gated by `xp_thresholds`; raises `TierChangeCooldownError` (defined in this module) inside the cooldown window
- `get_promotion_status(db, player_id)` — read-side promotion eligibility summary
- `prestige_player(db, player_id)` — B.49: gated on `xp_thresholds["Prestige"]` (default 50,000 when key absent); resets XP/credits/tier, deletes all ships/inventory, recreates the starter Betty loadout via `_create_starter_loadout()`, increments `prestige_count`; preserves lifetime_credits/duel stats/bounty stats. Returns dict with `tier_before` and `xp_before`.
- `transfer_credits(db, source_id, target_id, amount)` — atomic transfer using `get_by_id_for_update` to prevent race conditions
- `get_player_statistics(db, player_id)` — assembles comprehensive stats dict

Uses: `PlayerRepository`, `UserRepository`, `ConfigRepository`

---

### shop_service.py — `ShopService`

Multi-tier shop system (`VALID_TIERS = Bronze/Silver/Gold/Platinum`):
- `refresh_shop(db, guild_id, tier, force_tech_level=None)` — regenerates a tier's stock (`SHOP_DEFAULT_*_NUM` items per category); called by `shop_refresh_executor`. The batch draws a `shop_tech_level` over `[MIN_TECH_LEVEL, MAX_TECH_LEVEL]` = **`[1, 10]`** (TL ceiling raised 9→10 so TL10 armour/shields can finally spawn — `force_tech_level` also accepts 1..10); each item then gets its own drawn TL (`_select_item_tech_level`, unchanged 0.7/0.2/0.1 over T/T−1/T−2), and the **shop row stores that per-item TL** (ships: value-derived via `game_maths.ship_tech_level_for_value`; modules: the actual catalog TL **after step-down** via `_get_item_tech_level`) — NOT the batch TL. The returned `refresh_details` dict still carries the batch `tech_level`. See **"Shop module-bucket draw"** below + `COMBAT_SPEC_LOCKED.md §16`.

#### Shop module-bucket draw (rebalance, 2026-06-20) — CANONICAL: `COMBAT_SPEC_LOCKED.md §16`
The `module` branch of `_get_random_item_by_tech_level` no longer draws uniformly across a TL. The 21 module types are partitioned into three **disjoint** frozensets in `game_constants.py` (import-time `assert` enforces disjoint + covers-all-21):
- **`SHOP_JUNK_MODULE_TYPES`** (`TransfusionBeam`, `ShieldInjector`, `TimeExtender`, `JumpDrive`) — **excluded from the shop pool entirely.**
- **`SHOP_FILLER_MODULE_TYPES`** (`GammaShield`, `SpectralFilter`, `RepairBeam`, `Signature`, `MiningDrill`, `Compressor`, `Cabin`).
- **`SHOP_COMBAT_MODULE_TYPES`** (`Scanner`, `Armour`, `Shield`, `Cloak`, `Booster`, `EmergencySystem`, `RepairBot`, `PrimaryWeaponMod`, `Thruster`, **`TractorBeam`** — tractor is first-class here because it gates PvC loot).

Membership mirrors the criminal loadout classification with two shop overrides: TractorBeam moved FILLER→COMBAT, and JUNK dropped. Each module slot: pick a **bucket** — COMBAT with prob `SHOP_COMBAT_MODULE_PROB` (default **0.75**, resolved once per refresh via `resolve_constant`), else FILLER (0.25) — then uniform-random within that bucket at the band TL. **Empty-bucket step-down:** if the bucket is empty at the band TL, step DOWN one TL at a time until non-empty (current catalog: only COMBAT@TL9 is empty → steps to TL8); the stored row TL+price reflect the ACTUAL drawn item's TL, not the band TL. `SHOP_COMBAT_MODULE_PROB` is the only tunable (per-guild col via **migration `0023_shop_combat_module_prob`**, env `BOUNTYBOT_SHOP_COMBAT_MODULE_PROB`, admin config API); the frozensets are structural game data, NOT per-guild overridable.
- `_get_item_tech_level(db, item_type, item_name, base_price)` — resolves an item's real catalog TL; `_add_item_to_shop` (the `/sell` restock path) stores it so sold-back items keep their true TL
- `get_shop_items(db, guild_id, tier, ...)` — read side
- `purchase_item(...)` — validates tier eligibility + credits; cargo-only `add_item`; runs the duel auto-cancel hook
- `purchase_ship(...)` — buys + activates a ship via the `activate_ship` choke-point; runs the duel auto-cancel hook
- `sell_item(db, player_id, item_name, quantity=1)` — cargo-only; type/tier resolved server-side (A.42, see below). **Commodity branch (PvC loot):** commodities sell as a face-value **sink** (`Item.value × qty × LOOT_COMMODITY_SELL_FRACTION`, destroy + credit, the returned txn dict carries `"sunk": True`) — they are NEVER added to a `GuildShop`. Weapons/Modules still restock the player's current-tier shop via `_add_item_to_shop`.
- `sell_ship(...)` — evacuates the loadout to cargo first (`evacuate_ship_loadout_to_inventory`)
- `preload_static_data(db)` / `clear_static_cache()` — in-memory static-catalog cache for bulk refreshes

Uses: `ShopRepository`, `ConfigRepository`, `PlayerRepository`, `InventoryRepository`, `ItemRepository`, `ShipRepository`, `PlayerShipRepository`, `PrimaryWeaponRepository`, `SecondaryWeaponRepository`, `TurretWeaponRepository`, `ModuleRepository`

---

### system_graph_service.py — `SystemGraphService`

Star system adjacency graph (in-memory `SystemNode` cache):
- `load_graph(db)` — loads all systems from DB into the node cache
- `get_system(name)` / `get_neighbours(name)` / `get_all_systems()` / `get_systems_with_jump_gates()`
- `euclidean_distance(sys_a, sys_b)` (static), `is_loaded()`, `reset()`
- Used by `PathfindingService` and `MapRenderer`

---

### temperature_service.py — `TemperatureService`

Guild activity temperature (all static methods; per-division values live in `GuildConfig.division_temperatures`):
- `get_max_bounties(temperature)` — max bounties per division for an activity level (up to `MAX_BOUNTIES_PER_DIVISION`)
- `raise_temperature(current_temp, amount=None)` — bumps temperature on player activity
- `decay_temperature(current_temp, guild_config=None)` / `decay_temperature_n_hours(...)` — multiplies by 2/3, floors at 1.0; called hourly by `temperature_decay_executor`
- `calculate_spawn_delay(temperature, route_length, guild_config=None)` — spawn-pacing input for the orchestrator

---

### loot_engine.py — Pure Functions (PvC loot)

Stateless, RNG-injectable selection math for the PvC loot system (no DB, no class):
- discrete-**triangular** quantity sampler over `(min, mode, max)` — Band1 `(1,1,3)` → 50/33/17, Band2 `(4,8,12)`, Band3 `(10,16,22)`
- weighted **band-select** (Band1/2/3 = 10/20/70)
- **within-band item pick** — uniform over the eligible commodity pool (Bands 2/3), or uniform over Band-1 Weapons/Modules within ±`LOOT_BAND1_TL_WINDOW` of the criminal TL (with a nearest-TL fallback)
- **tractor → chance** resolution (static TL map {4,5,7,8} → 20/40/60/80; none → 0) and the success roll

Used by `LootService` (cache-backed) and `BountyService` (spawn roll + win-branch write).

### loot_service.py — `LootService` (PvC loot)

Owns the **startup static cache** (rebuilt only on a seed reload, mirroring `shop_service.preload_static_data`): the Band-1 base pool (lootable Weapons+Modules with `tech_level` + concrete type, minus the 3 excluded module types), the Band-2/3 commodity pools, each lootable commodity's `Item.value`, and the tractor-beam → chance map. Exposes `roll_loot(...)` (spawn-side item+qty roll), the tractor resolution helpers (`equipped_tractor_name`, chance lookup), and the success roll — all consumed off the latency-sensitive kill path so no per-kill query is issued.

### cargo_utils.py — Pure Functions (PvC loot)

Canonical cargo-load helpers so the T5 loot clamp and the T7 over-cap gate share ONE definition: `compute_free_cargo(current_load, effective_cap)` and `is_over_cap(current_load, effective_cap)` (strict `load > cap`). Cargo load = `sum(player_inventories.quantity)` (ship cargo only; equipped gear excluded).

---

### loadout_builder.py — `LoadoutBuilder`

Builds `ShipLoadout` objects for combat, separated from the simulation itself:
- `from_player(db, player_id)` — from the player's active ship + equipped items
- `from_criminal_ship(criminal_ship)` (static) — from a bounty's `criminal_ship` JSON dict

---

### loadout_consistency_service.py — `LoadoutConsistencyService`

The single canonical loadout↔inventory mutation choke-point. Fully documented in the dedicated section below ("Loadout↔Inventory Consistency Choke-Point").

---

### loadout_effect_service.py — `LoadoutEffectService`

Maps module types to display-ready effect strings server-side so the gateway embed builder renders them as-is (LOADOUT_EMBED_DESIGN_SPEC §2.4/§2.5).

---

### loadout_response_service.py — `LoadoutResponseService`

Assembles the `/loadout` and bounty-loadout API responses (`build_player_loadout`, `build_bounty_loadout`, `_build_cargo_items`) reading BOTH pools. Criminal module dedup rules: see "Criminal-Only Module Dedup Invariant" section above.

---

### _item_type_normalizer.py — Pure Functions

`expand_item_type_to_concrete(item_type, *, context)` — see "Item-Type Vocabulary & Normalizer Contract" section above.

---

### _transaction_guards.py — `requires_transaction`

Runtime transaction-discipline decorator (AC-6, B.34): raises at call time when a decorated service method runs outside an active SQLAlchemy transaction. Complements the static linter `tests/test_transaction_discipline.py`.

---

### exceptions.py — Custom Exceptions

- `GuildNotConfiguredError(Exception)` — no `guild_configs` row (carries `guild_id`)
- `InvalidItemTypeError(ValueError)` — unknown/disabled item type (subclasses `ValueError` so existing handlers still catch it)
- `OverCapError` — T7 over-cap lockout; raised by the duel-challenge / duel-accept / `/check` entries when a player's cargo load exceeds cap. Carries `current_load` / `effective_cap`; its message is `"Cargo Overloaded — NN/XX. Unable to leave station."` (rendered ephemeral by the gateway).

---

## Service Interaction Map

```
DuelService ──── CombatService ──── TickResolver (combat_resolver.py)
            │                 │
            │                 └──── CombatLogService
            └─── LoadoutBuilder

BountyService ─── PathfindingService ─── SystemGraphService
             │
             └─── CombatService (bounty-check fights via fight_ships)

EquipmentService ──┐
ShopService ───────┼── LoadoutConsistencyService (choke-point)
PlayerService ─────┘   (starter loadout / activate / evacuate)

TemperatureService — consumed by executors (temperature_decay, bounty_spawn),
                     not by other services
```

---

## How to Add a New Service

1. **Create the file** `services/<name>_service.py`
2. **Instantiate repositories** in `__init__(self)`:
   ```python
   from persist.repositories.my_repo import MyRepository


   class MyService:
       def __init__(self):
           self.my_repo = MyRepository()
   ```
3. **Inject `AsyncSession` per call** — never store `db` on the instance
4. **Add tests** in `tests/services/test_<name>_service.py`
5. **Add a `Depends()` factory** in the relevant router if it needs HTTP exposure

---

## Loadout↔Inventory Consistency Choke-Point (Package G B.19, 2026-04-29)

`LoadoutConsistencyService` is the **single canonical mutation point** for any
operation that touches both `player_ships.{weapons,modules,turrets,secondary_weapons}`
JSON and `player_inventories` rows.  It enforces four hard invariants:

- **I1** — No item duplication across a single player's ships.
- **I2** — No materialisation from nothing (every JSON entry has an inventory provenance).
- **I3** — Atomicity (caller owns the transaction; the service uses `commit=False`).
- **I4** — Active ship within static slot caps.

### Public API (always `commit=False`)

| Method | Used by |
|---|---|
| `equip_one(db, player_id, ship_id, item_name, equipment_type=None)` | `EquipmentService.equip_item` |
| `unequip_one(db, player_id, ship_id, item_name, equipment_type=None)` | `EquipmentService.unequip_item` |
| `activate_ship(db, player_id, target_ship_id, *, player_repo)` | `set_active_ship` router, `ShopService.purchase_ship` — **canonical activation choke-point (B.94/B.95)** |
| `transfer_loadout_to_new_ship(db, player_id, src_ship, dst_ship, slot_limits)` | `activate_ship` (called internally) |
| `evacuate_ship_loadout_to_inventory(db, ship)` | `ShopService.sell_ship`, `transfer_ship` router, `admin_remove_ship` |
| `reconcile_active_ship_slots(db, player_id, target_ship_id)` | `activate_ship` (called internally) |
| `repair_player(db, player_id, *, dry_run=False)` | `0002_b19_repair_loadout_consistency` migration; admin tool |

### HARD RULE — DO NOT VIOLATE

Direct calls to `inventory_repo.add_item` / `inventory_repo.remove_item`
**paired** with `player_ship_repo.add_equipment` / `remove_equipment` outside
`LoadoutConsistencyService` are forbidden.  Future PRs that introduce such
pairings should be rejected at review.

The choke-point is what guarantees I1 and I2 by construction; bypassing it
re-introduces the B.19 phantom-item / cross-ship duplication bug class.

### Constructor injection for tests

`LoadoutConsistencyService(*, player_ship_repo=None, inventory_repo=None,
item_repo=None, ship_repo=None, player_repo=None)` accepts optional repo overrides so callers
(e.g. `EquipmentService.equip_item`, `ShopService.purchase_ship`) can share
their already-mocked repositories with the consistency service in unit
tests.

### Choke-points & flows — where each operation actually mutates state

Use this as the map when changing or debugging any loadout/inventory flow. The
**invariants** these flows preserve are stated in the
*Loadout & Inventory system — CANONICAL REFERENCE* section above; this is the
plumbing.

| User flow | Entry point (service) | Touches slots? | Touches cargo? | Choke-point method |
|-----------|-----------------------|----------------|----------------|--------------------|
| `/equip` | `EquipmentService.equip_item` → `equip_one` | +1 slot | −1 cargo | `equip_one` |
| `/unequip` | `EquipmentService.unequip_item` → `unequip_one` | −1 slot | +1 cargo | `unequip_one` |
| swap (cog) | gateway `inventoryCog` does unequip-then-equip | replace | net 0 | `unequip_one` + `equip_one` |
| `/buy` item | `ShopService.purchase_item` (≈line 150) | no | +qty cargo | none (cargo-only `add_item`) |
| `/buy` ship | `ShopService.purchase_ship` (≈line 258) | moves gear src→dst, overflow→cargo | overflow only | `activate_ship` → `transfer_loadout_to_new_ship` |
| `/sell` item | `ShopService.sell_item` (≈line 409) | no (cargo-only — see "Unequip-before-sell") | −qty cargo | none (cargo-only `remove_item`) |
| sell ship | `ShopService.sell_ship` | evacuates gear→cargo | +cargo | `evacuate_ship_loadout_to_inventory` |
| `/transfer` (give) | `InventoryService.transfer_item_between_players` | no (cargo-only) | −cargo src / +cargo dst | none (cargo-only; see caveat above) |
| set active ship | `ships.set_active_ship` | reconcile + transfer | overflow→cargo | `activate_ship` |
| transfer ship | `ships.transfer_ship` | evacuates gear→cargo | +cargo | `evacuate_ship_loadout_to_inventory` |
| admin remove ship | `admin.remove_ship` | evacuates gear→cargo | +cargo | `evacuate_ship_loadout_to_inventory` |
| starter loadout | `PlayerService._create_starter_loadout` | +3 slots | +4 then −3 | `equip_one` ×3 |
| B.19 repair / migration | `repair_player` | dedups slots | none (never mints) | `repair_player` |

Two reads worth knowing: `LoadoutResponseService` (`_build_cargo_items` etc.)
assembles the `/loadout` view by reading **both** pools; gateway-side
`inventoryCog.equip_autocomplete` filters the equip dropdown on cargo `quantity`
(active-ship scope — see Autocomplete Filter note above). Reads must never
mutate.

Repositories backing all of this stay **dumb**: `PlayerShipRepository`
(`update_loadout` / `add_equipment` / `remove_equipment` / `get_ship_loadout_summary`)
and `InventoryRepository` (`add_item` / `remove_item` / `get_player_item` /
`get_player_items_by_name`) do data access only — they enforce none of the
cross-table invariants. That is the choke-point's job.

---

*Last updated: 2026-06-11 — full doc-vs-code reconciliation: module reference expanded to all 27 modules (combat_resolver/balance/log/preflight, loadout_* helpers, guards, exceptions); range-driven manual-turret switching documented (manual_turret_mode removed, migration 0018); CI-18 unique constraint on player_inventories documented (migration 0015); InventoryService include_ships and ShopService per-item shop tech levels documented; stale method names/signatures corrected throughout.*
