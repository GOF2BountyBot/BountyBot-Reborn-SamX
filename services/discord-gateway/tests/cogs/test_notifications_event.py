"""Tests for /notifications type:event slice 6 additions, _sync_player_notification_roles
event-role block, and /unregister event_announcements_role_id inclusion.
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
        "bounty_notifications_enabled": True,
        "shop_notifications_enabled": True,
        "event_notifications_enabled": True,
    }


def _make_config_data(
    bounty_hunter_role_id: int | None = 1001,
    bronze_role_id: int | None = 2001,
    shop_announcements_role_id: int | None = 3001,
    event_announcements_role_id: int | None = 4001,
) -> dict:
    return {
        "guild_id": 999,
        "bounty_hunter_role_id": bounty_hunter_role_id,
        "bronze_role_id": bronze_role_id,
        "silver_role_id": None,
        "gold_role_id": None,
        "platinum_role_id": None,
        "shop_announcements_role_id": shop_announcements_role_id,
        "event_announcements_role_id": event_announcements_role_id,
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


@pytest.fixture(scope="module")
def mock_bot():
    bot = MagicMock()
    bot.loop = MagicMock()
    return bot


@pytest.fixture(scope="module")
def cog(mock_bot):
    from cogs.playerCog import PlayerCog

    c = PlayerCog(mock_bot)
    c.http_client = MagicMock()
    c.http_client.aclose = AsyncMock()
    return c


# ---------------------------------------------------------------------------
# /notifications type:event
# ---------------------------------------------------------------------------


class TestNotificationsEventEnable:
    @pytest.mark.asyncio
    async def test_event_enable_adds_role(self, cog):
        """/notifications event:1 adds the Event Announcements role and persists."""
        interaction = _make_mock_interaction()
        config_data = _make_config_data(event_announcements_role_id=4001)
        player_data = _make_player_data()
        player_data["id"] = 10

        event_role = _make_mock_role(4001, "Event Announcements")
        interaction.guild.get_role.return_value = event_role
        interaction.user.roles = []

        cog.http_client.get = AsyncMock(return_value=_make_http_resp(200, config_data))
        cog.http_client.post = AsyncMock(return_value=_make_http_resp(200, player_data))
        cog.http_client.put = AsyncMock(return_value=_make_http_resp(200, {}))

        await cog.notifications.callback(cog, interaction, notification_type="event", enabled=1)

        interaction.user.add_roles.assert_awaited_once_with(event_role, reason="BountyBot event notification opt-in")
        call_kw = interaction.followup.send.call_args[1]
        assert "embed" in call_kw
        assert "enabled" in call_kw["embed"].title.lower()

        put_call = cog.http_client.put.call_args
        put_json = put_call[1].get("json", {})
        assert put_json.get("notification_type") == "event"
        assert put_json.get("enabled") is True

    @pytest.mark.asyncio
    async def test_event_disable_removes_role(self, cog):
        """/notifications event:0 removes the Event Announcements role."""
        interaction = _make_mock_interaction()
        config_data = _make_config_data(event_announcements_role_id=4001)
        player_data = _make_player_data()
        player_data["id"] = 11

        event_role = _make_mock_role(4001, "Event Announcements")
        interaction.guild.get_role.return_value = event_role
        interaction.user.roles = [event_role]

        cog.http_client.get = AsyncMock(return_value=_make_http_resp(200, config_data))
        cog.http_client.post = AsyncMock(return_value=_make_http_resp(200, player_data))
        cog.http_client.put = AsyncMock(return_value=_make_http_resp(200, {}))

        await cog.notifications.callback(cog, interaction, notification_type="event", enabled=0)

        interaction.user.remove_roles.assert_awaited_once_with(
            event_role, reason="BountyBot event notification opt-out"
        )
        call_kw = interaction.followup.send.call_args[1]
        assert "embed" in call_kw
        assert "disabled" in call_kw["embed"].title.lower()

        put_call = cog.http_client.put.call_args
        put_json = put_call[1].get("json", {})
        assert put_json.get("notification_type") == "event"
        assert put_json.get("enabled") is False

    @pytest.mark.asyncio
    async def test_event_no_role_id_returns_error(self, cog):
        """/notifications event:1 with no event_announcements_role_id sends error msg."""
        interaction = _make_mock_interaction()
        config_data = _make_config_data(event_announcements_role_id=None)

        cog.http_client.get = AsyncMock(return_value=_make_http_resp(200, config_data))

        await cog.notifications.callback(cog, interaction, notification_type="event", enabled=1)

        interaction.followup.send.assert_awaited_once()
        call_kw = interaction.followup.send.call_args[1]
        content = call_kw.get("content") or str(interaction.followup.send.call_args)
        assert "❌" in content or "not configured" in content.lower()


# ---------------------------------------------------------------------------
# _sync_player_notification_roles: event role block
# ---------------------------------------------------------------------------


class TestSyncPlayerNotificationRolesEvent:
    @pytest.mark.asyncio
    async def test_adds_event_role_when_flag_true_and_role_missing(self, cog):
        """Event role added when event_notifications_enabled=True and member lacks role."""
        guild = MagicMock(spec=discord.Guild)
        guild.id = 999
        member = MagicMock(spec=discord.Member)
        member.id = 111
        member.add_roles = AsyncMock()
        member.remove_roles = AsyncMock()

        event_role = _make_mock_role(4001, "Event Announcements")
        member.roles = []

        guild.get_role = MagicMock(return_value=event_role)

        config_data = _make_config_data(
            bounty_hunter_role_id=None,
            bronze_role_id=None,
            shop_announcements_role_id=None,
            event_announcements_role_id=4001,
        )
        player_data = _make_player_data()
        player_data["bounty_notifications_enabled"] = False  # skip bounty/tier roles
        player_data["shop_notifications_enabled"] = False  # skip shop role
        player_data["event_notifications_enabled"] = True

        cog.http_client.get = AsyncMock(return_value=_make_http_resp(200, config_data))

        await cog._sync_player_notification_roles(guild, member, 999, player_data)

        member.add_roles.assert_awaited_once()
        added = member.add_roles.call_args[0]
        assert event_role in added

    @pytest.mark.asyncio
    async def test_removes_event_role_when_flag_false_and_role_present(self, cog):
        """Event role removed when event_notifications_enabled=False and member has role."""
        guild = MagicMock(spec=discord.Guild)
        guild.id = 999
        member = MagicMock(spec=discord.Member)
        member.id = 111
        member.add_roles = AsyncMock()
        member.remove_roles = AsyncMock()

        event_role = _make_mock_role(4001, "Event Announcements")
        member.roles = [event_role]

        guild.get_role = MagicMock(return_value=event_role)

        config_data = _make_config_data(
            bounty_hunter_role_id=None,
            bronze_role_id=None,
            shop_announcements_role_id=None,
            event_announcements_role_id=4001,
        )
        player_data = _make_player_data()
        player_data["bounty_notifications_enabled"] = False
        player_data["shop_notifications_enabled"] = False
        player_data["event_notifications_enabled"] = False

        cog.http_client.get = AsyncMock(return_value=_make_http_resp(200, config_data))

        await cog._sync_player_notification_roles(guild, member, 999, player_data)

        member.remove_roles.assert_awaited_once()
        removed = member.remove_roles.call_args[0]
        assert event_role in removed


# ---------------------------------------------------------------------------
# /unregister includes event_announcements_role_id
# ---------------------------------------------------------------------------


class TestUnregisterEventRole:
    @pytest.mark.asyncio
    async def test_unregister_removes_event_role(self, cog):
        """/unregister strips the Event Announcements role along with others."""
        interaction = _make_mock_interaction()
        bh_role = _make_mock_role(1001, "Bounty Hunter")
        event_role = _make_mock_role(4001, "Event Announcements")

        interaction.user.roles = [bh_role, event_role]
        interaction.user.remove_roles = AsyncMock()

        config_data = _make_config_data(
            bounty_hunter_role_id=1001,
            bronze_role_id=None,
            shop_announcements_role_id=None,
            event_announcements_role_id=4001,
        )

        def _get_role(rid):
            return {1001: bh_role, 4001: event_role}.get(rid)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)

        cog.http_client.get = AsyncMock(return_value=_make_http_resp(200, config_data))

        await cog.unregister.callback(cog, interaction)

        interaction.user.remove_roles.assert_awaited_once()
        removed = set(interaction.user.remove_roles.call_args[0])
        assert event_role in removed, f"event_role not in removed: {removed}"
