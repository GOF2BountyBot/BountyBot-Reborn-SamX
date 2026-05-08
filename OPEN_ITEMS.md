# BountyBot Open Items

Consolidated from `BLITZ_PLAN.md`, `COMPACTION.md`, `E2E_TEST_CHECKLIST.md`, `DEFECTS.md`, and `DEFECTS_TEST_REVAMP.md`.
Generated: 2026-05-08

---

## 1. Test Quality / CI

| ID | Source | Sev | Summary | Notes |
|----|--------|-----|---------|-------|
| DEF-S11-003 | DEFECTS_TEST_REVAMP | 🔵 | 282 bare `assert_called_once()` patterns remain across test suites | Need to verify none are the sole assertion in their test; if any are, delete or replace |
| DEF-S11-004 | DEFECTS_TEST_REVAMP | 🔵 | `test_setupCog.py:413` — F401 (`discord` imported but unused) | Auto-fixable ruff error; check if this is still present after recent formatting |

---

## 2. Production Defects — Open

| ID | Source | Sev | Summary | Notes |
|----|--------|-----|---------|-------|
| B.82 | DEFECTS | 🔵 | Combat summary embed should surface the PvC armour buff (Kieth T Maxwell bonus) | Add `pvc_armour_buff_applied: bool` + `pvc_armour_buff_factor: float` to `/combat-bonus` response (`bot-core/src/api/routers/bounties.py`); read in `bountyCog.py` combat summary embed builder |
| B.72 | DEFECTS | ℹ️ | E2E D.3 test expectation wrong — `/ship_skin ship:Vol Noor` works fine (skinnable=true); use a `skinnable=false` ship (e.g. Phantom XT) to test the no-skin error path | Doc fix only when E2E checklist is recreated |
| B.71 | DEFECTS | ℹ️ | E2E D.2 ship (Phantom XT) has `skinnable=false` — use Aegir/Badger/Furious for 2-region; Kinzer RS/Razor 6 for 3-region | Doc fix only when E2E checklist is recreated |
| B.67 | DEFECTS | 🔵 | `duel_expire` executor requires `duel_id` in payload; firing it without one logs ERROR silently and does nothing — no bulk sweep mode | Option A: add bulk mode when `duel_id` omitted (expire all past `expires_at`) in `bot-core/src/utils/executors/duel_expire_executor.py`. Option B: just document the limitation. |
| B.62 | DEFECTS | 🟡 | No `display_name` column — all player-facing name fields show `discord_username` (e.g. `samx.ai`) instead of display name (e.g. `SamAccountX`) | Requires: Alembic migration adding `display_name: str \| None` to `users` table; populate on `/register` from `interaction.user.display_name`; update all name-resolution in cogs and bot-core. Deferred post-release. |
| B.63 | DEFECTS | 🟡 | Duel result embed shows ship model name (e.g. "Betty") as winner/loser — ambiguous when both players use the same ship | Blocked by B.62. Files: `bot-core/src/api/routers/duels.py`, `discord-gateway/src/cogs/duelCog.py` |
| B.59 | DEFECTS | 🔵 | `CombatBonusRequest.base_reward` missing `Field(ge=0)` guard — negative value would subtract credits on a "win" | One-liner: `base_reward: int = Field(..., ge=0)` in `bot-core/src/api/schemas/bounty_schema.py` |
| B.58 | DEFECTS | 🟡 | `combat_bonus` silently returns `won=True, bonus_credits=0` when player not found — player is told they won credits they never received | Add 404 guard after `player_repo.get_by_id()` in `bot-core/src/api/routers/bounties.py:214–221`; log at ERROR level |

---

## 3. Production Defects — Closed

Fixed-pending-verify items are treated as closed. Verified against code; live re-test is confirmatory only.

| ID | Sev | Summary | Evidence |
|----|-----|---------|---------|
| B.80 | 🔵 | `/admin_give_item` `item_type` param removed | `adminCog.py:1732-1802` |
| B.77 | 🟡 | A* heuristic → `0.0` constant (Dijkstra) | `pathfinding_service.py:59-65` |
| B.74 | 🟠 | AEI dimension snapped to nearest 4px | `aei_conversion_service.py:98-104` |
| B.73 | 🟡 | skinsCog preload retry `[5,10,20,40,60]s` | `skinsCog.py:261-304` |
| B.57 | 🟠 | PvC armour buff + unified PvP/PvC fight path | `combat_service.py`, `game_constants.py` |
| B.55 | 🟠 | Duel accept uses `varied_hp` (not broken `challenger_health`) | `duels.py:199,219-220` |
| B.53 | 🟠 | Prestige swaps tier roles (removes old, adds Bronze) | `playerCog.py:368-413` |
| B.52 | 🟡 | Criminal ship selection filters `max_primaries > 0` | `bounty_service.py:368,379` |
| B.51 | 🟠 | duelCog resolves Discord IDs → player PKs via `_get_player_id()` | `duelCog.py:155-184` |
| B.48 | 🟠 | Prestige resets to starter Betty; level/division system deleted | `player_service.py:336-447` |
| B.63 | 🟡 | Duel result embed shows player names not ship names | `duelCog.py:368-372` |
| B.61 | 🔵 | Accept embed includes `target_name` | `duels.py:51-53` |
| B.60 | 🔵 | Duel autocomplete shows challenger/target name not duel ID | `duelCog.py:88-91` |
| B.39 | 🟡 | `/promote` removes old tier role when adding new one | `playerCog.py:488-533` |

---

## 4. Enhancements / Future Work

| ID | Source | Sev | Summary | Notes |
|----|--------|-----|---------|-------|
| B.50 | DEFECTS | 🔵 | Replace typed `CONFIRM` strings with button dialogs (prestige/uninstall/clear-bounties) | `ConfirmView` is already implemented — wiring it to prestige is the remaining work |
| B.49 | DEFECTS | 🔵 | Audit hardcoded game constants for per-guild configurability | Researcher → architect → developer loop; ~10-15 files, 1 Alembic migration |
| B.83 | LIVE | 🟡 | Intermittent: expired bounties occasionally not auto-cleaned (Discord post not deleted) | Observed in production — not consistently reproducible. Needs investigation: APScheduler job firing but gateway DELETE failing silently? Race between expire job and bounty already-deleted state? Check `bounty_expire_executor` error handling and gateway 404 tolerance. Discuss before investigating. |
| CI-01 | FUTURE | 🔵 | Extend GitHub Actions workflow to pre-build and push all service images to GHCR | Build 4 images: `bot-core`, `discord-gateway`, `blender-service` (CUDA), `blender-service` (non-CUDA / CPU-only). Tag by commit SHA + `latest`. Use GHCR (free, no pull limits, no inactive expiry). Also review rebasing `bot-core` and `discord-gateway` base images to something slimmer (e.g. `python:3.12-slim` or `python:3.12-alpine`) to reduce layer size and build time. **Requires a local dev story**: `docker-compose.yml` must remain usable for fully local builds (`build:` context) without needing GHCR credentials or a pre-pushed image — likely via a `docker-compose.override.yml` or a `--profile` split so devs can `docker compose up --build` locally while CI/prod pulls pre-built images from GHCR. |

---

## 5. Stack Rebuild Checklist

When the stack is next rebuilt:

- [ ] Update `GAME_OBJS_FILEID` in `.env` — new archive ID `1oGwq6fm4OwGAYvwG94hEEVGt5e7a5Z1_` (already set)
- [ ] Bot invite URL: `https://discord.com/oauth2/authorize?client_id=1379827884851593256&permissions=2416438320&integration_type=0&scope=bot`
- [ ] Bot must join server **before** `/admin_setup` so game roles land below bot's managed role
- [ ] Run `/admin_setup` → `/profile` both accounts → reseed ships → apply golden config below
- [ ] Run Phase 1.5 (non-admin permission denials) before release
- [ ] Run Phase 8 (dueling) with both accounts

### Golden Config Commands (run after `/admin_setup` + `/profile`)

```bash
GID=1490693399307616276

# Reseed ships (required after every rebuild — applies skinnable fixes)
sudo docker exec bountybot-bot-core curl -s -X POST http://localhost:8000/api/v1/data/ship

# Compressed XP thresholds (Silver:10, Gold:20, Platinum:30, Prestige:50)
sudo docker exec bountybot-bot-core curl -s -X PUT -H 'Content-Type: application/json' \
  "http://localhost:8000/api/v1/config/guild/$GID/xp-thresholds" \
  -d '{"guild_id":'$GID',"thresholds":{"Silver":10,"Gold":20,"Platinum":30,"Prestige":50}}'

# Compressed bounty timers (spawn 5min, expire 10min, max 20 per tier)
sudo docker exec bountybot-bot-core curl -s -X PUT -H 'Content-Type: application/json' \
  "http://localhost:8000/api/v1/config/guild/$GID/bounty" \
  -d '{"guild_id":'$GID',"bounty_spawn_interval_minutes":5,"bounty_expiry_minutes":10,"max_bounties_per_tier":{"bronze":20,"silver":20,"gold":20,"platinum":20}}'
```

### Frequent DB / API Queries

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

## 6. Account Context

| Account | Username | Discord ID | Notes |
|---------|----------|------------|-------|
| Main | `samx.ai` / `SamAccountX` | `402296276617527306` | In `DEVELOPERS` env var — super-admin for scheduler/dev commands |
| Alt | `general_failure.` | `970691862035841048` | Has `@Bounty Bot Admin` role |

**Bot user**: `1379827884851593256` (BountyBot-SamX)

---

## 7. Architecture Reference

### Bot Discord Permissions
- **Permissions integer**: `2416438320` (minimum required set)
- **Includes**: VIEW_CHANNEL, SEND_MESSAGES, MANAGE_MESSAGES, EMBED_LINKS, ATTACH_FILES, READ_MESSAGE_HISTORY, MENTION_EVERYONE, USE_EXTERNAL_EMOJIS, MANAGE_CHANNELS, MANAGE_GUILD, MANAGE_ROLES, USE_APPLICATION_COMMANDS

### Super-Admin Gate
- `_check_is_super_admin()` / `is_super_admin()` in `adminCog.py`
- Checks `DEVELOPERS` env var only — no role fallback, no Discord Administrator fallback
- Applied to: all 6 schedulerCog commands + 2 devCog commands (`/load_data`, `/reload_autocomplete`)

### Preload Retry Pattern (standard for all cogs)
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
- All 65 ships have `skinnable=true`

### GDrive Asset Archive
- **File ID**: `1oGwq6fm4OwGAYvwG94hEEVGt5e7a5Z1_` — 15.2GB upscaled assets (set in `.env`)
- Archive must contain folder named `"game objects"` (with space) — entrypoint renames to `game-objects`
- Entrypoint skips download if `.bmp` or `.jpg` already found in target dir

### Loadout Consistency Equip Guard (B.41)
- `loadout_consistency_service.equip_one()` raises `ValueError("No unequipped copies remain")` when all copies already equipped across ships
- `inventoryCog` equip autocomplete filters out fully-equipped items

---

## 8. Deferred (Intentionally Not Doing Now)

| Item | Why deferred | When to revisit |
|------|-------------|-----------------|
| B.62 — display_name column | Significant schema migration; post-release | When display names become user-reported pain point |
| Phase 1.5 — permission denial E2E | Requires careful multi-account coordination | Pre-release milestone |
| Phase D — GPU rendering E2E | Requires GPU-enabled blender-service container | When GPU hardware is available |
| B.49 — per-guild game constants | Broad scope, low urgency | When operators request tuning controls |
