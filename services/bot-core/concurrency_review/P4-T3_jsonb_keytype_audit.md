# P4-T3: JSONB Key-Type Audit — Non-str Dict Keys + Hashing/Key-Order Dependencies

**Author:** Developer Agent  
**Date:** 2026-06-07  
**Status:** COMPLETE — gates P4-T2/T8/T9  
**Purpose:** Determine whether the orjson engine codec for P4-T2 requires `OPT_NON_STR_KEYS`.

---

## 1. Scope

Audited JSON columns in bot-core models:

| Model | Column(s) | Column type |
|-------|-----------|-------------|
| `CombatLog` | `data` | `JSON` |
| `GuildConfig` | `ship_count_range`, `weapon_count_range`, `secondary_weapon_count_range`, `module_count_range`, `turret_count_range`, `ship_quantity_range`, `weapon_quantity_range`, `secondary_weapon_quantity_range`, `module_quantity_range`, `turret_quantity_range`, `tech_level_probabilities`, `xp_thresholds`, `division_temperatures`, `bounty_max_per_tier`, `division_max_tl` | `JSON` |
| `Bounty` | `route`, `checked`, `criminal_ship` | `JSON` |
| `PlayerShip` | `weapons`, `modules`, `turrets`, `secondary_weapons`, `secondary_ammo` | `JSON` |
| `Ship` | `compatible_skins`, `extra_atts` | `JSON` |
| `Weapon` / `PrimaryWeapon` / `SecondaryWeapon` / `TurretWeapon` | `extra_atts` | `JSON` |
| `Module` | `extra_atts` | `JSON` |
| `System` | `neighbours`, `coordinates`, `aliases` | `ARRAY(String)` / `ARRAY(Integer)` — **NOT JSON** |

**Note:** `PlayerInventory` has no JSON columns. `AdminAuditLog.details` and `DiscordMessage.embed_payload` are `Text` (stdlib `json.dumps` produces a string blob; the SQLAlchemy engine JSON codec never touches them).

---

## 2. Per-Column Key-Type Findings

### 2.1 `player_ship.secondary_ammo`

**Model definition** (`src/persist/models/player_ship.py:38`):
```python
secondary_ammo: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
# Comment: {weapon_name: remaining_rounds}
```

**Code that writes it** — every write site in `loadout_consistency_service.py`, `combat_service.py`:
```python
current_ammo[item_name] = current_ammo.get(item_name, 0) + cargo_qty_to_move
ship.secondary_ammo = current_ammo  # item_name is a str (weapon display name)
```
```python
ammo: dict[str, int] = dict(ship.secondary_ammo or {})
ammo[w_name] = new_qty   # w_name comes from rounds_fired: dict[str, int]
```

The sidecar is keyed by `item_name` (a `str` weapon display name, e.g. `"AMR Tormentor"`), and the value is an `int` round count. **Keys are always strings.**

**Live row verification:**
```sql
SELECT id, ship_name, secondary_weapons, secondary_ammo
FROM player_ships;
-- id=1: ship_name=Betty, secondary_weapons=["AMR Tormentor"], secondary_ammo=NULL
-- id=2: ship_name=Betty, secondary_weapons=[], secondary_ammo=NULL
```
Both live rows have `secondary_ammo = NULL` (no ammo tracking yet seeded). There are no rows with non-null `secondary_ammo` in the dev DB. The code confirms it would write `{"AMR Tormentor": N}` — string keys.

**Verdict: STRING keys only.**

### 2.2 `player_ship.weapons`, `modules`, `turrets`, `secondary_weapons`

**Model definition** (`src/persist/models/player_ship.py:27-30`):
```python
weapons: Mapped[list[str] | None] = mapped_column(JSON, ...)   # Array of equipped primary weapon names
modules: Mapped[list[str] | None] = mapped_column(JSON, ...)   # Array of equipped module names
turrets: Mapped[list[str] | None] = mapped_column(JSON, ...)   # Array of equipped turret weapon names
secondary_weapons: Mapped[list[str] | None] = mapped_column(JSON, ...)
```

These are JSON **arrays** (`list[str]`), not dicts. Arrays have no keys in JSON. **Dict-key issue does not apply.**

### 2.3 `bounty.checked`

**Model definition** (`src/persist/models/bounty.py:36`):
```python
checked: Mapped[dict] = mapped_column(JSON, nullable=False)
# Comment: system_name -> user_id (-1 = unchecked)
```

**Code that writes it** (`src/services/bounty_service.py:1236`):
```python
checked = {system: -1 for system in route}  # system is a str (system name)
```
Sentinel values: `UNCHECKED = -1`, `FORFEITED_CHECK = -2` — these are **values**, not keys.

**Live row sample:**
```json
{"K'Ontrr": -1, "S'Kolptorr": -1, "V'Ikka": -1, "Augmenta": -1, ...}
```
Keys are system name strings. Values are integers (`-1`, `-2`, or Discord user_id `int`). **Keys are always strings.**

**Verdict: STRING keys only.**

### 2.4 `bounty.route`

`route: Mapped[list] = mapped_column(JSON, nullable=False)` — JSON **array** of system name strings. No dict keys. **Does not apply.**

### 2.5 `bounty.criminal_ship`

Built by `bounty_service.generate_loadout()` (`src/services/bounty_service.py:747-802`). This returns a dict with string keys: `"ship_name"`, `"ship_value"`, `"weapons"`, `"modules"`, `"turrets"`, `"secondaries"`, etc.

**Live row sample (confirmed via docker):**
```json
{
  "ship_name": "Furious", "ship_emoji": "...", "ship_value": 75800,
  "armor_hp": 176, "shield_hp": 0, "total_hp": 176,
  "weapons": [{"name": "K'booskk", "emoji": "...", "value": 15302, "dps": 15.9}],
  "modules": [{"name": "Telta Quickscan", ..., "extra_atts": {...}}],
  ...
}
```
All keys are strings. The nested `extra_atts` dict within `modules` also uses string keys only (e.g. `"builtIn"`, `"showCargo"`, `"timeToLock"`, `"extra_atts"`).

**Verdict: STRING keys only.**

### 2.6 `guild_config` JSON columns

All 15 JSON dict columns in `GuildConfig` use string keys:

- `*_count_range`, `*_quantity_range`: `{"min": N, "max": N}` — string keys `"min"`, `"max"`
- `tech_level_probabilities`: `{"same_level": 0.70, "one_lower": 0.20, "two_lower": 0.10}` — string keys
- `xp_thresholds`: `{"Silver": 1000, "Gold": 5000, "Platinum": 15000}` — string keys (tier names)
- `division_temperatures`: `{"bronze": 1.0, "silver": 1.0, "gold": 1.0, "platinum": 1.0}` — string keys
- `bounty_max_per_tier`: `{"bronze": 3, "silver": 3, "gold": 3, "platinum": 3}` — string keys
- `division_max_tl`: `{"bronze": 2, "silver": 4, "gold": 7, "platinum": 10}` from `GameConstants.DIVISION_MAX_TL` (`src/services/game_constants.py:64-69`) — string keys

**Live row verification:**
```sql
SELECT xp_thresholds, division_temperatures, bounty_max_per_tier, division_max_tl, tech_level_probabilities
FROM guild_configs LIMIT 1;
-- xp_thresholds: {"Silver": 1000, "Gold": 5000, "Platinum": 15000}
-- division_temperatures: {"bronze": 1.0, "silver": 1.0, "gold": 1.0, "platinum": 1.0}
-- bounty_max_per_tier: {"bronze": 3, "silver": 3, "gold": 3, "platinum": 3}
-- division_max_tl: NULL (not yet set in dev)
-- tech_level_probabilities: {"same_level": 0.7, "one_lower": 0.2, "two_lower": 0.1}
```
**Verdict: STRING keys only.**

### 2.7 `ship.compatible_skins`

**Model definition** (`src/persist/models/ship.py:20`):
```python
compatible_skins: Mapped[dict[str, str]] = mapped_column(JSON, nullable=True)
```

**Seed data sample** (from `import_data/ship/teneta.red.json`):
```json
"compatibleSkins": {
  "urban-camo": "https://i.postimg.cc/...",
  "racing-stripes": "https://i.postimg.cc/...",
  ...
}
```
Keys are skin name strings. **Verdict: STRING keys only.**

### 2.8 `ship.extra_atts`, `weapon.extra_atts`, `module.extra_atts`

**Model definitions** — all `Mapped[dict[str, Any]]` with `JSON` type.

**Seed data exhaustive scan:** Scanned all 358 JSON files in `import_data/`. Zero integer keys found in any dict field, including nested `extra_atts` dicts.

**Seed data sample:**
- `module extra_atts`: `{"armour": 160, "mechanics_text": "..."}` — string keys
- `primary_weapon extra_atts`: `{"loading_speed_ms": 120, "range_m": 2500, ...}` — string keys
- `secondary_weapon extra_atts`: `{"loading_speed_ms": 3000, "range_m": 6300, "subtype": "cluster-missile", ...}` — string keys
- `ship extra_atts`: `{"price_credits_android": 3805000}` — string keys

**Nested `extra_atts` pattern** — the DB stores a double-nesting: `{"builtIn": false, ..., "extra_atts": {"loading_speed_ms": ..., ...}}`. Both outer and inner dicts use string keys only.

**Verdict: STRING keys only.**

### 2.9 `combat_log.data`

**Model definition** (`src/persist/models/combat_log.py:33`):
```python
data: Mapped[dict] = mapped_column(JSON, nullable=False)
# Comment: "Full event-tick timeline + summary (§12). Generic JSON — never queried internally."
```

**Structure** (`src/services/combat_log_service.py:103-108`):
```python
data_blob: dict = {
    "schema_version": 1,
    "summary": summary,
    "timeline": serialised_timeline,
    "metadata": ...,
}
```

**`summary.combatants` keys** (`src/services/combat_resolver.py:1118-1120`):
```python
"combatants": {
    "1": _combatant_block(c1, c1_slot),
    "2": _combatant_block(c2, c2_slot),
}
```
Keys `"1"` and `"2"` are **string literals**.

**Event `data["side"]` values** — `state.slot` is `int` (1 or 2), stored as a **value**, not a key. E.g.:
```python
data={"layer": "shield", "amount": 1, "hp_after": state.current_shield, "side": state.slot}
```
The int value `1` or `2` is a dict **value** under the string key `"side"`. This is irrelevant for dict-key-type semantics (orjson does not raise on non-str dict **values**).

**Live row sample (via docker):**
```json
{"data": {"hit": false, "slot": "primary", "weapon": "Nirai Impulse EX 1", "subtype": "primary", "accuracy": 0.65},
 "tick": 767, "type": "weapon_fire", "actor": "Betty", "target": "Betty"}
```
All event keys (`"data"`, `"tick"`, `"type"`, `"actor"`, `"target"`) are strings. Inner `"data"` dict keys (`"hit"`, `"slot"`, `"weapon"`, `"subtype"`, `"accuracy"`) are strings.

**Additional sub-dicts within combatant blocks:**
- `module_activations`: `{module_key: count}` where `module_key` is a string constant (e.g. `"cloak"`, `"booster"`)
- `secondary_fired`: `{subtype: count}` where `subtype` is a string (e.g. `"rocket"`)
- `secondary_rounds_by_weapon`: `{weapon_name: count}` where `weapon_name` is a string
- `start_hp` / `final_hp`: `{"shield": N, "armour": N, "hull": N}` — string keys

**Verdict: STRING keys only.** The int `side` values in event dicts are values, not keys.

### 2.10 `system.connections` — NOT A JSON COLUMN

The `System` model (`src/persist/models/system.py`) uses `ARRAY(String)` for `neighbours`, `ARRAY(Integer)` for `coordinates`, and `ARRAY(String)` for `aliases`. These are PostgreSQL native arrays, not JSONB columns. The orjson engine codec is not involved. **Not in scope.**

---

## 3. Empirical orjson Behavior (Verified on Host)

Executed empirically on host (Python 3.13, orjson installed in project virtualenv):

```
orjson int-key without OPT_NON_STR_KEYS RAISES: TypeError: Dict key must be str
orjson int-key WITH OPT_NON_STR_KEYS: b'{"1":"a"}'
stdlib json.dumps int-key: {"1": "a"}
orjson round-trip int keys: {'1': 'a', '2': 'b'} key types: ['str', 'str']
stdlib json round-trip int keys: {'1': 'a', '2': 'b'} key types: ['str', 'str']
orjson str-keyed dict: b'{"a":1,"b":2}'
```

**Key conclusions:**

1. `orjson.dumps({1: "a"})` **raises `TypeError: Dict key must be str`** without `OPT_NON_STR_KEYS` (not `JSONEncodeError` as the PyPI docs describe — the actual exception class is `TypeError`, a subclass of the documented behavior).
2. `orjson.dumps({1: "a"}, option=orjson.OPT_NON_STR_KEYS)` yields `b'{"1":"a"}` — coerces int key to string.
3. `json.dumps({1: "a"})` yields `'{"1": "a"}'` — silently coerces int key to string.
4. **Round-trip semantics are identical:** both stdlib json and orjson+OPT_NON_STR_KEYS return string keys on `loads()`. This is a JSON standard invariant — JSON object keys are always strings.

**Cited documentation:** PyPI orjson — https://pypi.org/project/orjson/ (OPT_NON_STR_KEYS section):
> "Serialize `dict` keys of type other than `str`. This allows `dict` keys to be one of `str`, `int`, `float`, `bool`, `None`, `datetime.datetime`, `datetime.date`, `datetime.time`, `enum.Enum`, and `uuid.UUID`. [...] It raises `JSONEncodeError` if a dict has a key of a type other than `str`, unless `OPT_NON_STR_KEYS` is specified."

---

## 4. Hashing / Key-Order / Serialized-Equality Findings

### 4.1 `json.dumps(payload, default=str)` in `admin.py` and `scheduler.py`

`src/api/routers/admin.py:231,327,423` — uses stdlib `json.dumps` on APScheduler job payloads to build a string for substring-match (`if str(guild_id) in payload_str`). This is NOT using the SQLAlchemy engine codec — it calls stdlib `json.dumps` directly and never writes to the DB. **Not affected by the codec change.**

`src/api/routers/scheduler.py:40` — uses `json.loads(json.dumps(raw, default=str))` to sanitize APScheduler job args for API response. Again stdlib, not the codec. **Not affected.**

### 4.2 `json.dumps(details)` in `audit_service.py`

`src/services/audit_service.py:67` — writes to `AdminAuditLog.details` which is `Text` (not `JSON`). The codec is not involved. **Not affected.**

### 4.3 `json.dumps` in `discord_message.py` and announcement routers

`embed_payload` and related fields are `Text` columns. The codec is not involved. **Not affected.**

### 4.4 Hashlib / MD5 / SHA

`grep -rn "hashlib\|md5\|sha" src/` — no occurrences. **No hashing over JSON columns.**

### 4.5 Serialized JSON equality (`== json.dumps(...)`)

No instance found where the output of `json.dumps()` is used for equality comparison or as a dict/cache key. The map-render cache key in `bounties.py:478` uses `(bounty_id, tuple(route))` — a Python tuple, not serialized JSON. **No equality hazards.**

### 4.6 Key ordering

orjson serializes dict keys in insertion order (same as CPython 3.7+ dict ordering). It does NOT sort keys by default. The existing stdlib `json.dumps` also preserves insertion order. Since no code here compares raw JSON strings, this is safe. The `OPT_SORT_KEYS` option is NOT needed.

### 4.7 Float formatting

orjson emits shortest-round-trip floats (e.g. `0.7` not `0.7000000000000001`). Stdlib `json.dumps` may emit more decimal places for floats that cannot be represented exactly. For `tech_level_probabilities`, the live DB shows `{"same_level": 0.7, "one_lower": 0.2, "two_lower": 0.1}` — orjson will emit these as `0.7`, `0.2`, `0.1` (same as the round-trip value). No behavioral difference for consuming code since all consumers do `config.tech_level_probabilities.get("same_level", 0.70)` which parses the float back out.

---

## 5. Latent Bug Flags (Do Not Fix Here)

### FLAG-1: `combat_log.data` — `data["side"]` stores int values

**Location:** `src/services/combat_resolver.py:499,547,567,644,684,707,756,807,868,884,895` and many more.

`_CombatantState.slot` is `int = 1` (or `2`). These integer values are emitted into event dicts as `data["side"]`. When `dataclasses.asdict()` converts a `CombatEvent`, this remains as `int` in the dict value position.

**This is NOT a dict-key problem** — it is a dict **value**. orjson handles int values natively. The flag is that consuming code does `str(target_side)` (`src/services/combat_resolver.py:1077,1084`) to compare against string slot keys `"1"` / `"2"`. If `side` were read back via orjson, it comes back as `int` (JSON integers are always deserialized as `int`). This is the CURRENT behavior already — stdlib `json.loads` also returns `int` for numeric values.

**Conclusion:** No bug introduced by the orjson codec change. The read-back behavior is identical.

### FLAG-2: `_name_to_slot: dict[str, int]` (internal in-memory, not persisted)

`src/services/combat_resolver.py:1007,1013,1015` — this dict uses string keys and int values. It is never written to the DB. **Not a concern.**

### FLAG-3: `json.dumps(details)` in `audit_service.py` — no `default=str`

`src/services/audit_service.py:67` — calls `json.dumps(details)` without `default=str`. If `details` contains a non-serializable type (e.g. `datetime`), it would already raise under stdlib `json`. This is a pre-existing issue, not introduced by P4-T2. Flagged for awareness; out of scope here.

---

## 6. Final Decision

**All audited JSON columns use exclusively string dict keys.** No column constructs `{int: ...}` or `{tier_int: ...}` patterns anywhere in production write paths. The integer values stored in `combat_log.data` events (e.g. `"side": 1`) are dict **values**, not keys.

**`OPT_NON_STR_KEYS`: NOT REQUIRED.**

> **DECISION: The P4-T2 orjson engine codec MUST NOT set `OPT_NON_STR_KEYS`.** The codec should be configured with just `orjson.dumps` (default) and `orjson.loads`. No per-column caveats. Setting `OPT_NON_STR_KEYS` would be harmless but is unnecessary, adds a small performance penalty for all str-key serializations, and would silently mask any future bug where int-keyed dicts are accidentally written (better to fail fast).

---

## 7. Evidence Summary Table

| Column | Key type | Evidence source | Live row |
|--------|----------|-----------------|----------|
| `player_ship.secondary_ammo` | `str` (weapon name) | `loadout_consistency_service.py:443` `current_ammo[item_name]` | NULL in dev (no equipped secondaries with tracking) |
| `player_ship.weapons/modules/turrets/secondary_weapons` | N/A (JSON array, not dict) | `player_ship.py:27-30` | `["AMR Tormentor"]`, `[]` |
| `bounty.checked` | `str` (system name) | `bounty_service.py:1236` `{system: -1}` | `{"K'Ontrr": -1, ...}` |
| `bounty.route` | N/A (JSON array) | `bounty.py:28` `Mapped[list]` | `["K'Ontrr", "S'Kolptorr", ...]` |
| `bounty.criminal_ship` | `str` (all field names) | `bounty_service.py:747-802` | `{"ship_name": "Furious", ...}` |
| `guild_config.*_range` / `*_probabilities` / `xp_thresholds` / `division_*` | `str` (tier/field names) | `guild_config.py:44-82` + `game_constants.py:64` | `{"Silver": 1000, ...}`, `{"bronze": 3, ...}` |
| `ship.compatible_skins` | `str` (skin names) | `ship.py:20`, seed files | `{"urban-camo": "url", ...}` |
| `ship.extra_atts` / `weapon.extra_atts` / `module.extra_atts` | `str` (field names) | 358 seed files scanned; all string keys | `{"armour": 160, ...}` |
| `combat_log.data` | `str` (all dict keys incl. `"1"`, `"2"` slot keys) | `combat_resolver.py:1011-1120` | `{"schema_version": 1, "summary": {"combatants": {"1": {...}, "2": {...}}}}` |
| `system.neighbours` / `coordinates` / `aliases` | N/A — ARRAY, not JSON | `system.py:13-17` | Not applicable |

---

*Audit doc written at `/proj/services/bot-core/concurrency_review/P4-T3_jsonb_keytype_audit.md`.*
