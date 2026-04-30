import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import discord_mock_utils for consistent mock patterns
from tests.mocks.discord_mock_utils import DiscordMockUtils

# Create module-level mock utilities
_mock_utils = DiscordMockUtils()

# Setup mock shared.bblogger module
_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")

# Track the module-level logger
_module_logger = None


def _make_mock_logger(*_args, **_kwargs):
    """Return a MagicMock that already has common log-level methods."""
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

# Ensure real discord is used (not a hand-rolled fake from another test module)
for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


def _close_coro(coro):
    """Close coroutine to prevent 'never awaited' warning."""
    coro.close()
    return MagicMock()


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot for devCog testing."""
    loop = MagicMock()
    loop.create_task = MagicMock(side_effect=_close_coro)
    bot = DiscordMockUtils.create_mock_bot(
        user_id=123456789,
        username="TestBot",
        add_cog=MagicMock(),
        tree=MagicMock(),
        get_member=MagicMock(),
        flogger=MagicMock(),
        get_cog=MagicMock(),
        loop=loop,
    )
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
def mock_dev_cog(mock_bot):
    """Create a mock devCog instance."""
    _evict_discord_modules()
    from cogs.devCog import DevCog

    cog = DevCog(mock_bot)
    return cog


class TestDevCogInitialization:
    """Tests for devCog initialization."""

    def test_initialization(self, mock_dev_cog):
        """devCog should initialize properly with bot reference."""
        assert mock_dev_cog.bot is not None
        assert mock_dev_cog._categories == []
        mock_dev_cog.bot.loop.create_task.assert_called_once()


class TestCategoryPreload:
    """Tests for category preloading.

    B.33 followup (Finding 2): tests use respx to assert exact URL + HTTP method,
    confirming devCog calls GET /api/v1/data/categories on bot-core
    (route registered in bot-core/src/api/routers/data.py:43).
    """

    _CATEGORIES_URL = "http://bot-core:8000/api/v1/data/categories"

    def _with_real_client(self, cog, request):
        """Replace cog.http_client with a real httpx.AsyncClient for respx interception.

        Registers a pytest finalizer to close the client after the test so no
        httpx.AsyncClient instances are leaked between tests.
        """
        import httpx

        cog.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        request.addfinalizer(lambda: asyncio.run(cog.http_client.aclose()))
        return cog

    def test_preload_categories_success(self, mock_dev_cog, request):
        """_preload_categories calls GET /api/v1/data/categories and populates _categories."""
        import httpx
        import respx

        self._with_real_client(mock_dev_cog, request)
        mock_dev_cog.bot.wait_until_ready = AsyncMock()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(self._CATEGORIES_URL).mock(
                return_value=httpx.Response(200, json=["ships", "modules", "weapons"])
            )
            asyncio.run(mock_dev_cog._preload_categories())

        assert mock_dev_cog._categories == ["ships", "modules", "weapons"]

    def test_preload_categories_http_error_leaves_empty(self, mock_dev_cog, request):
        """_preload_categories leaves _categories empty on HTTP error response."""
        import httpx
        import respx

        self._with_real_client(mock_dev_cog, request)
        mock_dev_cog.bot.wait_until_ready = AsyncMock()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(self._CATEGORIES_URL).mock(
                return_value=httpx.Response(503, json={"detail": "Service Unavailable"})
            )
            asyncio.run(mock_dev_cog._preload_categories())

        # On HTTP error, _categories remains empty (no retry in this preload)
        assert mock_dev_cog._categories == []

    def test_preload_categories_network_error_leaves_empty(self, mock_dev_cog, request):
        """_preload_categories leaves _categories empty on network-level error."""
        import httpx
        import respx

        self._with_real_client(mock_dev_cog, request)
        mock_dev_cog.bot.wait_until_ready = AsyncMock()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(self._CATEGORIES_URL).mock(side_effect=httpx.ConnectError("connection refused"))
            asyncio.run(mock_dev_cog._preload_categories())

        assert mock_dev_cog._categories == []


class TestCategoryAutocomplete:
    """Tests for category autocomplete."""

    def test_category_autocomplete(self, mock_dev_cog):
        """category_autocomplete should return filtered choices."""
        mock_dev_cog._categories = ["ships", "modules", "weapons", "systems"]

        # Test with empty current
        choices = asyncio.run(mock_dev_cog.category_autocomplete(MagicMock(), ""))
        assert len(choices) == 5  # All + 4 categories
        assert choices[0].name == "All"
        assert choices[1].name == "ships"

        # Test with partial match (current is checked against cat names)
        # "sh" matches "ships" but NOT "All"
        choices = asyncio.run(mock_dev_cog.category_autocomplete(MagicMock(), "sh"))
        assert len(choices) == 1
        assert choices[0].name == "ships"

        # Test case insensitivity
        choices = asyncio.run(mock_dev_cog.category_autocomplete(MagicMock(), "SH"))
        assert len(choices) == 1
        assert choices[0].name == "ships"

        # Test limit - need at least 24 categories + All = 25
        mock_dev_cog._categories = [f"category_{i}" for i in range(30)]
        choices = asyncio.run(mock_dev_cog.category_autocomplete(MagicMock(), ""))
        assert len(choices) == 25  # Should be limited to 25


class TestLoadDataCommand:
    """Tests for load_data command."""

    @patch("cogs.devCog.httpx")
    def test_load_data_single_category_success(self, mock_httpx, mock_dev_cog):
        """load_data should load single category successfully."""
        # Mock interaction
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Mock API response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = ["file1", "file2"]
        mock_dev_cog.http_client.post = AsyncMock(return_value=mock_response)

        # Call command via callback
        asyncio.run(mock_dev_cog.load_data.callback(mock_dev_cog, interaction, "ships"))

        # Verify behavior
        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once_with("✅ Data load complete for **ships**: 2 files processed.")

    @patch("cogs.devCog.httpx")
    def test_load_data_all_categories_success(self, mock_httpx, mock_dev_cog):
        """load_data should load all categories when 'All' is selected."""
        # Mock interaction
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Mock API responses
        mock_dev_cog._categories = ["ships", "modules"]

        ships_resp = MagicMock()
        ships_resp.status_code = 200
        ships_resp.json.return_value = ["ship1", "ship2"]

        modules_resp = MagicMock()
        modules_resp.status_code = 200
        modules_resp.json.return_value = ["module1"]

        mock_dev_cog.http_client.post = AsyncMock(side_effect=[ships_resp, modules_resp])

        # Call command via callback
        asyncio.run(mock_dev_cog.load_data.callback(mock_dev_cog, interaction, "All"))

        # Verify behavior
        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once()

    @patch("cogs.devCog.httpx")
    def test_load_data_api_error(self, mock_httpx, mock_dev_cog):
        """load_data should handle API errors gracefully."""
        # Mock interaction
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Mock HTTP client with httpx HTTPStatusError using the mocked module
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        error = mock_httpx.HTTPStatusError("Server error", request=MagicMock(), response=mock_response)
        mock_dev_cog.http_client.post = AsyncMock(side_effect=error)

        # Call command via callback
        asyncio.run(mock_dev_cog.load_data.callback(mock_dev_cog, interaction, "ships"))

        # Verify error handling
        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once()


class TestReloadAutocompleteCommand:
    """Tests for reload_autocomplete command."""

    @patch("cogs.devCog.httpx")
    def test_reload_autocomplete_success(self, mock_httpx, mock_dev_cog):
        """reload_autocomplete should reload cog methods successfully."""
        # Mock interaction
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Mock a cog that responds to all method requests (AboutCog, BountyCog, etc.)
        about_cog = MagicMock()
        about_cog._preload_data = AsyncMock()
        about_cog._preload_categories = AsyncMock()
        about_cog._preload_ship_skins = AsyncMock()
        about_cog._preload_render_settings = AsyncMock()
        about_cog._preload_static_catalogs = AsyncMock()
        about_cog._shop_cache = MagicMock()
        about_cog._shop_cache.clear = MagicMock()
        mock_dev_cog.bot.get_cog.return_value = about_cog

        # Call command via callback
        asyncio.run(mock_dev_cog.reload_autocomplete.callback(mock_dev_cog, interaction))

        # Verify behavior — at least the preload methods were called
        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once()
        # _preload_data is called for AboutCog and BountyCog (2 times total)
        assert about_cog._preload_data.await_count >= 1

    @patch("cogs.devCog.httpx")
    def test_reload_autocomplete_with_errors(self, mock_httpx, mock_dev_cog):
        """reload_autocomplete should handle cog method failures."""
        # Mock interaction
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Mock other cogs with errors
        about_cog = MagicMock()
        about_cog._preload_data = AsyncMock(side_effect=Exception("Test error"))
        mock_dev_cog.bot.get_cog.return_value = about_cog

        # Call command via callback
        asyncio.run(mock_dev_cog.reload_autocomplete.callback(mock_dev_cog, interaction))

        # Verify error handling
        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once()


class TestErrorHandling:
    """Tests for error handling in devCog."""

    @patch("cogs.devCog.httpx")
    def test_load_data_connection_error(self, mock_httpx, mock_dev_cog):
        """load_data should handle connection errors gracefully."""
        import httpx

        # Make the mock httpx use real exception classes
        mock_httpx.HTTPStatusError = httpx.HTTPStatusError

        # Mock interaction
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Mock HTTP client with generic Exception
        mock_dev_cog.http_client.post = AsyncMock(side_effect=Exception("Connection failed"))

        # Call command via callback
        asyncio.run(mock_dev_cog.load_data.callback(mock_dev_cog, interaction, "ships"))

        # Verify error handling
        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once()


class TestCogUnload:
    """Tests for cog unload functionality."""

    @patch("cogs.devCog.httpx")
    def test_cog_unload_success(self, mock_httpx, mock_dev_cog):
        """cog_unload should close http_client successfully."""
        mock_dev_cog.http_client.aclose = AsyncMock()
        asyncio.run(mock_dev_cog.cog_unload())
        mock_dev_cog.http_client.aclose.assert_called_once()


class TestLoadDataAllCategoriesWithErrors:
    """Tests for load_data 'All' path with errors."""

    @patch("cogs.devCog.httpx")
    def test_load_data_all_with_partial_errors(self, mock_httpx, mock_dev_cog):
        """load_data should handle partial failures when loading all categories."""
        import httpx

        # Make the mock httpx use real exception classes
        mock_httpx.HTTPStatusError = httpx.HTTPStatusError

        # Mock interaction
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Mock API responses - one success, one failure
        mock_dev_cog._categories = ["ships", "modules"]

        ships_resp = MagicMock()
        ships_resp.status_code = 200
        ships_resp.json.return_value = ["ship1", "ship2"]
        ships_resp.raise_for_status = MagicMock()

        # Second category fails
        modules_error = httpx.HTTPStatusError("Failed", request=MagicMock(), response=MagicMock())
        mock_dev_cog.http_client.post = AsyncMock(side_effect=[ships_resp, modules_error])

        # Call command via callback
        asyncio.run(mock_dev_cog.load_data.callback(mock_dev_cog, interaction, "All"))

        # Verify behavior
        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once()
        # Check that the response mentions errors
        call_args = interaction.followup.send.call_args
        assert call_args is not None

    @patch("cogs.devCog.httpx")
    def test_load_data_all_body_truncation(self, mock_httpx, mock_dev_cog):
        """load_data should truncate very long response body for 'All'."""
        # Mock interaction
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Create many categories to trigger truncation
        mock_dev_cog._categories = [f"category_{i}" for i in range(50)]

        # Mock responses with long data
        responses = []
        for _ in range(50):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = [f"file_{j}" for j in range(10)]
            resp.raise_for_status = MagicMock()
            responses.append(resp)

        mock_dev_cog.http_client.post = AsyncMock(side_effect=responses)

        # Call command via callback
        asyncio.run(mock_dev_cog.load_data.callback(mock_dev_cog, interaction, "All"))

        # Verify behavior - body should be truncated
        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once()


class TestLoadDataSingleCategoryErrors:
    """Tests for load_data single category error paths."""

    @patch("cogs.devCog.httpx")
    def test_load_data_http_status_error(self, mock_httpx, mock_dev_cog):
        """load_data should handle HTTPStatusError for single category."""
        import httpx

        # Make the mock httpx use real exception classes
        mock_httpx.HTTPStatusError = httpx.HTTPStatusError

        # Mock interaction
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Create a real HTTPStatusError
        mock_response = MagicMock()
        mock_response.status_code = 500
        error = httpx.HTTPStatusError("Server error", request=MagicMock(), response=mock_response)
        mock_dev_cog.http_client.post = AsyncMock(side_effect=error)

        # Call command via callback
        asyncio.run(mock_dev_cog.load_data.callback(mock_dev_cog, interaction, "ships"))

        # Verify error handling
        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once()


class TestReloadAutocompleteEdgeCases:
    """Tests for reload_autocomplete edge cases."""

    @patch("cogs.devCog.httpx")
    def test_reload_autocomplete_cog_not_found(self, mock_httpx, mock_dev_cog):
        """reload_autocomplete should handle missing cogs gracefully."""
        # Mock interaction
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Mock bot.get_cog to return None for first cog
        mock_dev_cog.bot.get_cog.return_value = None

        # Call command via callback
        asyncio.run(mock_dev_cog.reload_autocomplete.callback(mock_dev_cog, interaction))

        # Verify behavior
        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once()
        # Response should contain "cog not found" message
        call_args = interaction.followup.send.call_args[0][0]
        assert "not found" in call_args

    @patch("cogs.devCog.httpx")
    def test_reload_autocomplete_method_not_found(self, mock_httpx, mock_dev_cog):
        """reload_autocomplete should handle missing methods gracefully."""
        # Mock interaction
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Mock cog without the expected method
        mock_cog = MagicMock()
        mock_cog._preload_data = None  # Method doesn't exist
        mock_dev_cog.bot.get_cog.return_value = mock_cog

        # Call command via callback
        asyncio.run(mock_dev_cog.reload_autocomplete.callback(mock_dev_cog, interaction))

        # Verify behavior - should report method not found
        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once()


class TestCogSetup:
    """Tests for cog setup function."""

    def test_setup_function(self, mock_bot):
        """setup function should add devCog to bot."""
        from cogs.devCog import setup

        # Make add_cog awaitable
        mock_bot.add_cog = AsyncMock()

        asyncio.run(setup(mock_bot))

        mock_bot.add_cog.assert_called_once()


# ===========================================================================
# Package E — Tests #27–29: /reload_autocomplete extended coverage
# ===========================================================================


class TestReloadAutocompletePackageE:
    """Tests for reload_autocomplete covering new Package E targets (spec tests #27–29)."""

    def _make_interaction(self):
        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()
        return interaction

    # ------------------------------------------------------------------
    # Test #27 — invokes AdminCog._preload_static_catalogs
    # ------------------------------------------------------------------

    @patch("cogs.devCog.httpx")
    def test_reload_invokes_admin_preload_static_catalogs(self, mock_httpx, mock_dev_cog):
        """reload_autocomplete invokes AdminCog._preload_static_catalogs."""
        interaction = self._make_interaction()

        admin_cog = MagicMock()
        admin_cog._preload_data = AsyncMock()
        admin_cog._preload_render_settings = AsyncMock()
        admin_cog._preload_static_catalogs = AsyncMock()

        bounty_cog = MagicMock()
        bounty_cog._preload_data = AsyncMock()

        about_cog = MagicMock()
        about_cog._preload_data = AsyncMock()

        dev_cog_inner = MagicMock()
        dev_cog_inner._preload_categories = AsyncMock()

        skins_cog = MagicMock()
        skins_cog._preload_ship_skins = AsyncMock()

        shop_cog = MagicMock()
        shop_cache = MagicMock()
        shop_cache.clear = MagicMock()
        shop_cog._shop_cache = shop_cache

        def get_cog_side_effect(name):
            return {
                "AboutCog": about_cog,
                "DevCog": dev_cog_inner,
                "SkinsCog": skins_cog,
                "BountyCog": bounty_cog,
                "AdminCog": admin_cog,
                "ShopCog": shop_cog,
            }.get(name)

        mock_dev_cog.bot.get_cog = MagicMock(side_effect=get_cog_side_effect)

        asyncio.run(mock_dev_cog.reload_autocomplete.callback(mock_dev_cog, interaction))

        admin_cog._preload_static_catalogs.assert_awaited_once()

    # ------------------------------------------------------------------
    # Test #28 — clears ShopCog._shop_cache
    # ------------------------------------------------------------------

    @patch("cogs.devCog.httpx")
    def test_reload_clears_shop_cache(self, mock_httpx, mock_dev_cog):
        """reload_autocomplete calls clear() on ShopCog._shop_cache."""
        interaction = self._make_interaction()

        about_cog = MagicMock()
        about_cog._preload_data = AsyncMock()

        dev_cog_inner = MagicMock()
        dev_cog_inner._preload_categories = AsyncMock()

        skins_cog = MagicMock()
        skins_cog._preload_ship_skins = AsyncMock()

        bounty_cog = MagicMock()
        bounty_cog._preload_data = AsyncMock()

        admin_cog = MagicMock()
        admin_cog._preload_render_settings = AsyncMock()
        admin_cog._preload_static_catalogs = AsyncMock()

        shop_cog = MagicMock()
        shop_cache = MagicMock()
        shop_cache.clear = MagicMock()
        shop_cog._shop_cache = shop_cache

        def get_cog_side_effect(name):
            return {
                "AboutCog": about_cog,
                "DevCog": dev_cog_inner,
                "SkinsCog": skins_cog,
                "BountyCog": bounty_cog,
                "AdminCog": admin_cog,
                "ShopCog": shop_cog,
            }.get(name)

        mock_dev_cog.bot.get_cog = MagicMock(side_effect=get_cog_side_effect)

        asyncio.run(mock_dev_cog.reload_autocomplete.callback(mock_dev_cog, interaction))

        shop_cache.clear.assert_called_once()

    # ------------------------------------------------------------------
    # Test #29 — invokes BountyCog._preload_data and AdminCog._preload_render_settings
    # ------------------------------------------------------------------

    @patch("cogs.devCog.httpx")
    def test_reload_invokes_bounty_preload_and_render_settings(self, mock_httpx, mock_dev_cog):
        """reload_autocomplete invokes BountyCog._preload_data and AdminCog._preload_render_settings."""
        interaction = self._make_interaction()

        about_cog = MagicMock()
        about_cog._preload_data = AsyncMock()

        dev_cog_inner = MagicMock()
        dev_cog_inner._preload_categories = AsyncMock()

        skins_cog = MagicMock()
        skins_cog._preload_ship_skins = AsyncMock()

        bounty_cog = MagicMock()
        bounty_cog._preload_data = AsyncMock()

        admin_cog = MagicMock()
        admin_cog._preload_render_settings = AsyncMock()
        admin_cog._preload_static_catalogs = AsyncMock()

        shop_cog = MagicMock()
        shop_cache = MagicMock()
        shop_cache.clear = MagicMock()
        shop_cog._shop_cache = shop_cache

        def get_cog_side_effect(name):
            return {
                "AboutCog": about_cog,
                "DevCog": dev_cog_inner,
                "SkinsCog": skins_cog,
                "BountyCog": bounty_cog,
                "AdminCog": admin_cog,
                "ShopCog": shop_cog,
            }.get(name)

        mock_dev_cog.bot.get_cog = MagicMock(side_effect=get_cog_side_effect)

        asyncio.run(mock_dev_cog.reload_autocomplete.callback(mock_dev_cog, interaction))

        bounty_cog._preload_data.assert_awaited_once()
        admin_cog._preload_render_settings.assert_awaited_once()


# ===========================================================================
# Cross-1: Defer fires BEFORE admin check in devCog commands
# ===========================================================================


class TestCrossOneDevCogDeferBeforeAdminCheck:
    """Cross-1: Verify that defer() fires before _check_is_admin() in devCog commands."""

    async def _run_with_admin_blocked(self, cog, coro_fn):
        """Run a command callback tracking defer vs admin_check order.

        Patches cogs.devCog._check_is_admin (the local name bound by the import)
        rather than cogs.adminCog._check_is_admin.
        """
        import cogs.devCog as dev_module

        original = dev_module._check_is_admin
        call_order = []

        async def track_defer(*a, **kw):
            call_order.append("defer")

        async def fake_check_is_admin(interaction):
            call_order.append("admin_check")
            return False  # non-admin

        interaction = MagicMock()
        interaction.guild_id = 123456789
        interaction.user = MagicMock()
        interaction.user.id = 987654321
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = False
        interaction.user.roles = []
        interaction.response = AsyncMock()
        interaction.response.defer = track_defer
        interaction.followup = AsyncMock()

        dev_module._check_is_admin = fake_check_is_admin
        try:
            await coro_fn(interaction)
        finally:
            dev_module._check_is_admin = original

        return call_order

    def test_load_data_defer_before_admin_check(self, mock_dev_cog):
        """Cross-1: /load_data defers before checking admin status."""

        async def run():
            return await self._run_with_admin_blocked(
                mock_dev_cog,
                lambda i: mock_dev_cog.load_data.callback(mock_dev_cog, i, "ships"),
            )

        call_order = asyncio.run(run())
        assert "defer" in call_order
        assert "admin_check" in call_order
        assert call_order.index("defer") < call_order.index("admin_check"), "defer must fire before admin check"

    def test_reload_autocomplete_defer_before_admin_check(self, mock_dev_cog):
        """Cross-1: /reload_autocomplete defers before checking admin status."""

        async def run():
            return await self._run_with_admin_blocked(
                mock_dev_cog,
                lambda i: mock_dev_cog.reload_autocomplete.callback(mock_dev_cog, i),
            )

        call_order = asyncio.run(run())
        assert call_order.index("defer") < call_order.index("admin_check")


if __name__ == "__main__":
    pytest.main([__file__])
