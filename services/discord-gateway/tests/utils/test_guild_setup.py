"""
Tests for utils/guild_setup.py — ensure_bountybot_infrastructure()
"""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── Bootstrap: mock shared.bblogger before any imports ───────────────────────

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_args, **_kwargs):
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_guild(categories=None, *, forbidden_category=False, error_category=False):
    """Build a minimal mock discord.Guild."""
    import discord

    guild = MagicMock(spec=discord.Guild)
    guild.id = 123456789
    guild.name = "Test Guild"

    guild.default_role = MagicMock()
    me = MagicMock()
    me.id = 999
    guild.me = me

    guild.categories = categories or []

    if forbidden_category:

        class FakeResponse:
            status = 403
            reason = "Forbidden"

        guild.create_category = AsyncMock(side_effect=discord.Forbidden(FakeResponse(), "Missing Permissions"))
    elif error_category:
        guild.create_category = AsyncMock(side_effect=RuntimeError("oops"))
    else:
        guild.create_category = AsyncMock()

    guild.create_text_channel = AsyncMock()
    return guild


def _make_category(name="BountyBot", channels=None):
    import discord

    cat = MagicMock(spec=discord.CategoryChannel)
    cat.name = name
    cat.id = 111
    cat.channels = channels or []
    return cat


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestEnsureBountyBotInfrastructure:
    """Tests for ensure_bountybot_infrastructure."""

    def test_creates_category_and_channels_when_none_exist(self):
        """Should create the category and all 3 channels when starting from scratch."""
        guild = _make_guild()
        new_cat = _make_category()
        new_cat.id = 111
        new_cat.channels = []
        guild.create_category.return_value = new_cat

        # create_text_channel returns a channel with a distinct ID each call
        bb_ch = MagicMock()
        bb_ch.id = 222
        shop_ch = MagicMock()
        shop_ch.id = 333
        gen_ch = MagicMock()
        gen_ch.id = 444
        guild.create_text_channel = AsyncMock(side_effect=[bb_ch, shop_ch, gen_ch])

        from utils.guild_setup import ensure_bountybot_infrastructure

        result = asyncio.run(ensure_bountybot_infrastructure(guild))

        guild.create_category.assert_called_once()
        assert guild.create_text_channel.call_count == 3
        assert result["category_id"] == 111
        assert result["bounty_channel_id"] == 222
        assert result["shop_channel_id"] == 333
        assert result["general_channel_id"] == 444

    def test_reuses_existing_category_case_insensitive(self):
        """Should find an existing category using case-insensitive name match."""
        existing_cat = _make_category(name="bountybot")  # lowercase — matches "BountyBot"
        existing_cat.id = 111
        existing_cat.channels = []

        guild = _make_guild(categories=[existing_cat])
        bb_ch = MagicMock()
        bb_ch.id = 222
        shop_ch = MagicMock()
        shop_ch.id = 333
        gen_ch = MagicMock()
        gen_ch.id = 444
        guild.create_text_channel = AsyncMock(side_effect=[bb_ch, shop_ch, gen_ch])

        from utils.guild_setup import ensure_bountybot_infrastructure

        result = asyncio.run(ensure_bountybot_infrastructure(guild))

        guild.create_category.assert_not_called()
        assert result["category_id"] == 111

    def test_reuses_existing_channels(self):
        """Should reuse channels that already exist under the category."""
        import discord

        bb_ch = MagicMock(spec=discord.TextChannel)
        bb_ch.name = "bounty-board"
        bb_ch.id = 222

        shop_ch = MagicMock(spec=discord.TextChannel)
        shop_ch.name = "shop"
        shop_ch.id = 333

        gen_ch = MagicMock(spec=discord.TextChannel)
        gen_ch.name = "general"
        gen_ch.id = 444

        existing_cat = _make_category(channels=[bb_ch, shop_ch, gen_ch])
        existing_cat.id = 111
        guild = _make_guild(categories=[existing_cat])

        from utils.guild_setup import ensure_bountybot_infrastructure

        result = asyncio.run(ensure_bountybot_infrastructure(guild))

        guild.create_category.assert_not_called()
        guild.create_text_channel.assert_not_called()
        assert result == {
            "category_id": 111,
            "bounty_channel_id": 222,
            "shop_channel_id": 333,
            "general_channel_id": 444,
        }

    def test_category_forbidden_returns_all_none(self):
        """When category creation is Forbidden, all IDs should be None."""
        guild = _make_guild(forbidden_category=True)

        from utils.guild_setup import ensure_bountybot_infrastructure

        result = asyncio.run(ensure_bountybot_infrastructure(guild))

        assert result == {
            "category_id": None,
            "bounty_channel_id": None,
            "shop_channel_id": None,
            "general_channel_id": None,
        }
        # Channels should not be attempted
        guild.create_text_channel.assert_not_called()

    def test_category_generic_error_returns_all_none(self):
        """When category creation raises a generic error, all IDs should be None."""
        guild = _make_guild(error_category=True)

        from utils.guild_setup import ensure_bountybot_infrastructure

        result = asyncio.run(ensure_bountybot_infrastructure(guild))

        assert result == {
            "category_id": None,
            "bounty_channel_id": None,
            "shop_channel_id": None,
            "general_channel_id": None,
        }

    def test_channel_forbidden_sets_id_to_none(self):
        """When a channel creation raises Forbidden, that channel ID is None."""
        import discord

        new_cat = _make_category()
        new_cat.id = 111
        new_cat.channels = []
        guild = _make_guild()
        guild.create_category.return_value = new_cat

        # First channel (bounty-board) raises Forbidden; others succeed
        class FakeResponse:
            status = 403
            reason = "Forbidden"

        shop_ch = MagicMock()
        shop_ch.id = 333
        gen_ch = MagicMock()
        gen_ch.id = 444
        guild.create_text_channel = AsyncMock(
            side_effect=[
                discord.Forbidden(FakeResponse(), "Missing Permissions"),
                shop_ch,
                gen_ch,
            ]
        )

        from utils.guild_setup import ensure_bountybot_infrastructure

        result = asyncio.run(ensure_bountybot_infrastructure(guild))

        assert result["category_id"] == 111
        assert result["bounty_channel_id"] is None  # Forbidden
        assert result["shop_channel_id"] == 333
        assert result["general_channel_id"] == 444

    def test_channel_generic_error_sets_id_to_none(self):
        """When a channel creation raises a generic error, that channel ID is None."""
        new_cat = _make_category()
        new_cat.id = 111
        new_cat.channels = []
        guild = _make_guild()
        guild.create_category.return_value = new_cat

        shop_ch = MagicMock()
        shop_ch.id = 333
        gen_ch = MagicMock()
        gen_ch.id = 444
        guild.create_text_channel = AsyncMock(side_effect=[RuntimeError("network error"), shop_ch, gen_ch])

        from utils.guild_setup import ensure_bountybot_infrastructure

        result = asyncio.run(ensure_bountybot_infrastructure(guild))

        assert result["category_id"] == 111
        assert result["bounty_channel_id"] is None  # generic error
        assert result["shop_channel_id"] == 333
        assert result["general_channel_id"] == 444

    def test_returns_dict_with_all_keys(self):
        """Result dict always contains all four expected keys."""
        guild = _make_guild(forbidden_category=True)

        from utils.guild_setup import ensure_bountybot_infrastructure

        result = asyncio.run(ensure_bountybot_infrastructure(guild))

        assert set(result.keys()) == {
            "category_id",
            "bounty_channel_id",
            "shop_channel_id",
            "general_channel_id",
        }


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
