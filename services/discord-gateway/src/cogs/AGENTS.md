# AGENTS.md - discord-gateway/src/cogs

This file provides detailed guidance for AI agents working on Discord cogs in this directory.

---

## A.35/A.37 Pattern: /inventory Choice + /equip Autocomplete (2026-04-22)

### /inventory item_type Choice (A.35)

`inventoryCog.inventory` now uses `@app_commands.choices(item_type=[...])` with
4 generic value choices: `ship`, `weapon`, `module`, `turret`. The server normalizes
these to concrete types. Do NOT pass concrete types in the choices — keep generic.

### /equip autocomplete filter (A.37)

The inline `equip_autocomplete` in `inventoryCog.py` now filters items by
`_CURRENTLY_EQUIPPABLE_INVENTORY_TYPES` (from `utils/autocomplete_helpers.py`) using
concrete item types: `{"primary_weapon", "turret_weapon", "module"}`. The function
also excludes already-equipped items by fetching the active ship.

`unequip_autocomplete` now includes `secondary_weapons` slot in its loadout scan
(for future compatibility).

### Surface gating principle

Cogs must gate secondary weapons using `_CURRENTLY_EQUIPPABLE_INVENTORY_TYPES`
from `utils/autocomplete_helpers.py`. This constant mirrors
`GameConstants.CURRENTLY_ENABLED_TYPES` (minus "ship") in bot-core.
Both must be updated together when secondary weapons are enabled.

---

## Overview

This directory contains all **Discord bot cogs** — modular collections of slash commands and event listeners. Cogs are the primary way users interact with the BountyBot game through Discord.

Each cog:
- Inherits from `commands.Cog`
- Registers slash commands via `@app_commands.command()` decorators
- Calls bot-core (and sometimes blender-service) via `httpx.AsyncClient`
- Exports an `async def setup(bot)` function at module level

---

## Auto-Discovery and Loading

`GatewayBot.setup_hook()` in `bot.py` auto-loads cogs:

```python
for fn in os.listdir("src/cogs"):
    if fn.endswith(".py") and not any(x in fn for x in ("template", "disabled", "test")):
        await self.load_extension(f"cogs.{fn[:-3]}")
```

**Rules:**
- Any `*.py` file in `src/cogs/` is loaded **unless** the filename contains `template`, `disabled`, or `test`
- `templateCog.py` and `testCog.py` are **never loaded** in production or test runs
- No manual registration in `bot.py` is needed — just create the file and the `setup()` function
- **Subdirectories are NOT walked** — the loader only inspects entries ending in `.py` from `os.listdir("src/cogs")`. The `_shared/` subdirectory is therefore never loaded as a cog (see below).

---

## `_shared/` — Cog-Adjacent Helpers

The `_shared/` subdirectory holds helper modules that are **game-layer** (consumed by cogs, not by `utils/`) but are **not themselves cogs**. Examples: `loadout_embed.py` (Discord embed builder for loadouts) and `embed_pagination.py` (continuation-field helper for `/list_category`).

Why here and not under `utils/`?
- `utils/` is reserved for game-agnostic helpers — if the game layer is replaced, `utils/` stays.
- Everything in `cogs/` (including `_shared/`) is part of the game layer and is removed together.
- The leading underscore is a Python convention signalling "private-but-colocated" and, together with the auto-loader's `.py` extension filter, prevents accidental cog loading.

## Alias Commands: `/register` ↔ `/profile`

The `/register` slash command in `playerCog.py` is a **full behavioural alias** for `/profile` — identical embed output, identical side effects (player upsert, role assignment). Both commands delegate to a single private handler `_display_profile(interaction)`. The only difference between the two public commands is the slash-command name users see in Discord, and each wrapper logs its own name for usage-frequency analysis. When adding other aliases in the future, follow the same pattern: keep the work in a shared `_display_*` method and let each `@app_commands.command(...)` wrapper do only `flogger.info(...)` + delegate.

---

## Cog File Template

Use `templateCog.py` as the reference implementation. The canonical pattern:

```python
import os

import discord
import httpx
from cogs.adminCog import is_admin      # import if you need admin protection
from discord import app_commands
from discord.ext import commands
from shared import bblogger

flogger = bblogger.get_logger("discord-gateway-MyCog")
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
flogger.debug(f"myCog loading with api_base: {api_base}")


class MyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        flogger.debug("MyCog initialized")

    async def cog_unload(self):
        """Called when the cog is unloaded. ALWAYS close the HTTP client."""
        await self.http_client.aclose()

    @app_commands.command(name="my_command", description="Description shown in Discord")
    @app_commands.describe(param="Description of param")
    async def my_command(self, interaction: discord.Interaction, param: str):
        """Docstring for the command."""
        await interaction.response.defer(thinking=True)
        flogger.debug(f"/my_command invoked: guild={interaction.guild_id} user={interaction.user.id}")

        try:
            resp = await self.http_client.get(f"{api_base}/some/endpoint", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            await interaction.followup.send(f"Result: {data}")
            flogger.info(f"/my_command success: guild={interaction.guild_id} user={interaction.user.id}")

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                await interaction.followup.send("❌ Not found.", ephemeral=True)
            else:
                flogger.error(f"/my_command API error: status={e.response.status_code}")
                await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"/my_command error: {e}")
            await interaction.followup.send("⚠️ An error occurred.", ephemeral=True)

    @my_command.error
    async def my_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        flogger.exception("Error in /my_command", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)


async def setup(bot: commands.Bot):
    flogger.debug("Setting up MyCog...")
    await bot.add_cog(MyCog(bot))
    flogger.info("MyCog loaded")
```

---

## HTTP Client Pattern

### Setup

Every cog that calls bot-core or blender-service must:
1. Create `self.http_client` in `__init__` with a 10-second timeout
2. Close it in `cog_unload`

```python
self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
```

For blender-service (longer operations):
```python
self.blender_client = httpx.AsyncClient(
    base_url=BLENDER_API_BASE_URL,
    timeout=httpx.Timeout(60.0, connect=10.0),
)
```

### Environment Variables

```python
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
BLENDER_API_BASE_URL = os.getenv("BLENDER_API_BASE_URL", "http://blender-service:8001/api/v1")
```

### Standard Error Handling

Always handle these cases explicitly:
- `httpx.HTTPStatusError` with `status_code == 404` → "not found" message
- `httpx.HTTPStatusError` with `status_code == 400` → extract `detail` from JSON body
- `httpx.HTTPStatusError` other → generic API error
- Generic `Exception` → generic warning, log full error

---

## Slash Command Patterns

### Defer for async operations
```python
await interaction.response.defer(thinking=True)       # public "thinking..." indicator
await interaction.response.defer(thinking=True, ephemeral=True)  # private "thinking..."
```
Always defer before any `await` calls to bot-core.

### Followup responses
```python
await interaction.followup.send(embed=embed)             # public embed response
await interaction.followup.send("message", ephemeral=True)  # private error message
```

### Response without defer
```python
await interaction.response.send_message("message", ephemeral=True)
```
Only for immediate responses (no async operations needed first).

### Error handlers
```python
@my_command.error
async def my_command_error(self, interaction, error):
    flogger.exception("Error in /my_command", exc_info=error)
    if not interaction.response.is_done():
        await interaction.response.send_message("⚠️ An error occurred.", ephemeral=True)
```
The guard `if not interaction.response.is_done()` prevents double-response errors.

---

## Autocomplete Pattern

Cogs that need autocomplete preload data on startup using `bot.loop.create_task`:

```python
class MyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._items: list[str] = []
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        bot.loop.create_task(self._preload_data())   # schedule preload after bot is ready

    async def _preload_data(self):
        await self.bot.wait_until_ready()
        try:
            resp = await self.http_client.get(f"{api_base}/items", timeout=10)
            resp.raise_for_status()
            self._items = [item["name"] for item in resp.json()]
        except Exception:  # broad catch is intentional for preload
            self._items = []  # degrade gracefully

    async def item_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=name, value=name)
            for name in self._items
            if current.lower() in name.lower()
        ][:25]  # Discord limit: 25 choices

    @app_commands.command(name="use_item")
    @app_commands.autocomplete(item=item_autocomplete)
    async def use_item(self, interaction: discord.Interaction, item: str):
        ...
```

**Preload error handling:** Always catch specific exceptions (`TimeoutException`, `HTTPStatusError`, `RequestError`) before the broad `Exception` fallback, to provide better logging.

---

## Admin Permission Pattern

Use the `is_admin()` decorator from `adminCog.py` to gate commands:

```python
from cogs.adminCog import is_admin, _check_is_admin

# Method 1: Decorator (standard for fully admin-gated commands)
@app_commands.command(name="admin_action")
@is_admin()
async def admin_action(self, interaction: discord.Interaction):
    ...

# Method 2: Runtime check (for conditional admin checks within a command)
if user and user != interaction.user:
    if not await _check_is_admin(interaction):
        await interaction.followup.send("❌ Admin only.", ephemeral=True)
        return
```

**Admin check order:**
1. `DEVELOPERS` env var (comma-separated Discord user IDs)
2. Discord built-in Administrator permission
3. Configured Bot Admin role from `GET /api/v1/config/guild/{guild_id}`

---

## Discord UI Views

For interactive components (buttons, selects), define `discord.ui.View` subclasses. Example from `skinsCog.py`:

```python
class ConfirmView(discord.ui.View):
    def __init__(self, timeout: float = 60):
        super().__init__(timeout=timeout)
        self.result: bool | None = None

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        _ = button  # unused, but required by callback signature
        self.result = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        _ = button
        self.result = False
        self.stop()
        await interaction.response.defer()
```

Send the view and wait for the response:
```python
view = ConfirmView(timeout=60)
await interaction.followup.send("Are you sure?", view=view)
await view.wait()
if view.result is None:  # timed out
    ...
```

---

## Event Listeners

For non-command event handling, use `@commands.Cog.listener()`:

```python
class SetupCog(commands.Cog):
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        """Called when bot is added to a new guild."""
        ...

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        """Called when bot is removed from a guild."""
        ...
```

---

## Player Resolution Pattern

Many cogs need to resolve a Discord user to a bot-core player ID. The standard pattern:

```python
async def _get_player_id(self, user_id: int, guild_id: int) -> int | None:
    """Helper to get player ID from Discord user ID."""
    try:
        user_data = {
            "discord_id": user_id,
            "guild_id": guild_id,
            "discord_username": None,  # None = don't update username; pass real username only from /profile
        }
        resp = await self.http_client.post(f"{api_base}/players/", json=user_data, timeout=5)
        resp.raise_for_status()
        return resp.json()["id"]
    except Exception:  # pylint: disable=broad-exception-caught
        return None
```

**Key semantics:**

- `discord_username: None` — preserves the existing username stored by `/profile`; it does **not** overwrite it. Only `playerCog`'s `/profile` command should pass the real username via `str(interaction.user)`.
- **No implicit auto-create** — the guild must already be configured via `/admin_setup`. If the guild is not configured, `POST /api/v1/players/` returns a `400 "Guild not configured"` error. Cogs should catch this with the `_is_guild_not_configured(error)` helper (see shopCog/bountyCog for the pattern):

```python
def _is_guild_not_configured(exc: httpx.HTTPStatusError) -> bool:
    """Return True if the HTTPStatusError is a 'guild not configured' 400 response."""
    if exc.response.status_code != 400:
        return False
    try:
        detail = exc.response.json().get("detail", "")
        return "not configured" in detail.lower() or "admin_setup" in detail.lower()
    except Exception:  # pylint: disable=broad-exception-caught
        return False
```

Usage in a command's error handler:

```python
except httpx.HTTPStatusError as e:
    if _is_guild_not_configured(e):
        await interaction.followup.send(
            "❌ This server has not been set up yet. Ask an admin to run `/admin_setup` first.",
            ephemeral=True,
        )
        return
    raise
```

---

## Cog-Specific Notes

### aboutCog.py
- Preloads ALL categories and ALL objects per category into memory on startup
- Two-level autocomplete: `category_autocomplete` then `object_autocomplete` (context-aware)
- `make-route` uses bot-core's A* routing via `GET /api/v1/systems/route`
- `EmbedConverter.payload_to_grid_embed()` forces 2-column layout for detailed embeds

### adminCog.py
- All commands require `@is_admin()` — non-admins get a 403-style error
- `admin_uninstall` requires the confirmation string `"CONFIRM-DELETE"` to protect against accidents
- `render_config` and `render_cache_clear` call **blender-service** (not bot-core)
- Error handlers are defined for `admin_setup` and `admin_player` to catch `MissingPermissions`

### bountyCog.py
- `bounty_autocomplete` is a **live** autocomplete that fetches current bounties on each keystroke
- `/check` returns CORRECT/INCORRECT/ALREADY_CHECKED/NOT_FOUND result types
- Cooldown is enforced by bot-core (returns HTTP 429); `/check` handles `resp.status_code == 429` before `raise_for_status()`

### devCog.py
- Uses `is_admin()` from `adminCog.py` (imported at top of file)
- `load_data` with `category="All"` iterates every category and summarizes results
- `reload_autocomplete` calls `_preload_*` methods on other loaded cogs by name

### duelCog.py
- `pending_duel_autocomplete` fetches live pending duels for the invoking user
- `/duel-accept` resolves combat immediately and returns winner/loser + credit transfer details

### healthCog.py
- Both `/ping` and `/health` are admin-only (use `@is_admin()`)
- `/health` calls bot-core's `/api/v1/health` endpoint and formats the response into an embed

### inventoryCog.py
- `/inventory` with `user=<other user>` requires admin permission (runtime check via `_check_is_admin`)
- `/equip` and `/unequip` resolve the player's **active ship** first, then modify its loadout
- `_EQUIPMENT_TYPE_MAP` translates user-facing type names to API type names (`"weapon"` → `"weapons"`)

### playerCog.py
- `/profile` uses `POST /api/v1/players/` upsert to create the player if they don't exist
- `/prestige` requires Platinum tier and the confirmation string `"CONFIRM"` as a parameter

### setupCog.py
- **No slash commands** — only listens to guild events
- `on_guild_join`: initializes guild via API, creates `BountyBot` category with `bounty-board`, `shop`, `general` channels, sends welcome embed
- `on_guild_remove`: calls best-effort cleanup API endpoint (non-fatal if it fails)

### skinsCog.py
- Uses `bot.wait_for("message", ...)` to collect texture uploads interactively (120s timeout)
- Two httpx clients: `self.http_client` (bot-core) and `self.blender_client` (blender-service)
- `SquareCheckView` asks crop/stretch/cancel when uploaded image is non-square
- `FormatDownloadView` offers AEI format conversion after render (ETC1 for Android, DXT5 for PC)
- `_preload_ship_skins` fetches render info for ALL ships on startup

---

## Testing Cogs

### Test File Location
`tests/cogs/test_<cogFileName>.py`

### Test Structure Pattern

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import discord

class TestMyCog:
    @pytest.fixture
    def bot(self):
        bot = MagicMock()
        bot.loop = MagicMock()
        bot.loop.create_task = MagicMock()
        return bot

    @pytest.fixture
    def cog(self, bot):
        from cogs.myCog import MyCog
        return MyCog(bot)

    @pytest.fixture
    def interaction(self):
        interaction = MagicMock(spec=discord.Interaction)
        interaction.guild_id = 123456789
        interaction.user = MagicMock()
        interaction.user.id = 987654321
        interaction.response = AsyncMock()
        interaction.followup = AsyncMock()
        return interaction

    @pytest.mark.asyncio
    async def test_my_command_success(self, cog, interaction):
        # Mock the HTTP response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": "data"}
        cog.http_client.get = AsyncMock(return_value=mock_resp)

        await cog.my_command(interaction, param="test")

        interaction.response.defer.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()
```

### Test Coverage Requirements
- ✅ Happy path (success response)
- ✅ 404 Not Found error
- ✅ Other API errors
- ✅ Network/timeout error
- ✅ Admin permission check (if applicable)
- ✅ Autocomplete function (if applicable)

---

## File Naming Conventions

| Pattern | Auto-loaded? | Purpose |
|---------|-------------|---------|
| `*Cog.py` (e.g., `aboutCog.py`) | ✅ Yes | Production cog |
| `*Cog.py` containing "template" | ❌ No | Template/scaffold only |
| `*Cog.py` containing "test" | ❌ No | Test utilities only |
| `*Cog.py` containing "disabled" | ❌ No | Temporarily disabled |

---

*Last updated: 2026-03-16*
