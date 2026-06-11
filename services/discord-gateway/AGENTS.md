# AGENTS.md - discord-gateway Service

This file provides comprehensive guidance for AI agents working on the discord-gateway service.
Read this file **before** making any changes within this service.

---

## Service Overview

**discord-gateway** is the Discord bot gateway service. It provides:
1. **A Discord bot** (via Discord.py) that exposes slash commands to users in Discord guilds
2. **A REST API** (via FastAPI) for programmatic Discord access by other services

It acts as the bridge between Discord users ↔ bot-core (game logic) ↔ blender-service (rendering). The service has no database of its own — all game state is stored and managed by `bot-core`.

---

## Technology Stack

| Technology | Role |
|------------|------|
| **Discord.py** | Discord bot library — slash commands via `app_commands`, cog system |
| **FastAPI** | REST API framework for programmatic Discord access |
| **httpx** | Async HTTP client used by cogs to call bot-core and blender-service |
| **uvicorn** | ASGI server |
| **Pydantic v2** | Request/response schema validation (`model_config = ConfigDict(from_attributes=True)`) |
| **bblogger** | Shared logging utility (copied from `services/shared/bblogger.py`) |
| **pytest** | Test runner (`asyncio_mode = auto`) |
| **Ruff** | Linter/formatter (`target-version = "py313"`, `line-length = 120`) |

---

## Directory Structure

```
services/discord-gateway/
├── Dockerfile
├── docker-entrypoint.sh        # Root: chown /app/data, then gosu botuser → python src/bot.py
├── requirements.txt
├── pytest.ini                  # asyncio_mode=auto, addopts = -n 2 --dist loadfile
├── .coveragerc
├── test-cleanup.sh             # Test artifact cleanup helper
├── src/
│   ├── bot.py                  # GatewayBot class + FastAPI lifespan + create_app()
│   ├── api-test.py             # Integration test harness (standalone runner)
│   ├── api/
│   │   ├── routers/            # 12 REST API router modules (auto-discovered)
│   │   │   ├── __init__.py
│   │   │   ├── announcements.py        # Bounty announcement rendering (unified channel/message push)
│   │   │   ├── categories.py           # Category channel CRUD
│   │   │   ├── channels.py             # Text/voice channel CRUD
│   │   │   ├── guilds.py               # Guild info + role management
│   │   │   ├── health.py               # Health check endpoints
│   │   │   ├── internal_autocomplete.py # Push endpoints for in-process autocomplete cache
│   │   │   ├── messages.py             # Message send/edit/delete
│   │   │   ├── permissions.py          # Permission overwrite management
│   │   │   ├── roles.py                # Role CRUD
│   │   │   ├── tags.py                 # Forum channel tag management
│   │   │   ├── threads.py              # Thread management
│   │   │   └── users.py                # User/member lookups
│   │   └── schemas/            # 9 Pydantic v2 schema modules
│   │       ├── __init__.py
│   │       ├── announcement_schemas.py # BountyAnnouncementRequest and related schemas
│   │       ├── base_schemas.py         # BaseResponse, pagination helpers
│   │       ├── channel_schemas.py      # Channel, Category, Thread models
│   │       ├── guild_schemas.py        # Guild model
│   │       ├── internal_schemas.py     # Internal autocomplete push schemas
│   │       ├── message_schemas.py      # Message, EmbedPayload, EmbedField models
│   │       ├── permission_schemas.py   # PermissionOverwrite model
│   │       ├── role_schemas.py         # Role model
│   │       └── user_schemas.py         # User, Member models
│   ├── cogs/                   # 17 Discord bot cog files (15 loaded; templateCog + testCog skipped)
│   │   ├── _shared/            # Shared cog utilities sub-package
│   │   │   ├── autocomplete_cache.py  # AutocompleteCache base class (peek/schedule_refresh/get_with_timeout/max_entries)
│   │   │   ├── confirm_view.py        # Confirmation UI view
│   │   │   ├── embed_pagination.py    # Paginated embed view
│   │   │   ├── http_error_handler.py  # Shared HTTP error handling
│   │   │   └── loadout_embed.py       # Loadout embed builder
│   │   ├── aboutCog.py         # /about, /list_category, /make-route
│   │   ├── adminCog.py         # All /admin_* and /render_* commands
│   │   ├── bountyCog.py        # /check, /bounties, /route, /criminal-loadout
│   │   ├── combatLogCog.py     # /combat-log, /admin_combat_log
│   │   ├── devCog.py           # /load_data, /reload_autocomplete, /force_reload_caches
│   │   ├── duelCog.py          # /duel-challenge, /duel-accept, /duel-reject, /duel-cancel
│   │   ├── healthCog.py        # /ping, /health
│   │   ├── helpCog.py          # /help, /admin_help
│   │   ├── inventoryCog.py     # /inventory, /search, /item, /equip, /unequip, /give
│   │   ├── playerCog.py        # /profile, /register, /leaderboard, /prestige, /promote, /demote, /loadout, /notifications, /unregister
│   │   ├── schedulerCog.py     # /scheduler_list, /scheduler_view, /scheduler_update, /scheduler_delete
│   │   ├── setupCog.py         # Listener: on_guild_join, on_guild_remove
│   │   ├── shipsCog.py         # /ships, /ship, /setactive, /nickname
│   │   ├── shopCog.py          # /shop, /buy, /sell, /shops
│   │   ├── skinsCog.py         # /ship_skin, /render_skin, /make_skin_texture
│   │   ├── templateCog.py      # Reference template (NOT loaded in production)
│   │   └── testCog.py          # Prefix test command (NOT loaded in production)
│   ├── lib/                    # Third-party libraries
│   └── utils/                  # Shared utility modules
│       ├── __init__.py
│       ├── autocomplete_helpers.py # Shared player-scoped autocomplete choice builders
│       ├── autocomplete_state.py   # Shared player/inventory/ships caches + init/getters/invalidators
│       ├── autocomplete_utils.py   # normalize_for_search(), fuzzy_filter(), resolve_system_name()
│       ├── autocomplete_warm.py    # Startup warm + APScheduler recurring refresh jobs
│       ├── command_utils.py    # CommandValidator, CommandHandler, get_command_handler()
│       ├── discord_converters.py  # Bidirectional Discord object ↔ JSON converters
│       ├── discord_helpers.py  # resolve_bot(), get_entity_or_404(), normalize_emoji()
│       ├── embed_converter.py  # EmbedConverter (JSON ↔ discord.Embed)
│       ├── guild_setup.py      # ensure_bountybot_infrastructure(): roles/category/channels for /admin_setup
│       ├── permission_utils.py # PERMISSION_FLAGS, calculate_effective_permissions()
│       └── timestamp_utils.py  # iso_to_discord_ts()
└── tests/
    ├── conftest.py             # Global fixtures: mocked bot, TestClient, Discord mocks
    ├── test_bot.py
    ├── test_bot_extended.py
    ├── test_health.py
    ├── api/                    # Tests for REST API routers (23 test files)
    ├── cogs/                   # Tests for Discord cogs (34 test files)
    │   └── _shared/            # Tests for cogs/_shared helpers (5 test files)
    ├── schemas/                # Tests for Pydantic schemas (8 test files)
    ├── utils/                  # Tests for utility modules (13 test files)
    └── mocks/                  # Shared mock objects
```

---

## Architecture: Dual-Process Model

The service runs a Discord bot AND a FastAPI REST server **in the same process** via asyncio task management.

### Startup Flow (`bot.py`)

```
uvicorn starts → create_app() → FastAPI lifespan begins
    │
    ├── autocomplete_state.init() with a bot-owned httpx client
    │       + bot-core health probe (3 attempts, non-fatal)
    ├── In-process APScheduler started (MemoryJobStore) → app.state.scheduler
    │
    ├── GatewayBot() created (commands.Bot subclass)
    │       intents: message_content=True, guilds=True, members=True
    │       command_prefix from COMMAND_PREFIX env (default "?p")
    │
    ├── asyncio.create_task(bot.start(token))
    │       → setup_hook() loads all cogs from src/cogs/
    │           (skips files containing "template", "disabled", or "test")
    │       → on_ready() syncs slash commands to guilds (unless AUTO_SYNC_COMMANDS=false)
    │           and registers autocomplete warm jobs on app.state.scheduler
    │
    └── FastAPI yields (routes now active)
            → Auto-discovers routers from api.routers.*
            → Includes each router at prefix /api/v1
```

### Key Classes

| Class | File | Purpose |
|-------|------|---------|
| `GatewayBot` | `bot.py` | Discord.py bot; stored at `app.state.bot` |
| `CommandValidator` | `utils/command_utils.py` | Permission/cooldown checks for prefix commands |
| `CommandHandler` | `utils/command_utils.py` | Wraps prefix command execution |

### Accessing the Bot from REST Routers

Routers use `app.state.bot` to access the live Discord bot:

```python
from utils.discord_helpers import resolve_bot

bot = await resolve_bot(request)  # raises HTTP 503 if bot not ready
guild = await get_entity_or_404(bot.get_guild, bot.fetch_guild, guild_id, "guild")
```

---

## Discord Cogs

The bot auto-discovers cogs via `setup_hook()` — any `*.py` file in `src/cogs/` is loaded **unless** the filename contains `template`, `disabled`, or `test`. Each cog file must export an async `setup(bot)` function.

### Cog Reference Table

| File | Class | Slash Commands | Notes |
|------|-------|----------------|-------|
| `aboutCog.py` | `AboutCog` | `/about`, `/list_category`, `/make-route` | Preloads all game object data on startup; autocomplete from cache |
| `adminCog.py` | `AdminCog` | `/admin_check`, `/admin_setup`, `/admin_player`, `/admin_refresh_shop`, `/admin_guild_stats`, `/admin_config`, `/admin_uninstall`, `/admin_config_shop`, `/admin_config_validate`, `/admin_clear_bounties`, `/admin_config_bounty`, `/admin_config_xp`, `/admin_spawn_bounty`, `/admin_cooldown_reset`, `/admin_give_item`, `/admin_remove_item`, `/admin_give_ship`, `/admin_remove_ship`, `/admin_config_constants`, `/admin_config_constants_view`, `/admin_config_constants_reset`, `/admin_duel`, `/render_config`, `/render_cache_clear` | Uses `@is_admin()` decorator; calls bot-core AND blender-service |
| `bountyCog.py` | `BountyCog` | `/check`, `/bounties`, `/route`, `/criminal-loadout` | Star systems + active bounties served from `AutocompleteCache`s (push + periodic refresh) |
| `combatLogCog.py` | `CombatLogCog` | `/combat-log`, `/admin_combat_log` | `/combat-log` has optional `public: bool = False` (embed posted publicly when true; errors always ephemeral); `/admin_combat_log` is admin-gated and always ephemeral |
| `devCog.py` | `DevCog` | `/load_data`, `/reload_autocomplete`, `/force_reload_caches` | Super-admin only (`DEVELOPERS` env var via `_check_is_super_admin`); also prefix commands `snooze`/`wake`/`botstatus` |
| `duelCog.py` | `DuelCog` | `/duel-challenge`, `/duel-accept`, `/duel-reject`, `/duel-cancel` | Pending/outgoing duel autocomplete from per-cog caches |
| `healthCog.py` | `HealthCog` | `/ping`, `/health` | Admin-only (`/ping` via `@is_admin()`, `/health` via runtime `_check_is_admin`); `/health` calls bot-core health endpoint |
| `helpCog.py` | `HelpCog` | `/help`, `/admin_help` | Help command listing for users and admins |
| `inventoryCog.py` | `InventoryCog` | `/inventory`, `/search`, `/item`, `/equip`, `/unequip`, `/give` | Equip/unequip modifies active ship loadout; `/inventory` passes `include_ships=true` so inactive ships show as inventory entries |
| `playerCog.py` | `PlayerCog` | `/profile`, `/register`, `/leaderboard`, `/prestige`, `/promote`, `/demote`, `/loadout`, `/notifications`, `/unregister` | `/register` is a full alias of `/profile`; `/prestige` requires Platinum tier + `ConfirmView` confirmation |
| `schedulerCog.py` | `SchedulerCog` | `/scheduler_list`, `/scheduler_view`, `/scheduler_update`, `/scheduler_delete` | Admin-only scheduler job management |
| `setupCog.py` | `SetupCog` | *(no slash commands)* | Listener: `on_guild_join` → welcome embed pointing admins at `/admin_setup`; `on_guild_remove` → cleanup. Channel/role creation lives in `utils/guild_setup.py`, invoked by `/admin_setup` |
| `shipsCog.py` | `ShipsCog` | `/ships`, `/ship`, `/setactive`, `/nickname` | Ship management; respects ownership |
| `shopCog.py` | `ShopCog` | `/shop`, `/buy`, `/sell`, `/shops` | Tier-gated shop access |
| `skinsCog.py` | `SkinsCog` | `/ship_skin`, `/render_skin`, `/make_skin_texture` | Calls blender-service; UI views: `SquareCheckView`, `RegionModeView`, `RegionOptionView`, `FormatDownloadView` |
| `templateCog.py` | `TemplateCog` | `/example` | **NOT loaded** (filename contains "template"); copy as scaffold |
| `testCog.py` | `TestCog` | `test_command` (prefix) | **NOT loaded** (filename contains "test"); prefix command only |

### Admin Permission System

`adminCog.py` defines the `is_admin()` check used by multiple cogs. It evaluates in order:
1. `DEVELOPERS` environment variable (comma-separated Discord user IDs)
2. Built-in Discord Administrator permission
3. Configured Bot Admin role fetched from `GET /api/v1/config/guild/{guild_id}`

```python
from cogs.adminCog import is_admin, _check_is_admin

@app_commands.command(name="my_cmd")
@is_admin()
async def my_cmd(self, interaction: discord.Interaction):
    ...
```

---

## REST API Routers

All 12 routers are auto-discovered from `api/routers/*.py` (any module with a `router` attribute) and mounted at `/api/v1`. Routers use `resolve_bot()` + `get_entity_or_404()` to access live Discord state.

### Router Reference Table

| File | Prefix | Purpose |
|------|--------|---------|
| `announcements.py` | `/announcements` | Bounty announcement rendering — unified channel/message push from bot-core |
| `categories.py` | `/categories` | Category channel CRUD |
| `channels.py` | `/channels` | Text/voice channel CRUD, message send/edit/delete within channel |
| `guilds.py` | `/guilds` | Guild info, role list, role create/update/delete |
| `health.py` | `/health` | Health check: `GET /health`, `GET /health/simple`, `GET /health/liveness` |
| `internal_autocomplete.py` | `/internal/autocomplete` | Push/invalidate endpoints for in-process autocomplete caches (shop, bounty, duel, combat-log) + cache health |
| `messages.py` | `/messages` | Global message send, edit, delete, fetch by ID |
| `permissions.py` | `/permissions` | Permission overwrite get/set/delete for channels |
| `roles.py` | `/roles` | Role CRUD at guild level |
| `tags.py` | `/tags` | Forum channel tag CRUD |
| `threads.py` | `/threads` | Thread create/archive/unarchive/list |
| `users.py` | `/users` | User/member lookup by ID, guild membership check |

---

## Schemas

All schemas use **Pydantic v2** conventions: `model_config = ConfigDict(from_attributes=True)`, `.model_dump()` (not `.dict()`).

| File | Key Models | Purpose |
|------|-----------|---------|
| `announcement_schemas.py` | `BountyAnnouncementRequest` | Schemas for bounty announcement push requests |
| `base_schemas.py` | `BaseResponse` | Common `status`, `timestamp` fields; base for all responses |
| `channel_schemas.py` | `Channel`, `Category`, `Thread` | Channel/thread representations |
| `guild_schemas.py` | `Guild` | Guild metadata |
| `internal_schemas.py` | `ShopCachePush`, `BountyCachePush`, `DuelCachePush` | Schemas for internal autocomplete push endpoints |
| `message_schemas.py` | `Message`, `MessageSummary`, `EmbedPayload`, `EmbedField` | Message and embed structures |
| `permission_schemas.py` | `PermissionOverwrite` | Permission overwrite target/allow/deny |
| `role_schemas.py` | `Role` | Role with permissions bitfield |
| `user_schemas.py` | `User`, `Member` | User account + guild membership |

---

## Utility Modules

See `src/utils/AGENTS.md` for detailed documentation.

| File | Key Exports | Purpose |
|------|------------|---------|
| `command_utils.py` | `CommandValidator`, `CommandHandler`, `get_command_handler()` | Centralized permission and cooldown validation for prefix commands |
| `discord_converters.py` | `GuildConverter`, `ChannelConverter`, `RoleConverter`, `UserConverter`, `MessageConverter`, `PermissionConverter` | Bidirectional conversion between Discord objects and Pydantic schemas |
| `discord_helpers.py` | `resolve_bot()`, `get_entity_or_404()`, `handle_discord_exception()`, `normalize_emoji()`, `tag_to_dict()`, `tags_to_edit_payload()` | REST router utilities; Discord exception → HTTP exception mapping |
| `embed_converter.py` | `EmbedConverter` | Round-trip JSON ↔ `discord.Embed` conversion with grid layout support |
| `permission_utils.py` | `PERMISSION_FLAGS`, `calculate_effective_permissions()`, `check_permission()`, `evaluate_user_guild_permissions()`, `has_channel_permission()` | Full Discord permission flag registry and evaluation |

---

## Inter-Service Communication

### Cogs → bot-core

Cogs use `httpx.AsyncClient` to call the bot-core REST API. The pattern is consistent across all cogs:

```python
import httpx
import os

api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")

class MyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))

    async def cog_unload(self):
        await self.http_client.aclose()  # Always close the client!
```

**Standard error handling pattern:**
```python
try:
    resp = await self.http_client.get(f"{api_base}/some/endpoint", timeout=10)
    resp.raise_for_status()
    data = resp.json()
except httpx.HTTPStatusError as e:
    if e.response.status_code == 404:
        await interaction.followup.send("❌ Not found.", ephemeral=True)
    else:
        await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
except Exception as e:
    flogger.error(f"Error: {e}")
    await interaction.followup.send("⚠️ An error occurred.", ephemeral=True)
```

**Important:** All error responses to the user use `ephemeral=True` so they are only visible to the invoking user.

### Cogs → blender-service

Only `skinsCog.py` and `adminCog.py` call blender-service directly:

```python
BLENDER_API_BASE_URL = os.getenv("BLENDER_API_BASE_URL", "http://blender-service:8001/api/v1")
self.blender_client = httpx.AsyncClient(
    base_url=BLENDER_API_BASE_URL,
    timeout=httpx.Timeout(60.0, connect=10.0),  # longer timeout for renders
)
```

### Data Flow

```
Discord user → discord.py → Cog command handler
                                   │
                                   ├─ GET/POST → bot-core:8000/api/v1/...
                                   │               (game state, player data, shops, etc.)
                                   │
                                   └─ POST → blender-service:8001/api/v1/...
                                               (skin rendering, texture compositing)
```

---

## Testing

### Statistics

| Scope | Count |
|-------|-------|
| Test files | 86 |

### Test Structure

```
tests/
├── conftest.py          # Root conftest: mocks shared.bblogger BEFORE any imports,
│                        #   creates mocked GatewayBot, FastAPI TestClient
├── api/                 # Router tests (TestClient-based)
├── cogs/                # Cog command tests
│   ├── conftest.py      # Cog-specific fixtures: mocked interactions, HTTP responses
│   ├── _shared/         # Tests for cogs/_shared helpers (5 test files)
│   └── test_*.py        # 34 cog test files
├── schemas/             # Pydantic schema validation tests
├── utils/               # Utility function unit tests
└── mocks/               # Shared mock objects
```

### Conftest Architecture

The root `conftest.py` performs a critical operation: it injects `sys.modules["shared"]` and `sys.modules["shared.bblogger"]` with mock objects **before** any application code is imported. This prevents runtime errors when running tests without the shared package installed.

```python
# conftest.py pattern (tests isolation)
_mock_shared = types.ModuleType("shared")
_mock_bblogger = types.ModuleType("shared.bblogger")
_mock_bblogger.get_logger = lambda *args, **kwargs: MagicMock()
sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger
```

### Running Tests

**IMPORTANT — always log to file.** The full suite takes ~6 minutes. Without capturing output, any failure detail is lost and requires a full re-run to recover.

```bash
# From /proj — ALWAYS use this form so failures are captured:
cd /proj/services/discord-gateway && timeout 600 python -m pytest tests/ -q --tb=short 2>&1 | tee /tmp/test-gateway.log | tail -20

# Cog tests only (faster, ~3 min):
cd /proj/services/discord-gateway && timeout 300 python -m pytest tests/cogs/ -q --tb=short 2>&1 | tee /tmp/test-gateway-cogs.log | tail -20

# Single file (for targeted iteration):
cd /proj/services/discord-gateway && timeout 120 python -m pytest tests/cogs/test_shopCog.py -q --tb=short 2>&1 | tee /tmp/test-single.log | tail -20

# Full output on failure (grep the log, don't re-run):
grep -A 20 "FAILED\|ERROR" /tmp/test-gateway.log

# Coverage:
cd /proj/services/discord-gateway && python -m pytest tests/ --cov=src --cov-report=html -q 2>&1 | tee /tmp/test-gateway-cov.log | tail -20

# Disable parallelism (for debugging hangs):
cd /proj/services/discord-gateway && python -m pytest tests/ -q --tb=short -p no:xdist 2>&1 | tee /tmp/test-gateway-serial.log | tail -20
```

### Test Performance (DEF-S11-002)

The test suite is configured for **parallel execution with module-scoped fixtures** to stay within the ≤8-minute target (actual: ~6m 21s):

- **`pytest.ini`** sets `addopts = -n 2 --dist loadfile` — 2 workers, all tests from one file on the same worker
- **`--dist loadfile` is mandatory** — ensures module-scoped fixtures are created once per file per worker, not once per worker per test
- **Module-scoped fixtures** — `mock_bot` and `mock_<cog>_cog` fixtures are widened to `scope="module"` in all cog test files (except the 3 UNSAFE files below)
- **UNSAFE files** (`test_aboutCog.py`, `test_skinsCog.py`, `test_bountyCog.py`) — only `mock_bot` is module-scoped; cog fixtures remain function-scoped due to direct per-test mutations of cog internal state
- **`_block_background_tasks`** in `tests/cogs/conftest.py` must remain **function-scoped** and `autouse=True` — do not change its scope
- When adding a **new cog test file**, widen `mock_bot` and `mock_<cog>_cog` to `scope="module"` as the default convention

---

## Common Tasks

### Adding a New Discord Cog

1. **Create** `src/cogs/myCog.py` — copy from `templateCog.py` as scaffold
2. **Name the class** `MyCog(commands.Cog)` and the file `myCog.py`
3. **Import pattern**: `from shared import bblogger`, set `api_base` from env
4. **Create httpx client** in `__init__`, close it in `cog_unload`
5. **Decorate commands** with `@app_commands.command()` and optional `@is_admin()`
6. **Export** `async def setup(bot): await bot.add_cog(MyCog(bot))`
7. **File is auto-loaded** — no registration needed as long as name doesn't contain "template", "disabled", or "test"
8. **Create tests** in `tests/cogs/test_myCog.py`

See `src/cogs/AGENTS.md` for the full cog development guide.

### Adding a New REST Router

1. **Create** `src/api/routers/my_resource.py`
2. **Define** `router = APIRouter(prefix="/my-resource", tags=["my-resource"])`
3. **Use** `resolve_bot(request)` to get the bot, `get_entity_or_404()` for Discord entity lookup
4. **Add schemas** in `src/api/schemas/` if needed
5. **Router is auto-discovered** — no registration needed
6. **Create tests** in `tests/api/test_my_resource.py`

See `src/api/routers/AGENTS.md` for the full router development guide.

### Updating an Existing Cog

1. Read the cog's test file to understand expected behavior
2. Modify the cog — keep error handling consistent with the existing pattern
3. Run the cog's test file: `pytest tests/cogs/test_myCog.py -v`
4. Run full suite: `pytest` to check for regressions

---

## Code Standards

### Python Version
- Python 3.13+
- Type hints everywhere (including `str | None`, `int | None` union syntax)

### Linting/Formatting
- **Ruff** — configured in `/proj/pyproject.toml`
  - `target-version = "py313"`
  - `line-length = 120`
- Run: `ruff check src/` and `ruff format src/`

### Pydantic
- Use `model_config = ConfigDict(from_attributes=True)` (NOT `class Config`)
- Use `.model_dump()` (NOT `.dict()`)
- Use `.model_copy(update={...})` (NOT `.copy(update={...})`)

### Testing
- **Max 2 mocks per test** — prefer real objects with deterministic inputs
- Use `AsyncMock` for coroutines, `MagicMock` for synchronous calls
- Every cog command must have tests covering: success path, 404 error, API error, permission denied

### Logging
- Use `bblogger.get_logger("discord-gateway-<ClassName>")` as the logger name pattern
- Log `INFO` for successful user actions, `ERROR` for failures, `DEBUG` for diagnostics
- **Always include entity IDs**: `f"guild={interaction.guild_id} user={interaction.user.id}"`

### Error Responses
- All error responses use `ephemeral=True` (only visible to invoking user)
- Always use `await interaction.response.defer(thinking=True)` before async API calls
- Check `if not interaction.response.is_done()` before sending in error handlers

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `BOTTOKEN` | *(required)* | Discord bot token |
| `BOTAPPID` | `0` | Discord application ID |
| `COMMAND_PREFIX` | `?p` | Prefix for legacy prefix commands |
| `AUTO_SYNC_COMMANDS` | `true` | Sync slash commands on startup; `false` = skip (use `wake` to force-sync) |
| `BOT_API_BASE_URL` | `http://bot-core:8000/api/v1` | bot-core API base URL (used by cogs) |
| `BLENDER_API_BASE_URL` | `http://blender-service:8001/api/v1` | blender-service API base URL |
| `GATEWAY_HOST` | `0.0.0.0` | FastAPI bind host |
| `GATEWAY_PORT` / `PORT` | `8000` | FastAPI bind port (compose sets `7999`) |
| `ACCESS_LOG` | `true` | Enable uvicorn access logging |
| `DEVELOPERS` | `` | Comma-separated Discord user IDs with developer override |
| `INTERNAL_AUTH_TOKEN` | `` | Shared secret for bot-core → gateway push endpoints; unset = dev mode (warns, allows) |
| `AUTOCOMPLETE_WARM_ACTIVE_DAYS` | `7` | Players active within N days are warmed on startup; 0 = warm everyone |
| `AUTOCOMPLETE_WARM_CONCURRENCY` | `16` | Max concurrent inventory/ships fetches during warm + refresh |
| `AUTOCOMPLETE_WARM_GUILD_STAGGER_MS` | `200` | Spacing between per-guild warm jobs at startup (ms) |
| `AUTOCOMPLETE_PLAYER_REFRESH_MINUTES` | `10` | Interval for player_cache bulk re-warm |
| `AUTOCOMPLETE_LOADOUT_REFRESH_MINUTES` | `5` | Interval for inventory/ships round-robin re-warm |
| `AUTOCOMPLETE_PLAYER_TTL_SECONDS` | `900` | player_cache TTL |
| `AUTOCOMPLETE_LOADOUT_TTL_SECONDS` | `600` | inventory_cache/ships_cache TTL |
| `AUTOCOMPLETE_INVENTORY_MAX_ENTRIES` | *(unset)* | LRU cap on inventory_cache; unset = no cap |
| `AUTOCOMPLETE_SHIPS_MAX_ENTRIES` | *(unset)* | LRU cap on ships_cache; unset = no cap |
| `AUTOCOMPLETE_COMBATLOG_TTL_SECONDS` | `120` | combatLogCog `_combatlog_cache` TTL |
| `AUTOCOMPLETE_COMBATLOG_MAX_ENTRIES` | `2000` | LRU cap on `_combatlog_cache` |
| `AUTOCOMPLETE_COMBATLOG_REFRESH_MINUTES` | `5` | Interval for combat-log round-robin re-warm |
| `AUTOCOMPLETE_ADMIN_DUEL_TTL_SECONDS` | `300` | adminCog `_admin_pending_duel_cache` TTL |

---

## Autocomplete Cache Architecture

The gateway runs a proactively-warmed in-process autocomplete cache that eliminates all HTTP calls on the Discord autocomplete hot path (≤100ms p99, target ~50ms).

### Cache model

Shared caches (in `utils/autocomplete_state.py`) plus per-cog `AutocompleteCache` instances. TTLs are dead-man switches — recurring APScheduler refresh jobs and bot-core push endpoints normally reset entries well before expiry.

| Cache | Module | Key | TTL | Refresh |
|-------|--------|-----|-----|---------|
| `player_cache` | `utils/autocomplete_state.py` | `(guild_id, user_id)` | 15 min | Every 10 min (APScheduler) |
| `inventory_cache` | `utils/autocomplete_state.py` | `(guild_id, player_id)` | 10 min | Every 5 min round-robin |
| `ships_cache` | `utils/autocomplete_state.py` | `(guild_id, player_id)` | 10 min | Every 5 min round-robin |
| `_shop_cache` | `shopCog` (per-cog) | `(guild_id, tier)` | 60 min | Every 6 min + bot-core push on refresh |
| `_systems_cache` | `bountyCog` (per-cog) | `"all"` | none | Self-heal via `refresh_fn` |
| `_bounty_cache` | `bountyCog` (per-cog) | `guild_id` | 20 min | Every 10 min + bot-core push on spawn/expire |
| `_pending_duel_cache` | `duelCog` (per-cog) | `(guild_id, player_id)` | 30 min | Every 5 min + bot-core push |
| `_outgoing_duel_cache` | `duelCog` (per-cog) | `(guild_id, player_id)` | 30 min | Every 5 min + bot-core push |
| `_job_cache` | `schedulerCog` (per-cog) | `"all"` | 10 min | Every 2 min (APScheduler) |
| `_combatlog_cache` | `combatLogCog` (per-cog) | `(guild_id, user_id)` | 120s | Every 5 min round-robin + bot-core push invalidation |
| `_item_catalog` / `_ship_catalog` | `adminCog` (per-cog) | `"all"` | none | Self-heal via `refresh_fn` |
| `_admin_pending_duel_cache` | `adminCog` (per-cog) | `guild_id` | 300s | Every 5 min + invalidate-and-cold-fill |

### Hot-path pattern (copy into new autocomplete handlers)

```python
items = self._some_cache.peek(key)
if items is None:
    self._some_cache.schedule_refresh(key)
    return []
norm_q = normalize_for_search(current)
return [app_commands.Choice(name=it["label"][:100], value=it["value"])
        for it in items if norm_q in (it.get("_norm") or normalize_for_search(it["label"]))][:25]
```

### Invalidation template (copy into command success paths)

```python
try:
    autocomplete_state.invalidate_inventory(interaction.guild_id, player_id)
    autocomplete_state.invalidate_ships(interaction.guild_id, player_id)
except Exception:
    flogger.warning(f"/command: cache invalidation failed for player_id={player_id}; transaction still succeeded")
```

**Rules:**
- Place invalidation AFTER `raise_for_status()` succeeds — never before, never on error path
- Use `set_*` (write-through) when the fresh value is already in hand
- Use `invalidate_*` when the command mutates state but doesn't have the fresh value

### Key files

| File | Purpose |
|------|---------|
| `src/utils/autocomplete_state.py` | Shared caches, init(), getters, invalidators |
| `src/utils/autocomplete_warm.py` | Startup warm + APScheduler recurring jobs |
| `src/utils/autocomplete_helpers.py` | Shared per-user helpers (peek-first; signatures preserved) |
| `src/cogs/_shared/autocomplete_cache.py` | `AutocompleteCache` base class with peek/schedule_refresh/get_with_timeout/max_entries |
| `src/api/routers/internal_autocomplete.py` | Internal push endpoints (shop, bounty, duel, combat-log) + cache health |

---

## Docker

- **Port**: `GATEWAY_PORT` (default `7999`), mapped host:container at the same number by docker-compose
- **Volume**: `./mappings/discord-gateway` → `/app/data`
- **Image**: Python 3.13 slim, copies `services/shared/` into `/app/src/shared/`
- **Entry point**: `docker-entrypoint.sh` — runs as root to chown `/app/data`, then drops to `botuser` via gosu and runs `python /app/src/bot.py`

---

## Health Check

- **Endpoint**: `GET /api/v1/health` — returns service status, environment info, system checks
- **Simple check**: `GET /api/v1/health/simple` — minimal status for load balancers
- **Liveness probe**: `GET /api/v1/healthliveness` — returns `{"status": "alive"}` (route path is `"liveness"` without a leading slash, so it concatenates with the `/health` prefix)

---

*Last updated: 2026-06-11*
