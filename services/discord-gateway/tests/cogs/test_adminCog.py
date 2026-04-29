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
# Track all loggers created (keyed by name) to support lookup after multi-logger inits.
_all_loggers: dict[str, MagicMock] = {}


def _make_mock_logger(*_args, **_kwargs):
    """Return a MagicMock that already has common log-level methods."""
    global _module_logger
    # Use the logger name if provided to allow targeted lookup.
    name = _args[0] if _args else None
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    _module_logger = logger
    if name:
        _all_loggers[name] = logger
    return logger


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

# Ensure real discord is used (not a hand-rolled fake from another test module)
for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


def _close_coro(coro):
    """Close a coroutine to prevent 'never awaited' RuntimeWarning."""
    coro.close()
    return MagicMock()


@pytest.fixture
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
        assert mock_admin_cog.bot is not None
        # AdminCog uses module-level flogger named "discord-gateway-AdminCog".
        # After __init__ multiple AutocompleteCache loggers are also created,
        # so we look up the AdminCog logger specifically.
        admin_logger = _all_loggers.get("discord-gateway-AdminCog")
        assert admin_logger is not None, "AdminCog logger not found in _all_loggers"
        admin_logger.debug.assert_called_with("AdminCog initialized")
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
        """admin_check should detect Bot Admin role for the TARGET user.

        B.25 Fix A: The INVOKER uses the default admin interaction (Discord admin),
        while the TARGET user has a Bot Admin role but not Discord admin.
        """
        # Mock interaction — invoker is admin (default MagicMock truthy administrator)
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        # TARGET user has a Bot Admin role but not Discord administrator
        user = _create_mock_user(is_admin=False)

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
        """admin_check should correctly identify users without admin rights.

        B.25 Fix A: The INVOKER uses the default admin interaction (Discord admin),
        while the TARGET user has neither Discord admin nor Bot Admin role.
        """
        # Mock interaction — invoker is admin (default MagicMock truthy administrator)
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        # TARGET user has no admin permissions at all
        user = _create_mock_user(is_admin=False)

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
        """admin_setup should build init_payload with all channel/role keys (including platinum).

        B.25 Fix A: Invoker uses default admin interaction (truthy administrator).
        """
        interaction = _create_mock_interaction()
        interaction.guild = MagicMock()
        interaction.guild.id = 987654321
        interaction.guild.name = "Test Guild"
        # Do NOT override interaction.user — keep the default admin mock (truthy guild_permissions.administrator)

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
        # B.25 Fix A: Use default interaction (truthy administrator), no user override needed

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
        # B.25 Fix A: Use default interaction (truthy administrator), no user override needed

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
        # B.25 Fix A: Use default interaction (truthy administrator), no user override needed

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
        # B.25 Fix A: Use default interaction (truthy administrator), no user override needed

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
        # B.25 Fix A: Use default interaction (truthy administrator), no user override needed

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


# ===========================================================================
# Bug A.15: admin_config "view" and admin_player "view_stats" — timestamps
#            must appear in embed fields (not footer) so Discord renders them
# ===========================================================================


class TestAdminConfigViewTimestamps:
    """Bug A.15 — <t:...> timestamps must appear in embed fields, not footer text."""

    def _make_config_data(self):
        return {
            "guild_id": 987654321,
            "configured": True,
            "admin_role_configured": True,
            "starting_credits": 500,
            "sale_price_factor": 0.5,
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-06-01T12:00:00",
            "xp_thresholds": {"Silver": 1000, "Gold": 5000, "Platinum": 20000},
        }

    def test_admin_config_view_timestamps_in_field(self, mock_admin_cog):
        """admin_config view: embed must have a field containing <t: timestamp markers."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        cfg_resp = MagicMock()
        cfg_resp.raise_for_status = MagicMock()
        cfg_resp.json.return_value = self._make_config_data()
        mock_admin_cog.http_client.get = AsyncMock(return_value=cfg_resp)

        asyncio.run(mock_admin_cog.admin_config.callback(mock_admin_cog, interaction, "view", None, None))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs.get("embed")
        assert embed is not None, "Expected an embed to be sent"

        # At least one field value must contain a Discord timestamp
        field_values = [f.value for f in embed.fields]
        has_ts_in_fields = any("<t:" in (v or "") for v in field_values)
        assert has_ts_in_fields, f"Expected <t: timestamp in at least one embed field. Field values: {field_values}"

    def test_admin_config_view_footer_has_no_timestamps(self, mock_admin_cog):
        """admin_config view: embed footer must NOT contain a <t: Discord timestamp."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        cfg_resp = MagicMock()
        cfg_resp.raise_for_status = MagicMock()
        cfg_resp.json.return_value = self._make_config_data()
        mock_admin_cog.http_client.get = AsyncMock(return_value=cfg_resp)

        asyncio.run(mock_admin_cog.admin_config.callback(mock_admin_cog, interaction, "view", None, None))

        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs.get("embed")
        assert embed is not None

        footer = embed.footer
        footer_text = ""
        if footer is not None:
            try:
                footer_text = str(footer.text or "")
            except AttributeError:
                footer_text = str(footer)

        assert "<t:" not in footer_text, (
            f"Discord timestamp found in embed footer: {footer_text!r}. "
            "Timestamps in footers render as raw text — move them to fields."
        )


class TestAdminPlayerViewStatsTimestamps:
    """Bug A.15 — admin_player view_stats created_at timestamp must be in a field."""

    def test_admin_player_view_stats_created_at_in_field(self, mock_admin_cog):
        """admin_player view_stats: embed must have a field containing the created_at timestamp."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        user = _create_mock_user(user_id=111111111, name="Test User")

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = {
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
        stats_resp.raise_for_status = MagicMock()
        stats_resp.json.return_value = {"total_games": 5, "total_victory": 2, "total_defeat": 3}

        mock_admin_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_admin_cog.http_client.get = AsyncMock(return_value=stats_resp)

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "view_stats", None, None))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs.get("embed")
        assert embed is not None, "Expected an embed to be sent"

        # The created_at timestamp should appear in an embed field
        field_values = [f.value for f in embed.fields]
        has_ts_in_fields = any("<t:" in (v or "") for v in field_values)
        assert has_ts_in_fields, (
            f"Expected <t: timestamp in at least one embed field for created_at. Field values: {field_values}"
        )

    def test_admin_player_view_stats_footer_has_no_timestamps(self, mock_admin_cog):
        """admin_player view_stats: embed footer must NOT contain a <t: Discord timestamp."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        user = _create_mock_user(user_id=111111111, name="Test User")

        player_resp = MagicMock()
        player_resp.raise_for_status = MagicMock()
        player_resp.json.return_value = {
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
        stats_resp.raise_for_status = MagicMock()
        stats_resp.json.return_value = {"total_games": 5, "total_victory": 2, "total_defeat": 3}

        mock_admin_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_admin_cog.http_client.get = AsyncMock(return_value=stats_resp)

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "view_stats", None, None))

        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs.get("embed")
        assert embed is not None

        footer = embed.footer
        footer_text = ""
        if footer is not None:
            try:
                footer_text = str(footer.text or "")
            except AttributeError:
                footer_text = str(footer)

        assert "<t:" not in footer_text, (
            f"Discord timestamp found in embed footer: {footer_text!r}. "
            "Timestamps in footers render as raw text — move them to fields."
        )


# ===========================================================================
# New tests for render-config autocomplete + Platinum tier choices
# ===========================================================================


class TestPreloadRenderSettings:
    """Tests for _preload_render_settings method.

    B.33 followup (Finding 1): tests use respx to assert exact URL + HTTP method,
    confirming adminCog calls GET /api/v1/config/render on blender-service
    (route registered in blender-service/src/routers/config.py:19).
    """

    _DEFAULT_BLENDER_URL = "http://blender-service:8001/api/v1"
    _RENDER_CONFIG_URL = "http://blender-service:8001/api/v1/config/render"

    def _with_real_client(self, cog):
        """Replace cog.http_client with a real httpx.AsyncClient for respx interception."""
        import httpx

        cog.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        return cog

    def test_render_settings_initialized_empty(self, mock_admin_cog):
        """_render_settings should start empty on init."""
        assert mock_admin_cog._render_settings == []

    def test_preload_creates_task_on_init(self, mock_bot):
        """__init__ should schedule both _preload_render_settings and _preload_static_catalogs as tasks."""
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        _evict_discord_modules()
        from cogs.adminCog import AdminCog

        cog = AdminCog(mock_bot)
        _ = cog  # use the cog
        # Two create_task calls: one for _preload_render_settings, one for _preload_static_catalogs
        assert mock_bot.loop.create_task.call_count == 2

    def test_preload_success_populates_settings(self, mock_admin_cog):
        """_preload_render_settings calls GET /api/v1/config/render and populates _render_settings."""
        import httpx
        import respx

        render_config_data = {
            "max_res_x": 3840,
            "max_res_y": 2160,
            "min_res_x": 352,
            "min_res_y": 240,
            "max_samples": 128,
            "min_samples": 1,
            "default_res_x": 1920,
            "default_res_y": 1080,
            "default_samples": 64,
            "max_concurrent_renders": 2,
            "job_ttl_hours": 1,
        }
        self._with_real_client(mock_admin_cog)
        mock_admin_cog.bot.wait_until_ready = AsyncMock()

        env_without_blender = {k: v for k, v in os.environ.items() if k != "BLENDER_API_BASE_URL"}
        with patch.dict(os.environ, env_without_blender, clear=True):
            with respx.mock(assert_all_called=True) as mock_router:
                mock_router.get(self._RENDER_CONFIG_URL).mock(
                    return_value=httpx.Response(200, json=render_config_data)
                )
                asyncio.run(mock_admin_cog._preload_render_settings())

        assert mock_admin_cog._render_settings == list(render_config_data.keys())
        assert len(mock_admin_cog._render_settings) == 11

    def test_preload_retries_on_failure_then_succeeds(self, mock_admin_cog):
        """_preload_render_settings retries up to 3 times; succeeds on 2nd attempt."""
        import httpx
        import respx

        render_config_data = {"max_res_x": 3840, "default_samples": 64}
        attempt_count = {"n": 0}

        async def flaky_handler(request):
            attempt_count["n"] += 1
            if attempt_count["n"] == 1:
                raise httpx.ConnectError("connection refused", request=request)
            return httpx.Response(200, json=render_config_data)

        self._with_real_client(mock_admin_cog)
        mock_admin_cog.bot.wait_until_ready = AsyncMock()

        env_without_blender = {k: v for k, v in os.environ.items() if k != "BLENDER_API_BASE_URL"}
        with patch.dict(os.environ, env_without_blender, clear=True):
            with respx.mock(assert_all_called=False) as mock_router:
                mock_router.get(self._RENDER_CONFIG_URL).mock(side_effect=flaky_handler)
                with patch("cogs.adminCog.asyncio.sleep", new=AsyncMock()) as mock_sleep:
                    asyncio.run(mock_admin_cog._preload_render_settings())

        # Should have slept once (after first failure) with 5s delay
        mock_sleep.assert_awaited_once_with(5)
        # Settings populated from successful 2nd attempt
        assert mock_admin_cog._render_settings == list(render_config_data.keys())

    def test_preload_all_3_attempts_fail_leaves_empty(self, mock_admin_cog):
        """_preload_render_settings leaves _render_settings empty after all 3 failures."""
        import httpx
        import respx

        self._with_real_client(mock_admin_cog)
        mock_admin_cog.bot.wait_until_ready = AsyncMock()

        env_without_blender = {k: v for k, v in os.environ.items() if k != "BLENDER_API_BASE_URL"}
        with patch.dict(os.environ, env_without_blender, clear=True):
            with respx.mock(assert_all_called=False) as mock_router:
                mock_router.get(self._RENDER_CONFIG_URL).mock(
                    return_value=httpx.Response(503, json={"detail": "Service Unavailable"})
                )
                with patch("cogs.adminCog.asyncio.sleep", new=AsyncMock()):
                    asyncio.run(mock_admin_cog._preload_render_settings())

        # Settings should remain empty after all attempts fail
        assert mock_admin_cog._render_settings == []

    def test_preload_uses_blender_api_base_url_env(self, mock_admin_cog):
        """_preload_render_settings uses BLENDER_API_BASE_URL env var for the request URL."""
        import httpx
        import respx

        render_config_data = {"max_res_x": 3840}
        custom_url = "http://custom-blender:9001/api/v1"
        custom_config_url = f"{custom_url}/config/render"

        self._with_real_client(mock_admin_cog)
        mock_admin_cog.bot.wait_until_ready = AsyncMock()

        with patch.dict(os.environ, {"BLENDER_API_BASE_URL": custom_url}):
            with respx.mock(assert_all_called=True) as mock_router:
                # respx intercepts the custom URL — only matches if cog uses env var
                mock_router.get(custom_config_url).mock(
                    return_value=httpx.Response(200, json=render_config_data)
                )
                asyncio.run(mock_admin_cog._preload_render_settings())

        assert mock_admin_cog._render_settings == list(render_config_data.keys())

    def test_preload_uses_default_blender_url_when_env_missing(self, mock_admin_cog):
        """_preload_render_settings falls back to default blender URL when env var absent."""
        import httpx
        import respx

        render_config_data = {"default_samples": 64}
        self._with_real_client(mock_admin_cog)
        mock_admin_cog.bot.wait_until_ready = AsyncMock()

        env_without_blender = {k: v for k, v in os.environ.items() if k != "BLENDER_API_BASE_URL"}
        with patch.dict(os.environ, env_without_blender, clear=True):
            with respx.mock(assert_all_called=True) as mock_router:
                # respx only matches if cog uses the default URL
                mock_router.get(self._RENDER_CONFIG_URL).mock(
                    return_value=httpx.Response(200, json=render_config_data)
                )
                asyncio.run(mock_admin_cog._preload_render_settings())

        assert mock_admin_cog._render_settings == list(render_config_data.keys())


class TestRenderSettingAutocomplete:
    """Tests for render_setting_autocomplete method."""

    def test_autocomplete_returns_all_when_current_empty(self, mock_admin_cog):
        """Autocomplete should return all settings when current is empty."""
        mock_admin_cog._render_settings = ["max_res_x", "max_res_y", "min_res_x", "default_samples"]
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_admin_cog.render_setting_autocomplete(interaction, ""))

        assert len(result) == 4
        assert all(hasattr(c, "name") and hasattr(c, "value") for c in result)

    def test_autocomplete_filters_by_current(self, mock_admin_cog):
        """Autocomplete should filter settings by current input (case-insensitive)."""
        mock_admin_cog._render_settings = ["max_res_x", "max_res_y", "min_res_x", "default_samples"]
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_admin_cog.render_setting_autocomplete(interaction, "max"))

        names = [c.name for c in result]
        assert "max_res_x" in names
        assert "max_res_y" in names
        assert "min_res_x" not in names
        assert "default_samples" not in names

    def test_autocomplete_case_insensitive(self, mock_admin_cog):
        """Autocomplete should filter case-insensitively."""
        mock_admin_cog._render_settings = ["max_res_x", "default_samples"]
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_admin_cog.render_setting_autocomplete(interaction, "MAX"))

        names = [c.name for c in result]
        assert "max_res_x" in names

    def test_autocomplete_returns_empty_when_no_match(self, mock_admin_cog):
        """Autocomplete returns empty list when no settings match."""
        mock_admin_cog._render_settings = ["max_res_x", "min_res_y"]
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_admin_cog.render_setting_autocomplete(interaction, "xyz_nonexistent"))

        assert result == []

    def test_autocomplete_returns_empty_when_render_settings_not_preloaded(self, mock_admin_cog):
        """Autocomplete returns empty list when _render_settings is not yet populated."""
        mock_admin_cog._render_settings = []
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_admin_cog.render_setting_autocomplete(interaction, "max"))

        assert result == []

    def test_autocomplete_caps_results_at_25(self, mock_admin_cog):
        """Autocomplete should cap results at 25 (Discord limit)."""
        mock_admin_cog._render_settings = [f"setting_{i}" for i in range(30)]
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_admin_cog.render_setting_autocomplete(interaction, "setting"))

        assert len(result) == 25

    def test_autocomplete_choice_name_and_value_match(self, mock_admin_cog):
        """Autocomplete choices should have matching name and value (both are the setting key)."""
        mock_admin_cog._render_settings = ["max_res_x", "job_ttl_hours"]
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_admin_cog.render_setting_autocomplete(interaction, ""))

        for choice in result:
            assert choice.name == choice.value


class TestPlatinumTierChoices:
    """Tests that Platinum is included in tier choices for clear/spawn bounty commands."""

    def test_admin_clear_bounties_includes_platinum_choice(self, mock_admin_cog):
        """admin_clear_bounties @app_commands.choices should include Platinum."""
        cmd = mock_admin_cog.admin_clear_bounties
        # Find the choices for 'tier' from the command's extras (app_commands stores these)
        # We verify at the command level by inspecting decorated choices
        choices_param = None
        for param in cmd._params.values():
            if hasattr(param, "choices") and param.choices:
                choices_param = param.choices
                break

        assert choices_param is not None, "No choices found on admin_clear_bounties command parameters"
        choice_values = [c.value for c in choices_param]
        assert "platinum" in choice_values, f"Platinum not in clear_bounties tier choices: {choice_values}"

    def test_admin_spawn_bounty_includes_platinum_choice(self, mock_admin_cog):
        """admin_spawn_bounty @app_commands.choices should include Platinum."""
        cmd = mock_admin_cog.admin_spawn_bounty
        choices_param = None
        for param in cmd._params.values():
            if hasattr(param, "choices") and param.choices:
                choices_param = param.choices
                break

        assert choices_param is not None, "No choices found on admin_spawn_bounty command parameters"
        choice_values = [c.value for c in choices_param]
        assert "platinum" in choice_values, f"Platinum not in spawn_bounty tier choices: {choice_values}"

    def test_admin_clear_bounties_all_4_tiers_present(self, mock_admin_cog):
        """admin_clear_bounties should have Bronze/Silver/Gold/Platinum choices."""
        cmd = mock_admin_cog.admin_clear_bounties
        choices_param = None
        for param in cmd._params.values():
            if hasattr(param, "choices") and param.choices:
                choices_param = param.choices
                break

        assert choices_param is not None
        choice_values = {c.value for c in choices_param}
        assert {"bronze", "silver", "gold", "platinum"} == choice_values

    def test_admin_spawn_bounty_all_4_tiers_present(self, mock_admin_cog):
        """admin_spawn_bounty should have Bronze/Silver/Gold/Platinum choices."""
        cmd = mock_admin_cog.admin_spawn_bounty
        choices_param = None
        for param in cmd._params.values():
            if hasattr(param, "choices") and param.choices:
                choices_param = param.choices
                break

        assert choices_param is not None
        choice_values = {c.value for c in choices_param}
        assert {"bronze", "silver", "gold", "platinum"} == choice_values

    def test_admin_clear_bounties_platinum_tier_accepted(self, mock_admin_cog):
        """admin_clear_bounties should accept platinum tier value and call API."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        clear_resp = MagicMock()
        clear_resp.raise_for_status = MagicMock()
        clear_resp.json.return_value = {"cleared_count": 2, "announcements_deleted": 2}
        mock_admin_cog.http_client.delete = AsyncMock(return_value=clear_resp)

        asyncio.run(mock_admin_cog.admin_clear_bounties.callback(mock_admin_cog, interaction, "CONFIRM", "platinum"))

        # Should have called the API with platinum tier
        call_kwargs = mock_admin_cog.http_client.delete.call_args[1]
        assert call_kwargs["params"].get("tier") == "platinum"
        interaction.followup.send.assert_awaited_once()

    def test_admin_spawn_bounty_platinum_tier_accepted(self, mock_admin_cog):
        """admin_spawn_bounty should accept platinum tier value and call API."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        spawn_resp = MagicMock()
        spawn_resp.raise_for_status = MagicMock()
        spawn_resp.json.return_value = {"spawned": [], "skipped_tiers": [], "errors": []}
        mock_admin_cog.http_client.post = AsyncMock(return_value=spawn_resp)

        asyncio.run(mock_admin_cog.admin_spawn_bounty.callback(mock_admin_cog, interaction, "platinum"))

        # Should have called the API with platinum tier
        call_kwargs = mock_admin_cog.http_client.post.call_args[1]
        assert call_kwargs["params"].get("tier") == "platinum"
        interaction.followup.send.assert_awaited_once()


class TestAdminCooldownReset:
    """Tests for /admin_cooldown_reset command."""

    def test_cooldown_reset_success(self, mock_admin_cog):
        """Successful cooldown reset returns success message."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 12345
        user = _create_mock_user(user_id=99999, name="TestPlayer")

        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"status": "success", "message": "Cooldown reset for player 7"}
        mock_admin_cog.http_client.put = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_cooldown_reset.callback(mock_admin_cog, interaction, user))

        interaction.response.defer.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True
        sent_msg = interaction.followup.send.call_args[0][0]
        assert "✅" in sent_msg

    def test_cooldown_reset_calls_correct_endpoint(self, mock_admin_cog):
        """admin_cooldown_reset calls PUT /players/{guild_id}/{user_id}/cooldown/reset."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 55555
        user = _create_mock_user(user_id=77777)

        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"status": "success", "message": "Cooldown reset for player 7"}
        mock_admin_cog.http_client.put = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_cooldown_reset.callback(mock_admin_cog, interaction, user))

        call_url = mock_admin_cog.http_client.put.call_args[0][0]
        assert "/players/55555/77777/cooldown/reset" in call_url

    def test_cooldown_reset_player_not_found_returns_error(self, mock_admin_cog):
        """admin_cooldown_reset shows 'Player not found' when API returns 404."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 12345
        user = _create_mock_user(user_id=99999)

        resp = MagicMock()
        resp.status_code = 404
        resp.raise_for_status = MagicMock()
        mock_admin_cog.http_client.put = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_cooldown_reset.callback(mock_admin_cog, interaction, user))

        interaction.followup.send.assert_awaited_once()
        sent_msg = interaction.followup.send.call_args[0][0]
        assert "❌" in sent_msg
        assert "not found" in sent_msg.lower()

    def test_cooldown_reset_api_error_shows_error_message(self, mock_admin_cog):
        """admin_cooldown_reset shows error when API fails (non-404 HTTP error)."""
        import httpx

        interaction = _create_mock_interaction()
        interaction.guild_id = 12345
        user = _create_mock_user(user_id=99999)

        err_resp = MagicMock()
        err_resp.status_code = 500
        mock_admin_cog.http_client.put = AsyncMock(
            side_effect=httpx.HTTPStatusError("Server error", request=MagicMock(), response=err_resp)
        )

        asyncio.run(mock_admin_cog.admin_cooldown_reset.callback(mock_admin_cog, interaction, user))

        interaction.followup.send.assert_awaited_once()
        # B.31b: helper now sends a sanitized embed instead of a raw URL string.
        call_kwargs = interaction.followup.send.call_args.kwargs
        assert call_kwargs.get("ephemeral", False)
        embed = call_kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")
        assert "http://" not in (embed.description or "")

    def test_cooldown_reset_generic_exception_shows_warning(self, mock_admin_cog):
        """admin_cooldown_reset shows warning message on unexpected exception."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 12345
        user = _create_mock_user(user_id=99999)

        mock_admin_cog.http_client.put = AsyncMock(side_effect=Exception("Unexpected!"))

        asyncio.run(mock_admin_cog.admin_cooldown_reset.callback(mock_admin_cog, interaction, user))

        interaction.followup.send.assert_awaited_once()
        sent_msg = interaction.followup.send.call_args[0][0]
        assert "⚠️" in sent_msg


class TestPlayerShipAutocomplete:
    """Tests for player_ship_autocomplete — /admin_remove_ship ship_name autocomplete.

    This autocomplete was updated (B.11) to filter by the target player's owned ships
    when interaction.namespace.user is available.  It falls back to all ships from game
    data when the user param has not yet been filled or when player resolution fails.
    """

    def _make_interaction(self, target_user=None, guild_id=12345):
        """Return a mock interaction with optional namespace.user pre-set."""
        interaction = _create_mock_interaction()
        interaction.guild_id = guild_id
        # interaction.namespace holds partially-filled command params during autocomplete
        interaction.namespace = MagicMock()
        interaction.namespace.user = target_user
        return interaction

    def _make_user(self, user_id=111111):
        user = MagicMock()
        user.id = user_id
        return user

    def test_filters_to_player_ships_when_user_param_available(self, mock_admin_cog):
        """When namespace.user is set and player resolves, only that player's ships are shown."""
        target_user = self._make_user(user_id=42)
        interaction = self._make_interaction(target_user=target_user, guild_id=99)

        # resolve_player_id → POST /players/ returns player_id=7
        player_resp = MagicMock()
        player_resp.status_code = 200
        player_resp.raise_for_status = MagicMock()
        player_resp.json = MagicMock(return_value={"id": 7})

        # GET /ships/player/7 returns two ships owned by the player
        ships_resp = MagicMock()
        ships_resp.status_code = 200
        ships_resp.json = MagicMock(
            return_value=[
                {"ship_name": "Niode", "ship_id": 1},
                {"ship_name": "Groza", "ship_id": 2},
            ]
        )

        mock_admin_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_admin_cog.http_client.get = AsyncMock(return_value=ships_resp)

        result = asyncio.run(mock_admin_cog.player_ship_autocomplete(interaction, ""))

        names = [c.name for c in result]
        assert "Niode" in names
        assert "Groza" in names
        # Should NOT have fetched all-ships fallback (only called player ships endpoint)
        get_calls = [call[0][0] for call in mock_admin_cog.http_client.get.call_args_list]
        assert any("ships/player/7" in url for url in get_calls)
        assert not any("about/ships" in url for url in get_calls)

    def test_falls_back_to_all_ships_when_user_param_is_none(self, mock_admin_cog):
        """When namespace.user is None (not yet selected), all ships from _ship_catalog are shown.

        Package E: fallback now reads from preloaded _ship_catalog (zero HTTP calls).
        """
        interaction = self._make_interaction(target_user=None, guild_id=99)

        # Populate the ship catalog (simulating what _preload_static_catalogs would do)
        mock_admin_cog._ship_catalog.set("all", ["Niode", "Groza", "Bloodstar"])
        mock_admin_cog.http_client.get = AsyncMock()

        result = asyncio.run(mock_admin_cog.player_ship_autocomplete(interaction, ""))

        names = [c.name for c in result]
        assert "Niode" in names
        assert "Groza" in names
        assert "Bloodstar" in names
        # No HTTP calls — served from in-memory catalog
        mock_admin_cog.http_client.get.assert_not_called()

    def test_falls_back_when_player_resolution_fails(self, mock_admin_cog):
        """When player resolution returns None, falls back to _ship_catalog (no HTTP, no error raised).

        Package E: fallback now reads from preloaded _ship_catalog.
        """
        target_user = self._make_user(user_id=42)
        interaction = self._make_interaction(target_user=target_user, guild_id=99)

        # POST /players/ returns non-200 → resolve_player_id returns None
        player_resp = MagicMock()
        player_resp.status_code = 400
        player_resp.raise_for_status = MagicMock(side_effect=Exception("guild not configured"))

        mock_admin_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_admin_cog.http_client.get = AsyncMock()
        # Populate ship catalog
        mock_admin_cog._ship_catalog.set("all", ["Niode", "Groza"])

        result = asyncio.run(mock_admin_cog.player_ship_autocomplete(interaction, ""))

        # Must not raise — must return the fallback all-ships list from cache
        assert isinstance(result, list)
        names = [c.name for c in result]
        assert "Niode" in names
        # No about/ships HTTP call made
        mock_admin_cog.http_client.get.assert_not_called()

    def test_filters_ships_by_current_input(self, mock_admin_cog):
        """Autocomplete filters results by the current typed prefix (case/accent-insensitive)."""
        target_user = self._make_user(user_id=42)
        interaction = self._make_interaction(target_user=target_user, guild_id=99)

        player_resp = MagicMock()
        player_resp.status_code = 200
        player_resp.raise_for_status = MagicMock()
        player_resp.json = MagicMock(return_value={"id": 7})

        ships_resp = MagicMock()
        ships_resp.status_code = 200
        ships_resp.json = MagicMock(
            return_value=[
                {"ship_name": "Niode", "ship_id": 1},
                {"ship_name": "Groza", "ship_id": 2},
                {"ship_name": "Bloodstar", "ship_id": 3},
            ]
        )
        mock_admin_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_admin_cog.http_client.get = AsyncMock(return_value=ships_resp)

        result = asyncio.run(mock_admin_cog.player_ship_autocomplete(interaction, "Ni"))

        names = [c.name for c in result]
        assert "Niode" in names
        assert "Groza" not in names
        assert "Bloodstar" not in names

    def test_returns_empty_on_unexpected_exception(self, mock_admin_cog):
        """Empty catalog (cold cache) returns [] without raising (silent degradation)."""
        interaction = self._make_interaction(target_user=None, guild_id=99)
        # _ship_catalog is empty (no preload run) → fallback returns []
        mock_admin_cog.http_client.get = AsyncMock()

        result = asyncio.run(mock_admin_cog.player_ship_autocomplete(interaction, ""))

        assert result == []


# ===========================================================================
# Package E — Tests #13–20: _preload_static_catalogs + cache-backed autocomplete
# ===========================================================================


class TestPreloadStaticCatalogs:
    """Tests for AdminCog._preload_static_catalogs (spec tests #13–16).

    B.33 remediation: all tests use respx to assert exact URL + HTTP method,
    matching the real bot-core route: GET /about/categories/{cat}/objects.
    The response shape is list[dict] with 'name' key — matching the actual
    server contract from about.py:85.
    """

    _API_BASE = "http://bot-core:8000/api/v1"

    # ------------------------------------------------------------------
    # Test #13 — populates _item_catalog for all 4 categories on success
    # Asserts: correct URL called, correct HTTP method (GET), correct cache state
    # ------------------------------------------------------------------

    def test_preload_populates_item_catalog_all_categories(self, mock_admin_cog):
        """_preload_static_catalogs calls GET /about/categories/{cat}/objects and
        populates _item_catalog for all 4 item categories on success."""
        import httpx
        import respx

        mock_admin_cog.bot.wait_until_ready = AsyncMock()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{self._API_BASE}/about/categories/primary_weapon/objects").mock(
                return_value=httpx.Response(200, json=[{"name": "Laser", "id": 1}, {"name": "Plasma", "id": 2}])
            )
            mock_router.get(f"{self._API_BASE}/about/categories/secondary_weapon/objects").mock(
                return_value=httpx.Response(200, json=[{"name": "Shield", "id": 3}])
            )
            mock_router.get(f"{self._API_BASE}/about/categories/turret_weapon/objects").mock(
                return_value=httpx.Response(200, json=[{"name": "Gatling", "id": 4}])
            )
            mock_router.get(f"{self._API_BASE}/about/categories/module/objects").mock(
                return_value=httpx.Response(200, json=[{"name": "Engine", "id": 5}])
            )
            mock_router.get(f"{self._API_BASE}/about/categories/ship/objects").mock(
                return_value=httpx.Response(200, json=[{"name": "Niode", "id": 6}])
            )

            asyncio.run(mock_admin_cog._preload_static_catalogs())

        # Verify correct cache population from list[dict] with 'name' key
        assert asyncio.run(mock_admin_cog._item_catalog.get("primary_weapon")) == ["Laser", "Plasma"]
        assert asyncio.run(mock_admin_cog._item_catalog.get("secondary_weapon")) == ["Shield"]
        assert asyncio.run(mock_admin_cog._item_catalog.get("turret_weapon")) == ["Gatling"]
        assert asyncio.run(mock_admin_cog._item_catalog.get("module")) == ["Engine"]

    # ------------------------------------------------------------------
    # Test #14 — populates _ship_catalog["all"] on success
    # Asserts: ship catalog URL is GET /about/categories/ship/objects (not /about/ships)
    # ------------------------------------------------------------------

    def test_preload_populates_ship_catalog(self, mock_admin_cog):
        """_preload_static_catalogs calls GET /about/categories/ship/objects and
        populates _ship_catalog['all'] correctly."""
        import httpx
        import respx

        mock_admin_cog.bot.wait_until_ready = AsyncMock()

        with respx.mock(assert_all_called=True) as mock_router:
            # 4 item categories — any valid response
            for cat in ("primary_weapon", "secondary_weapon", "turret_weapon", "module"):
                mock_router.get(f"{self._API_BASE}/about/categories/{cat}/objects").mock(
                    return_value=httpx.Response(200, json=[{"name": "Item", "id": 1}])
                )
            # Ship catalog — the correct endpoint (not the nonexistent /about/ships)
            mock_router.get(f"{self._API_BASE}/about/categories/ship/objects").mock(
                return_value=httpx.Response(
                    200,
                    json=[{"name": "Niode", "id": 10}, {"name": "Groza", "id": 11}, {"name": "Bloodstar", "id": 12}],
                )
            )

            asyncio.run(mock_admin_cog._preload_static_catalogs())

        ship_names = asyncio.run(mock_admin_cog._ship_catalog.get("all"))
        assert ship_names == ["Niode", "Groza", "Bloodstar"]

    # ------------------------------------------------------------------
    # Test #15 — partial failure: one category 500s, others succeed
    # Asserts: failed category cache is empty, successful categories are populated
    # ------------------------------------------------------------------

    def test_preload_partial_failure_one_category_fails(self, mock_admin_cog):
        """_preload_static_catalogs leaves the failed category empty while
        other categories are still populated on partial failure."""
        import httpx
        import respx

        mock_admin_cog.bot.wait_until_ready = AsyncMock()
        call_count = {"primary_weapon": 0}

        async def primary_weapon_handler(request):
            call_count["primary_weapon"] += 1
            # Fail all 5 attempts for primary_weapon
            return httpx.Response(500, json={"detail": "Internal Server Error"})

        with respx.mock(assert_all_called=False) as mock_router:
            mock_router.get(f"{self._API_BASE}/about/categories/primary_weapon/objects").mock(
                side_effect=primary_weapon_handler
            )
            mock_router.get(f"{self._API_BASE}/about/categories/secondary_weapon/objects").mock(
                return_value=httpx.Response(200, json=[{"name": "Shield", "id": 3}])
            )
            mock_router.get(f"{self._API_BASE}/about/categories/turret_weapon/objects").mock(
                return_value=httpx.Response(200, json=[{"name": "Gatling", "id": 4}])
            )
            mock_router.get(f"{self._API_BASE}/about/categories/module/objects").mock(
                return_value=httpx.Response(200, json=[{"name": "Engine", "id": 5}])
            )
            mock_router.get(f"{self._API_BASE}/about/categories/ship/objects").mock(
                return_value=httpx.Response(200, json=[{"name": "Niode", "id": 6}])
            )

            with patch("cogs.adminCog.asyncio.sleep", new=AsyncMock()):
                asyncio.run(mock_admin_cog._preload_static_catalogs())

        # Failed category → empty cache
        assert asyncio.run(mock_admin_cog._item_catalog.get("primary_weapon")) == []
        # Successful categories → populated
        assert asyncio.run(mock_admin_cog._item_catalog.get("secondary_weapon")) == ["Shield"]
        assert asyncio.run(mock_admin_cog._item_catalog.get("turret_weapon")) == ["Gatling"]
        assert asyncio.run(mock_admin_cog._item_catalog.get("module")) == ["Engine"]
        assert asyncio.run(mock_admin_cog._ship_catalog.get("all")) == ["Niode"]

    # ------------------------------------------------------------------
    # Test #15b — retries on transient 503 and eventually succeeds
    # ------------------------------------------------------------------

    def test_preload_retries_on_transient_error_and_succeeds(self, mock_admin_cog):
        """_preload_static_catalogs retries on 503 and succeeds on the second attempt."""
        import httpx
        import respx

        mock_admin_cog.bot.wait_until_ready = AsyncMock()
        attempt_tracker = {"primary_weapon_calls": 0}

        async def primary_weapon_flaky(request):
            attempt_tracker["primary_weapon_calls"] += 1
            if attempt_tracker["primary_weapon_calls"] == 1:
                return httpx.Response(503, json={"detail": "Service Unavailable"})
            return httpx.Response(200, json=[{"name": "Laser", "id": 1}])

        with respx.mock(assert_all_called=False) as mock_router:
            mock_router.get(f"{self._API_BASE}/about/categories/primary_weapon/objects").mock(
                side_effect=primary_weapon_flaky
            )
            for cat in ("secondary_weapon", "turret_weapon", "module"):
                mock_router.get(f"{self._API_BASE}/about/categories/{cat}/objects").mock(
                    return_value=httpx.Response(200, json=[{"name": "Item", "id": 1}])
                )
            mock_router.get(f"{self._API_BASE}/about/categories/ship/objects").mock(
                return_value=httpx.Response(200, json=[{"name": "Ship", "id": 10}])
            )

            with patch("cogs.adminCog.asyncio.sleep", new=AsyncMock()):
                asyncio.run(mock_admin_cog._preload_static_catalogs())

        # primary_weapon should be populated after successful retry
        result = asyncio.run(mock_admin_cog._item_catalog.get("primary_weapon"))
        assert result == ["Laser"]
        assert attempt_tracker["primary_weapon_calls"] == 2

    # ------------------------------------------------------------------
    # Test #16 — terminal failure leaves caches empty, no exception bubbles
    # Asserts: 5 retries exhausted, cache set to [], no exception raised
    # ------------------------------------------------------------------

    def test_preload_terminal_failure_leaves_caches_empty(self, mock_admin_cog):
        """_preload_static_catalogs leaves all caches empty on terminal failure;
        no exception raised to the caller."""
        import httpx
        import respx

        mock_admin_cog.bot.wait_until_ready = AsyncMock()

        with respx.mock(assert_all_called=False) as mock_router:
            # All endpoints return 500 on every attempt
            for cat in ("primary_weapon", "secondary_weapon", "turret_weapon", "module", "ship"):
                mock_router.get(f"{self._API_BASE}/about/categories/{cat}/objects").mock(
                    return_value=httpx.Response(500, json={"detail": "Internal Server Error"})
                )

            with patch("cogs.adminCog.asyncio.sleep", new=AsyncMock()):
                # Must not raise even when all retries fail
                asyncio.run(mock_admin_cog._preload_static_catalogs())

        # All caches should be set to empty lists after terminal failure
        assert asyncio.run(mock_admin_cog._item_catalog.get("primary_weapon")) == []
        assert asyncio.run(mock_admin_cog._item_catalog.get("secondary_weapon")) == []
        assert asyncio.run(mock_admin_cog._item_catalog.get("turret_weapon")) == []
        assert asyncio.run(mock_admin_cog._item_catalog.get("module")) == []
        assert asyncio.run(mock_admin_cog._ship_catalog.get("all")) == []


class TestItemNameAutocompleteFromCache:
    """Tests for item_name_autocomplete reading from preloaded cache (spec tests #17, #19)."""

    def _make_interaction(self, item_type=None):
        interaction = _create_mock_interaction()
        interaction.namespace = MagicMock()
        interaction.namespace.item_type = item_type
        return interaction

    # ------------------------------------------------------------------
    # Test #17 — after preload, autocomplete returns choices without HTTP calls
    # ------------------------------------------------------------------

    def test_autocomplete_after_preload_no_http_calls(self, mock_admin_cog):
        """item_name_autocomplete after preload returns choices without HTTP calls."""
        mock_admin_cog._item_catalog.set("primary_weapon", ["Laser Cannon", "Plasma Gun"])
        mock_admin_cog._item_catalog.set("secondary_weapon", ["Shield Array"])
        mock_admin_cog._item_catalog.set("turret_weapon", ["Gatling Turret"])
        mock_admin_cog._item_catalog.set("module", ["Engine Core"])
        mock_admin_cog.http_client.get = AsyncMock()

        interaction = self._make_interaction(item_type=None)
        result = asyncio.run(mock_admin_cog.item_name_autocomplete(interaction, ""))

        # No HTTP calls should have been made
        mock_admin_cog.http_client.get.assert_not_called()
        names = [c.name for c in result]
        assert "Laser Cannon" in names
        assert "Plasma Gun" in names
        assert "Shield Array" in names

    # ------------------------------------------------------------------
    # Test #19 — filtering by current substring works
    # ------------------------------------------------------------------

    def test_autocomplete_filters_by_current_substring(self, mock_admin_cog):
        """item_name_autocomplete filters names by the current input substring."""
        mock_admin_cog._item_catalog.set("primary_weapon", ["Laser Cannon", "Plasma Rifle", "Laser Pistol"])
        mock_admin_cog._item_catalog.set("secondary_weapon", [])
        mock_admin_cog._item_catalog.set("turret_weapon", [])
        mock_admin_cog._item_catalog.set("module", [])

        interaction = self._make_interaction(item_type=None)
        result = asyncio.run(mock_admin_cog.item_name_autocomplete(interaction, "Laser"))

        names = [c.name for c in result]
        assert "Laser Cannon" in names
        assert "Laser Pistol" in names
        assert "Plasma Rifle" not in names


class TestGameShipAutocompleteFromCache:
    """Tests for game_ship_autocomplete reading from preloaded cache (spec test #18)."""

    # ------------------------------------------------------------------
    # Test #18 — after preload, returns choices without HTTP calls
    # ------------------------------------------------------------------

    def test_autocomplete_after_preload_no_http_calls(self, mock_admin_cog):
        """game_ship_autocomplete after preload returns choices without HTTP calls."""
        mock_admin_cog._ship_catalog.set("all", ["Niode", "Groza", "Bloodstar"])
        mock_admin_cog.http_client.get = AsyncMock()

        interaction = _create_mock_interaction()
        result = asyncio.run(mock_admin_cog.game_ship_autocomplete(interaction, ""))

        mock_admin_cog.http_client.get.assert_not_called()
        names = [c.name for c in result]
        assert "Niode" in names
        assert "Groza" in names
        assert "Bloodstar" in names


class TestPlayerShipAutocompleteFallbackFromCache:
    """Tests for player_ship_autocomplete fallback branch reading from _ship_catalog (spec test #20)."""

    def _make_interaction(self, target_user, guild_id=9876):
        interaction = _create_mock_interaction()
        interaction.guild_id = guild_id
        interaction.namespace = MagicMock()
        interaction.namespace.user = target_user
        return interaction

    # ------------------------------------------------------------------
    # Test #20 — fallback reads from _ship_catalog, no HTTP
    # ------------------------------------------------------------------

    def test_fallback_reads_from_ship_catalog_no_http(self, mock_admin_cog):
        """player_ship_autocomplete fallback reads from _ship_catalog without HTTP."""
        mock_admin_cog._ship_catalog.set("all", ["Niode", "Groza", "Bloodstar"])
        mock_admin_cog.http_client.get = AsyncMock()
        mock_admin_cog.http_client.post = AsyncMock()

        # No target_user selected → forces fallback path
        interaction = self._make_interaction(target_user=None)
        result = asyncio.run(mock_admin_cog.player_ship_autocomplete(interaction, ""))

        mock_admin_cog.http_client.get.assert_not_called()
        names = [c.name for c in result]
        assert "Niode" in names
        assert "Groza" in names
        assert "Bloodstar" in names


if __name__ == "__main__":
    pytest.main([__file__])
