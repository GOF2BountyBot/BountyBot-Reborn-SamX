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

    def test_embed_has_six_fields(self, builder):
        """Expect 6 fields: Difficulty, Reward Pool, Bounty Ends, Loadout, Route, Checked Systems."""
        result = builder.build_payload(make_minimal_data())
        assert len(result["embed"]["fields"]) == 6

    def test_field_names_in_order(self, builder):
        result = builder.build_payload(make_minimal_data())
        field_names = [f["name"] for f in result["embed"]["fields"]]
        assert field_names == ["Difficulty", "Reward Pool", "Bounty Ends", "Loadout", "Route", "Checked Systems"]

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
    """Loadout field is formatted correctly based on criminal_ship data."""

    @pytest.fixture()
    def builder(self):
        from message_builders.builders.bounty_announcement import BountyAnnouncementBuilder

        return BountyAnnouncementBuilder()

    def _get_loadout_field(self, result):
        for f in result["embed"]["fields"]:
            if f["name"] == "Loadout":
                return f
        raise KeyError("Loadout field not found")

    def test_no_loadout_data_shows_fallback(self, builder):
        result = builder.build_payload(make_minimal_data(criminal_ship=None))
        field = self._get_loadout_field(result)
        assert field["value"] == "*No loadout data available*"

    def test_loadout_not_inline(self, builder):
        result = builder.build_payload(make_minimal_data(criminal_ship=None))
        field = self._get_loadout_field(result)
        assert field["inline"] is False

    def test_loadout_first_line_ship_header(self, builder):
        """First line: {ship_emoji} **{ship_name}** — Armor: X | Shield: Y | Total HP: Z | DPS: {total_dps}"""
        ship = make_full_criminal_ship()  # DPS: 45.0 + 30.0 = 75.0, armor_hp=360, shield_hp=380
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        lines = field["value"].split("\n")
        # Header line contains ship emoji, ship name, HP stats, and total DPS
        assert "<:nemesis:123>" in lines[0]
        assert "**Nemesis**" in lines[0]
        assert "360" in lines[0]  # armor_hp
        assert "380" in lines[0]  # shield_hp
        assert "740" in lines[0]  # total_hp
        assert "DPS: 75" in lines[0]  # 45 + 30

    def test_loadout_total_dps_is_sum(self, builder):
        """total_dps = sum of all weapon DPS values."""
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
        field = self._get_loadout_field(result)
        lines = field["value"].split("\n")
        assert "DPS: 35.5" in lines[0]

    def test_loadout_weapon_with_emoji(self, builder):
        """Weapon with emoji: '{emoji} {name}'."""
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
        lines = field["value"].split("\n")
        # One of the lines should contain the weapon with emoji
        weapon_lines = [ln for ln in lines if "Nirai Impulse EX" in ln]
        assert len(weapon_lines) == 1
        assert "<:nirai:456>" in weapon_lines[0]

    def test_loadout_weapon_without_emoji(self, builder):
        """Weapon without emoji: just the name on its own line."""
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
        # Should not have a leading emoji token
        assert weapon_lines[0].strip() == "Laser MK2"

    def test_loadout_module_with_emoji(self, builder):
        """Module with emoji: '{emoji} {name}'."""
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
        lines = field["value"].split("\n")
        module_lines = [ln for ln in lines if "Rhoda Blackhole" in ln]
        assert len(module_lines) == 1
        assert "<:rhoda:789>" in module_lines[0]

    def test_loadout_module_without_emoji(self, builder):
        """Module without emoji: just the name."""
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
        assert module_lines[0].strip() == "Shield Booster"

    def test_loadout_ship_without_emoji(self, builder):
        """Ship with no ship_emoji — header line omits emoji prefix."""
        ship = {
            "ship_name": "Nemesis",
            "ship_emoji": None,
            "armour": 200,
            "weapons": [],
            "modules": [],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        lines = field["value"].split("\n")
        assert "**Nemesis**" in lines[0]
        # No emoji token expected
        assert "<:" not in lines[0]

    def test_loadout_ends_with_use_command(self, builder):
        """Last line of loadout is the /criminal-loadout slash command hint."""
        ship = make_full_criminal_ship()
        result = builder.build_payload(make_minimal_data(criminal_ship=ship, criminal_name="Trent Jameson"))
        field = self._get_loadout_field(result)
        lines = field["value"].split("\n")
        last_line = lines[-1]
        assert "/criminal-loadout" in last_line
        assert "Trent Jameson" in last_line

    def test_loadout_full_structure(self, builder):
        """Full loadout with 2 weapons and 2 modules produces correct line count."""
        ship = make_full_criminal_ship()  # 2 weapons + 2 modules
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        lines = [ln for ln in field["value"].split("\n") if ln.strip()]
        # header + 2 weapons + 2 modules + command hint = 6 non-empty lines
        assert len(lines) == 6

    def test_loadout_empty_weapons_and_modules(self, builder):
        """criminal_ship with empty weapons/modules still shows header and command hint."""
        ship = {
            "ship_name": "Speeder",
            "ship_emoji": None,
            "armour": 50,
            "weapons": [],
            "modules": [],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        assert "**Speeder**" in field["value"]
        assert "/criminal-loadout" in field["value"]

    def test_loadout_hp_shows_armor_and_shield_when_both_present(self, builder):
        """Header line shows Armor, Shield, and Total HP when shield_hp > 0."""
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
        field = self._get_loadout_field(result)
        lines = field["value"].split("\n")
        assert "260" in lines[0]  # armor_hp
        assert "380" in lines[0]  # shield_hp
        assert "640" in lines[0]  # total_hp

    def test_loadout_hp_shows_simple_hp_when_no_shield(self, builder):
        """Header line shows HP: X when shield_hp is 0."""
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
        field = self._get_loadout_field(result)
        lines = field["value"].split("\n")
        assert "HP: 260" in lines[0]
        assert "Shield" not in lines[0]

    def test_loadout_hp_fallback_to_legacy_armour(self, builder):
        """Header line falls back to legacy armour/ship_armour if armor_hp missing."""
        ship = {
            "ship_name": "OldShip",
            "ship_emoji": None,
            "ship_armour": 175,
            # No armor_hp / shield_hp / total_hp keys
            "weapons": [],
            "modules": [],
        }
        result = builder.build_payload(make_minimal_data(criminal_ship=ship))
        field = self._get_loadout_field(result)
        lines = field["value"].split("\n")
        assert "HP: 175" in lines[0]


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
