"""
Tests verifying that discord-gateway cogs handle the 'guild not configured'
HTTP 400 response from bot-core gracefully.

All affected cogs (shopCog, playerCog, bountyCog, inventoryCog, shipsCog)
must display a user-friendly ephemeral message instead of crashing or
showing a stack trace.
"""

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Module-level mock setup — must run before any src imports
# ---------------------------------------------------------------------------

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []
_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_args, **_kwargs):
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    logger.exception = MagicMock()
    return logger


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GUILD_NOT_CONFIGURED_DETAIL = "Guild not configured; admin must run /admin_setup"
_EXPECTED_MESSAGE_FRAGMENT = "admin_setup"


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


def _make_mock_interaction(user_id=111111111, guild_id=987654321):
    import discord

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild_id = guild_id
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.user.display_name = "TestUser"
    interaction.user.display_avatar = MagicMock()
    interaction.user.display_avatar.url = "https://example.com/avatar.png"
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    return interaction


def _make_guild_not_configured_response():
    """Build a mock httpx response simulating a 400 guild-not-configured error."""
    import httpx

    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.json.return_value = {"detail": _GUILD_NOT_CONFIGURED_DETAIL}
    return httpx.HTTPStatusError(
        message="400 Bad Request",
        request=MagicMock(),
        response=mock_response,
    )


def _make_mock_bot():
    bot = MagicMock()
    bot.loop = MagicMock()
    bot.loop.create_task = MagicMock()
    bot.wait_until_ready = AsyncMock()
    return bot


# ---------------------------------------------------------------------------
# shopCog tests
# ---------------------------------------------------------------------------


class TestShopCogGuildNotConfigured:
    """shopCog shows friendly message when guild not configured."""

    @pytest.fixture
    def cog(self):
        _evict_discord_modules()
        from cogs.shopCog import ShopCog

        bot = _make_mock_bot()
        cog = ShopCog(bot)
        cog.http_client = MagicMock()
        cog.http_client.aclose = AsyncMock()
        return cog

    @pytest.mark.asyncio
    async def test_shop_command_guild_not_configured(self, cog):
        """'/shop' sends friendly message when POST /players/ returns 400 not-configured."""

        interaction = _make_mock_interaction()
        interaction.response.defer = AsyncMock()

        exc = _make_guild_not_configured_response()
        cog.http_client.post = AsyncMock(side_effect=exc)

        # Discord slash commands wrap the handler; call via .callback for testing
        await cog.shop.callback(cog, interaction, tier="Bronze")

        interaction.followup.send.assert_awaited_once()
        sent_text = interaction.followup.send.call_args[0][0]
        assert _EXPECTED_MESSAGE_FRAGMENT in sent_text
        # Must be ephemeral
        assert interaction.followup.send.call_args[1].get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_is_guild_not_configured_detects_correct_error(self, cog):
        """_is_guild_not_configured returns True for the right error pattern."""
        from cogs.shopCog import _is_guild_not_configured

        exc = _make_guild_not_configured_response()
        assert _is_guild_not_configured(exc) is True

    @pytest.mark.asyncio
    async def test_is_guild_not_configured_false_for_other_400(self, cog):
        """_is_guild_not_configured returns False for unrelated 400 errors."""
        import httpx
        from cogs.shopCog import _is_guild_not_configured

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"detail": "Invalid tier"}
        exc = httpx.HTTPStatusError(message="400", request=MagicMock(), response=mock_response)
        assert _is_guild_not_configured(exc) is False

    @pytest.mark.asyncio
    async def test_is_guild_not_configured_false_for_404(self, cog):
        """_is_guild_not_configured returns False for 404 errors."""
        import httpx
        from cogs.shopCog import _is_guild_not_configured

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.json.return_value = {"detail": "not configured"}
        exc = httpx.HTTPStatusError(message="404", request=MagicMock(), response=mock_response)
        assert _is_guild_not_configured(exc) is False


# ---------------------------------------------------------------------------
# playerCog tests
# ---------------------------------------------------------------------------


class TestPlayerCogGuildNotConfigured:
    """playerCog shows friendly message when guild not configured."""

    @pytest.fixture
    def cog(self):
        _evict_discord_modules()
        from cogs.playerCog import PlayerCog

        bot = _make_mock_bot()
        cog = PlayerCog(bot)
        cog.http_client = MagicMock()
        cog.http_client.aclose = AsyncMock()
        return cog

    @pytest.mark.asyncio
    async def test_profile_command_guild_not_configured(self, cog):
        """'/profile' sends friendly message when guild not configured."""
        interaction = _make_mock_interaction()
        interaction.response.defer = AsyncMock()
        interaction.user = MagicMock()
        interaction.user.id = 111
        interaction.user.display_name = "TestUser"
        interaction.user.display_avatar = MagicMock()
        interaction.user.display_avatar.url = "https://example.com/avatar.png"
        interaction.user.__str__ = MagicMock(return_value="TestUser#0001")

        exc = _make_guild_not_configured_response()
        cog.http_client.post = AsyncMock(side_effect=exc)

        await cog.profile.callback(cog, interaction)

        interaction.followup.send.assert_awaited_once()
        sent_text = interaction.followup.send.call_args[0][0]
        assert _EXPECTED_MESSAGE_FRAGMENT in sent_text
        assert interaction.followup.send.call_args[1].get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_is_guild_not_configured_helper(self, cog):
        """_is_guild_not_configured helper works in playerCog."""
        from cogs.playerCog import _is_guild_not_configured

        exc = _make_guild_not_configured_response()
        assert _is_guild_not_configured(exc) is True


# ---------------------------------------------------------------------------
# bountyCog tests
# ---------------------------------------------------------------------------


class TestBountyCogGuildNotConfigured:
    """bountyCog shows friendly message when guild not configured."""

    @pytest.fixture
    def cog(self):
        _evict_discord_modules()
        from cogs.bountyCog import BountyCog

        bot = _make_mock_bot()
        cog = BountyCog(bot)
        cog.http_client = MagicMock()
        cog.http_client.aclose = AsyncMock()
        return cog

    @pytest.mark.asyncio
    async def test_check_command_guild_not_configured(self, cog):
        """/check shows friendly message when guild not configured."""
        interaction = _make_mock_interaction()
        interaction.response.defer = AsyncMock()

        exc = _make_guild_not_configured_response()
        cog.http_client.post = AsyncMock(side_effect=exc)

        await cog.check.callback(cog, interaction, system="Alpha Centauri")

        interaction.followup.send.assert_awaited_once()
        sent_text = interaction.followup.send.call_args[0][0]
        assert _EXPECTED_MESSAGE_FRAGMENT in sent_text
        assert interaction.followup.send.call_args[1].get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_is_guild_not_configured_helper(self, cog):
        """_is_guild_not_configured helper works in bountyCog."""
        from cogs.bountyCog import _is_guild_not_configured

        exc = _make_guild_not_configured_response()
        assert _is_guild_not_configured(exc) is True


# ---------------------------------------------------------------------------
# inventoryCog tests
# ---------------------------------------------------------------------------


class TestInventoryCogGuildNotConfigured:
    """inventoryCog shows friendly message when guild not configured."""

    @pytest.fixture
    def cog(self):
        _evict_discord_modules()
        from cogs.inventoryCog import InventoryCog

        bot = _make_mock_bot()
        cog = InventoryCog(bot)
        cog.http_client = MagicMock()
        cog.http_client.aclose = AsyncMock()
        return cog

    @pytest.mark.asyncio
    async def test_inventory_command_guild_not_configured(self, cog):
        """/inventory shows friendly message when guild not configured."""
        interaction = _make_mock_interaction()
        interaction.response.defer = AsyncMock()
        interaction.user = MagicMock()
        interaction.user.id = 111
        interaction.user.display_name = "TestUser"
        interaction.user.display_avatar = MagicMock()
        interaction.user.display_avatar.url = "https://example.com/avatar.png"

        exc = _make_guild_not_configured_response()
        cog.http_client.post = AsyncMock(side_effect=exc)

        await cog.inventory.callback(cog, interaction, item_type=None, user=None)

        interaction.followup.send.assert_awaited_once()
        sent_text = interaction.followup.send.call_args[0][0]
        assert _EXPECTED_MESSAGE_FRAGMENT in sent_text
        assert interaction.followup.send.call_args[1].get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_is_guild_not_configured_helper(self, cog):
        """_is_guild_not_configured helper works in inventoryCog."""
        from cogs.inventoryCog import _is_guild_not_configured

        exc = _make_guild_not_configured_response()
        assert _is_guild_not_configured(exc) is True


# ---------------------------------------------------------------------------
# shipsCog tests
# ---------------------------------------------------------------------------


class TestShipsCogGuildNotConfigured:
    """shipsCog shows friendly message when guild not configured."""

    @pytest.fixture
    def cog(self):
        _evict_discord_modules()
        from cogs.shipsCog import ShipsCog

        bot = _make_mock_bot()
        cog = ShipsCog(bot)
        cog.http_client = MagicMock()
        cog.http_client.aclose = AsyncMock()
        return cog

    @pytest.mark.asyncio
    async def test_ships_command_guild_not_configured(self, cog):
        """/ships shows friendly message when guild not configured."""
        interaction = _make_mock_interaction()
        interaction.response.defer = AsyncMock()
        interaction.user = MagicMock()
        interaction.user.id = 111
        interaction.user.display_name = "TestUser"

        exc = _make_guild_not_configured_response()
        cog.http_client.post = AsyncMock(side_effect=exc)

        await cog.ships.callback(cog, interaction, user=None)

        interaction.followup.send.assert_awaited_once()
        sent_text = interaction.followup.send.call_args[0][0]
        assert _EXPECTED_MESSAGE_FRAGMENT in sent_text
        assert interaction.followup.send.call_args[1].get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_is_guild_not_configured_helper(self, cog):
        """_is_guild_not_configured helper works in shipsCog."""
        from cogs.shipsCog import _is_guild_not_configured

        exc = _make_guild_not_configured_response()
        assert _is_guild_not_configured(exc) is True
