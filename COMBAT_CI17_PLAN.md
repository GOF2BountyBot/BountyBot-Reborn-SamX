# CI-17 Implementation Plan — Criminals get COMPLETE auto-generated loadouts

*Ready-to-execute d-developer brief. Produced by d-architect 2026-06-04 (design-lock),
owner-decision knobs filled with conservative defaults by orchestrator (owner asleep —
all 4 flagged for morning review; they are isolated tunable constants).*
*Tracker: `COMBAT_ISSUES.md` → CI-17. Builds on CI-16 (consumable secondaries, committed `e1b8f65`,
live-validated combat-log 51). Read `services/bot-core/src/services/AGENTS.md` → "Loadout &
Inventory system" FIRST (slot caps, unique-equip, the secondary slot=type/ammo=rounds model).*

## Goal
Bounty criminals get **complete** auto-generated loadouts — they currently get primaries / turrets /
modules but **NO secondaries**. Add tier-appropriate secondary-weapon generation so criminals fire
secondaries (incl. nukes) in combat via the existing CI-16 resolver gate. Criminal ammo is in-fight
only (NO cross-fight persistence — criminals respawn fresh; that's deferred to Phase-2).

## Verdict (architect): PURELY ADDITIVE
No resolver change, no migration, no schema change. `criminal_ship` is a free-form JSON column;
the CI-16 resolver already iterates `loadout.secondary_weapons` uniformly for player+criminal,
bakes `remaining_ammo` from `WeaponStats.ammo`, gates+decrements per fire, and **already skips NPC
write-back** (`combat_service.py` `_consume_secondary_ammo`: `user_id is None → continue`). So
criminals consume in-fight with zero persistence automatically once their `secondary_weapons` list
is non-empty.

## OWNER-DECISION knobs (conservative defaults applied — flag for morning review)
1. **Nuke rounds for criminals = 1.** Rounds is the ONLY lever vs nuke spam (owner locked "no hard
   1/battle rule" in CI-16; seed nuke cooldowns 6–10s « 180s battle → would otherwise re-fire ~4×).
   Default 1 prevents unwinnable alpha-strikes; owner may raise.
2. **Other subtype rounds:** missile=5, rocket=5, cluster-missile=3, shock-blast=2. Flat constants.
   (Owner may switch to TL-scaled or random-in-range later.)
3. **Exclude zero-damage secondaries** (`damage <= 0`) as dead weight — drops ionizing-missile,
   "Mamba EMP", "Fireworks" (dmg=1 → also excluded by `<=0`? No: 1>0, so Fireworks would survive;
   architect listed it as borderline. DEFAULT: exclude `damage <= 1` to drop the dmg=1 Fireworks
   too, since a 1-dmg nuke is pure dead weight. Tunable constant.)
4. **Reward/`total_value` accounting:** count each secondary's `value` ONCE per equipped type
   (matches primaries/turrets unit-per-slot accounting). Not scaled by rounds.

All four live as named `GameConstants` (NOT magic numbers) so the owner retunes in one place.

## LOCKED design details (from architect, owner-confirmed model)
- **Secondary slot = one distinct weapon TYPE; quantity lives in `WeaponStats.ammo` (rounds), NOT
  repeated slot entries.** So "5 nukes" = ONE secondary slot, nuke type, `ammo=5`. Sample criminal
  secondaries **distinct-by-name, WITHOUT replacement** (unlike primaries/turrets which allow
  duplicate slot entries as counted copies). Loop `range(ship.max_secondaries)`.
- **Subtype-aware candidate pool (CRITICAL — avoids the deferred-subtype trap).** Do NOT call bare
  `find_item_tl(item_type="secondary_weapon")` then filter — a TL populated only by deferred/zero-dmg
  items yields an empty pool with no fallback. Instead build the pool directly:
  1. Gather secondaries across a TL window (preferred `item_tl = max(1, tech_level-1)`, widen down to
     `MIN_TECH_LEVEL` then up by the existing criminal max-gear-upgrade amount — mirror `find_item_tl`'s
     bidirectional intent).
  2. Exclude `DEFERRED_SECONDARY_SUBTYPES` (emp-bomb/mine/sentry-gun). Reuse/extract the inner-
     `extra_atts` subtype unwrap from `shop_service` (`_sw_subtype`) — single-source it (shared util),
     don't duplicate the unwrap logic.
  3. Exclude dead-weight by damage (knob #3).
  4. Sample `min(ship.max_secondaries, len(distinct_pool))` distinct names.
- Empty pool (e.g. `max_secondaries=0`, or only deferred items at that TL) → `secondaries=[]`
  (graceful; identical to today's behavior). Floor every weapon's rounds at 1 so it always fires once.

## Combat-field persistence (mirror CI-1 primary fix + CI-16 ammo)
Add `_extract_secondary_combat_fields(item)` in `bounty_service.py` (the existing
`_extract_weapon_combat_fields` is insufficient — it omits burst/emp/magnitude/steerable and reads
`damage_per_shot` whereas secondaries carry `damage`). Each generated `secondaries[]` entry carries:
`name, emoji, value, dps, damage (from item.damage column NOT dps), loading_speed_ms, range_m,
subtype, burst_count, emp_damage, magnitude_m, steerable, rounds`.

In `loadout_builder.from_criminal_ship`, add a secondaries read loop mirroring the player block:
for each `criminal_ship.get("secondaries", [])` build
`WeaponStats(name, dps, damage_per_shot=float(damage), loading_speed_ms, range_m, subtype,
burst_count, emp_damage, magnitude_m, steerable, ammo=rounds)`; append to `secondary_weapons`; pass
`secondary_weapons=secondary_weapons` into the returned `ShipLoadout` (currently omitted → defaults
empty). Update the docstring example dict. Resolver consumes with ZERO changes.

## File checklist
- `services/bot-core/src/services/bounty_service.py` — `_extract_secondary_combat_fields` helper;
  secondary-generation block in `generate_loadout` (after turrets, before value calc); add
  `"secondaries": [...]` + `"ship_max_secondaries"` to the return dict; fold secondary value into
  `total_value` per knob #4.
- `services/bot-core/src/services/loadout_builder.py` — secondaries read loop in `from_criminal_ship`;
  pass `secondary_weapons=`; update docstring.
- `services/bot-core/src/services/game_constants.py` — `CRIMINAL_SECONDARY_ROUNDS` map (knobs #1/#2),
  zero-damage exclusion threshold (knob #3).
- `services/bot-core/src/services/shop_service.py` — factor `_sw_subtype` into a shared util (single-
  source the inner-`extra_atts` subtype unwrap) OR import it where generation needs it.
- **NO** `combat_service.py` / `combat_models.py` / migration / schema changes.

## Test plan (bot-core; tee to log, grep; ≤2 mocks, real objects)
- `test_bounty_service.py`: for TLs 1/3/4/5/9, generated `secondaries` length ≤ `ship.max_secondaries`,
  distinct names, NO deferred subtypes, NO `damage<=threshold` items, every entry has all combat
  fields + `rounds>=1`; nuke rounds == configured cap.
- Edge: `max_secondaries=0` → empty; a TL whose only secondaries are deferred/zero-dmg → empty (no
  crash, no fallback to junk); TL0 fixed Betty path unchanged.
- `test_loadout_builder.py`: `from_criminal_ship` round-trips a secondaries dict → `ShipLoadout.
  secondary_weapons` with correct `damage_per_shot` (from `damage`), `subtype`, `ammo`, burst/emp/
  magnitude/steerable.
- Integration: a criminal with nuke rounds=N fires ≤N times in a resolved fight (CI-16 decrement);
  `secondary_depleted` emitted at 0; criminal's secondary deals damage (raw_dps reflects it).
- Regression: full bot-core suite green (currently 4127+); CI-1 primary/turret combat-field tests
  still pass; CI-16 conservation tests untouched.

## Risk register (architect)
| Risk | Mitigation |
|---|---|
| Deferred/zero-dmg-only TL → empty pool / crash | subtype-aware multi-TL pool; empty → `[]` graceful |
| Nuke alpha-strike unwinnable | nuke rounds floor = 1 (knob #1) |
| Duplicate secondary names break slot=type model / double-fire | sample WITHOUT replacement |
| Ship `max_secondaries=0` | `range(0)` no-op |
| `rounds=0` never fires | floor at 1 |
| Conservation | N/A — criminals have no cargo/sidecar; generation builds a dict, never touches equip paths |
| Preflight sim inherits secondaries | desired (better sims); preflight-never-persists + NPC user_id=None keep it safe |
