"""Unit tests for weapon repositories.

Mock-based tests (no SQLite/ARRAY columns involved).
Covers:
- WeaponRepository: initialization
- PrimaryWeaponRepository: create_or_update (with dps field), get_by_name
- SecondaryWeaponRepository: create_or_update (with damage field), get_by_name
- TurretWeaponRepository: create_or_update (with dps field), get_by_name
"""

import os
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Mock shared.bblogger and sqlalchemy_utils BEFORE any src imports
# ---------------------------------------------------------------------------
_mock_shared = ModuleType("shared")
_mock_shared.bblogger = MagicMock()
_mock_shared.bblogger.get_logger = MagicMock(return_value=MagicMock())
sys.modules.setdefault("shared", _mock_shared)
sys.modules.setdefault("shared.bblogger", _mock_shared.bblogger)

_mock_sau = ModuleType("sqlalchemy_utils")
_mock_sau.UUIDType = MagicMock()
sys.modules.setdefault("sqlalchemy_utils", _mock_sau)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import pytest
from persist.repositories.primary_weapon_repository import PrimaryWeaponRepository
from persist.repositories.secondary_weapon_repository import SecondaryWeaponRepository
from persist.repositories.turret_weapon_repository import TurretWeaponRepository
from persist.repositories.weapon_repository import WeaponRepository

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock()
    return db


def _make_one_or_none_result(value) -> MagicMock:
    scalars_mock = MagicMock()
    scalars_mock.one_or_none = MagicMock(return_value=value)
    result_mock = MagicMock()
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    return result_mock


# ---------------------------------------------------------------------------
# TestWeaponRepository
# ---------------------------------------------------------------------------


class TestWeaponRepository:
    def test_init_stores_weapon_model(self):
        """WeaponRepository.__init__ must store the Weapon model class."""
        from persist.models.weapon import Weapon

        repo = WeaponRepository()
        assert repo._model is Weapon

    def test_init_creates_instance_successfully(self):
        """WeaponRepository can be instantiated without errors."""
        repo = WeaponRepository()
        assert repo is not None


# ---------------------------------------------------------------------------
# TestPrimaryWeaponRepository
# ---------------------------------------------------------------------------


class TestPrimaryWeaponRepository:
    @pytest.fixture
    def repo(self) -> PrimaryWeaponRepository:
        return PrimaryWeaponRepository()

    def test_init_stores_primary_weapon_model(self, repo):
        """PrimaryWeaponRepository must store the PrimaryWeapon model class."""
        from persist.models.primary_weapon import PrimaryWeapon

        assert repo._model is PrimaryWeapon

    @pytest.mark.asyncio
    async def test_get_by_name_returns_weapon_when_found(self, repo, mock_db):
        """get_by_name should return the matching weapon."""
        weapon = MagicMock()
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(weapon))

        result = await repo.get_by_name(mock_db, "Plasma Cannon")

        assert result is weapon
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_by_name_returns_none_when_not_found(self, repo, mock_db):
        """get_by_name should return None when weapon does not exist."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        result = await repo.get_by_name(mock_db, "Ghost Gun")

        assert result is None

    @pytest.mark.asyncio
    async def test_create_or_update_creates_new_primary_weapon(self, repo, mock_db):
        """create_or_update should create a new PrimaryWeapon when not found."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        raw = {
            "name": "Plasma Cannon",
            "dps": 150.0,
            "builtIn": True,
            "techLevel": 4,
        }
        result = await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once()
        assert result is mock_db.refresh.call_args[0][0]

    @pytest.mark.asyncio
    async def test_create_or_update_includes_dps_field(self, repo, mock_db):
        """create_or_update must include the dps field in the new object."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}


        class MockPrimaryWeapon:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        with patch("persist.repositories.primary_weapon_repository.PrimaryWeapon", MockPrimaryWeapon):
            await repo.create_or_update(mock_db, {"name": "Laser", "dps": 75.0})

        assert captured_kwargs.get("dps") == 75.0

    @pytest.mark.asyncio
    async def test_create_or_update_updates_existing_primary_weapon(self, repo, mock_db):
        """create_or_update should update an existing PrimaryWeapon."""
        existing = MagicMock()
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(existing))

        raw = {"name": "Plasma Cannon", "dps": 200.0}
        result = await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_not_called()
        mock_db.commit.assert_awaited_once()
        assert result is existing


# ---------------------------------------------------------------------------
# TestSecondaryWeaponRepository
# ---------------------------------------------------------------------------


class TestSecondaryWeaponRepository:
    @pytest.fixture
    def repo(self) -> SecondaryWeaponRepository:
        return SecondaryWeaponRepository()

    def test_init_stores_secondary_weapon_model(self, repo):
        """SecondaryWeaponRepository must store the SecondaryWeapon model class."""
        from persist.models.secondary_weapon import SecondaryWeapon

        assert repo._model is SecondaryWeapon

    @pytest.mark.asyncio
    async def test_get_by_name_returns_weapon_when_found(self, repo, mock_db):
        """get_by_name should return the matching secondary weapon."""
        weapon = MagicMock()
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(weapon))

        result = await repo.get_by_name(mock_db, "Torpedo")

        assert result is weapon

    @pytest.mark.asyncio
    async def test_get_by_name_returns_none_when_not_found(self, repo, mock_db):
        """get_by_name should return None when secondary weapon does not exist."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        result = await repo.get_by_name(mock_db, "Unknown")

        assert result is None

    @pytest.mark.asyncio
    async def test_create_or_update_creates_new_secondary_weapon(self, repo, mock_db):
        """create_or_update should create a new SecondaryWeapon when not found."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        raw = {
            "name": "Torpedo",
            "damage": 500,
            "loadingSpeed": 3.0,
            "builtIn": False,
        }
        result = await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once()
        assert result is mock_db.refresh.call_args[0][0]

    @pytest.mark.asyncio
    async def test_create_or_update_includes_damage_field(self, repo, mock_db):
        """create_or_update must include the damage field in the new object."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockSecondaryWeapon:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        with patch("persist.repositories.secondary_weapon_repository.SecondaryWeapon", MockSecondaryWeapon):
            await repo.create_or_update(mock_db, {"name": "Missile", "damage": 250})

        assert captured_kwargs.get("damage") == 250

    @pytest.mark.asyncio
    async def test_create_or_update_updates_existing_secondary_weapon(self, repo, mock_db):
        """create_or_update should update an existing SecondaryWeapon."""
        existing = MagicMock()
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(existing))

        raw = {"name": "Torpedo", "damage": 600}
        result = await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_not_called()
        mock_db.commit.assert_awaited_once()
        assert result is existing


# ---------------------------------------------------------------------------
# TestTurretWeaponRepository
# ---------------------------------------------------------------------------


class TestTurretWeaponRepository:
    @pytest.fixture
    def repo(self) -> TurretWeaponRepository:
        return TurretWeaponRepository()

    def test_init_stores_turret_weapon_model(self, repo):
        """TurretWeaponRepository must store the TurretWeapon model class."""
        from persist.models.turret_weapon import TurretWeapon

        assert repo._model is TurretWeapon

    @pytest.mark.asyncio
    async def test_get_by_name_returns_weapon_when_found(self, repo, mock_db):
        """get_by_name should return the matching turret weapon."""
        weapon = MagicMock()
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(weapon))

        result = await repo.get_by_name(mock_db, "Gatling Turret")

        assert result is weapon

    @pytest.mark.asyncio
    async def test_get_by_name_returns_none_when_not_found(self, repo, mock_db):
        """get_by_name should return None when turret weapon does not exist."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        result = await repo.get_by_name(mock_db, "Unknown Turret")

        assert result is None

    @pytest.mark.asyncio
    async def test_create_or_update_creates_new_turret_weapon(self, repo, mock_db):
        """create_or_update should create a new TurretWeapon when not found."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        raw = {
            "name": "Gatling Turret",
            "dps": 80.0,
            "automatic": True,
            "builtIn": True,
        }
        result = await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once()
        assert result is mock_db.refresh.call_args[0][0]

    @pytest.mark.asyncio
    async def test_create_or_update_includes_dps_field(self, repo, mock_db):
        """create_or_update must include the dps field in the new turret object."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockTurretWeapon:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        with patch("persist.repositories.turret_weapon_repository.TurretWeapon", MockTurretWeapon):
            await repo.create_or_update(mock_db, {"name": "Turret X", "dps": 60.0})

        assert captured_kwargs.get("dps") == 60.0

    @pytest.mark.asyncio
    async def test_create_or_update_updates_existing_turret_weapon(self, repo, mock_db):
        """create_or_update should update an existing TurretWeapon."""
        existing = MagicMock()
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(existing))

        raw = {"name": "Gatling Turret", "dps": 100.0}
        result = await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_not_called()
        mock_db.commit.assert_awaited_once()
        assert result is existing
