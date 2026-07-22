import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

# Import discord_mock_utils for consistent mock patterns
from tests.mocks.discord_mock_utils import DiscordMockUtils

# Setup mock shared.bblogger module
_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")

# Track the module-level logger
_unused_module_logger = None


def _make_mock_logger(*_args, **_kwargs):
    """Return a MagicMock that already has common log-level methods."""
    global _unused_module_logger
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    _unused_module_logger = logger
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


@pytest.fixture(scope="module")
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


_API_BASE = "http://bot-core:8000/api/v1"


def _with_real_client(cog, request):
    """Replace cog.http_client with a real httpx.AsyncClient for respx interception.

    Registers a pytest finalizer to close the client after the test so no
    httpx.AsyncClient instances are leaked between tests.
    """
    cog.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    request.addfinalizer(lambda: asyncio.run(cog.http_client.aclose()))
    return cog


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
def mock_dev_cog(mock_bot, monkeypatch):
    """Create a mock devCog instance.

    Patches _check_is_super_admin to return True so that happy-path tests
    exercise command logic without being blocked by the super-admin gate.
    Tests that need to exercise the rejection path patch
    _check_is_super_admin directly via cogs.devCog module attribute.
    """
    _evict_discord_modules()
    import cogs.devCog as _dev_module

    cog = _dev_module.DevCog(mock_bot)

    # Bypass the super-admin gate for all tests in this file that don't
    # explicitly test the gate.  Tests that need the gate patched for
    # rejection do their own monkeypatching via cogs.devCog._check_is_super_admin.
    async def _always_super_admin(_interaction):
        return True

    monkeypatch.setattr(_dev_module, "_check_is_super_admin", _always_super_admin)
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

        with (
            respx.mock(assert_all_called=False) as mock_router,
            patch("cogs.devCog.asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_router.get(self._CATEGORIES_URL).mock(
                return_value=httpx.Response(503, json={"detail": "Service Unavailable"})
            )
            asyncio.run(mock_dev_cog._preload_categories())

        # On HTTP error, _categories remains empty after all retries exhausted
        assert mock_dev_cog._categories == []

    def test_preload_categories_network_error_leaves_empty(self, mock_dev_cog, request):
        """_preload_categories leaves _categories empty on network-level error."""
        import httpx
        import respx

        self._with_real_client(mock_dev_cog, request)
        mock_dev_cog.bot.wait_until_ready = AsyncMock()

        with (
            respx.mock(assert_all_called=False) as mock_router,
            patch("cogs.devCog.asyncio.sleep", new_callable=AsyncMock),
        ):
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
    """Tests for load_data command.

    Migrated to respx: no `httpx` module patch — the cog's real ``httpx.HTTPStatusError``
    is exercised so error paths that key off the exception type actually fire.
    """

    def test_load_data_single_category_success(self, mock_dev_cog, request):
        """load_data should load single category successfully, hitting POST /data/{category}."""
        _with_real_client(mock_dev_cog, request)
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        with respx.mock(assert_all_called=True) as mock_router:
            route = mock_router.post(f"{_API_BASE}/data/ships").mock(
                return_value=httpx.Response(200, json=["file1", "file2"])
            )
            asyncio.run(mock_dev_cog.load_data.callback(mock_dev_cog, interaction, "ships"))

        assert route.called
        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once_with("✅ Data load complete for **ships**: 2 files processed.")

    def test_load_data_all_categories_success(self, mock_dev_cog, request):
        """load_data should load all categories when 'All' is selected, hitting one POST per category."""
        _with_real_client(mock_dev_cog, request)
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        mock_dev_cog._categories = ["ships", "modules"]

        with respx.mock(assert_all_called=True) as mock_router:
            ships_route = mock_router.post(f"{_API_BASE}/data/ships").mock(
                return_value=httpx.Response(200, json=["ship1", "ship2"])
            )
            modules_route = mock_router.post(f"{_API_BASE}/data/modules").mock(
                return_value=httpx.Response(200, json=["module1"])
            )
            asyncio.run(mock_dev_cog.load_data.callback(mock_dev_cog, interaction, "All"))

        assert ships_route.called
        assert modules_route.called
        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once()
        body = interaction.followup.send.call_args[0][0]
        assert "Total files: 3" in body
        assert "ships: 2 files" in body
        assert "modules: 1 files" in body

    def test_load_data_api_error(self, mock_dev_cog, request):
        """load_data must actually raise httpx.HTTPStatusError on a 500 and take the ❌ branch.

        Regression guard for the prod-shaped bug: previously the whole `httpx` module was
        patched to a MagicMock, so `AsyncMock(side_effect=<MagicMock instance>)` treated the
        "error" as callable and *returned* it instead of raising — the success branch ran and
        the test passed even with the `except httpx.HTTPStatusError` handler deleted. respx
        returns a real 500 so `resp.raise_for_status()` genuinely raises.
        """
        _with_real_client(mock_dev_cog, request)
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_API_BASE}/data/ships").mock(
                return_value=httpx.Response(500, json={"detail": "Internal Server Error"})
            )
            asyncio.run(mock_dev_cog.load_data.callback(mock_dev_cog, interaction, "ships"))

        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once()
        call_args, call_kwargs = interaction.followup.send.call_args
        assert call_args[0].startswith("❌ "), f"expected the error branch, got: {call_args[0]!r}"
        assert call_kwargs.get("ephemeral") is True


class TestReloadAutocompleteCommand:
    """Tests for reload_autocomplete command."""

    def test_reload_autocomplete_success(self, mock_dev_cog):
        """reload_autocomplete should reload cog methods successfully.

        Uses a real AutocompleteCache for the cache-clear targets so the assertion is on
        actual cache state (empty after reload) rather than "a mock method was called" —
        a mock that was never wired to real cache semantics would still pass the old assert.
        """
        from cogs._shared.autocomplete_cache import AutocompleteCache

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
        about_cog._shop_cache = AutocompleteCache(name="test-shop")
        about_cog._shop_cache.set("bronze", [{"id": 1}])
        mock_dev_cog.bot.get_cog.return_value = about_cog

        # Call command via callback
        asyncio.run(mock_dev_cog.reload_autocomplete.callback(mock_dev_cog, interaction))

        # Verify behavior — Phase 3: backend-sourced static caches now clear-and-self-heal,
        # so the only remaining explicit preloads are the plain in-code lists:
        # DevCog._preload_categories and AdminCog._preload_render_settings.
        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once()
        assert about_cog._preload_categories.await_count >= 1
        assert about_cog._preload_render_settings.await_count >= 1
        # The static catalog is actually empty (self-heal on next keystroke) — real cache state.
        assert about_cog._shop_cache.size == 0

    def test_reload_autocomplete_with_errors(self, mock_dev_cog):
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

    def test_reload_clears_systems_cache_now_that_it_self_heals(self, mock_dev_cog):
        """Phase 3 (D-010 fix): the carve-out is GONE — _systems_cache IS now cleared.

        Now that _systems_cache has a refresh_fn, /reload_autocomplete can clear it
        uniformly like any other cache; the next /check keystroke cold-fills it. This
        is the inverse of the old D-010 carve-out behaviour (which left it untouched).
        Uses real AutocompleteCache instances so the assertion is on actual emptied state.
        """
        from cogs._shared.autocomplete_cache import AutocompleteCache

        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        cog = MagicMock()
        cog._preload_categories = AsyncMock()
        cog._preload_render_settings = AsyncMock()
        # Distinct real caches, pre-filled, so we can assert exactly which ones got emptied.
        cog._systems_cache = AutocompleteCache(name="test-systems")
        cog._systems_cache.set("sol", {"name": "Sol"})
        cog._bounty_cache = AutocompleteCache(name="test-bounty")
        cog._bounty_cache.set(123456789, [{"id": 1}])
        mock_dev_cog.bot.get_cog.return_value = cog

        asyncio.run(mock_dev_cog.reload_autocomplete.callback(mock_dev_cog, interaction))

        # The static systems catalog IS now cleared (self-heals via refresh_fn).
        assert cog._systems_cache.size == 0
        assert cog._bounty_cache.size == 0


class TestErrorHandling:
    """Tests for error handling in devCog."""

    def test_load_data_connection_error(self, mock_dev_cog, request):
        """load_data should handle a network-level error (not an HTTP status) gracefully.

        Uses a real httpx.ConnectError (via respx side_effect) rather than a generic
        Exception — this is what the real client actually raises when the connection
        fails, and it correctly falls through to the generic `except Exception` branch
        (⚠️) rather than the httpx.HTTPStatusError branch (❌).
        """
        _with_real_client(mock_dev_cog, request)
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_API_BASE}/data/ships").mock(side_effect=httpx.ConnectError("Connection failed"))
            asyncio.run(mock_dev_cog.load_data.callback(mock_dev_cog, interaction, "ships"))

        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once()
        call_args, call_kwargs = interaction.followup.send.call_args
        assert call_args[0].startswith("⚠️ "), f"expected the generic-error branch, got: {call_args[0]!r}"
        assert "Connection failed" in call_args[0]
        assert call_kwargs.get("ephemeral") is True


class TestCogUnload:
    """Tests for cog unload functionality."""

    def test_cog_unload_success(self, mock_dev_cog):
        """cog_unload should close http_client successfully."""
        mock_dev_cog.http_client.aclose = AsyncMock()
        asyncio.run(mock_dev_cog.cog_unload())
        mock_dev_cog.http_client.aclose.assert_called_once()


class TestLoadDataAllCategoriesWithErrors:
    """Tests for load_data 'All' path with errors."""

    def test_load_data_all_with_partial_errors(self, mock_dev_cog, request):
        """load_data should handle partial failures when loading all categories."""
        _with_real_client(mock_dev_cog, request)
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        mock_dev_cog._categories = ["ships", "modules"]

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_API_BASE}/data/ships").mock(return_value=httpx.Response(200, json=["ship1", "ship2"]))
            mock_router.post(f"{_API_BASE}/data/modules").mock(
                return_value=httpx.Response(500, json={"detail": "Failed"})
            )
            asyncio.run(mock_dev_cog.load_data.callback(mock_dev_cog, interaction, "All"))

        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once()
        body = interaction.followup.send.call_args[0][0]
        # Real behavior: successful category is summarized, failing category is reported
        # under "Errors:", and the header flags the error count.
        assert "ships: 2 files" in body
        assert "Errors in 1 categories" in body
        assert "modules:" in body

    def test_load_data_all_body_truncation(self, mock_dev_cog, request):
        """load_data should truncate very long response body for 'All'."""
        _with_real_client(mock_dev_cog, request)
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Create many categories to trigger truncation
        categories = [f"category_{i}" for i in range(50)]
        mock_dev_cog._categories = categories

        with respx.mock(assert_all_called=True) as mock_router:
            for cat in categories:
                mock_router.post(f"{_API_BASE}/data/{cat}").mock(
                    return_value=httpx.Response(200, json=[f"file_{j}" for j in range(10)])
                )
            asyncio.run(mock_dev_cog.load_data.callback(mock_dev_cog, interaction, "All"))

        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once()
        body = interaction.followup.send.call_args[0][0]
        assert "... (truncated)" in body
        assert "Total files: 500" in body


class TestLoadDataSingleCategoryErrors:
    """Tests for load_data single category error paths."""

    def test_load_data_http_status_error(self, mock_dev_cog, request):
        """load_data should handle HTTPStatusError for single category."""
        _with_real_client(mock_dev_cog, request)
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_API_BASE}/data/ships").mock(
                return_value=httpx.Response(500, json={"detail": "Server error"})
            )
            asyncio.run(mock_dev_cog.load_data.callback(mock_dev_cog, interaction, "ships"))

        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once()
        call_args, call_kwargs = interaction.followup.send.call_args
        assert call_args[0].startswith("❌ ")
        assert call_kwargs.get("ephemeral") is True


class TestReloadAutocompleteEdgeCases:
    """Tests for reload_autocomplete edge cases."""

    def test_reload_autocomplete_cog_not_found(self, mock_dev_cog):
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

    def test_reload_autocomplete_method_not_found(self, mock_dev_cog):
        """reload_autocomplete should handle missing methods gracefully.

        Both preload-method targets (DevCog._preload_categories,
        AdminCog._preload_render_settings) are explicitly set to None so
        ``getattr(cog, method_name, None)`` genuinely resolves to None and the
        cog exercises the "no method" branch, rather than incidentally landing
        there because a MagicMock auto-attribute isn't awaitable.
        """
        # Mock interaction
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Mock cog without either expected preload method
        mock_cog = MagicMock()
        mock_cog._preload_categories = None
        mock_cog._preload_render_settings = None
        mock_dev_cog.bot.get_cog.return_value = mock_cog

        # Call command via callback
        asyncio.run(mock_dev_cog.reload_autocomplete.callback(mock_dev_cog, interaction))

        # Verify behavior - should report method not found for both targets
        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "no method _preload_categories" in call_args
        assert "no method _preload_render_settings" in call_args


class TestCogSetup:
    """Tests for cog setup function."""

    def test_setup_function(self, mock_bot):
        """setup function should add devCog to bot."""
        import cogs.devCog as dev_module

        # Make add_cog awaitable
        mock_bot.add_cog = AsyncMock()

        asyncio.run(dev_module.setup(mock_bot))

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

    def test_reload_clears_admin_static_catalogs(self, mock_dev_cog):
        """Phase 3: AdminCog item/ship catalogs are now CLEARED (self-heal via refresh_fn)
        instead of being re-driven by _preload_static_catalogs from /reload_autocomplete.

        Uses real AutocompleteCache instances so the assertion reflects actual emptied
        cache state rather than "a mock method was called".
        """
        from cogs._shared.autocomplete_cache import AutocompleteCache

        interaction = self._make_interaction()

        admin_cog = MagicMock()
        admin_cog._preload_render_settings = AsyncMock()
        admin_cog._item_catalog = AutocompleteCache(name="test-item-catalog")
        admin_cog._item_catalog.set("laser", {"id": 1})
        admin_cog._ship_catalog = AutocompleteCache(name="test-ship-catalog")
        admin_cog._ship_catalog.set("scout", {"id": 2})
        admin_cog._admin_pending_duel_cache = AutocompleteCache(name="test-pending-duel")
        admin_cog._admin_pending_duel_cache.set(1, {"id": 3})

        bounty_cog = MagicMock()

        about_cog = MagicMock()

        dev_cog_inner = MagicMock()
        dev_cog_inner._preload_categories = AsyncMock()

        skins_cog = MagicMock()

        shop_cog = MagicMock()
        shop_cog._shop_cache = AutocompleteCache(name="test-shop")

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

        assert admin_cog._item_catalog.size == 0
        assert admin_cog._ship_catalog.size == 0

    # ------------------------------------------------------------------
    # Test #28 — clears ShopCog._shop_cache
    # ------------------------------------------------------------------

    def test_reload_clears_shop_cache(self, mock_dev_cog):
        """reload_autocomplete empties the real ShopCog._shop_cache."""
        from cogs._shared.autocomplete_cache import AutocompleteCache

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
        shop_cog._shop_cache = AutocompleteCache(name="test-shop")
        shop_cog._shop_cache.set("bronze", [{"id": 1}])

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

        assert shop_cog._shop_cache.size == 0

    # ------------------------------------------------------------------
    # Test #29 — invokes BountyCog._preload_data and AdminCog._preload_render_settings
    # ------------------------------------------------------------------

    def test_reload_clears_bounty_systems_and_preloads_render_settings(self, mock_dev_cog):
        """Phase 3: BountyCog._systems_cache is now CLEARED (self-heal), while the plain
        in-code AdminCog._preload_render_settings list is still explicitly preloaded.
        """
        from cogs._shared.autocomplete_cache import AutocompleteCache

        interaction = self._make_interaction()

        about_cog = MagicMock()

        dev_cog_inner = MagicMock()
        dev_cog_inner._preload_categories = AsyncMock()

        skins_cog = MagicMock()

        bounty_cog = MagicMock()
        bounty_cog._systems_cache = AutocompleteCache(name="test-systems")
        bounty_cog._systems_cache.set("sol", {"name": "Sol"})
        bounty_cog._bounty_cache = AutocompleteCache(name="test-bounty")
        bounty_cog._bounty_cache.set(123456789, [{"id": 1}])

        admin_cog = MagicMock()
        admin_cog._preload_render_settings = AsyncMock()

        shop_cog = MagicMock()
        shop_cog._shop_cache = AutocompleteCache(name="test-shop")

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

        assert bounty_cog._systems_cache.size == 0
        admin_cog._preload_render_settings.assert_awaited_once()


# ===========================================================================
# Cross-1: Defer fires BEFORE admin check in devCog commands
# ===========================================================================


class TestCrossOneDevCogDeferBeforeAdminCheck:
    """Cross-1: Verify that defer() fires before _check_is_super_admin() in devCog commands."""

    async def _run_with_admin_blocked(self, cog, coro_fn):
        """Run a command callback tracking defer vs admin_check order.

        Patches cogs.devCog._check_is_super_admin (the local name bound by the import)
        rather than cogs.adminCog._check_is_super_admin.
        """
        import cogs.devCog as dev_module

        original = dev_module._check_is_super_admin
        call_order = []

        async def track_defer(*a, **kw):
            call_order.append("defer")

        async def fake_check_is_super_admin(interaction):
            call_order.append("admin_check")
            return False  # non-super-admin

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

        dev_module._check_is_super_admin = fake_check_is_super_admin
        try:
            await coro_fn(interaction)
        finally:
            dev_module._check_is_super_admin = original

        return call_order

    def test_load_data_defer_before_admin_check(self, mock_dev_cog):
        """Cross-1: /load_data defers before checking super-admin status."""

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
        """Cross-1: /reload_autocomplete defers before checking super-admin status."""

        async def run():
            return await self._run_with_admin_blocked(
                mock_dev_cog,
                lambda i: mock_dev_cog.reload_autocomplete.callback(mock_dev_cog, i),
            )

        call_order = asyncio.run(run())
        assert call_order.index("defer") < call_order.index("admin_check")


# ===========================================================================
# TestSuperAdminGateDevCog — super-admin permission gate in devCog commands
# ===========================================================================


class TestSuperAdminGateDevCog:
    """Tests verifying that devCog commands use _check_is_super_admin (not _check_is_admin)."""

    def test_load_data_rejects_discord_admin_not_in_developers(self, mock_dev_cog):
        """load_data rejects a Discord Administrator who is NOT in DEVELOPERS."""
        import cogs.devCog as dev_module

        interaction = MagicMock()
        interaction.guild_id = 123456789
        interaction.user = MagicMock()
        interaction.user.id = 88888  # not in DEVELOPERS
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = True  # Discord admin, but not super-admin
        interaction.response = AsyncMock()
        interaction.followup = AsyncMock()

        original = dev_module._check_is_super_admin

        async def fake_not_super_admin(inter):
            return False  # even Discord admin is rejected by super-admin gate

        dev_module._check_is_super_admin = fake_not_super_admin
        try:
            asyncio.run(mock_dev_cog.load_data.callback(mock_dev_cog, interaction, "ships"))
        finally:
            dev_module._check_is_super_admin = original

        interaction.followup.send.assert_awaited_once()
        msg = str(interaction.followup.send.call_args)
        assert "super-admin" in msg.lower() or "privilege" in msg.lower()

    def test_reload_autocomplete_rejects_discord_admin_not_in_developers(self, mock_dev_cog):
        """reload_autocomplete rejects a Discord Administrator who is NOT in DEVELOPERS."""
        import cogs.devCog as dev_module

        interaction = MagicMock()
        interaction.guild_id = 123456789
        interaction.user = MagicMock()
        interaction.user.id = 88888
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = True
        interaction.response = AsyncMock()
        interaction.followup = AsyncMock()

        original = dev_module._check_is_super_admin

        async def fake_not_super_admin(inter):
            return False

        dev_module._check_is_super_admin = fake_not_super_admin
        try:
            asyncio.run(mock_dev_cog.reload_autocomplete.callback(mock_dev_cog, interaction))
        finally:
            dev_module._check_is_super_admin = original

        interaction.followup.send.assert_awaited_once()
        msg = str(interaction.followup.send.call_args)
        assert "super-admin" in msg.lower() or "privilege" in msg.lower()


if __name__ == "__main__":
    pytest.main([__file__])
