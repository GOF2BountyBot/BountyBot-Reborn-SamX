"""
Tests for new bounty admin commands in adminCog:
  - /admin_clear_bounties
  - /admin_config_bounty (view and update)
  - /admin_spawn_bounty
"""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# -------------------------------------------------------------------------
# Bootstrap: mock shared.bblogger before any cog imports
# -------------------------------------------------------------------------

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")

_unused_module_logger = None


def _make_mock_logger(*_args, **_kwargs):
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


def _close_coro(coro):
    """Close a coroutine to prevent 'never awaited' RuntimeWarning."""
    coro.close()
    return MagicMock()


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

# Ensure real discord is used
for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from tests.mocks.discord_mock_utils import DiscordMockUtils

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


def _create_mock_interaction(guild_id: int = 987654321):
    interaction = DiscordMockUtils.create_mock_interaction()
    interaction.guild_id = guild_id
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.guild.name = "Test Guild"
    interaction.guild.icon = None
    return interaction


def _create_mock_user(user_id: int = 111111111, name: str = "TestAdmin"):
    user = DiscordMockUtils.create_mock_user(user_id=user_id, username=name)
    user.display_name = name
    return user


# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mock_bot():
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    bot.loop = MagicMock()
    bot.loop.create_task = MagicMock(side_effect=_close_coro)
    return bot


@pytest.fixture(scope="module")
def mock_admin_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.adminCog import AdminCog

    cog = AdminCog(mock_bot)
    return cog


# -------------------------------------------------------------------------
# Response builders
# -------------------------------------------------------------------------


def _real_response(json_data, status_code: int = 200) -> httpx.Response:
    """Build a **real** ``httpx.Response`` (not a bare MagicMock).

    Because it's real, ``.raise_for_status()`` genuinely raises ``httpx.HTTPStatusError``
    for any 4xx/5xx status instead of unconditionally no-op'ing — a status_code=500 build
    can no longer silently take the success path.
    """
    request = httpx.Request("GET", "http://bot-core.test/api/v1/resource")
    return httpx.Response(status_code, json=json_data, request=request)


def _make_clear_bounties_response(tier=None, cleared_count=3):
    return _real_response(
        {
            "guild_id": 987654321,
            "tier": tier,
            "cleared_count": cleared_count,
            "bounty_ids": list(range(1, cleared_count + 1)),
            "announcements_deleted": cleared_count,
        }
    )


def _make_bounty_config_status_response():
    return _real_response(
        {
            "guild_id": 987654321,
            "max_bounties_per_tier": {"bronze": 3, "silver": 3, "gold": 3},
            "bounty_expiry_minutes": 480,
            "bounty_spawn_interval_minutes": 60,
            "next_spawn_check_at": "2026-04-05T12:00:00+00:00",
            "active_bounties_per_tier": {"bronze": 2, "silver": 1, "gold": 0},
        }
    )


def _make_bounty_config_put_response():
    return _real_response(
        {
            "guild_id": 987654321,
            "max_bounties_per_tier": {"bronze": 5, "silver": 3, "gold": 3},
            "bounty_expiry_minutes": 600,
            "bounty_spawn_interval_minutes": 90,
        }
    )


def _make_admin_spawn_response(spawned_count=2, skipped_tiers=None):
    spawned = [
        {
            "id": i + 1,
            "criminal_name": f"Villain{i + 1}",
            "division": "bronze",
            "reward": 10000 + i * 1000,
            "tech_level": 3,
        }
        for i in range(spawned_count)
    ]
    return _real_response(
        {
            "guild_id": 987654321,
            "spawned": spawned,
            "skipped_tiers": skipped_tiers or [],
            "errors": [],
        }
    )


# -------------------------------------------------------------------------
# Tests: /admin_clear_bounties
# -------------------------------------------------------------------------


class TestAdminClearBounties:
    """Tests for /admin_clear_bounties command (B.50: ConfirmView button dialog)."""

    @pytest.fixture(autouse=True)
    def _patch_confirm_view(self, mock_admin_cog):
        """Patch ConfirmView so tests don't block on view.wait(). Default: result=True (user confirmed).

        Depends on mock_admin_cog to ensure this fixture runs AFTER the cog fixture has
        evicted and re-imported cogs.adminCog.  Without this dependency, pytest may patch
        the old module object before mock_admin_cog evicts it, leaving the freshly-imported
        cogs.adminCog.ConfirmView unpatched and causing view.wait() to block forever.
        """
        view_mock = MagicMock()
        view_mock.result = True
        view_mock.wait = AsyncMock(return_value=None)
        with patch("cogs.adminCog.ConfirmView", return_value=view_mock):
            yield

    def test_clear_bounties_cancel_flow(self, mock_admin_cog):
        """User clicks Cancel → DELETE API is NOT called; cancellation message is sent."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.delete = AsyncMock()

        # Override autouse fixture: simulate user clicking Cancel
        view_mock = MagicMock()
        view_mock.result = False
        view_mock.wait = AsyncMock(return_value=None)
        with patch("cogs.adminCog.ConfirmView", return_value=view_mock):
            asyncio.run(mock_admin_cog.admin_clear_bounties.callback(mock_admin_cog, interaction, tier=None))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        # DELETE must NOT have been called
        mock_admin_cog.http_client.delete.assert_not_called()
        # A cancellation message should have been sent (at least one followup send)
        interaction.followup.send.assert_called()

    def test_clear_bounties_all_tiers_success(self, mock_admin_cog):
        """User confirms (result=True) + no tier → clears all tiers, sends embed with orange color."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.delete = AsyncMock(return_value=_make_clear_bounties_response())

        asyncio.run(mock_admin_cog.admin_clear_bounties.callback(mock_admin_cog, interaction, tier=None))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        mock_admin_cog.http_client.delete.assert_called_once()
        # Verify URL contains the guild clear endpoint
        call_url = mock_admin_cog.http_client.delete.call_args[0][0]
        assert "bounties/guild" in call_url
        assert "clear" in call_url
        # Should send an embed response (prompt + result, at least one embed send)
        assert interaction.followup.send.call_count >= 1
        # The last send should contain the success embed, and its content (not just its
        # presence) must reflect the API's cleared_count — previously only "an embed key
        # exists" was checked, so a dropped/garbled count would have shipped green.
        last_kwargs = interaction.followup.send.call_args_list[-1][1]
        embed = last_kwargs.get("embed")
        assert embed is not None
        assert embed.title == "🗑️ Bounties Cleared"
        field_values = {f.name: f.value for f in embed.fields}
        assert field_values["Bounties Cleared"] == "3"

    def test_clear_bounties_with_tier_filter(self, mock_admin_cog):
        """User confirms + tier=bronze → includes tier in query params."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.delete = AsyncMock(return_value=_make_clear_bounties_response(tier="bronze"))

        asyncio.run(mock_admin_cog.admin_clear_bounties.callback(mock_admin_cog, interaction, tier="bronze"))

        mock_admin_cog.http_client.delete.assert_called_once()
        call_kwargs = mock_admin_cog.http_client.delete.call_args[1]
        params = call_kwargs.get("params", {})
        assert "tier" in params
        assert params["tier"] == "bronze"

    def test_clear_bounties_embed_has_orange_color(self, mock_admin_cog):
        """Success embed should use orange color (0xFFA500)."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.delete = AsyncMock(return_value=_make_clear_bounties_response())

        asyncio.run(mock_admin_cog.admin_clear_bounties.callback(mock_admin_cog, interaction, tier=None))

        # Find the call that has the success embed (last send after the confirmation prompt)
        embed_calls = [call[1] for call in interaction.followup.send.call_args_list if "embed" in call[1]]
        # The success result embed is the last embed sent
        result_embed = embed_calls[-1]["embed"]
        assert result_embed.color.value == 0xFFA500

    def test_clear_bounties_api_error(self, mock_admin_cog):
        """API error on confirm path → sends ephemeral error embed."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        import httpx

        mock_req = MagicMock()
        http_err = httpx.HTTPStatusError("Not Found", request=mock_req, response=MagicMock(status_code=404))
        mock_admin_cog.http_client.delete = AsyncMock(side_effect=http_err)

        asyncio.run(mock_admin_cog.admin_clear_bounties.callback(mock_admin_cog, interaction, tier=None))

        assert interaction.followup.send.call_count >= 1
        # B.31b: helper now sends a sanitized embed instead of a raw URL string.
        embed = interaction.followup.send.call_args.kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")
        assert "http://" not in (embed.description or "")

    def test_clear_bounties_generic_error(self, mock_admin_cog):
        """Generic exception on confirm path → sends ephemeral warning."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.delete = AsyncMock(side_effect=RuntimeError("oops"))

        asyncio.run(mock_admin_cog.admin_clear_bounties.callback(mock_admin_cog, interaction, tier=None))

        assert interaction.followup.send.call_count >= 1
        call_args = interaction.followup.send.call_args[0][0]
        assert "⚠️" in call_args

    def test_clear_bounties_includes_user_id_param(self, mock_admin_cog):
        """DELETE request must include user_id query param for audit logging."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user(user_id=555)

        mock_admin_cog.http_client.delete = AsyncMock(return_value=_make_clear_bounties_response())

        asyncio.run(mock_admin_cog.admin_clear_bounties.callback(mock_admin_cog, interaction, tier=None))

        call_kwargs = mock_admin_cog.http_client.delete.call_args[1]
        params = call_kwargs.get("params", {})
        assert "user_id" in params
        assert params["user_id"] == 555


# -------------------------------------------------------------------------
# Tests: /admin_config_bounty
# -------------------------------------------------------------------------


class TestAdminConfigBounty:
    """Tests for /admin_config_bounty command."""

    def test_config_bounty_view_calls_get(self, mock_admin_cog):
        """action='view' → GET /config/guild/{id}/bounty."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.get = AsyncMock(return_value=_make_bounty_config_status_response())

        asyncio.run(
            mock_admin_cog.admin_config_bounty.callback(
                mock_admin_cog,
                interaction,
                action="view",
                max_bronze=None,
                max_silver=None,
                max_gold=None,
                expiry_minutes=None,
                spawn_interval=None,
            )
        )

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        mock_admin_cog.http_client.get.assert_called_once()
        call_url = mock_admin_cog.http_client.get.call_args[0][0]
        assert "config/guild" in call_url
        assert "bounty" in call_url

    def test_config_bounty_view_embed_uses_discord_timestamp(self, mock_admin_cog):
        """View embed should show Discord relative timestamp for next_spawn_check_at."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.get = AsyncMock(return_value=_make_bounty_config_status_response())

        asyncio.run(
            mock_admin_cog.admin_config_bounty.callback(
                mock_admin_cog,
                interaction,
                action="view",
                max_bronze=None,
                max_silver=None,
                max_gold=None,
                expiry_minutes=None,
                spawn_interval=None,
            )
        )

        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        # The embed should contain a Discord timestamp (<t:...:R>) somewhere in its fields
        embed_text = str(embed.to_dict())
        assert "<t:" in embed_text

    def test_config_bounty_update_calls_put(self, mock_admin_cog):
        """action='update' with params → PUT /config/guild/{id}/bounty."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.put = AsyncMock(return_value=_make_bounty_config_put_response())

        asyncio.run(
            mock_admin_cog.admin_config_bounty.callback(
                mock_admin_cog,
                interaction,
                action="update",
                max_bronze=5,
                max_silver=None,
                max_gold=None,
                max_platinum=None,
                expiry_minutes=600,
                spawn_interval=None,
            )
        )

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        mock_admin_cog.http_client.put.assert_called_once()
        call_url = mock_admin_cog.http_client.put.call_args[0][0]
        assert "config/guild" in call_url
        assert "bounty" in call_url

    def test_config_bounty_update_payload_uses_nested_tier_dict(self, mock_admin_cog):
        """Update payload must use max_bounties_per_tier nested dict, not flat max_bronze etc."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.put = AsyncMock(return_value=_make_bounty_config_put_response())

        asyncio.run(
            mock_admin_cog.admin_config_bounty.callback(
                mock_admin_cog,
                interaction,
                action="update",
                max_bronze=5,
                max_silver=None,
                max_gold=None,
                max_platinum=None,
                expiry_minutes=600,
                spawn_interval=None,
            )
        )

        call_kwargs = mock_admin_cog.http_client.put.call_args[1]
        payload = call_kwargs.get("json", {})
        # Flat fields must NOT be present
        assert "max_bronze" not in payload
        assert "max_silver" not in payload
        assert "max_gold" not in payload
        assert "max_platinum" not in payload
        # Nested dict must be present with only bronze (which was set)
        assert "max_bounties_per_tier" in payload
        assert payload["max_bounties_per_tier"]["bronze"] == 5
        assert "silver" not in payload["max_bounties_per_tier"]
        # expiry_minutes is a top-level field
        assert payload["bounty_expiry_minutes"] == 600

    def test_config_bounty_update_payload_omits_tier_dict_when_no_tiers_given(self, mock_admin_cog):
        """If no tier values are provided, max_bounties_per_tier must not appear in payload."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.put = AsyncMock(return_value=_make_bounty_config_put_response())

        asyncio.run(
            mock_admin_cog.admin_config_bounty.callback(
                mock_admin_cog,
                interaction,
                action="update",
                max_bronze=None,
                max_silver=None,
                max_gold=None,
                max_platinum=None,
                expiry_minutes=600,
                spawn_interval=None,
            )
        )

        call_kwargs = mock_admin_cog.http_client.put.call_args[1]
        payload = call_kwargs.get("json", {})
        assert "max_bounties_per_tier" not in payload
        assert payload["bounty_expiry_minutes"] == 600

    def test_config_bounty_update_platinum_tier(self, mock_admin_cog):
        """max_platinum param is sent inside max_bounties_per_tier nested dict."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.put = AsyncMock(return_value=_make_bounty_config_put_response())

        asyncio.run(
            mock_admin_cog.admin_config_bounty.callback(
                mock_admin_cog,
                interaction,
                action="update",
                max_bronze=None,
                max_silver=None,
                max_gold=None,
                max_platinum=2,
                expiry_minutes=None,
                spawn_interval=None,
            )
        )

        call_kwargs = mock_admin_cog.http_client.put.call_args[1]
        payload = call_kwargs.get("json", {})
        assert "max_bounties_per_tier" in payload
        assert payload["max_bounties_per_tier"]["platinum"] == 2
        assert "bronze" not in payload["max_bounties_per_tier"]

    def test_config_bounty_update_all_tiers_nested_dict(self, mock_admin_cog):
        """All four tiers provided → max_bounties_per_tier contains all four keys."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.put = AsyncMock(return_value=_make_bounty_config_put_response())

        asyncio.run(
            mock_admin_cog.admin_config_bounty.callback(
                mock_admin_cog,
                interaction,
                action="update",
                max_bronze=3,
                max_silver=3,
                max_gold=3,
                max_platinum=1,
                expiry_minutes=None,
                spawn_interval=None,
            )
        )

        call_kwargs = mock_admin_cog.http_client.put.call_args[1]
        payload = call_kwargs.get("json", {})
        assert "max_bounties_per_tier" in payload
        tiers = payload["max_bounties_per_tier"]
        assert tiers["bronze"] == 3
        assert tiers["silver"] == 3
        assert tiers["gold"] == 3
        assert tiers["platinum"] == 1

    def test_config_bounty_update_spawn_interval_top_level(self, mock_admin_cog):
        """spawn_interval param maps to bounty_spawn_interval_minutes as a top-level field."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.put = AsyncMock(return_value=_make_bounty_config_put_response())

        asyncio.run(
            mock_admin_cog.admin_config_bounty.callback(
                mock_admin_cog,
                interaction,
                action="update",
                max_bronze=None,
                max_silver=None,
                max_gold=None,
                max_platinum=None,
                expiry_minutes=None,
                spawn_interval=30,
            )
        )

        call_kwargs = mock_admin_cog.http_client.put.call_args[1]
        payload = call_kwargs.get("json", {})
        assert payload["bounty_spawn_interval_minutes"] == 30
        assert "max_bounties_per_tier" not in payload

    def test_config_bounty_view_api_error(self, mock_admin_cog):
        """API error in view → ephemeral error message."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        import httpx

        mock_req = MagicMock()
        http_err = httpx.HTTPStatusError("Server Error", request=mock_req, response=MagicMock(status_code=500))
        mock_admin_cog.http_client.get = AsyncMock(side_effect=http_err)

        asyncio.run(
            mock_admin_cog.admin_config_bounty.callback(
                mock_admin_cog,
                interaction,
                action="view",
                max_bronze=None,
                max_silver=None,
                max_gold=None,
                expiry_minutes=None,
                spawn_interval=None,
            )
        )

        interaction.followup.send.assert_called_once()
        # B.31b: helper now sends a sanitized embed instead of a raw URL string.
        embed = interaction.followup.send.call_args.kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")

    def test_config_bounty_update_api_error(self, mock_admin_cog):
        """API error in update → ephemeral error message."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        import httpx

        mock_req = MagicMock()
        http_err = httpx.HTTPStatusError("Bad Request", request=mock_req, response=MagicMock(status_code=400))
        mock_admin_cog.http_client.put = AsyncMock(side_effect=http_err)

        asyncio.run(
            mock_admin_cog.admin_config_bounty.callback(
                mock_admin_cog,
                interaction,
                action="update",
                max_bronze=5,
                max_silver=None,
                max_gold=None,
                expiry_minutes=None,
                spawn_interval=None,
            )
        )

        interaction.followup.send.assert_called_once()
        # B.31b: helper now sends a sanitized embed instead of a raw URL string.
        embed = interaction.followup.send.call_args.kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")

    def test_config_bounty_generic_error(self, mock_admin_cog):
        """Generic exception → sends ephemeral warning."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.get = AsyncMock(side_effect=RuntimeError("connection refused"))

        asyncio.run(
            mock_admin_cog.admin_config_bounty.callback(
                mock_admin_cog,
                interaction,
                action="view",
                max_bronze=None,
                max_silver=None,
                max_gold=None,
                expiry_minutes=None,
                spawn_interval=None,
            )
        )

        interaction.followup.send.assert_called_once()
        args = interaction.followup.send.call_args[0]
        assert "⚠️" in args[0]


# -------------------------------------------------------------------------
# Tests: /admin_spawn_bounty
# -------------------------------------------------------------------------


class TestAdminSpawnBounty:
    """Tests for /admin_spawn_bounty command."""

    def test_spawn_bounty_no_tier_success(self, mock_admin_cog):
        """No tier → POST to admin-spawn with no tier param."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.post = AsyncMock(return_value=_make_admin_spawn_response(spawned_count=2))

        asyncio.run(mock_admin_cog.admin_spawn_bounty.callback(mock_admin_cog, interaction, tier=None))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        mock_admin_cog.http_client.post.assert_called_once()
        call_url = mock_admin_cog.http_client.post.call_args[0][0]
        assert "bounties/guild" in call_url
        assert "admin-spawn" in call_url
        # Response should be an embed
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_spawn_bounty_with_tier_includes_param(self, mock_admin_cog):
        """tier=silver → POST includes tier in query params."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.post = AsyncMock(return_value=_make_admin_spawn_response(spawned_count=1))

        asyncio.run(mock_admin_cog.admin_spawn_bounty.callback(mock_admin_cog, interaction, tier="silver"))

        mock_admin_cog.http_client.post.assert_called_once()
        call_kwargs = mock_admin_cog.http_client.post.call_args[1]
        params = call_kwargs.get("params", {})
        assert "tier" in params
        assert params["tier"] == "silver"

    def test_spawn_bounty_embed_shows_spawned_names(self, mock_admin_cog):
        """Success embed should display criminal names from response."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.post = AsyncMock(return_value=_make_admin_spawn_response(spawned_count=2))

        asyncio.run(mock_admin_cog.admin_spawn_bounty.callback(mock_admin_cog, interaction, tier=None))

        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]
        embed_dict = embed.to_dict()
        embed_text = str(embed_dict)
        # Criminal names from the mock response should appear in the embed
        assert "Villain1" in embed_text or "Villain2" in embed_text

    def test_spawn_bounty_no_tier_param_in_url(self, mock_admin_cog):
        """When tier is None, tier param must NOT be in request params."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.post = AsyncMock(return_value=_make_admin_spawn_response())

        asyncio.run(mock_admin_cog.admin_spawn_bounty.callback(mock_admin_cog, interaction, tier=None))

        call_kwargs = mock_admin_cog.http_client.post.call_args[1]
        params = call_kwargs.get("params", {})
        assert "tier" not in params

    def test_spawn_bounty_includes_user_id_param(self, mock_admin_cog):
        """POST request must include user_id query param for audit logging."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user(user_id=777)

        mock_admin_cog.http_client.post = AsyncMock(return_value=_make_admin_spawn_response())

        asyncio.run(mock_admin_cog.admin_spawn_bounty.callback(mock_admin_cog, interaction, tier=None))

        call_kwargs = mock_admin_cog.http_client.post.call_args[1]
        params = call_kwargs.get("params", {})
        assert "user_id" in params
        assert params["user_id"] == 777

    def test_spawn_bounty_api_error(self, mock_admin_cog):
        """API error → sends ephemeral error message."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        import httpx

        mock_req = MagicMock()
        http_err = httpx.HTTPStatusError("Internal Error", request=mock_req, response=MagicMock(status_code=500))
        mock_admin_cog.http_client.post = AsyncMock(side_effect=http_err)

        asyncio.run(mock_admin_cog.admin_spawn_bounty.callback(mock_admin_cog, interaction, tier=None))

        interaction.followup.send.assert_called_once()
        # B.31b: helper now sends a sanitized embed instead of a raw URL string.
        embed = interaction.followup.send.call_args.kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")

    def test_spawn_bounty_generic_error(self, mock_admin_cog):
        """Generic exception → sends ephemeral warning."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.post = AsyncMock(side_effect=RuntimeError("network down"))

        asyncio.run(mock_admin_cog.admin_spawn_bounty.callback(mock_admin_cog, interaction, tier=None))

        interaction.followup.send.assert_called_once()
        args = interaction.followup.send.call_args[0]
        assert "⚠️" in args[0]

    def test_spawn_bounty_skipped_tiers_in_embed(self, mock_admin_cog):
        """Embed should include info about skipped tiers if present."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.post = AsyncMock(
            return_value=_make_admin_spawn_response(spawned_count=1, skipped_tiers=["gold"])
        )

        asyncio.run(mock_admin_cog.admin_spawn_bounty.callback(mock_admin_cog, interaction, tier=None))

        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]
        embed_text = str(embed.to_dict())
        # gold should be mentioned as skipped
        assert "gold" in embed_text.lower()

    def test_spawn_bounty_uses_60s_timeout(self, mock_admin_cog):
        """Admin-spawn POST must use a 60s timeout to match bot-core's upload client (P3-T4 / G-T3)."""
        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        mock_admin_cog.http_client.post = AsyncMock(return_value=_make_admin_spawn_response())

        asyncio.run(mock_admin_cog.admin_spawn_bounty.callback(mock_admin_cog, interaction, tier=None))

        mock_admin_cog.http_client.post.assert_called_once()
        call_kwargs = mock_admin_cog.http_client.post.call_args[1]
        assert call_kwargs.get("timeout") == 60, (
            f"Expected timeout=60 to match bot-core upload client, got {call_kwargs.get('timeout')}"
        )

    def test_spawn_bounty_timeout_surfaces_graceful_error_no_retry(self, mock_admin_cog):
        """httpx.TimeoutException on admin-spawn must surface a graceful ⚠️ error embed and NOT trigger a retry.

        Admin-spawn is non-idempotent — a second POST would double-spawn bounties.
        The except-Exception handler must catch the timeout exactly once and send
        a single ephemeral warning, not re-issue the request.
        """
        import httpx as _httpx

        interaction = _create_mock_interaction()
        interaction.user = _create_mock_user()

        # Raise TimeoutException on every call — if there were a retry loop this
        # AsyncMock would be called more than once.
        timeout_exc = _httpx.TimeoutException("timed out")
        mock_admin_cog.http_client.post = AsyncMock(side_effect=timeout_exc)

        asyncio.run(mock_admin_cog.admin_spawn_bounty.callback(mock_admin_cog, interaction, tier=None))

        # POST must have been attempted exactly once — no retry on this non-idempotent path.
        assert mock_admin_cog.http_client.post.call_count == 1, (
            "admin_spawn_bounty must NOT retry on timeout (non-idempotent: double-spawn risk)"
        )
        # User receives a single graceful warning message, not a silent failure.
        interaction.followup.send.assert_called_once()
        args = interaction.followup.send.call_args[0]
        assert "⚠️" in args[0]
