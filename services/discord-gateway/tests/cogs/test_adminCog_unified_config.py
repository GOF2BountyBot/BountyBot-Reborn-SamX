"""
Tests for the new unified /admin_config command (issue #70 Option A).

Covers:
- setting_autocomplete: metadata-backed path + static fallback + deprecated suffix/sort
- action:validate  — forwards to GET /validate, renders valid/invalid embed
- action:view      — only_overridden=True compact view
- action:help      — per-setting help embed + usage overview when no setting given
- action:set       — typed-param exclusivity; type-mismatch; bounds pre-check; json_value
                     rejected for non-dict field; happy paths for int/float/bool
- action:reset     — single-field reset; all-fields reset (both via ConfirmView stub);
                     starting_credits / sale_price_factor are blocked with a clear message
- /admin_config_shop extension: quantity_ranges and tech_level_probabilities forwarding

Pattern notes (respx):
  Always use ``with respx.mock() as router: route = router.method(url).mock(...)``.
  Inspect body INSIDE the with block after asyncio.run() (routes are cleared on exit).
  Use ``_json_body(route)`` which reads ``route.calls.last.request.content``.
"""

import asyncio
import json
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

# -------------------------------------------------------------------------
# Bootstrap: mock shared.bblogger before any cog imports
# -------------------------------------------------------------------------

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_args, **_kwargs):
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    return logger


def _close_coro(coro):
    coro.close()
    return MagicMock()


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from tests.mocks.discord_mock_utils import DiscordMockUtils

_API_BASE = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")

# ---------------------------------------------------------------------------
# Sample metadata (subset of real 97-field schema)
# ---------------------------------------------------------------------------

_SAMPLE_METADATA = [
    {
        "field": "close_bounty_threshold",
        "type": "int",
        "ge": 1,
        "le": 50,
        "default": 4,
        "description": "How many systems away a criminal must be before players see a 'close' hint.",
        "category": "Bounty Routing",
        "deprecated": False,
        "replaced_by": None,
    },
    {
        "field": "criminal_long_range_pct",
        "type": "float",
        "ge": 0.0,
        "le": 1.0,
        "default": 0.5,
        "description": "Fraction of criminal weapons that are long-range.",
        "category": "Criminal Loadout",
        "deprecated": False,
        "replaced_by": None,
    },
    {
        "field": "criminal_exclude_emp_weapons",
        "type": "bool",
        "ge": None,
        "le": None,
        "default": False,
        "description": "Exclude EMP weapons from criminal loadouts.",
        "category": "Criminal Loadout",
        "deprecated": False,
        "replaced_by": None,
    },
    {
        "field": "division_max_tl",
        "type": "dict",
        "ge": None,
        "le": None,
        "default": None,
        "description": "Max TL per division (legacy dict — use scalar scalars instead).",
        "category": "Division",
        "deprecated": True,
        "replaced_by": "division_max_tl_bronze / _silver / _gold / _platinum",
    },
    {
        "field": "starting_credits",
        "type": "int",
        "ge": 0,
        "le": None,
        "default": 0,
        "description": "Starting credits for new players.",
        "category": "Core",
        "deprecated": False,
        "replaced_by": None,
    },
]


def _evict_discord_modules():
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


def _create_mock_interaction(guild_id: int = 987654321):
    interaction = DiscordMockUtils.create_mock_interaction()
    interaction.guild_id = guild_id
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.guild.name = "TestGuild"
    interaction.user.guild_permissions.administrator = True
    return interaction


def _json_body(route):
    """Extract the JSON request body from a respx route's last call."""
    assert route.called, "expected route to have been called"
    return json.loads(route.calls.last.request.content)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mock_bot():
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    bot.loop = MagicMock()
    bot.loop.create_task = MagicMock(side_effect=_close_coro)
    return bot


@pytest.fixture(scope="module")
def mock_admin_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.adminCog import AdminCog

    return AdminCog(mock_bot)


@pytest.fixture()
def cog_with_metadata(mock_admin_cog):
    """Function-scoped: populate _config_metadata for one test, then restore empty."""
    mock_admin_cog._config_metadata = _SAMPLE_METADATA
    mock_admin_cog._config_metadata_by_field = {m["field"]: m for m in _SAMPLE_METADATA}
    yield mock_admin_cog
    mock_admin_cog._config_metadata = []
    mock_admin_cog._config_metadata_by_field = {}


def _with_real_client(cog):
    """Give the cog a fresh real httpx.AsyncClient (intercepted by respx.mock)."""
    cog.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))


def _run_admin_config(
    cog,
    interaction,
    *,
    action,
    setting=None,
    int_value=None,
    float_value=None,
    bool_value=None,
    text_value=None,
    json_value=None,
    only_overridden=True,
):
    """Run /admin_config.callback synchronously via asyncio.run()."""
    asyncio.run(
        cog.admin_config.callback(
            cog,
            interaction,
            action=action,
            setting=setting,
            int_value=int_value,
            float_value=float_value,
            bool_value=bool_value,
            text_value=text_value,
            json_value=json_value,
            only_overridden=only_overridden,
        )
    )


# ---------------------------------------------------------------------------
# Autocomplete tests
# ---------------------------------------------------------------------------


class TestSettingAutocomplete:
    """setting_autocomplete uses metadata when loaded; falls back to _GAME_CONSTANT_FIELDS."""

    def test_fallback_to_static_list_when_no_metadata(self, mock_admin_cog):
        """With empty _config_metadata, autocomplete returns _GAME_CONSTANT_FIELDS entries."""
        assert not mock_admin_cog._config_metadata, "expected empty metadata for this fixture"
        interaction = _create_mock_interaction()
        choices = asyncio.run(mock_admin_cog.setting_autocomplete(interaction, "bounty"))
        values = {c.value for c in choices}
        assert any("bounty" in v for v in values)

    def test_metadata_path_surfaces_all_fields(self, cog_with_metadata):
        interaction = _create_mock_interaction()
        choices = asyncio.run(cog_with_metadata.setting_autocomplete(interaction, ""))
        values = {c.value for c in choices}
        assert "close_bounty_threshold" in values
        assert "criminal_long_range_pct" in values
        assert "starting_credits" in values  # metadata-only, not in _GAME_CONSTANT_FIELDS

    def test_deprecated_fields_sort_last(self, cog_with_metadata):
        interaction = _create_mock_interaction()
        choices = asyncio.run(cog_with_metadata.setting_autocomplete(interaction, ""))
        names = [c.name for c in choices]
        non_dep_indices = [i for i, n in enumerate(names) if "(deprecated)" not in n]
        dep_indices = [i for i, n in enumerate(names) if "(deprecated)" in n]
        if non_dep_indices and dep_indices:
            assert max(non_dep_indices) < min(dep_indices), "deprecated fields must sort after non-deprecated fields"

    def test_deprecated_field_name_has_suffix_value_is_bare(self, cog_with_metadata):
        interaction = _create_mock_interaction()
        choices = asyncio.run(cog_with_metadata.setting_autocomplete(interaction, "division_max_tl"))
        dep = next((c for c in choices if "deprecated" in c.name), None)
        assert dep is not None, "expected a deprecated choice for division_max_tl"
        assert "(deprecated)" in dep.name
        assert dep.value == "division_max_tl"  # bare field name, no suffix

    def test_substring_filter_applies(self, cog_with_metadata):
        interaction = _create_mock_interaction()
        choices = asyncio.run(cog_with_metadata.setting_autocomplete(interaction, "criminal_long"))
        values = {c.value for c in choices}
        assert "criminal_long_range_pct" in values
        assert "close_bounty_threshold" not in values

    def test_max_25_returned(self, mock_admin_cog):
        interaction = _create_mock_interaction()
        choices = asyncio.run(mock_admin_cog.setting_autocomplete(interaction, ""))
        assert len(choices) <= 25


# ---------------------------------------------------------------------------
# action:validate
# ---------------------------------------------------------------------------


class TestAdminConfigValidate:
    def test_validate_valid_config(self, mock_admin_cog):
        _with_real_client(mock_admin_cog)
        interaction = _create_mock_interaction()
        with respx.mock() as router:
            router.get(f"{_API_BASE}/config/guild/{interaction.guild_id}/validate").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "valid": True,
                        "errors": [],
                        "warnings": [],
                        "guild_id": interaction.guild_id,
                    },
                )
            )
            _run_admin_config(mock_admin_cog, interaction, action="validate")
        interaction.followup.send.assert_called()
        embed = interaction.followup.send.call_args.kwargs.get("embed")
        assert embed is not None
        assert "Valid" in embed.title

    def test_validate_invalid_config(self, mock_admin_cog):
        _with_real_client(mock_admin_cog)
        interaction = _create_mock_interaction()
        with respx.mock() as router:
            router.get(f"{_API_BASE}/config/guild/{interaction.guild_id}/validate").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "valid": False,
                        "errors": ["shop channel not set"],
                        "warnings": [],
                        "guild_id": interaction.guild_id,
                    },
                )
            )
            _run_admin_config(mock_admin_cog, interaction, action="validate")
        embed = interaction.followup.send.call_args.kwargs.get("embed")
        assert embed is not None
        assert "Invalid" in embed.title


# ---------------------------------------------------------------------------
# action:view
# ---------------------------------------------------------------------------


class TestAdminConfigView:
    def test_view_only_overridden_true_no_overrides(self, mock_admin_cog):
        _with_real_client(mock_admin_cog)
        interaction = _create_mock_interaction()
        with respx.mock() as router:
            router.get(f"{_API_BASE}/config/guild/{interaction.guild_id}/game-constants").mock(
                return_value=httpx.Response(200, json={})
            )
            _run_admin_config(mock_admin_cog, interaction, action="view", only_overridden=True)
        embed = interaction.followup.send.call_args.kwargs.get("embed")
        assert embed is not None
        assert "Overrides" in embed.title

    def test_view_only_overridden_true_with_overrides(self, cog_with_metadata):
        _with_real_client(cog_with_metadata)
        interaction = _create_mock_interaction()
        with respx.mock() as router:
            router.get(f"{_API_BASE}/config/guild/{interaction.guild_id}/game-constants").mock(
                return_value=httpx.Response(200, json={"close_bounty_threshold": 8})
            )
            _run_admin_config(cog_with_metadata, interaction, action="view", only_overridden=True)
        embed = interaction.followup.send.call_args.kwargs.get("embed")
        assert embed is not None
        assert "close_bounty_threshold" in (embed.description or "")
        assert "8" in (embed.description or "")


# ---------------------------------------------------------------------------
# action:help
# ---------------------------------------------------------------------------


class TestAdminConfigHelp:
    def test_help_without_setting_shows_usage(self, mock_admin_cog):
        _with_real_client(mock_admin_cog)
        interaction = _create_mock_interaction()
        _run_admin_config(mock_admin_cog, interaction, action="help", setting=None)
        embed = interaction.followup.send.call_args.kwargs.get("embed")
        assert embed is not None
        assert "Help" in embed.title

    def test_help_with_setting_uses_metadata(self, cog_with_metadata):
        _with_real_client(cog_with_metadata)
        interaction = _create_mock_interaction()
        with respx.mock() as router:
            router.get(f"{_API_BASE}/config/guild/{interaction.guild_id}/game-constants").mock(
                return_value=httpx.Response(200, json={"close_bounty_threshold": 6})
            )
            _run_admin_config(cog_with_metadata, interaction, action="help", setting="close_bounty_threshold")
        embed = interaction.followup.send.call_args.kwargs.get("embed")
        assert embed is not None
        assert "close_bounty_threshold" in embed.title
        field_names = {f.name for f in embed.fields}
        assert "Type" in field_names
        assert "Range" in field_names

    def test_help_deprecated_field_shows_note(self, cog_with_metadata):
        _with_real_client(cog_with_metadata)
        interaction = _create_mock_interaction()
        with respx.mock() as router:
            router.get(f"{_API_BASE}/config/guild/{interaction.guild_id}/game-constants").mock(
                return_value=httpx.Response(200, json={})
            )
            _run_admin_config(cog_with_metadata, interaction, action="help", setting="division_max_tl")
        embed = interaction.followup.send.call_args.kwargs.get("embed")
        assert embed is not None
        assert "deprecated" in embed.title.lower()
        field_names = {f.name for f in embed.fields}
        assert "Deprecation" in field_names


# ---------------------------------------------------------------------------
# action:set
# ---------------------------------------------------------------------------


class TestAdminConfigSet:
    def test_set_no_value_param_returns_error(self, mock_admin_cog):
        _with_real_client(mock_admin_cog)
        interaction = _create_mock_interaction()
        with respx.mock(assert_all_called=False) as router:
            route = router.put(f"{_API_BASE}/config/guild/{interaction.guild_id}").mock(
                return_value=httpx.Response(200, json={"guild_id": interaction.guild_id})
            )
            _run_admin_config(mock_admin_cog, interaction, action="set", setting="close_bounty_threshold")
            assert not route.called, "PUT must not be called when no value param is given"
        interaction.followup.send.assert_called()
        msg = interaction.followup.send.call_args
        content = msg.args[0] if msg.args else msg.kwargs.get("content", "")
        assert "❌" in content

    def test_set_multiple_value_params_returns_error(self, mock_admin_cog):
        _with_real_client(mock_admin_cog)
        interaction = _create_mock_interaction()
        with respx.mock(assert_all_called=False) as router:
            route = router.put(f"{_API_BASE}/config/guild/{interaction.guild_id}").mock(
                return_value=httpx.Response(200, json={"guild_id": interaction.guild_id})
            )
            _run_admin_config(
                mock_admin_cog,
                interaction,
                action="set",
                setting="close_bounty_threshold",
                int_value=5,
                float_value=5.0,
            )
            assert not route.called, "PUT must not be called when multiple value params given"
        interaction.followup.send.assert_called()
        msg = interaction.followup.send.call_args
        content = msg.args[0] if msg.args else msg.kwargs.get("content", "")
        assert "❌" in content

    def test_set_int_field_happy_path(self, mock_admin_cog):
        _with_real_client(mock_admin_cog)
        interaction = _create_mock_interaction()
        with respx.mock(assert_all_called=True) as router:
            route = router.put(f"{_API_BASE}/config/guild/{interaction.guild_id}").mock(
                return_value=httpx.Response(200, json={"guild_id": interaction.guild_id})
            )
            _run_admin_config(mock_admin_cog, interaction, action="set", setting="close_bounty_threshold", int_value=8)
            # Check body while still inside the context (routes cleared on exit)
            body = _json_body(route)
            assert body["close_bounty_threshold"] == 8

    def test_set_bool_field_happy_path(self, mock_admin_cog):
        _with_real_client(mock_admin_cog)
        interaction = _create_mock_interaction()
        with respx.mock(assert_all_called=True) as router:
            route = router.put(f"{_API_BASE}/config/guild/{interaction.guild_id}").mock(
                return_value=httpx.Response(200, json={"guild_id": interaction.guild_id})
            )
            _run_admin_config(
                mock_admin_cog, interaction, action="set", setting="criminal_exclude_emp_weapons", bool_value=True
            )
            body = _json_body(route)
            assert body["criminal_exclude_emp_weapons"] is True

    def test_set_bounds_precheck_blocks_http_call(self, cog_with_metadata):
        """When metadata is loaded, out-of-range value is rejected before any HTTP call."""
        _with_real_client(cog_with_metadata)
        interaction = _create_mock_interaction()
        with respx.mock(assert_all_called=False) as router:
            route = router.put(f"{_API_BASE}/config/guild/{interaction.guild_id}").mock(
                return_value=httpx.Response(200, json={"guild_id": interaction.guild_id})
            )
            # close_bounty_threshold has ge=1, le=50; 999 is out of range
            _run_admin_config(
                cog_with_metadata, interaction, action="set", setting="close_bounty_threshold", int_value=999
            )
            assert not route.called, "PUT should NOT be called for out-of-range value"
        interaction.followup.send.assert_called()
        msg = interaction.followup.send.call_args
        content = msg.args[0] if msg.args else msg.kwargs.get("content", "")
        assert "❌" in content
        assert "between" in content.lower()

    def test_set_json_value_rejected_for_non_dict_field(self, cog_with_metadata):
        """json_value must be rejected for non-dict fields (e.g. bool field)."""
        _with_real_client(cog_with_metadata)
        interaction = _create_mock_interaction()
        with respx.mock(assert_all_called=False) as router:
            route = router.put(f"{_API_BASE}/config/guild/{interaction.guild_id}").mock(
                return_value=httpx.Response(200, json={"guild_id": interaction.guild_id})
            )
            _run_admin_config(
                cog_with_metadata, interaction, action="set", setting="criminal_exclude_emp_weapons", json_value="true"
            )
            assert not route.called
        interaction.followup.send.assert_called()
        msg = interaction.followup.send.call_args
        content = msg.args[0] if msg.args else msg.kwargs.get("content", "")
        assert "❌" in content

    def test_set_json_value_accepted_for_dict_field(self, mock_admin_cog):
        """json_value IS accepted for the 7 deprecated dict fields."""
        _with_real_client(mock_admin_cog)
        interaction = _create_mock_interaction()
        payload = '{"bronze": 5, "silver": 7, "gold": 9, "platinum": 10}'
        with respx.mock(assert_all_called=True) as router:
            route = router.put(f"{_API_BASE}/config/guild/{interaction.guild_id}").mock(
                return_value=httpx.Response(200, json={"guild_id": interaction.guild_id})
            )
            _run_admin_config(mock_admin_cog, interaction, action="set", setting="division_max_tl", json_value=payload)
            body = _json_body(route)
            assert body["division_max_tl"] == {"bronze": 5, "silver": 7, "gold": 9, "platinum": 10}

    def test_set_missing_setting_returns_error(self, mock_admin_cog):
        _with_real_client(mock_admin_cog)
        interaction = _create_mock_interaction()
        _run_admin_config(mock_admin_cog, interaction, action="set", setting=None)
        msg = interaction.followup.send.call_args
        content = msg.args[0] if msg.args else msg.kwargs.get("content", "")
        assert "❌" in content

    def test_set_type_mismatch_blocked(self, cog_with_metadata):
        """Using float_value for a metadata-typed int field is rejected."""
        _with_real_client(cog_with_metadata)
        interaction = _create_mock_interaction()
        with respx.mock(assert_all_called=False) as router:
            route = router.put(f"{_API_BASE}/config/guild/{interaction.guild_id}").mock(
                return_value=httpx.Response(200, json={"guild_id": interaction.guild_id})
            )
            _run_admin_config(
                cog_with_metadata, interaction, action="set", setting="close_bounty_threshold", float_value=5.0
            )
            assert not route.called
        interaction.followup.send.assert_called()


# ---------------------------------------------------------------------------
# action:reset
# ---------------------------------------------------------------------------


class TestAdminConfigReset:
    """Reset tests use a ConfirmView stub that auto-resolves."""

    @staticmethod
    def _stub_confirm_view(result):
        """
        Replace cogs.adminCog.ConfirmView with an auto-resolving stub.

        ``result`` is the value set on ``view.result``; ``wait()`` returns immediately.
        The patch targets the module-global name used by _admin_config_do_reset.
        """
        import cogs.adminCog as _admin_module
        import discord

        _result = result  # capture in closure

        class _AutoView(discord.ui.View):
            def __init__(self, *args, **kwargs):
                super().__init__(timeout=0)
                self.result = _result

            async def wait(self):
                return

        _admin_module.ConfirmView = _AutoView

    def test_reset_non_resettable_field_blocked(self, mock_admin_cog):
        """starting_credits cannot be reset via game-constants endpoint."""
        _with_real_client(mock_admin_cog)
        interaction = _create_mock_interaction()
        _run_admin_config(mock_admin_cog, interaction, action="reset", setting="starting_credits")
        msg = interaction.followup.send.call_args
        content = msg.args[0] if msg.args else msg.kwargs.get("content", "")
        assert "❌" in content

    def test_reset_sale_price_factor_blocked(self, mock_admin_cog):
        _with_real_client(mock_admin_cog)
        interaction = _create_mock_interaction()
        _run_admin_config(mock_admin_cog, interaction, action="reset", setting="sale_price_factor")
        msg = interaction.followup.send.call_args
        content = msg.args[0] if msg.args else msg.kwargs.get("content", "")
        assert "❌" in content

    def test_reset_single_field_confirmed(self, mock_admin_cog):
        _with_real_client(mock_admin_cog)
        self._stub_confirm_view(True)
        interaction = _create_mock_interaction()
        with respx.mock(assert_all_called=True) as router:
            route = router.post(f"{_API_BASE}/config/guild/{interaction.guild_id}/game-constants/reset").mock(
                return_value=httpx.Response(200, json={"reset": True})
            )
            _run_admin_config(mock_admin_cog, interaction, action="reset", setting="close_bounty_threshold")
            # Check body inside the context while route data is still available
            body = _json_body(route)
            assert body["fields"] == ["close_bounty_threshold"]

    def test_reset_all_confirmed(self, mock_admin_cog):
        _with_real_client(mock_admin_cog)
        self._stub_confirm_view(True)
        interaction = _create_mock_interaction()
        with respx.mock(assert_all_called=True) as router:
            route = router.post(f"{_API_BASE}/config/guild/{interaction.guild_id}/game-constants/reset").mock(
                return_value=httpx.Response(200, json={"reset": True})
            )
            _run_admin_config(mock_admin_cog, interaction, action="reset", setting=None)
            body = _json_body(route)
            assert body["fields"] is None  # None = all fields

    def test_reset_cancelled(self, mock_admin_cog):
        _with_real_client(mock_admin_cog)
        self._stub_confirm_view(False)
        interaction = _create_mock_interaction()
        with respx.mock(assert_all_called=False) as router:
            route = router.post(f"{_API_BASE}/config/guild/{interaction.guild_id}/game-constants/reset").mock(
                return_value=httpx.Response(200, json={"reset": True})
            )
            _run_admin_config(mock_admin_cog, interaction, action="reset", setting="close_bounty_threshold")
            # POST must NOT be called when user cancels
            assert not route.called, "POST must not be called when user cancels"


# ---------------------------------------------------------------------------
# /admin_config_shop extension: quantity_ranges + tech_level_probabilities
# ---------------------------------------------------------------------------


class TestAdminConfigShopExtension:
    """quantity_ranges and tech_level_probabilities are now wired into /admin_config_shop."""

    def _run_shop(self, cog, interaction, **kwargs):
        """Run admin_config_shop.callback with all params, defaults to None unless overridden."""
        defaults = dict(
            ship_count_min=None,
            ship_count_max=None,
            weapon_count_min=None,
            weapon_count_max=None,
            secondary_weapon_count_min=None,
            secondary_weapon_count_max=None,
            module_count_min=None,
            module_count_max=None,
            turret_count_min=None,
            turret_count_max=None,
            sale_factor=None,
            ship_qty_min=None,
            ship_qty_max=None,
            weapon_qty_min=None,
            weapon_qty_max=None,
            secondary_weapon_qty_min=None,
            secondary_weapon_qty_max=None,
            module_qty_min=None,
            module_qty_max=None,
            turret_qty_min=None,
            turret_qty_max=None,
            tl_prob_same_level=None,
            tl_prob_one_lower=None,
            tl_prob_two_lower=None,
        )
        defaults.update(kwargs)
        asyncio.run(cog.admin_config_shop.callback(cog, interaction, **defaults))

    def test_shop_quantity_ranges_forwarded(self, mock_admin_cog):
        _with_real_client(mock_admin_cog)
        interaction = _create_mock_interaction()
        shop_response = {
            "guild_id": interaction.guild_id,
            "shop_config": {
                "item_count_ranges": {},
                "quantity_ranges": {"ships": {"min": 2, "max": 5}},
                "tech_level_probabilities": {},
            },
        }
        with respx.mock(assert_all_called=True) as router:
            route = router.put(f"{_API_BASE}/config/guild/{interaction.guild_id}/shop").mock(
                return_value=httpx.Response(200, json=shop_response)
            )
            self._run_shop(mock_admin_cog, interaction, ship_qty_min=2, ship_qty_max=5)
            body = _json_body(route)
            assert "quantity_ranges" in body
            assert body["quantity_ranges"]["ships"] == {"min": 2, "max": 5}

    def test_shop_tech_level_probabilities_forwarded(self, mock_admin_cog):
        _with_real_client(mock_admin_cog)
        interaction = _create_mock_interaction()
        shop_response = {
            "guild_id": interaction.guild_id,
            "shop_config": {
                "item_count_ranges": {},
                "quantity_ranges": {},
                "tech_level_probabilities": {"same_level": 0.7, "one_lower": 0.2, "two_lower": 0.1},
            },
        }
        with respx.mock(assert_all_called=True) as router:
            route = router.put(f"{_API_BASE}/config/guild/{interaction.guild_id}/shop").mock(
                return_value=httpx.Response(200, json=shop_response)
            )
            self._run_shop(
                mock_admin_cog, interaction, tl_prob_same_level=0.7, tl_prob_one_lower=0.2, tl_prob_two_lower=0.1
            )
            body = _json_body(route)
            assert "tech_level_probabilities" in body
            assert body["tech_level_probabilities"] == {"same_level": 0.7, "one_lower": 0.2, "two_lower": 0.1}

    def test_shop_partial_qty_ranges_not_forwarded(self, mock_admin_cog):
        """Only BOTH min+max creates a range entry; partial pair is excluded."""
        _with_real_client(mock_admin_cog)
        interaction = _create_mock_interaction()
        shop_response = {
            "guild_id": interaction.guild_id,
            "shop_config": {"item_count_ranges": {}, "quantity_ranges": {}, "tech_level_probabilities": {}},
        }
        with respx.mock(assert_all_called=True) as router:
            route = router.put(f"{_API_BASE}/config/guild/{interaction.guild_id}/shop").mock(
                return_value=httpx.Response(200, json=shop_response)
            )
            # ship_qty_min=2 but ship_qty_max=None → ships must NOT appear in quantity_ranges
            self._run_shop(mock_admin_cog, interaction, ship_qty_min=2)
            body = _json_body(route)
            assert "quantity_ranges" not in body
