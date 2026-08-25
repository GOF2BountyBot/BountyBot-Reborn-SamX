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

# The 19 PvC loot tunable knobs newly exposed through the slash command (T2).
_NEW_LOOT_FIELDS = (
    "loot_chance_tractor_t1",
    "loot_chance_tractor_t2",
    "loot_chance_tractor_t3",
    "loot_chance_tractor_t4",
    "loot_chance_no_tractor",
    "loot_band1_select_pct",
    "loot_band2_select_pct",
    "loot_band3_select_pct",
    "loot_band1_tl_window",
    "loot_band1_qty_min",
    "loot_band1_qty_max",
    "loot_band1_qty_mode",
    "loot_band2_qty_min",
    "loot_band2_qty_max",
    "loot_band2_qty_mode",
    "loot_band3_qty_min",
    "loot_band3_qty_max",
    "loot_band3_qty_mode",
    "loot_commodity_sell_fraction",
)

# Full slash-settable surface (82 fields; _GAME_CONSTANT_FIELDS == _OVERRIDE_FIELDS).
# 52 prior + 27 added in D-trivial batch (issue #70, revision 0028):
#   4 previously API-only (min_route_systems, recently_spotted_max_window,
#     demotion_credit_penalty_pct, shop_combat_module_prob)
#   + 1 criminal_secondary_min_damage
#   + 2 shop_secondary_qty_scaler_{heavy,standard}
#   + 8 shop_tl_band_{lo,hi}_{bronze,silver,gold,platinum}
#   + 3 shop_{banded_tl_weight,uptier_tl_decay,downtier_tl_decay}
#   + 4 division_tl_center_{bronze,silver,gold,platinum}
#   + 5 orphans (bounty_{single,dual}_waypoint_prob, bounty_waypoint_{attempts,min_degree},
#               pvc_damage_reduction)
# + 3 added in Unit C batch (issue #70, revision 0029):
#   bronze_combat_bonus_{base_mult,per_prestige,cap}
# + 27 added in JSONB flatten (issue #70, revision 0030):
#   division_max_tl_{bronze,silver,gold,platinum}                       (4)
#   bounty_division_reward_mult_{bronze,silver,gold,platinum}           (4)
#   primary_tl_band_weight_{center,minus1,plus1}                       (3)
#   criminal_{cloak,booster,emergency,weaponmod}_chance_{bronze,...}   (16)
# Keep this in lock-step with AdminCog._GAME_CONSTANT_FIELDS.
# Rev 0031: 14 fields retired → 109 - 14 = 95
_EXPECTED_SLASH_FIELD_COUNT = 95


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


_API_BASE = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")


def _with_real_client(cog, request):
    """Replace cog.http_client with a real httpx.AsyncClient for respx interception."""
    cog.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    request.addfinalizer(lambda: asyncio.run(cog.http_client.aclose()))
    return cog


def _json_body(route):
    """Return the decoded JSON body of a matched respx route's single call."""
    import json as _json

    assert route.called, "expected route to have been called"
    return _json.loads(route.calls.last.request.content)


class TestCriminalLoadoutFieldExposure:
    """The 8 knobs must be registered + autocompletable, and the slash surface must be 53."""

    def test_all_new_fields_in_game_constant_fields(self, mock_admin_cog):
        for field in _NEW_CRIMINAL_FIELDS:
            assert field in mock_admin_cog._GAME_CONSTANT_FIELDS, f"{field} missing from _GAME_CONSTANT_FIELDS"

    def test_all_new_loot_fields_in_game_constant_fields(self, mock_admin_cog):
        for field in _NEW_LOOT_FIELDS:
            assert field in mock_admin_cog._GAME_CONSTANT_FIELDS, f"{field} missing from _GAME_CONSTANT_FIELDS"

    def test_slash_field_count_is_95(self, mock_admin_cog):
        # Locks the slash-settable surface (see _EXPECTED_SLASH_FIELD_COUNT above).
        # _GAME_CONSTANT_FIELDS now matches _OVERRIDE_FIELDS exactly (both 95 after rev 0031).
        # demotion_credit_penalty_pct IS now slash-settable (added in issue #70 batch).
        # kaamo_max_capacity was retired in issue #70.
        # bronze_combat_bonus_{base_mult,per_prestige,cap} added in Unit C (revision 0029).
        # 14 fields retired in rev 0031 (shop_default_*, activity/temperature, bounty timing).
        assert len(mock_admin_cog._GAME_CONSTANT_FIELDS) == _EXPECTED_SLASH_FIELD_COUNT
        assert "demotion_credit_penalty_pct" in mock_admin_cog._GAME_CONSTANT_FIELDS
        assert "kaamo_max_capacity" not in mock_admin_cog._GAME_CONSTANT_FIELDS
        assert "bronze_combat_bonus_base_mult" in mock_admin_cog._GAME_CONSTANT_FIELDS
        assert "bronze_combat_bonus_per_prestige" in mock_admin_cog._GAME_CONSTANT_FIELDS
        assert "bronze_combat_bonus_cap" in mock_admin_cog._GAME_CONSTANT_FIELDS
        # Rev 0031 retired fields must NOT appear
        for retired in (
            "shop_default_ships_num",
            "shop_default_weapons_num",
            "shop_default_modules_num",
            "shop_default_turrets_num",
            "turret_spawn_probability",
            "guild_activity_decay_rate",
            "min_guild_activity",
            "activity_temp_per_player",
            "bounty_delay_random_min",
            "bounty_delay_random_max",
            "bounty_spawn_jitter",
            "duel_cloak_chance",
            "ship_value_reward_percentage",
            "criminal_equip_damageless_weapon_chance",
        ):
            assert retired not in mock_admin_cog._GAME_CONSTANT_FIELDS, (
                f"{retired} should be RETIRED from _GAME_CONSTANT_FIELDS (rev 0031)"
            )

    def test_new_fields_surface_in_autocomplete(self, mock_admin_cog):
        # Uses the new setting_autocomplete (metadata-driven; falls back to _GAME_CONSTANT_FIELDS
        # in tests since no metadata is preloaded). Values must equal the bare field name.
        interaction = _create_mock_interaction()
        for field in _NEW_CRIMINAL_FIELDS:
            choices = asyncio.run(mock_admin_cog.setting_autocomplete(interaction, field))
            values = {c.value for c in choices}
            assert field in values, f"{field} not surfaced by setting_autocomplete"


class TestCriminalLoadoutSetForwarding:
    """A slash set must forward {setting: parsed_value} to the bot-core config API.

    Converted from admin_config_constants → /admin_config action:Set (issue #70 Option A).
    The forwarding assertions and respx pinning are unchanged; only the callback path changed.

    Note: the old-value GET (/config/guild/{id}/game-constants) is made inside
    _admin_config_do_set (best-effort, try/except). In the test respx context it raises
    ConnectError which is caught silently — old_value is None, which is acceptable.
    """

    def _run_set(
        self,
        mock_admin_cog,
        request,
        *,
        setting,
        int_value=None,
        float_value=None,
        bool_value=None,
        json_value=None,
    ):
        """Run /admin_config action:Set under respx and return the matched PUT route."""
        _with_real_client(mock_admin_cog, request)
        interaction = _create_mock_interaction()
        with respx.mock(assert_all_called=True) as mock_router:
            route = mock_router.put(f"{_API_BASE}/config/guild/{interaction.guild_id}").mock(
                return_value=httpx.Response(200, json={"guild_id": interaction.guild_id})
            )
            asyncio.run(
                mock_admin_cog.admin_config.callback(
                    mock_admin_cog,
                    interaction,
                    action="set",
                    setting=setting,
                    int_value=int_value,
                    float_value=float_value,
                    bool_value=bool_value,
                    text_value=None,
                    json_value=json_value,
                    only_overridden=True,
                )
            )
        return route

    def test_set_dict_field_via_json_value(self, mock_admin_cog, request):
        payload = '{"bronze": 0, "silver": 25, "gold": 66, "platinum": 100}'
        route = self._run_set(mock_admin_cog, request, setting="criminal_cloak_chance_by_division", json_value=payload)
        body = _json_body(route)
        assert body["criminal_cloak_chance_by_division"] == {
            "bronze": 0,
            "silver": 25,
            "gold": 66,
            "platinum": 100,
        }

    def test_set_bool_field_via_bool_value_true(self, mock_admin_cog, request):
        # criminal_exclude_emp_weapons is now set via bool_value (not json_value) in the new command.
        route = self._run_set(mock_admin_cog, request, setting="criminal_exclude_emp_weapons", bool_value=True)
        body = _json_body(route)
        assert body["criminal_exclude_emp_weapons"] is True

    def test_set_bool_field_via_bool_value_false(self, mock_admin_cog, request):
        route = self._run_set(mock_admin_cog, request, setting="criminal_exclude_emp_weapons", bool_value=False)
        body = _json_body(route)
        assert body["criminal_exclude_emp_weapons"] is False

    def test_set_bool_field_json_value_rejected(self, mock_admin_cog, request):
        # json_value is NOT accepted for bool fields — the new command returns an error embed.
        _with_real_client(mock_admin_cog, request)
        interaction = _create_mock_interaction()
        with respx.mock(assert_all_called=False) as mock_router:
            route = mock_router.put(f"{_API_BASE}/config/guild/{interaction.guild_id}").mock(
                return_value=httpx.Response(200, json={"guild_id": interaction.guild_id})
            )
            asyncio.run(
                mock_admin_cog.admin_config.callback(
                    mock_admin_cog,
                    interaction,
                    action="set",
                    setting="criminal_exclude_emp_weapons",
                    int_value=None,
                    float_value=None,
                    bool_value=None,
                    text_value=None,
                    json_value="true",
                    only_overridden=True,
                )
            )
        # PUT must NOT be called — bool fields reject json_value
        assert not route.called
        interaction.followup.send.assert_called()

    def test_set_scalar_field_via_int_value(self, mock_admin_cog, request):
        route = self._run_set(mock_admin_cog, request, setting="long_range_threshold_m", int_value=3000)
        body = _json_body(route)
        assert body["long_range_threshold_m"] == 3000

    def test_set_float_field_via_float_value(self, mock_admin_cog, request):
        # Exercises the float_value branch: a bare float must forward as-is.
        route = self._run_set(mock_admin_cog, request, setting="criminal_long_range_pct", float_value=0.65)
        body = _json_body(route)
        assert body == {"guild_id": 987654321, "criminal_long_range_pct": 0.65}

    def test_set_band_weights_field_via_json_value(self, mock_admin_cog, request):
        # Distinct dict structure from the *_chance_by_division fields.
        payload = '{"center": 70, "minus1": 20, "plus1": 10}'
        route = self._run_set(mock_admin_cog, request, setting="primary_tl_band_weights", json_value=payload)
        body = _json_body(route)
        assert body == {
            "guild_id": 987654321,
            "primary_tl_band_weights": {"center": 70, "minus1": 20, "plus1": 10},
        }

    def test_malformed_json_value_is_rejected_without_put(self, mock_admin_cog, request):
        # Same handling as every existing dict field: parse failure → no API call.
        _with_real_client(mock_admin_cog, request)
        interaction = _create_mock_interaction()
        with respx.mock(assert_all_called=False) as mock_router:
            route = mock_router.put(f"{_API_BASE}/config/guild/{interaction.guild_id}").mock(
                return_value=httpx.Response(200, json={"guild_id": interaction.guild_id})
            )
            asyncio.run(
                mock_admin_cog.admin_config.callback(
                    mock_admin_cog,
                    interaction,
                    action="set",
                    setting="criminal_cloak_chance_by_division",
                    int_value=None,
                    float_value=None,
                    bool_value=None,
                    text_value=None,
                    json_value="{not valid json",
                    only_overridden=True,
                )
            )
        assert not route.called
        interaction.followup.send.assert_called()


# ---------------------------------------------------------------------------
# issue #70 canonical JSON guard
# ---------------------------------------------------------------------------


class TestCanonicalJsonGuard:
    """Assert _GAME_CONSTANT_FIELDS matches the canonical override_fields.json.

    The JSON lives in services/bot-core/tests/data/ (bot-core can read it directly;
    gateway reads it via the repo-root-relative path, since the gateway suite runs
    with the repo root mounted at /app).
    """

    @staticmethod
    def _locate_json():
        """Return path to override_fields.json, or None if not present."""
        from pathlib import Path

        # The test file is at:
        #   services/discord-gateway/tests/cogs/test_adminCog_config_constants.py
        # parents[0] = cogs/, parents[1] = tests/, parents[2] = discord-gateway/,
        # parents[3] = services/, parents[4] = repo root
        repo_root = Path(__file__).resolve().parents[4]
        candidate = repo_root / "services" / "bot-core" / "tests" / "data" / "override_fields.json"
        return candidate if candidate.exists() else None

    def test_game_constant_fields_match_canonical_json(self, mock_admin_cog):
        """_GAME_CONSTANT_FIELDS set must equal the canonical JSON fields set."""
        import json as _json

        json_path = self._locate_json()
        if json_path is None:
            pytest.skip(
                "Canonical override_fields.json not found at "
                "services/bot-core/tests/data/override_fields.json "
                "(file is created by the bot-core test suite; run with repo root mounted)."
            )

        payload = _json.loads(json_path.read_text(encoding="utf-8"))
        json_fields: set[str] = set(payload["fields"])
        runtime_fields: set[str] = set(mock_admin_cog._GAME_CONSTANT_FIELDS)

        in_json_only = json_fields - runtime_fields
        in_runtime_only = runtime_fields - json_fields

        assert not in_json_only and not in_runtime_only, (
            f"_GAME_CONSTANT_FIELDS diverges from override_fields.json. "
            f"In JSON only (should be added to _GAME_CONSTANT_FIELDS): {in_json_only!r}. "
            f"In runtime only (should be added to override_fields.json or removed): {in_runtime_only!r}."
        )
