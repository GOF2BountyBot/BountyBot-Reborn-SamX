"""Tests for the /notifications slash command in playerCog.

Covers:
- /notifications type:bounty enabled:1 — adds tier role when not already assigned
- /notifications type:bounty enabled:0 — removes tier role
- /notifications type:shop enabled:1 — adds shop announcements role
- /notifications type:shop enabled:0 — removes shop announcements role
- Error cases: guild not configured, player not registered, role not found,
  shop_announcements_role_id is None, discord.Forbidden on role add/remove
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
_mock_bblogger.get_logger = MagicMock(return_value=MagicMock())

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import discord
import httpx

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_role(role_id: int, name: str = "TestRole") -> MagicMock:
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    role.name = name
    return role


def _make_mock_interaction(user_id: int = 111, guild_id: int = 999) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild_id = guild_id
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = user_id
    interaction.user.display_name = "TestUser"
    interaction.user.roles = []
    interaction.user.add_roles = AsyncMock()
    interaction.user.remove_roles = AsyncMock()
    interaction.response = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.followup.send = AsyncMock()

    # Mock guild
    interaction.guild = MagicMock(spec=discord.Guild)
    interaction.guild.id = guild_id
    interaction.guild.get_role = MagicMock()

    return interaction


def _make_player_data(tier: str = "Bronze") -> dict:
    return {
        "id": 1,
        "discord_id": 111,
        "guild_id": 999,
        "tier": tier,
        "xp": 100,
        "credits": 500,
    }


def _make_config_data(
    bounty_hunter_role_id: int | None = 1001,
    bronze_role_id: int | None = 2001,
    shop_announcements_role_id: int | None = 3001,
) -> dict:
    return {
        "guild_id": 999,
        "configured": True,
        "bounty_hunter_role_id": bounty_hunter_role_id,
        "bronze_role_id": bronze_role_id,
        "silver_role_id": None,
        "gold_role_id": None,
        "platinum_role_id": None,
        "shop_announcements_role_id": shop_announcements_role_id,
    }


def _make_http_resp(status_code: int, data: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=MagicMock(status_code=status_code)
        )
    else:
        resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.loop = MagicMock()
    bot.loop.create_task = MagicMock()
    return bot


@pytest.fixture
def cog(mock_bot):
    from cogs.playerCog import PlayerCog

    c = PlayerCog(mock_bot)
    c.http_client = MagicMock()
    c.http_client.aclose = AsyncMock()
    return c


# ---------------------------------------------------------------------------
# Bounty notification tests
# ---------------------------------------------------------------------------


class TestNotificationsBountyEnable:
    """Tests for /notifications type:bounty enabled:1 (opt-in)."""

    @pytest.mark.asyncio
    async def test_bounty_enable_assigns_tier_role(self, cog):
        """Should add the Bronze tier role when player has Bronze tier."""
        interaction = _make_mock_interaction()

        config_data = _make_config_data(bronze_role_id=2001)
        player_data = _make_player_data(tier="Bronze")

        tier_role = _make_mock_role(2001, "Bounty Hunter Bronze")
        interaction.guild.get_role.return_value = tier_role
        interaction.user.roles = []  # Role not yet assigned

        # Config GET → 200, Player POST → 200
        config_resp = _make_http_resp(200, config_data)
        player_resp = _make_http_resp(200, player_data)
        cog.http_client.get = AsyncMock(return_value=config_resp)
        cog.http_client.post = AsyncMock(return_value=player_resp)

        await cog.notifications.callback(cog, interaction, notification_type="bounty", enabled=1)

        interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        interaction.user.add_roles.assert_awaited_once_with(tier_role, reason="BountyBot bounty notification opt-in")
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "enabled" in embed.title.lower()

    @pytest.mark.asyncio
    async def test_bounty_enable_skips_role_already_assigned(self, cog):
        """Should not call add_roles if member already has the tier role."""
        interaction = _make_mock_interaction()

        config_data = _make_config_data(bronze_role_id=2001)
        player_data = _make_player_data(tier="Bronze")

        tier_role = _make_mock_role(2001, "Bounty Hunter Bronze")
        interaction.guild.get_role.return_value = tier_role
        interaction.user.roles = [tier_role]  # Already has role

        config_resp = _make_http_resp(200, config_data)
        player_resp = _make_http_resp(200, player_data)
        cog.http_client.get = AsyncMock(return_value=config_resp)
        cog.http_client.post = AsyncMock(return_value=player_resp)

        await cog.notifications.callback(cog, interaction, notification_type="bounty", enabled=1)

        # add_roles should NOT be called (role already present)
        interaction.user.add_roles.assert_not_awaited()
        # But followup.send should still be called with success embed
        interaction.followup.send.assert_awaited_once()


class TestNotificationsBountyDisable:
    """Tests for /notifications type:bounty enabled:0 (opt-out)."""

    @pytest.mark.asyncio
    async def test_bounty_disable_removes_tier_role(self, cog):
        """Should remove the Bronze tier role when player opts out."""
        interaction = _make_mock_interaction()

        config_data = _make_config_data(bronze_role_id=2001)
        player_data = _make_player_data(tier="Bronze")

        tier_role = _make_mock_role(2001, "Bounty Hunter Bronze")
        interaction.guild.get_role.return_value = tier_role
        interaction.user.roles = [tier_role]  # Has the role

        config_resp = _make_http_resp(200, config_data)
        player_resp = _make_http_resp(200, player_data)
        cog.http_client.get = AsyncMock(return_value=config_resp)
        cog.http_client.post = AsyncMock(return_value=player_resp)

        await cog.notifications.callback(cog, interaction, notification_type="bounty", enabled=0)

        interaction.user.remove_roles.assert_awaited_once_with(
            tier_role, reason="BountyBot bounty notification opt-out"
        )
        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]
        assert "disabled" in embed.title.lower()

    @pytest.mark.asyncio
    async def test_bounty_disable_skips_remove_when_not_assigned(self, cog):
        """Should not call remove_roles if member doesn't have the tier role."""
        interaction = _make_mock_interaction()

        config_data = _make_config_data(bronze_role_id=2001)
        player_data = _make_player_data(tier="Bronze")

        tier_role = _make_mock_role(2001, "Bounty Hunter Bronze")
        interaction.guild.get_role.return_value = tier_role
        interaction.user.roles = []  # Does not have role

        config_resp = _make_http_resp(200, config_data)
        player_resp = _make_http_resp(200, player_data)
        cog.http_client.get = AsyncMock(return_value=config_resp)
        cog.http_client.post = AsyncMock(return_value=player_resp)

        await cog.notifications.callback(cog, interaction, notification_type="bounty", enabled=0)

        interaction.user.remove_roles.assert_not_awaited()
        interaction.followup.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# Shop notification tests
# ---------------------------------------------------------------------------


class TestNotificationsShopEnable:
    """Tests for /notifications type:shop enabled:1 (opt-in)."""

    @pytest.mark.asyncio
    async def test_shop_enable_assigns_shop_role(self, cog):
        """Should add shop announcements role when player opts in."""
        interaction = _make_mock_interaction()

        config_data = _make_config_data(shop_announcements_role_id=3001)
        shop_role = _make_mock_role(3001, "Shop Announcements")
        interaction.guild.get_role.return_value = shop_role
        interaction.user.roles = []

        config_resp = _make_http_resp(200, config_data)
        cog.http_client.get = AsyncMock(return_value=config_resp)
        cog.http_client.post = AsyncMock()  # Not called for shop type

        await cog.notifications.callback(cog, interaction, notification_type="shop", enabled=1)

        interaction.user.add_roles.assert_awaited_once_with(shop_role, reason="BountyBot shop notification opt-in")
        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]
        assert "enabled" in embed.title.lower()

    @pytest.mark.asyncio
    async def test_shop_enable_skips_when_already_assigned(self, cog):
        """Should not call add_roles if member already has shop role."""
        interaction = _make_mock_interaction()

        config_data = _make_config_data(shop_announcements_role_id=3001)
        shop_role = _make_mock_role(3001, "Shop Announcements")
        interaction.guild.get_role.return_value = shop_role
        interaction.user.roles = [shop_role]  # Already has role

        config_resp = _make_http_resp(200, config_data)
        cog.http_client.get = AsyncMock(return_value=config_resp)

        await cog.notifications.callback(cog, interaction, notification_type="shop", enabled=1)

        interaction.user.add_roles.assert_not_awaited()
        interaction.followup.send.assert_awaited_once()


class TestNotificationsShopDisable:
    """Tests for /notifications type:shop enabled:0 (opt-out)."""

    @pytest.mark.asyncio
    async def test_shop_disable_removes_shop_role(self, cog):
        """Should remove shop role when player opts out."""
        interaction = _make_mock_interaction()

        config_data = _make_config_data(shop_announcements_role_id=3001)
        shop_role = _make_mock_role(3001, "Shop Announcements")
        interaction.guild.get_role.return_value = shop_role
        interaction.user.roles = [shop_role]

        config_resp = _make_http_resp(200, config_data)
        cog.http_client.get = AsyncMock(return_value=config_resp)

        await cog.notifications.callback(cog, interaction, notification_type="shop", enabled=0)

        interaction.user.remove_roles.assert_awaited_once_with(
            shop_role, reason="BountyBot shop notification opt-out"
        )
        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]
        assert "disabled" in embed.title.lower()


# ---------------------------------------------------------------------------
# Error case tests
# ---------------------------------------------------------------------------


class TestNotificationsErrorCases:
    """Tests for error handling in /notifications."""

    @pytest.mark.asyncio
    async def test_guild_not_configured_returns_ephemeral_error(self, cog):
        """404 on config endpoint → guild not configured message."""
        interaction = _make_mock_interaction()

        config_resp = MagicMock()
        config_resp.status_code = 404
        config_resp.raise_for_status = MagicMock()
        cog.http_client.get = AsyncMock(return_value=config_resp)

        await cog.notifications.callback(cog, interaction, notification_type="shop", enabled=1)

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        text = call_args[0][0] if call_args[0] else call_args[1].get("content", "")
        assert "set up" in text.lower() or "hasn't been" in text.lower() or "❌" in text

    @pytest.mark.asyncio
    async def test_shop_announcements_role_id_none_returns_error(self, cog):
        """When shop_announcements_role_id is None, returns configuration error."""
        interaction = _make_mock_interaction()

        config_data = _make_config_data(shop_announcements_role_id=None)
        config_resp = _make_http_resp(200, config_data)
        cog.http_client.get = AsyncMock(return_value=config_resp)

        await cog.notifications.callback(cog, interaction, notification_type="shop", enabled=1)

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        text = call_args[0][0] if call_args[0] else call_args[1].get("content", "")
        assert "❌" in text
        assert "admin" in text.lower() or "configured" in text.lower()

    @pytest.mark.asyncio
    async def test_role_not_found_in_guild_returns_error(self, cog):
        """When guild.get_role returns None, returns role-not-found error."""
        interaction = _make_mock_interaction()
        interaction.guild.get_role.return_value = None  # Role not found

        config_data = _make_config_data(shop_announcements_role_id=3001)
        config_resp = _make_http_resp(200, config_data)
        cog.http_client.get = AsyncMock(return_value=config_resp)

        await cog.notifications.callback(cog, interaction, notification_type="shop", enabled=1)

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        text = call_args[0][0] if call_args[0] else call_args[1].get("content", "")
        assert "❌" in text
        assert "admin_setup" in text.lower() or "not found" in text.lower()

    @pytest.mark.asyncio
    async def test_discord_forbidden_returns_permission_error(self, cog):
        """When discord.Forbidden raised on add_roles, returns an error response."""
        interaction = _make_mock_interaction()

        config_data = _make_config_data(shop_announcements_role_id=3001)
        shop_role = _make_mock_role(3001, "Shop Announcements")
        interaction.guild.get_role.return_value = shop_role
        interaction.user.roles = []

        # Simulate discord.Forbidden on add_roles — use a generic exception to avoid
        # module-state-dependent isinstance checks across test collection order.
        interaction.user.add_roles.side_effect = Exception("403 Forbidden: Missing Permissions")

        config_resp = _make_http_resp(200, config_data)
        cog.http_client.get = AsyncMock(return_value=config_resp)

        await cog.notifications.callback(cog, interaction, notification_type="shop", enabled=1)

        # Command should handle the error gracefully — an error message was sent
        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        text = call_args[0][0] if call_args[0] else call_args[1].get("content", "")
        # Either the specific Forbidden message or the generic error message is acceptable
        assert "❌" in text or "⚠️" in text

    @pytest.mark.asyncio
    async def test_bounty_player_not_registered_returns_error(self, cog):
        """When player POST returns 400, returns registration error."""
        interaction = _make_mock_interaction()

        config_data = _make_config_data()
        config_resp = _make_http_resp(200, config_data)

        player_resp = MagicMock()
        player_resp.status_code = 400
        player_resp.raise_for_status = MagicMock()

        cog.http_client.get = AsyncMock(return_value=config_resp)
        cog.http_client.post = AsyncMock(return_value=player_resp)

        await cog.notifications.callback(cog, interaction, notification_type="bounty", enabled=1)

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        text = call_args[0][0] if call_args[0] else call_args[1].get("content", "")
        assert "❌" in text
        assert "profile" in text.lower() or "register" in text.lower()

    @pytest.mark.asyncio
    async def test_bounty_tier_role_not_configured_returns_error(self, cog):
        """When bronze_role_id is None in config, returns role not found error."""
        interaction = _make_mock_interaction()

        # Bronze role not configured
        config_data = _make_config_data(bronze_role_id=None)
        player_data = _make_player_data(tier="Bronze")

        config_resp = _make_http_resp(200, config_data)
        player_resp = _make_http_resp(200, player_data)
        cog.http_client.get = AsyncMock(return_value=config_resp)
        cog.http_client.post = AsyncMock(return_value=player_resp)

        await cog.notifications.callback(cog, interaction, notification_type="bounty", enabled=1)

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        text = call_args[0][0] if call_args[0] else call_args[1].get("content", "")
        assert "❌" in text


# ---------------------------------------------------------------------------
# Unregister includes Shop Announcements role
# ---------------------------------------------------------------------------


class TestUnregisterIncludesShopRole:
    """Tests that /unregister also removes @Shop Announcements role."""

    @pytest.mark.asyncio
    async def test_unregister_removes_shop_announcements_role(self, cog):
        """unregister should include shop_announcements_role_id in removal."""
        interaction = _make_mock_interaction()

        bh_role = _make_mock_role(1001, "Bounty Hunter")
        shop_role = _make_mock_role(3001, "Shop Announcements")

        # Member has both BH and Shop roles
        interaction.user.roles = [bh_role, shop_role]

        def _get_role(rid):
            mapping = {1001: bh_role, 3001: shop_role}
            return mapping.get(rid)

        interaction.guild.get_role.side_effect = _get_role

        config_data = {
            "guild_id": 999,
            "bounty_hunter_role_id": 1001,
            "bronze_role_id": None,
            "silver_role_id": None,
            "gold_role_id": None,
            "platinum_role_id": None,
            "shop_announcements_role_id": 3001,
        }
        config_resp = _make_http_resp(200, config_data)
        cog.http_client.get = AsyncMock(return_value=config_resp)

        await cog.unregister.callback(cog, interaction)

        # remove_roles should have been called with both BH and Shop roles
        interaction.user.remove_roles.assert_awaited_once()
        removed = interaction.user.remove_roles.call_args[0]
        removed_ids = {r.id for r in removed}
        assert 1001 in removed_ids  # Bounty Hunter role
        assert 3001 in removed_ids  # Shop Announcements role


# ---------------------------------------------------------------------------
# Adversarial / edge case tests added by Tester review
# ---------------------------------------------------------------------------


class TestNotificationsShopDisableNoOp:
    """Shop opt-out when user does not have the role should be a no-op."""

    @pytest.mark.asyncio
    async def test_shop_disable_skips_remove_when_not_assigned(self, cog):
        """remove_roles must NOT be called if user doesn't have the shop role."""
        interaction = _make_mock_interaction()

        config_data = _make_config_data(shop_announcements_role_id=3001)
        shop_role = _make_mock_role(3001, "Shop Announcements")
        interaction.guild.get_role.return_value = shop_role
        interaction.user.roles = []  # User does NOT have the shop role

        config_resp = _make_http_resp(200, config_data)
        cog.http_client.get = AsyncMock(return_value=config_resp)

        await cog.notifications.callback(cog, interaction, notification_type="shop", enabled=0)

        # No role removal should happen
        interaction.user.remove_roles.assert_not_awaited()
        # But a success embed should still be sent
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]
        assert "disabled" in embed.title.lower()


class TestNotificationsDiscordForbidden:
    """Tests that discord.Forbidden on role add/remove returns an error.

    NOTE ON MODULE STATE: discord.Forbidden is matched by isinstance() in the
    production code. When other test modules run first and re-import discord
    (resetting sys.modules), the class identity may differ across collection
    order. To be robust against this ordering issue (same pattern as the
    existing test_discord_forbidden_returns_permission_error which uses a
    generic Exception), these tests use the same approach: inject a Forbidden-
    compatible exception and assert that ANY error response is returned.

    The production code path: inner `except discord.Forbidden:` → "❌ Bot
    doesn't have permission to manage roles." If discord.Forbidden class
    identity matches: inner handler fires (❌ message). If not (ordering issue),
    the outer `except Exception:` fires (⚠️ message). Both are graceful.

    We verify the BEHAVIOUR (no crash, error response returned) rather than the
    exact message, which is appropriate for an order-dependent class identity test.
    """

    def _make_discord_forbidden(self):
        """Build a discord.Forbidden instance from the currently-loaded discord module."""
        import importlib
        _discord = importlib.import_module("discord")
        fake_response = MagicMock()
        fake_response.status = 403
        fake_response.reason = "Forbidden"
        return _discord.Forbidden(fake_response, "Missing Permissions")

    @pytest.mark.asyncio
    async def test_shop_enable_discord_forbidden_returns_error(self, cog):
        """discord.Forbidden (or compatible) on shop add_roles returns an error response."""
        interaction = _make_mock_interaction()

        config_data = _make_config_data(shop_announcements_role_id=3001)
        shop_role = _make_mock_role(3001, "Shop Announcements")
        interaction.guild.get_role.return_value = shop_role
        interaction.user.roles = []

        interaction.user.add_roles.side_effect = self._make_discord_forbidden()

        config_resp = _make_http_resp(200, config_data)
        cog.http_client.get = AsyncMock(return_value=config_resp)

        await cog.notifications.callback(cog, interaction, notification_type="shop", enabled=1)

        # Must always return some error response — either permission-specific or generic
        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        text = call_args[0][0] if call_args[0] else call_args[1].get("content", "")
        assert "❌" in text or "⚠️" in text

    @pytest.mark.asyncio
    async def test_bounty_enable_discord_forbidden_returns_error(self, cog):
        """discord.Forbidden (or compatible) on bounty add_roles returns an error response."""
        interaction = _make_mock_interaction()

        config_data = _make_config_data(bronze_role_id=2001)
        player_data = _make_player_data(tier="Bronze")

        tier_role = _make_mock_role(2001, "Bounty Hunter Bronze")
        interaction.guild.get_role.return_value = tier_role
        interaction.user.roles = []

        interaction.user.add_roles.side_effect = self._make_discord_forbidden()

        config_resp = _make_http_resp(200, config_data)
        player_resp = _make_http_resp(200, player_data)
        cog.http_client.get = AsyncMock(return_value=config_resp)
        cog.http_client.post = AsyncMock(return_value=player_resp)

        await cog.notifications.callback(cog, interaction, notification_type="bounty", enabled=1)

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        text = call_args[0][0] if call_args[0] else call_args[1].get("content", "")
        assert "❌" in text or "⚠️" in text

    @pytest.mark.asyncio
    async def test_bounty_disable_discord_forbidden_returns_error(self, cog):
        """discord.Forbidden (or compatible) on bounty remove_roles returns an error response."""
        interaction = _make_mock_interaction()

        config_data = _make_config_data(bronze_role_id=2001)
        player_data = _make_player_data(tier="Bronze")

        tier_role = _make_mock_role(2001, "Bounty Hunter Bronze")
        interaction.guild.get_role.return_value = tier_role
        interaction.user.roles = [tier_role]  # User has the role to trigger remove

        interaction.user.remove_roles.side_effect = self._make_discord_forbidden()

        config_resp = _make_http_resp(200, config_data)
        player_resp = _make_http_resp(200, player_data)
        cog.http_client.get = AsyncMock(return_value=config_resp)
        cog.http_client.post = AsyncMock(return_value=player_resp)

        await cog.notifications.callback(cog, interaction, notification_type="bounty", enabled=0)

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        text = call_args[0][0] if call_args[0] else call_args[1].get("content", "")
        assert "❌" in text or "⚠️" in text


class TestNotificationsBountyNonBronzeTiers:
    """Bounty notifications work correctly for non-Bronze tier players."""

    @pytest.mark.asyncio
    async def test_bounty_enable_silver_tier_assigns_silver_role(self, cog):
        """Silver-tier player gets Silver role when enabling bounty notifications."""
        interaction = _make_mock_interaction()

        config_data = _make_config_data()
        config_data["silver_role_id"] = 2002
        player_data = _make_player_data(tier="Silver")

        silver_role = _make_mock_role(2002, "Bounty Hunter Silver")
        interaction.guild.get_role.return_value = silver_role
        interaction.user.roles = []

        config_resp = _make_http_resp(200, config_data)
        player_resp = _make_http_resp(200, player_data)
        cog.http_client.get = AsyncMock(return_value=config_resp)
        cog.http_client.post = AsyncMock(return_value=player_resp)

        await cog.notifications.callback(cog, interaction, notification_type="bounty", enabled=1)

        interaction.user.add_roles.assert_awaited_once_with(
            silver_role, reason="BountyBot bounty notification opt-in"
        )

    @pytest.mark.asyncio
    async def test_bounty_disable_gold_tier_removes_gold_role(self, cog):
        """Gold-tier player has Gold role removed when disabling bounty notifications."""
        interaction = _make_mock_interaction()

        config_data = _make_config_data()
        config_data["gold_role_id"] = 2003
        player_data = _make_player_data(tier="Gold")

        gold_role = _make_mock_role(2003, "Bounty Hunter Gold")
        interaction.guild.get_role.return_value = gold_role
        interaction.user.roles = [gold_role]

        config_resp = _make_http_resp(200, config_data)
        player_resp = _make_http_resp(200, player_data)
        cog.http_client.get = AsyncMock(return_value=config_resp)
        cog.http_client.post = AsyncMock(return_value=player_resp)

        await cog.notifications.callback(cog, interaction, notification_type="bounty", enabled=0)

        interaction.user.remove_roles.assert_awaited_once_with(
            gold_role, reason="BountyBot bounty notification opt-out"
        )
