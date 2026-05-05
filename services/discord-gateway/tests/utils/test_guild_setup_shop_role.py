"""Tests for _find_or_create_shop_announcements_role in guild_setup.py.

Covers:
- Role found by name (case-insensitive) → returned directly
- Role not found → create_role is called → success
- Role not found → create_role raises discord.Forbidden → returns None
- Role not found → create_role raises generic Exception → returns None
- ensure_bountybot_infrastructure includes shop_announcements_role_id in result
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
    """Build a minimal mock guild with controllable roles and create_role."""
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
# Tests: _find_or_create_shop_announcements_role
# ---------------------------------------------------------------------------


class TestFindOrCreateShopAnnouncementsRole:
    """Tests for the _find_or_create_shop_announcements_role function."""

    @pytest.mark.asyncio
    async def test_returns_existing_role_when_found_by_name(self):
        """Should return the existing role when 'Shop Announcements' already exists."""
        from utils.guild_setup import _find_or_create_shop_announcements_role

        existing_role = _make_mock_role(9999, "Shop Announcements")
        guild = _make_mock_guild(existing_roles=[existing_role])

        result = await _find_or_create_shop_announcements_role(guild)

        assert result is existing_role
        guild.create_role.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_existing_role_case_insensitive(self):
        """Should match 'shop announcements' case-insensitively."""
        from utils.guild_setup import _find_or_create_shop_announcements_role

        existing_role = _make_mock_role(9999, "SHOP ANNOUNCEMENTS")
        guild = _make_mock_guild(existing_roles=[existing_role])

        result = await _find_or_create_shop_announcements_role(guild)

        assert result is existing_role
        guild.create_role.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_role_when_not_found(self):
        """Should create the role when it doesn't exist."""
        from utils.guild_setup import _find_or_create_shop_announcements_role

        guild = _make_mock_guild(existing_roles=[])
        new_role = _make_mock_role(8888, "Shop Announcements")
        guild.create_role = AsyncMock(return_value=new_role)

        result = await _find_or_create_shop_announcements_role(guild)

        assert result is new_role
        guild.create_role.assert_awaited_once_with(
            name="Shop Announcements",
            mentionable=True,
            hoist=False,
        )

    @pytest.mark.asyncio
    async def test_returns_none_on_forbidden(self):
        """Should return None when discord.Forbidden is raised during create_role."""
        from utils.guild_setup import _find_or_create_shop_announcements_role

        guild = _make_mock_guild(existing_roles=[])
        fake_resp = MagicMock()
        fake_resp.status = 403
        fake_resp.reason = "Forbidden"
        guild.create_role = AsyncMock(side_effect=discord.Forbidden(fake_resp, "Missing Permissions"))

        result = await _find_or_create_shop_announcements_role(guild)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_generic_exception(self):
        """Should return None when a generic exception is raised during create_role."""
        from utils.guild_setup import _find_or_create_shop_announcements_role

        guild = _make_mock_guild(existing_roles=[])
        guild.create_role = AsyncMock(side_effect=RuntimeError("Unexpected error"))

        result = await _find_or_create_shop_announcements_role(guild)

        assert result is None


# ---------------------------------------------------------------------------
# Tests: ensure_bountybot_infrastructure includes shop_announcements_role_id
# ---------------------------------------------------------------------------


class TestEnsureBountyBotInfrastructureShopRole:
    """Verify ensure_bountybot_infrastructure includes shop_announcements_role_id."""

    @pytest.mark.asyncio
    async def test_result_includes_shop_announcements_role_id(self):
        """ensure_bountybot_infrastructure result must include shop_announcements_role_id key."""
        from utils.guild_setup import ensure_bountybot_infrastructure

        guild = _make_mock_guild()
        shop_role = _make_mock_role(3001, "Shop Announcements")

        with (
            patch("utils.guild_setup._find_or_create_role", AsyncMock(return_value=None)),
            patch("utils.guild_setup._find_or_create_tier_roles", AsyncMock(return_value={
                "bronze_role_id": None,
                "silver_role_id": None,
                "gold_role_id": None,
                "platinum_role_id": None,
            })),
            patch("utils.guild_setup._find_or_create_shop_announcements_role", AsyncMock(return_value=shop_role)),
            patch("utils.guild_setup._find_or_create_category", AsyncMock(return_value=None)),
        ):
            result = await ensure_bountybot_infrastructure(guild)

        assert "shop_announcements_role_id" in result
        assert result["shop_announcements_role_id"] == 3001

    @pytest.mark.asyncio
    async def test_result_has_none_shop_role_when_creation_fails(self):
        """Result should have shop_announcements_role_id=None when creation fails."""
        from utils.guild_setup import ensure_bountybot_infrastructure

        guild = _make_mock_guild()

        with (
            patch("utils.guild_setup._find_or_create_role", AsyncMock(return_value=None)),
            patch("utils.guild_setup._find_or_create_tier_roles", AsyncMock(return_value={
                "bronze_role_id": None,
                "silver_role_id": None,
                "gold_role_id": None,
                "platinum_role_id": None,
            })),
            patch("utils.guild_setup._find_or_create_shop_announcements_role", AsyncMock(return_value=None)),
            patch("utils.guild_setup._find_or_create_category", AsyncMock(return_value=None)),
        ):
            result = await ensure_bountybot_infrastructure(guild)

        assert "shop_announcements_role_id" in result
        assert result["shop_announcements_role_id"] is None
