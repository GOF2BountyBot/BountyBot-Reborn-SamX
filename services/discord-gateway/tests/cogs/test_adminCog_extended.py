"""
Extended tests for adminCog to boost coverage from 35% to 70%+.
Covers uncovered paths: add_credits, set_xp, reset, admin_config,
admin_guild_stats, admin_refresh_shop, error handlers, cog_unload, tier_autocomplete.
"""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import httpx
import pytest
import respx

# Import discord_mock_utils for consistent mock patterns
from tests.mocks.discord_mock_utils import DiscordMockUtils

# Setup mock shared.bblogger module
_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")

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
    logger.exception = MagicMock()
    _unused_module_logger = logger
    return logger


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

# Ensure real discord is used
for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


def _close_coro(coro):
    """Close a coroutine to prevent 'never awaited' RuntimeWarning."""
    coro.close()
    return MagicMock()


@pytest.fixture(scope="module")
def mock_bot():
    """Create a mock Discord bot for adminCog testing."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    bot.get_member = MagicMock()
    bot.flogger = MagicMock()
    bot.loop = MagicMock()
    bot.loop.create_task = MagicMock(side_effect=_close_coro)
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


@pytest.fixture(scope="module")
def mock_admin_cog(mock_bot):
    """Create a mock adminCog instance."""
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    import cogs.adminCog as _adm

    cog = _adm.AdminCog(mock_bot)
    return cog


_API_BASE = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")


def _make_http_status_error(status_code: int, message: str = "Server error") -> httpx.HTTPStatusError:
    """Build a genuine ``httpx.HTTPStatusError`` (real ``httpx.Response`` attached).

    Previously several tests hand-built the exception with
    ``response=MagicMock(status_code=...)`` — a bare MagicMock stand-in for the response
    that doesn't behave like a real ``httpx.Response`` (e.g. `.text`, `.json()`, `.headers`
    would all silently return more MagicMocks instead of raising/being absent as they
    would on a real error response). This raises the exception the way a real
    ``resp.raise_for_status()`` call would, so ``exc.response`` is faithful.
    """
    request = httpx.Request("GET", "http://bot-core.test/api/v1/resource")
    response = httpx.Response(status_code, json={"detail": message}, request=request)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return exc
    raise AssertionError(f"status_code={status_code} did not raise")


def _create_mock_interaction(guild_id=987654321):
    """Create a properly mocked interaction with all necessary attributes."""
    interaction = DiscordMockUtils.create_mock_interaction()
    interaction.guild_id = guild_id
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.guild.name = "Test Guild"
    interaction.guild.icon = None
    return interaction


def _create_mock_user(user_id=111111111, name="TestUser", is_admin=False):
    """Create a properly mocked user with string properties."""
    user = DiscordMockUtils.create_mock_user(user_id=user_id, username=name)
    user.display_name = name
    user.display_avatar = MagicMock()
    user.display_avatar.url = "https://example.com/avatar.jpg"
    user.guild_permissions = MagicMock()
    user.guild_permissions.administrator = is_admin
    return user


# ---------------------------------------------------------------------------
# TestAdminPlayerExtended — covers add_credits, set_xp, reset, missing args
# ---------------------------------------------------------------------------


class TestAdminPlayerAddCredits:
    """Tests for admin_player add_credits action."""

    def test_admin_player_add_credits_success(self, mock_admin_cog):
        """admin_player should add credits to player."""
        interaction = _create_mock_interaction()
        user = _create_mock_user()

        player_resp = MagicMock()
        player_resp.status_code = 200
        player_resp.json.return_value = {
            "id": 1,
            "credits": 500,
            "xp": 100,
            "tier": "Bronze",
            "lifetime_credits": 500,
            "prestige_count": 0,
            "created_at": "2024-01-01T00:00:00",
        }

        update_resp = MagicMock()
        update_resp.status_code = 200
        update_resp.json.return_value = {"old_credits": 500, "new_credits": 700}

        mock_admin_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_admin_cog.http_client.put = AsyncMock(return_value=update_resp)

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "add_credits", 200, None))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()

    def test_admin_player_add_credits_missing_amount(self, mock_admin_cog):
        """admin_player add_credits should reject missing credit_amount."""
        interaction = _create_mock_interaction()
        user = _create_mock_user()

        player_resp = MagicMock()
        player_resp.status_code = 200
        player_resp.json.return_value = {
            "id": 1,
            "credits": 500,
            "xp": 100,
            "tier": "Bronze",
            "lifetime_credits": 500,
            "prestige_count": 0,
            "created_at": "2024-01-01T00:00:00",
        }
        mock_admin_cog.http_client.post = AsyncMock(return_value=player_resp)

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "add_credits", None, None))

        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "❌" in call_args

    def test_admin_player_set_credits_missing_amount(self, mock_admin_cog):
        """admin_player set_credits should reject missing credit_amount."""
        interaction = _create_mock_interaction()
        user = _create_mock_user()

        player_resp = MagicMock()
        player_resp.status_code = 200
        player_resp.json.return_value = {
            "id": 1,
            "credits": 500,
            "xp": 100,
            "tier": "Bronze",
            "lifetime_credits": 500,
            "prestige_count": 0,
            "created_at": "2024-01-01T00:00:00",
        }
        mock_admin_cog.http_client.post = AsyncMock(return_value=player_resp)

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "set_credits", None, None))

        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "❌" in call_args

    def test_admin_player_set_xp_success(self, mock_admin_cog):
        """admin_player should set XP for player."""
        interaction = _create_mock_interaction()
        user = _create_mock_user()

        player_resp = MagicMock()
        player_resp.status_code = 200
        player_resp.json.return_value = {
            "id": 1,
            "credits": 500,
            "xp": 100,
            "tier": "Bronze",
            "lifetime_credits": 500,
            "prestige_count": 0,
            "created_at": "2024-01-01T00:00:00",
        }

        xp_resp = MagicMock()
        xp_resp.status_code = 200
        xp_resp.json.return_value = {
            "old_xp": 100,
            "new_xp": 5000,
            "old_tier": "Bronze",
            "new_tier": "Silver",
            "tier_changed": True,
        }

        mock_admin_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_admin_cog.http_client.put = AsyncMock(return_value=xp_resp)

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "set_xp", None, 5000))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        # Contract: the PUT body carries the requested xp amount to the right endpoint.
        put_call = mock_admin_cog.http_client.put.call_args
        assert put_call.args[0].endswith("/admin/players/xp")
        assert put_call.kwargs["json"]["xp"] == 5000
        # Behavior, not just "something was sent": the real embed reflects the tier change.
        interaction.followup.send.assert_called_once()
        embed = interaction.followup.send.call_args.kwargs.get("embed")
        assert embed is not None
        assert embed.title == "✅ XP Updated"
        field_values = {f.name: f.value for f in embed.fields}
        assert field_values["Old XP"] == "100"
        assert field_values["New XP"] == "5,000"
        assert field_values["Old Tier"] == "Bronze"
        assert field_values["New Tier"] == "Silver"
        assert field_values["Tier Change"] == "✅ Tier Updated!"

    def test_admin_player_set_xp_missing_amount(self, mock_admin_cog):
        """admin_player set_xp should reject missing xp amount."""
        interaction = _create_mock_interaction()
        user = _create_mock_user()

        player_resp = MagicMock()
        player_resp.status_code = 200
        player_resp.json.return_value = {
            "id": 1,
            "credits": 500,
            "xp": 100,
            "tier": "Bronze",
            "lifetime_credits": 500,
            "prestige_count": 0,
            "created_at": "2024-01-01T00:00:00",
        }
        mock_admin_cog.http_client.post = AsyncMock(return_value=player_resp)

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "set_xp", None, None))

        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "❌" in call_args

    def test_admin_player_set_xp_no_tier_change(self, mock_admin_cog):
        """admin_player set_xp with no tier change should not show tier change field."""
        interaction = _create_mock_interaction()
        user = _create_mock_user()

        player_resp = MagicMock()
        player_resp.status_code = 200
        player_resp.json.return_value = {
            "id": 1,
            "credits": 500,
            "xp": 100,
            "tier": "Bronze",
            "lifetime_credits": 500,
            "prestige_count": 0,
            "created_at": "2024-01-01T00:00:00",
        }

        xp_resp = MagicMock()
        xp_resp.status_code = 200
        xp_resp.json.return_value = {
            "old_xp": 100,
            "new_xp": 200,
            "old_tier": "Bronze",
            "new_tier": "Bronze",
            "tier_changed": False,
        }

        mock_admin_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_admin_cog.http_client.put = AsyncMock(return_value=xp_resp)

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "set_xp", None, 200))

        interaction.followup.send.assert_called_once()

    def test_admin_player_api_error(self, mock_admin_cog):
        """admin_player should handle API errors gracefully."""
        interaction = _create_mock_interaction()
        user = _create_mock_user()

        http_error = _make_http_status_error(500, "Server error")
        mock_admin_cog.http_client.post = AsyncMock(side_effect=http_error)

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "view_stats", None, None))

        interaction.followup.send.assert_called_once()
        # B.31b: helper now sends a sanitized embed instead of a raw URL string.
        embed = interaction.followup.send.call_args.kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")

    def test_admin_player_generic_error(self, mock_admin_cog):
        """admin_player should handle generic errors gracefully."""
        interaction = _create_mock_interaction()
        user = _create_mock_user()

        mock_admin_cog.http_client.post = AsyncMock(side_effect=Exception("Connection error"))

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "view_stats", None, None))

        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "⚠️" in call_args


# ---------------------------------------------------------------------------
# TestAdminRefreshShop — covers valid tiers, invalid tier, invalid tech level
# ---------------------------------------------------------------------------


class TestAdminRefreshShop:
    """Tests for admin_refresh_shop command."""

    def test_admin_refresh_shop_success(self, mock_admin_cog):
        """admin_refresh_shop should refresh shop successfully.

        `is_admin=True` is required for this to actually reach the success path: the
        original test used a non-admin user and never noticed because its only assertion
        (`followup.send.assert_called_once()`) also passes on the "❌ requires admin
        privileges" deny message.
        """
        interaction = _create_mock_interaction()
        user = _create_mock_user(is_admin=True)
        interaction.user = user

        refresh_resp = MagicMock()
        refresh_resp.status_code = 200
        refresh_resp.json.return_value = {"message": "Shop refreshed successfully"}
        mock_admin_cog.http_client.post = AsyncMock(return_value=refresh_resp)

        asyncio.run(mock_admin_cog.admin_refresh_shop.callback(mock_admin_cog, interaction, "Bronze", None))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        # Contract: POST hits the refresh endpoint with the requested tier in the JSON body.
        post_call = mock_admin_cog.http_client.post.call_args
        assert post_call.args[0].endswith("/admin/shops/refresh")
        assert post_call.kwargs["json"]["tier"] == "Bronze"
        interaction.followup.send.assert_called_once()
        embed = interaction.followup.send.call_args.kwargs.get("embed")
        assert embed is not None
        assert embed.title == "✅ Shop Refreshed Successfully!"
        assert "Bronze" in embed.description

    def test_admin_refresh_shop_with_tech_level(self, mock_admin_cog):
        """admin_refresh_shop should refresh shop with forced tech level.

        `is_admin=True` is required to actually reach the success path (see
        test_admin_refresh_shop_success for why the original non-admin user went unnoticed).
        """
        interaction = _create_mock_interaction()
        user = _create_mock_user(is_admin=True)
        interaction.user = user

        refresh_resp = MagicMock()
        refresh_resp.status_code = 200
        refresh_resp.json.return_value = {"message": "Shop refreshed with tech level 5"}
        mock_admin_cog.http_client.post = AsyncMock(return_value=refresh_resp)

        asyncio.run(mock_admin_cog.admin_refresh_shop.callback(mock_admin_cog, interaction, "Gold", 5))

        # Contract: force_tech_level is actually forwarded in the POST body.
        post_call = mock_admin_cog.http_client.post.call_args
        assert post_call.kwargs["json"]["force_tech_level"] == 5
        interaction.followup.send.assert_called_once()
        embed = interaction.followup.send.call_args.kwargs.get("embed")
        assert embed is not None
        assert embed.title == "✅ Shop Refreshed Successfully!"
        field_values = {f.name: f.value for f in embed.fields}
        assert field_values.get("Forced Tech Level") == "5"

    def test_admin_refresh_shop_invalid_tier(self, mock_admin_cog):
        """admin_refresh_shop should reject invalid tier."""
        interaction = _create_mock_interaction()

        asyncio.run(mock_admin_cog.admin_refresh_shop.callback(mock_admin_cog, interaction, "Diamond", None))

        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "❌" in call_args
        assert "Invalid tier" in call_args

    def test_admin_refresh_shop_invalid_tech_level_low(self, mock_admin_cog):
        """admin_refresh_shop should reject tech level < 1 (e.g., -1)."""
        interaction = _create_mock_interaction()

        asyncio.run(mock_admin_cog.admin_refresh_shop.callback(mock_admin_cog, interaction, "Silver", -1))

        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "❌" in call_args

    def test_admin_refresh_shop_invalid_tech_level_high(self, mock_admin_cog):
        """admin_refresh_shop should reject tech level > 10."""
        interaction = _create_mock_interaction()

        asyncio.run(mock_admin_cog.admin_refresh_shop.callback(mock_admin_cog, interaction, "Platinum", 11))

        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "❌" in call_args

    def test_admin_refresh_shop_tech_level_10_accepted(self, mock_admin_cog):
        """admin_refresh_shop should ACCEPT tech level 10 (TL ceiling raised 9 -> 10)."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user(is_admin=True)

        refresh_resp = MagicMock()
        refresh_resp.status_code = 200
        refresh_resp.json.return_value = {"message": "Shop refreshed with tech level 10"}
        mock_admin_cog.http_client.post = AsyncMock(return_value=refresh_resp)

        asyncio.run(mock_admin_cog.admin_refresh_shop.callback(mock_admin_cog, interaction, "Platinum", 10))

        # Validation passed → proceeded to the refresh call (not rejected).
        mock_admin_cog.http_client.post.assert_called()
        # Success path sends an embed (keyword arg), never the rejection message.
        assert "Tech level must be between" not in str(interaction.followup.send.call_args)

    def test_admin_refresh_shop_api_error(self, mock_admin_cog):
        """admin_refresh_shop should handle API errors.

        `is_admin=True` is required to actually reach the try/except around the POST call:
        with the previous non-admin user this test's `call_args[0][0]` assertion would have
        IndexError'd against the real `report_api_error` embed-only call — meaning it was, in
        practice, only ever exercising the "❌ requires admin privileges" deny branch.
        """
        interaction = _create_mock_interaction()
        user = _create_mock_user(is_admin=True)
        interaction.user = user

        http_error = _make_http_status_error(500, "Server error")
        mock_admin_cog.http_client.post = AsyncMock(side_effect=http_error)

        asyncio.run(mock_admin_cog.admin_refresh_shop.callback(mock_admin_cog, interaction, "Bronze", None))

        interaction.followup.send.assert_called_once()
        embed = interaction.followup.send.call_args.kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")

    def test_admin_refresh_shop_generic_error(self, mock_admin_cog):
        """admin_refresh_shop should handle generic errors."""
        interaction = _create_mock_interaction()

        mock_admin_cog.http_client.post = AsyncMock(side_effect=Exception("Unexpected error"))

        asyncio.run(mock_admin_cog.admin_refresh_shop.callback(mock_admin_cog, interaction, "Bronze", None))

        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "⚠️" in call_args


# ---------------------------------------------------------------------------
# TestAdminGuildStats — covers success and error paths
# ---------------------------------------------------------------------------


class TestAdminGuildStats:
    """Tests for admin_guild_stats command."""

    def test_admin_guild_stats_success(self, mock_admin_cog):
        """admin_guild_stats should show guild statistics."""
        interaction = _create_mock_interaction()
        interaction.guild.icon = None

        stats_resp = MagicMock()
        stats_resp.status_code = 200
        stats_resp.json.return_value = {
            "guild_id": 987654321,
            "total_players": 42,
            "tier_distribution": {"Bronze": 20, "Silver": 15, "Gold": 7},
            "total_credits": 100000,
            "total_xp": 500000,
            "average_credits": 2380.95,
            "average_xp": 11904.76,
        }
        mock_admin_cog.http_client.get = AsyncMock(return_value=stats_resp)

        asyncio.run(mock_admin_cog.admin_guild_stats.callback(mock_admin_cog, interaction))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        # Contract: GET hits the guild stats endpoint for THIS guild.
        get_call = mock_admin_cog.http_client.get.call_args
        assert get_call.args[0].endswith(f"/admin/guilds/{interaction.guild_id}/stats")
        interaction.followup.send.assert_called_once()
        embed = interaction.followup.send.call_args.kwargs.get("embed")
        assert embed is not None
        field_values = {f.name: f.value for f in embed.fields}
        assert field_values["Total Players"] == "42"
        assert field_values["Total Credits"] == "100,000"
        assert "Bronze: 20" in field_values["Tier Distribution"]

    def test_admin_guild_stats_no_tier_distribution(self, mock_admin_cog):
        """admin_guild_stats should work without tier distribution."""
        interaction = _create_mock_interaction()
        interaction.guild.icon = None

        stats_resp = MagicMock()
        stats_resp.status_code = 200
        stats_resp.json.return_value = {
            "guild_id": 987654321,
            "total_players": 0,
            "tier_distribution": None,
            "total_credits": 0,
            "total_xp": 0,
            "average_credits": 0.0,
            "average_xp": 0.0,
        }
        mock_admin_cog.http_client.get = AsyncMock(return_value=stats_resp)

        asyncio.run(mock_admin_cog.admin_guild_stats.callback(mock_admin_cog, interaction))

        interaction.followup.send.assert_called_once()

    def test_admin_guild_stats_api_error(self, mock_admin_cog):
        """admin_guild_stats should handle API errors."""
        interaction = _create_mock_interaction()

        http_error = _make_http_status_error(500, "Server error")
        mock_admin_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_admin_cog.admin_guild_stats.callback(mock_admin_cog, interaction))

        interaction.followup.send.assert_called_once()
        # B.31b: helper now sends a sanitized embed instead of a raw URL string.
        embed = interaction.followup.send.call_args.kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")

    def test_admin_guild_stats_generic_error(self, mock_admin_cog):
        """admin_guild_stats should handle generic errors."""
        interaction = _create_mock_interaction()

        mock_admin_cog.http_client.get = AsyncMock(side_effect=Exception("Connection failed"))

        asyncio.run(mock_admin_cog.admin_guild_stats.callback(mock_admin_cog, interaction))

        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "⚠️" in call_args


# ---------------------------------------------------------------------------
# TestAdminConfig — covers view, set_credits, set_role, reset actions
# ---------------------------------------------------------------------------


class TestAdminConfig:
    """Tests for admin_config command."""

    def test_admin_config_view(self, mock_admin_cog):
        """admin_config view should show guild configuration."""
        interaction = _create_mock_interaction()

        cfg_resp = MagicMock()
        cfg_resp.status_code = 200
        cfg_resp.json.return_value = {
            "guild_id": 987654321,
            "configured": True,
            "admin_role_configured": True,
            "starting_credits": 100,
            "sale_price_factor": 0.5,
            "xp_thresholds": {"Silver": 1000, "Gold": 5000, "Platinum": 20000},
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }
        mock_admin_cog.http_client.get = AsyncMock(return_value=cfg_resp)

        asyncio.run(mock_admin_cog.admin_config.callback(mock_admin_cog, interaction, "view", None, None))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()

    @pytest.mark.skip(reason="retired: 'set_credits' sub-action removed; use action:Set setting:starting_credits")
    def test_admin_config_set_credits_success(self, mock_admin_cog):
        """admin_config set_credits should update starting credits."""
        interaction = _create_mock_interaction()

        update_resp = MagicMock()
        update_resp.status_code = 200
        mock_admin_cog.http_client.put = AsyncMock(return_value=update_resp)

        asyncio.run(mock_admin_cog.admin_config.callback(mock_admin_cog, interaction, "set_credits", 500, None))

        # Contract: PUT hits the starting-credits endpoint with the requested amount in the path.
        put_call = mock_admin_cog.http_client.put.call_args
        assert put_call.args[0].endswith(f"/config/guild/{interaction.guild_id}/starting-credits/500")
        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert call_args == "✅ Starting credits set to 500"

    @pytest.mark.skip(reason="retired: 'set_credits' sub-action removed")
    def test_admin_config_set_credits_missing(self, mock_admin_cog):
        """admin_config set_credits should reject missing amount."""
        interaction = _create_mock_interaction()

        asyncio.run(mock_admin_cog.admin_config.callback(mock_admin_cog, interaction, "set_credits", None, None))

        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "❌" in call_args

    @pytest.mark.skip(reason="retired: 'set_role' sub-action removed; use /admin_setup")
    def test_admin_config_set_role_success(self, mock_admin_cog):
        """admin_config set_role should update admin role."""
        interaction = _create_mock_interaction()

        role = MagicMock()
        role.id = 222222222
        type(role).mention = PropertyMock(return_value="<@&222222222>")

        update_resp = MagicMock()
        update_resp.status_code = 200
        mock_admin_cog.http_client.put = AsyncMock(return_value=update_resp)

        asyncio.run(mock_admin_cog.admin_config.callback(mock_admin_cog, interaction, "set_role", None, role))

        # Contract: PUT hits the admin-role endpoint with the selected role's id in the path.
        put_call = mock_admin_cog.http_client.put.call_args
        assert put_call.args[0].endswith(f"/config/guild/{interaction.guild_id}/admin-role/222222222")
        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert call_args == "✅ Admin role set to <@&222222222>"

    @pytest.mark.skip(reason="retired: 'set_role' sub-action removed")
    def test_admin_config_set_role_missing(self, mock_admin_cog):
        """admin_config set_role should reject missing role."""
        interaction = _create_mock_interaction()

        asyncio.run(mock_admin_cog.admin_config.callback(mock_admin_cog, interaction, "set_role", None, None))

        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "❌" in call_args

    @pytest.mark.skip(reason="retired: old guild-config reset removed; new action:reset resets game-constant overrides")
    def test_admin_config_reset_success(self, mock_admin_cog):
        """admin_config reset should reset guild config to defaults."""
        interaction = _create_mock_interaction()

        reset_resp = MagicMock()
        reset_resp.status_code = 200
        mock_admin_cog.http_client.post = AsyncMock(return_value=reset_resp)

        asyncio.run(mock_admin_cog.admin_config.callback(mock_admin_cog, interaction, "reset", None, None))

        # Contract: POST hits the reset endpoint for THIS guild.
        post_call = mock_admin_cog.http_client.post.call_args
        assert post_call.args[0].endswith(f"/config/guild/{interaction.guild_id}/reset")
        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert call_args == "✅ Guild configuration has been reset to default values"

    def test_admin_config_api_error(self, mock_admin_cog):
        """admin_config should handle API errors gracefully."""
        interaction = _create_mock_interaction()

        http_error = _make_http_status_error(500, "Server error")
        mock_admin_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_admin_cog.admin_config.callback(mock_admin_cog, interaction, "view", None, None))

        interaction.followup.send.assert_called_once()
        # B.31b: helper now sends a sanitized embed instead of a raw URL string.
        embed = interaction.followup.send.call_args.kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")

    def test_admin_config_generic_error(self, mock_admin_cog):
        """admin_config should handle generic errors gracefully."""
        interaction = _create_mock_interaction()

        mock_admin_cog.http_client.get = AsyncMock(side_effect=Exception("Unexpected"))

        asyncio.run(mock_admin_cog.admin_config.callback(mock_admin_cog, interaction, "view", None, None))

        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "⚠️" in call_args


# ---------------------------------------------------------------------------
# TestAdminSetupExtended — error paths, no-role with fetch fallback
# ---------------------------------------------------------------------------


class TestAdminSetupExtended:
    """Extended tests for admin_setup command."""

    def test_admin_setup_no_role_fetch_fallback(self, mock_admin_cog):
        """admin_setup should send the full 16-field init payload built from channel_ids.

        Rewritten: the original test drove `admin_role=None` through the callback even
        though the command's real signature requires `admin_role: discord.Role` with no
        default (Discord itself enforces this — the param can never actually be None in
        production), while also priming an unused `guild.get_role`/`fetch_roles` side effect
        and a second `role_create_resp` mock that admin_setup's code never calls (role
        creation lives inside `ensure_bountybot_infrastructure`, which this test correctly
        patches out). Combined with an unasserted `send.assert_called_once()`, this
        accidentally exercised the *error* branch (the leftover `role_create_resp` was
        consumed as the init response, so `result["message"]` raised KeyError) while still
        passing — a real defect in the 20-field init_payload construction would have shipped
        green. This version passes a real admin_role (the only value Discord permits), drops
        the two dead mocks, and asserts the actual POST /admin/guilds/initialize payload.
        """
        interaction = _create_mock_interaction()
        user = _create_mock_user(is_admin=True)
        interaction.user = user

        role = MagicMock()
        role.id = 333333333
        type(role).mention = PropertyMock(return_value="<@&333333333>")

        init_resp = MagicMock()
        init_resp.status_code = 200
        init_resp.json.return_value = {
            "message": "Guild initialized successfully",
            "guild_id": 987654321,
            "shops_created": 4,
        }

        _channel_ids = {"category_id": 111, "bronze_bounty_channel_id": 222, "shop_channel_id": 333}
        with patch("cogs.adminCog.ensure_bountybot_infrastructure", new=AsyncMock(return_value=_channel_ids)):
            mock_admin_cog.http_client.post = AsyncMock(return_value=init_resp)

            asyncio.run(mock_admin_cog.admin_setup.callback(mock_admin_cog, interaction, role, 250))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        post_call = mock_admin_cog.http_client.post.call_args
        assert post_call.args[0].endswith("/admin/guilds/initialize")
        payload = post_call.kwargs["json"]
        assert payload["guild_id"] == interaction.guild_id
        assert payload["admin_role_id"] == 333333333
        assert payload["starting_credits"] == 250
        assert payload["category_id"] == 111
        assert payload["bronze_bounty_channel_id"] == 222
        assert payload["shop_channel_id"] == 333
        # Channel keys absent from ensure_bountybot_infrastructure's return degrade to None,
        # not KeyError — the get()-based construction must be defensive.
        assert payload["platinum_bounty_channel_id"] is None
        interaction.followup.send.assert_called_once()
        embed = interaction.followup.send.call_args.kwargs.get("embed")
        assert embed is not None
        assert embed.title == "✅ Guild Initialization Complete!"

    def test_admin_setup_http_status_error(self, mock_admin_cog):
        """admin_setup should handle HTTPStatusError specifically."""
        interaction = _create_mock_interaction()
        interaction.guild = MagicMock()
        interaction.guild.id = 987654321

        role = MagicMock()
        role.id = 222222222
        type(role).mention = PropertyMock(return_value="<@&222222222>")

        http_error = _make_http_status_error(409, "Conflict")
        # Patch ensure_bountybot_infrastructure so it doesn't require a real Guild mock
        _channel_ids = {"category_id": 111, "bounty_channel_id": 222, "shop_channel_id": 333, "general_channel_id": 444}
        with patch("cogs.adminCog.ensure_bountybot_infrastructure", new=AsyncMock(return_value=_channel_ids)):
            mock_admin_cog.http_client.post = AsyncMock(side_effect=http_error)

            asyncio.run(mock_admin_cog.admin_setup.callback(mock_admin_cog, interaction, role, 0))

        interaction.followup.send.assert_called_once()
        # B.31b: helper now sends a sanitized embed instead of a raw URL string.
        embed = interaction.followup.send.call_args.kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")


# ---------------------------------------------------------------------------
# TestAdminCheckExtended — member not found, API error
# ---------------------------------------------------------------------------


class TestAdminCheckExtended:
    """Extended tests for admin_check command."""

    def test_admin_check_no_admin_api_error(self, mock_admin_cog):
        """admin_check should silently pass when API call fails for TARGET (not admin).

        B.25 Fix A: The INVOKER uses the default admin interaction (Discord admin truthy),
        while the TARGET user lookup fails — still reports 'does not have admin rights'.
        """
        # Invoker interaction uses default MagicMock (truthy administrator)
        interaction = _create_mock_interaction()

        # TARGET user to check
        user = _create_mock_user(is_admin=False)

        guild = MagicMock()
        member = MagicMock()
        member.guild_permissions = MagicMock()
        member.guild_permissions.administrator = False
        member.roles = []
        guild.get_member = MagicMock(return_value=member)
        guild.fetch_member = AsyncMock(return_value=member)
        mock_admin_cog.bot.get_guild = MagicMock(return_value=guild)

        # API call raises an exception (for target's role lookup)
        mock_admin_cog.http_client.get = AsyncMock(side_effect=Exception("API unavailable"))

        asyncio.run(mock_admin_cog.admin_check.callback(mock_admin_cog, interaction, user))

        # Should still send a response (no admin)
        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "**does not have** bot-admin rights" in call_args

    def test_admin_check_member_fetch_fallback(self, mock_admin_cog):
        """admin_check should fetch member when get_member returns None.

        B.25 Fix A: The INVOKER uses the default admin interaction,
        while the TARGET user (fetched via get_guild) has Discord admin perm.
        """
        # Invoker interaction uses default MagicMock (truthy administrator)
        interaction = _create_mock_interaction()

        # TARGET user to check
        user = _create_mock_user(is_admin=False)

        guild = MagicMock()
        member = MagicMock()
        member.guild_permissions = MagicMock()
        member.guild_permissions.administrator = True
        member.roles = []
        # get_member returns None, so fetch_member is used
        guild.get_member = MagicMock(return_value=None)
        guild.fetch_member = AsyncMock(return_value=member)
        mock_admin_cog.bot.get_guild = MagicMock(return_value=guild)

        asyncio.run(mock_admin_cog.admin_check.callback(mock_admin_cog, interaction, user))

        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "**has** bot-admin rights" in call_args


# ---------------------------------------------------------------------------
# TestErrorHandlerPaths — admin_setup_error, admin_player_error
# ---------------------------------------------------------------------------


class TestErrorHandlerPaths:
    """Tests for error handler methods."""

    def test_admin_setup_error_missing_permissions(self, mock_admin_cog):
        """admin_setup_error should handle MissingPermissions error."""
        from discord import app_commands

        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)

        error = app_commands.MissingPermissions(["administrator"])

        asyncio.run(mock_admin_cog.admin_setup_error(interaction, error))

        interaction.response.send_message.assert_called_once()
        call_args = interaction.response.send_message.call_args[0][0]
        assert "Administrator" in call_args

    def test_admin_setup_error_other_error_not_done(self, mock_admin_cog):
        """admin_setup_error should handle other errors when response not done."""
        from discord import app_commands

        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)

        error = app_commands.AppCommandError("Some other error")

        asyncio.run(mock_admin_cog.admin_setup_error(interaction, error))

        interaction.response.send_message.assert_called_once()

    def test_admin_setup_error_other_error_already_done(self, mock_admin_cog):
        """admin_setup_error should not resend if response already done."""
        from discord import app_commands

        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)

        error = app_commands.AppCommandError("Some other error")

        asyncio.run(mock_admin_cog.admin_setup_error(interaction, error))

        interaction.response.send_message.assert_not_called()

    def test_admin_player_error_missing_permissions(self, mock_admin_cog):
        """admin_player_error should handle MissingPermissions error."""
        from discord import app_commands

        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)

        error = app_commands.MissingPermissions(["administrator"])

        asyncio.run(mock_admin_cog.admin_player_error(interaction, error))

        interaction.response.send_message.assert_called_once()
        call_args = interaction.response.send_message.call_args[0][0]
        assert "Administrator" in call_args

    def test_admin_player_error_other_error_not_done(self, mock_admin_cog):
        """admin_player_error should handle other errors when response not done."""
        from discord import app_commands

        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)

        error = app_commands.AppCommandError("Some other error")

        asyncio.run(mock_admin_cog.admin_player_error(interaction, error))

        interaction.response.send_message.assert_called_once()

    def test_admin_player_error_other_error_already_done(self, mock_admin_cog):
        """admin_player_error should not resend if response already done."""
        from discord import app_commands

        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)

        error = app_commands.AppCommandError("Some other error")

        asyncio.run(mock_admin_cog.admin_player_error(interaction, error))

        interaction.response.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# TestCogUnload — covers cog_unload method
# ---------------------------------------------------------------------------


class TestCogUnload:
    """Tests for cog_unload method."""

    def test_cog_unload_closes_http_client(self, mock_admin_cog):
        """cog_unload should close the HTTP client."""
        mock_admin_cog.http_client = MagicMock()
        mock_admin_cog.http_client.aclose = AsyncMock()

        asyncio.run(mock_admin_cog.cog_unload())

        mock_admin_cog.http_client.aclose.assert_called_once()


# ---------------------------------------------------------------------------
# TestTierAutocomplete — covers tier_autocomplete
# ---------------------------------------------------------------------------


class TestTierAutocomplete:
    """Tests for tier_autocomplete method."""

    def test_tier_autocomplete_empty_current(self, mock_admin_cog):
        """tier_autocomplete should return all tiers for empty current."""
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_admin_cog.tier_autocomplete(interaction, ""))

        assert len(result) == 4
        names = [choice.name for choice in result]
        assert "Bronze" in names
        assert "Silver" in names
        assert "Gold" in names
        assert "Platinum" in names

    def test_tier_autocomplete_partial_match(self, mock_admin_cog):
        """tier_autocomplete should filter by current input."""
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_admin_cog.tier_autocomplete(interaction, "gold"))

        assert len(result) == 1
        assert result[0].name == "Gold"

    def test_tier_autocomplete_no_match(self, mock_admin_cog):
        """tier_autocomplete should return empty list for no match."""
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_admin_cog.tier_autocomplete(interaction, "diamond"))

        assert len(result) == 0

    def test_tier_autocomplete_case_insensitive(self, mock_admin_cog):
        """tier_autocomplete should be case-insensitive."""
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_admin_cog.tier_autocomplete(interaction, "SILVER"))

        assert len(result) == 1
        assert result[0].name == "Silver"


# ---------------------------------------------------------------------------
# NOTE: A `TestIsAdminPredicate` class previously lived here with three tests
# (test_is_admin_developer_override, test_is_admin_discord_administrator,
# test_is_admin_no_rights) that were fully tautological: they never invoked
# `is_admin()`/`_check_is_admin()` at all — they re-implemented the branch
# logic inline and asserted on values the test itself had just assigned to
# MagicMocks (e.g. `assert interaction.user.guild_permissions.administrator is
# True` right after setting that same attribute to True two lines above).
# Each would still pass if `_check_is_admin` were deleted outright — the
# canonical SMELL this audit exists to catch. Deleted rather than rewritten:
# `TestIsAdminPredicateDirect` immediately below already invokes the real
# `_check_is_admin` predicate (via `asyncio.run(predicate(interaction))`) for
# every one of the same three scenarios plus more (developer override, Discord
# admin, API admin-role match, no-rights, API exception, role mismatch), so
# the intent is fully covered by a real behavioral test elsewhere.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TestIsAdminPredicateDirect — actually invoke the predicate to cover lines 24-46
# ---------------------------------------------------------------------------


def _extract_is_admin_predicate():
    """
    Import is_admin(), call it to obtain the decorator produced by
    app_commands.check(predicate), then walk the decorator's closure
    cells to find and return the async *predicate* function itself.
    """
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    import cogs.adminCog as _adm

    _is_admin_fn = _adm.is_admin
    decorator = _is_admin_fn()
    # app_commands.check wraps the predicate in a decorator whose closure
    # contains the original coroutine function.
    for cell in decorator.__closure__ or []:
        try:
            obj = cell.cell_contents
            if callable(obj) and asyncio.iscoroutinefunction(obj):
                return obj
        except ValueError:
            continue
    raise RuntimeError("Could not extract predicate from is_admin()")


class TestIsAdminPredicateDirect:
    """Tests that actually invoke the is_admin() predicate (covers lines 24-46)."""

    def test_predicate_returns_true_for_developer(self):
        """Predicate returns True when user.id is in DEVELOPERS env var (lines 24-26)."""
        predicate = _extract_is_admin_predicate()
        interaction = _create_mock_interaction()
        interaction.user = MagicMock()
        interaction.user.id = 999000111
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = False
        interaction.user.roles = []

        with patch.dict(os.environ, {"DEVELOPERS": "999000111,888000222"}):
            result = asyncio.run(predicate(interaction))
        assert result is True

    def test_predicate_returns_true_for_discord_admin(self):
        """Predicate returns True when user has Discord administrator perm (lines 28-30)."""
        predicate = _extract_is_admin_predicate()
        interaction = _create_mock_interaction()
        interaction.user = MagicMock()
        interaction.user.id = 123999
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = True
        interaction.user.roles = []

        with patch.dict(os.environ, {"DEVELOPERS": ""}):
            result = asyncio.run(predicate(interaction))
        assert result is True

    def test_predicate_returns_true_for_api_admin_role(self):
        """Predicate returns True when user has the configured admin role (lines 33-42).

        Bug fix: The check now uses guild.get_role(admin_role_id) + interaction.user.roles.
        interaction.user IS a discord.Member for guild slash commands and carries .roles.
        The old code used interaction.member which raised AttributeError (silently swallowed).

        Migrated to respx: pins the real GET /config/guild/{id} route+method rather than
        patching the whole httpx.AsyncClient class with a MagicMock chain (which asserts
        nothing about the outbound request).
        """
        predicate = _extract_is_admin_predicate()

        role = MagicMock()
        role.id = 444555666

        interaction = _create_mock_interaction()
        interaction.user = MagicMock()
        interaction.user.id = 123999
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = False
        # Bug fix: production code now checks interaction.user.roles via guild.get_role()
        interaction.user.roles = [role]
        interaction.guild_id = 987654321
        # guild.get_role must return the role object for the "in" check to work
        interaction.guild = MagicMock()
        interaction.guild.get_role = MagicMock(return_value=role)

        with (
            patch.dict(os.environ, {"DEVELOPERS": ""}),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.get(f"{_API_BASE}/config/guild/987654321").mock(
                return_value=httpx.Response(200, json={"admin_role_id": 444555666})
            )
            result = asyncio.run(predicate(interaction))
        assert result is True

    def test_predicate_returns_false_when_no_admin_rights(self):
        """Predicate returns False when user has no admin rights (line 46)."""
        predicate = _extract_is_admin_predicate()

        interaction = _create_mock_interaction()
        interaction.user = MagicMock()
        interaction.user.id = 123999
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = False
        interaction.user.roles = []
        interaction.guild_id = 987654321

        with (
            patch.dict(os.environ, {"DEVELOPERS": ""}),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.get(f"{_API_BASE}/config/guild/987654321").mock(
                return_value=httpx.Response(200, json={"admin_role_id": None})
            )
            result = asyncio.run(predicate(interaction))
        assert result is False

    def test_predicate_returns_false_on_api_exception(self):
        """Predicate returns False when the API call raises a network-level error (lines 43-44, 46)."""
        predicate = _extract_is_admin_predicate()

        interaction = _create_mock_interaction()
        interaction.user = MagicMock()
        interaction.user.id = 123999
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = False
        interaction.user.roles = []
        interaction.guild_id = 987654321

        with (
            patch.dict(os.environ, {"DEVELOPERS": ""}),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.get(f"{_API_BASE}/config/guild/987654321").mock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            result = asyncio.run(predicate(interaction))
        assert result is False

    def test_predicate_returns_false_when_role_does_not_match(self):
        """Predicate returns False when user has roles but none match admin_role_id (line 41-42, 46)."""
        predicate = _extract_is_admin_predicate()

        # User has roles, but none match the admin_role_id
        other_role = MagicMock()
        other_role.id = 999888777

        interaction = _create_mock_interaction()
        interaction.user = MagicMock()
        interaction.user.id = 123999
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = False
        interaction.user.roles = [other_role]
        interaction.guild_id = 987654321

        with (
            patch.dict(os.environ, {"DEVELOPERS": ""}),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.get(f"{_API_BASE}/config/guild/987654321").mock(
                return_value=httpx.Response(200, json={"admin_role_id": 444555666})
            )
            result = asyncio.run(predicate(interaction))
        assert result is False


# ---------------------------------------------------------------------------
# TestAdminPlayerViewStatsProper — covers lines 254-274 with proper mocking
# ---------------------------------------------------------------------------


class TestAdminPlayerViewStatsProper:
    """Tests for admin_player view_stats with proper http_client mocking (covers lines 254-274)."""

    def test_view_stats_builds_embed_correctly(self, mock_admin_cog):
        """view_stats should build a full embed with all player fields (lines 254-274)."""
        interaction = _create_mock_interaction()
        user = _create_mock_user(user_id=111111111, name="StatsPlayer")

        player_resp = MagicMock()
        player_resp.status_code = 200
        player_resp.json.return_value = {
            "id": 42,
            "discord_id": 111111111,
            "guild_id": 987654321,
            "tier": "Silver",
            "xp": 2500,
            "credits": 1200,
            "lifetime_credits": 3000,
            "prestige_count": 1,
            "created_at": "2024-06-15T12:00:00",
        }

        stats_resp = MagicMock()
        stats_resp.status_code = 200
        stats_resp.json.return_value = {"total_games": 10, "total_victory": 7, "total_defeat": 3}

        mock_admin_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_admin_cog.http_client.get = AsyncMock(return_value=stats_resp)

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "view_stats", None, None))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()

        # Verify embed was passed
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        assert call_kwargs["ephemeral"] is True


# ---------------------------------------------------------------------------
# TestAdminPlayerSetCreditsProper — covers lines 281-299 with proper mocking
# ---------------------------------------------------------------------------


class TestAdminPlayerSetCreditsProper:
    """Tests for admin_player set_credits with proper http_client mocking (covers lines 281-299)."""

    def test_set_credits_builds_embed_correctly(self, mock_admin_cog):
        """set_credits should PUT to API and build a credits-updated embed (lines 281-299)."""
        interaction = _create_mock_interaction()
        user = _create_mock_user(user_id=222222222, name="CreditPlayer")

        player_resp = MagicMock()
        player_resp.status_code = 200
        player_resp.json.return_value = {
            "id": 7,
            "discord_id": 222222222,
            "guild_id": 987654321,
            "tier": "Bronze",
            "xp": 50,
            "credits": 300,
            "lifetime_credits": 300,
            "prestige_count": 0,
            "created_at": "2024-01-01T00:00:00",
        }

        update_resp = MagicMock()
        update_resp.status_code = 200
        update_resp.json.return_value = {"old_credits": 300, "new_credits": 5000}

        mock_admin_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_admin_cog.http_client.put = AsyncMock(return_value=update_resp)

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "set_credits", 5000, None))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()

        # Verify embed was passed with correct data
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        assert call_kwargs["ephemeral"] is True

        # Verify the PUT was called with the correct payload
        put_call = mock_admin_cog.http_client.put.call_args
        put_json = put_call[1]["json"] if "json" in put_call[1] else put_call.kwargs["json"]
        assert put_json["player_id"] == 7
        assert put_json["credits"] == 5000
        assert put_json["update_lifetime"] is False

    def test_set_credits_clamps_negative_to_zero(self, mock_admin_cog):
        """set_credits should clamp negative credits to 0 (line 285: max(0, credit_amount))."""
        interaction = _create_mock_interaction()
        user = _create_mock_user(user_id=333333333, name="NegPlayer")

        player_resp = MagicMock()
        player_resp.status_code = 200
        player_resp.json.return_value = {
            "id": 8,
            "discord_id": 333333333,
            "guild_id": 987654321,
            "tier": "Bronze",
            "xp": 0,
            "credits": 100,
            "lifetime_credits": 100,
            "prestige_count": 0,
            "created_at": "2024-01-01T00:00:00",
        }

        update_resp = MagicMock()
        update_resp.status_code = 200
        update_resp.json.return_value = {"old_credits": 100, "new_credits": 0}

        mock_admin_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_admin_cog.http_client.put = AsyncMock(return_value=update_resp)

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "set_credits", -500, None))

        interaction.followup.send.assert_called_once()

        # Verify credits were clamped to 0
        put_call = mock_admin_cog.http_client.put.call_args
        put_json = put_call[1]["json"] if "json" in put_call[1] else put_call.kwargs["json"]
        assert put_json["credits"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
