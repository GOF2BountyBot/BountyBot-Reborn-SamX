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
        """AboutCog should start with empty categories list."""
        assert mock_about_cog._categories == []

    def test_initialization_sets_empty_objects_by_category(self, mock_about_cog):
        """AboutCog should start with empty objects_by_category dict."""
        assert mock_about_cog._objects_by_category == {}

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

        assert mock_about_cog._categories == ["ship", "module", "primary_weapon"]
        assert "ship" in mock_about_cog._objects_by_category
        assert len(mock_about_cog._objects_by_category["ship"]) == 2

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
        assert mock_about_cog._categories == []
        assert mock_about_cog._objects_by_category == {}

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
        assert mock_about_cog._categories == ["ship"]
        assert len(mock_about_cog._objects_by_category["ship"]) == 1
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
        assert mock_about_cog._categories == ["ship", "module"]
        # Ship objects should be loaded
        assert len(mock_about_cog._objects_by_category["ship"]) == 1
        # Module objects should be empty list (fallback on HTTP error)
        assert mock_about_cog._objects_by_category["module"] == []

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
        assert mock_about_cog._categories == []
        assert mock_about_cog._objects_by_category == {}


# ---------------------------------------------------------------------------
# category_autocomplete
# ---------------------------------------------------------------------------


class TestCategoryAutocomplete:
    """Tests for category_autocomplete."""

    def test_category_autocomplete_empty_current(self, mock_about_cog):
        """category_autocomplete with empty current returns all categories."""
        mock_about_cog._categories = ["ship", "module", "primary_weapon"]
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_about_cog.category_autocomplete(interaction, ""))

        assert len(result) == 3
        values = [c.value for c in result]
        assert "ship" in values
        assert "module" in values

    def test_category_autocomplete_partial_match(self, mock_about_cog):
        """category_autocomplete should filter by partial match."""
        mock_about_cog._categories = ["ship", "module", "primary_weapon"]
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_about_cog.category_autocomplete(interaction, "mod"))

        assert len(result) == 1
        assert result[0].value == "module"

    def test_category_autocomplete_empty_categories(self, mock_about_cog):
        """category_autocomplete with no data returns empty list."""
        mock_about_cog._categories = []
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_about_cog.category_autocomplete(interaction, ""))

        assert result == []

    def test_category_autocomplete_limits_to_25(self, mock_about_cog):
        """category_autocomplete should return at most 25 results."""
        mock_about_cog._categories = [f"cat{i}" for i in range(30)]
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
        mock_about_cog._objects_by_category = {"ship": [{"name": "Eagle"}, {"name": "Hawk"}]}
        interaction = _create_mock_interaction()
        # namespace has no category attribute
        interaction.namespace = MagicMock(spec=[])

        result = asyncio.run(mock_about_cog.object_autocomplete(interaction, ""))

        assert result == []

    def test_object_autocomplete_valid_category(self, mock_about_cog):
        """object_autocomplete should return objects for selected category."""
        mock_about_cog._objects_by_category = {"ship": [{"name": "Eagle"}, {"name": "Hawk"}, {"name": "Falcon"}]}
        interaction = _create_mock_interaction()
        interaction.namespace = MagicMock()
        interaction.namespace.category = "ship"

        result = asyncio.run(mock_about_cog.object_autocomplete(interaction, ""))

        assert len(result) == 3

    def test_object_autocomplete_partial_match(self, mock_about_cog):
        """object_autocomplete should filter by partial match."""
        mock_about_cog._objects_by_category = {"ship": [{"name": "Eagle"}, {"name": "Hawk"}, {"name": "Falcon"}]}
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
        mock_about_cog._categories = ["ship"]
        mock_about_cog._objects_by_category = {"ship": [{"name": "Eagle", "aliases": []}]}

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

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()

    def test_about_object_not_found_404(self, mock_about_cog):
        """about should send ephemeral error when object is not found."""
        import httpx

        interaction = _create_mock_interaction()

        mock_about_cog._categories = ["ship"]
        mock_about_cog._objects_by_category = {"ship": []}

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

        mock_about_cog._categories = ["ship"]
        mock_about_cog._objects_by_category = {"ship": []}

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

        mock_about_cog._categories = ["ship"]
        mock_about_cog._objects_by_category = {"ship": []}

        mock_about_cog.http_client.get = AsyncMock(side_effect=RuntimeError("network failure"))

        asyncio.run(mock_about_cog.about.callback(mock_about_cog, interaction, "ship", "Eagle"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_about_resolves_alias(self, mock_about_cog):
        """about should resolve an alias to the canonical name."""
        interaction = _create_mock_interaction()

        mock_about_cog._categories = ["ship"]
        mock_about_cog._objects_by_category = {"ship": [{"name": "Eagle", "aliases": ["Eagleship", "TheEagle"]}]}

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

        mock_about_cog._objects_by_category = {
            "ship": [{"name": "Eagle", "emoji": "🚀"}, {"name": "Hawk", "emoji": None}]
        }

        asyncio.run(mock_about_cog.list_category.callback(mock_about_cog, interaction, "ship"))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_list_category_not_found(self, mock_about_cog):
        """list_category with unknown category should send error."""
        interaction = _create_mock_interaction()

        mock_about_cog._objects_by_category = {}

        asyncio.run(mock_about_cog.list_category.callback(mock_about_cog, interaction, "unknown_category"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "not found" in call_kwargs[0][0].lower()

    def test_list_category_empty_category(self, mock_about_cog):
        """list_category with empty category should send ephemeral message."""
        interaction = _create_mock_interaction()

        mock_about_cog._objects_by_category = {"ship": []}

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


# ---------------------------------------------------------------------------
# object_autocomplete — additional branch coverage
# ---------------------------------------------------------------------------


class TestObjectAutocompleteExtraBranches:
    """Extra tests for object_autocomplete edge cases."""

    def test_object_autocomplete_invalid_category(self, mock_about_cog):
        """object_autocomplete with category not in cache returns empty."""
        mock_about_cog._objects_by_category = {"ship": [{"name": "Eagle"}]}
        interaction = _create_mock_interaction()
        interaction.namespace = MagicMock()
        interaction.namespace.category = "nonexistent"

        result = asyncio.run(mock_about_cog.object_autocomplete(interaction, ""))
        assert result == []

    def test_object_autocomplete_limits_to_25(self, mock_about_cog):
        """object_autocomplete should return at most 25 results."""
        mock_about_cog._objects_by_category = {"ship": [{"name": f"Ship_{i}"} for i in range(30)]}
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
        mock_about_cog._objects_by_category = {"ship": objects}

        asyncio.run(mock_about_cog.list_category.callback(mock_about_cog, interaction, "ship"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_list_category_more_than_100_objects(self, mock_about_cog):
        """list_category with >100 objects should add footer note."""
        interaction = _create_mock_interaction()

        # Create 101 objects
        objects = [{"name": f"Obj{i}", "emoji": None} for i in range(101)]
        mock_about_cog._objects_by_category = {"ship": objects}

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
        mock_about_cog._objects_by_category = {"ship": objects}

        asyncio.run(mock_about_cog.list_category.callback(mock_about_cog, interaction, "ship"))

        interaction.followup.send.assert_awaited_once()

    def test_list_category_generic_exception(self, mock_about_cog):
        """list_category should handle unexpected exceptions gracefully."""
        interaction = _create_mock_interaction()

        # Make _objects_by_category raise when accessed via __contains__
        mock_about_cog._objects_by_category = MagicMock()
        mock_about_cog._objects_by_category.__contains__ = MagicMock(side_effect=RuntimeError("unexpected error"))

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

        mock_about_cog._categories = ["ship"]
        mock_about_cog._objects_by_category = {}  # category not cached

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

        mock_about_cog._categories = ["ship"]
        mock_about_cog._objects_by_category = {"ship": [{"name": "Eagle", "aliases": ["Hawk"]}]}

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
        mock_about_cog._objects_by_category = {
            "system": [
                {"name": "Sol"},
                {"name": "Alpha Centauri"},
                {"name": "Beta Cygni"},
            ]
        }
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_about_cog.system_autocomplete(interaction, ""))

        assert len(result) == 3
        values = [c.value for c in result]
        assert "Sol" in values
        assert "Alpha Centauri" in values

    def test_system_autocomplete_filters_by_current(self, mock_about_cog):
        """system_autocomplete should filter by partial match."""
        mock_about_cog._objects_by_category = {
            "system": [
                {"name": "Sol"},
                {"name": "Alpha Centauri"},
                {"name": "Beta Cygni"},
            ]
        }
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_about_cog.system_autocomplete(interaction, "al"))

        assert len(result) == 1
        assert result[0].value == "Alpha Centauri"

    def test_system_autocomplete_empty_when_no_systems(self, mock_about_cog):
        """system_autocomplete returns empty list when no systems are preloaded."""
        mock_about_cog._objects_by_category = {}
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_about_cog.system_autocomplete(interaction, ""))

        assert result == []

    def test_system_autocomplete_limits_to_25(self, mock_about_cog):
        """system_autocomplete returns at most 25 results."""
        mock_about_cog._objects_by_category = {"system": [{"name": f"System_{i}"} for i in range(30)]}
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

        interaction.response.defer.assert_awaited_once_with(thinking=True)
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

        mock_about_cog._objects_by_category = {
            "ship": [
                {"name": "Eagle", "emoji": None, "tech_level": 1},
                {"name": "Hawk", "emoji": None, "tech_level": 2},
                {"name": "Falcon", "emoji": None, "tech_level": 1},
            ]
        }

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

        mock_about_cog._objects_by_category = {
            "ship": [
                {"name": "Eagle", "emoji": None, "manufacturer": "AcmeCorp"},
                {"name": "Hawk", "emoji": None, "manufacturer": "StarForge"},
                {"name": "Falcon", "emoji": None, "manufacturer": "AcmeCorp"},
            ]
        }

        asyncio.run(mock_about_cog.list_category.callback(mock_about_cog, interaction, "ship", manufacturer="AcmeCorp"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "2" in embed.description  # 2 objects match manufacturer

    def test_list_category_both_filters(self, mock_about_cog):
        """list_category with both tech_level and manufacturer filters combined."""
        interaction = _create_mock_interaction()

        mock_about_cog._objects_by_category = {
            "ship": [
                {"name": "Eagle", "emoji": None, "tech_level": 1, "manufacturer": "AcmeCorp"},
                {"name": "Hawk", "emoji": None, "tech_level": 2, "manufacturer": "AcmeCorp"},
                {"name": "Falcon", "emoji": None, "tech_level": 1, "manufacturer": "StarForge"},
            ]
        }

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

        mock_about_cog._objects_by_category = {
            "ship": [
                {"name": "Eagle", "emoji": None},
                {"name": "Hawk", "emoji": None},
            ]
        }

        asyncio.run(mock_about_cog.list_category.callback(mock_about_cog, interaction, "ship"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_list_category_filter_no_match_sends_ephemeral(self, mock_about_cog):
        """list_category should send ephemeral message when filters produce no matches."""
        interaction = _create_mock_interaction()

        mock_about_cog._objects_by_category = {
            "ship": [
                {"name": "Eagle", "emoji": None, "tech_level": 1},
            ]
        }

        asyncio.run(mock_about_cog.list_category.callback(mock_about_cog, interaction, "ship", tech_level=5))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_list_category_manufacturer_filter_case_insensitive(self, mock_about_cog):
        """list_category manufacturer filter should be case-insensitive."""
        interaction = _create_mock_interaction()

        mock_about_cog._objects_by_category = {
            "ship": [
                {"name": "Eagle", "emoji": None, "manufacturer": "AcmeCorp"},
                {"name": "Hawk", "emoji": None, "manufacturer": "StarForge"},
            ]
        }

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
        mock_about_cog._objects_by_category = {"ship": objects}

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
        mock_about_cog._objects_by_category = {"ship": objects}

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
        mock_about_cog._objects_by_category = {"ship": objects}

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
        mock_about_cog._objects_by_category = {"module": objects}

        asyncio.run(mock_about_cog.list_category.callback(mock_about_cog, interaction, "module"))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        footer_text = embed.footer.text if embed.footer else None
        assert not (footer_text and "Showing first" in footer_text)
        rendered = "\n".join(f.value for f in embed.fields)
        # Every one of the 66 modules is present.
        for i in range(66):
            assert f"Module{i:02d}" in rendered


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
