"""Tests for Pydantic schemas in the bot-core API."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from api.schemas.about_schema import (
    CriminalResponse,
    ItemResponse,
    ModuleResponse,
    PrimaryWeaponResponse,
    SecondaryWeaponResponse,
    SystemResponse,
    TurretWeaponResponse,
    WeaponResponse,
)
from api.schemas.about_schema import (
    ShipResponse as AboutShipResponse,
)
from api.schemas.admin_schema import (
    AddInventoryItemRequest,
    GuildInitializationResponse,
    InitializeGuildRequest,
    RefreshShopRequest,
    RemoveInventoryItemRequest,
    SystemHealthResponse,
    UpdatePlayerCreditsRequest,
    UpdatePlayerXPRequest,
    UpdateShopConfigRequest,
)
from api.schemas.config_schema import (
    ConfigValidationResponse,
    GuildConfigResponse,
    UpdateConfigRequest,
    UpdateXPThresholdsRequest,
)
from api.schemas.config_schema import (
    UpdateShopConfigRequest as ConfigUpdateShopConfigRequest,
)
from api.schemas.discord_message_schema import (
    DiscordMessageRequest,
    DiscordMessageResponse,
    EmbedPayloadDict,
)
from api.schemas.health_schema import HealthResponse, SimpleHealthResponse
from api.schemas.inventory_schema import (
    AddItemRequest,
    InventoryItemResponse,
    InventorySummaryResponse,
    ItemTransactionResponse,
    RemoveItemRequest,
    TransferItemRequest,
)
from api.schemas.players_schema import (
    CreatePlayerRequest,
    PlayerResponse,
    PlayerStatisticsResponse,
    UpdateCreditsRequest,
    UpdateTierRequest,
    UpdateXPRequest,
)
from api.schemas.scheduler_schema import JobInfo, OneTimeJob, RecurringJob, UpdateJob
from api.schemas.ships_schema import (
    CreateShipRequest,
    EquipItemRequest,
    ShipLoadoutSummaryResponse,
    ShipResponse,
    UnequipItemRequest,
    UpdateLoadoutRequest,
    UpdateNicknameRequest,
)
from api.schemas.shops_schema import (
    PurchaseRequest,
    SellRequest,
    ShopItemResponse,
    ShopSummaryResponse,
    TransactionResponse,
)
from api.schemas.shops_schema import (
    RefreshShopRequest as ShopsRefreshShopRequest,
)
from api.schemas.users_schema import CreateUserRequest, UpdateUserRequest, UserResponse
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ITEM_BASE = dict(
    id=1,
    name="Widget",
    aliases=["w", "wid"],
    built_in=True,
    emoji=None,
    icon=None,
    value=None,
    wiki=None,
    type="generic",
)


# ===========================================================================
# Health schemas (pre-existing tests kept intact)
# ===========================================================================


class TestHealthResponseSchema:
    """Tests for the HealthResponse schema."""

    def test_health_response_schema_valid(self):
        """Construct a HealthResponse and verify all fields are set correctly."""
        now = datetime.now(UTC)
        response = HealthResponse(
            status="healthy",
            timestamp=now,
            version="1.0.0",
            service="BountyBot API",
            environment={
                "python_version": "3.11.0",
                "platform": "Linux-6.1",
                "architecture": "64bit",
            },
            checks={
                "python_version": True,
                "memory_available": True,
                "disk_space": True,
                "database_connectivity": True,
                "schema_version_current": True,
            },
            database_check={
                "connectivity": True,
                "status": "healthy",
                "host": "localhost",
                "port": 5432,
                "database": "bountybot",
            },
            schema_check={
                "version_match": True,
                "current_version": "1.0.0",
                "expected_version": "1.0.0",
                "status": "current",
            },
        )

        assert response.status == "healthy"
        assert response.timestamp == now
        assert response.version == "1.0.0"
        assert response.service == "BountyBot API"
        assert response.environment["python_version"] == "3.11.0"
        assert response.checks["database_connectivity"] is True
        assert len(response.checks) == 5
        assert response.database_check["connectivity"] is True
        assert response.schema_check["version_match"] is True

    def test_health_response_optional_fields(self):
        """Verify database_check and schema_check default to None when omitted."""
        now = datetime.now(UTC)
        response = HealthResponse(
            status="unhealthy",
            timestamp=now,
            version="1.0.0",
            service="BountyBot API",
            environment={"python_version": "3.11.0"},
            checks={"python_version": True},
        )

        assert response.database_check is None
        assert response.schema_check is None
        assert response.status == "unhealthy"
        assert response.environment == {"python_version": "3.11.0"}
        assert response.checks == {"python_version": True}


class TestSimpleHealthResponseSchema:
    """Tests for the SimpleHealthResponse schema."""

    def test_simple_health_response_schema_valid(self):
        """Construct a SimpleHealthResponse and verify fields."""
        now = datetime.now(UTC)
        response = SimpleHealthResponse(
            status="healthy",
            timestamp=now,
        )

        assert response.status == "healthy"
        assert response.timestamp == now


# ===========================================================================
# about_schema
# ===========================================================================


class TestItemResponseSchema:
    """Tests for ItemResponse (about_schema)."""

    def test_valid_construction(self):
        item = ItemResponse(**_ITEM_BASE)
        assert item.id == 1
        assert item.name == "Widget"
        assert item.aliases == ["w", "wid"]
        assert item.built_in is True
        assert item.emoji is None
        assert item.value is None
        assert item.type == "generic"

    def test_optional_tech_level_defaults_none(self):
        item = ItemResponse(**_ITEM_BASE)
        assert item.tech_level is None

    def test_optional_extra_atts_defaults_none(self):
        item = ItemResponse(**_ITEM_BASE)
        assert item.extra_atts is None

    def test_with_all_optional_fields(self):
        item = ItemResponse(
            **_ITEM_BASE,
            tech_level=5,
            extra_atts={"armor": 100},
        )
        assert item.tech_level == 5
        assert item.extra_atts == {"armor": 100}

    def test_wrong_type_for_id_raises(self):
        with pytest.raises(ValidationError):
            ItemResponse(**{**_ITEM_BASE, "id": "not-an-int"})

    def test_wrong_type_for_aliases_raises(self):
        with pytest.raises(ValidationError):
            ItemResponse(**{**_ITEM_BASE, "aliases": "not-a-list"})


class TestModuleResponseSchema:
    """Tests for ModuleResponse (about_schema)."""

    def test_valid_construction(self):
        mod = ModuleResponse(**_ITEM_BASE)
        assert mod.name == "Widget"
        assert mod.max_equipped is None

    def test_max_equipped_set(self):
        mod = ModuleResponse(**_ITEM_BASE, max_equipped=3)
        assert mod.max_equipped == 3

    def test_inherits_item_response_fields(self):
        mod = ModuleResponse(**_ITEM_BASE, tech_level=2)
        assert mod.tech_level == 2


class TestWeaponResponseSchema:
    """Tests for WeaponResponse (about_schema)."""

    def test_valid_construction(self):
        weapon = WeaponResponse(**_ITEM_BASE)
        assert weapon.name == "Widget"
        assert weapon.type == "generic"

    def test_inherits_item_response_optional_fields(self):
        weapon = WeaponResponse(**_ITEM_BASE)
        assert weapon.tech_level is None


class TestPrimaryWeaponResponseSchema:
    """Tests for PrimaryWeaponResponse (about_schema)."""

    def test_valid_construction_dps_none(self):
        pw = PrimaryWeaponResponse(**_ITEM_BASE)
        assert pw.dps is None

    def test_dps_set(self):
        pw = PrimaryWeaponResponse(**_ITEM_BASE, dps=42.5)
        assert pw.dps == 42.5

    def test_wrong_type_for_dps_raises(self):
        with pytest.raises(ValidationError):
            PrimaryWeaponResponse(**_ITEM_BASE, dps="high")


class TestSecondaryWeaponResponseSchema:
    """Tests for SecondaryWeaponResponse (about_schema)."""

    def test_valid_construction(self):
        sw = SecondaryWeaponResponse(**_ITEM_BASE)
        assert sw.name == "Widget"

    def test_inherits_weapon_fields(self):
        sw = SecondaryWeaponResponse(**_ITEM_BASE, tech_level=3)
        assert sw.tech_level == 3


class TestTurretWeaponResponseSchema:
    """Tests for TurretWeaponResponse (about_schema)."""

    def test_valid_construction(self):
        tw = TurretWeaponResponse(**_ITEM_BASE)
        assert tw.name == "Widget"

    def test_inherits_weapon_fields(self):
        tw = TurretWeaponResponse(**_ITEM_BASE, tech_level=7)
        assert tw.tech_level == 7


class TestAboutShipResponseSchema:
    """Tests for about_schema.ShipResponse."""

    def test_valid_minimal(self):
        ship = AboutShipResponse(**_ITEM_BASE)
        assert ship.armour is None
        assert ship.cargo is None
        assert ship.handling is None
        assert ship.manufacturer is None
        assert ship.skinnable is None

    def test_all_optional_ship_fields(self):
        ship = AboutShipResponse(
            **_ITEM_BASE,
            armour=500,
            cargo=200,
            handling=80,
            shop_spawn_rate=0.5,
            max_modules=4,
            max_primaries=2,
            max_secondaries=3,
            max_turrets=1,
            manufacturer="AcmeCorp",
            skinnable=True,
            compatible_skins={"red": "red_skin"},
            model="X-1",
            norm_spec="normal",
            assets=["asset1.png"],
            save_due=False,
        )
        assert ship.armour == 500
        assert ship.manufacturer == "AcmeCorp"
        assert ship.compatible_skins == {"red": "red_skin"}
        assert ship.assets == ["asset1.png"]


class TestCriminalResponseSchema:
    """Tests for CriminalResponse (about_schema)."""

    def test_valid_construction(self):
        crim = CriminalResponse(**_ITEM_BASE, is_player=False, faction="Outlaws")
        assert crim.is_player is False
        assert crim.faction == "Outlaws"

    def test_missing_is_player_raises(self):
        with pytest.raises(ValidationError):
            CriminalResponse(**_ITEM_BASE, faction="Outlaws")

    def test_missing_faction_raises(self):
        with pytest.raises(ValidationError):
            CriminalResponse(**_ITEM_BASE, is_player=True)

    def test_wrong_type_for_is_player_raises(self):
        # Pydantic v2 coerces simple strings to bool; use a non-coercible type instead
        with pytest.raises(ValidationError):
            CriminalResponse(**_ITEM_BASE, is_player={"nested": "object"}, faction="Outlaws")


class TestSystemResponseSchema:
    """Tests for SystemResponse (about_schema)."""

    def test_valid_construction(self):
        sys_resp = SystemResponse(**_ITEM_BASE, coordinates=[1.0, 2.0, 3.0], faction="Federation")
        assert sys_resp.coordinates == [1.0, 2.0, 3.0]
        assert sys_resp.faction == "Federation"

    def test_missing_coordinates_raises(self):
        with pytest.raises(ValidationError):
            SystemResponse(**_ITEM_BASE, faction="Federation")

    def test_missing_faction_raises(self):
        with pytest.raises(ValidationError):
            SystemResponse(**_ITEM_BASE, coordinates=[0.0, 0.0, 0.0])

    def test_wrong_type_for_coordinates_raises(self):
        with pytest.raises(ValidationError):
            SystemResponse(**_ITEM_BASE, coordinates="1,2,3", faction="Federation")


# ===========================================================================
# admin_schema
# ===========================================================================


class TestInitializeGuildRequestSchema:
    """Tests for InitializeGuildRequest."""

    def test_valid_minimal(self):
        req = InitializeGuildRequest(guild_id=123)
        assert req.guild_id == 123
        assert req.admin_role_id is None
        assert req.starting_credits == 0

    def test_all_fields(self):
        req = InitializeGuildRequest(guild_id=123, admin_role_id=456, starting_credits=1000)
        assert req.admin_role_id == 456
        assert req.starting_credits == 1000

    def test_starting_credits_negative_raises(self):
        with pytest.raises(ValidationError):
            InitializeGuildRequest(guild_id=123, starting_credits=-1)

    def test_starting_credits_zero_valid(self):
        req = InitializeGuildRequest(guild_id=123, starting_credits=0)
        assert req.starting_credits == 0


class TestGuildInitializationResponseSchema:
    """Tests for GuildInitializationResponse."""

    def test_valid_construction(self):
        resp = GuildInitializationResponse(
            guild_id=1,
            admin_role_id=None,
            shops_created=4,
            config_created=True,
            message="Guild initialized",
        )
        assert resp.guild_id == 1
        assert resp.admin_role_id is None
        assert resp.shops_created == 4
        assert resp.config_created is True

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            GuildInitializationResponse(guild_id=1, shops_created=4, config_created=True)


class TestUpdatePlayerCreditsRequestSchema:
    """Tests for UpdatePlayerCreditsRequest."""

    def test_valid_construction(self):
        req = UpdatePlayerCreditsRequest(player_id=1, credits=500)
        assert req.player_id == 1
        assert req.credits == 500
        assert req.update_lifetime is True

    def test_credits_zero_valid(self):
        req = UpdatePlayerCreditsRequest(player_id=1, credits=0)
        assert req.credits == 0

    def test_credits_negative_raises(self):
        with pytest.raises(ValidationError):
            UpdatePlayerCreditsRequest(player_id=1, credits=-1)

    def test_update_lifetime_false(self):
        req = UpdatePlayerCreditsRequest(player_id=1, credits=100, update_lifetime=False)
        assert req.update_lifetime is False


class TestUpdatePlayerXPRequestSchema:
    """Tests for UpdatePlayerXPRequest."""

    def test_valid_xp(self):
        req = UpdatePlayerXPRequest(player_id=1, xp=500)
        assert req.xp == 500

    def test_xp_zero_valid(self):
        req = UpdatePlayerXPRequest(player_id=1, xp=0)
        assert req.xp == 0

    def test_xp_max_valid(self):
        req = UpdatePlayerXPRequest(player_id=1, xp=1_000_000)
        assert req.xp == 1_000_000

    def test_xp_negative_raises(self):
        with pytest.raises(ValidationError):
            UpdatePlayerXPRequest(player_id=1, xp=-1)

    def test_xp_exceeds_max_raises(self):
        with pytest.raises(ValidationError):
            UpdatePlayerXPRequest(player_id=1, xp=1_000_001)


class TestAddInventoryItemRequestSchema:
    """Tests for AddInventoryItemRequest."""

    def test_valid_ship_type(self):
        req = AddInventoryItemRequest(player_id=1, item_type="ship", item_name="Falcon")
        assert req.item_type == "ship"
        assert req.quantity == 1

    def test_valid_weapon_type(self):
        # A.45: concrete type (primary_weapon), not alias "weapon"
        req = AddInventoryItemRequest(player_id=1, item_type="primary_weapon", item_name="Laser")
        assert req.item_type == "primary_weapon"

    def test_valid_module_type(self):
        req = AddInventoryItemRequest(player_id=1, item_type="module", item_name="Shield")
        assert req.item_type == "module"

    def test_valid_turret_type(self):
        # A.45: concrete type (turret_weapon), not alias "turret"
        req = AddInventoryItemRequest(player_id=1, item_type="turret_weapon", item_name="AutoGun")
        assert req.item_type == "turret_weapon"

    def test_invalid_item_type_raises(self):
        with pytest.raises(ValidationError):
            AddInventoryItemRequest(player_id=1, item_type="robot", item_name="X")

    def test_quantity_zero_raises(self):
        with pytest.raises(ValidationError):
            AddInventoryItemRequest(player_id=1, item_type="ship", item_name="Falcon", quantity=0)

    def test_quantity_negative_raises(self):
        with pytest.raises(ValidationError):
            AddInventoryItemRequest(player_id=1, item_type="ship", item_name="Falcon", quantity=-5)

    def test_quantity_default_one(self):
        # A.45: concrete type
        req = AddInventoryItemRequest(player_id=1, item_type="primary_weapon", item_name="Laser")
        assert req.quantity == 1


class TestRemoveInventoryItemRequestSchema:
    """Tests for RemoveInventoryItemRequest."""

    def test_valid_construction(self):
        req = RemoveInventoryItemRequest(player_id=1, item_type="module", item_name="Shield", quantity=2)
        assert req.quantity == 2

    def test_invalid_item_type_raises(self):
        with pytest.raises(ValidationError):
            RemoveInventoryItemRequest(player_id=1, item_type="misc", item_name="X")

    def test_quantity_zero_raises(self):
        # A.45: use concrete type; the 422 is from quantity=0 validation
        with pytest.raises(ValidationError):
            RemoveInventoryItemRequest(player_id=1, item_type="turret_weapon", item_name="Gun", quantity=0)


class TestAdminRefreshShopRequestSchema:
    """Tests for admin_schema.RefreshShopRequest."""

    def test_valid_bronze(self):
        req = RefreshShopRequest(guild_id=1, tier="Bronze")
        assert req.tier == "Bronze"
        assert req.force_tech_level is None

    def test_valid_platinum(self):
        req = RefreshShopRequest(guild_id=1, tier="Platinum")
        assert req.tier == "Platinum"

    def test_invalid_tier_raises(self):
        with pytest.raises(ValidationError):
            RefreshShopRequest(guild_id=1, tier="Diamond")

    def test_force_tech_level_min_valid(self):
        req = RefreshShopRequest(guild_id=1, tier="Gold", force_tech_level=1)
        assert req.force_tech_level == 1

    def test_force_tech_level_max_valid(self):
        req = RefreshShopRequest(guild_id=1, tier="Gold", force_tech_level=10)
        assert req.force_tech_level == 10

    def test_force_tech_level_zero_raises(self):
        with pytest.raises(ValidationError):
            RefreshShopRequest(guild_id=1, tier="Gold", force_tech_level=0)

    def test_force_tech_level_exceeds_max_raises(self):
        with pytest.raises(ValidationError):
            RefreshShopRequest(guild_id=1, tier="Gold", force_tech_level=11)


class TestAdminUpdateShopConfigRequestSchema:
    """Tests for admin_schema.UpdateShopConfigRequest."""

    def test_valid_minimal(self):
        req = UpdateShopConfigRequest(guild_id=1)
        assert req.guild_id == 1
        assert req.tech_level_probabilities is None
        assert req.sale_price_factor is None

    def test_sale_price_factor_valid_range(self):
        req = UpdateShopConfigRequest(guild_id=1, sale_price_factor=0.5)
        assert req.sale_price_factor == 0.5

    def test_sale_price_factor_exactly_one_valid(self):
        req = UpdateShopConfigRequest(guild_id=1, sale_price_factor=1.0)
        assert req.sale_price_factor == 1.0

    def test_sale_price_factor_zero_raises(self):
        with pytest.raises(ValidationError):
            UpdateShopConfigRequest(guild_id=1, sale_price_factor=0.0)

    def test_sale_price_factor_exceeds_one_raises(self):
        with pytest.raises(ValidationError):
            UpdateShopConfigRequest(guild_id=1, sale_price_factor=1.1)

    def test_all_optional_set(self):
        req = UpdateShopConfigRequest(
            guild_id=1,
            tech_level_probabilities={"1": 0.5},
            item_count_ranges={"Bronze": {"min": 1, "max": 5}},
            quantity_ranges={"ship": {"min": 1, "max": 1}},
        )
        assert req.tech_level_probabilities == {"1": 0.5}


class TestAdminSchemaInputValidation:
    """Tests for input validation constraints added to admin schemas (ge=1, max_length)."""

    # ------------------------------------------------------------------
    # guild_id ge=1 constraints
    # ------------------------------------------------------------------

    def test_initialize_guild_zero_guild_id_raises(self):
        """guild_id=0 is rejected (ge=1)."""
        with pytest.raises(ValidationError):
            InitializeGuildRequest(guild_id=0)

    def test_initialize_guild_negative_guild_id_raises(self):
        """Negative guild_id is rejected (ge=1)."""
        with pytest.raises(ValidationError):
            InitializeGuildRequest(guild_id=-1)

    def test_refresh_shop_zero_guild_id_raises(self):
        """guild_id=0 is rejected on RefreshShopRequest (ge=1)."""
        with pytest.raises(ValidationError):
            RefreshShopRequest(guild_id=0, tier="Bronze")

    def test_refresh_shop_negative_guild_id_raises(self):
        """Negative guild_id is rejected on RefreshShopRequest (ge=1)."""
        with pytest.raises(ValidationError):
            RefreshShopRequest(guild_id=-5, tier="Silver")

    def test_update_shop_config_zero_guild_id_raises(self):
        """guild_id=0 is rejected on UpdateShopConfigRequest (ge=1)."""
        with pytest.raises(ValidationError):
            UpdateShopConfigRequest(guild_id=0)

    def test_update_shop_config_negative_guild_id_raises(self):
        """Negative guild_id is rejected on UpdateShopConfigRequest (ge=1)."""
        with pytest.raises(ValidationError):
            UpdateShopConfigRequest(guild_id=-100)

    # ------------------------------------------------------------------
    # player_id ge=1 constraints
    # ------------------------------------------------------------------

    def test_update_player_credits_zero_player_id_raises(self):
        """player_id=0 is rejected on UpdatePlayerCreditsRequest (ge=1)."""
        with pytest.raises(ValidationError):
            UpdatePlayerCreditsRequest(player_id=0, credits=100)

    def test_update_player_credits_negative_player_id_raises(self):
        """Negative player_id is rejected on UpdatePlayerCreditsRequest (ge=1)."""
        with pytest.raises(ValidationError):
            UpdatePlayerCreditsRequest(player_id=-1, credits=100)

    def test_update_player_xp_zero_player_id_raises(self):
        """player_id=0 is rejected on UpdatePlayerXPRequest (ge=1)."""
        with pytest.raises(ValidationError):
            UpdatePlayerXPRequest(player_id=0, xp=100)

    def test_update_player_xp_negative_player_id_raises(self):
        """Negative player_id is rejected on UpdatePlayerXPRequest (ge=1)."""
        with pytest.raises(ValidationError):
            UpdatePlayerXPRequest(player_id=-99, xp=100)

    def test_add_inventory_item_zero_player_id_raises(self):
        """player_id=0 is rejected on AddInventoryItemRequest (ge=1)."""
        with pytest.raises(ValidationError):
            AddInventoryItemRequest(player_id=0, item_type="ship", item_name="Falcon")

    def test_add_inventory_item_negative_player_id_raises(self):
        """Negative player_id is rejected on AddInventoryItemRequest (ge=1)."""
        with pytest.raises(ValidationError):
            AddInventoryItemRequest(player_id=-1, item_type="ship", item_name="Falcon")

    def test_remove_inventory_item_zero_player_id_raises(self):
        """player_id=0 is rejected on RemoveInventoryItemRequest (ge=1)."""
        with pytest.raises(ValidationError):
            RemoveInventoryItemRequest(player_id=0, item_type="weapon", item_name="Laser")

    def test_remove_inventory_item_negative_player_id_raises(self):
        """Negative player_id is rejected on RemoveInventoryItemRequest (ge=1)."""
        with pytest.raises(ValidationError):
            RemoveInventoryItemRequest(player_id=-1, item_type="weapon", item_name="Laser")

    # ------------------------------------------------------------------
    # item_name max_length=256 constraints
    # ------------------------------------------------------------------

    def test_add_inventory_item_name_too_long_raises(self):
        """item_name longer than 256 chars is rejected on AddInventoryItemRequest (max_length=256)."""
        long_name = "A" * 257
        with pytest.raises(ValidationError):
            AddInventoryItemRequest(player_id=1, item_type="ship", item_name=long_name)

    def test_add_inventory_item_name_at_max_length_valid(self):
        """item_name of exactly 256 chars is accepted on AddInventoryItemRequest."""
        max_name = "A" * 256
        # A.45: concrete type
        req = AddInventoryItemRequest(player_id=1, item_type="primary_weapon", item_name=max_name)
        assert len(req.item_name) == 256

    def test_remove_inventory_item_name_too_long_raises(self):
        """item_name longer than 256 chars is rejected on RemoveInventoryItemRequest (max_length=256)."""
        long_name = "B" * 257
        with pytest.raises(ValidationError):
            RemoveInventoryItemRequest(player_id=1, item_type="module", item_name=long_name)

    def test_remove_inventory_item_name_at_max_length_valid(self):
        """item_name of exactly 256 chars is accepted on RemoveInventoryItemRequest."""
        max_name = "B" * 256
        # A.45: concrete type
        req = RemoveInventoryItemRequest(player_id=1, item_type="turret_weapon", item_name=max_name)
        assert len(req.item_name) == 256

    # ------------------------------------------------------------------
    # Positive boundary: valid Discord snowflake IDs still work
    # ------------------------------------------------------------------

    def test_initialize_guild_valid_snowflake_guild_id(self):
        """A realistic Discord snowflake guild_id passes validation."""
        req = InitializeGuildRequest(guild_id=123456789012345678)
        assert req.guild_id == 123456789012345678

    def test_add_inventory_item_valid_snowflake_player_id(self):
        """A realistic Discord snowflake player_id passes validation."""
        req = AddInventoryItemRequest(player_id=987654321098765432, item_type="ship", item_name="Falcon")
        assert req.player_id == 987654321098765432


class TestSystemHealthResponseSchema:
    """Tests for SystemHealthResponse."""

    def test_valid_construction(self):
        resp = SystemHealthResponse(
            database_status="healthy",
            total_users=100,
            total_players=50,
            total_guilds=10,
            shop_items_count=200,
            system_status="ok",
        )
        assert resp.database_status == "healthy"
        assert resp.total_users == 100
        assert resp.system_status == "ok"

    def test_missing_field_raises(self):
        with pytest.raises(ValidationError):
            SystemHealthResponse(
                database_status="healthy",
                total_users=100,
                total_players=50,
                total_guilds=10,
            )


# ===========================================================================
# config_schema
# ===========================================================================


class TestGuildConfigResponseSchema:
    """Tests for GuildConfigResponse."""

    def test_valid_construction(self):
        resp = GuildConfigResponse(
            guild_id=1,
            configured=True,
            admin_role_configured=False,
            starting_credits=500,
            sale_price_factor=0.8,
            xp_thresholds={"Silver": 1000, "Gold": 5000},
            shop_config={"tech_levels": [1, 2]},
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-02T00:00:00",
        )
        assert resp.guild_id == 1
        assert resp.configured is True
        assert resp.sale_price_factor == 0.8

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            GuildConfigResponse(guild_id=1, configured=True)


class TestConfigValidationResponseSchema:
    """Tests for ConfigValidationResponse."""

    def test_valid_no_errors(self):
        resp = ConfigValidationResponse(valid=True, errors=[], warnings=[], guild_id=1)
        assert resp.valid is True
        assert resp.errors == []

    def test_with_errors_and_warnings(self):
        resp = ConfigValidationResponse(
            valid=False,
            errors=["Missing admin role"],
            warnings=["Low starting credits"],
            guild_id=1,
        )
        assert resp.valid is False
        assert len(resp.errors) == 1

    def test_wrong_type_for_errors_raises(self):
        with pytest.raises(ValidationError):
            ConfigValidationResponse(valid=True, errors="no-errors", warnings=[], guild_id=1)


class TestUpdateConfigRequestSchema:
    """Tests for UpdateConfigRequest."""

    def test_valid_minimal(self):
        req = UpdateConfigRequest(guild_id=1)
        assert req.admin_role_id is None
        assert req.starting_credits is None
        assert req.sale_price_factor is None
        assert req.xp_thresholds is None

    def test_starting_credits_zero_valid(self):
        req = UpdateConfigRequest(guild_id=1, starting_credits=0)
        assert req.starting_credits == 0

    def test_starting_credits_negative_raises(self):
        with pytest.raises(ValidationError):
            UpdateConfigRequest(guild_id=1, starting_credits=-100)

    def test_sale_price_factor_valid(self):
        req = UpdateConfigRequest(guild_id=1, sale_price_factor=0.75)
        assert req.sale_price_factor == 0.75

    def test_sale_price_factor_zero_raises(self):
        with pytest.raises(ValidationError):
            UpdateConfigRequest(guild_id=1, sale_price_factor=0.0)

    def test_sale_price_factor_exceeds_one_raises(self):
        with pytest.raises(ValidationError):
            UpdateConfigRequest(guild_id=1, sale_price_factor=1.5)

    def test_sale_price_factor_exactly_one_valid(self):
        req = UpdateConfigRequest(guild_id=1, sale_price_factor=1.0)
        assert req.sale_price_factor == 1.0


class TestConfigUpdateShopConfigRequestSchema:
    """Tests for config_schema.UpdateShopConfigRequest."""

    def test_valid_minimal(self):
        req = ConfigUpdateShopConfigRequest(guild_id=1)
        assert req.guild_id == 1
        assert req.tech_level_probabilities is None
        assert req.item_count_ranges is None
        assert req.quantity_ranges is None

    def test_all_optional_set(self):
        req = ConfigUpdateShopConfigRequest(
            guild_id=42,
            tech_level_probabilities={"5": 0.9},
            item_count_ranges={"Gold": {"min": 2, "max": 8}},
            quantity_ranges={"weapon": {"min": 1, "max": 3}},
        )
        assert req.guild_id == 42
        assert req.tech_level_probabilities == {"5": 0.9}


class TestUpdateXPThresholdsRequestSchema:
    """Tests for UpdateXPThresholdsRequest."""

    def test_valid_construction(self):
        req = UpdateXPThresholdsRequest(
            guild_id=1,
            thresholds={"Silver": 1000, "Gold": 5000, "Platinum": 20000},
        )
        assert req.thresholds["Silver"] == 1000
        assert req.thresholds["Platinum"] == 20000

    def test_missing_thresholds_raises(self):
        with pytest.raises(ValidationError):
            UpdateXPThresholdsRequest(guild_id=1)

    def test_wrong_type_for_thresholds_raises(self):
        with pytest.raises(ValidationError):
            UpdateXPThresholdsRequest(guild_id=1, thresholds="high")


# ===========================================================================
# discord_message_schema
# ===========================================================================


class TestEmbedPayloadDictSchema:
    """Tests for EmbedPayloadDict."""

    def test_all_defaults_none_or_empty(self):
        embed = EmbedPayloadDict()
        assert embed.title is None
        assert embed.description is None
        assert embed.color is None
        assert embed.fields == []
        assert embed.footer_text is None
        assert embed.footer_icon_url is None
        assert embed.timestamp is None
        assert embed.thumbnail_url is None
        assert embed.image_url is None

    def test_valid_full_construction(self):
        embed = EmbedPayloadDict(
            title="Test",
            description="A test embed",
            color=0xFF0000,
            fields=[{"name": "Field1", "value": "Val1"}],
            footer_text="Footer",
            footer_icon_url="https://example.com/icon.png",
            timestamp="2026-01-01T00:00:00",
            thumbnail_url="https://example.com/thumb.png",
            image_url="https://example.com/img.png",
        )
        assert embed.title == "Test"
        assert embed.color == 0xFF0000
        assert len(embed.fields) == 1

    def test_wrong_type_for_color_raises(self):
        with pytest.raises(ValidationError):
            EmbedPayloadDict(color="red")

    def test_wrong_type_for_fields_raises(self):
        with pytest.raises(ValidationError):
            EmbedPayloadDict(fields="not-a-list")


class TestDiscordMessageRequestSchema:
    """Tests for DiscordMessageRequest."""

    def test_valid_construction(self):
        req = DiscordMessageRequest(
            guild_id=111,
            channel_id=222,
            embed_payload=EmbedPayloadDict(title="Hello"),
        )
        assert req.guild_id == 111
        assert req.channel_id == 222
        assert req.message_id is None
        assert req.message_type == "general"

    def test_with_message_id(self):
        req = DiscordMessageRequest(
            guild_id=111,
            channel_id=222,
            message_id=333,
            embed_payload=EmbedPayloadDict(),
        )
        assert req.message_id == 333

    def test_custom_message_type(self):
        req = DiscordMessageRequest(
            guild_id=111,
            channel_id=222,
            embed_payload=EmbedPayloadDict(),
            message_type="bounty",
        )
        assert req.message_type == "bounty"

    def test_missing_required_fields_raises(self):
        with pytest.raises(ValidationError):
            DiscordMessageRequest(guild_id=111)


class TestDiscordMessageResponseSchema:
    """Tests for DiscordMessageResponse."""

    def test_valid_construction(self):
        uid = UUID("12345678-1234-5678-1234-567812345678")
        now = datetime.now(UTC)
        resp = DiscordMessageResponse(
            id=uid,
            guild_id=1,
            channel_id=2,
            message_id=3,
            embed_payload='{"title": "Hello"}',
            message_type="general",
            created_at=now,
            updated_at=now,
        )
        assert resp.id == uid
        assert resp.guild_id == 1
        assert resp.message_type == "general"

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            DiscordMessageResponse(guild_id=1, channel_id=2)


# ===========================================================================
# inventory_schema
# ===========================================================================


class TestInventoryItemResponseSchema:
    """Tests for InventoryItemResponse."""

    def test_valid_construction(self):
        item = InventoryItemResponse(
            id=1,
            item_type="ship",
            item_name="Falcon",
            quantity=1,
            acquired_at="2026-01-01T00:00:00",
            item_details={"armour": 500},
        )
        assert item.id == 1
        assert item.item_type == "ship"
        assert item.item_details == {"armour": 500}

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            InventoryItemResponse(id=1, item_type="ship")


class TestInventorySummaryResponseSchema:
    """Tests for InventorySummaryResponse."""

    def test_valid_construction(self):
        resp = InventorySummaryResponse(
            player_id=1,
            player_tier="Bronze",
            guild_id=10,
            ship=2,
            primary_weapon=3,
            secondary_weapon=1,
            turret_weapon=1,
            module=3,
            total_items=10,
        )
        assert resp.player_id == 1
        assert resp.total_items == 10

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            InventorySummaryResponse(player_id=1, player_tier="Bronze")


class TestInventoryAddItemRequestSchema:
    """Tests for inventory_schema.AddItemRequest."""

    def test_valid_ship(self):
        req = AddItemRequest(player_id=1, item_type="ship", item_name="Falcon")
        assert req.quantity == 1

    def test_valid_weapon(self):
        # A.45: concrete type (primary_weapon), not alias "weapon"
        req = AddItemRequest(player_id=1, item_type="primary_weapon", item_name="Laser")
        assert req.item_type == "primary_weapon"

    def test_invalid_type_raises(self):
        with pytest.raises(ValidationError):
            AddItemRequest(player_id=1, item_type="armor", item_name="X")

    def test_quantity_zero_raises(self):
        with pytest.raises(ValidationError):
            AddItemRequest(player_id=1, item_type="module", item_name="Shield", quantity=0)


class TestInventoryRemoveItemRequestSchema:
    """Tests for inventory_schema.RemoveItemRequest."""

    def test_valid_construction(self):
        # A.45: concrete type (turret_weapon), not alias "turret"
        req = RemoveItemRequest(player_id=1, item_type="turret_weapon", item_name="AutoGun", quantity=2)
        assert req.quantity == 2

    def test_invalid_type_raises(self):
        with pytest.raises(ValidationError):
            RemoveItemRequest(player_id=1, item_type="cargo", item_name="X")

    def test_quantity_negative_raises(self):
        with pytest.raises(ValidationError):
            RemoveItemRequest(player_id=1, item_type="ship", item_name="Falcon", quantity=-1)


class TestTransferItemRequestSchema:
    """Tests for TransferItemRequest."""

    def test_valid_construction(self):
        # A.45: concrete type (primary_weapon), not alias "weapon"
        req = TransferItemRequest(
            from_player_id=1,
            to_player_id=2,
            item_type="primary_weapon",
            item_name="Laser",
            quantity=1,
        )
        assert req.from_player_id == 1
        assert req.to_player_id == 2

    def test_invalid_type_raises(self):
        with pytest.raises(ValidationError):
            TransferItemRequest(from_player_id=1, to_player_id=2, item_type="food", item_name="X")

    def test_quantity_zero_raises(self):
        with pytest.raises(ValidationError):
            TransferItemRequest(from_player_id=1, to_player_id=2, item_type="module", item_name="Shield", quantity=0)

    def test_default_quantity_one(self):
        # A.45: concrete type
        req = TransferItemRequest(from_player_id=1, to_player_id=2, item_type="turret_weapon", item_name="Gun")
        assert req.quantity == 1


class TestItemTransactionResponseSchema:
    """Tests for ItemTransactionResponse."""

    def test_valid_with_time(self):
        resp = ItemTransactionResponse(
            player_id=1,
            item_type="ship",
            item_name="Falcon",
            quantity_changed=1,
            new_total_quantity=2,
            transaction_time="2026-01-01T00:00:00",
        )
        assert resp.transaction_time == "2026-01-01T00:00:00"

    def test_transaction_time_none(self):
        resp = ItemTransactionResponse(
            player_id=1,
            item_type="weapon",
            item_name="Laser",
            quantity_changed=-1,
            new_total_quantity=0,
            transaction_time=None,
        )
        assert resp.transaction_time is None

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            ItemTransactionResponse(player_id=1, item_type="ship")


# ===========================================================================
# players_schema
# ===========================================================================


class TestPlayerResponseSchema:
    """Tests for PlayerResponse."""

    def test_valid_construction(self):
        resp = PlayerResponse(
            id=1,
            user_id=100,
            guild_id=200,
            credits=5000,
            lifetime_credits=10000,
            systems_checked=3,
            bounty_wins=1,
            xp=250,
            tier="Bronze",
            prestige_count=0,
            duel_wins=2,
            duel_losses=1,
            duel_credits_won=500,
            duel_credits_lost=200,
            active_ship_id=None,
            created_at="2026-01-01",
            updated_at="2026-01-02",
        )
        assert resp.id == 1
        assert resp.tier == "Bronze"
        assert resp.active_ship_id is None

    def test_active_ship_id_set(self):
        resp = PlayerResponse(
            id=1,
            user_id=100,
            guild_id=200,
            credits=0,
            lifetime_credits=0,
            systems_checked=0,
            bounty_wins=0,
            xp=0,
            tier="Bronze",
            prestige_count=0,
            duel_wins=0,
            duel_losses=0,
            duel_credits_won=0,
            duel_credits_lost=0,
            active_ship_id=99,
            created_at="2026-01-01",
            updated_at="2026-01-01",
        )
        assert resp.active_ship_id == 99

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            PlayerResponse(id=1, user_id=100)


class TestPlayerStatisticsResponseSchema:
    """Tests for PlayerStatisticsResponse."""

    def test_valid_construction(self):
        resp = PlayerStatisticsResponse(
            player_id=1,
            tier="Gold",
            tier_level=3,
            xp=5000,
            prestige_count=1,
            credits=9000,
            lifetime_credits=50000,
            bounty_stats={"wins": 10, "losses": 2},
            duel_stats={"wins": 5, "losses": 3, "ratio": 1.67},
            created_at="2026-01-01",
            updated_at="2026-01-05",
        )
        assert resp.tier == "Gold"
        assert resp.bounty_stats["wins"] == 10

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            PlayerStatisticsResponse(player_id=1, tier="Gold")


class TestCreatePlayerRequestSchema:
    """Tests for CreatePlayerRequest."""

    def test_valid_minimal(self):
        req = CreatePlayerRequest(discord_id=999, guild_id=1)
        assert req.discord_id == 999
        assert req.discord_username is None

    def test_with_username(self):
        req = CreatePlayerRequest(discord_id=999, guild_id=1, discord_username="BountyHunter")
        assert req.discord_username == "BountyHunter"

    def test_missing_discord_id_raises(self):
        with pytest.raises(ValidationError):
            CreatePlayerRequest(guild_id=1)


class TestUpdateCreditsRequestSchema:
    """Tests for players_schema.UpdateCreditsRequest."""

    def test_valid_construction(self):
        req = UpdateCreditsRequest(credits=100)
        assert req.credits == 100
        assert req.update_lifetime is True

    def test_credits_zero_valid(self):
        req = UpdateCreditsRequest(credits=0)
        assert req.credits == 0

    def test_credits_negative_raises(self):
        with pytest.raises(ValidationError):
            UpdateCreditsRequest(credits=-50)

    def test_update_lifetime_false(self):
        req = UpdateCreditsRequest(credits=100, update_lifetime=False)
        assert req.update_lifetime is False


class TestUpdateXPRequestSchema:
    """Tests for players_schema.UpdateXPRequest."""

    def test_valid_xp(self):
        req = UpdateXPRequest(xp=750)
        assert req.xp == 750

    def test_xp_zero_valid(self):
        req = UpdateXPRequest(xp=0)
        assert req.xp == 0

    def test_xp_max_valid(self):
        req = UpdateXPRequest(xp=1_000_000)
        assert req.xp == 1_000_000

    def test_xp_negative_raises(self):
        with pytest.raises(ValidationError):
            UpdateXPRequest(xp=-1)

    def test_xp_over_max_raises(self):
        with pytest.raises(ValidationError):
            UpdateXPRequest(xp=1_000_001)


class TestUpdateTierRequestSchema:
    """Tests for UpdateTierRequest."""

    @pytest.mark.parametrize("tier", ["Bronze", "Silver", "Gold", "Platinum"])
    def test_valid_tiers(self, tier):
        req = UpdateTierRequest(tier=tier)
        assert req.tier == tier

    def test_invalid_tier_raises(self):
        with pytest.raises(ValidationError):
            UpdateTierRequest(tier="Diamond")

    def test_lowercase_tier_raises(self):
        with pytest.raises(ValidationError):
            UpdateTierRequest(tier="bronze")

    def test_empty_tier_raises(self):
        with pytest.raises(ValidationError):
            UpdateTierRequest(tier="")


# ===========================================================================
# scheduler_schema
# ===========================================================================


class TestOneTimeJobSchema:
    """Tests for OneTimeJob."""

    def test_all_defaults(self):
        job = OneTimeJob()
        assert job.payload == {}
        assert job.run_at is None
        assert job.delay_seconds is None

    def test_with_run_at(self):
        now = datetime.now(UTC)
        job = OneTimeJob(run_at=now)
        assert job.run_at == now

    def test_with_delay(self):
        job = OneTimeJob(delay_seconds=60)
        assert job.delay_seconds == 60

    def test_with_payload(self):
        job = OneTimeJob(payload={"key": "value"})
        assert job.payload == {"key": "value"}

    def test_wrong_type_for_delay_raises(self):
        with pytest.raises(ValidationError):
            OneTimeJob(delay_seconds="sixty")


class TestRecurringJobSchema:
    """Tests for RecurringJob."""

    def test_valid_construction(self):
        job = RecurringJob(cron="*/5 * * * *")
        assert job.cron == "*/5 * * * *"
        assert job.payload == {}

    def test_missing_cron_raises(self):
        with pytest.raises(ValidationError):
            RecurringJob()

    def test_with_payload(self):
        job = RecurringJob(cron="0 12 * * *", payload={"action": "refresh"})
        assert job.payload == {"action": "refresh"}

    def test_wrong_type_for_payload_raises(self):
        with pytest.raises(ValidationError):
            RecurringJob(cron="* * * * *", payload="not-a-dict")


class TestJobInfoSchema:
    """Tests for JobInfo."""

    def test_valid_with_next_run(self):
        now = datetime.now(UTC)
        info = JobInfo(id="job-1", next_run_time=now, trigger="cron", args=[])
        assert info.id == "job-1"
        assert info.next_run_time == now

    def test_next_run_time_none(self):
        info = JobInfo(id="job-2", next_run_time=None, trigger="date", args=["a", 1])
        assert info.next_run_time is None
        assert info.args == ["a", 1]

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            JobInfo(id="job-3")


class TestUpdateJobSchema:
    """Tests for UpdateJob."""

    def test_default_payload(self):
        job = UpdateJob()
        assert job.payload == {}

    def test_with_payload(self):
        job = UpdateJob(payload={"tier": "Gold"})
        assert job.payload == {"tier": "Gold"}

    def test_payload_none_raises_validation_error(self):
        """A.1: payload is non-nullable; None must raise ValidationError.

        The original schema had ``payload: dict | None = {}`` which allowed
        ``{"payload": null}`` through Pydantic validation and could corrupt
        live job args (job_executor.py does ``payload.get("job_type")`` on None,
        raising AttributeError on every subsequent execution).

        With ``payload: dict = Field(default_factory=dict)``, passing None
        must raise ValidationError instead of silently corrupting the job.
        """
        with pytest.raises(ValidationError):
            UpdateJob(payload=None)

    def test_wrong_type_for_payload_raises(self):
        with pytest.raises(ValidationError):
            UpdateJob(payload=["not", "a", "dict"])


# ===========================================================================
# ships_schema
# ===========================================================================


class TestShipsShipResponseSchema:
    """Tests for ships_schema.ShipResponse."""

    def test_valid_minimal(self):
        ship = ShipResponse(
            id=1,
            player_id=10,
            ship_name="Falcon",
            nickname=None,
            is_active=True,
            weapons=None,
            modules=None,
            turrets=None,
            created_at="2026-01-01",
        )
        assert ship.ship_name == "Falcon"
        assert ship.nickname is None
        assert ship.is_active is True

    def test_with_loadout(self):
        ship = ShipResponse(
            id=2,
            player_id=10,
            ship_name="Eagle",
            nickname="My Eagle",
            is_active=False,
            weapons=["Laser", "Blaster"],
            modules=["Shield"],
            turrets=["AutoGun"],
            created_at="2026-01-01",
        )
        assert ship.nickname == "My Eagle"
        assert ship.weapons == ["Laser", "Blaster"]

    def test_secondary_weapons_defaults_to_none(self):
        ship = ShipResponse(
            id=1,
            player_id=10,
            ship_name="Falcon",
            nickname=None,
            is_active=True,
            weapons=None,
            modules=None,
            turrets=None,
            created_at="2026-01-01",
        )
        assert ship.secondary_weapons is None

    def test_secondary_weapons_populated(self):
        ship = ShipResponse(
            id=3,
            player_id=10,
            ship_name="Eagle",
            nickname=None,
            is_active=True,
            weapons=["Laser"],
            modules=[],
            turrets=[],
            secondary_weapons=["AMR Tormentor"],
            created_at="2026-01-01",
        )
        assert ship.secondary_weapons == ["AMR Tormentor"]

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            ShipResponse(id=1, player_id=10)


class TestShipLoadoutSummaryResponseSchema:
    """Tests for ShipLoadoutSummaryResponse."""

    def test_valid_construction(self):
        resp = ShipLoadoutSummaryResponse(
            ship_id=1,
            ship_name="Falcon",
            nickname=None,
            is_active=True,
            weapons=["Laser"],
            modules=[],
            turrets=["AutoGun"],
            weapons_count=1,
            modules_count=0,
            turrets_count=1,
        )
        assert resp.ship_id == 1
        assert resp.weapons_count == 1

    def test_secondary_weapons_defaults_to_empty_list(self):
        resp = ShipLoadoutSummaryResponse(
            ship_id=1,
            ship_name="Falcon",
            nickname=None,
            is_active=True,
            weapons=["Laser"],
            modules=[],
            turrets=[],
            weapons_count=1,
            modules_count=0,
            turrets_count=0,
        )
        assert resp.secondary_weapons == []
        assert resp.secondary_weapons_count == 0

    def test_secondary_weapons_populated(self):
        resp = ShipLoadoutSummaryResponse(
            ship_id=2,
            ship_name="Eagle",
            nickname=None,
            is_active=True,
            weapons=[],
            modules=[],
            turrets=[],
            secondary_weapons=["AMR Tormentor"],
            weapons_count=0,
            modules_count=0,
            turrets_count=0,
            secondary_weapons_count=1,
        )
        assert resp.secondary_weapons == ["AMR Tormentor"]
        assert resp.secondary_weapons_count == 1

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            ShipLoadoutSummaryResponse(ship_id=1, ship_name="Falcon")


class TestCreateShipRequestSchema:
    """Tests for CreateShipRequest."""

    def test_valid_minimal(self):
        req = CreateShipRequest(player_id=1, ship_name="Falcon")
        assert req.player_id == 1
        assert req.nickname is None
        assert req.weapons == []
        assert req.modules == []
        assert req.turrets == []

    def test_with_all_fields(self):
        req = CreateShipRequest(
            player_id=1,
            ship_name="Eagle",
            nickname="My Eagle",
            weapons=["Laser"],
            modules=["Shield"],
            turrets=["AutoGun"],
        )
        assert req.nickname == "My Eagle"
        assert req.weapons == ["Laser"]

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            CreateShipRequest(player_id=1)


class TestUpdateLoadoutRequestSchema:
    """Tests for UpdateLoadoutRequest."""

    def test_all_none_defaults(self):
        req = UpdateLoadoutRequest()
        assert req.weapons is None
        assert req.modules is None
        assert req.turrets is None

    def test_with_loadout_set(self):
        req = UpdateLoadoutRequest(weapons=["Laser", "Blaster"], modules=[], turrets=["AutoGun"])
        assert req.weapons == ["Laser", "Blaster"]
        assert req.modules == []

    def test_wrong_type_raises(self):
        with pytest.raises(ValidationError):
            UpdateLoadoutRequest(weapons="Laser")


class TestUpdateNicknameRequestSchema:
    """Tests for UpdateNicknameRequest."""

    def test_valid_construction(self):
        req = UpdateNicknameRequest(nickname="Speedy")
        assert req.nickname == "Speedy"

    def test_missing_nickname_raises(self):
        with pytest.raises(ValidationError):
            UpdateNicknameRequest()

    def test_wrong_type_raises(self):
        with pytest.raises(ValidationError):
            UpdateNicknameRequest(nickname=42)


class TestEquipItemRequestSchema:
    """Tests for EquipItemRequest."""

    @pytest.mark.parametrize("eq_type", ["weapons", "modules", "turrets", "secondary_weapons"])
    def test_valid_equipment_types(self, eq_type):
        req = EquipItemRequest(player_id=1, equipment_type=eq_type, item_name="X")
        assert req.equipment_type == eq_type

    def test_secondary_weapons_accepted(self):
        """CI-25: secondary_weapons must be accepted — was wrongly excluded by old pattern."""
        req = EquipItemRequest(player_id=1, equipment_type="secondary_weapons", item_name="AMR Tormentor")
        assert req.equipment_type == "secondary_weapons"

    def test_invalid_equipment_type_raises(self):
        with pytest.raises(ValidationError):
            EquipItemRequest(player_id=1, equipment_type="armor", item_name="X")

    def test_shields_still_rejected(self):
        """CI-25: invalid values are still rejected after adding secondary_weapons."""
        with pytest.raises(ValidationError):
            EquipItemRequest(player_id=1, equipment_type="shields", item_name="X")

    def test_missing_item_name_raises(self):
        with pytest.raises(ValidationError):
            EquipItemRequest(player_id=1, equipment_type="weapons")

    def test_missing_player_id_raises(self):
        with pytest.raises(ValidationError):
            EquipItemRequest(equipment_type="weapons", item_name="X")


class TestUnequipItemRequestSchema:
    """Tests for UnequipItemRequest."""

    @pytest.mark.parametrize("eq_type", ["weapons", "modules", "turrets", "secondary_weapons"])
    def test_valid_equipment_types(self, eq_type):
        req = UnequipItemRequest(player_id=1, equipment_type=eq_type, item_name="Y")
        assert req.equipment_type == eq_type

    def test_secondary_weapons_accepted(self):
        """CI-25: secondary_weapons must be accepted — was wrongly excluded by old pattern."""
        req = UnequipItemRequest(player_id=1, equipment_type="secondary_weapons", item_name="AMR Tormentor")
        assert req.equipment_type == "secondary_weapons"

    def test_invalid_equipment_type_raises(self):
        with pytest.raises(ValidationError):
            UnequipItemRequest(player_id=1, equipment_type="shield", item_name="Y")

    def test_shields_still_rejected(self):
        """CI-25: invalid values are still rejected after adding secondary_weapons."""
        with pytest.raises(ValidationError):
            UnequipItemRequest(player_id=1, equipment_type="shields", item_name="Y")

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            UnequipItemRequest(player_id=1, equipment_type="modules")

    def test_missing_player_id_raises(self):
        with pytest.raises(ValidationError):
            UnequipItemRequest(equipment_type="modules", item_name="Y")


# ===========================================================================
# shops_schema
# ===========================================================================


class TestShopItemResponseSchema:
    """Tests for ShopItemResponse."""

    def test_valid_construction(self):
        item = ShopItemResponse(
            id=1,
            guild_id=100,
            tier="Bronze",
            tech_level=3,
            item_type="ship",
            item_name="Falcon",
            quantity=2,
            price=5000,
            last_restocked="2026-01-01T12:00:00",
            refresh_interval_hours=24,
        )
        assert item.tier == "Bronze"
        assert item.price == 5000

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            ShopItemResponse(id=1, guild_id=100)


class TestShopSummaryResponseSchema:
    """Tests for ShopSummaryResponse."""

    def test_valid_construction(self):
        resp = ShopSummaryResponse(
            guild_id=1,
            total_items=10,
            shops={"Bronze": {"ship": 3, "weapon": 4}, "Silver": {"ship": 3}},
        )
        assert resp.total_items == 10
        assert resp.shops["Bronze"]["ship"] == 3

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            ShopSummaryResponse(guild_id=1)


class TestPurchaseRequestSchema:
    """Tests for PurchaseRequest."""

    def test_valid_construction(self):
        req = PurchaseRequest(player_id=1, shop_item_id=5)
        assert req.quantity == 1

    def test_custom_quantity(self):
        req = PurchaseRequest(player_id=1, shop_item_id=5, quantity=3)
        assert req.quantity == 3

    def test_quantity_zero_raises(self):
        with pytest.raises(ValidationError):
            PurchaseRequest(player_id=1, shop_item_id=5, quantity=0)

    def test_quantity_negative_raises(self):
        with pytest.raises(ValidationError):
            PurchaseRequest(player_id=1, shop_item_id=5, quantity=-1)


class TestSellRequestSchema:
    """Tests for SellRequest (A.42b: item_type and target_tier removed).

    The new schema only requires player_id and item_name.
    item_type and target_tier are resolved server-side.
    """

    def test_valid_construction(self):
        """Minimal valid payload: player_id + item_name only."""
        req = SellRequest(player_id=1, item_name="Falcon")
        assert req.quantity == 1  # default

    def test_valid_with_quantity(self):
        """Accepts optional quantity."""
        req = SellRequest(player_id=1, item_name="Laser", quantity=3)
        assert req.quantity == 3

    def test_stale_extra_fields_silently_ignored(self):
        """Stale item_type and target_tier fields are silently ignored (not rejected).

        Documents the chosen contract for backward compatibility with stale clients.
        """
        # Pydantic default: extra fields are ignored (not forbidden).
        req = SellRequest(player_id=1, item_name="Falcon", **{"item_type": "ship", "target_tier": "Bronze"})
        assert req.player_id == 1
        assert req.item_name == "Falcon"
        assert not hasattr(req, "item_type")
        assert not hasattr(req, "target_tier")

    def test_quantity_zero_raises(self):
        with pytest.raises(ValidationError):
            SellRequest(player_id=1, item_name="Shield", quantity=0)


class TestTransactionResponseSchema:
    """Tests for TransactionResponse."""

    def test_valid_purchase(self):
        resp = TransactionResponse(
            player_id=1,
            item_type="ship",
            item_name="Falcon",
            quantity=1,
            total_cost=5000,
            remaining_credits=1000,
            transaction_type="purchase",
        )
        assert resp.total_cost == 5000
        assert resp.total_value is None

    def test_valid_sell(self):
        resp = TransactionResponse(
            player_id=1,
            item_type="weapon",
            item_name="Laser",
            quantity=1,
            total_value=2000,
            remaining_credits=7000,
            transaction_type="sell",
        )
        assert resp.total_cost is None
        assert resp.total_value == 2000

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            TransactionResponse(player_id=1, item_type="ship")


class TestShopsRefreshShopRequestSchema:
    """Tests for shops_schema.RefreshShopRequest."""

    def test_valid_construction(self):
        req = ShopsRefreshShopRequest(guild_id=1, tier="Silver")
        assert req.tier == "Silver"
        assert req.force_tech_level is None

    def test_valid_tiers(self):
        for tier in ["Bronze", "Silver", "Gold", "Platinum"]:
            req = ShopsRefreshShopRequest(guild_id=1, tier=tier)
            assert req.tier == tier

    def test_invalid_tier_raises(self):
        with pytest.raises(ValidationError):
            ShopsRefreshShopRequest(guild_id=1, tier="Starter")

    def test_force_tech_level_bounds(self):
        req_min = ShopsRefreshShopRequest(guild_id=1, tier="Bronze", force_tech_level=1)
        req_max = ShopsRefreshShopRequest(guild_id=1, tier="Bronze", force_tech_level=10)
        assert req_min.force_tech_level == 1
        assert req_max.force_tech_level == 10

    def test_force_tech_level_below_min_raises(self):
        with pytest.raises(ValidationError):
            ShopsRefreshShopRequest(guild_id=1, tier="Bronze", force_tech_level=0)

    def test_force_tech_level_above_max_raises(self):
        with pytest.raises(ValidationError):
            ShopsRefreshShopRequest(guild_id=1, tier="Bronze", force_tech_level=11)


# ===========================================================================
# users_schema
# ===========================================================================


class TestUserResponseSchema:
    """Tests for UserResponse."""

    def test_valid_construction(self):
        resp = UserResponse(
            id=1234,
            discord_username="Hunter42",
            created_at="2026-01-01",
            updated_at="2026-01-02",
        )
        assert resp.id == 1234
        assert resp.discord_username == "Hunter42"

    def test_discord_username_none(self):
        resp = UserResponse(
            id=9999,
            discord_username=None,
            created_at="2026-01-01",
            updated_at="2026-01-01",
        )
        assert resp.discord_username is None

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            UserResponse(id=1)


class TestCreateUserRequestSchema:
    """Tests for CreateUserRequest."""

    def test_valid_minimal(self):
        req = CreateUserRequest(id=9876)
        assert req.id == 9876
        assert req.discord_username is None

    def test_with_username(self):
        req = CreateUserRequest(id=9876, discord_username="Player1")
        assert req.discord_username == "Player1"

    def test_missing_id_raises(self):
        with pytest.raises(ValidationError):
            CreateUserRequest()

    def test_wrong_type_for_id_raises(self):
        with pytest.raises(ValidationError):
            CreateUserRequest(id="not-an-int")


class TestUpdateUserRequestSchema:
    """Tests for UpdateUserRequest."""

    def test_all_defaults_none(self):
        req = UpdateUserRequest()
        assert req.discord_username is None

    def test_with_username(self):
        req = UpdateUserRequest(discord_username="NewName")
        assert req.discord_username == "NewName"

    def test_username_can_be_set_to_none(self):
        req = UpdateUserRequest(discord_username=None)
        assert req.discord_username is None
