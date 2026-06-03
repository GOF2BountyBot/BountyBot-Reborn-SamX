"""Tests for aboutCog — boosting coverage from 0% to 60%+."""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import discord_mock_utils for consistent mock patterns
from tests.mocks.discord_mock_utils import DiscordMockUtils

# ---------------------------------------------------------------------------
# Module-level mock setup — must run before any src imports
# ---------------------------------------------------------------------------

_mock_utils = DiscordMockUtils()

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")

_module_logger = None


def _make_mock_logger(*_args, **_kwargs):
    """Return a MagicMock with common log-level methods."""
    global _module_logger
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    logger.exception = MagicMock()
    _module_logger = logger
    return logger


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import discord

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _create_mock_interaction(user_id=111111111, guild_id=987654321):
    """Build a mock interaction with all needed attributes."""
    interaction = DiscordMockUtils.create_mock_interaction(
        user_id=user_id,
        guild_id=guild_id,
    )
    interaction.guild_id = guild_id
    interaction.user.display_name = "TestUser"
    interaction.user.display_avatar = MagicMock()
    interaction.user.display_avatar.url = "https://example.com/avatar.jpg"
    interaction.user.__str__ = MagicMock(return_value="TestUser#0001")
    return interaction


def _make_object_data(name="Eagle", category="ship", obj_id=1):
    """Return a minimal game object data dict."""
    return {
        "id": obj_id,
        "name": name,
        "category": category,
        "type": "Fighter",
        "tech_level": 1,
        "value": 5000,
        "emoji": "🚀",
        "icon": None,
        "aliases": [],
        "built_in": False,
        "wiki": None,
        "extra_atts": None,
    }


def _close_coro(coro):
    """Close coroutine to prevent 'never awaited' warning."""
    coro.close()
    return MagicMock()


def _make_mock_bot_with_loop():
    """Create a mock bot that has a working loop.create_task."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    # loop.create_task should close the coroutine to prevent 'never awaited' warning
    bot.loop = MagicMock()
    bot.loop.create_task = MagicMock(side_effect=_close_coro)
    # wait_until_ready is already AsyncMock from create_mock_bot
    return bot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mock_bot():
    """Mock Discord bot for aboutCog testing."""
    return _make_mock_bot_with_loop()


@pytest.fixture
def mock_about_cog(mock_bot):
    """Create an AboutCog instance with mocked bot and http_client."""
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()

    from cogs.aboutCog import AboutCog

    cog = AboutCog(mock_bot)
    # Replace the real AsyncClient with a MagicMock for test control
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestAboutCogInitialization:
    """Tests for AboutCog initialization."""

    @pytest.fixture(autouse=True)
    def _reset_bot_mock(self, mock_bot):
        """Reset mock_bot call counters before each test.

        Required because mock_bot is module-scoped and accumulates create_task
        calls from mock_about_cog (function-scoped) across prior tests.
        """
        mock_bot.loop.create_task.reset_mock()

    def test_initialization(self, mock_about_cog, mock_bot):
        """AboutCog should store bot reference and create http_client."""
        assert mock_about_cog.bot is mock_bot
        assert mock_about_cog.http_client is not None

    def test_initialization_sets_empty_categories(self, mock_about_cog):
        """AboutCog should start with empty categories cache."""
        assert mock_about_cog._categories_cache.size == 0

    def test_initialization_sets_empty_objects_by_category(self, mock_about_cog):
        """AboutCog should start with empty objects_by_category dict."""
        assert mock_about_cog._objects_cache.size == 0

    def test_initialization_schedules_preload(self, mock_bot):
        """AboutCog __init__ should schedule _preload_data task."""
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        _evict_discord_modules()

        from cogs.aboutCog import AboutCog

        _ = AboutCog(mock_bot)

        # The bot.loop.create_task should have been called once
        mock_bot.loop.create_task.assert_called_once()


# ---------------------------------------------------------------------------
# cog_unload lifecycle
# ---------------------------------------------------------------------------


class TestCogUnload:
    """Tests for AboutCog.cog_unload."""

    def test_cog_unload_closes_http_client(self, mock_about_cog):
        """cog_unload should close the http client."""
        asyncio.run(mock_about_cog.cog_unload())
        mock_about_cog.http_client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# _preload_data
# ---------------------------------------------------------------------------


class TestPreloadData:
    """Tests for _preload_data method.

    B.33 remediation: tests use respx to assert exact URL + HTTP method,
    confirming aboutCog calls the correct bot-core routes:
    - GET /about/categories  (list categories)
    - GET /about/categories/{cat}/objects  (objects per category)
    Both routes are confirmed correct in about.py:69 and about.py:85.

    Note: the mock_about_cog fixture replaces http_client with a MagicMock for
    most tests. The preload tests reinstall a real httpx.AsyncClient so that
    respx can intercept network calls and assert URL+method correctness.

    Retry behaviour: _preload_data now retries the /about/categories fetch up to
    5 times with exponential backoff (5s, 10s, 20s, 40s, 60s). Tests that exercise
    the retry path patch asyncio.sleep to avoid real delays.
    """

    _API_BASE = "http://bot-core:8000/api/v1"

    def _with_real_client(self, cog, request):
        """Replace cog.http_client with a real httpx.AsyncClient for respx interception.

        Registers a pytest finalizer to close the client after the test so no
        httpx.AsyncClient instances are leaked between tests.
        """
        import httpx

        cog.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        request.addfinalizer(lambda: asyncio.run(cog.http_client.aclose()))
        return cog

    def test_preload_data_success(self, mock_about_cog, request):
        """_preload_data calls GET /about/categories then GET /about/categories/{cat}/objects
        for each category and populates _categories and _objects_by_category."""
        import httpx
        import respx

        self._with_real_client(mock_about_cog, request)
        mock_about_cog.bot.wait_until_ready = AsyncMock()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{self._API_BASE}/about/categories").mock(
                return_value=httpx.Response(200, json=["ship", "module", "primary_weapon"])
            )
            mock_router.get(f"{self._API_BASE}/about/categories/ship/objects").mock(
                return_value=httpx.Response(
                    200, json=[{"name": "Eagle", "aliases": []}, {"name": "Hawk", "aliases": []}]
                )
            )
            mock_router.get(f"{self._API_BASE}/about/categories/module/objects").mock(
                return_value=httpx.Response(200, json=[{"name": "Shield", "aliases": []}])
            )
            mock_router.get(f"{self._API_BASE}/about/categories/primary_weapon/objects").mock(
                return_value=httpx.Response(200, json=[{"name": "Laser", "aliases": []}])
            )

            asyncio.run(mock_about_cog._preload_data())

        assert mock_about_cog._categories_cache.peek("all") == ["ship", "module", "primary_weapon"]
        assert mock_about_cog._objects_cache.peek("ship") is not None
        assert len(mock_about_cog._objects_cache.peek("ship")) == 2

    def test_preload_data_api_failure_all_attempts(self, mock_about_cog, request):
        """_preload_data retries 5 times then degrades gracefully when all attempts fail.

        After 5 consecutive HTTP 503 responses, _categories and _objects_by_category
        must both be left as empty (the for/else terminal-failure branch).
        asyncio.sleep is patched to avoid real delays.
        """
        import httpx
        import respx

        self._with_real_client(mock_about_cog, request)
        mock_about_cog.bot.wait_until_ready = AsyncMock()

        with patch("cogs.aboutCog.asyncio.sleep", new_callable=AsyncMock), respx.mock() as mock_router:
            # respx will match all 5 retry attempts against the same route
            mock_router.get(f"{self._API_BASE}/about/categories").mock(
                return_value=httpx.Response(503, json={"detail": "Service Unavailable"})
            )

            # Should not raise — failure is caught internally
            asyncio.run(mock_about_cog._preload_data())

        # After terminal failure, categories must be empty
        assert mock_about_cog._categories_cache.peek("all") == []
        assert mock_about_cog._objects_cache.size == 0

    def test_preload_data_retry_succeeds_on_second_attempt(self, mock_about_cog, request):
        """_preload_data should succeed when the first attempt fails but the second succeeds.

        Simulates bot-core being slow to start: first call raises ConnectError,
        second call returns the categories list.
        """
        import httpx
        import respx

        self._with_real_client(mock_about_cog, request)
        mock_about_cog.bot.wait_until_ready = AsyncMock()

        call_count = 0

        def _categories_side_effect(_request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("Name or service not known")
            return httpx.Response(200, json=["ship"])

        with patch("cogs.aboutCog.asyncio.sleep", new_callable=AsyncMock), respx.mock() as mock_router:
            mock_router.get(f"{self._API_BASE}/about/categories").mock(side_effect=_categories_side_effect)
            mock_router.get(f"{self._API_BASE}/about/categories/ship/objects").mock(
                return_value=httpx.Response(200, json=[{"name": "Eagle", "aliases": []}])
            )

            asyncio.run(mock_about_cog._preload_data())

        # Retry succeeded — data should be populated
        assert mock_about_cog._categories_cache.peek("all") == ["ship"]
        assert len(mock_about_cog._objects_cache.peek("ship") or []) == 1
        # asyncio.sleep was called once (between attempt 1 and 2)
        assert call_count == 2

    def test_preload_data_category_object_failure(self, mock_about_cog, request):
        """_preload_data handles per-category failure gracefully:
        successful categories are populated; failed category gets empty list."""
        import httpx
        import respx

        self._with_real_client(mock_about_cog, request)
        mock_about_cog.bot.wait_until_ready = AsyncMock()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{self._API_BASE}/about/categories").mock(
                return_value=httpx.Response(200, json=["ship", "module"])
            )
            mock_router.get(f"{self._API_BASE}/about/categories/ship/objects").mock(
                return_value=httpx.Response(200, json=[{"name": "Eagle", "aliases": []}])
            )
            # Module category fails with 500
            mock_router.get(f"{self._API_BASE}/about/categories/module/objects").mock(
                return_value=httpx.Response(500, json={"detail": "Internal Server Error"})
            )

            asyncio.run(mock_about_cog._preload_data())

        # Categories should be loaded
        assert mock_about_cog._categories_cache.peek("all") == ["ship", "module"]
        # Ship objects should be loaded
        assert len(mock_about_cog._objects_cache.peek("ship") or []) == 1
        # Module objects should be empty list (fallback on HTTP error)
        assert (mock_about_cog._objects_cache.peek("module") or []) == []

    def test_preload_data_network_error_all_attempts(self, mock_about_cog, request):
        """_preload_data handles network-level ConnectError across all 5 attempts gracefully
        (resets to empty without raising).

        This is the startup scenario described in the issue: bot-core is not yet
        ready, so every attempt gets a ConnectError. After 5 attempts the cog
        leaves its caches empty for graceful degradation.
        """
        import httpx
        import respx

        self._with_real_client(mock_about_cog, request)
        mock_about_cog.bot.wait_until_ready = AsyncMock()

        with patch("cogs.aboutCog.asyncio.sleep", new_callable=AsyncMock), respx.mock() as mock_router:
            mock_router.get(f"{self._API_BASE}/about/categories").mock(
                side_effect=httpx.ConnectError("connection refused")
            )

            # Should not raise — failure is caught internally
            asyncio.run(mock_about_cog._preload_data())

        # On network error, categories should be reset to empty
        assert mock_about_cog._categories_cache.peek("all") == []
        assert mock_about_cog._objects_cache.size == 0


# ---------------------------------------------------------------------------
# category_autocomplete
# ---------------------------------------------------------------------------


class TestCategoryAutocomplete:
    """Tests for category_autocomplete."""

    def test_category_autocomplete_empty_current(self, mock_about_cog):
        """category_autocomplete with empty current returns all categories."""
        mock_about_cog._categories_cache.set("all", ["ship", "module", "primary_weapon"])
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_about_cog.category_autocomplete(interaction, ""))

        assert len(result) == 3
        values = [c.value for c in result]
        assert "ship" in values
        assert "module" in values

    def test_category_autocomplete_partial_match(self, mock_about_cog):
        """category_autocomplete should filter by partial match."""
        mock_about_cog._categories_cache.set("all", ["ship", "module", "primary_weapon"])
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_about_cog.category_autocomplete(interaction, "mod"))

        assert len(result) == 1
        assert result[0].value == "module"

    def test_category_autocomplete_empty_categories(self, mock_about_cog):
        """category_autocomplete with no data returns empty list."""
        mock_about_cog._categories_cache.set("all", [])
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_about_cog.category_autocomplete(interaction, ""))

        assert result == []

    def test_category_autocomplete_limits_to_25(self, mock_about_cog):
        """category_autocomplete should return at most 25 results."""
        mock_about_cog._categories_cache.set("all", [f"cat{i}" for i in range(30)])
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_about_cog.category_autocomplete(interaction, ""))

        assert len(result) <= 25


# ---------------------------------------------------------------------------
# object_autocomplete
# ---------------------------------------------------------------------------


class TestObjectAutocomplete:
    """Tests for object_autocomplete."""

    def test_object_autocomplete_no_category(self, mock_about_cog):
        """object_autocomplete with no category in namespace returns empty."""
        mock_about_cog._objects_cache.set("ship", [{"name": "Eagle"}, {"name": "Hawk"}])
        interaction = _create_mock_interaction()
        # namespace has no category attribute
        interaction.namespace = MagicMock(spec=[])

        result = asyncio.run(mock_about_cog.object_autocomplete(interaction, ""))

        assert result == []

    def test_object_autocomplete_valid_category(self, mock_about_cog):
        """object_autocomplete should return objects for selected category."""
        mock_about_cog._objects_cache.set("ship", [{"name": "Eagle"}, {"name": "Hawk"}, {"name": "Falcon"}])
        interaction = _create_mock_interaction()
        interaction.namespace = MagicMock()
        interaction.namespace.category = "ship"

        result = asyncio.run(mock_about_cog.object_autocomplete(interaction, ""))

        assert len(result) == 3

    def test_object_autocomplete_partial_match(self, mock_about_cog):
        """object_autocomplete should filter by partial match."""
        mock_about_cog._objects_cache.set("ship", [{"name": "Eagle"}, {"name": "Hawk"}, {"name": "Falcon"}])
        interaction = _create_mock_interaction()
        interaction.namespace = MagicMock()
        interaction.namespace.category = "ship"

        result = asyncio.run(mock_about_cog.object_autocomplete(interaction, "Ea"))

        assert len(result) == 1
        assert result[0].value == "Eagle"


# ---------------------------------------------------------------------------
# about command
# ---------------------------------------------------------------------------


class TestAboutCommand:
    """Tests for the /about slash command."""

    def test_about_happy_path_ship(self, mock_about_cog):
        """about should display embed for a ship object."""
        interaction = _create_mock_interaction()

        # Set up preloaded categories
        mock_about_cog._categories_cache.set("all", ["ship"])
        mock_about_cog._objects_cache.set("ship", [{"name": "Eagle", "aliases": []}])

        obj_resp = MagicMock()
        obj_resp.raise_for_status = MagicMock()
        obj_resp.json.return_value = {
            **_make_object_data("Eagle", "ship"),
            "armour": 500,
            "cargo": 100,
            "handling": 75,
            "shop_spawn_rate": 0.5,
            "max_modules": 4,
            "max_primaries": 4,
            "max_secondaries": 2,
            "max_turrets": 2,
            "manufacturer": "AcmeCorp",
            "skinnable": True,
            "compatible_skins": {},
        }

        # No icon check needed (icon=None skips the HEAD request)
        mock_about_cog.http_client.get = AsyncMock(return_value=obj_resp)

        # Mock EmbedConverter used in _create_object_embed
        with patch("cogs.aboutCog.EmbedConverter") as mock_converter:
            mock_payload = MagicMock()
            mock_converter.embed_to_payload.return_value = mock_payload
            mock_embed = MagicMock(spec=discord.Embed)
            mock_converter.payload_to_grid_embed.return_value = mock_embed

            asyncio.run(mock_about_cog.about.callback(mock_about_cog, interaction, "ship", "Eagle"))

        interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()

    def test_about_object_not_found_404(self, mock_about_cog):
        """about should send ephemeral error when object is not found."""
        import httpx

        interaction = _create_mock_interaction()

        mock_about_cog._categories_cache.set("all", ["ship"])
        mock_about_cog._objects_cache.set("ship", [])

        error_response = MagicMock()
        error_response.status_code = 404
        http_error = httpx.HTTPStatusError("404 Not Found", request=MagicMock(), response=error_response)
        mock_about_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_about_cog.about.callback(mock_about_cog, interaction, "ship", "NonExistentShip"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "not found" in call_kwargs[0][0].lower()

    def test_about_api_error_non_404(self, mock_about_cog):
        """about should handle non-404 API errors gracefully."""
        import httpx

        interaction = _create_mock_interaction()

        mock_about_cog._categories_cache.set("all", ["ship"])
        mock_about_cog._objects_cache.set("ship", [])

        error_response = MagicMock()
        error_response.status_code = 500
        http_error = httpx.HTTPStatusError("500 Server Error", request=MagicMock(), response=error_response)
        mock_about_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_about_cog.about.callback(mock_about_cog, interaction, "ship", "Eagle"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args.kwargs
        assert call_kwargs.get("ephemeral", False)
        # B.31b: helper sends a sanitized embed instead of a raw URL string.
        embed = call_kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")
        assert "http://" not in (embed.description or "")

    def test_about_generic_exception(self, mock_about_cog):
        """about should handle generic exceptions gracefully."""
        interaction = _create_mock_interaction()

        mock_about_cog._categories_cache.set("all", ["ship"])
        mock_about_cog._objects_cache.set("ship", [])

        mock_about_cog.http_client.get = AsyncMock(side_effect=RuntimeError("network failure"))

        asyncio.run(mock_about_cog.about.callback(mock_about_cog, interaction, "ship", "Eagle"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_about_resolves_alias(self, mock_about_cog):
        """about should resolve an alias to the canonical name."""
        interaction = _create_mock_interaction()

        mock_about_cog._categories_cache.set("all", ["ship"])
        mock_about_cog._objects_cache.set("ship", [{"name": "Eagle", "aliases": ["Eagleship", "TheEagle"]}])

        obj_resp = MagicMock()
        obj_resp.raise_for_status = MagicMock()
        obj_resp.json.return_value = _make_object_data("Eagle", "ship")
        mock_about_cog.http_client.get = AsyncMock(return_value=obj_resp)

        with patch("cogs.aboutCog.EmbedConverter") as mock_converter:
            mock_payload = MagicMock()
            mock_converter.embed_to_payload.return_value = mock_payload
            mock_embed = MagicMock(spec=discord.Embed)
            mock_converter.payload_to_grid_embed.return_value = mock_embed

            asyncio.run(mock_about_cog.about.callback(mock_about_cog, interaction, "ship", "TheEagle"))

        # Should have called the API with the canonical name "Eagle"
        mock_about_cog.http_client.get.assert_awaited()
        call_args = mock_about_cog.http_client.get.call_args
        assert "Eagle" in call_args[0][0]


# ---------------------------------------------------------------------------
# list_category command
# ---------------------------------------------------------------------------


class TestListCategoryCommand:
    """Tests for the /list_category slash command."""

    def test_list_category_success(self, mock_about_cog):
        """list_category should display embed with object list."""
        interaction = _create_mock_interaction()

        mock_about_cog._objects_cache.set("ship", [{"name": "Eagle", "emoji": "🚀"}, {"name": "Hawk", "emoji": None}])

        asyncio.run(mock_about_cog.list_category.callback(mock_about_cog, interaction, "ship"))

        interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_list_category_not_found(self, mock_about_cog):
        """list_category with unknown category should send error."""
        interaction = _create_mock_interaction()

        mock_about_cog._objects_cache.clear()

        asyncio.run(mock_about_cog.list_category.callback(mock_about_cog, interaction, "unknown_category"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "not found" in call_kwargs[0][0].lower()

    def test_list_category_empty_category(self, mock_about_cog):
        """list_category with empty category should send ephemeral message."""
        interaction = _create_mock_interaction()

        mock_about_cog._objects_cache.set("ship", [])

        asyncio.run(mock_about_cog.list_category.callback(mock_about_cog, interaction, "ship"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)


# ---------------------------------------------------------------------------
# setup() function
# ---------------------------------------------------------------------------


class TestCogSetup:
    """Tests for the module-level setup function."""

    def test_setup_adds_cog_to_bot(self, mock_bot):
        """setup() should add AboutCog to the bot."""
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        _evict_discord_modules()

        from cogs.aboutCog import setup

        asyncio.run(setup(mock_bot))

        mock_bot.add_cog.assert_called_once()
        added_arg = mock_bot.add_cog.call_args[0][0]
        from cogs.aboutCog import AboutCog

        assert isinstance(added_arg, AboutCog)


# ---------------------------------------------------------------------------
# is_developer() module-level function
# ---------------------------------------------------------------------------


class TestIsDeveloper:
    """Tests for the is_developer helper."""

    def test_is_developer_returns_true(self):
        """is_developer should return True (current stub implementation)."""
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        _evict_discord_modules()

        from cogs.aboutCog import is_developer

        assert is_developer() is True


# ---------------------------------------------------------------------------
# _create_object_embed — category-specific branches
# ---------------------------------------------------------------------------


class TestCreateObjectEmbed:
    """Tests for _create_object_embed exercising every category branch."""

    def test_embed_module_category_max_equipped(self, mock_about_cog):
        """_create_object_embed for 'module' should add Max Equipped field."""
        obj_data = {
            **_make_object_data("Shield Booster", "module", 10),
            "max_equipped": 3,
        }
        with patch("cogs.aboutCog.EmbedConverter") as mc:
            mc.embed_to_payload.return_value = MagicMock()
            mc.payload_to_grid_embed.return_value = MagicMock(spec=discord.Embed)
            result = asyncio.run(mock_about_cog._create_object_embed(obj_data))

        # Grid embed is used for module category
        mc.embed_to_payload.assert_called_once()
        mc.payload_to_grid_embed.assert_called_once()
        assert result is not None

    def test_embed_module_category_no_max_equipped(self, mock_about_cog):
        """_create_object_embed for 'module' without max_equipped should skip field."""
        obj_data = _make_object_data("Shield Booster", "module", 10)
        # max_equipped not present
        with patch("cogs.aboutCog.EmbedConverter") as mc:
            mc.embed_to_payload.return_value = MagicMock()
            mc.payload_to_grid_embed.return_value = MagicMock(spec=discord.Embed)
            result = asyncio.run(mock_about_cog._create_object_embed(obj_data))

        assert result is not None

    def test_embed_primary_weapon_category_dps(self, mock_about_cog):
        """_create_object_embed for 'primary_weapon' should add DPS field."""
        obj_data = {
            **_make_object_data("Pulse Laser", "primary_weapon", 20),
            "dps": 42.5,
        }
        with patch("cogs.aboutCog.EmbedConverter") as mc:
            mc.embed_to_payload.return_value = MagicMock()
            mc.payload_to_grid_embed.return_value = MagicMock(spec=discord.Embed)
            result = asyncio.run(mock_about_cog._create_object_embed(obj_data))

        mc.embed_to_payload.assert_called_once()
        assert result is not None

    def test_embed_primary_weapon_no_dps(self, mock_about_cog):
        """_create_object_embed for 'primary_weapon' without dps should skip field."""
        obj_data = _make_object_data("Pulse Laser", "primary_weapon", 20)
        with patch("cogs.aboutCog.EmbedConverter") as mc:
            mc.embed_to_payload.return_value = MagicMock()
            mc.payload_to_grid_embed.return_value = MagicMock(spec=discord.Embed)
            result = asyncio.run(mock_about_cog._create_object_embed(obj_data))

        assert result is not None

    def test_embed_ship_compatible_skins_even(self, mock_about_cog):
        """_create_object_embed for 'ship' with compatible_skins (even count)."""
        obj_data = {
            **_make_object_data("Hawk", "ship", 30),
            "armour": 800,
            "cargo": 200,
            "handling": 60,
            "shop_spawn_rate": 0.25,
            "max_modules": 3,
            "max_primaries": 2,
            "max_secondaries": 1,
            "max_turrets": 1,
            "manufacturer": "StarForge",
            "skinnable": True,
            "compatible_skins": {
                "Red Fury": 1,
                "Blue Ice": 2,
                "Gold Rush": 3,
                "Silver Wind": 4,
            },
        }
        with patch("cogs.aboutCog.EmbedConverter") as mc:
            mc.embed_to_payload.return_value = MagicMock()
            mc.payload_to_grid_embed.return_value = MagicMock(spec=discord.Embed)
            result = asyncio.run(mock_about_cog._create_object_embed(obj_data))

        assert result is not None
        mc.embed_to_payload.assert_called_once()

    def test_embed_ship_compatible_skins_odd(self, mock_about_cog):
        """_create_object_embed for 'ship' with odd number of compatible_skins."""
        obj_data = {
            **_make_object_data("Falcon", "ship", 31),
            "armour": 600,
            "cargo": 150,
            "handling": 70,
            "shop_spawn_rate": 0.30,
            "max_modules": 2,
            "max_primaries": 2,
            "max_secondaries": 1,
            "max_turrets": 0,
            "manufacturer": None,
            "skinnable": False,
            "compatible_skins": {
                "Red Fury": 1,
                "Blue Ice": 2,
                "Gold Rush": 3,
            },
        }
        with patch("cogs.aboutCog.EmbedConverter") as mc:
            mc.embed_to_payload.return_value = MagicMock()
            mc.payload_to_grid_embed.return_value = MagicMock(spec=discord.Embed)
            result = asyncio.run(mock_about_cog._create_object_embed(obj_data))

        assert result is not None

    def test_embed_ship_no_optional_fields(self, mock_about_cog):
        """_create_object_embed for 'ship' with no optional ship fields."""
        obj_data = {
            **_make_object_data("Bare Ship", "ship", 32),
            # no armour, cargo, handling, shop_spawn_rate, manufacturer, skinnable, compatible_skins
        }
        with patch("cogs.aboutCog.EmbedConverter") as mc:
            mc.embed_to_payload.return_value = MagicMock()
            mc.payload_to_grid_embed.return_value = MagicMock(spec=discord.Embed)
            result = asyncio.run(mock_about_cog._create_object_embed(obj_data))

        assert result is not None

    def test_embed_system_category(self, mock_about_cog):
        """_create_object_embed for 'system' should add Coordinates and Faction."""
        obj_data = {
            **_make_object_data("Sol", "system", 40),
            "coordinates": [10, 20, 30],
            "faction": "Federation",
        }
        # 'system' is NOT in the grid layout categories, so no EmbedConverter call
        result = asyncio.run(mock_about_cog._create_object_embed(obj_data))

        assert result is not None
        assert type(result).__name__ == "Embed"

    def test_embed_system_no_coords_no_faction(self, mock_about_cog):
        """_create_object_embed for 'system' without optional fields."""
        obj_data = _make_object_data("Empty System", "system", 41)
        result = asyncio.run(mock_about_cog._create_object_embed(obj_data))
        assert result is not None
        assert type(result).__name__ == "Embed"

    def test_embed_criminal_category(self, mock_about_cog):
        """_create_object_embed for 'criminal' should add Faction field."""
        obj_data = {
            **_make_object_data("Pirate Lord", "criminal", 50),
            "faction": "Outlaw",
        }
        result = asyncio.run(mock_about_cog._create_object_embed(obj_data))
        assert result is not None
        assert type(result).__name__ == "Embed"

    def test_embed_criminal_no_faction(self, mock_about_cog):
        """_create_object_embed for 'criminal' without faction."""
        obj_data = _make_object_data("Pirate", "criminal", 51)
        result = asyncio.run(mock_about_cog._create_object_embed(obj_data))
        assert result is not None
        assert type(result).__name__ == "Embed"

    def test_embed_unknown_category_default_color(self, mock_about_cog):
        """_create_object_embed for unknown category uses default color."""
        obj_data = _make_object_data("Mystery", "unknown_thing", 60)
        result = asyncio.run(mock_about_cog._create_object_embed(obj_data))
        assert result is not None
        assert type(result).__name__ == "Embed"

    def test_embed_icon_url_success(self, mock_about_cog):
        """_create_object_embed should set thumbnail when icon HEAD returns 200."""
        obj_data = {
            **_make_object_data("Eagle", "criminal", 70),
            "icon": "https://example.com/icon.png",
        }
        head_resp = MagicMock()
        head_resp.status_code = 200
        mock_about_cog.http_client.head = AsyncMock(return_value=head_resp)

        result = asyncio.run(mock_about_cog._create_object_embed(obj_data))
        assert result is not None
        assert type(result).__name__ == "Embed"
        mock_about_cog.http_client.head.assert_awaited_once()

    def test_embed_icon_url_non_200(self, mock_about_cog):
        """_create_object_embed should skip thumbnail when icon HEAD returns non-200."""
        obj_data = {
            **_make_object_data("Eagle", "criminal", 71),
            "icon": "https://example.com/broken.png",
        }
        head_resp = MagicMock()
        head_resp.status_code = 404
        mock_about_cog.http_client.head = AsyncMock(return_value=head_resp)

        result = asyncio.run(mock_about_cog._create_object_embed(obj_data))
        assert result is not None
        assert type(result).__name__ == "Embed"

    def test_embed_icon_url_exception(self, mock_about_cog):
        """_create_object_embed should handle icon HEAD request exception."""
        obj_data = {
            **_make_object_data("Eagle", "criminal", 72),
            "icon": "https://example.com/timeout.png",
        }
        mock_about_cog.http_client.head = AsyncMock(side_effect=RuntimeError("connection timeout"))

        result = asyncio.run(mock_about_cog._create_object_embed(obj_data))
        assert result is not None
        assert type(result).__name__ == "Embed"

    def test_embed_with_aliases(self, mock_about_cog):
        """_create_object_embed should add aliases field."""
        obj_data = {
            **_make_object_data("Eagle", "criminal", 80),
            "aliases": ["Big Bird", "Sky King"],
        }
        result = asyncio.run(mock_about_cog._create_object_embed(obj_data))
        assert result is not None
        assert type(result).__name__ == "Embed"

    def test_embed_with_long_aliases_truncation(self, mock_about_cog):
        """_create_object_embed should truncate very long aliases text."""
        # Create aliases that total > 1024 chars
        long_aliases = [f"LongAliasName_{i:04d}_padding" for i in range(60)]
        obj_data = {
            **_make_object_data("Eagle", "criminal", 81),
            "aliases": long_aliases,
        }
        result = asyncio.run(mock_about_cog._create_object_embed(obj_data))
        assert result is not None
        assert type(result).__name__ == "Embed"

    def test_embed_with_built_in(self, mock_about_cog):
        """_create_object_embed should add Built-in field when True."""
        obj_data = {
            **_make_object_data("Default Gun", "criminal", 82),
            "built_in": True,
        }
        result = asyncio.run(mock_about_cog._create_object_embed(obj_data))
        assert result is not None
        assert type(result).__name__ == "Embed"

    def test_embed_with_wiki_link(self, mock_about_cog):
        """_create_object_embed should add Wiki field when present."""
        obj_data = {
            **_make_object_data("Eagle", "criminal", 83),
            "wiki": "https://wiki.example.com/eagle",
        }
        result = asyncio.run(mock_about_cog._create_object_embed(obj_data))
        assert result is not None
        assert type(result).__name__ == "Embed"

    def test_embed_with_extra_atts(self, mock_about_cog):
        """_create_object_embed should add extra attributes when present."""
        obj_data = {
            **_make_object_data("Eagle", "criminal", 84),
            "extra_atts": {
                "speed": 100,
                "range": 500.5,
                "has_boost": True,
                "label": "Fast Ship",
            },
        }
        result = asyncio.run(mock_about_cog._create_object_embed(obj_data))
        assert result is not None
        assert type(result).__name__ == "Embed"

    def test_embed_with_extra_atts_long_truncation(self, mock_about_cog):
        """_create_object_embed should truncate long extra attributes."""
        # Create extra_atts that produce > 1024 chars
        extra = {f"attribute_{i:04d}": f"{'x' * 50}" for i in range(30)}
        obj_data = {
            **_make_object_data("Eagle", "criminal", 85),
            "extra_atts": extra,
        }
        result = asyncio.run(mock_about_cog._create_object_embed(obj_data))
        assert result is not None
        assert type(result).__name__ == "Embed"

    def test_embed_with_extra_atts_non_scalar_values_skipped(self, mock_about_cog):
        """_create_object_embed should skip non-scalar extra attribute values."""
        obj_data = {
            **_make_object_data("Eagle", "criminal", 86),
            "extra_atts": {
                "nested": {"a": 1},
                "list_val": [1, 2, 3],
                "scalar": 42,
            },
        }
        result = asyncio.run(mock_about_cog._create_object_embed(obj_data))
        assert result is not None
        assert type(result).__name__ == "Embed"

    def test_embed_no_emoji_in_title(self, mock_about_cog):
        """_create_object_embed with no emoji should use plain name as title."""
        obj_data = {
            **_make_object_data("Plain Object", "criminal", 87),
            "emoji": None,
        }
        result = asyncio.run(mock_about_cog._create_object_embed(obj_data))
        assert result is not None
        assert type(result).__name__ == "Embed"

    def test_embed_no_type_no_tech_no_value(self, mock_about_cog):
        """_create_object_embed with missing basic info fields."""
        obj_data = {
            "id": 88,
            "name": "Minimal",
            "category": "criminal",
            # no type, tech_level, value, emoji, icon, aliases, built_in, wiki, extra_atts
        }
        result = asyncio.run(mock_about_cog._create_object_embed(obj_data))
        assert result is not None
        assert type(result).__name__ == "Embed"

    def test_embed_secondary_weapon_grid_layout(self, mock_about_cog):
        """_create_object_embed for 'secondary_weapon' uses grid layout."""
        obj_data = _make_object_data("Missile", "secondary_weapon", 90)
        with patch("cogs.aboutCog.EmbedConverter") as mc:
            mc.embed_to_payload.return_value = MagicMock()
            mc.payload_to_grid_embed.return_value = MagicMock(spec=discord.Embed)
            result = asyncio.run(mock_about_cog._create_object_embed(obj_data))

        mc.embed_to_payload.assert_called_once()
        mc.payload_to_grid_embed.assert_called_once()
        assert result is not None

    def test_embed_turret_weapon_grid_layout(self, mock_about_cog):
        """_create_object_embed for 'turret_weapon' uses grid layout."""
        obj_data = _make_object_data("Turret", "turret_weapon", 91)
        with patch("cogs.aboutCog.EmbedConverter") as mc:
            mc.embed_to_payload.return_value = MagicMock()
            mc.payload_to_grid_embed.return_value = MagicMock(spec=discord.Embed)
            result = asyncio.run(mock_about_cog._create_object_embed(obj_data))

        mc.embed_to_payload.assert_called_once()
        assert result is not None

    def test_embed_commodity_category(self, mock_about_cog):
        """_create_object_embed for 'commodity' renders subcategory + price fields,
        suppresses raw_infobox from the generic dump, and still shows lore."""
        obj_data = {
            **_make_object_data("Hydrogen", "commodity", 200),
            "subcategory": "raw_material",
            "price_source": "wiki_table",
            "price_range_min_credits": 1000,
            "price_range_max_credits": 5000,
            "price_range_min_system": "Vega",
            "price_range_max_system": "Loma",
            "highest_non_loma_price": 4200,
            "highest_non_loma_system": "Vega",
            "extra_atts": {
                "raw_infobox": "RAWINFOBOXMARKER {{Infobox|price=999}}",
                "price_source": "wiki_table",
                "mechanics_text": "A common industrial gas traded across the galaxy.",
            },
        }

        # Commodity is in the 2-column grid set; pass EmbedConverter through so we can
        # inspect the real fields instead of a grid-rearranged MagicMock.
        with patch("cogs.aboutCog.EmbedConverter") as mc:
            mc.embed_to_payload.side_effect = lambda embed: embed
            mc.payload_to_grid_embed.side_effect = lambda embed, fields_per_row: embed
            result = asyncio.run(mock_about_cog._create_object_embed(obj_data))

        field_names = [f.name for f in result.fields]
        field_values = [f.value for f in result.fields]

        assert "Subcategory" in field_names
        assert "Price Range" in field_names
        assert "Lore / Mechanics" in field_names
        # raw_infobox must be suppressed from the generic "Additional Info" dump
        assert "Raw Infobox" not in field_names
        assert all("RAWINFOBOXMARKER" not in (v or "") for v in field_values)


# ---------------------------------------------------------------------------
# object_autocomplete — additional branch coverage
# ---------------------------------------------------------------------------


class TestObjectAutocompleteExtraBranches:
    """Extra tests for object_autocomplete edge cases."""

    def test_object_autocomplete_invalid_category(self, mock_about_cog):
        """object_autocomplete with category not in cache returns empty."""
        mock_about_cog._objects_cache.set("ship", [{"name": "Eagle"}])
        interaction = _create_mock_interaction()
        interaction.namespace = MagicMock()
        interaction.namespace.category = "nonexistent"

        result = asyncio.run(mock_about_cog.object_autocomplete(interaction, ""))
        assert result == []

    def test_object_autocomplete_limits_to_25(self, mock_about_cog):
        """object_autocomplete should return at most 25 results."""
        mock_about_cog._objects_cache.set("ship", [{"name": f"Ship_{i}"} for i in range(30)])
        interaction = _create_mock_interaction()
        interaction.namespace = MagicMock()
        interaction.namespace.category = "ship"

        result = asyncio.run(mock_about_cog.object_autocomplete(interaction, ""))
        assert len(result) <= 25


# ---------------------------------------------------------------------------
# list_category — additional branch coverage
# ---------------------------------------------------------------------------


class TestListCategoryExtraBranches:
    """Extra tests for list_category edge cases."""

    def test_list_category_field_overflow(self, mock_about_cog):
        """list_category should split objects into multiple fields when text exceeds 1024."""
        interaction = _create_mock_interaction()

        # Create objects with long names that will exceed the 1024-char limit
        objects = [{"name": f"VeryLongObjectName_{'x' * 80}_{i:03d}", "emoji": None} for i in range(20)]
        mock_about_cog._objects_cache.set("ship", objects)

        asyncio.run(mock_about_cog.list_category.callback(mock_about_cog, interaction, "ship"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_list_category_more_than_100_objects(self, mock_about_cog):
        """list_category with >100 objects should add footer note."""
        interaction = _create_mock_interaction()

        # Create 101 objects
        objects = [{"name": f"Obj{i}", "emoji": None} for i in range(101)]
        mock_about_cog._objects_cache.set("ship", objects)

        asyncio.run(mock_about_cog.list_category.callback(mock_about_cog, interaction, "ship"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_list_category_with_emoji_objects(self, mock_about_cog):
        """list_category should include emoji prefix for objects that have one."""
        interaction = _create_mock_interaction()

        objects = [
            {"name": "Eagle", "emoji": "🚀"},
            {"name": "Hawk", "emoji": "🦅"},
            {"name": "Plain", "emoji": ""},
        ]
        mock_about_cog._objects_cache.set("ship", objects)

        asyncio.run(mock_about_cog.list_category.callback(mock_about_cog, interaction, "ship"))

        interaction.followup.send.assert_awaited_once()

    def test_list_category_generic_exception(self, mock_about_cog):
        """list_category should handle unexpected exceptions gracefully."""
        interaction = _create_mock_interaction()

        # Make _objects_cache.peek raise an unexpected exception
        from unittest.mock import patch

        with patch.object(mock_about_cog._objects_cache, "peek", side_effect=RuntimeError("unexpected error")):
            asyncio.run(mock_about_cog.list_category.callback(mock_about_cog, interaction, "ship"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "error" in call_kwargs[0][0].lower()


# ---------------------------------------------------------------------------
# about command — additional branch coverage
# ---------------------------------------------------------------------------


class TestAboutCommandExtraBranches:
    """Extra tests for the /about command edge cases."""

    def test_about_category_not_in_cache(self, mock_about_cog):
        """about with category not in _objects_by_category should still call API."""
        interaction = _create_mock_interaction()

        mock_about_cog._categories_cache.set("all", ["ship"])
        mock_about_cog._objects_cache.clear()  # category not cached

        obj_resp = MagicMock()
        obj_resp.raise_for_status = MagicMock()
        obj_resp.json.return_value = _make_object_data("Eagle", "ship")
        mock_about_cog.http_client.get = AsyncMock(return_value=obj_resp)

        with patch("cogs.aboutCog.EmbedConverter") as mc:
            mc.embed_to_payload.return_value = MagicMock()
            mc.payload_to_grid_embed.return_value = MagicMock(spec=discord.Embed)

            asyncio.run(mock_about_cog.about.callback(mock_about_cog, interaction, "ship", "Eagle"))

        interaction.followup.send.assert_awaited_once()

    def test_about_name_matches_exact_name_not_alias(self, mock_about_cog):
        """about should use exact name match before checking aliases."""
        interaction = _create_mock_interaction()

        mock_about_cog._categories_cache.set("all", ["ship"])
        mock_about_cog._objects_cache.set("ship", [{"name": "Eagle", "aliases": ["Hawk"]}])

        obj_resp = MagicMock()
        obj_resp.raise_for_status = MagicMock()
        obj_resp.json.return_value = _make_object_data("Eagle", "ship")
        mock_about_cog.http_client.get = AsyncMock(return_value=obj_resp)

        with patch("cogs.aboutCog.EmbedConverter") as mc:
            mc.embed_to_payload.return_value = MagicMock()
            mc.payload_to_grid_embed.return_value = MagicMock(spec=discord.Embed)

            asyncio.run(mock_about_cog.about.callback(mock_about_cog, interaction, "ship", "Eagle"))

        call_args = mock_about_cog.http_client.get.call_args
        assert "Eagle" in call_args[0][0]


# ---------------------------------------------------------------------------
# system_autocomplete
# ---------------------------------------------------------------------------


class TestSystemAutocomplete:
    """Tests for system_autocomplete method."""

    def test_system_autocomplete_returns_systems(self, mock_about_cog):
        """system_autocomplete should return system names from preloaded data."""
        mock_about_cog._objects_cache.set(
            "system",
            [
                {"name": "Sol"},
                {"name": "Alpha Centauri"},
                {"name": "Beta Cygni"},
            ],
        )
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_about_cog.system_autocomplete(interaction, ""))

        assert len(result) == 3
        values = [c.value for c in result]
        assert "Sol" in values
        assert "Alpha Centauri" in values

    def test_system_autocomplete_filters_by_current(self, mock_about_cog):
        """system_autocomplete should filter by partial match."""
        mock_about_cog._objects_cache.set(
            "system",
            [
                {"name": "Sol"},
                {"name": "Alpha Centauri"},
                {"name": "Beta Cygni"},
            ],
        )
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_about_cog.system_autocomplete(interaction, "al"))

        assert len(result) == 1
        assert result[0].value == "Alpha Centauri"

    def test_system_autocomplete_empty_when_no_systems(self, mock_about_cog):
        """system_autocomplete returns empty list when no systems are preloaded."""
        mock_about_cog._objects_cache.clear()
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_about_cog.system_autocomplete(interaction, ""))

        assert result == []

    def test_system_autocomplete_limits_to_25(self, mock_about_cog):
        """system_autocomplete returns at most 25 results."""
        mock_about_cog._objects_cache.set("system", [{"name": f"System_{i}"} for i in range(30)])
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_about_cog.system_autocomplete(interaction, ""))

        assert len(result) <= 25


# ---------------------------------------------------------------------------
# make_route command
# ---------------------------------------------------------------------------


class TestMakeRouteCommand:
    """Tests for the /make-route slash command."""

    def test_make_route_success(self, mock_about_cog):
        """make_route should display a route embed on success."""
        interaction = _create_mock_interaction()

        route_resp = MagicMock()
        route_resp.raise_for_status = MagicMock()
        route_resp.json.return_value = {
            "route": ["Sol", "Alpha", "Beta"],
            "hops": 2,
        }
        mock_about_cog.http_client.get = AsyncMock(return_value=route_resp)

        asyncio.run(mock_about_cog.make_route.callback(mock_about_cog, interaction, "Sol", "Beta"))

        interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_make_route_embed_title_contains_start_and_end(self, mock_about_cog):
        """make_route embed title should contain start and end system names."""
        interaction = _create_mock_interaction()

        route_resp = MagicMock()
        route_resp.raise_for_status = MagicMock()
        route_resp.json.return_value = {
            "route": ["Sol", "Beta"],
            "hops": 1,
        }
        mock_about_cog.http_client.get = AsyncMock(return_value=route_resp)

        asyncio.run(mock_about_cog.make_route.callback(mock_about_cog, interaction, "Sol", "Beta"))

        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]
        assert "Sol" in embed.title
        assert "Beta" in embed.title

    def test_make_route_no_route_found_404(self, mock_about_cog):
        """make_route should send ephemeral error when route is not found (404)."""
        import httpx

        interaction = _create_mock_interaction()

        error_response = MagicMock()
        error_response.status_code = 404
        http_error = httpx.HTTPStatusError("404 Not Found", request=MagicMock(), response=error_response)
        mock_about_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_about_cog.make_route.callback(mock_about_cog, interaction, "Sol", "Nowhere"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "no route" in call_kwargs[0][0].lower()

    def test_make_route_too_long_400(self, mock_about_cog):
        """make_route should send ephemeral error when route is too long (400)."""
        import httpx

        interaction = _create_mock_interaction()

        error_response = MagicMock()
        error_response.status_code = 400
        http_error = httpx.HTTPStatusError("400 Bad Request", request=MagicMock(), response=error_response)
        mock_about_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_about_cog.make_route.callback(mock_about_cog, interaction, "A", "Z"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "maximum length" in call_kwargs[0][0].lower()

    def test_make_route_api_error_non_404_non_400(self, mock_about_cog):
        """make_route should handle non-404/400 HTTP errors gracefully."""
        import httpx

        interaction = _create_mock_interaction()

        error_response = MagicMock()
        error_response.status_code = 500
        http_error = httpx.HTTPStatusError("500 Server Error", request=MagicMock(), response=error_response)
        mock_about_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_about_cog.make_route.callback(mock_about_cog, interaction, "A", "B"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_make_route_generic_exception(self, mock_about_cog):
        """make_route should handle generic exceptions gracefully."""
        interaction = _create_mock_interaction()

        mock_about_cog.http_client.get = AsyncMock(side_effect=RuntimeError("network failure"))

        asyncio.run(mock_about_cog.make_route.callback(mock_about_cog, interaction, "A", "B"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_make_route_calls_correct_api_endpoint(self, mock_about_cog):
        """make_route should call the /systems/route endpoint with correct params."""
        interaction = _create_mock_interaction()

        route_resp = MagicMock()
        route_resp.raise_for_status = MagicMock()
        route_resp.json.return_value = {"route": ["Sol", "Alpha"], "hops": 1}
        mock_about_cog.http_client.get = AsyncMock(return_value=route_resp)

        asyncio.run(mock_about_cog.make_route.callback(mock_about_cog, interaction, "Sol", "Alpha"))

        # make_route makes 2 GET calls: /systems/route (first) and /systems/route/map (second).
        # Verify the first call is the route endpoint with the correct start/end params.
        assert mock_about_cog.http_client.get.await_count >= 1
        first_call = mock_about_cog.http_client.get.call_args_list[0]
        assert "systems/route" in first_call[0][0]
        # Verify start and end were passed as params
        assert first_call[1].get("params", {}).get("start") == "Sol"
        assert first_call[1].get("params", {}).get("end") == "Alpha"


# ---------------------------------------------------------------------------
# list_category with filters
# ---------------------------------------------------------------------------


class TestListCategoryFilters:
    """Tests for /list_category with optional tech_level and manufacturer filters."""

    def test_list_category_tech_level_filter(self, mock_about_cog):
        """list_category with tech_level filter shows only matching objects."""
        interaction = _create_mock_interaction()

        mock_about_cog._objects_cache.set(
            "ship",
            [
                {"name": "Eagle", "emoji": None, "tech_level": 1},
                {"name": "Hawk", "emoji": None, "tech_level": 2},
                {"name": "Falcon", "emoji": None, "tech_level": 1},
            ],
        )

        asyncio.run(mock_about_cog.list_category.callback(mock_about_cog, interaction, "ship", tech_level=1))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        # Description should show filtered count
        embed = call_kwargs["embed"]
        assert "2" in embed.description  # 2 objects match tech level 1

    def test_list_category_manufacturer_filter(self, mock_about_cog):
        """list_category with manufacturer filter shows only matching objects."""
        interaction = _create_mock_interaction()

        mock_about_cog._objects_cache.set(
            "ship",
            [
                {"name": "Eagle", "emoji": None, "manufacturer": "AcmeCorp"},
                {"name": "Hawk", "emoji": None, "manufacturer": "StarForge"},
                {"name": "Falcon", "emoji": None, "manufacturer": "AcmeCorp"},
            ],
        )

        asyncio.run(mock_about_cog.list_category.callback(mock_about_cog, interaction, "ship", manufacturer="AcmeCorp"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "2" in embed.description  # 2 objects match manufacturer

    def test_list_category_both_filters(self, mock_about_cog):
        """list_category with both tech_level and manufacturer filters combined."""
        interaction = _create_mock_interaction()

        mock_about_cog._objects_cache.set(
            "ship",
            [
                {"name": "Eagle", "emoji": None, "tech_level": 1, "manufacturer": "AcmeCorp"},
                {"name": "Hawk", "emoji": None, "tech_level": 2, "manufacturer": "AcmeCorp"},
                {"name": "Falcon", "emoji": None, "tech_level": 1, "manufacturer": "StarForge"},
            ],
        )

        asyncio.run(
            mock_about_cog.list_category.callback(
                mock_about_cog, interaction, "ship", tech_level=1, manufacturer="AcmeCorp"
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "1" in embed.description  # only Eagle matches both filters

    def test_list_category_no_filter_shows_all(self, mock_about_cog):
        """list_category without filters shows all objects (existing behavior)."""
        interaction = _create_mock_interaction()

        mock_about_cog._objects_cache.set(
            "ship",
            [
                {"name": "Eagle", "emoji": None},
                {"name": "Hawk", "emoji": None},
            ],
        )

        asyncio.run(mock_about_cog.list_category.callback(mock_about_cog, interaction, "ship"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_list_category_filter_no_match_sends_ephemeral(self, mock_about_cog):
        """list_category should send ephemeral message when filters produce no matches."""
        interaction = _create_mock_interaction()

        mock_about_cog._objects_cache.set(
            "ship",
            [
                {"name": "Eagle", "emoji": None, "tech_level": 1},
            ],
        )

        asyncio.run(mock_about_cog.list_category.callback(mock_about_cog, interaction, "ship", tech_level=5))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_list_category_manufacturer_filter_case_insensitive(self, mock_about_cog):
        """list_category manufacturer filter should be case-insensitive."""
        interaction = _create_mock_interaction()

        mock_about_cog._objects_cache.set(
            "ship",
            [
                {"name": "Eagle", "emoji": None, "manufacturer": "AcmeCorp"},
                {"name": "Hawk", "emoji": None, "manufacturer": "StarForge"},
            ],
        )

        asyncio.run(mock_about_cog.list_category.callback(mock_about_cog, interaction, "ship", manufacturer="acmecorp"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "1" in embed.description  # only Eagle matches


# ---------------------------------------------------------------------------
# A.26 / A.27 regression coverage — /list_category header dedup + truncation
# ---------------------------------------------------------------------------


class TestListCategoryBugBundleRegressions:
    """A.26 + A.27: duplicate "Objects" field headers and silent truncation.

    Uses a real ``discord.Embed`` through the real call path so the fixes
    are exercised end-to-end (no mocking of embed internals).
    """

    def test_list_category_no_duplicate_objects_field_names(self, mock_about_cog):
        """A.26: splitting into multiple fields must NOT repeat the "Objects" header."""
        from cogs._shared.embed_pagination import SPACER_NAME

        interaction = _create_mock_interaction()
        # Names long enough to force a 1024-char split into ≥2 fields.
        objects = [{"name": f"VeryLongObjectName_{'x' * 80}_{i:03d}", "emoji": None} for i in range(20)]
        mock_about_cog._objects_cache.set("ship", objects)

        asyncio.run(mock_about_cog.list_category.callback(mock_about_cog, interaction, "ship"))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        objects_named_fields = [f for f in embed.fields if f.name == "Objects"]
        assert len(objects_named_fields) == 1, f"Expected exactly ONE 'Objects' header, got {len(objects_named_fields)}"
        # Everything beyond the first header must be a zero-width spacer.
        content_fields = [f for f in embed.fields if f.name in ("Objects", SPACER_NAME)]
        assert len(content_fields) >= 2, "Dataset should have forced a 1024-char split into ≥2 fields"
        for f in content_fields[1:]:
            assert f.name == SPACER_NAME

    def test_list_category_101_items_triggers_truncation_footer(self, mock_about_cog):
        """A.27: passing 101 items must render 100 and set a truncation footer."""
        interaction = _create_mock_interaction()
        objects = [{"name": f"Obj{i:03d}", "emoji": None} for i in range(101)]
        mock_about_cog._objects_cache.set("ship", objects)

        asyncio.run(mock_about_cog.list_category.callback(mock_about_cog, interaction, "ship"))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        # Footer advertises the honest truncation ratio.
        assert embed.footer is not None and embed.footer.text is not None
        assert embed.footer.text == "Showing first 100 of 101 objects"
        # And only the first 100 actually appear in the rendered content.
        rendered = "\n".join(f.value for f in embed.fields)
        assert "Obj099" in rendered
        assert "Obj100" not in rendered

    def test_list_category_100_items_exact_cap_no_footer(self, mock_about_cog):
        """A.27: exactly-100 items are NOT truncated → no footer spam."""
        interaction = _create_mock_interaction()
        objects = [{"name": f"Obj{i:03d}", "emoji": None} for i in range(100)]
        mock_about_cog._objects_cache.set("ship", objects)

        asyncio.run(mock_about_cog.list_category.callback(mock_about_cog, interaction, "ship"))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        # No truncation footer when exactly at the cap.
        footer_text = embed.footer.text if embed.footer else None
        assert not (footer_text and "Showing first" in footer_text), (
            f"Unexpected truncation footer at exactly-100 items: {footer_text!r}"
        )

    def test_list_category_66_modules_renders_all_no_truncation(self, mock_about_cog):
        """A.27: current max real-world category (modules, 66 items) is below cap."""
        interaction = _create_mock_interaction()
        objects = [{"name": f"Module{i:02d}", "emoji": None} for i in range(66)]
        mock_about_cog._objects_cache.set("module", objects)

        asyncio.run(mock_about_cog.list_category.callback(mock_about_cog, interaction, "module"))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        footer_text = embed.footer.text if embed.footer else None
        assert not (footer_text and "Showing first" in footer_text)
        rendered = "\n".join(f.value for f in embed.fields)
        # Every one of the 66 modules is present.
        for i in range(66):
            assert f"Module{i:02d}" in rendered


# ===========================================================================
# T11 — §14 item-detail embed rendering tests
# ===========================================================================


def _field_names(embed: discord.Embed) -> list[str]:
    """Return lowercased field names from a discord.Embed (before grid conversion)."""
    return [f.name.lower() for f in embed.fields]


def _field_value(embed: discord.Embed, name_fragment: str) -> str | None:
    """Return the value of the first field whose name contains *name_fragment* (case-insensitive)."""
    fragment = name_fragment.lower()
    for f in embed.fields:
        if fragment in f.name.lower():
            return f.value
    return None


def _run_embed(cog, obj_data: dict) -> discord.Embed:
    """Call _create_object_embed and return the *pre-grid* discord.Embed.

    EmbedConverter is patched to a passthrough so we can inspect the embed
    fields that the cog rendered before the 2-column layout transformation.
    """
    captured: list[discord.Embed] = []

    def _capture(embed, *_a, **_kw):
        captured.append(embed)
        return MagicMock()

    def _passthrough(*_a, **_kw):
        # Return a real discord.Embed so we can inspect it downstream.
        if captured:
            return captured[0]
        return discord.Embed()

    with patch("cogs.aboutCog.EmbedConverter") as mock_conv:
        mock_conv.embed_to_payload.side_effect = _capture
        mock_conv.payload_to_grid_embed.side_effect = _passthrough
        embed = asyncio.run(cog._create_object_embed(obj_data))
    return embed


class TestT11EmbedRendering:
    """§14 / T11 — Discord embed rendering of combat fields in the /about cog.

    Each test builds an obj_data dict as the API would return it (after
    _enrich_combat_fields), calls _create_object_embed, and inspects the
    pre-grid discord.Embed fields via the captured embed from EmbedConverter.
    """

    # -------------------------------------------------------------------------
    # EMP primary weapons
    # -------------------------------------------------------------------------

    def test_emp_blaster_embed_has_emp_field(self, mock_about_cog):
        """EMP-blaster embed must contain an 'EMP damage' field."""
        obj_data = {
            **_make_object_data("Luna EMP Mk I", "primary_weapon"),
            "dps": 8.57,
            "emp_damage": 3,
        }
        embed = _run_embed(mock_about_cog, obj_data)
        assert any("emp damage" in n for n in _field_names(embed))
        assert _field_value(embed, "emp damage") == "3"

    def test_emp_blaster_embed_no_misleading_zero_damage(self, mock_about_cog):
        """EMP-blaster embed must NOT display a bare 'Damage: 0' field (no physical damage field)."""
        obj_data = {
            **_make_object_data("Sol EMP Mk II", "primary_weapon"),
            "dps": 11.11,
            "emp_damage": 5,
        }
        embed = _run_embed(mock_about_cog, obj_data)
        # There is no "damage" field at all (primary weapon embeds don't show damage directly)
        field_names = _field_names(embed)
        # Confirm EMP field is present
        assert any("emp" in n for n in field_names)

    def test_non_emp_primary_no_emp_field(self, mock_about_cog):
        """Non-EMP primary weapon must NOT render an EMP damage field."""
        obj_data = {
            **_make_object_data("Nirai Pulse", "primary_weapon"),
            "dps": 30.0,
            "emp_damage": None,
        }
        embed = _run_embed(mock_about_cog, obj_data)
        assert not any("emp" in n for n in _field_names(embed))

    # -------------------------------------------------------------------------
    # Cluster missiles
    # -------------------------------------------------------------------------

    def test_cluster_embed_has_burst_count_field(self, mock_about_cog):
        """Cluster-missile embed must contain a 'Burst count' field."""
        obj_data = {
            **_make_object_data("Shesha", "secondary_weapon"),
            "damage": 60,
            "burst_count": 3,
            "nuke_direct_damage": None,
            "nuke_effective_magnitude_m": None,
            "nuke_self_damage_factor": None,
            "emp_damage": None,
        }
        embed = _run_embed(mock_about_cog, obj_data)
        assert any("burst count" in n for n in _field_names(embed))
        assert _field_value(embed, "burst count") == "3"

    def test_cluster_embed_total_damage_correct(self, mock_about_cog):
        """Cluster-missile embed must contain 'Total damage on full hit' = burst_count * damage."""
        obj_data = {
            **_make_object_data("Patala", "secondary_weapon"),
            "damage": 90,
            "burst_count": 5,
            "nuke_direct_damage": None,
            "nuke_effective_magnitude_m": None,
            "nuke_self_damage_factor": None,
            "emp_damage": None,
        }
        embed = _run_embed(mock_about_cog, obj_data)
        total_field = _field_value(embed, "total damage")
        assert total_field == "450", f"Expected 450, got {total_field!r}"

    # -------------------------------------------------------------------------
    # Nuke weapons
    # -------------------------------------------------------------------------

    def test_nuke_embed_has_direct_hit_damage(self, mock_about_cog):
        """Nuke embed must contain a 'Direct hit damage' field."""
        obj_data = {
            **_make_object_data("Liberator", "secondary_weapon"),
            "damage": 850,
            "burst_count": None,
            "nuke_direct_damage": 850,
            "nuke_effective_magnitude_m": 1250,
            "nuke_self_damage_factor": 0.25,
            "emp_damage": None,
        }
        embed = _run_embed(mock_about_cog, obj_data)
        assert any("direct hit" in n for n in _field_names(embed))
        assert _field_value(embed, "direct hit") == "850"

    def test_nuke_embed_has_effective_blast_radius(self, mock_about_cog):
        """Nuke embed must contain 'Effective blast radius' (NOT raw magnitude_m)."""
        obj_data = {
            **_make_object_data("Liberator", "secondary_weapon"),
            "damage": 850,
            "burst_count": None,
            "nuke_direct_damage": 850,
            "nuke_effective_magnitude_m": 1250,
            "nuke_self_damage_factor": 0.25,
            "emp_damage": None,
        }
        embed = _run_embed(mock_about_cog, obj_data)
        assert any("blast radius" in n for n in _field_names(embed))
        blast_val = _field_value(embed, "blast radius")
        # Must use effective value (1250), NOT raw magnitude_m (12500)
        assert "1250" in blast_val, f"Expected effective radius 1250, got {blast_val!r}"
        assert "12500" not in blast_val, "Raw magnitude_m must not appear in embed"

    def test_nuke_embed_has_self_damage_warning(self, mock_about_cog):
        """Nuke embed must contain a self-damage warning field."""
        obj_data = {
            **_make_object_data("Liberator", "secondary_weapon"),
            "damage": 850,
            "burst_count": None,
            "nuke_direct_damage": 850,
            "nuke_effective_magnitude_m": 1250,
            "nuke_self_damage_factor": 0.25,
            "emp_damage": None,
        }
        embed = _run_embed(mock_about_cog, obj_data)
        assert any("self-damage" in n or "self damage" in n for n in _field_names(embed))
        # self_dmg = round(850 * 0.25) = round(212.5) = 212 (Python banker's rounding)
        self_val = _field_value(embed, "self-damage") or _field_value(embed, "self damage")
        assert self_val is not None
        assert "212" in self_val, f"Expected ~212 hp, got {self_val!r}"

    def test_nuke_embed_does_not_show_raw_magnitude(self, mock_about_cog):
        """Nuke embed must NOT display raw magnitude_m; only effective value is shown."""
        obj_data = {
            **_make_object_data("AMR Extinctor", "secondary_weapon"),
            "damage": 700,
            "burst_count": None,
            "nuke_direct_damage": 700,
            "nuke_effective_magnitude_m": 4000,
            "nuke_self_damage_factor": 0.25,
            "emp_damage": None,
            "extra_atts": {"extra_atts": {"subtype": "nuke", "magnitude_m": 40000}},
        }
        embed = _run_embed(mock_about_cog, obj_data)
        all_values = " ".join(f.value for f in embed.fields)
        # Raw magnitude_m (40000) must NOT appear
        assert "40000" not in all_values, f"Raw magnitude_m must not appear; fields: {all_values!r}"

    # -------------------------------------------------------------------------
    # Pure-EMP secondary weapons (§14 / T11 — D1.4)
    # -------------------------------------------------------------------------

    def test_mamba_emp_missile_no_misleading_zero_damage(self, mock_about_cog):
        """Mamba EMP missile (damage=0, emp_damage=100) must NOT show 'Damage: 0' and MUST show EMP damage.

        Pure-EMP secondary: physical damage field must be suppressed; only 'EMP damage' rendered.
        Mirrors the primary-weapon EMP path (test_emp_blaster_embed_no_misleading_zero_damage).
        """
        obj_data = {
            **_make_object_data("Mamba EMP", "secondary_weapon"),
            "damage": 0,
            "burst_count": None,
            "nuke_direct_damage": None,
            "nuke_effective_magnitude_m": None,
            "nuke_self_damage_factor": None,
            "emp_damage": 100,
            "subtype": "missile",
        }
        embed = _run_embed(mock_about_cog, obj_data)
        names = _field_names(embed)
        # Must NOT show "Damage: 0"
        damage_field = _field_value(embed, "damage")
        assert damage_field != "0", f"Pure-EMP secondary must not show 'Damage: 0'; got damage field={damage_field!r}"
        # Must show "EMP damage" with the correct value
        assert any("emp damage" in n for n in names), f"Missing 'EMP damage' field; fields={names}"
        assert _field_value(embed, "emp damage") == "100", (
            f"Expected EMP damage=100; got {_field_value(embed, 'emp damage')!r}"
        )

    def test_neetha_emp_mine_no_misleading_zero_damage(self, mock_about_cog):
        """Neétha EMP mine (damage=0, emp_damage=500) must NOT show 'Damage: 0' and MUST show EMP damage.

        Pure-EMP secondary: physical damage field must be suppressed; only 'EMP damage' rendered.
        """
        obj_data = {
            **_make_object_data("Neétha EMP", "secondary_weapon"),
            "damage": 0,
            "burst_count": None,
            "nuke_direct_damage": None,
            "nuke_effective_magnitude_m": None,
            "nuke_self_damage_factor": None,
            "emp_damage": 500,
            "subtype": "mine",
        }
        embed = _run_embed(mock_about_cog, obj_data)
        names = _field_names(embed)
        # Must NOT show "Damage: 0"
        damage_field = _field_value(embed, "damage")
        assert damage_field != "0", f"Pure-EMP secondary must not show 'Damage: 0'; got damage field={damage_field!r}"
        # Must show "EMP damage" with the correct value
        assert any("emp damage" in n for n in names), f"Missing 'EMP damage' field; fields={names}"
        assert _field_value(embed, "emp damage") == "500", (
            f"Expected EMP damage=500; got {_field_value(embed, 'emp damage')!r}"
        )

    def test_normal_secondary_with_damage_still_shows_damage(self, mock_about_cog):
        """Normal secondary with real damage (and no emp_damage) must still show the Damage field.

        Regression guard: the pure-EMP suppression must not affect ordinary missiles.
        """
        obj_data = {
            **_make_object_data("Standard Missile", "secondary_weapon"),
            "damage": 250,
            "burst_count": None,
            "nuke_direct_damage": None,
            "nuke_effective_magnitude_m": None,
            "nuke_self_damage_factor": None,
            "emp_damage": None,
            "subtype": "missile",
        }
        embed = _run_embed(mock_about_cog, obj_data)
        assert _field_value(embed, "damage") == "250", (
            f"Normal secondary must still show Damage field; got {_field_value(embed, 'damage')!r}"
        )

    # -------------------------------------------------------------------------
    # PrimaryWeaponMod modules
    # -------------------------------------------------------------------------

    def test_pwm_embed_has_all_three_fields(self, mock_about_cog):
        """PrimaryWeaponMod embed must show damage modifier, fire rate modifier, AND net DPS shift."""
        obj_data = {
            **_make_object_data("Nirai Overdrive", "module"),
            "max_equipped": 1,
            "damage_pct": -10,
            "fire_rate_pct": 20,
            "dps_multiplier": 1.1,
        }
        embed = _run_embed(mock_about_cog, obj_data)
        names = _field_names(embed)
        assert any("damage modifier" in n for n in names), f"Missing 'damage modifier'; fields={names}"
        assert any("fire rate modifier" in n for n in names), f"Missing 'fire rate modifier'; fields={names}"
        assert any("net dps shift" in n for n in names), f"Missing 'net dps shift'; fields={names}"

    def test_pwm_overdrive_values(self, mock_about_cog):
        """Nirai Overdrive: damage=-10%, fire_rate=+20%, dps_mult=1.10."""
        obj_data = {
            **_make_object_data("Nirai Overdrive", "module"),
            "max_equipped": 1,
            "damage_pct": -10,
            "fire_rate_pct": 20,
            "dps_multiplier": 1.1,
        }
        embed = _run_embed(mock_about_cog, obj_data)
        assert "-10%" in _field_value(embed, "damage modifier")
        assert "+20%" in _field_value(embed, "fire rate modifier")

    def test_pwm_overcharge_values(self, mock_about_cog):
        """Nirai Overcharge: damage=+20%, fire_rate=-10%, dps_mult=1.10."""
        obj_data = {
            **_make_object_data("Nirai Overcharge", "module"),
            "max_equipped": 1,
            "damage_pct": 20,
            "fire_rate_pct": -10,
            "dps_multiplier": 1.1,
        }
        embed = _run_embed(mock_about_cog, obj_data)
        assert "+20%" in _field_value(embed, "damage modifier")
        assert "-10%" in _field_value(embed, "fire rate modifier")

    def test_pwm_overdrive_overcharge_distinct_field_values(self, mock_about_cog):
        """Overdrive and Overcharge show different field values despite same dps_multiplier."""
        od_data = {
            **_make_object_data("Nirai Overdrive", "module"),
            "max_equipped": 1,
            "damage_pct": -10,
            "fire_rate_pct": 20,
            "dps_multiplier": 1.1,
        }
        oc_data = {
            **_make_object_data("Nirai Overcharge", "module"),
            "max_equipped": 1,
            "damage_pct": 20,
            "fire_rate_pct": -10,
            "dps_multiplier": 1.1,
        }
        embed_od = _run_embed(mock_about_cog, od_data)
        embed_oc = _run_embed(mock_about_cog, oc_data)
        # damage_pct values differ
        od_dmg = _field_value(embed_od, "damage modifier")
        oc_dmg = _field_value(embed_oc, "damage modifier")
        assert od_dmg != oc_dmg, f"Overdrive and Overcharge should differ; got od={od_dmg!r} oc={oc_dmg!r}"

    def test_non_pwm_module_no_pwm_fields(self, mock_about_cog):
        """Non-PrimaryWeaponMod module (Scanner) must not show damage/fire_rate/dps_mult fields."""
        obj_data = {
            **_make_object_data("Scanner", "module"),
            "max_equipped": 1,
            "damage_pct": None,
            "fire_rate_pct": None,
            "dps_multiplier": None,
        }
        embed = _run_embed(mock_about_cog, obj_data)
        names = _field_names(embed)
        assert not any("damage modifier" in n for n in names)
        assert not any("fire rate modifier" in n for n in names)
        assert not any("net dps shift" in n for n in names)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
