"""Tests for aboutCog — boosting coverage from 0% to 60%+."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import sys
import os
import types
import asyncio

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
from discord.ext import commands


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _evict_discord_modules():
    """Remove cached discord/source modules so they re-import with real discord."""
    to_evict = [
        k for k in sys.modules
        if k == "discord" or k.startswith("discord.")
        or k in ("api", "bot", "utils") or k.startswith("api.")
        or k.startswith("utils.") or k.startswith("cogs.")
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


def _make_mock_bot_with_loop():
    """Create a mock bot that has a working loop.create_task."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    # loop.create_task should accept a coroutine — use MagicMock
    bot.loop = MagicMock()
    bot.loop.create_task = MagicMock()
    # wait_until_ready is already AsyncMock from create_mock_bot
    return bot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
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
    """Tests for _preload_data method."""

    def test_preload_data_success(self, mock_about_cog):
        """_preload_data should load categories and objects successfully."""
        categories = ["ship", "module", "primary_weapon"]

        categories_resp = MagicMock()
        categories_resp.raise_for_status = MagicMock()
        categories_resp.json.return_value = categories

        ship_objects = [{"name": "Eagle", "aliases": []}, {"name": "Hawk", "aliases": []}]
        module_objects = [{"name": "Shield", "aliases": []}]
        weapon_objects = [{"name": "Laser", "aliases": []}]

        ship_resp = MagicMock()
        ship_resp.raise_for_status = MagicMock()
        ship_resp.json.return_value = ship_objects

        module_resp = MagicMock()
        module_resp.raise_for_status = MagicMock()
        module_resp.json.return_value = module_objects

        weapon_resp = MagicMock()
        weapon_resp.raise_for_status = MagicMock()
        weapon_resp.json.return_value = weapon_objects

        mock_about_cog.http_client.get = AsyncMock(
            side_effect=[categories_resp, ship_resp, module_resp, weapon_resp]
        )
        # wait_until_ready returns immediately
        mock_about_cog.bot.wait_until_ready = AsyncMock()

        asyncio.run(mock_about_cog._preload_data())

        assert mock_about_cog._categories == categories
        assert "ship" in mock_about_cog._objects_by_category
        assert len(mock_about_cog._objects_by_category["ship"]) == 2

    def test_preload_data_api_failure(self, mock_about_cog):
        """_preload_data should handle API failure gracefully."""
        import httpx
        mock_about_cog.http_client.get = AsyncMock(
            side_effect=httpx.HTTPError("connection refused")
        )
        mock_about_cog.bot.wait_until_ready = AsyncMock()

        # Should not raise — failure is caught internally
        asyncio.run(mock_about_cog._preload_data())

        # On failure, categories should be reset to empty
        assert mock_about_cog._categories == []
        assert mock_about_cog._objects_by_category == {}

    def test_preload_data_category_object_failure(self, mock_about_cog):
        """_preload_data should handle per-category failure gracefully."""
        categories = ["ship", "module"]

        categories_resp = MagicMock()
        categories_resp.raise_for_status = MagicMock()
        categories_resp.json.return_value = categories

        ship_resp = MagicMock()
        ship_resp.raise_for_status = MagicMock()
        ship_resp.json.return_value = [{"name": "Eagle", "aliases": []}]

        # Module category fails
        import httpx
        module_error = httpx.HTTPError("timeout")

        mock_about_cog.http_client.get = AsyncMock(
            side_effect=[categories_resp, ship_resp, module_error]
        )
        mock_about_cog.bot.wait_until_ready = AsyncMock()

        asyncio.run(mock_about_cog._preload_data())

        # Categories should be loaded
        assert mock_about_cog._categories == categories
        # Ship objects should be loaded
        assert len(mock_about_cog._objects_by_category["ship"]) == 1
        # Module objects should be empty list (fallback)
        assert mock_about_cog._objects_by_category["module"] == []


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
        mock_about_cog._objects_by_category = {
            "ship": [{"name": "Eagle"}, {"name": "Hawk"}]
        }
        interaction = _create_mock_interaction()
        # namespace has no category attribute
        interaction.namespace = MagicMock(spec=[])

        result = asyncio.run(mock_about_cog.object_autocomplete(interaction, ""))

        assert result == []

    def test_object_autocomplete_valid_category(self, mock_about_cog):
        """object_autocomplete should return objects for selected category."""
        mock_about_cog._objects_by_category = {
            "ship": [{"name": "Eagle"}, {"name": "Hawk"}, {"name": "Falcon"}]
        }
        interaction = _create_mock_interaction()
        interaction.namespace = MagicMock()
        interaction.namespace.category = "ship"

        result = asyncio.run(mock_about_cog.object_autocomplete(interaction, ""))

        assert len(result) == 3

    def test_object_autocomplete_partial_match(self, mock_about_cog):
        """object_autocomplete should filter by partial match."""
        mock_about_cog._objects_by_category = {
            "ship": [{"name": "Eagle"}, {"name": "Hawk"}, {"name": "Falcon"}]
        }
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
        mock_about_cog._objects_by_category = {
            "ship": [{"name": "Eagle", "aliases": []}]
        }

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

            asyncio.run(mock_about_cog.about.callback(
                mock_about_cog, interaction, "ship", "Eagle"
            ))

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
        http_error = httpx.HTTPStatusError(
            "404 Not Found", request=MagicMock(), response=error_response
        )
        mock_about_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_about_cog.about.callback(
            mock_about_cog, interaction, "ship", "NonExistentShip"
        ))

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
        http_error = httpx.HTTPStatusError(
            "500 Server Error", request=MagicMock(), response=error_response
        )
        mock_about_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_about_cog.about.callback(
            mock_about_cog, interaction, "ship", "Eagle"
        ))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_about_generic_exception(self, mock_about_cog):
        """about should handle generic exceptions gracefully."""
        interaction = _create_mock_interaction()

        mock_about_cog._categories = ["ship"]
        mock_about_cog._objects_by_category = {"ship": []}

        mock_about_cog.http_client.get = AsyncMock(
            side_effect=RuntimeError("network failure")
        )

        asyncio.run(mock_about_cog.about.callback(
            mock_about_cog, interaction, "ship", "Eagle"
        ))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_about_resolves_alias(self, mock_about_cog):
        """about should resolve an alias to the canonical name."""
        interaction = _create_mock_interaction()

        mock_about_cog._categories = ["ship"]
        mock_about_cog._objects_by_category = {
            "ship": [{"name": "Eagle", "aliases": ["Eagleship", "TheEagle"]}]
        }

        obj_resp = MagicMock()
        obj_resp.raise_for_status = MagicMock()
        obj_resp.json.return_value = _make_object_data("Eagle", "ship")
        mock_about_cog.http_client.get = AsyncMock(return_value=obj_resp)

        with patch("cogs.aboutCog.EmbedConverter") as mock_converter:
            mock_payload = MagicMock()
            mock_converter.embed_to_payload.return_value = mock_payload
            mock_embed = MagicMock(spec=discord.Embed)
            mock_converter.payload_to_grid_embed.return_value = mock_embed

            asyncio.run(mock_about_cog.about.callback(
                mock_about_cog, interaction, "ship", "TheEagle"
            ))

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

        asyncio.run(mock_about_cog.list_category.callback(
            mock_about_cog, interaction, "ship"
        ))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_list_category_not_found(self, mock_about_cog):
        """list_category with unknown category should send error."""
        interaction = _create_mock_interaction()

        mock_about_cog._objects_by_category = {}

        asyncio.run(mock_about_cog.list_category.callback(
            mock_about_cog, interaction, "unknown_category"
        ))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "not found" in call_kwargs[0][0].lower()

    def test_list_category_empty_category(self, mock_about_cog):
        """list_category with empty category should send ephemeral message."""
        interaction = _create_mock_interaction()

        mock_about_cog._objects_by_category = {"ship": []}

        asyncio.run(mock_about_cog.list_category.callback(
            mock_about_cog, interaction, "ship"
        ))

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
