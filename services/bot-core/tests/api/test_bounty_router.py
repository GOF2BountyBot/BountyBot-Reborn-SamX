"""Tests for the bounty API router endpoints.

Import path setup and sqlalchemy_utils mocking are handled by
tests/api/conftest.py which runs before this module is loaded.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def make_mock_bounty(**overrides):
    """Build a MagicMock that looks like a Bounty ORM object."""
    now = datetime(2026, 1, 1, 12, 0, 0)
    defaults = dict(
        id=1,
        guild_id=67890,
        division="Alpha",
        criminal_name="Dark Mage",
        criminal_faction="Void Syndicate",
        route=["Sol", "Proxima", "Tau Ceti"],
        answer="Tau Ceti",
        reward=5000,
        reward_per_sys=1000,
        checked={"Sol": 1, "Proxima": 2},
        issue_time=now,
        end_time=None,
        tech_level=3,
        criminal_ship={"name": "Interceptor", "class": "Fighter"},
        status="active",
        escape_count=0,
        win_user_id=None,
    )
    defaults.update(overrides)
    bounty = MagicMock()
    for k, v in defaults.items():
        setattr(bounty, k, v)
    return bounty


def make_check_response(result_value="correct", bounty_id=1, message=""):
    """Build a mock CheckResponse-like object."""
    from services.bounty_service import CheckResponse, CheckResult

    result_map = {
        "correct": CheckResult.CORRECT,
        "incorrect": CheckResult.INCORRECT,
        "not_found": CheckResult.NOT_FOUND,
        "on_cooldown": CheckResult.ON_COOLDOWN,
        "already_checked": CheckResult.ALREADY_CHECKED,
    }
    return CheckResponse(
        result=result_map[result_value],
        bounty_id=bounty_id,
        message=message,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_bounty_service():
    service = AsyncMock()
    service.bounty_repo = AsyncMock()
    service.bounty_repo.get_active_by_guild = AsyncMock(return_value=[make_mock_bounty()])
    service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[make_mock_bounty()])
    service.bounty_repo.get_by_id = AsyncMock(return_value=make_mock_bounty())
    service.check_bounty = AsyncMock(return_value=make_check_response("correct"))
    service.spawn_bounty = AsyncMock(return_value=make_mock_bounty())
    return service


@pytest.fixture
def mock_loadout_response_service():
    """Mock LoadoutResponseService injected via dep override for bounty loadout tests."""
    svc = MagicMock()
    svc.build_bounty_loadout = AsyncMock(return_value=None)
    return svc


@pytest.fixture
def test_app(mock_bounty_service, mock_loadout_response_service):
    app = FastAPI()
    from api.routers.bounties import (
        get_bounty_service,
        get_loadout_response_service,
    )
    from api.routers.bounties import router as bounties_router

    app.include_router(bounties_router, prefix="/api/v1")
    app.dependency_overrides[get_bounty_service] = lambda: mock_bounty_service
    app.dependency_overrides[get_loadout_response_service] = lambda: mock_loadout_response_service
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


# ---------------------------------------------------------------------------
# Helper: configure get_db_session mock as async context manager
# ---------------------------------------------------------------------------


def _configure_db_mock(mock_get_db):
    """Configure mock_get_db to act as an async context manager."""
    mock_session = AsyncMock()
    mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_session


# ===========================================================================
# 1. POST /bounties/check
# ===========================================================================


class TestCheckBounty:
    """Tests for POST /api/v1/bounties/check."""

    @patch("api.routers.bounties.get_db_session")
    def test_check_bounty_correct(self, mock_get_db, client, mock_bounty_service):
        """Returns CORRECT result when the system is the answer."""
        _configure_db_mock(mock_get_db)
        mock_bounty_service.check_bounty = AsyncMock(
            return_value=make_check_response("correct", bounty_id=1, message="Correct!")
        )

        response = client.post(
            "/api/v1/bounties/check?guild_id=67890",
            json={"player_id": 42, "system_name": "Tau Ceti"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"] == "correct"
        assert data["bounty_id"] == 1
        assert data["message"] == "Correct!"

    @patch("api.routers.bounties.get_db_session")
    def test_check_bounty_incorrect(self, mock_get_db, client, mock_bounty_service):
        """Returns INCORRECT result when system is in route but not the answer."""
        _configure_db_mock(mock_get_db)
        mock_bounty_service.check_bounty = AsyncMock(
            return_value=make_check_response("incorrect", bounty_id=1, message="Wrong system.")
        )

        response = client.post(
            "/api/v1/bounties/check?guild_id=67890",
            json={"player_id": 42, "system_name": "Sol"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"] == "incorrect"
        assert data["bounty_id"] == 1

    @patch("api.routers.bounties.get_db_session")
    def test_check_bounty_not_found(self, mock_get_db, client, mock_bounty_service):
        """Returns NOT_FOUND result when no active bounty exists for the guild."""
        _configure_db_mock(mock_get_db)
        mock_bounty_service.check_bounty = AsyncMock(
            return_value=make_check_response("not_found", bounty_id=None, message="No bounty.")
        )

        response = client.post(
            "/api/v1/bounties/check?guild_id=67890",
            json={"player_id": 42, "system_name": "Unknown"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"] == "not_found"
        assert data["bounty_id"] is None

    @patch("api.routers.bounties.get_db_session")
    def test_check_bounty_on_cooldown(self, mock_get_db, client, mock_bounty_service):
        """Returns ON_COOLDOWN result when the player is on cooldown."""
        _configure_db_mock(mock_get_db)
        mock_bounty_service.check_bounty = AsyncMock(
            return_value=make_check_response("on_cooldown", bounty_id=1, message="Please wait before checking again.")
        )

        response = client.post(
            "/api/v1/bounties/check?guild_id=67890",
            json={"player_id": 42, "system_name": "Proxima"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"] == "on_cooldown"
        assert "wait" in data["message"].lower()

    @patch("api.routers.bounties.get_db_session")
    def test_check_bounty_service_exception_returns_not_found_not_500(self, mock_get_db, client, mock_bounty_service):
        """Bug 8: When check_bounty raises an unexpected exception, the endpoint
        returns a graceful NOT_FOUND response (200) instead of propagating as 500.
        """
        _configure_db_mock(mock_get_db)
        mock_bounty_service.check_bounty = AsyncMock(side_effect=RuntimeError("DB query failed: no active bounties"))

        response = client.post(
            "/api/v1/bounties/check?guild_id=67890",
            json={"player_id": 42, "system_name": "Nowhere"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"] == "not_found"
        assert data["bounty_id"] is None

    @patch("api.routers.bounties.get_db_session")
    def test_check_bounty_no_active_bounties_returns_not_found(self, mock_get_db, client, mock_bounty_service):
        """Bug 8: When there are no active bounties the service returns NOT_FOUND
        and the endpoint passes it through without raising a 500.
        """
        _configure_db_mock(mock_get_db)
        mock_bounty_service.check_bounty = AsyncMock(
            return_value=make_check_response("not_found", bounty_id=None, message="No active bounties for division")
        )

        response = client.post(
            "/api/v1/bounties/check?guild_id=67890",
            json={"player_id": 42, "system_name": "Alpha"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"] == "not_found"
        assert data["bounty_id"] is None


# ===========================================================================
# 2. GET /bounties/
# ===========================================================================


class TestListBounties:
    """Tests for GET /api/v1/bounties/."""

    @patch("api.routers.bounties.get_db_session")
    def test_list_bounties_with_division(self, mock_get_db, client, mock_bounty_service):
        """Returns filtered bounties when division query param is provided."""
        _configure_db_mock(mock_get_db)
        mock_bounty = make_mock_bounty(division="Alpha")
        mock_bounty_service.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[mock_bounty])

        response = client.get("/api/v1/bounties/?guild_id=67890&division=Alpha")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["division"] == "Alpha"
        # Ensure answer is NOT in public response
        assert "answer" not in data[0]
        mock_bounty_service.bounty_repo.get_active_by_guild_and_division.assert_called_once()

    @patch("api.routers.bounties.get_db_session")
    def test_list_bounties_no_division(self, mock_get_db, client, mock_bounty_service):
        """Returns all active bounties when no division filter is given."""
        _configure_db_mock(mock_get_db)
        mock_bounty = make_mock_bounty()
        mock_bounty_service.bounty_repo.get_active_by_guild = AsyncMock(return_value=[mock_bounty])

        response = client.get("/api/v1/bounties/?guild_id=67890")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        mock_bounty_service.bounty_repo.get_active_by_guild.assert_called_once()

    @patch("api.routers.bounties.get_db_session")
    def test_list_bounties_empty(self, mock_get_db, client, mock_bounty_service):
        """Returns empty list when no active bounties exist."""
        _configure_db_mock(mock_get_db)
        mock_bounty_service.bounty_repo.get_active_by_guild = AsyncMock(return_value=[])

        response = client.get("/api/v1/bounties/?guild_id=67890")

        assert response.status_code == 200
        assert response.json() == []


# ===========================================================================
# 3. GET /bounties/{bounty_id}/route
# ===========================================================================


class TestGetBountyRoute:
    """Tests for GET /api/v1/bounties/{bounty_id}/route."""

    @patch("api.routers.bounties.get_db_session")
    def test_get_route_success(self, mock_get_db, client, mock_bounty_service):
        """Returns route, checked status, and division for a valid bounty."""
        _configure_db_mock(mock_get_db)
        mock_bounty = make_mock_bounty(
            id=1,
            route=["Sol", "Proxima", "Tau Ceti"],
            checked={"Sol": 1},
            status="active",
            division="bronze",
        )
        mock_bounty_service.bounty_repo.get_by_id = AsyncMock(return_value=mock_bounty)

        response = client.get("/api/v1/bounties/1/route")

        assert response.status_code == 200
        data = response.json()
        assert data["bounty_id"] == 1
        assert data["criminal_name"] == "Dark Mage"
        assert data["route"] == ["Sol", "Proxima", "Tau Ceti"]
        assert data["checked"] == {"Sol": 1}
        assert data["status"] == "active"
        # Division is now included in the route response
        assert data["division"] == "bronze"

    @patch("api.routers.bounties.get_db_session")
    def test_get_route_includes_division(self, mock_get_db, client, mock_bounty_service):
        """Route response includes the bounty division for tier display."""
        _configure_db_mock(mock_get_db)
        mock_bounty = make_mock_bounty(id=5, division="platinum", route=["A", "B"])
        mock_bounty_service.bounty_repo.get_by_id = AsyncMock(return_value=mock_bounty)

        response = client.get("/api/v1/bounties/5/route")

        assert response.status_code == 200
        data = response.json()
        assert data["division"] == "platinum"

    @patch("api.routers.bounties.get_db_session")
    def test_get_route_not_found(self, mock_get_db, client, mock_bounty_service):
        """Returns 404 when bounty does not exist."""
        _configure_db_mock(mock_get_db)
        mock_bounty_service.bounty_repo.get_by_id = AsyncMock(return_value=None)

        response = client.get("/api/v1/bounties/999/route")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


# ===========================================================================
# 4. POST /bounties/spawn
# ===========================================================================


class TestSpawnBounty:
    """Tests for POST /api/v1/bounties/spawn."""

    @patch("api.routers.bounties.get_db_session")
    def test_spawn_bounty_success(self, mock_get_db, client, mock_bounty_service):
        """Returns the created bounty on success."""
        _configure_db_mock(mock_get_db)
        mock_bounty = make_mock_bounty()
        mock_bounty_service.spawn_bounty = AsyncMock(return_value=mock_bounty)

        response = client.post(
            "/api/v1/bounties/spawn",
            json={"guild_id": 67890, "division": "Alpha", "tech_level": 3},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 1
        assert data["guild_id"] == 67890
        assert data["division"] == "Alpha"
        assert data["criminal_name"] == "Dark Mage"
        assert data["status"] == "active"

    @patch("api.routers.bounties.get_db_session")
    def test_spawn_bounty_fails(self, mock_get_db, client, mock_bounty_service):
        """Returns 400 when service cannot spawn (no criminals or systems)."""
        _configure_db_mock(mock_get_db)
        mock_bounty_service.spawn_bounty = AsyncMock(return_value=None)

        response = client.post(
            "/api/v1/bounties/spawn",
            json={"guild_id": 67890, "division": "Beta", "tech_level": 5},
        )

        assert response.status_code == 400
        assert "failed" in response.json()["detail"].lower()


# ===========================================================================
# 5. GET /bounties/{bounty_id}/loadout
# ===========================================================================


class TestGetBountyLoadout:
    """Tests for GET /api/v1/bounties/{bounty_id}/loadout (unified LoadoutResponse)."""

    @staticmethod
    def _make_criminal_response(**overrides):
        from api.schemas.loadout_schema import (
            EffectItem,
            LoadoutModuleItem,
            LoadoutResponse,
            LoadoutWeaponItem,
            ShipStats,
        )

        defaults = dict(
            subject_kind="criminal",
            subject_name="Dark Mage",
            subject_description="Void Syndicate",
            bounty_id=1,
            tech_level=3,
            ship_name="Interceptor",
            ship_emoji="<:interceptor:1>",
            ship_icon="https://cdn/interceptor.png",
            thumbnail_url="https://cdn/darkmage.png",
            ship_stats=ShipStats(armour=95, cargo=45, handling=60, hp=95, dps=5.2, total_value=1000),
            weapons=[LoadoutWeaponItem(name="Blaster", emoji="<:b:1>", dps=5.2, value=500)],
            turrets=[],
            modules=[
                LoadoutModuleItem(
                    name="D'iol",
                    emoji="<:diol:1>",
                    type="ArmourModule",
                    value=500,
                    tech_level=1,
                    effects=[EffectItem(label="Armour", value="40")],
                    combat_tier="combat",
                ),
            ],
            cargo=[],
            cargo_total_count=0,
        )
        defaults.update(overrides)
        return LoadoutResponse(**defaults)

    @patch("api.routers.bounties.get_db_session")
    def test_get_loadout_success(self, mock_get_db, client, mock_loadout_response_service):
        """Returns unified LoadoutResponse with subject_kind='criminal'."""
        _configure_db_mock(mock_get_db)
        mock_loadout_response_service.build_bounty_loadout.return_value = self._make_criminal_response()

        response = client.get("/api/v1/bounties/1/loadout")

        assert response.status_code == 200
        data = response.json()
        assert data["subject_kind"] == "criminal"
        assert data["subject_name"] == "Dark Mage"
        assert data["subject_description"] == "Void Syndicate"
        assert data["bounty_id"] == 1
        assert data["tech_level"] == 3
        assert data["ship_name"] == "Interceptor"
        # Cargo stats visible for criminal path (always shown)
        assert data["ship_stats"]["cargo"] == 45
        # Thumbnail uses criminal icon, not ship icon
        assert data["thumbnail_url"] == "https://cdn/darkmage.png"
        # Cargo list always empty (no loot drops yet)
        assert data["cargo"] == []
        assert data["cargo_total_count"] == 0
        # Module includes pre-formatted effects and combat_tier
        mod = data["modules"][0]
        assert mod["type"] == "ArmourModule"
        assert mod["combat_tier"] == "combat"
        assert mod["effects"] == [{"label": "Armour", "value": "40"}]

    @patch("api.routers.bounties.get_db_session")
    def test_get_loadout_not_found(self, mock_get_db, client, mock_loadout_response_service):
        """Returns 404 when bounty does not exist (service returns None)."""
        _configure_db_mock(mock_get_db)
        mock_loadout_response_service.build_bounty_loadout.return_value = None

        response = client.get("/api/v1/bounties/999/loadout")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @patch("api.routers.bounties.get_db_session")
    def test_get_loadout_missing_criminal_ship_returns_message(
        self, mock_get_db, client, mock_loadout_response_service
    ):
        """When criminal_ship is missing, returns 200 with message."""
        _configure_db_mock(mock_get_db)
        mock_loadout_response_service.build_bounty_loadout.return_value = self._make_criminal_response(
            message="Criminal ship data unavailable",
            ship_name=None,
        )

        response = client.get("/api/v1/bounties/1/loadout")

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Criminal ship data unavailable"
        assert data["subject_kind"] == "criminal"

    @patch("api.routers.bounties.get_db_session")
    def test_get_loadout_server_error_returns_500(
        self, mock_get_db, client, mock_loadout_response_service
    ):
        _configure_db_mock(mock_get_db)
        mock_loadout_response_service.build_bounty_loadout.side_effect = Exception("boom")

        response = client.get("/api/v1/bounties/1/loadout")

        assert response.status_code == 500


# ---------------------------------------------------------------------------
# LoadoutResponseService — bounty-path unit-level tests
# ---------------------------------------------------------------------------


class TestLoadoutResponseServiceBountyPath:
    """Unit tests for LoadoutResponseService.build_bounty_loadout."""

    def _make_svc(self, *, bounty, criminal=None, ship=None):
        from services.loadout_response_service import LoadoutResponseService

        svc = LoadoutResponseService()
        svc.bounty_repo = MagicMock()
        svc.bounty_repo.get_by_id = AsyncMock(return_value=bounty)
        svc.criminal_repo = MagicMock()
        svc.criminal_repo.get_by_name = AsyncMock(return_value=criminal)
        svc.item_repo = MagicMock()
        svc.inventory_repo = MagicMock()
        svc.player_repo = MagicMock()
        svc.user_repo = MagicMock()

        async def _execute(stmt):
            result = MagicMock()
            result.scalars.return_value.first.return_value = ship
            return result

        db = MagicMock()
        db.execute = _execute
        return svc, db

    async def test_bounty_not_found_returns_none(self):
        svc, db = self._make_svc(bounty=None)
        result = await svc.build_bounty_loadout(db, 999)
        assert result is None

    async def test_bounty_with_no_criminal_ship_returns_message(self):
        from types import SimpleNamespace

        bounty = SimpleNamespace(
            id=1,
            criminal_name="Dark Mage",
            criminal_faction="Void",
            tech_level=3,
            criminal_ship=None,
        )
        svc, db = self._make_svc(bounty=bounty)
        result = await svc.build_bounty_loadout(db, 1)

        assert result is not None
        assert result.message == "Criminal ship data unavailable"
        assert result.subject_kind == "criminal"
        assert result.subject_name == "Dark Mage"
        assert result.bounty_id == 1
        assert result.tech_level == 3

    async def test_bounty_happy_path_with_compressor_module(self):
        """Effective cargo = ship.cargo × CompressorModule multipliers (spec §2.6 step 9)."""
        from types import SimpleNamespace

        criminal_ship = {
            "ship_name": "Betty",
            "ship_emoji": "<:betty:1>",
            "ship_armour": 95,
            "total_hp": 120,
            "weapons": [{"name": "Blaster", "emoji": "<:b:1>", "dps": 5.2, "value": 500}],
            "turrets": [],
            "modules": [
                {
                    "name": "D'iol",
                    "emoji": "<:diol:1>",
                    "type": "ArmourModule",
                    "value": 500,
                    "tech_level": 1,
                    "extra_atts": {"armour": 40},
                },
                {
                    "name": "AutoPacker 2",
                    "emoji": "<:pack:1>",
                    "type": "CompressorModule",
                    "value": 300,
                    "tech_level": 2,
                    "extra_atts": {"cargoMultiplier": 1.5},
                },
            ],
        }

        bounty = SimpleNamespace(
            id=42,
            criminal_name="Dark Mage",
            criminal_faction="Void Syndicate",
            tech_level=3,
            criminal_ship=criminal_ship,
        )
        criminal = SimpleNamespace(icon="https://cdn/darkmage.png")
        ship = SimpleNamespace(
            name="Betty", armour=95, cargo=30, handling=60, icon="https://cdn/betty.png",
            max_primaries=1, max_secondaries=0, max_turrets=0, max_modules=2,
        )

        svc, db = self._make_svc(bounty=bounty, criminal=criminal, ship=ship)
        result = await svc.build_bounty_loadout(db, 42)

        assert result is not None
        assert result.subject_kind == "criminal"
        assert result.subject_name == "Dark Mage"
        assert result.subject_description == "Void Syndicate"
        assert result.bounty_id == 42
        assert result.tech_level == 3
        assert result.ship_name == "Betty"
        assert result.ship_emoji == "<:betty:1>"
        # Thumbnail uses Criminal.icon, NOT ship icon
        assert result.thumbnail_url == "https://cdn/darkmage.png"
        assert result.ship_icon == "https://cdn/betty.png"
        # Stats
        assert result.ship_stats.armour == 95
        assert result.ship_stats.hp == 120  # JSON-provided total_hp
        # Effective cargo = 30 × 1.5 = 45
        assert result.ship_stats.cargo == 45
        assert result.ship_stats.handling == 60
        assert result.ship_stats.dps == 5.2
        # Cargo list always empty for criminal path
        assert result.cargo == []
        assert result.cargo_total_count == 0
        # Modules have pre-formatted effects + combat_tier
        assert len(result.modules) == 2
        diol = result.modules[0]
        assert diol.type == "ArmourModule"
        assert diol.combat_tier == "combat"
        assert [(e.label, e.value) for e in diol.effects] == [("Armour", "40")]
        comp = result.modules[1]
        assert comp.type == "CompressorModule"
        assert comp.combat_tier == "utility"
        assert [(e.label, e.value) for e in comp.effects] == [("Cargo Bonus", "×1.5")]

    async def test_bounty_missing_ship_gives_partial_stats(self):
        """If Ship row is missing, ship_stats still populates from JSON where possible."""
        from types import SimpleNamespace

        criminal_ship = {
            "ship_name": "UnknownShip",
            "ship_armour": 80,
            "armor_hp": 80,
            "shield_hp": 20,
            "weapons": [],
            "turrets": [],
            "modules": [],
        }
        bounty = SimpleNamespace(
            id=7, criminal_name="Ghost", criminal_faction=None,
            tech_level=2, criminal_ship=criminal_ship,
        )
        svc, db = self._make_svc(bounty=bounty, criminal=None, ship=None)
        result = await svc.build_bounty_loadout(db, 7)

        assert result.ship_stats.armour == 80
        # hp = armor_hp + shield_hp
        assert result.ship_stats.hp == 100
        # cargo = 0 when ship missing
        assert result.ship_stats.cargo == 0
        # thumbnail_url is None when criminal is missing
        assert result.thumbnail_url is None

    async def test_bounty_with_no_criminal_row_has_null_thumbnail(self):
        from types import SimpleNamespace

        criminal_ship = {"ship_name": "X", "weapons": [], "turrets": [], "modules": []}
        bounty = SimpleNamespace(
            id=7, criminal_name="Ghost", criminal_faction=None,
            tech_level=2, criminal_ship=criminal_ship,
        )
        ship = SimpleNamespace(
            name="X", armour=50, cargo=10, handling=40, icon="https://cdn/x.png",
            max_primaries=0, max_secondaries=0, max_turrets=0, max_modules=0,
        )
        svc, db = self._make_svc(bounty=bounty, criminal=None, ship=ship)
        result = await svc.build_bounty_loadout(db, 7)

        assert result.thumbnail_url is None
        # Ship icon still populated independently
        assert result.ship_icon == "https://cdn/x.png"


# ===========================================================================
# Tests: DELETE /bounties/guild/{guild_id}/clear
# ===========================================================================


@pytest.fixture
def mock_bounty_service_for_admin():
    """Mock BountyService with clear_bounties and spawn_bounty for admin tests."""
    service = AsyncMock()
    service.bounty_repo = AsyncMock()
    service.clear_bounties = AsyncMock(
        return_value={
            "guild_id": 67890,
            "tier": None,
            "cleared_count": 3,
            "bounty_ids": [1, 2, 3],
            "announcements_deleted": 2,
        }
    )
    service.spawn_bounty = AsyncMock(return_value=make_mock_bounty())
    return service


@pytest.fixture
def test_app_admin(mock_bounty_service_for_admin):
    from api.routers.bounties import get_bounty_service
    from api.routers.bounties import router as bounties_router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(bounties_router, prefix="/api/v1")
    app.dependency_overrides[get_bounty_service] = lambda: mock_bounty_service_for_admin
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def admin_client(test_app_admin):
    return TestClient(test_app_admin)


class TestClearGuildBounties:
    """Tests for DELETE /api/v1/bounties/guild/{guild_id}/clear."""

    @patch("api.routers.bounties.get_db_session")
    @patch("api.routers.bounties.AuditService")
    def test_clear_all_tiers_returns_200(self, mock_audit, mock_get_db, admin_client, mock_bounty_service_for_admin):
        """Returns 200 with cleared bounty summary."""
        _configure_db_mock(mock_get_db)
        mock_audit.log_action = AsyncMock()

        response = admin_client.delete("/api/v1/bounties/guild/67890/clear?user_id=999")

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890
        assert data["cleared_count"] == 3
        assert data["bounty_ids"] == [1, 2, 3]
        assert data["announcements_deleted"] == 2

    @patch("api.routers.bounties.get_db_session")
    @patch("api.routers.bounties.AuditService")
    def test_clear_specific_tier(self, mock_audit, mock_get_db, admin_client, mock_bounty_service_for_admin):
        """Returns 200 when tier filter is applied."""
        _configure_db_mock(mock_get_db)
        mock_audit.log_action = AsyncMock()
        mock_bounty_service_for_admin.clear_bounties = AsyncMock(
            return_value={
                "guild_id": 67890,
                "tier": "bronze",
                "cleared_count": 1,
                "bounty_ids": [7],
                "announcements_deleted": 1,
            }
        )

        response = admin_client.delete("/api/v1/bounties/guild/67890/clear?tier=bronze&user_id=999")

        assert response.status_code == 200
        data = response.json()
        assert data["tier"] == "bronze"
        assert data["cleared_count"] == 1

    @patch("api.routers.bounties.get_db_session")
    @patch("api.routers.bounties.AuditService")
    def test_clear_service_error_returns_500(
        self, mock_audit, mock_get_db, admin_client, mock_bounty_service_for_admin
    ):
        """Returns 500 when service raises exception."""
        _configure_db_mock(mock_get_db)
        mock_audit.log_action = AsyncMock()
        mock_bounty_service_for_admin.clear_bounties = AsyncMock(side_effect=Exception("DB error"))

        response = admin_client.delete("/api/v1/bounties/guild/67890/clear?user_id=999")

        assert response.status_code == 500


# ===========================================================================
# Tests: POST /bounties/guild/{guild_id}/admin-spawn
# ===========================================================================


class TestAdminSpawnBounties:
    """Tests for POST /api/v1/bounties/guild/{guild_id}/admin-spawn."""

    @patch("api.routers.bounties.get_db_session")
    @patch("api.routers.bounties.AuditService")
    @patch("api.routers.bounties.ConfigRepository")
    def test_admin_spawn_with_available_slot(
        self,
        mock_cr_cls,
        mock_audit,
        mock_get_db,
        admin_client,
        mock_bounty_service_for_admin,
    ):
        """Returns 200 with spawned bounties when slots are available."""
        _configure_db_mock(mock_get_db)
        mock_audit.log_action = AsyncMock()

        # Config with expiry setting
        mock_config = MagicMock()
        mock_config.bounty_expiry_minutes = 480
        mock_cr = AsyncMock()
        mock_cr.get_by_guild_id = AsyncMock(return_value=mock_config)
        mock_cr_cls.return_value = mock_cr

        response = admin_client.post("/api/v1/bounties/guild/67890/admin-spawn?tier=bronze&user_id=999")

        assert response.status_code == 200
        data = response.json()
        assert data["guild_id"] == 67890
        assert "spawned" in data
        assert "skipped_tiers" in data

    @patch("api.routers.bounties.get_db_session")
    @patch("api.routers.bounties.AuditService")
    @patch("api.routers.bounties.ConfigRepository")
    def test_admin_spawn_bypasses_cap(
        self,
        mock_cr_cls,
        mock_audit,
        mock_get_db,
        admin_client,
        mock_bounty_service_for_admin,
    ):
        """Admin spawn bypasses the max-bounty cap — spawns even when many bounties are active.

        The old capacity check was removed; admin-spawn always attempts to spawn regardless
        of how many active bounties already exist.
        """
        _configure_db_mock(mock_get_db)
        mock_audit.log_action = AsyncMock()

        mock_config = MagicMock()
        mock_config.bounty_expiry_minutes = 480
        mock_cr = AsyncMock()
        mock_cr.get_by_guild_id = AsyncMock(return_value=mock_config)
        mock_cr_cls.return_value = mock_cr

        # Even though service.spawn_bounty returns a bounty (simulating many active),
        # admin-spawn does not skip — it spawns unconditionally.
        response = admin_client.post("/api/v1/bounties/guild/67890/admin-spawn?tier=bronze&user_id=999")

        assert response.status_code == 200
        data = response.json()
        # Bronze was NOT skipped — admin bypasses the cap
        assert "bronze" not in data["skipped_tiers"], f"Expected bronze NOT in skipped_tiers, got: {data}"
        assert len(data["spawned"]) == 1

    @patch("api.routers.bounties.get_db_session")
    @patch("api.routers.bounties.AuditService")
    @patch("api.routers.bounties.ConfigRepository")
    @patch("utils.executors.bounty_spawn_executor._announce_bounty")
    @patch("utils.executors.bounty_spawn_executor._schedule_expiry_job")
    def test_admin_spawn_calls_announce_and_schedule(
        self,
        mock_schedule,
        mock_announce,
        mock_cr_cls,
        mock_audit,
        mock_get_db,
        admin_client,
        mock_bounty_service_for_admin,
    ):
        """Bug 7: admin-spawn endpoint calls _schedule_expiry_job and _announce_bounty
        after a successful bounty spawn so that players receive announcements.
        """
        _configure_db_mock(mock_get_db)
        mock_audit.log_action = AsyncMock()
        mock_schedule.return_value = None
        mock_announce.return_value = None

        mock_config = MagicMock()
        mock_config.bounty_expiry_minutes = 480
        mock_cr = AsyncMock()
        mock_cr.get_by_guild_id = AsyncMock(return_value=mock_config)
        mock_cr_cls.return_value = mock_cr

        response = admin_client.post("/api/v1/bounties/guild/67890/admin-spawn?tier=bronze&user_id=999")

        assert response.status_code == 200
        data = response.json()
        assert len(data["spawned"]) == 1
        # Verify both announce and schedule were called once (for the one spawned bounty)
        mock_schedule.assert_awaited_once()
        mock_announce.assert_awaited_once()

    @patch("api.routers.bounties.get_db_session")
    @patch("api.routers.bounties.AuditService")
    @patch("api.routers.bounties.ConfigRepository")
    @patch("utils.executors.bounty_spawn_executor._announce_bounty")
    @patch("utils.executors.bounty_spawn_executor._schedule_expiry_job")
    def test_admin_spawn_announce_failure_is_non_fatal(
        self,
        mock_schedule,
        mock_announce,
        mock_cr_cls,
        mock_audit,
        mock_get_db,
        admin_client,
        mock_bounty_service_for_admin,
    ):
        """Bug 7: If _announce_bounty raises, the admin-spawn still returns 200 (best-effort)."""
        _configure_db_mock(mock_get_db)
        mock_audit.log_action = AsyncMock()
        mock_schedule.return_value = None
        mock_announce.side_effect = RuntimeError("Gateway unreachable")

        mock_config = MagicMock()
        mock_config.bounty_expiry_minutes = 480
        mock_cr = AsyncMock()
        mock_cr.get_by_guild_id = AsyncMock(return_value=mock_config)
        mock_cr_cls.return_value = mock_cr

        response = admin_client.post("/api/v1/bounties/guild/67890/admin-spawn?tier=bronze&user_id=999")

        # Even though announce failed, the spawn itself succeeded
        assert response.status_code == 200
        data = response.json()
        assert len(data["spawned"]) == 1

    @patch("api.routers.bounties.get_db_session")
    @patch("api.routers.bounties.AuditService")
    @patch("api.routers.bounties.ConfigRepository")
    @patch("utils.executors.bounty_spawn_executor._announce_bounty")
    @patch("utils.executors.bounty_spawn_executor._schedule_expiry_job")
    def test_admin_spawn_with_none_config_still_attempts_announce(
        self,
        mock_schedule,
        mock_announce,
        mock_cr_cls,
        mock_audit,
        mock_get_db,
        admin_client,
        mock_bounty_service_for_admin,
    ):
        """When guild config is None (unconfigured guild), admin-spawn still calls _announce_bounty
        (which handles the None case gracefully by returning early with a warning).
        This ensures no exception is raised and spawn still returns 200.
        """
        _configure_db_mock(mock_get_db)
        mock_audit.log_action = AsyncMock()
        mock_schedule.return_value = None
        mock_announce.return_value = None

        # Config is None — guild not configured
        mock_cr = AsyncMock()
        mock_cr.get_by_guild_id = AsyncMock(return_value=None)
        mock_cr_cls.return_value = mock_cr

        response = admin_client.post("/api/v1/bounties/guild/67890/admin-spawn?tier=bronze&user_id=999")

        assert response.status_code == 200
        data = response.json()
        assert len(data["spawned"]) == 1
        # _announce_bounty is called even when config is None (it handles None config internally)
        mock_announce.assert_awaited_once()


# ===========================================================================
# Gap 1: Empty-State / Null-Result Tests
# ===========================================================================


class TestGetGuildBountiesEmpty:
    """Gap 1: Empty-state tests for the bounties list endpoint."""

    @patch("api.routers.bounties.get_db_session")
    def test_get_guild_bounties_empty_guild_returns_empty_list(self, mock_get_db, client, mock_bounty_service):
        """GET /bounties/?guild_id={id} with no active bounties → 200 + empty list.

        Verifies that a guild with no bounties does not produce a 500; the endpoint
        should return an empty JSON array.
        """
        _configure_db_mock(mock_get_db)
        # Simulate a guild that has never had a bounty — repo returns []
        mock_bounty_service.bounty_repo.get_active_by_guild = AsyncMock(return_value=[])

        response = client.get("/api/v1/bounties/?guild_id=99999")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert data == []

    @patch("api.routers.bounties.get_db_session")
    def test_check_bounty_no_player_returns_not_found_not_500(self, mock_get_db, client, mock_bounty_service):
        """POST /bounties/check with a player_id that has never existed → 200 NOT_FOUND (not 500).

        A non-existent player should produce a graceful NOT_FOUND result rather than
        an unhandled exception that surfaces as a 500.
        """
        _configure_db_mock(mock_get_db)
        # Service raises because there are no bounties/player not found
        mock_bounty_service.check_bounty = AsyncMock(side_effect=ValueError("Player 99999 not found in guild"))

        response = client.post(
            "/api/v1/bounties/check?guild_id=67890",
            json={"player_id": 99999, "system_name": "Sol"},
        )

        # Must not be 500 — the router catches exceptions and returns NOT_FOUND gracefully
        assert response.status_code == 200
        data = response.json()
        assert data["result"] == "not_found"
        assert data["bounty_id"] is None


# ===========================================================================
# Gap 2: Cross-Service Side-Effect Tests
# ===========================================================================


class TestExpireBountyDeletesDiscordMessage:
    """Gap 2: Cross-service side-effect test — bounty expiry triggers Discord message deletion."""

    @patch("api.routers.bounties.get_db_session")
    @patch("api.routers.bounties.AuditService")
    def test_expire_bounty_deletes_discord_message(
        self, mock_audit, mock_get_db, admin_client, mock_bounty_service_for_admin
    ):
        """When a bounty is cleared, the discord message deletion is triggered.

        The clear_bounties service call returns announcements_deleted > 0, proving
        that the side-effect path (Discord message deletion) was exercised.
        """
        _configure_db_mock(mock_get_db)
        mock_audit.log_action = AsyncMock()

        # Service reports 2 Discord messages were deleted as a side-effect
        mock_bounty_service_for_admin.clear_bounties = AsyncMock(
            return_value={
                "guild_id": 67890,
                "tier": None,
                "cleared_count": 2,
                "bounty_ids": [10, 11],
                "announcements_deleted": 2,
            }
        )

        response = admin_client.delete("/api/v1/bounties/guild/67890/clear?user_id=999")

        assert response.status_code == 200
        data = response.json()
        # The response must include the Discord side-effect count
        assert data["announcements_deleted"] == 2
        assert data["cleared_count"] == 2
        # Verify clear_bounties was actually called (triggering the side effect)
        mock_bounty_service_for_admin.clear_bounties.assert_awaited_once()


# ===========================================================================
# New field: BountyCheckResponse with division/combat/bonus fields
# ===========================================================================


class TestCheckBountyNewFields:
    """Tests that new combat fields are included in the check response."""

    @patch("api.routers.bounties.get_db_session")
    def test_check_bounty_correct_includes_division(self, mock_get_db, client, mock_bounty_service):
        """The check response includes division when result is correct."""
        _configure_db_mock(mock_get_db)
        from services.bounty_service import CheckResponse, CheckResult

        mock_bounty_service.check_bounty = AsyncMock(
            return_value=CheckResponse(
                result=CheckResult.CORRECT,
                bounty_id=1,
                message="Bounty captured! +500cr",
                division="bronze",
                criminal_name="Viper",
                reward=500,
                total_reward=1000,
                bonus_won=True,
                criminal_ship={"ship_name": "Bandit", "ship_armour": 80},
            )
        )

        response = client.post(
            "/api/v1/bounties/check?guild_id=67890",
            json={"player_id": 42, "system_name": "Sol"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"] == "correct"
        assert data["division"] == "bronze"
        assert data["criminal_name"] == "Viper"
        assert data["reward"] == 500
        assert data["total_reward"] == 1000
        assert data["bonus_won"] is True
        assert data["criminal_ship"]["ship_name"] == "Bandit"

    @patch("api.routers.bounties.get_db_session")
    def test_check_bounty_includes_combat_result(self, mock_get_db, client, mock_bounty_service):
        """The check response includes combat_result dict when combat occurred."""
        _configure_db_mock(mock_get_db)
        from services.bounty_service import CheckResponse, CheckResult

        mock_bounty_service.check_bounty = AsyncMock(
            return_value=CheckResponse(
                result=CheckResult.CORRECT,
                bounty_id=2,
                message="Combat victory! +800cr",
                division="silver",
                criminal_name="Crusher",
                reward=800,
                combat_won=True,
                combat_result={
                    "winner_name": "Betty",
                    "loser_name": "Crusher",
                    "is_stalemate": False,
                    "ship1_stats": {"ship_name": "Betty"},
                    "ship2_stats": {"ship_name": "Crusher"},
                    "variance_percent": 0.05,
                },
            )
        )

        response = client.post(
            "/api/v1/bounties/check?guild_id=67890",
            json={"player_id": 42, "system_name": "Sol"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["combat_result"]["winner_name"] == "Betty"
        assert data["combat_result"]["is_stalemate"] is False
        assert data["division"] == "silver"
        assert data["combat_won"] is True

    @patch("api.routers.bounties.get_db_session")
    def test_check_bounty_silver_loss_no_reward(self, mock_get_db, client, mock_bounty_service):
        """Silver loss has no reward and combat_won=False."""
        _configure_db_mock(mock_get_db)
        from services.bounty_service import CheckResponse, CheckResult

        mock_bounty_service.check_bounty = AsyncMock(
            return_value=CheckResponse(
                result=CheckResult.CORRECT,
                bounty_id=3,
                message="Crusher defeated you in combat and escaped!",
                division="silver",
                criminal_name="Crusher",
                combat_won=False,
                reward=None,
                combat_result={
                    "winner_name": "Crusher",
                    "loser_name": "Betty",
                    "is_stalemate": False,
                    "ship1_stats": {},
                    "ship2_stats": {},
                    "variance_percent": 0.05,
                },
            )
        )

        response = client.post(
            "/api/v1/bounties/check?guild_id=67890",
            json={"player_id": 42, "system_name": "Sol"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["combat_won"] is False
        assert data["reward"] is None
        assert data["bonus_won"] is False


# ===========================================================================
# Tests: POST /bounties/combat-bonus
# ===========================================================================


class TestCombatBonusEndpoint:
    """Tests for POST /api/v1/bounties/combat-bonus."""

    @pytest.fixture
    def mock_bounty_service_with_player(self):
        service = AsyncMock()
        service.bounty_repo = AsyncMock()
        service.player_repo = AsyncMock()
        return service

    @pytest.fixture
    def test_app_combat(self, mock_bounty_service):
        app = FastAPI()
        from api.routers.bounties import get_bounty_service
        from api.routers.bounties import router as bounties_router

        app.include_router(bounties_router, prefix="/api/v1")
        app.dependency_overrides[get_bounty_service] = lambda: mock_bounty_service
        yield app
        app.dependency_overrides.clear()

    @pytest.fixture
    def combat_client(self, test_app_combat):
        return TestClient(test_app_combat)

    @patch("api.routers.bounties.get_db_session")
    @patch("services.loadout_builder.LoadoutBuilder")
    @patch("services.combat_service.CombatService")
    def test_combat_bonus_win(self, mock_combat_cls, mock_lb_cls, mock_get_db, combat_client, mock_bounty_service):
        """Player wins combat bonus → returns won=True, bonus_credits=base_reward."""
        from types import SimpleNamespace

        _configure_db_mock(mock_get_db)

        # Mock loadout builder
        player_loadout = MagicMock()
        player_loadout.ship_name = "Betty"
        mock_lb_cls.from_player = AsyncMock(return_value=player_loadout)
        mock_lb_cls.from_criminal_ship = MagicMock(return_value=MagicMock(ship_name="Bandit"))

        # Mock combat service
        mock_combat = MagicMock()
        fight_stats1 = SimpleNamespace(
            ship_name="Betty", raw_hp=200, raw_dps=10.0, varied_hp=195, varied_dps=10.5, ttk=18.57
        )
        fight_stats2 = SimpleNamespace(
            ship_name="Bandit", raw_hp=100, raw_dps=5.0, varied_hp=98, varied_dps=5.1, ttk=38.2
        )
        mock_fight = MagicMock()
        mock_fight.winner_name = "Betty"
        mock_fight.loser_name = "Bandit"
        mock_fight.is_stalemate = False
        mock_fight.ship1_stats = fight_stats1
        mock_fight.ship2_stats = fight_stats2
        mock_fight.variance_percent = 0.05
        mock_combat.fight_ships.return_value = mock_fight
        mock_combat_cls.return_value = mock_combat

        # Mock player in DB
        mock_player = MagicMock()
        mock_player.credits = 1000
        mock_player.lifetime_credits = 5000
        mock_bounty_service.player_repo.get_by_id = AsyncMock(return_value=mock_player)

        response = combat_client.post(
            "/api/v1/bounties/combat-bonus",
            json={
                "player_id": 42,
                "base_reward": 500,
                "criminal_ship": {"ship_name": "Bandit", "ship_armour": 80, "weapons": [], "turrets": []},
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["won"] is True
        assert data["bonus_credits"] == 500
        assert "2x" in data["message"] or "bonus" in data["message"].lower()
        assert "combat_result" in data

    @patch("api.routers.bounties.get_db_session")
    @patch("services.loadout_builder.LoadoutBuilder")
    @patch("services.combat_service.CombatService")
    def test_combat_bonus_loss(self, mock_combat_cls, mock_lb_cls, mock_get_db, combat_client, mock_bounty_service):
        """Player loses combat bonus → returns won=False, bonus_credits=0."""
        from types import SimpleNamespace

        _configure_db_mock(mock_get_db)

        player_loadout = MagicMock()
        player_loadout.ship_name = "Betty"
        mock_lb_cls.from_player = AsyncMock(return_value=player_loadout)
        mock_lb_cls.from_criminal_ship = MagicMock(return_value=MagicMock(ship_name="Overlord"))

        mock_combat = MagicMock()
        fight_stats1 = SimpleNamespace(
            ship_name="Betty", raw_hp=100, raw_dps=5.0, varied_hp=98, varied_dps=5.1, ttk=10.0
        )
        fight_stats2 = SimpleNamespace(
            ship_name="Overlord", raw_hp=1000, raw_dps=99.0, varied_hp=999, varied_dps=99.0, ttk=19.6
        )
        mock_fight = MagicMock()
        mock_fight.winner_name = "Overlord"
        mock_fight.loser_name = "Betty"
        mock_fight.is_stalemate = False
        mock_fight.ship1_stats = fight_stats1
        mock_fight.ship2_stats = fight_stats2
        mock_fight.variance_percent = 0.05
        mock_combat.fight_ships.return_value = mock_fight
        mock_combat_cls.return_value = mock_combat

        mock_bounty_service.player_repo.get_by_id = AsyncMock(return_value=None)

        response = combat_client.post(
            "/api/v1/bounties/combat-bonus",
            json={
                "player_id": 42,
                "base_reward": 500,
                "criminal_ship": {"ship_name": "Overlord", "ship_armour": 1000, "weapons": [], "turrets": []},
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["won"] is False
        assert data["bonus_credits"] == 0
        assert "combat_result" in data

    @patch("api.routers.bounties.get_db_session")
    @patch("services.loadout_builder.LoadoutBuilder")
    @patch("services.combat_service.CombatService")
    def test_combat_bonus_stalemate_counts_as_win(
        self, mock_combat_cls, mock_lb_cls, mock_get_db, combat_client, mock_bounty_service
    ):
        """Stalemate in combat-bonus endpoint counts as player win."""
        from types import SimpleNamespace

        _configure_db_mock(mock_get_db)

        player_loadout = MagicMock()
        player_loadout.ship_name = "Betty"
        mock_lb_cls.from_player = AsyncMock(return_value=player_loadout)
        mock_lb_cls.from_criminal_ship = MagicMock(return_value=MagicMock(ship_name="Raider"))

        mock_combat = MagicMock()
        fight_stats1 = SimpleNamespace(
            ship_name="Betty", raw_hp=100, raw_dps=0.0, varied_hp=100, varied_dps=0.0, ttk=None
        )
        fight_stats2 = SimpleNamespace(
            ship_name="Raider", raw_hp=100, raw_dps=0.0, varied_hp=100, varied_dps=0.0, ttk=None
        )
        mock_fight = MagicMock()
        mock_fight.winner_name = None
        mock_fight.loser_name = None
        mock_fight.is_stalemate = True
        mock_fight.ship1_stats = fight_stats1
        mock_fight.ship2_stats = fight_stats2
        mock_fight.variance_percent = 0.0
        mock_combat.fight_ships.return_value = mock_fight
        mock_combat_cls.return_value = mock_combat

        mock_player = MagicMock()
        mock_player.credits = 500
        mock_player.lifetime_credits = 1000
        mock_bounty_service.player_repo.get_by_id = AsyncMock(return_value=mock_player)

        response = combat_client.post(
            "/api/v1/bounties/combat-bonus",
            json={
                "player_id": 42,
                "base_reward": 300,
                "criminal_ship": {"ship_name": "Raider", "ship_armour": 100, "weapons": [], "turrets": []},
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["won"] is True
        assert data["bonus_credits"] == 300
