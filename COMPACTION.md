# BountyBot Session Compaction

**Generated**: 2026-05-02
**Session**: Post B.48-B.53 bug-fixing, Phase 8 duel testing blocked pending rebuild

---

## ⚠️ CRITICAL OPERATING PROTOCOLS — READ EVERY TIME

### Orchestrator Constraints (NEVER VIOLATE)
1. **🔴 ORCHESTRATOR DOES NOT MODIFY CONTAINERS**: Never run `docker compose down/up/build/restart`. Only USER may delete/rebuild/restart the stack. Orchestrator has `sudo docker exec` for DB queries, API calls, log inspection ONLY.
2. **🔴 ORCHESTRATOR DELEGATES, DOES NOT IMPLEMENT**: Use subagents for code/tests/research. Orchestrator coordinates testing, presents results, updates tracking docs — NOT write code directly.
3. **🔴 LOG-TO-FILE MANDATE FOR ALL TEST SUITES**: ALWAYS pipe test runs to a log file. NEVER rerun a test suite just to filter output differently — extract from the log file via grep/read.
   - bot-core: `python -m pytest tests/ 2>&1 | tee /tmp/<descriptive>.log`
   - discord-gateway: `python -m pytest tests/ 2>&1 | tee /tmp/<descriptive>.log` (takes 15-22 min)
4. **🔴 SUBAGENTS WORK ON /proj REPO, NOT IN CONTAINERS**: All code changes happen in the working tree at `/proj`. Only USER rebuilds the stack to deploy.

### Test Presentation Format (MANDATORY)

Always present tests in table form:

| # | Account | Command | Notes |
|---|---------|---------|-------|
| 8.1 | Main | `/duel-challenge target:@general_failure. stakes:100` | Expected behavior |

- **NEVER omit the Account column**
- Account must be explicit: **Main** or **Alt** — never assume
- Present one logical batch at a time; stop and wait for results before continuing

### Defect Handling
- **Defect found → log to `/proj/DEFECTS.md`** at top of OPEN section immediately
- User chooses log-and-continue vs stop-and-fix
- Entries must be self-contained for a stateless investigator

### Commits
- **DO NOT commit unless explicitly requested**
- Stage only the relevant changed files — never include file-mode-only changes (the repo has hundreds of files with 100644→100755 mode noise)
- Semantic commits: `type(scope): subject` per logical unit

### Subagent Delegation
| Role | Use For |
|------|---------|
| `@researcher` | Read-only investigation, code analysis, DB queries |
| `@developer` | Code fixes, small implementation, running tests |
| `@architect` | Deep design work, complex refactoring, multi-component changes |
| `@tester` | Adversarial QA, test review, bug-fix verification |

**Standard fix workflow**: researcher (if needed) → developer → tester → commit (when user asks)

**ALL delegations MUST include**:
- "Do NOT commit changes"
- "Do NOT modify containers"
- For test runs: "Pipe to log file: `2>&1 | tee /tmp/<descriptive>.log`. NEVER rerun suites."
- **DO NOT reuse prior task_id sessions** — they re-process the full session history and are token-expensive. Always start fresh subagent sessions.

---

## Account Context

### Account Identifiers
- **Main**: `samx.ai` / `SamAccountX` / Discord `402296276617527306` / player_id=1
- **Alt**: `general_failure.` / Discord `970691862035841048` / player_id=2 / **HAS @Bounty Bot Admin ROLE**

### Both accounts are admins
Main is also an admin (via DEVELOPERS env var or Discord admin perms). Alt has the `@Bounty Bot Admin` role (ID: `1495550109381951549`).

### Current Account Stats (as of 2026-05-02 post-prestige)
| Account | Tier | XP | Credits | Prestige | Active Ship | Ship ID |
|---------|------|----|---------|----------|-------------|---------|
| Main | Bronze | 726 | 7,212 | 2 ⭐⭐ | Betty | id=7 |
| Alt | Bronze | 897 | 1,000,008,975 | 0 | Betty | id=2 |

### Both players' loadout (starter Betty state)
- Active ship: Betty (starter loadout)
- Equipped: Nirai Impulse EX 1 (weapon), E2 Exoclad + Telta Quickscan (modules)
- Inventory: 1x Micro Gun MK I (cargo)

### Guild Config
- Guild ID: `1490693399307616276` (display name `bb-temp`)
- Admin role ID: `1495550109381951549` (Bounty Bot Admin)
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
| 5 — Inventory + equipment | ✅ Done (5.5 deferred A.39) |
| 6 — Player progression | ✅ Done (all prestige tests B.48/B.49 verified) |
| 7 — Bounty hunting | ✅ Done |
| **8 — Dueling [2P]** | ⏳ NOT STARTED — blocked on B.51 fix rebuild |
| 9 — Scheduler | ⏳ Not started |
| 10 — Data loading | ⏳ Not started |
| 11 — Additional edge cases | ⏳ Not started |
| 12 — Admin + config | ⏳ Not started |
| D — Blender/skins | ⏳ Not started |

### Pending Tests (grep `- [ ]` in E2E_TEST_CHECKLIST.md for full list)

**Immediate next (after rebuild):**
- Phase 8: 8.1-8.11 — Full duel flow (B.51 fix needs live verification)
- B.53 re-verify: prestige role swap (Platinum→Bronze) needs live check

**Also pending:**
- 1.5.x — Non-admin command visibility (deferred post-release)
- 4.8 — Insufficient stock buy (deferred, no suitable item)
- 5.5 — Item type mismatch error (A.39 deferred post-release)
- Phases 9, 10, 11, 12, D — not started

---

## Bugs Fixed This Session (ALL COMMITTED)

| ID | Severity | Summary | Commit |
|----|----------|---------|--------|
| B.40 | 🟡 medium | Admin role check used wrong attribute | `84a874b` |
| B.41 | 🟡 medium | Duplicate item equip autocomplete | `aaddb0d` |
| B.42 | 🟡 medium | Gateway starts before bot-core | `7b4cb2c` |
| B.43 | 🟡 medium | Zero-slot equip generic error | `aaddb0d` |
| B.44 | 🟡 medium | AboutCog preload no retry logic | `f39e73a` |
| B.45 | 🟡 medium | Loadout consistency over-validation | `aaddb0d` |
| B.46 | 🟡 medium | Equip autocomplete wrong mental model | `aaddb0d` |
| B.47 | 🟡 medium | WeaponSwapView duplicate slot values | `aaddb0d` |
| B.48 | 🟠 high | Vestigial level/division system removed; prestige now uses configurable XP threshold | `15eca1b` |
| B.49 | 🟠 high | Prestige now resets to starter Betty state (full fleet/inventory wipe + Betty recreated) | `a7d0f7c` |
| B.51 | 🟠 high | duelCog passed Discord snowflakes instead of player PKs | `18fa3ea` |
| B.52 | 🟡 medium | Criminal loadout could select non-combat ships (0 primaries) | `18fa3ea` |
| B.53 | 🟠 high | Prestige did not swap Discord tier roles | `18fa3ea` |

### Open Defects Needing Live Verification After Rebuild
- **B.51** — duel challenge/accept/reject/autocomplete player ID fix → verify via Phase 8 E2E
- **B.52** — criminal ship filter → verify by watching a few bounty spawns (no zero-weapon loadouts)
- **B.53** — prestige role swap → verify Platinum role removed + Bronze role added on next prestige

### Open Enhancements (Not Blocking)
- **B.49** (enhancement label) — Audit hardcoded operational constants for per-guild configurability
- **B.50** — Refine confirmation UX (button-style instead of `confirm:CONFIRM`) and admin command syntax

---

## Key Architectural Context

### Progression System (post-B.48)
- **Tier** (Bronze/Silver/Gold/Platinum) is set ONLY by `/promote` (manual) — never auto-advanced
- **XP** gates promotion but does NOT auto-change tier; player must run `/promote`
- **Prestige** gated on configurable `xp_thresholds["Prestige"]` per guild (default 50,000 if key absent)
- **Levels** — deleted entirely in B.48. No level concept exists anywhere in the codebase.
- **Division system** — deleted entirely in B.48. `DivisionService` is gone.

### Prestige Reset (post-B.49)
- Prestige is a **full account reset** back to first-time `/register` state
- ALL ships deleted, ALL inventory cleared
- Betty recreated as active ship with standard starter loadout
- Preserved: lifetime_credits, duel stats, bounty stats, prestige_count (incremented)

### Inventory Contract
- `player_inventories.quantity` = ONLY unequipped (cargo) copies
- `player_ships.weapons/modules/turrets` JSON = equipped copies
- Total ownership = inventory.quantity + equipped across all ships
- `LoadoutConsistencyService` is the ONLY valid mutation point for equip/unequip ops (I1-I4 invariants)

### Discord Gateway Message Format
- Gateway API returns embeds where data is INSIDE `content` as a dict, NOT in top-level `embeds` array
- `msg["content"]` → embed-shaped data; `msg["embeds"]` → always empty

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

# Guild config (xp thresholds etc.)
sudo docker exec bountybot-bot-core curl -s http://localhost:8000/api/v1/config/guild/1490693399307616276 | python3 -m json.tool

# Active bounties
sudo docker exec bountybot-db psql -U bounty -d bountydb -c \
  "SELECT id, division, criminal_name, answer, end_time, status FROM bounty WHERE guild_id=1490693399307616276 AND status='active';"

# Force-fire a scheduled executor (replace job_type as needed):
# Valid: bounty_spawn, bounty_expire, duel_expire, temperature_decay, shop_refresh, bounty_respawn, time_announcement
sudo docker exec bountybot-bot-core curl -s -X POST -H 'Content-Type: application/json' \
  "http://localhost:8000/api/v1/jobs" \
  -d '{"delay_seconds":0,"payload":{"job_type":"bounty_spawn","guild_id":1490693399307616276}}'
```

---

## Session Setup Commands (Run After /admin_setup, Once Per Session)

```bash
GID=1490693399307616276

# Compress bounty timers
sudo docker exec bountybot-bot-core curl -s -X PUT -H 'Content-Type: application/json' \
  "http://localhost:8000/api/v1/config/guild/$GID/bounty" \
  -d '{"guild_id":'$GID',"bounty_spawn_interval_minutes":5,"bounty_expiry_minutes":10,"max_bounties_per_tier":{"bronze":20,"silver":20,"gold":20,"platinum":20}}'

# Lower XP thresholds for fast tier testing
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
| `/proj/DEFECTS.md` | Single source of truth for E2E-discovered defects |
| `/proj/E2E_TEST_CHECKLIST.md` | Full E2E checklist with operator instructions |
| `/proj/COMPACTION.md` | This file — session state for compaction |
| `/proj/AGENTS.md` | Root project standards |
| `/proj/services/bot-core/src/services/AGENTS.md` | Service layer patterns, LoadoutConsistencyService choke-point |
| `/proj/services/discord-gateway/src/cogs/AGENTS.md` | Cog patterns, admin check pattern |

---

## How to Properly Compact This Session

When compacting again in the future, the compaction doc MUST include:

1. **ALL Operating Directives** — orchestrator-doesn't-touch-containers, log-to-file mandate, test presentation format, subagent delegation roles, no-reuse-prior-task-id rule
2. **Account Context** — Main vs Alt with Discord IDs, whether Alt is admin, current stats, tier, ships, inventory
3. **E2E Phase Status** — which phases done vs pending, specific test IDs pending
4. **Committed Fixes** — B.NN entries with commit SHAs and one-line summaries
5. **Open Defects** — what needs live verification after next rebuild
6. **Architectural context** — key invariants that affect testing (progression system, inventory contract, prestige reset semantics)
7. **Session setup commands** — the curl commands needed at the start of each test session
8. **Don't include deep bug details** — commits cover that; compaction is for operational state

---

## Quick-Start for Next Agent

1. Read this file fully.
2. Check `DEFECTS.md` OPEN section for any unverified fixes.
3. Check `E2E_TEST_CHECKLIST.md` for pending tests (`grep "^- \[ \]"`).
4. Confirm stack is healthy: `sudo docker ps --format '{{.Names}}: {{.Status}}'`
5. **Rebuild is needed** before testing: B.51, B.52, B.53 are committed but not yet deployed.
6. After rebuild, start with Phase 8 dueling (8.1) and B.53 prestige role swap verification.
7. Present next tests in table format: `# / Account / Command / Notes`.
8. After user runs commands, verify DB/API side, mark `[x]` in checklist.
9. Defects → `/proj/DEFECTS.md` OPEN top, then user decides stop vs continue.
10. Do NOT push commits without explicit ask.
11. Do NOT touch containers under any circumstances.
