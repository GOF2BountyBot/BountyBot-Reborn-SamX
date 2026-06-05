# Combat Phase-1 — End-to-End Test Plan

*Manual vetting script for the Combat-System Phase-1 work (T1–T11), the reseeded
game data, and a smoke-test sweep of related-but-unmodified features. Every step
is written as a **Discord slash command** to run in the dev server
(`guild_id 699744305274945650`).*

> **How validation works:** after each scenario, the **Expected** line says what
> the bot embed should show. Where a result is hard to eyeball (combat-log row
> written, dropped DB columns, enriched `about` fields), there's a
> **▶ API/DB check** note — those are the points where you'll ask me to hit the
> discord-gateway / bot-core APIs (or query the dev DB) to confirm.
>
> Mark each box: `[ ]` → `[x]` pass, or `[!]` fail (jot the symptom).

---

## 0. Pre-flight & environment

Confirm the stack is the freshly-rebuilt one carrying the committed T11 fix +
migration 0012, then seed the data.

- [ ] **0.1** Stack is healthy — `bountydev-bot-core`, `bountydev-discord-gateway`,
  `bountydev-blender`, `bountydev-db` all `healthy`.
  *(If not freshly rebuilt since the last combat commits, rebuild first —
  `sudo docker compose --env-file .env.dev -f docker-compose-gpu.yml up -d --build --force-recreate`.)*
- [ ] **0.2** Seed data loaded for every combat category.
  ▶ **API/DB check:** `POST /api/v1/data/{module,primary_weapon,secondary_weapon,turret_weapon,ship,criminal,system,commodity}`
  from inside the container network, then spot-check the `weapon`/`item` tables.
- [ ] **0.3** Migration `0012` applied — `duel_variance_percent` and
  `bounty_pvc_armour_buff_factor` columns no longer exist on the guild-config table.
  ▶ **DB check:** describe the guild-config table; confirm both columns are gone.
- [ ] **0.4** `/health` → API reports healthy. `/ping` → returns latency.
- [ ] **0.5** Bot initialised for the guild — `/admin_setup admin_role:<@role> starting_credits:100000`
  (only if this is a fresh guild). Otherwise skip.

**Test accounts:** you'll want **two registered players** for the duel scenarios.
Call them **PILOT-A** (you) and **PILOT-B** (alt / second member).

- [ ] **0.6** `/profile` as PILOT-A (auto-registers). Repeat as PILOT-B.
- [ ] **0.7** Give both pilots a working-capital float and a ship to fly:
  `/admin_player user:@PILOT-A action:Set Credits credits:500000` (repeat for B).

---

## 1. Reseed-data integrity — the `/about` & `/list_category` enrichment (T11)

Goal: confirm the reseeded combat fields surface correctly in the
`about`-schema enrichment, especially the fields added in T11.

### 1.1 Primary weapons + PrimaryWeaponMod display
- [ ] **1.1.1** `/about category:Primary Weapon name:Berger Focus I`
  → **Expected:** DPS, damage, fire rate shown; values match seed.
- [ ] **1.1.2** `/about category:Primary Weapon name:128MJ Railgun`
  → **Expected:** an `auto-cannon`; sensible DPS.
- [ ] **1.1.3** A PrimaryWeaponMod module via `/about category:Module name:<mod>`
  → **Expected:** shows **Damage modifier %**, **Fire rate modifier %**, and
  **Net DPS shift ×** (the T11 `damage_pct` / `fire_rate_pct` / `dps_multiplier`
  fields). ▶ confirm camelCase→snake_case `dpsMultiplier` mapping renders.

### 1.2 Turrets — fire rate + damage per shot (the reseed fix)
- [ ] **1.2.1** `/about category:Turret name:Berger AGT 20mm`
  → **Expected:** **damage per shot** and **fire rate / loading speed** present
  (this was the seed gap we backfilled from the GoF2 wiki). No "0"/missing.
- [ ] **1.2.2** `/about category:Turret name:Hammerhead D1` → same check.
- [ ] **1.2.3** `/about category:Turret name:PE Proton`
  → **Expected:** flagged as **plasma-collector** (a mining tool, not a weapon).
- [ ] **1.2.4** `/list_category category:Turret`
  → **Expected:** all 10 turrets listed; the 3 PE units read as collectors.

### 1.3 Secondary weapons — typed fields (EMP / burst / nuke)
- [ ] **1.3.1** `/about category:Secondary Weapon name:Shesha`
  → **Expected:** `cluster-missile`; **Burst count** + **total-on-full-hit** shown.
- [ ] **1.3.2** `/about category:Secondary Weapon name:Liberator`
  → **Expected:** `nuke`; area/blast framing, guaranteed-hit.
- [ ] **1.3.3** `/about category:Secondary Weapon name:Shock Blast`
  → **Expected:** `shock-blast`; **no/zero damage**, distance-push described —
  and crucially **no bare "Damage: 0"** line (T11 pure-effect display fix).
- [ ] **1.3.4** `/about category:Secondary Weapon name:Mamba EMP`
  → **Expected:** **EMP damage** field shown; pure-EMP must NOT render "Damage: 0".
- [ ] **1.3.5** `/about category:Secondary Weapon name:Edo`
  → **Expected:** `missile` (scanner-tracked); accuracy depends on scanner.

### 1.4 ⚠ Non-canonical secondary subtypes (resolver mapping check)
The seed contains subtypes **beyond the spec's five**: `emp-bomb`,
`ionizing-missile`, `mine`, `sentry-gun`. Vet how each is classified/handled.
- [ ] **1.4.1** `/about category:Secondary Weapon name:EMP GL I` (`emp-bomb`)
- [ ] **1.4.2** `/about category:Secondary Weapon name:Ion Lambda MK1` (`ionizing-missile`)
- [ ] **1.4.3** `/about category:Secondary Weapon name:AMR Saber` (`mine`)
- [ ] **1.4.4** `/about category:Secondary Weapon name:Berger SG-100` (`sentry-gun`)
  → **Expected (all):** render without error; either map to a canonical behaviour
  or degrade gracefully. **Flag any that error, show blank stats, or silently
  no-op in combat** — these are the prime suspects for a coverage gap.

### 1.5 Ships, systems, criminals
- [ ] **1.5.1** `/about category:Ship name:<a seeded ship>` → hull/shield/slots shown.
- [ ] **1.5.2** `/list_category category:Ship tech_level:1` → tech-level filter works.
- [ ] **1.5.3** `/about category:System name:<a seeded system>` → renders.

---

## 2. PvC combat — bounty hunting (the core resolver path)

This is the main combat code path: `/check` resolves a player-vs-criminal fight
through the `TickResolver` and returns the after-action report.

### 2.1 Setup
- [ ] **2.1.1** `/admin_spawn_bounty tier:Bronze quantity:3` → bounties created.
- [ ] **2.1.2** `/bounties` → lists active bounties for your tier.
- [ ] **2.1.3** `/criminal-loadout bounty:<id>` → shows the criminal's ship + weapons.
- [ ] **2.1.4** `/route bounty:<id>` → shows the route; note a **target system**.

### 2.2 Resolve a fight & read the after-action report
- [ ] **2.2.1** `/check system:<target system>` → combat resolves, embed returned.
  → **Expected after-action report contains:**
  - Outcome (win / loss / stalemate) + reason (hull depleted / time cap).
  - **Shield → Armour → Hull** progression reflected in damage taken.
  - **Shots fired / landed + accuracy %** per side.
  - **Total damage dealt / taken** per side.
  - **🛡️ PvC damage reduction: ~33% active** line (the bountyCog line we added).
  - Any modules that fired, with counts.
  - Remaining hull on the survivor.
- [ ] **2.2.2** Overkill sanity — on a decisive kill, **damage_dealt in the summary
  should exclude overkill** (the `absorbed` exclusion). Final blow shouldn't inflate
  total damage by the full last-hit amount.
  ▶ **API/DB check:** confirm a `combat_log` row was written and `combat_log_id`
  is set on the result; inspect the serialized summary/metadata.
- [ ] **2.2.3** Cooldown — immediately re-run `/check` → **Expected:** rejected with
  a cooldown message. Then `/admin_cooldown_reset user:@PILOT-A` clears it.
- [ ] **2.2.4** Win path effects: credits awarded, XP gained, stats incremented.
  ▶ verify via `/profile` (kills, credits) before/after.

### 2.3 Distance & range behaviour
- [ ] **2.3.1** Fight a criminal whose loadout is **short-range only** vs. one with
  **long-range** weapons → **Expected:** opening ticks show misses/no-fire until
  ships close; range genuinely gates early damage.

---

## 3. PvP combat — duels (even-match path, no PvC handicap)

### 3.1 Challenge lifecycle (related-but-unmodified plumbing)
- [ ] **3.1.1** PILOT-A: `/duel-challenge target:@PILOT-B stakes:1000`
  → **Expected:** pending challenge created; stakes escrow noted.
- [ ] **3.1.2** PILOT-B: `/duel-reject duel:<pick>` → challenge cleared, stakes released.
- [ ] **3.1.3** PILOT-A: re-challenge, then `/duel-cancel duel:<pick>` → cleared.
- [ ] **3.1.4** PILOT-A: re-challenge; **PILOT-B:** `/duel-accept duel:<pick>`
  → fight resolves.

### 3.2 Duel after-action report
- [ ] **3.2.1** On the resolved duel → **Expected:**
  - Full after-action stats as in 2.2.1, **but NO PvC damage-reduction line**
    (duels are an even match — handicap must be absent).
  - Stakes transferred to the winner; both players' duel W/L + credit stats update.
  ▶ **API/DB check:** `combat_log` row written with `guild_id` set; confirm
  `pvc_damage_reduction: 0.0` in the metadata.
- [ ] **3.2.2** Determinism — variance was removed in T10. Re-running an identical
  matchup should give a **stable** outcome (no `variance_percent` swing).
  ▶ confirm no `variance_percent` field anywhere in the result/log.

---

## 4. Weapon-class scenarios (equip → fight → confirm behaviour)

For each, equip the weapon on PILOT-A's active ship, then fight (duel vs a
known-weak PILOT-B loadout, or `/check` a soft Bronze criminal) and read the report.

**Equip helpers:** `/admin_give_item user:@PILOT-A item_name:<item> quantity:1`
then `/equip item_name:<item>`. Verify with `/loadout` / `/ships`.

### 4.1 Primary weapons + PrimaryWeaponMod (T5)
- [ ] **4.1.1** Equip `Berger Focus I`; fight → primaries fire on the reload timer;
  accuracy applied; damage lands.
- [ ] **4.1.2** Add a **PrimaryWeaponMod**; fight → DPS shifts per the mod's
  damage/fire-rate %. Compare report DPS vs. 4.1.1.
- [ ] **4.1.3** Equip **two** PrimaryWeaponMods (if allowed) → **Expected:** the
  multi-equip WARN fires once (pre-loop), only one applies per spec.

### 4.2 Turrets (T7)
- [ ] **4.2.1** Equip `Berger AGT 20mm` (auto) → fires autonomously each cycle;
  report labels subtype **`auto`** (not "auto-turret").
- [ ] **4.2.2** Equip a **manual** turret (e.g. `Matador TS`) → while manning,
  primaries fall silent; subtype labelled **`manual`**.
- [ ] **4.2.3** Equip `PE Proton` (plasma-collector) → **Expected:** does **nothing**
  in combat (mining tool); no damage attributed.
- [ ] **4.2.4** Confirm turret **damage_per_shot + fire rate** actually drive damage
  (the reseed fix) — turret contribution is non-zero in the report.

### 4.3 Secondary weapons (T6)
- [ ] **4.3.1** **Rocket** (`Jet Rocket`) → unguided; closer = more reliable hits.
- [ ] **4.3.2** **Missile** (`Edo`) with a **basic scanner** equipped → must aim;
  then swap to a **better scanner** (`Hiroto Ultrascan`) → tracking/lock improves
  hit rate. ▶ scanner-tier resolution is the thing under test.
- [ ] **4.3.3** **Cluster-missile** (`Shesha`) → fires a **burst**; sub-munitions roll
  to hit independently; close range = most land.
- [ ] **4.3.4** **Nuke** (`Liberator`) at **long range** → guaranteed area blast;
  **both ships** take distance-scaled damage. Then test at **point-blank** →
  **Expected:** PILOT-A takes self-damage too.
- [ ] **4.3.5** **Shock-blast** (`Shock Blast`) → **0 damage**; enemy shoved to long
  range (distance resets). Report shows distance change, no damage line.
- [ ] **4.3.6** **Pure-EMP secondary** (`Mamba EMP`) → disrupts; report must **not**
  show a misleading "Damage: 0" (T11 `is_pure_emp` gate).

### 4.4 Critical: the 5 secondaries must actually FIRE (T6 blocker regression)
- [ ] **4.4.1** Sanity that secondaries fire **at all** in production — the original
  T6 bug was the builder reading flat instead of nested `extra_atts`, so secondaries
  silently never fired. **Confirm at least one secondary appears in the combat log
  as having fired.** ▶ inspect the combat-log events for a secondary-fire entry.

---

## 5. Modules / gadgets (T8 + T9)

Equip each defensive module on PILOT-A, fight a drawn-out match so HP thresholds
are crossed, and confirm the gadget triggers + shows in the report.

- [ ] **5.1 Cloak** (e.g. `Yin Co. Shadow Ninja`) → for its window the enemy's
  hit chance collapses to near-zero (REPLACE-accuracy behaviour).
- [ ] **5.2 Booster** (e.g. `Linear Boost`) → pushes distance out **and** debuffs
  the attacker's chance to hit while active. *(Booster = defender-side; do not
  confuse with thruster.)*
- [ ] **5.3 Thruster** → sharpens **primaries only** (not turrets); attacker-side
  accuracy bonus. Confirm turret accuracy is unaffected.
- [ ] **5.4 Emergency System** → at the HP threshold, a one-shot **near-invuln
  window** kicks in; fires once. Report notes the activation.
- [ ] **5.5 Repair bot** (`Ketar Repair Bot`) → steady hull/health regen ticks over
  the fight.
- [ ] **5.6 Shield regen** (`Gamma Shield I` / `Beamshield II`) → shield recovers
  during a drawn-out fight.
- [ ] **5.7** Cooldown/off-by-one (T8 fix) → a module's loading/cooldown shouldn't
  let it fire on the same tick it was set; cadence matches its `loading_speed_ms`.

---

## 6. Edge & boundary cases

- [ ] **6.1 Stalemate** — pit two tanky, low-DPS loadouts → fight hits the **~3-min
  time cap** with neither destroyed → outcome `stalemate`, reason `time_cap`.
- [ ] **6.2 Instant-ish kill** — overwhelming loadout vs. a fragile target → ends in
  few ticks; overkill excluded from damage totals (cross-check 2.2.2).
- [ ] **6.3 Nuke mutual-destruction** — point-blank nuke that kills both → resolver
  picks a deterministic outcome without erroring.
- [ ] **6.4 Empty/again** — `/check` with no bounty in the system → graceful
  "no target" message, no crash.
- [ ] **6.5 No-weapon ship** — strip all weapons (`/unequip item_name:all`) then
  fight → resolver handles a zero-DPS combatant (likely stalemate/loss), no divide-by-zero.

---

## 7. Smoke tests — related-but-unmodified features

Quick "still works" sweep of everything around combat that we *didn't* change but
that shares plumbing (economy, inventory, ships, progression).

### 7.1 Economy & shop
- [ ] **7.1.1** `/shop` → browse tier shop. `/shop item_type:module` → filter works.
- [ ] **7.1.2** `/shops` → all-tier summary.
- [ ] **7.1.3** `/buy item_id:<id> quantity:1` → purchase; credits debited.
- [ ] **7.1.4** `/sell item:<pick> quantity:1` → sell back; credits credited.
- [ ] **7.1.5** `/admin_refresh_shop` → shop re-rolls.

### 7.2 Inventory & equipment
- [ ] **7.2.1** `/inventory` and `/inventory item_type:Primary Weapon` → list + filter.
- [ ] **7.2.2** `/search query:Berger` → matches by name.
- [ ] **7.2.3** `/item item_name:<item>` → item detail.
- [ ] **7.2.4** `/equip` then `/unequip item_name:<item>` round-trips into inventory.
- [ ] **7.2.5** `/unequip item_name:all` → strips everything to inventory.

### 7.3 Ships
- [ ] **7.3.1** `/ships` → owned ships + loadouts. `/ship ship_id:<id>` → detail.
- [ ] **7.3.2** `/setactive ship_id:<id>` → swaps active ship.
- [ ] **7.3.3** `/nickname ship_id:<id> nickname:Test Rig` → renames.
- [ ] **7.3.4** `/loadout` → active-ship loadout view (combat-relevant — verify the
  PvC-DR / armour line change didn't break rendering).

### 7.4 Player progression & social
- [ ] **7.4.1** `/profile` → stats render (incl. updated combat/duel counters).
- [ ] **7.4.2** `/leaderboard` → ranked list.
- [ ] **7.4.3** `/promote` / `/demote` → tier change (use `/admin_player ... reset_tier_cooldown` if gated).
- [ ] **7.4.4** `/prestige` → Platinum-only gate behaves (rejects if not eligible).
- [ ] **7.4.5** `/give type:Credits target:@PILOT-B amount:500` → transfer works.
- [ ] **7.4.6** `/give type:Item target:@PILOT-B item:<pick>` → item transfer.
- [ ] **7.4.7** `/notifications` → preference toggles persist.
- [ ] **7.4.8** `/unregister` then `/register` → role round-trip (data retained).

### 7.5 Navigation & help
- [ ] **7.5.1** `/make-route start:<sys> end:<sys>` → shortest route.
- [ ] **7.5.2** `/help` and `/admin_help` → command lists render.

### 7.6 Admin / data tooling
- [ ] **7.6.1** `/admin_give_ship user:@PILOT-A ship:<ship>` → ship granted.
- [ ] **7.6.2** `/admin_remove_item` / `/admin_remove_ship` → reversible.
- [ ] **7.6.3** `/admin_guild_stats` → guild aggregate stats.
- [ ] **7.6.4** `/admin_config action:View` and `/admin_config_bounty action:View`
  → **Expected:** render cleanly with the **retired fields gone** (no
  `duel_variance_percent` / `bounty_pvc_armour_buff_factor` in the output — the
  adminCog field-list change from T10).
- [ ] **7.6.5** `/admin_config_validate` → no errors against current schema.
- [ ] **7.6.6** `/load_data` (dev) and `/reload_autocomplete` → data reloads;
  autocomplete refreshes. `/list_category` reflects reseeded values.
- [ ] **7.6.7** `/admin_clear_bounties` → clears active bounties.

---

## 8. Persistence & data-layer verification (API/DB)

The points where you'll ask me to drive the discord-gateway / bot-core APIs or
query the dev DB directly:

- [ ] **8.1** Each PvC fight (`/check`) and PvP duel writes a **`combat_log`** row;
  `combat_log_id` is returned on the result.
- [ ] **8.2** The serialized log/summary contains the new typed fields
  (per-tick events, per-weapon fire records, module activations, distance changes).
- [ ] **8.3** `pvc_damage_reduction` ≈ 0.33 for PvC, `0.0` for PvP.
- [ ] **8.4** **No** `variance_percent` field persists anywhere (T10 removal).
- [ ] **8.5** Guild-config table has **no** `duel_variance_percent` /
  `bounty_pvc_armour_buff_factor` columns (migration 0012).
- [ ] **8.6** `GET /api/v1/data/categories` lists all 8 categories; a spot
  `POST /api/v1/data/turret_weapon` re-upsert is idempotent and turret rows carry
  `extra_atts->'extra_atts'->>'damage_per_shot'`.

---

## Result log

| Section | Pass | Fail | Notes |
|---|---|---|---|
| 0 Pre-flight | ⏳ | | not yet run |
| 1 Reseed data / about | ⏳ | | not yet run |
| 2 PvC bounty combat | ⏳ | | not yet run |
| 3 PvP duels | ⏳ | | not yet run |
| 4 Weapon classes | ⏳ | | not yet run |
| 5 Modules | ⏳ | | not yet run |
| 6 Edge cases | ⏳ | | not yet run |
| 7 Smoke (unmodified) | ⏳ | | not yet run |
| 8 Persistence / DB | ⏳ | | not yet run |

---

*Spec reference: `COMBAT_SPEC_LOCKED.md`. Player-facing overview: `COMBAT.md`.
Reconciliation notes: `COMBAT_RECONCILIATION.md`. Audit: `AUDIT_REPORT.md`.*
