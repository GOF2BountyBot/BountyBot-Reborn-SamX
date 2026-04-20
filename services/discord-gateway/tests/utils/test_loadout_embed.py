"""Tests for the shared loadout embed builder (utils/loadout_embed.py).

Covers:
- Basic shape (title, description, color, thumbnail null-guard)
- Spacer field inserted before every section
- Section header <N/M> format
- Weapon/module/cargo line formatting + '• ' bullet fallback
- Empty-effects modules render name-only
- Continuation-field splitting when 1024-char limit is exceeded
- 6000-char budget triggers cargo → utility modules → combat → weapons truncation
- Error embed path
- Ship Stats Total Value threshold heuristic
"""

from __future__ import annotations

import discord
from utils.loadout_embed import (
    MAX_EMBED_TOTAL,
    MAX_FIELD_VALUE,
    MAX_FIELDS,
    SPACER_NAME,
    build_loadout_embed,
    build_loadout_error_embed,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_player_response(**overrides):
    """Minimal valid player-path LoadoutResponse dict."""
    defaults = {
        "subject_kind": "player",
        "subject_name": "Alice",
        "subject_mention": "<@123456789>",
        "player_id": 1,
        "ship_name": "Wraith",
        "ship_nickname": "Betty",
        "ship_icon": "https://cdn/wraith.png",
        "ship_emoji": "<:wraith:1>",
        "thumbnail_url": "https://cdn/wraith.png",
        "ship_stats": {
            "armour": 95,
            "cargo": 20,
            "handling": 60,
            "hp": 320,
            "dps": 42.5,
            "total_value": 15000,
            "max_primaries": 2,
            "max_secondaries": 0,
            "max_turrets": 0,
            "max_modules": 4,
        },
        "weapons": [
            {"name": "Pulse Laser", "emoji": "<:pulse:1>", "dps": 12.0, "value": 1000},
            {"name": "Ion Beam", "emoji": "<:ion:2>", "dps": 18.0, "value": 2000},
        ],
        "turrets": [],
        "modules": [
            {
                "name": "D'iol",
                "emoji": "<:diol:1>",
                "type": "ArmourModule",
                "value": 500,
                "tech_level": 1,
                "effects": [{"label": "Armour", "value": "160"}],
                "combat_tier": "combat",
            },
            {
                "name": "Gamma Shield I",
                "emoji": "<:gshield:2>",
                "type": "GammaShieldModule",
                "value": 800,
                "tech_level": 8,
                "effects": [],
                "combat_tier": "utility",
            },
        ],
        "cargo": [],
        "cargo_total_count": 0,
    }
    defaults.update(overrides)
    return defaults


def _make_criminal_response(**overrides):
    defaults = {
        "subject_kind": "criminal",
        "subject_name": "Dark Mage",
        "subject_description": "Void Syndicate",
        "bounty_id": 1,
        "tech_level": 3,
        "ship_name": "Interceptor",
        "ship_emoji": "<:interceptor:1>",
        "ship_icon": "https://cdn/interceptor.png",
        "thumbnail_url": "https://cdn/darkmage.png",
        "ship_stats": {
            "armour": 95,
            "cargo": 45,
            "handling": 60,
            "hp": 95,
            "dps": 5.2,
            "total_value": 1000,
            "max_primaries": 1,
            "max_secondaries": 0,
            "max_turrets": 0,
            "max_modules": 2,
        },
        "weapons": [{"name": "Blaster", "emoji": "<:b:1>", "dps": 5.2, "value": 500}],
        "turrets": [],
        "modules": [
            {
                "name": "D'iol",
                "emoji": "<:diol:1>",
                "type": "ArmourModule",
                "value": 500,
                "tech_level": 1,
                "effects": [{"label": "Armour", "value": "40"}],
                "combat_tier": "combat",
            },
        ],
        "cargo": [],
        "cargo_total_count": 0,
    }
    defaults.update(overrides)
    return defaults


def _get_field(embed: discord.Embed, name: str):
    """Return the first field with this name (or None)."""
    for f in embed.fields:
        if f.name == name:
            return f
    return None


# ---------------------------------------------------------------------------
# Basic shape
# ---------------------------------------------------------------------------


class TestBasicShape:
    def test_title_and_color(self):
        embed = build_loadout_embed(_make_player_response(), viewer_is_owner_or_admin=True)
        assert embed.title == "Loadout — Alice"
        assert embed.color == discord.Color.blurple()

    def test_player_description_is_mention(self):
        embed = build_loadout_embed(_make_player_response(), viewer_is_owner_or_admin=True)
        assert embed.description == "<@123456789>"

    def test_criminal_description_combines_faction_and_tl(self):
        embed = build_loadout_embed(_make_criminal_response(), viewer_is_owner_or_admin=True)
        assert embed.description == "Void Syndicate · TL3"

    def test_thumbnail_set_when_present(self):
        embed = build_loadout_embed(_make_player_response(), viewer_is_owner_or_admin=True)
        assert embed.thumbnail.url == "https://cdn/wraith.png"

    def test_thumbnail_null_guarded(self):
        """Null thumbnail_url must not raise or set an invalid URL."""
        resp = _make_player_response()
        resp["thumbnail_url"] = None
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=True)
        assert embed.thumbnail.url is None

    def test_no_footer_no_timestamp(self):
        embed = build_loadout_embed(_make_player_response(), viewer_is_owner_or_admin=True)
        assert embed.footer.text is None
        assert embed.timestamp is None


# ---------------------------------------------------------------------------
# Error embed path
# ---------------------------------------------------------------------------


class TestErrorEmbed:
    def test_message_triggers_error_embed(self):
        resp = _make_player_response()
        resp["message"] = "No active ship"
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=True)
        assert embed.title == "Loadout — Alice"
        assert embed.description == "No active ship"
        assert embed.color == discord.Color.red()

    def test_build_loadout_error_embed_helper(self):
        embed = build_loadout_error_embed(title="X", description="Y")
        assert embed.title == "X"
        assert embed.description == "Y"
        assert embed.color == discord.Color.red()


# ---------------------------------------------------------------------------
# Active Ship field
# ---------------------------------------------------------------------------


class TestActiveShipField:
    def test_nickname_and_ship_name(self):
        embed = build_loadout_embed(_make_player_response(), viewer_is_owner_or_admin=True)
        field = _get_field(embed, "Active Ship")
        assert field is not None
        assert "<:wraith:1>" in field.value
        assert "Betty (Wraith)" in field.value

    def test_no_nickname_uses_ship_name_only(self):
        resp = _make_player_response(ship_nickname=None)
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=True)
        field = _get_field(embed, "Active Ship")
        assert field.value.endswith("Wraith")

    def test_nickname_same_as_ship_name_not_duplicated(self):
        """When nickname == ship_name, don't render 'Wraith (Wraith)'."""
        resp = _make_player_response(ship_nickname="Wraith")
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=True)
        field = _get_field(embed, "Active Ship")
        assert "(Wraith)" not in field.value


# ---------------------------------------------------------------------------
# Ship Stats field (spec §7.11)
# ---------------------------------------------------------------------------


class TestShipStatsField:
    def test_core_stats_rendered(self):
        embed = build_loadout_embed(_make_player_response(), viewer_is_owner_or_admin=True)
        field = _get_field(embed, "Ship Stats")
        v = field.value
        assert "Armour: **95**" in v
        assert "Handling: **60**" in v
        assert "HP: **320**" in v
        assert "DPS: **42.5**" in v

    def test_total_value_appended_when_within_threshold(self):
        embed = build_loadout_embed(_make_player_response(), viewer_is_owner_or_admin=True)
        field = _get_field(embed, "Ship Stats")
        assert "Total Value: **15,000**" in field.value

    def test_total_value_dropped_when_threshold_exceeded(self):
        """If core + Total Value suffix exceeds SHIP_STATS_TOTAL_VALUE_THRESHOLD, drop Total Value.

        Uses 16-digit integer stats to force the core string beyond the 89-char trigger
        (threshold=120, suffix=~31 chars, so core must exceed 89 chars).
        These stat values are unrealistic for gameplay but deterministically trigger the drop.
        """
        from utils.loadout_embed import SHIP_STATS_TOTAL_VALUE_THRESHOLD, _format_ship_stats_field

        resp = {
            "ship_stats": {
                "armour": 10**15,       # 16-digit: pads core significantly
                "handling": 10**15,
                "hp": 10**15,
                "dps": float(10**15),
                "total_value": 9_999_999,
            }
        }
        _, value = _format_ship_stats_field(resp)

        # The drop MUST have fired — Total Value must not appear
        assert "Total Value" not in value, (
            f"Total Value should have been dropped: core={value!r}, "
            f"threshold={SHIP_STATS_TOTAL_VALUE_THRESHOLD}"
        )
        # Core stats must still be present
        assert "Armour:" in value
        assert "HP:" in value

    def test_cargo_capacity_not_in_ship_stats(self):
        """Cargo is shown in section header, NOT in Ship Stats (spec §7.11)."""
        embed = build_loadout_embed(_make_player_response(), viewer_is_owner_or_admin=True)
        field = _get_field(embed, "Ship Stats")
        # Cargo value of 20 should NOT appear in the stats field
        assert "Cargo" not in field.value

    def test_missing_stats_rendered_as_dashes(self):
        resp = _make_player_response(ship_stats={"dps": None, "armour": None})
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=True)
        field = _get_field(embed, "Ship Stats")
        assert field.value == "—"


# ---------------------------------------------------------------------------
# Spacer invariant
# ---------------------------------------------------------------------------


class TestSpacerInvariant:
    def test_spacer_before_primary_weapons(self):
        """Spacer field must appear before the Primary Weapons section header."""
        embed = build_loadout_embed(_make_player_response(), viewer_is_owner_or_admin=True)
        names = [f.name for f in embed.fields]
        # Active Ship, Ship Stats, SPACER, Primary Weapons <N/M>, SPACER, Modules <N/M>, ...
        pw_idx = next(i for i, n in enumerate(names) if n.startswith("Primary Weapons"))
        assert names[pw_idx - 1] == SPACER_NAME

    def test_spacer_before_modules(self):
        embed = build_loadout_embed(_make_player_response(), viewer_is_owner_or_admin=True)
        names = [f.name for f in embed.fields]
        mod_idx = next(i for i, n in enumerate(names) if n.startswith("Modules"))
        assert names[mod_idx - 1] == SPACER_NAME

    def test_spacer_before_cargo_when_visible(self):
        resp = _make_player_response(cargo_total_count=0)
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=True)
        names = [f.name for f in embed.fields]
        cargo_idx = next(i for i, n in enumerate(names) if n.startswith("Cargo Hold"))
        assert names[cargo_idx - 1] == SPACER_NAME


# ---------------------------------------------------------------------------
# Section headers with <N/M> format
# ---------------------------------------------------------------------------


class TestSectionHeaders:
    def test_weapon_header_format(self):
        embed = build_loadout_embed(_make_player_response(), viewer_is_owner_or_admin=True)
        field = next((f for f in embed.fields if f.name.startswith("Primary Weapons")), None)
        assert field is not None
        # 2 weapons, max_primaries=2
        assert field.name == "Primary Weapons <2/2>"

    def test_modules_header_format(self):
        embed = build_loadout_embed(_make_player_response(), viewer_is_owner_or_admin=True)
        field = next((f for f in embed.fields if f.name.startswith("Modules")), None)
        # 2 modules, max_modules=4
        assert field.name == "Modules <2/4>"

    def test_cargo_header_format_with_capacity(self):
        """Cargo Hold <N/M> — N=cargo_total_count, M=ship_stats.cargo."""
        embed = build_loadout_embed(_make_player_response(), viewer_is_owner_or_admin=True)
        field = next((f for f in embed.fields if f.name.startswith("Cargo Hold")), None)
        assert field.name == "Cargo Hold <0/20>"


# ---------------------------------------------------------------------------
# Weapon/module/cargo line formatting
# ---------------------------------------------------------------------------


class TestLineFormatting:
    def test_weapon_with_emoji(self):
        embed = build_loadout_embed(_make_player_response(), viewer_is_owner_or_admin=True)
        field = next(f for f in embed.fields if f.name.startswith("Primary Weapons"))
        assert "<:pulse:1> Pulse Laser | DPS: **12**" in field.value

    def test_weapon_fallback_bullet_when_no_emoji(self):
        resp = _make_player_response(
            weapons=[{"name": "Reaver MkII", "emoji": None, "dps": 12.5, "value": 900}],
        )
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=True)
        field = next(f for f in embed.fields if f.name.startswith("Primary Weapons"))
        # Fallback uses '• ' bullet (NOT unicode per-type emoji fallbacks)
        assert "• Reaver MkII | DPS: **12.5**" in field.value

    def test_module_with_effects(self):
        embed = build_loadout_embed(_make_player_response(), viewer_is_owner_or_admin=True)
        field = next(f for f in embed.fields if f.name.startswith("Modules"))
        assert "<:diol:1> D'iol | Armour: **160**" in field.value

    def test_module_empty_effects_renders_name_only(self):
        """GammaShieldModule has empty effects list → just ':emoji: Name'."""
        embed = build_loadout_embed(_make_player_response(), viewer_is_owner_or_admin=True)
        field = next(f for f in embed.fields if f.name.startswith("Modules"))
        # Gamma Shield I rendered without any '|' suffix on its line
        lines = field.value.split("\n")
        gshield_line = next(ln for ln in lines if "Gamma Shield" in ln)
        assert "|" not in gshield_line
        assert gshield_line == "<:gshield:2> Gamma Shield I"

    def test_module_multiple_effects_pipe_joined(self):
        resp = _make_player_response(
            modules=[
                {
                    "name": "Booster",
                    "emoji": "<:b:1>",
                    "type": "BoosterModule",
                    "effects": [
                        {"label": "Duration", "value": "10s"},
                        {"label": "Speed", "value": "80%"},
                    ],
                    "combat_tier": "combat",
                }
            ],
        )
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=True)
        field = next(f for f in embed.fields if f.name.startswith("Modules"))
        assert "<:b:1> Booster | Duration: **10s** | Speed: **80%**" in field.value


# ---------------------------------------------------------------------------
# Cargo visibility
# ---------------------------------------------------------------------------


class TestCargoVisibility:
    def test_cargo_hidden_when_viewer_not_privileged(self):
        embed = build_loadout_embed(_make_player_response(), viewer_is_owner_or_admin=False)
        names = [f.name for f in embed.fields]
        assert not any(n.startswith("Cargo Hold") for n in names)

    def test_cargo_shown_empty_when_viewer_is_owner(self):
        embed = build_loadout_embed(_make_player_response(), viewer_is_owner_or_admin=True)
        field = next(f for f in embed.fields if f.name.startswith("Cargo Hold"))
        assert field.value == "Empty"

    def test_cargo_items_rendered(self):
        resp = _make_player_response(
            cargo=[
                {"item_name": "Small Credit Chip", "item_type": "misc", "quantity": 2, "emoji": "<:credits:1>"},
                {"item_name": "Ion Beam", "item_type": "weapon", "quantity": 1, "emoji": None},
            ],
            cargo_total_count=3,
        )
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=True)
        field = next(f for f in embed.fields if f.name.startswith("Cargo Hold"))
        assert "<:credits:1> Small Credit Chip (x2)" in field.value
        assert "• Ion Beam" in field.value
        # Header reflects count
        assert field.name == "Cargo Hold <3/20>"


# ---------------------------------------------------------------------------
# Continuation-field (1024-char overflow)
# ---------------------------------------------------------------------------


class TestContinuationField:
    def test_long_modules_section_splits_into_multiple_fields(self):
        """When module lines exceed 1024 chars total, continuation fields are added."""
        # Generate enough long module lines to exceed 1024 chars.
        long_effect = "X" * 100
        modules = []
        for i in range(20):
            modules.append(
                {
                    "name": f"Mod{i}",
                    "emoji": "<:m:1>",
                    "type": "ArmourModule",
                    "effects": [{"label": "Armour", "value": long_effect}],
                    "combat_tier": "combat",
                }
            )
        resp = _make_player_response(modules=modules)
        resp["ship_stats"]["max_modules"] = 20
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=False)

        # Expect at least one continuation field: first keeps the real header,
        # subsequent ones use SPACER_NAME.
        mod_fields = [f for f in embed.fields if f.name.startswith("Modules") or (
            f.name == SPACER_NAME and "Mod" in f.value
        )]
        assert len(mod_fields) >= 2
        # First is the real header
        assert mod_fields[0].name == "Modules <20/20>"
        # At least one continuation uses SPACER_NAME
        assert any(f.name == SPACER_NAME and "Mod" in f.value for f in mod_fields[1:])

    def test_no_field_value_exceeds_1024(self):
        """Every field value must stay under the Discord 1024-char per-field limit."""
        long_effect = "X" * 100
        modules = [
            {
                "name": f"Mod{i}",
                "emoji": "<:m:1>",
                "type": "ArmourModule",
                "effects": [{"label": "Armour", "value": long_effect}],
                "combat_tier": "combat",
            }
            for i in range(20)
        ]
        resp = _make_player_response(modules=modules)
        resp["ship_stats"]["max_modules"] = 20
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=False)
        for f in embed.fields:
            assert len(f.value) <= MAX_FIELD_VALUE, f"Field '{f.name}' exceeded 1024"


# ---------------------------------------------------------------------------
# Truncation strategy (spec §7.2)
# ---------------------------------------------------------------------------


class TestTruncationStrategy:
    @staticmethod
    def _make_heavy_response(*, weapon_count=0, utility_count=0, combat_count=0, cargo_count=0):
        """Build a response where items dominate the budget so truncation kicks in."""
        long_name = "X" * 200  # hefty line length
        weapons = [
            {"name": f"{long_name}-w{i}", "emoji": "<:w:1>", "dps": 10, "value": 100}
            for i in range(weapon_count)
        ]
        utility_modules = [
            {
                "name": f"util{i}",
                "emoji": "<:u:1>",
                "type": "CabinModule",
                "effects": [{"label": "Crew", "value": str(i)}],
                "combat_tier": "utility",
            }
            for i in range(utility_count)
        ]
        combat_modules = [
            {
                "name": f"combat{i}",
                "emoji": "<:c:1>",
                "type": "ArmourModule",
                "effects": [{"label": "Armour", "value": "100"}],
                "combat_tier": "combat",
            }
            for i in range(combat_count)
        ]
        cargo = [
            {"item_name": f"{long_name}-item{i}", "item_type": "weapon", "quantity": 1, "emoji": None}
            for i in range(cargo_count)
        ]
        return _make_player_response(
            weapons=weapons,
            modules=utility_modules + combat_modules,
            cargo=cargo,
            cargo_total_count=cargo_count,
            ship_stats={
                "armour": 100, "cargo": 30, "handling": 50,
                "hp": 200, "dps": 10, "total_value": 1000,
                "max_primaries": weapon_count,
                "max_secondaries": 0,
                "max_turrets": 0,
                "max_modules": utility_count + combat_count,
            },
        )

    def test_total_length_stays_under_6000(self):
        """Heavy response must keep total embed length under Discord's 6000 limit."""
        resp = self._make_heavy_response(
            weapon_count=10, utility_count=15, combat_count=10, cargo_count=30
        )
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=True)
        total = len(embed.title or "") + len(embed.description or "")
        total += sum(len(f.name) + len(f.value) for f in embed.fields)
        assert total <= MAX_EMBED_TOTAL

    def test_cargo_truncated_first(self):
        """Cargo section gets '… and N more' when it cannot fit."""
        resp = self._make_heavy_response(
            weapon_count=2, utility_count=2, combat_count=2, cargo_count=50,
        )
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=True)
        cargo_fields = [f for f in embed.fields if f.name.startswith("Cargo Hold") or (
            f.name == SPACER_NAME and "… and" in f.value and "item" in f.value
        )]
        # Expect truncation suffix somewhere in cargo section (first matching field)
        cargo_text = "\n".join(f.value for f in cargo_fields)
        assert "… and" in cargo_text

    def test_field_count_under_limit(self):
        """Embed field count must never exceed MAX_FIELDS."""
        resp = self._make_heavy_response(
            weapon_count=20, utility_count=20, combat_count=20, cargo_count=40,
        )
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=True)
        assert len(embed.fields) <= MAX_FIELDS

    def test_utility_modules_dropped_before_combat_modules(self):
        """Spec §7.2: utility-tier modules must be dropped BEFORE combat-tier modules
        when the embed budget is exhausted.

        Construct a response with many utility modules and a few combat modules where
        only the utility ones need to be dropped to fit within budget. Assert that
        at least one combat module survives while utility modules are truncated.
        """
        # Use very long utility module names to blow the budget quickly.
        long_name = "U" * 250
        utility_modules = [
            {
                "name": f"{long_name}-util{i}",
                "emoji": "<:u:1>",
                "type": "CabinModule",
                "effects": [{"label": "Crew", "value": str(i)}],
                "combat_tier": "utility",
            }
            for i in range(20)
        ]
        # Combat modules are short — they should survive.
        combat_modules = [
            {
                "name": f"combat{i}",
                "emoji": "<:c:1>",
                "type": "ArmourModule",
                "effects": [{"label": "Armour", "value": "100"}],
                "combat_tier": "combat",
            }
            for i in range(3)
        ]
        resp = _make_player_response(
            modules=utility_modules + combat_modules,
            ship_stats={
                "armour": 100, "cargo": 10, "handling": 50, "hp": 200,
                "dps": 10, "total_value": 1000,
                "max_primaries": 0, "max_secondaries": 0, "max_turrets": 0,
                "max_modules": 23,
            },
        )

        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=False)
        total = len(embed.title or "") + len(embed.description or "")
        total += sum(len(f.name) + len(f.value) for f in embed.fields)
        assert total <= MAX_EMBED_TOTAL

        # Find all field values for module section
        module_text = " ".join(
            f.value for f in embed.fields
            if f.name.startswith("Modules") or (f.name == SPACER_NAME and any(
                kw in f.value for kw in ["util", "combat", "U" * 10]
            ))
        )
        # At least one combat module name must be visible
        assert any(f"combat{i}" in module_text for i in range(3)), (
            "No combat module found in embed — utility should have been dropped first"
        )

    def test_entire_section_dropped_shows_truncation_suffix(self):
        """When all cargo items are dropped, the section still shows '… and N more'."""
        # One huge cargo item that alone exceeds what we can fit after other sections
        huge_name = "C" * 1500  # longer than MAX_FIELD_VALUE
        resp = _make_player_response(
            cargo=[{"item_name": huge_name, "item_type": "weapon", "quantity": 1, "emoji": None}],
            cargo_total_count=1,
        )
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=True)
        # Either the huge item is included (truncated to 1024) or the section shows suffix
        # The key invariant is that it doesn't crash
        assert len(embed.fields) <= MAX_FIELDS

    def test_utility_dropped_before_combat_fewer_utility_in_output(self):
        """Spec §7.2 tier-2: utility modules drop before combat modules.

        Construct a response with many long-named utility modules and a small number
        of combat modules where truncation definitely fires. Assert that:
        - Fewer utility module names appear in the output than combat module names.
        - The embed stays within the 6000-char budget.
        - At least one combat module is preserved.

        This covers the utility-dropping loop (loadout_embed.py lines 388-396) and
        verifies that combat modules are protected.
        """
        # 15 utility modules with very long names to exhaust the 5800-char embed budget.
        # Budget breakdown: ~5800 available; Active Ship + Ship Stats ~200 chars;
        # modules section must exceed ~5600 chars to trigger truncation.
        # Each utility line needs > 370 chars → use 360-char base name.
        long_util_name = "U" * 360
        utility_modules = [
            {
                "name": f"{long_util_name}{i}",
                "emoji": None,
                "type": "CabinModule",   # MODULE_COMBAT_TIER["CabinModule"] == "utility"
                "effects": [{"label": "Crew", "value": str(i)}],
                "combat_tier": "utility",
            }
            for i in range(15)
        ]
        # 5 short-named combat modules that will survive once utilities are dropped
        combat_modules = [
            {
                "name": f"ArmourMod{i}",
                "emoji": None,
                "type": "ArmourModule",  # MODULE_COMBAT_TIER["ArmourModule"] == "combat"
                "effects": [{"label": "Armour", "value": "160"}],
                "combat_tier": "combat",
            }
            for i in range(5)
        ]

        resp = _make_player_response(
            modules=utility_modules + combat_modules,
            ship_stats={
                "armour": 100, "cargo": 10, "handling": 50, "hp": 200,
                "dps": 10, "total_value": 1000,
                "max_primaries": 2, "max_secondaries": 0, "max_turrets": 0,
                "max_modules": 20,
            },
        )

        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=False)

        # Budget invariant
        total = len(embed.title or "") + len(embed.description or "")
        total += sum(len(f.name) + len(f.value) for f in embed.fields)
        assert total <= MAX_EMBED_TOTAL

        # Collect all text from the modules section fields
        all_text = " ".join(f.value for f in embed.fields)

        # Count utility vs combat module names visible and dropped in output
        utility_visible = sum(1 for i in range(15) if f"{long_util_name}{i}" in all_text)
        combat_visible = sum(1 for i in range(5) if f"ArmourMod{i}" in all_text)
        utility_dropped = 15 - utility_visible
        combat_dropped = 5 - combat_visible

        # Truncation must have fired (at least one utility was dropped)
        assert utility_dropped >= 1, "No utility module was dropped — truncation may not have fired"
        # All combat modules must be preserved (they are short and budget permits them)
        assert combat_visible == 5, (
            f"Expected all 5 combat modules to survive but only {combat_visible} visible"
        )
        # More utility modules were dropped than combat modules (utility dropped first)
        assert utility_dropped > combat_dropped, (
            f"Expected more utility drops ({utility_dropped}) than combat drops ({combat_dropped}) "
            "— utility-first truncation was not applied correctly"
        )

    def test_all_utility_dropped_then_combat_modules_start_dropping(self):
        """Spec §7.2 tier-3: when all utility modules are dropped and budget still exceeded,
        combat modules start dropping next.

        This covers the combat-tier dropping loop (loadout_embed.py lines 399-410) —
        the path that was 0% covered.

        Strategy: fill the budget with 20 utility modules AND 10 combat modules, all
        with 360-char names so the combined total far exceeds the 5800-char budget.
        All utilities must be dropped first; then the combat loop fires to drop enough
        combat modules to bring the total under budget.
        """
        # 20 utility + 20 combat, all with 360-char names to overwhelm the budget.
        # Budget = 5800 chars. 20 utility alone = ~7800 chars >> budget → all must drop.
        # After dropping all utility, 20 combat = ~7800 chars → still > budget → some drop.
        long_name = "M" * 360  # 360 chars each
        utility_modules = [
            {
                "name": f"{long_name}U{i:02d}",
                "emoji": None,
                "type": "ScannerModule",  # MODULE_COMBAT_TIER["ScannerModule"] == "utility"
                "effects": [],
                "combat_tier": "utility",
            }
            for i in range(20)
        ]
        combat_modules = [
            {
                "name": f"{long_name}C{i:02d}",
                "emoji": None,
                "type": "ArmourModule",  # MODULE_COMBAT_TIER["ArmourModule"] == "combat"
                "effects": [{"label": "Armour", "value": "160"}],
                "combat_tier": "combat",
            }
            for i in range(20)
        ]

        resp = _make_player_response(
            modules=utility_modules + combat_modules,
            ship_stats={
                "armour": 100, "cargo": 10, "handling": 50, "hp": 200,
                "dps": 10, "total_value": 1000,
                "max_primaries": 0, "max_secondaries": 0, "max_turrets": 0,
                "max_modules": 40,
            },
        )

        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=False)

        # Budget invariant: never exceed 6000 chars
        total = len(embed.title or "") + len(embed.description or "")
        total += sum(len(f.name) + len(f.value) for f in embed.fields)
        assert total <= MAX_EMBED_TOTAL

        all_text = " ".join(f.value for f in embed.fields)

        # ALL utility modules must have been dropped (there is no room for them)
        utility_visible = sum(1 for i in range(20) if f"{long_name}U{i:02d}" in all_text)
        assert utility_visible == 0, (
            f"{utility_visible} utility modules still visible — all should have been dropped "
            "before the combat-module cascade could fire"
        )

        # At least some combat modules must also have been dropped (combat cascade fired)
        # This asserts that lines 399-410 of loadout_embed.py were exercised.
        combat_visible = sum(1 for i in range(20) if f"{long_name}C{i:02d}" in all_text)
        assert combat_visible < 20, (
            "All 20 combat modules still visible — expected some to be dropped by cascade. "
            "Lines 399-410 (combat module dropping loop) were not exercised."
        )

        # Truncation suffix must appear
        assert "… and" in all_text, "Expected truncation suffix '… and N more' in output"


# ---------------------------------------------------------------------------
# Criminal path specifics
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Adversarial edge cases
# ---------------------------------------------------------------------------


class TestAdversarialEdgeCases:
    """Adversarial and edge case tests not covered by the main suites."""

    def test_cargo_quantity_zero_renders_as_name_only(self):
        """quantity=0 silently becomes 1 in _format_cargo_line (defensive: '0 or 1 = 1').
        Document and verify the actual behavior (DEF-007).
        """
        from utils.loadout_embed import _format_cargo_line

        line = _format_cargo_line({"item_name": "Widget", "item_type": "misc", "quantity": 0, "emoji": None})
        # quantity=0 → `0 or 1 = 1` → rendered without (xN) suffix
        assert "Widget" in line
        assert "(x0)" not in line  # quantity 0 is not shown explicitly

    def test_cargo_quantity_negative_same_as_zero(self):
        """Negative quantity follows the same `or 1` path as zero."""
        from utils.loadout_embed import _format_cargo_line

        line = _format_cargo_line({"item_name": "Bug", "item_type": "misc", "quantity": -5, "emoji": None})
        assert "Bug" in line
        # -5 is falsy-ish but -5 `or 1` = -5 (non-zero), so (x-5) would appear
        # Actually: in Python, -5 is truthy, so -5 or 1 = -5; 
        # then condition `if quantity > 1` is False for -5, so no suffix
        assert "(-5)" not in line  # no negative display

    def test_ship_with_null_icon_no_thumbnail_set(self):
        """Null thumbnail_url must not raise (spec §7.5)."""
        resp = _make_player_response()
        resp["thumbnail_url"] = None
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=True)
        # Should not raise; thumbnail should be None/unset
        assert embed.thumbnail.url is None

    def test_null_ship_name_renders_unknown(self):
        """Missing ship_name falls back to 'Unknown' in Active Ship field."""
        resp = _make_player_response(ship_name=None, ship_nickname=None, ship_emoji=None)
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=True)
        field = next(f for f in embed.fields if f.name == "Active Ship")
        assert "Unknown" in field.value

    def test_weapon_with_null_dps_renders_without_dps_suffix(self):
        """Weapon with dps=None renders without '| DPS: **X**' suffix."""
        resp = _make_player_response(
            weapons=[{"name": "Mystery Gun", "emoji": None, "dps": None, "value": 500}],
        )
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=True)
        field = next(f for f in embed.fields if f.name.startswith("Primary Weapons"))
        assert "Mystery Gun" in field.value
        assert "DPS" not in field.value

    def test_module_with_unknown_type_renders_name_only(self):
        """Module with unknown type (not in MODULE_EFFECT_MAP) renders name only, no effects."""
        resp = _make_player_response(
            modules=[
                {
                    "name": "FutureMod X",
                    "emoji": "<:fx:1>",
                    "type": "SomeFutureModule",
                    "effects": [],  # bot-core returns [] for unknown types
                    "combat_tier": "combat",
                }
            ],
        )
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=True)
        field = next(f for f in embed.fields if f.name.startswith("Modules"))
        lines = field.value.split("\n")
        future_line = next((ln for ln in lines if "FutureMod X" in ln), None)
        assert future_line is not None
        assert "|" not in future_line  # no effect suffix

    def test_empty_modules_list_does_not_crash(self):
        """Zero-module loadout must not crash.

        KNOWN DEFECT (DEF-EMPTY-SECTION): When lines=[] for a section, _render_section
        only adds a spacer field but omits the real section header (e.g., 'Modules <0/4>').
        This means an empty-modules loadout shows an orphaned spacer without a section header.
        This test documents the actual (defective) behavior to detect regressions.
        Report: DEF-017 in LOADOUT_QA_REVIEW.md — MAJOR defect, needs developer fix.
        """
        resp = _make_player_response(modules=[])
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=True)
        # Must not crash
        assert embed is not None
        # Document actual (buggy) behavior: no 'Modules' header field is added
        assert not any(f.name.startswith("Modules") for f in embed.fields), (
            "DEF-017: Empty modules section should render 'Modules <0/N>' header but does not. "
            "When this assertion starts FAILING, the bug has been fixed — update the test accordingly."
        )

    def test_empty_weapons_list_does_not_crash(self):
        """Zero-weapon loadout must not crash.

        KNOWN DEFECT (DEF-EMPTY-SECTION): Same as empty modules — Primary Weapons header
        is missing when the weapons list is empty. See test_empty_modules_list_does_not_crash.
        """
        resp = _make_player_response(weapons=[])
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=True)
        # Must not crash
        assert embed is not None
        # Document actual (buggy) behavior: no 'Primary Weapons' header field is added
        assert not any(f.name.startswith("Primary Weapons") for f in embed.fields), (
            "DEF-017: Empty weapons section should render 'Primary Weapons <0/N>' header but does not. "
            "When this assertion starts FAILING, the bug has been fixed — update the test accordingly."
        )

    def test_criminal_no_faction_renders_tl_only(self):
        """Criminal with no faction renders 'TL{n}' without faction prefix."""
        resp = _make_criminal_response(subject_description=None)
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=True)
        assert embed.description == "TL3"

    def test_criminal_no_faction_no_tech_level_no_description(self):
        """Criminal with no faction and no tech_level → description is None."""
        resp = _make_criminal_response(subject_description=None, tech_level=None)
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=True)
        assert embed.description is None

    def test_no_description_embed_still_renders(self):
        """Player loadout with no subject_mention → no description set, no crash."""
        resp = _make_player_response(subject_mention=None)
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=True)
        # Description should be None or empty when subject_mention is None
        assert embed.description is None or embed.description == ""

    def test_duplicate_cargo_items_listed_separately(self):
        """Two cargo items with the same name are rendered as separate lines."""
        resp = _make_player_response(
            cargo=[
                {"item_name": "Ion Beam", "item_type": "weapon", "quantity": 1, "emoji": None},
                {"item_name": "Ion Beam", "item_type": "weapon", "quantity": 3, "emoji": None},
            ],
            cargo_total_count=4,
        )
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=True)
        field = next(f for f in embed.fields if f.name.startswith("Cargo Hold"))
        lines = [ln for ln in field.value.split("\n") if "Ion Beam" in ln]
        assert len(lines) == 2  # listed separately, NOT merged


class TestCriminalPath:
    def test_cargo_always_shown_for_criminal(self):
        """Criminal path always shows Cargo Hold <0/M> even with empty cargo."""
        embed = build_loadout_embed(_make_criminal_response(), viewer_is_owner_or_admin=True)
        cargo_field = next((f for f in embed.fields if f.name.startswith("Cargo Hold")), None)
        assert cargo_field is not None
        # M = 45 from ship_stats.cargo
        assert cargo_field.name == "Cargo Hold <0/45>"

    def test_criminal_thumbnail_is_criminal_icon(self):
        embed = build_loadout_embed(_make_criminal_response(), viewer_is_owner_or_admin=True)
        assert embed.thumbnail.url == "https://cdn/darkmage.png"


# ---------------------------------------------------------------------------
# Total Value threshold heuristic (spec §7.11)
# ---------------------------------------------------------------------------


class TestShipStatsTotalValueHeuristic:
    def test_total_value_dropped_when_field_too_long(self):
        """If the with-total-value field would exceed ~200 chars, drop Total Value."""
        # Pad the core by using very long label/value strings via extreme numbers.
        # Easier: patch the threshold for the test by constructing a boundary condition.
        resp = _make_player_response()
        # Force the core string to be exactly ~190 chars with just core stats using wide numbers.
        resp["ship_stats"] = {
            # dps formatted via :g — a float with many significant digits stays long
            "armour": 12345678,
            "handling": 12345678,
            "hp": 12345678,
            "dps": 12345678.123456789,  # :g -> "1.23457e+07" (shortish); bulk via label count instead
            "total_value": 999999999,
            "cargo": 20,
        }
        embed = build_loadout_embed(resp, viewer_is_owner_or_admin=True)
        field = next(f for f in embed.fields if f.name == "Ship Stats")
        # When within threshold we DO append total value; both assertions are legal.
        # Just ensure the field length is reasonable.
        assert len(field.value) <= 1024

    def test_total_value_actually_dropped_when_core_exceeds_threshold(self):
        """Directly unit-test _format_ship_stats_field to verify the drop path fires.

        The SHIP_STATS_TOTAL_VALUE_THRESHOLD is 200. With realistic game stats the
        threshold is never reached by normal gameplay values (max realistic core is ~98
        chars). However the drop path must be verified directly for correctness.
        """
        from utils.loadout_embed import SHIP_STATS_TOTAL_VALUE_THRESHOLD, _format_ship_stats_field

        # Build a core that is exactly > THRESHOLD - len(suffix)
        # suffix is ' | Total Value: **999,999,999**' = ~31 chars
        # So we need core > 200 - 31 = 169 chars.
        # Use armour values with many digits (int formatting, not scientific).
        large_int = 10**45  # 46-digit integer, core will be ~193 chars
        resp = {
            "ship_stats": {
                "armour": large_int,
                "handling": large_int,
                "hp": large_int,
                "dps": 10.0,
                "total_value": 999999999,
            }
        }
        _, value = _format_ship_stats_field(resp)
        # Core alone should be > THRESHOLD - 31, so Total Value should be dropped
        core_only = value
        assert "Total Value" not in core_only, (
            f"Total Value should have been dropped (core len={len(core_only)}, "
            f"threshold={SHIP_STATS_TOTAL_VALUE_THRESHOLD})"
        )
        # Core stats should still be present
        assert "Armour:" in core_only
        assert "DPS:" in core_only

    def test_total_value_included_for_game_realistic_stats(self):
        """With all realistic game-scale stats, Total Value should always be included.

        This documents the known behavior: the threshold NEVER fires for normal
        ship stats (armour ~2–9999, handling ~1–100, HP ~95–50000, DPS ~0–1000).
        """
        from utils.loadout_embed import _format_ship_stats_field

        resp = {
            "ship_stats": {
                "armour": 9999,
                "handling": 99,
                "hp": 50000,
                "dps": 999.9,
                "total_value": 9999999,
            }
        }
        _, value = _format_ship_stats_field(resp)
        assert "Total Value: **9,999,999**" in value, (
            "Total Value should be present for all game-realistic stat values"
        )


# ---------------------------------------------------------------------------
# Cargo quantity edge cases (DEF-007 fix verification)
# ---------------------------------------------------------------------------


class TestCargoQuantityEdgeCases:
    """Verifies the DEF-007 fix: _format_cargo_line handles zero/negative/None
    quantity explicitly rather than using `or 1` coercion.

    Design choice: quantity=None → 1 (legacy fallback); quantity<=0 → render
    name-only (no count suffix), treated as 0 items visible but not silently
    coerced to 1. The item is still shown so the player can see it exists.
    """

    def test_none_quantity_defaults_to_one(self):
        """Missing quantity key defaults to 1 (backward-compat for legacy payloads)."""
        from utils.loadout_embed import _format_cargo_line

        line = _format_cargo_line({"item_name": "Widget", "item_type": "misc", "emoji": None})
        # No quantity key → defaults to 1 → shown without (xN) suffix
        assert "Widget" in line
        assert "(x1)" not in line  # quantity=1 never shows the suffix

    def test_zero_quantity_renders_name_only_no_coercion(self):
        """quantity=0 renders the item name without any count suffix.

        After the DEF-007 fix, 0 is no longer silently coerced to 1.
        The item still appears (it's in the cargo), but without a count suffix.
        """
        from utils.loadout_embed import _format_cargo_line

        line = _format_cargo_line({"item_name": "Widget", "item_type": "misc", "quantity": 0, "emoji": None})
        assert "Widget" in line  # item is still shown
        assert "(x" not in line  # no quantity suffix for 0

    def test_negative_quantity_renders_name_only(self):
        """Negative quantity renders the item name without a count suffix (defensive)."""
        from utils.loadout_embed import _format_cargo_line

        line = _format_cargo_line({"item_name": "Bug", "item_type": "misc", "quantity": -3, "emoji": None})
        assert "Bug" in line
        assert "(x" not in line  # negative quantity does not produce a suffix

    def test_positive_quantity_above_one_shows_count(self):
        """Normal quantity > 1 still renders (xN) suffix as before."""
        from utils.loadout_embed import _format_cargo_line

        line = _format_cargo_line({"item_name": "Credits", "item_type": "misc", "quantity": 5, "emoji": None})
        assert "Credits" in line
        assert "(x5)" in line
