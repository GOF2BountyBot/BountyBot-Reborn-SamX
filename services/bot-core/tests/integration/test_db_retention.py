"""Integration tests for the DB-retention executor and its repository methods.

Three concerns are covered:

* ``BountyRepository.delete_terminal_older_than`` — only terminal-status rows
  older than the cutoff are deleted; active and recent rows are preserved.
* ``DuelRepository.delete_terminal_older_than`` — same contract, different
  status vocabulary and timestamp column.
* ``AdminAuditLogRepository.delete_older_than`` — straight timestamp filter,
  status-agnostic.

The executor's happy-path is exercised end-to-end against a real SQLite
session: it runs all three passes and returns per-table deletion counts.

Per ``tests/AGENTS.md``: max 2 mocks per test, prefer real objects, and the
executor uses deferred imports so the canonical patch target is
``persist.database.manager.db_manager``.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from persist.models.admin_audit_log import AdminAuditLog
from persist.models.bounty import Bounty
from persist.models.combat_log import CombatLog
from persist.models.duel_request import DuelRequest
from persist.repositories.admin_audit_log_repository import AdminAuditLogRepository
from persist.repositories.bounty_repository import BountyRepository
from persist.repositories.duel_repository import DuelRepository
from sqlalchemy import select

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bounty(
    *,
    bounty_id: int | None = None,
    status: str = "active",
    updated_at: datetime | None = None,
    guild_id: int = 1,
) -> Bounty:
    """Build a minimal Bounty ORM object suitable for the SQLite test DB."""
    now = datetime.now(UTC)
    b = Bounty(
        guild_id=guild_id,
        division="bronze",
        criminal_name="Test Criminal",
        criminal_faction=None,
        route=["Sol", "Vega"],
        answer="Vega",
        reward=100,
        reward_per_sys=10,
        checked={},
        tech_level=1,
        status=status,
        escape_count=0,
        issue_time=now,
        created_at=now,
        updated_at=updated_at if updated_at is not None else now,
    )
    if bounty_id is not None:
        b.id = bounty_id
    return b


def _make_duel(
    *,
    status: str = "pending",
    created_at: datetime | None = None,
) -> DuelRequest:
    now = datetime.now(UTC)
    return DuelRequest(
        guild_id=1,
        challenger_id=100,
        target_id=200,
        stakes=50,
        status=status,
        created_at=created_at if created_at is not None else now,
        expires_at=None,
    )


def _make_audit(
    *,
    timestamp: datetime | None = None,
    action: str = "test_action",
) -> AdminAuditLog:
    return AdminAuditLog(
        timestamp=timestamp if timestamp is not None else datetime.now(UTC),
        user_id=42,
        guild_id=1,
        action=action,
        resource_type="test",
        resource_id="1",
        details=None,
        status="success",
    )


def _make_combat_log(*, created_at: datetime | None = None, context: str = "duel") -> CombatLog:
    """Build a minimal CombatLog ORM row for the SQLite retention test."""
    now = datetime.now(UTC)
    # Bounty (NPC) fights have a NULL combatant2_user_id; duels have both.
    c2_user_id = None if context in ("bounty_pvc", "bounty_bonus") else 200
    return CombatLog(
        guild_id=1,
        context=context,
        combatant1_name="A",
        combatant2_name="B",
        combatant1_user_id=100,
        combatant2_user_id=c2_user_id,
        winner_name="A",
        is_stalemate=False,
        data={"schema_version": 1},
        created_at=created_at if created_at is not None else now,
    )


# ---------------------------------------------------------------------------
# BountyRepository.delete_terminal_older_than
# ---------------------------------------------------------------------------


async def test_bounty_retention_deletes_old_terminal_rows(db_session):
    now = datetime.now(UTC)
    old = now - timedelta(hours=48)
    recent = now - timedelta(hours=1)

    # Old terminal rows — all should be deleted
    for status in ("completed", "expired", "cleared"):
        db_session.add(_make_bounty(status=status, updated_at=old))
    # Recent terminal — kept
    db_session.add(_make_bounty(status="completed", updated_at=recent))
    # Old active — kept (status filter)
    db_session.add(_make_bounty(status="active", updated_at=old))
    # Old escaped — kept (not in default terminal set)
    db_session.add(_make_bounty(status="escaped", updated_at=old))
    await db_session.commit()

    repo = BountyRepository()
    cutoff = now - timedelta(hours=24)
    deleted = await repo.delete_terminal_older_than(db_session, cutoff)

    assert deleted == 3
    remaining = (await db_session.execute(select(Bounty))).scalars().all()
    statuses = sorted(b.status for b in remaining)
    assert statuses == ["active", "completed", "escaped"]


async def test_bounty_retention_no_matches_returns_zero(db_session):
    now = datetime.now(UTC)
    db_session.add(_make_bounty(status="active", updated_at=now - timedelta(hours=48)))
    db_session.add(_make_bounty(status="completed", updated_at=now - timedelta(hours=1)))
    await db_session.commit()

    repo = BountyRepository()
    deleted = await repo.delete_terminal_older_than(db_session, now - timedelta(hours=24))

    assert deleted == 0
    remaining_count = len((await db_session.execute(select(Bounty))).scalars().all())
    assert remaining_count == 2


# ---------------------------------------------------------------------------
# DuelRepository.delete_terminal_older_than
# ---------------------------------------------------------------------------


async def test_duel_retention_deletes_old_terminal_rows(db_session):
    now = datetime.now(UTC)
    old = now - timedelta(hours=48)
    recent = now - timedelta(hours=1)

    for status in ("completed", "expired", "cancelled", "rejected", "declined"):
        db_session.add(_make_duel(status=status, created_at=old))
    db_session.add(_make_duel(status="completed", created_at=recent))  # too recent
    db_session.add(_make_duel(status="pending", created_at=old))  # not terminal
    await db_session.commit()

    repo = DuelRepository()
    deleted = await repo.delete_terminal_older_than(db_session, now - timedelta(hours=24))

    assert deleted == 5
    remaining = (await db_session.execute(select(DuelRequest))).scalars().all()
    statuses = sorted(d.status for d in remaining)
    assert statuses == ["completed", "pending"]


# ---------------------------------------------------------------------------
# AdminAuditLogRepository.delete_older_than
# ---------------------------------------------------------------------------


async def test_audit_retention_deletes_old_rows_regardless_of_action(db_session):
    now = datetime.now(UTC)

    db_session.add(_make_audit(timestamp=now - timedelta(days=45), action="admin_spawn_bounties"))
    db_session.add(_make_audit(timestamp=now - timedelta(days=31), action="credits_update"))
    db_session.add(_make_audit(timestamp=now - timedelta(days=29), action="credits_update"))
    db_session.add(_make_audit(timestamp=now - timedelta(days=1), action="credits_update"))
    await db_session.commit()

    repo = AdminAuditLogRepository()
    cutoff = now - timedelta(days=30)
    deleted = await repo.delete_older_than(db_session, cutoff)

    assert deleted == 2
    remaining = (await db_session.execute(select(AdminAuditLog))).scalars().all()
    remaining_actions = sorted(r.action for r in remaining)
    assert remaining_actions == ["credits_update", "credits_update"]


async def test_audit_count_reflects_table_state(db_session):
    repo = AdminAuditLogRepository()
    assert await repo.count(db_session) == 0

    db_session.add(_make_audit())
    db_session.add(_make_audit())
    await db_session.commit()

    assert await repo.count(db_session) == 2


# ---------------------------------------------------------------------------
# Executor end-to-end (Tier B per AGENTS.md S2 pattern)
# ---------------------------------------------------------------------------


async def test_executor_deletes_across_all_three_tables(db_session, async_engine):
    """End-to-end: executor runs all three passes against a real SQLite DB."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    now = datetime.now(UTC)

    # Seed: 2 deletable bounties, 1 keeper
    db_session.add(_make_bounty(status="expired", updated_at=now - timedelta(hours=48)))
    db_session.add(_make_bounty(status="completed", updated_at=now - timedelta(hours=48)))
    db_session.add(_make_bounty(status="active", updated_at=now - timedelta(hours=48)))
    # 1 deletable duel, 1 keeper
    db_session.add(_make_duel(status="expired", created_at=now - timedelta(hours=48)))
    db_session.add(_make_duel(status="pending", created_at=now - timedelta(hours=48)))
    # 1 deletable audit, 1 keeper
    db_session.add(_make_audit(timestamp=now - timedelta(days=45)))
    db_session.add(_make_audit(timestamp=now - timedelta(days=5)))
    # 1 deletable combat log (very old), 1 keeper (recent) — combat_log is now a
    # real SQLite table (integration conftest), so the 4th pass runs for real.
    db_session.add(_make_combat_log(created_at=now - timedelta(days=3650)))
    db_session.add(_make_combat_log(created_at=now - timedelta(days=1)))
    await db_session.commit()

    # Bridge patch: the executor calls db_manager.get_session(); substitute
    # a factory yielding fresh SQLite sessions from the same engine.
    session_factory = async_sessionmaker(async_engine, expire_on_commit=False)

    @asynccontextmanager
    async def _fake_get_session():
        async with session_factory() as session:
            yield session

    fake_db_manager = MagicMock()
    fake_db_manager.get_session = _fake_get_session

    from unittest.mock import patch

    from utils.executors.db_retention_executor import execute_db_retention_job

    # 1 mock: db_manager.get_session bridge. All four passes (bounty/duel/audit/
    # combat_log) run for real against the SQLite session.
    with patch("persist.database.manager.db_manager", fake_db_manager):
        result = await execute_db_retention_job("test-job", {"job_type": "db_retention"})

    assert result["status"] == "success"
    assert result["bounties_deleted"] == 2
    assert result["duels_deleted"] == 1
    assert result["audit_logs_deleted"] == 1
    assert result["combat_logs_deleted"] == 1  # real 4th pass deleted the 3650-day-old row
    assert result["errors"] == []

    # Verify via a FRESH session per the cross-session-reload rule
    async with session_factory() as fresh:
        bounties = (await fresh.execute(select(Bounty))).scalars().all()
        duels = (await fresh.execute(select(DuelRequest))).scalars().all()
        audits = (await fresh.execute(select(AdminAuditLog))).scalars().all()
        combat_logs = (await fresh.execute(select(CombatLog))).scalars().all()

    assert len(bounties) == 1 and bounties[0].status == "active"
    assert len(duels) == 1 and duels[0].status == "pending"
    assert len(audits) == 1
    assert len(combat_logs) == 1  # only the recent combat log survives


async def test_combat_log_retention_scoped_by_context(db_session, async_engine):
    """issue #86: bounty logs prune at 48h; duel logs survive until the 1-year PvP window."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    now = datetime.now(UTC)

    # Bounty logs: one past 48h (pruned), one bonus past 48h (pruned), one under (kept).
    db_session.add(_make_combat_log(context="bounty_pvc", created_at=now - timedelta(hours=60)))
    db_session.add(_make_combat_log(context="bounty_bonus", created_at=now - timedelta(hours=60)))
    db_session.add(_make_combat_log(context="bounty_pvc", created_at=now - timedelta(hours=24)))
    # Duels: 60h old survives (well inside 1yr — the key new behavior), 400d old pruned,
    # 1d old survives.
    db_session.add(_make_combat_log(context="duel", created_at=now - timedelta(hours=60)))
    db_session.add(_make_combat_log(context="duel", created_at=now - timedelta(days=400)))
    db_session.add(_make_combat_log(context="duel", created_at=now - timedelta(days=1)))
    await db_session.commit()

    session_factory = async_sessionmaker(async_engine, expire_on_commit=False)

    @asynccontextmanager
    async def _fake_get_session():
        async with session_factory() as session:
            yield session

    fake_db_manager = MagicMock()
    fake_db_manager.get_session = _fake_get_session

    from unittest.mock import patch

    from utils.executors.db_retention_executor import execute_db_retention_job

    with patch("persist.database.manager.db_manager", fake_db_manager):
        result = await execute_db_retention_job("test-job", {"job_type": "db_retention"})

    assert result["status"] == "success"
    assert result["combat_logs_bounty_deleted"] == 2
    assert result["combat_logs_pvp_deleted"] == 1
    assert result["combat_logs_deleted"] == 3

    async with session_factory() as fresh:
        survivors = (await fresh.execute(select(CombatLog))).scalars().all()
    # 1 recent bounty + 60h duel + 1d duel = 3 survivors; the 60h duel is the proof
    # bounty-window pruning does NOT touch duels.
    contexts = sorted(r.context for r in survivors)
    assert contexts == ["bounty_pvc", "duel", "duel"]


async def test_combat_log_pvp_retention_zero_is_permanent(db_session, async_engine):
    """COMBAT_LOG_PVP_RETENTION_HOURS=0 disables duel pruning entirely."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from services.game_constants import GameConstants

    now = datetime.now(UTC)
    # An ancient duel that WOULD be pruned under the 1yr window.
    db_session.add(_make_combat_log(context="duel", created_at=now - timedelta(days=3650)))
    # A stale bounty that must still prune regardless of the PvP setting.
    db_session.add(_make_combat_log(context="bounty_pvc", created_at=now - timedelta(hours=60)))
    await db_session.commit()

    session_factory = async_sessionmaker(async_engine, expire_on_commit=False)

    @asynccontextmanager
    async def _fake_get_session():
        async with session_factory() as session:
            yield session

    fake_db_manager = MagicMock()
    fake_db_manager.get_session = _fake_get_session

    from unittest.mock import patch

    from utils.executors.db_retention_executor import execute_db_retention_job

    with (
        patch("persist.database.manager.db_manager", fake_db_manager),
        patch.object(GameConstants, "COMBAT_LOG_PVP_RETENTION_HOURS", 0),
    ):
        result = await execute_db_retention_job("test-job", {"job_type": "db_retention"})

    assert result["status"] == "success"
    assert result["combat_logs_pvp_deleted"] == 0  # PvP pass skipped
    assert result["combat_logs_bounty_deleted"] == 1

    async with session_factory() as fresh:
        survivors = (await fresh.execute(select(CombatLog))).scalars().all()
    # The 10-year-old duel survives (permanent); the stale bounty is gone.
    assert [r.context for r in survivors] == ["duel"]


async def test_executor_returns_success_when_one_pass_fails(db_session, async_engine):
    """If the bounty pass raises, the duel and audit passes still run; status stays success."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    now = datetime.now(UTC)
    db_session.add(_make_duel(status="expired", created_at=now - timedelta(hours=48)))
    db_session.add(_make_audit(timestamp=now - timedelta(days=45)))
    await db_session.commit()

    session_factory = async_sessionmaker(async_engine, expire_on_commit=False)

    @asynccontextmanager
    async def _fake_get_session():
        async with session_factory() as session:
            yield session

    fake_db_manager = MagicMock()
    fake_db_manager.get_session = _fake_get_session

    from unittest.mock import patch

    from utils.executors.db_retention_executor import execute_db_retention_job

    # Patch BountyRepository.delete_terminal_older_than to raise — exercises the
    # per-pass try/except isolation in the executor.
    with (
        patch("persist.database.manager.db_manager", fake_db_manager),
        patch(
            "persist.repositories.bounty_repository.BountyRepository.delete_terminal_older_than",
            side_effect=RuntimeError("bounty pass kaboom"),
        ),
    ):
        # combat_log is now a real SQLite table, so ONLY the intentionally-broken
        # bounty pass errors — the other three passes (duel/audit/combat_log) run
        # for real. Exactly one error is expected.
        result = await execute_db_retention_job("test-job", {})

    assert result["status"] == "success"
    assert result["bounties_deleted"] == 0
    assert result["duels_deleted"] == 1
    assert result["audit_logs_deleted"] == 1
    assert len(result["errors"]) == 1  # only the forced bounty-pass failure
    assert any("bounty pass" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# GameConstants env override smoke test
# ---------------------------------------------------------------------------


def test_game_constants_load_picks_up_retention_env_vars(monkeypatch):
    """Sync test — env-var override does not need the event loop."""
    from services.game_constants import GameConstants

    monkeypatch.setenv("BOUNTYBOT_BOUNTY_RETENTION_HOURS", "48")
    monkeypatch.setenv("BOUNTYBOT_DUEL_RETENTION_HOURS", "72")
    monkeypatch.setenv("BOUNTYBOT_AUDIT_RETENTION_DAYS", "90")

    try:
        GameConstants.load()
        assert GameConstants.BOUNTY_RETENTION_HOURS == 48
        assert GameConstants.DUEL_RETENTION_HOURS == 72
        assert GameConstants.AUDIT_RETENTION_DAYS == 90
    finally:
        # Reset to defaults to avoid bleed-through
        monkeypatch.delenv("BOUNTYBOT_BOUNTY_RETENTION_HOURS", raising=False)
        monkeypatch.delenv("BOUNTYBOT_DUEL_RETENTION_HOURS", raising=False)
        monkeypatch.delenv("BOUNTYBOT_AUDIT_RETENTION_DAYS", raising=False)
        GameConstants.load()
