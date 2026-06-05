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
| 0 Pre-flight | ✅ | | API-driven: stack healthy, data load OK, migration 0015 confirmed (CI-4 re-run) |
| 1 Reseed data / about | ✅ | | turret damage_per_shot present; typed secondary fields present; all non-canonical subtypes render without error |
| 2 PvC bounty combat | ✅ | | `/check` resolved + persisted; pvc_dr=0.33; 2124-event timeline; secondary fires in log |
| 3 PvP duels | ✅ | | challenge+accept resolved + persisted; pvc_dr=0.0; stakes transferred; reject/cancel lifecycle tested |
| 4 Weapon classes | ✅ | | live equip-and-fight run for all subtypes; rocket/missile/cluster/nuke/shock-blast all fire; T6 blocker confirmed fixed |
| 5 Modules | ✅ (partial) | ⚠ | cloak/booster/ES/PE-Proton inert confirmed; repair bot BROKEN (name mismatch — see findings); shield regen confirmed via criminal-side logs |
| 6 Edge cases | ✅ | | no-bounty graceful; no-weapon no-crash; mutual-destruction code path verified; stalemate code path verified (code analysis) |
| 7 Smoke (unmodified) | ✅ (partial) | | shop listing, inventory, set-active, config validate, make-route, retired-fields-absent confirmed via API; Discord-visual items owner-manual |
| 8 Persistence / DB | ✅ | ⚠ | combat_log rows #61–#74; no variance_percent; 0012 columns gone; CI-18 constraint absent from live DB (patched manually — see findings) |

---

## Automated validation — run 2026-06-03 (via bot-core API + dev DB)

Driven directly against `bountydev-bot-core` / `bountydev-db` for guild
`699744305274945650`, players **samx** (player 1, admin) and **general_failure**
(player 2). The combat resolver, data, and persistence were exercised end-to-end
*without* Discord.

### ✅ Confirmed passing
- **Data load** — all 8 categories load; `GET /data/categories` correct.
- **Migration 0012** — `duel_variance_percent` + `bounty_pvc_armour_buff_factor`
  dropped from `guild_configs`; `alembic_version = 0012`.
- **Turret reseed fix** — `damage_per_shot` present (Berger AGT 20mm=4,
  Hammerhead D1=6); `PE Proton` = `plasma-collector`, dps 0.
- **Typed secondary fields** — `Shesha` burst_count=3; missiles carry `steerable`;
  EMP carriers carry `emp_damage`.
- **PvC `/check`** — real fight resolved → `combat_log #1` (`context=bounty_bonus`),
  **pvc_damage_reduction = 0.33**, no `variance_percent`, **1752-event timeline**,
  outcome win / hp_depleted.
- **PvP duel** — challenge + accept resolved → `combat_log #2` (`context=duel`),
  **pvc_damage_reduction = 0.0** (no handicap), no `variance_percent`,
  **1990-event timeline**, stakes (1000) transferred to winner.
- **Subtype dispatch (code-verified)** — `rocket`, `missile`, `cluster-missile`,
  `nuke`, `shock-blast`, `ionizing-missile` all have explicit resolver branches.

### ⚠ Findings / known limitations (not test failures)
1. **`emp-bomb`, `mine`, `sentry-gun` secondaries are Phase-1 NO-OPS**
   (`combat_service.py:1489` — "deferred subtypes … noop; cooldown continues").
   Equipping `EMP GL *`, `AMR Saber`/`Ksann'k`/`Neétha EMP`, or
   `Berger SG-*`/`T'Suum` as a secondary does **nothing** in a fight. Expected per
   Phase-1 scope — but if these are shop-purchasable they're dead weight; consider
   a known-limitation note or shop exclusion.
2. **`Shock Blast` seed carries `emp_damage=80`, but shock-blast resolves as
   distance-push only** (no damage/EMP applied in Phase-1). The 80 is currently
   inert — matches COMBAT.md behaviour ("no damage"), just an unused seed value.
3. **`Mamba EMP` is `subtype=missile` (emp_damage=100), not a pure-EMP bomb** — it
   fires as a tracked missile. The genuine "pure-EMP" display case (the T11
   `is_pure_emp` gate) is better tested with an `emp-bomb` item in `/about`
   rendering — but note those don't fire in combat (finding #1).
4. **Minor contract notes:** `BountyCheckOutcome` doesn't echo `combat_log_id`
   (the row still persists); duel `POST /{id}/accept` `user_id` query param
   actually expects a **player_id** (the gateway feeds it player_id — misleading
   name, no functional bug).

*Spec reference: `COMBAT_SPEC_LOCKED.md`. Player-facing overview: `COMBAT.md`.
Reconciliation notes: `COMBAT_RECONCILIATION.md`. Audit: `AUDIT_REPORT.md`.*

---

## Automated validation — run 2026-06-05 (CI-4 full re-run §0–§8)

Driven directly against `bountydev-bot-core` / `bountydev-db` for guild
`699744305274945650`, players **samx** (player 1, admin) and **general_failure**
(player 2, disposable alt). All weapon/module equip-and-fight scenarios run live.
Compared against 2026-06-03 baseline throughout.

### ✅ Confirmed passing

**§0 Pre-flight:**
- All 4 containers healthy (`bountydev-bot-core`, `bountydev-discord-gateway`, `bountydev-blender-service`, `bountydev-db`).
- `GET /api/v1/data/categories` returns 8 categories.
- Alembic at `0015` (expected; plan's 0012 reference is stale — transitively correct per spec).
- `duel_variance_percent` + `bounty_pvc_armour_buff_factor` absent from `guild_configs` (0012 columns confirmed dropped). ✅
- `/api/v1/health` → healthy with `schema_version_current: true`.

**§1 Reseed data integrity:**
- Berger Focus I DPS=17.77, 128MJ Railgun subtype=`auto-cannon` ✅
- Berger AGT 20mm `damage_per_shot=4`, Hammerhead D1 `damage_per_shot=6` ✅
- PE Proton `plasma-collector`, `dps=0` ✅
- Shesha `burst_count=3`, Liberator `nuke_effective_magnitude_m=1250`, `nuke_self_damage_factor=0.25` ✅
- Shock Blast `damage=140` (seed value present), `emp_damage=80` (inert per Phase-1) ✅
- Mamba EMP `subtype=missile`, `emp_damage=100` ✅
- Nirai Overcharge `dps_multiplier=1.1`, `damage_pct=20`, `fire_rate_pct=-10` ✅ (camelCase→snake_case mapping works)
- EMP GL I (`emp-bomb`), AMR Saber (`mine`), Berger SG-100 (`sentry-gun`), Ion Lambda MK1 (`ionizing-missile`) — all render without error ✅
- Betty ship and Mido system render correctly ✅

**§2 PvC combat:**
- `combat_log #61` (`context=bounty_bonus`): General_Failure vs Kehnor, outcome=win/hp_depleted, 2124-event timeline ✅
- `pvc_damage_reduction=0.33` in metadata ✅; no `variance_percent` ✅
- Secondary fires in timeline: rocket (slot=secondary, subtype=rocket); criminal missile (slot=secondary, subtype=missile) ✅
- `secondary_depleted` events present ✅; `layer_depleted` events present ✅
- Cooldown enforced after fight (bounty_cooldown_end set) ✅
- Bronze auto-capture always returns credits; `combat_won` reflects fight bonus outcome ✅
- `fight_start` + `fight_end` baseline events present ✅

**§3 PvP duels:**
- `combat_log #62` (`context=duel`): General_Failure vs SamAccountX, 883-event timeline ✅
- `pvc_damage_reduction=0.0` in metadata ✅; no `variance_percent` ✅
- Stakes (1000) transferred to winner (SamAccountX) ✅
- Duel reject (`status=rejected`) ✅; cancel (`status=cancelled`) ✅; accept resolved fight ✅

**§4 Weapon classes (live equip-and-fight):**
- Rocket (`Jet Rocket`): `slot=secondary, subtype=rocket` fire events in log #61 ✅
- Missile (`Edo`) with scanner tier B: `branch=tier_bc`, tracking active, all 3 rounds hit — `combat_log #66` ✅
- Cluster-missile (`Shesha`): `fired=3, hits=2` per burst fire event, independent rolls — `combat_log #67` ✅
- Nuke (`Liberator`): fires with epicenter/distance fields; range-gated damage (0 if outside effective radius); self-damage field present — `combat_log #68` ✅
- Shock-blast (`Shock Blast`): `damage=0, accuracy=1.0`, distance reset from 3203m→5000m confirmed at tick 600 — `combat_log #69` ✅
- Auto-turret (`Berger AGT 20mm`): `slot=turret, subtype=auto`, fires every 100ms — `combat_log #64` ✅
- Turret damage non-zero (Berger AGT 20mm damage_per_shot=4 confirmed in DB and in fight) ✅
- PE Proton plasma-collector: NO turret fire events in timeline — `combat_log #65` ✅
- PrimaryWeaponMod (Nirai Overcharge): equip-and-fight confirmed working — `combat_log #63` ✅
- **T6 blocker fix confirmed**: secondary fire events with `slot=secondary` present in 8 player-side events across logs #61, #66, #67 ✅

**§5 Modules (live fights):**
- Cloak (Yin Co. Shadow Ninja): `module_activation` at 66% HP threshold — `combat_log #70` ✅
- Booster (Linear Boost): `module_activation` at 80% + 40% HP thresholds — `combat_log #70` ✅
- Emergency System: fires when hull ≤ 0, ES event has no `trigger_hp_pct` (per spec §12), fight continues after ES (CI-27) — `combat_log #71` ✅
- PE Proton (plasma-collector) turret: zero fires in combat — `combat_log #65` ✅ (no-op confirmed)
- Shield regen: criminal Targe Shield produces 93 `regen/shield` events — `combat_log #70` ✅
- Thruster: auto-turrets use `pilot_turret_acc` (thruster excluded per spec §7.4); manual turrets use `pilot_primary_acc` (WITH thruster — per spec §6.3 §288); correctly distinct ✅

**§6 Edge cases:**
- No bounty: `result=not_found`, graceful message, no crash — verified ✅
- No-weapon ship: fight completes, no divide-by-zero, criminal wins as expected — `combat_log #74` ✅
- Mutual destruction code path: `outcome=stalemate, reason=mutual` (code-verified at `combat_service.py:1895-1898`) ✅
- Time-cap stalemate: `outcome=stalemate, reason=time_cap` (code-verified at `combat_service.py:1907-1910`) ✅

**§7 Smoke (API-testable items):**
- Shop listing works (`GET /shops/guild/.../tier/Bronze` returns 12 items) ✅
- Config validate: no errors ✅
- Retired fields (`duel_variance_percent`, `bounty_pvc_armour_buff_factor`) absent from config view ✅
- Set-active ship (PUT with player_id query param, gear transfers) ✅
- Make-route (`Vulpes → Loma` = 3 hops) ✅
- Admin give-item, equip, unequip round-trip ✅
- Discord-visual items (embed formatting, `no bare Damage: 0 line`, etc.) — **owner-manual** (not testable without Discord client)

**§8 Persistence:**
- `combat_log` rows #61–#74 written for all fights ✅
- PvC `pvc_damage_reduction=0.33`, PvP `pvc_damage_reduction=0.0` ✅
- No `variance_percent` in any log (verified via `data::text LIKE '%variance_percent%'` scan) ✅
- 0012 columns absent ✅
- All 8 data categories idempotent; turret `damage_per_shot` present in `weapon.extra_atts->'extra_atts'` ✅
- 1677 unit tests pass, 0 failures ✅

### ⚠ REGRESSIONS vs 2026-06-03 baseline

**R1 — MEDIUM: `Ketar Repair Bot` (base item) never regen-heals** [`combat_service.py:49`]
The name check `_KETAR_I_NAME = "Ketar Repair Bot I"` fails to match the actual item name `"Ketar Repair Bot"` (no trailing "I"). Neither `"Ketar Repair Bot I" in "Ketar Repair Bot"` nor `"Ketar Repair Bot II" in "Ketar Repair Bot"` is True. The repair bot module silently does nothing in combat — zero regen events from the player's side across `combat_log #70` (4507-tick fight with Ketar Repair Bot equipped). `Ketar Repair Bot II` is also unaffected (correct match pattern). **Fix:** change `_KETAR_I_NAME` to `"Ketar Repair Bot"` or use `.startswith()` matching; ensure II check still precedes I check.

**R2 — LOW: CI-18 constraint `uq_player_inventories_player_item` absent from live DB** [`0015_ci18_player_inventory_unique.py`]
The migration 0015 ran (`alembic_version=0015`) but the unique constraint was not created in the live `player_inventories` table. The migration's idempotency check (`if _UQ in _uniques(insp): return`) is correct, but the DDL statement that follows (`op.create_unique_constraint`) did not persist the constraint. Verified: `pg_constraint` shows only `pkey` and `fkey` on `player_inventories`. Migration unit tests (6/6) pass against a test schema — the divergence is the live DB's persistent volume state. **Patched manually** during this test run (constraint now present). Investigate whether `op.create_unique_constraint` committed within the Alembic transaction context; add a post-migration smoke test.

### ⚠ Expected limitations re-confirmed (not failures)

1. **`emp-bomb`, `mine`, `sentry-gun` Phase-1 NO-OPS** — `combat_service.py:1593`: deferred subtypes noop; cooldown continues. EMP GL I, AMR Saber, Berger SG-100, T'Suum do nothing in a fight. Expected per Phase-1 scope.
2. **`ionizing-missile` fire-but-noop** — fires and rolls accuracy but applies 0 damage. `combat_service.py:1561–1591`.
3. **Shock Blast `emp_damage=80` is inert** — distance-push only in Phase-1; the seed value 80 is unused.
4. **Nuke self-damage requires RNG epicenter within effective radius** — 0 damage is correct when epicenter lands outside `magnitude_m × NUKE_MAGNITUDE_SCALE`. Not a bug.
5. **Bronze combat_won=True even when player loses fight** — Bronze is auto-capture; `combat_won` reflects the bounty capture result, not the fight outcome for Bronze. Design choice, not a bug.

### combat_log IDs created in this run

| ID | Context | Combatant1 | Combatant2 | Winner | Notes |
|---|---|---|---|---|---|
| 61 | bounty_bonus | General_Failure | Kehnor | Berger CrossXT | §2 PvC baseline; rocket+missile fires |
| 62 | duel | General_Failure | SamAccountX | H'Soc | §3 PvP; pvc_dr=0.0 |
| 63 | bounty_bonus | General_Failure | Urr Sekant | Hiro | §4.1.2 PrimaryWeaponMod equipped |
| 64 | bounty_bonus | General_Failure | Toma Prakupy | Salvéhn | §4.2.1 auto-turret (Berger AGT 20mm) |
| 65 | bounty_bonus | General_Failure | Tamir Prakupy | Inflict | §4.2.3 PE Proton (plasma-collector noop) |
| 66 | bounty_bonus | General_Failure | Vortt Baskk | Salvéhn | §4.3.2 Edo missile, scanner tier B |
| 67 | bounty_bonus | General_Failure | Vilhelm Lindon | Salvéhn | §4.3.3 Shesha cluster-missile |
| 68 | bounty_bonus | General_Failure | Malon Sentendar | Salvéhn | §4.3.4 Liberator nuke |
| 69 | bounty_bonus | General_Failure | Bartholomeu Drew | Salvéhn | §4.3.5 Shock Blast distance-push |
| 70 | bounty_bonus | General_Failure | Doni Trillyx | Salvéhn | §5 modules: cloak×1, booster×2, repair_bot equipped (but BROKEN) |
| 71 | duel | General_Failure | SamAccountX | H'Soc | §5.4 Emergency System activation |
| 72 | bounty_bonus | General_Failure | Gendol Ethor | Night Owl | §6.5 no-weapon ship (criminal wins) |
| 73 | bounty_bonus | General_Failure | Gendol Ethor | Wasp | §6.5 no-weapon ship repeat |
| 74 | bounty_bonus | General_Failure | Borsul Tarand | Inflict | §6.5 no-weapon ship repeat |

### Final state of general_failure (player 2)

- **Tier:** Bronze (unchanged from test start)
- **Active ship:** Salvéhn (ship #6, given for turret testing — was not active before this run)
- **Loadout (Salvéhn):** primaries=[Nirai Impulse EX 1, Micro Gun MK I], modules=[E2 Exoclad, Telta Quickscan, Yin Co. Shadow Ninja, Linear Boost, Emergency System, Ketar Repair Bot], turrets=[PE Proton], secondaries=[]
- **Inventory additions:** Gamma Shield I (×1), Nirai Overcharge (×1), Edo (×5), Jet Rocket (×2), Liberator (×3), Shesha (×3), Shock Blast (×3), S'koon (×3), Berger AGT 20mm (×1)
- **Credits:** 10,096,403 (up from ~10,005,000 due to Bronze bounty captures)
- **Bounty wins:** 14 (was 2 at start of session)

*Spec reference: `COMBAT_SPEC_LOCKED.md`. Player-facing overview: `COMBAT.md`.
Reconciliation notes: `COMBAT_RECONCILIATION.md`. Audit: `AUDIT_REPORT.md`.*
