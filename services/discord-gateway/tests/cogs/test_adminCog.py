import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

# Import discord_mock_utils for consistent mock patterns
from tests.mocks.discord_mock_utils import DiscordMockUtils

# Create module-level mock utilities
_mock_utils = DiscordMockUtils()

# Setup mock shared.bblogger module
_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")

# Track the module-level logger
_module_logger = None


def _make_mock_logger(*_args, **_kwargs):
    """Return a MagicMock that already has common log-level methods."""
    global _module_logger
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    _module_logger = logger
    return logger


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

# Ensure real discord is used (not a hand-rolled fake from another test module)
for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot for adminCog testing."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    bot.get_member = MagicMock()
    bot.flogger = MagicMock()
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


@pytest.fixture
def mock_admin_cog(mock_bot):
    """Create a mock adminCog instance."""
    # Re-assert this file's own mock so that when adminCog is re-imported below
    # it calls *our* _make_mock_logger (which populates _module_logger).
    # Without this, whichever test file was imported last "owns" the shared
    # sys.modules["shared.bblogger"] entry and the other file's _module_logger
    # stays None.
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.adminCog import AdminCog

    cog = AdminCog(mock_bot)
    return cog


def _create_mock_interaction():
    """Create a properly mocked interaction with all necessary attributes."""
    return DiscordMockUtils.create_mock_interaction()


def _create_mock_user(user_id=111111111, name="TestUser", is_admin=False):
    """Create a properly mocked user with string properties."""
    user = DiscordMockUtils.create_mock_user(user_id=user_id, username=name)
    user.display_avatar = MagicMock()
    user.display_avatar.url = "https://example.com/avatar.jpg"
    user.guild_permissions = MagicMock()
    user.guild_permissions.administrator = is_admin
    return user


class TestAdminCogInitialization:
    """Tests for adminCog initialization."""

    def test_initialization(self, mock_admin_cog):
        """adminCog should initialize properly with bot reference."""
        global _module_logger
        assert mock_admin_cog.bot is not None
        # The cog uses the module-level flogger
        assert _module_logger is not None
        _module_logger.debug.assert_called_with("AdminCog initialized")
        assert mock_admin_cog._valid_tiers == ["Bronze", "Silver", "Gold", "Platinum"]


class TestAdminCheckCommand:
    """Tests for admin_check command."""

    @patch("cogs.adminCog.httpx.AsyncClient")
    def test_admin_check_developer_override(self, mock_httpx_client, mock_admin_cog):
        """admin_check should detect developer override."""
        # Mock interaction
        interaction = _create_mock_interaction()
        user = _create_mock_user()
        interaction.user = user

        # Mock developer override
        with patch.dict(os.environ, {"DEVELOPERS": "111111111"}):
            asyncio.run(mock_admin_cog.admin_check.callback(mock_admin_cog, interaction, user))

        # Verify response
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "**has** bot-admin rights" in call_args
        assert "Developer override" in call_args

    @patch("cogs.adminCog.httpx.AsyncClient")
    def test_admin_check_discord_admin(self, mock_httpx_client, mock_admin_cog):
        """admin_check should detect Discord Administrator permission."""
        # Mock interaction
        interaction = _create_mock_interaction()
        user = _create_mock_user(is_admin=True)
        interaction.user = user
        interaction.guild_id = 987654321

        # Mock guild and member
        guild = MagicMock()
        member = MagicMock()
        member.guild_permissions = MagicMock()
        member.guild_permissions.administrator = True
        # get_member is sync, fetch_member is async
        guild.get_member = MagicMock(return_value=member)
        guild.fetch_member = AsyncMock(return_value=member)
        mock_admin_cog.bot.get_guild = MagicMock(return_value=guild)

        asyncio.run(mock_admin_cog.admin_check.callback(mock_admin_cog, interaction, user))

        # Verify response
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "**has** bot-admin rights" in call_args
        assert "Discord Administrator permission" in call_args

    def test_admin_check_bot_admin_role(self, mock_admin_cog):
        """admin_check should detect Bot Admin role."""
        # Mock interaction
        interaction = _create_mock_interaction()
        user = _create_mock_user(is_admin=False)
        interaction.user = user
        interaction.guild_id = 987654321

        # Mock guild and member with role
        guild = MagicMock()
        role = MagicMock()
        role.id = 222222222
        member = MagicMock()
        member.roles = [role]
        member.guild_permissions = MagicMock()
        member.guild_permissions.administrator = False

        guild.get_member = MagicMock(return_value=member)
        guild.fetch_member = AsyncMock(return_value=member)
        mock_admin_cog.bot.get_guild = MagicMock(return_value=guild)

        # Mock API response with admin role - patch the cog's http_client
        api_response = MagicMock()
        api_response.status_code = 200
        api_response.json.return_value = {"admin_role_id": 222222222}
        mock_admin_cog.http_client.get = AsyncMock(return_value=api_response)

        asyncio.run(mock_admin_cog.admin_check.callback(mock_admin_cog, interaction, user))

        # Verify response
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "**has** bot-admin rights" in call_args
        assert "Assigned Bot Admin role" in call_args

    def test_admin_check_no_admin_rights(self, mock_admin_cog):
        """admin_check should correctly identify users without admin rights."""
        # Mock interaction
        interaction = _create_mock_interaction()
        user = _create_mock_user(is_admin=False)
        interaction.user = user
        interaction.guild_id = 987654321

        # Mock guild without admin role
        guild = MagicMock()
        member = MagicMock()
        member.roles = []
        member.guild_permissions = MagicMock()
        member.guild_permissions.administrator = False

        guild.get_member = MagicMock(return_value=member)
        guild.fetch_member = AsyncMock(return_value=member)
        mock_admin_cog.bot.get_guild = MagicMock(return_value=guild)

        # Mock API response with no admin role - patch the cog's http_client
        api_response = MagicMock()
        api_response.status_code = 200
        api_response.json.return_value = {"admin_role_id": None}
        mock_admin_cog.http_client.get = AsyncMock(return_value=api_response)

        asyncio.run(mock_admin_cog.admin_check.callback(mock_admin_cog, interaction, user))

        # Verify response
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args[0][0]
        assert "**does not have** bot-admin rights" in call_args


def _make_full_channel_ids():
    """Return a full channel_ids dict as returned by the guild_setup (with tier roles)."""
    return {
        "category_id": 100,
        "bronze_bounty_channel_id": 201,
        "silver_bounty_channel_id": 202,
        "gold_bounty_channel_id": 203,
        "platinum_bounty_channel_id": 204,
        "shop_channel_id": 205,
        "hunting_channel_id": 206,
        "discussion_channel_id": 207,
        "image_channel_id": 208,
        "bounty_hunter_role_id": 301,
        "bronze_role_id": 302,
        "silver_role_id": 303,
        "gold_role_id": 304,
        "platinum_role_id": 305,
        # backward-compat aliases still present in output
        "bounty_channel_id": 201,
        "general_channel_id": 207,
    }


def _make_init_response():
    """Return a standard init API response payload."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "message": "Guild initialized successfully",
        "guild_id": 987654321,
        "shops_created": 4,
    }
    return resp


class TestAdminSetupCommand:
    """Tests for admin_setup command."""

    @patch("cogs.adminCog.httpx.AsyncClient")
    def test_admin_setup_with_role(self, mock_httpx_client, mock_admin_cog):
        """admin_setup should work with provided admin role."""
        # Mock interaction
        interaction = _create_mock_interaction()
        interaction.guild = MagicMock()
        interaction.guild.id = 987654321
        interaction.guild.name = "Test Guild"
        interaction.guild.icon = None
        user = _create_mock_user()
        interaction.user = user

        # Mock provided role
        role = MagicMock()
        role.id = 222222222
        type(role).mention = PropertyMock(return_value="<@&222222222>")

        # Mock HTTP client
        mock_client = MagicMock()
        mock_httpx_client.return_value = mock_client

        # Mock API responses
        guild_create_resp = MagicMock()
        guild_create_resp.status_code = 200
        guild_create_resp.json.return_value = {"data": {"id": 987654321}}

        init_resp = _make_init_response()
        mock_client.post.side_effect = [guild_create_resp, init_resp]
        mock_client.aclose = AsyncMock()

        with patch(
            "cogs.adminCog.ensure_bountybot_infrastructure", new=AsyncMock(return_value=_make_full_channel_ids())
        ):
            asyncio.run(mock_admin_cog.admin_setup.callback(mock_admin_cog, interaction, role, 1000))

        # Verify behavior
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()

    def test_admin_setup_payload_contains_all_9_new_keys(self, mock_admin_cog):
        """admin_setup should build init_payload with all channel/role keys (including platinum)."""
        interaction = _create_mock_interaction()
        interaction.guild = MagicMock()
        interaction.guild.id = 987654321
        interaction.guild.name = "Test Guild"
        user = _create_mock_user()
        interaction.user = user

        role = MagicMock()
        role.id = 333333333
        type(role).mention = PropertyMock(return_value="<@&333333333>")

        channel_ids = _make_full_channel_ids()
        captured_payloads = []

        init_resp = _make_init_response()

        async def fake_post(url, **kwargs):
            if "initialize" in url:
                captured_payloads.append(kwargs.get("json", {}))
            resp = MagicMock()
            resp.status_code = 200
            if "initialize" in url:
                resp.json.return_value = init_resp.json()
            else:
                resp.json.return_value = {"data": {"id": 333333333}}
            return resp

        mock_admin_cog.http_client.post = fake_post

        with patch("cogs.adminCog.ensure_bountybot_infrastructure", new=AsyncMock(return_value=channel_ids)):
            asyncio.run(mock_admin_cog.admin_setup.callback(mock_admin_cog, interaction, role, 500))

        assert len(captured_payloads) == 1, "Expected exactly one POST to initialize endpoint"
        payload = captured_payloads[0]

        # New keys MUST be present
        assert "bronze_bounty_channel_id" in payload
        assert "silver_bounty_channel_id" in payload
        assert "gold_bounty_channel_id" in payload
        assert "platinum_bounty_channel_id" in payload
        assert "hunting_channel_id" in payload
        assert "discussion_channel_id" in payload
        assert "image_channel_id" in payload
        assert "bounty_hunter_role_id" in payload
        assert "category_id" in payload
        assert "shop_channel_id" in payload

        # Values should match what ensure_bountybot_infrastructure returned
        assert payload["bronze_bounty_channel_id"] == 201
        assert payload["silver_bounty_channel_id"] == 202
        assert payload["gold_bounty_channel_id"] == 203
        assert payload["platinum_bounty_channel_id"] == 204
        assert payload["shop_channel_id"] == 205
        assert payload["hunting_channel_id"] == 206
        assert payload["discussion_channel_id"] == 207
        assert payload["image_channel_id"] == 208
        assert payload["bounty_hunter_role_id"] == 301
        assert payload["category_id"] == 100

        # Tier role IDs must be present
        assert "bronze_role_id" in payload
        assert "silver_role_id" in payload
        assert "gold_role_id" in payload
        assert "platinum_role_id" in payload
        assert payload["bronze_role_id"] == 302
        assert payload["silver_role_id"] == 303
        assert payload["gold_role_id"] == 304
        assert payload["platinum_role_id"] == 305

        # Old keys must NOT appear (they were removed in SEG-01)
        assert "bounty_channel_id" not in payload
        assert "general_channel_id" not in payload

    def test_admin_setup_embed_shows_all_7_channels(self, mock_admin_cog):
        """admin_setup confirmation embed should mention all 8 channel IDs (including platinum)."""
        interaction = _create_mock_interaction()
        interaction.guild = MagicMock()
        interaction.guild.id = 987654321
        interaction.guild.name = "Test Guild"
        user = _create_mock_user()
        interaction.user = user

        role = MagicMock()
        role.id = 444444444
        type(role).mention = PropertyMock(return_value="<@&444444444>")

        channel_ids = _make_full_channel_ids()
        init_resp = _make_init_response()

        async def fake_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "initialize" in url:
                resp.json.return_value = init_resp.json()
            else:
                resp.json.return_value = {"data": {"id": 444444444}}
            return resp

        mock_admin_cog.http_client.post = fake_post

        with patch("cogs.adminCog.ensure_bountybot_infrastructure", new=AsyncMock(return_value=channel_ids)):
            asyncio.run(mock_admin_cog.admin_setup.callback(mock_admin_cog, interaction, role, 0))

        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs, "Expected an embed in the followup"

        embed = call_kwargs["embed"]
        # Collect all field values from the embed
        field_values = " ".join(str(f.value) for f in embed.fields)
        field_names = " ".join(str(f.name) for f in embed.fields)
        combined = field_values + " " + field_names

        # All 8 channel IDs should appear somewhere in the embed
        assert "<#201>" in combined, "Bronze bounty channel not in embed"
        assert "<#202>" in combined, "Silver bounty channel not in embed"
        assert "<#203>" in combined, "Gold bounty channel not in embed"
        assert "<#204>" in combined, "Platinum bounty channel not in embed"
        assert "<#205>" in combined, "Shop channel not in embed"
        assert "<#206>" in combined, "Hunting channel not in embed"
        assert "<#207>" in combined, "Discussion channel not in embed"
        assert "<#208>" in combined, "Image channel not in embed"

    def test_admin_setup_embed_shows_bounty_hunter_role(self, mock_admin_cog):
        """admin_setup confirmation embed should mention the Bounty Hunter role when created."""
        interaction = _create_mock_interaction()
        interaction.guild = MagicMock()
        interaction.guild.id = 987654321
        interaction.guild.name = "Test Guild"
        user = _create_mock_user()
        interaction.user = user

        role = MagicMock()
        role.id = 555555555
        type(role).mention = PropertyMock(return_value="<@&555555555>")

        channel_ids = _make_full_channel_ids()
        init_resp = _make_init_response()

        async def fake_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "initialize" in url:
                resp.json.return_value = init_resp.json()
            else:
                resp.json.return_value = {"data": {"id": 555555555}}
            return resp

        mock_admin_cog.http_client.post = fake_post

        with patch("cogs.adminCog.ensure_bountybot_infrastructure", new=AsyncMock(return_value=channel_ids)):
            asyncio.run(mock_admin_cog.admin_setup.callback(mock_admin_cog, interaction, role, 0))

        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]

        # Role mention <@&301> should appear in the embed
        field_values = " ".join(str(f.value) for f in embed.fields)
        assert "<@&301>" in field_values, "Bounty Hunter role mention not found in embed fields"

    def test_admin_setup_embed_shows_tier_roles(self, mock_admin_cog):
        """admin_setup confirmation embed should mention all 3 tier role mentions when created."""
        interaction = _create_mock_interaction()
        interaction.guild = MagicMock()
        interaction.guild.id = 987654321
        interaction.guild.name = "Test Guild"
        user = _create_mock_user()
        interaction.user = user

        role = MagicMock()
        role.id = 555555555
        type(role).mention = PropertyMock(return_value="<@&555555555>")

        channel_ids = _make_full_channel_ids()
        init_resp = _make_init_response()

        async def fake_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "initialize" in url:
                resp.json.return_value = init_resp.json()
            else:
                resp.json.return_value = {"data": {"id": 555555555}}
            return resp

        mock_admin_cog.http_client.post = fake_post

        with patch("cogs.adminCog.ensure_bountybot_infrastructure", new=AsyncMock(return_value=channel_ids)):
            asyncio.run(mock_admin_cog.admin_setup.callback(mock_admin_cog, interaction, role, 0))

        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]

        field_values = " ".join(str(f.value) for f in embed.fields)
        # Tier role mentions should appear
        assert "<@&302>" in field_values, "Bronze tier role mention not found in embed fields"
        assert "<@&303>" in field_values, "Silver tier role mention not found in embed fields"
        assert "<@&304>" in field_values, "Gold tier role mention not found in embed fields"
        assert "<@&305>" in field_values, "Platinum tier role mention not found in embed fields"

    def test_admin_setup_partial_none_values(self, mock_admin_cog):
        """admin_setup should work when ensure_bountybot_infrastructure returns some None values."""
        interaction = _create_mock_interaction()
        interaction.guild = MagicMock()
        interaction.guild.id = 987654321
        interaction.guild.name = "Test Guild"
        user = _create_mock_user()
        interaction.user = user

        role = MagicMock()
        role.id = 666666666
        type(role).mention = PropertyMock(return_value="<@&666666666>")

        # Partial failure: only category and bronze channel succeeded; role failed
        partial_channel_ids = {
            "category_id": 100,
            "bronze_bounty_channel_id": 201,
            "silver_bounty_channel_id": None,
            "gold_bounty_channel_id": None,
            "shop_channel_id": None,
            "hunting_channel_id": None,
            "discussion_channel_id": None,
            "image_channel_id": None,
            "bounty_hunter_role_id": None,
        }
        init_resp = _make_init_response()

        async def fake_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "initialize" in url:
                resp.json.return_value = init_resp.json()
            else:
                resp.json.return_value = {"data": {"id": 666666666}}
            return resp

        mock_admin_cog.http_client.post = fake_post

        with patch("cogs.adminCog.ensure_bountybot_infrastructure", new=AsyncMock(return_value=partial_channel_ids)):
            # Should not raise even with None values
            asyncio.run(mock_admin_cog.admin_setup.callback(mock_admin_cog, interaction, role, 0))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()
        # Should NOT show an error embed
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_admin_setup_no_bounty_hunter_role_in_embed_when_none(self, mock_admin_cog):
        """admin_setup embed should NOT show Bounty Hunter role field when role creation failed."""
        interaction = _create_mock_interaction()
        interaction.guild = MagicMock()
        interaction.guild.id = 987654321
        interaction.guild.name = "Test Guild"
        user = _create_mock_user()
        interaction.user = user

        role = MagicMock()
        role.id = 777777777
        type(role).mention = PropertyMock(return_value="<@&777777777>")

        # No bounty_hunter_role_id
        channel_ids_no_role = {
            "category_id": 100,
            "bronze_bounty_channel_id": 201,
            "silver_bounty_channel_id": 202,
            "gold_bounty_channel_id": 203,
            "shop_channel_id": 204,
            "hunting_channel_id": 205,
            "discussion_channel_id": 206,
            "image_channel_id": 207,
            "bounty_hunter_role_id": None,
        }
        init_resp = _make_init_response()

        async def fake_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "initialize" in url:
                resp.json.return_value = init_resp.json()
            else:
                resp.json.return_value = {"data": {"id": 777777777}}
            return resp

        mock_admin_cog.http_client.post = fake_post

        with patch("cogs.adminCog.ensure_bountybot_infrastructure", new=AsyncMock(return_value=channel_ids_no_role)):
            asyncio.run(mock_admin_cog.admin_setup.callback(mock_admin_cog, interaction, role, 0))

        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]

        # Bounty Hunter Role field should NOT appear when role_id is None
        field_names = [str(f.name) for f in embed.fields]
        assert "Bounty Hunter Role" not in field_names, "BH role field should not appear when role_id is None"


def _make_mock_role(role_id, name):
    """Create a minimal mock Discord role."""
    role = MagicMock()
    role.id = role_id
    role.name = name
    role.delete = AsyncMock()
    return role


class TestAdminUninstallCommand:
    """Tests for admin_uninstall command (SEG-03: delete Discord infra before API call)."""

    def _make_guild_config_response(self, include_all=True):
        """Build a standard config API response payload."""
        cfg = {
            "guild_id": 987654321,
            "configured": True,
            "admin_role_configured": True,
            "starting_credits": 0,
            "sale_price_factor": 0.8,
            "xp_thresholds": {"Silver": 100, "Gold": 500, "Platinum": 2000},
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }
        if include_all:
            cfg.update(
                {
                    "category_id": 100,
                    "bronze_bounty_channel_id": 201,
                    "silver_bounty_channel_id": 202,
                    "gold_bounty_channel_id": 203,
                    "platinum_bounty_channel_id": 204,
                    "shop_channel_id": 205,
                    "hunting_channel_id": 206,
                    "discussion_channel_id": 207,
                    "image_channel_id": 208,
                    "bounty_hunter_role_id": 301,
                    "bronze_role_id": 302,
                    "silver_role_id": 303,
                    "gold_role_id": 304,
                    "platinum_role_id": 305,
                }
            )
        return cfg

    def _make_uninstall_response(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "message": "Bot data removed from guild.",
            "removed_counts": {"players": 5, "shops": 4},
            "warning": "All data has been deleted.",
        }
        return resp

    def _make_guild_with_roles(self, role_objects=None, channel_map=None):
        """
        Build a minimal mock guild with roles list + channel lookup.

        role_objects: list of mock role objects (default: empty list)
        channel_map: dict of {channel_id: mock_channel} (default: all return None)
        """
        guild = MagicMock()
        guild.roles = role_objects or []
        if channel_map:
            guild.get_channel = MagicMock(side_effect=lambda cid: channel_map.get(cid))
        else:
            guild.get_channel = MagicMock(return_value=None)
        return guild

    def test_admin_uninstall_requires_confirmation(self, mock_admin_cog):
        """admin_uninstall should show warning when confirm string is missing."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction, None))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_admin_uninstall_fetches_config_before_deleting(self, mock_admin_cog):
        """admin_uninstall should GET the guild config to obtain channel/role IDs."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        cfg_resp = MagicMock()
        cfg_resp.status_code = 200
        cfg_resp.json.return_value = self._make_guild_config_response()

        uninstall_resp = self._make_uninstall_response()

        mock_admin_cog.http_client.get = AsyncMock(return_value=cfg_resp)
        mock_admin_cog.http_client.delete = AsyncMock(return_value=uninstall_resp)

        guild = self._make_guild_with_roles()
        mock_admin_cog.bot.get_guild = MagicMock(return_value=guild)

        asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction, "CONFIRM-DELETE"))

        # Must have called GET config
        mock_admin_cog.http_client.get.assert_called_once()
        get_call_url = mock_admin_cog.http_client.get.call_args[0][0]
        assert "config/guild/987654321" in get_call_url

    def test_admin_uninstall_deletes_all_channels_and_role(self, mock_admin_cog):
        """admin_uninstall should delete all 8 channels, category, and all BountyBot roles."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        cfg_resp = MagicMock()
        cfg_resp.status_code = 200
        cfg_resp.json.return_value = self._make_guild_config_response()

        uninstall_resp = self._make_uninstall_response()
        mock_admin_cog.http_client.get = AsyncMock(return_value=cfg_resp)
        mock_admin_cog.http_client.delete = AsyncMock(return_value=uninstall_resp)

        # Create mock Discord channels objects
        mock_channels = {}
        channel_ids = [201, 202, 203, 204, 205, 206, 207, 208, 100]  # 8 channels + category
        for cid in channel_ids:
            ch = MagicMock()
            ch.delete = AsyncMock()
            mock_channels[cid] = ch

        # Create all 5 BountyBot roles (matched by stored ID)
        mock_bh_role = _make_mock_role(301, "Bounty Hunter")
        mock_bronze_role = _make_mock_role(302, "Bounty Hunter Bronze")
        mock_silver_role = _make_mock_role(303, "Bounty Hunter Silver")
        mock_gold_role = _make_mock_role(304, "Bounty Hunter Gold")
        mock_platinum_role = _make_mock_role(305, "Bounty Hunter Platinum")

        guild = self._make_guild_with_roles(
            role_objects=[mock_bh_role, mock_bronze_role, mock_silver_role, mock_gold_role, mock_platinum_role],
            channel_map=mock_channels,
        )
        mock_admin_cog.bot.get_guild = MagicMock(return_value=guild)

        asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction, "CONFIRM-DELETE"))

        # All 8 channels should have been deleted
        for cid in [201, 202, 203, 204, 205, 206, 207, 208]:
            mock_channels[cid].delete.assert_awaited_once()

        # Category (100) should have been deleted
        mock_channels[100].delete.assert_awaited_once()

        # All 5 BountyBot roles should have been deleted
        mock_bh_role.delete.assert_awaited_once()
        mock_bronze_role.delete.assert_awaited_once()
        mock_silver_role.delete.assert_awaited_once()
        mock_gold_role.delete.assert_awaited_once()
        mock_platinum_role.delete.assert_awaited_once()

        # bot-core uninstall API should have been called
        mock_admin_cog.http_client.delete.assert_called_once()

    def test_admin_uninstall_handles_missing_channels_gracefully(self, mock_admin_cog):
        """admin_uninstall should skip channels that no longer exist (get_channel returns None)."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        cfg_resp = MagicMock()
        cfg_resp.status_code = 200
        cfg_resp.json.return_value = self._make_guild_config_response()

        uninstall_resp = self._make_uninstall_response()
        mock_admin_cog.http_client.get = AsyncMock(return_value=cfg_resp)
        mock_admin_cog.http_client.delete = AsyncMock(return_value=uninstall_resp)

        # No channels, no roles
        guild = self._make_guild_with_roles()
        mock_admin_cog.bot.get_guild = MagicMock(return_value=guild)

        # Should complete without raising
        asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction, "CONFIRM-DELETE"))

        # bot-core uninstall API should still have been called
        mock_admin_cog.http_client.delete.assert_called_once()

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()

    def test_admin_uninstall_calls_botcore_api_even_if_discord_deletions_fail(self, mock_admin_cog):
        """admin_uninstall should call bot-core API even if Discord channel/role deletion raises."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        cfg_resp = MagicMock()
        cfg_resp.status_code = 200
        cfg_resp.json.return_value = self._make_guild_config_response()

        uninstall_resp = self._make_uninstall_response()
        mock_admin_cog.http_client.get = AsyncMock(return_value=cfg_resp)
        mock_admin_cog.http_client.delete = AsyncMock(return_value=uninstall_resp)

        # All Discord deletions raise exceptions
        failing_channel = MagicMock()
        failing_channel.delete = AsyncMock(side_effect=Exception("Forbidden"))

        failing_role = _make_mock_role(301, "Bounty Hunter")
        failing_role.delete = AsyncMock(side_effect=Exception("Forbidden"))

        guild = self._make_guild_with_roles(
            role_objects=[failing_role],
            channel_map={cid: failing_channel for cid in [100, 201, 202, 203, 204, 205, 206, 207, 208]},
        )
        mock_admin_cog.bot.get_guild = MagicMock(return_value=guild)

        # Should NOT raise; should still call bot-core
        asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction, "CONFIRM-DELETE"))

        # bot-core API should still have been called despite Discord errors
        mock_admin_cog.http_client.delete.assert_called_once()

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()

    def test_admin_uninstall_handles_config_fetch_failure(self, mock_admin_cog):
        """admin_uninstall should proceed with bot-core API even if config fetch fails."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        # Config fetch raises an exception
        mock_admin_cog.http_client.get = AsyncMock(side_effect=Exception("Config API down"))

        uninstall_resp = self._make_uninstall_response()
        mock_admin_cog.http_client.delete = AsyncMock(return_value=uninstall_resp)

        guild = self._make_guild_with_roles()
        mock_admin_cog.bot.get_guild = MagicMock(return_value=guild)

        # Should NOT crash; should still call bot-core
        asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction, "CONFIRM-DELETE"))

        # bot-core uninstall API should still have been called
        mock_admin_cog.http_client.delete.assert_called_once()

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()

    def test_admin_uninstall_sends_success_embed(self, mock_admin_cog):
        """admin_uninstall should send a success embed after uninstall."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        cfg_resp = MagicMock()
        cfg_resp.status_code = 200
        cfg_resp.json.return_value = self._make_guild_config_response()

        uninstall_resp = self._make_uninstall_response()
        mock_admin_cog.http_client.get = AsyncMock(return_value=cfg_resp)
        mock_admin_cog.http_client.delete = AsyncMock(return_value=uninstall_resp)

        guild = self._make_guild_with_roles()
        mock_admin_cog.bot.get_guild = MagicMock(return_value=guild)

        asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction, "CONFIRM-DELETE"))

        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "Uninstalled" in embed.title or "Bot Uninstalled" in embed.title

    def test_admin_uninstall_deletes_roles_by_name_when_id_not_stored(self, mock_admin_cog):
        """
        admin_uninstall should delete BountyBot roles matched by name even when
        their IDs are not stored in config (name-scan robustness).
        """
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        # Config with NO role IDs stored (only channel IDs)
        cfg = {
            "guild_id": 987654321,
            "configured": True,
            "admin_role_configured": False,
            "starting_credits": 0,
            "sale_price_factor": 0.8,
            "xp_thresholds": {"Silver": 100, "Gold": 500, "Platinum": 2000},
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
            "category_id": 100,
            "bronze_bounty_channel_id": 201,
            "silver_bounty_channel_id": 202,
            "gold_bounty_channel_id": 203,
            "shop_channel_id": 204,
            "hunting_channel_id": 205,
            "discussion_channel_id": 206,
            "image_channel_id": 207,
            # No role IDs in config!
        }
        cfg_resp = MagicMock()
        cfg_resp.status_code = 200
        cfg_resp.json.return_value = cfg

        uninstall_resp = self._make_uninstall_response()
        mock_admin_cog.http_client.get = AsyncMock(return_value=cfg_resp)
        mock_admin_cog.http_client.delete = AsyncMock(return_value=uninstall_resp)

        # Roles exist in guild by name but not in config
        bh_role = _make_mock_role(999, "Bounty Hunter")
        bronze_role = _make_mock_role(1000, "Bounty Hunter Bronze")
        silver_role = _make_mock_role(1001, "Bounty Hunter Silver")
        gold_role = _make_mock_role(1002, "Bounty Hunter Gold")
        platinum_role = _make_mock_role(1003, "Bounty Hunter Platinum")
        unrelated_role = _make_mock_role(9999, "Admin")

        guild = self._make_guild_with_roles(
            role_objects=[bh_role, bronze_role, silver_role, gold_role, platinum_role, unrelated_role],
        )
        mock_admin_cog.bot.get_guild = MagicMock(return_value=guild)

        asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction, "CONFIRM-DELETE"))

        # All 5 BountyBot roles deleted (matched by name, not ID)
        bh_role.delete.assert_awaited_once()
        bronze_role.delete.assert_awaited_once()
        silver_role.delete.assert_awaited_once()
        gold_role.delete.assert_awaited_once()
        platinum_role.delete.assert_awaited_once()

        # Unrelated roles NOT deleted
        unrelated_role.delete.assert_not_awaited()

    def test_admin_uninstall_deletes_tier_roles_in_addition_to_general_role(self, mock_admin_cog):
        """
        admin_uninstall should delete all 5 BountyBot roles (general + 4 tier)
        when all role IDs are stored in config.
        """
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        cfg_resp = MagicMock()
        cfg_resp.status_code = 200
        cfg_resp.json.return_value = self._make_guild_config_response()

        uninstall_resp = self._make_uninstall_response()
        mock_admin_cog.http_client.get = AsyncMock(return_value=cfg_resp)
        mock_admin_cog.http_client.delete = AsyncMock(return_value=uninstall_resp)

        # Create all 5 BountyBot roles with IDs matching config
        bh_role = _make_mock_role(301, "Bounty Hunter")
        bronze_role = _make_mock_role(302, "Bounty Hunter Bronze")
        silver_role = _make_mock_role(303, "Bounty Hunter Silver")
        gold_role = _make_mock_role(304, "Bounty Hunter Gold")
        platinum_role = _make_mock_role(305, "Bounty Hunter Platinum")
        unrelated_role = _make_mock_role(9999, "Moderator")

        guild = self._make_guild_with_roles(
            role_objects=[bh_role, bronze_role, silver_role, gold_role, platinum_role, unrelated_role],
        )
        mock_admin_cog.bot.get_guild = MagicMock(return_value=guild)

        asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction, "CONFIRM-DELETE"))

        # All 5 BountyBot roles deleted
        bh_role.delete.assert_awaited_once()
        bronze_role.delete.assert_awaited_once()
        silver_role.delete.assert_awaited_once()
        gold_role.delete.assert_awaited_once()
        platinum_role.delete.assert_awaited_once()

        # Unrelated role not deleted
        unrelated_role.delete.assert_not_awaited()

        # bot-core API called
        mock_admin_cog.http_client.delete.assert_called_once()


class TestAdminPlayerCommand:
    """Tests for admin_player command."""

    @patch("cogs.adminCog.httpx.AsyncClient")
    def test_admin_player_view_stats(self, mock_httpx_client, mock_admin_cog):
        """admin_player should show player statistics."""
        # Mock interaction
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        # Mock user
        user = _create_mock_user(user_id=111111111, name="Test User")

        # Mock HTTP client
        mock_client = MagicMock()
        mock_httpx_client.return_value = mock_client

        # Mock API responses
        player_create_resp = MagicMock()
        player_create_resp.status_code = 200
        player_create_resp.json.return_value = {
            "id": 1,
            "discord_id": 111111111,
            "guild_id": 987654321,
            "tier": "Bronze",
            "xp": 100,
            "credits": 500,
            "lifetime_credits": 500,
            "prestige_count": 0,
            "created_at": "2024-01-01T00:00:00",
        }

        stats_resp = MagicMock()
        stats_resp.status_code = 200
        stats_resp.json.return_value = {"total_games": 5, "total_victory": 2, "total_defeat": 3}

        mock_client.post.return_value = player_create_resp
        mock_client.get.return_value = stats_resp
        mock_client.aclose = AsyncMock()

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "view_stats", None, None))

        # Verify behavior
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()

    @patch("cogs.adminCog.httpx.AsyncClient")
    def test_admin_player_set_credits(self, mock_httpx_client, mock_admin_cog):
        """admin_player should set player credits."""
        # Mock interaction
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        # Mock user
        user = _create_mock_user(user_id=111111111, name="Test User")

        # Mock HTTP client
        mock_client = MagicMock()
        mock_httpx_client.return_value = mock_client

        # Mock API responses
        player_create_resp = MagicMock()
        player_create_resp.status_code = 200
        player_create_resp.json.return_value = {"id": 1}

        update_resp = MagicMock()
        update_resp.status_code = 200
        update_resp.json.return_value = {"old_credits": 500, "new_credits": 1000}

        mock_client.post.return_value = player_create_resp
        mock_client.put.return_value = update_resp
        mock_client.aclose = AsyncMock()

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "set_credits", 1000, None))

        # Verify behavior
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()

    def test_admin_player_reset_success(self, mock_admin_cog):
        """admin_player reset should reset player stats and send confirmation embed."""
        # Mock interaction
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        # Mock user
        user = _create_mock_user(user_id=111111111, name="Test User")

        # First POST: player get-or-create
        player_create_resp = MagicMock()
        player_create_resp.status_code = 200
        player_create_resp.json.return_value = {"id": 1}

        # Second POST: reset endpoint
        reset_resp = MagicMock()
        reset_resp.status_code = 200
        reset_resp.json.return_value = {
            "player_id": 1,
            "credits": 1000,
            "xp": 0,
            "tier": "Bronze",
            "bounty_wins": 0,
            "duel_wins": 0,
            "duel_losses": 0,
            "prestige_count": 0,
            "message": "Player 1 stats reset to defaults",
        }

        # Directly mock the http_client on the cog instance
        mock_admin_cog.http_client = MagicMock()
        mock_admin_cog.http_client.post = AsyncMock(side_effect=[player_create_resp, reset_resp])

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "reset", None, None))

        # Verify behavior
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()
        # Verify an embed was sent (not a plain string error)
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_admin_player_reset_player_not_found(self, mock_admin_cog):
        """admin_player reset should handle player-not-found (404) gracefully."""
        # Mock interaction
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        # Mock user
        user = _create_mock_user(user_id=111111111, name="Test User")

        # First POST: player get-or-create succeeds
        player_create_resp = MagicMock()
        player_create_resp.status_code = 200
        player_create_resp.json.return_value = {"id": 9999}

        # Second POST: reset returns 404
        reset_resp = MagicMock()
        reset_resp.status_code = 404

        # Directly mock the http_client on the cog instance
        mock_admin_cog.http_client = MagicMock()
        mock_admin_cog.http_client.post = AsyncMock(side_effect=[player_create_resp, reset_resp])

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "reset", None, None))

        # Verify an error message was sent
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()
        call_args = interaction.followup.send.call_args
        # Should send a plain string error (not embed)
        assert call_args[1].get("ephemeral") is True

    def test_admin_player_reset_api_error(self, mock_admin_cog):
        """admin_player reset should handle API errors gracefully."""
        # Mock interaction
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        # Mock user
        user = _create_mock_user(user_id=111111111, name="Test User")

        # First POST: player get-or-create succeeds
        player_create_resp = MagicMock()
        player_create_resp.status_code = 200
        player_create_resp.json.return_value = {"id": 1}

        # Directly mock the http_client on the cog instance
        mock_admin_cog.http_client = MagicMock()
        mock_admin_cog.http_client.post = AsyncMock(side_effect=[player_create_resp, Exception("Connection error")])

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "reset", None, None))

        # Verify error handling
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()


class TestErrorHandling:
    """Tests for error handling in adminCog."""

    @patch("cogs.adminCog.httpx.AsyncClient")
    def test_admin_setup_api_error(self, mock_httpx_client, mock_admin_cog):
        """admin_setup should handle API errors gracefully."""
        # Mock interaction
        interaction = _create_mock_interaction()
        interaction.guild = MagicMock()
        interaction.guild.id = 987654321
        user = _create_mock_user()
        interaction.user = user

        # Mock required admin_role (required parameter, no longer optional)
        admin_role = MagicMock()
        admin_role.id = 222222222
        type(admin_role).mention = PropertyMock(return_value="<@&222222222>")

        # Mock HTTP client with error
        mock_client = MagicMock()
        mock_httpx_client.return_value = mock_client
        mock_client.post = AsyncMock(side_effect=Exception("API error"))
        mock_client.aclose = AsyncMock()

        asyncio.run(mock_admin_cog.admin_setup.callback(mock_admin_cog, interaction, admin_role, 1000))

        # Verify error handling
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()


class TestCogSetup:
    """Tests for cog setup function."""

    def test_setup_function(self, mock_bot):
        """setup function should add adminCog to bot."""
        from cogs.adminCog import setup

        asyncio.run(setup(mock_bot))

        mock_bot.add_cog.assert_called_once()


# ---------------------------------------------------------------------------
# /admin_config_xp command tests
# ---------------------------------------------------------------------------


class TestAdminConfigXp:
    """Tests for /admin_config_xp command."""

    def _make_interaction(self, is_admin_result=True):
        interaction = _create_mock_interaction()
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = is_admin_result
        return interaction

    def test_view_action_shows_thresholds(self, mock_admin_cog):
        """View action fetches config and displays XP thresholds."""
        interaction = self._make_interaction()

        config_data = {"xp_thresholds": {"Silver": 1000, "Gold": 5000, "Platinum": 15000}}
        config_resp = MagicMock()
        config_resp.raise_for_status = MagicMock()
        config_resp.json.return_value = config_data

        mock_admin_cog.http_client = MagicMock()
        mock_admin_cog.http_client.get = AsyncMock(return_value=config_resp)

        asyncio.run(
            mock_admin_cog.admin_config_xp.callback(
                mock_admin_cog, interaction, action="view", silver=None, gold=None, platinum=None
            )
        )

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_update_action_success(self, mock_admin_cog):
        """Update action with valid ascending thresholds succeeds."""
        interaction = self._make_interaction()

        result_data = {"xp_thresholds": {"Silver": 2000, "Gold": 8000, "Platinum": 20000}}
        update_resp = MagicMock()
        update_resp.raise_for_status = MagicMock()
        update_resp.json.return_value = result_data

        mock_admin_cog.http_client = MagicMock()
        mock_admin_cog.http_client.put = AsyncMock(return_value=update_resp)

        asyncio.run(
            mock_admin_cog.admin_config_xp.callback(
                mock_admin_cog, interaction, action="update", silver=2000, gold=8000, platinum=20000
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "Updated" in embed.title or "✅" in embed.title

    def test_update_missing_threshold_shows_error(self, mock_admin_cog):
        """Update action with missing threshold shows error message."""
        interaction = self._make_interaction()

        mock_admin_cog.http_client = MagicMock()

        asyncio.run(
            mock_admin_cog.admin_config_xp.callback(
                mock_admin_cog, interaction, action="update", silver=1000, gold=None, platinum=15000
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        msg = call_args[0][0]
        assert "required" in msg.lower() or "❌" in msg
        assert call_args[1].get("ephemeral", False)

    def test_update_non_ascending_thresholds_shows_error(self, mock_admin_cog):
        """Update action with non-ascending thresholds shows validation error."""
        interaction = self._make_interaction()

        mock_admin_cog.http_client = MagicMock()

        asyncio.run(
            mock_admin_cog.admin_config_xp.callback(
                mock_admin_cog,
                interaction,
                action="update",
                silver=5000,
                gold=1000,
                platinum=15000,  # silver > gold
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        msg = call_args[0][0]
        assert "ascending" in msg.lower() or "order" in msg.lower() or "❌" in msg
        assert call_args[1].get("ephemeral", False)

    def test_update_api_400_shows_validation_error(self, mock_admin_cog):
        """Update action with 400 from API shows error message from API."""
        import httpx

        interaction = self._make_interaction()

        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.return_value = {"detail": "Thresholds must be positive."}
        http_error = httpx.HTTPStatusError("400 Bad Request", request=MagicMock(), response=error_response)

        mock_admin_cog.http_client = MagicMock()
        mock_admin_cog.http_client.put = AsyncMock(side_effect=http_error)

        asyncio.run(
            mock_admin_cog.admin_config_xp.callback(
                mock_admin_cog, interaction, action="update", silver=1000, gold=5000, platinum=15000
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        msg = call_args[0][0]
        assert "positive" in msg.lower() or "❌" in msg

    def test_view_api_error_shows_generic_error(self, mock_admin_cog):
        """View action with API error shows generic error message."""
        interaction = self._make_interaction()

        mock_admin_cog.http_client = MagicMock()
        mock_admin_cog.http_client.get = AsyncMock(side_effect=RuntimeError("service unavailable"))

        asyncio.run(
            mock_admin_cog.admin_config_xp.callback(
                mock_admin_cog, interaction, action="view", silver=None, gold=None, platinum=None
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        msg = call_args[0][0]
        assert "⚠️" in msg or "error" in msg.lower()


# ===========================================================================
# Gap 4: Discord Embed Rendering Rule Tests — AdminCog
# ===========================================================================


class TestAdminConfigBountyNoTimestampsInBadLocations:
    """Gap 4: Embed rendering rule — <t:...> Discord timestamps must NOT appear
    in the embed footer or author fields for admin config commands.
    """

    def _get_admin_setup_embed(self, mock_admin_cog):
        """Helper: trigger /admin_setup and return the sent embed."""
        interaction = _create_mock_interaction()
        interaction.guild = MagicMock()
        interaction.guild.id = 987654321
        interaction.guild.name = "Test Guild"
        interaction.guild.icon = None
        user = _create_mock_user()
        interaction.user = user

        from unittest.mock import PropertyMock

        role = MagicMock()
        role.id = 222222222
        type(role).mention = PropertyMock(return_value="<@&222222222>")

        channel_ids = {
            "category_id": 100,
            "bronze_bounty_channel_id": 201,
            "silver_bounty_channel_id": 202,
            "gold_bounty_channel_id": 203,
            "shop_channel_id": 204,
            "hunting_channel_id": 205,
            "discussion_channel_id": 206,
            "image_channel_id": 207,
            "bounty_hunter_role_id": 301,
            "bronze_role_id": 302,
            "silver_role_id": 303,
            "gold_role_id": 304,
            "bounty_channel_id": 201,
            "general_channel_id": 206,
        }

        from unittest.mock import patch as _patch

        init_resp = MagicMock()
        init_resp.status_code = 200
        init_resp.json.return_value = {
            "message": "Guild initialized successfully",
            "guild_id": 987654321,
            "shops_created": 4,
        }

        async def fake_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "initialize" in url:
                resp.json.return_value = init_resp.json()
            else:
                resp.json.return_value = {"data": {"id": 222222222}}
            return resp

        mock_admin_cog.http_client.post = fake_post

        with _patch("cogs.adminCog.ensure_bountybot_infrastructure", new=AsyncMock(return_value=channel_ids)):
            asyncio.run(mock_admin_cog.admin_setup.callback(mock_admin_cog, interaction, role, 500))

        if not interaction.followup.send.called:
            return None

        call_kwargs = interaction.followup.send.call_args[1]
        return call_kwargs.get("embed")

    def test_admin_config_bounty_no_timestamps_in_footer(self, mock_admin_cog):
        """Admin setup embed footer must not contain a Discord timestamp (<t:...) pattern.

        Discord renders <t:...> in fields and descriptions but NOT in footer text
        where they appear as raw markup. Verifying the footer is free of raw timestamps
        prevents confusing output in the admin command confirmation.
        """
        embed = self._get_admin_setup_embed(mock_admin_cog)
        if embed is None:
            return  # embed not sent — skip

        footer = embed.footer
        footer_text = ""
        if footer is not None:
            try:
                footer_text = str(footer.text or "")
            except AttributeError:
                footer_text = str(footer)

        assert "<t:" not in footer_text, (
            f"Discord timestamp found in admin embed footer: {footer_text!r}. "
            "Timestamps in footers render as raw text — move them to fields or description."
        )

    def test_admin_config_bounty_no_timestamps_in_author(self, mock_admin_cog):
        """Admin setup embed author field must not contain a Discord timestamp (<t:...) pattern."""
        embed = self._get_admin_setup_embed(mock_admin_cog)
        if embed is None:
            return

        author = embed.author
        author_text = ""
        if author is not None:
            try:
                author_text = str(author.name or "")
            except AttributeError:
                author_text = str(author)

        assert "<t:" not in author_text, (
            f"Discord timestamp found in admin embed author: {author_text!r}. "
            "Timestamps in author fields render as raw text."
        )


if __name__ == "__main__":
    pytest.main([__file__])
