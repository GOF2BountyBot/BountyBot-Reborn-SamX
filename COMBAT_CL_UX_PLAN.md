# Combat-Log UX Batch Plan — CI-20 / CI-21 / CI-22 / CI-24 (+ CI-19 probe polish)

*Ready-to-execute d-developer brief. d-architect design-lock 2026-06-04 + owner decisions baked in.
Tracker: `COMBAT_ISSUES.md` → CI-20/21/22 (+ new CI-24). One subsystem: resolver event emission
(`combat_service.py`) → persisted `combat_log.data.timeline` → `_extract_key_events` + summary
(`combat_log_service.py`) → dropdown + detail embed (`combatLogCog.py`).*

## Owner decisions (LOCKED 2026-06-04)
- **CI-20 dropdown:** full two-name format "**`<criminal/bounty name>` vs `<player name>`**" (owner's exact
  words: "bounty name vs player name"), matching duel style. Log BODY attributes by pilot/criminal name.
- **CI-21 de-spam:** emission-side latch; re-emit `layer_depleted` only after the layer recovers to
  ≥ **25%** of max (`COMBAT_LAYER_REEMIT_FRACTION=0.25`, env-tunable).
- **CI-22 baseline events:** **Baseline + HP milestones** — engagement line + first-hit-per-side +
  outcome line + per-side 50%/25% HP-milestone lines (display-side synthesis).
- **CI-24 (fold in):** re-key the fight summary on combatant **slot** (not ship name) so same-ship
  fights show correct per-side dmg/accuracy/shots.
- Player-name source: reuse the duel resolver (`display_name` → `discord_username` → `Player {id}`);
  promote to a shared helper used by both bounty + duel paths.

## Key facts (architect-verified, cite when implementing)
- `_CombatantState.name = loadout.ship_name` (`combat_service.py:428`) — root of the identity problem.
  Every actor-bearing event sets `actor = <ship name>` (weapon_fire :1286/1339/1368/1401/1435/1461/1491;
  layer_depleted :832/842/852; module_activation :636/659/708; regen :479/519/535; secondary_depleted).
- **`layer_depleted` has EXACTLY ONE consumer** — `_extract_key_events` (`combat_log_service.py:436-448`).
  `_build_fight_summary` (`combat_service.py:948-1008`) never reads it. → emission-side de-spam is SAFE
  (no summary/stat impact). Verified by grep: emits at :831/841/851, sole read in extraction.
- Summary keys per-combatant accumulators on `c1.name`/`c2.name` (ship) — `combat_service.py:965-970,
  978, 990, 1005-1007` → THIS is the CI-24 merge bug when ships share a name.
- `damage` event carries `target=state.name` + `data.hp_after.{shield,armour,hull}` — basis for HP
  milestones (key by new `side`, not ship name).
- `Player.display_name` exists (nullable). Canonical resolver pattern: `duel_service._resolve_player_label`
  (`duel_service.py:42-50`); duel labels resolved at :341-372.
- **Retention 72h** (`COMBAT_LOG_RETENTION_HOURS`) → NO migration/backfill needed; new readers just need
  graceful fallback for old rows lacking `data["side"]` (fall back to ship-name label).

## Implementation

### CI-20 — name identity (thread names as NEW field; leave `name`=ship untouched)
- `_CombatantState`: add `slot: int` (1|2) and `display_name: str` (defaults to `ship_name`).
- `fight_ships` / `TickResolver.resolve` / `_init_combatant`: add `combatant1_label`/`combatant2_label`
  kwargs (default to ship_name → preflight/sim/dev-harness unchanged). Set `slot` 1/2.
- **Emit `data["side"] = <slot>` on EVERY actor-bearing event** (weapon_fire = attacker's slot). `side`
  is the unambiguous key (pilot names not guaranteed unique). Keep `actor = ship_name` byte-for-byte.
- `_build_fight_summary._combatant_block` (:1010-1027): `"name"` = `display_name`, `"ship"` = `ship_name`.
- Callers pass labels: bounty PvC/bonus (`bounty_service.py:1494,1540`; `bounties.py:224`) → c2 =
  `bounty.criminal_name`, c1 = player label; duel (`duel_service.py:281`) → challenger/target labels.
- `_extract_key_events(timeline, tick_ms, combatants_map)`: resolve each line's label via
  `data["side"]` → `combatants_map[str(side)]["name"]`; fall back to `actor` when `side` absent.
- Gateway `combatLogCog.py`: dropdown `_make_choice_label` → full "`<c1 name>` vs `<c2 name>`" two-name
  string (mirror duel). Summary block `**{name}** ({ship})` now renders `H'Soc (Betty)` / `SamX (Betty)`.

### CI-21 — de-spam (emission-side recovery latch)
- `_CombatantState.depleted_layers: set[str]` (init empty).
- On depletion transition (`layer_was_positive and current <= 0`, :826-857): emit `layer_depleted`
  only if `layer not in depleted_layers`; then add it.
- In `_tick_shield_regen` (:460) and `_tick_repair_bot_regen` (:490): after regen, if
  `current_layer >= ceil(max_layer * COMBAT_LAYER_REEMIT_FRACTION)` → discard layer from latch
  ("meaningful recovery"). Hull terminal → emits once on death. Latch also collapses same-tick dups.
- New `GameConstants.COMBAT_LAYER_REEMIT_FRACTION = 0.25` (env-tunable per existing `_track_*` pattern).

### CI-22 — baseline events (display-side synthesis in `_extract_key_events`; no new resolver events)
- **Tier A (always):** engagement line (from `fight_start`: "`Engagement: <c1> (<ship>) vs <c2> (<ship>) — <dist>m`");
  first-hit per side (first `weapon_fire` with `hit=True` per `side`); outcome line (from `fight_end`:
  "`<winner> wins — <loser> destroyed (Ns)`" / "`Stalemate (time cap)`").
- **Tier C (owner chose IN):** per-side HP milestone at 50% and 25% — synthesized from `damage`
  `data.hp_after` keyed by `side`, emit once on first crossing (milestone-crossing = inherently deduped).
- Sort ALL key events by tick before the cog's 1024-char field truncation so baseline lines aren't starved.

### CI-24 — summary slot-keying (folded in, owner-approved)
- Re-key `_build_fight_summary` per-combatant accumulators on **slot (1/2)** instead of `c1.name`/`c2.name`
  (`combat_service.py:965-970, 978, 990, 1005-1007`), so same-ship fights don't merge stats. Verify the
  output `combatants["1"|"2"]` mapping stays correct and the read path (`_pov_outcome`) still works.
  (Winner resolution by `winner_name == ship_name` at `bounty_service.py:1506/1551`, `bounties.py:237`,
  `duel_service.py:297` — note if it shares the fragility; fix if cheap + safe, else flag.)

### CI-19 probe polish (folded in — same gateway service)
- The startup autocomplete health probe (`bot.py` / `autocomplete_state.init`) fires ~3s into startup,
  before bot-core is ready on a full `--force-recreate`, logging a misleading ERROR though warm jobs
  retry+recover. Make the probe RETRY (mirror the cog preload retry: a few attempts w/ backoff) and only
  log ERROR after retries exhaust. Non-fatal.

## Risk register
- **CI-24 is the mechanics-adjacent change** — re-keying the summary. Regression-guard: existing
  single-ship-name fights must produce byte-identical summary stats; assert per-side stats are DISTINCT
  in a same-ship fight (the bug repro).
- `side` threading completeness — every emit site must set `data["side"]`; a miss degrades to ship-name
  fallback (safe but defeats the fix for that line). Test asserts `side` present on each event type.
- Old-row fallback (no `side`, ship-name only) must not crash; renders ship-name labels. No backfill (72h).
- Event volume: CI-21 reduces stored events; CI-22 baseline lines bounded (~7-9/fight), synthesized at read.

## File checklist
- `services/bot-core/src/services/combat_service.py` — `_CombatantState` (+slot, display_name,
  depleted_layers); `_init_combatant`/`resolve`/`fight_ships` label kwargs+slot; all emit sites (`side`);
  `_apply_damage` layer latch (826-857); regen latch-clear (460/490); `_build_fight_summary` (name=display,
  ship=ship, **re-key accumulators on slot** for CI-24).
- `services/bot-core/src/services/game_constants.py` — `COMBAT_LAYER_REEMIT_FRACTION` (+env), milestone thresholds.
- `services/bot-core/src/services/combat_log_service.py` — `_extract_key_events(+combatants_map, side→name,
  Tier A/C synthesis, sort-by-tick)`; `get_detail` passes the map; verify `combatant{1,2}_name` persist.
- `services/bot-core/src/services/bounty_service.py` (1494, 1540), `api/routers/bounties.py` (224) — pass labels.
- `services/bot-core/src/services/duel_service.py` (281) — pass labels; extract shared `_resolve_player_label` helper.
- `services/discord-gateway/src/cogs/combatLogCog.py` — dropdown full "X vs Y"; baseline-line formatting if needed.
- `services/discord-gateway/src/bot.py` / `utils/autocomplete_state.py` — CI-19 probe retry.
- Check `api/schemas/combat_log_schema.py` — new event_type strings still validate (likely free-form).

## Test plan (pytest; tee to log, grep; real objects, ≤2 mocks)
- CI-20: same-ship-name PvC → `summary.combatants[].name`=pilot/criminal, `.ship`=Betty; row names = pilots;
  dropdown "X vs Y"; body labels by side→name (never "Betty:" when names differ); old-row (no side) fallback.
- CI-21: shield with sliver regen between shots → exactly ONE shield depleted until recovery ≥25%; another
  only after genuine recovery; armour+repair-bot same; hull once on death; **summary stats byte-identical
  to pre-change** (no-consumer regression guard).
- CI-22: one-sided primaries-only winner → has engagement + first-hit + outcome lines; loser has milestones
  + depletions; per-side 50%/25% fire once; events tick-sorted; truncation well-formed.
- CI-24: same-ship fight → per-side dmg/accuracy/shots are DISTINCT and correct (not merged); single-name
  fights unchanged; winner resolution still correct.
- CI-19: probe retries on cold start, only ERRORs after retries exhaust.
- Full bot-core + gateway suites green; ruff clean.
