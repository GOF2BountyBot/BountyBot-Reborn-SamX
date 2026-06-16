"""Tests for combatLogCog — /combat-log and /admin_combat_log commands + autocomplete.

Covers:
  - autocomplete: filters to invoker's fights, returns up to 25 choices
  - autocomplete: returns [] on API error
  - autocomplete: empty when guild_id is None
  - /combat-log: renders detail embed on 200
  - /combat-log: 404 from API → user-friendly message
  - /combat-log: required 'battle' param (int) enforced
  - choice labels: ordinal disambiguation in label text
  - admin autocomplete: lists the SELECTED user's fights (namespace.user)
  - admin autocomplete: "Select a user first" sentinel when user unfilled
  - /admin_combat_log: admin gate, sentinel rejection, target-POV detail fetch

Max 2 mocks per test.
"""

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Module-level mock setup — must run before any src imports
# ---------------------------------------------------------------------------
from tests.mocks.discord_mock_utils import DiscordMockUtils

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")
_mock_logger = MagicMock()
for _level in ("info", "debug", "warning", "error", "trace", "critical", "exception"):
    setattr(_mock_logger, _level, MagicMock())
_mock_bblogger.get_logger = MagicMock(return_value=_mock_logger)
_mock_shared.bblogger = _mock_bblogger

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _evict_cog_modules():
    to_evict = [k for k in sys.modules if k.startswith("cogs.") or k == "cogs"]
    for k in to_evict:
        sys.modules.pop(k, None)


def _create_interaction(user_id: int = 402296276617527306, guild_id: int = 699744305274945650):
    interaction = DiscordMockUtils.create_mock_interaction(user_id=user_id, guild_id=guild_id)
    interaction.guild_id = guild_id
    interaction.user.display_name = "SamX"
    interaction.user.mention = f"<@{user_id}>"
    return interaction


def _make_list_item(
    row_id: int = 1,
    context: str = "duel",
    opponent_name: str = "General_Failure",
    outcome: str = "won",
    ordinal: int = 1,
    combatant1_name: str | None = None,
    combatant2_name: str | None = None,
) -> dict:
    item: dict = {
        "id": row_id,
        "guild_id": 699744305274945650,
        "context": context,
        "opponent_name": opponent_name,
        "outcome": outcome,
        "created_at": "2026-06-03T12:00:00+00:00",
        "ordinal": ordinal,
    }
    if combatant1_name is not None:
        item["combatant1_name"] = combatant1_name
    if combatant2_name is not None:
        item["combatant2_name"] = combatant2_name
    return item


def _make_detail(
    row_id: int = 1,
    outcome: str = "won",
    pvc_dr: float = 0.0,
) -> dict:
    return {
        "id": row_id,
        "guild_id": 699744305274945650,
        "context": "duel",
        "combatant1_name": "Betty",
        "combatant2_name": "Betty",
        "combatant1_user_id": 402296276617527306,
        "combatant2_user_id": 970691862035841048,
        "winner_name": "Betty",
        "is_stalemate": False,
        "created_at": "2026-06-03T12:00:00+00:00",
        "outcome": outcome,
        "combatant1": {
            "name": "Betty",
            "ship": "Betty",
            "start_hp": {"hull": 95, "armour": 40, "shield": 0},
            "final_hp": {"hull": 95, "armour": 40, "shield": 0},
            "shots_fired": 60,
            "shots_hit": 40,
            "accuracy": 40 / 60,
            "damage_dealt": 120,
            "damage_taken": 80,
        },
        "combatant2": {
            "name": "Betty",
            "ship": "Betty",
            "start_hp": {"hull": 95, "armour": 40, "shield": 0},
            "final_hp": {"hull": 0, "armour": 0, "shield": 0},
            "shots_fired": 55,
            "shots_hit": 35,
            "accuracy": 35 / 55,
            "damage_dealt": 80,
            "damage_taken": 120,
        },
        "duration_ticks": 3488,
        "duration_s": 34.88,
        "pvc_damage_reduction": pvc_dr,
        "key_events": [
            {
                "tick": 1767,
                "time_s": 17.67,
                "actor": "Betty",
                "event_type": "Armour depleted",
                "detail": "Betty: Armour depleted",
            }
        ],
    }


def _make_mock_response(json_data, status_code: int = 200):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data
    resp.status_code = status_code
    return resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mock_bot():
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="BountyBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    return bot


@pytest.fixture(scope="module")
def cog(mock_bot):
    _evict_cog_modules()
    import cogs.combatLogCog as clc

    c = clc.CombatLogCog(mock_bot)
    c.http_client = MagicMock()
    c.http_client.aclose = AsyncMock()
    return c


# ---------------------------------------------------------------------------
# Tests: autocomplete
# ---------------------------------------------------------------------------


class TestBattleAutocomplete:
    @pytest.fixture(autouse=True)
    def _clear_cache(self, cog):
        # The cog fixture is module-scoped, so the per-user _combatlog_cache persists
        # across tests. Clear it before each test so the cold-fill path actually
        # fetches that test's freshly-mocked http_client.get data instead of a stale
        # cached list from a prior test (the new cache is the point of these tests).
        cog._combatlog_cache.clear()
        yield

    async def test_returns_choices_for_invoker(self, cog):
        items = [_make_list_item(row_id=1, opponent_name="General_Failure")]
        cog.http_client.get = AsyncMock(return_value=_make_mock_response(items))
        interaction = _create_interaction()

        choices = await cog.battle_autocomplete(interaction, current="")
        assert len(choices) == 1
        assert choices[0].value == 1
        assert "General_Failure" in choices[0].name
        assert "Duel" in choices[0].name
        assert "WON" in choices[0].name

    async def test_autocomplete_filters_by_current(self, cog):
        items = [
            _make_list_item(row_id=1, opponent_name="General_Failure"),
            _make_list_item(row_id=2, opponent_name="Arch_Enemy"),
        ]
        cog.http_client.get = AsyncMock(return_value=_make_mock_response(items))
        interaction = _create_interaction()

        choices = await cog.battle_autocomplete(interaction, current="General")
        names = [c.name for c in choices]
        assert any("General_Failure" in n for n in names)
        assert not any("Arch_Enemy" in n for n in names)

    async def test_autocomplete_returns_empty_on_api_error(self, cog):
        cog.http_client.get = AsyncMock(side_effect=Exception("connection refused"))
        interaction = _create_interaction()

        choices = await cog.battle_autocomplete(interaction, current="")
        assert choices == []

    async def test_autocomplete_returns_empty_on_non_200(self, cog):
        cog.http_client.get = AsyncMock(return_value=_make_mock_response({}, status_code=500))
        interaction = _create_interaction()

        choices = await cog.battle_autocomplete(interaction, current="")
        assert choices == []

    async def test_autocomplete_returns_empty_when_no_guild(self, cog):
        interaction = _create_interaction()
        interaction.guild_id = None

        choices = await cog.battle_autocomplete(interaction, current="")
        assert choices == []

    async def test_ordinal_in_label(self, cog):
        """Ordinal appears in choice label when > 1 fight with same opponent."""
        items = [
            _make_list_item(row_id=10, opponent_name="Foe", ordinal=2),
            _make_list_item(row_id=11, opponent_name="Foe", ordinal=1),
        ]
        cog.http_client.get = AsyncMock(return_value=_make_mock_response(items))
        interaction = _create_interaction()

        choices = await cog.battle_autocomplete(interaction, current="")
        labels = [c.name for c in choices]
        assert any("#2" in lbl for lbl in labels)
        assert any("#1" in lbl for lbl in labels)

    async def test_capped_at_25_choices(self, cog):
        items = [_make_list_item(row_id=i, opponent_name=f"Foe{i}") for i in range(30)]
        cog.http_client.get = AsyncMock(return_value=_make_mock_response(items))
        interaction = _create_interaction()

        choices = await cog.battle_autocomplete(interaction, current="")
        assert len(choices) <= 25

    async def test_choice_label_full_x_vs_y_format(self, cog):
        """CI-20: when combatant1_name and combatant2_name are both present the label
        uses the full 'SamX vs H'Soc' format rather than the old 'vs <opponent>' fallback.
        """
        item = {
            "id": 42,
            "guild_id": 699744305274945650,
            "context": "duel",
            "opponent_name": "H'Soc",
            "combatant1_name": "SamX",
            "combatant2_name": "H'Soc",
            "outcome": "won",
            "created_at": "2026-06-03T12:00:00+00:00",
            "ordinal": 1,
        }
        cog.http_client.get = AsyncMock(return_value=_make_mock_response([item]))
        interaction = _create_interaction()

        choices = await cog.battle_autocomplete(interaction, current="")
        assert len(choices) == 1
        label = choices[0].name
        # Full format must include both combatant names separated by " vs "
        assert "SamX vs H'Soc" in label, f"Expected 'SamX vs H\\'Soc' in label but got: {label!r}"

    async def test_choice_label_fallback_when_names_absent(self, cog):
        """CI-20: when combatant names are absent (None / missing keys) the label falls back
        to 'vs <opponent_name>' — backward-compat for old rows.
        """
        item = _make_list_item(row_id=99, opponent_name="OldFoe")
        # combatant1_name / combatant2_name intentionally absent (old row simulation)
        assert "combatant1_name" not in item
        cog.http_client.get = AsyncMock(return_value=_make_mock_response([item]))
        interaction = _create_interaction()

        choices = await cog.battle_autocomplete(interaction, current="")
        assert len(choices) == 1
        label = choices[0].name
        assert "vs OldFoe" in label, f"Expected 'vs OldFoe' fallback in label but got: {label!r}"
        # Must NOT contain "SamX vs" or any full-name format
        assert "SamX vs" not in label


# ---------------------------------------------------------------------------
# Tests: /combat-log command
# ---------------------------------------------------------------------------


class TestCombatLogCommand:
    async def test_success_sends_embed(self, cog):
        detail = _make_detail()
        cog.http_client.get = AsyncMock(return_value=_make_mock_response(detail))
        interaction = _create_interaction()

        await cog.combat_log.callback(cog, interaction, battle=1)

        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        # Should have sent an embed
        assert "embed" in call_kwargs.kwargs

    async def test_default_is_ephemeral(self, cog):
        """public omitted → defer and success followup are both ephemeral (current behavior)."""
        cog.http_client.get = AsyncMock(return_value=_make_mock_response(_make_detail()))
        interaction = _create_interaction()

        await cog.combat_log.callback(cog, interaction, battle=1)

        assert interaction.response.defer.call_args.kwargs.get("ephemeral") is True
        assert interaction.followup.send.call_args.kwargs.get("ephemeral") is True

    async def test_public_true_sends_publicly(self, cog):
        """public=True → same embed, but defer and success followup are non-ephemeral."""
        cog.http_client.get = AsyncMock(return_value=_make_mock_response(_make_detail()))
        interaction = _create_interaction()

        await cog.combat_log.callback(cog, interaction, battle=1, public=True)

        assert interaction.response.defer.call_args.kwargs.get("ephemeral") is False
        call_kwargs = interaction.followup.send.call_args.kwargs
        assert call_kwargs.get("ephemeral") is False
        assert "embed" in call_kwargs

    async def test_public_true_error_stays_ephemeral(self, cog):
        """Errors are always ephemeral, even when public=True was requested."""
        resp = _make_mock_response({}, status_code=404)
        cog.http_client.get = AsyncMock(return_value=resp)
        interaction = _create_interaction()

        await cog.combat_log.callback(cog, interaction, battle=9999, public=True)

        assert interaction.followup.send.call_args.kwargs.get("ephemeral") is True

    async def test_404_sends_not_found_message(self, cog):
        resp = _make_mock_response({}, status_code=404)
        cog.http_client.get = AsyncMock(return_value=resp)
        interaction = _create_interaction()

        await cog.combat_log.callback(cog, interaction, battle=9999)

        interaction.followup.send.assert_called_once()
        args = interaction.followup.send.call_args
        text = args.args[0] if args.args else args.kwargs.get("content", "")
        assert "not found" in text.lower() or "not a combatant" in text.lower()

    async def test_api_error_sends_warning(self, cog):
        resp = _make_mock_response({}, status_code=500)
        resp.raise_for_status = MagicMock(side_effect=Exception("server error"))
        cog.http_client.get = AsyncMock(return_value=resp)
        interaction = _create_interaction()

        await cog.combat_log.callback(cog, interaction, battle=1)

        interaction.followup.send.assert_called_once()

    async def test_embed_shows_outcome_won(self, cog):
        cog.http_client.get = AsyncMock(return_value=_make_mock_response(_make_detail(outcome="won")))
        interaction = _create_interaction()

        await cog.combat_log.callback(cog, interaction, battle=1)

        embed_call = interaction.followup.send.call_args.kwargs.get("embed")
        assert embed_call is not None
        assert "WON" in embed_call.title or "won" in embed_call.title.lower()

    async def test_embed_shows_pvc_reduction_when_nonzero(self, cog):
        cog.http_client.get = AsyncMock(return_value=_make_mock_response(_make_detail(pvc_dr=0.33)))
        interaction = _create_interaction()

        await cog.combat_log.callback(cog, interaction, battle=1)

        embed_call = interaction.followup.send.call_args.kwargs.get("embed")
        # Find the Summary field value
        summary_field = next((f for f in embed_call.fields if "Summary" in f.name), None)
        assert summary_field is not None
        assert "PvC" in summary_field.value

    async def test_embed_no_pvc_reduction_when_zero(self, cog):
        cog.http_client.get = AsyncMock(return_value=_make_mock_response(_make_detail(pvc_dr=0.0)))
        interaction = _create_interaction()

        await cog.combat_log.callback(cog, interaction, battle=1)

        embed_call = interaction.followup.send.call_args.kwargs.get("embed")
        summary_field = next((f for f in embed_call.fields if "Summary" in f.name), None)
        assert summary_field is not None
        assert "PvC" not in summary_field.value

    async def test_key_events_field_present(self, cog):
        cog.http_client.get = AsyncMock(return_value=_make_mock_response(_make_detail()))
        interaction = _create_interaction()

        await cog.combat_log.callback(cog, interaction, battle=1)

        embed_call = interaction.followup.send.call_args.kwargs.get("embed")
        key_field = next((f for f in embed_call.fields if "Key Events" in f.name), None)
        assert key_field is not None
        assert "Armour depleted" in key_field.value

    async def test_key_events_field_stays_under_discord_limit_many_events(self, cog):
        """Key Events embed fields must not exceed Discord's 1024-char per-field limit.

        NEW behavior (DESIGN_COMBAT_LOG_RECAP §6): rather than truncating with '(+N more)',
        the embed packs lines into the first '🎯 Key Events' field and spills overflow into
        additional HEADERLESS continuation fields (zero-width space name), capped at 6 fields.
        Events that exceed 6 fields get a truncation indicator in the last continuation field.

        Generates 40 events with long names (~90 chars per line after detail truncation).
        40 × ~90 = ~3600 chars ÷ 1024/field = ~4 fields — well within the 6-field cap.
        So all 40 events appear across multiple fields with no truncation indicator.
        """
        long_detail = (
            "VeryLongActorNameThatIsQuiteExcessive fires "
            "AnExtremelyLongWeaponNameForTestPurposes at "
            "AnotherExtremelyLongTargetShipName dealing 9999 dmg"
        )  # ~113 chars (will be truncated to 80 by _build_detail_embed)

        many_events = [
            {"tick": i * 100, "time_s": i * 1.0, "actor": "ActorA", "event_type": "damage", "detail": long_detail}
            for i in range(40)
        ]

        detail_payload = _make_detail()
        detail_payload = {**detail_payload, "key_events": many_events}

        cog.http_client.get = AsyncMock(return_value=_make_mock_response(detail_payload))
        interaction = _create_interaction()

        await cog.combat_log.callback(cog, interaction, battle=1)

        embed_call = interaction.followup.send.call_args.kwargs.get("embed")
        # Must have at least one '🎯 Key Events' named field
        key_field = next((f for f in embed_call.fields if "Key Events" in f.name), None)
        assert key_field is not None, "Key Events field must be present"
        # Every key_events field (primary + continuations) must respect the 1024-char limit
        key_event_fields = [f for f in embed_call.fields if "Key Events" in f.name or f.name == "​"]
        for field in key_event_fields:
            assert len(field.value) <= 1024, (
                f"Key Events field value length {len(field.value)} exceeds Discord's 1024-char limit"
            )
        # The 40 events should span multiple fields (the new spill behavior)
        assert len(key_event_fields) > 1, (
            f"40 long-detail events should spill into continuation fields; got {len(key_event_fields)} field(s)"
        )

    async def test_key_events_overflow_uses_continuation_fields(self, cog):
        """NEW: when events don't fit in one field they spill into HEADERLESS continuation fields.

        This replaces the old single-field '(+N more)' truncation. The continuation
        fields have a zero-width space (\\u200b) as their name so Discord renders them
        without a visible header.
        """
        # 15 medium events (~60 chars each including time prefix) → 15*60 = ~900 chars/field
        # so they need 2 fields
        medium_events = [
            {
                "tick": i * 50,
                "time_s": i * 0.5,
                "actor": "A",
                "event_type": "Event",
                "detail": f"Some moderately long detail string number {i:03d} for field spill test",
            }
            for i in range(25)
        ]
        detail_payload = {**_make_detail(), "key_events": medium_events}
        cog.http_client.get = AsyncMock(return_value=_make_mock_response(detail_payload))
        interaction = _create_interaction()

        await cog.combat_log.callback(cog, interaction, battle=1)

        embed_call = interaction.followup.send.call_args.kwargs.get("embed")
        # Collect all key-events-related fields
        ke_fields = [f for f in embed_call.fields if "Key Events" in f.name or f.name == "​"]
        # Must have spilled into at least one continuation field
        primary = [f for f in ke_fields if "Key Events" in f.name]
        assert len(primary) == 1, f"Exactly one '🎯 Key Events' field expected; got {len(primary)}"
        # All field values must respect the per-field Discord limit
        for field in ke_fields:
            assert len(field.value) <= 1024

    async def test_key_events_no_truncation_indicator_when_all_fit(self, cog):
        """When all events fit within the limit, no truncation indicator is added."""
        short_events = [
            {"tick": i * 100, "time_s": i * 1.0, "actor": "A", "event_type": "damage", "detail": "Short event"}
            for i in range(5)
        ]
        detail_payload = {**_make_detail(), "key_events": short_events}

        cog.http_client.get = AsyncMock(return_value=_make_mock_response(detail_payload))
        interaction = _create_interaction()

        await cog.combat_log.callback(cog, interaction, battle=1)

        embed_call = interaction.followup.send.call_args.kwargs.get("embed")
        key_field = next((f for f in embed_call.fields if "Key Events" in f.name), None)
        assert key_field is not None
        assert len(key_field.value) <= 1024
        assert "more event" not in key_field.value

    async def test_key_events_long_detail_strings_are_truncated(self, cog):
        """Individual detail strings longer than 80 chars are truncated with '…'."""
        very_long_detail = "X" * 200
        events = [{"tick": 1, "time_s": 1.0, "actor": "A", "event_type": "damage", "detail": very_long_detail}]
        detail_payload = {**_make_detail(), "key_events": events}

        cog.http_client.get = AsyncMock(return_value=_make_mock_response(detail_payload))
        interaction = _create_interaction()

        await cog.combat_log.callback(cog, interaction, battle=1)

        embed_call = interaction.followup.send.call_args.kwargs.get("embed")
        key_field = next((f for f in embed_call.fields if "Key Events" in f.name), None)
        assert key_field is not None
        assert len(key_field.value) <= 1024
        # The 200-char detail should have been trimmed
        assert "X" * 200 not in key_field.value
        assert "…" in key_field.value

    async def test_summary_stats_line_shows_shots_secondaries_modules(self, cog):
        """Summary section includes per-combatant stats line with shots fired/hit, secondaries, modules.

        DESIGN_COMBAT_LOG_RECAP §6: the new stats line is
        'Shots: {fired} fired / {hit} hit | Secondaries: {n} | Modules: {n}'.
        """
        detail = _make_detail()
        # Inject secondaries_fired and modules_activated into combatant dicts
        detail["combatant1"] = {
            **detail["combatant1"],
            "secondaries_fired": 3,
            "modules_activated": 1,
        }
        detail["combatant2"] = {
            **detail["combatant2"],
            "secondaries_fired": 0,
            "modules_activated": 2,
        }
        cog.http_client.get = AsyncMock(return_value=_make_mock_response(detail))
        interaction = _create_interaction()

        await cog.combat_log.callback(cog, interaction, battle=1)

        embed_call = interaction.followup.send.call_args.kwargs.get("embed")
        summary_field = next((f for f in embed_call.fields if "Summary" in f.name), None)
        assert summary_field is not None, "Summary field must be present"
        value = summary_field.value
        # Stats line format: "Shots: {fired} fired / {hit} hit | Secondaries: {n} | Modules: {n}"
        assert "Shots:" in value, f"'Shots:' must appear in Summary; got:\n{value}"
        assert "Secondaries:" in value, f"'Secondaries:' must appear in Summary; got:\n{value}"
        assert "Modules:" in value, f"'Modules:' must appear in Summary; got:\n{value}"
        # Combatant 1 specific values
        assert "Secondaries: 3" in value, f"Expected 'Secondaries: 3' for c1; got:\n{value}"
        assert "Modules: 1" in value, f"Expected 'Modules: 1' for c1; got:\n{value}"
        assert "Modules: 2" in value, f"Expected 'Modules: 2' for c2; got:\n{value}"

    async def test_summary_stats_line_zero_values_when_fields_absent(self, cog):
        """Stats line shows 0 for secondaries/modules when fields are absent (old rows)."""
        detail = _make_detail()
        # combatant1/2 have no secondaries_fired or modules_activated keys (legacy row)
        cog.http_client.get = AsyncMock(return_value=_make_mock_response(detail))
        interaction = _create_interaction()

        await cog.combat_log.callback(cog, interaction, battle=1)

        embed_call = interaction.followup.send.call_args.kwargs.get("embed")
        summary_field = next((f for f in embed_call.fields if "Summary" in f.name), None)
        assert summary_field is not None
        value = summary_field.value
        # Should gracefully show 0 for missing fields
        assert "Secondaries: 0" in value, f"Expected 'Secondaries: 0' for legacy row; got:\n{value}"
        assert "Modules: 0" in value, f"Expected 'Modules: 0' for legacy row; got:\n{value}"


# ---------------------------------------------------------------------------
# Tests: /admin_combat_log autocomplete
# ---------------------------------------------------------------------------

_TARGET_USER_ID = 970691862035841048


def _make_target_user(user_id: int = _TARGET_USER_ID):
    user = MagicMock()
    user.id = user_id
    return user


class TestAdminBattleAutocomplete:
    @pytest.fixture(autouse=True)
    def _clear_cache(self, cog):
        cog._combatlog_cache.clear()
        yield

    async def test_returns_choices_for_selected_user(self, cog):
        """Choices come from the SELECTED user's history, keyed on namespace.user.id."""
        items = [_make_list_item(row_id=7, opponent_name="General_Failure")]
        cog.http_client.get = AsyncMock(return_value=_make_mock_response(items))
        interaction = _create_interaction()
        interaction.namespace.user = _make_target_user()

        choices = await cog.admin_battle_autocomplete(interaction, current="")
        assert len(choices) == 1
        assert choices[0].value == 7
        # The listing call must be keyed on the TARGET user's id, not the invoker's
        params = cog.http_client.get.call_args.kwargs.get("params", {})
        assert params.get("user_id") == _TARGET_USER_ID

    async def test_hint_choice_when_user_unfilled(self, cog):
        """Discord cannot enforce fill-order: unfilled user → sentinel hint choice."""
        import cogs.combatLogCog as clc

        interaction = _create_interaction()
        interaction.namespace.user = None

        choices = await cog.admin_battle_autocomplete(interaction, current="")
        assert len(choices) == 1
        assert choices[0].value == clc._SELECT_USER_FIRST
        assert "select a user" in choices[0].name.lower()

    async def test_returns_empty_when_no_guild(self, cog):
        interaction = _create_interaction()
        interaction.guild_id = None

        choices = await cog.admin_battle_autocomplete(interaction, current="")
        assert choices == []

    async def test_returns_empty_on_api_error(self, cog):
        cog.http_client.get = AsyncMock(side_effect=Exception("connection refused"))
        interaction = _create_interaction()
        interaction.namespace.user = _make_target_user()

        choices = await cog.admin_battle_autocomplete(interaction, current="")
        assert choices == []


# ---------------------------------------------------------------------------
# Tests: /admin_combat_log command
# ---------------------------------------------------------------------------


class TestAdminCombatLogCommand:
    @pytest.fixture(autouse=True)
    def _as_admin(self, monkeypatch):
        # Default every test to an admin invoker; deny-path tests override.
        import cogs.combatLogCog as clc

        monkeypatch.setattr(clc, "_check_is_admin", AsyncMock(return_value=True))
        yield

    async def test_non_admin_is_denied(self, cog, monkeypatch):
        import cogs.combatLogCog as clc

        monkeypatch.setattr(clc, "_check_is_admin", AsyncMock(return_value=False))
        cog.http_client.get = AsyncMock()
        interaction = _create_interaction()

        await cog.admin_combat_log.callback(cog, interaction, user=_make_target_user(), battle=1)

        interaction.followup.send.assert_called_once()
        args = interaction.followup.send.call_args
        text = args.args[0] if args.args else args.kwargs.get("content", "")
        assert "admin" in text.lower()
        cog.http_client.get.assert_not_called()

    async def test_sentinel_battle_rejected(self, cog):
        import cogs.combatLogCog as clc

        cog.http_client.get = AsyncMock()
        interaction = _create_interaction()

        await cog.admin_combat_log.callback(cog, interaction, user=_make_target_user(), battle=clc._SELECT_USER_FIRST)

        interaction.followup.send.assert_called_once()
        args = interaction.followup.send.call_args
        text = args.args[0] if args.args else args.kwargs.get("content", "")
        assert "select a user" in text.lower()
        cog.http_client.get.assert_not_called()

    async def test_success_sends_embed_with_target_pov(self, cog):
        """Detail is fetched with the SELECTED user's id so the embed renders
        exactly as if that player had invoked /combat-log themselves."""
        cog.http_client.get = AsyncMock(return_value=_make_mock_response(_make_detail()))
        interaction = _create_interaction()

        await cog.admin_combat_log.callback(cog, interaction, user=_make_target_user(), battle=1)

        params = cog.http_client.get.call_args.kwargs.get("params", {})
        assert params.get("user_id") == _TARGET_USER_ID
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        assert "embed" in call_kwargs.kwargs
        assert call_kwargs.kwargs.get("ephemeral") is True

    async def test_404_sends_not_combatant_message(self, cog):
        """Stale user/battle pair (user swapped after picking a battle) → 404 path."""
        cog.http_client.get = AsyncMock(return_value=_make_mock_response({}, status_code=404))
        interaction = _create_interaction()

        await cog.admin_combat_log.callback(cog, interaction, user=_make_target_user(), battle=9999)

        interaction.followup.send.assert_called_once()
        args = interaction.followup.send.call_args
        text = args.args[0] if args.args else args.kwargs.get("content", "")
        assert "not a combatant" in text.lower() or "not found" in text.lower()

    async def test_api_error_sends_warning(self, cog):
        resp = _make_mock_response({}, status_code=500)
        resp.raise_for_status = MagicMock(side_effect=Exception("server error"))
        cog.http_client.get = AsyncMock(return_value=resp)
        interaction = _create_interaction()

        await cog.admin_combat_log.callback(cog, interaction, user=_make_target_user(), battle=1)

        interaction.followup.send.assert_called_once()
