# Combat-System Phase-1 — Implementation TODO

This is the **scoped task list** for implementing the Phase-1 combat system
described in `/proj/COMBAT_SPEC_LOCKED.md`. Each task below is bounded for a
single GPT-4o implementation pass (~5–9h).

**This file scopes tasks. It does NOT define them.** The decomposer who picks
this up will write the full per-task spec (acceptance criteria, file lists,
test plans, etc.) using `COMBAT_SPEC_LOCKED.md` as the primary source of
truth. Do not duplicate spec content here.

---

## Tasks

### T1 — Foundation: constants + model surfaces + balance hooks
- All 26 Appendix A constants in `game_constants.py` (env + per-guild override)
- `ShipLoadout.manual_turret_mode: bool = False` field (in-memory dataclass)
- `LoadoutBuilder.from_player()` + `from_criminal_ship()` surface the new field
- `CombatEvent` dataclass + event-type literals
- `SUBTYPE_ACCURACY_MOD: dict[str, float]` empty hook + `weapon_accuracy()` passthrough in `combat_balance.py`
- Drop unused `WeaponStats.accuracy_modifier` (multiplicative, never populated); keep `ModuleStats.accuracy_modifier`

### T2 — Schema migrations + `CombatLogRepository` skeleton
- Single Alembic migration covering:
  - 3 new `Player` columns (`total_fights`, `total_nukes_fired`, `total_module_activations`)
  - Full `combat_log` table per §12 schema + indexes on `combatant1_user_id` and `combatant2_user_id`
  - `PlayerShip` persistence for `manual_turret_mode`
- `CombatLogRepository` with CRUD only (no service, no retention wiring yet)

### T3 — Resolver core: tick loop + distance + HP + regen + DR
- Tick step framework executing Appendix B phases in order, C1-before-C2 within phase
- Combatant init (cooldowns = 0, regen accumulators dormant at max HP)
- Distance closure / floor / Appendix B step 8 termination (outcome × reason matrix)
- shield → armour → hull damage stacking
- Keith T. Maxwell DR as first scaler in damage application
- Shield + Repair Bot integer-flush regen schedules with dormancy semantics
- Emits: `fight_start`, `fight_end`, `regen`, `damage`, `layer_depleted`, `distance`
- **No weapons, no modules yet.** Resolver runs end-to-end as a 5000 m drift to floor with no firing.

### T4 — Accuracy system + scanner tier resolution
- Layered formula + `[0.05, 0.99]` clamp
- `pilot_primary_acc` vs `pilot_turret_acc` split (turret variant excludes thruster bonus)
- Cloak set-value override surface (module mechanics in T8; this task lands the math hook)
- Thruster ramp function and booster-debuff function (modules in T8; functions ready to call)
- Scanner tier resolution (A/B/C → accuracy pp bonus + missile-behavior switch)
- Deterministic-RNG tests for the math

### T5 — Primary weapons + PrimaryWeaponMod
- Per-weapon cooldown decrement, range gate, accuracy roll, damage emission via T3 stacking
- PrimaryWeaponMod (unique-equip, primaries-only) — `damage_pct` / `fire_rate_pct`, with `effective_loading_speed_ms` snapping to `TICK_MS`
- Emits: `weapon_fire`, `cooldown_end`
- First "things actually fire" milestone

### T6 — Secondary weapons: all 5 subtypes
- Rocket accuracy curve
- Missile scanner-tier branch (Tier A → rocket curve; Tier B/C → §5 layered)
- Cluster missile: burst loop, fire-time accuracy snapshot, per-sub-munition independent rolls, overkill allowed, condensed single-row log event
- Nuke: epicenter sample, both-sides falloff, friendly factor on self-damage, self-damage routed through DR
- Shock-blast distance reset (no damage, 100% guaranteed)

### T7 — Turret weapons: auto + manual + mode switch
- Auto: `pilot_turret_acc × AUTO_TURRET_ACCURACY_MULTIPLIER`, parallel to primaries, one accuracy shared across all auto turrets on a ship
- Manual: `manual_turret_mode` flag swaps between primaries-fire and manual-turrets-fire branches
- Per-turret independent cooldowns at `pilot_primary_acc` (manual)
- Plasma-collector turret loads but does nothing

### T8 — Activation-rule modules: cloak + booster + thruster
- Universal HP-threshold trigger rule (off-cooldown only; skipped if cooling; cooldown timer starts at effect expiry)
- Cloak: thresholds [66, 33], built-in U'tool supersession per §10
- Booster: thresholds [80, 60, 40, 20], distance push + accuracy debuff during window, suspend passive closure during boost
- Thruster: passive ramp evaluated every tick, primaries-only (no activation event)
- Emits: `module_activation` (cloak + booster only; thruster is passive)

### T9 — EmergencySystem + inert modules + timeline emission + summary builder
- EmergencySystem: Appendix B step 4a trigger, clamp hull to 1, 10 s full-invuln window, consumable
- Inert modules (§7.9: GammaShield, plasma scanner, JumpDrive, etc.) — load but no combat effect
- All remaining §12 event types emitted in Appendix B phase order × C1-before-C2
- Per-combatant summary builder per §12 schema
- Populate `FightResults.combat_log` (timeline) and `FightResults.metadata` (summary)

### T10 — Persistence + `fight_ships` cutover + Player stats + retire `SimpleTTKResolver`
- `CombatLogService.persist(combat_meta, fight_results, context)`
- Extend `db_retention_default` executor with `CombatLogRepository.delete_older_than(cutoff)`; wire `BOUNTYBOT_COMBAT_LOG_RETENTION_HOURS`
- New `fight_ships(...)` signature: `context: str | None`, `log_result: bool = True`, `pvc_damage_reduction: float = 0.0`
- Boundary validation: `log_result=True ∧ context is None` → raise
- Internal post-fight (when `log_result=True`): persist log + Player stat increments (`total_fights`, `total_nukes_fired`, `total_module_activations`) for human combatants only
- Update all 5 callsites: `duel_service.accept_duel`, `bounty_service.check_system` (Silver+ and Bronze paths), `bounties.POST /bounties/combat-bonus`, `combat_preflight_service`
- Delete `SimpleTTKResolver`, `BOUNTY_PVC_ARMOUR_BUFF_FACTOR`, `player_armour_buff` parameter, `DUEL_VARIANCE_PERCENT`, `variance_percent` parameter
- Keep legacy `FightStats` (`raw_hp` / `varied_hp` / `raw_dps` / `varied_dps` / `ttk`) populated for wire-compat per §12

### T11 — Item-detail embed enrichment (§14)
- bot-core schemas: surface `emp_damage`, `burst_count`, nuke effective magnitude (= `magnitude_m × NUKE_MAGNITUDE_SCALE`), PrimaryWeaponMod (`damage_pct`, `fire_rate_pct`, `dpsMultiplier`) as explicit response fields
- discord-gateway cogs: render new fields on item-detail embeds with disambiguation labels and nuke self-damage warning
- Touches both services (bot-core schema + gateway cog)

---

## Dependency graph

```
T1 ─┐
    ├─→ T3 ─→ T4 ─→ T5 ─→ T6 ─→ T7 ─→ T8 ─→ T9 ─→ T10
T2 ─┘                                                 ▲
                                                      │
                                                  (T2 also feeds T10
                                                   for combat_log persist)

T11 ─ independent, can land at any point
```

- **T1, T2** are independent of each other; both must land before T3.
  - Strictly, T3 only consumes T1 (constants + dataclasses). T2 is consumed by T10. Doing T2 early lets the migration soak in dev while resolver work proceeds.
- **T3 → T4 → T5** is hard sequential (each layer consumes the previous).
- **T6, T7, T8, T9** can in principle be parallelised after T5, but the recommended order is sequential (T6 → T7 → T8 → T9) so each task is implemented while the resolver context is freshest. T9 depends on T8 (module_activation event types).
- **T9 → T10**. T10 needs the full summary + timeline emission shape from T9.
- **T11** is fully independent of the resolver chain — pick up whenever convenient.

---

## Notes for the decomposer

- **Primary source:** `/proj/COMBAT_SPEC_LOCKED.md` is the locked spec. Every mechanic, formula, default value, and naming convention is canonical there. Do not invent.
- **Symbol convention:** `UPPERCASE_SNAKE_CASE` = configurable knob (lives in `GameConstants`, overridable via `BOUNTYBOT_<NAME>` + per-guild). `lowercase_snake_case` = runtime state or per-item seed-data attribute. See spec §0.3.
- **Appendix B step order is canonical.** Per-tick phase ordering (1 cooldowns → 2 regen → 3 fire eval → 4 damage → 4a ES → 4b clamp → 5 HP-threshold → 6 distance → 7 events → 8 termination) MUST be preserved. Tasks T3, T5, T8, T9 all touch this order; the decomposer should call this out as a guardrail in each affected task.
- **Loadout validation is NOT a combat-resolver concern** (§10 "Loadout contract"). Tasks should consume `ShipLoadout` as-is and trust the builder.
- **Phase-2+ items are explicitly out of scope** for every task. The §7.10 deferred modules, EMP mechanic, damaged-start state, and ionizing-missile / mine / sentry-gun / emp-bomb subtypes are NOT to be implemented. The resolver may need to *load* the seed data for fidelity (per §4 and §7.9) but apply no combat effect.
- **GPT-4o risk areas:** T3, T6, T10 are the cross-cutting ones. Per-task spec for these should include a "stay inside these files" guardrail and reference the relevant spec § directly. T1, T2, T11 are largely mechanical.
- **Don't over-spec.** The implementer will have full spec context. The decomposer's job is to write actionable per-task specs (entry conditions, deliverables, test surface, acceptance criteria) — not to copy the spec into each task.

---

*Decomposition authored: 2026-06-01. Reviewed by `researcher` subagent — verdict: sound, no concerns.*
