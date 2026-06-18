"""
Tests for /admin_config_constants criminal-loadout balance knobs.

Covers the 8 criminal-loadout fields added to AdminCog._GAME_CONSTANT_FIELDS so they
are slash-settable (dict / bool / scalar) and surface in constants_autocomplete.

Value correctness is enforced server-side by the bot-core config schema; the gateway
only parses json_value and forwards {setting: parsed_value} to the config API. These
tests assert that forwarding behaviour (with a mocked http_client), matching how the
pre-existing dict field division_max_tl already flows.
"""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

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

# The 8 criminal-loadout knobs newly exposed through the slash command.
_NEW_CRIMINAL_FIELDS = (
    "long_range_threshold_m",
    "criminal_long_range_pct",
    "primary_tl_band_weights",
    "criminal_cloak_chance_by_division",
    "criminal_booster_chance_by_division",
    "criminal_emergency_chance_by_division",
    "criminal_weaponmod_chance_by_division",
    "criminal_exclude_emp_weapons",
)

# Full slash-settable surface after the change (33 fields; demotion_credit_penalty_pct
# stays API-only). Keep this in lock-step with AdminCog._GAME_CONSTANT_FIELDS.
_EXPECTED_SLASH_FIELD_COUNT = 33


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
    # Built-in Discord Administrator → _check_is_admin returns True without an API call.
    interaction.user.guild_permissions.administrator = True
    return interaction


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


def _put_call_payload(mock_admin_cog):
    """Return the JSON body of the single http_client.put call."""
    mock_admin_cog.http_client.put.assert_called_once()
    return mock_admin_cog.http_client.put.call_args.kwargs["json"]


class TestCriminalLoadoutFieldExposure:
    """The 8 knobs must be registered + autocompletable, and the slash surface must be 33."""

    def test_all_new_fields_in_game_constant_fields(self, mock_admin_cog):
        for field in _NEW_CRIMINAL_FIELDS:
            assert field in mock_admin_cog._GAME_CONSTANT_FIELDS, f"{field} missing from _GAME_CONSTANT_FIELDS"

    def test_slash_field_count_is_33(self, mock_admin_cog):
        # Locks the slash-settable surface: 25 prior + 8 criminal-loadout = 33.
        # demotion_credit_penalty_pct stays API-only and must NOT appear here.
        assert len(mock_admin_cog._GAME_CONSTANT_FIELDS) == _EXPECTED_SLASH_FIELD_COUNT
        assert "demotion_credit_penalty_pct" not in mock_admin_cog._GAME_CONSTANT_FIELDS

    def test_new_fields_surface_in_autocomplete(self, mock_admin_cog):
        interaction = _create_mock_interaction()
        for field in _NEW_CRIMINAL_FIELDS:
            choices = asyncio.run(mock_admin_cog.constants_autocomplete(interaction, field))
            values = {c.value for c in choices}
            assert field in values, f"{field} not surfaced by constants_autocomplete"


class TestCriminalLoadoutSetForwarding:
    """A slash set must forward {setting: parsed_value} to the bot-core config API."""

    def _run_set(self, mock_admin_cog, *, setting, int_value=None, float_value=None, json_value=None):
        interaction = _create_mock_interaction()
        put_resp = MagicMock()
        put_resp.raise_for_status = MagicMock()
        put_resp.json.return_value = {"guild_id": interaction.guild_id}
        mock_admin_cog.http_client.put = AsyncMock(return_value=put_resp)
        asyncio.run(
            mock_admin_cog.admin_config_constants.callback(
                mock_admin_cog,
                interaction,
                setting=setting,
                int_value=int_value,
                float_value=float_value,
                json_value=json_value,
            )
        )
        return interaction

    def test_set_dict_field_via_json_value(self, mock_admin_cog):
        payload = '{"bronze": 0, "silver": 25, "gold": 66, "platinum": 100}'
        self._run_set(mock_admin_cog, setting="criminal_cloak_chance_by_division", json_value=payload)
        body = _put_call_payload(mock_admin_cog)
        assert body["criminal_cloak_chance_by_division"] == {
            "bronze": 0,
            "silver": 25,
            "gold": 66,
            "platinum": 100,
        }

    def test_set_bool_field_true_via_json_value(self, mock_admin_cog):
        self._run_set(mock_admin_cog, setting="criminal_exclude_emp_weapons", json_value="true")
        body = _put_call_payload(mock_admin_cog)
        assert body["criminal_exclude_emp_weapons"] is True

    def test_set_bool_field_false_via_json_value(self, mock_admin_cog):
        self._run_set(mock_admin_cog, setting="criminal_exclude_emp_weapons", json_value="false")
        body = _put_call_payload(mock_admin_cog)
        assert body["criminal_exclude_emp_weapons"] is False

    def test_set_scalar_field_via_int_value(self, mock_admin_cog):
        self._run_set(mock_admin_cog, setting="long_range_threshold_m", int_value=3000)
        body = _put_call_payload(mock_admin_cog)
        assert body["long_range_threshold_m"] == 3000

    def test_malformed_json_value_is_rejected_without_put(self, mock_admin_cog):
        # Same handling as every existing dict field: parse failure → no API call.
        interaction = _create_mock_interaction()
        mock_admin_cog.http_client.put = AsyncMock()
        asyncio.run(
            mock_admin_cog.admin_config_constants.callback(
                mock_admin_cog,
                interaction,
                setting="criminal_cloak_chance_by_division",
                json_value="{not valid json",
            )
        )
        mock_admin_cog.http_client.put.assert_not_called()
        interaction.followup.send.assert_called()
