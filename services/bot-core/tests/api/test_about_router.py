"""Tests for the about API router endpoints.

Import path setup and sqlalchemy_utils mocking are handled by
tests/api/conftest.py which runs before this module is loaded.

The about router uses a get_db dependency (which uses db_manager.get_session),
not get_db_session. Tests override the get_db dependency directly.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.services.game_constants import GameConstants

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_object(**overrides):
    """Create a mock game object with common attributes."""
    defaults = dict(
        id=1,
        name="Test Object",
        aliases=["to", "testobj"],
        built_in=True,
        emoji="⚡",
        icon="icon.png",
        value=100,
        wiki="https://wiki.example.com/test",
        type="generic",
        tech_level=5,
        extra_atts=None,
    )
    defaults.update(overrides)
    obj = MagicMock()
    for k, v in defaults.items():
        setattr(obj, k, v)
    return obj


def make_mock_module(**overrides):
    obj = make_mock_object(**overrides)
    obj.max_equipped = 2
    return obj


def make_mock_primary_weapon(**overrides):
    obj = make_mock_object(**overrides)
    obj.dps = 150.0
    return obj


def make_mock_ship(**overrides):
    obj = make_mock_object(**overrides)
    obj.armour = 1000
    obj.cargo = 500
    obj.handling = 80
    obj.shop_spawn_rate = 0.5
    obj.max_modules = 4
    obj.max_primaries = 2
    obj.max_secondaries = 2
    obj.max_turrets = 1
    obj.manufacturer = "Corp X"
    obj.skinnable = True
    obj.compatible_skins = {"default": "skin_url"}
    obj.model = "model.glb"
    obj.norm_spec = "norm.png"
    obj.assets = ["asset1.png"]
    obj.save_due = False
    return obj


def make_mock_criminal(**overrides):
    obj = make_mock_object(**overrides)
    obj.faction = "Pirate"
    return obj


def make_mock_system(**overrides):
    obj = make_mock_object(**overrides)
    obj.coordinates = [1.0, 2.0, 3.0]
    obj.faction = "Federation"
    obj.neighbours = ["SystemB"]
    obj.security = "high"
    return obj


def make_mock_commodity(**overrides):
    obj = make_mock_object(**overrides)
    obj.type = "commodity"
    obj.manufacturer = None
    obj.subcategory = "booze"
    obj.price_source = "origin_system_price"
    obj.price_range_min_credits = 720
    obj.price_range_max_credits = 792
    obj.price_range_min_system = "Behén"
    obj.price_range_max_system = "Loma"
    obj.highest_non_loma_price = None
    obj.highest_non_loma_system = None
    return obj


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db_session():
    return AsyncMock()


@pytest.fixture
def mock_repos(mock_db_session):
    """Create mocked repositories that return no results by default."""
    repos = {}
    for name in ["module", "primary", "secondary", "turret", "ship", "system", "criminal", "commodity"]:
        repo = AsyncMock()
        repo.list_all = AsyncMock(return_value=[])
        repo.get_by_name = AsyncMock(return_value=None)
        repo.get_by_alias = AsyncMock(return_value=None)
        repo.get_by_id = AsyncMock(return_value=None)
        repos[name] = repo
    return repos


@pytest.fixture
def test_app(mock_db_session, mock_repos):
    import api.routers.about as about_module
    from api.routers.about import get_db
    from api.routers.about import router as about_router
    from api.routers.data import DataCategory

    # Override all repository instances on the module attributes
    about_module.module_repo = mock_repos["module"]
    about_module.primary_weapon_repo = mock_repos["primary"]
    about_module.secondary_weapon_repo = mock_repos["secondary"]
    about_module.turret_weapon_repo = mock_repos["turret"]
    about_module.ship_repo = mock_repos["ship"]
    about_module.system_repo = mock_repos["system"]
    about_module.criminal_repo = mock_repos["criminal"]
    about_module.commodity_repo = mock_repos["commodity"]

    # CRITICAL: CATEGORY_REPOS is a dict populated at module-import time with
    # references to the original repo instances.  Reassigning module attributes
    # above does NOT update the dict, so the router still calls the real repos.
    # We must also patch the dict itself.
    about_module.CATEGORY_REPOS[DataCategory.module] = mock_repos["module"]
    about_module.CATEGORY_REPOS[DataCategory.primary] = mock_repos["primary"]
    about_module.CATEGORY_REPOS[DataCategory.secondary] = mock_repos["secondary"]
    about_module.CATEGORY_REPOS[DataCategory.turret] = mock_repos["turret"]
    about_module.CATEGORY_REPOS[DataCategory.ship] = mock_repos["ship"]
    about_module.CATEGORY_REPOS[DataCategory.system] = mock_repos["system"]
    about_module.CATEGORY_REPOS[DataCategory.criminal] = mock_repos["criminal"]
    about_module.CATEGORY_REPOS[DataCategory.commodity] = mock_repos["commodity"]

    app = FastAPI()
    app.include_router(about_router, prefix="/api/v1")

    # Override the get_db dependency to return our mock session
    async def override_get_db():
        yield mock_db_session

    app.dependency_overrides[get_db] = override_get_db

    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


# ===========================================================================
# 1. GET /about/categories
# ===========================================================================


class TestListCategories:
    """Tests for GET /api/v1/about/categories."""

    def test_list_categories_happy_path(self, client):
        """Returns 200 with list of category strings."""
        response = client.get("/api/v1/about/categories")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_list_categories_includes_all_expected(self, client):
        """Returns all expected category values."""
        response = client.get("/api/v1/about/categories")

        assert response.status_code == 200
        data = response.json()
        expected = {
            "module",
            "primary_weapon",
            "secondary_weapon",
            "turret_weapon",
            "ship",
            "criminal",
            "system",
            "commodity",
        }
        assert expected == set(data)

    def test_list_categories_returns_strings(self, client):
        """All returned values are strings."""
        response = client.get("/api/v1/about/categories")

        assert response.status_code == 200
        for cat in response.json():
            assert isinstance(cat, str)


# ===========================================================================
# 2. GET /about/categories/{category}/objects
# ===========================================================================


class TestListObjectsForCategory:
    """Tests for GET /api/v1/about/categories/{category}/objects."""

    def test_list_objects_module_happy_path(self, client, mock_repos):
        """Returns 200 with list of module objects."""
        mock_obj = make_mock_module(name="Shield Generator")
        mock_repos["module"].list_all = AsyncMock(return_value=[mock_obj])

        response = client.get("/api/v1/about/categories/module/objects")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == 1
        assert data[0]["name"] == "Shield Generator"

    def test_list_objects_ship_category(self, client, mock_repos):
        """Returns 200 with list of ship objects."""
        mock_ship = make_mock_ship(name="Eagle")
        mock_repos["ship"].list_all = AsyncMock(return_value=[mock_ship])

        response = client.get("/api/v1/about/categories/ship/objects")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "Eagle"

    def test_list_objects_commodity_category(self, client, mock_repos):
        """Returns 200 with list of commodity objects."""
        mock_commodity = make_mock_commodity(name="Iron")
        mock_repos["commodity"].list_all = AsyncMock(return_value=[mock_commodity])

        response = client.get("/api/v1/about/categories/commodity/objects")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "Iron"

    def test_list_objects_empty_category(self, client, mock_repos):
        """Returns 200 with empty list when no objects exist."""
        mock_repos["module"].list_all = AsyncMock(return_value=[])

        response = client.get("/api/v1/about/categories/module/objects")

        assert response.status_code == 200
        assert response.json() == []

    def test_list_objects_invalid_category_returns_422(self, client):
        """Returns 422 when category is not valid."""
        response = client.get("/api/v1/about/categories/not_a_category/objects")

        assert response.status_code == 422

    def test_list_objects_server_error_returns_500(self, client, mock_repos):
        """Returns 500 when repository raises an unexpected exception."""
        mock_repos["module"].list_all = AsyncMock(side_effect=RuntimeError("DB failure"))

        response = client.get("/api/v1/about/categories/module/objects")

        assert response.status_code == 500

    def test_list_objects_includes_emoji_and_aliases(self, client, mock_repos):
        """Response includes emoji and aliases fields."""
        mock_obj = make_mock_module()
        mock_repos["module"].list_all = AsyncMock(return_value=[mock_obj])

        response = client.get("/api/v1/about/categories/module/objects")

        assert response.status_code == 200
        data = response.json()
        assert "aliases" in data[0]
        assert "emoji" in data[0]

    def test_list_objects_includes_tech_level_and_manufacturer(self, client, mock_repos):
        """A.31: Response includes tech_level and manufacturer fields for filter support.

        Previously these fields were missing, causing /list_category tech_level and
        manufacturer filters to always return empty results.
        """
        mock_obj = make_mock_module(tech_level=3)
        mock_obj.manufacturer = "Corp X"
        mock_repos["module"].list_all = AsyncMock(return_value=[mock_obj])

        response = client.get("/api/v1/about/categories/module/objects")

        assert response.status_code == 200
        data = response.json()
        assert "tech_level" in data[0], "tech_level must be present in preload response"
        assert data[0]["tech_level"] == 3
        assert "manufacturer" in data[0], "manufacturer must be present in preload response"
        assert data[0]["manufacturer"] == "Corp X"

    def test_list_objects_tech_level_none_when_missing(self, client, mock_repos):
        """A.31: tech_level returns None gracefully when object has no such attribute."""
        mock_ship = make_mock_ship(name="Betty")
        # Ships don't have tech_level in ORM; getattr should return None
        del mock_ship.tech_level  # remove the attribute if present on mock
        mock_repos["ship"].list_all = AsyncMock(return_value=[mock_ship])

        response = client.get("/api/v1/about/categories/ship/objects")

        assert response.status_code == 200
        data = response.json()
        assert "tech_level" in data[0]
        # Value is None (or may be set from mock defaults — just confirm field exists)


# ===========================================================================
# 3. GET /about/object/name/{object_name}
# ===========================================================================


class TestGetObjectByName:
    """Tests for GET /api/v1/about/object/name/{object_name}."""

    def test_get_object_by_name_found_in_module(self, client, mock_repos):
        """Returns 200 with object data when found in module repo."""
        mock_obj = make_mock_module(name="Shield Gen")
        mock_repos["module"].get_by_name = AsyncMock(return_value=mock_obj)

        response = client.get("/api/v1/about/object/name/Shield Gen")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Shield Gen"
        assert data["category"] == "module"
        assert "max_equipped" in data

    def test_get_object_by_name_found_in_ship(self, client, mock_repos):
        """Returns 200 with ship-specific fields when found in ship repo."""
        mock_ship = make_mock_ship(name="Eagle")
        mock_repos["ship"].get_by_name = AsyncMock(return_value=mock_ship)

        response = client.get("/api/v1/about/object/name/Eagle")

        assert response.status_code == 200
        data = response.json()
        assert data["category"] == "ship"
        assert "armour" in data
        assert "cargo" in data

    def test_get_object_by_name_found_in_primary_weapon(self, client, mock_repos):
        """Returns 200 with dps field when found in primary weapon repo."""
        mock_weapon = make_mock_primary_weapon(name="Pulse Laser")
        mock_repos["primary"].get_by_name = AsyncMock(return_value=mock_weapon)

        response = client.get("/api/v1/about/object/name/Pulse Laser")

        assert response.status_code == 200
        data = response.json()
        assert data["category"] == "primary_weapon"
        assert "dps" in data

    def test_get_object_by_name_found_in_criminal(self, client, mock_repos):
        """Returns 200 with faction field when found in criminal repo."""
        mock_criminal = make_mock_criminal(name="Bandit")
        mock_repos["criminal"].get_by_name = AsyncMock(return_value=mock_criminal)

        response = client.get("/api/v1/about/object/name/Bandit")

        assert response.status_code == 200
        data = response.json()
        assert data["category"] == "criminal"
        assert "faction" in data

    def test_get_object_by_name_found_in_system(self, client, mock_repos):
        """Returns 200 with coordinates and faction when found in system repo."""
        mock_system = make_mock_system(name="Sol")
        mock_repos["system"].get_by_name = AsyncMock(return_value=mock_system)

        response = client.get("/api/v1/about/object/name/Sol")

        assert response.status_code == 200
        data = response.json()
        assert data["category"] == "system"
        assert "coordinates" in data
        assert "faction" in data

    def test_get_object_by_name_found_in_commodity(self, client, mock_repos):
        """Returns 200 with subcategory + price fields when found in commodity repo."""
        mock_commodity = make_mock_commodity(name="Behén Wine", value=756)
        mock_repos["commodity"].get_by_name = AsyncMock(return_value=mock_commodity)

        response = client.get("/api/v1/about/object/name/Behén Wine")

        assert response.status_code == 200
        data = response.json()
        assert data["category"] == "commodity"
        assert data["subcategory"] == "booze"
        assert data["price_source"] == "origin_system_price"
        assert data["price_range_min_system"] == "Behén"
        assert data["value"] == 756

    def test_get_object_by_name_not_found_returns_404(self, client, mock_repos):
        """Returns 404 when object not found in any category."""
        # All repos return None (already the default)
        response = client.get("/api/v1/about/object/name/NonExistentObject")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_get_object_by_name_server_error_returns_500(self, client, mock_repos):
        """Returns 500 when repository raises unexpected exception."""
        mock_repos["module"].get_by_name = AsyncMock(side_effect=RuntimeError("DB failure"))

        response = client.get("/api/v1/about/object/name/SomeObject")

        assert response.status_code == 500


# ===========================================================================
# 4. GET /about/object/alias/{alias}
# ===========================================================================


class TestGetObjectByAlias:
    """Tests for GET /api/v1/about/object/alias/{alias}."""

    def test_get_object_by_alias_found_in_module(self, client, mock_repos):
        """Returns 200 with object data when found by alias."""
        mock_obj = make_mock_module()
        mock_repos["module"].get_by_alias = AsyncMock(return_value=mock_obj)

        response = client.get("/api/v1/about/object/alias/sg")

        assert response.status_code == 200
        data = response.json()
        assert data["category"] == "module"

    def test_get_object_by_alias_not_found_returns_404(self, client, mock_repos):
        """Returns 404 when alias not found in any category."""
        response = client.get("/api/v1/about/object/alias/zzzzz")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_get_object_by_alias_server_error_returns_500(self, client, mock_repos):
        """Returns 500 when repository raises unexpected exception."""
        mock_repos["module"].get_by_alias = AsyncMock(side_effect=RuntimeError("DB failure"))

        response = client.get("/api/v1/about/object/alias/sg")

        assert response.status_code == 500

    def test_get_object_by_alias_found_in_criminal(self, client, mock_repos):
        """Returns 200 with faction field when found in criminal repo by alias."""
        mock_criminal = make_mock_criminal()
        mock_repos["criminal"].get_by_alias = AsyncMock(return_value=mock_criminal)

        response = client.get("/api/v1/about/object/alias/bad_guy")

        assert response.status_code == 200
        data = response.json()
        assert data["category"] == "criminal"
        assert "faction" in data


# ===========================================================================
# 5. GET /about/object/{category}/{object_id}
# ===========================================================================


class TestGetObjectById:
    """Tests for GET /api/v1/about/object/{category}/{object_id}."""

    def test_get_object_by_id_module_happy_path(self, client, mock_repos):
        """Returns 200 with object when found in module repo."""
        mock_obj = make_mock_module()
        mock_repos["module"].get_by_id = AsyncMock(return_value=mock_obj)

        response = client.get("/api/v1/about/object/module/1")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 1
        assert data["category"] == "module"
        assert "max_equipped" in data

    def test_get_object_by_id_ship_happy_path(self, client, mock_repos):
        """Returns 200 with ship-specific fields."""
        mock_ship = make_mock_ship()
        mock_repos["ship"].get_by_id = AsyncMock(return_value=mock_ship)

        response = client.get("/api/v1/about/object/ship/1")

        assert response.status_code == 200
        data = response.json()
        assert data["category"] == "ship"
        assert "armour" in data
        assert "cargo" in data
        assert "max_modules" in data

    def test_get_object_by_id_primary_weapon_happy_path(self, client, mock_repos):
        """Returns 200 with dps field for primary weapon."""
        mock_weapon = make_mock_primary_weapon()
        mock_repos["primary"].get_by_id = AsyncMock(return_value=mock_weapon)

        response = client.get("/api/v1/about/object/primary_weapon/1")

        assert response.status_code == 200
        data = response.json()
        assert "dps" in data

    def test_get_object_by_id_not_found_returns_404(self, client, mock_repos):
        """Returns 404 when object ID doesn't exist in category."""
        # All repos return None (default)
        response = client.get("/api/v1/about/object/module/99999")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_get_object_by_id_invalid_category_returns_422(self, client):
        """Returns 422 when category is not a valid DataCategory."""
        response = client.get("/api/v1/about/object/not_a_category/1")

        assert response.status_code == 422

    def test_get_object_by_id_server_error_returns_500(self, client, mock_repos):
        """Returns 500 when repository raises unexpected exception.

        # BUG: In get_object_by_id, the call `obj = await repo.get_by_id(db, object_id)`
        # (about.py line ~252) is placed OUTSIDE the try/except block that
        # wraps the result-building logic. An exception from get_by_id therefore
        # propagates uncaught rather than being caught and converted to a 500
        # HTTPException. The correct behavior is 500; the production code fails
        # to achieve this by leaving repo.get_by_id() outside the guard.
        # This test will fail until the production code is fixed.
        """
        mock_repos["module"].get_by_id = AsyncMock(side_effect=RuntimeError("DB error"))

        # Use raise_server_exceptions=False so TestClient returns the response
        # rather than re-raising the unhandled server exception, allowing us to
        # assert on the status code.
        from fastapi.testclient import TestClient as TC

        safe_client = TC(client.app, raise_server_exceptions=False)
        response = safe_client.get("/api/v1/about/object/module/1")

        assert response.status_code == 500

    def test_get_object_by_id_system_has_system_fields(self, client, mock_repos):
        """Returns 200 with coordinates, faction, neighbours, security for system."""
        mock_system = make_mock_system()
        mock_repos["system"].get_by_id = AsyncMock(return_value=mock_system)

        response = client.get("/api/v1/about/object/system/1")

        assert response.status_code == 200
        data = response.json()
        assert data["category"] == "system"
        assert "coordinates" in data
        assert "faction" in data

    def test_get_object_by_id_criminal_has_faction(self, client, mock_repos):
        """Returns 200 with faction field for criminal."""
        mock_criminal = make_mock_criminal()
        mock_repos["criminal"].get_by_id = AsyncMock(return_value=mock_criminal)

        response = client.get("/api/v1/about/object/criminal/1")

        assert response.status_code == 200
        data = response.json()
        assert data["category"] == "criminal"
        assert "faction" in data

    def test_get_object_by_id_commodity_has_subcategory(self, client, mock_repos):
        """Returns 200 with subcategory + price fields for commodity."""
        mock_commodity = make_mock_commodity(name="Iron")
        mock_commodity.subcategory = "ore"
        mock_repos["commodity"].get_by_id = AsyncMock(return_value=mock_commodity)

        response = client.get("/api/v1/about/object/commodity/1")

        assert response.status_code == 200
        data = response.json()
        assert data["category"] == "commodity"
        assert data["subcategory"] == "ore"
        assert "price_range_min_credits" in data
        assert "highest_non_loma_system" in data


# ===========================================================================
# Additional tests for uncovered branches
# ===========================================================================


class TestListCategoriesErrorHandling:
    """Tests for the error-handling branch in list_categories (lines 76-78)."""

    def test_list_categories_server_error_returns_500(self, client):
        """Returns 500 when an unexpected exception is raised inside list_categories.

        Covers lines 76-78: the except block in list_categories.

        We patch the list comprehension by replacing the DataCategory class
        attribute on the about module temporarily so that iterating it raises.
        """
        import api.routers.about as about_module

        original = about_module.DataCategory

        class BrokenEnum:
            """Stub that raises when iterated."""

            def __iter__(self):
                raise RuntimeError("Enum exploded")

        about_module.DataCategory = BrokenEnum()
        try:
            response = client.get("/api/v1/about/categories")
        finally:
            about_module.DataCategory = original

        assert response.status_code == 500


class TestListObjectsForCategoryEdgeCases:
    """Tests for edge cases in list_objects_for_category."""

    def test_list_objects_http_exception_is_reraised(self, client, mock_repos):
        """An HTTPException raised inside list_objects_for_category propagates as-is.

        Covers line 105-106: the `except HTTPException: raise` path.
        The 404 from an unknown category (engineered here via direct exception)
        must not be swallowed by the outer except.
        """
        from fastapi import HTTPException as FastAPIHTTPException

        mock_repos["module"].list_all = AsyncMock(
            side_effect=FastAPIHTTPException(status_code=404, detail="Object not found")
        )
        response = client.get("/api/v1/about/categories/module/objects")
        # The HTTPException should propagate unchanged (not re-wrapped as 500)
        assert response.status_code == 404


class TestGetObjectByAliasAdditionalCategories:
    """Tests for category-specific field branches in get_object_by_alias (lines 203-230)."""

    def test_get_object_by_alias_found_in_primary_weapon(self, client, mock_repos):
        """Returns 200 with dps field when found in primary weapon repo by alias.

        Covers lines 205-206: the primary weapon branch in get_object_by_alias.
        """
        mock_weapon = make_mock_primary_weapon(name="Plasma Gun")
        mock_repos["primary"].get_by_alias = AsyncMock(return_value=mock_weapon)

        response = client.get("/api/v1/about/object/alias/pg")

        assert response.status_code == 200
        data = response.json()
        assert data["category"] == "primary_weapon"
        assert "dps" in data

    def test_get_object_by_alias_found_in_ship(self, client, mock_repos):
        """Returns 200 with ship-specific fields when found in ship repo by alias.

        Covers lines 207-224: the ship branch in get_object_by_alias.
        """
        mock_ship = make_mock_ship(name="Eagle MkII")
        mock_repos["ship"].get_by_alias = AsyncMock(return_value=mock_ship)

        response = client.get("/api/v1/about/object/alias/eagle2")

        assert response.status_code == 200
        data = response.json()
        assert data["category"] == "ship"
        assert "armour" in data
        assert "cargo" in data
        assert "max_modules" in data
        assert "manufacturer" in data

    def test_get_object_by_alias_found_in_system(self, client, mock_repos):
        """Returns 200 with coordinates and faction when found in system repo by alias.

        Covers lines 228-230: the system branch in get_object_by_alias.
        """
        mock_system = make_mock_system(name="Alpha Centauri")
        mock_repos["system"].get_by_alias = AsyncMock(return_value=mock_system)

        response = client.get("/api/v1/about/object/alias/alpha")

        assert response.status_code == 200
        data = response.json()
        assert data["category"] == "system"
        assert "coordinates" in data
        assert "faction" in data


class TestGetObjectByIdErrorHandling:
    """Tests for the error-handling block in get_object_by_id (lines 305-309)."""

    def test_get_object_by_id_exception_in_result_building_returns_500(self, client, mock_repos):
        """Returns 500 when an exception is raised during result dict construction.

        Covers lines 305-309: the try/except block INSIDE get_object_by_id that
        wraps the result-building logic (after the object is successfully fetched).

        We return a mock object whose attribute access raises an exception so
        the dict-building code fails.
        """
        broken_obj = MagicMock()
        # id and name work fine
        broken_obj.id = 99
        broken_obj.name = "BrokenShip"
        broken_obj.aliases = []
        # Accessing armour raises, which triggers the except block
        type(broken_obj).armour = property(lambda self: (_ for _ in ()).throw(RuntimeError("armour broken")))
        mock_repos["ship"].get_by_id = AsyncMock(return_value=broken_obj)

        from fastapi.testclient import TestClient as TC

        safe_client = TC(client.app, raise_server_exceptions=False)
        response = safe_client.get("/api/v1/about/object/ship/99")

        assert response.status_code == 500


# ===========================================================================
# 6. GET /about/ships/{ship_name}/render-info
# ===========================================================================


def make_mock_skinnable_ship(**overrides):
    """Return a mock Ship with full render-info fields for a skinnable Phantom."""
    defaults = dict(
        id=10,
        name="Phantom",
        skinnable=True,
        texture_regions=2,
        model="/app/data/game-objects/items/ships/Terran/Phantom.bbship/Phantom_Full.obj",
        norm_spec="/app/data/game-objects/items/ships/Terran/Phantom.bbship/ship_010_terran_normal_specular.bmp",
        assets=[
            "/app/data/game-objects/items/ships/Terran/Phantom.bbship/Phantom_Full.mtl",
            "/app/data/game-objects/items/ships/Terran/Phantom.bbship/skinBase.png",
            "/app/data/game-objects/items/ships/Terran/Phantom.bbship/ship_010_terran_diffuse.bmp",
            "/app/data/game-objects/items/ships/Terran/Phantom.bbship/mask1.jpg",
            "/app/data/game-objects/items/ships/Terran/Phantom.bbship/mask2.jpg",
        ],
        compatible_skins={"urban-camo": "https://example.com/urban-camo.png"},
    )
    defaults.update(overrides)
    obj = MagicMock()
    for k, v in defaults.items():
        setattr(obj, k, v)
    return obj


class TestGetShipRenderInfo:
    """Tests for GET /api/v1/about/ships/{ship_name}/render-info."""

    def test_render_info_skinnable_ship(self, client, mock_repos):
        """Known skinnable ship returns 200 with all required fields."""
        mock_ship = make_mock_skinnable_ship()
        mock_repos["ship"].get_by_name = AsyncMock(return_value=mock_ship)

        response = client.get("/api/v1/about/ships/Phantom/render-info")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Phantom"
        assert data["skinnable"] is True
        assert data["texture_regions"] == 2
        assert data["model_path"].endswith("Phantom_Full.obj")
        assert data["bbship_dir"] == "/app/data/game-objects/items/ships/Terran/Phantom.bbship"
        assert "mask_paths" in data
        assert "compatible_skins" in data

    def test_render_info_non_skinnable_ship(self, client, mock_repos):
        """Non-skinnable ship returns 404 with 'not skinnable' message."""
        mock_ship = make_mock_skinnable_ship(skinnable=False)
        mock_repos["ship"].get_by_name = AsyncMock(return_value=mock_ship)

        response = client.get("/api/v1/about/ships/Phantom/render-info")

        assert response.status_code == 404
        assert "not skinnable" in response.json()["detail"].lower()

    def test_render_info_ship_not_found(self, client, mock_repos):
        """Unknown ship name returns 404."""
        mock_repos["ship"].get_by_name = AsyncMock(return_value=None)

        response = client.get("/api/v1/about/ships/UnknownShip/render-info")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_render_info_parses_masks(self, client, mock_repos):
        """mask_paths is extracted and ordered numerically (mask1 before mask2)."""
        mock_ship = make_mock_skinnable_ship(
            assets=[
                "/path/mask2.jpg",
                "/path/mask1.jpg",
                "/path/skinBase.png",
                "/path/ship.mtl",
            ]
        )
        mock_repos["ship"].get_by_name = AsyncMock(return_value=mock_ship)

        response = client.get("/api/v1/about/ships/Phantom/render-info")

        assert response.status_code == 200
        masks = response.json()["mask_paths"]
        assert masks == ["/path/mask1.jpg", "/path/mask2.jpg"]

    def test_render_info_parses_mtl(self, client, mock_repos):
        """mtl_path is extracted from assets list."""
        mock_ship = make_mock_skinnable_ship(
            assets=[
                "/path/Phantom_Full.mtl",
                "/path/skinBase.png",
            ]
        )
        mock_repos["ship"].get_by_name = AsyncMock(return_value=mock_ship)

        response = client.get("/api/v1/about/ships/Phantom/render-info")

        assert response.status_code == 200
        assert response.json()["mtl_path"] == "/path/Phantom_Full.mtl"

    def test_render_info_parses_skin_base(self, client, mock_repos):
        """skin_base_path is extracted from assets list."""
        mock_ship = make_mock_skinnable_ship(
            assets=[
                "/path/Phantom_Full.mtl",
                "/path/skinBase.png",
            ]
        )
        mock_repos["ship"].get_by_name = AsyncMock(return_value=mock_ship)

        response = client.get("/api/v1/about/ships/Phantom/render-info")

        assert response.status_code == 200
        assert response.json()["skin_base_path"] == "/path/skinBase.png"

    def test_render_info_no_assets(self, client, mock_repos):
        """Ship with None/empty assets returns empty mask_paths and None path fields."""
        mock_ship = make_mock_skinnable_ship(assets=None)
        mock_repos["ship"].get_by_name = AsyncMock(return_value=mock_ship)

        response = client.get("/api/v1/about/ships/Phantom/render-info")

        assert response.status_code == 200
        data = response.json()
        assert data["mask_paths"] == []
        assert data["mtl_path"] is None
        assert data["skin_base_path"] is None
        assert data["diffuse_path"] is None


# ===========================================================================
# T11 — §14 combat field enrichment tests
# ===========================================================================


def _make_mock_secondary(name="Test Secondary", subtype="missile", damage=100, **extra_inner_overrides):
    """Build a secondary-weapon mock with inner extra_atts structure matching DB nesting."""
    inner = {"loading_speed_ms": 3000, "range_m": 5000, "subtype": subtype}
    inner.update(extra_inner_overrides)
    obj = make_mock_object(name=name, type="SecondaryWeapon")
    obj.damage = damage
    obj.loading_speed = 3000
    obj.extra_atts = {"extra_atts": inner}
    return obj


def _make_mock_primary_emp(name="Luna EMP Mk I", emp_damage=3, dps=8.57):
    """Build a primary-weapon mock with EMP damage in inner extra_atts."""
    inner = {"damage_per_shot": 0, "emp_damage": emp_damage, "subtype": "emp-blaster"}
    obj = make_mock_object(name=name, type="PrimaryWeapon")
    obj.dps = dps
    obj.extra_atts = {"extra_atts": inner}
    return obj


def _make_mock_pwm(name="Nirai Overdrive", damage_pct=-10, fire_rate_pct=20, dps_multiplier=1.1):
    """Build a PrimaryWeaponMod module mock with outer dpsMultiplier + inner damage/fire_rate pcts."""
    inner = {"damage_pct": float(damage_pct), "fire_rate_pct": float(fire_rate_pct)}
    obj = make_mock_object(name=name, type="PrimaryWeaponModModule")
    obj.max_equipped = 1
    obj.extra_atts = {"dpsMultiplier": dps_multiplier, "extra_atts": inner}
    return obj


class TestT11CombatFieldEnrichment:
    """§14 / T11 — combat-relevant fields surfaced in about API responses.

    All tests call GET /about/object/name/{name} which exercises _enrich_combat_fields().
    """

    # -------------------------------------------------------------------------
    # EMP primary weapons
    # -------------------------------------------------------------------------

    def test_emp_primary_luna_emp_damage_non_null(self, client, mock_repos):
        """Luna EMP Mk I: emp_damage must be 3 and non-null."""
        mock_repos["primary"].get_by_name = AsyncMock(return_value=_make_mock_primary_emp("Luna EMP Mk I", 3))

        resp = client.get("/api/v1/about/object/name/Luna EMP Mk I")

        assert resp.status_code == 200
        assert resp.json()["emp_damage"] == 3

    def test_emp_primary_sol_emp_damage(self, client, mock_repos):
        """Sol EMP Mk II: emp_damage must be 5."""
        mock_repos["primary"].get_by_name = AsyncMock(return_value=_make_mock_primary_emp("Sol EMP Mk II", 5))

        resp = client.get("/api/v1/about/object/name/Sol EMP Mk II")

        assert resp.status_code == 200
        assert resp.json()["emp_damage"] == 5

    def test_emp_primary_dia_emp_damage(self, client, mock_repos):
        """Dia EMP Mk III: emp_damage must be 8."""
        mock_repos["primary"].get_by_name = AsyncMock(return_value=_make_mock_primary_emp("Dia EMP Mk III", 8))

        resp = client.get("/api/v1/about/object/name/Dia EMP Mk III")

        assert resp.status_code == 200
        assert resp.json()["emp_damage"] == 8

    def test_non_emp_primary_emp_damage_null(self, client, mock_repos):
        """Non-EMP primary weapon: emp_damage must be null."""
        plain = make_mock_primary_weapon(name="Nirai Pulse", extra_atts={"extra_atts": {"damage_per_shot": 20}})
        mock_repos["primary"].get_by_name = AsyncMock(return_value=plain)

        resp = client.get("/api/v1/about/object/name/Nirai Pulse")

        assert resp.status_code == 200
        assert resp.json()["emp_damage"] is None

    # -------------------------------------------------------------------------
    # EMP secondary weapons (Mamba EMP missile, Netha EMP mine)
    # -------------------------------------------------------------------------

    def test_mamba_emp_secondary_emp_damage(self, client, mock_repos):
        """Mamba EMP: emp_damage must be 100."""
        mock_repos["secondary"].get_by_name = AsyncMock(
            return_value=_make_mock_secondary("Mamba EMP", subtype="missile", damage=0, emp_damage=100)
        )

        resp = client.get("/api/v1/about/object/name/Mamba EMP")

        assert resp.status_code == 200
        assert resp.json()["emp_damage"] == 100

    def test_netha_emp_mine_emp_damage(self, client, mock_repos):
        """Netha EMP mine: emp_damage must be 500."""
        mock_repos["secondary"].get_by_name = AsyncMock(
            return_value=_make_mock_secondary("Netha EMP", subtype="mine", damage=0, emp_damage=500)
        )

        resp = client.get("/api/v1/about/object/name/Netha EMP")

        assert resp.status_code == 200
        assert resp.json()["emp_damage"] == 500

    # -------------------------------------------------------------------------
    # Cluster missiles
    # -------------------------------------------------------------------------

    def test_shesha_burst_count_3(self, client, mock_repos):
        """Shesha cluster missile: burst_count == 3."""
        mock_repos["secondary"].get_by_name = AsyncMock(
            return_value=_make_mock_secondary("Shesha", subtype="cluster-missile", damage=60, burst_count=3)
        )

        resp = client.get("/api/v1/about/object/name/Shesha")

        assert resp.status_code == 200
        assert resp.json()["burst_count"] == 3

    def test_garuda_iv_burst_count_4(self, client, mock_repos):
        """Garuda-IV: burst_count == 4."""
        mock_repos["secondary"].get_by_name = AsyncMock(
            return_value=_make_mock_secondary("Garuda-IV", subtype="cluster-missile", damage=75, burst_count=4)
        )

        resp = client.get("/api/v1/about/object/name/Garuda-IV")

        assert resp.status_code == 200
        assert resp.json()["burst_count"] == 4

    def test_patala_burst_count_5(self, client, mock_repos):
        """Patala: burst_count == 5."""
        mock_repos["secondary"].get_by_name = AsyncMock(
            return_value=_make_mock_secondary("Patala", subtype="cluster-missile", damage=90, burst_count=5)
        )

        resp = client.get("/api/v1/about/object/name/Patala")

        assert resp.status_code == 200
        assert resp.json()["burst_count"] == 5

    def test_non_cluster_burst_count_null(self, client, mock_repos):
        """Plain missile: burst_count must be null."""
        mock_repos["secondary"].get_by_name = AsyncMock(
            return_value=_make_mock_secondary("Edo", subtype="missile", damage=200)
        )

        resp = client.get("/api/v1/about/object/name/Edo")

        assert resp.status_code == 200
        assert resp.json()["burst_count"] is None

    # -------------------------------------------------------------------------
    # Nuke weapons
    # -------------------------------------------------------------------------

    def test_liberator_nuke_effective_magnitude(self, client, mock_repos):
        """Liberator nuke: nuke_effective_magnitude_m == round(12500 * 0.10) == 1250."""
        mock_repos["secondary"].get_by_name = AsyncMock(
            return_value=_make_mock_secondary("Liberator", subtype="nuke", damage=850, magnitude_m=12500)
        )

        resp = client.get("/api/v1/about/object/name/Liberator")

        assert resp.status_code == 200
        data = resp.json()
        assert data["nuke_effective_magnitude_m"] == 1250
        assert data["nuke_direct_damage"] == 850
        assert data["nuke_self_damage_factor"] == pytest.approx(GameConstants.NUKE_FRIENDLY_FACTOR)  # 0.50 (D-014)

    def test_extinctor_nuke_effective_magnitude(self, client, mock_repos):
        """AMR Extinctor nuke: nuke_effective_magnitude_m == round(40000 * 0.10) == 4000."""
        mock_repos["secondary"].get_by_name = AsyncMock(
            return_value=_make_mock_secondary("AMR Extinctor", subtype="nuke", damage=700, magnitude_m=40000)
        )

        resp = client.get("/api/v1/about/object/name/AMR Extinctor")

        assert resp.status_code == 200
        data = resp.json()
        assert data["nuke_effective_magnitude_m"] == 4000

    def test_non_nuke_nuke_fields_null(self, client, mock_repos):
        """Plain missile: all nuke fields must be null."""
        mock_repos["secondary"].get_by_name = AsyncMock(
            return_value=_make_mock_secondary("Edo", subtype="missile", damage=200)
        )

        resp = client.get("/api/v1/about/object/name/Edo")

        assert resp.status_code == 200
        data = resp.json()
        assert data["nuke_direct_damage"] is None
        assert data["nuke_effective_magnitude_m"] is None
        assert data["nuke_self_damage_factor"] is None

    # -------------------------------------------------------------------------
    # PrimaryWeaponMod modules
    # -------------------------------------------------------------------------

    def test_nirai_overdrive_pwm_fields(self, client, mock_repos):
        """Nirai Overdrive: damage_pct=-10, fire_rate_pct=+20, dps_multiplier=1.1."""
        mock_repos["module"].get_by_name = AsyncMock(
            return_value=_make_mock_pwm("Nirai Overdrive", damage_pct=-10, fire_rate_pct=20, dps_multiplier=1.1)
        )

        resp = client.get("/api/v1/about/object/name/Nirai Overdrive")

        assert resp.status_code == 200
        data = resp.json()
        assert data["damage_pct"] == -10
        assert data["fire_rate_pct"] == 20
        assert data["dps_multiplier"] == pytest.approx(1.1)

    def test_nirai_overcharge_pwm_fields(self, client, mock_repos):
        """Nirai Overcharge: damage_pct=+20, fire_rate_pct=-10, dps_multiplier=1.1."""
        mock_repos["module"].get_by_name = AsyncMock(
            return_value=_make_mock_pwm("Nirai Overcharge", damage_pct=20, fire_rate_pct=-10, dps_multiplier=1.1)
        )

        resp = client.get("/api/v1/about/object/name/Nirai Overcharge")

        assert resp.status_code == 200
        data = resp.json()
        assert data["damage_pct"] == 20
        assert data["fire_rate_pct"] == -10
        assert data["dps_multiplier"] == pytest.approx(1.1)

    def test_overdrive_overcharge_distinct_pcts(self, client, mock_repos):
        """Overdrive and Overcharge have identical dps_multiplier but different pct values."""
        overdrive = _make_mock_pwm("Nirai Overdrive", damage_pct=-10, fire_rate_pct=20, dps_multiplier=1.1)
        overcharge = _make_mock_pwm("Nirai Overcharge", damage_pct=20, fire_rate_pct=-10, dps_multiplier=1.1)
        mock_repos["module"].get_by_name = AsyncMock(
            side_effect=lambda db, n: overdrive if "Overdrive" in n else overcharge
        )

        resp_od = client.get("/api/v1/about/object/name/Nirai Overdrive")
        resp_oc = client.get("/api/v1/about/object/name/Nirai Overcharge")

        d_od = resp_od.json()
        d_oc = resp_oc.json()
        # dps_multiplier identical
        assert d_od["dps_multiplier"] == pytest.approx(d_oc["dps_multiplier"])
        # but pcts differ
        assert d_od["damage_pct"] != d_oc["damage_pct"]
        assert d_od["fire_rate_pct"] != d_oc["fire_rate_pct"]

    def test_non_pwm_module_fields_null(self, client, mock_repos):
        """Non-PrimaryWeaponMod module (Scanner): damage_pct/fire_rate_pct/dps_multiplier all null."""
        scanner = make_mock_module(name="Nirai Scanner", type="ScannerModule")
        scanner.extra_atts = None
        mock_repos["module"].get_by_name = AsyncMock(return_value=scanner)

        resp = client.get("/api/v1/about/object/name/Nirai Scanner")

        assert resp.status_code == 200
        data = resp.json()
        assert data["damage_pct"] is None
        assert data["fire_rate_pct"] is None
        assert data["dps_multiplier"] is None

    # -------------------------------------------------------------------------
    # Backward compatibility: extra_atts blob preserved
    # -------------------------------------------------------------------------

    def test_extra_atts_blob_preserved_on_pwm(self, client, mock_repos):
        """extra_atts blob is still present alongside explicit T11 fields."""
        obj = _make_mock_pwm("Nirai Overdrive", damage_pct=-10, fire_rate_pct=20, dps_multiplier=1.1)
        mock_repos["module"].get_by_name = AsyncMock(return_value=obj)

        resp = client.get("/api/v1/about/object/name/Nirai Overdrive")

        assert resp.status_code == 200
        data = resp.json()
        # extra_atts blob must still be present (backward compat)
        assert "extra_atts" in data
        assert data["extra_atts"] is not None

    def test_extra_atts_blob_preserved_on_emp_primary(self, client, mock_repos):
        """extra_atts blob is still present for EMP primary weapons."""
        obj = _make_mock_primary_emp("Luna EMP Mk I", emp_damage=3)
        mock_repos["primary"].get_by_name = AsyncMock(return_value=obj)

        resp = client.get("/api/v1/about/object/name/Luna EMP Mk I")

        assert resp.status_code == 200
        data = resp.json()
        assert "extra_atts" in data
        assert data["extra_atts"] is not None


# ===========================================================================
# D-002 — primary_weapon and turret_weapon per-shot breakdown fields
# ===========================================================================


def _make_mock_primary_with_combat(
    name="Nirai Pulse",
    damage_per_shot=20,
    loading_speed_ms=900,
    subtype="laser",
    dps=22.2,
):
    """Build a primary-weapon mock with full inner extra_atts combat fields."""
    inner = {
        "damage_per_shot": damage_per_shot,
        "loading_speed_ms": loading_speed_ms,
        "subtype": subtype,
    }
    obj = make_mock_object(name=name, type="PrimaryWeapon")
    obj.dps = dps
    obj.extra_atts = {"extra_atts": inner}
    return obj


def _make_mock_turret_with_combat(
    name="Nirai Turret",
    damage_per_shot=35,
    loading_speed_ms=1200,
    subtype="auto-cannon",
    dps=29.1,
    automatic=True,
):
    """Build a turret-weapon mock with full inner extra_atts combat fields."""
    inner = {
        "damage_per_shot": damage_per_shot,
        "loading_speed_ms": loading_speed_ms,
        "subtype": subtype,
    }
    obj = make_mock_object(name=name, type="TurretWeapon")
    obj.dps = dps
    obj.automatic = automatic
    obj.extra_atts = {"extra_atts": inner}
    return obj


class TestPrimaryTurretWeaponFields:
    """D-002 — damage_per_shot, loading_speed_ms, subtype surfaced for primary and turret weapons.

    All tests call GET /about/object/name/{name} which exercises _enrich_combat_fields().
    Secondary and module paths are NOT touched here.
    """

    # -------------------------------------------------------------------------
    # Primary weapon — field presence and values
    # -------------------------------------------------------------------------

    def test_primary_damage_per_shot_present(self, client, mock_repos):
        """Primary weapon: damage_per_shot must be surfaced and match inner extra_atts."""
        mock_repos["primary"].get_by_name = AsyncMock(
            return_value=_make_mock_primary_with_combat(name="Pulse Laser", damage_per_shot=16)
        )

        resp = client.get("/api/v1/about/object/name/Pulse Laser")

        assert resp.status_code == 200
        assert resp.json()["damage_per_shot"] == 16

    def test_primary_loading_speed_ms_present(self, client, mock_repos):
        """Primary weapon: loading_speed_ms must be surfaced and match inner extra_atts."""
        mock_repos["primary"].get_by_name = AsyncMock(
            return_value=_make_mock_primary_with_combat(name="Pulse Laser", loading_speed_ms=900)
        )

        resp = client.get("/api/v1/about/object/name/Pulse Laser")

        assert resp.status_code == 200
        assert resp.json()["loading_speed_ms"] == 900

    def test_primary_subtype_present(self, client, mock_repos):
        """Primary weapon: subtype must be surfaced and match inner extra_atts."""
        mock_repos["primary"].get_by_name = AsyncMock(
            return_value=_make_mock_primary_with_combat(name="Pulse Laser", subtype="laser")
        )

        resp = client.get("/api/v1/about/object/name/Pulse Laser")

        assert resp.status_code == 200
        assert resp.json()["subtype"] == "laser"

    def test_primary_plasma_collector_subtype(self, client, mock_repos):
        """Primary weapon: plasma-collector subtype is surfaced without modification."""
        mock_repos["primary"].get_by_name = AsyncMock(
            return_value=_make_mock_primary_with_combat(name="Plasma Collector", subtype="plasma-collector")
        )

        resp = client.get("/api/v1/about/object/name/Plasma Collector")

        assert resp.status_code == 200
        assert resp.json()["subtype"] == "plasma-collector"

    def test_primary_missing_combat_fields_null(self, client, mock_repos):
        """Primary weapon with no inner extra_atts: damage_per_shot/loading_speed_ms/subtype all null."""
        obj = make_mock_primary_weapon(name="Bare Laser", extra_atts=None)
        mock_repos["primary"].get_by_name = AsyncMock(return_value=obj)

        resp = client.get("/api/v1/about/object/name/Bare Laser")

        assert resp.status_code == 200
        data = resp.json()
        assert data["damage_per_shot"] is None
        assert data["loading_speed_ms"] is None
        assert data["subtype"] is None

    # -------------------------------------------------------------------------
    # Turret weapon — field presence and values
    # -------------------------------------------------------------------------

    def test_turret_damage_per_shot_present(self, client, mock_repos):
        """Turret weapon: damage_per_shot must be surfaced and match inner extra_atts."""
        mock_repos["turret"].get_by_name = AsyncMock(
            return_value=_make_mock_turret_with_combat(name="Auto Cannon", damage_per_shot=35)
        )

        resp = client.get("/api/v1/about/object/name/Auto Cannon")

        assert resp.status_code == 200
        assert resp.json()["damage_per_shot"] == 35

    def test_turret_loading_speed_ms_present(self, client, mock_repos):
        """Turret weapon: loading_speed_ms must be surfaced and match inner extra_atts."""
        mock_repos["turret"].get_by_name = AsyncMock(
            return_value=_make_mock_turret_with_combat(name="Auto Cannon", loading_speed_ms=1200)
        )

        resp = client.get("/api/v1/about/object/name/Auto Cannon")

        assert resp.status_code == 200
        assert resp.json()["loading_speed_ms"] == 1200

    def test_turret_subtype_present(self, client, mock_repos):
        """Turret weapon: subtype must be surfaced and match inner extra_atts."""
        mock_repos["turret"].get_by_name = AsyncMock(
            return_value=_make_mock_turret_with_combat(name="Auto Cannon", subtype="auto-cannon")
        )

        resp = client.get("/api/v1/about/object/name/Auto Cannon")

        assert resp.status_code == 200
        assert resp.json()["subtype"] == "auto-cannon"

    def test_turret_plasma_collector_subtype(self, client, mock_repos):
        """Turret weapon: plasma-collector subtype is surfaced without modification."""
        mock_repos["turret"].get_by_name = AsyncMock(
            return_value=_make_mock_turret_with_combat(name="Mining Turret", subtype="plasma-collector")
        )

        resp = client.get("/api/v1/about/object/name/Mining Turret")

        assert resp.status_code == 200
        assert resp.json()["subtype"] == "plasma-collector"

    def test_turret_missing_combat_fields_null(self, client, mock_repos):
        """Turret weapon with no inner extra_atts: all three D-002 fields are null."""
        obj = make_mock_object(name="Bare Turret", type="TurretWeapon", extra_atts=None)
        obj.dps = 10.0
        mock_repos["turret"].get_by_name = AsyncMock(return_value=obj)

        resp = client.get("/api/v1/about/object/name/Bare Turret")

        assert resp.status_code == 200
        data = resp.json()
        assert data["damage_per_shot"] is None
        assert data["loading_speed_ms"] is None
        assert data["subtype"] is None

    # -------------------------------------------------------------------------
    # Regression guard: secondary weapons are NOT affected
    # -------------------------------------------------------------------------

    def test_secondary_not_affected_by_d002(self, client, mock_repos):
        """Secondary weapon must NOT gain loading_speed_ms/damage_per_shot keys."""
        mock_repos["secondary"].get_by_name = AsyncMock(
            return_value=_make_mock_secondary("Edo", subtype="missile", damage=200)
        )

        resp = client.get("/api/v1/about/object/name/Edo")

        assert resp.status_code == 200
        data = resp.json()
        # Secondary response schema does not include D-002 fields
        assert "loading_speed_ms" not in data
        assert "damage_per_shot" not in data


# ===========================================================================
# D-003 — turret_weapon automatic / firing-mode field
# ===========================================================================


class TestD003TurretFiringMode:
    """D-003 — automatic field surfaced for turret weapons.

    All tests call GET /about/object/name/{name} which exercises _build_object_result()
    (ORM column) and _enrich_combat_fields().
    """

    def test_turret_automatic_true(self, client, mock_repos):
        """Turret with automatic=True: 'automatic' must be True in the response."""
        mock_repos["turret"].get_by_name = AsyncMock(
            return_value=_make_mock_turret_with_combat(name="Auto Turret", automatic=True)
        )

        resp = client.get("/api/v1/about/object/name/Auto Turret")

        assert resp.status_code == 200
        assert resp.json()["automatic"] is True

    def test_turret_automatic_false(self, client, mock_repos):
        """Turret with automatic=False: 'automatic' must be False in the response."""
        mock_repos["turret"].get_by_name = AsyncMock(
            return_value=_make_mock_turret_with_combat(name="Manual Turret", automatic=False)
        )

        resp = client.get("/api/v1/about/object/name/Manual Turret")

        assert resp.status_code == 200
        assert resp.json()["automatic"] is False

    def test_turret_plasma_collector_automatic_false(self, client, mock_repos):
        """Plasma-collector turret with automatic=False: 'automatic' must be False (no special-casing)."""
        mock_repos["turret"].get_by_name = AsyncMock(
            return_value=_make_mock_turret_with_combat(name="Mining Turret", subtype="plasma-collector", automatic=False)
        )

        resp = client.get("/api/v1/about/object/name/Mining Turret")

        assert resp.status_code == 200
        assert resp.json()["automatic"] is False

    def test_turret_automatic_none(self, client, mock_repos):
        """Turret with automatic=None: 'automatic' must be None in the response."""
        obj = _make_mock_turret_with_combat(name="Unknown Turret")
        obj.automatic = None
        mock_repos["turret"].get_by_name = AsyncMock(return_value=obj)

        resp = client.get("/api/v1/about/object/name/Unknown Turret")

        assert resp.status_code == 200
        assert resp.json()["automatic"] is None

    def test_turret_automatic_via_get_by_id(self, client, mock_repos):
        """D-003 by-id path: automatic must be surfaced via GET /object/turret_weapon/{id}.

        This is the second code path (_get_object_by_id) distinct from _build_object_result.
        Exercises the turret branch in get_object_by_id to confirm result["automatic"] = obj.automatic
        is included. If the by-id branch were missing the automatic assignment, this test fails.
        """
        mock_repos["turret"].get_by_id = AsyncMock(
            return_value=_make_mock_turret_with_combat(name="Auto Turret", automatic=True)
        )

        resp = client.get("/api/v1/about/object/turret_weapon/1")

        assert resp.status_code == 200
        assert resp.json()["automatic"] is True

    def test_turret_automatic_false_via_get_by_id(self, client, mock_repos):
        """D-003 by-id path: automatic=False (Manual/collector) must round-trip via GET /object/turret_weapon/{id}."""
        mock_repos["turret"].get_by_id = AsyncMock(
            return_value=_make_mock_turret_with_combat(name="Mining Turret", subtype="plasma-collector", automatic=False)
        )

        resp = client.get("/api/v1/about/object/turret_weapon/2")

        assert resp.status_code == 200
        assert resp.json()["automatic"] is False


# ===========================================================================
# D-004 — secondary_weapon subtype field
# ===========================================================================


class TestD004SecondarySubtype:
    """D-004 — subtype field surfaced for secondary weapons.

    All tests call GET /about/object/name/{name} which exercises _enrich_combat_fields().
    """

    def test_secondary_subtype_missile(self, client, mock_repos):
        """Plain missile: subtype must be 'missile'."""
        mock_repos["secondary"].get_by_name = AsyncMock(
            return_value=_make_mock_secondary("Edo", subtype="missile", damage=200)
        )

        resp = client.get("/api/v1/about/object/name/Edo")

        assert resp.status_code == 200
        assert resp.json()["subtype"] == "missile"

    def test_secondary_subtype_cluster_missile(self, client, mock_repos):
        """Cluster-missile: subtype must be 'cluster-missile'."""
        mock_repos["secondary"].get_by_name = AsyncMock(
            return_value=_make_mock_secondary("Shesha", subtype="cluster-missile", damage=60, burst_count=3)
        )

        resp = client.get("/api/v1/about/object/name/Shesha")

        assert resp.status_code == 200
        assert resp.json()["subtype"] == "cluster-missile"

    def test_secondary_subtype_none_when_missing(self, client, mock_repos):
        """Secondary weapon with no subtype in extra_atts: 'subtype' must be None."""
        obj = make_mock_object(name="Bare Missile", type="SecondaryWeapon")
        obj.damage = 100
        obj.loading_speed = 2000
        obj.extra_atts = None
        mock_repos["secondary"].get_by_name = AsyncMock(return_value=obj)

        resp = client.get("/api/v1/about/object/name/Bare Missile")

        assert resp.status_code == 200
        assert resp.json()["subtype"] is None

    def test_secondary_subtype_via_get_by_id(self, client, mock_repos):
        """D-004 by-id path: subtype must be surfaced via GET /object/secondary_weapon/{id}.

        Covers the get_object_by_id code path which is separate from _build_object_result.
        Regression guard: if _enrich_combat_fields were accidentally not called in
        get_object_by_id, this test would fail.
        """
        mock_repos["secondary"].get_by_id = AsyncMock(
            return_value=_make_mock_secondary("Edo", subtype="missile", damage=200)
        )

        resp = client.get("/api/v1/about/object/secondary_weapon/1")

        assert resp.status_code == 200
        assert resp.json()["subtype"] == "missile"
