"""
Unit tests for bot-core utility modules:
  - utils.data_loader
  - utils.emoji_service
  - utils.job_executor
  - utils.executors.time_announcement_executor

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
    """Tests for EmojiService.fetch_application_emojis."""

    @pytest.fixture()
    def svc(self, monkeypatch):
        monkeypatch.setenv("BOTTOKEN", "tok")
        monkeypatch.setenv("BOTAPPID", "app")
        from utils.emoji_service import EmojiService

        return EmojiService()

    def test_fetch_list_response(self, svc):
        """Handles a direct list response from Discord."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"name": "Shield", "id": "111"},
            {"name": "Laser", "id": "222"},
        ]

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response

        with patch("utils.emoji_service.httpx.Client", return_value=mock_client):
            result = svc.fetch_application_emojis()

        assert result == {"shield": "111", "laser": "222"}

    def test_fetch_items_wrapper_response(self, svc):
        """Handles a dict with 'items' key from Discord."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "items": [
                {"name": "Cannon", "id": "333"},
            ]
        }

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response

        with patch("utils.emoji_service.httpx.Client", return_value=mock_client):
            result = svc.fetch_application_emojis()

        assert result == {"cannon": "333"}

    def test_fetch_empty_list(self, svc):
        """Returns empty dict when Discord returns no emojis."""
        mock_response = MagicMock()
        mock_response.json.return_value = []

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response

        with patch("utils.emoji_service.httpx.Client", return_value=mock_client):
            result = svc.fetch_application_emojis()

        assert result == {}

    def test_fetch_skips_items_missing_name_or_id(self, svc):
        """Entries missing name or id are silently skipped."""
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"name": "Good", "id": "999"},
            {"name": "NoId"},
            {"id": "NoName"},
        ]

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response

        with patch("utils.emoji_service.httpx.Client", return_value=mock_client):
            result = svc.fetch_application_emojis()

        assert result == {"good": "999"}

    def test_fetch_http_error_raises_runtime_error(self, svc):
        """HTTPError from httpx is wrapped in RuntimeError."""
        import httpx

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = httpx.HTTPError("connection refused")

        with (
            patch("utils.emoji_service.httpx.Client", return_value=mock_client),
            pytest.raises(RuntimeError, match="Discord API request failed"),
        ):
            svc.fetch_application_emojis()

    def test_fetch_generic_exception_raises_runtime_error(self, svc):
        """Unexpected exceptions during JSON processing are wrapped in RuntimeError."""
        mock_response = MagicMock()
        mock_response.json.side_effect = ValueError("bad json")

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response

        with (
            patch("utils.emoji_service.httpx.Client", return_value=mock_client),
            pytest.raises(RuntimeError, match="Failed to process emoji data"),
        ):
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

    async def test_dispatches_time_announcement_job(self):
        """time_announcement payload routes to execute_time_announcement_job."""
        from utils.job_executor import JobExecutor

        executor = JobExecutor()
        payload = {"job_type": "time_announcement", "guild_id": "g1", "channel_id": "c1"}

        mock_fn = AsyncMock(return_value=None)
        with patch("utils.job_executor.execute_time_announcement_job", mock_fn):
            await executor.execute("job-001", payload)

        mock_fn.assert_awaited_once_with("job-001", payload)

    async def test_generic_job_runs_without_dispatch(self):
        """Non-time_announcement payloads complete without calling the time executor."""
        from utils.job_executor import JobExecutor

        executor = JobExecutor()
        payload = {"job_type": "some_other_type"}

        mock_fn = AsyncMock()
        with patch("utils.job_executor.execute_time_announcement_job", mock_fn):
            await executor.execute("job-002", payload)

        mock_fn.assert_not_called()

    async def test_execute_swallows_exceptions_and_logs(self):
        """Exceptions from the executor are caught; no exception propagates."""
        from utils.job_executor import JobExecutor

        executor = JobExecutor()
        payload = {"job_type": "time_announcement"}

        boom = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("utils.job_executor.execute_time_announcement_job", boom):
            # Should not raise
            await executor.execute("job-err", payload)

    async def test_empty_payload_runs_generic_path(self):
        """Empty payload dict follows the generic fallback path."""
        from utils.job_executor import JobExecutor

        executor = JobExecutor()
        mock_fn = AsyncMock()
        with patch("utils.job_executor.execute_time_announcement_job", mock_fn):
            await executor.execute("job-empty", {})

        mock_fn.assert_not_called()


class TestRunJobWrapper:
    """Tests for the module-level run_job wrapper."""

    async def test_run_job_delegates_to_executor(self):
        """run_job calls executor.execute with the same args."""
        from utils import job_executor as je

        mock_execute = AsyncMock()
        je._executor.execute = mock_execute

        await je.run_job("job-rj", {"job_type": "time_announcement"})

        mock_execute.assert_awaited_once_with("job-rj", {"job_type": "time_announcement"})


# ===========================================================================
# Tests for utils.executors.time_announcement_executor
# ===========================================================================


class TestExecuteTimeAnnouncementJob:
    """Tests for execute_time_announcement_job."""

    @pytest.fixture()
    def _patch_env(self, monkeypatch):
        monkeypatch.setenv("EXECUTOR_HOST", "localhost")
        monkeypatch.setenv("EXECUTOR_PORT", "8000")

    def _make_async_client(self, get_status=200, post_status=201, put_status=200, post_json=None, put_json=None):
        """Build a mock httpx.AsyncClient."""
        get_resp = MagicMock()
        get_resp.status_code = get_status
        get_resp.text = ""

        post_resp = MagicMock()
        post_resp.status_code = post_status
        post_resp.text = ""
        post_resp.json.return_value = post_json or {"message_id": "new-msg-id"}
        post_resp.raise_for_status = MagicMock()

        put_resp = MagicMock()
        put_resp.status_code = put_status
        put_resp.text = ""
        put_resp.json.return_value = put_json or {"message_id": "existing-msg-id"}
        put_resp.raise_for_status = MagicMock()

        client = AsyncMock()
        client.get = AsyncMock(return_value=get_resp)
        client.post = AsyncMock(return_value=post_resp)
        client.put = AsyncMock(return_value=put_resp)

        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        return ctx, client, get_resp, post_resp, put_resp

    async def test_post_when_not_exists(self, _patch_env):
        """When GET returns non-200, a POST is issued to create the announcement."""
        from utils.executors.time_announcement_executor import execute_time_announcement_job

        ctx, client, _, _post_resp, _ = self._make_async_client(get_status=404)
        payload = {
            "guild_id": "g1",
            "channel_id": "c1",
            "current_time": "2026-01-01T00:00:00Z",
        }

        with patch("utils.executors.time_announcement_executor.httpx.AsyncClient", return_value=ctx):
            await execute_time_announcement_job("job-new", payload)

        client.post.assert_awaited_once()

    async def test_put_when_exists(self, _patch_env):
        """When GET returns 200, a PUT is issued to update the announcement."""
        from utils.executors.time_announcement_executor import execute_time_announcement_job

        ctx, client, _, _, _put_resp = self._make_async_client(get_status=200)
        payload = {
            "guild_id": "g1",
            "channel_id": "c1",
            "message_id": "existing-msg-id",
            "current_time": "2026-01-01T00:00:00Z",
        }

        with patch("utils.executors.time_announcement_executor.httpx.AsyncClient", return_value=ctx):
            await execute_time_announcement_job("job-update", payload)

        client.put.assert_awaited()

    async def test_message_id_param_included_in_get_when_present(self, _patch_env):
        """If message_id is in payload, it is passed as a GET query param."""
        from utils.executors.time_announcement_executor import execute_time_announcement_job

        ctx, client, _, _, _ = self._make_async_client(get_status=200)
        payload = {
            "guild_id": "g1",
            "channel_id": "c1",
            "message_id": "m999",
            "current_time": "2026-01-01T00:00:00Z",
        }

        with patch("utils.executors.time_announcement_executor.httpx.AsyncClient", return_value=ctx):
            await execute_time_announcement_job("job-mid", payload)

        call_kwargs = client.get.call_args
        # Accept that params are passed either as positional or keyword
        all_args = str(call_kwargs)
        assert "m999" in all_args

    async def test_get_timeout_raises(self, _patch_env):
        """TimeoutException on GET propagates upward."""
        import httpx
        from utils.executors.time_announcement_executor import execute_time_announcement_job

        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        payload = {"guild_id": "g1", "channel_id": "c1"}

        with (
            patch("utils.executors.time_announcement_executor.httpx.AsyncClient", return_value=ctx),
            pytest.raises(httpx.TimeoutException),
        ):
            await execute_time_announcement_job("job-timeout", payload)

    async def test_get_http_error_raises(self, _patch_env):
        """HTTPError on GET propagates upward."""
        import httpx
        from utils.executors.time_announcement_executor import execute_time_announcement_job

        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.HTTPError("bad gateway"))

        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        payload = {"guild_id": "g1", "channel_id": "c1"}

        with (
            patch("utils.executors.time_announcement_executor.httpx.AsyncClient", return_value=ctx),
            pytest.raises(httpx.HTTPError),
        ):
            await execute_time_announcement_job("job-http-err", payload)

    async def test_post_http_error_raises(self, _patch_env):
        """HTTPError on POST propagates upward."""
        import httpx
        from utils.executors.time_announcement_executor import execute_time_announcement_job

        get_resp = MagicMock()
        get_resp.status_code = 404
        get_resp.text = ""

        client = AsyncMock()
        client.get = AsyncMock(return_value=get_resp)
        client.post = AsyncMock(side_effect=httpx.HTTPError("post failed"))

        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        payload = {"guild_id": "g1", "channel_id": "c1"}

        with (
            patch("utils.executors.time_announcement_executor.httpx.AsyncClient", return_value=ctx),
            pytest.raises(httpx.HTTPError),
        ):
            await execute_time_announcement_job("job-post-err", payload)

    async def test_updates_job_args_after_first_create(self, _patch_env):
        """After a POST (new announcement), the executor updates the job args with the new message_id.

        P6-T8: update is now via direct scheduler.modify_job (no HTTP PUT loopback).
        Verifies that modify_job is called with args=[job_id, updated_payload] where
        updated_payload includes the new message_id.
        """
        from utils.executors.time_announcement_executor import execute_time_announcement_job

        get_resp = MagicMock()
        get_resp.status_code = 404
        get_resp.text = ""

        post_resp = MagicMock()
        post_resp.status_code = 201
        post_resp.text = ""
        post_resp.raise_for_status = MagicMock()
        post_resp.json.return_value = {"message_id": "brand-new-id"}

        client = AsyncMock()
        client.get = AsyncMock(return_value=get_resp)
        client.post = AsyncMock(return_value=post_resp)

        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        payload = {"guild_id": "g1", "channel_id": "c1", "current_time": "2026-01-01T00:00:00Z"}

        mock_scheduler = MagicMock()
        mock_scheduler.modify_job = MagicMock(return_value=None)

        with (
            patch("utils.executors.time_announcement_executor.httpx.AsyncClient", return_value=ctx),
            patch("utils.scheduler_holder.get_scheduler", return_value=mock_scheduler),
        ):
            await execute_time_announcement_job("job-create", payload)

        # P6-T8: modify_job must be called directly (no HTTP PUT loopback).
        mock_scheduler.modify_job.assert_called_once()
        call_args = mock_scheduler.modify_job.call_args
        # First positional arg is the job_id.
        job_id_arg = call_args.args[0] if call_args.args else call_args.kwargs.get("job_id")
        assert job_id_arg == "job-create", f"modify_job job_id={job_id_arg!r}, expected 'job-create'"
        # args= kwarg carries [job_id, new_payload].
        new_args = call_args.kwargs.get("args")
        assert new_args is not None, "modify_job must be called with args= kwarg"
        assert len(new_args) == 2, f"Expected args=[job_id, payload], got {new_args!r}"
        new_payload = new_args[1]
        assert new_payload.get("message_id") == "brand-new-id", (
            f"Updated payload must include message_id='brand-new-id', got {new_payload!r}"
        )

        # No HTTP PUT to the scheduler API (P6-T8 no-loopback assertion).
        all_put_calls = [str(c) for c in client.put.call_args_list] if hasattr(client, "put") else []
        assert not any("/jobs/" in c for c in all_put_calls), (
            f"HTTP PUT to /jobs/ should NOT be made (P6-T8 no-loopback); calls: {all_put_calls}"
        )

    async def test_uses_current_time_from_payload_if_provided(self, _patch_env):
        """If 'current_time' is in payload, it is used instead of datetime.now."""
        from utils.executors.time_announcement_executor import execute_time_announcement_job

        ctx, client, _, _post_resp2, _ = self._make_async_client(get_status=404)
        fixed_time = "2026-03-11T12:00:00Z"
        payload = {
            "guild_id": "g1",
            "channel_id": "c1",
            "current_time": fixed_time,
        }

        with patch("utils.executors.time_announcement_executor.httpx.AsyncClient", return_value=ctx):
            await execute_time_announcement_job("job-time", payload)

        post_call_kwargs = client.post.call_args
        body = post_call_kwargs.kwargs.get("json") or {}
        assert body.get("current_time") == fixed_time
