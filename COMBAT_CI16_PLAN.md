# CI-16 Implementation Plan — Consumable Secondary Weapons (ammo)

*Ready-to-execute d-developer brief. Produced by d-architect 2026-06-03, with owner's
final decisions baked in. Tracker entry: `COMBAT_ISSUES.md` → CI-16. Danger zone — read
`services/bot-core/src/services/AGENTS.md` → "Loadout & Inventory system" FIRST.*

## Goal
Secondary weapons become ammo-limited consumables. Firing consumes 1 round per trigger;
ammo persists across battles (player); restock via shop only. `ammo=None` = infinite
(legacy/test back-compat).

## ⚠️ ARCHITECT CORRECTIONS (design-lock pass, 2026-06-03) — THESE SUPERSEDE ANY CONFLICTING TEXT BELOW
The original brief (above/below) was validated against current code. It is ~90% sound but
had two BLOCKER omissions and several stale facts. Apply ALL of these:

1. **Conservation model (document in AGENTS.md as part of this change).** For a secondary,
   countable copies ARE its rounds:
   `owned(S) = cargo.quantity(S) + Σ_ships secondary_ammo[S]`.
   The `secondary_weapons` slot-list entry is **pure slot occupancy — NOT a counted copy**
   (deliberate divergence from primaries/turrets/modules where each slot entry = 1 copy).
   This dual meaning is the single conceptual hazard; every slot-mutating path must honor it.

2. **BLOCKER R1 — ship transfer.** `loadout_consistency_service.transfer_loadout_to_new_ship`
   (~line 442; reached by `purchase_ship`→`activate_ship`) currently moves the secondary NAME
   to the new ship but leaves `secondary_ammo[S]` on the old ship, and overflow returns only 1
   copy. FIX: when a secondary fits → move `secondary_ammo[S]` src→dst (reassign both dicts);
   when it overflows → `cargo += secondary_ammo[S]` then `del`. **Add to scope + tests.**

3. **BLOCKER R2 — ship sell/evacuate.** `evacuate_ship_loadout_to_inventory` (~line 513; used by
   sell_ship/transfer_ship/admin_remove_ship) returns `add_item(...,1...)` per secondary, dropping
   `secondary_ammo[S]-1` rounds. FIX: for secondaries `cargo += secondary_ammo[S]` then `del`.
   Also audit `_remove_one_slot_reference_from_other_ships` (~544, R3) and `repair_player` (~725)
   so they don't silently vaporize a full ammo stack. **Add to scope + tests.**

4. **Resolver: 7 fire branches, ONE post-dispatch decrement (NOT per-branch).** The branches are
   `rocket`/`missile`/`cluster-missile`/`nuke`/`shock-blast`/`ionizing-missile` (+ deferred `else`
   noop). The original "6 branches, decrement in each" is wrong (forgets `ionizing-missile`) and
   fragile. Instead place ONE decrement+depletion block AFTER the if/elif chain, gated on
   "fired this tick": every firing branch resets `_sw.cooldown_remaining_ms = loading_speed_ms`
   (always >0), the deferred `else` noop does not — so
   `if _sw.remaining_ammo is not None and _sw.cooldown_remaining_ms > 0: remaining_ammo -= 1;
   if remaining_ammo == 0 → emit secondary_depleted`. Naturally excludes non-firing subtypes.

5. **DROP the un-gate step — it's already done.** `secondary_weapon` is ALREADY in
   `GameConstants.CURRENTLY_ENABLED_TYPES` (`game_constants.py:216`). No change there. BUT
   AGENTS.md **INVARIANT #7** and the "Incoming: secondary_ammo" section both say it's off /
   not built — those are now factually WRONG; correct them in this pass.

6. **Line-ref drift (current code):** bake in `_init_combatant` is **333-362** (not ~333-351);
   sim guard returns at **2137**; `_increment_player_stats` call (write-back anchor) at
   **2173-2179**; fire loop **1300-1489**; CI-15 hull branch site **821-840**; CI-13 label ~**382**.
   Combat-log events are **CombatEvent dataclass objects** — use `ev.actor`/`ev.type`/`ev.data`
   ATTRIBUTE access, NOT dict keys. Load the active ship in write-back via
   `player_ship_repository.get_active_ship(session, player.id)` (exists, line 138).

## LOCKED design (owner-confirmed — do not relitigate)
1. Slot = a specific secondary weapon item ("type"); `max_secondaries` = # distinct types; each type has its own **unbounded** round stack.
2. Permanent cross-battle depletion (player). Restock = shop only.
3. Shop buy: if type already equipped on the **active** ship → top up the equipped stack; else → cargo (inventory) for `/equip`.
4. `/equip` of an already-equipped type = top-up (no new slot); new type = needs a free slot.
5. Auto-unequip at 0 = **post-fight** loadout cleanup (mid-fight just stops firing; never a live mid-tick mutation).
6. Keep current seed cooldowns (nukes 6–10s). Limit = qty + cooldown only; NO hard 1/battle rule (leave a clean extension point for a future per-battle cap / fire-chance RNG).
7. Uniform in-fight consumption gate for player AND criminal. Player ammo persists; criminal does NOT (respawns fresh; cross-fight persistence deferred to Phase-2). **NOTE:** criminals have no secondaries today — arming them is CI-17, a SEPARATE later task. CI-16 only needs the resolver gate to be uniform so it works once CI-17 lands.
8. Cluster missiles: **1 round per fire trigger** → N munitions, per-munition hit roll (existing burst mechanics unchanged; just add the single decrement).
9. Display: show round count wherever equipped gear shows (`/loadout` + ship API responses); combat log notes "out of ammo" when a secondary runs dry mid-fight.
- **#4 (owner): NO `DEFAULT_STARTER_AMMO`, NO backfill.** No equipped secondary ever pre-exists (starter has none; only shop grants them). Migration just adds the column default `{}`; ammo always originates from purchase qty.
- **#3 (owner): sell REQUIRES `/unequip` first — already STRUCTURALLY enforced** (sell/transfer touch cargo only; equipped gear isn't in `player_inventories`). PRESERVE this; do NOT add a redundant is-equipped check.

## Storage model (chosen): sidecar JSON map
Add `player_ships.secondary_ammo: Mapped[dict[str,int] | None]` (JSON, nullable, default `{}`).
Keep `secondary_weapons: list[str]` as the equipped-type list (slot identity; `len()` = slots used).
`secondary_ammo[name]` = remaining rounds for that equipped type. **Why sidecar:** purely additive —
every existing `secondary_weapons` reader keeps working; ammo read only where needed. Respects the
cargo-vs-equipped invariant. SQLAlchemy JSON requires **reassignment** (not in-place mutation) for change tracking.

**Migration `0013_secondary_ammo.py`** (after 0012; idempotent w/ `inspector` guards like 0011):
- `upgrade`: add `secondary_ammo` JSON column nullable if absent. **No backfill** (no equipped secondaries exist; default `{}`).
- `downgrade`: drop column if present.

## Resolver consumption (`combat_service.py`, `combat_models.py`)
- `WeaponStats` (frozen): add `ammo: int | None = None` (secondaries; None=infinite).
- `_SecondaryWeaponRuntime`: add mutable `remaining_ammo: int | None = None`; bake from `sw.ammo` in `_init_combatant` (~333-351).
- Fire gate (FIRST gate, before cooldown/range, in the `for _sw in effective_secondaries` loop ~1301): `if _sw.remaining_ammo is not None and _sw.remaining_ammo <= 0: continue`.
- Decrement: ⚠ SEE CORRECTION #4 — ONE block AFTER the if/elif chain (7 branches, gated on `cooldown_remaining_ms > 0` = "fired this tick"), `-1` (cluster = 1 per trigger). When it hits 0, emit a new `CombatEventType.secondary_depleted` event `{weapon, subtype}`. Do NOT decrement inside each branch.
- Add `CombatEventType.secondary_depleted = "secondary_depleted"`.
- Player builder `from_player`: set `WeaponStats.ammo = (player_ship.secondary_ammo or {}).get(sw_name, 0)`.

## Post-fight write-back (`combat_service.py` `fight_ships`)
- New `_consume_secondary_ammo`, called right after `_increment_player_stats` (~2179), inside the `log_result=True` branch. **Sim guard is free**: preflight passes `log_result=False` and returns at line ~2131 before write-back.
- For each HUMAN combatant (`user_id is not None`): load player → active `PlayerShip`; derive consumed `{name: rounds}` by scanning `fight_results.combat_log` for `weapon_fire` events with `actor==name and data["slot"]=="secondary"`, grouped by `data["weapon"]`; `ammo[name] = max(0, ammo.get(name,0)-used)`; if 0 → remove name from `secondary_weapons` AND `del ammo[name]` (auto-unequip); reassign both JSON fields; `session.flush()` (caller owns commit). Criminal side: no write-back. Mirror the non-fatal try/except style of `_increment_player_stats`.

## Equip / shop (DANGER ZONE — invariant-test exhaustively)
- ~~Un-gate: add `"secondary_weapon"` to `CURRENTLY_ENABLED_TYPES`~~ — ALREADY DONE (correction #5); no change.
- `loadout_consistency_service.equip_one`: already-equipped type → move player's WHOLE cargo stack into `secondary_ammo[name]` (no slot, no `secondary_weapons` change); new type → slot-cap check, append name, `secondary_ammo[name]=cargo_qty_moved`, decrement cargo whole stack.
- `unequip_one` / `return_all_to_inventory`: return WHOLE remaining stack to cargo (`cargo.quantity += secondary_ammo[name]`), remove name, `del secondary_ammo[name]`.
- **`transfer_loadout_to_new_ship` (R1) + `evacuate_ship_loadout_to_inventory` (R2)** — see corrections #2/#3: carry/return the WHOLE `secondary_ammo` stack, never just 1 copy. Audit `_remove_one_slot_reference_from_other_ships` + `repair_player` too.
- `shop_service.purchase_item`: for `item_type=="secondary_weapon"`, if name in active ship's `secondary_weapons` → `secondary_ammo[name] += quantity` (skip cargo add); else → normal cargo `add_item`. Load active ship inside the method; keep the single atomic commit; JSON reassignment.

## Display
- `PlayerShip.to_dict()`: add `secondary_ammo`.
- `ships_schema.py` `ShipResponse` + `ShipLoadoutSummaryResponse`: add `secondary_ammo` (+ counts); wire `/ships/{id}/loadout` + `get_ship_loadout_summary`.
- gateway `shipsCog.py` `/loadout`: render `• {name} ×{ammo}`.

## Folded-in fixes (same dev pass)
- **CI-13:** `combat_log_service.py:~387` nuke/shock-blast label reads a `hit` flag nukes lack → always "miss". Make damage-aware: nuke → "detonated" (use opponent/self damage); shock-blast → "distance reset"; hit-roll subtypes keep hit/miss. Add `secondary_depleted` → "{actor} ran out of {weapon}".
- **CI-15:** `combat_service.py:821-840` emits `layer_depleted` for shield/armour only — add a hull branch (`hull_was_positive and current_hull<=0`, only on true death after ES/clamp). Extractor already has `"Hull depleted (dead)"` label.

## Spec delta
Add to `COMBAT_SPEC_LOCKED.md` §6.2 a "Secondary ammunition (consumable)" subsection (1 round/trigger incl cluster; ammo gate before cooldown/range; `secondary_depleted` event; player persists across battles + auto-unequip at 0 post-fight; criminal in-fight only, no persist; ammo ≠ energy; future per-battle-cap extension point). Cross-ref §7.7 (generalized ES consumable pattern) + §0.2.

## Test plan (bot-core; tee to log, grep)
New `tests/services/test_secondary_ammo.py`: ammo=1 fires once; ammo=3 → 3 then stop; ammo=None → infinite (T6 back-compat); cluster ammo=1/burst=5 → one fire, depleted; `secondary_depleted` at exact tick. Write-back: consume 2 → active ship ammo -2; deplete → name removed; **preflight `log_result=False` → ammo UNCHANGED**; duel → both humans written back; PvC → player only, criminal untouched. **Equip/shop INVARIANTS (paranoid):** buy-equipped→top-up no cargo/no slot; buy-unequipped→cargo; equip new→slot+seed ammo; equip equipped→top-up no slot; unequip→rounds back to cargo; `owned=cargo+equipped` conserved throughout; no dup/orphan rows. Migration up/down round-trip. Keep `test_secondary_weapons_t6.py`/`test_loadout_builder.py`/`test_equipment_service.py`/`test_combat_service.py` green.

## File checklist (architect-adjusted)
- `combat_models.py` — `WeaponStats.ammo`; `CombatEventType.secondary_depleted`
- `combat_service.py` — `_SecondaryWeaponRuntime.remaining_ammo` (~71); bake (333-362); fire gate + SINGLE post-dispatch decrement (1300-1489); CI-15 hull branch (821-840); `_consume_secondary_ammo` + call (2173-2179)
- `loadout_builder.py` — `from_player` ammo read (312-324)
- `loadout_consistency_service.py` — `equip_one` (248), `unequip_one` (363), **`transfer_loadout_to_new_ship` (~442, R1)**, **`evacuate_ship_loadout_to_inventory` (~513, R2)**, audit `_remove_one_slot_reference_from_other_ships` (~544) + `repair_player` (~725)
- `shop_service.py` — `purchase_item` top-up branch (210-223); verify `purchase_ship`/`sell_ship` ammo via the consistency-service paths
- ~~`game_constants.py`~~ — REMOVED (already enabled; no change)
- `combat_log_service.py` — CI-13 label (~382); `secondary_depleted` label
- `persist/models/player_ship.py` — `secondary_ammo` column + `to_dict` (53)
- `persist/repositories/player_ship_repository.py` — ammo-aware loadout/serialization (`update_loadout` ~225)
- `api/schemas/ships_schema.py` — `ShipResponse` + `ShipLoadoutSummaryResponse`
- `api/routers/ships.py` — wire `secondary_ammo` into loadout summary
- `persist/database/revisions/versions/0013_secondary_ammo.py` (new, `down_revision="0012"`)
- `discord-gateway/src/cogs/shipsCog.py` — render `• {name} ×{ammo}`
- **`services/AGENTS.md`** — correct INVARIANT #7 + "Incoming: secondary_ammo" section; document the conservation model (#1)
- `COMBAT_SPEC_LOCKED.md` — §6.2 secondary-ammo subsection
