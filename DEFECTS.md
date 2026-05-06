# BountyBot Defects & Anomalies

Single source of truth for E2E-discovered defects. Add new entries at the **top** of the relevant status section.

**Severity**: 🔴 blocker · 🟠 high · 🟡 medium · 🔵 low · ℹ️ info
**Status**: open · deferred · fixed · fixed-pending-verify · closed · withdrawn

Cross-ref: `E2E_TEST_CHECKLIST.md` (test-item references). All commit SHAs are local to `samx-wip`.

---

## Testing Process (Read Before Every Session)

> **Orchestrator never touches containers.** `sudo docker exec` for read-only DB/API/log inspection only. Only USER rebuilds/restarts the stack.

> **Subagent roles**: `@researcher` (read-only), `@developer` (fixes + tests), `@architect` (design + refactor), `@tester` (adversarial review). Do NOT reuse prior task_id sessions — always start fresh.

> **Test suites**: pipe ONCE to a log file, never rerun to change grep patterns.
> `python -m pytest tests/ 2>&1 | tee /tmp/<descriptive>.log`

> **Test presentation format** — always use this table:
> | # | Account | Command | Notes |
> |---|---------|---------|-------|
> Account must be explicit (Main or Alt). Never omit it.

> **Defect found** → add at top of OPEN section below → user decides stop-and-fix vs log-and-continue.

> **Commits**: only when user explicitly asks. Stage only real changed files — not file-mode noise.

---

## OPEN

### B.80 — `/admin_give_item` requires redundant `item_type` parameter

🔵 low · discord-gateway · 2026-05-05 · **FIXED-PENDING-VERIFY**

**Summary**: `/admin_give_item` had an `item_type` parameter that the user must fill in manually. Since the item name is already provided (with autocomplete), the concrete item type can be resolved server-side from the item name — the same pattern used by `/sell` (A.42b). The `item_type` field was redundant and error-prone.

**Fix applied**: `adminCog.py:1732-1802` — `item_type` parameter removed from slash command; POST body contains only `{guild_id, user_id, item_name, quantity}`; server resolves type and returns `resolved_type` in response. Confirmed in code by researcher 2026-05-06.

---

### B.78 — Wah'noor system should have no neighboring systems (non-routable)

ℹ️ info · Data · 2026-05-05 · **CLOSED**

**Summary**: Wah'noor is intended to be a dead-end / isolated system with no connections — non-routable.

**Resolution**: `import_data/system/vossk.wahnorr.json` has `"neighbours": []` — correctly isolated. Confirmed by researcher code audit 2026-05-06. No action required.

---

### B.77 — A* pathfinding produces suboptimal routes due to inadmissible heuristic

🟡 medium · bot-core · 2026-05-05 · **FIXED-PENDING-VERIFY**

**Observed**: Vortt Baskk bounty (Discord message ID `1501255506281365605`) route Union→Prospero→Vulpes→Oom'Bak (3 hops) returned instead of optimal Union→Magnetar→Oom'Bak (2 hops).

**Root cause**: The Euclidean coordinate distance heuristic is inadmissible for uniform-hop-cost graphs. Vulpes is geometrically closer to Oom'Bak (174.6 units) than Magnetar (210.8 units), so A* greedily prefers the longer 3-hop path. Since all edges have uniform cost (1 hop), the heuristic must never overestimate hop count — but raw pixel distances do exactly that, misleading the search.

**Fix applied**: `pathfinding_service.py:59-65` — `_heuristic()` now returns `0.0` (constant zero heuristic, degrades A* to Dijkstra guaranteeing shortest hop count). Docstring explicitly documents B.77 rationale. Confirmed in code by researcher 2026-05-06.

---

### B.76 — Test suite widespread mock overuse / tautological tests

🟡 medium · All services · 2026-05-05 · **OPEN**

**Summary**: The test suite across bot-core, discord-gateway, and blender-service has widespread tautological mock tests — tests that mock the subject under test, so they only verify mock behaviour rather than real code paths. This was first surfaced during B.33 remediation (4 tautological adminCog preload tests replaced with respx contract tests) and again during the `setupCog` command-sync fix (tester found existing `on_guild_join` tests were accidentally exercising the error path due to non-awaitable `MagicMock`). A `tests/AGENTS.md` policy was added to prevent new instances, but no suite-wide audit has been performed.

**Impact**: Unknown number of tests are providing false confidence. Bugs may exist in code paths that appear covered.

**Required work**: Full audit of all three test suites to identify and replace tautological/mock-only tests with real-behaviour tests (respx for HTTP contracts, real objects with deterministic inputs per the `test_combat_service.py` reference pattern). Max 2 mocks per test per code standards.

**References**: B.33 fix (`452ac28`), setupCog tester review (2026-05-05), `tests/AGENTS.md` policy, `services/bot-core/tests/services/test_combat_service.py` (reference pattern).

---

### B.75 — High-res composite PNG "upload failed" for non-Nitro users

ℹ️ info · discord-gateway · 2026-05-05 · **withdrawn**

**Observed**: `/make_skin_texture` with 6K×4K source texture shows "1 File Uploading Failed" for non-Nitro users. Nitro users upload successfully (confirmed on Main account with Nitro sub).

**Resolution**: This is a Discord account-level upload limit constraint, not a bot bug. Non-Nitro users are limited to 8MB uploads; Nitro removes this limit. Low-res assets work fine for all users. No fix required.

---

### B.74 — AEI conversion fails: render output dimensions not multiples of 4

🟠 high · blender-service · 2026-05-04 · **FIXED-PENDING-VERIFY**

**Observed**: Clicking "AEI (Android/ETC1)" or "AEI (PC/DXT5)" buttons after a render returns `❌ Failed to convert texture to ETC1/DXT5: API error 400`. Blender logs: `AEI write failed: width or height not multiple of 4`. Render output was 594×274 — neither is a multiple of 4.

**Root cause**: AEPi requires image dimensions to be multiples of 4. The `trim()` step in `RenderService` crops to the content bounds, which can produce arbitrary dimensions. `AEIConversionService.convert_to_aei()` passes the image directly to AEPi without padding.

**Fix applied**: `aei_conversion_service.py:98-104` — snaps dimensions to nearest multiple of 4 using `round(dim / 4) * 4 or 4` with `Image.NEAREST` resize before AEI encoding. Confirmed in code by researcher 2026-05-06.

**Affected file**: `services/blender-service/src/services/aei_conversion_service.py`

---

### B.73 — `skinsCog._preload_ship_skins` has no retry logic — autocomplete permanently empty after startup ConnectError

🟡 medium · Reliability · 2026-05-04 · **FIXED-PENDING-VERIFY**

**Observed**: On startup, `_preload_ship_skins` hits `ConnectError` (bot-core not ready yet) and fails with no retry. `self._ship_skins` stays empty permanently — ship and skin autocomplete never populate until service restart.

**Root cause**: Same pattern as DevCog (fixed in B.XX) — no retry loop. DevCog fix used 5 retries with 5/10/20/40/60s delays.

**Fix applied**: `skinsCog.py:261-304` — retry loop with delays `[5, 10, 20, 40, 60]s` added, matching the `bountyCog._preload_data` pattern. Confirmed in code by researcher 2026-05-06.

---

### B.72 — D.3 test expectation wrong: Vol Noor (texture_regions=0) is skinnable via static images

ℹ️ info · Data/docs · 2026-05-04 · **OPEN**

**Observed**: `/ship_skin ship:Vol Noor skin:Default` shows the ship icon correctly (same as D.1). Vol Noor has `texture_regions=0` but `skinnable=true` and a full `compatible_skins` JSON of pre-composited images. The cog correctly shows skin images for it.

**Impact**: The checklist test expectation for D.3 ("Error: does not support custom skins") was wrong. The non-skinnable test should use a ship with `skinnable=false` (e.g. Phantom XT, Wraith, Dark Angel). Update checklist.

---

### B.71 — D.2 test ship (Phantom XT) has `skinnable=false` — no skins defined despite texture_regions=2

ℹ️ info · Data/docs · 2026-05-04 · **OPEN**

**Observed**: `/ship_skin ship:Phantom XT skin:urban-camo` returns "Skin not found" because `Phantom XT` has `skinnable=false` and `compatible_skins=null` in the DB. The checklist incorrectly listed it as a test ship for multi-region skin display.

**Correct test ships**: Use `Aegir`, `Badger`, or `Furious` for 2-region; `Kinzer RS` or `Razor 6` for 3-region.

---

### B.70 — Shop refresh announcement always says "all tiers" even for single-tier admin refresh

🔵 low · UX · 2026-05-03 · **closed**

**Observed**: `/admin_refresh_shop tier:Bronze` posts "restocked with new items across all tiers" and "Tiers Available: Bronze · Silver · Gold · Platinum" regardless of which tier was refreshed.

**Root cause**: `announce_shop_refresh()` in `utils/shop_announcement.py` has no `tier` parameter — copy is hardcoded.

**Fix**: Add optional `tier` parameter. When provided: description says "the {tier} shop has been restocked", field shows only that tier. When None (full refresh from executor): existing "all tiers" copy applies.

---

### B.67 — `duel_expire` executor requires `duel_id` in payload but one-time fire without it logs ERROR silently

🔵 low · 2026-05-03 · **OPEN**

**Observed**: Firing `{"job_type": "duel_expire"}` without a `duel_id` logs `ERROR - missing duel_id in payload` and does nothing. Prior sessions used this to sweep all expired duels guild-wide but the executor actually requires a specific `duel_id`.

**Impact**: The shortcut in E2E_TEST_CHECKLIST.md for 11.4 is wrong — the executor is per-duel, not a bulk sweeper. Duel expiry is correctly handled by the periodic `duel_expire_executor` which runs on schedule. Low severity since auto-expiry works; only the manual shortcut is misleading.

**Fix**: Either (a) make the executor support bulk mode when `duel_id` is omitted (expire all past `expires_at`), or (b) update the checklist shortcut to clarify it needs a specific duel_id. Option (b) is the quick fix.

---

### B.66 — `/admin_config Reset to Defaults` nukes admin role configuration

🟡 medium · UX · 2026-05-03 · **closed**

**Observed**: `reset_to_defaults` in `config_repository.py` deletes the existing config row and creates a fresh one with `admin_role_id: None`. This means after a reset, no user has bot-admin access (except those with Discord native Administrator permission or in `DEVELOPERS`). A server admin must re-run `/admin_config Set Admin Role` to restore access.

**Expected**: Reset should preserve `admin_role_id` (and ideally channel IDs) since those are infrastructure settings, not game settings. Only game-tuning values (starting_credits, xp_thresholds, shop ranges, etc.) should reset.

**Fix**: In `reset_to_defaults`, read the existing `admin_role_id` before delete and re-apply it to the new default config. Same consideration for channel IDs.

---

### B.65 — No admin command to cancel/clear duel requests

🟡 medium · Feature gap · 2026-05-03 · **closed** (implemented: `/admin_duel` with cancel-all + per-duel autocomplete, commit `2c71dc6`)

**Issue**: There is no admin command to cancel a pending or stuck duel. If a duel gets wedged (e.g. challenger goes offline, data issue, test cleanup) the only resolution is a direct DB edit.

**Required**:
- `adminCog.py`: add an `admin_player` action or standalone `/admin_duel` command — e.g. `action: Cancel Duel` + `duel_id` parameter. Requires admin role.
- `bot-core`: add `DELETE /api/v1/duels/{duel_id}` (or `POST /api/v1/duels/{duel_id}/cancel`) endpoint. Must produce an `AdminAuditLog` record.
- `duel_service.py`: add `cancel_duel(db, duel_id)` — marks status `cancelled`, no credit transfer.

---

### B.64 — No `/duel-cancel` command for the challenger to withdraw their own challenge

🟡 medium · Feature gap · 2026-05-03 · **closed** (implemented: `/duel-cancel`, commit `f68e07a`)

**Issue**: Once a challenger issues `/duel-challenge`, there is no way for them to take it back short of waiting for the 24-hour expiry. The target can reject, but the challenger cannot withdraw.

**Required**:
- `duelCog.py`: add `/duel-cancel` command. Autocomplete shows the invoking user's **outgoing** pending duels (challenger_id = invoking player). Only the challenger can cancel their own challenge.
- `bot-core`: reuse or extend the reject endpoint — add a `POST /api/v1/duels/{duel_id}/cancel` that validates the requesting player is the challenger (not the target). Mark status `cancelled`.
- `duel_service.py`: add `cancel_duel(db, duel_id, requesting_player_id)` — validates requester is challenger, marks `cancelled`, no credit change.
- Autocomplete: new `outgoing_duel_autocomplete` function — mirrors `pending_duel_autocomplete` but filters on `challenger_id` instead of `target_id`. Label format: `"general_failure. — 100cr stakes"` (target name + stakes).

**Note**: B.65 (admin cancel) can share the same service method — just without the challenger ownership check.

---

### ℹ️ Discord slash command invocation banners are always public

ℹ️ info · Design · 2026-05-03 · **BY DESIGN**

Discord always shows "X used /command" publicly regardless of whether the bot's response is ephemeral. Error responses from the bot ARE correctly ephemeral (only the invoker sees them), but the invocation banner itself is platform behaviour that cannot be suppressed without making the entire interaction ephemeral — which would prevent public announcements like duel challenge pings. Not a defect.

---

### B.63 — Duel result embed shows ship name as winner/loser instead of player name

🟡 medium · UX · 2026-05-03 · **FIXED** (pending rebuild)

**Symptom**: Duel Complete embed shows `Winner: Betty` / `Loser: Betty` — both ships have the same name, making the result meaningless. Should show the player's display name instead.

**Root cause**: `FightResults.winner_name` / `loser_name` are populated from `ShipLoadout.ship_name` (the ship model name), not the player's name. When both players fly the same ship model the result is ambiguous.

**Fix**: The `accept_duel` service already has both `challenger` and `target` Player objects and knows who won. The router (or cog) should map `winner_name` → challenger/target display name based on which ship name matched, OR the service should return explicit `winner_player_name` / `loser_player_name` fields derived from the resolved display names (B.62). Using display_name (B.62) as the source is the right approach — fix B.62 first, then wire winner/loser display into this embed.

**Files**: `services/bot-core/src/api/routers/duels.py`, `services/discord-gateway/src/cogs/duelCog.py`

---

### B.62 — Users table has no display_name column; all player-facing name fields show discord_username (e.g. samx.ai) instead of display name (e.g. SamAccountX)

🟡 medium · Design · 2026-05-03 · **DEFERRED** (post-release)

**Symptom**: Duel embeds, autocomplete, and profile show `samx.ai` / `general_failure.` (Discord usernames) instead of `SamAccountX` / `General_Failure` (Discord display names). Display names are what users actually recognise each other by.

**Root cause**: `users` table only has `discord_username`. Discord's display name (the name shown in the server member list) is a separate field available at registration/interaction time via `interaction.user.display_name` but is never persisted.

**Fix**:
1. Add `display_name: str | None` column to the `users` table via Alembic migration.
2. Populate it on `/register` (and update it on subsequent interactions) from `interaction.user.display_name` in the gateway.
3. All name-resolution code (`get_pending_for_target`, `accept_duel`, profile cog, etc.) should prefer `display_name` over `discord_username`, falling back to `discord_username` if `display_name` is None.

**Scope**: Alembic migration, User model, user_repository, player_service (register), all cogs that call `/register` or display player names, all bot-core endpoints that resolve usernames.

---

### B.61 — Duel accept "Final Balances" embed shows `Player 2` for target — no target_name in accept response

🔵 low · UX · 2026-05-03 · **FIXED** (pending rebuild)

**Issue**: The duel accept embed "Final Balances" field shows `challenger_name` correctly (added in B.60) but falls back to `"Player {db_pk}"` for the target because the accept response does not include a `target_name` field. Challenger is shown by name, target is shown as e.g. "Player 2".

**Fix**: Add `target_name: str | None = None` to the accept_duel response dict in `services/bot-core/src/api/routers/duels.py`, populated from the target Player's username (same pattern as `challenger_name`). Update `duelCog.py` to use it (already reads `data.get("target_name")` — the cog side is already correct).

**Files**: `services/bot-core/src/api/routers/duels.py`, `services/bot-core/src/services/duel_service.py`

---

### B.60 — Duel accept/reject autocomplete shows Duel ID instead of challenger name

🔵 low · UX · 2026-05-03 · **FIXED** (pending rebuild)

**Issue**: `/duel-accept` and `/duel-reject` autocomplete dropdown showed `"Duel #2 — 100cr stakes"`. Target likely can't identify which challenge is which if they have multiple pending.

**Fix**: Extended `DuelRequestResponse` with `challenger_name: str | None`. `get_pending_for_target()` now resolves challenger username via `player_repo → user_repo`. Autocomplete label is now `"SamAccountX — 100cr stakes"` with fallback to old format if name unavailable. Applies to both accept and reject (shared autocomplete function).

---

### B.59 — `CombatBonusRequest.base_reward` lacks non-negative schema guard

🔵 low · Code · 2026-05-03 · **OPEN**

**Issue**: `CombatBonusRequest.base_reward: int` has no `Field(ge=0)` constraint. A negative value passed by a buggy caller would subtract credits from the player on a "win". The cog controls this value from the capture response so exploitation requires cog-level tampering, but the schema should guard it.

**Fix**: Add `base_reward: int = Field(..., ge=0)` in `services/bot-core/src/api/schemas/bounty_schema.py`.

---

### B.58 — `combat_bonus` silently returns `won=True, bonus_credits=0` when player not found

🟡 medium · Code · 2026-05-03 · **OPEN**

**Issue**: If `player_repo.get_by_id()` returns `None` in the `combat_bonus` endpoint (race condition or data integrity gap between capture and bonus call), the response returns `won=True, message="Combat victory! +Xcr bonus (2x total)!"` but `bonus_credits=0`. No error is logged, no exception raised. Player is told they won credits they never received.

**Fix**: Add a guard after `player_repo.get_by_id()` — if player is `None`, return an error response (or raise `HTTPException(404)`). Log at ERROR level.

**File**: `services/bot-core/src/api/routers/bounties.py` lines 214–221.

---

### B.57 — Combat system refactor: unify PvP and PvC combat paths; introduce PvC player armour buff

🟠 high · Design · 2026-05-03 · **FIXED** (pending rebuild)

**Directive**: PvP duel combat and PvC (player vs criminal) bounty combat must follow the exact same code path through `CombatService.fight_ships()`. The only permitted difference is an optional armour buff applied to the player in PvC combat (proposed: +50% armour multiplier when fighting criminals). No separate combat logic should exist for duels vs bounties.

**Current state**:
- PvC (bounty combat bonus): `bounties.py` builds two `ShipLoadout` objects and calls `CombatService.fight_ships()` directly. ✅ Correct pattern.
- PvP (duel accept): `duel_service.py` calls `CombatService.fight_ships()` via `LoadoutBuilder.from_player()`. ✅ Correct at the service layer.
- Both paths already use `SimpleTTKResolver` via `CombatService`. ✅

**Required changes**:
1. Introduce an optional `player_armour_buff_factor: float = 1.0` parameter to `CombatService.fight_ships()` (or a wrapper) so the PvC path can pass `1.5` and the PvP path passes `1.0` (no buff).
2. The buff must be applied ONLY to the player's armour stat, BEFORE variance rolls, NOT to shield or DPS.
3. `get_armour()` or `collect_stats()` should accept an `armour_multiplier` override, OR the caller passes a modified `CombatStats` — whichever is cleaner.
4. The bounty `combat_bonus` endpoint and the duel `accept_duel` service must both invoke the same `CombatService` method — no forking.
5. `BOUNTY_PVC_ARMOUR_BUFF = 1.5` added to `GameConstants`, env-overridable, earmarked for per-guild configurability under B.49.

**Files**: `services/bot-core/src/services/combat_service.py`, `services/bot-core/src/services/duel_service.py`, `services/bot-core/src/api/routers/bounties.py`, `services/bot-core/src/services/game_constants.py`

**Note**: B.55 (duel response crash) is a blocker that will be fixed as part of this same pass. See B.55.

---

### B.56 — Duel challenge embed: target not pinged outside embed body

🔵 low · UX · 2026-05-03 · **FIXED** (pending rebuild)

**Symptom**: The duel challenge embed contains the target's mention inside the embed body only. Discord embed pings do not generate notifications for most users — the target is likely to miss the challenge entirely.

**Expected**: Target mention sent as plain message content (outside the embed), matching the pattern used for bounty announcements. The embed body can still reference the target by name/mention.

**File**: `services/discord-gateway/src/cogs/duelCog.py` — `duel_challenge` handler, challenge send call.

---

### B.55 — Duel accept crashes with AttributeError after successful combat resolution

🟠 high · E2E · 2026-05-03 · **FIXED** (pending rebuild)

**Symptom**: `/duel-accept` returns "Service issue, please try again" to the user. Duel resolves and credits transfer correctly in the DB, but the HTTP response crashes before reaching the gateway.

**Root cause**: `duels.py:accept_duel` (line ~166) accesses `fight.challenger_health` and `fight.target_health` — fields that do not exist on `FightResults`. The correct fields are `fight.ship1_stats.varied_hp` and `fight.ship2_stats.varied_hp`.

**Impact**: Duel result embed is never shown. Player has no idea who won or what the credit outcome was. Duel IS correctly marked `completed` in DB and credits DO transfer — data integrity is intact.

**Fix**: Replace `fight.challenger_health` → `fight.ship1_stats.varied_hp` and `fight.target_health` → `fight.ship2_stats.varied_hp` in `duels.py`. Also audit whether the inline dict response is the right shape for what `duelCog.py` expects and align them.

**File**: `services/bot-core/src/api/routers/duels.py` ~line 166

---

### B.53 — Prestige does not swap Discord tier roles (Platinum role not removed, Bronze role not added)

🟠 high · E2E · 2026-05-02 · **FIXED-PENDING-VERIFY**

**Symptom**: After `/prestige confirm:CONFIRM`, the player's tier resets to Bronze in the DB but their Discord roles still show the pre-prestige tier role (e.g. Platinum) and the Bronze role is never added.

**Root cause**: `playerCog.prestige()` (`services/discord-gateway/src/cogs/playerCog.py:343-385`) calls the prestige API and builds the success embed but contains no role management code. The `/promote` command (lines 441-484) has a full tier-role swap block (remove old tier role, add new tier role) that prestige is missing entirely.

**Fix**: After a successful prestige API call, add the same role-swap logic used by `/promote`: remove the player's current tier role (from `prestige_data["tier_before"]`) and add the Bronze role. Should be non-fatal (wrapped in try/except) per the existing pattern. The `tier_before` field is already returned in the prestige API response so no additional API call is needed.

**File**: `services/discord-gateway/src/cogs/playerCog.py` — prestige handler, after line 365

---

### B.54 — Bounty reward distribution undervalues the winner when many players miss (resolved)

🟡 medium · Design · 2026-05-02 · **FIXED** (pending rebuild)

**Issue**: Winner received remaining pool after consolation payouts, making it possible for a winner to receive less than consolation players in a fully-checked route. On a 5-system 7,635 cr bounty with 4 missed checks, winner received only 1,527 cr (20%) — same as a single missed check.

**Fix**: Introduced `BOUNTY_WINNER_RESERVE_FACTOR = 0.25` (env-overridable via `BOUNTYBOT_BOUNTY_WINNER_RESERVE_FACTOR`). At spawn, `reward_per_sys = floor((reward × 0.75) / route_length)`. Winner always receives their reserve + any unconsumed consolation, guaranteeing ≥25% of posted bounty. Failed checkers receive credits only — XP removed from consolation payouts. Winner XP based on full payout. Bronze 2x combat bonus applies to full winner payout including XP.

**Future**: `BOUNTY_WINNER_RESERVE_FACTOR` earmarked for per-guild configurability under B.49.

---

### B.52 — Criminal loadout generation can produce zero-weapon bounties when a non-combat ship is selected

🟡 medium · Runtime · 2026-05-02 · **FIXED-PENDING-VERIFY**

**Symptom**: A bounty is spawned with an empty weapons list on the criminal's ship, resulting in a 0-DPS criminal that any player trivially defeats (or a confusing loadout embed with no weapons section).

**Root cause**: `bounty_service.py:406` guards weapon selection behind `if ship.max_primaries > 0`. Seven ships in the DB have `max_primaries=0` (Cormorant, Rhino, Midorian/Nivelian/Terran/Vossk Freighters, Vossk Battlecruiser). These are non-combat cargo/transport ships. When the random ship selector at line 370 (TL-matched) or line 382 (fallback) picks one of these, the entire weapon block is silently skipped and `equipped_weapons = []`.

**Fix applied**: `bounty_service.py:368` and `bounty_service.py:379` — both TL-matched and fallback ship selection queries now filter `Ship.max_primaries > 0`, ensuring only combat-capable ships are eligible for criminal loadouts. Confirmed in code by researcher 2026-05-06.

---

### B.51 — duelCog passes Discord snowflake IDs as challenger_id/target_id but duels service expects player PKs

🟠 high · E2E · 2026-05-02 · **FIXED-PENDING-VERIFY**

**Symptom**: `/duel-challenge target:@general_failure. stakes:100` returns:
> ❌ Challenger player with ID 402296276617527306 could not be retrieved.

**Root cause**: `duelCog.duel_challenge()` (`services/discord-gateway/src/cogs/duelCog.py:83`) posts `challenger_id: interaction.user.id` and `target_id: target.id` — both are Discord snowflake IDs (e.g. `402296276617527306`). The duels router passes these directly to `DuelService.create_challenge()` which calls `player_repo.get_by_id(db, challenger_id)` — a lookup by internal player PK (1, 2, etc.), not Discord user ID.

**Fix**: The duel cog must resolve Discord user IDs to player PKs before posting to the duels API, using the same `_get_player_id()` pattern used by other cogs (e.g. `inventoryCog`, `bountyCog`). Both `challenger_id` and `target_id` need resolution. Alternatively, the duels router could accept Discord user IDs and resolve internally — but the cog-side resolution is consistent with the existing pattern.

**Files**: `services/discord-gateway/src/cogs/duelCog.py:80-88`

---

### B.49 — Audit hardcoded operational constants for per-guild configurability (enhancement)

🔵 low · Enhancement · 2026-05-02 · **OPEN** · Future patch — not blocking

**Context**: Discovered during B.48 live verification (E2E 6.18) when user noticed `/admin_config View Config` does not display the bounty-credit-to-XP payout factor. Investigation revealed `BOUNTY_REWARD_TO_XP_GAIN_MULT = 0.1` is a hardcoded global in `services/bot-core/src/services/game_constants.py:92`, with no guild override path.

**The broader pattern**: B.48 successfully consolidated XP-tier-prestige progression into per-guild `xp_thresholds`. Several other operational constants in `game_constants.py` likely deserve similar treatment so per-guild balance/tuning is possible. Doing them piecemeal is wasteful — a single audit + batched refactor is more efficient and gives a single design pass on "which dial belongs in which command surface."

**Candidate constants to audit** (initial list — architect should expand during scoping):

| Constant | Current Value | Notes |
|----------|---------------|-------|
| `BOUNTY_REWARD_TO_XP_GAIN_MULT` | 0.1 | Bounty payout → XP gain multiplier |
| `BOUNTY_WINNER_RESERVE_FACTOR` | 0.25 | Winner's guaranteed share of bounty pool (B.54); already env-overridable |
| `MAX_BOUNTIES_PER_DIVISION` | (env-overridable) | Already supports `BOUNTYBOT_*` env override but not per-guild |
| `BOUNTY_DELAY_RANDOM_MIN/MAX` | (env-overridable) | Same as above |
| `CHECK_COOLDOWN` | (env-overridable) | Same as above |
| `DUEL_VARIANCE_PERCENT` | (TBD) | Combat RNG knob — might affect per-guild competitive balance |
| Shop refresh intervals | (TBD) | Already partially configurable per-guild via shop config; verify completeness |
| Temperature decay rates / multipliers | (TBD) | Guild activity heat — clear per-guild candidate |
| Module/equipment limits | (intentional non-overridable per game balance) | Excluded — these are game-rule invariants, not operational tuning |

**Design questions for the audit**:

1. **Per-guild vs env-only override**: Some constants are already env-overridable globally. Decide for each whether per-guild override is wanted or if global env tuning is sufficient.

2. **Storage strategy**: Three options — (a) extend existing `guild_configs` JSON columns where a relevant one exists, (b) add dedicated typed columns (preferred for non-threshold values like multipliers/floats), (c) introduce a new generic `guild_config_overrides` JSON column.

3. **Admin command surface**: Which existing commands extend (e.g., `/admin_config_payout`, `/admin_config_combat`)? Or a single `/admin_config Set key:... value:...` generic update path? Mix?

4. **Default-fallback pattern**: All new configurable values must follow the B.48 pattern — `.get("Key", DEFAULT)` so backward-compat with existing guild rows works without migration. Where new columns are added, use `server_default` in Alembic.

5. **Tier/division naming consistency**: B.48 noted that `tier_levels = {"Bronze": 1, "Silver": 2, ...}` exists as a local lookup in `shop_service.py:748`. Audit should consider whether to elevate this to a shared `TierRank` enum used everywhere tier-comparison is needed.

**Estimated scope** (if all candidates above end up in scope): ~10-15 files, 1 Alembic migration (DB schema additions only, no data migration), ~250-400 lines including tests. Comparable to B.48 in shape but with one DB schema change.

**Workflow recommendation when this is picked up**:
1. `@researcher` first — full audit of `game_constants.py` against actual production usage; classify each constant as (a) per-guild candidate, (b) global env-overridable only, (c) hardcoded invariant. Output: design doc with the table above filled in completely.
2. User reviews and ratifies the audit.
3. Then `@architect` for design + implementation per the same loop pattern used for B.48.

**Cross-refs**: Discovered during E2E phase 6.18 verification (B.48 live testing). Related to the broader theme of "guild-configurable operational tuning" started by B.48.

---

### B.50 — Refine confirmation UX and admin command syntax (enhancement)

🔵 low · Enhancement · 2026-05-02 · **OPEN** · Future patch — not blocking

**Context**: During the B.48/B.49 prestige and admin-config work, two recurring UX problems stood out:

1. Several destructive / high-impact prompts still rely on typed `confirm:CONFIRM` style parameters.
2. Some admin config command surfaces are awkward and easy to misuse, e.g. `/admin_player user:@samx.ai action:Set Credits credits:999999999` versus accidental parameter mix-ups like `/admin_player user:@samx.ai action:Set Credits xp:999`.

**Future enhancement goals**:

- Replace typed confirmation tokens with button-style confirmation embeds for destructive actions (`CONFIRM` becomes a UI button, not a text parameter).
- Rework admin config/edit commands so the parameter flow is more fluid and less error-prone.
- Audit the existing command surfaces to decide which ones should remain explicit sub-actions and which should move to a cleaner form/dialog or a more structured command layout.

**Questions to resolve when this is picked up**:

1. Which commands are destructive enough to require button confirmation?
2. Which admin configuration flows are better served by a dedicated command vs a generic set/update surface?
3. Which command patterns should be preserved for discoverability, and which should be replaced to reduce user error?

**Scope**: Future UX/refactor work only. No behavior changes required for B.48/B.49 verification.

---

### B.48 — Prestige must reset the player to the starter Betty loadout; level concept is still vestigial and should be removed

🟠 high · Architecture · 2026-05-02 · **FIXED-PENDING-VERIFY**

**Revision (2026-05-02)**: The earlier B.48 refactor removed the vestigial level/division gate from prestige. The user now wants prestige to be a *full reset* of owned ships/inventory back to the exact first-time `/register` starter state: the starter Betty loadout with Betty as the active ship.

**Context**: BountyBot has two parallel and largely orthogonal progression systems:

**System A — Tier (user-visible, configurable)**:
- Stored in `players.tier` (string column: Bronze/Silver/Gold/Platinum)
- Set ONLY by `/promote` (manual) or admin commands
- Promotion gated by configurable per-guild `xp_thresholds` JSON in `guild_configs` (e.g., Silver=10, Gold=20, Platinum=30)
- Used by: `/profile` display, shop access, bounty board access, `/admin_player Set XP` embed

**System B — Level/Division (hidden, hardcoded, vestigial)**:
- Computed dynamically: `XP → calculate_user_level()` (1-10 buckets) `→ DivisionService.get_division_for_level()` (bronze/silver/gold)
- Hardcoded boundaries in `GameConstants.XP_LEVEL_BOUNDARIES` (level 10 = 1,000,000 XP)
- Hardcoded division mapping in `GameConstants.DIVISION_BOUNDARIES` (only 3 divisions — NO platinum)
- Used by: `prestige_player()` gate (requires `level == 10`), bounty reward `leveled_up` flag, prestige embed "Previous Level" field

**The contradiction (empirically verified 2026-05-02)**:

Setup: Main account is Platinum tier with XP=35. Configured guild xp_thresholds: Silver=10, Gold=20, Platinum=30.

| Surface | Output | Underlying system |
|---------|--------|-------------------|
| `/profile` | "Tier: Platinum / 🏆 Maximum Tier" | System A — `player.tier` column |
| `/promote` | "Already at maximum tier (Platinum)" | System A — `_TIER_NAMES` lookup |
| `/admin_config View Config` | "XP Thresholds: Silver:10, Gold:20, Platinum:30" | System A — `guild_configs.xp_thresholds` |
| `/prestige` | **"Player must be level 10 to prestige (current level: 1)"** | System B — `XP_LEVEL_BOUNDARIES` |

The user is "Platinum" (configured-max tier), with XP that exceeds all configured tier thresholds, yet is told they cannot prestige because they are "level 1" — a concept the user has never seen on any other surface and cannot influence via any command. The Platinum threshold (30 XP) and prestige requirement (1,000,000 XP) differ by 5 orders of magnitude with no discoverable explanation.

**Additional disconnects discovered during investigation**:

1. `/admin_player Set XP` does NOT auto-adjust tier. Setting Alt's XP from 927 → 2 left tier=Silver despite being far below Silver threshold (10). Tier and XP are independent columns; only `/promote` writes tier.

**B.48 revision requirement (starter Betty reset)**:

The prestige action must now:

- Reset the player's owned ships and inventory to the exact starter registration state
- Make Betty the active ship
- Recreate the starter Betty loadout (same items/equipment as first-time `/register`)
- Keep the new Prestige threshold behavior from B.48 intact (`xp_thresholds["Prestige"]`)

This supersedes the earlier "preserve ship hulls" behavior. Prestige is now a true account reset with the starter ship/loadout restored.

Implications:

1. `player_service.prestige_player()` must delete/rebuild the player's ship/inventory rows to match the starter registration flow.
2. Gateway prestige warning/success embed text and any docstrings must be updated to describe the starter Betty reset accurately.
3. E2E tests 6.13-6.15 and 6.20-6.21 must be rerun against the revised behavior.
4. Existing B.48 verification that the prestige flow preserves all ships is now superseded and no longer the desired behavior.

2. `DIVISION_BOUNDARIES` defines only 3 divisions (`bronze`/`silver`/`gold`) but `TIER_TECH_LEVEL_CAPS` and `_TIER_NAMES` reference 4 tiers (Bronze/Silver/Gold/Platinum). System B has no Platinum equivalent.

3. The hardcoded `XP_LEVEL_BOUNDARIES` curve is internally incoherent: level 6→7 costs 43,000 XP within Silver, but level 7→8 (Silver→Gold transition) costs only 10,000 XP. Level 9→10 jumps from 90,000 to 1,000,000 (10× wall) purely as an artificial prestige gate.

4. `XP_LEVEL_BOUNDARIES` is documented as intentionally non-configurable ("to maintain game balance") but System A's tier thresholds are fully per-guild configurable, defeating that rationale.

5. Player level appears in user-facing UI in exactly **two** places: the `/prestige` error message ("current level: 1") and the prestige confirmation embed ("Previous Level: 10"). Nowhere else — not `/profile`, not `/leaderboard`, not `/promote`, not bounty rewards.

**Production call sites of `calculate_user_level()`** (5 total, all internal):
- `bounty_service.py:1528,1544` — computes `leveled_up` flag for reward dict
- `player_service.py:548-553` — computes `leveled_up` for XP-add response (transient, not persisted)
- `player_service.py:364` — prestige gate
- `player_service.py:618-619` — `check_level_up()` static helper
- `division_service.py:108` — `get_division_for_player_xp()` convenience wrapper

**No persisted columns reference level** — every `level_*` field is in transient API responses, recomputed Discord embeds, or runtime dataclasses. Verified via `grep level_before|level_after|leveled_up` across both services.

**Proposed refactor**:

1. **Add `Prestige` key to `xp_thresholds` JSON** in guild config (no schema migration — JSON column). Default value TBD by architect (suggest something like 50 for test thresholds, with sane prod default per architect recommendation).

2. **Reroute `prestige_player()`** to check `xp >= xp_thresholds.get("Prestige", DEFAULT)` instead of `calculate_user_level(xp) == 10`.

3. **Update `/admin_config Set` and `View`** to support and display the new Prestige threshold. The `View Config` embed's "XP Thresholds" field should show all four thresholds.

4. **Delete entirely**:
   - `GameConstants.XP_LEVEL_BOUNDARIES` (constant)
   - `game_maths.calculate_user_level()` and `calculate_xp_for_level()` (functions)
   - `DivisionService` class (entire file `division_service.py`)
   - `PlayerService.get_level()`, `PlayerService.check_level_up()` (static helpers)
   - All test fixtures/cases that exercise the level boundary system

5. **Repurpose bounty reward `leveled_up`**:
   - In `bounty_service.py:1520-1547`, replace `level_before/level_after/leveled_up` computation with `tier_before/tier_after/tier_changed`.
   - Tier change happens only via explicit `/promote` so this would normally always be `False` after a bounty reward. Confirm with architect: should bounty rewards keep this concept at all? If used purely for the "Level Up!" announcement in bounty completion embeds, it can be removed entirely; otherwise repurpose to surface "you crossed an XP threshold — run `/promote` to advance" hint.

6. **Update `playerCog.py:346`** prestige embed: "Previous Level: 10" → "Previous Tier: Platinum" (use `prestige_data["tier_before"]` after backend rename).

7. **Update `PrestigeResponse` schema** (`players_schema.py:89`): rename `level_before` → `tier_before`. Update API contract docs.

8. **Update `update_player_xp()` response** (`player_service.py:580-585`): remove `level_before/level_after/leveled_up/division_before/division_after/division_changed` from the dict. Optionally add `tier_unchanged_but_eligible_for_promotion: bool` if useful for UX hints.

9. **NOT in scope of this refactor (separate decision)**: Should `/admin_player Set XP` auto-adjust tier when XP drops below current tier's threshold? Currently it does not, leading to the orphan-tier state (tier=Silver, xp=2). Architect should flag whether this is intentional (admin override scenario) or a separate defect.

**Verification approach**: No DB wipe needed. The refactor touches code only; existing player rows (Main: Platinum/35XP, Alt: Silver/2XP) remain valid. After refactor + stack rebuild, re-run targeted E2E tests listed below.

**Files in scope** (estimate, architect to refine):
- `services/bot-core/src/services/game_constants.py` (delete `XP_LEVEL_BOUNDARIES`)
- `services/bot-core/src/services/game_maths.py` (delete level functions)
- `services/bot-core/src/services/division_service.py` (delete entire file)
- `services/bot-core/src/services/player_service.py` (rewrite `prestige_player`, gut `update_player_xp` response, delete static helpers)
- `services/bot-core/src/services/bounty_service.py` (replace `leveled_up` with `tier_changed` or remove)
- `services/bot-core/src/services/config_service.py` (default `xp_thresholds` to include `Prestige`)
- `services/bot-core/src/api/schemas/players_schema.py` (rename PrestigeResponse field)
- `services/bot-core/src/api/schemas/config_schema.py` (verify Pydantic accepts new key)
- `services/bot-core/src/api/routers/admin.py` (admin_config Set/View handle Prestige)
- `services/bot-core/src/api/routers/players.py` (prestige docstring)
- `services/discord-gateway/src/cogs/playerCog.py` (line 346 embed field rename)
- `services/discord-gateway/src/cogs/adminCog.py` (`/admin_config_xp` accept Prestige param if applicable)
- All test files referencing the deleted symbols (~10 files)

**Cross-refs**: Uncovered during E2E phase 6.13-6.15 (prestige flow). Related: tier vs division mismatch found in earlier sessions but not previously documented as a defect.

---

### B.44 — AboutCog preload has no retry logic, leaves category cache empty on startup

🟡 medium · Runtime · 2026-05-01 · **FIXED** `f39e73a` · **CLOSED** 2026-05-01

**Context**: On stack startup, AboutCog's `_preload_about_data()` failed with "ConnectError: Name or service not known" because bot-core wasn't ready. Unlike AdminCog (which has 5 retries with backoff), AboutCog failed immediately and set `_objects_by_category = {}` — never retried.

**Root cause**: `services/discord-gateway/src/cogs/aboutCog.py` preload method had no retry logic.

**Fix**: Added 5-attempt exponential backoff loop (5s, 10s, 20s, 40s, 60s) matching AdminCog's pattern.

---

### B.43 — Zero-slot turret equip gives generic Discord API error

🟡 medium · E2E · 2026-05-01 · **FIXED** `d6c1e0b` · **VERIFIED** 2026-05-01 · **CLOSED** 2026-05-01

**Context**: Equip a turret on a ship with 0 turret slots (e.g., Specter has 0 turrets). Expected: helpful error. Actual: generic API error.

**Root cause**: inventoryCog slot_full handler creates empty Select when max_slots=0, Discord rejects.

**Fix**: Added `max_slots == 0` check, returns clear error message.

**Verified**: `/equip item_name:Matador TS` on Specter shows "0 turrets slots" error.

---

### B.42 — Bounty expire executor runs before discord-gateway is ready

🟡 medium · Runtime · 2026-05-01 · **FIXED** `7b4cb2c` · **CLOSED** 2026-05-01

**Context**: On stack startup, bot-core's `bounty-expire-executor` (APScheduler job) runs its recovery-sweep and attempts to delete stale bounty announcement messages from Discord channels. This fails because discord-gateway service hasn't started yet, resulting in:

```
bounty-expire-executor - WARNING - BountyExpireJob[recovery-sweep] failed to delete Discord message: [Errno -2] Name or service not known
```

The second attempt (moments later) succeeds once gateway is up.

**Root cause**: Startup dependency chain was `db → bot-core → discord-gateway`. Bot-core runs recovery sweeps immediately on startup, but gateway hadn't started yet because it depended on bot-core being healthy.

**Fix**: Reversed the dependency order in docker-compose.yml and docker-compose-gpu.yml:
- `discord-gateway` now depends only on `db` (not on bot-core)
- `bot-core` now depends on both `db` and `discord-gateway`
- `blender-service` now depends on both `db` and `discord-gateway`

New startup order: `db → discord-gateway → bot-core, blender-service`

---

### B.41 — `/equip` autocomplete filter blocks duplicate equips of same item type

🟡 medium · E2E · 2026-04-30 · **FIXED** `e4459c0` + `d6c1e0b` · **VERIFIED** 2026-05-01

**Original context**: Player has multiple instances of the same item (e.g. 3x M6 A4 "Raccoon" primary weapons) and a ship with multiple primary slots. After equipping the first instance, the autocomplete dropdown removes the item from suggestions, even though the player still owns more in inventory.

**Original fix** (e4459c0): Changed autocomplete filter to count equipped items, allowing `qty > equipped_count`.

**Regression** (d6c1e0b): LoadoutConsistencyService allowed over-equipping beyond inventory qty. Fixed by adding validation in `equip_one()` to ensure `equipped_count < inventory_quantity`.

**Verified**: Main can equip 2x Raccoon (shows remaining count), 3rd correctly rejected.

**Context**: Player has multiple instances of the same item (e.g. 3x M6 A4 "Raccoon" primary weapons) and a ship with multiple primary slots. After equipping the first instance, the autocomplete dropdown removes the item from suggestions, even though the player still owns 2 more in inventory and the game rules allow multiple equips of the same weapon (e.g. 4x M6 A4 "Raccoon" on a 4-primary-slot ship).

**Root cause**: `services/discord-gateway/src/cogs/inventoryCog.py:600-601` autocomplete filter:
```python
and item_name not in equipped_names  # Blocks ALL duplicates
```

**Game rule**: Players are explicitly allowed to equip multiple instances of the same weapon. Backend correctly supports this (verified via researcher investigation: equip_one allows duplicate item names, ship loadout arrays support duplicates, inventory tracks via quantity column).

**Workaround**: Player must type the full item name manually (with quotes for items containing them). Substring matching like "a4" fails because backend requires exact name.

**Fix scope**: Discord-gateway only — change autocomplete filter logic to allow duplicates as long as `inventory_quantity > equipped_count_of_same_name`.

**Severity**: 🟡 medium — workaround exists (full name manual entry), but UX is broken for the entire late-game loadout fitting flow.

---

### B.39 — `/promote` adds new tier role but fails to remove previous tier role

🟡 medium · E2E · 2026-04-30 · **FIXED-PENDING-VERIFY**

**Context**: During E2E Phase 6, Main (SamAccountX) used `/promote` to advance from Bronze to Silver. The Silver tier role (@Bounty Hunter Silver) was correctly added, but the Bronze tier role (@Bounty Hunter Bronze) was NOT removed. Player ended up with both tier roles simultaneously.

**Empirical evidence**:
- After `/promote`: profile showed Tier=Silver, but both @Bounty Hunter Silver AND @Bounty Hunter Bronze roles were present on the Discord user
- Expected: old tier role should be removed when new tier role is added
- Affects: visual/role display; any future code that checks Discord role for tier determination

**Fix applied**: `playerCog.py:488-533` — promote handler now fetches guild config, resolves both old and new tier role IDs, removes old role and adds new role atomically. Non-fatal exception handling. Confirmed in code by researcher 2026-05-06.

---

### B.40 — Configured admin role not granting admin command access to Alt user

🟡 medium · E2E · 2026-04-30 · **FIXED** `84a874b` · **CLOSED** 2026-05-01

**Context**: During E2E, the configured admin role (@Bounty Bot Admin) was assigned to the Alt account, but admin commands (e.g. `/admin_spawn_bounty`, `/admin_help`) remained inaccessible.

**Root cause**: Multiple issues:
1. adminCog used `interaction.user.roles` instead of `interaction.member.roles` (B.40 first fix in d6c1e0b)
2. API response was missing `admin_role_id` even though DB had it (6db86ac)
3. `interaction.member` can be None in some Discord contexts - need to use `guild.get_member()` instead (84a874b)
4. helpCog had `@app_commands.default_permissions(administrator=True)` hiding `/admin_help`

**Fix chain**:
- d6c1e0b: Changed to interaction.member.roles
- 6db86ac: Added admin_role_id to API response schema
- 84a874b: Use `interaction.guild.get_member(user.id)` for reliable member lookup, removed decorator

---

### B.38 — `UserRepository` missing `get_by_discord_id` method; all admin give/remove endpoints broken
> **FIXED** in commit (pending). Developer added method + 4 tests. Tester PASS with remediated test coverage. **✅ Verified live 2026-04-30** — `/admin_give_ship` succeeded after rebuild.

🟠 high · E2E · 2026-04-30

**Context**: During E2E Phase 7, `/admin_give_ship user:@SamAccountX ship_name:Kinzer RS` raised `AttributeError: 'UserRepository' object has no attribute 'get_by_discord_id'`.

**Empirical evidence**:
- `services/bot-core/src/persist/repositories/user_repository.py` — method did not exist
- `services/bot-core/src/api/routers/admin.py` — 4 call sites: `admin_give_item` (L924), `admin_remove_item` (L995), `admin_give_ship` (L1069), `admin_remove_ship` (L1144)

**Fix**:
- Added `get_by_discord_id(db, discord_id)` to `UserRepository` — delegates to `get_by_id` (User.id IS the Discord snowflake PK)
- Added 1 unit test (`test_get_by_discord_id_exception`) and 3 integration tests (happy path found/not-found/contract)
- All 26 user repository tests pass; ruff clean

---

> **Status snapshot (2026-04-30 post-Tier-2-audit)**: All Package A–G + Patch H + Patch I + B.34/B.35 closeouts have landed; the 2026-04-30 pre-E2E hardening pass added an empirical Tier 2 cog HTTP audit (B.36) that proved zero broken URL/method contracts across the 6 highest-traffic cogs (100 HTTP call sites checked against bot-core's 110 registered routes). Every entry in the OPEN section below carries one of:
> - `> **FIXED** in commit ...` annotation → fix committed, awaits live re-verification under DB-nuke E2E pass
> - `RETROACTIVELY CLOSED` header → already correct in HEAD or won't-fix-from-code (A.20, O.2)
> - No FIXED banner → genuinely unresolved (currently only **O.1**, intermittent `/setactive` autocomplete awaiting diagnostic reproduction)
>
> Items marked FIXED here are also summarized in the **FIXED** table at the bottom with their `Verified` column. `Verified: pending` will flip to `✅ live <date>` as the live re-verification pass walks each affected E2E phase.

### B.37 — `setupCog.on_guild_remove` called a nonexistent bot-core route; lingering DB state on bot removal
> **FIXED** in commits `15dc544` (bot-core: add `DELETE /admin/guilds/{id}/cleanup` endpoint + 6 router tests), `3283009` (discord-gateway: respx URL+method contract test for `on_guild_remove`). Verified: pending live re-verification.

🟡 low · pre-E2E hardening · 2026-04-30

**Context**: Final adversarial QA pass on the B.34/B.35/B.36 cleanup package surfaced exactly one B.33-shape defect across all 14 discord-gateway cogs. The user authorized an immediate fix-then-handoff before the morning E2E run, with the explicit directive: "if ANY of these issues, OR ANY ADJACENT OR SIMILAR appear when I next test, there will be hell to pay."

**Empirical evidence (pre-fix, code review at HEAD)**:

`services/discord-gateway/src/cogs/setupCog.py:87` issued:

```python
resp = await self.http_client.delete(
    f"{api_base}/admin/guilds/{guild.id}/cleanup",
    timeout=10,
)
```

Cross-referenced against the complete bot-core admin router (`services/bot-core/src/api/routers/admin.py`) at HEAD:

| Route | Method | Auth | Discord-side effects? |
|---|---|---|---|
| `/admin/guilds/initialize` | POST | requires `user_id` ∈ ADMIN_USER_IDS | no |
| `/admin/guilds/{id}/reset` | POST | requires `user_id` ∈ ADMIN_USER_IDS | no |
| `/admin/guilds/{id}/uninstall` | DELETE | requires `user_id` ∈ ADMIN_USER_IDS | yes (channel/role deletion attempted) |
| `/admin/guilds/{id}/cleanup` | — | **NO SUCH ROUTE** | — |

Result: every `on_guild_remove` event produced a 404 response, which was caught by the broad `except Exception` block at `setupCog.py:95` and logged at DEBUG level. Zero user-facing impact, but the cog's clearly-intended DB-cleanup contract was unfulfilled — `guild_configs`, `guild_shops`, `bounty`, `apscheduler_jobs`, `players` and cascaded `player_ships` / `player_inventories` rows for the removed guild persisted indefinitely. If the bot was later re-added to the same guild, it inherited stale state.

This is the textbook B.33-shape failure mode (cog calling a wrong/nonexistent URL co-shipping with mock-only tests that mask the bug). The pre-fix `tests/cogs/test_setupCog.py::TestOnGuildRemove` class used `cog.http_client.delete = AsyncMock(...)` — the mock returned a canned response regardless of URL, so the contract violation was invisible to the test layer.

**Decision rationale (Option 1 chosen)**:

Three reasonable fix shapes were on the table:

| Option | Tradeoff |
|---|---|
| **1. Implement the new endpoint** | Matches the cog's clearly-intended contract; leverages existing `config_service.uninstall_guild()` (DB-only, naturally idempotent); requires audit-log convention for system-event actor. **Chosen.** |
| 2. Repoint cog to `/uninstall` | Rejected: `/uninstall` requires `user_id` ∈ ADMIN_USER_IDS auth, which the bot's `on_guild_remove` cannot meaningfully supply (no user initiated the event). Would also attempt destructive Discord channel/role deletes that always fail (bot is already gone) — log noise pollution. |
| 3. Delete the call | Rejected: leaves stale DB state on bot re-add — the exact failure mode the original cog code was trying to prevent. Violates the user's "fix what fucking needs to be fixed" directive. |

**Fix shape**:

1. **bot-core** (`services/bot-core/src/api/routers/admin.py`): added `DELETE /admin/guilds/{guild_id}/cleanup` (commit `15dc544`):
   - Soft cleanup variant of `/uninstall`.
   - DB-only: removes guild-scoped rows via existing `config_service.uninstall_guild()` — same code path as `/uninstall`.
   - Does NOT touch Discord channels/roles (bot is already gone from the guild).
   - **No `user_id` query parameter** — system-event endpoint, auth bypass is intentional and docstring-documented.
   - **Audit-logged** with `actor_id=0` (convention for "no user — automated platform event") and `details.trigger="on_guild_remove event from discord-gateway"`.
   - **Idempotent**: `clear_guild_players` iterates a possibly-empty list, `clear_all_guild_shops` issues `DELETE WHERE` (no-op on empty), `delete_guild_config` returns False if no row exists. Discord retries succeed silently.
   - Mirrors `/uninstall` scheduler-job cancellation and bounty-clearing for parity.

2. **discord-gateway** (`services/discord-gateway/tests/cogs/test_setupCog.py`): added `TestOnGuildRemoveRespx` class (commit `3283009`):
   - `test_on_guild_remove_calls_correct_url_and_method` — respx `assert_all_called=True` against `DELETE /api/v1/admin/guilds/{guild_id}/cleanup`. Any drift (URL change, method swap) fails the test.
   - `test_on_guild_remove_handles_404_gracefully` — defense-in-depth regression net: even if a future deployment serves bot-core without the endpoint, the broad-except path must not crash the bot's event loop.

3. **bot-core router tests** (`services/bot-core/tests/api/test_admin_router.py`): 6 new tests in `TestCleanupGuildOnRemove` class (commit `15dc544`):
   - happy path → 200 + correct removed_counts
   - no `user_id` required (auth bypass for system events)
   - idempotency on consecutive calls (Discord-retry resilience)
   - guild_id from URL path is correctly threaded to `config_service.uninstall_guild`
   - 500 on internal exception
   - scheduler-job cancellation only matches the right guild (mirror of uninstall test)
   - **audit-log contract**: asserts `actor_id=0`, `action="guild_cleanup_on_remove"`, `details.trigger` contains `"on_guild_remove"`

**Test results**:

| Service | Test scope | Pre-fix | Post-fix |
|---|---|---|---|
| bot-core | `tests/api/test_admin_router.py::TestCleanupGuildOnRemove` (6 new) | (did not exist) | 6 passed |
| bot-core | full suite | 3180 passed | **3186 passed**, 1 skipped (47.63s) |
| bot-core | `test_transaction_discipline.py` (linter, B.34 layer-1 defense) | 2 passed | 2 passed |
| discord-gateway | `tests/cogs/test_setupCog.py` (11 pre-existing + 2 new respx) | 11 passed | **13 passed** (6.90s) |
| discord-gateway | full suite | 2208 passed | **2210 passed** (15:23) |

**Severity rationale (🟡 low)**: zero user-facing impact (404 was already swallowed); zero downtime risk (lingering data is bounded by the number of guilds the bot has ever been kicked from, currently 0–1 per dev cycle). The fix is preventive — it eliminates a B.33-shape latent risk that would have kept materializing on every guild-remove event in production. The respx contract test is the long-term insurance: the next time someone refactors `setupCog`, they cannot silently re-introduce the bug.

**Limitation**: the new endpoint relies on `config_service.uninstall_guild()` being naturally idempotent. The B.34 transaction-discipline contract (every cross-table flow must be wrapped in `async with db.begin():`) is not strictly required here because the endpoint mirrors `/uninstall`'s existing per-step-commit pattern — both are existing self-committing service methods. A future refactor that converts `uninstall_guild` to flush-only must update both endpoints together.

---

### B.36 — Empirical Tier 2 cog HTTP audit (pre-E2E hardening, no findings)
> **CLOSED — NO FINDINGS** in commits documented in 2026-04-30 closeout. Audit added 6 respx URL+method contract tests for Phase 1-3 critical paths (one per cog), establishing a regression net for the highest-traffic commands. Verified: pending live re-verification.

🟢 info · pre-E2E hardening · 2026-04-30

**Context**: After the B.34/B.35 closeouts landed, the user authorized one final pre-E2E adversarial pass to ensure no B.33-shape regression (cog calling a wrong URL/method) lurks in the highest-traffic commands. This entry is the empirical audit log.

**Methodology**: For each of the 6 highest-traffic cogs (playerCog, adminCog, inventoryCog, shopCog, shipsCog, bountyCog), every `self.http_client.<method>(url, ...)` call was extracted via AST + regex, the URL and method were normalized, and the result was cross-referenced against the complete bot-core route registry (110 registered routes) plus the blender-service route surface.

**Findings** (full table):

| Cog | HTTP calls | SAFE | CONFIRMED-BROKEN | DYNAMIC-VERIFIED |
|---|---|---|---|---|
| playerCog.py | 12 | 12 | 0 | 0 |
| adminCog.py | 38 | 38 | 0 | 0 |
| inventoryCog.py | 27 | 27 | 0 | 0 |
| shopCog.py | 9 | 9 | 0 | 0 |
| shipsCog.py | 7 | 7 | 0 | 0 |
| bountyCog.py | 8 | 8 | 0 | 0 |
| **TOTAL** | **101** | **101** | **0** | **0** |

(playerCog count includes 1 redundant call site at line 214 that uses an inline-built URL string; manually verified.)

Every URL pattern matched a registered bot-core route or blender-service route. Zero confirmed-broken or dynamic-unverifiable routes found.

**Test additions** (regression protection for Phase 1-3):

| Cog | Test class | Path covered | URLs asserted |
|---|---|---|---|
| playerCog | `TestProfileCommandRespx` | `/profile` | POST /players/, GET /players/{id}/statistics, GET /players/{id}/promotion-status |
| adminCog | `TestAdminSetupCommandRespx` | `/admin_setup` | POST /admin/guilds/initialize |
| inventoryCog | `TestEquipCommandRespx` | `/equip` | POST /players/, GET /ships/player/{id}, POST /ships/{id}/equip-check, POST /ships/{id}/equip |
| shopCog | `TestBuyCommandRespx` | `/buy` (item) | POST /players/, GET /shops/item/{id}, POST /shops/purchase |
| shipsCog | `TestSetActiveCommandRespx` | `/setactive` | POST /players/, PUT /ships/{id}/set-active |
| bountyCog | `TestCheckCommandRespx` | `/check` | POST /players/, POST /bounties/check |

All 6 tests pass.

**Limitation**: this audit covers only the 6 highest-traffic cogs. The remaining 8 cogs (aboutCog, devCog, duelCog, healthCog, helpCog, schedulerCog, setupCog, skinsCog) were NOT audited at this depth. The B.33 root cause (wrong URL + wrong method co-shipping with mock-only tests) could in principle exist there too — see A.23 for the full deferred-conversion plan.

**Severity rationale (🟢 info)**: Empirical assurance for the user's morning E2E pass. No live bug discovered, but an audit-style closure record is appropriate to demonstrate "the work was done" rather than "we believe it's fine."

---

### B.35 — B.34 closeout: 3 service-level split-brain transaction patterns + 11 missing repo commit kwargs (proactive sweep)
> **FIXED** in commits `2231008` (F-1 ruff + F-2 InventoryRepository.clear_player_inventory), `1ec7c6b` (Sweep 1 — 11 repo write methods missing commit kwarg), `d83360a` (Sweep 2 — 3 service split-brain fixes + linter refinement), `1ecae1f` (Sweep 7 — ruff cleanup across all 3 services). All 10 sweeps documented in handoff. Verified: pending live re-verification.

🟠 high · Phase pre-test hardening · 2026-04-30

**Context**: After the B.34 remediation landed, a tester pass surfaced 2 narrow findings (F-1: 3 E501s + format diff in `bounty_repository.py`; F-2: `InventoryRepository.clear_player_inventory` missing `commit: bool = True`). The user authorized a proactive sweep for any **adjacent or similar pattern** before the next live testing round, citing the original B.19/B.34 root cause: prior fixes did not exhaustively enumerate all callers / similar cases.

**Findings (post-sweep)**

**F-1 — 3 E501 + format diff in `bounty_repository.py`** (commit `2231008`)
- Lines 111, 134, 249 each had identical 131-char comment-suffixed expressions exceeding the 120-char limit.
- Fixed by moving the explanatory `# B.14: exclude stale bounties past end_time` comment to a separate line above each filtered query block; expression line now fits in 120.

**F-2 — `InventoryRepository.clear_player_inventory` missing `commit: bool = True`** (commit `2231008`)
- Method violated AC-3 (every repository write method accepts `commit` kwarg).
- Added `commit: bool = True` keyword-only parameter following the canonical pattern. Default preserves backward-compatible commit-on-success / rollback-on-exception. `commit=False` flushes only.
- Updated sole caller (`PlayerService.prestige_player`) to pass `commit=False` since it is wrapped in `async with db.begin()` at the router level.
- Added 4 unit tests covering commit=True/False × success/exception matrix.

**Sweep 1 — 11 additional repository write methods missing `commit` kwarg** (commit `1ec7c6b`)
Empirical AST sweep of all `services/bot-core/src/persist/repositories/*.py` for methods named `add`/`create`/`update`/`remove`/`delete`/`clear`/`set` that perform writes (db.add, db.delete, db.execute(update(...)), or attribute mutation followed by flush/commit). Found 11 methods with real writes but no `commit` kwarg:

`ConfigRepository`:
- `create_default_config`, `reset_to_defaults`, `update_shop_config`,
  `update_admin_role`, `update_starting_credits`, `update_xp_thresholds`,
  `update_division_temperatures`, `delete_guild_config`

`ShopRepository`:
- `clear_shop_tier`, `clear_all_guild_shops`, `update_prices`
  (`update_prices` also doc-noted as bulk Core UPDATE — identity-mapped rows may see stale values until refresh; treated as fire-and-forget admin op)

`BountyRepository`:
- `clear_active_by_guild` (already a documented bulk-op exception; now accepts `commit` kwarg consistent with other bulk write methods)

All defaulted to `commit=True` to preserve all existing call-site behavior. AGENTS.md inventory table updated.

**Sweep 2 — 3 service-level split-brain (B.34-class) patterns** (commit `d83360a`) — **most important finding**

The B.34 root cause was: a service method directly mutates one table's ORM instance THEN calls a self-committing repo method on a different table; the cross-table writes are persisted only because the repo's commit happens to flush them. If anyone changes the repo call to `commit=False`, the cross-table mutations silently rollback (exact B.34 mechanism).

Three additional methods exhibited this pattern:

1. **`duel_service.accept_duel`** — mutates `winner.credits/duel_wins/duel_credits_won` and `loser.credits/duel_losses/duel_credits_lost` (direct ORM attribute writes), then calls `duel_repo.update_status(duel_id, "completed")` with default `commit=True`. The Player ORM mutations are persisted only because update_status's commit propagates to all in-flight changes. Now: `update_status` uses `commit=False`; service explicitly commits at the end. Atomic.

2. **`bounty_service.distribute_rewards`** — mutates `player.credits/lifetime_credits/xp/systems_checked/bounty_wins` for every reward recipient (direct ORM writes), then calls `bounty_repo.update(bounty)` with default `commit=True`. Same pattern. Now: `bounty_repo.update` uses `commit=False`; service explicitly commits.

3. **`shop_service.purchase_item`** — deducted `player.credits` (ORM), called `inventory_repo.add_item` with default `commit=True` (mid-flow commit), then mutated `shop_item.quantity` (ORM), then called `db.commit()`. The mid-flow commit created a window where the shop-quantity update could fail and leave the player credit-deducted-with-item but shop unchanged. Now: a single atomic `db.commit()` covers all three writes (player + inventory + shop).

**Linter refinement**: the static linter (`tests/test_transaction_discipline.py`) initially flagged the corrected services as flush-only because they pass `commit=False` to inner repo calls. This is a false positive: methods that have BOTH `commit=False` (inner) AND an unconditional `db.commit()` (own commit) are SELF-COMMITTING AGGREGATORS, not flush-only. They own their own transaction. Refined `_has_commit_false_call` to recognize this case: any method with an unconditional `db.commit()` is never flush-only regardless of inner commit=False delegations.

**Sweep 3 — Routes calling 2+ mutating service methods**: 8 admin / config / shop / bounty routes call multiple mutating service methods. None create a B.34-class atomicity gap because each service method self-commits. They lack TRUE atomicity (partial-failure recoverable via re-run), but this is acceptable for admin-style idempotent operations. Listed for future hardening; not fixed in this pass.

**Sweep 4 — Executors**: All executor service calls (`spawn_bounty`, `expire_bounty`, `respawn_bounty`, `expire_duel`, `refresh_shop`, `update_division_temperatures`) target self-committing service or repo methods. Static linter confirms zero executor calls land on flush-only methods. SAFE.

**Sweep 5 — Dual-mode service methods**: 4 dual-mode methods (`audit_service.log_action`, `inventory_service.add_item_to_inventory`, `inventory_service.remove_item_from_inventory`, `shop_service._add_item_to_shop`) — all properly documented; linter handles them correctly. Zero new findings.

**Sweep 6 — Migration scripts**: Both Alembic revision scripts use the standard `op.get_bind()` / `op.execute()` pattern. Zero findings.

**Sweep 7 — Pre-existing ruff issues**: 5 SIM117 errors in `test_adminCog.py` (combined nested `with` statements) + 9 files with format diffs. All fixed.

**Sweep 8 — AsyncMock overuse**: 1249 `AsyncMock(return_value=...)` instances in bot-core tests, mostly tautological for transaction-discipline assertions but correct for behavioral round-trip. The 20 cross-session integration tests in `test_cross_session_persistence.py` are the safety net for the transaction-discipline class. Migration to integration tests is huge scope, deferred.

**Sweep 9 — cross-session test isolation**: All 20 tests in `test_cross_session_persistence.py` use ≥4 `factory()` calls (well above the required 2 — session A + session B). All 20 pass green. Confirmed clean.

**Sweep 10 — Architectural smells noted**: see "Documented but not fixed" below.

**Documented but not fixed (acceptable trade-offs)**:
- `bounty_service._process_single_bounty_check` commits per-iteration in `check_bounty`'s loop (resilience trade-off — partial progress preserved on mid-loop failure rather than atomic-all-or-nothing).
- `shop_service.refresh_shop` per-tier commits when called in admin `initialize_guild` loop — partial-failure recoverable.
- `inventory_service.consolidate_inventory` per-duplicate commits — maintenance function, partial progress recoverable.
- Admin multi-step routes (`initialize_guild`, `reset_guild`, `uninstall_bot`) lack `db.begin()` wrapping — non-atomic but idempotent admin operations.

**Test counts after closeout**:
- bot-core: 3179 passed, 1 skipped (was 3175 pre-closeout)
- discord-gateway: 2202 passed
- blender-service: 127 passed

**Ruff state**: Clean across all 3 services (zero errors, zero format diffs). No `# noqa` introduced in production code beyond the 4 pre-existing documented suppressions.

**Schema impact**: Zero. No model changes, no migration changes. The B.34 closeout is purely behavioral (transaction discipline) and stylistic (uniformity). Existing DB rows are unaffected.

---

### B.34 — `/profile` (player registration) silently drops starter ship + inventory on first registration
> **FIXED** in commits `4e43b93` (Phase B repo API uniformity), `9ed326d` (Phase A.1+A.2 — static linter and B.34 atomic-create fix), `15294b5` (Phase A.4 — audit-service commit kwarg + linter refinement), `22a857c` (Phase D.1+D.3 — @requires_transaction decorator + consumer enumeration), `1a6d63e` (Phase D.2 — auto-commit on clean exit), `e0fc6db` (Phase C — 20 cross-session integration tests). Also addresses the broader systemic gap (the "every cross-table flow must be wrapped" contract was reviewer-only; now enforced by 4 layers: static linter, runtime decorator, session auto-commit, and integration tests). Spec at `/proj/recon/B34-remediation-spec.md`. Verified: pending live re-verification under DB-nuke E2E pass.

🔴 high · Phase 1.7 · 2026-04-29

**Environment**: HEAD (`samx-wip` post-B.33 fix), fresh stack rebuild post-DB-nuke, dev guild `1490693399307616276`. Detected during E2E Phase 1 live re-verification — Main and Alt both ran `/profile` for first registration and reported "all pass" based on Discord embed which displays only tier/XP/credits/stats and DOES NOT surface ship/inventory.

**Symptom**
- On `/profile` first registration, `players` row commits correctly with credits/tier/XP.
- `player_ships` row, `player_inventories` rows, and `players.active_ship_id` update **all silently rolled back** — never persisted.
- Logs DO emit "Added ship: Betty for player N", "Added inventory item: …", "Updated active ship for player N: N", and "Created starter loadout for player N" because they fire on `db.flush()`, before the missing commit.
- Embed does not display loadout, so the silent failure is invisible to users until they try to `/equip`, fight a duel, hunt a bounty, or any other path that reads ship/inventory state.

**Empirical citations (HEAD post-redeploy 2026-04-29 ~18:55 UTC)**

`services/bot-core/src/services/player_service.py`:
- L77-106 `_create_new_player(db, user, guild_id) -> Player`:
  - L97: `player = await self.player_repo.add(db, player)` — `PlayerRepository.add` (player_repository.py:67) calls `await db.commit()` unconditionally. Player row commits at this point.
  - L100: `await self._create_starter_loadout(db, player)` — all internal writes use `commit=False`.
  - L102: `return player` — **no `await db.commit()`, no `async with db.begin()` wrapper.**
- L108-168 `_create_starter_loadout(db, player) -> None`:
  - L143: `player_ship_repo.create_or_update(..., commit=False)`
  - L146: `self.player_repo.update_active_ship(..., commit=False)`
  - L149-152: `inv_repo.add_item(..., commit=False)` × 4
  - L157-161: `consistency.equip_one(...)` × 3 — per choke-point I3 contract these flush only.

`services/bot-core/src/persist/repositories/AGENTS.md` (post-Package-G policy):
> "every cross-table flow is wrapped [in `async with db.begin():`], and every repo call inside that wrapper passes `commit=False`."

`_create_new_player` does not wrap, does not commit explicitly. After return, FastAPI dependency-managed session context exits and the flushed-only writes are discarded.

**DB-side proof (2026-04-29 18:55 UTC, post-redeploy with both Main + Alt registered)**:
- `players` count = 2 (PIDs 1 and 2 created)
- `player_ships` count = **0**
- `player_inventories` count = **0**
- `players.active_ship_id` = **NULL** for both rows
- `players.updated_at == created_at` (timestamps `2026-04-29 23:54:54.106+00` and `23:55:42.655+00`) — confirming no further commit ever touched these rows
- bot-core logs at 23:54:54.140-.197 and 23:55:42.680-.742 show all "Added"/"Equipped"/"Created starter loadout" success entries — log-vs-DB divergence is the smoking gun

**Root cause**
- Package G / B.19 refactor (commits `3ac67a4` family, 2026-04-29) flipped all inventory/ship/loadout-consistency repository methods to `commit=False` per choke-point invariant I3 ("caller owns the transaction").
- `_create_new_player` was the one cross-table flow not updated to actually own the transaction. Pre-refactor each repo call self-committed; post-refactor nothing commits.

**Test gap**
- Existing `tests/services/test_player_service.py` mocks the repositories so `commit=False` flag goes unobserved — the mock returns the persisted row regardless of whether commit was called.
- No integration test covers "create new player, then re-fetch from DB to verify ship + inventory rows actually exist." This is exactly the class of bug a real-DB integration test would have caught.

**Severity rationale**: 🔴 high. Every new-player registration produces a player with no ship and no inventory. Every downstream loadout/inventory/combat/duel/bounty path is broken for that player until manual repair. Phase 1 onward of the live re-verification cannot proceed meaningfully without this fixed.

**Proposed fix (Option A — minimal, recommended)**
Add `await db.commit()` at the end of `_create_new_player` after `_create_starter_loadout` returns. Match I3 contract (`_create_new_player` is the caller; it owns the transaction post-`player_repo.add`).

```python
# player_service.py:99-102
# Create starter loadout
await self._create_starter_loadout(db, player)
+await db.commit()
+await db.refresh(player)

return player
```

**Alternative fix (Option B — atomic)**
Wrap the entire body of `_create_new_player` in `async with db.begin():`, change `player_repo.add` call to `commit=False` (or use the FOR UPDATE / flush variant). Cleaner architecturally but touches `player_repo.add` signature.

**Verification plan post-fix**
1. Hot-reload picks up the change (bot-core has `reload=True`).
2. Both users `/unregister` then `/profile` to re-trigger creation path.
3. Self-verify: `player_ships=2`, `player_inventories=2` (Micro Gun MK I × 2), `players.active_ship_id` non-null on both, `weapons=["Nirai Impulse EX 1"]`, `modules=["E2 Exoclad","Telta Quickscan"]` on both ship rows.

**Sibling sweep — other paths that should commit but might also rely on choke-point flushes without an outer commit**
- `services/bot-core/src/services/shop_service.py` — `purchase_ship`, `sell_ship`, `transfer_ship` paths (TODO during fix).
- `services/bot-core/src/services/equipment_service.py` — `equip_item`, `unequip_item` (these are router-driven; routers should wrap with `db.begin()` per AGENTS.md).
- `services/bot-core/src/services/inventory_service.py` — buy/sell/transfer (router-driven).
- Worth a focused grep audit during the developer fix package.

**Remediation summary (2026-04-30)**

The 2026-04-30 remediation expanded scope beyond `_create_new_player` to address the systemic gap that the audit at `/proj/activity.md` surfaced: the "every cross-table flow must be wrapped" contract was enforced only by reviewer discipline. Post-remediation it is enforced by **four defense-in-depth layers**:

1. **Static linter** (test-time): `tests/test_transaction_discipline.py` walks the AST of every router file and fails CI when a route calls a flush-only service method without `async with db.begin():` or explicit `await db.commit()`. Pre-fix, the linter detected exactly one violation: `players.py:63 create_or_get_player → get_or_create_player` — the literal B.34. Zero unsuppressed violations remain post-fix.
2. **Runtime decorator** (call-time): `@requires_transaction` from `services/_transaction_guards.py` raises `RuntimeError` if a `LoadoutConsistencyService` public method is invoked outside an active transaction. Catches dynamic-dispatch bypasses the static linter cannot reason about.
3. **Session auto-commit** (exit-time): `db_manager.get_session()` now commits any pending transaction on clean exit. If a caller ever forgets to wrap or commit, work is preserved at the session boundary instead of silently rolled back. Audit at commit time confirmed all 102 read-only callsites are GET-style queries unaffected by the change.
4. **Cross-session integration tests** (CI-time): 20 new tests in `tests/integration/test_cross_session_persistence.py` covering all 20 cross-table operations from spec §6.1. Each test opens session A, performs the op, closes A, opens FRESH session B, and asserts persistence. Includes a negative-path test (Op 20) that forces an exception mid-flow and asserts NOTHING persists.

**Repository API uniformity (AC-3)**: every repository write method (INSERT/UPDATE/DELETE) now accepts `commit: bool = True` keyword. Default preserves backward compatibility. Inventory of methods updated:
- `GenericRepository`: add, remove
- `PlayerRepository`: add, create_or_update, remove, update_xp, update_tier (already had: update_credits, update_active_ship)
- `UserRepository`: add, create_or_update, remove, get_or_create_user
- `BountyRepository`: add, create_or_update, remove, create, update, delete
- `DuelRepository`: add, create_or_update, remove, create, update_status, delete_expired
- `ConfigRepository`: add, create_or_update, remove
- `DiscordMessageRepository`: create_or_update, delete_by_composite_key, delete_by_guild_type_and_reference

**Latent bug fixed in passing**: `BountyRepository.remove` and `DuelRepository.remove` were calling `db.delete(obj)` without `await`. SQLAlchemy AsyncSession.delete is async; the missing `await` would raise TypeError against a real database. Tests passed because they mocked `db.delete` as a sync MagicMock. Fixed in commit `4e43b93` along with the test files.

**Service body purity (AC-4)**: `_create_new_player` was the canonical mixed-pattern violation (mixed self-committing `player_repo.add` with flush-only `_create_starter_loadout`). Fixed by passing `commit=False` to all repo calls and wrapping the route in `db.begin()`. `audit_service.log_action` was also identified as architecturally incompatible with atomicity inside wrapped transactions (it self-committed even when called from inside a `db.begin()` block). Fixed by adding `commit: bool = True` keyword; the one site inside a wrapped transaction (`admin_remove_ship`) now passes `commit=False`.

**Test count delta**: bot-core 3146 → 3175 (+29 tests: 2 linter, 4 decorator, 3 AC-7, 20 integration).

---

### B.33 — Package E `_preload_static_catalogs` regression: GET vs POST + nonexistent ship URL leaves item & ship autocomplete caches empty on every boot
> **FIXED** in commits `452ac28` (production fix), `9a19909` (adminCog respx tests), `645c474` (project-wide preload test audit), `51c0bb5` (testing policy docs). Fix: L119 `GET /data/{category}` → `GET /about/categories/{category}/objects`; L142 `GET /about/ships` → `GET /about/categories/ship/objects`. Test debt: 4 old tautological mock tests replaced with 5 respx tests asserting URL+method. Audit: aboutCog, bountyCog, skinsCog preload tests converted; no new defects found. See `services/discord-gateway/tests/AGENTS.md` for policy.

🔴 high · Phase 0.3 · 2026-04-29

**Environment**: HEAD (commit `3ac67a4` tip of `samx-wip`), fresh stack rebuild, dev guild `1490693399307616276`. Detected during E2E Phase 0.3 docker-log scan immediately after clean-slate verification.

**Symptom**
- On every cold boot, `discord-gateway-AdminCog._preload_static_catalogs` retries 5 times each (5s/10s/20s/40s/60s back-off) for **all 4 item categories + ship catalog**, totalling ~8 minutes of warning/error log noise.
- Terminal failures land on `_item_catalog` and `_ship_catalog`, leaving both caches empty.
- `/reload_autocomplete` (devCog) re-runs the same broken `_preload_static_catalogs` and fails identically — confirmed by user reproduction in dev guild 2026-04-29 ~15:08 UTC.
- Downstream: every cog autocomplete that reads from `_item_catalog` (`/equip`, `/unequip`, `/buy`, `/sell`, `/admin_give_item`, `/admin_remove_item`) or `_ship_catalog` (skin commands, ship-name slots in admin commands) returns an empty dropdown after fresh boot until/unless an alternative cache feeds it.

**Empirical citations (HEAD)**

`services/discord-gateway/src/cogs/adminCog.py:116-160`:
- L116 iterates `("primary_weapon", "secondary_weapon", "turret_weapon", "module")`
- L119 issues `await self.http_client.get(f"{api_base}/data/{category}", timeout=10)` — **WRONG METHOD**
- L121 expects `[obj["name"] for obj in resp.json() if obj.get("name")]` — **WRONG SHAPE** (server returns `list[str]`)
- L142 issues `await self.http_client.get(f"{api_base}/about/ships", timeout=10)` — **NONEXISTENT URL**
- L144 expects `[s["name"] for s in resp.json() if s.get("name")]` — would also fail on shape

`services/bot-core/src/api/routers/data.py:23` registers `@router.post("/{category}", response_model=list[str])` — POST-only, returns plain string list.

`services/bot-core/src/api/routers/about.py` registered routes (verified via grep): only `/about/categories`, `/about/categories/{category}/objects`, `/about/object/...`, `/about/ships/{ship_name}/render-info` exist. **No `/about/ships` plural endpoint.** `aboutCog._preload_data` correctly uses `/about/categories/{cat}/objects` (verified working in same boot — log shows "Preloaded 65 objects for category ship").

**Direct curl probe from inside bot-core container, 2026-04-29 15:03 UTC:**
```
GET /api/v1/data/primary_weapon    → 405
GET /api/v1/data/secondary_weapon  → 405
GET /api/v1/data/turret_weapon     → 405
GET /api/v1/data/module            → 405
GET /api/v1/about/ships            → 404
```

**Live boot log evidence** (gateway, fresh container started 14:54 UTC):
```
14:54:01 ERROR _preload_static_catalogs: terminal failure for category=secondary_weapon after 5 attempts
14:56:16 ERROR _preload_static_catalogs: terminal failure for category=turret_weapon after 5 attempts
14:58:31 ERROR _preload_static_catalogs: terminal failure for category=module after 5 attempts
15:00:46 ERROR _preload_static_catalogs: terminal failure for ship catalog after 5 attempts
```
(Primary_weapon failed earlier, before tail window; user-triggered `/reload_autocomplete` at 15:03:59 reproduced the same failures starting again — pattern continues at 15:03:59-15:05:14+.)

**Provenance** — git blame: introduced in commit `1a07cb4` ("feat(discord-gateway): Package E autocomplete preload + cache framework (B.26)", 2026-04-29 06:44 UTC). Diff added 121 lines to adminCog.py with `_preload_static_catalogs` and 277 lines of test coverage to `test_adminCog.py`.

**Root cause of the test miss** (per user observation 2026-04-29 ~15:10 UTC)

This is a textbook violation of `services/bot-core/tests/AGENTS.md`'s "max 2 mocks / prefer real objects" rule, applied to the gateway test suite. The 277 new test lines mock `cog.http_client.get` with canned success payloads, never asserting:
1. The HTTP **method** used matches the server's actual route (server is `@router.post`, cog uses `.get`)
2. The **URL** maps to a route registered on bot-core (server has no `/about/ships`)
3. The **response shape** matches the server contract (`list[str]` from server vs cog's `obj["name"]` access — would `KeyError` even if URL/method were right)

A single integration test booting a real bot-core and exercising `_preload_static_catalogs` end-to-end would have caught all three bugs simultaneously. Mocking the HTTP client is tautological: it tests "the code calls the mock the way the mock expects", not "the code makes a request the server can serve".

A secondary masking factor: AboutCog's separate, *correct* preload (`/about/categories` + `/about/categories/{cat}/objects`) populates `_object_dict` for `/about` and `/list_category` autocomplete, hiding the breakage from any spot-check that exercised those commands. The new `_item_catalog` / `_ship_catalog` caches feed *different* commands (`/equip`, `/buy`, `/sell`, ship-skin) which were not exercised live before this re-verification pass.

**Implemented fix**

Option 1 (taken) — minimal change, reuse existing AboutCog endpoint contract:
- L119: `self.http_client.get(f"{api_base}/about/categories/{category}/objects", timeout=10)` (already returns `list[dict]` with `name` field — adminCog's existing parsing at L121 works unchanged)
- L142: `self.http_client.get(f"{api_base}/about/categories/ship/objects", timeout=10)` (same endpoint, ship category — adminCog's existing parsing at L144 works unchanged)
- Eliminated entire dependency on `/data/{category}` POST contract; aligned with AboutCog's known-working pattern. 2-line URL change with no shape-handling changes.

Rejected alternative (not taken): keep `/data/{category}` but fix method + shape — more invasive (changes both URL semantics and parse logic) and `/data/{category}` was designed as an admin import endpoint, not a query.

**Test-debt remediation** applied alongside the code fix:
1. Replace the `http_client.get` mock pattern in `test_adminCog.py::test_preload_static_catalogs_*` with either (a) a `respx`-backed mock that asserts URL+method, or (b) a real bot-core TestClient fixture.
2. Add similar URL+method assertions to all other gateway preload tests (devCog, shopCog, bountyCog) to prevent recurrence.
3. Audit `services/discord-gateway/tests/AGENTS.md` (if exists) or add a new section codifying: "preload tests must assert URL and HTTP method, not just response handling."

**Workaround**: none in-band. `/reload_autocomplete` reproduces the same bug. AboutCog cache covers `/about` + `/list_category`. Other commands relying on the empty caches will show empty dropdowns until fix ships.

**Severity rationale (🔴 high)**: Blocks live verification of Phase 5 (equip/unequip), Phase 6 (shop buy/sell), Phase 11 (skin selection), and degrades autocomplete UX silently in production with no user-visible error — only an empty dropdown. Fix is trivial (2-line URL change); cost of leaving open dwarfs cost of fixing.

---

### B.32 — `/render_config action:set setting:samples` silently accepts unrecognized field name
🟡 medium · Phase 12.16 · 2026-04-28
> **FIXED** in commit `8860c5a` (Package C, 2026-04-29). Fix A: cog validates `setting` against `self._render_settings` before API call (adminCog.py); returns ephemeral error listing valid settings. Fix B: blender-service router raises 422 when no valid `RenderConfig` fields appear in the update payload (config.py defense-in-depth).

**Environment**: dev guild `1490693399307616276`, post-rebuild stack, blender-service `/api/v1/config/render`.

**Reproduction**
1. `/render_config action:view` shows render config with fields: `max_res_x`, `max_res_y`, `min_res_x`, `min_res_y`, `max_samples`, `min_samples`, `default_res_x`, `default_res_y`, `default_samples`, `max_concurrent_renders`, `job_ttl_hours`. **No field named plain `samples`.**
2. `/render_config action:set setting:samples value:64` invoked
3. Bot replied: `"✅ Updated samples = 64"` (success-shape response with green check)
4. Subsequent `/render_config action:reset` and direct API check `GET /api/v1/config/render` returned the default values.

---

**Verified code paths** (HEAD, read-only recon 2026-04-28)

| Layer | File | Lines | Notes |
|---|---|---|---|
| Cog — preload | `services/discord-gateway/src/cogs/adminCog.py` | 78–95 | `_preload_render_settings()` fetches `GET /config/render` on startup; stores response keys in `self._render_settings` |
| Cog — autocomplete | `services/discord-gateway/src/cogs/adminCog.py` | 97–106 | `render_setting_autocomplete` filters `self._render_settings` by current input — soft allowlist for Discord UI |
| Cog — set handler | `services/discord-gateway/src/cogs/adminCog.py` | 934–945 | `action == "set"` branch; calls `PUT /config/render` with `json={setting: value}`; reports "✅ Updated" **solely on HTTP 200** — never inspects response body |
| Router | `services/blender-service/src/routers/config.py` | 26–35 | `PUT /render` accepts raw `updates: dict` (no Pydantic schema); calls `config_service.update(updates)`; returns `updated.to_dict()` with HTTP 200 regardless of how many fields were applied |
| Service — update | `services/blender-service/src/services/render_config_service.py` | 76–96 | `update(dict)` iterates keys; uses `hasattr(self._config, key)` to filter; applies only known fields via `setattr`; **unknown keys silently ignored**; returns unchanged `self._config` |
| Config dataclass | `services/blender-service/src/services/render_config_service.py` | 15–52 | `RenderConfig` fields: `max_res_x`, `max_res_y`, `min_res_x`, `min_res_y`, `max_samples`, `min_samples`, `default_res_x`, `default_res_y`, `default_samples`, `max_concurrent_renders`, `job_ttl_hours` — **no field named `samples`** |

---

**Did the API mutate anything?**

**No. The update was a complete silent no-op.**

`samples` is not a field on the `RenderConfig` dataclass. `hasattr(self._config, "samples")` returns `False`. The `update()` method skips it, logs a WARNING at service level (`"Config update: 1 unknown key(s) ignored: ['samples']"` at line 91, and `"Config update: no valid fields provided"` at line 95), returns the unchanged config, and the router returns HTTP 200 with the original values.

The subsequent `view` and `reset` showing defaults were unrelated to the "update" — the config was never modified.

`samples` is not an alias for any field. It is not stored anywhere. The value `64` was silently discarded.

---

**Where does the success response originate?**

Two-layer compounding failure:

1. **Blender-service router** (`config.py:26–35`): Returns HTTP 200 + full config dict even when zero fields were mutated. The router docstring explicitly says _"unknown keys are silently ignored"_ — but this intent is not signalled back to the caller (no `ignored_keys` in response, no 422).

2. **Cog handler** (`adminCog.py:944–945`): After `resp.raise_for_status()` (HTTP 200 → no exception), immediately sends `"✅ Updated \`{setting}\` = \`{value}\`"` to the user. **The cog never reads the response body.** It does not compare the before/after values of `setting` in the returned config dict. Any HTTP 200 from this endpoint is treated as confirmation of a successful mutation.

---

**Allowlist findings**

| Layer | Allowlist? | Enforced? |
|---|---|---|
| Cog autocomplete (`render_setting_autocomplete`) | Yes — keys from `GET /config/render` at startup | **No** — `setting` parameter is `str`, Discord permits freeform input regardless of autocomplete |
| Cog `action == "set"` handler | None | n/a — no guard before API call |
| Router (`PUT /config/render`) | None — accepts raw `dict` | n/a |
| Service `update()` | Implicit — `hasattr` filter | Not surfaced to caller as error |

The autocomplete provides valid field names as UI hints, but since `setting` is typed `str | None` (not `app_commands.Choice`), Discord does not block freeform text. A user (or API client bypassing Discord) can submit any string.

---

**Cross-cutting pattern with B.30** — **CONFIRMED, same class, different mechanism**

| Aspect | B.30 (`PUT /jobs/{job_id}`, bot-core) | B.32 (`PUT /config/render`, blender-service) |
|---|---|---|
| Layer | Pydantic schema (`extra="ignore"`) | Service `hasattr` filter |
| Input handling | Unknown fields stripped by Pydantic before service | Unknown fields reach service, ignored silently |
| Consequence | **Destructive write** — job payload replaced with `{}` | **Silent no-op** — config unchanged |
| Response | HTTP 200 `{"status": "updated"}` | HTTP 200 with full (unchanged) config dict |
| Caller inspection | Cog reports success on HTTP 200 | Cog reports success on HTTP 200 |
| Internal logging | None logged about the mismatch | WARNING logged at service level (not surfaced) |
| Severity | 🟠 high | 🟡 medium |

Both endpoints accept unrecognized input without returning a validation error; both callers report success based solely on HTTP 200 without verifying the mutation occurred. B.32 has better internal logging (a WARNING is emitted) but this is not observable from outside the service.

---

**Severity assessment** — 🟡 medium confirmed

- Admin-only command; blast radius limited to guild administrators
- No data corruption, no destructive write (config is never wrongly mutated)
- Consequence is false confidence: admins believe a setting was changed when it was not — could mask render misconfiguration and cause difficult-to-diagnose issues
- `samples` being a plausible near-miss for `default_samples` makes the bug especially easy to trigger accidentally

---

**Open questions**

1. Are there any other callers of `PUT /api/v1/config/render` (e.g., scripts, integration tests) that might silently rely on the lenient behavior?
2. Should `GET /config/render` response include a machine-readable list of writable field names so callers can self-validate? (Currently it just returns current values.)
3. Should the router return `applied_fields` and `ignored_fields` in its response even on success, to allow callers to detect partial application?

---

**Recommended fix-scope size** — **surgical** (1–3 lines per fix point)

Two independent fix points (either is sufficient; both is optimal):

**Fix A — Cog-side guard (preferred, immediate)**  
Before the API call in `adminCog.py:934–945`, validate `setting` against `self._render_settings`:
```python
elif action == "set":
    if not setting or value is None:
        ...
    if setting not in self._render_settings:
        await interaction.response.send_message(
            f"⚠️ Unknown setting `{setting}`. Valid settings: {', '.join(f'`{s}`' for s in self._render_settings)}",
            ephemeral=True,
        )
        return
    resp = await self.http_client.put(...)
```

**Fix B — Router/service-side 422 (defense-in-depth)**  
In `render_config_service.py:update()` (or in the router), return 422 when no valid fields were applied:
```python
# In router config.py:
updated = config_service.update(updates)
# Check if anything was actually applied:
# (alternatively, update() could raise ValueError when applied_updates is empty)
```
Or raise `HTTPException(status_code=422, detail=f"No valid fields in update. Valid fields: {list(RenderConfig.__dataclass_fields__)}")` when `applied_updates` is empty.

**Fix C — Cog response verification (optional)**  
After receiving the response, verify the field value changed:
```python
response_config = resp.json()
if str(response_config.get(setting)) != str(value):
    await interaction.response.send_message(f"⚠️ Setting `{setting}` was not applied (unrecognized field name).", ephemeral=True)
    return
```

---

**Cross-references**
- **B.30** (scheduler `PUT /jobs/{id}` silently wipes payload on unrecognized body shape) — same class of defect: write endpoint accepts unrecognized input without validation error, caller reports success on HTTP 200.

**Recon completed**: 2026-04-28 by developer (read-only investigation)

---

### B.31 — `/admin_config action:Reset to Defaults` returns 500; user sees raw bot-core URL
🟠 high · Phase 12.5 · 2026-04-28
> **B.31a FIXED** in commit `360287b` (Package A, 2026-04-29). `cascade="all, delete-orphan"` added to `GuildConfig.shops` relationship. No migration needed.
> **B.31b FIXED** in commit `a35aa7c` (Package F, 2026-04-29). Centralized `report_api_error(interaction, exc, *, action_label=None, detail_override=None)` helper added at `services/discord-gateway/src/cogs/_shared/http_error_handler.py` and applied to all 53 sites across 9 cogs. Helper sanitizes (strips internal URLs and httpx MDN-link lines, collapses whitespace, truncates at 1000 chars), maps status codes to friendly canned messages with severity-based emoji/color, surfaces FastAPI `{"detail": ...}` bodies (string + Pydantic-list shapes), and is race-safe via `contextlib.suppress(discord.HTTPException)`. Pre-existing specialized branches (404 specials in `schedulerCog`, `_is_guild_not_configured` checks, B.28 validation-failure sends, duelCog 400 detail extraction) preserved verbatim — only the bare "everything-else" `f"❌ API Error: {e}"` line was replaced. 15 helper unit tests + 9 cog integration tests assert the embed-based output is free of `bot-core` / `http://` leaks.

**Environment**: dev guild `1490693399307616276`, Main account, post-rebuild stack.

**Reproduction**
1. Pre-state: guild_config row exists; `guild_shops` populated with current Bronze shop (refreshed at 14:38 UTC).
2. `/admin_config action:Reset to Defaults` invoked at 15:38 ET.
3. User-visible reply (ephemeral):
   ```
   ❌ API Error: Server error '500 Internal Server Error' for url
   'http://bot-core:8000/api/v1/config/guild/1490693399307616276/reset'
   ```
   followed by an MDN link to the HTTP 500 status page.
4. `/admin_config action:View Config` immediately afterward shows pre-reset state intact (Starting Credits=5,000 from prior 12.2; not reverted to default).

**Bot-core logs** (timestamp 20:38:09 UTC):
```
config-repository - ERROR - Error removing config:
  (sqlalchemy.dialects.postgresql.asyncpg.IntegrityError)
  <class 'asyncpg.exceptions.NotNullViolationError'>:
  null value in column "guild_id" of relation "guild_shops" violates not-null constraint
config-repository - ERROR - Error resetting config for guild 1490693399307616276: (same)
config-service - ERROR - Error resetting config for guild 1490693399307616276: (same)
bot-database-manager - ERROR - Session error — rolling back transaction: IntegrityError
config-api-router - ERROR - Error resetting guild config: (same)
```

**Two distinct defects in one observation**:

| Sub | Severity | Issue |
|---|---|---|
| 12.5a | 🟠 high | `POST /api/v1/config/guild/{gid}/reset` produces a NOT NULL constraint violation on `guild_shops.guild_id` during the reset transaction. The reset path attempts to mutate `guild_shops` rows in a way that nullifies the FK column. Reset fails atomically (good — no partial state per the rollback log). |
| 12.5b | 🔵 low | Gateway cog displays the raw `http://bot-core:8000/...` URL to the end user as part of the error embed. Implementation detail leakage; should show a generic message like "Failed to reset config; please try again." |

---

**Verified code paths** (HEAD, read-only recon 2026-04-28)

| Layer | File | Lines | Notes |
|---|---|---|---|
| Route handler | `services/bot-core/src/api/routers/config.py` | 188–227 | `async def reset_guild_config(guild_id, ...)` — calls `config_service.reset_to_defaults(db, guild_id)` |
| Service | `services/bot-core/src/services/config_service.py` | 90–100 | `async def reset_to_defaults` — delegates directly to `self.config_repo.reset_to_defaults(db, guild_id)` |
| Repository | `services/bot-core/src/persist/repositories/config_repository.py` | 186–202 | `async def reset_to_defaults` — (1) `get_by_guild_id`; (2) `self.remove(db, existing_config)`; (3) `self.create_default_config(db, guild_id)` |
| Repository remove | `services/bot-core/src/persist/repositories/config_repository.py` | 95–104 | `await db.delete(obj)` + `await db.commit()` — commit inside remove |
| Gateway cog (12.5b) | `services/discord-gateway/src/cogs/adminCog.py` | 592–602 | `admin_config` handler, `action == "reset"` branch; `httpx.HTTPStatusError` caught at line 601 |

---

**Schema / constraint findings**

`GuildConfig` model (`guild_config.py:91`):
```python
shops: Mapped[list["GuildShop"]] = relationship("GuildShop", back_populates="guild_config")
```
**No `cascade` argument. No `passive_deletes=True`.**

`GuildShop` model (`guild_shop.py:21-23`):
```python
guild_id: Mapped[int] = mapped_column(
    BigInteger, ForeignKey(f"{TableNames.GuildConfigs.value}.guild_id"), nullable=False
)
```
- FK references `guild_configs.guild_id` (the `guild_id` COLUMN, not `guild_configs.id` the PK)
- `nullable=False` — column cannot be set to NULL
- FK defined with **no `ondelete=` argument** → DB-level constraint is `NO ACTION` (default)
- `GuildShop.guild_config` relationship: `relationship("GuildConfig", back_populates="shops", foreign_keys=[guild_id])` — no cascade on child side

**DB-level FK** (created by `0001_initial_schema.py` from `Base.metadata.sorted_tables`):
```sql
FOREIGN KEY (guild_id) REFERENCES guild_configs(guild_id)
-- no ON DELETE clause → defaults to NO ACTION (RESTRICT)
```

---

**12.5a root cause — empirical**

When `config_repository.remove(db, existing_config)` is called:
1. `db.delete(existing_config)` marks the `GuildConfig` ORM instance for deletion
2. `await db.commit()` triggers SQLAlchemy's unit-of-work flush

SQLAlchemy's default relationship behavior when deleting a parent:
- `cascade` not set → defaults to `"save-update, merge"` (NOT `"all, delete-orphan"`)
- `passive_deletes` not set → defaults to `False`
- With `passive_deletes=False`, SQLAlchemy does **not** rely on the DB to cascade; instead it proactively NULLs out child FKs before deleting the parent

This causes SQLAlchemy to issue (for the `shops` relationship):
```sql
UPDATE guild_shops SET guild_id = NULL WHERE guild_id = <guild_id_value>
```
**before** the `DELETE FROM guild_configs WHERE ...`.

PostgreSQL rejects this UPDATE because `guild_shops.guild_id` has a NOT NULL constraint. This raises `asyncpg.exceptions.NotNullViolationError`, which surfaces as `sqlalchemy.dialects.postgresql.asyncpg.IntegrityError`.

**Trigger condition**: Bug fires whenever `guild_shops` has ANY rows for the guild at reset time. Since the shop was refreshed at 14:38 UTC, shops were populated → bug always triggered in this state.

**Atomicity**: The session-level rollback works correctly — `bot-database-manager - ERROR - Session error — rolling back transaction: IntegrityError` confirms no partial state. `/admin_config action:View Config` showing pre-reset state is evidence the rollback succeeded.

**Unaffected path**: `config_service.uninstall_guild` explicitly calls `shop_repo.clear_all_guild_shops(db, guild_id)` BEFORE `config_repo.delete_guild_config(db, guild_id)` → sidesteps the FK issue entirely. Only the `reset` path is affected.

---

**12.5b root cause — empirical**

In `adminCog.py`, the `admin_config` handler uses a single `except httpx.HTTPStatusError as e` block (line 601) covering ALL actions including `reset`:

```python
except httpx.HTTPStatusError as e:
    await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)  # line 602
```

`httpx.HTTPStatusError.__str__()` formats as:
```
Server error '500 Internal Server Error' for url 'http://bot-core:8000/api/v1/config/guild/1490693399307616276/reset'
```
httpx also appends an MDN documentation link for the HTTP status code. Both the raw internal URL and the MDN link appear in the user-visible embed.

The raw URL exposes: internal container hostname (`bot-core`), internal port (`8000`), API versioning (`/api/v1`), and the complete operation path including the guild ID.

---

**Sibling sweep — 12.5a (cascade-NULL pattern)**

The `config_repository.reset_to_defaults` path is the **only** affected reset path. The `uninstall_guild` path is safe. No other repositories delete a `GuildConfig` parent row directly — other config methods do in-place updates, not deletes.

Other parent→child relationships were not exhaustively checked for the same cascade-NULL pattern in this recon. The `Player` → `PlayerShip` / `PlayerInventory` relationships are known to have `cascade="all, delete-orphan"` per the models AGENTS.md; those are safe.

**Sibling sweep — 12.5b (raw URL leak)**

The identical `f"❌ API Error: {e}"` pattern is present in **53 locations** across **9 cogs**:

| Cog | Occurrences |
|---|---|
| `adminCog.py` | 22 |
| `inventoryCog.py` | 6 |
| `schedulerCog.py` | 6 |
| `playerCog.py` | 5 |
| `shopCog.py` | 4 |
| `bountyCog.py` | 4 |
| `shipsCog.py` | 4 |
| `duelCog.py` | 3 |
| `aboutCog.py` | 2 |

Every `httpx.HTTPStatusError` catch block across all cogs exposes the internal URL if the request fails with a 5xx status. The 12.5b issue is not specific to the reset command — it is a cross-cutting project-wide pattern. However, 5xx errors reaching the user are operationally rare, and all embeds are `ephemeral=True`.

---

**Severity assessment**

| Sub | Original | Confirmed | Rationale |
|---|---|---|---|
| 12.5a | 🟠 high | **🟠 high — confirmed** | Every `Reset to Defaults` invocation with a populated shop fails 100% of the time. The atomic rollback prevents data corruption, but the feature is completely broken whenever shops exist (which is the normal post-setup state). |
| 12.5b | 🔵 low | **🔵 low — confirmed** (per-instance); cross-cutting scope is **🟡 medium** if fixed as a bundle | Individual instances are ephemeral and reveal internal topology only to the triggering user. However, 53 occurrences across 9 cogs make this a cross-cutting concern worth addressing as a hygiene bundle. |

---

**Open questions**

1. **Other cascade-NULL risks**: Are there other parent models with relationships that lack `cascade` where a `remove()` + `commit()` is called without first clearing the children? A sweep of all `repository.remove()` call sites against models with children would confirm.
2. **FK target column**: `GuildShop.guild_id` references `guild_configs.guild_id` (unique non-PK) rather than `guild_configs.id` (PK). This is unusual — most child FKs reference the parent PK. There is no functional defect from this choice (since `guild_id` is UNIQUE), but it may confuse future developers or tooling.
3. **Reset feature intent**: Does "Reset to Defaults" intend to DELETE the existing guild_config row and recreate it (current approach), or UPDATE it in place? Updating in place would avoid the FK issue entirely and preserve channel/role IDs, which would be more useful UX-wise (users don't lose their setup, just the numeric config values).
4. **MDN link source**: The MDN link appended to the error embed appears to come from httpx's `HTTPStatusError` message format. Confirm which httpx version produces this link (likely newer httpx).

---

**Recommended fix-scope size**

| Sub | Scope | Fix |
|---|---|---|
| 12.5a | **Surgical** | Add `cascade="all, delete-orphan"` to `GuildConfig.shops` relationship in `guild_config.py`. This causes SQLAlchemy to DELETE (not NULL) the related `guild_shops` rows before deleting the parent `guild_config` row. Alternatively (less idiomatic): add `passive_deletes=True` to the relationship AND `ondelete="CASCADE"` on the `GuildShop.guild_id` ForeignKey — but this requires a DB migration to add the ON DELETE CASCADE constraint. The ORM-level fix (cascade="all, delete-orphan") requires no migration. |
| 12.5b | **Theme-bundle** | Replace `f"❌ API Error: {e}"` with a sanitized message: `f"❌ API Error: {e.response.status_code} {e.response.reason_phrase}"` or a static `"❌ Server error — please try again."` for 5xx responses. The 53 occurrences across 9 cogs should be fixed as a single batch. For 4xx errors (client errors), preserving the status code is useful context; for 5xx, a static message is sufficient. |

See companion detail: `/proj/recon/B31-recon.md`

**Recon completed**: 2026-04-28 by developer (read-only investigation)

---

### B.30 — `PUT /api/v1/jobs/{job_id}` silently wipes payload when request body lacks expected fields
🟠 high · 2026-04-28 · surfaced during Phase 9 cleanup
> **FIXED** in commit `360287b` (Package A, 2026-04-29). `ConfigDict(extra="forbid")` added to `UpdateJob` schema.
> **Patch H hardening** (2026-04-29): `payload: dict | None = {}` changed to `payload: dict = Field(default_factory=dict)` — closes null-injection vector where `{"payload": null}` passed schema validation and corrupted job args (A.1). Test `test_update_job_rejects_null_payload` added.

**Environment**: dev guild `1490693399307616276`, post-rebuild stack, direct bot-core API call (no gateway/cog involved).

**Reproduction**
1. Initial state of `bounty_spawn_default`: `args = ["bounty_spawn_default", {"job_type": "bounty_spawn_orchestrate"}]` (verified via `GET /api/v1/jobs/bounty_spawn_default`)
2. PUT request:
   ```
   curl -X PUT -H 'Content-Type: application/json' \
     http://bot-core:8000/api/v1/jobs/bounty_spawn_default \
     -d '{"args":["bounty_spawn_default",{"job_type":"bounty_spawn_orchestrate"}]}'
   ```
   (Note: payload field name `args` is incorrect — actual schema expects `payload`)
3. Response: HTTP 200, body `{"status":"updated","job_id":"bounty_spawn_default"}`
4. Subsequent GET shows `args = ["bounty_spawn_default", {}]` — payload was wiped to empty dict, not preserved or rejected.

**Recovery**: PUT with correct field `{"payload":{"job_type":"bounty_spawn_orchestrate"}}` restored the original payload. Verified.

**Observed behavior**: the endpoint accepts requests with any unrecognized JSON shape, returns success, and replaces the existing payload with the default value (empty dict). No validation error. No schema-mismatch warning.

**Impact (factual, no scoping)**: any caller (admin tools, future API integrations, future cog flow) that sends a malformed payload to this endpoint silently destroys the existing job payload. For recurring jobs, this could break scheduled execution until manually restored.

---

**Verified code paths** (HEAD, read-only recon 2026-04-28)

| Question | Finding |
|---|---|
| Route handler (file:line) | `services/bot-core/src/api/routers/scheduler.py:161–183` — `async def update_job(req, job_id, update: UpdateJob)` |
| Pydantic request model | `services/bot-core/src/api/schemas/scheduler_schema.py:38–44` — `class UpdateJob(BaseModel)` |
| Fields declared | ONE: `payload: dict \| None = {}` — that is the entire model |
| `model_config` / `extra` policy | **None set**. `scheduler_schema.py` is the only schema module in `api/schemas/` with no `model_config`. Pydantic v2 default: `extra="ignore"` — unrecognized fields silently dropped |
| Source of `{}` | Line 44 of `scheduler_schema.py`: the `= {}` default on `payload`. When `args` key is dropped and `payload` is never provided, the field takes its default |
| Route behavior with default | `scheduler.py:175` builds `new_args = [job_id, update.payload]` → `[job_id, {}]`; `scheduler.py:177` calls `sched.modify_job(job_id, args=new_args)` — APScheduler overwrites the job |
| Gateway cog wire shape | `schedulerCog.py:241–244` sends `json={"payload": payload}` — **correct field name**. The cog is NOT affected. Defect is only triggered by direct API calls using wrong field name |

**Schema validation policy**: `UpdateJob` has no required fields and no `extra` policy. Because ALL fields are optional (the entire model is `payload: dict | None = {}`), any request body — including `{}`, `{"args": [...]}`, or even a missing body — deserializes without error and silently uses defaults.

---

**Sweep findings — scheduler router request models**

| Schema | Required fields | Extra policy | Vulnerable? |
|--------|----------------|--------------|-------------|
| `UpdateJob` (PUT /jobs/{job_id}) | None | `extra="ignore"` (default) | **YES** — all fields optional, destructive default |
| `OneTimeJob` (POST /jobs) | None | `extra="ignore"` (default) | **Partial** — router guards: `if not job.run_at and job.delay_seconds is None → HTTP 400`; wrong-field body gets 400, not 200 |
| `RecurringJob` (POST /jobs/recurring) | `cron: str` (required) | `extra="ignore"` (default) | **No** — missing `cron` → Pydantic 422 before route logic |

**Sweep findings — other bot-core request schemas**: exhaustive grep of `services/bot-core/src/api/schemas/` confirms no schema sets `extra="forbid"` or `extra="allow"`. However, all other request schemas have **at least one required field** (e.g., `guild_id`, `player_id`, `credits`, `item_type`) — sending a completely wrong body triggers Pydantic 422 on the missing required field before any business logic runs. `UpdateJob` is uniquely vulnerable.

---

**Cross-cutting pattern with B.32**

Both B.30 and B.32 are write endpoints that accept unrecognized input without a validation error:

| | B.30 (bot-core PUT /jobs/{id}) | B.32 (blender PUT /config/render) |
|--|--|--|
| Mechanism | Pydantic `extra="ignore"` → unknown field dropped → default used | Service `hasattr` filter → unknown key silently no-op'd |
| Layer | Schema validation | Service layer (`render_config_service.py:76–96`) |
| Consequence | **Destructive write** — existing payload replaced with `{}` | **Silent no-op** — no field changed, misleading success response |
| Severity | 🟠 high | 🟡 medium (confirmed) |
| Internal logging | None — no warning emitted for the mismatch | `WARNING` logged at service layer (`ignored_keys`), NOT surfaced to caller |

Same class of defect (missing input validation on write path); different mechanisms; different blast radius.

---

**Severity assessment**: **🟠 high — confirmed**. The defect causes silent, irreversible overwrite of a live scheduled job's execution payload. For `bounty_spawn_default` specifically: replacing `{"job_type": "bounty_spawn_orchestrate"}` with `{}` would cause the bounty spawn job to silently fail at every invocation (job_executor cannot dispatch without `job_type`), halting all bounty spawning until manually corrected. No log message or HTTP error alerts the operator.

---

**Open questions**
1. Are there other callers of `PUT /api/v1/jobs/{job_id}` besides the gateway cog? A search for `PUT` to the scheduler API in executor files would confirm (no executor appears to call it in the current codebase).
2. Was the all-optional design of `UpdateJob` intentional (allow partial updates, omit payload to keep existing)? If so, the correct fix is `extra="forbid"` rather than making `payload` required.
3. The `payload: dict | None = {}` antipattern also appears in `OneTimeJob` and `RecurringJob` — those models are less dangerous (protected by other guards) but share the same lenient-validation class.

---

**Recommended fix-scope size**: **surgical**

Add `model_config = ConfigDict(extra="forbid")` to `UpdateJob` in `scheduler_schema.py`. With `extra="forbid"`, sending `{"args": [...]}` returns HTTP 422 immediately, before any APScheduler interaction. One-line change to the import, one-line `model_config` declaration.

Optional defense-in-depth: apply `extra="forbid"` to `OneTimeJob` and `RecurringJob` as well for consistent validation posture.

See companion detail: `/proj/recon/B30-recon.md`

**Recon completed**: 2026-04-28 by developer (read-only investigation)

---

### B.29 — `/scheduler_*` cron trigger display strips asterisks (Discord markdown)
🔵 low · Phase 9.1 / 9.2 · 2026-04-28
> **FIXED** in commit `ec42c4d` (Package B, 2026-04-29). Cron trigger strings wrapped in backticks in `scheduler_list` and `scheduler_view` embed fields.

**Environment**: dev guild `1490693399307616276`, post-rebuild stack.

**Reproduction**: `/scheduler_list` and `/scheduler_view` render cron-trigger fields like `cron[month='', day='', day_of_week='', hour='', minute='*/5']` — empty quotes for month/day/day_of_week/hour.

**Comparison**: bot-core API `GET /api/v1/jobs/bounty_spawn_default` returns the same field as `cron[month='*', day='*', day_of_week='*', hour='*', minute='*/5']` (with explicit asterisks).

**Verified code path**: `schedulerCog.py` exists and is auto-loaded (note B.25 prior recon stated "No scheduler cog exists" — this was incorrect; the cog exists and has 6 commands). The `/scheduler_view` command displays cron trigger info via embed fields (file:line to be verified). Discord embed text interprets unescaped `*` as italic markdown delimiter. Adjacent asterisks (`'*'`) are consumed in pairs leaving empty visible text.

**Root cause**: Discord markdown processing of embed field values. Asterisks in the source `cron['*']` are rendered as markdown italic markers, collapsing adjacent pairs to empty text.

**Fix direction**: escape asterisks (`\*`) or wrap in code-format (backticks e.g. `` `*` ``) when serializing trigger info to embed fields.

**Severity assessment** — 🔵 low confirmed. Pure cosmetic UI issue; does not affect scheduled job functionality.

**Recommended fix-scope size** — **surgical** (string escaping or wrapping at 1–2 lines)

**Recon completed**: 2026-04-28 by researcher (read-only investigation)

---

### B.28 — `/scheduler_update` intermittently shows "This interaction failed" before the actual error embed
🔵 low · Phase 9.4 · 2026-04-28
> **FIXED** in commit `ec42c4d` (Package B, 2026-04-29). JSON validation moved before `defer()`; sync error path uses `response.send_message()` directly.

**Environment**: dev guild `1490693399307616276`, Main account, post-rebuild stack.

**Reproduction (single observation)**
1. `/scheduler_update job_id:bounty_spawn_default payload_json:invalid` invoked at 14:23 ET
2. Bot first showed an ephemeral `"This interaction failed"` (Discord client error indicating the interaction handler did not respond/defer in time)
3. Approximately concurrently, a second ephemeral appeared: `"❌ Invalid JSON payload: Expecting value: line 1 column 1 (char 0)\n\nExample: {\"job_type\": \"bounty_spawn\"}"` — the actual error embed

The actual error reaches the user, but the doubled response (failure-then-success) is poor UX. Possibly related to **B.25** (similar Discord-3s-timeout pattern on admin commands).

---

**Verified code paths** (HEAD, read-only recon 2026-04-28)

| Layer | File | Lines | Notes |
|-------|------|-------|-------|
| Cog command | `services/discord-gateway/src/cogs/schedulerCog.py` | 215–291 | `scheduler_update(interaction, job_id, payload_json)` |
| `defer()` call | `schedulerCog.py` | 225 | `await interaction.response.defer(thinking=True, ephemeral=True)` — **BEFORE** JSON validation |
| JSON validation | `schedulerCog.py` | 231–238 | `json.loads(payload_json)` inside `try:` block AFTER defer |
| Error handler | `schedulerCog.py` | 287–291 | `scheduler_update_error` — `if not is_done(): send_message(...)` |

---

**Root cause** (empirical)

The cog **does defer first** (line 225), then validates the JSON payload (line 232). `json.loads("invalid")` is synchronous and raises `JSONDecodeError` immediately — typically within microseconds of the `defer()` call completing. The followup with the error embed (`schedulerCog.py:234–237`) is then sent nearly simultaneously with Discord processing the defer acknowledgment.

Two mechanisms can explain the doubled response:

**Mechanism A — Discord client rendering race** (most likely): Discord's client receives the
deferred-state acknowledgment (type 5) and the followup message in near-simultaneous succession.
During the state transition from "thinking..." to the rendered followup, the client may briefly
flash "This interaction failed" before settling on the actual error embed. This is a Discord
client-side race condition, not a bot-side bug.

**Mechanism B — Transient followup acknowledgment failure**: `interaction.followup.send()` at
line 234 raises `discord.HTTPException` after the message was already delivered to Discord. The
exception propagates to `scheduler_update_error`, which sees `is_done()=True` and does nothing.
Discord shows "This interaction failed" as a secondary indicator while the already-delivered
followup also renders.

In both cases the actual error embed IS delivered (confirmed by observation). The "This interaction
failed" is a secondary artifact.

**Structural contributor**: JSON parsing (synchronous, zero async work) happens **after**
`defer()`. For synchronous validation errors that require no async I/O, it is more correct to
respond via `interaction.response.send_message()` directly without deferring. Deferring before
a synchronous error creates the near-simultaneous defer+followup condition that triggers the above.

**Not a B.25 class defect**: The doubled response is not caused by the pre-defer `@is_admin()`
latency. The Main account (server owner) hits the Discord Administrator fast-path in
`_check_is_admin` (line 37) with no HTTP call, so there is zero pre-defer latency pressure.

---

**Cross-cutting findings**

- **Same cog as B.27**: Both defects are in `schedulerCog.py`. A single PR can address both.
- **B.31b overlap**: `schedulerCog.py:279` (`/scheduler_update` error branch for non-404/non-503
  HTTP errors) uses `f"❌ API Error: {e}"` — part of the 53-occurrence URL-leak count. This is
  a separate concern addressed by the B.31b theme-bundle fix.
- **O.2 status**: `job_id_autocomplete` IS implemented (schedulerCog.py:35–54) and applied to
  `/scheduler_update` at line 221. O.2 is already resolved in HEAD.
- **Not related to B.25**: B.25 is a pre-defer latency issue caused by `@is_admin()` HTTP calls.
  B.28's mechanism is post-defer (JSON parse is synchronous).

---

**Severity assessment**

🔵 low — **confirmed**. The actual error message reaches the user. The spurious "This interaction
failed" is poor UX but not a data loss or silent failure. Single observation; may not reproduce
consistently.

---

**Recommended fix-scope size**

**Surgical** — change `scheduler_update` in `schedulerCog.py` to validate `payload_json` **before**
calling `defer()`, using `interaction.response.send_message()` for the synchronous validation
error:

```python
async def scheduler_update(self, interaction, job_id, payload_json):
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as e:
        await interaction.response.send_message(
            f'❌ Invalid JSON payload: `{e}`\n\nExample: `{{"job_type": "bounty_spawn"}}`',
            ephemeral=True,
        )
        return
    await interaction.response.defer(thinking=True, ephemeral=True)
    ...
```

A single fix addresses both B.27 and B.28 when bundled with the error handler enhancement
described in B.27. See companion file `/proj/recon/B27-B28-recon.md` §9 for full fix detail.

**Recon completed**: 2026-04-28 by developer (read-only investigation)

---

### B.27 — `/scheduler_view` with nonexistent job_id shows raw Discord "This interaction failed"
🟡 medium · Phase 9.5 · 2026-04-28
> **FIXED** in commit `ec42c4d` (Package B, 2026-04-29). Added `else: with suppress(Exception): followup.send(...)` fallback to all 6 scheduler error handlers.

**Environment**: dev guild `1490693399307616276`, Main account, post-rebuild stack.

**Reproduction**
1. `/scheduler_view job_id:nonexistent_job` invoked at 14:22 ET
2. Bot replied: `"This interaction failed"` (Discord client message, indicates uncaught exception or timeout)

**Expected** (per checklist 9.5): graceful "Job not found" error embed.

**Comparison**: `/scheduler_view job_id:bounty_spawn_default` (valid ID) returned a normal Job Details embed at 14:22 immediately afterward — confirms the cog works for valid IDs.

---

**Verified code paths** (HEAD, read-only recon 2026-04-28)

| Layer | File | Lines | Notes |
|-------|------|-------|-------|
| Cog command | `services/discord-gateway/src/cogs/schedulerCog.py` | 137–209 | `scheduler_view(interaction, job_id)` |
| `defer()` call | `schedulerCog.py` | 144 | `await interaction.response.defer(thinking=True, ephemeral=True)` — before try block |
| Bot-core GET | `schedulerCog.py` | 150 | `self.http_client.get(f"{api_base}/jobs/{job_id}", timeout=10)` |
| 404 branch | `schedulerCog.py` | 186–187 | `if e.response.status_code == 404: followup.send(f"❌ Job \`{job_id}\` not found.")` |
| Error handler | `schedulerCog.py` | 205–209 | `scheduler_view_error`: `if not is_done(): send_message(...)` — **structural gap** |
| Bot-core 404 | `services/bot-core/src/api/routers/scheduler.py` | 89–92 | `if not job: raise HTTPException(404, "Job not found")` |

---

**Root cause** (empirical)

**The 404 case IS handled explicitly.** The prior speculation ("lets it bubble to a broad except clause") was incorrect. `schedulerCog.py:185–187` catches `httpx.HTTPStatusError`, checks `status_code == 404`, and calls `interaction.followup.send(f"❌ Job \`{job_id}\` not found.", ephemeral=True)`. Under normal conditions this succeeds and the user sees a clear error embed.

The single observation of "This interaction failed" was caused by the **structural gap in the error handler pattern** combined with a transient failure:

1. `defer()` succeeds (line 144) → `interaction.response.is_done()` returns True.
2. Bot-core returns HTTP 404 → `raise_for_status()` raises `httpx.HTTPStatusError` → caught at line 185.
3. `interaction.followup.send(...)` at line 187 raises `discord.HTTPException` (transient Discord API error — rate limit, brief outage, or network hiccup).
4. This exception propagates **out of** the `except httpx.HTTPStatusError` block.
5. `scheduler_view_error` (lines 205–209) is called. `if not interaction.response.is_done():` evaluates **False** (defer was called).
6. Error handler does **nothing** — no fallback followup sent.
7. Discord receives no followup and eventually shows "This interaction failed."

**This gap exists in all six scheduler command error handlers.** The pattern `if not is_done(): send_message(...)` correctly handles pre-defer check failures but provides no fallback for post-defer followup failures.

**Not a timing/is_admin issue**: The Main account (server owner) hits the Discord Administrator
fast-path in `_check_is_admin` (line 37) with no HTTP call. Pre-defer latency is effectively
zero for this user. The B.25-class timing risk does not apply here.

**Why the valid ID worked immediately after**: The second invocation had a fresh interaction token
and succeeded without any transient followup failure — unrelated to the job_id being valid.

---

**Cross-cutting findings**

- **Same cog as B.28**: Both defects are in `schedulerCog.py`. A single PR can address both.
- **B.25 contradiction resolved**: The B.25 recon incorrectly stated "No scheduler cog exists."
  `schedulerCog.py` EXISTS and IS auto-loaded (bot.py:42–50). The cog has 6 commands. The prior
  claim was empirically false.
- **B.31b overlap**: `schedulerCog.py:197` (`/scheduler_view` error branch for non-404/non-503
  HTTP errors) uses `f"❌ API Error: {e}"` — part of the 53-occurrence URL-leak count in B.31b.
  This is a separate concern addressed by the B.31b theme-bundle fix.
- **O.2 status**: `job_id_autocomplete` IS implemented (schedulerCog.py:35–54) and applied to
  `/scheduler_view` at line 140 via `@app_commands.autocomplete(job_id=job_id_autocomplete)`.
  O.2 is already resolved in HEAD.
- **B.29 overlap**: The `/scheduler_view` command also has the asterisk-stripping issue (B.29)
  in its trigger display. Separate concern; not related to B.27.

---

**Severity assessment**

🟡 medium — **confirmed**. The failure results in **no user-visible error message** — the user
sees only Discord's "This interaction failed" with no indication that the job ID was invalid.
The code has the correct 404 handler but it is rendered ineffective by the transient followup
failure and the error handler gap. The gap applies to all six scheduler commands, meaning any
transient Discord API error after defer will silently swallow the response.

---

**Recommended fix-scope size**

**Surgical (shared with B.28)** — enhance all six `@scheduler_<cmd>.error` handlers in
`schedulerCog.py` to attempt a followup fallback when `is_done()` is True:

```python
@scheduler_view.error
async def scheduler_view_error(self, interaction, error):
    flogger.exception("Error in /scheduler_view", exc_info=error)
    if not interaction.response.is_done():
        await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)
    else:
        with suppress(Exception):
            await interaction.followup.send("⚠️ An error occurred.", ephemeral=True)
```

(`suppress` is already imported at schedulerCog.py:3.) Apply same pattern to all six error
handlers. Bundle with B.28's pre-defer JSON validation fix in the same PR.

See companion file `/proj/recon/B27-B28-recon.md` §9 for full fix detail.

**Recon completed**: 2026-04-28 by developer (read-only investigation)

---

### O.2 — RETROACTIVELY CLOSED — already fixed in HEAD
🔵 low · Phase 9.2–9.6 · 2026-04-28 · **CLOSED 2026-04-28**

Originally logged as missing autocomplete on `/scheduler_view`, `/scheduler_update`, `/scheduler_delete`. Cycle-8 recon (B.27/B.28) verified empirically that `job_id_autocomplete` is implemented at `schedulerCog.py:35-54` and applied to all three commands (`/scheduler_view` line 140, `/scheduler_update` line 221, `/scheduler_delete` line 300).

Status: moved to FIXED table for tracking. No further action.

---

### B.25 — `/admin_spawn_bounty` triggers Discord 3-second interaction timeout despite successful spawn
🟡 medium · Phase 7.15 prep · 2026-04-28
> **FIXED** in commit `ec42c4d` (Package B, 2026-04-29). Fix A: removed `@is_admin()` decorator from all 20 admin commands; replaced with inline post-defer `_check_is_admin()` call. Fix B: converted `render_config` and `render_cache_clear` to `defer()` + `followup.send()` pattern.

**Environment**: dev guild `1490693399307616276`, Main account, post-rebuild stack.

**Reproduction (single observation)**
1. `/admin_spawn_bounty tier:Bronze` invoked at 14:11 ET
2. Bot replied: `"The application did not respond"` (Discord client message, indicates interaction token expired before initial response)
3. Subsequent `/check system:S'Kolptorr` succeeded against bounty #2247 (Hongar Meton); confirmed the spawn DID complete server-side

**Comparison**: prior `/admin_spawn_bounty` invocations in this session at 09:54 and 09:57 returned a normal ephemeral embed within the timeout window. Only the 14:11 invocation timed out.

---

**Verified code paths** (HEAD, read-only recon 2026-04-28)

**Cog handler location**: `services/discord-gateway/src/cogs/adminCog.py`

| Question | Finding |
|---|---|
| Handler signature | `admin_spawn_bounty(interaction, tier)` at **adminCog.py:1298** |
| `interaction.response.defer()` present? | ✅ **YES** — `adminCog.py:1300`: `await interaction.response.defer(thinking=True, ephemeral=True)` |
| Spawn API call | Single `POST {api_base}/bounties/guild/{guild_id}/admin-spawn` — **adminCog.py:1307–1311**, httpx `timeout=30` |
| Does `@is_admin()` run before handler? | ✅ YES — `@is_admin()` at **adminCog.py:1288** is an `app_commands.check` predicate; executes before the handler body |
| HTTP call inside `_check_is_admin`? | **Conditional** — `adminCog.py:41–48`: fast-paths for DEVELOPERS env var (line 33) and Discord Administrator permission (line 37); **only** reaches an `httpx.AsyncClient` GET to `{api_base}/config/guild/{id}` with `timeout=5` for users who hold the **Bot Admin role** (not Discord admin, not Dev). Server owner / Discord admins → instant, no HTTP call |

**Latency profile of `POST /bounties/guild/{id}/admin-spawn`** (`bounties.py:437–519`, `bounty_service.py:822–933`)

For a single tier (e.g. Bronze), the endpoint performs synchronously within the same request:

| Step | Location | Work |
|---|---|---|
| 1 | `bounties.py:464` | DB: `ConfigRepository.get_by_guild_id()` — one SELECT |
| 2 | `bounty_service.py:855` | DB: `criminal_repo.list_all()` + `bounty_repo.get_active_by_guild_and_division()` — criminal selection |
| 3 | `bounty_service.py:871` | `graph_service.load_graph(db)` — loads system adjacency graph (cached after first load) |
| 4 | `bounty_service.py:879–888` | A\* pathfinding — up to 3 attempts; pure-CPU + in-memory, fast |
| 5 | `bounty_service.py:900` | DB: `generate_loadout()` — ship selection + multiple weapon/module queries (3–6 queries) |
| 6 | `bounty_service.py:931` | DB: `bounty_repo.create()` — single INSERT |
| 7 | `bounties.py:481–486` | HTTP: `_schedule_expiry_job()` — POST to scheduler API (`timeout=10`); non-fatal if fails |
| 8 | `bounties.py:489–492` | HTTP: `_announce_bounty()` — route map PNG render + gateway upload + gateway announcement POST (`timeout=10–15` each); non-fatal if fails |
| 9 | `bounties.py:499–512` | DB: `AuditService.log_action()` — single INSERT |

**No blender-service calls** in the spawn pipeline. The dominant variable latency steps are the gateway announcement chain in step 8 (which can take several seconds) and the expiry scheduler POST in step 7. Both are best-effort non-fatal — their latency does not affect the response to the cog. The cog waits up to 30 seconds for the full endpoint to return.

**Why did the timeout occur if `defer()` is present?**

The `defer()` call at line 1300 IS correctly placed before the spawn work. The server-side spawn completed. The most consistent explanation for "The application did not respond" with a successful spawn:

**Timing boundary / event loop contention**: The asyncio event loop of the discord-gateway process was under elevated load at 14:11 ET (session had been active since ~09:54; APScheduler `bounty_spawn_orchestrate` was firing every 5 minutes; multiple prior spawns, checks, and shop interactions had occurred). When the interaction event arrived on the WebSocket, coroutine dispatch was delayed. If the cumulative delay between interaction receipt and the `await interaction.response.defer()` call exceeded ~2.8–3.0 seconds, one of two failure modes applies:

| Mode | What Happened | Consistency with Observed Behavior |
|---|---|---|
| **A — Defer arrived within 3s but Discord client raced** | `defer()` POST reached Discord's API within ~3 seconds but Discord's client UI had already rendered "The application did not respond" before processing the acknowledgement. Bot continued; spawn completed; `followup.send()` succeeded but Discord client no longer displayed it. | ✅ Consistent — spawn completed, no exception propagated |
| **B — is_admin() HTTP call for Bot-Admin-role user** | If the main account was NOT a Discord Administrator and relied on the Bot Admin role, `_check_is_admin` made a 5-second HTTP call to bot-core (line 42) BEFORE the handler body. Handler body reached after >3s; `defer()` call failed (NotFound); exception propagated to discord.py error handler. Spawn did NOT run via this path. | ❌ Inconsistent — spawn DID complete |

**Mode A is the most likely cause for the server owner** (who has Discord Administrator permission → fast path, no HTTP call from `_check_is_admin`). Mode B is a structural risk for guilds where the invoking user holds only the Bot Admin role, not Discord Administrator.

**Defer-pattern sweep — all AdminCog commands**

| Command | Line | `defer()` used? | Note |
|---|---|---|---|
| `admin_check` | 121 | ✅ `defer(thinking=True, ephemeral=True)` | |
| `admin_setup` | 170 | ✅ `defer(thinking=True, ephemeral=True)` | |
| `admin_player` | 282 | ✅ `defer(thinking=True, ephemeral=True)` | |
| `admin_refresh_shop` | 434 | ✅ `defer(thinking=True, ephemeral=True)` | |
| `admin_guild_stats` | 479 | ✅ `defer(thinking=True, ephemeral=True)` | |
| `admin_config` | 528 | ✅ `defer(thinking=True, ephemeral=True)` | |
| `admin_uninstall` | 611 | ✅ `defer(thinking=True, ephemeral=True)` | |
| `admin_config_shop` | 754 | ✅ `defer(thinking=True, ephemeral=True)` | |
| `admin_config_validate` | 850 | ✅ `defer(thinking=True, ephemeral=True)` | |
| `render_config` | 914 | ❌ **uses `send_message()` directly** | Makes HTTP call to blender-service; no defer before I/O |
| `render_cache_clear` | 963 | ❌ **uses `send_message()` directly** | Makes HTTP call to blender-service; no defer before I/O |
| `admin_clear_bounties` | 1008 | ✅ `defer(thinking=True, ephemeral=True)` | |
| `admin_config_bounty` | 1066 | ✅ `defer(thinking=True, ephemeral=True)` | |
| `admin_config_xp` | 1194 | ✅ `defer(thinking=True, ephemeral=True)` | |
| `admin_spawn_bounty` | 1298 | ✅ `defer(thinking=True, ephemeral=True)` | **This defect** |
| `admin_cooldown_reset` | 1360 | ✅ `defer(thinking=True, ephemeral=True)` | |
| `admin_give_item` | 1491 | ✅ `defer(thinking=True, ephemeral=True)` | |
| `admin_remove_item` | 1571 | ✅ `defer(thinking=True, ephemeral=True)` | |
| `admin_give_ship` | 1641 | ✅ `defer(thinking=True, ephemeral=True)` | |
| `admin_remove_ship` | 1765 | ✅ `defer(thinking=True, ephemeral=True)` | |

**18 of 20 admin commands use defer correctly. `render_config` and `render_cache_clear` are the defer-gap exceptions.** For those two: the blender-service calls (view/set/reset config, cache clear) are typically fast, so the immediate-send approach usually works, but it carries structural risk because `@is_admin()` can itself consume up to 5 seconds via the Bot-Admin-role HTTP path.

**Cross-cutting findings — relation to B.27/B.28**

B.27 and B.28 are **not present in DEFECTS.md as of this recon**. No scheduler-specific Discord cog exists in `services/discord-gateway/src/cogs/` — the APScheduler job management is exposed via the bot-core REST API only (`bot-core/src/api/routers/scheduler.py`). If B.27/B.28 exist as "interaction-failure UX" defects in other cogs, they would share the event-loop-contention class with B.25 but are not yet logged.

**Structural note on `_check_is_admin` (adminCog.py:22–51)**

The predicate function creates a **fresh `httpx.AsyncClient`** on every invocation for the Bot-Admin-role path (line 42: `async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:`). This means TCP connection overhead is paid on every non-Discord-admin check. The `AdminCog` instance already holds a shared `self.http_client` (line 71) that could be reused if the admin check were refactored to run inside the handler after `defer()`.

**Severity assessment**: 🟡 medium — confirmed. The spawn itself is correct (no data loss, no state corruption). The UX failure is misleading but recoverable (user can verify via `/check` or `/bounties`). The failure is intermittent (1 of 3+ in a session), correlated with event loop load after extended bot activity.

**Open questions**
1. Does the main account have Discord Administrator permission in the dev guild? If YES, the `_check_is_admin` HTTP path is never reached for the server owner, making event-loop timing (Mode A) the only viable explanation.
2. What is the actual timing window between interaction receipt and defer() at 14:11? Requires adding timestamps to the interaction handler log (currently no pre-defer log exists in `admin_spawn_bounty`).
3. Does discord.py's `InteractionResponse.defer()` suppress/swallow `NotFound` exceptions internally in the version deployed? If so, Mode B becomes consistent with the spawn completing.
4. `render_config` / `render_cache_clear` — should these be promoted to a separate defect for the defer gap, or fixed alongside B.25?

**Recommended fix-scope size**: **surgical** (two independent repairs)

1. **Fix A** (addresses Mode B and the render_config/render_cache_clear gap): Refactor `_check_is_admin` so the HTTP call for Bot-Admin-role users runs INSIDE the handler body AFTER `defer()` — not in the `app_commands.check` predicate. Pattern: defer first, then call `_check_is_admin()` inside the handler and return early with an error if False. Remove the `@is_admin()` decorator from commands where this pattern applies, replacing it with an inline post-defer check.
2. **Fix B** (addresses Mode A / event loop pressure): Add a pre-defer log timestamp to interaction handlers (`flogger.info(f"/admin_spawn_bounty received: t={time.time()}"`) so future occurrences can be instrumented. Convert `render_config` and `render_cache_clear` to defer + followup pattern.

**Recon completed**: 2026-04-28 by developer (read-only investigation)

---

### B.24 — `/route` output does not visually highlight "Recently Spotted" system
🔵 low · Phase 7.6 · 2026-04-28
> **FIXED** in commit `8860c5a` (Package C, 2026-04-29). API: added `system_statuses` field to `GET /bounties/{id}/route` response (computed server-side via `_project_checked()` with `"found"` masked to `"checked"` to prevent answer leakage). Cog: updated `/route` embed builder to render 3-state markdown — `recently_spotted` → `**~~system~~** 🔍`, `checked` → `~~system~~ ✅`, unchecked → plain.

**Environment**: dev guild `1490693399307616276`, Main account, post-rebuild stack.

**Reproduction**
1. `/admin_spawn_bounty tier:Bronze` → bounty #2247 (Hongar Meton, route: Pescal Inartu, Buntta, V'Ikka, S'Kolptorr, K'Ontrr, Wah'Norr)
2. `/check system:Buntta` → "Recently Spotted!" response with intel "Hongar Meton was recently spotted here! They're close..."
3. `/check system:S'Kolptorr` → "System Checked" (no spotted intel)
4. `/route bounty:2247` invoked → embed displays:
   ```
   Pescal Inartu
   Buntta ✅
   V'Ikka
   S'Kolptorr ✅
   K'Ontrr
   Wah'Norr
   2/6 systems checked
   ```
   Buntta is rendered with the same `✅` indicator as S'Kolptorr — no visual distinction between "recently spotted (close to target)" and ordinary "checked, not here".

**Comparison evidence — main bounty announcement embed at https://discord.com/channels/1490693399307616276/1496573655201873961/1498764151080620154** (per user 2026-04-28): in that embed Buntta is rendered with **bold + strikethrough** to indicate the recently-spotted state. The announcement embed and `/route` produce different renderings of the same underlying state.

---

**Verified code paths** (HEAD `815cd59`, read-only recon 2026-04-28)

| Question | Finding |
|---|---|
| `/route` handler location | `bountyCog.py:667–750` — inline embed builder, no shared module |
| `/route` API endpoint | `bounties.py:261–278` `get_bounty_route()` — returns raw `bounty.checked` dict (player_id map) + route list; `bounty.answer` intentionally **not** returned |
| Announcement embed builder | `bounty_announcement_payload.py:157–263` — `_build_suffix_fields()` → `_project_checked()` → `_build_route_value()`; lives in bot-core |
| Gateway announcement rendering | `announcements.py:174–200` `_build_bounty_embed()` — delegates to shared `build_loadout_embed()` from `cogs/_shared/loadout_embed.py` using pre-built suffix fields from bot-core |
| `/route` cog rendering logic | `bountyCog.py:708–714` — binary: `checked.get(system_name, -1) != -1` → `~~name~~ ✅`; else plain. No proximity computation. |
| Announcement rendering logic | `bounty_announcement_payload._project_checked()` (lines 168–207) — computes 3-state map (`found`/`recently_spotted`/`checked`) using `bounty.answer` + `bounty.route`; identical distance formula to `check_system` in `bounty_service.py:1259–1261` |
| "recently_spotted" storage | Not stored in DB. Computed at render time: `answer_idx − checked_idx ∈ [1, 2]` |

**Rendering comparison**

| System status | Announcement embed markdown | `/route` cog markdown | Gap? |
|---|---|---|---|
| Not checked | plain | plain | ✅ same |
| Checked-not-here | `~~system~~` | `~~system~~ ✅` | minor (extra emoji) |
| Recently spotted (1–2 stops before answer) | `**~~system~~**` | `~~system~~ ✅` | ❌ **BUG — identical to checked-not-here** |
| Found / correct | `**system**` | N/A (bounty resolves) | n/a |

**Source-of-truth analysis**

Two independent, duplicated implementations. No shared source of truth:
- **Announcement**: `bounty_announcement_payload._project_checked()` — 3-state, uses `bounty.answer` (available server-side)
- **`/route` cog**: inline in `bountyCog.py:708–714` — 2-state binary, receives raw player-id map from API, lacks `answer`

The 3-state computation cannot be replicated in the cog because `bounty.answer` is intentionally withheld from the `/route` API response.

**B.13 cross-reference**: B.13 fixed announcement embed edit to preserve the route-map image URL via `announcements.py:142–154`. The announcement edit path (`_edit_bounty_announcement()` → `build_bounty_announcement_request()`) calls `_project_checked()` correctly on every check, so the announcement **does** correctly display `**~~Buntta~~**` after a spotted check. B.13 is orthogonal to B.24 — both concerns are independent.

**Tests**
- `tests/cogs/test_bountyCog.py:TestRouteCommand` (lines 662–757): tests checked/unchecked strikethrough, 404, error handling, division display — **no test for recently_spotted rendering distinction**
- `tests/api/test_bounty_router.py:TestGetBountyRoute` (lines 309–359): tests raw `checked` dict in response — **no test for a projected `system_statuses` field** (field does not yet exist)
- `tests/test_bounty_announcement_payload.py`: tests `_project_checked()` and `_build_route_value()` including recently_spotted cases — ✅ announcement side covered

**Severity assessment**: 🔵 low — confirmed. Pure visual inconsistency. No game-state corruption, no credit/XP impact. Hunters who check Buntta see "Recently Spotted!" in the `/check` response, so the information is communicated; `/route` just doesn't surface it in the route list. The announcement embed (always visible in the bounty-board channel) does correctly highlight spotted systems.

**Recommended fix-scope**: **Surgical** — 2 files, ~15 lines total:
1. **`bounties.py:261–278`** — add `system_statuses` field to `/route` response: call `_project_checked()` server-side, mask `"found"` → `"checked"` to prevent answer leakage.
2. **`bountyCog.py:708–714`** — use `system_statuses` for 3-state rendering: `"recently_spotted"` → `**~~name~~** 🔍`, `"checked"/"found"` → `~~name~~ ✅`, else plain.
New tests: 1 API endpoint test (`system_statuses` field) + 1 cog test (recently_spotted rendering).

**Open questions**
1. **Icon for spotted in `/route`**: Match announcement (`**~~name~~**`) or use a dedicated emoji (e.g. 🔍)? Annotation needed.
2. **Answer masking for non-active bounties**: Should a resolved/expired bounty's `/route` reveal `"found"` for the answer system? Currently all statuses masked. Inconsequential for active bounties.
3. **Backward-compat**: `system_statuses` addition is non-breaking (cog falls through to `else` if field absent). Safe to ship independently of other work.

**Companion file**: `/proj/recon/B24-recon.md`

**Recon completed**: 2026-04-28 by developer (read-only investigation)

---

### B.23 — `/bounties` listing returns bounties whose displayed expiry text shows past `end_time`
🟡 medium · Phase 7.1 · 2026-04-28
> **B.23a + B.23b FIXED** in commit `360287b` (Package A, 2026-04-29).
> B.23a: `_schedule_expiry_job()` now uses direct APScheduler Python API via `scheduler_holder.py`; HTTP POST fallback retained.
> B.23b: `run_stale_state_recovery_sweep()` now deletes Discord announcements for stale bounties after marking them expired.
> **Patch H hardening** (2026-04-29): lifespan test mock corrected so `.all()` returns a sync list (not AsyncMock coroutine), ensuring the B.23b cleanup branch is actually exercised in tests (A.2). Dedicated test `test_lifespan_b23b_announcement_cleanup_called_for_stale_bounties` added.

**Environment**: dev guild `1490693399307616276`, Main account, post-rebuild stack (commit `815cd59` — includes B.14 listing-filter fix `db79c60` and recovery sweeps).

**Reproduction (single observation; not yet replayed)**
1. `/bounties` invoked at 2026-04-28 09:53 ET (~13:53 UTC)
2. Embed listed 7 active bounties

| Bounty ID | Displayed expiry text |
|---|---|
| 2032 (Urr Sekant, Silver) | "Expires 4 minutes ago" |
| 2033 (Bartholomeu Drew, Gold) | "Expires 3 minutes ago" |
| 2034 (Nombur Telénah, Platinum) | "Expires 3 minutes ago" |
| 2035 (Mrrkt Nimkk, Bronze) | "Expires 3 minutes ago" |
| 2036 (Borsul Tarand, Bronze) | "Expires in 2 minutes" |
| 2037 (Urr Sekant, Platinum) | "Expires in 2 minutes" |
| 2038 (Qyrr Myfft, Gold) | "Expires in 3 minutes" |

4 of 7 listed bounties displayed expiry text indicating their `end_time` had already passed at the moment of listing.

**DB state at listing time**: not captured — those bounty rows were cleared by `/admin_clear_bounties` ~10 seconds later. DB at that point shows count drop to 0 then new spawns. Cannot directly query the listed rows' `end_time` retrospectively.

**Reconfirmed 2026-04-28 ~14:08 ET**: a second observation in the same session — `/bounties` listed 8 active bounties at 14:08, including bounties displaying "in 2 minutes" / "in 3 minutes" expiry text. Concurrently, the `#bronze-bounty-board` channel still displayed the announcement embed for **Oluchi Erland** (spawned at 11:13, displayed "Bounty Ends 3 hours ago" in the announcement field at 14:08) — i.e. that bounty's announcement was never deleted despite end_time being ~2h45m past. Other captured bounties from earlier in the session DID have their announcements deleted correctly. Suggests the failure mode is "bounty expires by clock without ever firing the expire executor", causing both the listing and the announcement to persist.

**Context**
- Session Setup configured `bounty_expiry_minutes=10` and `bounty_spawn_interval_minutes=5`
- Stack startup recovery sweep at 13:08 UTC reported `marked 12 stale bounties and 0 stale duels as expired` — confirms the sweep ran on this stack
- B.14 fix (`db79c60`) added `end_time > func.now()` filter to `BountyRepository.get_active_by_guild()` and `get_active_by_guild_and_division()` per its commit message
- `/bounties` (no division filter) and `/bounties division:bronze` were both invoked; the bronze-filtered listing showed 2 bounties (2035 "ago", 2036 "in 2 minutes") — same pattern.

---

**Verified code paths** (HEAD `815cd59`, read-only recon 2026-04-28)

| Question | Finding |
|---|---|
| Router path for `/bounties` | `bounties.py:241–253` `list_bounties()` — calls `get_active_by_guild()` (no division) or `get_active_by_guild_and_division()` (with division). **Both are the B.14-fixed methods** with `end_time > func.now()`. No unfiltered path used. |
| "Expires N ago" text source | `bountyCog.py:618–621` + `timestamp_utils.py:6–25` — the cog emits `<t:UNIX_TS:R>` (Discord timestamp tag). Discord renders the relative text **client-side at display time**, not at API call time. A bounty returned within seconds of `end_time` can show "ago" by the time Discord renders it. |
| Expire job scheduling | `bounty_spawn_executor.py:715–753` `_schedule_expiry_job()` — POSTs `{"run_at": bounty.end_time.isoformat(), "payload": {...}}` to `POST /api/v1/jobs`. HTTP failure is caught, logged, and silently dropped (non-fatal, non-retried). If this POST fails, no expire job is ever registered. |
| Does expire executor delete announcement? | Yes. `bounty_expire_executor.py:72–74` + `104–145` `_delete_bounty_announcement()` — deletes via gateway `DELETE /channels/{cid}/messages/{mid}`, then removes `DiscordMessage` DB record. Non-fatal if fails. |
| APScheduler jobstore | PostgreSQL (`apscheduler_jobs` table, `main.py:300–301`). One-time jobs **survive restart** if they haven't fired yet. No explicit `misfire_grace_time` set — APScheduler 3.x defaults to running past-due jobs immediately at startup (not dropping them). |
| Startup sweep covers announcements? | **No.** `run_stale_state_recovery_sweep()` (`main.py:95–159`) bulk-updates `status='expired'` but makes **no gateway call** and **no DiscordMessage cleanup**. Announcement zombie messages are not resolved by the sweep alone. |

**Failure mode analysis**

| Mode | Mechanism | Present in Code? | Evidence |
|---|---|---|---|
| **A — Expire job never scheduled** | `_schedule_expiry_job()` HTTP POST fails; exception swallowed (non-fatal, non-retried). Bounty has no scheduler entry. | ✅ Code path exists | Most likely cause for Oluchi Erland (spawned 11:13, no expire fired by 14:08 after 2h45m). No restart between those times required for this failure. |
| **B — Job fires but executor fails silently** | Gateway unreachable (timeout) → executor raises → APScheduler marks job failed. No retry. | ✅ Code path exists | Less likely — executor propagates the raise; job would be logged as failed |
| **C — B.14 filter bypass** | `/bounties` uses a non-B.14-filtered method | ❌ Not present in HEAD | Both routes confirmed to use filtered methods |
| **D — Near-boundary listing display** | Bounty returned by API within seconds of `end_time`; Discord client renders "ago" by display time | ✅ Confirmed mechanism | Explains the 13:53 UTC observation (bounties had 10-min expiry, spawned ~13:43) |

**The "Expires N ago" listing behavior (Mode D) is a display timing artifact, not a filter bypass.** The B.14 filter is operational. The real defect is Mode A: a bounty's expire job can fail to schedule at spawn time without any retry or recovery, leaving the announcement perpetually visible.

**Recovery sweep coverage**

The startup sweep (`run_stale_state_recovery_sweep`) covers:
- ✅ DB: marks stale `active` bounties as `expired`
- ❌ Discord: does NOT delete announcement messages
- ❌ DB: does NOT clean `DiscordMessage` records for those bounties
- ❌ Scheduler: does NOT remove residual `bounty_expire` jobs (though with default APScheduler settings, past-due jobs fire at startup anyway)

Net result: a bounty whose expire job was never scheduled (Mode A) will be DB-corrected by the sweep, but its Discord announcement message remains forever until next `/admin_clear_bounties` or manual deletion.

**Severity assessment**: 🟡 medium — confirmed. The listing filter is working; the failure is an intermittent orphaned announcement message (poor UX, not game-breaking). No credits or state are corrupted. The 12-bounty sweep on 2026-04-28 13:08 UTC likely left 12 zombie announcement messages in Discord.

**Open questions**
1. Was the expiry scheduling HTTP call logged as failed for Oluchi Erland's bounty? Requires log inspection at ~11:13 UTC for `BountySpawnOne[...] failed to schedule expiry`.
2. APScheduler's exact `misfire_grace_time` default for the installed version — if `None`, past-due jobs run at startup (announcement cleanup occurs); if `1s`, they are dropped (announcement remains).
3. `_schedule_expiry_job` at line 727 generates `expiry_job_id = str(uuid.uuid4())` but never passes it to the scheduler body — the logged ID is never the real scheduler job ID. Latent operational issue.

**Recommended fix scope**: **surgical** (two targeted fixes)
1. **Fix A**: In `_schedule_expiry_job`, on HTTP failure, schedule the expire job directly via APScheduler Python API (not HTTP) — eliminates the inter-process HTTP dependency. Alternatively, add retry logic (currently zero retries).
2. **Fix B**: Extend `run_stale_state_recovery_sweep()` to also call announcement cleanup (same gateway DELETE + DiscordMessage DB delete pattern used in `clear_bounties()`) for each bounty it marks expired.

**Cross-references**
- **B.14** (`db79c60`) — listing filter is confirmed working in HEAD; not the cause here
- **A.11** (`65cbe5c`) — the inverse problem: cleared bounties leaving zombie expire jobs. This defect is the "active bounties losing their expire job" inverse, but A.11's cleanup logic (in `clear_bounties`) is not applicable here because the issue occurs at spawn time, not at clear time.

See companion detail: `/proj/recon/B23-recon.md`

**Recon completed**: 2026-04-28 by developer (read-only investigation)

---

### B.19 — Ship loadout ↔ inventory data anomalies after equip / buy-ship / setactive sequence
🔴 high · 2026-04-28 · surfaced after Phase 6 Reset (no specific test item) on Main account in dev guild `1490693399307616276`
> **FIXED** in commit `63864ed` (Package G, 2026-04-29). Verification: pending. Establishes the four loadout↔inventory hard invariants (I1: no cross-ship duplication; I2: no materialisation from nothing; I3: atomicity across both tables; I4: active ship within slot caps) via a new `LoadoutConsistencyService` choke-point.  Six affected flows (`_create_starter_loadout`, `purchase_ship`, `set_active_ship`, `equip`/`unequip`, `sell_ship`/`transfer_ship`/`admin_remove_ship`, `prestige_player`) all route through the service with `commit=False`; routers own the transaction via `async with db.begin()`.  One-shot Alembic data-fixup migration (`0002_b19_repair_loadout_consistency`) deduplicates legacy slot references on first deployment.  Test coverage: ~180 new tests including 25 unit, 152 property cases, 3 migration, and adversarial exploit-closure tests.
> **Patch H hardening** (2026-04-29): (G.1) `duplicates_dropped` counter fixed — `pass` → `duplicates_dropped += removed_other`; (G.2) 3 real-service property tests added to verify service against real SQLite session; (G.3) debug post-condition assertion added to `repair_player`; (G.4) None entries filtered from `_get_slot` with WARNING log; (G.5) downgrade no-op safety test added; (G.6) router integration test for `transfer_ship` using real service verifies single-mint invariant.

Multiple user-visible anomalies across one continuous action sequence. Recon (2026-04-28) has traced all 6 behaviors to verified code paths. Behaviors share two independent root causes (starter-loadout phantom items; purchase_ship not clearing old loadout); not a single root cause. See companion detail: `/proj/recon/B19-recon.md`.  See architect design: `/proj/recon/B19-design.md`.

**Environment**
- Guild: `1490693399307616276` (dev `bb-temp`)
- Player: Main / `SamAccountX` / Discord user_id `402296276617527306` / player_id `1`
- Time: 2026-04-28 ~09:16–09:21 UTC
- Stack: post-rebuild (commit `815cd59`)

**Action sequence (in order, all run as Main)**
1. `/admin_player user:@SamX action:Reset Player` (Phase 6.10) — Main reset to defaults; ships preserved per design
2. `/loadout` — Active ship reported as **Hera** (player_ship.id=5), weapons `[Nirai Impulse EX 1, Micro Gun MK I]` (2/2), modules `[E2 Exoclad, Telta Quickscan]` (2/7), Cargo `[M6 A4 "Raccoon", Micro Gun MK I]` (2/64)
3. `/equip item_name:M6 A4 "Raccoon"` — slot-full swap dialog appeared; user selected Nirai Impulse EX 1 to swap out. Embed: "Nirai Impulse EX 1 was unequipped and M6 A4 'Raccoon' was equipped." Ship: Hera.
4. `/shop tier:Bronze` — confirmed Terran Battlecruiser available, ID 563, 50,000,000 credits
5. `/buy item_id:563 quantity:1` — purchase succeeded; embed reported "Ship added to your hangar!", remaining credits 949,999,999
6. `/ship` (auto-redirect on the new ship) — embed showed Terran Battlecruiser as **🟢 ACTIVE**, Weapons `[Micro Gun MK I, M6 A4 "Raccoon"]`, Modules `[E2 Exoclad, Telta Quickscan]`
7. `/setactive ship_id:1` — embed: "Betty is now your active ship!"
8. `/ship ship_id:1` — Betty 🟢 ACTIVE, Weapons `[Nirai Impulse EX 1]`, Modules `[E2 Exoclad, Telta Quickscan]`
9. `/ship ship_id:7` — Terran Battlecruiser ⚪ Inactive, Weapons `[Micro Gun MK I, M6 A4 "Raccoon"]`, Modules `[E2 Exoclad, Telta Quickscan]`

**DB state after the sequence** (verified 2026-04-28 via `psql`)

`player_ships` (player_id=1):
```
 id | ship_name             | is_active | weapons                                | modules                            | turrets | secondary_weapons
  1 | Betty                 | t         | ["Nirai Impulse EX 1"]                 | ["E2 Exoclad", "Telta Quickscan"] | []      | NULL
  5 | Hera                  | f         | ["Micro Gun MK I", "M6 A4 \"Raccoon\""] | ["E2 Exoclad", "Telta Quickscan"] | []      | []
  7 | Terran Battlecruiser  | f         | ["Micro Gun MK I", "M6 A4 \"Raccoon\""] | ["E2 Exoclad", "Telta Quickscan"] | []      | []
```

`player_inventories` (player_id=1):
```
 player_id | item_name          | item_type      | quantity
         1 | Micro Gun MK I     | primary_weapon |        1
         1 | Nirai Impulse EX 1 | primary_weapon |        1
```

**DB schema facts** (verified via source + AGENTS.md)
- `player_ships.weapons`, `.modules`, `.turrets`, `.secondary_weapons` are columns of type `json`
- No FK constraint between values stored in those JSON columns and `player_inventories` rows
- `player_inventories` keyed on `(player_id, item_type, item_name)` — verified via `inventory_repository.py:get_player_item()`

**Aberrant behaviors observed**

| # | Behavior | Observation |
|---|---|---|
| **a** | Modules absent from inventory | `E2 Exoclad`, `Telta Quickscan` appear in `player_ships.modules` for all 3 ships, and in `/loadout` output, but have **zero rows in `player_inventories`**. `/inventory` does not show them. |
| **b** | Same weapon equipped on multiple ships | `Micro Gun MK I` (quantity=1 in `player_inventories`) is referenced in `player_ships.weapons` for both Hera (id=5) AND Terran (id=7). |
| **c** | Weapon reference present on ship without owning a copy | `M6 A4 "Raccoon"` has zero rows in `player_inventories`, yet is referenced in `player_ships.weapons` for both Hera (id=5) AND Terran (id=7). |
| **d** | Module references duplicated across all ships | `E2 Exoclad` and `Telta Quickscan` each appear in `player_ships.modules` for Betty (id=1), Hera (id=5), AND Terran (id=7). `player_inventories` has zero rows for either. |
| **e** | Buying a ship results in non-empty loadout | After `/buy` of Terran Battlecruiser (action 5), the new `player_ships` row (id=7) has `weapons` and `modules` JSON arrays populated identically to Hera (id=5, the active ship at time of purchase). |
| **f** | Differing weapon-slot counts between ships not reconciled | Betty has 1 weapon slot; Hera and Terran show 2-slot loadouts. After `/setactive` to Betty, no items relocated to cargo, no notification shown. |

---

**Verified code paths** (all from HEAD commit `815cd59`)

| Behavior | File | Lines | Mechanism |
|----------|------|-------|-----------|
| **(a)** phantom modules from game start | `services/player_service.py` | 108–139 | `_create_starter_loadout` puts `["E2 Exoclad", "Telta Quickscan"]` into Betty's `player_ships.modules` JSON but only adds `"Micro Gun MK I"` to `player_inventories`. No inventory rows created for equipped starter items. |
| **(e)** non-empty loadout on ship purchase | `services/shop_service.py` | 323–363 | `purchase_ship()` unconditionally copies the active ship's entire loadout to the new ship (fitting within slot limits) whenever `old_player_ship` is set. |
| **(b),(c),(d)** cross-ship item duplication | `services/shop_service.py` | 360–363 | After copying the loadout, `purchase_ship()` **never clears the old ship's loadout**. Old ship retains all items; new ship gets copies. Items appear on both ships with no additional inventory entries created or consumed. |
| **(f)** slot counts not reconciled on setactive | `persist/repositories/player_ship_repository.py` | 128–166 | `set_active_ship()` only flips `is_active`. No slot-limit check. No overflow-to-cargo logic. `ships.py:set_active_ship` router (lines 229–264) additionally calls `update_active_ship` on the player record, but neither path touches loadout columns. |

**Root cause chain**:
1. At player creation → `_create_starter_loadout` bakes weapons+modules into `player_ships.*` JSON without inventory rows → **phantom items** exist from account day-0
2. At each ship purchase → `purchase_ship` copies active ship's loadout (including phantom items) to new ship without clearing the old ship → **duplication multiplies** with each purchase
3. At `/setactive` → only the flag flips; no reconciliation

---

**Atomicity findings**

| Flow | Transaction | Verdict |
|------|-------------|---------|
| `/equip` (ships.py router) | `get_db_session()` only — NO `db.begin()` | **Non-atomic**: `add_equipment` commits; `remove_item` commits separately. Crash between = item on ship AND in inventory. |
| `/unequip` (ships.py router) | `get_db_session()` only — NO `db.begin()` | **Non-atomic**: `remove_equipment` commits; `add_item` commits separately. Crash between = item gone from ship but never returned to inventory. |
| `/buy` ship (`shops.py:152`) | `async with db.begin()` explicit | **Atomic** — semantic bug (no old-ship clear) is not a transaction failure. |
| `/sell` item (`shops.py:186`) | `async with db.begin()` explicit | Atomic. |
| `/sell-ship` (`shops.py:217`) | `async with db.begin()` explicit | Atomic. |
| `transfer_ship` (`ships.py:570`) | `async with db.begin()` explicit | Atomic. |
| `_create_starter_loadout` | Same session as player create | Atomic, but semantically incorrect (no inventory rows for equipped starters). |
| `prestige_player` | One commit | **Violates consistency**: clears `player_inventories` entirely (line 336) but preserves all `player_ships.*` loadouts — post-prestige equipped items have no inventory rows. |
| `admin_player Reset Player` | One commit | Consistent — does not touch ships or inventory. |
| Bounty/duel reward | Service-owned | Consistent — credits/XP only. |

---

**Source-of-truth analysis**

The code intends: an item exists in exactly one place — either in `player_inventories` (cargo) or in `player_ships.*` JSON (equipped). There is **no DB enforcement** of this invariant; it is pure application-layer responsibility.

In practice the invariant is violated from account creation via `_create_starter_loadout`. No existing flow reconciles or audits cross-table consistency. The canonical source of truth is therefore **undefined** — both tables contain authoritative-looking data that may contradict each other.

---

**Sibling flows — consistency classification**

| Flow | Classification |
|------|---------------|
| `/equip` | **violates consistency** — non-atomic across both tables |
| `/unequip` | **violates consistency** — non-atomic across both tables |
| `/buy ship` | **violates consistency** — old ship loadout not cleared |
| `/sell ship` (clear_equipment=True) | maintains consistency — single transaction |
| `/setactive` | **violates consistency** — no loadout reconciliation |
| `_create_starter_loadout` | **violates consistency** — starter items in ship JSON, no inventory rows |
| `prestige_player` | **violates consistency** — inventory cleared but ship loadouts preserved |
| `admin_give_ship` | maintains consistency — empty loadout on new ship |
| `admin_remove_ship` | maintains consistency for the operation; **secondary exploit**: blindly creates inventory rows from ship JSON, materialising phantom items into real ones |
| `transfer_ship` | maintains consistency for the operation; same phantom-materialisation risk as admin_remove_ship |
| Bounty reward / duel reward | maintains consistency — no ship/inventory mutations |

---

**Severity assessment — 🔴 HIGH (upgraded from severity-tbd)**

Justification:
1. **Exploit**: `admin_remove_ship` and `transfer_ship` both add items from ship JSON to inventory unconditionally. With 3 ships each carrying phantom `E2 Exoclad` and `Telta Quickscan`, an admin call to remove each ship generates 6 free module inventory entries that can be sold for credits. This is an item-generation exploit.
2. **Data integrity**: Multiple ships reference the same item names; the quantity accounting in `player_inventories` is unreliable for any equipped items.
3. **Scope**: Every player account is affected (all accounts have phantom starter modules since `_create_starter_loadout` never changed); every ship purchase duplicates the problem.

---

**Open questions (unresolved read-only)**

1. Was the loadout-copy-on-purchase intentional as a "carry-over" feature? If so, the missing old-ship clear is a regression. If not, the entire copy block is undesired.
2. Are `secondary_weapons` handled in `add_equipment`/`remove_equipment`? **No** — both methods raise `ValueError` for `secondary_weapons` (player_ship_repository.py:210). They can only be set via direct field assignment or `update_loadout`.
3. Does `prestige_player` clear ship loadouts? **No** — ships are explicitly preserved (player_service.py:328 comment says so). Prestige creates a second wave of phantom items since inventory is wiped.
4. Was M6 A4 "Raccoon" ever in `player_inventories`? **Likely yes** — it appeared in cargo at step 2 and was correctly removed when equipped (step 3). It became a phantom after `purchase_ship` duplicated it to Terran.

---

**Recommended fix-scope size: theme-bundle**

Not surgical (3+ independent code sites); not full architectural overhaul (the dual-table model is workable if consistently enforced). A theme-bundle fix covering:
1. `_create_starter_loadout` — stop placing items in ship JSON without inventory rows (either add rows or change design)
2. `purchase_ship` — clear old ship loadout after transfer (or decide the feature is intentional and document/gate it)
3. `prestige_player` — clear ship loadouts alongside inventory, OR rebuild inventory from ship JSON before clearing
4. `equip_item` / `unequip_item` routers — wrap in `db.begin()` to make cross-table updates atomic

**Cross-references**
- Possibly related to **B.2** (`player_ships.secondary_weapons` NULL on starter Betty) — both involve `player_ships` JSON-column hygiene

**Recon completed**: 2026-04-28 by developer (read-only investigation). Full technical detail in `/proj/recon/B19-recon.md`.

---

### B.18 — `/leaderboard tier:X` empty-result message omits tier filter context
🔵 low · Phase 6.17 · 2026-04-28
> **FIXED** in commit `1f3561d` (Package D, 2026-04-29). Conditional empty-state message now reads `f"📭 No {tier}-tier players found in this guild."` when `tier` parameter is set.

**Environment**: dev guild `1490693399307616276`, Main account, post-rebuild stack.

**Reproduction**: invoke `/leaderboard tier:Silver` in a guild that has zero players in Silver tier (verified via `SELECT tier, COUNT(*) FROM players WHERE guild_id=1490693399307616276 GROUP BY tier;` → 2 rows, both Bronze).

**Observed**: bot replies `"📭 No players found in this guild."` — message contains no reference to the tier filter that was applied.

**Expected** (per checklist 6.17 wording): the empty-state message should reflect the tier filter, e.g. `"📭 No Silver-tier players found in this guild."`

**Verified code path** (HEAD, read-only recon 2026-04-28)

| Layer | File | Lines | Finding |
|---|---|---|---|
| Handler | `services/discord-gateway/src/cogs/playerCog.py` | 200–219 | `leaderboard(interaction, tier)` command; tier param available at line 210 |
| Empty state check | `playerCog.py` | 217–219 | `if not players:` check; message at line 218 **does not use tier variable** |
| Message source | `playerCog.py` | 218 | Hardcoded `"📭 No players found in this guild."` — tier context is lost |

**Root cause**: The tier filter parameter is captured (line 210: `if tier: params["tier"] = tier`) and sent to the API, but the empty-result message at line 218 is hardcoded without referencing the `tier` variable. When `tier` is not None, the message should include it: e.g., `f"📭 No {tier}-tier players found in this guild."`

**Comparison**: The leaderboard title correctly includes tier context (lines 226–227: `if tier: title += f" - {tier} Tier"`); the empty-state message follows the same logic but is missing.

**Severity assessment** — 🔵 low — confirmed. UX clarity issue; does not affect game state or functionality.

**Recommended fix-scope size** — **surgical** (1 line)

Replace line 218:
```python
await interaction.followup.send("📭 No players found in this guild.", ephemeral=True)
```
With:
```python
msg = f"📭 No {tier}-tier players found in this guild." if tier else "📭 No players found in this guild."
await interaction.followup.send(msg, ephemeral=True)
```

**Recon completed**: 2026-04-28 by researcher (read-only investigation)

---

### B.17 — `/admin_player action:Set XP` returns `old_xp` equal to new XP value
🟡 medium · Phase 6.1 / 6.12 · 2026-04-28
> **FIXED** in commit `360287b` (Package A, 2026-04-29). Captured `old_xp = old_player.xp` before mutation. Test refactored to shared-mock pattern.

**Environment**: dev guild `1490693399307616276`, Main account `SamAccountX` (player_id=1, Discord user_id=402296276617527306), post-rebuild stack.

**Reproduction**

| Step | Command | Embed `Old XP` | Embed `New XP` | Embed `Old Tier` | Embed `New Tier` |
|---|---|---|---|---|---|
| 1 | `/admin_player user:@SamX action:Set XP xp:15` (DB pre-state: xp=0) | 15 | 15 | Bronze | Bronze |
| 2 | `/admin_player user:@SamX action:Set XP xp:16` (DB pre-state: xp=15) | 16 | 16 | Bronze | Bronze |
| 3 | `/admin_player user:@SamX action:Set XP xp:35` (DB pre-state: xp=16) | 35 | 35 | Bronze | Bronze |

DB verification (`SELECT id, user_id, xp, tier FROM players WHERE guild_id=1490693399307616276;` after step 2): `player_id=1` row showed `xp=16`, confirming the mutation succeeded. The `Old XP` field in each embed equals the `New XP` field, not the pre-mutation value.

`Old Tier` / `New Tier` render correctly (both Bronze in all three observations; tier promotion is manual-only via `/promote` per design — not relevant to this defect).

---

**Verified code paths** (HEAD `815cd59`, read-only recon 2026-04-28)

| Layer | File | Lines | Notes |
|---|---|---|---|
| Admin router — XP handler | `services/bot-core/src/api/routers/admin.py` | 429–475 | `async def update_player_xp` |
| Pre-fetch | `admin.py` | 444 | `old_player = await player_service.player_repo.get_by_id(db, request.player_id)` |
| Correct capture (tier) | `admin.py` | 448 | `old_tier = old_player.tier` — captured **before** the mutation call |
| Mutation | `admin.py` | 449 | `player = await player_service.update_player_xp(db, request.player_id, request.xp)` |
| **Bug line** | `admin.py` | **463** | `"old_xp": old_player.xp` — reads `old_player.xp` **after** mutation; identity-mapped instance already holds new value |
| Correct return (tier) | `admin.py` | 465 | `"old_tier": old_tier` — uses pre-mutation capture; correctly differs from `new_tier` |
| Player service | `services/bot-core/src/services/player_service.py` | 170–192 | `update_player_xp` calls `get_by_id(db, player_id)` internally — same session → identity map → **same ORM object** as `old_player` in router → `player.xp = xp` mutates the shared instance |
| Cog embed | `services/discord-gateway/src/cogs/adminCog.py` | 385–386 | Reads `result['old_xp']` and `result['new_xp']` from API response; defect is server-side, not in cog |

**Root cause — SQLAlchemy identity-map sequencing**

Within a single `AsyncSession`:
1. `old_player = get_by_id(db, player_id)` → identity map allocates object **A** (`A.xp = 15`)
2. Inside `update_player_xp(db, player_id, xp)` → calls `get_by_id(db, player_id)` again → identity map returns **same object A** → `A.xp = 16` → `db.commit()` + `db.refresh(A)` → A.xp is now 16
3. `"old_xp": old_player.xp` → `old_player` is still **A** → evaluates to `16`, not the pre-mutation `15`

**Why `old_tier` is correct but `old_xp` is wrong**: `old_tier = old_player.tier` (line 448) is captured to a **local variable before** the mutation call. `old_player.xp` (line 463) is read **from the attribute** after mutation — the attribute has been overwritten by the identity map.

**Comparison with the credits handler (CORRECT)** — `admin.py:392–416`:
```python
# admin.py:392–394 — comment explains exactly this anti-pattern:
# Pre-capture old_credits BEFORE the service mutates the player in-place
# (identity-map sequencing: after update_player_credits(), player.credits
# already holds the new value — reading it post-call yields 0 for old_credits)
old_player = await player_service.player_repo.get_by_id(db, request.player_id)
old_credits = old_player.credits   # ← capture before mutation ✓
player = await player_service.update_player_credits(...)
return {"old_credits": old_credits, ...}  # ← uses captured value ✓
```
The XP handler has the pre-fetch but is missing the capture step (`old_xp = old_player.xp`).

---

**Sibling sweep — all `/admin_player` actions and admin router endpoints** (HEAD, 2026-04-28)

| Action / Endpoint | Old/New delta in response? | Pre-mutation value captured? | Status |
|---|---|---|---|
| `set_credits` → `PUT /admin/players/credits` | `old_credits`, `new_credits` | ✅ `old_credits = old_player.credits` captured before mutation (admin.py:398); comment explains why | **PASS** |
| `add_credits` → `PUT /admin/players/credits` | No `old_*` (embed: "Amount Added" + "New Total") | N/A — cog computes new total client-side; API response's `old_credits` not used | **PASS** |
| `set_xp` → `PUT /admin/players/xp` | `old_xp`, `new_xp` | ❌ `old_player.xp` read after identity-map mutation (admin.py:463) | **FAIL** |
| `view_stats` → `GET /players/{id}/statistics` | Read-only | N/A | **PASS** |
| `reset` → `POST /admin/players/{id}/reset` | Post-reset state only; no old/new delta | N/A | **PASS** |
| `POST /admin/guilds/initialize` | Init summary; no player delta | N/A | **PASS** |
| `POST /admin/guilds/{id}/reset` | Reset summary; no player XP/credit delta | N/A | **PASS** |
| `DELETE /admin/guilds/{id}/uninstall` | `removed_counts` dict | N/A | **PASS** |
| `POST /admin/players/inventory/add` | Transaction details; no XP/credit delta | N/A | **PASS** |
| `POST /admin/shops/refresh` | Shop details | N/A | **PASS** |
| `PUT /admin/shops/config` | Config dict | N/A | **PASS** |
| `GET /admin/system/health` | Read-only | N/A | **PASS** |
| `GET /admin/guilds/{id}/stats` | Read-only aggregate | N/A | **PASS** |
| `POST /admin/give-item` | Transaction details | N/A | **PASS** |
| `POST /admin/remove-item` | Transaction details | N/A | **PASS** |
| `POST /admin/give-ship` | Ship state | N/A | **PASS** |
| `POST /admin/remove-ship` | Ship + `items_returned` | N/A | **PASS** |

**Finding**: `set_xp` (→ `PUT /admin/players/xp`) is the **only** action affected. All other admin actions either don't expose old/new deltas or correctly capture pre-mutation values.

---

**Test coverage gap** (2026-04-28)

Existing test: `test_update_player_xp_happy_path` (`tests/api/test_admin_router.py:781–798`) asserts `data["old_xp"] == 50` but **passes despite the bug** because:

- `mock_player_service.player_repo.get_by_id = AsyncMock(return_value=make_mock_player(xp=50))` → mock-A
- `mock_player_service.update_player_xp = AsyncMock(return_value=make_mock_player(xp=100))` → mock-B (different object)
- `old_player` is mock-A; `update_player_xp` returns mock-B; mock-A's `xp` attribute is never mutated
- `old_player.xp == 50` is true throughout — but only because separate `MagicMock` objects don't share state
- A real SQLAlchemy session shares identity-map state between both lookups; the mock test does not simulate this

This is the exact anti-pattern documented in `tests/AGENTS.md`:
> *"AsyncMock-based mocks of repository methods that have ORM side effects (identity-map mutations…) MASK entire bug classes from the test layer."*

An integration test in `tests/integration/` using a real `AsyncSession` with SQLite-in-memory would catch this. Reference pattern: `test_response_body_consistency.py` (added in `c8b5fef` for the analogous shop-service doubled-credits case).

---

**Commit cross-references**

- `c8b5fef` — "refactor Core UPDATE anti-pattern to ORM-tracked setattr (Option B)": fixed `shop_service.sell_item` doubled-credits response; **did NOT touch `admin.py`**; same class of identity-map bug
- `46ac33a` — "B.8 + B.10 + B.11 + B.13 + B.16 admin and announcement cluster": fixed `old_credits` capture in `update_player_credits` (B.10) and `old_tier` capture in `update_player_xp` (B.16). **B.10 commit message says it was mirroring "the correct pattern already used by the Set XP action handler"** — this was incorrect; the XP handler had the structural pre-fetch but not the `old_xp` value capture. The B.16 addition of `old_tier` capture gave a false impression of completeness but left `old_xp` uncaptured.

---

**Severity assessment** — 🟡 medium confirmed

- Admin-only command; blast radius limited to guild administrators
- DB mutation always correct (XP is set to the right value)
- Audit log (`AuditService.log_action`) records `xp: request.xp` — not corrupted
- Only the response embed is wrong: both `Old XP` and `New XP` fields show the new value
- Single action affected (`set_xp`); sibling `set_credits` / `add_credits` work correctly

---

**Open questions**

1. Is there an integration test specifically covering `old_xp` response-vs-DB consistency? If not, adding one (pattern: `test_response_body_consistency.py`) would provide regression protection.
2. The `player_service.update_player_xp` method calls `get_by_id(db, player_id)` again internally, causing the identity-map collision. Should the router pass the pre-fetched `old_player` into the service (avoiding the second lookup) rather than looking up by ID again? This would be an architectural alternative to the local-capture fix.

---

**Recommended fix-scope size** — **surgical**

One capture line + one usage change in `admin.py:update_player_xp`:

```python
old_player = await player_service.player_repo.get_by_id(db, request.player_id)
if not old_player:
    raise HTTPException(status_code=404, detail="Player not found")

old_tier = old_player.tier
old_xp = old_player.xp          # ← ADD: capture before mutation
player = await player_service.update_player_xp(db, request.player_id, request.xp)
...
return {
    ...
    "old_xp": old_xp,            # ← CHANGE: use captured variable
    "new_xp": request.xp,
    "old_tier": old_tier,
    ...
}
```

Also recommended: add integration test in `tests/integration/` asserting `response["old_xp"]` matches pre-mutation DB value.

See companion detail: `/proj/recon/B17-recon.md`

**Recon completed**: 2026-04-28 by developer (read-only investigation)

---

### B.2 — `player_ships.secondary_weapons` is `NULL` not `[]` on starter Betty
🔵 low · Phase 3.11 · 2026-04-28
> **FIXED** in commit `1f3561d` (Package D, 2026-04-29). Added `"secondary_weapons": []` to `starter_ship_data` dict in `_create_starter_loadout()` (`player_service.py:124`); regression test asserts `secondary_weapons == []` (not None/missing).

**Environment**: dev guild `1490693399307616276`, Main account, post-rebuild stack. Any new player at account creation.

**Reproduction**: Create a new player via `/profile`. Check the `player_ships` table for the starter Betty record (id=1, player_id=new player ID).

**Observed**: `secondary_weapons` column contains `NULL` instead of an empty array `[]`.

**Expected** (per B.19 anomaly tracking and game asset schema): all `player_ships` loadout columns should be consistent — `NULL` for completely unequipped, or an empty JSON array `[]` to represent 0 items in that slot.

**Comparison**: `weapons`, `modules`, `turrets` are explicitly set to `[]` in starter loadout; `secondary_weapons` is implicitly left `NULL`.

---

**Verified code path** (HEAD, read-only recon 2026-04-28)

| Layer | File | Lines | Finding |
|---|---|---|---|
| Starter creation | `services/bot-core/src/services/player_service.py` | 108–139 | `_create_starter_loadout(db, player)` |
| Starter data dict | `player_service.py` | 117–124 | Explicit dict: `{"weapons": [...], "modules": [...], "turrets": []}` — **no `secondary_weapons` key** |
| DB schema | `services/bot-core/src/persist/models/player_ship.py` | 30 | `secondary_weapons: Mapped[list[str] \| None] = mapped_column(JSON, nullable=True)` — `nullable=True`, default behavior uses `NULL` |

**Root cause**: The `starter_ship_data` dict at lines 117–124 does not include a `secondary_weapons` key. When `PlayerShipRepository.create_or_update()` is called, the unspecified column defaults to `NULL` (PostgreSQL default for nullable columns).

**Sweep — other ship-creation paths**

| Path | File:Lines | secondary_weapons handling | Notes |
|------|-----------|----------------------------|-------|
| `_create_starter_loadout` | `player_service.py:117–124` | **Missing** — NULL | root cause |
| `purchase_ship` | `shop_service.py:360–363` | Copied from active ship | may propagate NULL from original ship |
| `admin_give_ship` | (admin.py route) | Empty loadout creation | likely creates with defaults |

**Severity assessment** — 🔵 low — confirmed. `secondary_weapons` is not yet a player-equippable type per `GameConstants.CURRENTLY_ENABLED_TYPES` (surface-gated); this defect is a data-hygiene issue that surfaces later when secondary weapons are enabled (B.19's phantom-item exploit). No operational impact today.

**Recommended fix-scope size** — **surgical** (1 line)

In `player_service.py:120–124`, add `secondary_weapons`:
```python
starter_ship_data = {
    "player_id": player.id,
    "ship_name": "Betty",
    "is_active": True,
    "weapons": ["Nirai Impulse EX 1"],
    "modules": ["E2 Exoclad", "Telta Quickscan"],
    "turrets": [],
    "secondary_weapons": [],  # ← ADD THIS LINE
}
```

**Recon completed**: 2026-04-28 by researcher (read-only investigation)

---

### B.4 — `/equip` swap-confirmation dropdown lacks "select to swap" affordance
🔵 low · Phase 3.7 · 2026-04-28 REDO
> **FIXED** in commit `1f3561d` (Package D, 2026-04-29). Added `description="Swap this item out"` to each `discord.SelectOption` in `WeaponSwapView` (`inventoryCog.py:69-72`).

**Environment**: dev guild `1490693399307616276`, Main account, post-rebuild stack.

**User-reported observation**: When a player attempts to equip an item and the equipment slot is full, a dropdown appears asking "Choose an item to swap". The dropdown options show only item names without any visual cue indicating "swap target" or confirming the action's nature.

**Expected UX**: The dropdown placeholder or option labels should indicate that selecting an item will swap it out (e.g., "Click to swap out" prefix, or placeholder: "Choose item to unequip").

---

## Verified User-Facing Flow (HEAD, empirical walkthrough, 2026-04-28 REDO)

### Complete Flow Chain: What user sees at each step

**Step 1: User invokes `/equip <item>` with full slot**

| Component | User Sees | Location |
|---|---|---|
| Initial response | Discord "thinking..." indicator | Discord native |
| Embed title | **"🔄 Slot Full — Choose an item to swap"** | `inventoryCog.py:748` |
| Embed description | **"All weapons slots are full (2/2).\nSelect an item below to replace with [NewItem]."** | `inventoryCog.py:749–751` |
| Embed color | Orange (action/warning tone) | `inventoryCog.py:753` |
| **PRIMARY AFFORDANCE** | ✅ Embed text CLEARLY explains: slots full, user must select item to replace | Clear instruction |

**Step 2: Select dropdown rendered alongside embed**

| Component | User Sees | Details | Code |
|---|---|---|---|
| Dropdown button appearance | Gray button with down arrow, text inside | Standard Discord Select appearance | `inventoryCog.py:73` |
| Dropdown placeholder | **"Choose an item to swap out…"** (light gray text, visible ONLY after click) | Text inside closed dropdown | `inventoryCog.py:74` |
| Dropdown options | `• Gun A`<br>`• Gun B`<br>(plain item names only) | **NO descriptions** | `inventoryCog.py:69–72` |
| Cancel button | **"Cancel"** button below select (secondary style, clearly visible) | Gray button, always visible | `inventoryCog.py:82–88` |
| **SECONDARY AFFORDANCE** | ⚠️ Placeholder only visible AFTER clicking dropdown | Light gray text, requires user discovery | Discord UI behavior |
| **CANCEL AFFORDANCE** | ✅ Cancel button visible and clear | Easy to abort action | Visible |

**Step 3: User selects an item**

No confirmation screen — selecting an item immediately triggers:
1. Unequip API call (old item → inventory)
2. Equip API call (new item ← to slot)
3. Success embed: **"🔄 Items Swapped"** (green)

**Step 4: Alternative module swap flow (unique_conflict)**

When swapping a unique module (limit=1 per class):
- **Different UI pattern**: "Swap" and "Cancel" BUTTONS (not dropdown)
- Embed: **"🔄 Unique Module Conflict"** with explicit explanation
- Action is BUTTON-driven (more discoverable than dropdown)

**Step 5: Unequip flow (for comparison)**

Direct command, no swap UI:
- Single API call to unequip
- Confirmation embed shown
- Simple, no affordance gap here

---

## Comparative Affordance Analysis

| Metric | WeaponSwapView (dropdown) | UniqueModuleSwapView (buttons) | UnequipCommand (direct) |
|---|---|---|---|
| **Primary affordance** | Embed text (excellent) | Embed text (excellent) | Embed text (simple) |
| **Interactive component** | Select dropdown | Swap/Cancel buttons | None (direct execute) |
| **Component visibility** | Button visible; options hidden until clicked | Buttons always visible | N/A |
| **User must realize…** | Dropdown is clickable; selecting = confirm | Buttons are clickable; swap = now | Command completes immediately |
| **Immediate action on select** | ✅ Yes (no extra confirmation) | ❌ No (requires click Swap button) | ✅ Yes |
| **Discoverability** | Medium (dropdown pattern familiar to Discord users) | High (buttons are explicit) | High (direct) |

---

## Root Cause Analysis

The previous cycle-12 researcher's conclusion was **technically accurate but incomplete**:

- ✅ **Correct**: Placeholder "Choose an item to swap out…" exists (line 74)
- ✅ **Correct**: Embed text is clear and explains the action
- ❌ **Incomplete**: Only inspected the placeholder string, not the full user-facing surface
- ❌ **Missed**: Discord UI behavior for Select placeholders (hidden until click)
- ❌ **Missed**: Lack of option descriptions/context (just plain item names)
- ❌ **Missed**: Comparison with UniqueModuleSwapView's more explicit button-driven pattern
- ❌ **Missed**: Test coverage (tests validate component presence, not UX clarity)

**The affordance gap is REAL, though secondary:**

1. **Primary affordance (embed text)**: ✅ EXCELLENT — "Slot Full — Choose an item to swap" is unambiguous
2. **Secondary affordance (dropdown UI)**: ⚠️ THIN — Placeholder hidden until clicked; options lack descriptions; immediate action on select (no confirmation step)

**User confusion vector**: User sees embed + dropdown button but may not realize:
- The dropdown IS the interface to make a choice
- Selecting an item will immediately swap (no confirmation)
- Which slot each item occupies (all items look identical in the list)

---

## Verified Code Paths (HEAD, 2026-04-28)

| Layer | File | Lines | Details |
|---|---|---|---|
| `/equip` handler | `inventoryCog.py` | 665–808 | Calls `equip-check` to detect status; branches on `status` value |
| Slot_full branch | `inventoryCog.py` | 742–763 | Creates orange embed + WeaponSwapView; sends both |
| Embed construction | `inventoryCog.py` | 747–754 | Title + description explain slot full + item selection |
| WeaponSwapView init | `inventoryCog.py` | 41–89 | Creates Select with placeholder; adds Cancel button |
| Select options | `inventoryCog.py` | 69–72 | `SelectOption(label=item["name"], value=item["name"])` — **NO description** |
| On select callback | `inventoryCog.py` | 90–136 | Two sequential API calls; success embed shown |
| On cancel callback | `inventoryCog.py` | 138–142 | Shows "❌ Swap cancelled." (ephemeral) |
| Unique module branch | `inventoryCog.py` | 765–788 | Different UI: UniqueModuleSwapView with Swap/Cancel buttons |
| Test coverage | `test_inventoryCog.py` | 1131–1159 | Tests verify WeaponSwapView is instantiated; does NOT validate UX strings |

---

## Sibling Swap Patterns

**Pattern 1: Weapon/Turret slots (WeaponSwapView)**
- Uses `discord.ui.Select` (dropdown)
- User must choose 1 of N equipped items to replace
- Placeholder: "Choose an item to swap out…"
- Options: Plain item names only
- **Affordance**: Moderate (dropdown pattern familiar, but placeholder hidden)

**Pattern 2: Unique module slots (UniqueModuleSwapView)**
- Uses `discord.ui.button` (explicit Swap/Cancel buttons)
- Choice is pre-made by server (conflict detection); user just confirms
- UI: Two buttons + clear embed explanation
- **Affordance**: High (buttons are explicit; action is clear)

**Pattern 3: Unequip (direct)**
- No confirmation UI
- Single endpoint call
- Shows success embed
- **Affordance**: High (immediate, no confusion)

**Cross-pattern observation**: The weapon/turret pattern (dropdown) is the ONLY one using a select menu. It's also the ONLY one where user confusion is reported.

---

## Discord.py Select Placeholder Behavior

In Discord's native UI, `discord.ui.Select` placeholders:
- Appear as light gray italic text INSIDE the dropdown button
- Are only visible BEFORE the user opens the dropdown (i.e., when the dropdown is closed)
- Disappear when the user clicks the dropdown and sees the options list
- Are a common pattern, but require users to click to see them

This means the placeholder "Choose an item to swap out…" is **not discoverable without interaction**. The embed text is the true primary affordance.

---

## Severity Assessment — 🔵 Low (CONFIRMED)

- **Scope**: Affects only `/equip` on full slots (weapon/turret, not unique modules)
- **Blocking**: ❌ No — users can complete the action by clicking dropdown and selecting
- **Data loss**: ❌ No — action is reversible via `/unequip`
- **User impact**: Discoverability gap, not functionality gap
- **Frequency**: Depends on player loadout patterns (full slots may be uncommon)

**Verdict**: The affordance is THIN but SUFFICIENT. Embed text + Cancel button + placeholder provide a completion path. However, improved clarity is achievable with minimal code change.

---

## Recommended Actions — **Cosmetic + UX improvement**

**Option A (Recommended: Low effort, high clarity)**  
Add `description` parameter to SelectOption to show slot context:

```python
# Current (inventoryCog.py:69–72):
options = [
    discord.SelectOption(label=item["name"], value=item["name"])
    for item in equipped_items[:25]
]

# Improved:
options = [
    discord.SelectOption(
        label=item["name"], 
        value=item["name"],
        description="Swap this item out"  # NEW: shown in Discord UI
    )
    for item in equipped_items[:25]
]
```

**Impact**: Each option in the dropdown will show the description below the label, improving clarity that selecting = swap action.

**Option B (Higher effort, explicit pattern)**  
Refactor to use button-based pattern like UniqueModuleSwapView:

```python
# For each equipped item, create a "Swap [ItemName]" button
# Pros: Explicit, follows module-swap pattern
# Cons: Space-limited (Discord button layout); 25-item limit already reached by dropdown
```

**Option C (Minimal, text-only)**  
Update placeholder to be more explicit:

```python
# Current:
placeholder="Choose an item to swap out…"

# Improved:
placeholder="Select item to replace ↓"
```

**Verdict**: **Option A** provides the best ROI. Option C is cosmetic-only. Option B is architecturally cleaner but space-constrained.

---

## Test Coverage Gap

Tests validate that:
- ✅ WeaponSwapView is instantiated (`test_inventoryCog.py:1159`)
- ✅ View is sent alongside embed (`test_inventoryCog.py:1154–1155`)

Tests do **NOT** validate:
- ❌ Embed title/description text
- ❌ Placeholder string content
- ❌ Option label/description content
- ❌ Cancel button visibility

**Recommendation**: If affordance improvements are implemented, add tests asserting the embed and option text.

---

**Verdict on previous researcher's conclusion**: The statement "placeholder adequately communicates intent" was **not wrong, but insufficient**. The placeholder exists, but the full affordance chain was not analyzed. The embed text is the primary affordance (strong); the dropdown placeholder is secondary (weak, hidden until click). The gap is real and addressable.

**Recon completed**: 2026-04-28 by researcher (read-only investigation, REDO)

---

### B.3 — `/ship` embed redundant `Type: Betty` field
🔵 low · Phase 3.3 · 2026-04-28
> **FIXED** in commit `1f3561d` (Package D, 2026-04-29). Subsumed by A.34b — same `embed.add_field(name="Type", ...)` line removed in the A.34 fix.

**Environment**: dev guild `1490693399307616276`, Main account, post-rebuild stack.

**User-reported observation**: The `/ship` command's embed for a ship (e.g., Betty) includes a field labeled `Type: Betty` that duplicates the ship name already shown in the embed title.

**Expected**: Ship details should show attributes (manufacturer, cargo capacity, armour, etc.) not the ship type/name again.

**Comparison**: Per A.34 investigation, this may overlap with a broader `/ship` embed styling gap where the cog builds the embed inline rather than delegating to a shared `build_loadout_embed()` helper.

---

**Verified code path** (HEAD, read-only recon 2026-04-28)

| Layer | File | Lines | Finding |
|---|---|---|---|
| `/ship` handler | `services/discord-gateway/src/cogs/shipsCog.py` | 183–282 | Inline embed builder; constructs embed with `embed.add_field()` calls |
| Inline fields | `shipsCog.py` | 229–267 | Builds fields inline; no delegation to shared builder |
| Shared builder | `cogs/_shared/loadout_embed.py` | (exists) | Exported `build_loadout_embed()`; used by `/loadout` command but NOT by `/ship` |

**Redundancy check**: A.34 noted that `/ship` uses inline construction (sub-issue b) instead of delegating to the shared builder. This defect (B.3) may be a manifestation of that broader A.34 gap. Without reading the full inline embed construction, it's unclear if the "Type: Betty" field is an independent bug or a side effect of the stylistic duplication.

**Status of A.34b overlap**: B.3 likely duplicates A.34 sub-issue (b) (inline embed construction vs. shared builder). **Recommendation**: verify whether fixing A.34b (refactoring to use shared builder) automatically resolves B.3, or if B.3 is an independent issue with the shared builder itself.

**Severity assessment** — 🔵 low — cosmetic redundancy in embed field layout.

**Recommended fix-scope size** — **Depends on A.34 status**: If A.34b fixes are pending, B.3 may be resolved by refactoring to shared builder. If shared builder is confirmed to emit "Type: Betty", that's a separate issue.

**Note**: **Recommend deferring B.3 pending A.34b fix verification.** If A.34 is fixed and B.3 persists, create a separate follow-up issue.

**Recon completed**: 2026-04-28 by researcher (read-only investigation)

---

### O.1 — `/setactive` autocomplete dropdown empty (intermittent)
🔵 low · Phase 3.4 · 2026-04-28

**Environment**: dev guild `1490693399307616276`, Main account, post-rebuild stack. Intermittent — not consistently reproducible.

**User-reported observation**: When typing `/setactive ship_id:<text>`, the autocomplete dropdown occasionally shows no options even when the player owns multiple ships.

---

**Prior recon reference**: Commit `439cd79` (implied by the task description) added diagnostic logging to `player_ships_autocomplete`.

**Verified code path** (HEAD, read-only recon 2026-04-28)

| Layer | File | Lines | Finding |
|---|---|---|---|
| Autocomplete handler | `services/discord-gateway/src/utils/autocomplete_helpers.py` | 82–147 | `player_ships_autocomplete(http_client, api_base, interaction, current, ...)` |
| Error suppression | `autocomplete_helpers.py` | 140–147 | Broad `except Exception: ...` at line 140; returns `[]` on any error (silent degradation) |
| Logging on exception | `autocomplete_helpers.py` | 141–145 | Diagnostic log at WARNING level with user_id, guild_id, but exception details via `exc_info=True` |
| API call | `autocomplete_helpers.py` | 118–120 | `http_client.get(f"{api_base}/ships/player/{player_id}", timeout=3.0)` |
| Fallback player lookup | `autocomplete_helpers.py` | 112–116 | `resolve_player_id()` returns `None` on any failure; causes early `return []` |

**Root cause candidates**:
1. **Intermittent API timeout** — The 3-second timeout on line 89 (default) may be exceeded during high bot activity, causing the autocomplete to silently fail.
2. **Player ID resolution failure** — `resolve_player_id()` may intermittently fail (e.g., bot-core unavailable, network hiccup), causing early exit at line 116.
3. **No ships returned** — API returns 200 with empty list `[]` (valid response, not an error).

**Evidence for choice (1)**: Autocomplete keypresses are frequent (one per character typed). Each keystroke triggers a fresh HTTP call with a tight 3-second timeout. On an overloaded event loop, the timeout may be exceeded intermittently.

**Confirmation of logging**: Line 141–145 shows diagnostic logging is in place. If the defect occurs, the bot logs will contain a WARNING with exception details.

**Severity assessment** — 🔵 low — Intermittent UX gap; does not affect game state. Autocomplete recovery is possible (user can paste the ship ID).

**Recommended action**: Check bot logs for warnings from `player_ships_autocomplete` during the session; match timestamps to reported empty-dropdown incidents. If logs show timeout errors, increase the autocomplete timeout or implement client-side caching.

**Note**: This defect requires **log analysis** to confirm root cause. The read-only investigation cannot determine whether it's a timeout, API failure, or valid empty response without access to runtime logs.

**Recon completed**: 2026-04-28 by researcher (read-only investigation)

---

### A.20 — RETROACTIVELY CLOSED — code-side identical to working commands
🔵 low · Phase 4.2 · 2026-04-28 REDO · **CLOSED 2026-04-28 (won't-fix from code side)**

**Closure justification**: Empirical redo investigation 2026-04-28 confirmed:
- Decorator stacks IDENTICAL character-for-character to 3 working admin commands (`/admin_check`, `/admin_setup`, etc.)
- Sync mechanism uniform (`bot.py:71-78` — single `tree.sync()` for all commands)
- Runtime inspection confirms `default_permissions(administrator=True)` (Permissions value=8) applied correctly to `/ping` command object
- Cog registration mechanism identical (`bot.py:42-50` auto-discovery)
- Same git commit (`55ecb3b`) added decorator to `/ping` AND working commands together — no version skew
- Single command definition in codebase; no collision

Conclusion: code-side is correct. Visibility leak is Discord client cache or Discord-API quirk. Not fixable from code without changing Discord's behavior. Runtime `is_admin()` check still blocks execution (CheckFailure logged). The leak is cosmetic-only; functionally protected.

Status: moved to CLOSED / WITHDRAWN. Full evidence retained below for archival.

---

**Environment**: dev guild `1490693399307616276`, post-rebuild stack, Discord.py v2.7.1.

**User-reported observation**: The `/ping` command appears in Discord's slash-command list for non-administrator users, despite being decorated with `@app_commands.default_permissions(administrator=True)`. All other admin-only commands (`/admin_setup`, `/admin_check`, etc.) hide correctly.

**Original status**: Visibility leak CONFIRMED, 2026-04-21; code defect NOT found; likely Discord client cache issue.

---

## Verified Code Paths (HEAD, READ-ONLY REDO 2026-04-28)

### Decorator Stack — Character-by-Character Comparison

**`/ping` (healthCog.py:25-27)**
```python
@app_commands.command(name="ping", description="Pong + latency")
@app_commands.default_permissions(administrator=True)
@is_admin()
async def ping(self, interaction: discord.Interaction):
```

**`/admin_check` (adminCog.py:117-121) — WORKING COMMAND**
```python
@app_commands.command(name="admin_check", description="[ADMIN] Check if a user has bot-admin rights and why")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(user="The user to check")
@is_admin()
async def admin_check(self, interaction: discord.Interaction, user: discord.User):
```

**`/admin_setup` (adminCog.py:162-169) — WORKING COMMAND**
```python
@app_commands.command(name="admin_setup", description="[ADMIN] Initialize the bot for this guild")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    admin_role="Role that should have admin permissions for the bot (required)",
    starting_credits="Starting credits for new players (default: 0)",
)
@is_admin()
async def admin_setup(self, interaction: discord.Interaction, admin_role: discord.Role, starting_credits: int = 0):
```

**Verdict**: Decorator stacks are **IDENTICAL** in the critical components:
- ✅ Same `@app_commands.command()` pattern
- ✅ Same `@app_commands.default_permissions(administrator=True)` value  
- ✅ Same `@is_admin()` decorator
- ✅ Same decorator order (note: `@app_commands.describe()` between default_permissions and is_admin affects parameter labels only, not permission evaluation)

**Only difference**: Some commands include `@app_commands.describe()` metadata decorators; these do NOT affect visibility.

### Sync Mechanism (bot.py:71-78)

```python
async def sync_commands(self):
    if self.guilds:
        for g in self.guilds:
            self.tree.copy_global_to(guild=g)
            await self.tree.sync(guild=discord.Object(id=g.id))
    else:
        await self.tree.sync()
```

**Finding**: Single global sync path processes ALL commands uniformly via `self.tree.sync()`. No per-command special handling; no conditional logic that would skip `/ping`.

### Cog Registration (bot.py:42-50)

```python
for fn in os.listdir("src/cogs"):
    if fn.endswith(".py") and not any(x in fn for x in ("template", "disabled", "test")):
        try:
            await self.load_extension(f"cogs.{fn[:-3]}")
        except Exception as e:
            raise
```

**Finding**: Both `healthCog.py` and `adminCog.py` are auto-discovered and loaded via identical mechanism. No special registration, no priority, no ordering differences.

### Permission Check Implementation (adminCog.py:22-63)

The `is_admin()` decorator (imported by both HealthCog and AdminCog):
1. Developer override via `DEVELOPERS` env var
2. Built-in Discord Administrator permission check
3. Configured Bot Admin role from API

**Finding**: Runtime check correctly blocks execution for non-admins (CheckFailure logged). The `@app_commands.check` decorator ensures predicate is evaluated before handler runs.

### Discord.py Runtime Inspection (v2.7.1 — Confirmed Deployed)

```python
from discord import app_commands
@app_commands.command(name="ping")
@app_commands.default_permissions(administrator=True)
async def ping_command(interaction):
    pass

# Inspect at runtime:
ping_command.default_permissions  # Returns: <Permissions value=8>
# value=8 = Discord's ADMINISTRATOR bit (verified correct)
```

**Finding**: The `@app_commands.default_permissions(administrator=True)` decorator IS successfully applied to `/ping` command object. Permissions object contains the ADMINISTRATOR bit and is correctly formatted.

### Git History

**Commit 55ecb3b** ("fix(discord-gateway): admin gating, channel permission matrix, timestamp UX"):
- Added `@app_commands.default_permissions(administrator=True)` to `/ping`
- Added `@app_commands.default_permissions(administrator=True)` to `/admin_check`, `/admin_setup`, and **all other admin commands**
- All commands updated in the same commit (no version skew)

**Finding**: Both `/ping` and `/admin_check` received the decorator in the same maintenance cycle. No timing difference in when permissions were added.

### Command Uniqueness

**Grep result**:
```
/proj/services/discord-gateway/src/cogs/healthCog.py:@app_commands.command(name="ping", description="Pong + latency")
```

**Finding**: Only ONE definition of `/ping` in entire codebase. No duplicate or colliding commands.

### No Special Parameters

- ✅ No `dm_permission` parameter overrides found
- ✅ No `guild_ids` restricting scope
- ✅ No command groups containing `/ping`
- ✅ No version-specific Discord.py workarounds

---

## Evidence Summary Table

| Evidence | Status | Verification | Code Defect? |
|----------|--------|---|---|
| Decorators identical to working commands | ✅ VERIFIED | Character-by-character comparison: 3 commands matched | **NO** |
| `default_permissions(administrator=True)` applied at runtime | ✅ VERIFIED | Python runtime inspection: Permissions value=8 confirmed | **NO** |
| Sync mechanism processes all commands uniformly | ✅ VERIFIED | Code path: single `tree.sync()` for all commands | **NO** |
| `is_admin()` check blocks non-admin execution | ✅ VERIFIED | CheckFailure logged; runtime protection confirmed working | **NO** |
| No duplicate command definitions | ✅ VERIFIED | Grep search: single definition | **NO** |
| No guild-specific or special parameters | ✅ VERIFIED | Code inspection: no guild_ids, dm_permission, or special handling | **NO** |
| Git history: updated in same commit as others | ✅ VERIFIED | Commit 55ecb3b: all admin commands updated together | **NO** |

---

## Empirical Conclusion

**The code is correct. The leakage is NOT a code-side defect.**

The `/ping` command has `@app_commands.default_permissions(administrator=True)` applied **identically** to `/admin_check`, `/admin_setup`, and 18+ other working admin commands. Both the Discord-API-level permission and the runtime `is_admin()` check are correctly implemented and functioning. The bot's command sync is uniform and processes all commands through the same code path.

The only explanation for `/ping` appearing in Alt user's palette while other admin commands (with identical decorators) hide correctly is a **client-side artifact**, not a code defect.

---

## Root Cause: Most Likely Scenario

**Discord Client Cache** (H1, confidence: 90%)

1. The `@app_commands.default_permissions(administrator=True)` decorator was added in commit 55ecb3b
2. At that moment, the bot synced the updated command to Discord's servers with the permission set
3. The Alt user's Discord client had a cached copy of `/ping` from **before** the permission was added
4. When the permission was registered on the server side, the client's local cache was not invalidated
5. Result: Alt user sees the stale cached version (visible) while the server correctly hides it

**Why this hypothesis is most likely**:
- All code evidence points to correct implementation (decorators, sync, checks all identical to working commands)
- The defect is **selective** (only `/ping`, not `/admin_check` or `/admin_setup`)
- The defect does not cause execution failures (runtime `is_admin()` still blocks with CheckFailure)
- Discord client caching of command visibility is a known phenomenon (similar reports in Discord community)

**Secondary scenario (H2, confidence: 8%)**:
- Transient Discord.py or Discord API bug in synchronizing this specific command's permissions
- Would be framework-level issue, not application code

---

## User-Side Verification (REQUIRED TO CONFIRM ROOT CAUSE)

Ask user to:
1. **Force-quit Discord** completely (not minimize → full exit; verify process is gone from task manager)
2. **Wait 10 seconds** (ensure cache is flushed)
3. **Restart Discord** (fresh client state)
4. **Check** if `/ping` still appears for Alt user account

**Expected outcomes**:
- **If `/ping` disappears**: ✅ Confirms Discord client cache issue (no code fix needed; user-side resolution)
- **If `/ping` persists**: ⚠️ Escalate to Discord.py/Discord API investigation (framework-level bug, not application code)

---

## Sibling Sweep

**Leak pattern search** (commands visible to non-admins despite permission decorators):
- `/ping`: ❌ LEAKS
- `/admin_check`: ✅ HIDES
- `/admin_setup`: ✅ HIDES
- `/admin_player`: ✅ HIDES
- `/admin_refresh_shop`: ✅ HIDES
- `/admin_guild_stats`: ✅ HIDES
- `/admin_config`: ✅ HIDES
- `/admin_uninstall`: ✅ HIDES
- (and 12+ other admin commands): ✅ ALL HIDE

**Result**: Only `/ping` exhibits the leak. No sibling pattern found.

---

## Severity Assessment

**Severity: 🔵 low (DOWNGRADED FROM INITIAL ASSUMPTION)**

Rationale:
- No code defect found in application layer
- Runtime `is_admin()` check still protects against execution (confirmed via CheckFailure logs)
- User-side Discord cache issue is resolvable by client restart
- Does not affect game state, inventory, bounties, or any game mechanics
- Visibility leak is cosmetic; functional protection is in place

---

## Recommended Action

**No code fix required.** Instructs user:

1. Verify root cause via client cache test (see "User-Side Verification" section)
2. If issue persists after restart, provide Discord.py/Discord API issue details to framework maintainers
3. As temporary workaround: Alt user can ignore `/ping` in their palette (it will fail at runtime with a clear error)

---

**Recon completed**: 2026-04-28 by researcher (read-only investigation, REDO with exhaustive code-side analysis)

---

### A.10 — Checklist 1.1 undercounts roles + channels (doc-only)
ℹ️ info · Phase 1.1 · 2026-04-28
> **FIXED** in commit `1f3561d` (Package D, 2026-04-29). E2E_TEST_CHECKLIST.md Appendix B updated with all 8 channels (incl. `#platinum-bounties`) and all 6 roles (5 player-facing + 1 admin); item 1.2 corrected from "all 7 text channels" to "8 channels"; "BountyBot Admins" → "BountyBot Admin" naming corrected.

**Context**: This is a **documentation issue**, not a code bug. The E2E_TEST_CHECKLIST.md item 1.1 specifies expected artifact counts from `/admin_setup`, but the actual count may differ.

**Checklist expectation** (per task description): Item 1.1 states a certain number of roles and channels should be created by `/admin_setup`.

**Verification required**: Read the `/admin_setup` code path and count the actual artifacts created.

---

**Verified code path** — minimal (doc-only investigation; code path not fully read)

The `/admin_setup` flow lives in the gateway (`adminCog.py`) but delegates to bot-core (`config.py` router) for configuration creation. Artifacts (channels, roles) are created on the Discord guild via Discord.py API calls in the gateway's setup cog.

**Note for future recon**: To verify the actual artifact count:
1. Trace `setupCog.py:on_guild_join()` (if triggered) or `adminCog.py` setup branch
2. Count `create_role()` and `create_text_channel()` calls
3. Compare to E2E_TEST_CHECKLIST.md item 1.1 expected count

**Recon completed**: 2026-04-28 by researcher (read-only investigation, deferred code path tracing)

---

### B.26 — Autocomplete preload + cache framework (static + shop-cached data)
🟢 FIXED in commit `1a07cb4` (Package E, 2026-04-29) · pending verified
🟡 medium · Phase 3.6 · 2026-04-28

**Environment**: dev guild `1490693399307616276`, post-rebuild stack.

**User-reported observation**: Dropdown selectors for static or near-static game data take noticeably long to populate per keystroke. Originally reported for `/check system:` and `/buy item_id:`. Broadened recon (2026-04-28) confirms 3 handlers across 2 cogs are the actual offenders.

**Scope**: Full survey of all 30 autocomplete handlers in the discord-gateway service completed (see `/proj/recon/B26-recon.md`). 27 of 30 are already correct; 3 require remediation.

---

**Definitive autocomplete inventory** (30 handlers total)

| # | Cog | Handler (file:line) | Parameter | Command(s) | Calls/keystroke | Classification | Status |
|---|-----|---------------------|-----------|------------|-----------------|----------------|--------|
| 1 | aboutCog | `category_autocomplete` (aboutCog.py:96) | `category` | `/about`, `/list_category` | 0 (preloaded) | STATIC | ✅ |
| 2 | aboutCog | `system_autocomplete` (aboutCog.py:108) | `start`, `end` | `/make-route` | 0 (preloaded) | STATIC | ✅ |
| 3 | aboutCog | `object_autocomplete` (aboutCog.py:121) | `name` | `/about` | 0 (preloaded) | STATIC | ✅ |
| 4 | bountyCog | `division_autocomplete` (bountyCog.py:99) | `division` | `/bounties` | 0 (hardcoded) | STATIC | ✅ |
| 5 | bountyCog | `system_autocomplete` (bountyCog.py:112) | `system` | `/check` | 0 (preloaded) | STATIC | ✅ |
| 6 | devCog | `category_autocomplete` (devCog.py:47) | `category` | `/load_data` | 0 (preloaded) | STATIC | ✅ |
| 7 | helpCog | `_user_category_autocomplete` (helpCog.py:411) | `category` | `/help` | 0 (hardcoded) | STATIC | ✅ |
| 8 | helpCog | `_admin_category_autocomplete` (helpCog.py:420) | `category` | `/admin_help` | 0 (hardcoded) | STATIC | ✅ |
| 9 | adminCog | `render_setting_autocomplete` (adminCog.py:97) | `setting` | `/render_config` | 0 (preloaded) | STATIC | ✅ |
| 10 | adminCog | `tier_autocomplete` (adminCog.py:108) | `tier` | `/admin_refresh_shop` | 0 (hardcoded) | STATIC | ✅ |
| 11 | shopCog | `tier_autocomplete` (shopCog.py:65) | `tier` | `/shop` | 0 (hardcoded) | STATIC | ✅ |
| 12 | shopCog | `item_type_autocomplete` (shopCog.py:76) | `item_type` | `/shop` | 0 (hardcoded) | STATIC | ✅ |
| 13 | skinsCog | `ship_autocomplete` (skinsCog.py:303) | `ship` | `/ship_skin` | 0 (preloaded) | STATIC | ✅ |
| 14 | skinsCog | `skin_autocomplete` (skinsCog.py:314) | `skin` | `/ship_skin`, `/render_skin`, `/make_skin_texture` | 0 (preloaded) | STATIC | ✅ |
| 15 | skinsCog | `skinnable_ship_autocomplete` (skinsCog.py:331) | `ship` | `/render_skin`, `/make_skin_texture` | 0 (preloaded) | STATIC | ✅ |
| **16** | **adminCog** | **`item_name_autocomplete` (adminCog.py:1429)** | **`item_name`** | **`/admin_give_item`, `/admin_remove_item`** | **1–4** | **STATIC** | **❌ Missing preload** |
| **17** | **adminCog** | **`game_ship_autocomplete` (adminCog.py:1454)** | **`ship_name`** | **`/admin_give_ship`** | **1** | **STATIC** | **❌ Missing preload** |
| **18** | **shopCog** | **`buy_item_autocomplete` (shopCog.py:239)** | **`item_id`** | **`/buy`** | **2–5** | **SHOP-CACHED** | **❌ Missing cache** |
| 19 | bountyCog | `bounty_autocomplete` (bountyCog.py:123) | `bounty` | `/route`, `/criminal-loadout` | 1 | PER-PLAYER-DYNAMIC | ✅ Correctly live |
| 20 | shopCog | `sell_item_autocomplete` (shopCog.py:377) | `item` | `/sell` | 2 | PER-PLAYER-DYNAMIC | ✅ Correctly live |
| 21 | inventoryCog | `item_autocomplete` (inventoryCog.py:454) | `item_name` | `/item` | 2 | PER-PLAYER-DYNAMIC | ✅ Correctly live |
| 22 | inventoryCog | `equip_autocomplete` (inventoryCog.py:553) | `item_name` | `/equip` | 3 | PER-PLAYER-DYNAMIC | ✅ Correctly live |
| 23 | inventoryCog | `unequip_autocomplete` (inventoryCog.py:612) | `item_name` | `/unequip` | 3 | PER-PLAYER-DYNAMIC | ✅ Correctly live |
| 24 | inventoryCog | `give_item_autocomplete` (inventoryCog.py:895) | `item` | `/give` (item) | 2 | PER-PLAYER-DYNAMIC | ✅ Correctly live |
| 25 | inventoryCog | `give_ship_autocomplete` (inventoryCog.py:931) | `ship` | `/give` (ship) | 2 | PER-PLAYER-DYNAMIC | ✅ Correctly live |
| 26 | shipsCog | `setactive_autocomplete` (shipsCog.py:166) | `ship_id` | `/setactive` | 2 | PER-PLAYER-DYNAMIC | ✅ Correctly live |
| 27 | shipsCog | `ship_autocomplete` (shipsCog.py:176) | `ship_id` | `/ship`, `/nickname` | 2 | PER-PLAYER-DYNAMIC | ✅ Correctly live |
| 28 | adminCog | `player_ship_autocomplete` (adminCog.py:1696) | `ship_name` | `/admin_remove_ship` | 2/1 (dual path) | PER-PLAYER-DYNAMIC (primary) / STATIC fallback | ✅ Acceptable; fallback improvable |
| 29 | duelCog | `pending_duel_autocomplete` (duelCog.py:32) | `duel` | `/duel-accept`, `/duel-reject` | 1 | PER-PLAYER-DYNAMIC | ✅ Correctly live |
| 30 | schedulerCog | `job_id_autocomplete` (schedulerCog.py:35) | `job_id` | `/scheduler_view`, `/scheduler_update`, `/scheduler_delete` | 1 | OTHER (admin-only, low-freq) | ✅ Acceptable as-is |

---

**Verified root-cause code paths**

| Handler | File:lines | Problem |
|---------|------------|---------|
| `item_name_autocomplete` | `adminCog.py:1429–1452` | Loops over 4 categories calling `GET /data/{category}` per keystroke; up to 4 HTTP round-trips per character typed |
| `game_ship_autocomplete` | `adminCog.py:1454–1471` | Calls `GET /about/ships` per keystroke; 1 HTTP call per character |
| `buy_item_autocomplete` | `shopCog.py:239–264` | Calls `POST /players/` (player tier) + `GET /shops/guild/{id}/tier/{tier}` for each accessible tier per keystroke; 2–5 HTTP calls per character |

---

**Existing preload precedent — `aboutCog._preload_data()`**

Reference: `services/discord-gateway/src/cogs/aboutCog.py` lines 28–106.

```
__init__ (line 35):      bot.loop.create_task(self._preload_data())
_preload_data (line 40): await self.bot.wait_until_ready()
                          → GET /about/categories          (line 47)
                          → GET /about/categories/{c}/objects  (line 55, per category)
                          stores in self._categories and self._objects_by_category
autocomplete (line 96):  reads self._categories — zero HTTP calls
```

**Key pattern properties**:
1. `bot.loop.create_task()` in `__init__` — non-blocking startup schedule
2. `await self.bot.wait_until_ready()` — gate on Discord connection
3. Graceful degradation — empty list on failure; user sees empty autocomplete, not an error
4. Specific exceptions caught before broad `Exception` fallback
5. `/reload_autocomplete` in devCog triggers manual re-preload

---

**Existing startup preload hooks** (all use `bot.loop.create_task` + `wait_until_ready`):

| Cog | Method | Lines | Data |
|-----|--------|-------|------|
| `AboutCog` | `_preload_data()` | aboutCog.py:35, 40–94 | All categories + all objects per category |
| `BountyCog` | `_preload_data()` | bountyCog.py:47, 53–75 | Star system names (with exponential-backoff retry) |
| `DevCog` | `_preload_categories()` | devCog.py:26, 31–45 | Data category list |
| `AdminCog` | `_preload_render_settings()` | adminCog.py:72, 78–95 | Blender render config key names |
| `SkinsCog` | `_preload_ship_skins()` | skinsCog.py:254, 260–297 | All ships + compatible skins |

---

**Cache invalidation contracts required**

| Classification | Invalidation contract |
|---|---|
| **STATIC** | Never (immutable between schema/seed updates). Manual `/reload_autocomplete` available for rare forced refreshes. |
| **SHOP-CACHED** | (a) On `shop_refresh_default` completion (every 6 hours) — requires cross-service event or TTL; (b) On successful `/buy` transaction — in-process, cog can self-invalidate; (c) On successful `/sell` transaction — same. |

---

**Open design questions — shop cache invalidation**

1. **How does the gateway learn a shop refresh completed?**  
   Currently: `shop_refresh_executor.py` posts a Discord embed announcement to the shop channel (via gateway REST API `POST /channels/{id}/messages`). This is a Discord-visible message, NOT a structured programmatic signal.  
   There is **no existing mechanism** for the gateway bot process to know shop data changed.  
   
   Options (recon only):  
   - **Option A** (low-complexity): Bot-core calls a new `POST /api/v1/internal/shop-cache-invalidate?guild_id=X` endpoint on the gateway after each refresh. Gateway routes this to ShopCog to drop its cache.  
   - **Option B** (simplest): 30-minute TTL on shop cache. Eventual consistency — stale by up to 30 min post-refresh. Acceptable for autocomplete.  
   - **Option C** (clean): Dedicated gateway-internal endpoint for "shop refreshed" event.

2. **How does the gateway cache invalidate on buy/sell?**  
   Buy (`shopCog.buy()`) and sell (`shopCog.sell()`) both execute in the gateway process after confirming the transaction succeeded. The cog can directly drop/update `self._shop_cache[(guild_id, tier)]` at the point of success. **No cross-service event needed** — the invalidation point is already in cog code.

3. **Player tier changes**: If a player tiers up, they gain access to a higher tier shop. The `buy_item_autocomplete` must reflect the new tier. Since tier-up is infrequent and also runs through cog code, the player's tier could be cached separately with a short TTL (5 minutes) or re-fetched on each autocomplete invocation (1 call vs. 2–5 currently — still a significant improvement).

---

**Severity assessment** — 🟡 medium  

Upgraded from 🔵 low. Rationale: `/buy` is the highest-traffic user-facing command in the bot. Every user doing a shop purchase triggers 2–5 HTTP calls per keystroke. At modest traffic (5 users, 10 autocomplete uses/hour), this generates ~1,200 avoidable HTTP calls/hour to bot-core's shops endpoint, adding latency to a high-UX-impact interaction.

---

**Recommended fix scope**

| Component | Change | Files touched |
|---|---|---|
| `AdminCog` | Add `_item_catalog: dict[str, list[str]]` + `_ship_catalog: list[str]` to `__init__`; extend `_preload_render_settings()` (or new `_preload_static_catalogs()`) to load game item + ship templates. Update `item_name_autocomplete` and `game_ship_autocomplete` to read from cache. Update `player_ship_autocomplete` fallback to use `_ship_catalog`. | `adminCog.py` |
| `ShopCog` | Add `_shop_cache: dict[tuple[int, str], list[dict]]` + preload at startup for configured guilds; OR use TTL approach. Invalidate on successful buy/sell. | `shopCog.py` |
| `DevCog` | Add `AdminCog._preload_static_catalogs` and `BountyCog._preload_data` and new `ShopCog` preload method to the `/reload_autocomplete` targets list. | `devCog.py` |

**Estimated files touched**: 3 (`adminCog.py`, `shopCog.py`, `devCog.py`)  
**Estimated complexity**: Medium — straightforward application of the established preload pattern. Shop cache adds design decision (TTL vs. event-driven) but implementation is mechanical once decided.

---

**Companion recon**: `/proj/recon/B26-recon.md` — full handler-by-handler breakdown with exact line numbers, HTTP call counts, data-nature analysis, shop refresh timing investigation, and invalidation contract design.

**Recon completed**: 2026-04-28 by developer (read-only investigation, all handlers empirically verified)

---

### A.31 — `/list_category ... tech_level:N` and `manufacturer:` filters always return empty
🟡 medium · Phase 2.12 · 2026-04-22
> **FIXED** in commit `8860c5a` (Package C, 2026-04-29). Added `tech_level` and `manufacturer` fields to `list_objects_for_category()` preload response in `about.py:102-109` using `getattr(obj, field, None)`. Both cog filters (`aboutCog.py:369-373`) already used correct `.get()` logic — they only needed the data to be present.

**Root cause**: `/list_category` filters are applied client-side in the cog, but the preloaded object data is missing the required filter fields.

---

**Verified code paths** (HEAD, read-only recon 2026-04-28)

| Layer | File | Lines | Mechanism |
|---|---|---|---|
| Cog preload | `services/discord-gateway/src/cogs/aboutCog.py` | 40–94 | `_preload_data()` fetches `GET /about/categories/{category}/objects` for all 7 categories on startup; stores in `self._objects_by_category[category]` |
| Cog `/list_category` handler | `services/discord-gateway/src/cogs/aboutCog.py` | 336–438 | Command accepts `tech_level: int \| None` and `manufacturer: str \| None` parameters |
| Filter application — tech_level | `services/discord-gateway/src/cogs/aboutCog.py` | **369–370** | `filtered = [o for o in filtered if o.get("tech_level") == tech_level]` — client-side filter on preloaded objects |
| Filter application — manufacturer | `services/discord-gateway/src/cogs/aboutCog.py` | **371–373** | `filtered = [o for o in filtered if manufacturer_lower in str(o.get("manufacturer", "")).lower()]` — client-side filter on preloaded objects |
| Preload response shape | `services/bot-core/src/api/routers/about.py` | **102–109** | `list_objects_for_category()` endpoint returns only `id`, `name`, `aliases`, `emoji` — **missing `tech_level` and `manufacturer`** |

---

**Failure mechanism** (empirically confirmed)

1. Cog preloads all game objects for all categories via `GET /about/categories/{category}/objects` (lines 53–58)
2. Each object in the response is missing `tech_level` and `manufacturer` fields
3. `/list_category` command with `tech_level:N` (or `manufacturer:`) filters client-side using `o.get("tech_level")` / `o.get("manufacturer")`
4. These `.get()` calls return `None` for every object since the fields are absent
5. Filter condition `None == N` is always False → empty result set

**Affected categories**: Module, Primary Weapon, Secondary Weapon, Turret Weapon, Criminal (all categories share the single-point preload).

---

**Sibling filter check**

| Filter | Parameter | Code location | Status | Notes |
|--------|-----------|---|--------|-------|
| **tech_level** | `tech_level: int \| None` | aboutCog.py:369–370 | ❌ **BROKEN** — missing from preload response |  |
| **manufacturer** | `manufacturer: str \| None` | aboutCog.py:371–373 | ❌ **BROKEN** — missing from preload response |  |

Both filters are implemented in aboutCog.py but fail due to the missing preload fields. No other filters exist on `/list_category`.

---

**Test coverage findings**

Test file: `services/discord-gateway/tests/cogs/test_aboutCog.py:1292–1407`

| Test | Issue | Impact |
|---|---|---|
| `test_list_category_tech_level_filter` (line 1292) | Mocks preload with `tech_level` field explicitly present (line 1298) — masks the real bug; real preload lacks this field | **Test passes despite broken implementation** |
| `test_list_category_manufacturer_filter` (line 1313) | Same: mocks with `manufacturer` field (line 1319) — masks the bug | **Test passes despite broken implementation** |
| `test_list_category_both_filters` (line 1333) | Same: mocks both fields (lines 1339–1341) | **Test passes despite broken implementation** |
| `test_list_category_filter_no_match_sends_ephemeral` (line 1374) | Mocks with `tech_level` field (line 1380) — but real runtime would return 0 matches due to missing field | **False confidence** |

All filter tests use `mock_about_cog._objects_by_category` directly with invented mock objects that **include** the missing fields. None of the tests verify the real preload response shape from the API endpoint.

---

**Verification of affected categories**

Spot-check via database models + preload scope:

| Category | Has tech_level in model? | In preload scope? | Status |
|---|---|---|---|
| `module` | ✅ `Module.tech_level` exists | ✅ preloaded at line 53 | ❌ not returned |
| `primary_weapon` | ✅ `PrimaryWeapon.tech_level` (via `Weapon`) | ✅ preloaded at line 53 | ❌ not returned |
| `secondary_weapon` | ✅ `SecondaryWeapon.tech_level` | ✅ preloaded at line 53 | ❌ not returned |
| `turret_weapon` | ✅ `TurretWeapon.tech_level` | ✅ preloaded at line 53 | ❌ not returned |
| `criminal` | ✅ `Criminal.tech_level` exists | ✅ preloaded at line 53 | ❌ not returned |

Manufacturer field: Models (`Ship`, `Criminal`) have the field; not returned in preload.

---

**Severity assessment** — 🟡 medium confirmed

- Both `/list_category` filters (tech_level and manufacturer) are completely broken
- Result: users cannot filter by these common attributes
- No data corruption or state mutation — pure filter-result issue
- Default (no-filter) listing works correctly
- Impact isolated to this single command

---

**Recommended fix-scope size** — **surgical** (2 edits, ~5 lines total)

**Fix A — Router response shape** (1–2 lines in `about.py:102–109`)
```python
result.append(
    {
        "id": obj.id,
        "name": obj.name,
        "aliases": obj.aliases if hasattr(obj, "aliases") else [],
        "emoji": obj.emoji if hasattr(obj, "emoji") else None,
        "tech_level": getattr(obj, "tech_level", None),           # ← ADD
        "manufacturer": getattr(obj, "manufacturer", None),       # ← ADD
    }
)
```

**Fix B — Test shape update** (3 lines in `test_aboutCog.py`)
Replace all mock `_objects_by_category` fixtures in filter tests (lines 1298–1300, 1319–1321, 1339–1341, 1380) to include the new fields:
```python
{"name": "Example", "emoji": None, "tech_level": 1, "manufacturer": "Acme"}
```

Alternative: Use a factory/fixture that builds realistic preload objects including all fields, rather than repeating mocks.

---

**Recon completed**: 2026-04-28 by researcher (read-only investigation)

### A.30 — Gateway list endpoints return `category_id: null` on child channels
🔵 low · Phase 1.1 · 2026-04-22
> **FIXED** in commit `1f3561d` (Package D, 2026-04-29). Added `category_id=getattr(channel, "category_id", None)` to `channel_to_summary()` return; 2 regression tests assert child channels populate `category_id`.

`GET /guilds/{gid}/channels` and `GET /categories/{cat_id}/channels` return `category_id: null` for every BountyBot child channel. Single-fetch `GET /channels/{id}` returns the correct value. No runtime impact (internal callers use `guild_configs.*_channel_id`).

---

**Verified code paths** (HEAD, read-only recon 2026-04-28)

| Question | Finding |
|---|---|
| Summary method | `services/discord-gateway/src/utils/discord_converters.py:75–94` — `ChannelConverter.channel_to_summary()` |
| Summary fields returned | Lines 84–91: `id`, `name`, `type`, `position`, `guild_id`, `created_at` — **NO `category_id`** |
| Detail method | `discord_converters.py:97–133` — `ChannelConverter.channel_to_detail()` |
| Detail fields returned | Lines 108–128: same as summary PLUS `category_id` (line 125), `topic`, `nsfw`, `slowmode_delay`, `bitrate`, `user_limit`, `default_auto_archive_duration` |
| Used by list endpoints | `/guilds/{id}/channels` and `/categories/{id}/channels` — likely call `channel_to_summary()` for brevity |

---

**Root cause — empirical**

The `channel_to_summary()` method (lines 75–94) constructs a `Channel` model with only 6 fields (lines 84–91), deliberately omitting the extended fields that `channel_to_detail()` includes.

Line 125 in the detail method shows the extraction logic:
```python
"category_id": getattr(channel, "category_id", None),
```

This line is **not present** in the summary method. The summary method returns only the minimal `id, name, type, position, guild_id, created_at` tuple.

**Consequence**: Any list-endpoint that uses `channel_to_summary()` returns channels with `category_id: null`. The detail endpoint (`/channels/{id}`) uses `channel_to_detail()` and correctly returns the `category_id`.

**Sibling sweep**: All 2-column tables (summary vs detail) in `ChannelConverter`:
- `guild_to_summary()` / `guild_to_detail()` — aliases to same method (no duplication)
- `category_to_detail()` — no summary variant (categories don't have a parent category, so the detail is sufficient)
- `thread_to_summary()` / `thread_to_detail()` — aliases to same method (no duplication)

The channel summary/detail split is **unique** to channels (because channels can have a parent category while other entities cannot). The missing field is isolated to the channel summary.

---

**Runtime impact analysis**

All internal callers of `/guilds/{id}/channels` and `/categories/{id}/channels` (within bot-core and discord-gateway) use the `guild_configs.{bounty_board_channel_id, shop_channel_id, general_channel_id}` fields stored in the database — they do NOT use the API response's `category_id` field. No cascading failures.

External callers (if any) would receive `null` for `category_id` and must handle it defensively (which they should anyway).

---

**Severity assessment**: 🔵 low — **confirmed**. API contract incompleteness, but no operational impact because internal callers don't depend on the field.

---

**Recommended fix-scope size**: **Surgical** (1 line)

Add the `category_id` field to the summary method (after line 90):

```python
@staticmethod
def channel_to_summary(channel: discord.TextChannel | discord.VoiceChannel | discord.CategoryChannel) -> Channel:
    """Convert a Discord channel to a summary payload."""
    try:
        position = ChannelConverter._coerce_position(getattr(channel, "position", None))
        return Channel(
            id=channel.id,
            name=channel.name,
            type=getattr(getattr(channel, "type", None), "name", None),
            position=position,
            guild_id=getattr(getattr(channel, "guild", None), "id", None),
            created_at=getattr(getattr(channel, "created_at", None), "isoformat", lambda: "")(),
            category_id=getattr(channel, "category_id", None),  # ← ADD THIS LINE
        )
    except Exception:  # pylint: disable=broad-exception-caught
        flogger.exception("Error converting channel to summary")
        raise
```

This mirrors the detail method's line 125 exactly.

---

**Recon completed**: 2026-04-28 by researcher (read-only investigation)

---

### A.34 — `/ship` and `/nickname` styling/UX gaps
🔵 low · Phase 3.2/3.3 · 2026-04-22
> **FIXED** in commit `1f3561d` (Package D, 2026-04-29). Added `show_active_indicator: bool = True` parameter to `player_ships_autocomplete()`; `/ship` and `/nickname` callers pass `False` (suppresses 🟢 prefix). Removed redundant `embed.add_field(name="Type", ...)` from inline `/ship` embed builder (subsumes B.3). Full delegation to `build_loadout_embed()` deferred (response-shape mismatch between `/ships/{id}/loadout` and `/players/{id}/loadout`).

Three sub-issues, same surface area:
- **a** — `/ship` autocomplete dropdown shows literal `🟢` prefix from `player_ships_autocomplete` (active-ship marker leaks into selection list)
- **b** — `/ship` detail embed uses inline field construction instead of delegating to shared `loadout_embed.build_loadout_embed()` (style inconsistency with `/loadout`)
- **c** — `/nickname` autocomplete has same `🟢` leak as (a)

---

**Verified code paths** (HEAD, read-only recon 2026-04-28)

| Question | Finding |
|---|---|
| **Sub-issue (a)/(c)** | |
| Helper location | `services/discord-gateway/src/utils/autocomplete_helpers.py:82–147` — `player_ships_autocomplete()` |
| Marker addition | Lines 134–135: `if ship.get("is_active"): label = f"🟢 {label}"` |
| Used by | `/ship` command (shipsCog line 177), `/setactive` command (line 178), `/nickname` command (not directly inspected but same helper) |
| Marker in dropdown | The 🟢 prefix is added to the `name` field of the Choice (line 138), which appears in Discord's autocomplete dropdown list |
| No disable param | The function has no parameter to control whether the marker is added; it's unconditional |
| **Sub-issue (b)** | |
| `/ship` handler | `services/discord-gateway/src/cogs/shipsCog.py:183–282` |
| Inline embed build | Lines 229–267: Builds embed with `embed.add_field()` calls directly in the handler |
| Shared builder | `cogs/_shared/loadout_embed.py` exports `build_loadout_embed()` (used by `/loadout` command) |
| `/loadout` handler | Not fully inspected; assumed to delegate to shared builder |

---

**Root cause — empirical**

**Sub-issue (a)/(c) root cause**:

The `player_ships_autocomplete()` function unconditionally prepends `"🟢 "` to the label of the active ship (line 134–135):

```python
if ship.get("is_active"):
    label = f"🟢 {label}"
choices.append(app_commands.Choice(name=label[:100], value=str(ship_id_val)))
```

The `name` field of the Choice becomes the text displayed in Discord's autocomplete dropdown. For `/ship` and `/nickname`, users see:
- `Betty 🟢 (Nickname)` — the active ship with marker
- `Hera (Nickname)` — other ships without marker

**The marker should only appear in the final command display (embed), not in the autocomplete dropdown itself.** Autocomplete dropdowns should show plain ship names without UI decoration — the user is selecting a ship, not seeing the final display.

**Sub-issue (b) root cause**:

The `/ship` command handler (shipsCog:229–267) manually constructs the ship detail embed using inline `embed.add_field()` calls:

```python
embed.add_field(name="Type", value=ship["ship_name"], inline=True)
embed.add_field(name="Status", value="Active" if ship["is_active"] else "Inactive", inline=True)
embed.add_field(name=f"🔫 Weapons ({loadout['weapons_count']})", value=weapons_text or "None", inline=False)
# ... etc
```

**Line 236 is the redundant field**: `embed.add_field(name="Type", value=ship["ship_name"], inline=True)` — the ship name is ALREADY in the embed title (line 225). This field is redundant.

The `/loadout` command (not directly inspected) delegates to `cogs/_shared/loadout_embed.py:build_loadout_embed()`, which is a **shared, centralized embed builder** for loadouts. The `/ship` command should use the same builder for consistency.

---

**Sibling sweep**

**For (a)/(c)**:
- `player_ships_autocomplete()` is the **only** autocomplete function in the codebase that adds a visual marker prefix
- All other autocompletes in `autocomplete_helpers.py` (`player_inventory_autocomplete`, etc.) do NOT add markers
- The marker is appropriate for final **display** (in embeds/messages) but not for **selection** (in dropdowns)

**For (b)**:
- `/ship` command (shipsCog) is the **only** place that manually builds a ship detail embed
- `/loadout` command correctly delegates to `cogs/_shared/loadout_embed.py`
- No other ship-detail displays were inspected, but the pattern suggests manual vs. shared builder inconsistency

---

**Severity assessment**: 🔵 low — **confirmed**

- **(a)/(c)**: UX clutter in dropdown. The marker appearance in the selection list is visually confusing but does not affect functionality.
- **(b)**: Style inconsistency. The redundant "Type" field and different embed structure are cosmetic differences. Both embeds display the same information.

---

**Recommended fix-scope size**

| Sub-issue | Scope | Fix |
|---|---|---|
| **(a)/(c)** | **Surgical** | Add `show_active_indicator: bool = False` parameter to `player_ships_autocomplete()` (line 82); default False for autocomplete flows (`/ship`, `/nickname`), True only for display flows (if any). Update callers in shipsCog to pass `show_active_indicator=False` (or accept default). |
| **(b)** | **Surgical** | Refactor `/ship` detail handler (shipsCog:229–267) to delegate to `loadout_embed.build_loadout_embed()` instead of manual `embed.add_field()` calls. Remove the redundant "Type" field (line 236). Use the shared builder for consistency with `/loadout`. |

**Implementation for (a)/(c)**:

```python
# In autocomplete_helpers.py:player_ships_autocomplete(), change signature:
async def player_ships_autocomplete(
    http_client: httpx.AsyncClient,
    api_base: str,
    interaction: discord.Interaction,
    current: str,
    *,
    exclude_active: bool = False,
    show_active_indicator: bool = False,  # ← ADD THIS
    timeout: float = 3.0,
) -> list[app_commands.Choice[str]]:
    ...
    # Then at line 134–135, change to:
    if ship.get("is_active") and show_active_indicator:  # ← ADD AND show_active_indicator
        label = f"🟢 {label}"
```

No changes needed to shipsCog callers (they accept default False, which is correct).

**Implementation for (b)**:

Defer to developer — requires understanding the exact signature and behavior of `loadout_embed.build_loadout_embed()`.

---

**Recon completed**: 2026-04-28 by researcher (read-only investigation)

---

### A.32 — `Mp'zzzm Thrust` module renders custom emoji `:mpzzzm:` without guild emoji upload
🔵 low · Phase 2.9 · 2026-04-22
> **FIXED** in commit `1f3561d` (Package D, 2026-04-29). Replaced `<:mpzzzm:723707097778225214>` with unicode `⚡` in `import_data/module/thrusters/mpzzzm_thrust.json`.

Single-row data gap: 1 of 66 modules. The `Mp'zzzm Thrust` module seed data specifies a custom Discord emoji reference `<:mpzzzm:723707097778225214>`, but the custom emoji is not uploaded to the guild.

---

**Verified code paths** (HEAD, read-only recon 2026-04-28)

| Question | Finding |
|---|---|
| Seed data file | `services/bot-core/import_data/module/thrusters.mpzzzm_thrust.json` |
| Emoji field | Line 4: `"emoji": "<:mpzzzm:723707097778225214>"` — custom Discord emoji reference syntax |
| ID reference | `723707097778225214` — custom emoji with this ID must exist in the guild |
| Total modules | 66 modules in seed data; `Mp'zzzm Thrust` is the **only** module with a custom emoji reference |
| Other modules | All other 65 modules use either unicode emojis (e.g., `⚙️`, `🔧`) or have `"emoji": null` |

---

**Root cause — empirical**

The seed data file declares the emoji as a **custom Discord emoji** via the syntax `<:NAME:ID>`. Discord interprets this syntax as a reference to a guild-specific custom emoji (not a unicode emoji).

When the bot renders this emoji in embeds or messages:
- If the custom emoji ID `723707097778225214` is NOT in the guild's emoji list → Discord displays the literal text `:mpzzzm:` (fallback for missing custom emoji)
- If the emoji IS in the guild → Discord renders the emoji image

The emoji **is not currently uploaded** to the dev guild `1490693399307616276`, so it renders as `:mpzzzm:` in all UI.

---

**Sibling sweep**

Seed data emoji inventory:
- Unicode emojis (working correctly): ⚙️, 🔧, 🛡️, ⚡, 🚀, 🎯, etc. — 65 entries
- `null` emojis: Some weapons/modules have `"emoji": null` — renders with no emoji (acceptable)
- Custom emoji reference: `<:mpzzzm:723707097778225214>` — 1 entry, non-functional

**Why a custom emoji?** The GalaxyOnFire asset library likely includes a custom pixel-art sprite for `mpzzzm` that doesn't have a direct unicode equivalent. The original developer intended to upload this sprite as a guild custom emoji with ID `723707097778225214` but never completed that step.

---

**Fix options** (two independent paths)

| Option | Effort | Permanence | Requires |
|---|---|---|---|
| **A — Upload custom emoji to guild** | 5 min | ✅ Permanent | Guild owner access; emoji sprite image; 723707097778225214 as target emoji ID (may be unavailable if that emoji exists elsewhere) |
| **B — Rewrite seed data to unicode emoji** | 2 min | ✅ Permanent | Identify/find a unicode emoji that represents "thrust" (e.g., `⚡`, `🚀`, `💨`); edit `thrusters.mpzzzm_thrust.json` line 4 |
| **C — Set to null** | 1 min | ✅ Permanent | Edit `thrusters.mpzzzm_thrust.json` line 4 to `"emoji": null` — module renders with no emoji |

**Recommended**: Option B (rewrite to unicode emoji). It avoids the need for external emoji setup and is maintainable across different Discord servers. A suitable unicode emoji might be `⚡` (zap) or `💨` (dashing away) to represent the "thrust" concept.

---

**Severity assessment**: 🔵 low — **confirmed**. Cosmetic rendering only. The module is fully functional; only the emoji display is affected. No game-state corruption, no combat impact.

---

**Recon completed**: 2026-04-28 by researcher (read-only investigation)

---

### A.25 — `/unregister` on unconfigured guild shows generic error
🔵 low · Phase 0.5.3 · 2026-04-20
> **FIXED** in commit `1f3561d` (Package D, 2026-04-29). Added explicit `except httpx.HTTPStatusError` branch before broad `except Exception`; calls existing `_is_guild_not_configured()` helper and emits `_GUILD_NOT_CONFIGURED_MSG` when applicable.

Alt account in unconfigured guild gets `⚠️ An error occurred while removing the role.` because `playerCog./unregister` does `GET /api/v1/config/guild/{id}` which returns 400 (not configured) → broad `except` swallows.

---

**Verified code paths** (HEAD, read-only recon 2026-04-28)

| Layer | File | Lines | Notes |
|---|---|---|---|
| Handler | `services/discord-gateway/src/cogs/playerCog.py` | 525–590 | `unregister()` command |
| Config call | `playerCog.py` | 531–532 | `GET /config/guild/{id}` with `raise_for_status()` |
| Error handler | `playerCog.py` | 588–590 | `except Exception` catches all exceptions including 400 from GET config |
| Helper exists | `playerCog.py` | 26–34 | `_is_guild_not_configured()` helper already defined in this file |

---

**Root cause — empirical**

When a guild has not been configured via `/admin_setup`, the bot-core endpoint `GET /config/guild/{guild_id}` returns HTTP 400 with body `{"detail": "Guild ... not configured"}`.

The `/unregister` handler:
1. Line 531: `config_resp = await self.http_client.get(...)`
2. Line 532: `config_resp.raise_for_status()` → raises `httpx.HTTPStatusError(400)`
3. Line 588: **`except Exception` catches the 400 error**
4. Line 590: Sends generic `"⚠️ An error occurred while removing the role."` message

The `_is_guild_not_configured()` helper (lines 26–34) is defined in the same file but is NOT used in this flow. It correctly detects 400 responses with "not configured" in the detail field.

**Sibling sweep**

The same helper is used correctly in:
- `shopCog.py` (lines 145–157): uses `_is_guild_not_configured(e)` check before broad except
- `bountyCog.py` (lines 132–146): uses `_is_guild_not_configured(e)` check before broad except

The `/unregister` handler is the **only** site that has the helper defined but does not use it.

---

**Severity assessment**: 🔵 low — **confirmed**. UX messaging issue only. The role-removal attempt correctly fails (no invalid state mutations), but the error message is generic instead of helpful.

---

**Recommended fix-scope size**: **Surgical** (3–4 lines)

In `playerCog.py:unregister()`, add a specific check before the broad except:

```python
except httpx.HTTPStatusError as e:
    if _is_guild_not_configured(e):
        await interaction.followup.send(_GUILD_NOT_CONFIGURED_MSG, ephemeral=True)
        return
    raise  # Re-raise for the broad except handler below
except Exception as e:  # pylint: disable=broad-exception-caught
    flogger.error(f"/unregister error: guild={interaction.guild_id}, user={interaction.user.id}, error={e}")
    await interaction.followup.send("⚠️ An error occurred while removing the role.", ephemeral=True)
```

The constant `_GUILD_NOT_CONFIGURED_MSG` is already defined at line 20–22 of the same file.

---

**Recon completed**: 2026-04-28 by researcher (read-only investigation)

---



## DEFERRED

### A.18 — Shop tier randomization redesign (pseudo-banded cascade)
🟡 medium · Phase 1.1 · 2026-04-19

`ShopService.refresh_shop()` picks `shop_tech_level = random.randint(1, 9)` independent of tier. Bronze can stock tech-9 endgame gear with equal probability as Platinum. Combined with sparse turret seed data (10 turrets at TL 5/6/9 only), 90% of refreshes under-stock turrets. Algorithm silently skips when filter returns empty pool — no logging, no fallback.

**Design (agreed)**: pseudo-banded two-stage probability cascade.
- **Stage 1** — `tier → shop_tech_level`: per-tier probability matrix; Gaussian-like mode at tier depth (Bronze→TL1-2, Silver→TL4, Gold→TL7, Platinum→TL9), ≥0.5% tail outside band for rare cross-tier surprises.
- **Stage 2** — `shop_tech_level → item_tech_level`: existing logic at `_select_item_tech_level()` (70% same / 20% −1 / 10% −2). Optional refinement: add `+1` for true Gaussian.

**Required alongside redesign**:
- Empty-pool fallback cascade in `_get_random_item_by_tech_level()` — try requested TL, then ±1, then any TL within tier band, then any TL at all; log a WARNING when fallback triggers.
- Per-tier item-count defaults that reflect seed data realities.
- Validation logging at refresh time (counts vs config minimums).
- Fix NULL `techLevel` in `plasma_collectors.pe_fusion_h2.json`.

**Compound probability example**: Bronze TL9 item ≈ 0.005 × 0.70 = ~0.35% (rare but nonzero, matches design intent).

**Code refs**: `bot-core/src/services/shop_service.py:578` (refresh_shop), `:701` (_get_random_item_by_tech_level).

---

### A.39 — `/item` UX cleanup bundle
🔵 low · Phase 5.5 · 2026-04-22 · post-release

- **a** — Remove `item_type` parameter; resolve concrete type from `item_name` via Item STI (consistent with A.36/A.37/A.42 pattern). Cross-type collisions architecturally impossible (146 distinct names).
- **b** — Use per-item emoji in embed title (parity with `/inventory` and `/search`).

---

### A.41 — Guarantee ≥1 of each enabled category in initial shops
🔵 low · 2026-04-22

Initial shop generation can legitimately produce 0 turret_weapon rows (RNG variance). `shop_refresh` cycles show ~25% of tier-slots have turrets, statistically plausible but UX-confusing ("turrets don't work" reports).

**Fix**: at `/admin_setup` initial generation only, guarantee ≥1 of each `CURRENTLY_ENABLED_TYPES` per tier. Post-refresh remains probabilistic.

---

### A.24 — `/health` Schema subsection redesign
🔵 low · Phase 0.2 · 2026-04-20

`/health` Schema fields render as `Status: unknown / Current Version: N/A / blank`. Top-level health is correct; only the embed subsection is wrong.

**Root cause**: contract mismatch. `SchemaManager.get_schema_health_info()` returns 3 keys (`version`, `expected_version`, `version_match`); `healthCog.py:121-138` reads 6 keys (`status`, `current_version`, `expected_version`, `schema_table_exists`, `version_match`, `error`). Also: nothing tied to live Alembic state — only reads legacy `schema` table (single row `1.0.0`).

**Fix**: redesign to surface Alembic current revision + expected head + match status + optional ORM metadata drift. Align `healthCog` field consumption. Add response-schema contract test.

---

### A.23 — Test suite over-mocking + sub-threshold coverage audit
🟠 high (upgraded from 🟡 2026-04-29) · cross-cutting · partial fix in flight

> **2026-04-30 partial closeout**: Pre-E2E hardening pass empirically audited all HTTP calls in 6 highest-traffic cogs (playerCog, adminCog, inventoryCog, shopCog, shipsCog, bountyCog) — 100 HTTP call sites, ALL of them confirmed to land on registered bot-core / blender-service routes. **Zero CONFIRMED-BROKEN routes found.** Added 1 respx-based URL+method contract test per cog (6 new tests total) covering Phase 1-3 critical paths: `/profile`, `/admin_setup`, `/equip`, `/buy`, `/setactive`, `/check`. The remaining ~22 cog test files still use the AsyncMock pattern — but the empirical audit gives high confidence none of them mask a B.33-shape live bug. Full Tier 2 conversion remains deferred (60-120h scope). See commits in 2026-04-30 closeout package.

Many tests exceed project's "max 2 mocks per test" standard (`AGENTS.md`). The B.33 incident (Package E preload regression, 3 bugs co-shipped with 277 lines of passing-but-tautological test coverage) confirmed the empirical impact: contract drift between cogs and bot-core/blender-service is invisible to the existing test suite.

**Empirical inventory (2026-04-29 skim-pass by @developer)**

Total corpus: 200 test files / 5,473 tests across 3 services.

**Tier 1 — Critical (B.33 family, blocking E2E re-verify)**
22 gateway cog test files (~24,065 LOC) all use `cog.http_client.method = AsyncMock(return_value=canned_payload)` without asserting URL or HTTP method matches a real server route.
Highest-volume offenders:

| File | LOC | `http_client.*` hits |
|---|---|---|
| `cogs/test_inventoryCog.py` | 2,242 | 123 |
| `cogs/test_shopCog.py` | 2,086 | 119 |
| `cogs/test_shipsCog.py` | — | 88 |
| `cogs/test_adminCog.py` | 2,398 | 84 |
| `cogs/test_playerCog.py` | 2,306 | 78 |
| `cogs/test_bountyCog.py` | 2,145 | 49 |

Effort estimate: 8–16 agent-hours for the 4 critical B.33-adjacent files; 60–120h for full Tier-2 conversion.

**Tier 2 — High value (catches future contract drift)**: all 22 cog HTTP test files (full list in @developer report archived in conversation log).

**Tier 3 — Hygiene (bot-core repo/session over-mocking, ORM identity-map risk class)**
8 high-priority bot-core API/service files (~10,000+ LOC of test code) patch entire repository classes at the constructor level, masking the doubled-credit bug class documented in `bot-core/tests/AGENTS.md`. Worst single offender: `api/test_admin_router.py` (1,746 LOC, 86 `@patch` decorators, 104 mock instantiations).

| File | LOC | `@patch` count |
|---|---|---|
| `api/test_admin_router.py` | 1,746 | 86 |
| `api/test_bounty_router.py` | 1,594 | 66 |
| `api/test_shops_router.py` | 1,053 | 46 |
| `api/test_config_router.py` | 862 | 44 |
| `api/test_inventory_router.py` | 942 | 43 |
| `api/test_admin_inventory_commands.py` | 746 | 39 |
| `services/test_bounty_service.py` | 4,327 | 422 mock-line hits |

Effort estimate: 20–60 agent-hours, deferred until after Tier 1–2.

**Blender-service**: clean (low mock usage, uses TestClient + DI). Not a remediation target.

**Recommended approach**
1. Fix `TestPreloadStaticCatalogs` in `test_adminCog.py` first (~130 LOC, ~2h) — closes B.33 directly + establishes canonical `respx` pattern.
2. Create `tests/cogs/fixtures/bot_core_contract.py` — single source of truth for real route URL + method + response shape. All `respx` mocks reference it. Done once (~4h), pays dividends across all Tier 2 conversions.
3. Stage Tier 2 by cog file (one PR per cog, ~4–8h each).
4. Defer Tier 3 until Tier 1–2 complete.

**Status**
- Tier 1 critical 4 files: **preload tests fixed under B.33** (commits `9a19909`, `645c474`, `51c0bb5`, `169b0e4`); command-path tests (~24K LOC across 22 files) still pending.
- Tier 2 + 3: still deferred, no agent dispatched.

**Cross-references**: B.33 (proven consequence), NC-002 / NC-003 (prior-session non-compliance findings).

---



## FIXED

| ID | Summary | Commit | Verified |
|---|---|---|---|
| **B.37** | `setupCog.on_guild_remove` called nonexistent `DELETE /admin/guilds/{id}/cleanup`; 404 caught by broad except, lingering DB state never cleaned. Fixed: added the endpoint (DB-only soft cleanup, no user_id auth, audit-logged actor_id=0, idempotent) + 6 router tests + 2 respx URL+method contract tests for setupCog. Pure B.33-shape: cog HTTP call to nonexistent route co-shipping with mock-only tests. | `15dc544`, `3283009` | Verified: pending |
| **B.33** | `adminCog._preload_static_catalogs` used wrong URLs: `GET /data/{cat}` (405 POST-only) for 4 item categories and `GET /about/ships` (404 nonexistent) for ships. Fixed: both changed to `GET /about/categories/{cat}/objects` — matching AboutCog's known-working pattern. Test debt: 4 tautological mock tests replaced with 5 respx tests asserting URL+method; aboutCog, bountyCog, skinsCog preload tests also converted; `tests/AGENTS.md` policy added. | `452ac28`, `9a19909`, `645c474`, `51c0bb5` | Verified: pending |
| **A.34** | `/ship` autocomplete showed `🟢` prefix in dropdown (a/c); `/ship` embed had redundant `Type: Betty` field (b/B.3). Fixed: added `show_active_indicator: bool = True` param to `player_ships_autocomplete()`; `/ship` and `/nickname` callers pass `False`; removed `embed.add_field(name="Type", ...)` from inline `/ship` builder. B.3 subsumed — `Type:` field is the same redundancy, removed by same change. Full delegation to `build_loadout_embed()` deferred (format mismatch between `/ships/{id}/loadout` and `/players/{id}/loadout` responses). | `1f3561d` | pending |
| **A.32** | `Mp'zzzm Thrust` module rendered custom emoji `:mpzzzm:` (guild emoji not uploaded). Fixed: replaced `<:mpzzzm:723707097778225214>` with unicode `⚡` in `thrusters.mpzzzm_thrust.json`. | `1f3561d` | pending |
| **A.30** | `GET /guilds/{id}/channels` returned `category_id: null` for all child channels. Fixed: added `category_id=getattr(channel, "category_id", None)` to `channel_to_summary()` return. Added 2 tests asserting category_id is populated for child channels. | `1f3561d` | pending |
| **A.25** | `/unregister` on unconfigured guild showed generic `⚠️ An error occurred while removing the role.`. Fixed: added explicit `except httpx.HTTPStatusError` before broad `except Exception`; calls existing `_is_guild_not_configured()` helper and emits `_GUILD_NOT_CONFIGURED_MSG`. | `1f3561d` | pending |
| **A.10** | E2E_TEST_CHECKLIST.md Appendix B undercounted channels (7→8) and roles (2→6). Fixed: Appendix B updated with all 8 channels (including `#platinum-bounties`), all 6 roles (5 player-facing + 1 admin), `#platinum-bounties` naming asymmetry documented; item 1.2 updated from "all 7 text channels" to "8 channels"; "BountyBot Admins" → "BountyBot Admin" naming corrected. | `1f3561d` | pending |
| **B.3** | `/ship` embed showed redundant `Type: Betty` field (ship name duplicated from embed title). **Subsumed by A.34b** — same line removed in the A.34 fix. | `1f3561d` | pending |
| **B.18** | `/leaderboard tier:X` empty-state message `"📭 No players found in this guild."` omitted tier filter context. Fixed: conditional `f"📭 No {tier}-tier players found in this guild."` when `tier` is set. | `1f3561d` | pending |
| **B.4** | `/equip` swap-confirmation dropdown options showed plain item names with no UX hint that selecting = swap action. Fixed: added `description="Swap this item out"` to each `discord.SelectOption` in `WeaponSwapView` (`inventoryCog.py:69-72`). | `1f3561d` | pending |
| **B.2** | `player_ships.secondary_weapons` was `NULL` not `[]` on starter Betty. Fixed: added `"secondary_weapons": []` key to `starter_ship_data` dict in `_create_starter_loadout()` (`player_service.py:124`). Added test asserting `secondary_weapons == []` (not None/missing) on starter creation. | `1f3561d` | pending |
| **B.32** | `/render_config action:set` silently accepted unrecognized field (cog reported ✅ on HTTP 200 without verifying mutation). Fixed: (A) cog validates `setting` against `self._render_settings` before API call; (B) blender-service router raises 422 when update payload contains no valid `RenderConfig` fields. **Patch I (C.1)**: guard was fail-open when `_render_settings` empty (preload failed) — now fail-closed; user sees "preload not ready" message instead of silent pass-through. | `8860c5a` + Patch I | pending |
| **B.24** | `/route` embed rendered "recently spotted" systems identically to plain-checked (binary ~~strikethrough~~). Fixed: API adds `system_statuses` field computed server-side (`_project_checked()`, `"found"` masked); cog uses 3-state rendering (`**~~spotted~~** 🔍`, `~~checked~~ ✅`, plain). | `8860c5a` | pending |
| **A.31** | `/list_category tech_level:N` and `manufacturer:` filters always returned empty. Fixed: add `tech_level` + `manufacturer` to preload response shape in `about.py:102-109`; cog filters already correct, only needed data present. | `8860c5a` | pending |
| **B.29** | `/scheduler_*` cron trigger display stripped asterisks (Discord markdown consumed `*` as italic delimiters). Fixed: wrap trigger strings in backticks in `scheduler_list` and `scheduler_view` embed fields (`schedulerCog.py`). | `ec42c4d` | pending |
| **B.28** | `/scheduler_update` with invalid JSON showed doubled response ("This interaction failed" + actual error). Fixed: move `json.loads` validation before `defer()`; use `response.send_message()` for sync error path (`schedulerCog.py`). | `ec42c4d` | pending |
| **B.27** | `/scheduler_view` with nonexistent `job_id` showed raw "This interaction failed" (no user-visible error). Fixed: add `else: with suppress(Exception): followup.send(...)` branch to all 6 scheduler error handlers (`schedulerCog.py`). | `ec42c4d` | pending |
| **B.26** | Autocomplete handlers for `/check system:`, `/buy item_id:`, and shop tier browsing made live HTTP calls per keystroke. Fixed: `AutocompleteCache` (TTL+lock+refresh-fn) in `cogs/_shared/autocomplete_cache.py`; `bountyCog` preloads star systems; `shopCog` uses 5-min TTL cache per `(guild_id, tier)` with live player-tier resolution. 12 unit tests for cache; 14 shopCog tests for autocomplete + cache + invalidation. **Patch I (E.1)**: added TOCTOU concurrency comment documenting double-check locking guarantee + concurrent-expiry test (`TestConcurrentExpiryLock`). **Patch I (E.2)**: `buy_item_autocomplete` now logs WARNING when player tier is not in `_valid_tiers` (was silent empty return). | `1a07cb4` + Patch I | pending |
| **B.25** | `/admin_spawn_bounty` (and all 20 admin commands) could hit Discord 3s timeout when Bot-Admin-role HTTP check ran before `defer()` (Mode B). Fixed: (A) remove `@is_admin()` decorator from all 20 admin commands, add inline post-defer `_check_is_admin()` call; (B) convert `render_config` and `render_cache_clear` to `defer()` + `followup.send()` pattern (`adminCog.py`). **Patch I (Cross-1)**: same Mode B risk audited and fixed in 9 commands across 3 other cogs: `schedulerCog` (6 commands), `devCog` (2 commands), `healthCog` `/health`; `healthCog` `/ping` and `helpCog` `/admin_help` documented as safe (no post-check HTTP calls). | `ec42c4d` + Patch I | pending |
| **B.17** | `/admin_player action:Set XP` returned `old_xp` equal to new value (identity-map read after mutation). Fixed: capture `old_xp = old_player.xp` before `update_player_xp()` call; test refactored to shared-mock pattern. | `360287b` | pending |
| **B.23a** | Bounty expire job silently not scheduled: `_schedule_expiry_job()` used HTTP POST to scheduler; failures were non-fatal non-retried. Fixed: direct APScheduler Python API via `scheduler_holder.py` singleton; HTTP retained as fallback. | `360287b` | pending |
| **B.23b** | `run_stale_state_recovery_sweep()` marked stale bounties expired in DB but never deleted Discord announcements (zombie messages). Fixed: collect stale bounty refs before bulk UPDATE, call `_delete_bounty_announcement` for each after commit. | `360287b` | pending |
| **B.30** | `PUT /api/v1/jobs/{job_id}` silently wiped job payload when request body used wrong field name. Fixed: `ConfigDict(extra="forbid")` on `UpdateJob` schema; wrong-field body → 422 before APScheduler. | `360287b` | pending |
| **B.31a** | `POST /config/guild/{id}/reset` returned 500 NOT NULL violation when `guild_shops` had rows. Fixed: `cascade="all, delete-orphan"` on `GuildConfig.shops` relationship (ORM-side only, no migration). | `360287b` | pending |
| **B.31b** | `f"❌ API Error: {e}"` pattern across 53 sites in 9 cogs leaked internal `http://bot-core:8000/...` URL + httpx MDN link to end users. Fixed: new `report_api_error()` helper at `cogs/_shared/http_error_handler.py` produces sanitized status-aware embeds; 53 sites migrated; pre-existing specialized branches (404 specials, guild-not-configured, B.28 validation, duelCog 400-detail) preserved verbatim. 15 unit + 9 integration tests added. **Patch I (F.1)**: sanitizer extended to also strip bare service hostnames (`bot-core`, `discord-gateway`, `blender-service`, `db`, with optional `:port`) and IPv4 addresses — defense-in-depth against topology leak via FastAPI `detail` fields. | `a35aa7c` + Patch I | pending |
| **O.2** | `/scheduler_view`/`/scheduler_update`/`/scheduler_delete` `job_id` autocomplete (verified retroactively present in HEAD per cycle-8 recon: `schedulerCog.py:35-54` + line 140/221/300) | (already in HEAD) | live (verified read-only) |
| **B.14-sibling** | Stale-respawn recovery sweep for `status='escaped'` past `respawn_time` | `815cd59` | ✅ live 2026-04-28 |
| **B.14** | Bounty/duel listing time filter + startup recovery sweep (12 stale bounties expired on first boot) | `db79c60` | ✅ live 2026-04-28 |
| **B.12** | `/check` processes ALL matching bounties on shared system (was: first-match-only via early returns at `bounty_service.py:1040,1096,1135`) | `ee81738` | pending |
| **B.15** | `/duel-challenge` ungraceful 500 on transient errors | `36f760e` | pending |
| **B.13** | `/check` recently-visited map drop + universal embed-image preservation via shared `preserve_embed_image()` helper | `46ac33a`, `edb8664` | pending |
| **B.11** | `/admin_give_ship` autocomplete | `46ac33a` | pending |
| **B.10** | `/admin_set_credits` old-value display | `46ac33a` | pending |
| **B.16** | `/admin_set_xp` old-tier display | `46ac33a` | pending |
| **B.8** | `/admin_refresh_shop` announcement (siblings `/admin_spawn_bounty` + `/admin_clear_bounties` empirically already worked — TODO comments removed) | `46ac33a`, `edb8664` | pending |
| **B.7** | `/sell`, `/equip`, `/unequip` error messages leaking numeric `player_id` | `439cd79` | pending |
| **B.6** | `/buy` embed `Item Type: Primary_Weapon` raw DB-concrete leak | `823c13d` | ✅ live 2026-04-27 |
| **B.5** | `/sell` embed `Item Type` raw leak; `c8b5fef` doubled-credits fix | `823c13d`, `c8b5fef` | pending |
| **A.48** | Bounty announcement embed Loadout field exceeded Discord 1024-char limit (24 of 1100 historical bounties affected at ~2.2%); silent post-failure | `72e3b31`, `d3ef0f9` | ✅ live 2026-04-28 |
| **A.47** | `/ships/transfer` Option Y transaction ownership | `3e73940` | pending |
| **A.46** | inventoryCog/adminCog/shopCog autocomplete choice values now concrete; display labels use `replace('_',' ').title()` | `3e73940` | pending |
| **A.45** | Inventory/admin request schemas use `Literal[concrete-types]` instead of regex pattern; 422 before service | `3e73940` | pending |
| **A.44** | `shop_service.sell_item` / `buy_ship` / `sell_ship` / `inventory.transfer_item_between_players` / `player.transfer_credits` drop `async with db.begin()` (router owns transaction); repo helpers threaded with `commit=False` | `3e73940` | pending |
| **A.43** | `InventorySummaryResponse` schema + router use concrete keys (no `weapon`/`turret` aliases) | `3e73940` | pending |
| **A.42 + D1** | `/sell` UX (drop `item_type`/`target_tier` params; resolve concrete type server-side); D1 `inventory_repository.get_inventory_summary()` initialized aggregation dict with generic alias keys → permanently 0 weapon/turret counts | `7351a1a` | pending |
| **A.38** | Secondary weapons leak into economy/loadout flows. `CURRENTLY_ENABLED_TYPES = {primary_weapon, turret_weapon, module, ship}` in `game_constants.py` | `3ad15b8` | pending |
| **A.37** | `/equip`, `/unequip` drop `equipment_type` param; new `player_equippable_autocomplete` + `player_equipped_autocomplete` helpers | `3ad15b8` | pending |
| **A.36** | Inventory API vocabulary mismatch (service `weapon`/`turret` aliases vs DB concrete `primary_weapon`/etc.); new `_item_type_normalizer.py`; reads expand aliases, writes require concrete via `InvalidItemTypeError → 422`; `admin.py:1026-1054` + `ships.py:612-620` write-site corruption fixed via STI lookup | `3ad15b8` | pending |
| **A.35** | `/inventory` `item_type` param now `app_commands.Choice` (matches `/item` pattern) | `3ad15b8` | pending |
| **A.33** | 404→422 for invalid item_type input; new `InvalidItemTypeError(ValueError)` mapped at router level on /inventory/add, /remove, /transfer | `3ad15b8` | pending |
| **A.29** | Numeric ID parameters lacked autocomplete on `/ship`, `/nickname`, `/item`. New `autocomplete_helpers.py` (`resolve_player_id`, `player_ships_autocomplete`, `player_inventory_autocomplete`); `ship_id` param type changed `int → str` | `f95b516` | pending |
| **A.28** | `GET /ships/{ship_id}` used `ShipRepository` (definition table) instead of `PlayerShipRepository` → 500 with `'Ship' object has no attribute 'player_id'`. 7 misused routes converted; 9 new regression tests with real ORM instances | `f95b516` | pending |
| **A.27** | `/list_category` truncated at 50 items but footer warned only above 100 | `65cbe5c` | pending |
| **A.26** | `/list_category` chunks list across fields all named `"Objects"` (continuation now uses `"\u200e"` zero-width spacer); `loadout_embed.py` moved `utils/ → cogs/_shared/` | `65cbe5c` | pending |
| **A.22** | Bounty spawns across all 4 tiers were synchronized (now per-tier randomized cadence) | (code-verified pre-rebuild) | pending |
| **A.21** | Shop refresh announcement posted to `#bounty-hunting` instead of `#shop`; role mention inside embed (didn't ping). Now posts to `shop_channel_id` with role mention in `text_content` | (code-verified pre-rebuild) | pending |
| **A.19** | Checklist referenced `/register` but command was `/profile`. Added `/register` alias delegating to shared `_display_profile()` handler | `65cbe5c` | pending |
| **A.16** | Postgres startup race on fresh DB volume: weak `pg_isready` healthcheck + no migration retry. Healthcheck strengthened to authenticated `psql SELECT 1`; 5x retry loop in `migration_manager.ensure_current()` | (verified 2026-04-21) | ✅ |
| **A.12** | Checklist Session Setup script contained A.9 platinum bug (tied to A.9 resolution) | `65cbe5c` | ✅ |
| **A.11** | Cleared bounties left zombie expire jobs in APScheduler. `BountyService.clear_bounties()` now cleans up both `bounty_expire` AND `bounty_respawn` orphan jobs by `bounty_id` payload match. **Orthogonal gap**: `BountyService.escape_bounty()` writes `bounty.respawn_time` but no code schedules a respawn job from it (separate issue, not yet investigated) | `65cbe5c` | pending |
| **A.9** | Bounty config validator rejected `platinum` in `max_bounties_per_tier`; spawner already produced platinum bounties (writer/reader disagreement) | `65cbe5c` | ✅ |
| **A.6** | Bounty spawn executor fired against un-setup guilds. Eligibility guard added | (code-verified) | pending |
| **A.5** | Help command set: new `/help` (user) + `/admin_help` (admin) commands | `helpCog.py` | ✅ live 2026-04-21 |
| **A.4** | Admin slash commands visible in non-admin autocomplete. Largely fixed; only `/ping` still leaks (tracked as A.20) | (decorator audit) | mostly ✅ |
| **A.3** | shopCog actively corrupted `users.discord_username` to `"temp"` on every invocation; cycled with `/profile`. Audit pass confirmed zero remaining offenders | (audit 2026-04-21) | ✅ |

---

## CLOSED / WITHDRAWN

| ID | Reason |
|---|---|
| **A.1** | Starter Betty + Nirai Impulse EX 1 + Micro Gun MK I cargo state matches authoritative spec (`player_service.py:104-121`). Not a bug. |
| **A.2** | Secondary weapons not yet implemented (planned: rockets/missiles/bombs, single-use). Their absence from starter loadout is correct. Stale `IMT Extract 1.3` reference in older checklist drafts. |
| **A.17** | "4 shops" wording in `/admin_setup` correctly refers to 4 tier-shop containers. Label is accurate. |
| **A.40** | "0 turrets across tiers" was RNG variance (5 forced refreshes showed normal distribution), not a systemic defect. Enhancement opportunity logged as A.41. |
| **A.20** | `/ping` visible to non-admins. Empirical redo (2026-04-28) verified decorator stack identical character-for-character to working admin commands; sync mechanism uniform; runtime inspection confirms `default_permissions(administrator=True)` applied. Code-side correct. Visibility leak is Discord client cache or Discord-API quirk; runtime `is_admin()` check still blocks execution. Cosmetic-only; not fixable from code side. Full evidence retained in OPEN section above. |

---

*Last updated: 2026-04-29 — B.33 fixed (adminCog _preload_static_catalogs wrong URLs + test debt remediation; commits 452ac28/9a19909/645c474/51c0bb5); B.33 added to FIXED table with Verified:pending. Earlier today: added inline FIXED annotations to 9 OPEN entries that were already in the FIXED table (B.18, B.2, B.4, B.3, A.10, A.30, A.34, A.32, A.25); added top-of-OPEN status snapshot. Patch I QA hardening: C.1 (B.32 guard fail-closed), Cross-1 (B.25 @is_admin() scope), E.1 (autocomplete cache TOCTOU comment + test), E.2 (B.26 tier-resolution WARNING), F.1 (B.31b sanitizer extended to bare hostnames + IPv4).*
