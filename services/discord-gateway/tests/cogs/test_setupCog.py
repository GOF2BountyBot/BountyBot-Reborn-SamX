"""
Tests for setupCog.py — on_guild_join and on_guild_remove event handlers.
"""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# -------------------------------------------------------------------------
# Bootstrap: mock shared.bblogger before any cog imports
# -------------------------------------------------------------------------

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")

_module_logger = None


def _make_mock_logger(*_args, **_kwargs):
    global _module_logger
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    _module_logger = logger
    return logger


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

# Ensure real discord is used
for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------


def _evict_discord_modules():
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


def _make_mock_guild(guild_id: int = 123456789, name: str = "Test Guild"):
    """Build a minimal mock discord.Guild."""
    import discord

    guild = MagicMock(spec=discord.Guild)
    guild.id = guild_id
    guild.name = name

    # Default role
    default_role = MagicMock()
    guild.default_role = default_role

    # Bot member
    me = MagicMock()
    me.id = 999999
    guild.me = me

    # Category / channel helpers
    guild.categories = []
    guild.text_channels = []
    guild.system_channel = None

    guild.create_category = AsyncMock(return_value=_make_mock_category(guild_id))
    guild.create_text_channel = AsyncMock(return_value=MagicMock())
    guild.fetch_roles = AsyncMock(return_value=[])

    return guild


def _make_mock_category(guild_id: int = 123456789):
    """Build a minimal mock discord.CategoryChannel."""
    import discord

    cat = MagicMock(spec=discord.CategoryChannel)
    cat.name = "BountyBot"
    cat.channels = []
    return cat


# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------


@pytest.fixture
def mock_bot():
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from discord.ext import commands

    bot = MagicMock(spec=commands.Bot)
    bot.add_cog = AsyncMock()
    return bot


@pytest.fixture
def mock_setup_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.setupCog import SetupCog

    cog = SetupCog(mock_bot)
    return cog


# -------------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------------


class TestOnGuildJoin:
    """Tests for on_guild_join event listener."""

    def test_on_guild_join_sends_welcome_to_system_channel(self, mock_setup_cog):
        """on_guild_join should send a welcome message to the system channel."""
        import discord

        guild = _make_mock_guild()

        system_channel = MagicMock(spec=discord.TextChannel)
        system_channel.name = "general"
        system_channel.send = AsyncMock()
        guild.system_channel = system_channel

        asyncio.run(mock_setup_cog.on_guild_join(guild))

        # Welcome message was sent
        system_channel.send.assert_called_once()
        embed_arg = system_channel.send.call_args[1]["embed"]
        assert embed_arg is not None
        # Should mention /admin_setup
        assert "admin_setup" in embed_arg.description

    def test_on_guild_join_falls_back_to_writable_channel(self, mock_setup_cog):
        """on_guild_join should fall back to first writable text channel if no system channel."""
        import discord

        guild = _make_mock_guild()
        guild.system_channel = None

        writable_channel = MagicMock(spec=discord.TextChannel)
        writable_channel.name = "chat"
        writable_channel.send = AsyncMock()
        writable_channel.permissions_for = MagicMock(return_value=MagicMock(send_messages=True))
        guild.text_channels = [writable_channel]

        asyncio.run(mock_setup_cog.on_guild_join(guild))

        writable_channel.send.assert_called_once()

    def test_on_guild_join_no_channel_available(self, mock_setup_cog):
        """on_guild_join should not raise if no writable channel is found."""
        guild = _make_mock_guild()
        guild.system_channel = None
        guild.text_channels = []

        # Should NOT raise
        asyncio.run(mock_setup_cog.on_guild_join(guild))

    def test_on_guild_join_permission_error_graceful(self, mock_setup_cog):
        """on_guild_join should handle Discord Forbidden errors gracefully."""
        import discord

        guild = _make_mock_guild()

        system_channel = MagicMock(spec=discord.TextChannel)
        system_channel.name = "general"
        system_channel.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "Forbidden"))
        guild.system_channel = system_channel

        # Should NOT raise
        asyncio.run(mock_setup_cog.on_guild_join(guild))

    def test_on_guild_join_does_not_create_infrastructure(self, mock_setup_cog):
        """on_guild_join should NOT create channels, categories, or roles."""
        import discord

        guild = _make_mock_guild()

        system_channel = MagicMock(spec=discord.TextChannel)
        system_channel.name = "general"
        system_channel.send = AsyncMock()
        guild.system_channel = system_channel

        asyncio.run(mock_setup_cog.on_guild_join(guild))

        # No infrastructure creation — only /admin_setup should create channels/roles
        guild.create_category.assert_not_called()
        guild.create_text_channel.assert_not_called()
        guild.fetch_roles.assert_not_called()

    def test_on_guild_join_generic_error_graceful(self, mock_setup_cog):
        """on_guild_join should handle unexpected errors gracefully."""
        import discord

        guild = _make_mock_guild()

        system_channel = MagicMock(spec=discord.TextChannel)
        system_channel.name = "general"
        system_channel.send = AsyncMock(side_effect=RuntimeError("unexpected"))
        guild.system_channel = system_channel

        # Should NOT raise
        asyncio.run(mock_setup_cog.on_guild_join(guild))


class TestOnGuildRemove:
    """Tests for on_guild_remove event listener."""

    def test_on_guild_remove_logs_removal(self, mock_setup_cog):
        """on_guild_remove should log the removal event."""
        guild = _make_mock_guild()

        # Cleanup endpoint — any response is fine
        cleanup_resp = MagicMock()
        cleanup_resp.status_code = 200
        mock_setup_cog.http_client.delete = AsyncMock(return_value=cleanup_resp)

        asyncio.run(mock_setup_cog.on_guild_remove(guild))

        # Module-level logger should have logged an info message
        global _module_logger
        if _module_logger is not None:
            _module_logger.info.assert_called()

    def test_on_guild_remove_cleanup_failure_is_nonfatal(self, mock_setup_cog):
        """on_guild_remove should not raise if the cleanup API call fails."""
        guild = _make_mock_guild()

        mock_setup_cog.http_client.delete = AsyncMock(side_effect=Exception("Connection refused"))

        # Must not raise
        asyncio.run(mock_setup_cog.on_guild_remove(guild))


class TestSetupCogInit:
    """Tests for SetupCog initialization."""

    def test_initialization(self, mock_setup_cog):
        """SetupCog should initialize with bot reference and http client."""
        assert mock_setup_cog.bot is not None
        assert mock_setup_cog.http_client is not None

    def test_setup_function(self, mock_bot):
        """setup() should add SetupCog to the bot."""
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        _evict_discord_modules()
        from cogs.setupCog import setup

        asyncio.run(setup(mock_bot))
        mock_bot.add_cog.assert_called_once()

    def test_cog_unload_closes_http_client(self, mock_setup_cog):
        """cog_unload should close the HTTP client."""
        mock_setup_cog.http_client = MagicMock()
        mock_setup_cog.http_client.aclose = AsyncMock()

        asyncio.run(mock_setup_cog.cog_unload())

        mock_setup_cog.http_client.aclose.assert_called_once()


# ---------------------------------------------------------------------------
# on_guild_remove URL+method contract (respx) — B.37 regression net (2026-04-30)
# ---------------------------------------------------------------------------


class TestOnGuildRemoveRespx:
    """respx-backed contract test for setupCog.on_guild_remove.

    The existing TestOnGuildRemove tests above use AsyncMock(http_client.delete)
    which is tautological — bugs in URL or HTTP method pass silently. This
    class follows the policy in services/discord-gateway/tests/AGENTS.md
    (B.33 remediation) and asserts the exact URL+method contract:

      DELETE /api/v1/admin/guilds/{guild_id}/cleanup

    History: this test was added as part of the B.37 remediation. Pre-fix,
    setupCog called this URL but bot-core had no matching route — every
    bot-removal returned 404 and lingering DB state was never cleaned. The
    fix added the bot-core endpoint AND this test, locking the contract so a
    future refactor (URL change, method swap) cannot silently re-introduce
    the bug.
    """

    _BOT_API = "http://bot-core:8000/api/v1"

    def _with_real_client(self, cog, request):
        """Replace cog.http_client with a real httpx.AsyncClient for respx interception.

        Registers a pytest finalizer to close the client after the test so no
        httpx.AsyncClient instances are leaked between tests.
        """
        import httpx

        cog.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        request.addfinalizer(lambda: asyncio.run(cog.http_client.aclose()))
        return cog

    def test_on_guild_remove_calls_correct_url_and_method(self, mock_setup_cog, request):
        """on_guild_remove must DELETE /api/v1/admin/guilds/{guild_id}/cleanup."""
        import httpx
        import respx

        self._with_real_client(mock_setup_cog, request)
        guild = _make_mock_guild(guild_id=987654321)

        env_without_bot_api = {k: v for k, v in os.environ.items() if k != "BOT_API_BASE_URL"}
        with (
            patch.dict(os.environ, env_without_bot_api, clear=True),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.delete(f"{self._BOT_API}/admin/guilds/987654321/cleanup").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "guild_id": 987654321,
                        "removed_counts": {"players": 0, "shop_items": "all", "config": 0},
                        "jobs_cancelled": 0,
                        "bounties_cleared": 0,
                        "message": "Soft cleanup of guild 987654321 complete (DB-only, Discord-side untouched)",
                    },
                )
            )
            asyncio.run(mock_setup_cog.on_guild_remove(guild))
        # respx assert_all_called=True ensures the DELETE endpoint WAS hit at the
        # exact URL — any drift in the URL or method causes the test to fail.

    def test_on_guild_remove_handles_404_gracefully(self, mock_setup_cog, request):
        """on_guild_remove must not raise if bot-core returns 404 (lingering bug regression net).

        This guards the legacy behaviour: even if some future deployment serves
        a bot-core that has not yet shipped the /cleanup endpoint, the cog must
        not crash the bot's event loop. The 404 is logged at DEBUG and swallowed.
        """
        import httpx
        import respx

        self._with_real_client(mock_setup_cog, request)
        guild = _make_mock_guild(guild_id=111222333)

        env_without_bot_api = {k: v for k, v in os.environ.items() if k != "BOT_API_BASE_URL"}
        with (
            patch.dict(os.environ, env_without_bot_api, clear=True),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.delete(f"{self._BOT_API}/admin/guilds/111222333/cleanup").mock(
                return_value=httpx.Response(404, json={"detail": "Not Found"})
            )
            # Must NOT raise.
            asyncio.run(mock_setup_cog.on_guild_remove(guild))


# ---------------------------------------------------------------------------
# TestOnGuildJoinCommandSync — tests for the slash-command sync block added to
# on_guild_join (lines 76-82 of setupCog.py).
#
# Coverage gaps found during review (2026-05-05):
#   • All existing TestOnGuildJoin tests inadvertently exercise the *except* path
#     of the sync block because bot.tree.sync is a plain MagicMock (not an
#     AsyncMock), so `await bot.tree.sync(...)` raises TypeError → caught →
#     warning logged.  Line 80 (info log on success) was therefore at 0%.
#   • No test verified that copy_global_to is called with the guild object.
#   • No test verified that sync is called with discord.Object(id=guild.id).
#   • No test verified the warning-log path is taken on sync failure.
#   • No test verified the independence of the sync block from the welcome-
#     message block (i.e. sync still runs even when welcome message fails).
# ---------------------------------------------------------------------------


class TestOnGuildJoinCommandSync:
    """Tests for the command-sync block added at the end of on_guild_join."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_guild_with_sync_bot(bot):
        """Return a mock guild AND configure bot.tree with AsyncMock so that
        the sync succeeds (exercises the success/info-log path)."""
        import discord

        bot.tree.copy_global_to = MagicMock()  # synchronous — no await needed
        bot.tree.sync = AsyncMock(return_value=[])

        guild = _make_mock_guild()
        return guild

    # ------------------------------------------------------------------
    # Happy path: sync succeeds
    # ------------------------------------------------------------------

    def test_sync_success_calls_copy_global_to_with_guild(self, mock_setup_cog, mock_bot):
        """copy_global_to must be called with the exact guild object."""
        guild = self._make_guild_with_sync_bot(mock_bot)
        mock_setup_cog.bot = mock_bot

        asyncio.run(mock_setup_cog.on_guild_join(guild))

        mock_bot.tree.copy_global_to.assert_called_once_with(guild=guild)

    def test_sync_success_calls_sync_with_guild_object(self, mock_setup_cog, mock_bot):
        """sync must be awaited with discord.Object(id=guild.id)."""
        import discord

        guild = self._make_guild_with_sync_bot(mock_bot)
        mock_setup_cog.bot = mock_bot

        asyncio.run(mock_setup_cog.on_guild_join(guild))

        mock_bot.tree.sync.assert_awaited_once()
        call_kwargs = mock_bot.tree.sync.call_args
        passed_guild = call_kwargs.kwargs.get("guild") or call_kwargs.args[0]
        # Must be a discord.Object (not a full Guild) with the right ID
        assert isinstance(passed_guild, discord.Object)
        assert passed_guild.id == guild.id

    def test_sync_success_does_not_raise(self, mock_setup_cog, mock_bot):
        """on_guild_join must not propagate exceptions from a successful sync."""
        guild = self._make_guild_with_sync_bot(mock_bot)
        mock_setup_cog.bot = mock_bot

        # Must not raise
        asyncio.run(mock_setup_cog.on_guild_join(guild))

    # ------------------------------------------------------------------
    # Independence: sync block runs even when welcome message fails
    # ------------------------------------------------------------------

    def test_sync_runs_even_when_welcome_message_raises(self, mock_setup_cog, mock_bot):
        """The sync try/except is independent: it executes even when the welcome
        message block raises an unhandled exception."""
        import discord

        guild = self._make_guild_with_sync_bot(mock_bot)
        mock_setup_cog.bot = mock_bot

        system_channel = MagicMock(spec=discord.TextChannel)
        system_channel.name = "general"
        system_channel.send = AsyncMock(side_effect=RuntimeError("channel exploded"))
        guild.system_channel = system_channel

        asyncio.run(mock_setup_cog.on_guild_join(guild))

        # Sync should still have been called despite the welcome-message failure
        mock_bot.tree.sync.assert_awaited_once()

    def test_sync_runs_even_when_forbidden_on_welcome(self, mock_setup_cog, mock_bot):
        """Sync must still run after a discord.Forbidden on the welcome message."""
        import discord

        guild = self._make_guild_with_sync_bot(mock_bot)
        mock_setup_cog.bot = mock_bot

        system_channel = MagicMock(spec=discord.TextChannel)
        system_channel.name = "general"
        system_channel.send = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(status=403), "Forbidden")
        )
        guild.system_channel = system_channel

        asyncio.run(mock_setup_cog.on_guild_join(guild))

        mock_bot.tree.sync.assert_awaited_once()

    # ------------------------------------------------------------------
    # Error path: sync failure is swallowed (warning log, no propagation)
    # ------------------------------------------------------------------

    def test_sync_failure_does_not_raise(self, mock_setup_cog, mock_bot):
        """A sync failure must be caught and swallowed — not re-raised."""
        mock_bot.tree.copy_global_to = MagicMock()
        mock_bot.tree.sync = AsyncMock(side_effect=Exception("Forbidden: missing scope"))
        mock_setup_cog.bot = mock_bot

        guild = _make_mock_guild()
        guild.system_channel = None
        guild.text_channels = []

        # Must NOT raise
        asyncio.run(mock_setup_cog.on_guild_join(guild))

    def test_sync_http_exception_swallowed(self, mock_setup_cog, mock_bot):
        """discord.HTTPException (e.g. missing applications.commands scope) must
        be caught and swallowed, not propagate out of on_guild_join."""
        import discord

        mock_bot.tree.copy_global_to = MagicMock()
        # Simulate the 403 that Discord returns when the bot lacks the
        # applications.commands OAuth2 scope.
        mock_response = MagicMock()
        mock_response.status = 403
        mock_response.reason = "Forbidden"
        mock_bot.tree.sync = AsyncMock(
            side_effect=discord.HTTPException(mock_response, "Missing Access")
        )
        mock_setup_cog.bot = mock_bot

        guild = _make_mock_guild()
        guild.system_channel = None
        guild.text_channels = []

        # Must NOT raise
        asyncio.run(mock_setup_cog.on_guild_join(guild))

    def test_sync_copy_global_failure_swallowed(self, mock_setup_cog, mock_bot):
        """If copy_global_to itself raises, the exception must be swallowed
        (it is inside the same try block as the sync)."""
        mock_bot.tree.copy_global_to = MagicMock(side_effect=RuntimeError("tree not ready"))
        mock_bot.tree.sync = AsyncMock()
        mock_setup_cog.bot = mock_bot

        guild = _make_mock_guild()
        guild.system_channel = None
        guild.text_channels = []

        # Must NOT raise
        asyncio.run(mock_setup_cog.on_guild_join(guild))

        # sync should NOT be awaited because copy_global_to raised first
        mock_bot.tree.sync.assert_not_awaited()

    # ------------------------------------------------------------------
    # Edge case: empty command tree
    # ------------------------------------------------------------------

    def test_sync_with_empty_command_tree(self, mock_setup_cog, mock_bot):
        """Syncing a bot with no registered commands should not raise."""
        mock_bot.tree.copy_global_to = MagicMock()
        mock_bot.tree.sync = AsyncMock(return_value=[])  # Discord returns empty list
        mock_setup_cog.bot = mock_bot

        guild = _make_mock_guild()
        guild.system_channel = None
        guild.text_channels = []

        asyncio.run(mock_setup_cog.on_guild_join(guild))
        mock_bot.tree.sync.assert_awaited_once()

    # ------------------------------------------------------------------
    # Edge case: sync returns non-empty list (normal case)
    # ------------------------------------------------------------------

    def test_sync_with_commands_returns_list(self, mock_setup_cog, mock_bot):
        """Sync returning a list of commands (normal success) must be handled."""
        mock_bot.tree.copy_global_to = MagicMock()
        mock_bot.tree.sync = AsyncMock(return_value=[MagicMock(), MagicMock()])
        mock_setup_cog.bot = mock_bot

        guild = _make_mock_guild()
        guild.system_channel = None
        guild.text_channels = []

        # No exception, sync was awaited
        asyncio.run(mock_setup_cog.on_guild_join(guild))
        mock_bot.tree.sync.assert_awaited_once()

    # ------------------------------------------------------------------
    # Verify correct discord.Object id, not a string or wrong type
    # ------------------------------------------------------------------

    def test_sync_uses_guild_id_not_guild_name(self, mock_setup_cog, mock_bot):
        """discord.Object must be constructed from guild.id (int), not name."""
        import discord

        mock_bot.tree.copy_global_to = MagicMock()
        mock_bot.tree.sync = AsyncMock(return_value=[])
        mock_setup_cog.bot = mock_bot

        guild = _make_mock_guild(guild_id=555666777, name="SomeGuild")

        asyncio.run(mock_setup_cog.on_guild_join(guild))

        call_kwargs = mock_bot.tree.sync.call_args
        passed_guild = call_kwargs.kwargs.get("guild") or call_kwargs.args[0]
        assert isinstance(passed_guild, discord.Object)
        assert passed_guild.id == 555666777, (
            f"Expected discord.Object(id=555666777) but got id={passed_guild.id}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
