"""Tests for GenericRepository CRUD operations.

Read/query methods (list_all/get_by_id/get_by_name) run against a real
in-memory SQLite engine over a real declarative `_FakeModel`, so the actual
select/filter is exercised end-to-end instead of a mock hard-coding the rows.

get_by_alias targets an ARRAY column (`_AliasModel.aliases`), which SQLite
cannot round-trip, so its session is mocked — but the REAL
`select(model).where(model.aliases.any(alias))` is built and asserted at the
statement level (no more patching out `select`).

add()/remove() ordering tests keep a mock session: observing the
add→commit→refresh call order is exactly what a mock is for.
"""

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import ARRAY, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ---------------------------------------------------------------------------
# Ensure shared.bblogger is mocked BEFORE any src imports
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
# Real SQLAlchemy declarative models used only in these tests.
# ---------------------------------------------------------------------------


class _TestBase(DeclarativeBase):
    pass


class _FakeModel(_TestBase):
    """Minimal SQLite-compatible model (plain columns → real round-trips)."""

    __tablename__ = "__test_fake__"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<FakeModel id={self.id}>"


class _AliasModel(_TestBase):
    """Model with a real ARRAY 'aliases' column (Postgres-only round-trip)."""

    __tablename__ = "__test_alias__"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=True)
    aliases: Mapped[list] = mapped_column(ARRAY(String), nullable=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo() -> GenericRepository:
    """Return a GenericRepository wired to _FakeModel."""
    return GenericRepository(_FakeModel)


@pytest.fixture
async def async_engine():
    """Real SQLite engine with only the SQLite-compatible _FakeModel table."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(_TestBase.metadata.create_all, tables=[_FakeModel.__table__])
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(async_engine) -> AsyncSession:
    factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
def mock_session() -> AsyncMock:
    """Mock async session — for order-of-operations and NotImplementedError tests."""
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
# TestListAll — real round-trips
# ---------------------------------------------------------------------------


class TestListAll:
    async def test_list_all_returns_all_rows(self, repo, db_session):
        db_session.add_all([_FakeModel(name="alpha"), _FakeModel(name="beta")])
        await db_session.commit()

        result = await repo.list_all(db_session)

        assert {r.name for r in result} == {"alpha", "beta"}

    async def test_list_all_returns_empty_list_when_no_rows(self, repo, db_session):
        result = await repo.list_all(db_session)

        assert list(result) == []

    async def test_list_all_single_entity(self, repo, db_session):
        solo = _FakeModel(name="solo")
        db_session.add(solo)
        await db_session.commit()

        result = await repo.list_all(db_session)

        assert len(result) == 1
        assert result[0].name == "solo"


# ---------------------------------------------------------------------------
# TestGetById — real round-trips
# ---------------------------------------------------------------------------


class TestGetById:
    async def test_get_by_id_returns_entity_when_found(self, repo, db_session):
        entity = _FakeModel(name="found")
        db_session.add(entity)
        await db_session.commit()

        result = await repo.get_by_id(db_session, entity.id)

        assert result is not None
        assert result.name == "found"

    async def test_get_by_id_returns_none_when_not_found(self, repo, db_session):
        result = await repo.get_by_id(db_session, 999)

        assert result is None


# ---------------------------------------------------------------------------
# TestGetByName — real round-trips
# ---------------------------------------------------------------------------


class TestGetByName:
    async def test_get_by_name_returns_entity_when_found(self, repo, db_session):
        db_session.add_all([_FakeModel(name="gamma"), _FakeModel(name="delta")])
        await db_session.commit()

        result = await repo.get_by_name(db_session, "gamma")

        assert result is not None
        assert result.name == "gamma"

    async def test_get_by_name_returns_none_when_not_found(self, repo, db_session):
        db_session.add(_FakeModel(name="present"))
        await db_session.commit()

        result = await repo.get_by_name(db_session, "missing")

        assert result is None

    async def test_get_by_name_discriminates(self, repo, db_session):
        """The name filter genuinely selects the matching row, not just any row."""
        db_session.add_all([_FakeModel(name="one"), _FakeModel(name="two")])
        await db_session.commit()

        assert (await repo.get_by_name(db_session, "two")).name == "two"
        assert (await repo.get_by_name(db_session, "one")).name == "one"


# ---------------------------------------------------------------------------
# TestGetByNames — real round-trips
# ---------------------------------------------------------------------------


class TestGetByNames:
    async def test_get_by_names_returns_matching_rows(self, repo, db_session):
        db_session.add_all([_FakeModel(name="a"), _FakeModel(name="b"), _FakeModel(name="c")])
        await db_session.commit()

        result = await repo.get_by_names(db_session, ["a", "c", "missing"])

        assert {r.name for r in result} == {"a", "c"}

    async def test_get_by_names_empty_input_returns_empty(self, repo, db_session):
        result = await repo.get_by_names(db_session, [])

        assert result == []


# ---------------------------------------------------------------------------
# TestAdd — real round-trip + justified ordering mock
# ---------------------------------------------------------------------------


class TestAdd:
    async def test_add_persists_and_returns_entity(self, repo, db_session):
        entity = _FakeModel(name="new")

        result = await repo.add(db_session, entity)

        assert result is entity
        assert entity.id is not None
        db_session.expunge_all()
        fetched = await repo.get_by_id(db_session, entity.id)
        assert fetched is not None
        assert fetched.name == "new"

    @pytest.mark.asyncio
    async def test_add_order_of_operations(self, repo, mock_session):
        """add() must call add -> commit -> refresh in that order.

        Justified mock: verifying call *ordering* is precisely what a spy session
        is for; a real session cannot observe the intermediate sequence.
        """
        call_order: list[str] = []
        mock_session.add = MagicMock(side_effect=lambda _: call_order.append("add"))
        mock_session.commit = AsyncMock(side_effect=lambda: call_order.append("commit"))
        mock_session.refresh = AsyncMock(side_effect=lambda _: call_order.append("refresh"))

        await repo.add(mock_session, _FakeModel(name="order"))

        assert call_order == ["add", "commit", "refresh"]


# ---------------------------------------------------------------------------
# TestRemove — real round-trip + justified ordering mock
# ---------------------------------------------------------------------------


class TestRemove:
    async def test_remove_deletes_row(self, repo, db_session):
        entity = _FakeModel(name="delete_me")
        db_session.add(entity)
        await db_session.commit()
        entity_id = entity.id

        result = await repo.remove(db_session, entity)

        assert result is None
        assert await repo.get_by_id(db_session, entity_id) is None

    @pytest.mark.asyncio
    async def test_remove_delete_before_commit(self, repo, mock_session):
        """remove() must call delete before commit (justified ordering mock)."""
        call_order: list[str] = []
        mock_session.delete = AsyncMock(side_effect=lambda _: call_order.append("delete"))
        mock_session.commit = AsyncMock(side_effect=lambda: call_order.append("commit"))

        await repo.remove(mock_session, _FakeModel(name="order_check"))

        assert call_order == ["delete", "commit"]


# ---------------------------------------------------------------------------
# TestCreateOrUpdate
# ---------------------------------------------------------------------------


class TestCreateOrUpdate:
    @pytest.mark.asyncio
    async def test_create_or_update_raises_not_implemented(self, repo, mock_session):
        """Base GenericRepository.create_or_update() must raise NotImplementedError."""
        with pytest.raises(NotImplementedError):
            await repo.create_or_update(mock_session, {"key": "value"})


# ---------------------------------------------------------------------------
# TestGetByAlias — real statement building (ARRAY blocks SQLite round-trip)
# ---------------------------------------------------------------------------


class TestGetByAlias:
    """get_by_alias builds select(model).where(model.aliases.any(alias)).

    ARRAY columns cannot round-trip on SQLite, so only the session is mocked;
    the REAL select/where/any is built and asserted at the statement level
    (the previous version patched out `select` entirely, testing only plumbing).
    """

    @pytest.fixture
    def alias_repo(self) -> GenericRepository:
        return GenericRepository(_AliasModel)

    def _capturing_session(self, rows) -> AsyncMock:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_scalars_result(rows))
        return session

    @pytest.mark.asyncio
    async def test_get_by_alias_returns_entity_when_found(self, alias_repo):
        entity = _AliasModel(id=5, name="aliased", aliases=["some_alias"])
        session = self._capturing_session([entity])

        result = await alias_repo.get_by_alias(session, "some_alias")

        assert result is entity

    @pytest.mark.asyncio
    async def test_get_by_alias_returns_none_when_not_found(self, alias_repo):
        session = self._capturing_session([])

        result = await alias_repo.get_by_alias(session, "ghost_alias")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_by_alias_emits_any_on_aliases_column(self, alias_repo):
        """The emitted statement must filter with `<alias> = ANY(aliases)`."""
        session = self._capturing_session([])

        await alias_repo.get_by_alias(session, "wanted")

        session.execute.assert_awaited_once()
        stmt = session.execute.call_args[0][0]
        compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "aliases" in compiled
        assert "ANY" in compiled.upper()
        assert "wanted" in compiled


# ---------------------------------------------------------------------------
# TestRepositoryInit
# ---------------------------------------------------------------------------


class TestRepositoryInit:
    def test_init_stores_model_class(self):
        local_repo = GenericRepository(_FakeModel)
        assert local_repo._model is _FakeModel

    def test_init_with_different_model(self):
        local_repo = GenericRepository(_AliasModel)
        assert local_repo._model is _AliasModel
