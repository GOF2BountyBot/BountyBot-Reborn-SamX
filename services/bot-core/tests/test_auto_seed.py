"""
Unit tests for the auto-seeding functionality in utils/auto_seeder.py.

Strategy
--------
* All DB and loader I/O is mocked — no real database connections required.
* ``shared.bblogger`` is already patched to a MagicMock by conftest.py, so
  we can import ``utils.auto_seeder`` without a real ``shared`` package on
  the path.
* For ``TestTableIsEmpty``: we patch the entire ``session.execute`` path so
  that SQLAlchemy never validates the mock model against a real table.
* For ``TestAutoSeedData``: we patch ``table_is_empty`` directly (same module),
  ``get_repository``, and ``load_data`` to keep tests fully unit-level.
"""

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Guard: ensure shared / shared.bblogger are mocked before any src import.
# conftest.py already does this for the whole session; the check below is a
# belt-and-suspenders guard for direct pytest invocations of this file.
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")

    def _make_mock_logger(name: str = "test") -> MagicMock:
        logger = MagicMock()
        for method in ("info", "debug", "warning", "error", "trace", "critical"):
            setattr(logger, method, MagicMock())
        return logger

    _mock_bblogger.get_logger = _make_mock_logger
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session_ctx(scalar_value=0) -> MagicMock:
    """
    Return a mock async context-manager for db_manager.get_session().

    The mock session's execute() bypasses SQLAlchemy query building
    entirely and returns a result with a configurable scalar.
    """
    result = MagicMock()
    result.scalar.return_value = scalar_value

    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_repo() -> MagicMock:
    """Return a mock repository."""
    repo = MagicMock()
    return repo


# ---------------------------------------------------------------------------
# Tests for table_is_empty
# ---------------------------------------------------------------------------


class TestTableIsEmpty:
    """Tests for the ``table_is_empty`` helper."""

    async def test_returns_true_when_count_is_zero(self):
        """scalar()==0 → table is empty."""
        from utils.auto_seeder import table_is_empty

        repo = _make_repo()
        ctx = _make_session_ctx(scalar_value=0)

        # Patch both db_manager.get_session AND the SQLAlchemy select expression
        # so the MagicMock model passes select_from() validation.
        with (
            patch("utils.auto_seeder.db_manager") as mock_mgr,
            patch("utils.auto_seeder.select") as mock_select,
        ):
            mock_mgr.get_session.return_value = ctx
            # Make select(...).select_from(...) return a sentinel so execute() accepts it
            mock_select.return_value.select_from.return_value = MagicMock()
            result = await table_is_empty(repo)

        assert result is True

    async def test_returns_false_when_count_is_positive(self):
        """scalar()>0 → table has rows."""
        from utils.auto_seeder import table_is_empty

        repo = _make_repo()
        ctx = _make_session_ctx(scalar_value=42)

        with (
            patch("utils.auto_seeder.db_manager") as mock_mgr,
            patch("utils.auto_seeder.select") as mock_select,
        ):
            mock_mgr.get_session.return_value = ctx
            mock_select.return_value.select_from.return_value = MagicMock()
            result = await table_is_empty(repo)

        assert result is False

    async def test_treats_none_scalar_as_empty(self):
        """scalar()==None → treated as 0 (empty)."""
        from utils.auto_seeder import table_is_empty

        repo = _make_repo()
        ctx = _make_session_ctx(scalar_value=None)

        with (
            patch("utils.auto_seeder.db_manager") as mock_mgr,
            patch("utils.auto_seeder.select") as mock_select,
        ):
            mock_mgr.get_session.return_value = ctx
            mock_select.return_value.select_from.return_value = MagicMock()
            result = await table_is_empty(repo)

        assert result is True


# ---------------------------------------------------------------------------
# Tests for auto_seed_data
# ---------------------------------------------------------------------------


class TestAutoSeedData:
    """Tests for the ``auto_seed_data()`` startup seeder."""

    # ------------------------------------------------------------------
    # Happy-path: all tables empty → load_data called for every category
    # ------------------------------------------------------------------

    async def test_seeds_all_categories_when_tables_empty(self):
        """When every table is empty, load_data is called once per category."""
        from utils.auto_seeder import SEED_CATEGORIES, auto_seed_data

        repo = _make_repo()

        with (
            patch("utils.auto_seeder.get_repository", return_value=repo) as mock_get_repo,
            patch("utils.auto_seeder.table_is_empty", new=AsyncMock(return_value=True)),
            patch(
                "utils.auto_seeder.load_data",
                new_callable=AsyncMock,
                return_value=["item1"],
            ) as mock_load,
        ):
            await auto_seed_data()

        assert mock_get_repo.call_count == len(SEED_CATEGORIES)
        assert mock_load.call_count == len(SEED_CATEGORIES)
        called_categories = [call.args[0] for call in mock_load.call_args_list]
        assert called_categories == SEED_CATEGORIES

    # ------------------------------------------------------------------
    # Idempotency: all tables populated → load_data never called
    # ------------------------------------------------------------------

    async def test_skips_all_categories_when_tables_populated(self):
        """When every table already has data, load_data is never called."""
        from utils.auto_seeder import auto_seed_data

        repo = _make_repo()

        with (
            patch("utils.auto_seeder.get_repository", return_value=repo),
            patch("utils.auto_seeder.table_is_empty", new=AsyncMock(return_value=False)),
            patch("utils.auto_seeder.load_data", new_callable=AsyncMock) as mock_load,
        ):
            await auto_seed_data()

        mock_load.assert_not_called()

    # ------------------------------------------------------------------
    # Partial: some empty, some populated
    # ------------------------------------------------------------------

    async def test_seeds_only_empty_categories(self):
        """load_data is called only for the categories whose tables are empty."""
        from utils.auto_seeder import SEED_CATEGORIES, auto_seed_data

        repo = _make_repo()
        # First call returns True (empty), rest return False (populated)
        empty_results = [True] + [False] * (len(SEED_CATEGORIES) - 1)
        empty_iter = iter(empty_results)

        async def _table_is_empty_side_effect(_repo):
            return next(empty_iter)

        with (
            patch("utils.auto_seeder.get_repository", return_value=repo),
            patch("utils.auto_seeder.table_is_empty", side_effect=_table_is_empty_side_effect),
            patch(
                "utils.auto_seeder.load_data",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_load,
        ):
            await auto_seed_data()

        # Only 'ship' (the first category in SEED_CATEGORIES) should be seeded
        mock_load.assert_called_once_with(SEED_CATEGORIES[0])

    # ------------------------------------------------------------------
    # Resilience: missing import_data directory → warning, continues
    # ------------------------------------------------------------------

    async def test_missing_import_data_dir_logs_warning_and_continues(self):
        """If load_data raises ValueError (no import_data dir), seeding continues."""
        from utils.auto_seeder import SEED_CATEGORIES, auto_seed_data

        repo = _make_repo()

        with (
            patch("utils.auto_seeder.get_repository", return_value=repo),
            patch("utils.auto_seeder.table_is_empty", new=AsyncMock(return_value=True)),
            patch(
                "utils.auto_seeder.load_data",
                new_callable=AsyncMock,
                side_effect=ValueError("No such data directory"),
            ) as mock_load,
        ):
            # Should NOT raise — errors are swallowed per category
            await auto_seed_data()

        # load_data was still attempted for every category
        assert mock_load.call_count == len(SEED_CATEGORIES)

    # ------------------------------------------------------------------
    # Resilience: get_repository raises → skips that category
    # ------------------------------------------------------------------

    async def test_repository_load_failure_skips_category(self):
        """If get_repository raises RuntimeError, that category is skipped."""
        from utils.auto_seeder import SEED_CATEGORIES, auto_seed_data

        def _get_repository_side_effect(category):
            if category == SEED_CATEGORIES[0]:
                raise RuntimeError("No repository module")
            return _make_repo()

        with (
            patch("utils.auto_seeder.get_repository", side_effect=_get_repository_side_effect),
            patch("utils.auto_seeder.table_is_empty", new=AsyncMock(return_value=True)),
            patch(
                "utils.auto_seeder.load_data",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_load,
        ):
            await auto_seed_data()

        # load_data should NOT have been called for the failing category
        called_categories = [call.args[0] for call in mock_load.call_args_list]
        assert SEED_CATEGORIES[0] not in called_categories
        # But all remaining categories should have been seeded
        assert len(called_categories) == len(SEED_CATEGORIES) - 1

    # ------------------------------------------------------------------
    # Resilience: table count query raises → skips that category
    # ------------------------------------------------------------------

    async def test_table_count_error_skips_category(self):
        """If table_is_empty raises, that category is skipped."""
        from utils.auto_seeder import SEED_CATEGORIES, auto_seed_data

        repo = _make_repo()
        call_counts = {"n": 0}

        async def _table_is_empty_side_effect(_repo):
            call_counts["n"] += 1
            if call_counts["n"] == 1:
                raise RuntimeError("DB exploded")
            return True  # empty for the rest

        with (
            patch("utils.auto_seeder.get_repository", return_value=repo),
            patch("utils.auto_seeder.table_is_empty", side_effect=_table_is_empty_side_effect),
            patch(
                "utils.auto_seeder.load_data",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_load,
        ):
            await auto_seed_data()

        called_categories = [call.args[0] for call in mock_load.call_args_list]
        # First category skipped due to DB error
        assert SEED_CATEGORIES[0] not in called_categories
        # Remaining categories processed
        assert len(called_categories) == len(SEED_CATEGORIES) - 1

    # ------------------------------------------------------------------
    # Resilience: load_data raises generic exception → continues
    # ------------------------------------------------------------------

    async def test_generic_seed_error_continues_to_next_category(self):
        """A non-ValueError exception from load_data is logged and the loop continues."""
        from utils.auto_seeder import SEED_CATEGORIES, auto_seed_data

        repo = _make_repo()
        call_counts = {"load": 0}

        async def _load_side_effect(category):
            call_counts["load"] += 1
            if category == SEED_CATEGORIES[0]:
                raise RuntimeError("Unexpected DB failure")
            return []

        with (
            patch("utils.auto_seeder.get_repository", return_value=repo),
            patch("utils.auto_seeder.table_is_empty", new=AsyncMock(return_value=True)),
            patch("utils.auto_seeder.load_data", side_effect=_load_side_effect),
        ):
            await auto_seed_data()

        # load_data was attempted for every category despite the first one failing
        assert call_counts["load"] == len(SEED_CATEGORIES)

    # ------------------------------------------------------------------
    # Correct categories list
    # ------------------------------------------------------------------

    def test_seed_categories_contains_all_seven(self):
        """SEED_CATEGORIES must include all 7 expected game-data categories."""
        from utils.auto_seeder import SEED_CATEGORIES

        expected = {
            "ship",
            "primary_weapon",
            "secondary_weapon",
            "turret_weapon",
            "module",
            "criminal",
            "system",
        }
        assert set(SEED_CATEGORIES) == expected
        assert len(SEED_CATEGORIES) == 7
