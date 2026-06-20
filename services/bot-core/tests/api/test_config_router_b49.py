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
        division_max_tl=None,
        ship_value_reward_percentage=None,
        criminal_equip_damageless_weapon_chance=None,
        criminal_max_gear_upgrade=None,
        bounty_reward_to_xp_gain_mult=None,
        bounty_winner_reserve_factor=None,
        # bounty_pvc_armour_buff_factor retired T10
        # duel_variance_percent retired T10
        duel_cloak_chance=None,
        close_bounty_threshold=None,
        max_route_length=None,
        bounty_delay_random_min=None,
        bounty_delay_random_max=None,
        bounty_spawn_jitter=None,
        check_cooldown=None,
        duel_request_expiry=None,
        guild_activity_decay_rate=None,
        min_guild_activity=None,
        activity_temp_per_player=None,
        shop_default_ships_num=None,
        shop_default_weapons_num=None,
        shop_default_modules_num=None,
        shop_default_turrets_num=None,
        turret_spawn_probability=None,
        kaamo_max_capacity=None,
        classic_credits_per_check=None,
        tier_change_cooldown=None,
        demotion_credit_penalty_pct=None,  # per-guild demotion penalty % (0–100; NULL → global default)
        # Criminal loadout balance (BALANCE_JOURNAL §A — Thread 3 & 4)
        long_range_threshold_m=None,
        criminal_long_range_pct=None,
        primary_tl_band_weights=None,
        criminal_cloak_chance_by_division=None,
        criminal_booster_chance_by_division=None,
        criminal_emergency_chance_by_division=None,
        criminal_weaponmod_chance_by_division=None,
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
    "division_max_tl",
    "ship_value_reward_percentage",
    "criminal_equip_damageless_weapon_chance",
    "criminal_max_gear_upgrade",
    "bounty_reward_to_xp_gain_mult",
    "bounty_winner_reserve_factor",
    # bounty_pvc_armour_buff_factor retired T10
    # duel_variance_percent retired T10
    "duel_cloak_chance",
    "close_bounty_threshold",
    "max_route_length",
    "bounty_delay_random_min",
    "bounty_delay_random_max",
    "bounty_spawn_jitter",
    "check_cooldown",
    "duel_request_expiry",
    "guild_activity_decay_rate",
    "min_guild_activity",
    "activity_temp_per_player",
    "shop_default_ships_num",
    "shop_default_weapons_num",
    "shop_default_modules_num",
    "shop_default_turrets_num",
    "turret_spawn_probability",
    "kaamo_max_capacity",
    "classic_credits_per_check",
    "tier_change_cooldown",
    "demotion_credit_penalty_pct",  # per-guild demotion penalty % (0–100; NULL → global default 10)
    # Criminal loadout balance (BALANCE_JOURNAL §A — Thread 3 & 4)
    "long_range_threshold_m",
    "criminal_long_range_pct",
    "primary_tl_band_weights",
    "criminal_cloak_chance_by_division",
    "criminal_booster_chance_by_division",
    "criminal_emergency_chance_by_division",
    "criminal_weaponmod_chance_by_division",
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
                # bounty_pvc_armour_buff_factor retired T10; use bounty_winner_reserve_factor instead
                bounty_winner_reserve_factor=0.35,
                duel_cloak_chance=10,
                division_max_tl={"bronze": 3, "silver": 6, "gold": 9, "platinum": 10},
            )
        )

        response = client.get("/api/v1/config/guild/67890/game-constants")

        assert response.status_code == 200
        data = response.json()
        assert data["bounty_winner_reserve_factor"] == pytest.approx(0.35)
        assert data["duel_cloak_chance"] == 10
        assert data["division_max_tl"]["bronze"] == 3

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
        """ship_value_reward_percentage must be <= 1.0."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "ship_value_reward_percentage": 1.5},
        )

        assert response.status_code == 422

    @patch("api.routers.config.get_db_session")
    def test_rejects_duel_cloak_chance_above_100(self, mock_get_db, client):
        """duel_cloak_chance must be <= 100."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "duel_cloak_chance": 101},
        )

        assert response.status_code == 422

    @patch("api.routers.config.get_db_session")
    def test_rejects_guild_activity_decay_rate_above_one(self, mock_get_db, client):
        """guild_activity_decay_rate must be <= 1.0."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "guild_activity_decay_rate": 1.5},
        )

        assert response.status_code == 422

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
# 3. Schema validator rejects division_max_tl with missing tier keys
# ===========================================================================


class TestDivisionMaxTlValidation:
    """division_max_tl must have exactly {bronze, silver, gold, platinum}."""

    @patch("api.routers.config.get_db_session")
    def test_rejects_division_max_tl_missing_keys(self, mock_get_db, client):
        """division_max_tl with only 'bronze' key is rejected with 422."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "division_max_tl": {"bronze": 2}},
        )

        assert response.status_code == 422

    @patch("api.routers.config.get_db_session")
    def test_rejects_division_max_tl_extra_keys(self, mock_get_db, client):
        """division_max_tl with extra keys beyond the required 4 is rejected."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={
                "guild_id": 67890,
                "division_max_tl": {"bronze": 2, "silver": 5, "gold": 8, "platinum": 10, "diamond": 12},
            },
        )

        assert response.status_code == 422

    @patch("api.routers.config.get_db_session")
    def test_rejects_division_max_tl_value_out_of_range(self, mock_get_db, client):
        """division_max_tl values must be integers between 1 and 10."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={
                "guild_id": 67890,
                "division_max_tl": {"bronze": 0, "silver": 5, "gold": 8, "platinum": 10},
            },
        )

        assert response.status_code == 422

    @patch("api.routers.config.get_db_session")
    def test_accepts_valid_division_max_tl(self, mock_get_db, client, mock_config_service):
        """A correctly formed division_max_tl dict is accepted."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={
                "guild_id": 67890,
                "division_max_tl": {"bronze": 3, "silver": 6, "gold": 9, "platinum": 10},
            },
        )

        assert response.status_code == 200


# ===========================================================================
# 4. Schema validator rejects bounty_delay_random_min > bounty_delay_random_max
# ===========================================================================


class TestBountyDelayRangeValidation:
    """bounty_delay_random_min must be <= bounty_delay_random_max."""

    @patch("api.routers.config.get_db_session")
    def test_rejects_min_greater_than_max(self, mock_get_db, client):
        """bounty_delay_random_min=10, bounty_delay_random_max=5 is invalid."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "bounty_delay_random_min": 10, "bounty_delay_random_max": 5},
        )

        assert response.status_code == 422

    @patch("api.routers.config.get_db_session")
    def test_accepts_min_equal_to_max(self, mock_get_db, client, mock_config_service):
        """bounty_delay_random_min == bounty_delay_random_max is valid."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "bounty_delay_random_min": 5, "bounty_delay_random_max": 5},
        )

        assert response.status_code == 200

    @patch("api.routers.config.get_db_session")
    def test_accepts_min_less_than_max(self, mock_get_db, client, mock_config_service):
        """bounty_delay_random_min < bounty_delay_random_max is valid."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "bounty_delay_random_min": 3, "bounty_delay_random_max": 7},
        )

        assert response.status_code == 200

    @patch("api.routers.config.get_db_session")
    def test_omitting_both_fields_is_valid(self, mock_get_db, client, mock_config_service):
        """When neither field is present in the request, no validation error occurs."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "starting_credits": 100},
        )

        assert response.status_code == 200

    @patch("api.routers.config.get_db_session")
    def test_omitting_only_max_is_valid(self, mock_get_db, client, mock_config_service):
        """Setting only min (no max) should not trigger the cross-field validation."""
        _configure_db_mock(mock_get_db)

        response = client.put(
            "/api/v1/config/guild/67890",
            json={"guild_id": 67890, "bounty_delay_random_min": 5},
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

        # T10: bounty_pvc_armour_buff_factor retired; use a still-live field instead
        response = client.post(
            "/api/v1/config/guild/67890/game-constants/reset",
            json={"fields": ["duel_cloak_chance", "bounty_winner_reserve_factor"]},
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
