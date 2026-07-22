"""
Unit tests for bot-core utility modules:
  - utils.data_loader
  - utils.emoji_service
  - utils.job_executor

IMPORTANT: shared.bblogger must be mocked BEFORE any source imports.
The conftest.py already does this via sys.modules patching, so the
module-level mock below is a belt-and-suspenders guard for direct runs.
"""

import json
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import respx

# ---------------------------------------------------------------------------
# Mock shared / shared.bblogger BEFORE importing any source modules.
# conftest.py does this at collection time; we repeat it here so the file
# can also be run standalone without the conftest.
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
# Ensure the src directory is on the path (conftest.py does this too).
# ---------------------------------------------------------------------------
_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


# ===========================================================================
# Tests for utils.emoji_service.EmojiService
# ===========================================================================


class TestEmojiServiceInit:
    """Tests for EmojiService.__init__."""

    def test_init_success(self, monkeypatch):
        """EmojiService initialises when both env vars are set."""
        monkeypatch.setenv("BOTTOKEN", "my-bot-token")
        monkeypatch.setenv("BOTAPPID", "123456789")

        from utils.emoji_service import EmojiService

        svc = EmojiService()
        assert svc.bot_token == "my-bot-token"
        assert svc.app_id == "123456789"
        assert svc.emojis_cache == {}

    def test_init_missing_bot_token(self, monkeypatch):
        """EmojiService raises ValueError when BOTTOKEN is absent."""
        monkeypatch.delenv("BOTTOKEN", raising=False)
        monkeypatch.setenv("BOTAPPID", "123456789")

        from utils.emoji_service import EmojiService

        with pytest.raises(ValueError, match="BOTTOKEN"):
            EmojiService()

    def test_init_missing_app_id(self, monkeypatch):
        """EmojiService raises ValueError when BOTAPPID is absent."""
        monkeypatch.setenv("BOTTOKEN", "my-bot-token")
        monkeypatch.delenv("BOTAPPID", raising=False)

        from utils.emoji_service import EmojiService

        with pytest.raises(ValueError, match="BOTAPPID"):
            EmojiService()


class TestEmojiServiceNormalize:
    """Tests for EmojiService.normalize_emoji_name."""

    @pytest.fixture()
    def svc(self, monkeypatch):
        monkeypatch.setenv("BOTTOKEN", "tok")
        monkeypatch.setenv("BOTAPPID", "app")
        from utils.emoji_service import EmojiService

        return EmojiService()

    def test_lowercase_simple(self, svc):
        assert svc.normalize_emoji_name("Shield") == "shield"

    def test_spaces_removed(self, svc):
        """Spaces and non-alphanumerics are removed (no underscores per current impl)."""
        result = svc.normalize_emoji_name("Mass Driver MD 10")
        assert result == "massdrivermd10"

    def test_special_chars_removed(self, svc):
        result = svc.normalize_emoji_name("E6 D-X Plating")
        assert result == "e6dxplating"

    def test_accented_chars_stripped(self, svc):
        result = svc.normalize_emoji_name("Résumé")
        assert result == "resume"

    def test_already_normalized(self, svc):
        assert svc.normalize_emoji_name("shield1") == "shield1"

    def test_empty_string(self, svc):
        assert svc.normalize_emoji_name("") == ""

    def test_numbers_preserved(self, svc):
        assert svc.normalize_emoji_name("Mk2") == "mk2"


class TestEmojiServiceFetchApplicationEmojis:
    """Tests for EmojiService.fetch_application_emojis.

    Uses respx transport-level mocking so the exact Discord emoji-list endpoint
    (URL + Bot auth header) is asserted, instead of a MagicMock httpx.Client that
    accepted any request.  The real (sync) httpx.Client runs against the mock.
    """

    @pytest.fixture()
    def svc(self, monkeypatch):
        monkeypatch.setenv("BOTTOKEN", "tok")
        monkeypatch.setenv("BOTAPPID", "app")
        from utils.emoji_service import EmojiService

        return EmojiService()

    @staticmethod
    def _emoji_url(svc) -> str:
        return f"https://discord.com/api/v10/applications/{svc.app_id}/emojis"

    def test_fetch_list_response(self, svc):
        """Handles a direct list response from Discord and hits the correct URL + auth header."""
        with respx.mock(assert_all_called=True, assert_all_mocked=True) as router:
            route = router.get(self._emoji_url(svc)).respond(
                200, json=[{"name": "Shield", "id": "111"}, {"name": "Laser", "id": "222"}]
            )
            result = svc.fetch_application_emojis()

        assert result == {"shield": "111", "laser": "222"}
        assert route.calls.last.request.headers["Authorization"] == f"Bot {svc.bot_token}"

    def test_fetch_items_wrapper_response(self, svc):
        """Handles a dict with 'items' key from Discord."""
        with respx.mock(assert_all_called=True, assert_all_mocked=True) as router:
            router.get(self._emoji_url(svc)).respond(200, json={"items": [{"name": "Cannon", "id": "333"}]})
            result = svc.fetch_application_emojis()

        assert result == {"cannon": "333"}

    def test_fetch_empty_list(self, svc):
        """Returns empty dict when Discord returns no emojis."""
        with respx.mock(assert_all_called=True, assert_all_mocked=True) as router:
            router.get(self._emoji_url(svc)).respond(200, json=[])
            result = svc.fetch_application_emojis()

        assert result == {}

    def test_fetch_skips_items_missing_name_or_id(self, svc):
        """Entries missing name or id are silently skipped."""
        with respx.mock(assert_all_called=True, assert_all_mocked=True) as router:
            router.get(self._emoji_url(svc)).respond(
                200, json=[{"name": "Good", "id": "999"}, {"name": "NoId"}, {"id": "NoName"}]
            )
            result = svc.fetch_application_emojis()

        assert result == {"good": "999"}

    def test_fetch_http_error_raises_runtime_error(self, svc):
        """A real transport ConnectError is wrapped in RuntimeError."""
        import httpx

        with (
            respx.mock(assert_all_called=True, assert_all_mocked=True) as router,
            pytest.raises(RuntimeError, match="Discord API request failed"),
        ):
            router.get(self._emoji_url(svc)).mock(side_effect=httpx.ConnectError("connection refused"))
            svc.fetch_application_emojis()

    def test_fetch_http_500_raises_runtime_error(self, svc):
        """A 500 response (raise_for_status → HTTPStatusError) is wrapped in RuntimeError."""
        with (
            respx.mock(assert_all_called=True, assert_all_mocked=True) as router,
            pytest.raises(RuntimeError, match="Discord API request failed"),
        ):
            router.get(self._emoji_url(svc)).respond(500)
            svc.fetch_application_emojis()

    def test_fetch_generic_exception_raises_runtime_error(self, svc):
        """Malformed (non-JSON) body → JSON parse error wrapped in RuntimeError."""
        with (
            respx.mock(assert_all_called=True, assert_all_mocked=True) as router,
            pytest.raises(RuntimeError, match="Failed to process emoji data"),
        ):
            router.get(self._emoji_url(svc)).respond(200, text="not valid json {{{")
            svc.fetch_application_emojis()


class TestEmojiServiceLoadAndResolve:
    """Tests for EmojiService.load_emojis and resolve_emoji."""

    @pytest.fixture()
    def svc(self, monkeypatch):
        monkeypatch.setenv("BOTTOKEN", "tok")
        monkeypatch.setenv("BOTAPPID", "app")
        from utils.emoji_service import EmojiService

        return EmojiService()

    def test_load_emojis_populates_cache(self, svc):
        svc.fetch_application_emojis = MagicMock(return_value={"shield": "111"})
        svc.load_emojis()
        assert svc.emojis_cache == {"shield": "111"}

    def test_resolve_emoji_found(self, svc):
        svc.emojis_cache = {"shield": "111"}
        result = svc.resolve_emoji("Shield")
        assert result == "<:shield:111>"

    def test_resolve_emoji_not_found_returns_none(self, svc):
        svc.emojis_cache = {"laser": "222"}
        result = svc.resolve_emoji("Cannon")
        assert result is None

    def test_resolve_emoji_loads_if_cache_empty(self, svc):
        """When cache is empty, resolve_emoji triggers load_emojis first."""
        svc.emojis_cache = {}
        svc.load_emojis = MagicMock(side_effect=lambda: svc.emojis_cache.update({"cannon": "333"}))
        result = svc.resolve_emoji("Cannon")
        svc.load_emojis.assert_called_once()
        assert result == "<:cannon:333>"

    def test_get_available_emojis_returns_copy(self, svc):
        svc.emojis_cache = {"a": "1", "b": "2"}
        copy = svc.get_available_emojis()
        assert copy == {"a": "1", "b": "2"}
        # Mutating the copy should not affect the cache
        copy["c"] = "3"
        assert "c" not in svc.emojis_cache


# ===========================================================================
# Tests for utils.data_loader
# ===========================================================================


class TestGetEmojiService:
    """Tests for data_loader.get_emoji_service singleton."""

    def test_returns_emoji_service_instance(self, monkeypatch):
        monkeypatch.setenv("BOTTOKEN", "tok")
        monkeypatch.setenv("BOTAPPID", "app")

        import utils.data_loader as dl

        # Reset the global so we get a fresh singleton
        dl._emoji_service = None
        svc = dl.get_emoji_service()

        from utils.emoji_service import EmojiService

        assert isinstance(svc, EmojiService)

    def test_returns_same_instance_on_second_call(self, monkeypatch):
        monkeypatch.setenv("BOTTOKEN", "tok")
        monkeypatch.setenv("BOTAPPID", "app")

        import utils.data_loader as dl

        dl._emoji_service = None
        first = dl.get_emoji_service()
        second = dl.get_emoji_service()
        assert first is second


class TestGetRepository:
    """Tests for data_loader.get_repository."""

    def test_raises_for_missing_module(self):
        import utils.data_loader as dl

        with pytest.raises(RuntimeError, match="No repository module"):
            dl.get_repository("nonexistent_things")

    def test_raises_for_missing_class(self):
        """Module exists but the expected class name is absent."""
        import utils.data_loader as dl

        fake_mod = types.ModuleType("persist.repositories.widgets_repository")
        # No WidgetRepository class on the module
        with (
            patch.dict(sys.modules, {"persist.repositories.widgets_repository": fake_mod}),
            pytest.raises(RuntimeError, match="does not export class"),
        ):
            dl.get_repository("widgets")

    def test_returns_repository_instance(self):
        """Happy path: module + class exist, returns instantiated repo."""
        import utils.data_loader as dl

        fake_repo_cls = MagicMock(return_value=MagicMock())
        fake_mod = types.ModuleType("persist.repositories.ships_repository")
        fake_mod.ShipRepository = fake_repo_cls  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"persist.repositories.ships_repository": fake_mod}):
            repo = dl.get_repository("ships")

        fake_repo_cls.assert_called_once()
        assert repo is fake_repo_cls.return_value

    def test_singularises_trailing_s(self):
        """Category 'modules' → class 'ModuleRepository' (not ModulesRepository)."""
        import utils.data_loader as dl

        fake_repo_cls = MagicMock(return_value=MagicMock())
        fake_mod = types.ModuleType("persist.repositories.modules_repository")
        fake_mod.ModuleRepository = fake_repo_cls  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"persist.repositories.modules_repository": fake_mod}):
            repo = dl.get_repository("modules")

        assert repo is fake_repo_cls.return_value

    def test_no_singularisation_when_no_trailing_s(self):
        """Category without trailing 's' is used as-is for class name."""
        import utils.data_loader as dl

        fake_repo_cls = MagicMock(return_value=MagicMock())
        fake_mod = types.ModuleType("persist.repositories.crew_repository")
        fake_mod.CrewRepository = fake_repo_cls  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"persist.repositories.crew_repository": fake_mod}):
            repo = dl.get_repository("crew")

        assert repo is fake_repo_cls.return_value


class TestLoadFolder:
    """Tests for data_loader.load_folder."""

    @pytest.fixture()
    def mock_repo(self):
        repo = MagicMock()
        repo.create_or_update = AsyncMock(return_value=MagicMock(repr="obj"))
        return repo

    @pytest.fixture()
    def mock_db_session(self):
        """A fake async context-manager session."""
        session = AsyncMock()
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    async def test_loads_json_files(self, tmp_path, mock_repo, mock_db_session):
        """All valid JSON files in the folder are upserted."""
        import utils.data_loader as dl

        (tmp_path / "item.json").write_text(json.dumps({"name": "sword"}))
        (tmp_path / "item2.json").write_text(json.dumps({"name": "shield"}))

        with patch("utils.data_loader.db_manager") as mock_mgr:
            mock_mgr.get_session.return_value = mock_db_session
            await dl.load_folder(mock_repo, tmp_path)

        assert mock_repo.create_or_update.call_count == 2

    async def test_skips_invalid_json(self, tmp_path, mock_repo, mock_db_session):
        """Files with invalid JSON are skipped without raising."""
        import utils.data_loader as dl

        (tmp_path / "bad.json").write_text("not valid json {{{")
        (tmp_path / "good.json").write_text(json.dumps({"name": "blade"}))

        with patch("utils.data_loader.db_manager") as mock_mgr:
            mock_mgr.get_session.return_value = mock_db_session
            await dl.load_folder(mock_repo, tmp_path)

        assert mock_repo.create_or_update.call_count == 1

    async def test_empty_folder_does_nothing(self, tmp_path, mock_repo, mock_db_session):
        """No files in directory → no upserts."""
        import utils.data_loader as dl

        with patch("utils.data_loader.db_manager") as mock_mgr:
            mock_mgr.get_session.return_value = mock_db_session
            await dl.load_folder(mock_repo, tmp_path)

        mock_repo.create_or_update.assert_not_called()


class TestResolveEmojis:
    """Tests for data_loader._resolve_emojis (internal helper)."""

    def test_resolves_emoji_for_named_dict(self, monkeypatch):
        import utils.data_loader as dl

        mock_svc = MagicMock()
        mock_svc.resolve_emoji.return_value = "<:shield:111>"
        dl._emoji_service = mock_svc

        obj = {"name": "Shield", "emoji": "old"}
        result = dl._resolve_emojis(obj)

        assert result["emoji"] == "<:shield:111>"
        mock_svc.resolve_emoji.assert_called_once_with("Shield")

    def test_returns_obj_unchanged_when_no_name(self, monkeypatch):
        import utils.data_loader as dl

        mock_svc = MagicMock()
        dl._emoji_service = mock_svc

        obj = {"type": "weapon"}
        result = dl._resolve_emojis(obj)

        assert result == {"type": "weapon"}
        mock_svc.resolve_emoji.assert_not_called()

    def test_does_not_overwrite_if_no_resolved_emoji(self, monkeypatch):
        import utils.data_loader as dl

        mock_svc = MagicMock()
        mock_svc.resolve_emoji.return_value = None
        dl._emoji_service = mock_svc

        obj = {"name": "Unknown", "emoji": "existing"}
        result = dl._resolve_emojis(obj)

        # emoji field untouched because resolve returned None
        assert result["emoji"] == "existing"

    def test_handles_non_dict_input(self):
        import utils.data_loader as dl

        # Non-dict input is returned unchanged
        result = dl._resolve_emojis(["a", "b"])
        assert result == ["a", "b"]


class TestLoadData:
    """Tests for data_loader.load_data."""

    @pytest.fixture()
    def mock_db_session(self):
        session = AsyncMock()
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    async def test_raises_for_missing_category_dir(self, tmp_path):
        import utils.data_loader as dl

        mock_svc = MagicMock()
        mock_svc.load_emojis = MagicMock()
        dl._emoji_service = mock_svc

        with pytest.raises(ValueError, match="No such data directory"):
            await dl.load_data("nonexistent", data_root=tmp_path)

    async def test_returns_results_for_valid_files(self, tmp_path, mock_db_session):
        import utils.data_loader as dl

        # Create category dir with one JSON file
        cat_dir = tmp_path / "ships"
        cat_dir.mkdir()
        (cat_dir / "ship1.json").write_text(json.dumps({"name": "Falcon"}))

        mock_svc = MagicMock()
        mock_svc.load_emojis = MagicMock()
        mock_svc.resolve_emoji.return_value = None
        dl._emoji_service = mock_svc

        mock_repo = MagicMock()
        upserted = MagicMock()
        upserted.__repr__ = lambda self: "Ship(Falcon)"
        mock_repo.create_or_update = AsyncMock(return_value=upserted)

        with (
            patch("utils.data_loader.get_repository", return_value=mock_repo),
            patch("utils.data_loader.db_manager") as mock_mgr,
        ):
            mock_mgr.get_session.return_value = mock_db_session
            results = await dl.load_data("ships", data_root=tmp_path)

        assert len(results) == 1
        assert "Falcon" in results[0] or "ship1.json" in results[0]

    async def test_skips_invalid_json_and_records_warning(self, tmp_path, mock_db_session):
        import utils.data_loader as dl

        cat_dir = tmp_path / "modules"
        cat_dir.mkdir()
        (cat_dir / "bad.json").write_text("{not json}")

        mock_svc = MagicMock()
        mock_svc.load_emojis = MagicMock()
        dl._emoji_service = mock_svc

        mock_repo = MagicMock()

        with (
            patch("utils.data_loader.get_repository", return_value=mock_repo),
            patch("utils.data_loader.db_manager") as mock_mgr,
        ):
            mock_mgr.get_session.return_value = mock_db_session
            results = await dl.load_data("modules", data_root=tmp_path)

        assert any("Skipping invalid JSON" in r for r in results)
        mock_repo.create_or_update.assert_not_called()

    async def test_records_upsert_errors(self, tmp_path, mock_db_session):
        import utils.data_loader as dl

        cat_dir = tmp_path / "ships"
        cat_dir.mkdir()
        (cat_dir / "s.json").write_text(json.dumps({"name": "Breaker"}))

        mock_svc = MagicMock()
        mock_svc.load_emojis = MagicMock()
        mock_svc.resolve_emoji.return_value = None
        dl._emoji_service = mock_svc

        mock_repo = MagicMock()
        mock_repo.create_or_update = AsyncMock(side_effect=RuntimeError("DB exploded"))

        with (
            patch("utils.data_loader.get_repository", return_value=mock_repo),
            patch("utils.data_loader.db_manager") as mock_mgr,
        ):
            mock_mgr.get_session.return_value = mock_db_session
            results = await dl.load_data("ships", data_root=tmp_path)

        assert any("Error upserting" in r for r in results)

    async def test_emoji_load_failure_does_not_abort(self, tmp_path, mock_db_session):
        """If emoji pre-loading raises, load_data still proceeds with data loading."""
        import utils.data_loader as dl

        cat_dir = tmp_path / "ships"
        cat_dir.mkdir()
        (cat_dir / "s.json").write_text(json.dumps({"name": "Raptor"}))

        mock_svc = MagicMock()
        mock_svc.load_emojis = MagicMock(side_effect=RuntimeError("discord down"))
        mock_svc.resolve_emoji.return_value = None
        dl._emoji_service = mock_svc

        mock_repo = MagicMock()
        upserted = MagicMock()
        upserted.__repr__ = lambda self: "Ship(Raptor)"
        mock_repo.create_or_update = AsyncMock(return_value=upserted)

        with (
            patch("utils.data_loader.get_repository", return_value=mock_repo),
            patch("utils.data_loader.db_manager") as mock_mgr,
        ):
            mock_mgr.get_session.return_value = mock_db_session
            results = await dl.load_data("ships", data_root=tmp_path)

        # Even though emoji loading failed, the upsert should have happened
        assert len(results) == 1


# ===========================================================================
# Tests for utils.job_executor
# ===========================================================================


class TestJobExecutorDispatch:
    """Tests for JobExecutor.execute dispatch logic."""

    async def test_dispatches_shop_refresh_job(self):
        """shop_refresh payload routes to execute_shop_refresh_job."""
        from utils.job_executor import JobExecutor

        executor = JobExecutor()
        payload = {"job_type": "shop_refresh", "guild_id": "g1"}

        mock_fn = AsyncMock(return_value=None)
        with patch("utils.job_executor.execute_shop_refresh_job", mock_fn):
            await executor.execute("job-001", payload)

        mock_fn.assert_awaited_once_with("job-001", payload)

    async def test_generic_job_runs_without_dispatch(self):
        """Unrecognized payloads complete without calling any typed executor."""
        from utils.job_executor import JobExecutor

        executor = JobExecutor()
        payload = {"job_type": "some_other_type"}

        mock_fn = AsyncMock()
        with patch("utils.job_executor.execute_shop_refresh_job", mock_fn):
            await executor.execute("job-002", payload)

        mock_fn.assert_not_called()

    async def test_execute_swallows_exceptions_and_logs(self):
        """Exceptions from the executor are caught; no exception propagates."""
        from utils.job_executor import JobExecutor

        executor = JobExecutor()
        payload = {"job_type": "shop_refresh"}

        boom = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("utils.job_executor.execute_shop_refresh_job", boom):
            # Should not raise
            await executor.execute("job-err", payload)

    async def test_empty_payload_runs_generic_path(self):
        """Empty payload dict follows the generic fallback path."""
        from utils.job_executor import JobExecutor

        executor = JobExecutor()
        mock_fn = AsyncMock()
        with patch("utils.job_executor.execute_shop_refresh_job", mock_fn):
            await executor.execute("job-empty", {})

        mock_fn.assert_not_called()


class TestRunJobWrapper:
    """Tests for the module-level run_job wrapper."""

    async def test_run_job_delegates_to_executor(self):
        """run_job calls executor.execute with the same args."""
        from utils import job_executor as je

        mock_execute = AsyncMock()
        je._executor.execute = mock_execute

        await je.run_job("job-rj", {"job_type": "shop_refresh"})

        mock_execute.assert_awaited_once_with("job-rj", {"job_type": "shop_refresh"})
