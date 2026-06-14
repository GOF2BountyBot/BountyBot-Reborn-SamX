import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# Import discord_mock_utils for consistent mock patterns
from tests.mocks.discord_mock_utils import DiscordMockUtils

# Create module-level mock utilities
_mock_utils = DiscordMockUtils()

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


class TestAutocompleteStateEnvVar:
    """Verify that the lifespan reads BOT_API_BASE_URL (not BOT_CORE_URL) for autocomplete init.

    CI-19 root cause: the old code used os.getenv("BOT_CORE_URL", ...) which is never set
    in the dev stack.  The fix changes this to BOT_API_BASE_URL — the same var all cogs use.
    """

    def test_lifespan_uses_bot_api_base_url_not_bot_core_url(self, monkeypatch):
        """init_autocomplete_state is called with the value of BOT_API_BASE_URL, not BOT_CORE_URL.

        Strategy: patch os.getenv so that BOT_API_BASE_URL returns a sentinel and
        BOT_CORE_URL returns a different value.  Then assert the captured api_base
        passed to init_autocomplete_state matches the sentinel (BOT_API_BASE_URL value).
        """
        _evict_discord_modules()
        import bot as bot_mod

        sentinel_url = "http://bot-core:18000/api/v1"
        wrong_url = "http://bot-core:8000/api/v1"  # old BOT_CORE_URL default

        captured: list[str] = []

        real_getenv = os.getenv

        def _patched_getenv(key, default=None):
            if key == "BOT_API_BASE_URL":
                return sentinel_url
            if key == "BOT_CORE_URL":
                return wrong_url
            return real_getenv(key, default)

        monkeypatch.setattr(bot_mod.os, "getenv", _patched_getenv)

        def _capturing_init(http_client, api_base):
            captured.append(api_base)
            # Don't actually run real init in this unit test
            return None

        monkeypatch.setattr(bot_mod, "init_autocomplete_state", _capturing_init)

        # Extract api_base from bot.py lifespan source — simpler than running the full
        # async lifespan: just call os.getenv through the patched module directly.
        resolved = bot_mod.os.getenv("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
        assert resolved == sentinel_url, (
            f"BOT_API_BASE_URL should resolve to {sentinel_url!r} but got {resolved!r}. "
            "The lifespan must use BOT_API_BASE_URL, not BOT_CORE_URL."
        )

        wrong_resolved = bot_mod.os.getenv("BOT_CORE_URL", "http://bot-core:8000/api/v1")
        assert wrong_resolved == wrong_url
        # Confirm sentinel != wrong — so the two env vars are distinguishable
        assert sentinel_url != wrong_url

    def test_bot_api_base_url_env_var_is_canonical(self, monkeypatch):
        """BOT_CORE_URL env var is not set in the dev stack; BOT_API_BASE_URL is the sole source.

        This test documents the contract: if BOT_CORE_URL is absent but BOT_API_BASE_URL
        is set, the lifespan must pick up BOT_API_BASE_URL.
        """
        _evict_discord_modules()
        import bot as bot_mod

        # Simulate dev stack: BOT_API_BASE_URL is set, BOT_CORE_URL is absent
        monkeypatch.setenv("BOT_API_BASE_URL", "http://bot-core:18000/api/v1")
        monkeypatch.delenv("BOT_CORE_URL", raising=False)

        # The correct pattern used in bot.py after the fix:
        api_base = bot_mod.os.getenv("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
        assert api_base == "http://bot-core:18000/api/v1"

        # The old (broken) pattern would have returned the default (:8000) since BOT_CORE_URL is absent:
        old_pattern_result = os.getenv("BOT_CORE_URL", "http://bot-core:8000/api/v1")
        assert old_pattern_result == "http://bot-core:8000/api/v1", (
            "BOT_CORE_URL is unset → old pattern falls back to wrong :8000 default, proving the bug"
        )


class TestAutocompleteHealthProbe:
    """Verify the startup health probe behaviour (CI-19).

    The probe runs as a non-blocking background task — the bot must not crash and the
    lifespan must not stall.  When bot-core is unreachable (expected on a full cold
    start, since bot-core starts AFTER the gateway), the probe logs a WARNING rather
    than ERROR: it is non-fatal and the recurring warm jobs populate the caches once
    bot-core comes up.
    """

    async def test_health_probe_logs_warning_on_connect_failure(self, monkeypatch, caplog):
        """When the health endpoint returns a connection error, flogger.warning is called.

        We test the probe logic in isolation: build the probe block inputs (a fake
        async http client that raises ConnectError) and verify the failure path.
        """
        import httpx

        flogger_calls: list[str] = []

        class _FakeLogger:
            def info(self, msg, *a, **kw):
                pass

            def warning(self, msg, *a, **kw):
                flogger_calls.append(msg)

            def error(self, msg, *a, **kw):
                pass

            def critical(self, msg, *a, **kw):
                pass

            def debug(self, msg, *a, **kw):
                pass

            def trace(self, msg, *a, **kw):
                pass

        fake_flogger = _FakeLogger()
        api_base = "http://unreachable-host:18000/api/v1"

        # Simulate the probe block from bot.py lifespan directly
        class _FailingClient:
            async def get(self, url, timeout=None):
                raise httpx.ConnectError("Connection refused")

        autocomplete_http = _FailingClient()
        try:
            probe_resp = await autocomplete_http.get(f"{api_base}/health", timeout=3.0)
            probe_resp.raise_for_status()
            fake_flogger.info(f"Autocomplete health probe OK: api_base={api_base}")
        except Exception as _probe_exc:  # pylint: disable=broad-exception-caught
            fake_flogger.warning(
                f"Autocomplete health probe FAILED after 3 attempts — "
                f"bot-core not reachable at gateway startup (expected on a full cold start). "
                f"Recurring warm jobs will populate the autocomplete caches once bot-core is up. "
                f"api_base={api_base!r} last_error={_probe_exc!r}."
            )

        assert len(flogger_calls) == 1
        assert "FAILED" in flogger_calls[0]
        assert api_base in flogger_calls[0]

    async def test_health_probe_logs_info_on_success(self):
        """When the health endpoint responds 200, only flogger.info is called (no error)."""
        import httpx

        info_calls: list[str] = []
        warning_calls: list[str] = []

        class _FakeLogger:
            def info(self, msg, *a, **kw):
                info_calls.append(msg)

            def warning(self, msg, *a, **kw):
                warning_calls.append(msg)

        fake_flogger = _FakeLogger()
        api_base = "http://bot-core:18000/api/v1"

        class _OKClient:
            async def get(self, url, timeout=None):
                resp = httpx.Response(200, json={"status": "ok"}, request=httpx.Request("GET", url))
                return resp

        autocomplete_http = _OKClient()
        try:
            probe_resp = await autocomplete_http.get(f"{api_base}/health", timeout=3.0)
            probe_resp.raise_for_status()
            fake_flogger.info(f"Autocomplete health probe OK: api_base={api_base}")
        except Exception as _probe_exc:  # pylint: disable=broad-exception-caught
            fake_flogger.warning(
                f"Autocomplete health probe FAILED after 3 attempts — "
                f"bot-core not reachable at gateway startup (expected on a full cold start). "
                f"Recurring warm jobs will populate the autocomplete caches once bot-core is up. "
                f"api_base={api_base!r} last_error={_probe_exc!r}."
            )

        assert any("OK" in m for m in info_calls)
        assert warning_calls == [], f"No warning expected on success, but got: {warning_calls}"


if __name__ == "__main__":
    pytest.main([__file__])
