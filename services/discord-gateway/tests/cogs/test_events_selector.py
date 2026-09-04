"""Tests for EventsCog event selector autocomplete (cold-fill, state filter, label length).

Mirrors test_phase1_coldfill.py style. Covers:
- cold-fill returns data on the first call within the 1s budget
- state filter applied (only matching state events returned)
- label length truncated to <= 100 chars
- fmt_delta returns sensible strings
"""

from __future__ import annotations

import asyncio
import os
import sys
import types
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Module-level mock setup — before any src imports
# ---------------------------------------------------------------------------

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []
_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_args, **_kwargs):
    logger = MagicMock()
    for attr in ("info", "debug", "warning", "error", "trace", "critical", "exception"):
        setattr(logger, attr, MagicMock())
    return logger


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


def _evict_discord_modules():
    for _mod in list(sys.modules):
        if _mod == "discord" or _mod.startswith("discord."):
            sys.modules.pop(_mod, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_mock_interaction(user_id=111111111, guild_id=987654321):
    interaction = MagicMock()
    interaction.guild_id = guild_id
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.user.display_name = "TestUser"
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.namespace = MagicMock()
    return interaction


def _make_event(event_id: int, state: str = "active", ends_at: str | None = None) -> dict:
    return {
        "id": event_id,
        "type_slug": "duels_won",
        "type_display": "Duels Won",
        "state": state,
        "duration_days": 7,
        "params": {},
        "guild_id": 987654321,
        "ends_at": ends_at,
        "scheduled_start_at": None,
        "started_at": None,
        "prize_count": 0,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mock_bot():
    bot = MagicMock()
    bot.loop = MagicMock()
    bot.loop.create_task = MagicMock()
    return bot


@pytest.fixture
def events_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.eventsCog import EventsCog

    cog = EventsCog(mock_bot)
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


# ---------------------------------------------------------------------------
# fmt_delta tests (unit tests on timestamp_utils)
# ---------------------------------------------------------------------------


class TestFmtDelta:
    def test_future_days_and_hours(self):
        from utils.timestamp_utils import fmt_delta
        future = (datetime.now(UTC) + timedelta(days=3, hours=4)).isoformat()
        result = fmt_delta(future)
        assert "3d" in result
        assert "h" in result

    def test_future_hours_only(self):
        from utils.timestamp_utils import fmt_delta
        future = (datetime.now(UTC) + timedelta(hours=5)).isoformat()
        result = fmt_delta(future)
        assert "d" not in result
        assert "h" in result

    def test_past_returns_ended(self):
        from utils.timestamp_utils import fmt_delta
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        assert fmt_delta(past) == "ended"

    def test_none_returns_question_mark(self):
        from utils.timestamp_utils import fmt_delta
        assert fmt_delta(None) == "?"

    def test_bad_string_returns_question_mark(self):
        from utils.timestamp_utils import fmt_delta
        assert fmt_delta("not-a-date") == "?"


# ---------------------------------------------------------------------------
# EventsCog._events_autocomplete — state filter, cold-fill, label length
# ---------------------------------------------------------------------------


class TestEventsSelectorColdFill:
    def test_coldfill_returns_data_on_first_call(self, events_cog):
        """Cold fill (cache miss) completes within budget and returns choices."""
        events_cog._events_cache.invalidate(987654321)

        active_event = _make_event(1, state="active", ends_at=(datetime.now(UTC) + timedelta(hours=10)).isoformat())

        async def _fn(key):
            return [active_event]

        events_cog._events_cache._refresh_fn = _fn
        interaction = _create_mock_interaction()
        res = asyncio.run(events_cog._events_autocomplete(interaction, "", states={"active"}))
        assert len(res) == 1
        assert res[0].value == "1"

    def test_state_filter_applied(self, events_cog):
        """Events not in the requested state set are excluded from choices."""
        draft_event = _make_event(2, state="draft")
        active_event = _make_event(3, state="active")
        events_cog._events_cache.set(987654321, [draft_event, active_event])

        interaction = _create_mock_interaction()
        res = asyncio.run(events_cog._events_autocomplete(interaction, "", states={"active"}))
        # Only active event should appear
        assert len(res) == 1
        assert res[0].value == "3"

    def test_empty_states_returns_all(self, events_cog):
        """Empty states set returns all events regardless of state."""
        events = [_make_event(4, state="draft"), _make_event(5, state="active"), _make_event(6, state="ended")]
        events_cog._events_cache.set(987654321, events)
        interaction = _create_mock_interaction()
        res = asyncio.run(events_cog._events_autocomplete(interaction, "", states=set()))
        assert len(res) == 3

    def test_label_truncated_to_100(self, events_cog):
        """Label is truncated to at most 100 characters."""
        long_display = "A" * 200
        event = _make_event(7, state="active")
        event["type_display"] = long_display
        events_cog._events_cache.set(987654321, [event])
        interaction = _create_mock_interaction()
        res = asyncio.run(events_cog._events_autocomplete(interaction, "", states={"active"}))
        assert len(res) == 1
        assert len(res[0].name) <= 100

    def test_warm_peek_no_refresh_call(self, events_cog):
        """Hot-path peek does not invoke refresh_fn."""
        event = _make_event(8, state="active")
        events_cog._events_cache.set(987654321, [event])
        calls = {"n": 0}

        async def _fn(key):
            calls["n"] += 1
            return [event]

        events_cog._events_cache._refresh_fn = _fn
        interaction = _create_mock_interaction()
        asyncio.run(events_cog._events_autocomplete(interaction, "", states={"active"}))
        assert calls["n"] == 0  # warm peek — refresh_fn never called


# ---------------------------------------------------------------------------
# UTC offset choices — must be exactly 25 (UTC-12 … UTC+12 whole hours)
# ---------------------------------------------------------------------------


class TestUTCOffsetChoices:
    def test_exactly_25_choices(self):
        from cogs.eventsCog import _UTC_OFFSET_CHOICES
        assert len(_UTC_OFFSET_CHOICES) == 25

    def test_no_half_hour_offsets(self):
        from cogs.eventsCog import _UTC_OFFSET_CHOICES
        for c in _UTC_OFFSET_CHOICES:
            assert "." not in str(c.value), f"Non-whole-hour offset found: {c.name}"


# ---------------------------------------------------------------------------
# _ac_event_player — player-facing selector (scheduled/active/ended ≤7d)
# ---------------------------------------------------------------------------


class TestPlayerEventSelector:
    def test_draft_excluded(self, events_cog):
        """Draft events are not shown to players."""
        events_cog._events_cache.set(987654321, [_make_event(10, state="draft")])
        interaction = _create_mock_interaction()
        res = asyncio.run(events_cog._ac_event_player(interaction, ""))
        assert res == []

    def test_cancelled_excluded(self, events_cog):
        """Cancelled events are not shown to players."""
        events_cog._events_cache.set(987654321, [_make_event(11, state="cancelled")])
        interaction = _create_mock_interaction()
        res = asyncio.run(events_cog._ac_event_player(interaction, ""))
        assert res == []

    def test_30d_ended_excluded(self, events_cog):
        """Ended events older than 7 days are excluded."""
        old_end = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        events_cog._events_cache.set(987654321, [_make_event(12, state="ended", ends_at=old_end)])
        interaction = _create_mock_interaction()
        res = asyncio.run(events_cog._ac_event_player(interaction, ""))
        assert res == []

    def test_2d_ended_included(self, events_cog):
        """Ended events within 7 days are included."""
        recent_end = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        events_cog._events_cache.set(987654321, [_make_event(13, state="ended", ends_at=recent_end)])
        interaction = _create_mock_interaction()
        res = asyncio.run(events_cog._ac_event_player(interaction, ""))
        assert len(res) == 1
        assert res[0].value == "13"

    def test_active_always_included(self, events_cog):
        """Active events are always included."""
        events_cog._events_cache.set(987654321, [_make_event(14, state="active")])
        interaction = _create_mock_interaction()
        res = asyncio.run(events_cog._ac_event_player(interaction, ""))
        assert len(res) == 1


# ---------------------------------------------------------------------------
# _item_autocomplete — no type → empty; Credits → empty; Ship/Secondary filtered
# ---------------------------------------------------------------------------


class TestItemAutocomplete:
    def _make_cog_with_admin(self, mock_bot, ship_names, item_catalog):
        """Return an EventsCog with a mock AdminCog providing catalog caches."""
        from cogs.eventsCog import EventsCog
        cog = EventsCog(mock_bot)
        cog.http_client = MagicMock()
        cog.http_client.aclose = AsyncMock()

        admin_cog = MagicMock()
        admin_cog._ship_catalog = MagicMock()
        admin_cog._ship_catalog.peek = MagicMock(return_value=ship_names)
        admin_cog._item_catalog = MagicMock()
        admin_cog._item_catalog.peek = MagicMock(side_effect=lambda cat: item_catalog.get(cat))
        mock_bot.get_cog = MagicMock(return_value=admin_cog)
        return cog

    def test_no_type_returns_empty(self, mock_bot):
        """No type selected → empty list (less confusing than dumping everything)."""
        cog = self._make_cog_with_admin(mock_bot, [], {})
        interaction = _create_mock_interaction()
        interaction.namespace.type = None
        res = asyncio.run(cog._item_autocomplete(interaction, ""))
        assert res == []

    def test_credits_returns_empty(self, mock_bot):
        """Credits prize type → empty (no item needed)."""
        cog = self._make_cog_with_admin(mock_bot, [], {})
        interaction = _create_mock_interaction()
        interaction.namespace.type = "Credits"
        res = asyncio.run(cog._item_autocomplete(interaction, ""))
        assert res == []

    def test_ship_returns_ship_names(self, mock_bot):
        """Ship type → ship catalog names."""
        ships = ["Vanguard", "Cutlass", "Aurora"]
        cog = self._make_cog_with_admin(mock_bot, ships, {})
        interaction = _create_mock_interaction()
        interaction.namespace.type = "Ship"
        res = asyncio.run(cog._item_autocomplete(interaction, ""))
        assert [c.value for c in res] == ships

    def test_secondary_returns_only_secondary(self, mock_bot):
        """Secondary type → only secondary_weapon catalog items."""
        catalog = {
            "primary_weapon": ["Laser Cannon"],
            "secondary_weapon": ["Missile Rack", "Rocket Pod"],
            "turret_weapon": ["Turret A"],
            "module": ["Shield Gen"],
        }
        cog = self._make_cog_with_admin(mock_bot, [], catalog)
        interaction = _create_mock_interaction()
        interaction.namespace.type = "Secondary"
        res = asyncio.run(cog._item_autocomplete(interaction, ""))
        assert {c.value for c in res} == {"Missile Rack", "Rocket Pod"}


# ---------------------------------------------------------------------------
# /events — _sync_player_notification_roles called; exception swallowed
# ---------------------------------------------------------------------------


class TestEventsPlayerSync:
    def test_sync_roles_exception_swallowed(self, events_cog):
        """/events best-effort role sync — raised exception must not propagate."""
        player_cog = MagicMock()
        player_cog._sync_player_notification_roles = AsyncMock(side_effect=RuntimeError("boom"))
        events_cog.bot.get_cog = MagicMock(return_value=player_cog)

        # Prime the events cache so the command's HTTP call (event detail) can skip
        events_cog._events_cache.set(987654321, [])

        interaction = _create_mock_interaction()
        interaction.guild = MagicMock()
        interaction.guild.name = "TestGuild"
        interaction.guild_id = 987654321

        import discord
        interaction.user = MagicMock(spec=discord.Member)
        interaction.user.id = 111111111
        interaction.response = AsyncMock()
        interaction.followup = AsyncMock()

        # Patch the HTTP client to return an empty events list
        async def _mock_get(*args, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()

            class FakeResp:
                status_code = 200
                def raise_for_status(self): pass
                def json(self): return []

            return FakeResp()

        events_cog.http_client.get = AsyncMock(side_effect=_mock_get)

        # Should NOT raise — exception is swallowed
        asyncio.run(events_cog.events.callback(events_cog, interaction, event=None))


# ---------------------------------------------------------------------------
# event_leaderboard — Prizes field from GET /events/{id}
# ---------------------------------------------------------------------------


class TestEventLeaderboardPrizes:
    def test_prizes_field_rendered_from_event_detail(self, events_cog):
        """event_leaderboard with event= fetches GET /events/{id} and adds a Prizes field."""
        standings_payload = [
            {"rank": 1, "display_name": "Alice", "value": 15.0, "qualified": True, "user_id": 111},
            {"rank": 2, "display_name": "Bob",   "value": 10.0, "qualified": True, "user_id": 222},
        ]
        detail_payload = {
            "id": 42,
            "type_slug": "bounty_caps",
            "prizes": [
                {"id": 1, "rank_from": 1, "rank_to": 1, "kind": "credits", "item_ref": None, "qty": 500},
                {"id": 2, "rank_from": None, "rank_to": None, "kind": "credits", "item_ref": None, "qty": 50},
            ],
        }

        call_order = []

        async def _mock_get(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if "/standings" in url:
                call_order.append("standings")
                resp.json = MagicMock(return_value=standings_payload)
            else:
                call_order.append("detail")
                resp.json = MagicMock(return_value=detail_payload)
            return resp

        events_cog.http_client.get = AsyncMock(side_effect=_mock_get)

        interaction = _create_mock_interaction()
        interaction.guild = MagicMock()
        interaction.guild.name = "TestGuild"

        captured_embeds = []

        async def _followup_send(*args, embed=None, **kwargs):
            if embed is not None:
                captured_embeds.append(embed)

        interaction.followup.send = AsyncMock(side_effect=_followup_send)

        asyncio.run(events_cog.event_leaderboard.callback(events_cog, interaction, event="42", type=None))

        assert "detail" in call_order, "expected GET /events/{id} to be called"
        assert captured_embeds, "expected an embed to be sent"

        embed = captured_embeds[0]
        field_names = [f.name for f in embed.fields]
        assert "Prizes" in field_names, f"expected Prizes field in embed, got: {field_names}"

        prizes_field = next(f for f in embed.fields if f.name == "Prizes")
        assert "500" in prizes_field.value, f"expected 1st prize in Prizes field: {prizes_field.value!r}"
        assert "Participation" in prizes_field.value, f"expected Participation in Prizes field: {prizes_field.value!r}"
