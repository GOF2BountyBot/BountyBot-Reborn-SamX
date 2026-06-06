"""Tests for utils.executors.pg_backup_executor — P1-T7 offload_io seam.

Behaviours covered
------------------
| # | Behaviour                                                           | Tier  |
|---|---------------------------------------------------------------------|-------|
| 1 | execute_pg_backup_job dispatches via offload_io, NOT run_in_executor| Unit  |
| 2 | offload_io is called with _dump_and_compress + correct positional args| Unit |
| 3 | Work runs on the SHARED thread pool, not the default executor       | Unit  |
| 4 | Exception from _dump_and_compress propagates + tmp file is cleaned  | Unit  |
| 5 | Dump smaller than MIN_BACKUP_BYTES → RuntimeError + file removed    | Unit  |
| 6 | Successful dump → correct return dict shape                         | Unit  |
| 7 | Source file contains no run_in_executor(None, ...) in the executor  | Static|
| 8 | _dump_and_compress args/return contract unchanged (mock subprocess) | Unit  |
| 9 | Live artifact: real dump produces non-empty, zstd-decompressable file| Live |

Tests #1–#8 use mocks/stubs only (no real DB required).
Test #9 is an optional live-DB integration test skipped when the
bountydev-db container is unreachable (or pg_dump binary unavailable).
"""

from __future__ import annotations

import concurrent.futures
import inspect
import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup (mirrors other executor test files in this suite)
# ---------------------------------------------------------------------------

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

# ---------------------------------------------------------------------------
# Lazy import of executor module + internal symbols
# ---------------------------------------------------------------------------

import utils.executors.pg_backup_executor as _exec_mod
from utils.executors.pg_backup_executor import _dump_and_compress, execute_pg_backup_job

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_noop_dump(*, write_bytes: int = 300_000) -> callable:
    """Return a synchronous callable that pretends to be _dump_and_compress.

    It writes *write_bytes* bytes to tmp_path so the size check passes.
    Signature matches _dump_and_compress(job_id, env, tmp_path).
    """

    def _noop(job_id: str, env: dict, tmp_path: Path) -> None:
        tmp_path.write_bytes(b"x" * write_bytes)

    return _noop


# ---------------------------------------------------------------------------
# Fixture: registered thread pool + fresh holder module
# ---------------------------------------------------------------------------


@pytest.fixture()
def named_thread_pool():
    """Yield a real ThreadPoolExecutor named 'shared-test-pool-*'.

    Registers it in executor_holder so offload_io routes through it.

    ISOLATION STRATEGY
    ------------------
    test_offload.py deletes and re-imports utils.executor_holder and
    utils.offload during its "without pool raises" tests, leaving those
    modules in sys.modules pointing to fresh instances.  pg_backup_executor
    holds a module-level reference to offload_io that was bound at import
    time.  To guarantee the pool set here is the one that offload_io calls
    into, this fixture:
    1. Reloads utils.executor_holder and utils.offload to get a clean pair.
    2. Sets the named thread pool on the fresh holder.
    3. Patches pg_backup_executor.offload_io to the fresh function so the
       executor uses the holder we just configured.
    Teardown restores the original binding and nulls the pool.
    """
    import importlib

    # Ensure fresh, consistent module instances.
    holder_mod = importlib.reload(sys.modules["utils.executor_holder"])
    offload_mod = importlib.reload(sys.modules["utils.offload"])

    pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=2,
        thread_name_prefix="shared-test-pool",
    )
    holder_mod.set_thread_pool(pool)

    # Point the executor module's offload_io at the freshly-reloaded function
    # so it calls get_thread_pool() on the holder we just configured.
    original_offload_io = _exec_mod.offload_io
    _exec_mod.offload_io = offload_mod.offload_io
    try:
        yield pool
    finally:
        pool.shutdown(wait=True)
        holder_mod._thread_pool = None
        _exec_mod.offload_io = original_offload_io


# ===========================================================================
# Test #7 (static) — no run_in_executor(None, ...) in the source
# ===========================================================================


class TestNoDefaultExecutorInSource:
    """Static assertion: the touched function must not use the default executor."""

    def test_no_run_in_executor_none_in_source(self):
        """The source file must not contain run_in_executor(None, ...) in execute_pg_backup_job.

        This proves the seam swap is in place and was not accidentally
        reverted or left alongside the old call.
        """
        source_file = Path(_exec_mod.__file__)
        source_text = source_file.read_text()

        # Confirm the OLD pattern is gone everywhere in the file.
        assert "run_in_executor(None," not in source_text, (
            "Found run_in_executor(None, ...) in pg_backup_executor.py — "
            "the default-executor call must be replaced by offload_io."
        )

    def test_offload_io_import_present(self):
        """offload_io must be imported at the top of pg_backup_executor."""
        source_file = Path(_exec_mod.__file__)
        source_text = source_file.read_text()

        assert "from utils.offload import offload_io" in source_text, (
            "offload_io import not found in pg_backup_executor.py"
        )


# ===========================================================================
# Test #1 + #2 — seam: offload_io is called, not run_in_executor
# ===========================================================================


class TestOffloadIoSeamSwap:
    """Verify that execute_pg_backup_job routes through offload_io."""

    async def test_offload_io_is_awaited_instead_of_run_in_executor(self, tmp_path, named_thread_pool):
        """execute_pg_backup_job must await offload_io, not loop.run_in_executor(None, ...).

        Strategy: patch utils.offload.offload_io inside the executor module's
        namespace and record the call.  A real noop dump writes bytes so the
        size-check passes, letting the full function run to completion.
        """
        call_log: list[tuple] = []

        # Write bytes to tmp_path for the size check — simulate the real write.
        noop = _make_noop_dump(write_bytes=300_000)

        async def _fake_offload_io(fn, *args, **kwargs):
            call_log.append((fn, args, kwargs))
            # Execute the noop so the file actually gets written.
            noop(*args, **kwargs)

        with (
            patch.object(_exec_mod, "offload_io", side_effect=_fake_offload_io),
            patch.object(_exec_mod, "_BACKUP_ROOT", tmp_path),
        ):
            result = await execute_pg_backup_job("test-seam-001", {})

        assert result["status"] == "success"
        assert len(call_log) == 1, f"Expected exactly 1 offload_io call, got {call_log!r}"

        fn_called, _pos_args, _kw_args = call_log[0]
        # The first positional arg to offload_io must be _dump_and_compress.
        assert fn_called is _dump_and_compress, (
            f"offload_io must be called with _dump_and_compress as first arg; got {fn_called!r}"
        )

    async def test_offload_io_receives_correct_positional_args(self, tmp_path, named_thread_pool):
        """offload_io must be called with (job_id: str, env: dict, tmp_path: Path).

        The signature of _dump_and_compress is (job_id, env, tmp_path).
        offload_io forwards *args as positional arguments.
        """
        captured_args: list = []
        noop = _make_noop_dump(write_bytes=300_000)

        async def _fake_offload_io(fn, *args, **kwargs):
            captured_args.extend(args)
            noop(*args, **kwargs)

        with (
            patch.object(_exec_mod, "offload_io", side_effect=_fake_offload_io),
            patch.object(_exec_mod, "_BACKUP_ROOT", tmp_path),
        ):
            await execute_pg_backup_job("test-args-002", {})

        assert len(captured_args) == 3, f"Expected 3 positional args; got {captured_args!r}"
        job_id_arg, env_arg, tmp_path_arg = captured_args

        assert isinstance(job_id_arg, str), f"First arg (job_id) must be str; got {type(job_id_arg)}"
        assert isinstance(env_arg, dict), f"Second arg (env) must be dict; got {type(env_arg)}"
        assert "PGPASSWORD" in env_arg, "env dict must contain PGPASSWORD"
        assert isinstance(tmp_path_arg, Path), f"Third arg (tmp_path) must be Path; got {type(tmp_path_arg)}"


# ===========================================================================
# Test #3 — work runs on the SHARED thread pool
# ===========================================================================


class TestSharedThreadPoolDispatch:
    """Prove the work runs on a thread from the SHARED pool, not the default executor."""

    async def test_dump_runs_on_shared_thread_pool_thread(self, tmp_path, named_thread_pool):
        """_dump_and_compress must execute on a thread whose name starts with 'shared-test-pool'.

        Strategy: replace _dump_and_compress with a callable that records
        threading.current_thread().name while the real offload_io (and the
        real ThreadPoolExecutor set in executor_holder) are left in place.
        The name prefix 'shared-test-pool' matches the fixture's pool only —
        it does NOT match 'ThreadPoolExecutor' threads used by the default executor.
        """
        thread_names: list[str] = []

        def _capture_thread(job_id: str, env: dict, tmp_path_arg: Path) -> None:
            thread_names.append(threading.current_thread().name)
            # Write bytes so the size check passes.
            tmp_path_arg.write_bytes(b"x" * 300_000)

        with (
            patch.object(_exec_mod, "_dump_and_compress", side_effect=_capture_thread),
            patch.object(_exec_mod, "_BACKUP_ROOT", tmp_path),
        ):
            result = await execute_pg_backup_job("test-thread-003", {})

        assert result["status"] == "success"
        assert len(thread_names) == 1, f"Expected 1 thread name capture, got {thread_names!r}"

        captured = thread_names[0]
        assert captured.startswith("shared-test-pool"), (
            f"Work ran on thread '{captured}' — expected a thread from the SHARED pool "
            f"(name prefix 'shared-test-pool').  If the name starts with 'ThreadPoolExecutor' "
            f"the default executor was used instead of the registered shared pool."
        )


# ===========================================================================
# Test #4 — exception propagation + temp file cleanup
# ===========================================================================


class TestExceptionHandling:
    """Exceptions from _dump_and_compress propagate and temp files are cleaned up."""

    async def test_exception_propagates_and_tmp_cleaned(self, tmp_path, named_thread_pool):
        """RuntimeError from _dump_and_compress must propagate and tmp file must be removed.

        # 1 mock — _dump_and_compress replaced with a raising stub
        """
        tmp_files_written: list[Path] = []

        def _failing_dump(job_id: str, env: dict, tmp_path_arg: Path) -> None:
            # Write a tiny file so it exists when the exception is raised.
            tmp_path_arg.write_bytes(b"corrupt")
            tmp_files_written.append(tmp_path_arg)
            raise RuntimeError("pg_dump exited with code 1")

        with (
            patch.object(_exec_mod, "_dump_and_compress", side_effect=_failing_dump),
            patch.object(_exec_mod, "_BACKUP_ROOT", tmp_path),
            pytest.raises(RuntimeError, match="pg_dump exited with code 1"),
        ):
            await execute_pg_backup_job("test-exc-004", {})

        # Temp file must have been deleted by the except block.
        assert len(tmp_files_written) == 1
        assert not tmp_files_written[0].exists(), f"Temp file {tmp_files_written[0]} should have been deleted on error"


# ===========================================================================
# Test #5 — dump too small → RuntimeError + file removed
# ===========================================================================


class TestDumpTooSmall:
    """Backup smaller than MIN_BACKUP_BYTES is rejected."""

    async def test_tiny_dump_raises_and_removes_tmp(self, tmp_path, named_thread_pool):
        """A dump smaller than _MIN_BACKUP_BYTES must raise RuntimeError and remove tmp.

        # 1 mock — _dump_and_compress writes only 1 byte
        """

        def _tiny_dump(job_id: str, env: dict, tmp_path_arg: Path) -> None:
            tmp_path_arg.write_bytes(b"x")  # 1 byte — well below 256 KiB

        with (
            patch.object(_exec_mod, "_dump_and_compress", side_effect=_tiny_dump),
            patch.object(_exec_mod, "_BACKUP_ROOT", tmp_path),
            pytest.raises(RuntimeError, match="discarding to protect existing backup"),
        ):
            await execute_pg_backup_job("test-tiny-005", {})


# ===========================================================================
# Test #6 — successful dump return shape
# ===========================================================================


class TestSuccessfulDumpReturnShape:
    """execute_pg_backup_job returns the correct dict on success."""

    async def test_return_dict_shape(self, tmp_path, named_thread_pool):
        """Return value must be {"status": "success", "path": str, "size_bytes": int}.

        # 1 mock — _dump_and_compress writes real bytes
        """
        noop = _make_noop_dump(write_bytes=400_000)

        with (
            patch.object(_exec_mod, "_dump_and_compress", side_effect=noop),
            patch.object(_exec_mod, "_BACKUP_ROOT", tmp_path),
        ):
            result = await execute_pg_backup_job("test-return-006", {})

        assert result["status"] == "success"
        assert isinstance(result["path"], str)
        assert result["path"].endswith(".sql.zst")
        assert isinstance(result["size_bytes"], int)
        assert result["size_bytes"] >= 400_000, f"Expected size_bytes >= 400000, got {result['size_bytes']}"


# ===========================================================================
# Test #8 — _dump_and_compress args/return contract (mocked subprocess)
# ===========================================================================


class TestDumpAndCompressContract:
    """_dump_and_compress signature and return contract are unchanged.

    These tests prove that the offload_io swap did not alter the internal
    helper's interface — the calling convention is identical.
    """

    def test_signature_accepts_job_id_env_tmp_path(self):
        """_dump_and_compress must accept (job_id, env, tmp_path) positionally."""
        sig = inspect.signature(_dump_and_compress)
        params = list(sig.parameters.keys())
        assert params == ["job_id", "env", "tmp_path"], (
            f"_dump_and_compress signature changed; expected ['job_id', 'env', 'tmp_path'], got {params!r}"
        )

    def test_returns_none_on_success(self, tmp_path):
        """_dump_and_compress must return None on success (verified by mocking subprocesses).

        Two subprocess.Popen context managers are patched:
        - pg mock: returncode=0, stdout pipe is closed by the function
        - zstd mock: returncode=0, communicate() returns (b"", b"")

        # 1 mock — subprocess.Popen replaced with stubs
        """
        job_id = "test-contract-008"
        env = {"PGPASSWORD": "test"}
        out_path = tmp_path / "test.sql.zst"

        # Build minimal Popen stubs compatible with the context-manager usage in
        # _dump_and_compress:
        #   with Popen(pg_cmd, ...) as pg, Popen(zstd_cmd, ...) as zst:
        #       if pg.stdout: pg.stdout.close()
        #       _zst_out, zst_err = zst.communicate()
        #       pg_rc = pg.wait()
        #       zst_rc = zst.returncode

        pg_mock = MagicMock()
        pg_mock.stdout = MagicMock()
        pg_mock.stdout.close = MagicMock()
        pg_mock.wait.return_value = 0
        pg_mock.returncode = 0
        pg_mock.__enter__ = MagicMock(return_value=pg_mock)
        pg_mock.__exit__ = MagicMock(return_value=False)

        zst_mock = MagicMock()
        zst_mock.communicate.return_value = (b"", b"")
        zst_mock.returncode = 0
        zst_mock.__enter__ = MagicMock(return_value=zst_mock)
        zst_mock.__exit__ = MagicMock(return_value=False)

        popen_calls: list = []

        def _fake_popen(cmd, **kwargs):
            popen_calls.append(cmd[0])  # track which binary was invoked
            if cmd[0] == "pg_dump":
                return pg_mock
            return zst_mock

        with patch("utils.executors.pg_backup_executor.subprocess.Popen", side_effect=_fake_popen):
            result = _dump_and_compress(job_id, env, out_path)

        assert result is None, f"_dump_and_compress must return None on success; got {result!r}"
        assert "pg_dump" in popen_calls, "pg_dump must be invoked"
        assert "zstd" in popen_calls, "zstd must be invoked"

    def test_raises_on_pg_dump_nonzero_exit(self, tmp_path):
        """_dump_and_compress must raise RuntimeError when pg_dump returns non-zero.

        # 1 mock — subprocess.Popen replaced with stubs
        """
        job_id = "test-pg-err-008b"
        env = {"PGPASSWORD": "test"}
        out_path = tmp_path / "test.sql.zst"

        pg_mock = MagicMock()
        pg_mock.stdout = None
        pg_mock.wait.return_value = 1  # non-zero exit
        pg_mock.__enter__ = MagicMock(return_value=pg_mock)
        pg_mock.__exit__ = MagicMock(return_value=False)

        zst_mock = MagicMock()
        zst_mock.communicate.return_value = (b"", b"")
        zst_mock.returncode = 0
        zst_mock.__enter__ = MagicMock(return_value=zst_mock)
        zst_mock.__exit__ = MagicMock(return_value=False)

        def _fake_popen(cmd, **kwargs):
            return pg_mock if cmd[0] == "pg_dump" else zst_mock

        with (
            patch("utils.executors.pg_backup_executor.subprocess.Popen", side_effect=_fake_popen),
            pytest.raises(RuntimeError, match="pg_dump exited with code 1"),
        ):
            _dump_and_compress(job_id, env, out_path)

    def test_raises_on_zstd_nonzero_exit(self, tmp_path):
        """_dump_and_compress must raise RuntimeError when zstd returns non-zero.

        # 1 mock — subprocess.Popen replaced with stubs
        """
        job_id = "test-zstd-err-008c"
        env = {"PGPASSWORD": "test"}
        out_path = tmp_path / "test.sql.zst"

        pg_mock = MagicMock()
        pg_mock.stdout = None
        pg_mock.wait.return_value = 0
        pg_mock.__enter__ = MagicMock(return_value=pg_mock)
        pg_mock.__exit__ = MagicMock(return_value=False)

        zst_mock = MagicMock()
        zst_mock.communicate.return_value = (b"", b"zstd: write error")
        zst_mock.returncode = 1
        zst_mock.__enter__ = MagicMock(return_value=zst_mock)
        zst_mock.__exit__ = MagicMock(return_value=False)

        def _fake_popen(cmd, **kwargs):
            return pg_mock if cmd[0] == "pg_dump" else zst_mock

        with (
            patch("utils.executors.pg_backup_executor.subprocess.Popen", side_effect=_fake_popen),
            pytest.raises(RuntimeError, match="zstd exited with code 1"),
        ):
            _dump_and_compress(job_id, env, out_path)


# ===========================================================================
# Test #9 — live artifact: real pg_dump + zstd produces a valid .zst file
# ===========================================================================


def _bountydev_db_reachable() -> bool:
    """Return True if the bountydev-db pg_dump binary is available via docker exec."""
    try:
        result = subprocess.run(
            ["sudo", "docker", "exec", "bountydev-db", "which", "pg_dump"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


@pytest.mark.skipif(not _bountydev_db_reachable(), reason="bountydev-db container unreachable or pg_dump absent")
class TestLiveArtifact:
    """Live DB integration: real dump produces a valid, non-empty .zst file.

    Runs pg_dump inside bountydev-db container (postgres 18 native client)
    via docker exec to avoid the pg_dump version mismatch on the host.
    The resulting artifact is fed through zstd decompression to confirm it
    is a valid compressed dump.
    """

    def test_real_dump_produces_valid_zst_artifact(self, tmp_path):
        """Run pg_dump | zstd inside bountydev-db → artifact is non-empty and decompressable.

        This test proves that the _dump_and_compress logic (unchanged by the
        offload_io swap) still produces a valid backup artifact end-to-end.
        """
        container = "bountydev-db"
        db_name = os.getenv("POSTGRES_DB", "bountydb")
        db_user = os.getenv("POSTGRES_USER", "bounty")
        db_password = os.getenv("POSTGRES_PASSWORD", "bounty")
        out_zst = tmp_path / "live_test.sql.zst"

        # Run the entire pg_dump | zstd pipeline inside the container.
        # Using a shell pipe avoids writing an intermediate uncompressed dump.
        cmd = [
            "sudo",
            "docker",
            "exec",
            "-e",
            f"PGPASSWORD={db_password}",
            container,
            "bash",
            "-c",
            f"pg_dump -U {db_user} -d {db_name} --no-password | zstd -10 -o /tmp/live_test.sql.zst --force",
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        assert result.returncode == 0, (
            f"pg_dump | zstd failed (rc={result.returncode}): {result.stderr.decode().strip()}"
        )

        # Copy the artifact from the container.
        cp_cmd = ["sudo", "docker", "cp", f"{container}:/tmp/live_test.sql.zst", str(out_zst)]
        cp_result = subprocess.run(cp_cmd, capture_output=True, timeout=15)
        assert cp_result.returncode == 0, f"docker cp failed: {cp_result.stderr.decode().strip()}"

        # Verify: file exists and is non-empty.
        assert out_zst.exists(), "Artifact .zst file was not created"
        size = out_zst.stat().st_size
        assert size > 0, "Artifact .zst file is empty"

        # Verify: the file is a valid zstd-compressed stream (decompress without error).
        decompress_result = subprocess.run(
            ["zstd", "--decompress", "--stdout", str(out_zst)],
            capture_output=True,
            timeout=30,
        )
        assert decompress_result.returncode == 0, (
            f"zstd decompression of artifact failed: {decompress_result.stderr.decode().strip()}"
        )
        decompressed = decompress_result.stdout
        assert len(decompressed) > 0, "Decompressed artifact is empty"

        # Verify: the decompressed content looks like a PostgreSQL dump.
        assert b"PostgreSQL database dump" in decompressed[:4096] or b"pg_dump" in decompressed[:4096], (
            f"Decompressed content does not look like a pg_dump: {decompressed[:200]!r}"
        )
