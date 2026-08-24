"""Schema-level tests for GameConstantsOverridesMixin / UpdateConfigRequest (issue #70).

Exercises strict-type gating, numeric le= / ge= bounds, cross-field loot qty
ordering, the existing bounty-delay ordering constraint, and the removal of
kaamo_max_capacity from both _OVERRIDE_FIELDS and UpdateConfigRequest.

No DB, no HTTP — Pydantic models are instantiated directly.
"""

import pytest
from api.schemas.config_schema import UpdateConfigRequest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE = {"guild_id": 12345}


def _make(**kwargs) -> UpdateConfigRequest:
    """Return a valid UpdateConfigRequest with only the given overrides set."""
    return UpdateConfigRequest(**{**_BASE, **kwargs})


def _fails(**kwargs) -> None:
    """Assert that instantiation raises ValidationError."""
    with pytest.raises(ValidationError):
        _make(**kwargs)


# ===========================================================================
# 1. Strict typing
# ===========================================================================


class TestStrictTyping:
    """ConfigDict(strict=True): cross-type coercions are rejected."""

    # -- int fields ---

    def test_string_for_int_field_rejected(self):
        """str "5" for duel_cloak_chance (int) must be rejected in strict mode."""
        _fails(duel_cloak_chance="5")

    def test_float_for_int_field_rejected(self):
        """float 5.5 for duel_cloak_chance (int) must be rejected in strict mode."""
        _fails(duel_cloak_chance=5.5)

    def test_bool_for_int_field_rejected(self):
        """bool True for duel_cloak_chance (int) must be rejected in strict mode."""
        _fails(duel_cloak_chance=True)

    # -- bool field (criminal_exclude_emp_weapons) ---

    def test_int_for_bool_field_rejected(self):
        """int 1 for criminal_exclude_emp_weapons (bool) must be rejected in strict mode."""
        _fails(criminal_exclude_emp_weapons=1)

    def test_string_for_bool_field_rejected(self):
        """str "true" for criminal_exclude_emp_weapons (bool) must be rejected."""
        _fails(criminal_exclude_emp_weapons="true")

    def test_real_bool_accepted_for_bool_field(self):
        """A real Python bool is accepted for criminal_exclude_emp_weapons."""
        req = _make(criminal_exclude_emp_weapons=True)
        assert req.criminal_exclude_emp_weapons is True

    def test_real_bool_false_accepted_for_bool_field(self):
        """False is also a valid bool."""
        req = _make(criminal_exclude_emp_weapons=False)
        assert req.criminal_exclude_emp_weapons is False

    # -- int widening to float ---

    def test_int_accepted_for_float_field(self):
        """int 1 for shop_combat_module_prob (float) should be accepted as lossless widening.

        Pydantic v2 strict mode allows int→float because the conversion is always
        lossless.  If this test fails it is a surprise worth flagging: do NOT
        change the schema — just note it in the test run output.
        """
        # NOTE: If this assertion fails, Pydantic v2 strict mode on this build does
        # NOT widen int→float.  Report but do not patch the schema.
        req = _make(shop_combat_module_prob=1)
        assert req.shop_combat_module_prob == 1.0

    def test_int_zero_accepted_for_float_field(self):
        """int 0 is also losslessly widened to float."""
        req = _make(shop_combat_module_prob=0)
        assert req.shop_combat_module_prob == 0.0


# ===========================================================================
# 2. Bounds — le= / ge= guards
# ===========================================================================


class TestBounds:
    """Each representative numeric field: value at le= passes; le+1 fails; negative fails."""

    # --- close_bounty_threshold (ge=1, le=50) ---

    def test_close_bounty_threshold_at_le_passes(self):
        req = _make(close_bounty_threshold=50)
        assert req.close_bounty_threshold == 50

    def test_close_bounty_threshold_above_le_rejected(self):
        _fails(close_bounty_threshold=51)

    def test_close_bounty_threshold_ge_enforced(self):
        _fails(close_bounty_threshold=0)  # ge=1

    # --- long_range_threshold_m (ge=0, le=50_000) ---

    def test_long_range_threshold_m_at_le_passes(self):
        req = _make(long_range_threshold_m=50_000)
        assert req.long_range_threshold_m == 50_000

    def test_long_range_threshold_m_above_le_rejected(self):
        _fails(long_range_threshold_m=50_001)

    def test_long_range_threshold_m_negative_rejected(self):
        _fails(long_range_threshold_m=-1)

    # --- classic_credits_per_check (ge=0, le=1_000_000) ---

    def test_classic_credits_per_check_at_le_passes(self):
        req = _make(classic_credits_per_check=1_000_000)
        assert req.classic_credits_per_check == 1_000_000

    def test_classic_credits_per_check_above_le_rejected(self):
        _fails(classic_credits_per_check=1_000_001)

    def test_classic_credits_per_check_negative_rejected(self):
        _fails(classic_credits_per_check=-1)

    # --- shop_default_ships_num (ge=0, le=50) ---

    def test_shop_default_ships_num_at_le_passes(self):
        req = _make(shop_default_ships_num=50)
        assert req.shop_default_ships_num == 50

    def test_shop_default_ships_num_above_le_rejected(self):
        _fails(shop_default_ships_num=51)

    # --- shop_default_weapons_num (ge=0, le=50) ---

    def test_shop_default_weapons_num_at_le_passes(self):
        req = _make(shop_default_weapons_num=50)
        assert req.shop_default_weapons_num == 50

    def test_shop_default_weapons_num_above_le_rejected(self):
        _fails(shop_default_weapons_num=51)

    # --- shop_default_modules_num (ge=0, le=50) ---

    def test_shop_default_modules_num_at_le_passes(self):
        req = _make(shop_default_modules_num=50)
        assert req.shop_default_modules_num == 50

    def test_shop_default_modules_num_above_le_rejected(self):
        _fails(shop_default_modules_num=51)

    # --- shop_default_turrets_num (ge=0, le=50) ---

    def test_shop_default_turrets_num_at_le_passes(self):
        req = _make(shop_default_turrets_num=50)
        assert req.shop_default_turrets_num == 50

    def test_shop_default_turrets_num_above_le_rejected(self):
        _fails(shop_default_turrets_num=51)

    # --- shop_combat_module_prob (ge=0.0, le=1.0) ---

    def test_shop_combat_module_prob_at_le_passes(self):
        req = _make(shop_combat_module_prob=1.0)
        assert req.shop_combat_module_prob == pytest.approx(1.0)

    def test_shop_combat_module_prob_above_le_rejected(self):
        _fails(shop_combat_module_prob=1.1)

    def test_shop_combat_module_prob_negative_rejected(self):
        _fails(shop_combat_module_prob=-0.1)

    # --- bounty_delay_random_min (ge=0, le=1440) ---

    def test_bounty_delay_random_min_at_le_passes(self):
        req = _make(bounty_delay_random_min=1440)
        assert req.bounty_delay_random_min == 1440

    def test_bounty_delay_random_min_above_le_rejected(self):
        _fails(bounty_delay_random_min=1441)

    def test_bounty_delay_random_min_negative_rejected(self):
        _fails(bounty_delay_random_min=-1)

    # --- bounty_spawn_jitter (ge=0, le=3600) ---

    def test_bounty_spawn_jitter_at_le_passes(self):
        req = _make(bounty_spawn_jitter=3600)
        assert req.bounty_spawn_jitter == 3600

    def test_bounty_spawn_jitter_above_le_rejected(self):
        _fails(bounty_spawn_jitter=3601)

    # --- check_cooldown (ge=0, le=86400) ---

    def test_check_cooldown_at_le_passes(self):
        req = _make(check_cooldown=86400)
        assert req.check_cooldown == 86400

    def test_check_cooldown_above_le_rejected(self):
        _fails(check_cooldown=86401)

    # --- guild_activity_decay_rate (ge=0.0, le=1.0) ---

    def test_guild_activity_decay_rate_at_le_passes(self):
        req = _make(guild_activity_decay_rate=1.0)
        assert req.guild_activity_decay_rate == pytest.approx(1.0)

    def test_guild_activity_decay_rate_above_le_rejected(self):
        _fails(guild_activity_decay_rate=1.1)

    # --- min_guild_activity (ge=0.0, le=100.0) ---

    def test_min_guild_activity_at_le_passes(self):
        req = _make(min_guild_activity=100.0)
        assert req.min_guild_activity == pytest.approx(100.0)

    def test_min_guild_activity_above_le_rejected(self):
        _fails(min_guild_activity=100.1)

    # --- activity_temp_per_player (ge=0, le=100) ---

    def test_activity_temp_per_player_at_le_passes(self):
        req = _make(activity_temp_per_player=100)
        assert req.activity_temp_per_player == 100

    def test_activity_temp_per_player_above_le_rejected(self):
        _fails(activity_temp_per_player=101)

    # --- loot qty le=1000 (representative: band1_qty_min, band2_qty_max, band3_qty_mode) ---

    def test_loot_band1_qty_min_at_le_passes(self):
        req = _make(loot_band1_qty_min=1000)
        assert req.loot_band1_qty_min == 1000

    def test_loot_band1_qty_min_above_le_rejected(self):
        _fails(loot_band1_qty_min=1001)

    def test_loot_band2_qty_max_at_le_passes(self):
        req = _make(loot_band2_qty_max=1000)
        assert req.loot_band2_qty_max == 1000

    def test_loot_band2_qty_max_above_le_rejected(self):
        _fails(loot_band2_qty_max=1001)

    def test_loot_band3_qty_mode_at_le_passes(self):
        req = _make(loot_band3_qty_mode=1000)
        assert req.loot_band3_qty_mode == 1000

    def test_loot_band3_qty_mode_above_le_rejected(self):
        _fails(loot_band3_qty_mode=1001)

    # --- loot_commodity_sell_fraction (ge=0.0, le=10.0) ---

    def test_loot_commodity_sell_fraction_at_le_passes(self):
        req = _make(loot_commodity_sell_fraction=10.0)
        assert req.loot_commodity_sell_fraction == pytest.approx(10.0)

    def test_loot_commodity_sell_fraction_above_le_rejected(self):
        _fails(loot_commodity_sell_fraction=10.01)

    def test_loot_commodity_sell_fraction_negative_rejected(self):
        _fails(loot_commodity_sell_fraction=-0.1)


# ===========================================================================
# 3. Loot qty ordering validator (validate_loot_qty_ordering)
# ===========================================================================


class TestLootQtyOrdering:
    """validate_loot_qty_ordering: min <= mode <= max, checked only when both sides present."""

    # --- Band 1 ---

    def test_band1_min_greater_than_max_rejected(self):
        """loot_band1_qty_min > loot_band1_qty_max in same request must be rejected."""
        _fails(loot_band1_qty_min=50, loot_band1_qty_max=10)

    def test_band1_mode_below_min_rejected(self):
        """mode < min is rejected."""
        _fails(loot_band1_qty_min=20, loot_band1_qty_mode=10)

    def test_band1_mode_above_max_rejected(self):
        """mode > max is rejected."""
        _fails(loot_band1_qty_max=30, loot_band1_qty_mode=50)

    def test_band1_valid_full_triple_accepted(self):
        """min <= mode <= max is valid."""
        req = _make(loot_band1_qty_min=5, loot_band1_qty_mode=10, loot_band1_qty_max=20)
        assert req.loot_band1_qty_min == 5
        assert req.loot_band1_qty_mode == 10
        assert req.loot_band1_qty_max == 20

    def test_band1_equal_triple_accepted(self):
        """min == mode == max is valid (degenerate triangular)."""
        req = _make(loot_band1_qty_min=10, loot_band1_qty_mode=10, loot_band1_qty_max=10)
        assert req.loot_band1_qty_min == 10

    def test_band1_only_mode_present_accepted(self):
        """Partial update with only mode is not cross-checked (no min/max in this request)."""
        req = _make(loot_band1_qty_mode=42)
        assert req.loot_band1_qty_mode == 42

    # --- Band 2 ---

    def test_band2_min_greater_than_max_rejected(self):
        """loot_band2_qty_min > loot_band2_qty_max rejected."""
        _fails(loot_band2_qty_min=100, loot_band2_qty_max=50)

    def test_band2_mode_below_min_rejected(self):
        """mode < min rejected for band 2."""
        _fails(loot_band2_qty_min=30, loot_band2_qty_mode=5)

    def test_band2_mode_above_max_rejected(self):
        """mode > max rejected for band 2."""
        _fails(loot_band2_qty_max=40, loot_band2_qty_mode=99)

    def test_band2_valid_full_triple_accepted(self):
        """Band 2 valid triple."""
        req = _make(loot_band2_qty_min=10, loot_band2_qty_mode=25, loot_band2_qty_max=50)
        assert req.loot_band2_qty_mode == 25

    # --- Band 3 ---

    def test_band3_min_greater_than_max_rejected(self):
        """loot_band3_qty_min > loot_band3_qty_max rejected."""
        _fails(loot_band3_qty_min=200, loot_band3_qty_max=100)

    def test_band3_mode_below_min_rejected(self):
        """mode < min rejected for band 3."""
        _fails(loot_band3_qty_min=50, loot_band3_qty_mode=20)

    def test_band3_mode_above_max_rejected(self):
        """mode > max rejected for band 3."""
        _fails(loot_band3_qty_max=80, loot_band3_qty_mode=100)

    def test_band3_valid_full_triple_accepted(self):
        """Band 3 valid triple."""
        req = _make(loot_band3_qty_min=1, loot_band3_qty_mode=5, loot_band3_qty_max=10)
        assert req.loot_band3_qty_mode == 5

    def test_band3_only_mode_present_accepted(self):
        """Partial update with only mode for band 3 is fine."""
        req = _make(loot_band3_qty_mode=7)
        assert req.loot_band3_qty_mode == 7

    def test_independent_bands_do_not_cross_check(self):
        """Each band is validated independently — valid band1 and band3 together is fine."""
        req = _make(
            loot_band1_qty_min=1,
            loot_band1_qty_mode=5,
            loot_band1_qty_max=10,
            loot_band3_qty_min=100,
            loot_band3_qty_mode=200,
            loot_band3_qty_max=500,
        )
        assert req.loot_band1_qty_max == 10
        assert req.loot_band3_qty_max == 500


# ===========================================================================
# 4. Bounty delay min <= max (existing validator still enforced)
# ===========================================================================


class TestBountyDelayOrdering:
    """validate_bounty_delay_range: bounty_delay_random_min must be <= max."""

    def test_min_greater_than_max_rejected(self):
        """bounty_delay_random_min=10, max=5 must be rejected."""
        _fails(bounty_delay_random_min=10, bounty_delay_random_max=5)

    def test_min_equal_to_max_accepted(self):
        """min == max is valid."""
        req = _make(bounty_delay_random_min=5, bounty_delay_random_max=5)
        assert req.bounty_delay_random_min == 5

    def test_min_less_than_max_accepted(self):
        """min < max is valid."""
        req = _make(bounty_delay_random_min=3, bounty_delay_random_max=7)
        assert req.bounty_delay_random_max == 7

    def test_only_min_present_accepted(self):
        """Partial update with only min — no cross-check triggers."""
        req = _make(bounty_delay_random_min=5)
        assert req.bounty_delay_random_min == 5

    def test_only_max_present_accepted(self):
        """Partial update with only max — no cross-check triggers."""
        req = _make(bounty_delay_random_max=10)
        assert req.bounty_delay_random_max == 10


# ===========================================================================
# 5. kaamo_max_capacity removed
# ===========================================================================


class TestKaamoRemoval:
    """kaamo_max_capacity must not appear in _OVERRIDE_FIELDS or UpdateConfigRequest."""

    def test_kaamo_not_in_override_fields(self):
        """_OVERRIDE_FIELDS must not contain 'kaamo_max_capacity' (issue #70)."""
        from api.routers.config import _OVERRIDE_FIELDS

        assert "kaamo_max_capacity" not in _OVERRIDE_FIELDS

    def test_kaamo_not_in_update_config_request_fields(self):
        """UpdateConfigRequest.model_fields must not include 'kaamo_max_capacity'."""
        assert "kaamo_max_capacity" not in UpdateConfigRequest.model_fields

    def test_kaamo_kwarg_ignored_or_raises(self):
        """Passing kaamo_max_capacity to UpdateConfigRequest must not silently set it.

        Because UpdateConfigRequest inherits strict=True (which sets extra='ignore'
        by default in Pydantic v2), an unknown field is silently dropped rather than
        raising.  What matters is that the field is absent from the parsed result.
        """
        # If Pydantic raises, fine.  If it silently ignores, also fine — the field
        # must simply not be present on the model.
        try:
            req = UpdateConfigRequest(guild_id=12345, kaamo_max_capacity=999)
            assert not hasattr(req, "kaamo_max_capacity") or getattr(req, "kaamo_max_capacity", None) is None
        except ValidationError:
            pass  # Also acceptable — the field is unknown
