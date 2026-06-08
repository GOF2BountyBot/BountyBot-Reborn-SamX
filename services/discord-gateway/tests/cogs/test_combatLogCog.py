"""Tests for combatLogCog — /combat-log command + autocomplete.

Covers:
  - autocomplete: filters to invoker's fights, returns up to 25 choices
  - autocomplete: returns [] on API error
  - autocomplete: empty when guild_id is None
  - /combat-log: renders detail embed on 200
  - /combat-log: 404 from API → user-friendly message
  - /combat-log: required 'battle' param (int) enforced
  - choice labels: ordinal disambiguation in label text

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

_mock_utils = DiscordMockUtils()

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
    from cogs.combatLogCog import CombatLogCog

    c = CombatLogCog(mock_bot)
    c.http_client = MagicMock()
    c.http_client.aclose = AsyncMock()
    return c


# ---------------------------------------------------------------------------
# Tests: autocomplete
# ---------------------------------------------------------------------------


class TestBattleAutocomplete:
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
        """Key Events embed field must not exceed Discord's 1024-char per-field limit.

        Generates 40 events with long actor/weapon names (~90 chars per line) to
        exercise the truncation path; asserts field value ≤ 1024 chars and that
        the truncation indicator appears when some events were clipped.
        """
        long_detail = (
            "VeryLongActorNameThatIsQuiteExcessive fires "
            "AnExtremelyLongWeaponNameForTestPurposes at "
            "AnotherExtremelyLongTargetShipName dealing 9999 dmg"
        )  # ~113 chars

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
        key_field = next((f for f in embed_call.fields if "Key Events" in f.name), None)
        assert key_field is not None, "Key Events field must be present"
        assert len(key_field.value) <= 1024, (
            f"Key Events field value length {len(key_field.value)} exceeds Discord's 1024-char limit"
        )
        # Truncation indicator must appear when events were clipped
        assert "more event" in key_field.value, (
            "Expected '…(+N more events)' truncation indicator in clipped Key Events field"
        )

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
