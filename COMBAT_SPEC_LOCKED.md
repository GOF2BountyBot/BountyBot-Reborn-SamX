# Combat System Specification (LOCKED)

> Canonical, locked-in design for the BountyBot Phase-1 combat system. Items
> here have been explicitly confirmed and are not subject to ambiguity.
> Configurable knobs include their default values.
>
> **For decision history, open questions, and rationale, see
> [`COMBAT_REWRITE_JOURNAL.md`](./COMBAT_REWRITE_JOURNAL.md).** The journal is
> the working / WIP space; this file is the destination for promoted decisions.

---

## 0. Meta

### 0.1 Configuration policy
Every numeric in this spec (rates, percentages, thresholds, durations, distances, magnitudes) is a **starting default**. All defaults land in
`services/bot-core/src/services/game_constants.py` as `GameConstants.<NAME>` with:
- `BOUNTYBOT_<NAME>` environment-variable override, and
- per-guild override (matching the existing pattern for `DUEL_VARIANCE_PERCENT`, `BOUNTY_PVC_ARMOUR_BUFF_FACTOR`).

Tuning post-Phase-1 must not require code changes.

### 0.2 Resource policy
**Energy is assumed infinite — for combat and any other gameplay surface that might check energy.** Energy cells are not tracked. Wiki lore references to "energy cell consumption per use" (e.g. U'tool cloak) are cosmetic only; the resolver does not gate on energy. Applies to player AND criminal/NPC combatants.

---

## 1. Tick & timing

- **Tick = 10 ms, fixed.**
- **Hard cap: 18,000 ticks per fight (3 simulated minutes).**
- **Per-weapon cooldown:** each weapon holds `cooldown_remaining_ms`; per tick decrement by 10; fires when `≤ 0` AND in-range AND not gated; resets to `loading_speed_ms`.
- All wiki `loading_speed_ms` values are clean multiples of 10 ms — no accumulator carry, no drift.

---

## 2. Distance model

| Quantity | Value |
|---|---|
| Starting distance | **5000 m** |
| Base ship speed (both combatants) | **150 m/s** (pinned, same both sides) |
| Passive closure (relative) | **300 m/s** (both sides approaching at 150 m/s) |
| Minimum-distance floor | **300 m** |

- **Range gating:** a weapon fires only when `current_distance ≤ weapon.range_m`. Outside its range it cannot fire at all (binary gate; no in-range degradation for primaries — see §5/§6.1).
- **Booster distance push** (while a booster is active on this ship):
  ```
  distance_gained_m = base_speed × (effect_pct / 100) × (duration_ms / 1000)
  ```
  During the boost window, passive closure is suspended; the booster's outward velocity dominates. After expiry, normal closure resumes.
- **Shock-blast distance reset:** instantly resets both ships to the starting distance (5000 m). 100% guaranteed (no accuracy roll). No damage. Fires on cooldown (`loading_speed_ms`); no per-fight cap; no HP-threshold gating. (Phase-1 weapons resolve same-tick, so in-flight projectile interaction is moot.)

---

## 3. HP layers + damage stacking + regen

### Layers (three per combatant)
| Layer | Source |
|---|---|
| Shield | sum of equipped `ShieldModule` HP |
| Armour | sum of equipped `ArmourModule` HP |
| Hull | `ship.armour` column (intrinsic to the Ship row — NOT a separate module type) |

- **Damage stacking order (per hit):** shield → armour → hull.
- **Starting HP (Phase-1):** every combatant starts at max on every layer. Damaged-start support is Phase-2 (schema hooks already exist on `Player.current_*` and `Bounty.criminal_current_*`).
- **HP is integer; per-tick HP delta is integer.**

### Shield regen (per shield module)
- **Continuous per-tick recharge** via integer schedule:
  ```
  +1 HP every N ticks   where  N = ceil(shield_recharge_ms / shield_capacity / tick_ms)
  ```
  Example: Targe (50 cap / 20,000 ms recharge) → +1 HP every 40 ticks (= 400 ms).
- Recharge **does NOT pause after a hit** (no "interrupt window" in Phase-1).

### Repair Bot regen
- **Scope: hull + armour ONLY.** Repair bots do NOT touch the shield layer.
- **Fill order:** hull first, then armour (inverse of damage stacking).
- **Rate = percentage of max** of `(max_hull + max_armour)`:
  - Ketar Repair Bot I → **2.5 %/s**
  - Ketar Repair Bot II → **5.0 %/s**
- **Per-tick HP delta** = `(max_hull + max_armour) × rate × (tick_ms / 1000)`, accumulated with the same `+1 HP every N ticks` integer-flush discretization as shield regen (pattern shared; layers disjoint).
- Seed `extra_atts.HPps` values (7 / 15) are stale data — **ignored.**

### Tick ordering for regen vs damage
- **Regen pulses are applied BEFORE damage in each tick.** Finishing-blow detection is clean: HP ≤ 0 at end of tick = dead.
- **Concurrent regen:** shield regen and hull/armour regen run in parallel each tick (independent tracks).

---

## 4. Damage type model

- **Phase-1 damage type: physical ONLY.**
- **EMP is a separate damage type, deferred to Phase-2+** (mechanic + disable-window design parked).
- **Resolver rule:** every weapon participates in cooldown / firing / event-log. Each hit applies `damage_per_shot` (physical) only; any `emp_damage` field is ignored regardless of value.
- **Hybrid weapons** (`damage_per_shot > 0` AND `emp_damage > 0`, secondaries only): fire normally; apply ONLY the physical `damage_per_shot`. EMP component ignored.
- **Pure-EMP weapons** (`damage_per_shot` = 0 / absent, `emp_damage > 0`): fire normally, roll accuracy, log hit/miss, apply **0 HP delta**.
- **Pure-EMP equip policy:** equipping a pure-EMP weapon in Phase-1 is accepted as a player choice. No preflight warning. No loadout-build filter. The combat log surfaces the 0-damage outcome.
- **GammaShield is inert in Phase-1** (no radiation-damage source exists). Kept in `UNIQUE_EQUIP_TYPES` for fidelity.

> **Phase-1 pure-EMP inventory** (5 weapons, verified against galaxyonfire.wiki.gg, seed-fix `e87db57`):
> - Primaries: `luna_emp_mk_i` (emp_damage=3), `sol_emp_mk_ii` (5), `dia_emp_mk_iii` (8)
> - Secondary missile: `missiles.mamba_emp` (emp_damage=100)
> - Secondary mine: `mines.netha_emp` (emp_damage=500)

---

## 5. Accuracy system

### Combatant base accuracy
| Combatant | Base |
|---|---|
| Player | **60 %** |
| NPC / criminal | **50 %** |

### Layered formula
```
attacker_accuracy = combatant_base                 # 60% / 50%
                  + own_scanner_bonus              # 0 (A) / +5pp (B) / +10pp (C)
                  + own_thruster_bonus             # primaries only; ramps 0 at 750m → max at 300m
                  − opponent_booster_debuff        # while target's boost is active
                  → clamp [0.05, 0.99]
```

- **Primaries have NO in-range distance penalty.** Range is a pure binary gate (§2 / §6.1). Distance-as-accuracy is a *secondary*-weapon concern (rocket curve, missile tier-A degrade) and lives in §6.2.

### Cloak override (replaces the layered result)
While the **target's** cloak is active, the entire layered formula above is **REPLACED** by an absolute set:
```
attacker_accuracy = cloak_set_value   (default 0.25)
                  → clamp [0.05, 0.99]
```
It does NOT stack with / subtract from the other terms — it supersedes them.

### Booster accuracy debuff (defender-side)
While the target's booster is active:
```
opponent_booster_debuff_pp = effect_pct × k_boost     # percentage-points
```
- `k_boost` default **0.10** (`BOOSTER_ACCURACY_DEBUFF_FACTOR`).
- Subtracted from attacker accuracy and clamped.
- Additive with the booster's distance-push effect (§2) — both fire together.
- No separate cap: at default `k_boost`, the strongest booster (Polytron, `effect_pct = 300`) yields 30 pp — below cloak's 35 pp. The `[0.05, 0.99]` clamp bounds any extreme `k_boost`.

### Thruster accuracy bonus (attacker-side)
The equipping ship's *own* primary accuracy is boosted when close to its opponent:
```
bonus_pp = max_bonus_pp × ramp
   max_bonus_pp = effect_pct × k_thruster
   ramp = clamp((750 − current_distance) / (750 − min_distance), 0, 1)
```
- `k_thruster` default **0.10** (`THRUSTER_ACCURACY_BONUS_FACTOR`).
- `ramp` = 0 outside 750 m, linear to 1 at the 300 m floor.
- **Primaries only.** Turrets and rockets unaffected (rockets have their own 5 % → 60 % curve).
- Always evaluated (passive — no toggle, no HP-threshold, no cooldown). Distance gate is the only gate.

### Per-weapon accuracy modifier — DROPPED
Per-weapon `accuracy_modifier` is permanently removed.
- Forward-compat hook: `weapon_accuracy(pilot_acc, weapon) -> float` returns `pilot_acc` unchanged in Phase-1; an empty `SUBTYPE_ACCURACY_MOD: dict[str, float]` lives in `combat_balance.py`. Future homing-vs-must-aim split slots in here without structural rewrite.
- Code cleanup: remove `WeaponStats.accuracy_modifier` (multiplicative, default 1.0 — never populated); keep `ModuleStats.accuracy_modifier` (additive, default 0.0 — carries scanner bonus).

---

## 6. Weapons

### 6.1 Primary weapons
- Hit damage = `damage_per_shot` (physical). Cooldown = `loading_speed_ms`.
- **Range is a pure binary gate:** `current_distance ≤ range_m` → fires at full §5 accuracy; otherwise cannot fire at all.
- **No in-range distance penalty.**

### 6.2 Secondary weapons

**Phase-1 in-scope subtypes:** rocket, missile, cluster-missile, shock-blast.
**Phase-2 deferred:** `emp-bomb` (mechanic in scope when EMP lands; the physical track is inert in Phase-1).
**Phase-3+ deferred:** `mine`, `sentry-gun`, `ionizing-missile` (no ionizer mechanic planned; seed `damage` is already 0 — fires, rolls accuracy, applies 0 HP delta).

#### Rocket (`steerable: false`)
Linear accuracy curve from 5 % at max range → 60 % at min distance:
```
accuracy = 0.05 + 0.55 × ((range_m − current_distance) / (range_m − min_distance))
         → clamp [0.05, 0.60]
```

#### Missile (`steerable: true`)
Behavior depends on the equipping ship's scanner tier (§7.1):
- **Tier B or C scanner equipped** → tracking active → fires at the pilot's current §5 accuracy (no distance penalty applied).
- **Tier A (no scanner)** → degrades to rocket behavior (same projectile, same stats, rocket accuracy curve applies).

#### Cluster missile (`subtype: "cluster-missile"`, `burst_count: N`)
Lock-on tracking missile that releases **N sub-munitions** per fire. Inherits the plain-Missile scanner-tier rule for whether tracking is active (Tier B/C → §5 accuracy; Tier A → rocket curve).

- **Accuracy snapshot semantics:** the pilot's §5 accuracy is captured **once** at the moment of fire. ALL N sub-munitions roll independently against that single snapshot. A thruster ramp / cloak activation occurring mid-flight does NOT retroactively alter sub-munition rolls.
- **Damage application:** each landing sub-munition deals `damage` (per-sub-munition, NOT total). Single-target only (no AoE — cluster missiles carry no `magnitude_m`).
- **Combat-log condensation:** **ONE event per cluster fire** with summary fields `{weapon, fired: N, hits: K, damage_per_hit, total_damage: K × damage}`. Not N rows.
- **Seed inventory (Phase-1):** Shesha (`burst_count: 3`, damage 60), Garuda-IV (`burst_count: 4`, damage 75), Patala (`burst_count: 5`, damage 90). Resolver reads `burst_count` from `extra_atts` generically.

#### Nuke (`subtype: "nuke"`)
Area-of-effect weapon with **no accuracy roll**. Bypasses the entire §5 accuracy system (no cloak override, no thruster/booster modifiers). `range_m` is the binary fire gate (consistent with primaries).

- **Epicenter:** on fire, sample a uniform random distance `epicenter ~ U[300, 5000]` along the 1D combat-distance axis (the §2 combat-distance bounds).
- **Both ships always take damage** based on their distance from the epicenter:
  - Firer is at position 0 → `d_firer = epicenter`.
  - Opponent is at position `current_distance` → `d_opponent = |epicenter − current_distance|`.
- **Falloff formula** (inverse-square shape, reaches 0 at the effective magnitude):
  ```
  dmg(d) = damage × (1 − min(1, d / effective_magnitude))²
  effective_magnitude = magnitude_m × NUKE_MAGNITUDE_SCALE
  ```
  - `NUKE_MAGNITUDE_SCALE` default **0.10** (`BOUNTYBOT_NUKE_MAGNITUDE_SCALE`). Calibrates seed `magnitude_m` (10000–40000m) down to combat-distance scale (1000–4000m effective).
- **Opponent damage** = `dmg(d_opponent)`.
- **Firer self-damage** = `dmg(d_firer) × NUKE_FRIENDLY_FACTOR`.
  - `NUKE_FRIENDLY_FACTOR` default **0.25** (`BOUNTYBOT_NUKE_FRIENDLY_FACTOR`). Same falloff, scaled by friendly factor — nukes do not respect friend/foe, just attenuate.
- **Steerable flag IGNORED Phase-1.** Liberator's `steerable: true` is data-only fidelity; all 5 nukes behave identically except for `damage` and `magnitude_m`.
- **Seed inventory (Phase-1, direct-hit anchors):**
  - Liberator (`damage: 850`, `magnitude_m: 12500` → eff 1250m)
  - Extinctor (`damage: 700`, `magnitude_m: 40000` → eff 4000m)
  - Oppressor (`damage: 400`, `magnitude_m: 30000` → eff 3000m)
  - Tormentor (`damage: 150`, `magnitude_m: 10000` → eff 1000m)
  - Fireworks (`damage: 1`, `magnitude_m: 10000` → eff 1000m; decorative — same code path applies)

#### Shock-blast
Pure distance-reset utility (§2). No damage. 100 % guaranteed. Fires on cooldown. The seed file (`misc.shock_blast.json`) carries `damage: 140` / `emp_damage: 80` — **both IGNORED** by the Phase-1 mechanic.

### 6.3 Turret weapons

Three subtypes exist; two are combat-relevant. Discriminate using the `automatic: bool` field (auto/manual turrets carry no explicit `subtype` field; only plasma-collectors do).

#### Auto turrets (`automatic: true`)
- Fire on each turret's own cooldown, additively *alongside* primaries (auto turrets do not compete with the primary slot).
- **Accuracy:**
  ```
  auto_turret_accuracy = clamp(pilot_current_accuracy × auto_turret_multiplier, 0.05, 0.99)
  ```
  - `auto_turret_multiplier` default **0.85** (`AUTO_TURRET_ACCURACY_MULTIPLIER`).
  - `pilot_current_accuracy` is the full §5 result (post layered modifiers OR cloak override).
  - **Auto turrets inherit the cloak set-value.** If the target is cloaked, pilot accuracy is `cloak_set_value` (0.25 default), so auto turrets fire at ~0.2125 (= 0.25 × 0.85), re-clamped.
- **One accuracy value shared across all auto turrets on a ship** — no per-turret variation. An 8-turret battlecruiser computes one value per tick and applies it to all 8 turret shots.

#### Manual turrets (`automatic: false`)
- **Mutually exclusive with primary.** Pre-combat pilot-dedicates via a `manual_turret_mode: bool` flag on `ShipLoadout`.
- Default mode = primary (manual is opt-in; primary is typically higher damage).
- Override command does not exist yet (loadout flag is the modeling slot).

#### Plasma-collector turrets (`subtype: "plasma-collector"`, `dps: 0`)
- **Inert in combat.** Equippable for fidelity; produces no effect.

---

## 7. Modules

### 7.1 Scanners (combat scanners; plasma scanners ignored)

Three tiers:

| Tier | Pilot accuracy bonus | Missile behavior | Modules |
|---|---|---|---|
| A | 0 (no scanner) | Degrade to rocket | — |
| B | **+5 pp** | Track at pilot accuracy (no distance penalty) | Telta Quickscan (4.0 s), Telta Ecoscan (3.0 s) |
| C | **+10 pp** | Same as Tier B | Hiroto Proscan (1.8 s), Hiroto Ultrascan (1.8 s) |

- Combat scanner is **unique-equip on its own subclass** — one combat scanner at a time.
- Plasma scanner is a separate subclass with no combat effect (none in current seed).
- Lock-time numerics are flavor only in Phase-1; tier membership is what matters.
- **Thermal-fusion homing effect is bypassed in Phase-1.** Thermal-fusion is a primary class and follows primary rules; scanner tier does NOT modify thermal-fusion behavior.

### 7.2 Cloaks
- **Effect:** while active, the opponent's hit-chance against you is **hard-set to an absolute value** `cloak_set_value` (default **0.25**, `CLOAK_SET_VALUE`). NOT a relative reduction; NOT a forced miss. Replaces the §5 layered formula entirely.
- **Activation:** HP thresholds **66 % / 33 %** — up to 2 activations per fight.
- **Trigger rule:** activates iff off cooldown at the threshold crossing; missed threshold = skipped, no retry. Cooldown timer starts at *effect expiry*.
- **Duration:** per-module `effect_duration_ms` from wiki (U'tool = 10 s, Sight Suppressor II = 20 s, Shadow Ninja = 40 s). **Phase-1: all cloaks share the same `cloak_set_value`; tiers differ by duration only.**
- **Cooldown:** per-module `loading_speed_ms` from wiki.
- **Built-in cloaks (Scimitar + Specter):** these ships carry an implicit U'tool cloak (`builtinModules: ["U'tool"]`) that DOES function in combat — same mechanic, duration, cooldown, HP-threshold activation. The built-in does NOT count against `max_modules` (off-slot). **Supersession rule:** if the pilot equips a cloak module, the equipped one wins:
  ```
  effective_cloak = equipped if has_equipped else builtin
  ```
  Generalises to all `UNIQUE_EQUIP_TYPES`: equipped wins over built-in; built-in still functions when no equipped instance is present.

### 7.3 Boosters
- **Two simultaneous effects** while active:
  - **(a) Distance push** — formula in §2 (uses `effect_pct`).
  - **(b) Opponent accuracy debuff** — formula in §5: `debuff_pp = effect_pct × k_boost`, default `k_boost = 0.10`.
- **Activation:** HP thresholds **80 % / 60 % / 40 % / 20 %** — up to 4 activations if cooldown permits.
- **Trigger rule:** universal HP-threshold rule (§8).
- **Booster-user can still fire during boost** (accepted simplification; mirrors GoF2 base behavior).
- Per-module debuff at default `k_boost`: Linear 6 pp / Cyclotron 8 pp / Synchrotron 16 pp / Me'al 20 pp / Polytron 30 pp.

### 7.4 Thrusters
- **Attacker-side primary-accuracy bonus** (the equipping ship's handling makes *itself* hit better at close range). Formula in §5.
- **NO effect on distance / closure / weapon range / rocket accuracy / turrets.**
- **Passive — always active when conditions permit.** No HP-threshold gating, no `duration_ms`, no cooldown, no toggle. Evaluated every tick; gated solely by `current_distance < 750 m`.
- Per-module max bonus at default `k_thruster` (0.10): Static +2 pp / Pendular +4 pp / D'ozzt +7 pp / Mp'zzzm +10 pp / Pulsed Plasma +13 pp.

### 7.5 Shields
- Layer 1 (absorbs damage first per §3 stacking order).
- Continuous per-tick recharge via the integer schedule in §3. Recharge does NOT pause after a hit.

### 7.6 Repair Bot
- Scope: **hull + armour ONLY** — does NOT touch the shield layer.
- Fill order: hull first, then armour (inverse of damage stacking).
- Rate, formula, integer-flush schedule: §3.

### 7.7 EmergencySystem
- **Trigger:** an incoming damage event that would reduce **hull** to ≤ 0 (true ship-death interception). Shield or armour reaching 0 does NOT trigger.
- **Effect:** hull HP clamped to 1; **10 s of full invulnerability** (ALL incoming damage blocked).
- **Regen during invuln:** continues normally (shield + hull/armour concurrently if Repair Bot equipped).
- **HP at expiry:** `1 + 10 s × applicable regen rates`, capped per-layer max. Edge case (no shield, no Repair Bot) → HP = 1 at expiry.
- **Consumable:** removed from loadout after use; player must manually re-equip a spare from inventory. **Once per fight by consumption.**

### 7.8 PrimaryWeaponMod
Passive per-shot stat modifier. **Unique-equip** (§10) — only one PrimaryWeaponMod can be slotted at a time.

**Scope:** primary weapons ONLY. Secondaries (rockets / missiles / cluster-missiles / nukes / shock-blast), turrets (auto + manual), and auto-turret outputs are all UNAFFECTED.

**Mechanic** — honor seed `damage_pct` + `fire_rate_pct` (NOT the legacy `dpsMultiplier`):
```
effective_damage_per_shot   = round(damage_per_shot × (1 + damage_pct / 100))
effective_loading_speed_ms  = round((loading_speed_ms / (1 + fire_rate_pct / 100)) / TICK_MS) × TICK_MS
```
- `damage_pct` and `fire_rate_pct` are integer percentages from the module's `extra_atts`.
- `effective_damage_per_shot` rounds to nearest integer. **No floor guard** — base-0 EMP-blaster primaries stay 0; normal primaries (`damage_per_shot ≥ 2`) never round to 0 from −10%.
- `effective_loading_speed_ms` snaps to the nearest `TICK_MS` boundary (10ms) so the tick-based resolver lines up cleanly.
- `fire_rate_pct` semantics: positive = faster (lower cooldown). +20% means the weapon fires every `loading_speed_ms / 1.2` ms.

**Legacy `dpsMultiplier` field:** retained on the seed for two consumers — the *current* SimpleTTKResolver (until it's retired) and the item-detail embed (a quick "net DPS shift" hint for the player). The new tick-based resolver IGNORES it; the breakdown above is the source of truth.

**Seed inventory (Phase-1):**

| Module | `damage_pct` | `fire_rate_pct` | `dpsMultiplier` | Feel |
|---|---|---|---|---|
| Nirai Overdrive | −10 | +20 | 1.1 | lighter, faster shots |
| Nirai Overcharge | +20 | −10 | 1.1 | heavier, slower shots |

Both have ~+8% effective DPS (`(1 ± 0.10) × (1 ∓ 0.10) ≈ 1.08`); the `dpsMultiplier: 1.1` is a hand-rounded source-data approximation. The two modules feel mechanically distinct under the new resolver even though the headline DPS shift is the same.

### 7.9 Inert in Phase-1 (kept for fidelity)
The resolver loads these but applies no combat effect:

| Module | Reason |
|---|---|
| GammaShield | No radiation-damage source in Phase-1 |
| Plasma-collector turret | No plasma resource model in Phase-1 |
| Plasma scanner | Separate subclass; no combat effect defined |
| JumpDriveModule (Khador Drive) | Non-combat |
| TimeExtenderModule (Rhoda Vortex) | Non-combat |
| Compressor | Non-combat |
| MiningDrill | Non-combat |
| TractorBeam | Non-combat |
| Cabin | Non-combat |
| Signature | Non-combat |
| SpectralFilter | Non-combat |

### 7.10 Deferred to Phase-2 (NOT equippable for combat effect in Phase-1)
- **ShieldInjector (Phoenix SIS)** — plasma resource model needed.
- **RepairBeam** — active heal pairing model needed.
- **TransfusionBeam** — active heal pairing model needed.
- **emp-bomb** (secondary subtype) — EMP-only effect; physical track inert in Phase-1.

---

## 8. Activation rules (HP-threshold devices)

| Device | Thresholds | Max activations | Notes |
|---|---|---|---|
| Cloak | 66 % / 33 % | 2 | |
| Booster | 80 % / 60 % / 40 % / 20 % | 4 (if cooldown permits) | |
| Thruster | — (passive) | — | Always active when `current_distance < 750 m` |
| EmergencySystem | Lethal hull damage (not a %) | 1 (consumable) | |

**Universal trigger rule:** at any HP-threshold crossing, the device activates **iff off cooldown**. Still cooling = threshold *skipped*, no retry. Cooldown timer starts when **effect expires**, NOT when activated.

Per-activation sequence:
```
trigger → run for duration_ms → cooldown begins → cooldown lasts loading_speed_ms → eligible at next threshold crossing
```

---

## 9. Fight termination

- **Hard cap:** 18,000 ticks (3 simulated minutes).
- **One side dead:** other side wins.
- **PvP stalemate (cap reached, both alive):** draw. Both players keep credits; no rewards.
- **PvC stalemate (cap reached, both alive):** draw, BUT the criminal escapes — new system is selected along the route; hunt-checks reset. (Reuse the existing loss-path flow when coding.)

---

## 10. Unique-equip list (`UNIQUE_EQUIP_TYPES`)

At most one of each type per ship loadout:

| Type | Scope |
|---|---|
| Cloak | Phase-1 |
| Booster | Phase-1 |
| EmergencySystem | Phase-1 |
| PrimaryWeaponMod | Phase-1 |
| Combat scanner | Phase-1 |
| Plasma scanner (inert) | Phase-1 (fidelity) |
| TimeExtenderModule (Rhoda Vortex) | Non-combat |
| ShieldInjector (Phoenix SIS) | Phase-2 |

---

## 11. Phase-1 scope summary

**In scope:** primary, secondary (rocket / missile / shock-blast), turret (auto + manual); shields, armour, repair-bot, thrusters, cloaks, boosters, scanners, EmergencySystem, PrimaryWeaponMod; tick-based simulation, distance, HP layers, regen, HP-threshold activations, EmergencySystem invuln.

**Inert** (loaded for fidelity, no combat effect): see §7.9.

**Phase-2 deferred:** EMP mechanic (full disable-window design), ShieldInjector / RepairBeam / TransfusionBeam, out-of-combat HP recovery + dock mechanic (schema hooks in Phase-1), damaged-opponent start state (schema hooks in Phase-1), `emp-bomb` secondary subtype.

**Phase-3+ deferred:** mines, sentry-guns.

---

## 12. Combat log & results — persistence model

> Scope: combat mechanics + data persistence only. User-facing visualization (Discord rendering / condensation) is a later cycle and lives outside this spec.

### Two-tier output

| Tier | Where | Sent on fight response? |
|---|---|---|
| **0 — Summary** | Inline in `combat_result` dict + copied into `data.summary` on the persisted row | Yes |
| **1 — Full event-tick timeline** | Stored only in `combat_log.data.timeline` JSON | No — fetched on demand by `combat_log_id` |

The Tier-0 summary carries: outcome / reason / duration; per-combatant module activations (which + counts), secondary-weapon use (by subtype), accuracy %, HP remaining per layer, damage dealt / taken. Plus the `combat_log_id` so detail can be fetched later.

### `combat_log` table schema

| Column | Type | Null? | Notes |
|---|---|---|---|
| `id` | `Integer` PK autoincrement | no | The only handle. No separate UUID. |
| `guild_id` | `BigInteger` | no | Discord snowflake (same type as `Bounty.guild_id`). |
| `context` | `String(20)` | no | `duel` / `bounty_pvc` / `bounty_bonus`. |
| `combatant1_name` / `combatant2_name` | `String(255)` | no | Display name. For PvC the NPC side holds the criminal / bounty name. |
| `combatant1_user_id` / `combatant2_user_id` | `BigInteger` | yes | Discord user id (matches `DuelRequest.challenger_id`). **NULL ⇒ that side is an NPC.** |
| `winner_name` | `String(255)` | yes | NULL on stalemate. |
| `is_stalemate` | `Boolean` | no | |
| `data` | `JSON` | no | Whole log object (schema below). Generic `JSON` per existing convention — never queried internally. |
| `created_at` | `DateTime(timezone=True)` default `now(UTC)` | no | **Retention key.** |

- **Invariant:** at least one `combatant{1,2}_user_id` is non-NULL — NPC-vs-NPC never occurs. The persist layer may assert this.
- Combatant identity + outcome are **projected to columns** for cheap lookup/listing; the authoritative per-combatant detail lives in `data.summary`.
- **Size is bounded by retention, not by truncation.** The timeline is stored whole.

### `data` blob — internal schema

```jsonc
{
  "schema_version": 1,
  "summary": {
    "outcome": "win",                           // win | stalemate
    "reason": "hp_depleted",                    // hp_depleted | time_cap | mutual | draw
    "duration_ticks": 8421,
    "winner": "Specter",
    "combatants": {
      "1": {
        "name": "Wraith", "ship": "Specter",
        "start_hp": { "shield": 120, "armour": 300, "hull": 200 },
        "final_hp": { "shield": 0,   "armour": 0,   "hull": 140 },
        "damage_dealt": 620, "damage_taken": 480,
        "shots_fired": 240, "shots_hit": 168, "accuracy": 0.70,
        "module_activations": { "Cloak": 2, "Repair Bot": 3 },
        "secondary_fired":    { "rocket": 12 }
      },
      "2": { /* …same shape… */ }
    }
  },
  "timeline": [ /* CombatEvent rows, in processing order */ ],
  "metadata": { "tick_ms": 10, "total_ticks": 8421, "variance_percent": 0.05, "resolver": "tick_v1", "pvc_armour_buff": 1.5 }
}
```

### Timeline — real event-ticks (NOT every tick, NOT narrative milestones)

- A **tick counter starts at 0** at combat start and increments by 1 each 10 ms tick. Real time = `tick × tick_ms`.
- The timeline records **one row per event**, only for ticks where something actually happens (a weapon fires, damage is applied, a module activates, a regen pulse applies, a cooldown ends, distance changes, a layer depletes, …). **Empty ticks are not stored.**
- **Multiple events on the same tick → multiple rows sharing that `tick` value**, stored in **exact processing order** within the tick. Array order *is* the sequence (no separate index). E.g. a tick that resolves `regen → primary fire → damage → booster activation` produces four consecutive rows with that `tick` in that order.

### `CombatEvent` (one timeline row)

```jsonc
{
  "tick":   3000,            // tick counter (0 at start). Real ms = tick × tick_ms
  "type":   "weapon_fire",
  "actor":  "Specter",       // acting combatant name; null for global/system events
  "target": "Vossk Raider",  // null when N/A
  "data":   { /* type-specific payload */ }
}
```

`CombatEvent` carries **structured data only** — no pre-rendered human strings. Wording can change later without rewriting stored history.

**Representative event vocabulary** (extensible — the resolver may emit additional types for any state-changing tick-step):

| `type` | Emitted when | Example `data` |
|---|---|---|
| `fight_start` | tick 0 | combatants, ships, start HP layers, initial distance |
| `regen` | shield recharge pulse or repair-bot hull/armour pulse applies | `{layer, amount, hp_after}` |
| `weapon_fire` | a primary / secondary / turret fires (incl. miss) | `{slot, subtype, weapon, hit, accuracy}` |
| `damage` | HP applied to a target after a hit | `{amount, breakdown:{shield,armour,hull}, hp_after, source}` |
| `module_activation` | cloak / booster / repair-bot / EmergencySystem / etc. engages | `{module, trigger_hp_pct}` |
| `cooldown_end` | a weapon or module comes off cooldown | `{system}` |
| `layer_depleted` | a ship's shield → 0 or armour → 0 | `{layer}` |
| `distance` | distance changes (booster push, closing, shock-blast reset) | `{from, to, cause}` |
| `fight_end` | terminal | `{winner, reason, duration_ticks, final_hp}` |

### Retention

- Mechanism: extend the existing **`db_retention_default`** scheduled job (daily 03:45 UTC) with `CombatLogRepository.delete_older_than(cutoff)`, mirroring the bounty / duel / audit retention pattern.
- Retention key: `created_at`.
- Configurable via **`BOUNTYBOT_COMBAT_LOG_RETENTION_HOURS`** (env / per-guild per §0.1).

### In-memory production & `FightResults` mapping

- The tick resolver builds the full `timeline` + summary in memory during the sim:
  - `FightResults.combat_log: list[dict]` ← the timeline
  - `FightResults.metadata: dict` ← the summary
  - Both stub fields already exist on the dataclass.
- After resolution the callsite:
  1. Persists a `combat_log` row via `CombatLogService` (returns `combat_log_id`).
  2. Updates Player lifetime metrics (§13).
  3. Puts `{summary…, combat_log_id}` into `combat_result`. The bulky `timeline` is **NOT** sent on the fight response.
- **Legacy `FightStats` (`raw_hp` / `varied_hp` / `raw_dps` / `varied_dps` / `ttk`) stay populated** for wire compatibility with existing consumers:
  - `varied_hp` ← effective start HP
  - `varied_dps` ← `damage_dealt / duration_s`
  - `ttk` ← `duration_s` for the loser / `None` for the winner

---

## 13. Player-profile stat promotion — mechanism

Aggregate **lifetime** combat metrics are promoted onto the **`Player` record** by the combat processor — **NOT** stored in `combat_log`. This is a handler inside the combat-service code that mutates the `Player` object after a fight; the log tables are unaffected.

**Phase-1 locked counter set** — 3 new `Player` columns (all `Integer`, `default=0`):

| Column | Increments when… |
|---|---|
| `total_fights` | post-fight (any fight the player participated in) |
| `total_nukes_fired` | per nuke fire event (uses §6.2 Nuke mechanic) |
| `total_module_activations` | per HP-threshold or manual module activation (cloak / booster / EmergencySystem / etc.) |

All 3 are **bounded-per-fight** — a single fight contributes a small finite count, so headline lifetime numbers stay meaningful rather than drifting into uninteresting-big-number territory. Existing counters (`bounty_wins`, `duel_wins`, `duel_losses`, `duel_credits_won`, `duel_credits_lost`, `lifetime_credits`, `systems_checked`, `xp`, `prestige_count`) are unchanged.

- The combat processor increments these on the `Player` row(s) for any human combatant as part of the post-fight update. NPC side has no Player row → skipped.
- Requires an Alembic migration for the 3 new columns.

**Intentionally NOT tracked at Player level** (revisit in Phase-2+ if a leaderboard feature lands): per-subtype shot breakdowns (rocket / missile / cluster), `total_shots_fired`, `total_secondaries_fired`, any `total_damage_*` family, `bounty_losses`. Per-subtype detail is derivable from `combat_log` while inside the retention window (§12).

---

## 14. Downstream sync — item-detail embeds

Phase-1 combat-spec corrections to seed-data structure must be reflected in user-facing item-detail embeds (the embed shown when a player views an individual weapon/module, typically via inventory/ships flows). The embed is the only surface where a player sees raw weapon stats; it is the canonical place to disambiguate physical vs EMP damage and to surface cluster-missile burst behavior.

**Required embed fields** (any item-detail embed that renders a weapon MUST include):

1. **EMP / physical damage distinction** — for any weapon with `emp_damage > 0` in `extra_atts`, the embed MUST surface EMP damage as a distinct labelled field, separate from physical `damage` / `damage_per_shot`. Background: seed-fix `e87db57` corrected the 3 EMP-blaster primaries (Luna/Sol/Dia EMP) from misplaced-physical to true pure-EMP (`damage_per_shot: 0`, `emp_damage: 3/5/8`). Without this distinction the embed shows "damage: 0" with no explanation, hiding the real weapon characteristic.
2. **Cluster missile `burst_count`** — for any weapon with `subtype: "cluster-missile"`, the embed MUST surface the `burst_count` value, ideally alongside per-sub-munition `damage` AND derived `total damage on full hit = burst_count × damage`. This is the only way a player can compare cluster-missile DPS to plain-missile DPS meaningfully.
3. **Nuke direct-hit + effective magnitude + self-damage warning** — for any weapon with `subtype: "nuke"`, the embed MUST surface (a) the direct-hit `damage` value (epicenter damage), (b) the **effective magnitude** = `magnitude_m × NUKE_MAGNITUDE_SCALE` (not the raw `magnitude_m`, which is misleading since the runtime scales it), and (c) a **self-damage warning** indicating the firer is caught in the blast at `NUKE_FRIENDLY_FACTOR × falloff_damage`. Without these the player has no way to reason about the risk/reward of nuke usage (e.g. Liberator's 850 direct damage carries a ~123 point-blank self-damage cost — that cost MUST be visible).
4. **PrimaryWeaponMod breakdown** — for any module with `type: "PrimaryWeaponModModule"`, the embed MUST surface (a) `damage_pct`, (b) `fire_rate_pct`, AND (c) the legacy `dpsMultiplier` value (separately labelled as "net DPS shift"). Background: §7.8 honors the per-shot breakdown, but the two modules in scope (Nirai Overdrive / Overcharge) have an identical `dpsMultiplier: 1.1` despite producing mechanically distinct loadouts (lighter-faster vs heavier-slower) — surfacing all 3 fields is the only way a player sees the tradeoff at equip time rather than discovering it mid-fight.

**Implementation scope** (touches both services):
- `services/bot-core/src/api/schemas/` — extend item-detail Pydantic schema(s) so `emp_damage` and `burst_count` are explicit response fields (currently they live inside the generic `extra_atts` blob).
- `services/discord-gateway/src/cogs/` — the cog(s) that build the item-detail embed must render the new fields. Most likely the inventory / ships cogs; verify against current cog list at implementation time.

**Out of scope** for this section: the `/about` bot-info command (BountyBot version/owner info, unrelated to item rendering). The phrase "/about embed" elsewhere in the journal refers to the item-detail embed described here.

---

## Appendix A — Configuration knobs (locked defaults)

All overridable via `BOUNTYBOT_<NAME>` env var **and** per-guild override (per §0.1).

| Constant | Default | Section |
|---|---|---|
| `CLOAK_SET_VALUE` | **0.25** | §5 / §7.2 |
| `BOOSTER_ACCURACY_DEBUFF_FACTOR` | **0.10** | §5 / §7.3 |
| `THRUSTER_ACCURACY_BONUS_FACTOR` | **0.10** | §5 / §7.4 |
| `AUTO_TURRET_ACCURACY_MULTIPLIER` | **0.85** | §6.3 |
| `KETAR_I_REPAIR_PCT_PER_SEC` | **0.025** (2.5 %/s) | §3 / §7.6 |
| `KETAR_II_REPAIR_PCT_PER_SEC` | **0.050** (5.0 %/s) | §3 / §7.6 |
| `TICK_MS` | **10** | §1 |
| `MAX_FIGHT_TICKS` | **18000** (3 min) | §1 / §9 |
| `STARTING_DISTANCE_M` | **5000** | §2 |
| `BASE_SHIP_SPEED_MPS` | **150** | §2 |
| `MIN_DISTANCE_M` | **300** | §2 |
| `THRUSTER_WINDOW_M` | **750** | §5 / §7.4 |
| `PLAYER_BASE_ACCURACY` | **0.60** | §5 |
| `NPC_BASE_ACCURACY` | **0.50** | §5 |
| `ACCURACY_CLAMP_MIN` | **0.05** | §5 |
| `ACCURACY_CLAMP_MAX` | **0.99** | §5 |
| `SCANNER_TIER_B_BONUS_PP` | **5** | §5 / §7.1 |
| `SCANNER_TIER_C_BONUS_PP` | **10** | §5 / §7.1 |
| `CLOAK_HP_THRESHOLDS_PCT` | **[66, 33]** | §7.2 / §8 |
| `BOOSTER_HP_THRESHOLDS_PCT` | **[80, 60, 40, 20]** | §7.3 / §8 |
| `EMERGENCY_SYSTEM_INVULN_S` | **10** | §7.7 |
| `NUKE_MAGNITUDE_SCALE` | **0.10** | §6.2 |
| `NUKE_FRIENDLY_FACTOR` | **0.25** | §6.2 |

> Names with `BOUNTYBOT_` prefix are listed verbatim where the existing convention uses the prefix in the env name; others use the unprefixed `GameConstants` name (the runtime env override is `BOUNTYBOT_<NAME>` in all cases).

---

## Appendix B — Formula reference (locked)

### Distance / movement
- **Passive closure (no boost):** `current_distance -= 300 × (tick_ms / 1000)` per tick; floor `MIN_DISTANCE_M`.
- **Booster push:** `distance_gained_m = base_speed × (effect_pct / 100) × (duration_ms / 1000)`. Passive closure suspended for the boost window.
- **Shock-blast:** `current_distance := STARTING_DISTANCE_M` (5000), guaranteed.

### Accuracy
- **Layered:** `acc = clamp(base + scanner + thruster_bonus − booster_debuff, 0.05, 0.99)`
- **Cloak override:** `acc = clamp(CLOAK_SET_VALUE, 0.05, 0.99)` (replaces layered)
- **Booster debuff:** `debuff_pp = effect_pct × BOOSTER_ACCURACY_DEBUFF_FACTOR`
- **Thruster bonus:** `bonus_pp = effect_pct × THRUSTER_ACCURACY_BONUS_FACTOR × ramp`, where `ramp = clamp((750 − current_distance) / (750 − 300), 0, 1)`
- **Auto turret:** `acc = clamp(pilot_current_accuracy × AUTO_TURRET_ACCURACY_MULTIPLIER, 0.05, 0.99)`

### Rocket accuracy curve
```
accuracy = 0.05 + 0.55 × ((range_m − current_distance) / (range_m − MIN_DISTANCE_M))
         → clamp [0.05, 0.60]
```

### Nuke (AoE — no accuracy roll)
```
epicenter           ~ U[MIN_DISTANCE_M, STARTING_DISTANCE_M]   # [300, 5000]
d_firer             = epicenter                                # firer at position 0
d_opponent          = |epicenter − current_distance|
effective_magnitude = magnitude_m × NUKE_MAGNITUDE_SCALE

dmg(d)              = damage × (1 − min(1, d / effective_magnitude))²

opponent_damage     = dmg(d_opponent)
self_damage         = dmg(d_firer) × NUKE_FRIENDLY_FACTOR
```
Both ships always take damage. Accuracy / cloak / thruster / booster modifiers do NOT apply. Steerable flag ignored.

### PrimaryWeaponMod (Nirai Overdrive / Overcharge — primary weapons only)
```
effective_damage_per_shot   = round(damage_per_shot × (1 + damage_pct / 100))
effective_loading_speed_ms  = round((loading_speed_ms / (1 + fire_rate_pct / 100)) / TICK_MS) × TICK_MS
```
`damage_pct` and `fire_rate_pct` per seed `extra_atts`. Legacy `dpsMultiplier` field IGNORED by tick-resolver (kept only for current SimpleTTKResolver + embed display). No floor on effective damage. Cooldown snaps to nearest `TICK_MS`.

### Regen pulse schedule (per layer source)
- **Shield:** `+1 HP every N ticks`, `N = ceil(shield_recharge_ms / shield_capacity / tick_ms)` per shield module.
- **Repair Bot (hull + armour):** per-tick delta = `(max_hull + max_armour) × rate × (tick_ms / 1000)`, accumulated and integer-flushed. `rate` ∈ {0.025, 0.050} for Ketar I / II.

### Tick step order (per tick)
1. Decrement all cooldowns (`-= tick_ms`).
2. Apply regen pulses (shield + hull/armour, in parallel — independent tracks).
3. Evaluate weapon firings (primary + secondary + auto turret) for any system with cooldown ≤ 0 AND in range AND not gated.
4. Apply damage (shield → armour → hull stacking order).
5. Check HP-threshold crossings → trigger eligible HP-gated activations (cloak / booster / EmergencySystem).
6. Update distance (passive closure OR booster push OR shock-blast reset).
7. Emit any state-changing events to the timeline (in the processing order above).
8. Check termination (any combatant dead OR tick == `MAX_FIGHT_TICKS`).

---

## Appendix C — Phase-1 scope matrix

| Item | Status | Where defined |
|---|---|---|
| Primary weapons | In scope | §6.1 |
| Secondary: rocket | In scope | §6.2 |
| Secondary: missile | In scope | §6.2 |
| Secondary: cluster-missile | In scope | §6.2 |
| Secondary: nuke | In scope | §6.2 |
| Secondary: shock-blast | In scope | §6.2 |
| Turret: auto | In scope | §6.3 |
| Turret: manual | In scope | §6.3 |
| Turret: plasma-collector | Inert (loaded for fidelity) | §6.3 / §7.9 |
| Scanner: Tier A / B / C | In scope | §7.1 |
| Plasma scanner | Inert | §7.1 / §7.9 |
| Cloak | In scope | §7.2 |
| Booster | In scope | §7.3 |
| Thruster | In scope | §7.4 |
| Shield (ShieldModule) | In scope | §7.5 |
| Armour (ArmourModule) | In scope | §3 |
| Repair Bot | In scope | §7.6 |
| EmergencySystem | In scope | §7.7 |
| PrimaryWeaponMod | In scope | §7.8 |
| GammaShield | Inert | §7.9 |
| Non-combat modules (Khador, Rhoda, Cabin, Compressor, MiningDrill, TractorBeam, Signature, SpectralFilter) | Inert | §7.9 |
| ShieldInjector (Phoenix SIS) | Deferred — Phase-2 | §7.10 |
| RepairBeam | Deferred — Phase-2 | §7.10 |
| TransfusionBeam | Deferred — Phase-2 | §7.10 |
| Secondary: `emp-bomb` | Deferred — Phase-2 | §6.2 / §7.10 |
| Mine | Deferred — Phase-3+ | §6.2 |
| Sentry-gun | Deferred — Phase-3+ | §6.2 |
| Secondary: ionizing-missile | Deferred — Phase-3+ | §6.2 |
| EMP damage type | Deferred — Phase-2 | §4 |
| Out-of-combat HP recovery + dock | Deferred — Phase-2 (schema hooks in Phase-1) | §11 |
| Damaged-opponent start state | Deferred — Phase-2 (schema hooks in Phase-1) | §3 / §11 |
