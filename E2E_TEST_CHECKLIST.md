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

---

## Phase 0 — Stack Health

Verify all four services are running and connected.

- [x] **0.1** `[ADMIN]` `/ping` — Returns ephemeral "Pong! Latency is N ms"
- [x] **0.2** `[ADMIN]` `/health` — Returns embed showing bot-core status, DB connectivity, schema version, connection pool stats
- [x] **0.3** Confirm no error messages in `docker compose logs` on fresh boot
- [x] **0.4** Confirm health check endpoints respond directly:
   - `curl http://localhost:8000/api/v1/health` (bot-core)
   - `curl http://localhost:7999/api/v1/health` (discord-gateway)
   - `curl http://localhost:8001/api/v1/health/` (blender-service)

---

## Phase 1 — Guild Setup, Channel Infrastructure & Player Registration

### First-time guild initialisation

- [ ] **1.1** `[ADMIN]` `/admin_setup` — "Guild initialized" response; creates:
   - "BountyBot" category with @everyone view denied
   - `#bronze-bounty-board` channel (read-only for players)
   - `#silver-bounty-board` channel (read-only for players)
   - `#gold-bounty-board` channel (read-only for players)
   - `#shop` channel (read-only for players)
   - `#bounty-hunting` channel (interactive — slash commands allowed)
   - `#bounty-discussions` channel (chat only — slash commands disabled)
   - `#bot-images` channel (hidden from all users, bot-only)
   - `@Bounty Hunter` role (mentionable)
   - "BountyBot Admins" role (if no admin_role provided)
   - GuildConfig + 4 tier shops in database
   - Confirmation embed lists all channel links and role mentions

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

- [ ] **1.5** `[ADMIN]` `/admin_config action:View Config` — Shows guild configuration embed including channel IDs, admin role, starting credits, sale price factor, XP thresholds
- [ ] **1.6** `[ADMIN]` `/admin_config_validate` — Returns "valid: true" with no errors or warnings

### Player registration (your account)

- [ ] **1.7** `/profile` — First use: creates User + Player with starter ship "Betty", starter equipment (Micro Gun MK I, Telta Quickscan, E2 Exoclad, IMT Extract 1.3), Bronze tier, 0 XP, starting credits per guild config. **Also assigns `@Bounty Hunter` role to the user.**
- [ ] **1.8** Verify BountyBot channels are now visible to you (you have `@Bounty Hunter` role)
- [ ] **1.9** `/profile` — Second use: identical response, no duplicate player created (idempotent)

### Unregister / re-register cycle

- [ ] **1.10** `/unregister` — Ephemeral "Bounty Hunter role removed. Your player data is preserved." Verify:
   - `@Bounty Hunter` role removed from your user
   - BountyBot channels are no longer visible to you
- [ ] **1.11** `/profile` — Re-assigns `@Bounty Hunter` role; channels visible again; same player data as before

### Unregister edge cases

- [ ] **1.12** `/unregister` when you don't have the role — "You don't have the Bounty Hunter role" message

### `[2P]` Second player registration

- [ ] **1.13** `[2P]` Player 2 runs `/profile` — Creates their own User + Player with separate state; assigns `@Bounty Hunter` role to Player 2

> Note: `/profile` takes no parameters — it always shows the invoking user's profile. There is no way to view another player's profile via `/profile`. Use `/admin_player user:@player action:View Stats` (admin) to inspect other players.

---

## Phase 2 — Game Data Browsing

Verify seeded data is accessible via the aboutCog. All of this is read-only.

### Object lookup

- [ ] **2.1** `/about category:ship name:Betty` — Returns detailed ship embed (armour, cargo, compatible skins, manufacturer, tech level)
- [ ] **2.2** `/about category:primary_weapon name:Micro Gun MK I` — Returns weapon stats
- [ ] **2.3** `/about category:module name:Telta Quickscan` — Returns module stats
- [ ] **2.4** `/about category:ship name:nonexistent_ship` — Error: not found

### Category listing

- [ ] **2.5** `/list_category category:ship` — Paginated list of all seeded ships (capped at 50)
- [ ] **2.6** `/list_category category:primary_weapon` — Lists primary weapons
- [ ] **2.7** `/list_category category:secondary_weapon` — Lists secondary weapons
- [ ] **2.8** `/list_category category:turret_weapon` — Lists turret weapons
- [ ] **2.9** `/list_category category:module` — Lists all modules
- [ ] **2.10** `/list_category category:criminal` — Lists NPC criminals
- [ ] **2.11** `/list_category category:system` — Lists all star systems
- [ ] **2.12** `/list_category category:module tech_level:2` — Filtered by tech level

### Route planning

- [ ] **2.13** `/make-route start:Wolf-Reiser end:Pan` — Returns numbered route with hop count and route map image attachment
- [ ] **2.14** `/make-route start:Pan end:Pan` — Edge case: same start/end system

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

## Phase 6 — Bounty Hunting (Core Gameplay)

> Note: Bounties spawn via APScheduler (`bounty_spawn_default` job, every 5 minutes by default).
> Use `/scheduler_list` to check timing, or wait for auto-spawn.
> Alternatively, call bot-core API directly to create test bounties.

### Wait for bounty spawn

- [ ] **6.1** `/bounties` — Lists active bounties (may be empty if scheduler hasn't fired yet)
- [ ] **6.2** `/bounties division:bronze` — Filter bounties by division
- [ ] **6.3** `[WAIT]` Wait for `bounty_spawn_default` to fire — `/bounties` now shows active bounty with criminal name, division, reward, reward_per_sys, systems checked count, time remaining, and bounty ID

### Verify bounty announcement (redesign feature)

- [ ] **6.4** Check the correct per-division bounty board channel (e.g. `#bronze-bounty-board` for a bronze bounty). Verify:
   - Rich embed with faction-specific color (Terran=gold, Vossk=teal, Midorian=dark red, Nivelian=blue)
   - Title: criminal name
   - Fields: Difficulty (T-level), Reward Pool, Bounty Ends (relative timestamp), Loadout (ship + weapons + modules), Route (system names), Checked Systems ("No systems checked yet")
   - Footer: faction name
   - `@Bounty Hunter` role mention above the embed
- [ ] **6.5** Verify route map image is embedded in the announcement (if route map generation succeeded)

### Investigate a bounty

- [ ] **6.6** `/route bounty:<bounty_id>` — Shows the bounty's system route with checked/unchecked indicators
- [ ] **6.7** `/criminal-loadout bounty:<bounty_id>` — Shows criminal's ship, weapons, modules

### Hunt the bounty

- [ ] **6.8** `/check system:<wrong_system>` — "System Checked" response (incorrect). **Also verify**: the announcement embed in the bounty board channel live-edits to show the checked system with ~~strikethrough~~
- [ ] **6.9** `/check system:<same_wrong_system>` — "Already checked" response (same system can't be checked twice)
- [ ] **6.10** `/check system:<correct_system>` — "Bounty Found!" response (correct answer = last system in route); reward credits + XP granted. **Also verify**: the announcement embed edits one final time showing the found system in **bold**
- [ ] **6.11** `/profile` — Credits and XP increased by bounty reward
- [ ] **6.12** `/bounties` — Bounty no longer listed (resolved)
- [ ] **6.13** Verify the announcement message has been **deleted** from the bounty board channel

### Bounty edge cases

- [ ] **6.14** `/check system:<any_system>` with no active bounties — Error: "No active bounty" or "No Bounty"
- [ ] **6.15** `/check system:<system>` immediately after a check — Cooldown message (per-player cooldown, default 180 seconds)

### `[2P]` Bounty competition

- [ ] **6.16** `[2P]` `[WAIT]` Both players race to `/check system:<correct>` on same bounty — Only the first correct answer wins
- [ ] **6.17** Both players `/profile` — Only the winner received credits/XP; non-winner contributors may receive partial reward (reward_per_sys * systems they checked)

---

## Phase 7 — Dueling `[2P]`

All duel tests require a second registered player.

### Challenge and accept

- [ ] **7.1** `[2P]` `/duel-challenge target:@player2 stakes:100` — Duel challenge sent; embed shows both players' mentions, stakes, duel ID, and "Challenge expires in 24 hours"
- [ ] **7.2** `[2P]` Player 2 runs `/duel-accept duel:<duel_id>` (autocomplete dropdown) — Combat resolves; winner/loser announced with damage breakdown
- [ ] **7.3** Both players `/profile` — Winner gained credits (stakes); loser lost credits (stakes). Both gained/lost XP
- [ ] **7.4** Check for stalemate: If both ships have similar stats, result may be "Stalemate!" (yellow embed, no credit transfer)

### Challenge and decline

- [ ] **7.5** `[2P]` `/duel-challenge target:@player2 stakes:50` — New challenge sent
- [ ] **7.6** `[2P]` Player 2 runs `/duel-reject duel:<duel_id>` — Challenge cancelled, no rewards or penalties

### Duel edge cases

- [ ] **7.7** `/duel-challenge target:@yourself stakes:0` — Error: can't duel yourself
- [ ] **7.8** `[2P]` `/duel-challenge target:@player2 stakes:0` — Zero-stakes friendly duel (should work)
- [ ] **7.9** `[2P]` `/duel-challenge target:@player2 stakes:999999` — Stakes exceed one player's credits; error: insufficient credits
- [ ] **7.10** `[2P]` Send challenge, then send another while first is pending — Error: already have pending duel (if enforced)
- [ ] **7.11** `[2P]` `[WAIT]` Send challenge, do NOT accept/decline, wait for expiry (~24 hours) — Challenge auto-expires via duel_expire_executor

---

## Phase 8 — Player Progression & Tiers `[ADMIN]`

Use admin commands to fast-track progression testing.

### XP and tier advancement

Tier thresholds (guild-configurable defaults): Bronze (0 XP) -> Silver (1,000 XP) -> Gold (5,000 XP) -> Platinum (15,000 XP)

- [ ] **8.1** `[ADMIN]` `/admin_player user:@you action:Set XP xp:1500` — Sets XP; confirmation embed shows old/new XP and tier change (Bronze -> Silver)
- [ ] **8.2** `/profile` — XP shows 1,500; tier shows Silver
- [ ] **8.3** `/shop tier:Silver` — Silver-tier items now accessible
- [ ] **8.4** `/shop tier:Gold` — Gold-tier items still locked (player is Silver)

### Credits management

- [ ] **8.5** `[ADMIN]` `/admin_player user:@you action:Add Credits credits:50000` — Adds credits; shows amount added + new total
- [ ] **8.6** `/profile` — Credits reflect the addition
- [ ] **8.7** `[ADMIN]` `/admin_player user:@you action:Set Credits credits:1000` — Sets credits to exact value
- [ ] **8.8** `/profile` — Credits show exactly 1,000

### Admin player inspection

- [ ] **8.9** `[ADMIN]` `/admin_player user:@player2 action:View Stats` — Shows Player 2's full stats (tier, XP, credits, lifetime credits, prestige count)
- [ ] **8.10** `[ADMIN]` `/admin_player user:@you action:Reset Player` — Resets player to defaults (XP, credits, stats zeroed; ships preserved)
- [ ] **8.11** `/profile` — Confirms reset state

### Prestige

- [ ] **8.12** `[ADMIN]` `/admin_player user:@you action:Set XP xp:999999` — Max out XP to reach Platinum / level 10
- [ ] **8.13** `/prestige` (without confirm) — Should show orange warning embed explaining what prestige does (reset to Bronze, keep ships/credits, gain prestige star)
- [ ] **8.14** `/prestige confirm:CONFIRM` — Resets level to 0, tier to Bronze, clears inventory; increments prestige_count; preserves ships and lifetime stats
- [ ] **8.15** `/profile` — Shows prestige count, reset XP/tier, preserved lifetime stats

### Leaderboard

- [ ] **8.16** `/leaderboard` — Shows top 10 players ranked by XP with tier/credits and rank emojis
- [ ] **8.17** `/leaderboard tier:Silver` — Filtered to Silver-tier players only

---

## Phase 9 — Skins & Rendering

### Skin display (no GPU rendering)

- [ ] **9.1** `/ship_skin ship:<skinnable_ship> skin:Default` — Shows ship icon embed (uses ship's default icon URL)
- [ ] **9.2** `/ship_skin ship:<skinnable_ship> skin:urban-camo` — Shows skin image URL in embed
- [ ] **9.3** `/ship_skin ship:<non_skinnable_ship> skin:Default` — Error: ship does not support custom skins
- [ ] **9.4** `/ship_skin ship:<ship> skin:nonexistent_skin` — Error: skin not found

### 3D rendering (requires blender-service + GPU)

- [ ] **9.5** `/render_skin ship:<1-region ship, e.g. Betty> skin:Default` — Returns rendered PNG as file attachment with format download buttons
- [ ] **9.6** `/render_skin ship:<2-region ship, e.g. Phantom XT> skin:urban-camo` — Renders with skin overlay on 2 regions
- [ ] **9.7** `/render_skin ship:<3-region ship, e.g. Kinzer RS> skin:racing-stripes` — Renders with skin overlay on 3 regions
- [ ] **9.8** Click "AEI (Android/ETC1)" button on render result — Delivers .aei file for mobile
- [ ] **9.9** Click "AEI (PC/DXT5)" button on render result — Delivers .aei file for desktop

### Texture compositing (no 3D render)

- [ ] **9.10** `/make_skin_texture ship:<1-region ship> skin:Default` — Returns composited 2D texture PNG
- [ ] **9.11** `/make_skin_texture ship:<2-region ship> skin:ferrari` — Returns composited texture with 2 skin regions applied

### Edge cases

- [ ] **9.12** `/render_skin ship:<non_skinnable_ship>` — Error: does not support custom skins
- [ ] **9.13** `/render_skin ship:<ship_without_3d_assets>` — Graceful error or fallback (no .blend file)
- [ ] **9.14** Ships with `textureRegions: 0` (e.g., Vol Noor, Amboss) — Should be treated as non-skinnable
- [ ] **9.15** Ships with `textureRegions: -1` (e.g., Cronus) — Edge case: should handle gracefully

---

## Phase 10 — Admin Operations & Audit `[ADMIN]`

### Configuration management

- [ ] **10.1** `[ADMIN]` `/admin_config action:View Config` — Shows full guild configuration embed (admin role, starting credits, sale price factor, XP thresholds, channel IDs)
- [ ] **10.2** `[ADMIN]` `/admin_config action:Set Starting Credits starting_credits:5000` — Updates starting credits
- [ ] **10.3** `[ADMIN]` `/admin_config action:View Config` — Verify starting credits changed to 5,000
- [ ] **10.4** `[ADMIN]` `/admin_config action:Set Admin Role admin_role:@SomeRole` — Updates admin role
- [ ] **10.5** `[ADMIN]` `/admin_config action:Reset to Defaults` — Resets config to defaults
- [ ] **10.6** `[ADMIN]` `/admin_config action:View Config` — Verify defaults restored

### Shop configuration

- [ ] **10.7** `[ADMIN]` `/admin_config_shop ship_count_min:2 ship_count_max:8` — Updates shop generation parameters
- [ ] **10.8** `[ADMIN]` `/admin_config_validate` — Validate config is still valid after changes

### Shop refresh

- [ ] **10.9** `[ADMIN]` `/admin_refresh_shop tier:Bronze` — Force refreshes Bronze shop inventory
- [ ] **10.10** `/shop tier:Bronze` — Verify items have changed
- [ ] **10.11** Verify shop refresh announcement appears in `#shop` channel with `@Bounty Hunter` role mention

### Guild statistics

- [ ] **10.12** `[ADMIN]` `/admin_guild_stats` — Shows total players, tier distribution, total/average credits and XP

### Admin permission check

- [ ] **10.13** `[ADMIN]` `/admin_check user:@admin_user` — Shows "has admin rights" with reason (developer/Discord admin/bot role)
- [ ] **10.14** `[ADMIN]` `/admin_check user:@non_admin_user` — Shows "does not have admin rights"

### Render configuration (blender-service)

- [ ] **10.15** `[ADMIN]` `/render_config action:view` — Shows current blender render settings
- [ ] **10.16** `[ADMIN]` `/render_config action:set setting:<key> value:<int>` — Updates a render setting
- [ ] **10.17** `[ADMIN]` `/render_config action:reset` — Resets render settings to defaults
- [ ] **10.18** `[ADMIN]` `/render_cache_clear` — Clears blender render cache; shows freed_mb

### Destructive operations (test last!)

- [ ] **10.19** `[ADMIN]` `/admin_uninstall` (without confirm) — Shows red warning embed listing what will be deleted (7 channels, category, @Bounty Hunter role, all DB data). Does NOT delete anything.
- [ ] **10.20** `[ADMIN]` `/admin_uninstall confirm:CONFIRM-DELETE` — **DESTRUCTIVE**: deletes all 7 BountyBot channels, the BountyBot category, the @Bounty Hunter role, and all guild DB data (config, shops, players, bounties, everything). Shows removed record counts.
- [ ] **10.21** Verify BountyBot category and all 7 channels are gone from Discord
- [ ] **10.22** Verify `@Bounty Hunter` role is gone from guild role list
- [ ] **10.23** `/profile` — Player data gone; must re-register
- [ ] **10.24** `/bounties` — All bounties gone
- [ ] **10.25** `/shop tier:Bronze` — Shops need re-initialisation
- [ ] **10.26** `[ADMIN]` `/admin_setup` — Re-initialise after uninstall; verify all channels, category, and role recreated cleanly

---

## Phase 11 — Scheduler Administration `[ADMIN]`

### View jobs

- [ ] **11.1** `[ADMIN]` `/scheduler_list` — Lists all APScheduler jobs with type, trigger, and next run time (bounty_spawn_default, temperature_decay_default, shop_refresh_default). Handles 503 if scheduler still starting.
- [ ] **11.2** `[ADMIN]` `/scheduler_view job_id:bounty_spawn_default` — Shows full job details including payload JSON

### Update jobs

- [ ] **11.3** `[ADMIN]` `/scheduler_update job_id:bounty_spawn_default payload_json:{"job_type": "bounty_spawn"}` — Updates job payload; confirmation message
- [ ] **11.4** `[ADMIN]` `/scheduler_update job_id:bounty_spawn_default payload_json:invalid` — Error: invalid JSON

### Delete jobs (careful!)

- [ ] **11.5** `[ADMIN]` `/scheduler_view job_id:nonexistent_job` — Error: job not found
- [ ] **11.6** `[ADMIN]` `/scheduler_delete job_id:<test_job_id>` — Only test with a job you can recreate

---

## Phase 12 — Dev Tools `[ADMIN]`

- [ ] **12.1** `[ADMIN]` `/load_data category:ship` — Re-seeds ship data from JSON files; shows file count
- [ ] **12.2** `[ADMIN]` `/load_data category:All` — Re-seeds all categories; summarizes results with error counts
- [ ] **12.3** `[ADMIN]` `/reload_autocomplete` — Force-reloads cached autocomplete data for AboutCog, DevCog, SkinsCog

---

## Phase 13 — Edge Cases & Error Handling

These can be tested at any point after Phase 1.

### Permission checks (non-admin user)

- [ ] **13.1** (Non-admin) `/admin_setup` — Ephemeral "permission denied" error
- [ ] **13.2** (Non-admin) `/admin_player user:@someone action:View Stats` — Ephemeral "permission denied"
- [ ] **13.3** (Non-admin) `/admin_config action:View Config` — Ephemeral "permission denied"
- [ ] **13.4** (Non-admin) `/scheduler_list` — Ephemeral "permission denied"
- [ ] **13.5** (Non-admin) `/health` — Ephemeral "permission denied"
- [ ] **13.6** (Non-admin) `/ping` — Ephemeral "permission denied"
- [ ] **13.7** (Non-admin) `/load_data category:ship` — Ephemeral "permission denied"

### Invalid input

- [ ] **13.8** `/check` with no system selected — Discord enforces required param
- [ ] **13.9** `/ship ship_id:999999` — Error: ship not found / not owned
- [ ] **13.10** `/buy item_id:999999` — Error: item not found
- [ ] **13.11** `/equip item_name:NonexistentWeapon equipment_type:Weapon` — Error: not in inventory
- [ ] **13.12** `/about category:ship name:ZZZZZ_nonexistent` — Error: not found

### Unregistered player edge case

- [ ] **13.13** (Before `/profile`) Try `/bounties` — May fail or return empty (bountyCog does not auto-register)
- [ ] **13.14** (Before `/profile`) Try `/shop tier:Bronze` — Should auto-register player (shopCog upserts)
- [ ] **13.15** (Before `/profile`) Try `/unregister` — Should handle gracefully ("You don't have the Bounty Hunter role" or similar)

---

## Phase 14 — Scheduled Jobs & Announcements `[WAIT]`

These require patience or admin force-triggers.

| # | Job | How to Test | Expected |
|---|-----|-------------|----------|
| **14.1** | Bounty auto-spawn | Wait for spawn interval (default 5 min), run `/bounties` | New bounty appears; **announcement posted to correct division channel** (`#bronze-bounty-board` / `#silver-bounty-board` / `#gold-bounty-board`) with rich embed and `@Bounty Hunter` mention |
| **14.2** | Shop auto-refresh | Wait 6 hours or `[ADMIN]` `/admin_refresh_shop tier:Bronze` | Shop items rotated; **announcement posted to `#shop` channel** with `@Bounty Hunter` role mention |
| **14.3** | Temperature decay | Spawn + resolve multiple bounties in one system, then wait for hourly decay job | System temperature cools over time |
| **14.4** | `[2P]` Duel expiry | Send `/duel-challenge`, don't respond, wait for `DUEL_PENDING_DURATION` (~24 hours) | Challenge auto-expires |
| **14.5** | Bounty expiry | Wait for bounty to exist past `end_time` without being solved | Bounty auto-expires; **announcement message deleted from bounty board channel** |
| **14.6** | Bounty escape + respawn | Find correct system but lose combat (weak ship vs strong criminal) — criminal escapes | Announcement deleted; criminal respawns with new route after delay (`len(route)` minutes) |

---

## Test Results Summary

| Phase | Total | Passed | Failed | Skipped | Notes |
|-------|-------|--------|--------|---------|-------|
| 0 — Health | 4 | 4 | 0 | 0 | Admin-only for /ping and /health |
| 1 — Setup, Channels & Registration | 13 | | | | 1 needs `[2P]` |
| 2 — Game Data | 14 | | | | |
| 3 — Ship Management | 6 | | | | 2 deferred to after Phase 4 |
| 4 — Shop | 12 | | | | |
| 5 — Inventory & Equipment | 15 | | | | 1 needs `[ADMIN]` |
| 6 — Bounty Hunting | 17 | | | | 2 need `[2P]`, 1 needs `[WAIT]` |
| 7 — Dueling | 11 | | | | ALL need `[2P]` |
| 8 — Progression | 17 | | | | ALL need `[ADMIN]` |
| 9 — Skins & Rendering | 15 | | | | Requires blender-service + GPU |
| 10 — Admin Ops | 26 | | | | ALL need `[ADMIN]` |
| 11 — Scheduler | 6 | | | | ALL need `[ADMIN]` |
| 12 — Dev Tools | 3 | | | | ALL need `[ADMIN]` |
| 13 — Edge Cases | 15 | | | | |
| 14 — Scheduled Jobs | 6 | | | | 1 needs `[2P]` |
| **TOTAL** | **180** | | | | |

### Tests requiring a second Discord user: ~15
`1.13, 6.16, 6.17, 7.1–7.11, 14.4, 14.6`

### Tests requiring admin permissions: ~65
`0.1, 0.2, 1.1, 1.5, 1.6, 5.15, 8.1–8.17, 10.1–10.26, 11.1–11.6, 12.1–12.3, 13.1–13.7`

### Tests requiring wait time: ~7
`6.3, 7.11, 14.1–14.6`

### Tests requiring blender-service + GPU: ~11
`9.5–9.15`

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

*Updated: 2026-04-05*
*Based on code review of 48 live slash commands across 12 active cogs + redesign audit*
