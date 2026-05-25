# COMBAT_SCHEMA_MAPPING — Phase-1 Wiki Ingest → DB Mapping

> Author: architect agent
> Date: 2026-05-25
> Scope: design-only. No code or migrations modified.
> Inputs cross-referenced: `COMBAT_REWRITE_JOURNAL.md` (Entries 0–5h), `.combat-rewrite-wiki-v2/` (211 items + `_summary.md`), `services/bot-core/src/persist/models/`, `services/bot-core/src/persist/repositories/`, `services/bot-core/import_data/`, `loadout_builder.py`, last 4 Alembic revisions.

This document is a **schema-mapping** report for ingesting the wiki v2 scrape into the bot-core catalog so the new tick-based combat sim has the data it needs. It is divided per object category. The proposal favours `extra_atts` JSON over new first-class columns wherever a field is not queried/filtered, in line with journal Entry 4 and the project's preference for low migration churn.

---

## 0. Cross-cutting Findings

### 0.1 Subtype / `type` discriminator inventory

The journal calls out a *"drift"* between wiki `weapon_subtype` and seed `subtype`. Direct sampling shows the two vocabularies are isomorphic, only the spelling/separator differs:

| Wiki `weapon_subtype` | Seed `subtype` (primary/secondary/turret) | Canonical (proposed) |
|---|---|---|
| `auto-cannon` | `auto_cannons` | `auto_cannon` |
| `laser` | `blaster_lasers` (11) | `blaster_laser` *(see §3 note)* |
| `beam-laser` | `beam_lasers` | `beam_laser` |
| `blaster` | `blasters` | `blaster` |
| `emp-blaster` | `emp_blasters` | `emp_blaster` |
| `scatter-gun` | `scatter_guns` | `scatter_gun` |
| `thermal-fusion` | `thermal_fusion_cannons` | `thermal_fusion` |
| `rocket` | `rockets` | `rocket` |
| `missile` | `missiles` | `missile` |
| `cluster-missile` | `cluster_missiles` | `cluster_missile` |
| `ionizing-missile` | `ionizing_missiles` | `ionizing_missile` |
| `emp-bomb` | `emp_bombs` | `emp_bomb` |
| `nuke` | `nukes` | `nuke` |
| `mine` | `mines` | `mine` |
| `sentry-gun` | `sentry_guns` | `sentry_gun` |
| `shock-blast` | `misc` (1 row: Shock Blast) | `shock_blast` |
| `Manual Turret` (raw) | `manual` | `manual_turret` |
| `Automatic Turret` (raw) | `auto` | `auto_turret` |
| `plasma-collector` | `plasma_collectors` | `plasma_collector` |

**Recommendation**: introduce a single canonical-subtype string set, snake_case singular. Materialise as a `WeaponSubtype`/`TurretSubtype` enum in `services/combat_balance.py` (NOT a DB enum — STI discriminator stays on `Item.type`, subtype stays in `extra_atts`). The merge step writes the canonical form to `extra_atts.subtype`, leaving the legacy `extra_atts.subtype_legacy` alongside it for one release in case something downstream still grepped for `"auto_cannons"`. Audit shows nothing in `services/`, `cogs/`, or routers reads weapon subtype today — it's only displayed by `aboutCog` if at all.

Module STI discriminators (`Item.type` values like `ArmourModule`, `ShieldModule`) already exist on every seed JSON and are stable. Wiki `raw_infobox.Type` ("Armor", "Shield", "Booster", etc.) is the human-readable label and should not become the DB discriminator. Mapping wiki→STI is straightforward and 1:1 — see §2.5.

### 0.2 Loader / repo posture

- **`ShipRepository`** writes every JSON key to a model attribute via camelCase→snake_case mapping. There is **no `extra_atts` column on Ship**. Unknown keys would crash the model constructor on insert.
- **`Module/PrimaryWeapon/SecondaryWeapon/TurretWeapon repos`** pull a known-keys set into model columns, then dump the rest into `extra_atts: JSON`. New wiki keys land there automatically with **no schema change**.
- **`CriminalRepository`** uses a `setattr` loop with a small camel→snake mapping; any unknown key triggers an `AttributeError`. New fields must be either snake_cased to match an existing column or routed into a new column. No `extra_atts` column exists on Criminal.

**This asymmetry is the central architectural reality of this report**:

| Model | Has `extra_atts`? | New wiki fields require... |
|---|---|---|
| `PrimaryWeapon` (via Weapon) | ✓ | Just merge into JSON. Zero migration. |
| `SecondaryWeapon` (via Weapon) | ✓ | Same. |
| `TurretWeapon` (via Weapon) | ✓ | Same. |
| `Module` | ✓ | Same. |
| `Ship` | ✗ | **Either add `extra_atts: JSON` column OR add per-field columns.** |
| `Criminal` | ✗ | None proposed for Phase-1 (criminals stay in seed-JSON as-is, see §6). |

### 0.3 Wiki v2 coverage holes

- **Vossk Battlecruiser**: empty wiki infobox — keep seed JSON as truth.
- **4 Signature modules**: shared wiki page, no per-variant stats — irrelevant to combat.
- **4 Freighters**: shared page, no infobox — already excluded by `max_primaries=0`.
- **Repair-bot HPps**: wiki has no field. Journal Entry 3 locks defaults (2.5%/sec, 5.0%/sec of max_hull+max_armour). Loader must derive a synthetic `hps_pct_per_sec` from the module name pattern.
- **All other 207 items have full infobox stats.**

### 0.4 Bias / philosophy

Per Entry 4's bias toward minimum migration churn:

- **Default: stash in `extra_atts`.**
- **Promote to column only if** the field is filtered/sorted/joined in queries, constrained by NOT NULL, or part of a frequently-rendered embed where JSON probing is awkward.
- **Phase-2 player/bounty current-* damage columns are first-class** because they are mutated per-tick by combat outcomes and read by every combat call.

---

## 1. SHIP

### A. Current schema

| Column | Type | Nullable |
|---|---|---|
| `id` | Integer PK | No |
| `name` | String UNIQUE | No |
| `aliases` | ARRAY(String) | Yes |
| `armour` | Integer | No |
| `built_in` | Boolean (DEAD per journal 5h) | default False |
| `cargo` | Integer | No |
| `compatible_skins` | JSON | Yes |
| `emoji` | String | Yes |
| `icon` | String | Yes |
| `manufacturer` | String | No |
| `handling` | Integer | No |
| `shop_spawn_rate` | Float | Yes |
| `skinnable` | Boolean | default False |
| `max_modules` | Integer | No |
| `max_primaries` | Integer | No |
| `max_secondaries` | Integer | No |
| `max_turrets` | Integer | No |
| `builtin_modules` | ARRAY(String) | Yes |
| `texture_regions` | Integer | No |
| `save_due` | Boolean | default False |
| `model` | String | Yes |
| `norm_spec` | String | Yes |
| `value` | Integer | No |
| `wiki` | String | Yes |
| `assets` | ARRAY(String) | Yes |

**No `extra_atts` column.**

Seed JSON keys (Betty, H'Soc, Scimitar samples): `aliases`, `armour`, `builtIn`, `cargo`, `compatibleSkins`, `emoji`, `handling`, `icon`, `manufacturer`, `maxModules`, `maxPrimaries`, `maxSecondaries`, `maxTurrets`, `model`, `name`, `saveDue`, `shopSpawnRate`, `skinnable`, `textureRegions`, `value`, `wiki`, `assets`, `normSpec`. Note: `builtinModules` is **never populated** in any current seed JSON (column exists, value is NULL for all 65 rows).

Loader: `ShipRepository.create_or_update` does a single-pass setattr with a camelCase→snake_case mapping (`builtIn→built_in`, `compatibleSkins→compatible_skins`, `shopSpawnRate→shop_spawn_rate`, etc.). **Any unrecognised JSON key would land as a raw `setattr` with the *lowercased* key**, which would (a) succeed if the column exists, (b) raise on insert if the column doesn't. This is the loader-side reason the Ship migration cannot be deferred — we cannot just dump new keys into the seed without either adding columns or routing them somewhere.

### B. Wiki v2 data inventory

Sampled Betty and Scimitar JSONs. `stats` shape:

| Stat | Frequency | Notes |
|---|---|---|
| `faction` | 65/65 | Already covered by `manufacturer`; verify naming. |
| `armour` | 64/65 | Vossk Battlecruiser missing. |
| `cargo` | 64/65 | Same. |
| `max_primaries` | 64/65 | Same. |
| `max_secondaries` | 64/65 | Same. |
| `max_turrets` | 64/65 | Same. |
| `max_modules` | 64/65 | Same. |
| `handling` | 64/65 | Same. |
| `price_credits` | 65/65 | PC price; today's `value` column. |
| `price_credits_android` | 65/65 | GoF2 HD price (often different). |
| `dlc` | ~10 | Supernova/Valkyrie tagging. |

Combat-relevant wiki additions beyond existing columns:

- **`dlc`** — for future PvE balance segregation or shop tier-gating.
- **`price_credits_android`** — informational; only relevant if we ever want to surface GoF2 HD prices.
- **builtinModules implication** (Entry 5h) — Scimitar + Specter need `builtinModules: ["U'tool"]` populated. Wiki JSONs say so in `mechanics_text`, NOT in `stats`. Enrichment step has to **derive** this from the prose, not pull a field.

### C. New schema proposal

**Recommendation: ADD a single `extra_atts: JSON` column on Ship.** Justification:

1. The journal's bias (Entry 4) is toward `extra_atts`-over-new-columns.
2. Ship has the second-most additive fields in this scrape (dlc, android price, faction-tag) and is the only catalog model lacking the `extra_atts` escape hatch — all of its sibling tables (Weapon/Module subtypes) have it. Adding it brings Ship into line with the existing pattern.
3. The handful of fields we're considering (dlc tag, android price, future fields) are not queried/filtered in any existing service. They're presentational.
4. New columns can always be carved out of `extra_atts` later (zero-data-loss migration) if a future query needs an index. The reverse (column→JSON) is harder.

**No new first-class combat columns are needed on Ship.** Combat-relevant stats (`armour`, `handling`, `max_*`, `builtin_modules`) **already exist** as columns. The combat sim consumes:
- `armour` → hull HP (locked, Entry 3)
- `handling` → close-quarters thruster scaling baseline (locked, Entry 5f)
- `max_*` → slot caps
- `builtin_modules` → equip-priority rule (locked, Entry 5h)

`base_speed_ms` and `min_distance_m` etc. (from Entry 5e) are **per-fight constants**, not per-ship — they live in `combat_balance.py`, not the DB.

**Builtin-cloak enrichment**: rather than scraping prose, the wiki has TWO ships with mechanics_text mentioning U'tool: Scimitar + Specter (confirmed by journal 5h). Hardcode this in the merge script as a deterministic override:

```python
BUILTIN_CLOAK_SHIPS = {"Scimitar", "Specter"}  # Both pre-installed U'tool (Supernova DLC)
# In merge step:
if ship.name in BUILTIN_CLOAK_SHIPS:
    ship_dict["builtinModules"] = ["U'tool"]
```

The seed JSON field name remains `builtinModules` (the camel form ShipRepository already maps to `builtin_modules`).

### D. Migrations + transformations

| Step | Description | Order |
|---|---|---|
| M1 | Alembic: `op.add_column("ship", sa.Column("extra_atts", postgresql.JSON, nullable=True, server_default="{}"))` | 1 |
| M2 | Alembic: backfill `extra_atts = '{}'` for existing rows (covered by server_default but explicit is safer) | 1 |
| M3 | Model: add `extra_atts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=True, default=dict)` to `Ship` | 1 |
| M4 | `ShipRepository.create_or_update`: stash unknown keys into `extra_atts`, mirroring the pattern in `module_repository.py`. Today the loader writes raw keys via setattr; we keep that for known columns but route unknowns into `extra_atts`. | 2 |
| D1 | Seed enrichment: wiki v2 → `import_data/ship/*.json`. Add `dlc`, `factionTag` (if different from manufacturer), `priceCreditsAndroid`. Drop wiki price into `extra_atts.price_credits_android`. Preserve existing values where wiki has none (Vossk BC, see §6). | 3 |
| D2 | Seed enrichment: add `builtinModules: ["U'tool"]` to `nivelian.scimitar.json` and `nivelian.specter.json`. | 3 |
| D3 | Production backfill: `import_data/` updates only fire `create_or_update` if the seeder is re-run. Auto-seeder skips populated tables. **A one-shot reseed pass** (manual `python -m utils.data_loader load_data ship` after the migration ships) is required for prod. The repo's `create_or_update` does an UPSERT by name, so existing rows pick up new fields and `extra_atts`. | 4 |

### E. Risks + open questions

- **R**: Ship has no `extra_atts` today and 65 rows exist in prod. Migration is a single `add_column` with nullable + default — low risk. The model needs the column added in lockstep with the migration revision or the seeder will fail mid-flight on the loader changes.
- **OQ**: Does the user want `dlc` to become a first-class filterable column? Today no router/service filters by DLC. Recommendation: leave in `extra_atts` for now.
- **OQ**: Do we want both PC and Android prices? Today `value` = PC. Recommendation: keep `value` as PC, add `extra_atts.price_credits_android`. No business logic depends on it.

---

## 2. MODULE

### A. Current schema

`Module` (extends `Item` via STI):

| Column | Type | Nullable |
|---|---|---|
| `id` (FK → item.id) | Integer PK | No |
| `tech_level` | Integer | No |
| `max_equipped` | Integer | Yes |
| `extra_atts` | JSON | Yes (default `dict`) |

`Item.type` discriminator carries the module subtype string (`ArmourModule`, `ShieldModule`, etc.). **No `module_type` column.** 21 distinct STI values.

Seed JSON shape: keys depend on subtype. Common: `name`, `aliases`, `builtIn`, `emoji`, `icon`, `techLevel`, `type` (STI), `value`, `wiki`. Subtype-specific keys (per loadout_builder.py and per-file sampling):

| Module subtype (Item.type) | Seed keys today | Notes |
|---|---|---|
| `ArmourModule` (×5) | `armour` | Plain int. |
| `ShieldModule` (×6) | `shield` | Plain int. Wiki adds `shield_recharge_ms`. |
| `BoosterModule` (×5) | `duration`, `effect` | `effect` = multiplier (e.g. 1.6). Wiki: `effect_pct`, `effect_multiplier`, `duration_ms`, `loading_speed_ms`. |
| `ThrusterModule` (×5) | `handlingMultiplier` | Wiki: `effect_pct`, `handling_multiplier`. |
| `CloakModule` (×3) | `duration` | Wiki: `duration_ms`, `loading_speed_ms`, `energy_per_use`. |
| `ScannerModule` (×4) | `timeToLock`, `showCargo`, `showClassAAsteroids` | Wiki: `time_to_lock_s`. |
| `RepairBotModule` (×2) | `HPps` (Bot I = 7, II = 15) | **Stale data** — Entry 3 supersedes with %/sec defaults. Keep in JSON but resolver ignores. |
| `EmergencySystemModule` (×1) | (none in seed beyond common) | Wiki: `duration_ms`, `consumable: true`. |
| `GammaShieldModule` (×2) | `effect` | Inert in Phase-1 (Entry 4 lock). |
| `CompressorModule` (×5) | `cargoMultiplier` | Non-combat. |
| `MiningDrillModule` (×5) | `drillHandling`, `oreYield` | Non-combat. |
| `CabinModule` (×3) | `cabinSize` | Non-combat. |
| `JumpDriveModule` (×1) | — | Non-combat. |
| `SignatureModule` (×4) | `manufacturer` | Non-combat. |
| `SpectralFilterModule` (×3) | `showInfo`, `showOnRadar` | Non-combat. |
| `TractorBeamModule` (×4) | `timeToLock` | Non-combat. |
| `PrimaryWeaponModModule` (×2) | `dpsMultiplier` (1.1) | Combat passive. Wiki: same. |
| `ShieldInjectorModule` (×1) | `plasmaConsumption` (30) | Combat (Phoenix SIS). Wiki: same. |
| `RepairBeamModule` (×2) | `count`, `effect` | Combat active (PvE healing). |
| `TransfusionBeamModule` (×2) | `count`, `effect` | Combat active. |
| `TimeExtenderModule` (×1) | (varies) | Out of scope (Rhoda Vortex). |

Loader: `ModuleRepository.create_or_update` whitelists `name`/`aliases`/`builtIn`/`emoji`/`icon`/`value`/`wiki`/`type` to Item; `techLevel`/`maxEquipped` to Module. **Everything else lands in `extra_atts`** (raw camelCase preserved). This is the cleanest loader in the codebase for adding fields.

`loadout_builder._module_stats_from_extra` (lines 43–77) reads `extra_atts` via a snake-or-camel fallback (`_get_extra`). It currently extracts only `armour`/`shield`/`dps` and their multipliers. The new tick resolver reads from the same dict, so adding wiki keys to JSON is non-breaking.

### B. Wiki v2 data inventory

| Wiki Type (×count) | Wiki stats keys (combat-relevant) |
|---|---|
| Shield (6) | `shield_capacity` (matches seed `shield`), `shield_recharge_ms` |
| Armor (5) | `armour` (matches) |
| Booster (5) | `effect_pct`, `effect_multiplier`, `loading_speed_ms`, `duration_ms` |
| Thruster (5) | `effect_pct`, `handling_multiplier` |
| Cloak (3) | `duration_ms`, `loading_speed_ms`, `energy_per_use` |
| Scanner (4) | `time_to_lock_s`, `shows_class_a_asteroids`, `shows_cargo` |
| Repair Bot (2) | (no HPps in wiki — defaults from Entry 3) |
| Emergency System (1) | `duration_ms`, `consumable: true`, `dlc` |
| Gamma Shield (2) | `effect_pct` |
| Weapon Mod (2) | `dps_multiplier` (matches `dpsMultiplier`) |
| Shield Injector (1) | `plasma_consumption_per_use` |
| Time Extender (1) | `duration_ms`, `time_dilation_factor` |
| Repair Beam, Transfusion Beam, Tractor Beam, Cabin, Compressor, Spectral Filter, Mining Drill, Signature, Jump Drive | Various non-combat / out-of-Phase-1 |

Distribution check: every module type has wiki coverage except the partial Signature (single shared page) and Freighter (not a module). 62/66 modules are fully scraped (4 Signature partials).

### C. New schema proposal

**No new columns on `Module`. All new combat fields stay in `extra_atts`.**

Justification:
- `Module.extra_atts` already exists, already consumed by `loadout_builder`.
- Phase-1 resolver wants to read `duration_ms`, `loading_speed_ms`, `effect_pct`, `effect_multiplier`, `shield_capacity`, `shield_recharge_ms`, `time_to_lock_s`, `handling_multiplier`. None of these are queried/filtered/sorted by services. They are read by-name from a single instance during loadout build.
- Repair-bot rate: not in wiki. The merge step should inject a synthetic key derived from the module name:

```python
REPAIR_BOT_HPS_PCT_PER_SEC = {
    "Ketar Repair Bot":    0.025,  # 2.5%/sec
    "Ketar Repair Bot II": 0.050,  # 5.0%/sec
}
# Seed: extra_atts.hps_pct_per_sec = 0.025  (etc.)
```

**STI discriminator reconciliation**: NONE NEEDED for modules. The seed JSON already carries the canonical STI string (`ArmourModule`, `ShieldModule`, ...). The wiki `raw_infobox.Type` is the human-readable label and is *not* used for STI. Mapping is implicit and 1:1; merger does not need to touch `Item.type`.

### D. Migrations + transformations

| Step | Description | Order |
|---|---|---|
| **No DB schema change for Module.** | — | — |
| D1 | Seed enrichment: for each module, write into JSON the wiki keys above. Suggested layout: keep the legacy seed key (e.g. `effect`, `duration`) **AND** add the wiki snake_case key (`effect_pct`, `duration_ms`). `_module_stats_from_extra._get_extra` already handles snake-or-camel, so resolver and legacy code coexist for one release. | 2 |
| D2 | Seed enrichment: add synthetic `hps_pct_per_sec` to both Ketar Repair Bot JSONs. | 2 |
| L1 | Loader: no change. `ModuleRepository` already routes unknown keys to `extra_atts`. | — |
| L2 | New consumer code (combat resolver) reads from `extra_atts` using the same snake-or-camel fallback as `_module_stats_from_extra`. | 5 |

### E. Risks + open questions

- **R**: `_module_stats_from_extra` only consults a fixed list (armour/shield/dps + multipliers). The new resolver must use its own reader. Don't centralise prematurely — let it diverge until the API stabilises.
- **OQ**: The legacy `duration` key on Booster JSONs is in *seconds*, while wiki `duration_ms` is in milliseconds. Loader does not normalise. Recommendation: enrichment script writes both; resolver reads `duration_ms` as canonical. After one release, strip the legacy key.
- **OQ**: ShieldModule `shield` (seed) vs `shield_capacity` (wiki) — semantically identical. Keep `shield` for back-compat, add `shield_capacity` for new readers, plan deprecation.

---

## 3. PRIMARY WEAPON

### A. Current schema

`PrimaryWeapon` (STI: Item → Weapon → PrimaryWeapon):

| Column | Type | Nullable |
|---|---|---|
| `id` (FK → weapon.id) | Integer PK | No |
| `dps` | Float | No |
| inherited `tech_level` | Integer | Yes |
| inherited `extra_atts` | JSON | Yes |
| inherited Item fields | — | — |

Seed JSON (sample 64MJ Railgun): `aliases`, `builtIn`, `dps`, `emoji`, `icon`, `name`, `techLevel`, `value`, `wiki`, `type` (`"PrimaryWeapon"`), `subtype` (`"auto_cannons"`).

Loader: `PrimaryWeaponRepository.create_or_update` whitelists `dps` + Item/Weapon fields, routes everything else (`subtype`) into `extra_atts`. New wiki keys go to `extra_atts` for free.

### B. Wiki v2 data inventory

Per-primary `stats` keys (verified on 64MJ Railgun):

| Stat | Coverage | Combat use |
|---|---|---|
| `tech_level` | 40/40 | Already a column. |
| `damage_per_shot` | 40/40 | **CRITICAL** — drives per-shot damage in tick sim. |
| `loading_speed_ms` | 40/40 | **CRITICAL** — fire rate; drives `interval_ticks`. |
| `dps` | 40/40 | Already a column. Sanity-check vs `damage_per_shot × 1000/loading_speed_ms`. |
| `range_m` | 40/40 | **CRITICAL** — range-gating (Entry 5d). |
| `projectile_speed_kmh` | 40/40 | Used for travel-time effects (later phase, but cheap to carry). |
| `weapon_subtype` | 40/40 | Canonical subtype (see §0.1). |
| `price_range_min_credits` / `_max_credits` | 40/40 | Informational. |

**Subtype distribution (40 primaries)**: 11 laser, 8 blaster, 7 auto-cannon, 4 beam-laser, 4 thermal-fusion, 3 scatter-gun, 3 emp-blaster.

**Note on `laser` vs `blaster_laser` mismatch**: wiki lumps 11 items under `weapon_subtype="laser"` while seed JSON splits them into `blaster_lasers` (11 entries). Cross-check shows these are the **same 11 items** — wiki's `laser` and seed's `blaster_lasers` are interchangeable labels. Canonical (recommended): `blaster_laser` for these 11. Wiki `laser` rows get remapped to `blaster_laser` in the merge step.

### C. New schema proposal

**No new columns on `PrimaryWeapon`.** All new fields go to `extra_atts`. Justified:

- `damage_per_shot` and `loading_speed_ms` are read once per loadout-build, not filtered.
- `range_m` similarly: only consumed inside the resolver for range-gating.
- Subtype lives in `extra_atts.subtype` (canonical form, see §0.1).
- DPS sanity check: wiki `dps` vs `damage_per_shot × 1000 / loading_speed_ms` agree to 2 decimal places on every sampled primary. The seed `dps` column stays canonical (it's already populated, validated, and queried).

**Subtype reconciliation** (proposed canonical set, snake_case singular, written to `extra_atts.subtype`):
`auto_cannon`, `blaster`, `blaster_laser`, `beam_laser`, `emp_blaster`, `scatter_gun`, `thermal_fusion`.

### D. Migrations + transformations

| Step | Description | Order |
|---|---|---|
| **No DB schema change for PrimaryWeapon.** | — | — |
| D1 | Seed enrichment: add `damage_per_shot`, `loading_speed_ms`, `range_m`, `projectile_speed_kmh`, `subtype` (canonical), `subtype_legacy` (preserve current value). | 2 |
| D2 | Verify wiki `tech_level` vs seed `techLevel` for the two known drifts (128MJ Railgun, H'Belam). Per journal 5: wiki infobox is authoritative → bump both to TL 6 in seed. | 2 |
| L1 | No loader change. | — |

### E. Risks + open questions

- **R**: `tech_level` bumps for 128MJ and H'Belam may shift their shop-availability tier band. Bronze-tier players may lose access. Confirm with user before flipping. *(Flag for user — not a journal-locked decision.)*
- **OQ**: Should we replace seed `dps` with wiki `dps` for the two near-matches (Micro Gun MK I 9.09 vs 9.9, Tyrfing Blaster 59.09 vs 59.9)? Journal Entry 2 says these are rounding artefacts — both report 9.09 in the v2 scrape. Recommendation: keep seed as-is.

---

## 4. SECONDARY WEAPON

### A. Current schema

`SecondaryWeapon`:

| Column | Type | Nullable |
|---|---|---|
| `id` (FK → weapon.id) | Integer PK | No |
| `damage` | Integer NOT NULL | No |
| `loading_speed` | Integer | Yes |
| inherited `tech_level`, `extra_atts` | — | — |

Seed JSON (Garuda-IV): `aliases`, `builtIn`, `damage` (0 — placeholder!), `icon`, `loading speed` (note the space), `name`, `value` (0!), `wiki`, `type` (`"SecondaryWeapon"`), `subtype`.

**MAJOR DATA HOLE**: 29/30 secondary JSONs have `damage: 0` and `loading speed: 0`. Only Shesha has nonzero damage (60). The wiki has full numbers for all 30. This is the single biggest enrichment win.

Loader: `SecondaryWeaponRepository` uses `raw.get("loadingSpeed")` (camelCase), but the JSON uses `"loading speed"` (space). **`loading_speed` is NULL in DB for all 30 rows**. The combat sim needs wiki data here regardless.

### B. Wiki v2 data inventory

Per-secondary `stats` keys:

| Stat | Coverage | Phase-1 in scope? | Notes |
|---|---|---|---|
| `tech_level` | 30/30 | ✓ | |
| `damage` | 30/30 | ✓ | Phase-1: direct-hit damage. |
| `loading_speed_ms` | 30/30 | ✓ | Cooldown. |
| `range_m` | 30/30 | ✓ | Range-gating; rockets use distance accuracy curve. |
| `projectile_speed_kmh` | 30/30 | Carry | Used by later mechanics. |
| `magnitude_m` | nukes (5), mines (3), sentry-guns (3), shock-blast (1) | ✓ for nukes | Inverse-square AoE radius (Entry 5c). |
| `steerable` | rockets (5), nukes (5) | ✓ | rockets dumb-fire, missiles tracking. |
| `emp_damage` | rockets and some others | Carry | Phase-2 — not in Phase-1 scope. |
| `weapon_subtype` | 30/30 | ✓ | See §0.1. |
| `dlc` | ~8 | Carry | |

**Subtype distribution (Phase-1 scope = rocket / missile / nuke per journal 5d)**:
- rocket: 5
- missile: 5
- nuke: 5
- (Phase-2: cluster-missile 3, ionizing-missile 2, emp-bomb 3, mine 3, sentry-gun 3, shock-blast 1)

The Phase-1 in-scope catalog is 15 items out of 30. The other 15 are scrubbed/enriched-but-inert in Phase-1.

### C. New schema proposal

**No new columns on `SecondaryWeapon`.** Wiki fields go to `extra_atts`. Two specific notes:

1. **Existing `damage: Integer NOT NULL`** column is currently uniformly 0 except Shesha. After enrichment, this column will carry real values for all 30. **Loader bug fix**: change `raw.get("loadingSpeed")` to `raw.get("loadingSpeed") or raw.get("loading speed")` AND on enrichment write the canonical `loadingSpeed` (camel, no space). Confirm both `damage` and `loading_speed` populate for all 30 after re-seed.

2. **Subtype reconciliation** — canonical set (snake_case singular):
   `rocket`, `missile`, `cluster_missile`, `ionizing_missile`, `emp_bomb`, `nuke`, `mine`, `sentry_gun`, `shock_blast`.
   Drop the lone `misc` (Shock Blast — re-tag to `shock_blast`).

3. **Nuke `magnitude_m`** drives the inverse-square AoE formula (Entry 5c). Stored as `extra_atts.magnitude_m`. No column needed.

4. **`steerable` (bool)** stored as `extra_atts.steerable`. Used by the resolver to branch dumb-fire (rocket) vs homing (missile).

### D. Migrations + transformations

| Step | Description | Order |
|---|---|---|
| **No DB schema change for SecondaryWeapon.** | — | — |
| D1 | Seed enrichment: write `damage`, `loadingSpeed` (canonical key), `range_m`, `projectile_speed_kmh`, `magnitude_m` (where applicable), `steerable`, `subtype` (canonical), `subtype_legacy`. | 2 |
| L1 | Loader fix: `raw.get("loadingSpeed") or raw.get("loading speed")` in `SecondaryWeaponRepository.create_or_update`. Drop the legacy space-key once seed is migrated. | 2 |
| L2 | Loader: also fix the `value: 0` problem — most secondaries have `price_range_min/max_credits` in wiki; set `value = price_range_max_credits` during enrichment. | 2 |

### E. Risks + open questions

- **R**: 30 secondaries currently sell for $0 (`value=0`). Any shop-listing logic that uses `value` for pricing will start charging real credits after the reseed. Confirm: this is desired. Audit `shop_service.py` references to `SecondaryWeapon.value` to confirm no surprises. *(Flag for user.)*
- **OQ**: Phase-1 explicitly excludes mines / sentry-guns / cluster-missiles / ionizing-missiles / emp-bombs / shock-blast (per journal 5d). Do we still enrich their JSONs with wiki data now (so Phase-2 is just a logic switch), or leave them at zero? Recommendation: enrich all 30 now. Schema is identical, JSON cost is negligible. The Phase-1 resolver skips by subtype.

---

## 5. TURRET WEAPON

### A. Current schema

`TurretWeapon`:

| Column | Type | Nullable |
|---|---|---|
| `id` (FK → weapon.id) | Integer PK | No |
| `dps` | Float NOT NULL | No |
| `automatic` | Boolean | default False |
| inherited `tech_level`, `extra_atts` | — | — |

Seed (Berger AGT 20mm): `aliases`, `builtIn`, `dps` (40), `emoji`, `icon`, `name`, `techLevel` (5), `value`, `automatic` (true), `wiki`, `type` (`"TurretWeapon"`), `subtype` (`"auto"`).

Loader: `TurretWeaponRepository` whitelists `dps`/`automatic` + Item/Weapon fields → `extra_atts` for rest. No issues.

### B. Wiki v2 data inventory

Per-turret `stats` keys (verified on Hammerhead D1):

| Stat | Coverage | Notes |
|---|---|---|
| `tech_level` | 10/10 | Column. |
| `damage_per_shot` | 7/10 | Plasma collectors have damage=0. |
| `loading_speed_ms` | 7/10 | |
| `dps` | 10/10 | Column. |
| `range_m` | 7/10 | |
| `projectile_speed_kmh` | 7/10 | |
| `handling` (turret rotation) | 7/10 | NEW — turret rotation speed, ignored in Phase-1. |
| `turret_autonomous` | 10/10 | Maps to `automatic` column. |
| `weapon_subtype` | 0/10 | **Not in `stats`** — only `raw_infobox.Type` ("Manual Turret"/"Automatic Turret"/"plasma-collector"). |

**Subtype reconciliation**: wiki uses `raw_infobox.Type` strings; seed uses `subtype` enum (`auto`/`manual`/`plasma_collectors`). Canonical:

| Wiki Type | Seed subtype | Canonical |
|---|---|---|
| "Automatic Turret" | `auto` | `auto_turret` |
| "Manual Turret" | `manual` | `manual_turret` |
| "plasma-collector" | `plasma_collectors` | `plasma_collector` |

Plasma collectors are non-combat (`damage_per_shot=0`, `dps=0`). They take a turret slot but are mining-only. Resolver should treat them as zero-DPS combat-passthrough (already correct).

### C. New schema proposal

**No new columns.** All wiki additions → `extra_atts`.

- `damage_per_shot`, `loading_speed_ms`, `range_m` in `extra_atts`.
- `turret_handling` (turret rotation) in `extra_atts` — Phase-2 candidate for "manual turret pilot-input penalty" — out of Phase-1 scope.
- `subtype` (canonical) in `extra_atts.subtype` to match the primary/secondary pattern.

The existing `automatic: Boolean` column stays — it's checked frequently by the new resolver (auto-turrets fire on their own; manual turrets need explicit handling).

### D. Migrations + transformations

| Step | Description | Order |
|---|---|---|
| **No DB schema change for TurretWeapon.** | — | — |
| D1 | Seed enrichment: add `damage_per_shot`, `loading_speed_ms`, `range_m`, `projectile_speed_kmh`, `turret_handling`, `subtype` (canonical), `subtype_legacy`. | 2 |
| L1 | No loader change. | — |

### E. Risks + open questions

- None significant. Smallest table, cleanest scrape.

---

## 6. CRIMINAL

### A. Current schema

| Column | Type | Nullable |
|---|---|---|
| `id` | Integer PK | No |
| `name` | String UNIQUE | No |
| `aliases` | ARRAY(String) | Yes |
| `built_in` | Boolean (DEAD) | default False |
| `faction` | String | No |
| `icon` | String | Yes |
| `is_player` | Boolean | default False |
| `wiki` | String | Yes |

**No `tech_level`, no `ship_name`, no `emoji`** in the model — despite the project AGENTS.md table listing them. Confirmed by direct read of `criminal.py`. **The AGENTS.md description is out-of-date** — flag this as a separate cleanup, NOT in scope here.

Seed JSON (Bartholomeu Drew): `aliases`, `builtIn`, `faction`, `icon`, `isPlayer`, `name`, `wiki`. Notably **no `ship_name`** or `tech_level` in the seed JSON either — those come from the `Bounty.criminal_ship` JSONB column generated at spawn time, not from the catalog.

Loader: `CriminalRepository` setattr loop with `builtIn→built_in`, `isPlayer→is_player`. Any unknown key would raise.

**No `extra_atts` column.**

### B. Wiki v2 data inventory

**Wiki v2 does NOT scrape criminals** (its 5 categories are primary, secondary, turret, module, ship — see `_summary.md` table). Criminals are a bot-specific construct, not a wiki entity in this scrape.

### C. New schema proposal

**No catalog-level changes to Criminal.** The combat sim does not need criminal-catalog stats — it consumes the `Bounty.criminal_ship` JSON at fight time, which is built from a separately-chosen ship + a roll of weapons/modules. The criminal table is essentially a faction-and-flavour lookup.

However, the **Phase-2 damage-tracking columns on `Bounty`** (per task description and journal Entry 4) need to be added in the Phase-1 migration so we don't churn the schema later. See §7.

### D. Migrations + transformations

| Step | Description |
|---|---|
| Catalog: **no change.** |
| Damage-tracking on `Bounty` — see §7. |

### E. Risks + open questions

- **OQ**: `models/AGENTS.md` lists Criminal columns (`tech_level`, `ship_name`, `emoji`) that don't exist on the model. This is doc drift, not code drift. Flag for separate cleanup. *(Not in this scope.)*

---

## 7. PHASE-2 DAMAGE-TRACKING COLUMNS (Phase-1 migration scope)

Per journal Entry 3 and the task description, the Phase-1 schema migration MUST add the columns the Phase-2 damaged-opponent mechanic will use.

### Player (`players` table)

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| `current_hull` | Integer | Yes | NULL | NULL = "at max"; resolver treats NULL as `ship.armour`. |
| `current_armour` | Integer | Yes | NULL | NULL = "at max from armour modules". |
| `current_shield` | Integer | Yes | NULL | NULL = "at max from shield modules". |
| `last_damage_at` | DateTime(timezone=True) | Yes | NULL | Drives OOC recovery clock. |

**Why nullable / no `0` default**: per Entry 3, Phase-1 combat starts both combatants at full HP. NULL semantically means "no damage outstanding" and is cheap to interpret. A `0` default would conflict with "alive at max" and require an additional max-load lookup just to interpret the row.

### Bounty (`bounty` table)

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| `criminal_current_hull` | Integer | Yes | NULL | Same semantics as player. |
| `criminal_current_armour` | Integer | Yes | NULL | |
| `criminal_current_shield` | Integer | Yes | NULL | |
| `criminal_last_damage_at` | DateTime(timezone=True) | Yes | NULL | Symmetric with player; supports OOC recovery for criminals at half rate per Entry 3. |

### Why columns (not `extra_atts`)

These differ from catalog enrichment:
- They are mutated **every combat call** (read+write hot path).
- They are filtered/sorted (e.g. "criminals with hull < 50%" for UI).
- They will eventually drive OOC recovery scheduled-job queries.
- Persistent damage is core game state, not catalog metadata.

### Migration order in the same revision

Following the `0007`/`0008` pattern (single revision adds the columns it cares about, idempotently with `inspector.get_columns()` checks):

```python
def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # Ship: add extra_atts JSON
    if "extra_atts" not in [c["name"] for c in insp.get_columns("ship")]:
        op.add_column("ship",
            sa.Column("extra_atts", postgresql.JSON, nullable=True, server_default="{}"))

    # Player: damage-tracking
    player_cols = [c["name"] for c in insp.get_columns("players")]
    for col, ctype in [
        ("current_hull",    sa.Integer()),
        ("current_armour",  sa.Integer()),
        ("current_shield",  sa.Integer()),
        ("last_damage_at",  sa.DateTime(timezone=True)),
    ]:
        if col not in player_cols:
            op.add_column("players", sa.Column(col, ctype, nullable=True))

    # Bounty: criminal damage-tracking
    b_cols = [c["name"] for c in insp.get_columns("bounty")]
    for col, ctype in [
        ("criminal_current_hull",    sa.Integer()),
        ("criminal_current_armour",  sa.Integer()),
        ("criminal_current_shield",  sa.Integer()),
        ("criminal_last_damage_at",  sa.DateTime(timezone=True)),
    ]:
        if col not in b_cols:
            op.add_column("bounty", sa.Column(col, ctype, nullable=True))
```

Revision id: `0009_combat_rewrite_phase1_schema` (filename) with `down_revision = "0008"`. The naming follows the existing convention (`0007`, `0008`).

---

## 8. Migration shopping list (consolidated)

| # | Item | File | Action |
|---|---|---|---|
| M1 | Alembic revision `0009_combat_rewrite_phase1_schema.py` | `services/bot-core/src/persist/database/revisions/versions/` | NEW |
| M2 | `Ship.extra_atts: JSON` | `services/bot-core/src/persist/models/ship.py` | ADD column |
| M3 | `Player.current_hull / current_armour / current_shield / last_damage_at` | `services/bot-core/src/persist/models/player.py` | ADD 4 columns |
| M4 | `Bounty.criminal_current_hull / criminal_current_armour / criminal_current_shield / criminal_last_damage_at` | `services/bot-core/src/persist/models/bounty.py` | ADD 4 columns |
| L1 | `ShipRepository.create_or_update` — route unknown keys to `extra_atts` | `services/bot-core/src/persist/repositories/ship_repository.py` | MODIFY |
| L2 | `SecondaryWeaponRepository.create_or_update` — accept `loadingSpeed` or `loading speed` | `services/bot-core/src/persist/repositories/secondary_weapon_repository.py` | FIX BUG |
| D1 | Seed JSON enrichment — 65 ships | `services/bot-core/import_data/ship/*.json` | UPDATE |
| D2 | Seed JSON enrichment — 40 primaries | `services/bot-core/import_data/primary_weapon/*.json` | UPDATE |
| D3 | Seed JSON enrichment — 30 secondaries (15 Phase-1 + 15 Phase-2) | `services/bot-core/import_data/secondary_weapon/*.json` | UPDATE |
| D4 | Seed JSON enrichment — 10 turrets | `services/bot-core/import_data/turret_weapon/*.json` | UPDATE |
| D5 | Seed JSON enrichment — 66 modules (synthetic `hps_pct_per_sec` for Ketar; wiki keys for all) | `services/bot-core/import_data/module/*.json` | UPDATE |
| D6 | Scimitar + Specter `builtinModules: ["U'tool"]` | `services/bot-core/import_data/ship/nivelian.scimitar.json`, `nivelian.specter.json` | UPDATE |
| D7 | TL fixes: 128MJ Railgun TL 5→6, H'Belam TL 5→6 (pending user confirm) | `services/bot-core/import_data/primary_weapon/`, `module/` | UPDATE |
| P1 | Production data backfill: post-deploy, run `python -m utils.data_loader load_data <category>` once per category to UPSERT enriched JSON | bot-core container | OPERATIONAL |

`TableNames` enum requires **no changes** — no new tables, only columns.

### Sequencing (must hold)

1. **PR-1 (schema-only)**: M1 + M2 + M3 + M4. Ship migration with idempotent `inspector.get_columns()` guards. Deploys cleanly to any DB whether or not Phase-1 logic exists. No behaviour change yet.
2. **PR-2 (loader-only)**: L1 + L2. Loader changes only — no consumer reads from the new fields yet.
3. **PR-3 (seed-data)**: D1–D7 (enrichment). UPSERT-safe; rerun loaders to populate prod DB.
4. **PR-4 (consumer code)**: `combat_balance.py` + new resolver in `services/combat/`. Reads `extra_atts` and the new damage columns. Feature-flagged behind a resolver-pick (Entry 4: "keep `SimpleTTKResolver` as a flag fallback").

Each PR is independently deployable and revertable. The schema PR is the only one that touches Alembic.

---

## 9. Backfill strategy for prod data

The auto-seeder is **first-boot-only** (skips populated tables). Production has 65 ships, 40 primaries, 30 secondaries, 10 turrets, 66 modules, 21 criminals, ~30 systems already in DB. To pick up enriched JSONs, we need one of:

**Option A (recommended): Manual one-shot reseed pass**
Add a CLI flag or a one-shot script that calls `load_data(category)` directly for each category. Since `create_or_update` is UPSERT-by-name, existing rows get their new fields filled in. Run once per category, ops-supervised.

**Option B: New auto-seeder mode (riskier)**
Extend `auto_seeder.py` with a "refresh from import_data" path triggered by an env var, e.g. `BOUNTYBOT_SEED_REFRESH=1`. Only enrich; never delete. Safer than Option A but adds startup logic that's only meaningful for one deploy.

**Option C: Idempotent migration-time seeder (most surgical)**
The combat-rewrite migration could include a post-upgrade hook that calls the seeder for the touched categories. Risks Alembic going async-unsafe; the project does not currently mix Alembic with async seeders.

**Recommendation**: Option A. Document as a deploy step. Single command per category. Easy to audit, easy to rerun.

---

## 10. Risks & open questions (consolidated)

### Migration risks

| Risk | Mitigation |
|---|---|
| Ship `add_column extra_atts` race with concurrent inserts | `server_default="{}"` + nullable; risk is near-zero for a 65-row table during the deploy window. |
| Loader L1 / L2 changes ship without enrichment first | Order PR-1 → PR-2 → PR-3 strictly. PR-2 is a no-op without enriched JSON, so it's safe to ship first. |
| Seed reseed re-overwrites manually-tuned rows | `create_or_update` is full-replace by JSON keys present. If anyone has hot-patched DB rows by hand, they get clobbered. Audit: announce to ops; no one is doing this. |
| Tech-level bumps (128MJ, H'Belam) shift shop-tier visibility | Confirm with user before flipping (Phase-1 carry-over, not journal-locked). |
| Secondary `value: 0 → wiki price` flips shop prices | Confirm with user. Audit `shop_service.py` reads of `SecondaryWeapon.value` for surprises. |
| `Bounty` damage columns nullable everywhere | Resolver must treat NULL as max — same pattern as Player. Document in resolver. |

### Open questions for the user

1. **TL drift for 128MJ Railgun and H'Belam**: journal says wiki infobox is authoritative → TL 6. Confirm we apply the bump (Phase-1 shop tier impact). *(Not journal-locked.)*
2. **Secondary `value: 0 → wiki price`**: 29 secondaries will go from "$0" to real prices after reseed. Confirm shop impact is acceptable. *(Not journal-locked.)*
3. **`dlc` field exposure**: leave as `extra_atts.dlc` or promote to a Ship column for future filtering? Recommendation: `extra_atts` for now.
4. **GoF2 HD Android prices**: keep in `extra_atts.price_credits_android` (recommended) or first-class column?
5. **Phase-2 secondaries (mines, sentry-guns, cluster-missiles, etc.)**: enrich now (recommended) or leave at zero until Phase-2 lands?
6. **Backfill strategy**: confirm Option A (manual reseed) is acceptable. Alternatives B/C exist.

### No contradictions to journal-locked decisions identified

I cross-checked every recommendation against Entries 0, 3, 4, 5, 5a–5h:
- Bias to `extra_atts` ✓ (Entry 4)
- Damage-tracking columns Phase-1 ✓ (Entry 3, Entry 4 #4)
- Ship `armour` = hull, ArmourModule = armour buffer, ShieldModule = shield ✓ (Entry 3)
- No NPC-only ships filter ✓ (Entry 4 #7)
- Wiki canonical except Vossk Battlecruiser ✓ (Entry 4 #10)
- Builtin-cloak rule + off-slot ✓ (Entry 5h)
- `builtIn` attribute left alone ✓ (Entry 5h audit)
- Phase-1 secondary subtypes = rocket/missile/nuke only ✓ (Entry 5d) — schema accommodates all 9 subtypes regardless

---

## 11. TL;DR

- **One Alembic migration** (`0009_combat_rewrite_phase1_schema`).
- **Single new column on Ship**: `extra_atts: JSON`.
- **Eight new columns split across Player (4) and Bounty (4)**: `current_*` damage state + `*_last_damage_at` for OOC recovery.
- **Zero new columns on Module, PrimaryWeapon, SecondaryWeapon, TurretWeapon, Criminal**. All wiki enrichment lands in their existing `extra_atts` JSON column.
- **Two loader patches**: route Ship unknowns to `extra_atts`; fix Secondary `loading speed` space-key bug.
- **211 seed JSON enrichments** across 5 categories, plus Scimitar/Specter builtinModules.
- **One manual reseed pass per category** at deploy time to backfill prod rows.

Confidence: design is internally consistent and respects every journal-locked decision. The biggest surface for surprise is the secondary-weapon `value: 0 → wiki price` shift; flag for user before reseed.

— architect
