"""Unit tests for Sub-task B: build_bounty_cap_payout_embed.

Acceptance criteria:
- Title is "💰 Active Bounty Payouts"
- Color matches tier from TIER_COLORS
- Fields group bounties by tier with count and payout range
- Footer is "Capture a bounty with /check"
- Works with ORM objects and plain dicts
- Tiers with no bounties are omitted from fields
"""

import sys
import types
from unittest.mock import MagicMock

# Mock shared.bblogger before any imports
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

if "sqlalchemy_utils" not in sys.modules:
    _mock_sqla_utils = types.ModuleType("sqlalchemy_utils")
    _mock_sqla_utils.UUIDType = MagicMock()
    sys.modules["sqlalchemy_utils"] = _mock_sqla_utils

from utils.bounty_announcement_payload import TIER_COLORS, build_bounty_cap_payout_embed, build_capture_payout_embed


def _make_bounty_orm(division: str, reward: int):
    """Return a mock Bounty ORM-like object."""
    b = MagicMock()
    b.division = division
    b.reward = reward
    return b


def _make_bounty_dict(division: str, reward: int) -> dict:
    return {"division": division, "reward": reward}


class TestBuildBountyCapPayoutEmbed:
    """Tests for build_bounty_cap_payout_embed (Sub-task B)."""

    def test_title_is_correct(self):
        """Embed title must be '💰 Active Bounty Payouts'."""
        result = build_bounty_cap_payout_embed([], capped_tier="bronze")
        assert result["title"] == "💰 Active Bounty Payouts"

    def test_footer_is_correct(self):
        """Footer text must be 'Capture a bounty with /check'."""
        result = build_bounty_cap_payout_embed([], capped_tier="bronze")
        assert result["footer"]["text"] == "Capture a bounty with /check"

    def test_color_matches_tier_bronze(self):
        """Color for bronze tier is TIER_COLORS['bronze']."""
        result = build_bounty_cap_payout_embed([], capped_tier="bronze")
        assert result["color"] == TIER_COLORS["bronze"]

    def test_color_matches_tier_silver(self):
        """Color for silver tier is TIER_COLORS['silver']."""
        result = build_bounty_cap_payout_embed([], capped_tier="silver")
        assert result["color"] == TIER_COLORS["silver"]

    def test_color_matches_tier_gold(self):
        """Color for gold tier is TIER_COLORS['gold']."""
        result = build_bounty_cap_payout_embed([], capped_tier="gold")
        assert result["color"] == TIER_COLORS["gold"]

    def test_color_matches_tier_platinum(self):
        """Color for platinum tier is TIER_COLORS['platinum']."""
        result = build_bounty_cap_payout_embed([], capped_tier="platinum")
        assert result["color"] == TIER_COLORS["platinum"]

    def test_tier_case_insensitive(self):
        """Tier lookup is case-insensitive."""
        result_lower = build_bounty_cap_payout_embed([], capped_tier="Bronze")
        result_upper = build_bounty_cap_payout_embed([], capped_tier="BRONZE")
        result_exact = build_bounty_cap_payout_embed([], capped_tier="bronze")
        assert result_lower["color"] == result_exact["color"]
        assert result_upper["color"] == result_exact["color"]

    def test_empty_active_bounties_produces_no_fields(self):
        """When there are no active bounties, fields list is empty."""
        result = build_bounty_cap_payout_embed([], capped_tier="bronze")
        assert result["fields"] == []

    def test_single_bronze_bounty_creates_field(self):
        """A single bronze bounty creates a Bronze field with count=1."""
        bounties = [_make_bounty_orm("bronze", 500)]
        result = build_bounty_cap_payout_embed(bounties, capped_tier="bronze")
        fields = result["fields"]
        assert len(fields) == 1
        assert fields[0]["name"] == "Bronze"
        assert "1 active" in fields[0]["value"]
        assert "500 cr" in fields[0]["value"]

    def test_multiple_bounties_same_tier_shows_range(self):
        """Multiple bounties in the same tier show a payout range."""
        bounties = [
            _make_bounty_orm("bronze", 250),
            _make_bounty_orm("bronze", 500),
            _make_bounty_orm("bronze", 750),
        ]
        result = build_bounty_cap_payout_embed(bounties, capped_tier="bronze")
        fields = result["fields"]
        assert len(fields) == 1
        assert "3 active" in fields[0]["value"]
        assert "250" in fields[0]["value"]
        assert "750" in fields[0]["value"]

    def test_multiple_tiers_creates_multiple_fields(self):
        """Bounties across multiple tiers each get their own field."""
        bounties = [
            _make_bounty_orm("bronze", 300),
            _make_bounty_orm("silver", 1200),
            _make_bounty_orm("gold", 5000),
        ]
        result = build_bounty_cap_payout_embed(bounties, capped_tier="gold")
        field_names = [f["name"] for f in result["fields"]]
        assert "Bronze" in field_names
        assert "Silver" in field_names
        assert "Gold" in field_names

    def test_fields_in_canonical_tier_order(self):
        """Fields appear in Bronze → Silver → Gold → Platinum order."""
        bounties = [
            _make_bounty_orm("platinum", 20000),
            _make_bounty_orm("bronze", 300),
            _make_bounty_orm("gold", 5000),
            _make_bounty_orm("silver", 1200),
        ]
        result = build_bounty_cap_payout_embed(bounties, capped_tier="platinum")
        field_names = [f["name"] for f in result["fields"]]
        assert field_names == ["Bronze", "Silver", "Gold", "Platinum"]

    def test_works_with_dict_bounties(self):
        """Works with plain dict bounty data (not just ORM objects)."""
        bounties = [
            _make_bounty_dict("silver", 1000),
            _make_bounty_dict("silver", 2500),
        ]
        result = build_bounty_cap_payout_embed(bounties, capped_tier="silver")
        fields = result["fields"]
        assert len(fields) == 1
        assert fields[0]["name"] == "Silver"
        assert "2 active" in fields[0]["value"]

    def test_single_reward_shows_exact_amount_not_range(self):
        """When all bounties in a tier have the same reward, show exact amount."""
        bounties = [_make_bounty_orm("gold", 5000), _make_bounty_orm("gold", 5000)]
        result = build_bounty_cap_payout_embed(bounties, capped_tier="gold")
        fields = result["fields"]
        assert len(fields) == 1
        # Same reward → no range dash separator needed
        value = fields[0]["value"]
        assert "5,000 cr each" in value

    def test_tiers_with_no_bounties_are_omitted(self):
        """Tiers with zero active bounties are not included in fields."""
        bounties = [_make_bounty_orm("bronze", 300)]
        result = build_bounty_cap_payout_embed(bounties, capped_tier="silver")
        field_names = [f["name"] for f in result["fields"]]
        assert "Silver" not in field_names
        assert "Gold" not in field_names
        assert "Platinum" not in field_names
        assert "Bronze" in field_names


class TestBuildBountyCapPayoutEmbedAdversarial:
    """Adversarial and edge case tests for build_bounty_cap_payout_embed."""

    def test_bounty_with_empty_division_is_skipped(self):
        """Bounty dicts with empty division string are silently skipped."""
        bounties = [
            _make_bounty_dict("", 300),  # empty string
            _make_bounty_dict("bronze", 500),
        ]
        result = build_bounty_cap_payout_embed(bounties, capped_tier="bronze")
        field_names = [f["name"] for f in result["fields"]]
        assert "Bronze" in field_names
        # Empty division should not appear
        assert "" not in field_names
        assert len(result["fields"]) == 1  # only bronze

    def test_bounty_with_none_division_is_skipped(self):
        """Bounty ORM objects with division=None are silently skipped."""
        bounty_none_div = _make_bounty_orm("bronze", 300)
        bounty_none_div.division = None
        bounties = [bounty_none_div]
        result = build_bounty_cap_payout_embed(bounties, capped_tier="bronze")
        # No valid tiers, so no fields
        assert result["fields"] == []

    def test_unknown_capped_tier_falls_back_to_default_color(self):
        """Unknown capped_tier falls back to _DEFAULT_COLOR (not a crash)."""
        result = build_bounty_cap_payout_embed([], capped_tier="diamond")
        # Should not raise; color must be an int
        assert isinstance(result["color"], int)

    def test_single_bounty_exact_reward_shows_no_range_dash(self):
        """A single bounty shows exact reward, not a range with '–'."""
        bounties = [_make_bounty_orm("gold", 3000)]
        result = build_bounty_cap_payout_embed(bounties, capped_tier="gold")
        value = result["fields"][0]["value"]
        # No range dash when there's only one value
        assert "–" not in value
        assert "3,000" in value

    def test_mixed_orm_and_dict_bounties(self):
        """Mix of ORM and dict bounties is handled correctly."""
        bounties = [
            _make_bounty_orm("bronze", 250),
            _make_bounty_dict("bronze", 750),
        ]
        result = build_bounty_cap_payout_embed(bounties, capped_tier="bronze")
        fields = result["fields"]
        assert len(fields) == 1
        assert "2 active" in fields[0]["value"]
        assert "250" in fields[0]["value"]
        assert "750" in fields[0]["value"]

    def test_embed_has_required_structure(self):
        """Result dict has all required keys: title, color, fields, footer."""
        result = build_bounty_cap_payout_embed([], capped_tier="gold")
        assert "title" in result
        assert "color" in result
        assert "fields" in result
        assert "footer" in result
        assert isinstance(result["fields"], list)


# ===========================================================================
# Tests for build_capture_payout_embed (C.2 refactor)
# ===========================================================================


class TestBuildCapturePayoutEmbed:
    """Tests for the refactored build_capture_payout_embed (C.2).

    Acceptance criteria:
    - Title is "💰 Bounty Captured!"
    - Description is "{criminal_name} has been brought in."
    - Color is gold (0xFFD700)
    - Fields in order: Division, Claimed by, Base Reward, Capture Bonus,
      System Checks (if provided), Total Payout
    - System Checks field is omitted when reward_per_sys/route_length not provided
    """

    def test_title_is_correct(self):
        """Title must be '💰 Bounty Captured!'."""
        result = build_capture_payout_embed("TestCriminal", "bronze", 10000)
        assert result["title"] == "💰 Bounty Captured!"

    def test_description_includes_criminal_name(self):
        """Description must include criminal_name."""
        result = build_capture_payout_embed("Mordecai Krill", "silver", 50000)
        assert result["description"] == "Mordecai Krill has been brought in."

    def test_color_is_gold(self):
        """Color must be gold (0xFFD700) regardless of division."""
        result_bronze = build_capture_payout_embed("X", "bronze", 10000)
        result_plat = build_capture_payout_embed("X", "platinum", 50000)
        assert result_bronze["color"] == 0xFFD700
        assert result_plat["color"] == 0xFFD700

    def test_fields_present_without_sys_checks(self):
        """Four fields are present when reward_per_sys/route_length not provided."""
        result = build_capture_payout_embed("Criminal", "gold", 80000, winner_name="Hunter")
        fields_by_name = {f["name"]: f for f in result["fields"]}
        assert "🏆 Division" in fields_by_name
        assert "⚔️ Claimed by" in fields_by_name
        assert "💵 Base Reward" in fields_by_name
        assert "🎯 Capture Bonus" in fields_by_name
        assert "🏆 Total Payout" in fields_by_name
        # No system checks field since not provided
        assert "📍 System Checks" not in fields_by_name

    def test_fields_present_with_sys_checks(self):
        """System checks field appears when reward_per_sys and route_length provided."""
        result = build_capture_payout_embed(
            "Criminal", "gold", 80000, winner_name="Hunter", reward_per_sys=3000, route_length=4
        )
        fields_by_name = {f["name"]: f for f in result["fields"]}
        assert "📍 System Checks" in fields_by_name

    def test_division_field_value(self):
        """Division field shows capitalized division name."""
        result = build_capture_payout_embed("Criminal", "silver", 50000)
        fields_by_name = {f["name"]: f["value"] for f in result["fields"]}
        assert fields_by_name["🏆 Division"] == "Silver"

    def test_winner_name_in_claimed_by_field(self):
        """Claimed by field shows winner_name."""
        result = build_capture_payout_embed("Criminal", "gold", 80000, winner_name="SamX")
        fields_by_name = {f["name"]: f["value"] for f in result["fields"]}
        assert fields_by_name["⚔️ Claimed by"] == "SamX"

    def test_default_winner_name(self):
        """Default winner_name is 'A bounty hunter'."""
        result = build_capture_payout_embed("Criminal", "bronze", 10000)
        fields_by_name = {f["name"]: f["value"] for f in result["fields"]}
        assert fields_by_name["⚔️ Claimed by"] == "A bounty hunter"

    def test_base_reward_formatted_with_commas(self):
        """Base reward is formatted with commas and 'cr'."""
        result = build_capture_payout_embed("Criminal", "gold", 80000)
        fields_by_name = {f["name"]: f["value"] for f in result["fields"]}
        assert fields_by_name["💵 Base Reward"] == "80,000 cr"

    def test_capture_bonus_is_25_percent(self):
        """Capture bonus is 25% of base reward (int floor)."""
        result = build_capture_payout_embed("Criminal", "gold", 80000)
        fields_by_name = {f["name"]: f["value"] for f in result["fields"]}
        assert fields_by_name["🎯 Capture Bonus"] == "20,000 cr"  # 80000 * 0.25 = 20000

    def test_sys_checks_field_format(self):
        """System checks field shows reward_per_sys × route_length = total format."""
        result = build_capture_payout_embed("Criminal", "gold", 80000, reward_per_sys=3000, route_length=4)
        fields_by_name = {f["name"]: f["value"] for f in result["fields"]}
        assert "3,000 cr × 4" in fields_by_name["📍 System Checks"]
        assert "12,000 cr" in fields_by_name["📍 System Checks"]

    def test_total_payout_computed_from_capture_bonus_plus_sys(self):
        """Total payout = capture_bonus + max_sys_payout when no total_reward given."""
        # capture_bonus = int(80000 * 0.25) = 20000
        # max_sys = 3000 * 4 = 12000
        # total = 20000 + 12000 = 32000
        result = build_capture_payout_embed("Criminal", "gold", 80000, reward_per_sys=3000, route_length=4)
        fields_by_name = {f["name"]: f["value"] for f in result["fields"]}
        assert "32,000 cr" in fields_by_name["🏆 Total Payout"]

    def test_total_payout_without_sys_is_capture_bonus(self):
        """Total payout = capture_bonus when no system checks."""
        # capture_bonus = int(10000 * 0.25) = 2500
        result = build_capture_payout_embed("Criminal", "bronze", 10000)
        fields_by_name = {f["name"]: f["value"] for f in result["fields"]}
        assert "2,500 cr" in fields_by_name["🏆 Total Payout"]

    def test_explicit_total_reward_overrides_computed(self):
        """When total_reward is provided, it overrides the computed value."""
        result = build_capture_payout_embed("Criminal", "gold", 80000, total_reward=99999)
        fields_by_name = {f["name"]: f["value"] for f in result["fields"]}
        assert "99,999 cr" in fields_by_name["🏆 Total Payout"]

    def test_embed_is_dict_with_required_keys(self):
        """Result is a dict with title, description, color, fields."""
        result = build_capture_payout_embed("Criminal", "bronze", 10000)
        assert isinstance(result, dict)
        assert "title" in result
        assert "description" in result
        assert "color" in result
        assert "fields" in result

    def test_sys_checks_omitted_when_only_reward_per_sys_given(self):
        """System checks field omitted when only reward_per_sys given (no route_length)."""
        result = build_capture_payout_embed(
            "Criminal",
            "gold",
            80000,
            reward_per_sys=3000,
            # route_length not provided
        )
        fields_by_name = {f["name"]: f for f in result["fields"]}
        assert "📍 System Checks" not in fields_by_name

    def test_sys_checks_omitted_when_only_route_length_given(self):
        """System checks field omitted when only route_length given (no reward_per_sys)."""
        result = build_capture_payout_embed(
            "Criminal",
            "gold",
            80000,
            route_length=4,
            # reward_per_sys not provided
        )
        fields_by_name = {f["name"]: f for f in result["fields"]}
        assert "📍 System Checks" not in fields_by_name


# ===========================================================================
# Tests for build_capture_payout_embed — combat_result parameter (Bug 2B)
# ===========================================================================


def _make_combat_result(
    ship1_name="Eagle MK II",
    ship1_hp=500,
    ship1_dps=45.0,
    ship1_ttk=11.1,
    ship2_name="Outlaw",
    ship2_hp=300,
    ship2_dps=30.0,
    ship2_ttk=16.7,
    is_stalemate=False,
    pvc_armour_buff_applied=False,
    pvc_armour_buff_factor=1.5,
):
    """Return a minimal serialized FightResults dict for tests."""
    return {
        "ship1_stats": {
            "ship_name": ship1_name,
            "varied_hp": ship1_hp,
            "varied_dps": ship1_dps,
            "ttk": ship1_ttk,
        },
        "ship2_stats": {
            "ship_name": ship2_name,
            "varied_hp": ship2_hp,
            "varied_dps": ship2_dps,
            "ttk": ship2_ttk,
        },
        "metadata": {
            "pvc_armour_buff_applied": pvc_armour_buff_applied,
            "pvc_armour_buff_factor": pvc_armour_buff_factor,
        },
        "is_stalemate": is_stalemate,
    }


class TestBuildCapturePayoutEmbedCombatResult:
    """Bug 2B: Tests for combat_result parameter in build_capture_payout_embed.

    Acceptance criteria:
    - When combat_result is provided: ⚔️ Your Ship, 🤖 Criminal Ship, ✅ Result fields present
    - When combat_result is None: embed renders correctly without combat fields
    - TTK renders as '∞' when ttk is None (zero-DPS opponent)
    - 🛡️ Keith T Maxwell Buff field only appears when pvc_armour_buff_applied=True
    - combat_result dict with missing keys doesn't crash (.get() used defensively)
    """

    def test_no_combat_result_omits_combat_fields(self):
        """When combat_result=None, no combat fields appear."""
        result = build_capture_payout_embed("Criminal", "gold", 80000, combat_result=None)
        field_names = [f["name"] for f in result["fields"]]
        assert "⚔️ Your Ship" not in field_names
        assert "🤖 Criminal Ship" not in field_names
        assert "✅ Result" not in field_names

    def test_no_combat_result_still_has_payout_fields(self):
        """When combat_result=None, payout fields are still present."""
        result = build_capture_payout_embed("Criminal", "gold", 80000, combat_result=None)
        field_names = [f["name"] for f in result["fields"]]
        assert "🏆 Division" in field_names
        assert "⚔️ Claimed by" in field_names
        assert "💵 Base Reward" in field_names
        assert "🏆 Total Payout" in field_names

    def test_with_combat_result_has_your_ship_field(self):
        """When combat_result is provided, '⚔️ Your Ship' field appears."""
        result = build_capture_payout_embed("Criminal", "gold", 80000, combat_result=_make_combat_result())
        field_names = [f["name"] for f in result["fields"]]
        assert "⚔️ Your Ship" in field_names

    def test_with_combat_result_has_criminal_ship_field(self):
        """When combat_result is provided, '🤖 Criminal Ship' field appears."""
        result = build_capture_payout_embed("Criminal", "gold", 80000, combat_result=_make_combat_result())
        field_names = [f["name"] for f in result["fields"]]
        assert "🤖 Criminal Ship" in field_names

    def test_with_combat_result_has_result_field(self):
        """When combat_result is provided, '✅ Result' field appears."""
        result = build_capture_payout_embed("Criminal", "gold", 80000, combat_result=_make_combat_result())
        field_names = [f["name"] for f in result["fields"]]
        assert "✅ Result" in field_names

    def test_your_ship_field_contains_ship_name(self):
        """⚔️ Your Ship field value contains the ship name from ship1_stats."""
        cr = _make_combat_result(ship1_name="Eagle MK II")
        result = build_capture_payout_embed("Criminal", "gold", 80000, combat_result=cr)
        fields_by_name = {f["name"]: f["value"] for f in result["fields"]}
        assert "Eagle MK II" in fields_by_name["⚔️ Your Ship"]

    def test_criminal_ship_field_contains_ship_name(self):
        """🤖 Criminal Ship field value contains the ship name from ship2_stats."""
        cr = _make_combat_result(ship2_name="Outlaw")
        result = build_capture_payout_embed("Criminal", "gold", 80000, combat_result=cr)
        fields_by_name = {f["name"]: f["value"] for f in result["fields"]}
        assert "Outlaw" in fields_by_name["🤖 Criminal Ship"]

    def test_result_field_shows_victory_when_not_stalemate(self):
        """✅ Result field shows 'Combat victory!' when is_stalemate=False."""
        cr = _make_combat_result(is_stalemate=False)
        result = build_capture_payout_embed("Criminal", "gold", 80000, combat_result=cr)
        fields_by_name = {f["name"]: f["value"] for f in result["fields"]}
        assert "victory" in fields_by_name["✅ Result"].lower()

    def test_result_field_shows_stalemate_when_stalemate(self):
        """✅ Result field shows 'Stalemate' when is_stalemate=True."""
        cr = _make_combat_result(is_stalemate=True)
        result = build_capture_payout_embed("Criminal", "gold", 80000, combat_result=cr)
        fields_by_name = {f["name"]: f["value"] for f in result["fields"]}
        assert "stalemate" in fields_by_name["✅ Result"].lower()

    def test_combat_fields_precede_payout_fields(self):
        """Combat summary fields appear before payout fields."""
        cr = _make_combat_result()
        result = build_capture_payout_embed("Criminal", "gold", 80000, combat_result=cr)
        field_names = [f["name"] for f in result["fields"]]
        your_ship_idx = field_names.index("⚔️ Your Ship")
        division_idx = field_names.index("🏆 Division")
        assert your_ship_idx < division_idx, "Combat fields must precede payout fields"

    def test_pvc_buff_field_absent_when_not_applied(self):
        """🛡️ Keith T Maxwell Buff field is absent when pvc_armour_buff_applied=False."""
        cr = _make_combat_result(pvc_armour_buff_applied=False)
        result = build_capture_payout_embed("Criminal", "gold", 80000, combat_result=cr)
        field_names = [f["name"] for f in result["fields"]]
        assert not any("Keith T Maxwell" in n for n in field_names)

    def test_pvc_buff_field_present_when_applied(self):
        """🛡️ Keith T Maxwell Buff field appears when pvc_armour_buff_applied=True."""
        cr = _make_combat_result(pvc_armour_buff_applied=True, pvc_armour_buff_factor=1.5)
        result = build_capture_payout_embed("Criminal", "gold", 80000, combat_result=cr)
        field_names = [f["name"] for f in result["fields"]]
        assert any("Keith T Maxwell" in n for n in field_names)

    def test_pvc_buff_shows_factor(self):
        """🛡️ Keith T Maxwell Buff field value includes the buff factor."""
        cr = _make_combat_result(pvc_armour_buff_applied=True, pvc_armour_buff_factor=2.0)
        result = build_capture_payout_embed("Criminal", "gold", 80000, combat_result=cr)
        fields_by_name = {f["name"]: f["value"] for f in result["fields"]}
        buff_field = next((f["value"] for f in result["fields"] if "Keith T Maxwell" in f["name"]), None)
        assert buff_field is not None
        assert "2.0" in buff_field

    def test_ttk_none_renders_as_infinity_your_ship(self):
        """TTK=None in ship1_stats renders as '∞' in ⚔️ Your Ship field (zero-DPS opponent)."""
        cr = _make_combat_result(ship1_ttk=None)
        result = build_capture_payout_embed("Criminal", "gold", 80000, combat_result=cr)
        fields_by_name = {f["name"]: f["value"] for f in result["fields"]}
        assert "∞" in fields_by_name["⚔️ Your Ship"]

    def test_ttk_none_renders_as_infinity_criminal_ship(self):
        """TTK=None in ship2_stats renders as '∞' in 🤖 Criminal Ship field (zero-DPS opponent)."""
        cr = _make_combat_result(ship2_ttk=None)
        result = build_capture_payout_embed("Criminal", "gold", 80000, combat_result=cr)
        fields_by_name = {f["name"]: f["value"] for f in result["fields"]}
        assert "∞" in fields_by_name["🤖 Criminal Ship"]

    def test_ttk_value_renders_with_seconds_suffix(self):
        """Finite TTK renders as 'X.Xs' in ship field."""
        cr = _make_combat_result(ship1_ttk=12.345)
        result = build_capture_payout_embed("Criminal", "gold", 80000, combat_result=cr)
        fields_by_name = {f["name"]: f["value"] for f in result["fields"]}
        assert "12.3s" in fields_by_name["⚔️ Your Ship"]

    # --- Adversarial: missing keys in combat_result dict ---

    def test_empty_combat_result_dict_does_not_crash(self):
        """An empty combat_result dict uses .get() defaults — no crash."""
        result = build_capture_payout_embed("Criminal", "gold", 80000, combat_result={})
        field_names = [f["name"] for f in result["fields"]]
        # Should still have the combat fields (with '?' defaults for missing names)
        assert "⚔️ Your Ship" in field_names
        assert "🤖 Criminal Ship" in field_names
        assert "✅ Result" in field_names

    def test_combat_result_missing_ship1_stats_does_not_crash(self):
        """Missing ship1_stats key falls back gracefully via .get() → {}."""
        cr = {"ship2_stats": {"ship_name": "Enemy"}, "is_stalemate": False, "metadata": {}}
        result = build_capture_payout_embed("Criminal", "gold", 80000, combat_result=cr)
        field_names = [f["name"] for f in result["fields"]]
        assert "⚔️ Your Ship" in field_names

    def test_combat_result_missing_ship2_stats_does_not_crash(self):
        """Missing ship2_stats key falls back gracefully via .get() → {}."""
        cr = {"ship1_stats": {"ship_name": "Player"}, "is_stalemate": False, "metadata": {}}
        result = build_capture_payout_embed("Criminal", "gold", 80000, combat_result=cr)
        field_names = [f["name"] for f in result["fields"]]
        assert "🤖 Criminal Ship" in field_names

    def test_combat_result_missing_metadata_does_not_crash(self):
        """Missing metadata key falls back gracefully via .get() → {}."""
        cr = {
            "ship1_stats": {"ship_name": "Eagle"},
            "ship2_stats": {"ship_name": "Foe"},
            "is_stalemate": False,
            # 'metadata' key absent
        }
        result = build_capture_payout_embed("Criminal", "gold", 80000, combat_result=cr)
        field_names = [f["name"] for f in result["fields"]]
        assert "⚔️ Your Ship" in field_names
        # No buff field since metadata absent (should not crash)
        assert not any("Keith T Maxwell" in n for n in field_names)

    def test_combat_result_null_ship_stats_does_not_crash(self):
        """ship1_stats=None handled by 'or {}' fallback — no crash."""
        cr = {"ship1_stats": None, "ship2_stats": None, "metadata": None, "is_stalemate": False}
        result = build_capture_payout_embed("Criminal", "gold", 80000, combat_result=cr)
        field_names = [f["name"] for f in result["fields"]]
        assert "⚔️ Your Ship" in field_names
        assert "🤖 Criminal Ship" in field_names

    def test_both_ttks_none_does_not_crash(self):
        """Both ship1 and ship2 ttk=None — renders '∞' for both without crash."""
        cr = _make_combat_result(ship1_ttk=None, ship2_ttk=None)
        result = build_capture_payout_embed("Criminal", "gold", 80000, combat_result=cr)
        fields_by_name = {f["name"]: f["value"] for f in result["fields"]}
        assert "∞" in fields_by_name["⚔️ Your Ship"]
        assert "∞" in fields_by_name["🤖 Criminal Ship"]

    def test_win_user_id_none_bounty_uses_default_winner_name(self):
        """When no winner_name is provided (default), 'A bounty hunter' appears in Claimed by."""
        result = build_capture_payout_embed("Criminal", "gold", 80000, combat_result=_make_combat_result())
        fields_by_name = {f["name"]: f["value"] for f in result["fields"]}
        assert fields_by_name["⚔️ Claimed by"] == "A bounty hunter"
