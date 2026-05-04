# BountyBot Session Compaction

**Generated**: 2026-05-04
**Session**: Phases 9–12 complete. Phase D (skins/rendering) in progress — D.1–D.4 done, D.5–D.15 next.

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
6. **🔴 NEVER BATCH-DELETE FILES/DIRS**: Always confirm per-item with user before deleting anything. The benchmarking/ folder was accidentally deleted this session — do not repeat.

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
- **Main**: `samx.ai` / `SamAccountX` / Discord `402296276617527306` / player_id=1 (was wiped by uninstall — needs re-register)
- **Alt**: `general_failure.` / Discord `970691862035841048` / player_id=2 (was wiped) / has `@Bounty Bot Admin` role
- **DEVELOPERS env var**: Main account ID `402296276617527306` (super-admin for scheduler/dev commands)

### Current Player State (post Phase 12 uninstall — all players wiped)
All players were wiped by `/admin_uninstall` in 12.20. Guild was re-setup via 12.26. Players need to re-register via `/profile`.

### Guild Config (post re-setup)
- Guild ID: `1490693399307616276`
- Admin role: `BountyBot Admin` ID `1495550109381951549`
- XP Thresholds: `{"Silver": 10, "Gold": 20, "Platinum": 30, "Prestige": 50}` (re-applied after setup)
- Starting credits: 0 (default — set higher if needed for testing)
- All channel IDs repopulated by `/admin_setup`

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
| 4 — Shop system | ✅ Done |
| 5 — Inventory + equipment | ✅ Done |
| 6 — Player progression | ✅ Done |
| 7 — Bounty hunting | ✅ Done |
| 8 — Dueling | ✅ Done |
| 9 — Scheduler | ✅ Done |
| 10 — Dev Tools | ✅ Done |
| 11 — Scheduled Jobs | ✅ Done (11.4 via executor, 11.5/11.6 via natural expiry) |
| 11.5 — Edge cases | ✅ Done |
| 12 — Admin + config | ✅ Done |
| **D — Blender/skins** | 🔄 **IN PROGRESS** — D.1–D.4 done, D.5–D.15 next |

### Phase D Status
| Item | Status | Notes |
|------|--------|-------|
| D.1 `/ship_skin ship:Betty skin:Default` | ✅ | Shows icon |
| D.2 `/ship_skin ship:Aegir skin:urban-camo` | ✅ | Shows skin image (Phantom XT wrong ship — see B.71) |
| D.3 `/ship_skin ship:Phantom XT skin:Default` | ✅ | Shows icon (skinnable=false at time of test) |
| D.4 `/ship_skin ship:Betty skin:nonexistent_skin` | ✅ | Error: skin not found |
| D.5–D.15 | ⏳ | **NEXT — 3D renders, texture compositing, edge cases** |

### Next Tests — Phase D Batch 2

| # | Account | Command | Notes |
|---|---------|---------|-------|
| D.5 | **Main** | `/render_skin ship:Betty skin:Default` | 1-region render — expect PNG attachment + AEI format buttons |
| D.6 | **Main** | `/render_skin ship:Aegir skin:urban-camo` | 2-region render with skin overlay |
| D.7 | **Main** | `/render_skin ship:Kinzer RS skin:racing-stripes` | 3-region render |
| D.8 | **Main** | Click "AEI (Android/ETC1)" on render result | Expect .aei file download |
| D.9 | **Main** | Click "AEI (PC/DXT5)" on render result | Expect .aei file download |
| D.10 | **Main** | `/make_skin_texture ship:Betty skin:Default` | 2D texture composite PNG (no 3D render) |
| D.11 | **Main** | `/make_skin_texture ship:Aegir skin:ferrari` | 2-region texture composite |
| D.12 | **Main** | `/render_skin ship:Amboss skin:Default` | skinnable=false — expect error |
| D.13 | **Main** | `/render_skin ship:Cronus skin:Default` | texture_regions=-1 edge case |
| D.14 | **Main** | `/ship_skin ship:Amboss skin:Default` | texture_regions=0, skinnable=false |
| D.15 | **Main** | `/ship_skin ship:Cronus skin:Default` | texture_regions=-1 edge case |

---

## Commits This Session

| SHA | What |
|-----|------|
| `eacc599` | docs: compaction doc update |
| (uncommitted) | super-admin gate (schedulerCog + devCog + adminCog) |
| (uncommitted) | devCog preload retry (B.73-style fix) |
| (uncommitted) | skinsCog preload retry (B.73) |
| (uncommitted) | shop defer ephemeral (B.69) |
| (uncommitted) | reset_to_defaults preserves infra config (B.66) |
| (uncommitted) | shop announcement tier-aware copy (B.70) |
| (uncommitted) | ship JSON skinnable=true for Blue Fyre, VoidX, Phantom XT, Salvéhn |

**All above changes are deployed in current stack but NOT committed to git.**

---

## Open Defects (summary)

| ID | Sev | Summary | Status |
|----|-----|---------|--------|
| B.73 | 🟡 | skinsCog preload retry (same pattern as DevCog) | **closed** — fixed + deployed |
| B.71 | ℹ️ | Phantom XT wrong test ship (skinnable=false) | **closed** — checklist corrected, JSON fixed |
| B.72 | ℹ️ | Vol Noor test expectation wrong | **closed** — checklist corrected |
| B.70 | 🔵 | Shop announce always said "all tiers" | **closed** |
| B.69 | 🔵 | /shop and /shops not ephemeral | **closed** |
| B.68 | 🟡 | Shop announce skipped after reset | **closed** |
| B.67 | 🔵 | duel_expire executor requires duel_id — bulk mode missing | **OPEN** |
| B.66 | 🟡 | reset_to_defaults nuked infra config | **closed** |
| B.62 | 🟡 | No display_name column | **DEFERRED** post-release |
| B.59 | 🔵 | base_reward lacks ge=0 guard | **OPEN** |
| B.58 | 🟡 | combat_bonus silent win on player-not-found | **OPEN** |
| A.20 | — | /ping visible to non-admins | **OPEN** |

---

## Key Architecture Notes (this session)

### Super-Admin Gate
- `_check_is_super_admin(interaction)` + `is_super_admin()` added to `adminCog.py`
- Checks `DEVELOPERS` env var only — no role fallback, no Discord Administrator fallback
- Applied to: all 6 schedulerCog commands + 2 devCog commands (`/load_data`, `/reload_autocomplete`)
- Regular admin commands unchanged

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
- `skinnable=true` + `compatibleSkins` populated → full static image support via `/ship_skin`
- `skinnable=true` + `compatibleSkins={}` → Blender render only (Phantom XT, Salvéhn)
- `skinnable=false` → no skin support (Amboss, Dark Angel, Vol Noor... wait — Vol Noor is actually `skinnable=true`)
- `texture_regions` → number of mask regions for Blender compositing (1, 2, or 3)
- `texture_regions=-1` → Cronus edge case

### Blender Asset Structure
```
mappings/blender-renderer/game-objects/    ← bind-mounted to /app/data/game-objects/
├── items/
│   └── ships/
│       ├── <Manufacturer>/
│       │   └── <Ship>.bbship/
│       │       ├── *.obj, *.mtl        — 3D model
│       │       ├── *_diffuse.png       — texture
│       │       ├── *_normal_specular.png
│       │       ├── mask1.png, mask2.png — skin region masks
│       │       └── skinBase.png        — base for compositing
└── ship skins/
    ├── colours/*.bbShipSkin
    └── special/*.bbShipSkin
```

### GDrive Asset Archive
- **Current file ID**: `1Z7S3ZtE7siZuSKuEob8cMmMicHXzVZLx` (old, pre-upscale)
- **Pending**: New 7z of upscaled assets being uploaded to GDrive (`game-objects-upscaled.7z` on G:\)
- **After upload**: Update `GAME_OBJS_FILEID` in both `.env` and `.env.example`
- Archive must contain folder named `"game objects"` (with space) — entrypoint renames it to `game-objects`
- Skip check: entrypoint skips download if `.bmp` or `.jpg` found in target dir (upscaled assets are PNG — clean skip won't trigger; a wipe + restart will correctly re-download)

### Static Skin Images
- Pre-composited skin PNGs hosted on **postimg.cc** — URLs stored in `compatibleSkins` JSON field
- Backed up locally as `skinned_ships.7z` (separate from the Blender assets 7z)
- Phantom XT + Salvéhn: have 3D assets but no pre-composited skin images uploaded yet

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

# Containers healthy?
sudo docker ps --format '{{.Names}}: {{.Status}}'
```

---

## Session Setup Commands (after /admin_setup + /profile)

```bash
GID=1490693399307616276

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

## Key Files

| File | Purpose |
|------|---------|
| `/proj/DEFECTS.md` | All open/closed defects |
| `/proj/E2E_TEST_CHECKLIST.md` | Full E2E checklist |
| `/proj/COMPACTION.md` | This file |
| `/proj/services/discord-gateway/src/cogs/adminCog.py` | `_check_is_super_admin`, `is_super_admin` |
| `/proj/services/discord-gateway/src/cogs/skinsCog.py` | Skin/render commands + preload retry |
| `/proj/services/discord-gateway/src/cogs/devCog.py` | `/load_data`, `/reload_autocomplete` + preload retry |
| `/proj/services/bot-core/src/utils/shop_announcement.py` | Tier-aware shop announcement |
| `/proj/services/bot-core/src/persist/repositories/config_repository.py` | `reset_to_defaults` preserves infra |
| `/proj/services/bot-core/import_data/ship/` | Ship JSON seed data (skinnable fixes applied) |

---

## Quick-Start for Next Agent

1. Read this file fully.
2. Confirm stack healthy: `sudo docker ps --format '{{.Names}}: {{.Status}}'`
3. Players were wiped — user needs to `/profile` to re-register before any game commands.
4. Reseed ships if needed: `sudo docker exec bountybot-bot-core curl -s -X POST http://localhost:8000/api/v1/data/ship` (skinnable fix requires this after each rebuild).
5. **Continue Phase D** — run D.5 through D.15 in the table above.
6. After Phase D: update COMPACTION with final test summary.
7. Pending GDrive upload: update `GAME_OBJS_FILEID` in `.env` and `.env.example` when user provides new file ID.
8. All code changes from this session are **NOT committed** — user will request commit when ready.
9. Do NOT commit without explicit ask. Do NOT touch containers.
