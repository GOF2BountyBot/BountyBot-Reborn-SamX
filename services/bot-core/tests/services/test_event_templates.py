"""OOB event templates: JSON validity, per-guild sync rules, startup fan-out isolation.

SQLite in-memory (same fixture pattern as test_event_service.py); the JSON is the real file.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from persist.models.base import Base
from persist.models.game_event import GameEvent, GameEventPrize
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from services import event_templates as mod

GUILD = 424242
_TABLES = [GameEvent.__table__, GameEventPrize.__table__]


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


def _defs(*names, prizes=None):
    return [
        {"name": n, "type_slug": "duels_won", "params": {"min_fights": 3}, "duration_days": 7, "prizes": prizes or []}
        for n in names
    ]


async def _templates(db, guild=GUILD):
    rows = (
        await db.execute(select(GameEvent).where(GameEvent.guild_id == guild, GameEvent.state == "template"))
    ).scalars()
    return {r.name: r for r in rows}


async def _prizes(db, event_id):
    rows = (await db.execute(select(GameEventPrize).where(GameEventPrize.event_id == event_id))).scalars().all()
    return sorted(((p.rank_from, p.rank_to, p.kind, p.qty) for p in rows), key=lambda t: (t[0] is None, t[0] or 0))


# ---------------------------------------------------------------------------
# The shipped JSON
# ---------------------------------------------------------------------------


def test_shipped_json_is_valid_and_sane():
    defs = mod.load_oob_templates()
    assert len(defs) >= 5
    assert len({d["name"] for d in defs}) == len(defs)
    for d in defs:
        assert d["prizes"], f"{d['name']} ships without prizes"
        ranked = [(p["rank_from"], p["rank_to"]) for p in d["prizes"] if p.get("rank_from")]
        assert ranked == sorted(ranked) and len(set(ranked)) == len(ranked)
        assert all(p["kind"] == "credits" for p in d["prizes"]), "stock templates stay catalog-independent"


def test_bad_definition_rejected(tmp_path: Path):
    bad = tmp_path / "t.json"
    bad.write_text(
        json.dumps({"templates": [{"name": "X", "type_slug": "duels_won", "params": {"division": "Copper"}}]})
    )
    with pytest.raises(Exception):  # HTTPException from the router's param validator
        mod.load_oob_templates(bad)
    bad.write_text(
        json.dumps({"templates": [{"name": "X", "type_slug": "duels_won", "prizes": [{"kind": "ship", "qty": 1}]}]})
    )
    with pytest.raises(ValueError, match="item_ref"):
        mod.load_oob_templates(bad)


# ---------------------------------------------------------------------------
# Sync rules
# ---------------------------------------------------------------------------


async def test_seeds_missing_templates_with_prizes(factory):
    prizes = [{"rank_from": 1, "rank_to": 1, "kind": "credits", "qty": 500}, {"kind": "credits", "qty": 50}]
    async with factory() as db:
        counts = await mod.sync_guild_templates(db, GUILD, _defs("A", "B", prizes=prizes))
        assert counts == {"created": 2, "updated": 0, "skipped": 0}
        rows = await _templates(db)
        assert set(rows) == {"A", "B"}
        a = rows["A"]
        assert (a.state, a.created_by_user_id, a.type_slug, a.duration_days) == ("template", None, "duels_won", 7)
        assert a.created_at == a.updated_at
        assert await _prizes(db, a.id) == [(1, 1, "credits", 500), (None, None, "credits", 50)]


async def test_resync_refreshes_only_unmodified_seeded_rows(factory):
    v1 = _defs("A", "B", prizes=[{"kind": "credits", "qty": 50}])
    async with factory() as db:
        await mod.sync_guild_templates(db, GUILD, v1)
        rows = await _templates(db)
        # an admin edits B (any edit bumps updated_at — settings or prizes)
        rows["B"].duration_days = 30
        rows["B"].updated_at = rows["B"].updated_at + timedelta(seconds=1)
        # and creates their own template that happens to share the name of a future OOB one
        now = datetime.now(UTC)
        db.add(
            GameEvent(
                guild_id=GUILD,
                type_slug="duels_won",
                params={},
                duration_days=3,
                state="template",
                name="C",
                created_by_user_id=12345,
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()

    v2 = [{**d, "duration_days": 14, "prizes": [{"kind": "credits", "qty": 999}]} for d in _defs("A", "B", "C")]
    async with factory() as db:
        counts = await mod.sync_guild_templates(db, GUILD, v2)
        assert counts == {"created": 0, "updated": 1, "skipped": 2}
        rows = await _templates(db)
        assert rows["A"].duration_days == 14 and rows["A"].created_at == rows["A"].updated_at  # still "unmodified"
        assert await _prizes(db, rows["A"].id) == [(None, None, "credits", 999)]  # prizes replaced
        assert rows["B"].duration_days == 30 and await _prizes(db, rows["B"].id) == [(None, None, "credits", 50)]
        assert rows["C"].duration_days == 3 and rows["C"].created_by_user_id == 12345


async def test_sync_is_per_guild(factory):
    async with factory() as db:
        await mod.sync_guild_templates(db, GUILD, _defs("A"))
        await mod.sync_guild_templates(db, GUILD + 1, _defs("A"))
        assert set(await _templates(db, GUILD)) == {"A"} and set(await _templates(db, GUILD + 1)) == {"A"}
        assert (await mod.sync_guild_templates(db, GUILD, _defs("A")))["skipped"] == 0  # unmodified → refreshed


async def test_catalog_missing_prize_is_skipped_not_fatal(factory):
    defs = _defs("A", prizes=[{"kind": "ship", "item_ref": "No Such Ship", "qty": 1}, {"kind": "credits", "qty": 5}])
    with patch.object(mod, "_prize_ref_exists", side_effect=lambda db, kind, ref: kind == "credits"):
        async with factory() as db:
            await mod.sync_guild_templates(db, GUILD, defs)
            a = (await _templates(db))["A"]
            assert await _prizes(db, a.id) == [(None, None, "credits", 5)]


# ---------------------------------------------------------------------------
# Startup fan-out
# ---------------------------------------------------------------------------


async def test_startup_sync_isolates_a_failing_guild():
    calls = []

    async def fake_sync(db, gid, defs):
        calls.append(gid)
        if gid == 2:
            raise RuntimeError("boom")
        return {"created": 1, "updated": 0, "skipped": 0}

    class _Sess:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *_a, **_k):
            class _R:
                @staticmethod
                def scalars():
                    class _S:
                        @staticmethod
                        def all():
                            return [1, 2, 3]

                    return _S()

            return _R()

    with (
        patch.object(mod, "load_oob_templates", return_value=[]),
        patch.object(mod, "get_db_session", _Sess),
        patch.object(mod, "sync_guild_templates", AsyncMock(side_effect=fake_sync)),
    ):
        totals = await mod.sync_all_guild_templates()
    assert calls == [1, 2, 3]
    assert totals == {"created": 2, "updated": 0, "skipped": 0, "failed": 1}
