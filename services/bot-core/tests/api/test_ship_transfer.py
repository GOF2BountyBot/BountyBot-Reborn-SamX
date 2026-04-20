"""Tests for the ship transfer endpoint: POST /ships/transfer.

Import path setup and sqlalchemy_utils mocking are handled by
tests/api/conftest.py which runs before this module is loaded.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def make_mock_player_ship(**overrides):
    defaults = dict(
        id=42,
        player_id=10,
        ship_name="Sidewinder",
        nickname=None,
        is_active=False,
        weapons=["Pulse Laser"],
        modules=["Shield Gen"],
        turrets=[],
        created_at=datetime(2026, 1, 1),
    )
    defaults.update(overrides)
    ship = MagicMock()
    for k, v in defaults.items():
        setattr(ship, k, v)
    return ship


def make_mock_player(**overrides):
    defaults = dict(id=10, user_id=1, guild_id=67890, credits=1000)
    defaults.update(overrides)
    player = MagicMock()
    for k, v in defaults.items():
        setattr(player, k, v)
    return player


def _configure_db_mock(mock_get_db):
    mock_session = AsyncMock()
    mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_session


@pytest.fixture
def mock_player_ship_repo():
    repo = AsyncMock()
    ship = make_mock_player_ship()
    repo.get_by_id = AsyncMock(return_value=ship)
    return repo


@pytest.fixture
def mock_player_repo():
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=make_mock_player())
    return repo


@pytest.fixture
def test_app(mock_player_ship_repo, mock_player_repo):
    app = FastAPI()
    from api.routers.ships import (
        get_player_repository,
        get_player_ship_repository,
    )
    from api.routers.ships import router as ships_router

    app.include_router(ships_router, prefix="/api/v1")
    app.dependency_overrides[get_player_ship_repository] = lambda: mock_player_ship_repo
    app.dependency_overrides[get_player_repository] = lambda: mock_player_repo
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


class TestShipTransfer:
    """Tests for POST /api/v1/ships/transfer."""

    @patch("api.routers.ships.InventoryRepository")
    @patch("api.routers.ships.get_db_session")
    def test_transfer_ship_success(
        self, mock_get_db, mock_inv_repo_cls, client, mock_player_ship_repo, mock_player_repo
    ):
        """Returns 200 with transfer details on success.

        Verifies: ship exists, belongs to from_player, is not active, items returned.
        """
        mock_session = _configure_db_mock(mock_get_db)
        # from_player is player 10, to_player is player 20
        mock_player_repo.get_by_id.side_effect = [
            make_mock_player(id=10),
            make_mock_player(id=20),
        ]
        # Ship belongs to from_player (10) and is NOT active
        ship = make_mock_player_ship(player_id=10, is_active=False)
        mock_player_ship_repo.get_by_id = AsyncMock(return_value=ship)

        mock_inv_repo = AsyncMock()
        mock_inv_repo.add_item = AsyncMock()
        mock_inv_repo_cls.return_value = mock_inv_repo

        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock()

        payload = {"from_player_id": 10, "to_player_id": 20, "ship_id": 42}
        resp = client.post("/api/v1/ships/transfer", json=payload)

        assert resp.status_code == 200
        data = resp.json()
        assert data["ship_id"] == 42
        assert data["from_player_id"] == 10
        assert data["to_player_id"] == 20
        assert "items_returned_to_source" in data
        assert "Pulse Laser" in data["items_returned_to_source"]
        assert "Shield Gen" in data["items_returned_to_source"]

    @patch("api.routers.ships.InventoryRepository")
    @patch("api.routers.ships.get_db_session")
    def test_transfer_ship_from_player_not_found(self, mock_get_db, mock_inv_repo_cls, client, mock_player_repo):
        """Returns 404 when from_player does not exist."""
        _configure_db_mock(mock_get_db)
        mock_player_repo.get_by_id = AsyncMock(return_value=None)
        mock_inv_repo_cls.return_value = AsyncMock()

        payload = {"from_player_id": 999, "to_player_id": 20, "ship_id": 42}
        resp = client.post("/api/v1/ships/transfer", json=payload)
        assert resp.status_code == 404

    @patch("api.routers.ships.InventoryRepository")
    @patch("api.routers.ships.get_db_session")
    def test_transfer_ship_to_player_not_found(self, mock_get_db, mock_inv_repo_cls, client, mock_player_repo):
        """Returns 404 when to_player does not exist."""
        _configure_db_mock(mock_get_db)
        mock_player_repo.get_by_id.side_effect = [
            make_mock_player(id=10),
            None,  # to_player not found
        ]
        mock_inv_repo_cls.return_value = AsyncMock()

        payload = {"from_player_id": 10, "to_player_id": 999, "ship_id": 42}
        resp = client.post("/api/v1/ships/transfer", json=payload)
        assert resp.status_code == 404

    @patch("api.routers.ships.InventoryRepository")
    @patch("api.routers.ships.get_db_session")
    def test_transfer_ship_not_found(
        self, mock_get_db, mock_inv_repo_cls, client, mock_player_ship_repo, mock_player_repo
    ):
        """Returns 404 when the ship does not exist."""
        _configure_db_mock(mock_get_db)
        mock_player_repo.get_by_id.side_effect = [
            make_mock_player(id=10),
            make_mock_player(id=20),
        ]
        mock_player_ship_repo.get_by_id = AsyncMock(return_value=None)
        mock_inv_repo_cls.return_value = AsyncMock()

        payload = {"from_player_id": 10, "to_player_id": 20, "ship_id": 9999}
        resp = client.post("/api/v1/ships/transfer", json=payload)
        assert resp.status_code == 404

    @patch("api.routers.ships.InventoryRepository")
    @patch("api.routers.ships.get_db_session")
    def test_transfer_ship_not_owned_by_from_player(
        self, mock_get_db, mock_inv_repo_cls, client, mock_player_ship_repo, mock_player_repo
    ):
        """Returns 400 when the ship belongs to a different player."""
        _configure_db_mock(mock_get_db)
        mock_player_repo.get_by_id.side_effect = [
            make_mock_player(id=10),
            make_mock_player(id=20),
        ]
        # Ship belongs to player 99, not player 10
        ship = make_mock_player_ship(player_id=99, is_active=False)
        mock_player_ship_repo.get_by_id = AsyncMock(return_value=ship)
        mock_inv_repo_cls.return_value = AsyncMock()

        payload = {"from_player_id": 10, "to_player_id": 20, "ship_id": 42}
        resp = client.post("/api/v1/ships/transfer", json=payload)
        assert resp.status_code == 400
        assert "does not belong" in resp.json()["detail"]

    @patch("api.routers.ships.InventoryRepository")
    @patch("api.routers.ships.get_db_session")
    def test_transfer_ship_active_ship_blocked(
        self, mock_get_db, mock_inv_repo_cls, client, mock_player_ship_repo, mock_player_repo
    ):
        """Returns 400 when trying to transfer the active ship."""
        _configure_db_mock(mock_get_db)
        mock_player_repo.get_by_id.side_effect = [
            make_mock_player(id=10),
            make_mock_player(id=20),
        ]
        # Ship IS active
        ship = make_mock_player_ship(player_id=10, is_active=True)
        mock_player_ship_repo.get_by_id = AsyncMock(return_value=ship)
        mock_inv_repo_cls.return_value = AsyncMock()

        payload = {"from_player_id": 10, "to_player_id": 20, "ship_id": 42}
        resp = client.post("/api/v1/ships/transfer", json=payload)
        assert resp.status_code == 400
        assert "active" in resp.json()["detail"].lower()

    @patch("api.routers.ships.InventoryRepository")
    @patch("api.routers.ships.get_db_session")
    def test_transfer_ship_empty_loadout(
        self, mock_get_db, mock_inv_repo_cls, client, mock_player_ship_repo, mock_player_repo
    ):
        """Returns 200 with empty items_returned_to_source when ship has no loadout."""
        mock_session = _configure_db_mock(mock_get_db)
        mock_player_repo.get_by_id.side_effect = [
            make_mock_player(id=10),
            make_mock_player(id=20),
        ]
        ship = make_mock_player_ship(player_id=10, is_active=False, weapons=[], modules=[], turrets=[])
        mock_player_ship_repo.get_by_id = AsyncMock(return_value=ship)

        mock_inv_repo = AsyncMock()
        mock_inv_repo.add_item = AsyncMock()
        mock_inv_repo_cls.return_value = mock_inv_repo

        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock()

        payload = {"from_player_id": 10, "to_player_id": 20, "ship_id": 42}
        resp = client.post("/api/v1/ships/transfer", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["items_returned_to_source"] == []

    def test_transfer_ship_schema_validation(self, client):
        """Returns 422 when required fields are missing."""
        resp = client.post("/api/v1/ships/transfer", json={"from_player_id": 10})
        assert resp.status_code == 422
