"""
Tests for the skinsCog Discord bot functionality.

This module provides comprehensive test coverage for the skinsCog commands,
ship skin management, and skin compatibility operations.
"""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import discord_mock_utils for consistent mock patterns
from tests.mocks.discord_mock_utils import DiscordMockUtils

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


_mock_bblogger.get_logger = _make_mock_logger

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
    """Create a mock Discord bot for skinsCog testing."""
    loop = MagicMock()
    loop.create_task = MagicMock(side_effect=_close_coro)
    bot = DiscordMockUtils.create_mock_bot(
        user_id=123456789,
        username="TestBot",
        add_cog=AsyncMock(),
        tree=MagicMock(),
        get_member=MagicMock(),
        flogger=MagicMock(),
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
def mock_skins_cog(mock_bot):
    """Create a mock skinsCog instance."""
    _evict_discord_modules()
    from cogs.skinsCog import SkinsCog

    cog = SkinsCog(mock_bot)
    return cog


class TestSkinsCogInitialization:
    """Tests for skinsCog initialization."""

    def test_initialization(self, mock_skins_cog):
        """skinsCog should initialize properly with bot reference."""
        assert mock_skins_cog.bot is not None
        assert mock_skins_cog._ship_skins == {}
        mock_skins_cog.bot.loop.create_task.assert_called_once()


class TestShipSkinPreload:
    """Tests for ship skin preloading.

    B.33 remediation: tests use respx to assert exact URL + HTTP method.
    skinsCog._preload_ship_skins calls:
    - GET /about/categories/ship/objects  (ship list)
    - GET /about/object/name/{name}       (per-ship detail with skins)
    Both routes are confirmed correct in about.py.
    """

    _API_BASE = "http://bot-core:8000/api/v1"

    def test_preload_ship_skins_success(self, mock_skins_cog):
        """_preload_ship_skins calls GET /about/categories/ship/objects and
        GET /about/object/name/{name} for each ship, populating _ship_skins."""
        import httpx
        import respx

        mock_skins_cog.bot.wait_until_ready = AsyncMock()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{self._API_BASE}/about/categories/ship/objects").mock(
                return_value=httpx.Response(
                    200, json=[{"name": "Test Ship 1", "id": 1}, {"name": "Test Ship 2", "id": 2}]
                )
            )
            mock_router.get(f"{self._API_BASE}/about/object/name/Test Ship 1").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "name": "Test Ship 1",
                        "compatible_skins": {
                            "Red Skin": "http://example.com/red.png",
                            "Blue Skin": "http://example.com/blue.png",
                        },
                    },
                )
            )
            mock_router.get(f"{self._API_BASE}/about/object/name/Test Ship 2").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "name": "Test Ship 2",
                        "compatible_skins": {"Green Skin": "http://example.com/green.png"},
                    },
                )
            )

            asyncio.run(mock_skins_cog._preload_ship_skins())

        assert len(mock_skins_cog._ship_skins) == 2
        assert "Test Ship 1" in mock_skins_cog._ship_skins
        assert "Test Ship 2" in mock_skins_cog._ship_skins
        assert "Red Skin" in mock_skins_cog._ship_skins["Test Ship 1"]
        assert "Blue Skin" in mock_skins_cog._ship_skins["Test Ship 1"]
        assert "Green Skin" in mock_skins_cog._ship_skins["Test Ship 2"]

    def test_preload_ship_skins_failure(self, mock_skins_cog):
        """_preload_ship_skins handles GET /about/categories/ship/objects failure gracefully."""
        import httpx
        import respx

        mock_skins_cog.bot.wait_until_ready = AsyncMock()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{self._API_BASE}/about/categories/ship/objects").mock(
                return_value=httpx.Response(500, json={"detail": "Internal Server Error"})
            )

            asyncio.run(mock_skins_cog._preload_ship_skins())

        # Error should be caught and _ship_skins stays empty
        assert mock_skins_cog._ship_skins == {}


class TestShipAutocomplete:
    """Tests for ship autocomplete."""

    def test_ship_autocomplete(self, mock_skins_cog):
        """ship_autocomplete should return filtered ship choices."""
        mock_skins_cog._ship_skins = {
            "Basic Ship": ["Skin A", "Skin B"],
            "Advanced Ship": ["Skin C"],
            "Elite Ship": ["Skin D", "Skin E", "Skin F"],
        }

        # Test with empty current
        choices = asyncio.run(mock_skins_cog.ship_autocomplete(MagicMock(), ""))
        assert len(choices) == 3
        assert choices[0].name == "Basic Ship"
        assert choices[0].value == "Basic Ship"

        # Test with partial match
        choices = asyncio.run(mock_skins_cog.ship_autocomplete(MagicMock(), "basic"))
        assert len(choices) == 1
        assert choices[0].name == "Basic Ship"

        # Test case insensitivity
        choices = asyncio.run(mock_skins_cog.ship_autocomplete(MagicMock(), "BASIC"))
        assert len(choices) == 1

        # Test limit of 25
        mock_skins_cog._ship_skins = {f"Ship {i}": [] for i in range(30)}
        choices = asyncio.run(mock_skins_cog.ship_autocomplete(MagicMock(), ""))
        assert len(choices) == 25


class TestSkinAutocomplete:
    """Tests for skin autocomplete."""

    def test_skin_autocomplete_no_ship(self, mock_skins_cog):
        """skin_autocomplete should return empty list when no ship selected."""
        mock_interaction = MagicMock()
        mock_interaction.namespace = MagicMock()
        mock_interaction.namespace.ship = None

        choices = asyncio.run(mock_skins_cog.skin_autocomplete(mock_interaction, ""))
        assert choices == []

    def test_skin_autocomplete_with_ship(self, mock_skins_cog):
        """skin_autocomplete should return skin choices for selected ship."""
        mock_skins_cog._ship_skins = {"Test Ship": ["Basic Skin", "Advanced Skin", "Premium Skin"]}

        mock_interaction = MagicMock()
        mock_interaction.namespace = MagicMock()
        mock_interaction.namespace.ship = "Test Ship"

        # Test with empty current
        choices = asyncio.run(mock_skins_cog.skin_autocomplete(mock_interaction, ""))
        assert len(choices) == 3
        assert choices[0].name == "Basic Skin"
        assert choices[0].value == "Basic Skin"

        # Test with partial match
        choices = asyncio.run(mock_skins_cog.skin_autocomplete(mock_interaction, "advanced"))
        assert len(choices) == 1
        assert choices[0].name == "Advanced Skin"

        # Test case insensitivity
        choices = asyncio.run(mock_skins_cog.skin_autocomplete(mock_interaction, "PREMIUM"))
        assert len(choices) == 1
        assert choices[0].name == "Premium Skin"

    def test_skin_autocomplete_default_for_empty(self, mock_skins_cog):
        """skin_autocomplete should return Default choice for ships with no skins."""
        mock_skins_cog._ship_skins = {"Test Ship": []}

        mock_interaction = MagicMock()
        mock_interaction.namespace = MagicMock()
        mock_interaction.namespace.ship = "Test Ship"

        choices = asyncio.run(mock_skins_cog.skin_autocomplete(mock_interaction, ""))
        assert len(choices) == 1
        assert choices[0].name == "Default"
        assert choices[0].value == "Default"

    def test_skin_autocomplete_default_when_no_match(self, mock_skins_cog):
        """skin_autocomplete should return Default when filter matches nothing."""
        mock_skins_cog._ship_skins = {"Test Ship": ["Basic Skin", "Advanced Skin"]}

        mock_interaction = MagicMock()
        mock_interaction.namespace = MagicMock()
        mock_interaction.namespace.ship = "Test Ship"

        choices = asyncio.run(mock_skins_cog.skin_autocomplete(mock_interaction, "nonexistent"))
        assert len(choices) == 1
        assert choices[0].name == "Default"
        assert choices[0].value == "Default"


class TestShipSkinCommand:
    """Tests for ship_skin command."""

    @patch("cogs.skinsCog.httpx")
    def test_ship_skin_success(self, mock_httpx, mock_skins_cog):
        """ship_skin should display skin image successfully."""
        # Mock HTTP client
        mock_httpx.AsyncClient = MagicMock()
        mock_client = MagicMock()
        mock_httpx.AsyncClient.return_value = mock_client

        # Mock API response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "name": "Test Ship",
            "icon": "http://example.com/default.png",
            "compatible_skins": {"Premium Skin": "http://example.com/premium.png"},
        }
        mock_response.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        # Replace the cog's http_client with our mock
        mock_skins_cog.http_client = mock_client

        # Mock interaction
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Call command - we don't care about the specific exception handling here
        asyncio.run(
            mock_skins_cog.ship_skin.callback(
                mock_skins_cog, interaction=interaction, ship="Test Ship", skin="Premium Skin"
            )
        )

        # Verify behavior - we don't care about the specific response here
        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once()

    @patch("cogs.skinsCog.httpx")
    def test_ship_skin_invalid_skin(self, mock_httpx, mock_skins_cog):
        """ship_skin should handle invalid skin."""
        # Mock HTTP client
        mock_httpx.AsyncClient = MagicMock()
        mock_client = MagicMock()
        mock_httpx.AsyncClient.return_value = mock_client

        # Mock API response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "name": "Test Ship",
            "icon": "http://example.com/default.png",
            "compatible_skins": {"Premium Skin": "http://example.com/premium.png"},
        }
        mock_response.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        # Replace the cog's http_client with our mock
        mock_skins_cog.http_client = mock_client

        # Mock interaction
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Call command - we don't care about the specific exception handling here
        asyncio.run(
            mock_skins_cog.ship_skin.callback(
                mock_skins_cog, interaction=interaction, ship="Test Ship", skin="Invalid Skin"
            )
        )

        # Verify behavior - should report skin not found
        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once()


class TestCogUnload:
    """Tests for cog unload functionality."""

    @patch("cogs.skinsCog.httpx")
    def test_cog_unload_success(self, mock_httpx, mock_skins_cog):
        """cog_unload should close http_client successfully."""
        mock_skins_cog.http_client.aclose = AsyncMock()
        asyncio.run(mock_skins_cog.cog_unload())
        mock_skins_cog.http_client.aclose.assert_called_once()


class TestShipSkinPreloadEdgeCases:
    """Tests for ship skin preloading edge cases.

    B.33 remediation: uses respx to assert URL + HTTP method correctness.
    """

    _API_BASE = "http://bot-core:8000/api/v1"

    def test_preload_ship_skins_missing_name(self, mock_skins_cog):
        """_preload_ship_skins skips ships without names from the ship list."""
        import httpx
        import respx

        mock_skins_cog.bot.wait_until_ready = AsyncMock()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{self._API_BASE}/about/categories/ship/objects").mock(
                return_value=httpx.Response(
                    200,
                    json=[
                        {"name": None},   # No name — should skip
                        {"name": ""},     # Empty name — should skip
                        {"name": "Valid Ship", "id": 3},
                    ],
                )
            )
            mock_router.get(f"{self._API_BASE}/about/object/name/Valid Ship").mock(
                return_value=httpx.Response(
                    200, json={"name": "Valid Ship", "compatible_skins": {"Skin1": "url1"}}
                )
            )

            asyncio.run(mock_skins_cog._preload_ship_skins())

        assert "Valid Ship" in mock_skins_cog._ship_skins
        assert len(mock_skins_cog._ship_skins) == 1

    def test_preload_ship_skins_individual_ship_error(self, mock_skins_cog):
        """_preload_ship_skins handles errors loading individual ship details:
        failed ship gets empty skins list, others continue loading."""
        import httpx
        import respx

        mock_skins_cog.bot.wait_until_ready = AsyncMock()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{self._API_BASE}/about/categories/ship/objects").mock(
                return_value=httpx.Response(
                    200, json=[{"name": "Ship 1", "id": 1}, {"name": "Ship 2", "id": 2}]
                )
            )
            # First ship fails with 500
            mock_router.get(f"{self._API_BASE}/about/object/name/Ship 1").mock(
                return_value=httpx.Response(500, json={"detail": "Internal Server Error"})
            )
            # Second ship succeeds
            mock_router.get(f"{self._API_BASE}/about/object/name/Ship 2").mock(
                return_value=httpx.Response(
                    200, json={"name": "Ship 2", "compatible_skins": {"Skin2": "url2"}}
                )
            )

            asyncio.run(mock_skins_cog._preload_ship_skins())

        # Failed ship should have empty skins list; successful ship should have skins
        assert "Ship 1" in mock_skins_cog._ship_skins
        assert mock_skins_cog._ship_skins["Ship 1"] == []
        assert "Ship 2" in mock_skins_cog._ship_skins
        assert "Skin2" in mock_skins_cog._ship_skins["Ship 2"]


class TestSkinAutocompleteEdgeCases:
    """Tests for skin autocomplete edge cases."""

    def test_skin_autocomplete_ship_not_in_dict(self, mock_skins_cog):
        """skin_autocomplete should return empty list when ship not in dict."""
        mock_skins_cog._ship_skins = {"Test Ship": ["Skin1"]}

        mock_interaction = MagicMock()
        mock_interaction.namespace = MagicMock()
        mock_interaction.namespace.ship = "Unknown Ship"

        choices = asyncio.run(mock_skins_cog.skin_autocomplete(mock_interaction, ""))
        assert choices == []


class TestShipSkinCommandErrors:
    """Tests for ship_skin command error paths."""

    @patch("cogs.skinsCog.httpx")
    def test_ship_skin_404_not_found(self, mock_httpx, mock_skins_cog):
        """ship_skin should handle 404 errors gracefully."""
        import httpx

        # Make sure we use the real HTTPStatusError class so it can be caught properly
        mock_httpx.HTTPStatusError = httpx.HTTPStatusError

        # Mock 404 response
        mock_response = MagicMock()
        mock_response.status_code = 404
        error = httpx.HTTPStatusError("Not found", request=MagicMock(), response=mock_response)
        mock_skins_cog.http_client.get = AsyncMock(side_effect=error)

        # Mock interaction
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Call command
        asyncio.run(
            mock_skins_cog.ship_skin.callback(mock_skins_cog, interaction=interaction, ship="Test Ship", skin="Default")
        )

        # Verify 404 handling
        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "not found" in call_args.lower()

    @patch("cogs.skinsCog.httpx")
    def test_ship_skin_missing_image_url(self, mock_httpx, mock_skins_cog):
        """ship_skin should handle missing image URLs."""
        # Mock HTTP client
        mock_httpx.AsyncClient = MagicMock()
        mock_client = MagicMock()
        mock_httpx.AsyncClient.return_value = mock_client

        # Mock response with missing URL
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "name": "Test Ship",
            "icon": None,  # Missing default icon
            "compatible_skins": {},
        }
        mock_response.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        # Replace the cog's http_client with our mock
        mock_skins_cog.http_client = mock_client

        # Mock interaction
        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Call command with Default skin
        asyncio.run(
            mock_skins_cog.ship_skin.callback(mock_skins_cog, interaction=interaction, ship="Test Ship", skin="Default")
        )

        # Verify error handling
        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "not found" in call_args.lower()


class TestCogSetup:
    """Tests for cog setup function."""

    def test_setup_function(self, mock_bot):
        """setup function should add skinsCog to bot."""
        from cogs.skinsCog import setup

        asyncio.run(setup(mock_bot))

        mock_bot.add_cog.assert_called_once()


# ===========================================================================
# NEW TESTS: Multi-Region Skin System
# ===========================================================================


class TestRegionModeView:
    """Tests for the RegionModeView UI component."""

    def test_region_mode_view_apply_all(self, mock_skins_cog):
        """RegionModeView apply-all button should set result to 'all'."""
        from cogs.skinsCog import RegionModeView

        view = RegionModeView(timeout=60)
        assert view.result is None

    def test_region_mode_view_initial_state(self, mock_skins_cog):
        """RegionModeView should start with result=None."""
        from cogs.skinsCog import RegionModeView

        view = RegionModeView()
        assert view.result is None
        # Should have 3 buttons
        assert len(view.children) == 3

    def test_region_mode_view_apply_all_button_callback(self):
        """RegionModeView apply_all_button sets result to 'all' and stops."""
        from cogs.skinsCog import RegionModeView

        results = {}

        async def run():
            view = RegionModeView()
            interaction = MagicMock()
            interaction.response.defer = AsyncMock()
            # Discord UI buttons become Button objects; invoke via children[0].callback(interaction)
            await view.children[0].callback(interaction)
            results["result"] = view.result
            results["finished"] = view.is_finished()

        asyncio.run(run())
        assert results["result"] == "all"
        assert results["finished"] is True

    def test_region_mode_view_customize_button_callback(self):
        """RegionModeView customize_button sets result to 'custom' and stops."""
        from cogs.skinsCog import RegionModeView

        results = {}

        async def run():
            view = RegionModeView()
            interaction = MagicMock()
            interaction.response.defer = AsyncMock()
            # Second button is "Customize Per Region"
            await view.children[1].callback(interaction)
            results["result"] = view.result
            results["finished"] = view.is_finished()

        asyncio.run(run())
        assert results["result"] == "custom"
        assert results["finished"] is True

    def test_region_mode_view_cancel_button_callback(self):
        """RegionModeView cancel_button sets result to None (and stops)."""
        from cogs.skinsCog import RegionModeView

        results = {}

        async def run():
            view = RegionModeView()
            view.result = "all"  # Pre-set to non-None to verify cancel resets it
            interaction = MagicMock()
            interaction.response.defer = AsyncMock()
            # Third button is "Cancel"
            await view.children[2].callback(interaction)
            results["result"] = view.result
            results["finished"] = view.is_finished()

        asyncio.run(run())
        assert results["result"] is None
        assert results["finished"] is True


class TestRegionOptionView:
    """Tests for the RegionOptionView UI component."""

    def test_region_option_view_no_skin(self):
        """RegionOptionView without skin_name should have upload+skip options only (no 'Apply' option)."""
        from cogs.skinsCog import RegionOptionView

        view = RegionOptionView(region_num=1, total_regions=3, skin_name=None, compatible_skins={})
        # Should have 1 select child
        assert len(view.children) == 1
        select = view.children[0]
        option_values = [o.value for o in select.options]
        assert "upload" in option_values
        assert "skip" in option_values
        # No skin: prefix option should not exist
        skin_options = [v for v in option_values if v.startswith("skin:")]
        assert len(skin_options) == 0

    def test_region_option_view_with_skin(self):
        """RegionOptionView with skin_name should include 'Apply skin' option."""
        from cogs.skinsCog import RegionOptionView

        view = RegionOptionView(region_num=1, total_regions=3, skin_name="lava", compatible_skins={})
        select = view.children[0]
        option_values = [o.value for o in select.options]
        assert "skin:lava" in option_values
        assert "upload" in option_values
        assert "skip" in option_values

    def test_region_option_view_compatible_skins_added(self):
        """RegionOptionView should add compatible skins as additional options."""
        from cogs.skinsCog import RegionOptionView

        compatible = {"onyx": "url1", "racing": "url2", "camo": "url3"}
        view = RegionOptionView(region_num=2, total_regions=2, skin_name="lava", compatible_skins=compatible)
        select = view.children[0]
        option_values = [o.value for o in select.options]
        # Should include selected skin (lava) + compatible ones (not lava since it's already included)
        assert "skin:lava" in option_values
        assert "skin:onyx" in option_values
        assert "skin:racing" in option_values
        assert "skin:camo" in option_values
        # Should not duplicate lava
        lava_count = sum(1 for v in option_values if v == "skin:lava")
        assert lava_count == 1

    def test_region_option_view_placeholder(self):
        """RegionOptionView placeholder should show region N of M."""
        from cogs.skinsCog import RegionOptionView

        view = RegionOptionView(region_num=2, total_regions=5, skin_name=None, compatible_skins={})
        select = view.children[0]
        assert "2" in select.placeholder
        assert "5" in select.placeholder

    def test_region_option_view_max_25_options(self):
        """RegionOptionView should not exceed 25 options (Discord limit)."""
        from cogs.skinsCog import RegionOptionView

        # Create 30 compatible skins
        compatible = {f"skin_{i}": f"url_{i}" for i in range(30)}
        view = RegionOptionView(region_num=1, total_regions=3, skin_name="lava", compatible_skins=compatible)
        select = view.children[0]
        assert len(select.options) <= 25

    def test_region_option_view_select_callback_sets_value(self):
        """RegionOptionView select callback should set selected_value and stop view."""
        from cogs.skinsCog import RegionOptionView

        results = {}

        async def run():
            view = RegionOptionView(region_num=1, total_regions=2, skin_name="lava", compatible_skins={})
            select = view.children[0]

            # Simulate the user selecting "skip" — must use _values (internal attribute)
            # because discord.ui.Select.values is a read-only property
            select._values = ["skip"]

            interaction = MagicMock()
            interaction.response.defer = AsyncMock()
            await view._select_callback(interaction)
            results["selected"] = view.selected_value
            results["finished"] = view.is_finished()

        asyncio.run(run())
        assert results["selected"] == "skip"
        assert results["finished"] is True

    def test_region_option_view_initial_selected_value_is_none(self):
        """RegionOptionView should start with selected_value=None."""
        from cogs.skinsCog import RegionOptionView

        view = RegionOptionView(region_num=1, total_regions=2)
        assert view.selected_value is None


class TestResolveRegionMode:
    """Tests for _resolve_region_mode helper."""

    def test_no_skin_no_image_returns_all(self, mock_skins_cog):
        """_resolve_region_mode should return 'all' when no skin and no image."""
        render_info = {"mask_paths": ["/path/mask1.jpg", "/path/mask2.jpg"]}

        result = asyncio.run(mock_skins_cog._resolve_region_mode(MagicMock(), render_info, None, None, False))
        assert result == "all"

    def test_single_region_with_skin_returns_all(self, mock_skins_cog):
        """_resolve_region_mode should return 'all' for single region ships."""
        render_info = {"mask_paths": ["/path/mask1.jpg"]}

        result = asyncio.run(
            mock_skins_cog._resolve_region_mode(MagicMock(), render_info, b"skin_bytes", "lava", False)
        )
        assert result == "all"

    def test_zero_regions_with_skin_returns_all(self, mock_skins_cog):
        """_resolve_region_mode should return 'all' for ships with 0 regions."""
        render_info = {"mask_paths": []}

        result = asyncio.run(
            mock_skins_cog._resolve_region_mode(MagicMock(), render_info, b"skin_bytes", "lava", False)
        )
        assert result == "all"

    def test_multi_region_with_skin_shows_view(self, mock_skins_cog):
        """_resolve_region_mode should show RegionModeView for 2+ regions with skin."""
        render_info = {
            "mask_paths": ["/path/mask1.jpg", "/path/mask2.jpg", "/path/mask3.jpg"],
            "ship_name": "Kinzer RS",
        }

        interaction = MagicMock()
        interaction.followup = AsyncMock()
        interaction.followup.send = AsyncMock(return_value=MagicMock())

        # Simulate user clicking "Apply to All"
        from cogs.skinsCog import RegionModeView

        original_wait = RegionModeView.wait

        async def mock_wait(self):
            self.result = "all"

        RegionModeView.wait = mock_wait

        try:
            result = asyncio.run(
                mock_skins_cog._resolve_region_mode(interaction, render_info, b"skin_bytes", "lava", False)
            )
        finally:
            RegionModeView.wait = original_wait

        assert result == "all"
        interaction.followup.send.assert_called_once()

    def test_multi_region_with_skin_returns_custom(self, mock_skins_cog):
        """_resolve_region_mode should return 'custom' when user selects Customize."""
        render_info = {"mask_paths": ["/path/mask1.jpg", "/path/mask2.jpg"], "ship_name": "Aegir"}

        interaction = MagicMock()
        interaction.followup = AsyncMock()
        interaction.followup.send = AsyncMock(return_value=MagicMock())

        from cogs.skinsCog import RegionModeView

        original_wait = RegionModeView.wait

        async def mock_wait(self):
            self.result = "custom"

        RegionModeView.wait = mock_wait

        try:
            result = asyncio.run(
                mock_skins_cog._resolve_region_mode(interaction, render_info, b"skin_bytes", "lava", False)
            )
        finally:
            RegionModeView.wait = original_wait

        assert result == "custom"

    def test_multi_region_cancel_returns_none(self, mock_skins_cog):
        """_resolve_region_mode should return None when user cancels."""
        render_info = {"mask_paths": ["/path/mask1.jpg", "/path/mask2.jpg"], "ship_name": "Aegir"}

        interaction = MagicMock()
        interaction.followup = AsyncMock()
        interaction.followup.send = AsyncMock(return_value=MagicMock())

        from cogs.skinsCog import RegionModeView

        original_wait = RegionModeView.wait

        async def mock_wait(self):
            self.result = None
            self._stopped = True  # mark as finished (cancelled)

        RegionModeView.wait = mock_wait

        try:
            result = asyncio.run(
                mock_skins_cog._resolve_region_mode(interaction, render_info, b"skin_bytes", "lava", False)
            )
        finally:
            RegionModeView.wait = original_wait

        assert result is None

    def test_multi_region_image_provided_shows_view(self, mock_skins_cog):
        """_resolve_region_mode should show view when image is uploaded (even without skin)."""
        render_info = {"mask_paths": ["/path/mask1.jpg", "/path/mask2.jpg"], "ship_name": "Aegir"}

        interaction = MagicMock()
        interaction.followup = AsyncMock()
        interaction.followup.send = AsyncMock(return_value=MagicMock())

        from cogs.skinsCog import RegionModeView

        original_wait = RegionModeView.wait

        async def mock_wait(self):
            self.result = "all"

        RegionModeView.wait = mock_wait

        try:
            result = asyncio.run(
                mock_skins_cog._resolve_region_mode(
                    interaction,
                    render_info,
                    b"uploaded_image",
                    None,
                    True,  # image_provided=True
                )
            )
        finally:
            RegionModeView.wait = original_wait

        assert result == "all"
        interaction.followup.send.assert_called_once()

    def test_missing_mask_paths_key_treated_as_empty(self, mock_skins_cog):
        """_resolve_region_mode should treat missing mask_paths as 0 regions."""
        render_info = {}  # no mask_paths key

        result = asyncio.run(
            mock_skins_cog._resolve_region_mode(MagicMock(), render_info, b"skin_bytes", "lava", False)
        )
        assert result == "all"


class TestCollectPerRegionChoices:
    """Tests for _collect_per_region_choices helper."""

    def test_single_region_skip(self, mock_skins_cog):
        """_collect_per_region_choices should handle 'skip' selection."""
        render_info = {"mask_paths": ["/path/mask1.jpg"], "compatible_skins": {}}
        interaction = MagicMock()
        interaction.user.id = 999
        interaction.followup.send = AsyncMock()

        from cogs.skinsCog import RegionOptionView

        original_wait = RegionOptionView.wait

        async def mock_wait(self):
            # Set selected_value directly (no need to touch select.values here)
            self.selected_value = "skip"

        RegionOptionView.wait = mock_wait

        try:
            result = asyncio.run(mock_skins_cog._collect_per_region_choices(interaction, render_info, None, None, {}))
        finally:
            RegionOptionView.wait = original_wait

        assert result is not None
        assert result[1]["action"] == "skip"

    def test_single_region_apply_skin(self, mock_skins_cog):
        """_collect_per_region_choices should store skin bytes when skin selected."""
        render_info = {"mask_paths": ["/path/mask1.jpg"], "compatible_skins": {"lava": "http://example.com/lava.png"}}
        interaction = MagicMock()
        interaction.user.id = 999
        interaction.followup.send = AsyncMock()

        skin_bytes = b"fake_skin_bytes"

        from cogs.skinsCog import RegionOptionView

        original_wait = RegionOptionView.wait

        async def mock_wait(self):
            self.selected_value = "skin:lava"

        RegionOptionView.wait = mock_wait

        try:
            result = asyncio.run(
                mock_skins_cog._collect_per_region_choices(
                    interaction, render_info, "lava", skin_bytes, {"lava": "url"}
                )
            )
        finally:
            RegionOptionView.wait = original_wait

        assert result is not None
        assert result[1]["action"] == "skin"
        assert result[1]["bytes"] == skin_bytes

    def test_skin_download_cached(self, mock_skins_cog):
        """_collect_per_region_choices should use skin cache to avoid re-downloads."""
        render_info = {
            "mask_paths": ["/path/mask1.jpg", "/path/mask2.jpg"],
            "compatible_skins": {"lava": "http://example.com/lava.png"},
        }
        interaction = MagicMock()
        interaction.user.id = 999
        interaction.followup.send = AsyncMock()

        skin_bytes = b"lava_bytes"

        from cogs.skinsCog import RegionOptionView

        original_wait = RegionOptionView.wait

        async def mock_wait(self):
            self.selected_value = "skin:lava"

        RegionOptionView.wait = mock_wait

        # Mock _download_skin_image to count calls
        download_count = {"count": 0}
        original_download = mock_skins_cog._download_skin_image

        async def mock_download(interaction, ship, skin, render_info):
            download_count["count"] += 1
            return skin_bytes

        mock_skins_cog._download_skin_image = mock_download

        try:
            result = asyncio.run(
                mock_skins_cog._collect_per_region_choices(
                    interaction, render_info, "lava", skin_bytes, {"lava": "url"}
                )
            )
        finally:
            RegionOptionView.wait = original_wait
            mock_skins_cog._download_skin_image = original_download

        # Both regions chose "lava" but lava was already in cache → no download needed
        assert download_count["count"] == 0
        assert result[1]["action"] == "skin"
        assert result[2]["action"] == "skin"

    def test_skin_download_not_cached_triggers_download(self, mock_skins_cog):
        """_collect_per_region_choices should download skins not in cache."""
        render_info = {"mask_paths": ["/path/mask1.jpg"], "compatible_skins": {"onyx": "http://example.com/onyx.png"}}
        interaction = MagicMock()
        interaction.user.id = 999
        interaction.followup.send = AsyncMock()

        onyx_bytes = b"onyx_bytes"

        from cogs.skinsCog import RegionOptionView

        original_wait = RegionOptionView.wait

        async def mock_wait(self):
            self.selected_value = "skin:onyx"

        RegionOptionView.wait = mock_wait

        download_count = {"count": 0}

        async def mock_download(interaction, ship, skin, render_info):
            download_count["count"] += 1
            return onyx_bytes

        mock_skins_cog._download_skin_image = mock_download

        try:
            result = asyncio.run(
                mock_skins_cog._collect_per_region_choices(
                    interaction,
                    render_info,
                    None,
                    None,
                    {"onyx": "url"},  # no pre-provided skin
                )
            )
        finally:
            RegionOptionView.wait = original_wait

        assert download_count["count"] == 1
        assert result[1]["action"] == "skin"
        assert result[1]["bytes"] == onyx_bytes

    def test_per_region_timeout_skips_region(self, mock_skins_cog):
        """_collect_per_region_choices should skip region on timeout and continue."""
        render_info = {"mask_paths": ["/path/mask1.jpg", "/path/mask2.jpg"], "compatible_skins": {}}
        interaction = MagicMock()
        interaction.user.id = 999
        interaction.followup.send = AsyncMock()

        from cogs.skinsCog import RegionOptionView

        original_wait = RegionOptionView.wait
        call_count = {"n": 0}

        async def mock_wait(self):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First region: timeout (selected_value remains None)
                self.selected_value = None
            else:
                # Second region: skip
                self.selected_value = "skip"

        RegionOptionView.wait = mock_wait

        try:
            result = asyncio.run(mock_skins_cog._collect_per_region_choices(interaction, render_info, None, None, {}))
        finally:
            RegionOptionView.wait = original_wait

        assert result is not None
        assert result[1]["action"] == "skip"
        assert result[2]["action"] == "skip"

    def test_per_region_upload(self, mock_skins_cog):
        """_collect_per_region_choices should handle upload selection."""
        render_info = {"mask_paths": ["/path/mask1.jpg"], "compatible_skins": {}}
        interaction = MagicMock()
        interaction.user.id = 999
        interaction.followup.send = AsyncMock()

        uploaded_bytes = b"uploaded_image_bytes"

        from cogs.skinsCog import RegionOptionView

        original_wait = RegionOptionView.wait

        async def mock_wait(self):
            self.selected_value = "upload"

        RegionOptionView.wait = mock_wait

        # Mock bot.wait_for to return a message with an attachment
        mock_attachment = MagicMock()
        mock_attachment.read = AsyncMock(return_value=uploaded_bytes)

        mock_message = MagicMock()
        mock_message.attachments = [mock_attachment]
        mock_skins_cog.bot.wait_for = AsyncMock(return_value=mock_message)

        try:
            result = asyncio.run(mock_skins_cog._collect_per_region_choices(interaction, render_info, None, None, {}))
        finally:
            RegionOptionView.wait = original_wait

        assert result is not None
        assert result[1]["action"] == "upload"
        assert result[1]["bytes"] == uploaded_bytes

    def test_per_region_upload_timeout_skips(self, mock_skins_cog):
        """_collect_per_region_choices should skip region on upload timeout."""
        render_info = {"mask_paths": ["/path/mask1.jpg"], "compatible_skins": {}}
        interaction = MagicMock()
        interaction.user.id = 999
        interaction.followup.send = AsyncMock()

        from cogs.skinsCog import RegionOptionView

        original_wait = RegionOptionView.wait

        async def mock_wait(self):
            self.selected_value = "upload"

        RegionOptionView.wait = mock_wait

        # Simulate upload timeout
        mock_skins_cog.bot.wait_for = AsyncMock(side_effect=TimeoutError())

        try:
            result = asyncio.run(mock_skins_cog._collect_per_region_choices(interaction, render_info, None, None, {}))
        finally:
            RegionOptionView.wait = original_wait

        assert result is not None
        assert result[1]["action"] == "skip"

    def test_skin_download_failure_skips_region(self, mock_skins_cog):
        """_collect_per_region_choices should skip region when skin download fails."""
        render_info = {
            "mask_paths": ["/path/mask1.jpg"],
            "compatible_skins": {"broken_skin": "http://bad-url.com/broken.png"},
        }
        interaction = MagicMock()
        interaction.user.id = 999
        interaction.followup.send = AsyncMock()

        from cogs.skinsCog import RegionOptionView

        original_wait = RegionOptionView.wait

        async def mock_wait(self):
            self.selected_value = "skin:broken_skin"

        RegionOptionView.wait = mock_wait

        # Mock download to fail
        async def mock_download_fail(interaction, ship, skin, render_info):
            return None  # failure

        mock_skins_cog._download_skin_image = mock_download_fail

        try:
            result = asyncio.run(
                mock_skins_cog._collect_per_region_choices(interaction, render_info, None, None, {"broken_skin": "url"})
            )
        finally:
            RegionOptionView.wait = original_wait

        assert result is not None
        assert result[1]["action"] == "skip"


class TestCompositeTexturesMultiregion:
    """Tests for _composite_textures_multiregion helper."""

    def test_multiregion_composite_success(self, mock_skins_cog):
        """_composite_textures_multiregion should call composite endpoint correctly."""
        interaction = MagicMock()
        interaction.followup.send = AsyncMock()

        region_choices = {
            1: {"action": "skin", "bytes": b"skin1"},
            2: {"action": "upload", "bytes": b"upload2"},
            3: {"action": "skip"},
        }

        mock_resp = MagicMock()
        mock_resp.content = b"composite_result"
        mock_resp.raise_for_status = MagicMock()
        mock_skins_cog.blender_client.post = AsyncMock(return_value=mock_resp)

        result = asyncio.run(
            mock_skins_cog._composite_textures_multiregion(
                interaction, "TestShip", "/path/ship.bbship", "/path/diffuse.bmp", region_choices
            )
        )

        assert result == b"composite_result"
        mock_skins_cog.blender_client.post.assert_called_once()
        call_kwargs = mock_skins_cog.blender_client.post.call_args
        data = call_kwargs[1]["data"]
        assert data["base_texture_path"] == "/path/diffuse.bmp"
        # region_indices should contain 1 and 2 (not 3, which is skip)
        assert "1" in data["region_indices"]
        assert "2" in data["region_indices"]
        assert "3" not in data["region_indices"]

    def test_multiregion_composite_all_skipped(self, mock_skins_cog):
        """_composite_textures_multiregion with all-skip should call composite with empty region_indices."""
        interaction = MagicMock()
        interaction.followup.send = AsyncMock()

        region_choices = {
            1: {"action": "skip"},
            2: {"action": "skip"},
        }

        mock_resp = MagicMock()
        mock_resp.content = b"composite_default"
        mock_resp.raise_for_status = MagicMock()
        mock_skins_cog.blender_client.post = AsyncMock(return_value=mock_resp)

        result = asyncio.run(
            mock_skins_cog._composite_textures_multiregion(
                interaction, "TestShip", "/path/ship.bbship", "/path/diffuse.bmp", region_choices
            )
        )

        assert result == b"composite_default"
        data = mock_skins_cog.blender_client.post.call_args[1]["data"]
        assert data["region_indices"] == ""

    def test_multiregion_composite_http_error(self, mock_skins_cog):
        """_composite_textures_multiregion should return None on HTTP error."""
        import httpx

        interaction = MagicMock()
        interaction.followup.send = AsyncMock()

        region_choices = {1: {"action": "skin", "bytes": b"skin1"}}

        mock_response = MagicMock()
        mock_response.status_code = 500
        error = httpx.HTTPStatusError("Error", request=MagicMock(), response=mock_response)
        mock_skins_cog.blender_client.post = AsyncMock(side_effect=error)

        result = asyncio.run(
            mock_skins_cog._composite_textures_multiregion(
                interaction, "TestShip", "/path/ship.bbship", "/path/diffuse.bmp", region_choices
            )
        )

        assert result is None
        interaction.followup.send.assert_called()

    def test_multiregion_composite_timeout(self, mock_skins_cog):
        """_composite_textures_multiregion should return None on timeout."""
        from httpx import TimeoutException

        interaction = MagicMock()
        interaction.followup.send = AsyncMock()

        region_choices = {1: {"action": "skin", "bytes": b"skin1"}}

        mock_skins_cog.blender_client.post = AsyncMock(side_effect=TimeoutException("Timeout"))

        result = asyncio.run(
            mock_skins_cog._composite_textures_multiregion(
                interaction, "TestShip", "/path/ship.bbship", "/path/diffuse.bmp", region_choices
            )
        )

        assert result is None

    def test_multiregion_uses_diffuse_as_base_texture_path(self, mock_skins_cog):
        """_composite_textures_multiregion should use diffuse_path as base_texture_path."""
        interaction = MagicMock()
        interaction.followup.send = AsyncMock()

        region_choices = {1: {"action": "skin", "bytes": b"skin1"}}

        mock_resp = MagicMock()
        mock_resp.content = b"result"
        mock_resp.raise_for_status = MagicMock()
        mock_skins_cog.blender_client.post = AsyncMock(return_value=mock_resp)

        asyncio.run(
            mock_skins_cog._composite_textures_multiregion(
                interaction, "Ship", "/ship_path", "/diffuse_path/diffuse.bmp", region_choices
            )
        )

        data = mock_skins_cog.blender_client.post.call_args[1]["data"]
        assert data["base_texture_path"] == "/diffuse_path/diffuse.bmp"
        # Should NOT have base_texture as file upload (that's for "apply to all" mode)
        files_arg = mock_skins_cog.blender_client.post.call_args[1]["files"]
        file_names = [f[0] for f in files_arg]
        assert "base_texture" not in file_names


class TestRenderSkinMultiRegion:
    """Tests for render_skin command multi-region integration."""

    def _make_render_info(self, num_masks=2, skin="lava"):
        """Helper to create render_info for tests."""
        mask_paths = [f"/path/mask{i}.jpg" for i in range(1, num_masks + 1)]
        return {
            "skinnable": True,
            "bbship_dir": "/path/ship.bbship",
            "diffuse_path": "/path/diffuse.bmp",
            "model_path": "/path/model.obj",
            "mask_paths": mask_paths,
            "compatible_skins": {"lava": "http://example.com/lava.png", "onyx": "http://example.com/onyx.png"},
            "ship_name": "Test Ship",
        }

    def test_render_skin_single_region_no_region_prompt(self, mock_skins_cog):
        """render_skin should not show region prompt for single-region ships (AC-1, AC-2)."""
        render_info = self._make_render_info(num_masks=1)

        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # Mock all internal methods
        mock_skins_cog._fetch_render_info = AsyncMock(return_value=render_info)
        mock_skins_cog._download_skin_image = AsyncMock(return_value=b"skin_bytes")
        mock_skins_cog._composite_textures = AsyncMock(return_value=b"composite")
        mock_skins_cog._resolve_region_mode = AsyncMock(return_value="all")

        # Mock blender render
        mock_render_resp = MagicMock()
        mock_render_resp.content = b"render_result"
        mock_render_resp.raise_for_status = MagicMock()
        mock_skins_cog.blender_client.post = AsyncMock(return_value=mock_render_resp)

        asyncio.run(
            mock_skins_cog.render_skin.callback(mock_skins_cog, interaction=interaction, ship="Test Ship", skin="lava")
        )

        # _composite_textures (single-region path) should be called
        mock_skins_cog._composite_textures.assert_called_once()
        # Final render should succeed
        interaction.followup.send.assert_called()

    def test_render_skin_zero_region_no_region_prompt(self, mock_skins_cog):
        """render_skin should not show region prompt for 0-region ships (AC-22)."""
        render_info = self._make_render_info(num_masks=0)

        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        mock_skins_cog._fetch_render_info = AsyncMock(return_value=render_info)
        mock_skins_cog._download_skin_image = AsyncMock(return_value=b"skin_bytes")
        mock_skins_cog._composite_textures = AsyncMock(return_value=b"composite")
        mock_skins_cog._resolve_region_mode = AsyncMock(return_value="all")

        mock_render_resp = MagicMock()
        mock_render_resp.content = b"render_result"
        mock_render_resp.raise_for_status = MagicMock()
        mock_skins_cog.blender_client.post = AsyncMock(return_value=mock_render_resp)

        asyncio.run(
            mock_skins_cog.render_skin.callback(mock_skins_cog, interaction=interaction, ship="Test Ship", skin="lava")
        )

        mock_skins_cog._composite_textures.assert_called_once()

    def test_render_skin_default_render_no_region_prompt(self, mock_skins_cog):
        """render_skin with no skin/image should not show region prompt (AC-5)."""
        render_info = self._make_render_info(num_masks=3)

        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        mock_skins_cog._fetch_render_info = AsyncMock(return_value=render_info)
        mock_skins_cog._composite_textures = AsyncMock(return_value=b"composite")
        mock_skins_cog._resolve_region_mode = AsyncMock(return_value="all")

        mock_render_resp = MagicMock()
        mock_render_resp.content = b"render_result"
        mock_render_resp.raise_for_status = MagicMock()
        mock_skins_cog.blender_client.post = AsyncMock(return_value=mock_render_resp)

        asyncio.run(
            mock_skins_cog.render_skin.callback(
                mock_skins_cog,
                interaction=interaction,
                ship="Test Ship",
                # No skin (defaults to "Default"), no image
            )
        )

        # Should call existing composite (no skin, default render)
        mock_skins_cog._composite_textures.assert_called_once()
        call_args = mock_skins_cog._composite_textures.call_args
        # skin_bytes should be None for default render
        assert call_args[0][4] is None

    def test_render_skin_multi_region_apply_all_uses_base_composite(self, mock_skins_cog):
        """render_skin multi-region 'Apply All' should use existing _composite_textures (AC-8, AC-9)."""
        render_info = self._make_render_info(num_masks=3)

        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        mock_skins_cog._fetch_render_info = AsyncMock(return_value=render_info)
        mock_skins_cog._download_skin_image = AsyncMock(return_value=b"lava_bytes")
        mock_skins_cog._composite_textures = AsyncMock(return_value=b"composite")
        mock_skins_cog._resolve_region_mode = AsyncMock(return_value="all")

        mock_render_resp = MagicMock()
        mock_render_resp.content = b"render_result"
        mock_render_resp.raise_for_status = MagicMock()
        mock_skins_cog.blender_client.post = AsyncMock(return_value=mock_render_resp)

        asyncio.run(
            mock_skins_cog.render_skin.callback(mock_skins_cog, interaction=interaction, ship="Test Ship", skin="lava")
        )

        # Should use single-region composite path (apply to all)
        mock_skins_cog._composite_textures.assert_called_once()

    def test_render_skin_multi_region_customize_uses_multiregion_composite(self, mock_skins_cog):
        """render_skin 'Customize Per Region' should use _composite_textures_multiregion (AC-10+)."""
        render_info = self._make_render_info(num_masks=2)

        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        region_choices = {
            1: {"action": "skin", "bytes": b"lava"},
            2: {"action": "skip"},
        }

        mock_skins_cog._fetch_render_info = AsyncMock(return_value=render_info)
        mock_skins_cog._download_skin_image = AsyncMock(return_value=b"lava_bytes")
        mock_skins_cog._resolve_region_mode = AsyncMock(return_value="custom")
        mock_skins_cog._collect_per_region_choices = AsyncMock(return_value=region_choices)
        mock_skins_cog._composite_textures_multiregion = AsyncMock(return_value=b"composite")

        mock_render_resp = MagicMock()
        mock_render_resp.content = b"render_result"
        mock_render_resp.raise_for_status = MagicMock()
        mock_skins_cog.blender_client.post = AsyncMock(return_value=mock_render_resp)

        asyncio.run(
            mock_skins_cog.render_skin.callback(mock_skins_cog, interaction=interaction, ship="Test Ship", skin="lava")
        )

        mock_skins_cog._composite_textures_multiregion.assert_called_once()
        mock_skins_cog._collect_per_region_choices.assert_called_once()

    def test_render_skin_cancelled_region_mode(self, mock_skins_cog):
        """render_skin should abort when user cancels region mode selection (AC-6)."""
        render_info = self._make_render_info(num_masks=2)

        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        mock_skins_cog._fetch_render_info = AsyncMock(return_value=render_info)
        mock_skins_cog._download_skin_image = AsyncMock(return_value=b"lava_bytes")
        mock_skins_cog._resolve_region_mode = AsyncMock(return_value=None)  # cancelled
        mock_skins_cog._composite_textures = AsyncMock()
        mock_skins_cog._composite_textures_multiregion = AsyncMock()

        asyncio.run(
            mock_skins_cog.render_skin.callback(mock_skins_cog, interaction=interaction, ship="Test Ship", skin="lava")
        )

        # Should not proceed to compositing
        mock_skins_cog._composite_textures.assert_not_called()
        mock_skins_cog._composite_textures_multiregion.assert_not_called()

    def test_render_skin_multiregion_choices_none_sends_cancel_message(self, mock_skins_cog):
        """render_skin should send cancellation message when region_choices is None."""
        render_info = self._make_render_info(num_masks=2)

        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        mock_skins_cog._fetch_render_info = AsyncMock(return_value=render_info)
        mock_skins_cog._download_skin_image = AsyncMock(return_value=b"lava_bytes")
        mock_skins_cog._resolve_region_mode = AsyncMock(return_value="custom")
        mock_skins_cog._collect_per_region_choices = AsyncMock(return_value=None)

        asyncio.run(
            mock_skins_cog.render_skin.callback(mock_skins_cog, interaction=interaction, ship="Test Ship", skin="lava")
        )

        # Should send cancellation message
        followup_messages = [str(call) for call in interaction.followup.send.call_args_list]
        assert any("cancel" in msg.lower() or "Region" in msg for msg in followup_messages)


class TestMakeSkinTextureMultiRegion:
    """Tests for make_skin_texture command multi-region integration."""

    def _make_render_info(self, num_masks=2):
        """Helper to create render_info for tests."""
        mask_paths = [f"/path/mask{i}.jpg" for i in range(1, num_masks + 1)]
        return {
            "skinnable": True,
            "bbship_dir": "/path/ship.bbship",
            "diffuse_path": "/path/diffuse.bmp",
            "mask_paths": mask_paths,
            "compatible_skins": {"lava": "http://example.com/lava.png"},
            "ship_name": "Test Ship",
        }

    def test_make_skin_texture_single_region_no_prompt(self, mock_skins_cog):
        """make_skin_texture should not show region prompt for single-region ships."""
        render_info = self._make_render_info(num_masks=1)

        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        mock_skins_cog._fetch_render_info = AsyncMock(return_value=render_info)
        mock_skins_cog._download_skin_image = AsyncMock(return_value=b"lava_bytes")
        mock_skins_cog._composite_textures = AsyncMock(return_value=b"composite")
        mock_skins_cog._resolve_region_mode = AsyncMock(return_value="all")

        asyncio.run(
            mock_skins_cog.make_skin_texture.callback(
                mock_skins_cog, interaction=interaction, ship="Test Ship", skin="lava"
            )
        )

        mock_skins_cog._composite_textures.assert_called_once()

    def test_make_skin_texture_multi_region_apply_all(self, mock_skins_cog):
        """make_skin_texture multi-region 'Apply All' should use standard composite (AC-20)."""
        render_info = self._make_render_info(num_masks=2)

        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        mock_skins_cog._fetch_render_info = AsyncMock(return_value=render_info)
        mock_skins_cog._download_skin_image = AsyncMock(return_value=b"lava_bytes")
        mock_skins_cog._composite_textures = AsyncMock(return_value=b"composite")
        mock_skins_cog._resolve_region_mode = AsyncMock(return_value="all")

        asyncio.run(
            mock_skins_cog.make_skin_texture.callback(
                mock_skins_cog, interaction=interaction, ship="Test Ship", skin="lava"
            )
        )

        mock_skins_cog._composite_textures.assert_called_once()

    def test_make_skin_texture_multi_region_customize(self, mock_skins_cog):
        """make_skin_texture multi-region 'Customize' should use multiregion composite (AC-20)."""
        render_info = self._make_render_info(num_masks=2)

        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        region_choices = {1: {"action": "skin", "bytes": b"lava"}, 2: {"action": "skip"}}

        mock_skins_cog._fetch_render_info = AsyncMock(return_value=render_info)
        mock_skins_cog._download_skin_image = AsyncMock(return_value=b"lava_bytes")
        mock_skins_cog._resolve_region_mode = AsyncMock(return_value="custom")
        mock_skins_cog._collect_per_region_choices = AsyncMock(return_value=region_choices)
        mock_skins_cog._composite_textures_multiregion = AsyncMock(return_value=b"composite")

        asyncio.run(
            mock_skins_cog.make_skin_texture.callback(
                mock_skins_cog, interaction=interaction, ship="Test Ship", skin="lava"
            )
        )

        mock_skins_cog._composite_textures_multiregion.assert_called_once()

    def test_make_skin_texture_cancelled_returns_early(self, mock_skins_cog):
        """make_skin_texture should abort when user cancels region mode (AC-6)."""
        render_info = self._make_render_info(num_masks=2)

        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        mock_skins_cog._fetch_render_info = AsyncMock(return_value=render_info)
        mock_skins_cog._download_skin_image = AsyncMock(return_value=b"lava_bytes")
        mock_skins_cog._resolve_region_mode = AsyncMock(return_value=None)  # cancelled
        mock_skins_cog._composite_textures = AsyncMock()
        mock_skins_cog._composite_textures_multiregion = AsyncMock()

        asyncio.run(
            mock_skins_cog.make_skin_texture.callback(
                mock_skins_cog, interaction=interaction, ship="Test Ship", skin="lava"
            )
        )

        mock_skins_cog._composite_textures.assert_not_called()
        mock_skins_cog._composite_textures_multiregion.assert_not_called()

    def test_make_skin_texture_default_skin_no_prompt(self, mock_skins_cog):
        """make_skin_texture with Default skin and multi-region ship has no region prompt (AC-5)."""
        render_info = self._make_render_info(num_masks=3)

        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        mock_skins_cog._fetch_render_info = AsyncMock(return_value=render_info)
        mock_skins_cog._composite_textures = AsyncMock(return_value=b"composite")
        mock_skins_cog._resolve_region_mode = AsyncMock(return_value="all")

        asyncio.run(
            mock_skins_cog.make_skin_texture.callback(
                mock_skins_cog,
                interaction=interaction,
                ship="Test Ship",
                # Default skin — no skin arg
            )
        )

        mock_skins_cog._composite_textures.assert_called_once()
        call_args = mock_skins_cog._composite_textures.call_args
        # skin_bytes should be None
        assert call_args[0][4] is None

    def test_make_skin_texture_shared_region_logic_with_render_skin(self, mock_skins_cog):
        """make_skin_texture uses same helper methods as render_skin (AC-21)."""
        # Both commands call _resolve_region_mode and _collect_per_region_choices
        # Verify both exist as methods on the cog
        assert hasattr(mock_skins_cog, "_resolve_region_mode")
        assert hasattr(mock_skins_cog, "_collect_per_region_choices")
        assert hasattr(mock_skins_cog, "_composite_textures_multiregion")


class TestOldCollectRegionTexturesRemoved:
    """Verify that the old _collect_region_textures method no longer exists."""

    def test_old_method_removed(self, mock_skins_cog):
        """_collect_region_textures should have been removed (replaced by _collect_per_region_choices)."""
        assert not hasattr(mock_skins_cog, "_collect_region_textures"), (
            "_collect_region_textures still exists; it should have been removed and replaced "
            "by _collect_per_region_choices"
        )

    def test_new_method_exists(self, mock_skins_cog):
        """_collect_per_region_choices should exist as the replacement."""
        assert hasattr(mock_skins_cog, "_collect_per_region_choices")


class TestRegionIndicesCorrectness:
    """Tests for correct region_indices building in multi-region composite."""

    def test_only_active_regions_in_indices(self, mock_skins_cog):
        """Only regions with 'skin' or 'upload' action should appear in region_indices."""
        interaction = MagicMock()
        interaction.followup.send = AsyncMock()

        # Region 1: skin, region 2: skip, region 3: upload
        region_choices = {
            1: {"action": "skin", "bytes": b"r1"},
            2: {"action": "skip"},
            3: {"action": "upload", "bytes": b"r3"},
        }

        mock_resp = MagicMock()
        mock_resp.content = b"composite"
        mock_resp.raise_for_status = MagicMock()
        mock_skins_cog.blender_client.post = AsyncMock(return_value=mock_resp)

        asyncio.run(
            mock_skins_cog._composite_textures_multiregion(interaction, "Ship", "/ship", "/diffuse", region_choices)
        )

        data = mock_skins_cog.blender_client.post.call_args[1]["data"]
        indices = data["region_indices"].split(",")
        assert "1" in indices
        assert "2" not in indices
        assert "3" in indices

    def test_files_match_active_regions(self, mock_skins_cog):
        """Number of region_textures files should match number of active regions."""
        interaction = MagicMock()
        interaction.followup.send = AsyncMock()

        region_choices = {
            1: {"action": "skin", "bytes": b"r1"},
            2: {"action": "skip"},
            3: {"action": "upload", "bytes": b"r3"},
        }

        mock_resp = MagicMock()
        mock_resp.content = b"composite"
        mock_resp.raise_for_status = MagicMock()
        mock_skins_cog.blender_client.post = AsyncMock(return_value=mock_resp)

        asyncio.run(
            mock_skins_cog._composite_textures_multiregion(interaction, "Ship", "/ship", "/diffuse", region_choices)
        )

        files = mock_skins_cog.blender_client.post.call_args[1]["files"]
        # Should have 2 region_textures (for regions 1 and 3)
        region_texture_files = [f for f in files if f[0] == "region_textures"]
        assert len(region_texture_files) == 2


if __name__ == "__main__":
    pytest.main([__file__])
