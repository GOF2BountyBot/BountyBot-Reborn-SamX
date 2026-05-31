# Combat System Rewrite — Journal

> Working memory for the effort to replace `services/bot-core/src/services/combat_service.py`
> and related code with a proper combat system.
> Started: 2026-05-24. Last reorganized: 2026-05-29.

**Resume rule:** read §1–§5 below; fall back to Historical Entries only when you need decision provenance or original rationale. §6 is the active queue of ambiguities surfaced during the 2026-05-29 condensation pass.

**Current PR target:** PR-4 (tick-based combat resolver). PRs 1–3 (catalog enrichment) and PRs A–E (commodity foundation) have shipped on `dev`.

**Working tree state (2026-05-29):** Entry 7 + 5 seed-JSON gap-fill edits + this reorganization are uncommitted. Reorganization preserves all Historical Entry text verbatim; only adds structure on top.

---

# 1. CURRENT DECISIONS

> **Locked-in canonical design lives in [`COMBAT_SPEC_LOCKED.md`](./COMBAT_SPEC_LOCKED.md).** This journal is the WIP / decision-log space — it contains the same locked content plus open questions, in-progress decisions, rationale, and Historical Entries. Promote items from §1 here into `COMBAT_SPEC_LOCKED.md` only once they are firmly locked-in (no `?`, no `OPEN`, no `pending`).

*Canonical Phase-1 combat spec. Supersedes any conflicting text in Historical Entries. Each subsection cites originating Historical Entry (HE-N) for traceability. Where a numeric is still TBD, the open-question ID (O-X) is given.*

**Configuration policy (locked 2026-05-30):** every numeric in §1 (rates, percentages, thresholds, durations, distances, magnitudes, etc.) is a **starting default**. The intent is for all of them to land in `services/bot-core/src/services/game_constants.py` as `GameConstants.X` with `BOUNTYBOT_<NAME>` env-var overrides AND per-guild overrides (matching the existing pattern for `DUEL_VARIANCE_PERCENT`, `BOUNTY_PVC_ARMOUR_BUFF_FACTOR`). Tuning post-Phase-1 should not require code changes.

**Resource policy (locked 2026-05-30, generalises HE-5a):** **energy is assumed infinite** — for combat AND any other gameplay surface that might check energy. Energy cells are NOT tracked. Wiki lore references to "energy cell consumption per use" (e.g. U'tool cloak) are cosmetic only; the resolver does not gate on energy. Applies to player AND criminal/NPC combatants.

## 1.1 Tick & timing
- **Tick = 10ms fixed.** (HE-5j)
- **Max ticks per fight = 18,000 (3-minute hard cap).** (HE-5 / HE-5j)
- Per-weapon cooldown: each weapon holds `cooldown_remaining_ms`; per tick, decrement by 10; fires when ≤ 0 AND in-range AND not gated; resets to `loading_speed_ms`. All wiki `loading_speed_ms` values are clean multiples of 10ms (verified HE-5j) — no accumulator carry, no drift.
- **Retired:** HE-5's "tick = fire-rate of fastest weapon, per fight" rule (superseded by HE-5j).

## 1.2 Distance model
- **Starting distance:** 5000m. (HE-5e)
- **Base ship speed:** 150 m/s, pinned (same both combatants). (HE-5e)
- **Passive closure:** 300 m/s relative (both ships approaching at 150 m/s). (HE-5e)
- **Minimum distance floor:** 300m. (HE-5e)
- **Booster distance push:** `distance_gained_m = base_speed × (effect_pct/100) × (duration_ms/1000)`. During boost-active window, passive closure is suspended; booster outward velocity dominates. After expiry, normal closure resumes. (HE-5f)
- **Shock-blast distance reset:** instantly resets both ships to starting distance (5000m). 100% guaranteed (no accuracy roll). No damage. Fires on cooldown (`loading_speed_ms`); no per-fight cap; no HP-threshold gating. (HE-7, session 2026-05-29) — see §6 C7 for the in-flight-projectile rule.
- **Range gating:** weapons fire only when `current_distance ≤ weapon.range_m`. (HE-5d)

## 1.3 HP layers + damage stacking + regen
- **Three layers per combatant:** shield, armour, hull. (HE-5e / HE-1)
- **Hull = `ship.armour` column** (intrinsic to the Ship row, NOT a separate module type). (HE-1 #13)
- **Armour = sum of equipped ArmourModule HP** (separate buffer above hull). (HE-1 / HE-3)
- **Shield = sum of equipped ShieldModule HP** (separate regenerating layer). (HE-3)
- **Damage stacking order:** shield → armour → hull. (HE-5e)
- **Repair Bot fill order:** hull first, then armour (inverse of damage stacking). (HE-5f)
- **Starting HP (Phase-1):** both combatants at max(hull, armour, shield). Hooks for damaged-start ship in Phase-1 schema for Phase-2 use. (HE-3)
- **HP is integer; per-tick HP delta is integer.** (HE-7)
- **Shield regen schedule:** `+1 HP every N ticks` where `N = ceil(shield_recharge_ms / shield_capacity / tick_ms)`. Worked example: Targe (50 cap / 20000ms recharge) → +1 HP every 40 ticks (= 400ms). Same shape for any slow regen. (HE-7)
- **Repair-bot regen rate:** percentage-of-max (HE-3, locked 2026-05-30 closing §6 C1). **Scope: hull + armour ONLY — repair bots do NOT touch the shield layer.** Shield recharge is fully independent (per-shield-module `shield_recharge_ms`, §1.3 shield regen schedule). Ketar I = **2.5%/s**, Ketar II = **5.0%/s** of `max_hull + max_armour`. Per-tick HP delta = `(max_hull + max_armour) × rate × (tick_ms / 1000)`, accumulated and integer-flushed (the +1-HP-every-N-ticks discretization PATTERN is shared with shield regen, but the LAYERS are disjoint). Fill order: hull first, then armour (HE-5f). Seed `extra_atts.HPps` values (7 / 15) are stale data — ignore. Rates are starting defaults; planned `BOUNTYBOT_KETAR_I_REPAIR_PCT_PER_SEC` / `BOUNTYBOT_KETAR_II_REPAIR_PCT_PER_SEC` env-var + per-guild overrides per §1's Configuration policy.
- **Concurrent regen:** shield regen and hull/armour regen run in parallel each tick (independent tracks). (HE-7)
- **Regen pulse applied BEFORE damage in each tick** — finishing-blow detection is clean (HP ≤ 0 at end of tick = dead). (HE-7)

## 1.4 Damage type model
- **Phase-1 in-scope damage type: physical ONLY.** (HE-7, session 2026-05-29)
- **EMP is a separate damage type; deferred to Phase-2+.** (HE-7, session 2026-05-29)
- **Resolver rule:** every weapon participates in cooldown / firing / event-log. Each hit applies `damage_per_shot` (physical) only; `emp_damage` is ignored regardless of value.
- **Pure-EMP weapons** (`damage_per_shot` = 0/absent, `emp_damage` > 0): fire normally, roll accuracy, log hit/miss, apply **0 HP delta**. Equipping one in Phase-1 is a no-op-cost choice — RESOLVED O-PE (2026-05-30) → option (a) accept: combat log surfaces the 0-damage outcome; no preflight warning, no loadout-build filter.
  - Pure-EMP inventory (post seed-fix `e87db57`, verified 2026-05-30): primaries `luna_emp_mk_i` (emp_damage=3), `sol_emp_mk_ii` (5), `dia_emp_mk_iii` (8); secondaries `missiles.mamba_emp` (emp_damage=100), `mines.netha_emp` (emp_damage=500). 5 weapons total.
- **Hybrid weapons** (`damage_per_shot` > 0 AND `emp_damage` > 0, secondaries only): fire normally; apply ONLY the physical `damage_per_shot`. EMP component ignored.
  - Hybrid inventory (verified 2026-05-29): `missiles.dephase_emp` (120+100), `missiles.intelli_jet` (100+50), `rockets.emp_rocket_mk_i` (10+45), `rockets.emp_rocket_mk_ii` (30+60), `emp_bombs.emp_gl_dx` (4+300), `emp_bombs.emp_gl_i` (2+80), `emp_bombs.emp_gl_ii` (2+150). **Bonus surprise:** `rockets.armour_rocket` carries `emp_damage: 24` despite no EMP-naming — Phase-1 non-issue (ignored); flagged for Phase-2 to verify it's not a seed typo.
- **GammaShield inert in Phase-1** (no radiation-damage source). Kept in `UNIQUE_EQUIP_TYPES` for fidelity. (HE-4 #12)

## 1.5 Accuracy system
**Combatant base accuracy** (HE-7):
- Player: **60%**
- NPC / criminal: **50%**

**Layered formula:**
```
attacker_accuracy = combatant_base                 # 60% / 50%
                  + own_scanner_bonus              # 0 (A) / +5pp (B) / +10pp (C)
                  + own_thruster_bonus             # primaries only; ramps 0 at 750m → max at 300m  [O-TH3 ✓]
                  − opponent_booster_debuff        # while boost active: effect_pct × k_boost       [O-B ✓]
                  → clamp [0.05, 0.99]
# Primaries: no in-range distance penalty (resolved O-DP, 2026-05-30). Range is
# a pure binary gate via §1.6 — within range_m fires at the value above; beyond
# range, primary cannot fire at all. Distance-as-accuracy is a SECONDARY-weapon
# concern (rocket curve, missile tier-A degrade) and lives in §1.6, not here.

# Cloak override (ABSOLUTE — resolved O-Q1, 2026-05-30):
# while the TARGET's cloak is active, the layered result above is REPLACED by a
# hard-set value: attacker_accuracy = cloak_set_value (default 0.25). It does
# NOT stack with / subtract from the other terms — it supersedes them — then
# re-clamp [0.05, 0.99].
```
- **Cloak is an absolute set, not a debuff term.** While the target is cloaked the attacker's hit-chance against it is forced to `cloak_set_value` regardless of scanner/booster/thruster/distance; those modifiers are moot for the duration. (O-Q1 RESOLVED — see §1.7 Cloaks for the value.)
- **Booster accuracy debuff (RESOLVED O-B, 2026-05-30):** `opponent_booster_debuff_pp = effect_pct × k_boost`, where `k_boost` is a configurable scaling factor (**default 0.10**, env/per-guild per §1's Configuration policy — `BOOSTER_ACCURACY_DEBUFF_FACTOR`). Percentage-points, subtracted from attacker accuracy while the target's boost is active, then clamped. At default k, the strongest booster (Polytron, `effect_pct`=300) yields 30pp — below cloak's 35pp, so no separate cap; the `[0.05, 0.99]` clamp bounds any extreme `k`. This is **in addition to** the booster's distance-push effect (§1.2). See §1.7 Boosters.
- **Thruster accuracy bonus (RESOLVED O-TH3, 2026-05-30 — restores HE-5f framing; HE-7's defender-debuff reframing was a thruster↔booster confusion, superseded):** ATTACKER-SIDE bonus on the equipping ship's own primary accuracy. `bonus_pp = max_bonus_pp × ramp`, where `max_bonus_pp = effect_pct × k_thruster` (**`k_thruster` default 0.10**, configurable — `THRUSTER_ACCURACY_BONUS_FACTOR`, env/per-guild per §1 config policy) and `ramp = clamp((750 − current_distance) / (750 − min_distance), 0, 1)`. → 0 outside the 750 m window, ramping linearly up to `max_bonus_pp` at the 300 m distance floor. **Primaries only.** Turrets and rockets unaffected (rockets have their own 5%→60% curve). At default k, per-module max bonus: Static +2pp / Pendular +4pp / D'ozzt +7pp / Mp'zzzm +10pp / Pulsed Plasma +13pp. See §1.7 Thrusters.
- **Per-weapon `accuracy_modifier`: dropped permanently.** (HE-7, session 2026-05-29 — closes Q2)
- Forward-compat hook: `weapon_accuracy(pilot_acc, weapon) -> float` returns `pilot_acc` unchanged in Phase-1; an empty `SUBTYPE_ACCURACY_MOD: dict[str, float]` lives in `combat_balance.py`. Future homing-vs-must-aim split slots in here without structural rewrite.
- Code cleanup: remove `WeaponStats.accuracy_modifier` (multiplicative, default 1.0 — never populated); keep `ModuleStats.accuracy_modifier` (additive, default 0.0 — carries scanner bonus).

## 1.6 Weapons

### Primary weapons
- Hit damage = `damage_per_shot` (physical). Cooldown = `loading_speed_ms`. **Range is a pure binary gate**: `current_distance ≤ range_m` → fires at full §1.5 accuracy; otherwise cannot fire at all. (Range gate is also locked in §1.2.)
- **No in-range distance penalty (RESOLVED 2026-05-30, closes O-DP):** primaries do NOT degrade with distance inside their range envelope. The earlier-floated 0.20 max was a stale carry-over from before primaries/rockets were split; distance-as-accuracy is a secondary-weapon concern (rocket 5%→60% curve, missile tier-A degrade) — see Secondary weapons below.

### Secondary weapons
**Phase-1 in-scope subtypes:** rocket, missile, cluster-missile, nuke, shock-blast. (HE-5d + HE-7 session + RESOLVED O-M 2026-05-30)
**Phase-2 deferred:** emp-bomb (mechanic in scope when EMP lands; physical track inert in Phase-1).
**Phase-3+ deferred:** mine, sentry-gun, ionizing-missile (no ionizer mechanic planned; seed `damage` already 0 — fires/rolls/applies 0). (HE-7 session + RESOLVED O-M 2026-05-30)

- **Rocket** (`steerable: false`): accuracy curve linear 5% at `range_m` → 60% at min distance. `accuracy = 0.05 + 0.55 × ((range_m − current_distance) / (range_m − min_distance))`, clamped `[0.05, 0.60]`. (HE-5f)
- **Missile** (`steerable: true`): behavior depends on equipped scanner tier (§1.7):
  - Tier B / C scanner equipped → tracking active → fires at pilot's current accuracy from §1.5 (no distance penalty applied)
  - Tier A (no scanner) → degrades to rocket behavior (same projectile, same stats, rocket accuracy curve applies). (HE-7)
- **Cluster missile** (`subtype: "cluster-missile"`, `burst_count: N` in seed): lock-on tracking missile that releases N sub-munitions per fire (RESOLVED O-M 2026-05-30). Inherits the plain-Missile scanner-tier rule for whether tracking is active. **Accuracy snapshot:** the pilot's §1.5 accuracy is captured ONCE at the moment of fire; ALL N sub-munitions roll independently against that single snapshot (so a thruster ramp / cloak activation mid-flight does NOT retroactively change sub-munition rolls). Each landing sub-munition deals `damage` (per-sub-munition, NOT total). Single-target (no AoE; cluster missiles have no `magnitude_m`). **Combat-log:** ONE event per cluster fire with summary fields `{weapon, fired: N, hits: K, damage_per_hit, total_damage: K × damage}` — NOT N rows per fire. Seed inventory: Shesha (N=3, dmg=60), Garuda-IV (N=4, dmg=75), Patala (N=5, dmg=90).
- **Nuke** (AoE, no accuracy roll — RESOLVED O-N 2026-05-30): completely bypasses the §1.5 accuracy system (no accuracy roll, no cloak override, no thruster/booster modifiers). `range_m` is the binary fire gate (consistent with primaries). On fire, an **epicenter** is sampled uniformly at random from `[300m, 5000m]` along the 1D combat-distance axis (same model as §1.2). Both ships always take damage based on their distance from this epicenter — there is no hit/miss:
  - `d_firer = epicenter` (firer treated as position 0)
  - `d_opponent = |epicenter − current_distance|`
  - **Falloff formula:** `dmg(d) = damage × (1 − min(1, d / effective_magnitude))²`, where `effective_magnitude = magnitude_m × NUKE_MAGNITUDE_SCALE` (**default 0.10**, configurable — `BOUNTYBOT_NUKE_MAGNITUDE_SCALE`, env/per-guild per §1 config policy). Effective magnitudes at default 0.10: Tormentor 1000m, Liberator 1250m, Oppressor 3000m, Extinctor 4000m, Fireworks 1000m.
  - **Opponent damage:** `dmg(d_opponent)`.
  - **Firer self-damage:** `dmg(d_firer) × NUKE_FRIENDLY_FACTOR` (**default 0.25**, configurable — `BOUNTYBOT_NUKE_FRIENDLY_FACTOR`). Same epicenter, same falloff formula, just scaled by friendly factor.
  - **Steerable flag IGNORED Phase-1** — all nukes treated identically; per-nuke flavor comes from `damage` + `magnitude_m`.
  - **Per-nuke seed values (direct-hit anchors):** Liberator=850, Extinctor=700, Oppressor=400, Tormentor=150, Fireworks=1 (decorative; same code path). Liberator/Oppressor calibrate the design.
  - **Combat-log event** (one per fire): `{weapon, epicenter, current_distance, d_firer, d_opponent, opponent_damage, self_damage}`.
- **Shock-blast**: see §1.2 (pure distance-reset utility, 100% guaranteed, no damage). One seed file (`misc.shock_blast.json`) — physical `damage: 140` and `emp_damage: 80` in seed are IGNORED by the Phase-1 mechanic. (HE-7 session)

### Turret weapons
**Three subtypes exist; two are combat-relevant.** (HE-7 session 2026-05-29)
- **Auto** (`automatic: true`): fires on own cooldown, additively alongside primaries. `auto_turret_accuracy = clamp(pilot_current_accuracy × auto_turret_multiplier, 0.05, 0.99)` (RESOLVED O-T2 2026-05-30; **multiplier default 0.85**, configurable — `AUTO_TURRET_ACCURACY_MULTIPLIER`, env/per-guild per §1 config policy). `pilot_current_accuracy` is the full §1.5 result (post layered modifiers OR cloak override). **Auto turrets inherit the cloak set-value** — if the target is cloaked, pilot's current accuracy is `cloak_set_value` (default 0.25), so auto turrets fire at ~0.2125 (= 0.25 × 0.85), re-clamped to `[0.05, 0.99]`. All auto turrets on a single ship share one accuracy value (no per-turret variation; an 8-turret battlecruiser has one auto-turret-accuracy value applied to all 8). (HE-7)
- **Manual** (`automatic: false`): mutually exclusive with primary; pre-combat pilot-dedicates. Default = primary (typically higher damage). Modeled by `manual_turret_mode: bool` flag on loadout. Override command does not exist yet. (HE-7)
- **Plasma-collector** (`subtype: "plasma-collector"`, `dps: 0`): inert in combat. Equippable for fidelity; no effect.
- **Data note:** auto/manual turrets lack explicit `subtype` field in seed; discriminate using `automatic: bool`. Only plasma-collectors carry `subtype`.

## 1.7 Modules

### Scanners (combat scanners; plasma scanners ignored)
**Three tiers** (HE-7):

| Tier | Lock time | Pilot accuracy bonus | Missile behavior | Modules |
|---|---|---|---|---|
| A | n/a (no scanner) | 0 | Degrade to rocket | — |
| B | ≥ 3.0s | +5pp | Track at pilot accuracy (no distance penalty) | Telta Quickscan (4.0s), Telta Ecoscan (3.0s) |
| C | ~1.8s | +10pp | Same as Tier B | Hiroto Proscan (1.8s), Hiroto Ultrascan (1.8s) |

- Combat scanner is **unique-equip on its own subclass** (one combat scanner at a time). Plasma scanner is a separate subclass with no combat effect (none in seed).
- Lock-time numerics are flavor only in Phase-1; tier membership is what matters.
- **Thermal-fusion homing effect bypassed in Phase-1.** Thermal-fusion is a primary class and follows primary rules; scanner tier does NOT modify thermal-fusion behavior.

### Cloaks
- **Effect:** while active, the opponent's hit-chance against you is **hard-set to an absolute value** (`cloak_set_value`, default **25%**) — NOT a relative reduction and NOT a forced miss. Example: a 60% attacker is set to 25% for the cloak's duration. (HE-7)
- **Activation:** HP thresholds **66% / 33%** (up to 2 activations per fight). (HE-7 — supersedes HE-5b's "30% single activation")
- **Trigger rule:** activates iff off cooldown at threshold crossing; missed threshold = skipped, no retry; cooldown timer starts at effect expiry. (HE-7)
- **Duration:** `effect_duration_ms` from wiki (U'tool=10s, Sight Suppressor II=20s, Shadow Ninja=40s).
- **Cooldown:** `loading_speed_ms` from wiki.
- **Built-in cloaks (Scimitar + Specter):** these ships carry an implicit U'tool cloak that **DOES function in combat** — it is a real, active cloak (same mechanic, duration, cooldown, HP-threshold activation as any equipped cloak). The built-in does NOT count against `max_modules` (off-slot — pilot still gets the ship's full equippable slot count). **Supersession rule:** if the pilot equips a cloak module, the equipped one wins and the built-in is bypassed: `effective_cloak = equipped if has_equipped else builtin`. (HE-5h) **Generalises to all `UNIQUE_EQUIP_TYPES`:** if any future ship gains a built-in <TYPE>, equipped wins over built-in, built-in still functions when no equipped instance is present. **Status (verified 2026-05-30):** both `nivelian.specter.json` and `nivelian.scimitar.json` populate `builtinModules: ["U'tool"]`; gap was closed during PR-3 enrichment. §6 C4 RESOLVED.
- **Energy:** see §1's Resource policy — infinite, not tracked.
- **Math interpretation (RESOLVED 2026-05-30, closes O-Q1):** **ABSOLUTE set** — opponent accuracy is hard-set to `cloak_set_value` (default **0.25**), overriding the §1.5 layered terms (does not stack with booster/thruster/distance). Value is a starting default, configurable per §1's Configuration policy. **Phase-1: all cloak tiers use the same set-value; tiers differ by duration only** (U'tool 10s / Sight Suppressor II 20s / Shadow Ninja 40s).

### Boosters
- **Effect:** (a) push distance outward (formula in §1.2, uses `effect_pct`), AND (b) reduce opponent's accuracy while active. Both fire together. (HE-7)
- **Activation:** HP thresholds **80% / 60% / 40% / 20%** (up to 4 activations if cooldown permits). (HE-5b — confirmed in HE-7; 75/50/25 alternative retired)
- **Trigger rule:** same universal HP-threshold rule (§1.8).
- **Booster-user can still fire during boost** (accepted simplification, mirrors GoF2 base behavior). (HE-5f)
- **Opponent accuracy debuff (RESOLVED 2026-05-30, closes O-B):** `debuff_pp = effect_pct × k_boost`, subtracted from attacker accuracy in §1.5 while boost is active. `k_boost` configurable, **default 0.10** (`BOOSTER_ACCURACY_DEBUFF_FACTOR`, env/per-guild per §1 config policy). Resulting per-module debuffs at default: Linear 6pp / Cyclotron 8pp / Synchrotron 16pp / Me'al 20pp / Polytron 30pp. No separate cap (stays under cloak's 35pp at default k; §1.5 `[0.05,0.99]` clamp bounds extremes). Independent of and additive with the distance-push (a).

### Thrusters
- **Effect (RESTORED to HE-5f framing 2026-05-30):** thruster is the equipping ship's *handling / maneuverability* module → ATTACKER-SIDE primary-accuracy bonus. While **YOU** are within 750 m of your opponent, **YOUR** primary-weapon hit-chance is boosted. (HE-7's defender-debuff reframing was a thruster↔booster confusion — see HE-5f/5g for original locked design; HE-7 superseded on this point.)
- **NO effect on distance / closure / weapon range / rocket accuracy / turrets.** Rockets carry their own 5%→60% distance curve; thrusters do not stack on top.
- **Magnitude (RESOLVED 2026-05-30, closes O-TH3):** `bonus_pp = max_bonus_pp × ramp`, where:
  - `max_bonus_pp = effect_pct × k_thruster`, with `k_thruster` configurable (**default 0.10** — `THRUSTER_ACCURACY_BONUS_FACTOR`, env/per-guild per §1 config policy).
  - `ramp = clamp((750 − current_distance) / (750 − min_distance), 0, 1)` — linear from 0 at the 750 m window edge → 1 at the 300 m distance floor.
- **Per-module max bonus at default k:** Static +2pp / Pendular +4pp / D'ozzt +7pp / Mp'zzzm +10pp / Pulsed Plasma +13pp.
- **Passive (RESOLVED 2026-05-30, closes O-TH4):** thruster is **always active when conditions permit** — no HP-threshold gating, no `duration_ms`, no cooldown, no toggle. The bonus formula above is evaluated every tick: if `current_distance < 750m`, apply the ramped bonus; otherwise zero. (Consistent with the wiki having no `duration_ms` for thrusters.)

### Shields
- Layer 1 (absorbs damage first per §1.3 stacking order).
- **Continuous per-tick recharge** via the integer schedule in §1.3.
- Recharge does NOT pause after a hit (no "interrupt window" in Phase-1).

### Repair Bot
- **Scope: hull + armour ONLY.** Repair bots do NOT touch the shield layer; shield recharge is fully independent (§1.3 shield regen schedule).
- Heals hull first, then armour (inverse of damage stacking — HE-5f).
- **Rate (locked 2026-05-30):** percentage-of-max. Ketar I = 2.5%/s, Ketar II = 5.0%/s of `max_hull + max_armour`. See §1.3 for formula and configuration. Closes §6 C1.

### EmergencySystem (fully locked, HE-7)
- **Trigger:** when an incoming damage event would reduce **hull** to 0 or below (true ship-death interception). Shield or armour reaching 0 does NOT trigger.
- **Effect:** hull HP clamped to 1; 10s of full invuln (ALL incoming damage blocked).
- **Regen during invuln:** continues (shield + hull/armour concurrently if Repair Bot equipped).
- **HP at expiry:** `1 + 10s × applicable regen rates`, capped per layer max. Edge case (no shield, no Repair Bot) → HP = 1 at expiry.
- **Consumable:** removed from loadout after use; player must manually re-equip a spare from inventory. Once per fight by consumption.
- **Retired:** HE-3 #5's "trigger at hull ≤ 25%, 10s invuln, once-per-fight" lock — superseded by HE-7's lethal-blow rule.

### GammaShield
- Inert in Phase-1 (no radiation damage source). Kept in `UNIQUE_EQUIP_TYPES` for fidelity. (HE-4 #12)

### Phoenix SIS / RepairBeam / TransfusionBeam
- Deferred to Phase-2. (HE-7)

### Other modules
- **PrimaryWeaponMod:** unique-equip in `UNIQUE_EQUIP_TYPES` (HE-3 #8; mutual-exclusion locked in Entry 3). **Phase-1 mechanic (RESOLVED O-PWM 2026-05-30):** the new tick-based resolver honors the seed `damage_pct` + `fire_rate_pct` breakdown (NOT the legacy `dpsMultiplier` field, which is metadata-only and used only by the current SimpleTTKResolver + item-detail embed). Applies to **primary weapons ONLY** (secondaries, turrets, auto-turrets unaffected). Formulas:
  - `effective_damage_per_shot = round(damage_per_shot × (1 + damage_pct / 100))`
  - `effective_loading_speed_ms = round((loading_speed_ms / (1 + fire_rate_pct / 100)) / TICK_MS) × TICK_MS` (snaps to 10ms tick boundary)
  - No floor guard on damage — base 0 (EMP-blasters) stays 0; normal primaries (damage ≥ 2) never round to 0 from −10%.
  - Seed inventory: `nirai_overdrive` (damage_pct=−10, fire_rate_pct=+20 → lighter-faster shots), `nirai_overcharge` (damage_pct=+20, fire_rate_pct=−10 → heavier-slower shots). Both `dpsMultiplier=1.1` is coincidental (~+8% effective DPS in both cases) — the *feel* differs but headline DPS is the same.
- **Non-combat modules** (no Phase-1 combat effect, resolver ignores entirely): JumpDriveModule (Khador Drive), TimeExtenderModule (Rhoda Vortex), Compressor, MiningDrill, TractorBeam, Cabin, Signature, SpectralFilter. (Inventoried 2026-05-29 + name-mapping corrected 2026-05-30 per §7 verification.)

## 1.8 Activation rules (HP-threshold devices)
- **Cloak:** 66% / 33% HP (2 activations max).
- **Booster:** 80% / 60% / 40% / 20% HP (4 activations max).
- **Thruster:** passive — no HP-threshold gating, no toggle, no cooldown. Always evaluated; effect gated solely by `current_distance < 750m`. (O-TH4 RESOLVED 2026-05-30.)
- **EmergencySystem:** triggers on lethal hull damage (not a percentage threshold).

**Universal trigger rule (HE-7):** at any HP-threshold crossing, the device activates **iff off cooldown**. Still cooling = threshold *skipped*, no retry. Cooldown timer starts when **effect expires**, NOT when activated.

Per-activation sequence: `trigger → run for duration_ms → cooldown begins → cooldown lasts loading_speed_ms → eligible at next threshold crossing`.

## 1.9 Fight termination
- **Hard cap:** 18,000 ticks (3 simulated minutes). (HE-5j)
- **One side dead:** other side wins.
- **PvP stalemate (cap reached, both alive):** draw, both players keep credits, no rewards. (HE-5)
- **PvC stalemate (cap reached, both alive):** draw, BUT the criminal escapes — new system selected along route, hunt-checks reset. (HE-5) Implementation note: reuse existing loss-path flow when coding; verify at code time.

## 1.10 Unique-equip list (`UNIQUE_EQUIP_TYPES`)
At most one of each type per ship loadout:
- Cloak
- Booster
- EmergencySystem
- ShieldInjector (Phoenix SIS) — Phase-2
- TimeExtenderModule (Rhoda Vortex) — non-combat
- PrimaryWeaponMod
- Combat scanner (one combat scanner)
- Plasma scanner (one plasma scanner — inert)

(HE-3 #8 + HE-7 scanner addition)

## 1.11 Scope summary

**In scope for Phase-1:**
- Weapons: primary, secondary (rocket/missile/nuke/shock-blast), turret (auto+manual)
- Modules: shields, armour, repair-bot, thrusters, cloaks, boosters, scanners, EmergencySystem, PrimaryWeaponMod
- Mechanics: tick-based simulation, distance, HP layers, regen, HP-threshold activations, EmergencySystem invuln

**Inert in Phase-1 (kept for fidelity):**
- GammaShield, plasma-collector turret, plasma scanner, non-combat modules listed in §1.7

**Phase-2 (designed; deferred):**
- EMP mechanic (full disable-window spec partially captured — see DEFERRED §3)
- ShieldInjector (Phoenix SIS), RepairBeam, TransfusionBeam
- Out-of-combat HP recovery + dock mechanic (schema in Phase-1)
- Damaged-opponent start state (schema hooks in Phase-1)
- emp-bomb subtype

**Phase-3+:**
- Mines, sentry-guns

---

## 1.12 Combat log & results output

**Scope of this section: combat mechanics + data persistence only.** The resolver simulates at full 10 ms tick fidelity and persists a full event-tick battle log per fight, plus returns a lightweight summary inline. **User-facing visualization (Discord rendering / condensation / summarization) is a LATER cycle** — those transformation helpers will live on the consumer side and are out of scope here. This section defines what the resolver produces and how it is stored.

### Persistence model — `combat_log` table (NEW)
The full battle log is persisted per fight, keyed by the row's integer PK, so it can be retrieved later without re-simulating. Types follow existing model conventions (cf. `bounty.py`, `duel_request.py`).

| Column | Type | Null? | Notes |
|---|---|---|---|
| `id` | `Integer` PK autoincrement | no | The only handle — returned inline so a fight can be pulled back later. No separate UUID (kept simple). |
| `guild_id` | `BigInteger` | no | Discord snowflake (same as `Bounty.guild_id`) |
| `context` | `String(20)` | no | `duel` / `bounty_pvc` / `bounty_bonus` — origin of the fight |
| `combatant1_name` / `combatant2_name` | `String(255)` | no | Display name. For PvC the NPC side holds the **criminal / bounty name** |
| `combatant1_user_id` / `combatant2_user_id` | `BigInteger` | yes | Discord user id (matches `DuelRequest.challenger_id`, `Bounty.win_user_id`). **NULL ⇒ that side is an NPC.** |
| `winner_name` | `String(255)` | yes | NULL on stalemate |
| `is_stalemate` | `Boolean` | no | |
| `data` | `JSON` | no | The whole log object (schema below). Generic `JSON` per existing convention (we never query inside it). |
| `created_at` | `DateTime(timezone=True)`, default `now(UTC)` | no | **Retention key** |

- **Invariant: ≥ 1 combatant is always a real player** (NPC-vs-NPC never occurs) — at least one `*_user_id` is non-NULL. The resolver/persist layer may assert this.
- **Single `data` blob** holds summary + full timeline + metadata; combatant identity + outcome are projected to columns for cheap lookup/listing.
- **Size is bounded by retention, not truncation** — the timeline is stored whole.

### Retention — 72 h cleanup
A scheduled cleanup deletes `combat_log` rows older than **~72 h** (`BOUNTYBOT_COMBAT_LOG_RETENTION_HOURS`, default 72 — env/per-guild per §1 config policy), implemented by extending the existing **`db_retention_default`** job (daily 03:45 UTC) with `CombatLogRepository.delete_older_than(cutoff)`, mirroring the bounty/duel/audit pattern. Table size is therefore self-limiting.

### `data` blob — internal schema
```jsonc
{
  "schema_version": 1,
  "summary": {                                  // Tier-0 — also copied inline into combat_result
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
        "module_activations": { "Cloak": 2, "Repair Bot": 3 },   // which + counts
        "secondary_fired":    { "rocket": 12, "nuke": 1 }
      },
      "2": { "name": "Vossk Raider", "ship": "Nivelian Berserker", "...": "..." }
    }
  },
  "timeline": [ /* event-tick rows, in processing order — see below */ ],
  "metadata": { "tick_ms": 10, "total_ticks": 8421, "variance_percent": 0.05, "resolver": "tick_v1", "pvc_armour_buff": 1.5 }
}
```

### Timeline — real event-ticks (not every tick, not narrative milestones)
- A **tick counter starts at 0** at combat start and increments by 1 each 10 ms tick. Real time = `tick × tick_ms`.
- The timeline records **one row per event**, only for ticks where something actually happens (a weapon fires, damage is applied, a module activates, shield/HP regen pulses, a cooldown ends, distance changes, a layer depletes, …). **Empty ticks are not stored.**
- **Multiple events on the same tick → multiple rows sharing that `tick` value.** They are stored in **exact processing order** within the tick. Array order *is* the sequence (no separate index needed). E.g. a tick that resolves `regen → primary fire → damage dealt → booster activation` produces four consecutive rows with that `tick`, in that order.

`CombatEvent` (one timeline row):
```jsonc
{
  "tick":   3000,            // tick counter (0 at start). Real ms = tick × tick_ms
  "type":   "weapon_fire",
  "actor":  "Specter",       // acting combatant name; null for global/system events
  "target": "Vossk Raider",  // null when N/A
  "data":   { /* type-specific payload */ }
}
```

Representative event vocabulary (extensible — the resolver emits a row for any state-changing tick-step):

| `type` | Emitted when | `data` payload (example) |
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

`CombatEvent` carries **structured data only** — no pre-rendered human strings (those are a later-cycle concern), so wording can change without rewriting stored history.

### Tier-0 summary returned inline
On every fight the response (`combat_result` dict) carries the `summary` object above **plus the `combat_log` row id** (`combat_log_id`) so the detail can be fetched later. The bulky `timeline` is **never** sent on the fight response — it goes only to the DB.

Summary content: outcome / reason / duration; per-combatant module activations (which + counts), secondary-weapon use (by subtype), accuracy %, HP remaining per layer, damage dealt / taken.

### Player-profile stat promotion (separate from the log)
Aggregate **lifetime** combat metrics (module activations, nukes used, secondaries fired, etc.) are promoted onto the **`Player` record** by the combat processor — **NOT** stored in `combat_log`. This is a handler/helper inside the combat-service code that mutates the `Player` object after a fight; the log tables are unaffected.

- **`Player` model gains 3 new metric columns** (Integer counters, default 0) — RESOLVED O-STAT 2026-05-30: `total_fights`, `total_nukes_fired`, `total_module_activations`. (Existing `duel_wins`/`duel_losses`/`bounty_wins`/`lifetime_credits`/`systems_checked`/`xp`/`prestige_count`/`duel_credits_won`/`duel_credits_lost` stay as-is.) Counters chosen for bounded growth per fight (no `total_shots_fired` or `total_secondaries_fired` to avoid uninteresting-large-number drift; no `total_damage_*` for the same reason; no `bounty_losses` since the wins/losses asymmetry with bounties is intentional in current scoring). One Alembic migration adds all 3.
- The combat processor increments these on the `Player` row(s) for any human combatant as part of the post-fight update (NPC side has no Player row → skipped). Requires an Alembic migration for the new columns.

### In-memory production & mapping onto `FightResults`
- The tick resolver builds the full `timeline` + summary in memory during the sim. `FightResults.combat_log: list[dict]` ← the timeline; `FightResults.metadata: dict` ← the summary (both stub fields already exist).
- After resolution the callsite (a) persists a `combat_log` row via `CombatLogService`, (b) updates Player metrics, (c) puts `{summary…, combat_log_id}` into `combat_result`.
- Legacy `FightStats` (`raw_hp/varied_hp/raw_dps/varied_dps/ttk`) **stay populated** for wire compat (HE-0): tick resolver maps `varied_hp`→effective start HP, `varied_dps`→`damage_dealt / duration_s`, `ttk`→`duration_s` (loser) / `None` (winner).
- Deferred to a later cycle: the `GET /combat-log/{id}` endpoint, gateway command, and any timeline→text condensation. Open knobs (retention hours; whether to denormalize summary columns) → **§2 O-LOG**; promoted Player metric field set → **§2 O-STAT**.

---

# 2. OPEN QUESTIONS

Status = OPEN unless noted. **This is the single canonical registry of every genuinely-open design question.** Resolve these before / during PR-4.

**ID namespaces (read once):**
- **`O-*` (this table)** — the only live open-question queue. Cite these everywhere going forward.
- **`C1–C9` (§6)** — *closed* condensation-pass disposition log (resolved / confirmed-in-§1 / moved here). Not an open queue; renamed from `O1–O9` to stop colliding with `O-*`.
- **`Q*/TH*/T*` (Entry 7, Historical)** — verbatim HE shorthand, bridged to `O-*` (e.g. `Q1 ≡ O-Q1`, `TH3 ≡ O-TH3`, `T2 ≡ O-T2`).

| ID | Topic | Status / Notes |
|---|---|---|
| O-Q1 | Cloak math: additive (`acc − 35pp`) / absolute (`set 25`) / multiplicative (`× 0.42`)? | ✅ **RESOLVED 2026-05-30 — ABSOLUTE set.** Opponent accuracy hard-set to `cloak_set_value` (default 0.25), overriding §1.5 layered terms (no stacking). Tiers differ by duration only in Phase-1. See §1.5 / §1.7 Cloaks. |
| O-T2 | Auto-turret accuracy multiplier final value within ×0.85–0.90 band | ✅ **RESOLVED 2026-05-30 — ×0.85.** Configurable (`AUTO_TURRET_ACCURACY_MULTIPLIER`, default 0.85). Applied AFTER §1.5 result (incl. cloak override), then re-clamped `[0.05, 0.99]`. Auto turrets inherit the cloak set-value. See §1.6 Turret weapons. |
| O-TH3 | Thruster opponent-accuracy debuff magnitude (close-range window) — scaling vs `effect_pct`? | ✅ **RESOLVED 2026-05-30 — REFRAMED to attacker-side bonus** (HE-7's defender-debuff framing was a thruster↔booster confusion, restored to HE-5f/5g). `bonus_pp = (effect_pct × k_thruster) × ramp(750→300m)`, `k_thruster` configurable (default 0.10, `THRUSTER_ACCURACY_BONUS_FACTOR`). Primaries only. 2–13pp peak across the 5 thrusters at default. See §1.5 / §1.7 Thrusters. |
| O-TH4 | Thruster passive vs toggled (with HP-thresholds + cooldown)? | ✅ **RESOLVED 2026-05-30 — PASSIVE.** Always active when `current_distance < 750m`; no HP threshold, no `duration_ms`, no cooldown, no toggle. See §1.7 Thrusters / §1.8. |
| O-B | Booster opponent-accuracy debuff magnitude — scaling vs `effect_pct`? | ✅ **RESOLVED 2026-05-30 — linear.** `debuff_pp = effect_pct × k_boost`, `k_boost` configurable (default 0.10, `BOOSTER_ACCURACY_DEBUFF_FACTOR`). 6–30pp across the 5 boosters; no cap (under cloak at default). Additive with distance-push. See §1.5 / §1.7 Boosters. |
| O-DP | Distance penalty for primaries — separate from rocket curve? Max value? | ✅ **RESOLVED 2026-05-30 — DOES NOT EXIST for primaries.** Range is a pure binary gate (§1.2/§1.6); within range, primaries fire at full §1.5 accuracy. The "0.20 max" was a stale carry-over from before primaries/rockets were split. Distance-as-accuracy lives entirely on the secondary side (rocket 5%→60% curve, missile tier-A degrade) — §1.6. Absorbed former §6 O2 (the duplicate). |
| O-M | Cluster-missile (3 files) + ionizing-missile (2 files) Phase-1 status: (a) treat as "missile" variants; (b) inert in Phase-1; (c) own rule. | ✅ RESOLVED 2026-05-30 → **split**: cluster-missile = in-scope missile variant with `burst_count` sub-munitions (N independent rolls against a fire-time accuracy snapshot, per-sub-munition damage, condensed to one combat-log event); ionizing-missile = Phase-3+ deferred (seed `damage` already 0, no ionizer mechanic planned). Seed-edit adds `burst_count` (3/4/5) to the 3 cluster-missile files. Promoted to `COMBAT_SPEC_LOCKED.md` §6.2 + new §14 (downstream sync). |
| O-N | Nuke AoE falloff specifics + per-nuke real damage values (Liberator/Oppressor anchors) | ✅ RESOLVED 2026-05-30 → mechanic locked. **No accuracy roll** (cloak/thruster/booster all ignored — nukes always apply). **Random epicenter** in `[300m, 5000m]` along 1D combat-distance axis. **Inverse-square falloff** `dmg = damage × (1 - min(1, d / eff_mag))²` with `eff_mag = magnitude_m × NUKE_MAGNITUDE_SCALE` (default **0.10**). **Self-damage** at `NUKE_FRIENDLY_FACTOR` (default **0.25**) — firer caught in own blast using same falloff formula at `d_firer = epicenter`. **Steerable flag ignored** Phase-1; per-nuke `damage` seed values (Liberator 850 / Extinctor 700 / Oppressor 400 / Tormentor 150 / Fireworks 1) accepted as direct-hit anchors. Promoted to `COMBAT_SPEC_LOCKED.md` §6.2 + Appendix A + Appendix B + §14. |
| O-PE | Pure-EMP weapons equipped in Phase-1 (fire, roll accuracy, apply 0 damage): (a) accept as player choice; (b) preflight warn; (c) filter at loadout-build. | ✅ RESOLVED 2026-05-30 → **(a) accept**. Combat log surfaces the 0-damage outcome post-fight; no preflight warning, no filter. Promoted to `COMBAT_SPEC_LOCKED.md` §4. Seed-fix `e87db57` corrected the 3 EMP-blaster primaries from misplaced-physical to true pure-EMP; Phase-1 pure-EMP set = 5 weapons (3 primaries + mamba_emp + netha_emp). |
| O-PWM | PrimaryWeaponMod (Nirai Overdrive / Overcharge) — which formula governs the new tick-based resolver? Spec §7.8 originally said "flat +N% dpsMultiplier" but seed data carries `damage_pct` + `fire_rate_pct` breakdowns that the spec ignored. | ✅ RESOLVED 2026-05-30 → **honor `damage_pct` + `fire_rate_pct`** breakdown; legacy `dpsMultiplier` is metadata-only (current SimpleTTKResolver + item-detail embed). Applies to **primary weapons only**. Effective damage rounds to integer, effective loading_speed_ms rounds to nearest TICK_MS (10ms). No floor guard on damage (base-0 stays 0, base-≥2 never reaches 0 from −10%). Promoted to `COMBAT_SPEC_LOCKED.md` §7.8 + Appendix B + §14. |
| O-LOG | Combat-log knobs (§1.12): `BOUNTYBOT_COMBAT_LOG_RETENTION_HOURS` (≈72 h default?), and whether any per-side summary fields get denormalized columns vs living only in the `data` JSON. (Discord rendering/condensation is a later cycle — out of scope.) | OPEN — design in §1.12; only the numerics/policy are unsettled |
| O-STAT | Exact set of lifetime combat-metric columns to add to `Player` (§1.12 stat promotion): beyond existing `duel_wins`/`bounty_wins`, which of `total_module_activations`, `total_nukes_fired`, `total_secondaries_fired`, `total_shots_fired`, `total_damage_dealt`, `total_fights`, … to persist? | ✅ RESOLVED 2026-05-30 → **3 fields only**: `total_fights`, `total_nukes_fired`, `total_module_activations` (all Integer, default 0). Dropped from consideration: `total_shots_fired` + `total_secondaries_fired` (grow uninterestingly fast); `total_damage_*` family (same big-number concern); `bounty_losses` (intentional asymmetry with current bounty scoring). One Alembic migration. Promoted to `COMBAT_SPEC_LOCKED.md` §13. |
| O-E | EMP mechanic full design (disable window, stacking, hit-roll, etc.) | ✅ LOCKED 2026-05-30 → **Phase-2 DEFERRED (formal)**. Partial Entry-7 #23 spec (victim outgoing damage = 0, firer accuracy vs victim = 100%, duration TBD) is a Phase-2 design starting-point, NOT a Phase-1 mechanic. All Phase-1 EMP weapons fire/roll/log/0-damage today per O-PE. Spec already reflects deferral in §4 / §11 / Appendix C — no spec edit needed. |

---

# 3. DEFERRED

| Item | Defer to | Reason |
|---|---|---|
| EMP mechanic | Phase-2 | New damage type; partial spec captured (victim outgoing damage = 0, firer accuracy vs victim = 100%, duration TBD); full design parked |
| ShieldInjector (Phoenix SIS) | Phase-2 | Plasma resource model needed |
| RepairBeam / TransfusionBeam | Phase-2 | Active heal pairing model needed |
| emp-bomb subtype | Phase-2 | EMP-only effect; physical track inert in Phase-1 |
| Mines | Phase-3+ | Deployment + proximity-trigger mechanic |
| Sentry-guns | Phase-3+ | Deployment + persistent-entity mechanic |
| Out-of-combat HP recovery (25%/hr players, 12.5%/hr criminals) | Phase-2 (schema in Phase-1) | Damage-tracking columns ship in Phase-1 migration |
| Dock instant-repair (2.5% of current credits) | Phase-2 | Pairs with OOC recovery |
| Damaged-opponent start state | Phase-2 (hooks in Phase-1) | Optional `current_hull / current_armour / current_shield` accepted by combatant init |
| Thermal-fusion homing effect | Indefinite | Phase-1 bypasses for simplicity; thermal-fusion follows primary rules |

---

# 4. IMPLEMENTATION PLAN

**Shipped on `dev`:**
- PR-1: Alembic migration — `ship.extra_atts` + Phase-2 damage-tracking columns on `Player` and `Bounty`
- PR-2: Loader patches — `"loading speed"` (space) → `loading_speed_ms` mapping; subtype normalization; `extra_atts` respected
- PR-3: Seed JSON enrichment — wiki values, `value = wiki median`, TL drift fixed, mechanics_text carried
- PR-A through PR-E: Commodity foundation (schema → seed → schemas → routers → cog)

**In progress:**
- PR-4: New tick-based combat resolver

**PR-4 file map:**
- `services/bot-core/src/services/game_constants.py` — add `UNIQUE_EQUIP_TYPES`, OOC recovery rates, dock cost; combat-log retention knob `BOUNTYBOT_COMBAT_LOG_RETENTION_HOURS` (default 72); resolved combat tunables `CLOAK_SET_VALUE` (default 0.25, O-Q1), `BOOSTER_ACCURACY_DEBUFF_FACTOR` (default 0.10, O-B), `THRUSTER_ACCURACY_BONUS_FACTOR` (default 0.10, O-TH3), `AUTO_TURRET_ACCURACY_MULTIPLIER` (default 0.85, O-T2) — all env/per-guild per §1 config policy (§1.5/§1.6/§1.7, O-LOG)
- `services/bot-core/src/services/combat_balance.py` — NEW: per-subtype defaults, empty `SUBTYPE_ACCURACY_MOD`
- `services/bot-core/src/services/combat_models.py` — drop `WeaponStats.accuracy_modifier`; keep `ModuleStats.accuracy_modifier`. `FightResults.combat_log` + `.metadata` fields ALREADY EXIST (stubs) — tick resolver populates them in memory (§1.12); no model change needed
- `services/bot-core/src/services/combat_service.py` — public API unchanged; default resolver swapped to new tick resolver; `SimpleTTKResolver` kept behind feature flag for one release
- `services/bot-core/src/services/combat/` — NEW package: `combatant.py`, `tick_resolver.py`, `weapon_systems.py`, `module_systems.py`, `event_log.py` (← Tier-0 summary aggregator + event-tick `CombatEvent` timeline collector; emits a row per state-changing tick-step in processing order, §1.12)
- `services/bot-core/src/services/bounty_service.py` — extend `_serialize_fight_results()` to emit Tier-0 `summary` + `combat_log_id` (NOT the full timeline — that goes to the DB). Wire slot `combat_result: dict` is free-form so this is additive/non-breaking
- **Player stat promotion (§1.12):** extend `services/bot-core/src/persist/models/player.py` with 3 new lifetime combat-metric Integer columns (default 0) — `total_fights`, `total_nukes_fired`, `total_module_activations` (RESOLVED O-STAT 2026-05-30) — plus an Alembic migration. Combat processor increments them on each human combatant's `Player` row post-fight. NOT stored in `combat_log`.

**PR-5 — combat-log persistence slice (NEW table + retention; §1.12). NB: Discord visualization / on-demand render endpoint is a LATER cycle, not PR-5:**
- `services/bot-core/src/persist/models/combat_log.py` — NEW `CombatLog` model (`id` Integer PK, `guild_id` BigInteger, `context` String(20), `combatant{1,2}_name` String(255), `combatant{1,2}_user_id` BigInteger nullable [NULL ⇒ NPC], `winner_name` nullable, `is_stalemate` Boolean, `data` `JSON`, `created_at`). Add `CombatLog = "combat_log"` to `TableNames` enum.
- Alembic migration — create `combat_log` table (+ index on `created_at` for retention scans, optional on `guild_id`).
- `services/bot-core/src/persist/repositories/combat_log_repository.py` — NEW: `add(...)`, `get_by_id(...)`, `delete_older_than(cutoff)`.
- `services/bot-core/src/services/combat_log_service.py` — NEW: `persist(fight_results, *, context, combatants) -> int` (returns the row id; called by the 5 callsites after resolution, `commit=False`).
- `services/bot-core/src/utils/executors/db_retention_executor.py` — extend to call `CombatLogRepository.delete_older_than` using `COMBAT_LOG_RETENTION_HOURS` (mirrors bounty/duel/audit retention).
- Tests: extend `tests/services/test_combat_service.py`; add `tests/services/test_combat_log_service.py` + retention coverage; ≤ 2 mocks per test (per services AGENTS.md).

**Later cycle (out of current scope):** `GET /api/v1/combat-log/{id}` endpoint, `BattleLog` deserialization + timeline→text condensation, and the discord-gateway `/combat-log` command. User-facing visualization is deferred; condensation/summarization helpers will live on the consumer side.

**Combat callsites (preserve `CombatService.fight_ships` contract):**
- `services/bot-core/src/services/duel_service.py:233`
- `services/bot-core/src/services/bounty_service.py:1318` (Bronze 2× bonus)
- `services/bot-core/src/services/bounty_service.py:1357` (Silver/Gold/Platinum gate)
- `services/bot-core/src/api/routers/bounties.py:227` (cleanup target — fresh `CombatService` per request)
- `services/bot-core/src/services/combat_preflight_service.py:173` (Monte-Carlo estimator)

**Public contract:** `CombatService.fight_ships(loadout1, loadout2, variance_percent=None, player_armour_buff=1.0, guild_config=None) -> FightResults` + statics + `CombatResolver` Protocol. (HE-0 / HE-4)

**Combat-log / results output wiring (§1.12):**
- **Mechanics + persistence only here; rendering is a later cycle.** Resolver builds the full event-tick timeline + summary in memory; the fight response carries only the small Tier-0 `summary` + `combat_log_id`. The full timeline is persisted to the `combat_log` table.
- **`FightStats` legacy fields stay populated** for wire compat (HE-0): tick resolver maps `varied_hp`→effective start HP, `varied_dps`→`damage_dealt / duration_s`, `ttk`→`duration_s` (loser) / `None` (winner). Per-layer richness lives in the persisted `data` JSON, NOT by mutating frozen `FightStats`.
- **Consumers unaffected unless they opt in:** `duelCog`/`bountyCog` read the same dict; the new `summary` + `combat_log_id` keys are ignored by existing renderers until wired.
- **Player metrics promoted separately:** combat processor increments lifetime counters on each human combatant's `Player` row post-fight (O-STAT) — independent of the `combat_log` table.
- **Size bounded by retention, not truncation:** stored timeline is whole; `db_retention` clears rows > ~72 h.

---

# 5. DATA REFERENCES

| Resource | Path |
|---|---|
| This journal | `/proj/COMBAT_REWRITE_JOURNAL.md` |
| Seed JSONs (runtime canonical) | `/proj/services/bot-core/import_data/{ship,primary_weapon,secondary_weapon,turret_weapon,module,criminal,system,commodity}/*.json` |
| Wiki v2 (catalog truth) | `/proj/.combat-rewrite-wiki-v2/{primary,secondary,turret,module,ship,commodities}/*.json` |
| Schema mapping report (architect) | `/proj/COMBAT_SCHEMA_MAPPING.md` |
| Audit report (stale 2026-05-22) | `/proj/AUDIT_REPORT.md` |
| Current combat code (being replaced) | `/proj/services/bot-core/src/services/combat_service.py` |
| Combat models (extend, don't rewrite) | `/proj/services/bot-core/src/services/combat_models.py` |
| Live DB (via docker) | `sudo docker exec bountybot-db psql -U bounty -d bountydb` |
| Live API (via docker) | `sudo docker exec bountybot-bot-core curl -s http://localhost:8000/api/v1/...` |

---

# 6. CONDENSATION REVIEW — DISPOSITION LOG (closed)

*Queue surfaced during the 2026-05-29 condensation pass, fully processed by 2026-05-30. Every item is now **RESOLVED**, **CONFIRMED** in §1, or **MOVED** to the canonical §2 registry. Renumbered `C1–C9` so the prefix no longer collides with §2's `O-*`. No open items remain here — live questions live in §2.*

| # | Topic | Disposition |
|---|---|---|
| C1 | **Repair Bot rate canonical rule** | ✅ **RESOLVED 2026-05-30** — percentage-of-max. Ketar I = 2.5%/s, Ketar II = 5.0%/s of `max_hull + max_armour`. Seed `extra_atts.HPps` (7/15) is stale; ignore. Rates are starting defaults; configurable per §1's Configuration policy. §1.3 + §1.7 updated. |
| C2 | **Primary distance penalty** | ➡️ **MOVED → §2 O-DP** (single source of truth). Was a duplicate of the §2 entry; resolve it there. |
| C3 | **Entry 7 roster wording (hull / auto-turrets as "modules")** | ✓ **CONFIRMED** — Entry 7's "in: shields, hull, armour, repair-bot, … auto-turrets, scanners" was a loose list of *Phase-1 combat-relevant loadout items*, not a literal SQLAlchemy module-type claim. §1.7 already reflects this. |
| C4 | **Specter / Scimitar seed JSON `builtinModules`** | ✅ **RESOLVED 2026-05-30** — both files DO populate `builtinModules: ["U'tool"]` (gap closed during PR-3 enrichment). §1.7 Cloaks paragraph reflects actual state. No further action. |
| C5 | **Non-combat modules explicit ruling** | ✓ **CONFIRMED** — §1.7 gives the one-line "resolver ignores entirely" rule for the 8 non-combat modules, with PrimaryWeaponMod carved out separately as combat-relevant. |
| C6 | **cluster-missile + ionizing-missile Phase-1 status** | ✅ **CLOSED via §2 O-M (split decision)** (2026-05-30). Cluster-missile in-scope as burst-roll missile variant; ionizing-missile deferred to Phase-3+. |
| C7 | **Shock-blast + in-flight projectiles** | ✓ **CONFIRMED** — all firings resolve same-tick (no multi-tick projectile travel in Phase-1), so a shock-blast distance reset cannot strand an in-flight projectile. Question moot. |
| C8 | **Pure-EMP weapons in Phase-1 loadout** | ✅ **CLOSED via §2 O-PE → (a) accept** (2026-05-30). Seed-fix `e87db57` reclassified the 3 EMP-blaster primaries as true pure-EMP (Phase-1 pure-EMP set now = 3 primaries + mamba_emp + netha_emp). |
| C9 | **Reorganization approach** | ✓ **CONFIRMED** — §1–§5 canonical + §6 disposition log + §7/§8 reviews on top, Historical Entries preserved verbatim below. |

---

# 7. Researcher verification pass (2026-05-30)

*Final verification pass on §1 CURRENT DECISIONS against Historical Entries and live seed data. All claims spot-checked against `/proj/services/bot-core/import_data/` and Historical Entries.*

## §1.1 — Tick & timing — ✓ verified

## §1.2 — Distance model — ✓ verified

## §1.3 — HP layers + damage stacking + regen

### ⚠️ Repair Bot rate specification mismatch

**Finding:** §1.3 states repair bot rates are "2.5%/sec (Ketar I), 5.0%/sec (Ketar II) of `max_hull + max_armour`" but seed data shows flat HPps values, not percentages.

| Module | §1.3 claim | Seed data | Discrepancy |
|---|---|---|---|
| Ketar Repair Bot | 2.5%/sec | `extra_atts.HPps: 7` | Percentage vs flat rate |
| Ketar Repair Bot II | 5.0%/sec | `extra_atts.HPps: 15` | Percentage vs flat rate |

**Source:** `/proj/services/bot-core/import_data/module/repair_bots.ketar_repair_bot.json` (L16), `ketar_repair_bot_ii.json` (L19).

**Severity:** Critical. The resolver needs to know which rate model applies (percentage-of-max vs flat HPps). Entry 5l notes these are "pre-existing seed values, not wiki-sourced," but §1.3's lock contradicts the seed data.

**Recommended action:** Clarify which model the user intends before PR-4 code writes the repair bot logic.

## §1.4 — Damage type model

### ✓ EMP-blaster primaries verified

- `dia_emp_mk_iii.json`: `damage_per_shot: 8` ✓
- `luna_emp_mk_i.json`: `damage_per_shot: 3` ✓
- `sol_emp_mk_ii.json`: `damage_per_shot: 5` ✓

### ✓ Pure-EMP secondary verified

- `missiles.mamba_emp.json`: `damage: 0, emp_damage: 100` ✓

### ✓ Hybrid secondaries verified

All 7 exist: `dephase_emp` (120+100), `intelli_jet` (100+50), `emp_rocket_mk_i`, `emp_rocket_mk_ii`, `emp_gl_dx`, `emp_gl_i`, `emp_gl_ii`. ✓

### ✓ Armour rocket bonus surprise verified

`rockets.armour_rocket.json`: `damage: 72, emp_damage: 24` ✓

## §1.5 — Accuracy system — ✓ verified

## §1.6 — Weapons — ✓ verified

## §1.7 — Modules

### ⚠️ Built-in modules claim outdated

**Finding:** §1.7 states "Data gap: Specter / Scimitar seed JSONs currently do NOT populate `builtinModules: ["U'tool"]` (verified 2026-05-29)."

**Reality:** Both files have the field populated:
- `/proj/services/bot-core/import_data/ship/nivelian.specter.json` (L56-58): `"builtinModules": ["U'tool"]` ✓
- `/proj/services/bot-core/import_data/ship/nivelian.scimitar.json` (L56-58): `"builtinModules": ["U'tool"]` ✓

**Severity:** Cosmetic. The gap mentioned in O4 has been filled (likely during PR-3 enrichment). Journal text is now stale but does not affect code correctness since both ships correctly have the field.

**Recommended action:** Update §1.7's O4 entry to note "RESOLVED: both seed JSONs populated 2026-05-30" when documenting this pass.

### ⚠️ Module type name confusion

**Finding:** §1.7 states "Non-combat modules (no Phase-1 combat effect, resolver ignores entirely): TimeExtender (Khador Drive)..." but the actual type mapping is:
- **Khador Drive** → `type: "JumpDriveModule"` (not TimeExtender)
- **Rhoda Vortex** → `type: "TimeExtenderModule"` (not JumpDrive)

**Source:** `/proj/services/bot-core/import_data/module/misc.khador_drive.json` (L10), `misc.rhoda_vortex.json` (L12).

**Severity:** Cosmetic (parenthetical naming confusion only; the mechanics are clear). Code should discriminate by `Item.type` value, not by the mnemonic name in the journal.

**Recommended action:** Clarify in text that Khador Drive is the JumpDrive and Rhoda Vortex is the TimeExtender. No code impact.

### ✓ Cloaks verified

3 files: utool (duration_ms=10000), sight_suppressor_ii (20s), shadow_ninja (40s). ✓

### ✓ Scanners verified

4 files with correct lock times: Telta Quickscan (4.0s), Telta Ecoscan (3.0s), Hiroto Proscan (1.8s), Hiroto Ultrascan (1.8s). ✓

### ✓ Boosters verified

5 files: cyclotron, linear, meal, polytron, synchrotron. ✓

### ✓ Thrusters verified

5 files: dozzt, mpzzzm, pendular, pulsed_plasma, static. ✓

### ✓ Emergency System verified

`misc.emergency_system.json`: `duration_ms: 10000` (10s). ✓

### ✓ Shield regen formula verified

Targe Shield: 50 capacity, 20000ms recharge. Formula `N = ceil(20000 / 50 / 10) = 40 ticks` ✓

### ✓ Shock-blast verified

`misc.shock_blast.json`: seed `damage: 140, emp_damage: 80` correctly marked as ignored in Phase-1. ✓

### ✓ PrimaryWeaponMod verified

2 files: nirai_overcharge, nirai_overdrive. ✓

### ✓ Non-combat modules verified

All 8 categories present: Cabins (3 files), Mining drills (5 files), Signatures (2+ files), TractorBeam, SpectralFilter, Compressor, Khador Drive (JumpDrive), Rhoda Vortex (TimeExtender). ✓

## §1.8–§1.11 — Activation rules, fight termination, unique-equip list — ✓ verified

## Summary

| Severity | Count | Items |
|---|---|---|
| 🔴 Critical | 1 | Repair Bot rate model (§1.3): percentage vs flat HPps conflict |
| ⚠️ Cosmetic | 2 | Built-in modules claim outdated (§1.7 O4); module type naming confusion (§1.7) |
| ✓ Verified | 13+ | All primary claims, EMP inventory, module counts, specifications, mechanics |

## Recommended actions

1. **Before PR-4 code:** Clarify repair bot rate model (percentage-of-max or flat HPps from seed). Current §1.3 lock contradicts seed data; resolver needs explicit direction.
2. **Journal housekeeping:** Update §1.7 O4 to note built-in modules gap is resolved; clarify Khador/Rhoda type mapping in text (cosmetic, no code impact).
3. **No blocking issues:** All other §1 claims verified against seed data and HE-N citations. Journal is accurate for implementation.

---

# Historical Entries

*Verbatim from prior sessions. Subsequent decisions in §1 supersede any conflicting text below. Entries 0 through 7 follow unchanged from their original write — this includes superseded locks (e.g. HE-3 #5's "EmergencySystem at 25% HP" replaced by HE-7's lethal-blow rule). When in doubt, §1 wins.*

## Entry 0 — Codebase Map (2026-05-24)

Initial exploration to map every file/symbol touched by the current combat system,
so the rewrite has a complete view of the public contract, callers, tests, and
downstream consumers.

### Core files

| File | Purpose |
|---|---|
| `services/bot-core/src/services/combat_service.py` (527 LOC) | `CombatService`, `SimpleTTKResolver`, `_apply_variance`, `_apply_variance_float` |
| `services/bot-core/src/services/combat_models.py` | Dataclasses (`WeaponStats`, `ModuleStats`, `UpgradeStats`, `ShipLoadout`, `CombatStats`, `FightStats`, `FightResults`) + `CombatResolver` Protocol |

### Current public contract (must preserve OR migrate carefully)

```python
CombatService(resolver: CombatResolver | None = None)
  .fight_ships(
      loadout1: ShipLoadout,
      loadout2: ShipLoadout,
      variance_percent: float | None = None,
      player_armour_buff: float = 1.0,
      guild_config = None,
  ) -> FightResults
  .collect_stats(loadout: ShipLoadout) -> CombatStats
  .get_dps(loadout: ShipLoadout) -> float        # @staticmethod
  .get_armour(loadout: ShipLoadout) -> int       # @staticmethod
  .get_shield(loadout: ShipLoadout) -> int       # @staticmethod

# Resolver Protocol
class CombatResolver(Protocol):
    def resolve(
        self,
        ship1_stats: CombatStats,
        ship2_stats: CombatStats,
        variance_percent: float,
    ) -> FightResults: ...
```

### Current legacy formulas (from `combat_service.py`)

```
DPS    = (Σ weapon.dps + Σ turret.dps + Σ module.dps) × Π module.dps_multiplier
Armour = int( (base_armour + Σ module.armour + Σ upgrade.armour)
              × Π module.armour_multiplier × Π upgrade.armour_multiplier )
Shield = int( Σ module.shield × Π module.shield_multiplier )    # no intrinsic ship shield
total_hp = armour + shield
```

`SimpleTTKResolver` algorithm:
1. Roll uniform variance on each ship's HP and DPS (4 independent rolls, ±variance_percent).
2. `ttk_i = varied_hp_i / opponent_varied_dps` (None if opponent DPS == 0).
3. Longer-surviving ship wins. Both-zero-DPS → stalemate. Exact tie → stalemate.

Variance helpers use `int`-truncated bounds + `random.randint` to match historical
behaviour from the removed legacy `shipBase.py`.

### `FightResults` fields consumed downstream (wire contract)

```
winner_name: str | None
loser_name:  str | None
is_stalemate: bool
variance_percent: float
ship1_stats / ship2_stats: FightStats(
    ship_name, raw_hp, raw_dps, varied_hp, varied_dps, ttk
)
```

`bounty_service._serialize_fight_results()` (bot-core L51-88) is the canonical
serializer that flattens this into a JSON dict for the gateway.

---

## Production callers (only 5 sites)

| # | File | Line | Context |
|---|---|---|---|
| 1 | `services/bot-core/src/services/duel_service.py` | L233 | PvP duel resolution in `accept_duel`. Default variance, no armour buff. |
| 2 | `services/bot-core/src/services/bounty_service.py` | L1318 | Bronze tier optional 2× combat bonus during `_process_single_bounty_check`. |
| 3 | `services/bot-core/src/services/bounty_service.py` | L1357 | Silver/Gold/Platinum mandatory combat gate during `_process_single_bounty_check`. Applies `bounty_pvc_armour_buff_factor` (default 1.5×). |
| 4 | `services/bot-core/src/api/routers/bounties.py` | L227 | `POST /api/v1/bounties/combat-bonus` endpoint — Bronze post-capture bonus duel. **Instantiates a fresh `CombatService()` (does NOT reuse BountyService's instance — possible cleanup target).** |
| 5 | `services/bot-core/src/services/combat_preflight_service.py` | L173 | Monte-Carlo win-rate estimator (default 20 sims) called by `/promote` confirmation. Returns `PreflightVerdict` GREEN/YELLOW/RED/NO_DATA. |

The Discord gateway has **zero** direct imports of combat code — it only consumes
serialized dicts over HTTP.

---

## `combat_models.py` consumers

### Production
- `combat_service.py` L20-26 — `CombatResolver`, `CombatStats`, `FightResults`, `FightStats`, `ShipLoadout`
- `loadout_builder.py` L17 — `ModuleStats`, `ShipLoadout`, `WeaponStats`
- `bounty_service.py` L28 — `ShipLoadout`, `WeaponStats` (used by `_build_criminal_loadout` L586 and `_build_player_loadout` L604)
- `duel_service.py` / `combat_preflight_service.py` — indirect (via `LoadoutBuilder`)

### Tests (notable)
- `tests/services/test_combat_service.py` (611 LOC, reference pattern)
- `tests/services/test_duel_service.py` (custom resolvers: `ChallengerWinsResolver`, `TargetWinsResolver`, `StalemateResolver`)
- `tests/services/test_loadout_builder.py` (instantiates `CombatService()` ×11 for end-to-end stat collection)
- `tests/services/test_combat_preflight_service.py`
- `tests/services/test_bounty_service.py` (12 mock sites of `fight_ships`; B.57 armour-buff coverage L4486-4612)
- `tests/api/test_bounty_router.py` (5× `@patch("services.combat_service.CombatService")`)
- `tests/integration/test_duel_service_integration.py` L431
- `tests/test_bounty_payout_embed.py` L401

---

## Loadout pipeline (feeds combat)

| File | Role |
|---|---|
| `services/bot-core/src/services/loadout_builder.py` (265 LOC) | `LoadoutBuilder.from_player(db, player_id)` builds `ShipLoadout` from PlayerShip + equipped item DB rows. `from_criminal_ship(dict)` builds it from `Bounty.criminal_ship` JSONB. `_module_stats_from_extra` handles snake_case vs camelCase keys from legacy JSON. |
| `services/bot-core/src/services/loadout_consistency_service.py` (825 LOC) | **Single canonical mutation choke-point** for `player_ships.{weapons,modules,turrets,secondary_weapons}` JSON ↔ `player_inventories`. Enforces I1-I4 invariants and `GameConstants.MODULE_EQUIP_LIMITS`. Do not bypass. |
| `services/bot-core/src/services/loadout_effect_service.py` (171 LOC) | Pre-formats module effects (`MODULE_EFFECT_MAP` L43-60) into `EffectItem` list for Discord embeds. Pure presentation. |
| `services/bot-core/src/services/loadout_response_service.py` (508 LOC) | Builds the unified `LoadoutResponse` for `/players/{id}/loadout` and `/bounties/{id}/loadout`. Performs criminal-only Cabin/CompressorModule dedup (L37-60). **HARD invariant:** never apply dedup to player loadouts. |

---

## Routers that trigger combat

| Router | Endpoint | Trigger |
|---|---|---|
| `bot-core/src/api/routers/duels.py` | `POST /api/v1/duels/{duel_id}/accept` (L258) | → `DuelService.accept_duel` → `CombatService.fight_ships` PvP. Returns free-form dict with `challenger_hp`, `challenger_dps`, `target_hp`, `target_dps` (L326-343). |
| `bot-core/src/api/routers/bounties.py` | `POST /api/v1/bounties/check` (L131) | → `BountyService.check_bounty` → combat at L1318 or L1357 depending on tier. |
| `bot-core/src/api/routers/bounties.py` | `POST /api/v1/bounties/combat-bonus` (L184) | Directly instantiates `CombatService` (L199, 226). |
| `bot-core/src/api/routers/players.py` | `GET /api/v1/players/{id}/combat-preflight` (L374) | → `CombatPreflightService.estimate()`. |

---

## Discord-gateway consumers

Pure HTTP consumers (no Python imports of combat_service):

| Cog | File | Lines | Use |
|---|---|---|---|
| `duelCog.py` | `discord-gateway/src/cogs/duelCog.py` | L311 (challenge), L438 (accept), L488 (`_build_accept_embed`) | `/duel-challenge`, `/duel-accept`, `/duel-reject`, `/duel-cancel`. Renders winner_name, is_stalemate, varied HP/DPS. |
| `bountyCog.py` | `discord-gateway/src/cogs/bountyCog.py` | L258 (check), L378-389, L431, L455-465, L531-573, L615 (`_build_capture_embed`), L624-658 | Reads `combat_won`, `bonus_won`, `combat_result` from `BountyCheckResponse`. |
| `bountyCog.py` | (same) | L919 | `/criminal-loadout` — uses `LoadoutResponse`. |
| `playerCog.py` | `discord-gateway/src/cogs/playerCog.py` | L600 | `/promote` — preflight; failure non-fatal (L620). |

**No gateway cog calls `/bounties/combat-bonus` directly** — Bronze 2× bonus is invoked server-side from the `/check` flow.

---

## Schemas

| File | Combat-bearing types |
|---|---|
| `bot-core/src/api/schemas/loadout_schema.py` (118 LOC) | `EffectItem`, `LoadoutWeaponItem`, `LoadoutModuleItem`, `CargoItem`, `ShipStats`, `LoadoutResponse`. No combat result fields. |
| `bot-core/src/api/schemas/duel_schema.py` (33 LOC) | `DuelRequestCreate`, `DuelRequestResponse`, `DuelResultResponse` (winner_name, loser_name, is_stalemate, winner_credits, loser_credits). **Schema drift:** duel router actually returns a free-form dict with varied HP/DPS, not `DuelResultResponse`. |
| `bot-core/src/api/schemas/bounty_schema.py` (161 LOC) | `BountyCheckOutcome` (L61, includes `combat_result: dict \| None`, `combat_won`, `bonus_won`, `total_reward`, `criminal_ship`), `BountyCheckResponse` (mirrors fields), `CombatBonusRequest` (L128), `CombatBonusResponse` (L136). |
| `bot-core/src/utils/bounty_announcement_payload.py` | L147 `combat_result` param; L192-243 renders varied stats + buff metadata; L226-227 reads `pvc_armour_buff_applied` / `pvc_armour_buff_factor`. |

---

## Game constants (`services/bot-core/src/services/game_constants.py`)

| Constant | Line | Default | ENV / Override |
|---|---|---|---|
| `DUEL_VARIANCE_PERCENT` | 181 | `0.05` (±5%) | `BOUNTYBOT_DUEL_VARIANCE_PERCENT`; per-guild `duel_variance_percent` |
| `DUEL_LOG_MAX_LENGTH` | 182 | `10` | (reserved, future tick-based combat log) |
| `DUEL_CLOAK_CHANCE` | 183 | `20%` | (future cloak mechanics) |
| `BOUNTY_PVC_ARMOUR_BUFF_FACTOR` | 192 | `1.5` (+50%) | `BOUNTYBOT_BOUNTY_PVC_ARMOUR_BUFF_FACTOR`; per-guild `bounty_pvc_armour_buff_factor` |
| `MODULE_EQUIP_LIMITS` | 251-273 | 21-entry dict | Not env-overridable. Examples: `ArmourModule=1`, `ShieldModule=1`, `CabinModule=-1` (unlimited), `JumpDriveModule=0` (disabled) |
| `DEFAULT_ACCURACY` / `DEFAULT_EVASION` | 280-281 | `1.0` / `0.0` | Future placeholders, currently unused |
| `CLOAK_ACCURACY_PENALTY`, `SCANNER_ACCURACY_BONUS`, `THRUSTER_EVASION_BONUS`, `SHIELD_RECHARGE_RATE` | 284-289 | `0.0` | Future-mechanic placeholders |

`resolve_constant(guild_config, field, fallback)` at L398 — per-guild override helper. Used by:
- `combat_service.py:489` → `duel_variance_percent`
- `bounty_service.py:1304` → `bounty_pvc_armour_buff_factor`
- `bounties.py:216` → `bounty_pvc_armour_buff_factor`

Per-guild override storage:
- `persist/models/guild_config.py:97-98` — `bounty_pvc_armour_buff_factor`, `duel_variance_percent` columns (nullable Float)
- `api/schemas/config_schema.py:20-21` — Pydantic constraints
- `api/routers/config.py:48-49` — updatable-fields whitelist
- `persist/database/revisions/versions/0005_b49_per_guild_game_constants.py:30-31` — Alembic migration

---

## Notable findings & cleanup targets

1. **Schema drift (duel):** `DuelResultResponse` is declared but the `/duels/{id}/accept` endpoint returns a free-form dict including varied HP/DPS. Rewrite is a good chance to align.
2. **Duplicate `CombatService()` instantiation:** `bounties.py:226` creates a fresh `CombatService` instead of reusing `BountyService.combat_service`. Consolidate via DI / shared instance.
3. **Missing doc reference:** `combat_service.py:9` cites `docs/analysis/03b-combat-system.md` — that file does NOT exist in the repo (legacy ref). Update docstring or write a fresh design doc.
4. **`FightResults` has no `combat_log` field:** if the new system is tick/turn-based, that's the surface that needs adding. Both wire schemas already carry a `combat_result: dict` slot, so adding fields is non-breaking for the gateway.
5. **`DEFAULT_ACCURACY` / `DEFAULT_EVASION` / cloak / thruster / shield-recharge placeholders are unused.** They exist on `CombatStats` but never affect resolution today. A proper combat system likely activates these.
6. **Variance helpers (`_apply_variance` / `_apply_variance_float`) reproduce a legacy int-truncation quirk** that may not be desirable in a modern design — they truncate via `int()` before `random.randint`, biasing low at very small values.
7. **Stat collection is fully separable from resolution.** The Protocol-based `CombatResolver` already cleanly splits them — the rewrite can keep `collect_stats` / `get_dps` / `get_armour` / `get_shield` static API and only swap the resolver implementation if desired.

---

## Tests to keep green (priority order)

1. `tests/services/test_combat_service.py` — reference pattern; covers stat collection, variance helpers, resolver protocol satisfaction (L466), `player_armour_buff` (L519-608).
2. `tests/services/test_combat_preflight_service.py` — Monte-Carlo verdicts (9 scenarios).
3. `tests/services/test_duel_service.py` — full PvP flow with injected resolvers (L468/514/557/671).
4. `tests/services/test_bounty_service.py` — PvC flows + buff coverage.
5. `tests/api/test_bounty_router.py` — `POST /combat-bonus` (5× CombatService patch).
6. `tests/api/test_duel_router.py` — duel response shape (L92).
7. `tests/services/test_loadout_builder.py` — end-to-end stat collection ×11.
8. `tests/integration/test_duel_service_integration.py` — DB-backed PvP.
9. `tests/test_bounty_payout_embed.py` — serialized `FightResults` embed shape (L401).

---

## Quick-reference: "What to update when replacing `combat_service.py`"

| Surface | Files |
|---|---|
| Public API used externally | `CombatService.__init__`, `fight_ships`, `collect_stats`, `get_dps`, `get_armour`, `get_shield` |
| Required output shape | `FightResults`, `FightStats` — consumed by `_serialize_fight_results`, duel router, bounty announcements, gateway embeds |
| Resolver protocol contract | `CombatResolver.resolve(ship1_stats, ship2_stats, variance_percent) -> FightResults` (`combat_models.py:231`) |
| Direct callers (5) | `duel_service.py:233`, `bounty_service.py:1318`, `bounty_service.py:1357`, `bounties.py:227`, `combat_preflight_service.py:173` |
| Constants | `DUEL_VARIANCE_PERCENT`, `BOUNTY_PVC_ARMOUR_BUFF_FACTOR`, `MODULE_EQUIP_LIMITS`, `resolve_constant()` for per-guild overrides |
| Gateway impact | None (Python). Wire format = `_serialize_fight_results` dict + bounty/duel response schemas. |

---

## Open design questions (to resolve before writing code)

- **Combat model**: tick-based / turn-based / analytical-TTK improvement / hybrid?
- **Accuracy & evasion**: activate the existing placeholders? How do they fold into damage application (multiplicative damage modifier vs. shot-by-shot hit roll)?
- **Shields**: recharge over time? distinct damage profile (kinetic vs energy)?
- **Secondary weapons & ammo**: currently inert in combat — incorporate?
- **Cloak / scanner / thruster modules**: activate `MODULE_EFFECT_MAP` effects?
- **Combat log**: structured per-tick log? How verbose? Gateway embed length budget?
- **Resolver strategy**: keep `CombatResolver` Protocol and ship multiple resolvers (e.g., `SimpleTTKResolver` legacy + `TickResolver` new), pluggable per game mode (PvP vs PvC)?
- **PvC armour buff**: keep current `player_armour_buff` param shape, or generalize to a `CombatModifiers` dataclass?
- **Variance**: keep uniform ±N% or move to gaussian / per-stat / per-weapon?
- **Determinism / seeding**: should resolver accept an RNG seed for replay & testing?
- **Backwards compatibility**: do we keep `SimpleTTKResolver` as a fallback, or full cut-over?

---

## Entry 1 — Data Inventory & Design Constraints (2026-05-24)

User direction: **tick-based simulation; accuracy is primary variance (hit/miss); weapon fire rates matter; shield recharge, hull repair (repair bot), thrusters, cloaks, boosters, auto-fire turrets, scanners, secondary weapons (dumb-fire vs heat-seeking)**.

Audited the live DB and the seed JSON files to inventory what data we actually have to drive this. Findings:

### Ships (65 rows)

| Field | Range / values | Combat meaning |
|---|---|---|
| `armour` (int) | 95–2100, mean 385 | Currently the ship's only HP — will be **hull** in new model |
| `handling` (int) | 10–162, mean 110 | Proxy for **evasion**; multiplied by `ThrusterModule.handlingMultiplier` |
| `max_primaries` | 1–4 | Slot count |
| `max_secondaries` | 0–3 | Slot count |
| `max_turrets` | 0–1 | Slot count |
| `max_modules` | 3–13 | Slot count |
| `builtin_modules` | `varchar[]` | Pre-equipped modules baked into the hull |

**Sample (low end):** Betty `armour=95 handling=120 1p/1s/0t/3m`. **High end:** large ships ~2100 armour.

No shield, no hull, no accuracy stat. Handling is the only stat suggestive of combat behaviour.

### Primary weapons (40 rows)

| Field | Detail |
|---|---|
| `dps` | float, 7.5 – 92.3 |
| `tech_level` | 1–10 |
| `extra_atts.subtype` | one of: `auto_cannons`, `beam_lasers`, `blaster_lasers`, `blasters`, `emp_blasters`, `scatter_guns`, `thermal_fusion_cannons` |

**Missing for tick combat:** fire-rate, damage-per-shot, accuracy modifier, damage type. **Subtype is the only flavour signal** — it's our hook for assigning sensible defaults.

### Turret weapons (10 rows)

| Field | Detail |
|---|---|
| `dps` | float, 0–90 |
| `automatic` | bool |
| `subtype` | `auto` (3, all automatic), `manual` (4, all non-automatic), `plasma_collectors` (3, dps=0 — non-combat) |

Automatic turrets fire on their own; manual turrets are likely "pilot-aimed" (treat as fire-rate-bounded primary). Plasma collectors are non-combat (resource gather).

### Secondary weapons (30 rows) — **MAJOR DATA HOLE**

| Field | Detail |
|---|---|
| `damage` (int) | All zero in DB except Shesha (60) |
| `loading_speed` (column) | NULL for all rows (the JSON has a `"loading speed"` key with mostly 0) |
| `subtype` | `missiles`, `rockets`, `cluster_missiles`, `ionizing_missiles`, `emp_bombs`, `mines`, `nukes`, `sentry_guns`, `misc` |

Damage and loading speed are essentially unpopulated. Either pull from wiki or assign per-subtype defaults.

### Modules — type-by-type combat relevance

| Module Type | Combat fields in JSON | Combat role in new model |
|---|---|---|
| `ArmourModule` | `armour` int (40 / 80 / 110 / 160 / 250) | Adds to ship's armour pool (intermediate damage layer above hull) |
| `ShieldModule` | `shield` int (50 / 80 / 120 / 150 / 220 / 380) | Adds to shield pool (regenerates over time — see below) |
| `ShieldInjectorModule` | `plasmaConsumption` (Phoenix SIS, 30) | Active shield refill at cost of "plasma" resource |
| `GammaShieldModule` | `effect` (0.4 / 0.6) | Flat damage reduction multiplier (resistance) |
| `BoosterModule` | `duration`, `effect` (1.6 / 1.8 / 2.6 / 3 / 4) | Temporary speed/evasion buff |
| `ThrusterModule` | `handlingMultiplier` (1.3 / 1.4 / 1.5 / 1.7 / 1.8 / 2.0) | Passive evasion multiplier on `ship.handling` |
| `CloakModule` | `duration` (10 / 20 / 40 s) | Untargetable window |
| `ScannerModule` | `timeToLock` (4 / 3 / 1.8 / 1.8) | Accuracy boost; lock-on speed for missiles |
| `RepairBotModule` | `HPps` (7 / 15) | Hull repair per second |
| `RepairBeamModule` / `TransfusionBeamModule` | `count`, `effect` (e.g. count=3, effect=2) | Active heal beam — N pulses of `effect` HP |
| `EmergencySystemModule` | `duration` (10s) | Likely an emergency invuln/burst when low HP |
| `PrimaryWeaponModModule` | `dpsMultiplier` (1.1) | Passive: +N% primary DPS (already supported by current model) |
| `MiningDrillModule` | `drillHandling`, `oreYield` | **Non-combat** (mining) |
| `CabinModule` | `cabinSize` | **Non-combat** (passengers) |
| `CompressorModule` | `cargoMultiplier` | **Non-combat** (cargo) |
| `TractorBeamModule` | `timeToLock` | **Non-combat** (loot pickup) |
| `SignatureModule` | `manufacturer` | Identity / disguise — could affect criminal recognition; not direct combat |
| `SpectralFilterModule` | `showInfo`, `showOnRadar` | Scanning support |
| `JumpDriveModule` | (none) | **Non-combat** (FTL) |
| `TimeExtenderModule` | unknown | (Phoenix-like, not sampled — likely "more time on a job") |

### Key gaps & how we'll close them

| Gap | Resolution path |
|---|---|
| No fire-rate per weapon | New `combat_balance.py` with per-subtype defaults (e.g. `auto_cannons: 8 shots/sec, scatter_guns: 1 shot/sec`). `damage_per_shot = dps / fire_rate`. Per-weapon overrides via `extra_atts.fire_rate` if/when we backfill. |
| No accuracy modifier per weapon | Per-subtype defaults (`railguns: 0.95, scatter_guns: 0.55`). Per-weapon overrides as above. |
| No hull/armour/shield separation | Ship's `armour` column → renamed conceptually to **hull** in the combat model. `ArmourModule.armour` adds to a separate **armour buffer** layer. `ShieldModule.shield` is the **shield** layer (regens). |
| Secondary damage / loading speed mostly 0 | Per-subtype defaults table (e.g. `nukes: damage=300 cooldown=20s ammo=1`, `missiles: damage=40 cooldown=3s ammo=8 homing=true`). Wiki backfill as a separate task. |
| No base ship accuracy | Derive from a constant base (e.g. 0.75) modified by Scanner / Booster / Cloak. |
| No damage types / resistances | Postpone. Subtype hints (EMP/ionizing) can be ignored in v1 or treated as flat damage. |

### Proposed default lookup tables (initial values — to be tuned)

These live in a new `services/combat_balance.py` module so they can be tuned without touching combat logic.

```python
# Fire rate (shots per second) and accuracy (0..1) by primary subtype
PRIMARY_DEFAULTS = {
    "auto_cannons":           {"fire_rate": 6.0,  "accuracy": 0.85},
    "blasters":               {"fire_rate": 4.0,  "accuracy": 0.80},
    "blaster_lasers":         {"fire_rate": 3.0,  "accuracy": 0.90},
    "beam_lasers":            {"fire_rate": 10.0, "accuracy": 0.95},  # near-continuous
    "emp_blasters":           {"fire_rate": 2.0,  "accuracy": 0.85},  # disable shields
    "scatter_guns":           {"fire_rate": 1.5,  "accuracy": 0.55},  # spread, lower acc
    "thermal_fusion_cannons": {"fire_rate": 2.5,  "accuracy": 0.85},
}

TURRET_DEFAULTS = {
    "auto":   {"fire_rate": 4.0, "accuracy": 0.70, "autonomous": True},
    "manual": {"fire_rate": 2.0, "accuracy": 0.90, "autonomous": False},
}

# Secondary weapon defaults — damage, cooldown (s), ammo, homing flag
SECONDARY_DEFAULTS = {
    "rockets":           {"damage":  40, "cooldown": 2.0, "ammo":  8, "homing": False},
    "missiles":          {"damage":  35, "cooldown": 3.0, "ammo":  8, "homing": True,  "lock_time": 1.5},
    "cluster_missiles":  {"damage":  60, "cooldown": 4.0, "ammo":  4, "homing": True,  "lock_time": 2.0, "burst": 3},
    "ionizing_missiles": {"damage":  25, "cooldown": 3.0, "ammo":  6, "homing": True,  "lock_time": 1.5, "ion": True},
    "emp_bombs":         {"damage":  10, "cooldown": 5.0, "ammo":  3, "homing": False, "shield_disable_s": 3.0},
    "mines":             {"damage": 100, "cooldown": 0.5, "ammo":  5, "delay": 1.0, "trigger": "proximity"},
    "nukes":             {"damage": 300, "cooldown":20.0, "ammo":  1, "homing": False},
    "sentry_guns":       {"damage":   8, "cooldown": 0.4, "ammo":  3, "duration": 15.0, "deploy": True},
    "misc":              {"damage":   0, "cooldown": 1.0, "ammo":  1},
}

# Base ship accuracy (before Scanner buff)
BASE_ACCURACY = 0.75
# Base evasion derivation: evasion = (ship.handling * thruster_mult) / EVASION_DIVISOR
EVASION_DIVISOR = 300  # handling 120 → evasion 0.40 ; max handling ~160 * 2.0 mult / 300 = 1.07 → clamp 0.95
EVASION_CLAMP = (0.0, 0.95)

# Tick rate & fight cap
TICK_HZ = 10               # 10 ticks/sec = 100ms granularity
MAX_FIGHT_TICKS = 1200     # 120 seconds hard cap
```

### Proposed simulator architecture (sketch)

Two new files. `combat_service.py` remains the public entry point; internally it now dispatches to a new tick resolver while keeping `SimpleTTKResolver` as a legacy fallback.

```
services/
├── combat_models.py            ← extended with Combatant, CombatEvent, expanded FightResults
├── combat_balance.py           ← NEW: subtype default tables, tuning constants
├── combat_service.py           ← public API unchanged; default resolver swapped
└── combat/                     ← NEW package
    ├── __init__.py
    ├── combatant.py            ← Combatant runtime state (HP layers, modules, weapons)
    ├── tick_resolver.py        ← TickResolver — fixed-step simulation loop
    ├── weapon_systems.py       ← WeaponSystem (cooldown, hit roll, damage)
    ├── module_systems.py       ← Per-module-type runtime behaviours (cloak, scanner, repair, etc.)
    └── event_log.py            ← Structured CombatEvent collector + embed renderer
```

### Damage layering

```
Incoming hit
   ↓
[Cloak active?] — yes → miss
   ↓ no
[Hit roll]  hit_chance = clamp((attacker.accuracy + scanner_buff + weapon.acc_mod) − defender.evasion, 0.05, 0.99)
   ↓ hit
[GammaShield reduction] — apply flat damage multiplier (1 − effect)
   ↓
[Shield layer] — absorb up to current_shield; remainder spills
   ↓
[Armour layer] — absorb up to current_armour; remainder spills
   ↓
[Hull layer] — apply remainder; if hull ≤ 0, ship is destroyed
```

Per-tick passives:
- Shield regen (`+X / sec` while not hit in last `N` ticks)
- RepairBot HPps applied to hull
- Booster/Scanner/Cloak duration countdown
- Weapon cooldowns advance; eligible weapons fire (one hit roll per shot)
- Auto-turrets fire independently
- Secondary weapons: lock-on tick for homing; cooldown for dumb-fire

### Open design questions raised by the data

1. **Hull definition**: use `ship.armour` directly as hull HP? (Existing data ranges 95–2100 work but criminals have widely varying values.)
2. **Shield regen rate**: not in data. Constant (e.g. 5% max shield/sec, paused for 3s after a hit)? Or scale with shield-module TL?
3. **Boost modules** (`effect` 1.6–4.0): these are speed multipliers in GoF2 lore. In combat — accuracy boost? evasion boost? both?
4. **CloakModule** with `duration: 20s`: is there a cooldown / charges? Or one-shot per fight?
5. **EmergencySystemModule** `duration: 10s`: emergency invuln when hull < 25%? One-shot per fight?
6. **ShieldInjectorModule** (Phoenix SIS): how much shield does it restore? Currently only `plasmaConsumption: 30` is given. Lore says it fully refills.
7. **GammaShieldModule** `effect`: is it absolute reduction (-effect) or multiplier (×(1−effect))? Two TL-8 modules have `effect=0.4` and `effect=0.6` — confusing if it's "reduction" because 0.6 reduction > 0.4.
8. **Backfill secondary weapon data from wiki?** Worth doing as a separate task before tuning balance.
9. **Per-fight ammo for secondaries**: full magazine each fight, or limited per-day? (Currently no ammo tracking in DB.)
10. **Auto-turret targeting**: same target as primary, or any? Independent fire timing → multiplies damage output.
11. **Manual turrets**: do they need pilot input (= acc penalty when also firing primary), or just be "second primary"?
12. **`PrimaryWeaponModModule` stacking** (Nirai Overdrive/Overcharge): multiplicative? Cap at +20% (2 modules)?
13. **Combat duration cap behaviour at MAX_FIGHT_TICKS**: stalemate? Winner = higher HP%?

### Backwards-compat surface to preserve

`FightResults` must still expose:
- `winner_name`, `loser_name`, `is_stalemate`, `variance_percent`
- `ship1_stats / ship2_stats` with `ship_name, raw_hp, raw_dps, varied_hp, varied_dps, ttk`

Strategy: populate `raw_hp = hull+armour+shield`, `raw_dps = analytic_dps_estimate`, `varied_hp = final_remaining_hp`, `varied_dps = actual_dps_dealt`, `ttk = ticks_elapsed/TICK_HZ`. Plus add new fields: `combat_log: list[CombatEvent]`, `combatants: list[CombatantSnapshot]`.

### Next steps (proposed)

1. Answer the open design questions (especially 1, 3, 4, 7, 8, 12).
2. Decide whether to backfill secondary-weapon data from the wiki now or ship v1 with the subtype defaults.
3. Implement `combat_balance.py` first (pure data, no logic) so we can iterate on tuning.
4. Sketch `Combatant` and `TickResolver` skeletons; write a few representative unit tests *before* fleshing out the body.
5. Ship the tick resolver behind a feature flag with `SimpleTTKResolver` as fallback for at least one release.

---

## Entry 2 — Wiki Scrape & DB Drift Audit (2026-05-24)

### What got built

Developer agent produced a proper repeatable scraper at `/proj/utils/wiki_scraper/scrape_gof2.py` (1169 LOC) + `README.md`. Scrapes `https://galaxyonfire.wiki.gg/wiki/`, GoF2-family only (excludes GoF 3D, Alliances, GoF 3). User-Agent identified; 500–1000ms pacing.

### Coverage

| Category | Total | Fully Scraped | Partial (page found, no infobox) | Failed |
|---|---|---|---|---|
| Primary weapons | 40 | 40 | 0 | 0 |
| Secondary weapons | 30 | 30 | 0 | 0 |
| Turret weapons | 10 | 10 | 0 | 0 |
| Modules | 66 | 62 | 4 | 0 |
| Ships | 65 | 60 | 5 | 0 |
| **TOTAL** | **211** | **202** | **9** | **0** |

**Partial items (wiki-side data hole, not scraper failure):**
- 4 Signature modules → single shared `/wiki/Signature` page; no per-variant infobox
- 4 Freighters → single shared `/wiki/Freighter` page
- Vossk Battlecruiser → page exists but has no infobox

### Output artefacts (all in `/tmp/` — not committed to repo)

- `/tmp/gof2_wiki_raw/{primary,secondary,turret,module,ship}/*.json` — 211 per-item files
- `/tmp/gof2_wiki_combined.json` (350 KB) — consolidated rollup
- `/tmp/gof2_wiki_diff.md` — DB-vs-wiki discrepancy report
- `/tmp/gof2_wiki_scraper.log` — full scrape log

### Data shape per item

Each item file contains:
- `raw_infobox`: verbatim key/value pairs from the wiki infobox (**authoritative source**)
- Normalized typed fields: `tech_level`, `damage`, `dps`, `loading_speed_ms`, `range_m`, `projectile_speed_kmh`, `effect_pct`, `effect_multiplier`, `duration_ms`, `magnitude` (for nukes/mines), `armour`, `cargo`, `max_primaries/secondaries/turrets/modules`, `value`, `handling`, etc.
- `description` — in-game flavour text
- `notes` / `characteristics` / `function` — mechanics-heavy prose sections
- `_wiki_categories` — useful sanity check for TL labels
- `known_price_range` — `{raw, min_credits, max_credits}` parsed from the price-range string

### KNOWN SCRAPER NORMALIZATION BUGS (raw_infobox is fine — bugs are in the normalized fields)

These need a second pass before merging into seed JSON. The raw infobox data is correct, so no re-scrape needed.

1. **Module items get spurious `range_m` and `projectile_speed_kmh`** populated from price strings and loading speeds. Example: `Linear Boost` ended up with `range_m: 4726` (that's actually the min credit price) and `projectile_speed_kmh: 8000` (that's actually the loading speed in ms). Fix: gate weapon-only normalization on `_category in ("primary","secondary","turret")`.
2. **Cloak `Effect: 10000ms` parsed as `effect_multiplier: 10000.0`** — wrong type and wrong field. For Cloak modules `Effect` is a duration. Fix: when `item_type == "Cloak"`, treat `Effect: Nms` as `duration_ms`.
3. **GoF2 HD-specific price-range rows are captured into raw_infobox but ignored** by the canonical normalizer. Example: `U'tool` has both `"Price Range"` (GoF2) and `"GoF2 HD (Android) Price Range"` rows. Fix: capture both into `known_price_range.gof2` and `known_price_range.gof2hd`.

### Key DB-vs-wiki drift (top hits — full report in `/tmp/gof2_wiki_diff.md`)

**Errors (significant balance drift):**

| Item | Field | DB | Wiki | Notes |
|---|---|---|---|---|
| Fireworks | value | 0 | 21000 | DB has $0 — clearly a seed bug |
| Vol Noor | armour | 165 | 380 | DB ≪ wiki |
| Vol Noor | cargo | 75 | 5 | DB ≫ wiki |
| H'Soc | armour | 210 | 360 | DB ≪ wiki |
| H'Soc | cargo | 45 | 10 | DB ≫ wiki |
| Gryphon | armour | 220 | 310 | DB ≪ wiki |
| Gryphon | cargo | 90 | 40 | DB ≫ wiki |
| Wraith | armour | 180 | 210 | DB ≪ wiki |
| Wraith | cargo | 65 | 25 | DB ≫ wiki |
| Phantom | armour | 200 | 220 | DB ≪ wiki |
| Phantom | cargo | 52 | 15 | DB ≫ wiki |
| Terran Battlecruiser | armour | 1800 | 7000 | DB ≪ wiki (likely GoF2 original vs GoF2 HD) |
| Terran Battlecruiser | cargo | 300 | 10 | DB ≫ wiki |

**Pattern:** every ship-armour drift shows DB-lower; every ship-cargo drift shows DB-higher. Suggests the original seed pulled from a different GoF2 build (possibly the pre-HD original where ships had less HP but more cargo, or possibly the legacy BountyBot codebase manually tuned them).

**Warnings:**
- `128MJ Railgun`: DB tech_level=5, wiki=6 (wiki *category* also says TL5 — infobox vs categories disagree; infobox is more trustworthy)
- `H'Belam`: same pattern, DB=5, wiki=6
- `Micro Gun MK I` DPS 9.09 vs 9.9 (likely rounding)
- `Tyrfing Blaster` DPS 59.09 vs 59.9 (likely rounding)

**Info-level drift (rounding only):** ~40 DPS fields match to 2 decimal places.

### Mechanics clarifications captured from prose

From scraped `description` / `function` / `characteristics` / `notes` text — these are direct quotes from the wiki that resolve the open design questions from Entry 1:

- **Linear Boost** (and by extension all Boosters): `"Keith normally flies his ships around 450 km/h... With the Linear Boost installed, he can increase its top speed to 720 km/h for three seconds, but must then rest for eight before using it again."` → Effect = **speed multiplier** (not accuracy). Cooldown = `Loading speed` (8000ms here). Duration = `Boost duration` (3000ms).
- **U'tool (Cloak)**: `"For one energy cell, a ship can turn invisible in two seconds and remain so for ten seconds. While invisible, no other pilots are able to track your ship, therefore stopping all fire towards you."` → Cloak takes 2s to activate (`Loading speed: 2000ms`), lasts 10s (`Effect: 10000ms`), consumes 1 energy cell. **Stops ALL incoming fire while active.** Repeatable with energy cells, not one-shot.
- **Liberator (Nuke)**: `"This deadly missile can be remotely controlled after being fired... The missile can be detonated by pressing the secondary fire button on the right side of the screen at any time, after twenty seconds of continuous flight, or upon contact with any ship."` → Steerable nuke; 13.8 km range, 12.5 km blast magnitude.
- **128MJ Railgun**: notes confirm `"It will not randomly explode, as there is no such mechanic in Galaxy on Fire 2"` — useful explicit confirmation that we are looking at GoF2 specifically.

### Next steps

1. **Fix the three normalization bugs in `scrape_gof2.py`** and re-run normalization (no re-fetch needed — raw_infobox is correct). Estimated 30–60 LOC.
2. **Capture the rich data we have not yet looked at** — modules like shields/repair bots/thrusters/scanners — by reading the `/tmp/gof2_wiki_raw/module/*.json` files and surfacing the mechanics-text quotes for the design decisions.
3. **Decide on per-ship armour/cargo drift** — pick a side (DB or wiki) per ship, or take wiki as source-of-truth and update seed JSON. Document the rationale.
4. **Merge into seed JSON**: design a clean merge script that:
   - Preserves all existing seed JSON fields not overridden by wiki
   - Adds the new combat fields (fire rate, damage per shot, range, projectile speed, etc.) under a new key like `combat_stats` or as top-level fields
   - Writes back to `/proj/services/bot-core/import_data/<category>/*.json` only after dry-run validation
5. **Note for data model:** the `WeaponModModule` is mutually-exclusive (only one equip-able at a time), per user direction. To be enforced in `MODULE_EQUIP_LIMITS` or new "unique-equip" list in `game_constants.py` alongside Cloak / Booster / EmergencySystem / ShieldInjector / Khador.

---

## Entry 3 — Wiki Scrape v2 (AI-driven) + Phase-1 Combat Design Decisions (2026-05-24)

### Wiki scrape v2 — final state

User raised reliability concerns about Python+BeautifulSoup parsing. Dispatched developer agent with strict instructions to do **AI-driven semantic extraction** (one page at a time, agent reads, agent writes JSON — no programmatic parsing). Output at `/tmp/gof2_wiki_v2/`.

**The v1 Python+BeautifulSoup scraper (`utils/wiki_scraper/scrape_gof2.py` from Entry 2) was deleted in this commit.** It was useful scaffolding that produced the initial DB-vs-wiki diff report, but it had three structural normalization bugs and was superseded by v2 AI extraction. Going forward `/tmp/gof2_wiki_v2/` is the single source of truth for wiki data.

| Category | Captured | Notes |
|---|---|---|
| Primary weapons | 40/40 | All complete with infobox stats |
| Secondary weapons | 30/30 | All complete |
| Turret weapons | 10/10 | All complete |
| Modules | 66/66 | All complete |
| Ships | 65/65 | Vossk Battlecruiser has empty infobox per wiki note: *"exact stats and drops are currently unknown for the GoF2 era"* |
| **TOTAL** | **211/211** | 0 failures, 0 discovered non-catalog items |

**Files:** `/tmp/gof2_wiki_v2/{category}/{slug}.json` (per item) + `_combined.json` + `_summary.md` + `_progress.json`

### Mechanics clarifications surfaced by v2 (not present in v1)

1. **Cloaks: `Effect: Xms` is DURATION, not multiplier.** v1 parser bug. Correct: U'tool 10s, Sight Suppressor II 20s, Shadow Ninja 40s. Each activation costs 1 energy cell, takes 2s to spool (loading speed), then invisible for full duration. **Repeatable per energy cell** (NOT one-shot).
2. **Emergency System: same fix.** 10-second emergency shield, consumable (destroyed after use). Cannot use Khador Drive while active.
3. **Rhoda Vortex (TimeExtender): time dilation.** `Effect: 15000ms` = perceived duration; only 7500ms real time elapses outside the AoE. 2× time-dilation factor.
4. **Thermal Fusion projectiles are heat-seeking, but ONLY with a scanner lock.** Wiki: *"When the scanner is locked onto a target, the projectiles will aim towards it. Without scanner lock: They will fire everywhere."* This connects ScannerModule → primary-weapon accuracy in a way our previous model never captured.
5. **H'Belam TL drift confirmed.** Wiki infobox says TL 6; wiki category page lists TL 5; our DB has TL 5. Infobox is authoritative — DB is wrong (matches the same drift on 128MJ Railgun).

### GoF2 vs GoF2 HD value drift (captured in `gof2_hd_overrides`)

Only price drift detected — no stat drift between versions:
- U'tool: 2.6× more expensive on Android (GoF2 HD)
- Sight Suppressor II: 2.2× more expensive
- Phoenix SIS: 1.7× more expensive

### Combat balance directives (FROM USER — 2026-05-24)

These supersede / extend the Entry 1 design proposals where they conflict.

#### Repair Bot rates (wiki data gap)

Wiki has no `HPps` field for either Ketar Repair Bot. **Default per user direction:**

| Module | Repair rate (of max hull + armour, per second, in combat) |
|---|---|
| Ketar Repair Bot | **2.5% / sec** |
| Ketar Repair Bot II | **5.0% / sec** |

Repair applies to **hull + armour combined** (NOT shield). Tick-based: per-tick heal = `(max_hull + max_armour) × rate × tick_seconds`. Healed HP is distributed to whichever layer (hull first to keep ship alive, then armour buffer back-fill).

#### Phase-1 starting conditions

Both combatants (player + opponent, whether PvP or PvC) start each combat at **full hull + armour + shield**. No prior-damage tracking in Phase-1.

**Design hooks required for future "damaged opponent" mechanic (Phase-2):**
- Combatant initialisation must accept optional `current_hull / current_armour / current_shield` overrides; default to max.
- `CombatStats` and `Combatant` dataclasses to expose both `max_*` and `current_*` per layer.
- `BountyService` / `DuelService` to pass current values from a future persistent-damage column on `Player` / `Criminal` / `Bounty`.

#### Out-of-combat recovery (NEW SYSTEM — Phase-2-ready)

Slow regen ticking outside combat. Applies to **hull + armour + shield**, recovers up to respective max each.

| Subject | Default recovery rate (per hour) | Override |
|---|---|---|
| Players | **25% / hour** of max each layer | `BOUNTYBOT_PLAYER_OOC_RECOVERY_PCT_PER_HR` env + per-guild `player_ooc_recovery_pct_per_hr` |
| Criminals | **12.5% / hour** of max each layer | `BOUNTYBOT_CRIMINAL_OOC_RECOVERY_PCT_PER_HR` env + per-guild `criminal_ooc_recovery_pct_per_hr` |

Rule of thumb: players recover ~2× faster than criminals.

**Dock mechanic (NEW):** A `/dock` (or equivalent) command instantly restores player to full hull + armour + shield for **2.5% of current credit balance**. Per-guild configurable: `dock_repair_cost_pct` (default `0.025`). Must clamp to a minimum (say 1 credit) and round up to nearest int.

Both the OOC recovery and dock mechanic are Phase-2 features but the **damage-tracking columns** they require should be added in the Phase-1 migration so we don't churn the schema later.

#### NPC ship stats — use seed data, not wiki

Battlecruisers and other NPC-only ships often have made-up or missing wiki data (Vossk Battlecruiser is the canonical example — wiki says "stats currently unknown"). Per user direction: **for NPC ships, the existing seed JSON data in `import_data/ship/` is the source of truth**, not the wiki.

**Practical implication for the data merge phase:**
- The DB-vs-wiki ship drift report (Vol Noor, H'Soc, Gryphon, etc. — see Entry 2) is **NOT** automatically actionable in favour of the wiki. For NPC-only ships, keep the existing seed values.
- For player-purchasable ships (those that appear in shops or are obtainable in the player progression path), prefer wiki values where they differ — these are the canonical GoF2 stats.
- We need to classify each ship as **player-purchasable** vs **NPC-only** before the merge. The `shop_spawn_rate` field on Ship is a usable signal (if `> 0`, it can appear in shops → player-purchasable). The `criminal` JSON references in `import_data/criminal/` cross-reference which ships are NPC-only.

### Updated open design questions for the tick simulator

(Resolved/updated since Entry 1; remaining ones still need decisions before code.)

| # | Question | Status |
|---|---|---|
| 1 | Hull = `ship.armour`? | **Decided — yes** (Entry 1 proposal stands). Hull = ship's intrinsic armour column. ArmourModule values add to a separate `armour_buffer` layer above hull. Shield is its own layer. |
| 2 | Shield regen rate | **Open** — wiki only gives `recharge_speed_ms` for one shield (Targe = 20000ms full-refill). Need defaults. Proposed: full-refill time = `recharge_speed_ms` if present, else `15000ms` default. Recharge pauses for 3s after taking shield damage. |
| 3 | What do Boosters boost? | **Decided** — wiki says **speed multiplier**. Per user's tick-design (accuracy = primary variance), translate booster effect → temporary **evasion bonus** (higher speed = harder to hit). `effect_multiplier=1.6` → `evasion += 0.20` for `duration_ms`, with `loading_speed_ms` cooldown. |
| 4 | Cloak charges per fight | **Decided (refined)** — repeatable per energy cell. Energy cells are a resource; for combat purposes assume a starting pool (default: 3 cells per fight, guild-configurable). |
| 5 | EmergencySystem trigger | **Decided** — auto-trigger at hull ≤ 25% (since wiki says "critical level"); 10s full invuln; module destroyed after use. Once per fight. |
| 6 | GammaShield damage type | **Decided** — gamma/radiation damage only; **does NOT mitigate projectile/laser/EMP damage**. In Phase-1, since no item deals "radiation damage", GammaShield is **effectively inert in combat** and should NOT contribute to combat stats. Document this clearly so it isn't surprising. |
| 7 | Backfill secondary data | **Done** — wiki has full secondary damage/loading-speed/magnitude data. |
| 8 | PrimaryWeaponModModule stacking | **Decided per user (NEW Entry 3)** — `WeaponModModule` is **mutually-exclusive unique-equip**. Only one allowed. To be added to a new `UNIQUE_EQUIP_TYPES` list in `game_constants.py` alongside Cloak / Booster / EmergencySystem / ShieldInjector / Khador / Phoenix SIS. |
| 9 | Fight cap behaviour | **Open** — recommend winner-by-remaining-HP% if both still alive at MAX_FIGHT_TICKS (with ≤ 5% HP delta = stalemate). |
| 10 | Repair Bot rate | **Decided per user (Entry 3)** — 2.5%/sec (I), 5.0%/sec (II) of `max_hull + max_armour`. |
| 11 | Starting HP | **Decided per user (Entry 3)** — full hull + armour + shield for both combatants in Phase-1. Hooks required for Phase-2 damaged-opponent mechanic. |
| 12 | Out-of-combat recovery | **Decided per user (Entry 3)** — 25%/hr (players) / 12.5%/hr (criminals) / `dock` instant-repair = 2.5% of current credits. Schema columns added in Phase-1 migration even though feature ships in Phase-2. |
| 13 | NPC ship stats source | **Decided per user (Entry 3)** — seed JSON wins for NPC-only ships; wiki wins for player-purchasable ships. |

### Updated next-steps queue

1. ~~**Fix the v1 scraper normalization bugs**~~ — **OBSOLETE**. v1 deleted; v2 AI extraction is the canonical source.
2. ~~**Classify each ship as player-purchasable vs NPC-only**~~ — **RESOLVED 2026-05-24 per user**: there are NO NPC-only ships. All 65 ships remain player-purchasable. Criminal ship selection is already correctly gated by `Ship.max_primaries > 0` (`bounty_service.py:395`), which auto-excludes: Vossk Battlecruiser (max_primaries=0), all 4 freighters, Rhino, Cormorant. **Note**: Terran Battlecruiser has `max_primaries=2` and IS therefore eligible for criminal selection — user accepted "whatever is current should remain in that aspect", so this is not changed.
3. **Seed-JSON merge policy** (next): wiki stats are canonical for all ships/weapons/modules where wiki has data; seed JSON wins where wiki has none (Vossk Battlecruiser is the only ship in that category). Naming convention TBD — likely a `combat` sub-object to keep new fields visually separate from legacy fields.
4. **Phase-1 DB migration**: add damage-tracking columns to `Player` (current_hull, current_armour, current_shield, last_damage_at) and `Bounty` (criminal_current_hull, criminal_current_armour, criminal_current_shield) — Phase-2 features but ship in the Phase-1 schema to avoid churn.
5. **`combat_balance.py`**: per-subtype defaults for missing data — fire rate, accuracy, secondary cooldowns — so Phase-1 has tunable values without touching the simulator.
6. **Tick resolver** behind a feature flag, with the legacy `SimpleTTKResolver` as fallback for one release.

---

*Last updated: 2026-05-24*

---

## Entry 4 — Compaction Handoff (2026-05-24)

> **READ FIRST ON RESUME.** Everything you need to pick up sits here; older entries are reference material.

### Git state
- Branch: `dev` (5 commits ahead of `origin/dev`, **not pushed**)
- Recent commits:
  - `3fee038` — ship classification verdict (no NPC-only ships)
  - `9882200` — deleted v1 wiki scraper
  - `cdfca0c` — journal Entry 3 (wiki v2 results + Phase-1 design)
  - `81ac884` — wiki scraper + journal Entry 2
  - `138a095` — merged ADMIN.md audit fixes
- Working tree: clean.

### Where to resume
**Next action**: Step #3 — design the seed-JSON merge structure (wiki → `import_data/`). No code written yet; this is still a design phase.

Proposal to put to user:
- Add a `combat` sub-object inside each seed JSON to keep new fields visually separate from legacy (e.g. `"combat": {"fire_rate_hz": 2.5, "accuracy": 0.85, ...}`).
- New columns on existing models OR a single JSON column? (open question for the user)
- Migration strategy: backfill from wiki v2 JSONs at seed time vs at migration time.

### Authoritative artifacts (survive compaction)
| Artifact | Location | Notes |
|----------|----------|-------|
| This journal | `/proj/COMBAT_REWRITE_JOURNAL.md` | 669+ lines; Entry 3 = full design; Entry 4 = this handoff. |
| Wiki v2 data | `/tmp/gof2_wiki_v2/` | 211 item JSONs across `primary/ secondary/ turret/ module/ ship/` + `_combined.json` + `_summary.md`. **/tmp** — copy to `/proj` if persistence needed. |
| Combat code (current) | `/proj/services/bot-core/src/services/combat_service.py` (527 LOC) | The file being replaced. |
| Combat models | `services/bot-core/src/services/combat_models.py` | `FightResults`, `CombatResolver` Protocol — public contract to preserve. |

### Locked decisions (full list)
1. **Tick-based combat**; accuracy = primary RNG axis; weapon fire rates matter.
2. **Mechanics in scope (Phase-1)**: shields, hull, armour, repair bot, thrusters, cloaks, boosters, auto-fire turrets, scanners, secondary weapons (dumb-fire vs heat-seeking).
3. **Repair Bot**: 2.5%/sec (I), 5.0%/sec (II) of `max_hull + max_armour`.
4. **Starting HP (Phase-1)**: both combatants full hull/armour/shield. Hooks (`current_*` cols) shipped in Phase-1 schema for Phase-2.
5. **OOC recovery (Phase-2 feature, Phase-1 schema)**: 25%/hr players, 12.5%/hr criminals; guild-configurable.
6. **Dock mechanic (Phase-2)**: full repair for 2.5% of current credits; guild-configurable.
7. **No NPC-only ships**; existing `max_primaries > 0` filter is the gate. Terran Battlecruiser eligible — left as-is.
8. **WeaponModModule** is unique-equip / mutually-exclusive → add to `UNIQUE_EQUIP_TYPES` alongside Cloak / Booster / EmergencySystem / ShieldInjector / Khador / Phoenix SIS.
9. **Scope**: GoF2 family only (GoF2, GoF2 HD, Valkyrie, Supernova). Exclude GoF 3D / Alliances / GoF3.
10. **Source-of-truth**: wiki v2 canonical for every item with data; seed JSON wins only for Vossk Battlecruiser.
11. **v1 wiki scraper deleted**; v2 AI semantic extraction is the only scrape going forward.
12. **GammaShield** inert in Phase-1 (no radiation damage source in scope).
13. **Hull** = `ship.armour` column. ArmourModule = separate armour buffer. ShieldModule = regenerating shield layer.
14. **User wants pointers, not guesses** — ask before assuming code locations.

### Open questions to put to the user when resuming
- Seed-JSON merge: `combat` sub-object vs flat? New columns vs JSON blob?
- Shield regen default (wiki only documents Targe's 20s full-refill).
- Fight cap behaviour: winner-by-remaining-HP% vs stalemate at `MAX_FIGHT_TICKS` (recommendation: HP%-winner with ≤5% delta = stalemate).
- Booster effect mapping (lore = speed mult; my derivation = temp evasion bonus) — confirm.
- Cloak repeatable-per-energy-cell, default 3 cells per fight — confirm.

### Public contract to preserve from `CombatService`
```
fight_ships(loadout1, loadout2, variance_percent=None,
            player_armour_buff=1.0, guild_config=None) -> FightResults
# + statics: collect_stats / get_dps / get_armour / get_shield
# + CombatResolver Protocol
```
`FightResults` wire fields downstream consumers depend on:
`winner_name, loser_name, is_stalemate, variance_percent,
ship{1,2}_stats(ship_name, raw_hp, raw_dps, varied_hp, varied_dps, ttk)`.

### Combat callsites (5)
- `services/bot-core/src/services/duel_service.py:233`
- `services/bot-core/src/services/bounty_service.py:1318` (Bronze bonus)
- `services/bot-core/src/services/bounty_service.py:1357` (Silver/Gold/Platinum gate)
- `services/bot-core/src/api/routers/bounties.py:227` (cleanup target — fresh `CombatService` per request)
- `services/bot-core/src/services/combat_preflight_service.py:173` (Monte-Carlo estimator)

### Files to touch when work resumes
- `services/bot-core/src/services/game_constants.py` (constants: `UNIQUE_EQUIP_TYPES`, OOC recovery rates, dock cost)
- `services/bot-core/src/services/loadout_consistency_service.py` (enforce `UNIQUE_EQUIP_TYPES`)
- `services/bot-core/src/services/loadout_builder.py` (consume new combat fields)
- `services/bot-core/src/persist/models/{player,bounty}.py` (Phase-1 schema columns)
- `services/bot-core/src/persist/database/revisions/versions/` (new Alembic migration)
- `services/bot-core/import_data/{primary_weapon,secondary_weapon,turret_weapon,module,ship}/*.json` (merge destination)
- `services/bot-core/src/services/combat_balance.py` (NEW — per-subtype defaults)
- `services/bot-core/src/services/combat_service.py` (REPLACE — keep `SimpleTTKResolver` as flag fallback)

### Wiki v2 known gaps (not blocking)
- Vossk Battlecruiser: no stats anywhere (wiki says "stats unknown").
- 48 items have empty `mechanics_text` (Disruptor Laser, Berger Focus I, Mass Driver MD 12, etc.) — stats complete, only editorial flavor missing.
- DB vs wiki TL drift: `128MJ Railgun` and `H'Belam` DB=5 vs wiki infobox=6 (infobox is authoritative).
- DB vs wiki ship drift cluster (DB-lower-armour / DB-higher-cargo): Vol Noor, H'Soc, Gryphon, Wraith, Phantom, Terran Battlecruiser — seed pulled from a different GoF2 build at some point.

*Compaction handoff ends here.*

---

## Entry 5 — Post-compaction mechanic clarifications (2026-05-24)

User clarified several open questions and corrected my mis-derivations.

### Locked mechanics

- **GammaShield**: keep in `UNIQUE_EQUIP_TYPES` (faithful to game), but **inert in combat** — never has a practical purpose in this bot.
- **Boosters in combat**: act as an **enemy-accuracy debuff** over their `duration_ms` active window — same shape as cloaks, just applied to the opponent's hit-roll. NOT a speed multiplier in the sim. (My earlier "evasion" derivation was directionally right but mis-attributed; correct framing is enemy-accuracy reduction.)
- **Energy pool**: **unlimited** for both player and criminal. Do NOT track energy cells. Simplification accepted.
- **Activation limits**: cloak / booster / etc. activations are capped per combat session via **HP thresholds** (mechanism TBD — e.g., one use per HP-band crossed). Open question: exact threshold scheme.
- **Combat duration cap**: **3 simulated minutes**, hard cap. Derived from "longest cloak ≈ 1 min" so 3 min gives ~3 cloak windows worth of fight.
- **Tick value**: **dynamically computed per fight** = fire-rate (ms) of the **fastest-firing weapon across both combatants** (primary + secondary + turret). All other weapon firings discretize to integer multiples of this tick. Max ticks per fight = `180_000ms / tick_ms`.
- **Stalemate (no winner at max ticks)**:
  - **PvP duel**: declared draw, both players keep their credits, no rewards.
  - **PvE / PvC (bounty)**: declared draw, BUT the criminal **escapes**:
    - A new system is selected along the criminal's route (existing route mechanic).
    - The bounty's hunt-checks are reset (i.e. the player loses their progress on this criminal, same as a player-loss outcome).

### Implications for the simulator

- Tick length is a **fight-local constant**, not a global. Resolver computes it from the loadouts at fight start.
- Every weapon has an `interval_ticks` = round(`fire_rate_ms / tick_ms`); fires when its cooldown counter hits zero.
- Boosters/cloaks have an `active_ticks` window during which they modify the opponent's hit-roll; after the window closes they re-enter cooldown.
- HP-threshold gating for activations means the resolver tracks "bands crossed" per combatant per device-type.

### Still open (real design questions, refined)

1. ~~HP-threshold activation scheme~~ — **RESOLVED Entry 5b**. See below.
2. **Stalemate route mechanic for PvC**: need to grep `bounty_service.py` to confirm "checks reset + new system chosen along route" is already an existing flow we can re-use, or whether new code is needed.
3. **Booster accuracy-debuff magnitude**: wiki gives `effect_pct` (60%/80%/160%/300%) — clearly a speed multiplier in lore. For combat, what mapping? Linear (60% boost → 60% accuracy debuff)? Capped? Need user input.

### Entry 5b — Activation gating + accuracy system framing (2026-05-24)

**Activation gating (LOCKED)**:
- Devices trigger automatically when defender's HP crosses a threshold, **conditional on the device not being on cooldown** from a prior activation.
- **Boosters** trigger at 80%, 60%, 40%, 20% HP (up to 4 activations per fight if cooldown permits).
- **Cloaks** trigger at 30% HP (1 activation, if cooldown permits).
- Cooldown = wiki's `loading_speed_ms` per device.

**Accuracy system (FRAMING — design open)**:
- Every weapon has a **base accuracy** (need source / default scheme — wiki doesn't expose this directly).
- **Scanners** modify accuracy. Modules of interest: `telta_ecoscan`, `telta_quickscan`, `hiroto_proscan`, `hiroto_ultrascan`. Need to read wiki specs.
- **Secondary weapons** have their own accuracy, derived from:
  - Equipped scanner (better scanner → better secondary accuracy)
  - Secondary type:
    - **Rocket** (dumb-fire) — lowest baseline accuracy, no tracking
    - **Missile** (tracking) — higher accuracy via target lock
    - **Nuke** (AoE) — accuracy is irrelevant for the primary hit, but damage falls off with distance
- **Nuke damage falloff**: use an **inverse-square / diffusion-style formula** (same shape as light intensity at distance: `I = I₀ / d²`).
  - Implication: we need a notion of "distance" in our abstract tick-sim. Likely a per-shot RNG-derived "miss distance" from the target, then `damage = base_damage / max(1, miss_distance²)`.
  - Need to define: what determines `miss_distance`? (Scanner level? Defender speed/agility? Fixed RNG band?)

### New open questions

- ~~Base accuracy source~~ — **DEFERRED per user**. Will revisit later.
- ~~Scanner → accuracy mapping~~ — **DEFERRED per user**.
- ~~Weapon-type accuracy modifiers~~ — **DEFERRED per user; may be skipped entirely for simplicity**.
- **Secondary classification source**: wiki already gives `weapon_subtype` for every secondary — see Entry 5c.
- **Nuke miss-distance model**: what drives `miss_distance` for the inverse-square damage falloff?
- **Booster accuracy-debuff magnitude** still open (carries from Entry 5).

### Entry 5c — Wiki data inventory for accuracy / secondary system (2026-05-24)

Confirmed by reading wiki JSONs directly (not asking user):

**Scanner data available** (`module/telta_*`, `module/hiroto_*`):
- All 4 scanners expose `time_to_lock_s`: Telta Quickscan=4.0, Telta Ecoscan=3.0, Hiroto ProScan=1.8, Hiroto UltraScan=1.8.
- No direct "accuracy bonus" stat — accuracy effect must be derived from `time_to_lock_s` (lower = better lock).

**Secondary subtypes available** (`secondary/*` — wiki `weapon_subtype` field):
- `rocket` — `steerable: false`, has `range_m` + `projectile_speed_kmh`. Dumb-fire.
- `missile` — `steerable: true`, has `range_m` + `projectile_speed_kmh`. Tracking.
- `nuke` — has `magnitude_m` (AoE radius — Fireworks=10000m!), `damage` = direct-hit base.
- `mine` — has `magnitude_m` (trigger radius) + `duration_s` (lifetime). Static deployment.
- `sentry-gun` — has `dps` + `range_m`. Deployed auto-fire turret (Supernova DLC).

**Implication**: user mentioned only rocket/missile/nuke. Mines and sentry-guns also exist as "secondaries" in the catalog. **Need user direction on whether Phase-1 supports mines and sentry-guns or skips them.** Likely skip for v1 (they have unique deployment semantics — mines wait for proximity, sentries deploy and persist), but flag.

**Primary weapon data** — confirmed has `damage_per_shot`, `loading_speed_ms` (= fire rate), `dps`, `range_m`, `projectile_speed_kmh`, `weapon_subtype` (auto-cannon / laser / plasma / etc.). **No accuracy field** in wiki — must be derived or assigned by us.

**Key insight for nuke inverse-square formula**:
- `magnitude_m` (AoE radius) is already in the data — this is the falloff distance scale.
- Formula candidate: `damage = base_damage * (1 - min(1, miss_distance / magnitude_m))²` (inverse-square inside the AoE, zero outside).
- OR true inverse-square: `damage = base_damage * (magnitude_m / max(magnitude_m, magnitude_m + miss_distance))²` — needs design discussion later.

### Entry 5d — Distance mechanic + Phase-1 scope tightening (2026-05-24)

**LOCKED**:
- **Mines and Sentry-guns**: deferred to Phase-2. Phase-1 secondary types = rocket / missile / nuke only.
- **Distance between combatants is a live state variable** in the tick sim, derived from primary weapon ranges.
- **Range-gating**: a weapon firing event is only computed if `distance <= weapon.range_m`. Out-of-range = no shot, no accuracy roll, no damage.

**Design implications now in scope**:
- The sim needs a `current_distance_m` state value between the two combatants, updated each tick.
- Primary weapon `range_m` from wiki (typically 1800–3000m) is the close-combat band.
- Missile `range_m` is much longer (10000–11800m for wiki samples), so missiles can fire while primaries cannot — this is real, intended asymmetry.
- Nukes have shorter `range_m` (Fireworks=6600m) but enormous `magnitude_m` AoE (10000m) — even a partial hit catches the opponent.

**OPEN design questions for distance mechanic**:
1. ~~Starting distance~~ — **RESOLVED Entry 5e**: 5000m, configurable.
2. ~~What changes distance over time~~ — **RESOLVED Entry 5e**: passive closure at 300 m/s, boosters push back out, min 300m floor.
3. ~~Distance floor~~ — **RESOLVED Entry 5e**: 300m, configurable.

### Entry 5e — Distance + damage-stacking lock-in (2026-05-24)

**Distance mechanic (LOCKED, all configurable per guild)**:
- **Starting distance**: 5000m
- **Base ship speed**: 150 m/s (pinned; same value for both combatants for simplicity)
- **Passive closure rate**: 300 m/s (= both ships approaching at 150 m/s each → 300 m/s relative)
- **Minimum distance floor**: 300m
- **Booster effect**: pushes distance back out based on booster `effect_pct` × `duration_ms`. Exact formula TBD (likely `distance_gained_m = base_speed * (effect_pct/100) * (duration_s)` — e.g. 60% booster for 3s adds ~270m).
- **Rocket accuracy**: scales inversely with distance (closer = more accurate). Exact curve TBD.
- **Range-gating**: weapons only fire when `current_distance_m <= weapon.range_m`.

**Open follow-ups on distance**:
- ~~Ship maneuverability + thruster effect mapping~~ — wiki data inventoried, see Entry 5f. Mechanic mapping still TBD.
- ~~Rocket accuracy curve~~ — **RESOLVED Entry 5f**: linear 5% at max-range → 60% at close-quarters.

**Damage-stacking order (LOCKED)**:
1. **Shield HP** absorbs first (recharges per per-shield `loading_speed_ms`)
2. **Armour module HP** second (recharged by Repair Bot)
3. **Ship hull HP** last (recharged by Repair Bot)

**Recharge implications**:
- Shield: existing wiki data per item (`shield_recharge_ms`). Phase-1 question: does shield recharge from any partial state continuously (e.g. `+capacity/recharge_ms per tick`), or only after a full break? Default proposal: **continuous** — every tick adds `capacity * (tick_ms / recharge_ms)` until full.
- **Repair Bot rates (already locked Entry 3)**: 2.5%/sec (I), 5.0%/sec (II) of `max_hull + max_armour`. **YES — values are specified.**
- ~~Repair Bot fill order~~ — **RESOLVED Entry 5f**: hull first, then armour (inverse of damage order). User noted these are practically equivalent in effect, but specified inverse for clarity.

### Entry 5f — Thrusters catalog + final formulas (2026-05-24)

**Thrusters (wiki data inventoried, 5 modules)** — `effect_pct` = handling increase:

| Module | Tech | effect_pct | handling_multiplier |
|--------|------|-----------|---------------------|
| Static Thrust | 1 | 20% | 1.2 |
| Pendular Thrust | 3 | 40% | 1.4 |
| Dozzt Thrust | 5 | 70% | 1.7 |
| MPZZZM Thrust | 7 | 100% | 2.0 |
| Pulsed Plasma Thrust | 8 | 130% | 2.3 |

Wiki notes: "Thruster effect % is handling increase. handling_multiplier = 1 + effect_pct/100."

**Mapping into combat (RESOLVED Entry 5g)**: thrusters boost the **equipping ship's outgoing primary-weapon accuracy when current_distance < 750m** (close-quarters). Above 750m, thrusters have no combat effect. Tighter turn radius = better tracking in dogfights.

**Thruster combat mechanic (LOCKED)**:
- **Shape**: linear ramp.
- **Scope**: **primaries only**. Turrets and rockets unaffected (rockets already have their own 5%→60% distance curve).
- **Bonus magnitude**: `max_bonus = effect_pct * 0.10` (10% of effect_pct).
- **Distance scaling**: linear from **0% bonus at 750m → max_bonus at min_distance (300m default)**, clamped to 0 above 750m.
- **Per-module max bonuses**:

  | Module | effect_pct | max accuracy bonus |
  |--------|-----------|---------------------|
  | Static Thrust | 20% | +2% |
  | Pendular Thrust | 40% | +4% |
  | Dozzt Thrust | 70% | +7% |
  | MPZZZM Thrust | 100% | +10% |
  | Pulsed Plasma Thrust | 130% | +13% |

- **Formula**:
  ```
  if current_distance >= 750:
      bonus = 0
  else:
      t = (750 - current_distance) / (750 - min_distance)   # 0..1
      bonus = max_bonus * t
  primary_accuracy += bonus   # clamped to [0, 1.0] at apply time
  ```

**LOCKED in this entry**:

1. **Repair Bot fill order**: hull FIRST, then armour (inverse of damage order). Practically equivalent in effect; specified inverse for clarity.
2. **Shield + Repair Bot recharge**: **continuous, applied per tick**. Per-tick contribution = `total_recharge_per_sec * (tick_ms / 1000)`. Any HP recovery that can be applied this tick is applied this tick.
3. **Rocket accuracy as f(distance)**:
   - Linear from **5%** at max range (= weapon's `range_m`) to **60%** at close-quarters (= configured min distance, default 300m).
   - Formula: `accuracy = 0.05 + (0.60 - 0.05) * ((range_m - current_distance) / (range_m - min_distance))`, clamped to `[0.05, 0.60]`.
4. **Booster distance formula**: while booster active, user moves AWAY at `base_speed * (1 + effect_pct/100)`, opponent follows at `base_speed`.
   - Net outward velocity = `base_speed * (effect_pct / 100)`
   - Total distance gained over booster duration = `base_speed * (effect_pct/100) * (duration_ms/1000)`
   - **Worked examples** (base_speed = 150 m/s):
     - Linear Boost (60%, 3s): +270m
     - Cyclotron (80%, 4.4s): +528m
     - Synchrotron (160%, 5.6s): +1344m
     - Polytron (300%, 6s): +2700m (re-opens primary range bracket → tactically meaningful)
   - During booster active window, passive 300 m/s closure is **suspended**; the booster's outward velocity dominates. After duration ends, normal closure resumes.
5. **Booster-user can still fire during boost**: accepted simplification (acknowledged as logically inconsistent with "boosting away"). Mirrors GoF2 base behaviour.

### Carries forward unchanged
- Seed-JSON merge structure (#3) is still the next implementation step.
- All Entry 4 locked decisions stand.

### Entry 5h — Built-in cloaks on Scimitar + Specter (2026-05-24)

**Wiki finding**: two ships ship with a **pre-installed U'tool cloak**:
- **Scimitar** (Supernova DLC, Nivelian faction, $5.8M)
- **Specter** (Supernova DLC, Nivelian faction, $30M)

**U'tool spec** (from `module/utool.json`):
- Type: Cloak | TL 6 | duration_ms 10000 (10s) | loading_speed_ms 2000 (2s) | energy_per_use 1

**Equip-priority rule (LOCKED per user)**:
```
effective_cloak_module = equipped_cloak if has_cloak_equipped else builtin_cloak
```
A ship with a built-in cloak CAN still equip a separate cloak module; the equipped one takes precedence in combat.

**Seed-JSON status**: the `Ship` model already has a `builtin_modules: ARRAY(String)` column, but no Scimitar / Specter JSON currently populates it. Wiki enrichment (Stage A) must add `"builtinModules": ["U'tool"]` to both ship seed JSONs.

**Generalised rule (applies to all unique-equip types)**: the equip-priority pattern should generalise — if any ship gets a built-in `<TYPE>` in future, the same rule applies for that type (equipped wins over builtin). Implementation should be `for type in UNIQUE_EQUIP_TYPES: effective[type] = equipped[type] or builtin[type]`.

**LOCKED per user**: built-in cloak is **free / off-slot** (does NOT count against `max_modules`). Scimitar/Specter still get full 15/16 equippable slots, plus the U'tool runs underneath. An equipped cloak takes priority by the equip-priority rule.

**`builtIn` attribute audit (researcher, 2026-05-24)**: confirmed DEAD.
- 270 seed JSON files: 0 set `builtIn: true`, 218 set false, 52 omit.
- Defined on `Item.built_in` (item.py:14), `Ship.built_in` (ship.py:16), `Criminal.built_in` (criminal.py:14).
- Read sites: ZERO business-logic gates. Only cosmetic passthrough in `about.py` router + `aboutCog.py` embed display ("Built-in: Yes" branch that never fires).
- Safe to ignore going forward — keep column in DB (deployed), stop populating in any new code, optionally strip the cosmetic API exposure later. Combat rewrite will NOT touch it.

### Carries forward unchanged (post-5h)
- Seed-JSON merge structure (#3) is still the next implementation step.
- All Entry 4 + 5a–5g locked decisions stand.

### Entry 5i — Pricing + stats normalisation policy (2026-05-24)

User direction following architect schema-mapping report:

**LOCKED**:
1. **In-game `value` for every item**: use **median of wiki `price_range_min_credits` and `price_range_max_credits`** = `(min + max) / 2`, rounded to nearest integer credit. Applies universally — primaries, secondaries, turrets, modules, ships. Overwrites current `value` field (the secondary-weapon `value: 0` bug is fixed by this rule, not by special-casing).
2. **Stat rounding allowed**: where it makes a combat formula cleaner (e.g. fractional DPS, awkward `loading_speed_ms` like 4287ms), round to a reasonable value. Document any rounding in the enriched JSON's notes or comments.
3. **Tech-level corrections**: fix all TL drift to match wiki infobox. Confirmed cases from architect report:
   - `128MJ Railgun`: 5 → 6
   - `H'Belam`: 5 → 6
   - Any others discovered during enrichment: also bump to wiki value.
4. **Builtin module population**: Scimitar + Specter get `"builtinModules": ["U'tool"]` populated in their seed JSONs as part of Stage A.

### Implementation plan (now unblocked)

Following architect's recommended sequencing:
- **PR-1**: Alembic migration — `ship.extra_atts` + Phase-2 damage-tracking columns on `Player` and `Bounty`.
- **PR-2**: Loader patches — fix `"loading speed"` (with space) → `loading_speed_ms` mapping; normalize subtype handling; respect `extra_atts` in seed JSONs.
- **PR-3**: Seed JSON enrichment — Stage A merge script, commits enriched JSONs. Updates `value` to wiki-median, fixes TL drift, populates `builtinModules` for Scimitar/Specter, drops wiki combat fields into `extra_atts`.
- **PR-4**: New tick resolver behind feature flag, `SimpleTTKResolver` retained as fallback for one release.

### Carries forward unchanged (post-5i)
- All Entry 4, 5a–5h locked decisions stand.
- Architect report `/proj/COMBAT_SCHEMA_MAPPING.md` is the authoritative implementation reference.

### Entry 5j — Data-gap pass: rounding + mechanics_text (2026-05-26)

Two parallel researcher tasks resolved both open data questions before PR work
begins.

**Stat-distribution analysis** (`/tmp/stat_distribution_report.md`):
- All 77 weapon `loading_speed_ms` values across primary + secondary + turret
  are clean multiples of 10ms. Range 90ms–10000ms. **Zero outliers.**
- GCD across all weapon loading speeds = **10ms**.
- GCD across all module cooldowns = 500ms.
- **LOCKED**: **no rounding needed**. Wiki values copied verbatim into enriched
  JSONs. The "stat rounding allowed" clause in Entry 5i stays as fallback
  authority but never exercises on current data.
- **LOCKED**: **base tick = 10ms**. Tick-cadence fairness problem (raised by
  user this session) is resolved by construction — every weapon fires on its
  exact cadence because every `loading_speed_ms` is an integer multiple of the
  base tick. No accumulator carry, no drift, no fairness skew.
- Implementation: each weapon holds `cooldown_remaining_ms`. Per tick:
  decrement by 10ms; if `≤ 0` AND in range AND not gated by mechanic, fire and
  reset to `loading_speed_ms`. 3-min combat cap = 18,000 ticks (in-memory math,
  trivially fast).
- Per-fight tick base stays 10ms (no longer derived from fastest weapon in
  fight) — simpler and equally correct given the clean-10ms-grid finding.

**Wiki mechanics_text re-scrape** (`/proj/.combat-rewrite-wiki-v2/_mechanics_rescrape.json`):
- Direct `?action=raw` fetch against galaxyonfire.wiki.gg for the 48 items
  whose v2 capture had empty `mechanics_text`. Kept sections in priority
  order: In-Game Description, Notes, Trivia, Strategy, Overview, Description.
  Wiki markup stripped (link/template/ref/gallery/category/file).
- Result: **48/48 FOUND, 0 EMPTY, 0 ERROR.**
- Prose lengths: median 653 chars, range 186–3058 chars.
- **LOCKED**: no residual mechanics-text gaps to surface to user.
- Two artefacts committed inside `.combat-rewrite-wiki-v2/`:
  - `_mechanics_rescrape.json` — 48 entries keyed by item name.
  - `_mechanics_rescrape_summary.md` — counts + per-category breakdown.
- Stage A enrichment script (PR-3) should merge this prose back into the
  item-level v2 records (or read it as a side-table) when producing the
  enriched seed JSONs.

**Vossk Battlecruiser sentinel** (user-directed):
- Stage A enrichment script must set `extra_atts.wiki_status = "missing"` on
  the Vossk Battlecruiser seed JSON so future audits can find it
  programmatically. Item stays inert (max_primaries=0 still excludes it from
  bounty generation).

### Implementation plan (refined post-5j)
PR sequencing unchanged; both blocking analyses are now closed.

- **PR-1**: Alembic migration — `ship.extra_atts` + Phase-2 damage-tracking
  columns on `Player` and `Bounty`.
- **PR-2**: Loader patches — fix `"loading speed"` (with space) →
  `loading_speed_ms` mapping; normalize subtype handling; respect
  `extra_atts` in seed JSONs.
- **PR-3**: Seed JSON enrichment — Stage A merge script. Wiki values copied
  verbatim (no rounding). `value` set to wiki median. TL drift fixed.
  `builtinModules: ["U'tool"]` for Scimitar + Specter. Mechanics prose
  carried into description field (or `extra_atts.mechanics_text`). Vossk
  Battlecruiser gets the missing-data sentinel.
- **PR-4**: New tick-based resolver (10ms base tick, per-weapon cooldown
  decrement) behind feature flag. `SimpleTTKResolver` retained as fallback
  for one release.

### Carries forward unchanged (post-5j)
- All Entry 4, 5a–5i locked decisions stand.
- Architect report `/proj/COMBAT_SCHEMA_MAPPING.md` is the authoritative
  implementation reference for catalog enrichment + migration.
- Wiki v2 capture at `/proj/.combat-rewrite-wiki-v2/` is the authoritative
  game-data source.

### Entry 5k — Secondary-weapon `damage=0` attribution correction (2026-05-26)

User flagged the JSON-update step as a missing prerequisite for PR-2.
While inspecting the secondary-weapon loader, the architect's compound
finding ("the `loading speed` bug causes `damage=0` and `value=0` across
29/30 secondaries") was decomposed into two separate facts. **The
original framing in Entry 5i/architect report conflated two independent
issues**:

**Fact 1 — Loader bug (real, fixed in PR-2 L2)**:
- `secondary_weapon_repository.py:68` reads `raw.get("loadingSpeed")`.
- All 30 seed JSONs use `"loading speed"` (lowercase, with space). Zero
  use `"loadingSpeed"`.
- Consequence: `loading_speed` column = NULL for every secondary that
  has a populated `"loading speed"` value (e.g. shesha: JSON has
  `"loading speed": 3000`, DB has `loading_speed: NULL`).
- Fix: accept both keys, primary = `"loading speed"`, fallback =
  `"loadingSpeed"` for forward-compat.

**Fact 2 — Bad seed data (real, fixed in PR-3)**:
- 28/30 secondary JSONs have `damage: 0` and `value: 0` literally in
  the file. This is **not** caused by the loader bug — it's placeholder
  data that was never populated.
- Wiki v2 capture has real values for all 30 (e.g. Garuda-IV
  damage=300, Patala damage=250, etc.). PR-3 enrichment populates them.

Practical impact on PR sequencing: unchanged. PR-2 still fixes the
loader so that PR-3's real values land correctly. The earlier
"shesha is the only secondary with real damage" turns out to be:
shesha + 1 other have real damage; even shesha has NULL loading_speed
in the DB because of the loader bug.

The Entry 5i claim "29/30 secondaries have `damage=0` and `value=0`
because loader reads `loadingSpeed` instead of `"loading speed"`" is
hereby corrected: those zeros are baked into the JSONs themselves. The
loader bug is real but its symptom is NULL `loading_speed` on the
populated rows, not zero damage on the placeholder rows.

### Entry 5l — Data-gap close: combat modules + Terran Battlecruiser + Specter key fix (2026-05-27)

Verification pass against PR-3 enrichment identified five files with missing or incorrect data.

**Combat module extra_atts gaps filled (3 files):**
- `repair_bots.ketar_repair_bot.json` — added `extra_atts.HPps: 7`, `mechanics_text` from wiki v2.
- `repair_bots.ketar_repair_bot_ii.json` — added `extra_atts.HPps: 15`, `dlc: "Valkyrie"`, `mechanics_text` from wiki v2.
- `misc.phoenix_sis.json` — added `extra_atts.plasma_consumption_t: 30`, `blueprint_only: true`, `mechanics_text` from wiki v2.

Note: HP/s figures (7 and 15) are pre-existing seed values, not wiki-sourced. The wiki v2 repair bot entries explicitly state "HP/s not listed in category table." The module loader already stashes unknown top-level keys into `extra_atts`; explicit `extra_atts` entries win on key conflict (PR-2 loader design).

**Terran Battlecruiser enrichment (1 file):**
- `terran.battleship.json` — added `extra_atts.wiki_status: "npc_stats_only"` sentinel + `mechanics_text` from wiki v2.
- Wiki describes the NPC capital-ship variant (armour 7000–7700, listed as non-player-purchasable). Player seed-JSON stats (armour 1800, maxPrimaries 2, etc.) are correct for the Supernova player version and were NOT overwritten.
- The "NPC capital ship. Not player-purchasable." wiki line was excluded from mechanics_text as it contradicts the ship's in-game shop behaviour.

**Specter duplicate key removed (1 file):**
- `nivelian.specter.json` — removed erroneous `builtin_modules` (snake_case) key introduced by the PR-3 enrichment script. Kept `builtinModules` (camelCase) which ShipRepository maps correctly to the `builtin_modules` DB column.

**Items intentionally not enriched in this pass:**
- 4 cargo/freighter ships (`midorian.cargo_midorian`, `nivelian.cargo_nivelian`, `terran.cargo_terran`, `vossk.cargo_vossk`) — non-combat (`maxPrimaries: 0`), deferred.
- `misc.shock_blast` `value: 0` — no wiki price source available.
- Ion Lambda MK1/MK2 `damage: 0` — ionizing missiles are inert in the combat system; no ionizer mechanic is planned.

**EMP secondary verdict (from verification):**
- `netha_emp` and `mamba_emp` `damage: 0` is correct — `extra_atts.emp_damage` holds the real stat.
- Ion Lambda weapons have `damage: 0` and no `emp_damage` — their effect is `magnitude_m` (engine disable radius), which requires no combat resolver support.

### Entry 6 — Commodities scrape + data model proposal (2026-05-27)

Scope: collect "loot" commodity catalog (drinks/space junk/ores/etc.) for the
upcoming tractor-beam-capture-to-shop-sell mechanic. Combat resolver (PR-4) is
deferred until commodity foundation lands.

**Data captured at `/proj/.combat-rewrite-wiki-v2/commodities/`** — 91 individual
JSON files + `_combined.json` + `_summary.md`.

Each file shape:
```json
{
  "_name": "...",
  "_category": "commodity",
  "_subcategory": "ore|ore_core|standard|technical|rare|waste|mission|booze|plasma",
  "_url": "...",
  "_extracted_at": "...",
  "raw_infobox": { ... },
  "stats": {
    "tech_level": int|null,
    "price_range_min_credits": int|null,
    "price_range_min_system": str|null,
    "price_range_max_credits": int|null,
    "price_range_max_system": str|null,
    "value": int|null,
    "price_source": "range_midpoint|single_price|origin_system_price|not_listed|mission_only|not_purchasable|not_available",
    "origin_system": str (booze only)
  },
  "in_game_description": str,
  "mechanics_text": [str, ...],
  "wiki_categories": [str, ...]
}
```

**Value calculation rules (locked per user):**
- Default: `value = round((min + max) / 2)` from wiki "Known Price Range"
- Single-price items (Documents): use that single value, `price_source = "single_price"`
- Booze (22 individual files): `value = named-system (origin) price`, NOT midpoint. User direction was explicit. `price_source = "origin_system_price"`.
- Items without listed prices: `value = null`, `price_source = "not_listed"`

**Coverage:** 87 real GOF2 commodities + 4 non-commodity pages still in the
directory (`amr.json`, `gemstones.json`, `neuro_algae.json`, `sao_perula.json`).
These four should be excluded at seed time, not deleted — user has not
confirmed deletion.

**Subcategory breakdown (91 files):**
- standard: 29 (22 booze + 7 others)
- ore: 12 · ore_core: 12 · technical: 13 · rare: 10
- plasma: 5 · waste: 3 · mission: 3 · other: 4 (excluded)

**8 null-value items (legitimate, all in `_summary.md`):**
- Purple Plasma, Red Plasma — no price on wiki
- Secure Cabin, Secure Container — mission-only, 0$ price
- AMR, Gemstones, Sao Perula — non-commodities
- Neuro-algae — not purchasable in GOF2

**Special flags surfaced in stats / mechanics:**
- `volatile=true`: K'mirkk Toad Mutagen, Red Plasma
- `blueprint_only=true`: Chromo Plasma (value ~471,801cr)
- `mission_only=true`: Documents, Secure Cabin, Secure Container
- Booze: `origin_system` field drives `value`

**Process gotchas (for future scrapes):**
1. Direct `wiki/Item_Name` HTML fetches via `crawl4ai_scrape` hit Cloudflare. Use `?action=raw` MediaWiki endpoint via `searxng_web_url_read` — bypasses the JS challenge cleanly. This is the same approach Entry 5j used for the mechanics_text rescrape.
2. Researcher subagent's HTTP client gets blocked; architect subagent + searxng works.
3. Each page must be fetched and parsed individually — infobox structures vary (`{{Infobox}}`, `{{infobox}}`, wikitable, no infobox at all for collection pages like Booze). NEVER assume layouts are uniform.

### Entry 6a — Commodity data model proposal (locked) (2026-05-27)

User-locked model: extend the existing `Item` joined-table-inheritance tree
with a new `Commodity` branch:

```
Item  [item table]
├── Module / Weapon / PrimaryWeapon / SecondaryWeapon / TurretWeapon  (existing)
└── Commodity   [commodity table]                       ← NEW
    ├── Booze   [booze table]                           ← NEW
    ├── Ore     [ore table]                             ← NEW
    └── OreCore [ore_core table]                        ← NEW
```

Standard/Technical/Rare/Waste/Mission/Plasma subcategories live directly on
`Commodity` and are distinguished by a `subcategory` enum column on the base.
Subclasses only exist where there are real schema differences:
- **Booze**: has `origin_system`, `loma_price`, `highest_non_loma_price`, `highest_non_loma_system`
- **Ore**: has `mining_locations` (ARRAY)
- **OreCore**: sibling of Ore (NOT a subclass / not via `is_core` flag) — user reasoning: ore cores are a distinct gameplay concept (higher value, mining-mechanic-specific, likely to grow more divergent), modeled as siblings makes future divergence cheap

Plasma was dropped from subclass list per user direction: SIS is the only
planned consumer and consumes plasma by name — no per-plasma column needed.

**`Commodity` base columns:**
- `id` (FK → item.id, PK)
- `subcategory` (String(32), discriminator: `standard|technical|rare|waste|mission|booze|ore|ore_core|plasma`)
- `tech_level` (Integer, nullable)
- `price_range_min_credits` / `price_range_min_system` / `price_range_max_credits` / `price_range_max_system` (nullable)
- `price_source` (String(32))
- `volatile`, `blueprint_only`, `mission_only` (Boolean, default False)
- `in_game_description` (Text, nullable)
- `mechanics_text` (JSON, default `[]`)
- `wiki_categories` (ARRAY(String), default `[]`)
- `extra_atts` (JSON, default `{}`) — escape hatch matching Weapon/Module/Ship pattern
- `polymorphic_identity = "commodity"`

`Item.value` is inherited (will hold the computed `value` from the JSON).

### Entry 6b — Commodity rollout plan (5 PRs) (2026-05-27)

PR ordering and scope locked. Each PR is independently mergeable.

**PR-A — Schema (smallest landable chunk):**
1. `persist/database/tablenames.py` — add `Commodity = "commodity"`, `Booze = "booze"`, `Ore = "ore"`, `OreCore = "ore_core"`
2. `persist/models/commodity.py` — new `Commodity(Item)`, polymorphic_identity = "commodity"
3. `persist/models/booze.py`, `persist/models/ore.py`, `persist/models/ore_core.py` — three subclass files
4. `persist/database/revisions/versions/0010_commodity_schema.py` — generated + hand-reviewed, idempotent inspector pattern matching 0008/0009
5. Migration smoke test on dev DB — verify 4 tables exist with correct FKs to item.id

**PR-B — Seed data + repositories:**
6. Copy 87 commodity JSONs (exclude the 4 `_subcategory: "other"` files) from `/proj/.combat-rewrite-wiki-v2/commodities/` to `/proj/services/bot-core/import_data/commodity/`
7. `persist/repositories/commodity_repository.py` — `CommodityRepository` with `create_or_update(payload)` dispatching on `_subcategory`:
   - `"booze"` → `Booze`
   - `"ore"` → `Ore`
   - `"ore_core"` → `OreCore`
   - everything else → base `Commodity` with `subcategory` set from `_subcategory`
   - `"other"` → skip with warning (defense-in-depth)
   - Maps `_name` → `Item.name`, `stats.value` → `Item.value` (or 0 for null), carries flags + locations + mechanics into the right columns
   - Standard `try/except` + `await db.rollback()` per repo AGENTS.md
8. `persist/repositories/booze_repository.py`, `ore_repository.py`, `ore_core_repository.py` — thin subclass repos for type-specific queries
9. `utils/auto_seeder.py` — append `"commodity"` to `SEED_CATEGORIES`
10. Smoke test: empty-DB startup → 87 commodity rows across 4 tables

**PR-C — Pydantic schemas:**
11. `api/schemas/about_schema.py` (or new `commodity_schema.py`) — `CommodityResponse`, `BoozeResponse(CommodityResponse)`, `OreResponse(CommodityResponse)`, `OreCoreResponse(CommodityResponse)`. All `ConfigDict(from_attributes=True)`.

**PR-E — bot-core about API surface:**
12. `api/routers/about.py` — extend three endpoints:
    - `GET /about/categories` — append `"commodity"`
    - `GET /about/categories/{category}/objects` — handle `commodity`, return `[{name, aliases, emoji, icon, subcategory, tech_level}, ...]`
    - `GET /about/object/name/{name}` — dispatch order: ship → weapon tree → module → commodity → criminal → system. Return correct subclass response.
13. `commodity_repository.list_all()` — lightweight catalog dict for the `/objects` endpoint

**PR-D — discord-gateway aboutCog:**
14. `cogs/aboutCog.py` `_create_object_embed` (~line 195):
    - Add `"commodity": discord.Color.teal()` to color_map (~line 201)
    - New `elif category == "commodity":` branch after the existing ones (~line 318):
      - **Subcategory** (inline) — `subcategory.replace("_", " ").title()`
      - **Tech Level** + **Value** — handled by existing generic block at lines 237/240
      - **Price Range** (inline=False) — `{min:,}cr ({min_system}) → {max:,}cr ({max_system})`, omitted when `price_source == "not_listed"` or null
      - **Origin System** (Booze only, inline)
      - **Loma Price** + **Highest Non-Loma** (Booze only)
      - **Mining Locations** (Ore/OreCore, inline=False, truncated to 1024 chars)
      - **Lore / Mechanics** — `in_game_description` + first few `mechanics_text` entries, truncated to 500 chars (reuse pattern at lines 338-339)
      - **Flags footer** — append to existing footer (`f"ID: {id} · Volatile · Blueprint-Only"`) only when applicable
    - Add `"commodity"` to 2-column grid tuple (~line 359)
    - `_preload_data` (~line 87) — NO CHANGES (iterates whatever `/about/categories` returns)
    - `object_autocomplete` (~line 131) — NO CHANGES (uses `_objects_cache` populated by generic preload)

**Smoke tests (PR-D + PR-E together):**
- `/about` category dropdown shows "Commodity"
- Iron → Subcategory=Ore, TL=1, Value=26cr, Price Range=9cr (Suteo) → 42cr (Ni'mrrod), Mining Locations populated
- Aquila Cocktail → Subcategory=Booze, Value=304cr, Origin System=Aquila, Loma Price=680cr
- Chromo Plasma → Value=471,801cr, "Blueprint-Only" in footer
- K'mirkk Toad Mutagen → "Volatile" in footer
- Documents → "Mission-Only" in footer
- Purple Plasma → no Value field, no Price Range field
- `/list_category category:commodity` → paginated list of all 87 commodities

**Tests:** `tests/cogs/test_aboutCog.py` — module-scoped `mock_bot` per `cogs/AGENTS.md`, max 2 mocks per test, mock the http_client.get to return commodity payloads, assert embed fields.

**Out of scope (deferred):**
- Loot rolls from combat (waits for PR-4 tick resolver to have stable fight-end hooks)
- Shop selling of captured commodities (`ShopService.sell_item` extension)
- Business rules for non-sellable items (Documents/Secure Cabin/Secure Container) and non-droppable items (Chromo Plasma)

**Risks called out at planning time:**
- `Item.name` unique constraint — must preflight-check all 87 commodity names against existing weapon/module/ship/criminal/system names before PR-A merge
- New polymorphic_identity values (`commodity`, `booze`, `ore`, `ore_core`) don't collide with existing ones — sanity-grep before PR-A
- auto_seeder idempotency holds: `table_is_empty(repo)` returns False once any row exists, same contract as existing 7 categories

**Rollback:** `python -m persist.database.run_migration downgrade -1` removes all 4 new tables. PRs are linear-dependent (D needs C needs B needs A) but each is shippable on its own once its predecessor lands.

### Entry 6c — Open process issues (2026-05-27)

Documented here so they don't get lost across context boundaries:

1. **Task tool subagent dispatch failing** — `task` tool calls with `subagent_type: "architect"` returned `Unknown agent type: oracle is not a valid agent type` despite `architect` being listed as valid in the system prompt AND `/proj/.opencode/agents/architect.md` being a valid `mode: subagent` definition. Same error for "general" and "developer". User opted to proceed without subagent dispatch — I (Opus 4.7) did the data model design work directly. If we need to dispatch subagents later, the opencode agent-type-resolution path needs investigation; the agent definitions look correct.

2. **User feedback updates** — `feedback_check_before_changing.md` was already updated this session to reflect "no exceptions on assumptions" — applies to ALL data/numbers/decisions, not just code edits. Saved before context compaction.

3. **PR-4 (tick combat resolver) is paused** — commodity foundation (PRs A-E above) lands first per user direction. Tick resolver work was the next thing in queue per the Entry 5j plan; resume after PR-D ships.

---

### Entry 7 — PR-4 design decisions session (2026-05-29 → 2026-05-30)

Live working session to resolve the remaining open design questions before
any PR-4 code is written. This entry is updated **in-place** during the
session as decisions land. See "LOCKED" and "STILL OPEN" rolling sections
at the bottom.

> **Canonical surface moved on 2026-05-30**: §1–§5 at the top of the file
> are now the canonical Phase-1 spec. Entry 7 below remains as a
> chronological working log of what was decided in this session and
> when. If §1 and Entry 7 disagree, §1 wins.

> Resume rule: on session resume, read §1–§5 first; only consult this
> entry if you need decision provenance.

#### Role separation (CORRECTED — was previously conflated)

- **Thrusters** = close-range maneuverability **only**. They do NOT affect
  distance / closure / weapon range / rocket accuracy. Their combat effect
  is to reduce the opponent's hit-chance against you while you are at close
  range (defender-side debuff). Range window: current_distance < 750m
  (inherited from Entry 5f, but the *direction* flips from attacker-bonus
  to defender-debuff).
- **Boosters** = the speed-boost / "turbo" device. Push distance out
  (locked 5e/5f formula stands) AND reduce opponent's accuracy while active
  (5a returns).

#### Combatant base accuracy (NEW — supersedes the per-weapon-base proposal)

- Player base accuracy: **60%**.
- NPC / criminal base accuracy: **50%**.
- Base is a property of the *pilot* (combatant-level), not the weapon. The
  committed `ShipLoadout.base_accuracy = 1.0` stub at
  `combat_models.py:128` is the slot — value gets set to 0.60 / 0.50.

#### Layered accuracy formula (shape)

```
attacker_accuracy = combatant_base                 # 60% / 50%
                  + own_equipment_modifiers        # scanner +X%, etc.
                  − opponent_cloak_debuff          # while cloak active
                  − opponent_booster_debuff        # while boost active, scales w/ speed
                  − opponent_thruster_debuff       # when current_distance < 750m
                  ± per-weapon mods?               # see Q2
                  ± distance penalty               # for primaries
                  → clamp [0.05, 0.99]
```

#### Cloak (REFRAMED)

- Cloak is an **accuracy debuff**, NOT a forced miss. User example:
  60% → 25% during cloak.
- Math interpretation (additive / absolute / multiplicative) still open —
  see Q1.
- HP-threshold triggers: **66% / 33%** (2 activations), replacing
  Entry 5b's "30% single activation" lock.

#### HP / damage integer rule

- HP pool is int; per-tick HP delta is int.
- Shield regen schedule: emit `+1 HP every N ticks`, where
  `N = ceil(shield_recharge_ms / shield_capacity / tick_ms)`.
  Worked example: Targe (50 cap / 20000ms recharge) → +1 HP every 40 ticks
  (400ms). Same shape for any slow regen.
- Regen pulse applied **before** damage in the tick, so "finishing blow"
  detection is clean (HP ≤ 0 at end of tick = dead).
- **Concurrent regen across layers**: shield regen and hull/armour regen
  run *in parallel* each tick (independent regen tracks). When both layers
  are damaged, both recover simultaneously — neither blocks or delays the
  other.

#### Activation triggers (HP-threshold devices)

- **Cloaks**: 66% / 33% HP (2 activations).
- **Boosters**: 80/60/40/20 HP (4 activations). Locked from Entry 5b;
  the 75/50/25 alternative is retired.
- **Thrusters**: passive vs toggled pending TH4 — leaning **passive**
  (close-range always-on, no HP-trigger needed, no `duration_ms` in wiki).
- **EmergencySystem**: triggers when an incoming damage event would
  reduce hull HP to 0 or below (lethal-blow interception). On trigger:
  **hull HP is clamped to 1** (lethal damage prevented from taking it
  below 1); 10s of full invuln begins. ALL incoming damage blocked during
  invuln. Regen continues — shield recharge and hull/armour repair (if a
  Repair Bot is equipped) both accumulate over the 10s window per the
  concurrent-regen rule. **HP at invuln expiry = 1 + 10s × applicable
  regen rates** (capped at each layer's max). Edge case: no shield and no
  Repair Bot equipped → HP = 1 at expiry. **Consumable** — removed from
  the player's ship loadout after use; must manually re-equip from
  inventory if they have a spare. **Trigger scope: hull-layer only**
  (true ship-death prevention; shield or armour reaching 0 does NOT
  trigger the device).

**Trigger rule (locked):** at any HP-threshold crossing, the device
activates iff off cooldown. Still cooling = threshold *skipped*, no retry.
Cooldown timer starts when the **effect expires**, not when activated.
Sequence per activation:
`trigger → run for duration_ms → cooldown begins → cooldown lasts loading_speed_ms → eligible at next threshold`.

#### Turrets

- **Manual turret + primary** = mutually exclusive. Pre-combat
  pilot-dedicates one. Default = primary (typically higher damage).
  Override command does not exist yet; modeled with a `manual_turret_mode`
  flag on the loadout (default False).
- **Auto turret** = additive — fires on its own cooldown alongside
  primaries.
- **Auto turret accuracy** = multiplicative against pilot's *current*
  accuracy (scales with all live debuffs). Magnitude: ×0.85 to ×0.90
  (= 10–15% reduction band). Final number TBD (T2).
- All auto turrets on a single ship **share one accuracy value** (no
  per-turret variation; e.g. an 8-turret battlecruiser has one
  auto-turret-accuracy value applied to all 8).

#### Scanners

- **Three tiers** (combat scanners only — plasma scanners ignored for
  Phase-1, none in seed data):
  - **Tier A — no scanner equipped**: no accuracy bonus. **Missiles
    degrade to rocket behavior** — no tracking, no lock, fire-and-forget.
    Same projectile object, same base stats (damage / cooldown / speed);
    accuracy uses the rocket curve (linear 5% at max range → 60% at min
    distance, Entry 5f) with the distance penalty applied.
  - **Tier B — slow combat scanner (≥3.0s lock)**: +5% to pilot base
    accuracy. Enables **missile tracking**: missiles fire at the pilot's
    current accuracy (subject to cloak / thruster / booster debuffs;
    distance penalty does NOT apply when tracking is active). Modules:
    Telta Quickscan (4.0s), Telta Ecoscan (3.0s).
  - **Tier C — fast combat scanner (~1.8s lock)**: +10% to pilot base
    accuracy. Same missile-tracking behavior as Tier B. Modules: Hiroto
    Proscan, Hiroto Ultrascan.
- **Unique-equip**: combat scanner is unique-equip on its own subclass
  (one combat scanner at a time). Plasma scanner is a separate subclass
  with no combat effect.
- **Stacking**: the +5% / +10% is added to combatant base accuracy
  (60% player / 50% NPC) before all other modifiers. A Tier-C player
  starts at 70% pre-modifier; a Tier-B player starts at 65%.
- **Lock-time numerics** (4.0 / 3.0 / 1.8s) are flavor in Phase-1 — the
  tick resolver does not model lock-time delay; tier membership is what
  matters. Recorded here for future use.
- **Thermal-fusion "homing" effect: out of scope.** Thermal-fusion is a
  primary weapon class and follows primary-weapon accuracy rules
  (combatant base + own/opponent modifiers + distance penalty). Its
  in-game homing effect is bypassed in Phase-1 for simplicity; scanner
  tier does **not** modify thermal-fusion behavior.

#### Phase-1 module roster

- **In**: shields, hull, armour, repair-bot, thrusters, cloaks, boosters,
  auto-turrets, scanners, secondaries (rocket / missile / nuke), and
  EmergencySystem (consumable; mechanic TBD).
- **Out (deferred to Phase-2)**: ShieldInjector (Phoenix SIS), RepairBeam,
  TransfusionBeam.
- **Inert (kept for fidelity, no combat effect)**: GammaShield (no
  radiation-damage source in scope).

#### EMP

- Gets a **real mechanic** (TBD), not flat damage.
- Applies to: EMP-blaster primaries, EMP-bomb secondaries, EMP nukes (e.g.
  `dephase_emp`, `mamba_emp`, `netha_emp`, EMP rockets, `intelli_jet`-style
  carriers). Possibly the right model is shield-disable for N seconds — to
  be decided.

#### Nukes

- **Fireworks** (`damage=1`) is decorative / joke item; ignore as baseline.
- Use **Liberator** and **Oppressor** as the realistic baseline. Pull all 5
  nukes' real damage values when we design AoE-falloff specifics.

#### Decisions log — running

##### LOCKED (in this Entry-7 session)
1. Combatant base accuracy: 60% player / 50% NPC.
2. HP / damage are int; shield regen via +1 HP every N ticks; regen before
   damage in each tick.
3. Auto-turret accuracy: multiplicative ×0.85–0.90 of pilot's current
   accuracy, uniform across all turrets.
4. Manual turret + primary mutex: pre-combat pilot-dedicates,
   default = primary, future override command.
5. Thrusters: close-range maneuverability only
   (`current_distance < 750m`). Direction: defender-side opponent-accuracy
   debuff. Do NOT affect distance / range. Entry 5f's *attacker-bonus*
   framing is **retired**; window stays the same.
6. Boosters: distance push (5e/5f formula stands) + opponent-accuracy
   debuff (5a returns) while active.
7. Cloak: accuracy debuff (60→25 example), NOT a forced miss. Earlier
   "forced miss" model **retired**.
8. Cloak HP-thresholds: **66% / 33%** (2 activations). Replaces Entry 5b's
   30%-single.
9. Phase-1 module roster: in = EmergencySystem (consumable, mechanic TBD);
   out = ShieldInjector, RepairBeam, TransfusionBeam.
10. EMP gets a real mechanic (TBD).
11. Nukes: Liberator / Oppressor baseline; ignore Fireworks.
12. Activation rule (HP-threshold devices): triggers iff off cooldown;
    missed = skipped, no retry; cooldown starts at *effect expiry*.
13. Scanner tiers (combat scanners; plasma ignored for Phase-1):
    Tier A = no scanner → no bonus, missiles act as rockets;
    Tier B = slow (≥3.0s lock — Quickscan, Ecoscan) → +5% pilot
    accuracy, enables missile tracking;
    Tier C = fast (~1.8s lock — Proscan, Ultrascan) → +10% pilot
    accuracy, same tracking.
14. Combat scanner is unique-equip on its own subclass.
15. Missile base accuracy: when tracking is active (Tier B/C scanner
    equipped), missiles use the pilot's current accuracy (with all live
    modifiers, no distance penalty). When no scanner is equipped
    (Tier A), missiles behave exactly like rockets including the
    distance penalty. Closes the "Missile base accuracy" open question.
16. Thermal-fusion homing effect bypassed in Phase-1. Thermal-fusion is
    a primary weapon — follows primary accuracy rules; scanner tier
    does not affect it.
17. Booster activation thresholds: 80/60/40/20 HP (4 activations).
    Inherits Entry 5b's lock; the 75/50/25 alternative is retired.
18. Per-weapon `accuracy_modifier` dropped permanently. All weapons use
    pilot's current accuracy verbatim (subject to distance penalty +
    cloak/thruster/booster debuffs). Weapon differentiation in Phase-1
    lives in damage / range / cooldown only. Forward-compat hook:
    `weapon_accuracy(pilot_acc, weapon)` returns `pilot_acc` unchanged;
    an empty `SUBTYPE_ACCURACY_MOD: dict[str, float]` lives in
    `combat_balance.py`. Tech-debt: remove `WeaponStats.accuracy_modifier`
    (multiplicative, never populated); keep `ModuleStats.accuracy_modifier`
    (additive, carries scanner bonus). Closes Q2.
19. Damage-type model: Phase-1 = **physical only**. EMP is a separate
    damage type, deferred to Phase-2+. Resolver rule: every weapon fires
    on cooldown, rolls accuracy, logs hit/miss; physical `damage_per_shot`
    is applied to HP layers; `emp_damage` is ignored regardless of value.
    Pure-EMP weapons (e.g. `mamba_emp`) fire normally but apply 0 HP delta.
    Hybrid weapons (e.g. `dephase_emp`, `intelli_jet`, EMP rockets, EMP
    bombs, plus surprise `armour_rocket` with `emp_damage=24`) apply only
    the physical component. **Verified inventory note:** the 3 EMP-blaster
    PRIMARIES (`dia_emp_mk_iii`, `luna_emp_mk_i`, `sol_emp_mk_ii`) all
    have non-zero `damage_per_shot` (3 / 5 / 8) — they are LOW-damage
    physical weapons, not pure-EMP.
20. Shock-blast pulled into Phase-1 with simplest mechanic:
    instantly resets both ships to starting distance (5000m); 100%
    guaranteed (no accuracy roll); no damage applied; fires on cooldown
    (`loading_speed_ms`); no per-fight cap; no HP-threshold gating. The
    seed's `damage: 140` and `emp_damage: 80` are IGNORED in Phase-1.
21. Phase scope tightening: mines + sentry-guns deferred to **Phase-3+**
    (not Phase-2 as Entry 5d originally claimed). EMP mechanic, emp-bombs,
    ShieldInjector (Phoenix SIS), RepairBeam, TransfusionBeam, OOC HP
    recovery, dock instant-repair, damaged-opponent start state remain
    Phase-2.
22. Turret subtypes: 3 exist in seed (auto, manual, plasma-collector);
    only 2 are combat-relevant (auto + manual). Plasma-collector
    (`subtype: "plasma-collector"`, `dps: 0`) is inert in combat —
    equippable for fidelity, no effect. Mirrors plasma-scanner pattern.
    **Data note:** auto/manual turrets carry no `subtype` field in
    `extra_atts`; combat code must discriminate via `automatic: bool`.
    Only plasma-collectors carry an explicit `subtype` value.
23. EMP Phase-2 partial spec captured for parking: when an EMP hit lands
    on victim, victim outgoing damage = 0, firer accuracy vs victim =
    100%, duration TBD (some seconds). "Exponential backoff" phrasing
    parked. Stacking, hit-roll-vs-effect, other-subsystem-disable
    sub-questions all parked for Phase-2 revisit. Full design = O-E in
    §2.
24. Repair Bot rate model: **percentage-of-max** locked
    (2026-05-30, closing §6 O1). Ketar I = 2.5%/s, Ketar II = 5.0%/s
    of `max_hull + max_armour`. Seed `extra_atts.HPps` (7/15) becomes
    stale data; resolver ignores. §1.3, §1.7 updated.
25. **Configuration policy** (2026-05-30): every numeric in §1 is a
    starting default. Targets `game_constants.py` + `BOUNTYBOT_*`
    env-var + per-guild override, matching the existing
    `DUEL_VARIANCE_PERCENT` / `BOUNTY_PVC_ARMOUR_BUFF_FACTOR` pattern.
    Post-Phase-1 tuning should not require code changes.
26. **Resource policy** (2026-05-30, generalises HE-5a): energy is
    assumed infinite for combat AND any other gameplay surface.
    Energy cells are NOT tracked. Wiki lore references to per-use
    energy consumption (U'tool cloak, etc.) are cosmetic. Applies
    to players AND criminals. Repair-bot consumption, cloak
    activation, and any future energy-gated mechanic skip the check
    and proceed unconditionally.

##### LOCKED (inherited from earlier journal entries)
- Tick = 10ms fixed; max ticks = 18,000 (3-min cap). (Entry 5j)
- Booster distance-push formula:
  `distance_gained = base_speed × (effect_pct/100) × (duration_ms/1000)`.
  (Entry 5f)
- Distance: 5000m start, 150 m/s base ship speed, 300 m/s passive closure,
  300m floor. (Entry 5e)
- Rocket accuracy curve: linear 5% at max range → 60% at min distance.
  (Entry 5f)
- Shield + Repair-bot recharge: continuous per tick. (Entry 5f)
- Repair Bot rates: 2.5%/s (Ketar I), 5.0%/s (Ketar II) of
  `max_hull + max_armour`. (Entry 3)
- EmergencySystem (fully locked): triggers when an incoming damage event
  would reduce the **hull layer** to 0 or below (true ship-death
  interception; shield or armour reaching 0 does NOT trigger). On
  trigger: hull HP clamped to 1. 10s full invuln — ALL incoming damage
  blocked. Regen continues during invuln (shield + hull/armour
  concurrently if a Repair Bot is equipped). HP at expiry = 1 + 10s ×
  applicable regen rates (capped per layer max). Edge case: no shield, no
  Repair Bot → HP = 1 at expiry. Consumable — removed from loadout after
  use; player must manually re-equip a spare from inventory; once per
  fight because consumed.
- WeaponMod is unique-equip in `UNIQUE_EQUIP_TYPES`. (Entry 4 #8)
- Damage stacking order: shield → armour → hull. (Entry 5e)
- Repair Bot fill order: hull first, then armour. (Entry 5f)
- GammaShield inert in Phase-1. (Entry 4 #12)
- Energy pool: unlimited (no energy-cell tracking). (Entry 5a)
- PvP duel stalemate = draw, no rewards. PvC stalemate = criminal
  escapes, hunt-checks reset, new system along route. (Entry 5a)

##### STILL OPEN (canonical queue now at §2 OPEN QUESTIONS — synced)
- **Q1 / O-Q1** Cloak math: additive (-35pp) / absolute (set-to-25) /
  multiplicative (×0.42)?
- **TH3 / O-TH3** Thruster opponent-accuracy debuff magnitude (close-range
  window). Scaling vs `effect_pct`?
- **TH4 / O-TH4** Thruster passive vs toggled (HP-thresholds + cooldown)?
  Leaning passive.
- **T2 / O-T2** Auto-turret accuracy multiplier final value within
  ×0.85–0.90 band.
- **O-B** Booster opponent-accuracy debuff magnitude (scales w/
  `effect_pct`).
- **O-DP** Distance penalty max for primary accuracy (was floated at
  0.20) — separate from rocket curve?
- **O-N** Nuke AoE falloff specifics + per-nuke real damage values
  (Liberator/Oppressor anchors).
- **O-E** EMP mechanic shape — DEFERRED to Phase-2 (partial spec in
  locked item #23 + §3 DEFERRED).

##### Resolved this session (2026-05-29)
- **Q2** dropped — see locked #18.
- Missile base accuracy — closed by scanner-tier rule, locked #15.
- Phase-1 scope of shock-blast / mines / sentry-guns — locked #20, #21.
- Damage-type model — locked #19.
- 3rd turret subtype recognition (plasma-collector) — locked #22.

##### Condensation review topics (2026-05-29 audit pass)
- See §6 CONDENSATION REVIEW OPEN TOPICS at top of file. Walks one at
  a time per standing user rule.

*Entry 7 was updated in-place across multiple sessions. As of
2026-05-30 the canonical surface is §1–§5 at the top of this file;
this entry remains as the chronological working log. This file remains
uncommitted in the working tree until the user reviews and locks PR-4
design.*

---

## Entry 8 — Seed-fix + O-PE lock (2026-05-30)

Researcher subagent verified all 8 EMP-family weapon seeds against
galaxyonfire.wiki.gg as source of truth. Three EMP-blaster primary
seeds were structurally wrong (EMP value misplaced into `damage_per_shot`,
`emp_damage` field missing). Fixed in commit `e87db57`. EMP-bombs,
`mamba_emp`, `netha_emp` verified correct as-is.

Post-fix Phase-1 pure-EMP inventory = 5 weapons:
- Primaries: `luna_emp_mk_i`, `sol_emp_mk_ii`, `dia_emp_mk_iii`
- Secondary missile: `missiles.mamba_emp`
- Secondary mine: `mines.netha_emp`

O-PE resolved → option (a) accept: player can fit pure-EMP, fight goes
ahead, combat log surfaces 0-damage post-fight. No preflight warning,
no loadout-build filter. Rationale: degrades naturally when EMP arrives
in Phase-2 (no UI to rip out); consistent with combat-log being the
canonical visibility surface; "buyer beware" acceptable for a knowable
edge case.

§1.4 updated: `Correction` paragraph removed (was wrong post-fix);
inventory line rewritten with all 5 weapons; `netha_emp` added (was
missing). §2 O-PE marked RESOLVED. §6 C8 marked CLOSED.
`COMBAT_SPEC_LOCKED.md` §4 updated to match.

---

## Entry 9 — O-M split + cluster-missile mechanic lock (2026-05-30)

Researcher subagent deep-dived cluster-missile mechanics from
galaxyonfire.wiki.gg. Findings: all 3 cluster-missile weapons are
**lock-on tracking missiles** (not dumb-fire rockets) that release a
fixed number of sub-munitions per fire (Shesha=3, Garuda-IV=4,
Patala=5). Each sub-munition deals `damage` (per-sub-munition, NOT
total — confirmed via Patala wiki note "If all Missiles hit an enemy
ship, the total damage would be 450" = 90 × 5). Single-target (no
AoE; cluster missiles have no `magnitude_m`).

O-M resolved as a **split**:
- **Cluster-missile** → Phase-1 in-scope as a missile variant. Inherits
  the plain-Missile scanner-tier rule. **N independent accuracy rolls
  against a single fire-time accuracy snapshot** (i.e. thruster ramp or
  cloak activation mid-flight does not retroactively change rolls).
  Each landing sub-munition deals `damage`. Combat-log = ONE event per
  cluster fire with summary `{weapon, fired, hits, damage_per_hit,
  total_damage}` — not N rows per fire.
- **Ionizing-missile** → Phase-3+ deferred (alongside mine, sentry-gun).
  Seed `damage` already 0; no ionizer mechanic planned.

Seed-edit: `burst_count` field added to `extra_atts` on the 3
cluster-missile JSONs (Shesha=3, Garuda-IV=4, Patala=5). Resolver
reads `burst_count` generically; no hardcoded per-weapon mapping.

§1.6 updated: Phase-1 subtypes list adds `cluster-missile`; Phase-3+
deferred list adds `ionizing-missile`; new Cluster-missile bullet added;
open-status O-M line dropped. §2 O-M marked RESOLVED. §6 C6 marked
CLOSED. `COMBAT_SPEC_LOCKED.md` §6.2 updated with the cluster-missile
sub-section; new §14 added capturing **downstream sync requirements**
for item-detail embeds (EMP physical/EMP-damage distinction post seed-
fix e87db57; cluster-missile burst_count display).

---

## Entry 10 — O-E formally locked as Phase-2 DEFERRED (2026-05-30)

EMP mechanic full design (O-E) was already marked "DEFERRED to Phase-2"
in §2 but never formally locked. User confirmed: EMP is unambiguously a
Phase-2 mechanic, fully deferred — closing it.

The partial Entry-7 #23 spec snippet (victim outgoing damage = 0; firer
accuracy vs victim = 100%; duration TBD) is a Phase-2 design
starting-point, NOT a Phase-1 mechanic. It is intentionally NOT
promoted to `COMBAT_SPEC_LOCKED.md` (the spec only carries firmly-locked
Phase-1 content per the strict no-escape-hatch rule).

Phase-1 handling of all EMP weapons (pure-EMP and hybrid) is fully
specified by:
- §1.4 damage type model (Phase-1 = physical only; EMP ignored)
- §2 O-PE (✅ pure-EMP equip policy = accept)
- §6 / spec §4 / spec §6.2 / spec §11 / spec Appendix C (deferral
  cross-references)

§2 O-E marked ✅ LOCKED as Phase-2 DEFERRED. No spec edit needed.

---

## Entry 11 — O-N lock: nuke mechanic (epicenter + inverse-square + self-damage) (2026-05-30)

User-driven design conversation locked the full nuke mechanic. Major
departures from the prior "AoE secondary" placeholder model:

1. **No accuracy roll.** Nukes completely bypass the §1.5 accuracy
   system: no cloak override, no thruster/booster modifiers, no §1.6
   missile/rocket curves. Rationale: nukes are area-of-effect /
   "radiation"-style weapons that cover the whole combat zone — there
   is nothing to "miss."
2. **Steerable flag ignored Phase-1.** Liberator's `steerable: true` is
   data-only fidelity. All 5 nukes are mechanically identical except
   for `damage` and `magnitude_m`.
3. **Epicenter is RNG.** On fire, sample a uniform random distance in
   `[300m, 5000m]` (the §1.2 combat-distance bounds). This is the
   "where the nuke detonates" point on the 1D combat axis.
4. **Both ships always take damage.** Computed from each ship's
   distance to the epicenter via inverse-square falloff:
   `dmg(d) = damage × (1 - min(1, d / effective_magnitude))²`. Firer
   is at position 0 (so `d_firer = epicenter`); opponent at
   `current_distance` (so `d_opponent = |epicenter - current_distance|`).
5. **Magnitude scaling.** Seed `magnitude_m` (10000–40000m) vastly
   exceeds combat distance (5000m max). Without scaling, falloff
   barely bites. Locked: `effective_magnitude = magnitude_m ×
   NUKE_MAGNITUDE_SCALE` where `NUKE_MAGNITUDE_SCALE` defaults to
   **0.10** (configurable). Resulting effective magnitudes: Tormentor
   1000m, Liberator 1250m, Oppressor 3000m, Extinctor 4000m,
   Fireworks 1000m. Preserves per-nuke AoE variation (Tormentor =
   tight short-range, Extinctor = wide long-range).
6. **Self-damage.** Firer absorbs `dmg(d_firer) ×
   NUKE_FRIENDLY_FACTOR` where `NUKE_FRIENDLY_FACTOR` defaults to
   **0.25** (configurable). Reasonable tactical layer emerges:
   firing at a close opponent risks heavy self-damage (Liberator
   point-blank ≈ 123 self-dmg, Extinctor point-blank ≈ 150 self-dmg).
7. **Combat-log event** (one per nuke fire) records `{weapon,
   epicenter, current_distance, d_firer, d_opponent, opponent_damage,
   self_damage}` per the §1.12 event-tick model.

Per-nuke `damage` seed values (Liberator 850 / Extinctor 700 /
Oppressor 400 / Tormentor 150 / Fireworks 1) accepted as direct-hit
anchors — the other half of O-N. No seed-data edits needed.

§1.6 Nuke bullet rewritten in full. §2 O-N marked RESOLVED. Spec
updates: §6.2 gains a Nuke sub-section; Appendix A adds
`NUKE_MAGNITUDE_SCALE` (0.10) and `NUKE_FRIENDLY_FACTOR` (0.25);
Appendix B adds the falloff formula; §14 adds an item-detail embed
requirement (display direct-hit damage + effective magnitude + self-
damage warning so players can reason about the trade-off).

---

## Entry 12 — O-STAT lock: 3 lifetime counters on Player (2026-05-30)

Conversation pared the draft 6-field list down through two filtering
rounds:

1. **Granularity** — chose hybrid (option c): one coarse
   `total_secondaries_fired` bucket + `total_nukes_fired` callout;
   skip per-rocket / per-missile / per-cluster counters (those are
   derivable from `combat_log` when it exists / before retention
   prunes).
2. **Bounded-growth concern** — user flagged that any counter that
   grows uninterestingly fast over time becomes a "huge meaningless
   number" stat. Removed:
   - `total_damage_dealt` / `total_damage_taken` / `total_self_damage`
     (damage values accumulate fast and the headline number isn't
     interesting to a player)
   - `total_shots_fired` (every primary trigger; same drift)
   - `total_secondaries_fired` (similar drift; less acute but still
     uninteresting at scale)
3. **Asymmetry** — user opted to keep `bounty_wins` without
   `bounty_losses`; the asymmetry is intentional in current bounty
   scoring (loss is implicit in "the bounty got away" rather than a
   counter-worthy event).

Final locked list (3 Integer counters, default 0):
- `total_fights`              — aggregate fight participation
- `total_nukes_fired`         — nuke-specific callout (self-damage
                                flavor; brag-worthy)
- `total_module_activations`  — cloak / booster / EmergencySystem etc.

All three are bounded-per-fight (a single fight contributes a small
finite count), so the long-term headline numbers stay meaningful.

§1.12, §2 O-STAT, §4 PR-4/PR-5 file map all updated to the locked
3-field list. Spec §13 prose-list updated to match. One Alembic
migration adds all 3.

NOT locked here (intentionally left for later):
- Per-subtype shot breakdowns (rocket / missile / cluster) — derive
  from `combat_log` if needed, within retention window
- Damage tracks — may revisit in Phase-2 if a tankiness / DPS
  leaderboard feature lands

---

## Entry 13 — O-PWM lock: PrimaryWeaponMod uses damage_pct + fire_rate_pct breakdown (2026-05-30)

Side-question surfaced mid-O-STAT batch: spec §7.8 originally treated
Nirai Overdrive / Overcharge as a flat `+N%` primary DPS multiplier
(honoring only the legacy `dpsMultiplier: 1.1` field), which made the
two modules mechanically indistinguishable. Seed-data inspection
revealed each module carries `damage_pct` + `fire_rate_pct` fields
that represent the actual tradeoff:

- Nirai Overdrive: damage_pct=-10, fire_rate_pct=+20 → lighter-faster
- Nirai Overcharge: damage_pct=+20, fire_rate_pct=-10 → heavier-slower
- Both `dpsMultiplier=1.1` is coincidental (~+8% effective DPS in both
  cases); the *feel* differs even though headline DPS is the same.

Resolved (option b): the new tick-based resolver honors the
damage_pct + fire_rate_pct breakdown. Legacy `dpsMultiplier` is
metadata-only — used by the current SimpleTTKResolver and as an
item-detail-embed display hint, but ignored by the tick-resolver.

Locked formulas:
- `effective_damage_per_shot = round(damage_per_shot × (1 + damage_pct / 100))`
- `effective_loading_speed_ms = round((loading_speed_ms / (1 + fire_rate_pct / 100)) / TICK_MS) × TICK_MS`

Scope clamp: **primary weapons only**. Secondaries (rockets, missiles,
cluster-missiles, nukes, shock-blast), turrets (auto + manual), and
auto-turret outputs are all UNAFFECTED by these modules.

Rounding rules: damage rounds to nearest integer (no `max(1, ...)`
floor — base-0 EMP-blasters stay 0, normal primaries with damage ≥ 2
never reach 0 from -10%). Cooldown snaps to the nearest TICK_MS
boundary (10ms) so the tick-based resolver lines up cleanly.

Mutual exclusion was already locked (Entry 3 + UNIQUE_EQUIP_TYPES);
only the effect-formula was open.

§1.7 PrimaryWeaponMod bullet rewritten. §2 gains a new O-PWM row
(marked RESOLVED — skipped the open phase since the conversation went
straight to lock). Spec: §7.8 rewritten as a proper mechanic spec;
Appendix B gains a PrimaryWeaponMod formula reference block; §14
gains a 4th item-detail embed requirement (display damage_pct,
fire_rate_pct, AND the legacy dpsMultiplier so a player can see both
the breakdown and the net DPS shift).

---

# 8. Architect review (2026-05-30)

*Structural sanity check on the reorganized journal, factoring in §7 researcher findings. **This review supersedes a prior in-tree §8** (discarded as unreliable; its lone 🔴 was stale). The original review made NO design changes and left §1–§7 unchanged. **Follow-up edit (2026-05-30 consolidation pass, user-authorized):** open-question IDs were consolidated — §2 is now the single canonical `O-*` registry, §6 relabelled to a closed `C1–C9` disposition log, the §6 O2 ≡ §2 O-DP duplicate merged, and §6 O6/O8 promoted to §2 O-M/O-PE; §1 inline cites updated to match. No design/numeric values changed. Historical Entries and §7 prose left verbatim as dated snapshots. Data claims independently spot-checked against `import_data/` seed JSONs.*

## 1. Completeness — ⚠️ minor gaps

- ✓ §1.1–§1.11 cover tick/timing, distance, HP layers, damage type, accuracy, all weapon classes, all combat modules, activation rules, termination, unique-equip, and scope. Sufficient as the read-first surface.
- ✅ **FightResults `combat_log` output format — RESOLVED 2026-05-30.** Now specified in **§1.12**: a small Tier-0 summary returned inline with every fight + the `combat_log` row id, and the full **event-tick timeline** (one row per state-changing tick-step, intra-tick processing order preserved) **persisted to a new `combat_log` table** (Integer PK handle, `JSON` `data`, combatant columns w/ Discord-`user_id` player-vs-NPC discriminator, 72 h retention via `db_retention`). Wired in **§4** (PR-4 in-memory production + Player stat promotion; PR-5 persistence slice). Lifetime combat metrics promoted onto `Player` (O-STAT). Discord visualization/render = a later cycle (out of scope). Residual knobs parked as **§2 O-LOG**.
- ⚠️ **Per-weapon stat availability never asserted.** §1.6 assumes every primary/secondary carries `damage_per_shot` + `loading_speed_ms` (post-PR-3 wiki enrichment). HE-1's `PRIMARY_DEFAULTS`/`SECONDARY_DEFAULTS` fallback tables are thereby superseded, but §1 never states "all weapons now carry these post-PR-3, so no damage fallback is needed." One confirming line closes the loop for the implementer.
- ⚠️ §1.6 manual turret cites a `manual_turret_mode: bool` loadout flag; §4 file map does not list adding it to `ShipLoadout`.
- ⚠️ §4 omits two resolver branches present in §1: shock-blast distance-reset hook (§1.2) and missile-tracking-vs-rocket dispatch on scanner tier (§1.6/§1.7).
- Note (not a gap): HE-3 Q9's "winner-by-remaining-HP% (≤5% delta = stalemate)" recommendation was **not** adopted; §1.9 deliberately uses flat draw-on-cap. Divergence is intentional, just undocumented as such.

## 2. Internal consistency within §1 — ✓ clean (1 cross-ref nit)

- §1.5 `own_scanner_bonus` (0/+5pp/+10pp) ↔ §1.7 Scanner tier table (A/B/C). ✓
- §1.8 thresholds ↔ §1.7 per-module: cloak 66/33, booster 80/60/40/20, thruster passive, EmergencySystem lethal-blow — all match. ✓
- §1.6 weapons apply physical `damage_per_shot` ↔ §1.3 stacking shield→armour→hull. ✓
- §1.6 missile Tier-A "degrades to rocket curve" ↔ §1.7 Scanner Tier-A "Degrade to rocket". ✓
- §1.2 shock-blast → "see §6 C7" ↔ §6 C7 (same-tick resolution → moot). ✓
- §1.3 repair-bot scope (hull+armour only, shield independent) stated identically in §1.3, §1.7 Repair Bot, and §1.7 Shields. ✓
- ✓ nit (was ⚠️, now resolved): §1.5 lists `distance_penalty (primaries only) [O-DP]` while §1.6 rockets carry their own 5%→60% curve and missiles (B/C) explicitly skip distance penalty. Internally consistent; O-DP's relationship to the rocket curve remains the open seam, now tracked under the single ID **§2 O-DP** (former §6 O2 merged in — see §8.3).

## 3. Naming / convention — ✅ RESOLVED 2026-05-30 (consolidation pass)

The three coexisting ID schemes have been consolidated. §2 is now the single open-question registry; §6 is a closed log:

| Surface | Scheme | Role |
|---|---|---|
| §2 OPEN QUESTIONS | `O-*` | **Single canonical open-question registry** (namespace legend added at top) |
| §6 DISPOSITION LOG | `C1–C9` | Renamed from `O1–O9` (collision removed); closed log, not a queue |
| Entry 7 (Historical) | `Q*/TH*/T*` | Verbatim HE shorthand, bridged to `O-*` (e.g. `Q1 ≡ O-Q1`) |

- ✅ **Prefix collision removed:** §6 `O<n>` → `C<n>`, so a bare `O-*` cite is unambiguously §2.
- ✅ **Duplication resolved:** former §6 O2 absorbed into §2 **O-DP** (single source of truth); §6 C2 now points there.
- ✅ **Two orphaned open items promoted into §2:** §6 O6 → **O-M** (cluster/ionizing-missile status), §6 O8 → **O-PE** (pure-EMP loadout); §6 C6/C8 point to them.
- ✅ **Inline §1 cites updated:** `§6 C1`, `§6 C7`, `§2 O-M`, `§2 O-PE`.
- ✓ Entry 7's STILL-OPEN HE↔§2 bridge (`Q1 / O-Q1`, `TH3 / O-TH3` …) unchanged and still valid.
- ℹ️ §7's prose still references the pre-rename `O4` (now `C4`); left verbatim as a dated snapshot — the §2 legend documents the `O1–O9 → C1–C9` rename.

## 4. Forward-compat hooks — ✓ adequate (one pointer thin)

- Damaged-start: §1.3 + §3 + HE-3 API hint (`current_*` overrides on combatant init). ✓
- OOC HP recovery + dock: §3 DEFERRED, columns shipped PR-1 (§4). ✓
- EMP: §1.4 detection seam (physical track only) + §3 + §2 O-E + Entry 7 #23 partial spec. ✓
- `weapon_accuracy()` + empty `SUBTYPE_ACCURACY_MOD`: §1.5. ✓
- ⚠️ **Schema column names** for the Phase-2 damage-tracking migration (`current_hull/current_armour/current_shield/last_damage_at` on Player; `criminal_current_*` on Bounty) live only in HE-3 step 4 / HE-4; §4 PR-1 line is terse ("Phase-2 damage-tracking columns"). A Phase-2 reader must dig. Add the column names to §4.

## 5. Reorganization integrity (HE 0–7) — ✓ load-bearing miss now closed

- HE-0 public contract + 5 callsites + cleanup target (bounties.py:227) → §4. ✓
- HE-0 #4 `FightResults.combat_log` addition → ✅ **RESOLVED** — output format specified in §1.12, wired in §4 (see §8.1).
- HE-1 default tables → superseded by wiki-enriched seed; §4 keeps `combat_balance.py` for per-subtype accuracy. ✓ (confirmation line wanted — §8.1).
- HE-2 wiki scrape / DB-drift → audit trail only; data already merged PR-3. ✓ not load-bearing.
- HE-3/5e/5f formulas (distance, stacking, rocket curve, booster push, fill order) → §1.2/§1.3. ✓
- HE-5h built-in cloak + UNIQUE_EQUIP generalization → §1.7. ✓
- HE-5h "`Item.built_in` attribute is DEAD" audit → not mentioned in §1; combat won't touch it, so safe, but a one-liner in §1.7 would prevent re-litigation. ⚠️ very minor.
- HE-5j tick=10ms / clean-grid / no-rounding → §1.1. ✓
- HE-6 commodities → out of PR-4 scope; §4 records PRs A–E shipped. ✓
- HE-7 → promoted wholesale into §1. ✓
- HE-5a PvC-stalemate route reuse → §1.9 (with "verify at code time"). ✓

## 6. Disposition of each §7 finding

Menu: (a) fix in-place in §1 · (b) add to §6 · (c) add to §8 to-do · (d) defer.

| §7 finding | Severity in §7 | Reality | Proposed disposition |
|---|---|---|---|
| Repair-Bot rate: §1.3 % vs seed flat `HPps` 7/15 | 🔴 Critical | **STALE.** §6 C1 = ✅ RESOLVED, Entry 7 #24 locks percentage-of-max, and §1.3 already states "seed `HPps` 7/15 are stale data — ignore." §7 compared spec to raw seed without crediting that clause. Verified seed still holds 7/15. | **(d) defer/close — NOT blocking.** Already resolved in-spec. |
| Built-in modules text outdated (§1.7) | ⚠️ Cosmetic | Already current: §1.7 Cloaks reads "verified 2026-05-30 … §6 C4 RESOLVED"; §6 C4 = ✅. Verified both ship JSONs hold `builtinModules:["U'tool"]`, no snake_case dup. | **(a)-equivalent already present — no action.** |
| Khador/Rhoda type-name swap (§1.7) | ⚠️ Cosmetic | Already current: §1.7 reads `JumpDriveModule (Khador Drive)` / `TimeExtenderModule (Rhoda Vortex)`. Verified seed: Khador=`JumpDriveModule`, Rhoda=`TimeExtenderModule`. | **(a)-equivalent already present — no action.** |

## 7. Improvements (non-blocking)

1. **§1.0 Glossary** — tick, layer, track, tier, window, threshold, debuff, modifier, regen pulse, activation, cooldown, in-flight projectile.
2. ✅ **DONE (this pass): Cross-reference / ID legend at top of §2** mapping the three schemes; former §6 O2 merged into §2 O-DP; §6 renamed `C1–C9` (§8.3).
3. ✅ **DONE (this pass): combat-log output format** — `FightResults.combat_log`/`CombatEvent` two-tier design + wire-compat now in §1.12 / §4 (§8.1). Remaining §4 nice-to-haves: `manual_turret_mode: bool` on `ShipLoadout`; shock-blast reset hook; missile/rocket dispatch; explicit Phase-2 column names (§8.4).
4. **TOC / anchor links** at file top (§1–§8) — file is 2200+ lines.
5. ✅ **DONE (this pass): §6 progress marked in place** — items kept as a `C1–C9` disposition log (RESOLVED / CONFIRMED / MOVED) rather than deleted, preserving history.

## Summary

- ✓ **No blocking items.** Correcting the prior §8: §6 C1 (repair-bot rate) is RESOLVED, and §7's lone 🔴 is stale relative to §1.3's explicit "ignore seed `HPps`" clause. §1 is implementable as written.
- ✅ **Combat-log output format RESOLVED (this pass):** §1.12 specifies an inline Tier-0 summary + `combat_log_id`, with the full event-tick timeline persisted to a new `combat_log` table (72 h retention) and lifetime metrics promoted onto `Player`; §4 splits the work into PR-4 (in-memory production + Player stat promotion) + PR-5 (persistence/retention). Discord rendering deferred to a later cycle. Was the prior "highest-value miss."
- ⚠️ **Thin pointers:** Phase-2 schema column names and the `manual_turret_mode` flag exist only in Historical Entries / prose; surface them in §4.
- ✓ **Reorg integrity sound:** §1 = read-first spec, §2 = sole open-question registry, §6 = closed disposition log, §7 = data snapshot, HE = audit trail.
- All §7 cosmetic findings already reflected in current §1.7 text; §7 prose left verbatim as a dated snapshot.

---

*Last updated: 2026-05-30 (§8 superseded prior in-tree review; open-question IDs consolidated — §2 canonical `O-*`, §6 relabelled `C1–C9`; combat-log data model added as §1.12 — `combat_log` table w/ full event-tick timeline + inline summary + Player stat promotion; §4 PR-4/PR-5 wiring; new §2 O-LOG / O-STAT. Discord visualization deferred to a later cycle.)*
