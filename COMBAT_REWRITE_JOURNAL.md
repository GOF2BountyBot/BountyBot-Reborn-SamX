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

*Last updated: 2026-05-24*
