# BountyBot Session Compaction

**Generated**: 2026-05-02
**Session**: Post B.48-B.54 fixes, stack rebuilt and deployed, Phase 8 duel testing is next

---

## ⚠️ CRITICAL OPERATING PROTOCOLS — READ EVERY TIME

### Orchestrator Constraints (NEVER VIOLATE)
1. **🔴 ORCHESTRATOR DOES NOT MODIFY CONTAINERS**: Never run `docker compose down/up/build/restart`. Only USER may delete/rebuild/restart the stack. Orchestrator has `sudo docker exec` for DB queries, API calls, log inspection ONLY.
2. **🔴 ORCHESTRATOR DELEGATES, DOES NOT IMPLEMENT**: Use subagents for code/tests/research. Orchestrator coordinates testing, presents results, updates tracking docs — does NOT write code directly.
3. **🔴 LOG-TO-FILE MANDATE FOR ALL TEST SUITES**: ALWAYS pipe test runs to a log file ONCE. NEVER rerun a suite just to change grep patterns — read the log file.
   - `python -m pytest tests/ 2>&1 | tee /tmp/<descriptive>.log`
   - discord-gateway suite takes 15-22 minutes
4. **🔴 SUBAGENTS WORK ON /proj REPO, NOT IN CONTAINERS**: All code changes happen in the working tree. Only USER rebuilds/deploys.
5. **🔴 DO NOT REUSE PRIOR TASK_ID SESSIONS**: Always start fresh subagent sessions — reusing task_ids re-processes full session history and wastes tokens.

### Test Presentation Format (MANDATORY)

| # | Account | Command | Notes |
|---|---------|---------|-------|
| 8.1 | Main | `/duel-challenge target:@general_failure. stakes:100` | Expected behavior |

- **NEVER omit the Account column** — always explicit: **Main** or **Alt**
- Present one logical batch at a time; stop and wait for results before continuing
- After user runs commands, verify DB/API side before marking `[x]`

### Defect Handling
- **Defect found → log to `/proj/DEFECTS.md`** at top of OPEN section immediately
- User chooses log-and-continue vs stop-and-fix
- Entries must be self-contained for a stateless investigator

### Commits
- **DO NOT commit unless explicitly requested**
- Stage only real changed files — never include file-mode-only noise (hundreds of 100644→100755 changes exist in repo)
- Semantic commits: `type(scope): subject`

### Subagent Delegation
| Role | Use For |
|------|---------|
| `@researcher` | Read-only investigation, code analysis, DB queries |
| `@developer` | Code fixes, small implementation, running tests |
| `@architect` | Deep design work, complex refactoring |
| `@tester` | Adversarial QA, test review, edge case analysis |

**Standard fix workflow**: researcher (if needed) → developer → tester → commit (when user asks)

**ALL delegations MUST include**:
- "Do NOT commit changes"
- "Do NOT modify containers"
- "Pipe test runs to log file: `2>&1 | tee /tmp/<descriptive>.log`. NEVER rerun suites."

---

## Account Context

### Both Accounts Are Admins
- **Main**: `samx.ai` / `SamAccountX` / Discord `402296276617527306` / player_id=1 (admin via DEVELOPERS env or Discord perms)
- **Alt**: `general_failure.` / Discord `970691862035841048` / player_id=2 / **has `@Bounty Bot Admin` role** (ID: `1495550109381951549`)

### Current Player State (post-prestige, 2026-05-02)
| Account | Tier | XP | Credits | Prestige | Active Ship | Ship ID |
|---------|------|----|---------|----------|-------------|---------|
| Main | Bronze | 726 | 7,212 | 2 ⭐⭐ | Betty (starter loadout) | id=7 |
| Alt | Bronze | 897 | 1,000,008,975 | 0 | Betty (starter loadout) | id=2 |

Both players: Betty active, Nirai Impulse EX 1 equipped (weapon), E2 Exoclad + Telta Quickscan (modules), 1x Micro Gun MK I in cargo.

### Guild Config
- Guild ID: `1490693399307616276` (display name `bb-temp`)
- Admin role ID: `1495550109381951549`
- XP Thresholds: `{"Silver": 10, "Gold": 20, "Platinum": 30, "Prestige": 50}`
- Starting credits: 999,999,999

---

## E2E Test Progress

### Phase Status
| Phase | Status |
|-------|--------|
| 0 — Stack health | ✅ Done |
| 0.5 — Pre-registration edge cases | ✅ Done |
| 1 — Setup + registration | ✅ Done |
| 1.5 — Non-admin permission denials | ⏳ Deferred to post-release |
| 2 — Game data browsing + routes | ✅ Done |
| 2.5 — Help discoverability | ✅ Done |
| 3 — Ship management | ✅ Done |
| 4 — Shop system | ✅ Done (4.8 deferred) |
| 5 — Inventory + equipment | ✅ Done (5.5/A.39 deferred) |
| 6 — Player progression | ✅ Done (all prestige tests B.48/B.49 verified) |
| 7 — Bounty hunting | ✅ Done |
| **8 — Dueling [2P]** | ⏳ NOT STARTED — next up |
| 9 — Scheduler | ⏳ Not started |
| 10 — Data loading | ⏳ Not started |
| 11 — Edge cases | ⏳ Not started |
| 12 — Admin + config | ⏳ Not started |
| D — Blender/skins | ⏳ Not started |

### Immediate Next Tests (Phase 8 — after rebuild)

| # | Account | Command | Notes |
|---|---------|---------|-------|
| 8.1 | Main | `/duel-challenge target:@general_failure. stakes:100` | Challenge embed: both players, stakes, duel ID, expiry |
| 8.2 | Alt | `/duel-accept duel:<id>` | Combat resolves; winner/loser embed with damage breakdown |
| 8.3 | Both | `/profile` | Winner +100cr, loser -100cr, both gained XP |
| 8.5 | Main | `/duel-challenge target:@general_failure. stakes:50` | New challenge |
| 8.6 | Alt | `/duel-reject duel:<id>` | Cancelled, no credit change |
| 8.7 | Main | `/duel-challenge target:@SamAccountX stakes:0` | Can't duel yourself — error |
| 8.8 | Main | `/duel-challenge target:@general_failure. stakes:0` | Zero-stakes — should work |
| 8.9 | Main | `/duel-challenge target:@general_failure. stakes:999999` | Exceeds credits — error |
| 8.10 | Both | Send challenge, send another while pending | Duplicate pending duel error |
| 8.11 | Main | Send challenge, don't accept, fire duel_expire executor | Auto-expire via scheduler |

**8.11 shortcut** (fire duel_expire immediately):
```bash
sudo docker exec bountybot-bot-core curl -s -X POST -H 'Content-Type: application/json' \
  "http://localhost:8000/api/v1/jobs" \
  -d '{"delay_seconds":0,"payload":{"job_type":"duel_expire"}}'
```

### Also Needs Live Verification Post-Rebuild
- **B.53**: Prestige role swap — next time Main prestiges, verify Platinum role removed + Bronze role added in Discord
- **B.52**: Watch a few bounty spawns — confirm no zero-weapon criminal loadouts
- **B.54**: Capture a bounty with at least one missed check — verify winner gets reserve + unconsumed consolation; failed checker gets credits only (no XP)

---

## All Committed Fixes (This Session)

| ID | Severity | Summary | Commit |
|----|----------|---------|--------|
| B.40 | 🟡 | Admin role check wrong attribute | `84a874b` |
| B.41 | 🟡 | Duplicate equip autocomplete | `aaddb0d` |
| B.42 | 🟡 | Gateway starts before bot-core | `7b4cb2c` |
| B.43 | 🟡 | Zero-slot equip generic error | `aaddb0d` |
| B.44 | 🟡 | AboutCog preload no retry | `f39e73a` |
| B.45 | 🟡 | Loadout consistency over-validation | `aaddb0d` |
| B.46 | 🟡 | Equip autocomplete wrong mental model | `aaddb0d` |
| B.47 | 🟡 | WeaponSwapView duplicate slot values | `aaddb0d` |
| B.48 | 🟠 | Removed vestigial level/division system; prestige uses configurable XP threshold | `15eca1b` |
| B.49 | 🟠 | Prestige resets to starter Betty state (full fleet/inventory wipe) | `a7d0f7c` |
| B.51 | 🟠 | duelCog passed Discord snowflakes instead of player PKs | `18fa3ea` |
| B.52 | 🟡 | Criminal loadout could select non-combat ships | `18fa3ea` |
| B.53 | 🟠 | Prestige did not swap Discord tier roles | `18fa3ea` |
| B.54 | 🟡 | Bounty winner-reserve reward model (25% guaranteed floor) | `4814209` |

### Open Enhancements (Non-blocking)
- **B.49** (enhancement): Audit hardcoded operational constants for per-guild configurability (`BOUNTY_REWARD_TO_XP_GAIN_MULT`, `BOUNTY_WINNER_RESERVE_FACTOR`, etc.)
- **B.50**: Replace `confirm:CONFIRM` prompts with button-style embeds; revamp awkward admin command surfaces

---

## Key Architectural Context

### Progression System (post-B.48)
- **Tier**: Bronze/Silver/Gold/Platinum — set ONLY by `/promote` (manual). Never auto-advanced.
- **XP** gates promotion but does NOT auto-change tier
- **Prestige** gated on `xp_thresholds["Prestige"]` per guild (default 50,000 if key absent in JSON)
- **No level concept** — deleted entirely in B.48

### Prestige Reset (post-B.49)
- Full account reset to first-time `/register` state
- ALL ships deleted, ALL inventory cleared
- Betty recreated active with standard starter loadout
- Preserved: lifetime_credits, duel stats, bounty stats, prestige_count (incremented)
- Discord tier roles swapped: old tier role removed, Bronze role added (B.53)

### Bounty Reward Distribution (post-B.54)
- `winner_reserve = floor(reward × 0.25)` — guaranteed minimum for winner
- `consolation_pool = reward - winner_reserve`
- `reward_per_sys = floor(consolation_pool / route_length)`
- Failed checkers: `reward_per_sys × systems_checked` credits, **zero XP**
- Winner: `winner_reserve + unconsumed_consolation`, XP on full amount
- Bronze 2x combat bonus: applied to full winner payout incl. XP
- `BOUNTY_WINNER_RESERVE_FACTOR = 0.25` (env-overridable via `BOUNTYBOT_BOUNTY_WINNER_RESERVE_FACTOR`)

### Bounty Division Mechanics
- **Bronze**: auto-captures on correct system (no combat); optional post-capture combat for 2x payout
- **Silver+**: must win combat to capture; loss resets bounty (new answer, checks cleared, no rewards)
- On any successful capture: `calc_rewards()` distributes to all contributors per above formula

### Inventory Contract
- `player_inventories.quantity` = unequipped (cargo) copies only
- `player_ships.weapons/modules/turrets` JSON = equipped copies
- `LoadoutConsistencyService` is the ONLY valid mutation point for equip/unequip

### Discord Gateway Message Format
- Embeds come back with data in `msg["content"]` as a dict, NOT in `msg["embeds"]`

---

## Frequent DB / API Queries

```bash
# Player state
sudo docker exec bountybot-db psql -U bounty -d bountydb -c \
  "SELECT p.id, u.discord_username, p.tier, p.xp, p.credits, p.prestige_count, p.active_ship_id FROM players p JOIN users u ON p.user_id = u.id ORDER BY p.id;"

# Ship loadouts
sudo docker exec bountybot-db psql -U bounty -d bountydb -c \
  "SELECT id, player_id, ship_name, is_active, weapons, modules, turrets FROM player_ships ORDER BY player_id, id;"

# Inventory
sudo docker exec bountybot-db psql -U bounty -d bountydb -c \
  "SELECT player_id, item_name, item_type, quantity FROM player_inventories ORDER BY player_id;"

# Guild config
sudo docker exec bountybot-bot-core curl -s http://localhost:8000/api/v1/config/guild/1490693399307616276 | python3 -m json.tool

# Active bounties with reward details
sudo docker exec bountybot-db psql -U bounty -d bountydb -c \
  "SELECT id, division, criminal_name, answer, reward, reward_per_sys, route, status FROM bounty WHERE guild_id=1490693399307616276 AND status='active';"

# Force-fire a scheduled executor
sudo docker exec bountybot-bot-core curl -s -X POST -H 'Content-Type: application/json' \
  "http://localhost:8000/api/v1/jobs" \
  -d '{"delay_seconds":0,"payload":{"job_type":"duel_expire"}}'

# Containers healthy?
sudo docker ps --format '{{.Names}}: {{.Status}}'
```

---

## Session Setup Commands (Once Per Session, After /admin_setup)

```bash
GID=1490693399307616276

# Compress bounty timers
sudo docker exec bountybot-bot-core curl -s -X PUT -H 'Content-Type: application/json' \
  "http://localhost:8000/api/v1/config/guild/$GID/bounty" \
  -d '{"guild_id":'$GID',"bounty_spawn_interval_minutes":5,"bounty_expiry_minutes":10,"max_bounties_per_tier":{"bronze":20,"silver":20,"gold":20,"platinum":20}}'

# Low XP thresholds for fast tier testing
sudo docker exec bountybot-bot-core curl -s -X PUT -H 'Content-Type: application/json' \
  "http://localhost:8000/api/v1/config/guild/$GID/xp-thresholds" \
  -d '{"guild_id":'$GID',"thresholds":{"Silver":10,"Gold":20,"Platinum":30,"Prestige":50}}'

# Generous starting credits
sudo docker exec bountybot-bot-core curl -s -X PUT \
  "http://localhost:8000/api/v1/config/guild/$GID/starting-credits/999999999"
```

---

## Key Files

| File | Purpose |
|------|---------|
| `/proj/DEFECTS.md` | Single source of truth for all defects and enhancements |
| `/proj/E2E_TEST_CHECKLIST.md` | Full E2E checklist with operator instructions |
| `/proj/COMPACTION.md` | This file |
| `/proj/AGENTS.md` | Root project standards |
| `/proj/services/bot-core/src/services/AGENTS.md` | Service layer patterns, LoadoutConsistencyService choke-point |
| `/proj/services/discord-gateway/src/cogs/AGENTS.md` | Cog patterns |

---

## Quick-Start for Next Agent

1. Read this file fully.
2. Confirm stack healthy: `sudo docker ps --format '{{.Names}}: {{.Status}}'`
3. Check `DEFECTS.md` OPEN section for any unverified fixes.
4. **Stack has been rebuilt** — B.51/B.52/B.53/B.54 are all deployed.
5. Start with **Phase 8 dueling** (8.1). Both accounts needed simultaneously for most tests.
6. Present tests in `# / Account / Command / Notes` table format.
7. After user runs commands, verify DB/API, mark `[x]` in checklist.
8. Defects → top of OPEN in `DEFECTS.md`, user decides stop vs continue.
9. Do NOT commit without explicit ask. Do NOT touch containers.
