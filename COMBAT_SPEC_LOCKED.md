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
- **Initial state at tick 0:** all weapons enter combat fully ready (`cooldown_remaining_ms = 0`); first-tick firing is gated only by range and any other normal checks. HP-threshold module cooldowns are also `0` at tick 0 (see §8). Regen-track dormancy and damaged-start handling: see §3.
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
- **No upper distance bound.** `current_distance` is naturally bounded by per-module booster limits (`effect_pct × duration_ms`) and the 4-activation-per-fight cap on boosters; there is no synthetic `MAX_DISTANCE_M` cap. Weapons stop firing once they exceed their own `range_m`; otherwise no special behavior at extreme distances.
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
A cluster missile is **identical to a plain missile in every respect except that it releases N sub-munitions per fire instead of one**. Inherits the plain-Missile scanner-tier rule for whether tracking is active (Tier B/C → §5 accuracy; Tier A → rocket curve).

- **Accuracy snapshot semantics:** the fire-time accuracy is captured **once** per cluster fire — sourced from the pilot's §5 accuracy under Tier B/C, or from the rocket-curve evaluated against `current_distance` at fire time under Tier A. ALL N sub-munitions then roll independently against that single snapshot. A thruster ramp / cloak activation / distance change occurring after the cluster fires does NOT retroactively alter sub-munition rolls.
- **Damage application — per-sub-munition, sequential:** each landing sub-munition deals `damage` (per-sub-munition, NOT total) and walks the shield → armour → hull stack independently. Single-target only (no AoE — cluster missiles carry no `magnitude_m`). **All K landed sub-munitions apply** even if an earlier one drops the target to HP ≤ 0; overkill is allowed (HP can go transiently negative within Appendix B step 4). EmergencySystem evaluates against the *cumulative* end-of-phase hull, not per sub-munition (§7.7) — so a cluster that goes wildly past lethal still triggers ES at most once.
- **Combat-log condensation:** **ONE event per cluster fire** with summary fields `{weapon, fired: N, hits: K, damage_per_hit, total_damage: K × damage}`. `total_damage` reports the weapon's *swung* output (max-possible across landed sub-munitions); the actual HP absorbed (post-clamp) feeds the per-combatant `damage_dealt` rollup in §12's summary. Not N rows.
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

Weapons and modules are independent subsystems: firing shock-blast resets `current_distance` only — active cloak / booster effects continue running on their own `duration_ms`, and module cooldowns are unaffected.

### 6.3 Turret weapons

Three subtypes exist; two are combat-relevant. Discriminate using the `automatic: bool` field (auto/manual turrets carry no explicit `subtype` field; only plasma-collectors do).

#### Auto turrets (`automatic: true`)
- Fire on each turret's own cooldown, additively *alongside* primaries (auto turrets do not compete with the primary slot).
- **Accuracy:**
  ```
  auto_turret_accuracy = clamp(pilot_current_accuracy × auto_turret_multiplier, 0.05, 0.99)
  ```
  - `auto_turret_multiplier` default **0.85** (`AUTO_TURRET_ACCURACY_MULTIPLIER`).
  - `pilot_current_accuracy` is the §5 result **with the thruster bonus excluded** (turrets are unaffected by thrusters per §7.4 — thruster bonus is a primary-only term). Scanner bonus and opponent booster debuff still apply; cloak override still applies (see next bullet).
  - **Auto turrets inherit the cloak set-value.** If the target is cloaked, pilot accuracy is `cloak_set_value` (0.25 default), so auto turrets fire at ~0.2125 (= 0.25 × 0.85), re-clamped.
  - **Implementation note:** the resolver computes two pilot-accuracy values per tick — `pilot_primary_acc` (full §5, with thruster) used for primaries, and `pilot_turret_acc` (§5 minus thruster) used for auto turrets and any future turret-class accuracy lookup.
- **One accuracy value shared across all auto turrets on a ship** — no per-turret variation. An 8-turret battlecruiser computes one value per tick and applies it to all 8 turret shots.

#### Manual turrets (`automatic: false`)
- **Mutually exclusive with primary** via a ship-wide `manual_turret_mode: bool` flag on `ShipLoadout`. Auto turrets are **unaffected** by the flag — they always fire on their own cooldown regardless of mode.
  - `manual_turret_mode = false` (default — "primary mode"): primaries fire normally; **manual turrets do NOT fire** this fight (the pilot is focused on the primary). Manual turrets stay equipped but inert. Auto turrets fire as usual.
  - `manual_turret_mode = true` ("turret mode"): **primaries do NOT fire**; manual turrets fire as pilot-aimed weapons (rules below). Auto turrets fire as usual.
- **Accuracy when firing — treated as a primary.** Each manual turret fires at `pilot_primary_acc` (full §5 layered formula, including the thruster bonus; the cloak override still applies if the target is cloaked). The 0.85 auto-turret multiplier does **NOT** apply. Range gate per §6.1 (`current_distance ≤ range_m`).
- **Cooldown — independent per turret.** Each manual turret runs its own `loading_speed_ms` cooldown. A ship with N manual turrets in turret-mode fires up to N shots per cycle, each rolled independently against `pilot_primary_acc`.
- **PrimaryWeaponMod does NOT apply** to manual turrets (§7.8 excludes all turrets — auto and manual).

##### Required schema / data-model enhancements (Phase-1)
The `manual_turret_mode` flag is the canonical Phase-1 mechanism for choosing primary vs. manual-turret mode. The field does not yet exist in code; Phase-1 implementation must add it. (Decomposition into ordered implementation tasks is a later concern — this section documents the required surface.)

1. **`ShipLoadout.manual_turret_mode: bool`** — new field on the frozen dataclass at `services/bot-core/src/services/combat_models.py`, default `false`.
2. **`PlayerShip` persistence** — the per-ship record needs to carry the mode (top-level `"manual_turret_mode": false` in the existing JSON blob, or a dedicated column — implementer's choice; both are consistent with current `PlayerShip` patterns).
3. **`LoadoutBuilder.from_player()` and `LoadoutBuilder.from_criminal_ship()`** — read the persisted value and surface it on the built `ShipLoadout`. Criminals default to `false` (NPCs always run primary-mode in Phase-1 — no per-criminal toggle exists yet).
4. **No UI command in Phase-1.** Flipping the flag is deferred to a later cycle. The default-`false` ("primary mode") path is what every fight runs through until a turret-mode toggle command lands. The resolver implements both branches now so it is forward-ready when the UI exists.

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
- **Activation:** HP thresholds **66 % / 33 %** — up to 2 activations per fight.
- **Trigger rule:** activates iff off cooldown at the threshold crossing; missed threshold = skipped, no retry. Cooldown timer starts at *effect expiry*.
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
- **Trigger:** evaluated at the **end of the damage-application phase** (Appendix B step 4). If, after ALL tick damage has been applied (including any overkill that pushed hull transiently negative), the combatant's hull is ≤ 0 AND the combatant has an unconsumed ES equipped → fire. Shield or armour reaching 0 does NOT trigger; only true ship-death does.
- **Effect:** hull HP clamped to **1**; the combatant enters **10 s of full invulnerability** (ALL incoming damage blocked across subsequent ticks until the window expires).
- **Multi-source same-tick:** when multiple weapons / sub-munitions / nuke self-damage all contribute to the lethal damage, the trigger still fires ONCE at end-of-phase — ES does not try to identify "the lethal hit," only whether end-of-phase hull is ≤ 0. Overkill is discarded by the clamp-to-1; it does not carry into the invuln window.
- **Regen during invuln:** continues normally (shield + hull/armour concurrently if Repair Bot equipped), subject to the dormancy rule in §3 (regen only ticks on layers below max).
- **HP at expiry:** `1 + 10 s × applicable regen rates`, capped per-layer max. Edge case (no shield, no Repair Bot) → HP = 1 at expiry.
- **Consumable:** removed from loadout after use; player must manually re-equip a spare from inventory. **Once per fight by consumption.**
- **Not an HP-threshold device:** despite being grouped with cloak/booster in §8 for activation-rule discussion, ES does NOT use the universal HP-threshold trigger rule. It fires from the lethal-damage check above, at step 4 of the tick (not step 5).

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
| Cloak | 66 % / 33 % | 2 | Universal HP-threshold rule below. Fires at Appendix B step 5. |
| Booster | 80 % / 60 % / 40 % / 20 % | 4 (if cooldown permits) | Universal HP-threshold rule below. Fires at Appendix B step 5. |
| Thruster | — (passive) | — | Always active when `current_distance < 750 m`. No activation event. |
| EmergencySystem | End-of-damage-phase hull ≤ 0 (not a %) | 1 (consumable) | NOT an HP-threshold device. Fires at Appendix B step 4 (after damage applies, before clamp). See §7.7. |

**Universal trigger rule:** at any HP-threshold crossing, the device activates **iff off cooldown**. Still cooling = threshold *skipped*, no retry. Cooldown timer starts when **effect expires**, NOT when activated.

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
- **Time-cap, both alive (`reason: time_cap` for the resolver; `outcome: stalemate`):**
  - **PvP (`summary.reason: draw`):** draw. Both players keep credits; no rewards.
  - **PvC (`summary.reason: draw`):** draw, criminal escapes — new system selected along the route; hunt-checks reset. (Reuse the existing loss-path flow when coding.)

**`outcome` × `reason` matrix** (§12 `data.summary` fields):

| `outcome` | `reason` | When |
|---|---|---|
| `win` | `hp_depleted` | exactly one side at HP ≤ 0 at end of tick |
| `stalemate` | `mutual` | both sides at HP ≤ 0 on the same tick |
| `stalemate` | `time_cap` (resolver) / `draw` (summary) | tick == `MAX_FIGHT_TICKS`, both alive |

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

Modules not referenced by combat (Cabin, Compressor, JumpDrive, MiningDrill, Signature, SpectralFilter, TimeExtender, TractorBeam) live entirely in the loadout-builder's domain and are out of scope for this document.

### Built-in cloak supersession (the one special case)
The Scimitar and Specter ships carry an implicit U'tool cloak as a built-in (off-slot — does not consume a regular module slot). Combat treatment:

- **No cloak equipped** → the built-in U'tool is the active cloak. Same mechanic as any equipped cloak (§7.2): set-value 0.25, HP-threshold activations at 66 % / 33 %, U'tool's 10 s duration and cooldown.
- **Equipped cloak present** → the equipped cloak supersedes the built-in. Combat uses only the equipped cloak's stats; the built-in is bypassed for the fight. (Canonical statement: `effective_cloak = equipped if has_equipped else builtin`.)

This is the only built-in / equipped supersession case in Phase-1. The same rule generalises to any future built-in-vs-equipped collision on a unique-equip combat module — combat picks the equipped instance when present, otherwise falls back to the built-in.

### Loadout contract
The combat resolver consumes a baked `ShipLoadout` from the loadout builder and **does not re-validate** equip rules, slot counts, or any other loadout-construction invariants. The loadout builder is a separate process (out of scope here) and is assumed to produce a well-formed loadout. If a malformed loadout reaches combat, behavior is undefined — that is an upstream invariant violation, not a combat-resolver concern.

---

## 11. Phase-1 scope summary

**In scope:** primary, secondary (rocket / missile / cluster-missile / nuke / shock-blast), turret (auto + manual); shields, armour, repair-bot, thrusters, cloaks, boosters, scanners, EmergencySystem, PrimaryWeaponMod; tick-based simulation, distance, HP layers, regen, HP-threshold activations, EmergencySystem invuln.

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
        "module_activations": { "cloak": 2, "booster": 3 },          // lowercase_snake_case keys per §0.3; embed layer renders friendly names
        "secondary_fired":    { "rocket": 12 }
      },
      "2": { /* …same shape… */ }
    }
  },
  "timeline": [ /* CombatEvent rows, in processing order */ ],
  "metadata": { "tick_ms": 10, "total_ticks": 8421, "resolver": "tick_v1", "pvc_damage_reduction": 0.33 }
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
| `weapon_fire` | a primary / secondary / turret fires (incl. miss) | Base shape `{slot, subtype, weapon, hit, accuracy}` for accuracy-roll fires. Cluster-missile, nuke, and shock-blast substitute or extend per the table below. |
| `damage` | HP applied to a target after a hit | `{amount, breakdown:{shield,armour,hull}, hp_after, source}`. During an active EmergencySystem invuln window (§7.7), the event still emits but with `amount: 0`, `breakdown` omitted, `hp_after` unchanged, and a `blocked_by: "emergency_system_invuln"` annotation. |
| `module_activation` | a discrete activation occurs — Phase-1: cloak / booster (HP-threshold crossing) or EmergencySystem (lethal-hull trigger). Passive modules (Repair Bot, Thruster, PrimaryWeaponMod, shield regen) do NOT emit this event. | `{module, trigger_hp_pct}` (trigger_hp_pct omitted for EmergencySystem) |
| `cooldown_end` | a weapon or module comes off cooldown | `{system}` |
| `layer_depleted` | a ship's shield → 0 or armour → 0 | `{layer}` |
| `distance` | distance changes (booster push, closing, shock-blast reset) | `{from, to, cause}` |
| `fight_end` | terminal | `{winner, reason, duration_ticks, final_hp}` |

#### `weapon_fire` per-subtype payloads

The base `{slot, subtype, weapon, hit, accuracy}` covers per-shot accuracy-roll fires. Cluster-missile condenses to one event per fire (§6.2). Nuke and shock-blast bypass the accuracy roll and use subtype-specific shapes.

| Subtype | Payload |
|---|---|
| primary | `{slot: "primary", subtype: "primary", weapon, hit, accuracy}` |
| rocket | `{slot: "secondary", subtype: "rocket", weapon, hit, accuracy}` |
| missile | `{slot: "secondary", subtype: "missile", weapon, hit, accuracy, branch: "tier_a" \| "tier_bc"}` |
| cluster-missile | `{slot: "secondary", subtype: "cluster-missile", weapon, fired, hits, damage_per_hit, total_damage}` |
| nuke | `{slot: "secondary", subtype: "nuke", weapon, epicenter, opponent_damage, self_damage}` |
| shock-blast | `{slot: "secondary", subtype: "shock-blast", weapon, hit: true, accuracy: 1.0}` |
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

### Future battle-log dropdown — UX requirement

When the battle-log command is implemented (deferred — Discord rendering is a later cycle), the result dropdown MUST disambiguate duplicate-name fights (common for bounty caps where multiple criminals share a name). Required entry format:

```
{combatant1_name} v {combatant2_name} ({created_at, local-formatted})
```

This is captured here so the eventual command lands with the right contract — `combatant{1,2}_name` + `created_at` are already projected to columns precisely so this dropdown query is a single indexed scan, no JOINs.

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

**Wire-compat parameters (retiring with `SimpleTTKResolver`):**
- `variance_percent` and the old positional `player_armour_buff` are accepted (existing callsites may still pass them) but ignored by the tick resolver. See §3 (PvC damage reduction) and the "Why variance is dropped" note further down. Both retire when `SimpleTTKResolver` is removed.

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

> **Why variance is dropped:** the `variance_percent` parameter (sourced from `DUEL_VARIANCE_PERCENT`) was a TTK-era smoothing layer used by `SimpleTTKResolver` to add fight-to-fight variability on top of an otherwise-deterministic time-to-kill calculation. The tick resolver derives all randomness from per-shot accuracy rolls (one uniform draw per weapon fire) — that intrinsic RNG is already the source of fight variance, so a separate `variance_percent` would compound randomness without giving anything back. The parameter stays in the `CombatService.fight_ships(...)` signature for wire-compat (callsites unchanged), but the tick resolver ignores it. `DUEL_VARIANCE_PERCENT` and `variance_percent` retire when `SimpleTTKResolver` is removed.

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

**Out of scope** for this section: the `/about` bot-info command (BountyBot version/owner info), which is unrelated to item rendering. §14 applies only to *item-detail* embeds — the per-weapon / per-module display surfaces in inventory/ships flows where a player inspects a single item. The motivation for §14 is the recent enrichment of the item data model (EMP-vs-physical damage split, cluster `burst_count`, nuke effective magnitude, PrimaryWeaponMod `damage_pct`/`fire_rate_pct` breakdown); without these embed updates the player loses visibility into mechanically-relevant fields the combat resolver now consumes.

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
| `PVC_DAMAGE_REDUCTION` | **0.33** | §3 (Keith T. Maxwell bonus — PvC, player side only) |
| `COMBAT_LOG_RETENTION_HOURS` | **72** | §12 |

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
