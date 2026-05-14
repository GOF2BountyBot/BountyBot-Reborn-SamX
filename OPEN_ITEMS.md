# BountyBot Open Items

Last updated: 2026-05-14

---

## 1. Test Quality / CI

| ID | Sev | Summary | Notes |
|----|-----|---------|-------|
| DEF-S11-003 | 🔵 | 285 bare `assert_called_once()` patterns remain across test suites | Verify none are the sole assertion in their test; delete or replace any that are |

---

## 2. Production Defects — Open

| ID | Sev | Summary | Notes |
|----|-----|---------|-------|
| B.82 | 🔵 | Combat summary embed should surface the PvC armour buff (Keith T Maxwell bonus) | Add `pvc_armour_buff_applied: bool` + `pvc_armour_buff_factor: float` to `/combat-bonus` response (`bot-core/src/api/routers/bounties.py`); read in `bountyCog.py` combat summary embed builder |
| B.67 | 🔵 | `duel_expire` executor has no bulk sweep mode — requires `duel_id` in payload; firing without one returns error and does nothing | Option A: add bulk mode when `duel_id` omitted (expire all past `expires_at`) in `bot-core/src/utils/executors/duel_expire_executor.py`. Option B: document the limitation. |
| B.63 | 🟡 | Duel result embed is ambiguous when both players use the same ship model (both show e.g. "Betty") | Blocked by B.62. Files: `bot-core/src/api/routers/duels.py`, `discord-gateway/src/cogs/duelCog.py` |
| B.62 | 🟡 | No `display_name` column — all player-facing name fields show `discord_username` (e.g. `samx.ai`) instead of display name (e.g. `SamAccountX`) | Requires: Alembic migration adding `display_name: str \| None` to `users` table; populate on `/register` from `interaction.user.display_name`; update all name-resolution in cogs and bot-core |

---

## 3. Production Defects — Closed

Fixed-in-code items are treated as closed. Live re-test is confirmatory only.

| ID | Sev | Summary | Evidence |
|----|-----|---------|---------|
| B.86 | 🟠 | Bounty 546 post-mortem: `/promote` mid-tier combat loss reset route to all-`-1`, player soft-locked on "No Bounty" until expiry | Fixed on branch `feat/promote-flow-correctness`: promote/demote ConfirmView flow + 24h tier-change cooldown + forfeit sentinel `-2` + strict same-tier shop + 20-sim combat preflight. Commits `8c6437b`..`b1448bd` |
| B.85 | 🔵 | No distinct `WRONG_TIER` result for `/check` against a bounty outside player's tier | Won't-fix: bounty routes only live while the bounty is active, and `/promote` forfeit-scrub already clears tier-mismatched references |
| B.87 | 🔵 | No credit refund for forfeited checks on `/promote` | Won't-fix (testing context): affected player already granted 1,000,000 cr quick-start |
| B.80 | 🔵 | `/admin_give_item` `item_type` param removed | `adminCog.py:1732-1802` |
| B.77 | 🟡 | A* heuristic → `0.0` constant (Dijkstra) | `pathfinding_service.py:59-65` |
| B.74 | 🟠 | AEI dimension snapped to nearest 4px | `aei_conversion_service.py:98-104` |
| B.73 | 🟡 | skinsCog preload retry `[5,10,20,40,60]s` | `skinsCog.py:261-304` |
| B.59 | 🔵 | `CombatBonusRequest.base_reward` missing `Field(ge=0)` guard | Fixed: `bounty_schema.py:127` — `base_reward: int = Field(ge=0, ...)` |
| B.58 | 🟡 | `combat_bonus` silently returned `won=True, bonus_credits=0` when player not found | Fixed: `bounties.py:200-203` — 404 guard added before loadout build |
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
| DEF-S11-004 | 🔵 | `test_setupCog.py` — F401 (`discord` imported but unused) | Verified clean: `ruff check` passes with no errors |

---

## 4. Enhancements / Future Work

| ID | Sev | Summary | Notes |
|----|-----|---------|-------|
| CI-01 | 🔵 | Extend GitHub Actions workflow to pre-build and push all service images to GHCR | Build 4 images: `bot-core`, `discord-gateway`, `blender-service` (CUDA), `blender-service` (non-CUDA / CPU-only). Tag by commit SHA + `latest`. Use GHCR (free, no pull limits, no inactive expiry). Also review rebasing `bot-core` and `discord-gateway` base images to something slimmer (e.g. `python:3.12-slim`) to reduce layer size. **Requires a local dev story**: `docker-compose.yml` must remain usable for fully local builds without needing GHCR credentials — likely via `docker-compose.override.yml` or a `--profile` split. |
| B.88 | 🔵 | `/admin_config_constants` UX is rough — needs redesign | Flagged during `feat/promote-flow-correctness` work; deferred out of that PR. Constant-editing admin flow suffers param sprawl and poor discoverability — wants a usability pass (grouped categories, search/autocomplete, clearer reset semantics). |
| B.84 | 🔵 | Prefix command support for high-use commands (mobile / legacy Discord client compatibility) | For users on mobile using 3rd-party Discord apps that don't support slash commands. **Approach**: Separate prefix layer (Approach B from researcher report in `activity.md`) — add `@commands.command()` wrappers alongside existing slash commands, sharing business logic via private `_do_x()` methods. **Scope**: High-use candidates only — suggested starters: `!check <system>`, `!bounties`, `!duel <@user> [stakes]`, `!buy <item_id> <qty>`, `!sell <item_name> <qty>`, `!profile`, `!inventory`. **Hard/skip**: `/equip`, `/ship`, `/setactive`, `/route` (exotic system names with accented characters — worse UX without autocomplete), all skins commands. **Required supporting work**: (1) `!help` — lists all available prefix commands with one-line descriptions; (2) `!help <command>` — shows syntax, arguments, and an example; (3) Per-guild configurable prefix stored in `guild_configs` — default `!` is likely already in use by other bots; expose via `/admin_config` or new `guild_configs.prefix` column + Alembic migration; `GatewayBot` must use `get_prefix()` async callable instead of static string. **Admin check prerequisite**: `is_admin()` currently only handles `discord.Interaction` — needs refactor to also accept `commands.Context`. `message_content` intent and `commands.Bot` base class are already correctly configured. |

### Resolved Enhancements

| ID | Sev | Summary | Evidence |
|----|-----|---------|---------|
| B.83 | 🟢 | Intermittent: expired bounties occasionally not auto-cleaned | **Fixed**: Added `bounty_failsafe_cleanup_executor` — hourly Discord-first sweep; classifies each channel post against DB (live/expired/captured/orphan); deletes stale posts + DB records. Registered as `bounty_failsafe_cleanup_default` cron at :30 past every hour. 17 tests. |
| B.50 | 🟢 | Replace typed `CONFIRM` strings with button dialogs (prestige/uninstall/clear-bounties) | **Fixed**: `ConfirmView` implemented and wired to all three flows. Committed `6e3edfa`. |
| B.49 | 🟢 | Per-guild game constants (audit hardcoded values for configurability) | **Fixed**: 25 new nullable `GuildConfig` columns, `resolve_constant()` helper, Alembic migration `0005`, 3 new admin slash commands (`/admin_config_constants`, `/admin_config_constants_view`, `/admin_config_constants_reset`), 67 new tests. Committed `6e3edfa`. |

---

## 5. Stack Rebuild Checklist

When the stack is next rebuilt:

- [ ] Update `GAME_OBJS_FILEID` in `.env` — archive ID `1oGwq6fm4OwGAYvwG94hEEVGt5e7a5Z1_` (already set)
- [ ] Bot invite URL: `https://discord.com/oauth2/authorize?client_id=1379827884851593256&permissions=2416438320&integration_type=0&scope=bot`
- [ ] Bot must join server **before** `/admin_setup` so game roles land below bot's managed role
- [ ] Run `/admin_setup` → `/profile` both accounts → reseed ships → apply golden config below

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
- `inventoryCog` equip autocomplete filters out fully-equipped items (`qty > 0` in cargo)
