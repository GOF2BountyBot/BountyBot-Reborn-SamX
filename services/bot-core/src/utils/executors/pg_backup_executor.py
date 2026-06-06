"""PostgreSQL backup executor — scheduled dump of the game database.

Runs every 3 hours via APScheduler.  Dumps the database with ``pg_dump``,
compresses the output with zstandard (level 10), and writes the result to a
date-partitioned directory under the bot-core data volume:

    /app/data/backups/YYYY-MM-DD/bountydb_HH-MM-SS.sql.zst

Rotation: backup directories older than 7 days are removed after each
successful dump.

Safety guarantees
-----------------
* Writes to a temp file first (``<target>.tmp.PID``), then atomically renames.
* Skips the atomic rename if the temp file is smaller than ``MIN_BACKUP_BYTES``
  (250 KiB) to protect against overwriting a good backup with a corrupt one.
* All errors are logged and re-raised so APScheduler can record the failure.

Environment variables
---------------------
POSTGRES_HOST     -- DB host (default: ``bounty_db``)
POSTGRES_PORT     -- DB port (default: ``5432``)
POSTGRES_DB       -- DB name (default: ``bountydb``)
POSTGRES_USER     -- DB user (default: ``bounty``)
POSTGRES_PASSWORD -- DB password (used via PGPASSWORD env var to avoid shell history)
BACKUP_DIR        -- Override root backup directory (default: ``/app/data/backups``)
BACKUP_RETAIN_DAYS -- Days of backups to keep (default: ``7``)

Deferred imports (ORM modules) are not used here — this executor calls the
OS-level ``pg_dump`` binary directly so it never opens a SQLAlchemy session.
"""

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from shared.bblogger import get_logger

from utils.offload import offload_io

flogger = get_logger("pg-backup-executor")

# ---------------------------------------------------------------------------
# Configuration (all overridable via env vars)
# ---------------------------------------------------------------------------

_DB_HOST = os.getenv("POSTGRES_HOST", "bounty_db")
_DB_PORT = os.getenv("POSTGRES_PORT", "5432")
_DB_NAME = os.getenv("POSTGRES_DB", "bountydb")
_DB_USER = os.getenv("POSTGRES_USER", "bounty")
_DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "bounty")

_BACKUP_ROOT = Path(os.getenv("BACKUP_DIR", "/app/data/backups"))
_RETAIN_DAYS = int(os.getenv("BACKUP_RETAIN_DAYS", "7"))

# A dump smaller than this is assumed corrupt and will not replace an existing backup.
_MIN_BACKUP_BYTES = 256_000  # 250 KiB


# ---------------------------------------------------------------------------
# Public executor entry point
# ---------------------------------------------------------------------------


async def execute_pg_backup_job(job_id: str, payload: dict) -> dict:
    """Dump and compress the PostgreSQL database.

    Returns
    -------
    dict
        ``{"status": "success", "path": str, "size_bytes": int}`` on success.

    Raises
    ------
    RuntimeError
        If ``pg_dump`` fails or the resulting file is too small.
    """
    start_ts = datetime.now(UTC)
    flogger.info(f"PgBackupJob[{job_id}] START")

    # ── Build output paths ────────────────────────────────────────────────────
    today = start_ts.strftime("%Y-%m-%d")
    time_str = start_ts.strftime("%H-%M-%S")
    day_dir = _BACKUP_ROOT / today
    day_dir.mkdir(parents=True, exist_ok=True)

    target = day_dir / f"{_DB_NAME}_{time_str}.sql.zst"
    tmp_path = Path(f"{target}.tmp.{os.getpid()}")

    flogger.info(f"PgBackupJob[{job_id}] writing to {target}")

    # ── Run pg_dump | zstd via subprocess (blocking — wrapped in executor) ────
    env = os.environ.copy()
    env["PGPASSWORD"] = _DB_PASSWORD

    try:
        await offload_io(_dump_and_compress, job_id, env, tmp_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    # ── Safety check ─────────────────────────────────────────────────────────
    size = tmp_path.stat().st_size if tmp_path.exists() else 0
    if size < _MIN_BACKUP_BYTES:
        tmp_path.unlink(missing_ok=True)
        msg = (
            f"PgBackupJob[{job_id}] dump is only {size} bytes "
            f"(< {_MIN_BACKUP_BYTES}); discarding to protect existing backup"
        )
        flogger.error(msg)
        raise RuntimeError(msg)

    # ── Atomic rename ─────────────────────────────────────────────────────────
    tmp_path.rename(target)
    human = _human_size(size)
    duration = (datetime.now(UTC) - start_ts).total_seconds()
    flogger.info(f"PgBackupJob[{job_id}] dump complete: {target} ({human}) in {duration:.1f}s")

    # ── Rotate old backups ────────────────────────────────────────────────────
    _rotate_old_backups(job_id)

    return {"status": "success", "path": str(target), "size_bytes": size}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _dump_and_compress(job_id: str, env: dict, tmp_path: Path) -> None:
    """Run ``pg_dump | zstd -10`` synchronously.

    Executed via ``offload_io`` (shared thread pool) so it doesn't block the event loop.
    """
    pg_dump_cmd = [
        "pg_dump",
        "-h",
        _DB_HOST,
        "-p",
        _DB_PORT,
        "-U",
        _DB_USER,
        "-d",
        _DB_NAME,
        "--no-password",
    ]
    zstd_cmd = ["zstd", "-10", "-o", str(tmp_path), "--force"]

    flogger.debug(f"PgBackupJob[{job_id}] running: {' '.join(pg_dump_cmd)} | {' '.join(zstd_cmd)}")

    with (
        subprocess.Popen(pg_dump_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env) as pg,
        subprocess.Popen(zstd_cmd, stdin=pg.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as zst,
    ):
        # Allow pg_dump to receive SIGPIPE if zstd exits early
        if pg.stdout:
            pg.stdout.close()

        _zst_out, zst_err = zst.communicate()
        pg_rc = pg.wait()
        zst_rc = zst.returncode

    if pg_rc != 0:
        raise RuntimeError(f"pg_dump exited with code {pg_rc}")
    if zst_rc != 0:
        raise RuntimeError(f"zstd exited with code {zst_rc}: {zst_err.decode().strip()}")

    flogger.debug(f"PgBackupJob[{job_id}] pg_dump + zstd completed successfully")


def _rotate_old_backups(job_id: str) -> None:
    """Remove day-directories older than ``_RETAIN_DAYS`` days."""
    cutoff = datetime.now(UTC).date() - timedelta(days=_RETAIN_DAYS)
    removed = 0

    if not _BACKUP_ROOT.exists():
        return

    for entry in sorted(_BACKUP_ROOT.iterdir()):
        if not entry.is_dir():
            continue
        try:
            dir_date = datetime.strptime(entry.name, "%Y-%m-%d").date()
        except ValueError:
            continue  # skip non-date directories

        if dir_date < cutoff:
            try:
                import shutil

                shutil.rmtree(entry)
                flogger.info(f"PgBackupJob[{job_id}] rotated old backup directory: {entry}")
                removed += 1
            except Exception as e:  # pylint: disable=broad-exception-caught
                flogger.warning(f"PgBackupJob[{job_id}] failed to remove {entry}: {e}")

    if removed:
        flogger.info(f"PgBackupJob[{job_id}] rotation complete: {removed} old director(y/ies) removed")
    else:
        flogger.debug(f"PgBackupJob[{job_id}] rotation: nothing older than {_RETAIN_DAYS} days")


def _human_size(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n //= 1024
    return f"{n:.1f} TiB"
