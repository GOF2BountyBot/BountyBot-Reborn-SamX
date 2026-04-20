"""
Unit tests for BountyAnnouncementBuilder.

Tests are written FIRST (TDD) — they define the desired behavior.
The implementation in message_builders/builders/bounty_announcement.py
must make all tests pass.

IMPORTANT: shared.bblogger must be mocked BEFORE importing any source modules.
"""

import json
import os
import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Mock shared / shared.bblogger BEFORE importing any source modules.
# conftest.py already does this at collection time; we repeat here for
# standalone execution safety.
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")

    def _make_mock_logger(name: str = "test") -> MagicMock:
        logger = MagicMock()
        for method in ("info", "debug", "warning", "error", "trace", "critical"):
            setattr(logger, method, MagicMock())
        return logger

    _mock_bblogger.get_logger = _make_mock_logger
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

# ---------------------------------------------------------------------------
# Ensure the src directory is on the path.
# ---------------------------------------------------------------------------
_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


# ---------------------------------------------------------------------------
# Shared fixtures and helpers
# ---------------------------------------------------------------------------


def make_minimal_data(**overrides) -> dict[str, Any]:
    """Return a minimal valid data dict for BountyAnnouncementBuilder.build_payload."""
    base = {
        "criminal_name": "Trent Jameson",
        "criminal_faction": "Terran",
        "division": "bronze",
        "tech_level": 5,
        "reward": 50000,
        "route": ["Pan", "Mido", "Pescal Ansen"],
        "end_time_unix": 1700000000,
        # Optional fields absent by default
        "criminal_icon": None,
        "criminal_ship": None,
        "checked": None,
        "bounty_hunter_role_id": None,
        "route_map_url": None,
    }
    base.update(overrides)
    return base


def make_full_criminal_ship() -> dict[str, Any]:
    """Return a complete criminal_ship dict."""
    return {
        "ship_name": "Nemesis",
        "ship_emoji": "<:nemesis:123>",
        "armour": 200,
        "armor_hp": 360,
        "shield_hp": 380,
        "total_hp": 740,
        "weapons": [
            {"name": "Nirai Impulse EX", "emoji": "<:nirai:456>", "dps": 45.0},
            {"name": "Laser MK2", "emoji": None, "dps": 30.0},
        ],
        "modules": [
            {"name": "Rhoda Blackhole", "emoji": "<:rhoda:789>"},
            {"name": "Shield Booster", "emoji": None},
        ],
    }


# ===========================================================================
# Tests for get_message_type()
# ===========================================================================


class TestGetMessageType:
    """BountyAnnouncementBuilder.get_message_type() must return the correct string."""

    @pytest.fixture()
    def builder(self):
        from message_builders.builders.bounty_announcement import BountyAnnouncementBuilder

        return BountyAnnouncementBuilder()

    def test_returns_bounty_announcement(self, builder):
        assert builder.get_message_type() == "bounty_announcement"

    def test_returns_string(self, builder):
        assert isinstance(builder.get_message_type(), str)


# ===========================================================================
# Tests for validate_input()
# ===========================================================================


class TestValidateInputValid:
    """validate_input() returns True when all required fields are present and typed correctly."""

    @pytest.fixture()
    def builder(self):
        from message_builders.builders.bounty_announcement import BountyAnnouncementBuilder

        return BountyAnnouncementBuilder()

    def test_valid_minimal_data(self, builder):
        assert builder.validate_input(make_minimal_data()) is True

    def test_valid_with_all_optional_fields(self, builder):
        data = make_minimal_data(
            criminal_icon="https://example.com/icon.png",
            criminal_ship=make_full_criminal_ship(),
            checked={"Pan": "checked"},
            bounty_hunter_role_id=123456789,
            route_map_url="https://cdn.example.com/map.png",
        )
        assert builder.validate_input(data) is True

    def test_valid_returns_true_not_truthy(self, builder):
        """Must return exactly True, not just a truthy value."""
        result = builder.validate_input(make_minimal_data())
        assert result is True

    def test_valid_with_extra_keys_ignored(self, builder):
        data = make_minimal_data()
        data["extra_key"] = "ignored"
        assert builder.validate_input(data) is True


class TestValidateInputMissingRequiredFields:
    """validate_input() returns False when any required field is absent."""

    @pytest.fixture()
    def builder(self):
        from message_builders.builders.bounty_announcement import BountyAnnouncementBuilder

        return BountyAnnouncementBuilder()

    def test_missing_criminal_name(self, builder):
        data = make_minimal_data()
        del data["criminal_name"]
        assert builder.validate_input(data) is False

    def test_missing_criminal_faction(self, builder):
        data = make_minimal_data()
        del data["criminal_faction"]
        assert builder.validate_input(data) is False

    def test_missing_division(self, builder):
        data = make_minimal_data()
        del data["division"]
        assert builder.validate_input(data) is False

    def test_missing_tech_level(self, builder):
        data = make_minimal_data()
        del data["tech_level"]
        assert builder.validate_input(data) is False

    def test_missing_reward(self, builder):
        data = make_minimal_data()
        del data["reward"]
        assert builder.validate_input(data) is False

    def test_missing_route(self, builder):
        data = make_minimal_data()
        del data["route"]
        assert builder.validate_input(data) is False

    def test_missing_end_time_unix(self, builder):
        data = make_minimal_data()
        del data["end_time_unix"]
        assert builder.validate_input(data) is False

    def test_empty_dict_returns_false(self, builder):
        assert builder.validate_input({}) is False


class TestValidateInputWrongTypes:
    """validate_input() returns False when required fields have wrong types."""

    @pytest.fixture()
    def builder(self):
        from message_builders.builders.bounty_announcement import BountyAnnouncementBuilder

        return BountyAnnouncementBuilder()

    def test_criminal_name_empty_string_returns_false(self, builder):
        assert builder.validate_input(make_minimal_data(criminal_name="")) is False

    def test_criminal_name_not_string(self, builder):
        assert builder.validate_input(make_minimal_data(criminal_name=123)) is False

    def test_criminal_name_none(self, builder):
        assert builder.validate_input(make_minimal_data(criminal_name=None)) is False

    def test_criminal_faction_not_string(self, builder):
        assert builder.validate_input(make_minimal_data(criminal_faction=42)) is False

    def test_criminal_faction_none(self, builder):
        assert builder.validate_input(make_minimal_data(criminal_faction=None)) is False

    def test_division_not_string(self, builder):
        assert builder.validate_input(make_minimal_data(division=1)) is False

    def test_division_none(self, builder):
        assert builder.validate_input(make_minimal_data(division=None)) is False

    def test_tech_level_not_int(self, builder):
        assert builder.validate_input(make_minimal_data(tech_level="5")) is False

    def test_tech_level_none(self, builder):
        assert builder.validate_input(make_minimal_data(tech_level=None)) is False

    def test_reward_not_int(self, builder):
        assert builder.validate_input(make_minimal_data(reward="50000")) is False

    def test_reward_none(self, builder):
        assert builder.validate_input(make_minimal_data(reward=None)) is False

    def test_route_not_list(self, builder):
        assert builder.validate_input(make_minimal_data(route="Pan, Mido")) is False

    def test_route_empty_list_returns_false(self, builder):
        assert builder.validate_input(make_minimal_data(route=[])) is False

    def test_route_none(self, builder):
        assert builder.validate_input(make_minimal_data(route=None)) is False

    def test_end_time_unix_not_int(self, builder):
        assert builder.validate_input(make_minimal_data(end_time_unix="1700000000")) is False

    def test_end_time_unix_none(self, builder):
        assert builder.validate_input(make_minimal_data(end_time_unix=None)) is False


# ===========================================================================
# Tests for build_payload() — structural correctness
# ===========================================================================


class TestBuildPayloadStructure:
    """build_payload() returns a dict with the correct top-level keys and embed structure."""

    @pytest.fixture()
    def builder(self):
        from message_builders.builders.bounty_announcement import BountyAnnouncementBuilder

        return BountyAnnouncementBuilder()

    def test_returns_dict(self, builder):
        result = builder.build_payload(make_minimal_data())
        assert isinstance(result, dict)

    def test_has_content_key(self, builder):
        result = builder.build_payload(make_minimal_data())
        assert "content" in result

    def test_has_embed_key(self, builder):
        result = builder.build_payload(make_minimal_data())
        assert "embed" in result

    def test_embed_is_dict(self, builder):
        result = builder.build_payload(make_minimal_data())
        assert isinstance(result["embed"], dict)

    def test_embed_has_title(self, builder):
        result = builder.build_payload(make_minimal_data())
        assert "title" in result["embed"]

    def test_embed_has_color(self, builder):
        result = builder.build_payload(make_minimal_data())
        assert "color" in result["embed"]

    def test_embed_has_fields(self, builder):
        result = builder.build_payload(make_minimal_data())
        assert "fields" in result["embed"]
        assert isinstance(result["embed"]["fields"], list)

    def test_embed_has_footer_text(self, builder):
        result = builder.build_payload(make_minimal_data())
        assert "footer_text" in result["embed"]

    def test_embed_has_seven_fields(self, builder):
        """Expect 7 fields: Difficulty, Reward Pool, Bounty Ends, Ship, Loadout, Route, Checked Systems."""
        result = builder.build_payload(make_minimal_data())
        assert len(result["embed"]["fields"]) == 7

    def test_field_names_in_order(self, builder):
        result = builder.build_payload(make_minimal_data())
        field_names = [f["name"] for f in result["embed"]["fields"]]
        assert field_names == [
            "Difficulty",
            "Reward Pool",
            "Bounty Ends",
            "Ship",
            "Loadout",
            "Route",
            "Checked Systems",
        ]

    def test_raises_value_error_on_invalid_input(self, builder):
        with pytest.raises(ValueError):
            builder.build_payload({})

    def test_raises_value_error_missing_criminal_name(self, builder):
        data = make_minimal_data()
        del data["criminal_name"]
        with pytest.raises(ValueError):
            builder.build_payload(data)


# ===========================================================================
# Tests for build_payload() — embed title and footer
# ===========================================================================


class TestBuildPayloadTitleAndFooter:
    """Embed title = criminal_name; footer_text = criminal_faction."""

    @pytest.fixture()
    def builder(self):
        from message_builders.builders.bounty_announcement import BountyAnnouncementBuilder

        return BountyAnnouncementBuilder()

    def test_title_equals_criminal_name(self, builder):
        result = builder.build_payload(make_minimal_data(criminal_name="Trent Jameson"))
        assert result["embed"]["title"] == "Trent Jameson"

    def test_footer_text_equals_criminal_faction(self, builder):
        result = builder.build_payload(make_minimal_data(criminal_faction="Terran"))
        assert result["embed"]["footer_text"] == "Terran"


# ===========================================================================
# Tests for build_payload() — faction colors
# ===========================================================================


class TestBuildPayloadFactionColors:
    """Correct integer color per faction; case-insensitive; default for unknown."""

    @pytest.fixture()
    def builder(self):
        from message_builders.builders.bounty_announcement import BountyAnnouncementBuilder

        return BountyAnnouncementBuilder()

    def test_terran_color(self, builder):
        result = builder.build_payload(make_minimal_data(criminal_faction="Terran"))
        assert result["embed"]["color"] == 15844367  # #F1C40F

    def test_vossk_color(self, builder):
        result = builder.build_payload(make_minimal_data(criminal_faction="Vossk"))
        assert result["embed"]["color"] == 1752220  # #1ABC9C

    def test_midorian_color(self, builder):
        result = builder.build_payload(make_minimal_data(criminal_faction="Midorian"))
        assert result["embed"]["color"] == 10038562  # #992D22

    def test_nivelian_color(self, builder):
        result = builder.build_payload(make_minimal_data(criminal_faction="Nivelian"))
        assert result["embed"]["color"] == 2123412  # #206694

    def test_unknown_faction_default_color(self, builder):
        result = builder.build_payload(make_minimal_data(criminal_faction="Unknown"))
        assert result["embed"]["color"] == 10181046  # #9B59B6

    def test_empty_faction_default_color(self, builder):
        result = builder.build_payload(make_minimal_data(criminal_faction=""))
        assert result["embed"]["color"] == 10181046

    def test_terran_case_insensitive_lowercase(self, builder):
        result = builder.build_payload(make_minimal_data(criminal_faction="terran"))
        assert result["embed"]["color"] == 15844367

    def test_terran_case_insensitive_uppercase(self, builder):
        result = builder.build_payload(make_minimal_data(criminal_faction="TERRAN"))
        assert result["embed"]["color"] == 15844367

    def test_vossk_case_insensitive_mixed(self, builder):
        result = builder.build_payload(make_minimal_data(criminal_faction="vOsSk"))
        assert result["embed"]["color"] == 1752220

    def test_midorian_case_insensitive(self, builder):
        result = builder.build_payload(make_minimal_data(criminal_faction="MIDORIAN"))
        assert result["embed"]["color"] == 10038562

    def test_nivelian_case_insensitive(self, builder):
        result = builder.build_payload(make_minimal_data(criminal_faction="nivelian"))
        assert result["embed"]["color"] == 2123412


# ===========================================================================
# Tests for build_payload() — inline fields: Difficulty, Reward Pool, Bounty Ends
# ===========================================================================


class TestBuildPayloadInlineFields:
    """Difficulty, Reward Pool, Bounty Ends fields — formatting and inline flag."""

    @pytest.fixture()
    def builder(self):
        from message_builders.builders.bounty_announcement import BountyAnnouncementBuilder

        return BountyAnnouncementBuilder()

    def _get_field(self, result, name):
        fields = result["embed"]["fields"]
        for f in fields:
            if f["name"] == name:
                return f
        raise KeyError(f"Field '{name}' not found in {[f['name'] for f in fields]}")

    def test_difficulty_format(self, builder):
        result = builder.build_payload(make_minimal_data(tech_level=5))
        field = self._get_field(result, "Difficulty")
        assert field["value"] == "T5"

    def test_difficulty_different_level(self, builder):
        result = builder.build_payload(make_minimal_data(tech_level=12))
        field = self._get_field(result, "Difficulty")
        assert field["value"] == "T12"

    def test_difficulty_is_inline(self, builder):
        result = builder.build_payload(make_minimal_data())
        field = self._get_field(result, "Difficulty")
        assert field["inline"] is True

    def test_reward_pool_format_with_comma(self, builder):
        result = builder.build_payload(make_minimal_data(reward=50000))
        field = self._get_field(result, "Reward Pool")
        assert field["value"] == "50,000 credits"

    def test_reward_pool_large_number(self, builder):
        result = builder.build_payload(make_minimal_data(reward=1234567))
        field = self._get_field(result, "Reward Pool")
        assert field["value"] == "1,234,567 credits"

    def test_reward_pool_small_number(self, builder):
        result = builder.build_payload(make_minimal_data(reward=100))
        field = self._get_field(result, "Reward Pool")
        assert field["value"] == "100 credits"

    def test_reward_pool_is_inline(self, builder):
        result = builder.build_payload(make_minimal_data())
        field = self._get_field(result, "Reward Pool")
        assert field["inline"] is True

    def test_bounty_ends_discord_timestamp(self, builder):
        result = builder.build_payload(make_minimal_data(end_time_unix=1700000000))
        field = self._get_field(result, "Bounty Ends")
        assert field["value"] == "<t:1700000000:R>"

    def test_bounty_ends_different_timestamp(self, builder):
        result = builder.build_payload(make_minimal_data(end_time_unix=9999999))
        field = self._get_field(result, "Bounty Ends")
        assert field["value"] == "<t:9999999:R>"

    def test_bounty_ends_is_inline(self, builder):
        result = builder.build_payload(make_minimal_data())
        field = self._get_field(result, "Bounty Ends")
        assert field["inline"] is True


# ===========================================================================
# Tests for build_payload() — thumbnail and image URLs
# ===========================================================================


class TestBuildPayloadImageFields:
    """thumbnail_url and image_url are set or absent based on optional inputs."""

    @pytest.fixture()
    def builder(self):
        from message_builders.builders.bounty_announcement import BountyAnnouncementBuilder

        return BountyAnnouncementBuilder()

    def test_thumbnail_url_set_when_criminal_icon_provided(self, builder):
        url = "https://example.com/icon.png"
        result = builder.build_payload(make_minimal_data(criminal_icon=url))
        assert result["embed"]["thumbnail_url"] == url

    def test_thumbnail_url_none_when_criminal_icon_none(self, builder):
        result = builder.build_payload(make_minimal_data(criminal_icon=None))
        assert result["embed"]["thumbnail_url"] is None

    def test_image_url_set_when_route_map_provided(self, builder):
        url = "https://cdn.example.com/map.png"
        result = builder.build_payload(make_minimal_data(route_map_url=url))
        assert result["embed"]["image_url"] == url

    def test_image_url_none_when_route_map_none(self, builder):
        result = builder.build_payload(make_minimal_data(route_map_url=None))
        assert result["embed"]["image_url"] is None


# ===========================================================================
# Tests for build_payload() — Loadout field
# ===========================================================================


class TestBuildPayloadLoadoutField:
    """Loadout field is formatted correctly based on criminal_ship data.

    After the embed redesign:
    - 'Ship' field contains: **{ship_name}** — Armor: X | Shield: Y | Total HP: Z | DPS: N
    - 'Loadout' field contains: bold category headers + item names (no emoji, no command hint)
    """

    @pytest.fixture()
    def builder(self):
        from message_builders.builders.bounty_announcement import BountyAnnouncementBuilder

        return BountyAnnouncementBuilder()

    def _get_loadout_field(self, result):
        for f in result["embed"]["fields"]:
            if f["name"] == "Loadout":
                return f
        raise KeyError("Loadout field not found")

    def _get_ship_field(self, result):
        for f in result["embed"]["fields"]:
            if f["name"] == "Ship":
                return f
        raise KeyError("Ship field not found")

    def test_no_ship_data_shows_fallback_in_ship_field(self, builder):
        """Ship field shows fallback when criminal_ship is None."""
        result = builder.build_payload(make_minimal_data(criminal_ship=None))
        field = self._get_ship_field(result)
        assert field["value"] == "*No ship data available*"

    def test_no_loadout_data_shows_fallback(self, builder):
        """Loadout field shows fallback when criminal_ship is None."""
        result = builder.build_payload(make_minimal_data(criminal_ship=None))
        field = self._get_loadout_field(result)
        assert field["value"] == "*No loadout data available*"

    def test_loadout_not_inline(self, builder):
        result = builder.build_payload(make_minimal_data(criminal_ship=None))
        field = self._get_loadout_field(result)
        assert field["inline"] is False

    def test_ship_field_contains_ship_name_bold(self, builder):
        """Ship field value starts with **{ship_name}**."""
        ship = make_full_criminal_ship()  # ship_name="Nemesis"
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_ship_field(result)
        assert "**Nemesis**" in field["value"]

    def test_ship_field_contains_hp_and_dps(self, builder):
        """Ship field shows Armor, Shield, Total HP, and DPS for the ship."""
        ship = make_full_criminal_ship()  # armor_hp=360, shield_hp=380, total_hp=740, DPS=75
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_ship_field(result)
        assert "360" in field["value"]  # armor_hp
        assert "380" in field["value"]  # shield_hp
        assert "740" in field["value"]  # total_hp
        assert "DPS: 75" in field["value"]

    def test_ship_field_no_emoji_prefix(self, builder):
        """Ship field does NOT include the ship_emoji prefix."""
        ship = make_full_criminal_ship()  # ship_emoji="<:nemesis:123>"
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_ship_field(result)
        # Emoji must NOT appear in the Ship field
        assert "<:nemesis:123>" not in field["value"]

    def test_loadout_total_dps_is_sum(self, builder):
        """Ship field DPS = sum of all weapon DPS values."""
        ship = {
            "ship_name": "Scout",
            "ship_emoji": None,
            "armour": 100,
            "weapons": [
                {"name": "Gun A", "emoji": None, "dps": 10.0},
                {"name": "Gun B", "emoji": None, "dps": 20.0},
                {"name": "Gun C", "emoji": None, "dps": 5.5},
            ],
            "modules": [],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_ship_field(result)
        assert "DPS: 35.5" in field["value"]

    def test_loadout_weapon_name_appears_in_loadout(self, builder):
        """Weapon name appears in the Loadout field (no emoji prefix)."""
        ship = {
            "ship_name": "Scout",
            "ship_emoji": None,
            "armour": 100,
            "weapons": [
                {"name": "Nirai Impulse EX", "emoji": "<:nirai:456>", "dps": 45.0},
            ],
            "modules": [],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        assert "Nirai Impulse EX" in field["value"]
        # Emoji appears after item name in loadout field
        assert "<:nirai:456>" in field["value"]

    def test_loadout_weapon_no_bullet_prefix(self, builder):
        """In the new design, weapon items appear as plain names (no emoji, no bullet)."""
        ship = {
            "ship_name": "Scout",
            "ship_emoji": None,
            "armour": 100,
            "weapons": [
                {"name": "Laser MK2", "emoji": None, "dps": 30.0},
            ],
            "modules": [],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        lines = field["value"].split("\n")
        weapon_lines = [ln for ln in lines if "Laser MK2" in ln]
        assert len(weapon_lines) == 1
        # New design: plain name with no bullet prefix
        assert weapon_lines[0] == "Laser MK2"

    def test_loadout_module_name_appears_in_loadout(self, builder):
        """Module name appears in the Loadout field (no emoji prefix)."""
        ship = {
            "ship_name": "Scout",
            "ship_emoji": None,
            "armour": 100,
            "weapons": [],
            "modules": [
                {"name": "Rhoda Blackhole", "emoji": "<:rhoda:789>"},
            ],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        assert "Rhoda Blackhole" in field["value"]
        # Emoji appears after item name in loadout field
        assert "<:rhoda:789>" in field["value"]

    def test_loadout_module_plain_name(self, builder):
        """Module without emoji: shown as plain name (no bullet prefix)."""
        ship = {
            "ship_name": "Scout",
            "ship_emoji": None,
            "armour": 100,
            "weapons": [],
            "modules": [
                {"name": "Shield Booster", "emoji": None},
            ],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        lines = field["value"].split("\n")
        module_lines = [ln for ln in lines if "Shield Booster" in ln]
        assert len(module_lines) == 1
        # New design: plain name with no prefix
        assert module_lines[0] == "Shield Booster"

    def test_loadout_no_criminal_loadout_command_hint(self, builder):
        """Loadout field does NOT contain the /criminal-loadout command hint."""
        ship = make_full_criminal_ship()
        result = builder.build_payload(make_minimal_data(criminal_ship=ship, criminal_name="Trent Jameson"))
        field = self._get_loadout_field(result)
        assert "/criminal-loadout" not in field["value"]

    def test_loadout_bold_category_headers(self, builder):
        """Loadout field uses **bold** category headers (no emoji)."""
        ship = make_full_criminal_ship()  # has weapons and modules
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        assert "**Primary Weapons**" in field["value"]
        assert "**Modules**" in field["value"]
        # No emoji before category header
        assert "🔫" not in field["value"]
        assert "⚙️" not in field["value"]

    def test_loadout_full_structure_weapons_and_modules(self, builder):
        """Full loadout with 2 weapons and 2 modules produces correct non-empty lines."""
        ship = make_full_criminal_ship()  # 2 weapons + 2 modules (no turrets)
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        lines = [ln for ln in field["value"].split("\n") if ln.strip()]
        # "**Primary Weapons**" + 2 weapons + "**Modules**" + 2 modules = 6 non-empty lines
        assert len(lines) == 6

    def test_loadout_empty_weapons_and_modules_shows_no_equipment(self, builder):
        """criminal_ship with empty weapons/modules shows '*No equipment*' in loadout."""
        ship = {
            "ship_name": "Speeder",
            "ship_emoji": None,
            "armour": 50,
            "weapons": [],
            "modules": [],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        assert "*No equipment*" in field["value"]

    def test_ship_field_hp_shows_armor_and_shield_when_both_present(self, builder):
        """Ship field shows Armor, Shield, and Total HP when shield_hp > 0."""
        ship = {
            "ship_name": "Phantom",
            "ship_emoji": None,
            "ship_armour": 100,
            "armor_hp": 260,
            "shield_hp": 380,
            "total_hp": 640,
            "weapons": [],
            "modules": [],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_ship_field(result)
        assert "260" in field["value"]  # armor_hp
        assert "380" in field["value"]  # shield_hp
        assert "640" in field["value"]  # total_hp

    def test_ship_field_hp_no_shield_shows_armor_and_total(self, builder):
        """Ship field shows Armor and Total HP when shield_hp is 0."""
        ship = {
            "ship_name": "Scout",
            "ship_emoji": None,
            "ship_armour": 100,
            "armor_hp": 260,
            "shield_hp": 0,
            "total_hp": 260,
            "weapons": [],
            "modules": [],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_ship_field(result)
        assert "260" in field["value"]  # armor_hp
        assert "Shield" not in field["value"]

    def test_ship_field_hp_fallback_to_legacy_armour(self, builder):
        """Ship field falls back to legacy ship_armour if armor_hp missing."""
        ship = {
            "ship_name": "OldShip",
            "ship_emoji": None,
            "ship_armour": 175,
            # No armor_hp / shield_hp / total_hp keys
            "weapons": [],
            "modules": [],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_ship_field(result)
        assert "175" in field["value"]


# ===========================================================================
# Tests for _build_loadout_value() — categorized sections
# ===========================================================================


class TestBuildLoadoutCategorized:
    """Loadout field groups items under bold section headers (no emoji) by category."""

    @pytest.fixture()
    def builder(self):
        from message_builders.builders.bounty_announcement import BountyAnnouncementBuilder

        return BountyAnnouncementBuilder()

    def _get_loadout_field(self, result):
        for f in result["embed"]["fields"]:
            if f["name"] == "Loadout":
                return f
        raise KeyError("Loadout field not found")

    def test_primary_weapons_bold_header_shown_when_weapons_present(self, builder):
        """'**Primary Weapons**' header appears when weapons list is non-empty."""
        ship = {
            "ship_name": "Scout",
            "ship_emoji": None,
            "armour": 100,
            "weapons": [{"name": "Gun A", "emoji": None, "dps": 10.0}],
            "modules": [],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        assert "**Primary Weapons**" in field["value"]

    def test_primary_weapons_header_absent_when_no_weapons(self, builder):
        """'**Primary Weapons**' header is omitted when weapons list is empty."""
        ship = {
            "ship_name": "Scout",
            "ship_emoji": None,
            "armour": 100,
            "weapons": [],
            "modules": [{"name": "Shield Booster", "emoji": None}],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        assert "**Primary Weapons**" not in field["value"]

    def test_modules_bold_header_shown_when_modules_present(self, builder):
        """'**Modules**' header appears when modules list is non-empty."""
        ship = {
            "ship_name": "Scout",
            "ship_emoji": None,
            "armour": 100,
            "weapons": [],
            "modules": [{"name": "Shield Booster", "emoji": None}],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        assert "**Modules**" in field["value"]

    def test_modules_header_absent_when_no_modules(self, builder):
        """'**Modules**' header is omitted when modules list is empty."""
        ship = {
            "ship_name": "Scout",
            "ship_emoji": None,
            "armour": 100,
            "weapons": [{"name": "Gun A", "emoji": None, "dps": 10.0}],
            "modules": [],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        assert "**Modules**" not in field["value"]

    def test_turrets_bold_header_shown_when_turrets_present(self, builder):
        """'**Turrets**' header appears when turrets list is non-empty."""
        ship = {
            "ship_name": "Scout",
            "ship_emoji": None,
            "armour": 100,
            "weapons": [],
            "modules": [],
            "turrets": [{"name": "Auto Turret", "emoji": None, "dps": 5.0}],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        assert "**Turrets**" in field["value"]

    def test_turrets_header_absent_when_no_turrets(self, builder):
        """'**Turrets**' header is omitted when turrets list is empty."""
        ship = {
            "ship_name": "Scout",
            "ship_emoji": None,
            "armour": 100,
            "weapons": [{"name": "Gun A", "emoji": None, "dps": 10.0}],
            "modules": [],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        assert "**Turrets**" not in field["value"]

    def test_items_appear_as_plain_names_no_bullet(self, builder):
        """Items appear as plain names — no emoji prefix, no bullet prefix."""
        ship = {
            "ship_name": "Scout",
            "ship_emoji": None,
            "armour": 100,
            "weapons": [{"name": "Bare Gun", "emoji": None, "dps": 5.0}],
            "modules": [],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        lines = field["value"].split("\n")
        item_lines = [ln for ln in lines if "Bare Gun" in ln]
        assert len(item_lines) == 1
        # New design: plain name, no bullet
        assert item_lines[0] == "Bare Gun"

    def test_items_with_emoji_suffixed_by_emoji(self, builder):
        """Items with emoji get the emoji AFTER the item name."""
        ship = {
            "ship_name": "Scout",
            "ship_emoji": None,
            "armour": 100,
            "weapons": [],
            "modules": [{"name": "Boost Module", "emoji": "<:boost:999>"}],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        lines = field["value"].split("\n")
        item_lines = [ln for ln in lines if "Boost Module" in ln]
        assert len(item_lines) == 1
        # Emoji appears after item name
        assert "<:boost:999>" in item_lines[0]
        assert item_lines[0] == "Boost Module <:boost:999>"

    def test_section_order_weapons_then_turrets_then_modules(self, builder):
        """Sections appear in order: Primary Weapons → Turrets → Modules."""
        ship = {
            "ship_name": "Nemesis",
            "ship_emoji": None,
            "armour": 100,
            "weapons": [{"name": "Gun", "emoji": None, "dps": 10.0}],
            "modules": [{"name": "Module X", "emoji": None}],
            "turrets": [{"name": "Turret Y", "emoji": None, "dps": 5.0}],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        value = field["value"]
        weapons_pos = value.index("**Primary Weapons**")
        turrets_pos = value.index("**Turrets**")
        modules_pos = value.index("**Modules**")
        assert weapons_pos < turrets_pos < modules_pos

    def test_only_modules_section_when_no_weapons_no_turrets(self, builder):
        """When only modules exist, only '**Modules**' section header appears."""
        ship = {
            "ship_name": "Hauler",
            "ship_emoji": None,
            "armour": 80,
            "weapons": [],
            "modules": [{"name": "Cargo Ext", "emoji": None}],
            "turrets": [],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        assert "**Modules**" in field["value"]
        assert "**Primary Weapons**" not in field["value"]
        assert "**Turrets**" not in field["value"]

    def test_turret_plain_name_no_prefix(self, builder):
        """Turrets appear as plain names (no emoji, no bullet)."""
        ship = {
            "ship_name": "Scout",
            "ship_emoji": None,
            "armour": 100,
            "weapons": [],
            "modules": [],
            "turrets": [{"name": "Basic Turret", "emoji": None, "dps": 8.0}],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        lines = field["value"].split("\n")
        item_lines = [ln for ln in lines if "Basic Turret" in ln]
        assert len(item_lines) == 1
        assert item_lines[0] == "Basic Turret"

    def test_all_sections_present_when_all_filled(self, builder):
        """All three bold section headers appear when all categories have items."""
        ship = {
            "ship_name": "Battlecruiser",
            "ship_emoji": "<:bc:101>",
            "armor_hp": 500,
            "shield_hp": 300,
            "total_hp": 800,
            "weapons": [{"name": "Main Gun", "emoji": None, "dps": 20.0}],
            "modules": [{"name": "Armor Plate", "emoji": None}],
            "turrets": [{"name": "Side Turret", "emoji": None, "dps": 10.0}],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        assert "**Primary Weapons**" in field["value"]
        assert "**Turrets**" in field["value"]
        assert "**Modules**" in field["value"]

    def test_empty_ship_shows_no_equipment(self, builder):
        """A ship with no weapons/modules/turrets shows '*No equipment*' in loadout."""
        ship = {
            "ship_name": "Empty",
            "ship_emoji": None,
            "armour": 50,
            "weapons": [],
            "modules": [],
            "turrets": [],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        assert "**Primary Weapons**" not in field["value"]
        assert "**Turrets**" not in field["value"]
        assert "**Modules**" not in field["value"]
        assert "*No equipment*" in field["value"]

    def test_sections_separated_by_blank_lines(self, builder):
        """Categories are separated by a blank line in the loadout value."""
        ship = {
            "ship_name": "Nemesis",
            "ship_emoji": None,
            "armour": 100,
            "weapons": [{"name": "Gun", "emoji": None, "dps": 10.0}],
            "modules": [{"name": "Module X", "emoji": None}],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        # Blank line separates sections
        assert "\n\n" in field["value"]


# ===========================================================================
# Tests for build_payload() — Route field
# ===========================================================================


class TestBuildPayloadRouteField:
    """Route field formatting: comma-separated systems with optional checked markup."""

    @pytest.fixture()
    def builder(self):
        from message_builders.builders.bounty_announcement import BountyAnnouncementBuilder

        return BountyAnnouncementBuilder()

    def _get_route_field(self, result):
        for f in result["embed"]["fields"]:
            if f["name"] == "Route":
                return f
        raise KeyError("Route field not found")

    def test_route_comma_separated_no_checked(self, builder):
        result = builder.build_payload(make_minimal_data(route=["Pan", "Mido", "Pescal Ansen"], checked=None))
        field = self._get_route_field(result)
        assert field["value"] == "Pan, Mido, Pescal Ansen"

    def test_route_single_system(self, builder):
        result = builder.build_payload(make_minimal_data(route=["Solo"], checked=None))
        field = self._get_route_field(result)
        assert field["value"] == "Solo"

    def test_route_not_inline(self, builder):
        result = builder.build_payload(make_minimal_data())
        field = self._get_route_field(result)
        assert field["inline"] is False

    def test_route_checked_system_strikethrough(self, builder):
        """Systems with value 'checked' appear as ~~SystemName~~."""
        result = builder.build_payload(
            make_minimal_data(
                route=["Pan", "Mido", "Pescal Ansen"],
                checked={"Pescal Ansen": "checked"},
            )
        )
        field = self._get_route_field(result)
        assert "~~Pescal Ansen~~" in field["value"]
        # Non-checked systems appear plain
        assert "Pan" in field["value"]
        assert "Mido" in field["value"]

    def test_route_found_system_bold(self, builder):
        """Systems with value 'found' appear as **SystemName**."""
        result = builder.build_payload(
            make_minimal_data(
                route=["Pan", "Mido", "Pescal Ansen"],
                checked={"Mido": "found"},
            )
        )
        field = self._get_route_field(result)
        assert "**Mido**" in field["value"]

    def test_route_unchecked_system_plain(self, builder):
        """Systems not in the checked dict appear as plain text."""
        result = builder.build_payload(
            make_minimal_data(
                route=["Pan", "Mido", "Pescal Ansen"],
                checked={"Mido": "checked"},
            )
        )
        field = self._get_route_field(result)
        # Pan is not in checked dict — should be plain
        assert "Pan" in field["value"]
        assert "~~Pan~~" not in field["value"]
        assert "**Pan**" not in field["value"]

    def test_route_multiple_checked_systems(self, builder):
        """Multiple checked systems are all formatted with strikethrough."""
        result = builder.build_payload(
            make_minimal_data(
                route=["Pan", "Mido", "Pescal Ansen"],
                checked={"Pan": "checked", "Pescal Ansen": "checked"},
            )
        )
        field = self._get_route_field(result)
        assert "~~Pan~~" in field["value"]
        assert "~~Pescal Ansen~~" in field["value"]
        # Mido is plain
        assert "Mido" in field["value"]

    def test_route_empty_checked_dict_treats_as_no_checked(self, builder):
        """An empty checked dict produces plain route — no markup."""
        result = builder.build_payload(
            make_minimal_data(
                route=["Pan", "Mido"],
                checked={},
            )
        )
        field = self._get_route_field(result)
        assert field["value"] == "Pan, Mido"

    def test_route_recently_spotted_system_bold_and_strikethrough(self, builder):
        """Systems with value 'recently_spotted' appear as **~~SystemName~~**."""
        result = builder.build_payload(
            make_minimal_data(
                route=["Pan", "Mido", "Pescal Ansen"],
                checked={"Mido": "recently_spotted"},
            )
        )
        field = self._get_route_field(result)
        assert "**~~Mido~~**" in field["value"]
        # Other systems plain
        assert "Pan" in field["value"]
        assert "~~Pan~~" not in field["value"]

    def test_route_recently_spotted_vs_checked_vs_found(self, builder):
        """Mixed statuses produce correct formatting for each system."""
        result = builder.build_payload(
            make_minimal_data(
                route=["A", "B", "C", "D"],
                checked={"A": "checked", "B": "recently_spotted", "C": "found"},
            )
        )
        field = self._get_route_field(result)
        assert "~~A~~" in field["value"]
        assert "**~~B~~**" in field["value"]
        assert "**C**" in field["value"]
        assert "D" in field["value"]  # plain
        assert "~~D~~" not in field["value"]
        assert "**D**" not in field["value"]


# ===========================================================================
# Tests for build_payload() — Checked Systems field
# ===========================================================================


class TestBuildPayloadCheckedSystemsField:
    """Checked Systems field formatting."""

    @pytest.fixture()
    def builder(self):
        from message_builders.builders.bounty_announcement import BountyAnnouncementBuilder

        return BountyAnnouncementBuilder()

    def _get_checked_field(self, result):
        for f in result["embed"]["fields"]:
            if f["name"] == "Checked Systems":
                return f
        raise KeyError("Checked Systems field not found")

    def test_no_checked_systems_shows_fallback(self, builder):
        result = builder.build_payload(make_minimal_data(checked=None))
        field = self._get_checked_field(result)
        assert "*No systems checked yet*" in field["value"]

    def test_empty_checked_dict_shows_fallback(self, builder):
        result = builder.build_payload(make_minimal_data(checked={}))
        field = self._get_checked_field(result)
        assert "*No systems checked yet*" in field["value"]

    def test_checked_systems_not_inline(self, builder):
        result = builder.build_payload(make_minimal_data())
        field = self._get_checked_field(result)
        assert field["inline"] is False

    def test_checked_system_strikethrough_in_checked_field(self, builder):
        """A 'checked' system appears with strikethrough."""
        result = builder.build_payload(
            make_minimal_data(
                route=["Pan", "Mido"],
                checked={"Pan": "checked"},
            )
        )
        field = self._get_checked_field(result)
        assert "~~Pan~~" in field["value"]

    def test_found_system_bold_in_checked_field(self, builder):
        """A 'found' system appears bold in the Checked Systems field."""
        result = builder.build_payload(
            make_minimal_data(
                route=["Pan", "Mido"],
                checked={"Mido": "found"},
            )
        )
        field = self._get_checked_field(result)
        assert "**Mido**" in field["value"]

    def test_checked_field_uses_blockquote_prefix(self, builder):
        """Checked Systems field uses '>' blockquote prefix."""
        result = builder.build_payload(
            make_minimal_data(
                route=["Pan"],
                checked={"Pan": "checked"},
            )
        )
        field = self._get_checked_field(result)
        assert field["value"].startswith(">")

    def test_fallback_uses_blockquote_prefix(self, builder):
        """Fallback 'No systems checked yet' uses '>' blockquote prefix."""
        result = builder.build_payload(make_minimal_data(checked=None))
        field = self._get_checked_field(result)
        assert field["value"].startswith(">")

    def test_multiple_checked_systems_appear(self, builder):
        result = builder.build_payload(
            make_minimal_data(
                route=["Pan", "Mido", "Pescal Ansen"],
                checked={"Pan": "checked", "Pescal Ansen": "checked"},
            )
        )
        field = self._get_checked_field(result)
        assert "~~Pan~~" in field["value"]
        assert "~~Pescal Ansen~~" in field["value"]

    def test_recently_spotted_system_bold_and_strikethrough_in_checked_field(self, builder):
        """A 'recently_spotted' system appears bold+strikethrough (**~~Name~~**) in Checked Systems."""
        result = builder.build_payload(
            make_minimal_data(
                route=["Pan", "Mido"],
                checked={"Pan": "recently_spotted"},
            )
        )
        field = self._get_checked_field(result)
        assert "**~~Pan~~**" in field["value"]

    def test_recently_spotted_uses_blockquote_prefix(self, builder):
        """Checked Systems field for recently_spotted uses '>' blockquote prefix."""
        result = builder.build_payload(
            make_minimal_data(
                route=["Pan"],
                checked={"Pan": "recently_spotted"},
            )
        )
        field = self._get_checked_field(result)
        assert field["value"].startswith(">")

    def test_recently_spotted_absent_from_checked_group(self, builder):
        """A 'recently_spotted' system is NOT shown as a plain checked (~~name~~) system."""
        result = builder.build_payload(
            make_minimal_data(
                route=["Pan", "Mido"],
                checked={"Pan": "recently_spotted"},
            )
        )
        field = self._get_checked_field(result)
        # Only strikethrough (without bold) should not appear for recently_spotted
        # i.e., "~~Pan~~" without ** surrounding it should not be present
        value = field["value"]
        # **~~Pan~~** is expected, bare ~~Pan~~ should not appear
        assert "**~~Pan~~**" in value


# ===========================================================================
# Tests for build_payload() — content / role mention
# ===========================================================================


class TestBuildPayloadContent:
    """content key: role mention or None."""

    @pytest.fixture()
    def builder(self):
        from message_builders.builders.bounty_announcement import BountyAnnouncementBuilder

        return BountyAnnouncementBuilder()

    def test_content_none_when_no_role_id(self, builder):
        result = builder.build_payload(make_minimal_data(bounty_hunter_role_id=None))
        assert result["content"] is None

    def test_content_role_mention_when_role_id_provided(self, builder):
        result = builder.build_payload(make_minimal_data(bounty_hunter_role_id=123456789))
        assert result["content"] == "<@&123456789>"

    def test_content_role_mention_different_id(self, builder):
        result = builder.build_payload(make_minimal_data(bounty_hunter_role_id=987654321))
        assert result["content"] == "<@&987654321>"


# ===========================================================================
# Tests for extract_data()
# ===========================================================================


class TestExtractData:
    """extract_data() extracts criminal_name and criminal_faction from a JSON payload string."""

    @pytest.fixture()
    def builder(self):
        from message_builders.builders.bounty_announcement import BountyAnnouncementBuilder

        return BountyAnnouncementBuilder()

    def _make_payload_str(self, name: str, faction: str) -> str:
        payload = {
            "content": None,
            "embed": {
                "title": name,
                "footer_text": faction,
                "color": 15844367,
                "fields": [],
            },
        }
        return json.dumps(payload)

    def test_extracts_criminal_name(self, builder):
        result = builder.extract_data(self._make_payload_str("Trent Jameson", "Terran"))
        assert result is not None
        assert result["criminal_name"] == "Trent Jameson"

    def test_extracts_criminal_faction(self, builder):
        result = builder.extract_data(self._make_payload_str("Trent Jameson", "Vossk"))
        assert result is not None
        assert result["criminal_faction"] == "Vossk"

    def test_returns_dict_with_two_keys(self, builder):
        result = builder.extract_data(self._make_payload_str("A", "B"))
        assert result is not None
        assert set(result.keys()) == {"criminal_name", "criminal_faction"}

    def test_returns_none_for_invalid_json(self, builder):
        result = builder.extract_data("{not valid json")
        assert result is None

    def test_returns_none_for_empty_string(self, builder):
        result = builder.extract_data("")
        assert result is None

    def test_returns_none_for_missing_embed_key(self, builder):
        payload = json.dumps({"content": None})
        result = builder.extract_data(payload)
        assert result is None

    def test_returns_none_for_missing_title(self, builder):
        payload = json.dumps({"embed": {"footer_text": "Terran"}})
        result = builder.extract_data(payload)
        assert result is None

    def test_returns_none_for_missing_footer_text(self, builder):
        payload = json.dumps({"embed": {"title": "Trent Jameson"}})
        result = builder.extract_data(payload)
        assert result is None

    def test_returns_none_for_empty_json_object(self, builder):
        result = builder.extract_data("{}")
        assert result is None


# ===========================================================================
# Tests for roundtrip: build_payload → json.dumps → extract_data
# ===========================================================================


class TestBuildExtractRoundtrip:
    """build_payload() → json.dumps → extract_data() returns the original name/faction."""

    @pytest.fixture()
    def builder(self):
        from message_builders.builders.bounty_announcement import BountyAnnouncementBuilder

        return BountyAnnouncementBuilder()

    def test_roundtrip_criminal_name(self, builder):
        data = make_minimal_data(criminal_name="Trent Jameson", criminal_faction="Terran")
        payload = builder.build_payload(data)
        extracted = builder.extract_data(json.dumps(payload))
        assert extracted is not None
        assert extracted["criminal_name"] == "Trent Jameson"

    def test_roundtrip_criminal_faction(self, builder):
        data = make_minimal_data(criminal_name="Trent Jameson", criminal_faction="Vossk")
        payload = builder.build_payload(data)
        extracted = builder.extract_data(json.dumps(payload))
        assert extracted is not None
        assert extracted["criminal_faction"] == "Vossk"

    def test_roundtrip_with_full_data(self, builder):
        data = make_minimal_data(
            criminal_name="Ghost Pirate",
            criminal_faction="Nivelian",
            criminal_ship=make_full_criminal_ship(),
            checked={"Pan": "checked", "Mido": "found"},
            bounty_hunter_role_id=111222333,
            route_map_url="https://cdn.example.com/map.png",
        )
        payload = builder.build_payload(data)
        extracted = builder.extract_data(json.dumps(payload))
        assert extracted is not None
        assert extracted["criminal_name"] == "Ghost Pirate"
        assert extracted["criminal_faction"] == "Nivelian"


# ===========================================================================
# Tests for MessageBuilderFactory integration
# ===========================================================================


class TestFactoryIntegration:
    """BountyAnnouncementBuilder is registered and creatable via the factory."""

    def test_factory_creates_bounty_announcement_builder(self):
        from message_builders.builders.bounty_announcement import BountyAnnouncementBuilder
        from message_builders.factory import MessageBuilderFactory

        builder = MessageBuilderFactory.create_builder("bounty_announcement")
        assert isinstance(builder, BountyAnnouncementBuilder)

    def test_bounty_announcement_in_supported_types(self):
        from message_builders.factory import MessageBuilderFactory

        assert "bounty_announcement" in MessageBuilderFactory.get_supported_types()

    def test_factory_builder_validate_and_build(self):
        from message_builders.factory import MessageBuilderFactory

        builder = MessageBuilderFactory.create_builder("bounty_announcement")
        data = make_minimal_data()
        assert builder.validate_input(data) is True
        payload = builder.build_payload(data)
        assert payload["embed"]["title"] == "Trent Jameson"

    def test_factory_builder_roundtrip(self):
        from message_builders.factory import MessageBuilderFactory

        builder = MessageBuilderFactory.create_builder("bounty_announcement")
        data = make_minimal_data(criminal_name="Ghost Pirate", criminal_faction="Midorian")
        payload = builder.build_payload(data)
        extracted = builder.extract_data(json.dumps(payload))
        assert extracted is not None
        assert extracted["criminal_name"] == "Ghost Pirate"
        assert extracted["criminal_faction"] == "Midorian"


# ===========================================================================
# Tests for FACTION_COLORS module-level constant
# ===========================================================================


class TestFactionColorsConstant:
    """FACTION_COLORS is a module-level dict with correct entries."""

    def test_faction_colors_exists(self):
        from message_builders.builders import bounty_announcement

        assert hasattr(bounty_announcement, "FACTION_COLORS")

    def test_faction_colors_is_dict(self):
        from message_builders.builders import bounty_announcement

        assert isinstance(bounty_announcement.FACTION_COLORS, dict)

    def test_faction_colors_has_terran(self):
        from message_builders.builders import bounty_announcement

        assert "terran" in bounty_announcement.FACTION_COLORS

    def test_faction_colors_has_vossk(self):
        from message_builders.builders import bounty_announcement

        assert "vossk" in bounty_announcement.FACTION_COLORS

    def test_faction_colors_has_midorian(self):
        from message_builders.builders import bounty_announcement

        assert "midorian" in bounty_announcement.FACTION_COLORS

    def test_faction_colors_has_nivelian(self):
        from message_builders.builders import bounty_announcement

        assert "nivelian" in bounty_announcement.FACTION_COLORS

    def test_terran_color_value(self):
        from message_builders.builders import bounty_announcement

        assert bounty_announcement.FACTION_COLORS["terran"] == 15844367

    def test_vossk_color_value(self):
        from message_builders.builders import bounty_announcement

        assert bounty_announcement.FACTION_COLORS["vossk"] == 1752220

    def test_midorian_color_value(self):
        from message_builders.builders import bounty_announcement

        assert bounty_announcement.FACTION_COLORS["midorian"] == 10038562

    def test_nivelian_color_value(self):
        from message_builders.builders import bounty_announcement

        assert bounty_announcement.FACTION_COLORS["nivelian"] == 2123412


# ===========================================================================
# Tests for "captured" state
# ===========================================================================


class TestBuildPayloadCapturedState:
    """When captured=True is passed, the embed shows the CAPTURED state."""

    @pytest.fixture()
    def builder(self):
        from message_builders.builders.bounty_announcement import BountyAnnouncementBuilder

        return BountyAnnouncementBuilder()

    def _get_field(self, result, name):
        fields = result["embed"]["fields"]
        for f in fields:
            if f["name"] == name:
                return f
        raise KeyError(f"Field '{name}' not found in {[f['name'] for f in fields]}")

    def test_captured_title_includes_criminal_name(self, builder):
        """Title includes the criminal name when captured=True."""
        result = builder.build_payload(make_minimal_data(criminal_name="Kato Vort", captured=True))
        assert "Kato Vort" in result["embed"]["title"]

    def test_captured_title_includes_captured_indicator(self, builder):
        """Title shows 'CAPTURED' when captured=True."""
        result = builder.build_payload(make_minimal_data(criminal_name="Kato Vort", captured=True))
        assert "CAPTURED" in result["embed"]["title"]

    def test_captured_title_format(self, builder):
        """Title format is '✅ {criminal_name} — CAPTURED' when captured=True."""
        result = builder.build_payload(make_minimal_data(criminal_name="Kato Vort", captured=True))
        assert result["embed"]["title"] == "✅ Kato Vort — CAPTURED"

    def test_normal_title_no_captured_indicator(self, builder):
        """Title is just the criminal_name when captured is not set."""
        result = builder.build_payload(make_minimal_data(criminal_name="Kato Vort"))
        assert result["embed"]["title"] == "Kato Vort"
        assert "CAPTURED" not in result["embed"]["title"]

    def test_captured_color_is_green(self, builder):
        """Color is green (3066993 / 0x2ECC71) when captured=True."""
        result = builder.build_payload(make_minimal_data(captured=True))
        assert result["embed"]["color"] == 3066993

    def test_normal_color_uses_faction_color(self, builder):
        """Color uses faction color when captured is not set."""
        result = builder.build_payload(make_minimal_data(criminal_faction="Terran"))
        assert result["embed"]["color"] == 15844367  # Terran color

    def test_captured_color_overrides_faction_color(self, builder):
        """Green captured color overrides faction color even for known factions."""
        result = builder.build_payload(make_minimal_data(criminal_faction="Vossk", captured=True))
        assert result["embed"]["color"] == 3066993  # Green, not Vossk color

    def test_captured_bounty_ends_field_shows_captured(self, builder):
        """'Bounty Ends' field shows '**Captured**' when captured=True."""
        result = builder.build_payload(make_minimal_data(captured=True))
        field = self._get_field(result, "Bounty Ends")
        assert field["value"] == "**Captured**"

    def test_normal_bounty_ends_field_shows_timestamp(self, builder):
        """'Bounty Ends' field shows Discord timestamp when captured is not set."""
        result = builder.build_payload(make_minimal_data(end_time_unix=1700000000))
        field = self._get_field(result, "Bounty Ends")
        assert field["value"] == "<t:1700000000:R>"

    def test_captured_false_bounty_ends_field_shows_timestamp(self, builder):
        """'Bounty Ends' field shows timestamp when captured=False explicitly."""
        result = builder.build_payload(make_minimal_data(end_time_unix=9999999, captured=False))
        field = self._get_field(result, "Bounty Ends")
        assert field["value"] == "<t:9999999:R>"

    def test_captured_still_has_seven_fields(self, builder):
        """Captured state still produces 7 fields (no fields removed)."""
        result = builder.build_payload(make_minimal_data(captured=True))
        assert len(result["embed"]["fields"]) == 7

    def test_captured_field_names_unchanged(self, builder):
        """Field names are the same in captured state as in normal state."""
        result = builder.build_payload(make_minimal_data(captured=True))
        field_names = [f["name"] for f in result["embed"]["fields"]]
        assert field_names == [
            "Difficulty",
            "Reward Pool",
            "Bounty Ends",
            "Ship",
            "Loadout",
            "Route",
            "Checked Systems",
        ]

    def test_captured_default_is_false(self, builder):
        """When 'captured' is absent from data, embed behaves as non-captured."""
        result = builder.build_payload(make_minimal_data(criminal_name="Zara", criminal_faction="Midorian"))
        assert result["embed"]["title"] == "Zara"
        assert result["embed"]["color"] == 10038562  # Midorian color
