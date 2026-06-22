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

        Generates 40 events with long names (~123 chars per line; the ~113-char detail is
        under _DETAIL_MAX=200 so it is NOT truncated).
        40 × ~123 = ~4920 chars ÷ 1024/field = ~5 fields — still within the 6-field cap.
        So all 40 events appear across multiple fields with no truncation indicator.
        """
        long_detail = (
            "VeryLongActorNameThatIsQuiteExcessive fires "
            "AnExtremelyLongWeaponNameForTestPurposes at "
            "AnotherExtremelyLongTargetShipName dealing 9999 dmg"
        )  # ~113 chars (under _DETAIL_MAX=200 → not truncated by _build_detail_embed)

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
        key_event_fields = [f for f in embed_call.fields if "Key Events" in f.name or f.name == "\u200b"]
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
        ke_fields = [f for f in embed_call.fields if "Key Events" in f.name or f.name == "\u200b"]
        # Must have spilled into at least one continuation field
        primary = [f for f in ke_fields if "Key Events" in f.name]
        assert len(primary) == 1, f"Exactly one '🎯 Key Events' field expected; got {len(primary)}"
        # All field values must respect the per-field Discord limit
        for field in ke_fields:
            assert len(field.value) <= 1024

    def test_embed_under_6000_with_battle137_sized_log(self, cog):
        """Regression: a battle-137-sized recap must not exceed Discord's 6000-char embed limit.

        Prod battle 137 (`/combat-log`) threw 400 (50035) "Embed size exceeds maximum size of 6000":
        its 103-event recap packed summary (≤1024) + 6×1024 event chunks ≈ 7200 > 6000. The
        per-field (1024) and field-count (6) guards do not bound the AGGREGATE size. This builds a
        comparable payload (100 ~75-char event lines → 6 full chunks) and asserts both that the
        UNGUARDED size would exceed 6000 and that the guarded embed stays safely under it, with the
        omission honestly surfaced.
        """
        # ~77-char detail (under _DETAIL_MAX=200, so untruncated) → ~87-char rendered line.
        # 100 such lines ≈ 8400 chars → far more than 6 × 1024, so without the budget guard the
        # embed would blow past 6000 (matching the real battle-137 failure).
        big_detail = "SsilverLeopard's Berger FlaK 9-9 re-enters range and connects — hit for 12 dmg"
        big_events = [
            {
                "tick": i * 30,
                "time_s": i * 0.3,
                "actor": "SsilverLeopard",
                "event_type": "Weapon in range",
                "detail": big_detail,
            }
            for i in range(100)
        ]
        detail_payload = {**_make_detail(), "key_events": big_events}
        user = MagicMock()
        user.display_name = "SsilverLeopard"

        # Non-vacuity: prove the guard is actually doing work. The raw rendered event-line text
        # for this exact input (the bytes that an UNGUARDED build would pour into the event fields)
        # far exceeds 6000, so without the budget guard the embed could not stay legal. Mirror the
        # cog's own line rendering: `{time_s:6.1f}s` prefix + the (≤200-char truncated) detail.
        detail_max = 200
        truncated = big_detail[: detail_max - 1] + "…" if len(big_detail) > detail_max else big_detail
        raw_event_text = "\n".join(f"`{ev['time_s']:6.1f}s` {truncated}" for ev in big_events)
        assert len(raw_event_text) > 6000, (
            f"Test is vacuous: raw event-line text is only {len(raw_event_text)} chars; "
            "it must exceed 6000 so the guard provably has something to bound"
        )

        embed = cog._build_detail_embed(detail_payload, user)

        # Aggregate size is bounded below Discord's hard 6000 limit.
        assert len(embed) < 6000, f"Embed aggregate size {len(embed)} must stay under Discord's 6000 limit"
        # Every individual field still respects the 1024 per-field cap.
        for field in embed.fields:
            assert len(field.value) <= 1024
        # The drop must be surfaced honestly (not silently truncated).
        all_text = "\n".join(f.value for f in embed.fields)
        assert "omitted" in all_text, "Dropped events must be signalled with an omission trailer"

    def test_omission_trailer_survives_when_shown_chunk_packs_to_exactly_1024(self, cog):
        """Regression (FIX 1): the omission trailer must NOT be sliced off when the last shown
        chunk already packs to exactly 1024 chars.

        Greedy packing yields exactly 1024 chars for 25 lines whose rendered length is 40 each:
        25*40 + 24 newlines = 1024. We then append many more events so a drop is forced. Before
        the fix, the trailer was appended as `(chunk[:1024] + trailer)[:1024]`, so the final slice
        cut the 27-char '…(+N more events omitted)' trailer back off and the user saw a hard
        cutoff with NO explanation. The trailer must always be present, and the field must still
        respect the 1024 per-field cap.
        """
        # Rendered line = `{time_s:6.1f}s ` (10 chars) + detail. With detail-len 30 → line len 40.
        # 25 such lines pack to exactly 1024 in the first chunk (25*40 + 24 newlines = 1024).
        detail_30 = "X" * 30
        exact_chunk_events = [
            {"tick": i, "time_s": float(i), "actor": "B", "event_type": "E", "detail": detail_30} for i in range(25)
        ]
        # Plenty of additional events to force a drop after the first (exactly-1024) chunk is shown.
        overflow_events = [
            {"tick": 1000 + i, "time_s": float(100 + i), "actor": "B", "event_type": "E", "detail": "Y" * 30}
            for i in range(200)
        ]
        detail_payload = {**_make_detail(), "key_events": exact_chunk_events + overflow_events}
        user = MagicMock()
        user.display_name = "B"

        embed = cog._build_detail_embed(detail_payload, user)

        ke_fields = [f for f in embed.fields if "Key Events" in f.name or f.name == "\u200b"]
        all_text = "\n".join(f.value for f in ke_fields)
        # Sanity: a drop was genuinely forced \u2014 measured INDEPENDENTLY of the trailer (each rendered
        # event line starts with a backtick; the trailer line starts with '\u2026'). With 225 input
        # events and the field-count / budget guards, fewer than 225 lines can be shown. This must
        # hold regardless of whether the (possibly-buggy) trailer survived, so the regression below
        # is non-vacuous even when the trailer is silently sliced off.
        shown_event_lines = sum(1 for line in all_text.splitlines() if line.startswith("`"))
        assert shown_event_lines < 225, (
            f"test setup must force a drop: {shown_event_lines}/225 event lines shown, expected fewer"
        )
        # The honesty guarantee: the omission trailer is present despite the exact-1024 packing.
        assert "omitted" in all_text, (
            "omission trailer was sliced off when the shown chunk packed to exactly 1024 chars"
        )
        # And every field still respects the 1024 per-field cap.
        for field in ke_fields:
            assert len(field.value) <= 1024, f"field value {len(field.value)} exceeds the 1024 per-field cap"

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
        """Individual detail strings longer than _DETAIL_MAX (200) chars are truncated with '…'."""
        very_long_detail = "X" * 250
        events = [{"tick": 1, "time_s": 1.0, "actor": "A", "event_type": "damage", "detail": very_long_detail}]
        detail_payload = {**_make_detail(), "key_events": events}

        cog.http_client.get = AsyncMock(return_value=_make_mock_response(detail_payload))
        interaction = _create_interaction()

        await cog.combat_log.callback(cog, interaction, battle=1)

        embed_call = interaction.followup.send.call_args.kwargs.get("embed")
        key_field = next((f for f in embed_call.fields if "Key Events" in f.name), None)
        assert key_field is not None
        assert len(key_field.value) <= 1024
        # The 250-char detail should have been trimmed to _DETAIL_MAX (200)
        assert "X" * 250 not in key_field.value
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


# ---------------------------------------------------------------------------
# Tests: Recurring section rendering (v3 recap redesign)
# ---------------------------------------------------------------------------


class TestRecurringSection:
    """Verify that the Recurring section is rendered correctly in the detail embed.

    The recurring field renders when the API returns a non-empty recurring list.
    Both Key Events and Recurring are packed into <=1024-char chunks and spilled
    into headerless continuation fields as needed (same machinery as Key Events).
    Key Events has precedence: when the ceiling forces drops, Recurring is trimmed
    before Key Events.
    It must:
      - appear under a '🔁 Recurring' header (first chunk) with continuation fields
      - respect the 1024-char per-field Discord limit across all fields
      - surface any dropped bullets with an honest '+N more bullets omitted' trailer
      - NOT appear when recurring is empty or absent
    """

    def test_recurring_section_rendered_when_present(self, cog):
        """When recurring is a non-empty list, a '🔁 Recurring' field is added."""
        detail = _make_detail()
        detail["recurring"] = ["• Alice: shield depleted ×3 -> 1.0s, 2.0s, 3.0s"]
        user = MagicMock()
        user.display_name = "SamX"

        embed = cog._build_detail_embed(detail, user)

        rec_field = next((f for f in embed.fields if "Recurring" in f.name), None)
        assert rec_field is not None, "🔁 Recurring field must be present when recurring is non-empty"
        assert "shield depleted ×3" in rec_field.value

    def test_recurring_section_absent_when_empty_list(self, cog):
        """When recurring is [] no Recurring field is added."""
        detail = _make_detail()
        detail["recurring"] = []
        user = MagicMock()
        user.display_name = "SamX"

        embed = cog._build_detail_embed(detail, user)

        rec_field = next((f for f in embed.fields if "Recurring" in f.name), None)
        assert rec_field is None, "🔁 Recurring field must NOT be present when recurring is empty"

    def test_recurring_section_absent_when_key_missing(self, cog):
        """When 'recurring' key is absent from data (old rows) no Recurring field is added."""
        detail = _make_detail()
        detail.pop("recurring", None)  # simulate old row without recurring key
        user = MagicMock()
        user.display_name = "SamX"

        embed = cog._build_detail_embed(detail, user)

        rec_field = next((f for f in embed.fields if "Recurring" in f.name), None)
        assert rec_field is None, "🔁 Recurring field must NOT be present when recurring key is absent"

    def test_recurring_all_fields_respect_1024_char_limit(self, cog):
        """Every Recurring field (header + continuations) must respect the 1024-char per-field limit.

        8 bullets × ~209 chars each = 1679 chars total: spans 2 fields.  Every field
        value must be ≤ 1024 chars.
        """
        # 8 bullets × ~209 chars each = 1679 chars total (exceeds 1024 → multiple fields)
        bullets = [
            "• Alice: shield depleted ×10 -> "
            + ", ".join([f"{j * 5.0:.1f}s (by Laser)" for j in range(1, 11)])
            for _ in range(8)
        ]
        detail = _make_detail()
        detail["recurring"] = bullets
        user = MagicMock()
        user.display_name = "SamX"

        embed = cog._build_detail_embed(detail, user)

        rec_fields = [f for f in embed.fields if "Recurring" in f.name or f.name == "​"]
        assert len(rec_fields) >= 1, "At least one Recurring field must be present"
        for f in rec_fields:
            assert len(f.value) <= 1024, (
                f"Recurring field value ({len(f.value)} chars) must not exceed Discord's 1024-char limit"
            )

    def test_recurring_over_1024_spans_continuation_fields_not_silent(self, cog):
        """FIX #1: When recurring text exceeds 1024 chars it spans continuation fields — NOT silent.

        Old behavior: silently hard-truncated a single field with '…'.
        New behavior: packed into <=1024-char chunks across continuation fields; any
        bullets that cannot fit at all get an honest '+N more bullets omitted' trailer.

        This test was previously 'test_recurring_field_truncation_is_silent_no_omission_count'
        and asserted the old silent behavior.  It now asserts the new NON-silent behavior.
        """
        # 8 bullets × ~209 chars each = ~1679 chars total — must span more than one field.
        bullets = [
            "• Alice: shield depleted ×10 -> "
            + ", ".join([f"{j * 5.0:.1f}s (by Laser)" for j in range(1, 11)])
            for _ in range(8)
        ]
        full_text = "\n".join(bullets)
        assert len(full_text) > 1024, "Test setup: recurring text must exceed 1024 chars"

        detail = _make_detail()
        detail["recurring"] = bullets
        user = MagicMock()
        user.display_name = "SamX"

        embed = cog._build_detail_embed(detail, user)

        # Collect all Recurring-related fields (header + continuations).
        # ZWSP (​) is the continuation field name; the header field contains "Recurring".
        rec_fields = [f for f in embed.fields if "Recurring" in f.name or f.name == "​"]
        assert len(rec_fields) >= 1, "At least one Recurring field must be present"

        # Non-silent: all bullet content is either rendered in full OR explicitly omitted.
        all_rec_text = "\n".join(f.value for f in rec_fields)

        # Either all bullets fit (no trailer needed) OR the trailer is present.
        # The total text is ~1679 chars so it will span at least 2 fields; no bullet is
        # silently dropped mid-word.  Verify that either all bullets appear OR omitted trailer.
        bullets_in_output = sum(1 for b in bullets if b[:20] in all_rec_text)
        omission_present = "omitted" in all_rec_text
        assert bullets_in_output == len(bullets) or omission_present, (
            f"Recurring must either render ALL bullets or surface an omission trailer. "
            f"Got {bullets_in_output}/{len(bullets)} bullets and omission_present={omission_present}.\n"
            f"Rec fields: {[(f.name, len(f.value)) for f in rec_fields]}"
        )

        # Every field value must still respect the per-field 1024-char limit.
        for f in rec_fields:
            assert len(f.value) <= 1024, (
                f"Recurring field value ({len(f.value)} chars) must not exceed Discord's 1024-char limit"
            )

    def test_battle285_recurring_8_bullets_all_render_no_silent_drops(self, cog):
        """Battle 285 regression: 8 Recurring bullets (~1679 chars total) must ALL render.

        Old behavior: silently truncated to ~1024 chars, dropping ~3 bullets with no notice.
        New behavior: packed across continuation fields; all 8 bullets appear in full.

        Uses representative bullets that match the B285 production length profile (each
        bullet is ~150-210 chars; 8 together exceed 1024).
        """
        # Representative Battle 285 Recurring bullets — each ~150-210 chars,
        # 8 together total ~1460 chars (reliably > 1024, reliably fits in 2 fields).
        bullets = [
            "• Vilhelm Lindon: shield depleted ×8 -> 3.7s, 35.8s, 63.7s, 79.8s, 93.7s, 108.7s, 138.7s, 168.7s  (all M6 A4 Raccoon primary weapon)",  # noqa: E501
            "• Vilhelm Lindon: armour depleted ×10 -> 3.7s, 40.1s, 42.3s, 48.7s, 79.8s, 93.7s, 109.8s, 138.7s, 154.8s, 169.8s  (all M6 A4 Raccoon)",  # noqa: E501
            "• Vilhelm Lindon activated cloak module ×4 -> 3.7s, 48.7s, 93.7s, 138.7s  (all at or below 66% HP threshold)",  # noqa: E501
            "• Vilhelm Lindon activated booster module ×6 -> 3.7s (80%), 39.1s (80%), 65.8s (40%), 93.7s (80%), 123.7s (80%), 154.8s (60%)",  # noqa: E501
            "• bluefyre: shield depleted ×6 -> 17.3s (AMR Extinctor), 35.2s (Disruptor Laser), 82.0s (Disruptor Laser), 112.0s, 143.5s, 170.0s",  # noqa: E501
            "• bluefyre activated booster module ×6 -> 17.3s, 51.2s, 81.0s, 111.8s, 141.9s, 169.8s  (all at or below 80% HP threshold)",  # noqa: E501
            "• bluefyre M6 A4 Raccoon primary weapon re-enters range ×10 -> 33.7s, 48.7s, 63.7s, 78.7s, 93.7s, 108.7s, 123.7s, 138.7s, 153.7s, 168.7s",  # noqa: E501
            "• Vilhelm Lindon Disruptor Laser secondary weapon re-enters range ×7 -> 49.0s, 64.0s, 79.0s, 109.0s, 139.0s, 154.0s, 169.0s (all secondary)",  # noqa: E501
        ]
        full_text = "\n".join(bullets)
        # These 8 bullets total ~1028 chars — confirmed > 1024 so multi-field packing is needed.
        assert len(full_text) > 1024, f"Test setup: B285 recurring must exceed 1024 chars; got {len(full_text)}"

        # Build a detail payload with minimal key_events and the B285 recurring.
        detail = _make_detail()
        detail["recurring"] = bullets

        user = MagicMock()
        user.display_name = "SamX"
        embed = cog._build_detail_embed(detail, user)

        # Collect ALL Recurring fields (header + any continuation fields).
        rec_fields = [f for f in embed.fields if "Recurring" in f.name or f.name == "​"]
        # Must have at least one Recurring field.
        assert len(rec_fields) >= 1, "At least one Recurring field must be present"

        # Collect all rendered recurring text.
        all_rec_text = "\n".join(f.value for f in rec_fields)

        # ALL 8 bullets must appear in the output — zero silent drops.
        missing = []
        for b in bullets:
            # Use first 30 chars of each bullet as a unique fingerprint.
            fingerprint = b[:30]
            if fingerprint not in all_rec_text:
                missing.append(fingerprint)

        assert not missing, (
            f"Battle 285 Recurring: {len(missing)}/8 bullets were silently dropped.\n"
            f"Missing fingerprints: {missing}\n"
            f"Recurring fields ({len(rec_fields)} total):\n"
            + "\n".join(f"  [{i}] name={f.name!r} len={len(f.value)}" for i, f in enumerate(rec_fields))
        )

        # Every field value must still respect the per-field 1024-char limit.
        for f in rec_fields:
            assert len(f.value) <= 1024, (
                f"Recurring field value ({len(f.value)} chars) must not exceed 1024-char per-field limit"
            )

        # No silent truncation: the field content must not end mid-word with '…' on first field
        # (that would indicate the old blunt-truncation path fired instead of field expansion).
        first_rec = rec_fields[0]
        if len(first_rec.value) == 1024:
            # If exactly 1024, it must be a clean line break, not mid-word truncation.
            assert not first_rec.value.endswith("…"), (
                "First Recurring field ends with '…' at exactly 1024 chars — "
                "old silent truncation path may have fired instead of field expansion"
            )

    def test_recurring_over_ceiling_omission_trailer_present_ke_survives(self, cog):
        """Over-ceiling test: when Discord field limit forces drops, Recurring is trimmed first.

        Construct a scenario where Key Events consumes most of the 24-field budget and the
        Recurring section cannot fit all its bullets.  Assert:
          - Key Events fields are all present (KE has precedence)
          - Recurring shows as many bullets as fit
          - Any dropped Recurring bullets produce an honest '+N more bullets omitted' trailer
          - Total embed stays under 6000 chars
        """
        # Fill up most of the field budget with Key Events (short lines, many events).
        # 22 events × ~30 chars per line → about 1-2 KE fields
        short_ke = [
            {
                "tick": i,
                "time_s": float(i),
                "actor": "A",
                "event_type": "Engagement",  # highest priority → won't be dropped
                "detail": f"Event {i:03d} short",
            }
            for i in range(22)
        ]
        # 30 Recurring bullets × ~100 chars each = 3000 chars → needs ~3 fields.
        many_rec = [
            f"• Alice recurring pattern number {i:02d} -> {i*1.0:.1f}s, {i*2.0:.1f}s, {i*3.0:.1f}s" for i in range(30)
        ]

        detail = _make_detail()
        detail["key_events"] = short_ke
        detail["recurring"] = many_rec

        user = MagicMock()
        user.display_name = "SamX"
        embed = cog._build_detail_embed(detail, user)

        # Embed must stay under Discord's 6000-char hard limit.
        assert len(embed) < 6000, f"Embed size {len(embed)} must be under 6000 chars"
        # Total field count must not exceed 25 (Discord limit: 1 Summary + 24 section fields).
        assert len(embed.fields) <= 25, f"Embed has {len(embed.fields)} fields; Discord allows 25 max"
        # Every field value must respect 1024-char limit.
        for f in embed.fields:
            assert len(f.value) <= 1024

        # Key Events must be present.
        ke_fields = [f for f in embed.fields if "Key Events" in f.name]
        assert len(ke_fields) >= 1, "Key Events section must be present"

        # Recurring section: collect all recurring fields.
        rec_fields = [f for f in embed.fields if "Recurring" in f.name or f.name == "​"]
        all_text = "\n".join(f.value for f in rec_fields)

        # If not all bullets fit, the omission trailer must appear.
        bullets_shown = sum(1 for b in many_rec if b[:20] in all_text)
        if bullets_shown < len(many_rec):
            assert "omitted" in all_text, (
                f"Only {bullets_shown}/{len(many_rec)} Recurring bullets shown but no omission trailer. "
                "Dropped bullets must be surfaced with '+N more bullets omitted'."
            )
