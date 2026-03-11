"""Unit tests for GenericRepository CRUD operations.

The shared.bblogger module is mocked via conftest.py (loaded by pytest before
any import from the src tree). These tests cover:
- list_all()         - returns all rows via scalars().all()
- get_by_id()        - delegates to db.get()
- get_by_name()      - queries by name filter, returns one_or_none()
- get_by_alias()     - queries by alias relationship
- add()              - calls db.add / commit / refresh, returns entity
- remove()           - calls db.delete / commit
- create_or_update() - raises NotImplementedError (base class contract)
"""

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ---------------------------------------------------------------------------
# Ensure shared.bblogger is mocked BEFORE any src imports
# (conftest.py does this project-wide, but we guard here too for safety)
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from persist.repositories.generic_repository import GenericRepository

# ---------------------------------------------------------------------------
# A real SQLAlchemy declarative model so select() coercion works correctly.
# Declared at module level so the mapper is only registered once.
# ---------------------------------------------------------------------------


class _TestBase(DeclarativeBase):
    pass


class _FakeModel(_TestBase):
    """Minimal SQLAlchemy model used only in these tests."""

    __tablename__ = "__test_fake__"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<FakeModel id={self.id}>"


class _AliasModel(_TestBase):
    """SQLAlchemy model with an 'aliases' attribute for alias tests."""

    __tablename__ = "__test_alias__"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=True)
    # 'aliases' relationship is patched in tests; not declared as a real FK here.


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo() -> GenericRepository:
    """Return a GenericRepository wired to _FakeModel."""
    return GenericRepository(_FakeModel)


@pytest.fixture
def mock_session() -> AsyncMock:
    """Return a fully mocked async SQLAlchemy session."""
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.delete = AsyncMock()
    session.get = AsyncMock()
    session.execute = AsyncMock()
    return session


def _make_scalars_result(rows) -> MagicMock:
    """Build a mock that mimics result.scalars().<all|one_or_none>."""
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=rows)
    scalars_mock.one_or_none = MagicMock(return_value=rows[0] if rows else None)
    result_mock = MagicMock()
    result_mock.scalars = MagicMock(return_value=scalars_mock)
    return result_mock


# ---------------------------------------------------------------------------
# TestListAll
# ---------------------------------------------------------------------------


class TestListAll:
    """Tests for GenericRepository.list_all()."""

    @pytest.mark.asyncio
    async def test_list_all_returns_all_rows(self, repo, mock_session):
        """list_all() should return every row returned by scalars().all()."""
        entities = [_FakeModel(id=1, name="alpha"), _FakeModel(id=2, name="beta")]
        mock_session.execute = AsyncMock(return_value=_make_scalars_result(entities))

        result = await repo.list_all(mock_session)

        assert result == entities
        mock_session.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_all_returns_empty_list_when_no_rows(self, repo, mock_session):
        """list_all() should return an empty list when the table is empty."""
        mock_session.execute = AsyncMock(return_value=_make_scalars_result([]))

        result = await repo.list_all(mock_session)

        assert result == []

    @pytest.mark.asyncio
    async def test_list_all_calls_execute_with_select(self, repo, mock_session):
        """list_all() must call session.execute() exactly once."""
        mock_session.execute = AsyncMock(return_value=_make_scalars_result([]))

        await repo.list_all(mock_session)

        mock_session.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_all_single_entity(self, repo, mock_session):
        """list_all() with one row in the DB returns a list of length 1."""
        entity = _FakeModel(id=42, name="solo")
        mock_session.execute = AsyncMock(return_value=_make_scalars_result([entity]))

        result = await repo.list_all(mock_session)

        assert len(result) == 1
        assert result[0] is entity


# ---------------------------------------------------------------------------
# TestGetById
# ---------------------------------------------------------------------------


class TestGetById:
    """Tests for GenericRepository.get_by_id()."""

    @pytest.mark.asyncio
    async def test_get_by_id_returns_entity_when_found(self, repo, mock_session):
        """get_by_id() should return the entity when it exists."""
        entity = _FakeModel(id=7, name="found")
        mock_session.get = AsyncMock(return_value=entity)

        result = await repo.get_by_id(mock_session, 7)

        assert result is entity
        mock_session.get.assert_awaited_once_with(_FakeModel, 7)

    @pytest.mark.asyncio
    async def test_get_by_id_returns_none_when_not_found(self, repo, mock_session):
        """get_by_id() should return None when the entity does not exist."""
        mock_session.get = AsyncMock(return_value=None)

        result = await repo.get_by_id(mock_session, 999)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_by_id_passes_correct_model_class(self, repo, mock_session):
        """get_by_id() must pass the correct model type to db.get()."""
        mock_session.get = AsyncMock(return_value=None)

        await repo.get_by_id(mock_session, 1)

        args, _ = mock_session.get.call_args
        assert args[0] is _FakeModel

    @pytest.mark.asyncio
    async def test_get_by_id_passes_correct_id(self, repo, mock_session):
        """get_by_id() must pass the given ID to db.get()."""
        mock_session.get = AsyncMock(return_value=None)
        target_id = 123

        await repo.get_by_id(mock_session, target_id)

        args, _ = mock_session.get.call_args
        assert args[1] == target_id


# ---------------------------------------------------------------------------
# TestGetByName
# ---------------------------------------------------------------------------


class TestGetByName:
    """Tests for GenericRepository.get_by_name()."""

    @pytest.mark.asyncio
    async def test_get_by_name_returns_entity_when_found(self, repo, mock_session):
        """get_by_name() should return the matching entity."""
        entity = _FakeModel(id=3, name="gamma")
        mock_session.execute = AsyncMock(return_value=_make_scalars_result([entity]))

        result = await repo.get_by_name(mock_session, "gamma")

        assert result is entity

    @pytest.mark.asyncio
    async def test_get_by_name_returns_none_when_not_found(self, repo, mock_session):
        """get_by_name() should return None when no match exists."""
        mock_session.execute = AsyncMock(return_value=_make_scalars_result([]))

        result = await repo.get_by_name(mock_session, "missing")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_by_name_calls_execute_once(self, repo, mock_session):
        """get_by_name() must call session.execute() exactly once."""
        mock_session.execute = AsyncMock(return_value=_make_scalars_result([]))

        await repo.get_by_name(mock_session, "anything")

        mock_session.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# TestAdd
# ---------------------------------------------------------------------------


class TestAdd:
    """Tests for GenericRepository.add()."""

    @pytest.mark.asyncio
    async def test_add_calls_db_add(self, repo, mock_session):
        """add() must call session.add() with the provided entity."""
        entity = _FakeModel(id=10, name="new")

        await repo.add(mock_session, entity)

        mock_session.add.assert_called_once_with(entity)

    @pytest.mark.asyncio
    async def test_add_commits_transaction(self, repo, mock_session):
        """add() must await session.commit()."""
        entity = _FakeModel(id=11, name="commit_me")

        await repo.add(mock_session, entity)

        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_add_refreshes_entity(self, repo, mock_session):
        """add() must await session.refresh() so server-defaults are loaded."""
        entity = _FakeModel(id=12, name="refresh_me")

        await repo.add(mock_session, entity)

        mock_session.refresh.assert_awaited_once_with(entity)

    @pytest.mark.asyncio
    async def test_add_returns_entity(self, repo, mock_session):
        """add() must return the same entity passed to it."""
        entity = _FakeModel(id=13, name="return_me")

        result = await repo.add(mock_session, entity)

        assert result is entity

    @pytest.mark.asyncio
    async def test_add_order_of_operations(self, repo, mock_session):
        """add() must call add -> commit -> refresh in that order."""
        call_order: list[str] = []
        mock_session.add = MagicMock(side_effect=lambda _: call_order.append("add"))
        mock_session.commit = AsyncMock(side_effect=lambda: call_order.append("commit"))
        mock_session.refresh = AsyncMock(side_effect=lambda _: call_order.append("refresh"))

        entity = _FakeModel(id=14, name="order")
        await repo.add(mock_session, entity)

        assert call_order == ["add", "commit", "refresh"]


# ---------------------------------------------------------------------------
# TestRemove
# ---------------------------------------------------------------------------


class TestRemove:
    """Tests for GenericRepository.remove()."""

    @pytest.mark.asyncio
    async def test_remove_calls_db_delete(self, repo, mock_session):
        """remove() must await session.delete() with the entity."""
        entity = _FakeModel(id=20, name="delete_me")

        await repo.remove(mock_session, entity)

        mock_session.delete.assert_awaited_once_with(entity)

    @pytest.mark.asyncio
    async def test_remove_commits_transaction(self, repo, mock_session):
        """remove() must await session.commit()."""
        entity = _FakeModel(id=21, name="commit_delete")

        await repo.remove(mock_session, entity)

        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_remove_returns_none(self, repo, mock_session):
        """remove() must return None (no entity to return after deletion)."""
        entity = _FakeModel(id=22, name="gone")

        result = await repo.remove(mock_session, entity)

        assert result is None

    @pytest.mark.asyncio
    async def test_remove_delete_before_commit(self, repo, mock_session):
        """remove() must call delete before commit."""
        call_order: list[str] = []
        mock_session.delete = AsyncMock(side_effect=lambda _: call_order.append("delete"))
        mock_session.commit = AsyncMock(side_effect=lambda: call_order.append("commit"))

        entity = _FakeModel(id=23, name="order_check")
        await repo.remove(mock_session, entity)

        assert call_order == ["delete", "commit"]


# ---------------------------------------------------------------------------
# TestCreateOrUpdate
# ---------------------------------------------------------------------------


class TestCreateOrUpdate:
    """Tests for GenericRepository.create_or_update() (base class raises)."""

    @pytest.mark.asyncio
    async def test_create_or_update_raises_not_implemented(self, repo, mock_session):
        """Base GenericRepository.create_or_update() must raise NotImplementedError."""
        with pytest.raises(NotImplementedError):
            await repo.create_or_update(mock_session, {"key": "value"})


# ---------------------------------------------------------------------------
# TestGetByAlias
# ---------------------------------------------------------------------------


class TestGetByAlias:
    """Tests for GenericRepository.get_by_alias().

    get_by_alias() calls select(model).where(model.aliases.any(alias)).
    We patch 'persist.repositories.generic_repository.select' so no real
    SQLAlchemy query building happens, then let session.execute return our
    controlled result.
    """

    @pytest.fixture
    def alias_repo(self) -> GenericRepository:
        """Repository wired to _AliasModel."""
        return GenericRepository(_AliasModel)

    @pytest.fixture
    def alias_session(self) -> AsyncMock:
        session = AsyncMock()
        session.execute = AsyncMock()
        return session

    @pytest.mark.asyncio
    async def test_get_by_alias_returns_entity_when_found(self, alias_repo, alias_session):
        """get_by_alias() should return the matching entity."""
        entity = _AliasModel(id=5, name="aliased")
        alias_session.execute = AsyncMock(return_value=_make_scalars_result([entity]))

        # Build mock select chain
        mock_stmt = MagicMock()
        mock_stmt.where = MagicMock(return_value=mock_stmt)
        mock_select = MagicMock(return_value=mock_stmt)

        # Give the model class an 'aliases' attribute
        _AliasModel.aliases = MagicMock()
        _AliasModel.aliases.any = MagicMock(return_value=MagicMock())

        with patch("persist.repositories.generic_repository.select", mock_select):
            result = await alias_repo.get_by_alias(alias_session, "some_alias")

        assert result is entity

    @pytest.mark.asyncio
    async def test_get_by_alias_returns_none_when_not_found(self, alias_repo, alias_session):
        """get_by_alias() should return None when no match exists."""
        alias_session.execute = AsyncMock(return_value=_make_scalars_result([]))

        mock_stmt = MagicMock()
        mock_stmt.where = MagicMock(return_value=mock_stmt)
        mock_select = MagicMock(return_value=mock_stmt)

        _AliasModel.aliases = MagicMock()
        _AliasModel.aliases.any = MagicMock(return_value=MagicMock())

        with patch("persist.repositories.generic_repository.select", mock_select):
            result = await alias_repo.get_by_alias(alias_session, "ghost_alias")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_by_alias_calls_execute_once(self, alias_repo, alias_session):
        """get_by_alias() must call session.execute() exactly once."""
        alias_session.execute = AsyncMock(return_value=_make_scalars_result([]))

        mock_stmt = MagicMock()
        mock_stmt.where = MagicMock(return_value=mock_stmt)
        mock_select = MagicMock(return_value=mock_stmt)

        _AliasModel.aliases = MagicMock()
        _AliasModel.aliases.any = MagicMock(return_value=MagicMock())

        with patch("persist.repositories.generic_repository.select", mock_select):
            await alias_repo.get_by_alias(alias_session, "any")

        alias_session.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# TestRepositoryInit
# ---------------------------------------------------------------------------


class TestRepositoryInit:
    """Tests for GenericRepository construction."""

    def test_init_stores_model_class(self):
        """Constructor must store the model class for later queries."""
        local_repo = GenericRepository(_FakeModel)
        assert local_repo._model is _FakeModel

    def test_init_with_different_model(self):
        """Constructor works with any SQLAlchemy model class."""
        local_repo = GenericRepository(_AliasModel)
        assert local_repo._model is _AliasModel
