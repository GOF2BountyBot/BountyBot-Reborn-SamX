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
# NOTE: `platinum` key intentionally omitted — see Appendix E A.9/A.12. Platinum tier is
#       spawned by the executor but the config writer's validator rejects it. Restore
#       `"platinum":20` once A.9 is fixed.
sudo docker exec bountybot-bot-core curl -s -X PUT -H 'Content-Type: application/json' \
  "http://localhost:8000/api/v1/config/guild/$GID/bounty" \
  -d "{\"guild_id\":$GID,\"bounty_spawn_interval_minutes\":5,\"bounty_expiry_minutes\":10,\"max_bounties_per_tier\":{\"bronze\":20,\"silver\":20,\"gold\":20}}"

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

- [x] **1.1** `[ADMIN]` `/admin_setup` — "Guild initialized" response; creates: ✅ PASS 2026-04-21 (see A.10 for checklist-vs-reality deltas)
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

- [ ] **1.2** Confirm all 7 text channels exist under the "BountyBot" category in Discord
- [ ] **1.3** Confirm `@Bounty Hunter` role exists in the guild role list and is mentionable
- [ ] **1.4** Verify channel permissions:
   - Users WITHOUT `@Bounty Hunter` role CANNOT see any BountyBot channels
   - `#bronze-bounty-board`, `#silver-bounty-board`, `#gold-bounty-board`, `#shop` are read-only for `@Bounty Hunter` (cannot type in them)
   - `#bounty-hunting` allows `@Bounty Hunter` to use slash commands
   - `#bounty-discussions` allows `@Bounty Hunter` to chat but NOT use slash commands
   - `#bot-images` is invisible to all users (only bot can see it)

### Verify config

- [x] **1.5** `[ADMIN]` `/admin_config action:View Config` — Shows guild configuration embed including channel IDs, admin role, starting credits, sale price factor, XP thresholds ✅ PASS 2026-04-21 (Starting Credits 999,999,999; Sale Factor 80%; XP Silver:10 Gold:20 Platinum:30 all reflect Session Setup)
- [x] **1.6** `[ADMIN]` `/admin_config_validate` — Returns "valid: true" with no errors or warnings ✅ PASS 2026-04-21

### Player registration (your account)

- [x] **1.7** `/profile` — First use: creates User + Player with starter ship "Betty" (active, `is_active=true`), equipped loadout: `Nirai Impulse EX 1` (primary weapon), `E2 Exoclad` (module), `Telta Quickscan` (module); inventory/cargo contains `Micro Gun MK I` (unequipped spare primary). Bronze tier, 0 XP, starting credits per guild config. **Also assigns `@Bounty Hunter` + `@Bounty Hunter Bronze` roles to the user.** [Note: secondary weapons not yet implemented; no turret on starter.] ✅ PASS 2026-04-21 (all backend state verified: users/players/player_ships/player_inventories all correct; roles assigned; credits=999,999,999 per Session Setup)
- [x] **1.8** Verify BountyBot channels are now visible to you (you have `@Bounty Hunter` role) ✅ PASS 2026-04-21 (all 7 player-visible channels + `#platinum-bounties` present; `#bot-images` also visible to Main — expected, since Discord's Administrator permission bypasses channel-level view denials; non-admin users will not see it)
- [x] **1.9** `/profile` — Second use: identical response, no duplicate player created (idempotent) ✅ PASS 2026-04-21 (embed identical; users/players/player_ships/player_inventories all still = 1)

### Unregister / re-register cycle

- [x] **1.10** `/unregister` — Ephemeral "Bounty Hunter role(s) removed: @Bounty Hunter, @Bounty Hunter Bronze. Your player data is preserved." Verify:
   - Both `@Bounty Hunter` and tier role (Bronze) removed from your user (A.14)
   - BountyBot channels are no longer visible to you
   ✅ PASS 2026-04-21 (exact ephemeral match; both roles stripped from Main; player data preserved — all 4 tables still = 1 row each; single clean INFO log line)
- [x] **1.11** `/profile` — Re-assigns both roles; channels visible again; same player data as before ✅ PASS 2026-04-21 (same embed as 1.9; both roles reassigned; DB unchanged — no resets, no duplicates)

### Unregister edge cases

- [x] **1.12** `/unregister` when you don't have the role — "You don't have the Bounty Hunter role" message ✅ PASS 2026-04-21 (exact ephemeral "ℹ️ You don't have the Bounty Hunter role."; no DB mutations; no errors. Confirms that the configured-guild path handles the noop correctly — A.25 is specifically about the unconfigured-guild path.)

### `[2P]` Second player registration

- [x] **1.13** `[2P]` Player 2 runs `/profile` — Creates their own User + Player with separate state; assigns `@Bounty Hunter` + `@Bounty Hunter Bronze` roles to Player 2 ✅ PASS 2026-04-21 (Alt = Player ID 2, separate from Main's ID 1; identical starter state (Betty + Nirai Impulse EX 1 + 2 modules + spare Micro Gun); both roles assigned; Main's state unchanged; channels visible to Alt)

> Note: `/profile` takes no parameters — it always shows the invoking user's profile. There is no way to view another player's profile via `/profile`. Use `/admin_player user:@player action:View Stats` (admin) to inspect other players.

---

## Phase 1.5 — Non-Admin Permission Denials

> ⚠️ **Run AFTER Phase 1** — the alt account must be registered (has `@Bounty Hunter` role) but must NOT have admin permissions.

- [x] **1.5.1** (Non-admin) `/admin_setup` — Command should be invisible in slash menu (A.4 hiding) ✅ PASS 2026-04-21 (not in Alt's autocomplete)
- [x] **1.5.2** (Non-admin) `/admin_player user:@someone action:View Stats` — Invisible ✅ PASS 2026-04-21
- [x] **1.5.3** (Non-admin) `/admin_config action:View Config` — Invisible ✅ PASS 2026-04-21
- [x] **1.5.4** (Non-admin) `/scheduler_list` — Invisible ✅ PASS 2026-04-21
- [x] **1.5.5** (Non-admin) `/health` — Invisible ✅ PASS 2026-04-21
- [~] **1.5.6** (Non-admin) `/ping` — Invisible (note: A.20 — was previously visible; verify fix still working) ⚠️ 2026-04-21 still visible/invokable to Alt; runtime `is_admin()` rejects with "An error occurred." — **A.20 updated** (scope narrowed from 2 commands leaking to just `/ping`)
- [x] **1.5.7** (Non-admin) `/load_data category:ship` — Invisible ✅ PASS 2026-04-21
- [x] **1.5.8** (Non-admin) `/admin_help` — Invisible (validates A.4 decorator on `/admin_help`) ✅ PASS 2026-04-21
- [x] **1.5.9** (Non-admin) Open the slash-command menu and start typing `/admin` — autocomplete does NOT surface any `admin_*`, `scheduler_*`, `ping`, `health`, `load_data`, `reload_autocomplete`, `render_config`, or `render_cache_clear` entries. `/admin_help` itself must also be absent. ✅ PASS 2026-04-21 (empty dropdown on `/admin ` for Alt)
- [x] **1.5.10** (Non-admin) `/help` — Still works and still shows only the 8 user categories (no Admin — * categories leak) ✅ PASS 2026-04-21 (all 8 correct categories; no admin-category entries. Footer line "Admins: use /admin_help to see admin commands" shown to non-admin — awaiting user decision on logging)

---

## Phase 2 — Game Data Browsing

Verify seeded data is accessible via the aboutCog. All of this is read-only.

### Object lookup

- [x] **2.1** `/about category:ship name:Betty` — Returns detailed ship embed (armour, cargo, compatible skins, manufacturer, tech level) ✅ PASS 2026-04-21 (ran on Alt)
- [x] **2.2** `/about category:primary_weapon name:Micro Gun MK I` — Returns weapon stats ✅ PASS 2026-04-21
- [x] **2.3** `/about category:module name:Telta Quickscan` — Returns module stats ✅ PASS 2026-04-21
- [x] **2.4** `/about category:ship name:nonexistent_ship` — Error: not found ✅ PASS 2026-04-21 (ephemeral error returned — non-public; no INFO success logged)

### Category listing

- [~] **2.5** `/list_category category:ship` — Paginated list of all seeded ships (capped at 50) ⚠️ 2026-04-21 (A.26 + A.27: formatting breaks with duplicate "Objects" headers; 50/65 silently dropped due to hard cap)
- [~] **2.6** `/list_category category:primary_weapon` — Lists primary weapons ⚠️ 2026-04-21 (A.26 formatting, though count=40 ≤ 50 so no truncation)
- [~] **2.7** `/list_category category:secondary_weapon` — Lists secondary weapons ⚠️ 2026-04-21 (data present — count=30 — but feature not usable per A.2; A.26 formatting applies)
- [~] **2.8** `/list_category category:turret_weapon` — Lists turret weapons ⚠️ 2026-04-21 (count=10, single field so no A.26 issue; no A.27 truncation)
- [~] **2.9** `/list_category category:module` — Lists all modules ⚠️ 2026-04-21 (count=66; A.26 applies — 3 chunks titled "Objects"; A.27 partial — 50/66 shown)
- [~] **2.10** `/list_category category:criminal` — Lists NPC criminals ⚠️ 2026-04-21 (count=25; A.26 formatting)
- [~] **2.11** `/list_category category:system` — Lists all star systems ⚠️ 2026-04-21 (count=34; A.26 formatting)
- [~] **2.12** `/list_category category:module tech_level:2` — Filtered by tech level ⚠️ 2026-04-21 (also tested tech_level=8 and tech_level=1 — all filtered correctly; A.26 formatting)

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

- [ ] **3.1** `/ships` — Shows your owned ships (starter ship "Betty" should be present, marked as active with green indicator)
- [ ] **3.2** `/ship ship_id:<your_betty_id>` — Detailed view of your ship: loadout (weapons, modules, turrets), stats

### Ship operations

- [ ] **3.3** `/nickname ship_id:<your_ship_id> nickname:MyBetty` — Sets custom nickname; visible in `/ships`
- [ ] **3.4** `/nickname ship_id:<your_ship_id> nickname:<51+ char string>` — Error: nickname too long

### Set active ship (tested after buying a second ship in Phase 4)

- [ ] **3.5** *(Deferred to after Phase 4)* `/setactive ship_id:<second_ship_id>` — Sets new ship as active
- [ ] **3.6** `/ships` — Confirm new active ship indicator

---

## Phase 4 — Shop System

### Browse all shops

- [ ] **4.1** `/shops` — Overview of all 4 tier shops (Bronze/Silver/Gold/Platinum) with item counts and lock/unlock status based on player tier
- [ ] **4.2** `/shop tier:Bronze` — Shows items available at Bronze tier with prices, quantities, and tech levels
- [ ] **4.3** `/shop tier:Silver` — Tier-locked: "insufficient tier" error (player is Bronze at this point)
- [ ] **4.4** `/shop tier:Bronze item_type:ship` — Filtered to ships only

### Purchase items

- [ ] **4.5** `/buy item_id:<affordable_item_id> quantity:1` — Purchase an item; credits deducted, confirmation embed
- [ ] **4.6** `/profile` — Verify credits decreased by item price
- [ ] **4.7** `/inventory` — Verify purchased item appears

### Purchase error cases

- [ ] **4.8** `/buy item_id:<expensive_item_id>` — Attempt to buy something you can't afford; error: "insufficient credits"
- [ ] **4.9** `/buy item_id:999999` — Nonexistent item; error: not found

### Sell items

- [ ] **4.10** `/sell item_name:<owned_item> item_type:weapon quantity:1 target_tier:Bronze` — Sell an item; credits increase (refund = value * sale_price_factor), item removed from inventory
- [ ] **4.11** `/sell item_name:<item_you_dont_own> item_type:weapon` — Error: item not in inventory

### Now complete Phase 3 ship management

- [ ] **4.12** *(If a second ship was purchased)* Return to **3.5** and **3.6** to test `/setactive`

---

## Phase 5 — Inventory & Equipment

### View inventory

- [ ] **5.1** `/inventory` — Shows all owned items (starter equipment + purchases), grouped by type
- [ ] **5.2** `/inventory item_type:weapon` — Filtered to weapons only
- [ ] **5.3** `/search query:Micro` — Search inventory by partial name; finds "Micro Gun MK I"
- [ ] **5.4** `/search query:nonexistent` — No results message
- [ ] **5.5** `/item item_name:Micro Gun MK I item_type:weapon` — Shows quantity owned

### Equip weapons/modules

- [ ] **5.6** `/equip item_name:<weapon_name> equipment_type:Weapon` — Equip a weapon to active ship
- [ ] **5.7** `/ship ship_id:<active_ship_id>` — Confirm weapon appears in loadout
- [ ] **5.8** `/equip item_name:<module_name> equipment_type:Module` — Equip a module to active ship
- [ ] **5.9** `/equip item_name:<turret_name> equipment_type:Turret` — Equip a turret (if ship has turret slots)

### Unequip

- [ ] **5.10** `/unequip item_name:<weapon_name> equipment_type:Weapon` — Remove weapon from ship
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
| **playerCog** | `/profile`, `/leaderboard`, `/prestige`, `/unregister` |
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
- **Status**: open — **should be fixed alongside A.26**
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
- **Status**: open
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
- **Source**: Phase 0.5.2 + Developer investigation 2026-04-18
- **Status**: open — **bigger bug than initially thought**
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
- **Source**: Post-nuke stack relaunch inspection
- **Status**: open
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

### A.5 — Missing: Help command set (feature request)
- **Severity**: 🟡 medium (feature gap, not a bug)
- **Source**: User-reported during Phase 0.5 session
- **Status**: open (feature request, post-E2E)
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
- **Severity**: 🟠 high
- **Source**: User-reported during Phase 0.5 session
- **Status**: open (needs follow-up audit)
- **Observed**: Commands gated by `@is_admin()` still appear in the slash-command dropdown for non-admin users. When a non-admin attempts to invoke, they get an ephemeral "permission denied" error — but the commands are discoverable/visible in the first place.
- **Expected**: Admin-only commands should be invisible (or at least hidden by default) to users without admin permissions — via Discord's `default_permissions` / `default_member_permissions` on the app command decorators.
- **Impact**: Information leakage (shows non-admins what admin tools exist), clutter in the slash-command UI, and poor UX (users can attempt commands they can't use).
- **Examples to audit**: `/load_data`, `/reload_autocomplete`, `/ping`, `/health`, all `/admin_*`, all `/scheduler_*`.
- **Next step** (deferred post-E2E): Audit every cog file in `services/discord-gateway/src/cogs/` for `@is_admin()` usage without matching `default_permissions` / `default_member_permissions` on the `@app_commands.command` decorator. Investigate whether the codebase uses discord.py's permission-based command visibility or relies purely on runtime check rejection. File updates likely needed in `healthCog.py`, `devCog.py`, `adminCog.py`, `schedulerCog.py`.

### A.9 — Bounty config validator rejects `platinum` tier while spawner produces platinum bounties
- **Severity**: 🟠 high (internal inconsistency — writer/reader tier lists disagree)
- **Source**: Session Setup during Phase 1 (2026-04-19)
- **Status**: open (deferred post-E2E)
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
- **Status**: observed during E2E; partially overlaps with A.6 "Next steps #4" (orphaned one-time jobs)
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
- **Source**: Phase 1 rebuild attempt (2026-04-19)
- **Status**: **FIXED in code** (needs rebuild to apply); see "Fix" below
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
- **Source**: Phase 1 Session Setup run (2026-04-19)
- **Status**: open (doc fix)
- **Observed**: The `max_bounties_per_tier` payload in the "Session Setup (Run Once at Start)" block at the top of this document includes `"platinum":20`, which fails validation per A.9. Users following the checklist verbatim will see a 400 error on that one call.
- **Expected**: Until A.9 is resolved, the Session Setup script should use only `bronze/silver/gold` keys, with a note linking to A.9. Once A.9 is fixed, restore platinum.
- **Next step** (during E2E close-out): Edit the Session Setup block in this document to drop `platinum` from the payload and add a 1-line inline note pointing to A.9.

### A.17 — CLOSED (not a bug)
- **Observation**: `/admin_setup` embed field `Shops Created: 4` was initially flagged as misleading copy. Confirmed by project owner that "4 shops" correctly refers to the 4 tier-shop containers (Bronze/Silver/Gold/Platinum), each of which holds multiple items. Label is accurate. Closed.

### A.22 — Bounty spawns across all 4 tiers are synchronized (should be randomized)
- **Severity**: 🟡 medium (gameplay UX — loses the "surprise" of bounties appearing at different times)
- **Source**: Observed during Phase 2 testing (2026-04-19)
- **Status**: open
- **Observed**: When `bounty_spawn_default` executor fires, bounties for all 4 tiers (Bronze/Silver/Gold/Platinum) pop at the exact same moment. Expected behavior: each tier should spawn independently on a staggered/randomized cadence within the configured spawn interval, so players checking different boards see bounties appear at different times.
- **Likely location**: `services/bot-core/src/utils/executors/bounty_spawn_executor.py` — probably iterates all tiers in a single executor invocation instead of per-tier jobs or randomized per-tier delays.
- **Possible fix approaches**:
  1. Per-tier scheduled jobs with independent cadences
  2. Single executor but with per-tier random delay (0 to `spawn_interval_minutes`) before each tier spawn
  3. Weighted dice roll per tier per execution (tier might spawn or might not, probabilistically)
- **Next step**: Investigate tomorrow. Design decision needed on which approach feels right. Not blocking.

### A.21 — Shop refresh announcement posted to wrong channel + role mention inside embed (won't ping)
- **Severity**: 🟡 medium (broken notification UX)
- **Source**: Observed during Phase 2 testing (2026-04-19)
- **Status**: open
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
- **Source**: Phase 1.2 live run (2026-04-19)
- **Status**: open (doc fix OR optional alias addition)
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
