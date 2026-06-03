# Combat Phase-1 — Open Issues & Follow-ups

*Tracking log for issues found during E2E validation of the combat workstream.
Created 2026-06-03. Status key: 🔴 open · 🟡 needs decision · 🟢 in-flight · ✅ done.*

Dev env: guild `699744305274945650`; players **samx** (player 1, user `402296276617527306`)
+ **general_failure** (player 2, user `970691862035841048`). Read Discord via the
gateway API (`docker exec bountydev-discord-gateway curl localhost:17999/api/v1/...`).

---

## CI-1 🔴 Criminals on certain ships deal 0 DPS in combat  *(HIGH — gameplay-breaking)*
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

**Next action:** d-developer to trace; reproduce live via `POST /api/v1/bounties/check`
and inspect `combat_result.ship2_stats.raw_dps` + the criminal combatant the resolver
actually builds. → then d-tester.

---

## CI-2 🟡 `HP: 135 → 135` dead arrow in combat summary  *(LOW — cosmetic)*
The `raw → varied` arrow is a leftover from the **variance system removed in T10**;
both numbers are now always identical, so the `→` is meaningless and looks broken.
**Fix:** drop the arrow (show a single HP value) in `bountyCog._format_combat_summary`
and the duel result embed (`duelCog`). Gateway-only change.

---

## CI-3 🟢 `/combat-log` feature — awaiting d-tester pass
New `/combat-log` command (mandatory battle select w/ ownership-gated autocomplete;
detail = summary + key events: secondary fires, module activations, HP-layer
milestones). Built by d-developer: 52 bot-core + 14 gateway tests green, ownership
gate (404 for non-combatant) verified live. **Uncommitted.**
**Next action:** d-tester adversarial review → fix/retest → commit.

---

## CI-4 🟢 E2E validation — §4/§5/§6 not yet run
§0–§3 + §8 complete (PvC/PvP/persistence/data all ✅, see `COMBAT_E2E_TEST_PLAN.md`).
Remaining: per-weapon equip-and-fight (§4), modules (§5), edge cases (§6). Runnable
now that the env is restored. Can be driven via bot-core API for mechanics; embed
rendering needs Discord.

---

## CI-5 🟡 Non-canonical secondary subtypes are Phase-1 no-ops  *(decision)*
`emp-bomb`, `mine`, `sentry-gun` subtypes (~9 seeded items: `EMP GL *`,
`AMR Saber`/`Ksann'k`/`Neétha EMP`, `Berger SG-*`/`T'Suum`) do **nothing** in a fight
(`combat_service.py:1489` — deferred). If they're shop-purchasable they're dead weight.
**Decision needed:** shop-exclude, or document as a known limitation. (`ionizing-missile`
IS handled; the canonical 5 all work.)

---

## CI-6 🟡 `Shock Blast` seed carries `emp_damage=80` that is inert  *(LOW)*
Shock-blast resolves as distance-push only in Phase-1; the 80 EMP value is unused.
Behaviour matches `COMBAT.md`; just a stale/unused seed value to reconcile.

---

## CI-7 🟡 `/admin_uninstall` leaves orphaned `bounty` + `combat_log` rows  *(LOW)*
After a guild nuke/uninstall, `users/players/player_ships/player_inventories/
guild_configs/guild_shops` are cleared but `bounty` (2) and `combat_log` (2) rows
survive, orphaned. Confirm whether uninstall *should* clean these; if so, it's a
cleanup-cascade gap. (Not a rebuild/persistence bug — volume persists fine.)

---

## CI-8 🧹 Housekeeping — uncommitted artifacts / nothing pushed
- Untracked: `COMBAT_E2E_TEST_PLAN.md`, `COMBAT_ISSUES.md` (this file),
  `AUDIT_REPORT.md`, `COMBAT_RECONCILIATION.md`.
- `/combat-log` code uncommitted (commit after CI-3 tester pass).
- Branch `dev` is ahead of origin; nothing pushed (commit-freely / don't-push).
- Lint/format gates currently green.
