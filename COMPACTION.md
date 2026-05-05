# BountyBot Session Compaction

**Generated**: 2026-05-05
**Session**: All E2E phases complete. CI pipeline green. Bot permissions tightened. Stack nuked.

---

## ⚠️ CRITICAL OPERATING PROTOCOLS — READ EVERY TIME

### Orchestrator Constraints (NEVER VIOLATE)
1. **🔴 ORCHESTRATOR DOES NOT MODIFY CONTAINERS**: Never run `docker compose down/up/build/restart`. Only USER may delete/rebuild/restart the stack. Orchestrator has `sudo docker exec` for DB queries, API calls, log inspection ONLY.
2. **🔴 ORCHESTRATOR DELEGATES, DOES NOT IMPLEMENT**: Use subagents for code/tests/research. Orchestrator coordinates testing, presents results, updates tracking docs — does NOT write code directly (exception: trivial single-line targeted edits).
3. **🔴 LOG-TO-FILE MANDATE FOR ALL TEST SUITES**: ALWAYS pipe test runs to a log file ONCE. NEVER rerun a suite just to change grep patterns — read the log file.
   - `python -m pytest tests/ 2>&1 > /tmp/<descriptive>.log; echo "EXIT:$?"` with timeout 1200000ms
   - discord-gateway suite takes **15–22 minutes** — NEVER use tee with default 120s timeout
   - To check results: `grep -E "passed|failed|error" /tmp/<descriptive>.log | tail -5`
4. **🔴 SUBAGENTS WORK ON /proj REPO, NOT IN CONTAINERS**: All code changes happen in the working tree. Only USER rebuilds/deploys.
5. **🔴 DO NOT REUSE PRIOR TASK_ID SESSIONS**: Always start fresh subagent sessions.
6. **🔴 NEVER BATCH-DELETE FILES/DIRS**: Always confirm per-item with user before deleting anything.

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
- Stage only real changed files — never include file-mode-only noise
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
- "Pipe test runs to log file with timeout 1200000ms. NEVER rerun suites."

---

## Account Context

### Both Accounts Are Admins
- **Main**: `samx.ai` / `SamAccountX` / Discord `402296276617527306`
- **Alt**: `general_failure.` / Discord `970691862035841048` / has `@Bounty Bot Admin` role
- **DEVELOPERS env var**: Main account ID `402296276617527306` (super-admin for scheduler/dev commands)

### Stack State
- Stack was **nuked** after Phase D testing completed
- `mappings/` folders also wiped — DB data gone, Blender game-object assets gone
- Completely clean slate on next rebuild: fresh DB, assets re-downloaded from GDrive on startup
- Both accounts need `/profile` to re-register after next stack rebuild
- Guild needs `/admin_setup` after next rebuild
- **Update `GAME_OBJS_FILEID` before rebuilding** — new archive ID `1oGwq6fm4OwGAYvwG94hEEVGt5e7a5Z1_` (already set in .env)

---

## CI Pipeline Status — ALL GREEN ✅

| Suite | Result |
|-------|--------|
| blender-service | 127 passed |
| bot-core | 3169 passed, 1 skipped |
| discord-gateway | 2289 passed |
| ruff lint | clean |
| pylint | 10.00/10 |

**Last commit**: `bd898ca`

---

## E2E Test Progress — ALL PHASES COMPLETE

| Phase | Status |
|-------|--------|
| 0 — Stack health | ✅ Done |
| 0.5 — Pre-registration edge cases | ✅ Done |
| 1 — Setup + registration | ✅ Done |
| 1.5 — Non-admin permission denials | ⏳ Deferred post-release |
| 2 — Game data browsing + routes | ✅ Done |
| 2.5 — Help discoverability | ✅ Done |
| 3 — Ship management | ✅ Done |
| 4 — Shop system | ✅ Done |
| 5 — Inventory + equipment | ✅ Done |
| 6 — Player progression | ✅ Done |
| 7 — Bounty hunting | ✅ Done |
| 8 — Dueling | ✅ Done |
| 9 — Scheduler | ✅ Done |
| 10 — Dev Tools | ✅ Done |
| 11 — Scheduled Jobs | ✅ Done |
| 11.5 — Edge cases | ✅ Done |
| 12 — Admin + config | ✅ Done |
| D — Blender/skins | ✅ Done (D.5–D.11 tested; D.12–D.15 skipped as out of scope) |

---

## Open Defects

| ID | Sev | Summary | Status |
|----|-----|---------|--------|
| B.74 | 🟠 | AEI conversion fails — dimensions not multiples of 4 | **closed** — fixed |
| B.73 | 🟡 | skinsCog preload retry missing | **closed** — fixed |
| B.67 | 🔵 | `duel_expire` executor bulk mode missing | **OPEN** |
| B.62 | 🟡 | No display_name column | **DEFERRED** post-release |
| B.59 | 🔵 | `base_reward` lacks `ge=0` guard | **OPEN** |
| B.58 | 🟡 | `combat_bonus` silent win path (wrong player wins edge case) | **OPEN** — not exploitable |
| A.20 | 🔵 | `/ping` visible to non-admins | **OPEN** |

---

## Key Architecture Notes

### Bot Discord Permissions
- **Old**: `permissions=8` (Administrator — too broad)
- **New**: `permissions=2416438320` (minimum required set)
- **Invite URL**: `https://discord.com/oauth2/authorize?client_id=1379827884851593256&permissions=2416438320&integration_type=0&scope=bot`
- **Permissions included**: VIEW_CHANNEL, SEND_MESSAGES, MANAGE_MESSAGES, EMBED_LINKS, ATTACH_FILES, READ_MESSAGE_HISTORY, MENTION_EVERYONE, USE_EXTERNAL_EMOJIS, MANAGE_CHANNELS, MANAGE_GUILD, MANAGE_ROLES, USE_APPLICATION_COMMANDS
- **Role hierarchy**: Bot's managed role must be above all game-created roles. Since `/admin_setup` creates all game roles, bot must join server BEFORE running setup — roles land below bot's role automatically.
- Bot Admin role (pre-existing, user-specified at setup) is only READ for permission checks — bot never assigns it, so no hierarchy concern.

### Super-Admin Gate
- `_check_is_super_admin(interaction)` + `is_super_admin()` in `adminCog.py`
- Checks `DEVELOPERS` env var only — no role fallback, no Discord Administrator fallback
- Applied to: all 6 schedulerCog commands + 2 devCog commands (`/load_data`, `/reload_autocomplete`)

### Preload Retry Pattern (standard — apply to ALL cogs)
```python
delays = [5, 10, 20, 40, 60]
for attempt, delay in enumerate(delays, start=1):
    try:
        flogger.info("XxxCog: Starting preload (attempt %d/%d)...", attempt, len(delays))
        # ... fetch ...
        return
    except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as e:
        flogger.warning("XxxCog: Attempt %d/%d failed: %s — retrying in %ds", attempt, len(delays), e, delay)
        await asyncio.sleep(delay)
    except Exception as e:
        flogger.warning("XxxCog: Unexpected error attempt %d/%d: %s", attempt, len(delays), e)
        await asyncio.sleep(delay)
flogger.error("XxxCog: All preload attempts exhausted.")
```

### Ship Skinning Data Model
- `skinnable=true` + `compatibleSkins` populated → static image support via `/ship_skin`
- `skinnable=true` + `compatibleSkins={}` → Blender render only (Phantom XT, Salvéhn)
- `texture_regions` → number of mask regions for Blender compositing (1, 2, or 3)
- ALL 65 ships now have `skinnable=true`

### AEI Conversion (B.74 fix)
- AEPi requires image dimensions to be multiples of 4
- Fix: `round(dim / 4) * 4` snap in `aei_conversion_service.py` before encoding
- Uses `Image.NEAREST` — nearest-neighbor rescale

### GDrive Asset Archive
- **New file ID**: `1oGwq6fm4OwGAYvwG94hEEVGt5e7a5Z1_` — 15.2GB upscaled assets (already set in .env)
- Archive must contain folder named `"game objects"` (with space) — entrypoint renames to `game-objects`
- Skip check: entrypoint skips download if `.bmp` or `.jpg` found in target dir

### B.41 — Loadout consistency equip guard
- `loadout_consistency_service.equip_one()` now raises `ValueError("No unequipped copies remain")` when all copies of an item are already equipped across ships
- `inventoryCog` equip autocomplete now filters out fully-equipped items

### adminCog._check_is_admin (B.40)
- Now uses `interaction.member` directly (with fallback to `guild.get_member()`) rather than calling `get_member()` first

---

## Session Setup Commands (after /admin_setup + /profile)

```bash
GID=1490693399307616276

# Reseed ships (required after every rebuild — applies skinnable fixes)
sudo docker exec bountybot-bot-core curl -s -X POST http://localhost:8000/api/v1/data/ship

# Compressed XP thresholds
sudo docker exec bountybot-bot-core curl -s -X PUT -H 'Content-Type: application/json' \
  "http://localhost:8000/api/v1/config/guild/$GID/xp-thresholds" \
  -d '{"guild_id":'$GID',"thresholds":{"Silver":10,"Gold":20,"Platinum":30,"Prestige":50}}'

# Compressed bounty timers
sudo docker exec bountybot-bot-core curl -s -X PUT -H 'Content-Type: application/json' \
  "http://localhost:8000/api/v1/config/guild/$GID/bounty" \
  -d '{"guild_id":'$GID',"bounty_spawn_interval_minutes":5,"bounty_expiry_minutes":10,"max_bounties_per_tier":{"bronze":20,"silver":20,"gold":20,"platinum":20}}'
```

---

## Frequent DB / API Queries

```bash
# Player state
sudo docker exec bountybot-db psql -U bounty -d bountydb -c \
  "SELECT p.id, u.discord_username, p.tier, p.xp, p.credits, p.prestige_count FROM players p JOIN users u ON p.user_id = u.id ORDER BY p.id;"

# Guild config
sudo docker exec bountybot-bot-core curl -s http://localhost:8000/api/v1/config/guild/1490693399307616276 | python3 -m json.tool

# Active bounties
sudo docker exec bountybot-db psql -U bounty -d bountydb -c \
  "SELECT id, division, criminal_name, reward, status FROM bounty WHERE guild_id=1490693399307616276 AND status='active' LIMIT 10;"

# Skinnable ships
sudo docker exec bountybot-db psql -U bounty -d bountydb -c \
  "SELECT name, skinnable, texture_regions FROM ship WHERE skinnable=true ORDER BY texture_regions, name;"
```

---

## Key Files

| File | Purpose |
|------|---------|
| `/proj/DEFECTS.md` | All open/closed defects |
| `/proj/E2E_TEST_CHECKLIST.md` | Full E2E checklist |
| `/proj/COMPACTION.md` | This file |
| `/proj/.env` | `GAME_OBJS_FILEID` + bot invite URL (permissions updated) |
| `services/discord-gateway/src/cogs/adminCog.py` | `_check_is_super_admin`, `is_super_admin`, `_check_is_admin` (B.40) |
| `services/discord-gateway/src/cogs/skinsCog.py` | Skin/render commands + preload retry |
| `services/discord-gateway/src/cogs/devCog.py` | `/load_data`, `/reload_autocomplete` + preload retry |
| `services/discord-gateway/src/cogs/inventoryCog.py` | equip_autocomplete filters fully-equipped items |
| `services/bot-core/src/services/loadout_consistency_service.py` | B.41 equip guard |
| `services/bot-core/src/utils/shop_announcement.py` | Tier-aware shop announcement |
| `services/bot-core/src/persist/repositories/config_repository.py` | `reset_to_defaults` preserves infra |
| `services/bot-core/import_data/ship/` | Ship JSON seed data (skinnable fixes applied) |
| `services/blender-service/src/services/aei_conversion_service.py` | B.74 4px alignment fix |

---

## Next Steps

1. Address remaining open defects (B.67, B.59, B.58, A.20) — user decides priority
2. **Phase 1.5** (non-admin permission denials) — deferred, revisit pre-release
3. Stack rebuild when ready — bot invite URL updated, use new permissions integer
4. After rebuild: bot joins first, then `/admin_setup`, then `/profile` both accounts, then reseed ships

---

## Planned Feature: `/notifications`

### Design

New slash command: `/notifications type:bounty|shop enabled:true|false`

**`type:bounty`**
- `enabled:true` → assign user's current DB tier role (`@Bounty Hunter Bronze/Silver/Gold/Platinum`)
- `enabled:false` → remove user's current tier role. `@Bounty Hunter` is NOT touched — user keeps full channel access, just won't be @-mentioned in bounty announcements

**`type:shop`**
- `enabled:true` → assign new `@Shop Announcements` role
- `enabled:false` → remove `@Shop Announcements` role
- Shop refresh announcements should @mention `@Shop Announcements` instead of `@Bounty Hunter`

### Why this works
Tier roles (`@Bounty Hunter Bronze/Silver/Gold/Platinum`) have **zero permission overwrites** on any channel — confirmed via live permission audit. They are purely cosmetic badges used for @-mentions in bounty announcements. `@Bounty Hunter` is the sole access gate for all BountyBot channels. Removing a tier role does not affect channel visibility or gameplay at all.

### Implementation requirements

| # | What | Where |
|---|------|-------|
| 1 | New role: `@Shop Announcements` created by `/admin_setup` | `guild_setup.py` |
| 2 | New DB column: `shop_announcements_role_id` on `guild_configs` | New Alembic migration |
| 3 | New slash command: `/notifications` | New cog or added to `playerCog.py` |
| 4 | Shop refresh announcement mentions `@Shop Announcements` instead of `@Bounty Hunter` | `shop_refresh_executor.py` + gateway announcement endpoint |
| 5 | `/unregister` also removes `@Shop Announcements` if present | `playerCog.py` |
| 6 | `/promote` and `/prestige` gracefully handle missing tier role (already non-fatal, just document) | `playerCog.py` — already handled |
| 7 | Update `/help` to include `/notifications` in the appropriate category | `adminCog.py` or help data |

### Permission utilities to use
- `GET /api/v1/permissions/convert/value-to-names` — decode bitfields
- `GET /api/v1/permissions/convert/names-to-value` — encode permission sets
- `GET /api/v1/permissions/calculate` — compute effective permissions with inheritance
