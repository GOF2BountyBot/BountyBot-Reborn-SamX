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

_EXPECTED_KEYS = {
    "category_id",
    "bronze_bounty_channel_id",
    "silver_bounty_channel_id",
    "gold_bounty_channel_id",
    "platinum_bounty_channel_id",
    "shop_channel_id",
    "hunting_channel_id",
    "discussion_channel_id",
    "image_channel_id",
    "bounty_hunter_role_id",
    "bronze_role_id",
    "silver_role_id",
    "gold_role_id",
    "platinum_role_id",
}

_CHANNEL_NAMES = [
    "bronze-bounty-board",
    "silver-bounty-board",
    "gold-bounty-board",
    "platinum-bounties",
    "shop",
    "bounty-hunting",
    "bounty-discussions",
    "bot-images",
]


def _make_role(name="Bounty Hunter", role_id=555):
    """Build a minimal mock discord.Role."""
    role = MagicMock()
    role.name = name
    role.id = role_id
    return role


def _make_tier_role_side_effects(base_id=556):
    """Return a list of 4 distinct role mocks for the 4 tier roles."""
    roles = []
    for i, name in enumerate(
        ["Bounty Hunter Bronze", "Bounty Hunter Silver", "Bounty Hunter Gold", "Bounty Hunter Platinum"]
    ):
        role = MagicMock()
        role.name = name
        role.id = base_id + i
        roles.append(role)
    return roles


def _make_guild(
    categories=None,
    roles=None,
    *,
    forbidden_category=False,
    error_category=False,
    forbidden_role=False,
    error_role=False,
    bot_permissions=None,
):
    """Build a minimal mock discord.Guild.

    bot_permissions: a real discord.Permissions object for guild.me.guild_permissions
    (defaults to Permissions.all() — every existing test that doesn't care about
    permission-filtering sees an unrestricted bot, matching pre-issue-47-follow-up
    behavior). Pass a curated value to test _read_only_overwrites' filtering to
    only permissions the bot actually holds (issue #47 follow-up: Discord 403s a
    channel overwrite that touches ANY permission bit the bot doesn't hold, even
    to deny it — a bare MagicMock here would never catch that, since getattr on a
    MagicMock is always truthy regardless of attribute name).
    """
    import discord

    guild = MagicMock(spec=discord.Guild)
    guild.id = 123456789
    guild.name = "Test Guild"

    guild.default_role = MagicMock()
    me = MagicMock()
    me.id = 999
    me.guild_permissions = bot_permissions if bot_permissions is not None else discord.Permissions.all()
    guild.me = me

    guild.categories = categories or []
    guild.roles = roles or []

    # Role creation mock
    if forbidden_role:

        class FakeResponse403:
            status = 403
            reason = "Forbidden"

        guild.create_role = AsyncMock(side_effect=discord.Forbidden(FakeResponse403(), "Missing Permissions"))
    elif error_role:
        guild.create_role = AsyncMock(side_effect=RuntimeError("role error"))
    else:
        guild.create_role = AsyncMock()

    # Category creation mock
    if forbidden_category:

        class FakeResponse403Category:
            status = 403
            reason = "Forbidden"

        _err = discord.Forbidden(FakeResponse403Category(), "Missing Permissions")
        guild.create_category = AsyncMock(side_effect=_err)
    elif error_category:
        guild.create_category = AsyncMock(side_effect=RuntimeError("oops"))
    else:
        guild.create_category = AsyncMock()

    guild.create_text_channel = AsyncMock()
    return guild


def _make_category(name="BountyBot", channels=None, cat_id=111):
    import discord

    cat = MagicMock(spec=discord.CategoryChannel)
    cat.name = name
    cat.id = cat_id
    cat.channels = channels or []
    return cat


def _make_channel(name, channel_id):
    """Build a minimal mock TextChannel."""
    import discord

    ch = MagicMock(spec=discord.TextChannel)
    ch.name = name
    ch.id = channel_id
    return ch


def _make_7_channel_side_effects():
    """Return a list of 7 distinct channel mocks for create_text_channel side_effect (legacy helper)."""
    ids = [201, 202, 203, 204, 205, 206, 207]
    names = _CHANNEL_NAMES[:7]
    return [_make_channel(n, i) for n, i in zip(names, ids, strict=True)]


def _make_8_channel_side_effects():
    """Return a list of 8 distinct channel mocks for create_text_channel side_effect."""
    ids = [201, 202, 203, 204, 205, 206, 207, 208]
    names = _CHANNEL_NAMES
    return [_make_channel(n, i) for n, i in zip(names, ids, strict=True)]


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestEnsureBountyBotInfrastructure:
    """Tests for ensure_bountybot_infrastructure."""

    @pytest.fixture(autouse=True)
    def _evict_modules_before_each_test(self):
        """Evict discord and utils modules before each test to avoid cross-worker contamination.

        xdist workers share a process between multiple test files. Some test files
        do module-level sys.modules.pop("discord", None) evictions which cause the
        discord module identity to change. This autouse fixture re-evicts and lets
        each test import a consistent discord version alongside its production code.
        """
        to_evict = [k for k in sys.modules if k == "discord" or k.startswith("discord.") or k.startswith("utils.")]
        for k in to_evict:
            sys.modules.pop(k, None)

    # ------------------------------------------------------------------
    # Test 1: Happy path — creates everything from scratch
    # ------------------------------------------------------------------

    def test_creates_role_category_and_all_7_channels_from_scratch(self):
        """
        Happy path: all infrastructure created fresh.

        Verifies:
        - create_role called 6 times (1 general + 4 tier roles + 1 shop announcements)
        - create_category called once
        - create_text_channel called 8 times
        - all required keys have non-None values
        """
        guild = _make_guild()
        new_role = _make_role(role_id=555)
        tier_roles = _make_tier_role_side_effects(base_id=556)
        shop_ann_role = _make_role(name="Shop Announcements", role_id=560)
        # create_role called 6 times: general BH role + 4 tier roles + 1 shop announcements
        guild.create_role = AsyncMock(side_effect=[new_role, *tier_roles, shop_ann_role])

        new_cat = _make_category(cat_id=111)
        guild.create_category.return_value = new_cat

        channels = _make_8_channel_side_effects()
        guild.create_text_channel = AsyncMock(side_effect=channels)

        from utils.guild_setup import ensure_bountybot_infrastructure

        result = asyncio.run(ensure_bountybot_infrastructure(guild))

        # create_role called 6 times: 1 general + 4 tier + 1 shop announcements
        assert guild.create_role.call_count == 6
        guild.create_category.assert_called_once()
        assert guild.create_text_channel.call_count == 8

        # All 14 required keys must be present (dict may also have backward-compat aliases)
        assert _EXPECTED_KEYS.issubset(result.keys())
        for key in _EXPECTED_KEYS:
            assert result[key] is not None, f"Expected {key} to be set, got None"

        assert result["bounty_hunter_role_id"] == 555
        assert result["bronze_role_id"] == 556
        assert result["silver_role_id"] == 557
        assert result["gold_role_id"] == 558
        assert result["platinum_role_id"] == 559
        assert result["shop_announcements_role_id"] == 560
        assert result["category_id"] == 111
        assert result["bronze_bounty_channel_id"] == 201
        assert result["silver_bounty_channel_id"] == 202
        assert result["gold_bounty_channel_id"] == 203
        assert result["platinum_bounty_channel_id"] == 204
        assert result["shop_channel_id"] == 205
        assert result["hunting_channel_id"] == 206
        assert result["discussion_channel_id"] == 207
        assert result["image_channel_id"] == 208

    # ------------------------------------------------------------------
    # Test 2: Reuses existing category (case-insensitive)
    # ------------------------------------------------------------------

    def test_reuses_existing_category_case_insensitive(self):
        """Existing 'bountybot' (lowercase) category is found, no create_category call."""
        existing_cat = _make_category(name="bountybot", cat_id=111)
        guild = _make_guild(categories=[existing_cat])

        new_role = _make_role(role_id=555)
        tier_roles = _make_tier_role_side_effects(base_id=556)
        guild.create_role = AsyncMock(side_effect=[new_role, *tier_roles])

        channels = _make_8_channel_side_effects()
        guild.create_text_channel = AsyncMock(side_effect=channels)

        from utils.guild_setup import ensure_bountybot_infrastructure

        result = asyncio.run(ensure_bountybot_infrastructure(guild))

        guild.create_category.assert_not_called()
        assert result["category_id"] == 111

    # ------------------------------------------------------------------
    # Test 3: Reuses existing channels
    # ------------------------------------------------------------------

    def test_reuses_existing_channels(self):
        """All 8 channels already exist under the category — no create_text_channel calls."""
        existing_channels = [
            _make_channel("bronze-bounty-board", 201),
            _make_channel("silver-bounty-board", 202),
            _make_channel("gold-bounty-board", 203),
            _make_channel("platinum-bounties", 204),
            _make_channel("shop", 205),
            _make_channel("bounty-hunting", 206),
            _make_channel("bounty-discussions", 207),
            _make_channel("bot-images", 208),
        ]
        existing_cat = _make_category(channels=existing_channels, cat_id=111)
        guild = _make_guild(categories=[existing_cat])

        new_role = _make_role(role_id=555)
        tier_roles = _make_tier_role_side_effects(base_id=556)
        guild.create_role = AsyncMock(side_effect=[new_role, *tier_roles])

        from utils.guild_setup import ensure_bountybot_infrastructure

        result = asyncio.run(ensure_bountybot_infrastructure(guild))

        guild.create_category.assert_not_called()
        guild.create_text_channel.assert_not_called()

        assert result["category_id"] == 111
        assert result["bronze_bounty_channel_id"] == 201
        assert result["silver_bounty_channel_id"] == 202
        assert result["gold_bounty_channel_id"] == 203
        assert result["platinum_bounty_channel_id"] == 204
        assert result["shop_channel_id"] == 205
        assert result["hunting_channel_id"] == 206
        assert result["discussion_channel_id"] == 207
        assert result["image_channel_id"] == 208
        assert result["bounty_hunter_role_id"] == 555
        assert result["bronze_role_id"] == 556
        assert result["silver_role_id"] == 557
        assert result["gold_role_id"] == 558
        assert result["platinum_role_id"] == 559

    # ------------------------------------------------------------------
    # Test 4: Reuses existing role (case-insensitive)
    # ------------------------------------------------------------------

    def test_reuses_existing_role_case_insensitive(self):
        """
        Existing 'bounty hunter' (lowercase) role found — no create_role call for it.

        Tier roles are NOT pre-existing in this test, so create_role is called 4 times
        (once per tier role) but not for the general BH role.
        Shop announcements role is NOT pre-existing, so create_role is called once more.
        Total: 4 tier + 1 shop announcements = 5 calls.
        """
        existing_role = _make_role(name="bounty hunter", role_id=555)
        guild = _make_guild(roles=[existing_role])

        tier_roles = _make_tier_role_side_effects(base_id=556)
        shop_ann_role = _make_role(name="Shop Announcements", role_id=560)
        guild.create_role = AsyncMock(side_effect=[*tier_roles, shop_ann_role])

        new_cat = _make_category(cat_id=111)
        guild.create_category.return_value = new_cat

        channels = _make_8_channel_side_effects()
        guild.create_text_channel = AsyncMock(side_effect=channels)

        from utils.guild_setup import ensure_bountybot_infrastructure

        result = asyncio.run(ensure_bountybot_infrastructure(guild))

        # General BH role was found — not created. Tier roles + shop announcements were created (5 calls).
        assert guild.create_role.call_count == 5
        assert result["bounty_hunter_role_id"] == 555
        assert result["bronze_role_id"] == 556
        assert result["silver_role_id"] == 557
        assert result["gold_role_id"] == 558
        assert result["platinum_role_id"] == 559

    # ------------------------------------------------------------------
    # Test 5: Category creation Forbidden → all channel IDs None
    # ------------------------------------------------------------------

    def test_category_forbidden_all_channel_ids_none(self):
        """
        Category creation raises Forbidden → returns early.

        role_id and tier role IDs may be set (roles are attempted before category),
        but all channel IDs and category_id are None.
        """
        guild = _make_guild(forbidden_category=True)

        new_role = _make_role(role_id=555)
        tier_roles = _make_tier_role_side_effects(base_id=556)
        guild.create_role = AsyncMock(side_effect=[new_role, *tier_roles])

        from utils.guild_setup import ensure_bountybot_infrastructure

        result = asyncio.run(ensure_bountybot_infrastructure(guild))

        assert result["category_id"] is None
        assert result["bronze_bounty_channel_id"] is None
        assert result["silver_bounty_channel_id"] is None
        assert result["gold_bounty_channel_id"] is None
        assert result["platinum_bounty_channel_id"] is None
        assert result["shop_channel_id"] is None
        assert result["hunting_channel_id"] is None
        assert result["discussion_channel_id"] is None
        assert result["image_channel_id"] is None
        # Channels should not be attempted
        guild.create_text_channel.assert_not_called()

    # ------------------------------------------------------------------
    # Test 6: Category generic error → all channel IDs None
    # ------------------------------------------------------------------

    def test_category_generic_error_all_channel_ids_none(self):
        """Category creation raises generic error → category_id and all channel IDs None."""
        guild = _make_guild(error_category=True)

        new_role = _make_role(role_id=555)
        tier_roles = _make_tier_role_side_effects(base_id=556)
        guild.create_role = AsyncMock(side_effect=[new_role, *tier_roles])

        from utils.guild_setup import ensure_bountybot_infrastructure

        result = asyncio.run(ensure_bountybot_infrastructure(guild))

        assert result["category_id"] is None
        assert result["bronze_bounty_channel_id"] is None
        assert result["silver_bounty_channel_id"] is None
        assert result["gold_bounty_channel_id"] is None
        assert result["platinum_bounty_channel_id"] is None
        assert result["shop_channel_id"] is None
        assert result["hunting_channel_id"] is None
        assert result["discussion_channel_id"] is None
        assert result["image_channel_id"] is None
        guild.create_text_channel.assert_not_called()

    # ------------------------------------------------------------------
    # Test 7: Individual channel forbidden → only that channel is None
    # ------------------------------------------------------------------

    def test_individual_channel_forbidden_only_that_channel_none(self):
        """First channel (bronze-bounty-board) raises Forbidden; others succeed."""
        import discord

        guild = _make_guild()
        new_role = _make_role(role_id=555)
        tier_roles = _make_tier_role_side_effects(base_id=556)
        guild.create_role = AsyncMock(side_effect=[new_role, *tier_roles])

        new_cat = _make_category(cat_id=111)
        guild.create_category.return_value = new_cat

        class FakeResponse403:
            status = 403
            reason = "Forbidden"

        # First channel call fails; remaining 7 succeed
        success_channels = [_make_channel(n, i) for n, i in zip(_CHANNEL_NAMES[1:], range(202, 209), strict=True)]
        guild.create_text_channel = AsyncMock(
            side_effect=[discord.Forbidden(FakeResponse403(), "Missing Permissions"), *success_channels]
        )

        from utils.guild_setup import ensure_bountybot_infrastructure

        result = asyncio.run(ensure_bountybot_infrastructure(guild))

        assert result["category_id"] == 111
        assert result["bronze_bounty_channel_id"] is None  # Forbidden
        assert result["silver_bounty_channel_id"] == 202
        assert result["gold_bounty_channel_id"] == 203
        assert result["platinum_bounty_channel_id"] == 204
        assert result["shop_channel_id"] == 205
        assert result["hunting_channel_id"] == 206
        assert result["discussion_channel_id"] == 207
        assert result["image_channel_id"] == 208

    # ------------------------------------------------------------------
    # Test 8: Role creation forbidden → role_id None, rest still works
    # ------------------------------------------------------------------

    def test_role_forbidden_role_id_none_rest_works(self):
        """
        Role creation Forbidden → bounty_hunter_role_id and tier role IDs all None,
        but category and channels are still created.
        """
        guild = _make_guild(forbidden_role=True)

        new_cat = _make_category(cat_id=111)
        guild.create_category.return_value = new_cat

        channels = _make_8_channel_side_effects()
        guild.create_text_channel = AsyncMock(side_effect=channels)

        from utils.guild_setup import ensure_bountybot_infrastructure

        result = asyncio.run(ensure_bountybot_infrastructure(guild))

        assert result["bounty_hunter_role_id"] is None
        # All tier role IDs are also None (create_role raises Forbidden for all)
        assert result["bronze_role_id"] is None
        assert result["silver_role_id"] is None
        assert result["gold_role_id"] is None
        assert result["platinum_role_id"] is None
        assert result["category_id"] == 111
        assert result["bronze_bounty_channel_id"] == 201
        assert result["silver_bounty_channel_id"] == 202
        assert result["gold_bounty_channel_id"] == 203
        assert result["platinum_bounty_channel_id"] == 204
        assert result["shop_channel_id"] == 205
        assert result["hunting_channel_id"] == 206
        assert result["discussion_channel_id"] == 207
        assert result["image_channel_id"] == 208
        # Category and all 8 channels still created
        guild.create_category.assert_called_once()
        assert guild.create_text_channel.call_count == 8

    # ------------------------------------------------------------------
    # Test 9: Result dict always has all 9 keys
    # ------------------------------------------------------------------

    def test_result_dict_always_has_all_12_keys(self):
        """Result dict always contains all 14 required keys regardless of failures."""
        # Test with forbidden category (most things None)
        guild = _make_guild(forbidden_category=True)
        new_role = _make_role(role_id=555)
        tier_roles = _make_tier_role_side_effects(base_id=556)
        guild.create_role = AsyncMock(side_effect=[new_role, *tier_roles])

        from utils.guild_setup import ensure_bountybot_infrastructure

        result = asyncio.run(ensure_bountybot_infrastructure(guild))

        # All 14 required keys must be present (dict may also contain backward-compat aliases)
        assert _EXPECTED_KEYS.issubset(result.keys())

    # ------------------------------------------------------------------
    # Test 10: Permission overwrites are passed correctly
    # ------------------------------------------------------------------

    def test_permission_overwrites_passed_to_create_category(self):
        """
        create_category and create_text_channel receive correct overwrites dicts.

        Verifies:
        - create_category receives overwrites with keys: default_role, guild.me, bounty_hunter_role
        - create_text_channel calls receive overwrites with guild.me key AND default_role key
        - @everyone (default_role) IS in every channel's overwrites with view_channel=False, send_messages=False
        """
        import discord

        guild = _make_guild()
        new_role = _make_role(role_id=555)
        tier_roles = _make_tier_role_side_effects(base_id=556)
        guild.create_role = AsyncMock(side_effect=[new_role, *tier_roles])

        new_cat = _make_category(cat_id=111)
        guild.create_category.return_value = new_cat

        channels = _make_8_channel_side_effects()
        guild.create_text_channel = AsyncMock(side_effect=channels)

        from utils.guild_setup import ensure_bountybot_infrastructure

        asyncio.run(ensure_bountybot_infrastructure(guild))

        # Inspect category creation call
        cat_call_kwargs = guild.create_category.call_args
        assert cat_call_kwargs is not None
        overwrites_arg = cat_call_kwargs.kwargs.get("overwrites") or cat_call_kwargs.args[1]
        # Category overwrites must include @everyone, guild.me, and bounty_hunter_role
        assert guild.default_role in overwrites_arg
        assert guild.me in overwrites_arg
        # The @everyone overwrite must DENY view_channel
        everyone_ow = overwrites_arg[guild.default_role]
        assert isinstance(everyone_ow, discord.PermissionOverwrite)
        assert everyone_ow.view_channel is False

        # Inspect channel creation calls — guild.me AND default_role must be present in all overwrites
        channel_calls = guild.create_text_channel.call_args_list
        assert len(channel_calls) == 8
        for ch_call in channel_calls:
            ch_kwargs = ch_call.kwargs
            ch_overwrites = ch_kwargs.get("overwrites", {})
            assert guild.me in ch_overwrites, "Bot (guild.me) must be in every channel's overwrites"
            # @everyone must be explicitly denied in every channel overwrite
            assert guild.default_role in ch_overwrites, (
                "@everyone must be explicitly set in channel overwrites with view_channel=False"
            )
            dr_ow = ch_overwrites[guild.default_role]
            assert isinstance(dr_ow, discord.PermissionOverwrite)
            assert dr_ow.view_channel is False, "@everyone must have view_channel=False in channel overwrites"
            assert dr_ow.send_messages is False, "@everyone must have send_messages=False in channel overwrites"

        # bot-images channel (#7, index 7) must have bounty_hunter view_channel=False
        bot_images_call = channel_calls[7]
        bot_images_ow = bot_images_call.kwargs.get("overwrites", {})
        assert new_role in bot_images_ow
        bh_ow = bot_images_ow[new_role]
        assert bh_ow.view_channel is False, "bot-images @Bounty Hunter must have view_channel=False"

    # ------------------------------------------------------------------
    # Test 10b: fresh setup — read-only board/shop channels deny app commands
    # (issue #47 — regression guard for the actual create_text_channel() calls,
    # not just the overwrite-factory functions in isolation)
    # ------------------------------------------------------------------

    def test_fresh_setup_read_only_channels_deny_app_commands(self):
        """A brand-new guild running /admin_setup for the first time must have
        use_application_commands=False wired into the actual create_text_channel()
        overwrites for all 5 read-only channels (bronze/silver/gold/platinum/shop) —
        not just correct in the _read_only_overwrites() factory in isolation."""
        guild = _make_guild()
        new_role = _make_role(role_id=555)
        tier_roles = _make_tier_role_side_effects(base_id=556)
        guild.create_role = AsyncMock(side_effect=[new_role, *tier_roles])

        new_cat = _make_category(cat_id=111)
        guild.create_category.return_value = new_cat

        channels = _make_8_channel_side_effects()
        guild.create_text_channel = AsyncMock(side_effect=channels)

        from utils.guild_setup import ensure_bountybot_infrastructure

        asyncio.run(ensure_bountybot_infrastructure(guild))

        channel_calls = guild.create_text_channel.call_args_list
        assert len(channel_calls) == 8

        # bronze/silver/gold/platinum/shop are indices 0-4 in _CHANNEL_NAMES.
        read_only_names = _CHANNEL_NAMES[:5]
        for idx, name in enumerate(read_only_names):
            call_args = channel_calls[idx]
            assert call_args.args[0] == name, f"unexpected channel at index {idx}: {call_args.args[0]!r}"
            overwrites = call_args.kwargs.get("overwrites", {})
            assert new_role in overwrites, f"#{name}: @Bounty Hunter must be in overwrites"
            bh_ow = overwrites[new_role]
            assert bh_ow.use_application_commands is False, (
                f"#{name}: @Bounty Hunter use_application_commands must be False on a fresh create_text_channel call"
            )
            assert bh_ow.view_channel is True, f"#{name}: @Bounty Hunter must still be able to view the channel"

    # ------------------------------------------------------------------
    # Test 11: #bounty-hunting channel allows players full interactive access
    # ------------------------------------------------------------------

    def test_hunting_channel_allows_players_full_interactive_access(self):
        """
        #bounty-hunting must allow full player interaction: view, send, history, slash commands.

        @Bounty Hunter overwrite: view_channel=True, send_messages=True,
        read_message_history=True, use_application_commands=True.
        """
        import discord

        guild = _make_guild()
        new_role = _make_role(role_id=555)
        tier_roles = _make_tier_role_side_effects(base_id=556)
        guild.create_role = AsyncMock(side_effect=[new_role, *tier_roles])

        new_cat = _make_category(cat_id=111)
        guild.create_category.return_value = new_cat

        channels = _make_8_channel_side_effects()
        guild.create_text_channel = AsyncMock(side_effect=channels)

        from utils.guild_setup import ensure_bountybot_infrastructure

        asyncio.run(ensure_bountybot_infrastructure(guild))

        channel_calls = guild.create_text_channel.call_args_list
        assert len(channel_calls) == 8

        # bounty-hunting is index 5 in _CHANNEL_NAMES (after platinum-bounties was inserted at index 3)
        hunting_call = channel_calls[5]
        hunting_ow = hunting_call.kwargs.get("overwrites", {})

        assert new_role in hunting_ow, "@Bounty Hunter must be in bounty-hunting overwrites"
        bh_ow = hunting_ow[new_role]
        assert isinstance(bh_ow, discord.PermissionOverwrite)
        assert bh_ow.view_channel is True, "#bounty-hunting @Bounty Hunter must have view_channel=True"
        assert bh_ow.send_messages is True, (
            "#bounty-hunting @Bounty Hunter must have send_messages=True (full interactive access)"
        )
        assert bh_ow.read_message_history is True, "#bounty-hunting @Bounty Hunter must have read_message_history=True"
        assert bh_ow.use_application_commands is True, (
            "#bounty-hunting @Bounty Hunter must have use_application_commands=True"
        )

    # ------------------------------------------------------------------
    # Tests 12–15: Each overwrite factory includes @everyone with view=False, send=False
    # ------------------------------------------------------------------

    def test_read_only_overwrites_includes_default_role_deny(self):
        """
        _read_only_overwrites() must include guild.default_role with
        view_channel=False and send_messages=False.
        """
        import discord
        from utils.guild_setup import _read_only_overwrites

        guild = _make_guild()
        role = _make_role(role_id=555)

        ow = _read_only_overwrites(guild, role)

        assert guild.default_role in ow, "@everyone must be in _read_only_overwrites"
        dr_ow = ow[guild.default_role]
        assert isinstance(dr_ow, discord.PermissionOverwrite)
        assert dr_ow.view_channel is False, "@everyone view_channel must be False"
        assert dr_ow.send_messages is False, "@everyone send_messages must be False"

    def test_hunting_overwrites_includes_default_role_deny(self):
        """
        _hunting_overwrites() must include guild.default_role with
        view_channel=False and send_messages=False.
        """
        import discord
        from utils.guild_setup import _hunting_overwrites

        guild = _make_guild()
        role = _make_role(role_id=555)

        ow = _hunting_overwrites(guild, role)

        assert guild.default_role in ow, "@everyone must be in _hunting_overwrites"
        dr_ow = ow[guild.default_role]
        assert isinstance(dr_ow, discord.PermissionOverwrite)
        assert dr_ow.view_channel is False, "@everyone view_channel must be False"
        assert dr_ow.send_messages is False, "@everyone send_messages must be False"

    def test_discussion_overwrites_includes_default_role_deny(self):
        """
        _discussion_overwrites() must include guild.default_role with
        view_channel=False and send_messages=False.
        """
        import discord
        from utils.guild_setup import _discussion_overwrites

        guild = _make_guild()
        role = _make_role(role_id=555)

        ow = _discussion_overwrites(guild, role)

        assert guild.default_role in ow, "@everyone must be in _discussion_overwrites"
        dr_ow = ow[guild.default_role]
        assert isinstance(dr_ow, discord.PermissionOverwrite)
        assert dr_ow.view_channel is False, "@everyone view_channel must be False"
        assert dr_ow.send_messages is False, "@everyone send_messages must be False"

    def test_image_overwrites_includes_default_role_deny(self):
        """
        _image_overwrites() must include guild.default_role with
        view_channel=False and send_messages=False.
        """
        import discord
        from utils.guild_setup import _image_overwrites

        guild = _make_guild()
        role = _make_role(role_id=555)

        ow = _image_overwrites(guild, role)

        assert guild.default_role in ow, "@everyone must be in _image_overwrites"
        dr_ow = ow[guild.default_role]
        assert isinstance(dr_ow, discord.PermissionOverwrite)
        assert dr_ow.view_channel is False, "@everyone view_channel must be False"
        assert dr_ow.send_messages is False, "@everyone send_messages must be False"

    def test_all_overwrite_factories_include_default_role_deny_without_role(self):
        """
        All overwrite factories must include guild.default_role even when
        bounty_hunter_role is None.
        """
        import discord
        from utils.guild_setup import (
            _discussion_overwrites,
            _hunting_overwrites,
            _image_overwrites,
            _read_only_overwrites,
        )

        guild = _make_guild()

        for factory_fn in [_read_only_overwrites, _hunting_overwrites, _discussion_overwrites, _image_overwrites]:
            ow = factory_fn(guild, None)
            assert guild.default_role in ow, f"@everyone must be in {factory_fn.__name__} (role=None)"
            dr_ow = ow[guild.default_role]
            assert isinstance(dr_ow, discord.PermissionOverwrite)
            assert dr_ow.view_channel is False, f"@everyone view_channel must be False in {factory_fn.__name__}"
            assert dr_ow.send_messages is False, f"@everyone send_messages must be False in {factory_fn.__name__}"

    # ------------------------------------------------------------------
    # Tests for per-channel @Bounty Hunter overwrite values
    # ------------------------------------------------------------------

    def test_read_only_overwrites_bounty_hunter_permissions(self):
        """
        _read_only_overwrites: @Bounty Hunter must have
        view_channel=True, send_messages=False, read_message_history=True,
        use_application_commands=False (board / shop channels are purely read-only).
        """
        import discord
        from utils.guild_setup import _read_only_overwrites

        guild = _make_guild()
        role = _make_role(role_id=555)

        ow = _read_only_overwrites(guild, role)

        assert role in ow, "@Bounty Hunter must be in _read_only_overwrites"
        bh_ow = ow[role]
        assert isinstance(bh_ow, discord.PermissionOverwrite)
        assert bh_ow.view_channel is True, "read-only: view_channel must be True"
        assert bh_ow.send_messages is False, "read-only: send_messages must be False"
        assert bh_ow.read_message_history is True, "read-only: read_message_history must be True"
        assert bh_ow.use_application_commands is False, "read-only: use_application_commands must be False"

    def test_read_only_overwrites_hard_denies_everything_except_view_and_history(self):
        """_read_only_overwrites: EVERY known discord.py permission is denied for
        both @everyone and @Bounty Hunter EXCEPT view_channel and
        read_message_history (issue #47 follow-up). A channel that only denies
        send_messages/use_application_commands is not actually read-only —
        anything else the guild's base role permissions happen to grant
        (reactions, thread creation/posting, etc.) leaks through unless every
        permission is explicitly denied per-channel."""
        from utils.guild_setup import _ALL_PERMISSION_NAMES, _read_only_overwrites

        guild = _make_guild()
        role = _make_role(role_id=555)

        ow = _read_only_overwrites(guild, role)
        everyone_ow = ow[guild.default_role]
        bh_ow = ow[role]

        # @everyone: every permission denied, no exceptions (channel is fully hidden).
        for name in _ALL_PERMISSION_NAMES:
            assert getattr(everyone_ow, name) is False, f"@everyone: {name} must be False"

        # Bounty Hunter: every permission denied EXCEPT view_channel/read_message_history.
        allowed = {"view_channel", "read_message_history"}
        for name in _ALL_PERMISSION_NAMES:
            expected = name in allowed
            assert getattr(bh_ow, name) is expected, f"Bounty Hunter: {name} must be {expected}"

    def test_read_only_overwrites_only_touches_permissions_bot_holds(self):
        """_read_only_overwrites must NOT set allow/deny for any permission the
        bot doesn't itself hold in the guild (issue #47 follow-up regression).

        Discord rejects (403 Missing Permissions) a channel-creation or
        permission-overwrite request that touches ANY bit the acting bot/user
        doesn't hold at the guild level — even to deny it. A bot invited with a
        curated permission set (no administrator/ban_members/manage_guild/etc.)
        got exactly this 403 and silently failed to create the shop/bounty-board
        channels at all, while /bounty-hunting, /bounty-discussions, /bot-images
        (different overwrite factories, never touching those bits) succeeded —
        reproduced here with a guild.me.guild_permissions that lacks them."""
        import discord
        from utils.guild_setup import _read_only_overwrites

        curated = discord.Permissions.none()
        curated.update(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
            manage_roles=True,
            manage_messages=True,
            attach_files=True,
            embed_links=True,
            add_reactions=True,
            use_application_commands=True,
            create_public_threads=True,
            create_private_threads=True,
            send_messages_in_threads=True,
        )
        guild = _make_guild(bot_permissions=curated)
        role = _make_role(role_id=555)

        ow = _read_only_overwrites(guild, role)
        everyone_ow = ow[guild.default_role]
        bh_ow = ow[role]

        # Permissions the bot does NOT hold must be left untouched (None = inherit),
        # never explicitly set to False — that's what triggers the 403.
        for name in ("administrator", "ban_members", "kick_members", "manage_guild", "view_audit_log"):
            assert getattr(everyone_ow, name) is None, f"@everyone: {name} must be untouched (bot doesn't hold it)"
            assert getattr(bh_ow, name) is None, f"Bounty Hunter: {name} must be untouched (bot doesn't hold it)"

        # Permissions the bot DOES hold are still correctly hard-denied.
        for name in ("manage_channels", "add_reactions", "use_application_commands"):
            assert getattr(everyone_ow, name) is False, f"@everyone: {name} must still be denied"
        for name in ("send_messages", "add_reactions", "use_application_commands", "create_public_threads"):
            assert getattr(bh_ow, name) is False, f"Bounty Hunter: {name} must still be denied"

        # manage_roles is the ONE exception: Discord forbids setting the
        # manage_roles ("Manage Permissions") bit in a channel overwrite unless
        # the actor has Administrator, even though the bot holds manage_roles at
        # the guild level (it's what lets the bot create channels WITH overwrites
        # at all). Including it in the deny set is what STILL 403'd the 5
        # read-only channels after the first fix. Since this bot is not admin,
        # manage_roles must be left untouched, not denied.
        assert everyone_ow.manage_roles is None, "@everyone: manage_roles must be untouched (bot is not admin)"
        assert bh_ow.manage_roles is None, "Bounty Hunter: manage_roles must be untouched (bot is not admin)"

        # view_channel/read_message_history stay ALLOW for Bounty Hunter regardless.
        assert bh_ow.view_channel is True
        assert bh_ow.read_message_history is True

    def test_read_only_overwrites_admin_bot_still_denies_manage_roles(self):
        """When the bot DOES have Administrator, denying manage_roles in a channel
        overwrite is permitted by Discord, so it stays in the hard-deny set. This
        pins the other branch of the manage_roles carve-out — the exclusion is
        gated on `not bot_perms.administrator`, not applied unconditionally."""
        import discord
        from utils.guild_setup import _read_only_overwrites

        admin_perms = discord.Permissions.all()  # includes administrator
        assert admin_perms.administrator is True
        guild = _make_guild(bot_permissions=admin_perms)
        role = _make_role(role_id=555)

        ow = _read_only_overwrites(guild, role)
        assert ow[guild.default_role].manage_roles is False, "admin bot: manage_roles is still denied for @everyone"
        assert ow[role].manage_roles is False, "admin bot: manage_roles is still denied for Bounty Hunter"

    def test_read_only_overwrites_bot_can_post_rich_announcements(self):
        """The bot's own overwrite in a read-only channel must ALLOW the
        permissions needed to render a full announcement, or Discord silently
        strips the offending part (HTTP 200, no error): without embed_links the
        embed vanishes, without use_external_emojis custom emojis render as raw
        :name: text, etc. This is why bounty/shop announcements posted only their
        @-mention text_content and lost the embed. Only bits the bot actually
        holds at the guild level may be granted (granting one it lacks 403s the
        create), so this asserts against a curated set that holds all of them."""
        import discord
        from utils.guild_setup import _read_only_overwrites

        curated = discord.Permissions.none()
        curated.update(
            view_channel=True,
            send_messages=True,
            manage_messages=True,
            read_message_history=True,
            embed_links=True,
            attach_files=True,
            use_external_emojis=True,
            use_external_stickers=True,
        )
        guild = _make_guild(bot_permissions=curated)
        role = _make_role(role_id=555)

        bot_ow = _read_only_overwrites(guild, role)[guild.me]
        for perm in (
            "view_channel",
            "send_messages",
            "manage_messages",
            "read_message_history",
            "embed_links",
            "attach_files",
            "use_external_emojis",
            "use_external_stickers",
        ):
            assert getattr(bot_ow, perm) is True, f"bot must be granted {perm} in read-only channel"

    def test_read_only_overwrites_bot_grant_skips_unheld_extras(self):
        """The bot's extra posting grants must be filtered to permissions it
        actually holds — granting a bit the bot lacks at guild level would 403
        the channel creation (same anti-escalation rule as the manage_roles
        carve-out). A bot without use_external_emojis/stickers/attach_files must
        still get its channel; those bits are simply left unset (inherit)."""
        import discord
        from utils.guild_setup import _read_only_overwrites

        curated = discord.Permissions.none()
        curated.update(view_channel=True, send_messages=True, embed_links=True)  # no extras
        guild = _make_guild(bot_permissions=curated)
        role = _make_role(role_id=555)

        bot_ow = _read_only_overwrites(guild, role)[guild.me]
        assert bot_ow.view_channel is True
        assert bot_ow.send_messages is True
        assert bot_ow.embed_links is True  # held → granted
        for unheld in ("use_external_emojis", "use_external_stickers", "attach_files", "manage_messages"):
            assert getattr(bot_ow, unheld) is None, f"{unheld} not held → must be left unset, not force-granted"

    def test_hunting_overwrites_bounty_hunter_permissions(self):
        """
        _hunting_overwrites: @Bounty Hunter must have
        view_channel=True, send_messages=True, read_message_history=True,
        use_application_commands=True (#bounty-hunting is the full player-use channel).
        """
        import discord
        from utils.guild_setup import _hunting_overwrites

        guild = _make_guild()
        role = _make_role(role_id=555)

        ow = _hunting_overwrites(guild, role)

        assert role in ow, "@Bounty Hunter must be in _hunting_overwrites"
        bh_ow = ow[role]
        assert isinstance(bh_ow, discord.PermissionOverwrite)
        assert bh_ow.view_channel is True, "hunting: view_channel must be True"
        assert bh_ow.send_messages is True, "hunting: send_messages must be True"
        assert bh_ow.read_message_history is True, "hunting: read_message_history must be True"
        assert bh_ow.use_application_commands is True, "hunting: use_application_commands must be True"

    def test_discussion_overwrites_bounty_hunter_permissions(self):
        """
        _discussion_overwrites: @Bounty Hunter must have
        view_channel=True, send_messages=True, read_message_history=True,
        use_application_commands=False (#bounty-discussions is chat-only, no slash cmds).
        """
        import discord
        from utils.guild_setup import _discussion_overwrites

        guild = _make_guild()
        role = _make_role(role_id=555)

        ow = _discussion_overwrites(guild, role)

        assert role in ow, "@Bounty Hunter must be in _discussion_overwrites"
        bh_ow = ow[role]
        assert isinstance(bh_ow, discord.PermissionOverwrite)
        assert bh_ow.view_channel is True, "discussion: view_channel must be True"
        assert bh_ow.send_messages is True, "discussion: send_messages must be True"
        assert bh_ow.read_message_history is True, "discussion: read_message_history must be True"
        assert bh_ow.use_application_commands is False, "discussion: use_application_commands must be False"

    def test_image_overwrites_bounty_hunter_permissions(self):
        """
        _image_overwrites: @Bounty Hunter must have
        view_channel=False, send_messages=False (#bot-images is hidden from humans).
        """
        import discord
        from utils.guild_setup import _image_overwrites

        guild = _make_guild()
        role = _make_role(role_id=555)

        ow = _image_overwrites(guild, role)

        assert role in ow, "@Bounty Hunter must be in _image_overwrites"
        bh_ow = ow[role]
        assert isinstance(bh_ow, discord.PermissionOverwrite)
        assert bh_ow.view_channel is False, "bot-images: view_channel must be False"
        assert bh_ow.send_messages is False, "bot-images: send_messages must be False"

    def test_image_overwrites_bounty_hunter_inherits_history_and_app_cmds(self):
        """
        Q5 / Adversarial: @Bounty Hunter in #bot-images must have
        read_message_history=None and use_application_commands=None (inherited).

        Explicitly setting these to False would create a redundant overwrite entry
        that may cause confusing audit log noise. The spec says: 'None (inherit)'.
        """
        import discord
        from utils.guild_setup import _image_overwrites

        guild = _make_guild()
        role = _make_role(role_id=555)

        ow = _image_overwrites(guild, role)

        bh_ow = ow[role]
        assert isinstance(bh_ow, discord.PermissionOverwrite)
        assert bh_ow.read_message_history is None, (
            f"bot-images: read_message_history must be None (inherit) per spec — got {bh_ow.read_message_history!r}"
        )
        assert bh_ow.use_application_commands is None, (
            "bot-images: use_application_commands must be None (inherit) per spec — "
            f"got {bh_ow.use_application_commands!r}"
        )

    def test_full_permission_matrix_for_all_8_channels(self):
        """
        Integration-style test: build all 8 channel overwrites and verify
        the full @Bounty Hunter permission matrix in one shot.

        Channel         | view | send | read_history | use_app_cmds
        ----------------+------+------+--------------+-------------
        bronze-board    | True | False| True         | False
        silver-board    | True | False| True         | False
        gold-board      | True | False| True         | False
        platinum-board  | True | False| True         | False
        shop            | True | False| True         | False
        bounty-hunting  | True | True | True         | True
        bounty-discuss  | True | True | True         | False
        bot-images      | False| False| (None)       | (None)
        """
        import discord

        guild = _make_guild()
        new_role = _make_role(role_id=555)
        tier_roles = _make_tier_role_side_effects(base_id=556)
        guild.create_role = AsyncMock(side_effect=[new_role, *tier_roles])

        new_cat = _make_category(cat_id=111)
        guild.create_category.return_value = new_cat

        channels = _make_8_channel_side_effects()
        guild.create_text_channel = AsyncMock(side_effect=channels)

        from utils.guild_setup import ensure_bountybot_infrastructure

        asyncio.run(ensure_bountybot_infrastructure(guild))

        channel_calls = guild.create_text_channel.call_args_list
        assert len(channel_calls) == 8

        # Expected matrix: (view, send, read_history, use_app_cmds)
        # None means "not set / inherit" — we don't assert those
        expected = [
            # 0: bronze-bounty-board
            (True, False, True, False),
            # 1: silver-bounty-board
            (True, False, True, False),
            # 2: gold-bounty-board
            (True, False, True, False),
            # 3: platinum-bounties
            (True, False, True, False),
            # 4: shop
            (True, False, True, False),
            # 5: bounty-hunting
            (True, True, True, True),
            # 6: bounty-discussions
            (True, True, True, False),
            # 7: bot-images  (view=False, send=False; history/app_cmds left None)
            (False, False, None, None),
        ]

        for idx, (exp_view, exp_send, exp_history, exp_app_cmds) in enumerate(expected):
            ch_name = _CHANNEL_NAMES[idx]
            call = channel_calls[idx]
            ow = call.kwargs.get("overwrites", {})

            assert new_role in ow, f"@Bounty Hunter must be in overwrites for #{ch_name}"
            bh_ow = ow[new_role]
            assert isinstance(bh_ow, discord.PermissionOverwrite)

            assert bh_ow.view_channel is exp_view, (
                f"#{ch_name}: expected view_channel={exp_view}, got {bh_ow.view_channel}"
            )
            assert bh_ow.send_messages is exp_send, (
                f"#{ch_name}: expected send_messages={exp_send}, got {bh_ow.send_messages}"
            )
            if exp_history is not None:
                assert bh_ow.read_message_history is exp_history, (
                    f"#{ch_name}: expected read_message_history={exp_history}, got {bh_ow.read_message_history}"
                )
            if exp_app_cmds is not None:
                assert bh_ow.use_application_commands is exp_app_cmds, (
                    f"#{ch_name}: expected use_application_commands={exp_app_cmds}, "
                    f"got {bh_ow.use_application_commands}"
                )

    # ------------------------------------------------------------------
    # Tier role tests
    # ------------------------------------------------------------------

    def test_tier_role_ids_in_result_dict(self):
        """ensure_bountybot_infrastructure returns bronze/silver/gold/platinum_role_id in result."""
        guild = _make_guild()
        new_role = _make_role(role_id=555)
        tier_roles = _make_tier_role_side_effects(base_id=600)
        guild.create_role = AsyncMock(side_effect=[new_role, *tier_roles])

        new_cat = _make_category(cat_id=111)
        guild.create_category.return_value = new_cat

        channels = _make_8_channel_side_effects()
        guild.create_text_channel = AsyncMock(side_effect=channels)

        from utils.guild_setup import ensure_bountybot_infrastructure

        result = asyncio.run(ensure_bountybot_infrastructure(guild))

        assert "bronze_role_id" in result
        assert "silver_role_id" in result
        assert "gold_role_id" in result
        assert "platinum_role_id" in result
        assert result["bronze_role_id"] == 600
        assert result["silver_role_id"] == 601
        assert result["gold_role_id"] == 602
        assert result["platinum_role_id"] == 603

    def test_tier_roles_created_when_not_existing(self):
        """
        When tier roles don't exist in guild.roles, create_role is called for each.

        Also verifies the roles are created with mentionable=True.
        Now includes Shop Announcements role, so create_role is called 6 times total:
        1 general + 4 tier + 1 shop announcements.
        """
        guild = _make_guild()
        new_role = _make_role(role_id=555)
        tier_roles = _make_tier_role_side_effects(base_id=600)
        shop_ann_role = _make_role(name="Shop Announcements", role_id=604)
        guild.create_role = AsyncMock(side_effect=[new_role, *tier_roles, shop_ann_role])

        new_cat = _make_category(cat_id=111)
        guild.create_category.return_value = new_cat

        channels = _make_8_channel_side_effects()
        guild.create_text_channel = AsyncMock(side_effect=channels)

        from utils.guild_setup import ensure_bountybot_infrastructure

        asyncio.run(ensure_bountybot_infrastructure(guild))

        # Should have called create_role 6 times: general + 4 tier + 1 shop announcements
        assert guild.create_role.call_count == 6

        # Each tier role should be created with mentionable=True and hoist=False
        # Calls: [0]=general BH, [1:5]=tier roles, [5]=shop announcements
        tier_calls = guild.create_role.call_args_list[1:5]
        tier_names = {"Bounty Hunter Bronze", "Bounty Hunter Silver", "Bounty Hunter Gold", "Bounty Hunter Platinum"}
        created_names = {call.kwargs.get("name") for call in tier_calls}
        assert created_names == tier_names, f"Expected tier role names {tier_names}, got {created_names}"

        for call in tier_calls:
            assert call.kwargs.get("mentionable") is True, "Tier roles must be mentionable=True"
            assert call.kwargs.get("hoist") is False, "Tier roles must be hoist=False"

    def test_tier_roles_found_and_reused_case_insensitive(self):
        """
        When all tier roles already exist (by case-insensitive name), create_role
        is not called for them; existing IDs are returned.

        Shop Announcements role also pre-exists, so create_role is never called.
        """
        general_role = _make_role(name="Bounty Hunter", role_id=555)
        bronze_role = _make_role(name="bounty hunter bronze", role_id=600)
        silver_role = _make_role(name="BOUNTY HUNTER SILVER", role_id=601)
        gold_role = _make_role(name="Bounty Hunter Gold", role_id=602)
        platinum_role = _make_role(name="bounty hunter platinum", role_id=603)
        shop_ann_role = _make_role(name="Shop Announcements", role_id=604)

        guild = _make_guild(roles=[general_role, bronze_role, silver_role, gold_role, platinum_role, shop_ann_role])

        new_cat = _make_category(cat_id=111)
        guild.create_category.return_value = new_cat

        channels = _make_8_channel_side_effects()
        guild.create_text_channel = AsyncMock(side_effect=channels)

        from utils.guild_setup import ensure_bountybot_infrastructure

        result = asyncio.run(ensure_bountybot_infrastructure(guild))

        # No roles should have been created (all found by name)
        guild.create_role.assert_not_called()

        assert result["bounty_hunter_role_id"] == 555
        assert result["bronze_role_id"] == 600
        assert result["silver_role_id"] == 601
        assert result["gold_role_id"] == 602
        assert result["platinum_role_id"] == 603
        assert result["shop_announcements_role_id"] == 604

    def test_tier_roles_no_channel_overwrites(self):
        """
        Tier roles must NOT appear in any channel permission overwrites.

        Tier roles are purely for @-mentions; channel visibility is controlled
        by the general '@Bounty Hunter' role only.
        """
        guild = _make_guild()
        new_role = _make_role(role_id=555)
        tier_roles = _make_tier_role_side_effects(base_id=600)
        guild.create_role = AsyncMock(side_effect=[new_role, *tier_roles])

        new_cat = _make_category(cat_id=111)
        guild.create_category.return_value = new_cat

        channels = _make_8_channel_side_effects()
        guild.create_text_channel = AsyncMock(side_effect=channels)

        from utils.guild_setup import ensure_bountybot_infrastructure

        asyncio.run(ensure_bountybot_infrastructure(guild))

        # Check that no channel overwrite uses a tier role object
        tier_role_objects = set(tier_roles)
        channel_calls = guild.create_text_channel.call_args_list
        for ch_call in channel_calls:
            ch_overwrites = ch_call.kwargs.get("overwrites", {})
            for tier_role in tier_role_objects:
                assert tier_role not in ch_overwrites, (
                    f"Tier role '{tier_role.name}' must NOT appear in channel permission overwrites"
                )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
