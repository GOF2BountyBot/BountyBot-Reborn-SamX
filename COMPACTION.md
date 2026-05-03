# BountyBot Session Compaction

**Generated**: 2026-05-03
**Session**: Phase 8 (dueling) complete. Stack rebuilt and deployed. Phase 9 (scheduler) is next.

---

## ⚠️ CRITICAL OPERATING PROTOCOLS — READ EVERY TIME

### Orchestrator Constraints (NEVER VIOLATE)
1. **🔴 ORCHESTRATOR DOES NOT MODIFY CONTAINERS**: Never run `docker compose down/up/build/restart`. Only USER may delete/rebuild/restart the stack. Orchestrator has `sudo docker exec` for DB queries, API calls, log inspection ONLY.
2. **🔴 ORCHESTRATOR DELEGATES, DOES NOT IMPLEMENT**: Use subagents for code/tests/research. Orchestrator coordinates testing, presents results, updates tracking docs — does NOT write code directly (exception: trivial single-line targeted edits).
3. **🔴 LOG-TO-FILE MANDATE FOR ALL TEST SUITES**: ALWAYS pipe test runs to a log file ONCE. NEVER rerun a suite just to change grep patterns — read the log file.
   - `python -m pytest tests/ 2>&1 | tee /tmp/<descriptive>.log`
   - discord-gateway suite takes 15-22 minutes
4. **🔴 SUBAGENTS WORK ON /proj REPO, NOT IN CONTAINERS**: All code changes happen in the working tree. Only USER rebuilds/deploys.
5. **🔴 DO NOT REUSE PRIOR TASK_ID SESSIONS**: Always start fresh subagent sessions.

### Test Presentation Format (MANDATORY)

| # | Account | Command | Notes |
|---|---------|---------|-------|

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

**ALL delegations MUST include**:
- "Do NOT commit changes"
- "Do NOT modify containers"
- "Pipe test runs to log file: `2>&1 | tee /tmp/<descriptive>.log`. NEVER rerun suites."

---

## Account Context

### Both Accounts Are Admins
- **Main**: `samx.ai` / `SamAccountX` / Discord `402296276617527306` / player_id=1
- **Alt**: `general_failure.` / Discord `970691862035841048` / player_id=2 / has `@Bounty Bot Admin` role (ID: `1495550109381951549`)
- **Alt2**: player_id=3, credits=999,999,999, no username stored yet (registered during testing)

### Current Player State (post Phase 8, 2026-05-03)
| Account | Tier | XP | Credits | Prestige | Active Ship |
|---------|------|----|---------|----------|-------------|
| Main (samx.ai) | Bronze | 726 | 6,912 | 2 ⭐⭐ | Betty id=7 |
| Alt (general_failure.) | Bronze | 897 | 1,000,009,275 | 0 | Betty id=2 |
| Alt2 (player_id=3) | Bronze | 0 | 999,999,999 | 0 | Betty id=8 |

### Guild Config
- Guild ID: `1490693399307616276`
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
| 1.5 — Non-admin permission denials | ⏳ Deferred post-release |
| 2 — Game data browsing + routes | ✅ Done |
| 2.5 — Help discoverability | ✅ Done |
| 3 — Ship management | ✅ Done |
| 4 — Shop system | ✅ Done (4.8 deferred) |
| 5 — Inventory + equipment | ✅ Done (5.5/A.39 deferred) |
| 6 — Player progression | ✅ Done |
| 7 — Bounty hunting | ✅ Done |
| **8 — Dueling** | ✅ **COMPLETE** |
| **9 — Scheduler** | ⏳ **NEXT** |
| 10 — Data loading | ⏳ Not started |
| 11 — Edge cases | ⏳ Not started |
| 12 — Admin + config | ⏳ Not started |
| D — Blender/skins | ⏳ Not started |

### Phase 8 Verified (all ✅)
8.1 challenge+ping, 8.2 accept+embed, 8.3 credits/profiles, 8.4 stalemate noted, 8.5 new challenge, 8.6 reject, 8.7 self-duel blocked, 8.8 zero-stakes, 8.9 insufficient credits, 8.10 duplicate blocked, 8.11 auto-expiry via executor

### Phase 9 — Next Tests

| # | Account | Command | Notes |
|---|---------|---------|-------|
| 9.1 | Main | `/scheduler_list` | Lists bounty_spawn_default, temperature_decay_default, shop_refresh_default with trigger + next run |
| 9.2 | Main | `/scheduler_view job_id:bounty_spawn_default` | Full job details including payload JSON |
| 9.3 | Main | `/scheduler_update job_id:bounty_spawn_default payload_json:{"job_type": "bounty_spawn"}` | Updates payload; confirmation |
| 9.4 | Main | `/scheduler_update job_id:bounty_spawn_default payload_json:invalid` | Invalid JSON → error |
| 9.5 | Main | `/scheduler_view job_id:nonexistent_job` | Error: job not found |
| 9.6 | Main | `/scheduler_delete job_id:<test_job_id>` | Only delete a job you can recreate |

---

## All Commits This Session (B.54 onwards)

| SHA | What |
|-----|------|
| `4814209` | B.54 bounty winner-reserve reward model |
| `efafcf4` | B.55 duel accept crash fix, B.56 target ping, B.57 unified PvC/PvP combat + 1.5× armour buff |
| `cd6fc8f` | B.60 challenger name in autocomplete + reject embed broken mention fix |
| `3002fce` | B.61 target_name in duel accept response |
| `1ec57b1` | B.63 player name instead of ship name in duel result embed |
| `8b8fb7e` | Humanise duplicate duel error (no PKs/guild IDs) |
| `f68e07a` | B.64 `/duel-cancel` + B.65 `/admin_duel cancel` |
| `2c71dc6` | Admin duel autocomplete (live pending duels, cancel-all) |

---

## Open Defects (summary)

| ID | Sev | Summary | Status |
|----|-----|---------|--------|
| B.58 | 🟡 | `combat_bonus` silent win on player-not-found | OPEN |
| B.59 | 🔵 | `base_reward` lacks `ge=0` schema guard | OPEN |
| B.62 | 🟡 | No display_name column — shows discord_username everywhere | DEFERRED post-release |
| B.49 | — | Per-guild configurability audit for operational constants | DEFERRED post-release |
| B.50 | — | Confirm UX revamp (button-style) | DEFERRED post-release |
| A.20 | — | `/ping` visible to non-admins (visibility leak) | OPEN |
| A.39 | — | `/item` mismatched item_type returns "Not Owned" | DEFERRED post-release |

### Pre-existing test failures (not introduced this session)
- `test_config_repository.py::test_create_default_config` — xp_thresholds Prestige key
- `test_loadout_consistency_service.py::TestEquipOne` × 3 — equip-all-copies logic

---

## Key Architecture — Duel System (post this session)

### Combat Path (unified, B.57)
Both PvP and PvC go through `CombatService.fight_ships(loadout1, loadout2, player_armour_buff=1.0)`:
- **PvP duels**: `player_armour_buff=1.0` (no buff) — called from `duel_service.accept_duel()`
- **PvC bounty combat**: `player_armour_buff=GameConstants.BOUNTY_PVC_ARMOUR_BUFF_FACTOR` (default 1.5) — called from `bounties.py combat_bonus`
- `BOUNTY_PVC_ARMOUR_BUFF_FACTOR = 1.5` in `GameConstants`, env-overridable via `BOUNTYBOT_BOUNTY_PVC_ARMOUR_BUFF_FACTOR`

### Duel Response Shape (accept endpoint)
```python
{
  "duel_id", "is_stalemate", "winner_name", "loser_name",
  "credits_transferred", "stakes",
  "challenger_id", "challenger_credits", "challenger_name", "challenger_hp", "challenger_dps",
  "target_id", "target_credits", "target_name", "target_hp", "target_dps"
}
```
Winner/loser determination in cog: re-derives TTK from HP/DPS since challenger=ship1 always.

### Duel Commands
| Command | Who | Notes |
|---------|-----|-------|
| `/duel-challenge` | Challenger | Pings target as plain content (B.56) |
| `/duel-accept` | Target | Autocomplete shows `"samx.ai — 100cr stakes"` |
| `/duel-reject` | Target | Same autocomplete as accept |
| `/duel-cancel` | Challenger | New B.64 — withdraws own pending challenge |
| `/admin_duel` | Admin | B.65 — autocomplete shows all guild pending duels; "All" option cancels all |

### Duel Endpoints (bot-core)
| Method | Path | Notes |
|--------|------|-------|
| POST | `/duels/challenge` | Create challenge |
| POST | `/duels/{id}/accept` | Resolve combat, transfer credits |
| POST | `/duels/{id}/reject` | Target declines |
| POST | `/duels/{id}/cancel?user_id=` | Challenger self-cancel (B.64) |
| GET | `/duels/pending?user_id=&guild_id=` | Incoming pending (target autocomplete) |
| GET | `/duels/outgoing?user_id=&guild_id=` | Outgoing pending (challenger autocomplete) |
| GET | `/duels/pending-all?guild_id=` | All guild pending (admin autocomplete) |
| POST | `/duels/{id}/admin-cancel?admin_user_id=` | Admin cancel one (B.65, audit logged) |
| POST | `/duels/admin-cancel-all?guild_id=&admin_user_id=` | Admin cancel all (audit logged) |

---

## Frequent DB / API Queries

```bash
# Player state
sudo docker exec bountybot-db psql -U bounty -d bountydb -c \
  "SELECT p.id, u.discord_username, p.tier, p.xp, p.credits, p.prestige_count FROM players p JOIN users u ON p.user_id = u.id ORDER BY p.id;"

# Duel state
sudo docker exec bountybot-db psql -U bounty -d bountydb -c \
  "SELECT id, challenger_id, target_id, stakes, status FROM duel_requests ORDER BY id DESC LIMIT 10;"

# Guild config
sudo docker exec bountybot-bot-core curl -s http://localhost:8000/api/v1/config/guild/1490693399307616276 | python3 -m json.tool

# Active bounties
sudo docker exec bountybot-db psql -U bounty -d bountydb -c \
  "SELECT id, division, criminal_name, reward, status FROM bounty WHERE guild_id=1490693399307616276 AND status='active' LIMIT 10;"

# Fire a scheduled job
sudo docker exec bountybot-bot-core curl -s -X POST -H 'Content-Type: application/json' \
  "http://localhost:8000/api/v1/jobs" \
  -d '{"delay_seconds":0,"payload":{"job_type":"duel_expire","duel_id":N}}'

# Containers healthy?
sudo docker ps --format '{{.Names}}: {{.Status}}'
```

---

## Session Setup Commands (once per session, after /admin_setup)

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
```

---

## Key Files

| File | Purpose |
|------|---------|
| `/proj/DEFECTS.md` | All open/closed defects |
| `/proj/E2E_TEST_CHECKLIST.md` | Full E2E checklist |
| `/proj/COMPACTION.md` | This file |
| `/proj/services/bot-core/src/services/AGENTS.md` | Service layer patterns |
| `/proj/services/discord-gateway/src/cogs/AGENTS.md` | Cog patterns |
| `/proj/services/bot-core/src/services/combat_service.py` | Unified combat engine |
| `/proj/services/discord-gateway/src/cogs/duelCog.py` | All duel slash commands |
| `/proj/services/bot-core/src/api/routers/duels.py` | All duel API endpoints |

---

## Quick-Start for Next Agent

1. Read this file fully.
2. Confirm stack healthy: `sudo docker ps --format '{{.Names}}: {{.Status}}'`
3. Stack is deployed with all commits through `2c71dc6`.
4. **Start Phase 9** — scheduler commands. Present tests in table format, Account column mandatory.
5. After user runs commands, verify via API/DB before marking `[x]` in checklist.
6. Defects → top of OPEN in `DEFECTS.md`, user decides stop vs continue.
7. Do NOT commit without explicit ask. Do NOT touch containers.
