# Combat System Rewrite — Journal

> Working memory for the effort to replace `services/bot-core/src/services/combat_service.py`
> and related code with a proper combat system.
> Started: 2026-05-24

---

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

*Last updated: 2026-05-24*
