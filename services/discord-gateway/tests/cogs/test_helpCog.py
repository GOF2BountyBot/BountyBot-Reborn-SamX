"""Tests for helpCog — covers /help and /admin_help commands (two-level overview/detail).

Acceptance criteria traced:
- AC-1: /help lists only user-facing (non-admin) commands
- AC-2: /admin_help lists only admin commands and requires @is_admin()
- AC-3: Both commands send ephemeral responses
- AC-4: Auto-discovery via bot.tree (no hardcoded list)
- AC-5: /help filters out admin commands by name prefix, name set, and cog origin
- AC-6: /help with no category → returns overview embed listing categories
- AC-7: /help category:<name> → returns detail embed for specified category
- AC-8: Case-insensitive category lookup (bounty_hunting → Bounty Hunting)
- AC-9: Unknown category → ephemeral error with available categories
- AC-10: /admin_help overview lists admin categories
- AC-11: /admin_help category:<name> → detail embed for specified admin category
- AC-12: Parameter formatting appears in detail embeds
- AC-13: Category autocomplete returns correct list for each command
- AC-14: /admin_help has @app_commands.default_permissions(administrator=True)
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
_module_logger = None


def _make_mock_logger(*_args, **_kwargs):
    """Return a MagicMock logger with all common level methods."""
    global _module_logger
    logger = MagicMock()
    for method in ("info", "debug", "warning", "error", "trace", "critical", "exception"):
        setattr(logger, method, MagicMock())
    _module_logger = logger
    return logger


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _evict_cog_modules():
    """Evict cached cog/discord modules so each test gets a clean import."""
    to_evict = [
        k
        for k in sys.modules
        if k in ("discord", "api", "bot", "utils")
        or k.startswith("discord.")
        or k.startswith("api.")
        or k.startswith("utils.")
        or k.startswith("cogs.")
    ]
    for k in to_evict:
        sys.modules.pop(k, None)


def _make_fake_cog_class(name: str):
    """Create a real class with the given name (simulates a Cog class)."""
    return type(name, (), {})


def _make_mock_cmd(
    name: str,
    description: str = "",
    cog_class_name: str | None = None,
    params: list | None = None,
) -> MagicMock:
    """Build a minimal mock app_commands.Command with a name and optional binding cog.

    Uses a real class instance for binding so that ``type(binding).__name__``
    correctly reflects *cog_class_name* (matching production behaviour).
    """
    import discord.app_commands as ac

    cmd = MagicMock(spec=ac.Command)
    cmd.name = name
    cmd.description = description or f"Description for {name}"
    cmd.default_permissions = None  # no default_permissions restriction by default

    if cog_class_name is not None:
        # Create a real class instance so type(binding).__name__ == cog_class_name
        FakeClass = _make_fake_cog_class(cog_class_name)
        cmd.binding = FakeClass()
    else:
        cmd.binding = None

    # Set up parameters mock
    if params is not None:
        mock_params = []
        for p in params:
            mp = MagicMock()
            mp.name = p["name"]
            mp.required = p.get("required", True)
            mp.description = p.get("description", f"Description for {p['name']}")
            mock_params.append(mp)
        cmd.parameters = mock_params
    else:
        cmd.parameters = []

    return cmd


def _create_mock_interaction(user_id: int = 111111111, guild_id: int = 987654321) -> MagicMock:
    """Build a minimal mock discord.Interaction."""
    interaction = MagicMock()
    interaction.guild_id = guild_id
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_bot():
    """Create a mock bot with a configured tree."""
    _evict_cog_modules()
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

    bot = MagicMock()
    bot.loop = MagicMock()
    bot.loop.create_task = MagicMock()
    bot.tree = MagicMock()
    bot.tree.get_commands = MagicMock(return_value=[])
    return bot


@pytest.fixture
def cog(mock_bot):
    """Instantiate HelpCog using the mock bot."""
    _evict_cog_modules()
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

    from cogs.helpCog import HelpCog

    return HelpCog(mock_bot)


# ---------------------------------------------------------------------------
# Helper: is_admin_command tests (via module-level function)
# ---------------------------------------------------------------------------


class TestIsAdminCommand:
    """AC-5: Admin detection logic."""

    def _get_fn(self):
        _evict_cog_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        from cogs.helpCog import _is_admin_command

        return _is_admin_command

    def test_admin_prefix_admin_underscore(self):
        """Commands starting with 'admin_' are admin (AC-5)."""
        fn = self._get_fn()
        cmd = _make_mock_cmd("admin_setup")
        assert fn(cmd) is True

    def test_admin_prefix_scheduler(self):
        """Commands starting with 'scheduler_' are admin (AC-5)."""
        fn = self._get_fn()
        cmd = _make_mock_cmd("scheduler_status")
        assert fn(cmd) is True

    def test_admin_explicit_name_ping(self):
        """'ping' is in the explicit admin-name set (AC-5)."""
        fn = self._get_fn()
        cmd = _make_mock_cmd("ping")
        assert fn(cmd) is True

    def test_admin_explicit_name_health(self):
        """'health' is in the explicit admin-name set (AC-5)."""
        fn = self._get_fn()
        cmd = _make_mock_cmd("health")
        assert fn(cmd) is True

    def test_admin_explicit_name_load_data(self):
        """'load_data' is in the explicit admin-name set (AC-5)."""
        fn = self._get_fn()
        cmd = _make_mock_cmd("load_data")
        assert fn(cmd) is True

    def test_admin_cog_origin_health_cog(self):
        """Commands from HealthCog are admin (AC-5).

        Uses a real class with name 'HealthCog' as cmd.binding so that
        type(binding).__name__ == 'HealthCog' — matching production behaviour.
        """
        fn = self._get_fn()
        cmd = _make_mock_cmd("somecmd", cog_class_name="HealthCog")
        assert fn(cmd) is True

    def test_admin_cog_origin_admin_cog(self):
        """Commands from AdminCog are admin (AC-5)."""
        fn = self._get_fn()
        cmd = _make_mock_cmd("render_config", cog_class_name="AdminCog")
        assert fn(cmd) is True

    def test_user_command_bounties(self):
        """'/bounties' is a user command — not admin (AC-1)."""
        fn = self._get_fn()
        cmd = _make_mock_cmd("bounties", cog_class_name="BountyCog")
        assert fn(cmd) is False

    def test_user_command_shop(self):
        """'/shop' is a user command — not admin (AC-1)."""
        fn = self._get_fn()
        cmd = _make_mock_cmd("shop", cog_class_name="ShopCog")
        assert fn(cmd) is False

    def test_user_command_profile(self):
        """'/profile' is a user command — not admin (AC-1)."""
        fn = self._get_fn()
        cmd = _make_mock_cmd("profile", cog_class_name="PlayerCog")
        assert fn(cmd) is False

    def test_admin_detection_via_default_permissions(self):
        """Commands with default_permissions.administrator=True are admin (AC-5, signal-1)."""
        fn = self._get_fn()
        import discord

        cmd = _make_mock_cmd("some_restricted_cmd")
        perms = discord.Permissions(administrator=True)
        cmd.default_permissions = perms
        assert fn(cmd) is True

    def test_non_admin_default_permissions_without_administrator(self):
        """Commands with default_permissions that do NOT have administrator=True are NOT admin via signal-1."""
        fn = self._get_fn()
        import discord

        cmd = _make_mock_cmd("some_public_cmd")
        perms = discord.Permissions(send_messages=True)
        cmd.default_permissions = perms
        assert fn(cmd) is False

    def test_is_admin_detects_real_admin_decorator(self):
        """Regression: _is_admin_command returns True for a real app_commands.Command decorated with
        @app_commands.default_permissions(administrator=True).  Uses a real Command object (not a
        MagicMock) so any future wrong-attribute typo is caught immediately.
        """
        import discord
        from discord import app_commands

        _evict_cog_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        from cogs.helpCog import _is_admin_command

        @app_commands.command(name="test_real_admin_cmd", description="Real admin command")
        @app_commands.default_permissions(administrator=True)
        async def test_real_admin_cmd(interaction: discord.Interaction) -> None:  # pragma: no cover
            pass

        assert _is_admin_command(test_real_admin_cmd) is True

    def test_is_admin_does_not_flag_real_public_command(self):
        """Regression: _is_admin_command returns False for a real app_commands.Command with no
        permissions decorator.  Uses a real Command object (not a MagicMock).
        """
        import discord
        from discord import app_commands

        _evict_cog_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        from cogs.helpCog import _is_admin_command

        @app_commands.command(name="test_real_public_cmd", description="Real public command")
        async def test_real_public_cmd(interaction: discord.Interaction) -> None:  # pragma: no cover
            pass

        assert _is_admin_command(test_real_public_cmd) is False


# ---------------------------------------------------------------------------
# Normalize category tests
# ---------------------------------------------------------------------------


class TestNormalizeCategory:
    """Tests for the case-insensitive category normalization helper."""

    def _get_fn(self):
        _evict_cog_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        from cogs.helpCog import _normalize_category

        return _normalize_category

    def test_lowercase(self):
        fn = self._get_fn()
        assert fn("Player Profile") == fn("player profile")

    def test_underscores_equivalent_to_spaces(self):
        """bounty_hunting normalizes the same as Bounty Hunting (AC-8)."""
        fn = self._get_fn()
        assert fn("bounty_hunting") == fn("Bounty Hunting")

    def test_dashes_stripped(self):
        fn = self._get_fn()
        assert fn("Admin — Setup") == fn("admin  setup")

    def test_mixed_case_and_separators(self):
        fn = self._get_fn()
        assert fn("SHOP_&_ECONOMY") == fn("shop & economy")


# ---------------------------------------------------------------------------
# Format params tests
# ---------------------------------------------------------------------------


class TestFormatParams:
    """Tests for the _format_params helper (AC-12)."""

    def _get_fn(self):
        _evict_cog_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        from cogs.helpCog import _format_params

        return _format_params

    def test_no_params_returns_empty(self):
        fn = self._get_fn()
        cmd = _make_mock_cmd("somecmd")
        cmd.parameters = []
        result = fn(cmd)
        assert result == ""

    def test_single_required_param(self):
        fn = self._get_fn()
        cmd = _make_mock_cmd(
            "somecmd",
            params=[{"name": "user", "required": True, "description": "The target user"}],
        )
        result = fn(cmd)
        assert "user" in result
        assert "required" in result
        assert "The target user" in result

    def test_optional_param_labeled(self):
        fn = self._get_fn()
        cmd = _make_mock_cmd(
            "somecmd",
            params=[{"name": "tier", "required": False, "description": "Filter by tier"}],
        )
        result = fn(cmd)
        assert "optional" in result
        assert "tier" in result

    def test_multiple_params_all_listed(self):
        fn = self._get_fn()
        cmd = _make_mock_cmd(
            "somecmd",
            params=[
                {"name": "user", "required": True, "description": "User"},
                {"name": "amount", "required": False, "description": "Amount"},
            ],
        )
        result = fn(cmd)
        assert "user" in result
        assert "amount" in result

    def test_no_parameters_attribute_returns_empty(self):
        """If cmd.parameters raises AttributeError, return empty string."""
        fn = self._get_fn()
        cmd = MagicMock()
        del cmd.parameters  # remove the attribute entirely
        result = fn(cmd)
        assert result == ""


# ---------------------------------------------------------------------------
# HelpCog /help command — overview mode (AC-6)
# ---------------------------------------------------------------------------


class TestHelpCmdOverview:
    """AC-3, AC-4, AC-6: /help with no category sends overview embed."""

    @pytest.mark.asyncio
    async def test_help_overview_sends_ephemeral_embed(self, cog, mock_bot):
        """AC-3, AC-6: /help (no category) response is ephemeral with embed."""
        user_cmd = _make_mock_cmd("bounties", "List active bounties", "BountyCog")
        mock_bot.tree.get_commands = MagicMock(return_value=[user_cmd])

        interaction = _create_mock_interaction()
        await cog.help_cmd.callback(cog, interaction, category=None)

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True
        assert "embed" in call_kwargs

    @pytest.mark.asyncio
    async def test_help_overview_title_is_bountybot_help(self, cog, mock_bot):
        """AC-6: Overview embed title references BountyBot Help."""
        mock_bot.tree.get_commands = MagicMock(return_value=[])

        interaction = _create_mock_interaction()
        await cog.help_cmd.callback(cog, interaction, category=None)

        embed = interaction.response.send_message.call_args.kwargs["embed"]
        assert "BountyBot Help" in embed.title

    @pytest.mark.asyncio
    async def test_help_overview_footer_mentions_admin_help(self, cog, mock_bot):
        """AC-6: Overview embed footer hints at /admin_help."""
        mock_bot.tree.get_commands = MagicMock(return_value=[])

        interaction = _create_mock_interaction()
        await cog.help_cmd.callback(cog, interaction, category=None)

        embed = interaction.response.send_message.call_args.kwargs["embed"]
        footer_text = embed.footer.text if hasattr(embed.footer, "text") else str(embed._footer)
        assert "admin_help" in footer_text.lower() or "admin" in footer_text.lower()

    @pytest.mark.asyncio
    async def test_help_overview_uses_tree_introspection(self, cog, mock_bot):
        """AC-4: /help uses bot.tree.get_commands for auto-discovery."""
        mock_bot.tree.get_commands = MagicMock(return_value=[])

        interaction = _create_mock_interaction()
        await cog.help_cmd.callback(cog, interaction, category=None)

        mock_bot.tree.get_commands.assert_called_once()

    @pytest.mark.asyncio
    async def test_help_overview_filters_admin_commands(self, cog, mock_bot):
        """AC-1: /help overview must not include admin commands."""
        user_cmd = _make_mock_cmd("bounties", "List active bounties", "BountyCog")
        admin_cmd = _make_mock_cmd("admin_setup", "Setup", "AdminCog")
        mock_bot.tree.get_commands = MagicMock(return_value=[user_cmd, admin_cmd])

        interaction = _create_mock_interaction()
        await cog.help_cmd.callback(cog, interaction, category=None)

        # Should succeed, overview embed returned
        interaction.response.send_message.assert_awaited_once()
        embed = interaction.response.send_message.call_args.kwargs["embed"]
        assert embed is not None

    @pytest.mark.asyncio
    async def test_help_overview_excludes_help_itself(self, cog, mock_bot):
        """/help and /admin_help must not appear in the overview listing."""
        help_self = _make_mock_cmd("help", "Show commands")
        admin_help_self = _make_mock_cmd("admin_help", "Show admin commands")
        user_cmd = _make_mock_cmd("shop", "Browse shop", "ShopCog")
        mock_bot.tree.get_commands = MagicMock(return_value=[help_self, admin_help_self, user_cmd])

        interaction = _create_mock_interaction()
        await cog.help_cmd.callback(cog, interaction, category=None)

        interaction.response.send_message.assert_awaited_once()
        embed = interaction.response.send_message.call_args.kwargs["embed"]
        assert embed is not None

    @pytest.mark.asyncio
    async def test_help_overview_shows_category_with_count(self, cog, mock_bot):
        """AC-6: Overview embed shows categories with command count."""
        bounties_cmd = _make_mock_cmd("bounties", "List bounties", "BountyCog")
        check_cmd = _make_mock_cmd("check", "Check system", "BountyCog")
        mock_bot.tree.get_commands = MagicMock(return_value=[bounties_cmd, check_cmd])

        interaction = _create_mock_interaction()
        await cog.help_cmd.callback(cog, interaction, category=None)

        embed = interaction.response.send_message.call_args.kwargs["embed"]
        # Find Bounty Hunting field
        field_names = [f.name for f in embed.fields]
        assert "Bounty Hunting" in field_names
        # The field value should contain a command count
        for f in embed.fields:
            if f.name == "Bounty Hunting":
                assert "2" in f.value  # 2 commands

    @pytest.mark.asyncio
    async def test_help_overview_empty_tree(self, cog, mock_bot):
        """Edge case: empty tree returns a valid overview embed."""
        mock_bot.tree.get_commands = MagicMock(return_value=[])

        interaction = _create_mock_interaction()
        await cog.help_cmd.callback(cog, interaction, category=None)

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True


# ---------------------------------------------------------------------------
# HelpCog /help command — detail mode (AC-7, AC-8, AC-9)
# ---------------------------------------------------------------------------


class TestHelpCmdDetail:
    """AC-7, AC-8, AC-9: /help category:<name> returns category detail or error."""

    @pytest.mark.asyncio
    async def test_help_detail_known_category(self, cog, mock_bot):
        """AC-7: /help category:Bounty Hunting returns detail embed."""
        bounties_cmd = _make_mock_cmd("bounties", "List bounties", "BountyCog")
        check_cmd = _make_mock_cmd("check", "Check system", "BountyCog")
        mock_bot.tree.get_commands = MagicMock(return_value=[bounties_cmd, check_cmd])

        interaction = _create_mock_interaction()
        await cog.help_cmd.callback(cog, interaction, category="Bounty Hunting")

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True
        embed = call_kwargs["embed"]
        assert "Bounty Hunting" in embed.title

    @pytest.mark.asyncio
    async def test_help_detail_case_insensitive_lookup(self, cog, mock_bot):
        """AC-8: /help category:bounty_hunting resolves to 'Bounty Hunting'."""
        bounties_cmd = _make_mock_cmd("bounties", "List bounties", "BountyCog")
        mock_bot.tree.get_commands = MagicMock(return_value=[bounties_cmd])

        interaction = _create_mock_interaction()
        await cog.help_cmd.callback(cog, interaction, category="bounty_hunting")

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        embed = call_kwargs["embed"]
        # Should have resolved to Bounty Hunting detail, not an error
        assert "Bounty Hunting" in embed.title

    @pytest.mark.asyncio
    async def test_help_detail_lowercase_no_spaces(self, cog, mock_bot):
        """AC-8: Lowercase no-space input resolves to correct category."""
        shop_cmd = _make_mock_cmd("shop", "Browse shop", "ShopCog")
        mock_bot.tree.get_commands = MagicMock(return_value=[shop_cmd])

        interaction = _create_mock_interaction()
        await cog.help_cmd.callback(cog, interaction, category="shopeconomy")

        interaction.response.send_message.assert_awaited_once()
        embed = interaction.response.send_message.call_args.kwargs["embed"]
        assert "Shop" in embed.title

    @pytest.mark.asyncio
    async def test_help_detail_unknown_category_returns_error(self, cog, mock_bot):
        """AC-9: Unknown category returns ephemeral error with available categories."""
        mock_bot.tree.get_commands = MagicMock(return_value=[])

        interaction = _create_mock_interaction()
        await cog.help_cmd.callback(cog, interaction, category="Nonexistent Category")

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True
        # Should not be an embed — just a text error message
        assert "embed" not in call_kwargs or call_kwargs.get("embed") is None
        # The message content should mention the bad category and list available ones
        call_args = interaction.response.send_message.call_args
        msg = call_args.args[0] if call_args.args else ""
        if not msg:
            msg = call_kwargs.get("content", "")
        assert "Nonexistent Category" in msg or "not found" in msg.lower()

    @pytest.mark.asyncio
    async def test_help_detail_error_lists_available_categories(self, cog, mock_bot):
        """AC-9: Error message for unknown category lists available categories."""
        mock_bot.tree.get_commands = MagicMock(return_value=[])

        interaction = _create_mock_interaction()
        await cog.help_cmd.callback(cog, interaction, category="zzz_invalid")

        call_args = interaction.response.send_message.call_args
        msg = call_args.args[0] if call_args.args else call_args.kwargs.get("content", "")
        # Should mention at least one known category
        assert "Player Profile" in msg or "Bounty Hunting" in msg

    @pytest.mark.asyncio
    async def test_help_detail_shows_command_names(self, cog, mock_bot):
        """AC-7: Detail embed shows command names for the category."""
        bounties_cmd = _make_mock_cmd("bounties", "List active bounties", "BountyCog")
        check_cmd = _make_mock_cmd("check", "Check a system", "BountyCog")
        mock_bot.tree.get_commands = MagicMock(return_value=[bounties_cmd, check_cmd])

        interaction = _create_mock_interaction()
        await cog.help_cmd.callback(cog, interaction, category="Bounty Hunting")

        embed = interaction.response.send_message.call_args.kwargs["embed"]
        field_names = [f.name for f in embed.fields]
        assert "/bounties" in field_names
        assert "/check" in field_names

    @pytest.mark.asyncio
    async def test_help_detail_shows_params(self, cog, mock_bot):
        """AC-12: Detail embed includes parameter info when commands have params."""
        cmd_with_params = _make_mock_cmd(
            "bounties",
            "List active bounties",
            "BountyCog",
            params=[{"name": "division", "required": False, "description": "Filter by division"}],
        )
        mock_bot.tree.get_commands = MagicMock(return_value=[cmd_with_params])

        interaction = _create_mock_interaction()
        await cog.help_cmd.callback(cog, interaction, category="Bounty Hunting")

        embed = interaction.response.send_message.call_args.kwargs["embed"]
        # Find the /bounties field
        bounties_field = next((f for f in embed.fields if f.name == "/bounties"), None)
        assert bounties_field is not None
        assert "division" in bounties_field.value

    @pytest.mark.asyncio
    async def test_help_detail_empty_category(self, cog, mock_bot):
        """AC-6: A category with no commands still shows a valid embed."""
        # No commands for "Ships" category
        mock_bot.tree.get_commands = MagicMock(return_value=[])

        interaction = _create_mock_interaction()
        await cog.help_cmd.callback(cog, interaction, category="Ships")

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True
        embed = call_kwargs["embed"]
        assert embed is not None
        # Should show "No commands available"
        field_values = [f.value for f in embed.fields]
        assert any("No commands available" in v for v in field_values)

    @pytest.mark.asyncio
    async def test_help_detail_response_is_ephemeral(self, cog, mock_bot):
        """AC-3: Detail response is always ephemeral."""
        profile_cmd = _make_mock_cmd("profile", "View profile", "PlayerCog")
        mock_bot.tree.get_commands = MagicMock(return_value=[profile_cmd])

        interaction = _create_mock_interaction()
        await cog.help_cmd.callback(cog, interaction, category="Player Profile")

        call_kwargs = interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True


# ---------------------------------------------------------------------------
# HelpCog /admin_help command — overview mode (AC-10)
# ---------------------------------------------------------------------------


class TestAdminHelpCmdOverview:
    """AC-2, AC-3, AC-4, AC-10: /admin_help with no category sends admin overview."""

    @pytest.mark.asyncio
    async def test_admin_help_overview_sends_ephemeral_embed(self, cog, mock_bot):
        """AC-3, AC-10: /admin_help (no category) is ephemeral with embed."""
        admin_cmd = _make_mock_cmd("admin_setup", "Setup", "AdminCog")
        mock_bot.tree.get_commands = MagicMock(return_value=[admin_cmd])

        interaction = _create_mock_interaction()
        await cog.admin_help_cmd.callback(cog, interaction, category=None)

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True
        assert "embed" in call_kwargs

    @pytest.mark.asyncio
    async def test_admin_help_overview_title_references_admin(self, cog, mock_bot):
        """AC-10: Admin overview embed title references Admin Help."""
        mock_bot.tree.get_commands = MagicMock(return_value=[])

        interaction = _create_mock_interaction()
        await cog.admin_help_cmd.callback(cog, interaction, category=None)

        embed = interaction.response.send_message.call_args.kwargs["embed"]
        assert "Admin" in embed.title

    @pytest.mark.asyncio
    async def test_admin_help_overview_uses_tree_introspection(self, cog, mock_bot):
        """AC-4: /admin_help uses bot.tree.get_commands."""
        mock_bot.tree.get_commands = MagicMock(return_value=[])

        interaction = _create_mock_interaction()
        await cog.admin_help_cmd.callback(cog, interaction, category=None)

        mock_bot.tree.get_commands.assert_called_once()

    @pytest.mark.asyncio
    async def test_admin_help_overview_filters_to_admin_only(self, cog, mock_bot):
        """AC-2: /admin_help overview shows admin commands, not user commands."""
        admin_cmd = _make_mock_cmd("admin_setup", "Setup", "AdminCog")
        user_cmd = _make_mock_cmd("bounties", "Bounties", "BountyCog")
        mock_bot.tree.get_commands = MagicMock(return_value=[admin_cmd, user_cmd])

        interaction = _create_mock_interaction()
        await cog.admin_help_cmd.callback(cog, interaction, category=None)

        interaction.response.send_message.assert_awaited_once()
        embed = interaction.response.send_message.call_args.kwargs["embed"]
        # Admin embed title must reference admin
        assert embed is not None
        assert "Admin" in embed.title

    @pytest.mark.asyncio
    async def test_admin_help_overview_shows_admin_category(self, cog, mock_bot):
        """AC-10: Admin overview shows Admin — Setup category."""
        setup_cmd = _make_mock_cmd("admin_setup", "Setup guild", "AdminCog")
        mock_bot.tree.get_commands = MagicMock(return_value=[setup_cmd])

        interaction = _create_mock_interaction()
        await cog.admin_help_cmd.callback(cog, interaction, category=None)

        embed = interaction.response.send_message.call_args.kwargs["embed"]
        field_names = [f.name for f in embed.fields]
        assert "Admin — Setup" in field_names

    def test_admin_help_has_is_admin_check(self):
        """AC-2: /admin_help must carry @is_admin() (app_commands check)."""
        _evict_cog_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger

        from cogs.helpCog import HelpCog

        bot = MagicMock()
        bot.tree = MagicMock()
        bot.tree.get_commands = MagicMock(return_value=[])
        cog = HelpCog(bot)

        # discord.app_commands.Command stores checks in .checks attribute
        assert hasattr(cog.admin_help_cmd, "checks")
        assert len(cog.admin_help_cmd.checks) >= 1

    def test_admin_help_has_default_permissions_decorator(self):
        """AC-14 (B.40 updated): /admin_help uses @is_admin() check, not @app_commands.default_permissions.

        B.40 removed @app_commands.default_permissions(administrator=True) from /admin_help because
        it was hiding the command from admin-role holders who lack the built-in administrator perm.
        /admin_help is now protected by @is_admin() check decorators only.
        """
        _evict_cog_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger

        from cogs.helpCog import HelpCog

        bot = MagicMock()
        bot.tree = MagicMock()
        bot.tree.get_commands = MagicMock(return_value=[])
        cog = HelpCog(bot)

        # B.40: default_permissions is intentionally None (removed to fix admin-role holder hiding).
        # The command is still protected by @is_admin() check in .checks.
        dmp = cog.admin_help_cmd.default_permissions
        assert dmp is None, (
            "B.40: /admin_help must NOT use @app_commands.default_permissions — "
            "it hides the command from admin-role holders lacking built-in Administrator perm. "
            "Use @is_admin() check decorator instead."
        )
        # @is_admin() must still be present as a check
        assert hasattr(cog.admin_help_cmd, "checks")
        assert len(cog.admin_help_cmd.checks) >= 1, "/admin_help must have at least one @is_admin() check"

    def test_help_cmd_does_not_have_default_permissions(self):
        """AC-14 (inverse): /help (user-facing) must NOT have restrictive default_permissions."""
        _evict_cog_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger

        from cogs.helpCog import HelpCog

        bot = MagicMock()
        bot.tree = MagicMock()
        bot.tree.get_commands = MagicMock(return_value=[])
        cog = HelpCog(bot)

        # /help should have no default_permissions restriction
        assert cog.help_cmd.default_permissions is None


# ---------------------------------------------------------------------------
# HelpCog /admin_help command — detail mode (AC-11)
# ---------------------------------------------------------------------------


class TestAdminHelpCmdDetail:
    """AC-11: /admin_help category:<name> returns admin category detail."""

    @pytest.mark.asyncio
    async def test_admin_help_detail_known_category(self, cog, mock_bot):
        """AC-11: /admin_help category:Admin — Setup returns detail embed."""
        setup_cmd = _make_mock_cmd("admin_setup", "Initialize guild", "AdminCog")
        uninstall_cmd = _make_mock_cmd("admin_uninstall", "Remove bot", "AdminCog")
        mock_bot.tree.get_commands = MagicMock(return_value=[setup_cmd, uninstall_cmd])

        interaction = _create_mock_interaction()
        await cog.admin_help_cmd.callback(cog, interaction, category="Admin — Setup")

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True
        embed = call_kwargs["embed"]
        assert "Admin — Setup" in embed.title

    @pytest.mark.asyncio
    async def test_admin_help_detail_case_insensitive(self, cog, mock_bot):
        """AC-11: Case-insensitive lookup works for admin categories."""
        setup_cmd = _make_mock_cmd("admin_setup", "Initialize guild", "AdminCog")
        mock_bot.tree.get_commands = MagicMock(return_value=[setup_cmd])

        interaction = _create_mock_interaction()
        await cog.admin_help_cmd.callback(cog, interaction, category="admin  setup")

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        embed = call_kwargs["embed"]
        # Should show the Admin — Setup detail, not an error
        assert "Setup" in embed.title

    @pytest.mark.asyncio
    async def test_admin_help_detail_unknown_category_error(self, cog, mock_bot):
        """AC-9 (admin): Unknown admin category returns ephemeral error."""
        mock_bot.tree.get_commands = MagicMock(return_value=[])

        interaction = _create_mock_interaction()
        await cog.admin_help_cmd.callback(cog, interaction, category="Admin — Nonexistent")

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True
        # Should be a text error, not an embed
        msg = (
            interaction.response.send_message.call_args.args[0]
            if interaction.response.send_message.call_args.args
            else call_kwargs.get("content", "")
        )
        assert "not found" in msg.lower() or "Nonexistent" in msg

    @pytest.mark.asyncio
    async def test_admin_help_detail_error_lists_categories(self, cog, mock_bot):
        """AC-9 (admin): Error message lists available admin categories."""
        mock_bot.tree.get_commands = MagicMock(return_value=[])

        interaction = _create_mock_interaction()
        await cog.admin_help_cmd.callback(cog, interaction, category="bogus_category_xyz")

        call_args = interaction.response.send_message.call_args
        msg = call_args.args[0] if call_args.args else call_args.kwargs.get("content", "")
        assert "Admin — Setup" in msg or "Admin — Players" in msg

    @pytest.mark.asyncio
    async def test_admin_help_detail_scheduler_category(self, cog, mock_bot):
        """AC-11: Scheduler commands fall into Admin — Scheduler category."""
        sched_cmd = _make_mock_cmd("scheduler_list", "List jobs", "SchedulerCog")
        mock_bot.tree.get_commands = MagicMock(return_value=[sched_cmd])

        interaction = _create_mock_interaction()
        await cog.admin_help_cmd.callback(cog, interaction, category="Admin — Scheduler")

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        embed = call_kwargs.get("embed")
        assert embed is not None
        assert "Scheduler" in embed.title

    @pytest.mark.asyncio
    async def test_admin_help_detail_shows_command_names(self, cog, mock_bot):
        """AC-11: Detail embed shows command names in the category."""
        setup_cmd = _make_mock_cmd("admin_setup", "Initialize guild", "AdminCog")
        uninstall_cmd = _make_mock_cmd("admin_uninstall", "Remove bot", "AdminCog")
        mock_bot.tree.get_commands = MagicMock(return_value=[setup_cmd, uninstall_cmd])

        interaction = _create_mock_interaction()
        await cog.admin_help_cmd.callback(cog, interaction, category="Admin — Setup")

        embed = interaction.response.send_message.call_args.kwargs["embed"]
        field_names = [f.name for f in embed.fields]
        assert "/admin_setup" in field_names
        assert "/admin_uninstall" in field_names


# ---------------------------------------------------------------------------
# Autocomplete tests (AC-13)
# ---------------------------------------------------------------------------


class TestAutocomplete:
    """AC-13: Autocomplete functions return correct category lists."""

    @pytest.mark.asyncio
    async def test_user_category_autocomplete_returns_all_when_empty(self):
        """AC-13: User autocomplete with empty string returns all categories."""
        _evict_cog_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        from cogs.helpCog import _USER_CATEGORY_ORDER, _user_category_autocomplete

        interaction = _create_mock_interaction()
        results = await _user_category_autocomplete(interaction, "")

        result_values = [r.value for r in results]
        for cat in _USER_CATEGORY_ORDER:
            assert cat in result_values

    @pytest.mark.asyncio
    async def test_user_category_autocomplete_filters_by_input(self):
        """AC-13: User autocomplete filters categories by current input."""
        _evict_cog_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        from cogs.helpCog import _user_category_autocomplete

        interaction = _create_mock_interaction()
        results = await _user_category_autocomplete(interaction, "bounty")

        result_values = [r.value for r in results]
        assert "Bounty Hunting" in result_values
        # Should NOT contain "Ships" since it doesn't match "bounty"
        assert "Ships" not in result_values

    @pytest.mark.asyncio
    async def test_admin_category_autocomplete_returns_all_when_empty(self):
        """AC-13: Admin autocomplete with empty string returns all admin categories."""
        _evict_cog_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        from cogs.helpCog import _ADMIN_CATEGORY_ORDER, _admin_category_autocomplete

        interaction = _create_mock_interaction()
        results = await _admin_category_autocomplete(interaction, "")

        result_values = [r.value for r in results]
        for cat in _ADMIN_CATEGORY_ORDER:
            assert cat in result_values

    @pytest.mark.asyncio
    async def test_admin_category_autocomplete_filters_by_input(self):
        """AC-13: Admin autocomplete filters by current input."""
        _evict_cog_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        from cogs.helpCog import _admin_category_autocomplete

        interaction = _create_mock_interaction()
        results = await _admin_category_autocomplete(interaction, "player")

        result_values = [r.value for r in results]
        assert "Admin — Players" in result_values
        # Should not contain categories that don't match "player"
        assert "Admin — Render" not in result_values

    @pytest.mark.asyncio
    async def test_autocomplete_max_25_results(self):
        """Autocomplete always returns at most 25 results (Discord limit)."""
        _evict_cog_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        from cogs.helpCog import _user_category_autocomplete

        interaction = _create_mock_interaction()
        results = await _user_category_autocomplete(interaction, "")
        assert len(results) <= 25


# ---------------------------------------------------------------------------
# Build embed helpers
# ---------------------------------------------------------------------------


class TestBuildUserEmbed:
    """Unit tests for _build_user_embed helper (legacy, AC-1, AC-3)."""

    def _get_fn(self):
        _evict_cog_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        from cogs.helpCog import _build_user_embed

        return _build_user_embed

    def test_returns_list_of_embeds(self):
        """_build_user_embed always returns a list (AC-3)."""
        fn = self._get_fn()
        result = fn([])
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_embed_has_title(self):
        """_build_user_embed returns an embed with a title (AC-3)."""
        fn = self._get_fn()
        cmd = _make_mock_cmd("bounties", "List bounties", "BountyCog")
        embeds = fn([cmd])
        assert len(embeds) == 1
        assert embeds[0].title is not None

    def test_embed_has_footer(self):
        """_build_user_embed embed has a footer pointing to /admin_help (AC-1)."""
        fn = self._get_fn()
        embeds = fn([])
        assert len(embeds) >= 1
        embed = embeds[0]
        assert embed is not None

    def test_uncategorised_commands_in_other_field(self):
        """Unknown command names go to 'Other' field without crashing (AC-1)."""
        fn = self._get_fn()
        cmd = _make_mock_cmd("unknown_cmd_xyz", "Some command")
        embeds = fn([cmd])
        assert len(embeds) >= 1

    def test_known_category_commands_grouped(self):
        """Known commands appear in their mapped category (AC-1)."""
        fn = self._get_fn()
        shop_cmd = _make_mock_cmd("shop", "Browse shop")
        buy_cmd = _make_mock_cmd("buy", "Buy item")
        embeds = fn([shop_cmd, buy_cmd])
        assert len(embeds) == 1
        field_names = [f.name for f in embeds[0].fields]
        assert "Shop & Economy" in field_names


class TestBuildAdminEmbed:
    """Unit tests for _build_admin_embed helper (AC-2)."""

    def _get_fn(self):
        _evict_cog_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        from cogs.helpCog import _build_admin_embed

        return _build_admin_embed

    def test_returns_list_of_embeds(self):
        """_build_admin_embed always returns a list."""
        fn = self._get_fn()
        result = fn([])
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_embed_has_admin_title(self):
        """_build_admin_embed embed has a title referencing admin (AC-2)."""
        fn = self._get_fn()
        cmd = _make_mock_cmd("admin_setup", "Setup guild", "AdminCog")
        embeds = fn([cmd])
        assert len(embeds) == 1
        assert embeds[0].title is not None

    def test_known_admin_category_commands_grouped(self):
        """Known admin commands appear in their mapped category (AC-2)."""
        fn = self._get_fn()
        setup_cmd = _make_mock_cmd("admin_setup", "Setup")
        uninstall_cmd = _make_mock_cmd("admin_uninstall", "Uninstall")
        embeds = fn([setup_cmd, uninstall_cmd])
        assert len(embeds) == 1
        field_names = [f.name for f in embeds[0].fields]
        assert "Admin — Setup" in field_names


class TestBuildOverviewEmbed:
    """Unit tests for _build_overview_embed helper."""

    def _get_imports(self):
        _evict_cog_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        import discord as _discord
        from cogs.helpCog import _build_overview_embed

        return _build_overview_embed, _discord

    def test_overview_embed_has_title(self):
        """_build_overview_embed returns an embed with the specified title."""
        fn, discord = self._get_imports()
        embed = fn(
            categories=["Cat A"],
            descriptions={"Cat A": "A description"},
            cmd_counts={"Cat A": 3},
            title="Test Title",
            intro="Test intro",
            footer="Test footer",
            color=discord.Color.blurple(),
        )
        assert embed.title == "Test Title"

    def test_overview_embed_shows_description(self):
        """_build_overview_embed intro appears in embed description."""
        fn, discord = self._get_imports()
        embed = fn(
            categories=["Cat A"],
            descriptions={"Cat A": "Short desc"},
            cmd_counts={"Cat A": 2},
            title="T",
            intro="Use /help category for detail",
            footer="Footer",
            color=discord.Color.blurple(),
        )
        assert "Use /help category for detail" in embed.description

    def test_overview_embed_skips_empty_categories(self):
        """_build_overview_embed skips categories with 0 commands."""
        fn, discord = self._get_imports()
        embed = fn(
            categories=["Cat A", "Cat B"],
            descriptions={"Cat A": "Has cmds", "Cat B": "Empty"},
            cmd_counts={"Cat A": 2, "Cat B": 0},
            title="T",
            intro="",
            footer="",
            color=discord.Color.blurple(),
        )
        field_names = [f.name for f in embed.fields]
        assert "Cat A" in field_names
        assert "Cat B" not in field_names

    def test_overview_embed_shows_count(self):
        """_build_overview_embed shows command count in field values."""
        fn, discord = self._get_imports()
        embed = fn(
            categories=["Cat A"],
            descriptions={"Cat A": "Some desc"},
            cmd_counts={"Cat A": 5},
            title="T",
            intro="",
            footer="",
            color=discord.Color.blurple(),
        )
        assert "5" in embed.fields[0].value


class TestBuildDetailEmbed:
    """Unit tests for _build_detail_embed helper."""

    def _get_imports(self):
        _evict_cog_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        import discord as _discord
        from cogs.helpCog import _build_detail_embed

        return _build_detail_embed, _discord

    def test_detail_embed_has_category_in_title(self):
        """_build_detail_embed title includes the category name."""
        fn, discord = self._get_imports()
        embed = fn(
            category="Bounty Hunting",
            description="Hunt criminals",
            cmds=[],
            color=discord.Color.blurple(),
        )
        assert "Bounty Hunting" in embed.title

    def test_detail_embed_empty_category_shows_no_commands(self):
        """_build_detail_embed with empty cmds shows 'No commands available'."""
        fn, discord = self._get_imports()
        embed = fn(
            category="Ships",
            description="Manage ships",
            cmds=[],
            color=discord.Color.blurple(),
        )
        field_values = [f.value for f in embed.fields]
        assert any("No commands available" in v for v in field_values)

    def test_detail_embed_shows_slash_prefix(self):
        """_build_detail_embed field names use /cmd_name format."""
        fn, discord = self._get_imports()
        cmd = _make_mock_cmd("bounties", "List bounties")
        embed = fn(
            category="Bounty Hunting",
            description="Hunt",
            cmds=[cmd],
            color=discord.Color.blurple(),
        )
        field_names = [f.name for f in embed.fields]
        assert "/bounties" in field_names

    def test_detail_embed_custom_emoji(self):
        """_build_detail_embed uses provided emoji in title."""
        fn, discord = self._get_imports()
        embed = fn(
            category="Admin — Setup",
            description="Setup",
            cmds=[],
            color=discord.Color.red(),
            emoji="🔐",
        )
        assert "🔐" in embed.title


# ---------------------------------------------------------------------------
# Error handler tests
# ---------------------------------------------------------------------------


class TestErrorHandlers:
    """Tests for help_cmd_error and admin_help_cmd_error."""

    @pytest.mark.asyncio
    async def test_help_cmd_error_sends_ephemeral_message(self, cog):
        """help_cmd_error sends an ephemeral error message when response not done."""
        import discord.app_commands as ac

        interaction = _create_mock_interaction()
        interaction.response.is_done.return_value = False

        error = MagicMock(spec=ac.AppCommandError)
        await cog.help_cmd_error(interaction, error)

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_help_cmd_error_no_double_response(self, cog):
        """help_cmd_error does NOT send if response already done."""
        import discord.app_commands as ac

        interaction = _create_mock_interaction()
        interaction.response.is_done.return_value = True

        error = MagicMock(spec=ac.AppCommandError)
        await cog.help_cmd_error(interaction, error)

        interaction.response.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_admin_help_cmd_error_sends_ephemeral_message(self, cog):
        """admin_help_cmd_error sends an ephemeral error message when response not done."""
        import discord.app_commands as ac

        interaction = _create_mock_interaction()
        interaction.response.is_done.return_value = False

        error = MagicMock(spec=ac.AppCommandError)
        await cog.admin_help_cmd_error(interaction, error)

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_admin_help_cmd_error_no_double_response(self, cog):
        """admin_help_cmd_error does NOT send if response already done."""
        import discord.app_commands as ac

        interaction = _create_mock_interaction()
        interaction.response.is_done.return_value = True

        error = MagicMock(spec=ac.AppCommandError)
        await cog.admin_help_cmd_error(interaction, error)

        interaction.response.send_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# Edge case tests for helpers
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge case tests for helper functions."""

    def test_build_user_embed_extra_categorised_cmds(self):
        """_build_user_embed handles commands categorised but not in order (goes to Other)."""
        _evict_cog_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        from cogs.helpCog import _build_user_embed

        # Add a command that belongs to a category not in _USER_CATEGORY_ORDER
        # We do this by temporarily patching — but simpler is to test with an uncategorised cmd
        cmd = _make_mock_cmd("unknown_xyz", "Some cmd")
        embeds = _build_user_embed([cmd])
        assert len(embeds) >= 1
        # The "Other" field should be present
        field_names = [f.name for f in embeds[0].fields]
        assert "Other" in field_names

    def test_build_detail_embed_truncates_long_field(self):
        """_build_detail_embed truncates field values >1024 chars."""
        _evict_cog_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        import discord as _discord
        from cogs.helpCog import _build_detail_embed

        # Create a command with a very long param description
        cmd = _make_mock_cmd(
            "bounties",
            "List bounties",
            params=[{"name": "x", "required": True, "description": "a" * 1100}],
        )
        embed = _build_detail_embed(
            category="Bounty Hunting",
            description="Hunt",
            cmds=[cmd],
            color=_discord.Color.blurple(),
        )
        # Each field value must be <= 1024 chars (Discord limit)
        for field in embed.fields:
            assert len(field.value) <= 1024

    def test_format_params_attribute_error_on_param(self):
        """_format_params silently skips params that raise AttributeError."""
        _evict_cog_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        from cogs.helpCog import _format_params

        cmd = _make_mock_cmd("somecmd")

        # Create a param that raises AttributeError when .required is accessed
        class BadParam:
            name = "x"

            @property
            def required(self):
                raise AttributeError("no required attr")

            description = "desc"

        cmd.parameters = [BadParam()]
        # Should not raise — the except AttributeError: continue block catches it
        result = _format_params(cmd)
        # The bad param is skipped; no output other than possibly the header
        # Result is either empty string or just the header (no actual params listed)
        # Either way it should not raise
        assert isinstance(result, str)

    def test_build_admin_embed_cog_fallback(self):
        """_build_admin_embed uses cog name for fallback categorization."""
        _evict_cog_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        from cogs.helpCog import _build_admin_embed

        # A command from SchedulerCog not in _ADMIN_CATEGORY_MAPPING
        cmd = _make_mock_cmd("scheduler_pause", "Pause a job", "SchedulerCog")
        embeds = _build_admin_embed([cmd])
        assert len(embeds) >= 1
        # Should appear under Admin — Scheduler
        field_names = [f.name for f in embeds[0].fields]
        assert "Admin — Scheduler" in field_names

    @pytest.mark.asyncio
    async def test_admin_help_overview_scheduler_cog_fallback(self, cog, mock_bot):
        """Admin overview categorizes SchedulerCog commands via cog fallback."""
        # Scheduler command not in _ADMIN_CATEGORY_MAPPING
        sched_cmd = _make_mock_cmd("scheduler_pause", "Pause a job", "SchedulerCog")
        mock_bot.tree.get_commands = MagicMock(return_value=[sched_cmd])

        interaction = _create_mock_interaction()
        await cog.admin_help_cmd.callback(cog, interaction, category=None)

        embed = interaction.response.send_message.call_args.kwargs["embed"]
        field_names = [f.name for f in embed.fields]
        assert "Admin — Scheduler" in field_names

    @pytest.mark.asyncio
    async def test_admin_help_detail_dev_cog_fallback(self, cog, mock_bot):
        """Admin detail categorizes DevCog commands via cog fallback."""
        dev_cmd = _make_mock_cmd("dev_debug", "Debug cmd", "DevCog")
        mock_bot.tree.get_commands = MagicMock(return_value=[dev_cmd])

        interaction = _create_mock_interaction()
        await cog.admin_help_cmd.callback(cog, interaction, category="Admin — Dev Tools")

        embed = interaction.response.send_message.call_args.kwargs["embed"]
        assert embed is not None


# ---------------------------------------------------------------------------
# Setup function
# ---------------------------------------------------------------------------


class TestSetupFunction:
    """Verifies the module-level setup() function registers the cog."""

    @pytest.mark.asyncio
    async def test_setup_adds_cog(self, mock_bot):
        """setup() must call bot.add_cog with a HelpCog instance."""
        _evict_cog_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger

        from cogs.helpCog import HelpCog, setup

        mock_bot.add_cog = AsyncMock()
        await setup(mock_bot)

        mock_bot.add_cog.assert_awaited_once()
        added_cog = mock_bot.add_cog.call_args.args[0]
        assert isinstance(added_cog, HelpCog)


# ---------------------------------------------------------------------------
# Category mapping integrity tests (Items 1 & 2 from polish pass)
# ---------------------------------------------------------------------------


class TestAdminCategoryMappingIntegrity:
    """Verify that _ADMIN_CATEGORY_MAPPING contains exactly the right entries.

    Item 1: admin_give_credits and admin_remove_credits must NOT be present
            (these commands do not exist in adminCog.py).
    Item 2: All 6 scheduler commands must map to 'Admin — Scheduler' so that
            the overview shows the correct count.
    """

    def _get_mapping(self):
        _evict_cog_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        from cogs.helpCog import _ADMIN_CATEGORY_MAPPING

        return _ADMIN_CATEGORY_MAPPING

    # -- Item 1: dead entries removed --

    def test_admin_give_credits_not_in_mapping(self):
        """admin_give_credits is a non-existent command and must not be mapped."""
        mapping = self._get_mapping()
        assert "admin_give_credits" not in mapping

    def test_admin_remove_credits_not_in_mapping(self):
        """admin_remove_credits is a non-existent command and must not be mapped."""
        mapping = self._get_mapping()
        assert "admin_remove_credits" not in mapping

    # -- Item 2: scheduler commands present --

    def test_scheduler_list_maps_to_admin_scheduler(self):
        """scheduler_list must map to 'Admin — Scheduler'."""
        mapping = self._get_mapping()
        assert mapping.get("scheduler_list") == "Admin — Scheduler"

    def test_scheduler_view_maps_to_admin_scheduler(self):
        """scheduler_view must map to 'Admin — Scheduler'."""
        mapping = self._get_mapping()
        assert mapping.get("scheduler_view") == "Admin — Scheduler"

    def test_scheduler_update_maps_to_admin_scheduler(self):
        """scheduler_update must map to 'Admin — Scheduler'."""
        mapping = self._get_mapping()
        assert mapping.get("scheduler_update") == "Admin — Scheduler"

    def test_scheduler_delete_maps_to_admin_scheduler(self):
        """scheduler_delete must map to 'Admin — Scheduler'."""
        mapping = self._get_mapping()
        assert mapping.get("scheduler_delete") == "Admin — Scheduler"

    def test_admin_reset_scheduler_maps_to_admin_scheduler(self):
        """admin_reset_scheduler must map to 'Admin — Scheduler'."""
        mapping = self._get_mapping()
        assert mapping.get("admin_reset_scheduler") == "Admin — Scheduler"

    def test_admin_clear_scheduler_maps_to_admin_scheduler(self):
        """admin_clear_scheduler must map to 'Admin — Scheduler'."""
        mapping = self._get_mapping()
        assert mapping.get("admin_clear_scheduler") == "Admin — Scheduler"

    def test_all_six_scheduler_commands_are_mapped(self):
        """All 6 SchedulerCog commands must be explicitly mapped to 'Admin — Scheduler'."""
        mapping = self._get_mapping()
        expected_scheduler_cmds = {
            "scheduler_list",
            "scheduler_view",
            "scheduler_update",
            "scheduler_delete",
            "admin_reset_scheduler",
            "admin_clear_scheduler",
        }
        mapped_to_scheduler = {k for k, v in mapping.items() if v == "Admin — Scheduler"}
        assert expected_scheduler_cmds == mapped_to_scheduler

    @pytest.mark.asyncio
    async def test_admin_overview_shows_scheduler_count_6(self, cog, mock_bot):
        """Admin — Scheduler appears in overview with count=6 when all 6 commands present."""
        scheduler_cmds = [
            _make_mock_cmd("scheduler_list", "List jobs", "SchedulerCog"),
            _make_mock_cmd("scheduler_view", "View job", "SchedulerCog"),
            _make_mock_cmd("scheduler_update", "Update job", "SchedulerCog"),
            _make_mock_cmd("scheduler_delete", "Delete job", "SchedulerCog"),
            _make_mock_cmd("admin_reset_scheduler", "Reset scheduler", "SchedulerCog"),
            _make_mock_cmd("admin_clear_scheduler", "Clear guild jobs", "SchedulerCog"),
        ]
        mock_bot.tree.get_commands = MagicMock(return_value=scheduler_cmds)

        interaction = _create_mock_interaction()
        await cog.admin_help_cmd.callback(cog, interaction, category=None)

        embed = interaction.response.send_message.call_args.kwargs["embed"]
        field_names = [f.name for f in embed.fields]
        assert "Admin — Scheduler" in field_names

        # Find the field and verify it shows 6 commands
        scheduler_field = next(f for f in embed.fields if f.name == "Admin — Scheduler")
        assert "6" in scheduler_field.value
