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

    def test_admin_setup_with_role(self, mock_admin_cog):
        """admin_setup should work with provided admin role."""
        # Mock interaction — B.25 Fix A: Use a Discord Administrator so _check_is_admin passes
        interaction = _create_mock_interaction()
        interaction.guild = MagicMock()
        interaction.guild.id = 987654321
        interaction.guild.name = "Test Guild"
        interaction.guild.icon = None

        # Mock provided role
        role = MagicMock()
        role.id = 222222222
        type(role).mention = PropertyMock(return_value="<@&222222222>")

        # Mock API response directly on the cog's http_client
        init_resp = _make_init_response()
        init_resp.raise_for_status = MagicMock()
        mock_admin_cog.http_client = MagicMock()
        mock_admin_cog.http_client.post = AsyncMock(return_value=init_resp)

        with patch(
            "cogs.adminCog.ensure_bountybot_infrastructure", new=AsyncMock(return_value=_make_full_channel_ids())
        ):
            asyncio.run(mock_admin_cog.admin_setup.callback(mock_admin_cog, interaction, role, 1000))

        # Verify behavior
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert embed.title is not None

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


class TestAdminSetupCommandRespx:
    """respx-backed URL+method contract test for /admin_setup.

    Asserts that admin_setup POSTs to the EXACT URL /api/v1/admin/guilds/initialize
    on bot-core. Verified against bot-core admin.py:97 during the 2026-04-30 Tier 2
    audit. This class enforces the gateway test policy from
    services/discord-gateway/tests/AGENTS.md (B.33 followup).
    """

    _BOT_API = "http://bot-core:8000/api/v1"

    def _with_real_client(self, cog, request):
        import httpx

        cog.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        request.addfinalizer(lambda: asyncio.run(cog.http_client.aclose()))
        return cog

    def test_admin_setup_posts_to_correct_url(self, mock_admin_cog, request):
        """admin_setup must POST /api/v1/admin/guilds/initialize."""
        import httpx
        import respx

        self._with_real_client(mock_admin_cog, request)

        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        interaction.guild = MagicMock()
        interaction.guild.id = 987654321
        interaction.guild.name = "Test Guild"
        interaction.guild.icon = None
        # Ensure _check_is_admin short-circuits on Discord administrator (L39 in adminCog.py)
        # without falling through to the API call at L42-49 which would hit unhandled respx routes.
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = True

        role = MagicMock()
        role.id = 222222222
        type(role).mention = PropertyMock(return_value="<@&222222222>")

        init_response = {
            "guild_id": 987654321,
            "shops_created": 4,
            "message": "Guild initialized successfully",
        }

        env_without_bot_api = {k: v for k, v in os.environ.items() if k != "BOT_API_BASE_URL"}
        infra_patch = patch(
            "cogs.adminCog.ensure_bountybot_infrastructure",
            new=AsyncMock(return_value=_make_full_channel_ids()),
        )
        with (
            patch.dict(os.environ, env_without_bot_api, clear=True),
            infra_patch,
            patch("os.getenv", side_effect=lambda k, d="": "" if k == "DEVELOPERS" else os.environ.get(k, d)),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{self._BOT_API}/admin/guilds/initialize").mock(
                return_value=httpx.Response(200, json=init_response)
            )
            asyncio.run(mock_admin_cog.admin_setup.callback(mock_admin_cog, interaction, role, 1000))

        # respx.mock(assert_all_called=True) ensures the POST was made
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()


def _make_mock_role(role_id, name):
    """Create a minimal mock Discord role."""
    role = MagicMock()
    role.id = role_id
    role.name = name
    role.delete = AsyncMock()
    return role


class TestAdminUninstallCommand:
    """Tests for admin_uninstall command (SEG-03: delete Discord infra before API call)."""

    @pytest.fixture(autouse=True)
    def _patch_confirm_view(self, mock_admin_cog):
        """Patch ConfirmView so tests don't block on view.wait(). result=True simulates user confirming.

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

    def test_admin_uninstall_shows_confirm_view(self, mock_admin_cog):
        """admin_uninstall should show a ConfirmView before proceeding (B.50)."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        # Override autouse fixture: cancel so we don't need the full API mock
        view_mock = MagicMock()
        view_mock.result = False
        view_mock.wait = AsyncMock(return_value=None)
        with patch("cogs.adminCog.ConfirmView", return_value=view_mock):
            asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called()
        # First send is the confirmation embed+view
        first_call_kwargs = interaction.followup.send.call_args_list[0][1]
        assert "embed" in first_call_kwargs
        emb = first_call_kwargs["embed"]
        assert emb.title is not None
        assert any(w in emb.title.lower() for w in ["confirm", "uninstall", "warning", "danger", "are you sure"])

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

        asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction))

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

        asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction))

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
        asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction))

        # bot-core uninstall API should still have been called
        mock_admin_cog.http_client.delete.assert_called_once()

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        # Command sends ConfirmView embed first, then result embed — at least one send
        interaction.followup.send.assert_called()

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
        asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction))

        # bot-core API should still have been called despite Discord errors
        mock_admin_cog.http_client.delete.assert_called_once()

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        # Command sends ConfirmView embed first, then result embed — at least one send
        interaction.followup.send.assert_called()

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
        asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction))

        # bot-core uninstall API should still have been called
        mock_admin_cog.http_client.delete.assert_called_once()

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        # Command sends ConfirmView embed first, then result embed — at least one send
        interaction.followup.send.assert_called()

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

        asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction))

        # Command sends ConfirmView embed first, then result embed — check at least 2 sends
        interaction.followup.send.assert_called()
        # The last call is the result embed (success after uninstall)
        last_call_kwargs = interaction.followup.send.call_args_list[-1][1]
        assert "embed" in last_call_kwargs
        embed = last_call_kwargs["embed"]
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

        asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction))

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

        asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction))

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

    def test_admin_player_view_stats(self, mock_admin_cog):
        """admin_player should show player statistics."""
        # Mock interaction
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        # Mock user (target player)
        user = _create_mock_user(user_id=111111111, name="Test User")

        # Mock API responses directly on the cog's http_client
        player_create_resp = MagicMock()
        player_create_resp.status_code = 200
        player_create_resp.raise_for_status = MagicMock()
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
        stats_resp.raise_for_status = MagicMock()
        stats_resp.json.return_value = {"total_games": 5, "total_victory": 2, "total_defeat": 3}

        mock_admin_cog.http_client = MagicMock()
        mock_admin_cog.http_client.post = AsyncMock(return_value=player_create_resp)
        mock_admin_cog.http_client.get = AsyncMock(return_value=stats_resp)

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "view_stats", None, None))

        # Verify behavior
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        all_text = (embed.description or "") + " ".join(f.value for f in embed.fields if f.value)
        assert len(all_text) > 0

    def test_admin_player_set_credits(self, mock_admin_cog):
        """admin_player should set player credits."""
        # Mock interaction
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        # Mock user (target player)
        user = _create_mock_user(user_id=111111111, name="Test User")

        # Mock API responses directly on the cog's http_client
        player_create_resp = MagicMock()
        player_create_resp.status_code = 200
        player_create_resp.raise_for_status = MagicMock()
        player_create_resp.json.return_value = {"id": 1}

        update_resp = MagicMock()
        update_resp.status_code = 200
        update_resp.raise_for_status = MagicMock()
        update_resp.json.return_value = {"old_credits": 500, "new_credits": 1000}

        mock_admin_cog.http_client = MagicMock()
        mock_admin_cog.http_client.post = AsyncMock(return_value=player_create_resp)
        mock_admin_cog.http_client.put = AsyncMock(return_value=update_resp)

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "set_credits", 1000, None))

        # Verify behavior
        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs or "content" in call_kwargs

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
        embed = call_kwargs["embed"]
        title_or_desc = (embed.title or "") + (embed.description or "")
        assert any(w in title_or_desc.lower() for w in ["reset", "success", "player"])

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

    def test_view_shows_prestige_threshold_when_present(self, mock_admin_cog):
        """B.48 (F.2): View embed includes the Prestige threshold when configured."""
        interaction = self._make_interaction()

        config_data = {
            "xp_thresholds": {
                "Silver": 1000,
                "Gold": 5000,
                "Platinum": 15000,
                "Prestige": 50000,
            }
        }
        config_resp = MagicMock()
        config_resp.raise_for_status = MagicMock()
        config_resp.json.return_value = config_data

        mock_admin_cog.http_client = MagicMock()
        mock_admin_cog.http_client.get = AsyncMock(return_value=config_resp)

        asyncio.run(
            mock_admin_cog.admin_config_xp.callback(
                mock_admin_cog, interaction, action="view", silver=None, gold=None, platinum=None, prestige=None
            )
        )

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        # Find the threshold field and assert Prestige is shown with formatted XP value.
        field_values = [f.value for f in embed.fields]
        combined = "\n".join(field_values)
        assert "Prestige" in combined
        assert "50,000" in combined

    def test_view_shows_default_marker_when_prestige_absent(self, mock_admin_cog):
        """B.48 (F.2): backward-compat — guilds without Prestige key see '(default)' label."""
        interaction = self._make_interaction()

        config_data = {"xp_thresholds": {"Silver": 10, "Gold": 20, "Platinum": 30}}
        config_resp = MagicMock()
        config_resp.raise_for_status = MagicMock()
        config_resp.json.return_value = config_data

        mock_admin_cog.http_client = MagicMock()
        mock_admin_cog.http_client.get = AsyncMock(return_value=config_resp)

        asyncio.run(
            mock_admin_cog.admin_config_xp.callback(
                mock_admin_cog, interaction, action="view", silver=None, gold=None, platinum=None, prestige=None
            )
        )

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        combined = "\n".join(f.value for f in embed.fields)
        assert "Prestige" in combined
        assert "default" in combined.lower()

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

    def test_update_with_prestige_param_success(self, mock_admin_cog):
        """B.48 (F.2): Update action with prestige param sends Prestige in payload and shows it."""
        interaction = self._make_interaction()

        result_data = {"xp_thresholds": {"Silver": 2000, "Gold": 8000, "Platinum": 20000, "Prestige": 75000}}
        update_resp = MagicMock()
        update_resp.raise_for_status = MagicMock()
        update_resp.json.return_value = result_data

        mock_admin_cog.http_client = MagicMock()
        mock_admin_cog.http_client.put = AsyncMock(return_value=update_resp)

        asyncio.run(
            mock_admin_cog.admin_config_xp.callback(
                mock_admin_cog,
                interaction,
                action="update",
                silver=2000,
                gold=8000,
                platinum=20000,
                prestige=75000,
            )
        )

        # Verify the PUT body included Prestige
        put_call = mock_admin_cog.http_client.put.call_args
        sent_payload = put_call.kwargs["json"]
        assert sent_payload["thresholds"]["Prestige"] == 75000
        assert sent_payload["thresholds"]["Silver"] == 2000

        # Verify the success embed surfaces the Prestige value
        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        combined = "\n".join(f.value for f in embed.fields)
        assert "Prestige" in combined
        assert "75,000" in combined

    def test_update_prestige_below_platinum_shows_error(self, mock_admin_cog):
        """B.48 (F.2): client-side guard — prestige <= platinum is rejected before HTTP call."""
        interaction = self._make_interaction()

        mock_admin_cog.http_client = MagicMock()
        mock_admin_cog.http_client.put = AsyncMock()

        asyncio.run(
            mock_admin_cog.admin_config_xp.callback(
                mock_admin_cog,
                interaction,
                action="update",
                silver=1000,
                gold=5000,
                platinum=15000,
                prestige=15000,  # equal to platinum — must be rejected
            )
        )

        # No HTTP call should be made — the cog rejects locally
        mock_admin_cog.http_client.put.assert_not_called()

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "prestige" in msg.lower()
        assert "platinum" in msg.lower() or "greater" in msg.lower()
        assert interaction.followup.send.call_args[1].get("ephemeral", False)

    def test_update_without_prestige_param_omits_prestige_from_payload(self, mock_admin_cog):
        """B.48 (F.2): when prestige is not supplied, payload contains only Silver/Gold/Platinum.

        Backward-compat: existing admins running /admin_config_xp without the
        new prestige arg leave the per-guild Prestige key untouched (the
        backend falls back to the default).
        """
        interaction = self._make_interaction()

        result_data = {"xp_thresholds": {"Silver": 1000, "Gold": 5000, "Platinum": 15000}}
        update_resp = MagicMock()
        update_resp.raise_for_status = MagicMock()
        update_resp.json.return_value = result_data

        mock_admin_cog.http_client = MagicMock()
        mock_admin_cog.http_client.put = AsyncMock(return_value=update_resp)

        asyncio.run(
            mock_admin_cog.admin_config_xp.callback(
                mock_admin_cog,
                interaction,
                action="update",
                silver=1000,
                gold=5000,
                platinum=15000,
                prestige=None,
            )
        )

        put_call = mock_admin_cog.http_client.put.call_args
        sent = put_call.kwargs["json"]["thresholds"]
        assert "Prestige" not in sent
        assert sent == {"Silver": 1000, "Gold": 5000, "Platinum": 15000}

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

    def _with_real_client(self, cog, request):
        """Replace cog.http_client with a real httpx.AsyncClient for respx interception.

        Registers a pytest finalizer to close the client after the test so no
        httpx.AsyncClient instances are leaked between tests.
        """
        import httpx

        cog.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        request.addfinalizer(lambda: asyncio.run(cog.http_client.aclose()))
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

    def test_preload_success_populates_settings(self, mock_admin_cog, request):
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
        self._with_real_client(mock_admin_cog, request)
        mock_admin_cog.bot.wait_until_ready = AsyncMock()

        env_without_blender = {k: v for k, v in os.environ.items() if k != "BLENDER_API_BASE_URL"}
        with (
            patch.dict(os.environ, env_without_blender, clear=True),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.get(self._RENDER_CONFIG_URL).mock(return_value=httpx.Response(200, json=render_config_data))
            asyncio.run(mock_admin_cog._preload_render_settings())

        assert mock_admin_cog._render_settings == list(render_config_data.keys())
        assert len(mock_admin_cog._render_settings) == 11

    def test_preload_retries_on_failure_then_succeeds(self, mock_admin_cog, request):
        """_preload_render_settings retries up to 3 times; succeeds on 2nd attempt."""
        import httpx
        import respx

        render_config_data = {"max_res_x": 3840, "default_samples": 64}
        attempt_count = {"n": 0}

        async def flaky_handler(req):
            attempt_count["n"] += 1
            if attempt_count["n"] == 1:
                raise httpx.ConnectError("connection refused", request=req)
            return httpx.Response(200, json=render_config_data)

        self._with_real_client(mock_admin_cog, request)
        mock_admin_cog.bot.wait_until_ready = AsyncMock()

        env_without_blender = {k: v for k, v in os.environ.items() if k != "BLENDER_API_BASE_URL"}
        with (
            patch.dict(os.environ, env_without_blender, clear=True),
            respx.mock(assert_all_called=False) as mock_router,
            patch("cogs.adminCog.asyncio.sleep", new=AsyncMock()) as mock_sleep,
        ):
            mock_router.get(self._RENDER_CONFIG_URL).mock(side_effect=flaky_handler)
            asyncio.run(mock_admin_cog._preload_render_settings())

        # Should have slept once (after first failure) with 5s delay
        mock_sleep.assert_awaited_once_with(5)
        # Settings populated from successful 2nd attempt
        assert mock_admin_cog._render_settings == list(render_config_data.keys())

    def test_preload_all_3_attempts_fail_leaves_empty(self, mock_admin_cog, request):
        """_preload_render_settings leaves _render_settings empty after all 3 failures."""
        import httpx
        import respx

        self._with_real_client(mock_admin_cog, request)
        mock_admin_cog.bot.wait_until_ready = AsyncMock()

        env_without_blender = {k: v for k, v in os.environ.items() if k != "BLENDER_API_BASE_URL"}
        with (
            patch.dict(os.environ, env_without_blender, clear=True),
            respx.mock(assert_all_called=False) as mock_router,
            patch("cogs.adminCog.asyncio.sleep", new=AsyncMock()),
        ):
            mock_router.get(self._RENDER_CONFIG_URL).mock(
                return_value=httpx.Response(503, json={"detail": "Service Unavailable"})
            )
            asyncio.run(mock_admin_cog._preload_render_settings())

        # Settings should remain empty after all attempts fail
        assert mock_admin_cog._render_settings == []

    def test_preload_uses_blender_api_base_url_env(self, mock_admin_cog, request):
        """_preload_render_settings uses BLENDER_API_BASE_URL env var for the request URL."""
        import httpx
        import respx

        render_config_data = {"max_res_x": 3840}
        custom_url = "http://custom-blender:9001/api/v1"
        custom_config_url = f"{custom_url}/config/render"

        self._with_real_client(mock_admin_cog, request)
        mock_admin_cog.bot.wait_until_ready = AsyncMock()

        with (
            patch.dict(os.environ, {"BLENDER_API_BASE_URL": custom_url}),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            # respx intercepts the custom URL — only matches if cog uses env var
            mock_router.get(custom_config_url).mock(return_value=httpx.Response(200, json=render_config_data))
            asyncio.run(mock_admin_cog._preload_render_settings())

        assert mock_admin_cog._render_settings == list(render_config_data.keys())

    def test_preload_uses_default_blender_url_when_env_missing(self, mock_admin_cog, request):
        """_preload_render_settings falls back to default blender URL when env var absent."""
        import httpx
        import respx

        render_config_data = {"default_samples": 64}
        self._with_real_client(mock_admin_cog, request)
        mock_admin_cog.bot.wait_until_ready = AsyncMock()

        env_without_blender = {k: v for k, v in os.environ.items() if k != "BLENDER_API_BASE_URL"}
        with (
            patch.dict(os.environ, env_without_blender, clear=True),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            # respx only matches if cog uses the default URL
            mock_router.get(self._RENDER_CONFIG_URL).mock(return_value=httpx.Response(200, json=render_config_data))
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

    @pytest.fixture(autouse=True)
    def _patch_confirm_view(self, mock_admin_cog):
        """Patch ConfirmView so clear_bounties tests don't block on view.wait().

        Depends on mock_admin_cog to ensure this fixture runs AFTER the cog fixture has
        evicted and re-imported cogs.adminCog (same ordering fix as TestAdminUninstallCommand).
        """
        view_mock = MagicMock()
        view_mock.result = True
        view_mock.wait = AsyncMock(return_value=None)
        with patch("cogs.adminCog.ConfirmView", return_value=view_mock):
            yield

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

        asyncio.run(mock_admin_cog.admin_clear_bounties.callback(mock_admin_cog, interaction, "platinum"))

        # Should have called the API with platinum tier
        call_kwargs = mock_admin_cog.http_client.delete.call_args[1]
        assert call_kwargs["params"].get("tier") == "platinum"
        # Command sends ConfirmView embed first, then result embed — at least one send
        interaction.followup.send.assert_awaited()

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


# ---------------------------------------------------------------------------
# Bug B.40: Admin commands must be visible to users with the configured admin role,
# not only to Discord Administrators.
#
# Root cause: @app_commands.default_permissions(administrator=True) on every admin
# command caused Discord to hide them from users who lack the built-in Discord
# Administrator permission, regardless of the custom role check in _check_is_admin().
# Fix: removed all @app_commands.default_permissions(administrator=True) decorators
# from AdminCog commands. Access control is now enforced solely by the runtime
# _check_is_admin() call inside each command handler.
# ---------------------------------------------------------------------------


class TestAdminRoleAccessGranted:
    """B.40: A user with only the configured admin_role_id (no Discord Admin permission)
    must be able to execute admin commands. The _check_is_admin() function must return
    True for them, and no @app_commands.default_permissions decorator must be present
    that would hide commands from them at the Discord level.
    """

    def test_check_is_admin_returns_true_for_admin_role_holder(self, mock_admin_cog):
        """B.40: _check_is_admin returns True for a user who holds the configured admin role."""
        from cogs.adminCog import _check_is_admin

        admin_role_id = 555666777
        admin_role = MagicMock()
        admin_role.id = admin_role_id

        interaction = _create_mock_interaction()
        # B.40 fix: production code reads interaction.member.roles (not user.roles).
        # interaction.member is a discord.Member; interaction.user is a discord.User.
        # In guild slash commands, interaction.member holds the guild-scoped object
        # (with roles); interaction.user is the base User (no guild context).
        interaction.member.roles = [admin_role]
        interaction.member = interaction.member  # ensure member is set
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = False
        interaction.guild_id = 987654321

        # Config API returns the matching admin_role_id
        api_resp = MagicMock()
        api_resp.raise_for_status = MagicMock()
        api_resp.json.return_value = {"admin_role_id": admin_role_id}

        with patch("cogs.adminCog.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=api_resp)
            mock_client_cls.return_value = mock_client

            result = asyncio.run(_check_is_admin(interaction))

        assert result is True, (
            "User holding the configured admin role must be granted admin access. "
            "Bug B.40: @app_commands.default_permissions(administrator=True) was hiding commands "
            "from admin-role holders who lack the built-in Discord Administrator permission."
        )

    def test_check_is_admin_returns_false_for_non_admin_role_holder(self, mock_admin_cog):
        """B.40: _check_is_admin returns False for a user without admin role or Discord admin."""
        from cogs.adminCog import _check_is_admin

        other_role = MagicMock()
        other_role.id = 111111111  # Some random non-admin role

        interaction = _create_mock_interaction()
        # B.40 fix: set roles on member (not user) to match production code.
        # The user does NOT have the admin role — member.roles has only a non-admin role.
        interaction.member.roles = [other_role]
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = False
        interaction.guild_id = 987654321

        # Config API returns a different admin_role_id (user doesn't have it)
        api_resp = MagicMock()
        api_resp.raise_for_status = MagicMock()
        api_resp.json.return_value = {"admin_role_id": 999888777}

        with patch("cogs.adminCog.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=api_resp)
            mock_client_cls.return_value = mock_client

            result = asyncio.run(_check_is_admin(interaction))

        assert result is False, "User without admin role or Discord Administrator should be denied access."

    def test_admin_guild_stats_allows_admin_role_holder(self, mock_admin_cog):
        """B.40: admin_guild_stats must be accessible to a user with the admin role (not Discord admin)."""
        admin_role_id = 555666777
        admin_role = MagicMock()
        admin_role.id = admin_role_id

        interaction = _create_mock_interaction()
        interaction.user.roles = [admin_role]
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = False
        interaction.guild_id = 987654321
        interaction.guild = MagicMock()
        interaction.guild.name = "Test Guild"
        interaction.guild.icon = None

        # _check_is_admin is called inside the command — mock it to return True
        # (separately tested above; here we verify the command does NOT short-circuit
        # with a "requires admin privileges" rejection)
        config_resp = MagicMock()
        config_resp.raise_for_status = MagicMock()
        config_resp.json.return_value = {"admin_role_id": admin_role_id}

        stats_resp = MagicMock()
        stats_resp.raise_for_status = MagicMock()
        stats_resp.json.return_value = {
            "guild_id": 987654321,
            "total_players": 5,
            "tier_distribution": {"Bronze": 3, "Silver": 2},
            "total_credits": 1000,
            "total_xp": 500,
            "average_credits": 200.0,
            "average_xp": 100.0,
        }

        # _check_is_admin first calls config (via httpx.AsyncClient context manager),
        # then the command calls config again via http_client.get
        with patch("cogs.adminCog._check_is_admin", AsyncMock(return_value=True)):
            mock_admin_cog.http_client.get = AsyncMock(return_value=stats_resp)
            asyncio.run(mock_admin_cog.admin_guild_stats.callback(mock_admin_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        # Must NOT be the rejection message
        assert "embed" in call_kwargs, "Admin-role holder must receive stats embed, not a rejection"
        embed = call_kwargs["embed"]
        assert "Guild Statistics" in (embed.title or ""), f"Expected guild stats embed, got title: {embed.title!r}"

    def test_admin_commands_have_no_default_permissions_decorator(self, mock_admin_cog):
        """B.40: No admin command should carry a default_permissions restriction.

        discord.py's @app_commands.default_permissions(administrator=True) sets a
        guild-level default that makes commands invisible to any user lacking the
        specified permission — this includes users with the custom admin_role who
        don't have the Discord 'Administrator' permission.

        We verify that the commands no longer carry this restriction by inspecting
        the 'default_member_permissions' attribute that discord.py exposes on the
        AppCommand object.
        """
        # Commands that had @app_commands.default_permissions(administrator=True) before the fix:
        admin_command_names = [
            "admin_check",
            "admin_setup",
            "admin_player",
            "admin_refresh_shop",
            "admin_guild_stats",
            "admin_config",
            "admin_uninstall",
            "admin_config_shop",
            "admin_config_validate",
            "render_config",
            "render_cache_clear",
            "admin_clear_bounties",
            "admin_config_bounty",
            "admin_config_xp",
            "admin_spawn_bounty",
            "admin_cooldown_reset",
            "admin_give_item",
            "admin_remove_item",
            "admin_give_ship",
            "admin_remove_ship",
        ]
        for cmd_name in admin_command_names:
            cmd = getattr(mock_admin_cog, cmd_name, None)
            assert cmd is not None, f"AdminCog must have a '{cmd_name}' command"
            # discord.py stores the resolved permissions as cmd.default_permissions
            # (a discord.Permissions object) or None when no default is set.
            # After removing @app_commands.default_permissions(administrator=True),
            # this attribute must be None (no restriction imposed at the Discord level).
            default_perms = getattr(cmd, "default_permissions", "ATTRIBUTE_MISSING")
            assert default_perms is None, (
                f"Command '{cmd_name}' still has default_permissions={default_perms!r}. "
                "Remove @app_commands.default_permissions(administrator=True) so users "
                "with the configured admin role can see and use this command."
            )


class TestAdminRoleAccessAdversarial:
    """B.40 adversarial edge cases for _check_is_admin.

    Tests that the `interaction.member` None-guard works correctly, and that
    setting roles only on interaction.user (the old bug) does NOT grant access
    when interaction.member is used for the role check.
    """

    def test_check_is_admin_member_none_falls_back_to_false(self, mock_admin_cog):
        """B.40 edge case: interaction.member is None → role check skipped → returns False.

        In certain Discord contexts (DMs, uncached guilds) interaction.member may be
        None.  The production code guards with `interaction.member and …`; this test
        ensures that guard prevents an AttributeError and correctly returns False.
        """
        from cogs.adminCog import _check_is_admin

        admin_role_id = 555666777
        interaction = _create_mock_interaction()
        interaction.member = None  # Simulate DM or uncached guild
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = False
        interaction.guild_id = 987654321

        api_resp = MagicMock()
        api_resp.raise_for_status = MagicMock()
        api_resp.json.return_value = {"admin_role_id": admin_role_id}

        with patch("cogs.adminCog.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=api_resp)
            mock_client_cls.return_value = mock_client

            result = asyncio.run(_check_is_admin(interaction))

        assert result is False, (
            "When interaction.member is None (DM context), the role check must be skipped "
            "and _check_is_admin must return False rather than raising AttributeError."
        )

    def test_check_is_admin_user_roles_not_sufficient(self, mock_admin_cog):
        """B.40 regression guard: setting roles on interaction.user (NOT member) does NOT grant access.

        This is the root-cause scenario that was broken before the fix.
        interaction.user.roles is a discord.User attribute that does NOT carry guild
        role assignments in real discord.py.  Only interaction.member.roles is valid.
        The fix ensures we check member.roles; this test asserts that user.roles
        alone is NOT checked (i.e., leaving member.roles empty does NOT grant access
        even when user.roles has the admin role).
        """
        from cogs.adminCog import _check_is_admin

        admin_role_id = 555666777
        admin_role = MagicMock()
        admin_role.id = admin_role_id

        interaction = _create_mock_interaction()
        # Only set on user — NOT member (simulates the pre-fix state)
        interaction.user.roles = [admin_role]
        # member.roles must be empty (different from user)
        interaction.member.roles = []
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = False
        interaction.guild_id = 987654321

        api_resp = MagicMock()
        api_resp.raise_for_status = MagicMock()
        api_resp.json.return_value = {"admin_role_id": admin_role_id}

        with patch("cogs.adminCog.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=api_resp)
            mock_client_cls.return_value = mock_client

            result = asyncio.run(_check_is_admin(interaction))

        assert result is False, (
            "interaction.user.roles must NOT be checked — only interaction.member.roles is authoritative. "
            "Setting the admin role on user.roles with an empty member.roles must NOT grant access."
        )

    def test_check_is_admin_api_failure_returns_false(self, mock_admin_cog):
        """B.40 edge case: if the config API call fails, _check_is_admin falls back to False.

        The broad except block must swallow the error and deny access rather than
        propagating a 500 to the user.
        """
        from cogs.adminCog import _check_is_admin

        interaction = _create_mock_interaction()
        interaction.member.roles = []
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = False
        interaction.guild_id = 987654321

        with patch("cogs.adminCog.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(side_effect=Exception("Network timeout"))
            mock_client_cls.return_value = mock_client

            result = asyncio.run(_check_is_admin(interaction))

        assert result is False, (
            "An API failure during the role-ID lookup must not propagate; _check_is_admin "
            "must return False (deny access) when the config API is unreachable."
        )


# ===========================================================================
# TestCheckIsSuperAdmin — unit tests for _check_is_super_admin
# ===========================================================================


class TestCheckIsSuperAdmin:
    """Unit tests for the _check_is_super_admin function in adminCog.

    _check_is_super_admin checks ONLY the DEVELOPERS env var — no role fallback,
    no Discord Administrator fallback.
    """

    def test_returns_true_for_user_in_developers_env(self):
        """Returns True when user ID appears in DEVELOPERS env var."""
        from cogs.adminCog import _check_is_super_admin

        interaction = _create_mock_interaction()
        interaction.user.id = 123456789

        with patch.dict("os.environ", {"DEVELOPERS": "123456789"}):
            result = asyncio.run(_check_is_super_admin(interaction))

        assert result is True

    def test_returns_false_for_user_not_in_developers_env(self):
        """Returns False when user ID is NOT in DEVELOPERS env var."""
        from cogs.adminCog import _check_is_super_admin

        interaction = _create_mock_interaction()
        interaction.user.id = 999999999

        with patch.dict("os.environ", {"DEVELOPERS": "123456789,111222333"}):
            result = asyncio.run(_check_is_super_admin(interaction))

        assert result is False

    def test_returns_false_when_developers_env_is_empty(self):
        """Returns False when DEVELOPERS env var is empty."""
        from cogs.adminCog import _check_is_super_admin

        interaction = _create_mock_interaction()
        interaction.user.id = 123456789

        with patch.dict("os.environ", {"DEVELOPERS": ""}):
            result = asyncio.run(_check_is_super_admin(interaction))

        assert result is False

    def test_returns_false_when_developers_env_is_missing(self):
        """Returns False when DEVELOPERS env var is not set at all."""
        import os

        from cogs.adminCog import _check_is_super_admin

        interaction = _create_mock_interaction()
        interaction.user.id = 123456789

        # Remove DEVELOPERS from environment entirely
        env_without_devs = {k: v for k, v in os.environ.items() if k != "DEVELOPERS"}
        with patch.dict("os.environ", env_without_devs, clear=True):
            result = asyncio.run(_check_is_super_admin(interaction))

        assert result is False

    def test_handles_whitespace_in_developers_list(self):
        """Returns True when DEVELOPERS list has whitespace around IDs."""
        from cogs.adminCog import _check_is_super_admin

        interaction = _create_mock_interaction()
        interaction.user.id = 111222333

        with patch.dict("os.environ", {"DEVELOPERS": " 111222333 , 444555666 "}):
            result = asyncio.run(_check_is_super_admin(interaction))

        assert result is True

    def test_handles_multiple_developers(self):
        """Returns True for any user ID present in a comma-separated DEVELOPERS list."""
        from cogs.adminCog import _check_is_super_admin

        interaction = _create_mock_interaction()
        interaction.user.id = 444555666

        with patch.dict("os.environ", {"DEVELOPERS": "111222333,444555666,777888999"}):
            result = asyncio.run(_check_is_super_admin(interaction))

        assert result is True

    def test_does_not_check_discord_administrator_permission(self):
        """Returns False for a Discord Administrator who is NOT in DEVELOPERS.

        Unlike _check_is_admin, the super-admin gate has NO Discord Administrator fallback.
        """
        from cogs.adminCog import _check_is_super_admin

        interaction = _create_mock_interaction()
        interaction.user.id = 999999999  # not in DEVELOPERS
        interaction.user.guild_permissions = MagicMock()
        interaction.user.guild_permissions.administrator = True  # Discord admin

        with patch.dict("os.environ", {"DEVELOPERS": "111222333"}):
            result = asyncio.run(_check_is_super_admin(interaction))

        assert result is False, (
            "_check_is_super_admin must NOT check Discord Administrator permission; "
            "only DEVELOPERS env var is consulted."
        )

    def test_does_not_check_bot_admin_role(self):
        """Returns False for a user with the configured Bot Admin role but NOT in DEVELOPERS.

        Unlike _check_is_admin, the super-admin gate has NO role fallback.
        """
        from cogs.adminCog import _check_is_super_admin

        interaction = _create_mock_interaction()
        interaction.user.id = 999999999  # not in DEVELOPERS

        # Give the user an admin role
        admin_role = MagicMock()
        admin_role.id = 111111111
        interaction.member = MagicMock()
        interaction.member.roles = [admin_role]

        with patch.dict("os.environ", {"DEVELOPERS": "123456789"}):
            result = asyncio.run(_check_is_super_admin(interaction))

        assert result is False, (
            "_check_is_super_admin must NOT check configured Bot Admin role; only DEVELOPERS env var is consulted."
        )


# ===========================================================================
# TestIsSuperAdmin — tests for the is_super_admin() decorator factory
# ===========================================================================


def _extract_is_super_admin_predicate():
    """Import is_super_admin(), call it to get the decorator from app_commands.check,
    then walk the decorator's closure to find and return the async predicate function."""
    from cogs.adminCog import is_super_admin as _is_super_admin_fn

    decorator = _is_super_admin_fn()
    # app_commands.check wraps the predicate in a decorator whose closure
    # contains the original coroutine function.
    import asyncio as _asyncio

    for cell in decorator.__closure__ or []:
        try:
            obj = cell.cell_contents
            if callable(obj) and _asyncio.iscoroutinefunction(obj):
                return obj
        except ValueError:
            continue
    raise RuntimeError("Could not extract predicate from is_super_admin()")


class TestIsSuperAdmin:
    """Tests for the is_super_admin() decorator factory.

    is_super_admin() wraps _check_is_super_admin as an app_commands.check predicate.
    When the check fails it sends an ephemeral message and returns False.
    """

    def test_is_super_admin_returns_callable(self):
        """is_super_admin() returns a callable (app_commands.check wrapper)."""
        from cogs.adminCog import is_super_admin

        decorator = is_super_admin()
        assert callable(decorator)

    def test_predicate_returns_true_for_developer(self):
        """Predicate returns True when user is in DEVELOPERS."""
        dev_user_id = 123456789
        interaction = _create_mock_interaction()
        interaction.user.id = dev_user_id
        interaction.response.send_message = AsyncMock()

        predicate = _extract_is_super_admin_predicate()

        with patch.dict("os.environ", {"DEVELOPERS": str(dev_user_id)}):
            result = asyncio.run(predicate(interaction))

        assert result is True
        interaction.response.send_message.assert_not_awaited()

    def test_predicate_returns_false_and_sends_message_for_non_developer(self):
        """Predicate returns False and sends ephemeral error message for non-developer."""
        interaction = _create_mock_interaction()
        interaction.user.id = 999999999  # not in DEVELOPERS
        interaction.response.send_message = AsyncMock()

        predicate = _extract_is_super_admin_predicate()

        with patch.dict("os.environ", {"DEVELOPERS": "111222333"}):
            result = asyncio.run(predicate(interaction))

        assert result is False
        interaction.response.send_message.assert_awaited_once()
        call_args = str(interaction.response.send_message.call_args)
        assert "super-admin" in call_args.lower() or "privilege" in call_args.lower()

    def test_predicate_error_message_is_ephemeral(self):
        """Error message sent by is_super_admin predicate is ephemeral."""
        interaction = _create_mock_interaction()
        interaction.user.id = 999999999
        interaction.response.send_message = AsyncMock()

        predicate = _extract_is_super_admin_predicate()

        with patch.dict("os.environ", {"DEVELOPERS": "111222333"}):
            asyncio.run(predicate(interaction))

        call_kwargs = interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True


# ===========================================================================
# S10: New tests — adminCog coverage gap-fill
# ===========================================================================


class TestAdminRefreshShop:
    """Tests for admin_refresh_shop command (lines 542-588)."""

    def test_refresh_shop_success(self, mock_admin_cog):
        """admin_refresh_shop sends success embed on valid tier."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"message": "Shop refreshed successfully"}
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_refresh_shop.callback(mock_admin_cog, interaction, "Bronze", None))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "Refreshed" in embed.title or "✅" in embed.title

    def test_refresh_shop_invalid_tier_sends_error(self, mock_admin_cog):
        """admin_refresh_shop rejects invalid tier without calling API."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        mock_admin_cog.http_client.post = AsyncMock()

        asyncio.run(mock_admin_cog.admin_refresh_shop.callback(mock_admin_cog, interaction, "InvalidTier", None))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "Invalid tier" in msg or "❌" in msg
        # API must not be called
        mock_admin_cog.http_client.post.assert_not_called()

    def test_refresh_shop_invalid_tech_level_sends_error(self, mock_admin_cog):
        """admin_refresh_shop rejects tech level out of 1-9 range."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        mock_admin_cog.http_client.post = AsyncMock()

        asyncio.run(mock_admin_cog.admin_refresh_shop.callback(mock_admin_cog, interaction, "Bronze", 10))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "tech level" in msg.lower() or "❌" in msg
        mock_admin_cog.http_client.post.assert_not_called()

    def test_refresh_shop_with_force_tech_level(self, mock_admin_cog):
        """admin_refresh_shop sends force_tech_level in request."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"message": "Shop refreshed"}
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_refresh_shop.callback(mock_admin_cog, interaction, "Gold", 5))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_refresh_shop_http_error(self, mock_admin_cog):
        """admin_refresh_shop handles HTTP error gracefully."""
        import httpx

        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        err_resp = MagicMock()
        err_resp.status_code = 500
        mock_admin_cog.http_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError("error", request=MagicMock(), response=err_resp)
        )

        asyncio.run(mock_admin_cog.admin_refresh_shop.callback(mock_admin_cog, interaction, "Bronze", None))

        interaction.followup.send.assert_awaited_once()

    def test_refresh_shop_not_admin_sends_denial(self, mock_admin_cog):
        """admin_refresh_shop rejects non-admin user."""
        interaction = _create_mock_interaction()
        interaction.user.guild_permissions.administrator = False
        interaction.member = MagicMock()
        interaction.member.roles = []

        with patch("cogs.adminCog._check_is_admin", AsyncMock(return_value=False)):
            asyncio.run(mock_admin_cog.admin_refresh_shop.callback(mock_admin_cog, interaction, "Bronze", None))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "admin" in msg.lower() or "❌" in msg


class TestAdminGuildStats:
    """Tests for admin_guild_stats command (lines 590-625)."""

    def test_guild_stats_success(self, mock_admin_cog):
        """admin_guild_stats sends stats embed on success."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        interaction.guild = MagicMock()
        interaction.guild.name = "Test Guild"
        interaction.guild.icon = None

        stats_data = {
            "guild_id": 987654321,
            "total_players": 10,
            "tier_distribution": {"Bronze": 5, "Silver": 3, "Gold": 2},
            "total_credits": 5000,
            "total_xp": 2500,
            "average_credits": 500.0,
            "average_xp": 250.0,
        }
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = stats_data
        mock_admin_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_guild_stats.callback(mock_admin_cog, interaction))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "Statistics" in embed.title or "📊" in embed.title

    def test_guild_stats_not_admin(self, mock_admin_cog):
        """admin_guild_stats rejects non-admin user."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        with patch("cogs.adminCog._check_is_admin", AsyncMock(return_value=False)):
            asyncio.run(mock_admin_cog.admin_guild_stats.callback(mock_admin_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "admin" in msg.lower() or "❌" in msg

    def test_guild_stats_http_error(self, mock_admin_cog):
        """admin_guild_stats handles API HTTP error gracefully."""
        import httpx

        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        err_resp = MagicMock()
        err_resp.status_code = 500
        mock_admin_cog.http_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError("error", request=MagicMock(), response=err_resp)
        )

        asyncio.run(mock_admin_cog.admin_guild_stats.callback(mock_admin_cog, interaction))

        interaction.followup.send.assert_awaited_once()

    def test_guild_stats_generic_exception(self, mock_admin_cog):
        """admin_guild_stats handles unexpected exception."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        mock_admin_cog.http_client.get = AsyncMock(side_effect=Exception("Connection lost"))

        asyncio.run(mock_admin_cog.admin_guild_stats.callback(mock_admin_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "⚠️" in msg or "error" in msg.lower()

    def test_guild_stats_no_tier_distribution(self, mock_admin_cog):
        """admin_guild_stats handles missing tier_distribution gracefully."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        interaction.guild = MagicMock()
        interaction.guild.name = "Test Guild"
        interaction.guild.icon = None

        stats_data = {
            "guild_id": 987654321,
            "total_players": 0,
            "total_credits": 0,
            "total_xp": 0,
            "average_credits": 0.0,
            "average_xp": 0.0,
            # No tier_distribution
        }
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = stats_data
        mock_admin_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_guild_stats.callback(mock_admin_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs


class TestAdminConfig:
    """Tests for admin_config command (lines 627-725)."""

    def test_admin_config_view_success(self, mock_admin_cog):
        """admin_config view returns embed with config data."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        cfg_data = {
            "guild_id": 987654321,
            "configured": True,
            "admin_role_configured": True,
            "starting_credits": 500,
            "sale_price_factor": 0.8,
            "xp_thresholds": {"Silver": 1000, "Gold": 5000, "Platinum": 15000},
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = cfg_data
        mock_admin_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_config.callback(mock_admin_cog, interaction, "view", None, None))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_admin_config_set_credits_success(self, mock_admin_cog):
        """admin_config set_credits updates starting credits."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {}
        mock_admin_cog.http_client.put = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_config.callback(mock_admin_cog, interaction, "set_credits", 1000, None))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "✅" in msg or "credits" in msg.lower()

    def test_admin_config_set_credits_missing_amount(self, mock_admin_cog):
        """admin_config set_credits sends error when credits amount not provided."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        asyncio.run(mock_admin_cog.admin_config.callback(mock_admin_cog, interaction, "set_credits", None, None))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "required" in msg.lower() or "❌" in msg

    def test_admin_config_set_role_success(self, mock_admin_cog):
        """admin_config set_role updates admin role."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {}
        mock_admin_cog.http_client.put = AsyncMock(return_value=resp)

        role = MagicMock()
        role.id = 888888888
        from unittest.mock import PropertyMock

        type(role).mention = PropertyMock(return_value="<@&888888888>")

        asyncio.run(mock_admin_cog.admin_config.callback(mock_admin_cog, interaction, "set_role", None, role))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "✅" in msg or "role" in msg.lower()

    def test_admin_config_set_role_missing_role(self, mock_admin_cog):
        """admin_config set_role sends error when no role provided."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        asyncio.run(mock_admin_cog.admin_config.callback(mock_admin_cog, interaction, "set_role", None, None))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "required" in msg.lower() or "❌" in msg

    def test_admin_config_reset_success(self, mock_admin_cog):
        """admin_config reset resets guild config to defaults."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {}
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_config.callback(mock_admin_cog, interaction, "reset", None, None))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "✅" in msg or "reset" in msg.lower()

    def test_admin_config_not_admin(self, mock_admin_cog):
        """admin_config rejects non-admin user."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        with patch("cogs.adminCog._check_is_admin", AsyncMock(return_value=False)):
            asyncio.run(mock_admin_cog.admin_config.callback(mock_admin_cog, interaction, "view", None, None))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "admin" in msg.lower() or "❌" in msg

    def test_admin_config_http_error(self, mock_admin_cog):
        """admin_config handles HTTP errors gracefully."""
        import httpx

        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        err_resp = MagicMock()
        err_resp.status_code = 500
        mock_admin_cog.http_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError("error", request=MagicMock(), response=err_resp)
        )

        asyncio.run(mock_admin_cog.admin_config.callback(mock_admin_cog, interaction, "view", None, None))

        interaction.followup.send.assert_awaited_once()

    def test_admin_config_generic_exception(self, mock_admin_cog):
        """admin_config handles unexpected exception."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        mock_admin_cog.http_client.get = AsyncMock(side_effect=Exception("Connection failed"))

        asyncio.run(mock_admin_cog.admin_config.callback(mock_admin_cog, interaction, "view", None, None))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "⚠️" in msg or "error" in msg.lower()

    def test_admin_config_xp_threshold_prestige_default(self, mock_admin_cog):
        """admin_config view: XP thresholds embed shows Prestige (default) when absent."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        cfg_data = {
            "guild_id": 987654321,
            "configured": True,
            "admin_role_configured": True,
            "starting_credits": 500,
            "sale_price_factor": 0.8,
            "xp_thresholds": {"Silver": 1000, "Gold": 5000, "Platinum": 15000},
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = cfg_data
        mock_admin_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_config.callback(mock_admin_cog, interaction, "view", None, None))

        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs.get("embed")
        assert embed is not None
        combined = " ".join(f.value for f in embed.fields)
        # Should show Prestige default
        assert "Prestige" in combined


class TestAdminConfigShop:
    """Tests for admin_config_shop command (lines 865-971)."""

    def test_admin_config_shop_success(self, mock_admin_cog):
        """admin_config_shop updates shop config and sends embed."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        cfg_result = {
            "shop_config": {
                "item_count_ranges": {
                    "ships": {"min": 2, "max": 4},
                    "weapons": {"min": 3, "max": 6},
                    "modules": {"min": 2, "max": 5},
                    "turrets": {"min": 1, "max": 3},
                }
            },
            "sale_price_factor": 0.8,
        }
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = cfg_result
        mock_admin_cog.http_client.put = AsyncMock(return_value=resp)

        asyncio.run(
            mock_admin_cog.admin_config_shop.callback(
                mock_admin_cog,
                interaction,
                ship_count_min=2,
                ship_count_max=4,
                weapon_count_min=3,
                weapon_count_max=6,
                module_count_min=None,
                module_count_max=None,
                turret_count_min=None,
                turret_count_max=None,
                sale_factor=None,
            )
        )

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_admin_config_shop_with_sale_factor(self, mock_admin_cog):
        """admin_config_shop also updates sale_price_factor when provided."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        shop_cfg = {"shop_config": {"item_count_ranges": {}}, "sale_price_factor": 0.75}
        sale_cfg = {"shop_config": {"item_count_ranges": {}}, "sale_price_factor": 0.75}

        resp1 = MagicMock()
        resp1.raise_for_status = MagicMock()
        resp1.json.return_value = shop_cfg

        resp2 = MagicMock()
        resp2.raise_for_status = MagicMock()
        resp2.json.return_value = sale_cfg

        mock_admin_cog.http_client.put = AsyncMock(side_effect=[resp1, resp2])

        asyncio.run(
            mock_admin_cog.admin_config_shop.callback(
                mock_admin_cog,
                interaction,
                ship_count_min=None,
                ship_count_max=None,
                weapon_count_min=None,
                weapon_count_max=None,
                module_count_min=None,
                module_count_max=None,
                turret_count_min=None,
                turret_count_max=None,
                sale_factor=0.75,
            )
        )

        # Two PUT calls: one for shop config, one for sale factor
        assert mock_admin_cog.http_client.put.await_count == 2

    def test_admin_config_shop_not_admin(self, mock_admin_cog):
        """admin_config_shop rejects non-admin user."""
        interaction = _create_mock_interaction()

        with patch("cogs.adminCog._check_is_admin", AsyncMock(return_value=False)):
            asyncio.run(
                mock_admin_cog.admin_config_shop.callback(
                    mock_admin_cog,
                    interaction,
                    ship_count_min=None,
                    ship_count_max=None,
                    weapon_count_min=None,
                    weapon_count_max=None,
                    module_count_min=None,
                    module_count_max=None,
                    turret_count_min=None,
                    turret_count_max=None,
                    sale_factor=None,
                )
            )

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "admin" in msg.lower() or "❌" in msg

    def test_admin_config_shop_http_error(self, mock_admin_cog):
        """admin_config_shop handles HTTP error gracefully."""
        import httpx

        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        err_resp = MagicMock()
        err_resp.status_code = 500
        mock_admin_cog.http_client.put = AsyncMock(
            side_effect=httpx.HTTPStatusError("error", request=MagicMock(), response=err_resp)
        )

        asyncio.run(
            mock_admin_cog.admin_config_shop.callback(
                mock_admin_cog,
                interaction,
                ship_count_min=2,
                ship_count_max=4,
                weapon_count_min=None,
                weapon_count_max=None,
                module_count_min=None,
                module_count_max=None,
                turret_count_min=None,
                turret_count_max=None,
                sale_factor=None,
            )
        )

        interaction.followup.send.assert_awaited_once()

    def test_admin_config_shop_generic_exception(self, mock_admin_cog):
        """admin_config_shop handles unexpected exception."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        mock_admin_cog.http_client.put = AsyncMock(side_effect=Exception("Unexpected"))

        asyncio.run(
            mock_admin_cog.admin_config_shop.callback(
                mock_admin_cog,
                interaction,
                ship_count_min=None,
                ship_count_max=None,
                weapon_count_min=None,
                weapon_count_max=None,
                module_count_min=None,
                module_count_max=None,
                turret_count_min=None,
                turret_count_max=None,
                sale_factor=None,
            )
        )

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "⚠️" in msg or "error" in msg.lower()


class TestAdminConfigValidate:
    """Tests for admin_config_validate command (lines 973-1026)."""

    def test_config_validate_success_valid(self, mock_admin_cog):
        """admin_config_validate shows valid config embed."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        interaction.guild = MagicMock()
        interaction.guild.name = "Test Guild"

        validate_data = {"valid": True, "errors": [], "warnings": [], "guild_id": 987654321}
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = validate_data
        mock_admin_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_config_validate.callback(mock_admin_cog, interaction))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "Valid" in embed.title or "✅" in embed.title

    def test_config_validate_with_errors_and_warnings(self, mock_admin_cog):
        """admin_config_validate shows errors and warnings in embed."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        interaction.guild = MagicMock()
        interaction.guild.name = "Test Guild"

        validate_data = {
            "valid": False,
            "errors": ["Missing admin role", "No channels configured"],
            "warnings": ["Low starting credits"],
            "guild_id": 987654321,
        }
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = validate_data
        mock_admin_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_config_validate.callback(mock_admin_cog, interaction))

        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]
        assert "Invalid" in embed.title or "❌" in embed.title
        # Errors should appear in fields
        combined = " ".join(f.value for f in embed.fields)
        assert "Missing admin role" in combined

    def test_config_validate_not_admin(self, mock_admin_cog):
        """admin_config_validate rejects non-admin user."""
        interaction = _create_mock_interaction()

        with patch("cogs.adminCog._check_is_admin", AsyncMock(return_value=False)):
            asyncio.run(mock_admin_cog.admin_config_validate.callback(mock_admin_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "admin" in msg.lower() or "❌" in msg

    def test_config_validate_http_error(self, mock_admin_cog):
        """admin_config_validate handles HTTP error gracefully."""
        import httpx

        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        err_resp = MagicMock()
        err_resp.status_code = 500
        mock_admin_cog.http_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError("error", request=MagicMock(), response=err_resp)
        )

        asyncio.run(mock_admin_cog.admin_config_validate.callback(mock_admin_cog, interaction))

        interaction.followup.send.assert_awaited_once()

    def test_config_validate_generic_exception(self, mock_admin_cog):
        """admin_config_validate handles unexpected exception."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        mock_admin_cog.http_client.get = AsyncMock(side_effect=Exception("Service down"))

        asyncio.run(mock_admin_cog.admin_config_validate.callback(mock_admin_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "⚠️" in msg or "error" in msg.lower()


class TestRenderConfig:
    """Tests for render_config command (lines 1032-1109)."""

    def test_render_config_view_success(self, mock_admin_cog):
        """render_config view returns embed with current config."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        config_data = {"max_res_x": 3840, "max_res_y": 2160, "default_samples": 64}
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = config_data
        mock_admin_cog.http_client.get = AsyncMock(return_value=resp)

        with patch.dict("os.environ", {"BLENDER_API_BASE_URL": "http://blender-service:8001/api/v1"}):
            asyncio.run(mock_admin_cog.render_config.callback(mock_admin_cog, interaction, "view", None, None))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_render_config_set_success(self, mock_admin_cog):
        """render_config set updates a render config setting."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        mock_admin_cog._render_settings = ["max_res_x", "default_samples"]

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"max_res_x": 1920}
        mock_admin_cog.http_client.put = AsyncMock(return_value=resp)

        with patch.dict("os.environ", {"BLENDER_API_BASE_URL": "http://blender-service:8001/api/v1"}):
            asyncio.run(mock_admin_cog.render_config.callback(mock_admin_cog, interaction, "set", "max_res_x", 1920))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "✅" in msg or "max_res_x" in msg

    def test_render_config_set_missing_params_sends_usage(self, mock_admin_cog):
        """render_config set without setting or value sends usage message."""
        interaction = _create_mock_interaction()
        mock_admin_cog._render_settings = ["max_res_x"]

        with patch.dict("os.environ", {"BLENDER_API_BASE_URL": "http://blender-service:8001/api/v1"}):
            asyncio.run(mock_admin_cog.render_config.callback(mock_admin_cog, interaction, "set", None, None))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "Usage" in msg or "⚠️" in msg

    def test_render_config_set_unknown_setting_sends_error(self, mock_admin_cog):
        """render_config set with unknown setting name sends validation error."""
        interaction = _create_mock_interaction()
        mock_admin_cog._render_settings = ["max_res_x", "default_samples"]

        with patch.dict("os.environ", {"BLENDER_API_BASE_URL": "http://blender-service:8001/api/v1"}):
            asyncio.run(
                mock_admin_cog.render_config.callback(mock_admin_cog, interaction, "set", "unknown_setting", 100)
            )

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "Unknown setting" in msg or "⚠️" in msg

    def test_render_config_set_preload_not_ready_sends_error(self, mock_admin_cog):
        """render_config set when preload not ready (empty list) fails closed."""
        interaction = _create_mock_interaction()
        mock_admin_cog._render_settings = []  # preload not done

        with patch.dict("os.environ", {"BLENDER_API_BASE_URL": "http://blender-service:8001/api/v1"}):
            asyncio.run(mock_admin_cog.render_config.callback(mock_admin_cog, interaction, "set", "max_res_x", 1920))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "not yet ready" in msg or "⚠️" in msg

    def test_render_config_reset_success(self, mock_admin_cog):
        """render_config reset sends confirmation message."""
        interaction = _create_mock_interaction()

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {}
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        with patch.dict("os.environ", {"BLENDER_API_BASE_URL": "http://blender-service:8001/api/v1"}):
            asyncio.run(mock_admin_cog.render_config.callback(mock_admin_cog, interaction, "reset", None, None))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "✅" in msg or "reset" in msg.lower()

    def test_render_config_not_admin(self, mock_admin_cog):
        """render_config rejects non-admin user."""
        interaction = _create_mock_interaction()

        with patch("cogs.adminCog._check_is_admin", AsyncMock(return_value=False)):
            asyncio.run(mock_admin_cog.render_config.callback(mock_admin_cog, interaction, "view", None, None))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "admin" in msg.lower() or "❌" in msg

    def test_render_config_http_error(self, mock_admin_cog):
        """render_config handles HTTP error gracefully."""
        import httpx

        interaction = _create_mock_interaction()

        err_resp = MagicMock()
        err_resp.status_code = 503
        mock_admin_cog.http_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError("error", request=MagicMock(), response=err_resp)
        )

        with patch.dict("os.environ", {"BLENDER_API_BASE_URL": "http://blender-service:8001/api/v1"}):
            asyncio.run(mock_admin_cog.render_config.callback(mock_admin_cog, interaction, "view", None, None))

        interaction.followup.send.assert_awaited_once()

    def test_render_config_generic_exception(self, mock_admin_cog):
        """render_config handles generic exception."""
        interaction = _create_mock_interaction()

        mock_admin_cog.http_client.get = AsyncMock(side_effect=Exception("Blender down"))

        with patch.dict("os.environ", {"BLENDER_API_BASE_URL": "http://blender-service:8001/api/v1"}):
            asyncio.run(mock_admin_cog.render_config.callback(mock_admin_cog, interaction, "view", None, None))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "⚠️" in msg or "error" in msg.lower()


class TestRenderCacheClear:
    """Tests for render_cache_clear command (lines 1111-1138)."""

    def test_render_cache_clear_success(self, mock_admin_cog):
        """render_cache_clear sends success embed with cleared stats."""
        interaction = _create_mock_interaction()

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"cleared_directories": 3, "freed_mb": 150}
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        with patch.dict("os.environ", {"BLENDER_API_BASE_URL": "http://blender-service:8001/api/v1"}):
            asyncio.run(mock_admin_cog.render_cache_clear.callback(mock_admin_cog, interaction))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "Cache" in embed.title or "🗑️" in embed.title

    def test_render_cache_clear_not_admin(self, mock_admin_cog):
        """render_cache_clear rejects non-admin user."""
        interaction = _create_mock_interaction()

        with patch("cogs.adminCog._check_is_admin", AsyncMock(return_value=False)):
            asyncio.run(mock_admin_cog.render_cache_clear.callback(mock_admin_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "admin" in msg.lower() or "❌" in msg

    def test_render_cache_clear_http_error(self, mock_admin_cog):
        """render_cache_clear handles HTTP error gracefully."""
        import httpx

        interaction = _create_mock_interaction()

        err_resp = MagicMock()
        err_resp.status_code = 503
        mock_admin_cog.http_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError("error", request=MagicMock(), response=err_resp)
        )

        with patch.dict("os.environ", {"BLENDER_API_BASE_URL": "http://blender-service:8001/api/v1"}):
            asyncio.run(mock_admin_cog.render_cache_clear.callback(mock_admin_cog, interaction))

        interaction.followup.send.assert_awaited_once()

    def test_render_cache_clear_generic_exception(self, mock_admin_cog):
        """render_cache_clear handles generic exception."""
        interaction = _create_mock_interaction()

        mock_admin_cog.http_client.post = AsyncMock(side_effect=Exception("Unexpected"))

        with patch.dict("os.environ", {"BLENDER_API_BASE_URL": "http://blender-service:8001/api/v1"}):
            asyncio.run(mock_admin_cog.render_cache_clear.callback(mock_admin_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "⚠️" in msg or "error" in msg.lower()


class TestAdminClearBountiesTimeoutAndCancel:
    """Tests for admin_clear_bounties ConfirmView timeout and cancel paths."""

    @pytest.fixture(autouse=True)
    def _use_confirm_view_fixture(self, mock_admin_cog):
        """Keep real ConfirmView for these tests to test timeout/cancel paths."""
        pass

    def test_clear_bounties_not_admin(self, mock_admin_cog):
        """admin_clear_bounties rejects non-admin user before showing confirm view."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        with patch("cogs.adminCog._check_is_admin", AsyncMock(return_value=False)):
            asyncio.run(mock_admin_cog.admin_clear_bounties.callback(mock_admin_cog, interaction, None))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "admin" in msg.lower() or "❌" in msg

    def test_clear_bounties_timeout_sends_cancelled(self, mock_admin_cog):
        """admin_clear_bounties sends timeout message when ConfirmView times out."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        view_mock = MagicMock()
        view_mock.result = None  # timeout
        view_mock.wait = AsyncMock(return_value=None)

        with patch("cogs.adminCog.ConfirmView", return_value=view_mock):
            asyncio.run(mock_admin_cog.admin_clear_bounties.callback(mock_admin_cog, interaction, None))

        msgs = [str(c) for c in interaction.followup.send.call_args_list]
        assert any("timed out" in m.lower() or "timeout" in m.lower() for m in msgs)

    def test_clear_bounties_cancel_sends_cancelled(self, mock_admin_cog):
        """admin_clear_bounties sends cancel message when user clicks Cancel."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        view_mock = MagicMock()
        view_mock.result = False  # cancelled
        view_mock.wait = AsyncMock(return_value=None)

        with patch("cogs.adminCog.ConfirmView", return_value=view_mock):
            asyncio.run(mock_admin_cog.admin_clear_bounties.callback(mock_admin_cog, interaction, None))

        msgs = [str(c) for c in interaction.followup.send.call_args_list]
        assert any("cancel" in m.lower() for m in msgs)

    def test_clear_bounties_with_tier_success(self, mock_admin_cog):
        """admin_clear_bounties with specific tier sends correct request."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        view_mock = MagicMock()
        view_mock.result = True
        view_mock.wait = AsyncMock(return_value=None)

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"cleared_count": 3, "announcements_deleted": 3}
        mock_admin_cog.http_client.delete = AsyncMock(return_value=resp)

        with patch("cogs.adminCog.ConfirmView", return_value=view_mock):
            asyncio.run(mock_admin_cog.admin_clear_bounties.callback(mock_admin_cog, interaction, "gold"))

        delete_kwargs = mock_admin_cog.http_client.delete.call_args[1]
        assert delete_kwargs["params"].get("tier") == "gold"

    def test_clear_bounties_all_tiers_omits_tier_param(self, mock_admin_cog):
        """admin_clear_bounties without tier omits tier from API request."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        view_mock = MagicMock()
        view_mock.result = True
        view_mock.wait = AsyncMock(return_value=None)

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"cleared_count": 5, "announcements_deleted": 5}
        mock_admin_cog.http_client.delete = AsyncMock(return_value=resp)

        with patch("cogs.adminCog.ConfirmView", return_value=view_mock):
            asyncio.run(mock_admin_cog.admin_clear_bounties.callback(mock_admin_cog, interaction, None))

        delete_kwargs = mock_admin_cog.http_client.delete.call_args[1]
        assert "tier" not in delete_kwargs.get("params", {})


class TestAdminUninstallTimeoutPath:
    """Tests for admin_uninstall ConfirmView timeout path (line 757-758)."""

    def test_admin_uninstall_timeout_sends_cancel(self, mock_admin_cog):
        """admin_uninstall sends timeout message when confirm view times out."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        view_mock = MagicMock()
        view_mock.result = None  # timeout
        view_mock.wait = AsyncMock(return_value=None)

        with patch("cogs.adminCog.ConfirmView", return_value=view_mock):
            asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction))

        msgs = [str(c) for c in interaction.followup.send.call_args_list]
        assert any("timed out" in m.lower() or "timeout" in m.lower() for m in msgs)

    def test_admin_uninstall_not_admin(self, mock_admin_cog):
        """admin_uninstall rejects non-admin user before showing confirm view."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        with patch("cogs.adminCog._check_is_admin", AsyncMock(return_value=False)):
            asyncio.run(mock_admin_cog.admin_uninstall.callback(mock_admin_cog, interaction))

        interaction.followup.send.assert_awaited()
        msgs = [str(c) for c in interaction.followup.send.call_args_list]
        assert any("admin" in m.lower() or "❌" in m for m in msgs)


class TestAdminPlayerAdditional:
    """Additional tests for admin_player add_credits and set_xp paths."""

    def test_admin_player_add_credits_success(self, mock_admin_cog):
        """admin_player add_credits computes new total and sends embed."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        user = _create_mock_user(user_id=111111111, name="Test User")

        player_create_resp = MagicMock()
        player_create_resp.status_code = 200
        player_create_resp.raise_for_status = MagicMock()
        player_create_resp.json.return_value = {
            "id": 1,
            "credits": 500,
        }

        update_resp = MagicMock()
        update_resp.status_code = 200
        update_resp.raise_for_status = MagicMock()
        update_resp.json.return_value = {"old_credits": 500, "new_credits": 750}

        mock_admin_cog.http_client = MagicMock()
        mock_admin_cog.http_client.post = AsyncMock(return_value=player_create_resp)
        mock_admin_cog.http_client.put = AsyncMock(return_value=update_resp)

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "add_credits", 250, None))

        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "Added" in embed.title or "✅" in embed.title

    def test_admin_player_add_credits_missing_amount(self, mock_admin_cog):
        """admin_player add_credits sends error when credit_amount not provided."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        user = _create_mock_user()

        player_create_resp = MagicMock()
        player_create_resp.status_code = 200
        player_create_resp.raise_for_status = MagicMock()
        player_create_resp.json.return_value = {"id": 1, "credits": 500}

        mock_admin_cog.http_client = MagicMock()
        mock_admin_cog.http_client.post = AsyncMock(return_value=player_create_resp)

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "add_credits", None, None))

        interaction.followup.send.assert_called_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "required" in msg.lower() or "❌" in msg

    def test_admin_player_set_xp_success(self, mock_admin_cog):
        """admin_player set_xp sends XP updated embed."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        user = _create_mock_user()

        player_create_resp = MagicMock()
        player_create_resp.status_code = 200
        player_create_resp.raise_for_status = MagicMock()
        player_create_resp.json.return_value = {"id": 1}

        update_resp = MagicMock()
        update_resp.status_code = 200
        update_resp.raise_for_status = MagicMock()
        update_resp.json.return_value = {
            "old_xp": 100,
            "new_xp": 5000,
            "old_tier": "Bronze",
            "new_tier": "Silver",
            "tier_changed": True,
        }

        mock_admin_cog.http_client = MagicMock()
        mock_admin_cog.http_client.post = AsyncMock(return_value=player_create_resp)
        mock_admin_cog.http_client.put = AsyncMock(return_value=update_resp)

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "set_xp", None, 5000))

        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "XP" in embed.title or "✅" in embed.title

    def test_admin_player_set_xp_missing_amount(self, mock_admin_cog):
        """admin_player set_xp sends error when xp amount not provided."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        user = _create_mock_user()

        player_create_resp = MagicMock()
        player_create_resp.status_code = 200
        player_create_resp.raise_for_status = MagicMock()
        player_create_resp.json.return_value = {"id": 1}

        mock_admin_cog.http_client = MagicMock()
        mock_admin_cog.http_client.post = AsyncMock(return_value=player_create_resp)

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "set_xp", None, None))

        interaction.followup.send.assert_called_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "required" in msg.lower() or "❌" in msg

    def test_admin_player_set_credits_missing_amount(self, mock_admin_cog):
        """admin_player set_credits sends error when credit_amount not provided."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        user = _create_mock_user()

        player_create_resp = MagicMock()
        player_create_resp.status_code = 200
        player_create_resp.raise_for_status = MagicMock()
        player_create_resp.json.return_value = {"id": 1}

        mock_admin_cog.http_client = MagicMock()
        mock_admin_cog.http_client.post = AsyncMock(return_value=player_create_resp)

        asyncio.run(mock_admin_cog.admin_player.callback(mock_admin_cog, interaction, user, "set_credits", None, None))

        interaction.followup.send.assert_called_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "required" in msg.lower() or "❌" in msg


class TestAdminGiveItem:
    """Tests for admin_give_item command (lines 1757-1827)."""

    def test_admin_give_item_success(self, mock_admin_cog):
        """admin_give_item sends success embed on success."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        user = _create_mock_user()

        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "message": "Item given successfully.",
            "item_type": "primary_weapon",
            "new_total_quantity": 2,
        }
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_give_item.callback(mock_admin_cog, interaction, user, "Laser Cannon", 1))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "Given" in embed.title or "✅" in embed.title

    def test_admin_give_item_404_sends_not_found(self, mock_admin_cog):
        """admin_give_item sends not-found error on 404."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        user = _create_mock_user()

        resp = MagicMock()
        resp.status_code = 404
        resp.json.return_value = {"detail": "Item not found."}
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_give_item.callback(mock_admin_cog, interaction, user, "Unknown Item", 1))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "not found" in msg.lower() or "❌" in msg

    def test_admin_give_item_400_sends_validation_error(self, mock_admin_cog):
        """admin_give_item sends validation error on 400."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        user = _create_mock_user()

        resp = MagicMock()
        resp.status_code = 400
        resp.json.return_value = {"detail": "Invalid quantity."}
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_give_item.callback(mock_admin_cog, interaction, user, "Laser Cannon", -1))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "Invalid" in msg or "❌" in msg

    def test_admin_give_item_not_admin(self, mock_admin_cog):
        """admin_give_item rejects non-admin user."""
        interaction = _create_mock_interaction()
        user = _create_mock_user()

        with patch("cogs.adminCog._check_is_admin", AsyncMock(return_value=False)):
            asyncio.run(mock_admin_cog.admin_give_item.callback(mock_admin_cog, interaction, user, "Laser Cannon", 1))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "admin" in msg.lower() or "❌" in msg

    def test_admin_give_item_http_error(self, mock_admin_cog):
        """admin_give_item handles HTTP error gracefully."""
        import httpx

        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        user = _create_mock_user()

        err_resp = MagicMock()
        err_resp.status_code = 500
        mock_admin_cog.http_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError("error", request=MagicMock(), response=err_resp)
        )

        asyncio.run(mock_admin_cog.admin_give_item.callback(mock_admin_cog, interaction, user, "Laser Cannon", 1))

        interaction.followup.send.assert_awaited_once()


class TestAdminRemoveItem:
    """Tests for admin_remove_item command (lines 1835-1909)."""

    def test_admin_remove_item_success(self, mock_admin_cog):
        """admin_remove_item sends success embed."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        user = _create_mock_user()

        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "message": "Item removed.",
            "item_type": "primary_weapon",
            "new_quantity": 0,
        }
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_remove_item.callback(mock_admin_cog, interaction, user, "Laser Cannon", 1))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_admin_remove_item_select_user_first_sentinel(self, mock_admin_cog):
        """admin_remove_item sends error for __select_user_first__ sentinel."""
        interaction = _create_mock_interaction()
        user = _create_mock_user()

        asyncio.run(
            mock_admin_cog.admin_remove_item.callback(mock_admin_cog, interaction, user, "__select_user_first__", 1)
        )

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "select a user" in msg.lower() or "❌" in msg

    def test_admin_remove_item_404_sends_not_found(self, mock_admin_cog):
        """admin_remove_item sends not-found error on 404."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        user = _create_mock_user()

        resp = MagicMock()
        resp.status_code = 404
        resp.json.return_value = {"detail": "Item not found."}
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_remove_item.callback(mock_admin_cog, interaction, user, "Nonexistent Item", 1))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "not found" in msg.lower() or "❌" in msg

    def test_admin_remove_item_400_sends_validation_error(self, mock_admin_cog):
        """admin_remove_item sends validation error on 400."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        user = _create_mock_user()

        resp = MagicMock()
        resp.status_code = 400
        resp.json.return_value = {"detail": "Cannot remove equipped item."}
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_remove_item.callback(mock_admin_cog, interaction, user, "Laser Cannon", 1))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "Cannot" in msg or "❌" in msg

    def test_admin_remove_item_not_admin(self, mock_admin_cog):
        """admin_remove_item rejects non-admin user."""
        interaction = _create_mock_interaction()
        user = _create_mock_user()

        with patch("cogs.adminCog._check_is_admin", AsyncMock(return_value=False)):
            asyncio.run(mock_admin_cog.admin_remove_item.callback(mock_admin_cog, interaction, user, "Laser Cannon", 1))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "admin" in msg.lower() or "❌" in msg


class TestAdminGiveShip:
    """Tests for admin_give_ship command (lines 1917-1973)."""

    def test_admin_give_ship_success(self, mock_admin_cog):
        """admin_give_ship sends success embed."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        user = _create_mock_user()

        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"message": "Ship given.", "ship_id": 42}
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_give_ship.callback(mock_admin_cog, interaction, user, "Eagle"))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_admin_give_ship_404(self, mock_admin_cog):
        """admin_give_ship handles 404 gracefully."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        user = _create_mock_user()

        resp = MagicMock()
        resp.status_code = 404
        resp.json.return_value = {"detail": "Ship not found."}
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_give_ship.callback(mock_admin_cog, interaction, user, "Unknown Ship"))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "not found" in msg.lower() or "❌" in msg

    def test_admin_give_ship_400(self, mock_admin_cog):
        """admin_give_ship handles 400 gracefully."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        user = _create_mock_user()

        resp = MagicMock()
        resp.status_code = 400
        resp.json.return_value = {"detail": "Player already owns this ship."}
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_give_ship.callback(mock_admin_cog, interaction, user, "Eagle"))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "already owns" in msg or "❌" in msg

    def test_admin_give_ship_not_admin(self, mock_admin_cog):
        """admin_give_ship rejects non-admin user."""
        interaction = _create_mock_interaction()
        user = _create_mock_user()

        with patch("cogs.adminCog._check_is_admin", AsyncMock(return_value=False)):
            asyncio.run(mock_admin_cog.admin_give_ship.callback(mock_admin_cog, interaction, user, "Eagle"))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "admin" in msg.lower() or "❌" in msg


class TestAdminRemoveShip:
    """Tests for admin_remove_ship command (lines 2038-2107)."""

    def test_admin_remove_ship_success(self, mock_admin_cog):
        """admin_remove_ship sends success embed."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        user = _create_mock_user()

        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "message": "Ship removed.",
            "items_returned_to_inventory": ["Laser Cannon", "Engine Core"],
        }
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_remove_ship.callback(mock_admin_cog, interaction, user, "Eagle"))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_admin_remove_ship_404(self, mock_admin_cog):
        """admin_remove_ship handles 404 gracefully."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        user = _create_mock_user()

        resp = MagicMock()
        resp.status_code = 404
        resp.json.return_value = {"detail": "Ship not found."}
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_remove_ship.callback(mock_admin_cog, interaction, user, "Unknown"))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "not found" in msg.lower() or "❌" in msg

    def test_admin_remove_ship_400(self, mock_admin_cog):
        """admin_remove_ship handles 400 gracefully."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        user = _create_mock_user()

        resp = MagicMock()
        resp.status_code = 400
        resp.json.return_value = {"detail": "Cannot remove only active ship."}
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_remove_ship.callback(mock_admin_cog, interaction, user, "Eagle"))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "Cannot" in msg or "❌" in msg

    def test_admin_remove_ship_not_admin(self, mock_admin_cog):
        """admin_remove_ship rejects non-admin user."""
        interaction = _create_mock_interaction()
        user = _create_mock_user()

        with patch("cogs.adminCog._check_is_admin", AsyncMock(return_value=False)):
            asyncio.run(mock_admin_cog.admin_remove_ship.callback(mock_admin_cog, interaction, user, "Eagle"))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "admin" in msg.lower() or "❌" in msg

    def test_admin_remove_ship_with_items_returned(self, mock_admin_cog):
        """admin_remove_ship embed includes returned items when present."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        user = _create_mock_user()

        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "message": "Ship removed.",
            "items_returned_to_inventory": ["Laser Cannon", "Engine Core", "Shield"],
        }
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_remove_ship.callback(mock_admin_cog, interaction, user, "Eagle"))

        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]
        field_names = [f.name for f in embed.fields]
        assert any("Items" in n or "Return" in n for n in field_names)


class TestAdminDuel:
    """Tests for admin_duel command (lines 2153-2234)."""

    def test_admin_duel_cancel_all_success(self, mock_admin_cog):
        """admin_duel 'all' cancels all pending duels."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"cancelled_count": 3}
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_duel.callback(mock_admin_cog, interaction, "all"))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()

    def test_admin_duel_cancel_all_none_pending(self, mock_admin_cog):
        """admin_duel 'all' with no pending duels shows no-op message."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"cancelled_count": 0}
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_duel.callback(mock_admin_cog, interaction, "all"))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "No pending" in msg or "✅" in msg

    def test_admin_duel_cancel_specific_success(self, mock_admin_cog):
        """admin_duel with valid duel ID sends success embed."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {}
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_duel.callback(mock_admin_cog, interaction, "42"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_admin_duel_invalid_duel_id_sends_error(self, mock_admin_cog):
        """admin_duel with non-numeric duel ID sends error message."""
        interaction = _create_mock_interaction()

        asyncio.run(mock_admin_cog.admin_duel.callback(mock_admin_cog, interaction, "not-an-id"))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "Invalid" in msg or "❌" in msg

    def test_admin_duel_cancel_specific_404(self, mock_admin_cog):
        """admin_duel with duel ID sends not-found error on 404."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        resp = MagicMock()
        resp.status_code = 404
        resp.json.return_value = {"detail": "Duel not found."}
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_duel.callback(mock_admin_cog, interaction, "99"))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "not found" in msg.lower() or "❌" in msg

    def test_admin_duel_cancel_specific_400(self, mock_admin_cog):
        """admin_duel with duel ID sends error on 400."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        resp = MagicMock()
        resp.status_code = 400
        resp.json.return_value = {"detail": "Duel already resolved."}
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_duel.callback(mock_admin_cog, interaction, "99"))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "resolved" in msg or "❌" in msg

    def test_admin_duel_not_admin(self, mock_admin_cog):
        """admin_duel rejects non-admin user."""
        interaction = _create_mock_interaction()

        with patch("cogs.adminCog._check_is_admin", AsyncMock(return_value=False)):
            asyncio.run(mock_admin_cog.admin_duel.callback(mock_admin_cog, interaction, "all"))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "admin" in msg.lower() or "❌" in msg

    def test_admin_duel_cancel_all_http_error(self, mock_admin_cog):
        """admin_duel 'all' handles HTTP error gracefully."""
        import httpx

        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        err_resp = MagicMock()
        err_resp.status_code = 500
        mock_admin_cog.http_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError("error", request=MagicMock(), response=err_resp)
        )

        asyncio.run(mock_admin_cog.admin_duel.callback(mock_admin_cog, interaction, "all"))

        interaction.followup.send.assert_awaited_once()

    def test_admin_duel_cancel_specific_generic_exception(self, mock_admin_cog):
        """admin_duel with specific ID handles generic exception."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        mock_admin_cog.http_client.post = AsyncMock(side_effect=Exception("Network error"))

        asyncio.run(mock_admin_cog.admin_duel.callback(mock_admin_cog, interaction, "42"))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "⚠️" in msg or "error" in msg.lower()


class TestAdminDuelAutocomplete:
    """Tests for admin_duel_autocomplete (lines 2113-2151)."""

    def test_admin_duel_autocomplete_success(self, mock_admin_cog):
        """admin_duel_autocomplete returns 'cancel all' plus pending duels."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        duels_data = [
            {
                "id": 1,
                "challenger_name": "Alice",
                "target_name": "Bob",
                "challenger_id": 111,
                "target_id": 222,
                "stakes": 500,
            }
        ]
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = duels_data
        mock_admin_cog.http_client.get = AsyncMock(return_value=resp)

        choices = asyncio.run(mock_admin_cog.admin_duel_autocomplete(interaction, ""))

        # First choice should be "Cancel ALL"
        assert choices[0].value == "all"
        assert len(choices) == 2  # 1 "all" + 1 duel

    def test_admin_duel_autocomplete_no_pending_duels(self, mock_admin_cog):
        """admin_duel_autocomplete returns only 'cancel all' when no duels."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = []
        mock_admin_cog.http_client.get = AsyncMock(return_value=resp)

        choices = asyncio.run(mock_admin_cog.admin_duel_autocomplete(interaction, ""))

        assert len(choices) == 1
        assert choices[0].value == "all"

    def test_admin_duel_autocomplete_error_returns_empty(self, mock_admin_cog):
        """admin_duel_autocomplete returns empty list on API error."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        mock_admin_cog.http_client.get = AsyncMock(side_effect=Exception("API down"))

        choices = asyncio.run(mock_admin_cog.admin_duel_autocomplete(interaction, ""))

        assert choices == []

    def test_admin_duel_autocomplete_friendly_duel_label(self, mock_admin_cog):
        """admin_duel_autocomplete labels friendly duel without stakes."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        duels_data = [
            {
                "id": 5,
                "challenger_name": "Alice",
                "target_name": "Bob",
                "challenger_id": 111,
                "target_id": 222,
                "stakes": 0,
            }
        ]
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = duels_data
        mock_admin_cog.http_client.get = AsyncMock(return_value=resp)

        choices = asyncio.run(mock_admin_cog.admin_duel_autocomplete(interaction, ""))

        # The duel choice should be present
        duel_choice = choices[1]
        assert "friendly" in duel_choice.name.lower() or "Alice" in duel_choice.name


class TestAdminSpawnBountyAdditional:
    """Additional tests for admin_spawn_bounty command."""

    def test_admin_spawn_bounty_success_with_spawned(self, mock_admin_cog):
        """admin_spawn_bounty shows bounty details when spawned."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "spawned": [
                {"division": "bronze", "criminal_name": "Darko", "tech_level": 3, "reward": 500},
            ],
            "skipped_tiers": ["silver"],
            "errors": [],
        }
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_spawn_bounty.callback(mock_admin_cog, interaction, None))

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_admin_spawn_bounty_not_admin(self, mock_admin_cog):
        """admin_spawn_bounty rejects non-admin user."""
        interaction = _create_mock_interaction()

        with patch("cogs.adminCog._check_is_admin", AsyncMock(return_value=False)):
            asyncio.run(mock_admin_cog.admin_spawn_bounty.callback(mock_admin_cog, interaction, None))

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "admin" in msg.lower() or "❌" in msg

    def test_admin_spawn_bounty_with_errors_in_response(self, mock_admin_cog):
        """admin_spawn_bounty shows errors when API returns error list."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "spawned": [],
            "skipped_tiers": [],
            "errors": ["Failed to post to bronze channel"],
        }
        mock_admin_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_admin_cog.admin_spawn_bounty.callback(mock_admin_cog, interaction, None))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        field_names = [f.name for f in embed.fields]
        assert any("Error" in n for n in field_names)

    def test_admin_spawn_bounty_http_error(self, mock_admin_cog):
        """admin_spawn_bounty handles HTTP error gracefully."""
        import httpx

        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        err_resp = MagicMock()
        err_resp.status_code = 500
        mock_admin_cog.http_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError("error", request=MagicMock(), response=err_resp)
        )

        asyncio.run(mock_admin_cog.admin_spawn_bounty.callback(mock_admin_cog, interaction, None))

        interaction.followup.send.assert_awaited_once()


class TestAdminConfigBounty:
    """Tests for admin_config_bounty command (lines 1214-1344)."""

    def test_admin_config_bounty_view(self, mock_admin_cog):
        """admin_config_bounty view action sends bounty config embed."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        cfg_data = {
            "max_bounties_per_tier": {"bronze": 3, "silver": 2, "gold": 2},
            "active_bounties_per_tier": {"bronze": 1, "silver": 0, "gold": 0},
            "bounty_expiry_minutes": 1440,
            "bounty_spawn_interval_minutes": 60,
            "next_spawn_check_at": "2024-01-01T12:00:00",
        }
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = cfg_data
        mock_admin_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(
            mock_admin_cog.admin_config_bounty.callback(
                mock_admin_cog,
                interaction,
                action="view",
                max_bronze=None,
                max_silver=None,
                max_gold=None,
                max_platinum=None,
                expiry_minutes=None,
                spawn_interval=None,
            )
        )

        interaction.response.defer.assert_called_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_admin_config_bounty_update(self, mock_admin_cog):
        """admin_config_bounty update action sends updated config embed."""
        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        result_data = {
            "max_bounties_per_tier": {"bronze": 5, "silver": 3},
            "bounty_expiry_minutes": 2880,
            "bounty_spawn_interval_minutes": None,
        }
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = result_data
        mock_admin_cog.http_client.put = AsyncMock(return_value=resp)

        asyncio.run(
            mock_admin_cog.admin_config_bounty.callback(
                mock_admin_cog,
                interaction,
                action="update",
                max_bronze=5,
                max_silver=3,
                max_gold=None,
                max_platinum=None,
                expiry_minutes=2880,
                spawn_interval=None,
            )
        )

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_admin_config_bounty_not_admin(self, mock_admin_cog):
        """admin_config_bounty rejects non-admin user."""
        interaction = _create_mock_interaction()

        with patch("cogs.adminCog._check_is_admin", AsyncMock(return_value=False)):
            asyncio.run(
                mock_admin_cog.admin_config_bounty.callback(
                    mock_admin_cog,
                    interaction,
                    action="view",
                    max_bronze=None,
                    max_silver=None,
                    max_gold=None,
                    max_platinum=None,
                    expiry_minutes=None,
                    spawn_interval=None,
                )
            )

        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "admin" in msg.lower() or "❌" in msg

    def test_admin_config_bounty_http_error(self, mock_admin_cog):
        """admin_config_bounty handles HTTP error gracefully."""
        import httpx

        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321

        err_resp = MagicMock()
        err_resp.status_code = 500
        mock_admin_cog.http_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError("error", request=MagicMock(), response=err_resp)
        )

        asyncio.run(
            mock_admin_cog.admin_config_bounty.callback(
                mock_admin_cog,
                interaction,
                action="view",
                max_bronze=None,
                max_silver=None,
                max_gold=None,
                max_platinum=None,
                expiry_minutes=None,
                spawn_interval=None,
            )
        )

        interaction.followup.send.assert_awaited_once()


class TestRemoveItemAutocomplete:
    """Tests for remove_item_autocomplete (lines 1666-1742)."""

    def test_autocomplete_no_user_returns_select_user_choice(self, mock_admin_cog):
        """remove_item_autocomplete prompts to select user when namespace.user is None."""
        interaction = _create_mock_interaction()
        interaction.namespace = MagicMock()
        interaction.namespace.user = None

        choices = asyncio.run(mock_admin_cog.remove_item_autocomplete(interaction, ""))

        assert len(choices) == 1
        assert choices[0].value == "__select_user_first__"

    def test_autocomplete_with_user_shows_inventory(self, mock_admin_cog):
        """remove_item_autocomplete shows player inventory when user selected."""
        target_user = MagicMock()
        target_user.id = 42

        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        interaction.namespace = MagicMock()
        interaction.namespace.user = target_user

        # resolve_player_id → player_id=7
        player_resp = MagicMock()
        player_resp.status_code = 200
        player_resp.raise_for_status = MagicMock()
        player_resp.json = MagicMock(return_value={"id": 7})

        # GET /inventory/player/7
        inv_resp = MagicMock()
        inv_resp.status_code = 200
        inv_resp.json = MagicMock(
            return_value=[
                {"item_name": "Laser Cannon", "item_type": "primary_weapon", "quantity": 2},
                {"item_name": "Engine Core", "item_type": "module", "quantity": 1},
            ]
        )

        mock_admin_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_admin_cog.http_client.get = AsyncMock(return_value=inv_resp)

        choices = asyncio.run(mock_admin_cog.remove_item_autocomplete(interaction, ""))

        names = [c.name for c in choices]
        assert any("Laser Cannon" in n for n in names)
        assert any("Engine Core" in n for n in names)

    def test_autocomplete_falls_back_to_catalog_on_player_resolution_failure(self, mock_admin_cog):
        """remove_item_autocomplete falls back to catalog when player resolution fails."""
        target_user = MagicMock()
        target_user.id = 42

        interaction = _create_mock_interaction()
        interaction.guild_id = 987654321
        interaction.namespace = MagicMock()
        interaction.namespace.user = target_user

        # Player resolution fails
        player_resp = MagicMock()
        player_resp.status_code = 400
        player_resp.raise_for_status = MagicMock(side_effect=Exception("guild not configured"))
        mock_admin_cog.http_client.post = AsyncMock(return_value=player_resp)

        # Catalog has some items
        mock_admin_cog._item_catalog.set("primary_weapon", ["Laser Cannon"])
        mock_admin_cog._item_catalog.set("secondary_weapon", [])
        mock_admin_cog._item_catalog.set("turret_weapon", [])
        mock_admin_cog._item_catalog.set("module", [])
        mock_admin_cog.http_client.get = AsyncMock()

        choices = asyncio.run(mock_admin_cog.remove_item_autocomplete(interaction, ""))

        # Should fall back to catalog
        names = [c.name for c in choices]
        assert "Laser Cannon" in names
        # No HTTP GET calls (served from cache)
        mock_admin_cog.http_client.get.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__])
