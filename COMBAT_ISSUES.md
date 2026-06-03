# Combat Phase-1 — Open Issues & Follow-ups

*Tracking log for issues found during E2E validation of the combat workstream.
Created 2026-06-03. Status key: 🔴 open · 🟡 needs decision · 🟢 in-flight · ✅ done.*

Dev env: guild `699744305274945650`; players **samx** (player 1, user `402296276617527306`)
+ **general_failure** (player 2, user `970691862035841048`). Read Discord via the
gateway API (`docker exec bountydev-discord-gateway curl localhost:17999/api/v1/...`).

---

## CI-1 ✅ Criminals deal 0 DPS in combat — FIXED & VERIFIED  *(HIGH)*
**Resolved 2026-06-03.** Fix (persist+read combat fields + self-healing fallback,
cadence 1000ms / range floor 800m) implemented, d-tester signed off (EMP weapons not
promoted, new bounties persist fields, legacy self-heal), cleanup done (dead code
removed, docstring fixed, +2 coverage tests). bot-core suite green. Live: Hiro/Inflict
criminals `raw_dps>0`. Committed.

<details><summary>(historical root-cause detail)</summary>
**d-architect verdict (2026-06-03):** the per-ship correlation was an ARTIFACT —
no ship-discriminating factor. "Betty 3.6" was stale data from the pre-T5
`SimpleTTKResolver` (retired in T10). Under current code **ALL criminals deal 0 DPS**
(universal). Root cause: criminal primary weapons lose `damage_per_shot` /
`loading_speed_ms` / `range_m` at two layers — (A) `BountyService._build_criminal_loadout_dict()`
(`bounty_service.py:587-590`, also turrets 602-605) only persists `dps`; (B)
`LoadoutBuilder.from_criminal_ship()` (`loadout_builder.py:388-392`) only reads `dps`.
The T5 bake (`combat_service.py:307-327`) then yields `eff_damage=0` + `range_m=0`
(fire gate blocks). Fix = persist + read the fields (mirror the turret block) WITH a
self-healing fallback for legacy bounties (derive damage_per_shot from dps + cadence,
non-zero range). Full brief handed to d-developer.

<details><summary>(original symptom table — premise now corrected)</summary>
**Symptom:** in resolved `/check` fights, some criminals render **DPS 0.0** and the
player's **Time-to-Kill = ∞** → those bounties are unlosable free captures.

**Key diagnostic (it's conditional, not global):**
| Criminal | Ship | Criminal DPS in fight |
|---|---|---|
| Ganfor Kant | Betty | **3.6** (fights back) |
| Urr Sekant | Hiro | **0.0** |
| Malon Sentendar | Inflict | **0.0** |
| (earlier) Toma Prakupy | Inflict | 0.0 |
| (earlier) Nombur Telénah | Hiro | 0.0 |

So a criminal on the **starter "Betty"** ship deals damage, but criminals on **"Hiro"
/ "Inflict"** come out at 0 DPS. Criminal loadouts *do* contain weapons on paper
(e.g. Ganfor Kant: `Micro Gun MK I` dps 9.09; `ship_stats.dps`=9.1) — so the weapon
loads into the loadout but yields no damage in the resolver for these ships.

**Hypothesis:** criminal ship/weapon generation or `LoadoutBuilder.from_criminal_ship`
mishandles weapon assignment for non-starter criminal ships (Hiro/Inflict). Compare
the built combatant (raw_dps) of a 0-DPS criminal vs the working Betty criminal.

**Next action:** d-developer applying fix (persist + read combat fields + self-heal),
then d-tester.
</details>
</details>

---

## CI-2 / CI-9 ✅ Combat-summary embed modernized — FIXED & VERIFIED
**Resolved 2026-06-03; owner approved.** Backend now exposes actual after-action data
(`_serialize_fight_results` + duel accept response: final_hp per layer, damage_dealt,
shots/accuracy, duration_s, pvc_dr from `data.summary`). Both embeds (bounty-capture +
duel) rewritten to show REAL stats; `→` arrow + projected DPS/TTK removed; PvC line
PvC-only. d-tester confirmed embed numbers match DB summary EXACTLY; found a CRITICAL
(duel winner resolved by ship-name → wrong winner when both fly "Betty" & target wins) —
FIXED (winner now resolved by surviving hull; slot map challenger=1/target=2).
Live-verified BOTH directions against deployed gateway. 4086 bot-core + gateway cog
tests green. Committed.
<details><summary>(detail)</summary>

**Root finding:** `bountyCog._format_combat_summary` renders **pre-fight PROJECTIONS**
from `FightStats` (`raw_hp`, `varied_hp`, `raw_dps`, projected `ttk`) — NOT what actually
happened. `varied_hp` is a dead variance-era field (== raw_hp since T10), hence the
pointless `→`. Meanwhile the resolver now produces rich ACTUAL after-action data in
`data.summary.combatants` that the embed never surfaces: **final HP per layer
(shield/armour/hull), damage dealt/taken, shots fired/hit, accuracy %, duration, modules
fired**, plus `pvc_damage_reduction`.

**Proposed redesign (needs owner sign-off — UI + backend change):**
- Backend: expose the relevant `data.summary` after-action fields in the `/check` (and
  duel) response so the embed renders REAL outcomes, not projections.
- Embed: drop the `→` arrow; show actual final HP (shield/armour/hull), damage dealt,
  accuracy (hits/fired), duration; keep the `🛡️ PvC reduction` line (PvC only).
- Applies to BOTH the bounty-capture embed and the duel result embed.
**Status:** analysis done; awaiting approval before architect→dev→tester. CI-2's arrow
removal ships as part of this (no separate throwaway fix).
</details>

---

## CI-3 ✅ `/combat-log` feature — COMPLETE
**Resolved 2026-06-03.** d-tester signed off (ownership gate 404, guild scoping,
same-ship-name POV, ordinals, PvC/stalemate, key-event extraction all verified live +
tested). One MEDIUM found & fixed: Key Events embed field could exceed Discord's
1024-char cap → fixed with accumulator + visible `…(+N more events)` truncation marker
(+3 tests, 17 gateway tests green). Committed.
<details><summary>(original)</summary>
New `/combat-log` command (mandatory battle select w/ ownership-gated autocomplete;
detail = summary + key events: secondary fires, module activations, HP-layer
milestones). Built by d-developer: 52 bot-core + 14 gateway tests green, ownership
gate (404 for non-combatant) verified live. **Uncommitted.**
**Next action:** d-tester adversarial review → fix/retest → commit.
</details>

---

## CI-4 🟢 E2E validation — §4/§5/§6 not yet run
§0–§3 + §8 complete (PvC/PvP/persistence/data all ✅, see `COMBAT_E2E_TEST_PLAN.md`).
Remaining: per-weapon equip-and-fight (§4), modules (§5), edge cases (§6). Runnable
now that the env is restored. Can be driven via bot-core API for mechanics; embed
rendering needs Discord.

---

## CI-5 ✅ Shop-exclude no-op secondaries (+ enable secondaries) — COMPLETE
**Resolved 2026-06-03; owner approved keeping secondaries enabled.** Deferred subtypes
(`emp-bomb`/`mine`/`sentry-gun`) excluded from shop at all tiers (`DEFERRED_SECONDARY_SUBTYPES`,
single-sourced w/ resolver); secondaries enabled in shop+equip. d-tester verified exclusion
+ buy→equip→fire end-to-end. Follow-up fix: exposed `secondary_weapons` in `ShipResponse` +
`ShipLoadoutSummaryResponse` (were invisible → `/unequip` autocomplete couldn't see them).
4082 tests green; live-verified. Committed.
<details><summary>(detail)</summary>
**Built but NOT tester-reviewed / NOT confirmed.** The dev found `secondary_weapon`
was gated off entirely (`CURRENTLY_ENABLED_TYPES`), so nothing was in the shop to
exclude. To honor "exclude the no-op ones," the dev **ENABLED secondary weapons in
shop + equip** and excluded the 3 deferred subtypes (`emp-bomb`/`mine`/`sentry-gun`
via new `DEFERRED_SECONDARY_SUBTYPES` constant cross-ref'd to `combat_service.py:1489`).
This is a gameplay change beyond a pure exclusion. **DECISION for owner:** (a) keep —
secondaries become buyable/equippable except the 3 no-ops; or (b) narrow — keep
secondaries gated, just ensure no-ops never appear. Tester pass held pending decision.
Also fixed a shop GET crash (secondary weapons use `damage`, not `dps`). 1527 tests green.
<details><summary>(original)</summary>
`emp-bomb`, `mine`, `sentry-gun` subtypes (~9 seeded items: `EMP GL *`,
`AMR Saber`/`Ksann'k`/`Neétha EMP`, `Berger SG-*`/`T'Suum`) do **nothing** in a fight
(`combat_service.py:1489` — deferred). If they're shop-purchasable they're dead weight.
**Decision needed:** shop-exclude, or document as a known limitation. (`ionizing-missile`
IS handled; the canonical 5 all work.)
</details>

---

## CI-6 🟡 `Shock Blast` seed carries `emp_damage=80` that is inert  *(LOW)*
Shock-blast resolves as distance-push only in Phase-1; the 80 EMP value is unused.
Behaviour matches `COMBAT.md`; just a stale/unused seed value to reconcile.

---

## CI-7 ✅ `/admin_uninstall` leaves orphaned `bounty` + `combat_log` rows — FIXED & VERIFIED
**Resolved 2026-06-03.** `delete_by_guild_id` added to BountyRepository + CombatLogRepository,
wired into `uninstall_guild()`; endpoint docstrings corrected. d-tester verified LIVE
(disposable-guild cleanup deleted bounty+combat_log; real dev guild untouched; idempotent;
privacy fix confirmed — re-register no longer resurfaces fights). Test-quality cleanup done
(hollow admin-router contract test fixed + combat_log repo test parity). 4070 tests green.
Committed (`e154a79`, `0df3181`).
<details><summary>(detail)</summary>
**d-researcher verdict (2026-06-03):** confirmed GAP, not intentional. Uninstall+cleanup
(shared path `config_service.uninstall_guild()`) deletes players/ships/inventories/
guild_shops/guild_configs, but only sets bounties to status='cleared' (no row delete) and
**never touches combat_log**. The `/cleanup` docstring already claims it removes bounty
rows — untrue. No guild-scoped delete methods exist. **Confirmed privacy bug:** a user
re-registering with the same Discord ID in the same guild sees pre-uninstall fights in
`/combat-log`. Fix: add `delete_by_guild_id` to BountyRepository + CombatLogRepository,
call both in `uninstall_guild()`. → d-developer.
<details><summary>(original)</summary>
After a guild nuke/uninstall, `users/players/player_ships/player_inventories/
guild_configs/guild_shops` are cleared but `bounty` (2) and `combat_log` (2) rows
survive, orphaned. Confirm whether uninstall *should* clean these; if so, it's a
cleanup-cascade gap. (Not a rebuild/persistence bug — volume persists fine.)
</details>

---

## CI-10 🟡 `POST /api/v1/shops/refresh` serialization crash  *(pre-existing, not from this work)*
Surfaced by the CI-5 tester. `refresh_shop()` returns a dict containing ORM `GuildShop`
objects under `"items"`, so the public `POST /api/v1/shops/refresh` endpoint throws
`PydanticSerializationError: Unable to serialize unknown type: GuildShop`. **Pre-existing**
(confirmed in git history before CI-5) — NOT a regression. The **admin** refresh path
(`POST /api/v1/admin/shops/refresh`) works correctly. Fix: serialize items to the response
schema in the endpoint (mirror the admin path). Not started.

---

## CI-11 ℹ️ Shop is weapon-heavy after enabling secondaries  *(informational / tuning)*
Secondary weapons reuse the `weapon` count config key, so each shop refresh now generates
up to **5 primary + 5 secondary = 10 weapons**, skewing the per-tier slot mix weapon-heavy.
Intentional/expected from CI-5, but flagged for tuning: if undesired, give `secondary_weapon`
its own quantity range in the shop count config. Owner decision; no action yet.

---

## CI-12 ✅ Embed showed "You won" for a LOST Bronze bonus fight — FIXED & VERIFIED
**Resolved 2026-06-03.** Combat embeds restructured to owner-approved "compact, worded"
layout (`⚔️ Combat vs X — Victory/Defeat/Stalemate in Ns`; `You (ship) — survived/destroyed`;
worded Shield/Armour/Hull; criminal `name (ship)`; duel uses player names). Win/loss header now
derived from ACTUAL final-hull (not the always-true-at-Bronze `combat_won` flag). d-tester
live-verified a Bronze DEFEAT renders "Defeat/destroyed" despite capture succeeding. 365 gateway
tests green; ruff clean. Live after rebuild. Two LOW theoretical edges noted (accuracy=None shows
0%; outcome="stalemate" w/ a 0-hull side would show Stalemate — neither occurs in real resolver
output). Committed.
<details><summary>(original)</summary>
At **Bronze tier**, bounty capture is **guaranteed by design** (`bounty_service.py:1389-1403`:
`combat_won=True` hardcoded; the fight only decides a 2× bonus via
`winner_name == player_ship`). Silver+ has a mandatory combat gate. So capturing even when
you lose the fight is intended at Bronze. **BUG:** the embed's win/loss header is wrong —
`bountyCog._format_combat_summary` derives "⚔️ You won in N.Ns" from the always-true-at-Bronze
`combat_won` capture flag, NOT the actual fight result. Live repro (retest 2026-06-03,
combat_log #48): player Betty hull **0** (died), criminal Inflict hull **83**, `summary.winner=Inflict`,
no 2× bonus awarded (correct) — but embed rendered "⚔️ You won in 33.5s" (WRONG).
**Fix:** derive the won/lost/stalemate header from the actual combat result (summary winner /
player final hull), separate from the capture/outcome label. Same family as the CI-2/#7 duel
winner bug. **Also confirm w/ owner:** is Bronze guaranteed-capture-on-loss intended? (code says yes.)

---

## CI-13 🔴 Nuke mechanics broken / mismatch intended design  *(found in retest 2026-06-03)*
Owner spec: nukes are **AoE, guaranteed-hit, inverse-square damage from epicenter, reduced
self-damage, ~1 use per battle.** Current state (resolver `combat_service.py:1400-1432`,
`_nuke_dmg` line 865, seed `AMR Tormentor`):
- ✅ guaranteed hit (no accuracy roll), ✅ AoE, ✅ reduced self-damage (`NUKE_FRIENDLY_FACTOR=0.25`).
- 🔴 **Random epicenter** (`_rng.uniform(300..5000m)`) almost never lands within the blast
  radius of the target → **0 damage** (battle 50: all 4 nukes 0 dmg, epicenters 2.4–4.9km from
  foe). Contradicts "guaranteed to hit". Likely fix: epicenter ON the target (full dmg to target,
  inverse-square falloff to firer by firer's distance).
- 🔴 **Seed `AMR Tormentor` has no `damage_per_shot`** → 0 dmg even on a direct hit. (All nukes
  likely affected — same class as CI-1 turret seed gap.)
- 🔴 **Cooldown `loading_speed_ms=6000` (6s)** → 4 fires/29s battle; owner wants **1 per battle**
  (battle-length cooldown or 1-charge ammo model — design decision).
- 🟡 **Display:** key-event formatter (`combat_log_service.py:387`) labels nukes "miss" (assumes a
  `hit` flag nukes lack). Nukes detonate; never miss.
- 🟡 **Formula:** `_nuke_dmg = dmg × (1 − d/blast)²` (bounded quadratic) ≠ literal `1/d²`
  inverse-square — confirm intended model w/ owner.
**Plan:** d-architect to reconcile intended model + spec + seed + code → dev → tester. Decisions
needed: epicenter-on-target?; 1-per-battle via long cooldown vs ammo/charges?; falloff formula.

---

## CI-14 🧹 Test agents mutated samx's (main) live loadout  *(process)*
During CI-5 buy/equip testing, an agent bought+equipped `Nirai Impulse EX 1` (primary) and
`AMR Tormentor` (nuke secondary) onto **samx's** Betty (main char), displacing the starter
`Micro Gun MK I` (now in inventory). Owner flagged the unexpected nuke. **Restore pending owner's
choice** (full starter restore vs keep new primary, drop nuke). Going forward: keep
loadout-mutating tests on general_failure / a disposable player, never samx.

---

## CI-15 🟡 `/combat-log` key events miss "Hull depleted" (death)  *(found in retest)*
The resolver (`combat_service.py:821-840`) emits `layer_depleted` events for **shield** and
**armour** only — hull-zero terminates the fight (fight_end) without a `layer_depleted{layer:hull}`
event. The extractor is already ready (`combat_log_service.py:49` has `"hull": "Hull depleted (dead)"`)
but never receives one, so the death milestone is absent from `/combat-log` key events (battle 50
showed only "Armour depleted", not the hull death). Fix: add a hull branch in `_apply_damage`
mirroring the shield/armour emission (emit when `hull_was_positive and current_hull <= 0`).
(Shield-absence for a no-shield ship is correct; armour-depleted already works.)

---

## CI-8 🧹 Housekeeping — committed status / nothing pushed
- ✅ `COMBAT_E2E_TEST_PLAN.md` + `COMBAT_ISSUES.md` committed (`ae157f0`).
- ✅ `/combat-log` feature fully committed (backend router/schema/service + tests were
  orphaned by independent agent commits; gathered into one feat commit).
- Still untracked **by intent**: `AUDIT_REPORT.md`, `COMBAT_RECONCILIATION.md`.
- Branch `dev` is ahead of origin; **nothing pushed** (commit-freely / don't-push).
- Lint/format gates green throughout.
