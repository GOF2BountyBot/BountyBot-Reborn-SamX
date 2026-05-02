# BountyBot E2E Test Checklist (Discord)

> **🔁 RESET 2026-04-29** — All 201 test items reset to pending after defect-remediation cycle. Full DB nuke + re-test from Phase 0 required. Reason: ~25 fixes across nearly every cog/service plus B.19 architectural overhaul of player/ship state mechanics, Patches H+I QA hardening (13 findings), and pylint cleanup. Prior pass-state cannot be trusted under the new code paths.
>
> **DB nuke procedure**: stop stack → remove `mappings/postgres-data/` → restart stack → migrations auto-apply on first boot.
>
> **Clean-slate verified 2026-04-29 14:50 UTC**:
> - Containers: `bountybot-db`, `bountybot-bot-core`, `bountybot-discord-gateway`, `bountybot-blender-service` — all healthy
> - Mutable tables empty: `players=0`, `users=0`, `guild_configs=0`, `bounty=0`, `guild_shops=0`, `player_ships=0`, `player_inventories=0`, `admin_audit_logs=0`
> - Seed data present: `ship=65`, `criminal=25`, `system=34`, `item=146`
> - Schema migrations applied (`schema_version_current: true`)
> - Default scheduler jobs registered: `bounty_spawn_default`, `shop_refresh_default`, `temperature_decay_default`

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

## Operating Instructions (Orchestrator)

> 🔴 **CRITICAL**: The ORCHESTRATOR must NOT `docker compose down`, `docker compose up`, `docker compose build`, `docker compose restart`, or otherwise modify running containers. **Only the USER may delete/rebuild/restart the stack.** The orchestrator has `sudo docker exec` access for DB queries, API calls, and log inspection ONLY.

> 🔴 **CRITICAL**: Task delegation roles:
> - **`@researcher`** — read-only investigation, code analysis, DB queries
> - **`@developer`** — code fixes, small changes, running tests
> - **`@architect`** — deep design work, complex refactoring
> - **`@tester`** — adversarial QA, test review, edge case analysis
>
> All subagents have `sudo docker exec` access if needed. The orchestrator delegates — it does not write code directly.

> **DB nuke procedure** (USER must execute): `docker compose down && rm -rf mappings/postgres-data/ && docker compose up --build`

> **🔴 Subagent output note**: Subagents (researcher, developer, tester, architect) will often write their findings to MD files (e.g., `/proj/investigation_activity.md`, `/tmp/<name>.md`) instead of returning text output directly. If a subagent returns empty or no output, check for file-based records before assuming it failed.

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
- [x] **0.3** Confirm no error messages in `docker compose logs` on fresh boot ✅ B.33 fix verified live (re-verified 2026-04-29 18:22 UTC post-redeploy: catalogs loaded 40/30/10/66/65 items, no 404/405)
- [x] **0.4** Confirm health check endpoints respond directly: ✅ bot-core, gateway, blender all return `status: healthy`
   - `curl http://localhost:8000/api/v1/health` (bot-core)
   - `curl http://localhost:7999/api/v1/health` (discord-gateway)
   - `curl http://localhost:8001/api/v1/health/` (blender-service)

---

## Phase 0.5 — Pre-Registration Edge Cases

> ⚠️ **Run BEFORE Phase 1** — these tests require the alt account to NOT yet be registered (no `/profile` run yet by alt user). If the alt account is already registered, skip this phase.

- [x] **0.5.1** (Alt account, unregistered) `/bounties` — ✅ "No active bounties at this time." clean response
- [x] **0.5.2** (Alt account, unregistered) `/shop tier:Bronze` — ✅ Correct "server hasn't been set up" ephemeral (validates A.3 no-auto-create)
- [x] **0.5.3** (Alt account, unregistered) `/unregister` — ⚠️ Shows generic "An error occurred while removing the role" instead of graceful message (A.25, low-priority fix deferred)

### Help discoverability (A.5, unconfigured guild)

- [x] **0.5.4** (Alt account, unregistered) `/help` (no args) — ✅ 8 user categories, DB still 0/0/0 after (no auto-create). **Note**: admin-hint line ("Admins: use /admin_help…") visible to non-admin Alt — verify on Main comparison in Phase 2.5 whether intentional
- [x] **0.5.5** (Alt account, unregistered) `/help category:Bounty Hunting` — ✅ `/bounties`, `/check`, `/criminal-loadout`, `/route` with correct params
- [x] **0.5.6** (Alt account, unregistered) `/help category:bounty hunting` (lowercase) — ✅ Case-insensitive confirmed, identical response
- [x] **0.5.7** (Alt account, unregistered) `/help category:nonsense` — ✅ Lists all 8 valid categories, no DB writes
- [x] **0.5.8** (Alt account, unregistered) `/help ` autocomplete — ✅ 8 user categories, no admin ones

---

## Phase 1 — Guild Setup, Channel Infrastructure & Player Registration

### First-time guild initialisation

- [x] **1.1** `[ADMIN]` `/admin_setup` — "Guild initialized" response; creates: *(Re-verified 2026-04-29 18:36 UTC post-wipe/rebuild: all 8 channels + 6 roles + category + GuildConfig + 4 tier shops (8/10/10/8 = 36 items) created correctly. **A.30 fix verified live** — gateway channel list now returns populated `category_id: 1499192680612233216`. Audit log: `guild_initialize` success uid=402296276617527306. Golden-config applied: spawn 5min/expire 10min/cap 20-per-tier/XP 10-20-30/credits 999,999,999.)*
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

- [x] **1.2** Confirm all 8 channels (7 player-visible + 1 hidden `#bot-images`) exist under the "BountyBot" category in Discord ✅ Re-verified 2026-04-29 18:42 UTC (gateway API confirms 8/8 channels share category_id 1499192680612233216; A.30 fix live)
- [x] **1.3** Confirm `@Bounty Hunter` role exists in the guild role list and is mentionable ✅ Re-verified 2026-04-29 18:42 UTC (plus 4 tier roles — Bronze/Silver/Gold/Platinum — all mentionable per roles API)
- [ ] **1.4** Verify channel permissions: *(Deferred — gateway API does not expose `permission_overwrites`; will be implicitly verified during gameplay phases. Partially verified 2026-04-22 PM via Alt unregister/re-register cycle)*
   - Users WITHOUT `@Bounty Hunter` role CANNOT see any BountyBot channels ✅ Alt's unregister showed channel visibility loss; re-register restored
   - `#bronze-bounty-board`, `#silver-bounty-board`, `#gold-bounty-board`, `#shop` are read-only for `@Bounty Hunter` (cannot type in them) — NOT YET VERIFIED (visual check deferred)
   - `#bounty-hunting` allows `@Bounty Hunter` to use slash commands — ✅ implicitly verified (Alt ran multiple slash commands without being blocked)
   - `#bounty-discussions` allows `@Bounty Hunter` to chat but NOT use slash commands — NOT YET VERIFIED (visual check deferred)
   - `#bot-images` is invisible to all users (only bot can see it) — NOT YET VERIFIED (Main sees it as Admin; Alt's view pending)

### Verify config

- [x] **1.5** `[ADMIN]` `/admin_config action:View Config` — ✅ Re-verified 2026-04-29 18:41 UTC: shows credits=999,999,999, sale_factor=80%, XP thresholds=Silver:10/Gold:20/Platinum:30, admin role ✅, configured ✅. Matches Session Setup exactly. (Note: does NOT include channel IDs in the embed — only role + numeric config. Documentation drift.)
- [x] **1.6** `[ADMIN]` `/admin_config_validate` — ✅ Re-verified 2026-04-29 18:41 UTC: 0 errors, 0 warnings, guild name "bb-temp" correct.

### Player registration (your account)

- [x] **1.7** `/profile` — ✅ Re-verified 2026-04-22 PM: Main (discord_id=402296276617527306, username=samx.ai) created as PID 1. Betty active ship with weapons=["Nirai Impulse EX 1"], modules=["E2 Exoclad","Telta Quickscan"], turrets=[], secondary_weapons=NULL. Inventory: Micro Gun MK I qty=1. Bronze/0 XP/999,999,999 credits. **Observation**: `secondary_weapons` field is NULL not [] — may matter for Phase 5 equip logic; monitor.
- [x] **1.8** Verify BountyBot channels are now visible to you (you have `@Bounty Hunter` role) ✅ Re-verified 2026-04-22 PM (Main + Alt both see all 7 player-visible channels; #bot-images Admin-only via Main client)
- [x] **1.9** `/profile` — Second use: identical response, researcher response, no duplicate player created (idempotent) ✅ Re-verified 2026-04-22 PM: same PID 1, row counts stable at 1/1/1/1

### Unregister / re-register cycle

- [x] **1.10** `/unregister` — ✅ Re-verified 2026-04-22 PM: "Bounty Hunter role(s) removed: @Bounty Hunter, @Bounty Hunter Bronze. Your player data is preserved." Both roles stripped; DB counts preserved at 1/1/1/1; Main had only @everyone after.
- [x] **1.11** `/profile` — ✅ Re-verified 2026-04-22 PM: re-register restores same PID 1, both tier+hunter roles restored; no new DB rows.

### Unregister edge cases

- [x] **1.12** `/unregister` when you don't have the role — ✅ Re-verified 2026-04-22 PM: "ℹ️ You don't have the Bounty Hunter role." — graceful, informational tone. **Important clarification**: registered-then-unregistered user takes this no-role path cleanly, whereas **never-registered** user still hits generic error (A.25). A.25 scope narrower than originally thought.

### `[2P]` Second player registration

- [x] **1.13** `[2P]` Player 2 runs `/profile` — ✅ Re-verified 2026-04-22 PM: Alt (discord_id=970691862035841048, username=general_failure.) created as PID 2 with own Betty (PID2 ship row), comic book Gun MK I in inventory, own credits/px; both tier+hunter roles assigned to Alt. Counts stable at 2/2/2/2.

### `/register` alias (A.19 closure verification — NEW 2026-04-22)

- [x] **1.14** `/register` — ✅ Re-verified 2026-04-22 PM: Alt ran `/register` immediately after `/profile`; returned identical embed (PID 2, Bronze, 0 XP, 999,999,999 credits); zero DB mutations (count stable at 2/2/2/2); no role change. **A.19 closed.**

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
- [ ] **1.5.6** (Non-admin) `/ping` — Invisible (note: A.20 — was previously visible; verify fix still working) *(Reset 2026-04-22 post-wipe; A.20 still open — /ping visible to non-admins; runtime guard rejects but visibility leak remains)*
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
- [x] **2.12** `/list_category category:module tech_level:2` — Filtered by tech level ✅ PASS 2026-04-30 (A.31 fix verified — bug was fixed at some point)

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
- [x] **3.2** `/ship ship_id:<your_betty_id>` — ✅ Re-verified 2026-04-22 PM on Alt (ship_id:2): shows Nirai Impulse EX 1 in Weapons, E2 Exoclad + Telta Quickscan in Modules. **A.28 closed.** **A.34a/b/c remain OPEN** — plus new **B.3** observation: embed shows `Type: Betty` (should be `Class:` or omitted since title already shows ship name).
- [x] **3.3** `/nickname ship_id:<your_ship_id> nickname:MyBetty` — Sets custom nickname; visible in `/ships` *(Reset 2026-04-22 post-wipe; A.34c remains OPEN — same helper as A.34a)*
- [x] **3.4** `/nickname ship_id:<your_ship_id> nickname:<51+ char string>` — Error: nickname too long *(Reset 2026-04-22 post-wipe)*

### Set active ship (tested after buying a second ship in Phase 4)

- [x] **3.5** *(Deferred to after Phase 4)* `/setactive ship_id:<second_ship_id>` — Sets new ship as active
- [x] **3.6** `/ships` — Confirm new active ship indicator

---

## Phase 4 — Shop System

### Browse all shops

- [x] **4.1** `/shops` — ✅ PASS 2026-04-30: Total 38 items, Bronze 9/24 🔓, Silver 9/20 🔒, Gold 12/29 🔒, Platinum 8/18 🔒. Lock/unlock correct for Bronze tier.
- [x] **4.2** `/shop tier:Bronze` — ✅ PASS 2026-04-30: 9 Bronze items across Ships(3)/Primary Weapons(3)/Modules(3). Sections render with proper spacing. Credits show 999,999,999.
- [x] **4.3** `/shop tier:Silver` — ✅ PASS 2026-04-30: Tier-gate error "🔒 You need to be Silver tier"
- [x] **4.4** `/shop tier:Gold` — ✅ PASS 2026-04-30: Tier-gate error. 4-tier gating fully working.

### Purchase items

- [x] **4.5** `/buy item_id:<affordable_item_id> quantity:1` — *(2026-04-27: Three buys verified on Alt — Linear Boost 5,704 (Module), Luna EMP Mk I 5,942 (Primary Weapon), 2x M6 A4 "Raccoon" 1,105,494 (Primary Weapon). All embeds render Item Type with proper spacing post-`823c13d`. Buy embed credits MATCH DB (buy path uses local var — no doubling bug, unlike /sell).)*
- [x] **4.6** `/profile` — *(2026-04-27 verified twice on Alt: credits 993,837,989 post-/sell then 992,732,495 post-/buy 1,105,494. Both match DB exactly. Tier displays "Bronze" cleanly. Note: profile credits show DB truth, NOT the inflated value the buggy `/sell` embed displayed — confirms response-body bug isolated to /sell embed only.)*
- [x] **4.7** `/inventory` — *(2026-04-27 verified on Alt: shows "Modules (1) Linear Boost", "Primary Weapons (1) Luna EMP Mk I" with correct field name spacing post-`823c13d`. Total Items: 2, summary line "Ships: 0 | Weapons: 1 | Modules: 1 | Turrets: 0" correct.)*

### Purchase error cases

- [ ] **4.8** `/buy` insufficient stock — Defer (no low-stock Bronze item to test with current shop)
- [x] **4.9** `/buy item_id:999999` — ✅ PASS 2026-04-30: "❌ Item not found in shop."

### Sell items

- [x] **4.10** `/sell item:<owned_item> quantity:1` — *(2026-04-27 verified on Alt: 64MJ Railgun sold for 15,343 — A.42 + A.44 closed end-to-end in Discord. Item Type renders "Primary Weapon" with space (B.6 closed). HOWEVER: response embed showed New Credits = 993,853,332 but DB showed 993,837,989 (off by exactly +15,343 = one extra sale price). DOUBLED-CREDITS BUG: shop_service.sell_item:514 read player.credits + total_sell_value AFTER update_credits() refreshed the identity-mapped instance. Refactored in commit `c8b5fef` (Option B — Core UPDATE → ORM setattr). **Stack rebuilt 2026-04-28 — fix now live; awaiting Discord re-test to confirm embed credits match DB exactly.**)*
- [x] **4.11** `/sell item_name:<item_you_dont_own>` — *(2026-04-27 verified on Alt: "❌ Item '<not-owned>' not found in player 2's inventory". Functional. Cosmetic observation: error leaks numeric player_id (2) — could read "in your inventory" instead. Logged as B.7.)*

### Now complete Phase 3 ship management

- [x] **4.12** *(If a second ship was purchased)* Return to **3.5** and **3.6** to test `/setactive` — *(2026-04-27 partially verified on Alt: `/setactive ship_id:3` set Cormorant active. DB confirms ship_id=3 is_active=true, ship_id=4 (Ghost) is_active=false, players.active_ship_id=3. Embed shows "🟢 Active" status. **Observation O.1**: autocomplete dropdown did not populate when invoking — user manually typed "3". Bot-core API responded correctly in ~6ms with both ships; cause likely Discord client-side caching or silent exception in cog autocomplete helper. Logged O.1 for investigation.)*

---

## Phase 5 — Inventory & Equipment

### View inventory

- [x] **5.1** `/inventory` — *(2026-04-27 verified on Alt during Phase 4.7: shows Modules (1) and Primary Weapons (1) with proper field-name spacing. Summary "Ships: 0 | Weapons: 1 | Modules: 1 | Turrets: 0" aggregates correctly. D1 + A.43 + A.46 all closed end-to-end. See test 4.7 for full embed text.)*
- [x] **5.2** `/inventory item_type:Primary Weapon` — ✅ 2026-05-01: shows "Primary Weapons (3)" filtered section
- [x] **5.3** `/search query:Raccoon` — ✅ 2026-05-01: shows "Found 1 matching items" → M6 A4 "Raccoon"
- [x] **5.4** `/search query:ZZZZZZZZ` — ✅ 2026-05-01: "No items found matching"
- [ ] **5.5** `/item item_name:Micro Gun MK I item_type:Weapon` — ✅ Re-verified 2026-04-22 PM on Alt: "📦 Micro Gun MK I / Type: Weapon / Quantity Owned: 1 / Status: ✅ Owned". **A.36 closed.** **But A.39 surfaced**: mismatched `item_type` (e.g. specifying Ship for a weapon) returns "Not Owned" rather than helpful cross-type error. User deferred A.39 to post-release (remove `item_type` param + use item emoji in title).

### Equip weapons/modules

- [x] **5.6** `/equip item_name:<weapon_name>` — *(2026-04-22 PM A.37 closed via swap flow. **Un-marked 2026-04-25** because autocomplete labels in inventoryCog.py:604 were updated by `3e73940` (concrete vocab + `.replace('_',' ').title()`). **Stack rebuilt 2026-04-28 — `3e73940` now live; awaiting Discord re-test of equip autocomplete labels (expected "(Primary Weapon)" not "(Primary_Weapon)") and swap flow.**)*
- [x] **5.7** `/ship ship_id:<active_ship_id>` — Confirm weapon appears in loadout
- [x] **5.8** `/equip item_name:E2 Exoclad` (no equipment_type) — ✅ 2026-05-01: "Item not found in inventory" (already equipped)
- [x] **5.9** `/equip item_name:Matador TS` (no equipment_type) — ✅ 2026-05-01: B.43 error "0 turrets" (Specter)

### Unequip

- [x] **5.10** `/unequip item_name:Ridil Blaster` — ✅ 2026-05-01: "Item Unequipped" moved to inventory
- [x] **5.11** `/ship ship_id:6` — ✅ 2026-05-01: Shows "Primary Weapons <2/4>" with Raccoon x2 only

### Equipment error cases

- [x] **5.12** `/equip item_name:<weapon> equipment_type:Weapon` repeatedly until primary slots full — Prompts swap when slots full (B.47 fix verified)
- [x] **5.13** `/unequip item_name:<item_not_equipped> equipment_type:Weapon` — Error: item not equipped
- [x] **5.14** `/equip item_name:<item_not_in_inventory> equipment_type:Weapon` — Error: item not found in inventory

### Admin inventory view

- [x] **5.15** `[ADMIN]` `/inventory user:@player2` — Admin can view another player's inventory

---

## Phase 6 — Player Progression & Tiers `[ADMIN]`

Use admin commands to fast-track progression testing.

### XP and tier advancement

Tier thresholds (lowered for fast testing via Session Setup): Bronze (0 XP) → Silver (10 XP) → Gold (20 XP) → Platinum (30 XP)

> ⚡ **Shortcut**: If you didn't run the Session Setup commands yet, run `/admin_config_xp action:Update silver:10 gold:20 platinum:30` now to lower thresholds before testing tier advancement.

- [x] **6.1** `[ADMIN]` `/admin_player user:@you action:Set XP xp:15` — Sets XP; confirmation embed shows old/new XP and tier change (Bronze -> Silver)
- [x] **6.2** `/profile` — XP shows 15; tier shows Silver
- [x] **6.3** `/shop tier:Silver` — Silver-tier items now accessible
- [x] **6.4** `/shop tier:Gold` — Gold-tier items still locked (player is Silver)

### Credits management

- [x] **6.5** `[ADMIN]` `/admin_player user:@you action:Add Credits credits:50000` — Adds credits; shows amount added + new total
- [x] **6.6** `/profile` — Credits reflect the addition
- [x] **6.7** `[ADMIN]` `/admin_player user:@you action:Set Credits credits:1000` — Sets credits to exact value
- [x] **6.8** `/profile` — Credits show exactly 1,000

### Admin player inspection

- [x] **6.9** `[ADMIN]` `/admin_player user:@player2 action:View Stats` — Shows Player 2's full stats (tier, XP, credits, lifetime credits, prestige count)
- [x] **6.10** `[ADMIN]` `/admin_player user:@SamAccountX action:Reset Player` — ✅ 2026-05-01: reset Main's XP/credits
- [x] **6.11** `/profile` — ✅ 2026-05-01: confirms reset state (8 items in inventory after reset)

### Prestige

> 🔁 **B.48 RE-VERIFY REQUIRED post-refactor** for 6.12-6.15. The prestige flow is being refactored to use a configurable per-guild `Prestige` XP threshold (instead of hardcoded `level == 10` / 1,000,000 XP). After refactor:
> - 6.12 — Re-verify Set XP embed shows ONLY tier (no level field leak from any new code path)
> - 6.13 — Re-verify warning embed text (no "level 10" reference; should reference configurable prestige threshold)
> - 6.14 — UNBLOCKED post-refactor (currently blocked: requires 1,000,000 XP under old level system)
> - 6.15 — UNBLOCKED post-refactor (depends on 6.14)
> - **NEW 6.18** — `/admin_config View Config` shows Prestige threshold in XP Thresholds section (Silver/Gold/Platinum/**Prestige**)
> - **NEW 6.19** — `/admin_config_xp action:Update prestige:N` sets the prestige threshold (or equivalent admin command — architect to specify)
> - **NEW 6.20** — `/prestige` error message when ineligible references the configured prestige XP threshold, NOT "level 10"
> - **NEW 6.21** — Prestige confirmation embed shows "Previous Tier" (NOT "Previous Level")

- [x] **6.12** `[ADMIN]` `/admin_player user:@you action:Set XP xp:35` — Max out XP to reach Platinum (above threshold of 30) — **RE-VERIFY post-B.48**
- [x] **6.13** `/prestige` (without confirm) — Should show orange warning embed explaining what prestige does (reset to Bronze, keep ships/credits, gain prestige star) — **RE-VERIFY post-B.48: text must not reference "level"**
- [ ] **6.14** `/prestige confirm:CONFIRM` — Resets level to 0, tier to Bronze, clears inventory; increments prestige_count; preserves ships and lifetime stats — **BLOCKED on B.48** (requires unattainable 1M XP under current level system)
- [ ] **6.15** `/profile` — Shows prestige count, reset XP/tier, preserved lifetime stats — **BLOCKED on B.48** (depends on 6.14)
- [ ] **6.18** *(NEW post-B.48)* `[ADMIN]` `/admin_config action:View Config` — Verify XP Thresholds section includes Prestige threshold alongside Silver/Gold/Platinum
- [ ] **6.19** *(NEW post-B.48)* `[ADMIN]` `/admin_config_xp action:Update prestige:50` (or architect-specified equivalent) — Sets configurable prestige threshold per guild
- [ ] **6.20** *(NEW post-B.48)* `/prestige` while ineligible — Error message references the configured prestige XP threshold, NOT a hardcoded level concept
- [ ] **6.21** *(NEW post-B.48)* `/prestige confirm:CONFIRM` after eligible — Confirmation embed shows "Previous Tier: Platinum" (NOT "Previous Level: 10")

### Leaderboard

- [x] **6.16** `/leaderboard` — ✅ 2026-05-01: shows top 10 by XP with tier/ranks
- [x] **6.17** `/leaderboard tier:Silver` — ✅ 2026-05-01: filters to Silver tier (autocomplete fix pending rebuild)

---

## Phase 7 — Bounty Hunting (Core Gameplay)

> Note: Bounties spawn via APScheduler (`bounty_spawn_default` job). Session Setup compressed the interval to 5 minutes.
> Use `/admin_spawn_bounty` to spawn immediately rather than waiting.

### Wait for bounty spawn

- [x] **7.1** `/bounties` — Lists active bounties. ✅ PASS.
- [x] **7.2** `/bounties division:bronze` — Filter by division. ✅ PASS.
- [x] **7.3** Scheduler fires correctly. ✅ PASS — bounties spawn every 5 min.

### Verify bounty announcement

- [x] **7.4** Per-division board channel embeds. ✅ PASS — faction color, title, Difficulty, Reward Pool, Bounty Ends, Loadout, Route, Checked Systems, footer, @Bounty Hunter mention.
- [x] **7.5** Route map image embedded. ✅ PASS.

### Investigate a bounty

- [x] **7.6** `/route bounty:<id>` — System route with indicators. ✅ PASS.
- [x] **7.7** `/criminal-loadout bounty:<id>` — Ship, weapons, modules. ✅ PASS.

### Hunt the bounty

- [x] **7.8** `/check system:<wrong>` — "System Checked — not here." ✅ PASS (Alt, multi-bounty overlap).
- [x] **7.9** `/check system:<same_wrong>` — "Already checked." ✅ PASS.
- [x] **7.10** `/check system:<correct>` — Capture! ✅ PASS — Alvar Julen #2, +4,308cr, +430 XP.
- [x] **7.11** `/profile` — Credits + XP match DB. ✅ PASS.
- [x] **7.12** `/bounties` — Resolved bounty removed. ✅ PASS.
- [x] **7.13** Announcement deleted from board channel. ✅ PASS.

### Bounty edge cases

- [x] **7.14** `/check` with no active bounties in division — Error. ✅ PASS.
- [x] **7.15** Cooldown after check — countdown timer. ✅ PASS (~26s).
- [x] **7.15b** `/admin_cooldown_reset` — Immediate reset. ✅ PASS.
- [x] **7.15c** RNG combat variance ~±5%. ✅ PASS.
- [x] **7.16** Multi-bounty overlap — 1 check → 3 responses. ✅ PASS (Alt).

### Bounty self-cleanup (monitor)

- [x] **7.18** 20-cycle monitor. ✅ PASS — 6 unique bounties, ~7 min avg lifespan, all cleaned correctly.

### `[2P]` Bounty competition

- [ ] **7.17** 2P race — only first correct wins. NOT YET TESTED.

### B.48 sanity checks (post-refactor)

- [ ] **7.19** *(NEW post-B.48)* Successful `/check` on a bounty — verify reward embed/announcement contains NO "Level Up!" string and NO `level_before`/`level_after`/`leveled_up` references. If `tier_changed` is implemented as the replacement, verify it surfaces correctly when the player has crossed a tier-up XP threshold but has not yet `/promote`d.
- [ ] **7.20** *(NEW post-B.48)* Run `/admin_player Set XP` to a value below the player's current tier threshold — confirm tier remains unchanged (orphan-tier behavior preserved unless architect declares this a separate defect to fix).

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

**RESET 2026-04-28** — Full re-test required from Phase 0. Reason: defect-remediation cycle landing ~25 fixes across nearly every cog and service, including B.19 architectural overhaul of player_service starter loadout, shop_service.purchase_ship, prestige flow, ships.py equip/unequip transactional boundaries, and set_active_ship reconciliation. Full DB nuke + re-test from square 1 is the proper validation strategy. All previously-passed test items reset to pending.

| Phase | Total | Passed | Partial | Pending | Notes |
|-------|-------|--------|---------|---------|-------|
| 0 — Stack Health | 4 | 0 | 0 | 4 | RESET — full re-test required |
| 0.5 — Pre-Registration Edge Cases | 8 | 0 | 0 | 8 | RESET |
| 1 — Setup, Channels & Registration | 15 | 0 | 0 | 15 | RESET; 1 needs `[2P]` |
| 1.5 — Non-Admin Permission Denials | 10 | 0 | 0 | 10 | RESET; A.20 closed (Discord-side, not code) |
| 2 — Game Data | 14 | 0 | 0 | 14 | RESET |
| 2.5 — Help Discoverability | 17 | 0 | 0 | 17 | RESET |
| 3 — Ship Management | 6 | 0 | 0 | 6 | RESET — covers B.19 fixes |
| 4 — Shop | 12 | 0 | 0 | 12 | RESET — covers B.19 + B.31a fixes |
| 5 — Inventory & Equipment | 15 | 0 | 0 | 15 | RESET — covers B.19 + B.2 fixes |
| 6 — Progression & Tiers | 17 | 0 | 0 | 17 | RESET — covers B.17 fix |
| 7 — Bounty Hunting | 17 | 0 | 0 | 17 | RESET — covers B.23 + B.24 + B.25 fixes |
| 8 — Dueling | 11 | 0 | 0 | 11 | RESET; ALL `[2P]` |
| 9 — Scheduler Administration | 6 | 0 | 0 | 6 | RESET — covers B.27/B.28/B.29/B.30 fixes |
| 10 — Dev Tools | 3 | 0 | 0 | 3 | RESET |
| 11 — Scheduled Jobs | 6 | 0 | 0 | 6 | RESET — covers B.23 reliability fixes |
| 11.5 — Invalid Input Edge Cases | 5 | 0 | 0 | 5 | RESET |
| 12 — Admin Ops & Audit | 26 | 0 | 0 | 26 | RESET — covers B.31a + B.32 + B.31b fixes |
| DEFERRED — Skins & Rendering | (15) | — | — | — | Requires blender-service + GPU; excluded from active count |
| **TOTAL (active)** | **192** | **0** | **0** | **192** | 0% — full re-test pending fix-cycle completion |

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

**8 channels total** (7 player-visible + 1 hidden):

| Channel | Permission | Purpose |
|---------|-----------|---------|
| `#bronze-bounty-board` | Read-only (players can view, not type) | Bronze division bounty announcements |
| `#silver-bounty-board` | Read-only | Silver division bounty announcements |
| `#gold-bounty-board` | Read-only | Gold division bounty announcements |
| `#platinum-bounties` | Read-only (players can view, not type) | Platinum division bounty announcements |
| `#shop` | Read-only | Shop refresh announcements |
| `#bounty-hunting` | Interactive (slash commands + chat) | Gameplay commands |
| `#bounty-discussions` | Chat-only (NO slash commands) | Player discussion |
| `#bot-images` | Hidden (bot-only) | Route map image hosting |

> **⚠️ Naming asymmetry (A.10)**: The three lower-tier channels follow the pattern `#<tier>-bounty-board`,
> but the platinum channel is named `#platinum-bounties` (not `#platinum-bounty-board`). This is a known
> cosmetic inconsistency in the seed infrastructure; changing it would require a migration or guild
> re-setup so it is documented here rather than fixed.

**6 roles total** (5 player-facing + 1 admin):

- `@Bounty Hunter` — Assigned by `/profile`, removed by `/unregister`. Controls visibility of all BountyBot channels.
- `@Bounty Hunter Bronze` — Assigned on Bronze tier. Controls visibility of `#bronze-bounty-board`.
- `@Bounty Hunter Silver` — Assigned on Silver tier. Controls visibility of `#silver-bounty-board`.
- `@Bounty Hunter Gold` — Assigned on Gold tier. Controls visibility of `#gold-bounty-board`.
- `@Bounty Hunter Platinum` — Assigned on Platinum tier. Controls visibility of `#platinum-bounties`.
- `BountyBot Admin` — Created if no admin role specified. Grants admin command access. (Note: role name is "BountyBot Admin", not "BountyBot Admins".)

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
## Appendix E — Defects & Anomalies

Moved to **`DEFECTS.md`** (2026-04-28). All open, deferred, fixed, and closed defects live there as the single source of truth. Add new defect entries to `DEFECTS.md`, not here. Reference IDs (e.g. A.31, B.7, O.1) remain stable across both files.

---

*Updated: 2026-04-28*
*Based on code review of 48 live slash commands across 12 active cogs*
*Defects log extracted to DEFECTS.md*
