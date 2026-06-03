# Combat Phase-1 — Open Issues & Follow-ups

*Tracking log for issues found during E2E validation of the combat workstream.
Created 2026-06-03. Status key: 🔴 open · 🟡 needs decision · 🟢 in-flight · ✅ done.*

Dev env: guild `699744305274945650`; players **samx** (player 1, user `402296276617527306`)
+ **general_failure** (player 2, user `970691862035841048`). Read Discord via the
gateway API (`docker exec bountydev-discord-gateway curl localhost:17999/api/v1/...`).

---

## ▶ RESUME HERE (post-compaction 2026-06-03)
**Next major phase = build CI-16 (consumable secondary weapons).** Full ready-to-execute dev brief:
**[`COMBAT_CI16_PLAN.md`](COMBAT_CI16_PLAN.md)**. Run d-developer → d-tester (one subagent at a
time). ⚠ It's in the loadout/inventory **danger zone** — dev+tester MUST read
`services/bot-core/src/services/AGENTS.md` → "Loadout & Inventory system" and test invariants
exhaustively ([[feedback_loadout_inventory_fragile]] in memory).
**Then, in order:** CI-17 (criminal complete loadouts — needs architect first) → CI-6 (Shock Blast
inert EMP) → CI-10 (`/shops/refresh` crash) → CI-11 (shop weapon-density tuning) → CI-18 (player_inv
unique constraint) → CI-4 (live §4/§5/§6 weapon/module/edge E2E tests).
**Closed this session:** CI-1, CI-2/9, CI-3, CI-5, CI-7, CI-12, CI-14.
**Stack:** rebuilt & healthy (combat fixes live). **Nothing pushed** — `dev` is ~30 commits ahead.

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

---

## CI-14 ✅ Test agents mutated samx's (main) live loadout — RESTORED
**Resolved 2026-06-03.** Owner chose full starter restore. Done via equip/unequip/remove APIs:
samx's Betty = `Micro Gun MK I` primary, no secondary, no turrets; test items (`AMR Tormentor`,
`Nirai Impulse EX 1`) removed from inventory. **Process rule going forward: keep loadout-mutating
tests on general_failure / a disposable player, never samx.**

---

## CI-16 🔴 Secondary weapons are not CONSUMABLE  *(net-new mechanic — owner intent 2026-06-03)*
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

---

## CI-17 🔵 Criminals should get COMPLETE auto-generated loadouts  *(owner 2026-06-03)*
Today the bounty criminal generator (`bounty_service.generate_loadout` + `loadout_builder.from_criminal_ship`)
produces primaries/turrets/modules but **NO secondaries**. Owner wants criminal loadouts auto-generated
as **complete** loadouts — modules, primaries, **secondaries**, turrets — bound by the ship's slot limits
and the standard unique-equip constraints, exactly like a player ("if their generated loadout has 5 nukes,
they have 5 nukes"). In-fight consumption applies (uniform CI-16 resolver gate); cross-fight persistence
for criminals is **deferred to Phase-2** (pre-damaged combat states). **Separate task** — sequence AFTER
CI-16 lands so the consumable mechanic exists first. Needs design (generation algorithm: tier/tech-level-
appropriate item selection within slot + uniqueness constraints) → architect → dev → tester.

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

## CI-18 🔵 No DB unique constraint on `player_inventories` (latent fragility)  *(architect-flagged 2026-06-03)*
There is NO `UniqueConstraint(player_id, item_type, item_name)` on `player_inventories` — the
one-row-per-item property is upheld solely by `InventoryRepository.add_item` (`get_player_item` →
increment). Concurrent/direct inserts could create duplicate rows; `sell_item` even comments "should
be exactly 1 row." Consider a `UniqueConstraint` migration to harden it. Low urgency; documented in
`services/AGENTS.md`. Also noted: `transfer_item_between_players` bypasses the LoadoutConsistency
choke point (safe ONLY because both legs are cargo-only — must route through it if extended to equipped gear).

---

## CI-8 🧹 Housekeeping — committed status / nothing pushed
- ✅ `COMBAT_E2E_TEST_PLAN.md` + `COMBAT_ISSUES.md` committed (`ae157f0`).
- ✅ `/combat-log` feature fully committed (backend router/schema/service + tests were
  orphaned by independent agent commits; gathered into one feat commit).
- Still untracked **by intent**: `AUDIT_REPORT.md`, `COMBAT_RECONCILIATION.md`.
- Branch `dev` is ahead of origin; **nothing pushed** (commit-freely / don't-push).
- Lint/format gates green throughout.
