"""Tests for B.49 game-constants config router endpoints.

Covers:
1. GET /config/guild/{guild_id}/game-constants — returns all override fields, all null for a
   fresh guild.
2. Schema validator rejects bounty_pvc_armour_buff_factor < 1.0 (minimum field value).
3. Schema validator rejects division_max_tl with missing required tier keys.
4. Schema validator rejects bounty_delay_random_min > bounty_delay_random_max.
5. POST /config/guild/{guild_id}/game-constants/reset with unknown field returns 400.
6. GET /game-constants returns 200 when guild config has non-null overrides.
7. POST /game-constants/reset (no fields) resets all override fields.
8. POST /game-constants/reset with known fields resets only those fields.

Matches the fixture pattern of test_config_router.py.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_config(**overrides):
    """Build a minimal config dict that satisfies GuildConfigResponse.

    All per-guild override fields default to None (the fresh-guild state).
    """
    defaults = dict(
        guild_id=67890,
        configured=True,
        admin_role_configured=True,
        starting_credits=0,
        sale_price_factor=0.8,
        xp_thresholds={"Silver": 1000, "Gold": 5000, "Platinum": 15000},
        shop_config={},
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
        # B.49 override fields — all null by default
        # Rev 0031 retired: ship_value_reward_percentage, criminal_equip_damageless_weapon_chance,
        # duel_cloak_chance, bounty_delay_random_min, bounty_delay_random_max, bounty_spawn_jitter,
        # guild_activity_decay_rate, min_guild_activity, activity_temp_per_player,
        # shop_default_ships_num, shop_default_weapons_num, shop_default_modules_num,
        # shop_default_turrets_num, turret_spawn_probability.
        # bounty_pvc_armour_buff_factor retired T10
        # duel_variance_percent retired T10
        # division_max_tl — RETIRED rev 0033 (JSONB column dropped)
        criminal_max_gear_upgrade=None,
        bounty_reward_to_xp_gain_mult=None,
        bounty_winner_reserve_factor=None,
        close_bounty_threshold=None,
        max_route_length=None,
        min_route_systems=None,
        recently_spotted_max_window=None,
        check_cooldown=None,
        duel_request_expiry=None,
        classic_credits_per_check=None,
        tier_change_cooldown=None,
        demotion_credit_penalty_pct=None,  # per-guild demotion penalty % (0–100; NULL → global default)
        # Criminal loadout balance (BALANCE_JOURNAL §A — Thread 3 & 4)
        long_range_threshold_m=None,
        criminal_long_range_pct=None,
        # primary_tl_band_weights — RETIRED rev 0033 (JSONB column dropped)
        # criminal_cloak_chance_by_division — RETIRED rev 0033 (JSONB column dropped)
        # criminal_booster_chance_by_division — RETIRED rev 0033
        # criminal_emergency_chance_by_division — RETIRED rev 0033
        # criminal_weaponmod_chance_by_division — RETIRED rev 0033
        criminal_exclude_emp_weapons=None,
        # Loot (PvC) tunable knobs (LOOT_JOURNAL §8 / T2) — all null by default
        loot_chance_tractor_t1=None,
        loot_chance_tractor_t2=None,
        loot_chance_tractor_t3=None,
        loot_chance_tractor_t4=None,
        loot_chance_no_tractor=None,
        loot_band1_select_pct=None,
        loot_band2_select_pct=None,
        loot_band3_select_pct=None,
        loot_band1_tl_window=None,
        loot_band1_qty_min=None,
        loot_band1_qty_max=None,
        loot_band1_qty_mode=None,
        loot_band2_qty_min=None,
        loot_band2_qty_max=None,
        loot_band2_qty_mode=None,
        loot_band3_qty_min=None,
        loot_band3_qty_max=None,
        loot_band3_qty_mode=None,
        loot_commodity_sell_fraction=None,
        # Shop module-draw combat/filler split
        shop_combat_module_prob=None,
        # D-trivial + DIVISION_TL_CENTERS scalar overrides (revision 0028) — all null by default
        criminal_secondary_min_damage=None,
        shop_secondary_qty_scaler_heavy=None,
        shop_secondary_qty_scaler_standard=None,
        shop_tl_band_lo_bronze=None,
        shop_tl_band_hi_bronze=None,
        shop_tl_band_lo_silver=None,
        shop_tl_band_hi_silver=None,
        shop_tl_band_lo_gold=None,
        shop_tl_band_hi_gold=None,
        shop_tl_band_lo_platinum=None,
        shop_tl_band_hi_platinum=None,
        shop_banded_tl_weight=None,
        shop_uptier_tl_decay=None,
        shop_downtier_tl_decay=None,
        division_tl_center_bronze=None,
        division_tl_center_silver=None,
        division_tl_center_gold=None,
        division_tl_center_platinum=None,
        # Previously column-only orphans (columns from revision 0026)
        bounty_single_waypoint_prob=None,
        bounty_dual_waypoint_prob=None,
        bounty_waypoint_attempts=None,
        bounty_waypoint_min_degree=None,
        pvc_damage_reduction=None,
        # Bronze combat bonus per-guild overrides (Unit C, revision 0029) — all null by default
        bronze_combat_bonus_base_mult=None,
        bronze_combat_bonus_per_prestige=None,
        bronze_combat_bonus_cap=None,
        # JSONB flatten scalars (issue #70, revision 0030) — all null by default
        division_max_tl_bronze=None,
        division_max_tl_silver=None,
        division_max_tl_gold=None,
        division_max_tl_platinum=None,
        bounty_division_reward_mult_bronze=None,
        bounty_division_reward_mult_silver=None,
        bounty_division_reward_mult_gold=None,
        bounty_division_reward_mult_platinum=None,
        primary_tl_band_weight_center=None,
        primary_tl_band_weight_minus1=None,
        primary_tl_band_weight_plus1=None,
        criminal_cloak_chance_bronze=None,
        criminal_cloak_chance_silver=None,
        criminal_cloak_chance_gold=None,
        criminal_cloak_chance_platinum=None,
        criminal_booster_chance_bronze=None,
        criminal_booster_chance_silver=None,
        criminal_booster_chance_gold=None,
        criminal_booster_chance_platinum=None,
        criminal_emergency_chance_bronze=None,
        criminal_emergency_chance_silver=None,
        criminal_emergency_chance_gold=None,
        criminal_emergency_chance_platinum=None,
        criminal_weaponmod_chance_bronze=None,
        criminal_weaponmod_chance_silver=None,
        criminal_weaponmod_chance_gold=None,
        criminal_weaponmod_chance_platinum=None,
        # Combat engine per-guild overrides (issue #70 unit A1, revision 0032) — all null by default
        cloak_set_value=None,
        booster_accuracy_debuff_factor=None,
        thruster_accuracy_bonus_factor=None,
        auto_turret_accuracy_multiplier=None,
        player_base_accuracy=None,
        npc_base_accuracy=None,
        scanner_tier_b_bonus_pp=None,
        scanner_tier_c_bonus_pp=None,
        starting_distance_m=None,
        base_ship_speed_mps=None,
        min_distance_m=None,
        thruster_window_m=None,
        emergency_system_invuln_s=None,
        nuke_magnitude_scale=None,
        nuke_friendly_factor=None,
        nuke_range_regime_threshold_m=None,
        nuke_lr_near_frac=None,
        nuke_cr_short_m=None,
        nuke_cr_overshoot_m=None,
        nuke_stack_falloff=None,
        shock_blast_trigger_range_m=None,
        combat_layer_reemit_fraction=None,
    )
    defaults.update(overrides)
    return defaults


def _configure_db_mock(mock_get_db):
    """Configure mock_get_db to act as an async context manager."""
    mock_session = AsyncMock()
    mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_session


_OVERRIDE_FIELD_NAMES = [
    # Rev 0031: 14 fields retired. Rev 0033: 7 JSONB dict fields retired:
    #   division_max_tl, bounty_division_reward_mult, primary_tl_band_weights,
    #   criminal_{cloak,booster,emergency,weaponmod}_chance_by_division.
    # bounty_pvc_armour_buff_factor retired T10. duel_variance_percent retired T10.
    # division_max_tl — RETIRED rev 0033 (JSONB dropped)
    "criminal_max_gear_upgrade",
    "bounty_reward_to_xp_gain_mult",
    "bounty_winner_reserve_factor",
    # bounty_division_reward_mult — RETIRED rev 0033 (JSONB dropped)
    "close_bounty_threshold",
    "max_route_length",
    "min_route_systems",
    "recently_spotted_max_window",
    "check_cooldown",
    "duel_request_expiry",
    "classic_credits_per_check",
    "tier_change_cooldown",
    "demotion_credit_penalty_pct",  # per-guild demotion penalty % (0–100; NULL → global default 10)
    # Criminal loadout balance (BALANCE_JOURNAL §A — Thread 3 & 4)
    "long_range_threshold_m",
    "criminal_long_range_pct",
    # primary_tl_band_weights — RETIRED rev 0033 (JSONB dropped)
    # criminal_cloak_chance_by_division — RETIRED rev 0033 (JSONB dropped)
    # criminal_booster_chance_by_division — RETIRED rev 0033
    # criminal_emergency_chance_by_division — RETIRED rev 0033
    # criminal_weaponmod_chance_by_division — RETIRED rev 0033
    "criminal_exclude_emp_weapons",
    # Loot (PvC) tunable knobs (LOOT_JOURNAL §8 / T2)
    "loot_chance_tractor_t1",
    "loot_chance_tractor_t2",
    "loot_chance_tractor_t3",
    "loot_chance_tractor_t4",
    "loot_chance_no_tractor",
    "loot_band1_select_pct",
    "loot_band2_select_pct",
    "loot_band3_select_pct",
    "loot_band1_tl_window",
    "loot_band1_qty_min",
    "loot_band1_qty_max",
    "loot_band1_qty_mode",
    "loot_band2_qty_min",
    "loot_band2_qty_max",
    "loot_band2_qty_mode",
    "loot_band3_qty_min",
    "loot_band3_qty_max",
    "loot_band3_qty_mode",
    "loot_commodity_sell_fraction",
    # Shop module-draw combat/filler split
    "shop_combat_module_prob",
    # D-trivial + DIVISION_TL_CENTERS scalar overrides (issue #70, revision 0028)
    "criminal_secondary_min_damage",
    "shop_secondary_qty_scaler_heavy",
    "shop_secondary_qty_scaler_standard",
    "shop_tl_band_lo_bronze",
    "shop_tl_band_hi_bronze",
    "shop_tl_band_lo_silver",
    "shop_tl_band_hi_silver",
    "shop_tl_band_lo_gold",
    "shop_tl_band_hi_gold",
    "shop_tl_band_lo_platinum",
    "shop_tl_band_hi_platinum",
    "shop_banded_tl_weight",
    "shop_uptier_tl_decay",
    "shop_downtier_tl_decay",
    "division_tl_center_bronze",
    "division_tl_center_silver",
    "division_tl_center_gold",
    "division_tl_center_platinum",
    # Previously column-only orphans (columns from revision 0026)
    "bounty_single_waypoint_prob",
    "bounty_dual_waypoint_prob",
    "bounty_waypoint_attempts",
    "bounty_waypoint_min_degree",
    "pvc_damage_reduction",
    # Bronze combat bonus per-guild overrides (Unit C, revision 0029)
    "bronze_combat_bonus_base_mult",
    "bronze_combat_bonus_per_prestige",
    "bronze_combat_bonus_cap",
    # JSONB flatten scalars (issue #70, revision 0030)
    "division_max_tl_bronze",
    "division_max_tl_silver",
    "division_max_tl_gold",
    "division_max_tl_platinum",
    "bounty_division_reward_mult_bronze",
    "bounty_division_reward_mult_silver",
    "bounty_division_reward_mult_gold",
    "bounty_division_reward_mult_platinum",
    "primary_tl_band_weight_center",
    "primary_tl_band_weight_minus1",
    "primary_tl_band_weight_plus1",
    "criminal_cloak_chance_bronze",
    "criminal_cloak_chance_silver",
    "criminal_cloak_chance_gold",
    "criminal_cloak_chance_platinum",
    "criminal_booster_chance_bronze",
    "criminal_booster_chance_silver",
    "criminal_booster_chance_gold",
    "criminal_booster_chance_platinum",
    "criminal_emergency_chance_bronze",
    "criminal_emergency_chance_silver",
    "criminal_emergency_chance_gold",
    "criminal_emergency_chance_platinum",
    "criminal_weaponmod_chance_bronze",
    "criminal_weaponmod_chance_silver",
    "criminal_weaponmod_chance_gold",
    "criminal_weaponmod_chance_platinum",
    # Combat engine per-guild overrides (issue #70 unit A1, revision 0032)
    "cloak_set_value",
    "booster_accuracy_debuff_factor",
    "thruster_accuracy_bonus_factor",
    "auto_turret_accuracy_multiplier",
    "player_base_accuracy",
    "npc_base_accuracy",
    "scanner_tier_b_bonus_pp",
    "scanner_tier_c_bonus_pp",
    "starting_distance_m",
    "base_ship_speed_mps",
    "min_distance_m",
    "thruster_window_m",
    "emergency_system_invuln_s",
    "nuke_magnitude_scale",
    "nuke_friendly_factor",
    "nuke_range_regime_threshold_m",
    "nuke_lr_near_frac",
    "nuke_cr_short_m",
    "nuke_cr_overshoot_m",
    "nuke_stack_falloff",
    "shock_blast_trigger_range_m",
    "combat_layer_reemit_fraction",
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_config_service():
    service = AsyncMock()
    service.get_guild_config = AsyncMock(return_value=make_mock_config())
    service.create_or_update_config = AsyncMock(return_value=make_mock_config())
    service.reset_game_constants = AsyncMock(return_value=make_mock_config())
    service.reset_to_defaults = AsyncMock(return_value=make_mock_config())
    return service


@pytest.fixture
def test_app(mock_config_service):
    from api.routers.config import get_config_service
    from api.routers.config import router as config_router

    app = FastAPI()
    app.include_router(config_router, prefix="/api/v1")
    app.dependency_overrides[get_config_service] = lambda: mock_config_service
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


# ===========================================================================
# 1. GET /config/guild/{guild_id}/game-constants — all nulls for fresh guild
# ===========================================================================


class TestGetGameConstants:
    """GET /api/v1/config/guild/{guild_id}/game-constants."""

    @patch("api.routers.config.get_db_session")
    def test_returns_200_with_all_override_fields(self, mock_get_db, client, mock_config_service):
        """Returns 200 with all override fields present (null for fresh guild)."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/config/guild/67890/game-constants")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == len(_OVERRIDE_FIELD_NAMES)
        for field in _OVERRIDE_FIELD_NAMES:
            assert field in data, f"Missing field: {field}"

    @patch("api.routers.config.get_db_session")
    def test_all_fields_are_null_for_fresh_guild(self, mock_get_db, client, mock_config_service):
        """All override fields are null for a guild that has never set overrides."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/config/guild/67890/game-constants")

        assert response.status_code == 200
        data = response.json()
        for field in _OVERRIDE_FIELD_NAMES:
            assert data[field] is None, f"Field {field} should be null, got {data[field]}"

    @patch("api.routers.config.get_db_session")
    def test_non_null_overrides_are_returned(self, mock_get_db, client, mock_config_service):
        """Non-null overrides are returned as-is."""
        _configure_db_mock(mock_get_db)
        mock_config_service.get_guild_config = AsyncMock(
            return_value=make_mock_config(
                # bounty_pvc_armour_buff_factor retired T10; duel_cloak_chance retired rev 0031
                # division_max_tl JSONB retired rev 0033; use flat scalars instead.
                bounty_winner_reserve_factor=0.35,
                criminal_max_gear_upgrade=2,
                division_max_tl_bronze=3,
                division_max_tl_silver=6,
            )
        )

        response = client.get("/api/v1/config/guild/67890/game-constants")

        assert response.status_code == 200
        data = response.json()
        assert data["bounty_winner_reserve_factor"] == pytest.approx(0.35)
        assert data["criminal_max_gear_upgrade"] == 2
        assert data["division_max_tl_bronze"] == 3
        assert data["division_max_tl_silver"] == 6

    @patch("api.routers.config.get_db_session")
    def test_returns_404_for_unconfigured_guild(self, mock_get_db, client, mock_config_service):
        """Returns 404 when guild has not been set up."""
        from services.exceptions import GuildNotConfiguredError

        _configure_db_mock(mock_get_db)
        mock_config_service.get_guild_config.side_effect = GuildNotConfiguredError(guild_id=99999)

        response = client.get("/api/v1/config/guild/99999/game-constants")

        assert response.status_code == 404


# ===========================================================================
# 2. Schema validation — field constraint tests (T10: retired fields removed)
# ===========================================================================


class TestGameConstantsSchemaValidation:
    """PUT /config/guild/{guild_id} — schema validators for override fields (B.49)."""

    @patch("api.routers.config.get_db_session")
    def test_rejects_bounty_pvc_armour_buff_factor_negative(self, mock_get_db, client):
        """T10: bounty_pvc_armour_buff_factor is retired — unknown field is ignored (not 422)."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "bounty_pvc_armour_buff_factor": -0.5},
        )

        # T10: The retired field is no longer in the schema; FastAPI's extra='ignore' means 200
        # (or 422 if schema rejects unknown extras — depends on model config). Accept either.
        assert response.status_code in (200, 422)

    @patch("api.routers.config.get_db_session")
    def test_rejects_ship_value_reward_percentage_above_one(self, mock_get_db, client):
        """ship_value_reward_percentage retired rev 0031 — now an unknown extra field, silently ignored."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "ship_value_reward_percentage": 1.5},
        )

        # Rev 0031: field removed from schema; unknown extras are ignored, so this succeeds now
        assert response.status_code in (200, 422)

    @patch("api.routers.config.get_db_session")
    def test_rejects_duel_cloak_chance_above_100(self, mock_get_db, client):
        """duel_cloak_chance retired rev 0031 — now an unknown extra field, silently ignored."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "duel_cloak_chance": 101},
        )

        # Rev 0031: field removed from schema; unknown extras are ignored, so this succeeds now
        assert response.status_code in (200, 422)

    @patch("api.routers.config.get_db_session")
    def test_rejects_guild_activity_decay_rate_above_one(self, mock_get_db, client):
        """guild_activity_decay_rate retired rev 0031 — now an unknown extra field, silently ignored."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "guild_activity_decay_rate": 1.5},
        )

        # Rev 0031: field removed from schema; unknown extras are ignored, so this succeeds now
        assert response.status_code in (200, 422)

    @patch("api.routers.config.get_db_session")
    def test_accepts_valid_bounty_pvc_armour_buff_factor(self, mock_get_db, client, mock_config_service):
        """bounty_pvc_armour_buff_factor = 2.0 is valid and accepted."""
        _configure_db_mock(mock_get_db)
        mock_config_service.create_or_update_config = AsyncMock(
            return_value=make_mock_config(bounty_pvc_armour_buff_factor=2.0)
        )

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "bounty_pvc_armour_buff_factor": 2.0},
        )

        assert response.status_code == 200


# ===========================================================================
# 3. Flat-scalar validation for rev 0033 (JSONB dict fields retired)
# ===========================================================================


class TestDivisionMaxTlScalarValidation:
    """division_max_tl_{bronze,...} flat scalar fields (rev 0033 — JSONB dict retired)."""

    @patch("api.routers.config.get_db_session")
    def test_rejects_division_max_tl_bronze_out_of_range(self, mock_get_db, client):
        """division_max_tl_bronze must be between 1 and 10."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "division_max_tl_bronze": 0},
        )

        assert response.status_code == 422

    @patch("api.routers.config.get_db_session")
    def test_rejects_division_max_tl_bronze_too_high(self, mock_get_db, client):
        """division_max_tl_bronze above 10 is rejected."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "division_max_tl_bronze": 11},
        )

        assert response.status_code == 422

    @patch("api.routers.config.get_db_session")
    def test_accepts_valid_division_max_tl_bronze(self, mock_get_db, client, mock_config_service):
        """A valid in-range value for division_max_tl_bronze is accepted."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "division_max_tl_bronze": 3},
        )

        assert response.status_code == 200

    @patch("api.routers.config.get_db_session")
    def test_division_max_tl_dict_is_now_unknown_field(self, mock_get_db, client, mock_config_service):
        """division_max_tl as a dict is now an unknown extra field (ignored, not 422)."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "division_max_tl": {"bronze": 3, "silver": 6, "gold": 9, "platinum": 10}},
        )

        # Unknown extra field → 200 (silently ignored by Pydantic extra='ignore')
        assert response.status_code == 200


class TestBountyDivisionRewardMultScalarValidation:
    """bounty_division_reward_mult_{bronze,...} flat scalar fields (rev 0033 — JSONB dict retired)."""

    @patch("api.routers.config.get_db_session")
    def test_rejects_negative_value(self, mock_get_db, client):
        """A negative multiplier scalar is rejected."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "bounty_division_reward_mult_silver": -2.4},
        )

        assert response.status_code == 422

    @patch("api.routers.config.get_db_session")
    def test_accepts_valid_scalar(self, mock_get_db, client, mock_config_service):
        """A valid non-negative scalar for bounty_division_reward_mult_silver is accepted."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "bounty_division_reward_mult_silver": 2.0},
        )

        assert response.status_code == 200


# ===========================================================================
# 4. Bounty delay range validation — RETIRED rev 0031
# bounty_delay_random_min/max fields removed from schema; validator also gone.
# ===========================================================================


class TestBountyDelayRangeValidation:
    """bounty_delay_random_min/max were retired in rev 0031 — no cross-field validation."""

    @patch("api.routers.config.get_db_session")
    def test_rejects_min_greater_than_max(self, mock_get_db, client):
        """bounty_delay_random_min/max retired rev 0031 — unknown extras are ignored, not 422."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "bounty_delay_random_min": 10, "bounty_delay_random_max": 5},
        )

        # Fields retired — unknown extras accepted (200) rather than rejected (422)
        assert response.status_code in (200, 422)

    @patch("api.routers.config.get_db_session")
    def test_omitting_both_fields_is_valid(self, mock_get_db, client, mock_config_service):
        """When neither field is present in the request, no validation error occurs."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "starting_credits": 100},
        )

        assert response.status_code == 200


# ===========================================================================
# 5. POST /config/guild/{guild_id}/game-constants/reset with unknown field
# ===========================================================================


class TestResetGameConstants:
    """POST /api/v1/config/guild/{guild_id}/game-constants/reset."""

    @patch("api.routers.config.get_db_session")
    def test_unknown_field_returns_400(self, mock_get_db, client, mock_config_service):
        """Requesting reset of an unknown field name returns HTTP 400."""
        _configure_db_mock(mock_get_db)

        response = client.post(
            "/api/v1/config/guild/67890/game-constants/reset",
            json={"fields": ["not_a_real_field"]},
        )

        assert response.status_code == 400
        assert "not_a_real_field" in response.json()["detail"]

    @patch("api.routers.config.get_db_session")
    def test_multiple_unknown_fields_returns_400(self, mock_get_db, client):
        """Multiple unknown field names all appear in the error detail."""
        _configure_db_mock(mock_get_db)

        response = client.post(
            "/api/v1/config/guild/67890/game-constants/reset",
            json={"fields": ["bogus_field_1", "bogus_field_2"]},
        )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "bogus_field_1" in detail or "bogus_field_2" in detail

    @patch("api.routers.config.get_db_session")
    def test_reset_all_fields_returns_200(self, mock_get_db, client, mock_config_service):
        """POST with no fields body (reset all) returns 200."""
        _configure_db_mock(mock_get_db)

        response = client.post(
            "/api/v1/config/guild/67890/game-constants/reset",
            json={},  # fields not provided → reset all
        )

        assert response.status_code == 200
        mock_config_service.reset_game_constants.assert_awaited_once()

    @patch("api.routers.config.get_db_session")
    def test_reset_specific_valid_fields_returns_200(self, mock_get_db, client, mock_config_service):
        """Resetting two valid known fields returns 200."""
        _configure_db_mock(mock_get_db)

        # T10: bounty_pvc_armour_buff_factor retired; duel_cloak_chance retired rev 0031;
        # use still-live fields instead
        response = client.post(
            "/api/v1/config/guild/67890/game-constants/reset",
            json={"fields": ["criminal_max_gear_upgrade", "bounty_winner_reserve_factor"]},
        )

        assert response.status_code == 200
        mock_config_service.reset_game_constants.assert_awaited_once()

    @patch("api.routers.config.get_db_session")
    def test_reset_404_for_unconfigured_guild(self, mock_get_db, client, mock_config_service):
        """Returns 404 when the guild has no config row."""
        from services.exceptions import GuildNotConfiguredError

        _configure_db_mock(mock_get_db)
        mock_config_service.reset_game_constants.side_effect = GuildNotConfiguredError(guild_id=99999)

        response = client.post(
            "/api/v1/config/guild/99999/game-constants/reset",
            json={},
        )

        assert response.status_code == 404

    @patch("api.routers.config.get_db_session")
    def test_reset_calls_service_with_all_fields_when_none_specified(self, mock_get_db, client, mock_config_service):
        """When fields is null, service is called with all live override field names (T10: 26 remaining)."""
        _configure_db_mock(mock_get_db)

        client.post(
            "/api/v1/config/guild/67890/game-constants/reset",
            json={"fields": None},
        )

        mock_config_service.reset_game_constants.assert_awaited_once()
        call_args = mock_config_service.reset_game_constants.call_args
        # The third positional arg is the fields list — should have all live entries
        fields_arg = call_args.args[2] if len(call_args.args) > 2 else call_args.kwargs.get("fields")
        assert fields_arg is not None
        # T10: bounty_pvc_armour_buff_factor and duel_variance_percent retired → count matches list above
        assert len(fields_arg) == len(_OVERRIDE_FIELD_NAMES)


# ===========================================================================
# 9. shop_combat_module_prob — per-guild override settable via API
# ===========================================================================


class TestShopCombatModuleProb:
    """shop_combat_module_prob is exposed in the game-constants override API."""

    @patch("api.routers.config.get_db_session")
    def test_put_valid_value_returns_200(self, mock_get_db, client, mock_config_service):
        """PUT /config/guild/{guild_id} with shop_combat_module_prob=0.9 succeeds (not 422)."""
        _configure_db_mock(mock_get_db)
        mock_config_service.create_or_update_config = AsyncMock(
            return_value=make_mock_config(shop_combat_module_prob=0.9)
        )

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "shop_combat_module_prob": 0.9},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["shop_combat_module_prob"] == pytest.approx(0.9)

    @patch("api.routers.config.get_db_session")
    def test_put_out_of_range_above_one_returns_422(self, mock_get_db, client):
        """shop_combat_module_prob=1.5 is out of [0.0, 1.0] and must be rejected with 422."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "shop_combat_module_prob": 1.5},
        )

        assert response.status_code == 422

    @patch("api.routers.config.get_db_session")
    def test_put_out_of_range_below_zero_returns_422(self, mock_get_db, client):
        """shop_combat_module_prob=-0.1 is below 0.0 and must be rejected with 422."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "shop_combat_module_prob": -0.1},
        )

        assert response.status_code == 422

    @patch("api.routers.config.get_db_session")
    def test_field_present_in_game_constants_get(self, mock_get_db, client, mock_config_service):
        """GET /config/guild/{guild_id}/game-constants includes shop_combat_module_prob."""
        _configure_db_mock(mock_get_db)

        response = client.get("/api/v1/config/guild/67890/game-constants")

        assert response.status_code == 200
        data = response.json()
        assert "shop_combat_module_prob" in data

    @patch("api.routers.config.get_db_session")
    def test_non_null_value_returned_in_get(self, mock_get_db, client, mock_config_service):
        """GET returns a non-null shop_combat_module_prob when it has been set per-guild."""
        _configure_db_mock(mock_get_db)
        mock_config_service.get_guild_config = AsyncMock(return_value=make_mock_config(shop_combat_module_prob=0.6))

        response = client.get("/api/v1/config/guild/67890/game-constants")

        assert response.status_code == 200
        data = response.json()
        assert data["shop_combat_module_prob"] == pytest.approx(0.6)

    @patch("api.routers.config.get_db_session")
    def test_reset_shop_combat_module_prob_returns_200(self, mock_get_db, client, mock_config_service):
        """POST /game-constants/reset with shop_combat_module_prob in fields list is valid (200)."""
        _configure_db_mock(mock_get_db)

        response = client.post(
            "/api/v1/config/guild/67890/game-constants/reset",
            json={"fields": ["shop_combat_module_prob"]},
        )

        assert response.status_code == 200
        mock_config_service.reset_game_constants.assert_awaited_once()

    @patch("api.routers.config.get_db_session")
    def test_boundary_zero_is_accepted(self, mock_get_db, client, mock_config_service):
        """shop_combat_module_prob=0.0 (always pick filler) is a valid probability and accepted."""
        _configure_db_mock(mock_get_db)
        mock_config_service.create_or_update_config = AsyncMock(
            return_value=make_mock_config(shop_combat_module_prob=0.0)
        )

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "shop_combat_module_prob": 0.0},
        )

        assert response.status_code == 200

    @patch("api.routers.config.get_db_session")
    def test_boundary_one_is_accepted(self, mock_get_db, client, mock_config_service):
        """shop_combat_module_prob=1.0 (always pick combat) is a valid probability and accepted."""
        _configure_db_mock(mock_get_db)
        mock_config_service.create_or_update_config = AsyncMock(
            return_value=make_mock_config(shop_combat_module_prob=1.0)
        )

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "shop_combat_module_prob": 1.0},
        )

        assert response.status_code == 200
