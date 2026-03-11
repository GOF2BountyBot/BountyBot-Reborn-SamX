"""
Tests for the skinsCog Discord bot functionality.

This module provides comprehensive test coverage for the skinsCog commands,
ship skin management, and skin compatibility operations.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import sys
import os
import types
import asyncio
from datetime import datetime

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

import discord
from discord.ext import commands


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot for skinsCog testing."""
    loop = MagicMock()
    loop.create_task = MagicMock(side_effect=lambda coro: coro.close())
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
    to_evict = [k for k in sys.modules if k == "discord" or k.startswith("discord.")
                or k in ("api", "bot", "utils") or k.startswith("api.") or k.startswith("utils.")
                or k.startswith("cogs.")]
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
    """Tests for ship skin preloading."""

    @patch("cogs.skinsCog.httpx")
    def test_preload_ship_skins_success(self, mock_httpx, mock_skins_cog):
        """_preload_ship_skins should load ship skins successfully."""
        # Mock HTTP client
        mock_httpx.AsyncClient = MagicMock()
        mock_client = MagicMock()
        mock_httpx.AsyncClient.return_value = mock_client

        # Mock API responses - ships list
        ships_resp = MagicMock()
        ships_resp.status_code = 200
        ships_resp.json.return_value = [
            {"name": "Test Ship 1"},
            {"name": "Test Ship 2"}
        ]
        ships_resp.raise_for_status = MagicMock()

        # Mock ship detail responses
        ship1_resp = MagicMock()
        ship1_resp.status_code = 200
        ship1_resp.json.return_value = {
            "name": "Test Ship 1",
            "compatible_skins": {"Red Skin": "http://example.com/red.png", "Blue Skin": "http://example.com/blue.png"}
        }
        ship1_resp.raise_for_status = MagicMock()

        ship2_resp = MagicMock()
        ship2_resp.status_code = 200
        ship2_resp.json.return_value = {
            "name": "Test Ship 2",
            "compatible_skins": {"Green Skin": "http://example.com/green.png"}
        }
        ship2_resp.raise_for_status = MagicMock()

        mock_client.get = AsyncMock(side_effect=[ships_resp, ship1_resp, ship2_resp])
        mock_client.aclose = AsyncMock()

        # Replace the cog's http_client with our mock
        mock_skins_cog.http_client = mock_client

        # Call method
        asyncio.run(mock_skins_cog._preload_ship_skins())

        # Verify behavior
        mock_client.get.assert_any_call("http://bot-core:8000/api/v1/about/categories/ship/objects", timeout=10)
        assert len(mock_skins_cog._ship_skins) == 2
        assert "Test Ship 1" in mock_skins_cog._ship_skins
        assert "Test Ship 2" in mock_skins_cog._ship_skins
        assert "Red Skin" in mock_skins_cog._ship_skins["Test Ship 1"]
        assert "Blue Skin" in mock_skins_cog._ship_skins["Test Ship 1"]
        assert "Green Skin" in mock_skins_cog._ship_skins["Test Ship 2"]

    @patch("cogs.skinsCog.httpx")
    def test_preload_ship_skins_failure(self, mock_httpx, mock_skins_cog):
        """_preload_ship_skins should handle failures gracefully."""
        # Mock HTTP client with error
        mock_httpx.AsyncClient = MagicMock()
        mock_client = MagicMock()
        mock_httpx.AsyncClient.return_value = mock_client
        mock_client.get = AsyncMock(side_effect=Exception("API error"))
        mock_client.aclose = AsyncMock()

        # Replace the cog's http_client with our mock
        mock_skins_cog.http_client = mock_client

        # Call method
        asyncio.run(mock_skins_cog._preload_ship_skins())

        # Verify behavior - error should be logged but not crash
        assert mock_skins_cog._ship_skins == {}


class TestShipAutocomplete:
    """Tests for ship autocomplete."""

    def test_ship_autocomplete(self, mock_skins_cog):
        """ship_autocomplete should return filtered ship choices."""
        mock_skins_cog._ship_skins = {
            "Basic Ship": ["Skin A", "Skin B"],
            "Advanced Ship": ["Skin C"],
            "Elite Ship": ["Skin D", "Skin E", "Skin F"]
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
        mock_skins_cog._ship_skins = {
            "Test Ship": ["Basic Skin", "Advanced Skin", "Premium Skin"]
        }

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
        mock_skins_cog._ship_skins = {
            "Test Ship": []
        }

        mock_interaction = MagicMock()
        mock_interaction.namespace = MagicMock()
        mock_interaction.namespace.ship = "Test Ship"

        choices = asyncio.run(mock_skins_cog.skin_autocomplete(mock_interaction, ""))
        assert len(choices) == 1
        assert choices[0].name == "Default"
        assert choices[0].value == "Default"

    def test_skin_autocomplete_default_when_no_match(self, mock_skins_cog):
        """skin_autocomplete should return Default when filter matches nothing."""
        mock_skins_cog._ship_skins = {
            "Test Ship": ["Basic Skin", "Advanced Skin"]
        }

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
            "compatible_skins": {
                "Premium Skin": "http://example.com/premium.png"
            }
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
        asyncio.run(mock_skins_cog.ship_skin.callback(mock_skins_cog, interaction=interaction, ship="Test Ship", skin="Premium Skin"))

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
            "compatible_skins": {
                "Premium Skin": "http://example.com/premium.png"
            }
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
        asyncio.run(mock_skins_cog.ship_skin.callback(mock_skins_cog, interaction=interaction, ship="Test Ship", skin="Invalid Skin"))

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
    """Tests for ship skin preloading edge cases."""

    @patch("cogs.skinsCog.httpx")
    def test_preload_ship_skins_missing_name(self, mock_httpx, mock_skins_cog):
        """_preload_ship_skins should skip ships without names."""
        # Mock HTTP client
        mock_httpx.AsyncClient = MagicMock()
        mock_client = MagicMock()
        mock_httpx.AsyncClient.return_value = mock_client

        # Mock API responses - ship without name and ship with name
        ships_resp = MagicMock()
        ships_resp.status_code = 200
        ships_resp.json.return_value = [
            {"name": None},  # No name - should skip
            {"name": ""},    # Empty name - should skip
            {"name": "Valid Ship"}
        ]
        ships_resp.raise_for_status = MagicMock()

        # Valid ship response
        valid_resp = MagicMock()
        valid_resp.status_code = 200
        valid_resp.json.return_value = {
            "name": "Valid Ship",
            "compatible_skins": {"Skin1": "url1"}
        }
        valid_resp.raise_for_status = MagicMock()

        mock_client.get = AsyncMock(side_effect=[ships_resp, valid_resp])
        mock_client.aclose = AsyncMock()
        mock_skins_cog.http_client = mock_client

        # Call method
        asyncio.run(mock_skins_cog._preload_ship_skins())

        # Verify only valid ship was loaded
        assert "Valid Ship" in mock_skins_cog._ship_skins
        assert len(mock_skins_cog._ship_skins) == 1

    @patch("cogs.skinsCog.httpx")
    def test_preload_ship_skins_individual_ship_error(self, mock_httpx, mock_skins_cog):
        """_preload_ship_skins should handle errors loading individual ship details."""
        # Mock HTTP client
        mock_httpx.AsyncClient = MagicMock()
        mock_client = MagicMock()
        mock_httpx.AsyncClient.return_value = mock_client

        # Mock API responses
        ships_resp = MagicMock()
        ships_resp.status_code = 200
        ships_resp.json.return_value = [
            {"name": "Ship 1"},
            {"name": "Ship 2"}
        ]
        ships_resp.raise_for_status = MagicMock()

        # First ship fails, second succeeds
        error_resp = MagicMock()
        error_resp.raise_for_status = MagicMock(side_effect=Exception("Failed to load ship"))

        success_resp = MagicMock()
        success_resp.status_code = 200
        success_resp.json.return_value = {
            "name": "Ship 2",
            "compatible_skins": {"Skin2": "url2"}
        }
        success_resp.raise_for_status = MagicMock()

        mock_client.get = AsyncMock(side_effect=[ships_resp, error_resp, success_resp])
        mock_client.aclose = AsyncMock()
        mock_skins_cog.http_client = mock_client

        # Call method
        asyncio.run(mock_skins_cog._preload_ship_skins())

        # Verify behavior - failed ship should have empty list, successful ship should have skins
        assert "Ship 1" in mock_skins_cog._ship_skins
        assert mock_skins_cog._ship_skins["Ship 1"] == []
        assert "Ship 2" in mock_skins_cog._ship_skins
        assert "Skin2" in mock_skins_cog._ship_skins["Ship 2"]


class TestSkinAutocompleteEdgeCases:
    """Tests for skin autocomplete edge cases."""

    def test_skin_autocomplete_ship_not_in_dict(self, mock_skins_cog):
        """skin_autocomplete should return empty list when ship not in dict."""
        mock_skins_cog._ship_skins = {
            "Test Ship": ["Skin1"]
        }

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
        asyncio.run(mock_skins_cog.ship_skin.callback(mock_skins_cog, interaction=interaction, ship="Test Ship", skin="Default"))

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
            "compatible_skins": {}
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
        asyncio.run(mock_skins_cog.ship_skin.callback(mock_skins_cog, interaction=interaction, ship="Test Ship", skin="Default"))

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


if __name__ == '__main__':
    pytest.main([__file__])
