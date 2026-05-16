"""Tests for utils.bounty_announcement_payload (A.48 unified rendering).

Covers the bot-core side of the structured payload that bot-core posts to the
gateway's /announcements/bounty/... endpoints. Asserts:
  - title and color follow normal vs captured rules
  - prefix fields (Difficulty / Reward Pool / Bounty Ends) format correctly
  - Route / Checked Systems fields are in prefix_fields (above Active Ship)
  - suffix_fields is always [] (empty — gateway no-op)
  - text_content is the role mention or None
  - LoadoutResponseService is delegated to for the loadout body
"""

from __future__ import annotations

import os
import sys
import types
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Mock shared.bblogger BEFORE importing source modules.
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = lambda *a, **kw: MagicMock()
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bounty(
    *,
    criminal_name: str = "Pal Tyyrt",
    criminal_faction: str = "Terran",
    tech_level: int = 10,
    reward: int = 50000,
    reward_per_sys: int = 2500,
    end_time: datetime | None = None,
    route: list[str] | None = None,
    checked: dict | None = None,
    answer: str | None = None,
    bounty_id: int = 5,
    division: str = "platinum",
):
    if end_time is None:
        end_time = datetime(2050, 1, 1, tzinfo=UTC)  # known timestamp for assertions
    return SimpleNamespace(
        id=bounty_id,
        criminal_name=criminal_name,
        criminal_faction=criminal_faction,
        tech_level=tech_level,
        reward=reward,
        reward_per_sys=reward_per_sys,
        end_time=end_time,
        route=route or ["Pan", "Mido", "Pescal Ansen"],
        checked=checked,
        answer=answer,
        division=division,
        criminal_ship={"ship_name": "Darkzov", "weapons": [], "turrets": [], "modules": []},
    )


async def _stub_loadout_response(monkeypatch, response_dict: dict | None):
    """Patch LoadoutResponseService.build_bounty_loadout to return the given dict.

    `response_dict` is a plain dict (the test caller chooses shape); it is
    wrapped in a SimpleNamespace whose model_dump() returns the dict so the
    helper's `loadout_response.model_dump()` call works.
    """
    from services import loadout_response_service as svc_mod

    fake_response = MagicMock()
    fake_response.model_dump.return_value = response_dict if response_dict is not None else {}

    async def _fake_build(_self, _db, _bid):
        return fake_response if response_dict is not None else None

    monkeypatch.setattr(svc_mod.LoadoutResponseService, "build_bounty_loadout", _fake_build)


# ===========================================================================
# Title and color
# ===========================================================================


class TestTitleAndColor:
    @pytest.fixture(autouse=True)
    def _stub(self, monkeypatch):
        # Make build_bounty_loadout a no-cost stub returning an empty dict.
        from services import loadout_response_service as svc_mod

        fake = MagicMock()
        fake.model_dump.return_value = {"subject_kind": "criminal", "subject_name": "Pal Tyyrt"}

        async def _fake(self_, db_, bid_):
            return fake

        monkeypatch.setattr(svc_mod.LoadoutResponseService, "build_bounty_loadout", _fake)

    async def test_title_normal(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty(criminal_name="Pal Tyyrt")
        out = await build_bounty_announcement_request(MagicMock(), b)
        assert out["metadata"]["title"] == "Pal Tyyrt"

    async def test_title_captured(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty(criminal_name="Pal Tyyrt")
        out = await build_bounty_announcement_request(MagicMock(), b, captured=True)
        assert out["metadata"]["title"] == "✅ Pal Tyyrt — CAPTURED"

    async def test_color_terran(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty(criminal_faction="Terran")
        out = await build_bounty_announcement_request(MagicMock(), b)
        assert out["metadata"]["color"] == 15844367

    async def test_color_vossk_case_insensitive(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty(criminal_faction="VOSSK")
        out = await build_bounty_announcement_request(MagicMock(), b)
        assert out["metadata"]["color"] == 1752220

    async def test_color_unknown_faction_default(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty(criminal_faction="Mystery")
        out = await build_bounty_announcement_request(MagicMock(), b)
        assert out["metadata"]["color"] == 10181046

    async def test_color_captured_overrides_faction(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty(criminal_faction="Terran")
        out = await build_bounty_announcement_request(MagicMock(), b, captured=True)
        assert out["metadata"]["color"] == 3066993  # green


# ===========================================================================
# Prefix and suffix fields
# ===========================================================================


class TestPrefixFields:
    @pytest.fixture(autouse=True)
    def _stub(self, monkeypatch):
        from services import loadout_response_service as svc_mod

        fake = MagicMock()
        fake.model_dump.return_value = {"subject_kind": "criminal", "subject_name": "X"}

        async def _fake(self_, db_, bid_):
            return fake

        monkeypatch.setattr(svc_mod.LoadoutResponseService, "build_bounty_loadout", _fake)

    async def test_difficulty_field_format(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty(tech_level=7)
        out = await build_bounty_announcement_request(MagicMock(), b)
        diff = next(f for f in out["metadata"]["prefix_fields"] if f["name"] == "Difficulty")
        assert diff["value"] == "T7"
        assert diff["inline"] is True

    async def test_reward_pool_format_with_thousands_comma(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty(reward=1234567)
        out = await build_bounty_announcement_request(MagicMock(), b)
        reward = next(f for f in out["metadata"]["prefix_fields"] if f["name"] == "Reward Pool")
        assert reward["value"] == "1,234,567 credits"
        assert reward["inline"] is True

    async def test_bounty_ends_normal_uses_relative_timestamp(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        end = datetime(2050, 1, 1, tzinfo=UTC)
        b = _make_bounty(end_time=end)
        out = await build_bounty_announcement_request(MagicMock(), b)
        ends = next(f for f in out["metadata"]["prefix_fields"] if f["name"] == "Bounty Ends")
        assert ends["value"] == f"<t:{int(end.timestamp())}:R>"

    async def test_bounty_ends_captured_shows_captured_marker(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty()
        out = await build_bounty_announcement_request(MagicMock(), b, captured=True)
        ends = next(f for f in out["metadata"]["prefix_fields"] if f["name"] == "Bounty Ends")
        assert ends["value"] == "**Captured**"


class TestSuffixFields:
    """Route and Checked Systems fields — now in prefix_fields (above Active Ship).

    NOTE: This class is named TestSuffixFields for historical continuity but the
    fields being tested are emitted in prefix_fields (not suffix_fields) since the
    field-reorder change (A.48 field reorder). suffix_fields is always [] after this change.
    """

    @pytest.fixture(autouse=True)
    def _stub(self, monkeypatch):
        from services import loadout_response_service as svc_mod

        fake = MagicMock()
        fake.model_dump.return_value = {"subject_kind": "criminal", "subject_name": "X"}

        async def _fake(self_, db_, bid_):
            return fake

        monkeypatch.setattr(svc_mod.LoadoutResponseService, "build_bounty_loadout", _fake)

    async def test_route_no_checked(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty(route=["Pan", "Mido", "Pescal Ansen"], checked=None)
        out = await build_bounty_announcement_request(MagicMock(), b)
        route = next(f for f in out["metadata"]["prefix_fields"] if f["name"] == "Route")
        assert route["value"] == "Pan, Mido, Pescal Ansen"

    async def test_route_with_checked_strikethrough(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        # Pan was checked by player 100, not the answer
        b = _make_bounty(
            route=["Pan", "Mido", "Pescal Ansen"],
            checked={"Pan": 100},
            answer="Pescal Ansen",
        )
        out = await build_bounty_announcement_request(MagicMock(), b)
        route = next(f for f in out["metadata"]["prefix_fields"] if f["name"] == "Route")
        # Pan checked → ~~Pan~~ (no recently_spotted because 2 stops away → still recently_spotted)
        # Actually distance = 2 - 0 = 2, so within 1..2 → recently_spotted
        # So expected: **~~Pan~~**, Mido, Pescal Ansen (answer not in checked dict)
        assert "**~~Pan~~**" in route["value"]

    async def test_route_with_found_status(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty(
            route=["Pan", "Mido", "Pescal Ansen"],
            checked={"Pescal Ansen": 100},
            answer="Pescal Ansen",
        )
        out = await build_bounty_announcement_request(MagicMock(), b)
        route = next(f for f in out["metadata"]["prefix_fields"] if f["name"] == "Route")
        assert "**Pescal Ansen**" in route["value"]

    async def test_checked_systems_fallback_when_no_checks(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty(checked=None)
        out = await build_bounty_announcement_request(MagicMock(), b)
        cs = next(f for f in out["metadata"]["prefix_fields"] if f["name"] == "Checked Systems")
        assert cs["value"] == "> *No systems checked yet*"

    async def test_checked_systems_unchecked_sentinel_filtered(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        # checker_id == -1 means unchecked; should be skipped entirely
        b = _make_bounty(checked={"Pan": -1, "Mido": -1, "Pescal Ansen": -1})
        out = await build_bounty_announcement_request(MagicMock(), b)
        cs = next(f for f in out["metadata"]["prefix_fields"] if f["name"] == "Checked Systems")
        assert cs["value"] == "> *No systems checked yet*"


# ===========================================================================
# Field ordering and suffix_fields=[] invariant (adversarial / edge case)
# ===========================================================================


class TestFieldOrdering:
    """Adversarial tests for the A.48 field-reorder change.

    Verifies:
    - suffix_fields is ALWAYS [] (never Route/Checked in suffix).
    - Route and Checked Systems appear at positions [3] and [4] in prefix_fields
      (after Difficulty/Reward/Ends at positions [0], [1], [2]).
    - Captured state still renders Route/Checked in prefix_fields.
    - _build_prefix_fields returns a fresh list each call (no shared mutable state).
    """

    @pytest.fixture(autouse=True)
    def _stub(self, monkeypatch):
        from services import loadout_response_service as svc_mod

        fake = MagicMock()
        fake.model_dump.return_value = {"subject_kind": "criminal", "subject_name": "X"}

        async def _fake(self_, db_, bid_):
            return fake

        monkeypatch.setattr(svc_mod.LoadoutResponseService, "build_bounty_loadout", _fake)

    async def test_suffix_fields_is_always_empty_list(self):
        """Regression: suffix_fields must be [] — Route/Checked moved to prefix."""
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty()
        out = await build_bounty_announcement_request(MagicMock(), b)
        assert out["metadata"]["suffix_fields"] == [], (
            "suffix_fields must be empty []. Route and Checked Systems should be in prefix_fields."
        )

    async def test_suffix_fields_empty_even_with_checked_systems(self):
        """suffix_fields stays [] regardless of how many systems are checked."""
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty(
            route=["Pan", "Mido", "Pescal Ansen"],
            checked={"Pan": 100, "Mido": 200},
            answer="Pescal Ansen",
        )
        out = await build_bounty_announcement_request(MagicMock(), b)
        assert out["metadata"]["suffix_fields"] == []

    async def test_prefix_fields_has_exactly_five_fields(self):
        """prefix_fields must contain Difficulty + Reward + Ends + Route + Checked (5 total)."""
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty()
        out = await build_bounty_announcement_request(MagicMock(), b)
        names = [f["name"] for f in out["metadata"]["prefix_fields"]]
        assert names == ["Difficulty", "Reward Pool", "Bounty Ends", "Route", "Checked Systems"], (
            f"prefix_fields field order incorrect. Got: {names}"
        )

    async def test_route_appears_at_index_3_in_prefix_fields(self):
        """Route field must be at index 3 in prefix_fields (after Difficulty/Reward/Ends)."""
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty(route=["Alpha", "Beta"])
        out = await build_bounty_announcement_request(MagicMock(), b)
        prefix = out["metadata"]["prefix_fields"]
        assert prefix[3]["name"] == "Route"
        assert "Alpha" in prefix[3]["value"]

    async def test_checked_systems_appears_at_index_4_in_prefix_fields(self):
        """Checked Systems field must be at index 4 in prefix_fields."""
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty(checked=None)
        out = await build_bounty_announcement_request(MagicMock(), b)
        prefix = out["metadata"]["prefix_fields"]
        assert prefix[4]["name"] == "Checked Systems"

    async def test_route_and_checked_in_prefix_fields_when_captured(self):
        """Captured state: Route and Checked Systems still appear in prefix_fields.

        The captured flag suppresses Active Ship / Ship Stats / loadout sections
        in the gateway embed builder, but prefix_fields (including Route/Checked)
        are always rendered.
        """
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty(
            route=["Pan", "Mido"],
            checked={"Pan": 100},
            answer="Mido",
        )
        out = await build_bounty_announcement_request(MagicMock(), b, captured=True)
        prefix_names = [f["name"] for f in out["metadata"]["prefix_fields"]]
        assert "Route" in prefix_names, "Route must appear in prefix_fields even when captured=True"
        assert "Checked Systems" in prefix_names, (
            "Checked Systems must appear in prefix_fields even when captured=True"
        )
        assert out["metadata"]["suffix_fields"] == []

    async def test_prefix_fields_fresh_list_no_shared_mutation(self):
        """Two successive calls must not share the same list object.

        _build_prefix_fields() must return a fresh list each invocation.
        Otherwise prefix_fields.extend(...) could accumulate across calls.
        """
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        db_mock = MagicMock()
        b = _make_bounty()
        out1 = await build_bounty_announcement_request(db_mock, b)
        out2 = await build_bounty_announcement_request(db_mock, b)
        # Each call should produce a list of exactly 5 items (not 5+2=7 or more).
        assert len(out1["metadata"]["prefix_fields"]) == 5
        assert len(out2["metadata"]["prefix_fields"]) == 5
        # The lists must be different objects (fresh each call).
        assert out1["metadata"]["prefix_fields"] is not out2["metadata"]["prefix_fields"]


# ===========================================================================
# Top-level payload structure
# ===========================================================================


class TestPayloadStructure:
    @pytest.fixture(autouse=True)
    def _stub(self, monkeypatch):
        from services import loadout_response_service as svc_mod

        fake = MagicMock()
        fake.model_dump.return_value = {"subject_kind": "criminal", "subject_name": "X", "thumbnail_url": None}

        async def _fake(self_, db_, bid_):
            return fake

        monkeypatch.setattr(svc_mod.LoadoutResponseService, "build_bounty_loadout", _fake)

    async def test_text_content_is_role_mention_when_role_id_present(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty()
        out = await build_bounty_announcement_request(MagicMock(), b, bounty_hunter_role_id=42)
        assert out["text_content"] == "<@&42>"

    async def test_text_content_none_when_role_id_absent(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty()
        out = await build_bounty_announcement_request(MagicMock(), b)
        assert out["text_content"] is None

    async def test_top_level_keys(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty()
        out = await build_bounty_announcement_request(MagicMock(), b)
        assert set(out.keys()) == {"text_content", "loadout_response", "metadata"}
        assert set(out["metadata"].keys()) == {
            "title",
            "color",
            "footer_text",
            "image_url",
            "captured",
            "prefix_fields",
            "suffix_fields",
            "reward",
            "reward_per_sys",
            "route_length",
        }
        # suffix_fields must be empty (Route/Checked moved to prefix_fields after field reorder).
        assert out["metadata"]["suffix_fields"] == []

    async def test_image_url_passed_through(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty()
        out = await build_bounty_announcement_request(MagicMock(), b, route_map_url="https://cdn/map.png")
        assert out["metadata"]["image_url"] == "https://cdn/map.png"

    async def test_footer_text_is_faction(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty(criminal_faction="Vossk")
        out = await build_bounty_announcement_request(MagicMock(), b)
        assert out["metadata"]["footer_text"] == "Vossk"

    async def test_criminal_icon_used_when_loadout_missing_thumbnail(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty()
        out = await build_bounty_announcement_request(MagicMock(), b, criminal_icon="https://cdn/criminal.png")
        # loadout_response.thumbnail_url was None in the stubbed response → criminal_icon fills it in
        assert out["loadout_response"]["thumbnail_url"] == "https://cdn/criminal.png"


class TestMissingLoadoutResponse:
    async def test_returns_message_when_loadout_unavailable(self, monkeypatch):
        """When LoadoutResponseService returns None, payload carries a 'message' field for graceful render."""
        from services import loadout_response_service as svc_mod

        async def _fake(self_, db_, bid_):
            return None

        monkeypatch.setattr(svc_mod.LoadoutResponseService, "build_bounty_loadout", _fake)

        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty(criminal_name="Lost Soul")
        out = await build_bounty_announcement_request(MagicMock(), b)
        assert out["loadout_response"]["message"] == "Criminal ship data unavailable"
        assert out["loadout_response"]["subject_name"] == "Lost Soul"


# ===========================================================================
# Payout metadata fields (Task B)
# ===========================================================================


class TestPayoutMetadataFields:
    """Verify that reward, reward_per_sys, and route_length are included in metadata."""

    @pytest.fixture(autouse=True)
    def _stub(self, monkeypatch):
        from services import loadout_response_service as svc_mod

        fake = MagicMock()
        fake.model_dump.return_value = {"subject_kind": "criminal", "subject_name": "X"}

        async def _fake(self_, db_, bid_):
            return fake

        monkeypatch.setattr(svc_mod.LoadoutResponseService, "build_bounty_loadout", _fake)

    async def test_reward_present_in_metadata(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty(reward=75000)
        out = await build_bounty_announcement_request(MagicMock(), b)
        assert out["metadata"]["reward"] == 75000

    async def test_reward_per_sys_present_in_metadata(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty(reward_per_sys=3500)
        out = await build_bounty_announcement_request(MagicMock(), b)
        assert out["metadata"]["reward_per_sys"] == 3500

    async def test_route_length_matches_route_list_length(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty(route=["Pan", "Mido", "Pescal Ansen"])
        out = await build_bounty_announcement_request(MagicMock(), b)
        assert out["metadata"]["route_length"] == 3

    async def test_route_length_zero_when_route_is_empty(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty(route=["X"])  # a 1-system route
        b.route = []  # override directly to empty
        out = await build_bounty_announcement_request(MagicMock(), b)
        assert out["metadata"]["route_length"] == 0

    async def test_route_length_single_system(self):
        from utils.bounty_announcement_payload import build_bounty_announcement_request

        b = _make_bounty(route=["Omega"])
        out = await build_bounty_announcement_request(MagicMock(), b)
        assert out["metadata"]["route_length"] == 1
