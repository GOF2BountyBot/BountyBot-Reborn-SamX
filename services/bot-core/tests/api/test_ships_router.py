"""Tests for the ships API router endpoints.

Import path setup and sqlalchemy_utils mocking are handled by
tests/api/conftest.py which runs before this module is loaded.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def make_mock_ship(**overrides):
    """Create a mock ship object with sensible defaults."""
    defaults = dict(
        id=1,
        player_id=1,
        ship_name="Sidewinder",
        nickname=None,
        is_active=False,
        weapons=["Pulse Laser"],
        modules=["Shield Generator"],
        turrets=[],
        created_at=datetime(2026, 1, 1),
    )
    defaults.update(overrides)
    ship = MagicMock()
    for k, v in defaults.items():
        setattr(ship, k, v)
    return ship


def make_mock_player(**overrides):
    """Create a mock player object."""
    defaults = dict(id=1, user_id=12345, guild_id=67890)
    defaults.update(overrides)
    player = MagicMock()
    for k, v in defaults.items():
        setattr(player, k, v)
    return player


@pytest.fixture
def mock_ship_repo():
    repo = AsyncMock()
    repo.get_player_ships = AsyncMock(return_value=[make_mock_ship()])
    repo.get_by_id = AsyncMock(return_value=make_mock_ship())
    repo.create_or_update = AsyncMock(return_value=make_mock_ship())
    repo.get_active_ship = AsyncMock(return_value=make_mock_ship(is_active=True))
    repo.set_active_ship = AsyncMock(return_value=make_mock_ship(is_active=True))
    repo.update_loadout = AsyncMock(return_value=make_mock_ship())
    repo.update_nickname = AsyncMock(return_value=make_mock_ship(nickname="MyShip"))
    repo.add_equipment = AsyncMock(return_value=make_mock_ship())
    repo.remove_equipment = AsyncMock(return_value=make_mock_ship())
    repo.get_ship_loadout_summary = AsyncMock(
        return_value={
            "ship_id": 1,
            "ship_name": "Sidewinder",
            "nickname": None,
            "is_active": False,
            "weapons": ["Pulse Laser"],
            "modules": ["Shield Generator"],
            "turrets": [],
            "weapons_count": 1,
            "modules_count": 1,
            "turrets_count": 0,
        }
    )
    repo.remove = AsyncMock()
    return repo


@pytest.fixture
def mock_player_repo():
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=make_mock_player())
    repo.update_active_ship = AsyncMock()
    return repo


@pytest.fixture
def mock_equipment_service():
    """Mock EquipmentService for router-level tests."""
    svc = AsyncMock()
    svc.equip_item = AsyncMock(return_value={"success": True, "ship": make_mock_ship(), "message": "equipped"})
    svc.unequip_item = AsyncMock(return_value={"success": True, "ship": make_mock_ship(), "message": "unequipped"})
    return svc


@pytest.fixture
def test_app(mock_ship_repo, mock_player_repo, mock_equipment_service):
    app = FastAPI()
    from api.routers.ships import (
        get_equipment_service,
        get_player_repository,
        get_player_ship_repository,
        get_ship_repository,
    )
    from api.routers.ships import router as ships_router

    app.include_router(ships_router, prefix="/api/v1")
    app.dependency_overrides[get_ship_repository] = lambda: mock_ship_repo
    app.dependency_overrides[get_player_repository] = lambda: mock_player_repo
    app.dependency_overrides[get_player_ship_repository] = lambda: mock_ship_repo
    app.dependency_overrides[get_equipment_service] = lambda: mock_equipment_service
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


@pytest.fixture(autouse=True)
def _patch_ships_db(mock_db_session, monkeypatch):
    """Patch get_db_session for all ships router tests automatically."""
    _, mock_cm = mock_db_session
    monkeypatch.setattr("api.routers.ships.get_db_session", lambda: mock_cm)


class TestGetPlayerShips:
    """Tests for GET /ships/player/{player_id}."""

    def test_get_player_ships_happy_path(self, client, mock_ship_repo):
        """Returns list of ships for a player with 200 status."""
        response = client.get("/api/v1/ships/player/1")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == 1
        assert data[0]["player_id"] == 1
        assert data[0]["ship_name"] == "Sidewinder"
        assert data[0]["is_active"] is False
        assert data[0]["weapons"] == ["Pulse Laser"]
        assert data[0]["modules"] == ["Shield Generator"]
        assert data[0]["turrets"] == []
        mock_ship_repo.get_player_ships.assert_called_once()

    def test_get_player_ships_empty_list(self, client, mock_ship_repo):
        """Returns empty list when player has no ships."""
        mock_ship_repo.get_player_ships = AsyncMock(return_value=[])

        response = client.get("/api/v1/ships/player/99")

        assert response.status_code == 200
        data = response.json()
        assert data == []

    def test_get_player_ships_server_error(self, client, mock_ship_repo):
        """Returns 500 when repository raises an unexpected exception."""
        mock_ship_repo.get_player_ships = AsyncMock(side_effect=Exception("DB failure"))

        response = client.get("/api/v1/ships/player/1")

        assert response.status_code == 500
        data = response.json()
        assert "detail" in data


class TestGetShip:
    """Tests for GET /ships/{ship_id}."""

    def test_get_ship_happy_path(self, client, mock_ship_repo):
        """Returns ship data with 200 status when ship exists."""
        response = client.get("/api/v1/ships/1")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 1
        assert data["ship_name"] == "Sidewinder"
        assert data["player_id"] == 1
        assert "created_at" in data
        mock_ship_repo.get_by_id.assert_called_once()

    def test_get_ship_not_found(self, client, mock_ship_repo):
        """Returns 404 when ship does not exist."""
        mock_ship_repo.get_by_id = AsyncMock(return_value=None)

        response = client.get("/api/v1/ships/999")

        assert response.status_code == 404
        data = response.json()
        assert "detail" in data
        assert "999" in data["detail"]

    def test_get_ship_server_error(self, client, mock_ship_repo):
        """Returns 500 when repository raises an unexpected exception."""
        mock_ship_repo.get_by_id = AsyncMock(side_effect=Exception("DB error"))

        response = client.get("/api/v1/ships/1")

        assert response.status_code == 500
        data = response.json()
        assert "detail" in data


class TestCreateShip:
    """Tests for POST /ships/."""

    def test_create_ship_happy_path(self, client, mock_ship_repo, mock_player_repo):
        """Creates ship and returns 201 with ship data."""
        payload = {
            "player_id": 1,
            "ship_name": "Sidewinder",
            "nickname": None,
            "weapons": ["Pulse Laser"],
            "modules": ["Shield Generator"],
            "turrets": [],
        }
        response = client.post("/api/v1/ships/", json=payload)

        assert response.status_code == 201
        data = response.json()
        assert data["id"] == 1
        assert data["ship_name"] == "Sidewinder"
        assert data["player_id"] == 1
        mock_player_repo.get_by_id.assert_called_once()
        mock_ship_repo.create_or_update.assert_called_once()

    def test_create_ship_player_not_found(self, client, mock_ship_repo, mock_player_repo):
        """Returns 404 when specified player does not exist."""
        mock_player_repo.get_by_id = AsyncMock(return_value=None)

        payload = {
            "player_id": 999,
            "ship_name": "Eagle",
            "nickname": None,
            "weapons": [],
            "modules": [],
            "turrets": [],
        }
        response = client.post("/api/v1/ships/", json=payload)

        assert response.status_code == 404
        data = response.json()
        assert "detail" in data
        assert "999" in data["detail"]
        mock_ship_repo.create_or_update.assert_not_called()

    def test_create_ship_server_error(self, client, mock_ship_repo, mock_player_repo):
        """Returns 500 when repository raises an unexpected exception."""
        mock_ship_repo.create_or_update = AsyncMock(side_effect=Exception("DB error"))

        payload = {
            "player_id": 1,
            "ship_name": "Sidewinder",
            "nickname": None,
            "weapons": [],
            "modules": [],
            "turrets": [],
        }
        response = client.post("/api/v1/ships/", json=payload)

        assert response.status_code == 500
        data = response.json()
        assert "detail" in data

    def test_create_ship_validation_missing_required_fields(self, client):
        """Returns 422 when required fields are missing."""
        # Missing ship_name
        payload = {"player_id": 1}
        response = client.post("/api/v1/ships/", json=payload)

        assert response.status_code == 422

    def test_create_ship_validation_missing_player_id(self, client):
        """Returns 422 when player_id is missing."""
        payload = {"ship_name": "Sidewinder"}
        response = client.post("/api/v1/ships/", json=payload)

        assert response.status_code == 422

    def test_create_ship_with_minimal_fields(self, client, mock_ship_repo, mock_player_repo):
        """Creates ship with only required fields; optional fields default correctly."""
        payload = {"player_id": 1, "ship_name": "Cobra"}
        response = client.post("/api/v1/ships/", json=payload)

        assert response.status_code == 201


class TestGetActiveShip:
    """Tests for GET /ships/player/{player_id}/active."""

    def test_get_active_ship_has_active(self, client, mock_ship_repo):
        """Returns the active ship for a player when one exists."""
        response = client.get("/api/v1/ships/player/1/active")

        assert response.status_code == 200
        data = response.json()
        assert data["is_active"] is True
        assert data["id"] == 1
        mock_ship_repo.get_active_ship.assert_called_once()

    def test_get_active_ship_no_active(self, client, mock_ship_repo):
        """Returns null/None when player has no active ship."""
        mock_ship_repo.get_active_ship = AsyncMock(return_value=None)

        response = client.get("/api/v1/ships/player/1/active")

        assert response.status_code == 200
        assert response.json() is None

    def test_get_active_ship_server_error(self, client, mock_ship_repo):
        """Returns 500 when repository raises an unexpected exception."""
        mock_ship_repo.get_active_ship = AsyncMock(side_effect=Exception("DB error"))

        response = client.get("/api/v1/ships/player/1/active")

        assert response.status_code == 500
        data = response.json()
        assert "detail" in data


class TestSetActiveShip:
    """Tests for PUT /ships/{ship_id}/set-active?player_id=X."""

    def test_set_active_ship_happy_path(self, client, mock_ship_repo, mock_player_repo):
        """Sets a ship as active and returns updated ship with 200 status."""
        response = client.put("/api/v1/ships/1/set-active?player_id=1")

        assert response.status_code == 200
        data = response.json()
        assert data["is_active"] is True
        assert data["id"] == 1
        mock_ship_repo.set_active_ship.assert_called_once()
        mock_player_repo.update_active_ship.assert_called_once()

    def test_set_active_ship_value_error_returns_400(self, client, mock_ship_repo, mock_player_repo):
        """Returns 400 when repository raises a ValueError."""
        mock_ship_repo.set_active_ship = AsyncMock(side_effect=ValueError("Ship does not belong to player"))

        response = client.put("/api/v1/ships/1/set-active?player_id=1")

        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "Ship does not belong to player" in data["detail"]

    def test_set_active_ship_server_error(self, client, mock_ship_repo, mock_player_repo):
        """Returns 500 when repository raises an unexpected exception."""
        mock_ship_repo.set_active_ship = AsyncMock(side_effect=Exception("DB failure"))

        response = client.put("/api/v1/ships/1/set-active?player_id=1")

        assert response.status_code == 500
        data = response.json()
        assert "detail" in data

    def test_set_active_ship_missing_player_id_query_param(self, client):
        """Returns 422 when player_id query parameter is missing."""
        response = client.put("/api/v1/ships/1/set-active")

        assert response.status_code == 422


class TestUpdateShipLoadout:
    """Tests for PUT /ships/{ship_id}/loadout."""

    def test_update_ship_loadout_happy_path(self, client, mock_ship_repo):
        """Updates ship loadout and returns updated ship with 200 status."""
        payload = {
            "weapons": ["Burst Laser", "Multi-cannon"],
            "modules": ["Shield Booster"],
            "turrets": ["Turreted Beam"],
        }
        response = client.put("/api/v1/ships/1/loadout", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 1
        mock_ship_repo.update_loadout.assert_called_once()
        # Verify the loadout_updates dict passed to the repo
        call_args = mock_ship_repo.update_loadout.call_args
        loadout_updates = call_args[0][2]  # third positional arg
        assert loadout_updates["weapons"] == ["Burst Laser", "Multi-cannon"]
        assert loadout_updates["modules"] == ["Shield Booster"]
        assert loadout_updates["turrets"] == ["Turreted Beam"]

    def test_update_ship_loadout_partial_update(self, client, mock_ship_repo):
        """Partial updates only include provided fields in the update dict."""
        # Only update weapons, leave modules and turrets as None
        payload = {"weapons": ["Pulse Laser"]}
        response = client.put("/api/v1/ships/1/loadout", json=payload)

        assert response.status_code == 200
        call_args = mock_ship_repo.update_loadout.call_args
        loadout_updates = call_args[0][2]
        assert "weapons" in loadout_updates
        assert "modules" not in loadout_updates
        assert "turrets" not in loadout_updates

    def test_update_ship_loadout_value_error_returns_400(self, client, mock_ship_repo):
        """Returns 400 when repository raises a ValueError."""
        mock_ship_repo.update_loadout = AsyncMock(side_effect=ValueError("Invalid loadout configuration"))

        payload = {"weapons": ["Invalid Weapon"]}
        response = client.put("/api/v1/ships/1/loadout", json=payload)

        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "Invalid loadout configuration" in data["detail"]

    def test_update_ship_loadout_server_error(self, client, mock_ship_repo):
        """Returns 500 when repository raises an unexpected exception."""
        mock_ship_repo.update_loadout = AsyncMock(side_effect=Exception("DB crash"))

        payload = {"weapons": ["Pulse Laser"]}
        response = client.put("/api/v1/ships/1/loadout", json=payload)

        assert response.status_code == 500
        data = response.json()
        assert "detail" in data


class TestUpdateShipNickname:
    """Tests for PUT /ships/{ship_id}/nickname."""

    def test_update_ship_nickname_happy_path(self, client, mock_ship_repo):
        """Updates ship nickname and returns updated ship with 200 status."""
        payload = {"nickname": "MyShip"}
        response = client.put("/api/v1/ships/1/nickname", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 1
        assert data["nickname"] == "MyShip"
        mock_ship_repo.update_nickname.assert_called_once()
        call_args = mock_ship_repo.update_nickname.call_args
        assert call_args[0][2] == "MyShip"

    def test_update_ship_nickname_value_error_returns_400(self, client, mock_ship_repo):
        """Returns 400 when repository raises a ValueError."""
        mock_ship_repo.update_nickname = AsyncMock(side_effect=ValueError("Ship not found"))

        payload = {"nickname": "GhostShip"}
        response = client.put("/api/v1/ships/999/nickname", json=payload)

        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "Ship not found" in data["detail"]

    def test_update_ship_nickname_validation_missing_field(self, client):
        """Returns 422 when nickname field is missing."""
        response = client.put("/api/v1/ships/1/nickname", json={})

        assert response.status_code == 422


class TestEquipItem:
    """Tests for POST /ships/{ship_id}/equip."""

    def test_equip_item_happy_path(self, client, mock_equipment_service):
        """Equips item to ship and returns updated ship with 200 status."""
        payload = {"player_id": 1, "equipment_type": "weapons", "item_name": "Burst Laser"}
        response = client.post("/api/v1/ships/1/equip", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 1
        mock_equipment_service.equip_item.assert_called_once()
        call_kwargs = mock_equipment_service.equip_item.call_args[1]
        assert call_kwargs["equipment_type"] == "weapons"
        assert call_kwargs["item_name"] == "Burst Laser"
        assert call_kwargs["player_id"] == 1

    def test_equip_item_modules_type(self, client, mock_equipment_service):
        """Equips a module type item successfully."""
        payload = {"player_id": 1, "equipment_type": "modules", "item_name": "Shield Booster"}
        response = client.post("/api/v1/ships/1/equip", json=payload)

        assert response.status_code == 200

    def test_equip_item_turrets_type(self, client, mock_equipment_service):
        """Equips a turret type item successfully."""
        payload = {"player_id": 1, "equipment_type": "turrets", "item_name": "Turreted Beam Laser"}
        response = client.post("/api/v1/ships/1/equip", json=payload)

        assert response.status_code == 200

    def test_equip_item_value_error_returns_400(self, client, mock_equipment_service):
        """Returns 400 when equipment service raises a ValueError."""
        mock_equipment_service.equip_item = AsyncMock(side_effect=ValueError("Item already equipped"))

        payload = {"player_id": 1, "equipment_type": "weapons", "item_name": "Pulse Laser"}
        response = client.post("/api/v1/ships/1/equip", json=payload)

        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "Item already equipped" in data["detail"]

    def test_equip_item_invalid_equipment_type_returns_422(self, client):
        """Returns 422 when equipment_type does not match the allowed pattern."""
        payload = {"player_id": 1, "equipment_type": "invalid_type", "item_name": "Some Item"}
        response = client.post("/api/v1/ships/1/equip", json=payload)

        assert response.status_code == 422

    def test_equip_item_missing_item_name_returns_422(self, client):
        """Returns 422 when item_name is missing."""
        payload = {"player_id": 1, "equipment_type": "weapons"}
        response = client.post("/api/v1/ships/1/equip", json=payload)

        assert response.status_code == 422

    def test_equip_item_without_equipment_type_is_accepted(self, client, mock_equipment_service):
        """equipment_type is now optional — request without it should succeed (200)."""
        payload = {"player_id": 1, "item_name": "Pulse Laser"}
        response = client.post("/api/v1/ships/1/equip", json=payload)

        assert response.status_code == 200


class TestUnequipItem:
    """Tests for POST /ships/{ship_id}/unequip."""

    def test_unequip_item_happy_path(self, client, mock_equipment_service):
        """Unequips item from ship and returns updated ship with 200 status."""
        payload = {"player_id": 1, "equipment_type": "weapons", "item_name": "Pulse Laser"}
        response = client.post("/api/v1/ships/1/unequip", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 1
        mock_equipment_service.unequip_item.assert_called_once()
        call_kwargs = mock_equipment_service.unequip_item.call_args[1]
        assert call_kwargs["equipment_type"] == "weapons"
        assert call_kwargs["item_name"] == "Pulse Laser"

    def test_unequip_item_value_error_returns_400(self, client, mock_equipment_service):
        """Returns 400 when equipment service raises a ValueError."""
        mock_equipment_service.unequip_item = AsyncMock(side_effect=ValueError("Item not equipped on ship"))

        payload = {"player_id": 1, "equipment_type": "weapons", "item_name": "Nonexistent Weapon"}
        response = client.post("/api/v1/ships/1/unequip", json=payload)

        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "Item not equipped on ship" in data["detail"]

    def test_unequip_item_invalid_equipment_type_returns_422(self, client):
        """Returns 422 when equipment_type does not match the allowed pattern."""
        payload = {"player_id": 1, "equipment_type": "armour", "item_name": "Reactive Armour"}
        response = client.post("/api/v1/ships/1/unequip", json=payload)

        assert response.status_code == 422

    def test_unequip_item_without_equipment_type_is_accepted(self, client, mock_equipment_service):
        """equipment_type is now optional — request without it should succeed (200)."""
        payload = {"player_id": 1, "item_name": "Pulse Laser"}
        response = client.post("/api/v1/ships/1/unequip", json=payload)

        assert response.status_code == 200

    def test_unequip_item_modules_type(self, client, mock_equipment_service):
        """Unequips a module type item successfully."""
        payload = {"player_id": 1, "equipment_type": "modules", "item_name": "Shield Generator"}
        response = client.post("/api/v1/ships/1/unequip", json=payload)

        assert response.status_code == 200


class TestGetShipLoadout:
    """Tests for GET /ships/{ship_id}/loadout."""

    def test_get_ship_loadout_happy_path(self, client, mock_ship_repo):
        """Returns loadout summary with 200 status."""
        response = client.get("/api/v1/ships/1/loadout")

        assert response.status_code == 200
        data = response.json()
        assert data["ship_id"] == 1
        assert data["ship_name"] == "Sidewinder"
        assert data["nickname"] is None
        assert data["is_active"] is False
        assert data["weapons"] == ["Pulse Laser"]
        assert data["modules"] == ["Shield Generator"]
        assert data["turrets"] == []
        assert data["weapons_count"] == 1
        assert data["modules_count"] == 1
        assert data["turrets_count"] == 0
        mock_ship_repo.get_ship_loadout_summary.assert_called_once()

    def test_get_ship_loadout_not_found_returns_404(self, client, mock_ship_repo):
        """Returns 404 when repository raises a ValueError (ship not found)."""
        mock_ship_repo.get_ship_loadout_summary = AsyncMock(side_effect=ValueError("Ship 999 not found"))

        response = client.get("/api/v1/ships/999/loadout")

        assert response.status_code == 404
        data = response.json()
        assert "detail" in data
        assert "Ship 999 not found" in data["detail"]

    def test_get_ship_loadout_with_nickname(self, client, mock_ship_repo):
        """Returns loadout summary including nickname when ship has one."""
        mock_ship_repo.get_ship_loadout_summary = AsyncMock(
            return_value={
                "ship_id": 2,
                "ship_name": "Cobra MkIII",
                "nickname": "Night Hawk",
                "is_active": True,
                "weapons": ["Multi-cannon", "Plasma Accelerator"],
                "modules": ["Shield Cell Bank", "Fuel Scoop"],
                "turrets": ["Turreted Beam"],
                "weapons_count": 2,
                "modules_count": 2,
                "turrets_count": 1,
            }
        )

        response = client.get("/api/v1/ships/2/loadout")

        assert response.status_code == 200
        data = response.json()
        assert data["nickname"] == "Night Hawk"
        assert data["is_active"] is True
        assert data["weapons_count"] == 2
        assert data["turrets_count"] == 1


class TestDeleteShip:
    """Tests for DELETE /ships/{ship_id}."""

    def test_delete_ship_happy_path(self, client, mock_ship_repo):
        """Deletes an inactive ship and returns success message with 200 status."""
        # Ensure the ship is not active
        mock_ship_repo.get_by_id = AsyncMock(return_value=make_mock_ship(is_active=False))

        response = client.delete("/api/v1/ships/1")

        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "1" in data["message"]
        mock_ship_repo.get_by_id.assert_called_once()
        mock_ship_repo.remove.assert_called_once()

    def test_delete_ship_not_found_returns_404(self, client, mock_ship_repo):
        """Returns 404 when ship does not exist."""
        mock_ship_repo.get_by_id = AsyncMock(return_value=None)

        response = client.delete("/api/v1/ships/999")

        assert response.status_code == 404
        data = response.json()
        assert "detail" in data
        assert "999" in data["detail"]
        mock_ship_repo.remove.assert_not_called()

    def test_delete_active_ship_returns_400(self, client, mock_ship_repo):
        """Returns 400 when attempting to delete an active ship."""
        # Ship is active
        mock_ship_repo.get_by_id = AsyncMock(return_value=make_mock_ship(is_active=True))

        response = client.delete("/api/v1/ships/1")

        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "active" in data["detail"].lower()
        mock_ship_repo.remove.assert_not_called()

    def test_delete_ship_server_error(self, client, mock_ship_repo):
        """Returns 500 when repository raises an unexpected exception during removal."""
        mock_ship_repo.get_by_id = AsyncMock(return_value=make_mock_ship(is_active=False))
        mock_ship_repo.remove = AsyncMock(side_effect=Exception("DB crash"))

        response = client.delete("/api/v1/ships/1")

        assert response.status_code == 500
        data = response.json()
        assert "detail" in data

    def test_delete_ship_calls_remove_with_ship_object(self, client, mock_ship_repo):
        """Verifies that remove is called with the ship object returned by get_by_id."""
        ship_obj = make_mock_ship(id=42, is_active=False)
        mock_ship_repo.get_by_id = AsyncMock(return_value=ship_obj)

        response = client.delete("/api/v1/ships/42")

        assert response.status_code == 200
        # remove should have been called with the ship object
        call_args = mock_ship_repo.remove.call_args
        assert call_args[0][1] is ship_obj


# ===========================================================================
# Additional tests for uncovered error branches
# ===========================================================================


class TestUpdateShipNicknameServerError:
    """Tests for the generic exception branch in update_ship_nickname (lines 310-315)."""

    def test_update_ship_nickname_server_error_returns_500(self, client, mock_ship_repo):
        """Returns 500 when the repo raises an unexpected (non-ValueError) exception.

        Covers lines 310-315: the generic except block in update_ship_nickname.
        """
        mock_ship_repo.update_nickname = AsyncMock(side_effect=RuntimeError("Unexpected DB crash"))

        payload = {"nickname": "GhostShip"}
        response = client.put("/api/v1/ships/1/nickname", json=payload)

        assert response.status_code == 500
        data = response.json()
        assert "detail" in data
        assert "Failed to update ship nickname" in data["detail"]


class TestEquipItemServerError:
    """Tests for the generic exception branch in equip_item."""

    def test_equip_item_server_error_returns_500(self, client, mock_equipment_service):
        """Returns 500 when the equipment service raises an unexpected (non-ValueError) exception."""
        mock_equipment_service.equip_item = AsyncMock(side_effect=RuntimeError("Unexpected equip crash"))

        payload = {"player_id": 1, "equipment_type": "weapons", "item_name": "Burst Laser"}
        response = client.post("/api/v1/ships/1/equip", json=payload)

        assert response.status_code == 500
        data = response.json()
        assert "detail" in data
        assert "Failed to equip item" in data["detail"]


class TestUnequipItemServerError:
    """Tests for the generic exception branch in unequip_item."""

    def test_unequip_item_server_error_returns_500(self, client, mock_equipment_service):
        """Returns 500 when the equipment service raises an unexpected (non-ValueError) exception."""
        mock_equipment_service.unequip_item = AsyncMock(side_effect=RuntimeError("Unexpected unequip crash"))

        payload = {"player_id": 1, "equipment_type": "weapons", "item_name": "Pulse Laser"}
        response = client.post("/api/v1/ships/1/unequip", json=payload)

        assert response.status_code == 500
        data = response.json()
        assert "detail" in data
        assert "Failed to unequip item" in data["detail"]


class TestGetShipLoadoutServerError:
    """Tests for the generic exception branch in get_ship_loadout (lines 425-430)."""

    def test_get_ship_loadout_server_error_returns_500(self, client, mock_ship_repo):
        """Returns 500 when the repo raises an unexpected (non-ValueError) exception.

        Covers lines 425-430: the generic except block in get_ship_loadout.
        """
        mock_ship_repo.get_ship_loadout_summary = AsyncMock(side_effect=RuntimeError("Unexpected loadout crash"))

        response = client.get("/api/v1/ships/1/loadout")

        assert response.status_code == 500
        data = response.json()
        assert "detail" in data
        assert "Failed to get ship loadout" in data["detail"]


# ===========================================================================
# Tests for POST /ships/{ship_id}/equip-check
# ===========================================================================


@pytest.fixture
def mock_equip_check_equipment_service():
    """Mock EquipmentService with equip_check method for equip-check tests."""
    svc = AsyncMock()
    svc.equip_check = AsyncMock(
        return_value={
            "status": "ok",
            "equipment_type": "weapons",
            "item_type": "PrimaryWeapon",
        }
    )
    svc.equip_item = AsyncMock(return_value={"success": True, "ship": make_mock_ship(), "message": "equipped"})
    svc.unequip_item = AsyncMock(return_value={"success": True, "ship": make_mock_ship(), "message": "unequipped"})
    return svc


@pytest.fixture
def equip_check_test_app(mock_ship_repo, mock_player_repo, mock_equip_check_equipment_service):
    app = FastAPI()
    from api.routers.ships import get_equipment_service, get_item_repository, get_player_repository, get_ship_repository
    from api.routers.ships import router as ships_router

    app.include_router(ships_router, prefix="/api/v1")
    app.dependency_overrides[get_ship_repository] = lambda: mock_ship_repo
    app.dependency_overrides[get_player_repository] = lambda: mock_player_repo
    app.dependency_overrides[get_equipment_service] = lambda: mock_equip_check_equipment_service
    app.dependency_overrides[get_item_repository] = lambda: AsyncMock()
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def equip_check_client(equip_check_test_app):
    return TestClient(equip_check_test_app)


@pytest.fixture(autouse=False)
def _patch_equip_check_db(mock_db_session, monkeypatch, equip_check_test_app):
    """Patch get_db_session for equip-check tests."""
    _, mock_cm = mock_db_session
    monkeypatch.setattr("api.routers.ships.get_db_session", lambda: mock_cm)


class TestEquipCheckEndpoint:
    """Tests for POST /ships/{ship_id}/equip-check."""

    @pytest.fixture(autouse=True)
    def patch_db(self, mock_db_session, monkeypatch):
        _, mock_cm = mock_db_session
        monkeypatch.setattr("api.routers.ships.get_db_session", lambda: mock_cm)

    def test_equip_check_ok_status(self, equip_check_client, mock_equip_check_equipment_service):
        """Returns status=ok when item can be equipped."""
        payload = {"player_id": 1, "item_name": "Pulse Laser"}
        response = equip_check_client.post("/api/v1/ships/1/equip-check", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["equipment_type"] == "weapons"
        assert data["item_type"] == "PrimaryWeapon"
        mock_equip_check_equipment_service.equip_check.assert_called_once()

    def test_equip_check_slot_full(self, equip_check_client, mock_equip_check_equipment_service):
        """Returns status=slot_full when all slots are occupied."""
        mock_equip_check_equipment_service.equip_check = AsyncMock(
            return_value={
                "status": "slot_full",
                "equipment_type": "weapons",
                "item_type": "PrimaryWeapon",
                "max_slots": 2,
                "equipped_items": [{"name": "Pulse Laser", "emoji": ""}, {"name": "Burst Laser", "emoji": ""}],
            }
        )
        payload = {"player_id": 1, "item_name": "New Cannon"}
        response = equip_check_client.post("/api/v1/ships/1/equip-check", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "slot_full"
        assert data["max_slots"] == 2
        assert len(data["equipped_items"]) == 2
        assert data["equipped_items"][0]["name"] == "Pulse Laser"

    def test_equip_check_unique_conflict(self, equip_check_client, mock_equip_check_equipment_service):
        """Returns status=unique_conflict when a unique module class is already equipped."""
        mock_equip_check_equipment_service.equip_check = AsyncMock(
            return_value={
                "status": "unique_conflict",
                "equipment_type": "modules",
                "item_type": "ArmourModule",
                "module_class": "ArmourModule",
                "max_equipped": 1,
                "conflicting_item": {"name": "D'iol", "emoji": ""},
            }
        )
        payload = {"player_id": 1, "item_name": "E2 Exoclad"}
        response = equip_check_client.post("/api/v1/ships/1/equip-check", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unique_conflict"
        assert data["module_class"] == "ArmourModule"
        assert data["max_equipped"] == 1
        assert data["conflicting_item"]["name"] == "D'iol"

    def test_equip_check_item_not_found_returns_400(self, equip_check_client, mock_equip_check_equipment_service):
        """Returns 400 when equipment service raises ValueError (item not found)."""
        mock_equip_check_equipment_service.equip_check = AsyncMock(
            side_effect=ValueError("Item 'Unknown' not found in game data")
        )
        payload = {"player_id": 1, "item_name": "Unknown"}
        response = equip_check_client.post("/api/v1/ships/1/equip-check", json=payload)

        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "Unknown" in data["detail"]

    def test_equip_check_server_error_returns_500(self, equip_check_client, mock_equip_check_equipment_service):
        """Returns 500 when equipment service raises an unexpected exception."""
        mock_equip_check_equipment_service.equip_check = AsyncMock(side_effect=RuntimeError("DB crash"))
        payload = {"player_id": 1, "item_name": "Pulse Laser"}
        response = equip_check_client.post("/api/v1/ships/1/equip-check", json=payload)

        assert response.status_code == 500
        data = response.json()
        assert "detail" in data
        assert "equip check" in data["detail"].lower()

    def test_equip_check_missing_player_id_returns_422(self, equip_check_client):
        """Returns 422 when player_id is missing."""
        payload = {"item_name": "Pulse Laser"}
        response = equip_check_client.post("/api/v1/ships/1/equip-check", json=payload)

        assert response.status_code == 422

    def test_equip_check_missing_item_name_returns_422(self, equip_check_client):
        """Returns 422 when item_name is missing."""
        payload = {"player_id": 1}
        response = equip_check_client.post("/api/v1/ships/1/equip-check", json=payload)

        assert response.status_code == 422


class TestEquipItemAutoDetect:
    """Tests for POST /ships/{ship_id}/equip with optional equipment_type."""

    def test_equip_item_without_equipment_type_accepted(self, client, mock_equipment_service):
        """Equip without equipment_type is accepted (optional field)."""
        payload = {"player_id": 1, "item_name": "Pulse Laser"}
        response = client.post("/api/v1/ships/1/equip", json=payload)

        assert response.status_code == 200

    def test_equip_item_with_explicit_equipment_type_still_works(self, client, mock_equipment_service):
        """Equip with explicit equipment_type still works as before."""
        payload = {"player_id": 1, "equipment_type": "weapons", "item_name": "Pulse Laser"}
        response = client.post("/api/v1/ships/1/equip", json=payload)

        assert response.status_code == 200


class TestUnequipItemAutoDetect:
    """Tests for POST /ships/{ship_id}/unequip with optional equipment_type."""

    def test_unequip_item_without_equipment_type_accepted(self, client, mock_equipment_service):
        """Unequip without equipment_type is accepted (optional field)."""
        payload = {"player_id": 1, "item_name": "Pulse Laser"}
        response = client.post("/api/v1/ships/1/unequip", json=payload)

        assert response.status_code == 200

    def test_unequip_item_with_explicit_equipment_type_still_works(self, client, mock_equipment_service):
        """Unequip with explicit equipment_type still works as before."""
        payload = {"player_id": 1, "equipment_type": "weapons", "item_name": "Pulse Laser"}
        response = client.post("/api/v1/ships/1/unequip", json=payload)

        assert response.status_code == 200
