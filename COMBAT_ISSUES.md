# Combat Phase-1 — Open Issues & Follow-ups

*Tracking log for issues found during E2E validation of the combat workstream.
Created 2026-06-03. Status key: 🔴 open · 🟡 needs decision · 🟢 in-flight · ✅ done.*

Dev env: guild `699744305274945650`; players **samx** (player 1, user `402296276617527306`)
+ **general_failure** (player 2, user `970691862035841048`). Read Discord via the
gateway API (`docker exec bountydev-discord-gateway curl localhost:17999/api/v1/...`).

---

## ▶ RESUME HERE (2026-06-04, overnight session)
**CI-16 AND CI-17 are DONE & LIVE-VALIDATED.** Both ran full architect→dev→tester cycles + live
smoke-tests against the rebuilt stack. **Owner decisions in (2026-06-04):** CI-17 knobs = keep my
defaults; CI-6 = WONTFIX (keep EMP seed, it's wiki data); CI-11 = YES build it.
**Next, in order:** CI-18 ✅ DONE (migration 0015, owner-approved 2026-06-05) → **CI-4 = full E2E
re-run** (owner 2026-06-05: re-run the WHOLE plan §0–§8, not just §4/§5/§6, since we found-and-fixed
a lot; owner will also do a manual run-through separately). CI-30 (add_item race) deferred, not blocking.
**Done & live-validated:** CI-10, CI-11, CI-18, CI-19 (+CI-23), CI-20, CI-21, CI-22, CI-24.

**CI-17 knobs (owner-confirmed, keep):** `CRIMINAL_SECONDARY_ROUNDS` = nuke 1, missile 5, rocket 5,
cluster-missile 3, shock-blast 2; `CRIMINAL_SECONDARY_MIN_DAMAGE` = 1. All in `GameConstants` (retune
in one place if balance shifts).

**Dev-guild state note:** `general_failure` (player 2) was promoted Bronze→**Silver** to test silver
criminals (which carry secondaries); demote is on a 24h tier cooldown (ends 2026-06-05 ~03:14 UTC).
Harmless (disposable alt). samx untouched (verified baseline: Micro Gun MK I, no secondaries).

**CI-16 LIVE-VALIDATED** (combat-log 51): grant→equip (whole stack cargo→ammo)→combat (fired 3×,
1 round each)→depleted→auto-unequipped; `secondary_depleted` + CI-15 "Hull depleted" events confirmed.
**CI-17 LIVE-VALIDATED** (combat-log 54): spawned silver criminals carry complete secondary loadouts
(DB-verified: Jet Rocket + AMR Tormentor nuke capped at 1 round, full combat fields); criminal Oluchi
Erland fired `missile ×3` and dealt 135 dmg in a real fight.
**Closed:** CI-1, CI-2/9, CI-3, CI-5, CI-6, CI-7, CI-10, CI-11, CI-12, CI-13, CI-14, CI-15, CI-16, CI-17, CI-18, CI-19, CI-20, CI-21, CI-22, CI-23, CI-24, CI-25, CI-26, CI-27, CI-28, CI-29.
**Open:** CI-4 (full E2E re-run §0–§8), CI-30 (add_item insert not race-safe vs new constraint — deferred).
**Watching:** `/route` reported broken (owner) — bot-core endpoint + command verified healthy; awaiting exact symptom to diagnose.
**Live-verified 2026-06-04:** CI-25 (equip secondaries), CI-27 (no false dead+ES), CI-28 (loadout embed
secondaries populated+rendered; turrets natural/not-forced), CI-29 (causal combat-log order).
**Live-verified 2026-06-05:** CI-26 (blender-service perms fixed → 15 GB assets downloaded → healthy, render API 200).
**Live-validated combat-log (battle 57):** dropdown `General_Failure vs Bartholomeu Drew`; body uses pilot/criminal
names; Engagement + first-hit-per-side + HP milestones + Outcome; each layer depleted ONCE (no flap); distinct per-side stats.
**Live-verified 2026-06-05:** CI-18 (unique constraint `uq_player_inventories_player_item` on
`player_inventories`; migration 0015; dup INSERT rejected; add_item increment path intact; full suite 4250✓).
**Stack:** full rebuild to migration **0015**, all combat/shop work LIVE & verified (all 4 containers
healthy incl. blender). **Nothing pushed** — `dev` is ahead of origin.

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

## CI-4 🟢 E2E validation — FULL re-run §0–§8  *(scope widened by owner 2026-06-05)*
Owner directive 2026-06-05: re-run the **entire** `COMBAT_E2E_TEST_PLAN.md` (§0–§8), not just the
remaining §4/§5/§6 — because we found-and-fixed a lot of issues (CI-1..CI-30) since §0–§3/§8 were last
validated, so prior green sections may have regressed/changed. Owner will ALSO do a manual full
run-through separately at some point (this automated pass is complementary, not a substitute).
Drivable via bot-core API for mechanics (exec-into-container, division-gated bounty-check — see
[[reference-combat-live-test-recipe]]); embed rendering needs Discord (gateway API for read-back).
Stack now at migration 0015, all 4 containers healthy.

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

## CI-6 ✅ `Shock Blast` inert `emp_damage=80` — WONTFIX (by design)  *(owner 2026-06-04)*
**Owner decision: leave the EMP damage stat as-is** — it comes from the wiki game data; the fact we
have no EMP mechanics yet is just happenstance, not a data error. Keep the seed value untouched. No
code/seed change. (If EMP mechanics are ever added, the value is already correct.)

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

## CI-10 ✅ `POST /api/v1/shops/refresh` serialization crash — FIXED  *(2026-06-04, committed `8049497`)*
`refresh_shop()` returns a dict with raw ORM `GuildShop` objects under `"items"` → the public
endpoint threw `PydanticSerializationError`. The admin path had the SAME latent bug (tests mocked
`refresh_shop()`, never serializing real items). Fix: `serialize_refresh_response()` now builds each
item via the canonical `ShopItemResponse` (same schema the GET shop endpoints use) so `/refresh`
returns the SAME shape as GET; applied to both public + admin endpoints. Added ORM-serialization
regression tests for both. d-developer → d-tester (PASS-w-notes) → dev follow-up (canonical serializer
+ admin test + tz-aware datetimes). Suite green (4195).

---

## CI-11 ✅ Dedicated `secondary_weapon` shop-count category — DONE & LIVE-VALIDATED  *(2026-06-04)*
**Resolved 2026-06-04; committed `aca3079` + fix `6018b1c`.** Secondaries now draw from their own
`secondary_weapon_count_range` (default {3,5}) / `secondary_weapon_quantity_range` ({2,4}) instead of
sharing the primary `weapon` key. Mirrored across all layers (GuildConfig columns + getters, migration
0014, shop_service key routing, config_service validate/unpack + compat loop, config_repository
whitelist + summary, gateway admin_config_shop params + embed). Built d-architect scope → d-developer →
d-tester (PASS: independent-range draw, deferred filter holds, both config endpoints round-trip,
no-clobber backward-compat). **⚠ Migration 0014 had a deploy-blocking bug** (JSON literal colons parsed
as bind params → `StatementError` on startup → stack stuck unhealthy at 0013) — FIXED (`6018b1c`: bind
JSON via `CAST(:val AS jsonb)`; hardened the test to drive the real `upgrade()`; SQLite tests had missed
it). **LIVE:** stack rebuilt to 0014, dev-guild row backfilled non-NULL on real PG, Silver refresh now
yields 2 primary + 2 secondary (own counts), no deferred subtypes, no crash.

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

## CI-13 ✅ Nuke key-event label fix — DONE  *(folded into CI-16, committed `e1b8f65` 2026-06-04)*
**Resolved:** the remaining actionable bit (the "miss" mislabel) is fixed — nuke/shock-blast key
events are now damage-aware (detonation/damage, not "miss"). Epicenter/damage/formula were all
confirmed correct/intended (no change). The 1-per-battle concern is subsumed by CI-16's consumable
+ cooldown model. (Full historical analysis below.)
<details><summary>(original analysis)</summary>

## CI-13 (original) 🔴 Nuke mechanics broken / mismatch intended design  *(found in retest 2026-06-03)*
Owner spec: nukes are **AoE, guaranteed-hit, inverse-square damage from epicenter, reduced
self-damage, ~1 use per battle.** Current state (resolver `combat_service.py:1400-1432`,
`_nuke_dmg` line 865, seed `AMR Tormentor`):
- ✅ guaranteed hit (no accuracy roll), ✅ AoE, ✅ reduced self-damage (`NUKE_FRIENDLY_FACTOR=0.25`).
- ✅ **Damage data PRESENT** (corrected — earlier "missing damage" was wrong): nuke damage lives in
  the `secondary_weapon.damage` column (Tormentor **150**, Extinctor **700**), and `from_player`
  (`loadout_builder.py:304,316`) correctly reads it into `WeaponStats.damage_per_shot`. Combat HAS
  the damage. NOT the problem.
- ✅ **Random epicenter is INTENDED** (owner-confirmed 2026-06-03): the nuke detonates at a
  pseudo-random point on the battlefield (`_rng.uniform`, locked-spec D5), and both ships take
  falloff damage by distance from it. "Guaranteed to hit" = always *detonates* (no accuracy roll),
  NOT guaranteed to damage the target. `/about`'s "Direct hit 150" / "self ~38" are the at-epicenter
  MAXIMUMS, not guarantees. Battle 50's four 0-dmg nukes were just unlucky rolls (all landed
  2.4–4.9km from foe, outside the 1km blast). **No change to epicenter logic.** (Possible future
  TUNING knob if nukes feel too weak: blast radius / epicenter sampling range — owner's call, not a bug.)
- 🔴 **Cooldown `loading_speed_ms=6000` (6s)** → 4 fires/29s battle; owner wants **1 per battle**
  (battle-length cooldown or 1-charge ammo model — design decision).
- 🟡 **Display:** key-event formatter (`combat_log_service.py:387`) labels nukes "miss" (assumes a
  `hit` flag nukes lack). Nukes detonate; never miss → show detonation/damage instead.
- ✅ **Formula OK:** `_nuke_dmg = dmg × (1 − d/blast)²` is CONSISTENT with the advertised numbers
  (direct hit 150 at d=0; self ~38 = 150×0.25 at d=0). Keep it (owner's "inverse-square" = this
  quadratic falloff; literal 1/d² has a singularity). No change.
**Net remaining fixes (no architect needed — epicenter/damage/formula all OK):**
(1) cooldown → 1 per battle; (2) nuke "miss" label → damage-aware ("detonated — N dmg" / "out of
range"). dev → tester. Owner decision: 1-per-battle via long cooldown vs ammo/charges.
</details>

---

## CI-14 ✅ Test agents mutated samx's (main) live loadout — RESTORED
**Resolved 2026-06-03.** Owner chose full starter restore. Done via equip/unequip/remove APIs:
samx's Betty = `Micro Gun MK I` primary, no secondary, no turrets; test items (`AMR Tormentor`,
`Nirai Impulse EX 1`) removed from inventory. **Process rule going forward: keep loadout-mutating
tests on general_failure / a disposable player, never samx.**

---

## CI-16 ✅ Secondary weapons are CONSUMABLE — DONE & VERIFIED  *(2026-06-04)*
**Resolved 2026-06-04; committed `e1b8f65`.** Sidecar `player_ships.secondary_ammo` JSON map
(migration 0013, no backfill); `ammo=None`=infinite back-compat. Single post-dispatch resolver
decrement across all 7 fire branches; `secondary_depleted` event; post-fight write-back (player
persists + auto-unequip at 0; criminal in-fight only); preflight sims never persist. Conservation
`owned = cargo + Σ secondary_ammo` upheld across equip/unequip/transfer/evacuate/reconcile/shop.
**Folds in CI-13 (nuke labels damage-aware) + CI-15 (hull layer event).** Built via d-architect
design-lock (caught 2 conservation BLOCKERs the stale plan missed — R1 ship transfer, R2 ship
sell/evacuate) → d-developer → d-tester. Tester's 1st pass found 4 defects (reconcile/transfer/
evacuate ammo-loss, slot-full top-up block, missing router wire-up); all fixed; 2nd pass PASS.
Full bot-core suite green (4127 passed); 41 secondary-ammo tests incl. paranoid conservation.
✅ **Live-validated 2026-06-04** (combat-log 51: fired 3×→depleted→auto-unequipped; CI-15 hull
event + secondary_depleted confirmed).
**LOW follow-up (tester-noted, no fix yet):** `/equip` of an already-equipped secondary with 0
cargo returns success as a silent no-op (conserves invariant; just a misleading "success"). Spec
clarification only — decide if it should error/no-op distinctly.
<details><summary>(historical — owner intent + locked design)</summary>

## CI-16 (original) 🔴 Secondary weapons are not CONSUMABLE  *(net-new mechanic — owner intent 2026-06-03)*
**Owner intent:** ALL secondary weapons are consumable — firing/using one drops the equipped
count by 1 (floor 0; "all used up"), like the EmergencySystem module. Cooldown/loading-speed
governs cadence consistently across ALL weapons. Nukes end up ~1/battle because their cooldown
should exceed the 180s max battle (fire once ready at t=0, never reload in-fight).
**Current state:** spec §348/§412 makes ONLY the EmergencySystem consumable; there is NO
ammo/count tracking for secondaries in the resolver — they fire every time cooldown allows
(infinite ammo). `player_ships.secondary_weapons` is a JSON list (qty = repeated entries).
**Nuke cooldowns are all 6–10s** (Tormentor 6000 … Liberator 10000ms) — far below the 180s
battle, so the "fires once b/c cooldown > battle" property does NOT hold (battle 50 re-fired 4×).
### LOCKED DESIGN (owner-confirmed 2026-06-03) — ready for d-architect
1. **Slot = a specific secondary weapon item ("type").** `max_secondaries` = # distinct types
   equippable; each type carries its OWN **unbounded** ammo stack. (3 different nuke models =
   3 types if you have 3 slots — type is the item, not the subtype category.)
2. **Permanent cross-battle depletion.** Firing decrements that type's qty (floor 0); persists
   across battles; restock ONLY via shop. (10 → use 3 → next fight starts at 7.)
3. **Shop buy routing:** if the type is already equipped → buy tops up the **equipped** stack;
   else → buy goes to **inventory** for the normal `/equip` flow.
4. **Equip vs restock:** `/equip` of an already-equipped type = **top-up** (no new slot);
   `/equip` of a NEW type = needs a free secondary slot.
5. **Auto-unequip at 0:** mid-fight a depleted secondary just stops firing; the auto-unequip
   (freeing the slot) is a **post-fight loadout cleanup**, not a live mid-tick mutation.
6. **Cooldowns:** keep current seed values (6–10s). Nukes limited ONLY by qty + cooldown — NO
   hard 1/battle rule. (Owner may add a "max per battle"/fire-chance RNG knob LATER for tuning.)
7. **Uniform NPC + player consumption** (in-fight). Criminals deplete in-fight, NO cross-fight
   persistence (respawn fresh). PvC player edge stays via the existing damage-reduction knob,
   not via ammo asymmetry.
8. **Cluster:** 1 round consumed per fire trigger → fires N munitions, per-munition hit roll
   (existing burst mechanics unchanged; just add the 1-round decrement).
9. **Display:** show the ammo count wherever equipped gear is shown — `/loadout` and the other
   equipped-gear surfaces (alongside primary/turret/module); combat log notes "out of ammo"
   when a secondary runs dry mid-fight.

**Architect's discretion (engineering):** storage representation + migration of the
`secondary_weapons` JSON list to a qty model; post-fight write-back from BOTH the PvC bounty and
PvP duel paths; `combat-preflight` sim must NOT persist consumption; sell-with-ammo refund
handling; locked-spec §(secondary weapons) delta. → **d-architect** to produce the plan → review → dev → tester.
**Folds into the eventual dev pass:** CI-13 nuke "miss"→detonation/damage-aware label; CI-15
hull-depleted key event.

### FINAL OWNER DECISIONS (2026-06-03) — architect plan accepted, ready for dev
- **Architect plan delivered** (sidecar `secondary_ammo` JSON map on `player_ships` + migration 0013;
  `ammo=None`=infinite back-compat; single write-back point in `fight_ships`; un-gate `secondary_weapon`).
- **#4 — NO `DEFAULT_STARTER_AMMO` / NO backfill:** the starter loadout has zero secondaries and the
  ONLY way to obtain one is a shop purchase, so no equipped secondaries ever pre-exist. Migration just
  adds the column default `{}`; ammo always originates from purchase qty. (Dropped from the plan.)
- **#2 — equip loads the whole owned stack** of that type onto the ship. ✅
- **#3 — sell REQUIRES unequip first (already enforced — PRESERVE IT).** No sell-with-ammo path; you
  `/unequip` (rounds → cargo) then `/sell` from cargo. ⚠ Loadout/inventory system is FRAGILE
  ([[feedback_loadout_inventory_fragile]]) — dev + tester must guard invariants exhaustively.
- **#1 — criminal complete loadouts → split to CI-17** (below). CI-16's resolver consumption gate is
  uniform (works for criminals IF they carry secondaries) but criminal cross-fight persistence is
  deferred to Phase-2 ("pre-damaged combat states"); criminals respawn fresh so in-fight-only is fine.
**CI-16 dev scope = PLAYER consumable secondaries (full) + uniform resolver gate + CI-13 + CI-15.**
📄 **Full ready-to-execute dev brief: [`COMBAT_CI16_PLAN.md`](COMBAT_CI16_PLAN.md)** (architect plan +
file checklist + test plan). ⚠ DANGER ZONE — dev/tester MUST read
`services/bot-core/src/services/AGENTS.md` → "Loadout & Inventory system" first.
</details>

---

## CI-17 ✅ Criminals get COMPLETE auto-generated loadouts — DONE & LIVE-VALIDATED  *(2026-06-04)*
**Resolved 2026-06-04; committed `78f9f6a`.** Purely additive (no resolver/schema/migration change).
`bounty_service.generate_loadout` now generates tier-appropriate secondaries via a subtype-aware pool
(excludes deferred subtypes + dead-weight `damage<=1`), distinct-by-name without replacement up to the
ship's `max_secondaries`, with full combat-field persistence so they fire+damage in the resolver;
`loadout_builder.from_criminal_ship` reads them back into `WeaponStats` (`damage`→`damage_per_shot`,
`rounds`→`ammo`). 4 owner-tunable balance knobs as `GameConstants` (nuke=1, missile/rocket=5,
cluster=3, shock-blast=2; min-damage=1; value once/type) — ⚠ flagged for owner review (`COMBAT_CI17_PLAN.md`).
Built d-architect (design-lock: purely additive verdict) → d-developer → d-tester (PASS: criminal
secondaries deal real damage end-to-end, no junk-subtype leakage over 500 RNG iterations, distinct
names, graceful empty, nuke cap, no reward inflation) + an added `from_criminal_ship`→`TickResolver`
regression test. Full suite green (4172). **LIVE:** combat-log 54 — criminal Oluchi Erland (Hatsuyuki)
fired `missile ×3`, dealt 135 dmg; DB-verified criminals carry Jet Rocket + nuke (capped 1 round).
Criminal cross-fight ammo persistence deferred to Phase-2 (respawn fresh; in-fight-only is correct).
<details><summary>(original intent)</summary>

## CI-17 (original) 🔵 Criminals should get COMPLETE auto-generated loadouts  *(owner 2026-06-03)*
Today the bounty criminal generator (`bounty_service.generate_loadout` + `loadout_builder.from_criminal_ship`)
produces primaries/turrets/modules but **NO secondaries**. Owner wants criminal loadouts auto-generated
as **complete** loadouts — modules, primaries, **secondaries**, turrets — bound by the ship's slot limits
and the standard unique-equip constraints, exactly like a player ("if their generated loadout has 5 nukes,
they have 5 nukes"). In-fight consumption applies (uniform CI-16 resolver gate); cross-fight persistence
for criminals is **deferred to Phase-2** (pre-damaged combat states). **Separate task** — sequence AFTER
CI-16 lands so the consumable mechanic exists first. Needs design (generation algorithm: tier/tech-level-
appropriate item selection within slot + uniqueness constraints) → architect → dev → tester.
</details>

---

## CI-15 ✅ `/combat-log` "Hull depleted" (death) event — DONE  *(folded into CI-16, committed `e1b8f65` 2026-06-04)*
**Resolved:** added the hull branch in the resolver (emits `layer_depleted{layer:hull}` when
`hull_was_positive and current_hull <= 0`, on true death after ES/clamp). The extractor's
"Hull depleted (dead)" label now receives its event, so death shows in `/combat-log` key events.
<details><summary>(original)</summary>
The resolver (`combat_service.py:821-840`) emits `layer_depleted` events for **shield** and
**armour** only — hull-zero terminates the fight (fight_end) without a `layer_depleted{layer:hull}`
event. The extractor is already ready (`combat_log_service.py:49` has `"hull": "Hull depleted (dead)"`)
but never receives one, so the death milestone is absent from `/combat-log` key events (battle 50
showed only "Armour depleted", not the hull death). Fix: add a hull branch in `_apply_damage`
mirroring the shield/armour emission (emit when `hull_was_positive and current_hull <= 0`).
(Shield-absence for a no-shield ship is correct; armour-depleted already works.)
</details>

---

## CI-18 ✅ DB unique constraint on `player_inventories` — DONE & LIVE-VALIDATED  *(2026-06-05, committed `c837fe3` + `0b06446`)*
Added `UniqueConstraint(player_id, item_type, item_name)` named `uq_player_inventories_player_item`
via **both** ORM model `__table_args__` (`player_inventory.py`) **and** Alembic migration **0015**
(`0015_ci18_player_inventory_unique.py`) — both required because fresh installs build tables from
`Base.metadata` (ORM-driven), not `op.create_table`; model-only or migration-only → schema drift.
Migration is inspector-guarded (no-ops if constraint already present, e.g. fresh DB) and includes a
defensive merge-quantities dedup (keep lowest id, SUM quantities, delete rest) BEFORE creating the
constraint so it can never crash a boot-loop on a dirty DB. App-code (`add_item` get-then-increment)
left unchanged per scope. architect→dev→tester full cycle. Tester PASS: live `\d` shows the
constraint, raw dup INSERT rejected, dedup SQL traced correct on a 3-row case, normal `add_item`
increment path still works end-to-end on the constrained DB (no regression), full suite **4250
passed / 1 skipped**. Deployed: `alembic_version` = **0015**. 6 Postgres-backed migration tests (real
`upgrade()`/`downgrade()` via importlib — NOT re-implemented inline; runs on PG, not SQLite).

**Follow-up logged → CI-30** (out of scope here): now that the constraint exists, the pre-existing
`add_item` get-then-increment TOCTOU race fails *loudly* (`IntegrityError`) under truly concurrent
same-key inserts instead of silently duplicating — harden with `ON CONFLICT DO UPDATE` / catch +
retry. Also still noted: `transfer_item_between_players` bypasses the LoadoutConsistency choke point
(safe ONLY because both legs are cargo-only).

---

## CI-30 🔵 `add_item` insert path not race-safe against the new unique constraint  *(tester-flagged 2026-06-05)*
`InventoryRepository.add_item` / `create_or_update` do get-then-increment with no `IntegrityError`
handler. With CI-18's constraint live, two concurrent callers for the same
`(player_id, item_type, item_name)` that both read `None` will have the 2nd insert raise
`IntegrityError` and propagate to the caller (previously: silent duplicate — worse, but quiet). Low
probability (requires true concurrent same-key inserts). Fix: `ON CONFLICT DO UPDATE SET quantity =
quantity + EXCLUDED.quantity` (upsert) or catch `IntegrityError` → re-get → increment, plus a
concurrent-insert test. Deferred; not blocking.

---

## CI-19 ✅ Discord autocomplete misbehaving — FIXED & LIVE-VALIDATED  *(2026-06-04, committed `908440e`)*
**Root cause (d-architect):** `bot.py:125` initialized the autocomplete-state HTTP client from
`os.getenv("BOT_CORE_URL", "http://bot-core:8000/api/v1")` — but `BOT_CORE_URL` is NEVER set, so it
fell back to dead port 8000 (bot-core is on 18000 via `BOT_API_BASE_URL`). Every autocomplete cache
refresh + warm job failed with ConnectError → player/inventory/ships caches never populated, EXCEPT
`player_cache` which `/profile` writes via its own working client (→ "rerun /profile to fix it";
equip/unequip/ship/set-active stayed broken even after, needing inventory/ships caches). **Fix:** read
`BOT_API_BASE_URL` (canonical var all cogs use) + non-fatal startup health probe + documented in
cogs/AGENTS.md. **LIVE-VALIDATED:** after rebuild, gateway logs show `_preload_static_catalogs` loaded
all catalogs and `warm_guild_players: Stage 1+2 complete — players loaded` (no ConnectError) — caches
populate WITHOUT /profile. d-architect → d-developer.
**Follow-up (minor, fold into next gateway pass):** the one-shot health probe fires ~3s into startup,
before bot-core is ready on a full `--force-recreate`, logging a misleading ERROR even though warm jobs
retry+recover. Make the probe retry (mirror the cog preload retry) so it only ERRORs on genuine
unreachability. **Cleanup:** junk player id 3 (fake discord_id 421321999791095808) created during
architect probing — inert but pollutes the dev guild player list; remove via a proper admin path.

## CI-23 ✅ Secondaries missing from `/equip` autocomplete — FIXED  *(folded into CI-19, `908440e`)*
`_CURRENTLY_EQUIPPABLE_INVENTORY_TYPES` excluded `secondary_weapon`, so the now-buyable/equippable
secondaries (CI-5/CI-16) never appeared in `/equip` autocomplete. Added `secondary_weapon` to the set
(equip flow already auto-detects + handles secondaries). Catalog preload confirms 30 secondary_weapon
items load. (Tester-flagged latent issue from CI-19 investigation.)

---

## CI-20 ✅ `/combat-log` name-based identity — DONE & LIVE-VALIDATED  *(2026-06-04, `c99fd3e`+`d1f4e75`)*
Dropdown now renders full "`<criminal>` vs `<player>`" (battle 57: "General_Failure vs Bartholomeu Drew");
log body attributes events by pilot/criminal name (readable in same-ship fights); summary shows
name=pilot, ship=ship. Threaded display names + a per-event `data["side"]` slot discriminator through the
resolver (actor/name stays = ship name → stats untouched); old rows fall back to ship-name (72h retention,
no migration). Last-mile fix `d1f4e75`: exposed `combatant1_name`/`combatant2_name` on the list schema
(Pydantic was dropping them). Part of the combat-log UX batch (`COMBAT_CL_UX_PLAN.md`).
<details><summary>(original)</summary>
Two related problems, both about telling combatants apart:
1. **Dropdown:** a bounty-capture battle shows as e.g. `#1 vs betty` — should use the **bounty
   (criminal) name vs player name**, the SAME style as a PvP duel (`<criminal> vs <player>`).
2. **The log body itself:** when both combatants fly the SAME ship (e.g. both "Betty"), it's
   impossible to tell who is who — actors are labeled by ship name. Use criminal-name / player-name
   (or otherwise disambiguate) so each line clearly attributes to the right side. (See battle #56
   example below — "Betty (Betty)" + "H'Soc (H'Soc)".) → architect scope w/ CI-21/CI-22 (same subsystem).
</details>

---

## CI-21 ✅ `/combat-log` regen-flapping de-spam — DONE & LIVE-VALIDATED  *(2026-06-04, `c99fd3e`)*
Emission-side recovery latch: a layer re-emits `layer_depleted` only after recovering ≥
`COMBAT_LAYER_REEMIT_FRACTION` (0.25, env-tunable) of max. Battle 57: each layer (Armour/Shield/Hull)
depleted exactly ONCE — no flap (vs battle #56's 5×). Safe: `layer_depleted` has no summary consumer
(stats byte-identical, regression-guarded). Applies to shield regen + repair-bot armour/hull.
<details><summary>(original)</summary>
A layer that regenerates a tiny amount (e.g. shield regen ~1 HP) between shots and is then re-depleted
emits a **`layer_depleted` event every time** → the log shows "Shield depleted" many times in one
fight (battle #56: 5× "Shield depleted"). Same situation expected for **armour regen when a repair
bot/repair beam is equipped**. Owner wants these gated behind a **threshold** (don't re-emit a
layer-depleted unless the layer meaningfully recovered first, or rate-limit/dedupe consecutive
depletions of the same layer). → architect scope w/ CI-20/CI-22.
</details>

---

## CI-22 ✅ `/combat-log` baseline events for both sides — DONE & LIVE-VALIDATED  *(2026-06-04, `c99fd3e`)*
Display-side synthesis: Engagement line + first-hit-per-side + Outcome line + per-side 50%/25% HP
milestones (owner chose richer "baseline + milestones"). Battle 57 shows BOTH combatants (first hit
for each, milestones for each, correct Outcome) — the outmatching/winning side is no longer inert.
Outcome winner resolved by final-hull SLOT (fixed a same-ship c2-wins mislabel found by d-tester).
Events tick-sorted before field truncation.
<details><summary>(original)</summary>
In battle #56 the criminal **H'Soc has ZERO key events** — it out-matched the player, never had a
layer depleted, hit no booster/cloak threshold, fired only primaries → nothing surfaced. Owner finds
this odd from a player's view (looks like one side did nothing). Want some **baseline events for BOTH
combatants** even absent threshold hits — e.g. an opening/first-blood/engagement line, periodic
status, or a per-side summary line — so both sides are represented in the timeline. → architect to
design what baseline events make sense (balance signal-vs-noise with CI-21's de-spam goal).

### Battle #56 reference (owner-pasted) — illustrates CI-20/21/22
```
Battle #56 — Bounty — WON   |   H'Soc (H'Soc) HP 400→356, acc 67%, dmg 200 (PvC DR 33%)
                                Betty (Betty) HP 185→0, acc 33%, dmg 47   |  Dur 10.7s, Winner H'Soc
Key Events:
 5.7s Betty fired Edo — miss
 6.2s Betty: Shield depleted   } 
 6.9s Betty: Shield depleted   }  CI-21: regen-flapping spam
 6.9s Betty: Armour depleted
 7.7s Betty fired Edo — miss
 7.7s Betty: Shield depleted   }
 8.4s Betty: Shield depleted   }
 9.2s Betty: Shield depleted   }
 9.7s Betty fired Edo — hit
10.7s Betty: Shield depleted   }
10.7s Betty: Hull depleted (dead)
 (CI-22: H'Soc — the winner — has NO events at all)
 (CI-20: both sides labeled "<ship> (<ship>)"; dropdown was "#1 vs betty")
```
</details>

---

## CI-24 ✅ Fight summary slot-keying (same-ship stat merge) — DONE & LIVE-VALIDATED  *(2026-06-04, `c99fd3e`)*
Re-keyed `_build_fight_summary` accumulators on combatant SLOT instead of ship name, so same-ship fights
no longer merge per-side dmg/accuracy/shots. Different-ship summaries byte-identical (regression-guarded);
same-ship now distinct. Battle 57 (diff ships) confirmed distinct per-side stats. Folded into the
combat-log UX batch per owner.
<details><summary>(original)</summary>
## CI-24 (original) 🟢 Fight summary merges per-side stats when both fly the same ship  *(found in CI-20 scoping; owner: FOLD IN 2026-06-04)*
`_build_fight_summary` (`combat_service.py:965-1007`) keys per-combatant accumulators (dmg dealt,
accuracy, shots) on **ship name** (`c1.name`/`c2.name`). When both combatants fly the SAME ship name
(e.g. both "Betty"), the dict collapses → both sides' stats merge into one bucket = wrong per-side
numbers. The win/loss header is unaffected (read path derives it from final HP). Owner chose to FOLD the
fix into the combat-log UX batch: re-key the summary on combatant **slot** (1/2). Plan: `COMBAT_CL_UX_PLAN.md`.
Regression guard: single-name fights byte-identical; same-name fights show DISTINCT per-side stats.
</details>

## CI-25 ✅ Equip/unequip of a SECONDARY rejected by schema regex — FIXED & LIVE  *(owner-reported 2026-06-04, `7b54702`)*
Equipping a secondary via Discord failed: `equipment_type: String should match pattern
'^(weapons|modules|turrets)$'`. The service layer already supported `secondary_weapons`
(`equipment_service.VALID_EQUIPMENT_TYPES` + slot/item-type maps), but the `EquipItemRequest` /
`UnequipItemRequest` Pydantic patterns were stricter and rejected it before reaching the service —
exposed once CI-23 surfaced secondaries in `/equip` autocomplete. Added `secondary_weapons` to both
patterns (still enumerated; invalid values still 422). **LIVE-VERIFIED:** equip w/ equipment_type=
secondary_weapons → 200 (ammo seeded); invalid value → 422. Regression tests added (4237 green).

## CI-26 ✅ blender-service down — data-dir mount permission — FIXED & LIVE  *(2026-06-05)*
**Root cause:** the bind-mounted `/app/data` was owned `root:root` 0755 from the blender container's
view (its mount namespace ≠ this admin container's `/proj` view — a marker written to my
`/proj/mappings/blender-renderer` did NOT appear in the container), so `botuser` (uid 1001) couldn't
`mkdir /app/data/game-objects` → startup aborted before any download.
**Fix:** `sudo docker exec -u 0 bountydev-blender-service chown -R 1001:1002 /app/data && chmod -R 0775
/app/data` (chowning the bind mount from inside as root changes the underlying dir; PERSISTS across
`--force-recreate`). Then full redeploy. After the fix, blender proceeded to download its one-time
**15.2 GB** game-objects archive from Google Drive via gdown (~7–8 min; lands in `/tmp/...7z.part` then
extracts to 309 textures / 15 GB), launched, and is now **healthy** — render API serves 200
(`/api/v1/config/render`, `/docs`, `/openapi.json`), reachable from bot-core on `:18001`.
**Recurrence note:** if the blender data volume is ever wiped or its ownership reverts to root, repeat
the `chown` and expect the 15 GB re-download. Affects ship-skin renders only.

## CI-27 ✅ "Hull depleted (dead)" + Emergency System on same tick — FIXED  *(owner 2026-06-04, `5b4501c`)*
The CI-15 hull `layer_depleted` ("dead") event was emitted inside `_apply_damage` (Phase 3) the moment
hull hit ≤0, BEFORE `_eval_emergency_system` (Phase 4a) clamps hull→1 to cheat death → an ES-saved ship
still logged "dead" + "Emergency System activated" same tick. Moved the hull-death emit to Phase 8
termination (true death, post-ES; ES-saved ships have hull=1 so are never `c_dead`). Shield/armour emits
+ ES/clamp/termination logic untouched. 6 new tests incl. the ES-saves-no-dead repro. 4243 green.

## CI-28 ✅ Criminal-loadout embed missing turrets + secondaries — FIXED  *(owner 2026-06-04, `3bac5dc`+`5dc4bf9`)*
TWO bugs: (1) the gateway `build_loadout_embed` only rendered Primary Weapons + Modules → added
**Turrets + Secondaries sections** (N/M headers, secondary ×N round counts, truncation-budget aware,
suppressed when empty; 15 tests; `3bac5dc`). (2) the bot-core `/bounties/{id}/loadout` response never
**populated** `secondaries` (`build_bounty_loadout`/`build_player_loadout` set weapons/turrets/modules
but not secondaries) → response returned `secondaries:[]` even when present, so the embed had nothing
to show → fixed to populate secondaries from `criminal_ship["secondaries"]` (criminal) and
`secondary_weapons`+`secondary_ammo` (player), with `rounds` on `LoadoutWeaponItem`; 4 tests; `5dc4bf9`.
**Turrets NOT forced** (owner 2026-06-04): a turret-capable ship with no turret due to TL-gating is
correct — turrets appear only when tier + availability (turret items are TL5+) + the stock RNG selection
naturally align (high-tier bounties). An interim forced-fill fallback was built then REVERTED per owner.

## CI-29 ✅ Combat-log key events out of causal order — FIXED  *(owner 2026-06-04, `3bac5dc`)*
`_extract_key_events` sorted same-tick events by `(tick, event_type)` — the alphabetical event_type
secondary key scrambled causal order (e.g. "Secondary depleted" before "Secondary fire" → ran-out
printed before the shot that depleted it; whole tick looked ~inverted). Fixed to sort by tick ONLY
(stable sort preserves the resolver's causal inline emission order). Engagement stays first, Outcome
last. Regression test fails pre-fix / passes post-fix.

## CI-8 🧹 Housekeeping — committed status / nothing pushed
- ✅ `COMBAT_E2E_TEST_PLAN.md` + `COMBAT_ISSUES.md` committed (`ae157f0`).
- ✅ `/combat-log` feature fully committed (backend router/schema/service + tests were
  orphaned by independent agent commits; gathered into one feat commit).
- Still untracked **by intent**: `AUDIT_REPORT.md`, `COMBAT_RECONCILIATION.md`.
- Branch `dev` is ahead of origin; **nothing pushed** (commit-freely / don't-push).
- Lint/format gates green throughout.
