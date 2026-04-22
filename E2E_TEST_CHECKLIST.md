# BountyBot E2E Test Checklist (Discord)

Run these tests against a live stack (`docker compose up`).
Work through each phase in order — later phases depend on earlier ones.

> **Legend**
> - [ ] = not started
> - [x] = passed
> - `[2P]` = **Requires a second Discord user** (alt account, friend, or second device)
> - `[ADMIN]` = Requires admin/dev permissions in the guild
> - `[WAIT]` = Involves waiting for a scheduled timer

> **Command Syntax Notes**
> All commands use Discord's slash command UI. Parameters shown as `param:value`.
> Choice-type parameters use dropdown menus (shown as `action:View Config`).
> Autocomplete parameters search as you type (shown as `category:ship`).

> **Testing Principles**
> - Run commands one at a time; confirm each works before moving on
> - After admin mutations, query the DB or API to verify side effects (not just Discord UI)
> - Use shortcuts liberally — they save hours
> - Mark items [x] immediately when confirmed passing; leave [ ] for pending; add notes for failures
> - Alt account testing happens in natural flow (Phase 1, 7, 8 mostly)

---

## Session Setup (Run Once at Start)

> ⚠️ **IMPORTANT**: Run these commands ONLY AFTER `/admin_setup` has been completed (Phase 1, item 1.1). They depend on the guild config existing in the database.

```bash
# Save as env vars
GID=1490693399307616276
ADMIN_UID=402296276617527306

# 1. Compress bounty timers (spawn every 5 min, expire in 10 min, max 20 per tier)
# A.9 (2026-04-21): platinum restored — the validator now accepts all four tiers.
sudo docker exec bountybot-bot-core curl -s -X PUT -H 'Content-Type: application/json' \
  "http://localhost:8000/api/v1/config/guild/$GID/bounty" \
  -d "{\"guild_id\":$GID,\"bounty_spawn_interval_minutes\":5,\"bounty_expiry_minutes\":10,\"max_bounties_per_tier\":{\"bronze\":20,\"silver\":20,\"gold\":20,\"platinum\":20}}"

# 2. Lower XP thresholds for fast tier testing (Silver:10, Gold:20, Platinum:30)
sudo docker exec bountybot-bot-core curl -s -X PUT -H 'Content-Type: application/json' \
  "http://localhost:8000/api/v1/config/guild/$GID/xp-thresholds" \
  -d "{\"guild_id\":$GID,\"thresholds\":{\"Silver\":10,\"Gold\":20,\"Platinum\":30}}"

# 3. Generous starting credits
sudo docker exec bountybot-bot-core curl -s -X PUT \
  "http://localhost:8000/api/v1/config/guild/$GID/starting-credits/999999999"
```

### Session Cleanup (Run at End)

- `/admin_uninstall confirm:CONFIRM-DELETE` — wipes all channels, roles, and DB data for the guild
- Remember to revert XP thresholds / timers if keeping the guild active after testing

---

## Shortcut Commands Reference

Quick-reference table of admin commands used inline throughout phases:

| Command | Purpose | When to use |
|---------|---------|-------------|
| `/admin_cooldown_reset user:@target` | Clear /check cooldown | Between repeated /check tests (Phase 7) |
| `/admin_spawn_bounty tier:<Bronze\|Silver\|Gold\|Platinum>` | Spawn bounty immediately (bypasses cap/schedule) | Phase 7 instead of waiting 5 min |
| `/admin_clear_bounties confirm:CONFIRM` | Wipe all active bounties | Reset between Phase 7 iterations |
| `/admin_config_bounty action:Update expiry_minutes:10 spawn_interval:5` | Runtime bounty timer config | Phase 11 wait compression |
| `/admin_config_xp action:Update silver:10 gold:20 platinum:30` | Lower tier thresholds | Phase 6 fast progression |
| `/admin_give_item user:@target item_name:<name> item_type:Weapon quantity:1` | Inject items without shop | Phase 5 equipment setup |
| `/admin_give_ship user:@target ship_name:<name>` | Inject ship (inactive, empty loadout) | Phase 3/4 multi-ship testing |
| `/admin_remove_item`, `/admin_remove_ship` | Reverse of give | Cleanup |

### Direct API Shortcuts

```bash
# Force-fire a scheduled executor immediately (replaces waiting):
# Valid job_type values: bounty_spawn, bounty_expire, duel_expire, temperature_decay, shop_refresh, bounty_respawn, time_announcement
sudo docker exec bountybot-bot-core curl -s -X POST -H 'Content-Type: application/json' \
  "http://localhost:8000/api/v1/jobs" \
  -d '{"delay_seconds":0,"payload":{"job_type":"bounty_expire","bounty_id":<ID>,"guild_id":'$GID'}}'

# View bounty answer directly from DB (to plan /check tests):
sudo docker exec bountybot-db psql -U bounty -d bountydb -c \
  "SELECT id, division, criminal_name, answer, end_time, status FROM bounty WHERE guild_id=$GID AND status='active';"

# Reset player cooldown via DB:
sudo docker exec bountybot-db psql -U bounty -d bountydb -c \
  "UPDATE players SET bounty_cooldown_end=NULL WHERE guild_id=$GID;"
```

---

## Phase 0 — Stack Health

Verify all four services are running and connected.

- [x] **0.1** `[ADMIN]` `/ping` — Returns ephemeral "Pong! Latency is N ms" ✅ 80 ms
- [x] **0.2** `[ADMIN]` `/health` — Top-level healthy ✅; Schema subsection has field-mapping bug (A.24, not a blocker)
- [x] **0.3** Confirm no error messages in `docker compose logs` on fresh boot ✅ all 3 services clean
- [x] **0.4** Confirm health check endpoints respond directly: ✅ bot-core, gateway, blender all return `status: healthy`
   - `curl http://localhost:8000/api/v1/health` (bot-core)
   - `curl http://localhost:7999/api/v1/health` (discord-gateway)
   - `curl http://localhost:8001/api/v1/health/` (blender-service)

---

## Phase 0.5 — Pre-Registration Edge Cases

> ⚠️ **Run BEFORE Phase 1** — these tests require the alt account to NOT yet be registered (no `/profile` run yet by alt user). If the alt account is already registered, skip this phase.

- [x] **0.5.1** (Alt account, unregistered) `/bounties` — ✅ "No active bounties at this time." clean response
- [x] **0.5.2** (Alt account, unregistered) `/shop tier:Bronze` — ✅ Correct "server hasn't been set up" ephemeral (validates A.3 no-auto-create)
- [~] **0.5.3** (Alt account, unregistered) `/unregister` — ⚠️ Shows generic "An error occurred while removing the role" instead of graceful message (A.25, low-priority fix deferred)

### Help discoverability (A.5, unconfigured guild)

- [x] **0.5.4** (Alt account, unregistered) `/help` (no args) — ✅ 8 user categories, DB still 0/0/0 after (no auto-create). **Note**: admin-hint line ("Admins: use /admin_help…") visible to non-admin Alt — verify on Main comparison in Phase 2.5 whether intentional
- [x] **0.5.5** (Alt account, unregistered) `/help category:Bounty Hunting` — ✅ `/bounties`, `/check`, `/criminal-loadout`, `/route` with correct params
- [x] **0.5.6** (Alt account, unregistered) `/help category:bounty hunting` (lowercase) — ✅ Case-insensitive confirmed, identical response
- [x] **0.5.7** (Alt account, unregistered) `/help category:nonsense` — ✅ Lists all 8 valid categories, no DB writes
- [x] **0.5.8** (Alt account, unregistered) `/help ` autocomplete — ✅ 8 user categories, no admin ones

---

## Phase 1 — Guild Setup, Channel Infrastructure & Player Registration

### First-time guild initialisation

- [x] **1.1** `[ADMIN]` `/admin_setup` — "Guild initialized" response; creates: *(Re-verified 2026-04-22 PM post-wipe/rebuild: all 8 channels + 6 roles + category + GuildConfig + 4 tier shops (12/11/10/10 = 43 items) created correctly. A.30 still observed — gateway channel list returns `category_id: null` despite DB category_id populated. A.10 doc-drift still unaddressed.)*
   - "BountyBot" category with @everyone view denied
   - `#bronze-bounty-board` channel (read-only for players)
   - `#silver-bounty-board` channel (read-only for players)
   - `#gold-bounty-board` channel (read-only for players)
   - `#platinum-bounties` channel (read-only for players) — **added per A.10**
   - `#shop` channel (read-only for players)
   - `#bounty-hunting` channel (interactive — slash commands allowed)
   - `#bounty-discussions` channel (chat only — slash commands disabled)
   - `#bot-images` channel (hidden from all users, bot-only)
   - `@Bounty Hunter` role (mentionable)
   - `@Bounty Hunter Bronze` role — **added per A.10**
   - `@Bounty Hunter Silver` role — **added per A.10**
   - `@Bounty Hunter Gold` role — **added per A.10**
   - `@Bounty Hunter Platinum` role — **added per A.10**
   - "BountyBot Admin" role (if no admin_role provided; reuses pre-existing when present) — **naming corrected per A.10** ("BountyBot Admins" → "BountyBot Admin")
   - GuildConfig + 4 tier shops in database (verified 2026-04-21: Bronze=12, Silver=13, Gold=12, Platinum=11 shop items = 48 total)
   - Confirmation embed lists all channel links and role mentions
   - Audit log recorded `guild_initialize` success

> 💡 **Immediately after this succeeds, run the Session Setup golden-config commands from the top of this document.**

### Verify channel infrastructure

- [x] **1.2** Confirm all 7 text channels exist under the "BountyBot" category in Discord ✅ Re-verified 2026-04-22 PM (visual confirm; gateway API still reports `category_id: null` per A.30 but DB category_id is populated)
- [x] **1.3** Confirm `@Bounty Hunter` role exists in the guild role list and is mentionable ✅ Re-verified 2026-04-22 PM (plus 4 tier roles — Bronze/Silver/Gold/Platinum — all mentionable per roles API)
- [~] **1.4** Verify channel permissions: *(Partially verified 2026-04-22 PM via Alt unregister/re-register cycle)*
   - Users WITHOUT `@Bounty Hunter` role CANNOT see any BountyBot channels ✅ Alt's unregister showed channel visibility loss; re-register restored
   - `#bronze-bounty-board`, `#silver-bounty-board`, `#gold-bounty-board`, `#shop` are read-only for `@Bounty Hunter` (cannot type in them) — NOT YET VERIFIED (visual check deferred)
   - `#bounty-hunting` allows `@Bounty Hunter` to use slash commands — ✅ implicitly verified (Alt ran multiple slash commands without being blocked)
   - `#bounty-discussions` allows `@Bounty Hunter` to chat but NOT use slash commands — NOT YET VERIFIED (visual check deferred)
   - `#bot-images` is invisible to all users (only bot can see it) — NOT YET VERIFIED (Main sees it as Admin; Alt's view pending)

### Verify config

- [x] **1.5** `[ADMIN]` `/admin_config action:View Config` — ✅ Re-verified 2026-04-22 PM: shows credits=999,999,999, sale_factor=80%, XP thresholds=Silver:10/Gold:20/Platinum:30, admin role ✅, configured ✅. Matches Session Setup exactly. (Note: does NOT include channel IDs in the embed — only role + numeric config. Documentation drift.)
- [x] **1.6** `[ADMIN]` `/admin_config_validate` — ✅ Re-verified 2026-04-22 PM: 0 errors, 0 warnings. **Observation**: embed shows `guild bb-temp` as display name — possible placeholder text where actual guild name should render.

### Player registration (your account)

- [x] **1.7** `/profile` — ✅ Re-verified 2026-04-22 PM: Main (discord_id=402296276617527306, username=samx.ai) created as PID 1. Betty active ship with weapons=["Nirai Impulse EX 1"], modules=["E2 Exoclad","Telta Quickscan"], turrets=[], secondary_weapons=NULL. Inventory: Micro Gun MK I qty=1. Bronze/0 XP/999,999,999 credits. **Observation**: `secondary_weapons` field is NULL not [] — may matter for Phase 5 equip logic; monitor.
- [x] **1.8** Verify BountyBot channels are now visible to you (you have `@Bounty Hunter` role) ✅ Re-verified 2026-04-22 PM (Main + Alt both see all 7 player-visible channels; #bot-images Admin-only via Main client)
- [x] **1.9** `/profile` — Second use: identical response, no duplicate player created (idempotent) ✅ Re-verified 2026-04-22 PM: same PID 1, row counts stable at 1/1/1/1

### Unregister / re-register cycle

- [x] **1.10** `/unregister` — ✅ Re-verified 2026-04-22 PM: "Bounty Hunter role(s) removed: @Bounty Hunter, @Bounty Hunter Bronze. Your player data is preserved." Both roles stripped; DB counts preserved at 1/1/1/1; Main had only @everyone after.
- [x] **1.11** `/profile` — ✅ Re-verified 2026-04-22 PM: re-register restores same PID 1, both tier+hunter roles restored; no new DB rows.

### Unregister edge cases

- [x] **1.12** `/unregister` when you don't have the role — ✅ Re-verified 2026-04-22 PM: "ℹ️ You don't have the Bounty Hunter role." — graceful, informational tone. **Important clarification**: registered-then-unregistered user takes this no-role path cleanly, whereas **never-registered** user still hits generic error (A.25). A.25 scope narrower than originally thought.

### `[2P]` Second player registration

- [x] **1.13** `[2P]` Player 2 runs `/profile` — ✅ Re-verified 2026-04-22 PM: Alt (discord_id=970691862035841048, username=general_failure.) created as PID 2 with own Betty (PID2 ship row), own Micro Gun MK I in inventory, own credits/XP; both tier+hunter roles assigned to Alt. Counts stable at 2/2/2/2.

### `/register` alias (A.19 closure verification — NEW 2026-04-22)

- [x] **1.14** `/register` — ✅ Re-verified 2026-04-22 PM: Alt ran `/register` immediately after `/profile`; returned identical embed (PID 2, Bronze, 0 XP, 999,999,999 credits); zero DB mutations (counts stable at 2/2/2/2); no role change. **A.19 closed.**

### A.9 platinum validator closure verification (NEW 2026-04-22)

- [x] **1.15** Session Setup platinum payload — AFTER `/admin_setup` completes, orchestrator runs: `PUT /api/v1/config/guild/{gid}/bounty` with `max_bounties_per_tier={"bronze":20,"silver":20,"gold":20,"platinum":20}`. Expected: HTTP 200 + response includes all 4 tier entries (previously returned 400 with "Must be bronze, silver, or gold" — see A.9). ✅ Re-verified 2026-04-22 PM post-wipe: all 3 Session Setup endpoints returned 200 with platinum included. **A.9 closed.**

> Note: `/profile` takes no parameters — it always shows the invoking user's profile. `/register` is a full alias of `/profile` — identical behavior, interchangeable. There is no way to view another player's profile via either. Use `/admin_player user:@player action:View Stats` (admin) to inspect other players.

---

## Phase 1.5 — Non-Admin Permission Denials

> ⚠️ **Run AFTER Phase 1** — the alt account must be registered (has `@Bounty Hunter` role) but must NOT have admin permissions.

- [ ] **1.5.1** (Non-admin) `/admin_setup` — Command should be invisible in slash menu (A.4 hiding) *(Reset 2026-04-22 post-wipe; Alt needs to be re-registered in Phase 1 first)*
- [ ] **1.5.2** (Non-admin) `/admin_player user:@someone action:View Stats` — Invisible *(Reset 2026-04-22 post-wipe)*
- [ ] **1.5.3** (Non-admin) `/admin_config action:View Config` — Invisible *(Reset 2026-04-22 post-wipe)*
- [ ] **1.5.4** (Non-admin) `/scheduler_list` — Invisible *(Reset 2026-04-22 post-wipe)*
- [ ] **1.5.5** (Non-admin) `/health` — Invisible *(Reset 2026-04-22 post-wipe)*
- [~] **1.5.6** (Non-admin) `/ping` — Invisible (note: A.20 — was previously visible; verify fix still working) *(Reset 2026-04-22 post-wipe; A.20 still open — /ping visible to non-admins; runtime guard rejects but visibility leak remains)*
- [ ] **1.5.7** (Non-admin) `/load_data category:ship` — Invisible *(Reset 2026-04-22 post-wipe)*
- [ ] **1.5.8** (Non-admin) `/admin_help` — Invisible (validates A.4 decorator on `/admin_help`) *(Reset 2026-04-22 post-wipe)*
- [ ] **1.5.9** (Non-admin) Open the slash-command menu and start typing `/admin` — autocomplete does NOT surface any `admin_*`, `scheduler_*`, `ping`, `health`, `load_data`, `reload_autocomplete`, `render_config`, or `render_cache_clear` entries. `/admin_help` itself must also be absent. *(Reset 2026-04-22 post-wipe)*
- [ ] **1.5.10** (Non-admin) `/help` — Still works and still shows only the 8 user categories (no Admin — * categories leak) *(Reset 2026-04-22 post-wipe)*

---

## Phase 2 — Game Data Browsing

Verify seeded data is accessible via the aboutCog. All of this is read-only.

### Object lookup

- [x] **2.1** `/about category:ship name:Betty` — Returns detailed ship embed (armour, cargo, compatible skins, manufacturer, tech level) ✅ PASS 2026-04-21 (ran on Alt)
- [x] **2.2** `/about category:primary_weapon name:Micro Gun MK I` — Returns weapon stats ✅ PASS 2026-04-21
- [x] **2.3** `/about category:module name:Telta Quickscan` — Returns module stats ✅ PASS 2026-04-21
- [x] **2.4** `/about category:ship name:nonexistent_ship` — Error: not found ✅ PASS 2026-04-21 (ephemeral error returned — non-public; no INFO success logged)

### Category listing

- [x] **2.5** `/list_category category:ship` — Paginated list of all seeded ships (capped at 100) ✅ PASS 2026-04-22 (A.26/A.27 closure: 65 ships, two continuation-field spacers, single clean list — surfaced A.32 `mpzzzm` emoji alias gap on one module, tracked separately)
- [x] **2.6** `/list_category category:primary_weapon` — Lists primary weapons (40 items) ✅ PASS 2026-04-22 (40 items, one continuation-field spacer)
- [x] **2.7** `/list_category category:secondary_weapon` — Lists secondary weapons (30 items; feature not usable per A.2 but data browsing works) ✅ PASS 2026-04-22 (30 items, one continuation-field spacer)
- [x] **2.8** `/list_category category:turret_weapon` — Lists turret weapons (10 items, single field) ✅ PASS 2026-04-22 (10 items, single field — no spacer needed)
- [x] **2.9** `/list_category category:module` — Lists all 66 modules ✅ PASS 2026-04-22 (66 items, three continuation-field spacers — A.32 `mpzzzm` emoji gap noted)
- [x] **2.10** `/list_category category:criminal` — Lists 25 NPC criminals ✅ PASS 2026-04-22 (25 items; no emoji prefix — criminals don't carry emoji metadata, which is expected)
- [x] **2.11** `/list_category category:system` — Lists all 34 star systems ✅ PASS 2026-04-22 (34 items; no emoji prefix — systems don't carry emoji metadata, expected)
- [~] **2.12** `/list_category category:module tech_level:2` — Filtered by tech level ❌ FAIL 2026-04-22 → **A.31 logged** (preload endpoint omits `tech_level` field, client-side filter always empty; affects all 5 categories that support tech_level filtering). Test coverage hid the bug (mocks included `tech_level`; real API doesn't).

### Route planning

- [x] **2.13** `/make-route start:Wolf-Reiser end:Pan` — Returns numbered route with hop count and route map image attachment ✅ PASS 2026-04-21 (6 hops, map rendered)
- [x] **2.14** `/make-route start:Pan end:Pan` — Edge case: same start/end system ✅ PASS 2026-04-21 (0 hops; description "1. Pan"; footer "Shortest path via A* (1 system(s))"; map with single node rendered — not an error, but consider early-return for same-system case as future UX polish)

---

## Phase 2.5 — Help Command Discoverability `[ADMIN]`

Validates the `/help` and `/admin_help` commands end-to-end as a discoverability surface (A.5) and confirms admin-only scoping (A.4) of the admin variant. Phase 0.5 already covered `/help` on an unconfigured guild; this phase covers the admin side and mid-session re-checks now that the guild IS configured.

### `/help` (user-facing, admin account)

- [x] **2.5.1** `/help` (no args) — Same 8-category overview embed as 0.5.4. Confirm no admin categories appear even when invoked by an admin. ✅ PASS 2026-04-21 (exact match; admin hint footer present but not a leak per earlier decision)
- [x] **2.5.2** `/help category:Player Profile` — Detail embed lists `/profile`, `/leaderboard`, `/prestige` with their parameter names + descriptions. ✅ PASS 2026-04-21
- [x] **2.5.3** `/help category:Shop & Economy` — Lists `/shop`, `/buy`, `/sell`, `/shops`. ✅ PASS 2026-04-21
- [x] **2.5.4** `/help category:Inventory & Equipment` — Lists `/inventory`, `/search`, `/item`, `/equip`, `/unequip`, `/give`. ✅ PASS 2026-04-21
- [x] **2.5.5** `/help category:Ships` — Lists `/ships`, `/ship`, `/setactive`, `/nickname`. ✅ PASS 2026-04-21
- [x] **2.5.6** `/help category:Dueling` — Lists `/duel-challenge`, `/duel-accept`, `/duel-reject`. ✅ PASS 2026-04-21
- [x] **2.5.7** `/help category:Game Data` — Lists `/about`, `/list_category`, `/make-route`. ✅ PASS 2026-04-21
- [x] **2.5.8** `/help category:Skins & Rendering` — Lists `/ship_skin`, `/render_skin`, `/make_skin_texture`. ✅ PASS 2026-04-21
- [x] **2.5.9** `/help category:SHIPS` (uppercase) — Case-insensitive match, same as 2.5.5. ✅ PASS 2026-04-21 (when invoked with exactly "SHIPS", resolves correctly; first attempt submitted literal "SHIPS (uppercase)" from Discord autocomplete label — not a bug, just a UI artifact)

### `/admin_help` (admin-only, admin account)

- [x] **2.5.10** `/admin_help` (no args) — Admin-only ephemeral embed. Lists all 9 admin categories with descriptions and command counts: ✅ PASS 2026-04-21 (9 categories present; 2 command-count deltas vs checklist — Config shows 4 not 3, Bounties shows 5 not 4; checklist doc gap — recommend reconciliation during post-E2E cleanup alongside A.10)
   - **Admin — Setup** (3): `/admin_setup`, `/admin_uninstall`, `/admin_check` ✓ matches
   - **Admin — Players** (5): `/admin_player`, `/admin_give_item`, `/admin_give_ship`, `/admin_remove_item`, `/admin_remove_ship` ✓ matches
   - **Admin — Config** (3 predicted → actual **4**): `/admin_config`, `/admin_config_shop`, `/admin_config_validate` + 1 more (needs verification via `/admin_help category:Admin — Config`)
   - **Admin — Bounties** (4 predicted → actual **5**): `/admin_config_bounty`, `/admin_spawn_bounty`, `/admin_clear_bounties`, `/admin_cooldown_reset` + 1 more (possibly `/admin_refresh_shop`)
   - **Admin — Stats** (1): `/admin_guild_stats` ✓ matches
   - **Admin — Render** (2): `/render_config`, `/render_cache_clear` ✓ matches
   - **Admin — Health** (2): `/ping`, `/health` ✓ matches
   - **Admin — Dev Tools** (2): `/load_data`, `/reload_autocomplete` ✓ matches
   - **Admin — Scheduler** (6): `/scheduler_list`, `/scheduler_view`, `/scheduler_update`, `/scheduler_delete`, `/admin_reset_scheduler`, `/admin_clear_scheduler` ✓ matches
- [x] **2.5.11** `/admin_help category:Admin — Setup` — Detail embed lists the 3 setup commands with their parameters + descriptions. ✅ PASS 2026-04-21 (3 commands: `/admin_check`, `/admin_setup`, `/admin_uninstall`)
- [x] **2.5.12** `/admin_help category:Admin — Scheduler` — Detail embed lists all 6 scheduler commands. **This is the key regression test** — earlier polish pass added the Scheduler mapping. ✅ PASS 2026-04-21 (all 6 commands: `/admin_clear_scheduler`, `/admin_reset_scheduler`, `/scheduler_delete`, `/scheduler_list`, `/scheduler_update`, `/scheduler_view`)
- [x] **2.5.13** `/admin_help category:admin — scheduler` (lowercase) — Case-insensitive match, same as 2.5.12. ✅ PASS 2026-04-21 (identical to 2.5.12)
- [x] **2.5.14** `/admin_help category:Scheduler` (short form, no "Admin — " prefix) — Behaviour: either resolves (if substring match is supported) OR returns the "Unknown category" error listing the 9 valid names. **Record which** — it documents the autocomplete/resolution contract. ✅ PASS 2026-04-21. **Contract: short form does NOT resolve; error message lists all 9 valid "Admin — *" full-form names.** This is a clean exact-match contract — no fuzzy/substring ambiguity.
- [x] **2.5.15** `/admin_help category:nonsense` — Ephemeral "Unknown category…" error listing all 9 admin categories. ✅ PASS 2026-04-21
- [x] **2.5.16** Open slash-menu autocomplete while typing `/admin_help ` — dropdown surfaces all 9 admin categories (not user categories). ✅ PASS 2026-04-21

### Cross-cutting

- [x] **2.5.17** Confirm `/help` + `/admin_help` produce zero DB mutations during this phase:
   ```bash
   sudo docker exec bountybot-db psql -U bounty -d bountydb -c \
     "SELECT COUNT(*) AS audit_rows FROM admin_audit_logs WHERE action LIKE '%help%';"
   ```
   Expect 0 — help commands are pure-read, no audit trail. ✅ PASS 2026-04-21 (0 help-related audit rows; note table name is `admin_audit_logs` plural, not singular as originally written)

---

## Phase 3 — Ship Management

### View your ships

- [x] **3.1** `/ships` — ✅ Re-verified 2026-04-22 PM on Alt: Betty shown with 🟢 ACTIVE indicator, Ship ID 2, W:1/M:2/T:0 loadout summary. **A.28 re-verified closed.**
- [~] **3.2** `/ship ship_id:<your_betty_id>` — ✅ Re-verified 2026-04-22 PM on Alt (ship_id:2): shows Nirai Impulse EX 1 in Weapons, E2 Exoclad + Telta Quickscan in Modules. **A.28 closed.** **A.34a/b/c remain OPEN** — plus new **B.3** observation: embed shows `Type: Betty` (should be `Class:` or omitted since title already shows ship name).
- [~] **3.3** `/nickname ship_id:<your_ship_id> nickname:MyBetty` — Sets custom nickname; visible in `/ships` *(Reset 2026-04-22 post-wipe; A.34c remains OPEN — same helper as A.34a)*
- [ ] **3.4** `/nickname ship_id:<your_ship_id> nickname:<51+ char string>` — Error: nickname too long *(Reset 2026-04-22 post-wipe)*

### Set active ship (tested after buying a second ship in Phase 4)

- [ ] **3.5** *(Deferred to after Phase 4)* `/setactive ship_id:<second_ship_id>` — Sets new ship as active
- [ ] **3.6** `/ships` — Confirm new active ship indicator

---

## Phase 4 — Shop System

### Browse all shops

- [ ] **4.1** `/shops` — Overview of all 4 tier shops (Bronze/Silver/Gold/Platinum) with item counts and lock/unlock status based on player tier
- [x] **4.2** `/shop tier:Bronze` — ✅ Re-verified 2026-04-22 PM on Alt: 14 items shown (5 ships / 3 primary / 2 turret / 4 modules); prices and quantities correct. Turrets confirmed present at Bronze (PE Ambipolar-5, Matador TS). **A.38 closed** — zero secondary_weapon entries across all shop tiers verified via 5 shop_refresh cycles. **B.6 observation**: success embed says `Item Type: Primary_Weapon` (capitalized + underscore leaking from DB concrete type — cosmetic only).
- [x] **4.3** `/shop tier:Silver` — ✅ Re-verified 2026-04-22 PM: "🔒 You need to be Silver tier to access this shop. Your current tier: Bronze" — tier gate works correctly.
- [x] **4.4** `/shop tier:Gold` (and Platinum) — ✅ Re-verified 2026-04-22 PM: both higher-tier shops correctly return tier-gate errors for Bronze player. 4-tier gating (including new Platinum) fully working.

### Purchase items

- [x] **4.5** `/buy item_id:<affordable_item_id> quantity:1` — ✅ Re-verified 2026-04-22 PM on Alt: bought Mimung Blaster (item_id=224) for 369,763 credits; success embed shows new credits 999,630,236; inventory row added as concrete `primary_weapon`.
- [ ] **4.6** `/profile` — Verify credits decreased by item price *(implied via /buy success message; explicit /profile recheck pending)*
- [ ] **4.7** `/inventory` — Verify purchased item appears

### Purchase error cases

- [ ] **4.8** `/buy item_id:<expensive_item_id>` — Attempt to buy something you can't afford; error: "insufficient credits"
- [ ] **4.9** `/buy item_id:999999` — Nonexistent item; error: not found

### Sell items

- [~] **4.10** `/sell item_name:<owned_item> quantity:1` — ❌ 2026-04-22 PM: Alt ran `/sell item:"Micro Gun MK I"` and received raw `API Error: Client error '422 Unprocessable Entity'`. Root cause: **A.42** — `shopCog._SELL_TYPE_MAP` downgraded concrete `primary_weapon` to generic alias `"weapon"` which the A.36-bundle router correctly rejects at write paths. Fix bundle committed 2026-04-22 PM: `item_type` and `target_tier` parameters removed from `/sell`; server resolves concrete type from inventory row by name; sells always land in player's current tier shop. Re-test after rebuild.
- [ ] **4.11** `/sell item_name:<item_you_dont_own>` — Error: item not in inventory

### Now complete Phase 3 ship management

- [ ] **4.12** *(If a second ship was purchased)* Return to **3.5** and **3.6** to test `/setactive`

---

## Phase 5 — Inventory & Equipment

### View inventory

- [x] **5.1** `/inventory` — ✅ Re-verified 2026-04-22 PM on Alt: shows "Primary_Weapons (1) • Micro Gun MK I". **Important caveat**: summary line showed "Ships: 0 | Weapons: 0 | Modules: 0 | Turrets: 0" despite 1 primary_weapon — this was **D1 latent defect exposed by A.36** (`inventory_repository.get_inventory_summary()` initialized aggregation dict with generic alias keys). Fix committed 2026-04-22 PM as part of A.42 bundle. Re-test after rebuild.
- [x] **5.2** `/inventory item_type:Weapon` — ✅ Re-verified 2026-04-22 PM on Alt: Choice dropdown worked (`Weapon` option selected), backend filtered correctly, returned "Primary_Weapons (1) • Micro Gun MK I". **A.33/A.35 closed.** **A.36 (read path) closed.** Empty filter case also verified via `/inventory item_type:Module` which gracefully returned "📭 No items found in General_Failure's inventory (module)."
- [x] **5.3** `/search query:Micro` — ✅ Re-verified 2026-04-22 PM on Alt: "Found 1 matching items / Primary_Weapons / • Micro Gun MK I"
- [ ] **5.4** `/search query:nonexistent` — No results message
- [~] **5.5** `/item item_name:Micro Gun MK I item_type:Weapon` — ✅ Re-verified 2026-04-22 PM on Alt: "📦 Micro Gun MK I / Type: Weapon / Quantity Owned: 1 / Status: ✅ Owned". **A.36 closed.** **But A.39 surfaced**: mismatched `item_type` (e.g. specifying Ship for a weapon) returns "Not Owned" rather than helpful cross-type error. User deferred A.39 to post-release (remove `item_type` param + use item emoji in title).

### Equip weapons/modules

- [x] **5.6** `/equip item_name:<weapon_name>` (no equipment_type param) — ✅ Re-verified 2026-04-22 PM on Alt: `/equip item_name:"Micro Gun MK I"` triggered swap flow (1/1 slot full); swapped Nirai Impulse out for Micro Gun. Server-side Item-STI resolution routed to correct `weapons` slot. **A.37 closed.** Bonus: swap-flow UI works cleanly. **B.4 observation**: swap-confirmation dropdown lacks clear "select to swap" affordance (minor).
- [ ] **5.7** `/ship ship_id:<active_ship_id>` — Confirm weapon appears in loadout
- [ ] **5.8** `/equip item_name:<module_name>` (no equipment_type param) — Equip a module to active ship
- [ ] **5.9** `/equip item_name:<turret_name>` (no equipment_type param) — Equip a turret (if ship has turret slots)

### Unequip

- [x] **5.10** `/unequip item_name:<weapon_name>` (no equipment_type param) — ✅ Re-verified 2026-04-22 PM on Alt: after swap, `/unequip item_name:"Micro Gun MK I"` worked; equipped-item autocomplete showed the currently-equipped item only. **A.37 closed.** Restored original starter loadout (Nirai Impulse EX 1 re-equipped via `/equip`).
- [ ] **5.11** `/ship ship_id:<active_ship_id>` — Confirm weapon no longer in loadout

### Equipment error cases

- [ ] **5.12** `/equip item_name:<weapon> equipment_type:Weapon` repeatedly until primary slots full — Error: slot limit exceeded
- [ ] **5.13** `/unequip item_name:<item_not_equipped> equipment_type:Weapon` — Error: item not equipped
- [ ] **5.14** `/equip item_name:<item_not_in_inventory> equipment_type:Weapon` — Error: item not found in inventory

### Admin inventory view

- [ ] **5.15** `[ADMIN]` `/inventory user:@player2` — Admin can view another player's inventory

---

## Phase 6 — Player Progression & Tiers `[ADMIN]`

Use admin commands to fast-track progression testing.

### XP and tier advancement

Tier thresholds (lowered for fast testing via Session Setup): Bronze (0 XP) → Silver (10 XP) → Gold (20 XP) → Platinum (30 XP)

> ⚡ **Shortcut**: If you didn't run the Session Setup commands yet, run `/admin_config_xp action:Update silver:10 gold:20 platinum:30` now to lower thresholds before testing tier advancement.

- [ ] **6.1** `[ADMIN]` `/admin_player user:@you action:Set XP xp:15` — Sets XP; confirmation embed shows old/new XP and tier change (Bronze -> Silver)
- [ ] **6.2** `/profile` — XP shows 15; tier shows Silver
- [ ] **6.3** `/shop tier:Silver` — Silver-tier items now accessible
- [ ] **6.4** `/shop tier:Gold` — Gold-tier items still locked (player is Silver)

### Credits management

- [ ] **6.5** `[ADMIN]` `/admin_player user:@you action:Add Credits credits:50000` — Adds credits; shows amount added + new total
- [ ] **6.6** `/profile` — Credits reflect the addition
- [ ] **6.7** `[ADMIN]` `/admin_player user:@you action:Set Credits credits:1000` — Sets credits to exact value
- [ ] **6.8** `/profile` — Credits show exactly 1,000

### Admin player inspection

- [ ] **6.9** `[ADMIN]` `/admin_player user:@player2 action:View Stats` — Shows Player 2's full stats (tier, XP, credits, lifetime credits, prestige count)
- [ ] **6.10** `[ADMIN]` `/admin_player user:@you action:Reset Player` — Resets player to defaults (XP, credits, stats zeroed; ships preserved)
- [ ] **6.11** `/profile` — Confirms reset state

### Prestige

- [ ] **6.12** `[ADMIN]` `/admin_player user:@you action:Set XP xp:35` — Max out XP to reach Platinum (above threshold of 30)

> ⚡ **Shortcut**: Use `/admin_config_xp action:Update platinum:30` (already done in Session Setup) so XP 35 exceeds the Platinum threshold trivially, making prestige reachable without grinding.

- [ ] **6.13** `/prestige` (without confirm) — Should show orange warning embed explaining what prestige does (reset to Bronze, keep ships/credits, gain prestige star)
- [ ] **6.14** `/prestige confirm:CONFIRM` — Resets level to 0, tier to Bronze, clears inventory; increments prestige_count; preserves ships and lifetime stats
- [ ] **6.15** `/profile` — Shows prestige count, reset XP/tier, preserved lifetime stats

### Leaderboard

- [ ] **6.16** `/leaderboard` — Shows top 10 players ranked by XP with tier/credits and rank emojis
- [ ] **6.17** `/leaderboard tier:Silver` — Filtered to Silver-tier players only

---

## Phase 7 — Bounty Hunting (Core Gameplay)

> Note: Bounties spawn via APScheduler (`bounty_spawn_default` job). Session Setup compressed the interval to 5 minutes.
> Use `/admin_spawn_bounty` to spawn immediately rather than waiting.

### Wait for bounty spawn

- [ ] **7.1** `/bounties` — Lists active bounties (may be empty if scheduler hasn't fired yet)
- [ ] **7.2** `/bounties division:bronze` — Filter bounties by division
- [ ] **7.3** `[WAIT]` Wait for `bounty_spawn_default` to fire — `/bounties` now shows active bounty with criminal name, division, reward, reward_per_sys, systems checked count, time remaining, and bounty ID

> ⚡ **Shortcut**: Skip waiting. Use `/admin_spawn_bounty tier:Bronze` to spawn immediately. Then query the DB to get the answer for testing /check:
> ```
> sudo docker exec bountybot-db psql -U bounty -d bountydb -c "SELECT id, criminal_name, answer FROM bounty WHERE guild_id=$GID AND status='active';"
> ```

### Verify bounty announcement (redesign feature)

- [ ] **7.4** Check the correct per-division bounty board channel (e.g. `#bronze-bounty-board` for a bronze bounty). Verify:
   - Rich embed with faction-specific color (Terran=gold, Vossk=teal, Midorian=dark red, Nivelian=blue)
   - Title: criminal name
   - Fields: Difficulty (T-level), Reward Pool, Bounty Ends (relative timestamp), Loadout (ship + weapons + modules), Route (system names), Checked Systems ("No systems checked yet")
   - Footer: faction name
   - `@Bounty Hunter` role mention above the embed
- [ ] **7.5** Verify route map image is embedded in the announcement (if route map generation succeeded)

### Investigate a bounty

- [ ] **7.6** `/route bounty:<bounty_id>` — Shows the bounty's system route with checked/unchecked indicators
- [ ] **7.7** `/criminal-loadout bounty:<bounty_id>` — Shows criminal's ship, weapons, modules

### Hunt the bounty

- [ ] **7.8** `/check system:<wrong_system>` — "System Checked" response (incorrect). **Also verify**: the announcement embed in the bounty board channel live-edits to show the checked system with ~~strikethrough~~
- [ ] **7.9** `/check system:<same_wrong_system>` — "Already checked" response (same system can't be checked twice)
- [ ] **7.10** `/check system:<correct_system>` — "Bounty Found!" response (correct answer = last system in route); reward credits + XP granted. **Also verify**: the announcement embed edits one final time showing the found system in **bold**
- [ ] **7.11** `/profile` — Credits and XP increased by bounty reward
- [ ] **7.12** `/bounties` — Bounty no longer listed (resolved)
- [ ] **7.13** Verify the announcement message has been **deleted** from the bounty board channel

### Bounty edge cases

- [ ] **7.14** `/check system:<any_system>` with no active bounties — Error: "No active bounty" or "No Bounty"
- [ ] **7.15** `/check system:<system>` immediately after a check — Cooldown message (per-player cooldown, default 180 seconds)

> ⚡ **Shortcut**: After testing cooldown, run `/admin_cooldown_reset user:@you` to continue without waiting.

### `[2P]` Bounty competition

- [ ] **7.16** `[2P]` `[WAIT]` Both players race to `/check system:<correct>` on same bounty — Only the first correct answer wins
- [ ] **7.17** Both players `/profile` — Only the winner received credits/XP; non-winner contributors may receive partial reward (reward_per_sys * systems they checked)

---

## Phase 8 — Dueling `[2P]`

All duel tests require a second registered player.

### Challenge and accept

- [ ] **8.1** `[2P]` `/duel-challenge target:@player2 stakes:100` — Duel challenge sent; embed shows both players' mentions, stakes, duel ID, and "Challenge expires in 24 hours"
- [ ] **8.2** `[2P]` Player 2 runs `/duel-accept duel:<duel_id>` (autocomplete dropdown) — Combat resolves; winner/loser announced with damage breakdown
- [ ] **8.3** Both players `/profile` — Winner gained credits (stakes); loser lost credits (stakes). Both gained/lost XP
- [ ] **8.4** Check for stalemate: If both ships have similar stats, result may be "Stalemate!" (yellow embed, no credit transfer)

### Challenge and decline

- [ ] **8.5** `[2P]` `/duel-challenge target:@player2 stakes:50` — New challenge sent
- [ ] **8.6** `[2P]` Player 2 runs `/duel-reject duel:<duel_id>` — Challenge cancelled, no rewards or penalties

### Duel edge cases

- [ ] **8.7** `/duel-challenge target:@yourself stakes:0` — Error: can't duel yourself
- [ ] **8.8** `[2P]` `/duel-challenge target:@player2 stakes:0` — Zero-stakes friendly duel (should work)
- [ ] **8.9** `[2P]` `/duel-challenge target:@player2 stakes:999999` — Stakes exceed one player's credits; error: insufficient credits
- [ ] **8.10** `[2P]` Send challenge, then send another while first is pending — Error: already have pending duel (if enforced)
- [ ] **8.11** `[2P]` `[WAIT]` Send challenge, do NOT accept/decline, wait for expiry (~24 hours) — Challenge auto-expires via duel_expire_executor

> ⚡ **Shortcut**: Fire duel_expire executor immediately:
> ```
> sudo docker exec bountybot-bot-core curl -s -X POST -H 'Content-Type: application/json' \
>   "http://localhost:8000/api/v1/jobs" \
>   -d '{"delay_seconds":0,"payload":{"job_type":"duel_expire"}}'
> ```

---

## Phase 9 — Scheduler Administration `[ADMIN]`

### View jobs

- [ ] **9.1** `[ADMIN]` `/scheduler_list` — Lists all APScheduler jobs with type, trigger, and next run time (bounty_spawn_default, temperature_decay_default, shop_refresh_default). Handles 503 if scheduler still starting.
- [ ] **9.2** `[ADMIN]` `/scheduler_view job_id:bounty_spawn_default` — Shows full job details including payload JSON

### Update jobs

- [ ] **9.3** `[ADMIN]` `/scheduler_update job_id:bounty_spawn_default payload_json:{"job_type": "bounty_spawn"}` — Updates job payload; confirmation message
- [ ] **9.4** `[ADMIN]` `/scheduler_update job_id:bounty_spawn_default payload_json:invalid` — Error: invalid JSON

### Delete jobs (careful!)

- [ ] **9.5** `[ADMIN]` `/scheduler_view job_id:nonexistent_job` — Error: job not found
- [ ] **9.6** `[ADMIN]` `/scheduler_delete job_id:<test_job_id>` — Only test with a job you can recreate

---

## Phase 10 — Dev Tools `[ADMIN]`

- [ ] **10.1** `[ADMIN]` `/load_data category:ship` — Re-seeds ship data from JSON files; shows file count
- [ ] **10.2** `[ADMIN]` `/load_data category:All` — Re-seeds all categories; summarizes results with error counts
- [ ] **10.3** `[ADMIN]` `/reload_autocomplete` — Force-reloads cached autocomplete data for AboutCog, DevCog, SkinsCog

---

## Phase 11 — Scheduled Jobs & Announcements `[WAIT]`

These require patience or admin force-triggers.

| # | Job | How to Test | Expected |
|---|-----|-------------|----------|
| **11.1** | Bounty auto-spawn | Wait for spawn interval (5 min with Session Setup compression), run `/bounties` | New bounty appears; **announcement posted to correct division channel** (`#bronze-bounty-board` / `#silver-bounty-board` / `#gold-bounty-board`) with rich embed and `@Bounty Hunter` mention |
| **11.2** | Shop auto-refresh | Wait 6 hours or `[ADMIN]` `/admin_refresh_shop tier:Bronze` | Shop items rotated; **announcement posted to `#shop` channel** with `@Bounty Hunter` role mention |
| **11.3** | Temperature decay | Spawn + resolve multiple bounties in one system, then wait for hourly decay job | System temperature cools over time |
| **11.4** | `[2P]` Duel expiry | Send `/duel-challenge`, don't respond, wait for `DUEL_PENDING_DURATION` (~24 hours) | Challenge auto-expires |
| **11.5** | Bounty expiry | Wait for bounty to exist past `end_time` without being solved | Bounty auto-expires; **announcement message deleted from bounty board channel** |
| **11.6** | Bounty escape + respawn | Find correct system but lose combat (weak ship vs strong criminal) — criminal escapes | Announcement deleted; criminal respawns with new route after delay (`len(route)` minutes) |

> ⚡ **Shortcut for 11.1**: Use `/admin_spawn_bounty tier:Bronze` — no waiting needed.

> ⚡ **Shortcut for 11.2**: Use `[ADMIN]` `/admin_refresh_shop tier:Bronze` — no waiting needed.

> ⚡ **Shortcut for 11.4**: Fire duel_expire executor immediately:
> ```
> sudo docker exec bountybot-bot-core curl -s -X POST -H 'Content-Type: application/json' \
>   "http://localhost:8000/api/v1/jobs" \
>   -d '{"delay_seconds":0,"payload":{"job_type":"duel_expire"}}'
> ```

> ⚡ **Shortcut for 11.5**: Session Setup set `bounty_expiry_minutes:10`. Alternatively force-fire bounty_expire:
> ```
> sudo docker exec bountybot-bot-core curl -s -X POST -H 'Content-Type: application/json' \
>   "http://localhost:8000/api/v1/jobs" \
>   -d '{"delay_seconds":0,"payload":{"job_type":"bounty_expire","bounty_id":<ID>,"guild_id":'$GID'}}'
> ```

---

## Phase 11.5 — Invalid Input Edge Cases

These can be tested at any point after Phase 1.

- [ ] **11.5.1** `/check` with no system selected — Discord enforces required param
- [ ] **11.5.2** `/ship ship_id:999999` — Error: ship not found / not owned
- [ ] **11.5.3** `/buy item_id:999999` — Error: item not found
- [ ] **11.5.4** `/equip item_name:NonexistentWeapon equipment_type:Weapon` — Error: not in inventory
- [ ] **11.5.5** `/about category:ship name:ZZZZZ_nonexistent` — Error: not found

---

## Phase 12 — Admin Operations & Audit `[ADMIN]`

### Configuration management

- [ ] **12.1** `[ADMIN]` `/admin_config action:View Config` — Shows full guild configuration embed (admin role, starting credits, sale price factor, XP thresholds, channel IDs)
- [ ] **12.2** `[ADMIN]` `/admin_config action:Set Starting Credits starting_credits:5000` — Updates starting credits
- [ ] **12.3** `[ADMIN]` `/admin_config action:View Config` — Verify starting credits changed to 5,000
- [ ] **12.4** `[ADMIN]` `/admin_config action:Set Admin Role admin_role:@SomeRole` — Updates admin role
- [ ] **12.5** `[ADMIN]` `/admin_config action:Reset to Defaults` — Resets config to defaults
- [ ] **12.6** `[ADMIN]` `/admin_config action:View Config` — Verify defaults restored

### Shop configuration

- [ ] **12.7** `[ADMIN]` `/admin_config_shop ship_count_min:2 ship_count_max:8` — Updates shop generation parameters
- [ ] **12.8** `[ADMIN]` `/admin_config_validate` — Validate config is still valid after changes

### Shop refresh

- [ ] **12.9** `[ADMIN]` `/admin_refresh_shop tier:Bronze` — Force refreshes Bronze shop inventory
- [ ] **12.10** `/shop tier:Bronze` — Verify items have changed
- [ ] **12.11** Verify shop refresh announcement appears in `#shop` channel with `@Bounty Hunter` role mention

### Guild statistics

- [ ] **12.12** `[ADMIN]` `/admin_guild_stats` — Shows total players, tier distribution, total/average credits and XP

### Admin permission check

- [ ] **12.13** `[ADMIN]` `/admin_check user:@admin_user` — Shows "has admin rights" with reason (developer/Discord admin/bot role)
- [ ] **12.14** `[ADMIN]` `/admin_check user:@non_admin_user` — Shows "does not have admin rights"

### Render configuration (blender-service)

- [ ] **12.15** `[ADMIN]` `/render_config action:view` — Shows current blender render settings
- [ ] **12.16** `[ADMIN]` `/render_config action:set setting:<key> value:<int>` — Updates a render setting
- [ ] **12.17** `[ADMIN]` `/render_config action:reset` — Resets render settings to defaults
- [ ] **12.18** `[ADMIN]` `/render_cache_clear` — Clears blender render cache; shows freed_mb

### Destructive operations (test last!)

- [ ] **12.19** `[ADMIN]` `/admin_uninstall` (without confirm) — Shows red warning embed listing what will be deleted (7 channels, category, @Bounty Hunter role, all DB data). Does NOT delete anything.
- [ ] **12.20** `[ADMIN]` `/admin_uninstall confirm:CONFIRM-DELETE` — **DESTRUCTIVE**: deletes all 7 BountyBot channels, the BountyBot category, the @Bounty Hunter role, and all guild DB data (config, shops, players, bounties, everything). Shows removed record counts.
- [ ] **12.21** Verify BountyBot category and all 7 channels are gone from Discord
- [ ] **12.22** Verify `@Bounty Hunter` role is gone from guild role list
- [ ] **12.23** `/profile` — Player data gone; must re-register
- [ ] **12.24** `/bounties` — All bounties gone
- [ ] **12.25** `/shop tier:Bronze` — Shops need re-initialisation
- [ ] **12.26** `[ADMIN]` `/admin_setup` — Re-initialise after uninstall; verify all channels, category, and role recreated cleanly

---

## Phase DEFERRED — Skins & Rendering

> ⏸️ **DEFERRED**: Skipping for now; revisit later. These tests require `blender-service` with a GPU-enabled container. All items are preserved here for when you return to this phase.

### Skin display (no GPU rendering)

- [ ] **D.1** `/ship_skin ship:<skinnable_ship> skin:Default` — Shows ship icon embed (uses ship's default icon URL)
- [ ] **D.2** `/ship_skin ship:<skinnable_ship> skin:urban-camo` — Shows skin image URL in embed
- [ ] **D.3** `/ship_skin ship:<non_skinnable_ship> skin:Default` — Error: ship does not support custom skins
- [ ] **D.4** `/ship_skin ship:<ship> skin:nonexistent_skin` — Error: skin not found

### 3D rendering (requires blender-service + GPU)

- [ ] **D.5** `/render_skin ship:<1-region ship, e.g. Betty> skin:Default` — Returns rendered PNG as file attachment with format download buttons
- [ ] **D.6** `/render_skin ship:<2-region ship, e.g. Phantom XT> skin:urban-camo` — Renders with skin overlay on 2 regions
- [ ] **D.7** `/render_skin ship:<3-region ship, e.g. Kinzer RS> skin:racing-stripes` — Renders with skin overlay on 3 regions
- [ ] **D.8** Click "AEI (Android/ETC1)" button on render result — Delivers .aei file for mobile
- [ ] **D.9** Click "AEI (PC/DXT5)" button on render result — Delivers .aei file for desktop

### Texture compositing (no 3D render)

- [ ] **D.10** `/make_skin_texture ship:<1-region ship> skin:Default` — Returns composited 2D texture PNG
- [ ] **D.11** `/make_skin_texture ship:<2-region ship> skin:ferrari` — Returns composited texture with 2 skin regions applied

### Edge cases

- [ ] **D.12** `/render_skin ship:<non_skinnable_ship>` — Error: does not support custom skins
- [ ] **D.13** `/render_skin ship:<ship_without_3d_assets>` — Graceful error or fallback (no .blend file)
- [ ] **D.14** Ships with `textureRegions: 0` (e.g., Vol Noor, Amboss) — Should be treated as non-skinnable
- [ ] **D.15** Ships with `textureRegions: -1` (e.g., Cronus) — Edge case: should handle gracefully

---

## Test Results Summary

| Phase | Total | Passed | Failed | Skipped | Notes |
|-------|-------|--------|--------|---------|-------|
| 0 — Stack Health | 4 | 4 | 0 | 0 | Admin-only for /ping and /health |
| 0.5 — Pre-Registration Edge Cases | 3 | | | | Alt account must be unregistered |
| 1 — Setup, Channels & Registration | 13 | | | | 1 needs `[2P]` |
| 1.5 — Non-Admin Permission Denials | 7 | | | | Alt account must have no admin |
| 2 — Game Data | 14 | | | | |
| 3 — Ship Management | 6 | | | | 2 deferred to after Phase 4 |
| 4 — Shop | 12 | | | | |
| 5 — Inventory & Equipment | 15 | | | | 1 needs `[ADMIN]` |
| 6 — Progression & Tiers | 17 | | | | ALL need `[ADMIN]` |
| 7 — Bounty Hunting | 17 | | | | 2 need `[2P]`, 1 needs `[WAIT]` |
| 8 — Dueling | 11 | | | | ALL need `[2P]` |
| 9 — Scheduler Administration | 6 | | | | ALL need `[ADMIN]` |
| 10 — Dev Tools | 3 | | | | ALL need `[ADMIN]` |
| 11 — Scheduled Jobs | 6 | | | | 1 needs `[2P]` |
| 11.5 — Invalid Input Edge Cases | 5 | | | | |
| 12 — Admin Ops & Audit | 26 | | | | ALL need `[ADMIN]` |
| DEFERRED — Skins & Rendering | (15) | | | | Requires blender-service + GPU; excluded from active count |
| **TOTAL (active)** | **165** | | | | |

### Tests requiring a second Discord user: ~15
`1.13, 7.16, 7.17, 8.1–8.11, 11.4, 11.6`

### Tests requiring admin permissions: ~65
`0.1, 0.2, 1.1, 1.5, 1.6, 5.15, 6.1–6.17, 9.1–9.6, 10.1–10.3, 11.1–11.6, 12.1–12.26`

### Tests requiring wait time: ~7
`7.3, 8.11, 11.1–11.6`

### Tests requiring blender-service + GPU: ~15
`D.1–D.15` (all deferred)

---

## Appendix A — Quick Command Reference

| Cog | Commands |
|-----|----------|
| **healthCog** | `/ping` `[ADMIN]`, `/health` `[ADMIN]` |
| **aboutCog** | `/about`, `/list_category`, `/make-route` |
| **playerCog** | `/profile`, `/register` (alias of `/profile`, 2026-04-21), `/leaderboard`, `/prestige`, `/promote`, `/loadout`, `/unregister` |
| **shipsCog** | `/ships`, `/ship`, `/setactive`, `/nickname` |
| **shopCog** | `/shops`, `/shop`, `/buy`, `/sell` |
| **inventoryCog** | `/inventory`, `/search`, `/item`, `/equip`, `/unequip` |
| **bountyCog** | `/bounties`, `/check`, `/route`, `/criminal-loadout` |
| **duelCog** | `/duel-challenge`, `/duel-accept`, `/duel-reject` |
| **skinsCog** | `/ship_skin`, `/render_skin`, `/make_skin_texture` |
| **adminCog** | `/admin_setup`, `/admin_check`, `/admin_player`, `/admin_config`, `/admin_config_shop`, `/admin_config_validate`, `/admin_guild_stats`, `/admin_refresh_shop`, `/admin_uninstall`, `/render_config`, `/render_cache_clear` |
| **schedulerCog** | `/scheduler_list`, `/scheduler_view`, `/scheduler_update`, `/scheduler_delete` |
| **devCog** | `/load_data`, `/reload_autocomplete` |
| **setupCog** | *(No slash commands — event listeners: `on_guild_join`, `on_guild_remove`)* |

## Appendix B — Channel Infrastructure Reference

Created by `/admin_setup` via `ensure_bountybot_infrastructure()`:

| Channel | Permission | Purpose |
|---------|-----------|---------|
| `#bronze-bounty-board` | Read-only (players can view, not type) | Bronze division bounty announcements |
| `#silver-bounty-board` | Read-only | Silver division bounty announcements |
| `#gold-bounty-board` | Read-only | Gold division bounty announcements |
| `#shop` | Read-only | Shop refresh announcements |
| `#bounty-hunting` | Interactive (slash commands + chat) | Gameplay commands |
| `#bounty-discussions` | Chat-only (NO slash commands) | Player discussion |
| `#bot-images` | Hidden (bot-only) | Route map image hosting |

**Roles:**
- `@Bounty Hunter` — Assigned by `/profile`, removed by `/unregister`. Controls visibility of all BountyBot channels.
- `BountyBot Admins` — Created if no admin role specified. Grants admin command access.

## Appendix C — Bounty Announcement Lifecycle

```
1. SPAWN (bounty_spawn_executor)
   └─> Rich embed posted to per-division channel (#bronze/#silver/#gold-bounty-board)
   └─> @Bounty Hunter role mentioned
   └─> Route map image uploaded to #bot-images, embedded in announcement
   └─> Discord message ID persisted to DiscordMessage table

2. LIVE-EDIT (on every /check)
   └─> Announcement embed updated in-place
   └─> Checked systems: ~~strikethrough~~
   └─> Found system: **bold**
   └─> No re-mention of @Bounty Hunter role

3. DELETE (on complete, escape, or expire)
   └─> Announcement message deleted from Discord channel
   └─> DiscordMessage DB record removed
   └─> Expiry: notification posted with bounty details
```

## Appendix D — Skin Test Ship Selection Guide

| textureRegions | Example Ships | Test Purpose |
|---|---|---|
| -1 | Cronus | Edge case: invalid/undefined regions |
| 0 | Vol Noor, Amboss | Non-skinnable despite having field |
| 1 | Betty, H'Soc, Darkzov, Bloodstar | Single-region rendering |
| 2 | Phantom XT, Badger, Aegir, Blue Fyre | Multi-region rendering |
| 3 | Kinzer RS | Maximum-region rendering |

Available skin names (consistent across skinnable ships):
`urban-camo`, `racing-stripes`, `ferrari`, `onyx`, `lilac`, `cargo`, `festive`, `neopolitan`, `bloodlust`, `soul-marble`, `slate`, `rainbow`, `tex`, `lava`, `camo`, `carbon-fibre`, `space`, `rusted`, `mint`, `leopard-print`, `candy`

---

## Appendix E — Defects & Anomalies Log

Running record of defects, anomalies, and discrepancies discovered during E2E testing. Each entry includes:
- **Severity**: 🔴 blocker | 🟠 high | 🟡 medium | 🔵 low | ℹ️ info
- **Source**: test item number that surfaced it
- **Status**: open | verified | fixed | wontfix | stale-checklist

Add new entries at the top as they're found.

---

### Cosmetic observation batch B.1–B.6 (2026-04-22 PM, all 🔵 low)
- **B.1** `/admin_config_validate` shows `guild bb-temp` as display-name placeholder where actual guild name should render
- **B.2** `player_ships.secondary_weapons` column is `NULL` (not `[]`) on starter Betty — new column added in A.36 bundle not backfilled to empty-array default in `seed_loadout` code path. Harmless so far but may trip future equip logic if the code expects a list
- **B.3** `/ship` detail embed shows redundant `Type: Betty` field (Betty is the ship name, not its type); should be `Class:` or omitted (name already in title)
- **B.4** Equip swap-flow dropdown lacks clear "select to swap" affordance — works, but minor discoverability gap
- **B.5** `/sell` success message would show `Item Type: Primary_Weapon` (DB concrete type with underscore + raw capitalization leaking) — now fixed indirectly by A.42 since `item_type` no longer in the response contract. **Verify after rebuild.**
- **B.6** `/buy` success embed shows `Item Type: Primary_Weapon` (same DB-concrete-type leak pattern as B.5 but on buy path). Cosmetic only.

### A.42 — `/sell` vocab-downgrade defect (latent A.36 miss) + UX parameter cleanup
- **Severity**: 🟠 medium (user-visible HTTP 422 on every `/sell` invocation; UX redesign bundled)
- **Source**: 2026-04-22 Phase 4.10 Alt test — raw `API Error: Client error '422 Unprocessable Entity'` surfaced during first live /sell attempt post-A.36 bundle
- **Status**: ✅ **FIXED 2026-04-22 PM** in follow-up bundle (commit pending after rebuild + live re-test)
- **Root cause**: A.36 bundle established "writes persist concrete types only" rule and added `InvalidItemTypeError → HTTP 422` guards on write paths. Bundle tests covered `/buy`, `/equip`, `/unequip`, admin writes — but missed `/sell`. The cog (`shopCog._SELL_TYPE_MAP`) was independently designed to **downgrade** concrete types (`primary_weapon`) to generic aliases (`weapon`) before POST. Backend sell endpoint then rejected the alias correctly, but raw 422 leaked to user.
- **Additional scope per user decision**: bundle also removes `item_type` and `target_tier` params from `/sell` entirely — `/sell` now takes only `item:<inventory item>` and `quantity`. Server resolves concrete type from inventory row by name (consistent with A.37 `/equip`/`/unequip` redesign). Items always land in player's current tier shop (consistent with `/buy` tier-gating: "you sell where you can buy").
- **Changes in fix**:
  - `shopCog.py`: removed `_SELL_TYPE_MAP`, removed `_resolve_sell_item_type()`, dropped `item_type` + `target_tier` slash command parameters; simplified POST payload
  - `shops_schema.SellRequest`: removed `item_type` and `target_tier` fields
  - `shops.py` router + `shop_service.sell_item()`: new 4-arg signature (no item_type, no target_tier); resolves concrete type from `inventory_repo.get_player_items_by_name()` (new helper); reads `target_tier` internally from `player.tier`
  - New cross-type collision guard (raises `InvalidItemTypeError` if same inventory name exists with two different concrete types for one player — currently impossible per 146/146 catalog name uniqueness)
  - Added regression test exercising the full happy path
- **Discovered during A.42 QA**: **D1 latent defect in `inventory_repository.get_inventory_summary()`** — summary aggregation dict initialized with generic alias keys (`"weapon"`, `"turret"`); A.36 made DB rows always concrete-typed; result = weapon/turret counts permanently 0 in `/inventory` summary. Bundled into A.42 fix. Retroactively this is why Alt's `/inventory` showed "Ships: 0 | Weapons: 0 | Modules: 0 | Turrets: 0" despite owning 1 primary_weapon.

### A.41 — Shop generation tier-balance enhancement (probabilistic zero-turret shops at initial setup)
- **Severity**: 🔵 low (enhancement candidate; not a bug)
- **Source**: 2026-04-22 during A.40 false-positive investigation
- **Status**: OPEN (enhancement)
- **Observation**: `/admin_setup`'s initial shop generation and each subsequent `shop_refresh` run produce tier-specific shop stock via probabilistic per-category draws. Initial Setup's random roll may legitimately produce 0 turret_weapon rows in a given tier. 5 forced `shop_refresh` cycles showed turrets appear in ~25% of tier-slots (5 of 20), which is statistically plausible but may confuse end-users who see empty turret categories post-setup and report "turrets don't work". Secondary weapons were correctly absent from all 6 generation cycles (A.38 gating verified).
- **Proposed enhancement**: Guarantee ≥1 of each `CURRENTLY_ENABLED_TYPES` category per tier at initial shop generation (post-refresh can remain probabilistic). Would eliminate the "surprising empty category" UX without changing overall stock balance.

### A.40 — WITHDRAWN (false positive: RNG-light turret placement, not a bug)
- **Severity**: n/a
- **Source**: 2026-04-22 shop composition DB query after admin_setup
- **Status**: WITHDRAWN 2026-04-22 — initial observation of "0 turret_weapon rows in any tier" was RNG coincidence, not a systemic defect. 5 forced `shop_refresh` cycles showed turrets generating normally across tiers. Enhancement opportunity logged as A.41. No code change needed.

### A.39 — `/item` UX cleanup bundle (deferred post-release per user decision)
- **Severity**: 🔵 low (UX redesign; functional command works correctly)
- **Source**: 2026-04-22 Phase 5.5 Alt test — user specified bundle during live testing
- **Status**: DEFERRED post-release (user decision 2026-04-22)
- **Scope**:
  - **A.39.1** — Remove `item_type` parameter from `/item` command. Backend resolves concrete type from `item_name` via Item STI lookup. Consistent with A.36/A.37 pattern applied to `/equip`/`/unequip` and A.42 applied to `/sell`. Cross-type name collisions are architecturally impossible (146 items, 146 distinct names in catalog).
  - **A.39.2** — `/item` embed title should include item emoji (e.g. `:m6a3wolverine: Micro Gun MK I`) instead of generic `📦`. Pattern parity with `/inventory` and `/search` embeds which already show per-item emojis.

### A.38 — Secondary weapons leak into economy/loadout flows (should be catalog-only until mechanics defined)
- **Severity**: 🟡 medium (correctness gap — unplayable item category appears in shops/inventory/give/etc.)
- **Source**: User-specified 2026-04-22 during A.36 design discussion
- **Status**: ✅ **FIXED 2026-04-22** in commit `3ad15b8` — `CURRENTLY_ENABLED_TYPES = {primary_weapon, turret_weapon, module, ship}` in `game_constants.py` excludes secondary_weapon; shop generation filters candidate pool; cog dropdowns expand generic "Weapon" through this gate; admin commands consistent. Data model fully supports secondary_weapon (player_ships.secondary_weapons column added). Re-verification via E2E items 4.2, 4.4, 5.6, 5.9, 6.1-6.11 post-wipe.
- **Design intent**: Secondary weapons have defined seed data (30 items) and should remain browsable via `/about category:secondary_weapon` and `/list_category category:secondary_weapon` for future-feature visibility, BUT they should not appear in:
  - `/shop` listings (shop_service.generate_shop_stock must exclude them)
  - `/buy` / `/sell` / `/give` flows
  - `/equip` / `/unequip` flows (no ship slot exists; previously protected only by "no user would own one" assumption)
  - `/admin_give_item` / `/admin_remove_item` flows (admin shouldn't bypass the gate either)
- **Current state** (likely — needs verification against live stack when it's back up):
  - `shop_service.generate_shop_stock` probably includes `secondary_weapon` in its candidate pool; current `guild_shops` rows may include secondary weapon listings
  - Any purchase/transfer flow that reaches `inventory_repository.add_item(..., "secondary_weapon", ...)` would silently store a row that can't be equipped
- **Proposed architecture** (ties into A.36 fix):
  - `CATALOG_ITEM_TYPES = {primary_weapon, secondary_weapon, turret_weapon, module, ship}` — used by `/about` + `/list_category` reads
  - `PLAYABLE_ITEM_TYPES = {primary_weapon, turret_weapon, module, ship}` — used by every economy/loadout validation
  - Service-layer generic alias expansion is context-aware: `weapon` in catalog context → `{primary, secondary, turret}`; `weapon` in playable context → `{primary, turret}`
  - Future enablement: single constant change (add `secondary_weapon` to `PLAYABLE_ITEM_TYPES`) flips the feature on everywhere
- **Takeaway**: Bundle with A.33+A.35+A.36+A.37 fix. DB wipe + fresh shop generation will naturally produce correct shop stock.

### A.37 — `/equip` and `/unequip` parameter redesign: auto-deduce `item_type`, scope `item_name` autocomplete to the relevant item set
- **Severity**: 🟡 medium (UX redesign; related to A.36 vocabulary issues but distinct design concern)
- **Source**: User-specified 2026-04-22 during Phase 5.5 triage
- **Status**: ✅ **FIXED 2026-04-22** in commit `3ad15b8` — `/equip` and `/unequip` drop `equipment_type` parameter; server-side resolves concrete type from selected item_name via Item STI lookup. Two new autocomplete helpers (`player_equippable_autocomplete` scopes to owned-but-unequipped; `player_equipped_autocomplete` scopes to items currently equipped on active ship) added to `utils/autocomplete_helpers.py`. Re-verification via E2E items 5.6-5.14 post-wipe.
- **Current state** (code at `services/discord-gateway/src/cogs/inventoryCog.py`):
  - `/equip` takes `item_name` (free-text with some autocomplete) + `equipment_type` (required user choice: Weapon/Module/Turret)
  - `/unequip` has same signature
  - User must know both the item's name AND remember to pick the correct equipment_type — if mismatched, operation fails
  - Autocomplete for `item_name` does not scope by equipped-status; user could pick an already-equipped item to equip, or an unequipped item to unequip
- **Desired state**:
  1. **Remove `equipment_type`** as a user-facing parameter — the cog should look up the selected `item_name` in the player's inventory and derive the type server-side (item row already has `item_type`).
  2. **`/equip` `item_name` autocomplete** should prepopulate with items the player **owns but has NOT equipped on their active ship** (the "available to equip" set).
  3. **`/unequip` `item_name` autocomplete** should prepopulate with items **currently equipped on the active ship** (the "available to unequip" set).
- **Implementation notes**:
  - Both flows need to resolve the player's active ship first (they already do via `EquipmentService.equip_item` / `unequip_item` at `services/bot-core/src/services/equipment_service.py`)
  - Autocomplete helpers would ideally live in `services/discord-gateway/src/utils/autocomplete_helpers.py` alongside the existing `player_ships_autocomplete` and `player_inventory_autocomplete` helpers; likely two new helpers: `player_equippable_autocomplete(...)` and `player_equipped_autocomplete(...)`.
  - Item type deduction happens after name is selected — resolve via `GET /api/v1/inventory/player/{id}` and find the matching row's `item_type`.
  - Requires A.36 resolution first (or concurrent with) since the deduced `item_type` (`primary_weapon` etc.) needs to be accepted by the downstream equip/unequip API.
- **Impact**: Friction reduction; prevents the "wrong equipment_type" error class entirely; autocomplete becomes actionable (only valid targets shown).
- **Fix direction**:
  1. Add two helpers to `autocomplete_helpers.py` (pattern matches existing `player_ships_autocomplete`)
  2. `/equip`: drop `equipment_type` param; wire new helper on `item_name`; resolve type after selection
  3. `/unequip`: same as above with the equipped-set variant
  4. Tests: new autocomplete helper tests (contract: correct filtering of owned-vs-equipped sets); updated cog tests (single-param invocation)
  5. Coordinate with A.36 resolution so the deduced concrete type (`primary_weapon`, etc.) flows cleanly through the equip/unequip API
- **Takeaway**: UX win that simplifies two commands substantially. Pair with A.36 fix — the vocabulary work and the autocomplete work both touch the same code paths.

### A.36 — Inventory API vocabulary mismatch breaks `/inventory` filter AND `/item` lookup (service vocabulary vs DB vocabulary)
- **Severity**: 🟠 high (two user-facing features silently produce wrong results; one leaks raw 404 URL)
- **Source**: Phase 5.2 `/inventory item_type:Weapon` + Phase 5.5 `/item item_name:Micro Gun MK I item_type:weapon` (2026-04-22, Alt)
- **Status**: ✅ **FIXED 2026-04-22** in commit `3ad15b8` — New normalizer module `_item_type_normalizer.py` with `expand_item_type_to_concrete(item_type, context)` function; reads accept generic aliases (expand to IN-clause against concrete types); writes require concrete (fail-fast via `InvalidItemTypeError`). Write-site corruption fixed at `admin.py:1013-1021` (now `admin.py:1026-1054`) and `ships.py:612-620` via ItemRepository STI discriminator lookup. Re-verification via E2E items 5.2, 5.5, 5.6-5.14, 4.x, 6.x post-wipe.
- **Observed**:
  - `/inventory item_type:Weapon` → raw API error: `❌ API Error: Client error '404 Not Found' for url '.../inventory/player/2?item_type=Weapon'`
  - `/item item_name:Micro Gun MK I item_type:weapon` → returns **"Quantity Owned: 0, Status: Not Owned"** despite DB row showing player 2 owns 1 Micro Gun MK I (verified: `SELECT ... FROM player_inventories WHERE player_id=2` → `item_type=primary_weapon, quantity=1`)
  - API probe: `GET /inventory/player/2/item/Micro Gun MK I/count?item_type=weapon` → `quantity: 0` (silent wrong answer)
  - API probe: `GET /inventory/player/2/item/Micro Gun MK I/count?item_type=primary_weapon` → `quantity: 1` (correct)
- **Root-cause chain** (double bug):
  1. **Cog-side** (`services/discord-gateway/src/cogs/inventoryCog.py:275,302-303`): The `/inventory` command accepts `item_type` as free-text (no `app_commands.Choice` constraint), no normalization, no autocomplete. The user's raw literal is passed directly to the API.
  2. **Service-side** (`services/bot-core/src/services/inventory_service.py:34`): `VALID_ITEM_TYPES = ["ship", "weapon", "module", "turret"]` (four generic aliases).
  3. **DB/repo-side** (`services/bot-core/src/persist/repositories/inventory_repository.py:106`): `PlayerInventory.item_type == item_type` — exact-match filter against column that actually stores `primary_weapon`, `secondary_weapon`, `turret_weapon`, `module`, `ship`.
  4. **Result**: `?item_type=weapon` → service accepts it → repo filters by exact match → 0 rows (0 items stored as literal `"weapon"`). `?item_type=Weapon` → service rejects → 404. `?item_type=primary_weapon` → service rejects (not in `VALID_ITEM_TYPES`) → 404.
  5. Additionally, service raises `ValueError` which FastAPI converts to 404 (wrong status code; should be 422 or 400 for invalid input).
- **Impact**: **Two user-visible features broken**:
  - `/inventory` `item_type` filter — always empty (silent fail) OR 404 with raw URL (loud fail)
  - `/item` quantity-owned lookup — always returns 0 for weapons stored as `primary_weapon`/`secondary_weapon`/`turret_weapon`. The user sees "Not Owned" for items they actually own.
  - Likely also affects `/buy`, `/sell`, `/equip`, `/unequip`, `/give` anywhere they pass the generic `weapon` alias through to a repository filter. Needs audit.
- **Fix direction**:
  1. **Cog**: add `app_commands.Choice` constraint on `item_type` with values `ship`, `weapon`, `module`, `turret` (matches `/item`'s pattern at inventoryCog.py:471-476)
  2. **Service**: map the generic aliases (`weapon`) to the family of concrete types (`primary_weapon`, `secondary_weapon`, `turret_weapon`) before querying, OR change repository to use `item_type.startswith(item_type_arg)` style match
  3. **Error status**: raise `HTTPException(status_code=400)` (or 422) for invalid item type, not 404
  4. **Consistency**: audit `/item`, `/buy`, `/sell`, `/equip`, `/unequip`, `/give` for the same vocabulary split — they may all need the same mapping layer
- **Takeaway**: Vocabulary-mismatch chain spanning cog/service/repo. Needs a single normalization layer, probably in `InventoryService`.

### A.35 — `/inventory` `item_type` param is free-text instead of auto-populated dropdown (UX)
- **Severity**: 🔵 low (UX gap; user-reported 2026-04-22)
- **Source**: Phase 5.2 `/inventory item_type:Weapon` (Alt observation)
- **Status**: ✅ **FIXED 2026-04-22** in commit `3ad15b8` — `/inventory` cog now uses `app_commands.Choice` decorator with Ship/Weapon/Module/Turret matching `/item`'s existing pattern. Re-verification via E2E item 5.2 post-wipe.
- **Observed**: The `/inventory` command's `item_type` parameter accepts free text. Related commands (`/item`) already use `app_commands.Choice` to constrain the user's input (`Ship`, `Weapon`, `Module`, `Turret`). This inconsistency plus the free-text field is what allowed A.36 to surface.
- **Fix direction**: mirror `/item`'s pattern — add `app_commands.Choice` decorator on the `item_type` param. Bundle with A.36's cog-side fix as a single change.
- **Takeaway**: Low-risk consistency fix. Will implicitly prevent the case-sensitivity leg of A.36.

### A.34 — `/ship` dropdown + `/ship` detail embed + `/nickname` dropdown styling gaps
- **Severity**: 🔵 low (cosmetic; three distinct sub-observations, related surface area)
- **Source**: Phase 3.2 `/ship ship_id:2` and Phase 3.3 `/nickname` (2026-04-22, Alt, user-observed)
- **Status**: open
- **Sub-observations**:
  1. **A.34a — green-dot emoji leak in `/ship` autocomplete dropdown**: `/ship` autocomplete shows a literal "green dot icon" prefix in the dropdown list. The autocomplete helper uses `🟢` to mark the active ship (`player_ships_autocomplete` label logic). User wants this removed from the dropdown (the indicator is redundant once the user selects the ship and sees the detail embed).
  2. **A.34b — `/ship` detail embed styling does not match `/loadout`**: The `/ship` command shows a different embed style than `/loadout` (which uses `loadout_embed.py` via the `_shared/` module). Weapons, modules, and ship icon rendering are inconsistent. User wants the two commands to produce matching embeds.
  3. **A.34c — `/nickname` autocomplete has the same green-dot issue as A.34a** (same helper, same pattern).
- **Fix direction**:
  1. Parameter on `player_ships_autocomplete()` to suppress the `🟢` marker (default-off? or opt-in for flows that want it); shipsCog commands pass `show_active_indicator=False`
  2. `/ship` detail handler refactored to delegate to `loadout_embed.build_loadout_embed()` — matches `/loadout` output
  3. No code change needed for A.34c beyond A.34a (same helper)
- **Takeaway**: Three small cosmetic changes that improve consistency. Can be bundled into one PR.

### A.33 — Rationalization defense: 404 used for "invalid input" responses across `/inventory` endpoint (possibly others)
- **Severity**: 🔵 low (HTTP semantics)
- **Source**: A.36 investigation (2026-04-22)
- **Status**: ✅ **FIXED 2026-04-22** in commit `3ad15b8` — `InvalidItemTypeError(ValueError)` raised by services, mapped to HTTP 422 via explicit router-level clause (placed before existing `ValueError → 400` catches). All three write endpoints covered (/inventory/add, /inventory/remove, /inventory/transfer). Re-verification implicit in A.35/A.36 closure checks.
- **Observed**: `/inventory/player/{id}?item_type=Weapon` returns HTTP 404 with body `{"detail": "Invalid item type: Weapon"}`. A 404 is semantically "resource not found" — but the resource (player 2) exists; what's invalid is the query parameter. HTTP 422 (Unprocessable Entity) or 400 (Bad Request) would be correct.
- **Likely root cause**: `inventory_service.py:49-50` raises `ValueError` which FastAPI maps to 404 by default. A proper `HTTPException(status_code=422)` would be more truthful.
- **Fix direction**: audit `inventory_service.py`, `shop_service.py`, and other services for `ValueError("Invalid ...")` patterns; replace with `HTTPException(status_code=422, detail=...)` for consistency
- **Takeaway**: Low-priority HTTP-hygiene issue. Fix as part of any broader error-handling pass.

### A.32 — `Mp'zzzm Thrust` module emoji alias `mpzzzm` has no corresponding Discord emoji
- **Severity**: 🔵 low (cosmetic; 1 of 66 modules displays emoji alias instead of image)
- **Source**: Phase 2.9 `/list_category category:module` (2026-04-22)
- **Status**: open
- **Observed**: In the module list, all entries render their emoji as a proper Discord emoji image except `Mp'zzzm Thrust`, which shows literal `:mpzzzm:` text before the name.
- **Likely cause**: The bot-core preload returns `emoji: "mpzzzm"` (alias), but no emoji with name `mpzzzm` is registered in the guild's emoji set. The cog falls back to rendering the raw `:alias:` text when it can't resolve the alias to a custom emoji.
- **Fix direction**: Either (a) upload a `mpzzzm` emoji to the guild's emoji pool, or (b) normalize the alias in the seed data to match an existing emoji. Start by checking `services/bot-core/import_data/module/` for the `mpzzzm` entry's emoji field, and compare against the guild's registered emoji set. If the gap is in the bot-images channel workflow (emojis uploaded automatically on setup), investigate that pipeline.
- **Takeaway**: Single-row data gap. Not blocking E2E.

### A.31 — `/list_category ... tech_level:N` always returns empty; preload endpoint doesn't include `tech_level` field
- **Severity**: 🟡 medium (feature completely non-functional; tests passed because mocks included the missing field)
- **Source**: Phase 2.12 `/list_category category:module tech_level:2` (2026-04-22)
- **Status**: open
- **Observed**: Running `/list_category category:module tech_level:2` on Alt returned "📭 No objects found in category 'module' matching tech level 2" despite 66 seeded modules of varying tech levels. Plain `/list_category category:module` (no filter) returned all 66 correctly, so the command path works; only the filter is broken.
- **Root cause** (confirmed by researcher, see `/proj/old-refs/session-research-2026-04-20/LIST_CATEGORY_TECH_LEVEL_INVESTIGATION.md`):
  - Cog at `services/discord-gateway/src/cogs/aboutCog.py:55-58` preloads all category objects at startup via `GET /about/categories/{category}/objects`
  - bot-core `services/bot-core/src/api/routers/about.py:102-109` returns only `id`, `name`, `aliases`, `emoji` — **omits `tech_level`**
  - Cog at `aboutCog.py:370` filters client-side via `filtered = [o for o in objects if o.get("tech_level") == 2]`
  - `o.get("tech_level")` returns `None` for every object → `None == 2` is always False → empty result
- **Affected categories**: module, primary_weapon, secondary_weapon, turret_weapon, criminal (all support a `tech_level` filter param but all share the same preload → all broken)
- **Test coverage gap**: `tests/cogs/test_aboutCog.py:1296-1301` mocks the preload response to include `tech_level`, so the filter appears to work in tests. Real API response omits it. Classic mock-vs-reality divergence — ties to A.23 (full test audit) scope.
- **Fix direction**:
  1. Update bot-core about.py preload response to include `tech_level` in the object list (preferred — keeps client-side filter fast)
  2. OR change cog to do server-side filtering via a new query param on the preload endpoint
  3. Update `test_aboutCog.py` mock to match real API shape — ideally use a contract test or fixture loaded from actual router response
- **Takeaway**: Need to fix the preload shape. Logging as A.31 for later prioritization.

### A.30 — Gateway `/channels` and `/categories/{id}/channels` list endpoints return `category_id: null` on child channels
- **Severity**: 🔵 low (data correctly parented in Discord; single-fetch `/channels/{id}` returns correct value; list endpoints strip it)
- **Source**: Phase 1.1 re-verification (2026-04-22, post-rebuild)
- **Status**: open
- **Observed**: After `/admin_setup` in guild `1490693399307616276`:
  - `GET /api/v1/guilds/{gid}/channels` → all 8 BountyBot child channels report `category_id: null`
  - `GET /api/v1/categories/{cat_id}/channels` → returns the 8 channels as children (correct) but each still has `category_id: null` in the serialized payload
  - `GET /api/v1/channels/{channel_id}` (single-fetch) → correctly returns `category_id: 1496313055699533934`
  - `guild_configs.category_id` is populated correctly; bot-core does not rely on child `category_id`
- **Likely cause**: The list endpoints probably use `ChannelConverter.channel_to_summary()` (which lacks `category_id`) or iterate over `guild.channels` while the bot's in-memory cache has a stale view where the parent link hasn't propagated. Single-fetch goes through `get_entity_or_404` → `bot.fetch_channel` which hits the Discord REST API and gets the up-to-date object.
- **Impact**: External consumers of the list endpoints (tooling, dashboards, future UI) would incorrectly classify all bot channels as orphaned. Not a runtime problem for the bot itself — all internal callers use `guild_configs.*_channel_id` lookups, which bypass this.
- **Fix direction**:
  1. Audit `channel_to_summary` vs `channel_to_detail` — if summary lacks `category_id`, and list endpoints use summary, that's the gap. Add `category_id` to `channel_to_summary`.
  2. If the list endpoints already use `channel_to_detail`, then the issue is cache freshness — list iteration returns cached objects without the parent link. Force `await channel.fetch()` or swap to `guild.fetch_channels()` on the list path.
- **Takeaway**: Serializer inconsistency between single and list endpoints. Fix after E2E test pass; not blocking.

### A.29 — Numeric ID parameters across ships/shop/skins/inventory cogs lack autocomplete selectors
- **Severity**: 🟡 medium (UX gap; bulk usability issue)
- **Source**: Phase 3.2 live observation (2026-04-21) — `/ship ship_id:1` required manual int entry
- **Status**: ✅ **FIXED 2026-04-21** — implementation complete; awaiting gateway restart + re-verification
- **Reality-check**: initial audit overstated scope. Final count was **3 real gaps** (not 10): `shipsCog /ship`, `shipsCog /nickname`, `inventoryCog /item`. Other "gaps" (shopCog /buy /sell, inventoryCog /equip /unequip, all skinsCog commands, shipsCog /setactive) already had working autocomplete.
- **Implementation**: new shared utility `services/discord-gateway/src/utils/autocomplete_helpers.py` exposes `resolve_player_id`, `player_ships_autocomplete`, `player_inventory_autocomplete`. Used by shipsCog (+ existing `/setactive` refactored to share helper) and inventoryCog. skinsCog unchanged per Q4=B (ship-definition-name autocomplete stays).
- **Param type change**: `/ship` and `/nickname` `ship_id` changed from `int` to `str` (matches `/setactive` pattern); graceful `int()` parse guard with 400 response on non-numeric input. Requires gateway restart for Discord command re-sync.
- **Tests**: 15 new gateway-side tests (7 helper + 6 shipsCog + 2 inventoryCog); all green.
- **Observed**: 10 parameters across 9 commands in 4 cogs currently require users to type numeric IDs instead of selecting from an autocompleted dropdown. Full audit: `/proj/old-refs/session-research-2026-04-20/AUTOCOMPLETE_COVERAGE_AUDIT.md`
- **Cogs affected**:
  - **shipsCog**: `/ship`, `/setactive`, `/nickname`, `/give` — 4× `ship_id` (int); data source `GET /api/v1/ships/player/{player_id}`
    - Note: `/setactive` already has a `setactive_autocomplete` function but needs verification it's wired to the decorator
  - **shopCog**: `/buy`, `/sell` — `shop_listing_id` (int) + `inventory_item_id` (int)
  - **skinsCog**: `/ship_skin`, `/render_skin`, `/make_skin_texture` — 3× `ship_id` (int)
  - **inventoryCog**: `/item`, `/equip` — 2× `item_name` (str)
- **Expected pattern**: Match existing working autocompletes in bountyCog (bounty IDs), duelCog (duel IDs), schedulerCog (job IDs) — all live-fetched on keystroke, return string IDs that are parsed inline
- **Takeaway**: Cover all 10 gaps in a single consolidated rollout. Consider sharing a `player_ships_autocomplete` utility between shipsCog and skinsCog (ship selection appears 7× across the two cogs).

### A.28 — GET /api/v1/ships/{ship_id} uses wrong repository (ShipRepository instead of PlayerShipRepository) → 500 error
- **Severity**: 🔴 blocker (Phase 3 blocker — `/ship`, `/nickname` both fail)
- **Source**: Phase 3.2 live run (2026-04-21)
- **Status**: ✅ **FIXED 2026-04-21** — implementation complete; awaiting gateway rebuild + E2E re-verification
- **Final scope**: 7 misused routes in `services/bot-core/src/api/routers/ships.py` (broader than initial single-route estimate). All converted from `ShipRepository` → `PlayerShipRepository`: GET `/{ship_id}`, POST `/`, GET `/player/{player_id}/active`, PUT `/{ship_id}/set-active`, PUT `/{ship_id}/loadout`, PUT `/{ship_id}/nickname`, GET `/{ship_id}/loadout`, DELETE `/{ship_id}`.
- **Tests**: 9 new `TestA28PlayerShipShape` regression tests in `services/bot-core/tests/api/test_ships_router.py` using real `PlayerShip` ORM instances (no mocks for model shape). Tripwire property verified empirically: temporarily reverting any fix causes the corresponding test to fail with 500.
- **DI-override split**: `get_ship_repository` and `get_player_ship_repository` now require separate overrides in tests — prevents a future mock-only test from hiding the same bug class.
- **Deferred observation**: `get_ship_repository` factory is retained in `ships.py` solely as a test-DI regression tripwire; production code no longer depends on it. Future polish could remove if a better tripwire is found.
- **Observed**: All `/ship ship_id:<N>` and `/nickname ship_id:<N>` invocations return HTTP 500 with bot-core log: `ships-api-router - ERROR - Error getting ship <N>: 'Ship' object has no attribute 'player_id'`
- **Root cause** (`services/bot-core/src/api/routers/ships.py:121-142`):
  - Route injects `ship_repo: ShipRepository` (line 122) which queries the `Ship` model — the ship-definition table (Betty, Darkzov, etc. — seed data)
  - Code then accesses PlayerShip-only fields (`player_id`, `ship_name`, `nickname`, `is_active`, `weapons`, `modules`, `turrets`) — none exist on `Ship`
  - Confirmed schema: `Ship` table has `name`, `armour`, `cargo`, `manufacturer`, etc. (definition) — no player-specific columns
- **Gateway expectation** (all callers treat it as PlayerShip lookup): `services/discord-gateway/src/cogs/shipsCog.py:169, 392` — `GET /ships/{ship_id}` expected to return PlayerShip with player-specific loadout/status
- **Ship definitions** are correctly accessed via a separate route: `GET /about/ships/{ship_name}/render-info` (`about.py:339`)
- **Fix direction**:
  1. Change `ships.py:122` dependency from `ShipRepository` → `PlayerShipRepository`
  2. Audit other routes in the same file (`get_active_ship`, `set_active`, `/loadout`, `/equip`, `/unequip`, `/transfer`) — some use `ship_repo.get_by_id()` on paths that also need PlayerShip (line 578 in `/transfer` does `ship.player_id != ...` which would fail the same way if it gets a Ship definition)
  3. Update tests to use real model objects instead of mocks (current tests passed because they mocked the repo — classic A.23 pattern)
- **Testing coverage**: existing `test_ships_router.py:166` tests GET `/ships/{ship_id}` but uses mocks, so the model-attribute mismatch was not caught

### A.27 — `/list_category` silently truncates at 50 items; footer warning only fires above 100
- **Severity**: 🟠 high (silent data loss)
- **Source**: Phase 2.5 live run (2026-04-21)
- **Status**: ✅ **FIXED 2026-04-21** — consistent cap at 100 items; footer "Showing first 100 of N" fires exactly when `len(filtered) > 100`. Current max category is modules at 66 items, so no user-visible truncation today. 4 real-data regression tests in `TestListCategoryBugBundleRegressions` use 101-item fixtures to exercise the previously-silent truncation path.
- **Observed**: `/list_category category:ship` returned 50 items in the embed despite the log reporting `count=65`. No truncation indicator shown to the user.
- **Root cause** (`services/discord-gateway/src/cogs/aboutCog.py:407-420`):
  - Line 407: iteration is hard-capped at `filtered[:50]`
  - Line 419: footer "Showing first 100 of N" only triggers when `len(filtered) > 100`
  - These two thresholds are inconsistent. For categories with 51–100 items (e.g. 65 ships, 66 modules), the user silently loses items without any "truncated" indicator.
- **Impact**:
  - Any category between 51 and 100 items silently drops items — 15 of 65 ships are hidden from the user in the current data
  - User has no way to know items were omitted
  - For the 66-module case the log reported success=66 but only ~50 were visually shown
- **Fix direction**:
  1. Pick a single consistent cap (suggest 100, matching the existing footer wording)
  2. Always display the footer when `len(filtered)` exceeds the cap, regardless of exact threshold
  3. Consider pagination or a "use /about name:<x> for details" hint when truncation occurs
- **Takeaway**: Fix alongside A.26 in a single aboutCog PR.

### A.26 — `/list_category` embed chunks list across fields all named "Objects", creating mid-list header breaks
- **Severity**: 🟡 medium (cosmetic but confusing; UX regression)
- **Source**: Phase 2.5 live run (2026-04-21)
- **Status**: ✅ **FIXED 2026-04-21** — new `add_continuation_fields()` helper in `services/discord-gateway/src/cogs/_shared/embed_pagination.py` (game-specific cog-adjacent location per user's yank-the-game architectural constraint). First field named "Objects", continuation fields use zero-width spacer name `"\u200e"` so they merge visually with the parent section. Also moved `loadout_embed.py` from `src/utils/` → `src/cogs/_shared/` (same rationale — game-specific logic belongs cog-adjacent, not in gateway-generic utilities).
- **Observed**: `/list_category` for any category with more than ~20 items renders multiple Discord embed fields each literally titled `"Objects"`, creating visible "Objects" header breaks inside what should look like one continuous list:
  ```
  Objects
  Darkzov
  Blue Fyre
  ... (20 items)
  Objects
  Teneta
  Nuyang II
  ... (20 items)
  Objects
  K'Suukk
  ```
- **Root cause** (`services/discord-gateway/src/cogs/aboutCog.py:412, 418`): both calls to `embed.add_field()` use `name="Objects"` instead of distinguishing the first field from continuation chunks.
- **Reference fix pattern**: The loadout embed redesign (`src/utils/loadout_embed.py`) solved the same problem — use an invisible-char field name (e.g. `"\u200b"`) for continuation chunks so the sections merge visually with the parent section. The first field keeps `"Objects"`, subsequent chunks use the invisible char.
- **Fix direction**:
  1. First field: `name="Objects"` (unchanged)
  2. All continuation fields: `name="\u200b"` (zero-width space)
  3. Also consider a sort order — current output appears un-sorted (not alpha, not by tech level) which makes the lists hard to scan
- **Takeaway**: Minor fix in `aboutCog.py`. Pair with A.27 as a single PR.

### A.25 — `/unregister` on unregistered user in unconfigured guild shows generic error instead of graceful "no role" message
- **Severity**: 🔵 low (fix later)
- **Source**: Phase 0.5.3 (Alt account, fresh rebuild 2026-04-20)
- **Status**: open — **low-priority bug, defer**
- **Observed**: Alt account (unregistered) ran `/unregister` in a not-yet-configured guild. Bot replied: `⚠️ An error occurred while removing the role.` — the generic catch-all ephemeral.
- **Expected** (per checklist 0.5.3): Graceful message such as "You don't have the Bounty Hunter role" — the user should not see a generic failure for a benign "nothing to do" state.
- **Root cause** (from gateway log):
  - `2026-04-21 00:20:20,446 - discord-gateway-PlayerCog - ERROR - /unregister error: guild=1490693399307616276, user=970691862035841048, error=Client error '404 Not Found' for url 'http://bot-core:8000/api/v1/config/guild/1490693399307616276'`
  - `playerCog./unregister` fetches guild config to determine which tier roles to remove
  - `GET /api/v1/config/guild/{id}` returns 404 when no `guild_configs` row exists
  - The 404 bubbles to the broad `except Exception` block instead of being handled as "nothing to unregister from"
- **Impact**: misleading error text to the user; no DB damage, no data loss
- **Fix direction**:
  1. Catch `httpx.HTTPStatusError` with `status_code == 404` specifically for the config lookup
  2. Treat it as "guild not configured → user isn't registered → respond with friendly 'you don't have the role' message"
  3. Separately: consider using the `_is_guild_not_configured()` helper already present for other cogs (shopCog / bountyCog) for consistency
- **Takeaway**: Low-priority functional bug. Fix alongside other error-handling polish.

### A.24 — `/health` Schema subsection has never actually worked; needs to be tied to live Alembic/SQLAlchemy state
- **Severity**: 🔵 low (not a blocker)
- **Source**: Phase 0.2 (fresh rebuild 2026-04-20)
- **Status**: open — **deferred, design-level fix**
- **Observed** (on a known-healthy stack, schema row present and correct):
  - Discord `/health` embed Schema subsection renders: `Status: unknown`, `Current Version: N/A`, blank `Schema Table Exists`, blank `Version Match`
  - Meanwhile direct `GET /api/v1/health` on bot-core returns `status: healthy`, `schema_check: {"version": "1.0.0", "expected_version": "1.0.0", "version_match": true}` — top-level health is correct
- **Root cause — contract mismatch**:
  - bot-core's `SchemaManager.get_schema_health_info()` (`services/bot-core/src/persist/schemas/schema_manager.py:121-147`) returns only three keys: `version`, `expected_version`, `version_match`
  - gateway's `healthCog` (`services/discord-gateway/src/cogs/healthCog.py:121-138`) reads six keys: `status`, `current_version`, `expected_version`, `schema_table_exists`, `version_match`, `error` — four of which bot-core never emits, and `current_version` is the wrong name for bot-core's `version` field
  - Additionally, nothing in `schema_check` is tied to the actual Alembic revision (`alembic_version.version_num` = `0007` at time of observation) or to the SQLAlchemy model/metadata state — it only reads the legacy `schema` table, which has a single row `(1.0.0, …)`
- **What the user wants**: The Schema subsection should reflect **concrete back-end state**:
  - Alembic current revision (from `alembic_version.version_num`)
  - Expected Alembic head (from migration scripts)
  - Match/mismatch between them
  - Optionally: SQLAlchemy ORM metadata drift (schemas known to ORM vs. tables present in DB)
- **Takeaway**: Redesign `get_schema_health_info()` to surface Alembic state alongside (or instead of) the legacy `schema` table version, then align `healthCog`'s field consumption to match. Add a response-schema contract test to prevent future drift. **Deferred** — embed is cosmetic and top-level health is accurate; schedule with test-audit work (A.23).

### A.23 — Full test-suite audit needed: over-mocking + sub-threshold coverage
- **Severity**: 🟡 medium
- **Source**: Cross-cutting observation across bot-core and discord-gateway test suites
- **Status**: open — **deferred until E2E testing fully passes**
- **Observed**: Many tests exceed the project's "max 2 mocks per test" standard (see `/proj/AGENTS.md` Code Standards). Additionally, several source files sit below the coverage threshold — in some cases possibly for valid reasons (e.g. thin glue code, integration-only paths, hardware-gated blender-service modules).
- **Scope of needed audit**:
  - Enumerate every test file that exceeds 2 mocks per test; refactor toward real objects with deterministic inputs (reference pattern: `test_combat_service.py`)
  - Measure per-file coverage across all three services; identify files below threshold
  - For each low-coverage file, decide: add tests, document the exemption, or remove dead code
  - Cross-reference with NC-002 (over-mocked tests) and NC-003 (integration test for orchestrator → one-time lifecycle) already tracked from prior sessions
- **Prerequisite**: Complete E2E manual testing first. Runtime behavior validation takes priority; test-quality refactor is stable-state cleanup that should not block or interleave with the E2E pass.
- **Takeaway**: Schedule as a dedicated follow-up pass after the 15-phase E2E checklist reaches green.

### A.1 — Starter loadout investigation — CLOSED (not a bug)
- **Severity**: ℹ️ info (was 🟡)
- **Source**: Phase 0.5.2 (Run 1 + Run 2) + Developer investigation 2026-04-18
- **Status**: ✅ **CLOSED — NOT A BUG**
- **Observed**: New player's Betty ship has `Nirai Impulse EX 1` equipped as primary weapon, with `Micro Gun MK I` (also primary) sitting unequipped in `player_inventories`.
- **Verdict after investigation**: The observed state exactly matches user's authoritative spec. Code at `services/bot-core/src/services/player_service.py:104-121` hardcodes exactly this design. Both weapons are primary-class; new player receives one equipped + one spare in cargo hold. Betty has `max_primaries=1`, so only one can be equipped at a time. By design, not a defect.
- **Takeaway**: Update the checklist item 1.7 description (it was misleading/stale). Current correct starter state: Betty active, `Nirai Impulse EX 1` equipped (primary), `E2 Exoclad` + `Telta Quickscan` equipped (modules), `Micro Gun MK I` in cargo (primary, spare).

### A.2 — Secondary weapons / IMT Extract 1.3 — CLOSED (not a bug, feature deferred)
- **Severity**: ℹ️ info (was 🟡)
- **Source**: Phase 0.5.2 (Run 1 + Run 2) + Developer investigation + user clarification 2026-04-18
- **Status**: ✅ **CLOSED — NOT A BUG**
- **Observed**: `IMT Extract 1.3` (per stale checklist claim, a secondary weapon) is absent from new player's starter state.
- **Verdict after investigation + user clarification**: **Secondary weapons are NOT yet implemented in the game.** They are planned as single-use weapons (rockets, missiles, bombs) but not active feature. Their absence from starter loadout is correct. The `IMT Extract 1.3` reference in the original checklist 1.7 description is stale — likely from an earlier iteration that had placeholder secondary weapons.
- **Schema context**: `player_ships` has a single `weapons` JSON column (no separate `secondary_weapons`). When secondary weapons ARE eventually implemented, they'll share this column (per current `equipment_service.py` design) OR the schema will be extended. Today, only primary weapons go into `weapons`.
- **Takeaway**: Update checklist 1.7 to remove `IMT Extract 1.3` reference. Do not treat as a missing item during Phase 1 testing.

### A.3 — shopCog actively CORRUPTS discord_username on every invocation
- **Severity**: 🟠 high (was 🔵 low — escalated after investigation)
- **Source**: User-reported during Phase 0.5 session
- **Status**: ✅ **FIXED / CLOSED 2026-04-21** — audit pass (researcher 2026-04-21) confirmed zero remaining offenders. All cogs now pass the correct value: `/profile`, `/prestige`, `/leaderboard` use `str(interaction.user)`; shop/bounty/admin cogs pass `None` (correct — they don't mutate username on existing users). No sibling anti-patterns found.
- **Observed**: `users.discord_username = "temp"` after any shopCog interaction.
- **Root cause** (after code trace):
  - `user_repository.get_or_create_user()` at `user_repository.py:114-119` updates `discord_username` whenever the caller passes a truthy value that differs from what's stored.
  - `shopCog._get_player_data()` at `shopCog.py:33` hardcodes `"discord_username": "temp"` when upserting the player.
  - This means:
    - First `/shop` → `users.discord_username = "temp"` (because it didn't exist before)
    - Later `/profile` → overwrites to real username (correct)
    - Later `/shop` or `/buy` or `/sell` → **overwrites real username BACK to "temp"** (bug)
    - Later `/profile` → corrects it again
    - And so on, cycling with every shop interaction
- **Code locations**:
  - Overwrite trigger: `services/bot-core/src/persist/repositories/user_repository.py:114-119`
  - Bug source: `services/discord-gateway/src/cogs/shopCog.py:33`
- **`/profile` behavior**: Correctly passes `str(interaction.user)` (real username). Updates the row.
- **Impact**:
  - Any feature that displays `users.discord_username` will show `"temp"` intermittently (after every shop action)
  - Username-based lookups could fail
  - Audit log entries including usernames would show `"temp"` after shop interactions
  - User confusion if any UI reflects `discord_username`
- **Minimum fix** (1-line change, zero risk): In `shopCog._get_player_data()`, change `"discord_username": "temp"` to `"discord_username": None`. Since `get_or_create_user()` has `elif username and ...`, passing `None` makes it truly get-or-create without touching the username field.
- **Better fix**: Change `_get_player_data()` signature to accept an optional `discord_username` parameter, and have callers pass `str(interaction.user)` when available. Makes shopCog a well-behaved username-refresher like playerCog.
- **Additional audit needed**: Check if other cogs (bountyCog, duelCog, inventoryCog, etc.) also pass `"temp"` or similar placeholders. If so, they have the same bug.

### A.6 — Bounty spawn executor fires against un-setup guilds
- **Severity**: 🟠 high
- **Source**: Phase 1 — fresh stack with zero `guild_configs` rows yet bounties were spawning
- **Status**: ✅ **FIXED** (code-verified 2026-04-21) — guild eligibility guard added to bounty_spawn_executor. Requires E2E re-verification; earlier "Next steps" sub-items (3) canonical reset procedure and (4) orphaned one-time jobs remain as separate concerns (see A.11).
- **Observed**: After a full DB nuke and stack relaunch (with `/load_data category:All` to re-seed game data), the dev guild had:
  - 1 `guild_configs` row (channel IDs all null, no admin role) — source of creation unclear (see below)
  - 4 `bounty` rows in `active` status (one per tier)
  - 4 `bounty_expire` one-time jobs and 3 recurring default jobs in `apscheduler_jobs`
  - 0 players, 0 channels, 0 roles — nothing to actually announce TO

- **Root-cause analysis — three intertwined issues**:

  **1. Scheduler state persisted through the nuke.** The `apscheduler_jobs` table was NOT in the user's DB nuke scope, so `bounty_spawn_default`, `temperature_decay_default`, and `shop_refresh_default` survived as pre-existing recurring jobs. On bot-core restart, APScheduler resumed these jobs from the SQLAlchemy job store and fired `bounty_spawn_default` on its next scheduled tick (within minutes of startup). This is **APScheduler doing its job correctly** — persistence is the feature — but it means "nuke the game-data tables" is NOT a clean reset; the scheduler state also needs attention.

  **2. Bounty spawn executor has no eligibility guard.** When the recurring job fired, `bounty_spawn_executor` iterated over all `guild_configs` rows and spawned bounties for any it found, regardless of whether the guild had channels/roles/players set up. This allowed zombie bounties in an un-setup guild.

  **3. Skeleton `guild_configs` row creation path — RESOLVED (Run 2 pristine test).** On a truly pristine DB:
  - Bot-core startup alone creates 0 `guild_configs` rows ✅ (not a startup auto-provision)
  - `/bounties` (read-only) creates 0 `guild_configs` rows ✅ (bountyCog does not touch config)
  - `/shop tier:Bronze` creates 1 `guild_configs` row ❌ (**culprit confirmed**: shopCog calls shop_service → config_service.get_or_create_config() which creates the skeleton row with all null channel/role IDs)

  So the skeleton row appears whenever a user interacts with shopCog before `/admin_setup`. Not inherently broken (shop needs starting_credits from config) but means config existence is NOT a reliable signal that the guild is set up. See "expected" and "next steps" below.

- **Expected**:
  - Scheduler jobs should be scoped so a full reset can cleanly clear them (either: (a) scheduler jobs table included in reset procedure, or (b) executors check eligibility before acting)
  - Bounty spawn executor should skip guilds where `bronze_bounty_channel_id IS NULL` or `bounty_hunter_role_id IS NULL` or player_count == 0
  - Skeleton `guild_configs` rows should only exist after explicit setup

- **Impact**:
  - Zombie bounties in un-setup guilds
  - Failed Discord announcements (no channel_id)
  - Confusing state during fresh onboarding
  - "Nuke DB" procedures give false sense of clean state if scheduler jobs persist
  - Wasted scheduler cycles

- **Next steps** (deferred post-E2E):
  1. **Add eligibility guard** in `services/bot-core/src/utils/executors/bounty_spawn_executor.py`: skip guilds where any critical `_id` config field is null
  2. **Audit `guild_configs` creation paths** — determine what creates skeleton rows and gate appropriately
  3. **Document canonical "reset procedure"** — either include scheduler table in nuke, or provide `POST /api/v1/jobs/reset` workflow, or have executor guards make scheduler-persistence harmless
  4. **Clean up orphaned one-time jobs** — when bounties are cleared/deleted, their linked `bounty_expire` jobs should also be cancelled; currently they linger and fire against non-existent bounty IDs

- **Run 2 evidence (2026-04-18, fully pristine DB)**:
  - 20:43:14: bot-core startup registered `bounty_spawn_default` with cron `*/5 * * * *`
  - 20:46:24: Alt account ran `/shop tier:Bronze` → shop_service → config_service.get_or_create_config() → skeleton `guild_configs` row created (all `_channel_id` and `_role_id` fields null)
  - 20:47:06: Regular 5-min cron tick fired; executor iterated `guild_configs` rows and:
    - Called `_spawn_new_bounty` for each of the 4 divisions (bronze/silver/gold/platinum)
    - 4 bounty rows created in DB, 4 `bounty_expire` one-time jobs scheduled at +8h
    - For each, logged `WARNING - division channel not configured, skipping announcement`
    - Set `next_spawn_check_at = 21:38:51` (~52 min out)
  - 20:52:59: Next tick fired → saw `next_spawn_check_at` in the future → skipped (throttling works)

- **Architectural observation**: The executor IS aware of un-set-up state (it emits WARNING when skipping announcements) but it still persists bounty records to the DB before checking. This is "data-first, UI-second" architecture — sensible for event-sourced systems but produces zombie bounties here. The fix is likely a pre-spawn guard: `if not self._is_guild_fully_configured(config): continue` at the top of the per-guild loop.

- **Zombie bounty characteristics in this state**:
  - ✅ Persisted in DB as `status='active'` with proper `end_time`
  - ✅ Have auto-selected criminals, routes, expiry jobs
  - ❌ Not announced to any Discord channel (channels don't exist)
  - ⚠️ Would still be reachable via `/bounties` slash command or `/check system:X` — but no one would know they exist
  - ⚠️ Count against the `bounty_max_per_tier` cap, so after setup, a player's first bounty experience is ALREADY at cap

### A.5 — Help command set (feature request)
- **Severity**: 🟡 medium (feature gap, not a bug)
- **Source**: User-reported during Phase 0.5 session
- **Status**: ✅ **FIXED** — `helpCog.py` implemented; `/help` + `/admin_help` live-verified 2026-04-21 across Phases 0.5.4–0.5.8, 2.5.1–2.5.17
- **Observed**: No discoverable `/help` command exists. Users currently must know command names ahead of time or browse Discord's autocomplete (which exposes ALL commands including admin — see A.4).
- **Expected**: Two-tier help system:
  - **`/help`** — user-facing; lists and describes non-admin commands only: `/profile`, `/bounties`, `/check`, `/ships`, `/ship`, `/shop`, `/buy`, `/sell`, `/inventory`, `/search`, `/item`, `/equip`, `/unequip`, `/duel-challenge`, `/duel-accept`, `/duel-reject`, `/about`, `/list_category`, `/make-route`, `/leaderboard`, `/prestige`, `/unregister`, `/nickname`, `/setactive`, `/criminal-loadout`, `/route`
  - **`/admin_help`** — admin-only; lists admin/dev commands: all `/admin_*`, all `/scheduler_*`, `/load_data`, `/reload_autocomplete`, `/ping`, `/health`, `/render_config`, `/render_cache_clear`
- **Impact**: Poor onboarding for new users; admins can't easily remember all admin command options; no single-source documentation in-app.
- **Design notes**:
  - Should respect permission scoping (A.4 fix applies): `/admin_help` should not even be visible to non-admins
  - Group commands by cog/category with short descriptions
  - Consider paginated embed or categorized dropdown if output is long
  - Could include links to full docs or examples
- **Next step** (deferred post-E2E): Create new `helpCog.py` in `services/discord-gateway/src/cogs/` implementing both commands. Reference existing command descriptions from each cog's `@app_commands.command(description=...)`.

### A.4 — Admin slash commands visible to non-admin users in autocomplete
- **Severity**: 🟠 high → 🟢 low (scope narrowed)
- **Source**: User-reported during Phase 0.5 session
- **Status**: ✅ **LARGELY FIXED 2026-04-21** — Phase 1.5 live-verified all admin-gated commands except `/ping` are correctly hidden from non-admin Alt account. Scope narrowed from ~10 leaking commands down to 1. Remaining leak is tracked separately as **A.20** (`/ping` still visible despite matching decorator pattern).
- **Observed**: Commands gated by `@is_admin()` still appear in the slash-command dropdown for non-admin users. When a non-admin attempts to invoke, they get an ephemeral "permission denied" error — but the commands are discoverable/visible in the first place.
- **Expected**: Admin-only commands should be invisible (or at least hidden by default) to users without admin permissions — via Discord's `default_permissions` / `default_member_permissions` on the app command decorators.
- **Impact**: Information leakage (shows non-admins what admin tools exist), clutter in the slash-command UI, and poor UX (users can attempt commands they can't use).
- **Examples to audit**: `/load_data`, `/reload_autocomplete`, `/ping`, `/health`, all `/admin_*`, all `/scheduler_*`.
- **Next step** (deferred post-E2E): Audit every cog file in `services/discord-gateway/src/cogs/` for `@is_admin()` usage without matching `default_permissions` / `default_member_permissions` on the `@app_commands.command` decorator. Investigate whether the codebase uses discord.py's permission-based command visibility or relies purely on runtime check rejection. File updates likely needed in `healthCog.py`, `devCog.py`, `adminCog.py`, `schedulerCog.py`.

### A.9 — Bounty config validator rejects `platinum` tier while spawner produces platinum bounties
- **Severity**: 🟠 high (internal inconsistency — writer/reader tier lists disagree)
- **Source**: Session Setup during Phase 1 (2026-04-19)
- **Status**: ✅ **FIXED 2026-04-21** — `"platinum"` added to `valid_tiers` set in `config_service.py`; 4 regression tests in `TestUpdateBountyConfigPlatinumTier`. A.12 (sibling doc entry) also resolved — Session Setup script in checklist restored to include `"platinum":20`.
- **Observed**: `PUT /api/v1/config/guild/{gid}/bounty` with `max_bounties_per_tier={bronze:20,silver:20,gold:20,platinum:20}` returns HTTP 400: `"Invalid tier keys: {'platinum'}. Must be bronze, silver, or gold."`. However, the same endpoint's GET response reports `active_bounties_per_tier` with all four tiers (`bronze`, `silver`, `gold`, `platinum`), and `bounty_spawn_executor` **does** spawn platinum-tier bounties (confirmed: bounty id=4, `division='platinum'`, `criminal_name='Nombur Telénah'`).
- **Expected**: Writer validator and reader / spawner must agree on the canonical tier list. Either (a) accept `platinum` in `max_bounties_per_tier` (likely correct — platinum is a real tier with its own channel, role, and shop), or (b) if platinum caps are intentionally controlled elsewhere, document it and drop `platinum` from `active_bounties_per_tier` output + stop spawning platinum bounties.
- **Impact**:
  - Can't configure platinum cap from the admin API — platinum spawns are effectively uncapped or use a hardcoded default
  - Session Setup script in this checklist (previously written with platinum in the payload) fails silently-ish when admins follow it verbatim
  - Potential for platinum-tier bounty flood during Phase 7 stress testing
- **Likely root cause**: Schema validator allowlist in `services/bot-core/src/api/schemas/config_schemas.py` (or similar) accepts only 3 tiers. Spawner in `services/bot-core/src/services/bounty_service.py` and/or `utils/executors/bounty_spawn_executor.py` iterates the 4-tier list.
- **Next step** (deferred post-E2E): Grep the codebase for tier-list constants — there's likely a `TIERS = ["bronze","silver","gold"]` somewhere that's out of sync with `["bronze","silver","gold","platinum"]` elsewhere. Unify on a single shared constant (probably `persist/models/player.py` or a `constants.py`).
- **Workaround during E2E**: Submit `max_bounties_per_tier` with only bronze/silver/gold. Platinum cap is out-of-reach until fixed.

### A.10 — Checklist 1.1 undercounts roles AND channels created by /admin_setup, plus naming inconsistencies
- **Severity**: 🟢 low (documentation gap + minor naming inconsistency, not a functional bug)
- **Source**: Phase 1 item 1.1 runs (2026-04-19, reconfirmed 2026-04-21 on fresh rebuild)
- **Status**: open (doc update only)

- **Role observations (2026-04-21 confirmed)**: `/admin_setup` on a pristine guild created **5** BountyBot bounty-related roles + reused 1 pre-existing admin role:
  - `Bounty Hunter` (generic — listed in 1.1 ✅)
  - `Bounty Hunter Bronze` (NOT listed)
  - `Bounty Hunter Silver` (NOT listed)
  - `Bounty Hunter Gold` (NOT listed)
  - `Bounty Hunter Platinum` (NOT listed)
  - Admin role: reused pre-existing `@BountyBot Admin` when available (the checklist mentions "BountyBot Admins" — actual name is singular: "BountyBot Admin"). On earlier runs without a pre-existing admin role, `/admin_setup` would create it; in this run it was reused.

- **Channel observations (2026-04-21)**: `/admin_setup` created **8 artifacts** (1 category + 7 text channels — checklist currently lists 7 text channels but misses `#platinum-bounties`):
  - Category: `BountyBot` ✅ listed
  - `#bronze-bounty-board` ✅ listed
  - `#silver-bounty-board` ✅ listed
  - `#gold-bounty-board` ✅ listed
  - `#platinum-bounties` ❌ **NOT listed in checklist 1.1** — also breaks naming convention (should be `#platinum-bounty-board` for consistency with the bronze/silver/gold trio; actual creation uses the shorter name)
  - `#shop` ✅ listed
  - `#bounty-hunting` ✅ listed
  - `#bounty-discussions` ✅ listed
  - `#bot-images` ✅ listed (hidden from all users)

- **Expected**: Checklist 1.1 should list all 5 bounty roles + admin role, all 8 channel artifacts (category + 7 text channels including `#platinum-bounties`), and reconcile the "BountyBot Admins" text vs actual "BountyBot Admin" role name. Additionally, the `platinum-bounties` channel name should be aligned to `platinum-bounty-board` for consistency (or accept the current name and document the asymmetry).

- **Impact**:
  - Checklist reader may flag the "extra" roles/channels as anomalies when they're intentional
  - Blocks downstream Phase 1.4 permission checks (tier roles likely gate tier-specific channel visibility — the checklist only describes @Bounty Hunter vs @everyone)
  - Naming inconsistency (`platinum-bounties` vs `platinum-bounty-board`) is cosmetic, not functional

- **Next step** (deferred post-E2E):
  1. Update checklist 1.1 expected artifacts to include all 5 bounty roles + the `#platinum-bounties` channel
  2. Reconcile the "BountyBot Admins" vs "BountyBot Admin" naming (checklist or code)
  3. Decide whether to rename `#platinum-bounties` → `#platinum-bounty-board` in `guildSetupService` for naming consistency
  4. Verify Phase 1.4 permission expectations account for tier-role gating

### A.11 — Cleared bounties leave zombie expire jobs in APScheduler
- **Severity**: 🟡 medium (log noise + potential false-positive errors; not data corruption)
- **Source**: Phase 1 cleanup operation — `/admin_clear_bounties confirm:CONFIRM` (2026-04-19)
- **Status**: ✅ **FIXED 2026-04-21** — `BountyService.clear_bounties()` now cleans up both `bounty_expire` AND `bounty_respawn` orphan jobs (Q1=B user decision). Filter matches `bounty_id` in payload; 6 regression tests in `TestClearBountiesSchedulerCleanup` cover happy path, 404 already-fired silent success, scheduler-down graceful failure, and tier-filter isolation. `executors/AGENTS.md` updated to lock in the `bounty_id` payload-shape contract. **Orthogonal gap surfaced (separate issue)**: `BountyService.escape_bounty()` writes `bounty.respawn_time` to the DB but no code currently schedules a respawn job from it — flagged for future investigation.
- **Observed**: `/admin_clear_bounties` soft-cleared 4 bounties (`status='cleared'`) and deleted their Discord announcements, but the 4 corresponding `bounty_expire` one-time jobs remained in the APScheduler store:
  ```
  e83b1baf-...  next=+8h  (was bounty id=1)
  1032a935-...  next=+8h  (was bounty id=2)
  d3462dcb-...  next=+8h  (was bounty id=3)
  899fbd22-...  next=+8h  (was bounty id=4)
  ```
  When these fire, they'll attempt to expire bounties that are already `status='cleared'` — log noise at best, confusing errors at worst. Manually deleted via `DELETE /api/v1/jobs/{job_id}` during E2E.
- **Expected**: `clear_bounties` service action should also cancel the linked expire jobs — either by looking up the `bounty_id → job_id` relation, or by broadcasting a bounty-cleared event that `bounty_expire_executor` treats as a no-op.
- **Impact**: Cluttered scheduler state; expire executor wastes cycles; log noise; possible false-positive ERROR log entries.
- **Next step** (deferred post-E2E): In `services/bot-core/src/services/bounty_service.py` (the `clear_bounties` path), after soft-clearing the bounty rows, also enumerate and cancel any `bounty_expire` one-time jobs where `payload.bounty_id` matches. Alternatively, make `bounty_expire_executor` idempotent by early-returning when the referenced bounty is not `status='active'`. Tests should cover the "expire after clear" double-action scenario.

### A.16 — Postgres startup race breaks stack on fresh DB volume
- **Severity**: 🟠 high (infra; blocks fresh-env bring-up every time)
- **Source**: Phase 1 — every fresh stack bring-up on a new DB volume
- **Status**: ✅ **VERIFIED FIXED 2026-04-21** — stack rebuilt from scratch 2026-04-20; retry logic in migration_manager.py confirmed in code; fresh-boot verified clean during Phase 0 cleanliness check.
- **Observed**: After wiping `mappings/postgres-data/` and running `docker compose up -d`, bot-core crashed during `MigrationManager.ensure_current()` with `psycopg2.OperationalError: connection to server at "db" (172.19.0.2), port 5432 failed: Connection refused`. The ENTRYPOINT chains migration → `python main.py` with `&&`, then backgrounds the whole chain with a trailing `& tail -f /dev/null`. When migration fails, the whole chain aborts silently; `tail` keeps the container alive → Docker reports `unhealthy` → gateway + blender (both `depends_on: bot-core: service_healthy`) never leave the `Created` state.
- **Root causes** (three compounding):
  1. **PG healthcheck too weak**: original `pg_isready -d bountydb -U bounty` passes as soon as the Postgres listener socket is open. During first-boot `initdb` on a wiped volume, there's a 1–3 second gap where `pg_isready` returns success but authenticated logins still get `Connection refused`.
  2. **No migration retry**: `migration_manager.ensure_current()` ran `get_current_revision()` exactly once and hard-failed on any `OperationalError`.
  3. **ENTRYPOINT hack masks failure**: `... & tail -f /dev/null` keeps the container alive even when the FastAPI app never starts. **User explicitly chose to keep this** — it's useful for debugging a crashed app. Downstream service dependency chain now relies on the bot-core healthcheck (`GET /api/v1/health/`) failing fast when the app is dead, which it does.
- **Fix applied** (2026-04-19):
  1. **Docker-Compose healthcheck strengthened**: `db` service healthcheck now runs `pg_isready -d bountydb -U bounty && psql -U bounty -d bountydb -c 'SELECT 1' >/dev/null 2>&1` — real authenticated query. Retries bumped 10→20, start_period 20s→30s. Applied to BOTH `docker-compose.yml` AND `docker-compose-gpu.yml`.
  2. **Migration retry loop** added in `services/bot-core/src/persist/database/migration_manager.py`: `ensure_current()` retries `get_current_revision()` up to 5 times with 2s delay between attempts, catching only `sqlalchemy.exc.OperationalError`. The actual Alembic upgrade step is NOT retried (partial-migration safety). 4 new tests in `tests/test_migration_manager.py` cover retry success, max-retry exhaustion, non-retryable-exception propagation, and first-attempt-success baseline.
- **Out-of-scope intentionally**: the `& tail -f /dev/null` ENTRYPOINT hack remains. Container stays alive on app crash; bot-core's healthcheck (curl `/health/`) fails the condition and prevents dependents from starting, which is the correct downstream behavior.
- **Deploy note**: fixes are in code but not live until the stack is rebuilt. When user runs `docker compose up -d --build` on a wiped PG volume, the sequence should be:
  1. `db` comes up, `initdb` runs, `pg_isready` passes but `psql SELECT 1` fails until auth is ready
  2. `db` healthcheck stays in `starting` until both probes pass
  3. `bot-core` begins only after `db` is truly healthy — migration should succeed on first try
  4. If a race still occurs, migration loop retries up to 5× (10s cumulative) before giving up
  5. Downstream (gateway, blender) start once bot-core's `/health/` endpoint returns 200

### A.12 — Checklist Session Setup script contains the A.9 platinum bug
- **Severity**: 🟢 low (doc-only)
- **Source**: Phase 1 Session Setup (2026-04-19)
- **Status**: ✅ **FIXED 2026-04-21** (tied to A.9 resolution) — Session Setup script restored to include `"platinum":20`; A.9 validator now accepts it.
- **Observed**: The `max_bounties_per_tier` payload in the "Session Setup (Run Once at Start)" block at the top of this document includes `"platinum":20`, which fails validation per A.9. Users following the checklist verbatim will see a 400 error on that one call.
- **Expected**: Until A.9 is resolved, the Session Setup script should use only `bronze/silver/gold` keys, with a note linking to A.9. Once A.9 is fixed, restore platinum.
- **Next step** (during E2E close-out): Edit the Session Setup block in this document to drop `platinum` from the payload and add a 1-line inline note pointing to A.9.

### A.17 — CLOSED (not a bug)
- **Observation**: `/admin_setup` embed field `Shops Created: 4` was initially flagged as misleading copy. Confirmed by project owner that "4 shops" correctly refers to the 4 tier-shop containers (Bronze/Silver/Gold/Platinum), each of which holds multiple items. Label is accurate. Closed.

### A.22 — Bounty spawns across all 4 tiers are synchronized (should be randomized)
- **Severity**: 🟡 medium (gameplay UX — loses the "surprise" of bounties appearing at different times)
- **Source**: Phase 1 observation
- **Status**: ✅ **FIXED** (code-verified 2026-04-21) — per-tier spawn jobs now randomized; awaits E2E re-verification during Phase 7
- **Observed**: When `bounty_spawn_default` executor fires, bounties for all 4 tiers (Bronze/Silver/Gold/Platinum) pop at the exact same moment. Expected behavior: each tier should spawn independently on a staggered/randomized cadence within the configured spawn interval, so players checking different boards see bounties appear at different times.
- **Likely location**: `services/bot-core/src/utils/executors/bounty_spawn_executor.py` — probably iterates all tiers in a single executor invocation instead of per-tier jobs or randomized per-tier delays.
- **Possible fix approaches**:
  1. Per-tier scheduled jobs with independent cadences
  2. Single executor but with per-tier random delay (0 to `spawn_interval_minutes`) before each tier spawn
  3. Weighted dice roll per tier per execution (tier might spawn or might not, probabilistically)
- **Next step**: Investigate tomorrow. Design decision needed on which approach feels right. Not blocking.

### A.21 — Shop refresh announcement posted to wrong channel + role mention inside embed (won't ping)
- **Severity**: 🟡 medium (broken notification UX)
- **Source**: Phase 1 live observation
- **Status**: ✅ **FIXED** (code-verified 2026-04-21) — posts to `shop_channel_id` with role mention in `text_content` (pings correctly); awaits E2E re-verification during Phase 4
- **Observed**: When `shop_refresh` executor fires and announces the refresh:
  1. Posted to `#bounty-hunting` instead of `#shop`
  2. The role mention (`@Bounty Hunter` or tier role) is embedded *inside* the embed body, not as plain text preceding the embed. Discord only triggers role notifications for mentions in the plain message content, not inside embed fields. Result: the role is visually mentioned but no one actually gets pinged.
- **Likely location**: `services/bot-core/src/utils/executors/shop_refresh_executor.py` (channel_id resolution + announcement payload construction). May also involve `services/bot-core/src/message_builders/` if shop refresh uses a builder.
- **Fix**:
  1. Route announcement to `guild_config.shop_channel_id`, not `hunting_channel_id`
  2. Emit role mention as the message `content` field (plain text), with the embed as a separate payload attribute
- **Next step**: Investigate tomorrow. Affects Phase 6 (Shop) and Phase 11 (Scheduled jobs/announcements). Not blocking current Phase 2 progress.

### A.20 — /ping visible to non-admins despite A.4 decorators
- **Severity**: 🟢 low (visibility leak only; `is_admin()` runtime check still blocks execution)
- **Source**: Phase 1.5.6 — observed 2026-04-19, **reconfirmed 2026-04-21 on fresh rebuild**
- **Status**: noted, deferred
- **Observed**:
  - Alt (no Administrator permission, `@Bounty Hunter` + `@Bounty Hunter Bronze` only) sees `/ping` in the slash command menu and can invoke it
  - Invocation produces "An error occurred." ephemeral
  - Log trace on 2026-04-21: `ERROR - Error in /ping — discord.app_commands.errors.CheckFailure: The check functions for command 'ping' failed.` (runtime `is_admin()` check blocks execution as designed)
  - **2026-04-21 delta**: scope narrowed — earlier runs had 2 admin commands leaking to non-admins; now only `/ping` remains. All other A.4-decorated commands verified hidden in the same run (`/admin_setup`, `/admin_player`, `/admin_config`, `/scheduler_list`, `/health`, `/load_data`, `/admin_help` — all absent from Alt's autocomplete).
- **Code**: `healthCog.py:25-32` has both `@app_commands.default_permissions(administrator=True)` and `@is_admin()` decorators — matches the pattern used elsewhere.
- **Possible causes**: Discord client cache; per-command sync quirk; decorator ordering nuance specific to `/ping`. Not investigated in depth.
- **Next step**: Re-investigate if/when priority warrants. Not a privilege escalation. Low risk.

### A.19 — Checklist references /register but the command is /profile
- **Severity**: 🟢 low (doc/UX)
- **Source**: Phase 1 item 1.7 runs
- **Status**: ✅ **FIXED 2026-04-21** — alias addition path taken. New `/register` slash command added in `playerCog.py` delegating to shared `_display_profile()` handler. Both `/register` and `/profile` are fully interchangeable. 4 regression tests in `TestRegisterAlias`.
- **Observed**: Checklist click 1.2 says "Main runs `/register`" but no `/register` command exists. The registering UX is `/profile`, which creates-or-gets the player on first invocation. User confirmed `/register` was never implemented as an alias.
- **Decision needed**:
  - (a) Update checklist click 1.2 to say `/profile` — faster, matches current UX
  - (b) Add `/register` as an alias to the `/profile` command — more discoverable for new users (the word "register" is more intuitive than "profile" for "I want to join the game")
- **Next step**: Project owner's call. No blocker.

### A.18 — Shop tier is decorative; under-stocking is chronic (deferred to future enhancement)
- **Severity**: 🟡 medium (gameplay design, not E2E-blocking)
- **Source**: Phase 1.1 live run statistical sampling (2026-04-19)
- **Status**: DEFERRED — tracked outside this checklist as a future enhancement
- **Summary**: `ShopService.refresh_shop()` picks `shop_tech_level` uniformly from 1-9 with no regard to tier. Bronze can stock tech-9 items; Platinum can stock tech-1. Combined with sparse canon turret seed data (10 turrets at only tech levels 5, 6, 9), this produces 90% of refreshes below configured turret minimums. Also: no empty-pool fallback, no validation logging, `plasma_collectors.pe_fusion_h2.json` has NULL `techLevel`.
- **Decision**: Full redesign required (pseudo-banded two-stage probability cascade, per-tier item count defaults, empty-pool fallback, validation logging). Scope is beyond an E2E hotfix.
- **Tracked in**: `/proj/old-refs/session-research-2026-04-20/FUTURE_ENHANCEMENT_SHOP_TIER_REDESIGN.md`
- **Research artifacts**: `/proj/old-refs/session-research-2026-04-20/SHOP_UNDER_STOCKING_DIAGNOSIS.md`, `/proj/old-refs/session-research-2026-04-20/LEGACY_SHOP_TIER_INVESTIGATION.md`
- **E2E impact**: None blocking. Phase 6 shop testing (`/shop`, `/buy`, `/sell`) will still exercise the commands; shops will appear under-stocked and tier-independent, which is acceptable for E2E verification of command plumbing.

---

*Updated: 2026-04-19*
*Based on code review of 48 live slash commands across 12 active cogs + redesign audit*
*Revised: Phase ordering optimized for testing flow; shortcuts added; Skins & Rendering deferred; A.9–A.12 logged during Phase 1 live testing*
