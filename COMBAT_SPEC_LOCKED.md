# Combat System Specification (LOCKED)

> Canonical, locked-in design for the BountyBot Phase-1 combat system. Items
> here have been explicitly confirmed and are not subject to ambiguity.
> Configurable knobs include their default values.

---

## 0. Meta

### 0.1 Configuration policy
Every numeric in this spec (rates, percentages, thresholds, durations, distances, magnitudes) is a **starting default**. All defaults land in
`services/bot-core/src/services/game_constants.py` as `GameConstants.<NAME>` with:
- `BOUNTYBOT_<NAME>` environment-variable override, and
- per-guild override (matching the existing pattern in `GameConstants`).

Tuning post-Phase-1 must not require code changes.

### 0.2 Resource policy
**Energy is assumed infinite — for combat and any other gameplay surface that might check energy.** Energy cells are not tracked. Wiki lore references to "energy cell consumption per use" (e.g. U'tool cloak) are cosmetic only; the resolver does not gate on energy. Applies to player AND criminal/NPC combatants.

### 0.3 Symbol naming convention
Throughout this spec:
- **`UPPERCASE_SNAKE_CASE`** (e.g. `MIN_DISTANCE_M`, `CLOAK_SET_VALUE`, `PVC_DAMAGE_REDUCTION`, `TICK_MS`) — **configurable tunables**. These land in `GameConstants` (§0.1) and are overridable via `BOUNTYBOT_<NAME>` environment variables and per-guild overrides. Appendix A is the canonical list of locked defaults.
- **`lowercase_snake_case`** (e.g. `current_distance`, `base_speed`, `effect_pct`, `damage_pct`, `loading_speed_ms`, `range_m`, `duration_ms`) — **internal variables, function arguments, formula parameters, or per-item seed-data attributes**. NOT configuration knobs. These flow from runtime resolver state (e.g. `current_distance`) or from per-weapon / per-module seed JSON (e.g. `loading_speed_ms`, `effect_pct`).

When a formula uses a lowercase symbol whose value is sourced from a config knob, the formula is written in the lowercase form for readability but the value is read from the matching UPPERCASE constant. Example: `ramp = clamp((750 − current_distance) / (750 − min_distance), 0, 1)` — `current_distance` is runtime state, `750` is the literal value of `THRUSTER_WINDOW_M`, `min_distance` is the literal value of `MIN_DISTANCE_M`. The lowercase form is for the prose; the implementation reads from `GameConstants`.

---

## 1. Tick & timing

- **Tick = 10 ms, fixed.**
- **Hard cap: 18,000 ticks per fight (3 simulated minutes).**
- **Per-weapon cooldown:** each weapon holds `cooldown_remaining_ms`; per tick decrement by 10; fires when `≤ 0` AND in-range AND not gated; resets to `loading_speed_ms`.
- All wiki `loading_speed_ms` values are clean multiples of 10 ms — no accumulator carry, no drift.
- **Initial state at tick 0:** all weapons enter combat fully ready (`cooldown_remaining_ms = 0`) — **EXCEPT nuke secondaries, which start on FULL cooldown (D-014 arming delay; see §6.2 Nuke)**. First-tick firing is gated only by range and any other normal checks. HP-threshold module cooldowns are also `0` at tick 0 (see §8). Regen-track dormancy and damaged-start handling: see §3.
- **Implementation note:** initial cooldown / regen state is a *combatant init* concern (set during combatant construction, not inside the tick loop). This isolates Phase-2 "damaged-start" / "ambush" scenarios — where a combatant might enter with weapons mid-cooldown — from the tick-step logic.

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
- **No upper distance bound.** `current_distance` is naturally bounded by per-module booster limits (`effect_pct × duration_ms`) and by the booster's **cooldown** — there is no per-fight booster activation cap (the legacy fixed-count cap was removed in the Thread-5 combat-chain change; see §7.3 / §8), and no synthetic `MAX_DISTANCE_M` cap. Booster re-activation is rate-limited solely by cooldown, so the practical distance ceiling is the gap one boost opens before passive closure resumes. Weapons stop firing once they exceed their own `range_m`; otherwise no special behavior at extreme distances.
- **Shock-blast distance reset:** instantly resets both ships to the starting distance (5000 m). 100% guaranteed (no accuracy roll). No damage. Fires on cooldown (`loading_speed_ms`); no per-fight cap; no HP-threshold gating. **Close-range trigger gate (FIX 2):** fires only when `current_distance < SHOCK_BLAST_TRIGGER_RANGE_M` (default **500 m**) — at long range the reset is pointless and would waste a cooldown/round. (Phase-1 weapons resolve same-tick, so in-flight projectile interaction is moot.)

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
- **HP is integer; per-layer HP delta is integer at end of tick.** Intermediate calculations (PvC DR scaling, nuke falloff, regen accumulators, etc.) may carry float values mid-phase. The integer boundary is enforced when an HP delta is committed to a layer at the end of the damage/regen phase — use Python `round()` (consistent with §7.8's PrimaryWeaponMod convention).

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

### Incoming damage reduction — PvC player buff (Keith T. Maxwell bonus)
In **PvC fights only**, the player-side combatant receives a uniform reduction on all incoming damage. Lore: the player character (Keith T. Maxwell in GoF2 canon) is a tougher pilot than a stock-loadout ship reflects.

- **Formula:** for each damage event landing on the player-side combatant, `applied_damage = raw_damage × (1 − PVC_DAMAGE_REDUCTION)`. Apply this scaling *before* the shield → armour → hull stacking.
- **Default magnitude:** `PVC_DAMAGE_REDUCTION = 0.33` (`BOUNTYBOT_PVC_DAMAGE_REDUCTION`, env/per-guild overridable per §0.1).
- **Sources covered — all incoming damage:** opponent weapon hits, opponent secondary impacts, AND the firer's own nuke self-damage (§6.2). The buff is a uniform "tougher hull" effect — it does not discriminate by damage source.
- **Stacking with other mechanics:** DR is the *first* modifier applied to a damage event before it walks the layers. EmergencySystem (§7.7) still evaluates against the post-DR end-of-damage-phase hull.
- **NPC-side and PvP combatants:** receive zero DR. PvP fights run with no DR on either side (the buff would cancel out, so it isn't applied).
- **Why DR instead of an HP / armour multiplier:** keeps the player's stored `max_*` and (Phase-2) `current_*` HP-layer values on their stock-loadout scale, so the planned Phase-2 "enter combat with sustained damage from earlier battles" hook does not need scale-aware conversion. The buff lives entirely in the damage-application step, not in the loadout.
- **Legacy retirement:** this replaces the older `player_armour_buff: float = 1.0` parameter on `CombatService.fight_ships(...)` and the `BOUNTY_PVC_ARMOUR_BUFF_FACTOR` constant. The PR-4 cut renames the parameter to `pvc_damage_reduction: float = 0.0` and removes the old constant; the SimpleTTKResolver retires alongside.

### Regen dormancy & initial state
- **Regen runs only when its target layer has HP to recover.** Shield regen ticks only while `current_shield < shield_max`. Repair Bot regen ticks only while `current_hull < hull_max` OR `current_armour < armour_max` (its fill scope per §7.6).
- **Accumulator semantics:** when a layer drops below max, the regen accumulator starts at 0 and ticks per the integer-flush schedule above. When the layer returns to max, the accumulator goes dormant and any partial accumulation is **discarded** — next damage to that layer starts a fresh accumulator. ("Recharge does NOT pause after a hit" still holds *within* a damaged window — it's continuous from first damage until layer returns to max.)
- **Phase-1 initial state:** all combatants start at max on every layer, so all regen accumulators are dormant at tick 0; each starts when the first damage on its relevant layer applies.
- **Phase-2 damaged-start hook:** if a combatant is constructed with `current_shield < shield_max` (or `current_hull / current_armour` below max), the relevant accumulator is active immediately at tick 0 — the dormancy check sees an already-damaged layer and engages. No tick-loop change needed; this is a combatant-init concern (see §1 implementation note).

---

## 4. Damage type model

- **Phase-1 damage type: physical ONLY.**
- **EMP is a separate damage type, deferred to Phase-2+** (mechanic + disable-window design parked).
- **Resolver rule:** every weapon participates in cooldown / firing / event-log. Each hit applies `damage_per_shot` (physical) only; any `emp_damage` field is ignored regardless of value.
- **Hybrid weapons** (`damage_per_shot > 0` AND `emp_damage > 0`, secondaries only): fire normally; apply ONLY the physical `damage_per_shot`. EMP component ignored.
- **Pure-EMP weapons** (`damage_per_shot` = 0 / absent, `emp_damage > 0`): fire normally, roll accuracy, log hit/miss, apply **0 HP delta**.
- **Pure-EMP equip policy:** equipping a pure-EMP weapon in Phase-1 is accepted as a player choice. No preflight warning. No loadout-build filter. The combat log surfaces the 0-damage outcome.
- **GammaShield is inert in Phase-1** (no radiation-damage source exists). Referenced in §10 for fidelity (the resolver loads the module class but applies no combat effect).

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
                  + own_thruster_bonus             # omitted in pilot_turret_acc (auto turrets); ramps 0 at 750m → max at 300m
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
- **Applies to weapons firing at the full §5 result (`pilot_primary_acc`):** primaries, manual turrets (§6.3), and tracking Tier-B/C missiles / cluster snapshots (§6.2 — "fires at the pilot's current §5 accuracy" includes this term). Auto turrets and rockets unaffected (auto turrets use `pilot_turret_acc`, which excludes this term; rockets and Tier-A missiles have their own 5 % → 60 % curve).
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

**Phase-1 in-scope subtypes:** rocket, missile, cluster-missile, nuke, shock-blast.
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
A cluster missile is **identical to a plain missile in every respect except that it releases N sub-munitions per fire instead of one**. Inherits the plain-Missile scanner-tier rule for whether tracking is active (Tier B/C → §5 accuracy; Tier A → rocket curve).

- **Accuracy snapshot semantics:** the fire-time accuracy is captured **once** per cluster fire — sourced from the pilot's §5 accuracy under Tier B/C, or from the rocket-curve evaluated against `current_distance` at fire time under Tier A. ALL N sub-munitions then roll independently against that single snapshot. A thruster ramp / cloak activation / distance change occurring after the cluster fires does NOT retroactively alter sub-munition rolls.
- **Damage application — per-sub-munition, sequential:** each landing sub-munition deals `damage` (per-sub-munition, NOT total) and walks the shield → armour → hull stack independently. Single-target only (no AoE — cluster missiles carry no `magnitude_m`). **All K landed sub-munitions apply** even if an earlier one drops the target to HP ≤ 0; overkill is allowed (HP can go transiently negative within Appendix B step 4). EmergencySystem evaluates against the *cumulative* end-of-phase hull, not per sub-munition (§7.7) — so a cluster that goes wildly past lethal still triggers ES at most once.
- **Combat-log condensation:** **ONE event per cluster fire** with summary fields `{weapon, fired: N, hits: K, damage_per_hit, total_damage: K × damage}`. `total_damage` is the swung output of the **K landed** sub-munitions (`K × damage` — pre-clamp, overkill included; NOT `N × damage` max-possible); the actual HP absorbed (post-clamp) feeds the per-combatant `damage_dealt` rollup in §12's summary. Not N rows.
- **Seed inventory (Phase-1):** Shesha (`burst_count: 3`, damage 60), Garuda-IV (`burst_count: 4`, damage 75), Patala (`burst_count: 5`, damage 90). Resolver reads `burst_count` from `extra_atts` generically.

#### Nuke (`subtype: "nuke"`)
Area-of-effect weapon with **no accuracy roll**. Bypasses the entire §5 accuracy system (no cloak override, no thruster/booster modifiers). `range_m` is the binary fire gate (consistent with primaries). **Revised under D-014 (2026-06-10): two-regime detonation window, arming delay, yield interference.**

- **Arming delay (D-014, nuke-only):** nuke tubes start the fight on FULL cooldown (`cooldown_remaining_ms = loading_speed_ms` at init). First fire lands exactly one cooldown into the fight — kills the free max-range alpha-strike. All other secondary subtypes remain ready at tick 0.
- **Epicenter (D-014 two-regime window):** on fire, sample `epicenter ~ U[window]` (one uniform draw) where the window depends on `current_distance` (`d`):
  - **Long-range** (`d > NUKE_RANGE_REGIME_THRESHOLD_M`): `window = [NUKE_LR_NEAR_FRAC × d, d]` — aimed at the target, never overshoots; the deepest short round lands `(1−NEAR_FRAC)` of the gap back toward the firer (long-range self-risk scales with the gap).
  - **Close-range** (`d ≤ threshold`): `window = [max(0, d − NUKE_CR_SHORT_M), d + NUKE_CR_OVERSHOOT_M]` — artillery bracket with overshoot; the epicenter can land directly on either ship. Edges meet continuously at the boundary with the defaults (0.40×1000 == 1000−600).
- **Both ships always take damage** based on their distance from the epicenter:
  - Firer is at position 0 → `d_firer = epicenter`.
  - Opponent is at position `current_distance` → `d_opponent = |epicenter − current_distance|`.
- **Falloff formula** (squared falloff, reaches 0 at the effective magnitude):
  ```
  dmg(d) = damage × (1 − min(1, d / effective_magnitude))² × stack_mult
  effective_magnitude = magnitude_m × NUKE_MAGNITUDE_SCALE
  stack_mult = NUKE_STACK_FALLOFF ** prior_detonations_this_side   # yield interference
  ```
  - `NUKE_MAGNITUDE_SCALE` default **0.10** (`BOUNTYBOT_NUKE_MAGNITUDE_SCALE`). Calibrates seed `magnitude_m` (10000–40000m) down to combat-distance scale (1000–4000m effective).
  - **Yield interference (D-014):** each side carries a per-fight detonation counter; every successive detonation by the same side multiplies the WHOLE detonation (opponent + self damage) by `NUKE_STACK_FALLOFF` (default **0.5**) per prior detonation → 1.0, 0.5, 0.25, … Total nuke damage per side per fight is hard-bounded at < 2× the best single detonation, killing the "load N nukes → alpha-win" strategy without weakening the first nuke. Counters are per-side and reset each fight.
- **Opponent damage** = `dmg(d_opponent)`.
- **Firer self-damage** = `dmg(d_firer) × NUKE_FRIENDLY_FACTOR`.
  - `NUKE_FRIENDLY_FACTOR` default **0.50** (`BOUNTYBOT_NUKE_FRIENDLY_FACTOR`; was 0.25 pre-D-014). Same falloff, scaled by friendly factor — nukes do not respect friend/foe, just attenuate.
- **Steerable flag IGNORED Phase-1.** Liberator's `steerable: true` is data-only fidelity; all 5 nukes behave identically except for `damage` and `magnitude_m`.
- **Seed inventory (Phase-1, direct-hit anchors):**
  - Liberator (`damage: 850`, `magnitude_m: 12500` → eff 1250m)
  - Extinctor (`damage: 700`, `magnitude_m: 40000` → eff 4000m)
  - Oppressor (`damage: 400`, `magnitude_m: 30000` → eff 3000m)
  - Tormentor (`damage: 150`, `magnitude_m: 10000` → eff 1000m)
  - Fireworks (`damage: 1`, `magnitude_m: 10000` → eff 1000m; decorative — same code path applies)

#### Shock-blast
Pure distance-reset utility (§2). No damage. 100 % guaranteed. Fires on cooldown — but **only inside `SHOCK_BLAST_TRIGGER_RANGE_M` (500 m)**; outside that range it holds (the reset would be pointless and waste a cooldown/round). The seed file (`misc.shock_blast.json`) carries `damage: 140` / `emp_damage: 80` — **both IGNORED** by the Phase-1 mechanic.

Weapons and modules are independent subsystems: firing shock-blast resets `current_distance` only — active cloak / booster effects continue running on their own `duration_ms`, and module cooldowns are unaffected.

#### Secondary ammunition (consumable) — CI-16

Secondary weapons are **ammo-limited consumables** as of CI-16. Key rules:

- **1 round per fire trigger**, regardless of subtype (including cluster-missile — burst/N munitions count as 1 trigger, 1 round).
- **Ammo gate** — evaluated FIRST, before cooldown/range. When `remaining_ammo == 0`, weapon is silently skipped.
- **`secondary_depleted` event** — emitted at the same tick the last round fires (when `remaining_ammo` transitions to 0).
- **`ammo=None`** → infinite (back-compat for legacy loadout paths).
- **Player ammo persists cross-battle** in `player_ships.secondary_ammo` (JSON sidecar `{name: rounds}`); written back by `_consume_secondary_ammo` after fight. **Equip seeds the sidecar with the WHOLE cargo stack** (all rounds move cargo → ammo; re-equipping the same name tops up).
- **Auto-unequip at 0** — post-fight only. Mid-fight just stops firing; never a live mid-tick slot mutation.
- **Criminal side (CI-17 — LANDED):** criminals are armed at **bounty creation** (`bounty_service` stores `rounds` on the criminal-ship JSON) with per-subtype round grants from `CRIMINAL_SECONDARY_ROUNDS` (defaults: nuke **1** — prevents unwinnable alpha-strikes; missile/rocket **5**; cluster-missile **3**; shock-blast **2**); `LoadoutBuilder.from_criminal_ship` reads the stored value at fight time. Both the grant and the read apply a `max(1, …)` floor, so a criminal secondary always fires at least once. Weapons with `damage ≤ CRIMINAL_SECONDARY_MIN_DAMAGE` (default 1) are excluded from criminal gear rolls (drops the 0-dmg seeds and 1-dmg Fireworks). In-fight only; criminals have no cross-fight persistence.
- **Future per-battle-cap extension point** — limit is qty + cooldown only; no hard 1/battle rule in Phase-1.

Conservation model (loadout subsystem): `owned(S) = cargo.quantity(S) + Σ_ships secondary_ammo[S]`.
The `secondary_weapons` slot-list entry is **pure slot occupancy — NOT a counted copy** (see `services/AGENTS.md`).

Cross-references: §7.7 (EmergencySystem — generalized consumable pattern) · §0.2 (item vocabulary).

### 6.3 Turret weapons

Three subtypes exist; two are combat-relevant. Discriminate using the `automatic: bool` field (auto/manual turrets carry no explicit `subtype` field; only plasma-collectors do).

#### Auto turrets (`automatic: true`)
- Fire on each turret's own cooldown, additively *alongside* primaries (auto turrets do not compete with the primary slot).
- **Accuracy:**
  ```
  auto_turret_accuracy = clamp(pilot_current_accuracy × auto_turret_multiplier, 0.05, 0.99)
  ```
  - `auto_turret_multiplier` default **0.85** (`AUTO_TURRET_ACCURACY_MULTIPLIER`).
  - `pilot_current_accuracy` is the §5 result **with the thruster bonus excluded** (auto turrets are unaffected by thrusters per §7.4 — the thruster bonus is a pilot-aimed term: primaries + manual turrets only). Scanner bonus and opponent booster debuff still apply; cloak override still applies (see next bullet).
  - **Auto turrets inherit the cloak set-value.** If the target is cloaked, pilot accuracy is `cloak_set_value` (0.25 default), so auto turrets fire at ~0.2125 (= 0.25 × 0.85), re-clamped.
  - **Implementation note:** the resolver computes two pilot-accuracy values per tick — `pilot_primary_acc` (full §5, with thruster) used for primaries and manual turrets, and `pilot_turret_acc` (§5 minus thruster) used for auto turrets and any future turret-class accuracy lookup.
- **One accuracy value shared across all auto turrets on a ship** — no per-turret variation. An 8-turret battlecruiser computes one value per tick and applies it to all 8 turret shots.

#### Manual turrets (`automatic: false`)
- **Range-driven gap-closer — mutually exclusive with primaries by RANGE, not by a mode flag.** Each tick, a combatant's manual turrets are eligible to fire ONLY while **no primary weapon is in range**: eligibility = `any(current_distance ≤ pw.range_m for pw in effective_primaries)` is false (`combat_resolver.py`, phase-3 turret evaluation). The instant any primary comes into range, primaries take over and manual turrets go inert. **Primary cooldown state is irrelevant to the switch** — an in-range primary that is still reloading silences manual turrets all the same.
- **Primaries are never suppressed.** Primaries evaluate every tick behind their own per-weapon gates only (cooldown ready + `current_distance ≤ range_m`, §6.1); manual-turret activity never gates them. Auto turrets are likewise unaffected by the switch — they always fire on their own cooldown in either phase.
- **Practical firing windows:** the initial approach (before the longest-range primary closes to range), after a shock-blast distance reset (§2), and while a booster push holds the gap beyond primary range (§7.3). A ship with **zero primaries** uses its manual turrets for the whole fight (each turret still subject to its own range gate + cooldown).
- **Accuracy when firing — treated as a primary.** Each manual turret fires at `pilot_primary_acc` (full §5 layered formula, including the thruster bonus; the cloak override still applies if the target is cloaked). The 0.85 auto-turret multiplier does **NOT** apply. Range gate per §6.1 (`current_distance ≤ range_m`).
- **Cooldown — independent per turret.** Each manual turret runs its own `loading_speed_ms` cooldown (resets on fire, hit OR miss; decrements every tick, including ticks where the turret is inert because a primary is in range — mirroring primaries, which keep decrementing during turret windows). A ship with N manual turrets in a turret window fires up to N shots per cycle, each rolled independently against `pilot_primary_acc`.
- **PrimaryWeaponMod does NOT apply** to manual turrets (§7.8 excludes all turrets — auto and manual).
- **RNG draw order (determinism):** within the phase-3 turret evaluation, C1 auto-turrets, C2 auto-turrets, C1 manual-turrets, C2 manual-turrets (each group in insertion order).

##### Schema / data-model surface
**There is no mode flag.** The former ship-wide `manual_turret_mode: bool` is REMOVED everywhere — from `ShipLoadout` (`combat_models.py`), the resolver's `_CombatantState`, `LoadoutBuilder.from_player()` / `from_criminal_ship()`, the `PlayerShip` ORM model, and the `player_ships` DB column (dropped by migration `0018`). The primary-vs-manual-turret switch is computed per tick from range alone — there is no per-fight choice, no UI command, and nothing to persist.

#### Plasma-collector turrets (`subtype: "plasma-collector"`, `dps: 0`)
- **Inert in combat.** Equippable for fidelity; produces no effect.

---

## 7. Modules

> **Naming convention:** §7 prose uses friendly names (Cloak, Booster, Repair Bot, Thruster, etc.). The canonical SQLAlchemy STI discriminator (`Item.type` value, e.g. `CloakModule`, `RepairBotModule`) for every module class referenced below is listed in §10's mapping table. When this spec promotes to implementation tasks, code identifiers come from §10.

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
- **Activation:** HP thresholds **66 % / 33 %**. **No per-fight activation cap** (the legacy 2-activation cap was removed in the Thread-5 combat-chain change) — thresholds are **re-armable** (see §8). The only gates are *off cooldown* AND *not already active*, plus the **no-activate-while-invuln** guard below.
- **Trigger rule:** activates iff off cooldown AND not already active at the threshold crossing; missed threshold = skipped (but stays re-armable — it can fire on a later downward re-cross once HP recovers above it and cooldown clears). Cooldown timer starts at *effect expiry*.
- **No-activate-while-EmergencySystem-invuln (Thread-5 guard):** the cloak is **skipped entirely** while the ship's EmergencySystem invuln window is open (`invuln_remaining_ms > 0`) — accuracy reduction is wasted during invuln. The cloak instead covers the vulnerable post-ES recovery via the **emergency-end chain** (§7.7 Trigger B). This guarantees cloak never co-activates with ES.
- **Duration:** per-module `effect_duration_ms` from wiki (U'tool = 10 s, Sight Suppressor II = 20 s, Shadow Ninja = 40 s). **Phase-1: all cloaks share the same `cloak_set_value`; tiers differ by duration only.**
- **Cooldown:** per-module `loading_speed_ms` from wiki.
- **Built-in cloaks (Scimitar + Specter):** these ships carry an implicit U'tool cloak (`builtinModules: ["U'tool"]`) that DOES function in combat — same mechanic, duration, cooldown, HP-threshold activation. The built-in does NOT count against `max_modules` (off-slot). **Supersession rule:** if the pilot equips a cloak module, the equipped one wins:
  ```
  effective_cloak = equipped if has_equipped else builtin
  ```
  This is the only built-in / equipped supersession case in Phase-1 (§10). The same rule generalises to any future built-in-vs-equipped collision on a unique-equip combat module: equipped wins over built-in; built-in still functions when no equipped instance is present.

### 7.3 Boosters
- **Two simultaneous effects** while active:
  - **(a) Distance push** — formula in §2 (uses `effect_pct`).
  - **(b) Opponent accuracy debuff** — formula in §5: `debuff_pp = effect_pct × k_boost`, default `k_boost = 0.10`.
- **Activation:** HP thresholds **80 % / 60 % / 40 % / 20 %**. **No per-fight activation cap** (the legacy fixed-count cap was removed in the Thread-5 combat-chain change) — thresholds are **re-armable** (see §8); cooldown is the sole rate limiter. The booster may now activate more times per fight than the old fixed cap allowed (intended).
- **Trigger rule:** universal HP-threshold rule (§8) — off cooldown AND not already active. ADDITIONALLY, the booster activates off the **emergency-system-activate chain** (§7.7 Trigger A): when this ship's ES fires, the booster also activates if off cooldown, to help reposition during the invuln window.
- **Booster-user can still fire during boost** (accepted simplification; mirrors GoF2 base behavior).
- Per-module debuff at default `k_boost`: Linear 6 pp / Cyclotron 8 pp / Synchrotron 16 pp / Me'al 20 pp / Polytron 30 pp.

### 7.4 Thrusters
- **Attacker-side primary-accuracy bonus** (the equipping ship's handling makes *itself* hit better at close range). Formula in §5.
- **NO effect on distance / closure / weapon range / rocket accuracy / auto turrets.** (Weapons that fire at the full §5 result — primaries, manual turrets (§6.3), tracking Tier-B/C missiles (§6.2) — DO receive the bonus; it is a term of `pilot_primary_acc`.)
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
- **Trigger:** evaluated at the **end of the damage-application phase** (Appendix B step 4). If, after ALL tick damage has been applied (including any overkill that pushed hull transiently negative), the combatant's hull is ≤ 0 AND the combatant has an unconsumed ES equipped → fire. Shield or armour reaching 0 does NOT trigger; only true ship-death does.
- **Effect:** hull HP clamped to **1**; the combatant enters **10 s of full invulnerability** (ALL incoming damage blocked across subsequent ticks until the window expires).
- **Multi-source same-tick:** when multiple weapons / sub-munitions / nuke self-damage all contribute to the lethal damage, the trigger still fires ONCE at end-of-phase — ES does not try to identify "the lethal hit," only whether end-of-phase hull is ≤ 0. Overkill is discarded by the clamp-to-1; it does not carry into the invuln window.
- **Regen during invuln:** continues normally (shield + hull/armour concurrently if Repair Bot equipped), subject to the dormancy rule in §3 (regen only ticks on layers below max).
- **HP at expiry:** `1 + 10 s × applicable regen rates`, capped per-layer max. Edge case (no shield, no Repair Bot) → HP = 1 at expiry.
- **Consumable:** removed from loadout after use; player must manually re-equip a spare from inventory. **Once per fight by consumption.**
- **Not an HP-threshold device:** despite being grouped with cloak/booster in §8 for activation-rule discussion, ES does NOT use the universal HP-threshold trigger rule. It fires from the lethal-damage check above, at step 4 of the tick (not step 5).

#### Chained activations (Thread-5 combat-chain — baseline behavior, NOT tunable)
The EmergencySystem lifecycle drives the booster and cloak so the two evasion tools fire when they actually help. Both chain activations are **one-shot at the trigger instant** — if the target module is on cooldown (or already active) at that instant, the chain activation is simply **lost** (no deferral, no retry); an already-active module is never refreshed nor cut short.

- **Trigger A — ES ACTIVATES → Booster.** At the tick where ES fires (step 4a), the **booster** also activates *if off cooldown and not already active*. Rationale: the booster's distance push lets the ship reposition during the 10 s invuln window; its accuracy debuff carries into the post-invuln recovery. Injected inside `_eval_emergency_system` immediately after ES fires.
- **Trigger B — ES ENDS → Cloak.** At the tick where the invuln window expires (`invuln_remaining_ms` transitions `>0 → 0`, in the Phase-1 tick-down), the **cloak** activates *if off cooldown and not already active*. Rationale: the cloak's accuracy reduction is wasted *during* invuln (all damage is already blocked) but valuable for the vulnerable post-ES recovery — hence it fires on ES **end**, never on ES start. Reinforced by the §7.2 no-activate-while-invuln guard.
- **Same-tick resolution (ES tick):** ES ✓ / Booster ✓ (if off cooldown) / Cloak ✗. This falls out of phase order (ES at step 4a runs before HP-threshold checks at step 5) plus the cloak invuln guard — no bespoke arbitration code. The booster's same-tick HP-threshold crossing is a no-op because Trigger A already activated it; the cloak's same-tick crossing is suppressed by the invuln guard and deferred to Trigger B.
- **Telemetry:** chained activations emit a `module_activation` event carrying a distinct **`trigger`** marker — `"emergency_activate"` for the booster (Trigger A) and `"emergency_end"` for the cloak (Trigger B) — to distinguish them from a normal HP-threshold crossing (which carries `trigger_hp_pct`). They still count toward `module_activations` stats under the same `module` key. See §12 event vocabulary.
- **No config flag:** this chain (and the §7.2/§7.3/§8 cap removal it depends on) is a baseline core-combat correction, deliberately exempt from the §0.1 tunable directive.
- **Edge cases:** fight ends during invuln → Trigger B never fires (G3). Booster duration (3–6 s) shorter than the 10 s invuln → the Trigger-A booster may expire mid-invuln; no auto re-fire (G5). Regen continues during invuln (§7.7 above, G1 — intended, symmetric with players).

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
- **Cadence floor — one TICK_MS.** The snapped `effective_loading_speed_ms` is floored at a single `TICK_MS`: a weapon whose loading speed snaps to 0 is treated as *continuous fire* and fires every tick, so the tick itself IS the floor by definition. There is no sub-tick cadence. (This floors only the *cadence*, not the per-shot damage — see "No floor guard" above, which remains true for `effective_damage_per_shot`.)
- **Intended model for continuous / sub-tick fire.** A weapon whose true cadence is faster than one tick must have its effect *integrated into discrete per-tick (10ms) chunks*, so that the aggregate effect summed over many ticks (e.g. ~1000 ticks) approximates the continuous effect to ~99% accuracy — rather than applying a full per-shot payload on every tick. The 10ms tick is the integration quantum; continuous fire is the limit case where the per-tick chunk is the whole per-shot payload divided across the ticks the shot would have spanned.
- **Implementation caveat (DEFERRED ENHANCEMENT).** The currently-shipped resolver floors the cadence correctly (fires every tick when the snapped speed is 0) but still applies the *full* `effective_damage_per_shot` on each such tick — the damage-chunking integration described above is not yet implemented. This is latent today: no Phase-1 seed weapon has a base `loading_speed_ms` low enough for the snapped value to reach the floor, so no shipped fight triggers it. The chunking integration is a deferred enhancement to be added if/when a fast-enough weapon is introduced.

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
| TractorBeam | Non-combat in the resolver — but **gameplay-active in PvC looting** (gates loot-pull on a bounty kill; see §15) |
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
| Cloak | 66 % / 33 % | **uncapped** (cooldown-limited; re-armable) | Universal HP-threshold rule below. Fires at Appendix B step 5. Skipped while ES invuln active (§7.2 guard); also fires off ES-end chain (§7.7 Trigger B). |
| Booster | 80 % / 60 % / 40 % / 20 % | **uncapped** (cooldown-limited; re-armable) | Universal HP-threshold rule below. Fires at Appendix B step 5. Also fires off ES-activate chain (§7.7 Trigger A). |
| Thruster | — (passive) | — | Always active when `current_distance < 750 m`. No activation event. |
| EmergencySystem | End-of-damage-phase hull ≤ 0 (not a %) | 1 (consumable) | NOT an HP-threshold device. Fires at Appendix B step 4 (after damage applies, before clamp). Drives the booster/cloak chain (§7.7). See §7.7. |

**Universal trigger rule (Thread-5):** at any HP-threshold crossing, the device activates **iff off cooldown AND not already active**. There is **no per-fight activation cap** and **no one-shot threshold consumption** — thresholds are **re-armable**: a threshold that is skipped (cooling or already active) can fire on a *later* downward re-cross, once HP recovers above it (via regen) and cooldown clears. Cooldown is the sole rate limiter. Crossing detection still uses `prev_hp_pct` (a threshold can't re-cross downward without first recovering above it → self-regulating). Cooldown timer starts when **effect expires**, NOT when activated. (The legacy fixed-count caps and the one-shot threshold-consumption tracking were both removed; an `activation_count` is retained for telemetry only and never gates.)

**HP-percent definition:** the percent that thresholds are evaluated against is the **sum-of-layers / sum-of-maxes** across all three HP layers:

```
hp_percent = (current_shield + current_armour + current_hull)
           / (shield_max + armour_max + hull_max)
```

A ship without shield modules has `shield_max = 0` and the formula degrades naturally. A threshold is **crossed** when `hp_percent` transitions from above the threshold to at-or-below it on a single tick (Phase-1 starts every combatant at 100%, so all thresholds are crossable from above). Crossings are evaluated at Appendix B step 5 (post-damage HP), C1-before-C2 within the phase.

**Initial state at tick 0:** all HP-threshold module cooldowns start at `0` — the device is ready to activate on the *first* qualifying threshold crossing without any warmup delay. (Combatant-init concern per §1.)

Per-activation sequence:
```
trigger → run for duration_ms → cooldown begins → cooldown lasts loading_speed_ms → eligible at next threshold crossing
```

---

## 9. Fight termination

- **Hard cap:** 18,000 ticks (3 simulated minutes).
- **One side dead at end of tick (`reason: hp_depleted`):** the surviving side wins. Per Appendix B step 4 → step 8, damage is fully applied before the termination check, so HP ≤ 0 at end-of-tick = dead.
- **Both sides dead at end of tick (`reason: mutual`):** draw. Same reward semantics as a time-cap stalemate, but logged distinctly so battle history reflects *how* the fight ended.
  - **PvP:** draw. Both players keep credits; no rewards.
  - **PvC:** draw, criminal escapes (mirrors the time-cap PvC path — new system selected along the route; hunt-checks reset; reuse the existing loss-path flow when coding).
- **Time-cap, both alive (`outcome: stalemate`, `reason: time_cap` — same value in the resolver AND in `data.summary`):**
  - **PvP:** draw. Both players keep credits; no rewards. **No HP-ahead tiebreak** — whoever is "winning" on HP at the cap does NOT win; `winner` is `null`.
  - **PvC:** draw, criminal escapes — new system selected along the route; hunt-checks reset. (Reuse the existing loss-path flow when coding.)

**`outcome` × `reason` matrix** (§12 `data.summary` fields):

| `outcome` | `reason` | When |
|---|---|---|
| `win` | `hp_depleted` | exactly one side at HP ≤ 0 at end of tick |
| `stalemate` | `mutual` | both sides at HP ≤ 0 on the same tick |
| `stalemate` | `time_cap` | tick == `MAX_FIGHT_TICKS`, both alive |

Reward / route-progression handling for `stalemate` is identical across `mutual` and `time_cap` — the reason field is for log granularity only.

---

## 10. Modules referenced in combat (code-identifier mapping)

This section maps every module class the combat spec mentions in §7 to its SQLAlchemy STI discriminator (`Item.type`) so that implementation tasks reference the correct identifier strings. Spec prose uses friendly names; this table is the canonical translation.

**Why "unique-equip" appears in the mechanics:** several combat formulae throughout §7 talk about "the cloak," "the booster," "the thruster" as singular things. That singularity is a *mechanical assumption* this spec makes when deriving behavior (one cooldown, one supersession rule, one set of HP-threshold gates per fight). It currently matches loadout-builder behavior, but this spec is *describing* what mechanics assume — it is **not** responsible for *enforcing* equip rules. The loadout builder is a separate process; see "Loadout contract" below.

| Friendly name | `Item.type` | Mechanics assumes unique? | Section |
|---|---|---|---|
| Shield | `ShieldModule` | Yes | §7.5 |
| Armour | `ArmourModule` | Yes | §3, §7 |
| Repair Bot | `RepairBotModule` | Yes | §7.6 |
| Thruster | `ThrusterModule` | Yes | §7.4 |
| Cloak | `CloakModule` | Yes (+ built-in supersession case — see §7.2 and below) | §7.2 |
| Booster | `BoosterModule` | Yes | §7.3 |
| Scanner | `ScannerModule` | Yes (combat scanner active; plasma scanner shares the class but is inert in combat) | §7.1 |
| EmergencySystem | `EmergencySystemModule` | Yes (consumable, once per fight) | §7.7 |
| PrimaryWeaponMod | `PrimaryWeaponModModule` | Yes | §7.8 |
| GammaShield | `GammaShieldModule` | Yes (Phase-1 inert — no rad-damage source) | §7.9 |
| RepairBeam | `RepairBeamModule` | Yes (Phase-2 deferred) | §7.10 |
| ShieldInjector (Phoenix SIS) | `ShieldInjectorModule` | Yes (Phase-2 deferred) | §7.10 |
| TransfusionBeam | `TransfusionBeamModule` | Yes (Phase-2 deferred) | §7.10 |

Modules not referenced by the combat resolver (Cabin, Compressor, JumpDrive, MiningDrill, Signature, SpectralFilter, TimeExtender, TractorBeam) live entirely in the loadout-builder's domain and are out of scope for the **combat-resolver** part of this document. **`TractorBeam` is not inert overall** — it is the loot-pull gate for the PvC looting system (§15); it carries no combat effect, but a bounty kill reads the winner's equipped `TractorBeamModule` to roll loot.

### Built-in cloak supersession (the one special case)
The Scimitar and Specter ships carry an implicit U'tool cloak as a built-in (off-slot — does not consume a regular module slot). Combat treatment:

- **No cloak equipped** → the built-in U'tool is the active cloak. Same mechanic as any equipped cloak (§7.2): set-value 0.25, HP-threshold activations at 66 % / 33 %, U'tool's 10 s duration and cooldown.
- **Equipped cloak present** → the equipped cloak supersedes the built-in. Combat uses only the equipped cloak's stats; the built-in is bypassed for the fight. (Canonical statement: `effective_cloak = equipped if has_equipped else builtin`.)

This is the only built-in / equipped supersession case in Phase-1. The same rule generalises to any future built-in-vs-equipped collision on a unique-equip combat module — combat picks the equipped instance when present, otherwise falls back to the built-in.

### Loadout contract
The combat resolver consumes a baked `ShipLoadout` from the loadout builder and **does not re-validate** equip rules, slot counts, or any other loadout-construction invariants. The loadout builder is a separate process (out of scope here) and is assumed to produce a well-formed loadout. If a malformed loadout reaches combat, behavior is undefined — that is an upstream invariant violation, not a combat-resolver concern.

---

## 11. Phase-1 scope summary

**In scope:** primary, secondary (rocket / missile / cluster-missile / nuke / shock-blast), turret (auto + manual); shields, armour, repair-bot, thrusters, cloaks, boosters, scanners, EmergencySystem, PrimaryWeaponMod; tick-based simulation, distance, HP layers, regen, HP-threshold activations, EmergencySystem invuln. **PvC looting** (loot-pull on a bounty kill, gated by an equipped TractorBeam) ships alongside Phase-1 combat — see §15.

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

- **Key-presence policy for `module_activations` / `secondary_fired` — SPARSE.** Both objects carry only the keys that actually fired (a count > 0); modules/subtypes that never fired are omitted, not present-at-0. The outer object is **always present** — an empty `{}` when nothing of that class fired.

### `combat_log` table schema

| Column | Type | Null? | Notes |
|---|---|---|---|
| `id` | `Integer` PK autoincrement | no | The only handle. No separate UUID. |
| `guild_id` | `BigInteger` | no | Discord snowflake (same type as `Bounty.guild_id`). |
| `context` | `String(20)` | no | One of `duel` / `bounty_pvc` / `bounty_bonus`. `duel` = PvP duel (`duel_service.accept_duel`). `bounty_pvc` = Silver+ tier bounty gate (`bounty_service.check_system` — combat win required to collect base reward). `bounty_bonus` = Bronze tier post-collection bonus combat (both the auto-run path in `bounty_service.check_system` and the manual `POST /bounties/combat-bonus` endpoint — combat result decides only the 2× reward multiplier, not collection). The combat resolver itself is `loadouts in → FightResults out` and has no awareness of reward semantics — the `context` value is set by the caller when invoking `CombatLogService.persist()`. |
| `combatant1_name` / `combatant2_name` | `String(255)` | no | Display name. For PvC the NPC side holds the criminal / bounty name. |
| `combatant1_user_id` / `combatant2_user_id` | `BigInteger` | yes | Discord user id (matches `DuelRequest.challenger_id`). **NULL ⇒ that side is an NPC.** |
| `winner_name` | `String(255)` | yes | NULL on stalemate. |
| `is_stalemate` | `Boolean` | no | |
| `data` | `JSON` | no | Whole log object (schema below). Generic `JSON` per existing convention — never queried internally. |
| `created_at` | `DateTime(timezone=True)` default `now(UTC)` | no | **Retention key.** |

- **Invariant:** at least one `combatant{1,2}_user_id` is non-NULL — NPC-vs-NPC never occurs. The persist layer may assert this.
- **C1/C2 assignment rule:** for duels, challenger = C1, opponent = C2. For PvC, player = C1, NPC (criminal/bounty) = C2 (NPC always gets `combatant2_user_id = NULL`). Same convention drives Appendix B's tick processing order — combatant slots are stable across the schema, the resolver, and the timeline.
- Combatant identity + outcome are **projected to columns** for cheap lookup/listing; the authoritative per-combatant detail lives in `data.summary`.
- **Size is bounded by retention, not by truncation.** The timeline is stored whole.

### `data` blob — internal schema

```jsonc
{
  "schema_version": 1,
  "summary": {
    "outcome": "win",                           // win | stalemate
    "reason": "hp_depleted",                    // hp_depleted | time_cap | mutual
    "duration_ticks": 8421,
    "winner": "Specter",
    "combatants": {
      "1": {
        "name": "Wraith", "ship": "Specter",
        "start_hp": { "shield": 120, "armour": 300, "hull": 200 },
        "final_hp": { "shield": 0,   "armour": 0,   "hull": 140 },
        "damage_dealt": 620, "damage_taken": 480,                    // from the per-event `absorbed` field — HP actually removed, overkill excluded
        "shots_fired": 240, "shots_hit": 168, "accuracy": 0.70,
        "module_activations": { "cloak": 2, "booster": 3 },          // lowercase_snake_case keys per §0.3; embed layer renders friendly names
        "secondary_fired":    { "rocket": 12 },
        "secondary_rounds_by_weapon": { "Intelli Jet": 4 }           // CI-16: sparse, by weapon NAME — feeds post-fight ammo write-back
      },
      "2": { /* …same shape… */ }
    }
  },
  "timeline": [ /* CombatEvent rows, in processing order */ ],
  "key_events": [ /* P4-T7a: precomputed key-moment subset, written at persist time; legacy rows without it fall back to on-read extraction */ ],
  "metadata": { "tick_ms": 10, "total_ticks": 8421, "resolver": "tick_v1", "pvc_damage_reduction": 0.33 }
}
```

> **Accuracy-counting caveat:** `shots_hit` increments only on `weapon_fire` events with `hit: true`. Cluster-missiles (condensed event, `hits` count instead of `hit`) and nukes (no accuracy roll) increment `shots_fired` but never `shots_hit` — so the `accuracy` field understates for cluster/nuke-heavy loadouts (a nuke-only ship reads 0.0 accuracy regardless of damage dealt).

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

> **CI-20 `side` field — ubiquitous:** every event tied to a specific combatant additionally carries `side: 1|2` (the combatant slot), so consumers can disambiguate when both ships share a name. The only events without it are global ones (`fight_start`, `fight_end`, passive-closure `distance`). The payload shapes below omit it for brevity.

| `type` | Emitted when | Example `data` |
|---|---|---|
| `fight_start` | tick 0 | `{combatants: [{name, display_name, ship, slot, hp: {shield, armour, hull}}, …], initial_distance}` (`display_name`/`slot` per CI-20) |
| `regen` | shield recharge pulse or repair-bot hull/armour pulse applies | `{layer, amount, hp_after}` |
| `weapon_fire` | a primary / secondary / turret fires (incl. miss) | Base shape `{slot, subtype, weapon, hit, accuracy}` for accuracy-roll fires. Cluster-missile, nuke, and shock-blast substitute or extend per the table below. |
| `damage` | HP applied to a target after a hit | `{amount, absorbed, breakdown:{shield,armour,hull}, hp_after, source}`. `absorbed` (T10) = HP actually removed, overkill excluded — the authoritative input to the summary's `damage_dealt`/`damage_taken`. During an active EmergencySystem invuln window (§7.7), the event still emits but with `amount: 0`, `absorbed: 0`, `breakdown` omitted, `hp_after` unchanged, and a `blocked_by: "emergency_system_invuln"` annotation. |
| `module_activation` | a discrete activation occurs — Phase-1: cloak / booster (HP-threshold crossing OR Thread-5 ES chain) or EmergencySystem (lethal-hull trigger). Passive modules (Repair Bot, Thruster, PrimaryWeaponMod, shield regen) do NOT emit this event. | `{module, trigger_hp_pct}` for a normal HP-threshold crossing (trigger_hp_pct omitted for EmergencySystem); **chained** activations instead carry `{module, trigger: "emergency_activate"}` (booster via §7.7 Trigger A) or `{module, trigger: "emergency_end"}` (cloak via Trigger B). All variants count toward `module_activations` stats under the same `module` key. |
| `cooldown_end` | a weapon or module comes off cooldown | `{system}` |
| `layer_depleted` | a ship's shield → 0 or armour → 0 | `{layer}` |
| `distance` | distance changes (booster push, closing, shock-blast reset) | `{from, to, cause}` |
| `secondary_depleted` | a secondary weapon's last round fires (`remaining_ammo` → 0; CI-16) | `{weapon, subtype}` — post-fight auto-unequip signal |
| `fight_end` | terminal | `{winner, reason, duration_ticks, final_hp: {c1: {…}, c2: {…}}}` |

#### `weapon_fire` per-subtype payloads

The base `{slot, subtype, weapon, hit, accuracy}` covers per-shot accuracy-roll fires. Cluster-missile condenses to one event per fire (§6.2). Nuke and shock-blast bypass the accuracy roll and use subtype-specific shapes.

| Subtype | Payload |
|---|---|
| primary | `{slot: "primary", subtype: "primary", weapon, hit, accuracy}` |
| rocket | `{slot: "secondary", subtype: "rocket", weapon, hit, accuracy}` |
| missile | `{slot: "secondary", subtype: "missile", weapon, hit, accuracy, branch: "tier_a" \| "tier_bc"}` |
| cluster-missile | `{slot: "secondary", subtype: "cluster-missile", weapon, fired, hits, damage_per_hit, total_damage, branch, accuracy}` (`branch`/`accuracy` = the snapshot used for all sub-munition rolls) |
| nuke | `{slot: "secondary", subtype: "nuke", weapon, epicenter, window_lo, window_hi, stack_mult, d_firer, d_opponent, opponent_damage, self_damage}` (D-014 adds `window_lo`/`window_hi`/`stack_mult`) |
| shock-blast | `{slot: "secondary", subtype: "shock-blast", weapon, hit: true, accuracy: 1.0}` — may additionally carry `damage: 0` as a debug field (shock-blast deals no HP damage; additive debug fields are permitted). |
| auto-turret | `{slot: "turret", subtype: "auto", weapon, hit, accuracy}` |
| manual-turret | `{slot: "turret", subtype: "manual", weapon, hit, accuracy}` |

Phase-2/3+ deferred subtypes (ionizing-missile fire-but-noop per §6.2, etc.) emit with their declared `subtype` value and the base shape.

### Retention

- **Default: 72 hours (3 days).** Configurable via **`BOUNTYBOT_COMBAT_LOG_RETENTION_HOURS`** (env / per-guild per §0.1).
- Mechanism: extend the existing **`db_retention_default`** scheduled job (daily 03:45 UTC) with `CombatLogRepository.delete_older_than(cutoff)`, mirroring the bounty / duel / audit retention pattern.
- Retention key: `created_at`.
- Per-battle aggregate stats (damage_dealt, shots_fired, secondaries_fired per combatant, etc.) live in `data.summary` and are therefore retention-bounded — distinct from lifetime stats which are promoted onto `Player` per §13.

### Lookup pattern

The future battle-log command surfaces a per-player history. Canonical query:

```sql
SELECT id, combatant1_name, combatant2_name, created_at
  FROM combat_log
 WHERE combatant1_user_id = :player_id
    OR combatant2_user_id = :player_id
 ORDER BY created_at DESC
 LIMIT :n;
```

- `:player_id` is the Discord `user_id` of the command invoker.
- Indexes: `combatant1_user_id` and `combatant2_user_id` (single-column each — the PG planner OR-merges; a covering composite is not required at expected scale).
- Combatant names are denormalized columns (frozen at fight-end, audit-log style). No JOIN to `users` / `players` is required — and the NPC case (no Users row) is handled natively.

### Battle-log dropdown — UX requirement (IMPLEMENTED as `/combat-log`)

The battle-log command exists: gateway `/combat-log battle:<pick>` (`combatLogCog.py`), with a per-user autocomplete cache fed by a cross-service invalidate-push from `CombatLogService.persist` (both combatants, after-commit, non-fatal — D-013). The dropdown disambiguates duplicate-name fights (common for bounty caps where multiple criminals share a name) using the required entry format:

```
{combatant1_name} v {combatant2_name} ({created_at, local-formatted})
```

`combatant{1,2}_name` + `created_at` are projected to columns precisely so this dropdown query is a single indexed scan, no JOINs.

**Visibility:** `/combat-log` accepts an optional `public` flag (default `false`) — when `true`, the same embed is posted publicly instead of ephemerally; errors stay ephemeral regardless (`combatLogCog.py`). The admin variant `/admin_combat_log` (fetch any player's battle) is always ephemeral and has no `public` option.

### Recap presentation — Key Events + Recurring (v3)

The `/combat-log` recap is **presentation-only** — it never affects simulation, damage, or stored stats. It is computed by `build_recap_sections` (`services/combat_recap.py`) at persist time from the raw per-occurrence rows of `_extract_key_events` (`combat_resolver.py`), and stored as two `data` fields: **`key_events`** (chronological highlight rows) and **`recurring`** (one bullet per repeated pattern). Legacy rows missing either field re-extract from the stored timeline on read. Contract: `_extract_key_events` emits **one raw row per occurrence and never collapses** — all folding/significance logic lives in `build_recap_sections`.

**Cyclic-noise folding.** Repeated low-signal events — a weapon re-entering range, a module re-activating, a layer re-breaking — keep their **first** occurrence in Key Events; once a `(actor, pattern)` group reaches **`RECAP_COLLAPSE_MIN_RUN`** (default **3**) occurrences, the repeats fold into a single Recurring bullet (`• … ×N -> t1, t2, …`).

**Nuke detonations.** Every detonation is emitted as one raw `Nuke detonation` row carrying its `opp`/`self` damage. `build_recap_sections` then splits per `(actor, weapon)`:

- If the weapon fired **fewer than `RECAP_NUKE_SUMMARY_MIN_COUNT`** (default **3**) nukes → **all** detonations stay individual in Key Events (no significance pass).
- Otherwise compute `best = max(opponent_damage)` for that weapon. Detonations with `opponent_damage ≥ RECAP_NUKE_SIGNIFICANCE_FRACTION × best` (default **0.25**, and `> 0`) are **significant** and stay as individual Key Events. The remaining **trivial** detonations fold into one Recurring bullet `• {who}'s {weapon} low-impact detonations ×N -> …` **only when there are `≥ RECAP_COLLAPSE_MIN_RUN` of them**; a run shorter than that stays individual.

The "fewer than `RECAP_COLLAPSE_MIN_RUN` trivial → stay individual" rule is deliberate: collapsing one or two detonations saves no space, and a `×1`/`×2` summary line labelled with the weapon's **global** best damage would mislabel a weak shot with an unrelated, already-shown detonation's number (the R-583 defect). Trivial detonations route to **Recurring**, never to a mid-battle summary line in Key Events.

All four knobs are `GameConstants` config (env overrides `BOUNTYBOT_RECAP_COLLAPSE_MIN_RUN`, `BOUNTYBOT_RECAP_NUKE_SUMMARY_MIN_COUNT`, `BOUNTYBOT_RECAP_NUKE_SIGNIFICANCE_FRACTION`, `BOUNTYBOT_RECAP_GAP_FILL_S`) per §0.1.

### Public API: `CombatService.fight_ships(...)`

```python
fight_ships(
    loadout1: ShipLoadout,
    loadout2: ShipLoadout,
    *,
    context: str | None = None,           # "duel" / "bounty_pvc" / "bounty_bonus" — required when log_result=True
    log_result: bool = True,              # gates BOTH combat-log persistence AND §13 Player lifetime-stat updates
    pvc_damage_reduction: float = 0.0,    # Keith T. Maxwell bonus (§3) — 0.33 in PvC, 0.0 in PvP/preflight
    guild_config: GuildConfig | None = None,
) -> FightResults
```

**`log_result` semantics:**
- **`log_result=True`** (default, safe-by-default for real fights) — `fight_ships` runs the sim, then **internally**: (a) persists a `combat_log` row via `CombatLogService.persist(...)` keyed by `context`, and (b) applies §13 Player lifetime-stat updates for each human combatant. Returns `FightResults` with `combat_log_id` populated.
- **`log_result=False`** — `fight_ships` runs the sim and returns `FightResults` only. **No database state is touched** (no `combat_log` row, no Player stat updates, no Bounty / Inventory / etc. mutations). Used exclusively by `CombatPreflightService` for the `/promote` Monte-Carlo loop. `FightResults.combat_log_id` is `None`.

**`context` parameter validation:**
- Required (non-None) when `log_result=True`. Allowed values: `"duel"` / `"bounty_pvc"` / `"bounty_bonus"` (matches the `combat_log.context` enum).
- Ignored when `log_result=False` (preflight passes `None` or a sentinel).
- The service raises at the call boundary if `log_result=True` AND `context is None`.

**Wire-compat parameters — RETIRED (T10, done):**
- `variance_percent`, `player_armour_buff`, `SimpleTTKResolver`, and the `duel_variance_percent` / `bounty_pvc_armour_buff_factor` guild-config columns are all REMOVED (migration `0012`). The signature above is current — plus a `session: AsyncSession | None` kwarg (required when `log_result=True`). See the "Why variance is dropped" note further down for the historical rationale.

**Callsite assignment (Phase-1):**

| Callsite | `log_result` | `context` | `pvc_damage_reduction` | Notes |
|---|---|---|---|---|
| `duel_service.accept_duel` | `True` (default) | `"duel"` | `0.0` | PvP duel; persists + updates stats |
| `bounty_service.check_system` (Bronze) | `True` (default) | `"bounty_bonus"` | `0.33` | Base reward already paid; combat decides 2× multiplier |
| `bounty_service.check_system` (Silver+) | `True` (default) | `"bounty_pvc"` | `0.33` | Combat win required to collect base reward |
| `bounties.POST /bounties/combat-bonus` | `True` (default) | `"bounty_bonus"` | `0.33` | Manual-trigger endpoint for the Bronze bonus combat |
| `combat_preflight_service` (Monte-Carlo loop) | **`False`** | n/a | `0.33` | Ephemeral simulations for `/promote` gear-check (PvC); matches target-tier experience. No state touched. |

### In-memory production & `FightResults` mapping

- The tick resolver builds the full `timeline` + summary in memory during the sim:
  - `FightResults.combat_log: list[dict]` ← the timeline
  - `FightResults.metadata: dict` ← the summary
  - Both stub fields already exist on the dataclass.
- After tick-resolution, **`fight_ships` itself** (gated by `log_result=True`):
  1. Persists a `combat_log` row via `CombatLogService.persist(...)` using the caller-supplied `context`. Returns `combat_log_id` on the `FightResults`.
  2. Applies §13 Player lifetime-stat updates on the human combatant(s).
- The **caller** then:
  - Receives the `FightResults` (with `combat_log_id` populated if `log_result=True`, `None` otherwise).
  - Puts `{summary…, combat_log_id}` into its own response payload (e.g. `combat_result` dict). The bulky `timeline` is **NOT** sent on the fight response — only the `combat_log_id`, which the player can later use to fetch detail.
  - Applies whatever **reward / game-state logic** belongs to that caller (credit transfer for duels, bounty collection for Silver+ gate, 2× multiplier for Bronze bonus, etc.). The combat service is `loadouts in → FightResults out + maybe a persisted log + maybe stat updates`. Reward semantics are out of scope here.

### Legacy `FightStats` wire-compat

- **Legacy `FightStats` (`raw_hp` / `varied_hp` / `raw_dps` / `varied_dps` / `ttk`) stay populated** for wire compatibility with existing consumers:
  - `varied_hp` ← effective start HP. **The tick resolver does NOT apply `variance_percent`** (see note below), so `varied_hp = raw_hp` for new fights. The two fields stay distinct only because legacy consumers may still read both.
  - `varied_dps` ← `damage_dealt / duration_s`
  - `ttk` ← `duration_s` for the loser / `None` for the winner

> **Why variance was dropped (historical):** the `variance_percent` parameter (sourced from `DUEL_VARIANCE_PERCENT`) was a TTK-era smoothing layer used by `SimpleTTKResolver` to add fight-to-fight variability on top of an otherwise-deterministic time-to-kill calculation. The tick resolver derives all randomness from per-shot accuracy rolls (one uniform draw per weapon fire) plus the nuke epicenter draw (§6.2) — that intrinsic RNG is the source of fight variance, so a separate `variance_percent` would compound randomness without giving anything back. **Retirement is complete (T10):** `SimpleTTKResolver`, `variance_percent`, and `DUEL_VARIANCE_PERCENT` are removed; `varied_hp = raw_hp` always (the legacy `FightStats` fields remain populated for wire-compat only).

---

## 13. Player-profile stat promotion — mechanism

Aggregate **lifetime** combat metrics are promoted onto the **`Player` record** by the combat processor — **NOT** stored in `combat_log`. This is a handler inside the combat-service code that mutates the `Player` object after a fight; the log tables are unaffected.

**Gated by `log_result`:** stat updates run inside `fight_ships` only when `log_result=True` (§12 Public API). The preflight Monte-Carlo loop passes `log_result=False` and therefore does NOT increment any Player counters — its 20 ephemeral simulations have zero impact on lifetime stats.

**Phase-1 locked counter set** — 3 new `Player` columns (all `Integer`, `default=0`):

| Column | Increments when… |
|---|---|
| `total_fights` | post-fight (any fight the player participated in) |
| `total_nukes_fired` | per nuke fire event (uses §6.2 Nuke mechanic) |
| `total_module_activations` | per discrete activation of an HP-threshold-gated or consumable module — Phase-1: **Cloak** (HP-threshold), **Booster** (HP-threshold), **EmergencySystem** (lethal-hull-damage trigger, consumable). **Passive modules are NOT counted** — Repair Bot, Thruster, PrimaryWeaponMod, and shield regen are always-on and produce no `module_activation` event. |

All 3 are **bounded-per-fight** — a single fight contributes a small finite count, so headline lifetime numbers stay meaningful rather than drifting into uninteresting-big-number territory. Existing counters (`bounty_wins`, `duel_wins`, `duel_losses`, `duel_credits_won`, `duel_credits_lost`, `lifetime_credits`, `systems_checked`, `xp`, `prestige_count`) are unchanged.

- The combat processor increments these on the `Player` row(s) for any human combatant as part of the post-fight update. NPC side has no Player row → skipped.
- Columns landed via migration `0011` (IMPLEMENTED).

**Intentionally NOT tracked at Player level** (revisit in Phase-2+ if a leaderboard feature lands): per-subtype shot breakdowns (rocket / missile / cluster), `total_shots_fired`, `total_secondaries_fired`, any `total_damage_*` family, `bounty_losses`. Per-subtype detail is derivable from `combat_log` while inside the retention window (§12).

---

## 14. Downstream sync — item-detail embeds

Phase-1 combat-spec corrections to seed-data structure must be reflected in user-facing item-detail embeds (the embed shown when a player views an individual weapon/module, typically via inventory/ships flows). The embed is the only surface where a player sees raw weapon stats; it is the canonical place to disambiguate physical vs EMP damage and to surface cluster-missile burst behavior.

**Required embed fields** (any item-detail embed that renders a weapon MUST include):

1. **EMP / physical damage distinction** — for any weapon with `emp_damage > 0` in `extra_atts`, the embed MUST surface EMP damage as a distinct labelled field, separate from physical `damage` / `damage_per_shot`. Background: seed-fix `e87db57` corrected the 3 EMP-blaster primaries (Luna/Sol/Dia EMP) from misplaced-physical to true pure-EMP (`damage_per_shot: 0`, `emp_damage: 3/5/8`). Without this distinction the embed shows "damage: 0" with no explanation, hiding the real weapon characteristic.
2. **Cluster missile `burst_count`** — for any weapon with `subtype: "cluster-missile"`, the embed MUST surface the `burst_count` value, ideally alongside per-sub-munition `damage` AND derived `total damage on full hit = burst_count × damage`. This is the only way a player can compare cluster-missile DPS to plain-missile DPS meaningfully.
3. **Nuke direct-hit + effective magnitude + self-damage warning** — for any weapon with `subtype: "nuke"`, the embed MUST surface (a) the direct-hit `damage` value (epicenter damage), (b) the **effective magnitude** = `magnitude_m × NUKE_MAGNITUDE_SCALE` (not the raw `magnitude_m`, which is misleading since the runtime scales it), and (c) a **self-damage warning** indicating the firer is caught in the blast at `NUKE_FRIENDLY_FACTOR × falloff_damage`. Without these the player has no way to reason about the risk/reward of nuke usage (e.g. Liberator's 850 direct damage carries a ~425 point-blank self-damage cost at the D-014 factor of 0.50 — that cost MUST be visible).
4. **PrimaryWeaponMod breakdown** — for any module with `type: "PrimaryWeaponModModule"`, the embed MUST surface (a) `damage_pct`, (b) `fire_rate_pct`, AND (c) the legacy `dpsMultiplier` value (separately labelled as "net DPS shift"). Background: §7.8 honors the per-shot breakdown, but the two modules in scope (Nirai Overdrive / Overcharge) have an identical `dpsMultiplier: 1.1` despite producing mechanically distinct loadouts (lighter-faster vs heavier-slower) — surfacing all 3 fields is the only way a player sees the tradeoff at equip time rather than discovering it mid-fight.

**Implementation scope** (touches both services):
- `services/bot-core/src/api/schemas/` — extend item-detail Pydantic schema(s) so `emp_damage` and `burst_count` are explicit response fields (currently they live inside the generic `extra_atts` blob).
- `services/discord-gateway/src/cogs/` — the cog(s) that build the item-detail embed must render the new fields. Most likely the inventory / ships cogs; verify against current cog list at implementation time.

**Out of scope** for this section: the `/about` bot-info command (BountyBot version/owner info), which is unrelated to item rendering. §14 applies only to *item-detail* embeds — the per-weapon / per-module display surfaces in inventory/ships flows where a player inspects a single item. The motivation for §14 is the recent enrichment of the item data model (EMP-vs-physical damage split, cluster `burst_count`, nuke effective magnitude, PrimaryWeaponMod `damage_pct`/`fire_rate_pct` breakdown); without these embed updates the player loses visibility into mechanically-relevant fields the combat resolver now consumes.

---

## 15. PvC Looting (loot-pull)

Winning a bounty fight (player-vs-criminal) yields **loot** pulled from the defeated criminal. Looting is **PvC-only** — there is no loot in a PvP duel. The pull is gated and scaled by the winner's equipped **TractorBeam** module. This section is the canonical, locked spec of the shipped system; it follows the configuration policy of §0.1 (every numeric is a `GameConstants` default with `BOUNTYBOT_<NAME>` env + per-guild override). It graduated from the `LOOT_JOURNAL.md` design journal, which is now superseded.

> **Code map (shipped):** pure selection math in `services/bot-core/src/services/loot_engine.py`; the startup static cache + tractor resolution in `loot_service.py` (`LootService`); the cargo-load/cap helpers in `cargo_utils.py`; the spawn roll and the kill-time loot write in `bounty_service.py`; the over-cap gate in `bounty_service.py` (`/check`) and `duel_service.py` (challenge + accept); the gateway result UX in `discord-gateway` `bountyCog.py` and the pre-fight advertise line in `_shared/loadout_embed.py`. The 19 tunables live in `game_constants.py`; their per-guild columns ship in **migration `0022_loot_config_knobs`**.

### 15.1 Commodity is a first-class inventory type

`commodity` is now a **6th concrete inventory type**, alongside `ship`, `primary_weapon`, `secondary_weapon`, `turret_weapon`, `module`. It is a member of all three economy frozensets in `game_constants.py` — `CATALOG_ITEM_TYPES`, `PLAYABLE_ITEM_TYPES`, `CURRENTLY_ENABLED_TYPES` — so it is browsable, ownable, and writable to `player_inventories` (`item_type = "commodity"`). It is **concrete**, so it has no `GENERIC_TO_CONCRETE_EXPANSION` alias entry.

Commodities are **pure cargo**: never equipped (no slot / loadout), so the entire equip / unequip / swap / secondary-ammo machinery never applies. They stack by quantity and always count toward the cargo load (§15.6).

**Commodities are never stocked in a shop.** `commodity` is deliberately **absent** from `_CONCRETE_TO_CONFIG_KEY` (`shop_service.py`), the map that gates shop stocking / purchasability — so a commodity is **not buyable** and is **never written to a `GuildShop`**. The shop-refresh algorithm continues to exclude commodities. Existence validation now scans `CommodityRepository` (`inventory_service._validate_item_exists`); pricing reads the base `Item.value` column.

### 15.2 Loot trigger — a player COMBAT WIN only (kill, not capture)

Loot fires **only on a proper combat victory** — the player must *win* the fight against the criminal (`fight_results.winner_side == 1`). It does **not** fire on a loss, a stalemate / draw, a bare capture, a no-ship resolution, or any PvP duel.

- **Stalemate** (mutual death or time-cap) sets `winner_side = None` (not `0`), so the strict `== 1` test cleanly excludes it; a criminal win is `winner_side == 2`. No loot on either.
- **Bronze** bounties auto-capture (no combat required) and then run an **automatic** post-capture bonus fight. The capture is **not** a kill — loot fires only if the player **wins that automatic fight** (`combat_player_won`), never on the bare auto-capture.
- **Silver+** capture requires winning the mandatory fight, so kill = capture; loot fires on that same win.
- **No-ship branch** (defensive — unreachable in normal play, since a player can never sell their active ship and criminals always have a ship): a Silver+ no-ship resolution sets `duel_won = True` with `fight_results = None`. Loot gates on `fight_results is not None and winner_side == 1`, so the `None` case grants no loot (and no tractor ⇒ 0% anyway).
- The standalone `POST /api/v1/bounties/combat-bonus` endpoint is orphaned (no caller); loot hooks **only** the inline combat-win branch of `_process_single_bounty_check`.

The loot write is its **own player-locked transaction**, never composed into `distribute_rewards`: it re-acquires the player `FOR UPDATE` (via `add_item_to_inventory(commit=False)` + an explicit commit) and reads free cargo under that re-lock (race-safe vs concurrent buy/sell). Reward credits/XP and loot are **independent outcomes** — a loot-write failure must never roll back the bounty reward. Loot is a **player** action: it logs via `bblogger` with player / bounty / item IDs and does **not** call `audit_service`.

### 15.3 Loot chance — gated by the equipped TractorBeam

Looting requires an equipped `TractorBeamModule`. Only four beams exist, resolved to a chance via a **static map** (keyed by the beam's tech level; lock time is **not** used by the loot mechanic):

| Tractor | Tech level | Loot chance | Knob |
|---------|-----------:|------------:|------|
| AB-1 "Retractor"  | 4 | 20 % | `LOOT_CHANCE_TRACTOR_T1` |
| AB-2 "Glue Gun"   | 5 | 40 % | `LOOT_CHANCE_TRACTOR_T2` |
| AB-3 "Kingfisher" | 7 | 60 % | `LOOT_CHANCE_TRACTOR_T3` |
| AB-4 "Octopus"    | 8 | 80 % | `LOOT_CHANCE_TRACTOR_T4` |
| (none equipped)   | — |  0 % | `LOOT_CHANCE_NO_TRACTOR` |

TractorBeams are unique-equip (0 or 1 ever equipped), so there is no multi-beam tie-break. There is exactly **one** loot roll per kill (the criminal carries exactly one cargo item — §15.4): pass ⇒ the item is looted (subject to the §15.6 cargo clamp); fail ⇒ nothing.

### 15.4 Criminal cargo — rolled at spawn, advertised pre-fight

**Every** criminal spawns carrying **exactly one** loot item (100 % drop guarantee — `LOOT_DROP_CHANCE` is a fixed constant, not a tunable). The item is rolled **at spawn** (`spawn_bounty`) and persisted in the existing `Bounty.criminal_ship` JSONB under a `cargo` key — `{item_type, item_name, quantity}`. Lifecycle = the bounty row; no separate table or migration. The win-branch loot write reads this persisted cargo rather than re-rolling. There is **no criminal-side cargo clamp** (the smallest criminal ship's hold exceeds the default Band-3 max, and no criminal-side cargo enforcement exists — the player-side §15.6 clamp is the only real gate).

The carried cargo is **advertised before the fight** — the bounty spawn announcement embed and the `/criminal-loadout` embed both render a **"Loot aboard"** field showing `Nx <Item>`, so players see what is lootable before engaging. This is informational only; the actual pull still gates on the §15.3 roll and §15.6 cargo space at the win.

### 15.5 Item & quantity selection

A criminal's single loot item is chosen in three steps (pure functions in `loot_engine.py`; pools preloaded by `LootService` at startup and rebuilt only on a seed reload):

**Step 1 — choose the band** (weighted; sum 100 %):

| Band | Members | Select chance | Knob |
|------|---------|--------------:|------|
| Band 1 | all Weapons + all Modules (equipment) | 10 % | `LOOT_BAND1_SELECT_PCT` |
| Band 2 | commodities `ore_core`, `rare` | 20 % | `LOOT_BAND2_SELECT_PCT` |
| Band 3 | commodities `booze`, `technical`, `ore`, `standard`, `waste` | 70 % | `LOOT_BAND3_SELECT_PCT` |

The loot domain excludes the moot commodity subcategories `plasma` and `mission`, and excludes the modules `JumpDriveModule`, `TimeExtenderModule`, `ShieldInjectorModule`.

**Step 2 — pick the item within the band:**
- **Bands 2 & 3** — uniform RNG over the eligible commodity **item pool** (not subcategory-then-item), so a subcategory with more rows is proportionally likelier (an intentional skew that mirrors the real game). No tech-level weighting.
- **Band 1** — restrict to Weapons / Modules whose tech level is within **±`LOOT_BAND1_TL_WINDOW`** (default ±1) of the criminal's TL (the criminal TL anchor is `Bounty.tech_level`, clamped to `[MIN_TECH_LEVEL, MAX_TECH_LEVEL]` = `[1,10]`), then pick uniform random. A trivial nearest-TL fallback (rank by `|item_TL − criminal_TL|` over the cached Band-1 pool) keeps the function total if the window is ever empty.

**Step 3 — roll quantity** from the chosen band's distribution. All three bands use **one discrete-triangular `(min, mode, max)` sampler**:

| Band | `(min, mode, max)` | Shape | Mean | Knobs |
|------|--------------------|-------|-----:|-------|
| Band 1 | (1, 1, 3) | descending ramp → 50 / 33 / 17 | ≈ 1.67 | `LOOT_BAND1_QTY_{MIN,MODE,MAX}` |
| Band 2 | (4, 8, 12) | symmetric | 8 | `LOOT_BAND2_QTY_{MIN,MODE,MAX}` |
| Band 3 | (10, 16, 22) | symmetric | 16 | `LOOT_BAND3_QTY_{MIN,MODE,MAX}` |

Bands are value-inverse (cheaper class ⇒ bigger stack) so total loot value stays in a sane range.

### 15.6 Cargo cap — free-cargo gate, per-unit clamp, over-cap lockout

Cargo load is counted **per-unit**: `sum(player_inventories.quantity)` (ship cargo only; equipped gear excluded). Effective cap = base `ship.cargo` × (1 + the sum of each equipped `CompressorModule`'s bonus fraction) — compressors **stack additively**, matching the game wiki (e.g. a +25% and a +100% compressor together give +125%, not +150%). `cargo_utils.py` provides the canonical `compute_free_cargo` / `is_over_cap` helpers so the loot clamp and the over-cap gate share one definition.

**Free-cargo gate (before rolling).** If the player has 0 free cargo at the win, looting is skipped entirely (`cargo_full` outcome) — no feel-bad "passed the roll, got nothing."

**Per-unit clamp.** A looted stack is clamped to free space: if the player has room for 6 units and the criminal carried `16x booze`, the player loots 6; the other 10 are lost forever (no re-loot). For a qty-1 item this is simply loot-it-if-there's-room.

**Over-cap lockout.** A player whose load **exceeds** cap is blocked from "leaving station": the over-cap check is the **first** thing evaluated in `/duel` challenge, `/duel` accept (both parties are gated), and `/check`, rejecting with an ephemeral **`"Cargo Overloaded — NN/XX. Unable to leave station."`** (NN = current load, XX = cap). Loot can never push a player over cap (the clamp fills only *to* cap); over-cap is reached by unequipping (esp. a Compressor, which lowers max capacity) or by buying past cap (purchase is intentionally **not** cap-gated). `equip` / `unequip` / `buy` are **not** gated — only the three combat entries — so the escapes (sell, equip-Compressor) stay available while over-cap.

### 15.7 Loot result UX

Loot resolves during `/check` on a combat win, so its result renders in the **same** capture/check embed (never a separate message), as a `<beam-emoji> Loot` field (the emoji is the equipped beam's own custom Discord emoji). bot-core returns a `loot` payload on the check response — `{item_name, qty_looted, qty_total, outcome, tractor_emoji, ...}` with `outcome ∈ {looted, partial, failed, cargo_full}` (the wire `LootResult.outcome` `Literal` in `bounty_schema.py` — the no-loot/no-beam case is sent as a null `loot` field, so `none` is an internal-only outcome that never appears on the wire) — and the gateway renders the line. The displayed quantity is always shown (even `1x`):

| `outcome` | Line |
|-----------|------|
| `looted` | `Tractored 16x Booze.` |
| `partial` | `Tractored 6 of 16 Booze — cargo full.` |
| `failed` | `Tractor beam failed — nothing looted.` |
| `cargo_full` | `Cargo hold full (NN/XX) — No room for loot.` |
| *no beam / nothing looted* | *(null `loot` field on the wire — internal `none`; the Loot field is omitted entirely — no nag on a no-beam kill)* |

No loot line appears on a loss / stalemate / defeat embed. The over-cap lockout is a separate ephemeral **pre-gate** message, not part of the result embed.

### 15.8 Looted item disposal

Looted Weapons / Modules keep their class and may be equipped, sold, or given. Disposal uses the existing commands:
- **Sell.** A **commodity** sells as a **pure sink**: payout = `Item.value × quantity × LOOT_COMMODITY_SELL_FRACTION` (default 100 %), the item is destroyed and **no shop is touched**. A **Weapon / Module** sells to the player's current-tier `GuildShop` as before (the item enters store stock). This divergence is the one net-new branch in `sell_item`.
- **Give.** `/give` now carries a `quantity` argument (was hard-coded to 1); commodity gives are enabled by the `"commodity"` member added to the `TransferItemRequest.item_type` `Literal`. Item transfers are cargo-only by construction (never touch equipped slots).
- **Equip caveat.** A looted secondary-weapon stack, on equip, moves the *whole* stack into the `secondary_ammo` sidecar (existing behaviour) — `2x` of a secondary becomes 2 ammo rounds, not 2 spare launchers.

### 15.9 Configuration

All 19 loot tunables are listed in Appendix A (the `LOOT_*` block). Each is a `GameConstants` scalar with a `BOUNTYBOT_<NAME>` env override and a per-guild `guild_configs` column added by **migration `0022_loot_config_knobs`** (additive, nullable, inspector-guarded; `NULL` ⇒ the `GameConstants` default). They are exposed for live tuning via `/admin_config_constants`. `LOOT_DROP_CHANCE` is the lone exception — a **fixed** 100 % constant with no env, no column, and no override (the tractor roll is the sole loot-frequency lever).

---

## 16. Shop module spawn (bucketed draw)

Tier shops auto-refresh their stock on a timer (`shop_refresh_executor` → `ShopService.refresh_shop`). The **module** category of that draw is rebalanced to fix the long-standing complaint that **good armour / shields almost never spawn in shops**: top-tier defensive gear was unreachable, and uniform-within-TL sampling drowned the useful combat modules under filler/junk. This section is the canonical, locked spec of the shipped rebalance; it follows the configuration policy of §0.1 (the one new numeric is a `GameConstants` default with `BOUNTYBOT_<NAME>` env + per-guild override). It is shop-spawn balance only — it changes **what** modules a refresh draws, never how shops are read, priced, purchased, or sold (those are unchanged; commodities remain unstockable per §15.1).

> **Code map (shipped):** the TL roll + per-refresh probability resolution in `services/bot-core/src/services/shop_service.py` (`refresh_shop`); the bucketed module draw + step-down in the same file (`_get_random_item_by_tech_level`, the `item_type == "module"` branch); the actual-TL row write via `_get_item_tech_level`; the bucket membership frozensets + the `SHOP_COMBAT_MODULE_PROB` default in `game_constants.py` (with a disjoint + covers-all-21-module-types `assert` drift guard). The one tunable ships its per-guild column in **migration `0023_shop_combat_module_prob`** and is exposed in the admin config override API (`api/routers/config.py`, `config_schema.py`).

### 16.1 Tech-level ceiling raised 9 → 10

> **Superseded in part by §16.6 (2026-08).** The batch TL is no longer a flat `randint` — it is a two-bucket draw governed by `SHOP_BANDED_TL_WEIGHT`. The `[1, 10]` **range** below is unchanged and still reachable; only the **distribution** within it changed.

`MAX_TECH_LEVEL` is **10**. `refresh_shop` rolls the batch tech level as `random.randint(MIN_TECH_LEVEL, MAX_TECH_LEVEL)` (= `[1, 10]`) and `force_tech_level` (the `/admin_refresh_shop` override) accepts `1..10`. Previously the shop ceiling was capped at 9, so TL10 gear — the **best armour and shields** (`T'yol`, `Particle Shield`, `Fluxed Matter Shield`) — could **never** appear in any shop, even though platinum-division criminals already field it. The downward TL band that fans each item below the batch TL (§16.3) is unchanged.

### 16.2 Module buckets — junk removed, combat vs filler split

The 21 module types in the catalog are partitioned into **three disjoint buckets** (constants in `game_constants.py`; an `assert` enforces disjoint + covers-all-21 on import). Membership is sourced conceptually from the criminal loadout classification (`bounty_service` priority order) with two shop-specific overrides: **TractorBeam is promoted into COMBAT** (it gates PvC loot — §15.3 — so it is first-class shop stock), and **JUNK is excluded from the shop pool entirely**.

| Bucket | In shop? | Module types |
|--------|----------|--------------|
| **JUNK** (`SHOP_JUNK_MODULE_TYPES`) | **No** — removed from the pool | `TransfusionBeamModule`, `ShieldInjectorModule`, `TimeExtenderModule`, `JumpDriveModule` |
| **FILLER** (`SHOP_FILLER_MODULE_TYPES`) | Yes — drawn with prob `1 − SHOP_COMBAT_MODULE_PROB` | `GammaShieldModule`, `SpectralFilterModule`, `RepairBeamModule`, `SignatureModule`, `MiningDrillModule`, `CompressorModule`, `CabinModule` |
| **COMBAT** (`SHOP_COMBAT_MODULE_TYPES`) | Yes — drawn with prob `SHOP_COMBAT_MODULE_PROB` | `ScannerModule`, `ArmourModule`, `ShieldModule`, `CloakModule`, `BoosterModule`, `EmergencySystemModule`, `RepairBotModule`, `PrimaryWeaponModModule`, `ThrusterModule`, `TractorBeamModule` |

### 16.3 Two-stage bucketed draw with empty-bucket step-down

Each module slot in a refresh is filled in two stages (replacing the old "uniform random over every module at the band TL"):

1. **Pick a bucket.** With probability `SHOP_COMBAT_MODULE_PROB` (default **0.75**) the slot draws from **COMBAT**, otherwise (prob `1 − 0.75` = 0.25) from **FILLER**. JUNK is already excluded from the pool. The probability is resolved **once per refresh** from the guild config (`resolve_constant(config, "shop_combat_module_prob", …)`) and threaded into every module draw of that refresh.
2. **Uniform within the bucket at the band TL.** Pick uniformly at random among the chosen bucket's modules whose `tech_level` equals the per-item band TL (the band TL comes from `_select_item_tech_level` — §16.4).

**Empty-bucket step-down.** If the chosen bucket has **no** module at the band TL, the draw steps **down one TL at a time** (`band_TL, band_TL−1, … , 1`) until it finds a non-empty (bucket, TL) pair, and returns a uniform pick from the first one found. In the current catalog only **COMBAT @ TL9 is empty**, so a COMBAT roll banded to TL9 steps down to TL8. If no TL in the bucket has any module (cannot happen in the shipped catalog), the draw returns `None` and the slot is skipped.

**Row-TL correctness.** The persisted `guild_shops.tech_level` and `price` reflect the **actual drawn item's** TL after any step-down — **not** the band TL. `refresh_shop` resolves the module row's TL via `_get_item_tech_level` (warm static cache, no DB round-trip), so a module that stepped down from TL9 to TL8 is stored and listed as T8 at its real T8 price. (Pre-rebalance, modules were stored at the band TL, which could mislabel and mis-price a stepped-down draw.)

### 16.4 Unchanged: the downward TL band

The per-item downward fan (`_select_item_tech_level`, weights **0.7 / 0.2 / 0.1** over band TL `T / T−1 / T−2`, floored at 1) is **unchanged** by this rebalance and applies to all categories, not just modules. The bucketed draw operates *at* whatever band TL this kernel produces; the step-down in §16.3 is a separate, downward-only correction that fires only when the chosen bucket happens to be empty at that band TL.

### 16.5 Configuration

`SHOP_COMBAT_MODULE_PROB` is the single tunable introduced here — listed in Appendix A. It is a `GameConstants` float (default **0.75**) with a `BOUNTYBOT_SHOP_COMBAT_MODULE_PROB` env override and a per-guild `guild_configs.shop_combat_module_prob` column added by **migration `0023_shop_combat_module_prob`** (additive, nullable; `NULL` ⇒ the `GameConstants` default). It is exposed for live tuning via the admin config override API (PUT/GET/reset, `[0.0, 1.0]`-validated). The bucket-membership frozensets are **structural game data, not tunables** — they are not per-guild-overridable and are guarded by the import-time disjoint+coverage assertion. The TL ceiling (§16.1) is governed by the existing `MAX_TECH_LEVEL` constant.

### 16.6 Batch TL banding — two-bucket draw (2026-08, PR #80)

Amends §16.1. The batch TL was uniform over `[1, 10]` regardless of tier, so a Bronze shop could stock TL10 gear its players cannot fly while a Platinum shop stocked Bettys. `refresh_shop` now delegates to `ShopService._select_shop_tech_level(tier, config)`, which picks **one of two buckets** and then draws a TL from it:

| | Bucket | Drawn how | Chance |
|---|---|---|---|
| **1** | The tier's division band | `game_maths.pick_division_tech_level(tier, division_max_tl)` — the identical draw `spawn_bounty` uses for that division's criminals: the §16.4 kernel centred on `DIVISION_TL_CENTERS[tier]`, capped at `DIVISION_MAX_TL[tier]` | `SHOP_BANDED_TL_WEIGHT` |
| **2** | Every valid TL | `random.randint(MIN_TECH_LEVEL, MAX_TECH_LEVEL)` — the pre-amendment §16.1 behaviour | `1 − SHOP_BANDED_TL_WEIGHT` |

**Locked properties:**

- `SHOP_BANDED_TL_WEIGHT = 0.0` reproduces §16.1 exactly; `1.0` tier-matches every refresh. Both endpoints are supported configurations, not degenerate cases.
- Bucket 2 is a **superset** of bucket 1, so the observed share of tier-matched shops always exceeds the configured weight. Intended.
- Bucket 2 is **not** a fallback. It is the only route by which a low tier reaches item classes the catalog stocks solely at high TL — turrets exist at TL 5/6/9 only, so at weight `1.0` a Bronze shop can never stock one. It also preserves the reward for players who save credits.
- `force_tech_level` (admin refresh) bypasses both buckets, unchanged.
- Bucket 1 shares **one function** with criminal spawning rather than restating the band. A future change to division centres or caps moves criminals and shops together by construction; there is no second copy to drift.
- Ships have no catalog `tech_level` column — their TL is derived from price via `ship_tech_level_for_value` (§16.3's step-down now applies to the ship branch too, matching modules).

**Configuration:** `SHOP_BANDED_TL_WEIGHT`, `GameConstants` float, default **0.7**, env `BOUNTYBOT_SHOP_BANDED_TL_WEIGHT`, resolved per refresh via `resolve_constant(config, "shop_banded_tl_weight", …)`. **No migration ships with this amendment** — the per-guild column required by §0.1 is deferred to the issue #70 override audit; until it exists, `resolve_constant` falls through to the `GameConstants` default and the knob is env/global only.

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
| `NUKE_FRIENDLY_FACTOR` | **0.50** | §6.2 (D-014; was 0.25) |
| `NUKE_RANGE_REGIME_THRESHOLD_M` | **1000** | §6.2 (D-014) |
| `NUKE_LR_NEAR_FRAC` | **0.40** | §6.2 (D-014) |
| `NUKE_CR_SHORT_M` | **600** | §6.2 (D-014) |
| `NUKE_CR_OVERSHOOT_M` | **400** | §6.2 (D-014) |
| `NUKE_STACK_FALLOFF` | **0.5** | §6.2 (D-014) |
| `PVC_DAMAGE_REDUCTION` | **0.33** | §3 (Keith T. Maxwell bonus — PvC, player side only) |
| `COMBAT_LOG_RETENTION_HOURS` | **72** | §12 |
| `SHOCK_BLAST_TRIGGER_RANGE_M` | **500** | §2 / §6.2 (shock-blast fires only inside this range) |
| `COMBAT_LAYER_REEMIT_FRACTION` | **0.25** | §12 (CI-21 — `layer_depleted` re-emit latch clears when the layer recovers ≥ this fraction of max) |
| `CRIMINAL_SECONDARY_ROUNDS` | **{nuke: 1, missile: 5, rocket: 5, cluster-missile: 3, shock-blast: 2}** | §6.2 CI-17 (criminal per-subtype round grants) |
| `CRIMINAL_SECONDARY_MIN_DAMAGE` | **1** | §6.2 CI-17 (criminal gear-roll exclusion: damage ≤ this is skipped) |
| `LOOT_CHANCE_TRACTOR_T1` | **20** | §15.3 (AB-1 "Retractor", TL4 — loot-roll %) |
| `LOOT_CHANCE_TRACTOR_T2` | **40** | §15.3 (AB-2 "Glue Gun", TL5) |
| `LOOT_CHANCE_TRACTOR_T3` | **60** | §15.3 (AB-3 "Kingfisher", TL7) |
| `LOOT_CHANCE_TRACTOR_T4` | **80** | §15.3 (AB-4 "Octopus", TL8) |
| `LOOT_CHANCE_NO_TRACTOR` | **0** | §15.3 (no tractor equipped) |
| `LOOT_BAND1_SELECT_PCT` | **10** | §15.5 (band-select % — Weapons+Modules) |
| `LOOT_BAND2_SELECT_PCT` | **20** | §15.5 (band-select % — ore_core, rare) |
| `LOOT_BAND3_SELECT_PCT` | **70** | §15.5 (band-select % — bulk commodities) |
| `LOOT_BAND1_TL_WINDOW` | **1** | §15.5 (Band-1 ±TL window vs criminal TL) |
| `LOOT_BAND1_QTY_MIN` / `LOOT_BAND1_QTY_MODE` / `LOOT_BAND1_QTY_MAX` | **1 / 1 / 3** | §15.5 (Band-1 triangular → 50/33/17) |
| `LOOT_BAND2_QTY_MIN` / `LOOT_BAND2_QTY_MODE` / `LOOT_BAND2_QTY_MAX` | **4 / 8 / 12** | §15.5 (Band-2 triangular, mean 8) |
| `LOOT_BAND3_QTY_MIN` / `LOOT_BAND3_QTY_MODE` / `LOOT_BAND3_QTY_MAX` | **10 / 16 / 22** | §15.5 (Band-3 triangular, mean 16) |
| `LOOT_COMMODITY_SELL_FRACTION` | **1.0** | §15.8 (commodity sink payout = `Item.value` × qty × this) |
| `SHOP_COMBAT_MODULE_PROB` | **0.75** | §16.3 (shop module draw: P(combat bucket); filler = 1 − this) |
| `SHOP_BANDED_TL_WEIGHT` | **0.7** | §16.6 (shop batch TL: P(tier-banded bucket); full-range = 1 − this). Env/global only until the issue #70 per-guild column lands. |

> `LOOT_DROP_CHANCE` is intentionally **not** in this table — it is a **fixed** 100 % constant (no env, no per-guild column, no migration), per §15.4 / §15.9.

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
- **Auto turret:** `acc = clamp(pilot_turret_acc × AUTO_TURRET_ACCURACY_MULTIPLIER, 0.05, 0.99)`, where `pilot_turret_acc` = §5 layered result with thruster bonus excluded (cloak override still applies if active).
- **Manual turret:** `acc = pilot_primary_acc` (full §5 layered result, thruster included; no 0.85 multiplier). Fire-eligible only while `any(current_distance ≤ pw.range_m for pw in effective_primaries)` is false (§6.3 range-driven switch; primary cooldown state irrelevant).

### Rocket accuracy curve
```
accuracy = 0.05 + 0.55 × ((range_m − current_distance) / (range_m − MIN_DISTANCE_M))
         → clamp [0.05, 0.60]
```

### Nuke (AoE — no accuracy roll; D-014 two-regime window + yield interference)
```
# Arming delay: cooldown_remaining_ms = loading_speed_ms at fight init (nuke-only)

window(d) = [NUKE_LR_NEAR_FRAC × d, d]                          if d > NUKE_RANGE_REGIME_THRESHOLD_M
          = [max(0, d − NUKE_CR_SHORT_M), d + NUKE_CR_OVERSHOOT_M]  otherwise
            # d = current_distance at fire time

epicenter           ~ U[window(d)]                              # one uniform draw
d_firer             = epicenter                                 # firer at position 0
d_opponent          = |epicenter − current_distance|
effective_magnitude = magnitude_m × NUKE_MAGNITUDE_SCALE
stack_mult          = NUKE_STACK_FALLOFF ** prior_detonations_this_side  # per-side, per-fight

dmg(d)              = damage × (1 − min(1, d / effective_magnitude))² × stack_mult

opponent_damage     = dmg(d_opponent)
self_damage         = dmg(d_firer) × NUKE_FRIENDLY_FACTOR
```
Both ships always take damage. Accuracy / cloak / thruster / booster modifiers do NOT apply. Steerable flag ignored.

### PrimaryWeaponMod (Nirai Overdrive / Overcharge — primary weapons only)
```
effective_damage_per_shot   = round(damage_per_shot × (1 + damage_pct / 100))
effective_loading_speed_ms  = round((loading_speed_ms / (1 + fire_rate_pct / 100)) / TICK_MS) × TICK_MS
```
`damage_pct` and `fire_rate_pct` per seed `extra_atts`. Legacy `dpsMultiplier` field IGNORED by tick-resolver (kept only for current SimpleTTKResolver + embed display). No floor on effective damage. Cooldown snaps to nearest `TICK_MS`. Snapped cadence is floored at one `TICK_MS` — a weapon that snaps to 0 fires every tick (continuous fire; the tick IS the floor). Per §7.8, sub-tick fire is the intended model to be integrated into discrete per-tick chunks so the aggregate over many ticks approximates the continuous effect; the shipped resolver floors the cadence but applies the full per-shot damage each tick (damage-chunking is a deferred enhancement, latent under current Phase-1 seeds).

### Regen pulse schedule (per layer source)
- **Shield:** `+1 HP every N ticks`, `N = ceil(shield_recharge_ms / shield_capacity / tick_ms)` per shield module.
- **Repair Bot (hull + armour):** per-tick delta = `(max_hull + max_armour) × rate × (tick_ms / 1000)`, accumulated and integer-flushed. `rate` ∈ {0.025, 0.050} for Ketar I / II.

### Incoming damage reduction (PvC player buff — Keith T. Maxwell bonus)
```
applied_damage = raw_damage × (1 − PVC_DAMAGE_REDUCTION)   if pvc_fight AND recipient is player
                 raw_damage                                otherwise
```
Default `PVC_DAMAGE_REDUCTION = 0.33`. Applied per-damage-event before shield → armour → hull stacking. Covers ALL incoming damage to the player, including own-nuke self-damage. PvP and NPC-side events: unscaled.

### Tick step order (per tick)

Each numbered step below is a **phase**. Within each phase, combatant 1 (C1) is processed before combatant 2 (C2). Phases run in strict order; intra-tick log events are emitted in this exact order (phase order × C1-before-C2 within phase).

**Combatant assignment:** for duels, challenger = C1, opponent = C2. For PvC, player = C1, NPC (criminal/bounty) = C2. Same convention used by §12's `combatant{1,2}_*` columns.

1. **Decrement cooldowns** (`-= tick_ms`) for both combatants. (Independent counters — phase-internal ordering doesn't change outcomes; C1 logs first if any `cooldown_end` events emit.)
2. **Apply regen pulses** — shield + hull/armour in parallel for each combatant (independent tracks per §3). C1 pulses logged before C2's.
3. **Evaluate weapon firings** — for C1 first, then C2: compute hit/miss for every weapon with `cooldown_remaining_ms ≤ 0` AND in range AND not gated. Hits are *recorded* this phase, not applied.
4. **Apply damage** — C1's hits land on C2 first, then C2's hits land on C1. For each damage event:
   - **(i) PvC damage reduction (player-side only):** if the fight is PvC AND the recipient is the player-side combatant, scale the raw damage by `(1 − PVC_DAMAGE_REDUCTION)` (default 0.33 — Keith T. Maxwell bonus, §3). Applies to *all* sources, including the firer's own nuke self-damage. PvP and NPC-side events: no scaling.
   - **(ii) Shield → armour → hull stacking** — apply the (possibly DR-scaled) damage in the stacking order.
   HP is allowed to go transiently negative mid-phase (overkill tracking — important for cluster missiles and nuke self-damage). After all C1→C2 and C2→C1 damage has been resolved:
   - **(4a) EmergencySystem evaluation** per combatant (C1 then C2): if combatant has an unconsumed ES equipped AND end-of-phase hull ≤ 0 → fire ES, clamp hull to 1, consume the ES instance, start the 10 s invuln window (§7.7).
   - **(4b) HP clamp** per combatant: any layer still below 0 is clamped to 0 for display and for the step 8 termination check. Negative HP never persists past step 4.
5. **HP-threshold checks** — for C1 first, then C2: evaluate threshold crossings against own post-damage HP; trigger eligible HP-gated activations (cloak / booster only — EmergencySystem already resolved at step 4a per §7.7).
6. **Update distance** — passive closure OR active booster push OR shock-blast reset. Distance is a shared scalar — no C1/C2 ordering concern.
7. **Emit timeline events** for everything state-changing in steps 1–6, in the order above.
8. **Check termination** — both combatants at HP ≤ 0 → `reason: mutual`; exactly one at HP ≤ 0 → `reason: hp_depleted` (other side wins); tick == `MAX_FIGHT_TICKS` with both alive → `reason: time_cap`. See §9.

**Why fire and damage are separate phases:** step 3 only *computes* hits; step 4 *applies* them. A same-tick mutual-fire ALWAYS lands both sides' damage — neither side's lethal blow "cancels" the other side's shot at fire time. This is what makes the mutual-kill rule (§9, terminal check at step 8) fall out naturally.

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
