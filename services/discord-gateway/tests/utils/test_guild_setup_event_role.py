"""Tests for _find_or_create_event_announcements_role and ensure_bountybot_infrastructure
including the event_announcements_role_id key (slice 6).

Mirrors test_guild_setup_shop_role.py exactly.
"""

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module-level mock setup
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_guild(guild_id: int = 12345, existing_roles: list | None = None) -> MagicMock:
    guild = MagicMock(spec=discord.Guild)
    guild.id = guild_id
    guild.roles = existing_roles or []
    guild.me = MagicMock()
    guild.default_role = MagicMock()
    guild.create_role = AsyncMock()
    guild.create_category = AsyncMock()
    guild.create_text_channel = AsyncMock()
    guild.categories = []
    return guild


def _make_mock_role(role_id: int, name: str) -> MagicMock:
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    role.name = name
    return role


# ---------------------------------------------------------------------------
# Tests: _find_or_create_event_announcements_role
# ---------------------------------------------------------------------------


class TestFindOrCreateEventAnnouncementsRole:
    @pytest.mark.asyncio
    async def test_returns_existing_role_when_found_by_name(self):
        from utils.guild_setup import _find_or_create_event_announcements_role

        existing_role = _make_mock_role(7777, "Event Announcements")
        guild = _make_mock_guild(existing_roles=[existing_role])

        result = await _find_or_create_event_announcements_role(guild)

        assert result is existing_role
        guild.create_role.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_existing_role_case_insensitive(self):
        from utils.guild_setup import _find_or_create_event_announcements_role

        existing_role = _make_mock_role(7777, "EVENT ANNOUNCEMENTS")
        guild = _make_mock_guild(existing_roles=[existing_role])

        result = await _find_or_create_event_announcements_role(guild)

        assert result is existing_role
        guild.create_role.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_role_when_not_found(self):
        from utils.guild_setup import _find_or_create_event_announcements_role

        guild = _make_mock_guild(existing_roles=[])
        new_role = _make_mock_role(6666, "Event Announcements")
        guild.create_role = AsyncMock(return_value=new_role)

        result = await _find_or_create_event_announcements_role(guild)

        assert result is new_role
        guild.create_role.assert_awaited_once_with(
            name="Event Announcements",
            mentionable=True,
            hoist=False,
        )

    @pytest.mark.asyncio
    async def test_returns_none_on_forbidden(self):
        from utils.guild_setup import _find_or_create_event_announcements_role

        guild = _make_mock_guild(existing_roles=[])
        fake_resp = MagicMock()
        fake_resp.status = 403
        fake_resp.reason = "Forbidden"
        guild.create_role = AsyncMock(side_effect=discord.Forbidden(fake_resp, "Missing Permissions"))

        result = await _find_or_create_event_announcements_role(guild)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_generic_exception(self):
        from utils.guild_setup import _find_or_create_event_announcements_role

        guild = _make_mock_guild(existing_roles=[])
        guild.create_role = AsyncMock(side_effect=RuntimeError("Unexpected error"))

        result = await _find_or_create_event_announcements_role(guild)

        assert result is None


# ---------------------------------------------------------------------------
# Tests: ensure_bountybot_infrastructure includes event_announcements_role_id
# ---------------------------------------------------------------------------


class TestEnsureBountyBotInfrastructureEventRole:
    @pytest.mark.asyncio
    async def test_result_includes_event_announcements_role_id(self):
        from utils.guild_setup import ensure_bountybot_infrastructure

        guild = _make_mock_guild()
        event_role = _make_mock_role(4001, "Event Announcements")

        with (
            patch("utils.guild_setup._find_or_create_role", AsyncMock(return_value=None)),
            patch(
                "utils.guild_setup._find_or_create_tier_roles",
                AsyncMock(
                    return_value={
                        "bronze_role_id": None,
                        "silver_role_id": None,
                        "gold_role_id": None,
                        "platinum_role_id": None,
                    }
                ),
            ),
            patch("utils.guild_setup._find_or_create_shop_announcements_role", AsyncMock(return_value=None)),
            patch("utils.guild_setup._find_or_create_event_announcements_role", AsyncMock(return_value=event_role)),
            patch("utils.guild_setup._find_or_create_category", AsyncMock(return_value=None)),
        ):
            result = await ensure_bountybot_infrastructure(guild)

        assert "event_announcements_role_id" in result
        assert result["event_announcements_role_id"] == 4001

    @pytest.mark.asyncio
    async def test_result_has_none_event_role_when_creation_fails(self):
        from utils.guild_setup import ensure_bountybot_infrastructure

        guild = _make_mock_guild()

        with (
            patch("utils.guild_setup._find_or_create_role", AsyncMock(return_value=None)),
            patch(
                "utils.guild_setup._find_or_create_tier_roles",
                AsyncMock(
                    return_value={
                        "bronze_role_id": None,
                        "silver_role_id": None,
                        "gold_role_id": None,
                        "platinum_role_id": None,
                    }
                ),
            ),
            patch("utils.guild_setup._find_or_create_shop_announcements_role", AsyncMock(return_value=None)),
            patch("utils.guild_setup._find_or_create_event_announcements_role", AsyncMock(return_value=None)),
            patch("utils.guild_setup._find_or_create_category", AsyncMock(return_value=None)),
        ):
            result = await ensure_bountybot_infrastructure(guild)

        assert "event_announcements_role_id" in result
        assert result["event_announcements_role_id"] is None
