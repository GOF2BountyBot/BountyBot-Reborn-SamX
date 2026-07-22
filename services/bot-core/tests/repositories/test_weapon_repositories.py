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
from types import ModuleType, SimpleNamespace
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
        """get_by_name should return the matching weapon with its real attributes intact."""
        weapon = SimpleNamespace(id=11, name="Plasma Cannon")
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(weapon))

        result = await repo.get_by_name(mock_db, "Plasma Cannon")

        assert result is weapon
        assert result.id == 11
        assert result.name == "Plasma Cannon"
        mock_db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_by_name_returns_none_when_not_found(self, repo, mock_db):
        """get_by_name should return None when weapon does not exist."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        result = await repo.get_by_name(mock_db, "Ghost Gun")

        assert result is None

    @pytest.mark.asyncio
    async def test_create_or_update_creates_new_primary_weapon(self, repo, mock_db):
        """create_or_update should create a new PrimaryWeapon, mapping item/weapon/
        primary fields onto the real PrimaryWeapon constructor kwargs."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockPrimaryWeapon:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                object.__setattr__(self, "id", None)
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        raw = {
            "name": "Plasma Cannon",
            "dps": 150.0,
            "builtIn": True,
            "techLevel": 4,
        }
        with patch("persist.repositories.primary_weapon_repository.PrimaryWeapon", MockPrimaryWeapon):
            result = await repo.create_or_update(mock_db, raw)

        assert captured_kwargs["name"] == "Plasma Cannon"
        assert captured_kwargs["dps"] == 150.0
        assert captured_kwargs["built_in"] is True
        assert captured_kwargs["tech_level"] == 4
        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once()
        assert result is mock_db.refresh.call_args[0][0]
        assert result.dps == 150.0

    @pytest.mark.asyncio
    async def test_create_or_update_includes_dps_field(self, repo, mock_db):
        """create_or_update must include the dps field in the new object."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockPrimaryWeapon:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                object.__setattr__(self, "id", None)
                object.__setattr__(self, "name", None)
                object.__setattr__(self, "dps", None)
                object.__setattr__(self, "tech_level", None)
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        with patch("persist.repositories.primary_weapon_repository.PrimaryWeapon", MockPrimaryWeapon):
            await repo.create_or_update(mock_db, {"name": "Laser", "dps": 75.0})

        assert captured_kwargs.get("dps") == 75.0

    @pytest.mark.asyncio
    async def test_create_or_update_updates_existing_primary_weapon(self, repo, mock_db):
        """create_or_update should update mapped attrs on an existing PrimaryWeapon."""
        existing = SimpleNamespace(id=5, name="Plasma Cannon", dps=150.0, tech_level=None, extra_atts={})
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(existing))

        raw = {"name": "Plasma Cannon", "dps": 200.0}
        result = await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_not_called()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once_with(existing)
        assert result is existing
        assert existing.dps == 200.0
        # builtIn absent from raw -> item_fields default of False is applied
        assert existing.built_in is False
        assert existing.extra_atts == {}

    @pytest.mark.asyncio
    async def test_create_or_update_raises_when_name_missing(self, repo, mock_db):
        """create_or_update must raise ValueError when 'name' key is absent."""
        with pytest.raises(ValueError, match="Missing required key 'name' in data for primary_weapon"):
            await repo.create_or_update(mock_db, {"dps": 100.0})

        mock_db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_or_update_raises_when_dps_missing(self, repo, mock_db):
        """create_or_update must raise ValueError when 'dps' key is absent."""
        with pytest.raises(ValueError, match="Missing required key 'dps' in data for primary_weapon"):
            await repo.create_or_update(mock_db, {"name": "Laser Cannon"})


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
        """get_by_name should return the matching secondary weapon with its real attributes intact."""
        weapon = SimpleNamespace(id=13, name="Torpedo")
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(weapon))

        result = await repo.get_by_name(mock_db, "Torpedo")

        assert result is weapon
        assert result.id == 13
        assert result.name == "Torpedo"

    @pytest.mark.asyncio
    async def test_get_by_name_returns_none_when_not_found(self, repo, mock_db):
        """get_by_name should return None when secondary weapon does not exist."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        result = await repo.get_by_name(mock_db, "Unknown")

        assert result is None

    @pytest.mark.asyncio
    async def test_create_or_update_creates_new_secondary_weapon(self, repo, mock_db):
        """create_or_update should create a new SecondaryWeapon, mapping item/weapon/
        secondary fields onto the real SecondaryWeapon constructor kwargs."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockSecondaryWeapon:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                object.__setattr__(self, "id", None)
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        raw = {
            "name": "Torpedo",
            "damage": 500,
            "loadingSpeed": 3.0,
            "builtIn": False,
        }
        with patch("persist.repositories.secondary_weapon_repository.SecondaryWeapon", MockSecondaryWeapon):
            result = await repo.create_or_update(mock_db, raw)

        assert captured_kwargs["name"] == "Torpedo"
        assert captured_kwargs["damage"] == 500
        assert captured_kwargs["loading_speed"] == 3.0
        assert captured_kwargs["built_in"] is False
        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once()
        assert result is mock_db.refresh.call_args[0][0]
        assert result.damage == 500

    @pytest.mark.asyncio
    async def test_create_or_update_includes_damage_field(self, repo, mock_db):
        """create_or_update must include the damage field in the new object."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockSecondaryWeapon:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                object.__setattr__(self, "id", None)
                object.__setattr__(self, "name", None)
                object.__setattr__(self, "damage", None)
                object.__setattr__(self, "tech_level", None)
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        with patch("persist.repositories.secondary_weapon_repository.SecondaryWeapon", MockSecondaryWeapon):
            await repo.create_or_update(mock_db, {"name": "Missile", "damage": 250})

        assert captured_kwargs.get("damage") == 250

    @pytest.mark.asyncio
    async def test_create_or_update_updates_existing_secondary_weapon(self, repo, mock_db):
        """create_or_update should update mapped attrs on an existing SecondaryWeapon."""
        existing = SimpleNamespace(id=9, name="Torpedo", damage=500, loading_speed=None, extra_atts={})
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(existing))

        raw = {"name": "Torpedo", "damage": 600}
        result = await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_not_called()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once_with(existing)
        assert result is existing
        assert existing.damage == 600
        assert existing.loading_speed is None
        assert existing.extra_atts == {}

    @pytest.mark.asyncio
    async def test_create_or_update_raises_when_name_missing(self, repo, mock_db):
        """create_or_update must raise ValueError when 'name' key is absent."""
        with pytest.raises(ValueError, match="Missing required key 'name' in data for secondary_weapon"):
            await repo.create_or_update(mock_db, {"damage": 500})

        mock_db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_or_update_raises_when_damage_missing(self, repo, mock_db):
        """create_or_update must raise ValueError when 'damage' key is absent."""
        with pytest.raises(ValueError, match="Missing required key 'damage' in data for secondary_weapon"):
            await repo.create_or_update(mock_db, {"name": "Missile"})

    @pytest.mark.asyncio
    async def test_loading_speed_read_from_wiki_style_key(self, repo, mock_db):
        """PR-2 L2: ``"loading speed"`` (wiki infobox style, with space) must be
        accepted as the source for the ``loading_speed`` column.

        All 30 current seed JSONs use this exact key. Prior to PR-2 the loader
        only read ``loadingSpeed`` (camelCase), leaving ``loading_speed`` NULL
        on every populated secondary in the DB.
        """
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockSecondaryWeapon:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                object.__setattr__(self, "id", None)
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        raw = {"name": "Shesha", "damage": 60, "loading speed": 3000}
        with patch("persist.repositories.secondary_weapon_repository.SecondaryWeapon", MockSecondaryWeapon):
            await repo.create_or_update(mock_db, raw)

        assert captured_kwargs.get("loading_speed") == 3000

    @pytest.mark.asyncio
    async def test_loading_speed_camelcase_fallback(self, repo, mock_db):
        """PR-2 L2: ``"loadingSpeed"`` (camelCase) remains a valid fallback for
        forward-compat with any future seed JSON that uses it.
        """
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockSecondaryWeapon:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                object.__setattr__(self, "id", None)
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        raw = {"name": "FutureWeapon", "damage": 100, "loadingSpeed": 1500}
        with patch("persist.repositories.secondary_weapon_repository.SecondaryWeapon", MockSecondaryWeapon):
            await repo.create_or_update(mock_db, raw)

        assert captured_kwargs.get("loading_speed") == 1500

    @pytest.mark.asyncio
    async def test_loading_speed_wiki_key_wins_over_camelcase(self, repo, mock_db):
        """PR-2 L2: when both keys are present, the wiki-style key (which is
        what every current seed file uses) takes precedence.
        """
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockSecondaryWeapon:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                object.__setattr__(self, "id", None)
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        raw = {
            "name": "BothKeys",
            "damage": 50,
            "loading speed": 2000,
            "loadingSpeed": 9999,
        }
        with patch("persist.repositories.secondary_weapon_repository.SecondaryWeapon", MockSecondaryWeapon):
            await repo.create_or_update(mock_db, raw)

        assert captured_kwargs.get("loading_speed") == 2000


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
        """get_by_name should return the matching turret weapon with its real attributes intact."""
        weapon = SimpleNamespace(id=17, name="Gatling Turret")
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(weapon))

        result = await repo.get_by_name(mock_db, "Gatling Turret")

        assert result is weapon
        assert result.id == 17
        assert result.name == "Gatling Turret"

    @pytest.mark.asyncio
    async def test_get_by_name_returns_none_when_not_found(self, repo, mock_db):
        """get_by_name should return None when turret weapon does not exist."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        result = await repo.get_by_name(mock_db, "Unknown Turret")

        assert result is None

    @pytest.mark.asyncio
    async def test_create_or_update_creates_new_turret_weapon(self, repo, mock_db):
        """create_or_update should create a new TurretWeapon, mapping item/weapon/
        turret fields onto the real TurretWeapon constructor kwargs."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockTurretWeapon:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                object.__setattr__(self, "id", None)
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        raw = {
            "name": "Gatling Turret",
            "dps": 80.0,
            "automatic": True,
            "builtIn": True,
        }
        with patch("persist.repositories.turret_weapon_repository.TurretWeapon", MockTurretWeapon):
            result = await repo.create_or_update(mock_db, raw)

        assert captured_kwargs["name"] == "Gatling Turret"
        assert captured_kwargs["dps"] == 80.0
        assert captured_kwargs["automatic"] is True
        assert captured_kwargs["built_in"] is True
        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once()
        assert result is mock_db.refresh.call_args[0][0]
        assert result.dps == 80.0

    @pytest.mark.asyncio
    async def test_create_or_update_includes_dps_field(self, repo, mock_db):
        """create_or_update must include the dps field in the new turret object."""
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(None))

        captured_kwargs = {}

        class MockTurretWeapon:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                object.__setattr__(self, "id", None)
                object.__setattr__(self, "name", None)
                object.__setattr__(self, "dps", None)
                object.__setattr__(self, "tech_level", None)
                for k, v in kwargs.items():
                    object.__setattr__(self, k, v)

        with patch("persist.repositories.turret_weapon_repository.TurretWeapon", MockTurretWeapon):
            await repo.create_or_update(mock_db, {"name": "Turret X", "dps": 60.0})

        assert captured_kwargs.get("dps") == 60.0

    @pytest.mark.asyncio
    async def test_create_or_update_updates_existing_turret_weapon(self, repo, mock_db):
        """create_or_update should update mapped attrs on an existing TurretWeapon."""
        existing = SimpleNamespace(id=4, name="Gatling Turret", dps=80.0, automatic=None, extra_atts={})
        mock_db.execute = AsyncMock(return_value=_make_one_or_none_result(existing))

        raw = {"name": "Gatling Turret", "dps": 100.0}
        result = await repo.create_or_update(mock_db, raw)

        mock_db.add.assert_not_called()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once_with(existing)
        assert result is existing
        assert existing.dps == 100.0
        assert existing.automatic is None
        assert existing.extra_atts == {}

    @pytest.mark.asyncio
    async def test_create_or_update_raises_when_name_missing(self, repo, mock_db):
        """create_or_update must raise ValueError when 'name' key is absent."""
        with pytest.raises(ValueError, match="Missing required key 'name' in data for turret_weapon"):
            await repo.create_or_update(mock_db, {"dps": 80.0})

        mock_db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_or_update_raises_when_dps_missing(self, repo, mock_db):
        """create_or_update must raise ValueError when 'dps' key is absent."""
        with pytest.raises(ValueError, match="Missing required key 'dps' in data for turret_weapon"):
            await repo.create_or_update(mock_db, {"name": "Heavy Turret"})
