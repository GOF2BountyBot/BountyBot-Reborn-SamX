import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, call, patch

import httpx
import pytest
import respx

# Capture the genuine asyncio.create_task BEFORE any test patches it, so a
# create_task stub can still schedule a real (harmless) completed task.
_REAL_CREATE_TASK = asyncio.create_task


async def _noop() -> None:
    """A trivially-completing coroutine used to satisfy create_task stubs."""
    return None


# Setup mock shared.bblogger module
_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_args, **_kwargs):
    """Return a MagicMock that already has common log-level methods."""
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    return logger


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

# Ensure real discord is used (not a hand-rolled fake from another test module)
for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import discord
from discord.ext import commands


def create_mock_cog_file(cog_name):
    """Create a mock cog file for testing."""
    cog_content = f"""
import discord
from discord.ext import commands
from shared import bblogger

flogger = bblogger.get_logger('test-cog-{cog_name}')

class {cog_name}Cog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        flogger.debug('Initializing {cog_name}Cog')

    @commands.command()
    async def test_command(self, ctx):
        await ctx.send('Test command from {cog_name}Cog')

async def setup(bot):
    await bot.add_cog({cog_name}Cog(bot))
"""
    return cog_content


@pytest.fixture
def mock_bot():
    """Create a mock GatewayBot instance for testing."""
    bot = MagicMock(spec=commands.Bot)
    bot.user = MagicMock(spec=discord.ClientUser)
    bot.user.id = 123456789
    bot.user.name = "TestBot"
    bot.is_ready = MagicMock(return_value=True)
    bot.add_cog = MagicMock()
    bot.remove_cog = MagicMock()
    bot.tree = MagicMock()
    bot.tree.copy_global_to = MagicMock()
    bot.tree.sync = AsyncMock()
    bot.get_guild = MagicMock()
    bot.get_channel = MagicMock()
    bot.get_member = MagicMock()
    bot.guilds = []
    bot.flogger = MagicMock()
    bot.startup_complete = False
    return bot


def _evict_discord_modules():
    """Remove cached discord/source modules so they re-import with real discord."""
    to_evict = [
        k
        for k in sys.modules
        if k == "discord"
        or k.startswith("discord.")
        or k in ("api", "bot", "utils")
        or k.startswith("api.")
        or k.startswith("utils.")
        or k.startswith("cogs.")
    ]
    for k in to_evict:
        sys.modules.pop(k, None)


@pytest.fixture
def mock_gateway_bot():
    """Create a mock GatewayBot-like instance with proper initialization."""
    _evict_discord_modules()
    from bot import GatewayBot

    # Use MagicMock so we can freely set attributes without hitting property restrictions
    bot = MagicMock(spec=GatewayBot)
    bot.user = MagicMock()
    bot.user.id = 123456789
    bot.user.name = "TestBot"
    bot.is_ready = MagicMock(return_value=True)
    bot.add_cog = MagicMock()
    bot.remove_cog = MagicMock()
    bot.tree = MagicMock()
    bot.tree.copy_global_to = MagicMock()
    bot.tree.sync = AsyncMock()
    bot.get_guild = MagicMock()
    bot.get_channel = MagicMock()
    bot.get_member = MagicMock()
    bot.guilds = []
    bot.flogger = MagicMock()
    bot.startup_complete = False
    bot._warm_jobs_registered = False
    # Attach real async methods from GatewayBot
    bot.on_ready = GatewayBot.on_ready.__get__(bot, GatewayBot)
    bot.sync_commands = GatewayBot.sync_commands.__get__(bot, GatewayBot)
    bot.setup_hook = GatewayBot.setup_hook.__get__(bot, GatewayBot)
    return bot


class TestGatewayBotInitialization:
    """Tests for GatewayBot initialization and setup."""

    def test_bot_initialization(self, mock_gateway_bot):
        """GatewayBot should initialize with correct intents and properties."""
        from bot import GatewayBot

        bot = GatewayBot()
        assert bot.command_prefix == "?p"
        assert bot.intents.message_content is True
        assert bot.intents.guilds is True
        assert bot.intents.members is True
        assert bot.flogger is not None
        assert bot.startup_complete is False

    @patch("os.getenv")
    def test_bot_application_id(self, mock_getenv, mock_gateway_bot):
        """GatewayBot should use BOTAPPID from environment."""
        mock_getenv.return_value = "987654321"

        from bot import GatewayBot

        bot = GatewayBot()
        assert bot.application_id == 987654321

    @patch("os.getenv")
    def test_bot_default_application_id(self, mock_getenv, mock_gateway_bot):
        """GatewayBot should default to 0 if BOTAPPID not set."""
        # Simulate os.getenv returning the default value (env var not set)
        mock_getenv.side_effect = lambda key, default=None: default

        from bot import GatewayBot

        bot = GatewayBot()
        assert bot.application_id == 0


class TestSetupHook:
    """Tests for bot setup_hook method."""

    @patch("os.listdir")
    @patch("os.path.join")
    def test_setup_hook_loads_cogs(self, mock_join, mock_listdir, mock_gateway_bot):
        """setup_hook should load all cog files except template, disabled, and test ones."""
        mock_listdir.return_value = ["aboutCog.py", "adminCog.py", "templateCog.py", "disabledCog.py", "testCog.py"]
        mock_join.return_value = "src/cogs/aboutCog.py"

        bot = mock_gateway_bot
        bot.flogger = MagicMock()

        # Mock load_extension
        bot.load_extension = AsyncMock()

        # Call setup_hook
        asyncio.run(bot.setup_hook())

        # Should load aboutCog and adminCog, skip templateCog, disabledCog, and testCog
        bot.load_extension.assert_any_call("cogs.aboutCog")
        bot.load_extension.assert_any_call("cogs.adminCog")
        assert call("cogs.templateCog") not in bot.load_extension.call_args_list
        assert call("cogs.disabledCog") not in bot.load_extension.call_args_list
        assert call("cogs.testCog") not in bot.load_extension.call_args_list

        bot.flogger.info.assert_called_with("=== SETUP HOOK COMPLETED (2 cogs) ===")

    @patch("os.listdir")
    @patch("os.path.join")
    def test_setup_hook_error_handling(self, mock_join, mock_listdir, mock_gateway_bot):
        """setup_hook should handle cog loading errors gracefully."""
        mock_listdir.return_value = ["errorCog.py"]
        mock_join.return_value = "src/cogs/errorCog.py"

        bot = mock_gateway_bot
        bot.flogger = MagicMock()

        # Mock load_extension to raise exception
        bot.load_extension = AsyncMock(side_effect=Exception("Test error"))

        # Call setup_hook
        with pytest.raises(Exception):
            asyncio.run(bot.setup_hook())

        bot.flogger.error.assert_called_with("✗ Cog load failed errorCog.py: Test error")


class TestOnReadyEvent:
    """Tests for on_ready event handler."""

    def test_on_ready_logs_in(self, mock_gateway_bot):
        """on_ready should log bot login information."""
        bot = mock_gateway_bot
        bot.flogger = MagicMock()
        bot.user = MagicMock()
        bot.user.name = "TestBot"
        bot.user.id = 123456789
        bot.user.__str__ = MagicMock(return_value="TestBot")

        asyncio.run(bot.on_ready())

        bot.flogger.info.assert_any_call("Bot logged in as TestBot (123456789)")

    def test_on_ready_syncs_commands_once(self, mock_gateway_bot):
        """on_ready should sync commands only once when startup_complete is False."""
        bot = mock_gateway_bot
        bot.flogger = MagicMock()
        bot.user = MagicMock()
        bot.user.name = "TestBot"
        bot.user.id = 123456789
        bot.startup_complete = False
        bot.sync_commands = AsyncMock()

        with patch.dict("os.environ", {"AUTO_SYNC_COMMANDS": "true"}):
            asyncio.run(bot.on_ready())

        bot.sync_commands.assert_called_once()
        assert bot.startup_complete is True
        bot.flogger.info.assert_called_with("Commands synced")

    def test_on_ready_skips_sync_when_auto_sync_disabled(self, mock_gateway_bot):
        """on_ready should skip startup sync when AUTO_SYNC_COMMANDS=false (boot dark)."""
        bot = mock_gateway_bot
        bot.flogger = MagicMock()
        bot.user = MagicMock()
        bot.user.name = "TestBot"
        bot.user.id = 123456789
        bot.startup_complete = False
        bot.sync_commands = AsyncMock()

        with patch.dict("os.environ", {"AUTO_SYNC_COMMANDS": "false"}):
            asyncio.run(bot.on_ready())

        bot.sync_commands.assert_not_called()
        assert bot.startup_complete is True
        assert call("Commands synced") not in bot.flogger.info.call_args_list

    def test_on_ready_does_not_resync(self, mock_gateway_bot):
        """on_ready should not resync commands if startup_complete is True."""
        bot = mock_gateway_bot
        bot.flogger = MagicMock()
        bot.user = MagicMock()
        bot.user.name = "TestBot"
        bot.user.id = 123456789
        bot.startup_complete = True
        bot.sync_commands = AsyncMock()

        asyncio.run(bot.on_ready())

        bot.sync_commands.assert_not_called()
        assert call("Commands synced") not in bot.flogger.info.call_args_list


class TestSyncCommands:
    """Tests for sync_commands method."""

    @patch("discord.Object")
    def test_sync_commands_global_only(self, mock_object, mock_gateway_bot):
        """sync_commands should sync globally if bot has no guilds."""
        bot = mock_gateway_bot
        bot.guilds = []
        bot.tree.sync = AsyncMock()

        asyncio.run(bot.sync_commands())

        bot.tree.sync.assert_called_once_with()
        bot.tree.copy_global_to.assert_not_called()

    @patch("discord.Object")
    def test_sync_commands_guild_specific(self, mock_object, mock_gateway_bot):
        """sync_commands should sync per guild if bot has guilds."""
        guild = MagicMock()
        guild.id = 987654321
        bot = mock_gateway_bot
        bot.guilds = [guild]
        bot.tree.sync = AsyncMock()
        bot.tree.copy_global_to = MagicMock()

        asyncio.run(bot.sync_commands())

        bot.tree.copy_global_to.assert_called_once_with(guild=guild)
        bot.tree.sync.assert_called_once_with(guild=mock_object.return_value)
        mock_object.assert_called_once_with(id=987654321)


class TestCogManagement:
    """Tests for cog loading and management."""

    @patch("os.listdir")
    @patch("os.path.join")
    def test_load_all_cogs(self, mock_join, mock_listdir, mock_gateway_bot):
        """Should be able to load all cog files dynamically, skipping template/disabled/test cogs."""
        mock_listdir.return_value = ["healthCog.py", "testCog.py", "templateCog.py"]
        mock_join.return_value = "src/cogs/healthCog.py"

        bot = mock_gateway_bot
        bot.load_extension = AsyncMock()
        bot.flogger = MagicMock()

        asyncio.run(bot.setup_hook())

        # healthCog should be loaded; testCog and templateCog must be skipped
        bot.load_extension.assert_called_once_with("cogs.healthCog")
        assert call("cogs.testCog") not in bot.load_extension.call_args_list
        assert call("cogs.templateCog") not in bot.load_extension.call_args_list


class TestErrorHandling:
    """Tests for error handling in bot methods."""

    def test_setup_hook_with_invalid_cog(self, mock_gateway_bot):
        """setup_hook should handle invalid cog files gracefully."""
        bot = mock_gateway_bot
        bot.flogger = MagicMock()
        bot.load_extension = AsyncMock(side_effect=Exception("Extension not found"))

        # Mock listdir to include invalid file
        with (
            patch("os.listdir", return_value=["invalidCog.py"]),
            patch("os.path.join", return_value="src/cogs/invalidCog.py"),
            pytest.raises(Exception),
        ):
            asyncio.run(bot.setup_hook())

        bot.flogger.error.assert_called()


# ---------------------------------------------------------------------------
# CI-19 tests: BOT_API_BASE_URL env var + health probe behaviour
# ---------------------------------------------------------------------------


def _make_lifespan_app():
    """Return a lightweight object with a ``.state`` namespace for the lifespan."""
    return types.SimpleNamespace(state=types.SimpleNamespace())


def _make_neutralized_bot():
    """A stand-in for GatewayBot whose start/close are no-op coroutines.

    The lifespan launches ``bot.start()`` as a background task; neutralizing it
    keeps the real lifespan code path intact while never touching the Discord
    network.
    """
    fake_bot = MagicMock()
    fake_bot.start = AsyncMock()
    fake_bot.close = AsyncMock()
    fake_bot.wait_until_ready = AsyncMock()
    fake_bot.guilds = []
    return fake_bot


def _stub_create_task(coro):
    """create_task replacement: discard the (un-run) coroutine and return a real,
    already-completing task so the lifespan's cancel/await shutdown path works."""
    coro.close()
    return _REAL_CREATE_TASK(_noop())


class TestAutocompleteStateEnvVar:
    """Drive the REAL bot.py lifespan and assert it initializes autocomplete state
    from BOT_API_BASE_URL (not BOT_CORE_URL).

    CI-19 root cause: the old code used os.getenv("BOT_CORE_URL", ...) which is never
    set in the dev stack.  The fix reads BOT_API_BASE_URL — the same var all cogs use.
    Rather than re-implementing the getenv call in the test body, these tests execute
    the actual ``lifespan`` context manager with ``init_autocomplete_state`` patched to
    capture the ``api_base`` the lifespan resolves, so the assertion fails if bot.py
    ever reverts to reading BOT_CORE_URL.
    """

    async def _capture_api_base(self, bot_mod, monkeypatch) -> str:
        """Enter+exit the real lifespan and return the api_base it passed to
        init_autocomplete_state."""
        captured: list[str] = []

        def _capturing_init(http_client, api_base):
            captured.append(api_base)
            return None

        monkeypatch.setattr(bot_mod, "init_autocomplete_state", _capturing_init)
        monkeypatch.setattr(bot_mod, "GatewayBot", lambda: _make_neutralized_bot())
        monkeypatch.setattr("bot.asyncio.create_task", _stub_create_task)

        app = _make_lifespan_app()
        async with bot_mod.lifespan(app):
            pass
        assert len(captured) == 1, f"init_autocomplete_state called {len(captured)} times, expected 1"
        return captured[0]

    async def test_lifespan_uses_bot_api_base_url_not_bot_core_url(self, monkeypatch):
        """The lifespan must pass BOT_API_BASE_URL's value (not BOT_CORE_URL's) to
        init_autocomplete_state."""
        _evict_discord_modules()
        import bot as bot_mod

        sentinel_url = "http://bot-core:18000/api/v1"
        wrong_url = "http://bot-core:8000/api/v1"  # old BOT_CORE_URL default

        monkeypatch.setenv("BOTTOKEN", "fake-token-not-used")
        monkeypatch.setenv("BOT_API_BASE_URL", sentinel_url)
        monkeypatch.setenv("BOT_CORE_URL", wrong_url)

        resolved = await self._capture_api_base(bot_mod, monkeypatch)

        assert resolved == sentinel_url, (
            f"lifespan resolved api_base={resolved!r}; expected BOT_API_BASE_URL {sentinel_url!r}. "
            "The lifespan must use BOT_API_BASE_URL, not BOT_CORE_URL."
        )
        assert resolved != wrong_url

    async def test_bot_api_base_url_env_var_is_canonical(self, monkeypatch):
        """With BOT_CORE_URL absent, the lifespan still uses BOT_API_BASE_URL (proving
        BOT_CORE_URL is not consulted)."""
        _evict_discord_modules()
        import bot as bot_mod

        monkeypatch.setenv("BOTTOKEN", "fake-token-not-used")
        monkeypatch.setenv("BOT_API_BASE_URL", "http://bot-core:18000/api/v1")
        monkeypatch.delenv("BOT_CORE_URL", raising=False)

        resolved = await self._capture_api_base(bot_mod, monkeypatch)

        assert resolved == "http://bot-core:18000/api/v1"


class TestAutocompleteHealthProbe:
    """Drive the REAL startup health probe (the closure inside bot.py's lifespan) via a
    respx-mocked transport.

    The probe runs as a non-blocking background task — the bot must not crash and the
    lifespan must not stall.  When bot-core is unreachable (expected on a full cold
    start, since bot-core starts AFTER the gateway), the probe logs a WARNING rather
    than ERROR: it is non-fatal and the recurring warm jobs populate the caches once
    bot-core comes up.  These tests exercise the genuine probe code (real httpx client,
    real raise_for_status) rather than a copy pasted into the test body.
    """

    @staticmethod
    def _patches(bot_mod, monkeypatch, shared_logger, api_base):
        monkeypatch.setenv("BOTTOKEN", "fake-token-not-used")
        monkeypatch.setenv("BOT_API_BASE_URL", api_base)
        # Neutralize the Discord bot and the warm-on-boot task so only the probe runs.
        monkeypatch.setattr(bot_mod, "GatewayBot", lambda: _make_neutralized_bot())
        monkeypatch.setattr(bot_mod, "_warm_on_boot", AsyncMock())
        # One shared logger so we can inspect the probe's info/warning calls.
        monkeypatch.setattr(bot_mod.bblogger, "get_logger", lambda *a, **kw: shared_logger)

    @staticmethod
    def _messages(mock_method):
        return [c.args[0] if c.args else "" for c in mock_method.call_args_list]

    async def test_health_probe_logs_info_on_success(self, monkeypatch):
        """When GET {api_base}/health returns 200, the probe logs an OK info and no failure warning."""
        _evict_discord_modules()
        import bot as bot_mod

        api_base = "http://bot-core.test:18000/api/v1"
        shared_logger = MagicMock()
        self._patches(bot_mod, monkeypatch, shared_logger, api_base)

        async with respx.mock:
            respx.get(f"{api_base}/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))
            app = _make_lifespan_app()
            cm = bot_mod.lifespan(app)
            await cm.__aenter__()
            await app.state.probe_task  # run the real probe to completion
            await cm.__aexit__(None, None, None)

        info_msgs = self._messages(shared_logger.info)
        warning_msgs = self._messages(shared_logger.warning)
        assert any("health probe OK" in m for m in info_msgs), info_msgs
        assert not any("FAILED" in m for m in warning_msgs), warning_msgs

    async def test_health_probe_logs_warning_on_connect_failure(self, monkeypatch):
        """When GET {api_base}/health raises ConnectError on every attempt, the probe logs a
        FAILED warning mentioning the api_base (and does not crash the lifespan)."""
        _evict_discord_modules()
        import bot as bot_mod

        api_base = "http://unreachable-host.test:18000/api/v1"
        shared_logger = MagicMock()
        self._patches(bot_mod, monkeypatch, shared_logger, api_base)
        # The probe retries with 1s/2s backoff; skip the real waits.
        monkeypatch.setattr("bot.asyncio.sleep", AsyncMock())

        async with respx.mock:
            respx.get(f"{api_base}/health").mock(side_effect=httpx.ConnectError("Connection refused"))
            app = _make_lifespan_app()
            cm = bot_mod.lifespan(app)
            await cm.__aenter__()
            await app.state.probe_task
            await cm.__aexit__(None, None, None)

        warning_msgs = self._messages(shared_logger.warning)
        failed = [m for m in warning_msgs if "FAILED" in m]
        assert len(failed) == 1, warning_msgs
        assert api_base in failed[0]


class TestWarmOnBoot:
    """Tests for the one-shot startup pre-warm task (_warm_on_boot).

    It waits for the Discord bot AND bot-core to be reachable, then runs a single warm
    pass over the autocomplete caches. It must self-terminate (and warm nothing) if
    either readiness gate fails, leaving the recurring scheduler jobs to self-heal.
    """

    _WARM_FN_NAMES = (
        "warm_guild_shop_cache",
        "warm_guild_bounty_cache",
        "warm_guild_players",
        "warm_guild_duel_caches",
        "warm_guild_admin_duel_cache",
        "warm_guild_combatlog_caches",
        "refresh_jobs_cache",
    )

    def _install_fake_warm(self, monkeypatch):
        """Inject a fake utils.autocomplete_warm so _warm_on_boot's inner import resolves
        to AsyncMocks instead of the real warm coroutines. Returns the fake module."""
        import utils  # real package (already imported by `import bot`)

        fake_warm = types.ModuleType("utils.autocomplete_warm")
        for name in self._WARM_FN_NAMES:
            setattr(fake_warm, name, AsyncMock())
        monkeypatch.setitem(sys.modules, "utils.autocomplete_warm", fake_warm)
        monkeypatch.setattr(utils, "autocomplete_warm", fake_warm, raising=False)
        return fake_warm

    @staticmethod
    def _make_bot(guild_ids=(123,)):
        bot = MagicMock()
        bot.wait_until_ready = AsyncMock()
        bot.guilds = [MagicMock(id=gid) for gid in guild_ids]
        return bot

    @staticmethod
    def _make_http(ok=True):
        http = MagicMock()
        if ok:
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            http.get = AsyncMock(return_value=resp)
        else:
            http.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        return http

    async def test_happy_path_warms_each_cache_once(self, monkeypatch):
        """Bot ready + bot-core reachable → every warm coroutine is called once per guild,
        with the jobs cache warmed once overall."""
        import bot as bot_mod

        fake_warm = self._install_fake_warm(monkeypatch)
        bot = self._make_bot(guild_ids=(123,))
        http = self._make_http(ok=True)

        await bot_mod._warm_on_boot(bot, http, "http://bot-core:18000/api/v1")

        bot.wait_until_ready.assert_awaited_once()
        http.get.assert_awaited()  # readiness probe happened
        fake_warm.warm_guild_shop_cache.assert_awaited_once_with(bot, 123)
        fake_warm.warm_guild_bounty_cache.assert_awaited_once_with(bot, 123)
        fake_warm.warm_guild_players.assert_awaited_once_with(123)
        fake_warm.warm_guild_duel_caches.assert_awaited_once_with(bot, 123)
        fake_warm.warm_guild_admin_duel_cache.assert_awaited_once_with(bot, 123)
        fake_warm.warm_guild_combatlog_caches.assert_awaited_once_with(bot, 123)
        fake_warm.refresh_jobs_cache.assert_awaited_once_with(bot)

    async def test_botcore_unreachable_skips_warm(self, monkeypatch):
        """If bot-core never responds, the task gives up at the deadline and warms nothing."""
        import bot as bot_mod

        # Deadline 0 + poll 0 → bail on the first failed probe.
        monkeypatch.setenv("AUTOCOMPLETE_PREWARM_BOTCORE_DEADLINE_S", "0")
        monkeypatch.setenv("AUTOCOMPLETE_PREWARM_POLL_INTERVAL_S", "0")

        fake_warm = self._install_fake_warm(monkeypatch)
        bot = self._make_bot()
        http = self._make_http(ok=False)

        await bot_mod._warm_on_boot(bot, http, "http://bot-core:18000/api/v1")

        bot.wait_until_ready.assert_awaited_once()
        http.get.assert_awaited()  # probe attempted
        for name in self._WARM_FN_NAMES:
            getattr(fake_warm, name).assert_not_awaited()

    async def test_bot_not_ready_skips_warm(self, monkeypatch):
        """If the Discord bot never becomes ready, the task gives up before probing
        bot-core and warms nothing."""
        import bot as bot_mod

        monkeypatch.setenv("AUTOCOMPLETE_PREWARM_BOT_READY_TIMEOUT_S", "0.01")

        fake_warm = self._install_fake_warm(monkeypatch)

        async def _never():
            await asyncio.sleep(10)

        bot = self._make_bot()
        bot.wait_until_ready = AsyncMock(side_effect=_never)
        http = self._make_http(ok=True)

        await bot_mod._warm_on_boot(bot, http, "http://bot-core:18000/api/v1")

        http.get.assert_not_awaited()  # never reached the bot-core probe
        for name in self._WARM_FN_NAMES:
            getattr(fake_warm, name).assert_not_awaited()


if __name__ == "__main__":
    pytest.main([__file__])
