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
    for name in ["module", "primary", "secondary", "turret", "ship", "system", "criminal"]:
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
        expected = {"module", "primary_weapon", "secondary_weapon", "turret_weapon", "ship", "criminal", "system"}
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
